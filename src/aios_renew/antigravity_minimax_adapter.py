"""Minimal adapter boundary for native Antigravity+MiniMax (agym) execution."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from . import correction_dispatch, dispatch_reconciliation, run
from .artifacts import (
    ArtifactValidationError,
    ResultPackage,
    validate_evidence,
    validate_result,
    validate_structural_result,
)
from .review import RemediationExecution
from .run import Run
from .task import Task

# Prospective extension: register the new admitted executor identity across
# product admission boundaries while leaving frozen Kernel v0.1 files byte-unchanged.
if "antigravity-minimax" not in run.SUPPORTED_EXECUTORS:
    run.SUPPORTED_EXECUTORS = run.SUPPORTED_EXECUTORS | {"antigravity-minimax"}
if "antigravity-minimax" not in dispatch_reconciliation.SUPPORTED_EXECUTORS:
    dispatch_reconciliation.SUPPORTED_EXECUTORS = (
        dispatch_reconciliation.SUPPORTED_EXECUTORS | {"antigravity-minimax"}
    )
if "antigravity-minimax" not in correction_dispatch.SUPPORTED_EXECUTORS:
    correction_dispatch.SUPPORTED_EXECUTORS = (
        correction_dispatch.SUPPORTED_EXECUTORS | {"antigravity-minimax"}
    )

NativeTransport = Callable[..., Any]
ProcessRunner = Callable[..., subprocess.CompletedProcess[bytes]]
RESULT_PACKAGE_SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "result_package.json"
).resolve()
REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "remediation_result_package.json"
).resolve()
REPAIR_RESULT_PACKAGE_SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "repair_result_package.json"
).resolve()

ANTIGRAVITY_MINIMAX_DEFAULT_MODEL = "MiniMax-M3"


class ExecutionPolicy(Protocol):
    """Provider-neutral admitted policy consumed by native mechanics."""

    authorizes_mutation: bool
    response_budget_minutes: int
    process_watchdog_seconds: int


@dataclass(frozen=True)
class _DefaultExecutionPolicy:
    authorizes_mutation: bool = True
    response_budget_minutes: int = 60
    process_watchdog_seconds: int = 65 * 60


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


class AntigravityMinimaxExecutionError(RuntimeError):
    """Raised when the native Antigravity+MiniMax transport fails."""

    def __init__(
        self,
        message: str,
        *,
        stdout: bytes | str | None = None,
        stderr: bytes | str | None = None,
    ) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class AntigravityMinimaxOutputError(AntigravityMinimaxExecutionError):
    """Raised when native output is not a canonical result package."""


class AntigravityMinimaxAdapter:
    """Own native Antigravity+MiniMax (agym) handoff, invocation, and output mechanics."""

    executor = "antigravity-minimax"

    def __init__(
        self,
        *,
        transport: NativeTransport | None = None,
        runner: ProcessRunner = subprocess.run,
        execution_policy: ExecutionPolicy | None = None,
        repo: str | Path | None = None,
        handoff_path: str | Path | None = None,
        structural_output: bool | None = None,
    ) -> None:
        self._transport = transport
        self._runner = runner
        self._execution_policy = execution_policy or _DefaultExecutionPolicy()
        self._repo = Path(repo).resolve() if repo is not None else None
        self._handoff_path = (
            Path(handoff_path) if handoff_path is not None else None
        )
        self._structural_output = (
            True if structural_output is None else structural_output
        )

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        """Execute the unchanged TASK/RUN pair through native agym transport."""
        try:
            if self._transport is not None:
                output = self._transport(task=task, run=run)
            else:
                task_data = asdict(task)
                task_data.pop("verification")
                output = self._invoke_native(
                    operation="PRIMARY",
                    handoff={
                        "execution_context": _native_execution_context(
                            run=run, operation="PRIMARY"
                        ),
                        "task": task_data,
                        "run": asdict(run),
                    },
                    expected_head=run.base_sha,
                )
        except AntigravityMinimaxExecutionError:
            raise
        except Exception as exc:
            raise AntigravityMinimaxExecutionError(
                f"Antigravity-MiniMax native invocation failed: {exc}"
            ) from exc

        return self._normalize_output(
            output,
            expected_head=run.base_sha,
            structural=self._structural_output,
        )

    def execute_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage:
        """Hand off one narrow remediation through native agym transport."""
        try:
            if self._transport is not None:
                output = self._transport(execution=execution)
            else:
                execution_data = asdict(execution)
                execution_data["remediation"].pop("affected_verification")
                output = self._invoke_native(
                    operation="REMEDIATION",
                    handoff={
                        "execution_context": _native_execution_context(
                            run=execution.run, operation="REMEDIATION"
                        ),
                        "remediation_execution": execution_data,
                    },
                    expected_head=execution.run.base_sha,
                )
        except AntigravityMinimaxExecutionError:
            raise
        except Exception as exc:
            raise AntigravityMinimaxExecutionError(
                f"Antigravity-MiniMax native invocation failed: {exc}"
            ) from exc

        return self._normalize_output(
            output,
            expected_head=execution.run.base_sha,
            structural=self._structural_output,
        )

    def execute_repair(self, *, execution: Mapping[str, Any]) -> ResultPackage:
        """Hand off one bound continuation contract through native agym transport."""
        try:
            if self._transport is not None:
                output = self._transport(execution=execution)
                expected_head = None
                run_obj = execution.get("run")
                if isinstance(run_obj, Run):
                    expected_head = run_obj.base_sha
            else:
                run_obj = execution.get("run")
                if not isinstance(run_obj, Run):
                    raise AntigravityMinimaxExecutionError(
                        "REPAIR execution has no bound RUN"
                    )
                handoff = dict(execution)
                handoff["run"] = asdict(run_obj)
                handoff["execution_context"] = _native_execution_context(
                    run=run_obj, operation="REPAIR"
                )
                output = self._invoke_native(
                    operation="REPAIR",
                    handoff=handoff,
                    expected_head=run_obj.base_sha,
                )
                expected_head = run_obj.base_sha
        except AntigravityMinimaxExecutionError:
            raise
        except Exception as exc:
            raise AntigravityMinimaxExecutionError(
                f"Antigravity-MiniMax native invocation failed: {exc}"
            ) from exc

        return self._normalize_output(
            output,
            expected_head=expected_head,
            structural=self._structural_output,
        )

    def _invoke_native(
        self,
        *,
        operation: str,
        handoff: Mapping[str, Any],
        expected_head: str | None = None,
    ) -> str:
        if self._repo is None or self._handoff_path is None:
            raise AntigravityMinimaxExecutionError(
                "Antigravity MiniMax native execution configuration is incomplete"
            )
        _write_json(self._handoff_path, handoff)
        instruction = _native_instruction(
            operation=operation, handoff_path=self._handoff_path
        )
        command = self.command_for(
            repo=self._repo,
            instruction=instruction,
            operation=operation,
            expected_head=expected_head,
        )
        try:
            completed = self._runner(
                command,
                cwd=str(self._repo),
                capture_output=True,
                text=False,
                check=False,
                timeout=self._execution_policy.process_watchdog_seconds,
            )
            stdout = _decode_utf8(completed.stdout)
            stderr = _decode_utf8(completed.stderr)
        except FileNotFoundError as exc:
            raise AntigravityMinimaxExecutionError(
                "Antigravity MiniMax CLI not found: agym"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AntigravityMinimaxExecutionError(
                "Antigravity MiniMax CLI exceeded the "
                f"{self._execution_policy.response_budget_minutes}-minute "
                "native response deadline",
                stdout=exc.stdout,
                stderr=exc.stderr,
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise AntigravityMinimaxExecutionError(
                f"Antigravity MiniMax CLI invocation failed: {exc}"
            ) from exc

        if completed.returncode != 0:
            detail = stderr.strip() or stdout.strip()
            message = (
                f"agym returned nonzero ({completed.returncode})"
            )
            if detail:
                message = f"{message}: {detail}"
            raise AntigravityMinimaxExecutionError(
                message, stdout=stdout, stderr=stderr
            )
        return stdout

    def command_for(
        self,
        *,
        repo: Path,
        instruction: str,
        operation: str = "PRIMARY",
        expected_head: str | None = None,
    ) -> tuple[str, ...]:
        """Build the native agym command from provider-neutral authorization."""
        schema_path = {
            "PRIMARY": RESULT_PACKAGE_SCHEMA_PATH,
            "REMEDIATION": REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH,
            "REPAIR": REPAIR_RESULT_PACKAGE_SCHEMA_PATH,
        }.get(operation, RESULT_PACKAGE_SCHEMA_PATH)

        command = [
            "agym",
            "--prompt",
            instruction,
            "--output-format",
            "json",
            "--workspace",
            str(repo),
            "--aios-mode",
            "--operation",
            operation,
            "--response-schema",
            str(schema_path),
            "--model",
            ANTIGRAVITY_MINIMAX_DEFAULT_MODEL,
            "--response-timeout",
            str(self._execution_policy.response_budget_minutes * 60),
            "--timeout",
            str(self._execution_policy.process_watchdog_seconds),
        ]
        if expected_head is not None:
            command.extend(["--expected-head", expected_head])
        if self._execution_policy.authorizes_mutation:
            command.append("--allow-commit")
        return tuple(command)

    def _normalize_output(
        self,
        output: Any,
        *,
        expected_head: str | None = None,
        structural: bool = False,
    ) -> ResultPackage:
        if isinstance(output, ResultPackage):
            return output

        raw_text = ""
        if isinstance(output, (bytes, str)):
            raw_text = _decode_utf8(output)
            if not raw_text.strip():
                raise AntigravityMinimaxExecutionError("agym output missing")
            try:
                data = json.loads(raw_text.removeprefix("\ufeff"))
            except json.JSONDecodeError as exc:
                raise AntigravityMinimaxExecutionError(
                    f"agym returned malformed terminal JSON: {exc}",
                    stdout=raw_text,
                ) from exc
        elif isinstance(output, Mapping):
            data = output
            raw_text = json.dumps(output)
        else:
            raise AntigravityMinimaxExecutionError(
                "agym output must be a JSON string or mapping"
            )

        if not isinstance(data, Mapping):
            raise AntigravityMinimaxExecutionError(
                "agym returned malformed terminal metadata: response envelope must be a mapping",
                stdout=raw_text,
            )

        if "schema_version" in data:
            return self._parse_envelope(
                data,
                raw_text=raw_text,
                expected_head=expected_head,
                structural=structural,
            )
        if "result" in data and "evidence" in data:
            return self._normalize_package(data, structural=structural)

        raise AntigravityMinimaxExecutionError(
            "agym output is neither an agym.result.v1 envelope nor a ResultPackage",
            stdout=raw_text,
        )

    def _parse_envelope(
        self,
        envelope: Mapping[str, Any],
        *,
        raw_text: str = "",
        expected_head: str | None = None,
        structural: bool = False,
    ) -> ResultPackage:
        schema_version = envelope.get("schema_version")
        if schema_version != "agym.result.v1":
            raise AntigravityMinimaxExecutionError(
                f"agym returned unsupported schema version: {schema_version!r}",
                stdout=raw_text,
            )

        status = envelope.get("status")
        exit_code = envelope.get("exit_code")

        if status == "DIRTY_WORKSPACE_REFUSED" or envelope.get("initial_dirty") is True:
            files = envelope.get("initial_dirty_files", [])
            detail = ", ".join(files) if isinstance(files, list) and files else ""
            msg = "agym reported pre-existing dirty workspace"
            if detail:
                msg = f"{msg}: {detail}"
            raise AntigravityMinimaxExecutionError(msg, stdout=raw_text)

        if status != "PASS":
            error = envelope.get("error")
            violations = envelope.get("state_guard_violations", [])
            viol_str = (
                ", ".join(violations)
                if isinstance(violations, list) and violations
                else ""
            )
            detail = (error or "").strip() or viol_str
            if "state_guard" in status.lower() or "state guard" in (detail or "").lower():
                msg = f"agym State Guard violation: status is {status}"
            else:
                msg = f"agym status is {status}"
            if detail:
                msg = f"{msg}: {detail}"
            raise AntigravityMinimaxExecutionError(msg, stdout=raw_text)

        if exit_code != 0:
            raise AntigravityMinimaxExecutionError(
                f"PASS/exit-code disagreement: status is PASS but exit_code is {exit_code}",
                stdout=raw_text,
            )

        state_guard = envelope.get("state_guard")
        if state_guard != "PASS" or envelope.get("state_guard_violations"):
            violations = envelope.get("state_guard_violations", [])
            detail = (
                ", ".join(violations)
                if isinstance(violations, list) and violations
                else ""
            )
            msg = f"agym State Guard violation: state_guard is {state_guard}"
            if detail:
                msg = f"{msg}: {detail}"
            raise AntigravityMinimaxExecutionError(msg, stdout=raw_text)

        if self._repo is not None:
            envelope_workspace = envelope.get("workspace")
            if (
                not envelope_workspace
                or Path(envelope_workspace).resolve() != self._repo.resolve()
            ):
                raise AntigravityMinimaxExecutionError(
                    f"workspace mismatch: admitted repo {self._repo} != "
                    f"agym workspace {envelope_workspace}",
                    stdout=raw_text,
                )

        if expected_head is not None:
            head_before = envelope.get("head_before")
            if head_before is not None and head_before != expected_head:
                raise AntigravityMinimaxExecutionError(
                    f"starting HEAD mismatch: expected {expected_head}, "
                    f"got {head_before}",
                    stdout=raw_text,
                )

        response_raw = envelope.get("response")
        if not isinstance(response_raw, str) or not response_raw.strip():
            raise AntigravityMinimaxExecutionError(
                "agym response is missing or empty",
                stdout=raw_text,
            )

        stripped_resp = response_raw.strip().removeprefix("\ufeff")
        if stripped_resp.startswith("```"):
            raise AntigravityMinimaxExecutionError(
                "agym response must not be markdown-wrapped",
                stdout=raw_text,
            )

        try:
            decoder = json.JSONDecoder()
            payload, end = decoder.raw_decode(stripped_resp)
            remainder = stripped_resp[end:].strip()
            if remainder:
                raise AntigravityMinimaxExecutionError(
                    "agym response contains prose or additional data after JSON",
                    stdout=raw_text,
                )
        except json.JSONDecodeError as exc:
            raise AntigravityMinimaxExecutionError(
                f"agym response contains malformed JSON: {exc}",
                stdout=raw_text,
            ) from exc

        if not isinstance(payload, Mapping):
            raise AntigravityMinimaxExecutionError(
                "agym response JSON must be a mapping",
                stdout=raw_text,
            )

        package = self._normalize_package(
            payload, structural=structural, stdout=raw_text
        )

        head_after = envelope.get("head_after")
        if head_after is not None and head_after != package.result.head_sha:
            raise AntigravityMinimaxExecutionError(
                f"final HEAD mismatch: envelope head_after {head_after} != "
                f"result.head_sha {package.result.head_sha}",
                stdout=raw_text,
            )

        return package

    @staticmethod
    def _normalize_package(
        payload: Mapping[str, Any],
        *,
        structural: bool = False,
        stdout: str | bytes | None = None,
    ) -> ResultPackage:
        try:
            root = _mapping(payload, "Antigravity-MiniMax output")
            validate = validate_structural_result if structural else validate_result
            result = validate(_normalize_satisfies(root["result"]))
            evidence_data = root["evidence"]
            if not isinstance(evidence_data, list):
                raise TypeError("evidence must be a list")
            evidence = tuple(validate_evidence(item) for item in evidence_data)
        except (
            ArtifactValidationError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            output_kind = "structural" if structural else "canonical"
            raise AntigravityMinimaxOutputError(
                f"Antigravity-MiniMax returned invalid {output_kind} output: {exc}",
                stdout=stdout,
            ) from exc

        return ResultPackage(result=result, evidence=evidence)


def _native_execution_context(*, run: Run, operation: str) -> dict[str, Any]:
    return {
        "role": "NATIVE_EXECUTOR",
        "selected_executor": run.executor,
        "operation": operation,
        "already_admitted": True,
        "direct_implementation": True,
        "operator_dispatch_authority": False,
        "runtime_verification_authority": False,
    }


def _native_instruction(*, operation: str, handoff_path: Path) -> str:
    if operation == "PRIMARY":
        return (
            _NATIVE_EXECUTOR_INSTRUCTION
            + f"Read the AIOS handoff JSON at {handoff_path}. "
            "Execute its TASK implementation context and RUN exactly within the supplied "
            "repository. Runtime owns canonical verification; do not execute canonical "
            "verification commands and do not generate verification evidence. Minimum "
            "implementation-local sanity checks on the changed surface are permitted when "
            "useful, but they are not canonical verification or EVIDENCE. Commit the final "
            "implementation state when required; do not push. Obtain final Git HEAD, and "
            "return the structural ResultPackage as the only response. Runtime captures and "
            "persists this response; do not write Runtime-owned operational state. The "
            "ResultPackage must be an object with result and evidence. result must contain "
            "head_sha, claims, changed_files, and unresolved. Each claim must contain id, "
            "satisfies, claim, and evidence. Each evidence entry must contain evidence_id, "
            "run_id, subject_sha, type, source.command, result.exit_code, result.summary, "
            "and raw.path when present. Root evidence and every claim.evidence must be empty; "
            "Runtime constructs canonical EVIDENCE. Every claim.satisfies entry must be a "
            "known TASK acceptance ID."
        )
    if operation == "REMEDIATION":
        return (
            _NATIVE_EXECUTOR_INSTRUCTION
            + f"Read the AIOS remediation handoff JSON at {handoff_path}. Execute exactly "
            "its one remediation_execution contract. Do not run or restart the original "
            "TASK, scan for a different repository, perform semantic review or repeat "
            "unaffected verification. Change only paths in remediation.modification_scope. "
            "For CODE_FIX, commit the permitted remediation delta before returning; for "
            "EVIDENCE_ONLY, do not create a code commit. Do not push. Runtime owns affected "
            "verification; do not execute verification commands and do not generate "
            "verification evidence. Minimum implementation-local sanity checks on the "
            "changed surface are permitted when useful, but they are not canonical "
            "verification or EVIDENCE. Return one structural ResultPackage as the only "
            "response with empty root evidence, result.claims, and result.unresolved. Bind "
            "result.head_sha to final Git HEAD. Runtime captures and persists the response; "
            "do not write Runtime-owned operational state."
        )
    return (
        _NATIVE_EXECUTOR_INSTRUCTION
        + f"Read the AIOS REPAIR handoff JSON at {handoff_path}. Execute exactly its single "
        "continuation bound to the supplied exact failed RUN and failed-lineage context. "
        "The three REPAIR actions are distinct. CODE_FIX authorizes mutation only to "
        "correct an established defect and requires committing the final permitted state. "
        "NO_CHANGE authorizes no repository mutation and does not permit resuming unfinished "
        "original TASK implementation. CONTINUE_IMPLEMENTATION authorizes mutation to "
        "resume the unfinished original TASK implementation from that exact failed lineage "
        "and perform the remaining authorized TASK work needed to produce the permitted "
        "implementation delta. For CONTINUE_IMPLEMENTATION, unfinished original TASK "
        "work is not prohibited merely because equivalent work would normally occur "
        "during PRIMARY; necessary bounded repository inspection, discovery, or live "
        "capture required by the original TASK is permitted as part of that unfinished "
        "implementation. This permission does not authorize repository-wide rediscovery, "
        "new intent, or work outside the TASK and REPAIR scope. Follow repair.instructions "
        "and limit every mutation to repair.modification_scope. Successful "
        "CONTINUE_IMPLEMENTATION work must commit the final permitted repository state and "
        "bind result.head_sha to final committed Git HEAD. Do not create or restart a fresh "
        "PRIMARY lineage, allocate a new TASK or RUN, synchronize to a different candidate, "
        "retry this admitted continuation, reroute or fall back to another Executor, widen "
        "scope, recursively continue, perform semantic review, or invoke an AIOS operator "
        "or worker launcher. Do not push. "
        "Runtime owns complete original TASK verification; do not execute canonical "
        "verification commands or construct EVIDENCE. Minimum implementation-local sanity "
        "checks on the changed surface remain permitted when useful, but they are not "
        "canonical verification or EVIDENCE. Runtime derives and persists "
        "canonical result.changed_files from the original TASK root base to final HEAD; "
        "do not reconstruct or enumerate that historical file set. Return one structural "
        "ResultPackage for the complete original TASK contract as the only response, with "
        "empty root evidence and every claim.evidence empty. Structural "
        "result.changed_files may contain only the narrow repair delta or be empty. Runtime "
        "captures and persists the response; do not write Runtime-owned operational state."
    )


def _decode_utf8(value: bytes | str) -> str:
    return (
        value.decode("utf-8", errors="strict")
        if isinstance(value, bytes)
        else value
    )


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping")
    return value


def _normalize_satisfies(result: Any) -> Any:
    """Wrap singleton string satisfies values without interpreting them."""
    if not isinstance(result, Mapping):
        return result
    claims = result.get("claims")
    if not isinstance(claims, list):
        return result

    normalized_claims: list[Any] = []
    changed = False
    for claim in claims:
        if isinstance(claim, Mapping) and isinstance(claim.get("satisfies"), str):
            claim = dict(claim)
            claim["satisfies"] = [claim["satisfies"]]
            changed = True
        normalized_claims.append(claim)

    if not changed:
        return result
    normalized_result = dict(result)
    normalized_result["claims"] = normalized_claims
    return normalized_result
