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
    validate_result,
)
from .run import Run
from .task import Task


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]

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
                input=prompt,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise CodexExecutionError(
                f"Codex CLI invocation failed: {exc}",
                exit_code=None,
            ) from exc

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            message = f"Codex CLI exited with code {completed.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise CodexExecutionError(
                message,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )

        return self._normalize(completed.stdout, stderr=completed.stderr)

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
        """Serialize only the canonical TASK and RUN as variable input."""

        canonical_input = json.dumps(
            {"task": asdict(task), "run": asdict(run)},
            sort_keys=True,
        )
        return (
            "Execute the canonical TASK within its bound RUN. "
            "Do not reinterpret its requirements. Return only one JSON object "
            "with keys 'result' and 'evidence' matching the canonical contracts.\n"
            f"CANONICAL_INPUT:\n{canonical_input}"
        )

    @staticmethod
    def _normalize(stdout: str, *, stderr: str) -> ResultPackage:
        try:
            payload = json.loads(stdout)
            root = _mapping(payload, "Codex output")
            result = validate_result(root["result"])
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
