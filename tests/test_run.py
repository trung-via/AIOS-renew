import pytest

from aios_renew import (
    LeaseConflictError,
    Run,
    RunLease,
    RunLeaseRegistry,
    RunTaskReference,
    RunValidationError,
)


def make_run(
    run_id: str,
    *,
    task_id: str = "TASK-003",
    revision: int = 1,
    executor: str = "codex",
) -> Run:
    return Run(
        run_id=run_id,
        task=RunTaskReference(id=task_id, revision=revision),
        executor=executor,
        base_sha="abc123",
        workspace="C:/workspace",
    )


def test_run_contains_task_reference_and_operational_metadata_only() -> None:
    run = make_run("RUN-003-001")

    assert run.task == RunTaskReference(id="TASK-003", revision=1)
    assert run.status == "ACTIVE"
    assert run.head_sha is None
    assert not hasattr(run, "goal")


def test_rejects_unsupported_executor() -> None:
    with pytest.raises(RunValidationError, match="executor"):
        make_run("RUN-003-001", executor="other")


def test_run_supports_antigravity_minimax_executor() -> None:
    run = make_run("RUN-003-001", executor="antigravity-minimax")

    assert run.executor == "antigravity-minimax"
    assert run.task == RunTaskReference(id="TASK-003", revision=1)
    assert run.status == "ACTIVE"


def test_antigravity_minimax_run_acquires_and_releases_lease() -> None:
    registry = RunLeaseRegistry()
    run = make_run("RUN-003-001", executor="antigravity-minimax")
    lease = registry.acquire(run)

    assert lease.executor == "antigravity-minimax"
    assert registry.holder("TASK-003") == lease
    registry.release(lease)
    assert registry.holder("TASK-003") is None


def test_run_antigravity_minimax_valid_in_fresh_process_before_adapter_import() -> None:
    import subprocess
    import sys

    code = """
import sys
from aios_renew.run import Run, RunTaskReference, SUPPORTED_EXECUTORS

assert "aios_renew.antigravity_minimax_adapter" not in sys.modules
assert SUPPORTED_EXECUTORS == frozenset({"codex", "antigravity", "antigravity-minimax"})

run = Run(
    run_id="RUN-138-001",
    task=RunTaskReference(id="TASK-138", revision=1),
    executor="antigravity-minimax",
    base_sha="abc123",
    workspace="C:/workspace",
)
assert run.executor == "antigravity-minimax"
assert "aios_renew.antigravity_minimax_adapter" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0


@pytest.mark.parametrize(
    "unsupported",
    ["other", "agym", "minimax", "", "None", "antigravity_minimax"],
)
def test_rejects_various_unsupported_executors(unsupported: str) -> None:
    with pytest.raises(
        RunValidationError,
        match="executor must be 'codex', 'antigravity', or 'antigravity-minimax'",
    ):
        make_run("RUN-003-001", executor=unsupported)


def test_same_run_acquires_lease_idempotently() -> None:
    registry = RunLeaseRegistry()
    run = make_run("RUN-003-001")

    assert registry.acquire(run) is registry.acquire(run)


def test_competing_run_cannot_lease_same_task_across_revisions() -> None:
    registry = RunLeaseRegistry()
    registry.acquire(make_run("RUN-003-001", revision=1))

    with pytest.raises(LeaseConflictError, match="RUN-003-001"):
        registry.acquire(make_run("RUN-003-002", revision=2))


def test_release_allows_next_run_to_acquire() -> None:
    registry = RunLeaseRegistry()
    first = registry.acquire(make_run("RUN-003-001"))
    registry.release(first)

    second = registry.acquire(make_run("RUN-003-002", executor="antigravity"))

    assert registry.holder("TASK-003") == second


def test_non_holder_cannot_release_lease() -> None:
    registry = RunLeaseRegistry()
    current = registry.acquire(make_run("RUN-003-001"))
    forged = RunLease(
        task_id=current.task_id,
        task_revision=current.task_revision,
        run_id=current.run_id,
        executor=current.executor,
        base_sha=current.base_sha,
        workspace=current.workspace,
    )

    with pytest.raises(LeaseConflictError, match="token does not match"):
        registry.release(forged)

    assert registry.holder("TASK-003") == current
