"""Reusable operator-layer GitHub transport for terminal RUN state."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


class ReviewTransportError(RuntimeError):
    """Raised when post-PASS review or artifact transport fails."""


class RemoteQueryError(ReviewTransportError):
    """Bounded failure to acquire a canonical remote ref snapshot."""

    def __init__(self, message: str, *, category: str = "UNKNOWN") -> None:
        super().__init__(message)
        self.category = category if category in {
            "AUTH", "DNS", "TLS", "TIMEOUT", "CONNECTIVITY", "UNKNOWN"
        } else "UNKNOWN"


@dataclass(frozen=True)
class RemoteRemediationLineage:
    """Immutable canonical inputs resolved from one remote remediation ref."""

    ref: str
    source_run_id: str
    review: bytes
    remediation: bytes
    run: bytes
    result: bytes
    repair: bytes | None = None
    commit_sha: str = ""
    task_id: str = ""
    task_revision: int = 0


@dataclass(frozen=True)
class RemoteFailureArtifacts:
    """Immutable Runtime-owned facts for one canonical failed RUN."""

    run_id: str
    candidate_sha: str
    run: bytes
    failure: bytes
    repair: bytes | None
    preverification: bytes | None = None


@dataclass(frozen=True)
class RemoteRepairRecovery:
    """Portable failed correction chain and canonical remote RUN namespace."""

    failures: tuple[RemoteFailureArtifacts, ...]
    remote_run_ids: tuple[str, ...]
    observed_refs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RemoteRunNamespace:
    """Validated canonical terminal RUN identities for one TASK namespace."""

    run_ids: tuple[str, ...]
    conflicts: tuple[str, ...]
    observed_refs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RemotePrimaryRecovery:
    """Exact immutable facts for one conflicting PRIMARY terminal identity."""

    run_id: str
    candidate_sha: str
    success_run: bytes
    result: bytes
    failure_run: bytes
    failure: bytes
    remote_run_ids: tuple[str, ...]
    observed_refs: tuple[tuple[str, str], ...] = ()


def _git_cmd(repo: Path, *args: str, strip: bool = True, allow_fail: bool = False) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=False,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        if allow_fail:
            return 1, "", str(exc)
        raise ReviewTransportError(f"Git command failed: {exc}") from exc
    if completed.returncode != 0 and not allow_fail:
        detail = stderr.strip() or stdout.strip()
        raise ReviewTransportError(f"Git command failed: {detail}")
    return completed.returncode, stdout.strip() if strip else stdout, stderr.strip()


def resolve_transport_remote(repo: Path) -> str:
    """Resolve configured upstream remote for the current branch or fail closed."""
    code, branch, _ = _git_cmd(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allow_fail=True)
    if code == 0 and branch:
        code, remote, _ = _git_cmd(repo, "config", "--get", f"branch.{branch}.remote", allow_fail=True)
        if code == 0 and remote:
            return remote
    raise ReviewTransportError("no configured upstream Git remote for current branch")


def _read_remote_blob(repo: Path, remote: str, commit_sha: str, rel_path: str) -> bytes | None:
    """Read a blob's content at commit_sha:rel_path from remote or local object DB."""
    _git_cmd(repo, "fetch", "--no-tags", remote, commit_sha, allow_fail=True)
    code, content, _ = _git_cmd(repo, "show", f"{commit_sha}:{rel_path}", strip=False, allow_fail=True)
    if code == 0:
        return content.encode("utf-8")
    return None


def _read_local_blob(repo: Path, commit_sha: str, rel_path: str) -> bytes | None:
    """Read an already-fetched optional blob without another remote operation."""

    code, content, _ = _git_cmd(
        repo, "show", f"{commit_sha}:{rel_path}", strip=False, allow_fail=True
    )
    return content.encode("utf-8") if code == 0 else None


def task_run_prefix(task_id: str) -> str:
    """Return the deterministic RUN prefix for one TASK."""
    if not task_id or "/" in task_id or "\\" in task_id:
        raise ReviewTransportError(f"invalid TASK id: {task_id!r}")
    task_part = task_id.removeprefix("TASK-")
    if not task_part:
        raise ReviewTransportError(f"invalid TASK id: {task_id!r}")
    return f"RUN-{task_part}-"


def resolve_remote_run_namespace(
    repo: Path, *, task_id: str, task_revision: int
) -> RemoteRunNamespace:
    """Resolve the TASK namespace, retaining RUNs from every valid revision."""

    if (
        isinstance(task_revision, bool)
        or not isinstance(task_revision, int)
        or task_revision < 1
    ):
        raise ReviewTransportError("invalid TASK revision")
    task_prefix = task_run_prefix(task_id)
    remote = resolve_transport_remote(repo)
    refs = _exact_remote_refs(
        repo,
        remote,
        f"refs/heads/aios/failure-artifacts/{task_prefix}*",
        f"refs/heads/aios/artifacts/{task_prefix}*",
    )
    try:
        return _remote_run_namespace_from_refs(
            repo,
            remote,
            task_id=task_id,
            task_revision=task_revision,
            task_prefix=task_prefix,
            refs=refs,
        )
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(refs.items()))
        raise


def _remote_run_namespace_from_refs(
    repo: Path,
    remote: str,
    *,
    task_id: str,
    task_revision: int,
    task_prefix: str,
    refs: Mapping[str, str],
) -> RemoteRunNamespace:
    """Validate a TASK terminal namespace from an already-acquired ref mapping."""

    terminal_kinds: dict[str, set[str]] = {}
    run_pattern = re.compile(rf"^{re.escape(task_prefix)}\d{{3,}}$")
    for ref, artifact_sha in refs.items():
        if ref.startswith("refs/heads/aios/failure-artifacts/"):
            terminal_kind = "FAILURE"
        elif ref.startswith("refs/heads/aios/artifacts/"):
            terminal_kind = "RESULT"
        elif ref.startswith("refs/heads/aios/failure/"):
            continue
        else:
            raise ReviewTransportError("canonical RUN ref namespace mismatch")
        run_id = ref.rsplit("/", 1)[-1]
        if not run_pattern.fullmatch(run_id):
            raise ReviewTransportError(
                f"canonical RUN ref identity is invalid for {task_id}: {ref}"
            )
        run_bytes = _read_remote_blob(
            repo, remote, artifact_sha, ".ai/transport/run.json"
        )
        if run_bytes is None:
            raise ReviewTransportError(f"canonical RUN content missing at {ref}")
        bound_task_id, _bound_revision, bound_run_id = _decode_run_task_identity(
            run_bytes, ref
        )
        if bound_run_id != run_id:
            raise ReviewTransportError(
                f"canonical RUN id mismatch at {ref}: expected {run_id}, got {bound_run_id}"
            )
        if bound_task_id != task_id:
            raise ReviewTransportError(
                f"canonical RUN TASK identity mismatch at {ref}"
            )
        terminal_kinds.setdefault(run_id, set()).add(terminal_kind)
    conflicts = tuple(
        sorted(
            run_id
            for run_id, kinds in terminal_kinds.items()
            if kinds == {"FAILURE", "RESULT"}
        )
    )
    return RemoteRunNamespace(
        tuple(sorted(terminal_kinds)), conflicts, tuple(sorted(refs.items()))
    )


def resolve_remote_primary_recovery(
    repo: Path, *, run_id: str
) -> RemotePrimaryRecovery:
    """Resolve one exact conflicting PRIMARY terminal identity from remote state."""

    task_prefix = _run_task_prefix(run_id)
    if not re.fullmatch(rf"{re.escape(task_prefix)}\d{{3,}}", run_id):
        raise ReviewTransportError(f"invalid RUN id: {run_id!r}")
    remote = resolve_transport_remote(repo)
    failure_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    success_ref = f"refs/heads/aios/artifacts/{run_id}"
    candidate_ref = f"refs/heads/aios/review/{run_id}"
    refs = _exact_remote_refs(
        repo, remote, failure_ref, success_ref, candidate_ref
    )
    try:
        return _resolve_remote_primary_recovery_from_refs(
            repo,
            remote=remote,
            run_id=run_id,
            failure_ref=failure_ref,
            success_ref=success_ref,
            candidate_ref=candidate_ref,
            refs=refs,
        )
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(refs.items()))
        raise


def _resolve_remote_primary_recovery_from_refs(
    repo: Path,
    *,
    remote: str,
    run_id: str,
    failure_ref: str,
    success_ref: str,
    candidate_ref: str,
    refs: Mapping[str, str],
) -> RemotePrimaryRecovery:
    """Validate PRIMARY recovery from one exact observed ref set."""

    missing = [
        ref for ref in (failure_ref, success_ref, candidate_ref) if ref not in refs
    ]
    if missing:
        raise ReviewTransportError(
            f"canonical PRIMARY terminal conflict is incomplete for {run_id}"
        )

    failure_run = _read_remote_blob(
        repo, remote, refs[failure_ref], ".ai/transport/run.json"
    )
    failure = _read_remote_blob(
        repo, remote, refs[failure_ref], ".ai/transport/failure.json"
    )
    success_run = _read_remote_blob(
        repo, remote, refs[success_ref], ".ai/transport/run.json"
    )
    result = _read_remote_blob(
        repo, remote, refs[success_ref], ".ai/transport/result.json"
    )
    if None in (failure_run, failure, success_run, result):
        raise ReviewTransportError(
            f"canonical PRIMARY terminal conflict content missing for {run_id}"
        )
    assert failure_run is not None
    assert failure is not None
    assert success_run is not None
    assert result is not None
    if (
        _read_remote_blob(
            repo, remote, refs[failure_ref], ".ai/transport/repair.json"
        )
        is not None
        or _read_remote_blob(
            repo, remote, refs[success_ref], ".ai/transport/repair.json"
        )
        is not None
    ):
        raise ReviewTransportError(
            f"canonical terminal conflict is not a PRIMARY lineage: {run_id}"
        )

    failure_identity = _decode_run_task_identity(failure_run, failure_ref)
    success_identity = _decode_run_task_identity(success_run, success_ref)
    if failure_identity[2] != run_id or success_identity[2] != run_id:
        raise ReviewTransportError("canonical PRIMARY terminal RUN identity mismatch")
    if failure_identity[:2] != success_identity[:2]:
        raise ReviewTransportError("canonical PRIMARY terminal TASK identity mismatch")
    namespace = resolve_remote_run_namespace(
        repo,
        task_id=success_identity[0],
        task_revision=success_identity[1],
    )
    if run_id not in namespace.conflicts:
        raise ReviewTransportError(
            f"canonical PRIMARY terminal identity is not conflicting: {run_id}"
        )
    return RemotePrimaryRecovery(
        run_id=run_id,
        candidate_sha=refs[candidate_ref],
        success_run=success_run,
        result=result,
        failure_run=failure_run,
        failure=failure,
        remote_run_ids=namespace.run_ids,
        observed_refs=tuple(sorted(refs.items())),
    )


def _decode_run_task_identity(run_bytes: bytes, ref: str) -> tuple[str, int, str]:
    try:
        run_data = json.loads(run_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewTransportError(f"canonical RUN JSON invalid for {ref}: {exc}") from exc
    if not isinstance(run_data, Mapping):
        raise ReviewTransportError(f"canonical RUN at {ref} must be a mapping")
    if run_data.get("kind") == "REMEDIATION":
        execution = run_data.get("execution")
        if not isinstance(execution, Mapping):
            raise ReviewTransportError(
                f"canonical REMEDIATION execution at {ref} must be a mapping"
            )
        source_run = execution.get("run")
        if not isinstance(source_run, Mapping):
            raise ReviewTransportError(
                f"canonical REMEDIATION run at {ref} must be a mapping"
            )
        run_id = source_run.get("run_id")
        task_data = source_run.get("task")
    elif "kind" not in run_data:
        run_id = run_data.get("run_id")
        task_data = run_data.get("task")
    else:
        raise ReviewTransportError(f"unknown canonical RUN kind at {ref}")
    if not isinstance(run_id, str) or not run_id:
        raise ReviewTransportError(f"canonical RUN run_id at {ref} is invalid")
    if not isinstance(task_data, Mapping):
        raise ReviewTransportError(f"canonical RUN task at {ref} must be a mapping")
    task_id = task_data.get("id")
    revision = task_data.get("revision")
    if not isinstance(task_id, str) or not task_id:
        raise ReviewTransportError(f"canonical RUN task.id at {ref} is invalid")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ReviewTransportError(f"canonical RUN task.revision at {ref} is invalid")
    return task_id, revision, run_id


def resolve_remote_remediation_lineages(
    repo: Path,
    *,
    finding_id: str,
    task_id: str | None = None,
    task_revision: int | None = None,
    source_run_id: str | None = None,
) -> tuple[RemoteRemediationLineage, ...]:
    """Resolve all structurally complete remote lineages for one finding id.

    TASK binding and frozen-contract validation are deliberately performed by the
    operator, which then requires exactly one matching lineage.
    """

    exact_source_requested = source_run_id is not None
    if not finding_id or "/" in finding_id or "\\" in finding_id:
        raise ReviewTransportError(f"invalid finding id: {finding_id!r}")
    if task_revision is not None and (
        isinstance(task_revision, bool)
        or not isinstance(task_revision, int)
        or task_revision < 1
    ):
        raise ReviewTransportError("invalid TASK revision")
    if source_run_id is not None:
        try:
            source_prefix = _run_task_prefix(source_run_id)
        except ReviewTransportError as exc:
            raise ReviewTransportError("invalid source RUN id") from exc
        if not re.fullmatch(rf"{re.escape(source_prefix)}\d{{3,}}", source_run_id):
            raise ReviewTransportError("invalid source RUN id")
    remote = resolve_transport_remote(repo)
    if source_run_id is not None:
        task_prefix = None
        pattern = f"refs/heads/aios/remediation/{source_run_id}-{finding_id}"
    elif task_id is not None:
        task_prefix = task_run_prefix(task_id)
        pattern = f"refs/heads/aios/remediation/{task_prefix}*-{finding_id}"
    else:
        task_prefix = None
        pattern = f"refs/heads/aios/remediation/*-{finding_id}"
    code, output, _ = _git_cmd(
        repo, "ls-remote", "--refs", remote, pattern, allow_fail=True
    )
    if code:
        raise RemoteQueryError("failed to query canonical REMEDIATION refs")

    resolved: list[RemoteRemediationLineage] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            raise ReviewTransportError("malformed canonical REMEDIATION ref result")
        commit_sha, ref = parts
        prefix = "refs/heads/aios/remediation/"
        suffix = f"-{finding_id}"
        if not ref.startswith(prefix) or not ref.endswith(suffix):
            raise ReviewTransportError("canonical REMEDIATION ref name mismatch")
        source_run_id = ref[len(prefix) : -len(suffix)]
        if not source_run_id:
            raise ReviewTransportError("canonical REMEDIATION ref has no source RUN")
        if task_prefix is not None and not source_run_id.startswith(task_prefix):
            continue

        artifacts_ref = f"refs/heads/aios/artifacts/{source_run_id}"
        if exact_source_requested:
            failure_ref = f"refs/heads/aios/failure-artifacts/{source_run_id}"
            failure_code, failure_output, _ = _git_cmd(
                repo, "ls-remote", "--refs", remote, failure_ref, allow_fail=True
            )
            if failure_code:
                raise RemoteQueryError(
                    f"failed to query canonical source state for {source_run_id}"
                )
            failure_lines = [item.split() for item in failure_output.splitlines()]
            if failure_lines:
                if any(len(item) != 2 for item in failure_lines):
                    raise ReviewTransportError(
                        f"canonical source state is malformed for {source_run_id}"
                    )
                raise ReviewTransportError(
                    f"canonical source RUN has conflicting terminal artifacts: {source_run_id}"
                )
        artifacts_code, artifacts_output, _ = _git_cmd(
            repo, "ls-remote", "--refs", remote, artifacts_ref, allow_fail=True
        )
        artifact_lines = [item.split() for item in artifacts_output.splitlines()]
        if artifacts_code or len(artifact_lines) != 1 or len(artifact_lines[0]) != 2:
            raise ReviewTransportError(
                f"canonical source artifacts missing or ambiguous for {source_run_id}"
            )
        artifacts_sha = artifact_lines[0][0]
        run = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/run.json"
        )
        result = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/result.json"
        )
        repair = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/repair.json"
        )
        if run is None or result is None:
            raise ReviewTransportError(f"canonical lineage content missing at {ref}")

        run_task_id, run_revision, run_id = _decode_run_task_identity(run, ref)
        if run_id != source_run_id:
            raise ReviewTransportError(
                f"canonical RUN id mismatch at {ref}: expected {source_run_id}, got {run_id}"
            )
        if task_id is not None and run_task_id != task_id:
            continue
        if task_revision is not None and run_revision != task_revision:
            continue

        _git_cmd(repo, "fetch", "--no-tags", remote, commit_sha, allow_fail=True)
        tree_code, tree_output, _ = _git_cmd(
            repo,
            "ls-tree",
            "-r",
            "--name-only",
            commit_sha,
            "--",
            ".ai/reviews",
            ".ai/remediations",
            allow_fail=True,
        )
        if tree_code:
            raise ReviewTransportError(f"cannot inspect canonical lineage at {ref}")
        review_paths = [
            path for path in tree_output.splitlines()
            if path.startswith(".ai/reviews/") and path.endswith((".yaml", ".yml"))
        ]
        remediation_paths = [
            path for path in tree_output.splitlines()
            if path.startswith(".ai/remediations/") and path.endswith((".yaml", ".yml"))
        ]
        if len(review_paths) != 1 or len(remediation_paths) != 1:
            raise ReviewTransportError(
                f"canonical lineage at {ref} must contain exactly one REVIEW and REMEDIATION"
            )
        review = _read_remote_blob(repo, remote, commit_sha, review_paths[0])
        remediation = _read_remote_blob(
            repo, remote, commit_sha, remediation_paths[0]
        )
        if None in (review, remediation):
            raise ReviewTransportError(f"canonical lineage content missing at {ref}")
        resolved.append(
            RemoteRemediationLineage(
                ref=ref,
                source_run_id=source_run_id,
                review=review,
                remediation=remediation,
                run=run,
                result=result,
                repair=repair,
                commit_sha=commit_sha,
                task_id=run_task_id,
                task_revision=run_revision,
            )
        )
    return tuple(resolved)


def resolve_remote_repair_recovery(
    repo: Path, *, failed_run_id: str
) -> RemoteRepairRecovery:
    """Resolve one failed correction chain entirely from canonical remote refs."""

    task_prefix = _run_task_prefix(failed_run_id)
    remote = resolve_transport_remote(repo)
    refs = _historical_repair_ref_snapshot(repo, remote, task_prefix)
    try:
        return _resolve_remote_repair_recovery_from_snapshot(
            repo,
            remote=remote,
            failed_run_id=failed_run_id,
            task_prefix=task_prefix,
            refs=refs,
        )
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(refs.items()))
        raise


def _resolve_remote_repair_recovery_from_snapshot(
    repo: Path,
    *,
    remote: str,
    failed_run_id: str,
    task_prefix: str,
    refs: Mapping[str, str],
) -> RemoteRepairRecovery:
    """Reconstruct historical REPAIR state from one already-observed snapshot."""

    discovered_run_ids = _remote_run_ids_from_refs(refs, task_prefix)
    if failed_run_id not in discovered_run_ids:
        raise ReviewTransportError(
            f"canonical failed RUN not found: {failed_run_id}"
        )
    cache: dict[str, RemoteFailureArtifacts] = {}

    def read_failure(run_id: str) -> RemoteFailureArtifacts:
        if run_id in cache:
            return cache[run_id]
        if _run_task_prefix(run_id) != task_prefix:
            raise ReviewTransportError("correction lineage crosses TASK identity")
        artifacts_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
        candidate_ref = f"refs/heads/aios/failure/{run_id}"
        if artifacts_ref not in refs or candidate_ref not in refs:
            raise ReviewTransportError(
                f"canonical failed RUN refs missing for {run_id}"
            )
        artifacts_sha = refs[artifacts_ref]
        run = _read_remote_blob(repo, remote, artifacts_sha, ".ai/transport/run.json")
        failure = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/failure.json"
        )
        repair = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/repair.json"
        )
        preverification = _read_local_blob(
            repo,
            artifacts_sha,
            ".ai/transport/pre-verification-candidate.json",
        )
        if run is None or failure is None:
            raise ReviewTransportError(
                f"canonical failed RUN content missing for {run_id}"
            )
        artifact = RemoteFailureArtifacts(
            run_id, refs[candidate_ref], run, failure, repair, preverification
        )
        cache[run_id] = artifact
        return artifact

    chain: list[RemoteFailureArtifacts] = []
    seen: set[str] = set()
    current = failed_run_id
    while True:
        if current in seen:
            raise ReviewTransportError("cyclic failed RUN continuation lineage")
        seen.add(current)
        artifact = read_failure(current)
        chain.append(artifact)
        failure_data = _json_mapping(artifact.failure, "FAILURE")
        if failure_data.get("kind") != "FAILURE" or failure_data.get("run_id") != current:
            raise ReviewTransportError("canonical FAILURE identity mismatch")
        if failure_data.get("failed_head_sha") != artifact.candidate_sha:
            raise ReviewTransportError("canonical failed-head ref mismatch")
        continuation = failure_data.get("continuation_of")
        if continuation is None:
            break
        if not isinstance(continuation, str) or not continuation:
            raise ReviewTransportError("invalid FAILURE continuation_of")
        if artifact.repair is not None:
            repair_data = _json_mapping(artifact.repair, "REPAIR execution")
            if repair_data.get("failed_run_id") != continuation:
                raise ReviewTransportError("conflicting REPAIR execution lineage")
        current = continuation

    target_task_id, target_revision, target_run_id = _decode_run_task_identity(
        chain[0].run,
        f"refs/heads/aios/failure-artifacts/{failed_run_id}",
    )
    if target_run_id != failed_run_id:
        raise ReviewTransportError("canonical failed RUN identity mismatch")
    if task_run_prefix(target_task_id) != task_prefix:
        raise ReviewTransportError("canonical failed RUN TASK identity mismatch")
    namespace = _remote_run_namespace_from_refs(
        repo,
        remote,
        task_id=target_task_id,
        task_revision=target_revision,
        task_prefix=task_prefix,
        refs=refs,
    )
    if namespace.conflicts:
        raise ReviewTransportError(
            "canonical RUN has conflicting terminal artifacts: "
            + ", ".join(namespace.conflicts)
        )
    remote_run_ids = set(namespace.run_ids)

    continuations: dict[str, list[str]] = {}
    for run_id in remote_run_ids:
        failure_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
        artifact_ref = f"refs/heads/aios/artifacts/{run_id}"
        if failure_ref in refs and artifact_ref in refs:
            raise ReviewTransportError(
                f"canonical RUN has conflicting terminal artifacts: {run_id}"
            )
        selected = refs.get(failure_ref) or refs.get(artifact_ref)
        if selected is None:
            continue
        if failure_ref in refs:
            failure = _read_remote_blob(
                repo, remote, selected, ".ai/transport/failure.json"
            )
            if failure is None:
                raise ReviewTransportError(
                    f"canonical FAILURE content missing for {run_id}"
                )
            parent = _json_mapping(failure, "FAILURE").get("continuation_of")
            if isinstance(parent, str) and parent:
                continuations.setdefault(parent, []).append(run_id)
        repair = _read_remote_blob(repo, remote, selected, ".ai/transport/repair.json")
        if repair is None:
            continue
        parent = _json_mapping(repair, "REPAIR execution").get("failed_run_id")
        if isinstance(parent, str) and parent and run_id not in continuations.get(parent, []):
            continuations.setdefault(parent, []).append(run_id)
    duplicates = continuations.get(failed_run_id, [])
    if duplicates:
        raise ReviewTransportError(
            "canonical continuation already exists for failed RUN: "
            + ", ".join(sorted(duplicates))
        )
    return RemoteRepairRecovery(
        tuple(chain), tuple(sorted(remote_run_ids)), tuple(sorted(refs.items()))
    )


def read_remote_task(repo: Path, *, commit_sha: str, task_id: str) -> bytes:
    """Read an exact historical TASK without checking out its subject tree."""

    if not task_id or "/" in task_id or "\\" in task_id:
        raise ReviewTransportError(f"invalid TASK id: {task_id!r}")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit_sha):
        raise ReviewTransportError("invalid historical TASK commit SHA")
    remote = resolve_transport_remote(repo)
    content = _read_remote_blob(
        repo, remote, commit_sha, f".ai/tasks/{task_id}.yaml"
    )
    if content is None:
        raise ReviewTransportError(
            f"historical TASK not found at {commit_sha}: {task_id}"
        )
    return content


def _run_task_prefix(run_id: str) -> str:
    stem, separator, sequence = run_id.rpartition("-")
    if (
        not separator
        or not stem.startswith("RUN-")
        or len(stem) <= len("RUN-")
        or not sequence.isdigit()
    ):
        raise ReviewTransportError(f"invalid RUN id: {run_id!r}")
    return f"{stem}-"


def _remote_run_ids(repo: Path, remote: str, task_prefix: str) -> set[str]:
    refs = _exact_remote_refs(
        repo,
        remote,
        f"refs/heads/aios/failure-artifacts/{task_prefix}*",
        f"refs/heads/aios/artifacts/{task_prefix}*",
    )
    return _remote_run_ids_from_refs(refs, task_prefix)


def _remote_run_ids_from_refs(
    refs: Mapping[str, str], task_prefix: str
) -> set[str]:
    result: set[str] = set()
    for ref in refs:
        if not ref.startswith(
            ("refs/heads/aios/failure-artifacts/", "refs/heads/aios/artifacts/")
        ):
            continue
        run_id = ref.rsplit("/", 1)[-1]
        if not run_id.startswith(task_prefix):
            raise ReviewTransportError("canonical RUN ref TASK identity mismatch")
        _run_task_prefix(run_id)
        result.add(run_id)
    return result


def _historical_repair_ref_snapshot(
    repo: Path, remote: str, task_prefix: str
) -> Mapping[str, str]:
    """Acquire and validate one immutable TASK-scoped historical REPAIR snapshot."""

    patterns = (
        f"refs/heads/aios/failure-artifacts/{task_prefix}*",
        f"refs/heads/aios/artifacts/{task_prefix}*",
        f"refs/heads/aios/failure/{task_prefix}*",
    )
    code, output, stderr = _git_cmd(
        repo, "ls-remote", "--refs", remote, *patterns, allow_fail=True
    )
    if code:
        category = _remote_snapshot_failure_category(stderr)
        raise RemoteQueryError(
            "historical REPAIR snapshot acquisition failed: "
            f"exit_status={code} category={category}",
            category=category,
        )

    allowed_prefixes = (
        "refs/heads/aios/failure-artifacts/",
        "refs/heads/aios/artifacts/",
        "refs/heads/aios/failure/",
    )
    run_pattern = re.compile(rf"^{re.escape(task_prefix)}\d{{3,}}$")
    object_pattern = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
    refs: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            raise ReviewTransportError(
                "malformed historical REPAIR canonical ref snapshot"
            )
        commit_sha, ref = parts
        matching_prefixes = [
            prefix for prefix in allowed_prefixes if ref.startswith(prefix)
        ]
        if len(matching_prefixes) != 1:
            raise ReviewTransportError(
                "invalid historical REPAIR canonical ref snapshot prefix"
            )
        run_id = ref[len(matching_prefixes[0]) :]
        if not run_pattern.fullmatch(run_id) or not object_pattern.fullmatch(commit_sha):
            raise ReviewTransportError(
                "invalid historical REPAIR canonical ref snapshot identity"
            )
        if ref in refs:
            raise ReviewTransportError(
                "ambiguous historical REPAIR canonical ref snapshot"
            )
        refs[ref] = commit_sha
    return MappingProxyType(refs)


def _remote_snapshot_failure_category(stderr: str) -> str:
    """Return a bounded operational category without relaying raw Git diagnostics."""

    detail = stderr.casefold()
    if any(
        signal in detail
        for signal in (
            "authentication failed",
            "authorization failed",
            "could not read username",
            "permission denied",
            "access denied",
        )
    ):
        return "AUTH"
    if any(
        signal in detail
        for signal in ("could not resolve host", "name resolution", "no such host")
    ):
        return "DNS"
    if any(
        signal in detail
        for signal in ("certificate", "ssl", "tls")
    ):
        return "TLS"
    if any(signal in detail for signal in ("timed out", "timeout")):
        return "TIMEOUT"
    if any(
        signal in detail
        for signal in (
            "could not connect",
            "couldn't connect",
            "failed to connect",
            "connection reset",
            "network is unreachable",
        )
    ):
        return "CONNECTIVITY"
    return "UNKNOWN"


def _exact_remote_refs(repo: Path, remote: str, *patterns: str) -> dict[str, str]:
    code, output, _ = _git_cmd(
        repo, "ls-remote", "--refs", remote, *patterns, allow_fail=True
    )
    if code:
        raise RemoteQueryError("failed to query canonical refs")
    refs: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2 or parts[1] in refs:
            raise ReviewTransportError("malformed or ambiguous canonical ref result")
        refs[parts[1]] = parts[0]
    return refs


def _json_mapping(content: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewTransportError(f"invalid canonical {name} JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ReviewTransportError(f"canonical {name} must be a mapping")
    return value


def _create_artifacts_commit(
    repo: Path,
    *,
    run_path: Path,
    result_path: Path,
    run_id: str,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
) -> str:
    """Create an isolated success artifact tree with optional operational state."""
    if not run_path.is_file():
        raise ReviewTransportError(f"persisted RUN JSON missing: {run_path}")
    if not result_path.is_file():
        raise ReviewTransportError(f"persisted canonical ResultPackage JSON missing: {result_path}")

    run_bytes = run_path.read_bytes()
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=run_bytes,
            capture_output=True,
            check=True,
        )
        run_blob_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to hash run.json: {exc}") from exc

    result_bytes = result_path.read_bytes()
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=result_bytes,
            capture_output=True,
            check=True,
        )
        result_blob_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to hash result.json: {exc}") from exc

    lineage_entry = ""
    if lineage_path is not None:
        if not lineage_path.is_file():
            raise ReviewTransportError(f"persisted REPAIR lineage JSON missing: {lineage_path}")
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=lineage_path.read_bytes(), capture_output=True, check=True,
            )
            lineage_sha = proc.stdout.decode("utf-8", errors="strict").strip()
            lineage_entry = f"100644 blob {lineage_sha}\trepair.json\n"
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(f"failed to hash repair.json: {exc}") from exc

    observation_entry = ""
    if observation_path is not None:
        if not observation_path.is_file():
            raise ReviewTransportError(
                f"persisted RUN_OBSERVATION JSON missing: {observation_path}"
            )
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=observation_path.read_bytes(), capture_output=True, check=True,
            )
            observation_sha = proc.stdout.decode("utf-8", errors="strict").strip()
            observation_entry = (
                f"100644 blob {observation_sha}\tobservation.json\n"
            )
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(
                f"failed to hash observation.json: {exc}"
            ) from exc

    tree_input = (
        f"100644 blob {run_blob_sha}\trun.json\n"
        f"100644 blob {result_blob_sha}\tresult.json\n"
        f"{lineage_entry}"
        f"{observation_entry}"
    )
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=tree_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        transport_tree_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create transport tree: {exc}") from exc

    ai_tree_input = f"040000 tree {transport_tree_sha}\ttransport\n"
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=ai_tree_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        ai_tree_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create .ai tree: {exc}") from exc

    root_tree_input = f"040000 tree {ai_tree_sha}\t.ai\n"
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=root_tree_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        root_tree_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create root artifacts tree: {exc}") from exc

    commit_message = f"AIOS artifacts for {run_id}"
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "commit-tree", root_tree_sha, "-m", commit_message),
            capture_output=True,
            check=True,
        )
        commit_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create artifacts commit: {exc}") from exc

    return commit_sha


def _create_named_artifacts_commit(
    repo: Path,
    *,
    run_path: Path,
    artifact_path: Path,
    artifact_name: str,
    run_id: str,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
    preverification_path: Path | None = None,
) -> str:
    """Create an isolated artifacts commit without touching the worktree."""

    if not run_path.is_file():
        raise ReviewTransportError(f"persisted RUN JSON missing: {run_path}")
    if not artifact_path.is_file():
        raise ReviewTransportError(f"persisted {artifact_name} JSON missing: {artifact_path}")

    blobs: dict[str, str] = {}
    inputs = [("run.json", run_path), (artifact_name, artifact_path)]
    if lineage_path is not None:
        if not lineage_path.is_file():
            raise ReviewTransportError(
                f"persisted REPAIR lineage JSON missing: {lineage_path}"
            )
        inputs.append(("repair.json", lineage_path))
    if observation_path is not None:
        if not observation_path.is_file():
            raise ReviewTransportError(
                f"persisted RUN_OBSERVATION JSON missing: {observation_path}"
            )
        inputs.append(("observation.json", observation_path))
    if preverification_path is not None:
        if not preverification_path.is_file():
            raise ReviewTransportError(
                "persisted pre-verification candidate JSON missing: "
                f"{preverification_path}"
            )
        inputs.append(
            ("pre-verification-candidate.json", preverification_path)
        )
    for name, path in inputs:
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=path.read_bytes(), capture_output=True, check=True,
            )
            blobs[name] = proc.stdout.decode("utf-8", errors="strict").strip()
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(f"failed to hash {name}: {exc}") from exc
    tree_input = "".join(
        f"100644 blob {sha}\t{name}\n" for name, sha in sorted(blobs.items())
    )
    try:
        transport = subprocess.run(
            ("git", "-C", str(repo), "mktree"), input=tree_input.encode(),
            capture_output=True, check=True,
        ).stdout.decode().strip()
        ai = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {transport}\ttransport\n".encode(),
            capture_output=True, check=True,
        ).stdout.decode().strip()
        root = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {ai}\t.ai\n".encode(),
            capture_output=True, check=True,
        ).stdout.decode().strip()
        return subprocess.run(
            ("git", "-C", str(repo), "commit-tree", root, "-m", f"AIOS {artifact_name} for {run_id}"),
            capture_output=True, check=True,
        ).stdout.decode().strip()
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        raise ReviewTransportError(f"failed to create {artifact_name} artifacts commit: {exc}") from exc


def transport_failure(
    repo: Path, *, run_id: str, head_sha: str, run_path: Path, failure_path: Path,
    publish_candidate: bool = True,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
    preverification_path: Path | None = None,
) -> None:
    """Publish an immutable, authority-checked failed candidate and its facts."""

    remote = resolve_transport_remote(repo)
    candidate_ref = f"refs/heads/aios/failure/{run_id}"
    artifacts_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    expected = {
        ".ai/transport/run.json": run_path.read_bytes(),
        ".ai/transport/failure.json": failure_path.read_bytes(),
    }
    if lineage_path is not None:
        expected[".ai/transport/repair.json"] = lineage_path.read_bytes()
    expected_observation = (
        observation_path.read_bytes() if observation_path is not None else None
    )
    expected_preverification = (
        preverification_path.read_bytes()
        if preverification_path is not None
        else None
    )
    queried_refs = (candidate_ref, artifacts_ref) if publish_candidate else (artifacts_ref,)
    code, output, _ = _git_cmd(repo, "ls-remote", remote, *queried_refs, allow_fail=True)
    if code:
        raise ReviewTransportError(f"failed to query remote refs from {remote}")
    refs = {parts[1]: parts[0] for line in output.splitlines() if len(parts := line.split()) >= 2}
    specs: list[str] = []
    if publish_candidate:
        if candidate_ref in refs:
            if refs[candidate_ref] != head_sha:
                raise ReviewTransportError(f"remote failure ref {candidate_ref} conflict")
        else:
            specs.append(f"{head_sha}:{candidate_ref}")
    if artifacts_ref in refs:
        for path, content in expected.items():
            if _read_remote_blob(repo, remote, refs[artifacts_ref], path) != content:
                raise ReviewTransportError(
                    f"remote failure artifacts ref {artifacts_ref} exists with different artifact content"
                )
        remote_observation = _read_remote_blob(
            repo, remote, refs[artifacts_ref], ".ai/transport/observation.json"
        )
        if (
            expected_observation is not None
            and remote_observation not in (None, expected_observation)
        ):
            raise ReviewTransportError(
                f"remote failure artifacts ref {artifacts_ref} exists with different observation content"
            )
        remote_preverification = _read_remote_blob(
            repo,
            remote,
            refs[artifacts_ref],
            ".ai/transport/pre-verification-candidate.json",
        )
        if (
            expected_preverification is not None
            and remote_preverification != expected_preverification
        ):
            raise ReviewTransportError(
                f"remote failure artifacts ref {artifacts_ref} exists with different pre-verification candidate content"
            )
    else:
        commit = _create_named_artifacts_commit(
            repo, run_path=run_path, artifact_path=failure_path,
            artifact_name="failure.json", run_id=run_id,
            lineage_path=lineage_path,
            observation_path=observation_path,
            preverification_path=preverification_path,
        )
        specs.append(f"{commit}:{artifacts_ref}")
    if specs:
        code, _, stderr = _git_cmd(repo, "push", "--no-tags", remote, *specs, allow_fail=True)
        if code:
            raise ReviewTransportError(f"failed to push transport refs to {remote}: {stderr}")


def transport_admission_failure(
    repo: Path, *, identity: str, diagnostic_path: Path
) -> None:
    """Publish one immutable, content-addressed pre-RUN diagnostic."""

    if len(identity) != 64 or any(
        character not in "0123456789abcdef" for character in identity
    ):
        raise ReviewTransportError("invalid admission-failure identity")
    if not diagnostic_path.is_file():
        raise ReviewTransportError(
            f"persisted admission-failure JSON missing: {diagnostic_path}"
        )
    if hashlib.sha256(diagnostic_path.read_bytes()).hexdigest() != identity:
        raise ReviewTransportError(
            "admission-failure identity does not match diagnostic content"
        )
    remote = resolve_transport_remote(repo)
    ref = f"refs/heads/aios/admission-failure/{identity}"
    code, output, _ = _git_cmd(
        repo, "ls-remote", "--refs", remote, ref, allow_fail=True
    )
    if code:
        raise ReviewTransportError(f"failed to query remote refs from {remote}")
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if lines:
        if len(lines) != 1 or len(lines[0]) != 2 or lines[0][1] != ref:
            raise ReviewTransportError("malformed admission-failure ref result")
        remote_content = _read_remote_blob(
            repo,
            remote,
            lines[0][0],
            ".ai/transport/admission-failure.json",
        )
        if remote_content != diagnostic_path.read_bytes():
            raise ReviewTransportError(
                f"remote admission-failure ref {ref} exists with different content"
            )
        return

    commit = _create_admission_failure_commit(
        repo, identity=identity, diagnostic_path=diagnostic_path
    )
    code, _, stderr = _git_cmd(
        repo,
        "push",
        "--no-tags",
        remote,
        f"{commit}:{ref}",
        allow_fail=True,
    )
    if code:
        raise ReviewTransportError(
            f"failed to push admission-failure ref to {remote}: {stderr}"
        )


def _create_admission_failure_commit(
    repo: Path, *, identity: str, diagnostic_path: Path
) -> str:
    """Create an isolated diagnostic commit without touching the worktree."""

    try:
        blob = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=diagnostic_path.read_bytes(),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        transport_tree = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=(
                f"100644 blob {blob}\tadmission-failure.json\n"
            ).encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        ai_tree = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {transport_tree}\ttransport\n".encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        root_tree = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {ai_tree}\t.ai\n".encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        return subprocess.run(
            (
                "git",
                "-C",
                str(repo),
                "commit-tree",
                root_tree,
                "-m",
                f"AIOS admission failure {identity}",
            ),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        raise ReviewTransportError(
            f"failed to create admission-failure commit: {exc}"
        ) from exc


def read_remote_repair(repo: Path, run_id: str) -> bytes:
    """Read the single ChatGPT-authored repair bound to a failed RUN."""

    remote = resolve_transport_remote(repo)
    ref = f"refs/heads/aios/repair/{run_id}"
    code, output, _ = _git_cmd(repo, "ls-remote", remote, ref, allow_fail=True)
    if code or not output.strip():
        raise ReviewTransportError(f"remote REPAIR not found for {run_id}")
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if len(lines) != 1 or len(lines[0]) != 2 or lines[0][1] != ref:
        raise ReviewTransportError(f"remote REPAIR is ambiguous for {run_id}")
    content = _read_remote_blob(repo, remote, lines[0][0], ".ai/transport/repair.json")
    if content is None:
        raise ReviewTransportError(f"remote REPAIR JSON missing for {run_id}")
    return content


def transport_post_pass(
    repo: Path,
    *,
    run_id: str,
    head_sha: str,
    run_path: Path,
    result_path: Path,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
) -> None:
    """Publish aios/review/<RUN_ID> and aios/artifacts/<RUN_ID> to upstream remote."""
    remote = resolve_transport_remote(repo)
    review_ref = f"refs/heads/aios/review/{run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"

    if not run_path.is_file():
        raise ReviewTransportError(f"persisted RUN JSON missing: {run_path}")
    if not result_path.is_file():
        raise ReviewTransportError(f"persisted canonical ResultPackage JSON missing: {result_path}")

    expected_run_bytes = run_path.read_bytes()
    expected_result_bytes = result_path.read_bytes()
    expected_lineage_bytes = lineage_path.read_bytes() if lineage_path is not None else None
    expected_observation_bytes = (
        observation_path.read_bytes() if observation_path is not None else None
    )

    code, ls_out, _ = _git_cmd(repo, "ls-remote", remote, review_ref, artifacts_ref, allow_fail=True)
    if code != 0:
        raise ReviewTransportError(f"failed to query remote refs from {remote}")

    existing_remote_refs: dict[str, str] = {}
    for line in ls_out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            existing_remote_refs[parts[1]] = parts[0]

    push_review = True
    if review_ref in existing_remote_refs:
        existing_review_sha = existing_remote_refs[review_ref]
        if existing_review_sha == head_sha:
            push_review = False
        else:
            raise ReviewTransportError(
                f"remote review ref {review_ref} conflict: points to {existing_review_sha}, expected {head_sha}"
            )

    push_artifacts = True
    if artifacts_ref in existing_remote_refs:
        existing_artifacts_sha = existing_remote_refs[artifacts_ref]
        remote_run_bytes = _read_remote_blob(repo, remote, existing_artifacts_sha, ".ai/transport/run.json")
        remote_result_bytes = _read_remote_blob(repo, remote, existing_artifacts_sha, ".ai/transport/result.json")
        remote_lineage_bytes = _read_remote_blob(
            repo, remote, existing_artifacts_sha, ".ai/transport/repair.json"
        )
        remote_observation_bytes = _read_remote_blob(
            repo, remote, existing_artifacts_sha, ".ai/transport/observation.json"
        )

        if (
            remote_run_bytes == expected_run_bytes
            and remote_result_bytes == expected_result_bytes
            and remote_lineage_bytes == expected_lineage_bytes
            and (
                expected_observation_bytes is None
                or remote_observation_bytes in (None, expected_observation_bytes)
            )
        ):
            push_artifacts = False
        else:
            raise ReviewTransportError(
                f"remote artifacts ref {artifacts_ref} exists with different artifact content"
            )

    if not push_review and not push_artifacts:
        return

    artifacts_commit_sha = _create_artifacts_commit(
        repo,
        run_path=run_path,
        result_path=result_path,
        run_id=run_id,
        lineage_path=lineage_path,
        observation_path=observation_path,
    )

    push_specs: list[str] = []
    if push_review:
        push_specs.append(f"{head_sha}:{review_ref}")
    if push_artifacts:
        push_specs.append(f"{artifacts_commit_sha}:{artifacts_ref}")

    if push_specs:
        code, _, stderr = _git_cmd(repo, "push", "--no-tags", remote, *push_specs, allow_fail=True)
        if code != 0:
            raise ReviewTransportError(f"failed to push transport refs to {remote}: {stderr}")
