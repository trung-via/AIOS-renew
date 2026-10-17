"""Pure historical BP-4A and bounded ROA conformance over portable profiles.

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
_DRIVE_PATH = re.compile(r"(?:^|[\s\"'(])(?:[A-Za-z]:[/\\]|\\\\|/home/|/Users/|/tmp/|/var/)")
_PRIVATE_KEYS = frozenset({
    "chat", "chat_history", "conversation", "conversation_history",
    "chain_of_thought", "cot", "reasoning_trace", "prompt", "prompt_history",
    "raw_log", "raw_logs", "raw_evidence", "raw_evidence_path",
    "evidence_path", "evidence_paths", "log_path", "log_paths",
    "credential", "credentials", "secret", "secrets", "password",
    "api_key", "access_token", "auth_token", "api_token", "bearer_token",
    "workspace", "workspace_root", "local_root", "repository_root",
    "remote", "remote_url", "root", "host", "hostname", "host_name",
    "host_id", "machine", "machine_id", "time", "date", "clock",
    "timestamp", "created_at", "updated_at", "datetime", "uuid",
    "random", "random_id", "nonce", "seed", "request_id",
    "provider", "provider_id", "provider_name", "provider_identity",
    "model", "model_id", "model_name", "model_identity",
    "session", "session_id", "session_name", "session_identity", "llm",
    "audit", "audit_profile", "risk_ledger", "coverage_ledger",
})
_ENVELOPE_KEYS = frozenset({
    "audit_profile_ref", "packet_fingerprint", "construct_fingerprint",
    "reconciled_candidate_fingerprint", "stage2_fingerprint", "construct_audit",
    "closure", "handoff_candidate", "stage1", "stage2", "audit_envelope",
    "acceptance_phase_ledger",
})
_RESERVED_SEMANTIC_KEYS = _ENVELOPE_KEYS | _PRIVATE_KEYS
_RESERVED_COMPACT_KEYS = frozenset(key.replace("_", "") for key in _RESERVED_SEMANTIC_KEYS)
_PROFILE_FIELDS = frozenset({"id", "version", "applicable_flows", "lenses", "procedure", "bounds"})
_LENS_FIELDS = frozenset({"id", "check"})
_LENS_IDS = (
    "AUTHORITY_BOUNDARY", "SCOPE_NON_GOALS", "PROVENANCE_LINEAGE",
    "FAILURE_MODE_COUNTEREXAMPLES", "AC_CONSISTENCY_COMPLETENESS",
    "VERIFICATION_OWNERSHIP_ORDERING", "PORTABILITY_PRIVACY_BOUNDEDNESS",
    "SIMPLIFICATION_DUPLICATE_AUTHORITY",
)
_BOUND_FIELDS = frozenset({
    "candidate_bytes", "risks_per_lens", "risk_summary_bytes",
    "counterexample_bytes", "candidate_anchor_bytes", "dismissal_basis_bytes",
    "closure_blocker_summary_bytes", "stage2_bytes", "max_depth", "lens_check_bytes",
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
_CONFORMANCE_LENSES = {
    "cross_authority_context": "AUTHORITY_BOUNDARY",
    "canonical_shape": "FAILURE_MODE_COUNTEREXAMPLES",
    "terminal_lifecycle": "AC_CONSISTENCY_COMPLETENESS",
}
_SHAPE_IDS = ("PRIMARY_RESULT", "FAILURE", "REVIEWED_RESULT", "REMEDIATION_RESULT", "REPAIR_RESULT")
_PATH_IDS = (
    "PRIMARY_PASS_TO_PUBLICATION",
    "CHANGES_REQUIRED_REMEDIATION_PASS_TO_PUBLICATION",
    "FAILURE_REPAIR_PASS_TO_PUBLICATION",
)
_CONFORMANCE_BOUNDS = {"sections_bytes": 65536, "entries_per_section": 16, "text_bytes": 2048}
_V3_RESERVED_KEYS = frozenset(_CONFORMANCE_LENSES) | frozenset(
    key.replace("_", "") for key in _CONFORMANCE_LENSES)
_ADAPTIVE_ID = "aios-adaptive-brain-audit-v1"
# Identity allowlist only: procedural meanings live in the portable registry.
# V1 is one exact supported revision; renaming old material or supplying only
# part of the adaptive procedure must not remint it as V1 proof.
_ADAPTIVE_DIGEST = "982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718"
_ROA_ID = "roa-1-r2"
_ROA_DIGEST = "aa6a5b60997849d6a00f9e4174ca7ef20fbc62ed64da645ee938a94f8e2639e4"
_PROFILE_VERSIONS = {"brain-high-value-v2": 2, "brain-high-value-v3": 3, _ADAPTIVE_ID: 1, _ROA_ID: 2}
_ROA_CHECK_IDS = (
    "EXACT_FAILURE_BOUNDARY", "EVIDENCE_DISCRIMINATING_CAUSE", "MATERIAL_REPAIR_LEVER",
    "FALSIFYING_CHALLENGE", "LAWFUL_READINESS",
)
_ROA_BOUNDS = {
    "check_bytes": 2048, "candidate_bytes": 131072, "basis_bytes": 4096,
    "evidence_refs_per_check": 4, "closure_summary_bytes": 4096,
    "outcome_bytes": 196608, "max_depth": 32,
}
_ROA_PROCEDURE = {
    "name": "REPAIR_OUTCOME_AUDIT", "check_outcomes": ["CLEAR", "BLOCKER"],
    "knowledge_states": ["OBSERVED", "INFERRED", "UNKNOWN"],
    "final_outcomes": ["CANDIDATE", "NO_DECISION"], "final_closure": True,
}
_ADAPTIVE_RESERVED_KEYS = frozenset({
    "selected_focus", "focus_selector", "focus_notes", "audit_profile_package",
    "return_contract_package", "stage1_lineage",
})
_ADAPTIVE_RESERVED_COMPACT = frozenset(key.replace("_", "") for key in _ADAPTIVE_RESERVED_KEYS)
_ADAPTIVE_ENVELOPES = (
    "AIOS_BRAIN_REQUEST", "AIOS_BRAIN_DECISION", "AIOS_BRAIN_AUDIT_PROFILE",
    "AIOS_BRAIN_AUDIT_PROFILE_PACKAGE", "AIOS_AUDITED_AUTHORING_HANDOFF",
    "AIOS_ADAPTIVE_BRAIN_AUDIT_V1",
)


def _has_conformance(policy: Mapping[str, Any]) -> bool:
    """Capabilities follow exact identity, never numeric successor ordering."""
    return policy["id"] in {"brain-high-value-v3", _ADAPTIVE_ID}


def _conformance_policy() -> dict[str, Any]:
    """Closed structural policy, without semantic applicability decisions."""
    return {
        "flow": "TASK_AUTHORING", "sections": list(_CONFORMANCE_LENSES),
        "shape_ids": list(_SHAPE_IDS), "path_ids": list(_PATH_IDS),
        "bounds": dict(_CONFORMANCE_BOUNDS),
    }


def _fields(value: Any, expected: frozenset[str] | set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise BrainAuditError(f"{name} fields do not match the closed contract")
    return value


def _normal(value: Any, *, depth: int, max_depth: int, private: bool = False,
            v3: bool = False, adaptive: bool = False) -> Any:
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
        return [_normal(v, depth=depth + 1, max_depth=max_depth, private=private,
                        v3=v3, adaptive=adaptive) for v in value]
    if type(value) is dict:
        if private and adaptive and "format" in value and (
                type(value["format"]) is not str or value["format"] in _ADAPTIVE_ENVELOPES):
            raise BrainAuditError("malformed or nested protocol envelope")
        if private and value.get("format") in (
            "AIOS_DECISION_PACKET", "AIOS_BRAIN_AUDIT_PROFILES", "AIOS_BRAIN_SEMANTIC_AUDIT"
        ):
            raise BrainAuditError("nested protocol envelope")
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise BrainAuditError("semantic mapping keys must be text")
            normalized_key = _normal(key, depth=depth + 1, max_depth=max_depth,
                                     private=private and adaptive)
            if normalized_key in result:
                raise BrainAuditError("duplicate normalized mapping key")
            if private:
                folded = normalized_key.lower().replace("-", "_")
                compact = folded.replace("_", "")
                if (folded in _RESERVED_SEMANTIC_KEYS or compact in _RESERVED_COMPACT_KEYS
                        or v3 and (folded in _V3_RESERVED_KEYS or compact in _V3_RESERVED_KEYS)
                        or adaptive and (folded in _ADAPTIVE_RESERVED_KEYS
                                         or compact in _ADAPTIVE_RESERVED_COMPACT)):
                    raise BrainAuditError("private metadata or nested audit envelope")
            result[normalized_key] = _normal(item, depth=depth + 1, max_depth=max_depth,
                                           private=private, v3=v3, adaptive=adaptive)
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
    normalized = _normal(value, depth=0, max_depth=32)
    if type(normalized) is dict and normalized.get("id") == _ROA_ID:
        return _roa_profile(normalized)
    conformant = type(normalized) is dict and normalized.get("id") in ("brain-high-value-v3", _ADAPTIVE_ID)
    profile = _fields(normalized, _PROFILE_FIELDS | ({"conformance"} if conformant else set()), "profile")
    if (type(profile["id"]) is not str or _ID.fullmatch(profile["id"]) is None
            or profile["id"] not in _PROFILE_VERSIONS
            or type(profile["version"]) is not int or profile["version"] != _PROFILE_VERSIONS[profile["id"]]):
        raise BrainAuditError("invalid audit profile identity")
    if conformant and _json(profile["conformance"]) != _json(_conformance_policy()):
        raise BrainAuditError("invalid conformance policy")
    flows = profile["applicable_flows"]
    if type(flows) is not list or flows != [
        "ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING"
    ]:
        raise BrainAuditError("invalid applicable flows")
    lenses = profile["lenses"]
    if type(lenses) is not list or len(lenses) != len(_LENS_IDS):
        raise BrainAuditError("incomplete lens coverage")
    for expected_id, lens in zip(_LENS_IDS, lenses):
        _fields(lens, _LENS_FIELDS, "lens")
        if type(lens["id"]) is not str or lens["id"] != expected_id:
            raise BrainAuditError("unknown, duplicate or out-of-order lens id")
    procedure = _fields(profile["procedure"], _PROCEDURE_FIELDS, "procedure")
    for name, expected in (
        ("stages", ["CONSTRUCT", "ADVERSARIAL_AUDIT_AND_RECONCILE"]),
        ("construct_outcomes", ["CLEAR", "RISK_FOUND"]),
        ("risk_dispositions", ["ADDRESSED_BY_RECONCILIATION", "DISMISSED_WITH_BOUNDED_BASIS"]),
        ("closure_outcomes", ["CLEAR", "BLOCKER"]),
        ("final_outcomes", ["CANDIDATE", "NO_DECISION"]),
    ):
        if procedure[name] != expected:
            raise BrainAuditError(f"invalid v2 {name} procedure")
    if procedure["closure_on_reconciled_candidate"] is not True:
        raise BrainAuditError("closure must cover the reconciled candidate")
    bounds = _fields(profile["bounds"], _BOUND_FIELDS, "bounds")
    # Ceiling guards prevent caller-supplied policy from widening the bounded
    # protocol. The repository registry supplies the actual v2 policy values.
    ceilings = {
        "lens_check_bytes": 2048,
        "candidate_bytes": 131072, "risks_per_lens": 4,
        "risk_summary_bytes": 4096, "counterexample_bytes": 8192,
        "candidate_anchor_bytes": 1024, "dismissal_basis_bytes": 4096,
        "closure_blocker_summary_bytes": 4096, "stage2_bytes": 524288,
        "max_depth": 32,
    }
    if any(type(bounds[k]) is not int or not 1 <= bounds[k] <= cap for k, cap in ceilings.items()):
        raise BrainAuditError("profile bounds exceed the v2 safety envelope")
    for lens in lenses:
        _text(lens["check"], bounds["lens_check_bytes"], "lens check")
    _bounded(profile, 32768, "profile material")
    if profile["id"] == _ADAPTIVE_ID and _digest(profile) != _ADAPTIVE_DIGEST:
        raise BrainAuditError("unsupported or partially adapted V1 profile content")
    return profile


def _roa_profile(value: dict[str, Any]) -> dict[str, Any]:
    profile = _fields(value, (_PROFILE_FIELDS - {"lenses"}) | {"checks"}, "ROA profile")
    if (type(profile["version"]) is not int or profile["version"] != 2
            or profile["applicable_flows"] != ["REPAIR_AUTHORING"]
            or _json(profile["procedure"]) != _json(_ROA_PROCEDURE)
            or _json(profile["bounds"]) != _json(_ROA_BOUNDS)):
        raise BrainAuditError("invalid bounded ROA identity or procedure")
    checks = profile["checks"]
    if type(checks) is not list or len(checks) != len(_ROA_CHECK_IDS):
        raise BrainAuditError("ROA requires exactly five checks")
    for identity, check in zip(_ROA_CHECK_IDS, checks):
        _fields(check, _LENS_FIELDS, "ROA check")
        if check["id"] != identity:
            raise BrainAuditError("unknown, duplicate or out-of-order ROA check")
        _text(check["check"], _ROA_BOUNDS["check_bytes"], "ROA check instruction")
    _bounded(profile, 8192, "ROA profile")
    if _digest(profile) != _ROA_DIGEST:
        raise BrainAuditError("unsupported ROA profile content")
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
    if type(profiles) is not list or not 1 <= len(profiles) <= 4:
        raise BrainAuditError("registry requires one to four recognized profiles")
    normalized = [_profile(profile) for profile in profiles]
    if len({profile["id"] for profile in normalized}) != len(normalized):
        raise BrainAuditError("duplicate profile identity")
    return {"format": registry["format"], "version": 1, "profiles": normalized}


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
    candidate = _normal(value, depth=0, max_depth=policy["bounds"]["max_depth"], private=True,
                        v3=_has_conformance(policy) or policy["id"] == _ROA_ID,
                        adaptive=policy["id"] in {_ADAPTIVE_ID, _ROA_ID})
    if type(candidate) is not dict:
        raise BrainAuditError("candidate must be a strict-JSON mapping")
    _bounded(candidate, policy["bounds"]["candidate_bytes"], "candidate")
    return candidate


def construct_stage1(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                     construct_candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Bind one normalized construct to one exact packet and profile."""
    body, policy = _packet(packet), _profile(profile)
    if policy["id"] == _ROA_ID:
        raise BrainAuditError("ROA has one procedure and cannot construct a historical stage")
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
        if type(raw) is not dict or raw.get("lens") != expected_lens["id"]:
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


def _acceptance_phase_ledger(value: Any) -> list[dict[str, str]]:
    """Normalize declarations only; final TASK coverage and phase gate are ingress-owned."""
    if type(value) is not list or len(value) > 256:
        raise BrainAuditError("acceptance_phase_ledger must be an array of at most 256 entries")
    result = []
    for item in value:
        entry = _fields(item, {"id", "phase"}, "acceptance phase entry")
        criterion_id = _text(entry["id"], 256, "acceptance phase id")
        if type(entry["phase"]) is not str or entry["phase"] not in {"CLAIM_NOW", "PROOF_LATER"}:
            raise BrainAuditError("acceptance phase must be CLAIM_NOW or PROOF_LATER")
        result.append({"id": criterion_id, "phase": entry["phase"]})
    _bounded(result, 32768, "acceptance_phase_ledger")
    return result


def _conformance_section(name: str, value: Any, packet_digest: str,
                         candidate_digest: str) -> dict[str, Any]:
    """Check Brain declarations, coverage and binding; never decide feasibility."""
    section = _fields(value, {"packet_fingerprint", "reconciled_candidate_fingerprint",
                              "status", "basis", "entries"}, name)
    if (section["packet_fingerprint"] != packet_digest
            or section["reconciled_candidate_fingerprint"] != candidate_digest):
        raise BrainAuditError(f"{name} lineage mismatch")
    status = section["status"]
    allowed = {"CLEAR", "BLOCKED"} if name == "terminal_lifecycle" else {"CLEAR", "NOT_APPLICABLE", "BLOCKED"}
    if type(status) is not str or status not in allowed:
        raise BrainAuditError(f"invalid {name} status")
    _normal(section["basis"], depth=0, max_depth=32, private=True, v3=True)
    _text(section["basis"], _CONFORMANCE_BOUNDS["text_bytes"], f"{name} basis")
    entries = _normal(section["entries"], depth=0, max_depth=32, private=True, v3=True)
    if type(entries) is not list or len(entries) > _CONFORMANCE_BOUNDS["entries_per_section"]:
        raise BrainAuditError(f"invalid {name} entry count")
    ids = []
    statuses = []
    for entry in entries:
        fields = {"id", "status", "basis", "candidate_anchor"}
        if name == "cross_authority_context":
            fields |= {"caller_authority", "consumer_authority", "context_role", "propagation"}
        elif name == "canonical_shape":
            fields |= {"shape"}
        _fields(entry, fields, f"{name} entry")
        for field in fields - {"status"}:
            _text(entry[field], _CONFORMANCE_BOUNDS["text_bytes"], f"{name} {field}")
        entry_statuses = {"FEASIBLE", "BLOCKED"} if name == "terminal_lifecycle" else {"COVERED", "NOT_APPLICABLE", "BLOCKED"}
        if type(entry["status"]) is not str or entry["status"] not in entry_statuses:
            raise BrainAuditError(f"invalid {name} entry status")
        ids.append(entry["id"])
        statuses.append(entry["status"])
    if len(ids) != len(set(ids)):
        raise BrainAuditError(f"duplicate {name} entry id")
    expected = _SHAPE_IDS if name == "canonical_shape" else _PATH_IDS if name == "terminal_lifecycle" else None
    if expected is not None and ids != list(expected):
        raise BrainAuditError(f"incomplete, substituted or out-of-order {name} coverage")
    if "BLOCKED" in statuses and status != "BLOCKED":
        raise BrainAuditError(f"{name} status contradicts BLOCKED entry")
    if status == "NOT_APPLICABLE" and any(s != "NOT_APPLICABLE" for s in statuses):
        raise BrainAuditError(f"{name} non-applicability contradicts entries")
    if status == "CLEAR" and not any(s in {"COVERED", "FEASIBLE"} for s in statuses):
        raise BrainAuditError(f"{name} CLEAR requires explicit covered entries")
    return section


def validate_stage2(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                    stage1: Mapping[str, Any], semantic_output: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one audit, reconciliation and final closure within Stage 2."""
    body, policy = _packet(packet), _profile(profile)
    if policy["id"] == _ROA_ID:
        raise BrainAuditError("ROA cannot reconcile a historical stage")
    if body["authority_owner"] != "BRAIN" or body["selected_flow"] not in policy["applicable_flows"]:
        raise BrainAuditError("profile does not apply to selected flow")
    first = _stage1(body, policy, stage1)
    material = _normal(semantic_output, depth=0, max_depth=policy["bounds"]["max_depth"])
    fields = _STAGE2_FIELDS
    conformant_task = _has_conformance(policy) and body["selected_flow"] == "TASK_AUTHORING"
    if conformant_task:
        fields = fields | set(_CONFORMANCE_LENSES)
    if type(material) is dict and "acceptance_phase_ledger" in material:
        if body["selected_flow"] != "TASK_AUTHORING":
            raise BrainAuditError("acceptance_phase_ledger is TASK_AUTHORING-only")
        fields = fields | {"acceptance_phase_ledger"}
        material["acceptance_phase_ledger"] = _acceptance_phase_ledger(material["acceptance_phase_ledger"])
    material = _fields(material, fields, "Stage-2 semantic output")
    _bounded(material, policy["bounds"]["stage2_bytes"], "Stage-2 semantic material")
    for field in ("packet_fingerprint", "audit_profile_ref", "selected_flow", "construct_candidate", "construct_fingerprint"):
        if _json(material[field]) != _json(first[field]):
            raise BrainAuditError(f"Stage-2 {field} lineage mismatch")
    for field in ("construct_audit", "closure"):
        _normal(material[field], depth=0, max_depth=policy["bounds"]["max_depth"], private=True,
                v3=policy["id"] == _ADAPTIVE_ID, adaptive=policy["id"] == _ADAPTIVE_ID)
    ledger = _ordered_lenses(material["construct_audit"], policy, closure=False)
    reconciled = _candidate(material["reconciled_candidate"], policy)
    addressed = any(risk["disposition"] == "ADDRESSED_BY_RECONCILIATION"
                    for lens in ledger for risk in lens.get("risks", []))
    if (_json(reconciled) != _json(first["construct_candidate"])) != addressed:
        raise BrainAuditError("reconciliation contradicts addressed-risk claims")
    closure = _ordered_lenses(material["closure"], policy, closure=True)
    final_fingerprint = _digest(reconciled)
    if conformant_task:
        sections = {name: _conformance_section(name, material[name], body["packet_fingerprint"],
                                               final_fingerprint) for name in _CONFORMANCE_LENSES}
        _bounded(sections, _CONFORMANCE_BOUNDS["sections_bytes"], "conformance sections")
        for name, lens in _CONFORMANCE_LENSES.items():
            if sections[name]["status"] == "BLOCKED" and not any(
                    item["lens"] == lens and item["outcome"] == "BLOCKER" for item in closure):
                raise BrainAuditError(f"{name} BLOCKED requires {lens} closure BLOCKER and NO_DECISION")
    outcome = "NO_DECISION" if any(item["outcome"] == "BLOCKER" for item in closure) else "CANDIDATE"
    if material["outcome"] != outcome:
        raise BrainAuditError("outcome contradicts closure")
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
    if "acceptance_phase_ledger" in material:
        result["acceptance_phase_ledger"] = material["acceptance_phase_ledger"]
    if conformant_task:
        result.update(sections)
    result["stage2_fingerprint"] = _digest({
        "stage1_construct_fingerprint": first["construct_fingerprint"],
        "semantic_material": material,
        "reconciled_candidate_fingerprint": final_fingerprint,
    })
    return result


def _git_sha(value: Any, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise BrainAuditError(f"invalid {name}")
    return value


def _roa_source(path: str, value: Any) -> dict[str, str]:
    # Source values stay in the exact packet, never in a second evidence store.
    _normal(value, depth=0, max_depth=32, private=True, v3=True, adaptive=True)
    return {"path": path, "digest": _digest(value)}


def construct_roa_subject(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                          return_contract_ref: Mapping[str, Any],
                          external_bindings: Mapping[str, Any]) -> dict[str, Any]:
    """Derive a portable subject from packet-owned sources, without discovering Git.

    Missing decisive sources remain null. They can only yield UNKNOWN/blocker;
    caller references or model-written evidence cannot fill those slots.
    """
    body, policy = _packet(packet), _profile(profile)
    if (policy["id"] != _ROA_ID or body["selected_flow"] != "REPAIR_AUTHORING"
            or body["authority_owner"] != "BRAIN"
            or body["decision_family_ref"] != "publication._validate_repair_authorization"
            or body["handoff_target"] != "AUTHORING_INGRESS"
            or body["expected_return_shape"] != "REPAIR_AUTHORIZATION_PROPOSAL"
            or body["requires_fresh_context_for_continuation"] is not True):
        raise BrainAuditError("ROA requires the exact Brain REPAIR_AUTHORING boundary")
    reference = _fields(_normal(return_contract_ref, depth=0, max_depth=32),
                        {"selected_flow", "version", "digest"}, "ROA return contract reference")
    if (reference["selected_flow"] != "REPAIR_AUTHORING"
            or type(reference["version"]) is not int or reference["version"] != 1):
        raise BrainAuditError("ROA return contract mismatch")
    _sha(reference["digest"], "return contract digest")
    bindings = _fields(_normal(external_bindings, depth=0, max_depth=32), {"repair_id"}, "ROA external binding")
    _text(bindings["repair_id"], 256, "ROA repair identity")
    if type(bindings["repair_id"]) is not str or re.fullmatch(r"REPAIR-[A-Za-z0-9_-]+-\d{3,}", bindings["repair_id"]) is None:
        raise BrainAuditError("invalid bound REPAIR identity")
    facts, target = body["canonical_facts"], body["subject"]
    if type(facts) is not dict or type(target) is not dict:
        raise BrainAuditError("ROA canonical subject is missing")
    task = _fields(facts.get("selected_task"), {"id", "revision"}, "ROA TASK identity")
    _text(task["id"], 128, "ROA TASK identity")
    _text(target.get("failed_run_id"), 128, "ROA failed RUN identity")
    if (type(task["id"]) is not str or re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task["id"]) is None
            or type(task["revision"]) is not int or task["revision"] < 1
            or type(target.get("failed_run_id")) is not str
            or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", target["failed_run_id"]) is None):
        raise BrainAuditError("invalid ROA failed RUN or TASK")
    _git_sha(target.get("failed_head_sha"), "ROA failed head")
    _git_sha(facts.get("main_sha"), "ROA source main")
    _sha(body["work_context_fingerprint"], "ROA work context")
    sources: dict[str, Any] = {"failure": None, "evidence": None, "authorization": None,
                               "prior_authorization": None}
    supersession = body["selection_basis"] == "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION"
    correction = facts.get("correction_subject")
    if correction is not None:
        if (type(correction) is not dict or correction.get("policy") != "EXACT_CORRECTION_SUBJECT_LINEAGE_V1"
                or correction.get("operation") != "AUTHOR_REPAIR" or _json(correction.get("task")) != _json(task)
                or any(correction.get(k, target[k]) != target[k] for k in ("failed_run_id", "failed_head_sha"))):
            raise BrainAuditError("ROA FAILURE source identity mismatch")
        if correction.get("artifacts_sha") is not None:
            _git_sha(correction["artifacts_sha"], "ROA FAILURE artifact")
            sources["failure"] = _roa_source("canonical_facts.correction_subject", correction)
    if (sources["failure"] is None and supersession and target.get("kind") == "REPAIR_SUPERSESSION"
            and target.get("failure_artifacts_sha") is not None):
        _git_sha(target["failure_artifacts_sha"], "ROA supersession FAILURE artifact")
        sources["failure"] = _roa_source("subject", target)
    observation = body["bounded_observations"]
    if (type(observation) is dict and observation.get("phase") in ("EXECUTION", "VERIFICATION", "COMPLETION_GATE")
            and type(observation.get("error")) is dict and observation["error"].get("type")
            and type(observation.get("strategy_facts")) is dict and observation["strategy_facts"]):
        sources["evidence"] = _roa_source("bounded_observations", observation)
    contract = facts.get("task_contract")
    if contract is not None:
        if (type(contract) is not dict or contract.get("task_id") != task["id"]
                or type(contract.get("revision")) is not int or contract["revision"] != task["revision"]):
            raise BrainAuditError("ROA TASK authorization identity mismatch")
        scope, constraints = contract.get("scope"), contract.get("constraints")
        if (type(scope) is dict and type(scope.get("modify")) is list
                and all(type(p) is str and p for p in scope["modify"])
                and type(constraints) is dict and type(constraints.get("hard")) is list
                and all(type(c) is str and c.strip() for c in constraints["hard"])
                and facts.get("canonical_next_action") in ("AUTHOR_REPAIR", "EXECUTE_REPAIR")
                and body["canonical_blocker"] is None):
            sources["authorization"] = _roa_source("canonical_facts.task_contract", contract)
    prior = body["prior_semantic_decisions"]
    if supersession:
        if (type(prior) is dict and prior.get("kind") == "REPAIR"
                and prior.get("authorization_sha") is not None and prior.get("authorization") is not None):
            _git_sha(prior.get("authorization_sha"), "ROA prior authorization")
            if (prior["authorization_sha"] != target.get("current_repair_authorization_sha")
                    or type(prior.get("authorization")) is not dict
                    or any(prior["authorization"].get(k) != target[k] for k in ("failed_run_id", "failed_head_sha"))
                    or _json(prior["authorization"].get("task")) != _json(task)
                    or correction is not None and correction.get("artifacts_sha") is not None
                    and target.get("failure_artifacts_sha") != correction["artifacts_sha"]):
                raise BrainAuditError("ROA supersession source mismatch")
            sources["prior_authorization"] = _roa_source("prior_semantic_decisions", prior)
        if (sources["prior_authorization"] is None or target.get("kind") != "REPAIR_SUPERSESSION"
                or facts.get("canonical_next_action") != "EXECUTE_REPAIR"):
            sources["authorization"] = None
    elif (prior is not None or facts.get("canonical_next_action") != "AUTHOR_REPAIR"
          or body["selection_basis"] != "UNIFIED_STATE_AUTHOR_REPAIR"):
        sources["authorization"] = None
    result = {
        "format": "AIOS_ROA_SUBJECT", "version": 1,
        "packet_fingerprint": body["packet_fingerprint"],
        "work_context_fingerprint": body["work_context_fingerprint"],
        "selected_flow": body["selected_flow"], "audit_profile_ref": profile_ref(policy),
        "return_contract_ref": reference, "external_bindings": bindings,
        "task": task, "failed_run_id": target["failed_run_id"], "failed_head_sha": target["failed_head_sha"],
        "sources": sources,
    }
    result["subject_fingerprint"] = _digest(result)
    return result


def _roa_candidate(value: Any, policy: dict[str, Any], body: dict[str, Any],
                   subject: dict[str, Any]) -> dict[str, Any]:
    candidate = _fields(_candidate(value, policy), {
        "repair_id", "failed_run_id", "failed_head_sha", "task", "action",
        "modification_scope", "instructions", "constraints",
    }, "ROA final REPAIR candidate")
    for key, expected in {**subject["external_bindings"], **{k: subject[k] for k in
                          ("failed_run_id", "failed_head_sha", "task")}}.items():
        if _json(candidate[key]) != _json(expected):
            raise BrainAuditError(f"ROA final candidate {key} binding mismatch")
    if candidate["action"] not in ("CODE_FIX", "NO_CHANGE", "CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE"):
        raise BrainAuditError("unknown ROA repair action")
    contract = body["canonical_facts"]["task_contract"]
    scope = candidate["modification_scope"]
    if (type(scope) is not list or any(type(p) is not str for p in scope)
            or len(scope) != len(set(scope)) or not set(scope) <= set(contract["scope"]["modify"])):
        raise BrainAuditError("ROA candidate exceeds TASK scope")
    for name in ("instructions", "constraints"):
        entries = candidate[name]
        if type(entries) is not list or len(entries) > 256 or (name == "instructions" and not entries):
            raise BrainAuditError(f"invalid ROA {name}")
        for entry in entries:
            _text(entry, policy["bounds"]["basis_bytes"], name)
    if not set(contract["constraints"]["hard"]) <= set(candidate["constraints"]):
        raise BrainAuditError("ROA candidate drops original constraints")
    return candidate


def validate_roa(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                 subject: Mapping[str, Any], semantic_output: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exactly five focused judgments and one closure in one procedure.

    OBSERVED/INFERRED are Brain judgments about packet-bound sources, not proof.
    This validator has no lifecycle, provider, policy or execution authority.
    """
    body, policy = _packet(packet), _profile(profile)
    supplied = _normal(subject, depth=0, max_depth=32)
    if type(supplied) is not dict:
        raise BrainAuditError("ROA subject required")
    bound = construct_roa_subject(body, policy, supplied.get("return_contract_ref"),
                                  supplied.get("external_bindings"))
    if _json(supplied) != _json(bound):
        raise BrainAuditError("ROA subject binding mismatch")
    material = _fields(_normal(semantic_output, depth=0, max_depth=policy["bounds"]["max_depth"]),
                       {"subject_fingerprint", "checks", "closure", "candidate"}, "ROA semantic output")
    _bounded(material, policy["bounds"]["outcome_bytes"], "ROA semantic output")
    if material["subject_fingerprint"] != bound["subject_fingerprint"]:
        raise BrainAuditError("stale ROA subject")
    checks = material["checks"]
    if type(checks) is not list or len(checks) != 5:
        raise BrainAuditError("ROA requires exactly five check results")
    sources = bound["sources"]
    required_roles = ("failure", "evidence", "evidence", "evidence", "authorization")
    for identity, role, check in zip(_ROA_CHECK_IDS, required_roles, checks):
        _fields(check, {"id", "outcome", "knowledge", "basis", "evidence_refs"}, "ROA check result")
        _normal(check, depth=0, max_depth=32, private=True, v3=True, adaptive=True)
        if (check["id"] != identity or check["outcome"] not in ("CLEAR", "BLOCKER")
                or check["knowledge"] not in ("OBSERVED", "INFERRED", "UNKNOWN")):
            raise BrainAuditError("invalid or out-of-order ROA judgment")
        _text(check["basis"], policy["bounds"]["basis_bytes"], "ROA basis")
        refs = check["evidence_refs"]
        if type(refs) is not list or len(refs) > policy["bounds"]["evidence_refs_per_check"]:
            raise BrainAuditError("ROA evidence references exceed bound")
        if (len({_json(ref) for ref in refs}) != len(refs)
                or any(ref not in [s for s in sources.values() if s is not None] for ref in refs)):
            raise BrainAuditError("forged or duplicate ROA evidence reference")
        if sources[role] is None and (check["knowledge"] != "UNKNOWN" or check["outcome"] != "BLOCKER"):
            raise BrainAuditError("missing decisive ROA source requires UNKNOWN blocker")
        if check["knowledge"] == "UNKNOWN":
            if check["outcome"] != "BLOCKER":
                raise BrainAuditError("UNKNOWN cannot discharge a ROA check")
        elif sources[role] not in refs:
            raise BrainAuditError("ROA judgment lacks its discriminating source")
    closure = _fields(material["closure"], {"outcome", "knowledge", "summary", "candidate_fingerprint"}, "ROA final closure")
    _text(closure["summary"], policy["bounds"]["closure_summary_bytes"], "ROA closure summary")
    _normal(closure["summary"], depth=0, max_depth=32, private=True)
    outcome = "NO_DECISION" if any(c["outcome"] == "BLOCKER" for c in checks) else "CANDIDATE"
    knowledge = ("UNKNOWN" if any(c["knowledge"] == "UNKNOWN" for c in checks) else
                 "INFERRED" if any(c["knowledge"] == "INFERRED" for c in checks) else "OBSERVED")
    if closure["outcome"] != outcome or closure["knowledge"] != knowledge:
        raise BrainAuditError("ROA closure contradicts the five judgments")
    if outcome == "NO_DECISION":
        if material["candidate"] is not None or closure["candidate_fingerprint"] is not None:
            raise BrainAuditError("ROA blocker cannot expose a speculative repair")
        candidate = None
    else:
        candidate = _roa_candidate(material["candidate"], policy, body, bound)
        if closure["candidate_fingerprint"] != _digest(candidate):
            raise BrainAuditError("ROA final candidate fingerprint mismatch")
    result = {
        "format": "AIOS_BRAIN_REPAIR_OUTCOME_AUDIT", "version": 2,
        "procedure": "REPAIR_OUTCOME_AUDIT", "packet_fingerprint": body["packet_fingerprint"],
        "audit_profile_ref": profile_ref(policy), "selected_flow": "REPAIR_AUTHORING",
        "subject_fingerprint": bound["subject_fingerprint"], "checks": checks,
        "closure": closure, "handoff_candidate": candidate,
    }
    result["audit_fingerprint"] = _digest(result)
    return result


__all__ = [
    "BrainAuditError", "parse_profile_registry", "normalize_profile", "profile_ref",
    "profile_applies", "construct_stage1", "validate_stage2", "construct_roa_subject", "validate_roa",
]
