"""Minimal native Codex CLI adapter."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .artifacts import (
    ArtifactValidationError,
    ResultPackage,
    validate_evidence,
    validate_structural_result,
)
from .native_execution_policy import resolve_native_execution_policy
from .run import Run
from .run_observation import TokenUsage
from .review import RemediationExecution
from .task import Task


ProcessRunner = Callable[..., subprocess.CompletedProcess[bytes]]


class ExecutionPolicy(Protocol):
    """Provider-neutral admitted policy consumed by native mechanics."""

    authorizes_mutation: bool
    response_budget_minutes: int
    process_watchdog_seconds: int


RESULT_PACKAGE_SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "result_package.json"
).resolve()
REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "remediation_result_package.json"
).resolve()
REPAIR_RESULT_PACKAGE_SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "repair_result_package.json"
).resolve()
FINALIZE_CANDIDATE_RESULT_PACKAGE_SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "finalize_candidate_result_package.json"
).resolve()

_NATIVE_EXECUTOR_INSTRUCTION = (
    "You are the already-selected native Executor inside an admitted AIOS execution. "
    "Perform the authorized implementation work in the supplied input directly. "
    "Repository-owned Human-facing worker surfaces and AIOS operator or dispatch "
    "launchers are outside this execution role: do not invoke $aios-worker, "
    "/aios-renew-worker, aios run, aios remediate, aios repair, or an equivalent "
    "launcher to perform the work. Do not authorize, admit, dispatch, or launch "
    "another AIOS execution. Use the supplied bounded execution context to begin "
    "the authorized implementation directly. Do not perform repository-wide "
    "rediscovery when that context is sufficient. Runtime is the canonical "
    "verification owner: do not spend execution time on baseline, full, or "
    "canonical verification before implementation or duplicate it for ceremony. "
)


def native_execution_context(*, run: Run, operation: str) -> dict[str, Any]:
    """Describe the already-admitted native role without changing kernel artifacts."""

    return {
        "role": "NATIVE_EXECUTOR",
        "selected_executor": run.executor,
        "operation": operation,
        "already_admitted": True,
        "direct_implementation": True,
        "operator_dispatch_authority": False,
        "runtime_verification_authority": False,
    }


from .execution_profile import (
    ResolvedExecutionProfile,
    default_execution_profile,
)



class CodexExecutionError(RuntimeError):
    """Raised when the native Codex process cannot complete successfully."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None,
        stdout: bytes | str | None = None,
        stderr: bytes | str | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class CodexOutputError(CodexExecutionError):
    """Raised when successful Codex output is not a canonical result package."""


class CodexNativeProbeAbsentError(CodexExecutionError):
    """Raised when source-bound native-probe is absent and live delivery cannot be proven."""

    def __init__(
        self,
        message: str = "source-bound native-probe absent: live Codex delivery cannot be proven",
        *,
        stdout: bytes | str | None = None,
        stderr: bytes | str | None = None,
    ) -> None:
        super().__init__(message, exit_code=None, stdout=stdout, stderr=stderr)


class CodexNativeDeliveryError(CodexExecutionError):
    """Raised when native Codex delivery fails or is rejected."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        stdout: bytes | str | None = None,
        stderr: bytes | str | None = None,
    ) -> None:
        super().__init__(message, exit_code=exit_code, stdout=stdout, stderr=stderr)


@dataclass(frozen=True, slots=True)
class CodexRecipientAcknowledgment:
    """Explicit recipient acknowledgment from the authentic live native recipient."""

    receipt_id: str
    feedback_digest: str
    acknowledged: bool = True
    recipient_identity: str = ""

    def __post_init__(self) -> None:
        from .verification_contract import _original_hash

        _original_hash(self.receipt_id)
        _original_hash(self.feedback_digest)


@dataclass(frozen=True, slots=True)
class CodexNativeReceipt:
    """Source-correlated receipt proving authentic native recipient delivery."""

    receipt_id: str
    feedback_digest: str
    run_id: str
    checkpoint_ordinal: int
    executor: str = "codex"
    delivery_mode: str = "AUTHENTIC_NATIVE_RECIPIENT"
    correlation_digest: str = ""

    def __post_init__(self) -> None:
        from .verification_contract import _original_hash

        _original_hash(self.receipt_id)
        _original_hash(self.feedback_digest)
        if self.correlation_digest:
            _original_hash(self.correlation_digest)


class AuthenticCodexProbe:
    """Source-authenticated native recipient probe for live Codex execution."""

    def __init__(
        self,
        *,
        acknowledged_deliveries: list[tuple[Any, str]] | None = None,
        reject: bool = False,
        interrupt: bool = False,
    ) -> None:
        self.acknowledged_deliveries = (
            acknowledged_deliveries if acknowledged_deliveries is not None else []
        )
        self.reject = reject
        self.interrupt = interrupt
        self._history: list[dict[str, Any]] = []

    def confirm_authentic_native_delivery(
        self,
        *,
        feedback: Any,
        receipt_id: str,
        run: Run,
        task: Task,
        lease: Any,
        profile: ResolvedExecutionProfile | None,
        checkpoint_ordinal: int,
        checkpoint_digest: str,
    ) -> CodexRecipientAcknowledgment:
        if self.interrupt:
            raise KeyboardInterrupt("native probe delivery interrupted")
        if self.reject:
            return CodexRecipientAcknowledgment(
                receipt_id=receipt_id,
                feedback_digest=feedback.digest,
                acknowledged=False,
            )

        if run.executor != "codex":
            raise CodexNativeDeliveryError("executor mismatch: expected codex")
        if lease is not None and getattr(lease, "released", False):
            raise CodexNativeDeliveryError("lease loss: lease released")

        fb_data = getattr(feedback, "data", {})
        binding = fb_data.get("binding", {})
        if binding.get("run_id") != run.run_id:
            raise CodexNativeDeliveryError("cross-RUN delivery rejected by recipient")
        if binding.get("task_id") != task.task_id:
            raise CodexNativeDeliveryError("cross-TASK delivery rejected by recipient")
        if binding.get("checkpoint_ordinal") != checkpoint_ordinal:
            raise CodexNativeDeliveryError("checkpoint ordinal mismatch")

        self.acknowledged_deliveries.append((feedback, receipt_id))
        self._history.append({
            "receipt_id": receipt_id,
            "feedback_digest": feedback.digest,
            "run_id": run.run_id,
            "checkpoint_ordinal": checkpoint_ordinal,
            "checkpoint_digest": checkpoint_digest,
        })

        return CodexRecipientAcknowledgment(
            receipt_id=receipt_id,
            feedback_digest=feedback.digest,
            acknowledged=True,
            recipient_identity=f"codex-recipient:{run.run_id}:{checkpoint_ordinal}",
        )

    confirm_native_delivery = confirm_authentic_native_delivery


class CodexAdapter:
    """Invoke Codex CLI once and normalize its canonical output."""

    executor = "codex"

    def __init__(
        self,
        *,
        runner: ProcessRunner = subprocess.run,
        schema_path: str | Path = RESULT_PACKAGE_SCHEMA_PATH,
        execution_policy: ExecutionPolicy | None = None,
        execution_profile: ResolvedExecutionProfile | None = None,
        native_probe: Any = None,
    ) -> None:
        self._runner = runner
        self._schema_path = Path(schema_path)
        self._execution_policy = (
            execution_policy
            if execution_policy is not None
            else resolve_native_execution_policy(authorizes_mutation=False)
        )
        self._execution_profile = execution_profile
        self._native_probe = native_probe
        self._active_run: Run | None = None
        self._active_task: Task | None = None
        self._active_boundary: Any = None
        self._active_lease: Any = None
        self._active_leases: Any = None
        self._execution_thread: int | None = None
        self._last_receipt: CodexNativeReceipt | None = None
        self._receipt_history: list[CodexNativeReceipt] = []

    @property
    def execution_profile(self) -> ResolvedExecutionProfile | None:
        """Return the bound execution profile, if any."""
        return self._execution_profile

    @property
    def native_probe(self) -> Any:
        """Return the bound native probe, if any."""
        if self._native_probe is not None:
            return self._native_probe
        return getattr(self._runner, "native_probe", None)

    @property
    def has_native_probe(self) -> bool:
        """Return whether an authentic native recipient probe is established."""
        probe = self.native_probe
        if probe is None:
            return False
        return hasattr(probe, "confirm_authentic_native_delivery") or hasattr(
            probe, "confirm_native_delivery"
        )

    @property
    def last_native_receipt(self) -> CodexNativeReceipt | None:
        """Return the latest source-correlated native delivery receipt, if any."""
        return self._last_receipt

    @property
    def receipt_history(self) -> tuple[CodexNativeReceipt, ...]:
        """Return all source-correlated native receipts recorded during execution."""
        return tuple(self._receipt_history)

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        """Execute an unchanged TASK/RUN pair through native Codex CLI."""

        import threading

        self._active_run = run
        self._active_task = task
        self._execution_thread = threading.get_ident()
        try:
            command = self.command_for(
                run,
                schema_path=self._schema_path,
                authorizes_mutation=self._execution_policy.authorizes_mutation,
                execution_profile=self._execution_profile,
            )
            prompt = self.prompt_for(task=task, run=run)
            try:
                completed = self._runner(
                    command,
                    input=prompt.encode("utf-8", errors="strict"),
                    capture_output=True,
                    text=False,
                    check=False,
                    timeout=self._execution_policy.process_watchdog_seconds,
                )
                stdout = _decode_utf8(completed.stdout)
                stderr = _decode_utf8(completed.stderr)
            except subprocess.TimeoutExpired as exc:
                raise CodexExecutionError(
                    "Codex CLI exceeded the "
                    f"{self._execution_policy.process_watchdog_seconds}-second "
                    "process watchdog "
                    f"({self._execution_policy.response_budget_minutes}-minute "
                    "native response budget)",
                    exit_code=None,
                    stdout=exc.stdout,
                    stderr=exc.stderr,
                ) from exc
            except (OSError, UnicodeError) as exc:
                raise CodexExecutionError(
                    f"Codex CLI invocation failed: {exc}",
                    exit_code=None,
                ) from exc

            usage = extract_token_usage(stdout)
            self._record_usage(usage, completed)

            if completed.returncode != 0:
                detail = stderr.strip() or stdout.strip()
                message = f"Codex CLI exited with code {completed.returncode}"
                if detail:
                    message = f"{message}: {detail}"
                raise CodexExecutionError(
                    message,
                    exit_code=completed.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )

            return self._normalize(stdout, stderr=stderr)
        finally:
            self._active_run = None
            self._active_task = None
            self._execution_thread = None

    def confirm_local_feedback(
        self,
        *,
        feedback: Any,
        receipt_id: str,
    ) -> Any:
        """Confirm feedback delivery to the live native Codex recipient."""
        import hashlib
        import threading
        from .intra_run_proof_contract import LocalFeedbackConfirmation

        current_thread = threading.get_ident()
        if self._execution_thread is None or current_thread != self._execution_thread:
            raise CodexNativeDeliveryError("feedback confirmation attempted outside native execution thread")

        if self._active_run is None or self._active_task is None:
            raise CodexNativeDeliveryError("no active live native invocation established")

        probe = self.native_probe
        if probe is None:
            raise CodexNativeProbeAbsentError(
                "source-bound native-probe absent: live Codex delivery cannot be proven"
            )

        if not self.has_native_probe:
            raise CodexNativeDeliveryError(
                "injected synthetic callback cannot prove authentic native recipient delivery"
            )

        fb_data = getattr(feedback, "data", None)
        if not isinstance(fb_data, Mapping):
            raise CodexNativeDeliveryError("malformed feedback: data mapping required")

        binding = fb_data.get("binding")
        if not isinstance(binding, Mapping):
            raise CodexNativeDeliveryError("malformed feedback: binding mapping required")

        if binding.get("run_id") != self._active_run.run_id:
            raise CodexNativeDeliveryError(
                f"cross-RUN feedback rejected: {binding.get('run_id')} != {self._active_run.run_id}"
            )
        if binding.get("task_id") != self._active_task.task_id:
            raise CodexNativeDeliveryError(
                f"cross-TASK feedback rejected: {binding.get('task_id')} != {self._active_task.task_id}"
            )

        checkpoint_ordinal = binding.get("checkpoint_ordinal")
        if not isinstance(checkpoint_ordinal, int) or checkpoint_ordinal < 1:
            raise CodexNativeDeliveryError("malformed feedback: invalid checkpoint ordinal")

        checkpoint_digest = binding.get("checkpoint_digest")
        if not checkpoint_digest or not isinstance(checkpoint_digest, str):
            raise CodexNativeDeliveryError("malformed feedback: missing checkpoint digest")

        feedback_digest = getattr(feedback, "digest", None)
        if any(
            r.receipt_id == receipt_id or r.feedback_digest == feedback_digest
            for r in self._receipt_history
        ):
            raise CodexNativeDeliveryError("feedback replay rejected: receipt or feedback already confirmed")

        if self._active_lease is not None:
            if getattr(self._active_lease, "run_id", None) != self._active_run.run_id:
                raise CodexNativeDeliveryError("lease loss: lease does not match active RUN")
            if getattr(self._active_lease, "released", False):
                raise CodexNativeDeliveryError("lease loss: active run lease has been released")
            if (
                self._active_leases is not None
                and self._active_leases.holder(self._active_task.task_id) is not self._active_lease
            ):
                raise CodexNativeDeliveryError("lease loss: lease holder changed or lost")

        if hasattr(probe, "confirm_authentic_native_delivery"):
            probe_result = probe.confirm_authentic_native_delivery(
                feedback=feedback,
                receipt_id=receipt_id,
                run=self._active_run,
                task=self._active_task,
                lease=self._active_lease,
                profile=self._execution_profile,
                checkpoint_ordinal=checkpoint_ordinal,
                checkpoint_digest=checkpoint_digest,
            )
        elif hasattr(probe, "confirm_native_delivery"):
            try:
                probe_result = probe.confirm_native_delivery(
                    feedback=feedback,
                    receipt_id=receipt_id,
                    run=self._active_run,
                    task=self._active_task,
                    lease=self._active_lease,
                    profile=self._execution_profile,
                    checkpoint_ordinal=checkpoint_ordinal,
                    checkpoint_digest=checkpoint_digest,
                )
            except TypeError:
                probe_result = probe.confirm_native_delivery(
                    feedback=feedback, receipt_id=receipt_id
                )
        else:
            raise CodexNativeDeliveryError(
                "injected synthetic callback cannot prove authentic native recipient delivery"
            )

        if probe_result is False or probe_result is None:
            raise CodexNativeDeliveryError("native probe rejected delivery")
        if isinstance(probe_result, CodexRecipientAcknowledgment) and not probe_result.acknowledged:
            raise CodexNativeDeliveryError("native probe rejected delivery")

        correlation_input = f"{self._active_run.run_id}:{checkpoint_ordinal}:{receipt_id}:{feedback.digest}"
        correlation_digest = hashlib.sha256(correlation_input.encode("ascii")).hexdigest()

        receipt = CodexNativeReceipt(
            receipt_id=receipt_id,
            feedback_digest=feedback.digest,
            run_id=self._active_run.run_id,
            checkpoint_ordinal=checkpoint_ordinal,
            executor=self.executor,
            delivery_mode="AUTHENTIC_NATIVE_RECIPIENT",
            correlation_digest=correlation_digest,
        )
        self._last_receipt = receipt
        self._receipt_history.append(receipt)

        return LocalFeedbackConfirmation(receipt_id, feedback.digest)

    confirm_native_feedback = confirm_local_feedback

    def consume_same_executor_feedback(
        self,
        request: Any,
        *,
        boundary: Any,
        lease: Any,
        runtime: Any = None,
    ) -> Any:
        """Consume prospective same-executor feedback through Runtime."""
        self._active_boundary = boundary
        self._active_lease = lease
        self._active_leases = getattr(boundary, "_leases", None)
        target_runtime = runtime
        if target_runtime is None and hasattr(boundary, "_prospective_runtime"):
            target_runtime = boundary._prospective_runtime
        if target_runtime is None:
            raise CodexExecutionError("no Runtime available for feedback consumption", exit_code=None)
        try:
            return target_runtime.consume_same_executor_feedback(
                request, boundary=boundary, lease=lease, adapter=self
            )
        finally:
            self._active_boundary = None
            self._active_lease = None
            self._active_leases = None

    def execute_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage:
        """Execute one narrow remediation through one native Codex process."""

        import threading

        self._active_run = execution.run
        self._active_task = None
        self._execution_thread = threading.get_ident()
        try:
            command = self.command_for(
                execution.run,
                schema_path=REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH,
                authorizes_mutation=self._execution_policy.authorizes_mutation,
                execution_profile=self._execution_profile,
            )
            try:
                completed = self._runner(
                    command,
                    input=self.remediation_prompt_for(execution=execution).encode(
                        "utf-8", errors="strict"
                    ),
                    capture_output=True,
                    text=False,
                    check=False,
                    timeout=self._execution_policy.process_watchdog_seconds,
                )
                stdout = _decode_utf8(completed.stdout)
                stderr = _decode_utf8(completed.stderr)
            except subprocess.TimeoutExpired as exc:
                raise CodexExecutionError(
                    "Codex CLI exceeded the "
                    f"{self._execution_policy.process_watchdog_seconds}-second "
                    "process watchdog "
                    f"({self._execution_policy.response_budget_minutes}-minute "
                    "native response budget)",
                    exit_code=None,
                    stdout=exc.stdout,
                    stderr=exc.stderr,
                ) from exc
            except (OSError, UnicodeError) as exc:
                raise CodexExecutionError(
                    f"Codex CLI invocation failed: {exc}", exit_code=None
                ) from exc

            usage = extract_token_usage(stdout)
            self._record_usage(usage, completed)

            if completed.returncode != 0:
                detail = stderr.strip() or stdout.strip()
                message = f"Codex CLI exited with code {completed.returncode}"
                if detail:
                    message = f"{message}: {detail}"
                raise CodexExecutionError(
                    message,
                    exit_code=completed.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
            return self._normalize(stdout, stderr=stderr)
        finally:
            self._active_run = None
            self._active_task = None
            self._execution_thread = None

    def execute_repair(self, *, execution: Mapping[str, Any]) -> ResultPackage:
        """Execute one bound pre-PASS continuation through one Codex process."""

        import threading

        run = execution.get("run")
        if not isinstance(run, Run):
            raise CodexExecutionError("REPAIR execution has no bound RUN", exit_code=None)
        repair = execution.get("repair")
        action = repair.get("action") if isinstance(repair, Mapping) else None
        self._active_run = run
        self._active_task = None
        self._execution_thread = threading.get_ident()
        try:
            command = self.command_for(
                run,
                schema_path=(
                    FINALIZE_CANDIDATE_RESULT_PACKAGE_SCHEMA_PATH
                    if action == "FINALIZE_CANDIDATE"
                    else REPAIR_RESULT_PACKAGE_SCHEMA_PATH
                ),
                authorizes_mutation=self._execution_policy.authorizes_mutation,
                execution_profile=self._execution_profile,
            )
            prompt = self.repair_prompt_for(execution=execution)
            try:
                completed = self._runner(
                    command, input=prompt.encode("utf-8", errors="strict"),
                    capture_output=True, text=False, check=False,
                    timeout=self._execution_policy.process_watchdog_seconds,
                )
                stdout = _decode_utf8(completed.stdout)
                stderr = _decode_utf8(completed.stderr)
            except subprocess.TimeoutExpired as exc:
                raise CodexExecutionError(
                    "Codex CLI exceeded the "
                    f"{self._execution_policy.process_watchdog_seconds}-second "
                    "process watchdog "
                    f"({self._execution_policy.response_budget_minutes}-minute "
                    "native response budget)",
                    exit_code=None,
                    stdout=exc.stdout,
                    stderr=exc.stderr,
                ) from exc
            except (OSError, UnicodeError) as exc:
                raise CodexExecutionError(
                    f"Codex CLI invocation failed: {exc}", exit_code=None
                ) from exc

            usage = extract_token_usage(stdout)
            self._record_usage(usage, completed)

            if completed.returncode != 0:
                raise CodexExecutionError(
                    f"Codex CLI exited with code {completed.returncode}",
                    exit_code=completed.returncode, stdout=stdout, stderr=stderr,
                )
            return self._normalize(stdout, stderr=stderr)
        finally:
            self._active_run = None
            self._active_task = None
            self._execution_thread = None

    def _record_usage(
        self, usage: TokenUsage | None, completed: Any = None
    ) -> None:
        if completed is not None and usage is not None:
            try:
                completed.aios_token_usage = {
                    "input_tokens": usage.input_tokens,
                    "cached_input_tokens": usage.cached_input_tokens,
                    "output_tokens": usage.output_tokens,
                }
            except Exception:
                pass
        record_fn = getattr(self._runner, "record_token_usage", None)
        if callable(record_fn):
            record_fn(usage)

    @staticmethod
    def command_for(
        run: Run,
        schema_path: str | Path = RESULT_PACKAGE_SCHEMA_PATH,
        *,
        authorizes_mutation: bool | None = None,
        execution_profile: ResolvedExecutionProfile | None = None,
    ) -> tuple[str, ...]:
        """Build the native non-interactive Codex command."""

        profile = execution_profile
        if profile is None:
            profile = default_execution_profile(executor="codex", run_id=run.run_id)

        return (
            "codex",
            "exec",
            "--cd",
            run.workspace,
            "-m",
            profile.model,
            "-c",
            f'model_reasoning_effort="{profile.reasoning_effort}"',
            "--sandbox",
            (
                "danger-full-access"
                if authorizes_mutation is True
                else "read-only"
            ),
            "--output-schema",
            str(schema_path),
            "--color",
            "never",
            "--json",
            "-",
        )

    @staticmethod
    def prompt_for(*, task: Task, run: Run) -> str:
        """Serialize executor context without Runtime-owned verification commands."""

        task_input = asdict(task)
        task_input.pop("verification")
        canonical_input = json.dumps(
            {
                "execution_context": native_execution_context(
                    run=run, operation="PRIMARY"
                ),
                "task": task_input,
                "run": asdict(run),
            },
            sort_keys=True,
        )
        return (
            _NATIVE_EXECUTOR_INSTRUCTION
            + "Execute the canonical TASK within its bound RUN. "
            "Do not reinterpret its requirements. Runtime owns all verification: "
            "do not execute canonical task verification commands and do not generate "
            "verification EVIDENCE. Minimum implementation-local sanity checks on the "
            "changed surface are permitted when useful, but they are not canonical verification "
            "or EVIDENCE. If the TASK "
            "requires repository changes, commit the final permitted implementation "
            "state before returning the ResultPackage; do not push. Bind result.head_sha "
            "to that final committed Git HEAD. Return only one structural JSON object "
            "with keys 'result' and 'evidence'. Set root evidence to [] and every "
            "claim.evidence to []; Runtime will construct canonical EVIDENCE.\n"
            "Claims must affirm concrete implementation properties actually observed or "
            "established. Omit an acceptance ID from claim.satisfies if it cannot be "
            "truthfully affirmed; lack of confirmation alone is not unresolved and leaves "
            "that ID for Runtime's missing-acceptance gate. result.unresolved may contain "
            "only concrete remaining Executor-owned implementation work or a material "
            "supplied TASK-contract conflict. Pending Runtime verification or EVIDENCE, "
            "Reviewer judgment, publication, roadmap advancement, and later lifecycle "
            "work are not unresolved. When implementation is complete with no such "
            "conflict, return result.unresolved=[] without claiming Runtime verification "
            "success.\n"
            f"CANONICAL_INPUT:\n{canonical_input}"
        )

    @staticmethod
    def remediation_prompt_for(*, execution: RemediationExecution) -> str:
        """Serialize only the shared narrow remediation contract."""

        execution_input = asdict(execution)
        execution_input["remediation"].pop("affected_verification")
        execution_input["execution_context"] = native_execution_context(
            run=execution.run, operation="REMEDIATION"
        )
        canonical_input = json.dumps(execution_input, sort_keys=True)
        return (
            _NATIVE_EXECUTOR_INSTRUCTION
            + "Execute exactly one canonical narrow REMEDIATION. Do not run or "
            "restart the original TASK, rediscover the repository, perform "
            "semantic review, or retry. Change only remediation.modification_scope. "
            "For CODE_FIX, commit the permitted remediation delta before returning; "
            "for EVIDENCE_ONLY, do not create a code commit and bind "
            "result.head_sha to the unchanged admitted run.base_sha, which is the "
            "actual final Git HEAD, not remediation.reviewed_sha. For every action, "
            "bind result.head_sha to the actual final Git HEAD. Do not push. "
            "Runtime owns affected verification: do not execute verification "
            "commands and do not generate verification EVIDENCE. Minimum "
            "implementation-local sanity checks on the changed surface are permitted "
            "when useful, but they are not canonical verification or EVIDENCE. Return "
            "one structural ResultPackage with empty root evidence, result.claims, "
            "and result.unresolved.\nREMEDIATION_INPUT:\n"
            f"{canonical_input}"
        )

    @staticmethod
    def repair_prompt_for(*, execution: Mapping[str, Any]) -> str:
        """Serialize the shared REPAIR contract without Runtime verification."""

        data = dict(execution)
        run = data.get("run")
        if isinstance(run, Run):
            data["run"] = asdict(run)
            data["execution_context"] = native_execution_context(
                run=run, operation="REPAIR"
            )
        canonical_input = json.dumps(data, sort_keys=True)
        return (
            _NATIVE_EXECUTOR_INSTRUCTION
            + "Execute exactly one canonical REPAIR continuation bound to the supplied "
            "exact failed RUN and failed-lineage context. The four REPAIR actions are "
            "distinct. CODE_FIX authorizes mutation only to correct an established defect "
            "and requires committing the final permitted state. NO_CHANGE authorizes no "
            "repository mutation and does not permit resuming unfinished original TASK "
            "implementation. CONTINUE_IMPLEMENTATION authorizes mutation to resume the "
            "unfinished original TASK implementation from that exact failed lineage and "
            "perform the remaining authorized TASK work needed to produce the permitted "
            "implementation delta. For CONTINUE_IMPLEMENTATION, unfinished original TASK "
            "work is not prohibited merely because equivalent work would normally occur "
            "during PRIMARY; necessary bounded repository inspection, discovery, or live "
            "capture required by the original TASK is permitted as part of that unfinished "
            "implementation. This permission does not authorize repository-wide "
            "rediscovery, new intent, or work outside the TASK and REPAIR scope. "
            "FINALIZE_CANDIDATE authorizes no repository mutation. For that action, inspect "
            "only the supplied exact clean failed candidate and bounded TASK/REPAIR context "
            "far enough to return the missing structural ResultPackage for the complete "
            "original TASK. Do not edit files, commit, push, or resume or repeat "
            "implementation; bind result.head_sha to the unchanged failed_head_sha. If the "
            "candidate is incomplete, report truthful unresolved work instead of changing "
            "it or fabricating completion. Claims for every REPAIR action must affirm "
            "concrete implementation properties actually observed or established. If an "
            "acceptance criterion cannot be truthfully affirmed, omit it from "
            "claim.satisfies; lack of confirmation alone is not unresolved, and Runtime's "
            "missing-acceptance gate will handle uncovered IDs. result.unresolved is only "
            "for concrete remaining Executor-owned implementation work or a material "
            "supplied TASK-contract conflict, never pending downstream authority work. "
            "For FINALIZE_CANDIDATE, return affirmative complete claims and "
            "result.unresolved=[] when supportable without claiming Runtime verification "
            "success; otherwise return truthful partial or zero claims and bounded "
            "unresolved only for actual incompleteness or conflict. Follow "
            "repair.instructions and limit every mutation to repair.modification_scope. "
            "Successful CONTINUE_IMPLEMENTATION work must commit the final permitted "
            "repository state and bind result.head_sha to final committed Git HEAD. Do not "
            "create or restart a fresh PRIMARY lineage, allocate a new TASK or RUN, "
            "synchronize to a different candidate, retry this admitted continuation, "
            "reroute or fall back to another Executor, widen scope, recursively continue, "
            "perform semantic review, or invoke an AIOS operator or worker launcher. Do not "
            "push. Runtime owns the "
            "complete original TASK verification: do not execute canonical verification "
            "commands or construct EVIDENCE. Minimum implementation-local sanity checks on "
            "the changed surface remain permitted when useful, but they are not canonical "
            "verification or EVIDENCE. Runtime derives and persists canonical "
            "result.changed_files from the original TASK root base to final HEAD; do not "
            "reconstruct or enumerate that historical file set. Return one structural "
            "ResultPackage for the complete original TASK contract with empty root evidence "
            "and every claim.evidence empty. Structural result.changed_files may contain "
            "only the narrow repair delta or be empty.\nREPAIR_INPUT:\n" + canonical_input
        )

    @staticmethod
    def _normalize(stdout: str, *, stderr: str) -> ResultPackage:
        try:
            payload = None
            stripped = stdout.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    direct = json.loads(stripped)
                    if (
                        isinstance(direct, Mapping)
                        and "result" in direct
                        and "evidence" in direct
                    ):
                        payload = direct
                except Exception:
                    pass

            if payload is None:
                last_agent_text = None
                direct_line_payload = None
                for line in stdout.splitlines():
                    line_str = line.strip()
                    if not line_str or not line_str.startswith("{"):
                        continue
                    try:
                        event = json.loads(line_str)
                    except Exception:
                        continue
                    if not isinstance(event, Mapping):
                        continue
                    if event.get("type") == "item.completed":
                        item = event.get("item")
                        if (
                            isinstance(item, Mapping)
                            and item.get("type") == "agent_message"
                        ):
                            text = item.get("text")
                            if isinstance(text, str):
                                last_agent_text = text
                    if "result" in event and "evidence" in event:
                        direct_line_payload = event

                if last_agent_text is not None:
                    payload = json.loads(last_agent_text)
                elif direct_line_payload is not None:
                    payload = direct_line_payload

            if payload is None:
                payload = json.loads(stdout)

            root = _mapping(payload, "Codex output")
            result = validate_structural_result(root["result"])
            evidence_data = root["evidence"]
            if not isinstance(evidence_data, list):
                raise TypeError("evidence must be a list")
            evidence = tuple(validate_evidence(item) for item in evidence_data)
        except (
            ArtifactValidationError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            UnicodeError,
        ) as exc:
            raise CodexOutputError(
                f"Codex CLI returned invalid canonical output: {exc}",
                exit_code=0,
                stdout=stdout,
                stderr=stderr,
            ) from exc

        return ResultPackage(result=result, evidence=evidence)


CODEX_USAGE_EVENT = "turn.completed"


def extract_token_usage(output: Any) -> TokenUsage | None:
    """Extract and map exact Codex token counters from the execution output."""

    if output is None:
        return None

    if isinstance(output, bytes):
        try:
            output = output.decode("utf-8", errors="replace")
        except Exception:
            return None

    candidates: list[TokenUsage] = []

    if isinstance(output, Mapping):
        if output.get("type") == CODEX_USAGE_EVENT:
            usage_data = output.get("usage")
            if isinstance(usage_data, Mapping):
                mapped = _map_codex_usage(usage_data)
            elif "input_tokens" in output and "output_tokens" in output:
                mapped = _map_codex_usage(output)
            else:
                mapped = None
            if mapped is not None:
                candidates.append(mapped)
            else:
                return None
        else:
            return None
    elif isinstance(output, str):
        for line in output.splitlines():
            line_str = line.strip()
            if not line_str or not line_str.startswith("{"):
                continue
            try:
                record = json.loads(line_str)
            except Exception:
                continue
            if not isinstance(record, Mapping):
                continue
            if record.get("type") == CODEX_USAGE_EVENT:
                usage_data = record.get("usage")
                if isinstance(usage_data, Mapping):
                    mapped = _map_codex_usage(usage_data)
                elif "input_tokens" in record and "output_tokens" in record:
                    mapped = _map_codex_usage(record)
                else:
                    mapped = None
                if mapped is not None:
                    candidates.append(mapped)
                else:
                    return None

    if not candidates:
        return None

    first = candidates[0]
    for other in candidates[1:]:
        if other != first:
            return None

    return first


def _map_codex_usage(usage: Any) -> TokenUsage | None:
    if not isinstance(usage, Mapping):
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")

    if (
        isinstance(input_tokens, bool)
        or not isinstance(input_tokens, int)
        or input_tokens < 0
    ):
        return None
    if (
        isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens < 0
    ):
        return None

    cached_candidates: list[int] = []
    if "cached_input_tokens" in usage:
        val = usage["cached_input_tokens"]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            return None
        cached_candidates.append(val)
    if "cached_tokens" in usage:
        val = usage["cached_tokens"]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            return None
        cached_candidates.append(val)
    prompt_details = usage.get("prompt_tokens_details")
    if isinstance(prompt_details, Mapping) and "cached_tokens" in prompt_details:
        val = prompt_details["cached_tokens"]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            return None
        cached_candidates.append(val)

    if not cached_candidates:
        return None

    if len(set(cached_candidates)) > 1:
        return None

    cached_input_tokens = cached_candidates[0]
    if cached_input_tokens > input_tokens:
        return None

    return TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
    )


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping")
    return value


def _decode_utf8(value: bytes | str) -> str:
    """Decode captured subprocess bytes synchronously and fail closed."""

    return value.decode("utf-8", errors="strict") if isinstance(value, bytes) else value
