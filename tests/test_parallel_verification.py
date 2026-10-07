"""Selected twelve-worker wrapper checks using injected observations only."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from aios_renew.parallel_verification import _failure_diagnostics

from scripts import aios_parallel_full_suite as selected
from test_bp_v4_parallel_probe import observation

REPOSITORY = Path(__file__).resolve().parents[1]
SUBJECT = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
CALLS = [("collection", 1, True), ("parallel-12", 12, False)]


def record_plugin_failure(plugin, index, *, phase="call", nodeid=None, detail="AssertionError: expected 1"):
    item = SimpleNamespace(config=SimpleNamespace(rootpath=Path("C:/exact-subject")))
    report = SimpleNamespace(failed=True, skipped=False, when=phase,
        nodeid=nodeid or f"tests/test_sample.py::test_{index:03d}", longreprtext=detail, sections=[])
    hook = plugin.pytest_runtest_makereport(item, None)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda: report))


def test_plugin_preserves_all_51_failures_independently_of_20_displayed(monkeypatch):
    from tests import bp_v4_probe_plugin as plugin
    monkeypatch.delenv("AIOS_V2_PROFILE", raising=False)
    plugin.pytest_sessionstart(None)
    for index in reversed(range(51)):
        record_plugin_failure(plugin, index)
    summary = plugin._summary(plugin._local_failure_count, plugin._local_failure_facts, plugin._local_failure_truncated)
    profile, toolchain = plugin._conditions()
    assert _failure_diagnostics(dict(failure_diagnostics=summary, exit_status=1, profile=profile, toolchain=toolchain)) == summary
    assert summary["reported_count"] == 51 and summary["displayed_count"] == 20 and summary["truncated"]
    canonical = summary["canonical"]
    assert canonical["complete"] and canonical["failure_count"] == 51
    assert len(canonical["reports"]) == 51
    assert canonical["reports"][-1]["nodeid"].endswith("test_050")
    assert all(r["fingerprint"] and r["profile"] == profile and r["toolchain"] == toolchain for r in canonical["reports"])


def test_plugin_parallel_worker_merge_preserves_exact_nodeid_and_phase(monkeypatch):
    from tests import bp_v4_probe_plugin as plugin
    monkeypatch.delenv("AIOS_V2_PROFILE", raising=False)
    plugin.pytest_sessionstart(None)
    long_nodeid = "tests/test_sample.py::test_parameter[C:\\temp\\" + "x" * 400 + "]"
    record_plugin_failure(plugin, 0, phase="setup", nodeid=long_nodeid)
    record_plugin_failure(plugin, 0, phase="teardown", nodeid=long_nodeid)
    failures = plugin._summary(plugin._local_failure_count, plugin._local_failure_facts, plugin._local_failure_truncated)
    assert all(r["clipped"] for r in failures["displayed_identities"])
    plugin.pytest_sessionstart(None)
    worker = SimpleNamespace(gateway=SimpleNamespace(id="gw0"), workeroutput={"aios_bp_v4_failures": failures})
    plugin.pytest_testnodedown(worker, None)
    canonical = plugin._canonical_summary()
    assert canonical["complete"] and canonical["failure_count"] == 2
    assert [(r["nodeid"], r["phase"]) for r in canonical["reports"]] == [(long_nodeid, "setup"), (long_nodeid, "teardown")]
    assert plugin._parallel_failure_count == 2


def test_plugin_missing_worker_truth_or_oversized_detail_fails_closed():
    from tests import bp_v4_probe_plugin as plugin
    plugin.pytest_sessionstart(None)
    record_plugin_failure(plugin, 0, detail="x" * 16385)
    assert not plugin._canonical_summary()["complete"]
    assert plugin._canonical_summary()["reports"][0]["nodeid"] == "tests/test_sample.py::test_000"
    plugin.pytest_sessionstart(None)
    worker = SimpleNamespace(gateway=SimpleNamespace(id="gw0"), workeroutput={})
    plugin.pytest_testnodedown(worker, "worker crash")
    assert plugin._canonical_summary()["unstable"]


def test_declared_count_cannot_hide_a_smaller_canonical_failure_population():
    from tests import bp_v4_probe_plugin as plugin
    plugin.pytest_sessionstart(None)
    record_plugin_failure(plugin, 0)
    summary = plugin._summary(51, plugin._local_failure_facts, True)
    profile, toolchain = plugin._conditions()
    with pytest.raises(selected.ProbeError, match="population"):
        _failure_diagnostics(dict(failure_diagnostics=summary, exit_status=1, profile=profile, toolchain=toolchain))


def test_plugin_preserves_collection_failure_phase_and_early_stop_is_incomplete(tmp_path, monkeypatch):
    import json
    from tests import bp_v4_probe_plugin as plugin
    config = SimpleNamespace(rootpath=tmp_path, option=SimpleNamespace(collectonly=False))
    session = SimpleNamespace(config=config)
    plugin.pytest_sessionstart(session)
    plugin.pytest_collectreport(SimpleNamespace(failed=True, nodeid="tests/test_broken.py", longreprtext="ImportError: missing dependency"))
    fact = plugin._canonical_summary()["reports"][0]
    assert fact["nodeid"] == "tests/test_broken.py" and fact["phase"] == "collect"
    assert fact["fingerprint"] and fact["toolchain"]
    plugin.pytest_sessionstart(session)
    config._aios_bp_v4_collection = ["tests/test_sample.py::test_000", "tests/test_sample.py::test_001"]
    record_plugin_failure(plugin, 0)
    output = tmp_path / "observation.json"
    monkeypatch.setenv("AIOS_BP_V4_PLUGIN_OUTPUT", str(output))
    plugin.pytest_sessionfinish(session, 1)
    canonical = json.loads(output.read_text(encoding="utf-8"))["failure_diagnostics"]["canonical"]
    assert not canonical["complete"] and canonical["failure_count"] == 1
    assert "complete selected population" in " ".join(canonical["errors"])


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
