"""CANONICAL_CONTEXT_PIPELINE_V1 and transient semantic Flow Card contracts.

Brain Sync and Unified State retain all engineering lifecycle authority. This module
compiles fresh observations with closed relevance/elision rules, disposable exact
reuse and a deterministic budget. Only supporting detail can overflow into a
non-authoritative representation; decision dependencies require exact rebound
sources through require_exact_context. No history/spec bodies are hydrated by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from .brain_sync import (
    BrainSyncError, BrainSyncSnapshot, ELISION_RULE_VERSION, PLANNING_PROJECTION_VERSION,
    _read_main_source, _RoadmapLoader, observe_brain_sync, project_active_planning,
)
from .unified_state import _UNIFIED_NEXT_ACTIONS


class BrainContextError(ValueError):
    """A request, observation, or Flow Card cannot be interpreted safely."""


class ContextBudgetError(BrainContextError):
    """Exact control facts cannot fit; no lossy fallback is authorized."""


class ExactExpansionRequired(BrainContextError):
    """A decision dependency has only a derived representation."""


CONTEXT_PIPELINE_VERSION = "CANONICAL_CONTEXT_PIPELINE_V1"
PROJECTION_RULE_VERSION = "FLOW_CARD_CONTEXT_PROJECTION_V1"
DEFAULT_CONTEXT_BUDGET = 131072
MAX_CONTEXT_BUDGET = 1048576
PIPELINE_ORDER = (
    "FRESH_CANONICAL_ANCHORS", "DETERMINISTIC_RELEVANCE_PROJECTION", "RULE_BASED_ELISION",
    "EXACT_DIGEST_BOUND_REUSE", "DETERMINISTIC_CONTEXT_BUDGET",
    "BOUNDED_SUMMARIZATION_ONLY_IF_STILL_OVER_BUDGET", "EXACT_EXPANSION_ON_DECISION_DEPENDENCY",
    "BRAIN_REASONING",
)
_PIPELINE_CONTRACT = {
    "id": CONTEXT_PIPELINE_VERSION, "version": 1,
    "projection_rules": PROJECTION_RULE_VERSION, "elision_rules": ELISION_RULE_VERSION,
    "budget_metric": "CANONICAL_JSON_UTF8_SEMANTIC_PAYLOAD_V1",
    "default_budget_bytes": DEFAULT_CONTEXT_BUDGET,
    "overflow_rule": "SUPPORTING_DETAIL_UTF8_PREFIX_256_V1",
    "exact_expansion": "REQUIRED_BEFORE_DECISION_DEPENDENCY",
    "compatibility_exits": ["BOUNDED_ACTIVE_PLANNING_PROJECTION_ACTIVE",
                            "ROADMAP_SINGLE_EFFECTIVE_NEXT_ACTIVE",
                            "MINIMUM_FRESH_BRAIN_SYNC_PRODUCTION_ACTIVE"],
}
_REQUEST_CLASSES = frozenset({"CONTINUATION", "PLANNING", "REVIEW", "CORRECTION", "DIAGNOSTIC", "RESEARCH"})
_EXACT_OBSERVATION_FIELDS = frozenset({
    "format", "version", "kind", "repository", "main_sha", "roadmap", "selection_status",
    "selected_task", "unified_state", "lifecycle_state", "next_action", "authority", "blocker",
    "task_contract", "source_bindings", "repair_supersession", "supporting_context",
    "run_created", "executor_invoked", "verification_invoked", "state_mutated",
})


_FLOWS = frozenset({
    "ARCHITECTURE", "TASK_AUTHORING", "SEMANTIC_REVIEW",
    "REMEDIATION_AUTHORING", "REPAIR_AUTHORING", "DIAGNOSTIC", "RESEARCH",
})
_EXPLICIT = frozenset({"ARCHITECTURE", "TASK_AUTHORING", "DIAGNOSTIC", "RESEARCH", "REPAIR_AUTHORING"})
_OBLIGATIONS = {
    "SEMANTIC_REVIEW": "SEMANTIC_REVIEW",
    "AUTHOR_REMEDIATION": "REMEDIATION_AUTHORING",
    "AUTHOR_REPAIR": "REPAIR_AUTHORING",
}
_OWNERS = {flow: "BRAIN" for flow in _FLOWS}
_OWNERS["SEMANTIC_REVIEW"] = "REVIEWER"
_FAMILIES = {
    "ARCHITECTURE": "HUMAN_BRAIN_ARCHITECTURE_PLANNING",
    "TASK_AUTHORING": "task.validate_task+authoring_ingress",
    "SEMANTIC_REVIEW": "review.validate_review",
    "REMEDIATION_AUTHORING": "review.validate_remediation",
    "REPAIR_AUTHORING": "publication._validate_repair_authorization",
    "DIAGNOSTIC": "HUMAN_BRAIN_DIAGNOSTIC",
    "RESEARCH": "HUMAN_BRAIN_RESEARCH_ASSURANCE",
}
_HANDOFFS = {
    "ARCHITECTURE": "HUMAN_BRAIN_PLANNING",
    "TASK_AUTHORING": "AUTHORING_INGRESS",
    "SEMANTIC_REVIEW": "AUTHORING_INGRESS",
    "REMEDIATION_AUTHORING": "AUTHORING_INGRESS",
    "REPAIR_AUTHORING": "AUTHORING_INGRESS",
    "DIAGNOSTIC": "HUMAN_BRAIN_PLANNING",
    "RESEARCH": "RESEARCH_PROTOCOL",
}
_RETURNS = {
    "ARCHITECTURE": "BOUNDED_SEMANTIC_PROPOSAL",
    "TASK_AUTHORING": "TASK_AUTHORING_PROPOSAL",
    "SEMANTIC_REVIEW": "REVIEW_CONTRACT_PROPOSAL",
    "REMEDIATION_AUTHORING": "REMEDIATION_CONTRACT_PROPOSAL",
    "REPAIR_AUTHORING": "REPAIR_AUTHORIZATION_PROPOSAL",
    "DIAGNOSTIC": "BOUNDED_DIAGNOSTIC_PROPOSAL",
    "RESEARCH": "RESEARCH_PACKET",
}
_ENTRIES = {
    "ARCHITECTURE": frozenset({"EXPLICIT_SELECTOR"}),
    "TASK_AUTHORING": frozenset({"EXPLICIT_SELECTOR", "UNIQUE_UNAUTHORED_NEXT"}),
    "SEMANTIC_REVIEW": frozenset({"UNIFIED_STATE_SEMANTIC_REVIEW"}),
    "REMEDIATION_AUTHORING": frozenset({"UNIFIED_STATE_AUTHOR_REMEDIATION"}),
    "REPAIR_AUTHORING": frozenset({"UNIFIED_STATE_AUTHOR_REPAIR", "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION"}),
    "DIAGNOSTIC": frozenset({"EXPLICIT_SELECTOR"}),
    "RESEARCH": frozenset({"EXPLICIT_SELECTOR"}),
}
_CONTEXT_PATHS = frozenset({
    "canonical_observation.repository", "canonical_observation.main_sha",
    "canonical_observation.roadmap", "canonical_observation.selection_status",
    "canonical_observation.selected_task", "canonical_observation.unified_state",
    "canonical_observation.blocker", "current_request.flow_selector",
    "current_request.human_input", "canonical_observation.repair_supersession",
    "canonical_observation.supporting_context", "current_request.request_class",
})
_REQUIRED_CONTEXT = {
    "ARCHITECTURE": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "current_request.flow_selector"}),
    "TASK_AUTHORING": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.roadmap", "canonical_observation.blocker"}),
    "SEMANTIC_REVIEW": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.selected_task", "canonical_observation.unified_state"}),
    "REMEDIATION_AUTHORING": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.selected_task", "canonical_observation.unified_state"}),
    "REPAIR_AUTHORING": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.selected_task", "canonical_observation.unified_state"}),
    "DIAGNOSTIC": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.blocker", "current_request.flow_selector"}),
    "RESEARCH": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "current_request.flow_selector"}),
}
_FORBIDDEN = frozenset({
    "chat_history", "credentials", "raw_logs", "unbounded_repository_content",
})
_INVALIDATION = frozenset({
    "WORK_CONTEXT_FINGERPRINT_CHANGE", "FRESH_COMPOSITION_FOR_CONTINUATION",
})
_CARD_FIELDS = frozenset({
    "id", "entry_conditions", "required_context", "optional_context",
    "forbidden_context", "authority_owner", "decision_family_ref",
    "handoff_target", "expected_return_shape", "invalidation_rules", "optional_context_rules",
})
_OPTIONAL_RULES = {
    "canonical_observation.roadmap": "EXACT_CONTROL_ALWAYS",
    "canonical_observation.blocker": "EXACT_CONTROL_ALWAYS",
    "canonical_observation.selected_task": "EXACT_CONTROL_ALWAYS",
    "canonical_observation.unified_state": "EXACT_CONTROL_ALWAYS",
    "canonical_observation.repair_supersession": "EXACT_CONTROL_ALWAYS",
    "current_request.flow_selector": "EXACT_CONTROL_ALWAYS",
    "current_request.human_input": "EXACT_CONTROL_ALWAYS",
    "current_request.request_class": "EXACT_CONTROL_ALWAYS",
    "canonical_observation.supporting_context": "CURRENT_TASK_PROBLEM_WHEN_PRESENT",
}
_EXACT_DEPENDENCY_ROOTS = _CONTEXT_PATHS | frozenset({
    "canonical_observation.lifecycle_state", "canonical_observation.next_action",
    "canonical_observation.authority", "canonical_observation.task_contract",
})


def _stable_copy(value: Any) -> Any:
    """Detach caller-owned mappings and require strictly serializable Unicode."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
        encoded.encode("utf-8", errors="strict")
        return json.loads(encoded)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BrainContextError("context is not bounded JSON data") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8", errors="strict")).hexdigest()


def _request(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or not value or set(value) - {"flow_selector", "human_input", "request_class"}:
        raise BrainContextError("current_request must be a nonempty strict mapping")
    selector = value.get("flow_selector")
    if "flow_selector" in value and (not isinstance(selector, str) or selector not in _EXPLICIT):
        raise BrainContextError("invalid flow_selector")
    if "request_class" in value and (not isinstance(value["request_class"], str)
                                    or value["request_class"] not in _REQUEST_CLASSES):
        raise BrainContextError("invalid closed request_class")
    human_input = value.get("human_input")
    if "human_input" in value:
        if not isinstance(human_input, str):
            raise BrainContextError("human_input must be UTF-8 text")
        try:
            size = len(human_input.encode("utf-8", errors="strict"))
        except UnicodeError as exc:
            raise BrainContextError("invalid human_input Unicode") from exc
        if size > 16384:
            raise BrainContextError("human_input exceeds 16384 UTF-8 bytes")
    return dict(value)


def _invalidation_basis(observed: Mapping[str, Any], request: Mapping[str, str] | None) -> dict[str, str]:
    basis = {
        # Root and remote are operational observations. Brain Sync's name is
        # the canonical repository identity and remains stable across checkouts.
        "repository_identity_sha256": _digest(observed["repository"]["name"]),
        "main_sha": observed["main_sha"],
        "roadmap_selection_sha256": _digest({
            "roadmap": observed["roadmap"],
            "selection_status": observed["selection_status"],
        }),
        "semantic_subject_lifecycle_sha256": _digest({
            "selected_task": observed["selected_task"],
            "unified_state": observed["unified_state"],
            "lifecycle_state": observed["lifecycle_state"],
            "next_action": observed["next_action"],
        }),
        "blocker_sha256": _digest(observed["blocker"]),
        "authority_sha256": _digest(observed["authority"]),
        "task_contract_sha256": _digest(observed.get("task_contract")),
        "source_bindings_sha256": _digest(observed.get("source_bindings")),
        "current_request_sha256": _digest(request),
    }
    if "repair_supersession" in observed:
        basis["repair_supersession_sha256"] = _digest(observed["repair_supersession"])
    return basis


def _supersession_facts(material: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the read-only reconstruction without projecting Runtime diagnostics."""
    return {
        "failed_run_id": material["failed_run"]["run_id"],
        "failed_head_sha": material["failure"]["failed_head_sha"],
        "failure_artifacts_sha": material["failure_artifacts_sha"],
        "current_authorization": _stable_copy(material["current_authorization"]),
        "canonical_material_sha256": _digest(material),
    }


@dataclass(frozen=True)
class BrainWorkContext:
    canonical_observation: Mapping[str, Any]
    current_request: Mapping[str, str] | None
    invalidation_basis: Mapping[str, Any]
    invalidation_fingerprint: str
    operational_repository_root: str
    # Private compiler input; raw FAILURE diagnostics never become Brain facts.
    repair_supersession_material: Mapping[str, Any] | None = field(default=None, repr=False)
    compilation: Mapping[str, Any] = field(default_factory=dict)
    compilation_integrity: str = field(default="", repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "AIOS_BRAIN_WORK_CONTEXT", "version": 1,
            "kind": "BRAIN_WORK_CONTEXT",
            "canonical_observation": _stable_copy(self.canonical_observation),
            "current_request": _stable_copy(self.current_request),
            "invalidation_basis": _stable_copy(self.invalidation_basis),
            "invalidation_fingerprint": self.invalidation_fingerprint,
            "context_pipeline": _stable_copy(self.compilation),
            "run_created": False, "executor_invoked": False,
            "verification_invoked": False, "state_mutated": False,
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class DerivedContextCache:
    """Disposable, bounded exact fragments. Never persisted and never authoritative."""

    def __init__(self, max_bytes: int = DEFAULT_CONTEXT_BUDGET):
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_CONTEXT_BUDGET:
            raise BrainContextError("invalid derived cache bound")
        self.max_bytes = max_bytes
        self._entries: dict[str, tuple[dict[str, Any], Any, int]] = {}
        self.hits = 0
        self.misses = 0

    def clear(self) -> None:
        self._entries.clear()

    def exact(self, binding: Mapping[str, Any], current: Any) -> Any:
        key = _digest(binding)
        expected = _digest(current)
        cached = self._entries.get(key)
        if cached is not None and cached[0] == binding and _digest(cached[1]) == expected:
            self.hits += 1
            return _stable_copy(cached[1])
        self.misses += 1
        value = _stable_copy(current)
        size = _byte_size(value)
        if size <= self.max_bytes:
            self._entries.pop(key, None)
            while self._entries and (sum(entry[2] for entry in self._entries.values()) + size > self.max_bytes
                                     or len(self._entries) >= 64):
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (_stable_copy(binding), value, size)
        return _stable_copy(value)


def _byte_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8", errors="strict"))


def _select_semantic_flow(observed: Mapping[str, Any], request: Mapping[str, str] | None,
                          supersession: Mapping[str, Any] | None) -> tuple[str, str, str | None]:
    unified = observed["unified_state"]
    unified_action = unified["next_action"] if unified is not None else None
    pending = _OBLIGATIONS.get(unified_action)
    explicit = request.get("flow_selector") if request else None
    if explicit == "REPAIR_AUTHORING":
        if (observed["next_action"] != "EXECUTE_REPAIR" or unified_action != "EXECUTE_REPAIR"
                or observed["blocker"] is not None or not isinstance(unified, Mapping)
                or unified.get("blocker") is not None or supersession is None
                or observed.get("repair_supersession") != _supersession_facts(supersession)
                or unified.get("failed_run_id") != observed["repair_supersession"]["failed_run_id"]
                or unified.get("failed_head_sha") != observed["repair_supersession"]["failed_head_sha"]
                or unified.get("correction_sha") != observed["repair_supersession"]["current_authorization"]["authorization_sha"]):
            raise BrainContextError("explicit REPAIR_AUTHORING requires exact unexecuted canonical supersession")
        return explicit, "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION", pending
    if explicit is not None:
        return explicit, "EXPLICIT_SELECTOR", pending
    if pending is not None:
        return pending, "UNIFIED_STATE", pending
    if observed["selection_status"] == "UNAUTHORED_TASK":
        proof = observed["roadmap"].get("next_proof", {})
        blocker = observed["blocker"]
        if (proof.get("status") == "UNIQUE" and observed["selected_task"] is None
                and observed["unified_state"] is None
                and (blocker is None or blocker.get("code") == "UNAUTHORED_TASK")):
            return "TASK_AUTHORING", "UNIQUE_UNAUTHORED_NEXT", pending
    return "NONE", "NO_SEMANTIC_FLOW", pending


def _validate_anchors(observed: dict[str, Any]) -> None:
    if (set(observed) - _EXACT_OBSERVATION_FIELDS or observed.get("format") != "AIOS_BRAIN_SYNC_SNAPSHOT"
            or type(observed.get("version")) is not int or observed["version"] != 1
            or not isinstance(observed.get("repository"), dict)
            or not isinstance(observed["repository"].get("name"), str)
            or not observed["repository"]["name"]
            or not isinstance(observed.get("main_sha"), str) or len(observed["main_sha"]) != 40
            or any(ch not in "0123456789abcdef" for ch in observed["main_sha"])
            or not isinstance(observed.get("roadmap"), dict)):
        raise BrainContextError("malformed canonical context anchors")
    for name in ("lifecycle_state", "next_action", "authority", "selection_status"):
        if not isinstance(observed.get(name), str) or not observed[name]:
            raise BrainContextError("missing exact lifecycle/control anchor")
    if observed["next_action"] not in _UNIFIED_NEXT_ACTIONS | {"TASK_AUTHORING"}:
        raise BrainContextError("unknown closed lifecycle next_action")
    if observed["authority"] not in {"NONE", "BRAIN", "REVIEWER", "PUBLICATION", "HUMAN_RUNTIME", "HUMAN_BRAIN_PLANNING"}:
        raise BrainContextError("unknown canonical authority owner")
    if observed["repository"].get("main_sha") != observed["main_sha"]:
        raise BrainContextError("competing canonical main anchors")
    for name in ("selected_task", "unified_state", "blocker", "task_contract", "source_bindings"):
        if observed.get(name) is not None and not isinstance(observed[name], dict):
            raise BrainContextError("malformed exact subject/control anchor")
    for flag in ("run_created", "executor_invoked", "verification_invoked", "state_mutated"):
        if observed.get(flag) is not False:
            raise BrainContextError("context composition requires observation-only anchors")
    selected = observed["selected_task"]
    if selected is not None and (set(selected) != {"id", "revision"}
            or not isinstance(selected["id"], str) or re.fullmatch(r"TASK-[0-9]+", selected["id"]) is None
            or type(selected["revision"]) is not int or selected["revision"] < 1):
        raise BrainContextError("malformed exact selected TASK subject")
    unified = observed["unified_state"]
    if unified is not None:
        if not isinstance(unified.get("next_action"), str) or unified["next_action"] not in _UNIFIED_NEXT_ACTIONS:
            raise BrainContextError("malformed Unified State relevance input")
        task_subject = unified.get("task")
        if (not isinstance(task_subject, dict) or set(task_subject) != {"id", "revision"}
                or not isinstance(task_subject["id"], str) or re.fullmatch(r"TASK-[0-9]+", task_subject["id"]) is None
                or type(task_subject["revision"]) is not int or task_subject["revision"] < 1):
            raise BrainContextError("malformed exact Unified State subject")
        selected = observed["selected_task"]
        if selected is not None and unified.get("task") != selected:
            raise BrainContextError("competing exact semantic subjects")
        if observed["next_action"] != "NONE" and unified["next_action"] != observed["next_action"]:
            raise BrainContextError("competing lifecycle next_action facts")
    if observed.get("task_contract") is not None:
        contract = observed["task_contract"]
        subject = observed["selected_task"] or (unified.get("task") if unified else None)
        if subject is None or contract.get("task_id") != subject["id"] or contract.get("revision") != subject["revision"]:
            raise BrainContextError("current TASK contract has a competing exact subject")
    sources = observed.get("source_bindings") or {}
    if set(sources) - {"roadmap", "task"}:
        raise BrainContextError("unknown canonical source binding class")
    for source in sources.values():
        if (not isinstance(source, dict) or set(source) != {"commit", "path", "source_digest"}
                or source["commit"] != observed["main_sha"]
                or not isinstance(source["path"], str) or not source["path"].startswith(".ai/")
                or not isinstance(source["source_digest"], str) or len(source["source_digest"]) != 64
                or any(ch not in "0123456789abcdef" for ch in source["source_digest"])):
            raise BrainContextError("incomplete or moved exact source binding")
    if sources and ("roadmap" not in sources or (observed.get("task_contract") is not None and "task" not in sources)):
        raise BrainContextError("incomplete current canonical source bindings")
    if "roadmap" in sources and sources["roadmap"]["path"] != ".ai/roadmap-state.yaml":
        raise BrainContextError("roadmap source binding has a competing path")
    if "task" in sources:
        subject = observed["selected_task"] or (unified.get("task") if unified else None)
        if subject is None or sources["task"]["path"] != f".ai/tasks/{subject['id']}.yaml":
            raise BrainContextError("TASK source binding has a competing exact subject path")
    roadmap = observed["roadmap"]
    if "sequence" in roadmap:
        try:
            observed["roadmap"] = project_active_planning(roadmap)
        except BrainSyncError as exc:
            raise BrainContextError("ambiguous planning relevance input") from exc
        roadmap = observed["roadmap"]
    proof = roadmap.get("next_proof")
    if proof is not None:
        candidate = roadmap.get("effective_next")
        if (roadmap.get("projection_version") != PLANNING_PROJECTION_VERSION
                or not isinstance(proof, dict) or proof.get("status") not in {"UNIQUE", "NONE", "CONFLICT"}
                or not isinstance(proof.get("sequence_next_ids"), list)
                or not isinstance(proof.get("conflicts"), list)
                or (proof["status"] == "UNIQUE" and (
                    not isinstance(candidate, dict) or candidate.get("status") != "NEXT"
                    or proof["sequence_next_ids"] != [candidate.get("id")] or proof["conflicts"]))
                or (proof["status"] != "UNIQUE" and candidate is not None)):
            raise BrainContextError("malformed or competing effective NEXT proof")
        if proof["status"] == "UNIQUE":
            from .brain_sync import _mirror_ids
            try:
                if _mirror_ids(roadmap["next_items"], [candidate]) != [candidate["id"]]:
                    raise BrainSyncError("NEXT mirror mismatch")
            except (BrainSyncError, KeyError) as exc:
                raise BrainContextError("competing effective NEXT compatibility facts") from exc
            if any(row.get("status") == "NEXT" for row in roadmap.get("active_controls", [])):
                raise BrainContextError("competing active planning subjects")
            selected = observed["selected_task"]
            if selected is not None and (candidate.get("task_id") != selected["id"] or
                    candidate.get("task_revision", selected["revision"]) != selected["revision"]):
                raise BrainContextError("roadmap and semantic TASK association disagree")
        if proof["status"] == "CONFLICT" and (observed["blocker"] is None
                or observed["next_action"] != "NONE" or observed["authority"] != "NONE"):
            raise BrainContextError("planning conflict lacks fail-closed control anchors")
    elif roadmap.get("present") is not False and roadmap.get("valid") is not False:
        raise BrainContextError("exact active planning proof is required")


def _semantic_payload(observed: Mapping[str, Any], request: Mapping[str, str] | None,
                      compilation: Mapping[str, Any]) -> dict[str, Any]:
    return {"canonical_observation": observed, "current_request": request,
            "context_contract": {key: value for key, value in compilation.items() if key != "budget"}}


def _compiled_basis(observed: Mapping[str, Any], request: Mapping[str, str] | None,
                    compilation: Mapping[str, Any]) -> dict[str, str]:
    payload = _stable_copy(_semantic_payload(observed, request, compilation))
    payload["canonical_observation"]["repository"] = {
        "name": observed["repository"]["name"], "main_sha": observed["main_sha"],
    }
    payload["context_contract"]["elision_manifest"] = [entry for entry in compilation["elision_manifest"]
                                                       if entry["class"] != "OPERATIONAL_METADATA"]
    return {**_invalidation_basis(observed, request),
            "compiled_context_sha256": _digest(payload),
            "budget_sha256": _digest({"metric": compilation["budget"]["metric"],
                                     "limit_bytes": compilation["budget"]["limit_bytes"],
                                     "status": compilation["budget"]["status"]})}


def _compile_context(observed: dict[str, Any], request: Mapping[str, str] | None,
                     cards: Mapping[str, Any], registry_binding: Mapping[str, Any],
                     supersession: Mapping[str, Any] | None, budget: int,
                     cache: DerivedContextCache | None, operational_manifest: list[dict[str, Any]]) -> dict[str, Any]:
    if type(budget) is not int or not 512 <= budget <= MAX_CONTEXT_BUDGET:
        raise BrainContextError("invalid deterministic context budget")
    if cache is not None and not isinstance(cache, DerivedContextCache):
        raise BrainContextError("invalid disposable derived cache")
    _validate_anchors(observed)
    selected, selection_basis, pending = _select_semantic_flow(observed, request, supersession)
    card = cards.get(selected)
    if selected in {"SEMANTIC_REVIEW", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING"} and (
            observed["selected_task"] is None or observed["unified_state"] is None):
        raise BrainContextError("Flow Card requires an exact current semantic subject")
    request_class = request.get("request_class", "CONTINUATION") if request else "CONTINUATION"
    planning_subject = observed["roadmap"].get("effective_next")
    projection_inputs = {
        "selected_flow": selected, "selection_basis": selection_basis,
        "lifecycle_state": observed["lifecycle_state"], "next_action": observed["next_action"],
        "subject_type": "REVIEW" if selected == "SEMANTIC_REVIEW" else "CORRECTION"
                        if selected in {"REMEDIATION_AUTHORING", "REPAIR_AUTHORING"} else
                        "TASK" if observed["selected_task"] is not None else "PLANNING",
        "subject": observed["selected_task"] or (planning_subject.get("id") if planning_subject else None),
        "required_context": card["required_context"] if card else [],
        "optional_context_rules": card["optional_context_rules"] if card else {},
        "canonical_blocker": observed["blocker"], "explicit_side_flow": request.get("flow_selector") if request else None,
        "request_class": request_class, "pending_canonical_obligation": pending,
    }
    # Binding covers fresh source facts, not the result of relevance reasoning.
    semantic_source = _stable_copy(observed)
    semantic_source["repository"] = {"name": observed["repository"]["name"]}
    binding = {
        "repository_identity_sha256": _digest(observed["repository"]["name"]),
        "canonical_main_sha": observed["main_sha"], "source_identity": observed.get("source_bindings") or {
            "observation": {"kind": "EXACT_BRAIN_SYNC_SNAPSHOT", "commit": observed["main_sha"]}},
        "source_digest": _digest(semantic_source), "structural_selector": "BRAIN_WORK_CONTEXT_V1",
        "flow_card_id": selected, "flow_card_digest": _digest(card), "flow_card_version": 1,
        "flow_registry": registry_binding, "projection_rule_version": PROJECTION_RULE_VERSION,
        "elision_rule_version": ELISION_RULE_VERSION, "context_pipeline_version": CONTEXT_PIPELINE_VERSION,
        "relevance_inputs_digest": _digest(projection_inputs), "current_request_digest": _digest(request),
    }
    planning_manifest = observed["roadmap"].get("elision_manifest", [])
    if not isinstance(planning_manifest, list) or len(planning_manifest) > 16:
        raise BrainContextError("unbounded or malformed elision provenance")
    manifest = _stable_copy(planning_manifest) + operational_manifest
    for entry in manifest:
        if (not isinstance(entry, dict) or entry.get("rule_version") != ELISION_RULE_VERSION
                or entry.get("rule") not in {"OMIT_DONE_SEQUENCE_BODIES_V1", "OMIT_NONSELECTED_DETAIL_FIELDS_V1",
                                              "OMIT_TRANSPORT_URL_V1", "OMIT_COMPLETED_CONTROL_HISTORY_V1"}
                or not isinstance(entry.get("source_digest"), str) or len(entry["source_digest"]) != 64):
            raise BrainContextError("unknown or ambiguous context elision rule")
    supporting = observed.get("supporting_context") or {}
    if (not isinstance(supporting, dict) or set(supporting) - {"task_problem"}
            or any(not isinstance(body, str) or _byte_size(body) > MAX_CONTEXT_BUDGET for body in supporting.values())):
        raise BrainContextError("unknown or unbounded supporting context class")
    material_index = {}
    include_support = bool(card and card["optional_context_rules"].get(
        "canonical_observation.supporting_context") == "CURRENT_TASK_PROBLEM_WHEN_PRESENT")
    for name, body in supporting.items():
        selector = f"canonical_observation.supporting_context.{name}"
        material_index[selector] = {"class": "CURRENT_TASK_SUPPORTING_DETAIL", "source_digest": _digest(body),
                                    "source_selector": "task#/problem", "representation": "EXACT" if include_support else "ELIDED"}
        if not include_support:
            manifest.append({"class": "NONSELECTED_FLOW_DETAIL", "rule": "OMIT_NONSELECTED_TASK_PROBLEM_V1",
                             "rule_version": ELISION_RULE_VERSION, "selector": selector,
                             "source_digest": _digest(body), "count": 1})
    observed["supporting_context"] = supporting if include_support else None
    # Exact reuse follows projection/elision, and never substitutes for fresh controls.
    if include_support and cache is not None:
        required_sources = {"roadmap", "task"} if observed["selected_task"] else {"roadmap"}
        if not required_sources <= set(observed.get("source_bindings") or {}):
            raise BrainContextError("exact reuse requires complete current canonical source bindings")
        observed["supporting_context"] = cache.exact({**binding,
            "structural_selector": "canonical_observation.supporting_context"}, supporting)
    compilation = {
        "id": CONTEXT_PIPELINE_VERSION, "version": 1, "order": list(PIPELINE_ORDER),
        "projection_inputs": projection_inputs, "flow_card": _stable_copy(card),
        "bindings": binding, "elision_manifest": manifest, "material_index": material_index,
        "overflow": {}, "requires_exact_expansion": bool(planning_manifest) or
            any(v["representation"] != "EXACT" for v in material_index.values()),
        "summary_authority": "NONE", "reuse_authority": "NONE",
        "compatibility_exits": _PIPELINE_CONTRACT["compatibility_exits"],
    }
    exact_bytes = _byte_size(_semantic_payload(observed, request, compilation))
    if exact_bytes > budget:
        for name, body in (observed["supporting_context"] or {}).items():
            selector = f"canonical_observation.supporting_context.{name}"
            compilation["overflow"][selector] = {
                "class": "CURRENT_TASK_SUPPORTING_DETAIL", "rule": _PIPELINE_CONTRACT["overflow_rule"],
                "summary": body.encode("utf-8")[:256].decode("utf-8", errors="ignore"),
                "source_digest": _digest(body), "canonical_authority": "NONE", "exact_expansion_required": True,
            }
            material_index[selector]["representation"] = "SUMMARY"
        observed["supporting_context"] = None
        compilation["requires_exact_expansion"] = bool(material_index) or bool(planning_manifest)
    compiled_bytes = _byte_size(_semantic_payload(observed, request, compilation))
    if compiled_bytes > budget:
        raise ContextBudgetError("non-elidable control facts exceed the declared context budget")
    compilation["budget"] = {"metric": _PIPELINE_CONTRACT["budget_metric"], "limit_bytes": budget,
                              "exact_projected_bytes": exact_bytes, "compiled_bytes": compiled_bytes,
                              "status": "OVERFLOW_SUMMARIZED" if compilation["overflow"] else "EXACT"}
    return _stable_copy(compilation)


def compose_brain_work_context(
    snapshot: BrainSyncSnapshot | None = None,
    current_request: Mapping[str, Any] | None = None,
    *,
    repo: str | Path | None = None,
    budget_bytes: int = DEFAULT_CONTEXT_BUDGET,
    reuse_cache: DerivedContextCache | None = None,
) -> BrainWorkContext:
    """Compose one request-scoped view from current Brain Sync observation."""
    if snapshot is None:
        snapshot = observe_brain_sync(repo=repo)
    elif repo is not None or not isinstance(snapshot, BrainSyncSnapshot):
        raise BrainContextError("supply either a Brain Sync snapshot or repository")
    request = _request(current_request)
    observed = snapshot.as_dict()
    # Brain Sync may expose a transport URL for operators. It is not needed by
    # reasoning and can contain credentials, so only canonical identity is copied.
    repository = observed["repository"]
    operational_manifest = []
    if "remote_url" in repository:
        operational_manifest.append({"class": "OPERATIONAL_METADATA", "rule": "OMIT_TRANSPORT_URL_V1",
            "rule_version": ELISION_RULE_VERSION, "selector": "repository.remote_url",
            "source_digest": _digest(repository["remote_url"]), "count": 1})
    observed["repository"] = {
        key: repository.get(key) for key in ("root", "name", "main_sha", "remote")
    }
    observed = _stable_copy(observed)
    if observed["format"] != "AIOS_BRAIN_SYNC_SNAPSHOT" or observed["version"] != 1:
        raise BrainContextError("unsupported Brain Sync snapshot")
    root = observed["repository"].get("root")
    if not isinstance(root, str) or not root:
        raise BrainContextError("Brain Sync repository root is missing")
    supersession = None
    if request and request.get("flow_selector") == "REPAIR_AUTHORING":
        # Reuse ingress's canonical read glue, not caller-supplied authorization
        # text or an alternate lifecycle reducer. This import is deliberately
        # lazy: ordinary BP-3 classification remains observation-only.
        from .authoring_ingress import _canonical_repair_supersession
        from .review_transport import ReviewTransportError
        import subprocess
        try:
            supersession = _stable_copy(_canonical_repair_supersession(observed))
            observed["repair_supersession"] = _supersession_facts(supersession)
        except (ValueError, OSError, KeyError, TypeError, BrainSyncError,
                ReviewTransportError, subprocess.SubprocessError) as exc:
            raise BrainContextError(f"explicit REPAIR supersession rejected: {exc}") from exc
    cards, registry_binding = _load_flow_registry(Path(observed["repository"]["root"]) / ".ai" / "flow-cards.yaml")
    compilation = _compile_context(observed, request, cards, registry_binding, supersession,
                                   budget_bytes, reuse_cache, operational_manifest)
    basis = _compiled_basis(observed, request, compilation)
    fingerprint = _digest(basis)
    return BrainWorkContext(observed, request, basis, fingerprint, observed["repository"]["root"], supersession,
                            compilation, _digest([observed, request, compilation]))


def _tokens(value: Any, *, allowed: frozenset[str], nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value) or any(
        not isinstance(item, str) or item not in allowed for item in value
    ) or len(value) != len(set(value)):
        raise BrainContextError("invalid Flow Card token list")
    return value


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise BrainContextError("duplicate or non-text Flow Card key")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _load_flow_registry(card_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        if card_path.stat().st_size > 65536:
            raise BrainContextError("Flow Card registry exceeds its exact source bound")
        source_bytes = card_path.read_bytes()
        raw = yaml.load(source_bytes.decode("utf-8", errors="strict"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise BrainContextError("cannot load Flow Cards") from exc
    if not isinstance(raw, dict) or set(raw) != {"format", "version", "cards", "context_pipeline"} or \
            raw["format"] != "AIOS_FLOW_CARDS" or type(raw["version"]) is not int or \
            raw["version"] != 1 or _digest(raw["context_pipeline"]) != _digest(_PIPELINE_CONTRACT) or \
            not isinstance(raw["cards"], list) or len(raw["cards"]) != 7:
        raise BrainContextError("invalid Flow Card registry")
    cards: dict[str, dict[str, Any]] = {}
    for card in raw["cards"]:
        if not isinstance(card, dict) or set(card) != _CARD_FIELDS:
            raise BrainContextError("malformed Flow Card")
        flow = card["id"]
        if not isinstance(flow, str) or flow not in _FLOWS or flow in cards:
            raise BrainContextError("unknown or duplicate Flow Card")
        entries = _tokens(card["entry_conditions"], allowed=_ENTRIES[flow])
        if frozenset(entries) != _ENTRIES[flow]:
            raise BrainContextError("incomplete Flow Card entry conditions")
        required = _tokens(card["required_context"], allowed=_CONTEXT_PATHS)
        optional = _tokens(card["optional_context"], allowed=_CONTEXT_PATHS, nonempty=False)
        if set(required) & set(optional) or frozenset(required) != _REQUIRED_CONTEXT[flow]:
            raise BrainContextError("invalid Flow Card context contract")
        if (not isinstance(card["optional_context_rules"], dict)
                or card["optional_context_rules"] != {path: _OPTIONAL_RULES.get(path) for path in optional}
                or any(path not in _OPTIONAL_RULES for path in optional)):
            raise BrainContextError("unknown or ambiguous Flow Card relevance rule")
        forbidden = _tokens(card["forbidden_context"], allowed=_FORBIDDEN)
        invalidation = _tokens(card["invalidation_rules"], allowed=_INVALIDATION)
        if frozenset(forbidden) != _FORBIDDEN or frozenset(invalidation) != _INVALIDATION:
            raise BrainContextError("incomplete Flow Card safety contract")
        if (card["authority_owner"] != _OWNERS[flow]
                or card["decision_family_ref"] != _FAMILIES[flow]
                or card["handoff_target"] != _HANDOFFS[flow]
                or card["expected_return_shape"] != _RETURNS[flow]):
            raise BrainContextError("Flow Card authority or decision family mismatch")
        cards[flow] = _stable_copy(card)
    if set(cards) != _FLOWS:
        raise BrainContextError("missing Flow Card")
    return cards, {"path": ".ai/flow-cards.yaml", "version": 1,
                   "source_digest": hashlib.sha256(source_bytes).hexdigest(),
                   "pipeline_contract_digest": _digest(raw["context_pipeline"])}


def load_flow_cards(path: str | Path | None = None, *, repo: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load a closed, non-executable repository-owned context contract."""
    if path is not None and repo is not None:
        raise BrainContextError("supply either registry path or repository")
    card_path = (Path(path) if path is not None else
                 Path(repo if repo is not None else Path.cwd()) / ".ai" / "flow-cards.yaml")
    return _load_flow_registry(card_path)[0]


@dataclass(frozen=True)
class FlowResolution:
    selected_flow: str
    selection_basis: str
    pending_canonical_obligation: str | None
    pending_canonical_authority_owner: str | None
    authority_owner: str | None
    card: Mapping[str, Any] | None
    canonical_blocker: Mapping[str, Any] | None
    canonical_next_action: str
    unified_state_next_action: str | None
    invalidation_fingerprint: str
    requires_fresh_context_for_continuation: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "AIOS_FLOW_RESOLUTION", "version": 1,
            "kind": "FLOW_RESOLUTION", "selected_flow": self.selected_flow,
            "selection_basis": self.selection_basis,
            "pending_canonical_obligation": self.pending_canonical_obligation,
            "pending_canonical_authority_owner": self.pending_canonical_authority_owner,
            "authority_owner": self.authority_owner, "card": _stable_copy(self.card),
            "canonical_blocker": _stable_copy(self.canonical_blocker),
            "canonical_next_action": self.canonical_next_action,
            "unified_state_next_action": self.unified_state_next_action,
            "invalidation_fingerprint": self.invalidation_fingerprint,
            "requires_fresh_context_for_continuation": self.requires_fresh_context_for_continuation,
            "run_created": False, "executor_invoked": False,
            "verification_invoked": False, "state_mutated": False,
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def resolve_flow(context: BrainWorkContext, *, cards_path: str | Path | None = None) -> FlowResolution:
    """Classify only semantic reasoning; never reduce or advance lifecycle state."""
    if not isinstance(context, BrainWorkContext):
        raise BrainContextError("expected Brain Work Context")
    _validate_context(context)
    observed = context.canonical_observation
    root = observed["repository"]["root"]
    if not isinstance(root, str) or not root or root != context.operational_repository_root:
        raise BrainContextError("Brain Sync repository root is missing or altered")
    canonical_path = Path(root) / ".ai" / "flow-cards.yaml"
    if cards_path is not None and Path(cards_path).resolve() != canonical_path.resolve():
        raise BrainContextError("Flow Card override is unrelated to observed repository")
    cards, registry_binding = _load_flow_registry(canonical_path)
    if registry_binding != context.compilation["bindings"]["flow_registry"]:
        raise BrainContextError("Flow Card source binding moved; fresh compilation required")
    unified = observed["unified_state"]
    next_action = observed["next_action"]
    # Brain Sync can correctly override its outer action to NONE for a roadmap
    # conflict while retaining Unified State's exact projection as evidence.
    unified_action = unified["next_action"] if unified is not None else None
    pending = _OBLIGATIONS.get(unified_action)
    explicit = context.current_request.get("flow_selector") if context.current_request else None
    selected, basis, _ = _select_semantic_flow(observed, context.current_request, context.repair_supersession_material)
    if (selected != context.compilation["projection_inputs"]["selected_flow"]
            or cards.get(selected) != context.compilation["flow_card"]):
        raise BrainContextError("compiled Flow Card relevance binding changed")
    return FlowResolution(
        selected, basis, pending, _OWNERS.get(pending), _OWNERS.get(selected), cards.get(selected),
        observed["blocker"], next_action, unified_action, context.invalidation_fingerprint,
        explicit is not None,
    )


def _validate_context(context: BrainWorkContext) -> None:
    if not isinstance(context, BrainWorkContext) or not context.compilation:
        raise BrainContextError("expected compiled Brain Work Context")
    try:
        basis = _compiled_basis(context.canonical_observation, context.current_request, context.compilation)
        if (basis != context.invalidation_basis or _digest(basis) != context.invalidation_fingerprint
                or _digest([context.canonical_observation, context.current_request, context.compilation])
                != context.compilation_integrity):
            raise BrainContextError("stale or altered Brain Work Context")
        root = context.canonical_observation["repository"]["root"]
        if not isinstance(root, str) or not root or root != context.operational_repository_root:
            raise BrainContextError("Brain Sync repository root is missing or altered")
        _, binding = _load_flow_registry(Path(root) / ".ai" / "flow-cards.yaml")
        if binding != context.compilation["bindings"]["flow_registry"]:
            raise BrainContextError("Flow Card source binding moved; fresh compilation required")
    except (KeyError, TypeError) as exc:
        raise BrainContextError("malformed compiled context binding") from exc


@dataclass(frozen=True)
class ExactContextExpansion:
    """Request-scoped canonical values, rebound to one compiled context; no authority."""

    context_fingerprint: str
    values: Mapping[str, Any]
    source_digests: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {"format": "AIOS_EXACT_CONTEXT_EXPANSION", "version": 1,
                "context_fingerprint": self.context_fingerprint, "values": _stable_copy(self.values),
                "source_digests": _stable_copy(self.source_digests), "authority": "NONE"}


def _dependencies(dependencies: Any) -> list[str]:
    if (not isinstance(dependencies, (list, tuple)) or not dependencies or len(dependencies) > 16
            or any(not isinstance(path, str) or not path or len(path) > 256 for path in dependencies)
            or len(set(dependencies)) != len(dependencies)):
        raise BrainContextError("decision dependencies must be explicit bounded structural selectors")
    return list(dependencies)


def _exact_value(context: BrainWorkContext, selector: str) -> Any:
    record = context.compilation["material_index"].get(selector)
    if selector == "canonical_observation.supporting_context" and any(
            value["representation"] != "EXACT" for value in context.compilation["material_index"].values()):
        raise ExactExpansionRequired("supporting context contains non-exact decision dependencies")
    if selector == "canonical_observation.roadmap.elision_manifest" or selector.startswith(
            "canonical_observation.roadmap.elision_manifest."):
        raise ExactExpansionRequired("elision provenance cannot establish canonical semantic content")
    if record is not None and record["representation"] != "EXACT":
        raise ExactExpansionRequired(f"exact expansion required for {selector}")
    if not any(selector == path or selector.startswith(path + ".") for path in _EXACT_DEPENDENCY_ROOTS):
        raise ExactExpansionRequired("dependency is outside the compiled exact projection")
    value: Any = {"canonical_observation": context.canonical_observation, "current_request": context.current_request}
    for part in selector.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise ExactExpansionRequired(f"exact canonical source required for {selector}")
        value = value[part]
    return _stable_copy(value)


def _fresh_context(context: BrainWorkContext, snapshot: BrainSyncSnapshot | None) -> tuple[BrainWorkContext, BrainSyncSnapshot]:
    _validate_context(context)
    source = snapshot if snapshot is not None else observe_brain_sync(repo=context.operational_repository_root)
    fresh = compose_brain_work_context(source, context.current_request,
                                       budget_bytes=context.compilation["budget"]["limit_bytes"])
    if fresh.invalidation_fingerprint != context.invalidation_fingerprint:
        raise BrainContextError("exact expansion source/projection bindings moved; recompile before reasoning")
    return fresh, source


def expand_exact_context(context: BrainWorkContext, dependencies: list[str] | tuple[str, ...], *,
                         fresh_snapshot: BrainSyncSnapshot | None = None,
                         max_bytes: int = DEFAULT_CONTEXT_BUDGET) -> ExactContextExpansion:
    """Expand only declared dependencies from fresh, digest-rebound canonical sources.

    Historical bodies are read only here, with an explicit selector and hard byte
    bound. Movement, an unknown selector, or an oversized exact dependency blocks.
    """
    selectors = _dependencies(dependencies)
    if type(max_bytes) is not int or not 512 <= max_bytes <= MAX_CONTEXT_BUDGET:
        raise BrainContextError("invalid exact expansion bound")
    fresh, source = _fresh_context(context, fresh_snapshot)
    values = {}
    roadmap_source = None
    for selector in selectors:
        try:
            values[selector] = _exact_value(fresh, selector)
            continue
        except ExactExpansionRequired:
            pass
        record = fresh.compilation["material_index"].get(selector)
        if selector == "canonical_observation.supporting_context":
            value = source.supporting_context
            for path, entry in fresh.compilation["material_index"].items():
                name = path.rsplit(".", 1)[1]
                if not isinstance(value, Mapping) or _digest(value.get(name)) != entry["source_digest"]:
                    raise BrainContextError("supporting source digest moved")
            values[selector] = _stable_copy(value)
            continue
        if record is not None and selector == "canonical_observation.supporting_context.task_problem":
            value = (source.supporting_context or {}).get("task_problem")
            if not isinstance(value, str) or _digest(value) != record["source_digest"]:
                raise BrainContextError("supporting source digest moved")
            values[selector] = value
            continue
        match = re.fullmatch(r"canonical_observation\.roadmap\.sequence\[id=([A-Za-z0-9_.-]+)\]", selector)
        history_selectors = {
            "canonical_observation.roadmap.planning_controls.research_assurance.milestones": ("research_assurance", "milestones"),
            "canonical_observation.roadmap.planning_controls.human_priority_side_track.historical_completed": ("human_priority_side_track", "historical_completed"),
        }
        if not match and selector not in history_selectors:
            raise ExactExpansionRequired("dependency needs a supported bounded canonical selector")
        binding = (source.source_bindings or {}).get("roadmap")
        if binding is None:
            raise BrainContextError("historical expansion requires an exact canonical source binding")
        if roadmap_source is None:
            text = _read_main_source(Path(context.operational_repository_root), source.main_sha, binding["path"])
            if text is None or hashlib.sha256(text.encode("utf-8")).hexdigest() != binding["source_digest"]:
                raise BrainContextError("canonical roadmap expansion source digest moved")
            roadmap_source = yaml.load(text, Loader=_RoadmapLoader)
        if match:
            matches = [row for row in roadmap_source["sequence"] if row["id"] == match.group(1)]
            if len(matches) != 1:
                raise BrainContextError("missing or ambiguous exact expansion subject")
            values[selector] = _stable_copy(matches[0])
        else:
            control_key, field = history_selectors[selector]
            try:
                value = roadmap_source[control_key][field]
            except (KeyError, TypeError) as exc:
                raise BrainContextError("missing exact completed history source") from exc
            values[selector] = _stable_copy(value)
    if _byte_size(values) > max_bytes:
        raise ContextBudgetError("exact expansion exceeds its bounded decision envelope")
    return ExactContextExpansion(context.invalidation_fingerprint, _stable_copy(values),
                                 {path: _digest(value) for path, value in values.items()})


def require_exact_context(context: BrainWorkContext, dependencies: list[str] | tuple[str, ...], *,
                          expansion: ExactContextExpansion | None = None,
                          fresh_snapshot: BrainSyncSnapshot | None = None,
                          max_bytes: int = DEFAULT_CONTEXT_BUDGET) -> Mapping[str, Any]:
    """Fail-closed final decision boundary. Derived representations are not answers.

    Consumers declare every source dependency before semantic finalization. An
    expansion is checked against fresh canonical sources again, never trusted as
    a digest, manifest, summary, cached fragment or an authority-bearing receipt.
    """
    selectors = _dependencies(dependencies)
    _validate_context(context)
    if expansion is None:
        # Reject a compressed dependency before reading anything additional.
        for path in selectors:
            _exact_value(context, path)
        fresh, _ = _fresh_context(context, fresh_snapshot)
        values = {path: _exact_value(fresh, path) for path in selectors}
        if type(max_bytes) is not int or not 512 <= max_bytes <= MAX_CONTEXT_BUDGET or _byte_size(values) > max_bytes:
            raise ContextBudgetError("exact decision dependencies exceed their bounded envelope")
        return values
    if not isinstance(expansion, ExactContextExpansion) or expansion.context_fingerprint != context.invalidation_fingerprint:
        raise BrainContextError("stale exact expansion binding")
    exact = expand_exact_context(context, selectors, fresh_snapshot=fresh_snapshot, max_bytes=max_bytes)
    if (dict(expansion.values) != dict(exact.values) or dict(expansion.source_digests) != dict(exact.source_digests)):
        raise BrainContextError("altered exact expansion material")
    return _stable_copy(exact.values)


__all__ = [
    "BrainContextError", "BrainWorkContext", "FlowResolution",
    "compose_brain_work_context", "load_flow_cards", "resolve_flow",
    "ContextBudgetError", "DerivedContextCache", "ExactContextExpansion", "ExactExpansionRequired",
    "expand_exact_context", "require_exact_context",
]
