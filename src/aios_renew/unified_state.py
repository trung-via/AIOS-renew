"""Authoritative Unified State + Next Action reduction boundary for AIOS-renew.

Reduces the exact current TASK lifecycle and computes next action without acquiring
mutation authority, creating RUNs, invoking Executors, running verification,
mutating runtime state, or reconciling repository state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import aios_renew.publication as publication_module
from .artifacts import (
    ArtifactValidationError,
    ResultPackage,
    validate_evidence,
    validate_result,
    validate_result_package,
)
from .correction_preflight import (
    CorrectionPreflightResult,
    preflight_remediation,
    preflight_repair,
)
from .correction_frontier import CorrectionFrontier, CorrectionFrontierError
from .review import (
    Review,
    ReviewValidationError,
    parse_review,
    validate_review,
)
from .review_transport import (
    RemoteLifecycleReview,
    RemoteLifecycleTerminal,
    RemoteQueryError,
    RemoteTaskLifecycle,
    ReviewTransportError,
    resolve_remote_primary_recovery,
    resolve_remote_task_lifecycle,
    resolve_transport_remote,
    task_run_prefix,
    validate_runtime_failure_binding,
)
from .run import Run
from .task import Task


_UNIFIED_NEXT_ACTIONS = frozenset({
    "EXECUTE_PRIMARY", "WAIT", "SEMANTIC_REVIEW", "AUTHOR_REMEDIATION",
    "EXECUTE_REMEDIATION", "AUTHOR_REPAIR", "EXECUTE_REPAIR",
    "RETRY_TRANSPORT", "RECOVER_PRIMARY", "PUBLICATION", "DONE", "NONE",
})

_UNIFIED_AUTHORITIES = {
    "EXECUTE_PRIMARY": "HUMAN_RUNTIME",
    "WAIT": "NONE",
    "SEMANTIC_REVIEW": "REVIEWER",
    "AUTHOR_REMEDIATION": "BRAIN",
    "EXECUTE_REMEDIATION": "HUMAN_RUNTIME",
    "AUTHOR_REPAIR": "BRAIN",
    "EXECUTE_REPAIR": "HUMAN_RUNTIME",
    "RETRY_TRANSPORT": "HUMAN_RUNTIME",
    "RECOVER_PRIMARY": "HUMAN_RUNTIME",
    "PUBLICATION": "PUBLICATION",
    "DONE": "NONE",
    "NONE": "NONE",
}

_OUTSTANDING_FINDING_LIMIT = 32


@dataclass(frozen=True)
class UnifiedStateObservation:
    """Versioned, bounded and mutation-free lifecycle reduction."""

    task_id: str
    task_revision: int
    lifecycle_state: str
    next_action: str
    run_id: str | None = None
    source_run_id: str | None = None
    failed_run_id: str | None = None
    review_id: str | None = None
    finding_id: str | None = None
    candidate_sha: str | None = None
    reviewed_sha: str | None = None
    failed_head_sha: str | None = None
    correction_sha: str | None = None
    execution_base_run_id: str | None = None
    execution_base_sha: str | None = None
    correction: Mapping[str, Any] | None = None
    # Exact repair content is retained only for the immediate admission handoff.
    # It is deliberately excluded from the Human/state renderings.
    correction_document: Mapping[str, Any] | None = None
    blocker: Mapping[str, Any] | None = None
    outstanding_findings: tuple[Mapping[str, str], ...] = ()
    admission_failures: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        if self.next_action not in _UNIFIED_NEXT_ACTIONS:
            raise ValueError("invalid Unified State next action")
        return {
            "format": "AIOS_UNIFIED_STATE",
            "version": 1,
            "kind": "UNIFIED_STATE",
            "task": {"id": self.task_id, "revision": self.task_revision},
            "lifecycle_state": self.lifecycle_state,
            "next_action": self.next_action,
            "authority": _UNIFIED_AUTHORITIES[self.next_action],
            "run_id": self.run_id,
            "source_run_id": self.source_run_id,
            "failed_run_id": self.failed_run_id,
            "review_id": self.review_id,
            "finding_id": self.finding_id,
            "candidate_sha": self.candidate_sha,
            "reviewed_sha": self.reviewed_sha,
            "failed_head_sha": self.failed_head_sha,
            "correction_sha": self.correction_sha,
            "execution_base": (
                {
                    "run_id": self.execution_base_run_id,
                    "candidate_sha": self.execution_base_sha,
                }
                if self.execution_base_run_id is not None
                and self.execution_base_sha is not None
                else None
            ),
            "correction_preflight": (
                dict(self.correction) if self.correction is not None else None
            ),
            "outstanding_findings": [dict(item) for item in self.outstanding_findings],
            "blocker": dict(self.blocker) if self.blocker is not None else None,
            "admission_failures": [dict(item) for item in self.admission_failures],
            "run_created": False,
            "executor_invoked": False,
            "verification_invoked": False,
            "state_mutated": False,
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def _operator():
    import aios_renew.operator as op

    return op


@dataclass(frozen=True)
class _LifecycleRun:
    run_id: str
    family: str
    run: Run
    candidate_sha: str
    terminal_kind: str
    terminal: Mapping[str, Any]
    parent_run_id: str | None = None
    review_id: str | None = None
    finding_id: str | None = None
    candidate_available: bool = True
    correction: Mapping[str, Any] | None = None
    run_document: Mapping[str, Any] | None = None
    semantic_predecessor: Any | None = None
    execution_base_run_id: str | None = None


def _unified_blocked(
    task: Task,
    code: str,
    *,
    admission_failures: tuple[Mapping[str, Any], ...],
    phase: str | None = None,
    reason_code: str | None = None,
    blocker_details: Mapping[str, Any] | None = None,
    **facts: Any,
) -> UnifiedStateObservation:
    blocker: dict[str, Any] = {"code": code}
    if blocker_details is not None:
        blocker.update(blocker_details)
    if phase is not None:
        blocker["phase"] = phase
    if reason_code is not None:
        blocker["reason_code"] = reason_code
    return UnifiedStateObservation(
        task.task_id,
        task.revision,
        "BLOCKED",
        "NONE",
        blocker=blocker,
        admission_failures=admission_failures,
        **facts,
    )


def _bounded_admission_context(
    state: Any, task: Task
) -> tuple[Mapping[str, Any], ...]:
    """Return structural v2 context without interpreting historical prose."""

    contexts: set[tuple[str, str, str]] = set()
    if not state.admission_failures.is_dir():
        return ()
    for path in state.admission_failures.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping):
            continue
        bound = value.get("task")
        requested = value.get("requested_task_id")
        exact = (
            isinstance(bound, Mapping)
            and bound.get("id") == task.task_id
            and bound.get("revision") == task.revision
        ) or (bound is None and requested == task.task_id)
        identity = (value.get("operation"), value.get("phase"), value.get("reason_code"))
        if (
            value.get("format") == "AIOS_ADMISSION_FAILURE"
            and value.get("version") == 2
            and exact
            and all(isinstance(item, str) and item for item in identity)
        ):
            contexts.add(identity)  # type: ignore[arg-type]
    return tuple(
        {"operation": operation, "phase": phase, "reason_code": reason}
        for operation, phase, reason in sorted(contexts)[:32]
    )


def _decode_lifecycle_run(
    raw: bytes, *, run_id: str
) -> tuple[Run, str, str | None, str | None]:
    value = json.loads(raw.decode("utf-8", errors="strict"))
    if not isinstance(value, Mapping):
        raise ValueError("RUN must be a mapping")
    op = _operator()
    if value.get("kind") == "REMEDIATION":
        execution = op._remediation_execution_from_data(value.get("execution"))
        predecessor = None
        execution_base = None
        if "predecessor" in value:
            predecessor = op._parse_remediation_predecessor(value["predecessor"])
            if (
                predecessor.review_id != execution.review_id
                or predecessor.finding_id != execution.finding.id
                or predecessor.reviewed_sha != execution.remediation.reviewed_sha
            ):
                raise ValueError("REMEDIATION predecessor identity mismatch")
        if "execution_base" in value:
            if predecessor is None:
                raise ValueError("REMEDIATION execution_base requires predecessor")
            execution_base = op._parse_remediation_execution_base(value["execution_base"])
            if execution_base.candidate_sha != execution.run.base_sha:
                raise ValueError("REMEDIATION execution_base does not match RUN base")
        elif execution.run.base_sha != execution.remediation.reviewed_sha:
            raise ValueError("legacy REMEDIATION RUN base does not match reviewed SHA")
        return (
            execution.run,
            "REMEDIATION",
            execution.review_id,
            execution.finding.id,
        )
    if "kind" in value:
        raise ValueError("unknown RUN kind")
    run = op._run_from_data(value)
    return run, "PRIMARY", None, None


def _validate_lifecycle_result(
    *,
    task: Task,
    run: Run,
    family: str,
    run_document: Mapping[str, Any],
    terminal: Mapping[str, Any],
    candidate_sha: str,
) -> ResultPackage:
    """Validate one canonical/local ResultPackage before lifecycle reduction."""

    result = validate_result(terminal["result"])
    evidence_data = terminal["evidence"]
    if not isinstance(evidence_data, list):
        raise TypeError("ResultPackage evidence must be a list")
    evidence = tuple(validate_evidence(item) for item in evidence_data)
    package = ResultPackage(result=result, evidence=evidence)
    if family == "REMEDIATION":
        op = _operator()
        execution = op._remediation_execution_from_data(run_document["execution"])
        op._require_remediation_package_contract(
            execution, package, actual_head=result.head_sha
        )
    else:
        validate_result_package(
            task=task, run=run, result=result, evidence=evidence
        )
    if result.head_sha != candidate_sha:
        raise ValueError("RESULT head does not match candidate ref")
    return package


def _validate_lifecycle_failure(
    *,
    repo: Path,
    task: Task,
    run: Run,
    terminal: Mapping[str, Any],
    candidate_sha: str,
    candidate_available: bool,
    continuation_of: str | None,
) -> None:
    """Bind one FAILURE to Runtime's exact RUN and candidate facts."""

    op = _operator()
    actual_descends = None
    actual_changed = None
    if candidate_available:
        try:
            actual_descends = op._git_is_ancestor(repo, run.base_sha, candidate_sha)
            actual_changed = (
                op._committed_changed_files(repo, run.base_sha, candidate_sha)
                if actual_descends
                else set()
            )
        except op.OperatorError as exc:
            raise ValueError("FAILURE candidate Git binding is invalid") from exc
    validate_runtime_failure_binding(
        terminal,
        run_id=run.run_id,
        task_id=task.task_id,
        task_revision=task.revision,
        executor=run.executor,
        base_sha=run.base_sha,
        candidate_sha=candidate_sha,
        modification_scope=task.scope.modify,
        actual_descends_from_base=actual_descends,
        actual_changed_files=actual_changed,
    )
    if terminal.get("continuation_of") != continuation_of:
        raise ValueError("FAILURE continuation does not match RUN family")


def _decode_remote_lifecycle(
    repo: Path, task: Task, lifecycle: RemoteTaskLifecycle
) -> tuple[list[_LifecycleRun], dict[str, Review]]:
    reviews: dict[str, Review] = {}
    for item in lifecycle.reviews:
        if item.run_id in reviews:
            raise ValueError("competing semantic review decisions")
        reviews[item.run_id] = parse_review(
            item.review.decode("utf-8", errors="strict")
        )
    decoded: list[_LifecycleRun] = []
    for item in lifecycle.terminals:
        run_document = json.loads(item.run.decode("utf-8", errors="strict"))
        if not isinstance(run_document, Mapping):
            raise ValueError("RUN must be a mapping")
        run, family, review_id, finding_id = _decode_lifecycle_run(
            item.run, run_id=item.run_id
        )
        semantic_predecessor = None
        execution_base_run_id = None
        if family == "REMEDIATION" and "predecessor" in run_document:
            semantic_predecessor = op._parse_remediation_predecessor(
                run_document["predecessor"]
            )
            if "execution_base" in run_document:
                execution_base_run_id = op._parse_remediation_execution_base(
                    run_document["execution_base"]
                ).run_id
            else:
                execution_base_run_id = semantic_predecessor.source_run_id
        if (
            run.run_id != item.run_id
            or run.task.id != task.task_id
            or run.task.revision != task.revision
            or run.status != "ACTIVE"
        ):
            raise ValueError("terminal RUN does not bind the current TASK revision")
        parent_run_id = None
        correction = None
        if item.correction is not None:
            correction = json.loads(item.correction.decode("utf-8", errors="strict"))
            if not isinstance(correction, Mapping):
                raise ValueError("REPAIR lineage must be a mapping")
            parent_run_id = correction.get("failed_run_id")
            if not isinstance(parent_run_id, str) or not parent_run_id:
                raise ValueError("REPAIR lineage has no failed RUN identity")
            if family != "PRIMARY":
                raise ValueError("terminal has competing correction families")
            embedded_run = correction.get("run")
            embedded_failure = correction.get("failure")
            authorization = correction.get("repair")
            correction_task = correction.get("task")
            run_value = json.loads(item.run.decode("utf-8", errors="strict"))
            if (
                correction.get("failed_head_sha") != run.base_sha
                or not isinstance(correction.get("root_base_sha"), str)
                or not correction.get("root_base_sha")
                or not isinstance(embedded_run, Mapping)
                or dict(embedded_run) != dict(run_value)
                or not isinstance(embedded_failure, Mapping)
                or embedded_failure.get("run_id") != parent_run_id
                or embedded_failure.get("failed_head_sha") != run.base_sha
                or not isinstance(authorization, Mapping)
                or authorization.get("failed_run_id") != parent_run_id
                or authorization.get("failed_head_sha") != run.base_sha
                or not isinstance(correction_task, Mapping)
                or correction_task.get("task_id") != task.task_id
                or correction_task.get("revision") != task.revision
            ):
                raise ValueError("REPAIR execution lineage is invalid")
            family = "REPAIR"
        terminal = json.loads(item.terminal.decode("utf-8", errors="strict"))
        if not isinstance(terminal, Mapping):
            raise ValueError("terminal document must be a mapping")
        if item.kind == "RESULT":
            _validate_lifecycle_result(
                task=task,
                run=run,
                family=family,
                run_document=run_document,
                terminal=terminal,
                candidate_sha=item.candidate_sha,
            )
        else:
            _validate_lifecycle_failure(
                repo=repo,
                task=task,
                run=run,
                terminal=terminal,
                candidate_sha=item.candidate_sha,
                candidate_available=item.candidate_available,
                continuation_of=parent_run_id if family == "REPAIR" else None,
            )
        decoded.append(
            _LifecycleRun(
                item.run_id,
                family,
                run,
                item.candidate_sha,
                item.kind,
                terminal,
                parent_run_id,
                review_id,
                finding_id,
                item.candidate_available,
                correction,
                run_document,
                semantic_predecessor,
                execution_base_run_id,
            )
        )
    # A remediation points to the uniquely reviewed predecessor with the same
    # immutable reviewed SHA and finding identity.  No numeric RUN ordering is used.
    by_candidate: dict[str, list[_LifecycleRun]] = {}
    for item in decoded:
        by_candidate.setdefault(item.candidate_sha, []).append(item)
    resolved: list[_LifecycleRun] = []
    for item in decoded:
        if item.family != "REMEDIATION":
            resolved.append(item)
            continue
        pred = item.semantic_predecessor
        if pred is not None:
            parents = [
                parent for parent in decoded
                if parent.run_id == pred.source_run_id
                and parent.candidate_sha == pred.reviewed_sha
                and parent.terminal_kind == "RESULT"
                and parent.run_id in reviews
                and reviews[parent.run_id].review_id == pred.review_id
                and pred.finding_id in {f.id for f in reviews[parent.run_id].findings}
            ]
        else:
            parents = [
                parent
                for parent in by_candidate.get(item.run.base_sha, ())
                if parent.terminal_kind == "RESULT"
                and parent.run_id in reviews
                and reviews[parent.run_id].review_id == item.review_id
                and item.finding_id in {finding.id for finding in reviews[parent.run_id].findings}
            ]
        if len(parents) != 1:
            raise ValueError("REMEDIATION predecessor is missing or ambiguous")
        base_parents = [
            parent for parent in decoded
            if parent.run_id == item.execution_base_run_id
            and parent.terminal_kind == "RESULT"
            and parent.candidate_sha == item.run.base_sha
        ]
        if item.execution_base_run_id is not None and len(base_parents) != 1:
            raise ValueError("REMEDIATION execution base is missing or ambiguous")
        resolved.append(replace(item, parent_run_id=parents[0].run_id))

    # A REPAIR keeps its immediate failed RUN as parent, while inheriting the
    # semantic identity embedded by the correction it repairs.  Resolve that
    # identity only by walking the immutable parent chain; never search the TASK
    # by finding id.
    by_run_id: dict[str, list[_LifecycleRun]] = {}
    for item in resolved:
        by_run_id.setdefault(item.run_id, []).append(item)

    def correction_origin(
        item: _LifecycleRun, seen: frozenset[str] = frozenset()
    ) -> _LifecycleRun | None:
        if item.family == "REMEDIATION":
            return item
        if item.family != "REPAIR":
            return None
        if item.run_id in seen or item.parent_run_id is None:
            raise ValueError("REPAIR predecessor is missing or cyclic")
        parents = by_run_id.get(item.parent_run_id, ())
        if len(parents) != 1:
            raise ValueError("REPAIR predecessor is missing or ambiguous")
        parent = parents[0]
        if (
            parent.terminal_kind != "FAILURE"
            or item.run.base_sha != parent.candidate_sha
            or item.correction is None
            or dict(item.correction["failure"]) != dict(parent.terminal)
            or parent.terminal.get("base_sha") != parent.run.base_sha
        ):
            raise ValueError("REPAIR predecessor identity or failed SHA mismatch")
        return correction_origin(parent, seen.union((item.run_id,)))

    correction_complete: list[_LifecycleRun] = []
    for item in resolved:
        if item.family != "REPAIR":
            correction_complete.append(item)
            continue
        origin = correction_origin(item)
        correction_complete.append(
            replace(
                item,
                review_id=None if origin is None else origin.review_id,
                finding_id=None if origin is None else origin.finding_id,
            )
        )
    return correction_complete, reviews


def _correction_prior_review(
    tip: _LifecycleRun,
    lifecycle: list[_LifecycleRun],
    reviews: Mapping[str, Review],
) -> Review:
    """Resolve a DELTA predecessor through one exact correction lineage."""

    by_run_id: dict[str, list[_LifecycleRun]] = {}
    for item in lifecycle:
        by_run_id.setdefault(item.run_id, []).append(item)
    current = tip
    seen: set[str] = set()
    while current.family == "REPAIR":
        if current.run_id in seen or current.parent_run_id is None:
            raise ValueError("DELTA review predecessor is missing or ambiguous")
        seen.add(current.run_id)
        parents = by_run_id.get(current.parent_run_id, ())
        if len(parents) != 1:
            raise ValueError("DELTA review predecessor is missing or ambiguous")
        current = parents[0]
    if current.family != "REMEDIATION" or current.parent_run_id is None:
        raise ValueError("DELTA review predecessor is missing or ambiguous")
    prior_review = reviews.get(current.parent_run_id)
    semantic_reviewed_sha = (
        current.semantic_predecessor.reviewed_sha
        if current.semantic_predecessor is not None
        else current.run.base_sha
    )
    if (
        prior_review is None
        or prior_review.review_id != current.review_id
        or prior_review.reviewed_sha != semantic_reviewed_sha
        or tip.review_id != current.review_id
        or tip.finding_id != current.finding_id
    ):
        raise ValueError("DELTA review predecessor is missing or ambiguous")
    return prior_review


def _operational_parent(item: _LifecycleRun) -> str | None:
    if item.family == "REMEDIATION" and item.execution_base_run_id is not None:
        return item.execution_base_run_id
    return item.parent_run_id


def _ordered_lineage(
    tip: _LifecycleRun, lifecycle: list[_LifecycleRun]
) -> list[_LifecycleRun]:
    by_id: dict[str, list[_LifecycleRun]] = {}
    for item in lifecycle:
        by_id.setdefault(item.run_id, []).append(item)
    ordered = [tip]
    seen = {tip.run_id}
    current = tip
    while (parent_id := _operational_parent(current)) is not None:
        parents = by_id.get(parent_id, ())
        if len(parents) != 1 or parent_id in seen:
            raise ValueError("cumulative correction lineage is missing or ambiguous")
        current = parents[0]
        ordered.append(current)
        seen.add(parent_id)
    ordered.reverse()
    if not ordered or ordered[0].family != "PRIMARY":
        raise ValueError("cumulative correction lineage has no PRIMARY root")
    return ordered


def _semantic_origin(
    item: _LifecycleRun, lifecycle: list[_LifecycleRun]
) -> _LifecycleRun:
    by_id = {candidate.run_id: candidate for candidate in lifecycle}
    current = item
    seen: set[str] = set()
    while current.family == "REPAIR":
        if current.run_id in seen or current.parent_run_id not in by_id:
            raise ValueError("correction semantic origin is missing or cyclic")
        seen.add(current.run_id)
        current = by_id[current.parent_run_id]
    if current.family != "REMEDIATION":
        raise ValueError("correction semantic origin is not REMEDIATION")
    return current


def _derive_tip_frontier(
    tip: _LifecycleRun,
    lifecycle: list[_LifecycleRun],
    reviews: Mapping[str, Review],
) -> CorrectionFrontier:
    ordered = _ordered_lineage(tip, lifecycle)
    primary = ordered[0]
    primary_review = reviews.get(primary.run_id)
    if primary_review is None:
        raise ValueError("PRIMARY correction frontier review is missing")
    frontier = CorrectionFrontier.from_primary(primary.run_id, primary_review)
    for item in ordered[1:]:
        review = reviews.get(item.run_id)
        if review is None:
            continue
        origin = _semantic_origin(item, lifecycle)
        predecessor = origin.semantic_predecessor
        if predecessor is None:
            if origin.parent_run_id is None:
                raise ValueError("legacy correction predecessor is missing")
            prior = reviews.get(origin.parent_run_id)
            if prior is None:
                raise ValueError("legacy correction predecessor review is missing")
            predecessor = _operator().RemediationPredecessor(
                source_run_id=origin.parent_run_id,
                review_id=origin.review_id,
                finding_id=origin.finding_id,
                reviewed_sha=prior.reviewed_sha,
            )
        frontier = frontier.advance(
            delta_run_id=item.run_id,
            delta_review=review,
            predecessor=predecessor,
        )
    return frontier


def _frontier_identities(frontier: CorrectionFrontier) -> tuple[Mapping[str, str], ...]:
    if len(frontier) > _OUTSTANDING_FINDING_LIMIT:
        raise OverflowError("outstanding finding frontier exceeds fixed bound")
    return tuple(
        {
            "source_run_id": item.source_run_id,
            "review_id": item.review_id,
            "finding_id": item.finding_id,
            "reviewed_sha": item.reviewed_sha,
        }
        for item in frontier.findings
    )


def _resolve_cumulative_execution_base(
    repo: Path,
    task: Task,
    lifecycle: RemoteTaskLifecycle,
    *,
    source_run_id: str,
    review_id: str,
    finding_id: str,
    reviewed_sha: str,
) -> tuple[str, str]:
    """Resolve the exact operational tip while retaining semantic finding identity."""

    decoded, reviews = _decode_remote_lifecycle(repo, task, lifecycle)
    children: dict[str, list[_LifecycleRun]] = {}
    for item in decoded:
        parent_id = _operational_parent(item)
        if parent_id is not None:
            children.setdefault(parent_id, []).append(item)
    if any(len(value) != 1 for value in children.values()):
        raise ValueError("cumulative correction lineage has competing continuations")
    tips = [item for item in decoded if item.run_id not in children]
    if len(tips) != 1 or tips[0].terminal_kind != "RESULT":
        raise ValueError("cumulative correction tip is missing or ambiguous")
    tip = tips[0]
    frontier = _derive_tip_frontier(tip, decoded, reviews)
    expected = (source_run_id, review_id, finding_id, reviewed_sha)
    if not any(item.key == expected for item in frontier.findings):
        raise ValueError("selected semantic finding is not outstanding at execution base")
    if not _operator()._git_is_ancestor(repo, reviewed_sha, tip.candidate_sha):
        raise ValueError("cumulative execution base does not descend from reviewed SHA")
    if not _operator()._git_is_ancestor(repo, lifecycle.main_sha, tip.candidate_sha):
        raise ValueError("current main and cumulative execution base require integration")
    return tip.run_id, tip.candidate_sha


def _local_pending_runs(
    repo: Path,
    state: Any,
    task: Task,
    remote: list[_LifecycleRun],
    reviews: Mapping[str, Review],
) -> tuple[list[_LifecycleRun], list[_LifecycleRun]]:
    op = _operator()
    remote_kinds = {(item.run_id, item.terminal_kind) for item in remote}
    terminal_pending: list[_LifecycleRun] = []
    active_pending: list[_LifecycleRun] = []
    prefix = task_run_prefix(task.task_id)
    if not state.runs.is_dir():
        return terminal_pending, active_pending
    for path in state.runs.glob(f"{prefix}*.json"):
        run_id = path.stem
        raw = path.read_bytes()
        run_document = json.loads(raw.decode("utf-8", errors="strict"))
        if not isinstance(run_document, Mapping):
            raise ValueError("persisted RUN must be a mapping")
        run, family, review_id, finding_id = _decode_lifecycle_run(raw, run_id=run_id)
        if run.task.id != task.task_id or run.task.revision != task.revision:
            continue
        if run.run_id != run_id or run.status != "ACTIVE":
            raise ValueError("persisted RUN identity is invalid")
        parent_run_id = None
        correction = None
        semantic_predecessor = None
        execution_base_run_id = None
        repair_path = state.repairs / path.name
        if repair_path.is_file():
            if family != "PRIMARY":
                raise ValueError("local RUN has competing correction families")
            repair_execution = json.loads(repair_path.read_text(encoding="utf-8"))
            if not isinstance(repair_execution, Mapping):
                raise ValueError("persisted REPAIR execution must be a mapping")
            correction = repair_execution
            parent_run_id = repair_execution.get("failed_run_id")
            failure = repair_execution.get("failure")
            authorization = repair_execution.get("repair")
            embedded_run = repair_execution.get("run")
            if (
                not isinstance(parent_run_id, str)
                or not parent_run_id
                or repair_execution.get("failed_head_sha") != run.base_sha
                or not isinstance(failure, Mapping)
                or failure.get("run_id") != parent_run_id
                or failure.get("failed_head_sha") != run.base_sha
                or not isinstance(authorization, Mapping)
                or authorization.get("failed_run_id") != parent_run_id
                or authorization.get("failed_head_sha") != run.base_sha
                or not isinstance(embedded_run, Mapping)
                or op._run_from_data(embedded_run) != run
            ):
                raise ValueError("persisted REPAIR execution lineage is invalid")
            family = "REPAIR"
        elif family == "REMEDIATION":
            local_value = json.loads(raw.decode("utf-8", errors="strict"))
            execution = op._remediation_execution_from_data(local_value["execution"])
            if "predecessor" in local_value:
                semantic_predecessor = op._parse_remediation_predecessor(
                    local_value["predecessor"]
                )
            if "execution_base" in local_value:
                execution_base_run_id = op._parse_remediation_execution_base(
                    local_value["execution_base"]
                ).run_id
            elif execution.remediation.reviewed_sha != run.base_sha:
                raise ValueError("persisted legacy REMEDIATION reviewed SHA is invalid")
            parents = [
                parent
                for parent in remote
                if (
                    (semantic_predecessor is None and parent.candidate_sha == run.base_sha)
                    or (
                        semantic_predecessor is not None
                        and parent.run_id == semantic_predecessor.source_run_id
                        and parent.candidate_sha == semantic_predecessor.reviewed_sha
                    )
                )
                and parent.terminal_kind == "RESULT"
                and parent.run_id in reviews
                and reviews[parent.run_id].review_id == review_id
                and reviews[parent.run_id].reviewed_sha
                == execution.remediation.reviewed_sha
                and finding_id in {
                    finding.id for finding in reviews[parent.run_id].findings
                }
            ]
            if len(parents) == 1:
                parent_run_id = parents[0].run_id
        if family == "REPAIR":
            parents = [
                parent
                for parent in remote
                if parent.run_id == parent_run_id
                and parent.candidate_sha == run.base_sha
                and parent.terminal_kind == "FAILURE"
            ]
            if len(parents) != 1:
                parent_run_id = None
        result_path = state.results / path.name
        failure_path = state.failures / path.name
        has_result = result_path.is_file()
        has_failure = failure_path.is_file()
        if has_result and has_failure:
            raise ValueError("persisted RUN has conflicting terminal state")
        if not has_result and not has_failure:
            if not any(item.run_id == run_id for item in remote):
                active_pending.append(
                    _LifecycleRun(
                        run_id, family, run, run.base_sha, "ACTIVE", {},
                        parent_run_id, review_id, finding_id,
                        correction=correction, run_document=run_document,
                        semantic_predecessor=semantic_predecessor,
                        execution_base_run_id=execution_base_run_id,
                    )
                )
            continue
        kind = "RESULT" if has_result else "FAILURE"
        value = json.loads(
            (result_path if has_result else failure_path).read_text(encoding="utf-8")
        )
        if not isinstance(value, Mapping):
            raise ValueError("persisted terminal document must be a mapping")
        if has_result:
            result_data = value.get("result")
            candidate_sha = (
                result_data.get("head_sha")
                if isinstance(result_data, Mapping)
                else None
            )
            if not isinstance(candidate_sha, str) or not candidate_sha:
                raise ValueError("persisted RESULT has no candidate head")
            _validate_lifecycle_result(
                task=task,
                run=run,
                family=family,
                run_document=run_document,
                terminal=value,
                candidate_sha=candidate_sha,
            )
        else:
            candidate_sha = value.get("failed_head_sha")
            if not isinstance(candidate_sha, str) or not candidate_sha:
                raise ValueError("persisted FAILURE has no failed head")
            _validate_lifecycle_failure(
                repo=repo,
                task=task,
                run=run,
                terminal=value,
                candidate_sha=candidate_sha,
                candidate_available=True,
                continuation_of=parent_run_id if family == "REPAIR" else None,
            )
        same_run = [item for item in remote if item.run_id == run_id]
        if same_run:
            canonical = same_run[0] if len(same_run) == 1 else None
            if (
                canonical is None
                or (run_id, kind) not in remote_kinds
                or canonical.candidate_sha != candidate_sha
                or canonical.family != family
                or canonical.parent_run_id != parent_run_id
                or canonical.execution_base_run_id != execution_base_run_id
                or canonical.review_id != review_id
                or canonical.finding_id != finding_id
                or canonical.run_document is None
                or dict(canonical.run_document) != dict(run_document)
                or dict(canonical.terminal) != dict(value)
                or (
                    None if canonical.correction is None
                    else dict(canonical.correction)
                )
                != (None if correction is None else dict(correction))
            ):
                raise ValueError("local and canonical terminal state conflict")
            continue
        terminal_pending.append(
            _LifecycleRun(
                run_id, family, run, candidate_sha, kind, value,
                parent_run_id=parent_run_id,
                review_id=review_id, finding_id=finding_id,
                correction=correction, run_document=run_document,
                semantic_predecessor=semantic_predecessor,
                execution_base_run_id=execution_base_run_id,
            )
        )
    return terminal_pending, active_pending


def _is_exact_local_correction_tip(
    pending: _LifecycleRun, remote: list[_LifecycleRun]
) -> bool:
    """Recognize one local child that exactly continues the canonical remote tip."""

    if pending.family not in ("REMEDIATION", "REPAIR"):
        return False
    remote_ids = [item.run_id for item in remote]
    if len(set(remote_ids)) != len(remote_ids):
        return False
    identities = set(remote_ids)
    children: dict[str, list[str]] = {}
    for item in remote:
        parent_id = _operational_parent(item)
        if parent_id is None:
            continue
        if parent_id not in identities:
            return False
        children.setdefault(parent_id, []).append(item.run_id)
    if any(len(set(value)) != 1 for value in children.values()):
        return False
    tips = [item for item in remote if item.run_id not in children]
    return len(tips) == 1 and _operational_parent(pending) == tips[0].run_id


def observe_unified_state(
    task_id: str, *, repo: str | Path | None = None
) -> UnifiedStateObservation:
    """Reduce the exact current TASK lifecycle without acquiring mutation authority."""

    op = _operator()
    root = op.resolve_repository(repo)
    task = op.load_task(root, task_id)
    state = op._runtime_paths_readonly(root)
    admission_context = _bounded_admission_context(state, task)
    try:
        with op._remote_observation_repository(root) as observer:
            resolve_lifecycle_fn = getattr(
                op, "resolve_remote_task_lifecycle", resolve_remote_task_lifecycle
            )
            lifecycle = resolve_lifecycle_fn(
                observer, task_id=task.task_id, task_revision=task.revision
            )
            remote, reviews = _decode_remote_lifecycle(observer, task, lifecycle)
            pending_terminal, pending_active = _local_pending_runs(
                observer, state, task, remote, reviews
            )
            if (
                len(pending_terminal) > 1
                or (pending_terminal and pending_active)
                or (
                    pending_terminal
                    and remote
                    and not _is_exact_local_correction_tip(
                        pending_terminal[0], remote
                    )
                )
            ):
                return _unified_blocked(
                    task, "AMBIGUOUS_LOCAL_TIPS", admission_failures=admission_context
                )
            if pending_terminal:
                tip = pending_terminal[0]
                return UnifiedStateObservation(
                    task.task_id, task.revision, "TRANSPORT", "RETRY_TRANSPORT",
                    run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                    failed_run_id=(tip.run_id if tip.terminal_kind == "FAILURE" else None),
                    failed_head_sha=(tip.candidate_sha if tip.terminal_kind == "FAILURE" else None),
                    admission_failures=admission_context,
                )
            if len(pending_active) > 1 or (
                pending_active
                and remote
                and not _is_exact_local_correction_tip(pending_active[0], remote)
            ):
                return _unified_blocked(
                    task, "AMBIGUOUS_ACTIVE_TIPS", admission_failures=admission_context
                )
            if pending_active:
                return UnifiedStateObservation(
                    task.task_id, task.revision, "WAIT", "WAIT",
                    run_id=pending_active[0].run_id,
                    admission_failures=admission_context,
                )

            kinds: dict[str, set[str]] = {}
            for item in remote:
                kinds.setdefault(item.run_id, set()).add(item.terminal_kind)
            conflicts = sorted(run_id for run_id, values in kinds.items() if len(values) > 1)
            if conflicts:
                if len(conflicts) != 1 or len(kinds) != 1:
                    return _unified_blocked(
                        task, "TERMINAL_CONFLICT", admission_failures=admission_context
                    )
                conflict = conflicts[0]
                try:
                    resolve_recovery_fn = getattr(
                        op, "resolve_remote_primary_recovery", resolve_remote_primary_recovery
                    )
                    recovery = resolve_recovery_fn(observer, run_id=conflict)
                    recovered_run, family, _, _ = _decode_lifecycle_run(
                        recovery.success_run, run_id=conflict
                    )
                    if (
                        family != "PRIMARY"
                        or recovered_run.task.id != task.task_id
                        or recovered_run.task.revision != task.revision
                    ):
                        raise ValueError("recovery does not bind current PRIMARY TASK")
                except (ReviewTransportError, ValueError, KeyError, TypeError, UnicodeError):
                    return _unified_blocked(
                        task, "TERMINAL_CONFLICT", admission_failures=admission_context,
                        run_id=conflict,
                    )
                return UnifiedStateObservation(
                    task.task_id, task.revision, "RECOVERY", "RECOVER_PRIMARY",
                    run_id=conflict, source_run_id=conflict,
                    candidate_sha=recovery.candidate_sha,
                    admission_failures=admission_context,
                )
            if not remote:
                return UnifiedStateObservation(
                    task.task_id, task.revision, "READY", "EXECUTE_PRIMARY",
                    admission_failures=admission_context,
                )

            children: dict[str, list[str]] = {}
            identities = {item.run_id for item in remote}
            for item in remote:
                operational_parent = _operational_parent(item)
                if operational_parent is not None:
                    if operational_parent not in identities:
                        raise ValueError("correction parent is not canonical")
                    children.setdefault(operational_parent, []).append(item.run_id)
            if any(len(set(value)) != 1 for value in children.values()):
                return _unified_blocked(
                    task, "COMPETING_CONTINUATIONS", admission_failures=admission_context
                )
            tips = [item for item in remote if item.run_id not in children]
            if len(tips) != 1:
                return _unified_blocked(
                    task, "AMBIGUOUS_LINEAGE_TIP", admission_failures=admission_context
                )
            tip = tips[0]
            if tip.terminal_kind == "FAILURE":
                candidate = tip.terminal.get("candidate")
                if (
                    not tip.candidate_available
                    or not isinstance(candidate, Mapping)
                    or candidate.get("repairable") is not True
                    or candidate.get("transportable") is not True
                ):
                    return _unified_blocked(
                        task, "NON_REPAIRABLE_FAILURE", admission_failures=admission_context,
                        run_id=tip.run_id, failed_run_id=tip.run_id,
                        failed_head_sha=tip.candidate_sha,
                    )
                selectors = [
                    item for item in lifecycle.repair_selectors if item[0] == tip.run_id
                ]
                if not selectors:
                    return UnifiedStateObservation(
                        task.task_id, task.revision, "CORRECTION", "AUTHOR_REPAIR",
                        run_id=tip.run_id, failed_run_id=tip.run_id,
                        failed_head_sha=tip.candidate_sha,
                        admission_failures=admission_context,
                    )
                if len(selectors) != 1:
                    return _unified_blocked(
                        task, "COMPETING_REPAIRS", admission_failures=admission_context,
                        run_id=tip.run_id, failed_run_id=tip.run_id,
                        failed_head_sha=tip.candidate_sha,
                    )
                repair_authorization = json.loads(
                    selectors[0][2].decode("utf-8", errors="strict")
                )
                if not isinstance(repair_authorization, Mapping):
                    raise ValueError("canonical REPAIR authorization must be a mapping")
                preflight_repair_fn = getattr(op, "preflight_repair", preflight_repair)
                preflight = preflight_repair_fn(
                    tip.run_id, repo=root, repair=repair_authorization
                )
                correction = preflight.as_dict()
                if preflight.status != "READY":
                    return _unified_blocked(
                        task, "CORRECTION_PREFLIGHT_BLOCKED",
                        phase=preflight.phase, reason_code=preflight.reason_code,
                        admission_failures=admission_context, run_id=tip.run_id,
                        failed_run_id=tip.run_id, failed_head_sha=tip.candidate_sha,
                        correction_sha=selectors[0][1], correction=correction,
                    )
                return UnifiedStateObservation(
                    task.task_id, task.revision, "CORRECTION", "EXECUTE_REPAIR",
                    run_id=tip.run_id, failed_run_id=tip.run_id,
                    failed_head_sha=tip.candidate_sha, correction=correction,
                    correction_sha=selectors[0][1],
                    correction_document=repair_authorization,
                    admission_failures=admission_context,
                )

            review = reviews.get(tip.run_id)
            result = validate_result(tip.terminal["result"])
            if review is None:
                return UnifiedStateObservation(
                    task.task_id, task.revision, "REVIEW", "SEMANTIC_REVIEW",
                    run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                    source_run_id=(
                        tip.parent_run_id if tip.family == "REMEDIATION" else None
                    ),
                    failed_run_id=(
                        tip.parent_run_id if tip.family == "REPAIR" else None
                    ),
                    review_id=tip.review_id, finding_id=tip.finding_id,
                    admission_failures=admission_context,
                )
            prior_review = None
            if review.prior_finding_id is not None:
                prior_review = _correction_prior_review(tip, remote, reviews)
                if (
                    review.prior_finding_id != tip.finding_id
                ):
                    raise ValueError("DELTA review predecessor is missing or ambiguous")
            validate_review(task=task, result=result, review=review, prior_review=prior_review)
            if review.reviewed_sha != tip.candidate_sha:
                raise ValueError("review decision does not bind candidate ref")
            if review.verdict == "BLOCKED":
                return _unified_blocked(
                    task, "SEMANTIC_REVIEW_BLOCKED", admission_failures=admission_context,
                    run_id=tip.run_id, review_id=review.review_id,
                    candidate_sha=tip.candidate_sha, reviewed_sha=review.reviewed_sha,
                )
            try:
                frontier = (
                    CorrectionFrontier.from_primary(tip.run_id, review)
                    if review.mode == "PRIMARY"
                    else _derive_tip_frontier(tip, remote, reviews)
                )
                outstanding = _frontier_identities(frontier)
            except OverflowError:
                over_bound_frontier = (
                    CorrectionFrontier.from_primary(tip.run_id, review)
                    if review.mode == "PRIMARY"
                    else _derive_tip_frontier(tip, remote, reviews)
                )
                return _unified_blocked(
                    task, "OUTSTANDING_FINDINGS_BOUND_EXCEEDED",
                    admission_failures=admission_context,
                    blocker_details={
                        "count": len(over_bound_frontier),
                        "limit": _OUTSTANDING_FINDING_LIMIT,
                    },
                )
            if review.verdict == "CHANGES_REQUIRED" or not frontier.is_empty:
                if any(
                    not op._git_is_ancestor(
                        observer, identity["reviewed_sha"], tip.candidate_sha
                    )
                    for identity in outstanding
                ):
                    return _unified_blocked(
                        task, "CUMULATIVE_BASE_INVALID",
                        admission_failures=admission_context,
                        run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                        execution_base_run_id=tip.run_id,
                        execution_base_sha=tip.candidate_sha,
                    )
                if not op._git_is_ancestor(
                    observer, lifecycle.main_sha, tip.candidate_sha
                ):
                    return _unified_blocked(
                        task, "INTEGRATION_REQUIRED",
                        admission_failures=admission_context,
                        run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                        execution_base_run_id=tip.run_id,
                        execution_base_sha=tip.candidate_sha,
                    )
                selector_matches = [
                    (identity, selector)
                    for identity in outstanding
                    for selector in lifecycle.remediation_selectors
                    if selector[0] == identity["source_run_id"]
                    and selector[1] == identity["finding_id"]
                ]
                if not selector_matches:
                    if len(outstanding) != 1:
                        return UnifiedStateObservation(
                            task.task_id, task.revision, "CORRECTION", "AUTHOR_REMEDIATION",
                            run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                            execution_base_run_id=tip.run_id,
                            execution_base_sha=tip.candidate_sha,
                            outstanding_findings=outstanding,
                            admission_failures=admission_context,
                        )
                    identity = outstanding[0]
                    return UnifiedStateObservation(
                        task.task_id, task.revision, "CORRECTION", "AUTHOR_REMEDIATION",
                        run_id=tip.run_id,
                        source_run_id=identity["source_run_id"],
                        review_id=identity["review_id"],
                        finding_id=identity["finding_id"],
                        candidate_sha=tip.candidate_sha,
                        reviewed_sha=identity["reviewed_sha"],
                        execution_base_run_id=tip.run_id,
                        execution_base_sha=tip.candidate_sha,
                        outstanding_findings=outstanding,
                        admission_failures=admission_context,
                    )
                if len(selector_matches) != 1:
                    return _unified_blocked(
                        task, "COMPETING_REMEDIATIONS", admission_failures=admission_context,
                        run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                    )
                identity, selector = selector_matches[0]
                preflight_remediation_fn = getattr(
                    op, "preflight_remediation", preflight_remediation
                )
                preflight = preflight_remediation_fn(
                    task.task_id, finding_id=identity["finding_id"],
                    source_run_id=identity["source_run_id"],
                    approved_remediation_sha=selector[2], repo=root,
                )
                correction = preflight.as_dict()
                if preflight.status != "READY":
                    return _unified_blocked(
                        task, "CORRECTION_PREFLIGHT_BLOCKED",
                        phase=preflight.phase, reason_code=preflight.reason_code,
                        admission_failures=admission_context, run_id=tip.run_id,
                        source_run_id=identity["source_run_id"],
                        review_id=identity["review_id"],
                        finding_id=identity["finding_id"], reviewed_sha=identity["reviewed_sha"],
                        correction_sha=selector[2], correction=correction,
                        outstanding_findings=outstanding,
                    )
                return UnifiedStateObservation(
                    task.task_id, task.revision, "CORRECTION", "EXECUTE_REMEDIATION",
                    run_id=tip.run_id, source_run_id=identity["source_run_id"],
                    review_id=identity["review_id"], finding_id=identity["finding_id"],
                    candidate_sha=tip.candidate_sha, reviewed_sha=identity["reviewed_sha"],
                    execution_base_run_id=preflight.execution_base_run_id,
                    execution_base_sha=preflight.execution_base_sha,
                    correction_sha=selector[2], correction=correction,
                    outstanding_findings=outstanding,
                    admission_failures=admission_context,
                )
            if op._git_is_ancestor(observer, tip.candidate_sha, lifecycle.main_sha):
                return UnifiedStateObservation(
                    task.task_id, task.revision, "DONE", "DONE",
                    run_id=tip.run_id, review_id=review.review_id,
                    candidate_sha=tip.candidate_sha, reviewed_sha=review.reviewed_sha,
                    outstanding_findings=outstanding,
                    admission_failures=admission_context,
                )
            # Reuse the safe-publication lineage validator in the isolated
            # observer before classifying ancestry.  Runtime PASS and a parsed
            # PASS document alone do not establish publication eligibility.
            try:
                publication_candidate, _ = publication_module._load_success_lineage(
                    observer,
                    remote=resolve_transport_remote(observer),
                    run_id=tip.run_id,
                    decision_sha=next(
                        item.decision_sha
                        for item in lifecycle.reviews
                        if item.run_id == tip.run_id
                    ),
                )
                if publication_candidate != tip.candidate_sha:
                    raise ValueError("publication candidate changed during observation")
            except (
                publication_module.PublicationError,
                ReviewTransportError,
                StopIteration,
                ValueError,
            ):
                return _unified_blocked(
                    task, "PUBLICATION_LINEAGE_INVALID",
                    admission_failures=admission_context, run_id=tip.run_id,
                    review_id=review.review_id, candidate_sha=tip.candidate_sha,
                    reviewed_sha=review.reviewed_sha,
                )
            if op._git_is_ancestor(observer, lifecycle.main_sha, tip.candidate_sha):
                return UnifiedStateObservation(
                    task.task_id, task.revision, "PUBLICATION", "PUBLICATION",
                    run_id=tip.run_id, review_id=review.review_id,
                    candidate_sha=tip.candidate_sha, reviewed_sha=review.reviewed_sha,
                    admission_failures=admission_context,
                )
            return _unified_blocked(
                task, "INTEGRATION_REQUIRED", admission_failures=admission_context,
                run_id=tip.run_id, review_id=review.review_id,
                candidate_sha=tip.candidate_sha, reviewed_sha=review.reviewed_sha,
            )
    except RemoteQueryError as exc:
        raise op.OperatorError(
            f"Unified State canonical observation unavailable ({exc.category})"
        ) from exc
    except (ReviewTransportError, ArtifactValidationError, ReviewValidationError,
            KeyError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        # Canonical facts were acquired but cannot support a guessed continuation.
        return _unified_blocked(
            task, "MALFORMED_CANONICAL_STATE", admission_failures=admission_context
        )


__all__ = [
    "UnifiedStateObservation",
    "observe_unified_state",
]
