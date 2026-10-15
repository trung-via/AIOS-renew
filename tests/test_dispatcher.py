import inspect
import json
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any

import pytest

from aios_renew import native_execution_policy
from aios_renew.antigravity_adapter import AntigravityAdapter, AntigravityExecutionError
from aios_renew.antigravity_minimax_adapter import (
    ANTIGRAVITY_MINIMAX_DEFAULT_MODEL,
    AntigravityMinimaxAdapter,
    AntigravityMinimaxExecutionError,
)
from aios_renew.artifacts import Claim, Result, ResultPackage
from aios_renew.codex_adapter import CodexAdapter, CodexExecutionError
from aios_renew.dispatcher import (
    Dispatcher,
    DispatcherError,
    NativeExecutionPolicy,
    primary_dispatcher,
    remediation_dispatcher,
    repair_dispatcher,
    resolve_native_execution_policy,
)
from aios_renew.execution_profile import ResolvedExecutionProfile
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


@pytest.mark.parametrize("selected", ["codex", "antigravity", "antigravity-minimax"])
def test_primary_resolves_only_selected_executor_and_invokes_once(
    selected: str,
) -> None:
    task, run = execution(selected)
    adapters = {
        "codex": RecordingAdapter("codex"),
        "antigravity": RecordingAdapter("antigravity"),
        "antigravity-minimax": RecordingAdapter("antigravity-minimax"),
    }
    factory_calls = {"codex": 0, "antigravity": 0, "antigravity-minimax": 0}

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
    for other in {"codex", "antigravity", "antigravity-minimax"} - {selected}:
        assert factory_calls[other] == 0
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
    assert policy.response_budget_minutes == 180
    assert policy.process_watchdog_seconds == 185 * 60


def test_dispatcher_preserves_shared_policy_imports() -> None:
    from aios_renew.dispatcher import (
        NATIVE_PROCESS_WATCHDOG_SECONDS,
        NATIVE_RESPONSE_BUDGET_MINUTES,
    )

    assert NativeExecutionPolicy is native_execution_policy.NativeExecutionPolicy
    assert (
        resolve_native_execution_policy
        is native_execution_policy.resolve_native_execution_policy
    )
    assert NATIVE_RESPONSE_BUDGET_MINUTES == 180
    assert NATIVE_PROCESS_WATCHDOG_SECONDS == 185 * 60


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


@pytest.mark.parametrize(
    ("factory_func", "operation"),
    [
        (primary_dispatcher, "PRIMARY"),
        (remediation_dispatcher, "REMEDIATION"),
        (repair_dispatcher, "REPAIR"),
    ],
)
def test_native_dispatcher_factories_resolve_antigravity_minimax(
    tmp_path: Path, factory_func: Any, operation: str
) -> None:
    dispatcher = factory_func(
        selected_executor="antigravity-minimax",
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
        execution_policy=resolve_native_execution_policy(
            authorizes_mutation=True
        ),
        native_runner=lambda *args, **kwargs: None,
    )
    assert dispatcher._selected_executor == "antigravity-minimax"
    assert dispatcher._operation == operation
    adapter = dispatcher._adapter_factory()
    assert isinstance(adapter, AntigravityMinimaxAdapter)
    assert adapter.executor == "antigravity-minimax"


def test_native_dispatcher_rejects_unrecognized_executor(
    tmp_path: Path,
) -> None:
    with pytest.raises(DispatcherError, match="unsupported selected executor"):
        primary_dispatcher(
            selected_executor="unrecognized-executor",
            repo=tmp_path,
            handoff_path=tmp_path / "handoff.json",
            execution_policy=resolve_native_execution_policy(
                authorizes_mutation=True
            ),
            native_runner=lambda *args, **kwargs: None,
        )


def test_dispatcher_execution_profile_executor_mismatch_fails(tmp_path: Path) -> None:
    from aios_renew.execution_profile import ResolvedExecutionProfile

    task, run = execution("codex")
    profile = ResolvedExecutionProfile(
        run_id=run.run_id,
        executor="antigravity",
        model="gemini-3.8-flash",
        reasoning_effort="high",
        model_source="REPOSITORY_DEFAULT",
        effort_source="REPOSITORY_DEFAULT",
    )
    dispatcher = primary_dispatcher(
        selected_executor="codex",
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
        execution_policy=resolve_native_execution_policy(authorizes_mutation=True),
        native_runner=lambda *args, **kwargs: None,
        execution_profile=profile,
    )
    leases = RunLeaseRegistry()
    lease = leases.acquire(run)
    with pytest.raises(DispatcherError, match="execution profile executor mismatch"):
        dispatcher.dispatch_primary(task=task, run=run, lease=lease, leases=leases)


def test_dispatcher_execution_profile_run_id_mismatch_fails(tmp_path: Path) -> None:
    from aios_renew.execution_profile import ResolvedExecutionProfile

    task, run = execution("codex")
    profile = ResolvedExecutionProfile(
        run_id="DIFFERENT-RUN-ID",
        executor="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        model_source="REPOSITORY_DEFAULT",
        effort_source="REPOSITORY_DEFAULT",
    )
    dispatcher = primary_dispatcher(
        selected_executor="codex",
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
        execution_policy=resolve_native_execution_policy(authorizes_mutation=True),
        native_runner=lambda *args, **kwargs: None,
        execution_profile=profile,
    )
    leases = RunLeaseRegistry()
    lease = leases.acquire(run)
    with pytest.raises(DispatcherError, match="execution profile run_id mismatch"):
        dispatcher.dispatch_primary(task=task, run=run, lease=lease, leases=leases)


def test_dispatcher_propagates_execution_profile_to_adapter(tmp_path: Path) -> None:
    from aios_renew.execution_profile import ResolvedExecutionProfile
    from aios_renew.codex_adapter import CodexAdapter
    from aios_renew.antigravity_adapter import AntigravityAdapter

    task, run = execution("codex")
    codex_prof = ResolvedExecutionProfile(
        run_id=run.run_id,
        executor="codex",
        model="gpt-7-sol",
        reasoning_effort="medium",
        model_source="EXPLICIT",
        effort_source="EXPLICIT",
    )
    disp_codex = primary_dispatcher(
        selected_executor="codex",
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
        execution_policy=resolve_native_execution_policy(authorizes_mutation=True),
        native_runner=lambda *args, **kwargs: None,
        execution_profile=codex_prof,
    )
    adapter_codex = disp_codex._adapter_factory()
    assert isinstance(adapter_codex, CodexAdapter)
    assert adapter_codex.execution_profile == codex_prof

    anti_task, anti_run = execution("antigravity")
    anti_prof = ResolvedExecutionProfile(
        run_id=anti_run.run_id,
        executor="antigravity",
        model="gemini-4-flash",
        reasoning_effort="low",
        model_source="EXPLICIT",
        effort_source="EXPLICIT",
    )
    disp_anti = primary_dispatcher(
        selected_executor="antigravity",
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
        execution_policy=resolve_native_execution_policy(authorizes_mutation=True),
        native_runner=lambda *args, **kwargs: None,
        execution_profile=anti_prof,
    )
    adapter_anti = disp_anti._adapter_factory()
    assert isinstance(adapter_anti, AntigravityAdapter)
    assert adapter_anti.execution_profile == anti_prof


class FalseySyntheticPolicy(NativeExecutionPolicy):
    """An explicitly supplied test policy must survive even if it is falsey."""

    def __bool__(self) -> bool:
        return False


@pytest.fixture(params=["codex", "antigravity", "antigravity-minimax"])
def native_executor(request) -> str:
    return request.param


@pytest.fixture(params=["PRIMARY", "REMEDIATION", "REPAIR"])
def native_operation(request) -> str:
    return request.param


@pytest.fixture(params=[
    "direct",
    "runtime-read-only",
    "runtime-authorized",
    "synthetic-read-only",
    "synthetic-authorized",
])
def native_policy_case(
    request,
) -> tuple[NativeExecutionPolicy | None, NativeExecutionPolicy]:
    mode = request.param
    authorizes_mutation = mode.endswith("-authorized")
    if mode.startswith("synthetic-"):
        policy = FalseySyntheticPolicy(
            authorizes_mutation=authorizes_mutation,
            response_budget_minutes=1,
            process_watchdog_seconds=75,
        )
        return policy, policy
    expected = NativeExecutionPolicy(
        authorizes_mutation=authorizes_mutation,
        response_budget_minutes=180,
        process_watchdog_seconds=185 * 60,
    )
    policy = (
        None if mode == "direct"
        else resolve_native_execution_policy(authorizes_mutation=authorizes_mutation)
    )
    return policy, expected


@pytest.fixture
def native_invocation(
    tmp_path: Path,
    monkeypatch,
    native_executor: str,
    native_operation: str,
    native_policy_case,
):
    """Bind real adapters and dispatch boundaries exclusively to a synthetic runner."""
    policy, expected = native_policy_case
    task, original_run = execution(native_executor)
    run = replace(original_run, workspace=str(tmp_path))
    profile = (
        ResolvedExecutionProfile(
            run_id=run.run_id,
            executor=native_executor,
            model=f"synthetic/{native_executor}-model",
            reasoning_effort="low",
            model_source="EXPLICIT",
            effort_source="EXPLICIT",
        )
        if native_executor != "antigravity-minimax" else None
    )
    package = structural_package()
    payload = asdict(package)
    if native_executor == "antigravity":
        payload = {"status": "SUCCESS", "structured_output": payload}
    elif native_executor == "antigravity-minimax":
        payload = {
            "schema_version": "agym.result.v1",
            "status": "PASS",
            "exit_code": 0,
            "state_guard": "PASS",
            "workspace": str(tmp_path),
            "head_before": run.base_sha,
            "head_after": package.result.head_sha,
            "response": json.dumps(payload),
        }
    success_output = json.dumps(payload).encode("utf-8")

    def forbid_sleep(*args, **kwargs):
        raise AssertionError("native completion must not wait out the budget")

    monkeypatch.setattr(time, "sleep", forbid_sleep)

    def bind(runner) -> Callable[[], ResultPackage]:
        def checked_runner(command, **kwargs):
            assert kwargs["timeout"] == expected.process_watchdog_seconds
            assert kwargs["capture_output"] is True
            assert kwargs["text"] is False
            assert kwargs["check"] is False
            if native_executor == "codex":
                assert command[command.index("--sandbox") + 1] == (
                    "danger-full-access" if expected.authorizes_mutation else "read-only"
                )
                assert command[command.index("-m") + 1] == profile.model
                assert command[command.index("-c") + 1] == 'model_reasoning_effort="low"'
            elif native_executor == "antigravity":
                assert command[command.index("--print-timeout") + 1] == (
                    f"{expected.response_budget_minutes}m"
                )
                assert (
                    "--dangerously-skip-permissions" in command
                ) is expected.authorizes_mutation
                assert ("--mode" in command) is expected.authorizes_mutation
                if expected.authorizes_mutation:
                    assert command[command.index("--mode") + 1] == "accept-edits"
                assert command[command.index("--model") + 1] == profile.model
                assert command[command.index("--effort") + 1] == profile.reasoning_effort
            else:
                assert command[command.index("--response-timeout") + 1] == str(
                    expected.response_budget_minutes * 60
                )
                assert command[command.index("--timeout") + 1] == str(
                    expected.process_watchdog_seconds
                )
                assert ("--allow-commit" in command) is expected.authorizes_mutation
                assert command[command.index("--model") + 1] == (
                    ANTIGRAVITY_MINIMAX_DEFAULT_MODEL
                )
                assert command[command.index("--operation") + 1] == native_operation
            return runner(command, **kwargs)

        if policy is None:
            if native_executor == "codex":
                adapter = CodexAdapter(runner=checked_runner, execution_profile=profile)
            else:
                adapter_type = (
                    AntigravityAdapter if native_executor == "antigravity"
                    else AntigravityMinimaxAdapter
                )
                profile_kwargs = {"execution_profile": profile} if profile else {}
                adapter = adapter_type(
                    runner=checked_runner,
                    repo=tmp_path,
                    handoff_path=tmp_path / "handoff.json",
                    **profile_kwargs,
                )
            assert type(adapter._execution_policy) is NativeExecutionPolicy
            assert adapter._execution_policy == expected
            dispatcher = None
        else:
            factory = {
                "PRIMARY": primary_dispatcher,
                "REMEDIATION": remediation_dispatcher,
                "REPAIR": repair_dispatcher,
            }[native_operation]
            dispatcher = factory(
                selected_executor=native_executor,
                repo=tmp_path,
                handoff_path=tmp_path / "handoff.json",
                execution_policy=policy,
                native_runner=checked_runner,
                execution_profile=profile,
            )
        leases = RunLeaseRegistry()
        lease = leases.acquire(run)
        correction = RemediationExecution(
            review_id="REVIEW-051-001",
            finding=Finding(
                id="F1", basis="AC1", action="CODE_FIX",
                location="src/aios_renew/dispatcher.py", issue="Missing boundary.",
                expected="Add boundary.",
            ),
            remediation=Remediation(
                finding_id="F1", action="CODE_FIX", reviewed_sha=run.base_sha,
                modification_scope=("src/aios_renew/dispatcher.py",),
            ),
            run=run,
        )
        repair = {
            "run": run, "failed_run_id": "RUN-051-002",
            "failed_head_sha": run.base_sha,
            "repair": {"action": "CODE_FIX", "instructions": ["Fix the boundary."]},
        }

        def invoke() -> ResultPackage:
            if native_operation == "PRIMARY":
                if dispatcher is not None:
                    return dispatcher.dispatch_primary(
                        task=task, run=run, lease=lease, leases=leases
                    )
                return adapter.execute(task=task, run=run)
            if native_operation == "REMEDIATION":
                if dispatcher is not None:
                    return dispatcher.dispatch_remediation(execution=correction)
                return adapter.execute_remediation(execution=correction)
            if dispatcher is not None:
                return dispatcher.dispatch_repair(execution=repair)
            return adapter.execute_repair(execution=repair)

        return invoke

    error_type = {
        "codex": CodexExecutionError,
        "antigravity": AntigravityExecutionError,
        "antigravity-minimax": AntigravityMinimaxExecutionError,
    }[native_executor]
    return bind, success_output, package, error_type


def test_native_budgets_permissions_and_early_completion(
    native_invocation, native_policy_case,
) -> None:
    bind, output, expected_package, _ = native_invocation
    policy, _ = native_policy_case
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")

    invoke = bind(runner)
    assert invoke() == expected_package
    assert len(calls) == 1
    if policy is not None:
        with pytest.raises(DispatcherError, match="already dispatched"):
            invoke()
        assert len(calls) == 1


@pytest.mark.parametrize("stdout,stderr", [
    (b"partial native progress\xff", b"provider deadline\xfe"),
    ("partial native progress", "provider deadline"),
    (None, None),
])
def test_native_watchdog_is_terminal_and_retains_original_diagnostics(
    native_invocation, native_policy_case, stdout, stderr,
) -> None:
    bind, _, _, error_type = native_invocation
    policy, expected = native_policy_case
    calls = []
    timeout = None

    def runner(command, **kwargs):
        nonlocal timeout
        calls.append(command)
        timeout = subprocess.TimeoutExpired(
            command, kwargs["timeout"], output=stdout, stderr=stderr
        )
        raise timeout

    invoke = bind(runner)
    with pytest.raises(error_type, match="process watchdog") as captured:
        invoke()

    assert captured.value.__cause__ is timeout
    assert captured.value.stdout is stdout
    assert captured.value.stderr is stderr
    assert captured.value.exit_code is None
    assert (
        f"{expected.response_budget_minutes}-minute native response budget"
        in str(captured.value)
    )
    assert (
        f"{expected.process_watchdog_seconds}-second process watchdog"
        in str(captured.value)
    )
    assert len(calls) == 1
    if policy is not None:
        with pytest.raises(DispatcherError, match="already dispatched"):
            invoke()
        assert len(calls) == 1


@pytest.mark.parametrize("returncode", [7, None])
def test_native_process_errors_preserve_known_or_unknown_status(
    native_invocation, native_policy_case, returncode,
) -> None:
    bind, _, _, error_type = native_invocation
    policy, _ = native_policy_case
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, returncode, stdout=b"partial native progress", stderr=b"native failure"
        )

    invoke = bind(runner)
    with pytest.raises(error_type) as captured:
        invoke()

    assert captured.value.exit_code == returncode
    assert captured.value.stdout == "partial native progress"
    assert captured.value.stderr == "native failure"
    assert len(calls) == 1
    if policy is not None:
        with pytest.raises(DispatcherError, match="already dispatched"):
            invoke()
        assert len(calls) == 1
