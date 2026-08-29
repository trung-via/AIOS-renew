"""Deterministic Runtime-owned execution of canonical verification commands."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from .artifacts import Claim, Evidence, EvidenceOutcome, EvidenceSource, Result


VerificationRunner = Callable[..., subprocess.CompletedProcess[bytes]]
_WINDOWS_POWERSHELL_UTF8_PREAMBLE = (
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "$PSDefaultParameterValues['*:Encoding'] = 'utf8'; "
)


class RuntimeVerificationError(RuntimeError):
    """Raised when deterministic Runtime verification fails closed."""

    def __init__(self, message: str, *, evidence: Iterable[Evidence] = ()) -> None:
        super().__init__(message)
        self.evidence = tuple(evidence)


def execute_verification(
    commands: Iterable[str],
    *,
    run_id: str,
    subject_sha: str,
    repository: Path,
    raw_directory: Path,
    runner: VerificationRunner = subprocess.run,
    platform: str = os.name,
    environment: Mapping[str, str] | None = None,
) -> tuple[Evidence, ...]:
    """Execute each canonical command once, in order, stopping at first failure."""

    repository = repository.resolve()
    raw_directory.mkdir(parents=True, exist_ok=True)
    evidence: list[Evidence] = []
    env = _verification_environment(environment=environment, platform=platform)
    for order, command in enumerate(commands, start=1):
        exact_command = _strict_utf8_command(command)
        evidence_id = f"{run_id}-V{order:03d}"
        raw_path = raw_directory / f"{evidence_id}.raw"
        shell_command = _shell_command(exact_command, platform=platform)
        try:
            completed = runner(
                shell_command,
                cwd=repository,
                env=env,
                capture_output=True,
                text=False,
                check=False,
            )
        except OSError as exc:
            raise RuntimeVerificationError(
                f"verification command could not start: {command}: {exc}",
                evidence=evidence,
            ) from exc

        stdout = _require_bytes(completed.stdout, "stdout")
        stderr = _require_bytes(completed.stderr, "stderr")
        raw_path.write_bytes(b"STDOUT\n" + stdout + b"\nSTDERR\n" + stderr)
        try:
            decoded_stdout = stdout.decode("utf-8", errors="strict")
            decoded_stderr = stderr.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RuntimeVerificationError(
                f"verification output is not strict UTF-8: {command}",
                evidence=evidence,
            ) from exc

        item = Evidence(
            evidence_id=evidence_id,
            run_id=run_id,
            subject_sha=subject_sha,
            type="VERIFICATION",
            source=EvidenceSource(command=command),
            result=EvidenceOutcome(
                exit_code=completed.returncode,
                summary=_summary(
                    completed.returncode,
                    stdout=decoded_stdout,
                    stderr=decoded_stderr,
                ),
            ),
            raw_path=str(raw_path),
        )
        evidence.append(item)
        if completed.returncode != 0:
            raise RuntimeVerificationError(
                f"verification command failed with exit code "
                f"{completed.returncode}: {command}",
                evidence=evidence,
            )

    return tuple(evidence)


def attach_verification_evidence(
    result: Result, evidence: Iterable[Evidence]
) -> Result:
    """Mechanically bind the complete Runtime evidence set to every claim."""

    evidence_ids = tuple(item.evidence_id for item in evidence)
    claims = tuple(
        Claim(
            id=claim.id,
            satisfies=claim.satisfies,
            claim=claim.claim,
            evidence=evidence_ids,
        )
        for claim in result.claims
    )
    return replace(result, claims=claims)


def _shell_command(command: str, *, platform: str) -> tuple[str, ...]:
    if platform == "nt":
        return (
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"{_WINDOWS_POWERSHELL_UTF8_PREAMBLE}& {{ {command} }}",
        )
    return ("/bin/sh", "-c", command)


def _verification_environment(
    *, environment: Mapping[str, str] | None, platform: str
) -> dict[str, str]:
    env = dict(os.environ if environment is None else environment)
    if platform == "nt":
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
    return env


def _require_bytes(value: bytes | str, stream: str) -> bytes:
    if not isinstance(value, bytes):
        raise RuntimeVerificationError(
            f"verification {stream} must be captured as bytes"
        )
    return value


def _strict_utf8_command(command: str) -> str:
    try:
        return command.encode("utf-8", errors="strict").decode(
            "utf-8", errors="strict"
        )
    except (AttributeError, UnicodeError) as exc:
        raise RuntimeVerificationError(
            "verification command must be strict UTF-8 text"
        ) from exc


def _summary(returncode: int, *, stdout: str, stderr: str) -> str:
    detail = stdout.strip() or stderr.strip()
    if detail:
        return detail
    return "verification passed" if returncode == 0 else "verification failed"
