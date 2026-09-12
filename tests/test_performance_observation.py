"""Unit tests for AIOS_PERFORMANCE_OBSERVATION v1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aios_renew.performance_observation import (
    CoverageSummary,
    DurationSummary,
    PerformanceObservation,
    PerformanceObservationError,
    TokenUsageSummary,
    build_performance_observation,
    nearest_rank_percentile,
    validate_performance_observation,
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


def test_nearest_rank_percentile_formulas() -> None:
    assert nearest_rank_percentile([], 50) is None
    assert nearest_rank_percentile([], 95) is None

    single = [42.0]
    assert nearest_rank_percentile(single, 50) == 42.0
    assert nearest_rank_percentile(single, 95) == 42.0

    two = [10.0, 20.0]
    assert nearest_rank_percentile(two, 50) == 10.0
    assert nearest_rank_percentile(two, 95) == 20.0

    three = [10.0, 20.0, 30.0]
    assert nearest_rank_percentile(three, 50) == 20.0
    assert nearest_rank_percentile(three, 95) == 30.0

    four = [10.0, 20.0, 30.0, 40.0]
    assert nearest_rank_percentile(four, 50) == 20.0
    assert nearest_rank_percentile(four, 95) == 40.0

    twenty = [float(x) for x in range(1, 21)]
    assert nearest_rank_percentile(twenty, 50) == 10.0
    assert nearest_rank_percentile(twenty, 95) == 19.0

    floats = [1.125, 2.875, 5.5]
    assert nearest_rank_percentile(floats, 50) == 2.875
    assert nearest_rank_percentile(floats, 95) == 5.5


def test_validate_task_selectors_edge_cases() -> None:
    with pytest.raises(PerformanceObservationError, match="between 1 and 32"):
        validate_task_selectors([])

    with pytest.raises(PerformanceObservationError, match="maximum bound of 32"):
        validate_task_selectors([f"TASK-{i:03d}" for i in range(33)])

    with pytest.raises(PerformanceObservationError, match="duplicate identities"):
        validate_task_selectors(["TASK-001", "TASK-001"])

    for invalid in ["task-001", "TASK-", "TASK-001/foo", "TASK-001 ", "TASK-001\n", "OTHER-001"]:
        with pytest.raises(PerformanceObservationError, match="malformed TASK selector identity"):
            validate_task_selectors([invalid])

    valid = ["TASK-002", "TASK-001", "TASK-100"]
    result = validate_task_selectors(valid)
    assert result == ("TASK-001", "TASK-002", "TASK-100")


def make_terminal_artifact(
    run_id: str,
    task_id: str = "TASK-101",
    task_revision: int = 1,
    operation: str = "PRIMARY",
    terminal_kind: str = "RESULT",
    executor: str = "codex",
    base_sha: str = "a" * 40,
    workspace: str = "w",
    observation: RunObservation | None = None,
    corrupt_observation: bytes | None = None,
    run_override: dict | None = None,
) -> RemoteTerminalArtifact:
    if run_override is not None:
        run_doc = run_override
    else:
        run_doc = {
            "run_id": run_id,
            "task": {"id": task_id, "revision": task_revision},
            "executor": executor,
            "base_sha": base_sha,
            "workspace": workspace,
            "status": "ACTIVE",
        }
    if terminal_kind == "RESULT":
        terminal_doc: dict = {
            "result": {"head_sha": "b" * 40, "claims": [], "changed_files": [], "unresolved": []},
            "evidence": [],
        }
    else:
        terminal_doc = {
            "kind": "FAILURE",
            "run_id": run_id,
            "task": {"id": task_id, "revision": task_revision},
            "executor": executor,
            "base_sha": base_sha,
            "failed_head_sha": "c" * 40,
            "candidate": {
                "transportable": True,
                "repairable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        }

    obs_bytes = None
    if corrupt_observation is not None:
        obs_bytes = corrupt_observation
    elif observation is not None:
        obs_bytes = json.dumps(observation_data(observation)).encode("utf-8")

    return RemoteTerminalArtifact(
        run_id=run_id,
        task_id=task_id,
        terminal_kind=terminal_kind,
        artifact_sha="f" * 40,
        run=json.dumps(run_doc).encode("utf-8"),
        terminal=json.dumps(terminal_doc).encode("utf-8"),
        observation=obs_bytes,
    )


def make_remediation_terminal_artifact(
    run_id: str,
    task_id: str = "TASK-101",
    task_revision: int = 1,
    terminal_kind: str = "RESULT",
    executor: str = "antigravity",
    base_sha: str = "a" * 40,
    workspace: str = "w",
    observation: RunObservation | None = None,
    corrupt_observation: bytes | None = None,
    execution_override: dict | None = None,
    predecessor: dict | None = None,
) -> RemoteTerminalArtifact:
    if execution_override is not None:
        execution = execution_override
    else:
        execution = {
            "review_id": "REVIEW-101-001",
            "finding": {
                "id": "F101-001",
                "basis": "AC3",
                "action": "CODE_FIX",
                "location": "src/foo.py",
                "issue": "issue text",
                "expected": "expected text",
            },
            "remediation": {
                "finding_id": "F101-001",
                "action": "CODE_FIX",
                "reviewed_sha": base_sha,
                "constraints": [],
                "modification_scope": ["src/foo.py"],
            },
            "run": {
                "run_id": run_id,
                "task": {"id": task_id, "revision": task_revision},
                "executor": executor,
                "base_sha": base_sha,
                "workspace": workspace,
                "status": "ACTIVE",
            },
            "original_constraints": [],
        }

    run_doc: dict = {
        "kind": "REMEDIATION",
        "execution": execution,
    }
    if predecessor is not None:
        run_doc["predecessor"] = predecessor

    if terminal_kind == "RESULT":
        terminal_doc: dict = {
            "result": {"head_sha": "b" * 40, "claims": [], "changed_files": [], "unresolved": []},
            "evidence": [],
        }
    else:
        terminal_doc = {
            "kind": "FAILURE",
            "run_id": run_id,
            "task": {"id": task_id, "revision": task_revision},
            "executor": executor,
            "base_sha": base_sha,
            "failed_head_sha": "c" * 40,
            "candidate": {
                "transportable": True,
                "repairable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        }

    obs_bytes = None
    if corrupt_observation is not None:
        obs_bytes = corrupt_observation
    elif observation is not None:
        obs_bytes = json.dumps(observation_data(observation)).encode("utf-8")

    return RemoteTerminalArtifact(
        run_id=run_id,
        task_id=task_id,
        terminal_kind=terminal_kind,
        artifact_sha="f" * 40,
        run=json.dumps(run_doc).encode("utf-8"),
        terminal=json.dumps(terminal_doc).encode("utf-8"),
        observation=obs_bytes,
    )


def test_empty_snapshot_returns_null_metrics() -> None:
    snapshot = RemotePerformanceSnapshot(
        task_selectors=("TASK-101",),
        terminals=(),
    )
    obs = build_performance_observation(snapshot)
    assert obs.coverage.terminal_runs == 0
    assert obs.coverage.valid_observations == 0
    assert obs.coverage.missing_observations == 0
    assert obs.failure_rate is None
    assert obs.verification_share is None
    assert obs.token_usage.sample_count == 0
    assert obs.token_usage.input_tokens is None
    assert obs.token_usage.cached_input_tokens is None
    assert obs.token_usage.output_tokens is None
    assert obs.soft_budget["sample_count"] == 0
    assert obs.counts["by_operation"] == {"PRIMARY": 0, "REMEDIATION": 0, "REPAIR": 0}
    assert obs.durations["executor_seconds"].p50 is None
    assert obs.durations["executor_seconds"].p95 is None

    data = obs.as_dict()
    round_tripped = validate_performance_observation(data)
    assert round_tripped.task_selectors == ("TASK-101",)


def test_missing_observation_is_explicit_coverage_gap() -> None:
    obs1 = RunObservation(
        run_id="RUN-101-001",
        task_id="TASK-101",
        task_revision=1,
        operation="PRIMARY",
        executor="codex",
        base_sha="a" * 40,
        terminal_kind="RESULT",
        executor_invoked=True,
        admitted_run_elapsed_seconds=10.0,
        executor_elapsed_seconds=5.0,
        verification_elapsed_seconds=2.0,
    )
    term1 = make_terminal_artifact("RUN-101-001", observation=obs1)
    term2 = make_terminal_artifact("RUN-101-002", observation=None)

    snapshot = RemotePerformanceSnapshot(
        task_selectors=("TASK-101",),
        terminals=(term1, term2),
    )
    obs = build_performance_observation(snapshot)
    assert obs.coverage.terminal_runs == 2
    assert obs.coverage.valid_observations == 1
    assert obs.coverage.missing_observations == 1
    assert obs.counts["by_operation"]["PRIMARY"] == 1
    assert obs.failure_rate == 0.0


def test_malformed_observation_fails_closed() -> None:
    term = make_terminal_artifact(
        "RUN-101-001",
        corrupt_observation=b'{"kind": "NOT_AN_OBSERVATION"}',
    )
    snapshot = RemotePerformanceSnapshot(
        task_selectors=("TASK-101",),
        terminals=(term,),
    )
    with pytest.raises(PerformanceObservationError, match="invalid RUN_OBSERVATION"):
        build_performance_observation(snapshot)


def test_observation_binding_mismatches_fail_closed() -> None:
    base_obs = RunObservation(
        run_id="RUN-101-001",
        task_id="TASK-101",
        task_revision=1,
        operation="PRIMARY",
        executor="codex",
        base_sha="a" * 40,
        terminal_kind="RESULT",
        executor_invoked=True,
        admitted_run_elapsed_seconds=10.0,
        executor_elapsed_seconds=5.0,
        verification_elapsed_seconds=2.0,
    )

    # run_id mismatch
    mismatched_run = RunObservation(
        run_id="RUN-101-999",
        task_id=base_obs.task_id,
        task_revision=base_obs.task_revision,
        operation=base_obs.operation,
        executor=base_obs.executor,
        base_sha=base_obs.base_sha,
        terminal_kind=base_obs.terminal_kind,
        executor_invoked=True,
        admitted_run_elapsed_seconds=10.0,
        executor_elapsed_seconds=5.0,
        verification_elapsed_seconds=2.0,
    )
    term = make_terminal_artifact("RUN-101-001", observation=mismatched_run)
    with pytest.raises(PerformanceObservationError, match="run_id mismatch"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # executor mismatch
    mismatched_executor = RunObservation(
        run_id="RUN-101-001",
        task_id=base_obs.task_id,
        task_revision=base_obs.task_revision,
        operation=base_obs.operation,
        executor="antigravity",
        base_sha=base_obs.base_sha,
        terminal_kind=base_obs.terminal_kind,
        executor_invoked=True,
        admitted_run_elapsed_seconds=10.0,
        executor_elapsed_seconds=5.0,
        verification_elapsed_seconds=2.0,
    )
    term = make_terminal_artifact("RUN-101-001", executor="codex", observation=mismatched_executor)
    with pytest.raises(PerformanceObservationError, match="executor mismatch"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # base_sha mismatch
    mismatched_base = RunObservation(
        run_id="RUN-101-001",
        task_id=base_obs.task_id,
        task_revision=base_obs.task_revision,
        operation=base_obs.operation,
        executor="codex",
        base_sha="b" * 40,
        terminal_kind=base_obs.terminal_kind,
        executor_invoked=True,
        admitted_run_elapsed_seconds=10.0,
        executor_elapsed_seconds=5.0,
        verification_elapsed_seconds=2.0,
    )
    term = make_terminal_artifact("RUN-101-001", base_sha="a" * 40, observation=mismatched_base)
    with pytest.raises(PerformanceObservationError, match="base_sha mismatch"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # terminal_kind mismatch
    mismatched_kind = RunObservation(
        run_id="RUN-101-001",
        task_id=base_obs.task_id,
        task_revision=base_obs.task_revision,
        operation=base_obs.operation,
        executor="codex",
        base_sha="a" * 40,
        terminal_kind="FAILURE",
        executor_invoked=True,
        admitted_run_elapsed_seconds=10.0,
        executor_elapsed_seconds=5.0,
        verification_elapsed_seconds=2.0,
    )
    term = make_terminal_artifact("RUN-101-001", terminal_kind="RESULT", observation=mismatched_kind)
    with pytest.raises(PerformanceObservationError, match="terminal_kind mismatch"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))


def test_full_aggregation_deterministic_formulas() -> None:
    obs1 = RunObservation(
        run_id="RUN-101-001",
        task_id="TASK-101",
        task_revision=1,
        operation="PRIMARY",
        executor="codex",
        base_sha="a" * 40,
        terminal_kind="RESULT",
        executor_invoked=True,
        admitted_run_elapsed_seconds=100.0,
        executor_elapsed_seconds=50.0,
        verification_elapsed_seconds=10.0,
        token_usage=TokenUsage(1000, 200, 300),
        soft_budget=SoftBudget(threshold_seconds=1800, status="WITHIN"),
    )
    obs2 = RunObservation(
        run_id="RUN-101-002",
        task_id="TASK-101",
        task_revision=1,
        operation="REMEDIATION",
        executor="antigravity",
        base_sha="a" * 40,
        terminal_kind="FAILURE",
        executor_invoked=True,
        admitted_run_elapsed_seconds=40.0,
        executor_elapsed_seconds=20.0,
        verification_elapsed_seconds=5.0,
        token_usage=TokenUsage(500, 100, 200),
        soft_budget=SoftBudget(threshold_seconds=900, status="WITHIN"),
    )
    obs3 = RunObservation(
        run_id="RUN-101-003",
        task_id="TASK-101",
        task_revision=2,
        operation="REPAIR",
        executor="antigravity",
        base_sha="a" * 40,
        terminal_kind="RESULT",
        executor_invoked=True,
        admitted_run_elapsed_seconds=1000.0,
        executor_elapsed_seconds=950.0,
        verification_elapsed_seconds=None,
        token_usage=None,
        soft_budget=SoftBudget(threshold_seconds=900, status="EXCEEDED"),
    )
    obs4 = RunObservation(
        run_id="RUN-101-004",
        task_id="TASK-101",
        task_revision=2,
        operation="PRIMARY",
        executor="codex",
        base_sha="a" * 40,
        terminal_kind="RESULT",
        executor_invoked=False,
        admitted_run_elapsed_seconds=10.0,
        executor_elapsed_seconds=None,
        verification_elapsed_seconds=None,
        token_usage=None,
        soft_budget=SoftBudget(threshold_seconds=1800, status="NOT_APPLICABLE"),
    )
    # Historical observation without soft_budget
    obs5 = RunObservation(
        run_id="RUN-101-005",
        task_id="TASK-101",
        task_revision=1,
        operation="PRIMARY",
        executor="codex",
        base_sha="a" * 40,
        terminal_kind="RESULT",
        executor_invoked=True,
        admitted_run_elapsed_seconds=60.0,
        executor_elapsed_seconds=30.0,
        verification_elapsed_seconds=15.0,
        token_usage=None,
        soft_budget=None,
    )

    t1 = make_terminal_artifact("RUN-101-001", operation="PRIMARY", terminal_kind="RESULT", executor="codex", task_revision=1, observation=obs1)
    t2 = make_terminal_artifact("RUN-101-002", operation="REMEDIATION", terminal_kind="FAILURE", executor="antigravity", task_revision=1, observation=obs2)
    t3 = make_terminal_artifact("RUN-101-003", operation="REPAIR", terminal_kind="RESULT", executor="antigravity", task_revision=2, observation=obs3)
    t4 = make_terminal_artifact("RUN-101-004", operation="PRIMARY", terminal_kind="RESULT", executor="codex", task_revision=2, observation=obs4)
    t5 = make_terminal_artifact("RUN-101-005", operation="PRIMARY", terminal_kind="RESULT", executor="codex", task_revision=1, observation=obs5)

    snapshot = RemotePerformanceSnapshot(
        task_selectors=("TASK-101",),
        terminals=(t1, t2, t3, t4, t5),
    )
    obs = build_performance_observation(snapshot)

    # Coverage
    assert obs.coverage.terminal_runs == 5
    assert obs.coverage.valid_observations == 5
    assert obs.coverage.missing_observations == 0
    assert obs.coverage.token_usage_samples == 2
    assert obs.coverage.soft_budget_samples == 4

    # Counts
    assert obs.counts["by_operation"] == {"PRIMARY": 3, "REMEDIATION": 1, "REPAIR": 1}
    assert obs.counts["by_terminal_kind"] == {"FAILURE": 1, "RESULT": 4}
    assert obs.counts["by_executor"] == {"antigravity": 2, "codex": 3}
    assert obs.counts["by_task_revision"] == {"1": 3, "2": 2}
    # Soft budget status: historical run is NOT fabricated
    assert obs.counts["by_soft_budget_status"] == {
        "EXCEEDED": 1,
        "NOT_APPLICABLE": 1,
        "WITHIN": 2,
    }
    assert obs.soft_budget["sample_count"] == 4

    # failure_rate
    assert obs.failure_rate == 1 / 5

    # verification_share: (10 + 5 + 0 + 0 + 15) / (100 + 40 + 1000 + 10 + 60) = 30 / 1210
    expected_share = 30.0 / 1210.0
    assert obs.verification_share == pytest.approx(expected_share)

    # token_usage: sum of obs1 + obs2
    assert obs.token_usage.sample_count == 2
    assert obs.token_usage.input_tokens == 1500
    assert obs.token_usage.cached_input_tokens == 300
    assert obs.token_usage.output_tokens == 500

    # durations
    # admitted_run_seconds: [10.0, 40.0, 60.0, 100.0, 1000.0] (5 values)
    # p50: ceil(50*5/100) = 3 -> sorted[2] = 60.0
    # p95: ceil(95*5/100) = 5 -> sorted[4] = 1000.0
    assert obs.durations["admitted_run_seconds"].sample_count == 5
    assert obs.durations["admitted_run_seconds"].p50 == 60.0
    assert obs.durations["admitted_run_seconds"].p95 == 1000.0

    # executor_seconds: [20.0, 30.0, 50.0, 950.0] (4 values; non-invoked is None)
    # p50: ceil(50*4/100) = 2 -> sorted[1] = 30.0
    # p95: ceil(95*4/100) = 4 -> sorted[3] = 950.0
    assert obs.durations["executor_seconds"].sample_count == 4
    assert obs.durations["executor_seconds"].p50 == 30.0
    assert obs.durations["executor_seconds"].p95 == 950.0

    # verification_seconds: [5.0, 10.0, 15.0] (3 values; None excluded)
    # p50: ceil(50*3/100) = 2 -> sorted[1] = 10.0
    # p95: ceil(95*3/100) = 3 -> sorted[2] = 15.0
    assert obs.durations["verification_seconds"].sample_count == 3
    assert obs.durations["verification_seconds"].p50 == 10.0
    assert obs.durations["verification_seconds"].p95 == 15.0

    # Validation
    round_trip = validate_performance_observation(obs.as_dict())
    assert round_trip.failure_rate == obs.failure_rate
    assert round_trip.verification_share == obs.verification_share


def test_verification_share_zero_admitted_returns_null() -> None:
    obs = RunObservation(
        run_id="RUN-101-001",
        task_id="TASK-101",
        task_revision=1,
        operation="PRIMARY",
        executor="codex",
        base_sha="a" * 40,
        terminal_kind="RESULT",
        executor_invoked=True,
        admitted_run_elapsed_seconds=0.0,
        executor_elapsed_seconds=0.0,
        verification_elapsed_seconds=0.0,
    )
    term = make_terminal_artifact("RUN-101-001", observation=obs)
    perf = build_performance_observation(
        RemotePerformanceSnapshot(("TASK-101",), (term,))
    )
    assert perf.verification_share is None


def test_validate_performance_observation_catches_invalid_payloads() -> None:
    valid_payload = {
        "format": "AIOS_PERFORMANCE_OBSERVATION",
        "version": 1,
        "kind": "PERFORMANCE_OBSERVATION",
        "task_selectors": ["TASK-101"],
        "coverage": {
            "terminal_runs": 1,
            "valid_observations": 1,
            "missing_observations": 0,
        },
        "counts": {
            "by_operation": {"PRIMARY": 1},
            "by_terminal_kind": {"RESULT": 1},
            "by_executor": {"codex": 1},
            "by_task_revision": {"1": 1},
            "by_soft_budget_status": {"WITHIN": 1},
        },
        "durations": {
            "admitted_run_seconds": {"sample_count": 1, "p50": 10.0, "p95": 10.0},
            "executor_seconds": {"sample_count": 1, "p50": 5.0, "p95": 5.0},
            "verification_seconds": {"sample_count": 1, "p50": 2.0, "p95": 2.0},
        },
        "failure_rate": 0.0,
        "verification_share": 0.2,
        "token_usage": {
            "sample_count": 1,
            "input_tokens": 100,
            "cached_input_tokens": 50,
            "output_tokens": 20,
        },
        "soft_budget": {
            "sample_count": 1,
            "counts": {"WITHIN": 1},
        },
    }

    # Format mismatch
    bad = dict(valid_payload, format="WRONG")
    with pytest.raises(PerformanceObservationError, match="format/version/kind mismatch"):
        validate_performance_observation(bad)

    # Version mismatch
    bad = dict(valid_payload, version=2)
    with pytest.raises(PerformanceObservationError, match="format/version/kind mismatch"):
        validate_performance_observation(bad)

    # Coverage sum mismatch
    bad = dict(valid_payload)
    bad["coverage"] = {"terminal_runs": 5, "valid_observations": 1, "missing_observations": 1}
    with pytest.raises(PerformanceObservationError, match="coverage.terminal_runs must equal"):
        validate_performance_observation(bad)

    # p50 > p95
    bad = dict(valid_payload)
    bad["durations"] = {
        "admitted_run_seconds": {"sample_count": 2, "p50": 20.0, "p95": 10.0},
        "executor_seconds": {"sample_count": 0, "p50": None, "p95": None},
        "verification_seconds": {"sample_count": 0, "p50": None, "p95": None},
    }
    with pytest.raises(PerformanceObservationError, match="must not exceed p95"):
        validate_performance_observation(bad)

    # cached tokens > input tokens
    bad = dict(valid_payload)
    bad["token_usage"] = {
        "sample_count": 1,
        "input_tokens": 100,
        "cached_input_tokens": 150,
        "output_tokens": 10,
    }
    with pytest.raises(PerformanceObservationError, match="cached_input_tokens must not exceed"):
        validate_performance_observation(bad)


def test_malformed_run_metadata_fails_closed_even_without_observation() -> None:
    # Unsupported executor fails closed even when observation is missing
    term = make_terminal_artifact("RUN-101-001", executor="unsupported_executor", observation=None)
    with pytest.raises(PerformanceObservationError, match="invalid run.json"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # Empty workspace fails closed even when observation is missing
    term = make_terminal_artifact("RUN-101-001", workspace="", observation=None)
    with pytest.raises(PerformanceObservationError, match="invalid run.json"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # Missing workspace fails closed even when observation is missing
    term = make_terminal_artifact(
        "RUN-101-001",
        run_override={
            "run_id": "RUN-101-001",
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": "a" * 40,
            "status": "ACTIVE",
        },
        observation=None,
    )
    with pytest.raises(PerformanceObservationError, match="invalid run.json"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # Non-positive revision fails closed
    term = make_terminal_artifact("RUN-101-001", task_revision=0, observation=None)
    with pytest.raises(PerformanceObservationError, match="invalid run.json"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # Non-ACTIVE status fails closed
    term = make_terminal_artifact(
        "RUN-101-001",
        run_override={
            "run_id": "RUN-101-001",
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": "a" * 40,
            "workspace": "w",
            "status": "TERMINATED",
        },
        observation=None,
    )
    with pytest.raises(PerformanceObservationError, match="status is not ACTIVE"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # run_id mismatch fails closed
    term = make_terminal_artifact(
        "RUN-101-001",
        run_override={
            "run_id": "RUN-101-999",
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": "a" * 40,
            "workspace": "w",
            "status": "ACTIVE",
        },
        observation=None,
    )
    with pytest.raises(PerformanceObservationError, match="run.json run_id mismatch"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # task_id mismatch fails closed
    term = make_terminal_artifact(
        "RUN-101-001",
        run_override={
            "run_id": "RUN-101-001",
            "task": {"id": "TASK-999", "revision": 1},
            "executor": "codex",
            "base_sha": "a" * 40,
            "workspace": "w",
            "status": "ACTIVE",
        },
        observation=None,
    )
    with pytest.raises(PerformanceObservationError, match="run.json task.id mismatch"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))

    # Unknown run kind fails closed
    term = make_terminal_artifact(
        "RUN-101-001",
        run_override={
            "kind": "UNKNOWN",
            "run_id": "RUN-101-001",
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": "a" * 40,
            "workspace": "w",
            "status": "ACTIVE",
        },
        observation=None,
    )
    with pytest.raises(PerformanceObservationError, match="unknown run kind"):
        build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))


def test_remediation_run_wrapper_validation() -> None:
    # Valid remediation run with missing observation is treated as coverage gap
    term = make_remediation_terminal_artifact("RUN-101-001", observation=None)
    obs = build_performance_observation(RemotePerformanceSnapshot(("TASK-101",), (term,)))
    assert obs.coverage.terminal_runs == 1
    assert obs.coverage.valid_observations == 0
    assert obs.coverage.missing_observations == 1

    # Valid remediation run with valid observation succeeds
    obs_item = RunObservation(
        run_id="RUN-101-001",
        task_id="TASK-101",
        task_revision=1,
        operation="REMEDIATION",
        executor="antigravity",
        base_sha="a" * 40,
        terminal_kind="RESULT",
        executor_invoked=True,
        admitted_run_elapsed_seconds=10.0,
        executor_elapsed_seconds=5.0,
        verification_elapsed_seconds=2.0,
    )
    term_valid = make_remediation_terminal_artifact("RUN-101-001", observation=obs_item)
    obs_valid = build_performance_observation(
        RemotePerformanceSnapshot(("TASK-101",), (term_valid,))
    )
    assert obs_valid.coverage.terminal_runs == 1
    assert obs_valid.coverage.valid_observations == 1
    assert obs_valid.coverage.missing_observations == 0

    # Remediation run with missing execution fails closed
    term_bad_exec = make_remediation_terminal_artifact(
        "RUN-101-001",
        execution_override={"run": {}},
        observation=None,
    )
    with pytest.raises(PerformanceObservationError, match="invalid run.json"):
        build_performance_observation(
            RemotePerformanceSnapshot(("TASK-101",), (term_bad_exec,))
        )

    # Remediation run with unsupported executor in execution.run fails closed
    term_bad_exec_run = make_remediation_terminal_artifact(
        "RUN-101-001",
        executor="unsupported_executor",
        observation=None,
    )
    with pytest.raises(PerformanceObservationError, match="invalid run.json"):
        build_performance_observation(
            RemotePerformanceSnapshot(("TASK-101",), (term_bad_exec_run,))
        )

    # Remediation run with empty workspace in execution.run fails closed
    term_bad_workspace = make_remediation_terminal_artifact(
        "RUN-101-001",
        workspace="",
        observation=None,
    )
    with pytest.raises(PerformanceObservationError, match="invalid run.json"):
        build_performance_observation(
            RemotePerformanceSnapshot(("TASK-101",), (term_bad_workspace,))
        )

