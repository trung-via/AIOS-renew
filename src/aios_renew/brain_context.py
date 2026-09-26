"""Transient BP-3 context and semantic flow classification.

Brain Sync and Unified State retain all engineering lifecycle authority. This module
only projects their observation and identifies a bounded reasoning contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from .brain_sync import BrainSyncSnapshot, observe_brain_sync


class BrainContextError(ValueError):
    """A request, observation, or Flow Card cannot be interpreted safely."""


_FLOWS = frozenset({
    "ARCHITECTURE", "TASK_AUTHORING", "SEMANTIC_REVIEW",
    "REMEDIATION_AUTHORING", "REPAIR_AUTHORING", "DIAGNOSTIC",
})
_EXPLICIT = frozenset({"ARCHITECTURE", "TASK_AUTHORING", "DIAGNOSTIC"})
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
}
_HANDOFFS = {
    "ARCHITECTURE": "HUMAN_BRAIN_PLANNING",
    "TASK_AUTHORING": "AUTHORING_INGRESS",
    "SEMANTIC_REVIEW": "AUTHORING_INGRESS",
    "REMEDIATION_AUTHORING": "AUTHORING_INGRESS",
    "REPAIR_AUTHORING": "AUTHORING_INGRESS",
    "DIAGNOSTIC": "HUMAN_BRAIN_PLANNING",
}
_RETURNS = {
    "ARCHITECTURE": "BOUNDED_SEMANTIC_PROPOSAL",
    "TASK_AUTHORING": "TASK_AUTHORING_PROPOSAL",
    "SEMANTIC_REVIEW": "REVIEW_CONTRACT_PROPOSAL",
    "REMEDIATION_AUTHORING": "REMEDIATION_CONTRACT_PROPOSAL",
    "REPAIR_AUTHORING": "REPAIR_AUTHORIZATION_PROPOSAL",
    "DIAGNOSTIC": "BOUNDED_DIAGNOSTIC_PROPOSAL",
}
_ENTRIES = {
    "ARCHITECTURE": frozenset({"EXPLICIT_SELECTOR"}),
    "TASK_AUTHORING": frozenset({"EXPLICIT_SELECTOR", "UNIQUE_UNAUTHORED_NEXT"}),
    "SEMANTIC_REVIEW": frozenset({"UNIFIED_STATE_SEMANTIC_REVIEW"}),
    "REMEDIATION_AUTHORING": frozenset({"UNIFIED_STATE_AUTHOR_REMEDIATION"}),
    "REPAIR_AUTHORING": frozenset({"UNIFIED_STATE_AUTHOR_REPAIR"}),
    "DIAGNOSTIC": frozenset({"EXPLICIT_SELECTOR"}),
}
_CONTEXT_PATHS = frozenset({
    "canonical_observation.repository", "canonical_observation.main_sha",
    "canonical_observation.roadmap", "canonical_observation.selection_status",
    "canonical_observation.selected_task", "canonical_observation.unified_state",
    "canonical_observation.blocker", "current_request.flow_selector",
    "current_request.human_input",
})
_REQUIRED_CONTEXT = {
    "ARCHITECTURE": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "current_request.flow_selector"}),
    "TASK_AUTHORING": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.roadmap", "canonical_observation.blocker"}),
    "SEMANTIC_REVIEW": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.selected_task", "canonical_observation.unified_state"}),
    "REMEDIATION_AUTHORING": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.selected_task", "canonical_observation.unified_state"}),
    "REPAIR_AUTHORING": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.selected_task", "canonical_observation.unified_state"}),
    "DIAGNOSTIC": frozenset({"canonical_observation.repository", "canonical_observation.main_sha", "canonical_observation.blocker", "current_request.flow_selector"}),
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
    "handoff_target", "expected_return_shape", "invalidation_rules",
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
    if not isinstance(value, Mapping) or not value or set(value) - {"flow_selector", "human_input"}:
        raise BrainContextError("current_request must be a nonempty strict mapping")
    selector = value.get("flow_selector")
    if "flow_selector" in value and (not isinstance(selector, str) or selector not in _EXPLICIT):
        raise BrainContextError("invalid flow_selector")
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
    return {
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
        "current_request_sha256": _digest(request),
    }


@dataclass(frozen=True)
class BrainWorkContext:
    canonical_observation: Mapping[str, Any]
    current_request: Mapping[str, str] | None
    invalidation_basis: Mapping[str, Any]
    invalidation_fingerprint: str
    operational_repository_root: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "AIOS_BRAIN_WORK_CONTEXT", "version": 1,
            "kind": "BRAIN_WORK_CONTEXT",
            "canonical_observation": _stable_copy(self.canonical_observation),
            "current_request": _stable_copy(self.current_request),
            "invalidation_basis": _stable_copy(self.invalidation_basis),
            "invalidation_fingerprint": self.invalidation_fingerprint,
            "run_created": False, "executor_invoked": False,
            "verification_invoked": False, "state_mutated": False,
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compose_brain_work_context(
    snapshot: BrainSyncSnapshot | None = None,
    current_request: Mapping[str, Any] | None = None,
    *,
    repo: str | Path | None = None,
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
    observed["repository"] = {
        key: repository.get(key) for key in ("root", "name", "main_sha", "remote")
    }
    observed = _stable_copy(observed)
    if observed["format"] != "AIOS_BRAIN_SYNC_SNAPSHOT" or observed["version"] != 1:
        raise BrainContextError("unsupported Brain Sync snapshot")
    basis = _invalidation_basis(observed, request)
    fingerprint = _digest(basis)
    return BrainWorkContext(observed, request, basis, fingerprint, observed["repository"]["root"])


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


def load_flow_cards(path: str | Path | None = None, *, repo: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load a closed, non-executable repository-owned procedural registry."""
    if path is not None and repo is not None:
        raise BrainContextError("supply either registry path or repository")
    card_path = (Path(path) if path is not None else
                 Path(repo if repo is not None else Path.cwd()) / ".ai" / "flow-cards.yaml")
    try:
        raw = yaml.load(card_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise BrainContextError("cannot load Flow Cards") from exc
    if not isinstance(raw, dict) or set(raw) != {"format", "version", "cards"} or \
            raw["format"] != "AIOS_FLOW_CARDS" or type(raw["version"]) is not int or \
            raw["version"] != 1 or not isinstance(raw["cards"], list) or len(raw["cards"]) != 6:
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
    return cards


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
    if _invalidation_basis(context.canonical_observation, context.current_request) != \
            context.invalidation_basis or _digest(context.invalidation_basis) != \
            context.invalidation_fingerprint:
        raise BrainContextError("stale or altered Brain Work Context")
    observed = context.canonical_observation
    root = observed["repository"]["root"]
    if not isinstance(root, str) or not root or root != context.operational_repository_root:
        raise BrainContextError("Brain Sync repository root is missing or altered")
    canonical_path = Path(root) / ".ai" / "flow-cards.yaml"
    if cards_path is not None and Path(cards_path).resolve() != canonical_path.resolve():
        raise BrainContextError("Flow Card override is unrelated to observed repository")
    cards = load_flow_cards(canonical_path)
    unified = observed["unified_state"]
    next_action = observed["next_action"]
    # Brain Sync can correctly override its outer action to NONE for a roadmap
    # conflict while retaining Unified State's exact projection as evidence.
    unified_action = unified["next_action"] if unified is not None else None
    pending = _OBLIGATIONS.get(unified_action)
    explicit = context.current_request.get("flow_selector") if context.current_request else None
    if explicit is not None:
        selected, basis = explicit, "EXPLICIT_SELECTOR"
    elif pending is not None:
        selected, basis = pending, "UNIFIED_STATE"
    elif observed["selection_status"] == "UNAUTHORED_TASK" and \
            observed["blocker"] is not None and \
            observed["blocker"].get("code") == "UNAUTHORED_TASK":
        selected, basis = "TASK_AUTHORING", "UNIQUE_UNAUTHORED_NEXT"
    else:
        selected, basis = "NONE", "NO_SEMANTIC_FLOW"
    return FlowResolution(
        selected, basis, pending, _OWNERS.get(pending), _OWNERS.get(selected), cards.get(selected),
        observed["blocker"], next_action, unified_action, context.invalidation_fingerprint,
        explicit is not None,
    )


__all__ = [
    "BrainContextError", "BrainWorkContext", "FlowResolution",
    "compose_brain_work_context", "load_flow_cards", "resolve_flow",
]
