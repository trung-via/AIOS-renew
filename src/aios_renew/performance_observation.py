'''Read-only bounded aggregation of canonical RUN_OBSERVATION facts.

Emits deterministic `AIOS_PERFORMANCE_OBSERVATION` v1 envelopes from the
exact validated remote RUN terminal identity already established by
:mod:ios_renew.review_transport. Mutation, RUN creation, Executor
invocation, Runtime writes, publication, and Git ref mutation are
explicitly out of scope for this module.
'''
from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .run_observation import (
    OPERATIONS,
    SOFT_BUDGET_STATUSES,
    TERMINAL_KINDS,
    RunObservation,
    RunObservationError,
    validate_observation,
)
from .review_transport import (
    RemotePerformanceNamespace,
    RemotePerformanceTerminal,
    resolve_remote_performance_namespace,
)


PERFORMANCE_VERSION = 1
PERFORMANCE_KIND = "AIOS_PERFORMANCE_OBSERVATION"
PERFORMANCE_MAX_SELECTORS = 32


class PerformanceObservationError(ValueError):
    """Raised when performance observation cannot be produced safely."""


@dataclass(frozen=True)
class PerformanceSelector:
    """One validated TASK identity submitted to `aios performance`."""

    task_id: str
    revision: int

    def render(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "revision": self.revision}


@dataclass(frozen=True)
class PerformanceCoverage:
    """Coverage classification emitted inside the v1 envelope."""

    selectors_requested: int
    selectors_resolved: int
    distinct_terminal_runs: int
    conflicting_run_ids: tuple[tuple[str, str], ...]
    overflow: bool
    missing_observations: tuple[str, ...]
    malformed_observations: tuple[str, ...]


@dataclass(frozen=True)
class PerformanceCounts:
    """Counts of bound terminal observations grouped by canonical dimensions."""

    by_operation: Mapping[str, int]
    by_terminal_kind: Mapping[str, int]
    by_executor: Mapping[str, int]
    by_task_revision: Mapping[str, int]
    by_soft_budget_status: Mapping[str, int]


@dataclass(frozen=True)
class PerformanceDurationSummary:
    """Duration statistics for one phase over exact non-null samples."""

    samples: int
    p50: float | None
    p95: float | None


@dataclass(frozen=True)
class PerformanceTokenSummary:
    """Aggregated token totals for trusted complete samples."""

    sample_count: int
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class PerformanceSoftBudgetSummary:
    """Soft-budget classification coverage across trusted observations."""

    by_status: Mapping[str, int]
    trusted_sample_count: int


@dataclass(frozen=True)
class PerformanceObservation:
    """The exact `AIOS_PERFORMANCE_OBSERVATION` v1 envelope."""

    kind: str
    version: int
    selectors: tuple[PerformanceSelector, ...]
    coverage: PerformanceCoverage
    counts: PerformanceCounts
    admitted_run_seconds: PerformanceDurationSummary
    executor_seconds: PerformanceDurationSummary
    verification_seconds: PerformanceDurationSummary
    failure_rate: float | None
    verification_share: float | None
    token_usage: PerformanceTokenSummary
    soft_budget: PerformanceSoftBudgetSummary

    def render(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "version": self.version,
            "selectors": [selector.render() for selector in self.selectors],
            "coverage": {
                "selectors_requested": self.coverage.selectors_requested,
                "selectors_resolved": self.coverage.selectors_resolved,
                "distinct_terminal_runs": self.coverage.distinct_terminal_runs,
                "conflicting_run_ids": [
                    list(pair) for pair in self.coverage.conflicting_run_ids
                ],
                "overflow": self.coverage.overflow,
                "missing_observations": list(self.coverage.missing_observations),
                "malformed_observations": list(self.coverage.malformed_observations),
            },
            "counts": {
                "by_operation": dict(self.counts.by_operation),
                "by_terminal_kind": dict(self.counts.by_terminal_kind),
                "by_executor": dict(self.counts.by_executor),
                "by_task_revision": dict(self.counts.by_task_revision),
                "by_soft_budget_status": dict(self.counts.by_soft_budget_status),
            },
            "durations": {
                "admitted_run_seconds": _duration_to_dict(
                    self.admitted_run_seconds
                ),
                "executor_seconds": _duration_to_dict(self.executor_seconds),
                "verification_seconds": _duration_to_dict(
                    self.verification_seconds
                ),
            },
            "failure_rate": self.failure_rate,
            "verification_share": self.verification_share,
            "token_usage": {
                "sample_count": self.token_usage.sample_count,
                "input_tokens": self.token_usage.input_tokens,
                "cached_input_tokens": self.token_usage.cached_input_tokens,
                "output_tokens": self.token_usage.output_tokens,
            },
            "soft_budget": {
                "by_status": dict(self.soft_budget.by_status),
                "trusted_sample_count": self.soft_budget.trusted_sample_count,
            },
        }


def parse_task_selectors(
    raw: Sequence[object] | Iterable[object],
) -> tuple[PerformanceSelector, ...]:
    """Parse, dedupe, and validate TASK selectors for `aios performance`."""

    if raw is None:
        raise PerformanceObservationError("task selectors are required")
    selectors: list[PerformanceSelector] = []
    seen: set[tuple[str, int]] = set()
    for entry in raw:
        if isinstance(entry, PerformanceSelector):
            selector = entry
        elif isinstance(entry, Mapping):
            task_id = entry.get("task_id")
            revision = entry.get("revision")
            if not isinstance(task_id, str) or not task_id:
                raise PerformanceObservationError(
                    "task selector missing task_id"
                )
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 1
            ):
                raise PerformanceObservationError(
                    f"task selector revision is invalid for {task_id}"
                )
            selector = PerformanceSelector(task_id=task_id, revision=int(revision))
        elif isinstance(entry, str):
            selector = _parse_string_selector(entry)
        else:
            raise PerformanceObservationError(
                "task selector must be a TASK id or mapping"
            )
        if not _is_valid_task_id(selector.task_id):
            raise PerformanceObservationError(
                f"invalid TASK id: {selector.task_id!r}"
            )
        identity = (selector.task_id, selector.revision)
        if identity in seen:
            raise PerformanceObservationError(
                f"duplicate TASK selector: {selector.task_id}@{selector.revision}"
            )
        seen.add(identity)
        selectors.append(selector)
    if not selectors:
        raise PerformanceObservationError("task selectors are required")
    if len(selectors) > PERFORMANCE_MAX_SELECTORS:
        raise PerformanceObservationError(
            f"more than {PERFORMANCE_MAX_SELECTORS} task selectors are not allowed"
        )
    selectors.sort(key=lambda item: (item.task_id, item.revision))
    return tuple(selectors)


def collect_performance_observation(
    repo: str | Path,
    selectors: Sequence[object] | Iterable[object],
) -> PerformanceObservation:
    """Produce the v1 `AIOS_PERFORMANCE_OBSERVATION` envelope for selectors."""

    resolved_selectors = parse_task_selectors(selectors)
    from pathlib import Path
    repository = repo if isinstance(repo, Path) else Path(repo)
    namespaces: list[RemotePerformanceNamespace] = []
    for selector in resolved_selectors:
        namespaces.append(
            resolve_remote_performance_namespace(
                repository,
                task_id=selector.task_id,
                task_revision=selector.revision,
            )
        )
    return _aggregate_envelope(resolved_selectors, namespaces)


def _parse_string_selector(value: str) -> PerformanceSelector:
    if ":" not in value:
        raise PerformanceObservationError(
            f"task selector must include explicit revision: {value!r}"
        )
    task_id, _, revision_text = value.partition(":")
    task_id = task_id.strip()
    revision_text = revision_text.strip()
    if not task_id or not revision_text:
        raise PerformanceObservationError(
            f"task selector must be TASK-id@revision form: {value!r}"
        )
    try:
        revision = int(revision_text)
    except ValueError as exc:
        raise PerformanceObservationError(
            f"task selector revision must be an integer: {value!r}"
        ) from exc
    if revision < 1:
        raise PerformanceObservationError(
            f"task selector revision must be positive: {value!r}"
        )
    return PerformanceSelector(task_id=task_id, revision=revision)


def _is_valid_task_id(task_id: str) -> bool:
    if not isinstance(task_id, str) or not task_id:
        return False
    if "/" in task_id or "\\" in task_id or " " in task_id:
        return False
    return task_id.startswith("TASK-") and bool(task_id[len("TASK-"):])


def _aggregate_envelope(
    selectors: Sequence[PerformanceSelector],
    namespaces: Sequence[RemotePerformanceNamespace],
) -> PerformanceObservation:
    by_operation: Counter[str] = Counter()
    by_terminal_kind: Counter[str] = Counter()
    by_executor: Counter[str] = Counter()
    by_task_revision: Counter[str] = Counter()
    by_soft_budget_status: Counter[str] = Counter()

    admitted_samples: list[float] = []
    executor_samples: list[float] = []
    verification_samples: list[float] = []
    admitted_total = 0.0
    verification_total = 0.0
    failure_count = 0
    valid_observations = 0

    trusted_input = 0
    trusted_cached = 0
    trusted_output = 0
    trusted_token_count = 0
    trusted_soft_budget_count = 0

    missing_observations: list[str] = []
    malformed_observations: list[str] = []
    distinct_run_ids: set[str] = set()
    all_conflicts: list[tuple[str, str]] = []
    overflow_seen = False

    for namespace in namespaces:
        if namespace.overflow:
            overflow_seen = True
        for conflict in namespace.conflicting_run_ids:
            all_conflicts.append((namespace.task_id, conflict))
        for terminal in namespace.terminals:
            distinct_run_ids.add(terminal.run_id)
            observation = _bind_observation(
                terminal, missing_observations, malformed_observations
            )
            if observation is None:
                continue
            valid_observations += 1
            by_operation[observation.operation] += 1
            by_terminal_kind[observation.terminal_kind] += 1
            by_executor[observation.executor] += 1
            by_task_revision[str(namespace.task_revision)] += 1
            if observation.soft_budget is not None:
                by_soft_budget_status[observation.soft_budget.status] += 1
                trusted_soft_budget_count += 1
            admitted_samples.append(observation.admitted_run_elapsed_seconds)
            admitted_total += observation.admitted_run_elapsed_seconds
            if observation.executor_elapsed_seconds is not None:
                executor_samples.append(observation.executor_elapsed_seconds)
            if observation.verification_elapsed_seconds is not None:
                verification_samples.append(observation.verification_elapsed_seconds)
                verification_total += observation.verification_elapsed_seconds
            if observation.terminal_kind == "FAILURE":
                failure_count += 1
            if observation.token_usage is not None:
                trusted_input += observation.token_usage.input_tokens
                trusted_cached += observation.token_usage.cached_input_tokens
                trusted_output += observation.token_usage.output_tokens
                trusted_token_count += 1

    counts = PerformanceCounts(
        by_operation=_empty_counter(by_operation, OPERATIONS),
        by_terminal_kind=_empty_counter(by_terminal_kind, TERMINAL_KINDS),
        by_executor=dict(sorted(by_executor.items())),
        by_task_revision=dict(sorted(by_task_revision.items(), key=lambda item: int(item[0]))),
        by_soft_budget_status=_empty_counter(
            by_soft_budget_status, SOFT_BUDGET_STATUSES
        ),
    )

    failure_rate = (
        failure_count / valid_observations
        if valid_observations > 0
        else None
    )
    verification_share = (
        verification_total / admitted_total
        if admitted_total > 0 and valid_observations > 0
        else None
    )

    coverage = PerformanceCoverage(
        selectors_requested=len(selectors),
        selectors_resolved=len({selector.task_id for selector in selectors}),
        distinct_terminal_runs=len(distinct_run_ids),
        conflicting_run_ids=tuple(sorted(all_conflicts)),
        overflow=overflow_seen,
        missing_observations=tuple(sorted(missing_observations)),
        malformed_observations=tuple(sorted(malformed_observations)),
    )

    return PerformanceObservation(
        kind=PERFORMANCE_KIND,
        version=PERFORMANCE_VERSION,
        selectors=tuple(selectors),
        coverage=coverage,
        counts=counts,
        admitted_run_seconds=_summarise(admitted_samples),
        executor_seconds=_summarise(executor_samples),
        verification_seconds=_summarise(verification_samples),
        failure_rate=_round_ratio(failure_rate),
        verification_share=_round_ratio(verification_share),
        token_usage=PerformanceTokenSummary(
            sample_count=trusted_token_count,
            input_tokens=trusted_input if trusted_token_count > 0 else None,
            cached_input_tokens=(
                trusted_cached if trusted_token_count > 0 else None
            ),
            output_tokens=trusted_output if trusted_token_count > 0 else None,
        ),
        soft_budget=PerformanceSoftBudgetSummary(
            by_status=counts.by_soft_budget_status,
            trusted_sample_count=trusted_soft_budget_count,
        ),
    )


def _bind_observation(
    terminal: RemotePerformanceTerminal,
    missing: list[str],
    malformed: list[str],
) -> RunObservation | None:
    if terminal.observation_bytes is None:
        missing.append(terminal.run_id)
        return None
    try:
        raw = json.loads(terminal.observation_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        malformed.append(terminal.run_id)
        return None
    if not isinstance(raw, Mapping):
        malformed.append(terminal.run_id)
        return None
    try:
        observation = validate_observation(raw)
    except RunObservationError:
        malformed.append(terminal.run_id)
        return None
    if observation.run_id != terminal.run_id:
        malformed.append(terminal.run_id)
        return None
    if observation.task_id != terminal.task_id:
        malformed.append(terminal.run_id)
        return None
    if observation.task_revision != terminal.task_revision:
        malformed.append(terminal.run_id)
        return None
    if observation.executor != terminal.executor:
        malformed.append(terminal.run_id)
        return None
    if observation.base_sha != terminal.base_sha:
        malformed.append(terminal.run_id)
        return None
    if observation.terminal_kind != terminal.kind:
        malformed.append(terminal.run_id)
        return None
    return observation


def _empty_counter(
    counter: Counter[str], keys: Iterable[str]
) -> Mapping[str, int]:
    base = {key: 0 for key in keys}
    for key, value in counter.items():
        base[key] = value
    return dict(sorted(base.items()))


def _summarise(samples: Sequence[float]) -> PerformanceDurationSummary:
    cleaned = [float(value) for value in samples if value is not None]
    cleaned = [value for value in cleaned if math.isfinite(value) and value >= 0]
    cleaned.sort()
    p50 = _percentile(cleaned, 50)
    p95 = _percentile(cleaned, 95)
    return PerformanceDurationSummary(
        samples=len(cleaned), p50=p50, p95=p95
    )


def _percentile(sorted_samples: Sequence[float], percentile: float) -> float | None:
    if not sorted_samples:
        return None
    rank = math.ceil(percentile * len(sorted_samples) / 100)
    rank = max(1, min(rank, len(sorted_samples)))
    return sorted_samples[rank - 1]


def _round_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 6)


def _duration_to_dict(summary: PerformanceDurationSummary) -> dict[str, Any]:
    return {
        "samples": summary.samples,
        "p50": summary.p50,
        "p95": summary.p95,
    }


__all__ = [
    "PerformanceObservation",
    "PerformanceObservationError",
    "PerformanceSelector",
    "collect_performance_observation",
    "parse_task_selectors",
]
