"""Contract regressions for the bounded stable-failure cause probe."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts import aios_stable_failure_cause_diagnostic as diagnostic
from tests import aios_stable_failure_probe_plugin as plugin


class AuthoringIngressError(ValueError):
    pass


class OperatorError(RuntimeError):
    pass


@pytest.mark.parametrize(("exception", "category"), [
    (AuthoringIngressError("canonical review decision missing for source run RUN-1"), "CANONICAL_REVIEW_DECISION_MISSING"),
    (AuthoringIngressError("expected reviewed SHA mismatch: expected a, got b"), "EXPECTED_REVIEWED_SHA_MISMATCH"),
    (AuthoringIngressError("finding F-1 is already resolved, superseded, or otherwise no longer outstanding"), "FINDING_NOT_OUTSTANDING"),
    (AuthoringIngressError("Git command failed: private path"), "GIT_COMMAND_FAILED"),
    (OperatorError("source-REPAIR bootstrap target is not activated"), "SOURCE_REPAIR_TARGET_NOT_ACTIVATED"),
    (OperatorError("source-REPAIR bootstrap legacy generation mismatch"), "SOURCE_REPAIR_LEGACY_GENERATION_MISMATCH"),
    (OperatorError("source-REPAIR bootstrap lineage identity mismatch"), "SOURCE_REPAIR_LINEAGE_MISMATCH"),
    (OperatorError("source-REPAIR bootstrap target source mismatch"), "SOURCE_REPAIR_TARGET_SOURCE_MISMATCH"),
    (OperatorError("source-REPAIR bootstrap transport identity mismatch"), "SOURCE_REPAIR_TRANSPORT_IDENTITY_MISMATCH"),
])
def test_source_guard_mapping(exception, category):
    assert plugin.guard_category(exception) == category


def test_unknown_and_cause_chain_disclose_no_message_or_private_path():
    private = r"C:\Users\Private\secret-token.txt"
    cause = OSError(5, private)
    outer = OperatorError("unmapped " + private)
    outer.__cause__ = cause
    fact = plugin.cause(outer, None, diagnostic.TARGETS[1])
    assert fact["guard_category"] == "UNKNOWN"
    assert fact["source_locus"] is None and fact["integer_facts"] == {}
    assert fact["cause_chain"] == [{"exception_type": "OSError", "integer_facts": {}}]
    assert private not in json.dumps(fact)
    assert fact["message_fingerprint"] == "sha256:" + hashlib.sha256(str(outer).encode()).hexdigest()


def _path_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()


def _observation(root: Path, label: str, pid: int, *, fail: bool = False, cache: str | None = None):
    targets = list(diagnostic.TARGETS if label == "collection" else (diagnostic.TARGETS[int(label[-1])],))
    fact = {"exception_type": "OperatorError", "message_fingerprint": "sha256:" + "a" * 64,
            "source_locus": None, "cause_chain": [], "integer_facts": {},
            "guard_category": "SOURCE_REPAIR_TARGET_NOT_ACTIVATED"}
    return {"schema": plugin.SCHEMA, "version": 1, "exit_status": int(fail),
            "collection": targets, "executed": [] if label == "collection" else targets,
            "failures": [{"nodeid": targets[0], "phase": "call", "cause": fact}] if fail else [],
            "process": {"pid": pid, "basetemp": _path_hash(root / label / "pytest"),
                        "cache": cache or _path_hash(root / label / "aios-git-fixtures-unique"),
                        "cache_in_profile": True}}


def _run(*, mutate=None):
    calls = []
    identities = iter([{"head_sha": "a" * 40, "worktree_clean": True}] * 10)

    def runner(repo, root, *, label, selectors):
        calls.append((label, selectors, root))
        value = _observation(root, label, len(calls) + 100, fail=label == "target-1")
        if mutate:
            mutate(value, label, calls)
        return value["exit_status"], value

    return runner, calls, lambda repo: next(identities)


def test_exact_three_target_binding_and_aggregate(tmp_path):
    runner, calls, identity = _run()
    result = diagnostic.diagnose(tmp_path, runner=runner, identity=identity)
    assert [item[0] for item in calls] == ["collection", "target-0", "target-1", "target-2"]
    assert calls[0][1] == diagnostic.TARGETS
    assert [item[1] for item in calls[1:]] == [(node,) for node in diagnostic.TARGETS]
    assert len({item[2] for item in calls}) == 1
    assert len(result["targets"]) == 3 and result["classification"] == "KNOWN_GUARDS"


@pytest.mark.parametrize("mutation", [
    lambda value: value["collection"].append(value["collection"][0]),
    lambda value: value["executed"].append(diagnostic.TARGETS[0]),
    lambda value: value["process"].update(pid=101),
    lambda value: value["process"].update(basetemp="sha256:" + "0" * 64),
    lambda value: value["process"].update(cache_in_profile=False),
])
def test_duplicate_or_nonisolated_observation_fails(tmp_path, mutation):
    def change(value, label, calls):
        if label == "target-0":
            mutation(value)
    runner, _, identity = _run(mutate=change)
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose(tmp_path, runner=runner, identity=identity)


def test_reused_cache_root_fails(tmp_path):
    def change(value, label, calls):
        if label == "target-0":
            value["process"]["cache"] = _path_hash(calls[0][2] / "collection" / "aios-git-fixtures-unique")
    runner, _, identity = _run(mutate=change)
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose(tmp_path, runner=runner, identity=identity)


def test_duplicate_json_key_is_rejected(tmp_path):
    path = tmp_path / "observation.json"
    path.write_text('{"schema":"first","schema":"second"}', encoding="utf-8")
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._read(path)


def test_subject_mutation_stops_before_next_target(tmp_path):
    runner, calls, _ = _run()
    count = 0

    def identity(repo):
        nonlocal count
        count += 1
        return {"head_sha": ("b" if count >= 4 else "a") * 40, "worktree_clean": True}

    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose(tmp_path, runner=runner, identity=identity)
    assert [item[0] for item in calls] == ["collection"]


def test_dirty_subject_stops_after_collection(tmp_path):
    runner, calls, _ = _run()
    count = 0

    def identity(repo):
        nonlocal count
        count += 1
        return {"head_sha": "a" * 40, "worktree_clean": count < 4}

    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose(tmp_path, runner=runner, identity=identity)
    assert [item[0] for item in calls] == ["collection"]


def test_argument_rejection_precedes_probe(monkeypatch):
    monkeypatch.setattr(diagnostic, "diagnose", lambda repo: pytest.fail("probe ran"))
    assert diagnostic.main(["--anything"]) == 2
    assert diagnostic.main(["unexpected"]) == 2
