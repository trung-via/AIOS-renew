"""Bounded read-only aggregation of canonical RUN_OBSERVATION facts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .review_transport import (
    PERFORMANCE_MAX_TERMINAL_RUNS,
    RemotePerformanceSnapshot,
    RemoteTerminalArtifact,
    ReviewTransportError,
    _bind_performance_terminal_identity,
    resolve_remote_performance_snapshot,
)
from .run import SUPPORTED_EXECUTORS
from .run_observation import (
    OPERATIONS,
    SOFT_BUDGET_STATUSES,
    TERMINAL_KINDS,
    RunObservation,
    RunObservationError,
    validate_observation,
)


TASK_ID_PATTERN = re.compile(r"TASK-[A-Za-z0-9_-]+")


class PerformanceObservationError(ValueError, RuntimeError):
    """Raised when the observation surface must fail closed."""


def validate_task_selectors(task_ids: Sequence[str]) -> tuple[str, ...]:
    """Validate 1-32 unique, bare canonical TASK identities."""

    if not isinstance(task_ids, (list, tuple)) or not task_ids:
        raise PerformanceObservationError(
            "task selectors must contain between 1 and 32 exact TASK identities"
        )
    if len(task_ids) > 32:
        raise PerformanceObservationError(
            "task selectors exceed maximum bound of 32"
        )
    if any(not isinstance(item, str) for item in task_ids):
        raise PerformanceObservationError("malformed TASK selector identity")
    if len(task_ids) != len(set(task_ids)):
        raise PerformanceObservationError(
            "task selectors contain duplicate identities"
        )
    for task_id in task_ids:
        if TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise PerformanceObservationError(
                f"malformed TASK selector identity: {task_id!r}"
            )
        if ":" in task_id or "@" in task_id:
            raise PerformanceObservationError(
                f"revision-qualified TASK selector is not allowed: {task_id!r}"
            )
    return tuple(sorted(task_ids))


def nearest_rank_percentile(values: Sequence[float], p: int) -> float | None:
    """Return exact nearest-rank percentile without interpolation or rounding."""

    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(p * len(ordered) / 100) - 1]


@dataclass(frozen=True)
class DurationSummary:
    sample_count: int
    p50: float | None
    p95: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "p50": self.p50,
            "p95": self.p95,
        }


@dataclass(frozen=True)
class TokenUsageSummary:
    sample_count: int
    sample_coverage: int
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "sample_coverage": self.sample_coverage,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass(frozen=True)
class CoverageSummary:
    terminal_runs: int
    valid_observations: int
    observed_runs: int
    missing_observations: int
    token_usage_samples: int
    soft_budget_samples: int

    def as_dict(self) -> dict[str, int]:
        return {
            "terminal_runs": self.terminal_runs,
            "valid_observations": self.valid_observations,
            "observed_runs": self.observed_runs,
            "missing_observations": self.missing_observations,
            "token_usage_samples": self.token_usage_samples,
            "soft_budget_samples": self.soft_budget_samples,
        }


@dataclass(frozen=True)
class PerformanceObservation:
    """Stable AIOS_PERFORMANCE_OBSERVATION version 1 envelope."""

    task_selectors: tuple[str, ...]
    coverage: CoverageSummary
    counts: Mapping[str, Any]
    durations: Mapping[str, DurationSummary]
    failure_rate: float | None
    verification_share: float | None
    token_usage: TokenUsageSummary
    soft_budget: Mapping[str, Any]

    @property
    def duration_summaries(self) -> Mapping[str, DurationSummary]:
        return self.durations

    def as_dict(self) -> dict[str, Any]:
        durations = {
            name: summary.as_dict()
            for name, summary in sorted(self.durations.items())
        }
        return {
            "format": "AIOS_PERFORMANCE_OBSERVATION",
            "version": 1,
            "kind": "PERFORMANCE_OBSERVATION",
            "task_selectors": list(self.task_selectors),
            "coverage": self.coverage.as_dict(),
            "counts": dict(self.counts),
            "durations": durations,
            "duration_summaries": durations,
            "failure_rate": self.failure_rate,
            "verification_share": self.verification_share,
            "token_usage": self.token_usage.as_dict(),
            "soft_budget": dict(self.soft_budget),
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class _BoundTerminal:
    source: RemoteTerminalArtifact
    task_revision: int
    executor: str
    base_sha: str
    candidate_sha: str


def build_performance_observation(
    snapshot: RemotePerformanceSnapshot,
) -> PerformanceObservation:
    """Aggregate a snapshot after binding every terminal before coverage."""

    selectors = validate_task_selectors(snapshot.task_selectors)
    if tuple(snapshot.task_selectors) != selectors:
        raise PerformanceObservationError("task selectors must be sorted")

    # F101-003: terminal identity is an all-snapshot precondition.  No missing
    # observation is counted until every transported RUN/terminal family binds.
    bound = tuple(_bind_terminal(item, selectors) for item in snapshot.terminals)
    observations: list[RunObservation] = []
    missing = 0
    for item in bound:
        raw = item.source.observation
        if raw is None:
            missing += 1
            continue
        observations.append(_bind_observation(raw, item))

    by_operation = {key: 0 for key in sorted(OPERATIONS)}
    by_terminal = {key: 0 for key in sorted(TERMINAL_KINDS)}
    by_executor = {key: 0 for key in sorted(SUPPORTED_EXECUTORS)}
    by_revision: dict[str, int] = {}
    by_budget = {key: 0 for key in sorted(SOFT_BUDGET_STATUSES)}
    for observation in observations:
        by_operation[observation.operation] += 1
        by_terminal[observation.terminal_kind] += 1
        by_executor[observation.executor] = (
            by_executor.get(observation.executor, 0) + 1
        )
        revision = str(observation.task_revision)
        by_revision[revision] = by_revision.get(revision, 0) + 1
        if observation.soft_budget is not None:
            by_budget[observation.soft_budget.status] += 1
    by_revision = {
        key: by_revision[key] for key in sorted(by_revision, key=int)
    }

    token_samples = [item for item in observations if item.token_usage is not None]
    budget_samples = [item for item in observations if item.soft_budget is not None]
    token_count = len(token_samples)
    token_usage = TokenUsageSummary(
        sample_count=token_count,
        sample_coverage=token_count,
        input_tokens=(
            sum(item.token_usage.input_tokens for item in token_samples)  # type: ignore[union-attr]
            if token_samples
            else None
        ),
        cached_input_tokens=(
            sum(item.token_usage.cached_input_tokens for item in token_samples)  # type: ignore[union-attr]
            if token_samples
            else None
        ),
        output_tokens=(
            sum(item.token_usage.output_tokens for item in token_samples)  # type: ignore[union-attr]
            if token_samples
            else None
        ),
    )
    coverage = CoverageSummary(
        terminal_runs=len(bound),
        valid_observations=len(observations),
        observed_runs=len(observations),
        missing_observations=missing,
        token_usage_samples=token_count,
        soft_budget_samples=len(budget_samples),
    )

    admitted = [item.admitted_run_elapsed_seconds for item in observations]
    executor = [
        item.executor_elapsed_seconds
        for item in observations
        if item.executor_elapsed_seconds is not None
    ]
    verification = [
        item.verification_elapsed_seconds
        for item in observations
        if item.verification_elapsed_seconds is not None
    ]
    durations = {
        "admitted_run_seconds": _duration(admitted),
        "executor_seconds": _duration(executor),
        "verification_seconds": _duration(verification),
    }
    valid_count = len(observations)
    admitted_total = sum(admitted)
    failure_rate = (
        by_terminal["FAILURE"] / valid_count if valid_count else None
    )
    verification_share = (
        sum(item.verification_elapsed_seconds or 0.0 for item in observations)
        / admitted_total
        if valid_count and admitted_total != 0
        else None
    )
    counts = {
        "by_operation": by_operation,
        "by_terminal_kind": by_terminal,
        "by_executor": dict(sorted(by_executor.items())),
        "by_task_revision": by_revision,
        "by_soft_budget_status": by_budget,
        # Stable v1 compatibility names.
        "operation": by_operation,
        "terminal_kind": by_terminal,
        "executor": dict(sorted(by_executor.items())),
        "task_revision": by_revision,
        "soft_budget_status": by_budget,
    }
    return PerformanceObservation(
        task_selectors=selectors,
        coverage=coverage,
        counts=counts,
        durations=durations,
        failure_rate=failure_rate,
        verification_share=verification_share,
        token_usage=token_usage,
        soft_budget={"sample_count": len(budget_samples), "counts": by_budget},
    )


def _bind_terminal(
    terminal: RemoteTerminalArtifact, selectors: tuple[str, ...]
) -> _BoundTerminal:
    if terminal.task_id not in selectors:
        raise PerformanceObservationError(
            f"terminal TASK is outside selected snapshot: {terminal.run_id}"
        )
    ref_prefix = (
        "refs/heads/aios/artifacts/"
        if terminal.terminal_kind == "RESULT"
        else "refs/heads/aios/failure-artifacts/"
    )
    try:
        revision, executor, base_sha, candidate_sha = (
            _bind_performance_terminal_identity(
                terminal.run,
                terminal.terminal,
                ref=f"{ref_prefix}{terminal.run_id}",
                run_id=terminal.run_id,
                task_id=terminal.task_id,
                terminal_kind=terminal.terminal_kind,
            )
        )
    except ReviewTransportError as exc:
        raise PerformanceObservationError(str(exc)) from exc
    transported = (
        terminal.task_revision,
        terminal.executor,
        terminal.base_sha,
        terminal.candidate_sha,
    )
    derived = (revision, executor, base_sha, candidate_sha)
    if any(value is not None for value in transported) and transported != derived:
        raise PerformanceObservationError(
            f"transported terminal binding conflicts for {terminal.run_id}"
        )
    return _BoundTerminal(terminal, revision, executor, base_sha, candidate_sha)


def _bind_observation(raw: bytes, terminal: _BoundTerminal) -> RunObservation:
    try:
        data = json.loads(raw.decode("utf-8", errors="strict"))
        observation = validate_observation(data)
    except (UnicodeError, json.JSONDecodeError, RunObservationError) as exc:
        raise PerformanceObservationError(
            f"invalid RUN_OBSERVATION for {terminal.source.run_id}: {exc}"
        ) from exc
    expected = (
        terminal.source.run_id,
        terminal.source.task_id,
        terminal.task_revision,
        terminal.executor,
        terminal.base_sha,
        terminal.source.terminal_kind,
    )
    actual = (
        observation.run_id,
        observation.task_id,
        observation.task_revision,
        observation.executor,
        observation.base_sha,
        observation.terminal_kind,
    )
    if actual != expected:
        raise PerformanceObservationError(
            f"RUN_OBSERVATION identity conflicts for {terminal.source.run_id}"
        )
    return observation


def _duration(values: Sequence[float]) -> DurationSummary:
    return DurationSummary(
        sample_count=len(values),
        p50=nearest_rank_percentile(values, 50),
        p95=nearest_rank_percentile(values, 95),
    )


def validate_performance_observation(data: Any) -> PerformanceObservation:
    """Validate a decoded v1 envelope's stable structural invariants."""

    if not isinstance(data, Mapping):
        raise PerformanceObservationError("PERFORMANCE_OBSERVATION must be a mapping")
    if (
        data.get("format") != "AIOS_PERFORMANCE_OBSERVATION"
        or data.get("version") != 1
        or data.get("kind") != "PERFORMANCE_OBSERVATION"
    ):
        raise PerformanceObservationError(
            "format/version/kind mismatch for AIOS_PERFORMANCE_OBSERVATION"
        )
    selectors_raw = data.get("task_selectors")
    if not isinstance(selectors_raw, list):
        raise PerformanceObservationError("task_selectors must be a list")
    selectors = validate_task_selectors(selectors_raw)
    if selectors_raw != list(selectors):
        raise PerformanceObservationError("task_selectors must be sorted")
    coverage = data.get("coverage")
    if not isinstance(coverage, Mapping):
        raise PerformanceObservationError("coverage must be a mapping")
    values: dict[str, int] = {}
    for name in (
        "terminal_runs",
        "valid_observations",
        "missing_observations",
        "token_usage_samples",
        "soft_budget_samples",
    ):
        value = coverage.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PerformanceObservationError(
                f"coverage.{name} must be a non-negative integer"
            )
        values[name] = value
    if values["terminal_runs"] != (
        values["valid_observations"] + values["missing_observations"]
    ):
        raise PerformanceObservationError(
            "coverage.terminal_runs must equal valid_observations + missing_observations"
        )
    # This validator is intentionally structural; canonical values are constructed
    # only by build_performance_observation from the bound snapshot.
    durations: dict[str, DurationSummary] = {}
    raw_durations = data.get("durations")
    if not isinstance(raw_durations, Mapping):
        raise PerformanceObservationError("durations must be a mapping")
    for name in (
        "admitted_run_seconds",
        "executor_seconds",
        "verification_seconds",
    ):
        value = raw_durations.get(name)
        if not isinstance(value, Mapping):
            raise PerformanceObservationError(f"durations.{name} must be a mapping")
        sample_count = value.get("sample_count")
        p50, p95 = value.get("p50"), value.get("p95")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int):
            raise PerformanceObservationError(
                f"durations.{name}.sample_count must be an integer"
            )
        if sample_count == 0 and (p50 is not None or p95 is not None):
            raise PerformanceObservationError(
                f"durations.{name} percentiles must be null for empty samples"
            )
        if sample_count and (
            isinstance(p50, bool)
            or isinstance(p95, bool)
            or not isinstance(p50, (int, float))
            or not isinstance(p95, (int, float))
            or not math.isfinite(p50)
            or not math.isfinite(p95)
            or p50 < 0
            or p50 > p95
        ):
            raise PerformanceObservationError(
                f"durations.{name} percentiles are invalid"
            )
        durations[name] = DurationSummary(sample_count, p50, p95)
    token = data.get("token_usage")
    soft_budget = data.get("soft_budget")
    counts = data.get("counts")
    if not all(isinstance(item, Mapping) for item in (token, soft_budget, counts)):
        raise PerformanceObservationError("counts and sample summaries must be mappings")
    return PerformanceObservation(
        task_selectors=selectors,
        coverage=CoverageSummary(
            terminal_runs=values["terminal_runs"],
            valid_observations=values["valid_observations"],
            observed_runs=coverage.get("observed_runs", values["valid_observations"]),
            missing_observations=values["missing_observations"],
            token_usage_samples=values["token_usage_samples"],
            soft_budget_samples=values["soft_budget_samples"],
        ),
        counts=counts,
        durations=durations,
        failure_rate=data.get("failure_rate"),
        verification_share=data.get("verification_share"),
        token_usage=TokenUsageSummary(
            sample_count=token.get("sample_count"),
            sample_coverage=token.get("sample_coverage", token.get("sample_count")),
            input_tokens=token.get("input_tokens"),
            cached_input_tokens=token.get("cached_input_tokens"),
            output_tokens=token.get("output_tokens"),
        ),
        soft_budget=soft_budget,
    )


def observe_performance(
    task_ids: Sequence[str], *, repo: str | Path | None = None
) -> PerformanceObservation:
    """Observe canonical remote facts without changing local or remote state."""

    selectors = validate_task_selectors(task_ids)
    import aios_renew.operator as operator

    root = operator.resolve_repository(repo)
    try:
        with operator._remote_observation_repository(root) as observer:
            snapshot = resolve_remote_performance_snapshot(
                observer, task_ids=selectors
            )
            return build_performance_observation(snapshot)
    except ReviewTransportError as exc:
        raise PerformanceObservationError(str(exc)) from exc


# These limits apply only to the additive L1 view, never to historical v1 decoding.
L1_MAX_ID_CHARS = 128
L1_MAX_RECORD_BYTES = 1024 * 1024
L1_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class L1BaselineReadiness:
    """Detached, deterministic view; its digest is a binding, not a trust root."""

    _json: str

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    def render(self) -> str:
        return self._json


def _l1_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    )


def _l1_strict_record(raw: bytes) -> None:
    """Reject ambiguous JSON without changing the historical decoders."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise PerformanceObservationError("duplicate L1 source JSON field")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise PerformanceObservationError("non-finite L1 source JSON value")

    try:
        json.loads(raw.decode("utf-8", errors="strict"),
                   object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PerformanceObservationError("invalid or ambiguous L1 source JSON") from exc


def _l1_sources(
    snapshot: RemotePerformanceSnapshot, task_ids: Sequence[str],
) -> tuple[tuple[str, ...], tuple[RemoteTerminalArtifact, ...]]:
    """Check the supplied snapshot only; never resolve or refresh a ref."""

    if not isinstance(snapshot, RemotePerformanceSnapshot):
        raise PerformanceObservationError("L1 requires a canonical terminal snapshot")
    if isinstance(task_ids, (list, tuple)) and any(
        isinstance(item, str) and len(item) > L1_MAX_ID_CHARS for item in task_ids
    ):
        raise PerformanceObservationError("L1 TASK selector exceeds identity bound")
    selectors = validate_task_selectors(task_ids)
    if snapshot.task_selectors != selectors:
        raise PerformanceObservationError("L1 snapshot must match exact sorted TASK selectors")
    if not isinstance(snapshot.terminals, tuple) or (
        len(snapshot.terminals) > PERFORMANCE_MAX_TERMINAL_RUNS
    ):
        raise PerformanceObservationError("L1 terminal snapshot exceeds tuple/bound contract")
    if not isinstance(snapshot.observed_refs, tuple) or (
        len(snapshot.observed_refs) != len(snapshot.terminals)
    ):
        raise PerformanceObservationError("L1 requires exact canonical observed refs")

    expected_refs: dict[str, str] = {}
    seen_runs: set[str] = set()
    seen_artifacts: set[str] = set()
    total_bytes = 0
    for item in snapshot.terminals:
        if not isinstance(item, RemoteTerminalArtifact):
            raise PerformanceObservationError("invalid L1 terminal source")
        if (
            not isinstance(item.run_id, str)
            or len(item.run_id) > L1_MAX_ID_CHARS
            or not isinstance(item.task_id, str)
            or item.task_id not in selectors
            or re.fullmatch(
                rf"RUN-{re.escape(item.task_id.removeprefix('TASK-'))}-\d{{3,}}",
                item.run_id,
            ) is None
            or not isinstance(item.terminal_kind, str)
            or item.terminal_kind not in TERMINAL_KINDS
            or not isinstance(item.artifact_sha, str)
            or re.fullmatch(r"[0-9a-f]{40}", item.artifact_sha) is None
        ):
            raise PerformanceObservationError("invalid L1 canonical terminal identity")
        if item.run_id in seen_runs:
            raise PerformanceObservationError("duplicate/competing L1 terminal RUN identity")
        if item.artifact_sha in seen_artifacts:
            raise PerformanceObservationError("conflicting L1 artifact reused across RUN identities")
        seen_runs.add(item.run_id)
        seen_artifacts.add(item.artifact_sha)
        prefix = "artifacts" if item.terminal_kind == "RESULT" else "failure-artifacts"
        expected_refs[f"refs/heads/aios/{prefix}/{item.run_id}"] = item.artifact_sha
        records = (item.run, item.terminal)
        if item.observation is not None:
            records += (item.observation,)
        for raw in records:
            if not isinstance(raw, bytes) or not 0 < len(raw) <= L1_MAX_RECORD_BYTES:
                raise PerformanceObservationError("L1 source record exceeds bytes/bound contract")
            total_bytes += len(raw)
            if total_bytes > L1_MAX_SNAPSHOT_BYTES:
                raise PerformanceObservationError("L1 source snapshot exceeds byte bound")

    refs: dict[str, str] = {}
    for entry in snapshot.observed_refs:
        if (
            not isinstance(entry, tuple) or len(entry) != 2
            or not all(isinstance(value, str) for value in entry)
        ):
            raise PerformanceObservationError("invalid L1 observed ref binding")
        ref, sha = entry
        if (
            len(ref) > L1_MAX_ID_CHARS + len("refs/heads/aios/failure-artifacts/")
            or len(sha) != 40
            or ref in refs or expected_refs.get(ref) != sha
        ):
            raise PerformanceObservationError("stale/conflicting L1 observed ref binding")
        refs[ref] = sha
    if refs != expected_refs:
        raise PerformanceObservationError("missing L1 canonical observed ref binding")
    # Bound sizes and all refs before decoding any optional observation.
    for item in snapshot.terminals:
        for raw in (item.run, item.terminal, item.observation):
            if raw is not None:
                _l1_strict_record(raw)
    return selectors, tuple(sorted(snapshot.terminals, key=lambda item: item.run_id))


def _l1_coverage(samples: int, population: int) -> dict[str, Any]:
    if not 0 <= samples <= population:
        raise PerformanceObservationError("inconsistent L1 sample coverage")
    return {
        "status": ("UNAVAILABLE" if samples == 0 else
                   "OBSERVED" if samples == population else "PARTIAL"),
        "sample_count": samples,
        "population_count": population,
        "missing_count": population - samples,
    }


def _l1_metrics(
    rows: Sequence[tuple[_BoundTerminal, RunObservation | None]],
) -> dict[str, Any]:
    population = len(rows)
    observations = [observation for _, observation in rows if observation is not None]
    metrics: dict[str, Any] = {}
    for name, attr in (
        ("admitted_run_seconds", "admitted_run_elapsed_seconds"),
        ("executor_seconds", "executor_elapsed_seconds"),
        ("verification_seconds", "verification_elapsed_seconds"),
    ):
        values = [getattr(item, attr) for item in observations
                  if getattr(item, attr) is not None]
        summary = _duration(values)
        metrics[name] = {
            **_l1_coverage(summary.sample_count, population), "unit": "seconds",
            "observed_sum": sum(values) if values else None,
            "p50": summary.p50, "p95": summary.p95,
        }
    for name in ("input_tokens", "cached_input_tokens", "output_tokens"):
        values = [getattr(item.token_usage, name) for item in observations
                  if item.token_usage is not None]
        metrics[name] = {
            **_l1_coverage(len(values), population), "unit": "tokens",
            "observed_sum": sum(values) if values else None,
        }
    return metrics


def _l1_counts(values: Sequence[str], population: int,
               keys: Sequence[str]) -> dict[str, Any]:
    return {
        **_l1_coverage(len(values), population),
        "values": ({key: values.count(key) for key in sorted(set(keys) | set(values))}
                   if values else None),
    }


def _l1_family_key(
    row: tuple[_BoundTerminal, RunObservation | None], family: str,
) -> str | None:
    terminal, observation = row
    return {
        "task": terminal.source.task_id,
        "executor": terminal.executor,
        "terminal_kind": terminal.source.terminal_kind,
        "operation": observation.operation if observation is not None else None,
    }[family]


def _l1_gaps(has_run_sample: bool) -> dict[str, Any]:
    unavailable = (
        "source_preparation_seconds", "git_startup_seconds", "fixture_startup_seconds",
        "worker_startup_seconds", "collection_seconds", "collected_test_items",
        "executed_test_items", "unique_test_items", "equivalent_duplicate_test_items",
        "checkpoint_seconds", "applicability_seconds", "reused_proof_items",
        "invalidated_proof_items", "unknown_proof_items", "feedback_seconds",
        "transport_seconds", "human_seconds", "review_seconds", "publication_seconds",
        "failure_handoff_seconds", "resource_costs", "dollar_cost",
    )
    gaps = {
        name: {"status": "UNAVAILABLE", "value": None,
               "reason": "NOT_REPRESENTED_IN_RUN_OBSERVATION"}
        for name in unavailable
    }
    for name in ("matched_episode_equivalence", "proof_coverage", "proof_applicability",
                 "comparative_speedup", "safety_conformance"):
        gaps[name] = {"status": "UNKNOWN", "value": None,
                      "reason": "NOT_ESTABLISHED_BY_TERMINAL_PERFORMANCE"}
    gaps["whole_episode_seconds"] = {
        "status": "PARTIAL" if has_run_sample else "UNKNOWN", "value": None,
        "available_component": "admitted_run_seconds" if has_run_sample else None,
        "reason": "ADMITTED_RUN_IS_NOT_THE_WHOLE_EPISODE",
    }
    return gaps


def build_l1_baseline_readiness(
    snapshot: RemotePerformanceSnapshot, *, task_ids: Sequence[str],
) -> L1BaselineReadiness:
    """Project an already authenticated snapshot, with no observation or effects.

    The caller supplies the exact TASK set and the existing transport's snapshot.
    Ref equality and hashes detect internal drift, not upstream authenticity or
    present-day freshness. No summary/claim envelope is accepted as a source.
    """

    selectors, terminals = _l1_sources(snapshot, task_ids)
    # Reuse the v1 canonical binders and decoder without changing their behavior.
    # Every terminal binds before missingness or any baseline metric is computed.
    bound = tuple(_bind_terminal(item, selectors) for item in terminals)
    try:
        rows = tuple((item, None if item.source.observation is None else
                      _bind_observation(item.source.observation, item)) for item in bound)
        metrics = _l1_metrics(rows)
    except (OverflowError, RecursionError) as exc:
        raise PerformanceObservationError("L1 numeric/record bound exceeded") from exc

    sources = []
    for item in bound:
        source = item.source
        sources.append({
            "run_id": source.run_id, "task_id": source.task_id,
            "task_revision": item.task_revision, "executor": item.executor,
            "terminal_kind": source.terminal_kind, "artifact_sha": source.artifact_sha,
            "base_sha": item.base_sha, "candidate_sha": item.candidate_sha,
            "run_sha256": hashlib.sha256(source.run).hexdigest(),
            "terminal_sha256": hashlib.sha256(source.terminal).hexdigest(),
            "observation_sha256": (hashlib.sha256(source.observation).hexdigest()
                                   if source.observation is not None else None),
        })
    source_binding = {
        "task_selectors": list(selectors), "terminals": sources,
        "observed_refs": [list(item) for item in sorted(snapshot.observed_refs)],
    }
    population = len(rows)
    observations = [observation for _, observation in rows if observation is not None]
    counts = {
        "by_terminal_kind": _l1_counts(
            [item.source.terminal_kind for item in bound], population, sorted(TERMINAL_KINDS)),
        "by_task": _l1_counts([item.source.task_id for item in bound], population, selectors),
        "by_executor": _l1_counts(
            [item.executor for item in bound], population, sorted(SUPPORTED_EXECUTORS)),
        "by_task_revision": _l1_counts(
            [f"{item.source.task_id}@{item.task_revision}" for item in bound], population, ()),
        "by_operation": _l1_counts(
            [item.operation for item in observations], population, sorted(OPERATIONS)),
    }
    families: dict[str, Any] = {}
    for family, keys in (
        ("task", selectors), ("executor", sorted(SUPPORTED_EXECUTORS)),
        ("terminal_kind", sorted(TERMINAL_KINDS)), ("operation", sorted(OPERATIONS)),
    ):
        groups: dict[str, Any] = {}
        for key in keys:
            selected = tuple(
                row for row in rows if _l1_family_key(row, family) == key
            )
            groups[key] = {
                "terminal_runs": len(selected),
                "outcomes": _l1_counts(
                    [item.source.terminal_kind for item, _ in selected],
                    len(selected), sorted(TERMINAL_KINDS)),
                "metrics": _l1_metrics(selected),
            }
        families[family] = {
            "classification_coverage": _l1_coverage(
                len(observations) if family == "operation" else population, population),
            "unclassified_terminal_runs": population - len(observations)
                if family == "operation" else 0,
            "groups": groups,
        }
    try:
        return L1BaselineReadiness(_l1_json({
            "format": "AIOS_LEAN_VP_L1_BASELINE_READINESS", "version": 1,
            "kind": "L1_BASELINE_READINESS", "authority": "READ_ONLY_NO_DECISIONS",
            "task_selectors": list(selectors),
            "source": {**source_binding, "binding_sha256": hashlib.sha256(
                _l1_json(source_binding).encode("ascii")).hexdigest()},
            "coverage": _l1_coverage(len(observations), population),
            "metrics": metrics, "counts": counts, "families": families,
            "gaps": _l1_gaps(bool(observations)),
        }))
    except (ValueError, OverflowError) as exc:
        raise PerformanceObservationError("L1 aggregate exceeds finite numeric bounds") from exc


__all__ = [
    "CoverageSummary",
    "DurationSummary",
    "L1BaselineReadiness",
    "PerformanceObservation",
    "PerformanceObservationError",
    "TokenUsageSummary",
    "build_l1_baseline_readiness",
    "build_performance_observation",
    "nearest_rank_percentile",
    "observe_performance",
    "validate_performance_observation",
    "validate_task_selectors",
]
