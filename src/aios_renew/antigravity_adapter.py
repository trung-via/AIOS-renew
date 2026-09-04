"""Minimal adapter boundary for native Antigravity execution."""

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
    validate_result,
    validate_structural_result,
)
from .run import Run
from .review import RemediationExecution
from .task import Task


NativeTransport = Callable[..., Any]
ProcessRunner = Callable[..., subprocess.CompletedProcess[bytes]]
RESULT_PACKAGE_SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "result_package.json"
).resolve()


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


ANTIGRAVITY_DEFAULT_MODEL = "gemini-3.8-flash"
ANTIGRAVITY_DEFAULT_EFFORT = "high"


class AntigravityExecutionError(RuntimeError):
    """Raised when the native Antigravity transport fails."""

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


class AntigravityOutputError(AntigravityExecutionError):
    """Raised when native output is not a canonical result package."""


class AntigravityAdapter:
    """Own native Antigravity handoff, invocation, and output mechanics."""

    executor = "antigravity"

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
            transport is None if structural_output is None else structural_output
        )

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        """Execute the unchanged TASK/RUN pair through the native transport."""

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
                )
        except AntigravityExecutionError:
            raise
        except Exception as exc:
            raise AntigravityExecutionError(
                f"Antigravity native invocation failed: {exc}"
            ) from exc

        return self._normalize(output, structural=self._structural_output)

    def execute_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage:
        """Hand off the same narrow contract used by Codex."""

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
                )
        except AntigravityExecutionError:
            raise
        except Exception as exc:
            raise AntigravityExecutionError(
                f"Antigravity native invocation failed: {exc}"
            ) from exc
        return self._normalize(output, structural=self._structural_output)

    def execute_repair(self, *, execution: Mapping[str, Any]) -> ResultPackage:
        """Hand off the same bound continuation contract used by Codex."""

        try:
            if self._transport is not None:
                output = self._transport(execution=execution)
            else:
                run = execution.get("run")
                if not isinstance(run, Run):
                    raise AntigravityExecutionError(
                        "REPAIR execution has no bound RUN"
                    )
                handoff = dict(execution)
                handoff["run"] = asdict(run)
                handoff["execution_context"] = _native_execution_context(
                    run=run, operation="REPAIR"
                )
                output = self._invoke_native(
                    operation="REPAIR", handoff=handoff
                )
        except AntigravityExecutionError:
            raise
        except Exception as exc:
            raise AntigravityExecutionError(
                f"Antigravity native invocation failed: {exc}"
            ) from exc
        return self._normalize(output, structural=self._structural_output)

    def _invoke_native(
        self, *, operation: str, handoff: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self._repo is None or self._handoff_path is None:
            raise AntigravityExecutionError(
                "Antigravity native execution configuration is incomplete"
            )
        _write_json(self._handoff_path, handoff)
        instruction = _native_instruction(
            operation=operation, handoff_path=self._handoff_path
        )
        command = self.command_for(repo=self._repo, instruction=instruction)
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
            raise AntigravityExecutionError(
                "Antigravity CLI not found: agy"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AntigravityExecutionError(
                "Antigravity CLI exceeded the "
                f"{self._execution_policy.response_budget_minutes}-minute "
                "native response deadline",
                stdout=exc.stdout,
                stderr=exc.stderr,
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise AntigravityExecutionError(
                f"Antigravity CLI invocation failed: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = stderr.strip() or stdout.strip()
            message = f"Antigravity CLI returned nonzero ({completed.returncode})"
            if detail:
                message = f"{message}: {detail}"
            raise AntigravityExecutionError(
                message, stdout=stdout, stderr=stderr
            )
        return _structured_output(stdout, stderr=stderr)

    def command_for(
        self, *, repo: Path, instruction: str
    ) -> tuple[str, ...]:
        """Build the native AGY command from provider-neutral authorization."""

        command = [
            "agy",
            "--print",
            instruction,
            "--add-dir",
            str(repo),
            "--model",
            ANTIGRAVITY_DEFAULT_MODEL,
            "--effort",
            ANTIGRAVITY_DEFAULT_EFFORT,
            "--mode",
            (
                "accept-edits"
                if self._execution_policy.authorizes_mutation
                else "plan"
            ),
            "--disable-slash-commands",
            "--output-format",
            "json",
            "--json-schema",
            str(RESULT_PACKAGE_SCHEMA_PATH),
            "--print-timeout",
            f"{self._execution_policy.response_budget_minutes}m",
        ]
        if self._execution_policy.authorizes_mutation:
            command.append("--dangerously-skip-permissions")
        return tuple(command)

    @staticmethod
    def _normalize(
        output: Any, *, structural: bool = False
    ) -> ResultPackage:
        try:
            payload = (
                json.loads(output.removeprefix("\ufeff"))
                if isinstance(output, str)
                else output
            )
            root = _mapping(payload, "Antigravity output")
            validate = validate_structural_result if structural else validate_result
            result = validate(_normalize_satisfies(root["result"]))
            evidence_data = root["evidence"]
            if not isinstance(evidence_data, list):
                raise TypeError("evidence must be a list")
            evidence = tuple(validate_evidence(item) for item in evidence_data)
        except (
            ArtifactValidationError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as exc:
            output_kind = "structural" if structural else "canonical"
            raise AntigravityOutputError(
                f"Antigravity returned invalid {output_kind} output: {exc}"
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
        "bound continuation. Do not restart PRIMARY discovery, synchronize, retry, review, "
        "or widen the original TASK. Change only repair.modification_scope. For CODE_FIX "
        "commit the final permitted state; for NO_CHANGE do not mutate it. Do not push. "
        "Runtime owns complete original TASK verification; do not execute canonical "
        "verification commands or construct EVIDENCE. Runtime derives and persists "
        "canonical result.changed_files from the original TASK root base to final HEAD; "
        "do not reconstruct or enumerate that historical file set. Return one structural "
        "ResultPackage for the complete original TASK contract as the only response, with "
        "empty root evidence and every claim.evidence empty. Structural "
        "result.changed_files may contain only the narrow repair delta or be empty. Runtime "
        "captures and persists the response; do not write Runtime-owned operational state."
    )


def _structured_output(stdout: str, *, stderr: str) -> Mapping[str, Any]:
    """Extract one schema-constrained payload from the native AGY envelope."""

    if not stdout.strip():
        detail = stderr.strip()
        raise AntigravityExecutionError(
            "Antigravity ResultPackage missing" + (f": {detail}" if detail else "")
        )
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = stderr.strip()
        message = f"Antigravity CLI returned malformed terminal JSON: {exc}"
        if detail:
            message = f"{message}: {detail}"
        raise AntigravityExecutionError(message) from exc
    if not isinstance(envelope, Mapping):
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "response envelope must be a mapping"
        )

    status = envelope.get("status")
    if not isinstance(status, str) or not status:
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "status must be a non-empty string"
        )
    response = envelope.get("response")
    if response is not None and not isinstance(response, str):
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "response must be a string"
        )
    error = envelope.get("error")
    if error is not None and not isinstance(error, str):
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "error must be a string"
        )
    if status != "SUCCESS":
        detail = (error or "").strip() or stderr.strip() or (response or "").strip()
        message = f"Antigravity CLI terminal status is {status}"
        if detail:
            message = f"{message}: {detail}"
        raise AntigravityExecutionError(message)

    if "structured_output" not in envelope:
        detail = stderr.strip() or (error or "").strip() or (response or "").strip()
        message = "Antigravity ResultPackage missing"
        if detail:
            message = f"{message}: {detail}"
        raise AntigravityExecutionError(message)
    payload = envelope["structured_output"]
    if not isinstance(payload, Mapping):
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "structured_output must be a mapping"
        )
    return payload


def _decode_utf8(value: bytes | str) -> str:
    return value.decode("utf-8", errors="strict") if isinstance(value, bytes) else value


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
    """Wrap singleton string ``satisfies`` values without interpreting them."""

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
