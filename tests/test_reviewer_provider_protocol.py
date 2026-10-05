"""Focused BP6-P3 protocol examples; Runtime owns canonical verification."""

from __future__ import annotations

from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path

import pytest

from aios_renew.brain_context import compose_brain_work_context, resolve_flow
from aios_renew.brain_sync import BrainSyncSnapshot
from aios_renew.decision_packet import compile_decision_packet
from aios_renew.reviewer_procedure import select_reviewer_procedure
from aios_renew.reviewer_return_contract import select_reviewer_return_contract
from aios_renew.reviewer_provider_protocol import (
    ReviewerProviderProtocolError, construct_request, revalidate_decision,
    revalidate_request, validate_response,
)


ROOT = Path(__file__).resolve().parents[1]
A, B, C = "a" * 40, "b" * 40, "c" * 40
LEGACY_AFFINITY = {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}
ORIGIN_AFFINITY = {"kind": "ORIGIN_AFFINE", "route_handle": "page-origin-v1:" + "d" * 64,
                   "generation": 7}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def task(affinity=None):
    value = {
        "task_id": "TASK-188", "revision": 1, "goal": "Review", "problem": "Review needed",
        "assumptions": [], "scope": {"inspect": [], "modify": ["src/example.py"]},
        "non_goals": [], "constraints": {"hard": []},
        "acceptance": [{"id": "AC1", "condition": "One"},
                       {"id": "AC2", "condition": "Two"}],
        "verification": {"required": ["pytest tests/test_example.py"]},
    }
    if affinity is not None:
        value["return_affinity"] = deepcopy(affinity)
    return value


def packet(affinity=None):
    snapshot = BrainSyncSnapshot(
        repository={"root": str(ROOT), "name": "AIOS-renew", "main_sha": B},
        main_sha=B, roadmap={"next_items": []}, selection_status="SELECTED",
        lifecycle_state="ACTIVE", next_action="SEMANTIC_REVIEW", authority="BRAIN",
        selected_task={"id": "TASK-188", "revision": 1},
        unified_state={"next_action": "SEMANTIC_REVIEW", "run_id": "RUN-188-001",
                       "candidate_sha": A}, blocker=None,
    )
    context = compose_brain_work_context(snapshot, None)
    material = {
        "kind": "SEMANTIC_REVIEW", "task": task(affinity),
        "run": {"run_id": "RUN-188-001", "task": {"id": "TASK-188", "revision": 1},
                "executor": "codex", "base_sha": B, "workspace": "C:/work",
                "head_sha": A, "status": "COMPLETE"},
        "result": {"head_sha": A, "claims": [{"id": "C1", "satisfies": ["AC1"],
                   "claim": "Implemented", "evidence": ["E1"]}],
                   "changed_files": ["src/example.py"], "unresolved": []},
        "evidence": [{"evidence_id": "E1", "run_id": "RUN-188-001",
                      "subject_sha": A, "type": "TEST",
                      "source": {"command": "pytest tests/test_example.py"},
                      "result": {"exit_code": 0, "summary": "passed"},
                      "raw": {"path": "C:/runtime/evidence.txt"}}],
    }
    if affinity is not None:
        material["run"]["return_affinity"] = deepcopy(affinity)
    return compile_decision_packet(context, resolve_flow(context), material)


def scope(mode="PRIMARY"):
    body = {
        "format": "AIOS_SEMANTIC_REVIEW_SCOPE", "version": 1,
        "kind": "SEMANTIC_REVIEW_SCOPE", "task": {"id": "TASK-188", "revision": 1},
        "reviewed_run_id": "RUN-188-001", "review_mode": mode,
        "semantic_origin_run_id": "RUN-188-001", "semantic_base_sha": B,
        "latest_delta_base_sha": B, "reviewed_head_sha": A,
        "prior_review_run_id": "RUN-187-001" if mode == "DELTA" else None,
        "prior_review_id": "REVIEW-187-001" if mode == "DELTA" else None,
        "prior_finding_id": "F-OLD" if mode == "DELTA" else None,
    }
    body["scope_fingerprint"] = digest(body)
    return body


def material(scope_value):
    body = {
        "format": "AIOS_REVIEW_MATERIAL_PACKAGE", "version": 1,
        "kind": "REVIEW_MATERIAL_PACKAGE",
        "review_scope_fingerprint": scope_value["scope_fingerprint"],
        "review_mode": scope_value["review_mode"],
        "semantic_base_sha": B, "latest_delta_base_sha": B,
        "reviewed_head_sha": A,
        "semantic_view": {"base_sha": B, "head_sha": A, "changes": []},
        "latest_delta_view": None, "sources": [],
    }
    body["package_fingerprint"] = digest(body)
    return body


def prior():
    return {
        "review_id": "REVIEW-187-001", "reviewed_sha": B, "mode": "PRIMARY",
        "verdict": "CHANGES_REQUIRED", "acceptance": {"AC1": "FAIL", "AC2": "PASS"},
        "findings": [{"id": "F-OLD", "basis": "AC1", "action": "CODE_FIX",
                      "location": "src/example.py", "issue": "Prior issue",
                      "expected": "Prior expected"}],
    }


def request(mode="PRIMARY", *, affinity=None):
    selected_scope = scope(mode)
    procedure = select_reviewer_procedure(
        (ROOT / ".ai/reviewer-procedure-profiles.yaml").read_bytes(), mode)
    returns = select_reviewer_return_contract(
        (ROOT / ".ai/reviewer-return-contracts.yaml").read_bytes())
    return construct_request(
        packet(affinity), selected_scope, material(selected_scope), procedure, returns,
        {"review_id": "REVIEW-188-001",
         "finding_id_slots": [f"F-{i:02d}" for i in range(32)]},
        prior_review=prior() if mode == "DELTA" else None,
    )


def finding(basis="AC1", **changes):
    return {"basis": basis, "action": "CODE_FIX", "location": "src/example.py",
            "issue": "Issue", "expected": "Expected", **changes}


def body(verdict="PASS", *, fail=(), findings=(), delta=False):
    return {"verdict": verdict,
            "acceptance": [{"id": name, "outcome": "FAIL" if name in fail else "PASS"}
                           for name in (("AC1",) if delta else ("AC1", "AC2"))],
            "findings": list(findings)}


def decision(req, semantic):
    return validate_response(req, {"request_fingerprint": req["request_fingerprint"],
                                   "semantic_body": semantic})


@pytest.mark.parametrize("verdict,fail,findings", [
    ("PASS", (), ()),
    ("CHANGES_REQUIRED", ("AC1",), (finding(),)),
    ("BLOCKED", (), ()),
])
def test_primary_decision_round_trip(verdict, fail, findings):
    req = request()
    assert revalidate_request(json.dumps(req)) == req
    made = decision(req, body(verdict, fail=fail, findings=findings))
    assert revalidate_decision(json.dumps(made), req) == made
    assert made["review_candidate"]["review_id"] == "REVIEW-188-001"
    assert made["review_candidate"]["reviewed_sha"] == A
    assert [f["id"] for f in made["review_candidate"]["findings"]] == (["F-00"] if findings else [])


@pytest.mark.parametrize("mode", ["PRIMARY", "DELTA"])
@pytest.mark.parametrize("affinity", [LEGACY_AFFINITY, ORIGIN_AFFINITY], ids=["legacy", "origin"])
def test_task_affinity_is_exact_closed_context_only(mode, affinity):
    req = request(mode, affinity=affinity)
    before = deepcopy(req)
    assert req["decision_packet"]["canonical_facts"]["task_contract"]["return_affinity"] == affinity
    assert revalidate_request(json.dumps(req)) == req
    made = decision(req, body(delta=mode == "DELTA"))
    assert revalidate_decision(made, req) == made
    assert req == before
    assert "return_affinity" not in made["review_candidate"]


@pytest.mark.parametrize("affinity", [
    None, {}, {"kind": "UNKNOWN"}, {**LEGACY_AFFINITY, "generation": 1},
    {"kind": "ORIGIN_AFFINE"}, {**ORIGIN_AFFINITY, "generation": True},
    {**ORIGIN_AFFINITY, "generation": 0}, {**ORIGIN_AFFINITY, "generation": 2147483648},
    {**ORIGIN_AFFINITY, "route_handle": "page-origin-v1:" + "D" * 64},
    {**ORIGIN_AFFINITY, "chat_url": "https://example.invalid/chat"},
])
def test_malformed_projected_task_affinity_fails_after_rehash(affinity):
    req = request(affinity=ORIGIN_AFFINITY)
    changed = deepcopy(req)
    packet_value = changed["decision_packet"]
    packet_value["canonical_facts"]["task_contract"]["return_affinity"] = affinity
    packet_value["packet_fingerprint"] = digest({
        k: v for k, v in packet_value.items() if k != "packet_fingerprint"})
    changed["request_fingerprint"] = digest({k: v for k, v in changed.items()
                                              if k != "request_fingerprint"})
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_request(changed)


def test_missing_projected_affinity_cannot_default_and_changed_affinity_cannot_replay():
    req = request(affinity=ORIGIN_AFFINITY)
    made = decision(req, body())
    missing = deepcopy(req)
    packet_value = missing["decision_packet"]
    del packet_value["canonical_facts"]["task_contract"]["return_affinity"]
    packet_value["packet_fingerprint"] = digest({
        k: v for k, v in packet_value.items() if k != "packet_fingerprint"})
    missing["request_fingerprint"] = digest({k: v for k, v in missing.items()
                                              if k != "request_fingerprint"})
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_request(missing)
    changed = request(affinity={**ORIGIN_AFFINITY, "generation": 8})
    assert revalidate_request(changed) == changed
    assert changed["request_fingerprint"] != req["request_fingerprint"]
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_decision(made, changed)


def test_delta_exact_prior_finding_and_text():
    req = request("DELTA")
    assert req["prior_review"]["findings"][0]["issue"] == "Prior issue"
    made = decision(req, body("PASS", delta=True))
    assert made["review_candidate"]["prior_finding_id"] == "F-OLD"
    assert revalidate_decision(made, req) == made
    with pytest.raises(ReviewerProviderProtocolError):
        decision(req, {"verdict": "PASS", "acceptance": [{"id": "AC2", "outcome": "PASS"}],
                       "findings": []})
    prose_changed = deepcopy(req)
    prose_changed["prior_review"]["findings"][0]["issue"] = "Different prior issue"
    prose_changed["request_fingerprint"] = digest({
        k: v for k, v in prose_changed.items() if k != "request_fingerprint"})
    assert revalidate_request(prose_changed) == prose_changed
    assert prose_changed["request_fingerprint"] != req["request_fingerprint"]
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_decision(made, prose_changed)
    too_large = deepcopy(req)
    too_large["prior_review"]["findings"][0]["issue"] = "x" * 196609
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_request(too_large)
    for field, value in (("review_id", "other"), ("reviewed_sha", C)):
        changed = deepcopy(req)
        changed["prior_review"][field] = value
        changed["request_fingerprint"] = digest({k: v for k, v in changed.items()
                                                  if k != "request_fingerprint"})
        with pytest.raises(ReviewerProviderProtocolError):
            revalidate_request(changed)


def test_all_finding_slots_keep_order_and_identity():
    req = request()
    findings = [finding(issue=f"Issue {i}") for i in range(32)]
    made = decision(req, body("CHANGES_REQUIRED", fail=("AC1",), findings=findings))
    assert [f["id"] for f in made["review_candidate"]["findings"]] == req["external_bindings"]["finding_id_slots"]
    assert [f["issue"] for f in made["review_candidate"]["findings"]] == [f"Issue {i}" for i in range(32)]
    assert revalidate_decision(made, req) == made
    changed = deepcopy(req)
    changed["external_bindings"]["finding_id_slots"][31] = "F-OTHER"
    changed["request_fingerprint"] = digest({k: v for k, v in changed.items()
                                              if k != "request_fingerprint"})
    assert revalidate_request(changed) == changed
    assert changed["request_fingerprint"] != req["request_fingerprint"]
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_decision(made, changed)


@pytest.mark.parametrize("field,path,value", [
    ("decision_packet", ("subject", "head_sha"), C),
    ("review_scope", ("reviewed_head_sha",), C),
    ("review_material_package", ("review_mode",), "DELTA"),
    ("reviewer_procedure_package", ("procedure", "mode"), "DELTA"),
    ("reviewer_return_contract_package", ("contract", "id"), "substitute"),
    ("external_bindings", ("review_id",), "REVIEW-OTHER"),
])
def test_request_substitution_fails_even_if_outer_fingerprint_recomputed(field, path, value):
    req = request()
    changed = deepcopy(req)
    node = changed[field]
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    changed["request_fingerprint"] = digest({k: v for k, v in changed.items()
                                              if k != "request_fingerprint"})
    if field == "external_bindings":
        assert revalidate_request(changed) == changed
        with pytest.raises(ReviewerProviderProtocolError):
            revalidate_decision(decision(req, body()), changed)
    else:
        with pytest.raises(ReviewerProviderProtocolError):
            revalidate_request(changed)


def test_response_echo_smuggling_and_round_trip_drift():
    req = request()
    with pytest.raises(ReviewerProviderProtocolError):
        validate_response(req, {"request_fingerprint": "0" * 64, "semantic_body": body()})
    with pytest.raises(ReviewerProviderProtocolError):
        decision(req, {**body(), "provider_id": "hidden"})
    with pytest.raises(ReviewerProviderProtocolError):
        decision(req, body("CHANGES_REQUIRED", fail=("AC1",),
                           findings=[finding(issue=" issue ")]))
    preserved = decision(req, body("CHANGES_REQUIRED", fail=("AC1",),
                                   findings=[finding(issue="line\r\nnext")]))
    assert preserved["review_candidate"]["findings"][0]["issue"] == "line\r\nnext"


def test_strict_json_bounds_fingerprints_and_purity():
    req = request()
    assert revalidate_request({key: req[key] for key in reversed(req)}) == req
    assert request()["request_fingerprint"] == req["request_fingerprint"]
    made = decision(req, body())
    assert made["decision_fingerprint"] == decision(req, body())["decision_fingerprint"]
    bad = deepcopy(made)
    bad["review_candidate"]["review_id"] = "other"
    bad["decision_fingerprint"] = digest({k: v for k, v in bad.items()
                                           if k != "decision_fingerprint"})
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_decision(bad, req)
    for invalid in (
        '{"request_fingerprint":"x","request_fingerprint":"x","semantic_body":{}}',
        {"request_fingerprint": req["request_fingerprint"], "semantic_body": body(), "extra": 1},
        {"request_fingerprint": req["request_fingerprint"], "semantic_body": body(), "model": "x"},
        {"request_fingerprint": req["request_fingerprint"], "semantic_body": {**body(), "issue": float("nan")}},
        {"request_fingerprint": req["request_fingerprint"], "semantic_body": {**body(), "issue": "\ud800"}},
        {"request_fingerprint": req["request_fingerprint"], "semantic_body": {**body(), "issue": [0] * 200000}},
        {"request_fingerprint": req["request_fingerprint"], "semantic_body": {**body(), "issue": [[[[0]]]]}},
    ):
        with pytest.raises(ReviewerProviderProtocolError):
            validate_response(req, invalid)
    deep = 0
    for _ in range(34):
        deep = [deep]
    with pytest.raises(ReviewerProviderProtocolError):
        validate_response(req, {"request_fingerprint": req["request_fingerprint"], "semantic_body": deep})
    assert not any(word in str(made).lower() for word in ("provider_id", "session_id", "publisher"))


def test_packet_result_and_prior_are_the_only_validation_context():
    req = request("DELTA")
    assert req["decision_packet"]["canonical_facts"]["task_contract"]["task_id"] == "TASK-188"
    assert req["decision_packet"]["executor_claims"][0]["evidence_ids"] == ["E1"]
    assert "result" not in req and "task" not in req
    assert decision(req, body("PASS", delta=True))["review_candidate"]["acceptance"] == {"AC1": "PASS"}
    for field, replacement in (("task_contract", {"task_id": "wrong"}),):
        changed = deepcopy(req)
        changed["decision_packet"]["canonical_facts"][field] = replacement
        changed["decision_packet"]["packet_fingerprint"] = digest({
            k: v for k, v in changed["decision_packet"].items() if k != "packet_fingerprint"})
        changed["request_fingerprint"] = digest({k: v for k, v in changed.items()
                                                  if k != "request_fingerprint"})
        with pytest.raises(ReviewerProviderProtocolError):
            revalidate_request(changed)
    changed = deepcopy(req)
    changed["decision_packet"]["executor_claims"][0]["evidence_ids"] = ["missing"]
    changed["decision_packet"]["packet_fingerprint"] = digest({
        k: v for k, v in changed["decision_packet"].items() if k != "packet_fingerprint"})
    changed["request_fingerprint"] = digest({k: v for k, v in changed.items()
                                              if k != "request_fingerprint"})
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_request(changed)


def test_request_and_identity_failures_are_protocol_errors():
    req = request()
    for edit in (
        lambda r: r["external_bindings"]["finding_id_slots"].__setitem__(1, "F-00"),
        lambda r: r["external_bindings"].__setitem__("review_id", " padded "),
        lambda r: r["external_bindings"].__setitem__("review_id", "x" * 257),
        lambda r: r.__setitem__("model_id", "local"),
        lambda r: r["review_scope"].__setitem__("unknown", 1),
        lambda r: r["prior_review"].__setitem__("review_id", "wrong") if r["prior_review"] else r.__setitem__("prior_review", prior()),
    ):
        changed = deepcopy(req)
        edit(changed)
        changed["request_fingerprint"] = digest({k: v for k, v in changed.items()
                                                  if k != "request_fingerprint"})
        with pytest.raises(ReviewerProviderProtocolError):
            revalidate_request(changed)
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_request('{"version":1,"version":1}')
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_request({**req, "extra": object()})
    changed = deepcopy(req)
    changed["external_bindings"]["review_id"] = "\ud800"
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_request(changed)
    changed = deepcopy(req)
    changed["external_bindings"]["review_id"] = "x" * 2100000
    with pytest.raises(ReviewerProviderProtocolError):
        revalidate_request(changed)


def test_no_provider_or_lifecycle_authority_imports():
    source = (ROOT / "src/aios_renew/reviewer_provider_protocol.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imports & {
        "brain_provider_protocol", "brain_audit", "brain_return_contract",
        "authoring_ingress", "publication", "operator", "runtime",
        "review_transport", "provider_adapter",
    }
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {"open", "exec", "eval"} for node in ast.walk(tree))
