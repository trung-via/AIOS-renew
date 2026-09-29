"""Local contract tests for the isolated full-suite contention diagnostic."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import aios_full_suite_contention_diagnostic as diagnostic
import aios_full_suite_contention_plugin as plugin


def _path(path: str) -> dict[str, object]:
    return {"path": path, "length": 80}


def _failure(node: str, worker: str) -> dict:
    return {"nodeid": node, "phase": "call", "worker_id": worker,
            "cause": {"exception_type": "AssertionError", "message_fingerprint": "sha256:" + "a" * 64}}


def _observation(label: str, workers: int, nodes: list[str], failed: list[str] | None = None,
                 *, collection_only: bool = False) -> dict:
    failed = failed or []
    worker_ids = [f"gw{i}" for i in range(workers)] if workers > 1 else []
    facts = [_failure(node, worker_ids[index % workers] if worker_ids else "controller")
             for index, node in enumerate(failed)]
    return {
        "schema": diagnostic.SCHEMA, "version": 1, "exit_status": int(bool(failed)),
        "controller_collection": None if worker_ids else nodes,
        "nodeid_fingerprints": {
            node: "sha256:" + hashlib.sha256(node.encode("utf-8")).hexdigest() for node in nodes
        },
        "worker_collections": {key: nodes for key in worker_ids},
        "workers": {key: {
            "worker_id": key, "process_id": 200 + index,
            "temporary_root": _path(f"<pytest_basetemp>/popen-{key}"),
            "git_fixture_cache_root": _path(f"<diagnostic_temp>/{label}/aios-git-fixtures-{key}"),
            "collection": nodes,
        } for index, key in enumerate(worker_ids)},
        "worker_failure_reports": sorted(worker_ids), "worker_execution_reports": sorted(worker_ids),
        "failures": facts, "over_bound": False,
        "executed_nodeids": [] if collection_only else nodes,
        "controller_process": {"process_id": 100, "temporary_root": _path("<pytest_basetemp>"),
                               "git_fixture_cache_root": _path(f"<diagnostic_temp>/{label}/aios-git-fixtures-controller")},
    }


NODES = [f"tests/test_example.py::test_{index}" for index in range(3)]
SUBJECT = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}


def _diagnose(monkeypatch: pytest.MonkeyPatch, full: list[str], serial: list[str] | None = None,
              n4: list[str] | None = None, *, nodes: list[str] = NODES):
    calls = []
    monkeypatch.setattr(diagnostic, "subject_identity", lambda _repository: SUBJECT)

    def runner(_repository, _root, *, label, workers, collect_only, nodeids):
        calls.append((label, workers, collect_only, nodeids))
        selected = {"full-n12": full, "serial": serial or [], "n4": n4 or []}.get(label, [])
        observed = _observation(label, workers, nodes if collect_only or label == "full-n12" else list(nodeids),
                                selected, collection_only=collect_only)
        return observed["exit_status"], observed

    result = diagnostic.diagnose(Path.cwd(), runner=runner, loader=lambda: {})
    return result, calls


def test_full_n12_pass_stops_without_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = _diagnose(monkeypatch, [])
    assert result["classification"] == "FULL_N12_PASS"
    assert [call[0] for call in calls] == ["collection", "full-n12"]
    assert result["full_n12_failed_nodeids"] == []


@pytest.mark.parametrize(("serial", "n4", "classification"), [
    ([], [], "FULL_SUITE_CONTEXT_DEPENDENT"),
    ([NODES[0], NODES[1]], [], "SERIAL_REPRODUCIBLE"),
    ([], [NODES[0]], "N4_PARALLEL_REPRODUCIBLE"),
    ([NODES[0]], [], "MIXED"),
])
def test_replay_classifications_and_exact_identity(monkeypatch: pytest.MonkeyPatch,
                                                     serial: list[str], n4: list[str],
                                                     classification: str) -> None:
    failed = NODES[:2]
    result, calls = _diagnose(monkeypatch, failed, serial, n4)
    assert result["classification"] == classification
    assert result["full_n12_failed_nodeids"] == sorted(map(diagnostic._safe_nodeid, failed))
    assert result["serial_reproduced_nodeids"] == sorted(map(diagnostic._safe_nodeid, serial))
    assert result["n4_reproduced_nodeids"] == sorted(map(diagnostic._safe_nodeid, n4))
    assert calls == [("collection", 1, True, ()), ("full-n12", 12, False, ()),
                     ("serial", 1, False, tuple(failed)), ("n4", 4, False, tuple(failed))]


def test_parameter_data_replays_exactly_without_durable_disclosure(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [
        'tests/test_example.py::test_case[/tmp/name]',
        'tests/test_example.py::test_case["quoted" and \'single\']',
        'tests/test_example.py::test_case[scheme://private.example/path]',
        'tests/test_example.py::test_case[../relative]',
        'tests/test_example.py::test_case[user@example.test%value$]',
        'tests/test_example.py::test_case[C:\\private\\name]',
    ]
    result, calls = _diagnose(monkeypatch, nodes, nodes[:2], nodes[2:4], nodes=nodes)
    assert calls[2][3] == tuple(sorted(nodes))
    assert calls[3][3] == tuple(sorted(nodes))
    expected = sorted(diagnostic._safe_nodeid(node) for node in nodes)
    assert result["full_n12_failed_nodeids"] == expected
    assert result["serial_reproduced_nodeids"] == sorted(map(diagnostic._safe_nodeid, nodes[:2]))
    assert result["n4_reproduced_nodeids"] == sorted(map(diagnostic._safe_nodeid, nodes[2:4]))
    assert len(set(expected)) == len(nodes)
    for node, safe in zip(nodes, map(diagnostic._safe_nodeid, nodes)):
        assert safe.startswith("tests/test_example.py::test_case#sha256:")
        assert safe.endswith(hashlib.sha256(node.encode("utf-8")).hexdigest())
    durable = json.dumps(result, sort_keys=True)
    assert all(node not in durable for node in nodes)
    assert all(fact["nodeid"] in expected for profile in result["profiles"]
               for fact in profile["failures"])


@pytest.mark.parametrize("node", [
    "C:/private/test_example.py::test_case[x]",
    "/tests/test_example.py::test_case[x]",
    "tests/../test_example.py::test_case[x]",
    "tests\\test_example.py::test_case[x]",
    "tests/test_example.py::test_case[bad\x00value]",
    "tests/test_example.py::test_case[bad\x1fvalue]",
    "tests/test_example.py::test_case[unterminated",
])
def test_unsafe_nodeid_region_fails_closed(node: str) -> None:
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._nodeid(node)


def test_ambiguous_identity_and_tampered_fingerprint_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = ["tests/test_example.py::test_case[first]", "tests/test_example.py::test_case[second]"]
    monkeypatch.setattr(diagnostic, "subject_identity", lambda _repository: SUBJECT)

    def runner(_repository, _root, *, label, workers, collect_only, nodeids):
        value = _observation(label, workers, nodes, [nodes[0]] if label == "full-n12" else [],
                             collection_only=collect_only)
        if label == "collection":
            value["nodeid_fingerprints"][nodes[1]] = value["nodeid_fingerprints"][nodes[0]]
        return value["exit_status"], value

    with pytest.raises(diagnostic.DiagnosticError, match="fingerprint"):
        diagnostic.diagnose(Path.cwd(), runner=runner, loader=lambda: {})
    with pytest.raises(diagnostic.DiagnosticError, match="fingerprint"):
        diagnostic._fingerprints({nodes[0]: "sha256:not-a-digest"}, nodes[:1])

    with monkeypatch.context() as patch:
        patch.setattr(diagnostic, "_safe_nodeid", lambda _node: "same-safe-identity")
        with pytest.raises(diagnostic.DiagnosticError, match="duplicate or inconsistent"):
            diagnostic._identities(nodes)


def test_observer_preserves_raw_selector_during_collection() -> None:
    node = r"tests/test_example.py::test_case[C:\private\name]"
    config = SimpleNamespace()
    plugin.pytest_collection_finish(SimpleNamespace(config=config, items=[SimpleNamespace(nodeid=node)]))
    assert config._aios_contention_collection == [node]
    worker = SimpleNamespace(gateway=SimpleNamespace(id="gw0"))
    try:
        plugin.pytest_xdist_node_collection_finished(worker, [node])
        assert plugin._collections["gw0"] == [node]
    finally:
        plugin._collections.clear()


def test_exact_64_identity_bound_and_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [f"tests/test_example.py::test_{index}" for index in range(65)]
    result, _ = _diagnose(monkeypatch, nodes[:64], nodes[:64], [], nodes=nodes)
    assert len(result["full_n12_failed_nodeids"]) == 64
    monkeypatch.setattr(diagnostic, "subject_identity", lambda _repository: SUBJECT)

    def runner(_repository, _root, *, label, workers, collect_only, nodeids):
        value = _observation(label, workers, nodes if not nodeids else list(nodeids),
                             nodes if label == "full-n12" else [], collection_only=collect_only)
        return value["exit_status"], value

    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose(Path.cwd(), runner=runner, loader=lambda: {})


def test_observer_marks_65th_distinct_identity_over_bound() -> None:
    plugin._failures.clear()
    plugin._overflow = False
    try:
        for index in range(65):
            plugin._record(_failure(f"tests/test_example.py::test_{index}", "gw0"))
        assert len({fact["nodeid"] for fact in plugin._failures}) == 64
        assert plugin._overflow is True
    finally:
        plugin._failures.clear()
        plugin._overflow = False


@pytest.mark.parametrize("defect", ["raw-message", "unsafe-path", "duplicate", "missing-execution", "over-bound"])
def test_unsafe_or_inconsistent_observation_fails_closed(monkeypatch: pytest.MonkeyPatch, defect: str) -> None:
    monkeypatch.setattr(diagnostic, "subject_identity", lambda _repository: SUBJECT)

    def runner(_repository, _root, *, label, workers, collect_only, nodeids):
        value = _observation(label, workers, NODES, [NODES[0]] if label == "full-n12" else [],
                             collection_only=collect_only)
        if label == "full-n12":
            if defect == "raw-message":
                value["failures"][0]["cause"]["message"] = "credential=secret"
            elif defect == "unsafe-path":
                value["failures"][0]["nodeid"] = "C:/private/test.py::test_secret"
            elif defect == "duplicate":
                value["failures"].append(value["failures"][0])
            elif defect == "missing-execution":
                value["executed_nodeids"].pop()
            else:
                value["over_bound"] = True
        return value["exit_status"], value

    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose(Path.cwd(), runner=runner, loader=lambda: {})


def test_subject_mutation_stops_before_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    checks = 0
    calls = []

    def subject(_repository):
        nonlocal checks
        checks += 1
        return SUBJECT if checks < 6 else {**SUBJECT, "head_sha": "b" * 40}

    monkeypatch.setattr(diagnostic, "subject_identity", subject)

    def runner(_repository, _root, *, label, workers, collect_only, nodeids):
        calls.append(label)
        value = _observation(label, workers, NODES, [NODES[0]] if label == "full-n12" else [],
                             collection_only=collect_only)
        return value["exit_status"], value

    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose(Path.cwd(), runner=runner, loader=lambda: {})
    assert calls == ["collection", "full-n12"]


def test_arguments_rejected_before_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostic, "diagnose", lambda *_args: pytest.fail("execution started"))
    assert diagnostic.main(["--workers", "4"]) == 2
    assert diagnostic.main([NODES[0]]) == 2


def test_fixed_pytest_commands_and_profile_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(diagnostic.subprocess, "run", run)
    monkeypatch.setattr(diagnostic, "_read", lambda _path: {})
    for label, workers, collect_only, nodes in [
        ("collection", 1, True, ()), ("full-n12", 12, False, ()),
        ("serial", 1, False, (NODES[0],)), ("n4", 4, False, (NODES[0],)),
    ]:
        diagnostic.run_pytest(Path.cwd(), tmp_path, label=label, workers=workers,
                              collect_only=collect_only, nodeids=nodes)
    assert len(calls) == 4
    assert "--collect-only" in calls[0][0]
    assert calls[1][0][-6:] == ["-n", "12", "--dist", "load", "--max-worker-restart", "0"]
    assert calls[2][0][-1:] == [NODES[0]]
    assert calls[3][0][-7:] == ["-n", "4", "--dist", "load", "--max-worker-restart", "0", NODES[0]]
    assert len({env["TMP"] for _, env in calls}) == 4
