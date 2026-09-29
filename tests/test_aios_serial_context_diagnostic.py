"""Implementation contract checks for the fixed serial context diagnostic."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import aios_serial_context_diagnostic as diagnostic


SUBJECT = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
OTHER = "tests/test_operator.py::test_other"


def _nodes() -> list[str]:
    # Parameter data intentionally contains characters that may be used for
    # exact execution but must never enter the public diagnostic result.
    nodes = [f"tests/test_operator.py::test_case[secret://private/{index}]"
             for index in range(34)]
    nodes.append('tests/test_hot_swap_conformance.py::test_case["private path"]')
    return nodes[::-1]


def _path(value: str) -> dict[str, object]:
    return {"path": value, "length": 80}


def _observation(label: str, nodes: list[str], failures: list[str]) -> dict:
    return {
        "schema": diagnostic.SCHEMA, "version": 1, "exit_status": int(bool(failures)),
        "collection": nodes,
        "nodeid_fingerprints": {node: diagnostic._fingerprint(node) for node in nodes},
        "executed_nodeids": [] if label == "collection" else nodes[:],
        "failures": [{"nodeid": node, "phase": "call", "cause": {
            "exception_type": "AssertionError", "message_fingerprint": "sha256:" + "b" * 64,
            "source_locus": {"path": "<subject>/" + node.split("::", 1)[0], "line": 42},
        }} for node in failures],
        "over_bound": False,
        "process": {"process_id": {"collection": 101, "phase-a": 102, "phase-b": 103}[label],
                    "temporary_root": _path("<pytest_basetemp>"),
                    "git_fixture_cache_root": _path(f"<diagnostic_temp>/{label}/aios-git-fixtures")},
    }


def _experiment(monkeypatch: pytest.MonkeyPatch, *, a_failed: tuple[int, ...] = (),
                b_failed: tuple[int, ...] = (), other_failed: bool = False,
                defect: str | None = None):
    nodes = _nodes()
    monkeypatch.setattr(diagnostic, "TARGETS", tuple(diagnostic._safe_nodeid(node) for node in nodes))
    monkeypatch.setattr(diagnostic, "TARGETS_DIGEST", hashlib.sha256(
        "".join(identity + "\n" for identity in diagnostic.TARGETS).encode()).hexdigest())
    monkeypatch.setattr(diagnostic, "subject_identity", lambda _repository: SUBJECT)
    calls = []

    def runner(_repository, _root, *, label, selectors):
        calls.append((label, selectors))
        if label == "collection":
            selected = [OTHER] + nodes
            failed = []
        elif label == "phase-a":
            selected = list(selectors)
            failed = [nodes[index] for index in a_failed]
        else:
            selected = [OTHER] + nodes
            failed = [nodes[index] for index in b_failed] + ([OTHER] if other_failed else [])
        value = _observation(label, selected, failed)
        if defect == "missing-collection" and label == "collection":
            value["collection"].remove(nodes[0])
            value["nodeid_fingerprints"].pop(nodes[0])
        elif defect == "missing-file-target" and label == "phase-b":
            value["collection"].remove(nodes[0])
            value["executed_nodeids"].remove(nodes[0])
            value["nodeid_fingerprints"].pop(nodes[0])
        elif defect == "duplicate-target" and label == "phase-b":
            value["collection"].append(nodes[0])
        elif defect == "missing-execution" and label == "phase-b":
            value["executed_nodeids"].remove(nodes[0])
        elif defect == "raw-cause" and label == "phase-a":
            value["failures"][0]["cause"]["message"] = "credential=secret"
        elif defect == "overflow" and label == "phase-b":
            value["over_bound"] = True
        elif defect == "unsafe-node" and label == "phase-b":
            value["collection"][0] = "C:/private/test.py::test_unsafe"
        return value["exit_status"], value

    return diagnostic.diagnose(Path.cwd(), runner=runner), calls, nodes


def test_fixed_binding_is_exact_published_35() -> None:
    assert len(diagnostic.TARGETS) == len(set(diagnostic.TARGETS)) == 35
    assert diagnostic._targets() == set(diagnostic.TARGETS)
    assert hashlib.sha256("".join(item + "\n" for item in diagnostic.TARGETS).encode()).hexdigest() == (
        "dee57adf8d0f606bee41eed302dbd2a961af2b3fff52e631df018a578df82fad"
    )
    assert sum(item.startswith("tests/test_hot_swap_conformance.py::") for item in diagnostic.TARGETS) == 1
    assert sum(item.startswith("tests/test_operator.py::") for item in diagnostic.TARGETS) == 34


@pytest.mark.parametrize(("a_failed", "b_failed", "classification"), [
    ((), (), "COLD_TARGET_PASS"),
    ((0,), (), "FILE_CONTEXT_RESOLVES"),
    ((0, 1), (0, 1), "SERIAL_STABLE_FAILURE"),
    ((0, 1), (1, 2), "PARTIAL_FILE_CONTEXT_EFFECT"),
])
def test_classification_and_canonical_phase_order(monkeypatch: pytest.MonkeyPatch,
                                                   a_failed: tuple[int, ...], b_failed: tuple[int, ...],
                                                   classification: str) -> None:
    result, calls, nodes = _experiment(monkeypatch, a_failed=a_failed, b_failed=b_failed,
                                       other_failed=True)
    assert result["classification"] == classification
    assert calls == [("collection", ()), ("phase-a", tuple(nodes)), ("phase-b", diagnostic.FILES)]
    assert result["phase_a_target_failed_nodeids"] == sorted(diagnostic._safe_nodeid(nodes[i]) for i in a_failed)
    assert result["phase_b_target_failed_nodeids"] == sorted(diagnostic._safe_nodeid(nodes[i]) for i in b_failed)
    assert result["phase_b_non_target_failed_nodeids"] == [diagnostic._safe_nodeid(OTHER)]
    assert result["phase_b_non_target_failure_count"] == 1
    assert result["target_count"] == 35
    durable = json.dumps(result)
    assert all(node not in durable for node in nodes)
    assert "secret://private" not in durable
    assert "private path" not in durable


@pytest.mark.parametrize("defect", ["missing-collection", "missing-file-target", "duplicate-target", "missing-execution",
                                         "raw-cause", "overflow", "unsafe-node"])
def test_inconsistent_or_unsafe_observation_fails_closed(monkeypatch: pytest.MonkeyPatch,
                                                          defect: str) -> None:
    with pytest.raises(diagnostic.DiagnosticError):
        _experiment(monkeypatch, a_failed=(0,), defect=defect)


def test_subject_mutation_stops_before_second_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = _nodes()
    monkeypatch.setattr(diagnostic, "TARGETS", tuple(diagnostic._safe_nodeid(node) for node in nodes))
    monkeypatch.setattr(diagnostic, "TARGETS_DIGEST", hashlib.sha256(
        "".join(identity + "\n" for identity in diagnostic.TARGETS).encode()).hexdigest())
    checks = 0
    calls = []

    def subject(_repository):
        nonlocal checks
        checks += 1
        return SUBJECT if checks < 5 else {**SUBJECT, "head_sha": "c" * 40}

    monkeypatch.setattr(diagnostic, "subject_identity", subject)

    def runner(_repository, _root, *, label, selectors):
        calls.append(label)
        selected = nodes if label == "collection" else list(selectors)
        value = _observation(label, selected, [])
        return 0, value

    with pytest.raises(diagnostic.DiagnosticError, match="subject changed"):
        diagnostic.diagnose(Path.cwd(), runner=runner)
    assert calls == ["collection", "phase-a"]


def test_hard_128_unique_failure_bound() -> None:
    nodes = [f"tests/test_operator.py::test_{index}" for index in range(129)]
    value = _observation("phase-b", nodes, nodes)
    with pytest.raises(diagnostic.DiagnosticError, match="bound"):
        diagnostic._phase("phase-b", 1, value, None)


def test_argument_rejection_precedes_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostic, "diagnose", lambda *_args: pytest.fail("execution began"))
    assert diagnostic.main(["--workers", "4"]) == 2
    assert diagnostic.main(["tests/test_operator.py"]) == 2


def test_fixed_subprocess_commands_and_isolated_roots(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(diagnostic.subprocess, "run", run)
    monkeypatch.setattr(diagnostic, "_read", lambda _path: {})
    targets = tuple(_nodes())
    for label, selectors in [("collection", ()), ("phase-a", targets),
                             ("phase-b", diagnostic.FILES)]:
        diagnostic.run_pytest(Path.cwd(), tmp_path, label=label, selectors=selectors)
    assert "--collect-only" in calls[0][0]
    assert calls[1][0][-35:] == list(targets)
    assert calls[2][0][-2:] == list(diagnostic.FILES)
    assert all("-n" not in command for command, _env in calls)
    assert len({env["TMP"] for _command, env in calls}) == 3
