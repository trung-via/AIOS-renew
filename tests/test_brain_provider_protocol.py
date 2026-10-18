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
from tests.test_brain_context import canonical_audit_repo
from tests.test_brain_audit import (
    roa_output, roa_packet, v3_sections, moa_packet, moa_first, moa_final,
    tca_example, tca_first, tca_final,
    aoa_example, aoa_first, aoa_final, aoa_candidate, audit_policy_example,
)


ROOT = Path(__file__).resolve().parents[1]


R5_FLOWS = ("ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING")


def production_inputs(registry, flow, canonical):
    """Bind existing semantic examples to a real canonical Git main."""
    from aios_renew.brain_sync import _resolve_repository_name
    repository = _resolve_repository_name(canonical.repo)
    if flow == "ARCHITECTURE":
        supplied, contract, bindings, context = aoa_inputs(registry)
        sources = {"architecture_source_context": context}
        context["provenance"]["main_sha"] = canonical.main_sha
        context["provenance"]["repository"] = repository
    elif flow == "TASK_AUTHORING":
        supplied, contract, bindings, context = tca_inputs(registry)
        sources = {"task_source_context": context}
    elif flow == "REMEDIATION_AUTHORING":
        supplied, contract, bindings, context = moa_inputs(registry)
        sources = {"moa_source_context": context}
    else:
        supplied, contract, bindings = roa_inputs(registry)
        sources = {}
    body = supplied.as_dict()
    body["canonical_facts"].update(repository=repository, main_sha=canonical.main_sha)
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    return DecisionPacket(body), contract, bindings, sources


@pytest.mark.parametrize("flow", R5_FLOWS)
def test_first_party_policy_supplier_binds_the_existing_pure_protocol(registry, canonical_audit_repo, flow):
    from aios_renew.brain_context import canonical_audit_inputs

    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical_audit_repo)
    policy, package, authenticate = canonical_audit_inputs(supplied, contract, bindings,
                                                         repo=canonical_audit_repo.repo, **sources)
    req = construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE",
                            audit_policy=policy, selection_authenticator=authenticate, **sources)
    assert req["audit_selection"]["policy"]["profiles"] == canonical_audit_repo.roadmap[
        canonical_audit_repo.approval_key]["target_profiles"]
    assert revalidate_request(req) == req
    first = validate_response(req, r5_response(req))
    if flow != "REPAIR_AUTHORING":
        second = r5_second(req, first, authenticate)
        assert second["audit_selection"] == req["audit_selection"]
        assert revalidate_decision(validate_response(second, r5_response(second)), second)
    # Serial revalidation remains pure and never refreshes canonical authority.
    canonical_audit_repo.commit("move canonical main without changing old subjects")
    assert revalidate_request(req) == req


class R5Authority:
    """Test-only external canonical source and one-subject Human authentication."""

    def __init__(self, policy, supplied, contract, bindings, package, sources, option=None, support=None):
        body = supplied.as_dict()
        self.expected = {
            "format": "AIOS_BRAIN_AUDIT_SELECTION", "version": 1, "policy": deepcopy(policy),
            "selected_flow": body["selected_flow"], "packet_fingerprint": body["packet_fingerprint"],
            "work_context_fingerprint": body["work_context_fingerprint"],
            "canonical_facts_fingerprint": digest(body["canonical_facts"]), "subject_fingerprint": digest(body["subject"]),
            "return_contract_ref": contract["return_contract_ref"], "external_bindings": deepcopy(bindings),
            "audit_profile_ref": deepcopy(package["audit_profile_ref"]),
            "source_context_fingerprint": digest({key: sources.get(key) for key in (
                "moa_source_context", "task_source_context", "architecture_source_context")}),
            "human_v1_option": deepcopy(option), "human_v1_deep_support": deepcopy(support),
        }
        self.current_policy = deepcopy(policy)
        self.admitted = None
        self.allow_original = False
        self.calls = []

    def __call__(self, selection, phase):
        self.calls.append((phase, deepcopy(selection)))
        if phase == "ADMIT":
            if selection != self.expected or selection["policy"] != self.current_policy:
                return False
            self.admitted = deepcopy(selection)
            return True
        return (phase == "CONTINUE" and self.admitted == selection
                and (selection["policy"] == self.current_policy or self.allow_original))


def r5_case(registry, flow, *, active=True, human=False, deep=False):
    if flow == "ARCHITECTURE":
        supplied, contract, bindings, context = aoa_inputs(registry)
        sources = {"architecture_source_context": context}
    elif flow == "TASK_AUTHORING":
        supplied, contract, bindings, context = tca_inputs(registry)
        sources = {"task_source_context": context}
    elif flow == "REMEDIATION_AUTHORING":
        supplied, contract, bindings, context = moa_inputs(registry)
        sources = {"moa_source_context": context}
    else:
        supplied, contract, bindings = roa_inputs(registry)
        sources, context = {}, None
    policy = audit_policy_example(supplied.as_dict(), active=active)
    reference = (audit_policy_example(supplied.as_dict(), active=False)["profiles"][flow]
                 if human else policy["profiles"][flow])
    profile = next(p for p in parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
                   if profile_ref(p) == reference)
    package = {"profile": profile, "audit_profile_ref": reference}
    option = human_v1_selection(supplied, contract, bindings, context, package) if human else None
    if not active and not human:
        sources = {}
    support = None
    if deep:
        support = {"selected_by": "HUMAN", "option": "V1_READ_ONLY",
                   "packet_fingerprint": supplied.packet_fingerprint,
                   "work_context_fingerprint": supplied.as_dict()["work_context_fingerprint"],
                   "return_contract_ref": contract["return_contract_ref"], "external_bindings": bindings,
                   "source_context_fingerprint": digest({key: sources.get(key) for key in (
                       "moa_source_context", "task_source_context", "architecture_source_context")}),
                   "audit_profile_ref": audit_policy_example(supplied.as_dict(), active=False)["profiles"][flow]}
    authority = R5Authority(policy, supplied, contract, bindings, package, sources, option, support)
    kwargs = {**sources, "audit_policy": policy, "selection_authenticator": authority,
              "human_v1_option": option, "human_v1_deep_support": support}
    return supplied, contract, bindings, package, kwargs, authority


def r5_response(req):
    mode = req["request_mode"]
    if mode.startswith("AOA_"):
        return aoa_response(req)
    if mode.startswith("TCA_"):
        return tca_response(req)
    if mode.startswith("MOA_"):
        return moa_response(req)
    if mode == "ROA_CONFORMANCE":
        return roa_response(req)
    candidate = (req["task_source_context"]["task"] if "task_source_context" in req else
                 aoa_candidate() if req["decision_packet"]["selected_flow"] == "ARCHITECTURE" else
                 {**req["external_bindings"], "proposal": "One exact V1 subject"})
    return response(req, candidate) if mode.endswith("CONSTRUCT") else stage2_response(req, candidate)


def r5_second(req, first, authority):
    second_mode = {"AOA_CONSTRUCT": "AOA_RECONCILE", "TCA_CONSTRUCT": "TCA_RECONCILE",
                   "MOA_CONSTRUCT": "MOA_RECONCILE", "AUDIT_CONSTRUCT": "AUDIT_RECONCILE",
                   "V1_OPTION_CONSTRUCT": "V1_OPTION_RECONCILE"}[req["request_mode"]]
    return construct_request(DecisionPacket(req["decision_packet"]), req["return_contract_package"],
        req["external_bindings"], req["audit_profile_package"], request_mode=second_mode, stage1_decision=first,
        **{key: req.get(key) for key in ("moa_source_context", "task_source_context", "architecture_source_context", "human_v1_option")},
        audit_policy=req["audit_selection"]["policy"], selection_authenticator=authority, selection_phase="CONTINUE",
        human_v1_deep_support=req["audit_selection"]["human_v1_deep_support"])


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("active", (False, True))
def test_r5_pre_and_future_cutover_use_one_exact_existing_profile_and_stage_lineage(registry, flow, active):
    supplied, contract, bindings, package, kwargs, authority = r5_case(registry, flow, active=active)
    req = construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", **kwargs)
    assert revalidate_request(req) == req
    first = validate_response(req, r5_response(req))
    assert revalidate_decision(first, req) == first
    if not active or flow != "REPAIR_AUTHORING":
        second = r5_second(req, first, authority)
        assert second["stage1_lineage"]["stage1_request_fingerprint"] == req["request_fingerprint"]
        final = validate_response(second, r5_response(second))
        assert revalidate_decision(final, second) == final
        assert second["audit_selection"] == req["audit_selection"]
    if active:
        assert req["request_mode"] == dict(zip(R5_FLOWS, ("AOA_CONSTRUCT", "TCA_CONSTRUCT", "MOA_CONSTRUCT", "ROA_CONFORMANCE")))[flow]
        assert req["audit_profile_package"]["profile"]["id"] != "aios-adaptive-brain-audit-v1"
    else:
        assert req["request_mode"] == "AUDIT_CONSTRUCT"
        assert req["audit_profile_package"]["profile"]["id"] == "aios-adaptive-brain-audit-v1"


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("fault", (
    "missing_policy", "missing_authenticator", "literal", "forged_human", "forged_brain", "false", "truthy",
    "exception", "partial", "stale_main", "stale_context", "missing_profile", "wrong_profile",
))
def test_r5_prospective_boundary_rejects_missing_stale_or_self_asserted_authority_without_fallback(registry, flow, fault):
    supplied, contract, bindings, package, kwargs, _ = r5_case(registry, flow)
    if fault == "missing_policy":
        kwargs["audit_policy"] = None
    elif fault == "missing_authenticator":
        kwargs["selection_authenticator"] = None
    elif fault == "literal":
        kwargs["selection_authenticator"] = "HUMAN"
    elif fault in {"forged_human", "forged_brain"}:
        kwargs["audit_policy"]["human_authority_digest" if fault == "forged_human" else "brain_authority_digest"] = "f" * 64
    elif fault in {"false", "truthy"}:
        kwargs["selection_authenticator"] = lambda *_: False if fault == "false" else "AUTHENTICATED"
    elif fault == "exception":
        def unavailable(*_):
            raise RuntimeError("private source unavailable")
        kwargs["selection_authenticator"] = unavailable
    elif fault == "partial":
        del kwargs["audit_policy"]["profiles"]["REPAIR_AUTHORING"]
    elif fault == "stale_main":
        kwargs["audit_policy"]["main_sha"] = "f" * 40
    elif fault == "stale_context":
        kwargs["audit_policy"]["work_context_fingerprint"] = "f" * 64
    elif fault == "missing_profile":
        package = None
    else:
        package = deepcopy(package)
        package["audit_profile_ref"]["digest"] = "f" * 64
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", **kwargs)


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "TASK_AUTHORING"))
@pytest.mark.parametrize("fault", (None, "literal", "selected_by", "source", "packet", "profile", "unauthenticated", "mixed"))
def test_r5_human_v1_substitution_requires_external_one_subject_authority(registry, flow, fault):
    supplied, contract, bindings, package, kwargs, authority = r5_case(registry, flow, human=True)
    if fault == "literal":
        kwargs["human_v1_option"] = "HUMAN"
    elif fault in {"selected_by", "source", "packet", "profile"}:
        key = {"selected_by": "selected_by", "source": "source_context_fingerprint",
               "packet": "packet_fingerprint", "profile": "audit_profile_ref"}[fault]
        kwargs["human_v1_option"][key] = "forged"
    elif fault == "unauthenticated":
        kwargs["selection_authenticator"] = lambda *_: False
    elif fault == "mixed":
        _, _, _, package, _, _ = r5_case(registry, flow)
    if fault:
        with pytest.raises(BrainProviderProtocolError):
            construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", **kwargs)
        return
    req = construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", **kwargs)
    assert req["request_mode"] == "V1_OPTION_CONSTRUCT" and "aoa_subject" not in req and "tca_subject" not in req
    second = r5_second(req, validate_response(req, r5_response(req)), authority)
    assert second["request_mode"] == "V1_OPTION_RECONCILE"
    assert revalidate_decision(validate_response(second, r5_response(second)), second)


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("fault", ("policy", "currentness", "subject", "source", "profile", "return", "private", "extra_verdict"))
def test_r5_policy_bound_requests_and_lineage_reject_rehashed_cross_binding_substitution(registry, flow, fault):
    supplied, contract, bindings, package, kwargs, authority = r5_case(registry, flow)
    req = construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", **kwargs)
    first = validate_response(req, r5_response(req))
    if flow != "REPAIR_AUTHORING":
        req = r5_second(req, first, authority)
    if fault == "policy":
        req["audit_selection"]["policy"]["human_authority_digest"] = "f" * 64
    elif fault == "currentness":
        req["audit_selection"]["work_context_fingerprint"] = "f" * 64
    elif fault == "subject":
        req["audit_selection"]["subject_fingerprint"] = "f" * 64
    elif fault == "source":
        req["audit_selection"]["source_context_fingerprint"] = "f" * 64
    elif fault == "profile":
        req["audit_selection"]["audit_profile_ref"] = audit_policy_example(supplied.as_dict(), active=False)["profiles"][flow]
    elif fault == "return":
        req["audit_selection"]["return_contract_ref"]["digest"] = "f" * 64
    elif fault == "private":
        req["audit_selection"]["policy"]["secret"] = "private"
    else:
        with pytest.raises(BrainProviderProtocolError):
            validate_response(req, {**r5_response(req), "v1_audit": {"outcome": "PASS"}})
        return
    req["request_fingerprint"] = digest({k: v for k, v in req.items() if k != "request_fingerprint"})
    if fault == "policy" and flow == "REPAIR_AUTHORING":
        # Pure serialized validation is not authentication. Re-entry must still
        # authenticate the exact canonical policy, even for a rehashed ROA request.
        kwargs["audit_policy"] = req["audit_selection"]["policy"]
        with pytest.raises(BrainProviderProtocolError):
            construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", **kwargs)
    else:
        with pytest.raises(BrainProviderProtocolError):
            revalidate_request(req)


@pytest.mark.parametrize("flow", ("REMEDIATION_AUTHORING", "REPAIR_AUTHORING"))
def test_r5_corrections_reject_normative_v1_option_and_bind_read_only_support(registry, flow):
    supplied, contract, bindings, package, kwargs, _ = r5_case(registry, flow, deep=True)
    req = construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", **kwargs)
    assert revalidate_request(req) == req
    assert req["audit_profile_package"]["audit_profile_ref"] == kwargs["audit_policy"]["profiles"][flow]
    kwargs["human_v1_option"] = {"selected_by": "HUMAN", "option": "V1_ONLY"}
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", **kwargs)


@pytest.mark.parametrize("flow", ("REMEDIATION_AUTHORING", "REPAIR_AUTHORING"))
@pytest.mark.parametrize("fault", ("literal", "normative", "profile", "source", "extra", "authority"))
def test_r5_optional_support_cannot_supply_authority_or_a_second_normative_profile(registry, flow, fault):
    supplied, contract, bindings, package, kwargs, _ = r5_case(registry, flow, deep=True)
    support = kwargs["human_v1_deep_support"]
    if fault == "literal":
        kwargs["human_v1_deep_support"] = "HUMAN"
    elif fault == "normative":
        support["option"] = "V1_ONLY"
    elif fault == "profile":
        support["audit_profile_ref"] = package["audit_profile_ref"]
    elif fault == "source":
        support["source_context_fingerprint"] = "f" * 64
    elif fault == "extra":
        support["corrected_payload"] = {"action": "CODE_FIX"}
    else:
        kwargs["selection_authenticator"] = lambda *_: False
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", **kwargs)


@pytest.mark.parametrize("identity", ("brain-high-value-v2", "brain-high-value-v3", "aios-adaptive-brain-audit-v1"))
def test_r5_historical_in_flight_subject_keeps_exact_original_profile_and_serialization(registry, identity):
    supplied, contract = inputs(registry, "ARCHITECTURE")
    profile = next(p for p in parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
                   if p["id"] == identity)
    package = {"profile": profile, "audit_profile_ref": profile_ref(profile)}
    req = construct_request(supplied, contract, {}, package, request_mode="AUDIT_CONSTRUCT")
    original = deepcopy(req)
    first = validate_response(req, response(req, aoa_candidate()))
    # An unrelated newly authenticated future policy cannot rewrite old subject bytes.
    current, future_contract, bindings, future_package, kwargs, _ = r5_case(registry, "ARCHITECTURE")
    construct_request(current, future_contract, bindings, future_package, request_mode="PROSPECTIVE", **kwargs)
    assert revalidate_request(req) == original and "audit_selection" not in req
    second = construct_request(supplied, contract, {}, package, request_mode="AUDIT_RECONCILE", stage1_decision=first)
    final = validate_response(second, stage2_response(second, aoa_candidate()))
    assert final["audit_profile_ref"] == package["audit_profile_ref"]
    assert revalidate_decision(final, second) == final
    with pytest.raises(BrainProviderProtocolError):
        construct_request(current, future_contract, bindings, future_package, request_mode="AOA_RECONCILE", stage1_decision=first, **kwargs)


@pytest.mark.parametrize("operation", ("AUTHOR_TASK", "AUTHOR_REPAIR", "AUTHOR_REMEDIATION"))
def test_r5_direct_final_ingress_does_not_consume_prospective_policy_or_audit_support(tmp_path, monkeypatch, operation):
    from aios_renew import brain_audit, brain_context, brain_provider, brain_provider_protocol
    from tests.test_authoring_ingress import (
        test_author_task_without_handoff_has_no_audit_dependencies,
        test_direct_author_repair_final_contract_and_identical_supersession_replay,
        test_direct_author_remediation_final_contract_and_read_only_replay,
    )
    def forbidden(*_args, **_kwargs):
        pytest.fail("direct-final ingress consumed R5 selection or provider authority")
    monkeypatch.setattr(brain_audit, "normalize_audit_policy", forbidden)
    monkeypatch.setattr(brain_context, "canonical_audit_inputs", forbidden)
    monkeypatch.setattr(brain_provider_protocol, "construct_request", forbidden)
    monkeypatch.setattr(brain_provider, "attempt", forbidden)
    if operation == "AUTHOR_TASK":
        test_author_task_without_handoff_has_no_audit_dependencies(tmp_path, monkeypatch)
    elif operation == "AUTHOR_REPAIR":
        test_direct_author_repair_final_contract_and_identical_supersession_replay(tmp_path, monkeypatch, {"policy": "inert"})
    else:
        test_direct_author_remediation_final_contract_and_read_only_replay(tmp_path, monkeypatch, {"policy": "inert"})


@pytest.fixture
def aoa_package():
    profile = next(p for p in parse_profile_registry(
        (ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"] if p["id"] == "aoa-1")
    return {"profile": profile, "audit_profile_ref": profile_ref(profile)}


def aoa_inputs(registry):
    body, context = aoa_example()
    supplied = DecisionPacket(body)
    return supplied, select_return_contract(registry, supplied), {}, context


def aoa_request(registry, package, *, unknown=False):
    supplied, contract, bindings, context = aoa_inputs(registry)
    if unknown:
        context["objective"] = None
    return construct_request(supplied, contract, bindings, package, request_mode="AOA_CONSTRUCT", architecture_source_context=context)


def aoa_response(req):
    context, profile, subject = req["architecture_source_context"], req["audit_profile_package"]["profile"], req["aoa_subject"]
    material = (aoa_first(context, profile, subject) if req["request_mode"] == "AOA_CONSTRUCT" else
                aoa_final(context, profile, subject, req["stage1_lineage"]["construct"]))
    return {"request_fingerprint": req["request_fingerprint"], **material}


def architecture_second(req, first):
    mode = "AOA_RECONCILE" if req["request_mode"] == "AOA_CONSTRUCT" else "V1_OPTION_RECONCILE"
    return construct_request(DecisionPacket(req["decision_packet"]), req["return_contract_package"], {}, req["audit_profile_package"],
        request_mode=mode, stage1_decision=first, architecture_source_context=req["architecture_source_context"],
        human_v1_option=req.get("human_v1_option"))


@pytest.mark.parametrize("unknown", (False, True))
def test_aoa_transport_revalidates_two_focused_passes_and_exact_final_closure(registry, aoa_package, unknown):
    req = aoa_request(registry, aoa_package, unknown=unknown)
    assert revalidate_request(json.loads(json.dumps(req))) == req
    first = validate_response(req, aoa_response(req))
    assert revalidate_decision(first, req) == first
    second = architecture_second(req, first)
    assert revalidate_request(second) == second
    final = validate_response(second, aoa_response(second))
    assert revalidate_decision(json.loads(json.dumps(final)), second) == final
    assert final["semantic_value"]["closure"]["outcome"] == ("NO_DECISION" if unknown else "CANDIDATE")
    assert final["return_contract_ref"] == req["return_contract_package"]["return_contract_ref"]
    for extra in ({"v1_audit": {}}, {"human_v1_option": "HUMAN"}, {"third_pass": {}}, {"decisions": [final]}):
        with pytest.raises(BrainProviderProtocolError):
            validate_response(second, {**aoa_response(second), **extra})


@pytest.mark.parametrize("mode", ("DIRECT", "AUDIT_CONSTRUCT", "AUDIT_RECONCILE", "TCA_CONSTRUCT", "MOA_CONSTRUCT", "ROA_CONFORMANCE", "V1_OPTION_CONSTRUCT"))
def test_aoa_registry_presence_does_not_authorize_another_flow_or_mode(registry, aoa_package, mode):
    supplied, contract, bindings, context = aoa_inputs(registry)
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, aoa_package, request_mode=mode, architecture_source_context=context)
    supplied, contract = inputs(registry, "TASK_AUTHORING")
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, {"task_id": "TASK-999", "revision": 1}, aoa_package,
                          request_mode="AOA_CONSTRUCT", architecture_source_context=context)


@pytest.mark.parametrize("fault", ("intent", "packet", "main", "work_context", "source", "return", "profile", "mode", "privacy", "lineage"))
def test_aoa_rehashed_requests_cannot_mix_or_substitute_bound_material(registry, aoa_package, v1_only_package, fault):
    req = aoa_request(registry, aoa_package)
    first = validate_response(req, aoa_response(req))
    req = architecture_second(req, first)
    packet = req["decision_packet"]
    if fault == "intent":
        packet["human_input"] += " Different intent."
    elif fault == "packet":
        packet["packet_fingerprint"] = "f" * 64
    elif fault == "main":
        packet["canonical_facts"]["main_sha"] = "f" * 40
    elif fault == "work_context":
        packet["work_context_fingerprint"] = "f" * 64
    elif fault == "source":
        req["architecture_source_context"]["constraints"].append("Different scope")
    elif fault == "return":
        package = req["return_contract_package"]
        package["contract"]["candidate_contract"]["requirements"].append("Different return")
        package["return_contract_ref"] = return_contract_ref(package["contract"], package["bounds"])
    elif fault == "profile":
        req["audit_profile_package"] = v1_only_package
    elif fault == "mode":
        req["request_mode"] = "V1_OPTION_RECONCILE"
    elif fault == "privacy":
        req["architecture_source_context"]["authorization"]["risk_acceptance"] = [{"secret": "x"}]
    else:
        req["stage1_lineage"]["stage1_decision_fingerprint"] = "f" * 64
    if fault in ("intent", "main", "work_context"):
        packet["packet_fingerprint"] = digest({k: v for k, v in packet.items() if k != "packet_fingerprint"})
    req["request_fingerprint"] = digest({k: v for k, v in req.items() if k != "request_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_request(req)


@pytest.mark.parametrize("fault", ("cost", "subject", "candidate", "profile", "closure", "nested", "format"))
def test_aoa_rehashed_final_decision_revalidates_the_complete_comparison(registry, aoa_package, fault):
    req = aoa_request(registry, aoa_package)
    second = architecture_second(req, validate_response(req, aoa_response(req)))
    final = validate_response(second, aoa_response(second))
    semantic = final["semantic_value"]
    if fault == "cost":
        semantic["alternatives"][0]["episode_cost"]["tokens"]["quantity"] = 1
    elif fault == "subject":
        semantic["subject_fingerprint"] = "f" * 64
    elif fault == "candidate":
        semantic["handoff_candidate"]["selected_alternative"] = "C"
    elif fault == "profile":
        final["audit_profile_ref"]["digest"] = "f" * 64
    elif fault == "closure":
        semantic["closure"]["outcome"] = "NO_DECISION"
    elif fault == "format":
        semantic["format"] = "AIOS_BRAIN_TASK_CONTRACT_AUDIT"
    else:
        semantic["handoff_candidate"]["uncertainties"] = [{"human_v1_option": "HUMAN"}]
    semantic["audit_fingerprint"] = digest({k: v for k, v in semantic.items() if k != "audit_fingerprint"})
    final["decision_fingerprint"] = digest({k: v for k, v in final.items() if k != "decision_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(final, second)


def test_architecture_exact_external_human_v1_option_never_invokes_aoa(registry, aoa_package, v1_only_package, monkeypatch):
    supplied, contract, bindings, context = aoa_inputs(registry)
    selection = human_v1_selection(supplied, contract, bindings, context, v1_only_package)
    def forbidden(*args, **kwargs):
        raise AssertionError("V1-only must not invoke AOA")
    monkeypatch.setattr("aios_renew.brain_provider_protocol.construct_aoa_subject", forbidden)
    monkeypatch.setattr("aios_renew.brain_provider_protocol.construct_aoa", forbidden)
    monkeypatch.setattr("aios_renew.brain_provider_protocol.validate_aoa", forbidden)
    req = construct_request(supplied, contract, bindings, v1_only_package, request_mode="V1_OPTION_CONSTRUCT",
                            architecture_source_context=context, human_v1_option=selection)
    first = validate_response(req, response(req, aoa_candidate()))
    second = architecture_second(req, first)
    final = validate_response(second, stage2_response(second, aoa_candidate()))
    assert revalidate_decision(final, second) == final
    assert all("aoa_subject" not in r for r in (req, second))
    assert final["audit_profile_ref"] == v1_only_package["audit_profile_ref"]
    assert len(final["semantic_value"]["closure"]) == 8


@pytest.mark.parametrize("fault", ("literal_human", "missing", "unauthorized", "option", "source", "packet", "digest", "v3", "untrusted_source", "dual", "mixed_first", "nested_candidate"))
def test_architecture_human_v1_option_requires_exact_external_selection_and_no_mixing(registry, aoa_package, v1_only_package, fault):
    supplied, contract, bindings, context = aoa_inputs(registry)
    option = human_v1_selection(supplied, contract, bindings, context, v1_only_package)
    package = v1_only_package
    if fault == "literal_human":
        option = "HUMAN"
    elif fault == "missing":
        option = None
    elif fault == "unauthorized":
        option["selected_by"] = "BRAIN"
    elif fault == "option":
        option["option"] = "V1_PLUS_AOA"
    elif fault in ("source", "packet"):
        option["source_context_fingerprint" if fault == "source" else "packet_fingerprint"] = "f" * 64
    elif fault == "digest":
        option["audit_profile_ref"] = {**option["audit_profile_ref"], "digest": "f" * 64}
    elif fault == "untrusted_source":
        context["provenance"]["status"] = "UNKNOWN"
        option = human_v1_selection(supplied, contract, bindings, context, v1_only_package)
    elif fault == "v3":
        profile = next(p for p in parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
                       if p["id"] == "brain-high-value-v3")
        package = {"profile": profile, "audit_profile_ref": profile_ref(profile)}
    elif fault == "dual":
        package = [package, aoa_package]
    elif fault == "mixed_first":
        req = aoa_request(registry, aoa_package)
        first = validate_response(req, aoa_response(req))
        with pytest.raises(BrainProviderProtocolError):
            construct_request(supplied, contract, bindings, package, request_mode="V1_OPTION_RECONCILE", stage1_decision=first,
                              architecture_source_context=context, human_v1_option=option)
        return
    else:
        req = construct_request(supplied, contract, bindings, package, request_mode="V1_OPTION_CONSTRUCT",
                                architecture_source_context=context, human_v1_option=option)
        with pytest.raises(BrainProviderProtocolError):
            validate_response(req, response(req, {**aoa_candidate(), "human_v1_option": "HUMAN"}))
        return
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, package, request_mode="V1_OPTION_CONSTRUCT",
                          architecture_source_context=context, human_v1_option=option)


@pytest.fixture
def tca_package():
    profile = next(p for p in parse_profile_registry(
        (ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"] if p["id"] == "tca-1")
    return {"profile": profile, "audit_profile_ref": profile_ref(profile)}


@pytest.fixture
def v1_only_package():
    profile = next(p for p in parse_profile_registry(
        (ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"] if p["id"] == "aios-adaptive-brain-audit-v1")
    return {"profile": profile, "audit_profile_ref": profile_ref(profile)}


def tca_inputs(registry):
    body, context = tca_example()
    supplied = DecisionPacket(body)
    return supplied, select_return_contract(registry, supplied), {"task_id": "TASK-002", "revision": 1}, context


def human_v1_selection(supplied, contract, bindings, context, package):
    body = supplied.as_dict()
    return {"selected_by": "HUMAN", "option": "V1_ONLY", "packet_fingerprint": body["packet_fingerprint"],
            "work_context_fingerprint": body["work_context_fingerprint"], "return_contract_ref": contract["return_contract_ref"],
            "external_bindings": bindings, "source_context_fingerprint": digest(context), "audit_profile_ref": package["audit_profile_ref"]}


def tca_request(registry, package, *, unknown=False):
    supplied, contract, bindings, context = tca_inputs(registry)
    if unknown:
        context["authorization"] = None
    return construct_request(supplied, contract, bindings, package, request_mode="TCA_CONSTRUCT", task_source_context=context)


def tca_response(req):
    profile, context, subject = req["audit_profile_package"]["profile"], req["task_source_context"], req["tca_subject"]
    material = (tca_first(context, profile, subject) if req["request_mode"] == "TCA_CONSTRUCT" else
                tca_final(context, profile, subject, req["stage1_lineage"]["construct"]))
    return {"request_fingerprint": req["request_fingerprint"], **material}


def task_audit_second(req, first):
    mode = "TCA_RECONCILE" if req["request_mode"] == "TCA_CONSTRUCT" else "V1_OPTION_RECONCILE"
    return construct_request(DecisionPacket(req["decision_packet"]), req["return_contract_package"], req["external_bindings"],
        req["audit_profile_package"], request_mode=mode, stage1_decision=first, task_source_context=req["task_source_context"],
        human_v1_option=req.get("human_v1_option"))


@pytest.mark.parametrize("unknown", (False, True))
def test_tca_two_pass_transport_round_trips_candidate_or_bounded_no_decision(registry, tca_package, unknown):
    req = tca_request(registry, tca_package, unknown=unknown)
    assert revalidate_request(req) == req
    first = validate_response(req, tca_response(req))
    assert revalidate_decision(first, req) == first
    second = task_audit_second(req, first)
    assert second["tca_subject"] == req["tca_subject"] and revalidate_request(second) == second
    final = validate_response(second, tca_response(second))
    assert revalidate_decision(final, second) == final
    assert final["semantic_value"]["closure"]["outcome"] == ("NO_DECISION" if unknown else "CANDIDATE")
    assert set(final["semantic_value"]) == {"format", "version", "stage", "subject_fingerprint", "construct_fingerprint",
        "challenges", "acceptance_checks", "consumer_checks", "acceptance_accounting", "reconciliation", "closure",
        "handoff_candidate", "audit_fingerprint"}
    for extra in ({"v1_audit": {}}, {"decisions": [final]}, {"third_pass": {}}, {"human_v1_option": {}}, {"model": "private"}):
        with pytest.raises(BrainProviderProtocolError):
            validate_response(second, {**tca_response(second), **extra})


@pytest.mark.parametrize("mode", ("DIRECT", "AUDIT_CONSTRUCT", "AUDIT_RECONCILE", "MOA_CONSTRUCT", "ROA_CONFORMANCE", "V1_OPTION_CONSTRUCT"))
def test_tca_registry_membership_does_not_authorize_ordinary_or_mixed_modes(registry, tca_package, mode):
    supplied, contract, bindings, context = tca_inputs(registry)
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, tca_package, request_mode=mode, task_source_context=context)


@pytest.mark.parametrize("fault", ("packet", "intent", "return", "source", "subject", "flow", "profile", "stage", "lineage", "binding"))
def test_tca_rehashed_request_rejects_stale_and_swapped_material(registry, tca_package, fault):
    req = tca_request(registry, tca_package)
    req = task_audit_second(req, validate_response(req, tca_response(req)))
    if fault in ("packet", "intent", "flow"):
        body = req["decision_packet"]
        if fault == "packet":
            body["canonical_facts"]["main_sha"] = "f" * 40
        elif fault == "intent":
            body["human_input"] = "An unrelated Human request"
        else:
            body["selected_flow"] = "REPAIR_AUTHORING"
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    elif fault == "return":
        package = req["return_contract_package"]
        package["contract"]["candidate_contract"]["requirements"][0] = "Different return requirements"
        package["return_contract_ref"] = return_contract_ref(package["contract"], package["bounds"])
    elif fault == "source":
        req["task_source_context"]["task"]["problem"] = "Changed task subject"
    elif fault == "subject":
        req["tca_subject"]["sources"]["intent"]["digest"] = "f" * 64
    elif fault == "profile":
        req["audit_profile_package"]["audit_profile_ref"]["digest"] = "f" * 64
    elif fault == "stage":
        req["request_mode"] = "V1_OPTION_RECONCILE"
    elif fault == "binding":
        req["external_bindings"]["revision"] += 1
    else:
        req["stage1_lineage"]["construct"]["subject_fingerprint"] = "f" * 64
    req["request_fingerprint"] = digest({k: v for k, v in req.items() if k != "request_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_request(req)


@pytest.mark.parametrize("fault", ("candidate", "closure", "subject", "counterexample", "first_blocker", "private"))
def test_tca_rehashed_decision_revalidates_semantics_and_does_not_trust_hashes(registry, tca_package, fault):
    req = tca_request(registry, tca_package, unknown=fault == "first_blocker")
    req = task_audit_second(req, validate_response(req, tca_response(req)))
    final = validate_response(req, tca_response(req))
    semantic = final["semantic_value"]
    if fault == "candidate":
        semantic["handoff_candidate"]["scope"]["modify"].append("src/aios_renew/task.py")
        semantic["closure"]["candidate_fingerprint"] = digest(semantic["handoff_candidate"])
    elif fault == "closure":
        semantic["closure"]["outcome"] = "PASS"
    elif fault == "subject":
        semantic["subject_fingerprint"] = "f" * 64
    elif fault == "counterexample":
        semantic["challenges"][0]["counterexample"] = ""
    elif fault == "first_blocker":
        semantic["reconciliation"] = []
        semantic["closure"]["outcome"] = "CANDIDATE"
    else:
        semantic["challenges"][0]["basis"] = {"raw_logs": "private"}
    semantic["audit_fingerprint"] = digest({k: v for k, v in semantic.items() if k != "audit_fingerprint"})
    final["decision_fingerprint"] = digest({k: v for k, v in final.items() if k != "decision_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(final, req)


def test_explicit_human_v1_only_option_is_one_exact_normative_audit(registry, v1_only_package, tca_package):
    supplied, contract, bindings, context = tca_inputs(registry)
    option = human_v1_selection(supplied, contract, bindings, context, v1_only_package)
    req = construct_request(supplied, contract, bindings, v1_only_package, request_mode="V1_OPTION_CONSTRUCT",
                            task_source_context=context, human_v1_option=option)
    assert "tca_subject" not in req and req["audit_profile_package"] == v1_only_package
    first = validate_response(req, response(req, context["task"]))
    second = task_audit_second(req, first)
    final = validate_response(second, stage2_response(second, context["task"]))
    assert revalidate_decision(final, second) == final
    assert final["audit_profile_ref"] == v1_only_package["audit_profile_ref"]
    assert all("tca_subject" not in r for r in (req, second))
    tca = tca_request(registry, tca_package)
    tca_first_decision = validate_response(tca, tca_response(tca))
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, v1_only_package, request_mode="V1_OPTION_RECONCILE",
                          stage1_decision=tca_first_decision, task_source_context=context, human_v1_option=option)
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, tca_package, request_mode="TCA_RECONCILE",
                          stage1_decision=first, task_source_context=context)


@pytest.mark.parametrize("fault", ("missing", "unauthorized", "option", "packet", "work_context", "return", "binding", "source", "digest", "profile", "v3", "dual"))
def test_human_v1_option_never_infers_authority_or_falls_back(registry, v1_only_package, tca_package, fault):
    supplied, contract, bindings, context = tca_inputs(registry)
    package = deepcopy(v1_only_package)
    option = human_v1_selection(supplied, contract, bindings, context, package)
    if fault == "missing":
        option = None
    elif fault == "unauthorized":
        option["selected_by"] = "EXECUTOR"
    elif fault == "option":
        option["option"] = "V1_PLUS_TCA"
    elif fault in ("packet", "work_context", "source"):
        key = {"packet": "packet_fingerprint", "work_context": "work_context_fingerprint", "source": "source_context_fingerprint"}[fault]
        option[key] = "f" * 64
    elif fault == "return":
        option["return_contract_ref"] = {**option["return_contract_ref"], "digest": "f" * 64}
    elif fault == "binding":
        option["external_bindings"] = {**bindings, "revision": 2}
    elif fault == "digest":
        option["audit_profile_ref"] = {**option["audit_profile_ref"], "digest": "f" * 64}
    elif fault == "profile":
        package = tca_package
    elif fault == "v3":
        profile = next(p for p in parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
                       if p["id"] == "brain-high-value-v3")
        package = {"profile": profile, "audit_profile_ref": profile_ref(profile)}
    else:
        package = [package, tca_package]
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, package, request_mode="V1_OPTION_CONSTRUCT",
                          task_source_context=context, human_v1_option=option)


def test_tca_and_option_sources_cannot_be_injected_into_ordinary_historical_modes(registry, v1_only_package):
    supplied, contract, bindings, context = tca_inputs(registry)
    option = human_v1_selection(supplied, contract, bindings, context, v1_only_package)
    for extra in ({"task_source_context": context}, {"human_v1_option": option}):
        with pytest.raises(BrainProviderProtocolError):
            construct_request(supplied, contract, bindings, v1_only_package, request_mode="AUDIT_CONSTRUCT", **extra)


@pytest.fixture
def moa_package():
    profile = next(p for p in parse_profile_registry(
        (ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"] if p["id"] == "moa-1")
    return {"profile": profile, "audit_profile_ref": profile_ref(profile)}


def moa_inputs(registry, *, frontier=False):
    body, sources = moa_packet(frontier=frontier)
    supplied = DecisionPacket(body)
    return supplied, select_return_contract(registry, supplied), {}, sources


def moa_request(registry, package, *, frontier=False):
    supplied, contract, bindings, sources = moa_inputs(registry, frontier=frontier)
    return construct_request(supplied, contract, bindings, package, request_mode="MOA_CONSTRUCT", moa_source_context=sources)


def moa_response(req):
    body, profile, subject = req["decision_packet"], req["audit_profile_package"]["profile"], req["moa_subject"]
    semantic = (moa_first(body, profile, subject) if req["request_mode"] == "MOA_CONSTRUCT" else
                moa_final(body, profile, subject, req["stage1_lineage"]["construct"]))
    return {"request_fingerprint": req["request_fingerprint"], **semantic}


def moa_second(req, first):
    return construct_request(DecisionPacket(req["decision_packet"]), req["return_contract_package"],
        req["external_bindings"], req["audit_profile_package"], request_mode="MOA_RECONCILE",
        stage1_decision=first, moa_source_context=req["moa_source_context"])


@pytest.mark.parametrize("frontier", (False, True))
def test_moa_two_pass_requests_and_decisions_round_trip_without_a_new_ingress(registry, moa_package, frontier):
    req = moa_request(registry, moa_package, frontier=frontier)
    assert req["stage1_lineage"] is None
    assert revalidate_request(json.loads(json.dumps(req))) == req
    first = validate_response(req, moa_response(req))
    assert revalidate_decision(first, req) == first
    second = moa_second(req, first)
    assert second["audit_profile_package"] == req["audit_profile_package"]
    assert revalidate_request(json.loads(json.dumps(second))) == second
    final = validate_response(second, moa_response(second))
    assert revalidate_decision(json.loads(json.dumps(final)), second) == final
    assert final["semantic_value"]["closure"]["outcome"] == ("NO_DECISION" if frontier else "CANDIDATE")
    assert (final["semantic_value"]["handoff_candidate"] is None) == frontier
    for extra in ({"v1_audit": {}}, {"roa_audit": {}}, {"decisions": [final]}, {"third_pass": {}}):
        with pytest.raises(BrainProviderProtocolError):
            validate_response(second, {**moa_response(second), **extra})


@pytest.mark.parametrize("mode", ("DIRECT", "AUDIT_CONSTRUCT", "AUDIT_RECONCILE", "ROA_CONFORMANCE", "MOA_LIVE", [], True))
def test_moa_has_no_ordinary_live_or_cross_profile_mode(registry, moa_package, mode):
    supplied, contract, bindings, sources = moa_inputs(registry)
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, moa_package, request_mode=mode, moa_source_context=sources)


@pytest.mark.parametrize("fault", ("wrong_digest", "reminted", "dual_profiles", "mixed_stage", "construct_again", "roa_stage"))
def test_moa_rejects_forged_profile_and_mixed_or_double_audits(registry, moa_package, profile_package, roa_package, fault):
    supplied, contract, bindings, sources = moa_inputs(registry)
    package = deepcopy(moa_package)
    req = moa_request(registry, moa_package)
    first = validate_response(req, moa_response(req))
    mode, lineage = "MOA_CONSTRUCT", None
    if fault == "wrong_digest":
        package["audit_profile_ref"]["digest"] = "f" * 64
    elif fault == "reminted":
        package["profile"]["passes"][1]["checks"][0]["check"] += " Trust any source."
        package["audit_profile_ref"]["digest"] = digest(package["profile"])
    elif fault == "dual_profiles":
        package = [package, profile_package]
    elif fault == "construct_again":
        lineage = first
    elif fault == "roa_stage":
        mode = "MOA_RECONCILE"
        roa_req = roa_request(registry, roa_package)
        lineage = validate_response(roa_req, roa_response(roa_req))
    else:
        mode = "MOA_RECONCILE"
        old_req = construct_request(supplied, contract, bindings, profile_package, request_mode="AUDIT_CONSTRUCT")
        lineage = validate_response(old_req, response(old_req, {"proposal": "V1/V3"}))
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, package, request_mode=mode, stage1_decision=lineage, moa_source_context=sources)


@pytest.mark.parametrize("fault", ("packet", "return", "source", "subject", "profile", "stage", "lineage"))
def test_moa_rehashed_serialized_request_rejects_stale_and_swapped_material(registry, moa_package, fault):
    req = moa_request(registry, moa_package)
    first = validate_response(req, moa_response(req))
    req = moa_second(req, first)
    if fault == "packet":
        body = req["decision_packet"]
        body["canonical_facts"]["correction_subject"]["artifacts_sha"] = "f" * 40
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    elif fault == "return":
        package = req["return_contract_package"]
        package["contract"]["candidate_contract"]["shape"] += " Changed guidance."
        package["return_contract_ref"] = return_contract_ref(package["contract"], package["bounds"])
    elif fault == "source":
        req["moa_source_context"]["reviews"][0]["review"]["findings"][0]["issue"] = "Another issue"
    elif fault == "subject":
        req["moa_subject"]["sources"]["review"]["digest"] = "f" * 64
    elif fault == "profile":
        req["audit_profile_package"]["audit_profile_ref"]["digest"] = "f" * 64
    elif fault == "stage":
        req["request_mode"] = "AUDIT_RECONCILE"
    else:
        req["stage1_lineage"]["stage1_decision_fingerprint"] = "f" * 64
    req["request_fingerprint"] = digest({k: v for k, v in req.items() if k != "request_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_request(req)


@pytest.mark.parametrize("fault", ("subject", "construct", "evidence", "candidate", "closure", "extra"))
def test_moa_rehashed_final_decision_revalidates_the_exact_semantic_closure(registry, moa_package, fault):
    req = moa_request(registry, moa_package)
    req = moa_second(req, validate_response(req, moa_response(req)))
    final = validate_response(req, moa_response(req))
    semantic = final["semantic_value"]
    if fault == "subject":
        semantic["subject_fingerprint"] = "f" * 64
    elif fault == "construct":
        semantic["construct_fingerprint"] = "f" * 64
    elif fault == "evidence":
        semantic["challenges"][1]["evidence_refs"] = []
    elif fault == "candidate":
        semantic["handoff_candidate"]["action"] = "EVIDENCE_ONLY"
        semantic["closure"]["candidate_fingerprint"] = digest(semantic["handoff_candidate"])
    elif fault == "closure":
        semantic["closure"]["knowledge"] = "OBSERVED"
    else:
        semantic["second_audit"] = {}
    semantic["audit_fingerprint"] = digest({k: v for k, v in semantic.items() if k != "audit_fingerprint"})
    final["decision_fingerprint"] = digest({k: v for k, v in final.items() if k != "decision_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(final, req)


def test_moa_absent_source_package_stays_unknown_and_cannot_be_supplied_by_model(registry, moa_package):
    supplied, contract, bindings, _ = moa_inputs(registry)
    req = construct_request(supplied, contract, bindings, moa_package, request_mode="MOA_CONSTRUCT")
    assert req["moa_subject"]["sources"]["review"] is None
    first = validate_response(req, moa_response(req))
    second = moa_second(req, first)
    final = validate_response(second, moa_response(second))
    assert final["semantic_value"]["closure"]["outcome"] == "NO_DECISION"
    assert final["semantic_value"]["closure"]["knowledge"] == "UNKNOWN"
    with pytest.raises(BrainProviderProtocolError):
        validate_response(req, {**moa_response(req), "moa_source_context": {"reviews": []}})


@pytest.fixture
def roa_package():
    profile = next(p for p in parse_profile_registry(
        (ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"] if p["id"] == "roa-1-r2")
    return {"profile": profile, "audit_profile_ref": profile_ref(profile)}


def roa_inputs(registry):
    supplied = DecisionPacket(roa_packet())
    return supplied, select_return_contract(registry, supplied), {"repair_id": "REPAIR-999-001"}


def roa_request(registry, package):
    supplied, contract, bindings = roa_inputs(registry)
    return construct_request(supplied, contract, bindings, package, request_mode="ROA_CONFORMANCE")


def roa_response(req, *, unknown=None):
    return {"request_fingerprint": req["request_fingerprint"],
            **roa_output(req["roa_subject"], req["audit_profile_package"]["profile"], unknown=unknown)}


@pytest.mark.parametrize("unknown", (None, 1, 4))
def test_roa_portable_subject_and_typed_decision_round_trip(registry, roa_package, unknown):
    req = roa_request(registry, roa_package)
    before = deepcopy(req)
    assert req["request_mode"] == "ROA_CONFORMANCE" and req["stage1_lineage"] is None
    assert revalidate_request(json.loads(json.dumps(req))) == req
    decision = validate_response(req, roa_response(req, unknown=unknown))
    assert revalidate_decision(json.loads(json.dumps(decision)), req) == decision
    assert decision["decision_mode"] == "ROA_CONFORMANCE"
    assert decision["semantic_value"]["closure"]["outcome"] == ("CANDIDATE" if unknown is None else "NO_DECISION")
    assert decision["semantic_value"]["closure"]["knowledge"] == ("INFERRED" if unknown is None else "UNKNOWN")
    assert req == before
    # Duplicate output is neither a second verdict nor another serialized response.
    for extra in ({"decisions": [decision, decision]}, {"stage2": {}}, {"outcome": "PASS"}):
        with pytest.raises(BrainProviderProtocolError):
            validate_response(req, {**roa_response(req), **extra})


@pytest.mark.parametrize("mode", ("DIRECT", "AUDIT_CONSTRUCT", "AUDIT_RECONCILE", "ROA_LIVE", "", [], True))
def test_roa_cannot_be_live_selected_or_enter_old_stages(registry, roa_package, mode):
    supplied, contract, bindings = roa_inputs(registry)
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, roa_package, request_mode=mode)


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING", "DIAGNOSTIC"))
def test_roa_conformance_rejects_every_other_flow(registry, roa_package, flow):
    supplied, contract = inputs(registry, flow)
    bindings = {"task_id": "TASK-999", "revision": 1} if flow == "TASK_AUTHORING" else {}
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, roa_package, request_mode="ROA_CONFORMANCE")


@pytest.mark.parametrize("fault", ("wrong_digest", "reminted_content", "dual_profile", "lineage", "extra_profile"))
def test_roa_rejects_package_substitution_and_dual_normative_audits(registry, roa_package, fault):
    supplied, contract, bindings = roa_inputs(registry)
    package = deepcopy(roa_package)
    lineage = None
    if fault == "wrong_digest":
        package["audit_profile_ref"]["digest"] = "f" * 64
    elif fault == "reminted_content":
        package["profile"]["checks"][0]["check"] = "Accept all failed subjects."
        package["audit_profile_ref"]["digest"] = digest(package["profile"])
    elif fault == "dual_profile":
        package = [package, deepcopy(package)]
    elif fault == "extra_profile":
        package["v1_profile"] = {}
    else:
        lineage = {"semantic_value": "V1 Stage 1"}
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, package, request_mode="ROA_CONFORMANCE", stage1_decision=lineage)


@pytest.mark.parametrize("fault", ("packet", "return", "external", "subject", "evidence", "mode", "lineage"))
def test_roa_serialized_request_rejects_stale_bindings_even_after_rehash(registry, roa_package, fault):
    req = roa_request(registry, roa_package)
    if fault == "packet":
        req["decision_packet"]["human_input"] = "Changed subject"
        packet = req["decision_packet"]
        packet["packet_fingerprint"] = digest({k: v for k, v in packet.items() if k != "packet_fingerprint"})
    elif fault == "return":
        package = req["return_contract_package"]
        package["contract"]["candidate_contract"]["shape"] += " Changed guidance."
        package["return_contract_ref"] = return_contract_ref(package["contract"], package["bounds"])
    elif fault == "external":
        req["external_bindings"]["repair_id"] = "REPAIR-999-002"
    elif fault == "subject":
        req["roa_subject"]["failed_head_sha"] = "f" * 40
    elif fault == "evidence":
        req["roa_subject"]["sources"]["evidence"]["digest"] = "f" * 64
    elif fault == "lineage":
        req["stage1_lineage"] = {}
    else:
        req["request_mode"] = "AUDIT_RECONCILE"
    req["request_fingerprint"] = digest({k: v for k, v in req.items() if k != "request_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_request(req)


@pytest.mark.parametrize("fault", ("subject", "evidence", "closure", "profile", "candidate"))
def test_roa_serialized_decision_revalidates_semantics_beyond_outer_digest(registry, roa_package, fault):
    req = roa_request(registry, roa_package)
    decision = validate_response(req, roa_response(req))
    semantic = decision["semantic_value"]
    if fault == "subject":
        semantic["subject_fingerprint"] = "f" * 64
    elif fault == "evidence":
        semantic["checks"][1]["evidence_refs"][0]["digest"] = "f" * 64
    elif fault == "closure":
        semantic["closure"]["knowledge"] = "OBSERVED"
    elif fault == "profile":
        semantic["audit_profile_ref"]["id"] = "aios-adaptive-brain-audit-v1"
    else:
        semantic["handoff_candidate"]["task"]["revision"] = 2
        semantic["closure"]["candidate_fingerprint"] = digest(semantic["handoff_candidate"])
    semantic["audit_fingerprint"] = digest({k: v for k, v in semantic.items() if k != "audit_fingerprint"})
    decision["decision_fingerprint"] = digest({k: v for k, v in decision.items() if k != "decision_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(decision, req)


def test_roa_and_historical_decisions_cannot_supply_each_others_lineage(registry, roa_package, profile_package):
    supplied, contract, bindings = roa_inputs(registry)
    req = roa_request(registry, roa_package)
    roa = validate_response(req, roa_response(req))
    old_request = construct_request(supplied, contract, bindings, profile_package, request_mode="AUDIT_CONSTRUCT")
    old = validate_response(old_request, response(old_request, {**bindings, "proposal": "Historical"}))
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, profile_package, request_mode="AUDIT_RECONCILE", stage1_decision=roa)
    with pytest.raises(BrainProviderProtocolError):
        construct_request(supplied, contract, bindings, roa_package, request_mode="ROA_CONFORMANCE", stage1_decision=old)
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(old, req)
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(roa, old_request)


def test_provider_protocol_transports_only_closed_failure_context_and_rejects_raw_fields(registry):
    from aios_renew.review_transport import failure_context
    from tests.test_review_transport import _native_failure_fixture
    supplied, package = inputs(registry, "DIAGNOSTIC")
    _, private = _native_failure_fixture()
    context = failure_context(private, artifact_sha="c" * 40)
    body = supplied.as_dict()
    body["canonical_facts"]["failure_context"] = context
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    request = construct_request(DecisionPacket(body), package, {}, request_mode="DIRECT")
    assert request["decision_packet"]["canonical_facts"]["failure_context"] == context
    assert revalidate_request(request) == request
    assert "synthetic-secret" not in json.dumps(request)
    body["canonical_facts"]["failure_context"]["diagnostic"]["detail"] = "credential=synthetic-secret"
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    with pytest.raises(BrainProviderProtocolError, match="FAILURE context"):
        construct_request(DecisionPacket(body), package, {}, request_mode="DIRECT")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


@pytest.fixture
def registry():
    return parse_return_contract_registry((ROOT / ".ai/brain-return-contracts.yaml").read_bytes())


@pytest.fixture(params=("brain-high-value-v3", "aios-adaptive-brain-audit-v1"))
def profile_package(request):
    profile = next(p for p in parse_profile_registry(
        (ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"] if p["id"] == request.param)
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
    material = {
        "request_fingerprint": req["request_fingerprint"],
        "construct_audit": [{"lens": lens, "outcome": "CLEAR"} for lens in lenses],
        "reconciled_candidate": candidate, "closure": closure,
        "outcome": "NO_DECISION" if blocker else "CANDIDATE",
    }
    if req["decision_packet"]["selected_flow"] == "TASK_AUTHORING":
        if "conformance" in req["audit_profile_package"]["profile"]:
            material.update(v3_sections(req["stage1_lineage"]["construct"], candidate))
        material["acceptance_phase_ledger"] = [
            {"id": entry["id"], "phase": "CLAIM_NOW"} for entry in candidate.get("acceptance", [])
        ]
    return material


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


@pytest.mark.parametrize("section", ("cross_authority_context", "canonical_shape", "terminal_lifecycle"))
@pytest.mark.parametrize("fault", ("missing", "unknown_field", "stale_packet", "stale_candidate", "invalid_status"))
@pytest.mark.parametrize("blocker", (False, True))
def test_v3_task_stage2_sections_are_closed_bound_and_revalidated(registry, profile_package, section, fault, blocker):
    first = request(registry, profile_package)
    candidate = {"task_id": "TASK-999", "revision": 1}
    stage1 = validate_response(first, response(first, candidate))
    second = construct_request(DecisionPacket(first["decision_packet"]), first["return_contract_package"],
                               first["external_bindings"], first["audit_profile_package"],
                               request_mode="AUDIT_RECONCILE", stage1_decision=stage1)
    material = stage2_response(second, candidate, blocker=blocker)
    assert set(material) == {"request_fingerprint", "construct_audit", "reconciled_candidate", "closure",
                             "outcome", "acceptance_phase_ledger", "cross_authority_context",
                             "canonical_shape", "terminal_lifecycle"}
    decision = validate_response(second, material)
    assert revalidate_decision(decision, second) == decision
    assert decision["semantic_value"][section] == material[section]

    def corrupt(value):
        if fault == "missing":
            del value[section]
        elif fault == "unknown_field":
            value[section]["future_authority"] = "injected"
        elif fault == "stale_packet":
            value[section]["packet_fingerprint"] = "0" * 64
        elif fault == "stale_candidate":
            value[section]["reconciled_candidate_fingerprint"] = "0" * 64
        else:
            value[section]["status"] = "UNKNOWN"

    invalid = deepcopy(material)
    corrupt(invalid)
    with pytest.raises(BrainProviderProtocolError):
        validate_response(second, invalid)
    altered = deepcopy(decision)
    corrupt(altered["semantic_value"])
    altered["decision_fingerprint"] = digest({k: v for k, v in altered.items() if k != "decision_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(altered, second)


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


@pytest.mark.parametrize("blocker", (False, True))
def test_task_phase_ledger_binds_serialized_decision_identity(registry, profile_package, blocker):
    first = request(registry, profile_package)
    candidate = {"task_id": "TASK-999", "revision": 1,
                 "acceptance": [{"id": "AC1", "condition": "A property exists"}]}
    stage1 = validate_response(first, response(first, candidate))
    second = construct_request(DecisionPacket(first["decision_packet"]), first["return_contract_package"],
                               first["external_bindings"], first["audit_profile_package"],
                               request_mode="AUDIT_RECONCILE", stage1_decision=stage1)
    material = stage2_response(second, candidate, blocker=blocker)
    final = validate_response(second, material)
    assert revalidate_decision(final, second) == final
    for ledger in (
        [], [{"id": "AC1", "phase": "PROOF_LATER"}],
        [{"id": "OTHER", "phase": "CLAIM_NOW"}],
        material["acceptance_phase_ledger"] * 2,
    ):
        # Protocol never applies the final ingress coverage/phase rule.
        changed = validate_response(second, {**material, "acceptance_phase_ledger": ledger})
        assert changed["decision_fingerprint"] != final["decision_fingerprint"]
        assert changed["semantic_value"]["stage2_fingerprint"] != final["semantic_value"]["stage2_fingerprint"]
        assert revalidate_decision(changed, second) == changed
        tampered = deepcopy(final)
        tampered["semantic_value"]["acceptance_phase_ledger"] = ledger
        # Rehashing the outer envelope cannot retain the audited Stage-2 identity.
        tampered["decision_fingerprint"] = digest({k: v for k, v in tampered.items() if k != "decision_fingerprint"})
        with pytest.raises(BrainProviderProtocolError):
            revalidate_decision(tampered, second)
    missing = deepcopy(material)
    del missing["acceptance_phase_ledger"]
    with pytest.raises(BrainProviderProtocolError):
        validate_response(second, missing)
    for ledger in (None, [{"id": "AC1", "phase": "UNKNOWN"}],
                   [{"id": "AC1", "phase": "CLAIM_NOW", "basis": "Not allowed"}]):
        with pytest.raises(BrainProviderProtocolError):
            validate_response(second, {**material, "acceptance_phase_ledger": ledger})


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING"))
def test_non_task_provider_stage2_rejects_task_phase_ledger(registry, profile_package, flow):
    bindings = {"repair_id": "REPAIR-999-001"} if flow == "REPAIR_AUTHORING" else {}
    first = request(registry, profile_package, flow=flow, bindings=bindings)
    candidate = {**bindings, "proposal": "Bounded"}
    stage1 = validate_response(first, response(first, candidate))
    second = construct_request(DecisionPacket(first["decision_packet"]), first["return_contract_package"],
                               bindings, first["audit_profile_package"], request_mode="AUDIT_RECONCILE",
                               stage1_decision=stage1)
    material = stage2_response(second, candidate)
    decision = validate_response(second, material)
    assert revalidate_decision(decision, second) == decision
    assert "acceptance_phase_ledger" not in decision["semantic_value"]
    with pytest.raises(BrainProviderProtocolError):
        validate_response(second, {**material, "acceptance_phase_ledger": []})
    altered = deepcopy(decision)
    altered["semantic_value"]["acceptance_phase_ledger"] = []
    altered["decision_fingerprint"] = digest({k: v for k, v in altered.items() if k != "decision_fingerprint"})
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(altered, second)
    for section, value in v3_sections(stage1["semantic_value"], candidate).items():
        with pytest.raises(BrainProviderProtocolError):
            validate_response(second, {**material, section: value})


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


@pytest.mark.parametrize("profile_id", ("brain-high-value-v2", "brain-high-value-v3", "aios-adaptive-brain-audit-v1"))
@pytest.mark.parametrize("flow", ("ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING"))
def test_exact_profile_provider_replay_and_cross_profile_rejection(registry, profile_id, flow):
    profiles = parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
    policy = next(p for p in profiles if p["id"] == profile_id)
    package = {"profile": policy, "audit_profile_ref": profile_ref(policy)}
    bindings = ({"task_id": "TASK-999", "revision": 1} if flow == "TASK_AUTHORING"
                else {"repair_id": "REPAIR-999"} if flow == "REPAIR_AUTHORING" else {})
    first = request(registry, package, flow=flow, bindings=bindings)
    candidate = {**bindings, "proposal": "Same bounded subject"}
    stage1 = validate_response(first, response(first, candidate))
    second = construct_request(DecisionPacket(first["decision_packet"]), first["return_contract_package"],
                               bindings, package, request_mode="AUDIT_RECONCILE", stage1_decision=stage1)
    for blocker in (False, True):
        material = stage2_response(second, candidate, blocker=blocker)
        final = validate_response(second, material)
        assert revalidate_decision(final, second) == final
        assert validate_response(second, material) == final
        assert final["audit_profile_ref"] == profile_ref(policy)
        if profile_id == "brain-high-value-v2":
            assert "terminal_lifecycle" not in final["semantic_value"]
        for other in profiles:
            if other["id"] == profile_id:
                continue
            replacement = {"profile": other, "audit_profile_ref": profile_ref(other)}
            with pytest.raises(BrainProviderProtocolError):
                construct_request(DecisionPacket(first["decision_packet"]), first["return_contract_package"],
                                  bindings, replacement, request_mode="AUDIT_RECONCILE", stage1_decision=stage1)
            with pytest.raises(BrainProviderProtocolError):
                revalidate_decision(final, request(registry, replacement, flow=flow, bindings=bindings))


@pytest.mark.parametrize("fault", ("digest", "unknown_id", "partial", "renamed_v3"))
def test_v1_provider_package_rejects_digest_unknown_and_partial_adaptation(registry, fault):
    profiles = parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
    policy = deepcopy(next(p for p in profiles if p["id"] == "aios-adaptive-brain-audit-v1"))
    historical = next(p for p in profiles if p["id"] == "brain-high-value-v3")
    reference = profile_ref(policy)
    if fault == "digest":
        reference["digest"] = "0" * 64
    elif fault == "unknown_id":
        policy["id"] = reference["id"] = "unknown-adaptive-v1"
    elif fault == "partial":
        policy["lenses"][3]["check"] = historical["lenses"][3]["check"]
        reference["digest"] = digest(policy)
    else:
        policy = {**deepcopy(historical), "id": reference["id"], "version": 1}
        reference["digest"] = digest(policy)
    with pytest.raises(BrainProviderProtocolError):
        request(registry, {"profile": policy, "audit_profile_ref": reference})


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING"))
@pytest.mark.parametrize("fault", (None, "missing_profile", "changed_digest", "invalid_stage2"))
def test_isolated_historical_v1_provider_attempt_has_one_package_two_invocations_and_no_fallback(registry, flow, fault):
    import yaml
    from aios_renew.brain_provider import BrainAttemptError, MappingBrainProvider, attempt

    cutover = yaml.safe_load((ROOT / ".ai/roadmap-state.yaml").read_bytes())[
        "adaptive_brain_audit_v1_priority_20261012"]["exclusive_prospective_cutover_20261012"]
    reference = {"id": cutover["profile_id"], "version": cutover["profile_version"],
                 "digest": cutover["profile_digest"]}
    assert reference == {"id": "aios-adaptive-brain-audit-v1", "version": 1,
                         "digest": "982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718"}
    policy = next(p for p in parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
                  if profile_ref(p) == reference)
    package = {"profile": policy, "audit_profile_ref": deepcopy(reference)}
    if fault == "missing_profile":
        package = None
    elif fault == "changed_digest":
        package["audit_profile_ref"]["digest"] = "0" * 64
    supplied, contract = inputs(registry, flow)
    bindings = ({"task_id": "TASK-999", "revision": 1} if flow == "TASK_AUTHORING" else
                {"repair_id": "REPAIR-999"} if flow == "REPAIR_AUTHORING" else {})
    candidate = {**bindings, "proposal": "Single prospective V1 audit"}
    calls, fresh_calls = [], []
    def callback(req):
        calls.append(deepcopy(req))
        semantic = (response(req, candidate) if req["request_mode"] == "AUDIT_CONSTRUCT" else
                    stage2_response(req, candidate))
        if fault == "invalid_stage2" and req["request_mode"] == "AUDIT_RECONCILE":
            semantic["closure"].pop()
        return {"semantic_response": semantic, "attribution": {
            "provider": "fixture", "model": "fixed", "session_id": None, "invocation_id": str(len(calls))}}
    def fresh():
        fresh_calls.append(None)
        return DecisionPacket(supplied.as_dict())
    provider = MappingBrainProvider("fixture", "fixed", callback)
    if fault:
        with pytest.raises(BrainAttemptError) as caught:
            attempt(provider, supplied, contract, bindings, package, fresh, conformance_only=True)
        expected_count = 2 if fault == "invalid_stage2" else 0
        assert caught.value.invocation_count == len(calls) == expected_count
        assert len(fresh_calls) == (1 if expected_count else 0)
    else:
        result = attempt(provider, supplied, contract, bindings, package, fresh, conformance_only=True)
        assert len(calls) == len(result.attributions) == 2 and len(fresh_calls) == 1
        assert result.decision["audit_profile_ref"] == reference
        assert result.decision["semantic_value"]["outcome"] == "CANDIDATE"
        assert revalidate_decision(result.decision, calls[1]) == result.decision
    if calls:
        assert [item["request_mode"] for item in calls] == ["AUDIT_CONSTRUCT", "AUDIT_RECONCILE"]
        assert all(item["audit_profile_package"] == package for item in calls)
        assert calls[1]["stage1_lineage"]["construct"]["audit_profile_ref"] == reference


@pytest.mark.parametrize("bad", (
    {"nested": {"Canonical-Shape": {}}}, {"nested": {"terminalLifecycle": {}}},
    {"nested": {"format": "AIOS_BRAIN_REQUEST", "candidate": {}}},
    {"nested": {"format": []}}, {"nested": {"format": {"kind": "audit"}}},
    {"nested": {"path": "C:/private/context"}}, {"nested": {"path": "/home/private/context"}},
    {"nested": {"focus_notes": "Only F-F"}},
))
def test_v1_provider_rejects_nested_candidate_metadata_and_local_paths(registry, bad):
    policy = next(p for p in parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
                  if p["id"] == "aios-adaptive-brain-audit-v1")
    first = request(registry, {"profile": policy, "audit_profile_ref": profile_ref(policy)})
    candidate = {"task_id": "TASK-999", "revision": 1}
    with pytest.raises(BrainProviderProtocolError):
        validate_response(first, response(first, {**candidate, **bad}))
    stage1 = validate_response(first, response(first, candidate))
    second = construct_request(DecisionPacket(first["decision_packet"]), first["return_contract_package"],
                               first["external_bindings"], first["audit_profile_package"],
                               request_mode="AUDIT_RECONCILE", stage1_decision=stage1)
    with pytest.raises(BrainProviderProtocolError):
        validate_response(second, stage2_response(second, {**candidate, **bad}))
