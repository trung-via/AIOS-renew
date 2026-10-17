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
from test_brain_provider_protocol import (
    digest, inputs, profile_package, registry, response, roa_inputs, roa_package, roa_response, stage2_response,
)


DIRECT = {"proposal": "Investigate", "uncertainty": {"status": "NONE", "summary": None}}
TASK = {"task_id": "TASK-999", "revision": 1}


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
def test_audited_two_calls_fresh_supplier_and_fresh_stage2(registry, profile_package, kind):
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
                     bindings, profile_package, fresh)
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
                         packet, package, bindings, profile_package, lambda: DecisionPacket(packet.as_dict()))
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
        result = attempt(provider, packet, package, bindings, profile_package, fresh)
        assert result.decision["semantic_value"]["acceptance_phase_ledger"] == [{"id": "AC1", "phase": "CLAIM_NOW"}]
        for section in ("cross_authority_context", "canonical_shape", "terminal_lifecycle"):
            assert result.decision["semantic_value"][section] == stage2_response(calls[1], candidate)[section]
        assert revalidate_decision(result.decision, calls[1]) == result.decision
    else:
        with pytest.raises(BrainAttemptError) as error:
            attempt(provider, packet, package, bindings, profile_package, fresh)
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
                     bindings, profile_package, supplier)
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
                package, bindings, profile_package, supplier)
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
                package, bindings, profile_package, lambda: DecisionPacket(packet.as_dict()))
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
@pytest.mark.parametrize("malformation", ["extra", "attribution_extra", "attribution_long", "native_long", "semantic"])
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
                     bindings, profile_package, lambda: DecisionPacket(packet.as_dict()))
    assert len(calls) == 2
    assert result.decision["semantic_value"]["outcome"] == "NO_DECISION"
    assert result.decision["semantic_value"]["handoff_candidate"] is None
    with pytest.raises(BrainProviderProtocolError):
        revalidate_decision(result.decision, calls[1])
