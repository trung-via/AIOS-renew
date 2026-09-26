"""Pure BP6-P2A Reviewer procedure registry and mode selection.

All policy material is supplied by the caller. This module performs no
repository discovery and does not create or validate REVIEW artifacts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import yaml


class ReviewerProcedureError(ValueError):
    """The supplied Reviewer procedure material violates the v1 contract."""


_BOUNDS = {
    "raw_registry_bytes": 65536,
    "selected_profile_bytes": 32768,
    "procedure_step_bytes": 4096,
    "steps_per_mode": 8,
    "max_depth": 32,
}
_REGISTRY_FIELDS = frozenset({"format", "version", "profiles", "bounds"})
_PROFILE_FIELDS = frozenset({
    "id", "version", "selected_flow", "authority_owner",
    "decision_family_ref", "handoff_target", "expected_return_shape",
    "one_call_semantics", "semantic_verdict_boundary", "invocation_boundary",
    "modes",
})
_METADATA = {
    "selected_flow": "SEMANTIC_REVIEW",
    "authority_owner": "REVIEWER",
    "decision_family_ref": "review.validate_review",
    "handoff_target": "AUTHORING_INGRESS",
    "expected_return_shape": "REVIEW_CONTRACT_PROPOSAL",
}
_STEP_IDS = {
    "PRIMARY": (
        "CONTRACT_COVERAGE", "SEMANTIC_DELTA_INSPECTION",
        "CLAIM_EVIDENCE_CROSSCHECK", "MATERIAL_DEFECT_SWEEP",
        "VERDICT_CLOSURE",
    ),
    "DELTA": (
        "PRIOR_FINDING_RESOLUTION", "LATEST_CORRECTION_INSPECTION",
        "PRIOR_CONCLUSION_INVALIDATION", "VERDICT_CLOSURE",
    ),
}


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ReviewerProcedureError("material must be strict UTF-8 JSON") from exc


def _normalize(value: Any, depth: int = 0) -> Any:
    if depth > _BOUNDS["max_depth"]:
        raise ReviewerProcedureError("registry exceeds structural depth")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        raise ReviewerProcedureError("non-JSON or unsupported numeric value")
    if type(value) is str:
        try:
            value.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise ReviewerProcedureError("invalid Unicode") from exc
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if type(value) is list:
        return [_normalize(item, depth + 1) for item in value]
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ReviewerProcedureError("mapping keys must be text")
            normalized_key = _normalize(key, depth + 1)
            if normalized_key in result:
                raise ReviewerProcedureError("duplicate normalized mapping key")
            result[normalized_key] = _normalize(item, depth + 1)
        return result
    raise ReviewerProcedureError("material must use JSON semantic values")


def _fields(value: Any, expected: frozenset[str] | set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise ReviewerProcedureError(f"{name} fields violate the closed v1 contract")
    return value


def _text(value: Any, name: str) -> None:
    if (type(value) is not str or not value.strip()
            or len(value.encode("utf-8")) > _BOUNDS["procedure_step_bytes"]):
        raise ReviewerProcedureError(f"{name} must be non-empty bounded UTF-8 text")


class _RegistryLoader(yaml.SafeLoader):
    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent: Any, index: Any) -> yaml.Node:
        if self.check_event(yaml.AliasEvent):
            raise ReviewerProcedureError("YAML aliases are forbidden")
        if self._depth > _BOUNDS["max_depth"]:
            raise ReviewerProcedureError("registry exceeds structural depth")
        self._depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _unique_mapping(loader: _RegistryLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if type(key) is not str or key in result:
            raise ReviewerProcedureError("duplicate or non-text YAML mapping key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_RegistryLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def normalize_profile_registry(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a caller-supplied decoded registry and return detached material."""
    registry = _fields(_normalize(value), _REGISTRY_FIELDS, "registry")
    if (registry["format"] != "AIOS_REVIEWER_PROCEDURE_PROFILES"
            or type(registry["version"]) is not int or registry["version"] != 1):
        raise ReviewerProcedureError("unknown Reviewer procedure registry")
    bounds = _fields(registry["bounds"], set(_BOUNDS), "bounds")
    if any(type(bounds[key]) is not int or bounds[key] != expected
           for key, expected in _BOUNDS.items()):
        raise ReviewerProcedureError("effective v1 bounds were substituted")
    profiles = registry["profiles"]
    if type(profiles) is not list or len(profiles) != 1:
        raise ReviewerProcedureError("v1 requires exactly one profile")
    profile = _fields(profiles[0], _PROFILE_FIELDS, "profile")
    if (profile["id"] != "reviewer-semantic-v1"
            or type(profile["version"]) is not int or profile["version"] != 1
            or any(profile[key] != expected for key, expected in _METADATA.items())
            or profile["one_call_semantics"] is not True):
        raise ReviewerProcedureError("profile identity, metadata or one-call rule mismatch")
    _text(profile["semantic_verdict_boundary"], "semantic verdict boundary")
    _text(profile["invocation_boundary"], "invocation boundary")
    modes = profile["modes"]
    if type(modes) is not list or len(modes) != 2:
        raise ReviewerProcedureError("exactly two ordered modes are required")
    for mode, expected_mode in zip(modes, _STEP_IDS):
        _fields(mode, {"mode", "steps"}, "mode")
        if type(mode["mode"]) is not str or mode["mode"] != expected_mode:
            raise ReviewerProcedureError("unknown or reordered mode")
        steps = mode["steps"]
        expected_steps = _STEP_IDS[expected_mode]
        if (type(steps) is not list or len(steps) != len(expected_steps)
                or len(steps) > bounds["steps_per_mode"]):
            raise ReviewerProcedureError("incorrect mode steps")
        for step, expected_id in zip(steps, expected_steps):
            _fields(step, {"id", "check"}, "step")
            if type(step["id"]) is not str or step["id"] != expected_id:
                raise ReviewerProcedureError("unknown, duplicate or reordered step")
            _text(step["check"], "step check")
    if len(_json_bytes(profile)) > bounds["selected_profile_bytes"]:
        raise ReviewerProcedureError("selected profile exceeds byte bound")
    return registry


def parse_profile_registry(raw: bytes | str) -> dict[str, Any]:
    """Parse caller-supplied UTF-8 YAML without opening any path."""
    if type(raw) is str:
        try:
            encoded = raw.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise ReviewerProcedureError("invalid registry Unicode") from exc
    elif type(raw) is bytes:
        encoded = raw
    else:
        raise ReviewerProcedureError("registry must be UTF-8 YAML")
    if len(encoded) > _BOUNDS["raw_registry_bytes"]:
        raise ReviewerProcedureError("registry exceeds raw byte bound")
    try:
        loaded = yaml.load(encoded.decode("utf-8", "strict"), Loader=_RegistryLoader)
    except (UnicodeError, yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise ReviewerProcedureError("invalid registry YAML") from exc
    return normalize_profile_registry(loaded)


def select_reviewer_procedure(
    registry: bytes | str | Mapping[str, Any], review_mode: str,
) -> dict[str, Any]:
    """Select an already-authoritative PRIMARY or DELTA mode from a valid v1 registry."""
    if type(review_mode) is not str or review_mode not in _STEP_IDS:
        raise ReviewerProcedureError("review_mode must be PRIMARY or DELTA")
    normalized = (parse_profile_registry(registry) if type(registry) in (bytes, str)
                  else normalize_profile_registry(registry))
    profile = normalized["profiles"][0]
    bounds = normalized["bounds"]
    digest = hashlib.sha256(_json_bytes({"profile": profile, "bounds": bounds})).hexdigest()
    return {
        "profile": profile,
        "procedure": profile["modes"][0 if review_mode == "PRIMARY" else 1],
        "bounds": bounds,
        "reviewer_procedure_ref": {
            "id": profile["id"], "version": profile["version"], "digest": digest,
        },
    }


__all__ = [
    "ReviewerProcedureError", "normalize_profile_registry",
    "parse_profile_registry", "select_reviewer_procedure",
]
