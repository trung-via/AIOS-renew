"""Durable at-most-once delivery around approved canonical REMEDIATION."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .remote_surface import ApprovedRemediation


CORRECTION_DISPATCH_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
)
SUPPORTED_EXECUTORS = frozenset({"codex", "antigravity"})
IN_PROGRESS_EXIT_CODE = 75
RECONCILIATION_BLOCKED_EXIT_CODE = 76
_TERMINAL = frozenset({"SUCCEEDED", "FAILED"})
_NONTERMINAL = frozenset({"STARTED", "IN_PROGRESS", "RECONCILIATION_BLOCKED"})
_RUN_ID_PATTERN = re.compile(r"^RUN-[A-Za-z0-9_-]+-\d{3,}$")
_TASK_ID_PATTERN = re.compile(r"^TASK-[A-Za-z0-9_-]+$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_RECORD_KEYS = frozenset(
    {
        "version",
        "correction_dispatch_id",
        "source_run_id",
        "finding_id",
        "executor",
        "task_id",
        "task_revision",
        "review_id",
        "action",
        "reviewed_sha",
        "remediation_ref",
        "remediation_sha",
        "approver",
        "status",
        "pre_run_ids",
        "run_id",
        "exit_code",
        "detail",
    }
)


class CorrectionDispatchError(RuntimeError):
    """Raised when an approved correction delivery cannot safely proceed."""


@dataclass(frozen=True)
class CorrectionInvocation:
    """Bounded outcome from the existing canonical REMEDIATION path."""

    exit_code: int
    run_id: str | None = None


@dataclass(frozen=True)
class CorrectionDispatchOutcome:
    correction_dispatch_id: str
    source_run_id: str
    finding_id: str
    executor: str
    task_id: str
    remediation_sha: str
    status: str
    run_id: str | None
    exit_code: int
    replayed: bool
    detail: str

    def render(self) -> str:
        return (
            f"AIOS CORRECTION DISPATCH {self.status}\n"
            f"correction_dispatch: {self.correction_dispatch_id}\n"
            f"source_run: {self.source_run_id}\n"
            f"finding: {self.finding_id}\n"
            f"task: {self.task_id}\n"
            f"executor: {self.executor}\n"
            f"remediation_sha: {self.remediation_sha}\n"
            f"run: {self.run_id or 'none'}\n"
            f"replayed: {str(self.replayed).lower()}\n"
            f"detail: {self.detail}"
        )


def reject_existing_selector_collision(
    *,
    state_root: Path,
    correction_dispatch_id: str,
    source_run_id: str,
    finding_id: str,
    executor: str,
) -> None:
    """Fail a reused id with changed remote selectors before approval lookup."""

    _validate_selectors(
        correction_dispatch_id, source_run_id, finding_id, executor
    )
    key = hashlib.sha256(correction_dispatch_id.encode("ascii")).hexdigest()
    record_path = state_root / "correction-dispatches" / f"{key}.json"
    if not record_path.is_file():
        return
    with _StateLock(state_root / "correction-dispatch.lock"):
        record = _read_record(record_path)
        expected = {
            "correction_dispatch_id": correction_dispatch_id,
            "source_run_id": source_run_id,
            "finding_id": finding_id,
            "executor": executor,
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise CorrectionDispatchError(
                "correction_dispatch_id collision: immutable request binding differs"
            )


def execute_correction_dispatch(
    *,
    state_root: Path,
    correction_dispatch_id: str,
    source_run_id: str,
    finding_id: str,
    executor: str,
    approval: ApprovedRemediation,
    invoke_remediation: Callable[[], CorrectionInvocation],
) -> CorrectionDispatchOutcome:
    """Invoke canonical REMEDIATION once or reconcile without re-execution."""

    _validate_request(
        correction_dispatch_id, source_run_id, finding_id, executor, approval
    )
    records = state_root / "correction-dispatches"
    key = hashlib.sha256(correction_dispatch_id.encode("ascii")).hexdigest()
    record_path = records / f"{key}.json"
    active_path = records / f"{key}.active.lock"
    invocation_guard: _StateLock | None = None

    with _StateLock(state_root / "correction-dispatch.lock"):
        if record_path.exists():
            record = _read_record(record_path)
            _require_same_binding(
                record,
                correction_dispatch_id=correction_dispatch_id,
                source_run_id=source_run_id,
                finding_id=finding_id,
                executor=executor,
                approval=approval,
            )
            if record["status"] in _TERMINAL:
                return _outcome(record, replayed=True)
            if _lock_is_held(active_path):
                return _outcome(
                    {
                        **record,
                        "status": "IN_PROGRESS",
                        "exit_code": IN_PROGRESS_EXIT_CODE,
                        "detail": "original correction invocation is still active",
                    },
                    replayed=True,
                )
            reconciled = _reconcile(state_root, record)
            _write_record(record_path, reconciled)
            return _outcome(reconciled, replayed=True)

        record = {
            "version": 1,
            "correction_dispatch_id": correction_dispatch_id,
            "source_run_id": source_run_id,
            "finding_id": finding_id,
            "executor": executor,
            **asdict(approval),
            "status": "STARTED",
            "pre_run_ids": list(_remediation_run_ids(state_root / "runs")),
            "run_id": None,
            "exit_code": None,
            "detail": "approved REMEDIATION invocation durably authorized",
        }
        # Request selectors are deliberately authoritative over duplicate fields.
        record["source_run_id"] = source_run_id
        record["finding_id"] = finding_id
        _write_record(record_path, record)
        invocation_guard = _StateLock(active_path)
        invocation_guard.__enter__()

    try:
        invocation = invoke_remediation()
        with _StateLock(state_root / "correction-dispatch.lock"):
            current = _read_record(record_path)
            finalized = _finalize_invocation(state_root, current, invocation)
            _write_record(record_path, finalized)
            return _outcome(finalized, replayed=False)
    finally:
        if invocation_guard is not None:
            invocation_guard.__exit__()


def bind_correction_run(
    *, state_root: Path, correction_dispatch_id: str, run_id: str
) -> None:
    """Bind the canonical REMEDIATION RUN at its existing admission boundary."""

    if (
        not isinstance(correction_dispatch_id, str)
        or not CORRECTION_DISPATCH_ID_PATTERN.fullmatch(correction_dispatch_id)
    ):
        raise CorrectionDispatchError("invalid correction_dispatch_id format")
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
        raise CorrectionDispatchError("invalid admitted REMEDIATION RUN id")
    key = hashlib.sha256(correction_dispatch_id.encode("ascii")).hexdigest()
    record_path = state_root / "correction-dispatches" / f"{key}.json"
    with _StateLock(state_root / "correction-dispatch.lock"):
        if not record_path.is_file():
            raise CorrectionDispatchError("correction dispatch record does not exist")
        record = _read_record(record_path)
        if record["correction_dispatch_id"] != correction_dispatch_id:
            raise CorrectionDispatchError("correction dispatch journal hash collision")
        if record["status"] != "STARTED" or record["run_id"] is not None:
            raise CorrectionDispatchError("correction dispatch is not awaiting a RUN")
        if run_id in record["pre_run_ids"] or not _run_matches_record(
            state_root / "runs" / f"{run_id}.json", record
        ):
            raise CorrectionDispatchError(
                "admitted REMEDIATION RUN does not match correction dispatch"
            )
        _write_record(
            record_path,
            {
                **record,
                "run_id": run_id,
                "detail": "REMEDIATION RUN ownership recorded at admission",
            },
        )


def _validate_request(
    correction_dispatch_id: str,
    source_run_id: str,
    finding_id: str,
    executor: str,
    approval: ApprovedRemediation,
) -> None:
    _validate_selectors(
        correction_dispatch_id, source_run_id, finding_id, executor
    )
    if approval.source_run_id != source_run_id or approval.finding_id != finding_id:
        raise CorrectionDispatchError("approval does not match correction request")
    expected_ref = f"refs/heads/aios/remediation/{source_run_id}-{finding_id}"
    if (
        not isinstance(approval.task_id, str)
        or not _TASK_ID_PATTERN.fullmatch(approval.task_id)
        or isinstance(approval.task_revision, bool)
        or not isinstance(approval.task_revision, int)
        or approval.task_revision < 1
        or not isinstance(approval.review_id, str)
        or not approval.review_id
        or approval.action not in {"CODE_FIX", "EVIDENCE_ONLY"}
        or not isinstance(approval.reviewed_sha, str)
        or not _SHA_PATTERN.fullmatch(approval.reviewed_sha)
        or approval.remediation_ref != expected_ref
        or not isinstance(approval.remediation_sha, str)
        or not _SHA_PATTERN.fullmatch(approval.remediation_sha)
        or not isinstance(approval.approver, str)
        or not approval.approver
    ):
        raise CorrectionDispatchError("approved remediation binding is invalid")


def _validate_selectors(
    correction_dispatch_id: str,
    source_run_id: str,
    finding_id: str,
    executor: str,
) -> None:
    if (
        not isinstance(correction_dispatch_id, str)
        or not CORRECTION_DISPATCH_ID_PATTERN.fullmatch(correction_dispatch_id)
    ):
        raise CorrectionDispatchError("invalid correction_dispatch_id format")
    if (
        not isinstance(source_run_id, str)
        or not _RUN_ID_PATTERN.fullmatch(source_run_id)
    ):
        raise CorrectionDispatchError("invalid source_run_id format")
    if (
        not isinstance(finding_id, str)
        or not CORRECTION_DISPATCH_ID_PATTERN.fullmatch(finding_id)
    ):
        raise CorrectionDispatchError("invalid finding_id format")
    if executor not in SUPPORTED_EXECUTORS:
        raise CorrectionDispatchError(f"unsupported executor: {executor!r}")


def _require_same_binding(
    record: Mapping[str, Any],
    *,
    correction_dispatch_id: str,
    source_run_id: str,
    finding_id: str,
    executor: str,
    approval: ApprovedRemediation,
) -> None:
    expected = {
        "correction_dispatch_id": correction_dispatch_id,
        "source_run_id": source_run_id,
        "finding_id": finding_id,
        "executor": executor,
        **asdict(approval),
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise CorrectionDispatchError(
            "correction_dispatch_id collision: immutable request binding differs"
        )


def _read_record(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorrectionDispatchError("invalid correction dispatch record") from exc
    if not isinstance(data, dict) or set(data) != _RECORD_KEYS:
        raise CorrectionDispatchError("invalid correction dispatch record shape")
    if (
        data.get("version") != 1
        or data.get("status") not in _TERMINAL | _NONTERMINAL
    ):
        raise CorrectionDispatchError("invalid correction dispatch record state")
    try:
        _validate_selectors(
            data["correction_dispatch_id"],
            data["source_run_id"],
            data["finding_id"],
            data["executor"],
        )
    except (KeyError, CorrectionDispatchError) as exc:
        raise CorrectionDispatchError("invalid correction dispatch binding") from exc
    expected_ref = (
        "refs/heads/aios/remediation/"
        f"{data['source_run_id']}-{data['finding_id']}"
    )
    if (
        not isinstance(data.get("task_id"), str)
        or not _TASK_ID_PATTERN.fullmatch(data["task_id"])
        or isinstance(data.get("task_revision"), bool)
        or not isinstance(data.get("task_revision"), int)
        or data["task_revision"] < 1
        or not isinstance(data.get("review_id"), str)
        or not data["review_id"]
        or data.get("action") not in {"CODE_FIX", "EVIDENCE_ONLY"}
        or not isinstance(data.get("reviewed_sha"), str)
        or not _SHA_PATTERN.fullmatch(data["reviewed_sha"])
        or data.get("remediation_ref") != expected_ref
        or not isinstance(data.get("remediation_sha"), str)
        or not _SHA_PATTERN.fullmatch(data["remediation_sha"])
        or not isinstance(data.get("approver"), str)
        or not data["approver"]
    ):
        raise CorrectionDispatchError("invalid approved remediation binding")
    pre_run_ids = data.get("pre_run_ids")
    if (
        not isinstance(pre_run_ids, list)
        or any(not isinstance(item, str) for item in pre_run_ids)
        or pre_run_ids != sorted(set(pre_run_ids))
        or any(not _RUN_ID_PATTERN.fullmatch(item) for item in pre_run_ids)
    ):
        raise CorrectionDispatchError("invalid pre-invocation REMEDIATION namespace")
    run_id = data.get("run_id")
    if run_id is not None and (
        not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id)
    ):
        raise CorrectionDispatchError("invalid correction RUN attribution")
    exit_code = data.get("exit_code")
    if exit_code is not None and (
        isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code < 0
    ):
        raise CorrectionDispatchError("invalid correction dispatch exit code")
    if data["status"] in _TERMINAL and exit_code is None:
        raise CorrectionDispatchError("terminal correction dispatch has no outcome")
    if data["status"] == "SUCCEEDED" and (exit_code != 0 or run_id is None):
        raise CorrectionDispatchError("successful correction dispatch is invalid")
    if data["status"] == "FAILED" and (exit_code is None or exit_code == 0):
        raise CorrectionDispatchError("failed correction dispatch is invalid")
    if not isinstance(data.get("detail"), str):
        raise CorrectionDispatchError("invalid correction dispatch detail")
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
        raise CorrectionDispatchError("cannot persist correction dispatch") from exc


def _remediation_run_ids(runs_path: Path) -> tuple[str, ...]:
    if not runs_path.is_dir():
        return ()
    found: list[str] = []
    for path in sorted(runs_path.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(data, Mapping)
            and data.get("kind") == "REMEDIATION"
            and _RUN_ID_PATTERN.fullmatch(path.stem)
        ):
            found.append(path.stem)
    return tuple(sorted(set(found)))


def _matching_new_runs(state_root: Path, record: Mapping[str, Any]) -> tuple[str, ...]:
    bound_run = record["run_id"]
    if bound_run is not None:
        path = state_root / "runs" / f"{bound_run}.json"
        return (bound_run,) if _run_matches_record(path, record) else ()
    before = set(record["pre_run_ids"])
    matches: list[str] = []
    for run_id in _remediation_run_ids(state_root / "runs"):
        if run_id in before:
            continue
        if _run_matches_record(state_root / "runs" / f"{run_id}.json", record):
            matches.append(run_id)
    return tuple(matches)


def _run_matches_record(path: Path, record: Mapping[str, Any]) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        execution = data["execution"]
        run = execution["run"]
        task = run["task"]
        finding = execution["finding"]
        remediation = execution["remediation"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        return False
    if not all(
        isinstance(item, Mapping)
        for item in (data, execution, run, task, finding, remediation)
    ):
        return False
    return (
        data.get("kind") == "REMEDIATION"
        and run.get("run_id") == path.stem
        and task == {"id": record["task_id"], "revision": record["task_revision"]}
        and run.get("executor") == record["executor"]
        and execution.get("review_id") == record["review_id"]
        and finding.get("id") == record["finding_id"]
        and remediation.get("finding_id") == record["finding_id"]
        and remediation.get("reviewed_sha") == record["reviewed_sha"]
        and remediation.get("action") == record["action"]
    )


def _reconcile(state_root: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    matches = _matching_new_runs(state_root, record)
    if len(matches) != 1:
        return {
            **record,
            "status": "RECONCILIATION_BLOCKED",
            "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
            "detail": f"correction RUN attribution is uncertain ({len(matches)} candidates)",
        }
    return _terminal_observation(state_root, record, matches[0])


def _terminal_observation(
    state_root: Path, record: Mapping[str, Any], run_id: str
) -> dict[str, Any]:
    result = (state_root / "results" / f"{run_id}.json").is_file()
    failure = (state_root / "failures" / f"{run_id}.json").is_file()
    if result and not failure:
        return {
            **record,
            "status": "SUCCEEDED",
            "run_id": run_id,
            "exit_code": 0,
            "detail": "attributed canonical REMEDIATION RESULT",
        }
    if failure and not result:
        return {
            **record,
            "status": "FAILED",
            "run_id": run_id,
            "exit_code": 1,
            "detail": "attributed canonical REMEDIATION FAILURE",
        }
    if _lock_is_held(state_root / "operator.lock") and not result and not failure:
        return {
            **record,
            "status": "IN_PROGRESS",
            "run_id": run_id,
            "exit_code": IN_PROGRESS_EXIT_CODE,
            "detail": "attributable REMEDIATION RUN remains active",
        }
    detail = (
        "attributable REMEDIATION RUN has conflicting terminal artifacts"
        if result and failure
        else "attributable REMEDIATION RUN is incomplete"
    )
    return {
        **record,
        "status": "RECONCILIATION_BLOCKED",
        "run_id": run_id,
        "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
        "detail": detail,
    }


def _finalize_invocation(
    state_root: Path,
    record: Mapping[str, Any],
    invocation: CorrectionInvocation,
) -> dict[str, Any]:
    if (
        isinstance(invocation.exit_code, bool)
        or not isinstance(invocation.exit_code, int)
        or invocation.exit_code < 0
    ):
        raise CorrectionDispatchError("REMEDIATION invocation returned invalid exit code")
    matches = _matching_new_runs(state_root, record)
    if invocation.run_id is not None:
        if invocation.run_id not in matches or len(matches) != 1:
            return {
                **record,
                "status": "RECONCILIATION_BLOCKED",
                "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
                "detail": "returned REMEDIATION RUN attribution is uncertain",
            }
        return _terminal_observation(state_root, record, invocation.run_id)
    if len(matches) == 1:
        return _terminal_observation(state_root, record, matches[0])
    if not matches and invocation.exit_code != 0:
        return {
            **record,
            "status": "FAILED",
            "exit_code": invocation.exit_code,
            "detail": "canonical REMEDIATION rejected before RUN creation",
        }
    return {
        **record,
        "status": "RECONCILIATION_BLOCKED",
        "exit_code": RECONCILIATION_BLOCKED_EXIT_CODE,
        "detail": (
            "REMEDIATION outcome has uncertain RUN attribution "
            f"({len(matches)} candidates)"
        ),
    }


def _outcome(record: Mapping[str, Any], *, replayed: bool) -> CorrectionDispatchOutcome:
    return CorrectionDispatchOutcome(
        correction_dispatch_id=record["correction_dispatch_id"],
        source_run_id=record["source_run_id"],
        finding_id=record["finding_id"],
        executor=record["executor"],
        task_id=record["task_id"],
        remediation_sha=record["remediation_sha"],
        status=record["status"],
        run_id=record["run_id"],
        exit_code=(
            record["exit_code"]
            if record["exit_code"] is not None
            else RECONCILIATION_BLOCKED_EXIT_CODE
        ),
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
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            _acquire_lock(stream, blocking=True)
        except OSError as exc:
            stream.close()
            raise CorrectionDispatchError(
                "another correction dispatch decision is active"
            ) from exc
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
            _acquire_lock(stream, blocking=False)
        except OSError:
            return True
        _release_lock(stream)
        return False
    finally:
        stream.close()


def _acquire_lock(stream: BinaryIO, *, blocking: bool) -> None:
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
