"""Minimal native Codex CLI adapter."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactValidationError,
    ResultPackage,
    validate_evidence,
    validate_structural_result,
)
from .run import Run
from .review import RemediationExecution
from .task import Task


ProcessRunner = Callable[..., subprocess.CompletedProcess[bytes]]

RESULT_PACKAGE_SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "result_package.json"
).resolve()


class CodexExecutionError(RuntimeError):
    """Raised when the native Codex process cannot complete successfully."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None,
        stdout: str = "",
        stderr: str = "",
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
    ) -> None:
        self._runner = runner
        self._schema_path = Path(schema_path)

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        """Execute an unchanged TASK/RUN pair through native Codex CLI."""

        command = self.command_for(run, schema_path=self._schema_path)
        prompt = self.prompt_for(task=task, run=run)
        try:
            completed = self._runner(
                command,
                input=prompt.encode("utf-8", errors="strict"),
                capture_output=True,
                text=False,
                check=False,
            )
            stdout = _decode_utf8(completed.stdout)
            stderr = _decode_utf8(completed.stderr)
        except (OSError, UnicodeError) as exc:
            raise CodexExecutionError(
                f"Codex CLI invocation failed: {exc}",
                exit_code=None,
            ) from exc

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

        command = self.command_for(execution.run, schema_path=self._schema_path)
        try:
            completed = self._runner(
                command,
                input=self.remediation_prompt_for(execution=execution).encode(
                    "utf-8", errors="strict"
                ),
                capture_output=True,
                text=False,
                check=False,
            )
            stdout = _decode_utf8(completed.stdout)
            stderr = _decode_utf8(completed.stderr)
        except (OSError, UnicodeError) as exc:
            raise CodexExecutionError(
                f"Codex CLI invocation failed: {exc}", exit_code=None
            ) from exc
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
        command = self.command_for(run, schema_path=self._schema_path)
        prompt = self.repair_prompt_for(execution=execution)
        try:
            completed = self._runner(
                command, input=prompt.encode("utf-8", errors="strict"),
                capture_output=True, text=False, check=False,
            )
            stdout = _decode_utf8(completed.stdout)
            stderr = _decode_utf8(completed.stderr)
        except (OSError, UnicodeError) as exc:
            raise CodexExecutionError(
                f"Codex CLI invocation failed: {exc}", exit_code=None
            ) from exc
        if completed.returncode != 0:
            raise CodexExecutionError(
                f"Codex CLI exited with code {completed.returncode}",
                exit_code=completed.returncode, stdout=stdout, stderr=stderr,
            )
        return self._normalize(stdout, stderr=stderr)

    @staticmethod
    def command_for(
        run: Run,
        schema_path: str | Path = RESULT_PACKAGE_SCHEMA_PATH,
    ) -> tuple[str, ...]:
        """Build the native non-interactive Codex command."""

        return (
            "codex",
            "exec",
            "--cd",
            run.workspace,
            "--sandbox",
            "workspace-write",
            "--output-schema",
            str(schema_path),
            "--color",
            "never",
            "-",
        )

    @staticmethod
    def prompt_for(*, task: Task, run: Run) -> str:
        """Serialize executor context without Runtime-owned verification commands."""

        task_input = asdict(task)
        task_input.pop("verification")
        canonical_input = json.dumps(
            {"task": task_input, "run": asdict(run)},
            sort_keys=True,
        )
        return (
            "Execute the canonical TASK within its bound RUN. "
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
            f"CANONICAL_INPUT:\n{canonical_input}"
        )

    @staticmethod
    def remediation_prompt_for(*, execution: RemediationExecution) -> str:
        """Serialize only the shared narrow remediation contract."""

        execution_input = asdict(execution)
        execution_input["remediation"].pop("affected_verification")
        canonical_input = json.dumps(execution_input, sort_keys=True)
        return (
            "Execute exactly one canonical narrow REMEDIATION. Do not run or "
            "restart the original TASK, rediscover the repository, perform "
            "semantic review, or retry. Change only remediation.modification_scope. "
            "For CODE_FIX, commit the permitted remediation delta before returning; "
            "for EVIDENCE_ONLY, do not create a code commit. Do not push. "
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
        canonical_input = json.dumps(data, sort_keys=True)
        return (
            "Execute exactly one canonical REPAIR continuation bound to the failed RUN. "
            "Do not restart PRIMARY discovery, synchronize with upstream, retry, review, "
            "or widen the original TASK. Follow repair.instructions and change only "
            "repair.modification_scope. For CODE_FIX commit the final permitted state; "
            "for NO_CHANGE do not mutate the repository. Do not push. Runtime owns the "
            "complete original TASK verification: do not execute canonical verification "
            "commands or construct EVIDENCE. Return one structural ResultPackage for the "
            "complete original TASK delta with empty root evidence and every claim.evidence "
            "empty.\nREPAIR_INPUT:\n" + canonical_input
        )

    @staticmethod
    def _normalize(stdout: str, *, stderr: str) -> ResultPackage:
        try:
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


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping")
    return value


def _decode_utf8(value: bytes | str) -> str:
    """Decode captured subprocess bytes synchronously and fail closed."""

    return value.decode("utf-8", errors="strict") if isinstance(value, bytes) else value
