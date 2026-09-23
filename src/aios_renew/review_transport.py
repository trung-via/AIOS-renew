"""Reusable operator-layer GitHub transport for terminal RUN state."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .terminal_attention import (
    TerminalAttentionError,
    publish_terminal_attention,
)


class ReviewTransportError(RuntimeError):
    """Raised when post-PASS review or artifact transport fails."""


class RemoteQueryError(ReviewTransportError):
    """Bounded failure to acquire a canonical remote ref snapshot."""

    def __init__(self, message: str, *, category: str = "UNKNOWN") -> None:
        super().__init__(message)
        self.category = category if category in {
            "AUTH", "DNS", "TLS", "TIMEOUT", "CONNECTIVITY", "UNKNOWN"
        } else "UNKNOWN"


def validate_runtime_failure_binding(
    failure: Mapping[str, Any],
    *,
    run_id: str,
    task_id: str,
    task_revision: int,
    executor: str,
    base_sha: str,
    candidate_sha: str,
    modification_scope: tuple[str, ...],
    actual_descends_from_base: bool | None = None,
    actual_changed_files: set[str] | None = None,
) -> None:
    """Validate the Runtime FAILURE identity and repair-candidate contract."""

    task = failure.get("task")
    if (
        failure.get("kind") != "FAILURE"
        or failure.get("run_id") != run_id
        or not isinstance(task, Mapping)
        or dict(task) != {"id": task_id, "revision": task_revision}
        or failure.get("executor") != executor
        or failure.get("base_sha") != base_sha
        or failure.get("failed_head_sha") != candidate_sha
    ):
        raise ValueError("FAILURE identity does not match RUN and candidate")

    candidate = failure.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("FAILURE candidate must be a mapping")
    flags = (
        candidate.get("transportable"),
        candidate.get("repairable"),
        candidate.get("dirty"),
        candidate.get("descends_from_base"),
    )
    if not all(isinstance(value, bool) for value in flags):
        raise ValueError("FAILURE candidate flags must be booleans")

    def paths(name: str) -> list[str]:
        value = candidate.get(name)
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) and item for item in value)
            or len(value) != len(set(value))
        ):
            raise ValueError(f"FAILURE candidate {name} is invalid")
        return value

    changed_files = paths("changed_files")
    outside_scope = paths("outside_task_scope")
    if set(outside_scope) != set(changed_files).difference(modification_scope):
        raise ValueError("FAILURE candidate scope binding is invalid")
    descends = candidate["descends_from_base"]
    repairable = not candidate["dirty"] and descends
    transportable = repairable and not outside_scope
    if (
        candidate["repairable"] is not repairable
        or candidate["transportable"] is not transportable
    ):
        raise ValueError("FAILURE candidate repair binding is invalid")
    if (
        actual_descends_from_base is not None
        and descends is not actual_descends_from_base
    ):
        raise ValueError("FAILURE candidate ancestry binding is invalid")
    if (
        actual_changed_files is not None
        and set(changed_files) != actual_changed_files
    ):
        raise ValueError("FAILURE candidate changed-files binding is invalid")


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
    execution_profile: bytes | None = None


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


@dataclass(frozen=True)
class RemoteLifecycleTerminal:
    """One immutable terminal identity observed for Unified State."""

    run_id: str
    kind: str
    candidate_sha: str
    run: bytes
    terminal: bytes
    correction: bytes | None = None
    candidate_available: bool = True


@dataclass(frozen=True)
class RemoteLifecycleReview:
    """One immutable semantic decision bound to a RUN identity."""

    run_id: str
    decision_sha: str
    review: bytes


@dataclass(frozen=True)
class RemoteTaskLifecycle:
    """Allowlisted canonical ref snapshot used by the read-only state reducer."""

    main_sha: str
    terminals: tuple[RemoteLifecycleTerminal, ...]
    reviews: tuple[RemoteLifecycleReview, ...]
    remediation_selectors: tuple[tuple[str, str, str], ...]
    repair_selectors: tuple[tuple[str, str, bytes], ...]
    observed_refs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RemoteRepairAuthorization:
    """The uniquely current immutable authorization for one failed RUN."""

    failed_run_id: str
    ref: str
    commit_sha: str
    revision: int
    repair: bytes
    predecessor_sha: str | None = None
    failure_artifacts_sha: str | None = None


REPAIR_SUPERSESSION_FORMAT = "AIOS_REPAIR_SUPERSESSION"
REPAIR_SUPERSESSION_PATH = ".ai/transport/repair-supersession.json"
REPAIR_SUPERSESSION_PREFIX = "refs/heads/aios/repair-supersession/"


@dataclass(frozen=True)
class RemoteTerminalArtifact:
    """One canonically bound terminal artifact from the upstream snapshot."""

    run_id: str
    task_id: str
    terminal_kind: str
    artifact_sha: str
    run: bytes
    terminal: bytes
    observation: bytes | None = None
    task_revision: int | None = None
    executor: str | None = None
    base_sha: str | None = None
    candidate_sha: str | None = None
    execution_profile: bytes | None = None


@dataclass(frozen=True)
class RemotePerformanceSnapshot:
    """Bounded terminal snapshot selected only by canonical upstream refs."""

    task_selectors: tuple[str, ...]
    terminals: tuple[RemoteTerminalArtifact, ...]
    observed_refs: tuple[tuple[str, str], ...] = ()


PERFORMANCE_MAX_TERMINAL_RUNS = 256


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


def _read_lifecycle_blob(
    repo: Path, remote: str, commit_sha: str, rel_path: str
) -> bytes | None:
    """Read in an observer while distinguishing unavailable transport from absence."""

    fetch_code, _, fetch_error = _git_cmd(
        repo, "fetch", "--no-tags", remote, commit_sha, allow_fail=True
    )
    code, content, _ = _git_cmd(
        repo, "show", f"{commit_sha}:{rel_path}", strip=False, allow_fail=True
    )
    if code == 0:
        return content.encode("utf-8")
    if fetch_code:
        raise RemoteQueryError(
            "failed to acquire canonical lifecycle object",
            category=_classify_remote_failure(fetch_error),
        )
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


def resolve_remote_performance_snapshot(
    repo: Path, *, task_ids: Sequence[str]
) -> RemotePerformanceSnapshot:
    """Acquire one bounded, read-only snapshot of selected terminal namespaces.

    The terminal refs are the sole selection authority.  RUN and terminal identity
    are completely bound before an optional observation sidecar is read, so absent
    telemetry can never mask malformed terminal lineage.
    """

    selectors = _validate_performance_task_ids(task_ids)
    prefixes = {task_id: task_run_prefix(task_id) for task_id in selectors}
    patterns = tuple(
        pattern
        for task_id in selectors
        for pattern in (
            f"refs/heads/aios/artifacts/{prefixes[task_id]}*",
            f"refs/heads/aios/failure-artifacts/{prefixes[task_id]}*",
        )
    )
    remote = resolve_transport_remote(repo)
    refs = _exact_remote_refs(repo, remote, *patterns)
    try:
        records: list[tuple[str, str, str, str, str]] = []
        kinds: dict[str, str] = {}
        for ref, artifact_sha in sorted(refs.items()):
            if ref.startswith("refs/heads/aios/artifacts/"):
                terminal_kind = "RESULT"
                run_id = ref.removeprefix("refs/heads/aios/artifacts/")
            elif ref.startswith("refs/heads/aios/failure-artifacts/"):
                terminal_kind = "FAILURE"
                run_id = ref.removeprefix(
                    "refs/heads/aios/failure-artifacts/"
                )
            else:
                raise ReviewTransportError("canonical ref namespace mismatch")

            matches = [
                task_id
                for task_id, prefix in prefixes.items()
                if re.fullmatch(rf"{re.escape(prefix)}\d{{3,}}", run_id)
            ]
            if len(matches) != 1:
                raise ReviewTransportError(
                    f"canonical RUN ref identity is invalid: {ref}"
                )
            previous = kinds.get(run_id)
            if previous is not None and previous != terminal_kind:
                raise ReviewTransportError(
                    f"competing RESULT/FAILURE terminal identity for RUN: {run_id}"
                )
            kinds[run_id] = terminal_kind
            records.append(
                (run_id, matches[0], terminal_kind, ref, artifact_sha)
            )

        if len(kinds) > PERFORMANCE_MAX_TERMINAL_RUNS:
            raise ReviewTransportError(
                "terminal RUN snapshot exceeds maximum bound of "
                f"{PERFORMANCE_MAX_TERMINAL_RUNS}: {len(kinds)}"
            )

        terminals: list[RemoteTerminalArtifact] = []
        for run_id, task_id, terminal_kind, ref, artifact_sha in records:
            run_bytes = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/run.json"
            )
            if run_bytes is None:
                raise ReviewTransportError(
                    f"canonical {terminal_kind} RUN content is missing for {run_id}"
                )
            terminal_path = (
                ".ai/transport/result.json"
                if terminal_kind == "RESULT"
                else ".ai/transport/failure.json"
            )
            terminal_bytes = _read_lifecycle_blob(
                repo, remote, artifact_sha, terminal_path
            )
            if terminal_bytes is None:
                raise ReviewTransportError(
                    f"canonical {terminal_kind} content is missing for {run_id}"
                )

            revision, executor, base_sha, candidate_sha = (
                _bind_performance_terminal_identity(
                    run_bytes,
                    terminal_bytes,
                    ref=ref,
                    run_id=run_id,
                    task_id=task_id,
                    terminal_kind=terminal_kind,
                )
            )
            # Coverage is classified only after the RUN and terminal family bind.
            observation = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/observation.json"
            )
            execution_profile = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/execution-profile.json"
            )
            terminals.append(
                RemoteTerminalArtifact(
                    run_id=run_id,
                    task_id=task_id,
                    terminal_kind=terminal_kind,
                    artifact_sha=artifact_sha,
                    run=run_bytes,
                    terminal=terminal_bytes,
                    observation=observation,
                    task_revision=revision,
                    executor=executor,
                    base_sha=base_sha,
                    candidate_sha=candidate_sha,
                    execution_profile=execution_profile,
                )
            )
        return RemotePerformanceSnapshot(
            task_selectors=selectors,
            terminals=tuple(sorted(terminals, key=lambda item: item.run_id)),
            observed_refs=tuple(sorted(refs.items())),
        )
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(refs.items()))
        raise


def _validate_performance_task_ids(task_ids: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(task_ids, (list, tuple)) or not task_ids:
        raise ReviewTransportError(
            "task selectors must contain between 1 and 32 exact TASK identities"
        )
    if len(task_ids) > 32:
        raise ReviewTransportError("task selectors exceed maximum bound of 32")
    if any(not isinstance(item, str) for item in task_ids):
        raise ReviewTransportError("malformed TASK selector identity")
    if len(task_ids) != len(set(task_ids)):
        raise ReviewTransportError("task selectors contain duplicate identities")
    for task_id in task_ids:
        if re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task_id) is None:
            raise ReviewTransportError(
                f"malformed TASK selector identity: {task_id!r}"
            )
        if ":" in task_id or "@" in task_id:
            raise ReviewTransportError(
                f"revision-qualified TASK selector is not allowed: {task_id!r}"
            )
    return tuple(sorted(task_ids))


def _bind_performance_terminal_identity(
    run_bytes: bytes,
    terminal_bytes: bytes,
    *,
    ref: str,
    run_id: str,
    task_id: str,
    terminal_kind: str,
) -> tuple[int, str, str, str]:
    """Bind terminal identity to a fully validated admitted RUN."""

    from .artifacts import validate_evidence, validate_result
    from .run import ACTIVE, Run, RunTaskReference

    run_document = _performance_json_mapping(run_bytes, "RUN")
    if run_document.get("kind") == "REMEDIATION":
        execution = run_document.get("execution")
        if not isinstance(execution, Mapping):
            raise ReviewTransportError(
                f"canonical REMEDIATION execution is invalid for {run_id}"
            )
        run_data = execution.get("run")
        if not isinstance(run_data, Mapping):
            raise ReviewTransportError(
                f"canonical REMEDIATION RUN is invalid for {run_id}"
            )
        _bind_remediation_wrapper(run_document, execution, run_id=run_id)
    elif "kind" in run_document:
        raise ReviewTransportError(f"unknown canonical RUN kind at {ref}")
    else:
        run_data = run_document
    task = run_data.get("task") if isinstance(run_data, Mapping) else None
    if not isinstance(task, Mapping) or set(task) != {"id", "revision"}:
        raise ReviewTransportError(f"canonical RUN task is invalid for {run_id}")
    try:
        run = Run(
            run_id=run_data["run_id"],
            task=RunTaskReference(id=task["id"], revision=task["revision"]),
            executor=run_data["executor"],
            base_sha=run_data["base_sha"],
            workspace=run_data["workspace"],
            head_sha=run_data.get("head_sha"),
            status=run_data["status"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewTransportError(
            f"canonical RUN is invalid for {run_id}: {exc}"
        ) from exc
    if run.run_id != run_id or run.task.id != task_id or run.status != ACTIVE:
        raise ReviewTransportError(
            f"canonical RUN identity does not match terminal ref for {run_id}"
        )
    if re.fullmatch(r"[0-9a-f]{40}", run.base_sha) is None or (
        run.head_sha is not None
        and re.fullmatch(r"[0-9a-f]{40}", run.head_sha) is None
    ):
        raise ReviewTransportError(
            f"canonical RUN SHA identity is invalid for {run_id}"
        )

    terminal = _performance_json_mapping(terminal_bytes, terminal_kind)
    if terminal_kind == "RESULT":
        if set(terminal) != {"result", "evidence"}:
            raise ReviewTransportError(
                f"canonical RESULT package is invalid for {run_id}"
            )
        try:
            result = validate_result(terminal["result"])
            raw_evidence = terminal["evidence"]
            if not isinstance(raw_evidence, list):
                raise TypeError("evidence must be a list")
            evidence = tuple(validate_evidence(item) for item in raw_evidence)
        except (KeyError, TypeError, ValueError) as exc:
            raise ReviewTransportError(
                f"canonical RESULT package is invalid for {run_id}: {exc}"
            ) from exc
        _bind_result_identity(run, result, evidence)
        candidate_sha = result.head_sha
    elif terminal_kind == "FAILURE":
        candidate_sha = _bind_failure_identity(terminal, run)
    else:
        raise ReviewTransportError("unsupported terminal family")
    if re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is None:
        raise ReviewTransportError(
            f"canonical {terminal_kind} candidate identity is invalid for {run_id}"
        )
    acceptance = run_document.get("acceptance")
    if acceptance is not None and (
        not isinstance(acceptance, Mapping)
        or dict(acceptance)
        != {"mode": "DIRECT_CANDIDATE", "candidate_head": candidate_sha}
    ):
        raise ReviewTransportError(
            f"canonical terminal candidate binding conflicts for {run_id}"
        )
    return run.task.revision, run.executor, run.base_sha, candidate_sha


def _bind_remediation_wrapper(
    document: Mapping[str, Any], execution: Mapping[str, Any], *, run_id: str
) -> None:
    from .review import REMEDIATION_ACTIONS, parse_remediation

    required = {"kind", "execution"}
    allowed = required | {"predecessor", "execution_base", "acceptance"}
    if not required.issubset(document) or set(document).difference(allowed):
        raise ReviewTransportError(
            f"canonical REMEDIATION wrapper is invalid for {run_id}"
        )
    finding = execution.get("finding")
    remediation_data = execution.get("remediation")
    run_data = execution.get("run")
    review_id = execution.get("review_id")
    original_constraints = execution.get("original_constraints", ())
    if (
        not isinstance(review_id, str)
        or not review_id
        or not isinstance(finding, Mapping)
        or set(finding) != {
            "id", "basis", "action", "location", "issue", "expected"
        }
        or not all(isinstance(value, str) and value for value in finding.values())
        or finding.get("action") not in REMEDIATION_ACTIONS
        or not isinstance(remediation_data, Mapping)
        or not isinstance(run_data, Mapping)
        or not isinstance(original_constraints, (list, tuple))
        or not all(
            isinstance(value, str) and value for value in original_constraints
        )
    ):
        raise ReviewTransportError(
            f"canonical REMEDIATION execution is invalid for {run_id}"
        )
    try:
        remediation = parse_remediation(json.dumps(remediation_data))
    except (TypeError, ValueError) as exc:
        raise ReviewTransportError(
            f"canonical REMEDIATION contract is invalid for {run_id}: {exc}"
        ) from exc
    if (
        remediation.finding_id != finding.get("id")
        or remediation.action != finding.get("action")
    ):
        raise ReviewTransportError(
            f"canonical REMEDIATION finding binding conflicts for {run_id}"
        )
    predecessor = document.get("predecessor")
    if predecessor is not None:
        if (
            not isinstance(predecessor, Mapping)
            or set(predecessor) != {
                "source_run_id", "review_id", "finding_id", "reviewed_sha"
            }
            or not isinstance(predecessor.get("source_run_id"), str)
            or re.fullmatch(
                r"RUN-[A-Za-z0-9_-]+-\d{3,}", predecessor["source_run_id"]
            )
            is None
        ):
            raise ReviewTransportError(
                f"canonical REMEDIATION predecessor is invalid for {run_id}"
            )
        expected = (
            review_id,
            finding.get("id"),
            remediation.reviewed_sha,
        )
        actual = (
            predecessor.get("review_id"),
            predecessor.get("finding_id"),
            predecessor.get("reviewed_sha"),
        )
        if expected != actual:
            raise ReviewTransportError(
                f"canonical REMEDIATION predecessor conflicts for {run_id}"
            )
    execution_base = document.get("execution_base")
    if execution_base is not None:
        if (
            predecessor is None
            or not isinstance(execution_base, Mapping)
            or set(execution_base) != {"run_id", "candidate_sha"}
            or not isinstance(run_data, Mapping)
            or execution_base.get("candidate_sha") != run_data.get("base_sha")
            or not isinstance(execution_base.get("run_id"), str)
            or re.fullmatch(
                r"RUN-[A-Za-z0-9_-]+-\d{3,}", execution_base["run_id"]
            )
            is None
        ):
            raise ReviewTransportError(
                f"canonical REMEDIATION execution base conflicts for {run_id}"
            )
    elif run_data.get("base_sha") != remediation.reviewed_sha:
        raise ReviewTransportError(
            f"canonical REMEDIATION base conflicts for {run_id}"
        )


def _bind_result_identity(run: Any, result: Any, evidence: Sequence[Any]) -> None:
    if run.head_sha is not None and run.head_sha != result.head_sha:
        raise ReviewTransportError("canonical RESULT head conflicts with RUN")
    by_id: dict[str, Any] = {}
    for item in evidence:
        if item.evidence_id in by_id:
            raise ReviewTransportError("canonical RESULT evidence identity is duplicated")
        by_id[item.evidence_id] = item
        if item.run_id != run.run_id or item.subject_sha != result.head_sha:
            raise ReviewTransportError(
                "canonical RESULT evidence does not bind to RUN and candidate"
            )
    for claim in result.claims:
        if set(claim.evidence).difference(by_id):
            raise ReviewTransportError(
                "canonical RESULT claim references missing evidence"
            )


def _bind_failure_identity(terminal: Mapping[str, Any], run: Any) -> str:
    task = terminal.get("task")
    if (
        terminal.get("kind") != "FAILURE"
        or terminal.get("run_id") != run.run_id
        or not isinstance(task, Mapping)
        or dict(task) != {"id": run.task.id, "revision": run.task.revision}
        or terminal.get("executor") != run.executor
        or terminal.get("base_sha") != run.base_sha
    ):
        raise ReviewTransportError(
            f"canonical FAILURE identity does not match RUN for {run.run_id}"
        )
    candidate = terminal.get("candidate")
    if not isinstance(candidate, Mapping) or set(candidate) != {
        "transportable",
        "repairable",
        "dirty",
        "descends_from_base",
        "changed_files",
        "outside_task_scope",
    }:
        raise ReviewTransportError(
            f"canonical FAILURE candidate is invalid for {run.run_id}"
        )
    flags = tuple(
        candidate[name]
        for name in (
            "transportable", "repairable", "dirty", "descends_from_base"
        )
    )
    if not all(isinstance(value, bool) for value in flags):
        raise ReviewTransportError(
            f"canonical FAILURE candidate flags are invalid for {run.run_id}"
        )
    paths: dict[str, list[str]] = {}
    for name in ("changed_files", "outside_task_scope"):
        value = candidate[name]
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) and item for item in value)
            or len(value) != len(set(value))
        ):
            raise ReviewTransportError(
                f"canonical FAILURE candidate paths are invalid for {run.run_id}"
            )
        paths[name] = value
    if not set(paths["outside_task_scope"]).issubset(paths["changed_files"]):
        raise ReviewTransportError(
            f"canonical FAILURE candidate scope conflicts for {run.run_id}"
        )
    repairable = not candidate["dirty"] and candidate["descends_from_base"]
    transportable = repairable and not paths["outside_task_scope"]
    if candidate["repairable"] is not repairable or candidate[
        "transportable"
    ] is not transportable:
        raise ReviewTransportError(
            f"canonical FAILURE candidate binding conflicts for {run.run_id}"
        )
    failed_head = terminal.get("failed_head_sha")
    if not isinstance(failed_head, str):
        raise ReviewTransportError(
            f"canonical FAILURE failed-head identity is invalid for {run.run_id}"
        )
    continuation = terminal.get("continuation_of")
    if continuation is not None and (
        not isinstance(continuation, str)
        or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", continuation) is None
    ):
        raise ReviewTransportError(
            f"canonical FAILURE continuation identity is invalid for {run.run_id}"
        )
    return failed_head


def _performance_json_mapping(content: bytes, name: str) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"), object_pairs_hook=pairs
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReviewTransportError(f"invalid canonical {name} JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ReviewTransportError(f"canonical {name} must be a mapping")
    return value


def resolve_remote_task_lifecycle(
    repo: Path, *, task_id: str, task_revision: int
) -> RemoteTaskLifecycle:
    """Acquire one bounded canonical snapshot for Unified State.

    The caller supplies an isolated observation repository.  This function never
    updates a ref and derives no ordering from ref or filesystem timestamps.
    """

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
        "refs/heads/main",
        f"refs/heads/aios/failure/{task_prefix}*",
        f"refs/heads/aios/failure-artifacts/{task_prefix}*",
        f"refs/heads/aios/artifacts/{task_prefix}*",
        f"refs/heads/aios/review/{task_prefix}*",
        f"refs/heads/aios/review-decision/{task_prefix}*",
        f"refs/heads/aios/remediation/{task_prefix}*",
        f"refs/heads/aios/repair/{task_prefix}*",
        f"{REPAIR_SUPERSESSION_PREFIX}{task_prefix}*/*",
    )
    try:
        main_ref = "refs/heads/main"
        main_sha = refs.get(main_ref)
        if main_sha is None:
            raise RemoteQueryError("canonical main ref is missing")
        main_fetch, _, main_error = _git_cmd(
            repo, "fetch", "--no-tags", remote, main_sha, allow_fail=True
        )
        if main_fetch:
            raise RemoteQueryError(
                "failed to acquire canonical main",
                category=_classify_remote_failure(main_error),
            )
        terminals: list[RemoteLifecycleTerminal] = []
        for ref, artifact_sha in sorted(refs.items()):
            if ref.startswith("refs/heads/aios/failure-artifacts/"):
                kind = "FAILURE"
                candidate_ref = "refs/heads/aios/failure/" + ref.rsplit("/", 1)[-1]
                terminal_path = ".ai/transport/failure.json"
            elif ref.startswith("refs/heads/aios/artifacts/"):
                kind = "RESULT"
                candidate_ref = "refs/heads/aios/review/" + ref.rsplit("/", 1)[-1]
                terminal_path = ".ai/transport/result.json"
            else:
                continue
            run_id = ref.rsplit("/", 1)[-1]
            run = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/run.json"
            )
            if run is None:
                raise ReviewTransportError(
                    f"canonical {kind} RUN content is missing for {run_id}"
                )
            bound_task, bound_revision, bound_run = _decode_run_task_identity(run, ref)
            if bound_task != task_id or bound_run != run_id:
                raise ReviewTransportError(
                    f"canonical terminal identity does not match TASK: {run_id}"
                )
            if bound_revision != task_revision:
                continue
            terminal = _read_lifecycle_blob(repo, remote, artifact_sha, terminal_path)
            correction = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/repair.json"
            )
            if terminal is None:
                raise ReviewTransportError(
                    f"canonical {kind} content is missing for {run_id}"
                )
            candidate_sha = refs.get(candidate_ref)
            candidate_available = candidate_sha is not None
            if candidate_sha is None:
                if kind != "FAILURE":
                    raise ReviewTransportError(
                        f"canonical {kind} candidate ref is missing for {run_id}"
                    )
                failure_data = _json_mapping(terminal, "FAILURE")
                candidate_sha = failure_data.get("failed_head_sha")
                if not isinstance(candidate_sha, str) or not candidate_sha:
                    raise ReviewTransportError(
                        f"canonical FAILURE has no failed head for {run_id}"
                    )
            else:
                candidate_fetch, _, candidate_error = _git_cmd(
                    repo, "fetch", "--no-tags", remote, candidate_sha, allow_fail=True
                )
                if candidate_fetch:
                    raise RemoteQueryError(
                        f"failed to acquire canonical candidate for {run_id}",
                        category=_classify_remote_failure(candidate_error),
                    )
            terminals.append(
                RemoteLifecycleTerminal(
                    run_id, kind, candidate_sha, run, terminal, correction,
                    candidate_available,
                )
            )

        reviews: list[RemoteLifecycleReview] = []
        decision_prefix = "refs/heads/aios/review-decision/"
        current_run_ids = {item.run_id for item in terminals}
        for ref, decision_sha in sorted(refs.items()):
            if not ref.startswith(decision_prefix):
                continue
            run_id = ref[len(decision_prefix) :]
            if run_id not in current_run_ids:
                continue
            decision_fetch, _, decision_error = _git_cmd(
                repo, "fetch", "--no-tags", remote, decision_sha, allow_fail=True
            )
            if decision_fetch:
                raise RemoteQueryError(
                    "failed to acquire canonical review decision",
                    category=_classify_remote_failure(decision_error),
                )
            code, tree, _ = _git_cmd(
                repo,
                "ls-tree",
                "-r",
                "--name-only",
                decision_sha,
                "--",
                ".ai/reviews",
                allow_fail=True,
            )
            paths = [
                path
                for path in tree.splitlines()
                if path.startswith(".ai/reviews/")
                and path.endswith((".yaml", ".yml"))
            ]
            if code or len(paths) != 1:
                raise ReviewTransportError(
                    f"canonical review decision is missing or ambiguous for {run_id}"
                )
            review = _read_lifecycle_blob(repo, remote, decision_sha, paths[0])
            if review is None:
                raise ReviewTransportError(
                    f"canonical review decision content is missing for {run_id}"
                )
            reviews.append(RemoteLifecycleReview(run_id, decision_sha, review))

        remediation_selectors: list[tuple[str, str, str]] = []
        remediation_prefix = "refs/heads/aios/remediation/"
        run_pattern = re.compile(
            rf"^({re.escape(task_prefix)}\d{{3,}})-(.+)$"
        )
        repair_candidates: dict[str, list[RemoteRepairAuthorization]] = {}
        for ref, sha in sorted(refs.items()):
            if ref.startswith(remediation_prefix):
                match = run_pattern.fullmatch(ref[len(remediation_prefix) :])
                if match is None:
                    raise ReviewTransportError(
                        "canonical REMEDIATION selector identity is malformed"
                    )
                remediation_selectors.append((match.group(1), match.group(2), sha))
            elif ref.startswith("refs/heads/aios/repair/"):
                run_id = ref.rsplit("/", 1)[-1]
                if not re.fullmatch(rf"{re.escape(task_prefix)}\d{{3,}}", run_id):
                    raise ReviewTransportError(
                        "canonical REPAIR selector identity is malformed"
                    )
                repair = _read_lifecycle_blob(
                    repo, remote, sha, ".ai/transport/repair.json"
                )
                if repair is None:
                    raise ReviewTransportError(
                        f"canonical REPAIR content is missing for {run_id}"
                    )
                repair_candidates.setdefault(run_id, []).append(
                    RemoteRepairAuthorization(run_id, ref, sha, 1, repair)
                )
            elif ref.startswith(REPAIR_SUPERSESSION_PREFIX):
                suffix = ref[len(REPAIR_SUPERSESSION_PREFIX) :]
                parts = suffix.split("/")
                if (
                    len(parts) != 2
                    or not re.fullmatch(rf"{re.escape(task_prefix)}\d{{3,}}", parts[0])
                    or not parts[1].isdigit()
                ):
                    raise ReviewTransportError(
                        "canonical REPAIR supersession selector identity is malformed"
                    )
                run_id = parts[0]
                revision = int(parts[1])
                repair = _read_lifecycle_blob(
                    repo, remote, sha, ".ai/transport/repair.json"
                )
                metadata = _read_lifecycle_blob(
                    repo, remote, sha, REPAIR_SUPERSESSION_PATH
                )
                if repair is None or metadata is None:
                    raise ReviewTransportError(
                        f"canonical REPAIR supersession content is missing for {run_id}"
                    )
                predecessor, failure_sha = _decode_repair_supersession(
                    metadata, run_id=run_id, revision=revision
                )
                parent_code, parent_sha, _ = _git_cmd(
                    repo, "rev-parse", f"{sha}^", allow_fail=True
                )
                if parent_code or parent_sha != predecessor:
                    raise ReviewTransportError(
                        "canonical REPAIR successor commit parent is invalid"
                    )
                repair_candidates.setdefault(run_id, []).append(
                    RemoteRepairAuthorization(
                        run_id, ref, sha, revision, repair, predecessor, failure_sha
                    )
                )
        repair_selectors: list[tuple[str, str, bytes]] = []
        for run_id, candidates in sorted(repair_candidates.items()):
            failure_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
            selected = _select_current_repair_authorization(
                candidates, failure_artifacts_sha=refs.get(failure_ref)
            )
            repair_selectors.append((run_id, selected.commit_sha, selected.repair))
        return RemoteTaskLifecycle(
            main_sha=main_sha,
            terminals=tuple(terminals),
            reviews=tuple(reviews),
            remediation_selectors=tuple(remediation_selectors),
            repair_selectors=tuple(repair_selectors),
            observed_refs=tuple(sorted(refs.items())),
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

    observed_refs: dict[str, str] = {}
    try:
        observed_lines: list[tuple[str, str]] = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) != 2:
                raise ReviewTransportError("malformed canonical REMEDIATION ref result")
            commit_sha, ref = parts
            observed_refs[ref] = commit_sha
            observed_lines.append((commit_sha, ref))

        resolved: list[RemoteRemediationLineage] = []
        for commit_sha, ref in observed_lines:
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
                        "canonical source RUN has conflicting terminal artifacts: "
                        f"{source_run_id}"
                    )
            artifacts_code, artifacts_output, _ = _git_cmd(
                repo, "ls-remote", "--refs", remote, artifacts_ref, allow_fail=True
            )
            artifact_lines = [item.split() for item in artifacts_output.splitlines()]
            if (
                artifacts_code
                or len(artifact_lines) != 1
                or len(artifact_lines[0]) != 2
            ):
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
                    f"canonical RUN id mismatch at {ref}: expected {source_run_id}, "
                    f"got {run_id}"
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
                path
                for path in tree_output.splitlines()
                if path.startswith(".ai/reviews/")
                and path.endswith((".yaml", ".yml"))
            ]
            remediation_paths = [
                path
                for path in tree_output.splitlines()
                if path.startswith(".ai/remediations/")
                and path.endswith((".yaml", ".yml"))
            ]
            if len(review_paths) != 1 or len(remediation_paths) != 1:
                raise ReviewTransportError(
                    f"canonical lineage at {ref} must contain exactly one REVIEW and "
                    "REMEDIATION"
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
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(observed_refs.items()))
        raise


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
        execution_profile = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/execution-profile.json"
        )
        if run is None or failure is None:
            raise ReviewTransportError(
                f"canonical failed RUN content missing for {run_id}"
            )
        artifact = RemoteFailureArtifacts(
            run_id,
            refs[candidate_ref],
            run,
            failure,
            repair,
            preverification,
            execution_profile=execution_profile,
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
    execution_profile_path: Path | None = None,
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

    execution_profile_entry = ""
    if execution_profile_path is not None:
        if not execution_profile_path.is_file():
            raise ReviewTransportError(
                f"persisted execution profile JSON missing: {execution_profile_path}"
            )
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=execution_profile_path.read_bytes(), capture_output=True, check=True,
            )
            execution_profile_sha = proc.stdout.decode("utf-8", errors="strict").strip()
            execution_profile_entry = (
                f"100644 blob {execution_profile_sha}\texecution-profile.json\n"
            )
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(
                f"failed to hash execution-profile.json: {exc}"
            ) from exc

    tree_entries = [
        f"100644 blob {run_blob_sha}\trun.json\n",
        f"100644 blob {result_blob_sha}\tresult.json\n",
    ]
    if lineage_entry:
        tree_entries.append(lineage_entry)
    if observation_entry:
        tree_entries.append(observation_entry)
    if execution_profile_entry:
        tree_entries.append(execution_profile_entry)
    tree_entries.sort(key=lambda line: line.split("\t")[1])
    tree_input = "".join(tree_entries)
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
    execution_profile_path: Path | None = None,
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
    if execution_profile_path is not None:
        if not execution_profile_path.is_file():
            raise ReviewTransportError(
                f"persisted execution profile JSON missing: {execution_profile_path}"
            )
        inputs.append(("execution-profile.json", execution_profile_path))
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
    execution_profile_path: Path | None = None,
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
    if execution_profile_path is None:
        default_profile_path = (
            run_path.parent.parent / "execution-profiles" / f"{run_id}.json"
        )
        if default_profile_path.is_file():
            execution_profile_path = default_profile_path
    expected_execution_profile = (
        execution_profile_path.read_bytes()
        if execution_profile_path is not None and execution_profile_path.is_file()
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
    terminal_artifact_sha = refs.get(artifacts_ref)
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
        remote_execution_profile = _read_remote_blob(
            repo, remote, refs[artifacts_ref], ".ai/transport/execution-profile.json"
        )
        if remote_execution_profile != expected_execution_profile:
            raise ReviewTransportError(
                f"remote failure artifacts ref {artifacts_ref} exists with different execution profile content"
            )
    else:
        commit = _create_named_artifacts_commit(
            repo, run_path=run_path, artifact_path=failure_path,
            artifact_name="failure.json", run_id=run_id,
            lineage_path=lineage_path,
            observation_path=observation_path,
            preverification_path=preverification_path,
            execution_profile_path=execution_profile_path,
        )
        terminal_artifact_sha = commit
        specs.append(f"{commit}:{artifacts_ref}")
    if specs:
        code, _, stderr = _git_cmd(repo, "push", "--no-tags", remote, *specs, allow_fail=True)
        if code:
            raise ReviewTransportError(f"failed to push transport refs to {remote}: {stderr}")
    assert terminal_artifact_sha is not None
    try:
        publish_terminal_attention(
            repo,
            remote=remote,
            run_id=run_id,
            terminal_kind="FAILURE",
            artifact_sha=terminal_artifact_sha,
        )
    except TerminalAttentionError as exc:
        raise ReviewTransportError(
            f"terminal FAILURE is canonical but attention delivery failed: {exc}"
        ) from exc


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


def _decode_repair_supersession(
    content: bytes, *, run_id: str, revision: int
) -> tuple[str, str]:
    metadata = _json_mapping(content, "REPAIR supersession")
    required = {
        "format",
        "version",
        "failed_run_id",
        "authorization_revision",
        "predecessor_repair_sha",
        "failure_artifacts_sha",
    }
    if set(metadata) != required:
        raise ReviewTransportError("REPAIR supersession fields are invalid")
    predecessor = metadata.get("predecessor_repair_sha")
    failure_sha = metadata.get("failure_artifacts_sha")
    if (
        metadata.get("format") != REPAIR_SUPERSESSION_FORMAT
        or metadata.get("version") != 1
        or metadata.get("failed_run_id") != run_id
        or metadata.get("authorization_revision") != revision
        or revision < 2
        or not isinstance(predecessor, str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", predecessor) is None
        or not isinstance(failure_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", failure_sha) is None
    ):
        raise ReviewTransportError("REPAIR supersession identity is invalid")
    return predecessor, failure_sha


def _select_current_repair_authorization(
    candidates: Sequence[RemoteRepairAuthorization],
    *,
    failure_artifacts_sha: str | None,
) -> RemoteRepairAuthorization:
    """Select one contiguous immutable authorization chain or fail closed."""

    roots = [item for item in candidates if item.revision == 1]
    if len(roots) != 1:
        raise ReviewTransportError("canonical REPAIR root is missing or ambiguous")
    by_revision: dict[int, list[RemoteRepairAuthorization]] = {}
    for item in candidates:
        by_revision.setdefault(item.revision, []).append(item)
    if any(len(items) != 1 for items in by_revision.values()):
        raise ReviewTransportError("canonical REPAIR successor is ambiguous")
    revisions = sorted(by_revision)
    if revisions != list(range(1, revisions[-1] + 1)):
        raise ReviewTransportError("canonical REPAIR revision chain is discontinuous")
    current = roots[0]
    root_identity = _json_mapping(current.repair, "REPAIR authorization")
    root_task = root_identity.get("task")
    if (
        root_identity.get("failed_run_id") != current.failed_run_id
        or not isinstance(root_identity.get("failed_head_sha"), str)
        or not isinstance(root_task, Mapping)
        or not isinstance(root_task.get("id"), str)
        or isinstance(root_task.get("revision"), bool)
        or not isinstance(root_task.get("revision"), int)
    ):
        raise ReviewTransportError("canonical REPAIR root identity is invalid")
    for revision in revisions[1:]:
        successor = by_revision[revision][0]
        if successor.predecessor_sha != current.commit_sha:
            raise ReviewTransportError("canonical REPAIR predecessor chain is broken")
        if (
            failure_artifacts_sha is None
            or successor.failure_artifacts_sha != failure_artifacts_sha
        ):
            raise ReviewTransportError("canonical REPAIR FAILURE identity is stale")
        identity = _json_mapping(successor.repair, "REPAIR authorization")
        for key in ("failed_run_id", "failed_head_sha", "task"):
            if identity.get(key) != root_identity.get(key):
                raise ReviewTransportError(
                    "canonical REPAIR supersession changes failed RUN identity"
                )
        current = successor
    return current


def resolve_remote_repair_authorization(
    repo: Path, run_id: str, *, remote: str | None = None
) -> RemoteRepairAuthorization:
    """Resolve the unique current REPAIR while preserving every immutable ref."""

    if re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", run_id) is None:
        raise ReviewTransportError(f"invalid failed RUN id: {run_id!r}")
    if remote is None:
        remote = resolve_transport_remote(repo)
    legacy_ref = f"refs/heads/aios/repair/{run_id}"
    successor_pattern = f"{REPAIR_SUPERSESSION_PREFIX}{run_id}/*"
    failure_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    refs = _exact_remote_refs(repo, remote, legacy_ref, successor_pattern, failure_ref)
    legacy_sha = refs.get(legacy_ref)
    if legacy_sha is None:
        raise ReviewTransportError(f"remote REPAIR not found for {run_id}")
    repair = _read_remote_blob(
        repo, remote, legacy_sha, ".ai/transport/repair.json"
    )
    if repair is None:
        raise ReviewTransportError(f"remote REPAIR JSON missing for {run_id}")
    candidates = [
        RemoteRepairAuthorization(run_id, legacy_ref, legacy_sha, 1, repair)
    ]
    prefix = f"{REPAIR_SUPERSESSION_PREFIX}{run_id}/"
    for ref, sha in sorted(refs.items()):
        if not ref.startswith(prefix):
            continue
        suffix = ref[len(prefix) :]
        if not suffix.isdigit():
            raise ReviewTransportError("remote REPAIR supersession is ambiguous")
        revision = int(suffix)
        successor_repair = _read_remote_blob(
            repo, remote, sha, ".ai/transport/repair.json"
        )
        metadata = _read_remote_blob(
            repo, remote, sha, REPAIR_SUPERSESSION_PATH
        )
        if successor_repair is None or metadata is None:
            raise ReviewTransportError("remote REPAIR supersession content is missing")
        predecessor, failure_sha = _decode_repair_supersession(
            metadata, run_id=run_id, revision=revision
        )
        parent_code, parent_sha, _ = _git_cmd(
            repo, "rev-parse", f"{sha}^", allow_fail=True
        )
        if parent_code or parent_sha != predecessor:
            raise ReviewTransportError(
                "remote REPAIR successor commit parent is invalid"
            )
        candidates.append(
            RemoteRepairAuthorization(
                run_id, ref, sha, revision, successor_repair,
                predecessor, failure_sha,
            )
        )
    return _select_current_repair_authorization(
        candidates, failure_artifacts_sha=refs.get(failure_ref)
    )


def read_remote_repair(repo: Path, run_id: str, *, remote: str | None = None) -> bytes:
    """Read the deterministically current ChatGPT-authored REPAIR."""

    return resolve_remote_repair_authorization(repo, run_id, remote=remote).repair


def transport_post_pass(
    repo: Path,
    *,
    run_id: str,
    head_sha: str,
    run_path: Path,
    result_path: Path,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
    execution_profile_path: Path | None = None,
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
    if execution_profile_path is None:
        default_profile_path = (
            run_path.parent.parent / "execution-profiles" / f"{run_id}.json"
        )
        if default_profile_path.is_file():
            execution_profile_path = default_profile_path
    expected_execution_profile_bytes = (
        execution_profile_path.read_bytes()
        if execution_profile_path is not None and execution_profile_path.is_file()
        else None
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

        remote_execution_profile_bytes = _read_remote_blob(
            repo, remote, existing_artifacts_sha, ".ai/transport/execution-profile.json"
        )

        if (
            remote_run_bytes == expected_run_bytes
            and remote_result_bytes == expected_result_bytes
            and remote_lineage_bytes == expected_lineage_bytes
            and (
                expected_observation_bytes is None
                or remote_observation_bytes in (None, expected_observation_bytes)
            )
            and remote_execution_profile_bytes == expected_execution_profile_bytes
        ):
            push_artifacts = False
        else:
            raise ReviewTransportError(
                f"remote artifacts ref {artifacts_ref} exists with different artifact content"
            )

    artifacts_commit_sha = existing_remote_refs.get(artifacts_ref)
    if push_artifacts:
        artifacts_commit_sha = _create_artifacts_commit(
            repo,
            run_path=run_path,
            result_path=result_path,
            run_id=run_id,
            lineage_path=lineage_path,
            observation_path=observation_path,
            execution_profile_path=execution_profile_path,
        )

    push_specs: list[str] = []
    if push_review:
        push_specs.append(f"{head_sha}:{review_ref}")
    if push_artifacts:
        assert artifacts_commit_sha is not None
        push_specs.append(f"{artifacts_commit_sha}:{artifacts_ref}")

    if push_specs:
        code, _, stderr = _git_cmd(repo, "push", "--no-tags", remote, *push_specs, allow_fail=True)
        if code != 0:
            raise ReviewTransportError(f"failed to push transport refs to {remote}: {stderr}")
    assert artifacts_commit_sha is not None
    try:
        publish_terminal_attention(
            repo,
            remote=remote,
            run_id=run_id,
            terminal_kind="RESULT",
            artifact_sha=artifacts_commit_sha,
        )
    except TerminalAttentionError as exc:
        raise ReviewTransportError(
            f"terminal RESULT is canonical but attention delivery failed: {exc}"
        ) from exc
