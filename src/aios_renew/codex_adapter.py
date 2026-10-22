"""Minimal native Codex CLI adapter."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from uuid import uuid4
from collections.abc import Callable, Mapping
from dataclasses import asdict
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
        g3_native_source=None,
    ) -> None:
        self._runner = runner
        self._schema_path = Path(schema_path)
        self._execution_policy = (
            execution_policy
            if execution_policy is not None
            else resolve_native_execution_policy(authorizes_mutation=False)
        )
        self._execution_profile = execution_profile
        self.g3_native_source = g3_native_source
        self._g3_process = None
        self._g3_exchange = None
        self._g3_owner = None
        self._g3_feedback_pending = False

    def execute_g3(self, *, task, run, runtime, boundary, lease, operation, execution=None):
        """One pinned controlled offline native process, within the sole frame.

        This explicit transport is not a CLI resume/retry or a production model
        provider. The selected program owns code strategy. Runtime owns all
        checkpoint scheduling, factual feedback and terminal proof consumption.
        """
        from .ka01_admission import G3NativeSource, _g3_native_source, _g3_semantic, _bytes, _deny
        from .intra_run_proof_contract import FactualFeedbackRequest, FeedbackConsumptionRequest
        budget = runtime._ka01_budget
        if (budget is None or not budget.g3 or type(self.g3_native_source) is not G3NativeSource
                or self.g3_native_source != budget.request.native or self._g3_process is not None
                or not self._execution_policy.authorizes_mutation):
            if budget is not None:
                budget.stop()
            _deny("SUPPORTED_G3_NATIVE_TRANSPORT_REQUIRED")
        budget.guard(live=False)
        frame = budget.frame
        if (boundary is not budget.boundary or frame is None or boundary._live is not frame
                or not frame.executing or frame.stopped or frame.adapter is not self
                or frame.thread != threading.get_ident() or frame.owner is not runtime
                or lease is not budget.lease or operation != budget.operation
                or operation == "PRIMARY" and execution is not None or frame.execution != execution):
            budget.stop()
            _deny("ORIGINAL_G3_INVOCATION_REQUIRED")
        _g3_native_source(runtime, self.g3_native_source)
        self._g3_owner = runtime
        # No injectable affirmative runner or session reconstruction exists here.
        process = None
        creation_started = time.monotonic_ns()
        try:
            process = subprocess.Popen((sys.executable, "-I", "-u", str(runtime.repo / self.g3_native_source.path)),
                cwd=runtime.repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self._g3_process = process
            budget.bind_native(process)
        except BaseException:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            budget.native_cleanup(creation_started)
            self._g3_owner = None
            raise
        messages = queue.Queue(maxsize=1)
        errors = queue.Queue(maxsize=1)
        session, sequence = uuid4().hex, 0
        stderr_bytes = [0]
        stop_reading = threading.Event()

        def publish(channel, value):
            while not stop_reading.is_set():
                try:
                    channel.put(value, timeout=0.05)
                    return
                except queue.Full:
                    pass

        def stdout_reader():
            try:
                while not stop_reading.is_set():
                    line = process.stdout.readline(2 * 1024 * 1024 + 2)
                    if not line or len(line) > 2 * 1024 * 1024 + 1 or not line.endswith(b"\n"):
                        publish(messages, None)
                        return
                    publish(messages, line[:-1])
            except (OSError, ValueError):
                publish(messages, None)

        def stderr_reader():
            try:
                while not stop_reading.is_set():
                    data = process.stderr.read(4096)
                    if not data:
                        return
                    stderr_bytes[0] += len(data)
                    if stderr_bytes[0] > 65536:
                        publish(errors, "NATIVE_STDERR_BOUND")
                        return
            except (OSError, ValueError):
                publish(errors, "NATIVE_STDERR_UNKNOWN")

        readers = [threading.Thread(target=target, daemon=True) for target in (stdout_reader, stderr_reader)]
        for reader in readers:
            reader.start()

        def send(kind, **fields):
            if kind != "TERMINAL":
                budget.guard(native_work=True)
            content = _bytes(dict(kind=kind, session=session, **fields)) + b"\n"
            if len(content) > 2 * 1024 * 1024 + 1 or process.poll() is not None:
                _deny("G3_NATIVE_CHANNEL_LOST")
            failures = []
            def write():
                try:
                    process.stdin.write(content)
                    process.stdin.flush()
                except (OSError, ValueError) as exc:
                    failures.append(exc)
            writer = threading.Thread(target=write, daemon=True)
            writer.start()
            while writer.is_alive():
                writer.join(timeout=min(0.05, budget.remaining_seconds()))
                if kind != "TERMINAL":
                    budget.guard(native_work=True)
            if failures:
                _deny("G3_NATIVE_CHANNEL_LOST")

        def receive(kind=None):
            nonlocal sequence
            from .verification_contract import _reuse_decode
            while True:
                budget.guard(native_work=True)
                if not errors.empty():
                    _deny(errors.get_nowait())
                try:
                    raw = messages.get(timeout=min(0.05, budget.remaining_seconds()))
                    break
                except queue.Empty:
                    pass
            if raw is None:
                _deny("G3_NATIVE_CHANNEL_LOST")
            data = _reuse_decode(raw)
            if (type(data) is not dict or data.get("session") != session
                    or type(data.get("sequence")) is not int or data["sequence"] != sequence + 1
                    or type(data.get("kind")) is not str or kind is not None and data["kind"] != kind):
                _deny("G3_NATIVE_FRAME_REPLAY_OR_CONFLICT")
            sequence += 1
            return data

        self._g3_exchange = (send, receive)
        last = None
        try:
            native_execution = (asdict(execution) if operation == "REMEDIATION" else
                                {**execution, "run": asdict(run)} if operation == "REPAIR" else None)
            semantic, _ = _g3_semantic(runtime, budget.request)
            send("BEGIN", version=1, operation=operation, run=asdict(run), task=asdict(task),
                execution=native_execution, profile=self.execution_profile.as_dict(),
                semantic_task=asdict(semantic.task), native_source_sha256=self.g3_native_source.sha256)
            ready = receive("READY")
            if ready != dict(kind="READY", session=session, sequence=1, run_id=run.run_id,
                             profile=self.execution_profile.as_dict()):
                _deny("G3_NATIVE_PROFILE_OR_RUN_MISMATCH")
            send("READY_ACCEPTED")
            for _ in range(2 * budget.limits["max_checkpoints"] + 2):
                data = receive()
                if data["kind"] == "CHECKPOINT":
                    from .verification import _original_git
                    if (set(data) != {"kind", "session", "sequence", "head_sha"}
                            or data["head_sha"] != _original_git(runtime.repo, "rev-parse", "HEAD").decode("ascii").strip()):
                        _deny("G3_NATIVE_CANDIDATE_SUBSTITUTION")
                    request = runtime.g3_checkpoint_request()
                    record = runtime.seal_checkpoint(request)
                    last = request, record
                    source = FactualFeedbackRequest(1, request, record.digest)
                    feedback = runtime.prepare_feedback(source)
                    if feedback.data["status"] == "PREPARED":
                        consumed = runtime.consume_same_executor_feedback(FeedbackConsumptionRequest(1, source, feedback.digest),
                            boundary=boundary, lease=lease, adapter=self)
                        if consumed.data["status"] != "LOCAL_ACKNOWLEDGED":
                            _deny("G3_NATIVE_FEEDBACK_NOT_CONSUMED")
                        send("CONSUMED", receipt_id=consumed.data["receipt_id"], feedback_digest=feedback.digest)
                    elif feedback.data["status"] == "NO_FAILED_PROOF":
                        send("CHECKPOINT_SEALED", checkpoint_digest=record.digest, candidate_sha=request.scheduler.subject_sha,
                             ordinal=request.ordinal)
                    else:
                        _deny("G3_NATIVE_PROOF_UNRESOLVED")
                elif data["kind"] == "RESULT":
                    if set(data) != {"kind", "session", "sequence", "package"} or last is None:
                        _deny("G3_NATIVE_RESULT_WITHOUT_CHECKPOINT")
                    package = self._normalize(_bytes(data["package"]).decode("ascii"), stderr="")
                    runtime.complete(package, budget.policy, checkpoint=last[0], checkpoint_digest=last[1].digest)
                    send("TERMINAL", head_sha=package.result.head_sha)
                    # The original process must terminate once; no next process
                    # or native work can be minted by this acknowledgment.
                    process.wait(timeout=min(5, budget.remaining_seconds()))
                    if process.returncode != 0 or not errors.empty():
                        _deny("G3_NATIVE_TERMINATION_UNKNOWN")
                    for reader in readers:
                        reader.join(timeout=1)
                    if not messages.empty() and messages.get_nowait() is not None:
                        _deny("G3_NATIVE_DOUBLE_TERMINAL")
                    return package
                else:
                    _deny("G3_NATIVE_UNSUPPORTED_FRAME")
            _deny("G3_NATIVE_MESSAGE_BOUND")
        except BaseException:
            budget.stop()
            raise
        finally:
            cleanup_started = time.monotonic_ns()
            self._g3_exchange = None
            self._g3_owner = None
            stop_reading.set()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            for reader in readers:
                reader.join(timeout=1)
            budget.native_cleanup(cleanup_started)

    def confirm_local_feedback(self, *, feedback, receipt_id):
        """Factual-only duplex receipt from the original still-live peer."""
        from .intra_run_proof_contract import LocalFeedbackConfirmation
        from .ka01_admission import _deny
        owner = self._g3_owner
        budget = None if owner is None else owner._ka01_budget
        if (self._g3_exchange is None or self._g3_feedback_pending or budget is None
                or budget.frame is None or not budget.frame.consuming or budget.frame.adapter is not self):
            if budget is not None:
                budget.stop()
            _deny("G3_NATIVE_CALLBACK_OUTSIDE_INVOCATION")
        budget.guard()
        send, receive = self._g3_exchange
        self._g3_feedback_pending = True
        try:
            send("FEEDBACK", receipt_id=receipt_id, feedback=feedback.data)
            reply = receive("ACK")
            if (set(reply) != {"kind", "session", "sequence", "receipt_id", "feedback_digest"}
                    or reply["receipt_id"] != receipt_id or reply["feedback_digest"] != feedback.digest):
                _deny("G3_NATIVE_FEEDBACK_ACK_CONFLICT")
            return LocalFeedbackConfirmation(receipt_id, feedback.digest)
        finally:
            self._g3_feedback_pending = False

    @property
    def execution_profile(self) -> ResolvedExecutionProfile | None:
        """Return the bound execution profile, if any."""
        return self._execution_profile

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        """Execute an unchanged TASK/RUN pair through native Codex CLI."""

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

    def execute_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage:
        """Execute one narrow remediation through one native Codex process."""

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

    def execute_repair(self, *, execution: Mapping[str, Any]) -> ResultPackage:
        """Execute one bound pre-PASS continuation through one Codex process."""

        run = execution.get("run")
        if not isinstance(run, Run):
            raise CodexExecutionError("REPAIR execution has no bound RUN", exit_code=None)
        repair = execution.get("repair")
        action = repair.get("action") if isinstance(repair, Mapping) else None
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
