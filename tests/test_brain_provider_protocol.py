"""Focused, caller-supplied BP5-P2B protocol conformance examples."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from aios_renew.brain_audit import parse_profile_registry, profile_ref
from aios_renew.brain_provider_protocol import (
    BrainProviderProtocolError, construct_request, revalidate_request,
    revalidate_decision, validate_response,
)
from aios_renew.brain_provider_protocol import _bound, _normal
from aios_renew.brain_return_contract import parse_return_contract_registry, select_return_contract
from aios_renew.brain_return_contract import return_contract_ref
from aios_renew.decision_packet import DecisionPacket
from aios_renew.task import validate_task


ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


@pytest.fixture
def registry():
    return parse_return_contract_registry((ROOT / ".ai/brain-return-contracts.yaml").read_bytes())


@pytest.fixture
def profile_package():
    profile = parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"][0]
    return {"profile": profile, "audit_profile_ref": profile_ref(profile)}


def packet(contract):
    body = {
        "format": "AIOS_DECISION_PACKET", "version": 1, "kind": "DECISION_PACKET",
        "work_context_fingerprint": "a" * 64,
        **{key: contract[key] for key in (
            "selected_flow", "authority_owner", "decision_family_ref", "handoff_target",
            "expected_return_shape")},
        "selection_basis": "EXPLICIT_SELECTOR", "pending_canonical_obligation": None,
        "pending_canonical_authority_owner": None, "requires_fresh_context_for_continuation": True,
        "canonical_facts": {}, "canonical_blocker": None, "bounded_observations": [],
        "executor_claims": None, "prior_semantic_decisions": None, "human_input": None,
        "subject": {"kind": contract["selected_flow"]}, "run_created": False,
        "executor_invoked": False, "verification_invoked": False, "state_mutated": False,
    }
    body["packet_fingerprint"] = digest(body)
    return DecisionPacket(body)


def inputs(registry, flow):
    contract = next(item for item in registry["contracts"] if item["selected_flow"] == flow)
    supplied = packet(contract)
    return supplied, select_return_contract(registry, supplied)


def request(registry, profile_package, flow="TASK_AUTHORING", bindings=None):
    supplied, package = inputs(registry, flow)
    if bindings is None:
        bindings = {"task_id": "TASK-999", "revision": 1} if flow == "TASK_AUTHORING" else {}
    return construct_request(supplied, package, bindings,
                             profile_package if flow != "DIAGNOSTIC" else None,
                             request_mode="DIRECT" if flow == "DIAGNOSTIC" else "AUDIT_CONSTRUCT")


def response(req, candidate):
    return {"request_fingerprint": req["request_fingerprint"], "candidate": candidate}


def stage2_response(req, candidate, *, blocker=False):
    lenses = [item["id"] for item in req["audit_profile_package"]["profile"]["lenses"]]
    closure = [{"lens": lens, "outcome": "CLEAR"} for lens in lenses]
    if blocker:
        closure[-1] = {"lens": lenses[-1], "outcome": "BLOCKER", "blocker_summary": "Open risk"}
    return {
        "request_fingerprint": req["request_fingerprint"],
        "construct_audit": [{"lens": lens, "outcome": "CLEAR"} for lens in lenses],
        "reconciled_candidate": candidate, "closure": closure,
        "outcome": "NO_DECISION" if blocker else "CANDIDATE",
    }


def test_audited_round_trip_and_stale_stage2(registry, profile_package):
    first = request(registry, profile_package)
    assert revalidate_request(first) == first
    candidate = {"task_id": "TASK-999", "revision": 1, "goal": "A bounded task"}
    stage1 = validate_response(first, response(first, candidate))
    assert revalidate_decision(stage1, first) == stage1
    supplied = DecisionPacket(first["decision_packet"])
    second = construct_request(supplied, first["return_contract_package"], first["external_bindings"],
                               first["audit_profile_package"], request_mode="AUDIT_RECONCILE",
                               stage1_decision=stage1)
    assert set(second["stage1_lineage"]) == {
        "stage1_request_fingerprint", "stage1_decision_fingerprint", "construct"}
    assert revalidate_request(second) == second
    for blocker in (False, True):
        final = validate_response(second, stage2_response(second, candidate, blocker=blocker))
        assert revalidate_decision(final, second) == final
        assert (final["semantic_value"]["handoff_candidate"] is None) == blocker
    stale = deepcopy(first["decision_packet"])
    stale["human_input"] = "new subject"
    stale["packet_fingerprint"] = digest({k: v for k, v in stale.items() if k != "packet_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        construct_request(DecisionPacket(stale), first["return_contract_package"],
                          first["external_bindings"], first["audit_profile_package"],
                          request_mode="AUDIT_RECONCILE", stage1_decision=stage1)
    bad = stage2_response(second, {"task_id": "TASK-999", "revision": 2}, blocker=True)
    with pytest.raises(BrainProviderProtocolError):
        validate_response(second, bad)


@pytest.mark.parametrize("tamper", (
    "stage2_fingerprint", "reconciled_candidate_fingerprint", "closure", "construct_audit",
))
def test_no_decision_serialized_stage2_tampering(registry, profile_package, tamper):
    first = request(registry, profile_package)
    candidate = {"task_id": "TASK-999", "revision": 1}
    stage1 = validate_response(first, response(first, candidate))
    second = construct_request(DecisionPacket(first["decision_packet"]),
                               first["return_contract_package"], first["external_bindings"],
                               first["audit_profile_package"], request_mode="AUDIT_RECONCILE",
                               stage1_decision=stage1)
    final = validate_response(second, stage2_response(second, candidate, blocker=True))
    assert revalidate_decision(final, second) == final

    altered = deepcopy(final)
    semantic = altered["semantic_value"]
    if tamper in {"stage2_fingerprint", "reconciled_candidate_fingerprint"}:
        semantic[tamper] = "0" * 64
    elif tamper == "closure":
        semantic["closure"][-1]["blocker_summary"] = "Different open risk"
    else:
        semantic["construct_audit"][0] = {
            "lens": semantic["construct_audit"][0]["lens"], "outcome": "RISK_FOUND",
            "risks": [{"risk_summary": "Change needed", "counterexample": "Current candidate fails",
                       "candidate_anchor": "task_id", "disposition": "ADDRESSED_BY_RECONCILIATION"}],
        }
    altered["decision_fingerprint"] = digest({k: v for k, v in altered.items()
                                               if k != "decision_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(altered, second)


def test_no_decision_changed_candidate_cannot_be_revalidated(registry, profile_package):
    first = request(registry, profile_package)
    candidate = {"task_id": "TASK-999", "revision": 1}
    stage1 = validate_response(first, response(first, candidate))
    second = construct_request(DecisionPacket(first["decision_packet"]),
                               first["return_contract_package"], first["external_bindings"],
                               first["audit_profile_package"], request_mode="AUDIT_RECONCILE",
                               stage1_decision=stage1)
    reconciled = {**candidate, "goal": "Addressed risk"}
    material = stage2_response(second, reconciled, blocker=True)
    material["construct_audit"][0] = {
        "lens": material["construct_audit"][0]["lens"], "outcome": "RISK_FOUND",
        "risks": [{"risk_summary": "Change needed", "counterexample": "Current candidate fails",
                   "candidate_anchor": "task_id", "disposition": "ADDRESSED_BY_RECONCILIATION"}],
    }
    final = validate_response(second, material)
    assert final["semantic_value"]["handoff_candidate"] is None
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(final, second)


def test_identity_package_binding_and_replay(registry, profile_package):
    first = request(registry, profile_package)
    same = request(registry, profile_package)
    assert same["request_fingerprint"] == first["request_fingerprint"]
    changed = request(registry, profile_package, bindings={"task_id": "TASK-1000", "revision": 1})
    assert changed["request_fingerprint"] != first["request_fingerprint"]
    operational_a = {"provider": "one", "model": "A", "session": "first"}
    operational_b = {"provider": "two", "model": "B", "session": "second"}
    assert operational_a != operational_b
    assert request(registry, profile_package)["request_fingerprint"] == first["request_fingerprint"]
    semantic = response(first, {"task_id": "TASK-999", "revision": 1})
    output_a = {"semantic": semantic, "operational": operational_a}
    output_b = {"semantic": deepcopy(semantic), "operational": operational_b}
    assert validate_response(first, output_a["semantic"])["decision_fingerprint"] == validate_response(
        first, output_b["semantic"])["decision_fingerprint"]
    for invalid in (dict(first, unknown=1), dict(first, request_fingerprint="0" * 64)):
        with pytest.raises(BrainProviderProtocolError):
            revalidate_request(invalid)
    stage1 = validate_response(first, response(first, {"task_id": "TASK-999", "revision": 1}))
    assert validate_response(first, response(first, {"task_id": "TASK-999", "revision": 1})) == stage1
    for package, bindings in (
        (first["return_contract_package"], changed["external_bindings"]),
        (dict(first["return_contract_package"], return_contract_ref={"digest": "0" * 64}), first["external_bindings"]),
    ):
        with pytest.raises(BrainProviderProtocolError):
            construct_request(DecisionPacket(first["decision_packet"]), package, bindings,
                              first["audit_profile_package"], request_mode="AUDIT_RECONCILE",
                              stage1_decision=stage1)
    bad = deepcopy(first)
    bad["external_bindings"]["extra"] = 1
    bad["request_fingerprint"] = digest({k: v for k, v in bad.items() if k != "request_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_request(bad)
    bad_profile = deepcopy(first["audit_profile_package"])
    bad_profile["profile"]["lenses"][0]["check"] += " Changed."
    with pytest.raises(BrainProviderProtocolError):
        construct_request(DecisionPacket(first["decision_packet"]),
                          first["return_contract_package"], first["external_bindings"],
                          bad_profile, request_mode="AUDIT_CONSTRUCT")
    replay = deepcopy(first)
    replay["external_bindings"] = changed["external_bindings"]
    replay["request_fingerprint"] = digest({k: v for k, v in replay.items() if k != "request_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        construct_request(DecisionPacket(first["decision_packet"]), first["return_contract_package"],
                          changed["external_bindings"], first["audit_profile_package"],
                          request_mode="AUDIT_RECONCILE", stage1_decision=stage1)


def test_generic_dotted_external_path(registry, profile_package):
    supplied, package = inputs(registry, "REPAIR_AUTHORING")
    altered = deepcopy(package)
    altered["contract"]["candidate_contract"]["bindings"][0]["path"] = "identity.repair_id"
    altered["return_contract_ref"] = return_contract_ref(altered["contract"], altered["bounds"])
    req = construct_request(supplied, altered, {"identity.repair_id": "REPAIR-999"},
                            profile_package, request_mode="AUDIT_CONSTRUCT")
    assert validate_response(req, response(req, {"identity": {"repair_id": "REPAIR-999"}}))
    for candidate in ({"identity": {"repair_id": "REPAIR-998"}}, {"identity.repair_id": "REPAIR-999"}):
        with pytest.raises(BrainProviderProtocolError):
            validate_response(req, response(req, candidate))


def test_task_handoff_remains_existing_family_validation(registry, profile_package):
    first = request(registry, profile_package)
    candidate = {
        "task_id": "TASK-999", "revision": 1,
        "goal": "Author a bounded candidate.", "problem": "A candidate is needed.",
        "assumptions": [], "scope": {"inspect": [], "modify": ["src/example.py"]},
        "non_goals": [], "constraints": {"hard": ["Keep scope bounded."]},
        "acceptance": [{"id": "AC1", "condition": "The bounded candidate exists."}],
        "verification": {"policy": "minimum-sufficient-v1", "required": ["python -m pytest -q tests/test_example.py"]},
    }
    first_decision = validate_response(first, response(first, candidate))
    second = construct_request(DecisionPacket(first["decision_packet"]),
                               first["return_contract_package"], first["external_bindings"],
                               first["audit_profile_package"], request_mode="AUDIT_RECONCILE",
                               stage1_decision=first_decision)
    final = validate_response(second, stage2_response(second, candidate))
    assert validate_task(final["semantic_value"]["handoff_candidate"]).task_id == "TASK-999"


def test_external_binding_and_closed_response(registry, profile_package):
    first = request(registry, profile_package)
    for candidate in (
        {"task_id": "TASK-999"},
        {"task_id": "TASK-999", "revision": 2},
        {"task_id": "TASK-999", "revision": 1, "provider": "x"},
        {"task_id": "TASK-999", "revision": 1, "nested": {"format": "AIOS_BRAIN_REQUEST"}},
    ):
        with pytest.raises(BrainProviderProtocolError):
            validate_response(first, response(first, candidate))
    with pytest.raises(BrainProviderProtocolError):
        validate_response(first, {**response(first, {}), "model": "x"})
    with pytest.raises(BrainProviderProtocolError):
        validate_response(first, response(first, {"task_id": "TASK-999", "revision": 1, "bad": float("nan")}))


def test_direct_grammar_and_modes(registry, profile_package):
    direct = request(registry, profile_package, flow="DIAGNOSTIC")
    candidate = {"proposal": "Investigate", "uncertainty": {"status": "NONE", "summary": None}}
    decision = validate_response(direct, response(direct, candidate))
    assert revalidate_decision(decision, direct) == decision
    for invalid in (
        {"proposal": "Investigate", "uncertainty": {"status": "MATERIAL", "summary": None}},
        {"proposal": "Investigate", "uncertainty": {"status": "NONE", "summary": "x"}},
        {"proposal": "Investigate", "uncertainty": {"status": "NONE", "summary": None, "extra": 1}},
    ):
        with pytest.raises(BrainProviderProtocolError):
            validate_response(direct, response(direct, invalid))
    supplied, package = inputs(registry, "DIAGNOSTIC")
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, package, {}, profile_package, request_mode="AUDIT_CONSTRUCT")
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied.as_dict(), package, {}, request_mode="DIRECT")


def test_strict_bounds_and_normalization(registry, profile_package):
    first = request(registry, profile_package)
    supplied = DecisionPacket(first["decision_packet"])
    for binding in ("x" * 4097, "\ud800", {"a\r": 1, "a\n": 2}, object()):
        with pytest.raises(BrainProviderProtocolError):
            construct_request(supplied, first["return_contract_package"],
                              {"task_id": binding, "revision": 1}, profile_package,
                              request_mode="AUDIT_CONSTRUCT")
    nested = {}
    current = nested
    for _ in range(33):
        current["x"] = {}
        current = current["x"]
    with pytest.raises(BrainProviderProtocolError):
        validate_response(first, response(first, nested))
    with pytest.raises(BrainProviderProtocolError):
        validate_response(first, response(first, {"task_id": "TASK-999", "revision": 1,
                                                   "large": "x" * 655360}))
    lf = {"task_id": "TASK-999\n", "revision": 1}
    crlf = {"task_id": "TASK-999\r\n", "revision": 1}
    a = construct_request(supplied, first["return_contract_package"], lf,
                          profile_package, request_mode="AUDIT_CONSTRUCT")
    b = construct_request(supplied, first["return_contract_package"], crlf,
                          profile_package, request_mode="AUDIT_CONSTRUCT")
    assert a["request_fingerprint"] == b["request_fingerprint"]
    for ceiling in (393216, 655360, 4096):
        _bound("x" * (ceiling - 2), ceiling, "edge")
        with pytest.raises(BrainProviderProtocolError):
            _bound("x" * (ceiling - 1), ceiling, "edge")
    at_depth = {}
    current = at_depth
    for _ in range(31):
        current["x"] = {}
        current = current["x"]
    assert _normal(at_depth) == at_depth
    current["x"] = {"beyond": 1}
    with pytest.raises(BrainProviderProtocolError):
        _normal(at_depth)
