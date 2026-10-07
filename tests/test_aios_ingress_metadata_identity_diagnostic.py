"""Fixed same-repository control and local contracts for the identity probe."""

import hashlib
import os
from copy import deepcopy

import pytest

from aios_renew.authoring_ingress import AuthoringIngressError, IngressEnvelope, execute_ingress
from scripts import aios_ingress_metadata_identity_diagnostic as diagnostic
from test_authoring_ingress import audited_envelope, git, setup_candidate_lineage


def test_ordinary_author_remediation_control(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    candidate_sha = lineage["candidate_sha"]
    review = f"""review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Defect found.
    expected: Fix defect.
"""
    execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW",
                                    {"run_id": run_id},
                                    {"expected_candidate_sha": candidate_sha}, review), repo=repo)
    payload = f"""finding_id: F1
action: CODE_FIX
reviewed_sha: {candidate_sha}
modification_scope: [src/sample.py]
affected_verification: [git diff --check]
constraints:
  hard: [Bounded mutation authority only.]
"""
    envelope = IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REMEDIATION",
                               {"source_run_id": run_id, "finding_id": "F1"},
                               {"expected_reviewed_sha": candidate_sha}, payload)
    before = (git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"),
              git(repo, "ls-remote", "--refs", "origin"))
    with pytest.raises(AuthoringIngressError, match="requires audited_handoff"):
        execute_ingress(envelope, repo=repo)
    assert (git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"),
            git(repo, "ls-remote", "--refs", "origin")) == before

    result = execute_ingress(audited_envelope(envelope, repo), repo=repo)
    assert result.status == "CANONICALIZED"


def test_classification_uses_bounded_identity_facts():
    byte = {"length": 2, "sha256": "a" * 64,
            "signature": {"bom": False, "crlf": 0, "lf": 1, "cr": 0, "trailing": "LF"}}
    facts = {"expected": byte, "expected_object": "1" * 40, "tree_object": "1" * 40,
             "native": byte, "native_object_valid": True, "readback": byte,
             "read_count": 1, "native_relation": "EXACT", "readback_relation": "EXACT",
             "native_readback_equal": True}
    assert diagnostic.classify(facts, status=0) == "PASS_EXACT"
    changed = deepcopy(facts)
    changed["tree_object"] = "2" * 40
    assert diagnostic.classify(changed, status=1) == "WRITE_OR_TREE_BINDING_MISMATCH"
    changed = deepcopy(facts)
    changed["readback_relation"] = "LINE_ENDING_OR_BOM"
    assert diagnostic.classify(changed, status=1) == "LINE_ENDING_OR_BOM_TRANSFORM"
    changed = deepcopy(facts)
    changed["native_readback_equal"] = False
    changed["readback_relation"] = "OTHER"
    assert diagnostic.classify(changed, status=1) == "PRODUCTION_READBACK_MISMATCH"
    assert diagnostic.classify(None, status=1) == "OBSERVATION_INCOMPLETE"


def test_observed_null_readback_resolves_fresh_clone_boundary(tmp_path, monkeypatch):
    byte = {"length": 2, "sha256": "a" * 64,
            "signature": {"bom": False, "crlf": 0, "lf": 1, "cr": 0, "trailing": "LF"}}
    fresh_facts = {"expected": byte, "expected_object": "1" * 40, "tree_object": "1" * 40,
                   "native": byte, "native_object_valid": True, "readback": None,
                   "read_count": 1, "native_relation": "EXACT", "readback_relation": None,
                   "native_readback_equal": None}
    diagnostic._facts(fresh_facts)
    assert diagnostic.classify(fresh_facts, status=1) == "PRODUCTION_READBACK_MISMATCH"

    unobserved = deepcopy(fresh_facts)
    unobserved["read_count"] = 0
    assert diagnostic.classify(unobserved, status=1) == "OBSERVATION_INCOMPLETE"
    unbound = deepcopy(fresh_facts)
    unbound["tree_object"] = "2" * 40
    assert diagnostic.classify(unbound, status=1) == "OBSERVATION_INCOMPLETE"
    assert diagnostic.classify(fresh_facts, status=0) == "OBSERVATION_INCOMPLETE"

    control_facts = deepcopy(fresh_facts)
    control_facts.update(readback=byte, readback_relation="EXACT", native_readback_equal=True)

    def observed_run(_repo, root, label):
        status = 0 if label == "control" else 1
        profile = root / label
        facts = control_facts if label == "control" else fresh_facts
        target = diagnostic.TARGETS[label]
        observation = {
            "schema": diagnostic.SCHEMA, "version": 1, "status": status,
            "collection": [target], "executed": [target],
            "failures": [] if status == 0 else [{"nodeid": target, "phase": "call",
                "type": "AuthoringIngressError", "message_sha256": "b" * 64}],
            "attempts": [{"delegations": 1, "operation": "AUTHOR_REMEDIATION", "facts": facts}],
            "pid": 100 + status,
            "basetemp_sha256": hashlib.sha256(
                os.path.normcase(str((profile / "pytest").resolve())).encode()).hexdigest(),
            "cache_sha256": ("c" if status == 0 else "d") * 64,
            "cache_in_profile": True,
        }
        return status, observation

    monkeypatch.setattr(diagnostic, "subject_identity", lambda _repo: {
        "kind": "git-commit", "head_sha": "1" * 40, "worktree_clean": True})
    monkeypatch.setattr(diagnostic, "_run", observed_run)
    result = diagnostic.diagnose(tmp_path)
    assert [item["classification"] for item in result["contexts"]] == [
        "PASS_EXACT", "PRODUCTION_READBACK_MISMATCH"]
    assert result["classification"] == "FRESH_CLONE_ONLY_MISMATCH_RESOLVED_BOUNDARY"


def test_fixed_command_rejects_arguments_before_execution(monkeypatch):
    monkeypatch.setattr(diagnostic, "diagnose", lambda _repo: (_ for _ in ()).throw(AssertionError("executed")))
    assert diagnostic.main(["--anything"]) == 2
