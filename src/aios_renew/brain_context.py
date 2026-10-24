"""Transient BP-3 context and semantic flow classification.

Brain Sync and Unified State retain all engineering lifecycle authority. This module
only projects their observation and identifies a bounded reasoning contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ast
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

import yaml

from .brain_sync import (
    BrainSyncError, BrainSyncSnapshot, observe_brain_sync, _has_failure_pages,
    _has_unresolved_diagnostic, _unresolved_portable_exhausted, _has_source_diagnostic,
)


class BrainContextError(ValueError):
    """A request, observation, or Flow Card cannot be interpreted safely."""


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
        "current_request_sha256": _digest(request),
    }
    if "repair_supersession" in observed:
        basis["repair_supersession_sha256"] = _digest(observed["repair_supersession"])
    if "correction_subject" in observed:
        # Immutable ref movement invalidates an audit even when facts are equal.
        basis["correction_subject_sha256"] = _digest(observed["correction_subject"])
    if "diagnostics" in observed:
        basis["diagnostics_sha256"] = _digest(observed["diagnostics"])
    if "failure_page" in observed:
        basis["failure_page_sha256"] = _digest(observed["failure_page"])
    if "post_task" in observed:
        basis["post_task_sha256"] = _digest({**observed, "repository": {
            "name": observed["repository"]["name"], "main_sha": observed["main_sha"],
        }})
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
    _post_task_current: Any = field(default=None, repr=False, compare=False)
    _diagnostic_current: Any = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        from .brain_sync import validate_diagnostic_context
        from .review_transport import validate_failure_context, validate_failure_detail_view, ReviewTransportError
        if self._post_task_current is not None:
            code = self._post_task_current()
            if code is not None:
                raise BrainContextError(code)
        if ("post_task" in self.canonical_observation or _has_source_diagnostic(self.canonical_observation)) and (
                _invalidation_basis(self.canonical_observation, self.current_request) != self.invalidation_basis
                or _digest(self.invalidation_basis) != self.invalidation_fingerprint):
            raise BrainContextError("stale or altered Post-TASK Work Context" if "post_task" in self.canonical_observation
                else "stale or altered source-bound Work Context")
        if self._diagnostic_current is not None:
            code = self._diagnostic_current()
            if code is not None:
                raise BrainContextError(code)
        if "failure_page" in self.canonical_observation:
            view = self.canonical_observation["failure_page"]
            try:
                validate_failure_detail_view(view)
            except (ReviewTransportError, ValueError, TypeError, KeyError) as exc:
                raise BrainContextError("FAILURE_PAGE_INTEGRITY_INVALID") from exc
            if view["binding"] is not None and view["binding"]["task"] != self.canonical_observation.get("selected_task"):
                raise BrainContextError("FAILURE_CONTEXT_SUBSTITUTION")
        diagnostics = self.canonical_observation.get("diagnostics")
        if diagnostics is not None:
            validate_diagnostic_context(diagnostics, self.canonical_observation.get("selected_task"))
        unified = self.canonical_observation.get("unified_state")
        if unified and unified.get("failure_context") is not None:
            validate_failure_context(unified["failure_context"])
            if (unified["failure_context"]["run_id"] != unified.get("failed_run_id")
                    or unified["failure_context"]["subject_sha"] != unified.get("failed_head_sha")
                    or unified["failure_context"]["task"] != unified.get("task")):
                raise BrainContextError("FAILURE_CONTEXT_SUBSTITUTION")
        portable = _stable_copy(self.canonical_observation)
        if "post_task" in portable or _has_failure_pages(portable) or _has_unresolved_diagnostic(portable) or _has_source_diagnostic(portable):
            portable["repository"]["root"] = "protected-repository-root"
        if _has_source_diagnostic(portable):
            portable["repository"]["remote"] = None
        value = {
            "format": "AIOS_BRAIN_WORK_CONTEXT", "version": 1,
            "kind": "BRAIN_WORK_CONTEXT",
            "canonical_observation": portable,
            "current_request": _stable_copy(self.current_request),
            "invalidation_basis": _stable_copy(self.invalidation_basis),
            "invalidation_fingerprint": self.invalidation_fingerprint,
            "run_created": False, "executor_invoked": False,
            "verification_invoked": False, "state_mutated": False,
        }
        if _has_unresolved_diagnostic(portable) and (_unresolved_portable_exhausted(portable)
                or len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")) > 8192):
            raise BrainContextError("LIMIT_EXHAUSTED")
        if "post_task" in portable and len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")) > 8192:
            raise BrainContextError("POST_TASK_LIMIT_EXHAUSTED")
        if _has_source_diagnostic(portable) and len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")) > 64 * 1024:
            raise BrainContextError("LIMIT_EXHAUSTED")
        return value

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compose_brain_work_context(
    snapshot: BrainSyncSnapshot | None = None,
    current_request: Mapping[str, Any] | None = None,
    *,
    repo: str | Path | None = None,
    diagnostic_selector: Mapping[str, Any] | None = None,
    post_task_selector: Mapping[str, Any] | None = None,
) -> BrainWorkContext:
    """Compose one request-scoped view from current Brain Sync observation."""
    if post_task_selector is not None and (snapshot is not None or diagnostic_selector is not None):
        raise BrainContextError("Post-TASK requires one fresh selector")
    if snapshot is not None and isinstance(snapshot, BrainSyncSnapshot) and snapshot.post_task is not None:
        raise BrainContextError("Post-TASK requires one fresh selector")
    if diagnostic_selector is not None and (not isinstance(diagnostic_selector, Mapping) or not diagnostic_selector
            or set(diagnostic_selector) - {"task_id", "run_id", "delivery_id", "issue_number", "workflow_run_id", "run_attempt", "failure_page_selector"}):
        raise BrainContextError("invalid diagnostic selector")
    if snapshot is None:
        snapshot = (observe_brain_sync(repo=repo, post_task_selector=post_task_selector)
                    if post_task_selector is not None else observe_brain_sync(repo=repo, **dict(diagnostic_selector or {})))
    elif repo is not None or diagnostic_selector is not None or not isinstance(snapshot, BrainSyncSnapshot):
        raise BrainContextError("supply either a Brain Sync snapshot or repository")
    request = _request(current_request)
    try:
        observed = snapshot.as_dict()
    except BrainSyncError as exc:
        # Keep snapshot rejection fail-closed within this ValueError-based API.
        raise BrainContextError(str(exc)) from exc
    if "post_task" in observed or _has_failure_pages(observed) or _has_unresolved_diagnostic(observed) or _has_source_diagnostic(observed):
        # Keep the operational root private for local Flow Card integrity checks.
        # Both portable serializers redact it before a provider sees this view.
        observed["repository"]["root"] = snapshot.repository["root"]
    # Brain Sync may expose a transport URL for operators. It is not needed by
    # reasoning and can contain credentials, so only canonical identity is copied.
    repository = observed["repository"]
    observed["repository"] = {
        key: repository.get(key) for key in ("root", "name", "main_sha", "remote")
    }
    observed = _stable_copy(observed)
    if observed["format"] != "AIOS_BRAIN_SYNC_SNAPSHOT" or observed["version"] != 1:
        raise BrainContextError("unsupported Brain Sync snapshot")
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
    basis = _invalidation_basis(observed, request)
    fingerprint = _digest(basis)
    return BrainWorkContext(observed, request, basis, fingerprint, observed["repository"]["root"], supersession,
                            snapshot._post_task_current if post_task_selector is not None else None,
                            snapshot._diagnostic_current)


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
            raw["version"] != 1 or not isinstance(raw["cards"], list) or len(raw["cards"]) != 7:
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
    if context._post_task_current is not None:
        code = context._post_task_current()
        if code is not None:
            raise BrainContextError(code)
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
    if explicit == "REPAIR_AUTHORING":
        if (next_action != "EXECUTE_REPAIR" or unified_action != "EXECUTE_REPAIR"
                or observed["blocker"] is not None or not isinstance(unified, Mapping)
                or unified.get("blocker") is not None
                or context.repair_supersession_material is None
                or observed.get("repair_supersession") != _supersession_facts(context.repair_supersession_material)
                or unified.get("failed_run_id") != observed["repair_supersession"]["failed_run_id"]
                or unified.get("failed_head_sha") != observed["repair_supersession"]["failed_head_sha"]
                or unified.get("correction_sha") != observed["repair_supersession"]["current_authorization"]["authorization_sha"]):
            raise BrainContextError("explicit REPAIR_AUTHORING requires exact unexecuted canonical supersession")
        selected, basis = explicit, "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION"
    elif explicit is not None:
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


_R21_APPROVAL = "human_r21_four_specialized_activation_20261017"
_R21_APPROVAL_STATES = frozenset({
    "HUMAN_APPROVED_CANONICALIZED_LIVE_PRODUCTION_CONFORMANCE_OUTSTANDING", "ACTIVE",
})
_R21_SUCCESSOR_APPROVAL = "human_r21_tca_roa_successor_activation"
_R21_TCA3_APPROVAL = "human_r21_tca3_successor_activation"
_TCA3_PRIORITY_RECORD = "human_tca3_retrospective_priority_20261023"
_R5_CANDIDATE_REF = "refs/heads/aios/review/RUN-368-001"
_R5_REVIEW_REF = "refs/heads/aios/review-decision/RUN-368-001"
_R5_ARTIFACT_REF = "refs/heads/aios/artifacts/RUN-368-001"
_THREE_FLOW_APPROVAL = "human_tca3_three_flow_activation_20261023"
_THREE_FLOW_RUN = "RUN-406-003"
_THREE_FLOW_REPAIR = {
    "failed_run_id": "RUN-406-002",
    "failed_head_sha": "cee20fb76df8fb93c05784135c508e1d5f678efa",
    "failure_artifacts_sha": "a20f9c799666556e02dc1e58de4f7698e65e5388",
    "repair_id": "REPAIR-406-002-001", "action": "CODE_FIX",
    "repair_authorization_sha": "2b1ef613755e15a1795ccf4e2fefffd95bfcdf31",
    "root_base_sha": "b3344968cef482615244a77ded439330c50a2dc7",
}
_THREE_FLOW_PREDECESSOR_DIGEST = "c1725256df1fd42e7b435ca3fd88618031db0606014fbbc5e7387a8c6f59a92e"
_THREE_FLOW_REFS = tuple(f"refs/heads/aios/{kind}/{_THREE_FLOW_RUN}"
                         for kind in ("review", "review-decision", "artifacts"))
_ON_DEMAND_APPROVAL = "human_on_demand_audit_policy_20261023"
_ON_DEMAND_PRIORITY = "human_on_demand_audit_priority_20261023"
_ON_DEMAND_RUN = "RUN-408-002"
_ON_DEMAND_REPAIR = {
    "failed_run_id": "RUN-408-001",
    "failed_head_sha": "7e95961949ddc115dc443d54b0bde6ed34bb5c07",
    "failure_artifacts_sha": "37edd5d281d2baa5557a7ac58996bf2dbdf8dd8f",
    "repair_id": "REPAIR-408-001-001", "action": "CODE_FIX",
    "repair_authorization_sha": "b4131dde44802ca005573e2a3acf3903e8c18cc0",
    "root_base_sha": "3f31b1ca87e980d94b1f8e28ef1e4d3b6c578fc6",
}
_ON_DEMAND_REFS = tuple(f"refs/heads/aios/{kind}/{_ON_DEMAND_RUN}"
                        for kind in ("review", "review-decision", "artifacts"))
_TASK405_EXCEPTION = {
    "authority": "EXPLICIT_HUMAN_ONE_TIME_TASK405_SOURCE_RISK_ACCEPTANCE",
    "approved_on": "2026-10-23",
    "disposition": "ACCEPT_EXACT_SOURCE_RISK_WITH_F1_UNRESOLVED_NOT_REVIEW_OR_PUBLISHER_PASS",
    "merge_sha": "f445626011d0664149011c8f57eb16a7014e6cd7",
    "parents": ["ccc369868e51b36fec2dce042fb6de24fae1e2a6", "cce2bca5f1dbbee591c7b47aaa1888d6b1d2fc05"],
    "tree": "315d6914f7cac46ed30418e522e33b74fe33af4d",
    "source_sha": "cce2bca5f1dbbee591c7b47aaa1888d6b1d2fc05",
    "reviewed_task_revision": 1, "integrated_main_task_revision": 2,
    "original_artifacts_sha": "8be451dd918510de220ff310d1adb88021e22061",
    "original_run": "RUN-405-004",
    "original_review_sha": "2b5c5d4c18d273213559ea964aa96c2a925dc337",
    "original_review_verdict": "CHANGES_REQUIRED",
    "delta_review_sha": "1b9b11a175a08be0c29f11a675af35d854502659",
    "delta_review_verdict": "BLOCKED",
    "finding_id": "F1", "finding_basis": "AC10", "finding_action": "EVIDENCE_ONLY",
    "evidence_ids": ["RUN-405-004-V001", "RUN-405-004-V002"],
    "evidence_exit_codes": [0, 0], "false_c2_byte_bound": 98304, "actual_byte_bound": 65536,
    "implementation_blobs": {
        ".ai/brain-audit-profiles.yaml": "b65e01a6a62a37ceb8d116874d5fe7f404df5fe2",
        ".ai/roadmap-state.yaml": "f3c9e1d2c89939b6e879a1cc50499a41d367870e",
        "docs/AIOS-BRAIN-SEMANTIC-AUDIT-PROTOCOL.md": "591faea4d054b6478d6ea3ecad4d4555e8494890",
        "docs/CHATGPT_PROJECT_CONTRACT.md": "5f2150ae0e7ccf9288e9663578e409822b379486",
        "src/aios_renew/brain_audit.py": "b32f055d5acf9eabbc1f2be1846e42eec2896005",
        "src/aios_renew/brain_context.py": "9ab23252a4bd7a077d53e44c448b24de5671727f",
        "src/aios_renew/brain_provider.py": "4931e929db389564cc28f1e71e5c4b8d9d12822e",
        "src/aios_renew/brain_provider_protocol.py": "875d9a702903d1a15d36a25c4597734497d234c6",
        "tests/test_brain_audit.py": "47dbb96037408680292c256b9e2a49c1c262c6df",
        "tests/test_brain_context.py": "462fc5d0044f407a37080dc741142d3ee4e54bc6",
        "tests/test_brain_provider.py": "bdcd4f6390c1a4f9932ac44fe10a86a18e439bec",
        "tests/test_brain_provider_protocol.py": "3fcaac9b915a9330e49b1f18dfca5d26a1fde68e",
    },
}


def _three_flow_approval(predecessor_digest: str) -> dict:
    """Exact Human decision, independent of the not-yet-created review/result.

    The supplied admitted continuation fixes the RUN identity, not its outcome.
    Future source identities come from its immutable repaired RESULT and first
    independent PRIMARY review, with the exact failed lineage authenticated.
    """
    from .brain_audit import _three_flow_profiles
    return {
        "authority": "EXPLICIT_HUMAN_BRAIN_PROSPECTIVE_POLICY_APPROVAL",
        "approved_on": "2026-10-23", "status": "READY_CANDIDATE_REQUIRES_TASK406_PASS_AND_PUBLICATION",
        "human_decision": "ACTIVATE_TCA3_RETIRE_MOA_WITH_UNCHANGED_AOA_ROA1R3",
        "supersedes": _R21_SUCCESSOR_APPROVAL, "superseded_approval_digest": predecessor_digest,
        "supersedes_planning_record": _TCA3_PRIORITY_RECORD,
        "implementation_task": "TASK-406", "implementation_revision": 2,
        "implementation_run": _THREE_FLOW_RUN, "required_review_mode": "PRIMARY", "required_review_verdict": "PASS",
        "implementation_repair": _stable_copy(_THREE_FLOW_REPAIR),
        "activation_boundary": "EXACT_TASK406_RUNTIME_RESULT_INDEPENDENT_REVIEW_PASS_PUBLISHER_SOURCE_ON_MAIN",
        "target_policy_format": "AIOS_BRAIN_AUDIT_POLICY", "target_policy_version": 1,
        "target_policy_state": "THREE_SPECIALIZED", "target_profiles": _three_flow_profiles(),
        "remediation_authoring": "DIRECT_AUTHOR_REMEDIATION_NO_AUTOMATIC_NORMATIVE_DEFAULT_OR_OPTIONAL_AUDIT",
        "moa_retirement": "RETIRED_FROM_NEW_ATTEMPTS_HISTORICAL_EXACT_REPLAY_ONLY",
        "architecture_and_task_v1_option": "HUMAN_EXPLICIT_EXACT_SUBJECT_V1_ONLY_ONE_NORMATIVE_AUDIT",
        "correction_v1_option": "HUMAN_EXPLICIT_READ_ONLY_ROA_SUPPORT_ONLY_NO_REMEDIATION_AUDIT",
        "task405_source_exception": _stable_copy(_TASK405_EXCEPTION),
        "prior_priority_and_pte": "HISTORICAL_PROVENANCE_PTE_PAUSED_RESUMABLE_NO_AUTOMATIC_ADVANCEMENT",
    }


def _on_demand_approval(predecessor_digest: str) -> dict:
    """One prospective Human decision; no execution claim can activate it."""
    from .brain_audit import _direct_authoring_profiles, _specialized_profiles
    return {
        "authority": "EXPLICIT_HUMAN_BRAIN_PROSPECTIVE_POLICY_APPROVAL",
        "approved_on": "2026-10-23", "status": "READY_CANDIDATE_REQUIRES_TASK408_PASS_AND_PUBLICATION",
        "human_decision": "DIRECT_AUTHORING_ON_DEMAND_TCA_ROA_MOA_SOFT_AOA_WITH_EXPLICIT_OPT_OUT",
        "supersedes": _THREE_FLOW_APPROVAL, "superseded_approval_digest": predecessor_digest,
        "implementation_task": "TASK-408", "implementation_revision": 1, "implementation_run": _ON_DEMAND_RUN,
        "implementation_repair": _stable_copy(_ON_DEMAND_REPAIR),
        "required_review_mode": "PRIMARY", "required_review_verdict": "PASS",
        "activation_boundary": "EXACT_TASK408_RUNTIME_RESULT_INDEPENDENT_REVIEW_PASS_PUBLISHER_SOURCE_ON_MAIN",
        "target_policy_format": "AIOS_BRAIN_AUDIT_POLICY", "target_policy_version": 2,
        "target_policy_state": "HUMAN_ON_DEMAND", "target_profiles": _direct_authoring_profiles(),
        "target_on_demand_profiles": _specialized_profiles(tca3=True), "architecture_default": "SOFT_HUMAN_OPT_OUT",
        "human_choice": "TRUSTED_EXACT_SUBJECT_SOURCE_ONE_REQUEST_AUDIT_ONLY_OR_NO_AOA",
        "v1_option": "EXISTING_EXACT_HUMAN_V1_ONLY_AND_READ_ONLY_CORRECTION_SUPPORT_CONSTRAINTS",
        "historical_lineage": "IMMUTABLE_EXACT_REPLAY_NO_IN_FLIGHT_RESELECTION",
        "task407_roa_r4": "PROSPECTIVE_DEFAULT_ACTIVATION_SUSPENDED_EXISTING_TASK_LINEAGE_UNCHANGED",
        "downstream": "NO_AUTOMATIC_PTE_LEAN_VP_R4_OR_ROADMAP_ADVANCEMENT",
    }


def _authenticate_task405_exception(repo: Path, main: str, record: dict, refs: dict,
                                    blob: Callable[[str, str], bytes]) -> dict:
    """Authenticate exactly one Human Git integration; never return PASS authority."""
    from .artifacts import validate_result, validate_evidence, validate_result_package
    from .authoring_ingress import _json_no_dups, _run_from_data, _validate_metadata_commit
    from .brain_audit import parse_profile_registry, profile_ref, _TCA_V3_DIGEST
    from .operator import _git_is_ancestor
    from .return_affinity import require_same_affinity
    from .review import parse_review, validate_review
    from .review_transport import _git_cmd
    from .task import parse_task

    if _digest(record) != _digest(_TASK405_EXCEPTION):
        raise BrainContextError("missing or conflicting exact TASK-405 Human risk acceptance")
    merge, source = record["merge_sha"], record["source_sha"]
    # Ensure objects are available through the same authenticated transport.
    source_task_raw = blob(source, ".ai/tasks/TASK-405.yaml")
    integrated_task_raw = blob(merge, ".ai/tasks/TASK-405.yaml")
    _, identity, _ = _git_cmd(repo, "show", "-s", "--format=%H %T %P", merge)
    if (identity.split() != [merge, record["tree"], *record["parents"]]
            or source != record["parents"][1] or not _git_is_ancestor(repo, merge, main)):
        raise BrainContextError("TASK-405 exception merge/parents/tree/main lineage mismatch")
    for path, expected in record["implementation_blobs"].items():
        original = blob(source, path)
        integrated = blob(merge, path)
        actual = hashlib.sha1(b"blob " + str(len(original)).encode("ascii") + b"\0" + original).hexdigest()
        if original != integrated or actual != expected:
            raise BrainContextError("TASK-405 exception implementation blob mismatch")
    reviewed_task = parse_task(source_task_raw.decode("utf-8"))
    integrated_task = parse_task(integrated_task_raw.decode("utf-8"))
    if (reviewed_task.task_id != "TASK-405" or reviewed_task.revision != 1
            or integrated_task.task_id != "TASK-405" or integrated_task.revision != 2):
        raise BrainContextError("TASK-405 reviewed r1 and integrated r2 identities conflict")
    expected_refs = {
        "refs/heads/aios/review/RUN-405-004": source,
        "refs/heads/aios/artifacts/RUN-405-004": record["original_artifacts_sha"],
        "refs/heads/aios/review-decision/RUN-405-004": record["original_review_sha"],
        "refs/heads/aios/review-decision/RUN-405-005": record["delta_review_sha"],
    }
    if any(refs.get(key) != value for key, value in expected_refs.items()):
        raise BrainContextError("TASK-405 immutable original refs conflict")
    artifact = record["original_artifacts_sha"]
    run_data = _json_no_dups(blob(artifact, ".ai/transport/run.json"), "TASK-405 original RUN")
    run = _run_from_data(run_data, "TASK-405 original RUN")
    package = _json_no_dups(blob(artifact, ".ai/transport/result.json"), "TASK-405 original ResultPackage")
    if set(package) != {"result", "evidence"} or "kind" in run_data:
        raise BrainContextError("TASK-405 exception requires original PRIMARY ResultPackage")
    result = validate_result(package["result"])
    evidence = tuple(validate_evidence(item) for item in package["evidence"])
    validate_result_package(task=reviewed_task, run=run, result=result, evidence=evidence)
    require_same_affinity(reviewed_task, run)
    if (run.run_id != "RUN-405-004" or run.status != "ACTIVE" or result.head_sha != source
            or result.unresolved or list(result.changed_files) != sorted(record["implementation_blobs"])
            or not _git_is_ancestor(repo, run.base_sha, source)
            or [e.evidence_id for e in evidence] != record["evidence_ids"]
            or [e.result.exit_code for e in evidence] != [0, 0]
            or any(e.type != "VERIFICATION" for e in evidence)
            or [e.source.command for e in evidence] != list(reviewed_task.verification.required)):
        raise BrainContextError("TASK-405 original Runtime 2/2 proof conflicts")
    c2 = [claim for claim in result.claims if claim.id == "C2"]
    if len(c2) != 1 or "98304-byte" not in c2[0].claim:
        raise BrainContextError("TASK-405 immutable false C2 claim must remain unchanged")
    parser = ast.parse(blob(source, "src/aios_renew/brain_audit.py").decode("utf-8"))
    parser_function = next(node for node in parser.body
                           if isinstance(node, ast.FunctionDef) and node.name == "parse_profile_registry")
    bound_check = ast.parse("len(encoded) > 65536", mode="eval").body
    if not any(isinstance(node, ast.Compare) and ast.dump(node) == ast.dump(bound_check)
               for node in ast.walk(parser_function)):
        raise BrainContextError("TASK-405 actual source parser cap is not 65536")
    registry = parse_profile_registry(blob(source, ".ai/brain-audit-profiles.yaml"))
    tca3 = [profile_ref(p) for p in registry["profiles"] if p["id"] == "tca-3"]
    if len(registry["profiles"]) != 10 or tca3 != [{"id": "tca-3", "version": 3, "digest": _TCA_V3_DIGEST}]:
        raise BrainContextError("TASK-405 exact ten-profile registry conflicts")
    reviews = []
    for sha, review_id in ((record["original_review_sha"], "REVIEW-405-001"),
                           (record["delta_review_sha"], "REVIEW-405-002")):
        path = f".ai/reviews/{review_id}.yaml"
        raw = blob(sha, path)
        review = parse_review(raw.decode("utf-8"))
        _validate_metadata_commit(repo, sha, expected_parent_sha=source, metadata_path=path,
                                  metadata_bytes=raw, operation="TASK-405 immutable non-PASS REVIEW")
        validate_review(task=reviewed_task, result=result, review=review,
                        prior_review=reviews[0] if reviews else None)
        if review.review_id != review_id or review.reviewed_sha != source:
            raise BrainContextError("TASK-405 REVIEW identity conflicts")
        reviews.append(review)
    original, delta = reviews
    if (original.mode != "PRIMARY" or original.verdict != "CHANGES_REQUIRED"
            or dict(original.acceptance) != {f"AC{i}": "FAIL" if i == 10 else "PASS" for i in range(1, 11)}
            or len(original.findings) != 1
            or (original.findings[0].id, original.findings[0].basis, original.findings[0].action) != ("F1", "AC10", "EVIDENCE_ONLY")
            or delta.mode != "DELTA" or delta.verdict != "BLOCKED" or delta.prior_finding_id != "F1"
            or dict(delta.acceptance) != {"AC10": "FAIL"} or delta.findings):
        raise BrainContextError("TASK-405 non-PASS decisions/F1 disposition must remain immutable")
    return {"kind": "HUMAN_GIT_SOURCE_EXCEPTION_NOT_PUBLISHER_PASS", "merge_sha": merge,
            "source_sha": source, "review_verdicts": [original.verdict, delta.verdict],
            "false_c2_byte_bound": 98304, "actual_byte_bound": 65536, "implementation_blob_count": 12}


def _canonical_audit_source(repo: Path) -> tuple[dict, dict, dict]:
    """Read approval/publication from canonical Git, never working-tree policy.

    The configured canonical transport is the trust boundary already used by
    authoring. A failed remote read cannot fall back to local main or literals.
    """
    from .authoring_ingress import (
        _query_matching_refs, _read_commit_blob, _fetch_if_remote, _ls_tree,
        _json_no_dups, _run_from_data, _validate_metadata_commit,
    )
    from .artifacts import validate_result, validate_evidence, validate_result_package
    from .brain_audit import parse_profile_registry, profile_ref, _specialized_profiles
    from .brain_return_contract import parse_return_contract_registry
    from .operator import _git_is_ancestor
    from .return_affinity import require_same_affinity
    from .review import parse_review, validate_review
    from .publication import PublicationError, _repair_review_lineage
    from .review_transport import (
        ReviewTransportError, resolve_transport_remote, resolve_detached_observation_remote,
        _git_cmd,
    )
    from .run import ACTIVE
    from .task import parse_task

    def transport() -> tuple[str, str]:
        try:
            name = resolve_transport_remote(repo)
        except ReviewTransportError:
            name = resolve_detached_observation_remote(repo)
        code, url, _ = _git_cmd(repo, "remote", "get-url", name, allow_fail=True)
        if name == "." or code or not url:
            raise BrainContextError("independent canonical audit transport is unavailable")
        return name, url

    remote, remote_url = transport()
    patterns = ("refs/heads/main", _R5_CANDIDATE_REF, _R5_REVIEW_REF, _R5_ARTIFACT_REF)
    refs = _query_matching_refs(repo, remote, *patterns)
    if set(refs) != set(patterns):
        raise BrainContextError("canonical audit main or R5 REVIEW is unavailable")
    main = refs["refs/heads/main"]

    def blob(sha: str, path: str) -> bytes:
        raw = _read_commit_blob(repo, sha, path)
        if raw is None:
            _fetch_if_remote(repo, remote, sha)
            raw = _read_commit_blob(repo, sha, path)
        if raw is None:
            raise BrainContextError("canonical audit source is unavailable")
        return raw

    # Decode only bounded selected approvals; unrelated historical roadmap keys
    # may be numeric, but top-level duplicate/mirrored authority is forbidden.
    raw = blob(main, ".ai/roadmap-state.yaml")
    if len(raw) > 1048576:
        raise BrainContextError("canonical audit roadmap exceeds bound")
    roadmap = yaml.compose(raw.decode("utf-8"), Loader=yaml.SafeLoader)
    if not isinstance(roadmap, yaml.MappingNode):
        raise BrainContextError("canonical audit roadmap must be a mapping")
    nodes = {}
    for key, value in roadmap.value:
        if not isinstance(key, yaml.ScalarNode) or key.tag != "tag:yaml.org,2002:str" or key.value in nodes:
            raise BrainContextError("ambiguous canonical audit roadmap")
        nodes[key.value] = value
    if _R21_APPROVAL not in nodes:
        raise BrainContextError("canonical Human R2.1 approval is missing")
    for key in nodes:
        if ((key.startswith("human_r21_four_specialized_activation_") and key != _R21_APPROVAL)
                or (key.startswith(_R21_SUCCESSOR_APPROVAL) and key != _R21_SUCCESSOR_APPROVAL)
                or (key.startswith(_R21_TCA3_APPROVAL) and key != _R21_TCA3_APPROVAL)
                or (key.startswith("human_tca3_three_flow_activation") and key != _THREE_FLOW_APPROVAL)):
            raise BrainContextError("conflicting canonical Human R2.1 approvals")
        node = nodes[key]
        if key != _THREE_FLOW_APPROVAL and isinstance(node, yaml.MappingNode):
            labels = {k.value: v.value for k, v in node.value
                      if isinstance(k, yaml.ScalarNode) and isinstance(v, yaml.ScalarNode)}
            if (labels.get("target_policy_state") == "THREE_SPECIALIZED"
                    or labels.get("human_decision") == "ACTIVATE_TCA3_RETIRE_MOA_WITH_UNCHANGED_AOA_ROA1R3"):
                raise BrainContextError("duplicate or conflicting three-flow Human approval")
        if key != _ON_DEMAND_APPROVAL and isinstance(node, yaml.MappingNode):
            labels = {k.value: v.value for k, v in node.value
                      if isinstance(k, yaml.ScalarNode) and isinstance(v, yaml.ScalarNode)}
            if (key.startswith(_ON_DEMAND_APPROVAL) or labels.get("target_policy_state") == "HUMAN_ON_DEMAND"):
                raise BrainContextError("duplicate or conflicting on-demand Human approval")
    if _TCA3_PRIORITY_RECORD in nodes:
        priority_encoded = yaml.serialize(nodes[_TCA3_PRIORITY_RECORD])
        if len(priority_encoded.encode("utf-8")) > 8192:
            raise BrainContextError("canonical TCA-3 priority record exceeds bound")
        priority_record = yaml.load(priority_encoded, Loader=_UniqueKeyLoader)
        if (not isinstance(priority_record, dict)
                or priority_record.get("planning_only") is not True
                or not isinstance(priority_record.get("prospective_successor_profile"), dict)
                or priority_record["prospective_successor_profile"].get("status") != "CANDIDATE_PREPARED_NOT_ACTIVE"
                or not isinstance(priority_record.get("current_live_default_profile"), dict)
                or priority_record["current_live_default_profile"].get("id") != "tca-2"):
            raise BrainContextError("planning priority record cannot self-activate TCA-3 or retire TCA-2")

    def approval_record(key: str, *, successor: bool = False, tca3: bool = False) -> dict:
        encoded = yaml.serialize(nodes[key])
        if len(encoded.encode("utf-8")) > 8192:
            raise BrainContextError("canonical audit approval exceeds bound")
        record = yaml.load(encoded, Loader=_UniqueKeyLoader)
        if tca3:
            decision = "ACTIVATE_R21_TCA3_WITH_UNCHANGED_AOA_MOA_ROA1R3_AND_HUMAN_V1_OPTION"
            task_id = "TASK-405"
            target_profiles = _specialized_profiles(tca3=True)
        elif successor:
            decision = "ACTIVATE_R21_TCA2_ROA1R3_WITH_UNCHANGED_AOA_MOA_AND_HUMAN_V1_OPTION"
            task_id = "TASK-392"
            target_profiles = _specialized_profiles(successors=True)
        else:
            decision = "ACTIVATE_R21_FOUR_SPECIALIZED_DEFAULT_WITH_ADAPTIVE_V1_AS_HUMAN_OPTION"
            task_id = "TASK-368"
            target_profiles = _specialized_profiles()
        required = {
            "authority": "EXPLICIT_HUMAN_BRAIN_PROSPECTIVE_POLICY_APPROVAL",
            "human_decision": decision,
            "implementation_task": task_id, "implementation_revision": 1,
            "review_verdict": "PASS", "target_policy_format": "AIOS_BRAIN_AUDIT_POLICY",
            "target_policy_version": 1, "target_policy_state": "FOUR_SPECIALIZED",
            "target_profiles": target_profiles,
            "architecture_and_task_v1_option": "HUMAN_EXPLICIT_EXACT_SUBJECT_V1_ONLY_ONE_NORMATIVE_AUDIT",
            "correction_v1_option": "HUMAN_EXPLICIT_READ_ONLY_SUPPORT_NO_SECOND_NORMATIVE_AUDIT",
        }
        if not successor and not tca3:
            required.update(approved_on="2026-10-17", implementation_run="RUN-368-001", review_id="REVIEW-368-001")
        if (not isinstance(record, dict)
                or any(_digest(record.get(k)) != _digest(v) for k, v in required.items())
                or type(record.get("implementation_revision")) is not int
                or type(record.get("target_policy_version")) is not int
                or record.get("status") not in _R21_APPROVAL_STATES):
            raise BrainContextError("canonical Human R2.1 approval conflicts with activation")
        task_num = "405" if tca3 else "392"
        if (successor or tca3) and (
                not isinstance(record.get("approved_on"), str)
                or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", record["approved_on"]) is None
                or re.fullmatch(rf"RUN-{task_num}-[0-9]{{3,}}", str(record.get("implementation_run"))) is None
                or re.fullmatch(rf"REVIEW-{task_num}-[0-9]{{3,}}", str(record.get("review_id"))) is None):
            raise BrainContextError("invalid separately approved successor identity")
        return record

    original = approval_record(_R21_APPROVAL, successor=False)
    selected = original
    successor = None
    tca3_approval = None
    if _R21_SUCCESSOR_APPROVAL in nodes:
        successor = approval_record(_R21_SUCCESSOR_APPROVAL, successor=True)
        if (successor.get("supersedes") != _R21_APPROVAL
                or successor.get("superseded_approval_digest") != _digest(original)):
            raise BrainContextError("successor does not preserve the exact historical approval")
        run_id = successor["implementation_run"]
        successor_patterns = tuple(f"refs/heads/aios/{kind}/{run_id}"
                                   for kind in ("review", "review-decision", "artifacts"))
        additional = _query_matching_refs(repo, remote, *successor_patterns)
        if set(additional) != set(successor_patterns):
            raise BrainContextError("successor reviewed publication is unavailable")
        refs.update(additional)
        patterns = (*patterns, *successor_patterns)
        selected = successor
    if _R21_TCA3_APPROVAL in nodes:
        if successor is None:
            raise BrainContextError("TCA-3 activation requires prior R2.1 successor approval")
        tca3_approval = approval_record(_R21_TCA3_APPROVAL, successor=True, tca3=True)
        if (tca3_approval.get("supersedes") != _R21_SUCCESSOR_APPROVAL
                or tca3_approval.get("superseded_approval_digest") != _digest(successor)):
            raise BrainContextError("TCA-3 activation does not preserve the exact R2.1 successor approval")
        tca3_run_id = tca3_approval["implementation_run"]
        tca3_patterns = tuple(f"refs/heads/aios/{kind}/{tca3_run_id}"
                              for kind in ("review", "review-decision", "artifacts"))
        tca3_additional = _query_matching_refs(repo, remote, *tca3_patterns)
        if set(tca3_additional) != set(tca3_patterns):
            raise BrainContextError("TCA-3 reviewed publication is unavailable")
        refs.update(tca3_additional)
        patterns = (*patterns, *tca3_patterns)
        selected = tca3_approval

    # Query even when the approval is absent: deleting it after publication must
    # fail closed rather than selecting the predecessor. Historical Git sources
    # without TASK-406 code/refs retain their authenticated pre-cutover mapping.
    current_refs = _query_matching_refs(repo, remote, *_THREE_FLOW_REFS)
    current_code = _read_commit_blob(repo, main, "src/aios_renew/brain_context.py")
    cutover = (_THREE_FLOW_APPROVAL in nodes or bool(current_refs)
               or current_code is not None and _THREE_FLOW_APPROVAL.encode("ascii") in current_code)
    three_flow = None
    exception = None
    if cutover:
        if (_THREE_FLOW_APPROVAL not in nodes or successor is None or tca3_approval is not None
                or set(current_refs) != set(_THREE_FLOW_REFS)):
            raise BrainContextError("TASK-406 approval/review/publication is missing or conflicting; no fallback")
        encoded = yaml.serialize(nodes[_THREE_FLOW_APPROVAL])
        if len(encoded.encode("utf-8")) > 16384:
            raise BrainContextError("TASK-406 approval exceeds bound")
        three_flow = yaml.load(encoded, Loader=_UniqueKeyLoader)
        if (_digest(successor) != _THREE_FLOW_PREDECESSOR_DIGEST
                or _digest(three_flow) != _digest(_three_flow_approval(_digest(successor)))):
            raise BrainContextError("current Human three-flow approval conflicts with exact decision")
        refs.update(current_refs)
        patterns = (*patterns, *_THREE_FLOW_REFS)
        repair = three_flow["implementation_repair"]
        repair_refs = {
            f"refs/heads/aios/failure/{repair['failed_run_id']}": repair["failed_head_sha"],
            f"refs/heads/aios/failure-artifacts/{repair['failed_run_id']}": repair["failure_artifacts_sha"],
            f"refs/heads/aios/repair/{repair['failed_run_id']}": repair["repair_authorization_sha"],
        }
        if _query_matching_refs(repo, remote, *repair_refs) != repair_refs:
            raise BrainContextError("TASK-406 admitted repair provenance is missing or conflicting")
        refs.update(repair_refs)
        patterns = (*patterns, *repair_refs)
        exception_patterns = (
            "refs/heads/aios/review/RUN-405-004", "refs/heads/aios/artifacts/RUN-405-004",
            "refs/heads/aios/review-decision/RUN-405-004", "refs/heads/aios/review-decision/RUN-405-005",
        )
        exception_refs = _query_matching_refs(repo, remote, *exception_patterns)
        if set(exception_refs) != set(exception_patterns):
            raise BrainContextError("exact TASK-405 exception provenance is unavailable")
        refs.update(exception_refs)
        patterns = (*patterns, *exception_patterns)
        exception = _authenticate_task405_exception(repo, main, three_flow["task405_source_exception"], refs, blob)
        selected = three_flow

    on_demand_refs = _query_matching_refs(repo, remote, *_ON_DEMAND_REFS)
    on_demand = None
    if (_ON_DEMAND_APPROVAL in nodes or _ON_DEMAND_PRIORITY in nodes or on_demand_refs
            or current_code is not None and _ON_DEMAND_APPROVAL.encode("ascii") in current_code):
        if three_flow is None or _ON_DEMAND_APPROVAL not in nodes or set(on_demand_refs) != set(_ON_DEMAND_REFS):
            raise BrainContextError("TASK-408 exact approval/review/publication is unavailable; no fallback")
        encoded = yaml.serialize(nodes[_ON_DEMAND_APPROVAL])
        if len(encoded.encode("utf-8")) > 8192:
            raise BrainContextError("TASK-408 approval exceeds bound")
        on_demand = yaml.load(encoded, Loader=_UniqueKeyLoader)
        if _digest(on_demand) != _digest(_on_demand_approval(_digest(three_flow))):
            raise BrainContextError("on-demand policy conflicts with the exact Human decision or predecessor")
        refs.update(on_demand_refs)
        patterns = (*patterns, *_ON_DEMAND_REFS)
        selected = on_demand

    def publication(record: dict, candidate_ref: str, review_ref: str, artifact_ref: str) -> str:
        try:
            return validated_publication(record, candidate_ref, review_ref, artifact_ref)
        except BrainContextError:
            raise
        except (ValueError, PublicationError) as exc:
            raise BrainContextError("canonical R5 publication validation failed") from exc

    def validated_publication(record: dict, candidate_ref: str, review_ref: str, artifact_ref: str) -> str:
        # Historical approvals require their original RUN. The current approval
        # requires the exact admitted repair and its independent first review.
        artifact_sha = refs[artifact_ref]
        run_data = _json_no_dups(blob(artifact_sha, ".ai/transport/run.json"), "R5 RUN")
        if "kind" in run_data:
            raise BrainContextError("canonical R5 artifact must be the original PRIMARY RUN")
        run = _run_from_data(run_data, "R5 RUN")
        result_data = _json_no_dups(blob(artifact_sha, ".ai/transport/result.json"), "R5 ResultPackage")
        if set(result_data) != {"result", "evidence"} or not isinstance(result_data["evidence"], list):
            raise BrainContextError("canonical R5 artifact must contain a nested ResultPackage")
        result = validate_result(result_data["result"])
        evidence = tuple(validate_evidence(item) for item in result_data["evidence"])
        source = result.head_sha
        current = three_flow is not None and record is three_flow
        prospective = on_demand is not None and record is on_demand
        if (not isinstance(source, str) or re.fullmatch(r"[0-9a-f]{40}", source) is None
                or refs[candidate_ref] != source
                or not current and not prospective and (record.get("reviewed_published_source_sha") != source
                                    or record.get("approved_against_main_sha") != source
                                    or record.get("review_decision_sha") != refs[review_ref])):
            raise BrainContextError("canonical R5 publication identity is invalid")
        task_id = record["implementation_task"]
        published_task = parse_task(blob(source, f".ai/tasks/{task_id}.yaml").decode("utf-8"))
        if (published_task.task_id != task_id or published_task.revision != record["implementation_revision"]
                or not _git_is_ancestor(repo, source, main)):
            raise BrainContextError("reviewed R5 source is not published on canonical main")
        if (run.run_id != record["implementation_run"] or run.task.id != task_id
                or run.task.revision != published_task.revision or run.status != ACTIVE
                or re.fullmatch(r"[0-9a-f]{40}", run.base_sha) is None
                or not _git_is_ancestor(repo, run.base_sha, source)):
            raise BrainContextError("canonical R5 RUN publication lineage conflicts")
        require_same_affinity(published_task, run)
        validate_result_package(task=published_task, run=run, result=result, evidence=evidence)
        if result.unresolved:
            raise BrainContextError("canonical R5 RESULT contains unresolved items")
        if prospective:
            reviewed = yaml.compose(blob(source, ".ai/roadmap-state.yaml").decode("utf-8"), Loader=yaml.SafeLoader)
            if not isinstance(reviewed, yaml.MappingNode):
                raise BrainContextError("TASK-408 reviewed policy is absent")
            entries = [(k.value, v) for k, v in reviewed.value if isinstance(k, yaml.ScalarNode)]
            approvals = [v for k, v in entries if k == _ON_DEMAND_APPROVAL]
            archived = {k: v for k, v in entries}
            predecessors = {key: old for key, old in (
                (_R21_APPROVAL, original), (_R21_SUCCESSOR_APPROVAL, successor),
                (_THREE_FLOW_APPROVAL, three_flow)) if old is not None}
            if (len(approvals) != 1 or len({k for k, _ in entries}) != len(entries)
                    or _digest(yaml.load(yaml.serialize(approvals[0]), Loader=_UniqueKeyLoader)) != _digest(record)
                    or any(key not in archived or _digest(yaml.load(yaml.serialize(archived[key]), Loader=_UniqueKeyLoader))
                           != _digest(old) for key, old in predecessors.items())
                    or {ac for claim in result.claims for ac in claim.satisfies} != {f"AC{i}" for i in range(1, 13)}):
                raise BrainContextError("TASK-408 exact reviewed policy or implementation claim coverage conflicts")
        if current or prospective:
            lineage_raw = blob(artifact_sha, ".ai/transport/repair.json")
            lineage = _json_no_dups(lineage_raw, f"{task_id} REPAIR execution")
            binding = record["implementation_repair"]
            failure_ref = f"refs/heads/aios/failure-artifacts/{binding['failed_run_id']}"
            if _query_matching_refs(repo, remote, failure_ref) != {failure_ref: binding["failure_artifacts_sha"]}:
                raise BrainContextError(f"{task_id} immutable failure artifacts conflict")
            authorization = lineage.get("repair")
            if (any(lineage.get(k) != binding[k] for k in (
                    "failed_run_id", "failed_head_sha", "repair_authorization_sha", "root_base_sha"))
                    or not isinstance(authorization, dict)
                    or any(authorization.get(k) != binding[k] for k in ("repair_id", "action"))):
                raise BrainContextError(f"{task_id} RESULT is not from the admitted repair lineage")
            root_base, result_base, prior_review, finding = _repair_review_lineage(
                repo, remote=remote, publication_run_id=run.run_id,
                child_run_data=run_data, child_run=run, child_head_sha=source,
                lineage_bytes=lineage_raw, task=published_task)
            if (root_base != binding["root_base_sha"] or result_base != root_base
                    or prior_review is not None or finding is not None):
                raise BrainContextError(f"{task_id} repaired first review must retain its original TASK root")
        if current:
            reviewed_roadmap = yaml.compose(blob(source, ".ai/roadmap-state.yaml").decode("utf-8"), Loader=yaml.SafeLoader)
            if not isinstance(reviewed_roadmap, yaml.MappingNode):
                raise BrainContextError("TASK-406 reviewed approval is missing")
            entries = [(k.value, v) for k, v in reviewed_roadmap.value if isinstance(k, yaml.ScalarNode)]
            approvals = [v for k, v in entries if k == _THREE_FLOW_APPROVAL]
            if (len(approvals) != 1 or len({k for k, _ in entries}) != len(entries)
                    or _digest(yaml.load(yaml.serialize(approvals[0]), Loader=_UniqueKeyLoader)) != _digest(record)
                    or len(result.claims) != 10
                    or {ac for claim in result.claims for ac in claim.satisfies} != {f"AC{i}" for i in range(1, 11)}
                    or not _git_is_ancestor(repo, record["task405_source_exception"]["merge_sha"], run.base_sha)):
                raise BrainContextError("TASK-406 original reviewed source/approval/claims/exception lineage conflicts")
        review_sha = refs[review_ref]
        if not _ls_tree(repo, review_sha, ".ai/reviews"):
            _fetch_if_remote(repo, remote, review_sha)
        paths = [p for p in _ls_tree(repo, review_sha, ".ai/reviews") if p.endswith((".yaml", ".yml"))]
        if len(paths) != 1:
            raise BrainContextError("canonical R5 REVIEW is missing or ambiguous")
        review_raw = blob(review_sha, paths[0])
        review = parse_review(review_raw.decode("utf-8"))
        _validate_metadata_commit(repo, review_sha, expected_parent_sha=source,
                                  metadata_path=paths[0], metadata_bytes=review_raw, operation="R5 REVIEW")
        validate_review(task=published_task, result=result, review=review)
        if ((not current and not prospective and review.review_id != record["review_id"])
                or current and re.fullmatch(r"REVIEW-406-[0-9]{3,}", review.review_id) is None
                or prospective and re.fullmatch(r"REVIEW-408-[0-9]{3,}", review.review_id) is None
                or review.reviewed_sha != source
                or review.mode != "PRIMARY" or review.verdict != "PASS"
                or set(review.acceptance) != {item.id for item in published_task.acceptance}
                or any(value != "PASS" for value in review.acceptance.values())):
            raise BrainContextError("canonical R5 REVIEW does not approve the published source")
        if current or prospective or record in (successor, tca3_approval):
            published_profiles = parse_profile_registry(blob(source, ".ai/brain-audit-profiles.yaml"))
            published_refs = [profile_ref(p) for p in published_profiles["profiles"]]
            required_profiles = [ref for ref in record["target_profiles"].values() if ref is not None]
            required_profiles.extend(record.get("target_on_demand_profiles", {}).values())
            if any(ref not in published_refs for ref in required_profiles):
                raise BrainContextError("successor mapping was not present in its reviewed publication")
        return hashlib.sha256(review_raw).hexdigest()

    original_review_digest = publication(original, _R5_CANDIDATE_REF, _R5_REVIEW_REF, _R5_ARTIFACT_REF)
    review_digest = original_review_digest
    if successor is not None:
        review_digest = publication(successor, *successor_patterns)
    if tca3_approval is not None:
        review_digest = publication(tca3_approval, *tca3_patterns)
    if three_flow is not None:
        review_digest = publication(three_flow, *_THREE_FLOW_REFS)
    if on_demand is not None:
        review_digest = publication(on_demand, *_ON_DEMAND_REFS)
    profiles = parse_profile_registry(blob(main, ".ai/brain-audit-profiles.yaml"))
    returns = parse_return_contract_registry(blob(main, ".ai/brain-return-contracts.yaml"))
    if _query_matching_refs(repo, remote, *patterns) != refs or transport() != (remote, remote_url):
        raise BrainContextError("canonical audit sources moved while reading")
    authority = {"remote": remote, "remote_url": remote_url, "refs": refs, "approval": selected,
                 "historical_approval": original, "historical_review_digest": original_review_digest,
                 "review_digest": review_digest, "registry": profiles, "return_contracts": returns}
    if three_flow is not None:
        authority.update(task405_source_exception=exception,
                         effective_status="TASK406_REVIEWED_PUBLISHED_THREE_FLOW_ACTIVE",
                         reviewed_source_sha=refs[_THREE_FLOW_REFS[0]], review_decision_sha=refs[_THREE_FLOW_REFS[1]])
    if on_demand is not None:
        authority.update(effective_status="TASK408_REVIEWED_PUBLISHED_HUMAN_ON_DEMAND_ACTIVE",
                         reviewed_source_sha=refs[_ON_DEMAND_REFS[0]], review_decision_sha=refs[_ON_DEMAND_REFS[1]])
    return selected, profiles, authority


def canonical_audit_inputs(
    packet: Any, return_contract_package: Mapping[str, Any], external_bindings: Mapping[str, Any],
    audit_profile_package: Mapping[str, Any] | None = None, *, repo: str | Path | None = None,
    moa_source_context: Mapping[str, Any] | None = None,
    task_source_context: Mapping[str, Any] | None = None,
    architecture_source_context: Mapping[str, Any] | None = None,
    human_v1_option: Mapping[str, Any] | None = None,
    human_v1_deep_support: Mapping[str, Any] | None = None,
    human_audit_choice: Mapping[str, Any] | None = None,
    human_choice_authenticator: Callable[[Mapping[str, Any], str], bool] | None = None,
) -> tuple[dict, dict | None, Callable[[Mapping[str, Any], str], bool]]:
    """Supply the existing R5 boundary for one ordinary first-party attempt.

    Canonical policy authority comes from Git; individual Human choices come
    from a separate trusted, caller-owned input channel. Neither is inferred
    from packet text, a provenance label, or model-provided authority digests.
    """
    from .brain_audit import _packet, normalize_audit_policy, profile_ref
    from .brain_provider_protocol import (
        _AUDITED, _audit_selection, _contract_package, _bindings, _normal, _selected_profile_ref,
    )
    from .brain_sync import _resolve_repository_name
    from .brain_return_contract import select_return_contract
    from .operator import resolve_repository

    try:
        body = _packet(packet)
        root = resolve_repository(repo)
        facts = body["canonical_facts"]
        if (body["selected_flow"] not in _AUDITED or not isinstance(facts, dict)
                or not isinstance(facts.get("main_sha"), str)
                or re.fullmatch(r"[0-9a-f]{40}", facts["main_sha"]) is None
                or facts.get("repository") != _resolve_repository_name(root)):
            raise BrainContextError("Brain packet lacks a canonical audit repository/main binding")
        approval, registry, authority = _canonical_audit_source(root)
        if facts["main_sha"] != authority["refs"]["refs/heads/main"]:
            raise BrainContextError("Brain packet is unrelated to current canonical audit source")
        policy_data = {
            "format": approval["target_policy_format"], "version": approval["target_policy_version"],
            "state": approval["target_policy_state"], "main_sha": facts["main_sha"],
            "work_context_fingerprint": body["work_context_fingerprint"],
            "human_authority_digest": _digest(approval),
            "brain_authority_digest": _digest({"review_digest": authority["review_digest"],
                                               "review_decision_sha": authority.get("review_decision_sha", approval.get("review_decision_sha")),
                                               "published_source_sha": authority.get("reviewed_source_sha", approval.get("reviewed_published_source_sha"))}),
            "r5_publication_sha": authority.get("reviewed_source_sha", approval.get("reviewed_published_source_sha")),
            "profiles": approval["target_profiles"],
        }
        if approval["target_policy_version"] == 2:
            policy_data.update(on_demand_profiles=approval["target_on_demand_profiles"],
                               architecture_default=approval["architecture_default"])
        policy = normalize_audit_policy(policy_data)
        if body["selected_flow"] == "REMEDIATION_AUTHORING" and policy["state"] != "HUMAN_ON_DEMAND":
            raise BrainContextError("remediation has no current audit; use direct AUTHOR_REMEDIATION")
        if body["selected_flow"] not in policy["profiles"]:
            raise BrainContextError("remediation has no current audit; use direct AUTHOR_REMEDIATION")
        chosen = _selected_profile_ref(policy, body["selected_flow"], human_audit_choice, human_v1_option)
        # Resolve every pinned body, even when the current subject uses only one.
        packages = {p["id"]: {"profile": p, "audit_profile_ref": profile_ref(p)} for p in registry["profiles"]}
        for reference in [*policy["profiles"].values(), *policy.get("on_demand_profiles", {}).values(), chosen]:
            if reference is None:
                continue
            if packages.get(reference["id"], {}).get("audit_profile_ref") != reference:
                raise BrainContextError("canonical audit mapping lacks exact published content")
        package = None if chosen is None else packages[chosen["id"]]
        if audit_profile_package is not None and _normal(audit_profile_package) != package:
            raise BrainContextError("explicit package conflicts with canonical audit selection")
        human = human_v1_option is not None or human_v1_deep_support is not None or human_audit_choice is not None
        if human and not callable(human_choice_authenticator):
            raise BrainContextError("individual Human audit choice requires independent authentication")
        if not human and human_choice_authenticator is not None:
            raise BrainContextError("Human authenticator has no bound individual choice")
        contract = _contract_package(return_contract_package, body)
        if contract != select_return_contract(authority["return_contracts"], packet):
            raise BrainContextError("Brain return contract differs from canonical selected contract")
        bindings = _bindings(external_bindings, contract)
        sources = _normal({"moa_source_context": moa_source_context, "task_source_context": task_source_context,
                           "architecture_source_context": architecture_source_context})
        expected = _audit_selection(body, contract, bindings, package, policy, sources,
                                    human_v1_option, human_v1_deep_support, human_audit_choice)
        if human_audit_choice is not None and body["selected_flow"] == "TASK_AUTHORING":
            # An on-demand TASK audit names an existing exact canonical TASK,
            # not a model-written draft relabeled as a Human source.
            from .authoring_ingress import _read_commit_blob
            from .task import validate_task
            raw_task = _read_commit_blob(root, facts["main_sha"], f".ai/tasks/{bindings['task_id']}.yaml")
            if raw_task is None or len(raw_task) > 131072:
                raise BrainContextError("on-demand TASK source is unavailable")
            canonical_task = yaml.load(raw_task.decode("utf-8"), Loader=_UniqueKeyLoader)
            validate_task(canonical_task)
            if _digest(canonical_task) != _digest(sources["task_source_context"]["task"]):
                raise BrainContextError("on-demand TASK differs from the exact canonical revision/source")
        expected_identity = _digest(expected)
        source_identity = _digest(authority)
    except Exception as exc:
        raise BrainContextError("canonical first-party audit inputs are unavailable or inconsistent") from exc

    admitted = None

    def authenticate(selection: Mapping[str, Any], phase: str) -> bool:
        nonlocal admitted
        if (phase not in {"ADMIT", "CONTINUE"} or _digest(selection) != expected_identity
                or phase == "CONTINUE" and admitted != expected_identity
                or policy["version"] == 2 and phase == "ADMIT" and admitted is not None):
            return False
        # Re-read canonical refs and content at both ADMIT and CONTINUE. This
        # deliberately stops on movement rather than reselecting an in-flight audit.
        _, _, current = _canonical_audit_source(root)
        if _digest(current) != source_identity:
            return False
        if human and human_choice_authenticator(_stable_copy(selection), phase) is not True:
            return False
        if phase == "ADMIT":
            admitted = expected_identity
        return True

    return _stable_copy(policy), _stable_copy(package), authenticate


CAA_PILOT_RECORD = "human_caa_ten_task_advisory_20261024"
CAA_PILOT_ID = "CAA-10-TASK-20261024-TASK-417-r1"
CAA_PILOT_FLOWS = ("ARCHITECTURE", "TASK_AUTHORING", "REPAIR_AUTHORING", "REMEDIATION_AUTHORING")
_CAA_PILOT_CONTRACT = dict(
    pilot_id=CAA_PILOT_ID, mode="OPTIONAL_NON_DECISIVE", flows=list(CAA_PILOT_FLOWS),
    task_limit=10, excluded_task_ids=["TASK-417"], other_traffic_enabled=False,
    decisive_evaluation_enabled=False, caa05_selected=False,
)


def _caa_pilot_planning(root, git, sha):
    """Read only selected planning nodes from a bounded canonical Git blob."""
    import os
    import subprocess
    import time
    from .caa_experience import ExperienceError, _shape

    path = ".ai/roadmap-state.yaml"
    entry = git(root, "ls-tree", "-l", sha, "--", path)
    match = re.fullmatch(r"100644 blob ([0-9a-f]{40})\s+(\d+)\t" + re.escape(path), entry)
    if match is None or not 0 < int(match[2]) <= 1048576:
        raise ExperienceError("PILOT_PLANNING_SOURCE_UNAVAILABLE")
    # The existing canonical planning file exceeds CAA-01's historical-document
    # cap. Keep its existing 1 MiB planning cap without changing CAA-01 at all.
    # cat-file reads the size-checked immutable blob, not a filter or worktree.
    size = int(match[2])
    git.calls += 1
    remaining = git.deadline - time.monotonic()
    if git.calls > 512 or remaining <= 0 or git.bytes + size > 4 * 1024 * 1024:
        raise ExperienceError("LIMIT_EXHAUSTED")
    result = subprocess.run(["git", "--no-pager", "-C", str(root), "cat-file", "blob", match[1]],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=min(10, remaining),
                            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0"))
    git.bytes += len(result.stdout) + len(result.stderr)
    if (result.returncode or len(result.stdout) != size or git.bytes > 4 * 1024 * 1024
            or time.monotonic() >= git.deadline):
        raise ExperienceError("PILOT_PLANNING_SOURCE_UNAVAILABLE")
    raw = result.stdout.decode("utf-8", errors="strict")
    # Reject aliases before constructing YAML; unrelated historical numeric keys
    # are not loaded as current pilot policy.
    depth = node_count = 0
    for event in yaml.parse(raw, Loader=yaml.SafeLoader):
        node_count += 1
        if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
            depth += 1
        elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
            depth -= 1
        if isinstance(event, yaml.AliasEvent) or depth > 32 or node_count > 131072:
            raise ExperienceError("PILOT_PLANNING_INVALID")
    tree = yaml.compose(raw, Loader=yaml.SafeLoader)
    if not isinstance(tree, yaml.MappingNode):
        raise ExperienceError("PILOT_PLANNING_INVALID")
    nodes = {}
    for key, value in tree.value:
        if (not isinstance(key, yaml.ScalarNode) or key.tag != "tag:yaml.org,2002:str"
                or key.value in nodes):
            raise ExperienceError("PILOT_PLANNING_INVALID")
        nodes[key.value] = value
    for name, node in nodes.items():
        if name != CAA_PILOT_RECORD and isinstance(node, yaml.MappingNode):
            labels = {k.value: v.value for k, v in node.value
                      if isinstance(k, yaml.ScalarNode) and isinstance(v, yaml.ScalarNode)}
            if (name.startswith(CAA_PILOT_RECORD) or labels.get("human_decision") ==
                    "ENABLE_OPTIONAL_CAA_NOW_OBSERVE_TEN_FUTURE_TASKS_BEFORE_EVALUATION_OR_CAA05"):
                raise ExperienceError("PILOT_PLANNING_INVALID")
    selected = {}
    # A later Human planning selection need not withdraw this optional pilot.
    # Read only this permission; the ordinary Brain Sync owns NEXT semantics.
    for key in (CAA_PILOT_RECORD,):
        if key not in nodes:
            raise ExperienceError("PILOT_PLANNING_UNAVAILABLE")
        selected[key] = yaml.load(yaml.serialize(nodes[key]), Loader=_UniqueKeyLoader)
        _shape(selected[key])
    record = selected[CAA_PILOT_RECORD]
    if (type(record) is not dict or record.get("authority") != "EXPLICIT_HUMAN_DECISION_AND_BRAIN_PROSPECTIVE_SELECTION"
            or record.get("implementation_task") != "TASK-417"
            or type(record.get("implementation_revision")) is not int or record["implementation_revision"] != 1
            or record.get("human_decision") != "ENABLE_OPTIONAL_CAA_NOW_OBSERVE_TEN_FUTURE_TASKS_BEFORE_EVALUATION_OR_CAA05"
            or _digest(record.get("pilot")) != _digest(_CAA_PILOT_CONTRACT)
            or type(record.get("enabled")) is not bool):
        raise ExperienceError("PILOT_PLANNING_INVALID")
    return selected, dict(commit_sha=sha, blob_sha=match[1], path=path, ref="refs/heads/main",
                          pointer="/" + CAA_PILOT_RECORD)


def _caa_pilot_members(root, git, sources, reviewed, main):
    """First canonical authoring, not numeric TASK order or terminal arrival order.

    A merge's first main inclusion is the publication boundary. Multiple new
    identities in one commit have no authenticated ordering and fail contained.
    No local draft, RUN/revision, deletion/re-addition, or activation TASK counts.
    """
    from .caa_experience import ExperienceError
    from .task import validate_task

    chain = git(root, "rev-list", "--first-parent", "--max-count=257", main).splitlines()
    boundary = None
    for commit in chain:
        if commit == reviewed:
            boundary = commit
            break
        if not sources.included(reviewed, commit):
            break
        boundary = commit
    else:
        if len(chain) >= 257:
            raise ExperienceError("PILOT_SEQUENCE_LIMIT_EXHAUSTED")
    if boundary is None:
        raise ExperienceError("PILOT_PUBLICATION_BOUNDARY_UNAVAILABLE")
    commits = git(root, "rev-list", "--first-parent", "--reverse", "--max-count=257",
                  boundary + ".." + main).splitlines()
    if len(commits) >= 257:
        raise ExperienceError("PILOT_SEQUENCE_LIMIT_EXHAUSTED")
    pattern = r"\.ai/tasks/(TASK-[A-Za-z0-9_-]{1,90})\.yaml"
    initial = git(root, "ls-tree", "-r", "--name-only", boundary, "--", ".ai/tasks").splitlines()
    seen = {m[1] for path in initial if (m := re.fullmatch(pattern, path))}
    seen.add("TASK-417")
    members = []
    for commit in commits:
        additions = git(root, "diff-tree", "--no-commit-id", "--name-status", "-r", "--diff-filter=A",
                        commit + "^1", commit, "--", ".ai/tasks").splitlines()
        new = []
        for row in additions:
            if row.startswith("A\t") and (match := re.fullmatch(pattern, row[2:])):
                if match[1] not in seen:
                    # A TASK deleted before activation is still an old identity.
                    # Query only this candidate path, with a single result cap.
                    prior = git(root, "rev-list", "--first-parent", "--max-count=1", boundary, "--", row[2:])
                    if prior:
                        seen.add(match[1])
                    else:
                        new.append((match[1], row[2:]))
        if len(new) > 1:
            raise ExperienceError("PILOT_FIRST_AUTHORING_ORDER_AMBIGUOUS")
        for task_id, path in new:
            task = validate_task(sources.document(commit, path))
            if task.task_id != task_id:
                raise ExperienceError("TASK_SOURCE_MISMATCH")
            seen.add(task_id)
            members.append(dict(task_id=task_id, first_revision=task.revision, ordinal=len(members) + 1,
                                first_authoring_source=sources.binding(commit, path, ref="refs/heads/main")))
        if len(members) == 10:
            break
    return boundary, members


def canonical_caa_pilot_context(repo: str | Path, *, expected_main_sha: str) -> dict[str, Any]:
    """Authenticate optional pilot permission and its cohort, without activation claims.

    This is a read-only consumer of CAA-01's terminal/review/publication reader.
    It neither verifies TASK-417 nor makes a new lifecycle decision. No object
    fetch, working-tree policy fallback, caller-supplied plan, or RUN is used.
    """
    from .brain_attention import GitSources
    from .caa_experience import (
        ExperienceError, _GitBudget, _Sources, _transport, _refs, capture_experience_cut,
        reconstruct_episodes,
    )

    root, git = Path(repo).resolve(), _GitBudget()
    source = None
    try:
        if type(expected_main_sha) is not str or re.fullmatch(r"[0-9a-f]{40}", expected_main_sha) is None:
            raise BrainContextError("CAA_PILOT_CANONICAL_MAIN_INVALID")
        transport = _transport(root, git)
        live = GitSources(root, git, transport[0])
        pins = _refs(live, ("refs/heads/main",))
        if pins.get("refs/heads/main") != expected_main_sha:
            raise BrainContextError("CAA_PILOT_CANONICAL_MAIN_CHANGED")
        planning, source = _caa_pilot_planning(root, git, expected_main_sha)
        record = planning[CAA_PILOT_RECORD]
        if not record["enabled"]:
            return dict(status="UNAVAILABLE", reason="HUMAN_DISABLED_PILOT", pilot_id=CAA_PILOT_ID, source=source)
        cut = capture_experience_cut(root, expected_main_sha=expected_main_sha, task_ids=("TASK-417",))
        view = reconstruct_episodes(root, cut=cut)
        published = {}
        for episode in view["episodes"]:
            facts = {fact["name"]: fact["value"] for fact in episode["observed"]}
            if (facts.get("task") == {"id": "TASK-417", "revision": 1}
                    and facts.get("terminal", {}).get("kind") == "RESULT"
                    and facts.get("review", {}).get("verdict") == "PASS"
                    and facts.get("publication", {}).get("state") == "MAIN_INCLUSION"):
                reviewed = facts["publication"]["reviewed_source_sha"]
                published[reviewed] = episode
        if len(published) != 1:
            raise ExperienceError("PILOT_EXACT_PUBLICATION_UNAVAILABLE")
        reviewed, episode = next(iter(published.items()))
        approved, approved_source = _caa_pilot_planning(root, git, reviewed)
        if approved[CAA_PILOT_RECORD] != record:
            raise ExperienceError("PILOT_APPROVAL_SOURCE_CHANGED")
        sources = _Sources(root, git, transport[0], pins)
        boundary, members = _caa_pilot_members(root, git, sources, reviewed, expected_main_sha)
        if _refs(live, ("refs/heads/main",)) != pins or _transport(root, git) != transport:
            raise BrainContextError("CAA_PILOT_CANONICAL_MAIN_CHANGED")
        # Recheck activation pins too; a moved review cannot authorize a pilot.
        if reconstruct_episodes(root, cut=cut) != view:
            raise ExperienceError("PILOT_ACTIVATION_SOURCE_CHANGED")
        return dict(status="AVAILABLE", reason="SOURCE_AUTHENTICATED_OPTIONAL_PERMISSION", pilot_id=CAA_PILOT_ID,
                    repository_id=cut.as_dict()["repository_id"], main_sha=expected_main_sha,
                    source=source, approval_source=approved_source, publication_boundary_sha=boundary,
                    # Project the sealed reader's tuple fields as JSON arrays.
                    activation=dict(reviewed_sha=reviewed, episode=episode, source_cut=_stable_copy(cut.as_dict())),
                    members=members, inclusion_closed=len(members) == 10, authority="NONE")
    except BrainContextError as exc:
        if str(exc) in {"CAA_PILOT_CANONICAL_MAIN_INVALID", "CAA_PILOT_CANONICAL_MAIN_CHANGED"}:
            raise
        return dict(status="UNAVAILABLE", reason="PILOT_PLANNING_INVALID", pilot_id=CAA_PILOT_ID, source=source)
    except ExperienceError as exc:
        code = str(exc)
        allowed = {"PILOT_PLANNING_SOURCE_UNAVAILABLE", "PILOT_PLANNING_INVALID", "PILOT_PLANNING_UNAVAILABLE",
                   "PILOT_SEQUENCE_LIMIT_EXHAUSTED", "PILOT_PUBLICATION_BOUNDARY_UNAVAILABLE",
                   "PILOT_FIRST_AUTHORING_ORDER_AMBIGUOUS", "PILOT_EXACT_PUBLICATION_UNAVAILABLE",
                   "PILOT_APPROVAL_SOURCE_CHANGED", "PILOT_ACTIVATION_SOURCE_CHANGED", "LIMIT_EXHAUSTED",
                   "SOURCE_UNAVAILABLE", "SOURCE_UNAVAILABLE_OR_INVALID", "SOURCE_MOVED", "CUT_INVALID",
                   "MAIN_REWRITTEN", "REPOSITORY_MISMATCH", "MAIN_MOVED", "TERMINAL_CONFLICT",
                   "TERMINAL_INCOMPLETE", "REVIEW_MISMATCH"}
        return dict(status="UNAVAILABLE", reason=code if code in allowed else "PILOT_SOURCE_UNAVAILABLE_OR_INVALID",
                    pilot_id=CAA_PILOT_ID, source=source)
    except Exception:
        # Optional activation/history failure is not an authoring verdict. Never
        # export Git stderr, provider errors, paths or arbitrary source text.
        return dict(status="UNAVAILABLE", reason="PILOT_SOURCE_UNAVAILABLE_OR_INVALID",
                    pilot_id=CAA_PILOT_ID, source=source)


__all__ = [
    "BrainContextError", "BrainWorkContext", "FlowResolution",
    "compose_brain_work_context", "load_flow_cards", "resolve_flow", "canonical_audit_inputs",
    "canonical_caa_pilot_context",
]
