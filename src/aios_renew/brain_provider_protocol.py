"""Pure, bounded BP5-P2B semantic request and decision envelopes.

All inputs are caller supplied. These functions neither invoke a provider nor
grant authority to write a canonical artifact.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Any, Callable, Mapping

from .brain_audit import (
    BrainAuditError, _CONFORMANCE_LENSES, _ROA_IDS, _MOA_ID, _MOA_FINAL_FIELDS,
    _TCA_IDS, _ADAPTIVE_ID, _TCA_FINAL_FIELDS, _tca_source_context, _tca_construct,
    _has_conformance, _ordered_lenses, _packet, _moa_construct, _moa_source_context,
    _stage1, construct_roa_subject, construct_stage1, construct_moa_subject, construct_moa,
    normalize_profile, normalize_audit_policy, profile_ref, validate_roa, validate_stage2, validate_moa,
    _ADAPTIVE_DIGEST,
    construct_tca_subject, construct_tca, validate_tca,
    _AOA_ID, _AOA_FINAL_FIELDS, _aoa_source_context, _aoa_construct, _aoa_private, _architecture_subject,
    construct_aoa_subject, construct_aoa, validate_aoa,
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
_TASK_AUDIT_MODES = frozenset({"TCA_CONSTRUCT", "TCA_RECONCILE", "V1_OPTION_CONSTRUCT", "V1_OPTION_RECONCILE"})
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
    "AIOS_BRAIN_SEMANTIC_AUDIT", "AIOS_ROA_SUBJECT", "AIOS_BRAIN_REPAIR_OUTCOME_AUDIT",
    "AIOS_BRAIN_AUDIT_POLICY", "AIOS_BRAIN_AUDIT_SELECTION",
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
        if value.get("format") == "AIOS_FAILURE_CONTEXT":
            from .review_transport import validate_failure_context, ReviewTransportError
            try:
                validate_failure_context(value)
            except (ValueError, TypeError, KeyError, ReviewTransportError) as exc:
                raise BrainProviderProtocolError("invalid bounded FAILURE context") from exc
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
    if flow not in profile["applicable_flows"] or _bytes(package["audit_profile_ref"]) != _bytes(profile_ref(profile)):
        raise BrainProviderProtocolError("audit-profile identity or applicability mismatch")
    return package


def _audit_selection(packet: dict, contract: dict, bindings: dict, profile: dict,
                     policy_value: Any, sources: dict, option: Any, support: Any) -> dict:
    """Bind one complete canonical policy; serialized bindings are not credentials."""
    policy = normalize_audit_policy(policy_value)
    flow = packet["selected_flow"]
    if (flow not in _AUDITED or packet["canonical_blocker"] is not None
            or type(packet["canonical_facts"]) is not dict
            or policy["main_sha"] != packet["canonical_facts"].get("main_sha")
            or policy["work_context_fingerprint"] != packet["work_context_fingerprint"]):
        raise BrainProviderProtocolError("prospective policy/flow/canonical currentness mismatch")
    v1_ref = {"id": _ADAPTIVE_ID, "version": 1, "digest": _ADAPTIVE_DIGEST}
    sources = deepcopy(sources)
    if policy["state"] == "FOUR_SPECIALIZED" or option is not None:
        if flow == "ARCHITECTURE":
            sources["architecture_source_context"] = _aoa_source_context(sources["architecture_source_context"])
        elif flow == "TASK_AUTHORING":
            sources["task_source_context"] = _tca_source_context(sources["task_source_context"])
        elif flow == "REMEDIATION_AUTHORING":
            sources["moa_source_context"] = _moa_source_context(sources["moa_source_context"])
    expected_ref = policy["profiles"][flow]
    if option is not None:
        if flow not in {"ARCHITECTURE", "TASK_AUTHORING"}:
            raise BrainProviderProtocolError("correction V1 cannot substitute for its normative procedure")
        expected_ref = v1_ref
        context = sources["architecture_source_context" if flow == "ARCHITECTURE" else "task_source_context"]
        option = _human_v1_option(packet, contract, bindings, profile, context, option)
    if _bytes(profile["audit_profile_ref"]) != _bytes(expected_ref):
        raise BrainProviderProtocolError("selected prospective profile is missing or mismatched; no fallback")
    if support is not None:
        if policy["state"] != "FOUR_SPECIALIZED" or flow not in {"REMEDIATION_AUTHORING", "REPAIR_AUTHORING"}:
            raise BrainProviderProtocolError("read-only V1 support requires normative MOA/ROA")
        expected_support = {
            "selected_by": "HUMAN", "option": "V1_READ_ONLY",
            "packet_fingerprint": packet["packet_fingerprint"],
            "work_context_fingerprint": packet["work_context_fingerprint"],
            "return_contract_ref": contract["return_contract_ref"], "external_bindings": bindings,
            "source_context_fingerprint": _digest(sources), "audit_profile_ref": v1_ref,
        }
        if _bytes(_normal(support)) != _bytes(expected_support):
            raise BrainProviderProtocolError("read-only Human V1 support binding mismatch")
        support = expected_support
    selection = {
        "format": "AIOS_BRAIN_AUDIT_SELECTION", "version": 1, "policy": policy,
        "selected_flow": flow, "packet_fingerprint": packet["packet_fingerprint"],
        "work_context_fingerprint": packet["work_context_fingerprint"],
        "canonical_facts_fingerprint": _digest(packet["canonical_facts"]),
        "subject_fingerprint": _digest(packet["subject"]),
        "return_contract_ref": contract["return_contract_ref"], "external_bindings": bindings,
        "audit_profile_ref": profile["audit_profile_ref"], "source_context_fingerprint": _digest(sources),
        "human_v1_option": option, "human_v1_deep_support": support,
    }
    _bound(selection, 16384, "prospective audit selection")
    return selection


def _selected_mode(selection: dict) -> str:
    """The existing selected_flow and exact policy determine one existing mode."""
    if selection["human_v1_option"] is not None:
        return "V1_OPTION_CONSTRUCT"
    if selection["policy"]["state"] == "ADAPTIVE_V1":
        return "AUDIT_CONSTRUCT"
    return {"ARCHITECTURE": "AOA_CONSTRUCT", "TASK_AUTHORING": "TCA_CONSTRUCT",
            "REMEDIATION_AUTHORING": "MOA_CONSTRUCT", "REPAIR_AUTHORING": "ROA_CONFORMANCE"}[
                selection["selected_flow"]]


def _policy_mode(selection: dict, mode: Any) -> str:
    first = _selected_mode(selection)
    second = {"AUDIT_CONSTRUCT": "AUDIT_RECONCILE", "AOA_CONSTRUCT": "AOA_RECONCILE",
              "TCA_CONSTRUCT": "TCA_RECONCILE", "MOA_CONSTRUCT": "MOA_RECONCILE",
              "V1_OPTION_CONSTRUCT": "V1_OPTION_RECONCILE"}.get(first)
    if mode == "PROSPECTIVE":
        return first
    if type(mode) is not str or mode not in {first, second}:
        raise BrainProviderProtocolError("prospective policy cannot mix procedures or stages")
    return mode


def _seal_request(result: dict, selection: dict | None = None) -> dict:
    if selection is not None:
        result["audit_selection"] = selection
    result["request_fingerprint"] = _digest(result)
    _bound(result, 393216, "Brain request")
    return result


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
                    profile: dict[str, Any], selection: dict | None = None) -> dict[str, Any]:
    result = {
        "format": "AIOS_BRAIN_REQUEST", "version": 1, "kind": "BRAIN_REQUEST",
        "request_mode": "AUDIT_CONSTRUCT", "decision_packet": packet,
        "return_contract_package": contract, "external_bindings": bindings,
        "audit_profile_package": profile, "stage1_lineage": None,
    }
    return _seal_request(result, selection)


def _lineage(packet: dict[str, Any], contract: dict[str, Any], bindings: dict[str, Any],
             profile: dict[str, Any], value: Any, selection: dict | None = None) -> dict[str, Any]:
    lineage = _exact(_normal(value), _STAGE1_LINEAGE_FIELDS, "Stage-1 lineage")
    first_request = _stage1_request(packet, contract, bindings, profile, selection)
    if lineage["stage1_request_fingerprint"] != first_request["request_fingerprint"]:
        raise BrainProviderProtocolError("Stage-1 request replay or substitution")
    construct = _stage1(packet, profile["profile"], lineage["construct"])
    _enforce_bindings(construct["construct_candidate"], bindings)
    first_decision = _decision(first_request, construct)
    if lineage["stage1_decision_fingerprint"] != first_decision["decision_fingerprint"]:
        raise BrainProviderProtocolError("Stage-1 decision lineage mismatch")
    return lineage


def _moa_request(packet: dict[str, Any], contract: dict[str, Any], bindings: dict[str, Any],
                 profile: dict[str, Any], sources: Any, mode: str,
                 lineage: dict[str, Any] | None = None, selection: dict | None = None) -> dict[str, Any]:
    context = _moa_source_context(sources)
    subject = construct_moa_subject(packet, profile["profile"], contract["return_contract_ref"], bindings, context)
    result = {
        "format": "AIOS_BRAIN_REQUEST", "version": 1, "kind": "BRAIN_REQUEST", "request_mode": mode,
        "decision_packet": packet, "return_contract_package": contract, "external_bindings": bindings,
        "audit_profile_package": profile, "stage1_lineage": lineage,
        "moa_subject": subject, "moa_source_context": context,
    }
    return _seal_request(result, selection)


def _moa_lineage(packet: dict[str, Any], contract: dict[str, Any], bindings: dict[str, Any],
                 profile: dict[str, Any], sources: Any, value: Any, selection: dict | None = None) -> dict[str, Any]:
    lineage = _exact(_normal(value), _STAGE1_LINEAGE_FIELDS, "MOA construct lineage")
    first_request = _moa_request(packet, contract, bindings, profile, sources, "MOA_CONSTRUCT", selection=selection)
    first = _moa_construct(packet, profile["profile"], first_request["moa_subject"], lineage["construct"], sources)
    if (lineage["stage1_request_fingerprint"] != first_request["request_fingerprint"]
            or lineage["stage1_decision_fingerprint"] != _decision(first_request, first)["decision_fingerprint"]):
        raise BrainProviderProtocolError("MOA first-pass request/decision lineage mismatch")
    return lineage


def _mode(packet: dict[str, Any], profile: dict[str, Any] | None, mode: Any) -> None:
    """Check existing historical/isolated or policy-bound procedure modes."""
    if type(mode) is not str or packet["authority_owner"] != "BRAIN":
        raise BrainProviderProtocolError("flow and request mode do not apply")
    flow = packet["selected_flow"]
    if profile is not None and profile["profile"]["id"] in _ROA_IDS:
        if flow != "REPAIR_AUTHORING" or mode != "ROA_CONFORMANCE":
            raise BrainProviderProtocolError("ROA requires its exact single procedure; no mixed stages")
    elif profile is not None and profile["profile"]["id"] == _MOA_ID:
        if flow != "REMEDIATION_AUTHORING" or mode not in {"MOA_CONSTRUCT", "MOA_RECONCILE"}:
            raise BrainProviderProtocolError("MOA requires its exact focused passes; no mixed stages")
    elif profile is not None and profile["profile"]["id"] in _TCA_IDS:
        if flow != "TASK_AUTHORING" or mode not in {"TCA_CONSTRUCT", "TCA_RECONCILE"}:
            raise BrainProviderProtocolError("TCA requires its exact focused passes; no mixed stages")
    elif profile is not None and profile["profile"]["id"] == _AOA_ID:
        if flow != "ARCHITECTURE" or mode not in {"AOA_CONSTRUCT", "AOA_RECONCILE"}:
            raise BrainProviderProtocolError("AOA requires its exact focused passes; no mixed stages")
    elif mode in {"V1_OPTION_CONSTRUCT", "V1_OPTION_RECONCILE"}:
        if flow not in {"TASK_AUTHORING", "ARCHITECTURE"} or profile is None or profile["profile"]["id"] != _ADAPTIVE_ID:
            raise BrainProviderProtocolError("Human V1-only option requires exact TASK_AUTHORING/ARCHITECTURE and Adaptive V1")
    elif not ((flow in _AUDITED and mode in {"AUDIT_CONSTRUCT", "AUDIT_RECONCILE"}) or
              (flow == "DIAGNOSTIC" and mode == "DIRECT")):
        raise BrainProviderProtocolError("flow and request mode do not apply")


def _human_v1_option(packet: dict, contract: dict, bindings: dict, profile: dict,
                     sources: dict, value: Any) -> dict[str, Any]:
    """Validate a caller-authenticated selection; never infer Human opt-in."""
    expected = {"selected_by": "HUMAN", "option": "V1_ONLY",
                "packet_fingerprint": packet["packet_fingerprint"],
                "work_context_fingerprint": packet["work_context_fingerprint"],
                "return_contract_ref": contract["return_contract_ref"], "external_bindings": bindings,
                "source_context_fingerprint": _digest(sources), "audit_profile_ref": profile["audit_profile_ref"]}
    selected = _exact(_normal(value), set(expected), "external Human V1-only selection")
    if _bytes(selected) != _bytes(expected):
        raise BrainProviderProtocolError("Human V1 option subject/profile/content binding mismatch")
    if packet["selected_flow"] == "ARCHITECTURE":
        # Source and selection authenticity is supplied by the external caller.
        # This is structural subject binding, never an AOA semantic invocation.
        bound = _architecture_subject(packet, contract["return_contract_ref"], bindings, sources, profile["audit_profile_ref"])
        if bound["blockers"]:
            raise BrainProviderProtocolError("architecture Human V1 option lacks authenticated intent/source/authority")
        return selected
    task = sources["task"]
    if (task is None or sources["authorization"] is None or not sources["consumers"]
            or any(task.get(k) != v for k, v in bindings.items())
            or not packet["human_input"] or packet["canonical_blocker"] is not None
            or packet["subject"] != {"kind": "TASK_AUTHORING"}):
        raise BrainProviderProtocolError("Human V1 option requires the exact supplied TASK and Human intent")
    return selected


def _task_request(packet: dict, contract: dict, bindings: dict, profile: dict, sources: Any,
                  option: Any, mode: str, lineage: dict | None = None,
                  selection: dict | None = None) -> dict[str, Any]:
    context = _tca_source_context(sources)
    result = {"format": "AIOS_BRAIN_REQUEST", "version": 1, "kind": "BRAIN_REQUEST", "request_mode": mode,
              "decision_packet": packet, "return_contract_package": contract, "external_bindings": bindings,
              "audit_profile_package": profile, "stage1_lineage": lineage, "task_source_context": context}
    if mode in {"TCA_CONSTRUCT", "TCA_RECONCILE"}:
        if option is not None:
            raise BrainProviderProtocolError("TCA cannot mix a Human V1-only selection or second normative audit")
        result["tca_subject"] = construct_tca_subject(packet, profile["profile"], contract["return_contract_ref"], bindings, context)
    else:
        result["human_v1_option"] = _human_v1_option(packet, contract, bindings, profile, context, option)
    return _seal_request(result, selection)


def _task_lineage(packet: dict, contract: dict, bindings: dict, profile: dict, sources: Any,
                  option: Any, mode: str, value: Any, selection: dict | None = None) -> dict[str, Any]:
    lineage = _exact(_normal(value), _STAGE1_LINEAGE_FIELDS, "TASK audit construct lineage")
    first_mode = "TCA_CONSTRUCT" if mode == "TCA_RECONCILE" else "V1_OPTION_CONSTRUCT"
    request = _task_request(packet, contract, bindings, profile, sources, option, first_mode, selection=selection)
    if first_mode == "TCA_CONSTRUCT":
        first = _tca_construct(packet, profile["profile"], request["tca_subject"], lineage["construct"], sources)
    else:
        first = _stage1(packet, profile["profile"], lineage["construct"])
        _enforce_bindings(first["construct_candidate"], bindings)
    if (lineage["stage1_request_fingerprint"] != request["request_fingerprint"]
            or lineage["stage1_decision_fingerprint"] != _decision(request, first)["decision_fingerprint"]):
        raise BrainProviderProtocolError("TASK audit first-pass subject/profile/option lineage mismatch")
    return lineage


def _architecture_request(packet: dict, contract: dict, bindings: dict, profile: dict, sources: Any,
                          option: Any, mode: str, lineage: dict | None = None,
                          selection: dict | None = None) -> dict[str, Any]:
    context = _aoa_source_context(sources)
    result = {"format": "AIOS_BRAIN_REQUEST", "version": 1, "kind": "BRAIN_REQUEST", "request_mode": mode,
              "decision_packet": packet, "return_contract_package": contract, "external_bindings": bindings,
              "audit_profile_package": profile, "stage1_lineage": lineage, "architecture_source_context": context}
    if mode in {"AOA_CONSTRUCT", "AOA_RECONCILE"}:
        if option is not None:
            raise BrainProviderProtocolError("AOA cannot mix a Human V1-only option or second normative audit")
        result["aoa_subject"] = construct_aoa_subject(packet, profile["profile"], contract["return_contract_ref"], bindings, context)
    else:
        result["human_v1_option"] = _human_v1_option(packet, contract, bindings, profile, context, option)
    return _seal_request(result, selection)


def _architecture_lineage(packet: dict, contract: dict, bindings: dict, profile: dict, sources: Any,
                          option: Any, mode: str, value: Any, selection: dict | None = None) -> dict[str, Any]:
    lineage = _exact(_normal(value), _STAGE1_LINEAGE_FIELDS, "architecture construct lineage")
    first_mode = "AOA_CONSTRUCT" if mode == "AOA_RECONCILE" else "V1_OPTION_CONSTRUCT"
    request = _architecture_request(packet, contract, bindings, profile, sources, option, first_mode, selection=selection)
    if first_mode == "AOA_CONSTRUCT":
        first = _aoa_construct(packet, profile["profile"], request["aoa_subject"], lineage["construct"], sources)
    else:
        first = _stage1(packet, profile["profile"], lineage["construct"])
        _aoa_private(first["construct_candidate"])
    if (lineage["stage1_request_fingerprint"] != request["request_fingerprint"]
            or lineage["stage1_decision_fingerprint"] != _decision(request, first)["decision_fingerprint"]):
        raise BrainProviderProtocolError("architecture first-pass source/profile/option lineage mismatch")
    return lineage


def construct_request(packet: DecisionPacket, return_contract_package: Mapping[str, Any],
                      external_bindings: Mapping[str, Any], audit_profile_package: Mapping[str, Any] | None = None,
                      *, request_mode: str, stage1_decision: Mapping[str, Any] | None = None,
                      moa_source_context: Mapping[str, Any] | None = None,
                      task_source_context: Mapping[str, Any] | None = None,
                      architecture_source_context: Mapping[str, Any] | None = None,
                      human_v1_option: Mapping[str, Any] | None = None,
                      audit_policy: Mapping[str, Any] | None = None,
                      selection_authenticator: Callable[[Mapping[str, Any], str], bool] | None = None,
                      selection_phase: str = "ADMIT",
                      human_v1_deep_support: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Construct an exact historical/isolated or externally authenticated request.

    PROSPECTIVE requires a complete policy and an out-of-band trusted caller
    authenticator. Ordinary first-party attempts supply both through
    brain_context.canonical_audit_inputs; this pure constructor does no Git reads.
    Serialized policy labels/hashes cannot supply that authority.
    """
    if not isinstance(packet, DecisionPacket):
        raise BrainProviderProtocolError("constructor requires a fresh DecisionPacket object")
    try:
        body = _packet(packet)
        _normal(body)  # Validate portable diagnostics without changing packet identity.
        contract = _contract_package(return_contract_package, body)
        bindings = _bindings(external_bindings, contract)
        flow = body["selected_flow"]
        profile = _profile_package(audit_profile_package, flow)
        selection = None
        if audit_policy is not None:
            sources = _normal({"moa_source_context": moa_source_context, "task_source_context": task_source_context,
                               "architecture_source_context": architecture_source_context})
            selection = _audit_selection(body, contract, bindings, profile, audit_policy, sources,
                                         human_v1_option, human_v1_deep_support)
            request_mode = _policy_mode(selection, request_mode)
            if selection_phase not in {"ADMIT", "CONTINUE"} or not callable(selection_authenticator):
                raise BrainProviderProtocolError("external canonical policy authenticator required")
            try:
                authenticated = selection_authenticator(deepcopy(selection), selection_phase) is True
            except Exception:
                authenticated = False
            if not authenticated:
                raise BrainProviderProtocolError("canonical policy/subject authority is absent, stale or ambiguous")
        elif (request_mode == "PROSPECTIVE" or selection_authenticator is not None
              or human_v1_deep_support is not None or selection_phase != "ADMIT"):
            raise BrainProviderProtocolError("complete prospective canonical policy required; no fallback")
        _mode(body, profile, request_mode)
        architecture = request_mode in {"AOA_CONSTRUCT", "AOA_RECONCILE"} or (
            flow == "ARCHITECTURE" and request_mode in {"V1_OPTION_CONSTRUCT", "V1_OPTION_RECONCILE"})
        if architecture:
            if moa_source_context is not None or task_source_context is not None:
                raise BrainProviderProtocolError("architecture cannot mix correction or TASK sources")
            first_mode = "AOA_CONSTRUCT" if request_mode.startswith("AOA_") else "V1_OPTION_CONSTRUCT"
            first_request = _architecture_request(body, contract, bindings, profile, architecture_source_context, human_v1_option,
                                                   first_mode, selection=selection)
            if request_mode == first_mode:
                if stage1_decision is not None:
                    raise BrainProviderProtocolError("architecture construct cannot accept another normative audit")
                return first_request
            first = revalidate_decision(stage1_decision, first_request)
            lineage = {"stage1_request_fingerprint": first_request["request_fingerprint"],
                       "stage1_decision_fingerprint": first["decision_fingerprint"], "construct": first["semantic_value"]}
            return _architecture_request(body, contract, bindings, profile, architecture_source_context, human_v1_option,
                                          request_mode, lineage, selection)
        if architecture_source_context is not None:
            raise BrainProviderProtocolError("architecture sources require AOA or the bound architecture V1 option")
        if request_mode in _TASK_AUDIT_MODES:
            if moa_source_context is not None:
                raise BrainProviderProtocolError("TASK audits cannot mix MOA sources")
            first_mode = "TCA_CONSTRUCT" if request_mode.startswith("TCA_") else "V1_OPTION_CONSTRUCT"
            first_request = _task_request(body, contract, bindings, profile, task_source_context, human_v1_option,
                                          first_mode, selection=selection)
            if request_mode == first_mode:
                if stage1_decision is not None:
                    raise BrainProviderProtocolError("TASK construct cannot accept a prior or additional normative audit")
                return first_request
            first = revalidate_decision(stage1_decision, first_request)
            lineage = {"stage1_request_fingerprint": first_request["request_fingerprint"],
                       "stage1_decision_fingerprint": first["decision_fingerprint"], "construct": first["semantic_value"]}
            return _task_request(body, contract, bindings, profile, task_source_context, human_v1_option,
                                 request_mode, lineage, selection)
        if task_source_context is not None or human_v1_option is not None:
            raise BrainProviderProtocolError("TASK sources/Human option require their exact bound procedure")
        if request_mode in {"MOA_CONSTRUCT", "MOA_RECONCILE"}:
            first_request = _moa_request(body, contract, bindings, profile, moa_source_context, "MOA_CONSTRUCT", selection=selection)
            if request_mode == "MOA_CONSTRUCT":
                if stage1_decision is not None:
                    raise BrainProviderProtocolError("MOA construct cannot accept an earlier or dual audit")
                return first_request
            first = revalidate_decision(stage1_decision, first_request)
            lineage = {"stage1_request_fingerprint": first_request["request_fingerprint"],
                       "stage1_decision_fingerprint": first["decision_fingerprint"], "construct": first["semantic_value"]}
            return _moa_request(body, contract, bindings, profile, moa_source_context, request_mode, lineage, selection)
        if moa_source_context is not None:
            raise BrainProviderProtocolError("MOA source context requires its exact focused procedure")
        if request_mode == "DIRECT":
            if bindings or stage1_decision is not None:
                raise BrainProviderProtocolError("DIRECT has no bindings or lineage")
            lineage = None
        elif request_mode == "AUDIT_CONSTRUCT":
            if stage1_decision is not None:
                raise BrainProviderProtocolError("Stage 1 has no prior decision")
            return _stage1_request(body, contract, bindings, profile, selection)
        elif request_mode == "ROA_CONFORMANCE":
            if stage1_decision is not None:
                raise BrainProviderProtocolError("ROA has no Stage-1 lineage or dual normative audit")
            lineage = None
        else:
            first_request = _stage1_request(body, contract, bindings, profile, selection)
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
        if request_mode == "ROA_CONFORMANCE":
            result["roa_subject"] = construct_roa_subject(body, profile["profile"],
                                                        contract["return_contract_ref"], bindings)
        return _seal_request(result, selection)
    except (BrainAuditError, BrainReturnContractError, KeyError, TypeError, RecursionError, UnicodeError) as exc:
        raise BrainProviderProtocolError("invalid caller-supplied Brain request material") from exc


def revalidate_request(value: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate a serialized request, including its complete packet mapping."""
    try:
        request = _normal(value)
        roa = type(request) is dict and request.get("request_mode") == "ROA_CONFORMANCE"
        moa = type(request) is dict and request.get("request_mode") in ("MOA_CONSTRUCT", "MOA_RECONCILE")
        tca = type(request) is dict and request.get("request_mode") in ("TCA_CONSTRUCT", "TCA_RECONCILE")
        option = type(request) is dict and request.get("request_mode") in ("V1_OPTION_CONSTRUCT", "V1_OPTION_RECONCILE")
        aoa = type(request) is dict and request.get("request_mode") in ("AOA_CONSTRUCT", "AOA_RECONCILE")
        architecture_option = (option and type(request.get("decision_packet")) is dict
                               and request["decision_packet"].get("selected_flow") == "ARCHITECTURE")
        prospective = type(request) is dict and "audit_selection" in request
        request = _exact(request, _REQUEST_FIELDS | ({"roa_subject"} if roa else set()) |
                          ({"moa_subject", "moa_source_context"} if moa else set()) |
                          ({"task_source_context", "tca_subject"} if tca else set()) |
                          ({"task_source_context", "human_v1_option"} if option and not architecture_option else set()) |
                          ({"architecture_source_context", "aoa_subject"} if aoa else set()) |
                          ({"architecture_source_context", "human_v1_option"} if architecture_option else set()) |
                          ({"audit_selection"} if prospective else set()), "Brain request")
        if request["format"] != "AIOS_BRAIN_REQUEST" or type(request["version"]) is not int or request["version"] != 1 or request["kind"] != "BRAIN_REQUEST":
            raise BrainProviderProtocolError("invalid request format")
        _bound(request, 393216, "Brain request")
        packet = _packet(request["decision_packet"])
        contract = _contract_package(request["return_contract_package"], packet)
        bindings = _bindings(request["external_bindings"], contract)
        flow, mode = packet["selected_flow"], request["request_mode"]
        profile = _profile_package(request["audit_profile_package"], flow)
        selection = None
        if prospective:
            supplied_selection = request["audit_selection"]
            sources = {key: request.get(key) for key in (
                "moa_source_context", "task_source_context", "architecture_source_context")}
            selection = _audit_selection(packet, contract, bindings, profile, supplied_selection["policy"], sources,
                                         request.get("human_v1_option"), supplied_selection["human_v1_deep_support"])
            if _bytes(selection) != _bytes(supplied_selection) or _policy_mode(selection, mode) != mode:
                raise BrainProviderProtocolError("prospective selection binding mismatch")
        _mode(packet, profile, mode)
        if mode == "AOA_RECONCILE" or architecture_option and mode == "V1_OPTION_RECONCILE":
            _architecture_lineage(packet, contract, bindings, profile, request["architecture_source_context"],
                                  request.get("human_v1_option"), mode, request["stage1_lineage"], selection)
        elif mode in {"TCA_RECONCILE", "V1_OPTION_RECONCILE"}:
            _task_lineage(packet, contract, bindings, profile, request["task_source_context"],
                          request.get("human_v1_option"), mode, request["stage1_lineage"], selection)
        elif mode == "MOA_RECONCILE":
            _moa_lineage(packet, contract, bindings, profile, request["moa_source_context"], request["stage1_lineage"], selection)
        elif mode == "AUDIT_RECONCILE":
            _lineage(packet, contract, bindings, profile, request["stage1_lineage"], selection)
        elif request["stage1_lineage"] is not None or mode == "DIRECT" and bindings:
            raise BrainProviderProtocolError("forbidden lineage or DIRECT bindings")
        if roa:
            expected_subject = construct_roa_subject(packet, profile["profile"],
                                                      contract["return_contract_ref"], bindings)
            if _bytes(request["roa_subject"]) != _bytes(expected_subject):
                raise BrainProviderProtocolError("ROA packet/return/subject binding mismatch")
        if moa:
            expected_subject = construct_moa_subject(packet, profile["profile"], contract["return_contract_ref"],
                                                      bindings, request["moa_source_context"])
            if _bytes(request["moa_subject"]) != _bytes(expected_subject):
                raise BrainProviderProtocolError("MOA packet/return/source/subject binding mismatch")
        if aoa or architecture_option:
            expected = _architecture_request(packet, contract, bindings, profile, request["architecture_source_context"],
                                             request.get("human_v1_option"), mode, request["stage1_lineage"], selection)
            if _bytes(expected) != _bytes(request):
                raise BrainProviderProtocolError("architecture packet/return/source/subject/option binding mismatch")
        if tca or option and not architecture_option:
            expected = _task_request(packet, contract, bindings, profile, request["task_source_context"],
                                     request.get("human_v1_option"), mode, request["stage1_lineage"], selection)
            if _bytes(expected) != _bytes(request):
                raise BrainProviderProtocolError("TASK audit packet/return/source/subject/option binding mismatch")
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


def _stage2_conformance_fields(request: Mapping[str, Any]) -> frozenset[str]:
    """Carry V3 protections for exact V3 and adaptive V1 TASK identities."""
    if (request["decision_packet"]["selected_flow"] == "TASK_AUTHORING"
            and _has_conformance(request["audit_profile_package"]["profile"])):
        return frozenset(_CONFORMANCE_LENSES)
    return frozenset()


def validate_response(request: Mapping[str, Any], semantic_response: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one provider semantic response; return a transient Brain decision."""
    try:
        req = revalidate_request(request)
        response = _normal(semantic_response, response=True)
        _bound(response, 655360, "provider semantic response")
        mode, packet = req["request_mode"], req["decision_packet"]
        mode = {"V1_OPTION_CONSTRUCT": "AUDIT_CONSTRUCT", "V1_OPTION_RECONCILE": "AUDIT_RECONCILE"}.get(mode, mode)
        expected = ({"request_fingerprint", "subject_fingerprint", "checks", "alternatives", "information", "draft_candidate"}
                    if mode == "AOA_CONSTRUCT" else
                    {"request_fingerprint", "subject_fingerprint", "construct_fingerprint", "challenges", "alternatives",
                     "information", "reconciliation", "closure", "reconciled_candidate"}
                    if mode == "AOA_RECONCILE" else
                    {"request_fingerprint", "subject_fingerprint", "checks", "acceptance_checks", "consumer_checks"}
                    if mode == "TCA_CONSTRUCT" else
                    {"request_fingerprint", "subject_fingerprint", "construct_fingerprint", "challenges", "acceptance_checks",
                     "consumer_checks", "acceptance_accounting", "reconciliation", "closure", "reconciled_candidate"}
                    if mode == "TCA_RECONCILE" else
                    {"request_fingerprint", "subject_fingerprint", "checks", "draft_candidate"}
                    if mode == "MOA_CONSTRUCT" else
                    {"request_fingerprint", "subject_fingerprint", "construct_fingerprint", "challenges", "closure", "reconciled_candidate"}
                    if mode == "MOA_RECONCILE" else
                    {"request_fingerprint", "subject_fingerprint", "checks", "closure", "candidate"}
                    if mode == "ROA_CONFORMANCE" else
                    {"request_fingerprint", "construct_audit", "reconciled_candidate", "closure", "outcome"}
                    if mode == "AUDIT_RECONCILE" else {"request_fingerprint", "candidate"})
        if mode == "AUDIT_RECONCILE" and packet["selected_flow"] == "TASK_AUTHORING":
            expected = expected | {"acceptance_phase_ledger"} | _stage2_conformance_fields(req)
        _exact(response, expected, "provider semantic response")
        if response["request_fingerprint"] != req["request_fingerprint"]:
            raise BrainProviderProtocolError("request echo mismatch")
        if "human_v1_option" in req and packet["selected_flow"] == "ARCHITECTURE":
            _aoa_private(response["candidate"] if mode == "AUDIT_CONSTRUCT" else response["reconciled_candidate"])
        if mode == "DIRECT":
            semantic = _diagnostic(response["candidate"])
        elif mode in {"AOA_CONSTRUCT", "AOA_RECONCILE"}:
            material = {k: v for k, v in response.items() if k != "request_fingerprint"}
            args = (packet, req["audit_profile_package"]["profile"], req["aoa_subject"])
            if mode == "AOA_CONSTRUCT":
                semantic = construct_aoa(*args, material, req["architecture_source_context"])
            else:
                semantic = validate_aoa(*args, req["stage1_lineage"]["construct"], material, req["architecture_source_context"])
        elif mode in {"TCA_CONSTRUCT", "TCA_RECONCILE"}:
            material = {k: v for k, v in response.items() if k != "request_fingerprint"}
            args = (packet, req["audit_profile_package"]["profile"], req["tca_subject"])
            if mode == "TCA_CONSTRUCT":
                semantic = construct_tca(*args, material, req["task_source_context"])
            else:
                semantic = validate_tca(*args, req["stage1_lineage"]["construct"], material, req["task_source_context"])
        elif mode in {"MOA_CONSTRUCT", "MOA_RECONCILE"}:
            material = {k: v for k, v in response.items() if k != "request_fingerprint"}
            args = (packet, req["audit_profile_package"]["profile"], req["moa_subject"])
            if mode == "MOA_CONSTRUCT":
                semantic = construct_moa(*args, material, req["moa_source_context"])
            else:
                semantic = validate_moa(*args, req["stage1_lineage"]["construct"], material, req["moa_source_context"])
        elif mode == "ROA_CONFORMANCE":
            semantic = validate_roa(packet, req["audit_profile_package"]["profile"], req["roa_subject"],
                                    {k: v for k, v in response.items() if k != "request_fingerprint"})
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
            if "acceptance_phase_ledger" in response:
                material["acceptance_phase_ledger"] = response["acceptance_phase_ledger"]
            material.update({key: response[key] for key in _stage2_conformance_fields(req)})
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
        mode = {"V1_OPTION_CONSTRUCT": "AUDIT_CONSTRUCT", "V1_OPTION_RECONCILE": "AUDIT_RECONCILE"}.get(mode, mode)
        if mode == "DIRECT":
            validated = _diagnostic(semantic)
        elif mode == "AOA_CONSTRUCT":
            validated = _aoa_construct(req["decision_packet"], req["audit_profile_package"]["profile"],
                                       req["aoa_subject"], semantic, req["architecture_source_context"])
        elif mode == "AOA_RECONCILE":
            _exact(semantic, _AOA_FINAL_FIELDS, "AOA final semantic result")
            material = {k: semantic[k] for k in ("subject_fingerprint", "construct_fingerprint", "challenges",
                                                "alternatives", "information", "reconciliation", "closure")}
            material["reconciled_candidate"] = semantic["handoff_candidate"]
            validated = validate_aoa(req["decision_packet"], req["audit_profile_package"]["profile"], req["aoa_subject"],
                                      req["stage1_lineage"]["construct"], material, req["architecture_source_context"])
            if _bytes(validated) != _bytes(semantic):
                raise BrainProviderProtocolError("AOA result does not match its exact two-pass closure")
        elif mode == "TCA_CONSTRUCT":
            validated = _tca_construct(req["decision_packet"], req["audit_profile_package"]["profile"],
                                       req["tca_subject"], semantic, req["task_source_context"])
        elif mode == "TCA_RECONCILE":
            _exact(semantic, _TCA_FINAL_FIELDS, "TCA final semantic result")
            material = {k: semantic[k] for k in ("subject_fingerprint", "construct_fingerprint", "challenges",
                                                "acceptance_checks", "consumer_checks", "acceptance_accounting",
                                                "reconciliation", "closure")}
            material["reconciled_candidate"] = semantic["handoff_candidate"]
            validated = validate_tca(req["decision_packet"], req["audit_profile_package"]["profile"], req["tca_subject"],
                                      req["stage1_lineage"]["construct"], material, req["task_source_context"])
            if _bytes(validated) != _bytes(semantic):
                raise BrainProviderProtocolError("TCA result does not match its exact two-pass closure")
        elif mode == "MOA_CONSTRUCT":
            validated = _moa_construct(req["decision_packet"], req["audit_profile_package"]["profile"],
                                       req["moa_subject"], semantic, req["moa_source_context"])
        elif mode == "MOA_RECONCILE":
            _exact(semantic, _MOA_FINAL_FIELDS, "MOA final semantic result")
            material = {k: semantic[k] for k in ("subject_fingerprint", "construct_fingerprint", "challenges", "closure")}
            material["reconciled_candidate"] = semantic["handoff_candidate"]
            validated = validate_moa(req["decision_packet"], req["audit_profile_package"]["profile"], req["moa_subject"],
                                      req["stage1_lineage"]["construct"], material, req["moa_source_context"])
            if _bytes(validated) != _bytes(semantic):
                raise BrainProviderProtocolError("MOA result does not match its two focused passes")
        elif mode == "ROA_CONFORMANCE":
            _exact(semantic, {"format", "version", "procedure", "packet_fingerprint", "audit_profile_ref",
                              "selected_flow", "subject_fingerprint", "checks", "closure", "handoff_candidate",
                              "audit_fingerprint"}, "ROA semantic result")
            material = {k: semantic[k] for k in ("subject_fingerprint", "checks", "closure")}
            material["candidate"] = semantic["handoff_candidate"]
            validated = validate_roa(req["decision_packet"], req["audit_profile_package"]["profile"],
                                     req["roa_subject"], material)
            if _bytes(validated) != _bytes(semantic):
                raise BrainProviderProtocolError("ROA result does not match its bound procedure")
        elif mode == "AUDIT_CONSTRUCT":
            validated = _stage1(req["decision_packet"], req["audit_profile_package"]["profile"], semantic)
            _enforce_bindings(validated["construct_candidate"], req["external_bindings"])
            if "human_v1_option" in req and req["decision_packet"]["selected_flow"] == "ARCHITECTURE":
                _aoa_private(validated["construct_candidate"])
        else:
            first = req["stage1_lineage"]["construct"]
            fields = {"format", "version", "stage", "packet_fingerprint", "audit_profile_ref", "selected_flow",
                      "construct_fingerprint", "construct_audit", "closure", "outcome",
                      "reconciled_candidate_fingerprint", "handoff_candidate", "stage2_fingerprint"}
            if req["decision_packet"]["selected_flow"] == "TASK_AUTHORING":
                fields = fields | {"acceptance_phase_ledger"} | _stage2_conformance_fields(req)
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
                if "human_v1_option" in req and req["decision_packet"]["selected_flow"] == "ARCHITECTURE":
                    _aoa_private(semantic["handoff_candidate"])
                if _digest(semantic["handoff_candidate"]) != semantic["reconciled_candidate_fingerprint"]:
                    raise BrainProviderProtocolError("Stage-2 candidate fingerprint mismatch")
            elif semantic["outcome"] != "NO_DECISION" or semantic["handoff_candidate"] is not None:
                raise BrainProviderProtocolError("invalid Stage-2 handoff exposure")
            material = {
                "packet_fingerprint": first["packet_fingerprint"],
                "audit_profile_ref": first["audit_profile_ref"],
                "selected_flow": first["selected_flow"],
                "construct_candidate": first["construct_candidate"],
                "construct_fingerprint": first["construct_fingerprint"],
                "construct_audit": semantic["construct_audit"],
                # NO_DECISION hides the reconciled candidate. Only the original
                # construct can be recovered from the serialized request; BP-4A
                # rejects it if the audit claims a different reconciliation.
                "reconciled_candidate": (semantic["handoff_candidate"] if semantic["outcome"] == "CANDIDATE"
                                         else first["construct_candidate"]),
                "closure": semantic["closure"], "outcome": semantic["outcome"],
            }
            if "acceptance_phase_ledger" in semantic:
                material["acceptance_phase_ledger"] = semantic["acceptance_phase_ledger"]
            material.update({key: semantic[key] for key in _stage2_conformance_fields(req)})
            if validate_stage2(req["decision_packet"], profile, first, material) != semantic:
                raise BrainProviderProtocolError("Stage-2 result does not match BP-4A semantics")
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
