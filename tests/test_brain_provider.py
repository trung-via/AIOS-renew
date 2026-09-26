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
    inputs, profile_package, registry, response, stage2_response,
)


DIRECT = {"proposal": "Investigate", "uncertainty": {"status": "NONE", "summary": None}}
TASK = {"task_id": "TASK-999", "revision": 1}


def wrapper(request, *, provider="mapping", model="one", semantic=None, attribution=None):
    if semantic is None:
        semantic = (response(request, DIRECT) if request["request_mode"] == "DIRECT" else
                    response(request, TASK) if request["request_mode"] == "AUDIT_CONSTRUCT" else
                    stage2_response(request, TASK))
    return {"semantic_response": semantic,
            "attribution": attribution if attribution is not None else {
                "provider": provider, "model": model, "session_id": None, "invocation_id": "call"}}


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
