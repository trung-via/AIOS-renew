import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aios_renew.run import Run, RunTaskReference
from aios_renew.run_observation import (
    RunObservationError,
    RunObservationTracker,
    SoftBudget,
    TokenUsage,
    observation_data,
    persist_observation,
    validate_observation,
    validate_token_usage,
)


class ControlledClock:
    def __init__(self, *values: float) -> None:
        self.values = iter(values)

    def __call__(self) -> float:
        return next(self.values)


def run_record() -> Run:
    return Run(
        run_id="RUN-046-001",
        task=RunTaskReference(id="TASK-046", revision=2),
        executor="codex",
        base_sha="abc123",
        workspace="/repo",
    )


def test_controlled_monotonic_clock_measures_all_three_phases() -> None:
    tracker = RunObservationTracker(
        "PRIMARY",
        monotonic_clock=ControlledClock(10.0, 12.0, 20.0, 22.0, 25.0, 30.0),
    )
    tracker.admit(run_record())
    completed = SimpleNamespace(
        aios_token_usage={
            "input_tokens": 13,
            "cached_input_tokens": 5,
            "output_tokens": 8,
        }
    )
    assert tracker.wrap_native_runner(lambda: completed)() is completed
    verification_started = tracker.begin_verification()
    tracker.end_verification(verification_started)

    observation = tracker.finalize("RESULT")

    assert observation is not None
    assert observation.terminal_kind == "RESULT"
    assert observation.executor_invoked is True
    assert observation.admitted_run_elapsed_seconds == 20.0
    assert observation.executor_elapsed_seconds == 8.0
    assert observation.verification_elapsed_seconds == 3.0
    assert observation.token_usage is not None
    assert observation.token_usage.cached_input_tokens == 5
    assert observation.soft_budget == SoftBudget(1800, "WITHIN")


def test_post_admission_failure_before_executor_is_truthful() -> None:
    tracker = RunObservationTracker(
        "REMEDIATION", monotonic_clock=ControlledClock(4.0, 9.0)
    )
    tracker.admit(run_record())

    observation = tracker.finalize("FAILURE")

    assert observation is not None
    assert observation.terminal_kind == "FAILURE"
    assert observation.executor_invoked is False
    assert observation.executor_elapsed_seconds is None
    assert observation.verification_elapsed_seconds is None
    assert observation.soft_budget == SoftBudget(900, "NOT_APPLICABLE")


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": 1, "output_tokens": 1},
        {"input_tokens": True, "cached_input_tokens": 0, "output_tokens": 1},
        {"input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 1},
        {"input_tokens": -1, "cached_input_tokens": 0, "output_tokens": 1},
    ],
)
def test_malformed_or_partial_token_usage_is_rejected(usage: object) -> None:
    with pytest.raises(RunObservationError):
        validate_token_usage(usage)


def test_untrusted_usage_is_omitted_without_changing_execution_truth() -> None:
    tracker = RunObservationTracker(
        "REPAIR", monotonic_clock=ControlledClock(1.0, 2.0, 3.0, 4.0)
    )
    tracker.admit(run_record())
    completed = SimpleNamespace(
        aios_token_usage={"input_tokens": 1, "output_tokens": 1}
    )
    tracker.wrap_native_runner(lambda: completed)()

    observation = tracker.finalize("FAILURE")

    assert observation is not None
    assert observation.executor_invoked is True
    assert observation.token_usage is None


@pytest.mark.parametrize("elapsed", [float("nan"), float("inf"), -0.01, True])
def test_elapsed_durations_must_be_finite_non_negative(elapsed: object) -> None:
    data = {
        "kind": "RUN_OBSERVATION",
        "run_id": "RUN-046-001",
        "task": {"id": "TASK-046", "revision": 2},
        "operation": "PRIMARY",
        "executor": "codex",
        "base_sha": "abc123",
        "terminal_kind": "RESULT",
        "executor_invoked": False,
        "durations": {
            "admitted_run_seconds": elapsed,
            "executor_seconds": None,
            "verification_seconds": None,
        },
        "token_usage": None,
    }
    with pytest.raises(RunObservationError):
        validate_observation(data)


def test_persistence_is_immutable_and_byte_identical_repetition_is_allowed(
    tmp_path: Path,
) -> None:
    tracker = RunObservationTracker(
        "PRIMARY", monotonic_clock=ControlledClock(1.0, 2.0)
    )
    tracker.admit(run_record())
    observation = tracker.finalize("FAILURE")
    assert observation is not None
    path = tmp_path / "observations" / "RUN-046-001.json"

    persist_observation(path, observation)
    first = path.read_bytes()
    persist_observation(path, observation)

    assert path.read_bytes() == first
    decoded = json.loads(first)
    decoded["base_sha"] = "different"
    conflict = validate_observation(decoded)
    with pytest.raises(RunObservationError, match="conflicting finalized"):
        persist_observation(path, conflict)
    assert json.loads(path.read_bytes()) == observation_data(observation)


def test_provider_specific_field_names_not_in_run_observation_module() -> None:
    import aios_renew.run_observation as ro_mod

    source = Path(ro_mod.__file__).read_text(encoding="utf-8")
    provider_names = [
        "cache_read_tokens",
        "reasoning_output_tokens",
        "cache_write_input_tokens",
        "thinking_tokens",
        "prompt_tokens_details",
        "total_tokens",
        "turn.completed",
        "item.completed",
        "agent_message",
    ]
    for name in provider_names:
        assert name not in source, f"Provider name {name!r} must not appear in run_observation.py"

    with pytest.raises(RunObservationError, match="exact counter group"):
        validate_token_usage({
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "output_tokens": 5,
            "extra_counter": 1,
        })


def test_historical_observation_without_soft_budget_round_trips_unchanged() -> None:
    data = {
        "kind": "RUN_OBSERVATION",
        "run_id": "RUN-046-001",
        "task": {"id": "TASK-046", "revision": 2},
        "operation": "PRIMARY",
        "executor": "codex",
        "base_sha": "abc123",
        "terminal_kind": "RESULT",
        "executor_invoked": True,
        "durations": {
            "admitted_run_seconds": 12.5,
            "executor_seconds": 10.0,
            "verification_seconds": 2.0,
        },
        "token_usage": None,
    }
    observation = validate_observation(data)
    assert observation.token_usage is None
    assert observation.soft_budget is None
    exported = observation_data(observation)
    assert exported["token_usage"] is None
    assert exported == data


@pytest.mark.parametrize(
    ("operation", "elapsed", "threshold", "status"),
    [
        ("PRIMARY", 1800.0, 1800, "WITHIN"),
        ("PRIMARY", 1800.001, 1800, "EXCEEDED"),
        ("REMEDIATION", 900.0, 900, "WITHIN"),
        ("REMEDIATION", 900.001, 900, "EXCEEDED"),
        ("REPAIR", 900.0, 900, "WITHIN"),
        ("REPAIR", 900.001, 900, "EXCEEDED"),
    ],
)
def test_tracker_classifies_exact_operation_soft_budget_boundary(
    operation: str, elapsed: float, threshold: int, status: str
) -> None:
    tracker = RunObservationTracker(
        operation,
        monotonic_clock=ControlledClock(0.0, 1.0, 1.0 + elapsed, 2000.0),
    )
    tracker.admit(run_record())
    tracker.wrap_native_runner(lambda: "done")()

    observation = tracker.finalize("RESULT")

    assert observation is not None
    assert observation.soft_budget == SoftBudget(threshold, status)
    assert observation_data(observation)["soft_budget"] == {
        "threshold_seconds": threshold,
        "status": status,
    }


@pytest.mark.parametrize(
    "soft_budget",
    [
        None,
        {},
        {"threshold_seconds": 1800},
        {"threshold_seconds": 1800, "status": "WITHIN", "extra": True},
        {"threshold_seconds": 1800.0, "status": "WITHIN"},
        {"threshold_seconds": 900, "status": "WITHIN"},
        {"threshold_seconds": 1800, "status": "INVALID"},
        {"threshold_seconds": 1800, "status": "EXCEEDED"},
        {"threshold_seconds": 1800, "status": "NOT_APPLICABLE"},
    ],
)
def test_soft_budget_validation_rejects_malformed_or_conflicting_data(
    soft_budget: object,
) -> None:
    data = {
        "kind": "RUN_OBSERVATION",
        "run_id": "RUN-046-001",
        "task": {"id": "TASK-046", "revision": 2},
        "operation": "PRIMARY",
        "executor": "codex",
        "base_sha": "abc123",
        "terminal_kind": "RESULT",
        "executor_invoked": True,
        "durations": {
            "admitted_run_seconds": 1800.0,
            "executor_seconds": 1800.0,
            "verification_seconds": 0.0,
        },
        "token_usage": None,
        "soft_budget": soft_budget,
    }
    with pytest.raises(RunObservationError):
        validate_observation(data)


def test_non_invoked_soft_budget_requires_not_applicable() -> None:
    data = {
        "kind": "RUN_OBSERVATION",
        "run_id": "RUN-046-001",
        "task": {"id": "TASK-046", "revision": 2},
        "operation": "REMEDIATION",
        "executor": "codex",
        "base_sha": "abc123",
        "terminal_kind": "FAILURE",
        "executor_invoked": False,
        "durations": {
            "admitted_run_seconds": 1.0,
            "executor_seconds": None,
            "verification_seconds": None,
        },
        "token_usage": None,
        "soft_budget": {
            "threshold_seconds": 900,
            "status": "WITHIN",
        },
    }
    with pytest.raises(RunObservationError, match="conflicts"):
        validate_observation(data)


def test_tracker_record_token_usage_lifecycle() -> None:
    tracker = RunObservationTracker(
        "PRIMARY",
        monotonic_clock=ControlledClock(1.0, 2.0, 5.0, 10.0),
    )
    tracker.admit(run_record())

    def fake_native():
        tracker.record_token_usage(TokenUsage(100, 20, 30))
        return "done"

    observed = tracker.wrap_native_runner(fake_native)
    assert observed() == "done"

    obs = tracker.finalize("RESULT")
    assert obs is not None
    assert obs.token_usage == TokenUsage(100, 20, 30)


@pytest.mark.parametrize(
    "invalid_usage",
    [
        {"input_tokens": -5, "cached_input_tokens": 0, "output_tokens": 10},
        {"input_tokens": 100, "cached_input_tokens": True, "output_tokens": 10},
        {"input_tokens": 50, "cached_input_tokens": 100, "output_tokens": 10},
        TokenUsage(input_tokens=50, cached_input_tokens=100, output_tokens=10),
        "malformed string",
    ],
)
def test_tracker_record_token_usage_fails_soft_on_invalid(invalid_usage: object) -> None:
    tracker = RunObservationTracker(
        "PRIMARY",
        monotonic_clock=ControlledClock(1.0, 2.0, 5.0, 10.0),
    )
    tracker.admit(run_record())

    def fake_native():
        tracker.record_token_usage(invalid_usage)
        return "done"

    observed = tracker.wrap_native_runner(fake_native)
    observed()
    obs = tracker.finalize("RESULT")
    assert obs is not None
    assert obs.token_usage is None


def test_executor_not_invoked_cannot_have_token_usage() -> None:
    data = {
        "kind": "RUN_OBSERVATION",
        "run_id": "RUN-046-001",
        "task": {"id": "TASK-046", "revision": 2},
        "operation": "PRIMARY",
        "executor": "codex",
        "base_sha": "abc123",
        "terminal_kind": "RESULT",
        "executor_invoked": False,
        "durations": {
            "admitted_run_seconds": 5.0,
            "executor_seconds": None,
            "verification_seconds": 4.0,
        },
        "token_usage": {
            "input_tokens": 100,
            "cached_input_tokens": 10,
            "output_tokens": 20,
        },
    }
    with pytest.raises(RunObservationError, match="requires an invoked Executor"):
        validate_observation(data)
