"""Deterministic local Runtime source-identity drift guard for AIOS self-host execution."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


GitRunner = Any
DriftDisposition = Literal["MATCH", "NOT_APPLICABLE", "DRIFT_DETECTED", "IDENTITY_UNAVAILABLE"]


class RuntimeIdentityError(RuntimeError):
    """Raised when runtime identity cannot be established or source drift is detected."""


@dataclass(frozen=True)
class RuntimeIdentity:
    """Resolved Git identity of the actually imported AIOS runtime package."""

    checkout_root: Path
    repository_identities: frozenset[str]
    tree_fingerprint: str
    package_path: Path


@dataclass(frozen=True)
class RuntimeDriftReport:
    """Deterministic assessment of subject vs loaded runtime identity."""

    disposition: DriftDisposition
    subject_repo: Path
    subject_fingerprint: str | None = None
    runtime_root: Path | None = None
    runtime_fingerprint: str | None = None
    diagnostic: str | None = None


def normalize_remote_url(raw_url: str) -> str:
    """Normalize ordinary Git remote forms into a deterministic platform-neutral identity."""

    url = raw_url.strip().strip("'\"")
    if not url:
        return ""

    if url.startswith("file://"):
        url = url[7:]
        if re.match(r"^/[A-Za-z]:", url):
            url = url[1:]
        try:
            return f"local:{Path(url).resolve().as_posix().lower()}"
        except (OSError, ValueError):
            return f"local:{url.lower()}"

    # SCP-style: [user@]host:path
    # Windows drive letter guard: if host is a single alpha character, it is a drive letter (e.g. C:\...)
    scp_match = re.match(
        r"^(?:(?P<user>[^@/:\\]+)@)?(?P<host>[^@/:\\]+):(?P<path>[^/\\].*)$", url
    )
    if scp_match and not (
        len(scp_match.group("host")) == 1 and scp_match.group("host").isalpha()
    ):
        host = scp_match.group("host").lower()
        path = scp_match.group("path").strip().lstrip("/").rstrip("/")
        if path.lower().endswith(".git"):
            path = path[:-4]
        return f"{host}/{path.lower()}"

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("https", "http", "ssh", "git"):
        host = (parsed.hostname or "").lower()
        path = parsed.path.strip().lstrip("/").rstrip("/")
        if path.lower().endswith(".git"):
            path = path[:-4]
        return f"{host}/{path.lower()}"

    try:
        candidate = Path(url)
        if candidate.is_absolute() or candidate.exists():
            return f"local:{candidate.resolve().as_posix().lower()}"
    except (OSError, ValueError):
        pass

    clean = url.rstrip("/")
    if clean.lower().endswith(".git"):
        clean = clean[:-4]
    return clean.lower()


def is_aios_self_host_repo(repo: Path) -> bool:
    """Determine whether a repository checkout is deterministically AIOS-renew."""

    repo_path = Path(repo).resolve()
    init_file = repo_path / "src" / "aios_renew" / "__init__.py"
    if not init_file.is_file():
        return False
    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8")
            if re.search(r'name\s*=\s*["\']aios-renew["\']', content):
                return True
        except (OSError, UnicodeDecodeError):
            pass
    return False


def resolve_repository_identities(
    repo: Path,
    *,
    git_runner: GitRunner = subprocess.run,
) -> frozenset[str]:
    """Resolve normalized repository identities for a checkout without network calls."""

    repo_path = Path(repo).resolve()
    identities: set[str] = set()

    identities.add(f"local:{repo_path.as_posix().lower()}")

    completed = git_runner(
        ("git", "-C", str(repo_path), "rev-parse", "--git-common-dir"),
        capture_output=True,
        text=False,
        check=False,
    )
    if completed.returncode == 0:
        common_raw = completed.stdout.decode("utf-8", errors="replace").strip()
        if common_raw:
            common_path = Path(common_raw)
            if not common_path.is_absolute():
                common_path = repo_path / common_path
            try:
                resolved_common = common_path.resolve()
                identities.add(f"common-dir:{resolved_common.as_posix().lower()}")
            except (OSError, RuntimeError):
                pass

    completed = git_runner(
        ("git", "-C", str(repo_path), "config", "--get-regexp", r"^remote\..*\.url$"),
        capture_output=True,
        text=False,
        check=False,
    )
    if completed.returncode == 0:
        lines = completed.stdout.decode("utf-8", errors="replace").splitlines()
        for line in lines:
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                raw_url = parts[1]
                normalized = normalize_remote_url(raw_url)
                if normalized:
                    identities.add(normalized)
                    if normalized.startswith("local:"):
                        target_dir = Path(normalized[6:])
                        if target_dir != repo_path and target_dir.is_dir():
                            try:
                                target_identities = resolve_repository_identities(
                                    target_dir, git_runner=git_runner
                                )
                                identities.update(target_identities)
                            except Exception:
                                pass

    if is_aios_self_host_repo(repo_path):
        identities.add("package:aios-renew")

    return frozenset(identities)


def resolve_tree_fingerprint(
    repo: Path,
    subpath: str = "src/aios_renew",
    *,
    git_runner: GitRunner = subprocess.run,
) -> str:
    """Resolve the deterministic Git tree fingerprint for a path in HEAD."""

    repo_path = Path(repo).resolve()
    clean_subpath = Path(subpath).as_posix()
    completed = git_runner(
        ("git", "-C", str(repo_path), "rev-parse", f"HEAD:{clean_subpath}"),
        capture_output=True,
        text=False,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeIdentityError(
            f"failed to resolve tree fingerprint for '{clean_subpath}' in '{repo_path}': {stderr}"
        )
    fingerprint = completed.stdout.decode("utf-8", errors="replace").strip()
    if not re.fullmatch(r"^[0-9a-f]{40}$", fingerprint):
        raise RuntimeIdentityError(
            f"invalid tree fingerprint for '{clean_subpath}' in '{repo_path}': {fingerprint!r}"
        )
    return fingerprint


def resolve_runtime_identity(
    package_or_path: Any = None,
    *,
    git_runner: GitRunner = subprocess.run,
) -> RuntimeIdentity:
    """Resolve the actually imported AIOS package's Git checkout and tree fingerprint."""

    if package_or_path is None:
        try:
            import aios_renew
            package_or_path = aios_renew
        except ImportError as exc:
            raise RuntimeIdentityError(f"aios_renew package is not imported: {exc}") from exc

    if isinstance(package_or_path, (str, Path)):
        candidate = Path(package_or_path).resolve()
        package_path = candidate.parent if candidate.is_file() else candidate
    else:
        file_path = getattr(package_or_path, "__file__", None)
        if not file_path:
            raise RuntimeIdentityError(
                f"cannot determine source location of loaded runtime package: {package_or_path!r}"
            )
        package_path = Path(file_path).resolve().parent

    completed = git_runner(
        ("git", "-C", str(package_path), "rev-parse", "--show-toplevel"),
        capture_output=True,
        text=False,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeIdentityError(
            f"loaded AIOS runtime package at '{package_path}' is not inside a Git repository: {stderr}"
        )

    checkout_root = Path(
        completed.stdout.decode("utf-8", errors="replace").strip()
    ).resolve()

    try:
        rel_subpath = package_path.relative_to(checkout_root).as_posix()
    except ValueError:
        rel_subpath = "src/aios_renew"

    tree_fingerprint = resolve_tree_fingerprint(
        checkout_root, rel_subpath, git_runner=git_runner
    )
    repository_identities = resolve_repository_identities(
        checkout_root, git_runner=git_runner
    )

    return RuntimeIdentity(
        checkout_root=checkout_root,
        repository_identities=repository_identities,
        tree_fingerprint=tree_fingerprint,
        package_path=package_path,
    )


def evaluate_runtime_drift(
    subject_repo: Path,
    *,
    runtime_package: Any = None,
    git_runner: GitRunner = subprocess.run,
) -> RuntimeDriftReport:
    """Evaluate whether the admitted subject matches the loaded AIOS runtime source."""

    subject_path = Path(subject_repo).resolve()
    is_self_host = is_aios_self_host_repo(subject_path)

    try:
        runtime_id = resolve_runtime_identity(
            runtime_package, git_runner=git_runner
        )
    except (RuntimeIdentityError, OSError) as exc:
        if is_self_host:
            diagnostic = (
                f"runtime source identity unavailable: loaded AIOS runtime source cannot "
                f"establish required Git/tree identity for self-host repository '{subject_path}': {exc}"
            )
            return RuntimeDriftReport(
                disposition="IDENTITY_UNAVAILABLE",
                subject_repo=subject_path,
                diagnostic=diagnostic,
            )
        return RuntimeDriftReport(
            disposition="NOT_APPLICABLE",
            subject_repo=subject_path,
        )

    subject_identities = resolve_repository_identities(
        subject_path, git_runner=git_runner
    )

    same_repo = bool(
        subject_identities & runtime_id.repository_identities
    ) or (subject_path == runtime_id.checkout_root)

    if not same_repo:
        return RuntimeDriftReport(
            disposition="NOT_APPLICABLE",
            subject_repo=subject_path,
            runtime_root=runtime_id.checkout_root,
            runtime_fingerprint=runtime_id.tree_fingerprint,
        )

    try:
        subject_fingerprint = resolve_tree_fingerprint(
            subject_path, "src/aios_renew", git_runner=git_runner
        )
    except (RuntimeIdentityError, OSError) as exc:
        diagnostic = (
            f"runtime source identity unavailable: subject self-host repository '{subject_path}' "
            f"cannot establish required src/aios_renew tree fingerprint: {exc}"
        )
        return RuntimeDriftReport(
            disposition="IDENTITY_UNAVAILABLE",
            subject_repo=subject_path,
            runtime_root=runtime_id.checkout_root,
            runtime_fingerprint=runtime_id.tree_fingerprint,
            diagnostic=diagnostic,
        )

    if subject_fingerprint == runtime_id.tree_fingerprint:
        return RuntimeDriftReport(
            disposition="MATCH",
            subject_repo=subject_path,
            subject_fingerprint=subject_fingerprint,
            runtime_root=runtime_id.checkout_root,
            runtime_fingerprint=runtime_id.tree_fingerprint,
        )

    diagnostic = (
        f"runtime source drift detected: subject repository '{subject_path}' "
        f"(src/aios_renew tree {subject_fingerprint}) does not match "
        f"loaded runtime '{runtime_id.checkout_root}' "
        f"(src/aios_renew tree {runtime_id.tree_fingerprint})"
    )
    return RuntimeDriftReport(
        disposition="DRIFT_DETECTED",
        subject_repo=subject_path,
        subject_fingerprint=subject_fingerprint,
        runtime_root=runtime_id.checkout_root,
        runtime_fingerprint=runtime_id.tree_fingerprint,
        diagnostic=diagnostic,
    )


def guard_runtime_drift(
    subject_repo: Path,
    *,
    runtime_package: Any = None,
    git_runner: GitRunner = subprocess.run,
) -> RuntimeDriftReport:
    """Enforce the self-host runtime drift guard; fail closed on drift or unavailable identity."""

    report = evaluate_runtime_drift(
        subject_repo, runtime_package=runtime_package, git_runner=git_runner
    )
    if report.disposition in ("DRIFT_DETECTED", "IDENTITY_UNAVAILABLE"):
        assert report.diagnostic is not None
        raise RuntimeIdentityError(report.diagnostic)
    return report
