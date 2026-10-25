"""Deterministic completion boundary for already-admitted AIOS work."""

from __future__ import annotations

import json
import ast
import hashlib
import os
import re
import stat
import subprocess
import time
from contextlib import contextmanager
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from .antigravity_adapter import AntigravityExecutionError
from .artifacts import (
    ArtifactValidationError,
    Evidence,
    ResultPackage,
    validate_evidence,
    validate_result_package,
    validate_structural_result,
    validate_structural_result_package,
    verification_requirement_passes,
)
from .codex_adapter import CodexExecutionError
from .review import RemediationExecution
from .review_transport import ReviewTransportError, transport_failure, transport_post_pass
from .run import Run
from .run_observation import RunObservationTracker, persist_observation
from .task import Task
from .ka01_admission import guarded as _ka01_guarded
from .verification import (
    RuntimeVerificationError,
    VerificationRunner,
    attach_verification_evidence,
    execute_verification,
    execute_minimum_verification,
    materialize_verification_subject,
)
from .verification_contract import (
    MINIMUM_SUFFICIENT_V2, VERIFICATION_POLICIES,
    normalize_verification, parse_pytest_coverage,
    ProofCheckpointRequest, VerificationContractError, _original_json,
    format_checkpoint_request, checkpoint_request_data, format_scheduler_plan,
    scheduler_plan_data, parse_checkpoint_request,
    verification_digest,
)
from .intra_run_proof_contract import (
    RuntimeCheckpoint, TerminalProofDecision, RUNTIME_CHECKPOINT_FORMAT,
    CHECKPOINT_RESOURCE_LIMITS, checkpoint_resource_facts, checkpoint_population_identity,
    parse_runtime_checkpoint,
    FactualFeedbackRequest, RuntimeFactualFeedback, format_factual_feedback_request,
    format_runtime_feedback, parse_runtime_feedback, _checkpoint_factual_feedback, _blocked_runtime_feedback,
    FeedbackConsumptionRequest, format_feedback_consumption_request, LocalFeedbackConfirmation,
    RuntimeFeedbackConsumption, format_feedback_consumption, parse_feedback_consumption, _consumption_result,
)
from .verification import (
    _checkpoint_reviewed_execution, _execute_checkpoint_verification, _checkpoint_native_facts,
    _original_git, _original_local, MAX_ORIGINAL_PROOF_BYTES,
)

if TYPE_CHECKING:
    from .executor import ExecutorAdapter, ExecutorBoundary
    from .run import RunLease


class RuntimeState(Protocol):
    """Filesystem locations admitted and allocated by the operator."""

    staging: Path
    preverification: Path
    verification: Path
    results: Path
    failures: Path
    observations: Path
    repairs: Path


BoundaryError = type[Exception]
CompletionKind = Literal["PRIMARY", "REMEDIATION", "REPAIR", "DIRECT_CANDIDATE"]
NATIVE_DIAGNOSTIC_LIMIT = 4096


@dataclass(frozen=True)
class _StagedResultReceipt:
    """Process-local Runtime observation, never reconstructible from Executor JSON."""
    path: Path
    run_digest: str
    task_digest: str
    head_sha: str
    sha256: str
    size: int


@dataclass(frozen=True)
class CompletionPolicy:
    """Authorized deterministic rules for one already-admitted completion."""

    kind: CompletionKind
    verification_commands: tuple[str, ...]
    result_base_sha: str
    result_scope: tuple[str, ...]
    require_task_completion: bool
    remediation_execution: RemediationExecution | None = None
    mutation_base_sha: str | None = None
    mutation_scope: tuple[str, ...] = ()
    mutation_action: str | None = None
    lineage_path: Path | None = None
    stage_structural_package: bool = True


@dataclass(frozen=True)
class CompletionOutcome:
    """Canonical state produced before returning to a Human-facing summary."""

    head_sha: str
    result_path: Path
    observation_path: Path | None


def primary_completion_policy(task: Task, *, base_sha: str) -> CompletionPolicy:
    return CompletionPolicy(
        kind="PRIMARY",
        verification_commands=task.verification.required,
        result_base_sha=base_sha,
        result_scope=task.scope.modify,
        require_task_completion=True,
    )


def remediation_completion_policy(
    execution: RemediationExecution, *, direct_candidate: bool = False
) -> CompletionPolicy:
    remediation = execution.remediation
    return CompletionPolicy(
        kind="DIRECT_CANDIDATE" if direct_candidate else "REMEDIATION",
        verification_commands=remediation.affected_verification,
        result_base_sha=remediation.reviewed_sha,
        result_scope=remediation.modification_scope,
        require_task_completion=False,
        remediation_execution=execution,
        mutation_action=remediation.action,
        stage_structural_package=not direct_candidate,
    )


def repair_completion_policy(
    task: Task,
    *,
    root_base_sha: str,
    result_base_sha: str | None = None,
    failed_head_sha: str,
    action: str,
    modification_scope: Sequence[str],
    lineage_path: Path,
    origin_affected_verification: Sequence[str] = (),
) -> CompletionPolicy:
    verification_commands = task.verification.required
    if origin_affected_verification:
        combined = (*verification_commands, *origin_affected_verification)
        if task.verification.policy == MINIMUM_SUFFICIENT_V2:
            verification_commands = tuple(dict.fromkeys((*origin_affected_verification, *verification_commands)))
        elif task.verification.policy in VERIFICATION_POLICIES:
            verification_commands = normalize_verification(combined)
        else:
            verification_commands = tuple(dict.fromkeys(combined))
    if task.verification.policy == MINIMUM_SUFFICIENT_V2:
        # Normalization cannot remove an exact authored integration guard.
        verification_commands = tuple(dict.fromkeys((*verification_commands, *(
            c for c in task.verification.required
            if (coverage := parse_pytest_coverage(c)) is not None and coverage.is_full_suite
        ))))
    return CompletionPolicy(
        kind="REPAIR",
        verification_commands=verification_commands,
        result_base_sha=(
            root_base_sha if result_base_sha is None else result_base_sha
        ),
        result_scope=task.scope.modify,
        require_task_completion=True,
        mutation_base_sha=failed_head_sha,
        mutation_scope=tuple(modification_scope),
        mutation_action=action,
        lineage_path=lineage_path,
    )


class RuntimeCompletion:
    """Own canonical completion after dispatch or direct-candidate admission.

    This boundary observes repository truth, applies deterministic completion
    gates, runs the authorized command list once, binds canonical EVIDENCE,
    persists terminal state, records subordinate observation, and transports the
    immutable terminal artifacts. It has no dispatch, review, or recovery policy.
    """

    def __init__(
        self,
        *,
        repo: Path,
        state: RuntimeState,
        task: Task,
        run: Run,
        run_path: Path,
        verification_runner: VerificationRunner,
        observation_tracker: RunObservationTracker | None,
        error_type: BoundaryError,
        transport_repo: Path | None = None,
    ) -> None:
        self._ka01_started_ns = time.monotonic_ns()
        self._ka01_started_real_ns = time.time_ns()
        self._ka01_original_tracker = observation_tracker
        self._ka01_admitted_at = getattr(observation_tracker, "_admitted_at", None)
        if (type(observation_tracker) is RunObservationTracker and observation_tracker._clock is time.monotonic
                and observation_tracker._run == run and type(self._ka01_admitted_at) is float):
            admitted_ns = int(self._ka01_admitted_at * 1_000_000_000)
            self._ka01_started_real_ns -= self._ka01_started_ns - admitted_ns
            self._ka01_started_ns = admitted_ns
        self._ka01_opted = False
        self._ka01_budget = None
        self.repo = repo
        self.state = state
        self.task = task
        self.run = run
        self.run_path = run_path
        self.verification_runner = verification_runner
        self.observation_tracker = observation_tracker
        self.error_type = error_type
        self.transport_repo = transport_repo or repo
        self.interruption_phase = "COMPLETION_GATE"
        self.verification_subject_sha: str | None = None
        self._staged_result_receipt: _StagedResultReceipt | None = None
        # Process-local capabilities; disk ACKs can never reconstruct these.
        self._local_feedback_identity: str | None = None
        self._local_feedback_invocation = None
        self._local_feedback_invocation_bytes: bytes | None = None
        self._local_feedback_stopped = False
        self._local_feedback_receipts: dict[int, bytes] = {}

    def _checkpoint_directory(self) -> Path:
        """Derive storage from the existing admitted Git/Runtime owner only."""
        root = Path(_original_git(self.repo, "rev-parse", "--absolute-git-dir").decode("utf-8").strip()) / "aios"
        if (self.repo.absolute().resolve(strict=True) != self.repo.absolute()
                or self.state.verification.absolute() != root / "verification"
                or self.state.results.absolute() != root / "results"
                or self.state.failures.absolute() != root / "failures"
                or self.state.preverification.absolute() != root / "preverification"
                or self.run_path.absolute() != root / "runs" / (self.run.run_id + ".json")):
            raise VerificationContractError("CHECKPOINT_EXISTING_RUNTIME_OWNERSHIP_REQUIRED")
        _checkpoint_safe_directory(root)
        directory = root / "verification" / self.run.run_id / "l3-02"
        if directory.parent.exists():
            _checkpoint_safe_directory(directory.parent)
            with os.scandir(directory.parent) as entries:
                for index, entry in enumerate(entries):
                    allowed = {"l3-02", ".runtime-mutation", "local-invocation.json"}
                    if self._ka01_opted:
                        allowed.add("ka01-v13")
                    if index >= len(allowed) or entry.name not in allowed:
                        raise VerificationContractError("CHECKPOINT_PREEXISTING_OR_UNKNOWN_EXECUTION_EFFECT")
        return directory

    @contextmanager
    def _checkpoint_effect(self, directory, intent):
        """Subordinate v1.3 reservations share the original mutation fence."""
        if self._ka01_budget is not None:
            self._ka01_budget.capacity(intent)
        with _checkpoint_mutation(directory, intent):
            if self._ka01_budget is None:
                if self._ka01_opted:
                    raise VerificationContractError("KA01_V13_OPTED_RUN_STOP_LATCHED")
                yield
            else:
                with self._ka01_budget.effect(intent):
                    yield
                    if intent["kind"] == "CHECKPOINT":
                        history = self._checkpoint_history(directory)
                        if len(history) > 1 and not set(history[-1].data["outstanding"]) < set(history[-2].data["outstanding"]):
                            raise VerificationContractError("KA01_V13_CAUSAL_PROGRESS_NOT_ESTABLISHED")

    def _ka01_verification_runner(self, command, **kwargs):
        """Bound each existing verifier call by the original remaining time."""
        budget = self._ka01_budget
        budget.guard(live=not budget.production)
        remaining = budget.remaining_seconds()
        current = kwargs.get("timeout")
        kwargs["timeout"] = remaining if current is None else min(current, remaining)
        result = self.verification_runner(command, **kwargs)
        budget.guard(live=not budget.production)
        budget.remaining_seconds()
        return result

    def _checkpoint_binding(self, request: ProofCheckpointRequest, *, terminal=False):
        format_checkpoint_request(request)
        target = request.scheduler.target
        from .publication import _run_from_data
        admitted = _run_from_data(json.loads(_original_local(self.run_path.absolute(), MAX_ORIGINAL_PROOF_BYTES)), "L3-02 admitted RUN")
        if (admitted != self.run or self.run.status != "ACTIVE" or request.scheduler.operation != "PRIMARY"
                or (target.task_id, target.task_revision, target.run_id, target.base_sha) != (
                    self.task.task_id, self.task.revision, self.run.run_id, self.run.base_sha)
                or self.run.task.id != self.task.task_id or self.run.task.revision != self.task.revision):
            raise VerificationContractError("CHECKPOINT_CURRENT_OPERATION_ADMISSION_NOT_ESTABLISHED")
        directory = self._checkpoint_directory()
        if os.path.lexists(self.state.failures / (self.run.run_id + ".json")):
            raise VerificationContractError("CHECKPOINT_RUN_ALREADY_FAILURE")
        if not terminal and (os.path.lexists(self.state.results / (self.run.run_id + ".json"))
                or os.path.lexists(directory / "terminal-intent.json")):
            raise VerificationContractError("CHECKPOINT_LATE_OR_AMBIGUOUS_TERMINAL")
        return directory

    def _checkpoint_history(self, directory: Path, *, check_captures=True):
        history = []
        if not directory.exists():
            return history
        _checkpoint_safe_directory(directory)
        with os.scandir(directory) as entries:
            names = []
            for entry in entries:
                if len(names) >= 72:
                    raise VerificationContractError("CHECKPOINT_HISTORY_BOUND")
                names.append(entry.name)
        allowed = {"terminal-intent.json"}
        if any(name not in allowed and re.fullmatch(r"k(?:00[1-9]|01[0-7])(?:\.json|\.feedback\.json|\.consumption\.json)?", name) is None for name in names):
            raise VerificationContractError("CHECKPOINT_CONFLICTING_HISTORY")
        run_digest = _checkpoint_digest(_original_local(self.run_path.absolute(), MAX_ORIGINAL_PROOF_BYTES))
        for name in sorted(n for n in names if re.fullmatch(r"k[0-9]{3}\.json", n)):
            record = parse_runtime_checkpoint(_original_local(directory / name, MAX_ORIGINAL_PROOF_BYTES))
            data, previous = record.data, history[-1] if history else None
            request = parse_checkpoint_request(_original_json(data["request"]).encode("ascii"))
            target = request.scheduler.target
            if ((target.task_id, target.task_revision, target.run_id, target.base_sha, target.run_sha256) != (
                    self.task.task_id, self.task.revision, self.run.run_id, self.run.base_sha, run_digest)
                    or request.scheduler.operation != "PRIMARY"):
                raise VerificationContractError("CHECKPOINT_HISTORY_ADMISSION_OR_OPERATION_CONFLICT")
            if (name != f"k{len(history) + 1:03d}.json" or request.ordinal != len(history) + 1
                    or request.predecessor_digest != (None if previous is None else previous.digest)
                    or data["resources_before"] != ({k: 0 for k in CHECKPOINT_RESOURCE_LIMITS} if previous is None else previous.data["resources_after"])
                    or any(r.data["request"]["scheduler"]["subject_sha"] == request.scheduler.subject_sha for r in history)):
                raise VerificationContractError("CHECKPOINT_NONCONSECUTIVE_OR_CONFLICTING_HISTORY")
            if previous is not None:
                prior_request = parse_checkpoint_request(_original_json(previous.data["request"]).encode("ascii"))
                old, new = prior_request.scheduler.target, request.scheduler.target
                if ((old.task_id, old.task_revision, old.run_id, old.base_sha, old.run_sha256) != (
                        new.task_id, new.task_revision, new.run_id, new.base_sha, new.run_sha256)
                        or previous.data["population_identity"] != data["population_identity"]
                        or previous.data["execution_identity"] != data["execution_identity"]
                        or old.tree_sha == new.tree_sha):
                    raise VerificationContractError("CHECKPOINT_CHANGED_AUTHORITY_OR_MANDATORY_POPULATION")
                # Raw immutable Git traversal is unaffected by replace/graft refs.
                from .verification import _ApplicabilityReader
                reader = _ApplicabilityReader(self.repo)
                reader.anchors(new.candidate_sha, {old.candidate_sha})
            if request.scheduler.subject_sha == request.scheduler.target.base_sha:
                raise VerificationContractError("CHECKPOINT_CANDIDATE_IS_BASE")
            if check_captures:
                evidence = tuple(validate_evidence(item) for item in data["evidence"])
                raw_directory = directory / f"k{request.ordinal:03d}"
                _checkpoint_capture_inventory(raw_directory, evidence)
                for fact in data["proofs"]:
                    order = int(fact["evidence_id"].rsplit("-V", 1)[1])
                    raw = _original_local(raw_directory / (fact["evidence_id"] + ".raw"), 32 * 1024 * 1024)
                    native = _original_local(raw_directory / f"observation-{order:03d}.json", 16 * 1024 * 1024)
                    if ((_checkpoint_digest(raw), len(raw), _checkpoint_digest(native), len(native)) != (
                            fact["raw_sha256"], fact["raw_size"], fact["native_sha256"], fact["native_size"])):
                        raise VerificationContractError("CHECKPOINT_ORIGINAL_HISTORY_CAPTURE_CHANGED")
            history.append(record)
        if any(name.startswith("k") and not name.endswith(".json") and name + ".json" not in names for name in names):
            raise VerificationContractError("CHECKPOINT_UNKNOWN_EXECUTION_EFFECT")
        # F_i is subordinate to its original K_i in this same bounded store.
        # There is no mutable feedback index, independent owner or recovery path.
        for name in sorted(n for n in names if n.endswith(".feedback.json")):
            ordinal = int(name[1:4])
            if ordinal > len(history):
                raise VerificationContractError("FEEDBACK_WITHOUT_ORIGINAL_CHECKPOINT")
            original = parse_runtime_feedback(_original_local(directory / name, MAX_ORIGINAL_PROOF_BYTES))
            expected = _checkpoint_factual_feedback(history[ordinal - 1], self.run.executor)
            if original.data["status"] != "PREPARED" or format_runtime_feedback(original) != format_runtime_feedback(expected):
                raise VerificationContractError("FEEDBACK_IMMUTABLE_IDENTITY_OR_CONTENT_CONFLICT")
        for name in sorted(n for n in names if n.endswith(".consumption.json")):
            ordinal = int(name[1:4])
            if ordinal > len(history) or f"k{ordinal:03d}.feedback.json" not in names:
                raise VerificationContractError("CONSUMPTION_WITHOUT_ORIGINAL_FEEDBACK")
            original = parse_feedback_consumption(_original_local(directory / name, MAX_ORIGINAL_PROOF_BYTES)).data
            expected = _checkpoint_factual_feedback(history[ordinal - 1], self.run.executor)
            binding = original["binding"]
            if (original["status"] != "LOCAL_ACKNOWLEDGED" or binding is None
                    or any(binding[k] != expected.data["binding"][k] for k in expected.data["binding"] if k in binding)
                    or binding["feedback_id"] != expected.data["feedback_id"]
                    or binding["feedback_digest"] != expected.digest
                    or original["resources"] != history[ordinal - 1].data["resources_after"]):
                raise VerificationContractError("CONSUMPTION_IMMUTABLE_SOURCE_CONFLICT")
        return history

    def _authenticate_checkpoint(self, request, digest, *, terminal=False):
        directory = self._checkpoint_binding(request, terminal=terminal)
        history = self._checkpoint_history(directory)
        if not history or history[-1].digest != digest or history[-1].data["request"] != checkpoint_request_data(request):
            raise VerificationContractError("CHECKPOINT_NOT_EXACT_CURRENT_FINAL_SEAL")
        record = history[-1]
        data = record.data
        plan, task, manifests = _checkpoint_reviewed_execution(self.repo, request,
            expected_plan=_original_json(data["plan"]).encode("ascii"))
        if task != self.task:
            raise VerificationContractError("CHECKPOINT_TASK_OBJECT_SUBSTITUTION")
        evidence = tuple(validate_evidence(item) for item in data["evidence"])
        _checkpoint_capture_inventory(directory / f"k{request.ordinal:03d}", evidence)
        facts = _checkpoint_native_facts(self.repo, request=request, manifests=manifests,
            evidence=evidence, raw_directory=directory / f"k{request.ordinal:03d}")
        if facts != data["proofs"]:
            raise VerificationContractError("CHECKPOINT_NATIVE_PROOF_CHANGED")
        # Recheck all roots and all raw observations after the last proof read.
        _, _, current = _checkpoint_reviewed_execution(self.repo, request, expected_plan=format_scheduler_plan(plan))
        if _checkpoint_native_facts(self.repo, request=request, manifests=current, evidence=evidence,
                raw_directory=directory / f"k{request.ordinal:03d}") != facts:
            raise VerificationContractError("CHECKPOINT_PROOF_CHANGED_AT_FINAL_BOUNDARY")
        self._checkpoint_binding(request, terminal=terminal)
        if self._checkpoint_history(directory) != history:
            raise VerificationContractError("CHECKPOINT_HISTORY_CHANGED_AT_FINAL_BOUNDARY")
        return record

    @_ka01_guarded
    def seal_checkpoint(self, request: ProofCheckpointRequest) -> RuntimeCheckpoint:
        """Explicit offline/prospective effect under this admitted Runtime owner.

        Authenticates the complete reviewed native execution mapping, reserves
        one immutable effect, invokes the existing verifier once, then seals.
        No caller evidence, plan validity flag, command or runner is an input.
        """
        try:
            if self._local_feedback_invocation is not None and self._local_feedback_stopped:
                raise VerificationContractError("LOCAL_FEEDBACK_STOP_LATCHED")
            return self._seal_checkpoint(request)
        except BaseException:
            if self._local_feedback_invocation is not None:
                self._local_feedback_stopped = True
            raise

    def _seal_checkpoint(self, request: ProofCheckpointRequest) -> RuntimeCheckpoint:
        started = time.monotonic_ns()
        directory = self._checkpoint_binding(request)
        history = self._checkpoint_history(directory)
        if _checkpoint_effect_present(directory):
            raise VerificationContractError("CHECKPOINT_COMPETING_OR_INTERRUPTED_EFFECT")
        if request.ordinal <= len(history):
            record = history[request.ordinal - 1]
            if record.data["request"] != checkpoint_request_data(request):
                raise VerificationContractError("CHECKPOINT_IDENTITY_COLLISION")
            # Only the current candidate can reconcile; old seals never reopen.
            if self._ka01_budget is None:
                return self._authenticate_checkpoint(request, record.digest)
            with self._checkpoint_effect(directory, dict(version=1, kind="CHECKPOINT_REPLAY", digest=record.digest)):
                return self._authenticate_checkpoint(request, record.digest)
        if request.ordinal != len(history) + 1 or request.predecessor_digest != (history[-1].digest if history else None):
            raise VerificationContractError("CHECKPOINT_GAP_OR_PREDECESSOR_CONFLICT")
        plan, task, manifests = _checkpoint_reviewed_execution(self.repo, request)
        if task != self.task or request.scheduler.subject_sha == self.run.base_sha:
            raise VerificationContractError("CHECKPOINT_CURRENT_TASK_OR_CANDIDATE_CONFLICT")
        execution_identity = verification_digest([dict(proof_id=proof_id,
            command=task.verification.required[manifest["command_index"]], profile=manifest["profile"],
            toolchain=manifest["toolchain"], collection=manifest["collection"])
            for proof_id, (_, manifest, _) in sorted(manifests.items())])
        before = history[-1].data["resources_after"] if history else {k: 0 for k in CHECKPOINT_RESOURCE_LIMITS}
        if (any(before[k] >= limit for k, limit in CHECKPOINT_RESOURCE_LIMITS.items())
                or before["executions"] + len(task.verification.required) > CHECKPOINT_RESOURCE_LIMITS["executions"]):
            raise VerificationContractError("CHECKPOINT_EXHAUSTED_RESOURCE_ACCOUNTING")
        if history:
            prior = history[-1].data
            old = prior["request"]["scheduler"]["target"]
            if (prior["population_identity"] != checkpoint_population_identity(plan)
                    or prior["execution_identity"] != execution_identity
                    or old["tree_sha"] == request.scheduler.target.tree_sha
                    or old["candidate_sha"] == request.scheduler.subject_sha):
                raise VerificationContractError("CHECKPOINT_UNCHANGED_CANDIDATE_OR_ALTERED_MANDATORY_PROOF")
            from .verification import _ApplicabilityReader
            _ApplicabilityReader(self.repo).anchors(request.scheduler.subject_sha, {old["candidate_sha"]})
        intent = dict(version=1, kind="CHECKPOINT", request=checkpoint_request_data(request),
            plan_sha256=_checkpoint_digest(format_scheduler_plan(plan)), resources_before=before)
        with self._checkpoint_effect(directory, intent):
            self._checkpoint_binding(request)
            if self._checkpoint_history(directory) != history:
                raise VerificationContractError("CHECKPOINT_COMPETING_MUTATION")
            _checkpoint_reviewed_execution(self.repo, request, expected_plan=format_scheduler_plan(plan))
            raw_directory = directory / f"k{request.ordinal:03d}"
            raw_directory.mkdir()  # Exclusive. Never erase an interrupted proof.
            try:
                evidence = _execute_checkpoint_verification(self.repo, request=request, task=task, manifests=manifests,
                    raw_directory=raw_directory, start_order=before["executions"],
                    runner=self.verification_runner if self._ka01_budget is None else self._ka01_verification_runner,
                    timeout_seconds=(CHECKPOINT_RESOURCE_LIMITS["elapsed_ms"] - before["elapsed_ms"]) / 1000,
                    capture_bytes_remaining=CHECKPOINT_RESOURCE_LIMITS["raw_bytes"] - before["raw_bytes"])
            except OSError as exc:
                # Missing/unreadable native captures cannot establish a seal.
                # Keep the reserved fence and original partial capture intact.
                raise VerificationContractError("CHECKPOINT_NATIVE_CAPTURE_UNAVAILABLE") from exc
            for path in _checkpoint_capture_inventory(raw_directory, evidence):
                _original_local(path, 32 * 1024 * 1024)
                with path.open("r+b") as stream:
                    os.fsync(stream.fileno())
            _checkpoint_sync_directory(raw_directory)
            facts = _checkpoint_native_facts(self.repo, request=request, manifests=manifests,
                evidence=evidence, raw_directory=raw_directory)
            _checkpoint_reviewed_execution(self.repo, request, expected_plan=format_scheduler_plan(plan))
            self._checkpoint_binding(request)
            after = dict(checkpoints=request.ordinal, executions=before["executions"] + len(evidence),
                raw_bytes=before["raw_bytes"] + sum(p["raw_size"] + p["native_size"] for p in facts),
                elapsed_ms=before["elapsed_ms"] + (time.monotonic_ns() - started + 999_999) // 1_000_000)
            checkpoint_resource_facts(after)
            outstanding = sorted({oid for fact in facts if not fact["satisfactory"]
                for oid in manifests[fact["proof_id"]][0]["obligation_ids"]})
            content = _checkpoint_json(dict(format=RUNTIME_CHECKPOINT_FORMAT, version=1,
                request=checkpoint_request_data(request), plan=scheduler_plan_data(plan),
                population_identity=checkpoint_population_identity(plan), execution_identity=execution_identity,
                evidence=[evidence_data(e) for e in evidence],
                proofs=facts, outstanding=outstanding, affected=[], resources_before=before, resources_after=after))
            record = parse_runtime_checkpoint(content)
            _checkpoint_write_once(directory / f"k{request.ordinal:03d}.json", content)
            self._authenticate_checkpoint(request, record.digest)
            return record

    @_ka01_guarded
    def prepare_feedback(self, request: FactualFeedbackRequest) -> RuntimeFactualFeedback:
        """Explicit inert L3-03 preparation; no proof execution or continuation.

        Only the exact latest native checkpoint of this admitted Runtime owner
        can produce durable factual F_i. Parsed records, caller outcomes and
        correction labels confer no authority. All failures expose fixed codes,
        never exception/log text or user filesystem paths.
        """
        if self._local_feedback_invocation is not None and self._local_feedback_stopped:
            return _blocked_runtime_feedback("NATIVE_CHECKPOINT_NOT_ESTABLISHED")
        format_factual_feedback_request(request)
        checkpoint = request.checkpoint
        operation = checkpoint.scheduler.operation
        if operation in ("REPAIR", "REMEDIATION"):
            if self._local_feedback_invocation is not None:
                self._local_feedback_stopped = True
            return _blocked_runtime_feedback("CORRECTION_NATIVE_AUTHORITY_NOT_ESTABLISHED")
        if operation != "PRIMARY":
            if self._local_feedback_invocation is not None:
                self._local_feedback_stopped = True
            return _blocked_runtime_feedback("OPERATION_NOT_SUPPORTED")
        try:
            directory = self._checkpoint_binding(checkpoint)
            if _checkpoint_effect_present(directory):
                raise VerificationContractError("FEEDBACK_COMPETING_OR_INTERRUPTED_EFFECT")
            record = self._authenticate_checkpoint(checkpoint, request.checkpoint_digest)
            expected = _checkpoint_factual_feedback(record, self.run.executor)
            path = directory / f"k{checkpoint.ordinal:03d}.feedback.json"

            def reconcile():
                original = parse_runtime_feedback(_original_local(path, MAX_ORIGINAL_PROOF_BYTES))
                if (original.data["status"] != "PREPARED"
                        or format_runtime_feedback(original) != format_runtime_feedback(expected)):
                    raise VerificationContractError("FEEDBACK_IMMUTABLE_IDENTITY_OR_CONTENT_CONFLICT")
                return original

            if os.path.lexists(path):
                if self._ka01_budget is not None:
                    with self._checkpoint_effect(directory, dict(version=1, kind="FEEDBACK_REPLAY", digest=expected.digest)):
                        original = reconcile()
                        self._authenticate_checkpoint(checkpoint, request.checkpoint_digest)
                        return original
                original = reconcile()
                self._authenticate_checkpoint(checkpoint, request.checkpoint_digest)
                if _checkpoint_effect_present(directory):
                    raise VerificationContractError("FEEDBACK_COMPETING_OR_INTERRUPTED_EFFECT")
                return original
            if expected.data["status"] != "PREPARED":
                # PASS and authenticated unresolved facts are read-only results.
                self._authenticate_checkpoint(checkpoint, request.checkpoint_digest)
                if _checkpoint_effect_present(directory):
                    raise VerificationContractError("FEEDBACK_COMPETING_OR_INTERRUPTED_EFFECT")
                if expected.data["status"] == "BLOCKED" and self._local_feedback_invocation is not None:
                    self._local_feedback_stopped = True
                return expected
            intent = dict(version=1, kind="FACTUAL_FEEDBACK", run_id=self.run.run_id,
                checkpoint_digest=record.digest, feedback_id=expected.data["feedback_id"], feedback_digest=expected.digest)
            with self._checkpoint_effect(directory, intent):
                # Same exclusive Runtime fence as checkpoint, RESULT and FAILURE.
                current = self._authenticate_checkpoint(checkpoint, request.checkpoint_digest)
                if format_runtime_feedback(_checkpoint_factual_feedback(current, self.run.executor)) != format_runtime_feedback(expected):
                    raise VerificationContractError("FEEDBACK_CHANGED_AT_EFFECT_BOUNDARY")
                if not os.path.lexists(path):
                    _checkpoint_write_once(path, format_runtime_feedback(expected))
                original = reconcile()
                self._authenticate_checkpoint(checkpoint, request.checkpoint_digest)
                return original
        except (ValueError, OSError, RuntimeError, TypeError, KeyError):
            if self._local_feedback_invocation is not None:
                self._local_feedback_stopped = True
            # Unknown/partial effects retain the existing fence and bytes. Never
            # erase, replace, rerun proof or return a raw diagnostic as F_i.
            return _blocked_runtime_feedback("NATIVE_CHECKPOINT_NOT_ESTABLISHED")

    def _begin_local_feedback_invocation(self, *, boundary, frame):
        """Reserve one explicit invocation, without creating a durable lease.

        An interrupted or reconstructed owner cannot invoke execute() again.
        Liveness still requires the original active frame and registry holder.
        """
        from .publication import _run_from_data
        try:
            if self._ka01_opted:
                if self._ka01_budget is None:
                    raise VerificationContractError("KA01_V13_OPTED_RUN_STOP_LATCHED")
                self._ka01_budget.bind(boundary, frame)
            if (self._local_feedback_stopped or self._local_feedback_invocation is not None
                    or frame.owner is not self or frame.task != self.task or frame.run != self.run
                    or boundary._live is not frame):
                raise VerificationContractError("original Runtime invocation required")
            directory = self._checkpoint_directory()
            path = directory.parent / "local-invocation.json"
            if _checkpoint_effect_present(directory):
                raise VerificationContractError("unknown or competing original invocation")
            admitted = _original_local(self.run_path.absolute(), MAX_ORIGINAL_PROOF_BYTES)

            def current():
                if (self._local_feedback_stopped
                        or _run_from_data(json.loads(admitted), "L3-04 original RUN") != self.run
                        or self.run.status != "ACTIVE"
                        or _original_local(self.run_path.absolute(), MAX_ORIGINAL_PROOF_BYTES) != admitted
                        or os.path.lexists(path)
                        or any(os.path.lexists(p) for p in (
                            self.state.results / (self.run.run_id + ".json"),
                            self.state.failures / (self.run.run_id + ".json"), directory / "terminal-intent.json"))):
                    raise VerificationContractError("original invocation unavailable or terminal")

            current()
            self._local_feedback_identity = hashlib.sha256(os.urandom(32)).hexdigest()
            content = _checkpoint_json(dict(version=1, kind="LOCAL_INVOCATION", run_id=self.run.run_id,
                admitted_run_sha256=_checkpoint_digest(admitted), runtime_identity=self._local_feedback_identity,
                invocation_identity=_checkpoint_digest(frame.identity.encode("ascii"))))
            with boundary._leases.held(frame.lease):
                with _checkpoint_mutation(directory, json.loads(content)):
                    current()
                    _checkpoint_write_once(path, content)
                    if _original_local(path, MAX_ORIGINAL_PROOF_BYTES) != content:
                        raise VerificationContractError("unknown original invocation intent")
                self._local_feedback_invocation = (boundary, frame.identity)
                self._local_feedback_invocation_bytes = content
        except BaseException:
            self._local_feedback_stopped = True
            raise

    @_ka01_guarded
    def consume_same_executor_feedback(self, request: FeedbackConsumptionRequest, *,
            boundary: ExecutorBoundary, lease: RunLease, adapter: ExecutorAdapter) -> RuntimeFeedbackConsumption:
        """Explicit preactivation observation inside the original execute().

        Authenticate existing sealed proof without executing proof or dispatch.
        Only a direct confirmation by that same live adapter acknowledges the
        local observation. C2 and production authority remain denied.
        """
        try:
            format_feedback_consumption_request(request)
        except (ValueError, TypeError, AttributeError):
            self._local_feedback_stopped = True
            raise
        if self._local_feedback_stopped:
            return _consumption_result(reason="STOP_LATCHED")
        if request.source.checkpoint.scheduler.operation != "PRIMARY":
            self._local_feedback_stopped = True
            return _consumption_result(reason="CORRECTION_NATIVE_AUTHORITY_NOT_ESTABLISHED")
        reason = "LIVE_INVOCATION_NOT_ESTABLISHED"
        try:
            from .executor import ExecutorBoundary
            if type(boundary) is not ExecutorBoundary:
                raise VerificationContractError("original native boundary required")
            with boundary._same_invocation(owner=self, task=self.task, run=self.run,
                    lease=lease, adapter=adapter) as (frame, live):
                if self._local_feedback_invocation != (boundary, frame.identity):
                    raise VerificationContractError("replacement invocation forbidden")
                reason = "NATIVE_SOURCE_OR_EFFECT_NOT_ESTABLISHED"
                source = request.source
                checkpoint = source.checkpoint
                directory = self._checkpoint_binding(checkpoint)
                intent = dict(version=1, kind="LOCAL_FEEDBACK", run_id=self.run.run_id,
                    checkpoint_digest=source.checkpoint_digest, feedback_digest=request.feedback_digest,
                    runtime_identity=self._local_feedback_identity,
                    invocation_identity=_checkpoint_digest(frame.identity.encode("ascii")))

                def authenticate(*, pending_receipt=None):
                    if self._local_feedback_stopped:
                        raise VerificationContractError("local continuation already stopped")
                    live()
                    if (self._local_feedback_invocation_bytes is None or _original_local(
                            directory.parent / "local-invocation.json", MAX_ORIGINAL_PROOF_BYTES)
                            != self._local_feedback_invocation_bytes):
                        raise VerificationContractError("original invocation intent not established")
                    record = self._authenticate_checkpoint(checkpoint, source.checkpoint_digest)
                    feedback = _checkpoint_factual_feedback(record, self.run.executor)
                    original = _original_local(directory / f"k{checkpoint.ordinal:03d}.feedback.json", MAX_ORIGINAL_PROOF_BYTES)
                    if (feedback.data["status"] != "PREPARED" or feedback.digest != request.feedback_digest
                            or original != format_runtime_feedback(feedback)):
                        raise VerificationContractError("original decisive current feedback required")
                    # Prior receipts must belong to this actual callback lineage;
                    # serialized ACKs never establish a reconstructed session.
                    for prior in self._checkpoint_history(directory):
                        ordinal = prior.data["request"]["ordinal"]
                        receipt_path = directory / f"k{ordinal:03d}.consumption.json"
                        retained = self._local_feedback_receipts.get(ordinal)
                        if not os.path.lexists(receipt_path):
                            if retained is not None:
                                raise VerificationContractError("original local receipt removed")
                            continue
                        original_receipt = _original_local(receipt_path, MAX_ORIGINAL_PROOF_BYTES)
                        local = parse_feedback_consumption(original_receipt).data["binding"]
                        allowed = retained
                        if ordinal == checkpoint.ordinal and pending_receipt is not None:
                            allowed = pending_receipt
                        if (original_receipt != allowed or local["runtime_identity"] != self._local_feedback_identity
                                or local["invocation_identity"] != intent["invocation_identity"]):
                            raise VerificationContractError("original callback lineage not established")
                    live()
                    return record, feedback

                # Use the original Runtime fence for both effects and replay.
                with self._checkpoint_effect(directory, intent):
                    record, feedback = authenticate()
                    _local_feedback_fence(directory, intent)
                    resources = record.data["resources_after"]
                    if any(resources[k] >= v for k, v in CHECKPOINT_RESOURCE_LIMITS.items()):
                        reason = "CHECKPOINT_RESOURCE_LIMIT_REACHED"
                        raise VerificationContractError("finite native resource bound reached")
                    binding = {k: v for k, v in feedback.data["binding"].items() if k in (
                        "task_id", "task_revision", "run_id", "operation", "base_sha", "candidate_sha", "candidate_tree_sha",
                        "admitted_executor", "admitted_run_sha256", "authorization_sha256", "checkpoint_ordinal",
                        "checkpoint_digest", "population_identity", "execution_identity")}
                    binding.update(feedback_id=feedback.data["feedback_id"], feedback_digest=feedback.digest,
                        runtime_identity=self._local_feedback_identity,
                        invocation_identity=intent["invocation_identity"],
                        profile_sha256=None if frame.profile is None else verification_digest(frame.profile.as_dict()))
                    progress = self._local_feedback_progress(directory, record, feedback)
                    expected = _consumption_result(binding=binding, resources=resources, progress=progress)
                    content = format_feedback_consumption(expected)
                    path = directory / f"k{checkpoint.ordinal:03d}.consumption.json"
                    if os.path.lexists(path):
                        original = _original_local(path, MAX_ORIGINAL_PROOF_BYTES)
                        if original != content or self._local_feedback_receipts.get(checkpoint.ordinal) != original:
                            raise VerificationContractError("receipt collision or unknown callback state")
                        authenticate()
                        _local_feedback_fence(directory, intent)
                        return parse_feedback_consumption(original)
                    if checkpoint.ordinal in self._local_feedback_receipts:
                        raise VerificationContractError("original receipt removed")
                    # Last native read before exposing F_i to this same adapter.
                    authenticate()
                    _local_feedback_fence(directory, intent)
                    reason = "CALLBACK_CONFIRMATION_NOT_ESTABLISHED"
                    confirmation = frame.callback(feedback=feedback, receipt_id=expected.data["receipt_id"])
                    if type(confirmation) is not LocalFeedbackConfirmation:
                        raise VerificationContractError("direct typed adapter confirmation required")
                    confirmation.__post_init__()
                    if (confirmation.receipt_id, confirmation.feedback_digest) != (expected.data["receipt_id"], feedback.digest):
                        raise VerificationContractError("conflicting adapter confirmation")
                    reason = "NATIVE_SOURCE_OR_EFFECT_NOT_ESTABLISHED"
                    authenticate()
                    _local_feedback_fence(directory, intent)
                    _checkpoint_write_once(path, content)
                    authenticate(pending_receipt=content)
                    _local_feedback_fence(directory, intent)
                    if _original_local(path, MAX_ORIGINAL_PROOF_BYTES) != content:
                        raise VerificationContractError("receipt changed after callback")
                    self._local_feedback_receipts[checkpoint.ordinal] = content
                    return expected
        except BaseException as exc:
            self._local_feedback_stopped = True
            if not isinstance(exc, Exception):
                raise
            # Preserve original effects and never disclose callback/source text.
            return _consumption_result(reason=reason)

    def _local_feedback_progress(self, directory, record, feedback):
        """Require strict improvement of the original failed obligations.

        Native history already requires changed trees, committed scoped
        descendants and unchanged full semantics. Only actual prior local
        consumption supplies a causal source; callers supply no progress facts.
        """
        history = self._checkpoint_history(directory)
        if len(history) < 2:
            return "NOT_ESTABLISHED"
        previous = history[-2]
        ordinal = previous.data["request"]["ordinal"]
        retained = self._local_feedback_receipts.get(ordinal)
        if retained is None:
            return "NOT_ESTABLISHED"
        if _original_local(directory / f"k{ordinal:03d}.consumption.json", MAX_ORIGINAL_PROOF_BYTES) != retained:
            raise VerificationContractError("original progress receipt changed")
        before = _checkpoint_factual_feedback(previous, self.run.executor)
        if (before.data["status"] == "PREPARED"
                and set(feedback.data["outstanding_obligation_ids"]) < set(before.data["outstanding_obligation_ids"])
                and record.data["population_identity"] == previous.data["population_identity"]
                and record.data["execution_identity"] == previous.data["execution_identity"]):
            return "VERIFIED_SCOPED_DESCENDANT_PROGRESS"
        return "NOT_ESTABLISHED"

    @_ka01_guarded
    def terminal_reuse(self, request: ProofCheckpointRequest, *, checkpoint_digest: str) -> TerminalProofDecision:
        """Prospective decision only; it cannot write RESULT or grant execution."""
        from .verification_contract import _original_hash
        if self._ka01_opted:
            if self._ka01_budget is None:
                raise VerificationContractError("KA01_V13_OPTED_RUN_STOP_LATCHED")
            self._ka01_budget.guard()
        format_checkpoint_request(request)
        _original_hash(checkpoint_digest)
        try:
            directory = self._checkpoint_binding(request)
            if _checkpoint_effect_present(directory):
                raise VerificationContractError("CHECKPOINT_COMPETING_OR_INTERRUPTED_EFFECT")
            record = self._authenticate_checkpoint(request, checkpoint_digest)
            if record.data["outstanding"] or not all(p["satisfactory"] for p in record.data["proofs"]):
                raise VerificationContractError("CHECKPOINT_MANDATORY_PROOF_UNSATISFACTORY")
            return TerminalProofDecision(True, "EXACT_CURRENT_COMPLETE_STABLE_SATISFACTORY_NATIVE_PROOF", record.digest,
                                         request.scheduler.subject_sha)
        except (ValueError, OSError, RuntimeError) as exc:
            if self._local_feedback_invocation is not None:
                self._local_feedback_stopped = True
            return TerminalProofDecision(False, str(exc)[:256], checkpoint_digest, request.scheduler.subject_sha)

    @_ka01_guarded
    def complete(self, package: ResultPackage, policy: CompletionPolicy, *,
                 checkpoint: ProofCheckpointRequest | None = None, checkpoint_digest: str | None = None) -> CompletionOutcome:
        """Keep the default V1/V2 path; explicit checkpoint proof uses this same writer."""
        if self._ka01_budget is not None and self._ka01_budget.production:
            return self._complete_ka01_primary(package, policy, checkpoint, checkpoint_digest)
        if self._ka01_opted and (checkpoint is None or policy != self._ka01_budget.policy):
            raise VerificationContractError("KA01_V13_EXACT_CHECKPOINT_TERMINAL_REQUIRED")
        if checkpoint is None:
            if checkpoint_digest is not None:
                raise VerificationContractError("CHECKPOINT_EXACT_REQUEST_REQUIRED")
            # No silent legacy rerun once this RUN has a prospective proof effect.
            path = self.state.verification / self.run.run_id / "l3-02"
            with _runtime_effect(path.parent, dict(version=1, kind="LEGACY_COMPLETION", run_id=self.run.run_id),
                                 retain_ambiguity=False):
                if os.path.lexists(path):
                    raise VerificationContractError("CHECKPOINT_EXPLICIT_TERMINAL_CONSUMPTION_REQUIRED")
                return self._complete(package, policy)
        if policy != primary_completion_policy(self.task, base_sha=self.run.base_sha):
            raise VerificationContractError("CHECKPOINT_CORRECTION_OR_ALTERED_COMPLETION_POLICY_BLOCKED")
        from .verification_contract import _original_hash
        _original_hash(checkpoint_digest)
        directory = self._checkpoint_binding(checkpoint, terminal=True)
        if os.path.lexists(directory / "terminal-intent.json"):
            # Reconciliation is read-only, including after transport interruption.
            return self._reconcile_checkpoint_terminal(package, policy, checkpoint, checkpoint_digest, directory)
        if os.path.lexists(self.state.results / (self.run.run_id + ".json")):
            raise VerificationContractError("CHECKPOINT_ALREADY_TERMINAL_RUN")
        decision = self.terminal_reuse(checkpoint, checkpoint_digest=checkpoint_digest)
        if not decision.eligible:
            raise VerificationContractError(decision.reason)
        intent = dict(version=1, kind="TERMINAL", request=checkpoint_request_data(checkpoint), checkpoint_digest=checkpoint_digest)
        with self._checkpoint_effect(directory, intent):
            record = self._authenticate_checkpoint(checkpoint, checkpoint_digest)
            reviewed = {}
            for row in record.data["plan"]["obligations"]:
                obligation = row["obligation"]
                claim = obligation["claim"]
                value = reviewed.setdefault(claim["id"], (claim["text"], set()))
                if value[0] != claim["text"]:
                    raise VerificationContractError("CHECKPOINT_REVIEWED_CLAIM_CONFLICT")
                value[1].update(obligation["acceptance_ids"])
            actual = {c.id: (c.claim, set(c.satisfies)) for c in package.result.claims}
            if len(actual) != len(package.result.claims) or actual != reviewed:
                raise VerificationContractError("CHECKPOINT_EXACT_REVIEWED_RESULT_CLAIMS_REQUIRED")
            return self._complete(package, policy, checkpoint_record=record)

    def _complete_ka01_primary(self, package, policy, checkpoint, checkpoint_digest):
        """Budget the original authored proof and terminal writer once.

        This supported production operation has no in-turn feedback/reuse loop.
        It does not accept a synthetic checkpoint or an ACK as native proof.
        """
        from .ka01_admission import _digest, _deny
        budget = self._ka01_budget
        if (checkpoint is not None or checkpoint_digest is not None or policy != budget.policy
                or policy != primary_completion_policy(self.task, base_sha=self.run.base_sha)
                or budget.native_state != "RETURNED" or budget.native_calls != 1
                or budget.package_digest != _digest(result_package_data(package))):
            _deny("ORIGINAL_PRIMARY_COMPLETION_REQUIRED")
        directory = self._checkpoint_directory()
        if any(os.path.lexists(p) for p in (directory / "terminal-intent.json",
                self.state.results / (self.run.run_id + ".json"), self.state.failures / (self.run.run_id + ".json"))):
            _deny("TERMINAL_RUN")
        intent = dict(kind="PRODUCTION_COMPLETION", package_sha256=budget.package_digest,
            policy_sha256=_digest(asdict(policy)))
        budget.capacity(intent)
        # A caught completion failure may use the existing FAILURE writer.
        # Reservations remain spent/unknown and this owner is STOP-latched;
        # an actual process crash still retains its exclusive negative fence.
        with _checkpoint_mutation(directory, intent, retain_ambiguity=False):
            with budget.effect(intent):
                return self._complete(package, policy)

    def _transport_ka01_completion(self, outcome, policy):
        """Existing post-PASS transport after finite accounting has settled."""
        from .ka01_admission import _digest, _deny
        budget = self._ka01_budget
        expected_path = self.state.results / (self.run.run_id + ".json")
        if (budget is None or not budget.production or budget.pending is not None
                or not budget.stopped or budget.terminal_digest is None or policy != budget.policy
                or outcome.result_path != expected_path):
            _deny("EXACT_SETTLED_TERMINAL_REQUIRED")
        raw = _original_local(expected_path, MAX_ORIGINAL_PROOF_BYTES)
        if (_digest(raw) != budget.terminal_digest or json.loads(raw)["result"]["head_sha"] != outcome.head_sha
                or _digest(_original_local(self.run_path, MAX_ORIGINAL_PROOF_BYTES)) !=
                    json.loads(budget.authority)["source"]["admitted_run_sha256"]):
            _deny("EXACT_SETTLED_TERMINAL_REQUIRED")
        try:
            transport_post_pass(self.transport_repo, run_id=self.run.run_id, head_sha=outcome.head_sha,
                run_path=self.run_path, result_path=outcome.result_path, lineage_path=policy.lineage_path,
                observation_path=outcome.observation_path)
        except ReviewTransportError as exc:
            self._raise(f"review transport failed: {exc}", cause=exc)

    def _reconcile_checkpoint_terminal(self, package, policy, request, digest, directory):
        from .verification_contract import _app_exact, _reuse_decode
        receipt = _app_exact(_reuse_decode(_original_local(directory / "terminal-intent.json", MAX_ORIGINAL_PROOF_BYTES)),
            ("version", "checkpoint_digest", "request", "package_sha256", "policy_sha256", "result_sha256"))
        result_path = self.state.results / (self.run.run_id + ".json")
        if (type(receipt["version"]) is not int or receipt["version"] != 1 or receipt["checkpoint_digest"] != digest
                or receipt["request"] != checkpoint_request_data(request)
                or receipt["package_sha256"] != _checkpoint_digest(_checkpoint_json(result_package_data(package)))
                or receipt["policy_sha256"] != _checkpoint_digest(_checkpoint_json(asdict(policy)))
                or _checkpoint_digest(_original_local(result_path, MAX_ORIGINAL_PROOF_BYTES)) != receipt["result_sha256"]):
            raise VerificationContractError("CHECKPOINT_CONFLICTING_OR_AMBIGUOUS_TERMINAL")
        history = self._checkpoint_history(directory, check_captures=False)
        if not history or history[-1].digest != digest or history[-1].data["request"] != receipt["request"]:
            raise VerificationContractError("CHECKPOINT_TERMINAL_HISTORY_CONFLICT")
        # A terminal replay reads the exact original fact; moved refs cannot
        # grant new work, and no native execution/transport/observation repeats.
        return CompletionOutcome(request.scheduler.subject_sha, result_path, None)

    def _complete(
        self, package: ResultPackage, policy: CompletionPolicy,
        *, checkpoint_record: RuntimeCheckpoint | None = None
    ) -> CompletionOutcome:
        """Produce one canonical RESULT or raise before/after terminal persistence."""

        result_path = self.state.results / f"{self.run.run_id}.json"
        self._staged_result_receipt = None
        if policy.stage_structural_package:
            staged = self.state.staging / f"{self.run.run_id}.json"
            content = json.dumps(result_package_data(package), sort_keys=True, separators=(",", ":")).encode("utf-8")
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)
            self._staged_result_receipt = _StagedResultReceipt(staged.absolute(),
                _checkpoint_digest(_checkpoint_json(asdict(self.run))),
                _checkpoint_digest(_checkpoint_json(asdict(self.task))), package.result.head_sha,
                _checkpoint_digest(content), len(content))
        self._require_executor_structure(package)

        actual_head = self._git("rev-parse", "HEAD")
        if package.result.head_sha != actual_head:
            self._raise("RESULT.head_sha mismatch")
        dirty_message = (
            "working tree dirty after REPAIR"
            if policy.kind == "REPAIR"
            else "working tree dirty after execution"
        )
        if self._git("status", "--porcelain"):
            self._raise(dirty_message)

        if policy.kind == "REPAIR":
            package = self._canonicalize_repair_changed_files(
                package, policy, actual_head=actual_head
            )

        if policy.kind in ("PRIMARY", "REPAIR"):
            self._require_task_result(package, policy, actual_head=actual_head)
        else:
            self._require_remediation_result(package, policy, actual_head=actual_head)

        try:
            persist_preverification_candidate(
                self.state.preverification / f"{self.run.run_id}.json",
                task=self.task,
                run=self.run,
                subject_sha=actual_head,
                package=package,
            )
        except (OSError, UnicodeError, ArtifactValidationError) as exc:
            self._raise(
                f"pre-verification candidate persistence failed: {exc}", cause=exc
            )

        self.interruption_phase = "VERIFICATION"
        self.verification_subject_sha = actual_head
        verification_started = (
            None
            if self.observation_tracker is None
            else self.observation_tracker.begin_verification()
        )
        try:
            if checkpoint_record is not None:
                runtime_evidence = tuple(validate_evidence(item) for item in checkpoint_record.data["evidence"])
            elif self.task.verification.policy == MINIMUM_SUFFICIENT_V2:
                commands = policy.verification_commands
                if policy.remediation_execution is not None:
                    commands = tuple(dict.fromkeys((*commands, *(
                        c for c in self.task.verification.required
                        if (coverage := parse_pytest_coverage(c)) is not None and coverage.is_full_suite
                    ))))
                runtime_evidence = execute_minimum_verification(
                    commands, run_id=self.run.run_id, base_sha=self.run.base_sha,
                    subject_sha=actual_head, repository=self.repo,
                    raw_directory=self.state.verification / self.run.run_id,
                    cache_directory=self.state.verification / MINIMUM_SUFFICIENT_V2,
                    modification_scope=policy.mutation_scope if policy.kind == "REPAIR" else policy.result_scope,
                    operation=policy.kind, runner=self.verification_runner,
                    subject_check=self._require_post_verification_state,
                    correction_base_sha=policy.mutation_base_sha if policy.kind == "REPAIR" else policy.result_base_sha,
                )
            else:
                with materialize_verification_subject(
                    self.repo,
                    run_id=self.run.run_id,
                    subject_sha=actual_head,
                ) as verification_subject:
                    runtime_evidence = execute_verification(
                        policy.verification_commands,
                        run_id=self.run.run_id,
                        subject_sha=actual_head,
                        repository=verification_subject,
                        raw_directory=(self._checkpoint_directory() / "production" if self._ka01_budget is not None
                            and self._ka01_budget.production else self.state.verification / self.run.run_id),
                        runner=self._ka01_verification_runner if self._ka01_budget is not None
                            and self._ka01_budget.production else self.verification_runner,
                    )
                    self._require_post_verification_state(
                        verification_subject,
                        expected_head=actual_head,
                        evidence=runtime_evidence,
                    )
        except RuntimeVerificationError as exc:
            self._raise(str(exc), cause=exc)
        finally:
            if self.observation_tracker is not None:
                self.observation_tracker.end_verification(verification_started)
        self.interruption_phase = "COMPLETION_GATE"
        if policy.remediation_execution is None:
            canonical_result = attach_verification_evidence(
                package.result, runtime_evidence
            )
            try:
                canonical_package = validate_result_package(
                    task=self.task,
                    run=self.run,
                    result=canonical_result,
                    evidence=runtime_evidence,
                )
            except ArtifactValidationError as exc:
                self._raise(f"invalid canonical ResultPackage: {exc}", cause=exc)
        else:
            canonical_package = ResultPackage(
                result=package.result, evidence=runtime_evidence
            )
            self._require_remediation_package_contract(
                policy.remediation_execution,
                canonical_package,
                actual_head=actual_head,
            )

        if checkpoint_record is None:
            if self._ka01_budget is not None and self._ka01_budget.production:
                budget = self._ka01_budget
                budget.guard(live=False)
                budget.remaining_seconds()
                content = _checkpoint_json(result_package_data(canonical_package))
                intent = dict(version=2, kind="PRODUCTION_TERMINAL", subject_sha=actual_head,
                    package_sha256=budget.package_digest, result_sha256=_checkpoint_digest(content))
                _checkpoint_write_once(self._checkpoint_directory() / "terminal-intent.json", _checkpoint_json(intent))
                _checkpoint_write_once(result_path, content)
                budget.terminal_digest = _checkpoint_digest(content)
            else:
                _write_json(result_path, result_package_data(canonical_package))
        else:
            # Last native read before the single existing terminal effect.
            request = parse_checkpoint_request(_original_json(checkpoint_record.data["request"]).encode("ascii"))
            self._authenticate_checkpoint(request, checkpoint_record.digest)
            content = _checkpoint_json(result_package_data(canonical_package))
            intent = dict(version=1, checkpoint_digest=checkpoint_record.digest,
                request=checkpoint_record.data["request"], package_sha256=_checkpoint_digest(_checkpoint_json(result_package_data(package))),
                policy_sha256=_checkpoint_digest(_checkpoint_json(asdict(policy))), result_sha256=_checkpoint_digest(content))
            directory = self._checkpoint_directory()
            if self._ka01_budget is not None:
                self._ka01_budget.guard()
                self._ka01_budget.remaining_seconds()
            _checkpoint_write_once(directory / "terminal-intent.json", _checkpoint_json(intent))
            _checkpoint_write_once(result_path, content)
            if self._ka01_budget is not None:
                self._ka01_budget.terminal_digest = _checkpoint_digest(content)
        observation_path = persist_terminal_observation(
            self.state, self.observation_tracker, "RESULT"
        )
        if checkpoint_record is not None or (self._ka01_budget is not None and self._ka01_budget.production):
            # Historical local checkpoint completion ends here. Production
            # PRIMARY transports through this same owner after its accounting
            # settles, without repeating proof or the terminal write.
            return CompletionOutcome(actual_head, result_path, observation_path)
        try:
            transport_post_pass(
                self.transport_repo,
                run_id=self.run.run_id,
                head_sha=actual_head,
                run_path=self.run_path,
                result_path=result_path,
                lineage_path=policy.lineage_path,
                observation_path=observation_path,
            )
        except ReviewTransportError as exc:
            self._raise(f"review transport failed: {exc}", cause=exc)
        return CompletionOutcome(actual_head, result_path, observation_path)

    def _canonicalize_repair_changed_files(
        self,
        package: ResultPackage,
        policy: CompletionPolicy,
        *,
        actual_head: str,
    ) -> ResultPackage:
        """Replace the structural REPAIR declaration with complete Git truth."""

        actual_changed = self._committed_changed_files(
            policy.result_base_sha, actual_head
        )
        outside_scope = actual_changed.difference(policy.result_scope)
        if outside_scope:
            self._raise(
                "committed changed paths outside TASK.scope.modify: "
                + ", ".join(sorted(outside_scope))
            )
        canonical_result = replace(
            package.result, changed_files=tuple(sorted(actual_changed))
        )
        return replace(package, result=canonical_result)

    def _require_task_result(
        self,
        package: ResultPackage,
        policy: CompletionPolicy,
        *,
        actual_head: str,
    ) -> None:
        result_base = policy.result_base_sha
        if package.result.changed_files and actual_head == result_base:
            self._raise("final Git HEAD did not advance")
        self._require_changed_files(
            package,
            base_sha=result_base,
            actual_head=actual_head,
            scope=policy.result_scope,
            scope_name="TASK.scope.modify",
        )
        if policy.require_task_completion:
            self._require_complete_result(package)

        if policy.kind == "PRIMARY" and self.task.scope.modify:
            if actual_head == self.run.base_sha:
                self._raise("final Git HEAD did not advance")
        if policy.kind == "REPAIR":
            assert policy.mutation_base_sha is not None
            if (
                policy.mutation_action == "CONTINUE_IMPLEMENTATION"
                and not self._is_ancestor(policy.mutation_base_sha, actual_head)
            ):
                self._raise(
                    "CONTINUE_IMPLEMENTATION final HEAD does not descend from failed HEAD"
                )
            repair_changed = self._committed_changed_files(
                policy.mutation_base_sha, actual_head
            )
            if policy.mutation_action in (
                "CODE_FIX", "CONTINUE_IMPLEMENTATION"
            ):
                if repair_changed.difference(policy.mutation_scope):
                    self._raise(
                        "REPAIR committed paths outside authorized correction scope"
                    )
                if actual_head == policy.mutation_base_sha:
                    self._raise(
                        f"{policy.mutation_action} REPAIR did not advance HEAD"
                    )
                if not repair_changed:
                    message = (
                        "CODE_FIX REPAIR committed correction delta is empty"
                        if policy.mutation_action == "CODE_FIX"
                        else "CONTINUE_IMPLEMENTATION REPAIR committed delta is empty"
                    )
                    self._raise(message)
            elif actual_head != policy.mutation_base_sha:
                self._raise(f"{policy.mutation_action} REPAIR changed HEAD")

    def _require_remediation_result(
        self,
        package: ResultPackage,
        policy: CompletionPolicy,
        *,
        actual_head: str,
    ) -> None:
        execution = policy.remediation_execution
        assert execution is not None
        if package.result.claims:
            self._raise("remediation RESULT claims must be empty")
        if package.result.unresolved:
            self._raise("remediation RESULT has unresolved items")
        self._require_changed_files(
            package,
            base_sha=policy.result_base_sha,
            actual_head=actual_head,
            scope=policy.result_scope,
            scope_name="REMEDIATION modification scope",
        )
        actual_changed = self._committed_changed_files(
            policy.result_base_sha, actual_head
        )
        if policy.mutation_action == "EVIDENCE_ONLY":
            if actual_head != policy.result_base_sha or actual_changed:
                self._raise("EVIDENCE_ONLY remediation changed repository HEAD")
        else:
            if actual_head == policy.result_base_sha:
                self._raise("CODE_FIX remediation did not advance HEAD")
            if not actual_changed:
                self._raise("CODE_FIX remediation committed delta is empty")

    def _require_changed_files(
        self,
        package: ResultPackage,
        *,
        base_sha: str,
        actual_head: str,
        scope: Sequence[str],
        scope_name: str,
    ) -> None:
        actual_changed = self._committed_changed_files(base_sha, actual_head)
        if set(package.result.changed_files) != actual_changed:
            self._raise("RESULT.changed_files mismatch")
        outside_scope = actual_changed.difference(scope)
        if outside_scope:
            self._raise(
                f"committed changed paths outside {scope_name}: "
                + ", ".join(sorted(outside_scope))
            )

    def _require_complete_result(self, package: ResultPackage) -> None:
        if package.result.unresolved:
            self._raise("RESULT has unresolved items")
        satisfied = {
            acceptance_id
            for claim in package.result.claims
            for acceptance_id in claim.satisfies
        }
        missing = [item.id for item in self.task.acceptance if item.id not in satisfied]
        if missing:
            self._raise(
                "RESULT does not satisfy acceptance criteria: " + ", ".join(missing)
            )

    def _require_executor_structure(self, package: ResultPackage) -> None:
        if package.evidence:
            self._raise("executor structural output evidence must be empty")
        if any(claim.evidence for claim in package.result.claims):
            self._raise(
                "executor structural claim evidence references must be empty"
            )

    def _require_remediation_package_contract(
        self,
        execution: RemediationExecution,
        package: ResultPackage,
        *,
        actual_head: str,
    ) -> None:
        if package.result.claims:
            self._raise("remediation RESULT claims must be empty")
        if package.result.unresolved:
            self._raise("remediation RESULT has unresolved items")
        evidence_ids: set[str] = set()
        for item in package.evidence:
            if item.evidence_id in evidence_ids:
                self._raise(f"duplicate evidence_id: {item.evidence_id}")
            evidence_ids.add(item.evidence_id)
            if item.run_id != execution.run.run_id:
                self._raise(
                    f"{item.evidence_id} does not reference RUN {execution.run.run_id}"
                )
            if item.subject_sha != actual_head:
                self._raise(
                    f"{item.evidence_id} subject_sha does not match RESULT head_sha"
                )
            if (self.task.verification.policy == MINIMUM_SUFFICIENT_V2 and item.verification is not None
                    and item.verification.get("binding", {}).get("base_sha") != self.run.base_sha):
                self._raise("V2 attribution base does not match RUN base_sha")
            if (self.task.verification.policy == MINIMUM_SUFFICIENT_V2 and item.type == "VERIFICATION"
                    and not verification_requirement_passes(item, policy=self.task.verification.policy)):
                self._raise("blocking V2 observation cannot appear in canonical RESULT")
        required = execution.remediation.affected_verification
        if self.task.verification.policy == MINIMUM_SUFFICIENT_V2:
            required = tuple(dict.fromkeys((*required, *(
                c for c in self.task.verification.required
                if (coverage := parse_pytest_coverage(c)) is not None and coverage.is_full_suite
            ))))
        for command in required:
            matching = [
                item for item in package.evidence if item.source.command == command
            ]
            if not matching:
                self._raise(
                    "missing affected verification evidence for required command: "
                    + command
                )
            if not any(verification_requirement_passes(item, policy=self.task.verification.policy) for item in matching):
                self._raise(
                    "affected verification command has no successful evidence: "
                    + command
                )

    def _require_post_verification_state(
        self,
        repository: Path,
        *,
        expected_head: str,
        evidence: Sequence[Evidence] = (),
    ) -> None:
        try:
            if self._git_at(repository, "rev-parse", "HEAD") != expected_head:
                raise RuntimeVerificationError(
                    "verification changed Git HEAD", evidence=evidence
                )
            if self._git_at(repository, "status", "--porcelain"):
                raise RuntimeVerificationError(
                    "verification dirtied working tree", evidence=evidence
                )
        except RuntimeVerificationError:
            raise
        except self.error_type as exc:
            raise RuntimeVerificationError(str(exc), evidence=evidence) from exc

    def _committed_changed_files(self, base_sha: str, head_sha: str) -> set[str]:
        output = self._git(
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base_sha,
            head_sha,
            strip_stdout=False,
        )
        return {path for path in output.split("\0") if path}

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        try:
            return _git_is_ancestor(self.repo, ancestor, descendant)
        except RuntimeError as exc:
            self._raise(str(exc), cause=exc)

    def _git(self, *args: str, strip_stdout: bool = True) -> str:
        return self._git_at(self.repo, *args, strip_stdout=strip_stdout)

    def _git_at(
        self, repository: Path, *args: str, strip_stdout: bool = True
    ) -> str:
        try:
            completed = subprocess.run(
                ("git", "-C", str(repository), *args),
                capture_output=True,
                text=False,
                check=False,
            )
            stdout = _decode_utf8(completed.stdout)
            stderr = _decode_utf8(completed.stderr)
        except (OSError, UnicodeError) as exc:
            self._raise(f"Git invocation failed: {exc}", cause=exc)
        if completed.returncode != 0:
            detail = stderr.strip() or stdout.strip()
            self._raise(f"Git command failed: {detail}")
        return stdout.strip() if strip_stdout else stdout

    def _raise(self, message: str, *, cause: Exception | None = None) -> None:
        error = self.error_type(message)
        if self._staged_result_receipt is not None:
            error._runtime_staged_result = self._staged_result_receipt
        if cause is None:
            raise error
        raise error from cause


def persist_terminal_observation(
    state: RuntimeState,
    tracker: RunObservationTracker | None,
    terminal_kind: str,
) -> Path | None:
    """Best-effort one immutable sidecar without changing terminal authority."""

    if tracker is None:
        return None
    try:
        observation = tracker.finalize(terminal_kind)
        if observation is None:
            return None
        path = state.observations / f"{observation.run_id}.json"
        persist_observation(path, observation)
        return path
    except Exception:
        return None


def _persist_failure(
    root: Path,
    *,
    state: RuntimeState,
    task: Task,
    run: Run,
    run_path: Path,
    failure: BaseException,
    observation_tracker: RunObservationTracker | None = None,
    observation_path: Path | None = None,
    interruption_phase: str | None = None,
    transport: bool = True,
    transport_repo: Path | None = None,
    verification_subject_sha: str | None = None,
) -> None:
    """Persist one admitted FAILURE and optionally best-effort transport it."""

    try:
        if (str(failure) in {"RESULT has unresolved items", "remediation RESULT has unresolved items"}
                and os.path.lexists(state.failures / (run.run_id + ".json"))):
            # The original pin is immutable. Currentness is checked by the
            # authorized reader, without repinning or repeating inventory Git.
            return
        if observation_path is None:
            observation_path = persist_terminal_observation(
                state, observation_tracker, "FAILURE"
            )
        cause = failure if isinstance(failure, (CodexExecutionError, AntigravityExecutionError, RuntimeVerificationError)) else failure.__cause__
        if isinstance(failure, KeyboardInterrupt) and interruption_phase is not None:
            phase = interruption_phase
        elif isinstance(
            cause, (CodexExecutionError, AntigravityExecutionError)
        ) or isinstance(failure, KeyboardInterrupt):
            phase = "EXECUTION"
        elif isinstance(cause, RuntimeVerificationError):
            phase = "VERIFICATION"
        else:
            phase = "COMPLETION_GATE"
        exact_verification_subject = (
            verification_subject_sha
            if phase == "VERIFICATION" and verification_subject_sha is not None
            else None
        )
        head_sha = (
            exact_verification_subject
            if exact_verification_subject is not None
            else _git(root, "rev-parse", "HEAD")
        )
        # A verification failure describes the immutable candidate materialized
        # from PRE_VERIFICATION_CANDIDATE, never a later control checkout HEAD.
        dirty = (
            False
            if exact_verification_subject is not None
            else bool(_git(root, "status", "--porcelain"))
        )
        descendant = _git_is_ancestor(root, run.base_sha, head_sha)
        changed = (
            _committed_changed_files(root, run.base_sha, head_sha)
            if descendant
            else set()
        )
        outside_scope = changed.difference(task.scope.modify)
        repairable = not dirty and descendant
        transportable = repairable and not outside_scope
        error_message = str(failure)
        if isinstance(cause, (CodexExecutionError, AntigravityExecutionError)):
            error_message = str(cause).splitlines()[0][:512]
        elif isinstance(failure, KeyboardInterrupt):
            error_message = {
                "EXECUTION": "native execution interrupted by Human",
                "VERIFICATION": "Runtime verification interrupted by Human",
                "COMPLETION_GATE": "Runtime completion interrupted by Human",
            }.get(phase, "admitted run interrupted by Human")
        record: dict[str, Any] = {
            "kind": "FAILURE",
            "run_id": run.run_id,
            "task": {"id": run.task.id, "revision": run.task.revision},
            "executor": run.executor,
            "base_sha": run.base_sha,
            "failed_head_sha": head_sha,
            "phase": phase,
            "error": {
                "type": type(cause or failure).__name__,
                "message": error_message,
            },
            "candidate": {
                "transportable": transportable,
                "repairable": repairable,
                "dirty": dirty,
                "descends_from_base": descendant,
                "changed_files": sorted(changed),
                "outside_task_scope": sorted(outside_scope),
            },
        }
        repair_execution = state.repairs / f"{run.run_id}.json"
        if repair_execution.is_file():
            record["continuation_of"] = json.loads(
                repair_execution.read_text(encoding="utf-8")
            ).get("failed_run_id")
        if isinstance(cause, (CodexExecutionError, AntigravityExecutionError)):
            record["error"]["exit_code"] = getattr(cause, "exit_code", None)
            record["error"]["native_reason"] = (
                "TIMEOUT" if _is_native_timeout(cause) else
                "OUTPUT_INVALID" if type(cause).__name__ in {"CodexOutputError", "AntigravityOutputError"} else
                "INVOCATION_FAILED" if isinstance(cause.__cause__, (OSError, UnicodeError)) else
                "PROCESS_EXIT")
        elif isinstance(failure, KeyboardInterrupt) and phase == "EXECUTION":
            record["error"]["native_reason"] = "INTERRUPTED"
        if isinstance(
            cause, (CodexExecutionError, AntigravityExecutionError)
        ) or isinstance(failure, KeyboardInterrupt):
            diagnostic_source: BaseException = cause or failure
            record["error"]["native_diagnostics"] = {
                "limit_chars": NATIVE_DIAGNOSTIC_LIMIT,
                "stdout": _bounded_native_stream(diagnostic_source, "stdout"),
                "stderr": _bounded_native_stream(diagnostic_source, "stderr"),
            }
        if isinstance(cause, RuntimeVerificationError):
            record["error"]["verification"] = [
                {
                    "command": item.source.command,
                    "exit_code": item.result.exit_code,
                    "summary": item.result.summary,
                    **({"verification": item.verification} if item.verification is not None else {}),
                }
                for item in cause.evidence
            ]
        pin, executor_unresolved = _pin_staged_unresolved(root, state, task, run, head_sha, failure)
        if phase == "COMPLETION_GATE" and error_message in {
                "RESULT has unresolved items", "remediation RESULT has unresolved items"}:
            record["error"]["unresolved_source"] = pin
        if executor_unresolved:
            from .review_transport import PUBLIC_FAILURE_MAX_BYTES
            # The pinned staging remains the full source. Duplicating it cannot
            # consume more than the existing private FAILURE capture allowance.
            if len(_checkpoint_json(record)) + len(_checkpoint_json(executor_unresolved)) + 64 <= PUBLIC_FAILURE_MAX_BYTES:
                record["error"]["executor_diagnostics"] = {"unresolved": executor_unresolved}
        failure_path = state.failures / f"{run.run_id}.json"
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        try:
            with failure_path.open("xb") as stream:
                stream.write(encoded)
        except FileExistsError:
            # Recording/replay cannot rewrite an original terminal's diagnostics.
            if failure_path.read_bytes() != encoded:
                return
        preverification_path = state.preverification / f"{run.run_id}.json"
        reusable_candidate_path = (
            preverification_path
            if phase == "VERIFICATION" and preverification_path.is_file()
            else None
        )
        if transport:
            try:
                transport_failure(
                    transport_repo or root,
                    run_id=run.run_id,
                    head_sha=head_sha,
                    run_path=run_path,
                    failure_path=failure_path,
                    publish_candidate=transportable,
                    lineage_path=(
                        repair_execution if repair_execution.is_file() else None
                    ),
                    observation_path=observation_path,
                    preverification_path=reusable_candidate_path,
                )
            except (ReviewTransportError, OSError) as transport_error:
                _write_json(
                    state.failures / f"{run.run_id}.transport.json",
                    {"run_id": run.run_id, "subject_sha": head_sha,
                     "kind": "FAILURE_TRANSPORT_BLOCKER",
                     "code": getattr(transport_error, "code", "FAILURE_TRANSPORT_SOURCE_UNAVAILABLE"
                                     if isinstance(transport_error, OSError) else "FAILURE_TRANSPORT_UNAVAILABLE"),
                     "error": str(transport_error)},
                )
    except Exception:
        # Failure recording is subordinate and never replaces the original failure.
        return


def result_package_data(package: ResultPackage) -> dict[str, Any]:
    return {
        "result": {
            "head_sha": package.result.head_sha,
            "claims": [asdict(claim) for claim in package.result.claims],
            "changed_files": list(package.result.changed_files),
            "unresolved": list(package.result.unresolved),
        },
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "run_id": item.run_id,
                "subject_sha": item.subject_sha,
                "type": item.type,
                "source": asdict(item.source),
                "result": asdict(item.result),
                "raw": {"path": item.raw_path},
                **({"verification": item.verification} if item.verification is not None else {}),
            }
            for item in package.evidence
        ],
    }


def persist_preverification_candidate(
    path: Path,
    *,
    task: Task,
    run: Run,
    subject_sha: str,
    package: ResultPackage,
) -> None:
    """Persist exact subordinate state only after deterministic completion gates."""

    data = {
        "kind": "PRE_VERIFICATION_CANDIDATE",
        "run_id": run.run_id,
        "task": {"id": task.task_id, "revision": task.revision},
        "subject_sha": subject_sha,
        "package": result_package_data(package),
    }
    content = json.dumps(data, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    # Validate the exact representation before making it reusable authority.
    validate_preverification_candidate(
        content,
        task=task,
        run_id=run.run_id,
        subject_sha=subject_sha,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ArtifactValidationError(
                f"conflicting pre-verification candidate for {run.run_id}"
            )


def validate_preverification_candidate(
    content: bytes,
    *,
    task: Task,
    run_id: str,
    subject_sha: str,
) -> ResultPackage:
    """Validate one exact Runtime-owned structural candidate for deterministic reuse."""

    try:
        data = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(
            f"invalid pre-verification candidate JSON: {exc}"
        ) from exc
    if not isinstance(data, Mapping) or set(data) != {
        "kind",
        "run_id",
        "task",
        "subject_sha",
        "package",
    }:
        raise ArtifactValidationError(
            "pre-verification candidate fields do not match the contract"
        )
    if data.get("kind") != "PRE_VERIFICATION_CANDIDATE":
        raise ArtifactValidationError("invalid pre-verification candidate kind")
    if data.get("run_id") != run_id or data.get("subject_sha") != subject_sha:
        raise ArtifactValidationError(
            "pre-verification candidate RUN or subject mismatch"
        )
    task_ref = data.get("task")
    expected_task = {"id": task.task_id, "revision": task.revision}
    if not isinstance(task_ref, Mapping) or dict(task_ref) != expected_task:
        raise ArtifactValidationError("pre-verification candidate TASK mismatch")
    payload = data.get("package")
    if not isinstance(payload, Mapping) or set(payload) != {"result", "evidence"}:
        raise ArtifactValidationError(
            "pre-verification candidate package fields do not match the contract"
        )
    result_data = payload.get("result")
    if not isinstance(result_data, Mapping) or set(result_data) != {
        "head_sha",
        "claims",
        "changed_files",
        "unresolved",
    }:
        raise ArtifactValidationError(
            "pre-verification candidate RESULT fields do not match the contract"
        )
    claims_data = result_data.get("claims")
    if not isinstance(claims_data, list) or any(
        not isinstance(claim, Mapping)
        or set(claim) != {"id", "satisfies", "claim", "evidence"}
        for claim in claims_data
    ):
        raise ArtifactValidationError(
            "pre-verification candidate claim fields do not match the contract"
        )
    evidence_data = payload.get("evidence")
    if not isinstance(evidence_data, list):
        raise ArtifactValidationError(
            "pre-verification candidate evidence must be a list"
        )
    run = Run.from_task(
        run_id=run_id,
        task=task,
        executor="codex",
        base_sha=subject_sha,
        workspace="pre-verification-candidate",
    )
    package = validate_structural_result_package(
        task=task,
        run=run,
        result=validate_structural_result(result_data),
        evidence=tuple(validate_evidence(item) for item in evidence_data),
    )
    if package.result.head_sha != subject_sha:
        raise ArtifactValidationError(
            "pre-verification candidate RESULT head mismatch"
        )
    if package.evidence or any(claim.evidence for claim in package.result.claims):
        raise ArtifactValidationError(
            "pre-verification candidate contains semantic evidence"
        )
    return package


def _read_unresolved_bytes(path: Path, limit: int) -> bytes:
    """Finite regular-file read, with size checked before parsing or allocation."""
    from .review_transport import FailureDiagnosticError
    try:
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise FailureDiagnosticError("ACCESS_DENIED")
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise FailureDiagnosticError("ACCESS_DENIED")
        if before.st_size > limit:
            raise FailureDiagnosticError("LIMIT_EXHAUSTED")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            linked = path.lstat()
            if stat.S_ISLNK(linked.st_mode) or getattr(linked, "st_file_attributes", 0) & 0x400:
                raise FailureDiagnosticError("ACCESS_DENIED")
            if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
                raise FailureDiagnosticError("SOURCE_MISMATCH")
            content = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
        if len(content) > limit:
            raise FailureDiagnosticError("LIMIT_EXHAUSTED")
        if any((item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) !=
               (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) for item in (opened, after, current)):
            raise FailureDiagnosticError("SOURCE_MISMATCH")
        return content
    except FileNotFoundError as exc:
        raise FailureDiagnosticError("SOURCE_MISSING") from exc
    except PermissionError as exc:
        raise FailureDiagnosticError("ACCESS_DENIED") from exc
    except OSError as exc:
        raise FailureDiagnosticError("SOURCE_INVALID") from exc


def _unresolved_package(raw, task, run, head_sha):
    from .review_transport import FailureDiagnosticError, UNRESOLVED_SOURCE_MAX_BYTES, _public_document
    if len(raw) > UNRESOLVED_SOURCE_MAX_BYTES or raw.count(b",") > 65536 or raw.count(b"{") + raw.count(b"[") > 65536:
        raise FailureDiagnosticError("LIMIT_EXHAUSTED")
    try:
        payload = _public_document(raw, max_bytes=UNRESOLVED_SOURCE_MAX_BYTES)
        if (set(payload) != {"result", "evidence"} or payload["evidence"] != []
                or not isinstance(payload["result"], dict)
                or set(payload["result"]) != {"head_sha", "claims", "changed_files", "unresolved"}
                or not isinstance(payload["result"]["claims"], list)
                or any(not isinstance(c, dict) or set(c) != {"id", "claim", "satisfies", "evidence"}
                       or c["evidence"] != [] for c in payload["result"]["claims"])):
            raise ValueError("invalid structural envelope")
        package = validate_structural_result_package(task=task, run=run,
            result=validate_structural_result(payload["result"]), evidence=())
        if package.result.head_sha != head_sha or payload["result"]["head_sha"] != head_sha:
            raise FailureDiagnosticError("SOURCE_MISMATCH")
        if len(package.result.unresolved) > 65536:
            raise FailureDiagnosticError("LIMIT_EXHAUSTED")
        for item in payload["result"]["unresolved"]:
            item.encode("utf-8")  # Reject unpaired JSON surrogate escapes.
        return replace(package, result=replace(package.result, unresolved=tuple(payload["result"]["unresolved"])))
    except FailureDiagnosticError:
        raise
    except (ReviewTransportError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise FailureDiagnosticError("SOURCE_INVALID") from exc


def _unresolved_test_inventory(root, task, head_sha, items):
    """At most eight exact Git blobs, 256 KiB each, one ten-second total budget.

    Only committed Python definitions grant a test-identity allowlist. Unknown
    names and parameters never become public merely because an Executor said so.
    The immutable private FAILURE retains this Runtime-observed allowlist, so
    repeated readers need no Git fetch or test execution.
    """
    from .review_transport import _unresolved_path, _safe_test_identity
    allowed_paths = {p for p in (*task.scope.inspect, *task.scope.modify) if _unresolved_path(p) and p.startswith("tests/")}
    mentioned = set()
    for item in items:
        for match in re.finditer(r"tests/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*)+", item):
            if match[0].split("::", 1)[0] in allowed_paths:
                mentioned.add(match[0])
            if len(mentioned) >= 256:
                break
        if len(mentioned) >= 256:
            break
    paths = sorted({t.split("::", 1)[0] for t in mentioned})
    identities = set()
    deadline = time.monotonic() + 10
    for path in paths[:8]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            size = subprocess.run(["git", "cat-file", "-s", head_sha + ":" + path], cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=min(2, remaining), check=False)
            if size.returncode or not size.stdout.strip().isdigit() or int(size.stdout) > 256 * 1024:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            result = subprocess.run(["git", "cat-file", "blob", head_sha + ":" + path], cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=min(2, remaining), check=False)
            if result.returncode or len(result.stdout) != int(size.stdout):
                continue
            tree = ast.parse(result.stdout.decode("utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    identities.add(path + "::" + node.name)
                elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    identities.update(path + "::" + node.name + "::" + member.name for member in node.body
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith("test_"))
        except (OSError, ValueError, SyntaxError, UnicodeError, RecursionError, subprocess.TimeoutExpired):
            continue
    # Store only names actually referenced, never the entire test population.
    return sorted(identity for identity in identities if len(identity) <= 256
        and _safe_test_identity(identity) == (identity, False) and identity in mentioned)[:256]


def _unresolved_anchors(task, items, test_identities):
    from .review_transport import _unresolved_path
    acceptance = {c.id for c in task.acceptance}
    paths = {p for p in (*task.scope.inspect, *task.scope.modify, f".ai/tasks/{task.task_id}.yaml") if _unresolved_path(p)}
    tests = set(test_identities)
    anchors, truncated, test_withheld = [], False, False
    # Tokenization is linear in the bounded source. Nothing outside these exact
    # canonical allowlists can become an anchor, including secret-looking paths.
    token = re.compile(r"(?<![A-Za-z0-9_./:\\-])(?:AC[A-Za-z0-9_-]{1,64}|(?:docs|tests|src|\.ai/tasks)/[A-Za-z0-9_./-]+(?:::[A-Za-z_][A-Za-z0-9_]*)*)(?![A-Za-z0-9_./:\\-])")
    for index, item in enumerate(items):
        rows, seen = [], set()
        item_digest = _checkpoint_digest(item.encode("utf-8"))
        for match in token.finditer(item):
            identity = match[0]
            kind = "AC" if identity in acceptance else "TEST" if identity in tests else "REPOSITORY_PATH" if identity in paths else None
            if kind is None and "::" in identity:
                test_withheld = True
                path = identity.split("::", 1)[0]
                if path in paths:
                    kind, identity = "REPOSITORY_PATH", path
            if kind is not None and (kind, identity) not in seen:
                seen.add((kind, identity))
                rows.append(dict(item=index, item_digest=item_digest, type=kind, identity=identity))
            if len(rows) > 32:
                truncated = True
                break
        if not rows:
            rows = [dict(item=index, item_digest=item_digest, type="OPAQUE_ITEM", identity=None)]
        for row in rows:
            # Reserve room for context/source/Sync envelopes as well as the 8KiB
            # per-view ceiling. Truncation is explicit and deterministic.
            if len(anchors) == 32 or len(_checkpoint_json(anchors + [row])) > 2800:
                truncated = True
                break
            anchors.append(row)
        if truncated:
            break
    limitations = ["SEMANTIC_CAUSE_UNKNOWN", "UNRESOLVED_TEXT_WITHHELD"]
    if truncated:
        limitations.append("ANCHORS_TRUNCATED")
    if test_withheld:
        limitations.append("TEST_IDENTITIES_WITHHELD")
    return anchors, sorted(limitations)


def _pin_staged_unresolved(root, state, task, run, head_sha, failure):
    from .review_transport import FailureDiagnosticError, UNRESOLVED_SOURCE_MAX_BYTES
    receipt = getattr(failure, "_runtime_staged_result", None)
    pin = dict(format="AIOS_RUNTIME_UNRESOLVED_SOURCE", version=1, run_id=run.run_id,
        task=dict(id=task.task_id, revision=task.revision), failed_head_sha=head_sha,
        source_id="runtime-staging:" + run.run_id, staged_head_sha=None, staged_sha256=None,
        staged_bytes=None, task_digest=None, run_digest=None, unresolved_count=None, blocker="SOURCE_MISSING",
        anchors=[], test_identities=[], limitations=["SEMANTIC_CAUSE_UNKNOWN", "UNRESOLVED_TEXT_WITHHELD"])
    path = state.staging / (run.run_id + ".json")
    try:
        if not isinstance(receipt, _StagedResultReceipt):
            pin["blocker"] = "SOURCE_MISMATCH" if path.exists() else "SOURCE_MISSING"
            return pin, None
        pin.update(staged_head_sha=receipt.head_sha, staged_sha256=receipt.sha256,
            staged_bytes=receipt.size, task_digest=receipt.task_digest, run_digest=receipt.run_digest)
        if (receipt.path != path.absolute() or receipt.head_sha != head_sha
                or receipt.run_digest != _checkpoint_digest(_checkpoint_json(asdict(run)))
                or receipt.task_digest != _checkpoint_digest(_checkpoint_json(asdict(task)))):
            raise FailureDiagnosticError("SOURCE_MISMATCH")
        raw = _read_unresolved_bytes(path, UNRESOLVED_SOURCE_MAX_BYTES)
        if len(raw) != receipt.size or _checkpoint_digest(raw) != receipt.sha256:
            raise FailureDiagnosticError("SOURCE_MISMATCH")
        package = _unresolved_package(raw, task, run, head_sha)
        items = list(package.result.unresolved)
        tests = _unresolved_test_inventory(root, task, head_sha, items) if items else []
        anchors, limitations = _unresolved_anchors(task, items, tests)
        pin.update(unresolved_count=len(items), blocker=None, anchors=anchors,
            test_identities=tests, limitations=limitations)
        return pin, items
    except FailureDiagnosticError as exc:
        pin["blocker"] = exc.code
        return pin, None
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        pin["blocker"] = "SOURCE_INVALID"
        return pin, None


def _validate_unresolved_pin(pin, owner):
    from .review_transport import unresolved_diagnostic, _safe_test_identity, _unresolved_path, _is_digest
    fields = {"format", "version", "run_id", "task", "failed_head_sha", "source_id", "staged_head_sha",
        "staged_sha256", "staged_bytes", "task_digest", "run_digest", "unresolved_count", "blocker", "anchors", "test_identities", "limitations"}
    if (not isinstance(pin, dict) or set(pin) != fields or pin["format"] != "AIOS_RUNTIME_UNRESOLVED_SOURCE"
            or type(pin["version"]) is not int or pin["version"] != 1
            or any(pin[k] != owner[k] for k in ("run_id", "task", "failed_head_sha"))
            or pin["source_id"] != "runtime-staging:" + owner["run_id"]
            or pin["run_digest"] is not None and not _is_digest(pin["run_digest"])
            or pin["blocker"] is None and pin["run_digest"] is None
            or not isinstance(pin["test_identities"], list) or len(pin["test_identities"]) > 256
            or any(not isinstance(t, str) or len(t) > 256 or _safe_test_identity(t) != (t, False)
                or not _unresolved_path(t.split("::", 1)[0]) for t in pin["test_identities"])
            or pin["test_identities"] != sorted(set(pin["test_identities"]))):
        raise ValueError("invalid Runtime unresolved pin")
    if pin["blocker"] is not None and (pin["unresolved_count"] is not None or pin["anchors"] or pin["test_identities"]):
        raise ValueError("blocked Runtime pin retains unavailable facts")
    unresolved_diagnostic(dict(owner, private_failure_digest="0" * 64), pin)


def _is_native_timeout(failure: BaseException | None) -> bool:
    """Recognize the adapter's directly chained process timeout."""

    return isinstance(
        failure, (CodexExecutionError, AntigravityExecutionError)
    ) and isinstance(failure.__cause__, subprocess.TimeoutExpired)


def _bounded_native_stream(
    failure: BaseException, stream: str
) -> dict[str, Any]:
    """Capture only one allowlisted native stream with explicit availability."""

    missing = object()
    value: Any = getattr(failure, stream, missing)
    if value is missing and stream == "stdout":
        value = getattr(failure, "output", missing)
    if value is missing and failure.__cause__ is not None:
        return _bounded_native_stream(failure.__cause__, stream)
    if value is missing or value is None:
        return {"availability": "unavailable"}
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8", errors="strict")
        except UnicodeError:
            return {"availability": "unavailable", "reason": "invalid_utf8"}
    elif isinstance(value, str):
        text = value
    else:
        return {"availability": "unavailable", "reason": "unsupported_type"}
    if not text:
        return {"availability": "empty"}
    return {
        "availability": "captured",
        "text": text[:NATIVE_DIAGNOSTIC_LIMIT],
        "truncated": len(text) > NATIVE_DIAGNOSTIC_LIMIT,
    }


def _committed_changed_files(repo: Path, base_sha: str, head_sha: str) -> set[str]:
    output = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        base_sha,
        head_sha,
        strip_stdout=False,
    )
    return {path for path in output.split("\0") if path}


def _git_is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant),
            capture_output=True,
            text=False,
            check=False,
        )
        _decode_utf8(completed.stdout)
        stderr = _decode_utf8(completed.stderr)
    except (OSError, UnicodeError):
        return False
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise RuntimeError(f"Git command failed: {stderr.strip()}")


def _git(repo: Path, *args: str, strip_stdout: bool = True) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=False,
        check=False,
    )
    stdout = _decode_utf8(completed.stdout)
    stderr = _decode_utf8(completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(f"Git command failed: {stderr.strip() or stdout.strip()}")
    return stdout.strip() if strip_stdout else stdout


def _decode_utf8(value: bytes | str) -> str:
    return value.decode("utf-8", errors="strict") if isinstance(value, bytes) else value


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

# Existing Runtime ownership, with one bounded exclusive fence for opted-in
# proof/terminal effects. It is not a RUN reducer or an independent journal.
def _checkpoint_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _checkpoint_json(value) -> bytes:
    # dataclasses/legacy projections contain tuples; the wire has only lists.
    return _original_json(json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False))).encode("ascii")


def _checkpoint_safe_directory(path: Path):
    if not path.is_absolute() or ".." in path.parts:
        raise VerificationContractError("CHECKPOINT_UNSAFE_RUNTIME_DIRECTORY")
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise VerificationContractError("CHECKPOINT_UNSAFE_RUNTIME_DIRECTORY")


def _checkpoint_sync_directory(directory: Path):
    if os.name == "nt":
        # File fsync uses FlushFileBuffers on Windows; opening a directory for
        # POSIX fsync is unavailable. A retained exclusive fence makes an
        # uncertain/partial filesystem effect BLOCKED on reentry.
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _checkpoint_make_directory(path: Path):
    if not path.is_absolute() or ".." in path.parts:
        raise VerificationContractError("CHECKPOINT_UNSAFE_RUNTIME_DIRECTORY")
    for parent in (*reversed(path.parents), path):
        if not os.path.lexists(parent):
            try:
                parent.mkdir()
            except FileExistsError:
                pass
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise VerificationContractError("CHECKPOINT_UNSAFE_RUNTIME_DIRECTORY")


def _checkpoint_capture_inventory(directory: Path, evidence):
    _checkpoint_safe_directory(directory)
    expected = set()
    for item in evidence:
        expected.add(item.evidence_id + ".raw")
        order = int(item.evidence_id.rsplit("-V", 1)[1])
        expected.add(f"observation-{order:03d}.json")
    seen = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in expected or not entry.is_file(follow_symlinks=False) or len(seen) >= 512:
                raise VerificationContractError("CHECKPOINT_UNKNOWN_NATIVE_CAPTURE_OR_RESOURCE_EFFECT")
            seen.add(entry.name)
    if seen != expected:
        raise VerificationContractError("CHECKPOINT_NATIVE_CAPTURE_MISSING")
    return tuple(directory / name for name in sorted(expected))


def _checkpoint_write_once(path: Path, content: bytes):
    _checkpoint_safe_directory(path.parent)
    # Never reconcile an uncertain partial write here or overwrite a fact.
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    _checkpoint_sync_directory(path.parent)


def _checkpoint_fence(directory: Path) -> Path:
    return directory.parent / ".runtime-mutation"


def _local_feedback_contention(fence: Path):
    """Retain negative contention only for an explicit local invocation.

    Preflight rejection and exclusive-create rejection must both be visible
    to the live owner even if the competing caller swallows the exception.
    Historical checkpoint/terminal fences keep their original behavior.
    """
    try:
        current = json.loads(_original_local(fence / "intent.json", MAX_ORIGINAL_PROOF_BYTES))
        if (type(current) is dict and current.get("kind") in {"LOCAL_FEEDBACK", "LOCAL_INVOCATION"}
                and not os.path.lexists(fence / "contested.json")):
            _checkpoint_write_once(fence / "contested.json",
                _checkpoint_json(dict(version=1, kind="COMPETING_RUNTIME_MUTATION")))
    except (ValueError, OSError, RuntimeError, TypeError):
        pass


def _checkpoint_effect_present(directory: Path) -> bool:
    fence = _checkpoint_fence(directory)
    if not os.path.lexists(fence):
        return False
    _local_feedback_contention(fence)
    return True


def _local_feedback_fence(directory: Path, intent: dict):
    fence = _checkpoint_fence(directory)
    _checkpoint_safe_directory(fence)
    with os.scandir(fence) as entries:
        names = []
        for entry in entries:
            if len(names) >= 1 or entry.name != "intent.json":
                raise VerificationContractError("LOCAL_FEEDBACK_COMPETING_OR_UNKNOWN_EFFECT")
            names.append(entry.name)
        if names != ["intent.json"]:
            raise VerificationContractError("LOCAL_FEEDBACK_COMPETING_OR_UNKNOWN_EFFECT")
    if _original_local(fence / "intent.json", MAX_ORIGINAL_PROOF_BYTES) != _checkpoint_json(intent):
        raise VerificationContractError("LOCAL_FEEDBACK_CHANGED_RUNTIME_INTENT")


@contextmanager
def _runtime_effect(directory: Path, intent: dict, *, retain_ambiguity=True):
    _checkpoint_make_directory(directory)
    _checkpoint_safe_directory(directory)
    fence = directory / ".runtime-mutation"
    try:
        fence.mkdir()
    except FileExistsError as exc:
        _local_feedback_contention(fence)
        raise VerificationContractError("CHECKPOINT_COMPETING_OR_INTERRUPTED_EFFECT") from exc
    _checkpoint_sync_directory(directory)
    _checkpoint_write_once(fence / "intent.json", _checkpoint_json(intent))
    try:
        yield
    except BaseException:
        # No unlock/retry after unknown execution, resource or terminal effects.
        # The first immutable intent and all native captures remain the cause.
        if not retain_ambiguity:
            (fence / "intent.json").unlink()
            fence.rmdir()
            _checkpoint_sync_directory(directory)
        raise
    else:
        if intent.get("kind") in {"LOCAL_FEEDBACK", "LOCAL_INVOCATION"}:
            _local_feedback_fence(directory / "l3-02", intent)
        (fence / "intent.json").unlink()
        fence.rmdir()
        _checkpoint_sync_directory(directory)


@contextmanager
def _checkpoint_mutation(directory: Path, intent: dict, *, retain_ambiguity=True):
    # No separate lock/reducer: legacy terminal writes and prospective effects
    # exclude one another at this same Runtime-owned per-RUN boundary.
    _checkpoint_make_directory(directory)
    _checkpoint_safe_directory(directory)
    with _runtime_effect(directory.parent, intent, retain_ambiguity=retain_ambiguity):
        yield


def evidence_data(item: Evidence) -> dict:
    return dict(evidence_id=item.evidence_id, run_id=item.run_id, subject_sha=item.subject_sha,
        type=item.type, source=asdict(item.source), result=asdict(item.result), raw=dict(path=item.raw_path),
        **({"verification": item.verification} if item.verification is not None else {}))


def persist_failure(root: Path, *, state: RuntimeState, task: Task, run: Run,
                    run_path: Path, failure: BaseException,
                    observation_tracker: RunObservationTracker | None = None,
                    observation_path: Path | None = None, interruption_phase: str | None = None,
                    transport: bool = True, transport_repo: Path | None = None,
                    verification_subject_sha: str | None = None) -> None:
    """Keep original FAILURE ownership; share exclusion with prospective proof."""
    directory = state.verification / run.run_id / "l3-02"
    prospective = os.path.lexists(directory)
    try:
        # An exact existing terminal is immutable, including its failure cause.
        if prospective and (os.path.lexists(state.results / (run.run_id + ".json"))
                or os.path.lexists(state.failures / (run.run_id + ".json"))
                or os.path.lexists(directory / "terminal-intent.json")):
            return
        with _runtime_effect(directory.parent, dict(version=1, kind="FAILURE", run_id=run.run_id),
                             retain_ambiguity=prospective):
            if os.path.lexists(directory) and (os.path.lexists(state.results / (run.run_id + ".json"))
                    or os.path.lexists(directory / "terminal-intent.json")):
                return
            _persist_failure(root, state=state, task=task, run=run, run_path=run_path, failure=failure,
                observation_tracker=observation_tracker, observation_path=observation_path,
                interruption_phase=interruption_phase, transport=transport, transport_repo=transport_repo,
                verification_subject_sha=verification_subject_sha)
            if prospective:
                failure_path = state.failures / (run.run_id + ".json")
                _original_local(failure_path, MAX_ORIGINAL_PROOF_BYTES)
                with failure_path.open("r+b") as stream:
                    os.fsync(stream.fileno())
                _checkpoint_sync_directory(failure_path.parent)
    except Exception:
        # Never replace the original exception with bookkeeping/recovery policy.
        return
