"""Focused BP5-P3 non-network invocation conformance."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

from aios_renew.brain_provider import (
    BrainAttemptError, JsonBrainProvider, MappingBrainProvider, ProviderInvocation,
    ProviderResponseError, attempt,
)
from aios_renew.brain_provider_protocol import (
    BrainProviderProtocolError, construct_request, revalidate_decision, validate_response,
)
from aios_renew.decision_packet import DecisionPacket
from tests.test_brain_context import (
    CanonicalAuditRepository, task395_activation_record, task395_canonical_audit_repo,
    task406_canonical_audit_repo,
    task408_canonical_audit_repo,
)
from test_brain_provider_protocol import (
    digest, inputs, registry, response, roa_inputs, roa_package, roa_response, stage2_response,
    moa_inputs, moa_package, moa_response,
    tca_inputs, tca_package, tca_response, v1_only_package, human_v1_selection,
    aoa_inputs, aoa_package, aoa_response, aoa_candidate,
    R5_FLOWS, r5_case, r5_response, audit_policy_example,
    canonical_audit_repo, production_inputs,
    task408_inputs, task408_policy, task408_choice, ExactHumanChoice,
)


DIRECT = {"proposal": "Investigate", "uncertainty": {"status": "NONE", "summary": None}}
TASK = {"task_id": "TASK-999", "revision": 1}


def caa_attempt_inputs(tmp_path, registry, flow, aoa_package):
    from aios_renew.brain_return_contract import select_return_contract
    from tests.test_brain_audit import aoa_example, moa_candidate
    from tests.test_caa_integration import Case

    case = Case(tmp_path, flow)
    body = case.packet.as_dict()
    contract = select_return_contract(registry, case.packet)
    sources, bindings, package = {}, {}, None
    if flow == "TASK_AUTHORING":
        candidate = deepcopy(case.fixture.task)
        candidate["verification"]["policy"] = "minimum-sufficient-v1"
        bindings = {k: candidate[k] for k in ("task_id", "revision")}
    elif flow == "REPAIR_AUTHORING":
        bindings = {"repair_id": "REPAIR-402-099"}
        task = body["canonical_facts"]["task_contract"]
        candidate = dict(**bindings, failed_run_id=body["subject"]["failed_run_id"],
            failed_head_sha=body["subject"]["failed_head_sha"], task=body["canonical_facts"]["selected_task"],
            action="CONTINUE_IMPLEMENTATION", modification_scope=task["scope"]["modify"],
            instructions=["Brain independently chooses the existing lawful strategy."], constraints=task["constraints"]["hard"])
    elif flow == "REMEDIATION_AUTHORING":
        candidate = moa_candidate(body)
    else:
        _, context = aoa_example()
        context["provenance"].update(repository=body["canonical_facts"]["repository"],
            main_sha=body["canonical_facts"]["main_sha"], work_context_fingerprint=body["work_context_fingerprint"])
        sources = dict(architecture_source_context=context)
        candidate, package = {}, aoa_package
    policy = task408_policy(body)
    def authenticate(selection, _phase):
        return (selection["policy"] == policy and selection["packet_fingerprint"] == body["packet_fingerprint"]
                and selection["subject_fingerprint"] == digest(body["subject"]))
    return case, contract, bindings, package, sources, candidate, policy, authenticate


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "TASK_AUTHORING", "REPAIR_AUTHORING", "REMEDIATION_AUTHORING"))
@pytest.mark.parametrize("kind", ("mapping", "json"))
@pytest.mark.parametrize("outcome", ("ADVICE", "NO_MATERIAL_ADVICE"))
def test_ordinary_attempt_automatically_consults_before_decision_in_all_four_flows(
        tmp_path, registry, aoa_package, monkeypatch, flow, kind, outcome):
    from aios_renew import brain_context, caa_integration
    from tests.test_caa_integration import provider as caa_provider, response as caa_response

    case, contract, bindings, package, sources, candidate, policy, authenticate = caa_attempt_inputs(
        tmp_path, registry, flow, aoa_package)
    def forbidden(*_args, **_kwargs):
        pytest.fail("ordinary attempt used retired pilot admission")
    monkeypatch.setattr(brain_context, "canonical_caa_pilot_context", forbidden)
    monkeypatch.setattr(caa_integration, "_consult_pilot", forbidden)
    calls, advisories = [], []
    def callback(request):
        calls.append(deepcopy(request))
        if request["format"] == "AIOS_CAA_FLOW_REQUEST":
            semantic = caa_response(request)
            if outcome == "NO_MATERIAL_ADVICE":
                semantic["consultation"].update(outcome=outcome, suggestions=[])
            advisories.append(deepcopy(semantic))
            return semantic
        # Brain's existing transport has completed the advisory exchange before
        # its semantic decision call, without an extension to either grammar.
        assert advisories[0]["binding"]["packet_fingerprint"] == case.packet.packet_fingerprint
        assert advisories[0]["consultation"]["outcome"] == outcome
        assert "caa_advisory" not in request
        # Stop at the actual decision transport boundary. This ordering test
        # does not manufacture final strategy, strict return-family provenance
        # or lifecycle authorization from an advisory-only CAA fixture.
        raise RuntimeError("decision boundary observed")
    provider = caa_provider(kind, callback)
    # No enabled flag, Human per-consult choice, cohort, profile or reservation.
    # These existing policy inputs exercise the supported trusted-caller path;
    # the separate TASK-408 test also covers canonical first-party Git policy.
    with pytest.raises(BrainAttemptError) as stopped:
        attempt(provider, case.packet, contract, bindings, package, case.fresh,
            repo=case.fixture.repo, advisory_candidate=case.candidate,
            audit_policy=policy, selection_authenticator=authenticate, **sources)
    assert stopped.value.reason_code == "PROVIDER_TRANSPORT_FAILURE"
    assert stopped.value.invocation_count == 1
    assert calls[0]["format"] == "AIOS_CAA_FLOW_REQUEST"
    assert sum(r["format"] == "AIOS_CAA_FLOW_REQUEST" for r in calls) == 1
    brain_calls = calls[1:]
    expected = ["AOA_CONSTRUCT"] if flow == "ARCHITECTURE" else ["DIRECT"]
    assert [r["request_mode"] for r in brain_calls] == expected
    assert advisories[0]["consultation"]["outcome"] == outcome
    if flow != "ARCHITECTURE":
        assert brain_calls[0]["audit_profile_package"] is None


def test_default_attempt_composes_advisory_draft_without_an_extra_caller_requirement(tmp_path, registry, aoa_package):
    from tests.test_caa_integration import provider as caa_provider

    case, contract, bindings, package, sources, candidate, policy, authenticate = caa_attempt_inputs(
        tmp_path, registry, "TASK_AUTHORING", aoa_package)
    calls = []
    def callback(request):
        calls.append(deepcopy(request))
        if request["format"] == "AIOS_CAA_FLOW_REQUEST":
            inner = request["consultation"]
            return dict(format="AIOS_CAA_FLOW_RESPONSE", version=1, binding=request["binding"],
                request_fingerprint=request["request_fingerprint"], consultation=dict(
                    request_fingerprint=inner["request_fingerprint"], candidate_digest=inner["candidate_digest"],
                    cut_digest=inner["source_cut"]["digest"], outcome="NO_MATERIAL_ADVICE", suggestions=[]))
        return response(request, candidate)
    result = attempt(caa_provider("mapping", callback), case.packet, contract, bindings,
        fresh_packet_supplier=case.fresh, repo=case.fixture.repo,
        audit_policy=policy, selection_authenticator=authenticate)
    assert calls[-1]["request_mode"] == "DIRECT"
    assert result.decision["semantic_value"] == candidate
    assert result.advisory["outcome"] in {"NO_MATERIAL_ADVICE", "INSUFFICIENT_GROUNDED_EVIDENCE"}
    assert result.advisory["retrieval"]["complete"] is True


@pytest.fixture
def profile_package(v1_only_package):
    # Exact V1 transport compatibility uses explicit isolated conformance.
    return deepcopy(v1_only_package)


@pytest.fixture(autouse=True)
def no_implicit_repository_network(tmp_path, monkeypatch):
    # Production integration tests explicitly select their real Git fixture.
    # Legacy rejection cases must never contact the developer's canonical remote.
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("kind", ("mapping", "json"))
@pytest.mark.parametrize("status", CanonicalAuditRepository.activation_states)
def test_first_party_ordinary_attempt_reads_canonical_policy_and_runs_exact_default(
        registry, canonical_audit_repo, monkeypatch, flow, kind, status):
    # This is the public production entrypoint with neither a policy nor an
    # authenticator/test adapter/conformance flag supplied by the test.
    monkeypatch.chdir(canonical_audit_repo.repo)
    approval = canonical_audit_repo.roadmap[canonical_audit_repo.approval_key]
    approval["status"] = status
    canonical_audit_repo.save_roadmap()
    assert canonical_audit_repo.run["status"] == "ACTIVE"
    assert canonical_audit_repo.run["head_sha"] is None
    assert "head_sha" not in canonical_audit_repo.result_package
    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical_audit_repo)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, provider=kind, model=kind, semantic=r5_response(req))
    if flow == "REMEDIATION_AUTHORING":
        with pytest.raises(BrainAttemptError) as retired:
            attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings,
                                 fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()), **sources)
        assert retired.value.reason_code == "PROTOCOL_INPUT_INVALID"
        assert retired.value.invocation_count == 0 and calls == []
        return
    result = attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings,
                     fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()), **sources)
    modes = {"ARCHITECTURE": ["AOA_CONSTRUCT", "AOA_RECONCILE"],
             "TASK_AUTHORING": ["TCA_CONSTRUCT", "TCA_RECONCILE"],
             "REMEDIATION_AUTHORING": ["MOA_CONSTRUCT", "MOA_RECONCILE"],
             "REPAIR_AUTHORING": ["ROA_CONFORMANCE"]}
    assert [req["request_mode"] for req in calls] == modes[flow]
    assert len(result.attributions) == len(modes[flow])
    assert all(req["audit_profile_package"]["audit_profile_ref"] == approval["target_profiles"][flow] for req in calls)
    assert all(req["audit_selection"] == calls[0]["audit_selection"] for req in calls)
    assert calls[0]["audit_selection"]["policy"]["r5_publication_sha"] == canonical_audit_repo.source_sha
    assert result.decision["audit_profile_ref"] == approval["target_profiles"][flow]
    assert revalidate_decision(result.decision, calls[-1]) == result.decision
    assert "selection_authenticator" not in json.dumps(calls)


@pytest.mark.parametrize("fault", (
    "missing", "duplicate", "conflicting", "partial", "wrong_pin", "missing_body",
    "missing_authenticator", "unavailable", "stale_main", "foreign_repository",
    "review_binding", "wrong_review_source", "failed_review_acceptance", "missing_review",
    "unpublished", "artifact_binding", "explicit_v1", "explicit_v3",
    "stale_work", "return_binding", "revoked_approval", "unknown_approval_status", "active_conflict",
    "missing_candidate", "candidate_binding", "review_lineage", "flat_result", "shadowed_result",
    "malformed_result", "run_head", "run_status", "run_identity", "run_revision", "run_kind",
    "foreign_run_base", "missing_evidence", "evidence_run", "evidence_subject", "evidence_command",
    "failed_evidence", "duplicate_evidence", "claim_evidence", "unresolved_result",
))
def test_first_party_canonical_failures_abort_before_provider_without_fallback(
        registry, canonical_audit_repo, fault):
    import yaml
    from aios_renew.brain_audit import parse_profile_registry, profile_ref

    canonical = canonical_audit_repo
    approval = canonical.roadmap[canonical.approval_key]
    if fault == "missing":
        canonical.roadmap.clear()
        canonical.save_roadmap()
    elif fault == "duplicate":
        raw = yaml.safe_dump(canonical.roadmap).encode()
        canonical.save_roadmap(raw + raw)
    elif fault == "conflicting":
        canonical.roadmap["human_r21_four_specialized_activation_20261018"] = deepcopy(approval)
        canonical.save_roadmap()
    elif fault == "partial":
        del approval["target_profiles"]["REPAIR_AUTHORING"]  # Reject even for ARCHITECTURE.
        canonical.save_roadmap()
    elif fault in {"revoked_approval", "unknown_approval_status", "active_conflict"}:
        approval["status"] = {"revoked_approval": "REVOKED", "unknown_approval_status": "APPROVED",
                              "active_conflict": "ACTIVE"}[fault]
        if fault == "active_conflict":
            approval["human_decision"] = "REVOKE_R21_FOUR_SPECIALIZED_DEFAULT"
        canonical.save_roadmap()
    elif fault == "wrong_pin":
        approval["target_profiles"]["TASK_AUTHORING"]["version"] = True
        canonical.save_roadmap()
    elif fault == "missing_body":
        raw = yaml.safe_load((canonical.canonical / ".ai/brain-audit-profiles.yaml").read_bytes())
        raw["profiles"] = [p for p in raw["profiles"] if p["id"] != "tca-1"]
        canonical.write(".ai/brain-audit-profiles.yaml", yaml.safe_dump(raw).encode())
        canonical.main_sha = canonical.commit("missing pinned content")
    elif fault == "missing_authenticator":
        canonical.git(canonical.repo, "config", "--unset", "branch.main.remote")
    elif fault == "unavailable":
        canonical.git(canonical.repo, "config", "remote.origin.url", str(canonical.repo / "unavailable"))
    elif fault == "review_binding":
        approval["review_decision_sha"] = "f" * 40
        canonical.save_roadmap()
    elif fault in {"wrong_review_source", "failed_review_acceptance"}:
        if fault == "wrong_review_source":
            canonical.review["reviewed_sha"] = "f" * 40
        else:
            canonical.review["acceptance"]["AC1"] = "FAIL"
        canonical.save_review()
        approval["review_decision_sha"] = canonical.review_sha
        canonical.save_roadmap()
    elif fault == "missing_review":
        canonical.git(canonical.canonical, "update-ref", "-d", "refs/heads/aios/review-decision/RUN-368-001")
    elif fault == "missing_candidate":
        canonical.git(canonical.canonical, "update-ref", "-d", "refs/heads/aios/review/RUN-368-001")
    elif fault == "candidate_binding":
        canonical.git(canonical.canonical, "update-ref", "refs/heads/aios/review/RUN-368-001", canonical.base_sha)
    elif fault == "review_lineage":
        canonical.write(".ai/reviews/REVIEW-368-001.yaml", yaml.safe_dump(canonical.review).encode())
        sha = canonical.commit("copied review on unrelated metadata parent")
        canonical.git(canonical.canonical, "update-ref", "refs/heads/aios/review-decision/RUN-368-001", sha)
        approval["review_decision_sha"] = sha
        canonical.save_roadmap()
    elif fault == "unpublished":
        tree = canonical.git(canonical.canonical, "rev-parse", "HEAD^{tree}")
        sha = canonical.git(canonical.canonical, "commit-tree", tree, "-m", "main without published R5 ancestry")
        canonical.git(canonical.canonical, "update-ref", "refs/heads/main", sha)
        canonical.main_sha = sha
    elif fault in {"artifact_binding", "flat_result", "shadowed_result", "malformed_result",
                   "run_head", "run_status", "run_identity", "run_revision", "run_kind", "foreign_run_base",
                   "missing_evidence", "evidence_run", "evidence_subject", "evidence_command",
                   "failed_evidence", "duplicate_evidence", "claim_evidence", "unresolved_result"}:
        package = canonical.result_package
        if fault == "artifact_binding":
            package["result"]["head_sha"] = "f" * 40
        elif fault == "flat_result":
            canonical.result_package = deepcopy(package["result"])
        elif fault == "shadowed_result":
            package["head_sha"] = canonical.source_sha
            package["result"]["head_sha"] = "f" * 40
        elif fault == "malformed_result":
            del package["result"]["changed_files"]
        elif fault == "run_head":
            canonical.run["head_sha"] = "f" * 40
        elif fault == "run_status":
            canonical.run["status"] = "COMPLETED"
        elif fault == "run_identity":
            canonical.run["run_id"] = "RUN-368-999"
        elif fault == "run_revision":
            canonical.run["task"]["revision"] = True
        elif fault == "run_kind":
            canonical.run = {"kind": "REMEDIATION", "execution": {"run": deepcopy(canonical.run)}}
        elif fault == "foreign_run_base":
            tree = canonical.git(canonical.canonical, "rev-parse", "HEAD^{tree}")
            canonical.run["base_sha"] = canonical.git(canonical.canonical, "commit-tree", tree,
                                                       "-m", "unrelated admitted base")
        elif fault == "missing_evidence":
            package["evidence"].pop()
        elif fault == "evidence_run":
            package["evidence"][0]["run_id"] = "RUN-368-999"
        elif fault == "evidence_subject":
            package["evidence"][0]["subject_sha"] = "f" * 40
        elif fault == "evidence_command":
            package["evidence"][0]["source"]["command"] = "unrelated command"
        elif fault == "failed_evidence":
            package["evidence"][0]["result"]["exit_code"] = 1
        elif fault == "duplicate_evidence":
            package["evidence"].append(deepcopy(package["evidence"][0]))
        elif fault == "claim_evidence":
            package["result"]["claims"][0]["evidence"] = ["RUN-368-999-V001"]
        else:
            package["result"]["unresolved"] = ["R5 implementation remains incomplete"]
        canonical.save_artifacts()
    supplied, contract, bindings, sources = production_inputs(registry, "ARCHITECTURE", canonical)
    if fault == "stale_main":
        canonical.commit("advance canonical main while caller still has old packet")
    elif fault == "foreign_repository":
        body = supplied.as_dict()
        body["canonical_facts"]["repository"] = "unrelated/repository"
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        supplied = DecisionPacket(body)
    elif fault == "return_binding":
        from aios_renew.brain_return_contract import return_contract_ref
        contract["contract"]["candidate_contract"]["requirements"].append("Self-declared replacement guidance")
        contract["return_contract_ref"] = return_contract_ref(contract["contract"], contract["bounds"])
    fresh_packet = supplied
    if fault == "stale_work":
        body = supplied.as_dict()
        body["work_context_fingerprint"] = "f" * 64
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        fresh_packet = DecisionPacket(body)
    package = None
    if fault in {"explicit_v1", "explicit_v3"}:
        profiles = parse_profile_registry((canonical.repo / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
        identity = "aios-adaptive-brain-audit-v1" if fault == "explicit_v1" else "brain-high-value-v3"
        profile = next(p for p in profiles if p["id"] == identity)
        package = {"profile": profile, "audit_profile_ref": profile_ref(profile)}
    calls = []
    with pytest.raises(BrainAttemptError) as error:
        attempt(MappingBrainProvider("mapping", "one", lambda req: calls.append(req)), supplied, contract,
                bindings, package, lambda: fresh_packet, repo=canonical.repo, **sources)
    assert error.value.reason_code == "PROTOCOL_INPUT_INVALID"
    assert error.value.invocation_count == 0 and calls == []


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("fault", ("result_source", "evidence_source"))
def test_first_party_all_flows_reject_forged_r5_artifact_before_provider(
        registry, canonical_audit_repo, flow, fault):
    canonical = canonical_audit_repo
    if fault == "result_source":
        canonical.result_package["result"]["head_sha"] = canonical.base_sha
    else:
        canonical.result_package["evidence"][0]["subject_sha"] = canonical.base_sha
    canonical.save_artifacts()
    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical)
    calls = []
    with pytest.raises(BrainAttemptError) as error:
        attempt(MappingBrainProvider("mapping", "one", lambda req: calls.append(req)), supplied, contract,
                bindings, fresh_packet_supplier=lambda: supplied, repo=canonical.repo, **sources)
    assert error.value.reason_code == "PROTOCOL_INPUT_INVALID"
    assert error.value.invocation_count == 0 and calls == []


@pytest.mark.parametrize("flow", R5_FLOWS)
def test_first_party_continuation_stops_when_r5_artifacts_move_without_main_movement(
        registry, canonical_audit_repo, flow):
    canonical = canonical_audit_repo
    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical)
    main_sha = canonical.main_sha
    artifact_sha = canonical.artifact_sha
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        canonical.result_package["evidence"][0]["result"]["summary"] = "Replaced canonical artifact"
        canonical.save_artifacts()
        return wrapper(req, semantic=r5_response(req))
    if flow == "REMEDIATION_AUTHORING":
        with pytest.raises(BrainAttemptError) as retired:
            attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                            fresh_packet_supplier=lambda: supplied, repo=canonical.repo, **sources)
        assert retired.value.invocation_count == 0 and calls == []
        return
    with pytest.raises(BrainAttemptError) as error:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                fresh_packet_supplier=lambda: supplied, repo=canonical.repo, **sources)
    assert error.value.reason_code == "FRESH_PACKET_INVALID"
    assert error.value.invocation_count == 1 and len(calls) == 1
    assert canonical.artifact_sha != artifact_sha and canonical.main_sha == main_sha


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("after", (1, 2))
def test_first_party_continuation_stops_on_canonical_movement_and_keeps_original_lineage(
        registry, canonical_audit_repo, flow, after):
    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical_audit_repo)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        if len(calls) == after:
            canonical_audit_repo.commit("canonical main moved after admission")
        return wrapper(req, semantic=r5_response(req))
    provider = MappingBrainProvider("mapping", "one", callback)
    if flow == "REMEDIATION_AUTHORING":
        with pytest.raises(BrainAttemptError) as retired:
            attempt(provider, supplied, contract, bindings, fresh_packet_supplier=lambda: supplied,
                                   repo=canonical_audit_repo.repo, **sources)
        assert retired.value.invocation_count == 0 and calls == []
        return
    if flow == "REPAIR_AUTHORING" and after == 2:
        assert attempt(provider, supplied, contract, bindings, fresh_packet_supplier=lambda: supplied,
                       repo=canonical_audit_repo.repo, **sources)
    else:
        with pytest.raises(BrainAttemptError) as error:
            attempt(provider, supplied, contract, bindings, fresh_packet_supplier=lambda: supplied,
                    repo=canonical_audit_repo.repo, **sources)
        assert error.value.reason_code == "FRESH_PACKET_INVALID"
        assert error.value.invocation_count == after
    assert all(req["audit_selection"] == calls[0]["audit_selection"] for req in calls)


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("successors", (False, True, "task395"))
def test_first_party_individual_human_choice_is_bound_without_dual_audit(
        registry, canonical_audit_repo, v1_only_package, flow, successors, task395_activation_record):
    if successors == "task395":
        canonical_audit_repo.publish_successor(activation_record=task395_activation_record)
    elif successors:
        canonical_audit_repo.publish_successor()
    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical_audit_repo)
    if flow in {"ARCHITECTURE", "TASK_AUTHORING"}:
        context = sources["architecture_source_context" if flow == "ARCHITECTURE" else "task_source_context"]
        choice = human_v1_selection(supplied, contract, bindings, context, v1_only_package)
        choices = {"human_v1_option": choice}
    else:
        choice = {"selected_by": "HUMAN", "option": "V1_READ_ONLY", "packet_fingerprint": supplied.packet_fingerprint,
                  "work_context_fingerprint": supplied.as_dict()["work_context_fingerprint"],
                  "return_contract_ref": contract["return_contract_ref"], "external_bindings": bindings,
                  "source_context_fingerprint": digest({k: sources.get(k) for k in (
                      "moa_source_context", "task_source_context", "architecture_source_context")}),
                  "audit_profile_ref": v1_only_package["audit_profile_ref"]}
        choices = {"human_v1_deep_support": choice}
    # The trusted input channel has its own detached exact-subject Human choice.
    human_channel = deepcopy(choice)
    phases, calls = [], []
    def authenticate_human(selection, phase):
        phases.append(phase)
        return selection[next(iter(choices))] == human_channel
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=r5_response(req))
    if flow == "REMEDIATION_AUTHORING":
        with pytest.raises(BrainAttemptError) as retired:
            attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                                 fresh_packet_supplier=lambda: supplied, repo=canonical_audit_repo.repo,
                                 human_choice_authenticator=authenticate_human, **choices, **sources)
        assert retired.value.reason_code == "PROTOCOL_INPUT_INVALID"
        assert retired.value.invocation_count == 0 and calls == []
        return
    result = attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                     fresh_packet_supplier=lambda: supplied, repo=canonical_audit_repo.repo,
                     human_choice_authenticator=authenticate_human, **choices, **sources)
    assert phases[0] == "ADMIT" and all(phase == "CONTINUE" for phase in phases[1:])
    if "human_v1_option" in choices:
        assert [req["request_mode"] for req in calls] == ["V1_OPTION_CONSTRUCT", "V1_OPTION_RECONCILE"]
        assert result.decision["audit_profile_ref"] == v1_only_package["audit_profile_ref"]
    else:
        assert len(calls) == (1 if flow == "REPAIR_AUTHORING" else 2)
        assert not any(req["request_mode"].startswith(("AUDIT_", "V1_OPTION_")) for req in calls)
        assert "v1_audit" not in result.decision["semantic_value"]


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("fault", ("missing", "literal", "false", "truthy", "unavailable", "revoked"))
def test_first_party_human_labels_need_available_current_individual_authority(
        registry, canonical_audit_repo, v1_only_package, flow, fault):
    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical_audit_repo)
    if flow in {"ARCHITECTURE", "TASK_AUTHORING"}:
        context = sources["architecture_source_context" if flow == "ARCHITECTURE" else "task_source_context"]
        choices = {"human_v1_option": human_v1_selection(supplied, contract, bindings, context, v1_only_package)}
    else:
        choices = {"human_v1_deep_support": {
            "selected_by": "HUMAN", "option": "V1_READ_ONLY", "packet_fingerprint": supplied.packet_fingerprint,
            "work_context_fingerprint": supplied.as_dict()["work_context_fingerprint"],
            "return_contract_ref": contract["return_contract_ref"], "external_bindings": bindings,
            "source_context_fingerprint": digest({k: sources.get(k) for k in (
                "moa_source_context", "task_source_context", "architecture_source_context")}),
            "audit_profile_ref": v1_only_package["audit_profile_ref"]}}
    calls = []
    def authenticate(_selection, _phase):
        if fault == "unavailable":
            raise OSError("trusted Human source unavailable")
        return "AUTHENTICATED" if fault == "truthy" else fault == "revoked" and not calls
    authority = None if fault == "missing" else "HUMAN" if fault == "literal" else authenticate
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=r5_response(req))
    with pytest.raises(BrainAttemptError) as error:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                fresh_packet_supplier=lambda: supplied, repo=canonical_audit_repo.repo,
                human_choice_authenticator=authority, **choices, **sources)
    assert error.value.invocation_count == len(calls) == (1 if fault == "revoked" and flow != "REMEDIATION_AUTHORING" else 0)


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("active", (False, True))
@pytest.mark.parametrize("kind", ("mapping", "json"))
def test_r5_simulated_canonical_cutover_invokes_only_the_selected_normative_procedure(registry, flow, active, kind):
    supplied, contract, bindings, package, kwargs, authority = r5_case(registry, flow, active=active)
    calls, fresh_calls = [], []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, provider=kind, model=kind, semantic=r5_response(req))
    def fresh():
        fresh_calls.append(True)
        return DecisionPacket(supplied.as_dict())
    result = attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings, package, fresh, **kwargs)
    count = 1 if active and flow == "REPAIR_AUTHORING" else 2
    assert len(calls) == len(result.attributions) == count
    assert len(fresh_calls) == (1 if count == 1 else 2)
    assert all(r["audit_profile_package"] == package and r["audit_selection"] == calls[0]["audit_selection"] for r in calls)
    if active:
        assert not any(r["request_mode"].startswith(("AUDIT_", "V1_OPTION_")) for r in calls)
    else:
        assert [r["request_mode"] for r in calls] == ["AUDIT_CONSTRUCT", "AUDIT_RECONCILE"]
    assert authority.calls[0][0] == "ADMIT" and all(phase == "CONTINUE" for phase, _ in authority.calls[1:])
    assert revalidate_decision(result.decision, calls[-1]) == result.decision


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "TASK_AUTHORING"))
@pytest.mark.parametrize("fault", (None, "literal", "untrusted", "stale", "specialized_package"))
def test_r5_authenticated_human_v1_only_runs_one_substitute_and_never_specialized(registry, flow, fault):
    supplied, contract, bindings, package, kwargs, _ = r5_case(registry, flow, human=True)
    if fault == "literal":
        kwargs["human_v1_option"] = "HUMAN"
    elif fault == "untrusted":
        kwargs["selection_authenticator"] = lambda *_: False
    elif fault == "stale":
        kwargs["human_v1_option"]["source_context_fingerprint"] = "f" * 64
    elif fault == "specialized_package":
        _, _, _, package, _, _ = r5_case(registry, flow)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=r5_response(req))
    provider = MappingBrainProvider("mapping", "one", callback)
    if fault:
        with pytest.raises(BrainAttemptError) as error:
            attempt(provider, supplied, contract, bindings, package, lambda: supplied, **kwargs)
        assert error.value.invocation_count == 0 and not calls
        return
    result = attempt(provider, supplied, contract, bindings, package, lambda: supplied, **kwargs)
    assert [r["request_mode"] for r in calls] == ["V1_OPTION_CONSTRUCT", "V1_OPTION_RECONCILE"]
    assert len(result.attributions) == 2


@pytest.mark.parametrize("flow", ("REMEDIATION_AUTHORING", "REPAIR_AUTHORING"))
@pytest.mark.parametrize("deep", (False, True))
def test_r5_optional_correction_v1_support_is_read_only_without_an_extra_call_or_verdict(registry, flow, deep):
    supplied, contract, bindings, package, kwargs, _ = r5_case(registry, flow, deep=deep)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=r5_response(req))
    result = attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings, package, lambda: supplied, **kwargs)
    assert len(calls) == (1 if flow == "REPAIR_AUTHORING" else 2)
    assert all(r["audit_profile_package"] == package for r in calls)
    assert all((r["audit_selection"]["human_v1_deep_support"] is not None) == deep for r in calls)
    assert result.decision["audit_profile_ref"] == package["audit_profile_ref"]
    assert "v1_audit" not in result.decision["semantic_value"]


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("initially_active", (False, True))
@pytest.mark.parametrize("allow_original", (False, True))
@pytest.mark.parametrize("after", (1, 2))
def test_r5_policy_movement_finishes_only_an_authenticated_original_subject_or_stops(
        registry, flow, initially_active, allow_original, after):
    supplied, contract, bindings, package, kwargs, authority = r5_case(registry, flow, active=initially_active)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        if len(calls) == after:
            authority.current_policy = audit_policy_example(supplied.as_dict(), active=not initially_active)
            authority.allow_original = allow_original
        return wrapper(req, semantic=r5_response(req))
    provider = MappingBrainProvider("mapping", "one", callback)
    count = 1 if initially_active and flow == "REPAIR_AUTHORING" else 2
    if allow_original or after > count:
        result = attempt(provider, supplied, contract, bindings, package, lambda: supplied, **kwargs)
        assert len(calls) == count and result.decision["audit_profile_ref"] == package["audit_profile_ref"]
    else:
        with pytest.raises(BrainAttemptError) as error:
            attempt(provider, supplied, contract, bindings, package, lambda: supplied, **kwargs)
        assert error.value.invocation_count == len(calls) == after
        assert error.value.reason_code == "FRESH_PACKET_INVALID"
    assert all(r["audit_selection"] == calls[0]["audit_selection"] for r in calls)


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("fault", ("missing_policy", "missing_authority", "missing_profile", "conformance", "private", "default_v1", "wrong_flow"))
def test_r5_invalid_future_selection_stops_before_any_provider_call(registry, flow, fault):
    supplied, contract, bindings, package, kwargs, _ = r5_case(registry, flow)
    if fault == "missing_policy":
        kwargs["audit_policy"] = None
    elif fault == "missing_authority":
        kwargs["selection_authenticator"] = None
    elif fault == "missing_profile":
        package = None
    elif fault == "conformance":
        kwargs["conformance_only"] = True
    elif fault == "private":
        kwargs["audit_policy"]["raw_logs"] = "private"
    elif fault == "default_v1":
        _, _, _, package, _, _ = r5_case(registry, flow, active=False)
    else:
        other = R5_FLOWS[(R5_FLOWS.index(flow) + 1) % 4]
        _, _, _, package, _, _ = r5_case(registry, other)
    calls = []
    provider = MappingBrainProvider("mapping", "one", lambda req: calls.append(req))
    with pytest.raises(BrainAttemptError) as error:
        attempt(provider, supplied, contract, bindings, package, lambda: supplied, **kwargs)
    assert error.value.reason_code == "PROTOCOL_INPUT_INVALID" and error.value.invocation_count == 0 and not calls


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("after", (1, 2))
def test_r5_fresh_canonical_main_movement_cannot_recompose_under_a_new_default(registry, flow, after):
    supplied, contract, bindings, package, kwargs, _ = r5_case(registry, flow)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=r5_response(req))
    def fresh():
        if len(calls) < after:
            return supplied
        body = supplied.as_dict()
        body["canonical_facts"]["main_sha"] = "f" * 40
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        return DecisionPacket(body)
    provider = MappingBrainProvider("mapping", "one", callback)
    if flow == "REPAIR_AUTHORING" and after == 2:
        assert attempt(provider, supplied, contract, bindings, package, fresh, **kwargs)
    else:
        with pytest.raises(BrainAttemptError) as error:
            attempt(provider, supplied, contract, bindings, package, fresh, **kwargs)
        assert error.value.invocation_count == len(calls) == after


@pytest.mark.parametrize("identity", ("brain-high-value-v2", "brain-high-value-v3"))
def test_r5_historical_profile_presence_cannot_select_a_new_ordinary_attempt(registry, identity):
    from pathlib import Path
    from aios_renew.brain_audit import parse_profile_registry, profile_ref
    profiles = parse_profile_registry((Path(__file__).resolve().parents[1] / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
    profile = next(p for p in profiles if p["id"] == identity)
    supplied, contract = inputs(registry, "ARCHITECTURE")
    calls = []
    with pytest.raises(BrainAttemptError) as error:
        attempt(MappingBrainProvider("mapping", "one", lambda req: calls.append(req)), supplied, contract, {},
                {"profile": profile, "audit_profile_ref": profile_ref(profile)}, lambda: supplied)
    assert error.value.invocation_count == 0 and not calls


@pytest.mark.parametrize("kind", ("mapping", "json"))
@pytest.mark.parametrize("human_option", (False, True))
@pytest.mark.parametrize("unknown", (False, True))
def test_architecture_one_selected_profile_two_calls_and_fresh_final_closure(
        registry, aoa_package, v1_only_package, kind, human_option, unknown):
    supplied, contract, bindings, context = aoa_inputs(registry)
    if unknown and not human_option:
        context["objective"] = None
    package = v1_only_package if human_option else aoa_package
    option = human_v1_selection(supplied, contract, bindings, context, package) if human_option else None
    calls, freshness = [], []
    def callback(req):
        calls.append(deepcopy(req))
        semantic = ((response(req, aoa_candidate()) if req["request_mode"] == "V1_OPTION_CONSTRUCT" else
                     stage2_response(req, aoa_candidate(), blocker=unknown)) if human_option else aoa_response(req))
        return wrapper(req, provider=kind, model=kind, semantic=semantic)
    def fresh():
        freshness.append(True)
        return DecisionPacket(supplied.as_dict())
    result = attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings, package, fresh,
                     conformance_only=True, architecture_source_context=context, human_v1_option=option)
    prefix = "V1_OPTION" if human_option else "AOA"
    assert [r["request_mode"] for r in calls] == [prefix + "_CONSTRUCT", prefix + "_RECONCILE"]
    assert len(freshness) == 2 and len(result.attributions) == 2
    assert all(r["audit_profile_package"] == package for r in calls)
    assert all(("aoa_subject" not in r) == human_option for r in calls)
    semantic = result.decision["semantic_value"]
    assert (semantic["outcome"] if human_option else semantic["closure"]["outcome"]) == ("NO_DECISION" if unknown else "CANDIDATE")


def test_aoa_mapping_and_json_transports_produce_the_same_portable_decision(registry, aoa_package):
    supplied, contract, bindings, context = aoa_inputs(registry)
    decisions = []
    for kind in ("mapping", "json"):
        def callback(req):
            return wrapper(req, provider=kind, model=kind, semantic=aoa_response(req))
        decisions.append(attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings,
            aoa_package, lambda: supplied, conformance_only=True, architecture_source_context=context).decision)
    assert decisions[0] == decisions[1]


@pytest.mark.parametrize("human_option", (False, True))
@pytest.mark.parametrize("after", (1, 2))
@pytest.mark.parametrize("fault", ("main", "intent", "work_context", "unavailable"))
def test_architecture_source_movement_before_stage_two_or_handoff_aborts_without_fallback(
        registry, aoa_package, v1_only_package, human_option, after, fault):
    supplied, contract, bindings, context = aoa_inputs(registry)
    package = v1_only_package if human_option else aoa_package
    option = human_v1_selection(supplied, contract, bindings, context, package) if human_option else None
    calls = []
    def callback(req):
        calls.append(req)
        semantic = ((response(req, aoa_candidate()) if req["request_mode"] == "V1_OPTION_CONSTRUCT" else
                     stage2_response(req, aoa_candidate())) if human_option else aoa_response(req))
        return wrapper(req, semantic=semantic)
    def fresh():
        if len(calls) < after:
            return supplied
        if fault == "unavailable":
            raise ValueError("source unavailable")
        body = supplied.as_dict()
        if fault == "main":
            body["canonical_facts"]["main_sha"] = "f" * 40
        elif fault == "intent":
            body["human_input"] += " Changed objective."
        else:
            body["work_context_fingerprint"] = "f" * 64
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        return DecisionPacket(body)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings, package, fresh,
                conformance_only=True, architecture_source_context=context, human_v1_option=option)
    assert len(calls) == after and caught.value.invocation_count == after
    assert caught.value.reason_code in {"FRESH_PACKET_INVALID", "FRESH_PACKET_UNAVAILABLE", "STALE_BEFORE_STAGE2",
                                       "STALE_BEFORE_AOA_RETURN", "STALE_BEFORE_V1_OPTION_RETURN"}


@pytest.mark.parametrize("fault", ("ordinary_aoa", "missing_freshness", "implicit_v1", "literal_human", "mixed_sources", "v3_fallback"))
def test_architecture_policy_and_selection_errors_abort_before_provider_invocation(registry, aoa_package, v1_only_package, fault):
    supplied, contract, bindings, context = aoa_inputs(registry)
    package = v1_only_package if fault in ("implicit_v1", "literal_human", "v3_fallback") else aoa_package
    if fault == "v3_fallback":
        package = deepcopy(v1_only_package)
        package["audit_profile_ref"]["digest"] = "f" * 64
    called = []
    def callback(req):
        called.append(req)
        return wrapper(req)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings, package,
                None if fault == "missing_freshness" else lambda: supplied,
                conformance_only=fault != "ordinary_aoa", architecture_source_context=context,
                task_source_context={} if fault == "mixed_sources" else None,
                human_v1_option="HUMAN" if fault == "literal_human" else None)
    assert not called and caught.value.invocation_count == 0


@pytest.mark.parametrize("kind", ("mapping", "json"))
@pytest.mark.parametrize("stage", (1, 2))
@pytest.mark.parametrize("fault", ("cost", "private", "wrong_subject", "extra_audit"))
def test_aoa_invalid_native_semantic_output_stops_at_its_pass(registry, aoa_package, kind, stage, fault):
    supplied, contract, bindings, context = aoa_inputs(registry)
    calls = []
    def callback(req):
        calls.append(req)
        semantic = aoa_response(req)
        if len(calls) == stage:
            if fault == "cost":
                semantic["alternatives"][1]["episode_cost"]["human_minutes"]["quantity"] = 0
            elif fault == "private":
                semantic["alternatives"][0]["basis"] = {"secret": "x"}
            elif fault == "wrong_subject":
                semantic["subject_fingerprint"] = "f" * 64
            else:
                semantic["v1_audit"] = {}
        return wrapper(req, provider=kind, model=kind, semantic=semantic)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings, aoa_package,
                lambda: supplied, conformance_only=True, architecture_source_context=context)
    assert len(calls) == stage and caught.value.invocation_count == stage
    assert caught.value.reason_code == "PROVIDER_RESPONSE_INVALID"


def test_aoa_duplicate_native_json_closure_is_rejected(registry, aoa_package):
    supplied, contract, bindings, context = aoa_inputs(registry)
    calls = []
    def callback(raw):
        req = json.loads(raw)
        calls.append(req)
        encoded = json.dumps(wrapper(req, provider="json", semantic=aoa_response(req)))
        return encoded.replace('"closure":', '"closure": {}, "closure":') if len(calls) == 2 else encoded
    with pytest.raises(BrainAttemptError) as caught:
        attempt(JsonBrainProvider("json", "one", callback), supplied, contract, bindings, aoa_package,
                lambda: supplied, conformance_only=True, architecture_source_context=context)
    assert caught.value.reason_code == "PROVIDER_RESPONSE_INVALID" and len(calls) == 2


@pytest.mark.parametrize("kind", ("mapping", "json"))
@pytest.mark.parametrize("human_option", (False, True))
@pytest.mark.parametrize("unknown", (False, True))
def test_task_audit_selects_exactly_one_two_pass_profile_with_fresh_final_closure(
        registry, tca_package, v1_only_package, kind, human_option, unknown):
    supplied, contract, bindings, context = tca_inputs(registry)
    if unknown and human_option:
        # The existing V1 closure expresses its own blocker; no TCA pass is run.
        context["task"]["assumptions"].append("A material consumer effect remains unknown")
    elif unknown:
        context["authorization"] = None
    package = v1_only_package if human_option else tca_package
    option = human_v1_selection(supplied, contract, bindings, context, package) if human_option else None
    calls, fresh_calls = [], []
    def callback(req):
        calls.append(deepcopy(req))
        semantic = ((response(req, context["task"]) if req["request_mode"] == "V1_OPTION_CONSTRUCT" else
                     stage2_response(req, context["task"], blocker=unknown)) if human_option else tca_response(req))
        return wrapper(req, provider=kind, model=kind, semantic=semantic)
    def fresh():
        fresh_calls.append(True)
        return DecisionPacket(supplied.as_dict())
    result = attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings, package, fresh,
                     conformance_only=True, task_source_context=context, human_v1_option=option)
    prefix = "V1_OPTION" if human_option else "TCA"
    assert [r["request_mode"] for r in calls] == [prefix + "_CONSTRUCT", prefix + "_RECONCILE"]
    assert len(fresh_calls) == 2 and len(result.attributions) == 2
    assert all(r["audit_profile_package"] == package for r in calls)
    assert all(("tca_subject" not in r) == human_option for r in calls)
    semantic = result.decision["semantic_value"]
    outcome = semantic["outcome"] if human_option else semantic["closure"]["outcome"]
    assert outcome == ("NO_DECISION" if unknown else "CANDIDATE")


def test_tca_native_transports_produce_the_same_portable_decision(registry, tca_package):
    supplied, contract, bindings, context = tca_inputs(registry)
    decisions = []
    for kind in ("mapping", "json"):
        def callback(req):
            return wrapper(req, provider=kind, model=kind, semantic=tca_response(req))
        decisions.append(attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings,
            tca_package, lambda: DecisionPacket(supplied.as_dict()), conformance_only=True,
            task_source_context=context).decision)
    assert decisions[0] == decisions[1]


@pytest.mark.parametrize("fault", ("ordinary_tca", "implicit_v1_option", "ordinary_option", "executor_option", "mixed_option", "v3_fallback"))
def test_task_audit_policy_or_option_mismatch_aborts_before_provider_invocation(registry, tca_package, v1_only_package, fault):
    supplied, contract, bindings, context = tca_inputs(registry)
    calls = []
    def callback(req):
        calls.append(req)
        raise AssertionError("Invalid selection must not reach a provider")
    conformance = fault not in ("ordinary_tca", "ordinary_option")
    package = tca_package if fault in ("ordinary_tca", "mixed_option") else v1_only_package
    option = None if fault in ("ordinary_tca", "implicit_v1_option") else human_v1_selection(
        supplied, contract, bindings, context, v1_only_package)
    if fault == "executor_option":
        option["selected_by"] = "EXECUTOR"
    elif fault == "v3_fallback":
        from aios_renew.brain_audit import parse_profile_registry, profile_ref
        from test_brain_provider_protocol import ROOT
        profile = next(p for p in parse_profile_registry((ROOT / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"]
                       if p["id"] == "brain-high-value-v3")
        package = {"profile": profile, "audit_profile_ref": profile_ref(profile)}
    with pytest.raises(BrainAttemptError) as caught:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings, package,
                lambda: supplied, conformance_only=conformance, task_source_context=context, human_v1_option=option)
    assert caught.value.reason_code == "PROTOCOL_INPUT_INVALID" and caught.value.invocation_count == 0 and not calls


@pytest.mark.parametrize("after", (1, 2))
@pytest.mark.parametrize("human_option", (False, True))
@pytest.mark.parametrize("fault", ("main", "intent", "source_currentness", "unavailable", "malformed"))
def test_task_audit_currentness_movement_aborts_without_retry_or_fallback(
        registry, tca_package, v1_only_package, after, human_option, fault):
    supplied, contract, bindings, context = tca_inputs(registry)
    package = v1_only_package if human_option else tca_package
    option = human_v1_selection(supplied, contract, bindings, context, package) if human_option else None
    calls, fresh_calls = [], []
    def callback(req):
        calls.append(req)
        semantic = ((response(req, context["task"]) if req["request_mode"] == "V1_OPTION_CONSTRUCT" else
                     stage2_response(req, context["task"])) if human_option else tca_response(req))
        return wrapper(req, semantic=semantic)
    def fresh():
        fresh_calls.append(True)
        if len(fresh_calls) != after:
            return supplied
        if fault == "unavailable":
            raise ValueError("No current canonical packet")
        if fault == "malformed":
            return {}
        body = supplied.as_dict()
        if fault == "main":
            body["canonical_facts"]["main_sha"] = "f" * 40
        elif fault == "intent":
            body["human_input"] = "New intent"
        else:
            body["work_context_fingerprint"] = "f" * 64
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        return DecisionPacket(body)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings, package, fresh,
                conformance_only=True, task_source_context=context, human_v1_option=option)
    assert len(calls) == after and caught.value.invocation_count == after
    assert all(r["audit_profile_package"] == package for r in calls)


@pytest.mark.parametrize("kind", ("mapping", "json"))
@pytest.mark.parametrize("stage", (1, 2))
@pytest.mark.parametrize("fault", ("double_audit", "duplicate_output", "forged_source", "missing_ac"))
def test_tca_invalid_native_output_stops_at_its_pass(registry, tca_package, kind, stage, fault):
    supplied, contract, bindings, context = tca_inputs(registry)
    calls = []
    def callback(req):
        calls.append(req)
        semantic = tca_response(req)
        if len(calls) == stage:
            if fault == "double_audit":
                semantic["v1_audit"] = {}
            elif fault == "duplicate_output":
                semantic = [semantic, semantic]
            elif fault == "forged_source":
                semantic["acceptance_checks"][0]["evidence_refs"][0]["digest"] = "f" * 64
            else:
                semantic["acceptance_checks"].pop()
        return wrapper(req, provider=kind, model=kind, semantic=semantic)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings, tca_package,
                lambda: supplied, conformance_only=True, task_source_context=context)
    assert len(calls) == stage and caught.value.invocation_count == stage


def test_tca_duplicate_native_json_closure_fails_before_semantic_return(registry, tca_package):
    supplied, contract, bindings, context = tca_inputs(registry)
    calls = []
    def callback(raw):
        req = json.loads(raw)
        calls.append(req)
        encoded = json.dumps(wrapper(req, provider="json", semantic=tca_response(req)))
        return encoded.replace('"closure":', '"closure": {}, "closure":') if len(calls) == 2 else encoded
    with pytest.raises(BrainAttemptError) as caught:
        attempt(JsonBrainProvider("json", "one", callback), supplied, contract, bindings, tca_package,
                lambda: supplied, conformance_only=True, task_source_context=context)
    assert caught.value.reason_code == "PROVIDER_RESPONSE_INVALID" and len(calls) == 2


@pytest.mark.parametrize("kind", ("mapping", "json"))
@pytest.mark.parametrize("frontier", (False, True))
def test_moa_exactly_two_focused_calls_and_fresh_final_closure(registry, moa_package, kind, frontier):
    supplied, contract, bindings, sources = moa_inputs(registry, frontier=frontier)
    calls, freshness = [], []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, provider=kind, model=kind, semantic=moa_response(req))
    def fresh():
        freshness.append(None)
        return DecisionPacket(supplied.as_dict())
    result = attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings,
                     moa_package, fresh, conformance_only=True, moa_source_context=sources)
    assert [r["request_mode"] for r in calls] == ["MOA_CONSTRUCT", "MOA_RECONCILE"]
    assert len(freshness) == len(result.attributions) == 2
    assert revalidate_decision(result.decision, calls[1]) == result.decision
    assert result.decision["semantic_value"]["closure"]["outcome"] == ("NO_DECISION" if frontier else "CANDIDATE")
    assert all(req["audit_profile_package"] == moa_package for req in calls)


def test_moa_native_transports_produce_the_same_portable_semantic_decision(registry, moa_package):
    supplied, contract, bindings, sources = moa_inputs(registry)
    decisions = []
    for kind in ("mapping", "json"):
        def callback(req):
            return wrapper(req, provider=kind, semantic=moa_response(req))
        decisions.append(attempt(adapter(kind, native(kind, callback), kind), supplied, contract, bindings,
            moa_package, lambda: DecisionPacket(supplied.as_dict()), conformance_only=True,
            moa_source_context=sources).decision)
    assert decisions[0] == decisions[1]


@pytest.mark.parametrize("flag,supplier", ((False, True), ("true", True), (True, False)))
def test_moa_registry_presence_cannot_activate_an_ordinary_attempt(registry, moa_package, flag, supplier):
    supplied, contract, bindings, sources = moa_inputs(registry)
    calls = []
    with pytest.raises(BrainAttemptError) as caught:
        attempt(MappingBrainProvider("mapping", "one", lambda req: calls.append(req)), supplied, contract, bindings,
                moa_package, (lambda: supplied) if supplier else None, conformance_only=flag, moa_source_context=sources)
    assert caught.value.reason_code == "PROTOCOL_INPUT_INVALID"
    assert caught.value.invocation_count == 0 and not calls


@pytest.mark.parametrize("after", (1, 2))
@pytest.mark.parametrize("fault", ("main", "review_ref", "authority", "predecessor", "verification", "invalid", "unavailable"))
def test_moa_currentness_movement_aborts_without_retry_or_another_audit(registry, moa_package, after, fault):
    supplied, contract, bindings, sources = moa_inputs(registry)
    calls, freshness = [], []
    def callback(req):
        calls.append(req)
        return wrapper(req, semantic=moa_response(req))
    def fresh():
        freshness.append(None)
        if len(freshness) < after:
            return DecisionPacket(supplied.as_dict())
        if fault == "unavailable":
            raise ValueError("Canonical source moved")
        if fault == "invalid":
            return DecisionPacket({})
        body = supplied.as_dict()
        facts = body["canonical_facts"]
        if fault == "main":
            facts["main_sha"] = "f" * 40
        elif fault == "review_ref":
            facts["correction_subject"]["review_decision_sha"] = "f" * 40
        elif fault == "authority":
            body["canonical_blocker"] = {"reason": "Authority moved"}
        elif fault == "predecessor":
            body["prior_semantic_decisions"] = {"kind": "REPAIR", "authorization_sha": "f" * 40}
        else:
            facts["task_contract"]["verification"]["required"].append("pytest tests/another.py")
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        return DecisionPacket(body)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings, moa_package,
                fresh, conformance_only=True, moa_source_context=sources)
    expected = ("FRESH_PACKET_UNAVAILABLE" if fault == "unavailable" else "FRESH_PACKET_INVALID" if fault == "invalid" else
                "STALE_BEFORE_STAGE2" if after == 1 else "STALE_BEFORE_MOA_RETURN")
    # A malformed DecisionPacket at the between-pass boundary fails before access
    # can be interpreted as a successful or unknown semantic decision.
    assert caught.value.reason_code == expected
    assert caught.value.invocation_count == len(calls) == after


@pytest.mark.parametrize("kind", ("mapping", "json"))
@pytest.mark.parametrize("stage", ("MOA_CONSTRUCT", "MOA_RECONCILE"))
@pytest.mark.parametrize("fault", ("double_audit", "duplicate_output", "missing_evidence", "profile_substitution"))
def test_moa_bad_provider_output_stops_at_its_pass_without_fallback(registry, moa_package, kind, stage, fault):
    supplied, contract, bindings, sources = moa_inputs(registry)
    calls = []
    def callback(req):
        calls.append(req)
        semantic = moa_response(req)
        if req["request_mode"] == stage:
            if fault == "double_audit":
                semantic["v1_audit"] = {}
            elif fault == "duplicate_output":
                semantic = [semantic, deepcopy(semantic)]
            elif fault == "missing_evidence":
                semantic["checks" if stage == "MOA_CONSTRUCT" else "challenges"][0]["evidence_refs"] = []
            else:
                semantic["subject_fingerprint"] = "f" * 64
        return wrapper(req, provider=kind, semantic=semantic)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(adapter(kind, native(kind, callback), kind), supplied, contract, bindings, moa_package,
                lambda: supplied, conformance_only=True, moa_source_context=sources)
    assert caught.value.reason_code == "PROVIDER_RESPONSE_INVALID"
    assert caught.value.invocation_count == len(calls) == (1 if stage == "MOA_CONSTRUCT" else 2)
    assert caught.value.phase == stage


def test_moa_duplicate_native_json_closure_is_rejected(registry, moa_package):
    supplied, contract, bindings, sources = moa_inputs(registry)
    calls = []
    def callback(raw):
        req = json.loads(raw)
        calls.append(req)
        encoded = json.dumps(wrapper(req, provider="json", semantic=moa_response(req)))
        return encoded.replace('"closure": {', '"closure": null, "closure": {', 1)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(JsonBrainProvider("json", "one", callback), supplied, contract, bindings, moa_package,
                lambda: supplied, conformance_only=True, moa_source_context=sources)
    assert caught.value.reason_code == "PROVIDER_RESPONSE_INVALID" and len(calls) == 2


@pytest.mark.parametrize("unknown", (None, 1))
def test_roa_one_call_fresh_closure_is_portable_across_native_transports(registry, roa_package, unknown):
    supplied, contract, bindings = roa_inputs(registry)
    decisions = []
    for kind in ("mapping", "json"):
        calls, freshness = [], []
        def callback(req):
            calls.append(deepcopy(req))
            return wrapper(req, provider=kind, model=kind, semantic=roa_response(req, unknown=unknown))
        def fresh():
            freshness.append(None)
            return DecisionPacket(supplied.as_dict())
        result = attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract,
                         bindings, roa_package, fresh, conformance_only=True)
        assert len(calls) == len(freshness) == len(result.attributions) == 1
        assert calls[0]["request_mode"] == "ROA_CONFORMANCE" and calls[0]["stage1_lineage"] is None
        assert revalidate_decision(result.decision, calls[0]) == result.decision
        assert (result.decision["semantic_value"]["handoff_candidate"] is None) == (unknown is not None)
        decisions.append(result.decision)
    assert decisions[0] == decisions[1]


@pytest.mark.parametrize("flag,supplier", ((False, True), ("true", True), (True, False)))
def test_roa_never_activates_in_an_ordinary_production_attempt(registry, roa_package, flag, supplier):
    supplied, contract, bindings = roa_inputs(registry)
    calls = []
    provider = MappingBrainProvider("fixture", "fixed", lambda req: calls.append(req))
    with pytest.raises(BrainAttemptError) as caught:
        attempt(provider, supplied, contract, bindings, roa_package,
                (lambda: supplied) if supplier else None, conformance_only=flag)
    assert caught.value.reason_code == "PROTOCOL_INPUT_INVALID"
    assert caught.value.invocation_count == 0 and not calls


@pytest.mark.parametrize("fault", ("main", "failure", "evidence", "authority", "invalid_packet", "unavailable"))
def test_roa_invalidates_after_one_call_when_currentness_moves(registry, roa_package, fault):
    supplied, contract, bindings = roa_inputs(registry)
    calls = []
    before = supplied.as_dict()
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=roa_response(req))
    def fresh():
        if fault == "unavailable":
            raise RuntimeError("Source unavailable")
        body = supplied.as_dict()
        if fault == "main":
            body["canonical_facts"]["main_sha"] = "f" * 40
        elif fault == "failure":
            body["canonical_facts"]["correction_subject"]["artifacts_sha"] = "f" * 40
        elif fault == "evidence":
            body["bounded_observations"]["strategy_facts"]["structural_result_package"] = "PRESENT"
        elif fault == "authority":
            body["canonical_blocker"] = {"reason": "Authority unresolved"}
        else:
            return DecisionPacket({})
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        return DecisionPacket(body)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract,
                bindings, roa_package, fresh, conformance_only=True)
    expected = ("FRESH_PACKET_UNAVAILABLE" if fault == "unavailable" else
                "FRESH_PACKET_INVALID" if fault == "invalid_packet" else "STALE_BEFORE_ROA_RETURN")
    assert caught.value.reason_code == expected and caught.value.invocation_count == len(calls) == 1
    assert supplied.as_dict() == before


@pytest.mark.parametrize("fault", ("forged_evidence", "missing_check", "dual_audit", "duplicate_output"))
def test_roa_invalid_output_has_no_retry_or_v1_fallback(registry, roa_package, fault):
    supplied, contract, bindings = roa_inputs(registry)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        semantic = roa_response(req)
        if fault == "forged_evidence":
            semantic["checks"][1]["evidence_refs"][0]["digest"] = "f" * 64
        elif fault == "missing_check":
            semantic["checks"].pop()
        elif fault == "dual_audit":
            semantic["v1_audit"] = {}
        else:
            semantic = [semantic, deepcopy(semantic)]
        return wrapper(req, semantic=semantic)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract,
                bindings, roa_package, lambda: supplied, conformance_only=True)
    assert caught.value.reason_code == "PROVIDER_RESPONSE_INVALID"
    assert caught.value.invocation_count == len(calls) == 1
    assert calls[0]["audit_profile_package"] == roa_package


def test_roa_duplicate_native_json_closure_is_rejected_before_semantic_validation(registry, roa_package):
    supplied, contract, bindings = roa_inputs(registry)
    calls = []
    def callback(raw):
        req = json.loads(raw)
        calls.append(req)
        encoded = json.dumps(wrapper(req, provider="json", semantic=roa_response(req)))
        return encoded.replace('"closure": {', '"closure": null, "closure": {', 1)
    with pytest.raises(BrainAttemptError) as caught:
        attempt(JsonBrainProvider("json", "one", callback), supplied, contract, bindings,
                roa_package, lambda: supplied, conformance_only=True)
    assert caught.value.reason_code == "PROVIDER_RESPONSE_INVALID" and len(calls) == 1


def wrapper(request, *, provider="mapping", model="one", semantic=None, attribution=None):
    if semantic is None:
        semantic = (response(request, DIRECT) if request["request_mode"] == "DIRECT" else
                    response(request, TASK) if request["request_mode"] == "AUDIT_CONSTRUCT" else
                    stage2_response(request, TASK))
    return {"semantic_response": semantic,
            "attribution": attribution if attribution is not None else {
                "provider": provider, "model": model, "session_id": None, "invocation_id": "call"}}


@pytest.mark.parametrize("kind", ["mapping", "json"])
def test_provider_receives_safe_bound_failure_context_without_native_capture(registry, kind):
    from aios_renew.review_transport import failure_context
    from test_brain_provider_protocol import digest
    from tests.test_review_transport import _native_failure_fixture
    packet, package = inputs(registry, "DIAGNOSTIC")
    _, private = _native_failure_fixture()
    context = failure_context(private, artifact_sha="c" * 40)
    body = packet.as_dict()
    body["canonical_facts"]["failure_context"] = context
    body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
    calls = []
    def callback(request):
        calls.append(deepcopy(request))
        return wrapper(request, provider=kind, model="model")
    result = attempt(adapter(kind, native(kind, callback), kind, "model"), DecisionPacket(body), package, {})
    received = calls[0]["decision_packet"]["canonical_facts"]["failure_context"]
    assert received == context and received["source"]["artifact_sha"] == "c" * 40
    assert "synthetic-secret" not in json.dumps(calls)
    assert result.decision["semantic_value"] == DIRECT


def setup(registry, flow="TASK_AUTHORING"):
    packet, package = inputs(registry, flow)
    bindings = {} if flow == "DIAGNOSTIC" else deepcopy(TASK)
    return packet, package, bindings


def adapter(kind, callback, provider="mapping", model="one"):
    if kind == "mapping":
        return MappingBrainProvider(provider, model, callback)
    return JsonBrainProvider(provider, model, callback)


def native(kind, callback):
    if kind == "mapping":
        return callback

    def text_callable(raw):
        request = json.loads(raw.decode("utf-8"))
        return json.dumps(callback(request), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    return text_callable


@pytest.mark.parametrize("kind", ["mapping", "json"])
def test_direct_one_call_and_attribution_independence(registry, kind):
    packet, package, bindings = setup(registry, "DIAGNOSTIC")
    calls = []
    def callback(request):
        calls.append(deepcopy(request))
        return wrapper(request, provider=kind, model="model", attribution={
            "provider": kind, "model": "model", "session_id": "s", "invocation_id": str(len(calls))})
    result = attempt(adapter(kind, native(kind, callback), kind, "model"), packet,
                     package, bindings, fresh_packet_supplier=lambda: pytest.fail("DIRECT freshness"))
    assert len(calls) == 1
    assert calls[0]["request_mode"] == "DIRECT"
    assert result.decision["semantic_value"] == DIRECT
    assert len(result.attributions) == 1
    assert set(result.attributions[0]) == {"provider", "model", "session_id", "invocation_id"}
    with pytest.raises(TypeError):
        result.attributions[0]["model"] = "changed"


@pytest.mark.parametrize("kind", ["mapping", "json"])
def test_isolated_v1_two_calls_fresh_supplier_and_fresh_stage2(registry, profile_package, kind):
    packet, package, bindings = setup(registry)
    calls, fresh_calls = [], []
    def callback(request):
        calls.append(deepcopy(request))
        return wrapper(request, provider=kind, model="model", attribution={
            "provider": kind, "model": "model", "session_id": "session-" + str(len(calls)),
            "invocation_id": str(len(calls))})
    def fresh():
        fresh_calls.append(None)
        return DecisionPacket(packet.as_dict())
    result = attempt(adapter(kind, native(kind, callback), kind, "model"), packet, package,
                     bindings, profile_package, fresh, conformance_only=True)
    assert [item["request_mode"] for item in calls] == ["AUDIT_CONSTRUCT", "AUDIT_RECONCILE"]
    assert len(fresh_calls) == 1 and len(result.attributions) == 2
    assert calls[1]["stage1_lineage"]["stage1_decision_fingerprint"]
    assert result.decision["semantic_value"]["outcome"] == "CANDIDATE"
    assert all("provider" not in item for item in calls)


def test_independent_adapter_semantic_identity(registry, profile_package):
    packet, package, bindings = setup(registry)
    observed = []
    for kind in ("mapping", "json"):
        calls = []
        def callback(request):
            calls.append(deepcopy(request))
            return wrapper(request, provider=kind, model="model-" + kind,
                           attribution={"provider": kind, "model": "model-" + kind,
                                        "session_id": kind, "invocation_id": str(len(calls))})
        result = attempt(adapter(kind, native(kind, callback), kind, "model-" + kind),
                         packet, package, bindings, profile_package, lambda: DecisionPacket(packet.as_dict()), conformance_only=True)
        observed.append(([item["request_fingerprint"] for item in calls],
                         result.decision["decision_fingerprint"], result.attributions))
    assert observed[0][:2] == observed[1][:2]
    assert observed[0][2] != observed[1][2]


def test_stage2_is_self_sufficient_on_fresh_adapter(registry, profile_package):
    packet, package, bindings = setup(registry)
    first_request = construct_request(packet, package, bindings, profile_package,
                                      request_mode="AUDIT_CONSTRUCT")
    first_decision = validate_response(first_request, response(first_request, TASK))
    second_request = construct_request(DecisionPacket(packet.as_dict()), package, bindings,
                                       profile_package, request_mode="AUDIT_RECONCILE",
                                       stage1_decision=first_decision)
    received = []
    def fresh_callable(raw):
        received.append(json.loads(raw.decode("utf-8")))
        return json.dumps(wrapper(received[0], provider="fresh", model="new"),
                          ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fresh = JsonBrainProvider("fresh", "new", fresh_callable)
    invocation = fresh.invoke(second_request)
    assert len(received) == 1 and received[0] == second_request
    assert validate_response(second_request, invocation.semantic_response)["semantic_value"]["outcome"] == "CANDIDATE"
    assert "semantic_response" not in received[0] and "attribution" not in received[0]


@pytest.mark.parametrize("kind", ("mapping", "json"))
@pytest.mark.parametrize("fault", (None, "missing", "invalid_phase", "cross_flow",
                                   "missing_section", "stale_section", "unknown_section",
                                   "blocked_without_closure"))
def test_provider_attempt_phase_ledger_is_bound_and_fail_closed(registry, profile_package, kind, fault):
    flow = "ARCHITECTURE" if fault == "cross_flow" else "TASK_AUTHORING"
    packet, package = inputs(registry, flow)
    bindings = {} if flow == "ARCHITECTURE" else deepcopy(TASK)
    candidate = {**bindings, "acceptance": [{"id": "AC1", "condition": "Implementation exists"}]}
    calls, freshness = [], []
    def callback(request):
        calls.append(deepcopy(request))
        if request["request_mode"] == "AUDIT_CONSTRUCT":
            semantic = response(request, candidate)
        else:
            guidance = " ".join(request["return_contract_package"]["contract"]["candidate_contract"]["requirements"])
            if flow == "TASK_AUTHORING":
                assert "before Runtime verification" in guidance and "PROOF_LATER" in guidance
            semantic = stage2_response(request, candidate)
            if fault == "missing":
                del semantic["acceptance_phase_ledger"]
            elif fault == "invalid_phase":
                semantic["acceptance_phase_ledger"][0]["phase"] = "UNKNOWN"
            elif fault == "cross_flow":
                semantic["acceptance_phase_ledger"] = []
            elif fault == "missing_section":
                del semantic["cross_authority_context"]
            elif fault == "stale_section":
                semantic["canonical_shape"]["packet_fingerprint"] = "0" * 64
            elif fault == "unknown_section":
                semantic["future_authority"] = {}
            elif fault == "blocked_without_closure":
                semantic["terminal_lifecycle"]["status"] = "BLOCKED"
        return wrapper(request, provider=kind, model="model", semantic=semantic)
    def fresh():
        freshness.append(None)
        return DecisionPacket(packet.as_dict())
    provider = adapter(kind, native(kind, callback), kind, "model")
    if fault is None:
        result = attempt(provider, packet, package, bindings, profile_package, fresh, conformance_only=True)
        assert result.decision["semantic_value"]["acceptance_phase_ledger"] == [{"id": "AC1", "phase": "CLAIM_NOW"}]
        for section in ("cross_authority_context", "canonical_shape", "terminal_lifecycle"):
            assert result.decision["semantic_value"][section] == stage2_response(calls[1], candidate)[section]
        assert revalidate_decision(result.decision, calls[1]) == result.decision
    else:
        with pytest.raises(BrainAttemptError) as error:
            attempt(provider, packet, package, bindings, profile_package, fresh, conformance_only=True)
        assert error.value.reason_code == "PROVIDER_RESPONSE_INVALID"
    assert len(calls) == 2 and len(freshness) == 1


def test_stage2_uses_stage1_normalized_packages_after_supplier_mutation(registry, profile_package):
    packet, package, bindings = setup(registry)
    calls = []
    def callback(request):
        calls.append(request)
        return wrapper(request)
    def supplier():
        package.clear()
        bindings.clear()
        profile_package.clear()
        return DecisionPacket(packet.as_dict())
    result = attempt(MappingBrainProvider("mapping", "one", callback), packet, package,
                     bindings, profile_package, supplier, conformance_only=True)
    assert len(calls) == 2
    assert calls[1]["return_contract_package"] == calls[0]["return_contract_package"]
    assert calls[1]["external_bindings"] == calls[0]["external_bindings"]
    assert calls[1]["audit_profile_package"] == calls[0]["audit_profile_package"]
    assert result.decision["semantic_value"]["outcome"] == "CANDIDATE"


@pytest.mark.parametrize("kind", ["mapping", "json"])
def test_adapter_identity_is_immutable(kind):
    provider = adapter(kind, lambda request: None)
    with pytest.raises(FrozenInstanceError):
        provider.model = "replacement"


@pytest.mark.parametrize("kind", ["mapping", "json"])
@pytest.mark.parametrize("failure,expected,count,phase", [
    ("stale", "STALE_BEFORE_STAGE2", 1, "FRESH_PACKET"),
    ("supplier_exception", "FRESH_PACKET_UNAVAILABLE", 1, "FRESH_PACKET"),
    ("supplier_invalid", "FRESH_PACKET_INVALID", 1, "FRESH_PACKET"),
    ("stage1_transport", "PROVIDER_TRANSPORT_FAILURE", 1, "AUDIT_CONSTRUCT"),
    ("stage1_semantic", "PROVIDER_RESPONSE_INVALID", 1, "AUDIT_CONSTRUCT"),
    ("stage2_transport", "PROVIDER_TRANSPORT_FAILURE", 2, "AUDIT_RECONCILE"),
    ("stage2_semantic", "PROVIDER_RESPONSE_INVALID", 2, "AUDIT_RECONCILE"),
])
def test_exact_failure_boundaries(registry, profile_package, kind, failure, expected, count, phase):
    packet, package, bindings = setup(registry)
    calls, fresh_calls = [], []
    def callback(request):
        calls.append(request)
        if failure == "stage1_transport" or failure == "stage2_transport" and len(calls) == 2:
            raise OSError("provider down")
        if failure == "stage1_semantic" or failure == "stage2_semantic" and len(calls) == 2:
            return wrapper(request, provider=kind, semantic={"request_fingerprint": "wrong", "candidate": {}})
        return wrapper(request, provider=kind)
    def supplier():
        fresh_calls.append(None)
        if failure == "supplier_exception":
            raise OSError("freshness unavailable")
        if failure == "supplier_invalid":
            return packet.as_dict()
        if failure == "stale":
            body = packet.as_dict()
            body["packet_fingerprint"] = "0" * 64
            return DecisionPacket(body)
        return DecisionPacket(packet.as_dict())
    with pytest.raises(BrainAttemptError) as caught:
        attempt(adapter(kind, native(kind, callback), kind, "one"), packet,
                package, bindings, profile_package, supplier, conformance_only=True)
    error = caught.value
    assert (error.reason_code, error.invocation_count, error.phase) == (expected, count, phase)
    assert len(calls) == count
    assert len(fresh_calls) == (0 if failure.startswith("stage1_") else 1)


@pytest.mark.parametrize("kind", ["mapping", "json"])
@pytest.mark.parametrize("stage", [1, 2])
def test_provider_model_drift(registry, profile_package, kind, stage):
    packet, package, bindings = setup(registry)
    calls = []
    def callback(request):
        calls.append(None)
        return wrapper(request, provider=kind, model="wrong" if len(calls) == stage else "one")
    with pytest.raises(BrainAttemptError) as caught:
        attempt(adapter(kind, native(kind, callback), kind, "one"), packet,
                package, bindings, profile_package, lambda: DecisionPacket(packet.as_dict()), conformance_only=True)
    assert (caught.value.reason_code, caught.value.invocation_count) == ("PROVIDER_ATTRIBUTION_MISMATCH", stage)
    assert len(calls) == stage


def test_caller_input_invalid_before_invocation(registry):
    packet, package, bindings = setup(registry, "DIAGNOSTIC")
    calls = []
    provider = MappingBrainProvider("mapping", "one", lambda request: calls.append(request))
    with pytest.raises(BrainAttemptError) as caught:
        attempt(provider, packet.as_dict(), package, bindings)
    assert (caught.value.reason_code, caught.value.invocation_count) == ("PROTOCOL_INPUT_INVALID", 0)
    assert calls == []


@pytest.mark.parametrize("kind", ["mapping", "json"])
@pytest.mark.parametrize("malformation", ["extra", "attribution_extra", "attribution_missing", "attribution_long", "native_long", "semantic"])
def test_closed_wrappers_and_bounds(registry, kind, malformation):
    packet, package, bindings = setup(registry, "DIAGNOSTIC")
    calls = []
    def callback(request):
        calls.append(None)
        value = wrapper(request, provider=kind)
        if malformation == "extra":
            value["extra"] = 1
        elif malformation == "attribution_extra":
            value["attribution"]["endpoint"] = "hidden"
        elif malformation == "attribution_missing":
            del value["attribution"]["invocation_id"]
        elif malformation == "attribution_long":
            value["attribution"]["session_id"] = "x" * 2049
        elif malformation == "native_long":
            value["semantic_response"]["padding"] = "x" * 786432
        else:
            value["semantic_response"]["candidate"] = {"bad": "shape"}
        return value
    with pytest.raises(BrainAttemptError) as caught:
        attempt(adapter(kind, native(kind, callback), kind, "one"), packet, package, bindings)
    assert (caught.value.reason_code, caught.value.invocation_count) == ("PROVIDER_RESPONSE_INVALID", 1)
    assert len(calls) == 1


@pytest.mark.parametrize("raw", [b"\xff", b"{", b'{"semantic_response":{},"semantic_response":{},"attribution":{}}',
                                     b'{"semantic_response":{},"attribution":{},"extra":NaN}'])
def test_json_native_malformed(registry, raw):
    packet, package, bindings = setup(registry, "DIAGNOSTIC")
    with pytest.raises(BrainAttemptError) as caught:
        attempt(JsonBrainProvider("json", "one", lambda request: raw), packet, package, bindings)
    assert caught.value.reason_code == "PROVIDER_RESPONSE_INVALID"


@pytest.mark.parametrize("bad", [{"semantic_response": {}, "attribution": {1: "x"}},
                                  {"semantic_response": {}, "attribution": {"x": (1, 2)}},
                                  {"semantic_response": {}, "attribution": {"x": float("nan")}}])
def test_mapping_native_non_json_grammar(registry, bad):
    packet, package, bindings = setup(registry, "DIAGNOSTIC")
    with pytest.raises(BrainAttemptError) as caught:
        attempt(MappingBrainProvider("mapping", "one", lambda request: bad), packet, package, bindings)
    assert caught.value.reason_code == "PROVIDER_RESPONSE_INVALID"


def test_changed_candidate_no_decision_stays_transient(registry, profile_package):
    packet, package, bindings = setup(registry)
    calls = []
    def callback(request):
        calls.append(request)
        if len(calls) == 1:
            return wrapper(request)
        changed = {**TASK, "goal": "Addressed risk"}
        material = stage2_response(request, changed, blocker=True)
        material["construct_audit"][0] = {
            "lens": material["construct_audit"][0]["lens"], "outcome": "RISK_FOUND",
            "risks": [{"risk_summary": "Change needed", "counterexample": "Current candidate fails",
                       "candidate_anchor": "task_id", "disposition": "ADDRESSED_BY_RECONCILIATION"}],
        }
        return wrapper(request, semantic=material)
    result = attempt(MappingBrainProvider("mapping", "one", callback), packet, package,
                     bindings, profile_package, lambda: DecisionPacket(packet.as_dict()), conformance_only=True)
    assert len(calls) == 2
    assert result.decision["semantic_value"]["outcome"] == "NO_DECISION"
    assert result.decision["semantic_value"]["handoff_candidate"] is None
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(result.decision, calls[1])


@pytest.mark.parametrize("flow", R5_FLOWS)
def test_task392_first_party_default_caller_uses_separately_published_mapping(registry, canonical_audit_repo, flow):
    canonical = canonical_audit_repo
    original = deepcopy(canonical.roadmap[canonical.approval_key])
    approval = canonical.publish_successor()
    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical)
    if flow == "TASK_AUTHORING":
        context = sources["task_source_context"]
        context["source_material"] = [{"path": context["authorization"]["scope"]["modify"][0],
                                       "content": "def retain(context): return context"}]
    elif flow == "REPAIR_AUTHORING":
        body = supplied.as_dict()
        body["canonical_facts"]["task_contract"]["acceptance"] = [{"id": "AC1", "condition": "Preserve the explicit context"}]
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        supplied = DecisionPacket(body)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=r5_response(req))
    # No policy, authenticator, explicit package or isolated conformance cutover.
    if flow == "REMEDIATION_AUTHORING":
        with pytest.raises(BrainAttemptError) as retired:
            attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                                 fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()), repo=canonical.repo, **sources)
        assert retired.value.reason_code == "PROTOCOL_INPUT_INVALID"
        assert retired.value.invocation_count == 0 and calls == []
        return
    result = attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                     fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()), repo=canonical.repo, **sources)
    expected = approval["target_profiles"][flow]
    assert result.decision["audit_profile_ref"] == expected
    assert all(c["audit_profile_package"]["audit_profile_ref"] == expected for c in calls)
    assert calls[0]["audit_selection"]["policy"]["r5_publication_sha"] == canonical.successor_source_sha
    assert len(calls) == (1 if flow == "REPAIR_AUTHORING" else 2)
    assert canonical.roadmap[canonical.approval_key] == original
    if flow in {"ARCHITECTURE", "REMEDIATION_AUTHORING"}:
        assert expected == original["target_profiles"][flow]


def test_task392_admitted_default_never_switches_when_successor_approval_arrives(registry, canonical_audit_repo):
    canonical = canonical_audit_repo
    supplied, contract, bindings, sources = production_inputs(registry, "TASK_AUTHORING", canonical)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        canonical.publish_successor()
        return wrapper(req, semantic=r5_response(req))
    with pytest.raises(BrainAttemptError):
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                fresh_packet_supplier=lambda: supplied, repo=canonical.repo, **sources)
    assert len(calls) == 1 and calls[0]["audit_profile_package"]["audit_profile_ref"]["id"] == "tca-1"


def task395_production_inputs(registry, flow, canonical):
    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical)
    if flow == "TASK_AUTHORING":
        context = sources["task_source_context"]
        context["source_material"] = [{"path": context["authorization"]["scope"]["modify"][0],
                                       "content": "def retain(context): return context"}]
    elif flow == "REPAIR_AUTHORING":
        body = supplied.as_dict()
        body["canonical_facts"]["task_contract"]["acceptance"] = [
            {"id": "AC1", "condition": "Preserve the explicit context"}]
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        supplied = DecisionPacket(body)
    return supplied, contract, bindings, sources


@pytest.mark.parametrize("flow", R5_FLOWS)
@pytest.mark.parametrize("kind", ("mapping", "json"))
def test_task395_approved_record_drives_first_party_production_selection(
        registry, task395_canonical_audit_repo, flow, kind):
    from aios_renew.brain_context import _R21_SUCCESSOR_APPROVAL
    canonical = task395_canonical_audit_repo
    approval = canonical.roadmap[_R21_SUCCESSOR_APPROVAL]
    supplied, contract, bindings, sources = task395_production_inputs(registry, flow, canonical)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, provider=kind, model=kind, semantic=r5_response(req))
    # The ordinary caller authenticates canonical Git. No supplied policy,
    # explicit profile, selection authenticator or conformance-only path.
    if flow == "REMEDIATION_AUTHORING":
        with pytest.raises(BrainAttemptError) as retired:
            attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings,
                                 fresh_packet_supplier=lambda: supplied, repo=canonical.repo, **sources)
        assert retired.value.reason_code == "PROTOCOL_INPUT_INVALID"
        assert retired.value.invocation_count == 0 and calls == []
        return
    result = attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings,
                     fresh_packet_supplier=lambda: supplied, repo=canonical.repo, **sources)
    policy = calls[0]["audit_selection"]["policy"]
    assert policy["profiles"] == approval["target_profiles"]
    assert policy["profiles"]["TASK_AUTHORING"]["id"] == "tca-2"
    assert policy["profiles"]["REPAIR_AUTHORING"]["id"] == "roa-1-r3"
    assert policy["r5_publication_sha"] == canonical.successor_source_sha
    assert policy["human_authority_digest"] == digest(approval)
    assert result.decision["audit_profile_ref"] == approval["target_profiles"][flow]
    assert all(req["audit_profile_package"]["audit_profile_ref"] == approval["target_profiles"][flow]
               and req["audit_selection"] == calls[0]["audit_selection"] for req in calls)
    assert [req["request_mode"] for req in calls] == {
        "ARCHITECTURE": ["AOA_CONSTRUCT", "AOA_RECONCILE"],
        "TASK_AUTHORING": ["TCA_CONSTRUCT", "TCA_RECONCILE"],
        "REMEDIATION_AUTHORING": ["MOA_CONSTRUCT", "MOA_RECONCILE"],
        "REPAIR_AUTHORING": ["ROA_CONFORMANCE"],
    }[flow]
    assert revalidate_decision(result.decision, calls[-1]) == result.decision
    for unchanged in ("ARCHITECTURE", "REMEDIATION_AUTHORING"):
        assert policy["profiles"][unchanged] == canonical.roadmap[canonical.approval_key]["target_profiles"][unchanged]


@pytest.mark.parametrize("fault", (
    "partial", "mixed_tca", "mixed_roa", "profile_digest", "approval_digest", "old_approval",
    "review_sha", "review_source", "source_sha", "approved_against", "missing_candidate", "wrong_candidate",
    "missing_review", "missing_artifact", "forged_artifact", "unpublished", "unreviewed_body",
    "duplicate", "duplicate_key", "stale_packet", "untrusted_remote", "revoked",
))
def test_task395_invalid_activation_stops_ordinary_caller_before_provider(
        registry, canonical_audit_repo, task395_activation_record, fault):
    import yaml
    from aios_renew.brain_context import _R21_SUCCESSOR_APPROVAL
    canonical = canonical_audit_repo
    approval = canonical.publish_successor(activation_record=task395_activation_record,
                                          omit_reviewed_body=fault == "unreviewed_body")
    if fault == "partial":
        del approval["target_profiles"]["REPAIR_AUTHORING"]
    elif fault in {"mixed_tca", "mixed_roa"}:
        flow = "TASK_AUTHORING" if fault == "mixed_tca" else "REPAIR_AUTHORING"
        approval["target_profiles"][flow] = deepcopy(canonical.roadmap[canonical.approval_key]["target_profiles"][flow])
    elif fault == "profile_digest":
        approval["target_profiles"]["TASK_AUTHORING"]["digest"] = "f" * 64
    elif fault == "approval_digest":
        approval["superseded_approval_digest"] = "f" * 64
    elif fault == "old_approval":
        canonical.roadmap[canonical.approval_key]["status"] = "ACTIVE"
    elif fault == "review_sha":
        approval["review_decision_sha"] = canonical.review_sha
    elif fault == "review_source":
        review = yaml.safe_load(canonical.git(canonical.canonical, "show",
            f"{canonical.successor_review_sha}:.ai/reviews/REVIEW-392-001.yaml"))
        review["reviewed_sha"] = canonical.source_sha
        canonical.git(canonical.canonical, "switch", "--detach", canonical.successor_source_sha)
        canonical.write(".ai/reviews/REVIEW-392-001.yaml", yaml.safe_dump(review).encode())
        sha = canonical.commit("forged successor review source")
        canonical.git(canonical.canonical, "update-ref", "refs/heads/aios/review-decision/RUN-392-001", sha)
        canonical.git(canonical.canonical, "switch", "main")
        approval["review_decision_sha"] = sha
    elif fault in {"source_sha", "approved_against"}:
        approval["reviewed_published_source_sha" if fault == "source_sha" else "approved_against_main_sha"] = canonical.source_sha
    elif fault in {"missing_candidate", "missing_review", "missing_artifact"}:
        kind = {"missing_candidate": "review", "missing_review": "review-decision", "missing_artifact": "artifacts"}[fault]
        canonical.git(canonical.canonical, "update-ref", "-d", f"refs/heads/aios/{kind}/RUN-392-001")
    elif fault in {"wrong_candidate", "forged_artifact"}:
        kind, sha = ("review", canonical.source_sha) if fault == "wrong_candidate" else ("artifacts", canonical.artifact_sha)
        canonical.git(canonical.canonical, "update-ref", f"refs/heads/aios/{kind}/RUN-392-001", sha)
    elif fault == "unpublished":
        canonical.git(canonical.canonical, "switch", "--detach", canonical.source_sha)
        canonical.write(".ai/roadmap-state.yaml", yaml.safe_dump(canonical.roadmap).encode())
        sha = canonical.commit("activation without successor publication ancestry")
        canonical.git(canonical.canonical, "branch", "-f", "main", sha)
        canonical.git(canonical.canonical, "switch", "main")
    elif fault == "duplicate":
        canonical.roadmap[_R21_SUCCESSOR_APPROVAL + "_other"] = deepcopy(approval)
    elif fault == "untrusted_remote":
        canonical.git(canonical.repo, "config", "branch.main.remote", ".")
    elif fault == "revoked":
        approval["status"] = "REVOKED"
    canonical.save_roadmap()
    if fault == "duplicate_key":
        raw = yaml.safe_dump(canonical.roadmap).encode()
        canonical.save_roadmap(raw + yaml.safe_dump({_R21_SUCCESSOR_APPROVAL: approval}).encode())
    # Even an unchanged AOA caller must reject any invalid member of the policy.
    supplied, contract, bindings, sources = production_inputs(registry, "ARCHITECTURE", canonical)
    if fault == "stale_packet":
        canonical.main_sha = canonical.commit("main moved after packet composition")
    calls = []
    with pytest.raises(BrainAttemptError) as error:
        attempt(MappingBrainProvider("mapping", "one", lambda req: calls.append(req)), supplied, contract, bindings,
                fresh_packet_supplier=lambda: supplied, repo=canonical.repo, **sources)
    assert error.value.reason_code == "PROTOCOL_INPUT_INVALID"
    assert error.value.invocation_count == 0 and calls == []


@pytest.mark.parametrize("flow", ("TASK_AUTHORING", "REPAIR_AUTHORING"))
def test_task395_unpublished_local_record_keeps_old_policy_and_historical_replay(
        registry, canonical_audit_repo, task395_activation_record, flow):
    import yaml
    from aios_renew.brain_context import _R21_SUCCESSOR_APPROVAL
    canonical = canonical_audit_repo
    # A local policy carrier, even with the exact approved contents, is untrusted.
    local = {**deepcopy(canonical.roadmap), _R21_SUCCESSOR_APPROVAL: deepcopy(task395_activation_record)}
    (canonical.repo / ".ai/roadmap-state.yaml").write_bytes(yaml.safe_dump(local).encode())
    supplied, contract, bindings, sources = production_inputs(registry, flow, canonical)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=r5_response(req))
    provider = MappingBrainProvider("mapping", "one", callback)
    old = attempt(provider, supplied, contract, bindings, fresh_packet_supplier=lambda: supplied,
                  repo=canonical.repo, **sources)
    historical = deepcopy(calls[-1])
    assert old.decision["audit_profile_ref"] == canonical.roadmap[canonical.approval_key]["target_profiles"][flow]
    canonical.publish_successor(activation_record=task395_activation_record)
    assert revalidate_decision(old.decision, historical) == old.decision
    supplied, contract, bindings, sources = task395_production_inputs(registry, flow, canonical)
    new = attempt(provider, supplied, contract, bindings, fresh_packet_supplier=lambda: supplied,
                  repo=canonical.repo, **sources)
    assert new.decision["audit_profile_ref"] == canonical.roadmap[_R21_SUCCESSOR_APPROVAL]["target_profiles"][flow]
    assert old.decision["audit_profile_ref"] != new.decision["audit_profile_ref"]
    assert revalidate_decision(old.decision, historical) == old.decision


@pytest.mark.parametrize("flow", ("TASK_AUTHORING", "REPAIR_AUTHORING"))
@pytest.mark.parametrize("moved", ("main", "review", "artifacts"))
def test_task395_admitted_successor_stops_on_movement_without_reselection(
        registry, task395_canonical_audit_repo, flow, moved):
    canonical = task395_canonical_audit_repo
    supplied, contract, bindings, sources = task395_production_inputs(registry, flow, canonical)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        if moved == "main":
            canonical.main_sha = canonical.commit("main moved during admitted successor audit")
        else:
            kind, sha = ("review-decision", canonical.review_sha) if moved == "review" else ("artifacts", canonical.artifact_sha)
            canonical.git(canonical.canonical, "update-ref", f"refs/heads/aios/{kind}/RUN-392-001", sha)
        return wrapper(req, semantic=r5_response(req))
    with pytest.raises(BrainAttemptError) as error:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                fresh_packet_supplier=lambda: supplied, repo=canonical.repo, **sources)
    assert error.value.reason_code == "FRESH_PACKET_INVALID"
    assert error.value.invocation_count == len(calls) == 1
    assert calls[0]["audit_profile_package"]["audit_profile_ref"]["id"] == (
        "tca-2" if flow == "TASK_AUTHORING" else "roa-1-r3")


@pytest.mark.parametrize("kind", ("mapping", "json"))
def test_task405_tca3_two_pass_provider_and_prospective_cutover_preserve_tca2_until_published(
        registry, task395_canonical_audit_repo, kind):
    from aios_renew.brain_audit import _TCA_V3_DIGEST, _TCA_SUCCESSOR_DIGEST
    from aios_renew.brain_context import _R21_TCA3_APPROVAL, _TCA3_PRIORITY_RECORD
    import yaml
    from test_brain_provider_protocol import ROOT
    canonical = task395_canonical_audit_repo
    priority = yaml.safe_load((ROOT / ".ai/roadmap-state.yaml").read_bytes())[_TCA3_PRIORITY_RECORD]
    canonical.roadmap[_TCA3_PRIORITY_RECORD] = deepcopy(priority)
    canonical.save_roadmap()

    # Step 1: With human_tca3_retrospective_priority_20261023 published, tca-2 remains the live default.
    supplied2, contract2, bindings2, sources2 = task395_production_inputs(registry, "TASK_AUTHORING", canonical)
    calls2 = []
    def callback2(req):
        calls2.append(deepcopy(req))
        return wrapper(req, provider=kind, model=kind, semantic=r5_response(req))
    res2 = attempt(adapter(kind, native(kind, callback2), kind, kind), supplied2, contract2, bindings2,
                   fresh_packet_supplier=lambda: DecisionPacket(supplied2.as_dict()), repo=canonical.repo, **sources2)
    assert len(calls2) == 2
    assert res2.decision["audit_profile_ref"] == {"id": "tca-2", "version": 2, "digest": _TCA_SUCCESSOR_DIGEST}
    historical_tca2_req = deepcopy(calls2[-1])

    # Step 2: After separately reviewed and published tca-3 activation cutover, first-party caller uses tca-3.
    tca3_approval = canonical.publish_tca3_successor()
    assert revalidate_decision(res2.decision, historical_tca2_req) == res2.decision

    supplied3, contract3, bindings3, sources3 = task395_production_inputs(registry, "TASK_AUTHORING", canonical)
    context3 = sources3["task_source_context"]
    context3["baseline_observations"] = [
        {"id": "V1", "command": context3["task"]["verification"]["required"][0], "status": "NOT_RUN",
         "summary": "Pre-execution authoring audit; Runtime owns canonical verification."}
    ]
    calls3 = []
    def callback3(req):
        calls3.append(deepcopy(req))
        return wrapper(req, provider=kind, model=kind, semantic=r5_response(req))
    res3 = attempt(adapter(kind, native(kind, callback3), kind, kind), supplied3, contract3, bindings3,
                   fresh_packet_supplier=lambda: DecisionPacket(supplied3.as_dict()), repo=canonical.repo, **sources3)
    assert len(calls3) == 2
    assert [r["request_mode"] for r in calls3] == ["TCA_CONSTRUCT", "TCA_RECONCILE"]
    assert res3.decision["audit_profile_ref"] == {"id": "tca-3", "version": 3, "digest": _TCA_V3_DIGEST}
    assert calls3[0]["audit_selection"]["policy"]["profiles"] == tca3_approval["target_profiles"]
    assert calls3[0]["audit_selection"]["policy"]["r5_publication_sha"] == canonical.tca3_source_sha
    assert "verification_checks" in res3.decision["semantic_value"]
    assert revalidate_decision(res3.decision, calls3[-1]) == res3.decision
    # Historical tca-2 decision still revalidates against its original request without reinterpretation.
    assert revalidate_decision(res2.decision, historical_tca2_req) == res2.decision


def task406_production_inputs(registry, flow, canonical):
    supplied, contract, bindings, sources = task395_production_inputs(registry, flow, canonical)
    if flow == "TASK_AUTHORING":
        context = sources["task_source_context"]
        context["baseline_observations"] = [
            {"id": f"V{i}", "command": command, "status": "NOT_RUN", "summary": "Runtime owns verification."}
            for i, command in enumerate(context["task"]["verification"]["required"], 1)]
    return supplied, contract, bindings, sources


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "TASK_AUTHORING", "REPAIR_AUTHORING"))
@pytest.mark.parametrize("kind", ("mapping", "json"))
def test_task406_current_native_caller_uses_exact_three_flow_default(
        registry, task406_canonical_audit_repo, flow, kind):
    from aios_renew.brain_audit import _three_flow_profiles
    c = task406_canonical_audit_repo
    supplied, contract, bindings, sources = task406_production_inputs(registry, flow, c)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, provider=kind, model=kind, semantic=r5_response(req))
    result = attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings,
                     fresh_packet_supplier=lambda: supplied, repo=c.repo, **sources)
    assert result.decision["audit_profile_ref"] == _three_flow_profiles()[flow]
    assert [r["request_mode"] for r in calls] == {
        "ARCHITECTURE": ["AOA_CONSTRUCT", "AOA_RECONCILE"], "TASK_AUTHORING": ["TCA_CONSTRUCT", "TCA_RECONCILE"],
        "REPAIR_AUTHORING": ["ROA_CONFORMANCE"]}[flow]
    assert all(r["audit_selection"]["policy"]["profiles"] == _three_flow_profiles() for r in calls)
    assert all(r["audit_selection"] == calls[0]["audit_selection"] for r in calls)
    assert revalidate_decision(result.decision, calls[-1]) == result.decision


@pytest.mark.parametrize("material", ("absent", "moa", "v1", "three_flow", "unavailable_git"))
def test_task406_remediation_current_default_never_invokes_a_provider_or_requires_audit_material(
        registry, moa_package, v1_only_package, monkeypatch, material):
    from aios_renew.brain_audit import _three_flow_profiles
    supplied, contract, bindings, _ = moa_inputs(registry)
    calls = []
    def forbidden(*args, **kwargs):
        raise AssertionError("retired remediation must not read audit authority or invoke a provider")
    monkeypatch.setattr("aios_renew.brain_context.canonical_audit_inputs", forbidden)
    if material == "unavailable_git":
        monkeypatch.setattr("subprocess.run", forbidden)
    kwargs = {}
    package = moa_package if material == "moa" else v1_only_package if material == "v1" else None
    if material == "three_flow":
        policy = audit_policy_example(supplied.as_dict())
        policy.update(state="THREE_SPECIALIZED", profiles=_three_flow_profiles())
        kwargs.update(audit_policy=policy, selection_authenticator=forbidden, human_v1_deep_support={})
    with pytest.raises(BrainAttemptError) as error:
        attempt(MappingBrainProvider("mapping", "one", lambda req: calls.append(req)), supplied, contract, bindings,
                package, **kwargs)
    assert error.value.reason_code == "PROTOCOL_INPUT_INVALID"
    assert error.value.invocation_count == 0 and calls == []


@pytest.mark.parametrize("flow", ("ARCHITECTURE", "TASK_AUTHORING", "REPAIR_AUTHORING"))
def test_task406_individually_authenticated_human_v1_keeps_exact_applicability(
        registry, task406_canonical_audit_repo, v1_only_package, flow):
    c = task406_canonical_audit_repo
    supplied, contract, bindings, sources = task406_production_inputs(registry, flow, c)
    if flow in {"ARCHITECTURE", "TASK_AUTHORING"}:
        context = sources["architecture_source_context" if flow == "ARCHITECTURE" else "task_source_context"]
        choices = {"human_v1_option": human_v1_selection(supplied, contract, bindings, context, v1_only_package)}
    else:
        choices = {"human_v1_deep_support": {
            "selected_by": "HUMAN", "option": "V1_READ_ONLY", "packet_fingerprint": supplied.packet_fingerprint,
            "work_context_fingerprint": supplied.as_dict()["work_context_fingerprint"], "return_contract_ref": contract["return_contract_ref"],
            "external_bindings": bindings, "source_context_fingerprint": digest({key: sources.get(key) for key in (
                "moa_source_context", "task_source_context", "architecture_source_context")}),
            "audit_profile_ref": v1_only_package["audit_profile_ref"]}}
    calls, phases = [], []
    def human(selection, phase):
        phases.append(phase)
        return all(selection[key] == value for key, value in choices.items())
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=r5_response(req))
    provider = MappingBrainProvider("mapping", "one", callback)
    # A serialized Human label alone cannot select V1.
    with pytest.raises(BrainAttemptError):
        attempt(provider, supplied, contract, bindings, fresh_packet_supplier=lambda: supplied, repo=c.repo, **sources, **choices)
    assert calls == []
    result = attempt(provider, supplied, contract, bindings, fresh_packet_supplier=lambda: supplied, repo=c.repo,
                     human_choice_authenticator=human, **sources, **choices)
    assert phases[0] == "ADMIT" and "CONTINUE" in phases
    if flow == "REPAIR_AUTHORING":
        assert len(calls) == 1 and result.decision["audit_profile_ref"]["id"] == "roa-1-r3"
    else:
        assert [r["request_mode"] for r in calls] == ["V1_OPTION_CONSTRUCT", "V1_OPTION_RECONCILE"]
        assert result.decision["audit_profile_ref"] == v1_only_package["audit_profile_ref"]


@pytest.mark.parametrize("movement", ("main", "review", "artifact", "failure", "failure_artifacts", "authorization", "repository", "git"))
def test_task406_currentness_stops_before_another_provider_call(
        registry, task406_canonical_audit_repo, monkeypatch, movement):
    from aios_renew.brain_context import _THREE_FLOW_RUN
    c = task406_canonical_audit_repo
    supplied, contract, bindings, sources = task406_production_inputs(registry, "TASK_AUTHORING", c)
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        if movement == "main":
            c.commit("canonical main moved during TASK-406 audit")
        elif movement in {"review", "artifact"}:
            kind = "review-decision" if movement == "review" else "artifacts"
            c.git(c.canonical, "update-ref", f"refs/heads/aios/{kind}/{_THREE_FLOW_RUN}", c.source_sha)
        elif movement in {"failure", "failure_artifacts", "authorization"}:
            kind = {"failure": "failure", "failure_artifacts": "failure-artifacts", "authorization": "repair"}[movement]
            failed_run = c.three_flow_lineage["failed_run_id"]
            c.git(c.canonical, "update-ref", f"refs/heads/aios/{kind}/{failed_run}", c.source_sha)
        elif movement == "repository":
            c.git(c.repo, "config", "remote.origin.url", str(c.canonical) + "/.")
        else:
            def unavailable(*args, **kwargs):
                raise OSError("Git unavailable")
            monkeypatch.setattr("aios_renew.authoring_ingress._query_matching_refs", unavailable)
        return wrapper(req, semantic=r5_response(req))
    with pytest.raises(BrainAttemptError) as error:
        attempt(MappingBrainProvider("mapping", "one", callback), supplied, contract, bindings,
                fresh_packet_supplier=lambda: supplied, repo=c.repo, **sources)
    assert error.value.invocation_count == len(calls) == 1
    assert calls[0]["audit_profile_package"]["audit_profile_ref"]["id"] == "tca-3"


@pytest.mark.parametrize("kind", ("mapping", "json"))
def test_task408_ordinary_provider_direct_defaults_and_soft_architecture(registry, task408_canonical_audit_repo, kind):
    c = task408_canonical_audit_repo
    before = c.git(c.canonical, "show-ref")
    for flow in ("TASK_AUTHORING", "REPAIR_AUTHORING", "REMEDIATION_AUTHORING", "ARCHITECTURE"):
        supplied, contract, bindings, sources, candidate = task408_inputs(registry, flow, c)
        calls = []
        def callback(req):
            calls.append(deepcopy(req))
            semantic = response(req, candidate) if req["request_mode"] == "DIRECT" else r5_response(req)
            return wrapper(req, provider=kind, model=kind, semantic=semantic)
        result = attempt(adapter(kind, native(kind, callback), kind, kind), supplied, contract, bindings,
                         fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()), repo=c.repo,
                         **(sources if flow == "ARCHITECTURE" else {}))
        modes = ["AOA_CONSTRUCT", "AOA_RECONCILE"] if flow == "ARCHITECTURE" else ["DIRECT"]
        assert [req["request_mode"] for req in calls] == modes
        assert result.advisory is not None and result.advisory["authority"] == "NONE"
        assert revalidate_decision(result.decision, calls[-1]) == result.decision
        if flow != "ARCHITECTURE":
            assert result.decision["semantic_value"] == candidate
            assert result.decision["audit_profile_ref"] is None
            assert calls[0]["audit_profile_package"] is calls[0]["stage1_lineage"] is None
            assert not {"task_source_context", "moa_source_context", "architecture_source_context"} & set(calls[0])
            assert calls[0]["audit_selection"]["human_audit_choice"] is None
    assert c.git(c.canonical, "show-ref") == before


@pytest.mark.parametrize("kind", ("mapping", "json"))
def test_task408_trusted_on_demand_is_one_request_and_never_becomes_a_default(registry, task408_canonical_audit_repo, kind):
    c = task408_canonical_audit_repo
    before = c.git(c.canonical, "show-ref")
    for flow, modes in (("TASK_AUTHORING", ["TCA_CONSTRUCT", "TCA_RECONCILE"]),
                        ("REPAIR_AUTHORING", ["ROA_CONFORMANCE"]),
                        ("REMEDIATION_AUTHORING", ["MOA_CONSTRUCT", "MOA_RECONCILE"])):
        supplied, contract, bindings, sources, candidate = task408_inputs(registry, flow, c)
        reference = task408_policy(supplied.as_dict())["on_demand_profiles"][flow]
        choice = task408_choice(supplied, contract, bindings, sources, reference)
        trusted = ExactHumanChoice(choice)
        calls = []
        def callback(req):
            calls.append(deepcopy(req))
            return wrapper(req, provider=kind, model=kind, semantic=(
                response(req, candidate) if req["request_mode"] == "DIRECT" else r5_response(req)))
        provider = adapter(kind, native(kind, callback), kind, kind)
        result = attempt(provider, supplied, contract, bindings, repo=c.repo,
                         fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()),
                         human_audit_choice=choice, human_choice_authenticator=trusted, **sources)
        assert [req["request_mode"] for req in calls] == modes
        assert result.decision["audit_profile_ref"] == reference
        assert result.decision["semantic_value"]["closure"]["outcome"] in {"CANDIDATE", "NO_DECISION"}
        assert revalidate_decision(result.decision, calls[-1]) == result.decision
        assert trusted.phases[0] == "ADMIT" and "CONTINUE" in trusted.phases
        with pytest.raises(BrainAttemptError) as replay:
            attempt(provider, supplied, contract, bindings, repo=c.repo,
                    fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()),
                    human_audit_choice=choice, human_choice_authenticator=trusted, **sources)
        assert replay.value.invocation_count == 0
        calls.clear()
        direct = attempt(provider, supplied, contract, bindings, repo=c.repo,
                         fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()))
        assert [req["request_mode"] for req in calls] == ["DIRECT"]
        assert direct.decision["semantic_value"] == candidate
    assert c.git(c.canonical, "show-ref") == before


def test_task408_no_aoa_is_authenticated_exact_and_has_no_fallback(registry, task408_canonical_audit_repo):
    c = task408_canonical_audit_repo
    supplied, contract, bindings, _, candidate = task408_inputs(registry, "ARCHITECTURE", c)
    choice = task408_choice(supplied, contract, bindings, {}, None, "NO_AOA")
    calls = []
    def callback(req):
        calls.append(deepcopy(req))
        return wrapper(req, semantic=response(req, candidate))
    provider = MappingBrainProvider("mapping", "one", callback)
    for trusted in (None, lambda *_: False):
        with pytest.raises(BrainAttemptError) as error:
            attempt(provider, supplied, contract, bindings, repo=c.repo,
                    fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()),
                    human_audit_choice=choice, human_choice_authenticator=trusted)
        assert error.value.invocation_count == 0
    result = attempt(provider, supplied, contract, bindings, repo=c.repo,
                     fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()),
                     human_audit_choice=choice, human_choice_authenticator=ExactHumanChoice(choice))
    assert len(calls) == 1 and calls[0]["request_mode"] == "DIRECT"
    assert result.decision["semantic_value"] == candidate
    assert result.decision["audit_profile_ref"] is None


@pytest.mark.parametrize("change", ("main", "subject", "canonical_task", "human_wording"))
def test_task408_stale_choices_and_direct_return_movement_fail_closed(registry, task408_canonical_audit_repo, change):
    c = task408_canonical_audit_repo
    supplied, contract, bindings, sources, candidate = task408_inputs(registry, "TASK_AUTHORING", c)
    choice = task408_choice(supplied, contract, bindings, sources,
                            task408_policy(supplied.as_dict())["on_demand_profiles"]["TASK_AUTHORING"])
    calls = []
    def callback(req):
        calls.append(req)
        if change == "main":
            c.main_sha = c.commit("canonical movement during direct call")
        return wrapper(req, semantic=response(req, candidate))
    provider = MappingBrainProvider("mapping", "one", callback)
    if change in {"subject", "canonical_task"}:
        if change == "subject":
            choice["external_bindings"]["revision"] += 1
        else:
            sources["task_source_context"]["task"]["goal"] += " forged canonical source"
            choice = task408_choice(supplied, contract, bindings, sources, choice["audit_profile_ref"])
        with pytest.raises(BrainAttemptError) as error:
            attempt(provider, supplied, contract, bindings, repo=c.repo,
                    fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()),
                    human_audit_choice=choice, human_choice_authenticator=ExactHumanChoice(choice), **sources)
        assert error.value.invocation_count == 0 and calls == []
    elif change == "main":
        with pytest.raises(BrainAttemptError) as error:
            attempt(provider, supplied, contract, bindings, repo=c.repo,
                    fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()))
        assert error.value.reason_code == "STALE_BEFORE_DIRECT_RETURN" and len(calls) == 1
    else:
        body = supplied.as_dict()
        body["human_input"] = "HUMAN says audit this TASK; model-generated selection"
        body["packet_fingerprint"] = digest({k: v for k, v in body.items() if k != "packet_fingerprint"})
        supplied = DecisionPacket(body)
        result = attempt(provider, supplied, contract, bindings, repo=c.repo,
                         fresh_packet_supplier=lambda: DecisionPacket(supplied.as_dict()))
        assert len(calls) == 1 and result.decision["decision_mode"] == "DIRECT"
