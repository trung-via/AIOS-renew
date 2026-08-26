"""Shared boundary for native executor adapters."""

from __future__ import annotations

from typing import Protocol

from .artifacts import ResultPackage, validate_result_package
from .run import ACTIVE, Run, RunLease, RunLeaseRegistry
from .task import Task


class ExecutorBoundaryError(RuntimeError):
    """Raised when an execution request violates the executor boundary."""


class ExecutorAdapter(Protocol):
    """Minimal interface implemented by a native executor adapter."""

    executor: str

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        """Execute the unchanged semantic TASK within its bound RUN."""
        ...


class ExecutorBoundary:
    """Validate execution authority before invoking one native adapter."""

    def __init__(self, leases: RunLeaseRegistry) -> None:
        self._leases = leases

    def invoke(
        self,
        *,
        task: Task,
        run: Run,
        lease: RunLease,
        adapter: ExecutorAdapter,
    ) -> ResultPackage:
        """Invoke the selected adapter after deterministic boundary checks."""

        if run.task.id != task.task_id or run.task.revision != task.revision:
            raise ExecutorBoundaryError("RUN does not reference the supplied TASK")
        if run.status != ACTIVE:
            raise ExecutorBoundaryError("RUN must be ACTIVE")
        if adapter.executor != run.executor:
            raise ExecutorBoundaryError(
                f"adapter {adapter.executor!r} does not match RUN executor "
                f"{run.executor!r}"
            )
        if not _lease_matches_run(lease, run):
            raise ExecutorBoundaryError("lease does not match the supplied RUN")
        if self._leases.holder(task.task_id) is not lease:
            raise ExecutorBoundaryError("RUN does not hold the active task lease")

        package = adapter.execute(task=task, run=run)
        if not isinstance(package, ResultPackage):
            raise ExecutorBoundaryError(
                "adapter must return a canonical ResultPackage"
            )
        return validate_result_package(
            task=task,
            run=run,
            result=package.result,
            evidence=package.evidence,
        )


def _lease_matches_run(lease: RunLease, run: Run) -> bool:
    return (
        lease.task_id == run.task.id
        and lease.task_revision == run.task.revision
        and lease.run_id == run.run_id
        and lease.executor == run.executor
        and lease.base_sha == run.base_sha
        and lease.workspace == run.workspace
    )
