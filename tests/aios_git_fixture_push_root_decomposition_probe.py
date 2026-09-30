"""Real-Git case construction and transient, bounded stderr instrumentation.

No CLI, output files, substituted Git results, retries or alternate transports.
Only the fixed primitive calls run_case; tests exercise push mechanics separately
in short disposable repositories, never the current-subject live experiment.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess

from scripts import aios_git_fixture_push_root_decomposition_diagnostic as contract

# TASK-240 categories and precedence are preserved, independently of signatures.
STDERR_PATTERNS = (
    ("LOCK_OR_REF_UPDATE", ("cannot lock ref", "unable to lock", "could not lock", "failed to update ref", "cannot update ref", "unable to create", "file exists")),
    ("PATH_OR_FILENAME", ("filename too long", "file name too long", "path too long", "invalid path", "invalid argument")),
    ("ACCESS_OR_PERMISSION", ("permission denied", "access is denied", "access denied", "operation not permitted", "read-only file system")),
    ("REPOSITORY_STATE", ("not a git repository", "does not appear to be a git repository", "repository not found", "bad repository", "remote unpack failed")),
    ("TRANSPORT_OR_REMOTE", ("unable to access", "could not read from remote repository", "could not resolve host", "connection refused", "connection reset", "failed to push some refs")),
    ("UNKNOWN_REVISION_OR_OBJECT", ("unknown revision", "bad object", "invalid object", "src refspec", "does not match any")),
)
SIGNATURE_PATTERNS = (
    ("REMOTE_UNPACK_FAILED", ("remote unpack failed",)),
    ("CANNOT_LOCK_REF", ("cannot lock ref",)),
    ("UNABLE_TO_CREATE", ("unable to create",)),
    ("FILENAME_TOO_LONG", ("filename too long", "file name too long", "path too long")),
    ("FAILED_TO_PUSH_REFS", ("failed to push some refs",)),
    ("ACCESS_DENIED", ("permission denied", "access is denied", "access denied", "operation not permitted", "read-only file system")),
)
MAX_STDERR = 8192


def stderr_facts(stderr: object, operands: tuple[str, ...]) -> tuple[str, list[str]]:
    text = stderr if type(stderr) is str else ""
    variants = {variant.lower() for operand in operands if operand for variant in
                (operand, operand.replace("\\", "/"), operand.replace("/", "\\"))}
    text = text.lower()
    # Remove operands BEFORE truncation or matching, including overlapping forms.
    for operand in sorted(variants, key=len, reverse=True):
        text = text.replace(operand, "<operand>")
    # Unknown quoted operands, URLs, refs, object IDs and absolute paths cannot
    # supply signatures either. Nothing from this buffer is ever serialized.
    text = re.sub(r"'[^'\r\n]*'|\"[^\"\r\n]*\"", "<operand>", text)
    text = re.sub(r"\b[a-z][a-z0-9+.-]*://[^\s<>]+|\b[a-z]:[\\/][^\s<>]+|(?<!\w)/[^\s<>]+|\brefs/[^\s<>]+|\b[0-9a-f]{40,64}\b", "<operand>", text)
    text = text[:MAX_STDERR]
    category = next((category for category, patterns in STDERR_PATTERNS
                     if any(pattern in text for pattern in patterns)), "OTHER")
    signatures = sorted(signature for signature, patterns in SIGNATURE_PATTERNS
                        if any(pattern in text for pattern in patterns))
    return category, signatures or ["OTHER"]


def canonical_ref(family: str) -> str:
    if type(family) is not str or family not in contract.FAMILIES:
        raise contract.DiagnosticError("unsupported family")
    # Same identity construction/form as sandbox zero of the existing fixture.
    identity = hashlib.sha256(f"{family}-0".encode()).hexdigest()
    return f"refs/heads/aios/{family}/{identity}"


def case_layout(root: Path, family: str, length: int) -> tuple[Path, Path]:
    contract.validate_geometry(family, length, length)
    root = root.resolve()
    prefix = root / f"{family}-{length}"
    # One authored construction, no search/approximation/fallback. A short root
    # supplies room for a single bounded ASCII padding component.
    unpadded = prefix / "p" / "upstream.git"
    padding = length - len(str(unpadded)) + 1
    if not 1 <= padding <= 200:
        raise contract.DiagnosticError("geometry cannot be constructed")
    sandbox = prefix / ("p" * padding)
    remote = sandbox / "upstream.git"
    measure_geometry(remote, family, length)
    return sandbox, remote


def measure_geometry(remote: Path, family: str, length: int) -> tuple[int, int]:
    """Prove actual resolved root and its derived full lock path, without Git."""
    resolved = remote.resolve()
    observed = len(str(resolved))
    contract.validate_geometry(family, length, observed)
    lock_length = len(str(resolved / f"{canonical_ref(family)}.lock"))
    if lock_length != contract.derived_lock_path_length(family, observed):
        raise contract.DiagnosticError("derived lock geometry mismatch")
    return observed, lock_length


def check_longpaths(repository: Path) -> None:
    if contract.git(repository, "config", "--local", "--bool", "--get", "core.longpaths").stdout.strip() != "true":
        raise contract.DiagnosticError("local longpaths configuration mismatch")


def check_resolution(repository: Path, *args: str, expected: str) -> None:
    if contract.git(repository, *args).stdout.strip() != expected:
        raise contract.DiagnosticError("Git resolution contradiction")


def checked_push(repo: Path, remote: Path, ref: str, head: str) -> tuple[int, str, list[str]]:
    """Exactly one checked PUSH. Failed PUSH is an observation, never retried."""
    operand = f"{ref}:{ref}"
    try:
        result = contract.git(repo, "push", "--quiet", "origin", operand)
    except subprocess.CalledProcessError as exc:
        if (type(exc.returncode) is not int or exc.returncode == 0
                or not contract.MIN_RETURN_CODE <= exc.returncode <= contract.MAX_RETURN_CODE
                or exc.cmd != ["git", "-C", str(repo), "push", "--quiet", "origin", operand]):
            raise contract.DiagnosticError("invalid checked push failure") from None
        category, signatures = stderr_facts(
            exc.stderr, (str(repo), str(remote), remote.as_posix(), ref, operand, head,
                         ref.rsplit("/", 1)[-1]),
        )
        return exc.returncode, category, signatures
    if type(result.returncode) is not int or result.returncode != 0:
        raise contract.DiagnosticError("checked push did not enforce failure")
    check_resolution(remote, "show-ref", "--verify", ref, expected=f"{head} {ref}")
    check_resolution(repo, "ls-remote", "--refs", "origin", ref, expected=f"{head}\t{ref}")
    return 0, "OTHER", []


def run_case(root: Path, family: str, length: int) -> dict:
    from tests.git_fixture_support import materialize_git_baseline

    sandbox, expected_remote = case_layout(root, family, length)
    # Each pair gets a fresh writable tree, index, refs, config, objects and bare
    # remote. Only immutable baseline object material may be shared (TASK-154).
    if sandbox.parent.exists():
        raise contract.DiagnosticError("case is not fresh")
    repo, remote, head = materialize_git_baseline(
        sandbox, files={"README.md": "# Fixture repository\n"},
        user_name="AIOS Operator Test", user_email="operator@example.invalid",
        commit_message="baseline", remote_head_main=True,
    )
    if (repo.resolve() != (sandbox / "repo").resolve()
            or remote.resolve() != expected_remote.resolve()
            or not re.fullmatch(r"[0-9a-f]{40}", head)):
        raise contract.DiagnosticError("materialization identity mismatch")
    ref = canonical_ref(family)
    remote_operand = contract.git(repo, "remote", "get-url", "origin").stdout.strip()
    if remote_operand != remote.as_posix():
        raise contract.DiagnosticError("origin mismatch")
    if contract.git(remote, "rev-parse", "--is-bare-repository").stdout.strip() != "true":
        raise contract.DiagnosticError("upstream is not bare")
    check_longpaths(repo)
    check_longpaths(remote)
    check_resolution(repo, "rev-parse", "--verify", "HEAD", expected=head)
    contract.git(repo, "update-ref", ref, head)
    check_resolution(repo, "show-ref", "--verify", ref, expected=f"{head} {ref}")
    # Proof against the actual resolved remote path immediately before PUSH.
    observed, lock_length = measure_geometry(remote, family, length)
    code, category, signatures = checked_push(repo, remote, ref, head)
    fact = {
        "family": family, "authored_remote_root_length": length,
        "observed_remote_root_length": observed,
        "derived_lock_path_length": lock_length,
        "outcome": "failure" if code else "success", "return_code": code,
    }
    if code:
        fact.update(stderr_category=category, stderr_signatures=signatures)
    return contract.validate_case(fact, (family, length))
