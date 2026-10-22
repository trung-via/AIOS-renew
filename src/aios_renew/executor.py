"""Shared boundary for native executor adapters."""

from __future__ import annotations

from typing import Protocol
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock, get_ident
from uuid import uuid4

from .artifacts import ResultPackage, validate_structural_result_package
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


@dataclass
class _LiveInvocation:
    task: Task
    run: Run
    lease: RunLease
    adapter: ExecutorAdapter
    thread: int
    identity: str
    profile: object
    callback: object
    owner: object
    executing: bool = False
    consuming: bool = False
    stopped: bool = False


class ExecutorBoundary:
    """Validate execution authority before invoking one native adapter."""

    def __init__(self, leases: RunLeaseRegistry, *, execution_profile=None, prospective_runtime=None) -> None:
        self._leases = leases
        self._execution_profile = execution_profile
        self._prospective_runtime = prospective_runtime
        self._live: _LiveInvocation | None = None
        self._invocation_lock = Lock()

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

        if self._prospective_runtime is None:
            package = adapter.execute(task=task, run=run)
        else:
            budget = getattr(self._prospective_runtime, "_ka01_budget", None)
            if getattr(self._prospective_runtime, "_ka01_opted", False) and budget is None:
                raise ExecutorBoundaryError("KA01_V13_OPTED_RUN_STOP_LATCHED")
            if budget is not None and budget.operation != "PRIMARY":
                budget.stop()
                raise ExecutorBoundaryError("KA01_V13_CROSS_OPERATION_INVOCATION_BLOCKED")
            package = self._invoke_prospective(task=task, run=run, lease=lease, adapter=adapter)
        if not isinstance(package, ResultPackage):
            raise ExecutorBoundaryError(
                "adapter must return a structural ResultPackage"
            )
        return validate_structural_result_package(
            task=task,
            run=run,
            result=package.result,
            evidence=package.evidence,
        )

    def _invoke_prospective(self, *, task, run, lease, adapter):
        # Only this original execute() stack can establish current liveness.
        # A durable invocation intent is exclusively a negative crash guard.
        with self._invocation_lock:
            if self._live is not None:
                self._live.stopped = True
                raise ExecutorBoundaryError("native invocation reentry is forbidden")
            frame = _LiveInvocation(task, run, lease, adapter, get_ident(), uuid4().hex,
                getattr(adapter, "execution_profile", None),
                getattr(adapter, "confirm_local_feedback", None), self._prospective_runtime)
            self._live = frame
        try:
            self._prospective_runtime._begin_local_feedback_invocation(boundary=self, frame=frame)
            frame.executing = True
            package = adapter.execute(task=task, run=run)
        finally:
            with self._invocation_lock:
                frame.stopped = True
                frame.executing = False
                self._live = None
        return package

    @contextmanager
    def _same_invocation(self, *, owner, task, run, lease, adapter):
        """Internal capability for the same Runtime's explicit local seam."""
        frame = self._live

        def check():
            if (frame is None or self._live is not frame or frame.stopped or not frame.executing
                    or frame.thread != get_ident() or frame.task != task or frame.run != run
                    or frame.lease is not lease or frame.adapter is not adapter
                    or not _lease_matches_run(lease, run) or run.status != ACTIVE
                    or adapter.executor != run.executor or frame.owner is not owner
                    or getattr(adapter, "execution_profile", None) != frame.profile
                    or getattr(adapter, "confirm_local_feedback", None) != frame.callback
                    or not callable(frame.callback)):
                raise ExecutorBoundaryError("same current native invocation not established")
            from .execution_profile import ResolvedExecutionProfile
            profile = frame.profile
            if self._execution_profile is not None and profile != self._execution_profile:
                raise ExecutorBoundaryError("original execution profile changed")
            if profile is not None:
                if type(profile) is not ResolvedExecutionProfile:
                    raise ExecutorBoundaryError("unknown execution profile")
                profile.__post_init__()
                if (profile.run_id, profile.executor) != (run.run_id, run.executor):
                    raise ExecutorBoundaryError("cross-RUN execution profile")
            if self._leases.holder(task.task_id) is not lease:
                raise ExecutorBoundaryError("current original lease holder not established")

        try:
            if frame is not None and frame.consuming:
                raise ExecutorBoundaryError("feedback callback reentry")
            check()
            with self._leases.held(lease):
                frame.consuming = True
                try:
                    yield frame, check
                    check()
                finally:
                    frame.consuming = False
        except BaseException:
            if frame is not None:
                frame.stopped = True
            raise


def _lease_matches_run(lease: RunLease, run: Run) -> bool:
    return (
        lease.task_id == run.task.id
        and lease.task_revision == run.task.revision
        and lease.run_id == run.run_id
        and lease.executor == run.executor
        and lease.base_sha == run.base_sha
        and lease.workspace == run.workspace
    )
