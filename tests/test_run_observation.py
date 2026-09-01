import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aios_renew.run import Run, RunTaskReference
from aios_renew.run_observation import (
    RunObservationError,
    RunObservationTracker,
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
