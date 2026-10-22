"""Authoritative Brain Sync observation boundary for AIOS-renew.

Reconstructs repository/main identity, roadmap planning bookmark, selected exact TASK,
and Unified State lifecycle next_action/authority without acquiring mutation authority,
creating RUNs, invoking Executors, running verification, or mutating runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
import uuid

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


def _has_source_diagnostic(value):
    diagnostics, unified = value.get("diagnostics") or {}, value.get("unified_state") or {}
    return any("diagnostic_read" in (context or {}) for context in (
        diagnostics.get("failure_context"), diagnostics.get("attributed_failure_context"), unified.get("failure_context")))


def _has_unresolved_diagnostic(value):
    diagnostics, unified = value.get("diagnostics") or {}, value.get("unified_state") or {}
    return (diagnostics.get("blocker") or {}).get("code") == "LIMIT_EXHAUSTED" or any((context or {}).get("safe_detail", {}).get("cause") == "UNRESOLVED_ITEMS" for context in (
        diagnostics.get("failure_context"), diagnostics.get("attributed_failure_context"), unified.get("failure_context")))


def _unresolved_portable_exhausted(value):
    diagnostics, unified = value.get("diagnostics") or {}, value.get("unified_state") or {}
    contexts = (diagnostics.get("failure_context"), diagnostics.get("attributed_failure_context"), unified.get("failure_context"))
    return (sum(len((c or {}).get("safe_detail", {}).get("unresolved", {}).get("anchors", [])) for c in contexts) > 32
        or len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")) > 8192)


@dataclass(frozen=True)
class BrainSyncSnapshot:
    """Versioned, bounded, and mutation-free Brain Sync observation snapshot."""

    repository: Mapping[str, Any]
    main_sha: str | None
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
    post_task: Mapping[str, Any] | None = None
    _post_task_digest: str | None = field(default=None, repr=False, compare=False)
    _post_task_current: Any = field(default=None, repr=False, compare=False)
    _diagnostic_digest: str | None = field(default=None, repr=False, compare=False)
    _diagnostic_current: Any = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        if self._diagnostic_digest is not None and _post_digest(self.diagnostics) != self._diagnostic_digest:
            raise BrainSyncError("DIAGNOSTIC_CONTEXT_INVALID")
        if self._diagnostic_current is not None:
            code = self._diagnostic_current()
            if code is not None:
                raise BrainSyncError(code)
        if self.post_task is not None:
            if (_post_digest(dict(view=self.post_task, unified=self.unified_state, repository=self.repository)) != self._post_task_digest
                    or self.selected_task != self.post_task["task"]
                    or self.main_sha != self.post_task["main_sha"]
                    or self.roadmap != {"selection": "EXACT_POST_TASK"}
                    or any((self.run_created, self.executor_invoked, self.verification_invoked, self.state_mutated))
                    or any(x is not None for x in (self.diagnostics, self.failure_page, self.correction_subject))
                    or any(getattr(self, key) != self.post_task[key] for key in
                           ("lifecycle_state", "next_action", "authority", "blocker"))):
                raise BrainSyncError("POST_TASK_SUBSTITUTION")
            if self._post_task_current is not None:
                code = self._post_task_current()
                if code is not None:
                    return _post_unknown(code).as_dict()
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
        if self.post_task is not None:
            body["post_task"] = json.loads(_post_encode(self.post_task))
            body["repository"] = dict(self.repository, root="protected-repository-root")
            if len(_post_encode(body).encode("utf-8")) > 8192:
                return _post_unknown("POST_TASK_LIMIT_EXHAUSTED").as_dict()
        if _has_failure_pages(body) or _has_unresolved_diagnostic(body) or _has_source_diagnostic(body):
            body["repository"]["root"] = "protected-repository-root"
        if _has_source_diagnostic(body):
            body["repository"].pop("remote_url", None)
            body["repository"]["remote"] = None
            if len(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")) > 64 * 1024:
                raise BrainSyncError("LIMIT_EXHAUSTED")
        if _has_unresolved_diagnostic(body):
            body["repository"].pop("remote_url", None)
        if _has_unresolved_diagnostic(body) and _unresolved_portable_exhausted(body):
            raise BrainSyncError("LIMIT_EXHAUSTED")
        return body

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

    def checkpoint(self) -> str:
        """Render the compact human-readable SYNC CHECKPOINT."""
        if self.post_task is not None:
            view = self.as_dict()["post_task"]
            task = view["task"]
            return (f"TASK: {task['id'] if task else 'UNKNOWN'}\nMAIN: {view['main_sha'] or 'UNKNOWN'}\n"
                    f"RUN: {view['run_id'] or 'none'}\nSTATE: {view['lifecycle_state']}\nNEXT: {view['next_action']}")
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
                "FAILURE_SOURCE_MOVED", "FAILURE_SUBJECT_MISMATCH", "FAILURE_DETAIL_UNAVAILABLE", "RUNTIME_TERMINAL_UNAVAILABLE", "LIMIT_EXHAUSTED"}
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
        if (context.get("safe_detail", {}).get("availability") == "UNAVAILABLE"
                and ("diagnostic_read" not in context or getattr(backend, "runtime_repo", None) is None)
                or context.get("safe_detail", {}).get("cause") == "UNRESOLVED_ITEMS"
                and getattr(backend, "runtime_repo", None) is None):
            context = op.observe_runtime_failure_detail(root, context)
        return context
    try:
        if sources is not None:
            return read(sources)
        with op._remote_observation_repository(root) as observer:
            return read(GitSources(observer, op._failure_source_git, resolve_transport_remote(observer), runtime_repo=root))
    except AttentionError as exc:
        code = "FAILURE_SOURCE_MOVED" if str(exc) == "FAILURE_SOURCE_MOVED" else (
            "FAILURE_SOURCE_UNAVAILABLE" if str(exc) == "FAILURE_SOURCE_UNAVAILABLE" else "FAILURE_SOURCE_INTEGRITY_INVALID")
        raise BrainSyncError(code) from None


def _diagnostic_current_guard(root, contexts, sources):
    """Request-local receipt, never a cache, portable capability or truth store.

    Reuse the SAME authorized reader when a protected projection is serialized
    later. A snapshot cannot replay private-derived fields after refs, origin,
    retention or reader authority have changed.
    """
    pinned = [(context["run_id"], _post_digest(context["diagnostic_read"]))
        for key in ("failure_context", "attributed_failure_context")
        if (context := contexts.get(key)) is not None
        and context.get("diagnostic_read", {}).get("authority") is not None]
    if not pinned:
        return None
    def current():
        for run_id, expected in pinned:
            try:
                context = _exact_failure_context(root, run_id, sources)
                if _post_digest(context.get("diagnostic_read")) != expected:
                    return "FAILURE_DETAIL_UNAVAILABLE"
            except (BrainSyncError, ReviewTransportError, OperatorError, OSError, ValueError, TypeError, KeyError):
                return "FAILURE_SOURCE_MOVED"
        return None
    return current


def observe_brain_sync(repo: str | Path | None = None, *, task_id: str | None = None,
                       run_id: str | None = None, delivery_id: str | None = None,
                       issue_number: int | None = None, workflow_run_id: int | None = None,
                       run_attempt: int | None = None, operational_sources=None, canonical_sources=None,
                       failure_page_selector: Mapping[str, Any] | None = None,
                       post_task_selector: Mapping[str, Any] | None = None) -> BrainSyncSnapshot:
    """Existing planning Sync, or an explicitly selected read-only diagnostic.

    Exact diagnostic selectors do not select work or change a planning bookmark.
    Neither user URLs/blocks nor filesystem locators are accepted.
    post_task_selector is exclusively {task_id: current canonical TASK} or
    {wake_selector: exact terminal event ID / attention envelope}. It joins the
    existing TASK reduction and never changes the legacy diagnostic RUN API.
    """
    if post_task_selector is not None:
        if any(x is not None for x in (task_id, run_id, delivery_id, issue_number, workflow_run_id,
                                      run_attempt, operational_sources, canonical_sources, failure_page_selector)):
            return _post_unknown("POST_TASK_SELECTOR_INVALID")
        return _observe_post_task(repo, post_task_selector)
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
            code = str(exc) if str(exc) in {"FAILURE_SOURCE_UNAVAILABLE", "FAILURE_SOURCE_MOVED", "FAILURE_SUBJECT_MISMATCH", "FAILURE_DETAIL_UNAVAILABLE", "LIMIT_EXHAUSTED"} else "FAILURE_SOURCE_INTEGRITY_INVALID"
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
        selected_task=selected, unified_state=unified, blocker=None, diagnostics=contexts,
        _diagnostic_digest=_post_digest(contexts),
        _diagnostic_current=_diagnostic_current_guard(root, contexts, canonical_sources))
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


def _post_encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _post_digest(value):
    return hashlib.sha256(_post_encode(value).encode("ascii")).hexdigest()


def _post_snapshot(body, *, unified=None, current=None, root="protected-repository-root"):
    """Adapt a presentation of the existing reduction to existing consumers."""
    body = json.loads(_post_encode(body))
    repository = dict(root=root, name=body["repository_name"] or "git:UNKNOWN",
                      main_sha=body["main_sha"], remote=None)
    return BrainSyncSnapshot(
        repository=repository,
        main_sha=body["main_sha"], roadmap={"selection": "EXACT_POST_TASK"},
        selection_status="EXACT_POST_TASK", selected_task=body["task"], unified_state=unified,
        lifecycle_state=body["lifecycle_state"], next_action=body["next_action"],
        authority=body["authority"], blocker=body["blocker"], post_task=body,
        _post_task_digest=_post_digest(dict(view=body, unified=unified, repository=repository)), _post_task_current=current,
    )


def _post_body(code=None):
    return dict(
        format="AIOS_POST_TASK_STATE", version=1, kind="POST_TASK_STATE",
        selection=None, repository_name=None, task=None, main_sha=None, run_id=None,
        lifecycle_state="BLOCKED", next_action="NONE", authority="NONE",
        blocker=dict(code=code) if code else None,
        terminal=dict(status="UNKNOWN", reason=code or "TERMINAL_UNPROVEN", head_sha=None,
                      candidate_sha=None, artifact_sha=None, artifact_ref=None),
        review=dict(status="UNKNOWN", reason="REVIEW_UNPROVEN", review_id=None,
                    decision_sha=None, reviewed_sha=None),
        publication=dict(status="UNKNOWN", reason="PUBLICATION_UNPROVEN", published_source_sha=None),
        next_source=None,
        witness=dict(request_id=uuid.uuid4().hex, currentness="UNAVAILABLE", repository_id=None, source_digest=None),
        run_created=False, executor_invoked=False, verification_invoked=False, state_mutated=False,
    )


def _post_unknown(code):
    return _post_snapshot(_post_body(code))


def _post_patterns(task_id):
    from .review_transport import task_run_prefix
    prefix = task_run_prefix(task_id)
    return ("refs/heads/main", *(f"refs/heads/aios/{name}/{prefix}*" for name in (
        "failure", "failure-artifacts", "artifacts", "review", "review-decision",
        "publication-recovery", "remediation", "repair")),
        f"refs/heads/aios/repair-supersession/{prefix}*/*")


def _post_transport(root):
    code, _, _ = _git_cmd(root, "symbolic-ref", "--quiet", "HEAD", allow_fail=True)
    remote = resolve_transport_remote(root) if code == 0 else resolve_detached_observation_remote(root)
    url = str(root) if remote == "." else op._git(root, "remote", "get-url", remote)
    return remote, url


def _post_repository_name(url):
    """Retain canonical network repository identity without URL credentials.

    Local Git transports have an opaque identity; no local folder is portable.
    """
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    path = parsed.path if parsed.scheme in {"https", "http", "ssh", "git"} and parsed.hostname else None
    if path is None:
        scp = re.fullmatch(r"[A-Za-z0-9_-]+@[A-Za-z0-9.-]+:(.+)", url)
        path = scp[1] if scp else ""
    match = re.fullmatch(r"/?([A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,96}?)(?:\.git)?/?", path)
    return match[1] if match and not parsed.query and not parsed.fragment else "git:" + _post_digest(url)


def _post_sources(path, remote, frozen=None):
    from .brain_attention import GitSources, AttentionError
    from .terminal_attention import _load_yaml
    from .review_transport import PUBLIC_ARTIFACT_MAX_BYTES, PUBLIC_FAILURE_MAX_BYTES

    class Sources(GitSources):
        def refs(self, *patterns):
            from fnmatch import fnmatchcase
            values = (super().refs(*patterns) if frozen is None else
                      {ref: sha for ref, sha in frozen.items() if any(fnmatchcase(ref, p) for p in patterns)})
            if (len(values) > 64 or any(len(ref) > 256 or re.fullmatch(
                    r"refs/heads/(?:main|aios/[A-Za-z0-9_./-]+)", ref) is None for ref in values)):
                raise BrainSyncError("POST_TASK_LIMIT_EXHAUSTED")
            return values

        def fetch(self, sha):
            if frozen is None:
                return super().fetch(sha)
            # Only snapshot tips are acquired. TASK/base ancestors must already
            # exist in that exact Git ancestry, never at a caller-selected URL.
            self.git(self.path, "cat-file", "-e", sha + "^{commit}")

        def document(self, sha, name):
            limits = {".ai/transport/run.json": PUBLIC_ARTIFACT_MAX_BYTES,
                      ".ai/transport/result.json": PUBLIC_ARTIFACT_MAX_BYTES,
                      ".ai/transport/failure.json": PUBLIC_FAILURE_MAX_BYTES,
                      ".ai/transport/repair.json": PUBLIC_ARTIFACT_MAX_BYTES}
            limit = limits.get(name)
            if re.fullmatch(r"\.ai/(?:tasks/TASK-[A-Za-z0-9_-]{1,90}|reviews/REVIEW-[A-Za-z0-9._-]{1,96})\.ya?ml", name):
                limit = 262144
            if limit is None:
                raise AttentionError("UNPROVEN_SOURCE_DOCUMENT")
            self.fetch(sha)
            entry = self.git(self.path, "ls-tree", "-l", sha, "--", name)
            match = re.fullmatch(r"100644 blob [0-9a-f]{40}\s+(\d+)\t" + re.escape(name), entry)
            if match is None:
                raise BrainSyncError("POST_TASK_SOURCE_INVALID")
            if not 0 < int(match[1]) <= limit:
                raise BrainSyncError("POST_TASK_LIMIT_EXHAUSTED")
            return _load_yaml(self.git(self.path, "show", sha + ":" + name), "Post-TASK identity")

    source = Sources(path, op._git, remote)
    if frozen is not None:
        source.acquire(*frozen.values())
    return source


def _post_local_mark(root, task_id):
    """Currentness guard only; Runtime/Unified State still authenticate RUNs.

    Capture bounded RUN bytes and terminal metadata, never private log bytes.
    Nothing from these local filenames or stat records is exported.
    """
    from .review_transport import task_run_prefix
    state = op._runtime_paths_readonly(root)
    rows = []
    for directory in (state.runs, state.results, state.failures, state.repairs, state.preverification):
        for path in directory.glob(task_run_prefix(task_id) + "*"):
            if len(rows) >= 64:
                raise BrainSyncError("POST_TASK_LIMIT_EXHAUSTED")
            if path.is_symlink() or not path.is_file():
                raise BrainSyncError("POST_TASK_LOCAL_SOURCE_INVALID")
            info = path.stat()
            digest = None
            if directory == state.runs:
                if info.st_size > 65536:
                    raise BrainSyncError("POST_TASK_LIMIT_EXHAUSTED")
                with path.open("rb") as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise BrainSyncError("POST_TASK_LIMIT_EXHAUSTED")
                digest = hashlib.sha256(raw).hexdigest()
            rows.append((directory.name, path.name, info.st_size, info.st_mtime_ns, digest))
    return _post_digest(sorted(rows))


def _observe_post_task(repo, selector):
    """One bounded ref-guarded view, never another lifecycle reduction.

    TASK selectors choose current canonical main. Terminal wake SHA values are
    assertions to authenticate against exact refs, never source authority.
    Legacy planning and diagnostic selectors retain their original semantics.
    """
    from .brain_attention import (AttentionError, parse_event_id, parse_body, resolve_return_affinity,
                                  RESULT, FAILURE, _review_patterns)
    from .task import validate_task

    try:
        if (not isinstance(selector, Mapping) or set(selector) not in ({"task_id"}, {"wake_selector"})
                or len(_post_encode(dict(selector)).encode("ascii")) > 8192):
            return _post_unknown("POST_TASK_SELECTOR_INVALID")
        item = None
        if "task_id" in selector:
            task_id = selector["task_id"]
            if not isinstance(task_id, str) or re.fullmatch(r"TASK-[A-Za-z0-9_-]{1,90}", task_id) is None:
                return _post_unknown("POST_TASK_SELECTOR_INVALID")
        else:
            wake = selector["wake_selector"]
            try:
                item = parse_event_id(wake) if isinstance(wake, str) else parse_body(_post_encode(wake))
            except (AttentionError, TypeError, ValueError, RecursionError):
                return _post_unknown("POST_TASK_SELECTOR_INVALID")
            if item.family not in {RESULT, FAILURE}:
                return _post_unknown("POST_TASK_WAKE_UNSUPPORTED")
            task_id = None
        root = resolve_repository(repo)
        transport = _post_transport(root)
        with op._remote_observation_repository(root) as observer:
            remote = resolve_transport_remote(observer)
            live = _post_sources(observer, remote)
            wake_refs = None
            if item is not None:
                wake_refs = live.refs("refs/heads/main", *_review_patterns(item.selectors["run_id"]))
                proof = _post_sources(observer, remote, wake_refs)
                if resolve_return_affinity(item.event_id, proof) is None:
                    return _post_unknown("POST_TASK_TERMINAL_UNPROVEN")
                run_doc = proof.document(item.selectors["artifact_sha"], ".ai/transport/run.json")
                run_doc = run_doc["execution"]["run"] if run_doc.get("kind") == "REMEDIATION" else run_doc
                task_id = run_doc["task"]["id"]
                if re.fullmatch(r"TASK-[A-Za-z0-9_-]{1,90}", task_id) is None:
                    return _post_unknown("POST_TASK_SOURCE_INVALID")
            patterns = _post_patterns(task_id)
            before = live.refs(*patterns)
            if wake_refs is not None and any(before.get(ref) != sha for ref, sha in wake_refs.items()):
                return _post_unknown("POST_TASK_SOURCE_MOVED")
            for ref in before:
                namespace, _, run = ref.removeprefix("refs/heads/aios/").partition("/")
                required = {"review": "artifacts", "review-decision": "artifacts", "failure": "failure-artifacts"}.get(namespace)
                if required and f"refs/heads/aios/{required}/{run}" not in before:
                    return _post_unknown("POST_TASK_SOURCE_INCOMPLETE")
            main = before.get("refs/heads/main")
            if main is None:
                return _post_unknown("POST_TASK_MAIN_UNAVAILABLE")
            proof = _post_sources(observer, remote, before)
            task = validate_task(proof.document(main, f".ai/tasks/{task_id}.yaml"))
            identity = dict(id=task.task_id, revision=task.revision)
            if task.task_id != task_id or load_task(root, task_id) != task:
                return _post_unknown("POST_TASK_TASK_SOURCE_MISMATCH")
            if item is not None and run_doc["task"] != identity:
                return _post_unknown("POST_TASK_TASK_REVISION_MISMATCH")
            local = _post_local_mark(root, task_id)
            # This is the existing public observation and the only lifecycle
            # owner. No status or next action is reconstructed from wake/carrier.
            sync = observe_brain_sync(repo=root, task_id=task_id)
            unified = sync.as_dict()["unified_state"]
            if sync.main_sha != main or sync.selected_task != identity or unified["task"] != identity:
                return _post_unknown("POST_TASK_SOURCE_MOVED")
            for key in ("run_id", "source_run_id", "failed_run_id", "review_id", "finding_id"):
                value = unified.get(key)
                if value is not None and (not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) is None):
                    return _post_unknown("POST_TASK_SOURCE_INVALID")
            for key in ("candidate_sha", "reviewed_sha", "failed_head_sha", "correction_sha"):
                value = unified.get(key)
                if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None):
                    return _post_unknown("POST_TASK_SOURCE_INVALID")
            run_id = unified["run_id"]
            if item is not None and run_id != item.selectors["run_id"]:
                return _post_unknown("POST_TASK_WAKE_STALE")
            body = _post_body()
            body.update(selection=dict(wake_selector=item.event_id) if item else dict(task_id=task_id),
                        task=identity, main_sha=main, run_id=run_id,
                        lifecycle_state=sync.lifecycle_state, next_action=sync.next_action, authority=sync.authority)
            blocker = unified["blocker"]
            if blocker is not None:
                code = blocker.get("code")
                body["blocker"] = dict(code=code if isinstance(code, str) and re.fullmatch(
                    r"[A-Z][A-Z0-9_]{0,95}", code) else "POST_TASK_STATE_BLOCKED")
            if run_id is None:
                if unified["next_action"] == "EXECUTE_PRIMARY":
                    body["terminal"].update(status="FRESH_TASK", reason="NO_ADMITTED_RUN")
            else:
                from .brain_attention import attention
                success = before.get("refs/heads/aios/artifacts/" + run_id)
                failure = before.get("refs/heads/aios/failure-artifacts/" + run_id)
                if (success and (failure or "refs/heads/aios/failure/" + run_id in before)
                        or failure and any("refs/heads/aios/" + name + "/" + run_id in before
                                           for name in ("artifacts", "review", "review-decision"))):
                    return _post_unknown("POST_TASK_TERMINAL_CONFLICT")
                artifact = success or failure
                if artifact:
                    terminal_item = attention(RESULT if success else FAILURE, dict(run_id=run_id, artifact_sha=artifact))
                    affinity = resolve_return_affinity(terminal_item.event_id, proof)
                    if affinity is None or affinity != task.return_affinity:
                        return _post_unknown("POST_TASK_TERMINAL_UNPROVEN")
                    terminal_run = proof.document(artifact, ".ai/transport/run.json")
                    terminal_run = terminal_run["execution"]["run"] if terminal_run.get("kind") == "REMEDIATION" else terminal_run
                    if terminal_run["task"] != identity:
                        return _post_unknown("POST_TASK_TASK_REVISION_MISMATCH")
                    if item is not None and terminal_item.event_id != item.event_id:
                        return _post_unknown("POST_TASK_WAKE_STALE")
                    candidate = before.get("refs/heads/aios/" + ("review/" if success else "failure/") + run_id)
                    head = unified["candidate_sha"] if success else unified["failed_head_sha"]
                    if head is None or candidate is not None and candidate != head:
                        return _post_unknown("POST_TASK_HEAD_MISMATCH")
                    ref = "refs/heads/aios/" + ("artifacts/" if success else "failure-artifacts/") + run_id
                    body["terminal"].update(status="RESULT" if success else "FAILURE", head_sha=head,
                        candidate_sha=candidate, artifact_sha=artifact, artifact_ref=ref,
                        reason=None if candidate else "CANDIDATE_REF_ABSENT")
                    if failure:
                        body["review"].update(status="NOT_APPLICABLE", reason="FAILURE_HAS_NO_REVIEW")
                        body["next_source"] = dict(reader="terminal_failure_context", event_id=terminal_item.event_id,
                            task=identity, run_id=run_id, head_sha=head, ref=ref, sha=artifact,
                            reason="FAILURE_DIAGNOSIS_REQUIRED")
                    else:
                        decision = before.get("refs/heads/aios/review-decision/" + run_id)
                        if decision:
                            review_identity, review = proof.review(run_id, decision)
                            if review_identity["reviewed_sha"] != head or unified["reviewed_sha"] != head:
                                return _post_unknown("POST_TASK_REVIEW_MISMATCH")
                            body["review"].update(status=review["verdict"], reason=None,
                                review_id=review["review_id"], decision_sha=decision, reviewed_sha=head)
                            if review["verdict"] == "PASS":
                                included = proof.included(head, main)
                                if included != (unified["lifecycle_state"] == "DONE"):
                                    return _post_unknown("POST_TASK_PUBLICATION_MISMATCH")
                                body["publication"].update(status="INCLUDED" if included else "NOT_INCLUDED",
                                    reason=None if included else "REVIEWED_SOURCE_NOT_IN_MAIN",
                                    published_source_sha=head if included else None)
                        elif unified["next_action"] == "SEMANTIC_REVIEW":
                            body["review"].update(status="PENDING", reason="INDEPENDENT_REVIEW_ABSENT")
                elif unified["next_action"] == "WAIT":
                    body["terminal"].update(status="ACTIVE", reason="ADMITTED_RUN_WITHOUT_TERMINAL")
            repository_id = _post_digest(transport[1])
            body["repository_name"] = _post_repository_name(transport[1])
            body["witness"].update(currentness="CHECKED_DURING_REQUEST", repository_id=repository_id,
                source_digest=_post_digest(dict(refs=before, task=identity, local=local)))

            def current():
                try:
                    if (_post_transport(root) != transport
                            or _post_sources(root, transport[0]).refs(*patterns) != before
                            or _post_local_mark(root, task_id) != local or load_task(root, task_id) != task):
                        return "POST_TASK_SOURCE_MOVED"
                except Exception:
                    return "POST_TASK_SOURCE_UNAVAILABLE"
                return None

            code = current()
            if code:
                return _post_unknown(code)
            # Keep the existing consumer seam, but only copy bounded structural
            # facts. No diagnostic prose, private paths, raw captures or URLs.
            portable = {key: unified[key] for key in (
                "format", "version", "kind", "task", "lifecycle_state", "next_action", "authority",
                "run_id", "source_run_id", "failed_run_id", "review_id", "finding_id", "candidate_sha",
                "reviewed_sha", "failed_head_sha", "correction_sha", "execution_base", "outstanding_findings",
                "run_created", "executor_invoked",
                "verification_invoked", "state_mutated")}
            portable["blocker"] = body["blocker"]
            for key in ("batch_id", "batch_selection"):
                if key in unified:
                    portable[key] = unified[key]
            return _post_snapshot(body, unified=portable, current=current, root=str(root))
    except BrainSyncError as exc:
        code = str(exc)
        return _post_unknown(code if code in {
            "POST_TASK_LIMIT_EXHAUSTED", "POST_TASK_SOURCE_INVALID", "POST_TASK_LOCAL_SOURCE_INVALID"
        } else "POST_TASK_SOURCE_UNAVAILABLE")
    except Exception:
        # Source/codec errors are typed and closed; Git stderr, paths and user
        # selectors never become portable blocker messages or fallback truth.
        return _post_unknown("POST_TASK_SOURCE_UNAVAILABLE")


build_brain_sync_snapshot = observe_brain_sync

__all__ = [
    "BrainSyncError",
    "BrainSyncSnapshot",
    "build_brain_sync_snapshot",
    "observe_brain_sync",
]
