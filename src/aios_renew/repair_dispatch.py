"""Durable at-most-once delivery around canonical pre-PASS REPAIR."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .execution_profile import (
    PROFILE_IDENTITY_FIELDS,
    ResolvedExecutionProfile,
    execution_profile_identity,
    parse_execution_profile,
    validate_profile_identity,
)


REPAIR_DISPATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
FAILED_RUN_ID_PATTERN = re.compile(r"^RUN-[A-Za-z0-9_-]+-\d{3,}$")
REPAIR_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SUPPORTED_EXECUTORS = frozenset({"codex", "antigravity"})
REPAIR_ACTIONS = frozenset(
    {"CODE_FIX", "CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE", "NO_CHANGE"}
)
IN_PROGRESS_EXIT_CODE = 75
RECONCILIATION_BLOCKED_EXIT_CODE = 76
_TERMINAL = frozenset({"SUCCEEDED", "FAILED"})
_NONTERMINAL = frozenset({"STARTED", "IN_PROGRESS", "RECONCILIATION_BLOCKED"})
_V1_RECORD_KEYS = frozenset(
    {
        "version",
        "repair_dispatch_id",
        "failed_run_id",
        "repair_sha",
        "executor",
        "task_id",
        "action",
        "status",
        "run_id",
        "exit_code",
        "detail",
    }
)
_V2_RECORD_KEYS = _V1_RECORD_KEYS | frozenset(PROFILE_IDENTITY_FIELDS[1:])


def _profile_binding(
    executor: str | None, execution_profile: ResolvedExecutionProfile | None
) -> dict[str, str | None]:
    if executor is None:
        if execution_profile is not None:
            raise RepairDispatchError("NO_CHANGE REPAIR forbids an execution profile")
        return {field: None for field in PROFILE_IDENTITY_FIELDS[1:]}
    if execution_profile is None:
        raise RepairDispatchError("coding REPAIR requires a resolved execution profile")
    identity = execution_profile_identity(execution_profile)
    if identity["executor"] != executor:
        raise RepairDispatchError("execution profile executor does not match REPAIR")
    return {field: identity[field] for field in PROFILE_IDENTITY_FIELDS[1:]}


class RepairDispatchError(RuntimeError):
    """Raised when a dedicated REPAIR delivery cannot safely proceed."""


@dataclass(frozen=True)
class RepairInvocation:
    """Bounded outcome returned by the existing canonical ``run_repair`` path."""

    exit_code: int
    run_id: str | None = None


@dataclass(frozen=True)
class RepairDispatchOutcome:
    repair_dispatch_id: str
    failed_run_id: str
    repair_sha: str
    executor: str | None
    task_id: str
    action: str
    status: str
    run_id: str | None
    exit_code: int
    replayed: bool
    detail: str

    def render(self) -> str:
        return (
            f"AIOS REPAIR DISPATCH {self.status}\n"
            f"repair_dispatch: {self.repair_dispatch_id}\n"
            f"failed_run: {self.failed_run_id}\n"
            f"repair_sha: {self.repair_sha}\n"
            f"task: {self.task_id}\n"
            f"action: {self.action}\n"
            f"executor: {self.executor or 'none'}\n"
            f"run: {self.run_id or 'none'}\n"
            f"replayed: {str(self.replayed).lower()}\n"
            f"detail: {self.detail}"
        )


def reject_existing_selector_collision(
    *,
    state_root: Path,
    repair_dispatch_id: str,
    failed_run_id: str,
    repair_sha: str,
    executor: str | None,
    execution_profile: ResolvedExecutionProfile | None = None,
) -> None:
    """Reject changed carrier selectors before canonical REPAIR resolution."""

    _validate_selectors(repair_dispatch_id, failed_run_id, repair_sha, executor)
    record_path = _record_path(state_root, repair_dispatch_id)
    if not record_path.is_file():
        return
    with _StateLock(state_root / "repair-dispatch.lock"):
        record = _read_record(record_path)
        _require_same_selectors(
            record,
            repair_dispatch_id=repair_dispatch_id,
            failed_run_id=failed_run_id,
            repair_sha=repair_sha,
            executor=executor,
        )
        if record["version"] == 2 and any(
            record[field] != value
            for field, value in _profile_binding(executor, execution_profile).items()
        ):
            raise RepairDispatchError(
                "repair_dispatch_id collision: immutable profile binding differs"
            )


def replay_existing_repair_dispatch(
    *,
    state_root: Path,
    repair_dispatch_id: str,
    failed_run_id: str,
    repair_sha: str,
    executor: str | None,
    execution_profile: ResolvedExecutionProfile | None = None,
) -> RepairDispatchOutcome | None:
    """Return/reconcile an existing exact delivery without reacquiring authority."""

    _validate_selectors(repair_dispatch_id, failed_run_id, repair_sha, executor)
    record_path = _record_path(state_root, repair_dispatch_id)
    if not record_path.is_file():
        return None
    active_path = record_path.with_suffix(".active.lock")
    with _StateLock(state_root / "repair-dispatch.lock"):
        record = _read_record(record_path)
        _require_same_selectors(
            record,
            repair_dispatch_id=repair_dispatch_id,
            failed_run_id=failed_run_id,
            repair_sha=repair_sha,
            executor=executor,
        )
        if record["version"] == 2 and any(
            record[field] != value
            for field, value in _profile_binding(executor, execution_profile).items()
        ):
            raise RepairDispatchError(
                "repair_dispatch_id collision: immutable profile binding differs"
            )
        if record["status"] in _TERMINAL:
            return _outcome(record, replayed=True)
        if _lock_is_held(active_path):
            return _outcome(
                {
                    **record,
                    "status": "IN_PROGRESS",
                    "exit_code": IN_PROGRESS_EXIT_CODE,
                    "detail": "original REPAIR invocation is still active",
                },
                replayed=True,
            )
        reconciled = _reconcile(state_root, record)
        _write_record(record_path, reconciled)
        return _outcome(reconciled, replayed=True)


def execute_repair_dispatch(
    *,
    state_root: Path,
    repair_dispatch_id: str,
    failed_run_id: str,
    repair_sha: str,
    executor: str | None,
    task_id: str,
    action: str,
    invoke_repair: Callable[[], RepairInvocation],
    execution_profile: ResolvedExecutionProfile | None = None,
) -> RepairDispatchOutcome:
    """Invoke ``run_repair`` at most once, or reconcile its exact bound RUN."""

    _validate_binding(
        repair_dispatch_id, failed_run_id, repair_sha, executor, task_id, action
    )
    profile = (
        _profile_binding(executor, execution_profile)
        if execution_profile is not None or executor is None
        else None
    )
    record_path = _record_path(state_root, repair_dispatch_id)
    active_path = record_path.with_suffix(".active.lock")
    invocation_guard: _StateLock | None = None

    with _StateLock(state_root / "repair-dispatch.lock"):
        if record_path.exists():
            record = _read_record(record_path)
            _require_same_binding(
                record,
                repair_dispatch_id=repair_dispatch_id,
                failed_run_id=failed_run_id,
                repair_sha=repair_sha,
                executor=executor,
                task_id=task_id,
                action=action,
                **(profile or {}),
            )
            if record["status"] in _TERMINAL:
                return _outcome(record, replayed=True)
            if _lock_is_held(active_path):
                return _outcome(
                    {
                        **record,
                        "status": "IN_PROGRESS",
                        "exit_code": IN_PROGRESS_EXIT_CODE,
                        "detail": "original REPAIR invocation is still active",
                    },
                    replayed=True,
                )
            reconciled = _reconcile(state_root, record)
            _write_record(record_path, reconciled)
            return _outcome(reconciled, replayed=True)

        if profile is None:
            raise RepairDispatchError("coding REPAIR requires a resolved execution profile")
        record = {
            "version": 2,
            "repair_dispatch_id": repair_dispatch_id,
            "failed_run_id": failed_run_id,
            "repair_sha": repair_sha,
            "executor": executor,
            "task_id": task_id,
            "action": action,
            **profile,
            "status": "STARTED",
            "run_id": None,
            "exit_code": None,
            "detail": "canonical REPAIR invocation durably admitted",
        }
        _write_record(record_path, record)
        invocation_guard = _StateLock(active_path)
        invocation_guard.__enter__()

    try:
        invocation = invoke_repair()
        with _StateLock(state_root / "repair-dispatch.lock"):
            current = _read_record(record_path)
            finalized = _finalize_invocation(state_root, current, invocation)
            _write_record(record_path, finalized)
            return _outcome(finalized, replayed=False)
    finally:
        if invocation_guard is not None:
            invocation_guard.__exit__()


def bind_repair_run(
    *,
    state_root: Path,
    repair_dispatch_id: str,
    run_id: str,
    execution_profile: ResolvedExecutionProfile | None = None,
) -> None:
    """Bind the exact continuation RUN before any coding Executor can run."""

    if not isinstance(repair_dispatch_id, str) or not REPAIR_DISPATCH_ID_PATTERN.fullmatch(
        repair_dispatch_id
    ):
        raise RepairDispatchError("invalid repair_dispatch_id format")
    if not isinstance(run_id, str) or not FAILED_RUN_ID_PATTERN.fullmatch(run_id):
        raise RepairDispatchError("invalid admitted REPAIR RUN id")
    record_path = _record_path(state_root, repair_dispatch_id)
    with _StateLock(state_root / "repair-dispatch.lock"):
        if not record_path.is_file():
            raise RepairDispatchError("repair dispatch record does not exist")
        record = _read_record(record_path)
        if record["repair_dispatch_id"] != repair_dispatch_id:
            raise RepairDispatchError("repair dispatch journal hash collision")
        if record["status"] != "STARTED" or record["run_id"] is not None:
            raise RepairDispatchError("repair dispatch is not awaiting a RUN")
        if not _run_matches_record(state_root, run_id, record):
            raise RepairDispatchError("admitted REPAIR RUN does not match dispatch")
        if record["version"] == 2 and any(
            record[field] != value
            for field, value in _profile_binding(
                record["executor"], execution_profile
            ).items()
        ):
            raise RepairDispatchError(
                "admitted REPAIR profile does not match durable dispatch"
            )
        _write_record(
            record_path,
            {
                **record,
                "run_id": run_id,
                "detail": "REPAIR RUN ownership recorded at canonical admission",
            },
        )


def _validate_selectors(
    repair_dispatch_id: str,
    failed_run_id: str,
    repair_sha: str,
    executor: str | None,
) -> None:
    if not isinstance(repair_dispatch_id, str) or not REPAIR_DISPATCH_ID_PATTERN.fullmatch(
        repair_dispatch_id
    ):
        raise RepairDispatchError("invalid repair_dispatch_id format")
    if not isinstance(failed_run_id, str) or not FAILED_RUN_ID_PATTERN.fullmatch(
        failed_run_id
    ):
        raise RepairDispatchError("invalid failed_run_id format")
    if not isinstance(repair_sha, str) or not REPAIR_SHA_PATTERN.fullmatch(repair_sha):
        raise RepairDispatchError("invalid repair_sha format")
    if executor is not None and executor not in SUPPORTED_EXECUTORS:
        raise RepairDispatchError(f"unsupported executor: {executor!r}")


def _validate_binding(
    repair_dispatch_id: str,
    failed_run_id: str,
    repair_sha: str,
    executor: str | None,
    task_id: str,
    action: str,
) -> None:
    _validate_selectors(repair_dispatch_id, failed_run_id, repair_sha, executor)
    if not isinstance(task_id, str) or not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task_id):
        raise RepairDispatchError("invalid canonical TASK identity")
    if action not in REPAIR_ACTIONS:
        raise RepairDispatchError("invalid canonical REPAIR action")
    if action in {
        "CODE_FIX", "CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE"
    } and executor is None:
        raise RepairDispatchError("coding REPAIR requires an explicit Executor")
    if action == "NO_CHANGE" and executor is not None:
        raise RepairDispatchError("NO_CHANGE REPAIR forbids a coding Executor")


def _require_same_selectors(record: Mapping[str, Any], **expected: Any) -> None:
    if any(record.get(key) != value for key, value in expected.items()):
        raise RepairDispatchError(
            "repair_dispatch_id collision: immutable request binding differs"
        )


def _require_same_binding(record: Mapping[str, Any], **expected: Any) -> None:
    _require_same_selectors(record, **expected)
    if record["version"] == 2 and any(
        field not in expected or record[field] != expected[field]
        for field in PROFILE_IDENTITY_FIELDS[1:]
    ):
        raise RepairDispatchError(
            "repair_dispatch_id collision: immutable profile binding differs"
        )


def _record_path(state_root: Path, repair_dispatch_id: str) -> Path:
    key = hashlib.sha256(repair_dispatch_id.encode("ascii")).hexdigest()
    return state_root / "repair-dispatches" / f"{key}.json"


def _read_record(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepairDispatchError("invalid repair dispatch record") from exc
    version = data.get("version") if isinstance(data, dict) else None
    expected_keys = _V1_RECORD_KEYS if version == 1 else _V2_RECORD_KEYS
    if not isinstance(data, dict) or set(data) != expected_keys:
        raise RepairDispatchError("invalid repair dispatch record shape")
    if version not in (1, 2) or data.get("status") not in _TERMINAL | _NONTERMINAL:
        raise RepairDispatchError("invalid repair dispatch record state")
    try:
        _validate_binding(
            data["repair_dispatch_id"],
            data["failed_run_id"],
            data["repair_sha"],
            data["executor"],
            data["task_id"],
            data["action"],
        )
    except (KeyError, RepairDispatchError) as exc:
        raise RepairDispatchError("invalid repair dispatch binding") from exc
    if version == 2:
        if data["executor"] is None:
            if any(data[field] is not None for field in PROFILE_IDENTITY_FIELDS[1:]):
                raise RepairDispatchError("NO_CHANGE dispatch has a profile binding")
        else:
            try:
                validate_profile_identity(
                    executor=data["executor"],
                    model=data["model"],
                    reasoning_effort=data["reasoning_effort"],
                    model_source=data["model_source"],
                    effort_source=data["effort_source"],
                )
            except Exception as exc:
                raise RepairDispatchError("invalid repair profile binding") from exc
    run_id = data.get("run_id")
    if run_id is not None and (
        not isinstance(run_id, str) or not FAILED_RUN_ID_PATTERN.fullmatch(run_id)
    ):
        raise RepairDispatchError("invalid repair RUN attribution")
    exit_code = data.get("exit_code")
    if exit_code is not None and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code < 0
    ):
        raise RepairDispatchError("invalid repair dispatch exit code")
    if data["status"] in _TERMINAL and exit_code is None:
        raise RepairDispatchError("terminal repair dispatch has no outcome")
    if data["status"] == "SUCCEEDED" and (exit_code != 0 or run_id is None):
        raise RepairDispatchError("successful repair dispatch is invalid")
    if data["status"] == "FAILED" and (exit_code is None or exit_code == 0):
        raise RepairDispatchError("failed repair dispatch is invalid")
    if not isinstance(data.get("detail"), str):
        raise RepairDispatchError("invalid repair dispatch detail")
    return data


def _write_record(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise RepairDispatchError("cannot persist repair dispatch") from exc


def _run_matches_record(
    state_root: Path, run_id: str, record: Mapping[str, Any]
) -> bool:
    try:
        run = json.loads((state_root / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
        execution = json.loads(
            (state_root / "repairs" / f"{run_id}.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    task = run.get("task") if isinstance(run, Mapping) else None
    authorization = execution.get("repair") if isinstance(execution, Mapping) else None
    if not isinstance(task, Mapping) or not isinstance(authorization, Mapping):
        return False
    expected_executor = record["executor"]
    matches = (
        run.get("run_id") == run_id
        and task.get("id") == record["task_id"]
        and execution.get("failed_run_id") == record["failed_run_id"]
        and execution.get("repair_authorization_sha") == record["repair_sha"]
        and authorization.get("failed_run_id") == record["failed_run_id"]
        and authorization.get("action") == record["action"]
        and (expected_executor is None or run.get("executor") == expected_executor)
    )
    if not matches or record["version"] == 1 or expected_executor is None:
        return matches
    try:
        profile = parse_execution_profile(
            (state_root / "execution-profiles" / f"{run_id}.json").read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return False
    identity = execution_profile_identity(profile)
    return profile.run_id == run_id and all(
        identity[field] == record[field] for field in PROFILE_IDENTITY_FIELDS
    )


def _reconcile(state_root: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    run_id = record["run_id"]
    if run_id is None:
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "repair dispatch has no durably bound REPAIR RUN",
        }
    if not _run_matches_record(state_root, run_id, record):
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "bound REPAIR RUN attribution is invalid",
        }
    return _terminal_observation(state_root, record, run_id)


def _terminal_observation(
    state_root: Path, record: Mapping[str, Any], run_id: str
) -> dict[str, Any]:
    result = (state_root / "results" / f"{run_id}.json").is_file()
    failure = (state_root / "failures" / f"{run_id}.json").is_file()
    if result and not failure:
        return {**record, "status": "SUCCEEDED", "exit_code": 0, "detail": "attributed canonical REPAIR RESULT"}
    if failure and not result:
        return {**record, "status": "FAILED", "exit_code": 1, "detail": "attributed canonical REPAIR FAILURE"}
    if _lock_is_held(state_root / "operator.lock") and not result and not failure:
        return {
            **record,
            "status": "IN_PROGRESS",
            "exit_code": IN_PROGRESS_EXIT_CODE,
            "detail": "attributable REPAIR RUN remains active",
        }
    detail = (
        "attributable REPAIR RUN has conflicting terminal artifacts"
        if result and failure
        else "attributable REPAIR RUN is incomplete"
    )
    return {
        **record,
        "status": "RECONCILIATION_BLOCKED",
        "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
        "detail": detail,
    }


def _finalize_invocation(
    state_root: Path, record: Mapping[str, Any], invocation: RepairInvocation
) -> dict[str, Any]:
    if (
        isinstance(invocation.exit_code, bool)
        or not isinstance(invocation.exit_code, int)
        or invocation.exit_code < 0
    ):
        raise RepairDispatchError("REPAIR invocation returned an invalid exit code")
    run_id = record["run_id"]
    if invocation.run_id is not None and invocation.run_id != run_id:
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "REPAIR returned RUN attribution without matching admission ownership",
        }
    if invocation.exit_code != 0:
        return {
            **record,
            "status": "FAILED",
            "exit_code": invocation.exit_code,
            "detail": "REPAIR invocation returned a nonzero outcome",
        }
    if run_id is None:
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "successful REPAIR outcome has no attributable RUN",
        }
    if not _run_matches_record(state_root, run_id, record):
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "successful REPAIR outcome has invalid RUN ownership",
        }
    if not (state_root / "results" / f"{run_id}.json").is_file():
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "successful REPAIR outcome has no canonical RESULT",
        }
    return {
        **record,
        "status": "SUCCEEDED",
        "exit_code": 0,
        "detail": "REPAIR invocation completed with canonical RESULT",
    }


def _outcome(record: Mapping[str, Any], *, replayed: bool) -> RepairDispatchOutcome:
    exit_code = record["exit_code"]
    if exit_code is None:
        exit_code = RECONCILIATION_BLOCKED_EXIT_CODE
    return RepairDispatchOutcome(
        repair_dispatch_id=record["repair_dispatch_id"],
        failed_run_id=record["failed_run_id"],
        repair_sha=record["repair_sha"],
        executor=record["executor"],
        task_id=record["task_id"],
        action=record["action"],
        status=record["status"],
        run_id=record["run_id"],
        exit_code=exit_code,
        replayed=replayed,
        detail=record["detail"],
    )


class _StateLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def __enter__(self) -> _StateLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            _acquire_lock(stream, blocking=True)
        except OSError as exc:
            stream.close()
            raise RepairDispatchError("another repair dispatch decision is active") from exc
        self._file = stream
        return self

    def __exit__(self, *args: Any) -> None:
        if self._file is not None:
            stream = self._file
            self._file = None
            try:
                _release_lock(stream)
            finally:
                stream.close()


def _lock_is_held(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        stream = path.open("r+b")
    except OSError:
        return True
    try:
        try:
            _acquire_lock(stream)
        except OSError:
            return True
        _release_lock(stream)
        return False
    finally:
        stream.close()


def _acquire_lock(stream: BinaryIO, *, blocking: bool = False) -> None:
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(stream.fileno(), mode, 1)
    else:
        import fcntl

        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(stream.fileno(), flags)


def _release_lock(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
