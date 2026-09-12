"""Authoritative Unified Human Surface boundary for AIOS-renew.

Exposes the thin deterministic Human-facing front door over Unified State,
delegating at most one authoritative operation per invocation without acquiring
planning, review, correction authoring, or publication authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping

from .dispatch_reconciliation import DispatchError
from .unified_state import UnifiedStateObservation

if TYPE_CHECKING:
    from .operator import PreflightResult

NativeRunner = Callable[..., subprocess.CompletedProcess[bytes]]
VerificationRunner = Callable[..., subprocess.CompletedProcess[bytes]]
MonotonicClock = Callable[[], float]

_HUMAN_DISPOSITIONS = frozenset({
    "DELEGATED", "EXECUTOR_REQUIRED", "EXTERNAL_AUTHORITY_REQUIRED",
    "NO_ACTION", "BLOCKED",
})
_HUMAN_AUTHORITIES = frozenset({
    "RUNTIME", "BRAIN", "REVIEWER", "PUBLISHER", "HUMAN", "NONE",
})


@dataclass(frozen=True)
class HumanSurfaceResult:
    """One allowlisted result from the optional unified Human front door."""

    task_id: str
    task_revision: int
    next_action: str
    disposition: str
    authority: str
    delegated_operation: str | None = None
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
    outstanding_findings: tuple[Mapping[str, str], ...] = ()
    executor_required: bool = False
    executor_supplied: bool = False
    executor: str | None = None
    resulting_run_id: str | None = None
    resulting_head_sha: str | None = None
    blocker: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        if self.disposition not in _HUMAN_DISPOSITIONS:
            raise ValueError("invalid Human-surface disposition")
        if self.authority not in _HUMAN_AUTHORITIES:
            raise ValueError("invalid Human-surface authority")
        return {
            "format": "AIOS_HUMAN_SURFACE",
            "version": 1,
            "kind": "HUMAN_SURFACE_RESULT",
            "task": {"id": self.task_id, "revision": self.task_revision},
            "observed_next_action": self.next_action,
            "disposition": self.disposition,
            "authority": self.authority,
            "delegated_operation": self.delegated_operation,
            "selectors": {
                "run_id": self.run_id,
                "source_run_id": self.source_run_id,
                "failed_run_id": self.failed_run_id,
                "review_id": self.review_id,
                "finding_id": self.finding_id,
                "candidate_sha": self.candidate_sha,
                "reviewed_sha": self.reviewed_sha,
                "failed_head_sha": self.failed_head_sha,
                "correction_sha": self.correction_sha,
            },
            "execution_base": (
                {
                    "run_id": self.execution_base_run_id,
                    "candidate_sha": self.execution_base_sha,
                }
                if self.execution_base_run_id is not None
                and self.execution_base_sha is not None
                else None
            ),
            "outstanding_findings": [
                dict(item) for item in self.outstanding_findings
            ],
            "executor": {
                "required": self.executor_required,
                "supplied": self.executor_supplied,
                "identity": self.executor,
            },
            "result": {
                "run_id": self.resulting_run_id,
                "head_sha": self.resulting_head_sha,
            },
            "blocker": dict(self.blocker) if self.blocker is not None else None,
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def _operator():
    import aios_renew.operator as op

    return op


def _human_surface_result(
    observation: UnifiedStateObservation,
    *,
    disposition: str,
    authority: str,
    executor: str | None,
    executor_required: bool = False,
    delegated_operation: str | None = None,
    resulting_run_id: str | None = None,
    resulting_head_sha: str | None = None,
    blocker: Mapping[str, Any] | None = None,
) -> HumanSurfaceResult:
    return HumanSurfaceResult(
        task_id=observation.task_id,
        task_revision=observation.task_revision,
        next_action=observation.next_action,
        disposition=disposition,
        authority=authority,
        delegated_operation=delegated_operation,
        run_id=observation.run_id,
        source_run_id=observation.source_run_id,
        failed_run_id=observation.failed_run_id,
        review_id=observation.review_id,
        finding_id=observation.finding_id,
        candidate_sha=observation.candidate_sha,
        reviewed_sha=observation.reviewed_sha,
        failed_head_sha=observation.failed_head_sha,
        correction_sha=observation.correction_sha,
        execution_base_run_id=observation.execution_base_run_id,
        execution_base_sha=observation.execution_base_sha,
        outstanding_findings=observation.outstanding_findings,
        executor_required=executor_required,
        executor_supplied=executor is not None,
        executor=executor,
        resulting_run_id=resulting_run_id,
        resulting_head_sha=resulting_head_sha,
        blocker=blocker if blocker is not None else observation.blocker,
    )


def _human_surface_delegation_failed(
    observation: UnifiedStateObservation,
    *,
    operation: str,
    executor: str | None,
    executor_required: bool,
) -> tuple[HumanSurfaceResult, int]:
    """Project one canonical operation failure without creating new authority."""

    return (
        _human_surface_result(
            observation,
            disposition="DELEGATED",
            authority="RUNTIME",
            executor=executor,
            executor_required=executor_required,
            delegated_operation=operation,
            blocker={"code": "DELEGATED_OPERATION_FAILED"},
        ),
        1,
    )


def _pre_resolve_continue_task(
    root: Path,
    *,
    task_id: str,
    argv: list[str] | None = None,
    runner: NativeRunner = subprocess.run,
) -> PreflightResult:
    """Resolve an absent valid TASK once before Unified State observation."""

    op = _operator()
    task_path = op._canonical_task_path(root, task_id)
    if task_path.is_file():
        return op.PreflightResult()
    if os.environ.get("AIOS_RESTART_ATTEMPTED") == "1":
        raise op.OperatorError(f"TASK not found: {task_id}")

    preflight = op._preflight_primary_sync(root, argv=argv, runner=runner)
    if preflight.restart_code is not None:
        return preflight
    if not task_path.is_file():
        raise op.OperatorError(f"TASK not found: {task_id}")
    return preflight


def continue_task(
    task_id: str,
    *,
    executor: str | None = None,
    repo: str | Path | None = None,
    argv: list[str] | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
) -> tuple[HumanSurfaceResult | None, int]:
    """Observe once and delegate at most one already-authoritative operation."""

    op = _operator()
    root = op.resolve_repository(repo)
    resolution = _pre_resolve_continue_task(
        root, task_id=task_id, argv=argv, runner=native_runner
    )
    if resolution.restart_code is not None:
        return None, resolution.restart_code
    observation = op.observe_unified_state(task_id, repo=root)
    action = observation.next_action
    repair_executor_required = (
        observation.correction.get("executor_required")
        if isinstance(observation.correction, Mapping)
        else None
    )
    repair_action = (
        observation.correction.get("action")
        if isinstance(observation.correction, Mapping)
        else None
    )
    repair_document_action = (
        observation.correction_document.get("action")
        if isinstance(observation.correction_document, Mapping)
        else None
    )
    if (
        repair_action is not None
        and repair_document_action is not None
        and repair_action != repair_document_action
    ):
        raise op.OperatorError("Unified State repair action identity is inconsistent")
    exact_repair_action = repair_document_action or repair_action
    executor_required = action in ("EXECUTE_PRIMARY", "EXECUTE_REMEDIATION") or (
        action == "EXECUTE_REPAIR"
        and (
            exact_repair_action in ("CODE_FIX", "CONTINUE_IMPLEMENTATION")
            or repair_executor_required is not False
        )
    )
    if executor_required and executor is None:
        return (
            _human_surface_result(
                observation,
                disposition="EXECUTOR_REQUIRED",
                authority="HUMAN",
                executor=None,
                executor_required=True,
            ),
            0,
        )

    external = {
        "SEMANTIC_REVIEW": "REVIEWER",
        "AUTHOR_REMEDIATION": "BRAIN",
        "AUTHOR_REPAIR": "BRAIN",
        "PUBLICATION": "PUBLISHER",
    }
    if action in external:
        return (
            _human_surface_result(
                observation,
                disposition="EXTERNAL_AUTHORITY_REQUIRED",
                authority=external[action],
                executor=executor,
            ),
            0,
        )
    if action in ("WAIT", "DONE"):
        return (
            _human_surface_result(
                observation,
                disposition="NO_ACTION",
                authority="NONE",
                executor=executor,
            ),
            0,
        )
    if action == "NONE":
        return (
            _human_surface_result(
                observation,
                disposition="BLOCKED",
                authority="NONE",
                executor=executor,
            ),
            0,
        )

    resulting_run_id: str | None = None
    resulting_head_sha: str | None = None
    delegated_operation: str
    if action == "EXECUTE_PRIMARY":
        assert executor is not None
        try:
            preflight = op._preflight_primary_admission(
                root,
                task_id=task_id,
                executor=executor,
                argv=argv,
                runner=native_runner,
            )
            if preflight.restart_code is not None:
                # The synchronized child re-enters `continue` and derives state again.
                return None, preflight.restart_code
            summary = op.run_task(
                task_id,
                executor=executor,
                repo=root,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
                synchronize=False,
                preflight_sha=preflight.preflight_sha,
            )
        except (op.OperatorError, DispatchError):
            return _human_surface_delegation_failed(
                observation, operation="PRIMARY", executor=executor,
                executor_required=executor_required,
            )
        delegated_operation = "PRIMARY"
        resulting_run_id = summary.run_id
        resulting_head_sha = summary.head_sha
    elif action == "EXECUTE_REMEDIATION":
        assert executor is not None
        if not all((observation.source_run_id, observation.finding_id,
                    observation.correction_sha)):
            raise op.OperatorError("Unified State remediation selectors are incomplete")
        try:
            summary = op.run_remediation(
                task_id,
                finding_id=observation.finding_id,
                source_run_id=observation.source_run_id,
                approved_remediation_sha=observation.correction_sha,
                executor=executor,
                repo=root,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
        except (op.OperatorError, DispatchError):
            return _human_surface_delegation_failed(
                observation, operation="REMEDIATION", executor=executor,
                executor_required=executor_required,
            )
        delegated_operation = "REMEDIATION"
        resulting_run_id = summary.run_id
        resulting_head_sha = summary.head_sha
    elif action == "EXECUTE_REPAIR":
        if (
            observation.failed_run_id is None
            or observation.correction_sha is None
            or observation.correction_document is None
        ):
            raise op.OperatorError("Unified State repair selectors are incomplete")
        if exact_repair_action == "CONTINUE_IMPLEMENTATION" and executor is None:
            raise op.OperatorError(
                "CONTINUE_IMPLEMENTATION requires an explicit coding Executor"
            )
        try:
            summary = op.run_repair(
                observation.failed_run_id,
                executor=executor,
                repo=root,
                repair=observation.correction_document,
                required_repair_sha=observation.correction_sha,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
        except (op.OperatorError, DispatchError):
            return _human_surface_delegation_failed(
                observation, operation="REPAIR", executor=executor,
                executor_required=executor_required,
            )
        delegated_operation = "REPAIR"
        resulting_run_id = summary.run_id
        resulting_head_sha = summary.head_sha
    elif action == "RETRY_TRANSPORT":
        if observation.run_id is None:
            raise op.OperatorError("Unified State transport selector is incomplete")
        try:
            op.retry_transport(observation.run_id, repo=root)
        except (op.OperatorError, DispatchError):
            return _human_surface_delegation_failed(
                observation, operation="TRANSPORT", executor=executor,
                executor_required=executor_required,
            )
        delegated_operation = "TRANSPORT"
        resulting_run_id = observation.run_id
        resulting_head_sha = observation.candidate_sha
    elif action == "RECOVER_PRIMARY":
        if observation.source_run_id is None:
            raise op.OperatorError("Unified State recovery selector is incomplete")
        try:
            summary = op.recover_primary(
                observation.source_run_id,
                repo=root,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
        except (op.OperatorError, DispatchError):
            return _human_surface_delegation_failed(
                observation, operation="RECOVER_PRIMARY", executor=executor,
                executor_required=executor_required,
            )
        delegated_operation = "RECOVER_PRIMARY"
        resulting_run_id = summary.run_id
        resulting_head_sha = summary.head_sha
    else:
        raise op.OperatorError("Unified State returned an unsupported next action")

    return (
        _human_surface_result(
            observation,
            disposition="DELEGATED",
            authority="RUNTIME",
            executor=executor,
            executor_required=executor_required,
            delegated_operation=delegated_operation,
            resulting_run_id=resulting_run_id,
            resulting_head_sha=resulting_head_sha,
        ),
        0,
    )


__all__ = [
    "HumanSurfaceResult",
    "continue_task",
]
