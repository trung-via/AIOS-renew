import inspect
import json

import pytest

from aios_renew import (
    AntigravityAdapter,
    AntigravityExecutionError,
    AntigravityOutputError,
    ExecutorBoundary,
    ExecutorBoundaryError,
    ResultPackage,
    Run,
    RunLeaseRegistry,
    parse_task,
)


TASK_SOURCE = """
task_id: TASK-008
revision: 1
goal: Add a minimal native Antigravity adapter.
problem: ExecutorBoundary cannot yet invoke Antigravity.
assumptions: []
scope:
  inspect: []
  modify:
    - src/aios_renew/antigravity_adapter.py
non_goals:
  - Executor routing.
constraints:
  hard:
    - Pass TASK and RUN through unchanged.
acceptance:
  - id: AC1
    condition: Antigravity output normalizes into ResultPackage.
verification:
  required:
    - pytest tests/test_antigravity_adapter.py
"""


def make_execution():
    task = parse_task(TASK_SOURCE)
    run = Run.from_task(
        run_id="RUN-008-001",
        task=task,
        executor="antigravity",
        base_sha="abc123",
        workspace="C:/workspace",
    )
    registry = RunLeaseRegistry()
    return task, run, registry, ExecutorBoundary(registry)


def successful_output(run_id: str) -> dict:
    return {
        "result": {
            "head_sha": "def456",
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "Antigravity adapter completed the task.",
                    "evidence": ["E1"],
                }
            ],
            "changed_files": ["src/aios_renew/antigravity_adapter.py"],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id,
                "subject_sha": "def456",
                "type": "TEST",
                "source": {
                    "command": "pytest tests/test_antigravity_adapter.py"
                },
                "result": {"exit_code": 0, "summary": "tests passed"},
                "raw": {"path": ".ai/evidence/E1.log"},
            }
        ],
    }


def test_antigravity_adapter_identity() -> None:
    adapter = AntigravityAdapter(transport=lambda **kwargs: {})

    assert adapter.executor == "antigravity"
    assert list(inspect.signature(adapter.execute).parameters) == ["task", "run"]


def test_hands_off_unchanged_task_and_run() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    captured = {}

    def transport(*, task, run):
        captured["task"] = task
        captured["run"] = run
        return successful_output(run.run_id)

    boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=AntigravityAdapter(transport=transport),
    )

    assert captured == {"task": task, "run": run}
    assert captured["task"] is task
    assert captured["run"] is run


def test_boundary_rejects_without_active_lease_before_native_invocation() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    registry.release(lease)
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        raise AssertionError("transport must not be invoked")

    with pytest.raises(ExecutorBoundaryError, match="active task lease"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=transport),
        )

    assert calls == []


def test_success_normalizes_result_package() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    adapter = AntigravityAdapter(
        transport=lambda **kwargs: json.dumps(successful_output(run.run_id))
    )

    package = boundary.invoke(task=task, run=run, lease=lease, adapter=adapter)

    assert isinstance(package, ResultPackage)
    assert package.result.head_sha == "def456"
    assert package.result.claims[0].satisfies == ("AC1",)
    assert package.evidence[0].run_id == run.run_id


def test_singleton_string_satisfies_is_wrapped_before_validation() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    output = successful_output(run.run_id)
    output["result"]["claims"][0]["satisfies"] = "AC1"

    package = boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=AntigravityAdapter(transport=lambda **kwargs: output),
    )

    assert package.result.claims[0].satisfies == ("AC1",)


def test_list_satisfies_is_preserved() -> None:
    output = successful_output("RUN-008-001")
    output["result"]["claims"][0]["satisfies"] = ["AC1", "AC2"]

    package = AntigravityAdapter._normalize(output)

    assert package.result.claims[0].satisfies == ("AC1", "AC2")


@pytest.mark.parametrize("malformed", [1, {"id": "AC1"}, None])
def test_malformed_non_string_satisfies_still_fails(malformed) -> None:
    output = successful_output("RUN-008-001")
    output["result"]["claims"][0]["satisfies"] = malformed

    with pytest.raises(AntigravityOutputError, match="invalid canonical output"):
        AntigravityAdapter._normalize(output)


@pytest.mark.parametrize("satisfies", ["AC1,AC2", "AC-UNKNOWN"])
def test_string_satisfies_is_not_reinterpreted_and_remains_canonically_bound(
    satisfies: str,
) -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    output = successful_output(run.run_id)
    output["result"]["claims"][0]["satisfies"] = satisfies

    with pytest.raises(ValueError, match="unknown acceptance criteria"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=lambda **kwargs: output),
        )


def test_native_failure_propagates() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)

    def transport(**kwargs):
        raise OSError("native session unavailable")

    with pytest.raises(
        AntigravityExecutionError, match="native session unavailable"
    ) as captured:
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=transport),
        )

    assert isinstance(captured.value.__cause__, OSError)


def test_invalid_output_is_explicit_failure() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)

    with pytest.raises(AntigravityOutputError, match="invalid canonical output"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=lambda **kwargs: {"evidence": []}),
        )


def test_boundary_retains_canonical_artifact_validation() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    output = successful_output("RUN-WRONG")

    with pytest.raises(ValueError, match="does not reference RUN"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=lambda **kwargs: output),
        )


def test_core_executor_boundary_does_not_require_antigravity_specific_logic() -> None:
    source = inspect.getsource(ExecutorBoundary)

    assert "antigravity" not in source.lower()
