import inspect
from collections.abc import Mapping
from dataclasses import fields
from typing import Any

import pytest

from aios_renew.artifacts import Claim, Result, ResultPackage
from aios_renew.dispatcher import (
    Dispatcher,
    DispatcherError,
    NativeExecutionPolicy,
    primary_dispatcher,
    remediation_dispatcher,
    repair_dispatcher,
    resolve_native_execution_policy,
)
from aios_renew.review import Finding, Remediation, RemediationExecution
from aios_renew.run import Run, RunLeaseRegistry
from aios_renew.task import Task, parse_task


TASK_SOURCE = """
task_id: TASK-051
revision: 1
goal: Establish a thin deterministic Dispatcher.
problem: Native invocation is embedded in the operator.
assumptions: []
scope:
  inspect: []
  modify: [src/aios_renew/dispatcher.py]
non_goals: [Add retry or fallback.]
constraints:
  hard: [Invoke exactly one selected Executor.]
acceptance:
  - id: AC1
    condition: Dispatcher invokes the selected Executor once.
verification:
  required: [python -m pytest tests/test_dispatcher.py -q]
"""


def structural_package() -> ResultPackage:
    return ResultPackage(
        result=Result(
            head_sha="def456",
            claims=(Claim("C1", ("AC1",), "Dispatched once.", ()),),
            changed_files=("src/aios_renew/dispatcher.py",),
            unresolved=(),
        ),
        evidence=(),
    )


def execution(executor: str = "codex") -> tuple[Task, Run]:
    task = parse_task(TASK_SOURCE)
    run = Run.from_task(
        run_id="RUN-051-003",
        task=task,
        executor=executor,
        base_sha="abc123",
        workspace="C:/workspace",
    )
    return task, run


class RecordingAdapter:
    def __init__(
        self, executor: str, *, failure: Exception | None = None
    ) -> None:
        self.executor = executor
        self.failure = failure
        self.calls: list[tuple[str, Any]] = []
        self.package = structural_package()

    def _record(self, operation: str, value: Any) -> ResultPackage:
        self.calls.append((operation, value))
        if self.failure is not None:
            raise self.failure
        return self.package

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        return self._record("PRIMARY", (task, run))

    def execute_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage:
        return self._record("REMEDIATION", execution)

    def execute_repair(
        self, *, execution: Mapping[str, Any]
    ) -> ResultPackage:
        return self._record("REPAIR", execution)


@pytest.mark.parametrize("selected", ["codex", "antigravity"])
def test_primary_resolves_only_selected_executor_and_invokes_once(
    selected: str,
) -> None:
    task, run = execution(selected)
    adapters = {
        "codex": RecordingAdapter("codex"),
        "antigravity": RecordingAdapter("antigravity"),
    }
    factory_calls = {"codex": 0, "antigravity": 0}

    def factory(name: str):
        def create() -> RecordingAdapter:
            factory_calls[name] += 1
            return adapters[name]

        return create

    dispatcher = Dispatcher(
        selected_executor=selected,
        operation="PRIMARY",
        adapter_factories={name: factory(name) for name in adapters},
    )
    leases = RunLeaseRegistry()
    lease = leases.acquire(run)

    package = dispatcher.dispatch_primary(
        task=task, run=run, lease=lease, leases=leases
    )

    assert package.result == adapters[selected].package.result
    assert factory_calls[selected] == 1
    assert factory_calls[{"codex", "antigravity"}.difference({selected}).pop()] == 0
    assert adapters[selected].calls == [("PRIMARY", (task, run))]

    with pytest.raises(DispatcherError, match="already dispatched"):
        dispatcher.dispatch_primary(
            task=task, run=run, lease=lease, leases=leases
        )
    assert len(adapters[selected].calls) == 1


def test_native_failure_is_returned_without_fallback() -> None:
    task, run = execution("codex")
    failure = RuntimeError("native failure")
    selected = RecordingAdapter("codex", failure=failure)
    fallback = RecordingAdapter("antigravity")
    dispatcher = Dispatcher(
        selected_executor="codex",
        operation="PRIMARY",
        adapter_factories={
            "codex": lambda: selected,
            "antigravity": lambda: fallback,
        },
    )
    leases = RunLeaseRegistry()
    lease = leases.acquire(run)

    with pytest.raises(RuntimeError, match="native failure"):
        dispatcher.dispatch_primary(
            task=task, run=run, lease=lease, leases=leases
        )

    assert len(selected.calls) == 1
    assert fallback.calls == []
    with pytest.raises(DispatcherError, match="already dispatched"):
        dispatcher.dispatch_primary(
            task=task, run=run, lease=lease, leases=leases
        )


@pytest.mark.parametrize("operation", ["REMEDIATION", "REPAIR"])
def test_correction_operations_preserve_structural_outcome(
    operation: str,
) -> None:
    _, run = execution("antigravity")
    adapter = RecordingAdapter("antigravity")
    dispatcher = Dispatcher(
        selected_executor="antigravity",
        operation=operation,
        adapter_factories={"antigravity": lambda: adapter},
    )

    if operation == "REMEDIATION":
        bounded_execution = RemediationExecution(
            review_id="REVIEW-051-001",
            finding=Finding(
                id="F1",
                basis="AC1",
                action="CODE_FIX",
                location="src/aios_renew/dispatcher.py",
                issue="Boundary missing.",
                expected="Add boundary.",
            ),
            remediation=Remediation(
                finding_id="F1",
                action="CODE_FIX",
                reviewed_sha=run.base_sha,
                modification_scope=("src/aios_renew/dispatcher.py",),
            ),
            run=run,
        )
        package = dispatcher.dispatch_remediation(execution=bounded_execution)
    else:
        bounded_execution = {"run": run, "failed_run_id": "RUN-051-002"}
        package = dispatcher.dispatch_repair(execution=bounded_execution)

    assert package is adapter.package
    assert adapter.calls == [(operation, bounded_execution)]
    with pytest.raises(DispatcherError, match="already dispatched"):
        if operation == "REMEDIATION":
            dispatcher.dispatch_remediation(execution=bounded_execution)
        else:
            dispatcher.dispatch_repair(execution=bounded_execution)
    assert len(adapter.calls) == 1


def test_mismatched_run_is_rejected_before_adapter_construction() -> None:
    task, run = execution("antigravity")
    factory_calls = []
    dispatcher = Dispatcher(
        selected_executor="codex",
        operation="PRIMARY",
        adapter_factories={
            "codex": lambda: factory_calls.append("codex")
        },
    )
    leases = RunLeaseRegistry()
    lease = leases.acquire(run)

    with pytest.raises(DispatcherError, match="does not match RUN executor"):
        dispatcher.dispatch_primary(
            task=task, run=run, lease=lease, leases=leases
        )

    assert factory_calls == []


@pytest.mark.parametrize("authorizes_mutation", [True, False])
def test_execution_policy_is_provider_neutral_and_bounded(
    authorizes_mutation: bool,
) -> None:
    policy = resolve_native_execution_policy(
        authorizes_mutation=authorizes_mutation
    )

    assert policy == NativeExecutionPolicy(
        authorizes_mutation=authorizes_mutation
    )
    assert {field.name for field in fields(policy)} == {
        "authorizes_mutation",
        "response_budget_minutes",
        "process_watchdog_seconds",
    }
    assert policy.response_budget_minutes == 60
    assert policy.process_watchdog_seconds == 65 * 60


def test_dispatcher_factory_surface_exposes_only_provider_neutral_policy() -> None:
    signatures = " ".join(
        str(inspect.signature(factory))
        for factory in (
            primary_dispatcher,
            remediation_dispatcher,
            repair_dispatcher,
        )
    )

    assert "execution_policy" in signatures
    for provider_native_field in ("sandbox", "mode", "permission", "command"):
        assert provider_native_field not in signatures.lower()
