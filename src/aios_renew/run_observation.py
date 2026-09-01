"""Bounded Runtime-owned operational observations for admitted RUNs."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from .run import SUPPORTED_EXECUTORS, Run


MonotonicClock = Callable[[], float]
P = ParamSpec("P")
T = TypeVar("T")

OPERATIONS = frozenset({"PRIMARY", "REMEDIATION", "REPAIR"})
TERMINAL_KINDS = frozenset({"RESULT", "FAILURE"})


class RunObservationError(ValueError):
    """Raised when an observation is invalid or conflicts with immutable state."""


@dataclass(frozen=True)
class TokenUsage:
    """Exact all-or-none counters emitted by the observed native invocation."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class RunObservation:
    """One terminal operational observation bound to one admitted RUN."""

    run_id: str
    task_id: str
    task_revision: int
    operation: str
    executor: str
    base_sha: str
    terminal_kind: str
    executor_invoked: bool
    admitted_run_elapsed_seconds: float
    executor_elapsed_seconds: float | None
    verification_elapsed_seconds: float | None
    token_usage: TokenUsage | None = None


def validate_token_usage(data: Any) -> TokenUsage:
    """Validate exact trusted counters without accepting partial or bool values."""

    if not isinstance(data, Mapping):
        raise RunObservationError("token_usage must be a mapping")
    required = {"input_tokens", "cached_input_tokens", "output_tokens"}
    if set(data) != required:
        raise RunObservationError("token_usage must contain the exact counter group")
    values: dict[str, int] = {}
    for name in sorted(required):
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RunObservationError(
                f"token_usage.{name} must be a non-negative integer"
            )
        values[name] = value
    if values["cached_input_tokens"] > values["input_tokens"]:
        raise RunObservationError(
            "token_usage.cached_input_tokens must not exceed input_tokens"
        )
    return TokenUsage(**values)


def validate_observation(data: Any) -> RunObservation:
    """Validate the complete, bounded observation representation."""

    if not isinstance(data, Mapping):
        raise RunObservationError("RUN_OBSERVATION must be a mapping")
    required = {
        "kind",
        "run_id",
        "task",
        "operation",
        "executor",
        "base_sha",
        "terminal_kind",
        "executor_invoked",
        "durations",
        "token_usage",
    }
    if set(data) != required or data.get("kind") != "RUN_OBSERVATION":
        raise RunObservationError("RUN_OBSERVATION fields do not match the contract")
    task = data["task"]
    durations = data["durations"]
    if not isinstance(task, Mapping) or set(task) != {"id", "revision"}:
        raise RunObservationError("task must contain exactly id and revision")
    if not isinstance(durations, Mapping) or set(durations) != {
        "admitted_run_seconds",
        "executor_seconds",
        "verification_seconds",
    }:
        raise RunObservationError("durations fields do not match the contract")

    task_revision = task["revision"]
    if (
        isinstance(task_revision, bool)
        or not isinstance(task_revision, int)
        or task_revision < 1
    ):
        raise RunObservationError("task.revision must be a positive integer")
    operation = _bounded_string(data["operation"], "operation")
    if operation not in OPERATIONS:
        raise RunObservationError("operation is not supported")
    terminal_kind = _bounded_string(data["terminal_kind"], "terminal_kind")
    if terminal_kind not in TERMINAL_KINDS:
        raise RunObservationError("terminal_kind must be RESULT or FAILURE")
    invoked = data["executor_invoked"]
    if not isinstance(invoked, bool):
        raise RunObservationError("executor_invoked must be a boolean")

    executor_elapsed = _optional_elapsed(
        durations["executor_seconds"], "durations.executor_seconds"
    )
    if invoked != (executor_elapsed is not None):
        raise RunObservationError(
            "executor_seconds must be present exactly when executor_invoked is true"
        )
    usage_data = data["token_usage"]
    usage = None if usage_data is None else validate_token_usage(usage_data)
    if usage is not None and not invoked:
        raise RunObservationError("token_usage requires an invoked Executor")

    executor = _bounded_string(data["executor"], "executor")
    if executor not in SUPPORTED_EXECUTORS:
        raise RunObservationError("executor is not supported")

    return RunObservation(
        run_id=_bounded_string(data["run_id"], "run_id"),
        task_id=_bounded_string(task["id"], "task.id"),
        task_revision=task_revision,
        operation=operation,
        executor=executor,
        base_sha=_bounded_string(data["base_sha"], "base_sha"),
        terminal_kind=terminal_kind,
        executor_invoked=invoked,
        admitted_run_elapsed_seconds=_elapsed(
            durations["admitted_run_seconds"], "durations.admitted_run_seconds"
        ),
        executor_elapsed_seconds=executor_elapsed,
        verification_elapsed_seconds=_optional_elapsed(
            durations["verification_seconds"], "durations.verification_seconds"
        ),
        token_usage=usage,
    )


def observation_data(observation: RunObservation) -> dict[str, Any]:
    """Return the stable JSON representation of a validated observation."""

    usage = observation.token_usage
    data = {
        "kind": "RUN_OBSERVATION",
        "run_id": observation.run_id,
        "task": {
            "id": observation.task_id,
            "revision": observation.task_revision,
        },
        "operation": observation.operation,
        "executor": observation.executor,
        "base_sha": observation.base_sha,
        "terminal_kind": observation.terminal_kind,
        "executor_invoked": observation.executor_invoked,
        "durations": {
            "admitted_run_seconds": observation.admitted_run_elapsed_seconds,
            "executor_seconds": observation.executor_elapsed_seconds,
            "verification_seconds": observation.verification_elapsed_seconds,
        },
        "token_usage": (
            None
            if usage is None
            else {
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
            }
        ),
    }
    validate_observation(data)
    return data


def persist_observation(path: Path, observation: RunObservation) -> None:
    """Create one immutable sidecar, accepting only byte-identical repetition."""

    content = json.dumps(
        observation_data(observation), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise RunObservationError(
                f"conflicting finalized RUN_OBSERVATION for {observation.run_id}"
            )


class RunObservationTracker:
    """Subordinate phase timer whose failure can never alter execution truth."""

    def __init__(
        self,
        operation: str,
        *,
        monotonic_clock: MonotonicClock = time.monotonic,
    ) -> None:
        if operation not in OPERATIONS:
            raise RunObservationError("operation is not supported")
        self.operation = operation
        self._clock = monotonic_clock
        self._run: Run | None = None
        self._admitted_at: float | None = None
        self._executor_elapsed: float | None = None
        self._verification_elapsed: float | None = None
        self._executor_invoked = False
        self._token_usage: TokenUsage | None = None
        self._valid = True
        self._finalized = False
        self._last_clock: float | None = None

    @property
    def admitted(self) -> bool:
        return self._run is not None

    def admit(self, run: Run) -> None:
        """Start admitted-RUN timing after the canonical RUN is persisted."""

        if self._run is not None:
            self._valid = False
            return
        self._run = run
        self._admitted_at = self._read_clock()

    def wrap_native_runner(self, runner: Callable[P, T]) -> Callable[P, T]:
        """Time the already-authorized native call without changing its protocol."""

        def observed(*args: P.args, **kwargs: P.kwargs) -> T:
            self._executor_invoked = True
            started = self._read_clock()
            try:
                completed = runner(*args, **kwargs)
                self._capture_usage(completed)
                return completed
            finally:
                finished = self._read_clock()
                if started is not None and finished is not None:
                    elapsed = finished - started
                    if self._executor_elapsed is None:
                        self._executor_elapsed = elapsed
                    else:
                        self._executor_elapsed += elapsed
                    if not _is_elapsed(self._executor_elapsed):
                        self._valid = False

        return observed

    def begin_verification(self) -> float | None:
        """Mark the beginning of the one Runtime verification attempt."""

        return self._read_clock()

    def end_verification(self, started: float | None) -> None:
        """Record attempted verification elapsed time on success or failure."""

        finished = self._read_clock()
        if started is None or finished is None:
            return
        self._verification_elapsed = finished - started
        if not _is_elapsed(self._verification_elapsed):
            self._valid = False

    def finalize(self, terminal_kind: str) -> RunObservation | None:
        """Build the terminal observation, or omit it if trustworthy timing failed."""

        if self._finalized or terminal_kind not in TERMINAL_KINDS:
            return None
        self._finalized = True
        finished = self._read_clock()
        if (
            not self._valid
            or self._run is None
            or self._admitted_at is None
            or finished is None
        ):
            return None
        admitted_elapsed = finished - self._admitted_at
        if not _is_elapsed(admitted_elapsed):
            return None
        if self._executor_invoked and self._executor_elapsed is None:
            return None
        data = {
            "kind": "RUN_OBSERVATION",
            "run_id": self._run.run_id,
            "task": {
                "id": self._run.task.id,
                "revision": self._run.task.revision,
            },
            "operation": self.operation,
            "executor": self._run.executor,
            "base_sha": self._run.base_sha,
            "terminal_kind": terminal_kind,
            "executor_invoked": self._executor_invoked,
            "durations": {
                "admitted_run_seconds": admitted_elapsed,
                "executor_seconds": self._executor_elapsed,
                "verification_seconds": self._verification_elapsed,
            },
            "token_usage": (
                None
                if self._token_usage is None
                else {
                    "input_tokens": self._token_usage.input_tokens,
                    "cached_input_tokens": self._token_usage.cached_input_tokens,
                    "output_tokens": self._token_usage.output_tokens,
                }
            ),
        }
        try:
            return validate_observation(data)
        except RunObservationError:
            return None

    def _read_clock(self) -> float | None:
        if not self._valid:
            return None
        try:
            value = self._clock()
        except Exception:
            self._valid = False
            return None
        if not _is_elapsed(value):
            self._valid = False
            return None
        current = float(value)
        if self._last_clock is not None and current < self._last_clock:
            self._valid = False
            return None
        self._last_clock = current
        return current

    def _capture_usage(self, completed: Any) -> None:
        """Accept only an explicit machine-readable group on this invocation."""

        try:
            data = getattr(completed, "aios_token_usage", None)
            if data is None:
                return
            self._token_usage = validate_token_usage(data)
        except Exception:
            # Malformed or partial counters are unavailable, never inferred.
            self._token_usage = None


def _bounded_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise RunObservationError(f"{path} must be bounded non-empty text")
    return value


def _elapsed(value: Any, path: str) -> float:
    if not _is_elapsed(value):
        raise RunObservationError(f"{path} must be finite and non-negative")
    return float(value)


def _optional_elapsed(value: Any, path: str) -> float | None:
    return None if value is None else _elapsed(value, path)


def _is_elapsed(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )
