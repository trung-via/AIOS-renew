"""Pure, bounded projection of repository-owned Brain candidate guidance.

The caller supplies the registry and exact Decision Packet. This module does
not validate candidate artifacts, discover files, or perform lifecycle work.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

import yaml

from .brain_audit import BrainAuditError, _packet


class BrainReturnContractError(ValueError):
    """Return-contract material or its Decision Packet binding is invalid."""


_FLOWS = (
    "ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING",
    "REPAIR_AUTHORING", "DIAGNOSTIC",
)
_BOUNDS = {
    "raw_registry_bytes": 65536,
    "selected_contract_bytes": 16384,
    "shape_bytes": 8192,
    "requirements_count": 16,
    "requirement_bytes": 2048,
    "bindings_count": 16,
    "binding_path_bytes": 256,
    "binding_instruction_bytes": 2048,
    "max_depth": 32,
}
_METADATA = (
    "selected_flow", "authority_owner", "decision_family_ref",
    "handoff_target", "expected_return_shape",
)
_CONTRACT_FIELDS = set(_METADATA) | {"version", "candidate_contract"}
_CANDIDATE_FIELDS = {"representation", "bindings", "shape", "requirements"}
_BINDING_FIELDS = {"path", "source", "instruction"}
_SOURCES = {"DECISION_PACKET", "EXTERNAL_REQUEST_BINDING_REQUIRED"}
_OWNERS = {"BRAIN"}
_FAMILIES = {
    "HUMAN_BRAIN_ARCHITECTURE_PLANNING", "task.validate_task+authoring_ingress",
    "review.validate_remediation", "publication._validate_repair_authorization",
    "HUMAN_BRAIN_DIAGNOSTIC",
}
_HANDOFFS = {"HUMAN_BRAIN_PLANNING", "AUTHORING_INGRESS"}
_RETURN_SHAPES = {
    "BOUNDED_SEMANTIC_PROPOSAL", "TASK_AUTHORING_PROPOSAL",
    "REMEDIATION_CONTRACT_PROPOSAL", "REPAIR_AUTHORIZATION_PROPOSAL",
    "BOUNDED_DIAGNOSTIC_PROPOSAL",
}
_PATH = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*\Z")


class _UniqueKeyLoader(yaml.SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> yaml.Node:
        # Alias expansion can turn a bounded YAML input into unbounded material.
        if self.check_event(yaml.AliasEvent):
            raise BrainReturnContractError("YAML aliases are outside the v1 grammar")
        return super().compose_node(parent, index)


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if type(key) is not str or key in result:
            raise BrainReturnContractError("duplicate or non-text YAML mapping key")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _normalize(value: Any, depth: int = 1) -> Any:
    if depth > _BOUNDS["max_depth"]:
        raise BrainReturnContractError("return-contract structural depth exceeded")
    if type(value) is str:
        value.encode("utf-8", errors="strict")
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise BrainReturnContractError("non-text mapping key")
        return {key: _normalize(item, depth + 1) for key, item in value.items()}
    if type(value) is list:
        return [_normalize(item, depth + 1) for item in value]
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        # Strict JSON allows finite numbers, though the v1 registry grammar
        # currently has no floating-point fields.
        if value != value or value in (float("inf"), float("-inf")):
            raise BrainReturnContractError("non-finite JSON number")
        return value
    raise BrainReturnContractError("non-JSON semantic value")


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BrainReturnContractError("strict UTF-8 JSON required") from exc


def _exact_mapping(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise BrainReturnContractError(f"invalid {name} fields")
    return value


def _text(value: Any, name: str, limit: int) -> str:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > limit:
        raise BrainReturnContractError(f"invalid or over-bound {name}")
    return value


def normalize_return_contract_registry(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one caller-supplied decoded v1 registry."""
    try:
        data = _normalize(value)
        root = _exact_mapping(data, {"format", "version", "bounds", "contracts"}, "registry")
        if root["format"] != "AIOS_BRAIN_RETURN_CONTRACTS" or type(root["version"]) is not int or root["version"] != 1:
            raise BrainReturnContractError("unknown return-contract registry format or version")
        if _exact_mapping(root["bounds"], set(_BOUNDS), "bounds") != _BOUNDS:
            raise BrainReturnContractError("v1 effective bounds mismatch")
        contracts = root["contracts"]
        if type(contracts) is not list or len(contracts) != len(_FLOWS):
            raise BrainReturnContractError("wrong number of Brain contracts")
        for index, contract in enumerate(contracts):
            item = _exact_mapping(contract, _CONTRACT_FIELDS, "contract")
            if item["selected_flow"] != _FLOWS[index] or type(item["version"]) is not int or item["version"] != 1:
                raise BrainReturnContractError("unknown, duplicate, or reordered flow")
            for field, allowed in (
                ("authority_owner", _OWNERS), ("decision_family_ref", _FAMILIES),
                ("handoff_target", _HANDOFFS), ("expected_return_shape", _RETURN_SHAPES),
            ):
                if type(item[field]) is not str or item[field] not in allowed:
                    raise BrainReturnContractError(f"unknown {field} token")
            candidate = _exact_mapping(item["candidate_contract"], _CANDIDATE_FIELDS, "candidate contract")
            if candidate["representation"] != "STRICT_JSON_MAPPING":
                raise BrainReturnContractError("unknown candidate representation")
            _text(candidate["shape"], "candidate shape", _BOUNDS["shape_bytes"])
            requirements = candidate["requirements"]
            if type(requirements) is not list or not 1 <= len(requirements) <= _BOUNDS["requirements_count"]:
                raise BrainReturnContractError("invalid requirement count")
            for requirement in requirements:
                _text(requirement, "requirement", _BOUNDS["requirement_bytes"])
            bindings = candidate["bindings"]
            if type(bindings) is not list or len(bindings) > _BOUNDS["bindings_count"]:
                raise BrainReturnContractError("invalid binding count")
            paths: set[str] = set()
            for binding in bindings:
                record = _exact_mapping(binding, _BINDING_FIELDS, "binding")
                path = _text(record["path"], "binding path", _BOUNDS["binding_path_bytes"])
                if _PATH.fullmatch(path) is None or path in paths:
                    raise BrainReturnContractError("invalid or duplicate binding path")
                paths.add(path)
                if type(record["source"]) is not str or record["source"] not in _SOURCES:
                    raise BrainReturnContractError("unknown binding source")
                _text(record["instruction"], "binding instruction", _BOUNDS["binding_instruction_bytes"])
            selected_bytes = _json_bytes({"bounds": root["bounds"], "contract": item})
            if len(selected_bytes) > _BOUNDS["selected_contract_bytes"]:
                raise BrainReturnContractError("normalized selected contract exceeds bound")
        if len(_json_bytes(root)) > _BOUNDS["raw_registry_bytes"]:
            raise BrainReturnContractError("normalized registry exceeds bound")
        return root
    except UnicodeError as exc:
        raise BrainReturnContractError("invalid Unicode in return contract") from exc


def parse_return_contract_registry(raw: bytes | str) -> dict[str, Any]:
    """Parse caller-supplied UTF-8 YAML, with no filesystem access."""
    try:
        if type(raw) is bytes:
            source = raw.decode("utf-8", errors="strict")
        elif type(raw) is str:
            source = raw
        else:
            raise BrainReturnContractError("registry must be UTF-8 bytes or text")
        if len(source.encode("utf-8", errors="strict")) > _BOUNDS["raw_registry_bytes"]:
            raise BrainReturnContractError("raw registry exceeds bound")
        return normalize_return_contract_registry(yaml.load(source, Loader=_UniqueKeyLoader))
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise BrainReturnContractError("invalid return-contract YAML or Unicode") from exc


def return_contract_ref(contract: Mapping[str, Any], bounds: Mapping[str, Any]) -> dict[str, Any]:
    """Content address complete normalized provider guidance and effective bounds."""
    if type(contract) is not dict or type(bounds) is not dict:
        raise BrainReturnContractError("normalized contract and bounds required")
    body = {"bounds": bounds, "contract": contract}
    return {"selected_flow": contract["selected_flow"], "version": contract["version"],
            "digest": hashlib.sha256(_json_bytes(body)).hexdigest()}


def select_return_contract(registry: Mapping[str, Any], packet: Any) -> dict[str, Any]:
    """Select one contract only when its metadata exactly matches the packet."""
    root = normalize_return_contract_registry(registry)
    try:
        # Reuse the existing closed v1 packet and fingerprint check before
        # binding any packet metadata to the selected provider contract.
        packet_data = _packet(packet)
        flow = packet_data.get("selected_flow")
        if flow not in _FLOWS:
            raise BrainReturnContractError("flow has no Brain return contract")
        contract = root["contracts"][_FLOWS.index(flow)]
        if any(packet_data.get(field) != contract[field] for field in _METADATA):
            raise BrainReturnContractError("Decision Packet metadata substitution")
        # Fresh JSON copies prevent callers from mutating validated material
        # through the returned projection.
        return {"contract": json.loads(_json_bytes(contract)),
                "bounds": dict(root["bounds"]),
                "return_contract_ref": return_contract_ref(contract, root["bounds"])}
    except (BrainAuditError, UnicodeError, RecursionError) as exc:
        raise BrainReturnContractError("invalid Decision Packet material") from exc


__all__ = [
    "BrainReturnContractError", "normalize_return_contract_registry",
    "parse_return_contract_registry", "select_return_contract", "return_contract_ref",
]
