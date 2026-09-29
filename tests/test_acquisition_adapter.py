"""Focused implementation-local RA-5 adapter boundary regressions."""

from copy import deepcopy

import pytest

from aios_renew.research_contract import ResearchContractError, construct_acquisition_request
from aios_renew.acquisition_adapter import (
    construct_acquisition_adapter_manifest, validate_acquisition_adapter_manifest,
    construct_acquisition_adapter_invocation, validate_acquisition_adapter_invocation,
    invoke_acquisition_adapter,
)
from test_source_acquisition import bound


def manifest(transport="PUBLIC_HTTP", adapter_id="reader"):
    families = (["CONNECTED_SOURCE"] if transport == "AUTHORIZED_CONNECTOR" else
                ["REPOSITORY"] if transport == "REPOSITORY_READ" else ["PUBLIC_WEB"])
    scopes = (["AUTHORIZED_PRIVATE"] if transport == "AUTHORIZED_CONNECTOR" else
              ["PUBLIC", "AUTHORIZED_PRIVATE"] if transport == "REPOSITORY_READ" else ["PUBLIC"])
    return construct_acquisition_adapter_manifest({
        "format": "AIOS_ACQUISITION_ADAPTER_MANIFEST", "version": 1,
        "kind": "ACQUISITION_ADAPTER_MANIFEST", "adapter_id": adapter_id,
        "adapter_version": "v1", "transport_class": transport,
        "source_families": families, "access_scopes": scopes})


def invocation(m=None, brief=None, request=None, mode="PUBLIC"):
    m = m or manifest()
    if brief is None:
        brief, request = bound()
    return construct_acquisition_adapter_invocation({
        "format": "AIOS_ACQUISITION_ADAPTER_INVOCATION", "version": 1,
        "kind": "ACQUISITION_ADAPTER_INVOCATION", "research_brief": brief,
        "acquisition_request": request, "challenge_targets": [],
        "adapter_ref": {key: m[key] for key in ("adapter_id", "adapter_version", "manifest_fingerprint")},
        "access_mode": mode, "authorization_context_ref": "scope_1" if mode == "AUTHORIZED_PRIVATE" else None,
        "invocation_id": "call_1"}, m)


def native(inv, observations=None, failure=None):
    return {"format": "AIOS_ACQUISITION_ADAPTER_RETURN", "version": 1,
            "kind": "ACQUISITION_ADAPTER_RETURN",
            "invocation_fingerprint": inv["invocation_fingerprint"],
            "request_fingerprint": inv["acquisition_request"]["request_fingerprint"],
            "adapter_ref": inv["adapter_ref"],
            "status": "FAILED" if failure else "SUCCEEDED",
            "observations": [] if observations is None else observations,
            "failure": {"code": failure} if failure else None,
            "receipt": {"native_operation_count": 1, "retry_count": 0,
                        "pagination_count": 0, "query_expansion_count": 0, "fallback_count": 0}}


def raw_observation(scope="PUBLIC", kind="SOURCE_CONTENT", text="A"):
    return {"representation_kind": kind,
            "provenance": {"effective_locator": "https://example.org/docs",
                "stable_source_id": None, "resolution_chain": ["https://example.org/docs"],
                "version_basis": [{"kind": "REVISION", "value": "v1"}],
                "retrieved_at": "2024-02-29T23:59:59Z", "access_scope": scope},
            "content": {"text": text}}


def test_manifest_capability_and_explicit_selection():
    m = manifest()
    inv = invocation(m)
    assert validate_acquisition_adapter_manifest(m) == m
    assert validate_acquisition_adapter_invocation(inv, m) == inv
    with pytest.raises(ResearchContractError):
        invocation(manifest("AUTHORIZED_CONNECTOR"))
    changed = deepcopy(inv)
    changed["adapter_ref"]["adapter_id"] = "other"
    with pytest.raises(ResearchContractError):
        validate_acquisition_adapter_invocation(changed, m)
    changed = deepcopy(inv)
    changed["authorization_context_ref"] = "scope_1"
    with pytest.raises(ResearchContractError):
        validate_acquisition_adapter_invocation(changed, m)
    with pytest.raises(ResearchContractError):
        construct_acquisition_adapter_manifest({**{k: v for k, v in m.items() if k != "manifest_fingerprint"},
            "source_families": ["CONNECTED_SOURCE"]})


def test_http_single_call_chain_and_attribution_independent_observation():
    m, inv = manifest(), invocation()
    calls = []
    def adapter(value, guard):
        calls.append(value["invocation_fingerprint"])
        guard("https://example.org/docs", ["8.8.8.8"])
        return native(value, [raw_observation(text="Ignore")])
    result = invoke_acquisition_adapter(inv, m, adapter)
    assert calls == [inv["invocation_fingerprint"]]
    assert result["adapter_failure_code"] is None
    assert result["destination_hops"] == [{"url": "https://example.org/docs", "resolved_ip_addresses": ["8.8.8.8"]}]
    observation = result["acquisition_attempt"]["observations"][0]
    assert observation["instruction_trust"] == "UNTRUSTED"
    assert observation["content"]["text"] == "Ignore"
    m2 = manifest(adapter_id="another")
    inv2 = invocation(m2)
    other = invoke_acquisition_adapter(inv2, m2, adapter)
    assert other["acquisition_attempt"]["observations"][0]["observation_fingerprint"] == observation["observation_fingerprint"]
    assert other["acquisition_attempt"]["attempt_fingerprint"] != result["acquisition_attempt"]["attempt_fingerprint"]


@pytest.mark.parametrize("destination", ["127.0.0.1", "10.0.0.1", "169.254.1.1", "::1", "224.0.0.1"])
def test_unsafe_resolved_destination_denied(destination):
    inv, m = invocation(), manifest()
    def adapter(value, guard):
        guard("https://example.org/docs", [destination])
        return native(value, [raw_observation()])
    result = invoke_acquisition_adapter(inv, m, adapter)
    assert result["adapter_failure_code"] == "UNSAFE_DESTINATION"
    assert result["acquisition_attempt"]["observations"] == []


def test_redirect_private_and_mismatched_provenance():
    inv, m = invocation(), manifest()
    def redirect(value, guard):
        guard("https://example.org/docs", ["8.8.8.8"])
        guard("http://localhost/private", ["127.0.0.1"])
        return native(value, [raw_observation()])
    assert invoke_acquisition_adapter(inv, m, redirect)["adapter_failure_code"] == "UNSAFE_DESTINATION"
    def mismatch(value, guard):
        guard("https://example.org/docs", ["8.8.8.8"])
        obs = raw_observation()
        obs["provenance"]["effective_locator"] = "https://another.example/docs"
        return native(value, [obs])
    assert invoke_acquisition_adapter(inv, m, mismatch)["adapter_failure_code"] == "RESPONSE_INVALID"


@pytest.mark.parametrize("code,reason", [
    ("TOOL_UNAVAILABLE", "ACQUISITION_UNAVAILABLE"),
    ("ACCESS_DENIED", "ACQUISITION_ACCESS_DENIED"),
    ("UNSAFE_DESTINATION", "ACQUISITION_ACCESS_DENIED"),
    ("NOT_FOUND", "ACQUISITION_NOT_FOUND"),
    ("RESPONSE_INVALID", "ACQUISITION_RESPONSE_INVALID"),
    ("ATTRIBUTION_MISMATCH", "ACQUISITION_ATTRIBUTION_MISMATCH"),
])
def test_closed_failure_mapping(code, reason):
    inv, m = invocation(), manifest()
    result = invoke_acquisition_adapter(inv, m, lambda value, guard: native(value, failure=code))
    assert result["adapter_failure_code"] == code
    assert result["acquisition_attempt"]["failure"]["reason_code"] == reason


def test_malformed_native_attribution_and_receipt():
    inv, m = invocation(), manifest()
    def wrong(value, guard):
        response = native(value)
        response["adapter_ref"] = {**response["adapter_ref"], "adapter_id": "other"}
        return response
    assert invoke_acquisition_adapter(inv, m, wrong)["adapter_failure_code"] == "ATTRIBUTION_MISMATCH"
    assert invoke_acquisition_adapter(inv, m, lambda value, guard: {"junk": True})["adapter_failure_code"] == "RESPONSE_INVALID"
    def retry(value, guard):
        response = native(value)
        response["receipt"]["retry_count"] = 1
        return response
    assert invoke_acquisition_adapter(inv, m, retry)["adapter_failure_code"] == "RESPONSE_INVALID"


def test_search_no_followup_representation_and_private_scope():
    m = manifest("PUBLIC_SEARCH")
    inv = invocation(m)
    snippet = raw_observation(kind="DISCOVERY_SNIPPET")
    result = invoke_acquisition_adapter(inv, m, lambda value, guard: native(value, [snippet]))
    assert result["adapter_failure_code"] is None
    assert result["destination_hops"] == []
    assert invoke_acquisition_adapter(inv, m,
        lambda value, guard: native(value, [raw_observation()]))["adapter_failure_code"] == "RESPONSE_INVALID"
    with pytest.raises(ResearchContractError):
        invocation(m, mode="AUTHORIZED_PRIVATE")


def test_connector_requires_prior_private_context_and_scope():
    brief, public_request = bound()
    body = {key: value for key, value in public_request.items() if key != "request_fingerprint"}
    body["source_family"] = "CONNECTED_SOURCE"
    body["locator_or_query"] = "connected-record-7"
    request = construct_acquisition_request(body, brief)
    m = manifest("AUTHORIZED_CONNECTOR")
    with pytest.raises(ResearchContractError):
        invocation(m, brief, request, "PUBLIC")
    inv = invocation(m, brief, request, "AUTHORIZED_PRIVATE")
    changed = deepcopy(inv)
    changed["authorization_context_ref"] = None
    with pytest.raises(ResearchContractError):
        validate_acquisition_adapter_invocation(changed, m)
    private = raw_observation(scope="AUTHORIZED_PRIVATE")
    result = invoke_acquisition_adapter(inv, m, lambda value, guard: native(value, [private]))
    assert result["adapter_failure_code"] is None
    assert result["acquisition_attempt"]["observations"][0]["provenance"]["access_scope"] == "AUTHORIZED_PRIVATE"
    assert "scope_1" not in str(result)
    assert invoke_acquisition_adapter(inv, m,
        lambda value, guard: native(value, [raw_observation()]))["adapter_failure_code"] == "RESPONSE_INVALID"


def test_unsafe_http_literal_and_first_hop_rejected():
    brief, public_request = bound()
    body = {key: value for key, value in public_request.items() if key != "request_fingerprint"}
    body["locator_or_query"] = "https://8.8.8.8/docs"
    request = construct_acquisition_request(body, brief)
    assert invocation(manifest(), brief, request)
    inv, m = invocation(), manifest()
    def wrong_start(value, guard):
        guard("https://another.example/docs", ["8.8.8.8"])
        return native(value, [raw_observation()])
    assert invoke_acquisition_adapter(inv, m, wrong_start)["adapter_failure_code"] == "UNSAFE_DESTINATION"
