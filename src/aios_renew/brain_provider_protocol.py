"""Pure, bounded BP5-P2B semantic request and decision envelopes.

All inputs are caller supplied. These functions neither invoke a provider nor
grant authority to write a canonical artifact.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

from .brain_audit import (
    BrainAuditError, _ordered_lenses, _packet, _stage1, construct_stage1, normalize_profile,
    profile_ref, validate_stage2,
)
from .brain_return_contract import (
    BrainReturnContractError, _BOUNDS, _CANDIDATE_FIELDS, _CONTRACT_FIELDS,
    _BINDING_FIELDS, _METADATA, _PATH, _SOURCES, return_contract_ref,
)
from .decision_packet import DecisionPacket


class BrainProviderProtocolError(ValueError):
    """The supplied Brain semantic protocol material fails closed."""


_REQUEST_FIELDS = frozenset({
    "format", "version", "kind", "request_mode", "decision_packet",
    "return_contract_package", "external_bindings", "audit_profile_package",
    "stage1_lineage", "request_fingerprint",
})
_DECISION_FIELDS = frozenset({
    "format", "version", "kind", "authority_owner", "selected_flow",
    "decision_mode", "request_fingerprint", "packet_fingerprint",
    "work_context_fingerprint", "decision_family_ref", "handoff_target",
    "expected_return_shape", "return_contract_ref", "audit_profile_ref",
    "external_bindings", "semantic_value", "decision_fingerprint",
})
_STAGE1_LINEAGE_FIELDS = frozenset({
    "stage1_request_fingerprint", "stage1_decision_fingerprint", "construct",
})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_AUDITED = frozenset({
    "ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING",
})
_OPERATIONAL = frozenset({
    "provider", "model", "session", "endpoint", "host", "timestamp", "time",
    "request_id", "invocation_id", "transport", "credential", "credentials",
    "api_key", "token", "raw_response", "usage", "latency", "finish_reason",
    "provider_id", "provider_name", "model_id", "model_name", "session_id",
    "session_name", "endpoint_url", "host_id", "hostname", "request_metadata",
    "operational_metadata", "provider_metadata", "model_metadata", "session_metadata",
})
_OPERATIONAL_COMPACT = frozenset(key.replace("_", "") for key in _OPERATIONAL)
_NESTED_FORMATS = frozenset({
    "AIOS_BRAIN_REQUEST", "AIOS_BRAIN_DECISION", "AIOS_DECISION_PACKET",
    "AIOS_BRAIN_SEMANTIC_AUDIT",
})


def _normal(value: Any, depth: int = 0, *, response: bool = False) -> Any:
    if depth > 32:
        raise BrainProviderProtocolError("semantic structural depth exceeds 32")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise BrainProviderProtocolError("non-finite number")
        return value
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise BrainProviderProtocolError("invalid Unicode") from exc
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if type(value) is list:
        return [_normal(item, depth + 1, response=response) for item in value]
    if type(value) is dict:
        if depth > 0 and value.get("format") in {"AIOS_BRAIN_REQUEST", "AIOS_BRAIN_DECISION"}:
            raise BrainProviderProtocolError("nested Brain protocol envelope")
        if response and value.get("format") in _NESTED_FORMATS:
            raise BrainProviderProtocolError("nested protocol envelope")
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise BrainProviderProtocolError("non-string mapping key")
            name = _normal(key, depth + 1)
            if name in result:
                raise BrainProviderProtocolError("duplicate normalized key")
            folded = name.lower().replace("-", "_")
            if response and (folded in _OPERATIONAL or folded.replace("_", "") in _OPERATIONAL_COMPACT):
                raise BrainProviderProtocolError("operational metadata in semantic response")
            result[name] = _normal(item, depth + 1, response=response)
        return result
    raise BrainProviderProtocolError("non-JSON semantic material")


def _bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise BrainProviderProtocolError("strict UTF-8 JSON required") from exc


def _bound(value: Any, ceiling: int, name: str) -> None:
    if len(_bytes(value)) > ceiling:
        raise BrainProviderProtocolError(f"{name} exceeds {ceiling} UTF-8 bytes")


def _digest(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _exact(value: Any, fields: frozenset[str] | set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise BrainProviderProtocolError(f"{name} fields do not match closed contract")
    return value


def _fingerprint(value: Any, name: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise BrainProviderProtocolError(f"invalid {name}")
    return value


def _contract_package(value: Any, packet: dict[str, Any]) -> dict[str, Any]:
    package = _exact(_normal(value), {"contract", "bounds", "return_contract_ref"}, "return-contract package")
    contract = _exact(package["contract"], _CONTRACT_FIELDS, "selected contract")
    if type(package["bounds"]) is not dict or package["bounds"] != _BOUNDS or any(
        type(package["bounds"][key]) is not int for key in _BOUNDS
    ) or any(
        contract.get(key) != packet[key] for key in _METADATA
    ) or type(contract["version"]) is not int or contract["version"] != 1:
        raise BrainProviderProtocolError("return contract and packet metadata mismatch")
    candidate = _exact(contract["candidate_contract"], _CANDIDATE_FIELDS, "candidate contract")
    if candidate["representation"] != "STRICT_JSON_MAPPING":
        raise BrainProviderProtocolError("invalid candidate representation")
    if type(candidate["shape"]) is not str or not candidate["shape"].strip() or len(candidate["shape"].encode("utf-8")) > _BOUNDS["shape_bytes"]:
        raise BrainProviderProtocolError("invalid candidate shape")
    requirements = candidate["requirements"]
    if type(requirements) is not list or not 1 <= len(requirements) <= _BOUNDS["requirements_count"] or any(
        type(item) is not str or not item.strip() or len(item.encode("utf-8")) > _BOUNDS["requirement_bytes"]
        for item in requirements
    ):
        raise BrainProviderProtocolError("invalid candidate requirements")
    bindings = candidate["bindings"]
    if type(bindings) is not list or len(bindings) > _BOUNDS["bindings_count"]:
        raise BrainProviderProtocolError("invalid contract bindings")
    paths = set()
    for binding in bindings:
        entry = _exact(binding, _BINDING_FIELDS, "contract binding")
        path = entry["path"]
        if (type(path) is not str or _PATH.fullmatch(path) is None or path in paths or
            len(path.encode("utf-8")) > _BOUNDS["binding_path_bytes"] or
            entry["source"] not in _SOURCES or type(entry["instruction"]) is not str or
            not entry["instruction"].strip() or
            len(entry["instruction"].encode("utf-8")) > _BOUNDS["binding_instruction_bytes"]):
            raise BrainProviderProtocolError("invalid contract binding")
        paths.add(path)
    if package["return_contract_ref"] != return_contract_ref(contract, package["bounds"]):
        raise BrainProviderProtocolError("return-contract content identity mismatch")
    _bound({"bounds": package["bounds"], "contract": contract}, _BOUNDS["selected_contract_bytes"], "selected contract")
    return package


def _profile_package(value: Any, flow: str) -> dict[str, Any] | None:
    if flow == "DIAGNOSTIC":
        if value is not None:
            raise BrainProviderProtocolError("DIRECT cannot carry audit profile")
        return None
    package = _exact(_normal(value), {"profile", "audit_profile_ref"}, "audit-profile package")
    profile = normalize_profile(package["profile"])
    if flow not in profile["applicable_flows"] or package["audit_profile_ref"] != profile_ref(profile):
        raise BrainProviderProtocolError("audit-profile identity or applicability mismatch")
    return package


def _bindings(value: Any, package: dict[str, Any]) -> dict[str, Any]:
    bindings = _normal(value)
    required = {item["path"] for item in package["contract"]["candidate_contract"]["bindings"]
                if item["source"] == "EXTERNAL_REQUEST_BINDING_REQUIRED"}
    _exact(bindings, required, "external bindings")
    for item in bindings.values():
        _bound(item, 4096, "external binding")
    return bindings


def _enforce_bindings(candidate: Any, bindings: dict[str, Any]) -> None:
    if type(candidate) is not dict:
        raise BrainProviderProtocolError("candidate must be a mapping")
    for path, expected in bindings.items():
        found: Any = candidate
        for segment in path.split("."):
            if type(found) is not dict or segment not in found:
                raise BrainProviderProtocolError(f"missing external binding {path}")
            found = found[segment]
        if _bytes(found) != _bytes(expected):
            raise BrainProviderProtocolError(f"external binding mismatch at {path}")


def _decision(request: dict[str, Any], semantic_value: dict[str, Any]) -> dict[str, Any]:
    packet = request["decision_packet"]
    result = {
        "format": "AIOS_BRAIN_DECISION", "version": 1, "kind": "BRAIN_DECISION",
        "authority_owner": "BRAIN", "selected_flow": packet["selected_flow"],
        "decision_mode": request["request_mode"],
        "request_fingerprint": request["request_fingerprint"],
        "packet_fingerprint": packet["packet_fingerprint"],
        "work_context_fingerprint": packet["work_context_fingerprint"],
        "decision_family_ref": packet["decision_family_ref"],
        "handoff_target": packet["handoff_target"],
        "expected_return_shape": packet["expected_return_shape"],
        "return_contract_ref": request["return_contract_package"]["return_contract_ref"],
        "audit_profile_ref": (request["audit_profile_package"] or {}).get("audit_profile_ref"),
        "external_bindings": request["external_bindings"], "semantic_value": semantic_value,
    }
    result["decision_fingerprint"] = _digest(result)
    _bound(result, 655360, "Brain decision")
    return result


def _stage1_request(packet: dict[str, Any], contract: dict[str, Any], bindings: dict[str, Any],
                    profile: dict[str, Any]) -> dict[str, Any]:
    result = {
        "format": "AIOS_BRAIN_REQUEST", "version": 1, "kind": "BRAIN_REQUEST",
        "request_mode": "AUDIT_CONSTRUCT", "decision_packet": packet,
        "return_contract_package": contract, "external_bindings": bindings,
        "audit_profile_package": profile, "stage1_lineage": None,
    }
    result["request_fingerprint"] = _digest(result)
    _bound(result, 393216, "Brain request")
    return result


def _lineage(packet: dict[str, Any], contract: dict[str, Any], bindings: dict[str, Any],
             profile: dict[str, Any], value: Any) -> dict[str, Any]:
    lineage = _exact(_normal(value), _STAGE1_LINEAGE_FIELDS, "Stage-1 lineage")
    first_request = _stage1_request(packet, contract, bindings, profile)
    if lineage["stage1_request_fingerprint"] != first_request["request_fingerprint"]:
        raise BrainProviderProtocolError("Stage-1 request replay or substitution")
    construct = _stage1(packet, profile["profile"], lineage["construct"])
    _enforce_bindings(construct["construct_candidate"], bindings)
    first_decision = _decision(first_request, construct)
    if lineage["stage1_decision_fingerprint"] != first_decision["decision_fingerprint"]:
        raise BrainProviderProtocolError("Stage-1 decision lineage mismatch")
    return lineage


def construct_request(packet: DecisionPacket, return_contract_package: Mapping[str, Any],
                      external_bindings: Mapping[str, Any], audit_profile_package: Mapping[str, Any] | None = None,
                      *, request_mode: str, stage1_decision: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Construct one request from an actual freshly compiled packet object."""
    if not isinstance(packet, DecisionPacket):
        raise BrainProviderProtocolError("constructor requires a fresh DecisionPacket object")
    try:
        body = _packet(packet)
        contract = _contract_package(return_contract_package, body)
        bindings = _bindings(external_bindings, contract)
        flow = body["selected_flow"]
        if body["authority_owner"] != "BRAIN" or not (
            (flow in _AUDITED and request_mode in {"AUDIT_CONSTRUCT", "AUDIT_RECONCILE"}) or
            (flow == "DIAGNOSTIC" and request_mode == "DIRECT")
        ):
            raise BrainProviderProtocolError("flow and request mode do not apply")
        profile = _profile_package(audit_profile_package, flow)
        if request_mode == "DIRECT":
            if bindings or stage1_decision is not None:
                raise BrainProviderProtocolError("DIRECT has no bindings or lineage")
            lineage = None
        elif request_mode == "AUDIT_CONSTRUCT":
            if stage1_decision is not None:
                raise BrainProviderProtocolError("Stage 1 has no prior decision")
            return _stage1_request(body, contract, bindings, profile)
        else:
            first_request = _stage1_request(body, contract, bindings, profile)
            first = revalidate_decision(stage1_decision, first_request)
            construct = _stage1(body, profile["profile"], first["semantic_value"])
            _enforce_bindings(construct["construct_candidate"], bindings)
            lineage = {
                "stage1_request_fingerprint": first_request["request_fingerprint"],
                "stage1_decision_fingerprint": first["decision_fingerprint"],
                "construct": construct,
            }
        result = {
            "format": "AIOS_BRAIN_REQUEST", "version": 1, "kind": "BRAIN_REQUEST",
            "request_mode": request_mode, "decision_packet": body,
            "return_contract_package": contract, "external_bindings": bindings,
            "audit_profile_package": profile, "stage1_lineage": lineage,
        }
        result["request_fingerprint"] = _digest(result)
        _bound(result, 393216, "Brain request")
        return result
    except (BrainAuditError, BrainReturnContractError, KeyError, TypeError, RecursionError, UnicodeError) as exc:
        raise BrainProviderProtocolError("invalid caller-supplied Brain request material") from exc


def revalidate_request(value: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate a serialized request, including its complete packet mapping."""
    try:
        request = _exact(_normal(value), _REQUEST_FIELDS, "Brain request")
        if request["format"] != "AIOS_BRAIN_REQUEST" or type(request["version"]) is not int or request["version"] != 1 or request["kind"] != "BRAIN_REQUEST":
            raise BrainProviderProtocolError("invalid request format")
        _bound(request, 393216, "Brain request")
        packet = _packet(request["decision_packet"])
        contract = _contract_package(request["return_contract_package"], packet)
        bindings = _bindings(request["external_bindings"], contract)
        flow, mode = packet["selected_flow"], request["request_mode"]
        if packet["authority_owner"] != "BRAIN" or not (
            (flow in _AUDITED and mode in {"AUDIT_CONSTRUCT", "AUDIT_RECONCILE"}) or
            (flow == "DIAGNOSTIC" and mode == "DIRECT")
        ):
            raise BrainProviderProtocolError("flow and request mode do not apply")
        profile = _profile_package(request["audit_profile_package"], flow)
        if mode == "AUDIT_RECONCILE":
            _lineage(packet, contract, bindings, profile, request["stage1_lineage"])
        elif request["stage1_lineage"] is not None or mode == "DIRECT" and bindings:
            raise BrainProviderProtocolError("forbidden lineage or DIRECT bindings")
        supplied = _fingerprint(request["request_fingerprint"], "request fingerprint")
        if supplied != _digest({k: v for k, v in request.items() if k != "request_fingerprint"}):
            raise BrainProviderProtocolError("request fingerprint mismatch")
        return request
    except (BrainAuditError, BrainReturnContractError, KeyError, TypeError, RecursionError, UnicodeError) as exc:
        raise BrainProviderProtocolError("invalid serialized Brain request") from exc


def _diagnostic(candidate: Any) -> dict[str, Any]:
    body = _exact(candidate, {"proposal", "uncertainty"}, "diagnostic candidate")
    proposal = body["proposal"]
    if type(proposal) is not str or not proposal.strip() or len(proposal.encode("utf-8")) > 131072:
        raise BrainProviderProtocolError("invalid diagnostic proposal")
    uncertainty = _exact(body["uncertainty"], {"status", "summary"}, "diagnostic uncertainty")
    status, summary = uncertainty["status"], uncertainty["summary"]
    if not (status == "NONE" and summary is None or status == "MATERIAL" and
            type(summary) is str and summary.strip() and len(summary.encode("utf-8")) <= 4096):
        raise BrainProviderProtocolError("diagnostic status and summary mismatch")
    return body


def validate_response(request: Mapping[str, Any], semantic_response: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one provider semantic response; return a transient Brain decision."""
    try:
        req = revalidate_request(request)
        response = _normal(semantic_response, response=True)
        _bound(response, 655360, "provider semantic response")
        mode, packet = req["request_mode"], req["decision_packet"]
        expected = ({"request_fingerprint", "construct_audit", "reconciled_candidate", "closure", "outcome"}
                    if mode == "AUDIT_RECONCILE" else {"request_fingerprint", "candidate"})
        _exact(response, expected, "provider semantic response")
        if response["request_fingerprint"] != req["request_fingerprint"]:
            raise BrainProviderProtocolError("request echo mismatch")
        if mode == "DIRECT":
            semantic = _diagnostic(response["candidate"])
        elif mode == "AUDIT_CONSTRUCT":
            _enforce_bindings(response["candidate"], req["external_bindings"])
            semantic = construct_stage1(packet, req["audit_profile_package"]["profile"], response["candidate"])
        else:
            first = req["stage1_lineage"]["construct"]
            _enforce_bindings(response["reconciled_candidate"], req["external_bindings"])
            material = {
                "packet_fingerprint": first["packet_fingerprint"],
                "audit_profile_ref": first["audit_profile_ref"],
                "selected_flow": first["selected_flow"],
                "construct_candidate": first["construct_candidate"],
                "construct_fingerprint": first["construct_fingerprint"],
                **{key: response[key] for key in ("construct_audit", "reconciled_candidate", "closure", "outcome")},
            }
            semantic = validate_stage2(packet, req["audit_profile_package"]["profile"], first, material)
        return _decision(req, semantic)
    except (BrainAuditError, BrainReturnContractError, KeyError, TypeError, RecursionError, UnicodeError) as exc:
        raise BrainProviderProtocolError("invalid provider semantic response") from exc


def revalidate_decision(value: Mapping[str, Any] | None, request: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate a serialized decision against its exact request and mode."""
    try:
        req = revalidate_request(request)
        decision = _exact(_normal(value), _DECISION_FIELDS, "Brain decision")
        _bound(decision, 655360, "Brain decision")
        semantic = decision["semantic_value"]
        if type(semantic) is not dict:
            raise BrainProviderProtocolError("invalid decision semantic value")
        mode = req["request_mode"]
        if mode == "DIRECT":
            validated = _diagnostic(semantic)
        elif mode == "AUDIT_CONSTRUCT":
            validated = _stage1(req["decision_packet"], req["audit_profile_package"]["profile"], semantic)
            _enforce_bindings(validated["construct_candidate"], req["external_bindings"])
        else:
            first = req["stage1_lineage"]["construct"]
            fields = {"format", "version", "stage", "packet_fingerprint", "audit_profile_ref", "selected_flow",
                      "construct_fingerprint", "construct_audit", "closure", "outcome",
                      "reconciled_candidate_fingerprint", "handoff_candidate", "stage2_fingerprint"}
            _exact(semantic, fields, "Stage-2 semantic result")
            if (semantic["format"] != "AIOS_BRAIN_SEMANTIC_AUDIT" or type(semantic["version"]) is not int or
                semantic["version"] != 1 or semantic["stage"] != "ADVERSARIAL_AUDIT_AND_RECONCILE" or
                any(semantic[key] != first[key] for key in ("packet_fingerprint", "audit_profile_ref", "selected_flow", "construct_fingerprint"))):
                raise BrainProviderProtocolError("Stage-2 semantic lineage mismatch")
            for key in ("stage2_fingerprint", "reconciled_candidate_fingerprint"):
                _fingerprint(semantic[key], key)
            profile = req["audit_profile_package"]["profile"]
            _ordered_lenses(semantic["construct_audit"], profile, closure=False)
            closure = _ordered_lenses(semantic["closure"], profile, closure=True)
            expected_outcome = "NO_DECISION" if any(item["outcome"] == "BLOCKER" for item in closure) else "CANDIDATE"
            if semantic["outcome"] != expected_outcome:
                raise BrainProviderProtocolError("Stage-2 closure and outcome mismatch")
            if semantic["outcome"] == "CANDIDATE":
                _enforce_bindings(semantic["handoff_candidate"], req["external_bindings"])
                if _digest(semantic["handoff_candidate"]) != semantic["reconciled_candidate_fingerprint"]:
                    raise BrainProviderProtocolError("Stage-2 candidate fingerprint mismatch")
                material = {
                    "packet_fingerprint": first["packet_fingerprint"],
                    "audit_profile_ref": first["audit_profile_ref"],
                    "selected_flow": first["selected_flow"],
                    "construct_candidate": first["construct_candidate"],
                    "construct_fingerprint": first["construct_fingerprint"],
                    "construct_audit": semantic["construct_audit"],
                    "reconciled_candidate": semantic["handoff_candidate"],
                    "closure": semantic["closure"], "outcome": semantic["outcome"],
                }
                if validate_stage2(req["decision_packet"], profile, first, material) != semantic:
                    raise BrainProviderProtocolError("Stage-2 result does not match BP-4A semantics")
            elif semantic["outcome"] != "NO_DECISION" or semantic["handoff_candidate"] is not None:
                raise BrainProviderProtocolError("invalid Stage-2 handoff exposure")
            validated = semantic
        expected = _decision(req, validated)
        if decision != expected or decision["decision_fingerprint"] != _digest({k: v for k, v in decision.items() if k != "decision_fingerprint"}):
            raise BrainProviderProtocolError("Brain decision identity mismatch")
        return decision
    except (BrainAuditError, BrainReturnContractError, KeyError, TypeError, RecursionError, UnicodeError) as exc:
        raise BrainProviderProtocolError("invalid serialized Brain decision") from exc


__all__ = [
    "BrainProviderProtocolError", "construct_request", "revalidate_request",
    "validate_response", "revalidate_decision",
]
