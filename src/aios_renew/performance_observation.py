"""Bounded read-only Performance Observation Surface for AIOS-renew."""

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
    ReviewTransportError,
    resolve_remote_performance_snapshot,
)
from .run import SUPPORTED_EXECUTORS
from .run_observation import (
    OPERATIONS,
    SOFT_BUDGET_STATUSES,
    RunObservation,
    RunObservationError,
    validate_observation,
)


TASK_ID_PATTERN = re.compile(r"^TASK-[A-Za-z0-9_-]+$")


class PerformanceObservationError(ValueError, RuntimeError):
    """Raised when performance aggregation fails or observation is malformed."""


def nearest_rank_percentile(values: Sequence[float], p: int) -> float | None:
    """Nearest-rank percentile over non-null values: one-based rank ceil(p*n/100)."""

    non_null = [v for v in values if v is not None]
    if not non_null:
        return None
    sorted_values = sorted(non_null)
    n = len(sorted_values)
    rank = math.ceil(p * n / 100)
    return sorted_values[rank - 1]


def validate_task_selectors(task_ids: Sequence[str]) -> tuple[str, ...]:
    """Validate 1-32 unique exact TASK identities and fail closed on invalid selectors."""

    if not isinstance(task_ids, (list, tuple)) or not task_ids:
        raise PerformanceObservationError(
            "task selectors must contain between 1 and 32 exact TASK identities"
        )
    if len(task_ids) > 32:
        raise PerformanceObservationError("task selectors exceed maximum bound of 32")
    if len(task_ids) != len(set(task_ids)):
        raise PerformanceObservationError("task selectors contain duplicate identities")
    for item in task_ids:
        if not isinstance(item, str) or not TASK_ID_PATTERN.fullmatch(item):
            raise PerformanceObservationError(
                f"malformed TASK selector identity: {item!r}"
            )
    return tuple(sorted(task_ids))


@dataclass(frozen=True)
class DurationSummary:
    """One duration summary with exact nearest-rank percentiles."""

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
    """Sample coverage and exact totals for complete trusted token usage."""

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
    """Snapshot coverage distinguishing valid observations from coverage gaps."""

    terminal_runs: int
    valid_observations: int
    observed_runs: int
    missing_observations: int
    token_usage_samples: int
    soft_budget_samples: int

    def as_dict(self) -> dict[str, Any]:
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
    """Stable AIOS_PERFORMANCE_OBSERVATION v1 data structure."""

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
        durations_dict = {
            k: v.as_dict() for k, v in sorted(self.durations.items())
        }
        return {
            "format": "AIOS_PERFORMANCE_OBSERVATION",
            "version": 1,
            "kind": "PERFORMANCE_OBSERVATION",
            "task_selectors": list(self.task_selectors),
            "coverage": self.coverage.as_dict(),
            "counts": dict(self.counts),
            "durations": durations_dict,
            "duration_summaries": durations_dict,
            "failure_rate": self.failure_rate,
            "verification_share": self.verification_share,
            "token_usage": self.token_usage.as_dict(),
            "soft_budget": dict(self.soft_budget),
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def _operator():
    import aios_renew.operator as op

    return op


def build_performance_observation(
    snapshot: RemotePerformanceSnapshot,
) -> PerformanceObservation:
    """Construct one PerformanceObservation from validated snapshot records."""

    task_selectors = tuple(sorted(snapshot.task_selectors))
    valid_observations: list[RunObservation] = []

    for terminal in snapshot.terminals:
        try:
            run_data = json.loads(terminal.run.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PerformanceObservationError(
                f"invalid run.json for {terminal.run_id}: {exc}"
            ) from exc
        if not isinstance(run_data, Mapping):
            raise PerformanceObservationError(
                f"run.json must be a mapping for {terminal.run_id}"
            )

        if "kind" in run_data and run_data.get("kind") != "REMEDIATION":
            raise PerformanceObservationError(
                f"unknown run kind at {terminal.run_id}"
            )

        try:
            op = _operator()
            if run_data.get("kind") == "REMEDIATION":
                execution = op._remediation_execution_from_data(
                    run_data.get("execution")
                )
                if "predecessor" in run_data:
                    pred = op._parse_remediation_predecessor(
                        run_data["predecessor"]
                    )
                    if (
                        pred.review_id != execution.review_id
                        or pred.finding_id != execution.finding.id
                        or pred.reviewed_sha != execution.remediation.reviewed_sha
                    ):
                        raise ValueError("predecessor mismatch")
                if "execution_base" in run_data:
                    base = op._parse_remediation_execution_base(
                        run_data["execution_base"]
                    )
                    if base.candidate_sha != execution.run.base_sha:
                        raise ValueError("execution_base mismatch")
                canonical_run = execution.run
            else:
                canonical_run = op._run_from_data(run_data)
        except (KeyError, TypeError, ValueError) as exc:
            raise PerformanceObservationError(
                f"invalid run.json for {terminal.run_id}: {exc}"
            ) from exc

        if canonical_run.run_id != terminal.run_id:
            raise PerformanceObservationError(
                f"run.json run_id mismatch: expected {terminal.run_id}, got {canonical_run.run_id}"
            )
        if canonical_run.task.id != terminal.task_id:
            raise PerformanceObservationError(
                f"run.json task.id mismatch: expected {terminal.task_id}, got {canonical_run.task.id}"
            )
        if canonical_run.status != "ACTIVE":
            raise PerformanceObservationError(
                f"run.json status is not ACTIVE for {terminal.run_id}"
            )

        try:
            terminal_doc = json.loads(
                terminal.terminal.decode("utf-8", errors="strict")
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PerformanceObservationError(
                f"invalid terminal JSON for {terminal.run_id}: {exc}"
            ) from exc
        if not isinstance(terminal_doc, Mapping):
            raise PerformanceObservationError(
                f"terminal document must be a mapping for {terminal.run_id}"
            )
        if terminal.terminal_kind == "RESULT":
            if "result" not in terminal_doc or "evidence" not in terminal_doc:
                raise PerformanceObservationError(
                    f"canonical RESULT is incomplete for {terminal.run_id}"
                )
        elif terminal.terminal_kind == "FAILURE":
            if (
                terminal_doc.get("kind") != "FAILURE"
                or terminal_doc.get("run_id") != terminal.run_id
            ):
                raise PerformanceObservationError(
                    f"canonical FAILURE identity mismatch for {terminal.run_id}"
                )

        if terminal.observation is not None:
            try:
                obs_raw = json.loads(
                    terminal.observation.decode("utf-8", errors="strict")
                )
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise PerformanceObservationError(
                    f"invalid observation JSON for {terminal.run_id}: {exc}"
                ) from exc
            try:
                obs = validate_observation(obs_raw)
            except RunObservationError as exc:
                raise PerformanceObservationError(
                    f"invalid RUN_OBSERVATION for {terminal.run_id}: {exc}"
                ) from exc

            if obs.run_id != terminal.run_id:
                raise PerformanceObservationError(
                    f"observation run_id mismatch: expected {terminal.run_id}, got {obs.run_id}"
                )
            if obs.task_id != terminal.task_id:
                raise PerformanceObservationError(
                    f"observation task_id mismatch: expected {terminal.task_id}, got {obs.task_id}"
                )
            if obs.task_revision != canonical_run.task.revision:
                raise PerformanceObservationError(
                    f"observation task_revision mismatch: expected {canonical_run.task.revision}, got {obs.task_revision}"
                )
            if obs.executor != canonical_run.executor:
                raise PerformanceObservationError(
                    f"observation executor mismatch: expected {canonical_run.executor}, got {obs.executor}"
                )
            if obs.base_sha != canonical_run.base_sha:
                raise PerformanceObservationError(
                    f"observation base_sha mismatch: expected {canonical_run.base_sha}, got {obs.base_sha}"
                )
            if obs.terminal_kind != terminal.terminal_kind:
                raise PerformanceObservationError(
                    f"observation terminal_kind mismatch: expected {terminal.terminal_kind}, got {obs.terminal_kind}"
                )

            valid_observations.append(obs)

    terminal_runs = len(snapshot.terminals)
    valid_obs_count = len(valid_observations)
    missing_obs_count = terminal_runs - valid_obs_count

    obs_with_tokens = [
        obs for obs in valid_observations if obs.token_usage is not None
    ]
    token_sample_count = len(obs_with_tokens)
    if token_sample_count == 0:
        token_usage_summary = TokenUsageSummary(
            sample_count=0,
            sample_coverage=0,
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
        )
    else:
        token_usage_summary = TokenUsageSummary(
            sample_count=token_sample_count,
            sample_coverage=token_sample_count,
            input_tokens=sum(o.token_usage.input_tokens for o in obs_with_tokens),  # type: ignore[union-attr]
            cached_input_tokens=sum(
                o.token_usage.cached_input_tokens for o in obs_with_tokens  # type: ignore[union-attr]
            ),
            output_tokens=sum(
                o.token_usage.output_tokens for o in obs_with_tokens  # type: ignore[union-attr]
            ),
        )

    obs_with_soft_budget = [
        obs for obs in valid_observations if obs.soft_budget is not None
    ]
    soft_budget_sample_count = len(obs_with_soft_budget)

    by_soft_budget_status = {
        "EXCEEDED": sum(
            1 for o in obs_with_soft_budget if o.soft_budget.status == "EXCEEDED"  # type: ignore[union-attr]
        ),
        "NOT_APPLICABLE": sum(
            1
            for o in obs_with_soft_budget
            if o.soft_budget.status == "NOT_APPLICABLE"  # type: ignore[union-attr]
        ),
        "WITHIN": sum(
            1 for o in obs_with_soft_budget if o.soft_budget.status == "WITHIN"  # type: ignore[union-attr]
        ),
    }

    coverage = CoverageSummary(
        terminal_runs=terminal_runs,
        valid_observations=valid_obs_count,
        observed_runs=valid_obs_count,
        missing_observations=missing_obs_count,
        token_usage_samples=token_sample_count,
        soft_budget_samples=soft_budget_sample_count,
    )

    by_operation = {
        "PRIMARY": sum(1 for o in valid_observations if o.operation == "PRIMARY"),
        "REMEDIATION": sum(
            1 for o in valid_observations if o.operation == "REMEDIATION"
        ),
        "REPAIR": sum(1 for o in valid_observations if o.operation == "REPAIR"),
    }

    by_terminal_kind = {
        "FAILURE": sum(1 for o in valid_observations if o.terminal_kind == "FAILURE"),
        "RESULT": sum(1 for o in valid_observations if o.terminal_kind == "RESULT"),
    }

    by_executor: dict[str, int] = {"antigravity": 0, "codex": 0}
    for o in valid_observations:
        by_executor[o.executor] = by_executor.get(o.executor, 0) + 1
    by_executor = dict(sorted(by_executor.items()))

    by_task_revision: dict[str, int] = {}
    for o in valid_observations:
        key = str(o.task_revision)
        by_task_revision[key] = by_task_revision.get(key, 0) + 1
    by_task_revision = {
        k: by_task_revision[k] for k in sorted(by_task_revision, key=int)
    }

    counts = {
        "by_operation": by_operation,
        "by_terminal_kind": by_terminal_kind,
        "by_executor": by_executor,
        "by_task_revision": by_task_revision,
        "by_soft_budget_status": by_soft_budget_status,
        "operation": by_operation,
        "terminal_kind": by_terminal_kind,
        "executor": by_executor,
        "task_revision": by_task_revision,
        "soft_budget_status": by_soft_budget_status,
    }

    admitted_values = [
        o.admitted_run_elapsed_seconds
        for o in valid_observations
        if o.admitted_run_elapsed_seconds is not None
    ]
    executor_values = [
        o.executor_elapsed_seconds
        for o in valid_observations
        if o.executor_elapsed_seconds is not None
    ]
    verification_values = [
        o.verification_elapsed_seconds
        for o in valid_observations
        if o.verification_elapsed_seconds is not None
    ]

    durations = {
        "admitted_run_seconds": DurationSummary(
            sample_count=len(admitted_values),
            p50=nearest_rank_percentile(admitted_values, 50),
            p95=nearest_rank_percentile(admitted_values, 95),
        ),
        "executor_seconds": DurationSummary(
            sample_count=len(executor_values),
            p50=nearest_rank_percentile(executor_values, 50),
            p95=nearest_rank_percentile(executor_values, 95),
        ),
        "verification_seconds": DurationSummary(
            sample_count=len(verification_values),
            p50=nearest_rank_percentile(verification_values, 50),
            p95=nearest_rank_percentile(verification_values, 95),
        ),
    }

    if valid_obs_count == 0:
        failure_rate = None
        verification_share = None
    else:
        failure_rate = by_terminal_kind["FAILURE"] / valid_obs_count
        summed_verification = sum(
            o.verification_elapsed_seconds or 0.0 for o in valid_observations
        )
        summed_admitted = sum(
            o.admitted_run_elapsed_seconds for o in valid_observations
        )
        if summed_admitted == 0.0:
            verification_share = None
        else:
            verification_share = summed_verification / summed_admitted

    soft_budget = {
        "sample_count": soft_budget_sample_count,
        "counts": by_soft_budget_status,
    }

    return PerformanceObservation(
        task_selectors=task_selectors,
        coverage=coverage,
        counts=counts,
        durations=durations,
        failure_rate=failure_rate,
        verification_share=verification_share,
        token_usage=token_usage_summary,
        soft_budget=soft_budget,
    )


def validate_performance_observation(data: Any) -> PerformanceObservation:
    """Validate decoded AIOS_PERFORMANCE_OBSERVATION v1 data structure."""

    if not isinstance(data, Mapping):
        raise PerformanceObservationError("PERFORMANCE_OBSERVATION must be a mapping")

    format_val = data.get("format")
    version_val = data.get("version")
    kind_val = data.get("kind")
    if (
        format_val != "AIOS_PERFORMANCE_OBSERVATION"
        or version_val != 1
        or kind_val != "PERFORMANCE_OBSERVATION"
    ):
        raise PerformanceObservationError(
            "format/version/kind mismatch for AIOS_PERFORMANCE_OBSERVATION"
        )

    task_selectors_raw = data.get("task_selectors")
    if not isinstance(task_selectors_raw, list):
        raise PerformanceObservationError("task_selectors must be a list")
    task_selectors = validate_task_selectors(task_selectors_raw)
    if task_selectors_raw != list(task_selectors):
        raise PerformanceObservationError("task_selectors must be sorted")

    coverage_raw = data.get("coverage")
    if not isinstance(coverage_raw, Mapping):
        raise PerformanceObservationError("coverage must be a mapping")
    terminal_runs = coverage_raw.get("terminal_runs")
    valid_obs = coverage_raw.get("valid_observations")
    missing_obs = coverage_raw.get("missing_observations")
    for name, val in (
        ("terminal_runs", terminal_runs),
        ("valid_observations", valid_obs),
        ("missing_observations", missing_obs),
    ):
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise PerformanceObservationError(
                f"coverage.{name} must be a non-negative integer"
            )
    assert isinstance(terminal_runs, int)
    assert isinstance(valid_obs, int)
    assert isinstance(missing_obs, int)
    if terminal_runs != valid_obs + missing_obs:
        raise PerformanceObservationError(
            "coverage.terminal_runs must equal valid_observations + missing_observations"
        )

    counts_raw = data.get("counts")
    if not isinstance(counts_raw, Mapping):
        raise PerformanceObservationError("counts must be a mapping")

    for section in ("by_operation", "by_terminal_kind", "by_executor", "by_task_revision", "by_soft_budget_status"):
        val = counts_raw.get(section)
        if not isinstance(val, Mapping):
            raise PerformanceObservationError(f"counts.{section} must be a mapping")
        for k, v in val.items():
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise PerformanceObservationError(
                    f"counts.{section}.{k} must be a non-negative integer"
                )

    durations_raw = data.get("durations")
    if not isinstance(durations_raw, Mapping):
        raise PerformanceObservationError("durations must be a mapping")
    durations_out: dict[str, DurationSummary] = {}
    for name in ("admitted_run_seconds", "executor_seconds", "verification_seconds"):
        d = durations_raw.get(name)
        if not isinstance(d, Mapping):
            raise PerformanceObservationError(f"durations.{name} must be a mapping")
        sc = d.get("sample_count")
        p50 = d.get("p50")
        p95 = d.get("p95")
        if isinstance(sc, bool) or not isinstance(sc, int) or sc < 0:
            raise PerformanceObservationError(
                f"durations.{name}.sample_count must be a non-negative integer"
            )
        if sc == 0:
            if p50 is not None or p95 is not None:
                raise PerformanceObservationError(
                    f"durations.{name} percentiles must be null when sample_count is 0"
                )
        else:
            for pname, pval in (("p50", p50), ("p95", p95)):
                if (
                    isinstance(pval, bool)
                    or not isinstance(pval, (int, float))
                    or not math.isfinite(pval)
                    or pval < 0
                ):
                    raise PerformanceObservationError(
                        f"durations.{name}.{pname} must be a finite non-negative number"
                    )
            assert isinstance(p50, (int, float)) and isinstance(p95, (int, float))
            if p50 > p95:
                raise PerformanceObservationError(
                    f"durations.{name}.p50 must not exceed p95"
                )
        durations_out[name] = DurationSummary(sample_count=sc, p50=p50, p95=p95)

    failure_rate = data.get("failure_rate")
    if valid_obs == 0:
        if failure_rate is not None:
            raise PerformanceObservationError(
                "failure_rate must be null when valid_observations is 0"
            )
    else:
        if (
            isinstance(failure_rate, bool)
            or not isinstance(failure_rate, (int, float))
            or not math.isfinite(failure_rate)
            or failure_rate < 0
            or failure_rate > 1
        ):
            raise PerformanceObservationError("failure_rate must be a number between 0 and 1")

    verification_share = data.get("verification_share")
    if valid_obs == 0:
        if verification_share is not None:
            raise PerformanceObservationError(
                "verification_share must be null when valid_observations is 0"
            )
    elif verification_share is not None:
        if (
            isinstance(verification_share, bool)
            or not isinstance(verification_share, (int, float))
            or not math.isfinite(verification_share)
            or verification_share < 0
        ):
            raise PerformanceObservationError(
                "verification_share must be a finite non-negative number or null"
            )

    token_usage_raw = data.get("token_usage")
    if not isinstance(token_usage_raw, Mapping):
        raise PerformanceObservationError("token_usage must be a mapping")
    t_sc = token_usage_raw.get("sample_count")
    if isinstance(t_sc, bool) or not isinstance(t_sc, int) or t_sc < 0:
        raise PerformanceObservationError(
            "token_usage.sample_count must be a non-negative integer"
        )
    inp = token_usage_raw.get("input_tokens")
    cached = token_usage_raw.get("cached_input_tokens")
    outp = token_usage_raw.get("output_tokens")
    if t_sc == 0:
        if inp is not None or cached is not None or outp is not None:
            raise PerformanceObservationError(
                "token_usage counters must be null when sample_count is 0"
            )
    else:
        for tname, tval in (
            ("input_tokens", inp),
            ("cached_input_tokens", cached),
            ("output_tokens", outp),
        ):
            if isinstance(tval, bool) or not isinstance(tval, int) or tval < 0:
                raise PerformanceObservationError(
                    f"token_usage.{tname} must be a non-negative integer"
                )
        assert isinstance(inp, int) and isinstance(cached, int)
        if cached > inp:
            raise PerformanceObservationError(
                "token_usage.cached_input_tokens must not exceed input_tokens"
            )

    soft_budget_raw = data.get("soft_budget")
    if not isinstance(soft_budget_raw, Mapping):
        raise PerformanceObservationError("soft_budget must be a mapping")
    sb_sc = soft_budget_raw.get("sample_count")
    if isinstance(sb_sc, bool) or not isinstance(sb_sc, int) or sb_sc < 0:
        raise PerformanceObservationError(
            "soft_budget.sample_count must be a non-negative integer"
        )
    sb_counts = soft_budget_raw.get("counts")
    if not isinstance(sb_counts, Mapping):
        raise PerformanceObservationError("soft_budget.counts must be a mapping")

    coverage = CoverageSummary(
        terminal_runs=terminal_runs,
        valid_observations=valid_obs,
        observed_runs=coverage_raw.get("observed_runs", valid_obs),
        missing_observations=missing_obs,
        token_usage_samples=coverage_raw.get("token_usage_samples", t_sc),
        soft_budget_samples=coverage_raw.get("soft_budget_samples", sb_sc),
    )

    token_usage_summary = TokenUsageSummary(
        sample_count=t_sc,
        sample_coverage=token_usage_raw.get("sample_coverage", t_sc),
        input_tokens=inp,
        cached_input_tokens=cached,
        output_tokens=outp,
    )

    return PerformanceObservation(
        task_selectors=task_selectors,
        coverage=coverage,
        counts=counts_raw,
        durations=durations_out,
        failure_rate=failure_rate,
        verification_share=verification_share,
        token_usage=token_usage_summary,
        soft_budget=soft_budget_raw,
    )


def observe_performance(
    task_ids: Sequence[str], *, repo: str | Path | None = None
) -> PerformanceObservation:
    """Read-only observational aggregation of canonical remote RUN_OBSERVATION facts."""

    validated_selectors = validate_task_selectors(task_ids)
    op = _operator()

    root = op.resolve_repository(repo)
    try:
        with op._remote_observation_repository(root) as observer:
            snapshot = resolve_remote_performance_snapshot(
                observer, task_ids=validated_selectors
            )
            return build_performance_observation(snapshot)
    except ReviewTransportError as exc:
        raise PerformanceObservationError(str(exc)) from exc
