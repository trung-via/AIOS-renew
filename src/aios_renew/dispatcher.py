"""Thin deterministic dispatch boundary for admitted native execution."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from .antigravity_adapter import AntigravityAdapter
from .antigravity_minimax_adapter import AntigravityMinimaxAdapter
from .artifacts import ResultPackage
from .codex_adapter import CodexAdapter
from .execution_profile import ResolvedExecutionProfile
from .executor import ExecutorBoundary
from .native_execution_policy import (
    NATIVE_PROCESS_WATCHDOG_SECONDS,
    NATIVE_RESPONSE_BUDGET_MINUTES,
    NativeExecutionPolicy,
    resolve_native_execution_policy,
)
from .review import RemediationExecution
from .run import Run, RunLease, RunLeaseRegistry
from .task import Task


NativeRunner = Callable[..., subprocess.CompletedProcess[bytes]]
Operation = Literal["PRIMARY", "REMEDIATION", "REPAIR"]


class DispatcherError(RuntimeError):
    """Raised when an admitted execution cannot be dispatched as bound."""


class NativeAdapter(Protocol):
    """Provider-neutral execution surface exposed to the Dispatcher."""

    executor: str

    def execute(self, *, task: Task, run: Run) -> ResultPackage: ...

    def execute_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage: ...

    def execute_repair(
        self, *, execution: Mapping[str, Any]
    ) -> ResultPackage: ...


AdapterFactory = Callable[[], NativeAdapter]


class Dispatcher:
    """Resolve and invoke exactly one already-selected native Executor."""

    def __init__(
        self,
        *,
        selected_executor: str,
        operation: Operation,
        adapter_factories: Mapping[str, AdapterFactory],
        execution_profile: ResolvedExecutionProfile | None = None,
        prospective_runtime=None,
    ) -> None:
        try:
            self._adapter_factory = adapter_factories[selected_executor]
        except KeyError as exc:
            raise DispatcherError(
                f"unsupported selected executor: {selected_executor}"
            ) from exc
        self._selected_executor = selected_executor
        self._operation = operation
        self._execution_profile = execution_profile
        self._prospective_runtime = prospective_runtime
        self._invoked = False
        self._active_boundary: ExecutorBoundary | None = None

    @property
    def active_boundary(self) -> ExecutorBoundary | None:
        """Original prospective PRIMARY stack; never an invocation permit."""
        return self._active_boundary

    def dispatch_primary(
        self,
        *,
        task: Task,
        run: Run,
        lease: RunLease,
        leases: RunLeaseRegistry,
    ) -> ResultPackage:
        """Invoke the selected PRIMARY adapter through the frozen lease boundary."""

        owner = self._prospective_runtime
        if owner is not None and getattr(owner, "_ka01_opted", False):
            budget = owner._ka01_budget
            if budget is None:
                raise DispatcherError("KA01_V13_OPTED_RUN_STOP_LATCHED")
            budget.guard(live=False)
            if (budget.operation != "PRIMARY" or task != owner.task or run != owner.run
                    or lease is not budget.lease or leases is not budget.leases
                    or self._execution_profile != budget.profile):
                budget.stop()
                raise DispatcherError("KA01_V13_ORIGINAL_DISPATCH_BINDING_REQUIRED")
        adapter = self._claim_adapter(run=run, operation="PRIMARY")
        if self._prospective_runtime is None:
            return ExecutorBoundary(leases).invoke(task=task, run=run, lease=lease, adapter=adapter)
        boundary = ExecutorBoundary(leases, execution_profile=self._execution_profile,
            prospective_runtime=self._prospective_runtime)
        self._active_boundary = boundary
        try:
            return boundary.invoke(task=task, run=run, lease=lease, adapter=adapter)
        finally:
            self._active_boundary = None

    def dispatch_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage:
        """Invoke the selected adapter for one admitted REMEDIATION."""

        self._require_native_continuity()

        adapter = self._claim_adapter(
            run=execution.run, operation="REMEDIATION"
        )
        return self._require_package(
            adapter.execute_remediation(execution=execution)
        )

    def dispatch_repair(
        self, *, execution: Mapping[str, Any]
    ) -> ResultPackage:
        """Invoke the selected adapter for one admitted REPAIR continuation."""

        self._require_native_continuity()

        run = execution.get("run")
        if not isinstance(run, Run):
            raise DispatcherError("REPAIR execution has no bound RUN")
        adapter = self._claim_adapter(run=run, operation="REPAIR")
        return self._require_package(adapter.execute_repair(execution=execution))

    def _require_native_continuity(self):
        if self._prospective_runtime is not None and getattr(self._prospective_runtime, "_ka01_opted", False):
            budget = self._prospective_runtime._ka01_budget
            if budget is not None:
                budget.stop()
            raise DispatcherError("KA01_V13_CORRECTION_NATIVE_CONTINUITY_BLOCKED")

    def _claim_adapter(
        self, *, run: Run, operation: Operation
    ) -> NativeAdapter:
        if operation != self._operation:
            raise DispatcherError(
                f"Dispatcher is bound to {self._operation}, not {operation}"
            )
        if run.executor != self._selected_executor:
            raise DispatcherError(
                f"selected executor {self._selected_executor!r} does not match "
                f"RUN executor {run.executor!r}"
            )
        if self._execution_profile is not None:
            if self._execution_profile.executor != self._selected_executor:
                raise DispatcherError(
                    f"execution profile executor mismatch: {self._execution_profile.executor!r} "
                    f"does not match selected executor {self._selected_executor!r}"
                )
            if self._execution_profile.executor != run.executor:
                raise DispatcherError(
                    f"execution profile executor mismatch: {self._execution_profile.executor!r} "
                    f"does not match RUN executor {run.executor!r}"
                )
            if self._execution_profile.run_id != run.run_id:
                raise DispatcherError(
                    f"execution profile run_id mismatch: {self._execution_profile.run_id!r} "
                    f"does not match RUN {run.run_id!r}"
                )
        if self._invoked:
            raise DispatcherError("admitted execution was already dispatched")

        # Claim the sole invocation before adapter construction. Construction or
        # native failure is terminal and cannot select an alternate adapter.
        self._invoked = True
        adapter = self._adapter_factory()
        if adapter.executor != self._selected_executor:
            raise DispatcherError(
                f"adapter {adapter.executor!r} does not match selected executor "
                f"{self._selected_executor!r}"
            )
        return adapter

    @staticmethod
    def _require_package(package: ResultPackage) -> ResultPackage:
        if not isinstance(package, ResultPackage):
            raise DispatcherError(
                "native Executor must return a structural ResultPackage"
            )
        return package


def primary_dispatcher(
    *,
    selected_executor: str,
    repo: Path,
    handoff_path: Path,
    execution_policy: NativeExecutionPolicy,
    native_runner: NativeRunner,
    execution_profile: ResolvedExecutionProfile | None = None,
) -> Dispatcher:
    """Bind one provider-neutral admitted PRIMARY execution."""

    return _native_dispatcher(
        selected_executor=selected_executor,
        operation="PRIMARY",
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=execution_policy,
        native_runner=native_runner,
        execution_profile=execution_profile,
    )


def remediation_dispatcher(
    *,
    selected_executor: str,
    repo: Path,
    handoff_path: Path,
    execution_policy: NativeExecutionPolicy,
    native_runner: NativeRunner,
    execution_profile: ResolvedExecutionProfile | None = None,
) -> Dispatcher:
    """Bind one provider-neutral admitted REMEDIATION execution."""

    return _native_dispatcher(
        selected_executor=selected_executor,
        operation="REMEDIATION",
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=execution_policy,
        native_runner=native_runner,
        execution_profile=execution_profile,
    )


def repair_dispatcher(
    *,
    selected_executor: str,
    repo: Path,
    handoff_path: Path,
    execution_policy: NativeExecutionPolicy,
    native_runner: NativeRunner,
    execution_profile: ResolvedExecutionProfile | None = None,
) -> Dispatcher:
    """Bind one provider-neutral admitted REPAIR continuation."""

    return _native_dispatcher(
        selected_executor=selected_executor,
        operation="REPAIR",
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=execution_policy,
        native_runner=native_runner,
        execution_profile=execution_profile,
    )


def _native_dispatcher(
    *,
    selected_executor: str,
    operation: Operation,
    repo: Path,
    handoff_path: Path,
    execution_policy: NativeExecutionPolicy,
    native_runner: NativeRunner,
    execution_profile: ResolvedExecutionProfile | None = None,
) -> Dispatcher:
    return Dispatcher(
        selected_executor=selected_executor,
        operation=operation,
        execution_profile=execution_profile,
        adapter_factories={
            "codex": lambda: CodexAdapter(
                runner=native_runner,
                execution_policy=execution_policy,
                execution_profile=execution_profile,
                native_probe=getattr(native_runner, "native_probe", None),
            ),
            "antigravity": lambda: AntigravityAdapter(
                runner=native_runner,
                execution_policy=execution_policy,
                repo=repo,
                handoff_path=handoff_path,
                execution_profile=execution_profile,
            ),
            "antigravity-minimax": lambda: AntigravityMinimaxAdapter(
                runner=native_runner,
                execution_policy=execution_policy,
                repo=repo,
                handoff_path=handoff_path,
            ),
        },
    )
