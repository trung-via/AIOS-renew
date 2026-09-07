"""Bounded remote observation and exact Human approval surfaces."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .dispatch_reconciliation import DispatchError, inspect_dispatch
from .review import ReviewValidationError, validate_remediation, validate_review
from .review_transport import ReviewTransportError, resolve_remote_remediation_lineages


_SOURCE_RUN_PATTERN = re.compile(r"^RUN-[A-Za-z0-9_-]+-\d{3,}$")
_FINDING_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_APPROVER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\[\]]{0,99}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_APPROVAL_KEYS = frozenset(
    {
        "version",
        "source_run_id",
        "task_id",
        "task_revision",
        "review_id",
        "finding_id",
        "action",
        "reviewed_sha",
        "remediation_ref",
        "remediation_sha",
        "approver",
    }
)


class RemoteSurfaceError(RuntimeError):
    """Raised with a bounded message safe for a remote operator surface."""


@dataclass(frozen=True)
class RemoteStatusSummary:
    dispatch_id: str
    task_id: str
    executor: str
    stored_status: str
    run_id: str | None
    observed_run_state: str

    def render(self) -> str:
        return (
            "AIOS REMOTE STATUS\n"
            f"dispatch: {self.dispatch_id}\n"
            f"task: {self.task_id}\n"
            f"executor: {self.executor}\n"
            f"stored_status: {self.stored_status}\n"
            f"run: {self.run_id or 'none'}\n"
            f"observed_run_state: {self.observed_run_state}"
        )


@dataclass(frozen=True)
class ApprovalSummary:
    source_run_id: str
    task_id: str
    task_revision: int
    review_id: str
    finding_id: str
    action: str
    reviewed_sha: str
    remediation_ref: str
    remediation_sha: str
    approver: str
    replayed: bool

    def render(self) -> str:
        return (
            "AIOS REMOTE APPROVAL RECORDED\n"
            f"source_run: {self.source_run_id}\n"
            f"task: {self.task_id}\n"
            f"task_revision: {self.task_revision}\n"
            f"review: {self.review_id}\n"
            f"finding: {self.finding_id}\n"
            f"action: {self.action}\n"
            f"reviewed_sha: {self.reviewed_sha}\n"
            f"remediation_ref: {self.remediation_ref}\n"
            f"remediation_sha: {self.remediation_sha}\n"
            f"approver: {self.approver}\n"
            f"replayed: {str(self.replayed).lower()}\n"
            "execution_started: false"
        )


def remote_status(
    dispatch_id: str,
    *,
    repo: Path,
    state_root: Path | None = None,
) -> RemoteStatusSummary:
    """Return only allowlisted facts for one existing dispatch."""

    try:
        if state_root is None:
            from .operator import runtime_state_root

            state_root = runtime_state_root(repo)
        status = inspect_dispatch(state_root=state_root, dispatch_id=dispatch_id)
    except (DispatchError, RuntimeError) as exc:
        raise RemoteSurfaceError("dispatch status is unavailable") from exc
    return RemoteStatusSummary(**asdict(status))


def record_remote_approval(
    source_run_id: str,
    finding_id: str,
    *,
    repo: Path,
    state_root: Path | None = None,
    approver: str,
) -> ApprovalSummary:
    """Persist Human authority for one exact immutable remediation commit."""

    if not _SOURCE_RUN_PATTERN.fullmatch(source_run_id):
        raise RemoteSurfaceError("approval selectors are invalid")
    if not _FINDING_PATTERN.fullmatch(finding_id):
        raise RemoteSurfaceError("approval selectors are invalid")
    if not _APPROVER_PATTERN.fullmatch(approver):
        raise RemoteSurfaceError("approver attribution is invalid")

    try:
        if state_root is None:
            from .operator import runtime_state_root

            state_root = runtime_state_root(repo)
        lineages = resolve_remote_remediation_lineages(
            repo, finding_id=finding_id, source_run_id=source_run_id
        )
        if len(lineages) != 1:
            raise ValueError("exact lineage is missing or ambiguous")
        remote = lineages[0]
        if remote.source_run_id != source_run_id:
            raise ValueError("source RUN mismatch")
        expected_ref = (
            f"refs/heads/aios/remediation/{source_run_id}-{finding_id}"
        )
        if remote.ref != expected_ref:
            raise ValueError("remediation ref identity mismatch")
        if not _SHA_PATTERN.fullmatch(remote.commit_sha):
            raise ValueError("remediation commit is invalid")

        # Reuse the existing frozen-contract lineage validator without entering
        # any admission, execution, verification, reconciliation, or transport path.
        from .operator import _parse_remote_direct_lineage, load_task

        task = load_task(repo, remote.task_id)
        if task.revision != remote.task_revision:
            raise ValueError("source TASK revision is stale")
        parsed = _parse_remote_direct_lineage(repo, task=task, remote=remote)
        if parsed is None:
            raise ValueError("source TASK mismatch")
        review, remediation, result, prior_review = parsed
        validate_review(
            task=task, result=result, review=review, prior_review=prior_review
        )
        validate_remediation(review=review, remediation=remediation, task=task)
        if review.verdict != "CHANGES_REQUIRED":
            raise ValueError("review does not require changes")
        findings = [item for item in review.findings if item.id == finding_id]
        if len(findings) != 1:
            raise ValueError("finding identity mismatch")
        finding = findings[0]
        if (
            remediation.finding_id != finding_id
            or remediation.action != finding.action
        ):
            raise ValueError("remediation identity mismatch")
        if result.head_sha != review.reviewed_sha:
            raise ValueError("reviewed RESULT head mismatch")

        record: dict[str, Any] = {
            "version": 1,
            "source_run_id": source_run_id,
            "task_id": task.task_id,
            "task_revision": task.revision,
            "review_id": review.review_id,
            "finding_id": finding_id,
            "action": remediation.action,
            "reviewed_sha": remediation.reviewed_sha,
            "remediation_ref": remote.ref,
            "remediation_sha": remote.commit_sha,
            "approver": approver,
        }
    except (
        KeyError,
        OSError,
        UnicodeError,
        ValueError,
        ReviewTransportError,
        ReviewValidationError,
        RuntimeError,
    ) as exc:
        raise RemoteSurfaceError(
            "approval lineage is missing, stale, malformed, conflicting, or ambiguous"
        ) from exc

    key_material = "\0".join((source_run_id, finding_id, remote.commit_sha))
    approval_key = hashlib.sha256(key_material.encode("ascii")).hexdigest()
    approval_path = state_root / "approvals" / f"{approval_key}.json"
    with _StateLock(state_root / "approval.lock"):
        replayed = approval_path.is_file()
        if replayed:
            existing = _read_approval(approval_path)
            if existing != record:
                raise RemoteSurfaceError(
                    "approval attribution conflicts with existing authority"
                )
        else:
            _write_approval(approval_path, record)

    return ApprovalSummary(
        source_run_id=record["source_run_id"],
        task_id=record["task_id"],
        task_revision=record["task_revision"],
        review_id=record["review_id"],
        finding_id=record["finding_id"],
        action=record["action"],
        reviewed_sha=record["reviewed_sha"],
        remediation_ref=record["remediation_ref"],
        remediation_sha=record["remediation_sha"],
        approver=record["approver"],
        replayed=replayed,
    )


def _read_approval(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteSurfaceError("existing approval state is invalid") from exc
    if (
        not isinstance(data, dict)
        or set(data) != _APPROVAL_KEYS
        or isinstance(data.get("version"), bool)
        or data.get("version") != 1
        or isinstance(data.get("task_revision"), bool)
        or not isinstance(data.get("task_revision"), int)
        or data["task_revision"] < 1
        or any(
            not isinstance(data.get(key), str) or not data[key]
            for key in _APPROVAL_KEYS - {"version", "task_revision"}
        )
        or not _SHA_PATTERN.fullmatch(data["reviewed_sha"])
        or not _SHA_PATTERN.fullmatch(data["remediation_sha"])
    ):
        raise RemoteSurfaceError("existing approval state is invalid")
    return data


def _write_approval(path: Path, record: Mapping[str, Any]) -> None:
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
        raise RemoteSurfaceError("approval state could not be persisted") from exc


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
            _acquire_lock(stream)
        except OSError as exc:
            stream.close()
            raise RemoteSurfaceError("another approval decision is active") from exc
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


def _acquire_lock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _release_lock(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
