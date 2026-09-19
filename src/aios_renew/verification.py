"""Deterministic Runtime-owned execution of canonical verification commands."""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from .artifacts import Claim, Evidence, EvidenceOutcome, EvidenceSource, Result


VerificationRunner = Callable[..., subprocess.CompletedProcess[bytes]]
_WINDOWS_POWERSHELL_UTF8_PREAMBLE = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)
_WINDOWS_TEMP_PREFIX = "aios-verification-"
_TEMP_CLEANUP_ATTEMPTS = 3
_TEMP_CLEANUP_RETRY_DELAY_SECONDS = 0.05


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
    temp_root: Path | None = None
    if platform == "nt":
        try:
            raw_directory.mkdir(parents=True, exist_ok=True)
            temp_root = _isolated_temp_root(
                repository=repository,
                run_id=run_id,
                environment=environment,
            )
        except (OSError, RuntimeError) as exc:
            raise RuntimeVerificationError(
                f"verification environment could not be isolated: {exc}"
            ) from exc
    else:
        raw_directory.mkdir(parents=True, exist_ok=True)
    evidence: list[Evidence] = []
    try:
        env = _verification_environment(
            environment=environment,
            platform=platform,
            temp_root=temp_root,
        )
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
    finally:
        if temp_root is not None:
            try:
                _remove_temp_root(temp_root)
            except OSError as exc:
                raise RuntimeVerificationError(
                    f"verification environment could not be cleaned: {exc}",
                    evidence=evidence,
                ) from exc

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
            f"{_WINDOWS_POWERSHELL_UTF8_PREAMBLE}& {{ {command} }}; exit $LASTEXITCODE",
        )
    return ("/bin/sh", "-c", command)


def _verification_environment(
    *,
    environment: Mapping[str, str] | None,
    platform: str,
    temp_root: Path | None,
) -> dict[str, str]:
    env = dict(os.environ if environment is None else environment)
    if platform == "nt":
        if temp_root is None:
            raise RuntimeVerificationError(
                "Windows verification requires an isolated temporary root"
            )
        isolated = str(temp_root)
        overrides = {
            "TEMP": isolated,
            "TMP": isolated,
            "TMPDIR": isolated,
            "PYTHONIOENCODING": "utf-8",
        }
        for name in tuple(env):
            if name.upper() in overrides:
                del env[name]
        env.update(overrides)
    return env


def _isolated_temp_root(
    *,
    repository: Path,
    run_id: str,
    environment: Mapping[str, str] | None,
) -> Path:
    """Allocate one execution root outside the verified repository's Git ancestry."""

    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id)
    prefix = f"{_WINDOWS_TEMP_PREFIX}{safe_run_id}-"
    errors: list[str] = []
    for base in _windows_temp_bases(repository, environment):
        temp_root: Path | None = None
        try:
            if (
                not base.is_dir()
                or base.is_relative_to(repository)
                or _has_git_ancestor(base)
            ):
                continue
            temp_root = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
            temp_root = temp_root.resolve(strict=True)
            if temp_root.is_relative_to(repository) or _has_git_ancestor(temp_root):
                _remove_temp_root(temp_root)
                continue
            return temp_root
        except OSError as exc:
            if temp_root is not None and temp_root.exists():
                try:
                    _remove_temp_root(temp_root)
                except OSError as cleanup_exc:
                    raise RuntimeError(
                        f"temporary root cleanup after allocation failure failed: "
                        f"{cleanup_exc}"
                    ) from cleanup_exc
            errors.append(f"{base}: {exc}")
    detail = "; ".join(errors) or "no external temporary base is available"
    raise RuntimeError(detail)


def _windows_temp_bases(
    repository: Path, environment: Mapping[str, str] | None
) -> tuple[Path, ...]:
    source = os.environ if environment is None else environment
    normalized = {name.upper(): value for name, value in source.items()}
    candidates = [Path(tempfile.gettempdir())]
    if normalized.get("LOCALAPPDATA"):
        candidates.append(Path(normalized["LOCALAPPDATA"]) / "Temp")
    if normalized.get("USERPROFILE"):
        candidates.extend(
            [
                Path(normalized["USERPROFILE"])
                / "AppData"
                / "Local"
                / "Temp",
                Path(normalized["USERPROFILE"]),
            ]
        )
    if normalized.get("SYSTEMROOT"):
        candidates.append(Path(normalized["SYSTEMROOT"]) / "Temp")
    candidates.append(Path(repository.anchor))

    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return tuple(unique)


def _has_git_ancestor(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (path, *path.parents))


def _remove_temp_root(temp_root: Path) -> None:
    for attempt in range(1, _TEMP_CLEANUP_ATTEMPTS + 1):
        try:
            shutil.rmtree(temp_root, onerror=_make_writable_and_retry)
            break
        except OSError as exc:
            if not temp_root.exists():
                break
            if not _is_permission_error(exc) or attempt == _TEMP_CLEANUP_ATTEMPTS:
                raise
            time.sleep(_TEMP_CLEANUP_RETRY_DELAY_SECONDS * attempt)
    if temp_root.exists():
        raise OSError(f"temporary root still exists: {temp_root}")


def _make_writable_and_retry(remove, path: str, exc_info) -> None:
    error = exc_info[1]
    if not _is_permission_error(error):
        raise error
    current_mode = os.stat(path, follow_symlinks=False).st_mode
    writable_mode = current_mode | stat.S_IREAD | stat.S_IWRITE
    if stat.S_ISDIR(current_mode):
        writable_mode |= stat.S_IEXEC
    os.chmod(path, writable_mode)
    remove(path)


def _is_permission_error(error: BaseException) -> bool:
    return isinstance(error, PermissionError) or (
        isinstance(error, OSError) and error.errno in {errno.EACCES, errno.EPERM}
    )


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
