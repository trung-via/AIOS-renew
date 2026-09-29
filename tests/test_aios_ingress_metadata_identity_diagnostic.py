"""Fixed same-repository control and local contracts for the identity probe."""

from copy import deepcopy

from aios_renew.authoring_ingress import IngressEnvelope, execute_ingress
from scripts import aios_ingress_metadata_identity_diagnostic as diagnostic
from test_authoring_ingress import setup_candidate_lineage


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
    result = execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REMEDIATION",
                                             {"source_run_id": run_id, "finding_id": "F1"},
                                             {"expected_reviewed_sha": candidate_sha}, payload), repo=repo)
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


def test_fixed_command_rejects_arguments_before_execution(monkeypatch):
    monkeypatch.setattr(diagnostic, "diagnose", lambda _repo: (_ for _ in ()).throw(AssertionError("executed")))
    assert diagnostic.main(["--anything"]) == 2
