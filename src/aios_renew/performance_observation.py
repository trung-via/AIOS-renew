"""Bounded read-only aggregation of canonical RUN_OBSERVATION facts."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .review_transport import (
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


__all__ = [
    "CoverageSummary",
    "DurationSummary",
    "PerformanceObservation",
    "PerformanceObservationError",
    "TokenUsageSummary",
    "build_performance_observation",
    "nearest_rank_percentile",
    "observe_performance",
    "validate_performance_observation",
    "validate_task_selectors",
]
