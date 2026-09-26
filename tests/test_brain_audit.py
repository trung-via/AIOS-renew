"""BP-4A pure contract examples; Runtime owns canonical verification."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from aios_renew.brain_audit import (
    BrainAuditError, construct_stage1, normalize_profile, parse_profile_registry,
    profile_applies, profile_ref, validate_stage2,
)


REGISTRY = Path(__file__).resolve().parents[1] / ".ai" / "brain-audit-profiles.yaml"


def digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def profile():
    return parse_profile_registry(REGISTRY.read_bytes())["profiles"][0]


def packet(flow="ARCHITECTURE"):
    body = {
        "format": "AIOS_DECISION_PACKET", "version": 1, "kind": "DECISION_PACKET",
        "work_context_fingerprint": "a" * 64, "selected_flow": flow,
        "selection_basis": "EXPLICIT_SELECTOR",
        "authority_owner": "REVIEWER" if flow == "SEMANTIC_REVIEW" else "BRAIN",
        "decision_family_ref": "planning", "handoff_target": "HUMAN_BRAIN_PLANNING",
        "expected_return_shape": "BOUNDED_SEMANTIC_PROPOSAL",
        "pending_canonical_obligation": None, "pending_canonical_authority_owner": None,
        "requires_fresh_context_for_continuation": True,
        "canonical_facts": {}, "canonical_blocker": None, "bounded_observations": [],
        "executor_claims": None, "prior_semantic_decisions": None, "human_input": None,
        "subject": {"kind": flow}, "run_created": False, "executor_invoked": False,
        "verification_invoked": False, "state_mutated": False,
    }
    body["packet_fingerprint"] = digest(body)
    return body


def stage2_input(first, profile, *, changed=False, blocker=False):
    lenses = profile["lenses"]
    audit = [{"lens": lens, "outcome": "CLEAR"} for lens in lenses]
    candidate = deepcopy(first["construct_candidate"])
    if changed:
        candidate["proposal"] = "revised"
        audit[0] = {"lens": lenses[0], "outcome": "RISK_FOUND", "risks": [{
            "risk_summary": "Boundary risk", "counterexample": "A concrete counterexample",
            "candidate_anchor": "proposal", "disposition": "ADDRESSED_BY_RECONCILIATION",
        }]}
    closure = [{"lens": lens, "outcome": "CLEAR"} for lens in lenses]
    if blocker:
        closure[-1] = {"lens": lenses[-1], "outcome": "BLOCKER",
                       "blocker_summary": "The final proposal still crosses a boundary"}
    return {
        "packet_fingerprint": first["packet_fingerprint"],
        "audit_profile_ref": first["audit_profile_ref"],
        "selected_flow": first["selected_flow"],
        "construct_candidate": deepcopy(first["construct_candidate"]),
        "construct_fingerprint": first["construct_fingerprint"],
        "construct_audit": audit, "reconciled_candidate": candidate,
        "closure": closure, "outcome": "NO_DECISION" if blocker else "CANDIDATE",
    }


def test_registry_digest_and_exact_applicability(profile):
    raw = REGISTRY.read_bytes()
    crlf = raw.replace(b"\n", b"\r\n")
    assert profile_ref(parse_profile_registry(raw)["profiles"][0]) == profile_ref(
        parse_profile_registry(crlf)["profiles"][0])
    assert normalize_profile(profile) == profile
    changed = deepcopy(profile)
    changed["lenses"].reverse()
    assert profile_ref(changed)["digest"] != profile_ref(profile)["digest"]
    changed = deepcopy(profile)
    changed["bounds"]["risk_summary_bytes"] -= 1
    assert profile_ref(changed)["digest"] != profile_ref(profile)["digest"]
    assert [flow for flow in (
        "ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING",
        "DIAGNOSTIC", "SEMANTIC_REVIEW",
    ) if profile_applies(packet(flow), profile)] == profile["applicable_flows"]


def test_registry_rejects_duplicates_unknown_and_non_json(profile):
    raw = REGISTRY.read_text(encoding="utf-8")
    for bad in (
        raw + "\nversion: 1\n",
        raw.replace("  - id: brain-high-value-v1", "  - id: brain-high-value-v1\n    extra: nope"),
        raw.replace("  - id: brain-high-value-v1", "  - id: brain-high-value-v1\n    id: duplicate"),
        raw.replace("  - id: brain-high-value-v1", "  - id: brain-high-value-v1\n    tagged: !!python/object/apply:os.system [echo]"),
        raw + " " * 32768,
    ):
        with pytest.raises(BrainAuditError):
            parse_profile_registry(bad)
    with pytest.raises(BrainAuditError):
        normalize_profile({**profile, "lenses": profile["lenses"] + [profile["lenses"][0]]})


def test_stage1_normalizes_and_binds_exact_packet_and_profile(profile):
    p = packet()
    a = construct_stage1(p, profile, {"proposal": "line 1\r\nline 2", "part": [1, 2]})
    b = construct_stage1(p, profile, {"part": [1, 2], "proposal": "line 1\nline 2"})
    assert a == b
    assert a["construct_candidate"]["proposal"] == "line 1\nline 2"
    assert construct_stage1(p, profile, {"proposal": "other"})["construct_fingerprint"] != a["construct_fingerprint"]
    q = packet()
    q["human_input"] = "different"
    q["packet_fingerprint"] = digest({k: v for k, v in q.items() if k != "packet_fingerprint"})
    assert construct_stage1(q, profile, a["construct_candidate"])["construct_fingerprint"] != a["construct_fingerprint"]
    with pytest.raises(BrainAuditError):
        construct_stage1(packet("DIAGNOSTIC"), profile, {})


def test_stage2_closure_and_reconciliation(profile):
    p = packet()
    first = construct_stage1(p, profile, {"proposal": "initial"})
    clear = validate_stage2(p, profile, first, stage2_input(first, profile))
    assert clear["outcome"] == "CANDIDATE"
    assert clear["handoff_candidate"] == {"proposal": "initial"}
    revised = validate_stage2(p, profile, first, stage2_input(first, profile, changed=True))
    assert revised["reconciled_candidate_fingerprint"] != clear["reconciled_candidate_fingerprint"]
    assert revised["stage2_fingerprint"] != clear["stage2_fingerprint"]
    blocked = validate_stage2(p, profile, first, stage2_input(first, profile, changed=True, blocker=True))
    assert blocked["outcome"] == "NO_DECISION" and blocked["handoff_candidate"] is None


def test_stage2_rejects_lineage_lens_and_outcome_substitution(profile):
    p = packet()
    first = construct_stage1(p, profile, {"proposal": "initial"})
    good = stage2_input(first, profile)
    mutations = []
    for field, replacement in (
        ("packet_fingerprint", "f" * 64), ("construct_fingerprint", "f" * 64),
        ("audit_profile_ref", {**good["audit_profile_ref"], "digest": "f" * 64}),
        ("construct_candidate", {"proposal": "substituted"}),
        ("outcome", "NO_DECISION"),
    ):
        bad = deepcopy(good)
        bad[field] = replacement
        mutations.append(bad)
    for field in ("construct_audit", "closure"):
        bad = deepcopy(good)
        bad[field].pop()
        mutations.append(bad)
        bad = deepcopy(good)
        bad[field][0]["lens"] = "NOT_APPLICABLE"
        mutations.append(bad)
        bad = deepcopy(good)
        bad[field][0], bad[field][1] = bad[field][1], bad[field][0]
        mutations.append(bad)
    bad = deepcopy(good)
    bad["construct_audit"][0]["outcome"] = "NOT_APPLICABLE"
    mutations.append(bad)
    bad = deepcopy(good)
    bad["provider"] = "some-model"
    mutations.append(bad)
    for bad in mutations:
        with pytest.raises(BrainAuditError):
            validate_stage2(p, profile, first, bad)
    bad_first = deepcopy(first)
    bad_first["construct_fingerprint"] = "0" * 64
    with pytest.raises(BrainAuditError):
        validate_stage2(p, profile, bad_first, good)
    changed_packet = deepcopy(p)
    changed_packet["human_input"] = "new canonical context"
    changed_packet["packet_fingerprint"] = digest({
        k: v for k, v in changed_packet.items() if k != "packet_fingerprint"
    })
    with pytest.raises(BrainAuditError):
        validate_stage2(changed_packet, profile, first, good)
    changed_profile = deepcopy(profile)
    changed_profile["bounds"]["risk_summary_bytes"] -= 1
    with pytest.raises(BrainAuditError):
        validate_stage2(p, changed_profile, first, good)
    blocked = stage2_input(first, profile, blocker=True)
    blocked["outcome"] = "CANDIDATE"
    with pytest.raises(BrainAuditError):
        validate_stage2(p, profile, first, blocked)


def test_risk_and_reconciliation_fail_closed(profile):
    p = packet()
    first = construct_stage1(p, profile, {"proposal": "initial"})
    good = stage2_input(first, profile, changed=True)
    for mutate in (
        lambda m: m["construct_audit"][0]["risks"][0].update({"severity": "HIGH"}),
        lambda m: m["construct_audit"][0]["risks"][0].update({"disposition": "DISMISSED_WITH_BOUNDED_BASIS"}),
        lambda m: m["construct_audit"][0]["risks"][0].update({"risk_summary": "x" * 4097}),
        lambda m: m.update({"reconciled_candidate": first["construct_candidate"]}),
    ):
        bad = deepcopy(good)
        mutate(bad)
        with pytest.raises(BrainAuditError):
            validate_stage2(p, profile, first, bad)
    bad = stage2_input(first, profile)
    bad["reconciled_candidate"] = {"proposal": "silent rewrite"}
    with pytest.raises(BrainAuditError):
        validate_stage2(p, profile, first, bad)
    dismissed = stage2_input(first, profile)
    dismissed["construct_audit"][0] = {"lens": profile["lenses"][0], "outcome": "RISK_FOUND", "risks": [{
        "risk_summary": "Concern", "counterexample": "Example", "candidate_anchor": "proposal",
        "disposition": "DISMISSED_WITH_BOUNDED_BASIS", "dismissal_basis": "Bounded reason",
    }]}
    assert validate_stage2(p, profile, first, dismissed)["outcome"] == "CANDIDATE"


def test_candidate_privacy_depth_and_strict_json(profile):
    p = packet()
    for bad in (
        {"provider": "x"}, {"nested": {"chain_of_thought": "x"}},
        {"stage2": {}}, {"path": "C:/private/workspace"},
        {"format": "AIOS_BRAIN_SEMANTIC_AUDIT"},
        {"value": float("nan")}, {"value": {1: "bad key"}},
        {"value": "\ud800"}, {"content": "x" * 131073},
    ):
        with pytest.raises(BrainAuditError):
            construct_stage1(p, profile, bad)
    nested = {}
    cursor = nested
    for _ in range(33):
        cursor["child"] = {}
        cursor = cursor["child"]
    with pytest.raises(BrainAuditError):
        construct_stage1(p, profile, nested)


@pytest.mark.parametrize("key", [
    "provider", "provider_identity", "MODEL-ID", "session_id", "chat_history", "chain_of_thought",
    "raw_logs", "evidence_paths", "credentials", "workspace_root",
    "remote_url", "host_name", "created_at", "uuid", "random_id",
    "audit_profile_ref", "stage2fingerprint",
])
def test_exact_reserved_keys_fail_at_any_semantic_depth(profile, key):
    p = packet()
    bad = {"proposal": {"detail": {key: "private"}}}
    with pytest.raises(BrainAuditError, match="private metadata or nested audit envelope"):
        construct_stage1(p, profile, bad)

    first = construct_stage1(p, profile, {"proposal": "initial"})
    second = stage2_input(first, profile)
    second["reconciled_candidate"] = bad
    with pytest.raises(BrainAuditError, match="private metadata or nested audit envelope"):
        validate_stage2(p, profile, first, second)


@pytest.mark.parametrize("flow", ["ARCHITECTURE", "TASK_AUTHORING"])
def test_semantic_vocabulary_is_accepted_and_fingerprint_bound(profile, flow):
    p = packet(flow)
    semantic = {
        "provider_contract": {"model_policy": "portable"},
        "prompt_schema": "structured", "chat_memory_dependency": False,
    }
    first = construct_stage1(p, profile, semantic)
    final = validate_stage2(p, profile, first, stage2_input(first, profile))
    assert final["handoff_candidate"] == semantic
    assert final["reconciled_candidate_fingerprint"] == digest(semantic)

    changed = deepcopy(semantic)
    changed["provider_contract"]["model_policy"] = "revised"
    changed_first = construct_stage1(p, profile, changed)
    changed_final = validate_stage2(p, profile, changed_first,
                                    stage2_input(changed_first, profile))
    assert changed_first["construct_fingerprint"] != first["construct_fingerprint"]
    assert changed_final["reconciled_candidate_fingerprint"] == digest(changed)
    assert changed_final["reconciled_candidate_fingerprint"] != final["reconciled_candidate_fingerprint"]
    assert changed_final["stage2_fingerprint"] != final["stage2_fingerprint"]
