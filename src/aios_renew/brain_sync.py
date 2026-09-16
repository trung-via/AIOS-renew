"""Authoritative AIOS Brain Sync Snapshot observation boundary.

Provides one versioned, deterministic, read-only canonical rehydration snapshot
that reconstructs repository identity, planning bookmark, exact Unified State
lifecycle, next action/authority, and explicit blockers from canonical repository
facts without using chat/model memory.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping
import yaml

from .operator import OperatorError, load_task, resolve_repository
from .unified_state import UnifiedStateObservation, observe_unified_state, _UNIFIED_AUTHORITIES


class BrainSyncError(Exception):
    """Raised when Brain Sync observation fails unexpectedly."""


def _git(repo: Path, *args: str, allow_fail: bool = False) -> tuple[int, str, str]:
    """Execute one read-only Git command against the specified repository."""
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, UnicodeError) as exc:
        if allow_fail:
            return 1, "", str(exc)
        raise BrainSyncError(f"Git invocation failed: {exc}") from exc


def _resolve_main_sha(repo: Path, head_sha: str) -> str:
    """Resolve the canonical main branch SHA from local or remote tracking refs."""
    code, out, _ = _git(repo, "rev-parse", "--verify", "refs/heads/main", allow_fail=True)
    if code == 0 and out:
        return out
    code, out, _ = _git(repo, "rev-parse", "--verify", "refs/remotes/origin/main", allow_fail=True)
    if code == 0 and out:
        return out
    code, out, _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", allow_fail=True)
    if code == 0 and out == "main":
        return head_sha
    return head_sha


@dataclass(frozen=True)
class BrainSyncSnapshot:
    """Versioned, deterministic, read-only canonical rehydration snapshot."""

    repository_root: str
    head_sha: str
    main_sha: str
    roadmap_present: bool
    active_track: str | None
    active_track_status: str | None
    next_items: tuple[str, ...]
    selected_task_id: str | None
    selected_task_revision: int | None
    selection_mode: str
    lifecycle_state: str
    next_action: str
    authority: str
    unified_state: Mapping[str, Any] | None = None
    blocker: Mapping[str, Any] | None = None
    last_published_task_id: str | None = None
    last_published_sha: str | None = None
    active_run_id: str | None = None
    active_finding_id: str | None = None
    active_failure_run_id: str | None = None
    run_created: bool = False
    executor_invoked: bool = False
    verification_invoked: bool = False
    state_mutated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "AIOS_BRAIN_SYNC_SNAPSHOT",
            "version": 1,
            "kind": "BRAIN_SYNC_SNAPSHOT",
            "repository": {
                "root": self.repository_root,
                "head_sha": self.head_sha,
                "main_sha": self.main_sha,
            },
            "roadmap": {
                "present": self.roadmap_present,
                "active_track": self.active_track,
                "active_track_status": self.active_track_status,
                "next_items": list(self.next_items),
                "last_published": (
                    {
                        "task_id": self.last_published_task_id,
                        "published_sha": self.last_published_sha,
                    }
                    if self.last_published_task_id is not None
                    else None
                ),
            },
            "task": (
                {
                    "id": self.selected_task_id,
                    "revision": self.selected_task_revision,
                }
                if self.selected_task_id is not None
                else None
            ),
            "selection_mode": self.selection_mode,
            "lifecycle_state": self.lifecycle_state,
            "next_action": self.next_action,
            "authority": self.authority,
            "unified_state": dict(self.unified_state) if self.unified_state is not None else None,
            "blocker": dict(self.blocker) if self.blocker is not None else None,
            "active_identifiers": {
                "run_id": self.active_run_id,
                "finding_id": self.active_finding_id,
                "failed_run_id": self.active_failure_run_id,
            },
            "run_created": False,
            "executor_invoked": False,
            "verification_invoked": False,
            "state_mutated": False,
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

    def render_checkpoint(self) -> str:
        state_str = "READY" if self.lifecycle_state in ("READY", "CORRECTION") and not self.blocker else "BLOCKED"
        lines = [
            "PROJECT: AIOS-renew",
            f"MAIN: {self.main_sha}",
            f"LAST PUBLISHED: {self.last_published_task_id or 'none'}",
            f"AUTHORED NEXT TASK: {self.selected_task_id or 'none'}",
            f"ACTIVE RUN: {self.active_run_id or 'none'}",
            f"ACTIVE FINDING: {self.active_finding_id or 'none'}",
            f"ACTIVE FAILURE: {self.active_failure_run_id or 'none'}",
            f"STATE: {state_str}",
        ]
        return "\n".join(lines)


def _blocked_snapshot(
    repo_root: Path,
    head_sha: str,
    main_sha: str,
    *,
    code: str,
    message: str,
    roadmap_present: bool = False,
    active_track: str | None = None,
    active_track_status: str | None = None,
    next_items: tuple[str, ...] = (),
    selected_task_id: str | None = None,
    selected_task_revision: int | None = None,
    selection_mode: str = "NONE",
    last_published_task_id: str | None = None,
    last_published_sha: str | None = None,
    unified_state: Mapping[str, Any] | None = None,
    active_run_id: str | None = None,
    active_finding_id: str | None = None,
    active_failure_run_id: str | None = None,
    extra_blocker: Mapping[str, Any] | None = None,
) -> BrainSyncSnapshot:
    blocker_dict: dict[str, Any] = {"code": code, "message": message}
    if extra_blocker:
        blocker_dict.update(extra_blocker)
    return BrainSyncSnapshot(
        repository_root=str(repo_root),
        head_sha=head_sha,
        main_sha=main_sha,
        roadmap_present=roadmap_present,
        active_track=active_track,
        active_track_status=active_track_status,
        next_items=next_items,
        selected_task_id=selected_task_id,
        selected_task_revision=selected_task_revision,
        selection_mode=selection_mode,
        lifecycle_state="BLOCKED",
        next_action="NONE",
        authority="NONE",
        unified_state=unified_state,
        blocker=blocker_dict,
        last_published_task_id=last_published_task_id,
        last_published_sha=last_published_sha,
        active_run_id=active_run_id,
        active_finding_id=active_finding_id,
        active_failure_run_id=active_failure_run_id,
        run_created=False,
        executor_invoked=False,
        verification_invoked=False,
        state_mutated=False,
    )


def observe_brain_sync(
    repo: str | Path | None = None,
    *,
    task_id: str | None = None,
) -> BrainSyncSnapshot:
    """Observe and reconstruct canonical Brain Sync Snapshot facts without mutation."""
    repo_root = resolve_repository(repo)
    code, head_out, head_err = _git(repo_root, "rev-parse", "HEAD", allow_fail=True)
    if code != 0 or not head_out:
        raise BrainSyncError(f"Failed to acquire repository HEAD SHA: {head_err}")
    head_sha = head_out.strip()
    main_sha = _resolve_main_sha(repo_root, head_sha)

    roadmap_path = repo_root / ".ai" / "roadmap-state.yaml"
    if not roadmap_path.is_file():
        if task_id is None:
            return _blocked_snapshot(
                repo_root,
                head_sha,
                main_sha,
                code="MISSING_ROADMAP",
                message="Roadmap state file .ai/roadmap-state.yaml does not exist",
                roadmap_present=False,
            )
        # Explicit task requested without roadmap present
        roadmap_data: dict[str, Any] = {}
        roadmap_present = False
    else:
        roadmap_present = True
        try:
            raw_yaml = roadmap_path.read_text(encoding="utf-8")
            roadmap_data = yaml.safe_load(raw_yaml)
        except Exception as exc:
            return _blocked_snapshot(
                repo_root,
                head_sha,
                main_sha,
                code="MALFORMED_ROADMAP",
                message=f"Failed to parse .ai/roadmap-state.yaml: {exc}",
                roadmap_present=True,
            )

        if not isinstance(roadmap_data, dict):
            return _blocked_snapshot(
                repo_root,
                head_sha,
                main_sha,
                code="MALFORMED_ROADMAP",
                message="Roadmap state file .ai/roadmap-state.yaml must be a YAML mapping",
                roadmap_present=True,
            )

    active_track = roadmap_data.get("active_track") if roadmap_present else None
    active_track_status = roadmap_data.get("active_track_status") if roadmap_present else None
    next_items_raw = roadmap_data.get("next_items") if roadmap_present else None
    sequence = roadmap_data.get("sequence") or [] if roadmap_present else []

    # Extract last published task & published SHA from sequence
    last_published_task_id: str | None = None
    last_published_sha: str | None = None
    if isinstance(sequence, list):
        for item in sequence:
            if isinstance(item, dict) and item.get("status") == "DONE":
                completed_by = item.get("completed_by")
                if isinstance(completed_by, dict):
                    if completed_by.get("task_id"):
                        last_published_task_id = completed_by.get("task_id")
                    if completed_by.get("published_sha"):
                        last_published_sha = completed_by.get("published_sha")
                elif item.get("task_id"):
                    last_published_task_id = item.get("task_id")

    # Validate sequence lineage consistency
    if isinstance(sequence, list):
        for item in sequence:
            if isinstance(item, dict) and item.get("status") == "DONE":
                completed_by = item.get("completed_by")
                if isinstance(completed_by, dict):
                    pub_sha = completed_by.get("published_sha")
                    if pub_sha and isinstance(pub_sha, str):
                        code, _, _ = _git(repo_root, "cat-file", "-e", pub_sha, allow_fail=True)
                        if code != 0:
                            return _blocked_snapshot(
                                repo_root,
                                head_sha,
                                main_sha,
                                code="ROADMAP_LINEAGE_CONTRADICTION",
                                message=(
                                    f"Completed task {completed_by.get('task_id')} specifies "
                                    f"published_sha {pub_sha} which does not exist in repository"
                                ),
                                roadmap_present=roadmap_present,
                                active_track=active_track,
                                active_track_status=active_track_status,
                                last_published_task_id=last_published_task_id,
                                last_published_sha=last_published_sha,
                            )
                        code_main, _, _ = _git(
                            repo_root, "merge-base", "--is-ancestor", pub_sha, main_sha, allow_fail=True
                        )
                        code_head, _, _ = _git(
                            repo_root, "merge-base", "--is-ancestor", pub_sha, head_sha, allow_fail=True
                        )
                        if code_main != 0 and code_head != 0:
                            return _blocked_snapshot(
                                repo_root,
                                head_sha,
                                main_sha,
                                code="ROADMAP_LINEAGE_CONTRADICTION",
                                message=(
                                    f"Completed task {completed_by.get('task_id')} published_sha "
                                    f"{pub_sha} is not an ancestor of main ({main_sha})"
                                ),
                                roadmap_present=roadmap_present,
                                active_track=active_track,
                                active_track_status=active_track_status,
                                last_published_task_id=last_published_task_id,
                                last_published_sha=last_published_sha,
                            )

    # Extract NEXT candidates
    candidate_next_items: list[str] = []
    if isinstance(next_items_raw, list):
        for entry in next_items_raw:
            if isinstance(entry, str) and entry.strip():
                candidate_next_items.append(entry.strip())
            elif isinstance(entry, dict):
                tid = entry.get("task_id") or entry.get("id")
                if tid and isinstance(tid, str):
                    candidate_next_items.append(tid.strip())
    if not candidate_next_items and isinstance(sequence, list):
        for entry in sequence:
            if isinstance(entry, dict) and entry.get("status") == "NEXT":
                tid = entry.get("task_id") or entry.get("id")
                if tid and isinstance(tid, str):
                    candidate_next_items.append(tid.strip())

    next_items_tuple = tuple(candidate_next_items)

    if task_id is None:
        selection_mode = "ROADMAP_NEXT"
        if active_track_status == "COMPLETE":
            if candidate_next_items:
                return _blocked_snapshot(
                    repo_root,
                    head_sha,
                    main_sha,
                    code="ROADMAP_LINEAGE_CONTRADICTION",
                    message="active_track_status is COMPLETE but roadmap specifies non-empty NEXT items",
                    roadmap_present=roadmap_present,
                    active_track=active_track,
                    active_track_status=active_track_status,
                    next_items=next_items_tuple,
                    selection_mode="NONE",
                    last_published_task_id=last_published_task_id,
                    last_published_sha=last_published_sha,
                )
            return _blocked_snapshot(
                repo_root,
                head_sha,
                main_sha,
                code="COMPLETED_TRACK_NO_NEXT",
                message="Active track is COMPLETE with zero NEXT items",
                roadmap_present=roadmap_present,
                active_track=active_track,
                active_track_status=active_track_status,
                next_items=(),
                selection_mode="NONE",
                last_published_task_id=last_published_task_id,
                last_published_sha=last_published_sha,
            )

        if len(candidate_next_items) == 0:
            return _blocked_snapshot(
                repo_root,
                head_sha,
                main_sha,
                code="NO_NEXT_ITEMS",
                message="Active track has zero NEXT items",
                roadmap_present=roadmap_present,
                active_track=active_track,
                active_track_status=active_track_status,
                next_items=(),
                selection_mode="NONE",
                last_published_task_id=last_published_task_id,
                last_published_sha=last_published_sha,
            )

        if len(candidate_next_items) > 1:
            return _blocked_snapshot(
                repo_root,
                head_sha,
                main_sha,
                code="AMBIGUOUS_NEXT_ITEMS",
                message=f"Roadmap contains multiple NEXT items: {candidate_next_items}",
                roadmap_present=roadmap_present,
                active_track=active_track,
                active_track_status=active_track_status,
                next_items=next_items_tuple,
                selection_mode="NONE",
                last_published_task_id=last_published_task_id,
                last_published_sha=last_published_sha,
                extra_blocker={"candidates": candidate_next_items},
            )

        target_task_id = candidate_next_items[0]
    else:
        selection_mode = "EXPLICIT"
        target_task_id = task_id

    # Check if target_task_id is contradictory with sequence completion
    if isinstance(sequence, list):
        for entry in sequence:
            if isinstance(entry, dict) and entry.get("status") == "DONE":
                completed_by = entry.get("completed_by")
                done_tid = (
                    completed_by.get("task_id")
                    if isinstance(completed_by, dict)
                    else entry.get("task_id")
                )
                if done_tid == target_task_id or entry.get("id") == target_task_id:
                    return _blocked_snapshot(
                        repo_root,
                        head_sha,
                        main_sha,
                        code="ROADMAP_LINEAGE_CONTRADICTION",
                        message=f"Task {target_task_id} is already marked DONE in roadmap sequence",
                        roadmap_present=roadmap_present,
                        active_track=active_track,
                        active_track_status=active_track_status,
                        next_items=next_items_tuple,
                        selected_task_id=(target_task_id if selection_mode == "EXPLICIT" else None),
                        selection_mode=selection_mode,
                        last_published_task_id=last_published_task_id,
                        last_published_sha=last_published_sha,
                    )

    # Check canonical task authoring
    task_path = repo_root / ".ai" / "tasks" / f"{target_task_id}.yaml"
    if not task_path.is_file():
        return _blocked_snapshot(
            repo_root,
            head_sha,
            main_sha,
            code="UNAUTHORED_NEXT_TASK",
            message=f"Task {target_task_id} is not authored at {task_path}",
            roadmap_present=roadmap_present,
            active_track=active_track,
            active_track_status=active_track_status,
            next_items=next_items_tuple,
            selected_task_id=(target_task_id if selection_mode == "EXPLICIT" else None),
            selection_mode=selection_mode,
            last_published_task_id=last_published_task_id,
            last_published_sha=last_published_sha,
            extra_blocker={"task_id": target_task_id},
        )

    try:
        task = load_task(repo_root, target_task_id)
    except Exception as exc:
        return _blocked_snapshot(
            repo_root,
            head_sha,
            main_sha,
            code="UNAUTHORED_NEXT_TASK",
            message=f"Task {target_task_id} is malformed: {exc}",
            roadmap_present=roadmap_present,
            active_track=active_track,
            active_track_status=active_track_status,
            next_items=next_items_tuple,
            selected_task_id=(target_task_id if selection_mode == "EXPLICIT" else None),
            selection_mode=selection_mode,
            last_published_task_id=last_published_task_id,
            last_published_sha=last_published_sha,
            extra_blocker={"task_id": target_task_id},
        )

    # Reduce lifecycle via authoritative Unified State
    try:
        unified_obs = observe_unified_state(task.task_id, repo=repo_root)
    except Exception as exc:
        return _blocked_snapshot(
            repo_root,
            head_sha,
            main_sha,
            code="UNIFIED_STATE_UNAVAILABLE",
            message=f"Unified state reduction failed for {task.task_id}: {exc}",
            roadmap_present=roadmap_present,
            active_track=active_track,
            active_track_status=active_track_status,
            next_items=next_items_tuple,
            selected_task_id=task.task_id,
            selected_task_revision=task.revision,
            selection_mode=selection_mode,
            last_published_task_id=last_published_task_id,
            last_published_sha=last_published_sha,
        )

    # Contradiction check: Unified State reports DONE for a roadmap NEXT task
    if selection_mode == "ROADMAP_NEXT" and unified_obs.lifecycle_state == "DONE":
        return _blocked_snapshot(
            repo_root,
            head_sha,
            main_sha,
            code="ROADMAP_LINEAGE_CONTRADICTION",
            message=f"Roadmap NEXT task {task.task_id} is already DONE in engineering lineage (lifecycle_state DONE in Unified State)",
            roadmap_present=roadmap_present,
            active_track=active_track,
            active_track_status=active_track_status,
            next_items=next_items_tuple,
            selected_task_id=None,
            selected_task_revision=None,
            selection_mode=selection_mode,
            last_published_task_id=last_published_task_id,
            last_published_sha=last_published_sha,
            unified_state=unified_obs.as_dict(),
            active_run_id=unified_obs.run_id,
            active_finding_id=unified_obs.finding_id,
            active_failure_run_id=unified_obs.failed_run_id,
        )

    is_unified_blocked = unified_obs.lifecycle_state == "BLOCKED" or unified_obs.blocker is not None
    effective_blocker = unified_obs.blocker if is_unified_blocked else None
    effective_lifecycle = unified_obs.lifecycle_state
    effective_action = unified_obs.next_action
    effective_authority = _UNIFIED_AUTHORITIES.get(effective_action, "NONE")

    return BrainSyncSnapshot(
        repository_root=str(repo_root),
        head_sha=head_sha,
        main_sha=main_sha,
        roadmap_present=roadmap_present,
        active_track=active_track,
        active_track_status=active_track_status,
        next_items=next_items_tuple,
        selected_task_id=task.task_id,
        selected_task_revision=task.revision,
        selection_mode=selection_mode,
        lifecycle_state=effective_lifecycle,
        next_action=effective_action,
        authority=effective_authority,
        unified_state=unified_obs.as_dict(),
        blocker=effective_blocker,
        last_published_task_id=last_published_task_id,
        last_published_sha=last_published_sha,
        active_run_id=unified_obs.run_id,
        active_finding_id=unified_obs.finding_id,
        active_failure_run_id=unified_obs.failed_run_id,
        run_created=False,
        executor_invoked=False,
        verification_invoked=False,
        state_mutated=False,
    )


observe_brain_sync_snapshot = observe_brain_sync

__all__ = [
    "BrainSyncError",
    "BrainSyncSnapshot",
    "observe_brain_sync",
    "observe_brain_sync_snapshot",
]
