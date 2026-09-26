"""Read-only, bounded BP-4 projection for one BP-3 semantic decision.

All decision material is supplied by the caller. This module neither discovers
canonical artifacts nor selects a correction or performs a lifecycle action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Mapping

from .artifacts import ArtifactValidationError, validate_evidence, validate_result, validate_result_package
from .brain_context import (
    BrainContextError, BrainWorkContext, FlowResolution, _invalidation_basis,
    resolve_flow,
)
from .review import (
    ReviewValidationError, parse_remediation, parse_review,
    validate_remediation, validate_review,
)
from .task import TaskValidationError, validate_task
from .run import Run, RunTaskReference, RunValidationError
from .review_transport import validate_runtime_failure_binding


class DecisionPacketError(ValueError):
    """The supplied decision material cannot be safely projected."""


_FLOWS = frozenset({
    "ARCHITECTURE", "TASK_AUTHORING", "SEMANTIC_REVIEW",
    "REMEDIATION_AUTHORING", "REPAIR_AUTHORING", "DIAGNOSTIC",
})
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MAX_PACKET = 131072
_MAX_INPUT = 2097152
_MAX_TEXT = 65536
_MAX_FRONTIER = 32
_PRIVATE_KEYS = frozenset({
    "root", "workspace", "remote", "remote_url", "raw", "raw_logs",
    "credentials", "secret", "chat_history", "chain_of_thought",
    "timestamp", "created_at", "provider", "model", "session",
    "evidence_path", "log_path",
})


def _json(value: Any) -> str:
    try:
        rendered = json.dumps(value, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False)
        rendered.encode("utf-8", errors="strict")
        return rendered
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DecisionPacketError("decision material must be strict UTF-8 JSON") from exc


def _normal(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, list):
        return [_normal(item) for item in value]
    if isinstance(value, dict):
        return {key: _normal(item) for key, item in value.items()}
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, name: str, *, fields: set[str], required: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - fields or not (required or fields) <= set(value):
        raise DecisionPacketError(f"{name} fields do not match the typed contract")
    return value


def _identity(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 256 or "/" in value or "\\" in value:
        raise DecisionPacketError(f"invalid {name}")
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise DecisionPacketError(f"invalid {name}")
    return value


def _bounded_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_TEXT:
        raise DecisionPacketError(f"{name} exceeds its text contract")
    return value


def _no_packet_nesting(value: Any) -> None:
    if isinstance(value, dict):
        if value.get("format") == "AIOS_DECISION_PACKET" or "packet" in value:
            raise DecisionPacketError("nested Decision Packet is forbidden")
        for item in value.values():
            _no_packet_nesting(item)
    elif isinstance(value, list):
        for item in value:
            _no_packet_nesting(item)


def _bound_projection(value: Any) -> None:
    if isinstance(value, dict):
        if set(value) & _PRIVATE_KEYS:
            raise DecisionPacketError("private or machine-local field in packet projection")
        for item in value.values():
            _bound_projection(item)
    elif isinstance(value, list):
        for item in value:
            _bound_projection(item)
    elif isinstance(value, str):
        _bounded_text(value, "projected text")


def _roadmap_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionPacketError("canonical roadmap observation is invalid")
    # The planning bookmark is sufficient here. Historical dates and provenance
    # remain in Work Context invalidation, not the reasoning envelope.
    return {key: value[key] for key in ("active_track", "active_track_status", "next_items")
            if key in value}


def _task(value: Any, observed: Mapping[str, Any]) -> Any:
    task = validate_task(value)
    expected = observed.get("selected_task")
    if not isinstance(expected, Mapping) or dict(expected) != {"id": task.task_id, "revision": task.revision}:
        raise DecisionPacketError("TASK does not match selected canonical subject")
    return task


def _run(value: Any, task: Any, *, expected_id: str | None = None) -> dict[str, Any]:
    run = _mapping(value, "RUN", fields={"run_id", "task", "executor", "base_sha", "workspace", "head_sha", "status"},
                   required={"run_id", "task", "executor", "base_sha"})
    _identity(run["run_id"], "RUN id")
    _sha(run["base_sha"], "RUN base SHA")
    supplied_task = _mapping(run["task"], "RUN task", fields={"id", "revision"})
    task_reference = RunTaskReference(supplied_task["id"], supplied_task["revision"])
    if task_reference.id != task.task_id or task_reference.revision != task.revision or (
        expected_id is not None and run["run_id"] != expected_id
    ):
        raise DecisionPacketError("RUN does not match canonical subject")
    if "head_sha" in run and run["head_sha"] is not None:
        _sha(run["head_sha"], "RUN head SHA")
    Run(
        run_id=run["run_id"], task=task_reference,
        executor=run["executor"], base_sha=run["base_sha"],
        workspace=run["workspace"], head_sha=run.get("head_sha"),
        status=run.get("status", "ACTIVE"),
    )
    return run


def _result(value: Any, task: Any, run: Mapping[str, Any]) -> Any:
    result = validate_result(value)
    _sha(result.head_sha, "RESULT head SHA")
    if run.get("head_sha") is not None and run["head_sha"] != result.head_sha:
        raise DecisionPacketError("RESULT head does not match RUN head")
    for claim in result.claims:
        if set(claim.satisfies) - {item.id for item in task.acceptance}:
            raise DecisionPacketError("RESULT claim references unrelated acceptance")
        _bounded_text(claim.claim, "Executor claim")
    return result


def _review(value: Any, task: Any, result: Any, prior: Any = None) -> Any:
    review = parse_review(_json(value))
    prior_review = parse_review(_json(prior)) if prior is not None else None
    validate_review(task=task, result=result, review=review, prior_review=prior_review)
    return review


def _finding_entry(value: Any, task: Any) -> dict[str, Any]:
    item = _mapping(value, "finding material", fields={"source_run", "review", "result", "finding_id", "prior_review"},
                    required={"source_run", "review", "result", "finding_id"})
    source_run = _run(item["source_run"], task)
    result = _result(item["result"], task, source_run)
    review = _review(item["review"], task, result, item.get("prior_review"))
    finding_id = _identity(item["finding_id"], "finding id")
    finding = next((f for f in review.findings if f.id == finding_id), None)
    if finding is None or review.verdict != "CHANGES_REQUIRED" or review.acceptance.get(finding.basis) != "FAIL":
        raise DecisionPacketError("finding is not an outstanding failed REVIEW finding")
    for name in ("location", "issue", "expected"):
        _bounded_text(getattr(finding, name), f"finding {name}")
    return {
        "source_run_id": source_run["run_id"], "review_id": review.review_id,
        "reviewed_sha": review.reviewed_sha, "finding_id": finding.id,
        "basis": finding.basis, "action": finding.action,
        "location": finding.location, "issue": finding.issue,
        "expected": finding.expected,
    }


def _finding_identity(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (item["source_run_id"], item["review_id"], item["finding_id"], item["reviewed_sha"])


def _authoring(material: dict[str, Any], observed: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    _mapping(material, "REMEDIATION_AUTHORING", fields={"kind", "subject_kind", "task", "findings"})
    task = _task(material["task"], observed)
    unified = observed.get("unified_state")
    if not isinstance(unified, dict) or unified.get("next_action") != "AUTHOR_REMEDIATION":
        raise DecisionPacketError("canonical state is not authoring REMEDIATION")
    raw = material["findings"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= _MAX_FRONTIER:
        raise DecisionPacketError("invalid bounded finding set")
    findings = [_finding_entry(item, task) for item in raw]
    keys = [_finding_identity(item) for item in findings]
    if len(set(keys)) != len(keys):
        raise DecisionPacketError("duplicate finding identity")
    outstanding = unified.get("outstanding_findings")
    if not isinstance(outstanding, list) or not 1 <= len(outstanding) <= _MAX_FRONTIER:
        raise DecisionPacketError("canonical outstanding finding set is absent or unbounded")
    expected = []
    for item in outstanding:
        _mapping(item, "outstanding finding", fields={"source_run_id", "review_id", "finding_id", "reviewed_sha"})
        expected.append(_finding_identity(item))
    if len(set(expected)) != len(expected) or set(keys) != set(expected):
        raise DecisionPacketError("finding set does not exactly match Unified State")
    findings.sort(key=_finding_identity)  # Stable serialization, never a priority order.
    shape = material["subject_kind"]
    if shape == "UNIQUE_FINDING":
        if len(findings) != 1 or any(
            unified.get(field) != findings[0][field]
            for field in ("source_run_id", "review_id", "finding_id", "reviewed_sha")
        ):
            raise DecisionPacketError("unique finding does not match selected Unified State identity")
        subject = {"kind": shape, **findings[0]}
    elif shape == "CORRECTION_FRONTIER":
        if len(findings) < 2 or any(unified.get(field) is not None for field in
            ("source_run_id", "review_id", "finding_id", "reviewed_sha")):
            raise DecisionPacketError("frontier requires an unselected multi-finding state")
        base = unified.get("execution_base")
        if not isinstance(base, dict) or not base:
            raise DecisionPacketError("frontier execution base is missing")
        from .operator import _parse_remediation_execution_base
        if _parse_remediation_execution_base(base).as_dict() != base:
            raise DecisionPacketError("invalid frontier execution base")
        subject = {"kind": shape, "execution_base": base, "findings": findings}
    else:
        raise DecisionPacketError("unknown REMEDIATION_AUTHORING subject kind")
    return subject, asdict(task), None, None


def _prior_correction(value: Any, run_doc: dict[str, Any], run: dict[str, Any], task: Any,
                      unified: Mapping[str, Any]) -> dict[str, Any]:
    prior = _mapping(value, "prior correction", fields={
        "kind", "authorization_sha", "authorization", "source_review", "source_result",
        "source_run", "prior_review", "execution", "failed_run", "failure",
    }, required={"kind", "authorization_sha", "authorization"})
    sha = _sha(prior["authorization_sha"], "correction authorization SHA")
    if prior["kind"] == "REMEDIATION":
        if set(prior) - {"kind", "authorization_sha", "authorization", "source_review", "source_result", "source_run", "prior_review"} or not {"source_review", "source_result", "source_run"} <= set(prior):
            raise DecisionPacketError("invalid REMEDIATION provenance contract")
        if run_doc.get("kind") != "REMEDIATION" or run_doc.get("remediation_authorization_sha") != sha:
            raise DecisionPacketError("REMEDIATION authorization identity mismatch or missing")
        if unified.get("failed_run_id") is not None:
            raise DecisionPacketError("REMEDIATION material conflicts with failed RUN lineage")
        execution = _mapping(run_doc.get("execution"), "REMEDIATION execution", fields={"review_id", "finding", "remediation", "run", "original_constraints"},
                             required={"review_id", "finding", "remediation", "run"})
        persisted_remediation = parse_remediation(_json(execution["remediation"]))
        remediation = parse_remediation(_json(prior["authorization"]))
        if execution["run"] != run or persisted_remediation != remediation:
            raise DecisionPacketError("REMEDIATION execution differs from supplied authorization")
        source_run = _run(prior["source_run"], task)
        source_result = _result(prior["source_result"], task, source_run)
        source_review = _review(prior["source_review"], task, source_result, prior.get("prior_review"))
        validate_remediation(review=source_review, remediation=remediation, task=task)
        predecessor = _mapping(run_doc.get("predecessor"), "REMEDIATION predecessor", fields={"source_run_id", "review_id", "finding_id", "reviewed_sha"})
        if predecessor != {"source_run_id": source_run["run_id"], "review_id": source_review.review_id,
                           "finding_id": remediation.finding_id, "reviewed_sha": source_review.reviewed_sha} or (
            execution["review_id"] != source_review.review_id or
            execution["finding"] != asdict(next(f for f in source_review.findings if f.id == remediation.finding_id))
        ):
            raise DecisionPacketError("REMEDIATION source lineage mismatch")
        return {"kind": "REMEDIATION", "authorization_sha": sha, "predecessor": predecessor,
                "authorization": asdict(remediation)}
    if prior["kind"] == "REPAIR":
        if set(prior) != {"kind", "authorization_sha", "authorization", "execution", "failed_run", "failure"} or run_doc.get("kind") == "REMEDIATION":
            raise DecisionPacketError("invalid REPAIR provenance contract")
        execution = _mapping(prior["execution"], "REPAIR execution", fields={
            "failed_run_id", "root_base_sha", "result_base_sha", "failed_head_sha", "failure", "task", "repair", "run", "repair_authorization_sha"},
            required={"failed_run_id", "failed_head_sha", "failure", "task", "repair", "run", "repair_authorization_sha"})
        failed = _run(prior["failed_run"], task)
        failure = prior["failure"]
        if not isinstance(failure, dict) or unified.get("failed_run_id") != failed["run_id"] or (
            failed.get("head_sha") is not None and failed["head_sha"] != run["base_sha"]
        ) or not isinstance(execution["task"], dict) or execution["task"].get("task_id") != task.task_id or execution["task"].get("revision") != task.revision or execution["repair_authorization_sha"] != sha or execution["run"] != run or execution["repair"] != prior["authorization"] or execution["failure"] != failure or execution["failed_run_id"] != failed["run_id"] or execution["failed_head_sha"] != run["base_sha"] or failure.get("run_id") != failed["run_id"] or failure.get("failed_head_sha") != run["base_sha"] or failure.get("task") != {"id": task.task_id, "revision": task.revision}:
            raise DecisionPacketError("REPAIR authorization or failed RUN lineage mismatch")
        from .publication import _validate_repair_authorization
        candidate = failure.get("candidate")
        if not isinstance(candidate, dict) or not isinstance(candidate.get("changed_files"), list):
            raise DecisionPacketError("failed candidate scope is absent")
        _validate_repair_authorization(prior["authorization"], failed_run_id=failed["run_id"],
                                       failed_head_sha=run["base_sha"], task=task,
                                       failed_changed_files=set(candidate["changed_files"]))
        return {"kind": "REPAIR", "authorization_sha": sha,
                "failed_run_id": failed["run_id"], "failed_head_sha": run["base_sha"],
                "authorization": prior["authorization"]}
    raise DecisionPacketError("unknown prior correction kind")


def _semantic_review(material: dict[str, Any], observed: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    _mapping(material, "SEMANTIC_REVIEW", fields={"kind", "task", "run", "result", "evidence", "prior_correction"},
             required={"kind", "task", "run", "result", "evidence"})
    task = _task(material["task"], observed)
    unified = observed.get("unified_state")
    if not isinstance(unified, dict) or unified.get("next_action") != "SEMANTIC_REVIEW":
        raise DecisionPacketError("canonical state is not SEMANTIC_REVIEW")
    run_doc = material["run"]
    if not isinstance(run_doc, dict):
        raise DecisionPacketError("invalid RUN document")
    if run_doc.get("kind") == "REMEDIATION":
        _mapping(run_doc, "REMEDIATION RUN", fields={
            "kind", "remediation_authorization_sha", "predecessor", "execution",
            "execution_base", "acceptance"},
            required={"kind", "predecessor", "execution"})
        if not isinstance(run_doc["execution"], dict) or not isinstance(run_doc["predecessor"], dict):
            raise DecisionPacketError("malformed REMEDIATION RUN lineage")
    run = _run(run_doc["execution"].get("run") if run_doc.get("kind") == "REMEDIATION" else run_doc,
               task, expected_id=unified.get("run_id"))
    result = _result(material["result"], task, run)
    if run_doc.get("kind") == "REMEDIATION":
        base = run_doc.get("execution_base")
        if base is not None:
            from .operator import _parse_remediation_execution_base
            parsed_base = _parse_remediation_execution_base(base)
            if parsed_base.as_dict() != base or parsed_base.candidate_sha != run["base_sha"]:
                raise DecisionPacketError("REMEDIATION execution base mismatch")
        elif run_doc["predecessor"].get("reviewed_sha") != run["base_sha"]:
            raise DecisionPacketError("legacy REMEDIATION execution base mismatch")
        if "acceptance" in run_doc and run_doc["acceptance"] != {
            "mode": "DIRECT_CANDIDATE", "candidate_head": result.head_sha
        }:
            raise DecisionPacketError("direct candidate identity mismatch")
    raw_evidence = material["evidence"]
    if not isinstance(raw_evidence, list) or len(raw_evidence) > 128:
        raise DecisionPacketError("invalid bounded EVIDENCE set")
    evidence = tuple(validate_evidence(item) for item in raw_evidence)
    canonical_run = Run(
        run_id=run["run_id"], task=RunTaskReference(task.task_id, task.revision),
        executor=run["executor"], base_sha=run["base_sha"],
        workspace=run["workspace"], head_sha=run.get("head_sha"),
        status=run.get("status", "ACTIVE"),
    )
    validate_result_package(task=task, run=canonical_run, result=result, evidence=evidence)
    if unified.get("candidate_sha") is not None and unified["candidate_sha"] != result.head_sha:
        raise DecisionPacketError("candidate SHA does not match RESULT")
    correction = material.get("prior_correction")
    if correction is not None:
        prior = _prior_correction(correction, run_doc, run, task, unified)
    elif run_doc.get("kind") == "REMEDIATION" or unified.get("failed_run_id") is not None:
        raise DecisionPacketError("exact prior correction provenance is required")
    else:
        prior = None
    claims = [{"id": c.id, "satisfies": sorted(c.satisfies), "claim": c.claim,
               "evidence_ids": sorted(c.evidence)} for c in result.claims]
    subject = {"run_id": run["run_id"], "task": {"id": task.task_id, "revision": task.revision},
               "base_sha": run["base_sha"], "head_sha": result.head_sha,
               "changed_files": sorted(result.changed_files),
               "unresolved": sorted(result.unresolved)}
    observations = []
    for item in evidence:
        _bounded_text(item.result.summary, "EVIDENCE summary")
        observations.append({"evidence_id": item.evidence_id, "type": item.type,
                             "exit_code": item.result.exit_code, "summary": item.result.summary})
    observations.sort(key=lambda item: item["evidence_id"])
    return subject, asdict(task), claims, prior, observations


def _repair_authoring(material: dict[str, Any], observed: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    _mapping(material, "REPAIR_AUTHORING", fields={"kind", "task", "failed_run", "failure"})
    task = _task(material["task"], observed)
    unified = observed.get("unified_state")
    if not isinstance(unified, dict) or unified.get("next_action") != "AUTHOR_REPAIR":
        raise DecisionPacketError("canonical state is not REPAIR authoring")
    failed = _run(material["failed_run"], task, expected_id=unified.get("failed_run_id"))
    failure = material["failure"]
    failed_head = _sha(unified.get("failed_head_sha"), "failed head SHA")
    if not isinstance(failure, dict) or (
        failed.get("head_sha") is not None and failed["head_sha"] != failed_head
    ):
        raise DecisionPacketError("FAILURE is not bound to failed RUN")
    validate_runtime_failure_binding(
        failure,
        run_id=failed["run_id"], task_id=task.task_id, task_revision=task.revision,
        executor=failed["executor"], base_sha=failed["base_sha"],
        candidate_sha=failed_head, modification_scope=task.scope.modify,
    )
    subject = {"failed_run_id": failed["run_id"], "failed_head_sha": failed_head}
    candidate = failure["candidate"]
    subject["failed_changed_files"] = sorted(candidate["changed_files"])
    observation = {}
    if failure.get("phase") not in {"VERIFICATION", "EXECUTION", "COMPLETION_GATE"}:
        raise DecisionPacketError("FAILURE phase is invalid")
    for key in ("phase", "reason_code"):
        if key in failure:
            value = _bounded_text(failure[key], f"FAILURE {key}")
            if len(value) > 256 or re.fullmatch(r"[A-Z][A-Z_0-9]*", value) is None:
                raise DecisionPacketError(f"FAILURE {key} is invalid")
            observation[key] = value
    error = failure.get("error")
    if not isinstance(error, dict) or not {"type", "message"} <= set(error):
        raise DecisionPacketError("FAILURE error is not a structured Runtime error")
    error_type = _bounded_text(error["type"], "FAILURE error type")
    message = _bounded_text(error["message"], "FAILURE error message")
    if not error_type or len(error_type) > 256 or not message or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", error_type) is None:
        raise DecisionPacketError("FAILURE error type or message is invalid")
    # Runtime diagnostic messages and structured details can contain local
    # paths, streams, and secrets. Project only the bounded error class.
    observation["error"] = {"type": error_type}
    return subject, asdict(task), None, observation


@dataclass(frozen=True)
class DecisionPacket:
    _body: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return json.loads(_json(self._body))

    def render(self) -> str:
        return _json(self._body)

    @property
    def packet_fingerprint(self) -> str:
        return self._body["packet_fingerprint"]


def compile_decision_packet(
    context: BrainWorkContext, resolution: FlowResolution, material: Mapping[str, Any]
) -> DecisionPacket:
    """Compile a caller-supplied, exact semantic subject with no discovery or mutation."""
    if not isinstance(context, BrainWorkContext) or not isinstance(resolution, FlowResolution):
        raise DecisionPacketError("exact Work Context and Flow Resolution are required")
    try:
        expected = resolve_flow(context)
    except BrainContextError as exc:
        raise DecisionPacketError("invalid or stale Work Context") from exc
    if resolution.as_dict() != expected.as_dict() or resolution.selected_flow not in _FLOWS:
        raise DecisionPacketError("Flow Resolution is absent, NONE, or mismatched")
    if not isinstance(material, Mapping):
        raise DecisionPacketError("typed decision material is required")
    raw = _json(material)
    if len(raw.encode("utf-8")) > _MAX_INPUT:
        raise DecisionPacketError("decision material exceeds input bound")
    supplied = _normal(json.loads(raw))
    _no_packet_nesting(supplied)
    if supplied.get("kind") != resolution.selected_flow:
        raise DecisionPacketError("material kind does not match selected flow")
    observed = _normal(json.loads(_json(context.canonical_observation)))
    request = _normal(json.loads(_json(context.current_request)))
    if request is not None and "human_input" in request:
        if len(request["human_input"].encode("utf-8")) > 16384:
            raise DecisionPacketError("Human input exceeds 16384 UTF-8 bytes")
    flow = resolution.selected_flow
    try:
        if flow == "REMEDIATION_AUTHORING":
            subject, task_facts, claims, prior = _authoring(supplied, observed)
        elif flow == "SEMANTIC_REVIEW":
            subject, task_facts, claims, prior, observations = _semantic_review(supplied, observed)
        elif flow == "REPAIR_AUTHORING":
            subject, task_facts, claims, prior = _repair_authoring(supplied, observed)
        else:
            _mapping(supplied, flow, fields={"kind", "observations"}, required={"kind"})
            notes = supplied.get("observations", [])
            if not isinstance(notes, list) or len(notes) > 32:
                raise DecisionPacketError("invalid bounded observations")
            for note in notes:
                _bounded_text(note, "observation")
            subject, task_facts, claims, prior = {"kind": flow}, None, None, None
    except (TaskValidationError, ArtifactValidationError, ReviewValidationError, RunValidationError, TypeError, KeyError, StopIteration, ValueError) as exc:
        if isinstance(exc, DecisionPacketError):
            raise
        raise DecisionPacketError(f"canonical decision material is invalid: {exc}") from exc
    normalized_basis = _invalidation_basis(observed, request)
    repository = observed.get("repository")
    if not isinstance(repository, dict) or not isinstance(repository.get("name"), str):
        raise DecisionPacketError("canonical repository identity is absent")
    body = {
        "format": "AIOS_DECISION_PACKET", "version": 1, "kind": "DECISION_PACKET",
        "work_context_fingerprint": _digest(normalized_basis),
        "selected_flow": flow, "selection_basis": resolution.selection_basis,
        "authority_owner": resolution.authority_owner,
        "decision_family_ref": resolution.card["decision_family_ref"],
        "handoff_target": resolution.card["handoff_target"],
        "expected_return_shape": resolution.card["expected_return_shape"],
        "pending_canonical_obligation": resolution.pending_canonical_obligation,
        "pending_canonical_authority_owner": resolution.pending_canonical_authority_owner,
        "requires_fresh_context_for_continuation": resolution.requires_fresh_context_for_continuation,
        "canonical_facts": {
            "repository": repository["name"], "main_sha": observed["main_sha"],
            "roadmap": _roadmap_projection(observed["roadmap"]) if flow in {"ARCHITECTURE", "TASK_AUTHORING", "DIAGNOSTIC"} else None,
            "selected_task": observed.get("selected_task"), "task_contract": task_facts,
            "selection_status": observed["selection_status"],
            "lifecycle_state": observed["lifecycle_state"],
            "canonical_next_action": resolution.canonical_next_action,
            "unified_state_next_action": resolution.unified_state_next_action,
        },
        "canonical_blocker": _normal(json.loads(_json(resolution.canonical_blocker))),
        "bounded_observations": notes if flow in {"ARCHITECTURE", "TASK_AUTHORING", "DIAGNOSTIC"} else (observations if flow == "SEMANTIC_REVIEW" else (prior if flow == "REPAIR_AUTHORING" else None)),
        "executor_claims": claims,
        "prior_semantic_decisions": prior if flow == "SEMANTIC_REVIEW" else None,
        "human_input": request.get("human_input") if request else None,
        "subject": subject,
        "run_created": False, "executor_invoked": False,
        "verification_invoked": False, "state_mutated": False,
    }
    _bound_projection(body)
    body["packet_fingerprint"] = _digest(body)
    if len(_json(body).encode("utf-8")) > _MAX_PACKET:
        raise DecisionPacketError("Decision Packet exceeds 131072 UTF-8 bytes")
    return DecisionPacket(body)


__all__ = ["DecisionPacket", "DecisionPacketError", "compile_decision_packet"]
