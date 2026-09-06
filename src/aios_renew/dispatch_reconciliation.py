"""Durable outer dispatch identity and observational PRIMARY reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


DISPATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
TASK_ID_PATTERN = re.compile(r"^TASK-[A-Za-z0-9_-]+$")
SUPPORTED_EXECUTORS = frozenset({"codex", "antigravity"})
IN_PROGRESS_EXIT_CODE = 75
RECONCILIATION_BLOCKED_EXIT_CODE = 76
_TERMINAL = frozenset({"SUCCEEDED", "FAILED"})
_NONTERMINAL = frozenset(
    {"STARTED", "IN_PROGRESS", "RECONCILIATION_BLOCKED"}
)
_RECORD_KEYS = frozenset(
    {
        "version",
        "dispatch_id",
        "task_id",
        "executor",
        "status",
        "pre_run_ids",
        "run_id",
        "exit_code",
        "detail",
    }
)


class DispatchError(RuntimeError):
    """Raised when an outer dispatch cannot safely proceed."""


@dataclass(frozen=True)
class DispatchInvocation:
    """Non-semantic outcome returned by the existing PRIMARY CLI path."""

    exit_code: int
    run_id: str | None = None


@dataclass(frozen=True)
class DispatchOutcome:
    """Operational dispatch outcome; never canonical engineering truth."""

    dispatch_id: str
    task_id: str
    executor: str
    status: str
    run_id: str | None
    exit_code: int
    replayed: bool
    detail: str

    def render(self) -> str:
        attribution = self.run_id if self.run_id is not None else "none"
        return (
            f"AIOS DISPATCH {self.status}\n"
            f"dispatch: {self.dispatch_id}\n"
            f"task: {self.task_id}\n"
            f"executor: {self.executor}\n"
            f"run: {attribution}\n"
            f"replayed: {str(self.replayed).lower()}\n"
            f"detail: {self.detail}"
        )


def execute_dispatch(
    *,
    state_root: Path,
    dispatch_id: str,
    task_id: str,
    executor: str,
    invoke_primary: Callable[[], DispatchInvocation],
) -> DispatchOutcome:
    """Invoke PRIMARY once for a new dispatch or reconcile an existing one."""

    _validate_request(dispatch_id, task_id, executor)
    dispatches = state_root / "dispatches"
    dispatch_key = _dispatch_key(dispatch_id)
    record_path = dispatches / f"{dispatch_key}.json"
    active_path = dispatches / f"{dispatch_key}.active.lock"
    lock_path = state_root / "dispatch.lock"
    invocation_guard: _DispatchLock | None = None

    with _DispatchLock(lock_path):
        if record_path.exists():
            record = _read_record(record_path)
            _require_same_request(record, dispatch_id, task_id, executor)
            if record["status"] in _TERMINAL:
                return _outcome(record, replayed=True)
            if _lock_is_held(active_path):
                active = {
                    **record,
                    "status": "IN_PROGRESS",
                    "exit_code": IN_PROGRESS_EXIT_CODE,
                    "detail": "original dispatch invocation is still active",
                }
                _write_record(record_path, active)
                return _outcome(active, replayed=True)
            reconciled = _reconcile(state_root, record)
            _write_record(record_path, reconciled)
            return _outcome(reconciled, replayed=True)

        pre_run_ids = _primary_run_ids(state_root / "runs", task_id)
        record: dict[str, Any] = {
            "version": 1,
            "dispatch_id": dispatch_id,
            "task_id": task_id,
            "executor": executor,
            "status": "STARTED",
            "pre_run_ids": list(pre_run_ids),
            "run_id": None,
            "exit_code": None,
            "detail": "PRIMARY invocation durably authorized but not yet reconciled",
        }
        _write_record(record_path, record)
        invocation_guard = _DispatchLock(active_path)
        invocation_guard.__enter__()

    try:
        invocation = invoke_primary()

        with _DispatchLock(lock_path):
            current = _read_record(record_path)
            _require_same_request(current, dispatch_id, task_id, executor)
            finalized = _finalize_invocation(state_root, current, invocation)
            _write_record(record_path, finalized)
            return _outcome(finalized, replayed=False)
    finally:
        if invocation_guard is not None:
            invocation_guard.__exit__()


def _validate_request(dispatch_id: str, task_id: str, executor: str) -> None:
    if not isinstance(dispatch_id, str) or not DISPATCH_ID_PATTERN.fullmatch(
        dispatch_id
    ):
        raise DispatchError(
            "dispatch_id must be 1-128 ASCII letters, digits, '_' or '-', "
            "starting with a letter or digit"
        )
    if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
        raise DispatchError(f"invalid task_id format: {task_id!r}")
    if executor not in SUPPORTED_EXECUTORS:
        raise DispatchError(f"unsupported executor: {executor!r}")


def _dispatch_key(dispatch_id: str) -> str:
    return hashlib.sha256(dispatch_id.encode("ascii")).hexdigest()


def _require_same_request(
    record: Mapping[str, Any], dispatch_id: str, task_id: str, executor: str
) -> None:
    if record["dispatch_id"] != dispatch_id:
        raise DispatchError("dispatch journal hash collision")
    if record["task_id"] != task_id or record["executor"] != executor:
        raise DispatchError(
            "dispatch_id collision: existing request binding does not match"
        )


def _read_record(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DispatchError(f"invalid dispatch record: {exc}") from exc
    if not isinstance(data, dict) or set(data) != _RECORD_KEYS:
        raise DispatchError("invalid dispatch record shape")
    if data.get("version") != 1:
        raise DispatchError("unsupported dispatch record version")
    try:
        _validate_request(data["dispatch_id"], data["task_id"], data["executor"])
    except (KeyError, DispatchError) as exc:
        raise DispatchError(f"invalid dispatch record binding: {exc}") from exc
    status = data.get("status")
    if status not in _TERMINAL | _NONTERMINAL:
        raise DispatchError("invalid dispatch record status")
    pre_run_ids = data.get("pre_run_ids")
    if (
        not isinstance(pre_run_ids, list)
        or any(not isinstance(item, str) for item in pre_run_ids)
        or pre_run_ids != sorted(set(pre_run_ids))
    ):
        raise DispatchError("invalid dispatch pre-invocation RUN namespace")
    run_id = data.get("run_id")
    if run_id is not None and not isinstance(run_id, str):
        raise DispatchError("invalid dispatch RUN attribution")
    exit_code = data.get("exit_code")
    if exit_code is not None and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        raise DispatchError("invalid dispatch exit code")
    if not isinstance(data.get("detail"), str):
        raise DispatchError("invalid dispatch detail")
    if status in _TERMINAL and exit_code is None:
        raise DispatchError("terminal dispatch record has no exit code")
    if status == "SUCCEEDED" and (exit_code != 0 or run_id is None):
        raise DispatchError("successful dispatch record has invalid attribution")
    if status == "FAILED" and (exit_code is None or exit_code == 0):
        raise DispatchError("failed dispatch record has invalid exit code")
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
        raise DispatchError(f"cannot persist dispatch record: {exc}") from exc


def _primary_run_ids(runs_path: Path, task_id: str) -> tuple[str, ...]:
    run_ids: list[str] = []
    if not runs_path.is_dir():
        return ()
    for path in sorted(runs_path.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            task = data.get("task") if isinstance(data, Mapping) else None
            run_id = data.get("run_id") if isinstance(data, Mapping) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(task, Mapping)
            and task.get("id") == task_id
            and isinstance(run_id, str)
            and path.stem == run_id
            and "kind" not in data
        ):
            run_ids.append(run_id)
    return tuple(sorted(set(run_ids)))


def _matching_new_runs(state_root: Path, record: Mapping[str, Any]) -> tuple[str, ...]:
    before = set(record["pre_run_ids"])
    current = _primary_run_ids(state_root / "runs", record["task_id"])
    candidates: list[str] = []
    for run_id in current:
        if run_id in before:
            continue
        run_path = state_root / "runs" / f"{run_id}.json"
        try:
            data = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(data, Mapping) and data.get("executor") == record["executor"]:
            candidates.append(run_id)
    return tuple(candidates)


def _reconcile(state_root: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    updated = dict(record)
    bound_run = record["run_id"]
    if bound_run is None:
        candidates = _matching_new_runs(state_root, record)
        if len(candidates) != 1:
            updated.update(
                status="RECONCILIATION_BLOCKED",
                exit_code=RECONCILIATION_BLOCKED_EXIT_CODE,
                detail=(
                    "cannot prove exactly one attributable PRIMARY RUN; "
                    "no execution was attempted"
                ),
            )
            return updated
        bound_run = candidates[0]
        updated["run_id"] = bound_run

    result_exists = (state_root / "results" / f"{bound_run}.json").is_file()
    failure_exists = (state_root / "failures" / f"{bound_run}.json").is_file()
    if result_exists and failure_exists:
        updated.update(
            status="RECONCILIATION_BLOCKED",
            exit_code=RECONCILIATION_BLOCKED_EXIT_CODE,
            detail="attributable RUN has conflicting terminal artifacts",
        )
    elif result_exists:
        updated.update(
            status="SUCCEEDED",
            exit_code=0,
            detail="reconciled to existing canonical RESULT",
        )
    elif failure_exists:
        updated.update(
            status="FAILED",
            exit_code=1,
            detail="reconciled to existing canonical FAILURE",
        )
    elif _operator_lock_is_held(state_root / "operator.lock"):
        updated.update(
            status="IN_PROGRESS",
            exit_code=IN_PROGRESS_EXIT_CODE,
            detail="attributable RUN is incomplete while Operator lock is held",
        )
    else:
        updated.update(
            status="RECONCILIATION_BLOCKED",
            exit_code=RECONCILIATION_BLOCKED_EXIT_CODE,
            detail="attributable RUN is incomplete; explicit recovery is required",
        )
    return updated


def _finalize_invocation(
    state_root: Path,
    record: Mapping[str, Any],
    invocation: DispatchInvocation,
) -> dict[str, Any]:
    if (
        isinstance(invocation.exit_code, bool)
        or not isinstance(invocation.exit_code, int)
        or invocation.exit_code < 0
    ):
        raise DispatchError("PRIMARY invocation returned an invalid exit code")

    candidates = _matching_new_runs(state_root, record)
    run_id = invocation.run_id
    if run_id is not None and run_id not in candidates:
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "run_id": None,
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "PRIMARY returned RUN attribution outside the recorded namespace",
        }
    if run_id is None and len(candidates) == 1:
        run_id = candidates[0]
    if run_id is None and len(candidates) > 1:
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "PRIMARY outcome has ambiguous RUN attribution",
        }

    if invocation.exit_code != 0:
        return {
            **record,
            "status": "FAILED",
            "run_id": run_id,
            "exit_code": invocation.exit_code,
            "detail": "PRIMARY invocation returned a nonzero outcome",
        }
    if run_id is None:
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "successful PRIMARY outcome has no attributable RUN",
        }
    if not (state_root / "results" / f"{run_id}.json").is_file():
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "run_id": run_id,
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": "successful PRIMARY outcome has no canonical RESULT",
        }
    return {
        **record,
        "status": "SUCCEEDED",
        "run_id": run_id,
        "exit_code": 0,
        "detail": "PRIMARY invocation completed with canonical RESULT",
    }


def _outcome(record: Mapping[str, Any], *, replayed: bool) -> DispatchOutcome:
    exit_code = record["exit_code"]
    if exit_code is None:
        exit_code = RECONCILIATION_BLOCKED_EXIT_CODE
    return DispatchOutcome(
        dispatch_id=record["dispatch_id"],
        task_id=record["task_id"],
        executor=record["executor"],
        status=record["status"],
        run_id=record["run_id"],
        exit_code=exit_code,
        replayed=replayed,
        detail=record["detail"],
    )


class _DispatchLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def __enter__(self) -> _DispatchLock:
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
            raise DispatchError("another dispatch decision is active") from exc
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


def _operator_lock_is_held(path: Path) -> bool:
    return _lock_is_held(path)


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
