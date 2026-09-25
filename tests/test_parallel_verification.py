"""Focused selected-wrapper execution checks with an injected fixed runner."""

from pathlib import Path

from scripts import aios_parallel_full_suite as selected
from test_bp_v4_parallel_probe import observation


def test_one_collection_one_four_worker_suite(monkeypatch) -> None:
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
        return 0, 1400.0, observation(workers=0 if collect_only else 4)

    envelope, success = selected.execute(
        Path(__file__).resolve().parents[1], runner=runner,
        toolchain_loader=lambda: toolchain,
    )
    assert calls == [("collection", 1, True), ("parallel-4", 4, False)]
    assert success is True
    assert envelope["performance_guard"]["status"] == "NOT_COMPARABLE"
    assert envelope["selected_profile"]["workers"] == 4


def test_cli_rejects_arguments_before_execution(monkeypatch) -> None:
    monkeypatch.setattr(selected, "execute", lambda _: (_ for _ in ()).throw(AssertionError()))
    assert selected.main(["--workers", "3"]) == 2
