"""Pure BP-4A profile identity and two-stage structural conformance.

All packet, profile and candidate material is supplied by the caller. Audit
outcomes are transient Brain claims, never canonical findings or evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

import yaml

from .decision_packet import DecisionPacket


class BrainAuditError(ValueError):
    """A supplied BP-4A semantic envelope is malformed or inconsistent."""


_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_SYMBOL = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_DRIVE_PATH = re.compile(r"(?:^|[\s\"'(])(?:[A-Za-z]:[/\\]|\\\\|/home/|/Users/|/tmp/|/var/)")
_PRIVATE_PARTS = (
    "chat", "conversation", "chain_of_thought", "cot", "reasoning_trace",
    "raw_log", "raw_evidence", "evidence_path", "log_path", "credential",
    "secret", "password", "api_key", "access_token", "auth_token",
    "workspace", "local_root", "repository_root", "remote_url", "hostname",
    "host_name", "timestamp", "created_at", "updated_at", "datetime",
    "uuid", "random", "nonce", "provider", "model", "session", "prompt",
)
_PRIVATE_KEYS = frozenset({
    "root", "remote", "host", "machine", "time", "date", "clock", "seed",
    "llm", "api_token", "bearer_token", "request_id", "host_id", "machine_id",
    "audit", "audit_profile", "risk_ledger", "coverage_ledger",
})
_ENVELOPE_KEYS = frozenset({
    "audit_profile_ref", "packet_fingerprint", "construct_fingerprint",
    "reconciled_candidate_fingerprint", "stage2_fingerprint", "construct_audit",
    "closure", "handoff_candidate", "stage1", "stage2", "audit_envelope",
})
_PRIVATE_COMPACT_KEYS = frozenset(key.replace("_", "") for key in _ENVELOPE_KEYS | _PRIVATE_KEYS)
_PROFILE_FIELDS = frozenset({"id", "version", "applicable_flows", "lenses", "procedure", "bounds"})
_BOUND_FIELDS = frozenset({
    "candidate_bytes", "risks_per_lens", "risk_summary_bytes",
    "counterexample_bytes", "candidate_anchor_bytes", "dismissal_basis_bytes",
    "closure_blocker_summary_bytes", "stage2_bytes", "max_depth",
})
_PROCEDURE_FIELDS = frozenset({
    "stages", "construct_outcomes", "risk_dispositions", "closure_outcomes",
    "final_outcomes", "closure_on_reconciled_candidate",
})
_PACKET_FIELDS = frozenset({
    "format", "version", "kind", "work_context_fingerprint", "selected_flow",
    "selection_basis", "authority_owner", "decision_family_ref", "handoff_target",
    "expected_return_shape", "pending_canonical_obligation",
    "pending_canonical_authority_owner", "requires_fresh_context_for_continuation",
    "canonical_facts", "canonical_blocker", "bounded_observations",
    "executor_claims", "prior_semantic_decisions", "human_input", "subject",
    "run_created", "executor_invoked", "verification_invoked", "state_mutated",
    "packet_fingerprint",
})
_STAGE1_FIELDS = frozenset({
    "format", "version", "stage", "packet_fingerprint", "audit_profile_ref",
    "selected_flow", "expected_return_shape", "construct_candidate",
    "construct_fingerprint",
})
_STAGE2_FIELDS = frozenset({
    "packet_fingerprint", "audit_profile_ref", "selected_flow",
    "construct_candidate", "construct_fingerprint", "construct_audit",
    "reconciled_candidate", "closure", "outcome",
})


def _fields(value: Any, expected: frozenset[str] | set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise BrainAuditError(f"{name} fields do not match the closed contract")
    return value


def _normal(value: Any, *, depth: int, max_depth: int, private: bool = False) -> Any:
    if depth > max_depth:
        raise BrainAuditError("semantic material exceeds structural depth")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise BrainAuditError("non-finite JSON number")
        return value
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise BrainAuditError("invalid Unicode") from exc
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        if private and _DRIVE_PATH.search(normalized):
            raise BrainAuditError("machine-local path in semantic material")
        return normalized
    if type(value) is list:
        return [_normal(v, depth=depth + 1, max_depth=max_depth, private=private) for v in value]
    if type(value) is dict:
        if private and value.get("format") in (
            "AIOS_DECISION_PACKET", "AIOS_BRAIN_AUDIT_PROFILES", "AIOS_BRAIN_SEMANTIC_AUDIT"
        ):
            raise BrainAuditError("nested protocol envelope")
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise BrainAuditError("semantic mapping keys must be text")
            normalized_key = _normal(key, depth=depth + 1, max_depth=max_depth)
            if normalized_key in result:
                raise BrainAuditError("duplicate normalized mapping key")
            if private:
                folded = normalized_key.lower().replace("-", "_")
                compact = folded.replace("_", "")
                if (folded in _ENVELOPE_KEYS or folded in _PRIVATE_KEYS
                        or compact in _PRIVATE_COMPACT_KEYS
                        or any(part in folded or part.replace("_", "") in compact
                               for part in _PRIVATE_PARTS)):
                    raise BrainAuditError("private metadata or nested audit envelope")
            result[normalized_key] = _normal(item, depth=depth + 1, max_depth=max_depth, private=private)
        return result
    raise BrainAuditError("semantic material must be strict JSON")


def _json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BrainAuditError("semantic material must be strict UTF-8 JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _bounded(value: Any, limit: int, name: str) -> None:
    if len(_json(value)) > limit:
        raise BrainAuditError(f"{name} exceeds {limit} UTF-8 bytes")


def _text(value: Any, limit: int, name: str) -> str:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > limit:
        raise BrainAuditError(f"{name} must be non-empty bounded text")
    return value


def _sha(value: Any, name: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise BrainAuditError(f"{name} is not a SHA-256 digest")
    return value


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if type(key) is not str or key in result:
            raise BrainAuditError("duplicate or non-text YAML mapping key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _profile(value: Any) -> dict[str, Any]:
    profile = _fields(_normal(value, depth=0, max_depth=32), _PROFILE_FIELDS, "profile")
    if (type(profile["id"]) is not str or _ID.fullmatch(profile["id"]) is None
            or profile["id"] != "brain-high-value-v1"
            or type(profile["version"]) is not int or profile["version"] != 1):
        raise BrainAuditError("invalid v1 profile identity")
    for name in ("applicable_flows", "lenses"):
        entries = profile[name]
        if type(entries) is not list or not entries or len(entries) > 32 or any(
            type(v) is not str or _SYMBOL.fullmatch(v) is None for v in entries
        ) or len(set(entries)) != len(entries):
            raise BrainAuditError(f"invalid or duplicate {name}")
    procedure = _fields(profile["procedure"], _PROCEDURE_FIELDS, "procedure")
    for name, expected in (
        ("stages", ["CONSTRUCT", "ADVERSARIAL_AUDIT_AND_RECONCILE"]),
        ("construct_outcomes", ["CLEAR", "RISK_FOUND"]),
        ("risk_dispositions", ["ADDRESSED_BY_RECONCILIATION", "DISMISSED_WITH_BOUNDED_BASIS"]),
        ("closure_outcomes", ["CLEAR", "BLOCKER"]),
        ("final_outcomes", ["CANDIDATE", "NO_DECISION"]),
    ):
        if procedure[name] != expected:
            raise BrainAuditError(f"invalid v1 {name} procedure")
    if procedure["closure_on_reconciled_candidate"] is not True:
        raise BrainAuditError("closure must cover the reconciled candidate")
    bounds = _fields(profile["bounds"], _BOUND_FIELDS, "bounds")
    # Ceiling guards prevent caller-supplied policy from widening the bounded
    # protocol. The repository registry supplies the actual v1 policy values.
    ceilings = {
        "candidate_bytes": 131072, "risks_per_lens": 4,
        "risk_summary_bytes": 4096, "counterexample_bytes": 8192,
        "candidate_anchor_bytes": 1024, "dismissal_basis_bytes": 4096,
        "closure_blocker_summary_bytes": 4096, "stage2_bytes": 524288,
        "max_depth": 32,
    }
    if any(type(bounds[k]) is not int or not 1 <= bounds[k] <= cap for k, cap in ceilings.items()):
        raise BrainAuditError("profile bounds exceed the v1 safety envelope")
    _bounded(profile, 32768, "profile material")
    return profile


def parse_profile_registry(raw: bytes | str) -> dict[str, Any]:
    """Parse caller-supplied YAML; never opens a repository path."""
    if type(raw) is str:
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise BrainAuditError("invalid registry Unicode") from exc
    elif type(raw) is bytes:
        encoded = raw
    else:
        raise BrainAuditError("registry must be UTF-8 YAML")
    if len(encoded) > 32768:
        raise BrainAuditError("registry exceeds 32768 UTF-8 bytes")
    try:
        loaded = yaml.load(encoded.decode("utf-8", errors="strict"), Loader=_UniqueLoader)
    except (UnicodeError, yaml.YAMLError, ValueError, TypeError) as exc:
        raise BrainAuditError("invalid registry YAML") from exc
    registry = _fields(loaded, {"format", "version", "profiles"}, "registry")
    if registry["format"] != "AIOS_BRAIN_AUDIT_PROFILES" or type(registry["version"]) is not int or registry["version"] != 1:
        raise BrainAuditError("unknown audit-profile registry")
    profiles = registry["profiles"]
    if type(profiles) is not list or len(profiles) != 1:
        raise BrainAuditError("v1 registry requires exactly one profile")
    profile = _profile(profiles[0])
    return {"format": registry["format"], "version": 1, "profiles": [profile]}


def normalize_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate supplied profile body and return normalized portable material."""
    return _profile(value)


def profile_ref(profile: Mapping[str, Any]) -> dict[str, Any]:
    body = _profile(profile)
    return {"id": body["id"], "version": body["version"], "digest": _digest(body)}


def _packet(value: DecisionPacket | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, DecisionPacket):
        value = value.as_dict()
    body = _fields(_normal(value, depth=0, max_depth=32), _PACKET_FIELDS, "Decision Packet")
    if (body["format"] != "AIOS_DECISION_PACKET" or type(body["version"]) is not int
            or body["version"] != 1 or body["kind"] != "DECISION_PACKET"):
        raise BrainAuditError("not a Decision Packet v1")
    if type(body["authority_owner"]) is not str or not body["authority_owner"] or any(body[k] is not False for k in
        ("run_created", "executor_invoked", "verification_invoked", "state_mutated")):
        raise BrainAuditError("packet authority or side-effect contract mismatch")
    if type(body["selected_flow"]) is not str or type(body["expected_return_shape"]) is not str or not body["expected_return_shape"]:
        raise BrainAuditError("packet flow or expected return shape is invalid")
    supplied = _sha(body["packet_fingerprint"], "packet fingerprint")
    if supplied != _digest({k: v for k, v in body.items() if k != "packet_fingerprint"}):
        raise BrainAuditError("Decision Packet fingerprint mismatch")
    _bounded(body, 131072, "Decision Packet")
    return body


def profile_applies(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    """Applicability is exact membership in the supplied profile's flow list."""
    body = _packet(packet)
    policy = _profile(profile)
    return body["selected_flow"] in policy["applicable_flows"]


def _candidate(value: Any, policy: dict[str, Any]) -> dict[str, Any]:
    candidate = _normal(value, depth=0, max_depth=policy["bounds"]["max_depth"], private=True)
    if type(candidate) is not dict:
        raise BrainAuditError("candidate must be a strict-JSON mapping")
    _bounded(candidate, policy["bounds"]["candidate_bytes"], "candidate")
    return candidate


def construct_stage1(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                     construct_candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Bind one normalized construct to one exact packet and profile."""
    body, policy = _packet(packet), _profile(profile)
    if body["authority_owner"] != "BRAIN" or body["selected_flow"] not in policy["applicable_flows"]:
        raise BrainAuditError("profile does not apply to selected flow")
    candidate = _candidate(construct_candidate, policy)
    result = {
        "format": "AIOS_BRAIN_SEMANTIC_AUDIT", "version": 1, "stage": "CONSTRUCT",
        "packet_fingerprint": body["packet_fingerprint"],
        "audit_profile_ref": profile_ref(policy),
        "selected_flow": body["selected_flow"],
        "expected_return_shape": body["expected_return_shape"],
        "construct_candidate": candidate,
    }
    result["construct_fingerprint"] = _digest(result)
    return result


def _stage1(packet: dict[str, Any], profile: dict[str, Any], value: Any) -> dict[str, Any]:
    supplied = _fields(_normal(value, depth=0, max_depth=profile["bounds"]["max_depth"]),
                       _STAGE1_FIELDS, "Stage 1")
    _sha(supplied["construct_fingerprint"], "construct fingerprint")
    expected = construct_stage1(packet, profile, supplied["construct_candidate"])
    if _json(supplied) != _json(expected):
        raise BrainAuditError("Stage-1 packet, profile or construct lineage mismatch")
    return expected


def _ordered_lenses(value: Any, policy: dict[str, Any], *, closure: bool) -> list[dict[str, Any]]:
    lenses = policy["lenses"]
    if type(value) is not list or len(value) != len(lenses):
        raise BrainAuditError("incomplete lens coverage")
    bounds = policy["bounds"]
    result = []
    for expected_lens, raw in zip(lenses, value):
        if type(raw) is not dict or raw.get("lens") != expected_lens:
            raise BrainAuditError("unknown, duplicate or out-of-order lens")
        outcome = raw.get("outcome")
        if closure:
            if outcome == "CLEAR":
                _fields(raw, {"lens", "outcome"}, "CLEAR closure")
            elif outcome == "BLOCKER":
                _fields(raw, {"lens", "outcome", "blocker_summary"}, "BLOCKER closure")
                _text(raw["blocker_summary"], bounds["closure_blocker_summary_bytes"], "blocker summary")
            else:
                raise BrainAuditError("unknown closure outcome")
        elif outcome == "CLEAR":
            _fields(raw, {"lens", "outcome"}, "CLEAR audit")
        elif outcome == "RISK_FOUND":
            _fields(raw, {"lens", "outcome", "risks"}, "RISK_FOUND audit")
            risks = raw["risks"]
            if type(risks) is not list or not 1 <= len(risks) <= bounds["risks_per_lens"]:
                raise BrainAuditError("risk count exceeds profile bound")
            for risk in risks:
                if type(risk) is not dict:
                    raise BrainAuditError("invalid risk")
                disposition = risk.get("disposition")
                required = {"risk_summary", "counterexample", "candidate_anchor", "disposition"}
                if disposition == "DISMISSED_WITH_BOUNDED_BASIS":
                    _fields(risk, required | {"dismissal_basis"}, "dismissed risk")
                    _text(risk["dismissal_basis"], bounds["dismissal_basis_bytes"], "dismissal basis")
                elif disposition == "ADDRESSED_BY_RECONCILIATION":
                    _fields(risk, required, "addressed risk")
                else:
                    raise BrainAuditError("unknown risk disposition")
                for field in ("risk_summary", "counterexample", "candidate_anchor"):
                    _text(risk[field], bounds[field + "_bytes"], field)
        else:
            raise BrainAuditError("unknown construct-audit outcome")
        result.append(raw)
    return result


def validate_stage2(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                    stage1: Mapping[str, Any], semantic_output: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one audit, reconciliation and final closure within Stage 2."""
    body, policy = _packet(packet), _profile(profile)
    if body["authority_owner"] != "BRAIN" or body["selected_flow"] not in policy["applicable_flows"]:
        raise BrainAuditError("profile does not apply to selected flow")
    first = _stage1(body, policy, stage1)
    material = _fields(_normal(semantic_output, depth=0, max_depth=policy["bounds"]["max_depth"]),
                       _STAGE2_FIELDS, "Stage-2 semantic output")
    _bounded(material, policy["bounds"]["stage2_bytes"], "Stage-2 semantic material")
    for field in ("packet_fingerprint", "audit_profile_ref", "selected_flow", "construct_candidate", "construct_fingerprint"):
        if _json(material[field]) != _json(first[field]):
            raise BrainAuditError(f"Stage-2 {field} lineage mismatch")
    _normal(material["construct_audit"], depth=0, max_depth=policy["bounds"]["max_depth"], private=True)
    _normal(material["closure"], depth=0, max_depth=policy["bounds"]["max_depth"], private=True)
    ledger = _ordered_lenses(material["construct_audit"], policy, closure=False)
    reconciled = _candidate(material["reconciled_candidate"], policy)
    addressed = any(risk["disposition"] == "ADDRESSED_BY_RECONCILIATION"
                    for lens in ledger for risk in lens.get("risks", []))
    if (_json(reconciled) != _json(first["construct_candidate"])) != addressed:
        raise BrainAuditError("reconciliation contradicts addressed-risk claims")
    closure = _ordered_lenses(material["closure"], policy, closure=True)
    outcome = "NO_DECISION" if any(item["outcome"] == "BLOCKER" for item in closure) else "CANDIDATE"
    if material["outcome"] != outcome:
        raise BrainAuditError("outcome contradicts closure")
    final_fingerprint = _digest(reconciled)
    result = {
        "format": "AIOS_BRAIN_SEMANTIC_AUDIT", "version": 1,
        "stage": "ADVERSARIAL_AUDIT_AND_RECONCILE",
        "packet_fingerprint": first["packet_fingerprint"],
        "audit_profile_ref": first["audit_profile_ref"],
        "selected_flow": first["selected_flow"],
        "construct_fingerprint": first["construct_fingerprint"],
        "construct_audit": ledger, "closure": closure, "outcome": outcome,
        "reconciled_candidate_fingerprint": final_fingerprint,
        "handoff_candidate": reconciled if outcome == "CANDIDATE" else None,
    }
    result["stage2_fingerprint"] = _digest({
        "stage1_construct_fingerprint": first["construct_fingerprint"],
        "semantic_material": material,
        "reconciled_candidate_fingerprint": final_fingerprint,
    })
    return result


__all__ = [
    "BrainAuditError", "parse_profile_registry", "normalize_profile", "profile_ref",
    "profile_applies", "construct_stage1", "validate_stage2",
]
