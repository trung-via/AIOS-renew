"""Authoritative Correction Preflight boundary for AIOS-renew.

Observes exact remote REMEDIATION and REPAIR readiness without acquiring mutation authority,
creating RUNs, invoking Executors, running verification, mutating runtime state, or reconciling repository state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aios_renew.review_transport import RemoteQueryError


_ADMISSION_PHASES = frozenset(
    {
        "PRIMARY_SYNCHRONIZATION",
        "TASK_ADMISSION",
        "FAILED_RUN_RESOLUTION",
        "REMOTE_LINEAGE_RESOLUTION",
        "CANONICAL_CONTRACT_ADMISSION",
        "REPOSITORY_ADMISSION",
        "HISTORICAL_SUBJECT_ADMISSION",
        "REUSABLE_STATE_ADMISSION",
        "RUN_RESERVATION",
    }
)
_ADMISSION_REASONS = frozenset(
    {
        "PRIMARY_SYNCHRONIZATION_REJECTED",
        "TASK_CONTRACT_REJECTED",
        "REMOTE_TRANSPORT_UNAVAILABLE",
        "CANONICAL_LINEAGE_MISSING",
        "CANONICAL_LINEAGE_AMBIGUOUS",
        "CANONICAL_LINEAGE_INVALID",
        "REPOSITORY_ADMISSION_REJECTED",
        "RUN_NAMESPACE_CONFLICT",
        "HISTORICAL_SUBJECT_REJECTED",
        "REUSABLE_STATE_REJECTED",
        "CUMULATIVE_BASE_REJECTED",
        "INTEGRATION_REQUIRED",
    }
)


def _find_remote_query_error(failure: BaseException) -> RemoteQueryError | None:
    current: BaseException | None = failure
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RemoteQueryError):
            return current
        current = current.__cause__ or current.__context__
    return None


@dataclass(frozen=True)
class CorrectionPreflightResult:
    """Bounded, non-authoritative observation of correction admission readiness."""

    family: str
    status: str
    phase: str
    reason_code: str
    task_id: str | None = None
    task_revision: int | None = None
    source_run_id: str | None = None
    failed_run_id: str | None = None
    review_id: str | None = None
    finding_id: str | None = None
    reviewed_sha: str | None = None
    execution_base_run_id: str | None = None
    execution_base_sha: str | None = None
    failed_head_sha: str | None = None
    subject_mode: str | None = None
    action: str | None = None
    executor_required: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "AIOS_CORRECTION_PREFLIGHT",
            "version": 1,
            "kind": "CORRECTION_PREFLIGHT",
            "family": self.family,
            "status": self.status,
            "phase": self.phase,
            "reason_code": self.reason_code,
            "task": (
                {"id": self.task_id, "revision": self.task_revision}
                if self.task_id is not None and self.task_revision is not None
                else None
            ),
            "source_run_id": self.source_run_id,
            "failed_run_id": self.failed_run_id,
            "review_id": self.review_id,
            "finding_id": self.finding_id,
            "reviewed_sha": self.reviewed_sha,
            "execution_base": (
                {
                    "run_id": self.execution_base_run_id,
                    "candidate_sha": self.execution_base_sha,
                }
                if self.execution_base_run_id is not None
                and self.execution_base_sha is not None
                else None
            ),
            "failed_head_sha": self.failed_head_sha,
            "subject_mode": self.subject_mode,
            "action": self.action,
            "executor_required": self.executor_required,
            "run_created": False,
            "executor_invoked": False,
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def _operator():
    import aios_renew.operator as op

    return op


def _blocked_correction_preflight(
    family: str, admission: Mapping[str, Any], failure: BaseException
) -> CorrectionPreflightResult:
    """Project an admission boundary into the bounded observation contract."""

    task = admission.get("task")
    task_id = None
    task_revision = None
    if isinstance(task, Mapping):
        if isinstance(task.get("id"), str) and len(task["id"]) <= 128:
            task_id = task["id"]
        revision = task.get("revision")
        if isinstance(revision, int) and not isinstance(revision, bool):
            task_revision = revision
    phase = admission.get("phase")
    reason_code = admission.get("reason_code")
    if phase not in _ADMISSION_PHASES:
        phase = "CANONICAL_CONTRACT_ADMISSION"
    if reason_code not in _ADMISSION_REASONS:
        reason_code = "CANONICAL_LINEAGE_INVALID"
    if _find_remote_query_error(failure) is not None:
        reason_code = "REMOTE_TRANSPORT_UNAVAILABLE"

    def fact(name: str, limit: int = 256) -> str | None:
        value = admission.get(name)
        return value if isinstance(value, str) and len(value) <= limit else None

    action = fact("action", 32)
    if action not in ("CODE_FIX", "EVIDENCE_ONLY", "NO_CHANGE"):
        action = None
    subject_mode = fact("subject_mode", 16)
    if subject_mode not in ("CURRENT", "HISTORICAL"):
        subject_mode = None
    return CorrectionPreflightResult(
        family=family,
        status="BLOCKED",
        phase=phase,
        reason_code=reason_code,
        task_id=task_id,
        task_revision=task_revision,
        source_run_id=fact("source_run_id"),
        failed_run_id=fact("failed_run_id"),
        review_id=fact("review_id"),
        finding_id=fact("finding_id"),
        reviewed_sha=fact("reviewed_sha", 64),
        execution_base_run_id=fact("execution_base_run_id"),
        execution_base_sha=fact("execution_base_sha", 64),
        failed_head_sha=fact("failed_head_sha", 64),
        subject_mode=subject_mode,
        action=action,
    )


def preflight_remediation(
    task_id: str,
    *,
    finding_id: str,
    repo: str | Path | None = None,
    source_run_id: str | None = None,
    approved_remediation_sha: str | None = None,
) -> CorrectionPreflightResult:
    """Observe exact remote REMEDIATION readiness without creating execution state."""

    op = _operator()
    admission = op._new_admission(
        "REMEDIATION",
        phase="TASK_ADMISSION",
        reason_code="TASK_CONTRACT_REJECTED",
        task_id=task_id,
        finding_id=finding_id,
    )
    if source_run_id is not None:
        admission["source_run_id"] = source_run_id
    try:
        root = op.resolve_repository(repo)
        task = op.load_task(root, task_id)
        op._bind_admission_task(admission, task)
        op._set_admission_boundary(
            admission, "REMOTE_LINEAGE_RESOLUTION", "CANONICAL_LINEAGE_MISSING"
        )
        with op._remote_observation_repository(root) as remote_repo:
            resolved = op._resolve_remediation_admission(
                task_id,
                finding_id=finding_id,
                source_run_id=source_run_id,
                approved_remediation_sha=approved_remediation_sha,
                repo=remote_repo,
                state=op._runtime_paths_readonly(root),
                resolved_task=task,
                admission=admission,
            )
            admission["action"] = resolved.remediation.action
            op._set_admission_boundary(
                admission, "REPOSITORY_ADMISSION", "REPOSITORY_ADMISSION_REJECTED"
            )
            current_head = op._git(root, "rev-parse", "HEAD")
            historical = current_head != resolved.execution_base_sha
            subject_mode = "HISTORICAL" if historical else "CURRENT"
            admission["subject_mode"] = subject_mode
            if op._git(root, "status", "--porcelain"):
                raise op.OperatorError("repository dirty")
            if historical:
                op._set_admission_boundary(
                    admission,
                    "HISTORICAL_SUBJECT_ADMISSION",
                    "HISTORICAL_SUBJECT_REJECTED",
                )
                op._git(
                    remote_repo,
                    "cat-file",
                    "-e",
                    f"{resolved.execution_base_sha}^{{commit}}",
                )
            op._set_admission_boundary(
                admission, "RUN_RESERVATION", "RUN_NAMESPACE_CONFLICT"
            )
            op._remote_run_reservations(
                remote_repo, resolved.task, admission=admission
            )
        return CorrectionPreflightResult(
            family="REMEDIATION",
            status="READY",
            phase="READY",
            reason_code="READY",
            task_id=resolved.task.task_id,
            task_revision=resolved.task.revision,
            source_run_id=admission.get("source_run_id"),
            review_id=resolved.review.review_id,
            finding_id=resolved.remediation.finding_id,
            reviewed_sha=resolved.remediation.reviewed_sha,
            execution_base_run_id=resolved.execution_base_run_id,
            execution_base_sha=resolved.execution_base_sha,
            subject_mode=subject_mode,
            action=resolved.remediation.action,
        )
    except Exception as exc:
        return _blocked_correction_preflight("REMEDIATION", admission, exc)


def preflight_repair(
    failed_run_id: str,
    *,
    repo: str | Path | None = None,
    repair: Mapping[str, Any] | str | Path | None = None,
) -> CorrectionPreflightResult:
    """Observe exact REPAIR readiness without creating execution state."""

    op = _operator()
    admission = op._new_admission(
        "REPAIR",
        phase="FAILED_RUN_RESOLUTION",
        reason_code="CANONICAL_LINEAGE_MISSING",
        failed_run_id=failed_run_id,
    )
    try:
        root = op.resolve_repository(repo)
        state = op._runtime_paths_readonly(root)
        with op._remote_observation_repository(root) as remote_repo:
            resolved = op._resolve_repair_admission(
                failed_run_id,
                repo=root,
                state=state,
                repair=repair,
                admission=admission,
                remote_repo=remote_repo,
            )
            admission["action"] = resolved.action
            subject_mode = "HISTORICAL" if resolved.historical else "CURRENT"
            admission["subject_mode"] = subject_mode
            op._set_admission_boundary(
                admission, "REPOSITORY_ADMISSION", "REPOSITORY_ADMISSION_REJECTED"
            )
            if op._git(root, "status", "--porcelain"):
                raise op.OperatorError("repository dirty")
            failed_head = resolved.failure["failed_head_sha"]
            if resolved.historical:
                op._set_admission_boundary(
                    admission,
                    "HISTORICAL_SUBJECT_ADMISSION",
                    "HISTORICAL_SUBJECT_REJECTED",
                )
                op._git(remote_repo, "cat-file", "-e", f"{failed_head}^{{commit}}")
            elif op._git(root, "rev-parse", "HEAD") != failed_head:
                raise op.OperatorError("current HEAD does not match failed committed state")
            op._set_admission_boundary(
                admission, "RUN_RESERVATION", "RUN_NAMESPACE_CONFLICT"
            )
            op._remote_run_reservations(
                remote_repo, resolved.task, admission=admission
            )
        return CorrectionPreflightResult(
            family="REPAIR",
            status="READY",
            phase="READY",
            reason_code="READY",
            task_id=resolved.task.task_id,
            task_revision=resolved.task.revision,
            failed_run_id=failed_run_id,
            failed_head_sha=failed_head,
            subject_mode=subject_mode,
            action=resolved.action,
            executor_required=resolved.reusable_package is None,
        )
    except Exception as exc:
        return _blocked_correction_preflight("REPAIR", admission, exc)


__all__ = [
    "CorrectionPreflightResult",
    "preflight_remediation",
    "preflight_repair",
]
