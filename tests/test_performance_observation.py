"""Focused regression coverage for AIOS_PERFORMANCE_OBSERVATION v1."""

from __future__ import annotations

import json

import pytest

from aios_renew.performance_observation import (
    PerformanceObservationError,
    build_performance_observation,
    nearest_rank_percentile,
    validate_task_selectors,
)
from aios_renew.review_transport import (
    RemotePerformanceSnapshot,
    RemoteTerminalArtifact,
)
from aios_renew.run_observation import (
    RunObservation,
    SoftBudget,
    TokenUsage,
    observation_data,
)


def terminal(
    run_id: str,
    *,
    revision: int = 4,
    kind: str = "RESULT",
    observation: RunObservation | None = None,
    run: dict | None = None,
    terminal_document: dict | None = None,
) -> RemoteTerminalArtifact:
    base_sha = "a" * 40
    run_document = run or {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": revision},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": "bounded",
        "head_sha": None,
        "status": "ACTIVE",
    }
    if terminal_document is None and kind == "RESULT":
        terminal_document = {
            "result": {
                "head_sha": "b" * 40,
                "claims": [],
                "changed_files": [],
                "unresolved": [],
            },
            "evidence": [],
        }
    elif terminal_document is None:
        terminal_document = {
            "kind": "FAILURE",
            "run_id": run_id,
            "task": {"id": "TASK-101", "revision": revision},
            "executor": "codex",
            "base_sha": base_sha,
            "failed_head_sha": "c" * 40,
            "phase": "EXECUTION",
            "error": {"type": "Example", "message": "bounded"},
            "candidate": {
                "transportable": True,
                "repairable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        }
    return RemoteTerminalArtifact(
        run_id=run_id,
        task_id="TASK-101",
        terminal_kind=kind,
        artifact_sha="d" * 40,
        run=json.dumps(run_document).encode(),
        terminal=json.dumps(terminal_document).encode(),
        observation=(
            None
            if observation is None
            else json.dumps(observation_data(observation)).encode()
        ),
    )


def observed(
    run_id: str,
    *,
    revision: int = 4,
    operation: str = "PRIMARY",
    kind: str = "RESULT",
    admitted: float = 10.0,
    executor_seconds: float | None = 4.0,
    verification_seconds: float | None = 2.0,
    token_usage: TokenUsage | None = None,
    soft_budget: SoftBudget | None = None,
) -> RunObservation:
    return RunObservation(
        run_id=run_id,
        task_id="TASK-101",
        task_revision=revision,
        operation=operation,
        executor="codex",
        base_sha="a" * 40,
        terminal_kind=kind,
        executor_invoked=executor_seconds is not None,
        admitted_run_elapsed_seconds=admitted,
        executor_elapsed_seconds=executor_seconds,
        verification_elapsed_seconds=verification_seconds,
        token_usage=token_usage,
        soft_budget=soft_budget,
    )


def test_selectors_are_bare_unique_bounded_and_sorted() -> None:
    assert validate_task_selectors(["TASK-102", "TASK-101"]) == (
        "TASK-101",
        "TASK-102",
    )
    for invalid in ("TASK-101:4", "TASK-101@4", "task-101", "TASK-101 "):
        with pytest.raises(PerformanceObservationError):
            validate_task_selectors([invalid])
    with pytest.raises(PerformanceObservationError, match="duplicate"):
        validate_task_selectors(["TASK-101", "TASK-101"])
    with pytest.raises(PerformanceObservationError, match="maximum"):
        validate_task_selectors([f"TASK-{index:03d}" for index in range(33)])


def test_terminal_binding_precedes_missing_coverage_classification() -> None:
    malformed_failure = terminal(
        "RUN-101-001",
        kind="FAILURE",
        terminal_document={
            "kind": "FAILURE",
            "run_id": "RUN-101-OTHER",
            "task": {"id": "TASK-101", "revision": 4},
            "executor": "codex",
            "base_sha": "a" * 40,
            "failed_head_sha": "c" * 40,
            "candidate": {},
        },
    )
    with pytest.raises(PerformanceObservationError, match="FAILURE identity"):
        build_performance_observation(
            RemotePerformanceSnapshot(("TASK-101",), (malformed_failure,))
        )


def test_missing_observation_is_explicit_only_after_valid_terminal() -> None:
    result = build_performance_observation(
        RemotePerformanceSnapshot(
            ("TASK-101",), (terminal("RUN-101-001"),)
        )
    )
    assert result.coverage.terminal_runs == 1
    assert result.coverage.valid_observations == 0
    assert result.coverage.missing_observations == 1
    assert result.failure_rate is None
    assert result.verification_share is None


def test_observation_identity_conflict_fails_closed() -> None:
    mismatched = observed("RUN-101-999")
    item = terminal("RUN-101-001", observation=mismatched)
    with pytest.raises(PerformanceObservationError, match="identity conflicts"):
        build_performance_observation(
            RemotePerformanceSnapshot(("TASK-101",), (item,))
        )


def test_exact_formulas_counts_and_complete_sample_coverage() -> None:
    first = observed(
        "RUN-101-001",
        admitted=10.0,
        verification_seconds=2.0,
        token_usage=TokenUsage(100, 25, 40),
        soft_budget=SoftBudget(1800, "WITHIN"),
    )
    second = observed(
        "RUN-101-002",
        revision=3,
        operation="REPAIR",
        kind="FAILURE",
        admitted=30.0,
        executor_seconds=None,
        verification_seconds=None,
        soft_budget=SoftBudget(900, "NOT_APPLICABLE"),
    )
    result = build_performance_observation(
        RemotePerformanceSnapshot(
            ("TASK-101",),
            (
                terminal("RUN-101-001", observation=first),
                terminal(
                    "RUN-101-002",
                    revision=3,
                    kind="FAILURE",
                    observation=second,
                ),
            ),
        )
    )
    assert result.counts["by_operation"] == {
        "PRIMARY": 1,
        "REMEDIATION": 0,
        "REPAIR": 1,
    }
    assert result.counts["by_task_revision"] == {"3": 1, "4": 1}
    assert result.failure_rate == 0.5
    assert result.verification_share == 2.0 / 40.0
    assert result.durations["admitted_run_seconds"].p50 == 10.0
    assert result.durations["admitted_run_seconds"].p95 == 30.0
    assert result.token_usage.sample_count == 1
    assert result.token_usage.input_tokens == 100
    assert result.coverage.token_usage_samples == 1
    assert result.soft_budget["sample_count"] == 2


def test_nearest_rank_has_no_interpolation_or_rounding() -> None:
    assert nearest_rank_percentile([], 50) is None
    assert nearest_rank_percentile([1.125, 2.875], 50) == 1.125
    assert nearest_rank_percentile([1.125, 2.875], 95) == 2.875
