"""Pure, bounded BP-4 projection of one already-selected semantic decision.

Callers select and supply all material. This module never observes a repository or
chooses a lifecycle action. The six payload types are deliberately separate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from typing import Any, Mapping

from . import brain_context as bp3
from .artifacts import (Evidence, Result, validate_evidence, validate_result,
                        validate_result_package)
from .brain_context import BrainWorkContext, FlowResolution
from .publication import _validate_repair_authorization
from .review import (Remediation, Review, parse_review, validate_remediation,
                     validate_review)
from .review_transport import validate_runtime_failure_binding
from .run import Run
from .task import Task, validate_task


class DecisionPacketError(ValueError):
    """Inputs cannot be projected into a safe, exact Decision Packet."""


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_LOCAL = re.compile(r"(?:[A-Za-z]:[/\\]|\\\\|(?:^|\s)/(?:home|Users|tmp|var|mnt)/)")
_SECRET = re.compile(r"(?i)\b(?:password|secret|api[_-]?key|access[_-]?token|bearer)\s*[:= ]\s*\S+")
_REMOTE_URL = re.compile(r"(?:https?://\S+\.git\b|git@[^\s:]+:[^\s]+)")
_FORBIDDEN_KEYS = frozenset({
    "root", "workspace", "remote", "remote_url", "raw", "raw_path", "path",
    "stdout", "stderr", "log", "logs", "credentials", "secret", "token",
    "chat_history", "chain_of_thought", "provider", "model", "session",
    "hostname", "host", "timestamp", "packet", "decision_packet", "api_key",
})
_OPTIONAL_CARD_CONTEXT = {
    "ARCHITECTURE": frozenset({"canonical_observation.roadmap", "canonical_observation.blocker",
                               "current_request.human_input"}),
    "TASK_AUTHORING": frozenset({"canonical_observation.selected_task", "current_request.human_input"}),
    "SEMANTIC_REVIEW": frozenset({"canonical_observation.blocker", "current_request.human_input"}),
    "REMEDIATION_AUTHORING": frozenset({"canonical_observation.blocker", "current_request.human_input"}),
    "REPAIR_AUTHORING": frozenset({"canonical_observation.blocker", "current_request.human_input"}),
    "DIAGNOSTIC": frozenset({"canonical_observation.roadmap", "canonical_observation.unified_state",
                             "current_request.human_input"}),
}


def _json(value: Any) -> Any:
    try:
        def check(item: Any) -> None:
            if isinstance(item, Mapping):
                if any(not isinstance(key, str) for key in item):
                    raise DecisionPacketError("JSON material has non-text keys")
                for child in item.values():
                    check(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    check(child)
        check(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8", errors="strict")) > 262144:
            raise DecisionPacketError("input material exceeds 262144 UTF-8 bytes")
        return json.loads(encoded)
    except DecisionPacketError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise DecisionPacketError("invalid JSON or Unicode material") from exc


def _render(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_render(value).encode("utf-8", errors="strict")).hexdigest()


def _text(value: Any, name: str, *, maximum: int = 65536) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionPacketError(f"{name} must be nonempty text")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise DecisionPacketError(f"{name} has invalid Unicode") from exc
    if size > maximum or _LOCAL.search(value) or _SECRET.search(value) or _REMOTE_URL.search(value):
        raise DecisionPacketError(f"{name} is over-bound or machine-local")
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise DecisionPacketError(f"{name} must be a canonical SHA")
    return value


def _unique(values: Any, name: str) -> None:
    if not isinstance(values, (list, tuple)) or any(not isinstance(item, str) for item in values) \
            or len(values) != len(set(values)):
        raise DecisionPacketError(f"{name} contains duplicate or invalid semantic material")


def _safe(value: Any, name: str) -> Any:
    value = _json(value)
    def visit(item: Any) -> Any:
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if key.lower() in _FORBIDDEN_KEYS:
                    raise DecisionPacketError(f"{name} contains forbidden field {key}")
                result[key] = visit(child)
            return result
        elif isinstance(item, list):
            return [visit(child) for child in item]
        elif isinstance(item, str):
            return _text(item, name)
        return item
    return visit(value)


@dataclass(frozen=True)
class PlanningReference:
    ref_id: str
    content_sha256: str
    excerpt: str


@dataclass(frozen=True)
class ArchitecturePayload:
    subject_id: str
    planning_excerpt: str
    references: tuple[PlanningReference, ...]
    kind: str = field(default="ARCHITECTURE", init=False)


@dataclass(frozen=True)
class TaskAuthoringPayload:
    roadmap_item_id: str
    planning_excerpt: str
    references: tuple[PlanningReference, ...]
    kind: str = field(default="TASK_AUTHORING", init=False)


@dataclass(frozen=True)
class PriorRemediationLineage:
    source_run: Run
    source_result: Result
    review: Review
    remediation: Remediation
    preceding_review: Review | None = None


@dataclass(frozen=True)
class PriorRepairLineage:
    failed_run: Run
    failure: Mapping[str, Any]
    authorization: Mapping[str, Any]


@dataclass(frozen=True)
class SemanticReviewPayload:
    task: Task
    run: Run
    result: Result
    evidence: tuple[Evidence, ...]
    delta_base_sha: str
    delta_head_sha: str
    implementation_delta: str
    prior_remediation: PriorRemediationLineage | None = None
    prior_repair: PriorRepairLineage | None = None
    kind: str = field(default="SEMANTIC_REVIEW", init=False)


@dataclass(frozen=True)
class RemediationAuthoringPayload:
    task: Task
    run: Run
    result: Result
    evidence: tuple[Evidence, ...]
    review: Review
    finding_id: str
    prior_review: Review | None = None
    kind: str = field(default="REMEDIATION_AUTHORING", init=False)


@dataclass(frozen=True)
class RepairAuthoringPayload:
    task: Task
    run: Run
    failure: Mapping[str, Any]
    failed_head_sha: str
    failure_excerpt: str
    kind: str = field(default="REPAIR_AUTHORING", init=False)


@dataclass(frozen=True)
class DiagnosticPayload:
    subject_kind: str
    subject_id: str
    diagnostic_excerpt: str
    kind: str = field(default="DIAGNOSTIC", init=False)


_PAYLOADS = {
    "ARCHITECTURE": ArchitecturePayload,
    "TASK_AUTHORING": TaskAuthoringPayload,
    "SEMANTIC_REVIEW": SemanticReviewPayload,
    "REMEDIATION_AUTHORING": RemediationAuthoringPayload,
    "REPAIR_AUTHORING": RepairAuthoringPayload,
    "DIAGNOSTIC": DiagnosticPayload,
}


@dataclass(frozen=True)
class DecisionPacket:
    _body_json: str
    packet_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {**json.loads(self._body_json), "packet_fingerprint": self.packet_fingerprint}

    def render(self) -> str:
        return _render(self.as_dict())


def _context(context: BrainWorkContext, resolution: FlowResolution) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(context, BrainWorkContext) or not isinstance(resolution, FlowResolution):
        raise DecisionPacketError("expected BP-3 Work Context and Flow Resolution")
    observed = _json(context.canonical_observation)
    request = _json(context.current_request)
    required_observation = {"format", "version", "kind", "repository", "main_sha",
                            "roadmap", "selection_status", "selected_task", "unified_state",
                            "lifecycle_state", "next_action", "authority", "blocker",
                            "run_created", "executor_invoked", "verification_invoked", "state_mutated"}
    if (set(observed) != required_observation or observed["format"] != "AIOS_BRAIN_SYNC_SNAPSHOT"
            or type(observed["version"]) is not int or observed["version"] != 1
            or observed["kind"] != "BRAIN_SYNC_SNAPSHOT"
            or any(observed[flag] is not False for flag in
                   ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))):
        raise DecisionPacketError("invalid Work Context observation shape")
    basis = bp3._invalidation_basis(observed, request)
    if basis != context.invalidation_basis or bp3._digest(basis) != context.invalidation_fingerprint:
        raise DecisionPacketError("stale or altered Work Context")
    if request is not None and bp3._request(request) != request:
        raise DecisionPacketError("invalid Human request")
    repository = observed.get("repository")
    if (not isinstance(repository, dict) or set(repository) != {"root", "name", "main_sha", "remote"}
            or not isinstance(repository.get("root"), str) or not repository["root"]
            or repository.get("root") != context.operational_repository_root):
        raise DecisionPacketError("altered operational repository identity")
    if repository.get("main_sha") != observed.get("main_sha"):
        raise DecisionPacketError("repository/main identity mismatch")
    _sha(observed.get("main_sha"), "main_sha")
    _text(repository.get("name"), "repository name", maximum=256)
    flow = resolution.selected_flow
    if flow not in _PAYLOADS or resolution.invalidation_fingerprint != context.invalidation_fingerprint:
        raise DecisionPacketError("unsupported or stale Flow Resolution")
    unified = observed.get("unified_state")
    unified_action = unified.get("next_action") if isinstance(unified, dict) else None
    pending = bp3._OBLIGATIONS.get(unified_action)
    if isinstance(unified, dict) and observed.get("selection_status") == "SELECTED":
        expected_authority = unified.get("authority", bp3._OWNERS.get(pending))
        if expected_authority is not None and observed.get("authority") != expected_authority:
            raise DecisionPacketError("Brain Sync authority observation mismatch")
    explicit = request.get("flow_selector") if request else None
    expected = (explicit if explicit is not None else pending if pending is not None else
                "TASK_AUTHORING" if observed.get("selection_status") == "UNAUTHORED_TASK"
                and isinstance(observed.get("blocker"), dict)
                and observed["blocker"].get("code") == "UNAUTHORED_TASK" else "NONE")
    selection_basis = ("EXPLICIT_SELECTOR" if explicit is not None else "UNIFIED_STATE" if pending
                       else "UNIQUE_UNAUTHORED_NEXT" if expected == "TASK_AUTHORING" else "NO_SEMANTIC_FLOW")
    card = _json(resolution.card)
    if isinstance(card, dict):
        for name, allowed in (("required_context", bp3._CONTEXT_PATHS),
                              ("optional_context", bp3._CONTEXT_PATHS),
                              ("entry_conditions", bp3._ENTRIES[flow]),
                              ("forbidden_context", bp3._FORBIDDEN),
                              ("invalidation_rules", bp3._INVALIDATION)):
            bp3._tokens(card.get(name), allowed=allowed,
                        nonempty=name != "optional_context")
    if (flow != expected or resolution.selection_basis != selection_basis
            or resolution.pending_canonical_obligation != pending
            or resolution.pending_canonical_authority_owner != bp3._OWNERS.get(pending)
            or resolution.authority_owner != bp3._OWNERS[flow]
            or resolution.canonical_blocker != observed.get("blocker")
            or resolution.canonical_next_action != observed.get("next_action")
            or resolution.unified_state_next_action != unified_action
            or resolution.requires_fresh_context_for_continuation is not (explicit is not None)
            or not isinstance(card, dict) or set(card) != bp3._CARD_FIELDS
            or card.get("id") != flow or card.get("authority_owner") != bp3._OWNERS[flow]
            or card.get("decision_family_ref") != bp3._FAMILIES[flow]
            or card.get("handoff_target") != bp3._HANDOFFS[flow]
            or card.get("expected_return_shape") != bp3._RETURNS[flow]
            or set(card.get("required_context", [])) != bp3._REQUIRED_CONTEXT[flow]
            or set(card["optional_context"]) != _OPTIONAL_CARD_CONTEXT[flow]
            or bool(set(card["required_context"]) & set(card["optional_context"]))
            or set(card.get("entry_conditions", [])) != bp3._ENTRIES[flow]
            or set(card.get("forbidden_context", [])) != bp3._FORBIDDEN
            or set(card.get("invalidation_rules", [])) != bp3._INVALIDATION):
        raise DecisionPacketError("Flow Resolution conflicts with Work Context or BP-3 contract")
    selected = observed.get("selected_task")
    if selected is not None and (not isinstance(selected, dict) or set(selected) != {"id", "revision"}):
        raise DecisionPacketError("invalid selected TASK identity")
    if unified is not None and selected is not None and unified.get("task") != selected:
        raise DecisionPacketError("selected TASK and Unified State disagree")
    return observed, request


def _references(items: tuple[PlanningReference, ...]) -> list[dict[str, str]]:
    if not isinstance(items, tuple) or not items:
        raise DecisionPacketError("planning references are required")
    refs = []
    seen = set()
    for item in items:
        if not isinstance(item, PlanningReference):
            raise DecisionPacketError("invalid planning reference")
        ref_id = _text(item.ref_id, "reference id", maximum=256)
        digest = item.content_sha256
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None or ref_id in seen:
            raise DecisionPacketError("duplicate or invalid planning reference")
        seen.add(ref_id)
        refs.append({"ref_id": ref_id, "content_sha256": digest,
                     "excerpt": _text(item.excerpt, "reference excerpt")})
    return sorted(refs, key=lambda ref: ref["ref_id"])


def _task(task: Task) -> dict[str, Any]:
    if not isinstance(task, Task):
        raise DecisionPacketError("expected canonical TASK")
    value = asdict(task)
    _unique(task.scope.inspect, "TASK inspect scope")
    _unique(task.scope.modify, "TASK modify scope")
    verification = value["verification"]
    if verification["policy"] is None:
        verification.pop("policy")
    if verification["full_suite_reason"] is None:
        verification.pop("full_suite_reason")
    value = _json(value)
    if validate_task(value) != task:
        raise DecisionPacketError("TASK differs from canonical normalization")
    return _safe(value, "TASK")


def _run(run: Run, task: Task) -> dict[str, Any]:
    if not isinstance(run, Run) or run.task.id != task.task_id or run.task.revision != task.revision:
        raise DecisionPacketError("RUN/TASK mismatch")
    _sha(run.base_sha, "RUN base_sha")
    if run.head_sha is not None:
        _sha(run.head_sha, "RUN head_sha")
    return {"run_id": _text(run.run_id, "RUN id", maximum=256),
            "task": {"id": task.task_id, "revision": task.revision},
            "executor": run.executor, "base_sha": run.base_sha,
            "head_sha": run.head_sha, "status": run.status}


def _result_material(task: Task, run: Run, result: Result,
                     evidence: tuple[Evidence, ...]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(result, Result) or not isinstance(evidence, tuple):
        raise DecisionPacketError("invalid RESULT/EVIDENCE types")
    result_data = {"head_sha": result.head_sha,
                   "claims": [asdict(claim) for claim in result.claims],
                   "changed_files": result.changed_files, "unresolved": result.unresolved}
    if validate_result(_json(result_data)) != result:
        raise DecisionPacketError("RESULT differs from canonical normalization")
    _unique(result.changed_files, "RESULT changed files")
    _unique(result.unresolved, "RESULT unresolved")
    for claim in result.claims:
        _unique(claim.satisfies, "claim acceptance")
        _unique(claim.evidence, "claim EVIDENCE")
    for item in evidence:
        if not isinstance(item, Evidence):
            raise DecisionPacketError("invalid EVIDENCE type")
        evidence_data = {"evidence_id": item.evidence_id, "run_id": item.run_id,
                         "subject_sha": item.subject_sha, "type": item.type,
                         "source": asdict(item.source), "result": asdict(item.result),
                         "raw": {"path": item.raw_path}}
        if validate_evidence(_json(evidence_data)) != item:
            raise DecisionPacketError("EVIDENCE differs from canonical normalization")
    validate_result_package(task=task, run=run, result=result, evidence=evidence)
    _sha(result.head_sha, "RESULT head_sha")
    claims = []
    for claim in result.claims:
        claims.append({"id": _text(claim.id, "claim id", maximum=256),
                       "satisfies": list(claim.satisfies), "claim": _text(claim.claim, "Executor claim"),
                       "evidence": list(claim.evidence)})
    evidence_out = []
    for item in evidence:
        evidence_out.append({"evidence_id": item.evidence_id, "run_id": item.run_id,
                             "subject_sha": item.subject_sha, "type": item.type,
                             "command": _text(item.source.command, "evidence command", maximum=4096),
                             "exit_code": item.result.exit_code,
                             "summary": _text(item.result.summary, "evidence summary")})
    result_out = {"head_sha": result.head_sha, "changed_files": list(result.changed_files),
                  "unresolved": list(result.unresolved)}
    return _safe(result_out, "RESULT"), _safe(sorted(evidence_out, key=lambda e: e["evidence_id"]), "EVIDENCE"), _safe(sorted(claims, key=lambda c: c["id"]), "Executor claims")


def _review(review: Review, task: Task, result: Result, prior: Review | None = None) -> dict[str, Any]:
    if not isinstance(review, Review):
        raise DecisionPacketError("invalid REVIEW type")
    structural = {"review_id": review.review_id, "reviewed_sha": review.reviewed_sha,
                  "mode": review.mode, "verdict": review.verdict,
                  "acceptance": dict(review.acceptance),
                  "findings": [asdict(finding) for finding in review.findings],
                  "prior_finding_id": review.prior_finding_id}
    if parse_review(_render(_json(structural))) != review:
        raise DecisionPacketError("REVIEW differs from canonical normalization")
    validate_review(task=task, result=result, review=review, prior_review=prior)
    return _safe(structural, "REVIEW")


def _subject_task(observed: Mapping[str, Any], task: Task, run: Run,
                  *, source: bool = False) -> None:
    selected = observed.get("selected_task")
    unified = observed.get("unified_state")
    expected_run = (unified.get("source_run_id") or unified.get("run_id")) if source and isinstance(unified, dict) \
        else unified.get("run_id") if isinstance(unified, dict) else None
    if selected != {"id": task.task_id, "revision": task.revision} or not isinstance(unified, dict) \
            or expected_run != run.run_id:
        raise DecisionPacketError("material does not bind selected TASK/RUN")


def _project(flow: str, payload: Any, observed: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    facts: dict[str, Any] = {}
    observations: dict[str, Any] = {}
    claims: dict[str, Any] = {}
    prior: dict[str, Any] = {}
    if flow == "ARCHITECTURE":
        subject = {"kind": "PLANNING", "id": _text(payload.subject_id, "planning subject", maximum=256)}
        observations = {"planning_excerpt": _text(payload.planning_excerpt, "planning excerpt"),
                        "references": _references(payload.references)}
    elif flow == "TASK_AUTHORING":
        item_id = _text(payload.roadmap_item_id, "roadmap item", maximum=256)
        next_items = observed.get("roadmap", {}).get("next_items", [])
        matches = [item for item in next_items if item == item_id or
                   isinstance(item, dict) and item.get("id") == item_id]
        if len(matches) != 1:
            raise DecisionPacketError("roadmap NEXT identity mismatch")
        subject = {"kind": "ROADMAP_NEXT", "item_id": item_id}
        facts = {"roadmap_next": _safe(matches[0], "roadmap NEXT")}
        observations = {"planning_excerpt": _text(payload.planning_excerpt, "planning excerpt"),
                        "references": _references(payload.references)}
    elif flow in {"SEMANTIC_REVIEW", "REMEDIATION_AUTHORING"}:
        task, run, result = payload.task, payload.run, payload.result
        task_out = _task(task)
        run_out = _run(run, task)
        _subject_task(observed, task, run, source=flow == "REMEDIATION_AUTHORING")
        result_out, evidence_out, claim_out = _result_material(task, run, result, payload.evidence)
        subject = {"kind": "CANDIDATE" if flow == "SEMANTIC_REVIEW" else "REVIEW_FINDING",
                   "task": run_out["task"], "run_id": run.run_id,
                   "candidate_sha": result.head_sha}
        facts = {"task": task_out, "run": run_out, "result_identity": result_out,
                 "evidence": evidence_out}
        claims = {"executor_claims": claim_out}
        if flow == "SEMANTIC_REVIEW":
            unified = observed["unified_state"]
            if unified.get("candidate_sha") not in (None, result.head_sha):
                raise DecisionPacketError("selected candidate SHA mismatch")
            if payload.delta_base_sha != run.base_sha or payload.delta_head_sha != result.head_sha:
                raise DecisionPacketError("implementation delta endpoints mismatch")
            observations = {"implementation_delta": {"base_sha": _sha(payload.delta_base_sha, "delta base"),
                            "head_sha": _sha(payload.delta_head_sha, "delta head"),
                            "text": _text(payload.implementation_delta, "implementation delta")}}
            source_id = unified.get("source_run_id")
            failed_id = unified.get("failed_run_id")
            if source_id is not None and failed_id is not None:
                raise DecisionPacketError("competing correction lineage")
            if bool(source_id) != (payload.prior_remediation is not None) or \
                    bool(failed_id) != (payload.prior_repair is not None):
                raise DecisionPacketError("missing or unrelated prior correction lineage")
            if payload.prior_remediation is not None:
                lineage = payload.prior_remediation
                if not isinstance(lineage, PriorRemediationLineage):
                    raise DecisionPacketError("invalid prior REMEDIATION lineage")
                source_run = _run(lineage.source_run, task)
                if source_run["run_id"] != source_id or lineage.source_result.head_sha != lineage.review.reviewed_sha:
                    raise DecisionPacketError("prior REVIEW source identity mismatch")
                source_result_data = _json({"head_sha": lineage.source_result.head_sha,
                    "claims": [asdict(claim) for claim in lineage.source_result.claims],
                    "changed_files": lineage.source_result.changed_files,
                    "unresolved": lineage.source_result.unresolved})
                if validate_result(source_result_data) != lineage.source_result:
                    raise DecisionPacketError("prior RESULT differs from canonical normalization")
                source_result_data = _safe(source_result_data, "prior RESULT")
                _review(lineage.review, task, lineage.source_result, lineage.preceding_review)
                validate_remediation(review=lineage.review, remediation=lineage.remediation, task=task)
                _unique(lineage.remediation.modification_scope, "prior REMEDIATION scope")
                _unique(lineage.remediation.affected_verification, "prior REMEDIATION verification")
                _unique(lineage.remediation.constraints, "prior REMEDIATION constraints")
                if (unified.get("review_id") != lineage.review.review_id
                        or unified.get("finding_id") != lineage.remediation.finding_id):
                    raise DecisionPacketError("prior REMEDIATION correction identity mismatch")
                prior = {"kind": "REMEDIATION", "source_run": source_run,
                         "source_result_sha256": _digest(source_result_data),
                         "review_id": lineage.review.review_id,
                         "reviewed_sha": lineage.review.reviewed_sha,
                         "remediation": _safe(asdict(lineage.remediation), "prior REMEDIATION")}
            if payload.prior_repair is not None:
                lineage = payload.prior_repair
                if not isinstance(lineage, PriorRepairLineage):
                    raise DecisionPacketError("invalid prior REPAIR lineage")
                failed_run = _run(lineage.failed_run, task)
                failure = _json(lineage.failure)
                failed_head = _sha(failure.get("failed_head_sha"), "prior failed head")
                validate_runtime_failure_binding(
                    failure, run_id=failed_run["run_id"], task_id=task.task_id,
                    task_revision=task.revision, executor=lineage.failed_run.executor,
                    base_sha=lineage.failed_run.base_sha, candidate_sha=failed_head,
                    modification_scope=task.scope.modify,
                )
                if failed_run["run_id"] != failed_id or run.base_sha != failed_head:
                    raise DecisionPacketError("prior FAILURE does not bind repair RUN base")
                authorization = _validate_repair_authorization(
                    _json(lineage.authorization), failed_run_id=failed_id,
                    failed_head_sha=failed_head, task=task,
                    failed_changed_files=set(failure["candidate"]["changed_files"]),
                )
                _unique(authorization["modification_scope"], "prior REPAIR scope")
                _unique(authorization["instructions"], "prior REPAIR instructions")
                _unique(authorization["constraints"], "prior REPAIR constraints")
                prior = {"kind": "REPAIR", "failed_run": failed_run,
                         "failed_head_sha": failed_head,
                         "authorization": _safe(authorization, "prior REPAIR")}
        else:
            review_out = _review(payload.review, task, result, payload.prior_review)
            finding = next((f for f in payload.review.findings if f.id == payload.finding_id), None)
            unified = observed["unified_state"]
            if (finding is None or unified.get("finding_id") != payload.finding_id
                    or unified.get("review_id") not in (None, payload.review.review_id)
                    or unified.get("reviewed_sha") not in (None, payload.review.reviewed_sha)):
                raise DecisionPacketError("selected finding identity mismatch")
            subject.update({"review_id": payload.review.review_id, "finding_id": payload.finding_id})
            prior = {"review": review_out}
            if payload.prior_review is not None:
                prior["prior_review"] = _safe({
                    "review_id": payload.prior_review.review_id,
                    "reviewed_sha": payload.prior_review.reviewed_sha,
                    "finding_ids": [finding.id for finding in payload.prior_review.findings],
                }, "prior REVIEW")
    elif flow == "REPAIR_AUTHORING":
        task, run = payload.task, payload.run
        task_out, run_out = _task(task), _run(run, task)
        _subject_task(observed, task, run)
        failed_head = _sha(payload.failed_head_sha, "failed head")
        failure = _json(payload.failure)
        if (not isinstance(failure, dict) or
                set(failure) - {"kind", "run_id", "task", "executor", "base_sha",
                                "failed_head_sha", "phase", "error", "candidate",
                                "continuation_of"}):
            raise DecisionPacketError("unrelated FAILURE material")
        validate_runtime_failure_binding(
            failure, run_id=run.run_id, task_id=task.task_id, task_revision=task.revision,
            executor=run.executor, base_sha=run.base_sha, candidate_sha=failed_head,
            modification_scope=task.scope.modify,
        )
        if (observed["unified_state"].get("failed_run_id") not in (None, run.run_id)
                or observed["unified_state"].get("failed_head_sha") not in (None, failed_head)):
            raise DecisionPacketError("selected failed RUN mismatch")
        subject = {"kind": "FAILURE", "task": run_out["task"],
                   "failed_run_id": run.run_id, "failed_head_sha": failed_head}
        candidate = failure["candidate"]
        facts = {"task": task_out, "run": run_out,
                 "failure": {"kind": "FAILURE", "run_id": run.run_id,
                             "failed_head_sha": failed_head,
                             "phase": _text(failure["phase"], "FAILURE phase", maximum=256),
                             "error_type": _text(failure["error"]["type"], "FAILURE error type", maximum=256),
                             "continuation_of": (_text(failure["continuation_of"],
                                                      "FAILURE continuation", maximum=256)
                                                 if failure.get("continuation_of") is not None else None),
                             "candidate": _safe(candidate, "FAILURE candidate")}}
        observations = {"failure_excerpt": _text(payload.failure_excerpt, "failure excerpt")}
    else:
        kind = payload.subject_kind
        if kind not in {"BLOCKER", "SEMANTIC_SUBJECT"}:
            raise DecisionPacketError("diagnostic subject kind is invalid")
        subject_id = _text(payload.subject_id, "diagnostic subject", maximum=256)
        if kind == "BLOCKER":
            blocker = observed.get("blocker")
            if not isinstance(blocker, dict) or blocker.get("code") != subject_id:
                raise DecisionPacketError("diagnostic blocker mismatch")
        elif observed.get("selected_task") is None or observed["selected_task"].get("id") != subject_id:
            raise DecisionPacketError("diagnostic semantic subject mismatch")
        subject = {"kind": kind, "id": subject_id}
        observations = {"diagnostic_excerpt": _text(payload.diagnostic_excerpt, "diagnostic excerpt")}
    return subject, facts, observations, claims, prior


def compile_decision_packet(context: BrainWorkContext, resolution: FlowResolution,
                            payload: Any) -> DecisionPacket:
    """Compile exactly one caller-selected, provenance-bound semantic packet."""
    try:
        observed, request = _context(context, resolution)
        flow = resolution.selected_flow
        if type(payload) is not _PAYLOADS[flow] or payload.kind != flow:
            raise DecisionPacketError("payload kind does not match selected flow")
        subject, facts, observations, claims, prior = _project(flow, payload, observed)
        blocker = _safe(resolution.canonical_blocker, "canonical blocker")
        human = request.get("human_input") if request else None
        if human is not None:
            _text(human, "Human input", maximum=16384)
        body = _safe({
            "format": "AIOS_DECISION_PACKET", "version": 1, "kind": "DECISION_PACKET",
            "selected_flow": flow, "subject": subject,
            "provenance": {"repository": observed["repository"]["name"],
                           "main_sha": observed["main_sha"],
                           "work_context_invalidation_fingerprint": context.invalidation_fingerprint,
                           "flow_contract_sha256": _digest({
                               key: sorted(value) if isinstance(value, list) else value
                               for key, value in resolution.card.items()})},
            "authority": {"owner": resolution.authority_owner,
                          "pending_canonical_obligation": resolution.pending_canonical_obligation,
                          "pending_canonical_authority_owner": resolution.pending_canonical_authority_owner,
                          "requires_fresh_context_for_continuation": resolution.requires_fresh_context_for_continuation},
            "canonical_facts": {"selection_status": observed["selection_status"],
                                "selected_task": observed["selected_task"],
                                "lifecycle_state": observed["lifecycle_state"],
                                "canonical_next_action": resolution.canonical_next_action,
                                "unified_state_next_action": resolution.unified_state_next_action,
                                "canonical_blocker": blocker, **facts},
            "observations": observations, "executor_claims": claims,
            "prior_canonical_decisions": prior, "current_human_input": human,
            "run_created": False, "executor_invoked": False,
            "verification_invoked": False, "state_mutated": False,
        }, "Decision Packet")
        fingerprint = _digest(body)
        packet = DecisionPacket(_render(body), fingerprint)
        if len(packet.render().encode("utf-8", errors="strict")) > 131072:
            raise DecisionPacketError("Decision Packet exceeds 131072 UTF-8 bytes")
        return packet
    except DecisionPacketError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        raise DecisionPacketError(str(exc)) from exc


__all__ = ["DecisionPacketError", "DecisionPacket", "PlanningReference",
           "ArchitecturePayload", "TaskAuthoringPayload", "PriorRemediationLineage",
           "PriorRepairLineage", "SemanticReviewPayload",
           "RemediationAuthoringPayload", "RepairAuthoringPayload", "DiagnosticPayload",
           "compile_decision_packet"]
