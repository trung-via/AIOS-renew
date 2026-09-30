"""Selected twelve-worker wrapper checks using injected observations only."""

from pathlib import Path

import pytest

from scripts import aios_parallel_full_suite as selected
from test_bp_v4_parallel_probe import observation

REPOSITORY = Path(__file__).resolve().parents[1]
SUBJECT = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
CALLS = [("collection", 1, True), ("parallel-12", 12, False)]


def execute(monkeypatch, runner):
    monkeypatch.setattr(selected, "subject_identity", lambda _: dict(SUBJECT))
    return selected.execute(REPOSITORY, runner=runner, toolchain_loader=lambda: {})


def test_one_collection_one_twelve_worker_suite(monkeypatch) -> None:
    calls = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        return 0, 1400.0, observation(workers=0 if collect_only else 12)

    envelope, success = execute(monkeypatch, runner)
    assert calls == CALLS
    assert success is True
    assert all(envelope["result"]["conformance"].values())
    assert envelope["performance_guard"]["status"] == "NOT_COMPARABLE"
    assert envelope["selected_profile"] == {
        "profile": "bounded-parallel-full-suite-v1", "workers": 12,
        "distribution": "load", "max_worker_restart": 0,
        "selection_provenance": {"authority": "HUMAN", "task_id": "TASK-231"},
    }


@pytest.mark.parametrize("status,pytest_status", [(1, 0), (0, 1), (1, 1)])
def test_nonzero_execution_or_pytest_status_fails_without_retry(monkeypatch, status, pytest_status):
    calls = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        if collect_only:
            return 0, 1.0, observation()
        return status, 1.0, observation(workers=12, exit_status=pytest_status)

    envelope, success = execute(monkeypatch, runner)
    assert success is False
    assert envelope["correctness_and_conformance_passed"] is False
    assert "failure_diagnostics" in envelope["result"]
    assert calls == CALLS
    monkeypatch.setattr(selected, "execute", lambda _: (envelope, success))
    assert selected.main([]) == 1


@pytest.mark.parametrize("status,pytest_status", [(1, 0), (0, 1)])
def test_collection_failure_stops_before_profile(monkeypatch, status, pytest_status):
    calls = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        return status, 1.0, observation(exit_status=pytest_status)

    with pytest.raises(selected.ProbeError, match="canonical pytest collection failed"):
        execute(monkeypatch, runner)
    assert calls == CALLS[:1]


@pytest.mark.parametrize("defect", [
    "missing_worker", "collection_disagreement", "shared_temporary_root",
    "shared_git_cache_root", "shared_process", "missing_diagnostics",
])
def test_worker_or_diagnostic_defect_fails_without_retry(monkeypatch, defect):
    calls = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        if collect_only:
            return 0, 1.0, observation()
        data = observation(workers=12)
        first = data["workers"]["gw0"]
        second = data["workers"]["gw1"]
        if defect == "missing_worker":
            del data["workers"]["gw11"]
        elif defect == "collection_disagreement":
            data["worker_collections"]["gw1"] = ["tests/test_other.py::test_other"]
            second["collection"] = data["worker_collections"]["gw1"]
        elif defect == "shared_temporary_root":
            second["temporary_root"] = first["temporary_root"]
        elif defect == "shared_git_cache_root":
            second["git_fixture_cache_root"] = first["git_fixture_cache_root"]
        elif defect == "shared_process":
            second["process_id"] = first["process_id"]
        elif defect == "missing_diagnostics":
            del data["failure_diagnostics"]
        return 0, 1.0, data

    with pytest.raises(selected.ProbeError):
        execute(monkeypatch, runner)
    assert calls == CALLS


@pytest.mark.parametrize("changed_at,expected_calls", [(1, 1), (2, 2)])
def test_subject_changes_after_collection_or_profile_fail(monkeypatch, changed_at, expected_calls):
    calls = []
    subjects = iter([
        dict(SUBJECT) if index < changed_at else {**SUBJECT, "head_sha": "b" * 40}
        for index in range(3)
    ])
    monkeypatch.setattr(selected, "subject_identity", lambda _: next(subjects))

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        return 0, 1.0, observation(workers=0 if collect_only else 12)

    with pytest.raises(selected.ProbeError, match="changed the verification subject"):
        selected.execute(REPOSITORY, runner=runner, toolchain_loader=lambda: {})
    assert calls == CALLS[:expected_calls]


@pytest.mark.parametrize("subject", [
    {"kind": "unavailable", "head_sha": None},
    {**SUBJECT, "worktree_clean": False},
])
def test_nonexact_subject_stops_before_collection(monkeypatch, subject):
    monkeypatch.setattr(selected, "subject_identity", lambda _: subject)

    def runner(*args, **kwargs):
        raise AssertionError("runner must not start")

    with pytest.raises(selected.ProbeError, match="clean exact Git commit"):
        selected.execute(REPOSITORY, runner=runner, toolchain_loader=lambda: {})


def test_false_conformance_fact_prevents_success(monkeypatch):
    monkeypatch.setattr(selected, "_parallel_conformance", lambda *args: {"all_workers_reported": False})
    envelope, success = execute(
        monkeypatch,
        lambda *args, **kwargs: (0, 1.0, observation(workers=0 if kwargs["collect_only"] else 12)),
    )
    assert success is False
    assert envelope["correctness_and_conformance_passed"] is False


def test_cli_surfaces_conformance_error(monkeypatch):
    def fail(_):
        raise selected.ProbeError("worker conformance defect")

    monkeypatch.setattr(selected, "execute", fail)
    assert selected.main([]) == 2


def test_cli_rejects_arguments_before_execution(monkeypatch) -> None:
    monkeypatch.setattr(selected, "execute", lambda _: (_ for _ in ()).throw(AssertionError()))
    assert selected.main(["--workers", "12"]) == 2
