"""Focused selected-wrapper execution checks with an injected fixed runner."""

from pathlib import Path

import pytest

from aios_renew.parallel_verification import ProbeError
from scripts import aios_parallel_full_suite as selected
from test_bp_v4_parallel_probe import observation


def test_one_collection_one_twelve_worker_suite(monkeypatch) -> None:
    calls = []
    subject = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
    monkeypatch.setattr(selected, "subject_identity", lambda _: subject)
    baseline = selected.load_policy(Path(__file__).resolve().parents[1])["baseline"]
    toolchain = {key: baseline[key] for key in (
        "platform_system", "platform_machine", "python_implementation",
        "python_version", "pytest_version", "pytest_xdist_version",
    )}

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        return 0, 1400.0, observation(workers=0 if collect_only else 12)

    envelope, success = selected.execute(
        Path(__file__).resolve().parents[1], runner=runner,
        toolchain_loader=lambda: toolchain,
    )
    assert calls == [("collection", 1, True), ("parallel-12", 12, False)]
    assert success is True
    assert all(envelope["result"]["conformance"].values())
    assert envelope["performance_guard"]["status"] == "NOT_COMPARABLE"
    assert envelope["selected_profile"]["workers"] == 12
    assert envelope["selected_profile"]["selection_provenance"] == {
        "authority": "HUMAN", "task_id": "TASK-231",
    }


def test_cli_rejects_arguments_before_execution(monkeypatch) -> None:
    monkeypatch.setattr(selected, "execute", lambda _: (_ for _ in ()).throw(AssertionError()))
    assert selected.main(["--workers", "3"]) == 2


@pytest.mark.parametrize("process_status,pytest_status", [(1, 0), (0, 1)])
def test_twelve_worker_pytest_failure_is_not_success(
    monkeypatch, process_status, pytest_status,
) -> None:
    subject = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
    monkeypatch.setattr(selected, "subject_identity", lambda _: subject)
    calls = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        if collect_only:
            return 0, 0.1, observation(workers=0)
        return process_status, 0.1, observation(workers=12, exit_status=pytest_status)

    envelope, success = selected.execute(
        Path(__file__).resolve().parents[1], runner=runner,
        toolchain_loader=lambda: {},
    )
    assert calls == [("collection", 1, True), ("parallel-12", 12, False)]
    assert success is False
    assert envelope["correctness_and_conformance_passed"] is False
    assert "failure_diagnostics" in envelope["result"]


@pytest.mark.parametrize("profile_workers,shared_root", [(11, False), (12, True)])
def test_twelve_worker_conformance_defect_fails_closed(
    monkeypatch, profile_workers, shared_root,
) -> None:
    subject = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
    monkeypatch.setattr(selected, "subject_identity", lambda _: subject)
    calls = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        return 0, 0.1, observation(
            workers=0 if collect_only else profile_workers,
            shared_root=shared_root,
        )

    with pytest.raises(ProbeError):
        selected.execute(
            Path(__file__).resolve().parents[1], runner=runner,
            toolchain_loader=lambda: {},
        )
    assert calls == [("collection", 1, True), ("parallel-12", 12, False)]


@pytest.mark.parametrize("changed_after", ["collection", "profile"])
def test_subject_change_fails_before_success(monkeypatch, changed_after) -> None:
    subject = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
    changed = {**subject, "head_sha": "b" * 40}
    identities = iter([subject, changed] if changed_after == "collection" else [subject, subject, changed])
    monkeypatch.setattr(selected, "subject_identity", lambda _: next(identities))
    calls = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        return 0, 0.1, observation(workers=0 if collect_only else 12)

    with pytest.raises(ProbeError):
        selected.execute(
            Path(__file__).resolve().parents[1], runner=runner,
            toolchain_loader=lambda: {},
        )
    assert calls == (
        [("collection", 1, True)] if changed_after == "collection" else
        [("collection", 1, True), ("parallel-12", 12, False)]
    )


def test_failed_canonical_collection_prevents_profile(monkeypatch) -> None:
    subject = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
    monkeypatch.setattr(selected, "subject_identity", lambda _: subject)
    calls = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        return 1, 0.1, observation(workers=0)

    with pytest.raises(ProbeError):
        selected.execute(
            Path(__file__).resolve().parents[1], runner=runner,
            toolchain_loader=lambda: {},
        )
    assert calls == [("collection", 1, True)]


def test_cli_returns_failure_for_unsuccessful_selected_profile(monkeypatch, capsys) -> None:
    monkeypatch.setattr(selected, "execute", lambda _: ({"failure": True}, False))
    assert selected.main([]) == 1
    assert '"failure":true' in capsys.readouterr().out
