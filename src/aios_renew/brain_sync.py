"""Authoritative Brain Sync observation boundary for AIOS-renew.

Reconstructs repository/main identity, roadmap planning bookmark, selected exact TASK,
and Unified State lifecycle next_action/authority without acquiring mutation authority,
creating RUNs, invoking Executors, running verification, or mutating runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass
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


class _RoadmapConflict(BrainSyncError):
    """A declared active pointer disagrees with another canonical identity."""


_MAX_NEXT_ITEMS = 16
_MAX_RETURN_ITEMS = 2
_ITEM_FIELDS = (
    "id", "status", "phase", "authority", "task_id", "task_revision",
    "task_blob_sha", "task_author_commit_sha", "authored_commit_sha",
    "parent_milestone", "planning_document", "objective", "return_to",
)
_PUBLICATION_FIELDS = (
    "task_id", "task_revision", "run_id", "review_id", "reviewed_sha", "published_sha",
)


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
        last_pub = self.roadmap.get("last_published_task") or "unavailable"
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
        except (ReviewTransportError, RemoteQueryError):
            pass
    code, out, _ = _git_cmd(repo, "rev-parse", "--verify", "--quiet", "refs/heads/main", allow_fail=True)
    if code == 0 and out.strip():
        return out.strip()
    code, out, _ = _git_cmd(repo, "rev-parse", "--verify", "--quiet", "HEAD", allow_fail=True)
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


def _extract_next_candidates(
    roadmap: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Extract candidate NEXT items from roadmap next_items and sequence."""
    sequence = roadmap.get("sequence", [])
    if not isinstance(sequence, list):
        raise _RoadmapConflict("roadmap sequence must be a list")
    next_items = roadmap.get("next_items")
    if next_items is not None and not isinstance(next_items, list):
        raise _RoadmapConflict("roadmap next_items must be a list")
    if isinstance(next_items, list) and len(next_items) > _MAX_NEXT_ITEMS:
        raise _RoadmapConflict("too many roadmap NEXT pointers")

    seq_next = []
    if isinstance(sequence, list):
        for item in sequence:
            if isinstance(item, Mapping) and item.get("status") == "NEXT":
                seq_next.append(item)

    # When next_items is explicitly an empty list
    if next_items is not None and isinstance(next_items, list) and len(next_items) == 0:
        if seq_next:
            # Conflicting indicators: next_items says none, sequence says NEXT
            return [dict(item) for item in seq_next] + [{"id": "conflict_empty_next_items"}]
        return []

    # When next_items is populated
    if isinstance(next_items, list) and len(next_items) > 0:
        candidates: list[dict[str, Any]] = []
        for raw in next_items:
            if isinstance(raw, str):
                matching = [
                    item for item in sequence
                    if isinstance(item, Mapping) and raw in (item.get("id"), item.get("task_id"))
                ]
                if matching:
                    candidates.extend(dict(item) for item in matching)
                else:
                    candidates.append({
                        "id": raw,
                        "task_id": raw if raw.startswith("TASK-") else None,
                    })
            elif isinstance(raw, Mapping):
                identities = [raw[key] for key in ("id", "task_id") if raw.get(key) is not None]
                matching = [item for item in sequence if isinstance(item, Mapping)
                            and any(identity in (item.get("id"), item.get("task_id"))
                                    for identity in identities)]
                if not matching:
                    candidates.append(dict(raw))
                else:
                    for item in matching:
                        # Inline pointers cannot override canonical sequence identity.
                        if any(key in raw and key in item and raw[key] != item[key]
                               for key in _ITEM_FIELDS + (
                                   "live_exit_gate", "current_live_blocker",
                                   "engineering_completion", "completed_by", "published_sha",
                               )):
                            candidates.extend((dict(raw), dict(item)))
                        else:
                            candidates.append({**item, **raw})
            else:
                raise _RoadmapConflict("NEXT pointer must be a text id or mapping")

        if seq_next:
            seq_ids = {item.get("task_id") or item.get("id") for item in seq_next}
            cand_ids = {item.get("task_id") or item.get("id") for item in candidates}
            if seq_ids != cand_ids:
                return candidates + [dict(item) for item in seq_next]
        return candidates

    return [dict(item) for item in seq_next]


def _bounded_fields(item: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    result = {}
    for key in fields:
        if key not in item:
            continue
        value = item[key]
        if value is None:
            result[key] = None
            continue
        if key == "task_revision":
            if type(value) is not int or not 0 < value <= 2147483647:
                raise _RoadmapConflict("invalid active task_revision")
        else:
            limit = 2048 if key == "objective" else 256
            if not isinstance(value, str):
                raise _RoadmapConflict(f"active field {key} must be text")
            try:
                size = len(value.encode("utf-8", errors="strict"))
            except UnicodeError as exc:
                raise _RoadmapConflict(f"invalid active field {key} Unicode") from exc
            if size > limit:
                raise _RoadmapConflict(f"active field {key} exceeds {limit} bytes")
        result[key] = value
    return result


def _active_items(roadmap: Mapping[str, Any], candidates: list[dict[str, Any]]) -> list[Mapping[str, Any]]:
    """Read only explicit return edges; never select a new roadmap successor."""
    sequence = roadmap.get("sequence", [])
    if len(candidates) > _MAX_NEXT_ITEMS:
        raise _RoadmapConflict("too many roadmap NEXT candidates")
    if len(candidates) > 1:
        return []
    if candidates:
        active = candidates[0]
        if active.get("status") not in (None, "NEXT"):
            raise _RoadmapConflict("NEXT pointer targets a non-NEXT item")
    else:
        gates = [item for item in sequence if isinstance(item, Mapping)
                 and item.get("status") in ("BLOCKED", "LIVE_EXIT_GATE_PENDING")]
        if len(gates) > 1:
            raise _RoadmapConflict("ambiguous active roadmap gate")
        if not gates:
            return []
        active = gates[0]
    items = [active]
    seen: set[str] = set()
    for index in range(_MAX_RETURN_ITEMS + 1):
        item = items[-1]
        _bounded_fields(item, ("id", "status", "return_to"))
        identity = item.get("id")
        if identity is not None:
            if identity in seen or sum(isinstance(other, Mapping) and other.get("id") == identity
                                       for other in sequence) > 1:
                raise _RoadmapConflict("duplicate or cyclic active roadmap identity")
            seen.add(identity)
        target = item.get("return_to")
        if target is None:
            break
        matches = [other for other in sequence
                   if isinstance(other, Mapping) and other.get("id") == target]
        if not target or target in seen or len(matches) != 1:
            raise _RoadmapConflict("missing, cyclic or ambiguous active return_to")
        if index == _MAX_RETURN_ITEMS:
            # Retain the exact boundary id, but do not hydrate a further body.
            break
        items.append(matches[0])
    return items


def _project_item(item: Mapping[str, Any]) -> dict[str, Any]:
    # Even directly relevant DONE entries expose identity only by default.
    fields = _ITEM_FIELDS if item.get("status") != "DONE" else (
        "id", "status", "task_id", "task_revision", "task_blob_sha", "return_to",
    )
    result = _bounded_fields(item, fields)
    for key, fields in (
        ("live_exit_gate", ("authority", "status", "contract", "published_subject_sha")),
        ("current_live_blocker", ("code", "authority", "next_action")),
        ("engineering_completion", _PUBLICATION_FIELDS),
        ("completed_by", _PUBLICATION_FIELDS),
    ):
        if key in item:
            if not isinstance(item[key], Mapping):
                raise _RoadmapConflict(f"invalid active {key}")
            result[key] = _bounded_fields(item[key], fields)
    return result


def _publication_pointer(item: Mapping[str, Any]) -> dict[str, Any] | None:
    pointers = [item[key] for key in ("completed_by", "engineering_completion")
                if isinstance(item.get(key), Mapping) and item[key].get("published_sha")]
    if item.get("published_sha"):
        pointers.append(item)
    gate = item.get("live_exit_gate")
    if isinstance(gate, Mapping) and gate.get("published_subject_sha"):
        pointers.append({"task_id": item.get("task_id"),
                         "published_sha": gate["published_subject_sha"]})
    if not pointers:
        return None
    result: dict[str, Any] = {}
    for pointer in pointers:
        for key, value in _bounded_fields(pointer, _PUBLICATION_FIELDS).items():
            if value is not None:
                if key in result and result[key] != value:
                    raise _RoadmapConflict(f"conflicting active publication {key}")
                result[key] = value
    if result.get("reviewed_sha") not in (None, result["published_sha"]):
        raise _RoadmapConflict("active reviewed/published SHA conflict")
    for key in ("task_id", "task_revision"):
        if item.get(key) is not None and result.get(key) not in (None, item[key]):
            raise _RoadmapConflict(f"active publication {key} conflict")
    if not re.fullmatch(r"[0-9a-f]{40}", result["published_sha"]):
        raise _RoadmapConflict("invalid roadmap publication SHA")
    return result


def _check_ancestry(root: Path, item: Mapping[str, Any], sha: str, main_sha: str) -> bool:
    try:
        return _git_is_ancestor(root, sha, main_sha)
    except OperatorError as exc:
        raise BrainSyncError(
            "cannot observe Git ancestry for roadmap item "
            f"{str(item.get('id'))[:80]!r} (published_sha={sha[:64]!r}, "
            f"main_sha={main_sha[:64]!r})"
        ) from exc


def _check_task_pointer(root: Path, item: Mapping[str, Any], main_sha: str, *, selected: bool) -> None:
    """Prove declared exact active authoring pointers without loading RUN history."""
    task_id = item.get("task_id")
    if task_id is None and isinstance(item.get("id"), str) and item["id"].startswith("TASK-"):
        task_id = item["id"]
    if task_id is None:
        return
    if not isinstance(task_id, str) or not re.fullmatch(r"TASK-[0-9]+", task_id):
        raise _RoadmapConflict("invalid active task_id")
    task_path = f".ai/tasks/{task_id}.yaml"
    if not selected:
        try:
            task = load_task(root, task_id)
        except OperatorError as exc:
            raise _RoadmapConflict(f"active {task_id} TASK unavailable") from exc
        if item.get("task_revision") not in (None, task.revision):
            raise _RoadmapConflict(f"active {task_id} revision conflict")
    blob = item.get("task_blob_sha")
    commits = [item[key] for key in ("task_author_commit_sha", "authored_commit_sha")
               if item.get(key) is not None]
    if blob is None and not commits:
        return  # Selected bare revision retains the existing load_task boundary.
    for value in ([blob] if blob is not None else []) + commits:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise _RoadmapConflict(f"invalid active {task_id} exact authoring pointer")
    code, local_blob, _ = _git_cmd(root, "hash-object", task_path, allow_fail=True)
    if code or (blob is not None and local_blob.strip() != blob):
        raise _RoadmapConflict(f"active {task_id} blob conflict")
    code, main_blob, _ = _git_cmd(root, "rev-parse", f"{main_sha}:{task_path}", allow_fail=True)
    if code or main_blob.strip() != local_blob.strip():
        raise _RoadmapConflict(f"active {task_id} canonical main identity conflict")
    for commit in commits:
        if not _check_ancestry(root, item, commit, main_sha):
            raise _RoadmapConflict(f"active {task_id} author commit is not on main")
        code, authored_blob, _ = _git_cmd(root, "rev-parse", f"{commit}:{task_path}", allow_fail=True)
        if code or authored_blob.strip() != local_blob.strip():
            raise _RoadmapConflict(f"active {task_id} authoring identity conflict")


def observe_brain_sync(
    repo: str | Path | None = None,
    *,
    include_history: bool = False,
) -> BrainSyncSnapshot:
    """Observe active continuity; opt in to DONE bodies/ancestry for historical decisions."""
    if type(include_history) is not bool:
        raise BrainSyncError("include_history must be an explicit boolean")
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

    roadmap_path = root / ".ai" / "roadmap-state.yaml"
    if not roadmap_path.is_file():
        roadmap_summary = {"present": False}
        return BrainSyncSnapshot(
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
        raw_text = roadmap_path.read_text(encoding="utf-8")
        roadmap_data = yaml.safe_load(raw_text)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        roadmap_summary = {"present": True, "valid": False}
        return BrainSyncSnapshot(
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
        return BrainSyncSnapshot(
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
    roadmap_summary: dict[str, Any] = {
        "present": True,
        "version": None,
        "last_published_task": None,
        "publication": {"status": "UNAVAILABLE"},
    }
    try:
        version = roadmap_data.get("version", 1)
        if type(version) is not int or not 0 < version <= 2147483647:
            raise _RoadmapConflict("invalid roadmap version")
        roadmap_summary["version"] = version
        candidates = _extract_next_candidates(roadmap_data)
        # Bound candidate identity even when selection must remain ambiguous.
        candidate_ids = []
        for candidate in candidates:
            _bounded_fields(candidate, _ITEM_FIELDS)
            identity = candidate.get("task_id") or candidate.get("id")
            if not isinstance(identity, str) or not identity:
                raise _RoadmapConflict("NEXT pointer has no identity")
            candidate_ids.append(identity)
        if len(candidate_ids) > _MAX_NEXT_ITEMS:
            raise _RoadmapConflict("too many roadmap NEXT candidates")
        active_items = _active_items(roadmap_data, candidates)
        if active_track_status == "COMPLETE" and active_items:
            raise _RoadmapConflict("completed track still has an active commitment")
        next_items = roadmap_data.get("next_items")
        next_ids = [item.get("id") or item.get("task_id") if isinstance(item, Mapping) else item
                    for item in (next_items or [])]
        for identity in next_ids:
            _bounded_fields({"id": identity}, ("id",))
            if not identity:
                raise _RoadmapConflict("NEXT pointer has no identity")
        roadmap_summary.update({
            **_bounded_fields(roadmap_data, ("active_track", "active_track_status")),
            "next_items": next_ids,
            "sequence_count": len(sequence),
            "active_item": _project_item(active_items[0]) if active_items else None,
            "return_path": [_project_item(item) for item in active_items[1:]],
            "return_path_truncated": bool(active_items and active_items[-1].get("return_to")),
        })
        for index, item in enumerate(active_items):
            _check_task_pointer(root, item, main_sha, selected=index == 0 and bool(candidates))

        lineage_items = list(active_items)
        if include_history:
            history = [dict(item) for item in sequence
                       if isinstance(item, Mapping) and item.get("status") == "DONE"]
            roadmap_summary["history"] = history
            lineage_items.extend(history)
        checked: set[str] = set()
        for index, item in enumerate(lineage_items):
            pointer = _publication_pointer(item)
            if pointer is None:
                continue
            published_sha = pointer["published_sha"]
            if published_sha not in checked and not _check_ancestry(root, item, published_sha, main_sha):
                return BrainSyncSnapshot(
                    repository=repo_info, main_sha=main_sha, roadmap=roadmap_summary,
                    selection_status="ROADMAP_LINEAGE_CONFLICT", lifecycle_state="BLOCKED",
                    next_action="NONE", authority="NONE",
                    blocker={
                        "code": "ROADMAP_LINEAGE_CONFLICT", "item_id": item.get("id"),
                        "published_sha": published_sha, "main_sha": main_sha,
                        "message": f"roadmap item {item.get('id')} publication is not on main",
                    },
                )
            checked.add(published_sha)
            # This is a relevance pointer along explicit active edges, never a
            # global recency claim. Historical sequence order has no authority.
            if index < len(active_items) and roadmap_summary["publication"]["status"] == "UNAVAILABLE":
                roadmap_summary["publication"] = {
                    "status": "RELEVANT_ACTIVE_LINEAGE", "item_id": item.get("id"), **pointer,
                }
                roadmap_summary["last_published_task"] = pointer.get("task_id")
    except _RoadmapConflict as exc:
        return BrainSyncSnapshot(
            repository=repo_info, main_sha=main_sha, roadmap=roadmap_summary,
            selection_status="ROADMAP_LINEAGE_CONFLICT", lifecycle_state="BLOCKED",
            next_action="NONE", authority="NONE",
            blocker={"code": "ROADMAP_LINEAGE_CONFLICT", "message": str(exc)},
        )

    if len(candidates) == 0:
        if active_track_status == "COMPLETE":
            return BrainSyncSnapshot(
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
        return BrainSyncSnapshot(
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

    if len(candidates) > 1:
        return BrainSyncSnapshot(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="AMBIGUOUS_NEXT",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=None,
            blocker={
                "code": "AMBIGUOUS_NEXT",
                "candidates": candidate_ids,
                "message": "multiple or conflicting NEXT items in roadmap",
            },
        )

    candidate = candidates[0]
    task_id = candidate.get("task_id")
    if not task_id and isinstance(candidate.get("id"), str) and candidate["id"].startswith("TASK-"):
        task_id = candidate["id"]
    task_revision = candidate.get("task_revision")

    if not task_id:
        return BrainSyncSnapshot(
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
                "item_id": candidate.get("id"),
                "message": "NEXT item has no associated task_id",
            },
        )

    task_file = root / ".ai" / "tasks" / f"{task_id}.yaml"
    if not task_file.is_file():
        return BrainSyncSnapshot(
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
        task = load_task(root, task_id)
    except Exception as exc:
        return BrainSyncSnapshot(
            repository=repo_info,
            main_sha=main_sha,
            roadmap=roadmap_summary,
            selection_status="UNAUTHORED_TASK",
            lifecycle_state="BLOCKED",
            next_action="NONE",
            authority="NONE",
            selected_task=None,
            unified_state=None,
            blocker={"code": "UNAUTHORED_TASK", "task_id": task_id, "message": str(exc)},
        )

    if task_revision is not None and task.revision != task_revision:
        return BrainSyncSnapshot(
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

    try:
        unified_obs = observe_unified_state(task_id, repo=root)
    except Exception as exc:
        return BrainSyncSnapshot(
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
        return BrainSyncSnapshot(
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

    return BrainSyncSnapshot(
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
]
