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


def v3_sections(first, candidate):
    """Bounded fixture Brain judgments, without future artifact facts."""
    binding = {"packet_fingerprint": first["packet_fingerprint"],
               "reconciled_candidate_fingerprint": digest(candidate)}
    shapes = [
        ("PRIMARY_RESULT", "PRIMARY RUN with RESULT before review"),
        ("FAILURE", "Failed RUN with FAILURE and no RESULT"),
        ("REVIEWED_RESULT", "RUN with RESULT and existing REVIEW preserving reviewed subject"),
        ("REMEDIATION_RESULT", "REMEDIATION RUN with RESULT linked to prior finding"),
        ("REPAIR_RESULT", "REPAIR RUN with RESULT linked to failed RUN"),
    ]
    paths = [
        ("PRIMARY_PASS_TO_PUBLICATION", "Current PRIMARY supports Reviewer PASS then Publisher publication"),
        ("CHANGES_REQUIRED_REMEDIATION_PASS_TO_PUBLICATION", "Current correction supports REMEDIATION then PASS then publication"),
        ("FAILURE_REPAIR_PASS_TO_PUBLICATION", "Current failure recovery supports REPAIR then PASS then publication"),
    ]
    return {
        "cross_authority_context": {**binding, "status": "CLEAR",
            "basis": "Caller-owned publication authority context crosses shared helpers",
            "entries": [{"id": "PUBLISHER_REMOTE", "status": "COVERED",
                "basis": "A caller-selected remote different from the ambient remote must survive",
                "candidate_anchor": "scope.modify", "caller_authority": "Publisher caller",
                "consumer_authority": "Publication shared helper", "context_role": "explicit remote selection",
                "propagation": "Pass the explicit caller input through every helper; do not rediscover it"}]},
        "canonical_shape": {**binding, "status": "CLEAR",
            "basis": "Preserve both FAILURE and reviewed RESULT lineage across shared reconciliation",
            "entries": [{"id": identity, "status": "COVERED", "shape": shape,
                         "candidate_anchor": "scope.modify", "basis": "Considered against current canonical family"}
                        for identity, shape in shapes]},
        "terminal_lifecycle": {**binding, "status": "CLEAR",
            "basis": "Brain feasibility judgment uses current capabilities, without future proof",
            "entries": [{"id": identity, "status": "FEASIBLE", "basis": basis,
                         "candidate_anchor": "constraints.hard"} for identity, basis in paths]},
    }


def test_task_phase_ledger_is_normalized_and_part_of_stage2_identity(profile):
    supplied = packet("TASK_AUTHORING")
    first = construct_stage1(supplied, profile, {"acceptance": [{"id": "AC1", "condition": "Exists"}]})
    material = stage2_input(first, profile)
    # BP-4A is deliberately not a final TASK validator: even incomplete,
    # duplicate or PROOF_LATER declarations have a distinct semantic identity.
    historical = validate_stage2(supplied, profile, first, material)
    material["acceptance_phase_ledger"] = [{"id": "AC1", "phase": "CLAIM_NOW"}]
    final = validate_stage2(supplied, profile, first, material)
    assert final["stage2_fingerprint"] != historical["stage2_fingerprint"]
    assert final["handoff_candidate"] == first["construct_candidate"]
    fingerprints = {final["stage2_fingerprint"]}
    for ledger in (
        [], [{"id": "AC1", "phase": "PROOF_LATER"}],
        [{"id": "OTHER", "phase": "CLAIM_NOW"}],
        material["acceptance_phase_ledger"] * 2,
    ):
        result = validate_stage2(supplied, profile, first, {**material, "acceptance_phase_ledger": ledger})
        assert result["stage2_fingerprint"] not in fingerprints
        fingerprints.add(result["stage2_fingerprint"])
    crlf = {**material, "acceptance_phase_ledger": [{"id": "AC1\r\nline", "phase": "CLAIM_NOW"}]}
    lf = {**crlf, "acceptance_phase_ledger": [{"phase": "CLAIM_NOW", "id": "AC1\nline"}]}
    assert validate_stage2(supplied, profile, first, crlf) == validate_stage2(supplied, profile, first, lf)


@pytest.mark.parametrize("ledger", (
    None, {}, [{"id": "AC1"}], [{"id": "AC1", "phase": "UNKNOWN"}],
    [{"id": "", "phase": "CLAIM_NOW"}], [{"id": "x" * 257, "phase": "CLAIM_NOW"}],
    [{"id": "AC1", "phase": "CLAIM_NOW", "condition": "Hidden semantics"}],
    [{"id": "AC1", "phase": "CLAIM_NOW"}] * 257,
    [{"id": "x" * 256, "phase": "CLAIM_NOW"}] * 128,
))
def test_task_phase_ledger_shape_and_bounds_fail_closed(profile, ledger):
    supplied = packet("TASK_AUTHORING")
    first = construct_stage1(supplied, profile, {"proposal": "Task"})
    material = {**stage2_input(first, profile), "acceptance_phase_ledger": ledger}
    with pytest.raises(BrainAuditError):
        validate_stage2(supplied, profile, first, material)


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING"))
def test_task_phase_ledger_is_excluded_from_other_flows_and_candidates(profile, flow):
    supplied = packet(flow)
    first = construct_stage1(supplied, profile, {"proposal": "Bounded"})
    material = stage2_input(first, profile)
    assert "acceptance_phase_ledger" not in validate_stage2(supplied, profile, first, material)
    with pytest.raises(BrainAuditError):
        validate_stage2(supplied, profile, first, {**material, "acceptance_phase_ledger": []})
    with pytest.raises(BrainAuditError):
        construct_stage1(supplied, profile, {"acceptance_phase_ledger": []})


def test_task_phase_ledger_cannot_be_nested_in_candidate(profile):
    supplied = packet("TASK_AUTHORING")
    with pytest.raises(BrainAuditError):
        construct_stage1(supplied, profile, {"acceptance_phase_ledger": []})
    first = construct_stage1(supplied, profile, {"proposal": "Task"})
    material = stage2_input(first, profile)
    material["reconciled_candidate"]["acceptance_phase_ledger"] = []
    with pytest.raises(BrainAuditError):
        validate_stage2(supplied, profile, first, material)


def test_stage2_reconciles_later_proof_out_of_final_acceptance(profile):
    supplied = packet("TASK_AUTHORING")
    candidate = {"acceptance": [
        {"id": "AC1", "condition": "Implementation exposes the gate"},
        {"id": "AC2", "condition": "Runtime verification passes"},
    ], "constraints": {"hard": []}}
    first = construct_stage1(supplied, profile, candidate)
    material = stage2_input(first, profile)
    material["reconciled_candidate"]["acceptance"].pop()
    material["reconciled_candidate"]["constraints"]["hard"].append("Runtime owns verification and its results")
    lens = material["construct_audit"][5]["lens"]
    material["construct_audit"][5] = {
        "lens": lens, "outcome": "RISK_FOUND", "risks": [{
            "risk_summary": "AC2 requires future Runtime proof",
            "counterexample": "Executor completes implementation before verification runs",
            "candidate_anchor": "acceptance.AC2",
            "disposition": "ADDRESSED_BY_RECONCILIATION",
        }],
    }
    material["acceptance_phase_ledger"] = [{"id": "AC1", "phase": "CLAIM_NOW"}]
    result = validate_stage2(supplied, profile, first, material)
    assert result["outcome"] == "CANDIDATE"
    assert result["handoff_candidate"]["acceptance"] == candidate["acceptance"][:1]
    assert result["handoff_candidate"]["constraints"]["hard"]
    assert result["acceptance_phase_ledger"] == [{"id": "AC1", "phase": "CLAIM_NOW"}]


def digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def profile():
    # Immutable historical v2 protocol examples retain their original grammar.
    return next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"]
                if p["id"] == "brain-high-value-v2")


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
    lenses = [lens["id"] for lens in profile["lenses"]]
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
    crlf = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    assert profile_ref(parse_profile_registry(raw)["profiles"][0]) == profile_ref(
        parse_profile_registry(crlf)["profiles"][0])
    assert normalize_profile(profile) == profile
    assert profile["id"] == "brain-high-value-v2" and profile["version"] == 2
    assert profile["bounds"]["lens_check_bytes"] == 2048
    assert len(profile["lenses"]) == 8
    assert all(set(lens) == {"id", "check"} and lens["check"].strip()
               for lens in profile["lenses"])
    reordered_fields = deepcopy(profile)
    reordered_fields["lenses"][0] = dict(reversed(list(reordered_fields["lenses"][0].items())))
    assert profile_ref(reordered_fields) == profile_ref(profile)
    line_endings = deepcopy(profile)
    line_endings["lenses"][0]["check"] += "\r\nSecond line."
    lf = deepcopy(line_endings)
    lf["lenses"][0]["check"] = lf["lenses"][0]["check"].replace("\r\n", "\n")
    assert profile_ref(line_endings) == profile_ref(lf)
    changed = deepcopy(profile)
    changed["lenses"][0]["check"] += " Additional bounded instruction."
    assert profile_ref(changed)["digest"] != profile_ref(profile)["digest"]
    changed = deepcopy(profile)
    changed["lenses"][0]["id"] = "SCOPE_NON_GOALS"
    with pytest.raises(BrainAuditError):
        profile_ref(changed)
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
        raw.replace("  - id: brain-high-value-v2", "  - id: brain-high-value-v2\n    extra: nope"),
        raw.replace("  - id: brain-high-value-v2", "  - id: brain-high-value-v2\n    id: duplicate"),
        raw.replace("  - id: brain-high-value-v2", "  - id: brain-high-value-v2\n    tagged: !!python/object/apply:os.system [echo]"),
        raw + " " * 32768,
    ):
        with pytest.raises(BrainAuditError):
            parse_profile_registry(bad)
    with pytest.raises(BrainAuditError):
        normalize_profile({**profile, "lenses": profile["lenses"] + [profile["lenses"][0]]})


def test_v2_lens_records_fail_closed(profile):
    for mutate in (
        lambda p: p["lenses"][0].pop("check"),
        lambda p: p["lenses"][0].update(extra="unexpected"),
        lambda p: p["lenses"].__setitem__(0, p["lenses"][0]["id"]),
        lambda p: p["lenses"].__setitem__(1, deepcopy(p["lenses"][0])),
        lambda p: p["lenses"][0].update(id="UNKNOWN_LENS"),
        lambda p: p["lenses"].reverse(),
        lambda p: p["lenses"][0].update(check=""),
        lambda p: p["lenses"][0].update(check=" \t "),
        lambda p: p["lenses"][0].update(check="\ud800"),
        lambda p: p["lenses"][0].update(check=1),
        lambda p: p["lenses"][0].update(check="x" * 2049),
        lambda p: p["bounds"].update(lens_check_bytes=2049),
        lambda p: p.update(extra="unexpected"),
    ):
        bad = deepcopy(profile)
        mutate(bad)
        with pytest.raises(BrainAuditError):
            normalize_profile(bad)
    for bad in ("x" * 32769, "\ud800"):
        with pytest.raises(BrainAuditError):
            parse_profile_registry(bad)


def test_lens_procedure_changes_bind_construct_and_stage2(profile):
    p = packet()
    candidate = {"proposal": "same"}
    first = construct_stage1(p, profile, candidate)
    changed = deepcopy(profile)
    changed["lenses"][0]["check"] += " Verify one more case."
    other = construct_stage1(p, changed, candidate)
    assert other["audit_profile_ref"]["digest"] != first["audit_profile_ref"]["digest"]
    assert other["construct_fingerprint"] != first["construct_fingerprint"]
    with pytest.raises(BrainAuditError):
        validate_stage2(p, changed, first, stage2_input(first, profile))


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
    dismissed["construct_audit"][0] = {"lens": profile["lenses"][0]["id"], "outcome": "RISK_FOUND", "risks": [{
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


@pytest.fixture
def v3_profile():
    return parse_profile_registry(REGISTRY.read_bytes())["profiles"][0]


@pytest.fixture(params=("brain-high-value-v3", "aios-adaptive-brain-audit-v1"))
def conformant_profile(request):
    return next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"]
                if p["id"] == request.param)


@pytest.fixture
def adaptive_profile():
    return next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"]
                if p["id"] == "aios-adaptive-brain-audit-v1")


def v3_material(profile):
    supplied = packet("TASK_AUTHORING")
    first = construct_stage1(supplied, profile, {"proposal": "Preserve canonical authority context"})
    material = stage2_input(first, profile)
    material.update(v3_sections(first, material["reconciled_candidate"]))
    return supplied, first, material


def test_prospective_v3_and_immutable_v2_have_distinct_direct_protocols(profile, v3_profile):
    assert v3_profile["id"] == "brain-high-value-v3" and v3_profile["version"] == 3
    historical_registry = {"format": "AIOS_BRAIN_AUDIT_PROFILES", "version": 1, "profiles": [profile]}
    import yaml
    assert parse_profile_registry(yaml.safe_dump(historical_registry))["profiles"] == [profile]
    supplied = packet("TASK_AUTHORING")
    first = construct_stage1(supplied, profile, {"proposal": "Historical v2"})
    old = validate_stage2(supplied, profile, first, stage2_input(first, profile))
    assert old["audit_profile_ref"] == profile_ref(profile)
    assert old["outcome"] == "CANDIDATE"
    assert "terminal_lifecycle" not in old
    with pytest.raises(BrainAuditError):
        validate_stage2(supplied, v3_profile, first, stage2_input(first, profile))
    current_packet, current_first, material = v3_material(v3_profile)
    assert validate_stage2(current_packet, v3_profile, current_first, material)["outcome"] == "CANDIDATE"
    with pytest.raises(BrainAuditError):
        validate_stage2(current_packet, v3_profile, current_first, stage2_input(current_first, v3_profile))


def test_caller_owned_remote_propagation_is_bounded_and_fingerprint_bound(conformant_profile):
    supplied, first, material = v3_material(conformant_profile)
    result = validate_stage2(supplied, conformant_profile, first, material)
    edge = material["cross_authority_context"]["entries"][0]
    assert edge["context_role"] == "explicit remote selection"
    assert "every helper" in edge["propagation"]
    edge["propagation"] = "Thread the caller argument through the reconciliation helper too"
    changed = validate_stage2(supplied, conformant_profile, first, material)
    assert changed["stage2_fingerprint"] != result["stage2_fingerprint"]
    assert changed["handoff_candidate"] == result["handoff_candidate"]
    material["cross_authority_context"]["entries"] = []
    with pytest.raises(BrainAuditError, match="CLEAR requires explicit"):
        validate_stage2(supplied, conformant_profile, first, material)
    # Applicability is a Brain declaration, not inferred from candidate prose.
    material["cross_authority_context"].update(status="NOT_APPLICABLE", basis="No authority edge is affected")
    assert validate_stage2(supplied, conformant_profile, first, material)["outcome"] == "CANDIDATE"


def test_failure_alone_cannot_substitute_for_reviewed_result_shape(conformant_profile):
    supplied, first, material = v3_material(conformant_profile)
    shapes = material["canonical_shape"]["entries"]
    assert "no RESULT" in shapes[1]["shape"]
    assert "existing REVIEW" in shapes[2]["shape"]
    shapes[:] = [entry for entry in shapes if entry["id"] != "REVIEWED_RESULT"]
    with pytest.raises(BrainAuditError, match="canonical_shape coverage"):
        validate_stage2(supplied, conformant_profile, first, material)


@pytest.mark.parametrize("section,lens,index", [
    ("cross_authority_context", "AUTHORITY_BOUNDARY", 0),
    ("canonical_shape", "FAILURE_MODE_COUNTEREXAMPLES", 2),
    ("terminal_lifecycle", "AC_CONSISTENCY_COMPLETENESS", 1),
])
def test_conformance_blocker_requires_no_decision(conformant_profile, section, lens, index):
    supplied, first, material = v3_material(conformant_profile)
    material[section]["entries"][index].update(
        status="BLOCKED", basis="Current normal correction path cannot reach publication")
    with pytest.raises(BrainAuditError, match="contradicts BLOCKED"):
        validate_stage2(supplied, conformant_profile, first, material)
    material[section]["status"] = "BLOCKED"
    with pytest.raises(BrainAuditError, match="closure BLOCKER"):
        validate_stage2(supplied, conformant_profile, first, material)
    closure = next(entry for entry in material["closure"] if entry["lens"] == lens)
    closure.update(outcome="BLOCKER", blocker_summary="Current capability blocks this prerequisite")
    with pytest.raises(BrainAuditError, match="outcome contradicts closure"):
        validate_stage2(supplied, conformant_profile, first, material)
    material["outcome"] = "NO_DECISION"
    result = validate_stage2(supplied, conformant_profile, first, material)
    assert result["outcome"] == "NO_DECISION" and result["handoff_candidate"] is None


@pytest.mark.parametrize("section", ["cross_authority_context", "canonical_shape", "terminal_lifecycle"])
@pytest.mark.parametrize("fault", ["missing", "extra", "empty_basis", "binding", "duplicate", "oversize", "private", "path"])
def test_v3_closed_sections_binding_bounds_and_privacy(conformant_profile, section, fault):
    supplied, first, material = v3_material(conformant_profile)
    target = material[section]
    if fault == "missing":
        del material[section]
    elif fault == "extra":
        target["extra"] = "Substituted support"
    elif fault == "empty_basis":
        target["basis"] = " "
    elif fault == "binding":
        target["reconciled_candidate_fingerprint"] = "0" * 64
    elif fault == "duplicate":
        target["entries"].append(deepcopy(target["entries"][0]))
    elif fault == "oversize":
        target["entries"][0]["basis"] = "x" * 2049
    elif fault == "private":
        target["entries"][0]["provider_identity"] = "Forbidden"
    else:
        target["entries"][0]["basis"] = "C:/private/context"
    with pytest.raises(BrainAuditError):
        validate_stage2(supplied, conformant_profile, first, material)


def test_v3_reconciliation_rebinds_sections_and_rejects_nested_support(conformant_profile):
    supplied, first, material = v3_material(conformant_profile)
    material["reconciled_candidate"]["proposal"] = "Reconciled"
    material["construct_audit"][0] = {"lens": "AUTHORITY_BOUNDARY", "outcome": "RISK_FOUND", "risks": [{
        "risk_summary": "Caller input can be lost", "counterexample": "Helper rediscovers ambient authority",
        "candidate_anchor": "proposal", "disposition": "ADDRESSED_BY_RECONCILIATION",
    }]}
    with pytest.raises(BrainAuditError, match="lineage mismatch"):
        validate_stage2(supplied, conformant_profile, first, material)
    material.update(v3_sections(first, material["reconciled_candidate"]))
    assert validate_stage2(supplied, conformant_profile, first, material)["handoff_candidate"]["proposal"] == "Reconciled"
    for section in v3_sections(first, material["reconciled_candidate"]):
        with pytest.raises(BrainAuditError, match="nested audit envelope"):
            construct_stage1(supplied, conformant_profile, {"nested": {section: {}}})


@pytest.mark.parametrize("fault", ["missing_path", "substituted_path", "non_applicable_path", "order", "bare_clear"])
def test_all_normal_terminal_paths_are_required(conformant_profile, fault):
    supplied, first, material = v3_material(conformant_profile)
    target = material["terminal_lifecycle"]
    if fault == "missing_path":
        target["entries"].pop()
    elif fault == "substituted_path":
        target["entries"][1]["id"] = "PRIMARY_PASS_TO_PUBLICATION"
    elif fault == "non_applicable_path":
        target["entries"][1]["status"] = "NOT_APPLICABLE"
    elif fault == "order":
        target["entries"].reverse()
    else:
        target["entries"] = []
    with pytest.raises(BrainAuditError):
        validate_stage2(supplied, conformant_profile, first, material)


@pytest.mark.parametrize("flow", ["ARCHITECTURE", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING"])
def test_v3_task_sections_are_excluded_from_other_flows(conformant_profile, flow):
    supplied = packet(flow)
    first = construct_stage1(supplied, conformant_profile, {"proposal": "Bounded"})
    material = stage2_input(first, conformant_profile)
    assert "terminal_lifecycle" not in validate_stage2(supplied, conformant_profile, first, material)
    material.update(v3_sections(first, material["reconciled_candidate"]))
    with pytest.raises(BrainAuditError, match="closed contract"):
        validate_stage2(supplied, conformant_profile, first, material)


@pytest.mark.parametrize("fault", ["entry_count", "combined_bytes", "policy_substitution"])
def test_v3_conformance_safety_bounds_are_closed(conformant_profile, fault):
    supplied, first, material = v3_material(conformant_profile)
    if fault == "policy_substitution":
        changed = deepcopy(conformant_profile)
        changed["conformance"]["bounds"]["entries_per_section"] = 17
        with pytest.raises(BrainAuditError, match="conformance policy"):
            normalize_profile(changed)
        return
    edge = material["cross_authority_context"]["entries"][0]
    count = 17 if fault == "entry_count" else 16
    material["cross_authority_context"]["entries"] = [
        {**deepcopy(edge), "id": f"EDGE-{index}"} for index in range(count)
    ]
    if fault == "combined_bytes":
        for entry in material["cross_authority_context"]["entries"]:
            for field in ("basis", "propagation", "context_role"):
                entry[field] = "x" * 2048
    with pytest.raises(BrainAuditError, match="entry count|conformance sections"):
        validate_stage2(supplied, conformant_profile, first, material)


def test_adaptive_exact_identity_rejects_rename_partial_adaptation_and_unknowns(adaptive_profile, v3_profile):
    assert (adaptive_profile["id"], adaptive_profile["version"]) == ("aios-adaptive-brain-audit-v1", 1)
    assert adaptive_profile["procedure"] == v3_profile["procedure"]
    assert adaptive_profile["bounds"] == v3_profile["bounds"]
    assert adaptive_profile["conformance"] == v3_profile["conformance"]
    for legacy in parse_profile_registry(REGISTRY.read_bytes())["profiles"][:2]:
        renamed = {**deepcopy(legacy), "id": adaptive_profile["id"], "version": 1,
                   "conformance": deepcopy(adaptive_profile["conformance"])}
        with pytest.raises(BrainAuditError, match="partially adapted"):
            normalize_profile(renamed)
    for mutate in (
        lambda p: p.update(id="unknown-v1"), lambda p: p.update(version=True),
        lambda p: p["lenses"][0].update(check=v3_profile["lenses"][0]["check"]),
        lambda p: p["lenses"][0].update(check=p["lenses"][0]["check"] + " Additional instruction."),
        lambda p: p["bounds"].update(candidate_bytes=131071),
        lambda p: p.pop("conformance"),
        lambda p: p["procedure"]["stages"].append("RESEARCH_AUDIT"),
    ):
        bad = deepcopy(adaptive_profile)
        mutate(bad)
        with pytest.raises(BrainAuditError):
            normalize_profile(bad)
    equivalent = dict(reversed(list(adaptive_profile.items())))
    assert profile_ref(equivalent) == profile_ref(adaptive_profile)
    raw = REGISTRY.read_bytes().replace(b"\r\n", b"\n")
    assert parse_profile_registry(raw) == parse_profile_registry(raw.replace(b"\n", b"\r\n"))


@pytest.mark.parametrize("profile_id,expected_digest", (
    ("brain-high-value-v3", "2a2d46f85b5d9d4e3653f3c3fb7b7354be44da6fdbf13894dc5d374039370425"),
    ("brain-high-value-v2", "307a38c6ca3dae0146dcc59d225ea820f86fba082028bb105c6cf3e02d255ce0"),
))
def test_historical_normalized_profile_digests_remain_exact(profile_id, expected_digest):
    profile = next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"] if p["id"] == profile_id)
    assert profile_ref(profile)["digest"] == expected_digest


@pytest.mark.parametrize("key", (
    "cross_authority_context", "Cross-Authority-Context", "CANONICALSHAPE", "terminal_lifecycle",
    "provider_identity", "chain_of_thought", "audit_profile_ref", "acceptance_phase_ledger",
))
def test_v1_retains_reserved_key_rejection_in_construct_and_reconciliation(conformant_profile, key):
    supplied = packet()
    bad = {"proposal": [{"nested": {key: {}}}]}
    with pytest.raises(BrainAuditError):
        construct_stage1(supplied, conformant_profile, bad)
    first = construct_stage1(supplied, conformant_profile, {"proposal": "Initial"})
    material = stage2_input(first, conformant_profile)
    material["reconciled_candidate"] = bad
    with pytest.raises(BrainAuditError):
        validate_stage2(supplied, conformant_profile, first, material)


@pytest.mark.parametrize("bad", (
    {"nested": [{"format": "AIOS_BRAIN_REQUEST"}]},
    {"nested": {"format": "AIOS_BRAIN_DECISION", "semantic_value": {}}},
    {"nested": {"format": "AIOS_BRAIN_AUDIT_PROFILES", "profiles": []}},
    {"nested": {"format": []}}, {"nested": {"format": {"kind": "audit"}}},
    {"nested": {"focus_notes": "Only local correction"}},
    {"nested": {"selected_focus": ["F-F"]}},
    {"path": "C:/private/material"}, {"path": "read /home/private/material"},
    {"nested": {"C:/private/material": "path used as a key"}},
    {"value": float("nan")}, {"value": "\ud800"}, {"value": "x" * 131073},
))
def test_adaptive_malformed_nested_envelopes_paths_and_bounds(adaptive_profile, bad):
    supplied = packet()
    with pytest.raises(BrainAuditError):
        construct_stage1(supplied, adaptive_profile, bad)
    first = construct_stage1(supplied, adaptive_profile, {"proposal": "Initial"})
    material = stage2_input(first, adaptive_profile)
    material["reconciled_candidate"] = bad
    with pytest.raises(BrainAuditError):
        validate_stage2(supplied, adaptive_profile, first, material)


@pytest.mark.parametrize("source_id,target_id", (
    ("brain-high-value-v2", "aios-adaptive-brain-audit-v1"),
    ("brain-high-value-v3", "aios-adaptive-brain-audit-v1"),
    ("aios-adaptive-brain-audit-v1", "brain-high-value-v3"),
    ("aios-adaptive-brain-audit-v1", "brain-high-value-v2"),
))
def test_no_cross_profile_stage_reconciliation(source_id, target_id):
    profiles = {p["id"]: p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"]}
    supplied = packet()
    first = construct_stage1(supplied, profiles[source_id], {"proposal": "Same subject"})
    with pytest.raises(BrainAuditError, match="lineage mismatch"):
        validate_stage2(supplied, profiles[target_id], first, stage2_input(first, profiles[target_id]))


def test_adaptive_stage2_binding_closure_and_focus_completeness_basis(adaptive_profile):
    supplied = packet()
    candidate = {"proposal": "Minor local fix adds a credential issuer and a publication handoff"}
    first = construct_stage1(supplied, adaptive_profile, candidate)
    material = stage2_input(first, adaptive_profile)
    # A modeled Brain challenge, not a scripted semantic detection or live finding.
    material["construct_audit"][3] = {"lens": "FAILURE_MODE_COUNTEREXAMPLES", "outcome": "RISK_FOUND", "risks": [{
        "risk_summary": "Omitted F-A/F-D/F-E despite a local-only label",
        "counterexample": "New issuer and handoff change trust and workflow boundaries",
        "candidate_anchor": "proposal", "disposition": "ADDRESSED_BY_RECONCILIATION",
    }]}
    material["reconciled_candidate"] = {"proposal": "Keep the local fix within settled trust and handoff boundaries"}
    final = validate_stage2(supplied, adaptive_profile, first, material)
    assert final["construct_audit"][3]["outcome"] == "RISK_FOUND"
    checks = " ".join(lens["check"] for lens in adaptive_profile["lenses"])
    assert "Stage 2 independently re-derives material impacts" in checks
    assert "Unbound transient Stage-1 focus notes" in checks
    assert "omitted or mislabeled" in checks
    for field in ("focus_notes", "selected_focus"):
        with pytest.raises(BrainAuditError):
            validate_stage2(supplied, adaptive_profile, first, {**material, field: "Only F-F"})
        with pytest.raises(BrainAuditError):
            validate_stage2(supplied, adaptive_profile, {**first, field: "Only F-F"}, material)
    altered_packet = deepcopy(supplied)
    altered_packet["subject"] = {"kind": "ARCHITECTURE", "proposal": "Other subject"}
    altered_packet["packet_fingerprint"] = digest({k: v for k, v in altered_packet.items() if k != "packet_fingerprint"})
    with pytest.raises(BrainAuditError):
        validate_stage2(altered_packet, adaptive_profile, first, material)
    for field in ("construct_audit", "closure"):
        for fault in ("missing", "duplicate", "unknown", "not_applicable"):
            bad = deepcopy(material)
            if fault == "missing":
                bad[field].pop()
            elif fault == "duplicate":
                bad[field][1] = deepcopy(bad[field][0])
            elif fault == "unknown":
                bad[field][0]["lens"] = "FOCUS_ROUTER"
            else:
                bad[field][0]["outcome"] = "NOT_APPLICABLE"
            with pytest.raises(BrainAuditError):
                validate_stage2(supplied, adaptive_profile, first, bad)


_COMPARISON_QUESTIONS = [
    "Which material actionable risk is supported by the exact inputs? Identify candidate anchors and counterexamples.",
    "Which known risks were missed, which findings are false positives, and which work or authority expansion is unnecessary?",
    "Where do no-change, minimum change and new mechanism satisfy or violate approved hard floors?",
    "Does a local correction need research, or does its exact canonical cause support a minimum legal delta?",
    "What remains UNKNOWN about live deployment, useful decisions, whole-episode cost and North Star value?",
]


def export_comparison_basis(registry=None):
    """Portable paired inputs and honest cost basis; no invocation, scorer or cutover."""
    if registry is None:
        registry = parse_profile_registry(REGISTRY.read_bytes())
    profiles = [next(p for p in registry["profiles"] if p["id"] == identity)
                for identity in ("brain-high-value-v3", "aios-adaptive-brain-audit-v1")]
    cases = deepcopy(_COMPARISON_CANONICAL_CASES)
    modeled = (
        ("ORDINARY_TASK", "TASK_AUTHORING", "Add a bounded optional display field using the settled interface; no trust or proof changes.",
         "Latest delta and affected consumers; all eight lenses still mandatory; no default Research Audit."),
        ("LOCAL_CORRECTION", "REPAIR_AUTHORING", "Correct an exact modeled failure: caller argument lost by one shared helper; retain the admitted Executor and original failed subject.",
         "Causal finding, minimum legal delta, sticky Executor, negative regression, reuse only still-valid evidence; no new intent."),
        ("OMITTED_SECURITY", "TASK_AUTHORING", "A minor local fix adds a credential issuer, an always-on service and a publication handoff.",
         "Independently attack omitted architecture/security/integration despite unbound local-only notes."),
        ("UNKNOWN_BASELINE", "ARCHITECTURE", "Claim improved North Star from fewer test items with no comparable episode or live provider measurements.",
         "Unknown utility and cost; no false PASS, fabricated savings or automatic V1 activation."),
        ("MANDATORY_HIGH_TRUST", "ARCHITECTURE", "Replace a Human-required independent trust boundary with ambient identity to reduce cost.",
         "Reject security downgrade; compare alternatives only after satisfying approved hard floors."),
        ("DUPLICATE_CONTROLLER", "ARCHITECTURE", "Add an adaptive router, persistent focus database and third audit authority.",
         "No new router, independent audit authority, memory store or third stage."),
    )
    for identity, flow, proposal, question in modeled:
        cases.append({"case_id": identity, "source": {"kind": "MODELED_ADVERSARIAL_OR_LOCAL", "canonical_status": "NOT_CANONICAL"},
                      "flow": flow, "candidate": {"proposal": proposal}, "case_question": question})
    pairs = []
    for case in cases:
        supplied = packet(case.get("flow", "TASK_AUTHORING"))
        supplied["subject"] = {"kind": supplied["selected_flow"], "case_id": case["case_id"],
                               "source": case["source"]}
        supplied["canonical_facts"] = ({"frozen_projection": case["candidate"]}
                                         if "commit" in case["source"] else {})
        supplied["human_input"] = {"questions": _COMPARISON_QUESTIONS,
                                   "case_question": case.get("case_question", "Audit the frozen canonical projection without historical proof substitution.")}
        supplied["packet_fingerprint"] = digest({k: v for k, v in supplied.items() if k != "packet_fingerprint"})
        pair = {"case_id": case["case_id"], "inputs": [], "adjudication": {
            "actionable_material_risks": "UNKNOWN", "missed_known_risks": "UNKNOWN",
            "false_positives": "UNKNOWN", "unnecessary_work": "UNKNOWN",
            "scope_or_authority_expansion": "UNKNOWN", "tradeoffs": "UNKNOWN",
        }}
        for profile in profiles:
            first = construct_stage1(supplied, profile, case["candidate"])
            pair["inputs"].append({
                "decision_packet": deepcopy(supplied), "candidate": deepcopy(case["candidate"]),
                "audit_profile_package": {"profile": deepcopy(profile), "audit_profile_ref": profile_ref(profile)},
                "modeled_construct": first,
                "observations": {"basis": "OBSERVED_STRUCTURAL_BYTES_ONLY",
                    "profile_json_bytes": len(json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()),
                    "stage1_json_bytes": len(json.dumps(first, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()),
                    "provider_wall_time_by_stage": "UNKNOWN", "provider_input_output_tokens": "UNKNOWN",
                    "whole_episode_time": "UNKNOWN", "executed_reused_test_items": "UNKNOWN",
                    "human_review_minutes_interventions": "UNKNOWN", "live_provider_availability": "UNKNOWN"},
            })
        if case["case_id"] == "OMITTED_SECURITY":
            pair["unbound_decoy_notes"] = "Stage 1 considered only local correction F-F. These notes confer no coverage authority."
        pairs.append(pair)
    return {
        "format": "BOUNDED_V3_V1_COMPARISON_BASIS", "version": 1,
        "selection": "Preselected frozen projections and modeled adversarial cases; retrospective hindsight disclosed; no causal experiment.",
        "measurement_protocol": [
            "Keep exact packet, candidate, Human intent, references and questions identical within each pair; only profile material differs.",
            "Run exactly two stages separately per profile with exact bindings; invalidate/restart on source movement; never mix stage identities.",
            "Control or disclose provider/model/effort/order confounds; record each stage time and source-reported tokens, whole-episode tests/ops and Human cost separately.",
            "Human/Brain assess material actionable risks, missed risks, false positives, unnecessary work and limits against sources; no count score or canonical Reviewer verdict.",
            "Record measurements with source, assumptions and horizon as OBSERVED/MEASURED/ESTIMATED_ONLY; missing or unpaired records and disputed tradeoffs stay UNKNOWN.",
        ],
        "pairs": pairs, "quality_improvement": "UNKNOWN", "north_star_superiority": "UNKNOWN",
        "provider_run_status": "NOT_RUN", "cutover": "NOT_AUTHORIZED", "active_profile": "brain-high-value-v3",
        "observed_tradeoff": "V1 carries more bound guidance bytes; substantive benefit, token cost and correction-depth efficiency remain UNKNOWN.",
    }


def test_comparison_basis_is_paired_bounded_and_does_not_fabricate_measurements():
    basis = export_comparison_basis()
    assert [pair["case_id"] for pair in basis["pairs"][:4]] == ["TASK-320", "TASK-329", "TASK-334", "TASK-336"]
    assert len(basis["pairs"]) == 10
    for pair in basis["pairs"]:
        old, new = pair["inputs"]
        assert old["decision_packet"] == new["decision_packet"]
        assert old["candidate"] == new["candidate"]
        assert old["modeled_construct"]["construct_fingerprint"] != new["modeled_construct"]["construct_fingerprint"]
        assert old["observations"]["profile_json_bytes"] < new["observations"]["profile_json_bytes"]
        assert pair["adjudication"]["actionable_material_risks"] == "UNKNOWN"
        for item in pair["inputs"]:
            assert item["observations"]["provider_input_output_tokens"] == "UNKNOWN"
            assert len(json.dumps(item["decision_packet"]).encode()) < 131072
            assert item["modeled_construct"]["audit_profile_ref"] == item["audit_profile_package"]["audit_profile_ref"]
    assert basis["north_star_superiority"] == "UNKNOWN"
    assert basis["quality_improvement"] == "UNKNOWN"
    assert basis["cutover"] == "NOT_AUTHORIZED" and basis["provider_run_status"] == "NOT_RUN"
    assert len(json.dumps(basis, ensure_ascii=False).encode()) < 524288


# Frozen, bounded canonical projections selected before any provider output.
# Historical hindsight is disclosed; these are not replayed TASK ingress contracts.
_COMPARISON_CANONICAL_CASES = json.loads(r'''
[
  {
    "case_id": "TASK-320",
    "source": {
      "commit": "35e3fe6b382cf5e0a4f743c7dee18ae94403b7c3",
      "path": ".ai/tasks/TASK-320.yaml",
      "sha256": "c1a2f1ae51119280c0834de1c645986f7450fb3d337119b5b55e0dfb27b9c49d",
      "projection_fields": [
        "task_id",
        "revision",
        "goal",
        "problem",
        "non_goals",
        "constraints",
        "scope.modify"
      ]
    },
    "candidate": {
      "task_id": "TASK-320",
      "revision": 1,
      "goal": "Harden MINIMUM_SUFFICIENT_VERIFICATION_V2 execution ordering so Runtime reaches a trustworthy PASS/BLOCK decision with substantially less redundant pytest execution, while preserving the authored proof envelope, exact attribution, fail-close behavior, evidence binding, and all existing authority boundaries.",
      "problem": "RUN-313-007 exposed a deterministic cost defect in the active V2 execution strategy. The focused authored command collected 796 candidate test items and failed on 5 identities, all five of which were candidate-only relative to the 776-item base collection. Runtime nevertheless replayed the entire authored command on base before returning unresolved attribution, producing 1,572 pytest item executions. Current V2 also enables affected-first narrowing only for correction operations, and when a correction's previous failed nodeids are already covered by an authored broader command it moves that entire command first rather than probing the exact failed identities. Harden the existing V2 implementation so exact collection/failure identities are used as bounded fail-fast and attribution projections, without allowing those projections to replace successful authored verification.",
      "non_goals": [
        "Create MINIMUM_SUFFICIENT_VERIFICATION_V3, a second verification policy, semantic dependency graph, test-impact database, Planner, lifecycle router, retry/failover authority, correction selector, Reviewer, or Publisher.",
        "Infer semantic dependencies from changed production paths or let Runtime guess which authored proof obligations may be dropped.",
        "Allow an early candidate-delta probe or previous-failure probe to count as successful completion of the broader authored verification requirement.",
        "Remove, weaken, or automatically omit an explicitly authored full-suite integration guard.",
        "Add focused parallel execution, change the selected twelve-worker full-suite profile, or redesign TASK-319 parallel observation infrastructure except for compatibility needed by the existing canonical collection observer.",
        "Change TASK-313 production semantics, AUTHOR_TASK behavior, correction authoring semantics, BO-2+, TASK-310/H4, roadmap state, Reviewer verdict logic, Publisher behavior, or immutable historical artifacts.",
        "Repair any TASK-313 historical failure or reinterpret RUN-313-007 as a successful RUN."
      ],
      "constraints": {
        "hard": [
          "Keep policy identity minimum-sufficient-v2. All new behavior is Runtime-owned deterministic execution hardening inside the existing policy.",
          "Primary candidate-delta fail-fast may be derived only from exact mechanically observed collection identities for the exact authored pytest coverage on exact base/candidate commits under bound execution conditions. No semantic dependency inference is permitted.",
          "Candidate-delta execution is an early failure gate only. If the bounded delta probe passes or is ineligible, unavailable, incomplete, unstable or non-comparable, Runtime must continue to the existing authored verification requirement or safely fall back; it must not claim PASS from the delta probe.",
          "Delta-first eligibility must be deterministically bounded so an early probe cannot create pathological near-full duplicate execution merely for optimization. Ineligible projections fall back to the authored command without weakening proof.",
          "For REPAIR, REMEDIATION and DIRECT_CANDIDATE, exact previously failed nodeids that are validly bound to the correction subject may run before a covering broader authored command. Failure of the exact probe blocks immediately; success never discharges the broader authored requirement.",
          "When an authored candidate pytest command fails, Runtime should prefer an exact failure-projection attribution path: reproduce the exact failed identities on candidate, then compare the same exact identities on base under comparable profile/toolchain conditions before considering broad base replay.",
          "If an exact failed identity is reproducible on candidate but is absent from the exact base collection, Runtime may deterministically classify it as candidate-only blocking evidence and must not replay the entire broad base command merely to obtain an impossible same-node attribution.",
          "If exact failure projection cannot reproduce the candidate failure, collection identity is unavailable or conflicting, execution conditions are incomparable, or order/context dependence prevents trustworthy projection, fail closed or escalate to the existing broader attribution path. Optimization must never turn uncertainty into PASS.",
          "Exact SHA, tree, command/projection identity, profile, toolchain, collection identity, failure nodeid/phase/fingerprint, changed-files digest, envelope digest, correction lineage and raw evidence bindings remain canonical and lossless as applicable.",
          "Existing evidence reuse remains conservative. New projection evidence must not make cross-tree or cross-context reuse less strict.",
          "Generated exact-node probes must preserve parameterized nodeids and platform-safe quoting deterministically; malformed or ambiguous node identities fail closed.",
          "The explicit full-suite integration guard remains exactly authored policy input; TASK-320 does not authorize Runtime to add or remove full-suite proof based on heuristics.",
          "Executor modifies only scope.modify. Any required production mutation outside that scope must fail closed and return a Brain scope decision rather than widening itself.",
          "Implementation must start from the then-current canonical main containing TASK-320, remain a descendant of that admitted base, and use canonical Runtime/Reviewer/Publisher publication. Never use a stale TASK-313 candidate, review-decision ref, repair ref or alternate local branch as main-equivalent; never force publication."
        ]
      },
      "scope": {
        "modify": [
          "src/aios_renew/verification.py",
          "src/aios_renew/verification_contract.py",
          "tests/bp_v4_probe_plugin.py",
          "tests/test_verification.py",
          "tests/test_verification_contract.py",
          "tests/test_bp_v4_parallel_probe.py"
        ]
      }
    }
  },
  {
    "case_id": "TASK-329",
    "source": {
      "commit": "35e3fe6b382cf5e0a4f743c7dee18ae94403b7c3",
      "path": ".ai/tasks/TASK-329.yaml",
      "sha256": "807858c1de911a9d8928f92d9d715eef6304a3277660e4ed24ee51b392ceb96a",
      "projection_fields": [
        "task_id",
        "revision",
        "goal",
        "problem",
        "non_goals",
        "constraints",
        "scope.modify"
      ]
    },
    "candidate": {
      "task_id": "TASK-329",
      "revision": 1,
      "goal": "Publish the separately Human-ratified, prospective AIOS Kernel Amendment KA-01 as a precise bounded normative addendum and a single discoverable documentation link. Extend only the future-version governance contract for semantic verification obligations and optional Runtime-preterminal same-RUN feedback/continuation; preserve frozen Kernel v0.1 and all legacy behavior. Do not activate any production execution, verification, or correction feature.",
      "problem": "The reviewed, published TASK-326/TASK-328 VP-02 foundations explicitly identify frozen Kernel v0.1 section 11 automatic-retry exclusions and missing Runtime preterminal semantics as a STOP for VP-03-to-VP-07 production direction. Human explicitly ratified the two-stage-audited KA-01 prospective addendum and prioritized its separate canonical specification publication before VP-03. The amendment must not be conflated with a Constitution change, rewriting frozen v0.1, or a functioning automatic retry/scheduler.",
      "non_goals": [
        "Edit the Constitution, overwrite/rewrite the frozen Kernel v0.1 specification or freeze record, or reinterpret any existing TASK/RUN/RESULT/FAILURE/EVIDENCE/REVIEW/REPAIR/REMEDIATION identities or schemas.",
        "Implement or activate a Runtime-owned proof scheduler, feedback dispatcher, checkpoint store, evidence reuse authority, same-RUN retry loop, executor interface, new TASK parser/verification policy, new operational status, or optional production cutover.",
        "Add a second Planner, lifecycle router, generic retry/failover decision-maker, Reviewer, Publisher, memory database, semantic dependency guesser or duplicate proof execution engine.",
        "Claim that Human ratification, synthetic TASK-328 tests or this document alone authenticates a real applicability witness, leases, risk consent, actual cost savings, successful VP-01 measurements or production readiness.",
        "Relax sticky executor, Human opt-in, finite budget, source-to-target evidence validity, one-terminal, BR-1..BR-6, separate integration/concurrency/ordering proof, or mandatory independent review and Publisher gates.",
        "Update the roadmap, downstream dependency pin, VP-03 status, H4 origin, legacy live V2 behavior or any transport/workflow; Brain/Human own prospective planning semantics."
      ],
      "constraints": {
        "hard": [
          "Write a single separately versioned KA-01 normative addendum, visibly marked HUMAN_RATIFIED_PROSPECTIVE_SPEC and NOT_ACTIVATED; explicitly state its effect occurs only for future versioned Human-opted-in work after separate Runtime/Reviewer/Publisher-gated engineering. The constitutional baseline and frozen v0.1 remain unchanged.",
          "Map exact frozen Kernel clauses §3, §4.1–4.5, §5, §7, §9, §10, §11 and §12 to the narrow KA-01 extension while preserving all unaffected boundaries. §11 automatic retry exclusion gets only a narrowly future opt-in ACTIVE PRIMARY preterminal exception, not general automatic reroute/failover/repair.",
          "Future-version TASK may specify reviewed semantic proof obligations and deterministic mapping in lieu of mandatory Brain-authored executable test commands only after separate versioned schema admission; maintain TASK WHAT/WHY, no Executor/model/budget fields in TASK and legacy decoding intact.",
          "Describe exactly one admitted ACTIVE PRIMARY RUN with monotonic SHA/provenance-bound checkpoint sequence C1/K1→factual failed-proof feedback→same Executor implementation C2/K2 within finite Human-authorized opt-in/budget/live lease; terminal RESULT or FAILURE exactly once, never reopen terminal; terminal FAILURE remains separate AUTHOR_REPAIR/new RUN; REVIEW findings remain REMEDIATION.",
          "Specify factual typed feedback only (proof/acceptance identity, subject, state and evidence references), with zero code-edit/strategy instructions; no Runtime selection of HOW, model, Executor or Human/Brain risk and semantic decisions. Unknown, stale, uncommitted, over-budget, lease loss, risk change and no progress must stop/fail closed.",
          "Specify Runtime-authenticated source-to-target applicability over full test/fixture/helper/shared-state/toolchain/environment/profile/worker/concurrency/ordering/collection/integration footprints; VALID/INVALIDATED/UNKNOWN keep original evidence subject and do not grant PASS on a synthetic label. Distinct required integration/concurrency/ordering remain. One Runtime proof scheduler prohibits equivalent duplicate execution; all BR-1..BR-6 gates mandatory before base replay.",
          "Define crash/replay/duplicate-checkpoint behavior, idempotent feedback and bounded cost without creating competing canonical RUN/FAILURE from a pre-AIOS transport event. Subordinate operational metadata does not constitute a new lifecycle authority.",
          "State lifecycle gates separately: (i) Human ratification, (ii) exact independently admitted/Runtime-verified/Reviewer-PASS/Publisher-published normative addendum, (iii) separately admitted VP-03–VP-07 features, (iv) actual VP-01 measurement dependency reconciliation, VP-06 measured performance and VP-07 AC-01..AC-36 real negative conformance, (v) explicit Human opt-in for prospective activation. Missing any gate means NOT_ACTIVATED and no policy substitution.",
          "Describe legacy default/rollback, no cross-policy dual execution, no retroactive schema changes, and downstream pin/adoption separate fresh authority. Do not claim VP-02 fully closed, VP-01 DONE, VP-03 admitted or any production savings.",
          "Only modify the two exact allowed documentation paths. README receives a concise governance pointer to the separately versioned KA-01 document, not a second normative specification. No code, tests, GitHub workflow, roadmap or frozen document changes."
        ]
      },
      "scope": {
        "modify": [
          "docs/AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md",
          "README.md"
        ]
      }
    }
  },
  {
    "case_id": "TASK-334",
    "source": {
      "commit": "35e3fe6b382cf5e0a4f743c7dee18ae94403b7c3",
      "path": ".ai/tasks/TASK-334.yaml",
      "sha256": "0c554ffce405e0bfb322633d7ca614565af6ce37bbb325deca61b5959cfadb4e",
      "projection_fields": [
        "task_id",
        "revision",
        "goal",
        "problem",
        "non_goals",
        "constraints",
        "scope.modify"
      ]
    },
    "candidate": {
      "task_id": "TASK-334",
      "revision": 2,
      "goal": "Complete one bounded VP-03D end-to-end authenticated Runtime terminal-source and independent Reviewer-source contract, using published TASK-335 owner-bound observations and only valid in-scope material from failed TASK-333/334 candidates. Include GitHub App writer credential isolation and a staged, non-enforcing ref-authority smoke contract. Do not promote Git transport, metadata or a successful smoke alone to canonical Runtime verification, Reviewer PASS, publication or proof-reuse authority.",
      "problem": "RUN-333-001 and RUN-334-001 terminated at COMPLETION_GATE with clean preserved failed candidates and no canonical verification run. The first lacked a real Runtime/Reviewer source; the second captured conditional content but could not authenticate independently admitted Runtime/Reviewer ownership within its five-file scope. TASK-335 added in-process owner observations and was reviewed PASS and published, but its issuer_authenticated and reviewer_authority fields remain false and raw/currentness are unavailable. Runtime's local Git push principal is still unproven; both self-hosted runners use .\\TRUNG.",
      "non_goals": [
        "No modification or publication of failed TASK-333/334 candidate refs or original terminal artifacts.",
        "No second Runtime, Reviewer, Publisher, lifecycle router, scheduler or trust registry.",
        "No approval or bypass for arbitrary private-key/token access by Executor.",
        "No Windows Administrator adversarial threat model or administrator anti-tamper.",
        "No public raw verification logs, secrets, file-system paths or protected REVIEW payloads.",
        "No silent source-to-current-target proof conversion or synthetic terminal/preterminal authority.",
        "No live Stage-2 restrict-creations/updates enforcement, Stage-1 modification or bypass change.",
        "No frozen Kernel or schema amendment, KA-01 cutover, C1/C2 checkpoint activation or downstream adoption.",
        "No full-suite, base replay or unchanged verification repetition without proved invalidation or obligation."
      ],
      "constraints": {
        "hard": [
          "Preserve all current TASK/RUN/RESULT/EVIDENCE/REVIEW immutable schemas, admission, separate Reviewer verdict and Publisher CAS source authority.",
          "Issued terminal-source facts must originate in the existing admitted Runtime/verification completion boundary and bind TASK revision/blob, admitted RUN, base, profile, candidate/tree, RESULT/EVIDENCE, original raw digest/size and exact terminal artifact. Public constructors, fixtures, arbitrary remotes and Git content alone cannot authenticate issuance.",
          "Independent SUBMIT_REVIEW origin must be verifiably authenticated by its existing ingress-owner path, bind exact decision/ref/candidate/artifacts and never infer semantic verdict from GitHub App identity.",
          "Protected raw stays local at its existing authorized Runtime location. Public GitHub may carry only bounded safe digest/size/identity metadata. Lack of authorized independent raw read keeps RAW_UNAVAILABLE/UNKNOWN.",
          "Runtime Writer and Control Writer have separate GitHub App private keys. Runtime Writer token is minted only for trung-via/AIOS-renew with minimum necessary permissions; broker shall not leak token or private key to Codex. A dedicated Windows service identity is the approved isolation design; Windows Administrator remains a trusted Human recovery authority.",
          "An untrusted request from the .\\TRUNG runner is not sufficient IPC authority. Broker enforces a narrowly bound Runtime-owned operation, authenticated approved caller boundary, exact ref/old-new SHA/CAS and non-replay, without becoming lifecycle authority.",
          "The shared refs/heads/aios/publication-reservation/main lease/CAS path remains coherent across AUTHOR_TASK, SUBMIT_REVIEW, Publisher and publication recovery. Do not assign it blindly to only one writer.",
          "Stage 1 ruleset 24824106 stays unchanged, no bypass. Stage 2 separately evaluates restrict-creations and restrict-updates on Runtime/Control namespaces; activation requires positive/negative live writer smoke plus separate Human authority, not completion of this TASK alone.",
          "Historical source provenance cannot be retroactively minted. Positive source/currentness claims require independent proof; absent/torn, stale, forged or swapped inputs stay UNKNOWN/BLOCK.",
          "Reuse reviewed/published TASK-335 on main. Treat failed TASK-333/334 source only as exact-SHA inspectable material; do not merge or publish failed candidates wholesale.",
          "Verification under minimum-sufficient-v2 remains focused on newly affected obligations and actually invalidated evidence. Keep distinct mandatory integration proof; no automatic baseline replay or double-execution of unchanged items.",
          "Broker service deployment, live private key, GitHub installation credentials and production smoke are separate protected operational setup, never TASK payload or canonical public artifact. Missing live credentials keeps live-smoke unproven and enforcement off."
        ]
      },
      "scope": {
        "modify": [
          "src/aios_renew/operator.py",
          "src/aios_renew/authoring_ingress.py",
          "src/aios_renew/github_issue_ingress.py",
          "src/aios_renew/runtime.py",
          "src/aios_renew/review_transport.py",
          "src/aios_renew/publication.py",
          "src/aios_renew/runtime_provenance_owner.py",
          "src/aios_renew/runtime_provenance_issuer.py",
          "src/aios_renew/runtime_writer_broker.py",
          "src/aios_renew/proof_authority_handoff.py",
          "tests/test_runtime_provenance_issuer.py",
          "tests/test_runtime_provenance_owner.py",
          "tests/test_runtime_writer_broker.py",
          "tests/test_authoring_ingress.py",
          "tests/test_review_transport.py",
          "tests/test_publication.py",
          ".github/workflows/aios-brain-ingress.yml",
          ".github/workflows/aios-auto-publish.yml",
          "docs/AIOS-VP03-RUNTIME-SOURCE-ISSUANCE-CONTRACT-v1.md",
          "docs/AIOS-VP03-GITHUB-APP-WRITER-TRUST-v1.md"
        ]
      }
    }
  },
  {
    "case_id": "TASK-336",
    "source": {
      "commit": "35e3fe6b382cf5e0a4f743c7dee18ae94403b7c3",
      "path": ".ai/tasks/TASK-336.yaml",
      "sha256": "ca3072541cdd06416d71b24a0174161121b472b9df1f42a33dba7f43ba5b600c",
      "projection_fields": [
        "task_id",
        "revision",
        "goal",
        "problem",
        "non_goals",
        "constraints",
        "scope.modify"
      ]
    },
    "candidate": {
      "task_id": "TASK-336",
      "revision": 1,
      "goal": "Forward-recover AIOS-renew production source to the reviewed pre-VP03/TASK330 operational behavior at exact baseline 84478a59405427ef932740eb26d8ce6292b0fe71, retiring the additional VP03A/B/C/D evidence-attestation/protected-writer architecture and all of its runtime-integrated hooks. Preserve current canonical history, independent Runtime verification/Reviewer/Publisher responsibilities, existing Git CAS, and minimal safe public transport redaction. This TASK is recovery only, not a new evidence validity engine or intra-PRIMARY correction implementation.",
      "problem": "After TASK-330 through TASK-335 and TASK-334 r2 publication, VP03 introduced read-only proof catalog/checkpoint/handoff plus owner-bound provenance, independent source issuance and GitHub App Writer Broker. The new protected Windows broker is not deployed and the VP03 paths have not established production verification savings. The extra service and authentication requirements create implementation and operations cost contrary to the AIOS Manifesto's sufficient-evidence, no-redundant-work North Star. Human explicitly superseded VP03 protect design and selected a forward recovery, retaining existing KA01 prospective and VP-02 foundations. Do not turn this recovery into VP04 or VP05.",
      "non_goals": [
        "Reset main, force-push, delete or rewrite historical canonical refs, history, TASK records, FAILURE, REVIEW, REVIEW decision or RUN artifacts.",
        "Install or operate a Windows Writer Broker, new Runtime Writer/Control Writer App credentials, TLS service, provenance journal, additional currentness attestation system or Stage 2 enforcement.",
        "Change Github Stage 1 ruleset 24824106, its bypass settings, Stage 2 rulesets, GitHub secrets, Actions variables, runner accounts or workflow files.",
        "Retire existing admission/CAS/security or leak raw verification material merely to match the old transport byte-for-byte.",
        "Activate KA01 intra-RUN correction, change frozen Kernel, create checkpoint persistence, new proof engine/scheduler, automatic repair routing or another Reviewer/Publisher/Runtime.",
        "Rewrite the current roadmap, author VP03 Lean/VP04/VP05, publish source on behalf of Publisher, update downstream dependency pins or retest all history."
      ],
      "constraints": {
        "hard": [
          "Perform forward recovery in the new TASK-336 candidate from current canonical main, never by checkout/reset/force-update of main or historical artifacts; reference pre-VP03 SHA only as a source for file behavior comparison.",
          "Restore six existing source integration files toward their pre-VP03 behavior without removing independent canonical Runtime admission, versioned legacy decoding, verification, RESULT/FAILURE production, SUBMIT_REVIEW authority, Publisher exact-source CAS or correction flows.",
          "Retire VP03-exclusive six modules, six focused test files and six associated versioned docs; only the published pre-VP03 TASK/RUN/REVIEW Git history keeps their historical content. No remaining import path may demand those modules.",
          "Public transport must still use minimal deterministic safe projections, preserving canonical field/schema relations and immutable source evidence identity while never exposing protected original raw bytes, local path locators, secrets or unbounded diagnostics.",
          "Retain the previous canonical identity/lease/currentness/expected-old CAS checks; no credential escalation or opaque broker fallback. Empty or stale protected client config must not route Git operations into retired code.",
          "Historical code and artifacts remain inspectable using exact Git SHAs; immutable TASK-330..335 and TASK-334 r2 completion, RUN-333 failure, and existing REVIEW/REPAIR/REMEDIATION lineage stay unchanged.",
          "Keep all existing public workflow entrypoints, Runner transport and minimum-sufficient-v2 Runtime verification policy functional. Do not introduce a second scheduler or require new environment/provisioning.",
          "Limit production conformance testing to the identified affected surfaces, using the canonical Runtime test requirement and necessary narrow compatibility coverage; no full suite, automatic broad base replay or needless unchanged-proof rerun.",
          "If a necessary safety fix requires a file outside scope or contradicts a frozen/higher authority contract, stop with a typed scoped blocker and request separate Human/Brain semantic authorization rather than broadening."
        ]
      },
      "scope": {
        "modify": [
          "src/aios_renew/authoring_ingress.py",
          "src/aios_renew/github_issue_ingress.py",
          "src/aios_renew/operator.py",
          "src/aios_renew/runtime.py",
          "src/aios_renew/review_transport.py",
          "src/aios_renew/publication.py",
          "src/aios_renew/proof_applicability.py",
          "src/aios_renew/proof_authority_handoff.py",
          "src/aios_renew/proof_checkpoint_lineage.py",
          "src/aios_renew/runtime_provenance_issuer.py",
          "src/aios_renew/runtime_provenance_owner.py",
          "src/aios_renew/runtime_writer_broker.py",
          "tests/test_proof_applicability.py",
          "tests/test_proof_authority_handoff.py",
          "tests/test_proof_checkpoint_lineage.py",
          "tests/test_runtime_provenance_issuer.py",
          "tests/test_runtime_provenance_owner.py",
          "tests/test_runtime_writer_broker.py",
          "docs/AIOS-VP03-CANONICAL-PROOF-HANDOFF-v1.md",
          "docs/AIOS-VP03-CHECKPOINT-LINEAGE-AND-REPLAY-v1.md",
          "docs/AIOS-VP03-GITHUB-APP-WRITER-TRUST-v1.md",
          "docs/AIOS-VP03-OWNER-BOUND-PROVENANCE-AUTHORITY-v1.md",
          "docs/AIOS-VP03-PROVENANCE-APPLICABILITY-v1.md",
          "docs/AIOS-VP03-RUNTIME-SOURCE-ISSUANCE-CONTRACT-v1.md",
          "tests/test_runtime.py",
          "tests/test_review_transport.py",
          "tests/test_publication.py",
          "tests/test_authoring_ingress.py",
          "tests/test_operator.py",
          "tests/test_github_issue_ingress.py"
        ]
      }
    }
  }
]
''')
