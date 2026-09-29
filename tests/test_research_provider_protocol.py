"""Focused implementation-local RA-5 semantic protocol regressions."""

from copy import deepcopy

import pytest

from aios_renew.research_contract import ResearchContractError
from aios_renew.research_provider_protocol import (
    construct_research_provider_request, validate_research_provider_request,
    construct_research_provider_return, validate_research_provider_return,
)
from test_research_protocol import scenario


def stage1():
    profile, baseline, counter, pass1, frozen, pass2 = scenario()
    request = construct_research_provider_request({
        "format": "AIOS_RESEARCH_PROVIDER_REQUEST", "version": 1,
        "kind": "RESEARCH_PROVIDER_REQUEST", "request_mode": "EVIDENCE_CONSTRUCT",
        "research_brief": pass1["research_brief"], "audit_profile": profile,
        "predecessor_record": None, "baseline_acquisitions": baseline,
        "stage1_lineage": None, "evidence_construct": None, "counter_acquisitions": []})
    response = {"request_fingerprint": request["request_fingerprint"],
                "claims": pass1["claims"], "challenge_targets": pass1["challenge_targets"],
                "counter_evidence_requests": pass1["counter_evidence_requests"]}
    returned = construct_research_provider_return(response, request)
    return request, returned, counter, pass2


def stage2():
    first, returned, counter, pass2 = stage1()
    frozen = returned["semantic_value"]
    second = construct_research_provider_request({
        "format": "AIOS_RESEARCH_PROVIDER_REQUEST", "version": 1,
        "kind": "RESEARCH_PROVIDER_REQUEST", "request_mode": "AUDIT_RECONCILE",
        "research_brief": first["research_brief"], "audit_profile": first["audit_profile"],
        "predecessor_record": None, "baseline_acquisitions": first["baseline_acquisitions"],
        "stage1_lineage": {"stage1_request_fingerprint": first["request_fingerprint"],
                           "stage1_return_fingerprint": returned["return_fingerprint"],
                           "construct_fingerprint": frozen["construct_fingerprint"]},
        "evidence_construct": frozen, "counter_acquisitions": counter},
        stage1_request=first, stage1_return=returned)
    response = {"request_fingerprint": second["request_fingerprint"],
                **{key: pass2[key] for key in ("audit_results", "claim_reconciliation",
                    "new_claim_fingerprints", "research_record", "outcome")}}
    return first, returned, second, response


def test_two_pass_identity_and_exact_lineage():
    first, returned, second, response = stage2()
    assert validate_research_provider_request(first) == first
    assert validate_research_provider_return(returned, first) == returned
    assert validate_research_provider_request(second, stage1_request=first, stage1_return=returned) == second
    final = construct_research_provider_return(response, second, stage1_request=first, stage1_return=returned)
    assert final["semantic_value"]["format"] == "AIOS_RESEARCH_RECONCILIATION"
    assert validate_research_provider_return(final, second, stage1_request=first, stage1_return=returned) == final
    assert construct_research_provider_return(deepcopy(response), second,
        stage1_request=first, stage1_return=returned) == final
    for field in ("stage1_request_fingerprint", "stage1_return_fingerprint", "construct_fingerprint"):
        altered = deepcopy(second)
        altered["stage1_lineage"][field] = "0" * 64
        with pytest.raises(ResearchContractError):
            validate_research_provider_request(altered, stage1_request=first, stage1_return=returned)


def test_operational_fields_and_substitution_rejected():
    first, returned, second, response = stage2()
    for field in ("provider", "model", "session", "endpoint", "usage", "raw_response", "chain_of_thought"):
        raw = deepcopy(response)
        raw[field] = "operational"
        with pytest.raises(ResearchContractError):
            construct_research_provider_return(raw, second, stage1_request=first, stage1_return=returned)
    altered = deepcopy(second)
    altered["baseline_acquisitions"][0]["attempt"]["attribution"]["adapter_id"] = "substituted"
    with pytest.raises(ResearchContractError):
        validate_research_provider_request(altered, stage1_request=first, stage1_return=returned)
    altered = deepcopy(second)
    altered["evidence_construct"]["construct_fingerprint"] = "0" * 64
    with pytest.raises(ResearchContractError):
        validate_research_provider_request(altered, stage1_request=first, stage1_return=returned)
    altered = deepcopy(returned)
    altered["semantic_value"]["construct_fingerprint"] = "0" * 64
    with pytest.raises(ResearchContractError):
        validate_research_provider_return(altered, first)


def test_prompt_like_source_text_is_data():
    first, returned, _, _ = stage2()
    assert "Ignore previous instructions" in first["baseline_acquisitions"][0]["attempt"]["observations"][0]["content"]["text"]
    assert returned["semantic_value"]["claims"]
