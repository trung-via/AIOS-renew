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
from aios_renew.unified_state import observe_unified_state


# Bounds on the read projection, never a separately persisted planning authority.
_MAX_NEXT_ITEMS = 16
_MAX_RETURN_ITEMS = 2
_ITEM_FIELDS = (
    "id", "status", "phase", "task_id", "task_revision", "task_blob_sha",
    "task_author_commit_sha", "authored_commit_sha", "parent_milestone",
    "planning_document", "objective", "return_to",
)
_PUBLICATION_FIELDS = (
    "task_id", "task_revision", "run_id", "review_id", "reviewed_sha", "published_sha",
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
    sequence = roadmap.get("sequence") or []
    if not isinstance(sequence, list):
        raise BrainSyncError("roadmap sequence must be a list")
    next_items = roadmap.get("next_items")
    if next_items is not None and not isinstance(next_items, list):
        raise BrainSyncError("roadmap next_items must be a list")

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
                    if isinstance(item, Mapping)
                    and (item.get("id") == raw or item.get("task_id") == raw)
                ]
                if matching:
                    candidates.extend(dict(item) for item in matching)
                else:
                    candidates.append({
                        "id": raw,
                        "task_id": raw if raw.startswith("TASK-") else None,
                    })
            elif isinstance(raw, Mapping):
                identity = raw.get("id") or raw.get("task_id")
                matching = [
                    item for item in sequence if isinstance(item, Mapping)
                    and identity is not None
                    and identity in (item.get("id"), item.get("task_id"))
                ]
                if not matching:
                    candidates.append(dict(raw))
                else:
                    for item in matching:
                        # Both pointers must agree; neither silently overrides the other.
                        if any(key in raw and key in item and raw[key] != item[key]
                               for key in _ITEM_FIELDS):
                            candidates.extend((dict(raw), dict(item)))
                        else:
                            candidates.append({**item, **raw})
            else:
                raise BrainSyncError("roadmap NEXT pointer must be an id or mapping")

        if seq_next:
            seq_ids = {item.get("task_id") or item.get("id") for item in seq_next}
            cand_ids = {item.get("task_id") or item.get("id") for item in candidates}
            if seq_ids != cand_ids:
                return candidates + [dict(item) for item in seq_next]
        return candidates

    return [dict(item) for item in seq_next]


def _bounded_fields(item: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    projected = {}
    for key in fields:
        if key not in item:
            continue
        value = item[key]
        limit = 2048 if key == "objective" else 256
        valid = (
            type(value) is int and 0 < value <= 2147483647
            if key == "task_revision" else isinstance(value, str)
        )
        if value is not None and not valid:
            raise BrainSyncError(f"invalid active roadmap field {key}")
        if isinstance(value, str) and len(value.encode("utf-8")) > limit:
            raise BrainSyncError(f"active roadmap field {key} exceeds {limit} bytes")
        projected[key] = value
    return projected


def _project_item(item: Mapping[str, Any]) -> dict[str, Any]:
    fields = _ITEM_FIELDS if item.get("status") != "DONE" else (
        "id", "status", "task_id", "task_revision", "task_blob_sha", "return_to",
    )
    projected = _bounded_fields(item, fields)
    for key, fields in (
        ("live_exit_gate", ("authority", "status", "contract", "published_subject_sha")),
        ("current_live_blocker", ("code", "authority", "next_action")),
        ("completed_by", _PUBLICATION_FIELDS),
        ("engineering_completion", _PUBLICATION_FIELDS),
    ):
        if key in item:
            if not isinstance(item[key], Mapping):
                raise BrainSyncError(f"invalid active roadmap {key}")
            projected[key] = _bounded_fields(item[key], fields)
    return projected


def _active_items(
    roadmap: Mapping[str, Any], candidates: list[dict[str, Any]],
) -> list[Mapping[str, Any]]:
    """Follow explicit return_to edges only, without inferring a successor."""
    sequence = roadmap.get("sequence") or []
    if len(candidates) > _MAX_NEXT_ITEMS:
        raise BrainSyncError("too many roadmap NEXT pointers")
    for candidate in candidates:
        _bounded_fields(candidate, ("id", "task_id", "task_revision"))
    if len(candidates) == 1:
        active = candidates[0]
    elif not candidates:
        gates = [item for item in sequence if isinstance(item, Mapping)
                 and item.get("status") in ("BLOCKED", "LIVE_EXIT_GATE_PENDING")]
        if len(gates) > 1:
            raise BrainSyncError("ambiguous active roadmap gate")
        if not gates:
            return []
        active = gates[0]
    else:
        return []
    _bounded_fields(active, ("id", "status", "return_to"))
    if active.get("id") is not None and sum(
        isinstance(item, Mapping) and item.get("id") == active["id"] for item in sequence
    ) > 1:
        raise BrainSyncError("ambiguous active roadmap identity")
    items = [active]
    seen = {active.get("id")}
    # A side-track NEXT can need its return gate and that gate's queued successor.
    # With the gate itself active, only its directly queued return body is needed.
    return_limit = _MAX_RETURN_ITEMS if candidates else 1
    for _ in range(return_limit):
        target = items[-1].get("return_to")
        if not target:
            break
        if not isinstance(target, str) or target in seen:
            raise BrainSyncError("invalid or cyclic active roadmap return_to")
        matches = [item for item in sequence
                   if isinstance(item, Mapping) and item.get("id") == target]
        if len(matches) != 1:
            raise BrainSyncError(f"missing or ambiguous active return_to {target[:256]!r}")
        _bounded_fields(matches[0], ("id", "status", "return_to"))
        items.append(matches[0])
        seen.add(target)
    # A boundary pointer remains an exact identifier even when its body is deferred.
    boundary = items[-1].get("return_to")
    if boundary and (
        not isinstance(boundary, str) or boundary in seen
        or sum(isinstance(item, Mapping) and item.get("id") == boundary
               for item in sequence) != 1
    ):
        raise BrainSyncError("invalid or ambiguous active roadmap boundary pointer")
    return items


def _published_pointer(item: Mapping[str, Any]) -> dict[str, Any] | None:
    pointers = [item[key] for key in ("engineering_completion", "completed_by")
                if isinstance(item.get(key), Mapping) and item[key].get("published_sha")]
    if item.get("published_sha"):
        pointers.append(item)
    gate = item.get("live_exit_gate")
    if isinstance(gate, Mapping) and gate.get("published_subject_sha"):
        pointers.append({"task_id": item.get("task_id"),
                         "published_sha": gate["published_subject_sha"]})
    if not pointers:
        return None
    for pointer in pointers:
        _bounded_fields(pointer, _PUBLICATION_FIELDS)
    if len({pointer["published_sha"] for pointer in pointers}) != 1:
        raise BrainSyncError("conflicting active roadmap publication pointers")
    for key in ("task_id", "task_revision"):
        if len({pointer[key] for pointer in pointers if pointer.get(key) is not None}) > 1:
            raise BrainSyncError(f"conflicting active publication {key}")
    for pointer in pointers:
        if pointer.get("reviewed_sha") not in (None, pointer["published_sha"]):
            raise BrainSyncError("active reviewed/published identity conflict")
        for key in ("task_id", "task_revision"):
            if (pointer.get(key) is not None and item.get(key) is not None
                    and pointer[key] != item[key]):
                raise BrainSyncError(f"active publication {key} conflict")
    return _bounded_fields(pointers[0], _PUBLICATION_FIELDS)


def _check_ancestry(root: Path, item_id: Any, published_sha: str, main_sha: str) -> bool:
    try:
        return _git_is_ancestor(root, published_sha, main_sha)
    except OperatorError as exc:
        raise BrainSyncError(
            "cannot observe Git ancestry for roadmap item "
            f"{str(item_id)[:80]!r} (published_sha={published_sha[:64]!r}, "
            f"main_sha={main_sha[:64]!r})"
        ) from exc


def _check_task_pointer(
    root: Path, item: Mapping[str, Any], main_sha: str, *, allow_missing: bool = False,
) -> None:
    """Check declared exact authoring pointers without hydrating historical RUNs."""
    task_id = item.get("task_id")
    if not task_id:
        return
    if not isinstance(task_id, str) or not re.fullmatch(r"TASK-[0-9]+", task_id):
        raise BrainSyncError("invalid active roadmap task_id")
    task_path = f".ai/tasks/{task_id}.yaml"
    # The selected candidate retains the ordinary load_task/UNAUTHORED_TASK
    # boundary below. Related gate/return pointers have no such later boundary.
    if not allow_missing:
        if not (root / task_path).is_file():
            raise BrainSyncError(f"active {task_id} canonical TASK is missing")
        task = load_task(root, task_id)
        if item.get("task_revision") not in (None, task.revision):
            raise BrainSyncError(f"active {task_id} revision conflict")
    blob = item.get("task_blob_sha")
    commits = [item[key] for key in ("task_author_commit_sha", "authored_commit_sha")
               if item.get(key)]
    if blob is None and not commits:
        return
    if blob is not None:
        if not isinstance(blob, str) or not re.fullmatch(r"[0-9a-f]{40}", blob):
            raise BrainSyncError(f"invalid active {task_id} blob pointer")
    code, current_blob, _ = _git_cmd(root, "hash-object", task_path, allow_fail=True)
    if code != 0 or (blob is not None and current_blob.strip() != blob):
        raise BrainSyncError(f"active {task_id} blob conflict")
    code, main_blob, _ = _git_cmd(root, "rev-parse", f"{main_sha}:{task_path}", allow_fail=True)
    if code != 0 or main_blob.strip() != current_blob.strip():
        raise BrainSyncError(f"active {task_id} canonical main blob conflict")
    for commit in commits:
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise BrainSyncError(f"invalid active {task_id} author commit")
        if not _check_ancestry(root, item.get("id"), commit, main_sha):
            raise BrainSyncError(f"active {task_id} author commit is not on main")
        code, authored_blob, _ = _git_cmd(root, "rev-parse", f"{commit}:{task_path}", allow_fail=True)
        if code != 0 or authored_blob.strip() != current_blob.strip():
            raise BrainSyncError(f"active {task_id} authoring identity conflict")


def observe_brain_sync(
    repo: str | Path | None = None,
    *,
    include_history: bool = False,
) -> BrainSyncSnapshot:
    """Observe active continuity; explicitly opt in to full DONE-body/ancestry hydration."""
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
    version = roadmap_data.get("version", 1)
    if type(version) is not int or not 0 < version <= 2147483647:
        raise BrainSyncError("invalid roadmap version")
    sequence = roadmap_data.get("sequence") or []
    next_items = roadmap_data.get("next_items")

    candidates = _extract_next_candidates(roadmap_data)
    active_items = _active_items(roadmap_data, candidates)
    projected_items = [_project_item(item) for item in active_items]
    next_ids = [item.get("id") or item.get("task_id") if isinstance(item, Mapping) else item
                for item in (next_items or [])]
    if len(next_ids) > _MAX_NEXT_ITEMS:
        raise BrainSyncError("too many roadmap NEXT pointers")
    for identity in next_ids:
        if not isinstance(identity, str) or len(identity.encode("utf-8")) > 256:
            raise BrainSyncError("invalid roadmap NEXT identity")
    roadmap_summary = {
        "present": True,
        "version": version,
        **_bounded_fields(roadmap_data, ("active_track", "active_track_status")),
        "next_items": next_ids,
        "sequence_count": len(sequence) if isinstance(sequence, list) else 0,
        "active_item": projected_items[0] if projected_items else None,
        "return_path": projected_items[1:],
        "return_path_truncated": bool(active_items and active_items[-1].get("return_to")),
        "publication": {"status": "UNAVAILABLE"},
        "last_published_task": None,
    }
    lineage_items = list(active_items)
    if include_history:
        history = [dict(item) for item in sequence
                   if isinstance(item, Mapping) and item.get("status") == "DONE"]
        roadmap_summary["history"] = history
        lineage_items.extend(history)

    checked = set()
    for item in lineage_items:
        pointer = _published_pointer(item)
        if pointer is None:
            continue
        published_sha = pointer["published_sha"]
        if not isinstance(published_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", published_sha):
            raise BrainSyncError("invalid roadmap published SHA")
        identity = (item.get("id"), published_sha)
        if identity not in checked and not _check_ancestry(root, item.get("id"), published_sha, main_sha):
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
        checked.add(identity)
        if any(item is active for active in active_items) and roadmap_summary["publication"]["status"] == "UNAVAILABLE":
            roadmap_summary["publication"] = {
                "status": "RELEVANT_ACTIVE_LINEAGE", "item_id": item.get("id"), **pointer,
            }
            roadmap_summary["last_published_task"] = pointer.get("task_id")

    for index, item in enumerate(active_items):
        try:
            _check_task_pointer(root, item, main_sha, allow_missing=index == 0 and bool(candidates))
        except OperatorError as exc:
            return BrainSyncSnapshot(
                repository=repo_info, main_sha=main_sha, roadmap=roadmap_summary,
                selection_status="ROADMAP_LINEAGE_CONFLICT", lifecycle_state="BLOCKED",
                next_action="NONE", authority="NONE",
                blocker={"code": "ROADMAP_LINEAGE_CONFLICT", "item_id": item.get("id"),
                         "message": str(exc)},
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
