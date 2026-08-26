"""Operational RUN records and one-active-executor lease enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from .task import Task


ACTIVE = "ACTIVE"
SUPPORTED_EXECUTORS = frozenset({"codex", "antigravity"})


class RunValidationError(ValueError):
    """Raised when RUN metadata does not satisfy the canonical contract."""


class LeaseConflictError(RuntimeError):
    """Raised when a task lease is held by another RUN."""


@dataclass(frozen=True)
class RunTaskReference:
    id: str
    revision: int

    def __post_init__(self) -> None:
        _non_empty(self.id, "task.id")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise RunValidationError("task.revision must be a positive integer")


@dataclass(frozen=True)
class Run:
    run_id: str
    task: RunTaskReference
    executor: str
    base_sha: str
    workspace: str
    head_sha: str | None = None
    status: str = ACTIVE

    def __post_init__(self) -> None:
        _non_empty(self.run_id, "run_id")
        if not isinstance(self.task, RunTaskReference):
            raise RunValidationError("task must be a RunTaskReference")
        if self.executor not in SUPPORTED_EXECUTORS:
            raise RunValidationError(
                "executor must be 'codex' or 'antigravity'"
            )
        _non_empty(self.base_sha, "base_sha")
        _non_empty(self.workspace, "workspace")
        if self.head_sha is not None:
            _non_empty(self.head_sha, "head_sha")
        _non_empty(self.status, "status")

    @classmethod
    def from_task(
        cls,
        *,
        run_id: str,
        task: Task,
        executor: str,
        base_sha: str,
        workspace: str,
    ) -> Run:
        """Create an active RUN containing only a reference to its TASK."""

        return cls(
            run_id=run_id,
            task=RunTaskReference(id=task.task_id, revision=task.revision),
            executor=executor,
            base_sha=base_sha,
            workspace=workspace,
        )


@dataclass(frozen=True)
class RunLease:
    task_id: str
    task_revision: int
    run_id: str
    executor: str
    base_sha: str
    workspace: str


class RunLeaseRegistry:
    """Thread-safe, in-memory enforcement of one active RUN per task."""

    def __init__(self) -> None:
        self._leases: dict[str, RunLease] = {}
        self._lock = Lock()

    def acquire(self, run: Run) -> RunLease:
        """Acquire a task lease, idempotently for the current holder."""

        if run.status != ACTIVE:
            raise RunValidationError("only an ACTIVE run may acquire a lease")

        with self._lock:
            current = self._leases.get(run.task.id)
            requested = RunLease(
                task_id=run.task.id,
                task_revision=run.task.revision,
                run_id=run.run_id,
                executor=run.executor,
                base_sha=run.base_sha,
                workspace=run.workspace,
            )
            if current is not None:
                if current == requested:
                    return current
                raise LeaseConflictError(
                    f"{run.task.id} is already leased by {current.run_id}"
                )

            self._leases[run.task.id] = requested
            return requested

    def release(self, lease: RunLease) -> None:
        """Release a lease only when it matches the current holder."""

        with self._lock:
            current = self._leases.get(lease.task_id)
            if current is not lease:
                holder = current.run_id if current is not None else "none"
                raise LeaseConflictError(
                    f"{lease.task_id} lease token does not match holder {holder}"
                )
            del self._leases[lease.task_id]

    def holder(self, task_id: str) -> RunLease | None:
        """Return the current lease holder without mutating registry state."""

        with self._lock:
            return self._leases.get(task_id)


def _non_empty(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunValidationError(f"{path} must be a non-empty string")
    return value
