"""Authoritative Brain Sync observation boundary for AIOS-renew.

Reconstructs repository/main identity, roadmap planning bookmark, selected exact TASK,
and Unified State lifecycle next_action/authority without acquiring mutation authority,
creating RUNs, invoking Executors, running verification, or mutating runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

import aios_renew.operator as op
from aios_renew.operator import (
    OperatorError,
    _git_is_ancestor,
    load_task,
    resolve_repository,
)
from aios_renew.review_transport import (
    RemoteQueryError,
    ReviewTransportError,
    _exact_remote_refs,
    _git_cmd,
    resolve_detached_observation_remote,
    resolve_transport_remote,
)
from aios_renew.unified_state import (
    UnifiedStateObservation,
    observe_unified_state,
)


class BrainSyncError(OperatorError):
    """Raised when Brain Sync observation cannot be completed."""


PLANNING_PROJECTION_VERSION = "BOUNDED_ACTIVE_PLANNING_PROJECTION_V1"
ELISION_RULE_VERSION = "RULE_BASED_CONTEXT_ELISION_V1"
MAX_PLANNING_BYTES = 262144
MAX_ACTIVE_ITEMS = 128


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BrainSyncError("planning source is not exact JSON data") from exc


def _source_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


class _RoadmapLoader(yaml.SafeLoader):
    pass


def _roadmap_mapping(loader: _RoadmapLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        # Canonical history contains integer-indexed diagnostic populations.
        # JSON's structural key spelling is exact and deterministic; collisions
        # with a text spelling still fail instead of choosing a winning value.
        if type(key) is int:
            key = str(key)
        if not isinstance(key, str) or key in result:
            raise BrainSyncError("duplicate or non-text canonical source key")
        result[key] = loader.construct_object(value_node)
    return result


_RoadmapLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _roadmap_mapping)


def _read_main_source(root: Path, main_sha: str, path: str) -> str | None:
    """Read the exact observed main tree, never an author/review/worktree substitute."""
    code, text, _ = _git_cmd(root, "show", f"{main_sha}:{path}", allow_fail=True)
    if code == 0:
        return text
    code, _, _ = _git_cmd(root, "cat-file", "-e", f"{main_sha}^{{commit}}", allow_fail=True)
    if code != 0:
        raise BrainSyncError("canonical main source object is unavailable")
    code, tree, _ = _git_cmd(root, "ls-tree", main_sha, "--", path, allow_fail=True)
    if code != 0 or tree.strip():
        raise BrainSyncError("cannot read exact canonical main source")
    return None


@dataclass(frozen=True)
class BrainSyncSnapshot:
    """Versioned, bounded, and mutation-free Brain Sync observation snapshot."""

    repository: Mapping[str, Any]
    main_sha: str
    roadmap: Mapping[str, Any]
    selection_status: str
    lifecycle_state: str
    next_action: str
    authority: str
    selected_task: Mapping[str, Any] | None = None
    unified_state: Mapping[str, Any] | None = None
    blocker: Mapping[str, Any] | None = None
    run_created: bool = False
    executor_invoked: bool = False
    verification_invoked: bool = False
    state_mutated: bool = False
    format: str = "AIOS_BRAIN_SYNC_SNAPSHOT"
    version: int = 1
    kind: str = "BRAIN_SYNC_SNAPSHOT"
    task_contract: Mapping[str, Any] | None = None
    supporting_context: Mapping[str, Any] | None = None
    source_bindings: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "kind": self.kind,
            "repository": dict(self.repository),
            "main_sha": self.main_sha,
            "roadmap": dict(self.roadmap),
            "selection_status": self.selection_status,
            "selected_task": dict(self.selected_task) if self.selected_task is not None else None,
            "unified_state": dict(self.unified_state) if self.unified_state is not None else None,
            "lifecycle_state": self.lifecycle_state,
            "next_action": self.next_action,
            "authority": self.authority,
            "blocker": dict(self.blocker) if self.blocker is not None else None,
            "task_contract": dict(self.task_contract) if self.task_contract is not None else None,
            "supporting_context": dict(self.supporting_context) if self.supporting_context is not None else None,
            "source_bindings": dict(self.source_bindings) if self.source_bindings is not None else None,
            "run_created": self.run_created,
            "executor_invoked": self.executor_invoked,
            "verification_invoked": self.verification_invoked,
            "state_mutated": self.state_mutated,
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

    def checkpoint(self) -> str:
        """Render the compact human-readable SYNC CHECKPOINT."""
        repo_name = self.repository.get("name") or "AIOS-renew"
        task_id = self.selected_task.get("id") if self.selected_task else "none"
        run_id = "none"
        finding_id = "none"
        failed_run_id = "none"
        if self.unified_state:
            run_id = self.unified_state.get("run_id") or "none"
            finding_id = self.unified_state.get("finding_id") or "none"
            failed_run_id = self.unified_state.get("failed_run_id") or "none"
        state = "BLOCKED" if self.blocker is not None else "READY"
        last_pub = self.roadmap.get("last_published_task") or "none"
        return (
            f"PROJECT: {repo_name}\n"
            f"MAIN: {self.main_sha}\n"
            f"LAST PUBLISHED: {last_pub}\n"
            f"AUTHORED NEXT TASK: {task_id}\n"
            f"ACTIVE RUN: {run_id}\n"
            f"ACTIVE FINDING: {finding_id}\n"
            f"ACTIVE FAILURE: {failed_run_id}\n"
            f"STATE: {state}"
        )


def _resolve_main_sha(repo: Path) -> str:
    """Resolve the canonical main commit SHA using remote refs when available."""
    code, _, _ = _git_cmd(repo, "symbolic-ref", "--quiet", "HEAD", allow_fail=True)
    if code != 0:
        try:
            remote = resolve_detached_observation_remote(repo)
            refs = _exact_remote_refs(repo, remote, "refs/heads/main")
            return refs["refs/heads/main"]
        except (ReviewTransportError, RemoteQueryError, KeyError) as exc:
            raise BrainSyncError("cannot resolve detached canonical remote main") from exc
    try:
        remote = resolve_transport_remote(repo)
    except ReviewTransportError:
        remote = None
    if remote is not None:
        try:
            refs = _exact_remote_refs(repo, remote, "refs/heads/main")
            if "refs/heads/main" in refs:
                return refs["refs/heads/main"]
        except (ReviewTransportError, RemoteQueryError) as exc:
            raise BrainSyncError("cannot freshly observe canonical remote main") from exc
        raise BrainSyncError("canonical remote main is missing")
    code, out, _ = _git_cmd(repo, "rev-parse", "--verify", "--quiet", "refs/heads/main", allow_fail=True)
    if code == 0 and out.strip():
        return out.strip()
    raise BrainSyncError("cannot resolve canonical main SHA")


def _resolve_repository_name(repo: Path) -> str:
    """Resolve canonical repository identity from origin URL or folder name."""
    code, url, _ = _git_cmd(repo, "config", "--get", "remote.origin.url", allow_fail=True)
    if code == 0 and url.strip():
        clean_url = url.strip().rstrip("/")
        match = re.search(r"[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", clean_url)
        if match:
            return match.group(1)
    return repo.name


_NONSELECTED_DETAIL_FIELDS = frozenset({
    # These are bodies of other planning subjects, never the selected subject.
    "objective", "original_objective", "audit", "brain_audit", "task_design_audit",
    "preemptive_two_stage_audit", "preemptive_audit_on", "post_ready_brain_audit",
    "prior_research_assurance", "implementation_next", "live_probe", "live_conformance",
    "existing_capability_basis", "classifications", "cleanup_passes", "mandatory_regressions",
    "required_live_cases", "target_composition", "diagnosed_gap", "task_design_direction",
})
_NESTED_CONTROL_FIELDS = frozenset({
    "human_decision", "human_instruction", "human_intent", "human_priority", "human_override",
    "human_authorization", "human_finalization", "decision", "priority", "risk_acceptance",
    "risk_boundary", "residual_risks", "accepted_residual_gaps", "executor_delegation", "execution_delegation",
    "blocker", "blockers", "current_live_blocker", "next_action", "scope", "acceptance", "candidate_acceptance",
    "failed_run_id", "failed_head_sha", "finding_id", "review_id", "reviewed_sha", "correction_sha",
    "lineage", "cas", "predecessor", "authority",
})


def _nested_control_facts(value: Any, selector: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{selector}.{key}"
            if key in _NESTED_CONTROL_FIELDS:
                facts.append({"selector": path, "value": child})
            else:
                facts.extend(_nested_control_facts(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            facts.extend(_nested_control_facts(child, f"{selector}[{index}]"))
    if len(facts) > 512:
        raise BrainSyncError("nested exact control population exceeds its bound")
    return facts


def _mirror_ids(value: Any, sequence: list[Mapping[str, Any]]) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_ACTIVE_ITEMS:
        raise BrainSyncError("malformed NEXT compatibility view")
    ids: list[str] = []
    for raw in value:
        if isinstance(raw, str) and raw:
            matches = [item for item in sequence if raw in (item["id"], item.get("task_id"))]
        elif isinstance(raw, Mapping) and raw:
            if not (raw.get("id") or raw.get("task_id")):
                raise BrainSyncError("NEXT mirror has no exact subject")
            matches = [item for item in sequence if all(item.get(key) == val for key, val in raw.items())]
        else:
            raise BrainSyncError("malformed NEXT mirror item")
        if len(matches) != 1 or matches[0]["id"] in ids:
            raise BrainSyncError("missing, duplicate or ambiguous NEXT mirror subject")
        ids.append(matches[0]["id"])
    return ids


def project_active_planning(roadmap: Mapping[str, Any]) -> dict[str, Any]:
    """The single structural NEXT proof, with closed, digest-provenanced elision.

    Compatibility mirrors can confirm the sequence but can never independently
    select work. Unknown material is retained exactly or exceeds the hard bound.
    """
    if not isinstance(roadmap, Mapping):
        raise BrainSyncError("roadmap must be a mapping")
    if type(roadmap.get("version", 1)) is not int or roadmap.get("version", 1) != 1:
        raise BrainSyncError("unsupported roadmap source version")
    source_digest = _source_digest(roadmap)
    sequence = roadmap.get("sequence", [])
    if not isinstance(sequence, list):
        raise BrainSyncError("roadmap sequence must be a list")
    seen: set[str] = set()
    for item in sequence:
        if (not isinstance(item, Mapping) or not isinstance(item.get("id"), str)
                or not item["id"] or item["id"] in seen
                or not isinstance(item.get("status"), str) or not item["status"]):
            raise BrainSyncError("malformed or duplicate roadmap sequence subject")
        seen.add(item["id"])
        if item.get("task_id") is not None and (
                not isinstance(item["task_id"], str)
                or re.fullmatch(r"TASK-[0-9]+", item["task_id"]) is None):
            raise BrainSyncError("invalid exact TASK association")
        if item.get("task_revision") is not None and (
                type(item["task_revision"]) is not int or item["task_revision"] < 1
                or not item.get("task_id")):
            raise BrainSyncError("invalid exact TASK revision")
    active = [item for item in sequence if item["status"] != "DONE"]
    if len(active) > MAX_ACTIVE_ITEMS:
        raise BrainSyncError("active planning population exceeds its exact bound")
    seq_next = [item for item in sequence if item["status"] == "NEXT"]
    next_ids = [item["id"] for item in seq_next]
    conflicts: list[str] = []
    mirrors = roadmap.get("next_items")
    if "next_items" in roadmap:
        try:
            if _mirror_ids(mirrors, sequence) != next_ids:
                conflicts.append("NEXT_ITEMS_SEQUENCE_MISMATCH")
        except BrainSyncError:
            conflicts.append("MALFORMED_OR_AMBIGUOUS_NEXT_ITEMS")
    if len(seq_next) > 1:
        conflicts.append("MULTIPLE_EFFECTIVE_NEXT")
    if seq_next and roadmap.get("active_track_status") == "COMPLETE":
        conflicts.append("COMPLETE_TRACK_HAS_NEXT")
    if len(seq_next) == 1:
        candidate = seq_next[0]
        if any(candidate.get(key) for key in ("blocker", "blockers", "blocked_by", "conflict")):
            conflicts.append("BLOCKED_ITEM_SELECTED_AS_EFFECTIVE_NEXT")
    # Explicit priority/return NEXT mirrors are checked, never used as selectors.
    for key in ("human_priority_side_track", "human_priority_sequence", "return_metadata"):
        control = roadmap.get(key)
        if control is None:
            continue
        if not isinstance(control, Mapping):
            raise BrainSyncError("malformed planning compatibility control")
        if control.get("status") in {"DONE", "COMPLETE", "SUPERSEDED"}:
            continue
        if control.get("status") == "NEXT":
            subject = {field: control[field] for field in ("id", "task_id") if field in control}
            try:
                if _mirror_ids([subject], sequence) != next_ids:
                    conflicts.append("STALE_HUMAN_PRIORITY_OR_RETURN_PROJECTION")
            except BrainSyncError:
                conflicts.append("STALE_HUMAN_PRIORITY_OR_RETURN_PROJECTION")
        for mirror_key in ("next_items", "effective_next", "next_item_id"):
            if mirror_key in control:
                raw = control[mirror_key]
                try:
                    ids = _mirror_ids(raw if mirror_key == "next_items" else [raw], sequence)
                    if ids != next_ids:
                        conflicts.append("STALE_HUMAN_PRIORITY_OR_RETURN_PROJECTION")
                except BrainSyncError:
                    conflicts.append("STALE_HUMAN_PRIORITY_OR_RETURN_PROJECTION")
        if "sequence" in control:
            rows = control["sequence"]
            if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                raise BrainSyncError("malformed compatibility sequence")
            marked = [row for row in rows if row.get("status") == "NEXT"]
            if marked:
                try:
                    if _mirror_ids(marked, sequence) != next_ids:
                        conflicts.append("STALE_HUMAN_PRIORITY_OR_RETURN_PROJECTION")
                except BrainSyncError:
                    conflicts.append("STALE_HUMAN_PRIORITY_OR_RETURN_PROJECTION")
    done = [item for item in sequence if item["status"] == "DONE"]
    last = next((item.get("completed_by") for item in reversed(done)
                 if isinstance(item.get("completed_by"), Mapping)), None)
    omitted_details = []
    active_controls = []
    nested_controls = []
    for item in active:
        if item["status"] == "NEXT":
            continue
        retained = {key: value for key, value in item.items() if key not in _NONSELECTED_DETAIL_FIELDS}
        omitted = {key: value for key, value in item.items() if key in _NONSELECTED_DETAIL_FIELDS}
        active_controls.append(retained)
        if omitted:
            omitted_details.append({"id": item["id"], "detail": omitted})
            nested_controls.extend(_nested_control_facts(omitted, f"sequence[id={item['id']}]"))
    manifest = []
    planning_controls = {key: value for key, value in roadmap.items() if key not in {
        "version", "sequence", "next_items", "active_track", "active_track_status",
    }}
    planning_controls = json.loads(_canonical_json(planning_controls))
    resolved_bodies = []
    # Only explicitly completed containers have these historical bodies elided.
    for control_key, field in (("research_assurance", "milestones"),
                               ("human_priority_side_track", "historical_completed")):
        control = planning_controls.get(control_key)
        if isinstance(control, dict) and control.get("status") in {"DONE", "COMPLETE"} and field in control:
            body = control.pop(field)
            resolved_bodies.append({"selector": f"planning_controls.{control_key}.{field}", "body": body})
    for context_class, rule, selector, population in (
        ("RESOLVED_HISTORY", "OMIT_DONE_SEQUENCE_BODIES_V1", "sequence[status=DONE]", done),
        ("UNRELATED_LINEAGE_DETAIL", "OMIT_NONSELECTED_DETAIL_FIELDS_V1",
         "sequence[status!=NEXT,DONE].closed_detail_fields", omitted_details),
    ):
        if population:
            manifest.append({"class": context_class, "rule": rule,
                             "rule_version": ELISION_RULE_VERSION, "selector": selector,
                             "source_digest": _source_digest(population), "count": len(population)})
    for body in resolved_bodies:
        manifest.append({"class": "RESOLVED_HISTORY", "rule": "OMIT_COMPLETED_CONTROL_HISTORY_V1",
                         "rule_version": ELISION_RULE_VERSION, "selector": body["selector"],
                         "source_digest": _source_digest(body["body"]), "count": 1})
    projection = {
        "format": "AIOS_ACTIVE_PLANNING_PROJECTION", "projection_version": PLANNING_PROJECTION_VERSION,
        "present": True, "version": roadmap.get("version", 1),
        "active_track": roadmap.get("active_track"), "active_track_status": roadmap.get("active_track_status"),
        "next_items": mirrors if "next_items" in roadmap else next_ids,
        "sequence_count": len(sequence), "last_published_task": last.get("task_id") if last else None,
        "predecessor_anchor": seq_next[0].get("predecessor", last) if len(seq_next) == 1 else last,
        "effective_next": dict(seq_next[0]) if len(seq_next) == 1 and not conflicts else None,
        "competing_next_controls": [dict(item) for item in seq_next] if conflicts else [],
        "next_proof": {"status": "CONFLICT" if conflicts else "UNIQUE" if len(seq_next) == 1 else "NONE",
                       "sequence_next_ids": next_ids, "conflicts": sorted(set(conflicts))},
        "active_controls": active_controls,
        "nested_control_facts": nested_controls,
        # Current Human intent, priority, risk, authority and unknown facts stay exact.
        "planning_controls": planning_controls,
        "source_digest": source_digest, "elision_manifest": manifest,
    }
    if len(_canonical_json(projection)) > MAX_PLANNING_BYTES:
        raise BrainSyncError("non-elidable planning projection exceeds its exact bound")
    return json.loads(_canonical_json(projection))


def observe_brain_sync(
    repo: str | Path | None = None,
) -> BrainSyncSnapshot:
    """Reconstruct Brain Sync continuity facts deterministically without mutation."""
    root = resolve_repository(repo)
    main_sha = _resolve_main_sha(root)
    repo_name = _resolve_repository_name(root)

    code, _, _ = _git_cmd(root, "symbolic-ref", "--quiet", "HEAD", allow_fail=True)
    if code == 0:
        try:
            remote_name = resolve_transport_remote(root)
        except ReviewTransportError:
            remote_name = None
    else:
        remote_name = resolve_detached_observation_remote(root)

    code, remote_url, _ = _git_cmd(root, "config", "--get", f"remote.{remote_name}.url", allow_fail=True)
    repo_info: dict[str, Any] = {
        "root": str(root),
        "name": repo_name,
        "main_sha": main_sha,
        "remote": remote_name,
        "remote_url": remote_url.strip() if code == 0 and remote_url.strip() else None,
    }

    source_bindings: dict[str, Any] = {}
    task_contract = None
    supporting_context = None

    def snapshot_for_sources(**values: Any) -> BrainSyncSnapshot:
        if _resolve_main_sha(root) != main_sha:
            raise BrainSyncError("canonical main moved during Brain Sync")
        return BrainSyncSnapshot(**values, source_bindings=dict(source_bindings),
                                 task_contract=task_contract, supporting_context=supporting_context)

    raw_text = _read_main_source(root, main_sha, ".ai/roadmap-state.yaml")
    if raw_text is None:
        roadmap_summary = {"present": False}
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="MISSING_ROADMAP",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=None,
            blocker={"code": "MISSING_ROADMAP", "message": ".ai/roadmap-state.yaml is missing"},
        )

    try:
        roadmap_data = yaml.load(raw_text, Loader=_RoadmapLoader)
        source_bindings["roadmap"] = {"commit": main_sha, "path": ".ai/roadmap-state.yaml",
                                      "source_digest": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()}
    except (OSError, UnicodeError, yaml.YAMLError, BrainSyncError) as exc:
        roadmap_summary = {"present": True, "valid": False}
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="MALFORMED_ROADMAP",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=None,
            blocker={"code": "MALFORMED_ROADMAP", "message": f"failed to load roadmap: {exc}"},
        )

    if not isinstance(roadmap_data, Mapping):
        roadmap_summary = {"present": True, "valid": False}
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="MALFORMED_ROADMAP",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=None,
            blocker={"code": "MALFORMED_ROADMAP", "message": "roadmap-state.yaml must be a mapping"},
        )

    active_track_status = roadmap_data.get("active_track_status")
    sequence = roadmap_data.get("sequence", [])

    try:
        roadmap_summary = project_active_planning(roadmap_data)
    except BrainSyncError as exc:
        return snapshot_for_sources(
            repository=repo_info, main_sha=main_sha, roadmap={"present": True, "valid": False},
            selection_status="MALFORMED_ROADMAP", lifecycle_state="BLOCKED", next_action="NONE",
            authority="NONE", blocker={"code": "MALFORMED_ROADMAP", "message": str(exc)},
        )

    # Verify roadmap DONE items against canonical Git ancestry
    if isinstance(sequence, list):
        for item in sequence:
            if isinstance(item, Mapping) and item.get("status") == "DONE":
                completed_by = item.get("completed_by")
                if isinstance(completed_by, Mapping):
                    published_sha = completed_by.get("published_sha")
                    if isinstance(published_sha, str) and published_sha:
                        try:
                            is_ancestor = _git_is_ancestor(root, published_sha, main_sha)
                        except OperatorError as exc:
                            raise BrainSyncError(
                                "cannot observe Git ancestry for roadmap item "
                                f"{str(item.get('id'))[:80]!r} "
                                f"(published_sha={published_sha[:64]!r}, "
                                f"main_sha={main_sha[:64]!r})"
                            ) from exc
                        if not is_ancestor:
                            return snapshot_for_sources(
                                repository=repo_info,
                                main_sha=main_sha,
                                roadmap=roadmap_summary,
                                selection_status="ROADMAP_LINEAGE_CONFLICT",
                                lifecycle_state="BLOCKED",
                                next_action="NONE",
                                authority="NONE",
                                selected_task=None,
                                unified_state=None,
                                blocker={
                                    "code": "ROADMAP_LINEAGE_CONFLICT",
                                    "item_id": item.get("id"),
                                    "published_sha": published_sha,
                                    "main_sha": main_sha,
                                    "message": (
                                        f"roadmap item {item.get('id')} published SHA {published_sha} "
                                        f"is not an ancestor of main {main_sha}"
                                    ),
                                },
                            )

    proof = roadmap_summary["next_proof"]
    if proof["status"] == "CONFLICT":
        return snapshot_for_sources(
            repository=repo_info, main_sha=main_sha, roadmap=roadmap_summary,
            selection_status="AMBIGUOUS_NEXT", lifecycle_state="BLOCKED", next_action="NONE",
            authority="NONE", blocker={"code": "AMBIGUOUS_NEXT", "proof": proof,
                "candidates": proof["sequence_next_ids"],
                "message": "competing or malformed roadmap control facts"},
        )
    candidates = [roadmap_summary["effective_next"]] if proof["status"] == "UNIQUE" else []

    if len(candidates) == 0:
        if active_track_status == "COMPLETE":
            return snapshot_for_sources(
                repository=repo_info,
                main_sha=main_sha,
                roadmap=roadmap_summary,
                selection_status="COMPLETED_TRACK",
                lifecycle_state="COMPLETE",
                next_action="NONE",
                authority="NONE",
                selected_task=None,
                unified_state=None,
                blocker=None,
            )
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="NO_NEXT_ITEMS",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=None,
            blocker={"code": "NO_NEXT_ITEMS", "message": "roadmap has no NEXT items"},
        )

    candidate = candidates[0]
    task_id = candidate.get("task_id")
    task_revision = candidate.get("task_revision")

    if not task_id:
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="UNAUTHORED_TASK",
            lifecycle_state="PLANNING",
            next_action="TASK_AUTHORING",
            authority="HUMAN_BRAIN_PLANNING",
            selected_task=None,
            unified_state=None,
            blocker=None,
        )

    task_file = root / ".ai" / "tasks" / f"{task_id}.yaml"
    task_text = _read_main_source(root, main_sha, f".ai/tasks/{task_id}.yaml")
    if task_text is None:
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="UNAUTHORED_TASK",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=None,
            blocker={
                "code": "UNAUTHORED_TASK",
                "task_id": task_id,
                "message": f"task file {task_file} does not exist",
            },
        )

    try:
        if not task_file.is_file() or task_file.read_text(encoding="utf-8") != task_text:
            raise BrainSyncError("selected TASK worktree differs from exact canonical main TASK")
        task_data = yaml.load(task_text, Loader=_RoadmapLoader)
        task = load_task(root, task_id)
    except Exception as exc:
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="BLOCKED",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=None,
            blocker={"code": "CANONICAL_TASK_CONFLICT", "task_id": task_id, "message": str(exc)},
        )

    if task_revision is not None and task.revision != task_revision:
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="ROADMAP_LINEAGE_CONFLICT",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=None,
            blocker={
                "code": "ROADMAP_LINEAGE_CONFLICT",
                "task_id": task_id,
                "message": f"roadmap specifies revision {task_revision} but task file is revision {task.revision}",
            },
        )

    source_bindings["task"] = {"commit": main_sha, "path": f".ai/tasks/{task_id}.yaml",
                               "source_digest": hashlib.sha256(task_text.encode("utf-8")).hexdigest()}
    # The current contract is exact; only its descriptive problem body is overflow eligible.
    # Historical TASK/spec/lineage bodies are never hydrated for this observation.
    task_contract = {key: value for key, value in task_data.items() if key != "problem"}
    supporting_context = {"task_problem": task_data["problem"]} if "problem" in task_data else None

    try:
        unified_obs = observe_unified_state(task_id, repo=root)
    except Exception as exc:
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="BLOCKED",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task={"id": task.task_id, "revision": task.revision},
            unified_state=None,
            blocker={"code": "UNIFIED_STATE_ERROR", "message": str(exc)},
        )

    if unified_obs.lifecycle_state == "DONE":
        return snapshot_for_sources(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="ROADMAP_LIFECYCLE_CONFLICT",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=unified_obs.as_dict(),
            blocker={
                "code": "ROADMAP_LIFECYCLE_CONFLICT",
                "task_id": task_id,
                "message": f"roadmap lists {task_id} as NEXT, but canonical lifecycle is already DONE",
            },
        )

    return snapshot_for_sources(
        repository=repo_info,
        main_sha=main_sha,
        roadmap=roadmap_summary,
        selection_status="SELECTED",
        lifecycle_state=unified_obs.lifecycle_state,
        next_action=unified_obs.next_action,
        authority=unified_obs.as_dict()["authority"],
        selected_task={"id": task.task_id, "revision": task.revision},
        unified_state=unified_obs.as_dict(),
        blocker=unified_obs.blocker,
    )


build_brain_sync_snapshot = observe_brain_sync

__all__ = [
    "BrainSyncError",
    "BrainSyncSnapshot",
    "build_brain_sync_snapshot",
    "observe_brain_sync",
    "project_active_planning",
]
