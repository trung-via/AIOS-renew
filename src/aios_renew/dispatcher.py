"""Thin deterministic dispatch boundary for admitted native execution."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from .antigravity_adapter import AntigravityAdapter
from .artifacts import ResultPackage
from .codex_adapter import CodexAdapter
from .executor import ExecutorBoundary
from .review import RemediationExecution
from .run import Run, RunLease, RunLeaseRegistry
from .task import Task


NativeRunner = Callable[..., subprocess.CompletedProcess[bytes]]
Operation = Literal["PRIMARY", "REMEDIATION", "REPAIR"]
NATIVE_RESPONSE_BUDGET_MINUTES = 15
NATIVE_PROCESS_WATCHDOG_SECONDS = NATIVE_RESPONSE_BUDGET_MINUTES * 60


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


@dataclass(frozen=True)
class NativeExecutionPolicy:
    """Provider-neutral policy derived from admitted execution authority."""

    authorizes_mutation: bool
    response_budget_minutes: int = NATIVE_RESPONSE_BUDGET_MINUTES
    process_watchdog_seconds: int = NATIVE_PROCESS_WATCHDOG_SECONDS


def resolve_native_execution_policy(
    *, authorizes_mutation: bool
) -> NativeExecutionPolicy:
    """Resolve the bounded provider-neutral policy before dispatch."""

    return NativeExecutionPolicy(authorizes_mutation=authorizes_mutation)


class Dispatcher:
    """Resolve and invoke exactly one already-selected native Executor."""

    def __init__(
        self,
        *,
        selected_executor: str,
        operation: Operation,
        adapter_factories: Mapping[str, AdapterFactory],
    ) -> None:
        try:
            self._adapter_factory = adapter_factories[selected_executor]
        except KeyError as exc:
            raise DispatcherError(
                f"unsupported selected executor: {selected_executor}"
            ) from exc
        self._selected_executor = selected_executor
        self._operation = operation
        self._invoked = False

    def dispatch_primary(
        self,
        *,
        task: Task,
        run: Run,
        lease: RunLease,
        leases: RunLeaseRegistry,
    ) -> ResultPackage:
        """Invoke the selected PRIMARY adapter through the frozen lease boundary."""

        adapter = self._claim_adapter(run=run, operation="PRIMARY")
        return ExecutorBoundary(leases).invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=adapter,
        )

    def dispatch_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage:
        """Invoke the selected adapter for one admitted REMEDIATION."""

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

        run = execution.get("run")
        if not isinstance(run, Run):
            raise DispatcherError("REPAIR execution has no bound RUN")
        adapter = self._claim_adapter(run=run, operation="REPAIR")
        return self._require_package(adapter.execute_repair(execution=execution))

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
) -> Dispatcher:
    """Bind one provider-neutral admitted PRIMARY execution."""

    return _native_dispatcher(
        selected_executor=selected_executor,
        operation="PRIMARY",
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=execution_policy,
        native_runner=native_runner,
    )


def remediation_dispatcher(
    *,
    selected_executor: str,
    repo: Path,
    handoff_path: Path,
    execution_policy: NativeExecutionPolicy,
    native_runner: NativeRunner,
) -> Dispatcher:
    """Bind one provider-neutral admitted REMEDIATION execution."""

    return _native_dispatcher(
        selected_executor=selected_executor,
        operation="REMEDIATION",
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=execution_policy,
        native_runner=native_runner,
    )


def repair_dispatcher(
    *,
    selected_executor: str,
    repo: Path,
    handoff_path: Path,
    execution_policy: NativeExecutionPolicy,
    native_runner: NativeRunner,
) -> Dispatcher:
    """Bind one provider-neutral admitted REPAIR continuation."""

    return _native_dispatcher(
        selected_executor=selected_executor,
        operation="REPAIR",
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=execution_policy,
        native_runner=native_runner,
    )


def _native_dispatcher(
    *,
    selected_executor: str,
    operation: Operation,
    repo: Path,
    handoff_path: Path,
    execution_policy: NativeExecutionPolicy,
    native_runner: NativeRunner,
) -> Dispatcher:
    return Dispatcher(
        selected_executor=selected_executor,
        operation=operation,
        adapter_factories={
            "codex": lambda: CodexAdapter(
                runner=native_runner,
                execution_policy=execution_policy,
            ),
            "antigravity": lambda: AntigravityAdapter(
                runner=native_runner,
                execution_policy=execution_policy,
                repo=repo,
                handoff_path=handoff_path,
            ),
        },
    )
