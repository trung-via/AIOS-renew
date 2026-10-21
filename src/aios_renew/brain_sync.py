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


def _has_failure_pages(value):
    diagnostics, unified = value.get("diagnostics") or {}, value.get("unified_state") or {}
    return "failure_page" in value or any("paging" in (context or {}) for context in (
        diagnostics.get("failure_context"), diagnostics.get("attributed_failure_context"), unified.get("failure_context")))


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
    # Exact correction observations supply this; ordinary planning never does.
    correction_subject: Mapping[str, Any] | None = None
    diagnostics: Mapping[str, Any] | None = None
    failure_page: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        if self.failure_page is not None:
            from .review_transport import validate_failure_detail_view
            try:
                validate_failure_detail_view(self.failure_page)
            except (ReviewTransportError, ValueError, TypeError, KeyError) as exc:
                raise BrainSyncError("FAILURE_PAGE_INTEGRITY_INVALID") from exc
            if (self.unified_state is not None or self.diagnostics is not None
                    or self.failure_page["binding"] is not None and self.selected_task != self.failure_page["binding"]["task"]):
                raise BrainSyncError("FAILURE_SUBJECT_MISMATCH")
        if self.diagnostics is not None:
            validate_diagnostic_context(self.diagnostics, self.selected_task)
        if self.unified_state and self.unified_state.get("failure_context") is not None:
            from .review_transport import validate_failure_context, ReviewTransportError
            context = self.unified_state["failure_context"]
            try:
                validate_failure_context(context)
            except (ValueError, TypeError, KeyError, ReviewTransportError) as exc:
                raise BrainSyncError("FAILURE_CONTEXT_INTEGRITY_INVALID") from exc
            task = self.unified_state.get("task")
            if (not isinstance(task, Mapping) or type(task.get("revision")) is not int
                    or context["run_id"] != self.unified_state.get("failed_run_id")
                    or context["subject_sha"] != self.unified_state.get("failed_head_sha")
                    or context["task"] != self.unified_state.get("task")):
                raise BrainSyncError("FAILURE context does not bind Unified State")
        if self.unified_state and self.unified_state.get("operational_context") is not None:
            from .operational_receipt import validate_repair_delivery_context
            operational = self.unified_state["operational_context"]
            validate_repair_delivery_context(operational)
            attempt = operational.get("attempt")
            if attempt and (attempt["failed_run_id"] != self.unified_state.get("failed_run_id")
                    or self.unified_state.get("correction_sha") is not None and attempt["repair_sha"] != self.unified_state["correction_sha"]):
                raise BrainSyncError("DIAGNOSTIC_RUN_SUBSTITUTION")
        body = {
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
        if self.correction_subject is not None:
            body["correction_subject"] = dict(self.correction_subject)
        if self.diagnostics is not None:
            body["diagnostics"] = dict(self.diagnostics)
        if self.failure_page is not None:
            body["failure_page"] = dict(self.failure_page)
        if _has_failure_pages(body):
            body["repository"]["root"] = "protected-repository-root"
        return body

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
        failure_cause = "unavailable"
        if self.unified_state and self.unified_state.get("failure_context"):
            self.as_dict()  # Apply the same source/schema gate as portable snapshots.
            failure_cause = self.unified_state["failure_context"]["diagnostic"]["category"]
        return (
            f"PROJECT: {repo_name}\n"
            f"MAIN: {self.main_sha}\n"
            f"LAST PUBLISHED: {last_pub}\n"
            f"AUTHORED NEXT TASK: {task_id}\n"
            f"ACTIVE RUN: {run_id}\n"
            f"ACTIVE FINDING: {finding_id}\n"
            f"ACTIVE FAILURE: {failed_run_id}\n"
            f"FAILURE CAUSE: {failure_cause}\n"
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


def validate_diagnostic_context(value, task=None):
    from .review_transport import validate_failure_context
    from .operational_receipt import validate_repair_delivery_context
    if not isinstance(value, dict) or set(value) != {"failure_context", "operational_context", "attributed_failure_context", "blocker"}:
        raise BrainSyncError("DIAGNOSTIC_CONTEXT_INVALID")
    failure, operational, attributed = (value[k] for k in ("failure_context", "operational_context", "attributed_failure_context"))
    for context in (failure, attributed):
        if context is not None:
            validate_failure_context(context)
            if task is not None and context["task"] != task:
                raise BrainSyncError("DIAGNOSTIC_TASK_SUBSTITUTION")
    if operational is not None:
        validate_repair_delivery_context(operational)
        attempt = operational.get("attempt")
        if attempt and failure and attempt["failed_run_id"] != failure["run_id"]:
            raise BrainSyncError("DIAGNOSTIC_RUN_SUBSTITUTION")
        if attributed and (attempt is None or attributed["run_id"] != attempt["run_id"] or operational["run_created"] is not True):
            raise BrainSyncError("DIAGNOSTIC_ATTRIBUTION_SUBSTITUTION")
    elif attributed is not None:
        raise BrainSyncError("DIAGNOSTIC_ATTRIBUTION_SUBSTITUTION")
    blocker = value["blocker"]
    if blocker is not None and (not isinstance(blocker, dict) or set(blocker) != {"code", "action"}
            or blocker["code"] not in {"FAILURE_SOURCE_UNAVAILABLE", "FAILURE_SOURCE_INTEGRITY_INVALID",
                "FAILURE_SOURCE_MOVED", "FAILURE_SUBJECT_MISMATCH", "FAILURE_DETAIL_UNAVAILABLE", "RUNTIME_TERMINAL_UNAVAILABLE"}
            or blocker["action"] != "READ_EXACT_CANONICAL_FAILURE"):
        raise BrainSyncError("DIAGNOSTIC_BLOCKER_INVALID")


def _exact_failure_context(root, run_id, sources=None):
    from .brain_attention import GitSources, attention, FAILURE, terminal_failure_context, AttentionError
    if not isinstance(run_id, str) or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", run_id) is None:
        raise BrainSyncError("FAILURE_SUBJECT_MISMATCH")
    def read(backend):
        ref = "refs/heads/aios/failure-artifacts/" + run_id
        sha = backend.refs(ref).get(ref)
        if sha is None:
            raise BrainSyncError("FAILURE_SOURCE_UNAVAILABLE")
        item = attention(FAILURE, dict(run_id=run_id, artifact_sha=sha))
        context = terminal_failure_context(item, backend, include_paging=True)
        if context is None:
            raise BrainSyncError("FAILURE_DETAIL_UNAVAILABLE")
        # This owner-gated helper can only add a safe projection; a provider can
        # neither select a private path nor receive raw capture through Sync.
        if context.get("safe_detail", {}).get("availability") == "UNAVAILABLE":
            context = op.observe_runtime_failure_detail(root, context)
        return context
    try:
        if sources is not None:
            return read(sources)
        with op._remote_observation_repository(root) as observer:
            return read(GitSources(observer, op._git, resolve_transport_remote(observer)))
    except AttentionError as exc:
        code = "FAILURE_SOURCE_MOVED" if str(exc) == "FAILURE_SOURCE_MOVED" else (
            "FAILURE_SOURCE_UNAVAILABLE" if str(exc) == "FAILURE_SOURCE_UNAVAILABLE" else "FAILURE_SOURCE_INTEGRITY_INVALID")
        raise BrainSyncError(code) from None


def observe_brain_sync(repo: str | Path | None = None, *, task_id: str | None = None,
                       run_id: str | None = None, delivery_id: str | None = None,
                       issue_number: int | None = None, workflow_run_id: int | None = None,
                       run_attempt: int | None = None, operational_sources=None, canonical_sources=None,
                       failure_page_selector: Mapping[str, Any] | None = None) -> BrainSyncSnapshot:
    """Existing planning Sync, or an explicitly selected read-only diagnostic.

    Exact diagnostic selectors do not select work or change a planning bookmark.
    Neither user URLs/blocks nor filesystem locators are accepted.
    """
    if failure_page_selector is not None:
        if any(x is not None for x in (task_id, run_id, delivery_id, issue_number, workflow_run_id, run_attempt, operational_sources)):
            raise BrainSyncError("FAILURE_SUBJECT_MISMATCH")
        return _observe_failure_page(repo, failure_page_selector, canonical_sources)
    if all(x is None for x in (task_id, run_id, delivery_id, issue_number, workflow_run_id, run_attempt)):
        return _observe_brain_sync(repo=repo)
    root = resolve_repository(repo)
    main = _resolve_main_sha(root)
    repository = dict(root=str(root), name=_resolve_repository_name(root), main_sha=main, remote=None)
    contexts = dict(failure_context=None, operational_context=None, attributed_failure_context=None, blocker=None)
    unified, selected = None, None
    if task_id is not None:
        if not isinstance(task_id, str) or re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task_id) is None:
            raise BrainSyncError("FAILURE_SUBJECT_MISMATCH")
        task = load_task(root, task_id)
        selected = dict(id=task.task_id, revision=task.revision)
        if run_id is None:
            unified = observe_unified_state(task_id, repo=root).as_dict()
            contexts["failure_context"] = unified.get("failure_context")
            if delivery_id is None and issue_number is None and unified.get("failed_run_id") and unified.get("correction_sha"):
                delivery_id = f"repair-{unified['failed_run_id']}-{unified['correction_sha']}"
    if delivery_id is not None or issue_number is not None:
        from .operational_receipt import observe_repair_delivery, REPAIR_REPOSITORY
        if repository["name"] != REPAIR_REPOSITORY:
            raise BrainSyncError("FAILURE_SUBJECT_MISMATCH")
        contexts["operational_context"] = observe_repair_delivery(delivery_id=delivery_id, issue_number=issue_number,
            workflow_run_id=workflow_run_id, run_attempt=run_attempt, sources=operational_sources)
        attempt = contexts["operational_context"].get("attempt")
        if attempt and unified and attempt["failed_run_id"] != unified.get("failed_run_id"):
            raise BrainSyncError("FAILURE_SUBJECT_MISMATCH")
        if attempt and run_id is None and contexts["failure_context"] is None:
            run_id = attempt["failed_run_id"]
    elif workflow_run_id is not None or run_attempt is not None:
        raise BrainSyncError("FAILURE_SUBJECT_MISMATCH")
    if run_id is not None:
        try:
            failure = _exact_failure_context(root, run_id, canonical_sources)
            if selected is not None and failure["task"] != selected:
                raise BrainSyncError("FAILURE_SUBJECT_MISMATCH")
            selected, contexts["failure_context"] = failure["task"], failure
        except (BrainSyncError, ReviewTransportError, OperatorError, OSError, ValueError, TypeError, KeyError) as exc:
            code = str(exc) if str(exc) in {"FAILURE_SOURCE_UNAVAILABLE", "FAILURE_SOURCE_MOVED", "FAILURE_SUBJECT_MISMATCH", "FAILURE_DETAIL_UNAVAILABLE"} else "FAILURE_SOURCE_INTEGRITY_INVALID"
            contexts["blocker"] = dict(code=code, action="READ_EXACT_CANONICAL_FAILURE")
            if selected is not None:
                contexts["operational_context"] = None
    operational = contexts["operational_context"]
    attempt = operational.get("attempt") if operational else None
    if attempt and operational["run_created"] is True and attempt["run_id"] is not None:
        try:
            contexts["attributed_failure_context"] = _exact_failure_context(root, attempt["run_id"], canonical_sources)
        except (BrainSyncError, ReviewTransportError, OperatorError, OSError, ValueError, TypeError, KeyError):
            if contexts["blocker"] is None:
                contexts["blocker"] = dict(code="RUNTIME_TERMINAL_UNAVAILABLE", action="READ_EXACT_CANONICAL_FAILURE")
    snapshot = BrainSyncSnapshot(repository=repository, main_sha=main, roadmap={"selection": "EXACT_DIAGNOSTIC_SUBJECT"},
        selection_status="EXACT_DIAGNOSTIC_SUBJECT", lifecycle_state=unified["lifecycle_state"] if unified else "OBSERVATION",
        next_action=unified["next_action"] if unified else "NONE", authority=unified["authority"] if unified else "NONE",
        selected_task=selected, unified_state=unified, blocker=None, diagnostics=contexts)
    snapshot.as_dict()
    return snapshot


def _observe_failure_page(repo, selector, sources=None):
    """Same Git-only artifact access as attention; no local/raw fallback."""
    from .brain_attention import GitSources, terminal_failure_page
    from .review_transport import failure_page_unavailable
    root = resolve_repository(repo)
    main = _resolve_main_sha(root)
    try:
        if sources is not None:
            view = terminal_failure_page(selector, sources)
        else:
            with op._remote_observation_repository(root) as observer:
                view = terminal_failure_page(selector, GitSources(observer, op._git, resolve_transport_remote(observer)))
    except (OperatorError, ReviewTransportError, OSError, ValueError, TypeError, KeyError):
        view = failure_page_unavailable("FAILURE_SOURCE_UNAVAILABLE")
    snapshot = BrainSyncSnapshot(repository=dict(root=str(root), name=_resolve_repository_name(root), main_sha=main, remote=None),
        main_sha=main, roadmap={"selection": "EXACT_FAILURE_PAGE"}, selection_status="EXACT_DIAGNOSTIC_SUBJECT",
        lifecycle_state="OBSERVATION", next_action="NONE", authority="NONE",
        selected_task=view["binding"]["task"] if view["binding"] is not None else None, failure_page=view)
    snapshot.as_dict()
    return snapshot


def _observe_brain_sync(
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
