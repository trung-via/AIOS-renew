"""BP-4A pure contract examples; Runtime owns canonical verification."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from aios_renew.brain_audit import (
    BrainAuditError, construct_roa_subject, construct_stage1, normalize_profile, parse_profile_registry,
    profile_applies, profile_ref, validate_roa, validate_stage2, normalize_audit_policy,
    construct_moa_subject, construct_moa, validate_moa,
    construct_tca_subject, construct_tca, validate_tca,
    construct_aoa_subject, construct_aoa, validate_aoa,
)


REGISTRY = Path(__file__).resolve().parents[1] / ".ai" / "brain-audit-profiles.yaml"


def audit_policy_example(body, *, active=True):
    """Synthetic future authority input; never a repository planning mutation."""
    refs = {
        "ARCHITECTURE": {"id": "aoa-1", "version": 1, "digest": "7789fcc53bd6593c8d190e867d14d9fe80060395ae20a0706981d16fec7e617b"},
        "TASK_AUTHORING": {"id": "tca-1", "version": 1, "digest": "1fa1d5fd098494eeb5cb97665f40e7556969555aed552dfc7fa28871555884a2"},
        "REMEDIATION_AUTHORING": {"id": "moa-1", "version": 1, "digest": "49d1c4c3edefe3d3cefcd66e186b7d7b89738516129957c18415d55760ffe903"},
        "REPAIR_AUTHORING": {"id": "roa-1-r2", "version": 2, "digest": "aa6a5b60997849d6a00f9e4174ca7ef20fbc62ed64da645ee938a94f8e2639e4"},
    }
    if not active:
        refs = {flow: {"id": "aios-adaptive-brain-audit-v1", "version": 1,
                       "digest": "982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718"} for flow in refs}
    return {"format": "AIOS_BRAIN_AUDIT_POLICY", "version": 1,
            "state": "FOUR_SPECIALIZED" if active else "ADAPTIVE_V1",
            "main_sha": body["canonical_facts"]["main_sha"], "work_context_fingerprint": body["work_context_fingerprint"],
            "human_authority_digest": "1" * 64, "brain_authority_digest": "2" * 64,
            "r5_publication_sha": "d" * 40 if active else None, "profiles": refs}


@pytest.mark.parametrize("active", (False, True))
def test_r5_complete_policy_pins_existing_profiles_without_reminting_or_order_selection(active):
    body, _ = aoa_example()
    policy = audit_policy_example(body, active=active)
    if not active:
        # Reviewed publication alone does not activate the target mapping.
        policy["r5_publication_sha"] = "d" * 40
    assert normalize_audit_policy(policy) == policy
    profiles = parse_profile_registry(REGISTRY.read_bytes())["profiles"]
    for reference in policy["profiles"].values():
        assert sum(profile_ref(p) == reference for p in reversed(profiles)) == 1


@pytest.mark.parametrize("fault", (
    "partial", "mixed", "wrong_digest", "wrong_version", "bool_version", "wrong_flow", "unknown_profile",
    "missing_human", "missing_brain", "missing_publication", "bad_main", "bad_currentness", "state", "private", "oversized",
))
def test_r5_partial_ambiguous_or_forged_policy_has_no_usable_selection(fault):
    body, _ = aoa_example()
    policy = audit_policy_example(body)
    refs = policy["profiles"]
    if fault == "partial":
        del refs["REPAIR_AUTHORING"]
    elif fault == "mixed":
        refs["TASK_AUTHORING"] = audit_policy_example(body, active=False)["profiles"]["TASK_AUTHORING"]
    elif fault == "wrong_digest":
        refs["ARCHITECTURE"]["digest"] = "0" * 64
    elif fault == "wrong_version":
        refs["REPAIR_AUTHORING"]["version"] = 1
    elif fault == "bool_version":
        refs["TASK_AUTHORING"]["version"] = True
    elif fault == "wrong_flow":
        refs["ARCHITECTURE"] = deepcopy(refs["TASK_AUTHORING"])
    elif fault == "unknown_profile":
        refs["TASK_AUTHORING"]["id"] = "tca-new"
    elif fault in {"missing_human", "missing_brain"}:
        del policy["human_authority_digest" if fault == "missing_human" else "brain_authority_digest"]
    elif fault == "missing_publication":
        policy["r5_publication_sha"] = None
    elif fault == "bad_main":
        policy["main_sha"] = "main"
    elif fault == "bad_currentness":
        policy["work_context_fingerprint"] = None
    elif fault == "state":
        policy["state"] = "IMPLEMENTATION_APPROVED"
    elif fault == "private":
        policy["credentials"] = "private"
    else:
        policy["human_authority_digest"] = "x" * 8192
    with pytest.raises(BrainAuditError):
        normalize_audit_policy(policy)


@pytest.fixture
def aoa_profile():
    return next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"] if p["id"] == "aoa-1")


def aoa_example():
    body = packet()
    body.update(decision_family_ref="HUMAN_BRAIN_ARCHITECTURE_PLANNING",
                human_input="Preserve caller-selected context with the least necessary change; plan only.")
    body["canonical_facts"] = {"repository": "AIOS-renew", "main_sha": "b" * 40,
                               "roadmap": {"next_items": ["context-planning"]}}
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    return body, {
        "objective": "Preserve caller-selected context through every affected consumer",
        "constraints": ["Planning only; Human decides implementation and risk"],
        "authorization": {"planning_scope": ["caller context propagation"], "risk_acceptance": []},
        "alternatives": [
            {"id": "A", "kind": "NO_BUILD_REUSE", "description": "Reuse the existing explicit-context path"},
            {"id": "B", "kind": "MINIMUM_LAWFUL_DELTA", "description": "Pass the existing context through one helper"},
            {"id": "C", "kind": "NEW_MECHANISM", "description": "Introduce a context service only if simpler paths fail"},
        ],
        "consumers": [{"id": "transport", "authority": "TRANSPORT", "requirement": "Preserve the caller's choice"}],
        "evidence": [],
        "provenance": {"status": "AUTHENTICATED", "repository": "AIOS-renew", "main_sha": "b" * 40,
                       "work_context_fingerprint": body["work_context_fingerprint"]},
    }


def aoa_subject(body, profile, context):
    return construct_aoa_subject(body, profile, {"selected_flow": "ARCHITECTURE", "version": 1, "digest": "c" * 64}, {}, context)


def aoa_judgment(identity, subject, roles):
    missing = any(subject["sources"][r] is None for r in (*roles, "canonical", "intent", "constraints"))
    return {"id": identity, "outcome": "BLOCKER" if missing else "CLEAR", "knowledge": "UNKNOWN" if missing else "INFERRED",
            "basis": "Decisive source unavailable." if missing else "The supplied bounded context supports this comparative planning judgment.",
            "evidence_refs": [subject["sources"][r] for r in roles if subject["sources"][r] is not None]}


def aoa_candidate(selected="A"):
    return {"selected_alternative": selected, "proposal": "Reuse, change minimally or defer according to the bound comparison.",
            "rationale": "Prefer the least necessary change under the Human outcome and hard floors; no empirical superiority inferred.",
            "uncertainties": ["Episode cost is unmeasured in this synthetic conformance example"],
            "next_human_decision": "Human decides whether to accept the bounded planning recommendation."}


def aoa_first(context, profile, subject, selected="A"):
    from aios_renew.brain_audit import _AOA_CONSTRUCT_ROLES, _AOA_COST_DIMENSIONS
    roles = ("objective", "alternatives", "authorization", "consumers")
    alternatives = [{**aoa_judgment(identity, subject, roles),
        "expected_outcome": context["objective"] or "Human objective unavailable",
        "feasibility": "FEASIBLE", "planning_scope": [] if context["authorization"] is None else deepcopy(context["authorization"]["planning_scope"]),
        "risks": ["A hidden consumer might discard caller context"], "risk_acceptance_required": [],
        "reversibility": "Retain the existing path; Human decides any later bounded delta.",
        "necessity": "The declared outcome must fail under simpler options before a new mechanism is necessary.",
        "externalities": {"positive": ["Reuse current context where feasible"], "negative": ["A new dependency adds maintenance"]},
        "episode_cost": {dimension: {"knowledge": "UNKNOWN", "quantity": None,
            "basis": "Full episode not measured: adoption, operation, maintenance, retries and recovery remain unpaired.",
            "evidence_refs": []} for dimension in _AOA_COST_DIMENSIONS},
        "relative_episode_cost": {"knowledge": "UNKNOWN", "relation": "UNKNOWN", "evidence_refs": [],
            "compared_with": [other for other in ("A", "B", "C") if other != identity],
            "basis": "Unmeasured relative episode cost; include Human, coordination and verification overhead."}
    } for identity in ("A", "B", "C")]
    if selected in ("B", "C"):
        for option in alternatives[:1 if selected == "B" else 2]:
            option.update(feasibility="INFEASIBLE", expected_outcome="Cannot meet the stated caller-context requirement in this modeled case.",
                          necessity="The bound simpler alternative lacks the required consumer edge; its existing shape cannot satisfy the objective.")
        alternatives[1 if selected == "B" else 2]["necessity"] = (
            "B carries the required context through the existing helper without another service." if selected == "B" else
            "Only C provides the required isolation in this modeled case; both simpler alternatives lack that Human-required boundary.")
    information = aoa_judgment("DISCRIMINATOR", subject, ("objective", "alternatives"))
    information.pop("id")
    information.update(question="Can existing explicit-context propagation meet the same outcome?",
                       decision_if_known="Choose A if existing propagation suffices; otherwise compare B before C.",
                       value_of_information="Inspect the bounded consumer edge before authorizing implementation; no numeric ROI assumed.")
    checks = [aoa_judgment(c["id"], subject, r) for c, r in zip(profile["passes"][0]["checks"], _AOA_CONSTRUCT_ROLES)]
    if any(c["outcome"] == "BLOCKER" for c in alternatives):
        checks[1].update(outcome="BLOCKER", knowledge="UNKNOWN")
    return {"subject_fingerprint": subject["subject_fingerprint"], "checks": checks, "alternatives": alternatives,
            "information": information, "draft_candidate": None if subject["blockers"] else aoa_candidate(selected)}


def aoa_final(context, profile, subject, first):
    from aios_renew.brain_audit import _AOA_CHALLENGE_ROLES
    cases = (
        "A already preserves context; building B or C would add no required useful outcome.",
        "B passes the existing value; C's new service adds a dependency without an unmet requirement.",
        "The declared transport consumer is covered while an omitted Runtime caller loses context.",
        "An apparent token saving omits Human approval, setup, retries and verification cost.",
        "A migration deletes the old path before Human accepts the irreversible recovery risk.",
        "A rehashed old main, fabricated selection or successful transport receipt is treated as architecture approval.",
    )
    challenges = [{**aoa_judgment(c["id"], subject, roles), "counterexample": case,
                   "option_ids": ["B", "C"] if i == 0 else ["C"] if i == 1 else ["A", "B", "C"],
                   "disposition": "REJECTED_WITH_BOUNDED_BASIS"}
                  for i, (c, roles, case) in enumerate(zip(profile["passes"][1]["checks"], _AOA_CHALLENGE_ROLES, cases))]
    for c in challenges:
        if c["outcome"] == "BLOCKER":
            c["disposition"] = "BLOCKER"
    prior = [(kind, c) for kind, entries in (("CHECK", first["checks"]), ("ALTERNATIVE", first["alternatives"]),
              ("INFORMATION", [{"id": "DISCRIMINATOR", **first["information"]}])) for c in entries if c["outcome"] == "BLOCKER"]
    known = first["checks"] + first["alternatives"] + [first["information"]] + challenges
    blocked = bool(subject["blockers"] or prior) or any(c["outcome"] == "BLOCKER" for c in challenges)
    candidate = None if blocked else deepcopy(first["draft_candidate"])
    return {"subject_fingerprint": subject["subject_fingerprint"], "construct_fingerprint": first["construct_fingerprint"],
            "challenges": challenges, "alternatives": deepcopy(first["alternatives"]), "information": deepcopy(first["information"]),
            "reconciliation": [{"kind": kind, "id": c["id"], "disposition": "UNRESOLVED", "knowledge": c["knowledge"],
                "basis": c["basis"], "evidence_refs": c["evidence_refs"], "changed_anchors": []} for kind, c in prior],
            "closure": {"outcome": "NO_DECISION" if blocked else "CANDIDATE",
                "knowledge": "UNKNOWN" if any(c["knowledge"] == "UNKNOWN" for c in known) else "INFERRED",
                "summary": "One bounded planning recommendation for Human; no implemented or empirically verified architecture claim.",
                "candidate_fingerprint": None if blocked else digest(candidate)}, "reconciled_candidate": candidate}


@pytest.mark.parametrize("selected", ("A", "B", "C", "DEFER"))
def test_aoa_exact_two_pass_comparison_can_recommend_each_choice_or_defer(aoa_profile, selected):
    body, context = aoa_example()
    if selected in ("B", "C"):
        context["alternatives"][0]["description"] = "The existing path cannot preserve context at the required consumer edge."
    if selected == "C":
        context["constraints"].append("The caller context requires an isolated boundary unavailable in A or B.")
        context["alternatives"][1]["description"] = "The existing helper cannot provide the required isolated boundary."
    subject = aoa_subject(body, aoa_profile, context)
    assert profile_ref(aoa_profile) == {"id": "aoa-1", "version": 1,
        "digest": "7789fcc53bd6593c8d190e867d14d9fe80060395ae20a0706981d16fec7e617b"}
    assert aoa_profile["applicable_flows"] == ["ARCHITECTURE"] and "lenses" not in aoa_profile
    assert [len(p["checks"]) for p in aoa_profile["passes"]] == [5, 6]
    first = construct_aoa(body, aoa_profile, subject, aoa_first(context, aoa_profile, subject, selected), context)
    assert "closure" not in first and "handoff_candidate" not in first
    final = validate_aoa(body, aoa_profile, subject, first, aoa_final(context, aoa_profile, subject, first), context)
    assert final["closure"]["outcome"] == "CANDIDATE"
    assert final["handoff_candidate"]["selected_alternative"] == selected
    assert all(cost["quantity"] is None and cost["knowledge"] == "UNKNOWN"
               for option in final["alternatives"] for cost in option["episode_cost"].values())
    with pytest.raises(BrainAuditError):
        construct_stage1(body, aoa_profile, aoa_candidate())


@pytest.mark.parametrize("missing", ("intent", "objective", "canonical", "authorization", "alternatives", "consumers", "provenance", "unauthenticated"))
def test_aoa_missing_or_unauthenticated_decisive_source_stays_unknown(aoa_profile, missing):
    body, context = aoa_example()
    if missing == "intent":
        body["human_input"] = None
    elif missing == "canonical":
        body["canonical_blocker"] = {"code": "SOURCE_CONFLICT"}
    elif missing == "unauthenticated":
        context["provenance"]["status"] = "UNKNOWN"
    else:
        context[missing] = [] if missing in ("alternatives", "consumers") else None
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    subject = aoa_subject(body, aoa_profile, context)
    material = aoa_first(context, aoa_profile, subject)
    first = construct_aoa(body, aoa_profile, subject, material, context)
    final = validate_aoa(body, aoa_profile, subject, first, aoa_final(context, aoa_profile, subject, first), context)
    assert final["closure"]["knowledge"] == "UNKNOWN" and final["handoff_candidate"] is None
    next(c for c in material["checks"] if c["outcome"] == "BLOCKER").update(outcome="CLEAR", knowledge="OBSERVED")
    with pytest.raises(BrainAuditError):
        construct_aoa(body, aoa_profile, subject, material, context)


@pytest.mark.parametrize("fault", ("quantity", "observed_without_evidence", "wrong_option", "relabel_estimate", "forged_ref", "unknown_evidence"))
def test_aoa_costs_cannot_invent_estimates_or_upgrade_supplied_attribution(aoa_profile, fault):
    body, context = aoa_example()
    context["evidence"] = [{"id": "bounded-estimate", "knowledge": "INFERRED", "basis": "Synthetic bounded estimate for one episode.",
                            "measurements": [{"option_id": "A", "dimension": "tokens", "quantity": 100}]}]
    if fault == "unknown_evidence":
        context["evidence"][0]["knowledge"] = "UNKNOWN"
        with pytest.raises(BrainAuditError):
            aoa_subject(body, aoa_profile, context)
        return
    subject = aoa_subject(body, aoa_profile, context)
    material = aoa_first(context, aoa_profile, subject)
    cost = material["alternatives"][0]["episode_cost"]["tokens"]
    cost.update(knowledge="INFERRED", quantity=100, evidence_refs=[subject["evidence"][0]["ref"]])
    assert construct_aoa(body, aoa_profile, subject, material, context)["alternatives"][0]["episode_cost"]["tokens"] == cost
    if fault == "quantity":
        cost["quantity"] = 99
    elif fault == "observed_without_evidence":
        cost.update(knowledge="OBSERVED", evidence_refs=[])
    elif fault == "wrong_option":
        material["alternatives"][1]["episode_cost"]["tokens"] = deepcopy(cost)
    elif fault == "relabel_estimate":
        cost["knowledge"] = "OBSERVED"
    else:
        cost["evidence_refs"] = [{"path": "forged", "digest": "f" * 64}]
    with pytest.raises(BrainAuditError):
        construct_aoa(body, aoa_profile, subject, material, context)


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("knowledge", ("INFERRED", "UNKNOWN"))
def test_aoa_all_clear_second_pass_cannot_erase_first_pass_blockers(aoa_profile, index, knowledge):
    body, context = aoa_example()
    subject = aoa_subject(body, aoa_profile, context)
    material = aoa_first(context, aoa_profile, subject)
    material["checks"][index].update(outcome="BLOCKER", knowledge=knowledge, basis="An unresolved material threat or hidden fixed cost remains.")
    first = construct_aoa(body, aoa_profile, subject, material, context)
    second = aoa_final(context, aoa_profile, subject, first)
    assert all(c["outcome"] == "CLEAR" for c in second["challenges"])
    assert validate_aoa(body, aoa_profile, subject, first, second, context)["closure"]["outcome"] == "NO_DECISION"
    second["reconciliation"] = []
    second["reconciled_candidate"] = aoa_candidate()
    second["closure"].update(outcome="CANDIDATE", knowledge="INFERRED", candidate_fingerprint=digest(aoa_candidate()))
    with pytest.raises(BrainAuditError):
        validate_aoa(body, aoa_profile, subject, first, second, context)


def test_aoa_material_missing_information_and_unaccepted_irreversible_risk_block(aoa_profile):
    body, context = aoa_example()
    subject = aoa_subject(body, aoa_profile, context)
    for risk in (False, True):
        material = aoa_first(context, aoa_profile, subject)
        if risk:
            material["alternatives"][2].update(outcome="BLOCKER", risk_acceptance_required=["Irreversible old-path deletion"])
            material["checks"][1].update(outcome="BLOCKER")
        else:
            material["information"].update(outcome="BLOCKER", knowledge="UNKNOWN", basis="The decisive consumer requirement is unknown.")
            material["checks"][3].update(outcome="BLOCKER", knowledge="UNKNOWN")
        first = construct_aoa(body, aoa_profile, subject, material, context)
        final = validate_aoa(body, aoa_profile, subject, first, aoa_final(context, aoa_profile, subject, first), context)
        assert final["closure"]["outcome"] == "NO_DECISION" and final["handoff_candidate"] is None


def test_aoa_lawful_grounded_reconciliation_resolves_a_concrete_risk(aoa_profile):
    body, context = aoa_example()
    subject = aoa_subject(body, aoa_profile, context)
    material = aoa_first(context, aoa_profile, subject)
    material["checks"][4].update(outcome="BLOCKER", basis="C has no bounded rollback proposal.")
    first = construct_aoa(body, aoa_profile, subject, material, context)
    second = aoa_final(context, aoa_profile, subject, first)
    second["alternatives"][2]["reversibility"] = "Retain the old path; stop before migration until Human decides risk."
    second["reconciliation"][0].update(disposition="RESOLVED", changed_anchors=["alternatives.2.reversibility"],
                                       basis="The authorized planning scope allows retaining the old path and deferring migration.")
    second["challenges"][4]["disposition"] = "ADDRESSED_BY_RECONCILIATION"
    second["reconciled_candidate"] = aoa_candidate()
    second["closure"].update(outcome="CANDIDATE", candidate_fingerprint=digest(aoa_candidate()))
    assert validate_aoa(body, aoa_profile, subject, first, second, context)["closure"]["outcome"] == "CANDIDATE"
    second["reconciliation"][0]["changed_anchors"] = ["alternatives.2.necessity"]
    with pytest.raises(BrainAuditError):
        validate_aoa(body, aoa_profile, subject, first, second, context)


@pytest.mark.parametrize("index", range(6))
def test_aoa_adversarial_challenges_can_reject_dominated_builds_and_hidden_costs(aoa_profile, index):
    body, context = aoa_example()
    subject = aoa_subject(body, aoa_profile, context)
    first = construct_aoa(body, aoa_profile, subject, aoa_first(context, aoa_profile, subject, "C"), context)
    second = aoa_final(context, aoa_profile, subject, first)
    second["challenges"][index].update(outcome="BLOCKER", disposition="BLOCKER")
    second["reconciled_candidate"] = None
    second["closure"].update(outcome="NO_DECISION", candidate_fingerprint=None)
    assert validate_aoa(body, aoa_profile, subject, first, second, context)["handoff_candidate"] is None


@pytest.mark.parametrize("fault", ("scope", "stale_main", "wrong_repository", "stale_context", "wrong_flow", "binding", "profile", "duplicate", "extra_stage", "empty_falsifier"))
def test_aoa_scope_identity_profile_and_focused_pass_substitutions_fail_closed(aoa_profile, fault):
    body, context = aoa_example()
    profile = deepcopy(aoa_profile)
    if fault in ("stale_main", "wrong_repository", "stale_context"):
        context["provenance"][{"stale_main": "main_sha", "wrong_repository": "repository", "stale_context": "work_context_fingerprint"}[fault]] = (
            "f" * 40 if fault == "stale_main" else "other" if fault == "wrong_repository" else "f" * 64)
    elif fault == "wrong_flow":
        body["selected_flow"] = "TASK_AUTHORING"
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    elif fault == "profile":
        profile["passes"][0]["checks"][0]["check"] += " Reminted."
    elif fault == "extra_stage":
        profile["procedure"]["stages"].append("EXTRA")
    if fault in ("stale_main", "wrong_repository", "stale_context", "wrong_flow", "profile", "extra_stage", "binding"):
        with pytest.raises(BrainAuditError):
            construct_aoa_subject(body, profile, {"selected_flow": "ARCHITECTURE", "version": 1, "digest": "c" * 64},
                                  {"task_id": "TASK-999"} if fault == "binding" else {}, context)
        return
    subject = aoa_subject(body, profile, context)
    material = aoa_first(context, profile, subject)
    if fault == "scope":
        material["alternatives"][0]["planning_scope"].append("new authority")
    elif fault == "duplicate":
        material["alternatives"][1]["id"] = "A"
    else:
        first = construct_aoa(body, profile, subject, material, context)
        final = aoa_final(context, profile, subject, first)
        final["challenges"][0]["counterexample"] = ""
        with pytest.raises(BrainAuditError):
            validate_aoa(body, profile, subject, first, final, context)
        return
    with pytest.raises(BrainAuditError):
        construct_aoa(body, profile, subject, material, context)


@pytest.mark.parametrize("private", ({"secret": "x"}, {"human_v1_option": "HUMAN"}, {"aoa_subject": {}},
    {"task_source_context": {}}, {"format": "AIOS_AOA_SUBJECT"}, {"provider": "x"}, {"note": "C:/private/log"}))
def test_aoa_context_and_candidate_exclude_private_and_nested_option_payloads(aoa_profile, private):
    body, context = aoa_example()
    bad = deepcopy(context)
    bad["evidence"] = [{"id": "bad", "knowledge": "INFERRED", "basis": private, "measurements": []}]
    with pytest.raises(BrainAuditError):
        aoa_subject(body, aoa_profile, bad)
    subject = aoa_subject(body, aoa_profile, context)
    material = aoa_first(context, aoa_profile, subject)
    material["draft_candidate"]["uncertainties"] = [private]
    with pytest.raises(BrainAuditError):
        construct_aoa(body, aoa_profile, subject, material, context)


@pytest.mark.parametrize("fault", ("relative_claim", "missing_dimension", "unknown_quantity", "oversized_basis", "extra_check", "too_many_sources", "depth"))
def test_aoa_cost_completeness_and_closed_size_bounds(aoa_profile, fault):
    body, context = aoa_example()
    if fault == "too_many_sources":
        context["evidence"] = [{"id": str(i), "knowledge": "UNKNOWN", "basis": "Unavailable", "measurements": []} for i in range(17)]
        with pytest.raises(BrainAuditError):
            aoa_subject(body, aoa_profile, context)
        return
    subject = aoa_subject(body, aoa_profile, context)
    material = aoa_first(context, aoa_profile, subject)
    if fault == "relative_claim":
        material["alternatives"][0]["relative_episode_cost"].update(knowledge="OBSERVED", relation="LOWER")
    elif fault == "missing_dimension":
        material["alternatives"][0]["episode_cost"].pop("verification_minutes")
    elif fault == "unknown_quantity":
        material["alternatives"][0]["episode_cost"]["tokens"]["quantity"] = 0
    elif fault == "oversized_basis":
        material["checks"][0]["basis"] = "x" * 2049
    elif fault == "extra_check":
        material["checks"].append(deepcopy(material["checks"][0]))
    else:
        nested = "x"
        for _ in range(33):
            nested = [nested]
        material["draft_candidate"]["uncertainties"] = nested
    with pytest.raises(BrainAuditError):
        construct_aoa(body, aoa_profile, subject, material, context)


@pytest.fixture
def tca_profile():
    return next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"] if p["id"] == "tca-1")


def tca_example():
    from tests.test_decision_packet import task
    proposal = task()
    proposal.update(goal="Preserve caller-selected context in the task consumer",
                    problem="Ambient rediscovery loses the explicit caller selection")
    proposal["verification"]["policy"] = "minimum-sufficient-v1"
    proposal["acceptance"] = [
        {"id": "AC1", "condition": "Runtime consumer retains the supplied caller context"},
        {"id": "AC2", "condition": "Transport consumer preserves the same context through handoff"},
    ]
    context = {"task": proposal, "authorization": {k: deepcopy(proposal[k]) for k in ("scope", "constraints", "non_goals")},
               "consumers": [{"id": "runtime", "authority": "RUNTIME", "requirement": "Consume the explicit caller context"},
                             {"id": "transport", "authority": "TRANSPORT", "requirement": "Preserve context through handoff"}]}
    body = packet("TASK_AUTHORING")
    body.update(decision_family_ref="task.validate_task+authoring_ingress", handoff_target="AUTHORING_INGRESS",
                expected_return_shape="TASK_AUTHORING_PROPOSAL",
                human_input="Preserve explicit caller-selected context in Runtime and transport; no lifecycle mutation.")
    body["canonical_facts"] = {"repository": "AIOS-renew", "main_sha": "b" * 40,
                               "roadmap": {"next_items": ["task-context"]}, "selected_task": None}
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    return body, context


def tca_subject(body, profile, context):
    return construct_tca_subject(body, profile, {"selected_flow": "TASK_AUTHORING", "version": 1, "digest": "c" * 64},
                                 {"task_id": context["task"]["task_id"] if context["task"] is not None else "TASK-002",
                                  "revision": context["task"]["revision"] if context["task"] is not None else 1}, context)


def tca_judgment(identity, subject, roles):
    if subject["audit_profile_ref"]["id"] == "tca-2":
        roles = tuple(dict.fromkeys((*roles, "implementation", "authorization")))
    missing = subject["sources"]["canonical"] is None or any(subject["sources"][r] is None for r in roles)
    return {"id": identity, "outcome": "BLOCKER" if missing else "CLEAR",
            "knowledge": "UNKNOWN" if missing else "INFERRED",
            "basis": "Missing decisive source." if missing else
                     "The bound Human request and TASK preserve explicit caller context across the authorized consumer edge.",
            "evidence_refs": [deepcopy(subject["sources"][r]) for r in roles if subject["sources"][r] is not None]}


def tca_acceptance(task, subject):
    return [{**tca_judgment(c["id"], subject, ("task",)), "condition": c["condition"], "phase": "CLAIM_NOW",
             "implementation_property": c["condition"],
             "falsifying_counterexample": "A consumer rediscovering ambient context loses the caller's explicit value."}
            for c in ([] if task is None else task["acceptance"])]


def tca_consumers(task, context, subject):
    return [{**tca_judgment(c["id"], subject, ("task", "consumers")),
             "acceptance_ids": [] if task is None else [task["acceptance"][min(i, len(task["acceptance"]) - 1)]["id"]]}
            for i, c in enumerate(context["consumers"])]


def tca_first(context, profile, subject):
    roles = (("intent", "task"), ("intent", "task"), ("task",), ("task", "authorization"), ("task", "consumers"))
    return {"subject_fingerprint": subject["subject_fingerprint"],
            "checks": [tca_judgment(c["id"], subject, r) for c, r in zip(profile["passes"][0]["checks"], roles)],
            "acceptance_checks": tca_acceptance(context["task"], subject),
            "consumer_checks": tca_consumers(context["task"], context, subject)}


def tca_final(context, profile, subject, first):
    roles = (("task",), ("task", "consumers"), ("task", "authorization", "intent"),
             ("task", "authorization"), ("task", "consumers"), ("canonical", "intent", "authorization"))
    cases = (
        "A TASK can pass its file-presence criteria while Runtime still reads ambient context.",
        "The Runtime path can preserve context while transport drops it at the shared helper.",
        "An added unrelated file or contradictory non-goal can hide a new product requirement.",
        "An Executor claiming TEST PASS before Runtime verification merges authority and reverses proof order.",
        "A happy-path test alone misses a conflicting caller value at the transport boundary.",
        "A rehashed old packet or substituted profile can hide a moved canonical main or different Human intent.",
    )
    challenges = [{**tca_judgment(c["id"], subject, r), "counterexample": example,
                   "disposition": "BLOCKER" if tca_judgment(c["id"], subject, r)["outcome"] == "BLOCKER"
                   else "REJECTED_WITH_BOUNDED_BASIS"}
                  for c, r, example in zip(profile["passes"][1]["checks"], roles, cases)]
    prior = [(kind, c) for kind, field in (("CHECK", "checks"), ("ACCEPTANCE", "acceptance_checks"),
                                          ("CONSUMER", "consumer_checks")) for c in first[field] if c["outcome"] == "BLOCKER"]
    blocked = bool(prior) or any(c["outcome"] == "BLOCKER" for c in challenges)
    candidate = None if blocked else deepcopy(context["task"])
    known = [c for f in ("checks", "acceptance_checks", "consumer_checks") for c in first[f]] + challenges
    return {"subject_fingerprint": subject["subject_fingerprint"], "construct_fingerprint": first["construct_fingerprint"],
            "challenges": challenges, "acceptance_checks": tca_acceptance(context["task"], subject),
            "consumer_checks": tca_consumers(context["task"], context, subject),
            "acceptance_accounting": [{"id": c["id"], "disposition": "RETAINED", "final_ids": [c["id"]],
                                       "candidate_anchors": [], "basis": "Exact original condition retained.",
                                       "evidence_refs": tca_judgment(c["id"], subject, ("task",))["evidence_refs"]} for c in (
                                           [] if context["task"] is None else context["task"]["acceptance"])],
            "reconciliation": [{"kind": kind, "id": c["id"], "disposition": "UNRESOLVED", "knowledge": c["knowledge"],
                                "basis": c["basis"], "evidence_refs": c["evidence_refs"], "candidate_anchors": []} for kind, c in prior],
            "closure": {"outcome": "NO_DECISION" if blocked else "CANDIDATE",
                        "knowledge": "UNKNOWN" if any(c["knowledge"] == "UNKNOWN" for c in known) else "INFERRED",
                        "summary": "One bounded semantic TASK closure; Runtime and Reviewer retain proof and verdict.",
                        "candidate_fingerprint": None if blocked else digest(candidate)}, "reconciled_candidate": candidate}


def tca_block_final(material, index, basis):
    material["challenges"][index].update(outcome="BLOCKER", disposition="BLOCKER", basis=basis)
    material["reconciled_candidate"] = None
    material["closure"].update(outcome="NO_DECISION", candidate_fingerprint=None)


def test_tca_exact_task_specific_profile_and_valid_proposal(tca_profile):
    assert profile_ref(tca_profile) == {"id": "tca-1", "version": 1,
        "digest": "1fa1d5fd098494eeb5cb97665f40e7556969555aed552dfc7fa28871555884a2"}
    assert tca_profile["applicable_flows"] == ["TASK_AUTHORING"]
    assert "lenses" not in tca_profile and [len(p["checks"]) for p in tca_profile["passes"]] == [5, 6]
    body, context = tca_example()
    subject = tca_subject(body, tca_profile, context)
    first = construct_tca(body, tca_profile, subject, tca_first(context, tca_profile, subject), context)
    assert "closure" not in first and "handoff_candidate" not in first
    final = validate_tca(body, tca_profile, subject, first, tca_final(context, tca_profile, subject, first), context)
    assert final["closure"]["outcome"] == "CANDIDATE" and final["handoff_candidate"] == context["task"]
    assert subject["human_intent"] == body["human_input"] and not subject["blockers"]
    with pytest.raises(BrainAuditError):
        construct_stage1(body, tca_profile, context["task"])


@pytest.mark.parametrize("fault", ("missing", "duplicate", "extra", "condition", "phase", "no_property", "no_falsifier"))
@pytest.mark.parametrize("stage", ("construct", "reconcile"))
def test_tca_acceptance_is_exact_independently_claimable_and_falsifiable(tca_profile, fault, stage):
    body, context = tca_example()
    subject = tca_subject(body, tca_profile, context)
    first = construct_tca(body, tca_profile, subject, tca_first(context, tca_profile, subject), context)
    material = tca_first(context, tca_profile, subject) if stage == "construct" else tca_final(context, tca_profile, subject, first)
    checks = material["acceptance_checks"]
    if fault == "missing":
        checks.pop()
    elif fault == "duplicate":
        checks[1] = deepcopy(checks[0])
    elif fault == "extra":
        checks.append(deepcopy(checks[0]))
    elif fault == "condition":
        checks[0]["condition"] = "Runtime verification passed"
    elif fault == "phase":
        checks[0]["phase"] = "PROOF_LATER"
    else:
        checks[0]["implementation_property" if fault == "no_property" else "falsifying_counterexample"] = ""
    with pytest.raises(BrainAuditError):
        if stage == "construct":
            construct_tca(body, tca_profile, subject, material, context)
        else:
            validate_tca(body, tca_profile, subject, first, material, context)


@pytest.mark.parametrize("phase", ("runtime_result", "evidence", "review", "publication"))
def test_tca_proof_later_remains_no_decision_until_lawfully_accounted_out(tca_profile, phase):
    body, context = tca_example()
    condition = {
        "runtime_result": "Runtime verification reports PASS", "evidence": "Canonical EVIDENCE proves success",
        "review": "Reviewer issues PASS", "publication": "Publisher publishes and advances the roadmap"}[phase]
    context["task"]["acceptance"].append({"id": "AC3", "condition": condition})
    subject = tca_subject(body, tca_profile, context)
    material = tca_first(context, tca_profile, subject)
    material["acceptance_checks"][2].update(phase="PROOF_LATER", outcome="BLOCKER", basis="This fact belongs to a later authority.")
    material["checks"][2].update(outcome="BLOCKER", basis="AC3 is not claimable by an Executor before verification.")
    first = construct_tca(body, tca_profile, subject, material, context)
    material = tca_final(context, tca_profile, subject, first)
    material["acceptance_checks"][2].update(phase="PROOF_LATER", outcome="BLOCKER")
    assert validate_tca(body, tca_profile, subject, first, material, context)["closure"]["outcome"] == "NO_DECISION"
    candidate = deepcopy(context["task"])
    candidate["acceptance"].pop()
    candidate["constraints"]["hard"].append("Runtime owns verification/EVIDENCE, Reviewer verdict and Publisher publication remain later facts.")
    anchor = f"constraints.hard.{len(candidate['constraints']['hard']) - 1}"
    material.update(reconciled_candidate=candidate, acceptance_checks=tca_acceptance(candidate, subject),
                    consumer_checks=tca_consumers(candidate, context, subject))
    material["acceptance_accounting"][2].update(disposition="MOVED_TO_CONSTRAINT", final_ids=[], candidate_anchors=[anchor],
        basis="Preserve the later authority's obligation as a hard boundary, outside Executor acceptance.")
    for record in material["reconciliation"]:
        record.update(disposition="RESOLVED", knowledge="INFERRED", candidate_anchors=[anchor],
                      basis="The AC3 later-phase obligation is now an authority constraint; AC1/AC2 retain both consumers.")
    material["challenges"][3]["disposition"] = "ADDRESSED_BY_RECONCILIATION"
    material["closure"].update(outcome="CANDIDATE", candidate_fingerprint=digest(candidate))
    final = validate_tca(body, tca_profile, subject, first, material, context)
    assert [c["id"] for c in final["handoff_candidate"]["acceptance"]] == ["AC1", "AC2"]
    for bad in ("accounting", "unresolved", "unchanged_anchor", "forged_source"):
        invalid = deepcopy(material)
        if bad == "accounting":
            invalid["acceptance_accounting"].pop()
        elif bad == "unresolved":
            invalid["reconciliation"][0].update(disposition="UNRESOLVED", candidate_anchors=[])
        elif bad == "unchanged_anchor":
            invalid["reconciliation"][0]["candidate_anchors"] = ["goal"]
        else:
            invalid["reconciliation"][0]["evidence_refs"][0]["digest"] = "f" * 64
        with pytest.raises(BrainAuditError):
            validate_tca(body, tca_profile, subject, first, invalid, context)


@pytest.mark.parametrize("index,case", (
    (0, "File-presence AC allows empty implementation and circular success claims."),
    (1, "A hidden transport consumer loses the context while declared Runtime coverage is clear."),
    (2, "Scope requires lifecycle mutation while the non-goal forbids it; extra service is hidden intent."),
    (3, "Executor-specific commands and a self-reported PASS impersonate Human and Runtime authority."),
    (4, "No conflicting-value test can falsify the claimed transport preservation."),
    (5, "Current main moved; old packet observations cannot authorize the current TASK."),
))
def test_tca_independent_counterexamples_block_false_completion(tca_profile, index, case):
    body, context = tca_example()
    subject = tca_subject(body, tca_profile, context)
    first = construct_tca(body, tca_profile, subject, tca_first(context, tca_profile, subject), context)
    material = tca_final(context, tca_profile, subject, first)
    tca_block_final(material, index, case)
    final = validate_tca(body, tca_profile, subject, first, material, context)
    assert final["closure"]["outcome"] == "NO_DECISION" and final["handoff_candidate"] is None
    material["closure"]["outcome"] = "CANDIDATE"
    with pytest.raises(BrainAuditError):
        validate_tca(body, tca_profile, subject, first, material, context)


def test_tca_unrelated_constraint_cannot_relabel_unchanged_proof_later_acceptance(tca_profile):
    body, context = tca_example()
    context["task"]["acceptance"].append({"id": "AC3", "condition": "Runtime reports verification PASS"})
    subject = tca_subject(body, tca_profile, context)
    material = tca_first(context, tca_profile, subject)
    material["checks"][2]["outcome"] = "BLOCKER"
    material["acceptance_checks"][2].update(outcome="BLOCKER", phase="PROOF_LATER")
    first = construct_tca(body, tca_profile, subject, material, context)
    material = tca_final(context, tca_profile, subject, first)
    candidate = deepcopy(context["task"])
    candidate["constraints"]["hard"].append("Runtime retains verification authority")
    anchor = f"constraints.hard.{len(candidate['constraints']['hard']) - 1}"
    for record in material["reconciliation"]:
        record.update(disposition="RESOLVED", knowledge="INFERRED", candidate_anchors=[anchor])
    material["challenges"][3]["disposition"] = "ADDRESSED_BY_RECONCILIATION"
    material["reconciled_candidate"] = candidate
    material["closure"].update(outcome="CANDIDATE", candidate_fingerprint=digest(candidate))
    assert all(c["phase"] == "CLAIM_NOW" for c in material["acceptance_checks"])
    with pytest.raises(BrainAuditError, match="never merely relabeled"):
        validate_tca(body, tca_profile, subject, first, material, context)


def test_tca_conflicting_authorized_scope_has_no_falsely_sufficient_candidate(tca_profile):
    body, context = tca_example()
    context["task"]["scope"]["modify"].append("src/aios_renew/task.py")
    context["task"]["non_goals"].append("Do not change the TASK parser")
    subject = tca_subject(body, tca_profile, context)
    material = tca_first(context, tca_profile, subject)
    material["checks"][3].update(outcome="BLOCKER", basis="The proposal includes task.py outside Human scope and contradicts its parser non-goal.")
    first = construct_tca(body, tca_profile, subject, material, context)
    final = tca_final(context, tca_profile, subject, first)
    assert validate_tca(body, tca_profile, subject, first, final, context)["closure"]["outcome"] == "NO_DECISION"
    # Even an optimistic model cannot expose the unmodified out-of-authority TASK.
    clean = construct_tca(body, tca_profile, subject, tca_first(context, tca_profile, subject), context)
    with pytest.raises(BrainAuditError, match="authorized scope"):
        validate_tca(body, tca_profile, subject, clean, tca_final(context, tca_profile, subject, clean), context)


@pytest.mark.parametrize("fault", ("duplicate_ac", "missing_ac", "consumer", "unbounded_scope"))
def test_tca_malformed_source_cannot_create_a_new_subject(tca_profile, fault):
    body, context = tca_example()
    if fault == "duplicate_ac":
        context["task"]["acceptance"][1]["id"] = "AC1"
    elif fault == "missing_ac":
        context["task"]["acceptance"].clear()
    elif fault == "consumer":
        context["consumers"].append(deepcopy(context["consumers"][0]))
    else:
        context["authorization"]["scope"]["modify"] = ["src/**"]
    with pytest.raises(BrainAuditError):
        tca_subject(body, tca_profile, context)


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("knowledge", ("OBSERVED", "INFERRED", "UNKNOWN"))
def test_tca_clear_second_pass_cannot_erase_first_pass_blocker(tca_profile, index, knowledge):
    body, context = tca_example()
    subject = tca_subject(body, tca_profile, context)
    material = tca_first(context, tca_profile, subject)
    material["checks"][index].update(outcome="BLOCKER", knowledge=knowledge, basis="Material intent or scope ambiguity remains unresolved.")
    first = construct_tca(body, tca_profile, subject, material, context)
    material = tca_final(context, tca_profile, subject, first)
    assert all(c["outcome"] == "CLEAR" for c in material["challenges"])
    assert validate_tca(body, tca_profile, subject, first, material, context)["closure"]["outcome"] == "NO_DECISION"
    material["reconciliation"] = []
    material["reconciled_candidate"] = context["task"]
    material["closure"].update(outcome="CANDIDATE", candidate_fingerprint=digest(context["task"]))
    with pytest.raises(BrainAuditError):
        validate_tca(body, tca_profile, subject, first, material, context)


@pytest.mark.parametrize("missing", ("intent", "task", "authorization", "consumers", "canonical"))
def test_tca_missing_decisive_sources_cannot_be_declared_clear(tca_profile, missing):
    body, context = tca_example()
    if missing == "intent":
        body["human_input"] = None
    elif missing == "canonical":
        body["canonical_facts"].pop("main_sha")
    else:
        context[missing] = [] if missing == "consumers" else None
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    subject = tca_subject(body, tca_profile, context)
    material = tca_first(context, tca_profile, subject)
    first = construct_tca(body, tca_profile, subject, material, context)
    final = validate_tca(body, tca_profile, subject, first, tca_final(context, tca_profile, subject, first), context)
    assert final["closure"]["outcome"] == "NO_DECISION" and final["closure"]["knowledge"] == "UNKNOWN"
    for check in material["checks"]:
        check.update(outcome="CLEAR", knowledge="OBSERVED")
    with pytest.raises(BrainAuditError):
        construct_tca(body, tca_profile, subject, material, context)


@pytest.mark.parametrize("fault", ("scope", "goal", "constraints", "non_goals", "verification", "consumer", "counterexample", "basis", "evidence"))
def test_tca_final_candidate_and_consumer_bounds_fail_closed(tca_profile, fault):
    body, context = tca_example()
    subject = tca_subject(body, tca_profile, context)
    first = construct_tca(body, tca_profile, subject, tca_first(context, tca_profile, subject), context)
    material = tca_final(context, tca_profile, subject, first)
    candidate = material["reconciled_candidate"]
    if fault == "scope":
        candidate["scope"]["modify"].append("src/aios_renew/task.py")
    elif fault == "goal":
        candidate["goal"] = "Hidden new intent"
    elif fault in ("constraints", "non_goals"):
        candidate[fault]["hard"].clear() if fault == "constraints" else candidate[fault].clear()
    elif fault == "verification":
        candidate["verification"]["required"] = ["echo success"]
    elif fault == "consumer":
        material["consumer_checks"][1]["acceptance_ids"] = []
    elif fault == "counterexample":
        material["challenges"][0]["counterexample"] = ""
    elif fault == "basis":
        material["challenges"][0]["basis"] = "x" * 2049
    else:
        material["challenges"][0]["evidence_refs"][0]["digest"] = "f" * 64
    material["closure"]["candidate_fingerprint"] = digest(candidate)
    with pytest.raises(BrainAuditError):
        validate_tca(body, tca_profile, subject, first, material, context)


@pytest.mark.parametrize("fault", ("profile", "digest", "flow", "stage", "version", "bounds", "extra_check"))
def test_tca_profile_cannot_be_reminted_or_recomposed(tca_profile, fault):
    bad = deepcopy(tca_profile)
    if fault == "profile":
        bad["id"] = "tca-2"
    elif fault == "digest":
        bad["passes"][0]["checks"][0]["check"] += " Trust all output."
    elif fault == "flow":
        bad["applicable_flows"].append("REPAIR_AUTHORING")
    elif fault == "stage":
        bad["passes"].reverse()
    elif fault == "version":
        bad["version"] = True
    elif fault == "bounds":
        bad["bounds"]["consumer_count"] += 1
    else:
        bad["passes"][0]["checks"].append(deepcopy(bad["passes"][0]["checks"][0]))
    with pytest.raises(BrainAuditError):
        normalize_profile(bad)


@pytest.mark.parametrize("private", ({"model": "private"}, {"raw_logs": "capture"},
    {"format": "AIOS_TCA_SUBJECT"}, {"human_v1_option": {}}, {"note": "C:/private/capture"}))
def test_tca_source_and_judgments_exclude_private_and_nested_payloads(tca_profile, private):
    body, context = tca_example()
    context["task"]["assumptions"].append(private)
    with pytest.raises(BrainAuditError):
        tca_subject(body, tca_profile, context)
    body, context = tca_example()
    subject = tca_subject(body, tca_profile, context)
    material = tca_first(context, tca_profile, subject)
    material["checks"][0]["basis"] = private
    with pytest.raises(BrainAuditError):
        construct_tca(body, tca_profile, subject, material, context)


@pytest.fixture
def moa_profile():
    return next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"] if p["id"] == "moa-1")


def moa_packet(*, frontier=False, action="CODE_FIX", prior_kind=None):
    """Use the existing compiler; no new Decision Packet field or target selector."""
    from tests.test_decision_packet import authoring, context, item
    from aios_renew.decision_packet import compile_decision_packet

    items = [item("RUN-002-001", "REVIEW-002-001", "F1")]
    items[0]["finding"]["action"] = action
    if frontier:
        items.append(item("RUN-002-002", "REVIEW-002-002", "F2", "b" * 40))
    work, flow, material = authoring(items, selected=not frontier)
    contract = material["task"]
    contract["verification"]["policy"] = "minimum-sufficient-v1"
    identities = [{"source_run_id": i["source_run_id"], "review_id": i["review_id"],
                   "finding_id": i["finding"]["id"], "reviewed_sha": i["reviewed_sha"]} for i in items]
    correction = {"policy": "EXACT_CORRECTION_SUBJECT_LINEAGE_V1", "operation": "AUTHOR_REMEDIATION",
        "selectors": {"source_run_id": items[0]["source_run_id"], "finding_id": "F1"},
        "task": {"id": "TASK-002", "revision": 1}, "artifacts_sha": "c" * 40,
        **identities[0], "review_decision_sha": "d" * 40,
        "outstanding_reviews": [{**identity, "review_decision_sha": str(index + 4) * 40}
                                for index, identity in enumerate(identities)]}
    correction["review_decision_sha"] = correction["outstanding_reviews"][0]["review_decision_sha"]
    work, flow = context("AUTHOR_REMEDIATION", work.canonical_observation["unified_state"], correction_subject=correction)
    canonical = compile_decision_packet(work, flow, material).as_dict()
    records = []
    for index, entry in enumerate(items):
        prior = None
        if prior_kind == "REPAIR":
            prior = {"kind": "REPAIR", "authorization_sha": "e" * 40, "failed_run_id": "RUN-002-000", "failed_head_sha": "f" * 40,
                     "authorization": {"failed_run_id": "RUN-002-000", "failed_head_sha": "f" * 40}}
        elif prior_kind == "REMEDIATION":
            prior = {"kind": "REMEDIATION", "authorization_sha": "e" * 40,
                     "predecessor": {**identities[0], "finding_id": "F0", "reviewed_sha": "f" * 40},
                     "authorization": {"finding_id": "F0", "reviewed_sha": "f" * 40}}
        review = {"review_id": entry["review_id"], "reviewed_sha": entry["reviewed_sha"],
                  "mode": "DELTA" if prior_kind == "REMEDIATION" else "PRIMARY", "verdict": "CHANGES_REQUIRED",
                  "acceptance": {"AC1": "FAIL", "AC2": "PASS"}, "findings": [deepcopy(entry["finding"])]}
        if prior_kind == "REMEDIATION":
            review["prior_finding_id"] = "F0"
        records.append({"run": {"run_id": entry["source_run_id"], "task": correction["task"],
                                "head_sha": entry["reviewed_sha"], "kind": prior_kind or "PRIMARY", "prior_correction": prior},
                        "artifacts_sha": "c" * 40, "review_decision_sha": correction["outstanding_reviews"][index]["review_decision_sha"],
                        "review": review})
    canonical["packet_fingerprint"] = digest({k: v for k, v in canonical.items() if k != "packet_fingerprint"})
    return canonical, {"reviews": records}


def moa_subject(body, profile, sources):
    return construct_moa_subject(body, profile, {"selected_flow": "REMEDIATION_AUTHORING", "version": 1,
                                                "digest": "a" * 64}, {}, sources)


def moa_candidate(body):
    target = body["subject"]
    task = body["canonical_facts"]["task_contract"]
    return {**{k: target[k] for k in ("finding_id", "action", "reviewed_sha")},
            "modification_scope": [] if target["action"] == "EVIDENCE_ONLY" else list(task["scope"]["modify"]),
            "verification": {"policy": task["verification"]["policy"], "affected": list(task["verification"]["required"])},
            "constraints": deepcopy(task["constraints"])}


def moa_checks(subject, profile, *, challenge=False):
    roles = (("review", "lineage"), ("finding", "review"), ("review", "task"), ("task", "lineage")) if challenge else (
        ("finding", "review", "lineage"), ("finding", "review"), ("task", "lineage"))
    counterexamples = (
        "Another REVIEW can reuse F1 at a different SHA; compare all four identity fields and immutable REVIEW refs.",
        "A proof-only response cannot repair a CODE_FIX finding; preserve the exact action and expected behavior.",
        "Fixing F1 by changing an unrelated authorized file can break F2; restrict the supporting delta and challenge shared cause.",
        "A moved authority ref or a second V1 audit cannot authorize this proposal; require one exact current two-pass lineage.",
    )
    results = []
    for index, (spec, required) in enumerate(zip(profile["passes"][int(challenge)]["checks"], roles)):
        missing = any(subject["sources"][role] is None for role in required)
        missing = missing or challenge and index == 3 and subject["kind"] == "CORRECTION_FRONTIER"
        result = {"id": spec["id"], "outcome": "BLOCKER" if missing else "CLEAR",
                  "knowledge": "UNKNOWN" if missing else "INFERRED" if index == 1 else "OBSERVED",
                  "basis": "Missing exact support/target; await canonical context." if missing else
                           "Bound finding issue and expected behavior support this minimum delta; compare exact sources without claiming proof.",
                  "evidence_refs": [deepcopy(subject["sources"][r]) for r in required if subject["sources"][r] is not None],
                  "finding_refs": [{k: f[k] for k in ("source_run_id", "review_id", "finding_id", "reviewed_sha")}
                                   for f in subject["findings"]]}
        if challenge:
            result.update(counterexample=counterexamples[index],
                          disposition="BLOCKER" if missing else "REJECTED_WITH_BOUNDED_BASIS")
        results.append(result)
    return results


def moa_first(body, profile, subject):
    return {"subject_fingerprint": subject["subject_fingerprint"], "checks": moa_checks(subject, profile),
            "draft_candidate": None if subject["blockers"] else moa_candidate(body)}


def moa_final(body, profile, subject, first):
    challenges = moa_checks(subject, profile, challenge=True)
    blocked = any(c["outcome"] == "BLOCKER" for c in challenges)
    candidate = None if blocked else moa_candidate(body)
    return {"subject_fingerprint": subject["subject_fingerprint"], "construct_fingerprint": first["construct_fingerprint"],
            "challenges": challenges, "reconciled_candidate": candidate,
            "closure": {"outcome": "NO_DECISION" if blocked else "CANDIDATE",
                        "knowledge": "UNKNOWN" if blocked else "INFERRED", "summary": "One bounded semantic closure; no canonical finding resolution.",
                        "candidate_fingerprint": None if blocked else digest(candidate)}}


@pytest.mark.parametrize("action", ("CODE_FIX", "EVIDENCE_ONLY"))
@pytest.mark.parametrize("prior_kind", (None, "REPAIR", "REMEDIATION"))
def test_moa_unique_two_focused_passes_preserve_family_and_original_authority(moa_profile, action, prior_kind):
    body, sources = moa_packet(action=action, prior_kind=prior_kind)
    subject = moa_subject(body, moa_profile, sources)
    assert subject["blockers"] == {}
    first = construct_moa(body, moa_profile, subject, moa_first(body, moa_profile, subject), sources)
    assert "closure" not in first and "handoff_candidate" not in first
    final = validate_moa(body, moa_profile, subject, first, moa_final(body, moa_profile, subject, first), sources)
    assert final["closure"]["outcome"] == "CANDIDATE"
    assert final["handoff_candidate"] == moa_candidate(body)
    assert "verdict" not in final and "finding_resolved" not in final
    with pytest.raises(BrainAuditError):
        construct_stage1(body, moa_profile, {"proposal": "Cannot mix with V1"})


@pytest.mark.parametrize("check_id", (
    "EXACT_REVIEW_FINDING", "FINDING_CAUSE_AND_MINIMUM_DELTA", "ORIGINAL_TASK_AUTHORITY",
))
@pytest.mark.parametrize("knowledge", ("OBSERVED", "INFERRED", "UNKNOWN"))
@pytest.mark.parametrize("disposition", ("REJECTED_WITH_BOUNDED_BASIS", "ADDRESSED_BY_RECONCILIATION"))
def test_moa_first_pass_blockers_survive_clear_second_pass_without_speculative_candidates(
        moa_profile, check_id, knowledge, disposition):
    body, sources = moa_packet()
    subject = moa_subject(body, moa_profile, sources)
    assert subject["blockers"] == {}
    material = moa_first(body, moa_profile, subject)
    draft = deepcopy(material["draft_candidate"])
    check = next(c for c in material["checks"] if c["id"] == check_id)
    check.update(outcome="BLOCKER", knowledge=knowledge,
                 basis="The exact sources exist, but this check's causal or authority judgment remains unresolved.")
    with pytest.raises(BrainAuditError, match="first-pass blocker cannot expose a speculative draft"):
        construct_moa(body, moa_profile, subject, material, sources)

    material["draft_candidate"] = None
    first = construct_moa(body, moa_profile, subject, material, sources)
    material = moa_final(body, moa_profile, subject, first)
    assert all(c["outcome"] == "CLEAR" for c in material["challenges"])
    assert material["reconciled_candidate"] == draft
    # A generic second-pass disposition does not resolve the precise first-pass blocker.
    material["challenges"][1]["disposition"] = disposition
    with pytest.raises(BrainAuditError, match="final closure contradicts focused passes"):
        validate_moa(body, moa_profile, subject, first, material, sources)

    material["reconciled_candidate"] = None
    material["closure"].update(outcome="NO_DECISION", candidate_fingerprint=None,
                                knowledge="UNKNOWN" if knowledge == "UNKNOWN" else "INFERRED")
    final = validate_moa(body, moa_profile, subject, first, material, sources)
    assert final["closure"]["outcome"] == "NO_DECISION"
    assert final["closure"]["knowledge"] == material["closure"]["knowledge"]
    assert final["handoff_candidate"] is None
    material["reconciled_candidate"] = draft
    material["closure"]["candidate_fingerprint"] = digest(draft)
    with pytest.raises(BrainAuditError, match="blocker cannot expose a final remediation"):
        validate_moa(body, moa_profile, subject, first, material, sources)


def test_moa_frontier_is_bounded_analysis_without_an_implicit_target(moa_profile):
    body, sources = moa_packet(frontier=True)
    subject = moa_subject(body, moa_profile, sources)
    assert set(subject["blockers"]) == {"target"}
    first_material = moa_first(body, moa_profile, subject)
    first = construct_moa(body, moa_profile, subject, first_material, sources)
    material = moa_final(body, moa_profile, subject, first)
    assert validate_moa(body, moa_profile, subject, first, material, sources)["handoff_candidate"] is None
    # Neither an externally proposed winner nor the first sorted element selects.
    first_material["draft_candidate"] = {"finding_id": "F1"}
    with pytest.raises(BrainAuditError):
        construct_moa(body, moa_profile, subject, first_material, sources)
    with pytest.raises(BrainAuditError):
        construct_moa_subject(body, moa_profile, subject["return_contract_ref"], {"finding_id": "F1"}, sources)
    material["challenges"][-1].update(outcome="CLEAR", knowledge="OBSERVED", disposition="REJECTED_WITH_BOUNDED_BASIS")
    material["closure"].update(outcome="CANDIDATE", knowledge="INFERRED")
    with pytest.raises(BrainAuditError):
        validate_moa(body, moa_profile, subject, first, material, sources)


@pytest.mark.parametrize("fault", (
    "missing_review", "duplicate_review", "wrong_run", "wrong_review", "wrong_action", "wrong_sha",
    "wrong_finding", "duplicate_finding", "false_pass", "pass_empty", "mixed_pass", "basis", "issue", "expected",
    "stale_ref", "resolved", "revoked", "task", "verification", "prior_missing", "prior_changed",
))
def test_moa_missing_conflicting_sources_force_unknown_without_model_source_laundering(moa_profile, fault):
    body, sources = moa_packet(prior_kind="REMEDIATION" if fault.startswith("prior_") else None)
    record = sources["reviews"][0]
    review, finding = record["review"], record["review"]["findings"][0]
    lineage = body["canonical_facts"]["correction_subject"]
    if fault == "missing_review":
        sources["reviews"] = []
    elif fault == "duplicate_review":
        sources["reviews"].append(deepcopy(record))
    elif fault == "wrong_run":
        record["run"]["run_id"] = "RUN-002-099"
    elif fault == "wrong_review":
        review["review_id"] = "REVIEW-002-099"
    elif fault == "wrong_action":
        finding["action"] = "EVIDENCE_ONLY"
    elif fault == "wrong_sha":
        review["reviewed_sha"] = "f" * 40
    elif fault == "wrong_finding":
        finding["id"] = "F99"
    elif fault == "duplicate_finding":
        review["findings"].append(deepcopy(finding))
    elif fault == "false_pass":
        review["verdict"] = "PASS"
    elif fault == "pass_empty":
        review.update(verdict="PASS", findings=[], acceptance={"AC1": "PASS", "AC2": "PASS"})
    elif fault == "mixed_pass":
        review["acceptance"]["AC1"] = "PASS"
    elif fault in ("basis", "issue", "expected"):
        finding[fault] = "AC2" if fault == "basis" else "Another finding contract"
    elif fault == "stale_ref":
        record["review_decision_sha"] = "f" * 40
    elif fault == "resolved":
        lineage["outstanding_reviews"] = []
    elif fault == "revoked":
        body["canonical_blocker"] = {"reason": "Authority revoked"}
    elif fault == "task":
        record["run"]["task"] = {"id": "TASK-003", "revision": 1}
    elif fault == "verification":
        body["canonical_facts"]["task_contract"].pop("verification")
    elif fault == "prior_missing":
        record["run"]["prior_correction"] = None
    else:
        body["prior_semantic_decisions"] = deepcopy(record["run"]["prior_correction"])
        record["run"]["prior_correction"]["authorization_sha"] = "f" * 40
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    subject = moa_subject(body, moa_profile, sources)
    assert subject["blockers"]
    first = construct_moa(body, moa_profile, subject, moa_first(body, moa_profile, subject), sources)
    material = moa_final(body, moa_profile, subject, first)
    final = validate_moa(body, moa_profile, subject, first, material, sources)
    assert final["closure"]["knowledge"] == "UNKNOWN" and final["handoff_candidate"] is None
    for check in material["challenges"]:
        if check["knowledge"] == "UNKNOWN":
            check.update(knowledge="INFERRED", outcome="CLEAR", disposition="REJECTED_WITH_BOUNDED_BASIS")
            break
    with pytest.raises(BrainAuditError):
        validate_moa(body, moa_profile, subject, first, material, sources)


@pytest.mark.parametrize("fault", ("action", "reviewed_sha", "finding_id", "scope", "empty_code_fix", "constraints", "policy", "command", "duplicate_command", "evidence_only"))
def test_moa_final_proposal_cannot_escape_exact_finding_and_original_policy(moa_profile, fault):
    body, sources = moa_packet(action="EVIDENCE_ONLY" if fault == "evidence_only" else "CODE_FIX")
    subject = moa_subject(body, moa_profile, sources)
    first = construct_moa(body, moa_profile, subject, moa_first(body, moa_profile, subject), sources)
    material = moa_final(body, moa_profile, subject, first)
    candidate = material["reconciled_candidate"]
    if fault == "scope":
        candidate["modification_scope"].append("src/unrelated.py")
    elif fault == "empty_code_fix":
        candidate["modification_scope"] = []
    elif fault == "constraints":
        candidate["constraints"]["hard"] = []
    elif fault == "policy":
        candidate["verification"]["policy"] = "minimum-sufficient-v2"
    elif fault == "command":
        candidate["verification"]["affected"] = ["pytest tests/unrelated.py"]
    elif fault == "duplicate_command":
        candidate["verification"]["affected"] *= 2
    elif fault == "evidence_only":
        candidate["modification_scope"] = body["canonical_facts"]["task_contract"]["scope"]["modify"]
    else:
        candidate[fault] = {"action": "EVIDENCE_ONLY", "reviewed_sha": "f" * 40, "finding_id": "F2"}[fault]
    material["closure"]["candidate_fingerprint"] = digest(candidate)
    material["challenges"][2]["disposition"] = "ADDRESSED_BY_RECONCILIATION"
    with pytest.raises(BrainAuditError):
        validate_moa(body, moa_profile, subject, first, material, sources)


@pytest.mark.parametrize("fault", ("no_counterexample", "forged_evidence", "unauthorized_finding", "missing_evidence", "unknown_clear", "double_closure", "third_pass", "false_closure", "hidden_support"))
def test_moa_second_pass_requires_concrete_bound_challenges_and_one_closure(moa_profile, fault):
    body, sources = moa_packet()
    subject = moa_subject(body, moa_profile, sources)
    first = construct_moa(body, moa_profile, subject, moa_first(body, moa_profile, subject), sources)
    material = moa_final(body, moa_profile, subject, first)
    challenge = material["challenges"][2]
    if fault == "no_counterexample":
        challenge["counterexample"] = ""
    elif fault == "forged_evidence":
        challenge["evidence_refs"][0]["digest"] = "f" * 64
    elif fault == "unauthorized_finding":
        challenge["finding_refs"][0]["review_id"] = "REVIEW-002-099"
    elif fault == "missing_evidence":
        challenge["evidence_refs"] = []
    elif fault == "unknown_clear":
        challenge["knowledge"] = "UNKNOWN"
    elif fault == "double_closure":
        material["closure"] = [material["closure"], deepcopy(material["closure"])]
    elif fault == "third_pass":
        material["v1_audit"] = {}
    elif fault == "false_closure":
        material["closure"]["outcome"] = "PASS"
    else:
        material["reconciled_candidate"]["support"] = {"format": "AIOS_MOA_SUBJECT"}
    with pytest.raises(BrainAuditError):
        validate_moa(body, moa_profile, subject, first, material, sources)


def test_moa_reconciles_a_concrete_non_interference_risk_without_a_third_audit(moa_profile):
    body, sources = moa_packet()
    subject = moa_subject(body, moa_profile, sources)
    draft = moa_first(body, moa_profile, subject)
    draft["draft_candidate"]["modification_scope"].append("src/unrelated.py")
    first = construct_moa(body, moa_profile, subject, draft, sources)
    material = moa_final(body, moa_profile, subject, first)
    material["challenges"][2].update(disposition="ADDRESSED_BY_RECONCILIATION",
        basis="Remove the unrelated file; the final candidate now limits the correction to the exact finding's supporting delta.")
    assert validate_moa(body, moa_profile, subject, first, material, sources)["handoff_candidate"] == moa_candidate(body)
    material["challenges"][2]["disposition"] = "REJECTED_WITH_BOUNDED_BASIS"
    with pytest.raises(BrainAuditError):
        validate_moa(body, moa_profile, subject, first, material, sources)


@pytest.mark.parametrize("counterexample", (
    "F1 and F2 have similar wording but no evidence of a shared cause; grouping them cannot authorize a larger correction.",
    "The proposed F1 fix disables a still-required F2 behavior; non-interference remains unsupported.",
    "The source requires CODE_FIX but only rerunning evidence leaves the reported issue unchanged.",
    "A prior correction can change the execution predecessor while preserving a finding label; current lineage is decisive.",
))
def test_moa_valid_draft_does_not_discharge_an_unresolved_semantic_counterexample(moa_profile, counterexample):
    body, sources = moa_packet()
    subject = moa_subject(body, moa_profile, sources)
    first = construct_moa(body, moa_profile, subject, moa_first(body, moa_profile, subject), sources)
    material = moa_final(body, moa_profile, subject, first)
    material["challenges"][2].update(outcome="BLOCKER", knowledge="UNKNOWN", disposition="BLOCKER",
                                    counterexample=counterexample, basis="Discriminating support is absent; do not assume closure.")
    material["reconciled_candidate"] = None
    material["closure"].update(outcome="NO_DECISION", knowledge="UNKNOWN", candidate_fingerprint=None)
    final = validate_moa(body, moa_profile, subject, first, material, sources)
    assert final["handoff_candidate"] is None and final["closure"]["outcome"] == "NO_DECISION"
    material["reconciled_candidate"] = first["draft_candidate"]
    with pytest.raises(BrainAuditError):
        validate_moa(body, moa_profile, subject, first, material, sources)


@pytest.mark.parametrize("fault", ("basis_bytes", "candidate_bytes", "depth", "private_path", "nested_moa", "source_bytes", "source_count", "unknown_source_field", "duplicate_refs"))
def test_moa_bounded_portable_material_cannot_hide_private_or_extra_protocol_data(moa_profile, fault):
    body, sources = moa_packet()
    subject = moa_subject(body, moa_profile, sources)
    material = moa_first(body, moa_profile, subject)
    if fault == "basis_bytes":
        material["checks"][1]["basis"] = "x" * 2049
    elif fault == "candidate_bytes":
        material["draft_candidate"]["large"] = "x" * 131072
    elif fault == "depth":
        nested = {}
        material["draft_candidate"]["nested"] = nested
        for _ in range(33):
            nested["next"] = {}
            nested = nested["next"]
    elif fault == "private_path":
        material["checks"][1]["basis"] = "Read C:/private/source.log"
    elif fault == "nested_moa":
        material["draft_candidate"]["nested"] = {"format": "AIOS_BRAIN_REMEDIATION_OUTCOME_AUDIT"}
    elif fault == "source_bytes":
        sources["reviews"][0]["review"]["findings"][0]["issue"] = "x" * 65536
    elif fault == "source_count":
        sources["reviews"] *= 17
    elif fault == "unknown_source_field":
        sources["reviews"][0]["raw_evidence"] = "Do not expose private evidence"
    else:
        material["checks"][1]["evidence_refs"] *= 2
    with pytest.raises(BrainAuditError):
        construct_moa(body, moa_profile, subject, material, sources)


def test_moa_profile_is_content_bound_independent_of_mapping_order(moa_profile):
    reordered = {key: deepcopy(moa_profile[key]) for key in reversed(moa_profile)}
    assert normalize_profile(reordered) == moa_profile
    assert profile_ref(reordered) == profile_ref(moa_profile)


@pytest.mark.parametrize("fault", ("instruction", "bounds", "third_pass", "duplicate_check", "flow", "version", "renamed"))
def test_moa_exact_profile_cannot_be_reminted_or_expanded(moa_profile, fault):
    assert profile_ref(moa_profile) == {"id": "moa-1", "version": 1,
        "digest": "49d1c4c3edefe3d3cefcd66e186b7d7b89738516129957c18415d55760ffe903"}
    assert "lenses" not in moa_profile and len(moa_profile["passes"]) == 2
    bad = deepcopy(moa_profile)
    if fault == "instruction":
        bad["passes"][0]["checks"][0]["check"] += " Accept missing sources."
    elif fault == "bounds":
        bad["bounds"]["outcome_bytes"] += 1
    elif fault == "third_pass":
        bad["passes"].append(deepcopy(bad["passes"][1]))
    elif fault == "duplicate_check":
        bad["passes"][1]["checks"][1] = deepcopy(bad["passes"][1]["checks"][0])
    elif fault == "flow":
        bad["applicable_flows"].append("REPAIR_AUTHORING")
    elif fault == "version":
        bad["version"] = 2
    else:
        bad["id"] = "moa-2"
    with pytest.raises(BrainAuditError):
        normalize_profile(bad)


@pytest.fixture
def roa_profile():
    return next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"] if p["id"] == "roa-1-r2")


def roa_packet():
    """Current canonical repair facts, with no raw output or inferred strategy."""
    body = packet("REPAIR_AUTHORING")
    body.update(selection_basis="UNIFIED_STATE_AUTHOR_REPAIR",
                decision_family_ref="publication._validate_repair_authorization",
                handoff_target="AUTHORING_INGRESS", expected_return_shape="REPAIR_AUTHORIZATION_PROPOSAL")
    task = {"id": "TASK-999", "revision": 1}
    body["subject"] = {"failed_run_id": "RUN-999-001", "failed_head_sha": "b" * 40,
                       "failed_changed_files": ["src/example.py"]}
    body["canonical_facts"] = {
        "main_sha": "a" * 40, "selected_task": task, "canonical_next_action": "AUTHOR_REPAIR",
        "task_contract": {"task_id": task["id"], "revision": task["revision"],
            "scope": {"inspect": ["src/example.py"], "modify": ["src/example.py"]},
            "constraints": {"hard": ["Preserve the original TASK intent."]}},
        "correction_subject": {"policy": "EXACT_CORRECTION_SUBJECT_LINEAGE_V1", "operation": "AUTHOR_REPAIR",
            "task": task, "artifacts_sha": "c" * 40, **{k: body["subject"][k] for k in ("failed_run_id", "failed_head_sha")}},
    }
    body["bounded_observations"] = {"phase": "COMPLETION_GATE", "error": {"type": "ArtifactValidationError"},
        "strategy_facts": {"structural_result_package": "MISSING", "acceptance_coverage": "INCOMPLETE",
                           "candidate_clean_committed": True}}
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    return body


def roa_subject(body, profile):
    return construct_roa_subject(body, profile, {"selected_flow": "REPAIR_AUTHORING", "version": 1,
                                                "digest": "d" * 64}, {"repair_id": "REPAIR-999-001"})


def roa_output(subject, profile, *, inferred=True, unknown=None):
    roles = ("failure", "evidence", "evidence", "evidence", "authorization")
    bases = (
        "Exact failed RUN and candidate have a canonical completion-gate FAILURE.",
        "Missing result package distinguishes incomplete finalization from a verification failure.",
        "Complete the authorized candidate package without changing the TASK intent.",
        "A present valid result package would falsify this diagnosis; the bound observation reports MISSING.",
        "Original TASK scope and constraints apply; execution and proof still belong to Runtime.",
    )
    checks = []
    for index, (spec, role, basis) in enumerate(zip(profile["checks"], roles, bases)):
        missing = subject["sources"][role] is None or index == unknown
        checks.append({"id": spec["id"], "outcome": "BLOCKER" if missing else "CLEAR",
                       "knowledge": "UNKNOWN" if missing else "INFERRED" if inferred and index in (1, 2, 3) else "OBSERVED",
                       "basis": "Decisive material is unavailable; no speculative repair." if missing else basis,
                       "evidence_refs": [] if missing else [deepcopy(subject["sources"][role])]})
    blocked = any(c["outcome"] == "BLOCKER" for c in checks)
    candidate = None if blocked else {
        **subject["external_bindings"], "failed_run_id": subject["failed_run_id"],
        "failed_head_sha": subject["failed_head_sha"], "task": deepcopy(subject["task"]),
        "action": "CONTINUE_IMPLEMENTATION", "modification_scope": ["src/example.py"],
        "instructions": ["Complete the missing final candidate package within the original TASK."],
        "constraints": ["Preserve the original TASK intent."],
    }
    return {"subject_fingerprint": subject["subject_fingerprint"], "checks": checks,
        "closure": {"outcome": "NO_DECISION" if blocked else "CANDIDATE",
                    "knowledge": "UNKNOWN" if blocked else "INFERRED" if inferred else "OBSERVED",
                    "summary": "Missing decisive support blocks authoring." if blocked else "One bounded repair candidate.",
                    "candidate_fingerprint": None if blocked else digest(candidate)}, "candidate": candidate}


def test_roa_is_exactly_five_checks_one_procedure_and_one_closure(roa_profile):
    assert profile_ref(roa_profile) == {"id": "roa-1-r2", "version": 2,
        "digest": "aa6a5b60997849d6a00f9e4174ca7ef20fbc62ed64da645ee938a94f8e2639e4"}
    assert roa_profile["applicable_flows"] == ["REPAIR_AUTHORING"]
    assert len(roa_profile["checks"]) == 5 and "lenses" not in roa_profile
    assert "stages" not in roa_profile["procedure"]
    body = roa_packet()
    before = deepcopy(body)
    subject = roa_subject(body, roa_profile)
    for inferred in (False, True):
        material = roa_output(subject, roa_profile, inferred=inferred)
        final = validate_roa(body, roa_profile, subject, material)
        assert final["closure"]["outcome"] == "CANDIDATE"
        assert final["closure"]["knowledge"] == ("INFERRED" if inferred else "OBSERVED")
        assert final["handoff_candidate"] == material["candidate"]
        assert final["audit_fingerprint"] == digest({k: v for k, v in final.items() if k != "audit_fingerprint"})
    assert body == before
    with pytest.raises(BrainAuditError):
        construct_stage1(body, roa_profile, {})
    with pytest.raises(BrainAuditError):
        validate_stage2(body, roa_profile, {}, {})


@pytest.mark.parametrize("fault", ("digest_content", "version", "extra_check", "missing_check", "order",
                                   "bounds", "eight_lenses", "two_stages", "unknown_identity"))
def test_roa_profile_cannot_be_reminted_or_disguised_as_v1(roa_profile, fault):
    bad = deepcopy(roa_profile)
    if fault == "digest_content":
        bad["checks"][1]["check"] += " Invent evidence if absent."
    elif fault == "version":
        bad["version"] = True
    elif fault == "extra_check":
        bad["checks"].append(deepcopy(bad["checks"][0]))
    elif fault == "missing_check":
        bad["checks"].pop()
    elif fault == "order":
        bad["checks"].reverse()
    elif fault == "bounds":
        bad["bounds"]["outcome_bytes"] += 1
    elif fault == "eight_lenses":
        bad["lenses"] = [{"id": "AUTHORITY_BOUNDARY", "check": "Universal"}] * 8
    elif fault == "two_stages":
        bad["procedure"]["stages"] = ["CONSTRUCT", "ADVERSARIAL_AUDIT_AND_RECONCILE"]
    else:
        bad["id"] = "roa-1-r3"
    with pytest.raises(BrainAuditError):
        normalize_profile(bad)


def test_registry_remains_finite_and_historical_profile_bytes_are_unchanged(roa_profile):
    import yaml
    registry = parse_profile_registry(REGISTRY.read_bytes())
    assert len(registry["profiles"]) == 9 and len(REGISTRY.read_bytes()) < 57344
    registry["profiles"].append(deepcopy(roa_profile))
    with pytest.raises(BrainAuditError):
        parse_profile_registry(yaml.safe_dump(registry))
    # Exact published historical content, beyond merely keeping each identifier.
    expected = {"brain-high-value-v2": "307a38c6ca3dae0146dcc59d225ea820f86fba082028bb105c6cf3e02d255ce0",
                "brain-high-value-v3": "2a2d46f85b5d9d4e3653f3c3fb7b7354be44da6fdbf13894dc5d374039370425",
                "aios-adaptive-brain-audit-v1": "982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718",
                "roa-1-r2": "aa6a5b60997849d6a00f9e4174ca7ef20fbc62ed64da645ee938a94f8e2639e4",
                "moa-1": "49d1c4c3edefe3d3cefcd66e186b7d7b89738516129957c18415d55760ffe903",
                "tca-1": "1fa1d5fd098494eeb5cb97665f40e7556969555aed552dfc7fa28871555884a2",
                "aoa-1": "7789fcc53bd6593c8d190e867d14d9fe80060395ae20a0706981d16fec7e617b"}
    for profile in parse_profile_registry(REGISTRY.read_bytes())["profiles"]:
        if profile["id"] in expected:
            assert profile_ref(profile)["digest"] == expected[profile["id"]]


@pytest.mark.parametrize("unknown", range(5))
def test_roa_unknown_blocks_without_exposing_a_speculative_repair(roa_profile, unknown):
    body = roa_packet()
    subject = roa_subject(body, roa_profile)
    material = roa_output(subject, roa_profile, unknown=unknown)
    final = validate_roa(body, roa_profile, subject, material)
    assert final["closure"]["outcome"] == "NO_DECISION" and final["closure"]["knowledge"] == "UNKNOWN"
    assert final["handoff_candidate"] is None
    material["candidate"] = roa_output(subject, roa_profile)["candidate"]
    with pytest.raises(BrainAuditError):
        validate_roa(body, roa_profile, subject, material)


@pytest.mark.parametrize("missing", ("failure", "failure_identity", "evidence", "authorization"))
def test_roa_missing_decisive_packet_material_is_a_bounded_unknown(roa_profile, missing):
    body = roa_packet()
    if missing == "failure":
        body["canonical_facts"].pop("correction_subject")
    elif missing == "failure_identity":
        body["canonical_facts"]["correction_subject"].pop("artifacts_sha")
    elif missing == "evidence":
        body["bounded_observations"] = None
    else:
        body["canonical_facts"].pop("task_contract")
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    subject = roa_subject(body, roa_profile)
    assert subject["sources"]["failure" if missing == "failure_identity" else missing] is None
    material = roa_output(subject, roa_profile)
    assert validate_roa(body, roa_profile, subject, material)["handoff_candidate"] is None
    for check in material["checks"]:
        check.update(outcome="CLEAR", knowledge="OBSERVED")
    with pytest.raises(BrainAuditError):
        validate_roa(body, roa_profile, subject, material)


@pytest.mark.parametrize("fault", ("missing", "duplicate", "order", "forged_digest", "forged_path", "no_evidence",
    "unknown_clear", "false_closure", "false_knowledge", "duplicate_closure", "stale_candidate", "stale_subject",
    "basis_bound", "references_bound", "private_path", "private_key", "dual_audit", "invalid_mode"))
def test_roa_malformed_and_adversarial_closures_fail_closed(roa_profile, fault):
    body = roa_packet()
    subject = roa_subject(body, roa_profile)
    material = roa_output(subject, roa_profile)
    check = material["checks"][1]
    if fault == "missing":
        material["checks"].pop()
    elif fault == "duplicate":
        material["checks"][1] = deepcopy(material["checks"][0])
    elif fault == "order":
        material["checks"].reverse()
    elif fault == "forged_digest":
        check["evidence_refs"][0]["digest"] = "f" * 64
    elif fault == "forged_path":
        check["evidence_refs"][0]["path"] = "executor_claims"
    elif fault == "no_evidence":
        check["evidence_refs"] = []
    elif fault == "unknown_clear":
        check["knowledge"] = "UNKNOWN"
    elif fault == "false_closure":
        material["closure"]["outcome"] = "PASS"
    elif fault == "false_knowledge":
        material["closure"]["knowledge"] = "OBSERVED"
    elif fault == "duplicate_closure":
        material["closure"] = [deepcopy(material["closure"])] * 2
    elif fault == "stale_candidate":
        material["candidate"]["failed_head_sha"] = "f" * 40
        material["closure"]["candidate_fingerprint"] = digest(material["candidate"])
    elif fault == "stale_subject":
        material["subject_fingerprint"] = "f" * 64
    elif fault == "basis_bound":
        check["basis"] = "x" * 4097
    elif fault == "references_bound":
        check["evidence_refs"] *= 5
    elif fault == "private_path":
        check["basis"] = "Read C:/private/log"
    elif fault == "private_key":
        check["evidence_refs"][0]["provider_identity"] = "hidden"
    elif fault == "dual_audit":
        material["stage1"] = {}
    else:
        material["closure"]["knowledge"] = "MEASURED"
    before = deepcopy(body)
    with pytest.raises(BrainAuditError):
        validate_roa(body, roa_profile, subject, material)
    assert body == before


@pytest.mark.parametrize("fault", ("main", "failure", "observations", "authority", "intent"))
def test_roa_changed_currentness_invalidates_the_whole_subject(roa_profile, fault):
    body = roa_packet()
    subject = roa_subject(body, roa_profile)
    material = roa_output(subject, roa_profile)
    changed = deepcopy(body)
    if fault == "main":
        changed["canonical_facts"]["main_sha"] = "f" * 40
    elif fault == "failure":
        changed["canonical_facts"]["correction_subject"]["artifacts_sha"] = "f" * 40
    elif fault == "observations":
        changed["bounded_observations"]["strategy_facts"]["structural_result_package"] = "PRESENT"
    elif fault == "authority":
        changed["canonical_blocker"] = {"reason": "Current authority unresolved"}
    else:
        changed["human_input"] = "Different Human intent"
    changed["packet_fingerprint"] = digest({k: v for k, v in changed.items() if k != "packet_fingerprint"})
    with pytest.raises(BrainAuditError):
        validate_roa(changed, roa_profile, subject, material)


@pytest.mark.parametrize("fault", (None, "subject_failure", "unresolved", "wrong_action", "missing_prior", "forged_prior"))
def test_roa_unexecuted_supersession_preserves_exact_prior_authority(roa_profile, fault):
    body = roa_packet()
    body["selection_basis"] = "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION"
    body["canonical_facts"]["canonical_next_action"] = "EXECUTE_REPAIR"
    body["subject"].update(kind="REPAIR_SUPERSESSION", failure_artifacts_sha="c" * 40,
                           current_repair_authorization_sha="e" * 40)
    authorization = {**{k: body["subject"][k] for k in ("failed_run_id", "failed_head_sha")},
                     "task": body["canonical_facts"]["selected_task"]}
    body["prior_semantic_decisions"] = {"kind": "REPAIR", "authorization_sha": "e" * 40,
                                         "authorization": authorization}
    if fault == "subject_failure":
        body["canonical_facts"].pop("correction_subject")
    elif fault == "unresolved":
        body["canonical_blocker"] = {"reason": "Current authority is unresolved"}
    elif fault == "wrong_action":
        body["canonical_facts"]["canonical_next_action"] = "AUTHOR_REPAIR"
    elif fault == "missing_prior":
        body["prior_semantic_decisions"] = None
    elif fault == "forged_prior":
        body["prior_semantic_decisions"]["authorization"]["failed_head_sha"] = "f" * 40
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    if fault == "forged_prior":
        with pytest.raises(BrainAuditError):
            roa_subject(body, roa_profile)
    else:
        subject = roa_subject(body, roa_profile)
        final = validate_roa(body, roa_profile, subject, roa_output(subject, roa_profile))
        assert final["closure"]["outcome"] == ("CANDIDATE" if fault in (None, "subject_failure") else "NO_DECISION")
        if fault not in (None, "subject_failure"):
            assert final["closure"]["knowledge"] == "UNKNOWN" and final["handoff_candidate"] is None


@pytest.mark.parametrize("fault", ("repair_id", "failed_run_id", "task", "action", "scope", "constraints"))
def test_roa_final_candidate_cannot_change_identity_scope_or_original_constraints(roa_profile, fault):
    body = roa_packet()
    subject = roa_subject(body, roa_profile)
    material = roa_output(subject, roa_profile)
    candidate = material["candidate"]
    if fault == "repair_id":
        candidate["repair_id"] = "REPAIR-999-002"
    elif fault == "failed_run_id":
        candidate["failed_run_id"] = "RUN-999-002"
    elif fault == "task":
        candidate["task"]["revision"] = 2
    elif fault == "action":
        candidate["action"] = "AUTOMATIC_RETRY"
    elif fault == "scope":
        candidate["modification_scope"] = ["src/unrelated.py"]
    else:
        candidate["constraints"] = []
    material["closure"]["candidate_fingerprint"] = digest(candidate)
    with pytest.raises(BrainAuditError):
        validate_roa(body, roa_profile, subject, material)


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
    return next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"]
                if p["id"] == "brain-high-value-v3")


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


def test_historical_v3_and_immutable_v2_have_distinct_direct_protocols(profile, v3_profile):
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
    for legacy in (p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"]
                   if p["id"] in {"brain-high-value-v2", "brain-high-value-v3"}):
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


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING"))
def test_v1_single_profile_covers_exact_two_stages_and_ordered_final_closure(adaptive_profile, flow):
    expected_ref = {"id": "aios-adaptive-brain-audit-v1", "version": 1,
                    "digest": "982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718"}
    assert profile_ref(adaptive_profile) == expected_ref
    assert adaptive_profile["procedure"]["stages"] == ["CONSTRUCT", "ADVERSARIAL_AUDIT_AND_RECONCILE"]
    lens_ids = [lens["id"] for lens in adaptive_profile["lenses"]]
    assert len(lens_ids) == 8 and len(set(lens_ids)) == 8
    supplied = packet(flow)
    first = construct_stage1(supplied, adaptive_profile, {"proposal": "Exact new subject"})
    material = stage2_input(first, adaptive_profile)
    if flow == "TASK_AUTHORING":
        material.update(v3_sections(first, material["reconciled_candidate"]))
    final = validate_stage2(supplied, adaptive_profile, first, material)
    assert first["audit_profile_ref"] == final["audit_profile_ref"] == expected_ref
    assert [item["lens"] for item in final["construct_audit"]] == lens_ids
    assert [item["lens"] for item in final["closure"]] == lens_ids
    for field in ("construct_audit", "closure"):
        malformed = deepcopy(material)
        malformed[field].reverse()
        with pytest.raises(BrainAuditError, match="out-of-order"):
            validate_stage2(supplied, adaptive_profile, first, malformed)
    with pytest.raises(BrainAuditError):
        validate_stage2(supplied, adaptive_profile, first, {**material, "third_stage": {}})


def test_historical_v3_exact_serialized_subject_replay_survives_v1_cutover(v3_profile):
    supplied, first, material = v3_material(v3_profile)
    serialized = json.dumps({"packet": supplied, "stage1": first, "stage2": material}, sort_keys=True)
    original = validate_stage2(supplied, v3_profile, first, material)
    assert original["audit_profile_ref"] == {
        "id": "brain-high-value-v3", "version": 3,
        "digest": "2a2d46f85b5d9d4e3653f3c3fb7b7354be44da6fdbf13894dc5d374039370425",
    }
    profiles = parse_profile_registry(REGISTRY.read_bytes())["profiles"]
    replay = json.loads(serialized)
    for ordered in (profiles, list(reversed(profiles))):
        historical = next(p for p in ordered if profile_ref(p) == original["audit_profile_ref"])
        assert validate_stage2(replay["packet"], historical, replay["stage1"], replay["stage2"]) == original
        adaptive = next(p for p in ordered if p["id"] == "aios-adaptive-brain-audit-v1")
        with pytest.raises(BrainAuditError, match="lineage mismatch"):
            validate_stage2(replay["packet"], adaptive, replay["stage1"], replay["stage2"])
    assert json.dumps(replay, sort_keys=True) == serialized


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


# TASK-392 fixtures model explicit Brain judgments, not model semantic proof.
def successor_profile(identity):
    return next(p for p in parse_profile_registry(REGISTRY.read_bytes())["profiles"] if p["id"] == identity)


@pytest.mark.parametrize("identity,version,pin", (
    ("tca-2", 2, "75f7644caf478f39b50279475b81583df6f894e519951199575873de9dd6a17d"),
    ("roa-1-r3", 3, "cee2404daa670ff90314cc7cce938766b2eaf4a59ebaf3c7fca3d6ed41886c5a"),
))
def test_task392_successors_have_distinct_exact_content_and_original_topology(identity, version, pin):
    profile = successor_profile(identity)
    assert profile_ref(profile) == {"id": identity, "version": version, "digest": pin}
    if identity == "tca-2":
        assert [len(p["checks"]) for p in profile["passes"]] == [5, 6]
        assert profile["procedure"] == successor_profile("tca-1")["procedure"]
    else:
        assert len(profile["checks"]) == 5 and "stages" not in profile["procedure"]
        assert profile["procedure"] == successor_profile("roa-1-r2")["procedure"]
    for fault in ("version", "content", "old_body", "unknown", "bounds"):
        bad = deepcopy(profile)
        if fault == "version":
            bad["version"] += 1
        elif fault == "content":
            checks = bad["passes"][0]["checks"] if "passes" in bad else bad["checks"]
            checks[0]["check"] += " Trust supplied claims."
        elif fault == "old_body":
            bad = successor_profile("tca-1" if identity == "tca-2" else "roa-1-r2")
            bad = {**bad, "id": identity, "version": version}
        elif fault == "unknown":
            bad["id"] += "-next"
        else:
            bad["bounds"]["candidate_bytes"] += 1
        with pytest.raises(BrainAuditError):
            normalize_profile(bad)


def test_task392_registry_limits_reject_extra_duplicate_and_oversized_material():
    import yaml
    raw = REGISTRY.read_bytes()
    registry = parse_profile_registry(raw)
    assert len(registry["profiles"]) == 9
    with pytest.raises(BrainAuditError, match="57344"):
        parse_profile_registry(raw + b" " * (57345 - len(raw)))
    for extra in (deepcopy(registry["profiles"][0]), {"id": "tca-99", "version": 99}):
        bad = deepcopy(registry)
        bad["profiles"].append(extra)
        with pytest.raises(BrainAuditError):
            parse_profile_registry(yaml.safe_dump(bad))
    registry["profiles"][0] = deepcopy(registry["profiles"][1])
    with pytest.raises(BrainAuditError, match="duplicate"):
        parse_profile_registry(yaml.safe_dump(registry))


def task392_tca_context():
    body, context = tca_example()
    path = context["authorization"]["scope"]["modify"][0]
    context["source_material"] = [{"path": path, "content": "def retain(context): return context"}]
    return body, context


def test_task392_tca_missing_source_is_unknown_and_cannot_emit_a_candidate():
    profile = successor_profile("tca-2")
    body, context = tca_example()
    subject = tca_subject(body, profile, context)
    assert subject["sources"]["implementation"] is None
    first = construct_tca(body, profile, subject, tca_first(context, profile, subject), context)
    final = tca_final(context, profile, subject, first)
    result = validate_tca(body, profile, subject, first, final, context)
    assert result["closure"]["outcome"] == "NO_DECISION"
    assert result["closure"]["knowledge"] == "UNKNOWN" and result["handoff_candidate"] is None
    false = deepcopy(final)
    false["reconciled_candidate"] = deepcopy(context["task"])
    false["closure"].update(outcome="CANDIDATE", candidate_fingerprint=digest(context["task"]))
    with pytest.raises(BrainAuditError):
        validate_tca(body, profile, subject, first, false, context)


def test_task392_tca391_r1_direct_codec_api_versus_inspect_only_historical_ast_test():
    profile = successor_profile("tca-2")
    body, context = tca_example()
    code = "src/aios_renew/intra_run_proof_contract.py"
    legacy = "tests/test_intra_run_proof_contract.py"
    # Frozen relevant pre-r2 source excerpts at c8900cbd4420a93daef853dc6ddf7024abb649d4.
    context["task"].update(task_id="TASK-391", revision=1,
                           goal="Preserve published direct native K_i/F_i codec APIs while adding the bounded L3-04 preactivation seam.",
                           problem="The historical AST import guard conflicts with published native codec-local imports.")
    body["human_input"] = "Implement bounded L3-04 while preserving L3-01/02/03 and historical behavior; only the seven r1 paths are authorized."
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    context["task"]["scope"] = {"inspect": [code, legacy], "modify": [code]}
    context["authorization"]["scope"] = deepcopy(context["task"]["scope"])
    context["task"]["acceptance"] = [{"id": "AC12", "condition":
        "Diff remains confined to the seven permitted paths, preserves L3-01/02/03 and original historical V1/V2 behavior, and documents L3-04 as preactivation only; real rollout/finite consent are L3-05, integrated G3 proof is L3-06, G6 exact Human per-RUN activation and postactivation L4 measurement remain separate."}]
    context["source_material"] = [
        {"path": code, "content": "def parse_runtime_checkpoint(content: bytes) -> RuntimeCheckpoint:\n    from .verification_contract import (VerificationContractError, _reuse_decode, _app_exact, _original_hash, _original_json, parse_checkpoint_request, parse_scheduler_plan)\n    # ... bounded checkpoint decoding ...\n    from .artifacts import validate_evidence\n"},
        {"path": legacy, "content": 'for node in ast.walk(tree):\n    if isinstance(node, ast.ImportFrom): imports.add(node.module)\nassert imports == {"__future__", "hashlib", "json", "re", "dataclasses", "proof_coverage_contract"}'}]
    context["consumers"] = [
        {"id": "native-codecs", "authority": "RUNTIME", "requirement": "Preserve directly callable published K_i/F_i codecs with canonical local imports."},
        {"id": "historical-ast-test", "authority": "EXECUTOR", "requirement": "Historical AST guard forbids verification_contract/artifacts imports; test is inspect-only in TASK-391 r1."}]
    subject = tca_subject(body, profile, context)
    material = tca_first(context, profile, subject)
    conflict = "TASK-391 r1 AC12 cannot preserve direct native codecs and the AST guard without separately authorized legacy-test modification."
    for c in [*material["checks"][2:], *material["acceptance_checks"], *material["consumer_checks"]]:
        c.update(outcome="BLOCKER", knowledge="UNKNOWN", basis=conflict)
    first = construct_tca(body, profile, subject, material, context)
    final = tca_final(context, profile, subject, first)
    for index in (1, 2, 4):
        final["challenges"][index].update(outcome="BLOCKER", knowledge="UNKNOWN", disposition="BLOCKER",
                                           basis=conflict, counterexample=conflict)
    result = validate_tca(body, profile, subject, first, final, context)
    assert result["closure"]["outcome"] == "NO_DECISION" and result["handoff_candidate"] is None
    assert legacy not in context["authorization"]["scope"]["modify"]
    # An all-CLEAR later pass, command exit 0 or omitted reconciliation cannot erase the first conflict.
    final["challenges"] = tca_final(context, profile, subject, first)["challenges"]
    final["reconciliation"] = []
    final["reconciled_candidate"] = deepcopy(context["task"])
    final["closure"].update(outcome="CANDIDATE", knowledge="INFERRED", candidate_fingerprint=digest(context["task"]))
    with pytest.raises(BrainAuditError):
        validate_tca(body, profile, subject, first, final, context)
    final["reconciled_candidate"]["scope"]["modify"].append(legacy)
    with pytest.raises(BrainAuditError, match="scope"):
        validate_tca(body, profile, subject, first, final, context)


def test_task392_tca_source_content_currentness_and_minimal_scope():
    profile = successor_profile("tca-2")
    body, context = task392_tca_context()
    subject = tca_subject(body, profile, context)
    first = construct_tca(body, profile, subject, tca_first(context, profile, subject), context)
    final = tca_final(context, profile, subject, first)
    assert validate_tca(body, profile, subject, first, final, context)["closure"]["outcome"] == "CANDIDATE"
    changed = deepcopy(context)
    changed["source_material"][0]["content"] += "\n# changed source"
    with pytest.raises(BrainAuditError, match="binding"):
        validate_tca(body, profile, subject, first, final, changed)
    changed["source_material"][0]["path"] = "src/forbidden.py"
    with pytest.raises(BrainAuditError, match="authority"):
        tca_subject(body, profile, changed)


def task392_roa_episode(run_id):
    body = roa_packet()
    task_id = "TASK-" + run_id.split("-")[1]
    body["canonical_facts"]["selected_task"] = {"id": task_id, "revision": 1}
    body["subject"].update(failed_run_id=run_id, failed_changed_files=[])
    contract = body["canonical_facts"]["task_contract"]
    contract.update(task_id=task_id, acceptance=[{"id": f"AC{i}", "condition": f"Established original implementation property {i}"}
                                               for i in range(1, 13 if task_id == "TASK-391" else 10)])
    correction = body["canonical_facts"]["correction_subject"]
    correction.update(task=body["canonical_facts"]["selected_task"], failed_run_id=run_id)
    body["bounded_observations"]["strategy_facts"].update(
        source_delta=[], missing_complete_acceptance_claims=True,
        structural_result_package="PRESENT_INCOMPLETE_CLAIMS", verification_invoked=False)
    if run_id == "RUN-383-002":
        body["bounded_observations"]["strategy_facts"]["missing_acceptance_ids"] = ["AC2", "AC5", "AC7", "AC9"]
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    return body


@pytest.mark.parametrize("run_id", ("RUN-391-013", "RUN-383-002"))
def test_task392_roa_completion_only_missing_claims_has_a_completion_lever(run_id):
    profile = successor_profile("roa-1-r3")
    body = task392_roa_episode(run_id)
    before = deepcopy(body)
    subject = roa_subject(body, profile)
    material = roa_output(subject, profile)
    material["checks"][1]["basis"] = "COMPLETION_GATE, ArtifactValidationError: no source delta, missing full AC claims; no evidenced code failure."
    material["checks"][2]["basis"] = "FINALIZE_CANDIDATE supplies the complete truthful final contract from established implementation, closing the exact missing-claims gate without a source edit."
    material["checks"][3]["basis"] = "An unsupported AC or actual failing code would falsify completion-only readiness; retain a blocker rather than synthesize that claim."
    candidate = material["candidate"]
    candidate.update(action="FINALIZE_CANDIDATE", modification_scope=[], instructions=[
        "Establish every original AC from the committed candidate; return the complete ResultPackage with truthful full AC claims and exact schema. If any property is unsupported, retain a blocker; do not manufacture claims.",
        "Keep the admitted Executor; Runtime applies the unchanged completion gate and owns verification/EVIDENCE."])
    candidate["instructions"].extend(f"Retain {c['id']}: {c['condition']}" for c in body["canonical_facts"]["task_contract"]["acceptance"])
    material["closure"]["candidate_fingerprint"] = digest(candidate)
    final = validate_roa(body, profile, subject, material)
    assert final["handoff_candidate"]["action"] == "FINALIZE_CANDIDATE"
    assert final["handoff_candidate"]["modification_scope"] == []
    assert len(final["checks"]) == 5 and body == before
    assert final["handoff_candidate"]["constraints"] == body["canonical_facts"]["task_contract"]["constraints"]["hard"]


@pytest.mark.parametrize("phase", ("VERIFICATION", "EXECUTION", "UNKNOWN"))
def test_task392_roa_code_failure_and_native_or_private_unknown_are_distinct(phase):
    profile = successor_profile("roa-1-r3")
    body = task392_roa_episode("RUN-391-013")
    body["bounded_observations"] = {"phase": phase, "error": {"type": "AssertionError" if phase == "VERIFICATION" else "NativeInvocationError"},
        "strategy_facts": {"failed_test": "test_preserve_context", "actual": "context discarded"} if phase == "VERIFICATION" else {}}
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    subject = roa_subject(body, profile)
    material = roa_output(subject, profile)
    if phase == "VERIFICATION":
        material["candidate"].update(action="CODE_FIX", instructions=["Fix the evidenced context-discard regression within original scope, retaining every AC and the Runtime completion gate."])
        material["checks"][1]["basis"] = "Native verification assertion distinguishes actual lost context from missing claims."
        material["checks"][2]["basis"] = "CODE_FIX restores the faulty context path; final claims alone cannot repair it."
        material["closure"]["candidate_fingerprint"] = digest(material["candidate"])
    final = validate_roa(body, profile, subject, material)
    assert final["closure"]["outcome"] == ("CANDIDATE" if phase == "VERIFICATION" else "NO_DECISION")
    if phase != "VERIFICATION":
        assert final["closure"]["knowledge"] == "UNKNOWN" and final["handoff_candidate"] is None


def test_task392_roa_requires_full_original_acceptance_source():
    profile = successor_profile("roa-1-r3")
    body = task392_roa_episode("RUN-383-002")
    body["canonical_facts"]["task_contract"].pop("acceptance")
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    subject = roa_subject(body, profile)
    material = roa_output(subject, profile)
    assert subject["sources"]["authorization"] is None
    assert validate_roa(body, profile, subject, material)["closure"]["outcome"] == "NO_DECISION"


@pytest.mark.parametrize("identity", (None, [], {}, True, "tca-99"))
def test_task392_malformed_identity_is_a_closed_parser_error(identity):
    profile = deepcopy(successor_profile("tca-2"))
    profile["id"] = identity
    with pytest.raises(BrainAuditError):
        normalize_profile(profile)


def test_task392_roa_unsupported_code_fix_cannot_escape_a_completion_blocker():
    profile = successor_profile("roa-1-r3")
    body = task392_roa_episode("RUN-383-002")
    subject = roa_subject(body, profile)
    material = roa_output(subject, profile)
    code_fix = deepcopy(material["candidate"])
    code_fix["action"] = "CODE_FIX"
    material["checks"][2].update(outcome="BLOCKER", knowledge="UNKNOWN",
        basis="No source delta or code failure supports CODE_FIX. Missing AC2/AC5/AC7/AC9 must first be truthfully established for the complete final contract.")
    material["closure"].update(outcome="NO_DECISION", knowledge="UNKNOWN", candidate_fingerprint=None)
    material["candidate"] = None
    assert validate_roa(body, profile, subject, material)["handoff_candidate"] is None
    material["candidate"] = code_fix
    material["closure"]["candidate_fingerprint"] = digest(code_fix)
    with pytest.raises(BrainAuditError, match="blocker"):
        validate_roa(body, profile, subject, material)
