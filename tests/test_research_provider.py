"""One-shot RA-6 provider integration with injected native transports."""

from copy import deepcopy
import json

import pytest

from aios_renew.research_provider import (
    JsonResearchProvider, MappingResearchProvider, ResearchInvocationError,
    invoke_research_provider,
)
from aios_renew.research_provider_protocol import validate_research_provider_return
from test_research_provider_protocol import stage1, stage2


def wrapped(response, provider="alpha", model="m1", session=None):
    return {"semantic_response": deepcopy(response),
            "attribution": {"provider": provider, "model": model,
                            "session_id": session, "invocation_id": None}}


def test_fresh_stage1_cross_provider_identity_and_distinct_valid_conclusions():
    first, expected, _, _ = stage1()
    response = {"request_fingerprint": first["request_fingerprint"],
                **{key: expected["semantic_value"][key] for key in
                   ("claims", "challenge_targets", "counter_evidence_requests")}}
    count = []

    def alpha(request):
        count.append(request["request_fingerprint"])
        return wrapped(response, session="session-one")

    a = invoke_research_provider(MappingResearchProvider("alpha", "m1", alpha), first)
    assert count == [first["request_fingerprint"]]
    assert a.semantic_return == expected
    assert a.attribution["session_id"] == "session-one"
    assert "alpha" not in json.dumps(a.semantic_return)
    assert "session-one" not in json.dumps(a.semantic_return)
    assert validate_research_provider_return(a.semantic_return, first) == a.semantic_return


def test_stage2_explicit_other_provider_preserves_stage1_lineage():
    first, returned, second, response = stage2()
    baseline = invoke_research_provider(
        MappingResearchProvider("alpha", "m1", lambda _: wrapped(response)), second,
        stage1_request=first, stage1_return=returned)
    alternative = deepcopy(response)
    alternative["audit_results"][0]["disposition"] = "LIMITATION"
    seen = []

    def beta(request_bytes):
        seen.append(json.loads(request_bytes)["request_fingerprint"])
        return json.dumps(wrapped(alternative, "beta", "m2", "new-session"))

    other = invoke_research_provider(JsonResearchProvider("beta", "m2", beta), second,
                                     stage1_request=first, stage1_return=returned)
    assert seen == [second["request_fingerprint"]]
    assert baseline.semantic_return["request_fingerprint"] == other.semantic_return["request_fingerprint"]
    assert baseline.semantic_return["return_fingerprint"] != other.semantic_return["return_fingerprint"]
    assert other.semantic_return["semantic_value"]["audit_results"][0]["disposition"] == "LIMITATION"
    assert other.attribution["provider"] == "beta"
    assert "beta" not in json.dumps(other.semantic_return)
    bad = deepcopy(second)
    bad["stage1_lineage"]["stage1_return_fingerprint"] = "0" * 64
    with pytest.raises(ResearchInvocationError) as exc:
        invoke_research_provider(JsonResearchProvider("beta", "m2", beta), bad,
                                 stage1_request=first, stage1_return=returned)
    assert exc.value.reason_code == "PROTOCOL_INPUT_INVALID"
    assert exc.value.invocation_count == 0
    assert seen == [second["request_fingerprint"]]


@pytest.mark.parametrize("native,reason", [
    (lambda response: wrapped(response, "other"), "PROVIDER_ATTRIBUTION_MISMATCH"),
    (lambda response: {**wrapped(response), "credentials": "secret"}, "PROVIDER_RESPONSE_INVALID"),
    (lambda response: {**wrapped(response), "semantic_response": {**response, "provider": "alpha"}},
     "PROVIDER_RESPONSE_INVALID"),
    (lambda response: "bad native type", "PROVIDER_RESPONSE_INVALID"),
])
def test_one_call_failure_is_bounded(native, reason):
    first, expected, _, _ = stage1()
    response = {"request_fingerprint": first["request_fingerprint"],
                **{key: expected["semantic_value"][key] for key in
                   ("claims", "challenge_targets", "counter_evidence_requests")}}
    calls = []

    def transport(request):
        calls.append(request)
        return native(response)

    with pytest.raises(ResearchInvocationError) as exc:
        invoke_research_provider(MappingResearchProvider("alpha", "m1", transport), first)
    assert exc.value.reason_code == reason
    assert exc.value.invocation_count == 1
    assert len(calls) == 1


def test_transport_failure_does_not_retry_or_advance():
    first, _, _, _ = stage1()
    count = []

    def broken(request):
        count.append(request)
        raise OSError("transport unavailable")

    with pytest.raises(ResearchInvocationError) as exc:
        invoke_research_provider(MappingResearchProvider("alpha", "m1", broken), first)
    assert (exc.value.reason_code, exc.value.invocation_count) == ("PROVIDER_TRANSPORT_FAILURE", 1)
    assert len(count) == 1
