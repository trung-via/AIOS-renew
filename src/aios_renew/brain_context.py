"""Transient BP-3 context and semantic flow classification.

Brain Sync and Unified State retain all engineering lifecycle authority. This module
only projects their observation and identifies a bounded reasoning contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
                or (key.startswith(_R21_TCA3_APPROVAL) and key != _R21_TCA3_APPROVAL)):
            raise BrainContextError("conflicting canonical Human R2.1 approvals")
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

    def publication(record: dict, candidate_ref: str, review_ref: str, artifact_ref: str) -> str:
        # All approvals use the same original PRIMARY RUN/RESULT/REVIEW trust
        # path. Registry presence and selected digests cannot authenticate policy.
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
        if (not isinstance(source, str) or re.fullmatch(r"[0-9a-f]{40}", source) is None
                or record.get("reviewed_published_source_sha") != source
                or record.get("approved_against_main_sha") != source
                or refs[candidate_ref] != source or record.get("review_decision_sha") != refs[review_ref]):
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
        if (review.review_id != record["review_id"] or review.reviewed_sha != source
                or review.mode != "PRIMARY" or review.verdict != "PASS"
                or set(review.acceptance) != {item.id for item in published_task.acceptance}
                or any(value != "PASS" for value in review.acceptance.values())):
            raise BrainContextError("canonical R5 REVIEW does not approve the published source")
        if record in (successor, tca3_approval):
            published_profiles = parse_profile_registry(blob(source, ".ai/brain-audit-profiles.yaml"))
            published_refs = [profile_ref(p) for p in published_profiles["profiles"]]
            if any(ref not in published_refs for ref in record["target_profiles"].values()):
                raise BrainContextError("successor mapping was not present in its reviewed publication")
        return hashlib.sha256(review_raw).hexdigest()

    original_review_digest = publication(original, _R5_CANDIDATE_REF, _R5_REVIEW_REF, _R5_ARTIFACT_REF)
    review_digest = original_review_digest
    if successor is not None:
        review_digest = publication(successor, *successor_patterns)
    if tca3_approval is not None:
        review_digest = publication(tca3_approval, *tca3_patterns)
    profiles = parse_profile_registry(blob(main, ".ai/brain-audit-profiles.yaml"))
    returns = parse_return_contract_registry(blob(main, ".ai/brain-return-contracts.yaml"))
    if _query_matching_refs(repo, remote, *patterns) != refs or transport() != (remote, remote_url):
        raise BrainContextError("canonical audit sources moved while reading")
    authority = {"remote": remote, "remote_url": remote_url, "refs": refs, "approval": selected,
                 "historical_approval": original, "historical_review_digest": original_review_digest,
                 "review_digest": review_digest, "registry": profiles, "return_contracts": returns}
    return selected, profiles, authority


def canonical_audit_inputs(
    packet: Any, return_contract_package: Mapping[str, Any], external_bindings: Mapping[str, Any],
    audit_profile_package: Mapping[str, Any] | None = None, *, repo: str | Path | None = None,
    moa_source_context: Mapping[str, Any] | None = None,
    task_source_context: Mapping[str, Any] | None = None,
    architecture_source_context: Mapping[str, Any] | None = None,
    human_v1_option: Mapping[str, Any] | None = None,
    human_v1_deep_support: Mapping[str, Any] | None = None,
    human_choice_authenticator: Callable[[Mapping[str, Any], str], bool] | None = None,
) -> tuple[dict, dict, Callable[[Mapping[str, Any], str], bool]]:
    """Supply the existing R5 boundary for one ordinary first-party attempt.

    Canonical policy authority comes from Git; individual Human choices come
    from a separate trusted, caller-owned input channel. Neither is inferred
    from packet text, a provenance label, or model-provided authority digests.
    """
    from .brain_audit import _packet, normalize_audit_policy, profile_ref, _ADAPTIVE_ID, _ADAPTIVE_DIGEST
    from .brain_provider_protocol import _AUDITED, _audit_selection, _contract_package, _bindings, _normal
    from .brain_sync import _resolve_repository_name
    from .brain_return_contract import select_return_contract
    from .operator import resolve_repository

    try:
        root = resolve_repository(repo)
        body = _packet(packet)
        facts = body["canonical_facts"]
        if (body["selected_flow"] not in _AUDITED or not isinstance(facts, dict)
                or not isinstance(facts.get("main_sha"), str)
                or re.fullmatch(r"[0-9a-f]{40}", facts["main_sha"]) is None
                or facts.get("repository") != _resolve_repository_name(root)):
            raise BrainContextError("Brain packet lacks a canonical audit repository/main binding")
        approval, registry, authority = _canonical_audit_source(root)
        if facts["main_sha"] != authority["refs"]["refs/heads/main"]:
            raise BrainContextError("Brain packet is unrelated to current canonical audit source")
        policy = normalize_audit_policy({
            "format": approval["target_policy_format"], "version": approval["target_policy_version"],
            "state": approval["target_policy_state"], "main_sha": facts["main_sha"],
            "work_context_fingerprint": body["work_context_fingerprint"],
            "human_authority_digest": _digest(approval),
            "brain_authority_digest": _digest({"review_digest": authority["review_digest"],
                                               "review_decision_sha": approval["review_decision_sha"],
                                               "published_source_sha": approval["reviewed_published_source_sha"]}),
            "r5_publication_sha": approval["reviewed_published_source_sha"], "profiles": approval["target_profiles"],
        })
        chosen = ({"id": _ADAPTIVE_ID, "version": 1, "digest": _ADAPTIVE_DIGEST}
                  if human_v1_option is not None else policy["profiles"][body["selected_flow"]])
        # Resolve every pinned body, even when the current subject uses only one.
        packages = {p["id"]: {"profile": p, "audit_profile_ref": profile_ref(p)} for p in registry["profiles"]}
        for reference in [*policy["profiles"].values(), chosen]:
            if packages.get(reference["id"], {}).get("audit_profile_ref") != reference:
                raise BrainContextError("canonical audit mapping lacks exact published content")
        package = packages[chosen["id"]]
        if audit_profile_package is not None and _normal(audit_profile_package) != package:
            raise BrainContextError("explicit package conflicts with canonical audit selection")
        human = human_v1_option is not None or human_v1_deep_support is not None
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
                                    human_v1_option, human_v1_deep_support)
        expected_identity = _digest(expected)
        source_identity = _digest(authority)
    except Exception as exc:
        raise BrainContextError("canonical first-party audit inputs are unavailable or inconsistent") from exc

    admitted = None

    def authenticate(selection: Mapping[str, Any], phase: str) -> bool:
        nonlocal admitted
        if (phase not in {"ADMIT", "CONTINUE"} or _digest(selection) != expected_identity
                or phase == "CONTINUE" and admitted != expected_identity):
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


__all__ = [
    "BrainContextError", "BrainWorkContext", "FlowResolution",
    "compose_brain_work_context", "load_flow_cards", "resolve_flow", "canonical_audit_inputs",
]
