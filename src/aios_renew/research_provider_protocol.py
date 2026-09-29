"""Pure, caller-supplied RA-5 Brain research semantic protocol.

These transient artifacts carry no provider identity, acquisition authority or
lifecycle effect. RA-4 remains the sole constructor of semantic values.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .research_contract import (
    ResearchContractError, validate_acquisition_request, validate_research_brief,
)
from .source_acquisition import validate_acquisition_attempt
from .research_record import (normalize_research_audit_profile,
                              research_audit_profile_ref, validate_research_record)
from .research_protocol import (
    construct_research_evidence_construct, validate_research_evidence_construct,
    construct_research_reconciliation, validate_research_reconciliation,
)


_REQUEST = {"format", "version", "kind", "request_mode", "research_brief",
            "audit_profile", "predecessor_record", "baseline_acquisitions",
            "stage1_lineage", "evidence_construct", "counter_acquisitions",
            "request_fingerprint"}
_RETURN = {"format", "version", "kind", "request_mode", "request_fingerprint",
           "semantic_value", "return_fingerprint"}
_STAGE1 = {"stage1_request_fingerprint", "stage1_return_fingerprint", "construct_fingerprint"}


def _fields(value: Any, names: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != names:
        raise ResearchContractError(f"invalid {label} fields")
    return value


def _json(value: Any, ceiling: int, depth_limit: int = 32) -> bytes:
    def walk(item: Any, depth: int) -> None:
        if depth > depth_limit:
            raise ResearchContractError("research provider material is too deep")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ResearchContractError("non-text JSON key")
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
        elif type(item) is list:
            for child in item:
                walk(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ResearchContractError("non-JSON research provider material")
    walk(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ResearchContractError("invalid strict JSON") from exc
    if len(encoded) > ceiling:
        raise ResearchContractError("research provider material exceeds byte limit")
    return encoded


def _identity(body: dict[str, Any], field: str, ceiling: int) -> dict[str, Any]:
    result = dict(body, **{field: hashlib.sha256(_json(body, ceiling)).hexdigest()})
    _json(result, ceiling)
    return result


def _pairs(raw: Any, brief: dict[str, Any], phase: str, targets: dict[str, Any],
           maximum: int, expected: set[str] | None = None) -> list[dict[str, Any]]:
    if type(raw) is not list or not 1 <= len(raw) <= maximum:
        raise ResearchContractError("invalid acquisition pair batch")
    pairs, seen = [], set()
    for entry in raw:
        pair = _fields(entry, {"request", "attempt"}, "acquisition pair")
        request_raw = pair["request"]
        if type(request_raw) is not dict or type(request_raw.get("challenge_target_fingerprints")) is not list:
            raise ResearchContractError("invalid acquisition request")
        refs = request_raw["challenge_target_fingerprints"]
        if any(type(ref) is not str or ref not in targets for ref in refs):
            raise ResearchContractError("foreign challenge target")
        bound_targets = [targets[ref] for ref in refs]
        request = validate_acquisition_request(request_raw, brief, bound_targets)
        if request["phase"] != phase or request["request_fingerprint"] in seen:
            raise ResearchContractError("wrong phase or duplicate acquisition request")
        seen.add(request["request_fingerprint"])
        attempt = validate_acquisition_attempt(pair["attempt"], brief, request, bound_targets)
        if attempt["outcome"] != "SUCCEEDED":
            raise ResearchContractError("research provider requires successful acquisition")
        pairs.append({"request": request, "attempt": attempt})
    if expected is not None and seen != expected:
        raise ResearchContractError("counter acquisition does not cover precommit")
    return sorted(pairs, key=lambda entry: entry["request"]["request_fingerprint"])


def _request(material: Any, stage1_request: Any, stage1_return: Any,
             validate: bool) -> dict[str, Any]:
    item = _fields(material, _REQUEST if validate else _REQUEST - {"request_fingerprint"},
                   "research provider request")
    _json(item, 16777216)
    for name, expected in (("format", "AIOS_RESEARCH_PROVIDER_REQUEST"), ("version", 1),
                           ("kind", "RESEARCH_PROVIDER_REQUEST")):
        if type(item[name]) is not type(expected) or item[name] != expected:
            raise ResearchContractError(f"invalid {name}")
    mode = item["request_mode"]
    if mode not in ("EVIDENCE_CONSTRUCT", "AUDIT_RECONCILE"):
        raise ResearchContractError("invalid research provider mode")
    brief = validate_research_brief(item["research_brief"])
    profile = normalize_research_audit_profile(item["audit_profile"])
    predecessor = (None if item["predecessor_record"] is None else
                   validate_research_record(item["predecessor_record"], profile))
    if predecessor is not None and (predecessor["research_brief"] != brief):
        raise ResearchContractError("predecessor Brief substitution")
    baseline = _pairs(item["baseline_acquisitions"], brief, "BASELINE", {},
                      brief["resource_bounds"]["max_baseline_requests"])
    if mode == "EVIDENCE_CONSTRUCT":
        if item["stage1_lineage"] is not None or item["evidence_construct"] is not None or item["counter_acquisitions"] != []:
            raise ResearchContractError("Stage-1 forbids Stage-2 material")
        lineage, frozen, counter = None, None, []
    else:
        if stage1_request is None or stage1_return is None:
            raise ResearchContractError("Stage-2 requires exact Stage-1 request and return")
        prior = _request(stage1_request, None, None, True)
        returned = _return(stage1_return, prior, True)
        if prior["request_mode"] != "EVIDENCE_CONSTRUCT" or any(
            prior[name] != value for name, value in (("research_brief", brief),
                ("audit_profile", profile), ("predecessor_record", predecessor),
                ("baseline_acquisitions", baseline))):
            raise ResearchContractError("Stage-1 semantic basis substitution")
        frozen = validate_research_evidence_construct(item["evidence_construct"], profile, baseline)
        if returned["semantic_value"] != frozen:
            raise ResearchContractError("Stage-1 construct substitution")
        lineage = _fields(item["stage1_lineage"], _STAGE1, "Stage-1 lineage")
        expected_lineage = {"stage1_request_fingerprint": prior["request_fingerprint"],
                            "stage1_return_fingerprint": returned["return_fingerprint"],
                            "construct_fingerprint": frozen["construct_fingerprint"]}
        if lineage != expected_lineage:
            raise ResearchContractError("Stage-1 lineage substitution")
        targets = {target["target_fingerprint"]: target for target in frozen["challenge_targets"]}
        expected = {request["request_fingerprint"] for request in frozen["counter_evidence_requests"]}
        counter = _pairs(item["counter_acquisitions"], brief, "COUNTER_EVIDENCE", targets,
                         len(expected), expected)
        requests = {request["request_fingerprint"]: request for request in frozen["counter_evidence_requests"]}
        if any(pair["request"] != requests[pair["request"]["request_fingerprint"]] for pair in counter):
            raise ResearchContractError("counter request substitution")
    body = {"format": "AIOS_RESEARCH_PROVIDER_REQUEST", "version": 1,
            "kind": "RESEARCH_PROVIDER_REQUEST", "request_mode": mode,
            "research_brief": brief, "audit_profile": profile, "predecessor_record": predecessor,
            "baseline_acquisitions": baseline, "stage1_lineage": lineage,
            "evidence_construct": frozen, "counter_acquisitions": counter}
    result = _identity(body, "request_fingerprint", 16777216)
    if validate and item["request_fingerprint"] != result["request_fingerprint"]:
        raise ResearchContractError("research provider request fingerprint mismatch")
    return result


def construct_research_provider_request(material: Any, *, stage1_request: Any = None,
                                        stage1_return: Any = None) -> dict[str, Any]:
    return _request(material, stage1_request, stage1_return, False)


def validate_research_provider_request(material: Any, *, stage1_request: Any = None,
                                       stage1_return: Any = None) -> dict[str, Any]:
    return _request(material, stage1_request, stage1_return, True)


def _return(material: Any, request: Any, validate: bool) -> dict[str, Any]:
    item = _fields(material, _RETURN if validate else
                   ({"request_fingerprint", "claims", "challenge_targets", "counter_evidence_requests"}
                    if request["request_mode"] == "EVIDENCE_CONSTRUCT" else
                    {"request_fingerprint", "audit_results", "claim_reconciliation",
                     "new_claim_fingerprints", "research_record", "outcome"}),
                   "research provider response or return")
    _json(item, 4194304)
    if item["request_fingerprint"] != request["request_fingerprint"]:
        raise ResearchContractError("research provider response request mismatch")
    mode = request["request_mode"]
    if validate:
        if (item["format"], item["version"], item["kind"], item["request_mode"]) != (
            "AIOS_RESEARCH_PROVIDER_RETURN", 1, "RESEARCH_PROVIDER_RETURN", mode):
            raise ResearchContractError("invalid research provider return header")
        semantic = (validate_research_evidence_construct(item["semantic_value"],
                    request["audit_profile"], request["baseline_acquisitions"])
                    if mode == "EVIDENCE_CONSTRUCT" else
                    validate_research_reconciliation(item["semantic_value"], request["evidence_construct"],
                    request["audit_profile"], request["baseline_acquisitions"],
                    request["counter_acquisitions"], request["predecessor_record"]))
    elif mode == "EVIDENCE_CONSTRUCT":
        semantic = construct_research_evidence_construct({
            "format": "AIOS_RESEARCH_EVIDENCE_CONSTRUCT", "version": 1,
            "kind": "RESEARCH_EVIDENCE_CONSTRUCT", "research_brief": request["research_brief"],
            "audit_profile_ref": research_audit_profile_ref(request["audit_profile"]),
            "claims": item["claims"], "challenge_targets": item["challenge_targets"],
            "counter_evidence_requests": item["counter_evidence_requests"]},
            request["audit_profile"], request["baseline_acquisitions"])
    else:
        semantic = construct_research_reconciliation({
            "format": "AIOS_RESEARCH_RECONCILIATION", "version": 1,
            "kind": "RESEARCH_RECONCILIATION",
            "construct_fingerprint": request["evidence_construct"]["construct_fingerprint"],
            "audit_results": item["audit_results"], "claim_reconciliation": item["claim_reconciliation"],
            "new_claim_fingerprints": item["new_claim_fingerprints"],
            "research_record": item["research_record"], "outcome": item["outcome"]},
            request["evidence_construct"], request["audit_profile"],
            request["baseline_acquisitions"], request["counter_acquisitions"],
            request["predecessor_record"])
    body = {"format": "AIOS_RESEARCH_PROVIDER_RETURN", "version": 1,
            "kind": "RESEARCH_PROVIDER_RETURN", "request_mode": mode,
            "request_fingerprint": request["request_fingerprint"], "semantic_value": semantic}
    result = _identity(body, "return_fingerprint", 4194304)
    if validate and item != result:
        raise ResearchContractError("research provider return substitution")
    return result


def construct_research_provider_return(raw_response: Any, request: Any, *,
                                       stage1_request: Any = None,
                                       stage1_return: Any = None) -> dict[str, Any]:
    bound = validate_research_provider_request(request, stage1_request=stage1_request,
                                               stage1_return=stage1_return)
    return _return(raw_response, bound, False)


def validate_research_provider_return(material: Any, request: Any, *,
                                      stage1_request: Any = None,
                                      stage1_return: Any = None) -> dict[str, Any]:
    bound = validate_research_provider_request(request, stage1_request=stage1_request,
                                               stage1_return=stage1_return)
    return _return(material, bound, True)


__all__ = ["construct_research_provider_request", "validate_research_provider_request",
           "construct_research_provider_return", "validate_research_provider_return"]
