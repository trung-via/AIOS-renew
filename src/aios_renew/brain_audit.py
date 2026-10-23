"""Pure historical BP-4A and bounded ROA/MOA/TCA/AOA conformance over portable profiles.

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
    "audit_selection", "audit_policy", "human_v1_deep_support", "selection_authenticator",
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
_MOA_ID = "moa-1"
_MOA_DIGEST = "49d1c4c3edefe3d3cefcd66e186b7d7b89738516129957c18415d55760ffe903"
_TCA_ID = "tca-1"
_TCA_DIGEST = "1fa1d5fd098494eeb5cb97665f40e7556969555aed552dfc7fa28871555884a2"
_TCA_SUCCESSOR_ID = "tca-2"
_TCA_SUCCESSOR_DIGEST = "75f7644caf478f39b50279475b81583df6f894e519951199575873de9dd6a17d"
_TCA_V3_ID = "tca-3"
_ROA_SUCCESSOR_ID = "roa-1-r3"
_ROA_SUCCESSOR_DIGEST = "cee2404daa670ff90314cc7cce938766b2eaf4a59ebaf3c7fca3d6ed41886c5a"
_TCA_SUCCESSOR_IDS = frozenset({_TCA_SUCCESSOR_ID, _TCA_V3_ID})
_TCA_IDS = frozenset({_TCA_ID, _TCA_SUCCESSOR_ID, _TCA_V3_ID})
_ROA_IDS = frozenset({_ROA_ID, _ROA_SUCCESSOR_ID})
_AOA_ID = "aoa-1"
_AOA_DIGEST = "7789fcc53bd6593c8d190e867d14d9fe80060395ae20a0706981d16fec7e617b"
_PROFILE_VERSIONS = {"brain-high-value-v2": 2, "brain-high-value-v3": 3,
                     _ADAPTIVE_ID: 1, _ROA_ID: 2, _MOA_ID: 1, _TCA_ID: 1, _AOA_ID: 1,
                     _TCA_SUCCESSOR_ID: 2, _TCA_V3_ID: 3, _ROA_SUCCESSOR_ID: 3}
_AOA_CONSTRUCT_IDS = (
    "HUMAN_OUTCOME_AND_NECESSITY", "COUNTERFACTUAL_ALTERNATIVES", "EPISODE_COST_AND_EXTERNALITIES",
    "DISCRIMINATING_INFORMATION", "THREATS_AUTHORITY_AND_REVERSIBILITY",
)
_AOA_CHALLENGE_IDS = (
    "NO_BUILD_FALSIFIER", "MINIMAL_DELTA_CHALLENGER", "OMITTED_CONSUMERS_AND_ALTERNATIVES",
    "HIDDEN_COST_AND_INVENTED_METRICS", "RISK_OWNERSHIP_AND_IRREVERSIBILITY", "SOURCE_AUTHORITY_AND_CLOSURE",
)
_AOA_CONSTRUCT_ROLES = (("intent", "objective", "authorization"), ("alternatives", "objective"),
                        ("alternatives", "consumers"), ("objective", "alternatives"), ("authorization", "consumers"))
_AOA_CHALLENGE_ROLES = (("alternatives", "objective"), ("alternatives", "objective"),
                        ("consumers", "alternatives"), ("alternatives", "consumers"),
                        ("authorization", "alternatives"), ("canonical", "intent", "authorization"))
_AOA_KINDS = {"A": "NO_BUILD_REUSE", "B": "MINIMUM_LAWFUL_DELTA", "C": "NEW_MECHANISM"}
_AOA_COST_DIMENSIONS = ("elapsed_minutes", "tokens", "human_minutes", "coordination_minutes", "verification_minutes")
_AOA_BOUNDS = {
    "check_bytes": 2048, "candidate_bytes": 32768, "basis_bytes": 2048,
    "evidence_refs_per_check": 16, "evidence_count": 16, "consumer_count": 16,
    "closure_summary_bytes": 2048, "outcome_bytes": 262144,
    "source_context_bytes": 65536, "max_depth": 32,
}
_AOA_PROCEDURE = {
    "name": "ARCHITECTURE_OUTCOME_AUDIT", "stages": ["CONSTRUCT", "ADVERSARIAL_AUDIT_AND_RECONCILE"],
    "check_outcomes": ["CLEAR", "BLOCKER"], "knowledge_states": ["OBSERVED", "INFERRED", "UNKNOWN"],
    "challenge_dispositions": ["REJECTED_WITH_BOUNDED_BASIS", "ADDRESSED_BY_RECONCILIATION", "BLOCKER"],
    "blocker_dispositions": ["UNRESOLVED", "RESOLVED"],
    "final_outcomes": ["CANDIDATE", "NO_DECISION"], "final_closure": True,
}
_AOA_CONSTRUCT_FIELDS = {
    "format", "version", "stage", "subject_fingerprint", "checks", "alternatives", "information",
    "draft_candidate", "construct_fingerprint",
}
_AOA_FINAL_FIELDS = {
    "format", "version", "stage", "subject_fingerprint", "construct_fingerprint", "challenges",
    "alternatives", "information", "reconciliation", "closure", "handoff_candidate", "audit_fingerprint",
}
_TCA_CONSTRUCT_IDS = (
    "HUMAN_INTENT_FIDELITY", "TASK_OBJECTIVE_OUTCOME", "ACCEPTANCE_SATISFIABILITY",
    "SCOPE_AND_AUTHORITY", "REQUIRED_CONSUMERS",
)
_TCA_CHALLENGE_IDS = (
    "FALSE_COMPLETION_CHALLENGE", "MATERIAL_CONSUMER_CHALLENGE", "SCOPE_AND_HIDDEN_INTENT_CHALLENGE",
    "AUTHORITY_AND_PROOF_ORDER_CHALLENGE", "FALSIFYING_COUNTEREXAMPLE_CHALLENGE",
    "SOURCE_CURRENTNESS_AND_PORTABILITY_CHALLENGE",
)
_TCA_CONSTRUCT_ROLES = (("intent", "task"), ("intent", "task"), ("task",),
                        ("task", "authorization"), ("task", "consumers"))
_TCA_CHALLENGE_ROLES = (("task",), ("task", "consumers"), ("task", "authorization", "intent"),
                        ("task", "authorization"), ("task", "consumers"), ("canonical", "intent", "authorization"))
_TCA_BOUNDS = {
    "check_bytes": 2048, "candidate_bytes": 131072, "basis_bytes": 2048,
    "evidence_refs_per_check": 5, "acceptance_count": 256, "consumer_count": 16,
    "closure_summary_bytes": 2048, "outcome_bytes": 262144,
    "source_context_bytes": 65536, "max_depth": 32,
}
_TCA_PROCEDURE = {
    "name": "TASK_CONTRACT_AUDIT", "stages": ["CONSTRUCT", "ADVERSARIAL_AUDIT_AND_RECONCILE"],
    "check_outcomes": ["CLEAR", "BLOCKER"], "knowledge_states": ["OBSERVED", "INFERRED", "UNKNOWN"],
    "acceptance_phases": ["CLAIM_NOW", "PROOF_LATER"],
    "challenge_dispositions": ["REJECTED_WITH_BOUNDED_BASIS", "ADDRESSED_BY_RECONCILIATION", "BLOCKER"],
    "blocker_dispositions": ["UNRESOLVED", "RESOLVED"],
    "final_outcomes": ["CANDIDATE", "NO_DECISION"], "final_closure": True,
}
_TCA_CONSTRUCT_FIELDS = {
    "format", "version", "stage", "subject_fingerprint", "checks", "acceptance_checks",
    "consumer_checks", "draft_candidate", "construct_fingerprint",
}
_TCA_FINAL_FIELDS = {
    "format", "version", "stage", "subject_fingerprint", "construct_fingerprint", "challenges",
    "acceptance_checks", "consumer_checks", "acceptance_accounting", "reconciliation",
    "closure", "handoff_candidate", "audit_fingerprint",
}
_TCA_V3_PROFILE_SPEC = {
    "id": _TCA_V3_ID,
    "version": 3,
    "applicable_flows": ["TASK_AUTHORING"],
    "passes": [
        {
            "id": "CONSTRUCT",
            "checks": [
                {
                    "id": "HUMAN_INTENT_FIDELITY",
                    "check": (
                        "Extract the exact Human-requested outcome, priority, constraints and non-goals from the packet-bound "
                        "request, canonical main and authorized source excerpts. Preserve sticky admitted Executor and profile "
                        "identity, pre-RUN dirty-worktree quarantine vs post-RUN workspace boundaries (TASK-393/TASK-401), "
                        "and prospective two-step activation separation where implementation and live cutover are distinct. "
                        "Missing decisive request or canonical main context is UNKNOWN and BLOCKER."
                    ),
                },
                {
                    "id": "TASK_OBJECTIVE_OUTCOME",
                    "check": (
                        "State the concrete repository change and why it achieves the Human outcome without unrequested "
                        "architecture, silent policy activation, implicit full-suite verification or role drift. Distinguish "
                        "prospective planning/candidate preparation from live activation cutover and separate baseline failure "
                        "causes (such as TASK-403 pre-existing newline drift vs new git object-fetch defect) rather than "
                        "conflating them under one label."
                    ),
                },
                {
                    "id": "ACCEPTANCE_SATISFIABILITY",
                    "check": (
                        "Examine every acceptance criterion and verification.required command against current packet-bound "
                        "implementation and test sources. Separate implementation properties establishable at Executor completion "
                        "(CLAIM_NOW) from post-implementation Runtime verification/Reviewer/Publisher gates (PROOF_LATER), "
                        "and record per-command baseline observations (PASS, FAIL, NOT_RUN, UNKNOWN) without inventing verified "
                        "outcomes, skipped verification or an implicit full suite (TASK-372/TASK-399). Ensure all acceptance "
                        "IDs can be truthfully claimed in final ResultPackage.result.claims without missing-claim or unresolved-item "
                        "completion-gate deadlocks (TASK-391/TASK-383/TASK-384)."
                    ),
                },
                {
                    "id": "SCOPE_AND_AUTHORITY",
                    "check": (
                        "Check scope.modify, scope.create, scope.delete and scope.inspect against current implementation, "
                        "transitive direct-call helper/API consumers, Git rev-parse/cat-file framing and object-fetch paths, "
                        "and legacy test doubles or AST/signature guards. Any file requiring an edit to satisfy acceptance "
                        "or keep existing tests/guards compatible (including TASK-391 r1 AST guard tests) must be in scope.modify "
                        "rather than inspect-only; inspect-only covers read-only context and cannot authorize edits."
                    ),
                },
                {
                    "id": "REQUIRED_CONSUMERS",
                    "check": (
                        "Identify every direct and bounded one-hop transitive caller, CLI/subcommand parser, provider/protocol "
                        "codec, Git object-fetch/framing helper, documentation contract and existing test double or AST "
                        "guard touched by the change. Account for how each material consumer and mixed targeted verification "
                        "command in verification.required remains coherent within authorized scope."
                    ),
                },
            ],
        },
        {
            "id": "ADVERSARIAL_AUDIT_AND_RECONCILE",
            "checks": [
                {
                    "id": "FALSE_COMPLETION_CHALLENGE",
                    "check": (
                        "Attempt to satisfy literal TASK wording while leaving the Human outcome broken, incomplete "
                        "or deadlocked at Runtime completion gate. Challenge missing final acceptance claims (TASK-391 r2/TASK-383), "
                        "leftover result.unresolved entries (TASK-384), malformed ResultPackage/API shape (TASK-398), or "
                        "conflated failure causes where a single pytest node is treated as a complete root cause (TASK-403). "
                        "Reconcile the TASK or block."
                    ),
                },
                {
                    "id": "MATERIAL_CONSUMER_CHALLENGE",
                    "check": (
                        "Challenge whether any direct or bounded transitive caller, provider/protocol adapter, Git "
                        "framing/object-fetch helper, legacy test double, AST/signature guard or mixed verification command "
                        "in verification.required breaks or is omitted from scope.modify (TASK-391 r1, TASK-372, TASK-399, "
                        "TASK-403). If a consumer requires edits outside scope.modify or decisive source is absent, record "
                        "BLOCKER."
                    ),
                },
                {
                    "id": "SCOPE_AND_HIDDEN_INTENT_CHALLENGE",
                    "check": (
                        "Challenge scope.modify, scope.create, scope.delete and scope.inspect for missing modify authority "
                        "on AST/test guards, overbroad edits, hidden scope creep, unapproved profile self-activation or "
                        "premature retirement of the active default profile. Verify pre-RUN dirty-tree quarantine and native "
                        "runner boundaries (TASK-393/TASK-401) are not confused with post-RUN execution defects."
                    ),
                },
                {
                    "id": "AUTHORITY_AND_PROOF_ORDER_CHALLENGE",
                    "check": (
                        "Challenge any step that confuses Brain authoring with Runtime verification/completion gate, "
                        "Reviewer judgment, Publisher publication or live profile activation, or that conflates Executor "
                        "CLAIM_NOW implementation assertions with PROOF_LATER Runtime verification evidence or PASS/FAIL/NOT_RUN/UNKNOWN "
                        "baseline observations. Never invent verified outcomes, Executor claims, skipped verification or "
                        "implicit full-suite execution."
                    ),
                },
                {
                    "id": "FALSIFYING_COUNTEREXAMPLE_CHALLENGE",
                    "check": (
                        "Construct concrete counterexamples from packet-bound source: an inspect-only AST/test guard "
                        "requiring edits (TASK-391 r1), a committed diff with omitted AC claims or non-empty unresolved "
                        "at completion gate (TASK-391 r2/TASK-383/TASK-384), a wrong ResultPackage/API contract assumption "
                        "(TASK-398), a conflated newline vs git object-fetch failure (TASK-403), or mixed verification command "
                        "mismatch (TASK-372/TASK-399). Verify reconciled TASK text closes each or return NO_DECISION."
                    ),
                },
                {
                    "id": "SOURCE_CURRENTNESS_AND_PORTABILITY_CHALLENGE",
                    "check": (
                        "Verify canonical main identity, authorization binding, profile id/version/digest and packet-bound "
                        "implementation/test source excerpts are current, authentic and non-swapped. Missing, truncated, "
                        "stale or mismatched source excerpts must remain UNKNOWN/BLOCKER and terminate in NO_DECISION rather "
                        "than guessed CANDIDATE readiness."
                    ),
                },
            ],
        },
    ],
    "procedure": _TCA_PROCEDURE,
    "bounds": _TCA_BOUNDS,
}
_TCA_V3_DIGEST = hashlib.sha256(
    json.dumps(_TCA_V3_PROFILE_SPEC, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
).hexdigest()
_MOA_CONSTRUCT_IDS = ("EXACT_REVIEW_FINDING", "FINDING_CAUSE_AND_MINIMUM_DELTA", "ORIGINAL_TASK_AUTHORITY")
_MOA_CHALLENGE_IDS = (
    "SOURCE_AND_CURRENTNESS_CHALLENGE", "FINDING_CLOSURE_CHALLENGE",
    "NON_INTERFERENCE_CHALLENGE", "SINGLE_AUDIT_AND_AUTHORITY_CHALLENGE",
)
_MOA_BOUNDS = {
    "check_bytes": 2048, "candidate_bytes": 131072, "basis_bytes": 2048,
    "evidence_refs_per_check": 4, "finding_refs_per_check": 16,
    "closure_summary_bytes": 2048, "outcome_bytes": 196608,
    "source_context_bytes": 65536, "max_depth": 32,
}
_MOA_PROCEDURE = {
    "name": "REMEDIATION_OUTCOME_AUDIT", "stages": ["CONSTRUCT", "ADVERSARIAL_AUDIT_AND_RECONCILE"],
    "check_outcomes": ["CLEAR", "BLOCKER"], "knowledge_states": ["OBSERVED", "INFERRED", "UNKNOWN"],
    "challenge_dispositions": ["REJECTED_WITH_BOUNDED_BASIS", "ADDRESSED_BY_RECONCILIATION", "BLOCKER"],
    "final_outcomes": ["CANDIDATE", "NO_DECISION"], "final_closure": True,
}
_FINDING_IDENTITY = ("source_run_id", "review_id", "finding_id", "reviewed_sha")
_MOA_FINDING_FIELDS = set(_FINDING_IDENTITY) | {"basis", "action", "location", "issue", "expected"}
_MOA_CONSTRUCT_FIELDS = {
    "format", "version", "stage", "subject_fingerprint", "checks", "draft_candidate", "construct_fingerprint",
}
_MOA_FINAL_FIELDS = {
    "format", "version", "stage", "subject_fingerprint", "construct_fingerprint",
    "challenges", "closure", "handoff_candidate", "audit_fingerprint",
}
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
            "AIOS_DECISION_PACKET", "AIOS_BRAIN_AUDIT_PROFILES", "AIOS_BRAIN_SEMANTIC_AUDIT",
            "AIOS_BRAIN_AUDIT_POLICY", "AIOS_BRAIN_AUDIT_SELECTION",
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
    if type(normalized) is dict and type(normalized.get("id")) is str and normalized["id"] in _ROA_IDS:
        return _roa_profile(normalized)
    if type(normalized) is dict and normalized.get("id") == _MOA_ID:
        return _moa_profile(normalized)
    if type(normalized) is dict and type(normalized.get("id")) is str and normalized["id"] in _TCA_IDS:
        return _tca_profile(normalized)
    if type(normalized) is dict and normalized.get("id") == _AOA_ID:
        return _aoa_profile(normalized)
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
    if (type(profile["version"]) is not int or profile["version"] != _PROFILE_VERSIONS[profile["id"]]
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
    if _digest(profile) != {_ROA_ID: _ROA_DIGEST, _ROA_SUCCESSOR_ID: _ROA_SUCCESSOR_DIGEST}[profile["id"]]:
        raise BrainAuditError("unsupported ROA profile content")
    return profile


def _moa_profile(value: dict[str, Any]) -> dict[str, Any]:
    profile = _fields(value, (_PROFILE_FIELDS - {"lenses"}) | {"passes"}, "MOA profile")
    if (type(profile["version"]) is not int or profile["version"] != 1
            or profile["applicable_flows"] != ["REMEDIATION_AUTHORING"]
            or _json(profile["procedure"]) != _json(_MOA_PROCEDURE)
            or _json(profile["bounds"]) != _json(_MOA_BOUNDS)):
        raise BrainAuditError("invalid bounded MOA identity or procedure")
    passes = profile["passes"]
    if type(passes) is not list or len(passes) != 2:
        raise BrainAuditError("MOA requires exactly two focused passes")
    for stage, identities, item in zip(_MOA_PROCEDURE["stages"],
                                        (_MOA_CONSTRUCT_IDS, _MOA_CHALLENGE_IDS), passes):
        _fields(item, {"id", "checks"}, "MOA pass")
        if item["id"] != stage or type(item["checks"]) is not list or len(item["checks"]) != len(identities):
            raise BrainAuditError("invalid MOA focused pass")
        for identity, check in zip(identities, item["checks"]):
            _fields(check, _LENS_FIELDS, "MOA instruction")
            if check["id"] != identity:
                raise BrainAuditError("unknown, duplicate or out-of-order MOA instruction")
            _text(check["check"], _MOA_BOUNDS["check_bytes"], "MOA instruction")
    _bounded(profile, 8192, "MOA profile")
    if _digest(profile) != _MOA_DIGEST:
        raise BrainAuditError("unsupported MOA profile content")
    return profile


def _tca_profile(value: dict[str, Any]) -> dict[str, Any]:
    profile = _fields(value, (_PROFILE_FIELDS - {"lenses"}) | {"passes"}, "TCA profile")
    if (type(profile["version"]) is not int or profile["version"] != _PROFILE_VERSIONS[profile["id"]]
            or profile["applicable_flows"] != ["TASK_AUTHORING"]
            or _json(profile["procedure"]) != _json(_TCA_PROCEDURE)
            or _json(profile["bounds"]) != _json(_TCA_BOUNDS)):
        raise BrainAuditError("invalid bounded TCA identity or procedure")
    passes = profile["passes"]
    if type(passes) is not list or len(passes) != 2:
        raise BrainAuditError("TCA requires exactly two focused passes")
    for stage, identities, item in zip(_TCA_PROCEDURE["stages"],
                                        (_TCA_CONSTRUCT_IDS, _TCA_CHALLENGE_IDS), passes):
        _fields(item, {"id", "checks"}, "TCA pass")
        if item["id"] != stage or type(item["checks"]) is not list or len(item["checks"]) != len(identities):
            raise BrainAuditError("invalid TCA focused pass")
        for identity, check in zip(identities, item["checks"]):
            _fields(check, _LENS_FIELDS, "TCA instruction")
            if check["id"] != identity:
                raise BrainAuditError("unknown, duplicate or out-of-order TCA instruction")
            _text(check["check"], _TCA_BOUNDS["check_bytes"], "TCA instruction")
    _bounded(profile, 12288, "TCA profile")
    if _digest(profile) != {
        _TCA_ID: _TCA_DIGEST,
        _TCA_SUCCESSOR_ID: _TCA_SUCCESSOR_DIGEST,
        _TCA_V3_ID: _TCA_V3_DIGEST,
    }[profile["id"]]:
        raise BrainAuditError("unsupported TCA profile content")
    return profile


def _aoa_profile(value: dict[str, Any]) -> dict[str, Any]:
    profile = _fields(value, (_PROFILE_FIELDS - {"lenses"}) | {"passes"}, "AOA profile")
    if (type(profile["version"]) is not int or profile["version"] != 1
            or profile["applicable_flows"] != ["ARCHITECTURE"]
            or _json(profile["procedure"]) != _json(_AOA_PROCEDURE)
            or _json(profile["bounds"]) != _json(_AOA_BOUNDS)):
        raise BrainAuditError("invalid bounded AOA identity or procedure")
    passes = profile["passes"]
    if type(passes) is not list or len(passes) != 2:
        raise BrainAuditError("AOA requires exactly two architecture passes")
    for stage, identities, item in zip(_AOA_PROCEDURE["stages"], (_AOA_CONSTRUCT_IDS, _AOA_CHALLENGE_IDS), passes):
        _fields(item, {"id", "checks"}, "AOA pass")
        if item["id"] != stage or type(item["checks"]) is not list or len(item["checks"]) != len(identities):
            raise BrainAuditError("invalid AOA focused pass")
        for identity, check in zip(identities, item["checks"]):
            _fields(check, _LENS_FIELDS, "AOA instruction")
            if check["id"] != identity:
                raise BrainAuditError("unknown, duplicate or out-of-order AOA instruction")
            _text(check["check"], 2048, "AOA instruction")
    _bounded(profile, 12288, "AOA profile")
    if _digest(profile) != _AOA_DIGEST:
        raise BrainAuditError("unsupported AOA profile content")
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
    if len(encoded) > 65536:
        raise BrainAuditError("registry exceeds 65536 UTF-8 bytes")
    try:
        loaded = yaml.load(encoded.decode("utf-8", errors="strict"), Loader=_UniqueLoader)
    except (UnicodeError, yaml.YAMLError, ValueError, TypeError) as exc:
        raise BrainAuditError("invalid registry YAML") from exc
    registry = _fields(loaded, {"format", "version", "profiles"}, "registry")
    if registry["format"] != "AIOS_BRAIN_AUDIT_PROFILES" or type(registry["version"]) is not int or registry["version"] != 1:
        raise BrainAuditError("unknown audit-profile registry")
    profiles = registry["profiles"]
    if type(profiles) is not list or not 1 <= len(profiles) <= 10:
        raise BrainAuditError("registry requires one to ten recognized profiles")
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


def _specialized_profiles(*, successors: bool = False, tca3: bool = False) -> dict[str, dict[str, Any]]:
    """Finite complete mappings; registry order never selects a default."""
    if tca3:
        task_profile = (_TCA_V3_ID, 3, _TCA_V3_DIGEST)
        repair_profile = (_ROA_SUCCESSOR_ID, 3, _ROA_SUCCESSOR_DIGEST)
    elif successors:
        task_profile = (_TCA_SUCCESSOR_ID, 2, _TCA_SUCCESSOR_DIGEST)
        repair_profile = (_ROA_SUCCESSOR_ID, 3, _ROA_SUCCESSOR_DIGEST)
    else:
        task_profile = (_TCA_ID, 1, _TCA_DIGEST)
        repair_profile = (_ROA_ID, 2, _ROA_DIGEST)
    identities = {
        "ARCHITECTURE": (_AOA_ID, 1, _AOA_DIGEST),
        "TASK_AUTHORING": task_profile,
        "REMEDIATION_AUTHORING": (_MOA_ID, 1, _MOA_DIGEST),
        "REPAIR_AUTHORING": repair_profile,
    }
    return {flow: {"id": identity, "version": version, "digest": digest}
            for flow, (identity, version, digest) in identities.items()}


def normalize_audit_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Check a complete prospective policy's shape/identities, never its authority.

    These are the existing registry identities, not new profiles or activation
    authority. The caller must independently authenticate canonical Human/Brain
    policy and its currentness at admission and continuation.
    """
    policy = _fields(_normal(value, depth=0, max_depth=8), {
        "format", "version", "state", "main_sha", "work_context_fingerprint",
        "human_authority_digest", "brain_authority_digest", "r5_publication_sha", "profiles",
    }, "prospective audit policy")
    _bounded(policy, 4096, "prospective audit policy")
    if (policy["format"] != "AIOS_BRAIN_AUDIT_POLICY" or type(policy["version"]) is not int
            or policy["version"] != 1 or policy["state"] not in ("ADAPTIVE_V1", "FOUR_SPECIALIZED")):
        raise BrainAuditError("unsupported prospective audit policy")
    _git_sha(policy["main_sha"], "policy canonical main")
    for key in ("work_context_fingerprint", "human_authority_digest", "brain_authority_digest"):
        _sha(policy[key], key)
    if policy["r5_publication_sha"] is not None:
        _git_sha(policy["r5_publication_sha"], "reviewed R5 publication")
    if policy["state"] == "FOUR_SPECIALIZED" and policy["r5_publication_sha"] is None:
        raise BrainAuditError("future cutover requires separately authenticated reviewed R5 publication")
    expected = _specialized_profiles()
    successor = _specialized_profiles(successors=True)
    tca3 = _specialized_profiles(tca3=True)
    if policy["state"] == "ADAPTIVE_V1":
        expected = {flow: {"id": _ADAPTIVE_ID, "version": 1, "digest": _ADAPTIVE_DIGEST}
                    for flow in expected}
    # Strict JSON comparison rejects bool-as-version and partial/mixed activation.
    if (_json(policy["profiles"]) != _json(expected)
            and (policy["state"] != "FOUR_SPECIALIZED"
                 or _json(policy["profiles"]) not in (_json(successor), _json(tca3)))):
        raise BrainAuditError("prospective policy must pin all four exact profile identities")
    return policy


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
                        v3=_has_conformance(policy) or policy["id"] in (_ROA_IDS | _TCA_IDS | {_MOA_ID}),
                        adaptive=policy["id"] in (_ROA_IDS | _TCA_IDS | {_ADAPTIVE_ID, _MOA_ID}))
    if type(candidate) is not dict:
        raise BrainAuditError("candidate must be a strict-JSON mapping")
    _bounded(candidate, policy["bounds"]["candidate_bytes"], "candidate")
    return candidate


def construct_stage1(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                     construct_candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Bind one normalized construct to one exact packet and profile."""
    body, policy = _packet(packet), _profile(profile)
    if policy["id"] in _ROA_IDS:
        raise BrainAuditError("ROA has one procedure and cannot construct a historical stage")
    if policy["id"] == _MOA_ID:
        raise BrainAuditError("MOA cannot construct a historical stage")
    if policy["id"] in _TCA_IDS:
        raise BrainAuditError("TCA cannot construct a historical stage")
    if policy["id"] == _AOA_ID:
        raise BrainAuditError("AOA cannot construct a historical stage")
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
    if policy["id"] in _ROA_IDS:
        raise BrainAuditError("ROA cannot reconcile a historical stage")
    if policy["id"] == _MOA_ID:
        raise BrainAuditError("MOA cannot reconcile a historical stage")
    if policy["id"] in _TCA_IDS:
        raise BrainAuditError("TCA cannot reconcile a historical stage")
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
    if (policy["id"] not in _ROA_IDS or body["selected_flow"] != "REPAIR_AUTHORING"
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
    if policy["id"] == _ROA_SUCCESSOR_ID and sources["authorization"] is not None:
        acceptance = contract.get("acceptance")
        # A completion strategy needs the entire original acceptance, not just
        # the subset the failed ResultPackage happened to claim.
        if (type(acceptance) is not list or not 1 <= len(acceptance) <= 256
                or any(type(c) is not dict or set(c) != {"id", "condition"}
                       or any(type(c[k]) is not str or not c[k].strip() or len(c[k].encode("utf-8")) > 16384
                              for k in ("id", "condition")) for c in acceptance)
                or len({c["id"] for c in acceptance}) != len(acceptance)):
            sources["authorization"] = None
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


def _moa_private(value: Any) -> None:
    """MOA support cannot be smuggled into a proposal or a semantic basis."""
    _normal(value, depth=0, max_depth=32, private=True, v3=True, adaptive=True)
    reserved = {"moasubject", "moasourcecontext", "draftcandidate", "challenges", "auditfingerprint"}
    if type(value) is dict:
        if value.get("format") in {"AIOS_MOA_SUBJECT", "AIOS_BRAIN_REMEDIATION_OUTCOME_AUDIT",
                                    "AIOS_ROA_SUBJECT", "AIOS_BRAIN_REPAIR_OUTCOME_AUDIT"}:
            raise BrainAuditError("nested outcome-audit support envelope")
        for key, item in value.items():
            if key.lower().replace("_", "").replace("-", "") in reserved:
                raise BrainAuditError("nested MOA support material")
            _moa_private(item)
    elif type(value) is list:
        for item in value:
            _moa_private(item)


def _moa_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in _FINDING_IDENTITY}


def _moa_source_context(value: Any) -> dict[str, Any]:
    # These are caller-supplied canonical projections, never provider replacements
    # or an authenticity assertion. The caller owns authenticated reads and freshness.
    context = {"reviews": []} if value is None else _normal(value, depth=0, max_depth=32)
    _fields(context, {"reviews"}, "MOA source context")
    _bounded(context, _MOA_BOUNDS["source_context_bytes"], "MOA source context")
    reviews = context["reviews"]
    if type(reviews) is not list or len(reviews) > 16:
        raise BrainAuditError("MOA source REVIEW count exceeds bound")
    for record in reviews:
        _fields(record, {"run", "review", "artifacts_sha", "review_decision_sha"}, "MOA source record")
        run = _fields(record["run"], {"run_id", "task", "head_sha", "kind", "prior_correction"}, "MOA source RUN")
        _text(run["run_id"], 256, "MOA source RUN identity")
        _fields(run["task"], {"id", "revision"}, "MOA source RUN TASK")
        if (type(run["task"]["id"]) is not str or type(run["task"]["revision"]) is not int
                or run["task"]["revision"] < 1):
            raise BrainAuditError("invalid MOA source RUN TASK identity")
        _git_sha(run["head_sha"], "MOA source RUN head")
        review = record["review"]
        fields = {"review_id", "reviewed_sha", "mode", "verdict", "acceptance", "findings"}
        _fields(review, fields | ({"prior_finding_id"} if type(review) is dict and "prior_finding_id" in review else set()),
                "MOA source REVIEW")
        if type(review["findings"]) is not list or len(review["findings"]) > 16:
            raise BrainAuditError("MOA source findings are unbounded")
        for finding in review["findings"]:
            _fields(finding, {"id", "basis", "action", "location", "issue", "expected"}, "MOA source finding")
        _moa_private(record)
    return context


def _moa_prior_matches(run: dict[str, Any], review: Any) -> bool:
    """Check available canonical projection lineage, without issuing authority."""
    prior = run["prior_correction"]
    if run["kind"] == "PRIMARY":
        return prior is None and review.mode == "PRIMARY"
    if (run["kind"] not in ("REPAIR", "REMEDIATION") or type(prior) is not dict
            or prior.get("kind") != run["kind"] or type(prior.get("authorization")) is not dict
            or type(prior.get("authorization_sha")) is not str
            or re.fullmatch(r"[0-9a-f]{40}", prior["authorization_sha"]) is None):
        return False
    authorization = prior["authorization"]
    if run["kind"] == "REPAIR":
        return (set(prior) == {"kind", "authorization_sha", "failed_run_id", "failed_head_sha", "authorization"}
                and type(prior["failed_run_id"]) is str
                and re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", prior["failed_run_id"]) is not None
                and prior["failed_run_id"] == authorization.get("failed_run_id")
                and prior["failed_head_sha"] == authorization.get("failed_head_sha")
                and type(authorization.get("failed_head_sha")) is str
                and re.fullmatch(r"[0-9a-f]{40}", authorization["failed_head_sha"]) is not None
                and review.mode == "PRIMARY")
    predecessor = prior.get("predecessor")
    return (set(prior) == {"kind", "authorization_sha", "predecessor", "authorization"}
            and type(predecessor) is dict and set(predecessor) == set(_FINDING_IDENTITY)
            and all(type(predecessor[k]) is str and predecessor[k].strip() for k in _FINDING_IDENTITY)
            and re.fullmatch(r"[0-9a-f]{40}", predecessor["reviewed_sha"]) is not None
            and predecessor["finding_id"] == authorization.get("finding_id") == review.prior_finding_id
            and predecessor["reviewed_sha"] == authorization.get("reviewed_sha")
            and review.mode == "DELTA")


def construct_moa_subject(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                          return_contract_ref: Mapping[str, Any], external_bindings: Mapping[str, Any],
                          source_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bind existing REMEDIATION subjects to bounded, explicitly supplied sources.

    This function reads no Git and authenticates no source. Missing/conflicting
    facts produce explicit unavailable roles; a provider cannot fill them in.
    No frontier selector or Decision Packet extension is introduced.
    """
    from .review import ReviewValidationError, parse_review

    body, policy = _packet(packet), _profile(profile)
    if (policy["id"] != _MOA_ID or body["selected_flow"] != "REMEDIATION_AUTHORING"
            or body["authority_owner"] != "BRAIN"
            or body["decision_family_ref"] != "review.validate_remediation"
            or body["handoff_target"] != "AUTHORING_INGRESS"
            or body["expected_return_shape"] != "REMEDIATION_CONTRACT_PROPOSAL"
            or body["requires_fresh_context_for_continuation"] is not False):
        raise BrainAuditError("MOA requires the exact Brain REMEDIATION_AUTHORING boundary")
    reference = _fields(_normal(return_contract_ref, depth=0, max_depth=32),
                        {"selected_flow", "version", "digest"}, "MOA return contract reference")
    if (reference["selected_flow"] != "REMEDIATION_AUTHORING"
            or type(reference["version"]) is not int or reference["version"] != 1):
        raise BrainAuditError("MOA return contract mismatch")
    _sha(reference["digest"], "MOA return contract digest")
    bindings = _fields(_normal(external_bindings, depth=0, max_depth=32), set(), "MOA external bindings")
    facts, target = body["canonical_facts"], body["subject"]
    if type(facts) is not dict or type(target) is not dict:
        raise BrainAuditError("MOA canonical subject is missing")
    task = _fields(facts.get("selected_task"), {"id", "revision"}, "MOA TASK identity")
    if (type(task["id"]) is not str or re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task["id"]) is None
            or type(task["revision"]) is not int or task["revision"] < 1):
        raise BrainAuditError("invalid MOA TASK identity")
    _git_sha(facts.get("main_sha"), "MOA source main")
    _sha(body["work_context_fingerprint"], "MOA work context")
    kind = target.get("kind")
    if kind == "UNIQUE_FINDING":
        _fields(target, _MOA_FINDING_FIELDS | {"kind"}, "MOA unique finding")
        findings = [{k: v for k, v in target.items() if k != "kind"}]
    elif kind == "CORRECTION_FRONTIER":
        _fields(target, {"kind", "execution_base", "findings"}, "MOA frontier")
        findings = target["findings"]
        if (type(findings) is not list or not 2 <= len(findings) <= 16
                or type(target["execution_base"]) is not dict or not target["execution_base"]):
            raise BrainAuditError("MOA frontier must be a bounded unselected set")
    else:
        raise BrainAuditError("unknown MOA subject kind")
    for finding in findings:
        _fields(finding, _MOA_FINDING_FIELDS, "MOA finding")
        for key in _MOA_FINDING_FIELDS - {"reviewed_sha"}:
            _text(finding[key], 2048, f"MOA finding {key}")
        _git_sha(finding["reviewed_sha"], "MOA reviewed source")
        if finding["action"] not in ("CODE_FIX", "EVIDENCE_ONLY"):
            raise BrainAuditError("unknown MOA finding action")
        _moa_private(finding)
    identities = [_moa_identity(f) for f in findings]
    if len({_json(i) for i in identities}) != len(identities):
        raise BrainAuditError("duplicate MOA finding identity")
    context = _moa_source_context(source_context)
    sources: dict[str, Any] = {"finding": _roa_source("decision_packet.subject", target),
                               "review": None, "task": None, "lineage": None}
    blockers = {"review": "Exact canonical RUN/CHANGES_REQUIRED REVIEW material is missing or conflicting.",
                "task": "Original TASK scope, constraints or verification authority is missing or conflicting.",
                "lineage": "Current canonical correction provenance/outstanding status is missing or conflicting."}
    contract = facts.get("task_contract")
    if (type(contract) is dict and contract.get("task_id") == task["id"]
            and type(contract.get("revision")) is int and contract["revision"] == task["revision"]
            and type(contract.get("scope")) is dict and type(contract["scope"].get("modify")) is list
            and all(type(p) is str and p for p in contract["scope"]["modify"])
            and type(contract.get("constraints")) is dict and type(contract["constraints"].get("hard")) is list
            and all(type(c) is str and c.strip() for c in contract["constraints"]["hard"])
            and type(contract.get("verification")) is dict
            and contract["verification"].get("policy") in ("minimum-sufficient-v1", "minimum-sufficient-v2")
            and type(contract["verification"].get("required")) is list and contract["verification"]["required"]
            and all(type(c) is str and c.strip() for c in contract["verification"]["required"])
            and type(contract.get("acceptance")) is list and contract["acceptance"]
            and all(type(a) is dict and type(a.get("id")) is str and a["id"].strip()
                    for a in contract["acceptance"])):
        sources["task"] = _roa_source("decision_packet.canonical_facts.task_contract", contract)
        blockers.pop("task")
    correction = facts.get("correction_subject")
    outstanding = correction.get("outstanding_reviews") if type(correction) is dict else None
    lineage_ok = (
        type(correction) is dict and correction.get("policy") == "EXACT_CORRECTION_SUBJECT_LINEAGE_V1"
        and correction.get("operation") == "AUTHOR_REMEDIATION" and _json(correction.get("task")) == _json(task)
        and facts.get("canonical_next_action") == facts.get("unified_state_next_action") == "AUTHOR_REMEDIATION"
        and body["selection_basis"] == "UNIFIED_STATE" and body["canonical_blocker"] is None
        and type(outstanding) is list and len(outstanding) == len(identities)
    )
    if lineage_ok:
        for entry in outstanding:
            _fields(entry, set(_FINDING_IDENTITY) | {"review_decision_sha"}, "MOA outstanding REVIEW")
            _git_sha(entry["review_decision_sha"], "MOA outstanding REVIEW ref")
        lineage_ok = ({_json(_moa_identity(i)) for i in outstanding} == {_json(i) for i in identities}
                      and len({_json(_moa_identity(i)) for i in outstanding}) == len(outstanding))
        selected = {k: correction.get(k) for k in _FINDING_IDENTITY}
        lineage_ok = lineage_ok and selected in identities
        if kind == "UNIQUE_FINDING":
            lineage_ok = lineage_ok and selected == identities[0]
        lineage_ok = (lineage_ok and correction.get("selectors") == {
            "source_run_id": selected["source_run_id"], "finding_id": selected["finding_id"]})
        if lineage_ok:
            _git_sha(correction.get("artifacts_sha"), "MOA source artifacts")
            _git_sha(correction.get("review_decision_sha"), "MOA source REVIEW ref")
            lineage_ok = any(_moa_identity(i) == selected and i["review_decision_sha"] == correction["review_decision_sha"]
                             for i in outstanding)
    if lineage_ok:
        sources["lineage"] = _roa_source("decision_packet.canonical_facts.correction_subject", correction)
        blockers.pop("lineage")
    # Use the established REVIEW parser, without assuming that parsing issues a
    # canonical REVIEW or repeats ingress's source-family authorization checks.
    records = context["reviews"]
    review_ok = bool(records) and lineage_ok and sources["task"] is not None
    seen: set[str] = set()
    for record in records:
        run, review = record["run"], record["review"]
        relevant = [f for f in findings if f["source_run_id"] == run["run_id"]]
        try:
            parsed = parse_review(_json(review).decode("utf-8"))
        except (ReviewValidationError, ValueError, TypeError):
            review_ok = False
            continue
        acceptance_ids = {a.get("id") for a in contract["acceptance"] if type(a) is dict} if sources["task"] else set()
        prior = run["prior_correction"]
        prior_ok = _moa_prior_matches(run, parsed)
        if body["prior_semantic_decisions"] is not None:
            prior_ok = prior_ok and _json(prior) == _json(body["prior_semantic_decisions"])
        if (not relevant or run["run_id"] in seen or _json(run["task"]) != _json(task) or not prior_ok
                or parsed.verdict != "CHANGES_REQUIRED" or parsed.reviewed_sha != run["head_sha"]
                or any(f.basis not in acceptance_ids or parsed.acceptance.get(f.basis) != "FAIL" for f in parsed.findings)
                or set(parsed.acceptance) - acceptance_ids):
            review_ok = False
        seen.add(run["run_id"])
        for finding in relevant:
            expected = {"id": finding["finding_id"], **{k: finding[k] for k in ("basis", "action", "location", "issue", "expected")}}
            if (parsed.review_id != finding["review_id"] or parsed.reviewed_sha != finding["reviewed_sha"]
                    or sum(f == expected for f in review["findings"]) != 1
                    or not lineage_ok or not any(_moa_identity(i) == _moa_identity(finding)
                        and i["review_decision_sha"] == record["review_decision_sha"] for i in outstanding)):
                review_ok = False
        if lineage_ok and run["run_id"] == correction["source_run_id"]:
            if (record["artifacts_sha"] != correction["artifacts_sha"]
                    or record["review_decision_sha"] != correction["review_decision_sha"]):
                review_ok = False
        _git_sha(record["artifacts_sha"], "MOA supplied artifacts")
        _git_sha(record["review_decision_sha"], "MOA supplied REVIEW ref")
    if review_ok and seen == {f["source_run_id"] for f in findings}:
        sources["review"] = _roa_source("moa_source_context.reviews", records)
        blockers.pop("review")
    if kind == "CORRECTION_FRONTIER":
        blockers["target"] = "Unselected frontier: await a separate exact selected canonical subject; no implicit target."
    result = {
        "format": "AIOS_MOA_SUBJECT", "version": 1, "selected_flow": "REMEDIATION_AUTHORING",
        "packet_fingerprint": body["packet_fingerprint"], "work_context_fingerprint": body["work_context_fingerprint"],
        "audit_profile_ref": profile_ref(policy), "return_contract_ref": reference, "external_bindings": bindings,
        "task": task, "kind": kind, "findings": findings, "sources": sources, "blockers": blockers,
    }
    result["subject_fingerprint"] = _digest(result)
    return result


def _moa_bound(packet: Any, profile: Any, subject: Any, source_context: Any) -> tuple[dict, dict, dict]:
    body, policy = _packet(packet), _profile(profile)
    supplied = _normal(subject, depth=0, max_depth=32)
    if type(supplied) is not dict:
        raise BrainAuditError("MOA subject required")
    bound = construct_moa_subject(body, policy, supplied.get("return_contract_ref"),
                                  supplied.get("external_bindings"), source_context)
    if _json(bound) != _json(supplied):
        raise BrainAuditError("MOA subject binding mismatch")
    return body, policy, bound


def _moa_checks(value: Any, bound: dict[str, Any], *, challenge: bool) -> list[dict[str, Any]]:
    identities = _MOA_CHALLENGE_IDS if challenge else _MOA_CONSTRUCT_IDS
    roles = (("review", "lineage"), ("finding", "review"), ("review", "task"), ("task", "lineage")) if challenge else (
        ("finding", "review", "lineage"), ("finding", "review"), ("task", "lineage"))
    if type(value) is not list or len(value) != len(identities):
        raise BrainAuditError("MOA requires exact focused check coverage")
    allowed = [_moa_identity(f) for f in bound["findings"]]
    sources = bound["sources"]
    for identity, required, check in zip(identities, roles, value):
        fields = {"id", "outcome", "knowledge", "basis", "evidence_refs", "finding_refs"}
        _fields(check, fields | ({"counterexample", "disposition"} if challenge else set()), "MOA check")
        _moa_private(check)
        if (check["id"] != identity or check["outcome"] not in ("CLEAR", "BLOCKER")
                or check["knowledge"] not in ("OBSERVED", "INFERRED", "UNKNOWN")):
            raise BrainAuditError("invalid or out-of-order MOA judgment")
        _text(check["basis"], _MOA_BOUNDS["basis_bytes"], "MOA basis")
        refs, findings = check["evidence_refs"], check["finding_refs"]
        if (type(refs) is not list or len(refs) > _MOA_BOUNDS["evidence_refs_per_check"]
                or len({_json(r) for r in refs}) != len(refs)
                or any(r not in [s for s in sources.values() if s is not None] for r in refs)):
            raise BrainAuditError("forged, duplicate or unbounded MOA evidence reference")
        if (type(findings) is not list or not 1 <= len(findings) <= _MOA_BOUNDS["finding_refs_per_check"]
                or len({_json(f) for f in findings}) != len(findings) or any(f not in allowed for f in findings)):
            raise BrainAuditError("MOA check includes unauthorized or duplicate findings")
        if identity in (_MOA_CONSTRUCT_IDS[0], _MOA_CHALLENGE_IDS[0]) and {_json(f) for f in findings} != {_json(f) for f in allowed}:
            raise BrainAuditError("MOA source check must cover the exact outstanding set")
        missing = any(sources[r] is None for r in required)
        if challenge and identity == _MOA_CHALLENGE_IDS[-1] and bound["kind"] == "CORRECTION_FRONTIER":
            missing = True
        if missing and (check["knowledge"] != "UNKNOWN" or check["outcome"] != "BLOCKER"):
            raise BrainAuditError("missing decisive MOA source/target requires UNKNOWN blocker")
        if check["knowledge"] == "UNKNOWN":
            if check["outcome"] != "BLOCKER":
                raise BrainAuditError("UNKNOWN cannot discharge a MOA check")
        elif any(sources[r] not in refs for r in required):
            raise BrainAuditError("MOA judgment lacks discriminating source references")
        if challenge:
            _text(check["counterexample"], _MOA_BOUNDS["basis_bytes"], "MOA counterexample")
            if (check["disposition"] not in _MOA_PROCEDURE["challenge_dispositions"]
                    or (check["outcome"] == "BLOCKER") != (check["disposition"] == "BLOCKER")):
                raise BrainAuditError("MOA counterexample disposition contradicts judgment")
    return value


def construct_moa(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                  subject: Mapping[str, Any], semantic_output: Mapping[str, Any],
                  source_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """First focused pass: a draft only when source/cause/authority checks clear."""
    _, policy, bound = _moa_bound(packet, profile, subject, source_context)
    material = _fields(_normal(semantic_output, depth=0, max_depth=32),
                       {"subject_fingerprint", "checks", "draft_candidate"}, "MOA construct")
    _bounded(material, policy["bounds"]["outcome_bytes"], "MOA construct")
    if material["subject_fingerprint"] != bound["subject_fingerprint"]:
        raise BrainAuditError("stale MOA construct subject")
    checks = _moa_checks(material["checks"], bound, challenge=False)
    draft = material["draft_candidate"]
    if draft is not None:
        if bound["kind"] != "UNIQUE_FINDING" or bound["blockers"]:
            raise BrainAuditError("MOA frontier/missing authority cannot expose a speculative draft")
        if any(c["outcome"] == "BLOCKER" for c in checks):
            raise BrainAuditError("MOA first-pass blocker cannot expose a speculative draft")
        draft = _candidate(draft, policy)
        _moa_private(draft)
    result = {"format": "AIOS_BRAIN_REMEDIATION_OUTCOME_AUDIT", "version": 1, "stage": "CONSTRUCT",
              "subject_fingerprint": bound["subject_fingerprint"], "checks": checks, "draft_candidate": draft}
    result["construct_fingerprint"] = _digest(result)
    return result


def _moa_construct(packet: Any, profile: Any, subject: Any, value: Any, source_context: Any) -> dict[str, Any]:
    first = _fields(_normal(value, depth=0, max_depth=32), _MOA_CONSTRUCT_FIELDS, "MOA first pass")
    expected = construct_moa(packet, profile, subject,
                             {k: first[k] for k in ("subject_fingerprint", "checks", "draft_candidate")}, source_context)
    if _json(first) != _json(expected):
        raise BrainAuditError("MOA construct lineage mismatch")
    return expected


def _moa_candidate(value: Any, policy: dict[str, Any], body: dict[str, Any], bound: dict[str, Any]) -> dict[str, Any]:
    from .review import ReviewValidationError, parse_remediation

    candidate = _fields(_candidate(value, policy), {
        "finding_id", "action", "reviewed_sha", "modification_scope", "verification", "constraints",
    }, "MOA final REMEDIATION proposal")
    _moa_private(candidate)
    if bound["kind"] != "UNIQUE_FINDING" or bound["blockers"]:
        raise BrainAuditError("MOA requires an exact authorized unique finding")
    finding = bound["findings"][0]
    if any(candidate[k] != finding[k] for k in ("finding_id", "action", "reviewed_sha")):
        raise BrainAuditError("MOA candidate finding/action/reviewed SHA mismatch")
    contract = body["canonical_facts"]["task_contract"]
    scope = candidate["modification_scope"]
    if (type(scope) is not list or any(type(p) is not str for p in scope)
            or len(scope) != len(set(scope)) or not set(scope) <= set(contract["scope"]["modify"])
            or candidate["action"] == "EVIDENCE_ONLY" and scope
            or candidate["action"] == "CODE_FIX" and not scope):
        raise BrainAuditError("MOA candidate exceeds TASK/action scope")
    constraints = _fields(candidate["constraints"], {"hard"}, "MOA original constraints")["hard"]
    if _json(constraints) != _json(contract["constraints"]["hard"]):
        raise BrainAuditError("MOA candidate changes original constraints")
    verification = candidate["verification"]
    fields = {"policy", "affected"}
    _fields(verification, fields | ({"full_suite_reason"} if type(verification) is dict and "full_suite_reason" in verification else set()),
            "MOA affected verification")
    affected = verification["affected"]
    original = contract["verification"]
    if (verification["policy"] != original["policy"] or type(affected) is not list or not affected
            or any(type(c) is not str for c in affected) or len(affected) != len(set(affected))
            or not set(affected) <= set(original["required"])
            or verification.get("full_suite_reason") != original.get("full_suite_reason")):
        raise BrainAuditError("MOA candidate changes original verification authority")
    try:
        parse_remediation(_json(candidate).decode("utf-8"))
    except (ReviewValidationError, ValueError, TypeError) as exc:
        raise BrainAuditError("invalid MOA remediation family proposal") from exc
    return candidate


def validate_moa(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                 subject: Mapping[str, Any], stage1: Mapping[str, Any], semantic_output: Mapping[str, Any],
                 source_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Independent finding-closure/non-interference challenge and one final closure."""
    body, policy, bound = _moa_bound(packet, profile, subject, source_context)
    first = _moa_construct(body, policy, bound, stage1, source_context)
    material = _fields(_normal(semantic_output, depth=0, max_depth=32), {
        "subject_fingerprint", "construct_fingerprint", "challenges", "closure", "reconciled_candidate",
    }, "MOA second pass")
    _bounded(material, policy["bounds"]["outcome_bytes"], "MOA second pass")
    if (material["subject_fingerprint"] != bound["subject_fingerprint"]
            or material["construct_fingerprint"] != first["construct_fingerprint"]):
        raise BrainAuditError("MOA second-pass lineage mismatch")
    challenges = _moa_checks(material["challenges"], bound, challenge=True)
    closure = _fields(material["closure"], {"outcome", "knowledge", "summary", "candidate_fingerprint"}, "MOA final closure")
    _text(closure["summary"], policy["bounds"]["closure_summary_bytes"], "MOA closure summary")
    _moa_private(closure["summary"])
    # The fixed envelope has no explicit disposition for first-pass blockers.
    # They remain unresolved in this lineage even when later challenges are CLEAR.
    checks = [*first["checks"], *challenges]
    outcome = "NO_DECISION" if any(c["outcome"] == "BLOCKER" for c in checks) else "CANDIDATE"
    knowledge = ("UNKNOWN" if any(c["knowledge"] == "UNKNOWN" for c in checks) else
                 "INFERRED" if any(c["knowledge"] == "INFERRED" for c in checks) else "OBSERVED")
    if closure["outcome"] != outcome or closure["knowledge"] != knowledge:
        raise BrainAuditError("MOA final closure contradicts focused passes")
    candidate = material["reconciled_candidate"]
    if outcome == "NO_DECISION":
        if candidate is not None or closure["candidate_fingerprint"] is not None:
            raise BrainAuditError("MOA blocker cannot expose a final remediation")
    else:
        candidate = _moa_candidate(candidate, policy, body, bound)
        changed = _json(candidate) != _json(first["draft_candidate"])
        addressed = any(c["disposition"] == "ADDRESSED_BY_RECONCILIATION" for c in challenges)
        if changed != addressed or closure["candidate_fingerprint"] != _digest(candidate):
            raise BrainAuditError("MOA reconciliation/closure does not bind the final candidate")
    result = {"format": "AIOS_BRAIN_REMEDIATION_OUTCOME_AUDIT", "version": 1,
              "stage": "ADVERSARIAL_AUDIT_AND_RECONCILE", "subject_fingerprint": bound["subject_fingerprint"],
              "construct_fingerprint": first["construct_fingerprint"], "challenges": challenges,
              "closure": closure, "handoff_candidate": candidate}
    result["audit_fingerprint"] = _digest(result)
    return result


def _tca_private(value: Any) -> None:
    """Apply existing privacy floors plus TCA/option support exclusions locally."""
    _moa_private(value)
    if type(value) is dict:
        if value.get("format") in {"AIOS_TCA_SUBJECT", "AIOS_BRAIN_TASK_CONTRACT_AUDIT"}:
            raise BrainAuditError("nested TCA support envelope")
        for key, item in value.items():
            if key.lower().replace("_", "").replace("-", "") in {
                "tcasubject", "tasksourcecontext", "humanv1option", "acceptancechecks",
                "consumerchecks", "acceptanceaccounting", "reconciliation",
            }:
                raise BrainAuditError("nested TCA/option support material")
            _tca_private(item)
    elif type(value) is list:
        for item in value:
            _tca_private(item)


def _tca_texts(value: Any, count: int, name: str) -> list[str]:
    if type(value) is not list or len(value) > count:
        raise BrainAuditError(f"unbounded {name}")
    for item in value:
        _text(item, 2048, name)
    if len(value) != len(set(value)):
        raise BrainAuditError(f"duplicate {name}")
    return value


def _tca_task(value: Any) -> dict[str, Any]:
    from .task import TaskValidationError, validate_task

    task = _normal(value, depth=0, max_depth=32)
    _tca_private(task)
    _bounded(task, _TCA_BOUNDS["candidate_bytes"], "TCA TASK")
    try:
        validate_task(task)
    except (TaskValidationError, TypeError, ValueError) as exc:
        raise BrainAuditError("invalid TCA TASK proposal") from exc
    if not 1 <= len(task["acceptance"]) <= _TCA_BOUNDS["acceptance_count"]:
        raise BrainAuditError("unbounded TCA acceptance")
    for entry in task["acceptance"]:
        _text(entry["id"], 256, "TCA acceptance id")
        _text(entry["condition"], 2048, "TCA acceptance condition")
    if task["verification"].get("policy") != "minimum-sufficient-v1":
        raise BrainAuditError("TCA requires the current TASK verification policy")
    return task


_TCA_CONSUMER_CATEGORIES = (
    "DIRECT_CALLER",
    "DIRECT_CALL_API",
    "TRANSITIVE_CALLER",
    "TRANSITIVE_DIRECT_CALL",
    "GIT_SUBPROCESS",
    "GIT_FRAMING_OBJECT_FETCH",
    "TEST_DOUBLE",
    "AST_GUARD",
    "LEGACY_TEST_DOUBLE_AST_GUARD",
    "MIXED_VERIFICATION_SURFACE",
)
_TCA_SOURCE_ROLES = (
    "IMPLEMENTATION",
    "TRANSITIVE_CALLER",
    "TRANSITIVE_CONSUMER",
    "GIT_SUBPROCESS",
    "GIT_FRAMING_OBJECT_FETCH",
    "TEST_DOUBLE",
    "AST_GUARD",
    "LEGACY_TEST_AST_GUARD",
)
_TCA_COUNTEREXAMPLE_KINDS = (
    "VACUOUS_CRITERION",
    "TRANSITIVE_CALLER",
    "SCOPE_CREEP",
    "AUTHORITY_INVERSION",
    "TEST_DOUBLE_DRIFT",
    "STALE_SOURCE_PACKET",
    "MISSING_FINAL_ACCEPTANCE_CLAIM",
    "NON_EMPTY_UNRESOLVED_OBLIGATION",
    "BAD_RESULT_PACKAGE_OR_API_ASSUMPTION",
    "MISSING_MODIFY_AUTHORITY",
    "CONFLATED_FAILURE_CAUSE",
    "MIXED_VERIFICATION_MISMATCH",
    "PRE_RUN_DIRTY_OR_NATIVE_BOUNDARY",
)


def _tca_source_context(value: Any) -> dict[str, Any]:
    # Caller supplies authenticated canonical projections before invocation.
    # These hashes bind content; they do not authenticate Human authorization.
    context = {"task": None, "authorization": None, "consumers": []} if value is None else _normal(
        value, depth=0, max_depth=32)
    if type(context) is dict and "executor_claims" in context:
        raise BrainAuditError("TCA source context cannot include pre-execution executor_claims")
    optional_context_keys = {"source_material", "baseline_observations"} & set(context) if type(context) is dict else set()
    _fields(context, {"task", "authorization", "consumers"} | optional_context_keys, "TCA source context")
    _bounded(context, _TCA_BOUNDS["source_context_bytes"], "TCA source context")
    _tca_private(context)
    if context["task"] is not None:
        context["task"] = _tca_task(context["task"])
    auth = context["authorization"]
    if auth is not None:
        from .task import TaskValidationError, _exact_repo_path
        _fields(auth, {"scope", "constraints", "non_goals"}, "TCA authorization")
        _fields(auth["scope"], {"inspect", "modify"}, "TCA authorized scope")
        for key in ("inspect", "modify"):
            paths = _tca_texts(auth["scope"][key], 256, "TCA authorized paths")
            if key == "modify":
                try:
                    for path in paths:
                        _exact_repo_path(path, "TCA scope.modify")
                except TaskValidationError as exc:
                    raise BrainAuditError("unbounded TCA mutation scope") from exc
        _fields(auth["constraints"], {"hard"}, "TCA authorized constraints")
        _tca_texts(auth["constraints"]["hard"], 256, "TCA authorized constraints")
        _tca_texts(auth["non_goals"], 256, "TCA authorized non-goals")
    consumers = context["consumers"]
    if type(consumers) is not list or len(consumers) > _TCA_BOUNDS["consumer_count"]:
        raise BrainAuditError("unbounded TCA consumers")
    seen = set()
    for consumer in consumers:
        from .task import TaskValidationError, _exact_repo_path
        extra_consumer_keys = (
            {"kind", "path", "symbol", "category", "required_modify_paths", "required_source_paths"} & set(consumer)
            if type(consumer) is dict else set()
        )
        _fields(consumer, {"id", "authority", "requirement"} | extra_consumer_keys, "TCA declared consumer")
        identity = _text(consumer["id"], 256, "TCA consumer id")
        _text(consumer["requirement"], 2048, "TCA consumer requirement")
        if identity in seen or consumer["authority"] not in (
                "HUMAN", "BRAIN", "EXECUTOR", "RUNTIME", "REVIEWER", "PUBLISHER", "TRANSPORT"):
            raise BrainAuditError("duplicate consumer or invalid consumer authority")
        if "kind" in consumer and consumer["kind"] not in _TCA_CONSUMER_CATEGORIES:
            raise BrainAuditError("invalid TCA consumer kind")
        if "category" in consumer and consumer["category"] not in _TCA_CONSUMER_CATEGORIES:
            raise BrainAuditError("invalid TCA consumer category")
        if "path" in consumer:
            try:
                _exact_repo_path(consumer["path"], "TCA consumer path")
            except TaskValidationError as exc:
                raise BrainAuditError("invalid TCA consumer path") from exc
        if "symbol" in consumer:
            _text(consumer["symbol"], 256, "TCA consumer symbol")
        for path_key in ("required_modify_paths", "required_source_paths"):
            if path_key in consumer:
                req_paths = _tca_texts(consumer[path_key], 64, f"TCA consumer {path_key}")
                try:
                    for req_path in req_paths:
                        _exact_repo_path(req_path, f"TCA consumer {path_key}")
                except TaskValidationError as exc:
                    raise BrainAuditError(f"invalid TCA consumer {path_key}") from exc
        seen.add(identity)
    # Optional for historical replay; mandatory decisive source for tca-2 and tca-3.
    material = context.get("source_material", [])
    if type(material) is not list or len(material) > 16:
        raise BrainAuditError("unbounded TCA source material")
    paths = set()
    for source in material:
        from .task import TaskValidationError, _exact_repo_path
        extra_source_keys = (
            {"kind", "role", "symbols", "call_sites", "failure_causes"} & set(source)
            if type(source) is dict else set()
        )
        _fields(source, {"path", "content"} | extra_source_keys, "TCA source excerpt")
        try:
            _exact_repo_path(source["path"], "TCA source path")
        except TaskValidationError as exc:
            raise BrainAuditError("invalid TCA source path") from exc
        _text(source["content"], 16384, "TCA source content")
        if "kind" in source and source["kind"] not in _TCA_SOURCE_ROLES:
            raise BrainAuditError("invalid TCA source kind")
        if "role" in source and source["role"] not in _TCA_SOURCE_ROLES:
            raise BrainAuditError("invalid TCA source role")
        if "symbols" in source:
            _tca_texts(source["symbols"], 64, "TCA source symbols")
        if "call_sites" in source:
            _tca_texts(source["call_sites"], 64, "TCA source call_sites")
        if "failure_causes" in source:
            causes = _tca_texts(source["failure_causes"], 16, "TCA source failure causes")
            if not causes or any("::" in cause for cause in causes):
                raise BrainAuditError("TCA source failure causes cannot be empty or treat a pytest node as a complete cause")
        if (source["path"] in paths or auth is None
                or source["path"] not in auth["scope"]["inspect"] + auth["scope"]["modify"]):
            raise BrainAuditError("TCA source path is duplicate or outside inspect/modify authority")
        paths.add(source["path"])
    if "baseline_observations" in context:
        observations = context["baseline_observations"]
        if type(observations) is not list or len(observations) > 32:
            raise BrainAuditError("unbounded TCA baseline observations")
        seen_commands = set()
        seen_ids = set()
        for obs in observations:
            extra_obs_keys = {"id", "summary", "basis"} & set(obs) if type(obs) is dict else set()
            _fields(obs, {"command", "status"} | extra_obs_keys, "TCA baseline observation")
            if "summary" not in obs and "basis" not in obs:
                raise BrainAuditError("TCA baseline observation requires summary or basis")
            if "id" in obs:
                obs_id = _text(obs["id"], 256, "TCA baseline observation id")
                if obs_id in seen_ids:
                    raise BrainAuditError("duplicate TCA baseline observation id")
                seen_ids.add(obs_id)
            command = _text(obs["command"], 512, "TCA baseline command")
            if "summary" in obs:
                _text(obs["summary"], 2048, "TCA baseline summary")
            if "basis" in obs:
                _text(obs["basis"], 2048, "TCA baseline basis")
            if command in seen_commands or obs["status"] not in ("PASS", "FAIL", "NOT_RUN", "UNKNOWN"):
                raise BrainAuditError("duplicate or invalid TCA baseline observation")
            seen_commands.add(command)
    return context


def construct_tca_subject(packet: DecisionPacket | Mapping[str, Any], profile: Mapping[str, Any],
                          return_ref: Mapping[str, Any], external_bindings: Mapping[str, Any],
                          source_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bind one explicit TASK subject to supplied intent, authority and source content."""
    body, policy = _packet(packet), _profile(profile)
    if (policy["id"] not in _TCA_IDS or body["selected_flow"] != "TASK_AUTHORING"
            or body["authority_owner"] != "BRAIN" or body["subject"] != {"kind": "TASK_AUTHORING"}
            or body["selection_basis"] not in ("EXPLICIT_SELECTOR", "UNIQUE_UNAUTHORED_NEXT")
            or body["decision_family_ref"] != "task.validate_task+authoring_ingress"
            or body["handoff_target"] != "AUTHORING_INGRESS"
            or body["expected_return_shape"] != "TASK_AUTHORING_PROPOSAL"):
        raise BrainAuditError("TCA applies only to the exact TASK_AUTHORING subject")
    if policy["id"] == _TCA_V3_ID and body["executor_claims"] is not None:
        raise BrainAuditError("TCA-3 TASK_AUTHORING cannot invent pre-execution Executor claims")
    _sha(body["work_context_fingerprint"], "TCA work-context fingerprint")
    ref = _fields(_normal(return_ref, depth=0, max_depth=32),
                  {"selected_flow", "version", "digest"}, "TCA return reference")
    if ref["selected_flow"] != "TASK_AUTHORING" or type(ref["version"]) is not int or ref["version"] != 1:
        raise BrainAuditError("TCA return identity mismatch")
    _sha(ref["digest"], "TCA return digest")
    bindings = _fields(_normal(external_bindings, depth=0, max_depth=32), {"task_id", "revision"}, "TCA bindings")
    if (type(bindings["task_id"]) is not str or re.fullmatch(r"TASK-[0-9]{3,}", bindings["task_id"]) is None
            or type(bindings["revision"]) is not int or bindings["revision"] < 1):
        raise BrainAuditError("invalid TCA TASK identity")
    context = _tca_source_context(source_context)
    task = context["task"]
    if task is not None and any(task[k] != bindings[k] for k in bindings):
        raise BrainAuditError("TCA source TASK identity mismatch")
    facts = body["canonical_facts"]
    if type(facts) is not dict:
        raise BrainAuditError("invalid TCA canonical snapshot")
    for value in (body["human_input"], facts, body["bounded_observations"],
                  body["executor_claims"], body["prior_semantic_decisions"]):
        _tca_private(value)
    intent = body["human_input"]
    if intent is not None:
        _text(intent, 16384, "TCA Human intent")
    canonical_available = (type(facts.get("repository")) is str and bool(facts["repository"].strip())
                           and type(facts.get("main_sha")) is str
                           and re.fullmatch(r"[0-9a-f]{40}", facts["main_sha"]) is not None
                           and type(facts.get("roadmap")) is dict and body["canonical_blocker"] is None)
    canonical_available = (canonical_available and body["pending_canonical_obligation"] is None
                           and body["pending_canonical_authority_owner"] is None)
    sources = {
        "intent": None if intent is None else _roa_source("human_input", intent),
        "task": None if task is None else _roa_source("task_source_context.task", task),
        "authorization": None if context["authorization"] is None else _roa_source(
            "task_source_context.authorization", context["authorization"]),
        "consumers": None if not context["consumers"] else _roa_source("task_source_context.consumers", context["consumers"]),
        "canonical": _roa_source("canonical_facts", facts) if canonical_available else None,
    }
    if policy["id"] in _TCA_SUCCESSOR_IDS:
        sources["implementation"] = (_roa_source("task_source_context.source_material", context["source_material"])
                                     if context.get("source_material") else None)
    result = {"format": "AIOS_TCA_SUBJECT", "version": 1, "selected_flow": "TASK_AUTHORING",
              "packet_fingerprint": body["packet_fingerprint"], "work_context_fingerprint": body["work_context_fingerprint"],
              "audit_profile_ref": profile_ref(policy), "return_contract_ref": ref, "external_bindings": bindings,
              "human_intent": intent, "sources": sources,
              "blockers": {role: "MISSING_DECISIVE_SOURCE" for role, source in sources.items() if source is None}}
    if policy["id"] == _TCA_V3_ID:
        if context.get("source_material"):
            kinds: dict[str, int] = {}
            for src in context["source_material"]:
                kind = src.get("kind") or src.get("role") or "IMPLEMENTATION"
                kinds[kind] = kinds.get(kind, 0) + 1
            result["source_material_summary"] = {"count": len(context["source_material"]), "kinds": kinds}
        else:
            result["source_material_summary"] = None
    result["subject_fingerprint"] = _digest(result)
    return result


def _tca_bound(packet: Any, profile: Any, subject: Any, source_context: Any) -> tuple[dict, dict, dict, dict]:
    body, policy = _packet(packet), _profile(profile)
    supplied = _normal(subject, depth=0, max_depth=32)
    if type(supplied) is not dict:
        raise BrainAuditError("TCA subject required")
    context = _tca_source_context(source_context)
    bound = construct_tca_subject(body, policy, supplied.get("return_contract_ref"),
                                  supplied.get("external_bindings"), context)
    if _json(bound) != _json(supplied):
        raise BrainAuditError("TCA packet/return/source/subject binding mismatch")
    return body, policy, bound, context


def _tca_judgment(check: dict[str, Any], bound: dict[str, Any], roles: tuple[str, ...]) -> None:
    _tca_private(check)
    if (check["outcome"] not in ("CLEAR", "BLOCKER")
            or check["knowledge"] not in ("OBSERVED", "INFERRED", "UNKNOWN")):
        raise BrainAuditError("invalid TCA judgment")
    _text(check["basis"], 2048, "TCA judgment basis")
    refs, sources = check["evidence_refs"], bound["sources"]
    if bound["audit_profile_ref"]["id"] in _TCA_SUCCESSOR_IDS:
        roles = tuple(dict.fromkeys((*roles, "implementation", "authorization")))
    if (type(refs) is not list or len(refs) > _TCA_BOUNDS["evidence_refs_per_check"]
            or len({_json(r) for r in refs}) != len(refs)
            or any(r not in [s for s in sources.values() if s is not None] for r in refs)):
        raise BrainAuditError("forged, duplicate or unbounded TCA source reference")
    if any(sources[r] is None for r in roles) or sources["canonical"] is None:
        if check["outcome"] != "BLOCKER" or check["knowledge"] != "UNKNOWN":
            raise BrainAuditError("missing decisive TCA source requires UNKNOWN/BLOCKER")
    if check["knowledge"] == "UNKNOWN":
        if check["outcome"] != "BLOCKER":
            raise BrainAuditError("UNKNOWN cannot discharge TCA obligations")
    elif any(sources[r] not in refs for r in roles):
        raise BrainAuditError("TCA judgment lacks its bound source references")


_TCA_JUDGMENT_FIELDS = {"id", "outcome", "knowledge", "basis", "evidence_refs"}


def _tca_checks(value: Any, bound: dict[str, Any], *, challenge: bool) -> list[dict[str, Any]]:
    identities = _TCA_CHALLENGE_IDS if challenge else _TCA_CONSTRUCT_IDS
    roles = _TCA_CHALLENGE_ROLES if challenge else _TCA_CONSTRUCT_ROLES
    if type(value) is not list or len(value) != len(identities):
        raise BrainAuditError("TCA requires exact focused check coverage")
    for identity, required, check in zip(identities, roles, value):
        extra = ({"counterexample", "disposition"} | (
            {"counterexample_kind"} if type(check) is dict and "counterexample_kind" in check else set()
        )) if challenge else set()
        _fields(check, _TCA_JUDGMENT_FIELDS | extra, "TCA check")
        if check["id"] != identity:
            raise BrainAuditError("unknown, duplicate or out-of-order TCA check")
        _tca_judgment(check, bound, required)
        if challenge:
            _text(check["counterexample"], 2048, "TCA falsifying counterexample")
            if "counterexample_kind" in check and check["counterexample_kind"] not in _TCA_COUNTEREXAMPLE_KINDS:
                raise BrainAuditError("invalid TCA counterexample kind")
            if (check["disposition"] not in _TCA_PROCEDURE["challenge_dispositions"]
                    or (check["outcome"] == "BLOCKER") != (check["disposition"] == "BLOCKER")):
                raise BrainAuditError("TCA challenge disposition contradicts judgment")
    return value


def _tca_acceptance(value: Any, task: dict[str, Any] | None, bound: dict[str, Any]) -> list[dict[str, Any]]:
    criteria = [] if task is None else task["acceptance"]
    if type(value) is not list or len(value) != len(criteria):
        raise BrainAuditError("TCA requires every acceptance id exactly once")
    for criterion, check in zip(criteria, value):
        extra = {"counterexample_kind"} if type(check) is dict and "counterexample_kind" in check else set()
        _fields(check, _TCA_JUDGMENT_FIELDS | {"condition", "phase", "implementation_property", "falsifying_counterexample"} | extra,
                "TCA acceptance check")
        if any(check[k] != criterion[k] for k in ("id", "condition")):
            raise BrainAuditError("missing, duplicate or substituted TCA acceptance")
        _tca_judgment(check, bound, ("task",))
        for key in ("implementation_property", "falsifying_counterexample"):
            _text(check[key], 2048, f"TCA {key}")
        if "counterexample_kind" in check and check["counterexample_kind"] not in _TCA_COUNTEREXAMPLE_KINDS:
            raise BrainAuditError("invalid TCA acceptance counterexample kind")
        if (check["phase"] not in ("CLAIM_NOW", "PROOF_LATER")
                or check["phase"] == "PROOF_LATER" and check["outcome"] != "BLOCKER"):
            raise BrainAuditError("PROOF_LATER cannot be sufficient TCA acceptance")
    return value


def _tca_consumers(value: Any, task: dict[str, Any] | None, context: dict[str, Any], bound: dict[str, Any]) -> list[dict]:
    consumers = context["consumers"]
    if type(value) is not list or len(value) != len(consumers):
        raise BrainAuditError("TCA requires every declared consumer exactly once")
    ids = set() if task is None else {c["id"] for c in task["acceptance"]}
    is_tca3 = bound["audit_profile_ref"]["id"] == _TCA_V3_ID
    source_paths = {s["path"] for s in context.get("source_material", [])}
    modify_paths = set() if task is None else set(task["scope"]["modify"])
    auth = context.get("authorization")
    inspect_only = (set(auth["scope"]["inspect"]) - set(auth["scope"]["modify"])) if auth is not None else set()
    for consumer, check in zip(consumers, value):
        from .task import TaskValidationError, _exact_repo_path
        extra_check_keys = {"kind", "path", "symbol"} & set(check) if type(check) is dict else set()
        _fields(check, _TCA_JUDGMENT_FIELDS | {"acceptance_ids"} | extra_check_keys, "TCA consumer check")
        if check["id"] != consumer["id"]:
            raise BrainAuditError("missing, duplicate or substituted TCA consumer")
        if "kind" in check and (check["kind"] not in _TCA_CONSUMER_CATEGORIES
                                or ("kind" in consumer and check["kind"] != consumer["kind"])):
            raise BrainAuditError("invalid or mismatched TCA consumer check kind")
        if "path" in check:
            try:
                _exact_repo_path(check["path"], "TCA consumer check path")
            except TaskValidationError as exc:
                raise BrainAuditError("invalid TCA consumer check path") from exc
            if "path" in consumer and check["path"] != consumer["path"]:
                raise BrainAuditError("mismatched TCA consumer check path")
        if "symbol" in check:
            _text(check["symbol"], 256, "TCA consumer check symbol")
            if "symbol" in consumer and check["symbol"] != consumer["symbol"]:
                raise BrainAuditError("mismatched TCA consumer check symbol")
        _tca_judgment(check, bound, ("consumers", "task"))
        covered = _tca_texts(check["acceptance_ids"], 256, "TCA consumer acceptance ids")
        if not set(covered) <= ids or check["outcome"] == "CLEAR" and not covered:
            raise BrainAuditError("material TCA consumer lacks acceptance coverage")
        if is_tca3:
            req_sources = consumer.get("required_source_paths", [])
            if req_sources and not set(req_sources) <= source_paths:
                if check["outcome"] != "BLOCKER" or check["knowledge"] != "UNKNOWN":
                    raise BrainAuditError("TCA-3 consumer missing required source excerpt requires UNKNOWN/BLOCKER")
            req_modify = consumer.get("required_modify_paths", [])
            if req_modify and any(p in inspect_only or p not in modify_paths for p in req_modify):
                if check["outcome"] != "BLOCKER":
                    raise BrainAuditError("TCA-3 consumer requiring inspect-only or unauthorized edit must be BLOCKER")
    return value


def _tca_verification_checks(value: Any, task: dict[str, Any] | None, context: dict[str, Any],
                             bound: dict[str, Any]) -> list[dict[str, Any]]:
    commands = [] if task is None else task["verification"]["required"]
    if type(value) is not list or len(value) != len(commands):
        raise BrainAuditError("TCA-3 requires every verification.required command in order")
    observed = {o["command"]: o["status"] for o in context.get("baseline_observations", [])}
    has_full_reason = bool(task and task["verification"].get("full_suite_reason"))
    seen_ids = set()
    for cmd, check in zip(commands, value):
        extra = {"distinct_causes"} if type(check) is dict and "distinct_causes" in check else set()
        _fields(check, _TCA_JUDGMENT_FIELDS | {"command", "phase", "baseline_status"} | extra, "TCA-3 verification check")
        identity = _text(check["id"], 256, "TCA-3 verification check id")
        if identity in seen_ids or check["command"] != cmd:
            raise BrainAuditError("missing, duplicate, skipped or substituted TCA-3 verification command")
        seen_ids.add(identity)
        _tca_judgment(check, bound, ("task",))
        status = check["baseline_status"]
        if status not in ("PASS", "FAIL", "NOT_RUN", "UNKNOWN"):
            raise BrainAuditError("invalid TCA-3 verification baseline_status")
        if check["phase"] not in ("CLAIM_NOW", "PROOF_LATER"):
            raise BrainAuditError("invalid TCA-3 verification phase")
        if check["phase"] == "CLAIM_NOW" and (status in ("FAIL", "NOT_RUN", "UNKNOWN") or check["outcome"] == "CLEAR"):
            raise BrainAuditError("TCA-3 verification check is post-implementation PROOF_LATER, not pre-execution CLAIM_NOW")
        if "distinct_causes" in check:
            causes = _tca_texts(check["distinct_causes"], 16, "TCA-3 verification distinct_causes")
            if not causes:
                raise BrainAuditError("TCA-3 verification distinct_causes cannot be empty")
            if any("::" in cause for cause in causes):
                raise BrainAuditError("TCA-3 distinct_causes cannot treat a bare pytest node ID as a complete cause")
        else:
            causes = []
        if ("aios_parallel_full_suite" in cmd or cmd.strip() in ("pytest", "pytest -q")) and not has_full_reason:
            if check["outcome"] != "BLOCKER":
                raise BrainAuditError("TCA-3 rejects implicit full-suite verification without full_suite_reason")
        if cmd in observed and status != observed[cmd]:
            raise BrainAuditError("TCA-3 verification baseline_status contradicts baseline_observations")
        if cmd not in observed and status == "PASS" and check["knowledge"] == "OBSERVED":
            raise BrainAuditError("TCA-3 cannot invent observed baseline PASS without baseline_observations")
        if status == "UNKNOWN" and (check["outcome"] != "BLOCKER" or check["knowledge"] != "UNKNOWN"):
            raise BrainAuditError("TCA-3 UNKNOWN baseline status requires UNKNOWN/BLOCKER")
        if status == "FAIL" and check["outcome"] != "BLOCKER":
            basis_lower = check["basis"].lower()
            if not causes and "pre-existing" not in basis_lower and "prior" not in basis_lower and "baseline" not in basis_lower:
                raise BrainAuditError("TCA-3 failing baseline command requires distinct_causes, BLOCKER, or explicit prior-baseline distinction")
    return value


def construct_tca(packet: Any, profile: Any, subject: Any, semantic_output: Any,
                  source_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """First pass binds the supplied proposal and independently reviewable obligations."""
    _, policy, bound, context = _tca_bound(packet, profile, subject, source_context)
    raw = _normal(semantic_output, depth=0, max_depth=32)
    has_verif = policy["id"] == _TCA_V3_ID and (
        (type(raw) is dict and "verification_checks" in raw) or "baseline_observations" in context
    )
    allowed = {"subject_fingerprint", "checks", "acceptance_checks", "consumer_checks"} | (
        {"verification_checks"} if has_verif else set()
    )
    material = _fields(raw, allowed, "TCA construct")
    _bounded(material, _TCA_BOUNDS["outcome_bytes"], "TCA construct")
    if material["subject_fingerprint"] != bound["subject_fingerprint"]:
        raise BrainAuditError("stale TCA construct subject")
    checks = _tca_checks(material["checks"], bound, challenge=False)
    acceptance = _tca_acceptance(material["acceptance_checks"], context["task"], bound)
    consumers = _tca_consumers(material["consumer_checks"], context["task"], context, bound)
    verification = _tca_verification_checks(material["verification_checks"], context["task"], context, bound) if has_verif else None
    for index, judgments in ((2, acceptance), (4, consumers)):
        if any(c["outcome"] == "BLOCKER" for c in judgments) and checks[index]["outcome"] != "BLOCKER":
            raise BrainAuditError("TCA construct gives false sufficient aggregate coverage")
    if verification is not None and any(c["outcome"] == "BLOCKER" for c in verification):
        if checks[2]["outcome"] != "BLOCKER" and checks[4]["outcome"] != "BLOCKER":
            raise BrainAuditError("TCA construct gives false sufficient verification coverage")
    result = {"format": "AIOS_BRAIN_TASK_CONTRACT_AUDIT", "version": 1, "stage": "CONSTRUCT",
              "subject_fingerprint": bound["subject_fingerprint"], "checks": checks,
              "acceptance_checks": acceptance, "consumer_checks": consumers, "draft_candidate": context["task"]}
    if verification is not None:
        result["verification_checks"] = verification
    result["construct_fingerprint"] = _digest(result)
    return result


def _tca_construct(packet: Any, profile: Any, subject: Any, value: Any, source_context: Any) -> dict[str, Any]:
    raw = _normal(value, depth=0, max_depth=32)
    has_verif = type(raw) is dict and "verification_checks" in raw
    first = _fields(raw, _TCA_CONSTRUCT_FIELDS | ({"verification_checks"} if has_verif else set()), "TCA first pass")
    keys = ("subject_fingerprint", "checks", "acceptance_checks", "consumer_checks") + (
        ("verification_checks",) if has_verif else ()
    )
    expected = construct_tca(packet, profile, subject, {k: first[k] for k in keys}, source_context)
    if _json(first) != _json(expected):
        raise BrainAuditError("TCA construct lineage mismatch")
    return expected


def _tca_candidate(value: Any, context: dict[str, Any], bound: dict[str, Any]) -> dict[str, Any]:
    candidate = _tca_task(value)
    original, auth = context["task"], context["authorization"]
    if original is None or auth is None or bound["blockers"]:
        raise BrainAuditError("missing TCA intent/source/authority cannot expose a final TASK")
    mutable = {"acceptance", "scope", "constraints", "non_goals", "verification"}
    if (set(candidate) != set(original)
            or any(_json(candidate[k]) != _json(original[k]) for k in original if k not in mutable)
            or any(candidate[k] != bound["external_bindings"][k] for k in ("task_id", "revision"))):
        raise BrainAuditError("TCA candidate changes the bound TASK objective or identity")
    if (candidate["scope"]["inspect"] != original["scope"]["inspect"]
            or not set(candidate["scope"]["modify"]) <= set(auth["scope"]["modify"])
            or not set(candidate["scope"]["modify"]) <= set(original["scope"]["modify"])):
        raise BrainAuditError("TCA candidate exceeds exact authorized scope")
    for field, old, required, new in (
        ("constraints", original["constraints"]["hard"], auth["constraints"]["hard"], candidate["constraints"]["hard"]),
        ("non_goals", original["non_goals"], auth["non_goals"], candidate["non_goals"]),
    ):
        _tca_texts(new, 256, f"TCA final {field}")
        if not (set(old) | set(required)) <= set(new):
            raise BrainAuditError("TCA candidate removes original/authorized constraints or non-goals")
    old, new = original["verification"], candidate["verification"]
    if (old.get("policy") != new.get("policy") or old.get("full_suite_reason") != new.get("full_suite_reason")
            or not set(old["required"]) <= set(new["required"])):
        raise BrainAuditError("TCA candidate weakens the original verification contract")
    return candidate


def _tca_anchor(candidate: Any, anchor: Any) -> Any:
    path = _text(anchor, 256, "TCA candidate anchor").split(".")
    found = candidate
    for part in path:
        if type(found) is list and part.isdecimal() and int(part) < len(found):
            found = found[int(part)]
        elif type(found) is dict and part in found:
            found = found[part]
        else:
            raise BrainAuditError("TCA anchor does not identify candidate content")
    return found


def _tca_changed_anchors(value: Any, original: Any, candidate: Any) -> None:
    anchors = _tca_texts(value, 16, "TCA changed anchors")
    if not anchors:
        raise BrainAuditError("TCA resolution requires changed candidate anchors")
    for anchor in anchors:
        after = _tca_anchor(candidate, anchor)
        try:
            before = _tca_anchor(original, anchor)
        except BrainAuditError:
            continue
        if _json(before) == _json(after):
            raise BrainAuditError("TCA resolution anchor did not change")


def _tca_accounting(value: Any, original: Any, candidate: Any, bound: dict[str, Any]) -> list[dict]:
    old = [] if original is None else original["acceptance"]
    new = [] if candidate is None else candidate["acceptance"]
    if type(value) is not list or len(value) != len(old):
        raise BrainAuditError("TCA must account for every original acceptance id")
    final = {c["id"]: c for c in new}
    covered = []
    for criterion, record in zip(old, value):
        _fields(record, {"id", "disposition", "final_ids", "candidate_anchors", "basis", "evidence_refs"},
                "TCA acceptance accounting")
        if record["id"] != criterion["id"]:
            raise BrainAuditError("TCA acceptance accounting dropped, duplicated or replaced an id")
        roles = ("task", "canonical") + (("implementation", "authorization")
                 if bound["audit_profile_ref"]["id"] in _TCA_SUCCESSOR_IDS else ())
        missing = any(bound["sources"][role] is None for role in roles)
        _tca_judgment({**record, "outcome": "BLOCKER" if missing else "CLEAR",
                       "knowledge": "UNKNOWN" if missing else "INFERRED"}, bound, ("task",))
        ids = _tca_texts(record["final_ids"], 256, "TCA final acceptance ids")
        disposition = record["disposition"]
        if disposition == "RETAINED":
            if ids != [criterion["id"]] or final.get(criterion["id"]) != criterion or record["candidate_anchors"]:
                raise BrainAuditError("TCA retained acceptance changed or disappeared")
        elif disposition == "REPLACED":
            if not ids or any(i not in final for i in ids):
                raise BrainAuditError("TCA replacement lacks exact final acceptance ids")
            _tca_changed_anchors(record["candidate_anchors"], original, candidate)
            if not all(a.startswith("acceptance.") for a in record["candidate_anchors"]):
                raise BrainAuditError("TCA replacement must anchor changed acceptance")
        elif disposition in ("MOVED_TO_VERIFICATION", "MOVED_TO_CONSTRAINT", "MOVED_TO_NON_GOAL"):
            prefix = {"MOVED_TO_VERIFICATION": "verification.required.", "MOVED_TO_CONSTRAINT": "constraints.hard.",
                      "MOVED_TO_NON_GOAL": "non_goals."}[disposition]
            if ids or criterion["id"] in final or any(not a.startswith(prefix) for a in record["candidate_anchors"]):
                raise BrainAuditError("TCA moved acceptance must leave final acceptance and name its proper surface")
            _tca_changed_anchors(record["candidate_anchors"], original, candidate)
        else:
            raise BrainAuditError("unknown TCA acceptance accounting disposition")
        covered.extend(ids)
    if len(covered) != len(set(covered)) or set(covered) != set(final):
        raise BrainAuditError("TCA final acceptance lacks exact reconciled accounting")
    return value


def _tca_reconcile(value: Any, first: dict, candidate: Any, bound: dict) -> list[dict]:
    verif_checks = first.get("verification_checks", [])
    blockers = {(kind, c["id"]): (c, roles) for kind, checks, required in (
        ("CHECK", first["checks"], _TCA_CONSTRUCT_ROLES),
        ("ACCEPTANCE", first["acceptance_checks"], [("task",)] * len(first["acceptance_checks"])),
        ("CONSUMER", first["consumer_checks"], [("task", "consumers")] * len(first["consumer_checks"])),
        ("VERIFICATION", verif_checks, [("task",)] * len(verif_checks)),
    ) for c, roles in zip(checks, required) if c["outcome"] == "BLOCKER"}
    if type(value) is not list or len(value) != len(blockers):
        raise BrainAuditError("TCA must retain, reconcile or explicitly resolve every first-pass blocker")
    seen, effective = set(), []
    for record in value:
        _fields(record, {"kind", "id", "disposition", "knowledge", "basis", "evidence_refs", "candidate_anchors"},
                "TCA blocker reconciliation")
        key = (record["kind"], record["id"])
        if key not in blockers or key in seen or record["disposition"] not in ("UNRESOLVED", "RESOLVED"):
            raise BrainAuditError("unknown or duplicate first-pass TCA blocker")
        seen.add(key)
        prior, roles = blockers[key]
        resolved = record["disposition"] == "RESOLVED"
        judgment = {**record, "outcome": "CLEAR" if resolved else "BLOCKER"}
        _tca_judgment(judgment, bound, roles)
        if resolved:
            if candidate is None or record["knowledge"] == "UNKNOWN":
                raise BrainAuditError("TCA blocker resolution requires a lawful grounded candidate")
            _tca_changed_anchors(record["candidate_anchors"], first["draft_candidate"], candidate)
        elif record["candidate_anchors"] or record["knowledge"] != prior["knowledge"]:
            raise BrainAuditError("unresolved TCA blocker cannot erase prior knowledge")
        effective.append(judgment)
    # CLEAR first-pass obligations still contribute their knowledge to closure.
    effective.extend(c for field in ("checks", "acceptance_checks", "consumer_checks")
                     for c in first[field] if c["outcome"] == "CLEAR")
    effective.extend(c for c in verif_checks if c["outcome"] == "CLEAR")
    return effective


def validate_tca(packet: Any, profile: Any, subject: Any, stage1: Any, semantic_output: Any,
                 source_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Independently challenge, reconcile exact blockers and expose one final closure."""
    body, policy, bound, context = _tca_bound(packet, profile, subject, source_context)
    first = _tca_construct(body, policy, bound, stage1, context)
    raw = _normal(semantic_output, depth=0, max_depth=32)
    has_verif = policy["id"] == _TCA_V3_ID and (
        "verification_checks" in first
        or "baseline_observations" in context
        or (type(raw) is dict and "verification_checks" in raw)
    )
    allowed = {
        "subject_fingerprint", "construct_fingerprint", "challenges", "acceptance_checks", "consumer_checks",
        "acceptance_accounting", "reconciliation", "closure", "reconciled_candidate",
    } | ({"verification_checks"} if has_verif else set())
    material = _fields(raw, allowed, "TCA second pass")
    _bounded(material, _TCA_BOUNDS["outcome_bytes"], "TCA second pass")
    if (material["subject_fingerprint"] != bound["subject_fingerprint"]
            or material["construct_fingerprint"] != first["construct_fingerprint"]):
        raise BrainAuditError("TCA second-pass subject/CONSTRUCT mismatch")
    candidate = material["reconciled_candidate"]
    if candidate is not None:
        candidate = _tca_candidate(candidate, context, bound)
    reviewed_task = candidate if candidate is not None else context["task"]
    challenges = _tca_checks(material["challenges"], bound, challenge=True)
    acceptance = _tca_acceptance(material["acceptance_checks"], reviewed_task, bound)
    consumers = _tca_consumers(material["consumer_checks"], reviewed_task, context, bound)
    verification = (
        _tca_verification_checks(material["verification_checks"], reviewed_task, context, bound)
        if has_verif else []
    )
    if candidate is not None:
        later_conditions = {c["condition"] for c in first["acceptance_checks"] if c["phase"] == "PROOF_LATER"}
        if any(c["condition"] in later_conditions for c in candidate["acceptance"]):
            raise BrainAuditError("PROOF_LATER acceptance must be removed or rewritten, never merely relabeled CLAIM_NOW")
    # No-decision retains original accounting; no hidden reconciled TASK is serialized.
    if context["task"] is None:
        if material["acceptance_accounting"] != []:
            raise BrainAuditError("missing TASK has no acceptance accounting")
    else:
        _tca_accounting(material["acceptance_accounting"], context["task"], reviewed_task, bound)
    effective = _tca_reconcile(material["reconciliation"], first, candidate, bound)
    judgments = [*effective, *challenges, *acceptance, *consumers, *verification]
    blocked = bool(bound["blockers"]) or any(c["outcome"] == "BLOCKER" for c in judgments)
    outcome = "NO_DECISION" if blocked else "CANDIDATE"
    knowledge = ("UNKNOWN" if any(c["knowledge"] == "UNKNOWN" for c in judgments) else
                 "INFERRED" if any(c["knowledge"] == "INFERRED" for c in judgments) else "OBSERVED")
    closure = _fields(material["closure"], {"outcome", "knowledge", "summary", "candidate_fingerprint"}, "TCA final closure")
    _text(closure["summary"], 2048, "TCA closure summary")
    _tca_private(closure["summary"])
    if closure["outcome"] != outcome or closure["knowledge"] != knowledge:
        raise BrainAuditError("TCA final closure contradicts focused passes or unresolved first-pass blockers")
    if outcome == "NO_DECISION":
        if candidate is not None or closure["candidate_fingerprint"] is not None:
            raise BrainAuditError("TCA NO_DECISION cannot expose a TASK candidate")
    else:
        if candidate is None or closure["candidate_fingerprint"] != _digest(candidate):
            raise BrainAuditError("TCA closure does not bind the final TASK")
        if policy["id"] == _TCA_V3_ID:
            distinct_causes = [
                cause
                for src in context.get("source_material", [])
                for cause in src.get("failure_causes", [])
            ]
            if len(set(distinct_causes)) >= 2:
                combined_text = " ".join(
                    [closure["summary"], *(c["basis"] for c in challenges), *(c["basis"] for c in acceptance)]
                ).lower()
                if any(cause.lower() not in combined_text for cause in set(distinct_causes)):
                    raise BrainAuditError("TCA-3 CANDIDATE must distinguish all bound source failure causes")
        changed = _json(candidate) != _json(first["draft_candidate"])
        addressed = any(c["disposition"] == "ADDRESSED_BY_RECONCILIATION" for c in challenges)
        if changed != addressed:
            raise BrainAuditError("TCA reconciliation contradicts the bound candidate delta")
    result = {"format": "AIOS_BRAIN_TASK_CONTRACT_AUDIT", "version": 1, "stage": "ADVERSARIAL_AUDIT_AND_RECONCILE",
              "subject_fingerprint": bound["subject_fingerprint"], "construct_fingerprint": first["construct_fingerprint"],
              **{k: material[k] for k in ("challenges", "acceptance_checks", "consumer_checks", "acceptance_accounting",
                                          "reconciliation", "closure")}, "handoff_candidate": candidate}
    if has_verif:
        result["verification_checks"] = verification
    result["audit_fingerprint"] = _digest(result)
    return result


def _aoa_private(value: Any) -> None:
    """Architecture support uses the existing privacy floor and closed envelopes."""
    _tca_private(value)
    if type(value) is dict:
        if value.get("format") in {"AIOS_AOA_SUBJECT", "AIOS_BRAIN_ARCHITECTURE_OUTCOME_AUDIT"}:
            raise BrainAuditError("nested AOA envelope")
        for key, item in value.items():
            if key.lower().replace("_", "").replace("-", "") in {"aoasubject", "architecturesourcecontext"}:
                raise BrainAuditError("nested AOA source or subject")
            _aoa_private(item)
    elif type(value) is list:
        for item in value:
            _aoa_private(item)


def _aoa_source_context(value: Any) -> dict[str, Any]:
    # This is a trusted caller projection of externally authenticated reads,
    # not a model-authored provenance certificate or an authentication scheme.
    context = {"objective": None, "constraints": [], "authorization": None, "alternatives": [],
               "consumers": [], "evidence": [], "provenance": None} if value is None else _normal(
                   value, depth=0, max_depth=32)
    _fields(context, {"objective", "constraints", "authorization", "alternatives", "consumers", "evidence", "provenance"},
            "AOA source context")
    _bounded(context, _AOA_BOUNDS["source_context_bytes"], "AOA source context")
    _aoa_private(context)
    if context["objective"] is not None:
        _text(context["objective"], 2048, "AOA Human objective")
    _tca_texts(context["constraints"], 32, "AOA Human constraints")
    auth = context["authorization"]
    if auth is not None:
        _fields(auth, {"planning_scope", "risk_acceptance"}, "AOA planning authorization")
        for key in auth:
            _tca_texts(auth[key], 32, "AOA planning authority")
        if not auth["planning_scope"]:
            raise BrainAuditError("AOA planning authority needs bounded scope")
    alternatives = context["alternatives"]
    if type(alternatives) is not list or len(alternatives) not in (0, 3):
        raise BrainAuditError("AOA source requires A/B/C or unavailable alternatives")
    for (identity, kind), option in zip(_AOA_KINDS.items(), alternatives):
        _fields(option, {"id", "kind", "description"}, "AOA source alternative")
        if option["id"] != identity or option["kind"] != kind:
            raise BrainAuditError("AOA alternatives must preserve ordered A/B/C identities")
        _text(option["description"], 2048, "AOA alternative description")
    consumers = context["consumers"]
    if type(consumers) is not list or len(consumers) > 16:
        raise BrainAuditError("unbounded AOA consumers")
    seen = set()
    for consumer in consumers:
        _fields(consumer, {"id", "authority", "requirement"}, "AOA consumer")
        identity = _text(consumer["id"], 256, "AOA consumer id")
        _text(consumer["requirement"], 2048, "AOA consumer requirement")
        if identity in seen or consumer["authority"] not in (
                "HUMAN", "BRAIN", "EXECUTOR", "RUNTIME", "REVIEWER", "PUBLISHER", "TRANSPORT"):
            raise BrainAuditError("invalid AOA consumer identity or authority")
        seen.add(identity)
    provenance = context["provenance"]
    if provenance is not None:
        _fields(provenance, {"status", "repository", "main_sha", "work_context_fingerprint"}, "AOA caller provenance")
        if provenance["status"] not in ("AUTHENTICATED", "UNKNOWN"):
            raise BrainAuditError("invalid AOA source authentication status")
        _text(provenance["repository"], 256, "AOA source repository")
        _git_sha(provenance["main_sha"], "AOA source main")
        _sha(provenance["work_context_fingerprint"], "AOA source currentness")
    evidence = context["evidence"]
    if type(evidence) is not list or len(evidence) > 16:
        raise BrainAuditError("unbounded AOA evidence")
    seen = set()
    for item in evidence:
        _fields(item, {"id", "knowledge", "basis", "measurements"}, "AOA evidence")
        identity = _text(item["id"], 256, "AOA evidence id")
        _text(item["basis"], 2048, "AOA evidence basis and horizon")
        if identity in seen or item["knowledge"] not in ("OBSERVED", "INFERRED", "UNKNOWN"):
            raise BrainAuditError("invalid AOA evidence identity or status")
        seen.add(identity)
        measurements = item["measurements"]
        if (type(measurements) is not list or len(measurements) > 15
                or item["knowledge"] == "UNKNOWN" and measurements):
            raise BrainAuditError("UNKNOWN evidence cannot supply quantitative measurements")
        dimensions = set()
        for measurement in measurements:
            _fields(measurement, {"option_id", "dimension", "quantity"}, "AOA supplied measurement")
            key = (measurement["option_id"], measurement["dimension"])
            quantity = measurement["quantity"]
            if (type(key[0]) is not str or type(key[1]) is not str
                    or key[0] not in _AOA_KINDS or key[1] not in _AOA_COST_DIMENSIONS or key in dimensions
                    or type(quantity) not in (int, float) or quantity < 0):
                raise BrainAuditError("invalid AOA supplied measurement")
            dimensions.add(key)
    return context


def _architecture_subject(packet: Any, return_ref: Any, external_bindings: Any, source_context: Any,
                 audit_ref: dict[str, Any]) -> dict[str, Any]:
    body = _packet(packet)
    if (body["selected_flow"] != "ARCHITECTURE" or body["subject"] != {"kind": "ARCHITECTURE"}
            or body["authority_owner"] != "BRAIN" or body["selection_basis"] != "EXPLICIT_SELECTOR"
            or body["decision_family_ref"] != "HUMAN_BRAIN_ARCHITECTURE_PLANNING"
            or body["handoff_target"] != "HUMAN_BRAIN_PLANNING"
            or body["expected_return_shape"] != "BOUNDED_SEMANTIC_PROPOSAL"
            or body["requires_fresh_context_for_continuation"] is not True):
        raise BrainAuditError("AOA requires the exact existing ARCHITECTURE planning flow")
    _sha(body["work_context_fingerprint"], "AOA work context")
    ref = _fields(_normal(return_ref, depth=0, max_depth=32), {"selected_flow", "version", "digest"}, "AOA return reference")
    if ref["selected_flow"] != "ARCHITECTURE" or type(ref["version"]) is not int or ref["version"] != 1:
        raise BrainAuditError("AOA return identity mismatch")
    _sha(ref["digest"], "AOA return digest")
    if external_bindings != {}:
        raise BrainAuditError("architecture has no external TASK identity bindings")
    context = _aoa_source_context(source_context)
    for value in (body["human_input"], body["canonical_facts"], body["bounded_observations"],
                  body["executor_claims"], body["prior_semantic_decisions"]):
        _aoa_private(value)
    intent, facts = body["human_input"], body["canonical_facts"]
    if intent is not None:
        _text(intent, 16384, "AOA Human intent")
    if type(facts) is not dict:
        raise BrainAuditError("invalid AOA canonical snapshot")
    canonical = (type(facts.get("repository")) is str and bool(facts["repository"].strip())
                 and type(facts.get("main_sha")) is str and re.fullmatch(r"[0-9a-f]{40}", facts["main_sha"]) is not None
                 and type(facts.get("roadmap")) is dict and body["canonical_blocker"] is None
                 and body["pending_canonical_obligation"] is None and body["pending_canonical_authority_owner"] is None)
    provenance = context["provenance"]
    if provenance is not None and (provenance["repository"] != facts.get("repository")
            or provenance["main_sha"] != facts.get("main_sha")
            or provenance["work_context_fingerprint"] != body["work_context_fingerprint"]):
        raise BrainAuditError("stale or swapped AOA caller source snapshot")
    authenticated = provenance is not None and provenance["status"] == "AUTHENTICATED"
    values = {"intent": ("human_input", intent), "canonical": ("canonical_facts", facts if canonical else None),
              "objective": ("architecture_source_context.objective", context["objective"]),
              "authorization": ("architecture_source_context.authorization", context["authorization"]),
              "alternatives": ("architecture_source_context.alternatives", context["alternatives"] or None),
              "consumers": ("architecture_source_context.consumers", context["consumers"] or None)}
    sources = {role: _roa_source(path, value) if authenticated and value is not None else None
               for role, (path, value) in values.items()}
    sources["constraints"] = _roa_source("architecture_source_context.constraints", context["constraints"]) if authenticated else None
    evidence = [{"id": item["id"], "knowledge": item["knowledge"],
                 "ref": _roa_source(f"architecture_source_context.evidence.{i}", item)}
                for i, item in enumerate(context["evidence"])] if authenticated else []
    result = {"format": "AIOS_AOA_SUBJECT", "version": 1, "selected_flow": "ARCHITECTURE",
              "packet_fingerprint": body["packet_fingerprint"], "work_context_fingerprint": body["work_context_fingerprint"],
              "audit_profile_ref": audit_ref, "return_contract_ref": ref, "external_bindings": {},
              "human_intent": intent, "human_objective": context["objective"], "constraints": context["constraints"],
              "source_context_fingerprint": _digest(context), "sources": sources, "evidence": evidence,
              "blockers": {role: "MISSING_OR_UNAUTHENTICATED_DECISIVE_SOURCE" for role, source in sources.items() if source is None}}
    result["subject_fingerprint"] = _digest(result)
    return result


def construct_aoa_subject(packet: Any, profile: Any, return_ref: Any, external_bindings: Any,
                          source_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bind architecture intent, current caller sources and one exact AOA profile."""
    policy = _profile(profile)
    if policy["id"] != _AOA_ID:
        raise BrainAuditError("AOA subject requires exact AOA-1")
    return _architecture_subject(packet, return_ref, external_bindings, source_context, profile_ref(policy))


def _aoa_bound(packet: Any, profile: Any, subject: Any, source_context: Any) -> tuple[dict, dict, dict, dict]:
    body, policy = _packet(packet), _profile(profile)
    supplied = _normal(subject, depth=0, max_depth=32)
    if type(supplied) is not dict:
        raise BrainAuditError("AOA subject required")
    context = _aoa_source_context(source_context)
    bound = construct_aoa_subject(body, policy, supplied.get("return_contract_ref"), supplied.get("external_bindings"), context)
    if _json(bound) != _json(supplied):
        raise BrainAuditError("AOA packet/return/source/subject binding mismatch")
    return body, policy, bound, context


def _aoa_refs(refs: Any, bound: dict) -> None:
    known = [s for s in bound["sources"].values() if s is not None] + [e["ref"] for e in bound["evidence"]]
    if (type(refs) is not list or len(refs) > 16 or len({_json(r) for r in refs}) != len(refs)
            or any(r not in known for r in refs)):
        raise BrainAuditError("forged, duplicate or unbounded AOA source reference")


def _aoa_judgment(check: dict, bound: dict, roles: tuple[str, ...]) -> None:
    # Check only semantic fields: outer support schemas are validated separately.
    for key in ("basis", "evidence_refs"):
        _aoa_private(check[key])
    if check["outcome"] not in ("CLEAR", "BLOCKER") or check["knowledge"] not in ("OBSERVED", "INFERRED", "UNKNOWN"):
        raise BrainAuditError("invalid AOA judgment")
    _text(check["basis"], 2048, "AOA judgment basis")
    _aoa_refs(check["evidence_refs"], bound)
    sources = bound["sources"]
    if any(sources[r] is None for r in (*roles, "canonical", "intent", "constraints")):
        if check["outcome"] != "BLOCKER" or check["knowledge"] != "UNKNOWN":
            raise BrainAuditError("missing decisive AOA source requires UNKNOWN/BLOCKER")
    if check["knowledge"] == "UNKNOWN":
        if check["outcome"] != "BLOCKER":
            raise BrainAuditError("UNKNOWN cannot discharge an AOA obligation")
    elif any(sources[r] not in check["evidence_refs"] for r in roles):
        raise BrainAuditError("AOA judgment lacks exact relevant source references")


def _aoa_checks(value: Any, bound: dict, *, challenge: bool) -> list[dict]:
    ids = _AOA_CHALLENGE_IDS if challenge else _AOA_CONSTRUCT_IDS
    roles = _AOA_CHALLENGE_ROLES if challenge else _AOA_CONSTRUCT_ROLES
    if type(value) is not list or len(value) != len(ids):
        raise BrainAuditError("AOA requires exact architecture check coverage")
    for identity, required, check in zip(ids, roles, value):
        _fields(check, _TCA_JUDGMENT_FIELDS | ({"counterexample", "disposition", "option_ids"} if challenge else set()), "AOA check")
        if check["id"] != identity:
            raise BrainAuditError("unknown, duplicate or out-of-order AOA check")
        _aoa_judgment(check, bound, required)
        if challenge:
            _text(check["counterexample"], 2048, "AOA counterexample")
            _aoa_private(check["counterexample"])
            targets = ["B", "C"] if identity == _AOA_CHALLENGE_IDS[0] else ["C"] if identity == _AOA_CHALLENGE_IDS[1] else ["A", "B", "C"]
            if (check["option_ids"] != targets or check["disposition"] not in _AOA_PROCEDURE["challenge_dispositions"]
                    or (check["outcome"] == "BLOCKER") != (check["disposition"] == "BLOCKER")):
                raise BrainAuditError("AOA challenge omits alternatives or contradicts judgment")
    return value


def _aoa_cost(value: Any, identity: str, dimension: str, bound: dict, context: dict) -> None:
    _fields(value, {"knowledge", "quantity", "basis", "evidence_refs"}, "AOA episode cost")
    _aoa_private(value)
    _text(value["basis"], 2048, "AOA cost assumptions and horizon")
    _aoa_refs(value["evidence_refs"], bound)
    if value["knowledge"] == "UNKNOWN":
        if value["quantity"] is not None:
            raise BrainAuditError("UNKNOWN cost cannot contain a fabricated point estimate")
        return
    if value["knowledge"] not in ("OBSERVED", "INFERRED") or type(value["quantity"]) not in (int, float):
        raise BrainAuditError("invalid AOA cost attribution")
    measurement = {"option_id": identity, "dimension": dimension, "quantity": value["quantity"]}
    if not any(e["ref"] in value["evidence_refs"] and e["knowledge"] == value["knowledge"]
               and measurement in context["evidence"][i]["measurements"] for i, e in enumerate(bound["evidence"])):
        raise BrainAuditError("AOA quantitative cost lacks exact supplied evidence and attribution")


def _aoa_alternatives(value: Any, bound: dict, context: dict) -> list[dict]:
    if type(value) is not list or len(value) != 3:
        raise BrainAuditError("AOA compares exactly A/B/C")
    for identity, option in zip(_AOA_KINDS, value):
        _fields(option, _TCA_JUDGMENT_FIELDS | {"expected_outcome", "feasibility", "planning_scope", "risks",
                "risk_acceptance_required", "reversibility", "necessity", "externalities", "episode_cost", "relative_episode_cost"},
                "AOA comparative alternative")
        if option["id"] != identity or option["feasibility"] not in ("FEASIBLE", "INFEASIBLE", "UNKNOWN"):
            raise BrainAuditError("AOA alternative identity or feasibility mismatch")
        _aoa_judgment(option, bound, ("objective", "alternatives", "authorization", "consumers"))
        for key in ("expected_outcome", "reversibility", "necessity"):
            _text(option[key], 2048, f"AOA {key}")
            _aoa_private(option[key])
        for key in ("planning_scope", "risks", "risk_acceptance_required"):
            _tca_texts(option[key], 16, f"AOA {key}")
            _aoa_private(option[key])
        auth = context["authorization"]
        if auth is not None and (not set(option["planning_scope"]) <= set(auth["planning_scope"])
                or option["feasibility"] == "FEASIBLE" and not option["planning_scope"]):
            raise BrainAuditError("AOA alternative exceeds supplied planning scope")
        accepted = auth is not None and set(option["risk_acceptance_required"]) <= set(auth["risk_acceptance"])
        if (option["feasibility"] == "UNKNOWN" or not accepted) and option["outcome"] != "BLOCKER":
            raise BrainAuditError("unresolved AOA feasibility or Human risk acceptance blocks")
        _fields(option["externalities"], {"positive", "negative"}, "AOA externalities")
        for side in option["externalities"].values():
            _tca_texts(side, 16, "AOA externalities")
            _aoa_private(side)
        _fields(option["episode_cost"], set(_AOA_COST_DIMENSIONS), "AOA full episode cost")
        for dimension in _AOA_COST_DIMENSIONS:
            _aoa_cost(option["episode_cost"][dimension], identity, dimension, bound, context)
        relative = _fields(option["relative_episode_cost"], {
            "knowledge", "basis", "evidence_refs", "compared_with", "relation"}, "AOA relative episode cost")
        _aoa_private(relative)
        _text(relative["basis"], 2048, "AOA relative cost basis")
        _aoa_refs(relative["evidence_refs"], bound)
        if (relative["compared_with"] != [i for i in _AOA_KINDS if i != identity]
                or relative["knowledge"] not in ("OBSERVED", "INFERRED", "UNKNOWN")
                or relative["relation"] not in ("LOWER", "SIMILAR", "HIGHER", "MIXED", "UNKNOWN")
                or (relative["knowledge"] == "UNKNOWN") != (relative["relation"] == "UNKNOWN")):
            raise BrainAuditError("invalid or incomplete AOA relative cost comparison")
        if relative["knowledge"] != "UNKNOWN" and not any(
                e["ref"] in relative["evidence_refs"] and e["knowledge"] == relative["knowledge"] for e in bound["evidence"]):
            raise BrainAuditError("AOA relative cost assertion lacks attributed bound evidence")
    return value


def _aoa_information(value: Any, bound: dict) -> dict:
    _fields(value, {"outcome", "knowledge", "basis", "evidence_refs", "question", "decision_if_known", "value_of_information"},
            "AOA discriminating information")
    _aoa_judgment(value, bound, ("objective", "alternatives"))
    for key in ("question", "decision_if_known", "value_of_information"):
        _text(value[key], 2048, f"AOA {key}")
        _aoa_private(value[key])
    return value


def _aoa_candidate(value: Any, bound: dict, alternatives: list[dict]) -> dict:
    candidate = _normal(value, depth=0, max_depth=32)
    _fields(candidate, {"selected_alternative", "proposal", "rationale", "uncertainties", "next_human_decision"}, "AOA planning candidate")
    _aoa_private(candidate)
    _bounded(candidate, _AOA_BOUNDS["candidate_bytes"], "AOA candidate")
    selected = candidate["selected_alternative"]
    if selected not in (*_AOA_KINDS, "DEFER") or bound["blockers"]:
        raise BrainAuditError("AOA candidate lacks exact intent/source/authority")
    if selected != "DEFER" and next(o for o in alternatives if o["id"] == selected)["feasibility"] != "FEASIBLE":
        raise BrainAuditError("AOA cannot recommend an infeasible or unknown alternative")
    for key in ("proposal", "rationale", "next_human_decision"):
        _text(candidate[key], 2048, f"AOA candidate {key}")
    _tca_texts(candidate["uncertainties"], 16, "AOA candidate uncertainties")
    return candidate


def construct_aoa(packet: Any, profile: Any, subject: Any, semantic_output: Any,
                  source_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Construct the counterfactual comparison, without a final engineering verdict."""
    _, _, bound, context = _aoa_bound(packet, profile, subject, source_context)
    material = _fields(_normal(semantic_output, depth=0, max_depth=32), {
        "subject_fingerprint", "checks", "alternatives", "information", "draft_candidate"}, "AOA construct")
    _bounded(material, _AOA_BOUNDS["outcome_bytes"], "AOA construct")
    if material["subject_fingerprint"] != bound["subject_fingerprint"]:
        raise BrainAuditError("stale AOA construct subject")
    checks = _aoa_checks(material["checks"], bound, challenge=False)
    options = _aoa_alternatives(material["alternatives"], bound, context)
    information = _aoa_information(material["information"], bound)
    for index, judgments in ((1, options), (3, [information])):
        if any(c["outcome"] == "BLOCKER" for c in judgments) and checks[index]["outcome"] != "BLOCKER":
            raise BrainAuditError("AOA construct gives false sufficient comparison or information")
    candidate = material["draft_candidate"]
    if candidate is not None:
        candidate = _aoa_candidate(candidate, bound, options)
    result = {"format": "AIOS_BRAIN_ARCHITECTURE_OUTCOME_AUDIT", "version": 1, "stage": "CONSTRUCT",
              "subject_fingerprint": bound["subject_fingerprint"], "checks": checks, "alternatives": options,
              "information": information, "draft_candidate": candidate}
    result["construct_fingerprint"] = _digest(result)
    return result


def _aoa_construct(packet: Any, profile: Any, subject: Any, value: Any, source_context: Any) -> dict:
    first = _fields(_normal(value, depth=0, max_depth=32), _AOA_CONSTRUCT_FIELDS, "AOA first pass")
    expected = construct_aoa(packet, profile, subject, {k: first[k] for k in (
        "subject_fingerprint", "checks", "alternatives", "information", "draft_candidate")}, source_context)
    if _json(first) != _json(expected):
        raise BrainAuditError("AOA construct lineage mismatch")
    return expected


def _aoa_reconcile(value: Any, first: dict, final: dict, bound: dict) -> list[dict]:
    prior = [("CHECK", c, roles) for c, roles in zip(first["checks"], _AOA_CONSTRUCT_ROLES)]
    prior += [("ALTERNATIVE", c, ("objective", "alternatives", "authorization", "consumers")) for c in first["alternatives"]]
    prior += [("INFORMATION", {"id": "DISCRIMINATOR", **first["information"]}, ("objective", "alternatives"))]
    blockers = {(kind, c["id"]): (c, roles) for kind, c, roles in prior if c["outcome"] == "BLOCKER"}
    if type(value) is not list or len(value) != len(blockers):
        raise BrainAuditError("AOA must retain or explicitly resolve every first-pass blocker")
    original = {"candidate": first["draft_candidate"], "alternatives": first["alternatives"], "information": first["information"]}
    changed = {"candidate": final["reconciled_candidate"], "alternatives": final["alternatives"], "information": final["information"]}
    seen, effective = set(), []
    for record in value:
        _fields(record, {"kind", "id", "disposition", "knowledge", "basis", "evidence_refs", "changed_anchors"}, "AOA reconciliation")
        _text(record["kind"], 256, "AOA blocker kind")
        _text(record["id"], 256, "AOA blocker id")
        key = (record["kind"], record["id"])
        if key in seen or key not in blockers or record["disposition"] not in ("RESOLVED", "UNRESOLVED"):
            raise BrainAuditError("unknown or duplicate AOA first-pass blocker")
        seen.add(key)
        old, roles = blockers[key]
        resolved = record["disposition"] == "RESOLVED"
        judgment = {**record, "outcome": "CLEAR" if resolved else "BLOCKER"}
        _aoa_judgment(judgment, bound, roles)
        if resolved:
            if record["knowledge"] == "UNKNOWN" or bound["blockers"]:
                raise BrainAuditError("AOA blocker cannot be resolved without decisive bound sources")
            if type(record["changed_anchors"]) is not list or any(
                    type(a) is not str or not a.startswith(("alternatives.", "information.", "candidate."))
                    for a in record["changed_anchors"]):
                raise BrainAuditError("AOA resolution requires specific planning content anchors")
            _tca_changed_anchors(record["changed_anchors"], original, changed)
            if key[0] == "ALTERNATIVE" and not any(a.startswith(f"alternatives.{list(_AOA_KINDS).index(key[1])}.")
                                                      for a in record["changed_anchors"]):
                raise BrainAuditError("AOA resolution must change the blocked alternative")
            if key[0] == "INFORMATION" and not any(a.startswith("information.") for a in record["changed_anchors"]):
                raise BrainAuditError("AOA information resolution must change the discriminator")
        elif record["changed_anchors"] or record["knowledge"] != old["knowledge"]:
            raise BrainAuditError("unresolved AOA blocker cannot erase prior knowledge")
        effective.append(judgment)
    effective.extend(c for _, c, _ in prior if c["outcome"] == "CLEAR")
    return effective


def validate_aoa(packet: Any, profile: Any, subject: Any, stage1: Any, semantic_output: Any,
                 source_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Challenge A/B/C, retain unresolved blockers, and close once for Human planning."""
    body, policy, bound, context = _aoa_bound(packet, profile, subject, source_context)
    first = _aoa_construct(body, policy, bound, stage1, context)
    material = _fields(_normal(semantic_output, depth=0, max_depth=32), {
        "subject_fingerprint", "construct_fingerprint", "challenges", "alternatives", "information",
        "reconciliation", "closure", "reconciled_candidate"}, "AOA reconcile")
    _bounded(material, _AOA_BOUNDS["outcome_bytes"], "AOA second pass")
    if (material["subject_fingerprint"] != bound["subject_fingerprint"]
            or material["construct_fingerprint"] != first["construct_fingerprint"]):
        raise BrainAuditError("AOA second-pass subject/construct mismatch")
    challenges = _aoa_checks(material["challenges"], bound, challenge=True)
    options = _aoa_alternatives(material["alternatives"], bound, context)
    information = _aoa_information(material["information"], bound)
    candidate = material["reconciled_candidate"]
    if candidate is not None:
        candidate = _aoa_candidate(candidate, bound, options)
    effective = _aoa_reconcile(material["reconciliation"], first, material, bound)
    judgments = [*effective, *challenges, *options, information]
    blocked = bool(bound["blockers"]) or any(c["outcome"] == "BLOCKER" for c in judgments)
    outcome = "NO_DECISION" if blocked else "CANDIDATE"
    knowledge = ("UNKNOWN" if any(c["knowledge"] == "UNKNOWN" for c in judgments) else
                 "INFERRED" if any(c["knowledge"] == "INFERRED" for c in judgments) else "OBSERVED")
    closure = _fields(material["closure"], {"outcome", "knowledge", "summary", "candidate_fingerprint"}, "AOA closure")
    _text(closure["summary"], 2048, "AOA closure summary")
    _aoa_private(closure["summary"])
    if closure["outcome"] != outcome or closure["knowledge"] != knowledge:
        raise BrainAuditError("AOA closure contradicts comparison or retained blockers")
    if blocked:
        if candidate is not None or closure["candidate_fingerprint"] is not None:
            raise BrainAuditError("AOA NO_DECISION cannot expose a planning candidate")
    elif candidate is None or closure["candidate_fingerprint"] != _digest(candidate):
        raise BrainAuditError("AOA closure must bind one final planning candidate")
    changed = any(_json(material[k]) != _json(first[old]) for k, old in (
        ("alternatives", "alternatives"), ("information", "information"), ("reconciled_candidate", "draft_candidate")))
    addressed = any(c["disposition"] == "ADDRESSED_BY_RECONCILIATION" for c in challenges)
    # Suppressing a draft on NO_DECISION is not a claimed resolution.
    substantive = changed if not blocked else any(_json(material[k]) != _json(first[k]) for k in ("alternatives", "information"))
    if substantive != addressed:
        raise BrainAuditError("AOA addressed challenge contradicts the bound reconciliation delta")
    result = {"format": "AIOS_BRAIN_ARCHITECTURE_OUTCOME_AUDIT", "version": 1, "stage": "ADVERSARIAL_AUDIT_AND_RECONCILE",
              "subject_fingerprint": bound["subject_fingerprint"], "construct_fingerprint": first["construct_fingerprint"],
              **{k: material[k] for k in ("challenges", "alternatives", "information", "reconciliation", "closure")},
              "handoff_candidate": candidate}
    result["audit_fingerprint"] = _digest(result)
    return result


__all__ = [
    "BrainAuditError", "parse_profile_registry", "normalize_profile", "normalize_audit_policy", "profile_ref",
    "profile_applies", "construct_stage1", "validate_stage2", "construct_roa_subject", "validate_roa",
    "construct_moa_subject", "construct_moa", "validate_moa",
    "construct_tca_subject", "construct_tca", "validate_tca",
    "construct_aoa_subject", "construct_aoa", "validate_aoa",
]
