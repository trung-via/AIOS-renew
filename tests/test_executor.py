import pytest

from aios_renew import (
    Claim,
    ExecutorBoundary,
    ExecutorBoundaryError,
    ResultPackage,
    Result,
    Run,
    RunLeaseRegistry,
    Task,
    parse_evidence,
    parse_result,
    parse_task,
)


TASK_SOURCE = """
task_id: TASK-004
revision: 1
goal: Establish the executor boundary.
problem: Executors require one canonical invocation interface.
assumptions: []
scope:
  inspect: []
  modify:
    - src/aios_renew/executor.py
non_goals:
  - Native executor integration.
constraints:
  hard:
    - Pass TASK through unchanged.
acceptance:
  - id: AC1
    condition: Only the leased executor can be invoked.
verification:
  required:
    - pytest tests/test_executor.py
"""


class FakeAdapter:
    def __init__(self, executor: str = "codex") -> None:
        self.executor = executor
        self.calls: list[tuple[Task, Run]] = []

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        self.calls.append((task, run))
        result = parse_result(
            """
head_sha: def456
claims:
  - id: C1
    satisfies: [AC1]
    claim: The native executor completed the task.
    evidence: [E1]
changed_files:
  - src/aios_renew/executor.py
unresolved: []
"""
        )
        evidence = parse_evidence(
            f"""
evidence_id: E1
run_id: {run.run_id}
subject_sha: def456
type: TEST
source:
  command: pytest tests/test_executor.py
result:
  exit_code: 0
  summary: tests passed
raw:
  path: .ai/evidence/E1.log
"""
        )
        return ResultPackage(result=result, evidence=(evidence,))


def make_execution() -> tuple[
    Task, Run, RunLeaseRegistry, ExecutorBoundary, FakeAdapter
]:
    task = parse_task(TASK_SOURCE)
    run = Run.from_task(
        run_id="RUN-004-001",
        task=task,
        executor="codex",
        base_sha="abc123",
        workspace="C:/workspace",
    )
    registry = RunLeaseRegistry()
    boundary = ExecutorBoundary(registry)
    return task, run, registry, boundary, FakeAdapter()


def test_invokes_adapter_with_unchanged_task_and_run() -> None:
    task, run, registry, boundary, adapter = make_execution()
    lease = registry.acquire(run)

    output = boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=adapter,
    )

    assert output.result.head_sha == "def456"
    assert output.evidence[0].evidence_id == "E1"
    assert adapter.calls == [(task, run)]
    assert adapter.calls[0][0] is task
    assert adapter.calls[0][1] is run


def test_boundary_accepts_structural_package_without_executor_evidence() -> None:
    task, run, registry, boundary, _ = make_execution()
    lease = registry.acquire(run)

    class StructuralAdapter:
        executor = "codex"

        def execute(self, *, task: Task, run: Run) -> ResultPackage:
            return ResultPackage(
                result=Result(
                    head_sha="def456",
                    claims=(Claim("C1", ("AC1",), "Implemented.", ()),),
                    changed_files=("src/aios_renew/executor.py",),
                    unresolved=(),
                ),
                evidence=(),
            )

    package = boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=StructuralAdapter(),
    )

    assert package.result.claims[0].evidence == ()
    assert package.evidence == ()


def test_rejects_run_for_different_task_revision() -> None:
    task, run, registry, boundary, adapter = make_execution()
    lease = registry.acquire(run)
    revised_task = parse_task(TASK_SOURCE.replace("revision: 1", "revision: 2"))

    with pytest.raises(ExecutorBoundaryError, match="does not reference"):
        boundary.invoke(
            task=revised_task,
            run=run,
            lease=lease,
            adapter=adapter,
        )

    assert adapter.calls == []


def test_rejects_adapter_not_selected_by_run() -> None:
    task, run, registry, boundary, _ = make_execution()
    lease = registry.acquire(run)
    adapter = FakeAdapter(executor="antigravity")

    with pytest.raises(ExecutorBoundaryError, match="does not match"):
        boundary.invoke(task=task, run=run, lease=lease, adapter=adapter)

    assert adapter.calls == []


def test_rejects_released_lease() -> None:
    task, run, registry, boundary, adapter = make_execution()
    lease = registry.acquire(run)
    registry.release(lease)

    with pytest.raises(ExecutorBoundaryError, match="active task lease"):
        boundary.invoke(task=task, run=run, lease=lease, adapter=adapter)

    assert adapter.calls == []


def test_rejects_run_state_not_bound_to_lease() -> None:
    task, run, registry, boundary, adapter = make_execution()
    lease = registry.acquire(run)
    changed_run = Run.from_task(
        run_id=run.run_id,
        task=task,
        executor=run.executor,
        base_sha="different-sha",
        workspace=run.workspace,
    )

    with pytest.raises(ExecutorBoundaryError, match="does not match"):
        boundary.invoke(
            task=task,
            run=changed_run,
            lease=lease,
            adapter=adapter,
        )

    assert adapter.calls == []
