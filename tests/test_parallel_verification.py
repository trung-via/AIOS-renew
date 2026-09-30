"""Selected-wrapper fail-closed checks using injected observations only."""

from pathlib import Path

import pytest

from scripts import aios_parallel_full_suite as selected
from test_bp_v4_parallel_probe import observation


REPOSITORY = Path(__file__).resolve().parents[1]
SUBJECT = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}


def execute_injected(monkeypatch, *, collection=None, parallel=None, statuses=(0, 0)):
    calls = []
    monkeypatch.setattr(selected, "subject_identity", lambda _: dict(SUBJECT))

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        if collect_only:
            return statuses[0], 1.0, observation() if collection is None else collection
        return statuses[1], 1400.0, observation(workers=12) if parallel is None else parallel

    return calls, lambda: selected.execute(
        REPOSITORY, runner=runner, toolchain_loader=lambda: {"platform_system": "Windows"},
    )


def test_one_collection_one_twelve_worker_suite(monkeypatch) -> None:
    calls, execute = execute_injected(monkeypatch)
    envelope, success = execute()
    assert calls == [("collection", 1, True), ("parallel-12", 12, False)]
    assert success is True
    assert envelope["subject"] == SUBJECT
    assert envelope["collection"] == selected.collection_identity(observation()["controller_collection"])
    assert all(envelope["result"]["conformance"].values())
    assert envelope["result"]["workers"] == 12
    assert envelope["performance_guard"]["status"] == "NOT_COMPARABLE"
    assert envelope["selected_profile"] == {
        "profile": "bounded-parallel-full-suite-v1", "workers": 12,
        "distribution": "load", "max_worker_restart": 0,
        "selection_provenance": {"authority": "HUMAN", "task_id": "TASK-231"},
    }


@pytest.mark.parametrize("process_status,pytest_status", [(1, 0), (0, 1), (1, 1)])
def test_nonzero_status_fails_without_retry(monkeypatch, process_status, pytest_status) -> None:
    parallel = observation(workers=12, exit_status=pytest_status)
    calls, execute = execute_injected(monkeypatch, parallel=parallel, statuses=(0, process_status))
    envelope, success = execute()
    assert success is False
    assert envelope["correctness_and_conformance_passed"] is False
    assert "failure_diagnostics" in envelope["result"]
    assert calls == [("collection", 1, True), ("parallel-12", 12, False)]


@pytest.mark.parametrize("defect", [
    "missing_worker", "collection", "temporary_root", "git_fixture_cache_root",
    "process_id", "worker_identity", "malformed_exit_status", "missing_diagnostics",
])
def test_worker_or_observation_defect_fails_without_fallback(monkeypatch, defect) -> None:
    parallel = observation(workers=12)
    workers = parallel["workers"]
    if defect == "missing_worker":
        del workers["gw11"]
    elif defect == "collection":
        parallel["worker_collections"]["gw11"] = ["tests/other.py::test_other"]
        workers["gw11"]["collection"] = parallel["worker_collections"]["gw11"]
    elif defect in {"temporary_root", "git_fixture_cache_root", "process_id"}:
        workers["gw11"][defect] = workers["gw0"][defect]
    elif defect == "worker_identity":
        workers["gw11"]["worker_id"] = "gw0"
    elif defect == "malformed_exit_status":
        parallel["exit_status"] = False
    else:
        del parallel["failure_diagnostics"]
    calls, execute = execute_injected(monkeypatch, parallel=parallel)
    with pytest.raises(selected.ProbeError):
        execute()
    assert calls == [("collection", 1, True), ("parallel-12", 12, False)]


@pytest.mark.parametrize("defect", ["process_status", "pytest_status", "duplicate_nodeid"])
def test_collection_failure_prevents_profile(monkeypatch, defect) -> None:
    collection = observation()
    status = 0
    if defect == "process_status":
        status = 1
    elif defect == "pytest_status":
        collection["exit_status"] = 1
    else:
        collection["controller_collection"] *= 2
    calls, execute = execute_injected(monkeypatch, collection=collection, statuses=(status, 0))
    with pytest.raises(selected.ProbeError):
        execute()
    assert calls == [("collection", 1, True)]


@pytest.mark.parametrize("stage", ["initial", "post_collection", "post_profile"])
def test_subject_gate_fails_at_each_boundary(monkeypatch, stage) -> None:
    calls, execute = execute_injected(monkeypatch)
    changed = {**SUBJECT, "head_sha": "b" * 40}
    subjects = iter({
        "initial": [{**SUBJECT, "worktree_clean": False}],
        "post_collection": [SUBJECT, changed],
        "post_profile": [SUBJECT, SUBJECT, changed],
    }[stage])
    monkeypatch.setattr(selected, "subject_identity", lambda _: next(subjects))
    with pytest.raises(selected.ProbeError):
        execute()
    assert len(calls) == {"initial": 0, "post_collection": 1, "post_profile": 2}[stage]


def test_false_conformance_fact_cannot_be_success(monkeypatch) -> None:
    _, execute = execute_injected(monkeypatch)
    monkeypatch.setattr(selected, "_parallel_conformance", lambda *_: {"all_workers_reported": False})
    envelope, success = execute()
    assert success is False
    assert envelope["correctness_and_conformance_passed"] is False


@pytest.mark.parametrize("outcome", ["passed", "failed", "defect"])
def test_cli_exit_status_surfaces_execution_outcome(monkeypatch, outcome) -> None:
    def execute(_):
        if outcome == "defect":
            raise selected.ProbeError("worker conformance defect")
        return {"correctness_and_conformance_passed": outcome == "passed"}, outcome == "passed"

    monkeypatch.setattr(selected, "execute", execute)
    assert selected.main([]) == {"passed": 0, "failed": 1, "defect": 2}[outcome]


def test_cli_rejects_arguments_before_execution(monkeypatch) -> None:
    monkeypatch.setattr(selected, "execute", lambda _: (_ for _ in ()).throw(AssertionError()))
    assert selected.main(["--workers", "12"]) == 2
