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
    sequence = roadmap.get("sequence") or []
    next_items = roadmap.get("next_items")

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
                    if isinstance(item, Mapping) and item.get("id") == raw
                ]
                if matching:
                    candidates.append(dict(matching[0]))
                else:
                    candidates.append({
                        "id": raw,
                        "task_id": raw if raw.startswith("TASK-") else None,
                    })
            elif isinstance(raw, Mapping):
                candidates.append(dict(raw))

        if seq_next:
            seq_ids = {item.get("task_id") or item.get("id") for item in seq_next}
            cand_ids = {item.get("task_id") or item.get("id") for item in candidates}
            if seq_ids != cand_ids:
                return candidates + [dict(item) for item in seq_next]
        return candidates

    return [dict(item) for item in seq_next]


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

    active_track = roadmap_data.get("active_track")
    active_track_status = roadmap_data.get("active_track_status")
    version = roadmap_data.get("version", 1)
    sequence = roadmap_data.get("sequence") or []
    next_items = roadmap_data.get("next_items")

    last_published_task = None
    if isinstance(sequence, list):
        for item in sequence:
            if isinstance(item, Mapping) and item.get("status") == "DONE":
                completed_by = item.get("completed_by")
                if isinstance(completed_by, Mapping) and completed_by.get("task_id"):
                    last_published_task = completed_by["task_id"]

    roadmap_summary = {
        "present": True,
        "version": version,
        "active_track": active_track,
        "active_track_status": active_track_status,
        "next_items": list(next_items) if isinstance(next_items, list) else [],
        "sequence_count": len(sequence) if isinstance(sequence, list) else 0,
        "last_published_task": last_published_task,
    }

    # Verify roadmap DONE items against canonical Git ancestry
    if isinstance(sequence, list):
        for item in sequence:
            if isinstance(item, Mapping) and item.get("status") == "DONE":
                completed_by = item.get("completed_by")
                if isinstance(completed_by, Mapping):
                    published_sha = completed_by.get("published_sha")
                    if isinstance(published_sha, str) and published_sha:
                        is_ancestor = False
                        try:
                            is_ancestor = _git_is_ancestor(root, published_sha, main_sha)
                        except OperatorError:
                            is_ancestor = False
                        if not is_ancestor:
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
                                    "item_id": item.get("id"),
                                    "published_sha": published_sha,
                                    "main_sha": main_sha,
                                    "message": (
                                        f"roadmap item {item.get('id')} published SHA {published_sha} "
                                        f"is not an ancestor of main {main_sha}"
                                    ),
                                },
                            )

    candidates = _extract_next_candidates(roadmap_data)

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
        candidate_ids = [c.get("task_id") or c.get("id") for c in candidates]
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
