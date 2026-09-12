"""Generic Brain-to-repository authoring ingress for AIOS-renew."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .artifacts import (
    ArtifactValidationError,
    Evidence,
    Result,
    ResultPackage,
    validate_evidence,
    validate_result,
    validate_result_package,
)
from .publication import (
    _parse_remediation_run,
    _validate_remediation_package,
    _validate_repair_authorization,
)
from .review import (
    Finding,
    Remediation,
    Review,
    ReviewValidationError,
    parse_remediation,
    parse_review,
    validate_remediation,
    validate_review,
)
from .review_transport import ReviewTransportError, resolve_transport_remote
from .run import (
    ACTIVE,
    Run,
    RunTaskReference,
    RunValidationError,
    SUPPORTED_EXECUTORS,
)
from .task import Task, TaskValidationError, parse_task
from .unified_state import observe_unified_state


class AuthoringIngressError(ValueError):
    """Raised when an ingress envelope or semantic payload fails closed."""


_ALLOWED_OPERATIONS = frozenset(
    {"AUTHOR_TASK", "SUBMIT_REVIEW", "AUTHOR_REMEDIATION", "AUTHOR_REPAIR"}
)

_ALLOWED_ENVELOPE_FORMATS = frozenset(
    {"AIOS_INGRESS_ENVELOPE", "AIOS_AUTHORING_INGRESS"}
)

_PROHIBITED_FIELD_NAMES = frozenset(
    {
        "destination",
        "destination_path",
        "destination_ref",
        "dest",
        "target_path",
        "target_ref",
        "path",
        "ref",
        "refs",
        "branch",
        "file_path",
        "out_path",
        "output_path",
        "target_file",
        "command",
        "cmd",
        "shell",
        "exec",
        "git_command",
        "script",
        "credentials",
        "token",
        "auth",
        "author_override",
        "committer_override",
        "force",
        "override",
        "bypass",
    }
)

_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "format",
        "version",
        "operation",
        "identity",
        "expected_state",
        "payload",
        "task_id",
        "run_id",
        "source_run_id",
        "finding_id",
        "failed_run_id",
    }
)


@dataclass(frozen=True)
class IngressEnvelope:
    """Bounded, carrier-neutral ingress envelope."""

    format: str
    version: int
    operation: str
    identity: Mapping[str, Any]
    expected_state: Mapping[str, Any]
    payload: str | Mapping[str, Any]


@dataclass(frozen=True)
class IngressResult:
    """Attributable, deterministic outcome of one ingress operation."""

    format: str = "AIOS_INGRESS_RESULT"
    version: int = 1
    operation: str = ""
    status: str = "CANONICALIZED"
    canonical_destination: str = ""
    canonical_sha: str = ""
    replayed: bool = False
    detail: str = ""

    def render(self) -> str:
        return (
            "AIOS INGRESS PASS\n"
            f"operation: {self.operation}\n"
            f"status: {self.status}\n"
            f"destination: {self.canonical_destination}\n"
            f"sha: {self.canonical_sha}\n"
            f"replayed: {str(self.replayed).lower()}\n"
            f"detail: {self.detail}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "operation": self.operation,
            "status": self.status,
            "canonical_destination": self.canonical_destination,
            "canonical_sha": self.canonical_sha,
            "replayed": self.replayed,
            "detail": self.detail,
        }


def parse_envelope(raw: str | bytes | Mapping[str, Any]) -> IngressEnvelope:
    """Parse and structurally validate one ingress envelope."""

    if isinstance(raw, (str, bytes)):
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise AuthoringIngressError(f"invalid ingress envelope YAML/JSON: {exc}") from exc
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        raise AuthoringIngressError("ingress envelope must be a mapping or YAML/JSON string")

    if not isinstance(data, Mapping):
        raise AuthoringIngressError("ingress envelope must be a mapping")

    # Check for forbidden fields in the entire structure
    _scan_for_prohibited_fields(data)

    unknown = set(data) - _ALLOWED_TOP_LEVEL_KEYS
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise AuthoringIngressError(f"envelope contains unknown field(s): {fields}")

    envelope_format = data.get("format")
    if not isinstance(envelope_format, str) or envelope_format not in _ALLOWED_ENVELOPE_FORMATS:
        raise AuthoringIngressError(
            f"invalid envelope format: expected one of {sorted(_ALLOWED_ENVELOPE_FORMATS)}, got {envelope_format!r}"
        )

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise AuthoringIngressError(f"invalid envelope version: expected 1, got {version!r}")

    operation = data.get("operation")
    if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
        raise AuthoringIngressError(
            f"invalid envelope operation: expected one of {sorted(_ALLOWED_OPERATIONS)}, got {operation!r}"
        )

    identity_data = data.get("identity")
    if identity_data is not None:
        if not isinstance(identity_data, Mapping):
            raise AuthoringIngressError("identity must be a mapping")
        identity = dict(identity_data)
    else:
        identity = {}

    # Extract top-level identity fields if present
    for key in ("task_id", "run_id", "source_run_id", "finding_id", "failed_run_id"):
        if key in data and key not in identity:
            identity[key] = data[key]

    expected_state = data.get("expected_state")
    if not isinstance(expected_state, Mapping):
        raise AuthoringIngressError("expected_state is required and must be a mapping")

    payload = data.get("payload")
    if payload is None or (isinstance(payload, str) and not payload.strip()):
        raise AuthoringIngressError("payload is required and must not be empty")
    if not isinstance(payload, (str, Mapping)):
        raise AuthoringIngressError("payload must be a string or mapping")

    _validate_operation_identity(operation, identity)

    return IngressEnvelope(
        format=envelope_format,
        version=version,
        operation=operation,
        identity=identity,
        expected_state=dict(expected_state),
        payload=payload,
    )


def _scan_for_prohibited_fields(obj: Any, path: str = "") -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if not isinstance(key, str):
                continue
            key_lower = key.lower()
            if key_lower in _PROHIBITED_FIELD_NAMES:
                raise AuthoringIngressError(
                    f"prohibited field detected: {key!r} (arbitrary destinations, commands, and authority overrides are forbidden)"
                )
            _scan_for_prohibited_fields(value, f"{path}.{key}" if path else key)
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            _scan_for_prohibited_fields(item, f"{path}[{index}]")


def _validate_operation_identity(operation: str, identity: Mapping[str, Any]) -> None:
    if operation == "AUTHOR_TASK":
        task_id = identity.get("task_id")
        if not isinstance(task_id, str) or not re.fullmatch(r"^TASK-[A-Za-z0-9_-]+$", task_id):
            raise AuthoringIngressError(f"AUTHOR_TASK requires valid task_id, got {task_id!r}")
    elif operation == "SUBMIT_REVIEW":
        run_id = identity.get("run_id")
        if not isinstance(run_id, str) or not re.fullmatch(r"^RUN-[A-Za-z0-9_-]+$", run_id):
            raise AuthoringIngressError(f"SUBMIT_REVIEW requires valid run_id, got {run_id!r}")
    elif operation == "AUTHOR_REMEDIATION":
        source_run_id = identity.get("source_run_id")
        finding_id = identity.get("finding_id")
        if not isinstance(source_run_id, str) or not re.fullmatch(r"^RUN-[A-Za-z0-9_-]+$", source_run_id):
            raise AuthoringIngressError(f"AUTHOR_REMEDIATION requires valid source_run_id, got {source_run_id!r}")
        if not isinstance(finding_id, str) or not finding_id.strip():
            raise AuthoringIngressError(f"AUTHOR_REMEDIATION requires valid finding_id, got {finding_id!r}")
    elif operation == "AUTHOR_REPAIR":
        failed_run_id = identity.get("failed_run_id")
        if not isinstance(failed_run_id, str) or not re.fullmatch(r"^RUN-[A-Za-z0-9_-]+$", failed_run_id):
            raise AuthoringIngressError(f"AUTHOR_REPAIR requires valid failed_run_id, got {failed_run_id!r}")


def read_carrier_input(
    source: str | Path | None = None,
    *,
    stdin_bytes: bytes | None = None,
) -> IngressEnvelope:
    """Read carrier delivery from a file or standard input and return the parsed envelope."""

    if source is not None and source != "-":
        path = Path(source)
        if not path.is_file():
            raise AuthoringIngressError(f"envelope file not found: {source}")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AuthoringIngressError(f"cannot read envelope file: {exc}") from exc
    else:
        if stdin_bytes is not None:
            content = stdin_bytes.decode("utf-8", errors="strict")
        else:
            if source is None and sys.stdin.isatty():
                raise AuthoringIngressError("no input envelope provided via file or stdin")
            try:
                content = sys.stdin.read()
            except (OSError, UnicodeError) as exc:
                raise AuthoringIngressError(f"cannot read envelope from stdin: {exc}") from exc

    if not content.strip():
        raise AuthoringIngressError("empty input envelope")

    return parse_envelope(content)


def ingest_carrier(
    source: str | Path | None = None,
    *,
    repo: Path,
    stdin_bytes: bytes | None = None,
) -> IngressResult:
    """Convenience entry point reading from carrier and executing semantic ingress."""

    envelope = read_carrier_input(source, stdin_bytes=stdin_bytes)
    return execute_ingress(envelope, repo=repo)


def execute_ingress(envelope: IngressEnvelope, *, repo: Path) -> IngressResult:
    """Execute one carrier-neutral ingress envelope against the target repository."""

    repo_root = Path(repo).resolve()
    if not (repo_root / ".git").exists():
        raise AuthoringIngressError(f"not a Git repository: {repo}")

    operation = envelope.operation
    if operation == "AUTHOR_TASK":
        return _execute_author_task(envelope, repo_root)
    elif operation == "SUBMIT_REVIEW":
        return _execute_submit_review(envelope, repo_root)
    elif operation == "AUTHOR_REMEDIATION":
        return _execute_author_remediation(envelope, repo_root)
    elif operation == "AUTHOR_REPAIR":
        return _execute_author_repair(envelope, repo_root)
    else:
        raise AuthoringIngressError(f"unsupported operation: {operation}")


# ---------------------------------------------------------------------------
# Operation: AUTHOR_TASK
# ---------------------------------------------------------------------------

def _execute_author_task(envelope: IngressEnvelope, repo: Path) -> IngressResult:
    task_id = envelope.identity["task_id"]
    payload_str = _payload_to_str(envelope.payload)

    try:
        task = parse_task(payload_str)
    except TaskValidationError as exc:
        raise AuthoringIngressError(f"invalid TASK contract: {exc}") from exc

    if task.task_id != task_id:
        raise AuthoringIngressError(
            f"TASK task_id mismatch: identity specified {task_id}, payload contains {task.task_id}"
        )

    expected_main_sha = _get_expected_sha(envelope.expected_state, "expected_main_sha", "main_sha")
    remote = _resolve_remote(repo)
    current_main_sha = _resolve_ref_sha(repo, "refs/heads/main", remote)
    if current_main_sha is None:
        raise AuthoringIngressError("cannot resolve canonical main ref")

    _fetch_if_remote(repo, remote, current_main_sha)

    canonical_dest = f".ai/tasks/{task_id}.yaml"
    existing_bytes = _read_commit_blob(repo, current_main_sha, canonical_dest)
    task_bytes = payload_str.encode("utf-8")

    if existing_bytes is not None:
        try:
            existing_task = parse_task(existing_bytes.decode("utf-8"))
        except TaskValidationError as exc:
            raise AuthoringIngressError(f"corrupt existing canonical TASK on main: {exc}") from exc

        if task.revision == existing_task.revision:
            if existing_bytes.strip() == task_bytes.strip() or existing_task == task:
                code, parent_out, _ = _git(repo, "rev-parse", f"{current_main_sha}^", allow_fail=True)
                parent_sha = parent_out.strip() if code == 0 else None
                if current_main_sha == expected_main_sha or parent_sha == expected_main_sha:
                    return IngressResult(
                        operation="AUTHOR_TASK",
                        status="IDEMPOTENT",
                        canonical_destination=canonical_dest,
                        canonical_sha=current_main_sha,
                        replayed=True,
                        detail="identical TASK already canonicalized at current main",
                    )
            raise AuthoringIngressError(
                f"conflicting TASK payload for existing {task_id} revision {task.revision}"
            )
        elif task.revision != existing_task.revision + 1:
            raise AuthoringIngressError(
                f"TASK revision continuity violation: expected revision {existing_task.revision + 1}, got {task.revision}"
            )
    else:
        if task.revision != 1:
            raise AuthoringIngressError(
                f"new TASK must have revision 1, got {task.revision}"
            )

    if current_main_sha != expected_main_sha:
        raise AuthoringIngressError(
            f"expected main SHA mismatch (stale predecessor): expected {expected_main_sha}, current is {current_main_sha}"
        )

    # Check that working tree is clean if currently checked out on main
    code, status_out, _ = _git(repo, "status", "--porcelain", allow_fail=True)
    if code == 0 and status_out.strip():
        # Only error if currently checked out branch is main
        curr_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", allow_fail=True)[1].strip()
        if curr_branch == "main":
            raise AuthoringIngressError("working tree is dirty: cannot mutate main")

    # Create new tree using temporary index
    with tempfile.TemporaryDirectory(prefix="aios-ingress-") as tmp_dir:
        temp_index = Path(tmp_dir) / "index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(temp_index)
        _git_env(repo, env, "read-tree", current_main_sha)
        blob_sha = _hash_blob(repo, task_bytes)
        _git_env(
            repo,
            env,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{blob_sha},{canonical_dest}",
        )
        new_tree_sha = _git_env(repo, env, "write-tree").strip()

    # Verify no unrelated delta
    code, diff_out, _ = _git(
        repo,
        "diff-tree",
        "--name-only",
        "-r",
        current_main_sha,
        new_tree_sha,
        allow_fail=True,
    )
    changed_files = [line.strip() for line in diff_out.splitlines() if line.strip()]
    if changed_files != [canonical_dest]:
        raise AuthoringIngressError(
            f"unrelated delta detected in TASK mutation: expected {[canonical_dest]}, got {changed_files}"
        )

    commit_sha = _commit_tree(
        repo,
        new_tree_sha,
        [current_main_sha],
        f"task: {task_id} r{task.revision}",
    )

    _publish_ingress_ref(
        repo,
        remote,
        "refs/heads/main",
        commit_sha,
        expected_old_sha=current_main_sha,
    )

    # If current branch is main, sync working tree if clean
    curr_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", allow_fail=True)[1].strip()
    if curr_branch == "main":
        _git(repo, "reset", "--hard", "refs/heads/main", allow_fail=True)

    return IngressResult(
        operation="AUTHOR_TASK",
        status="CANONICALIZED",
        canonical_destination=canonical_dest,
        canonical_sha=commit_sha,
        replayed=False,
        detail=f"canonicalized {task_id} r{task.revision}",
    )


# ---------------------------------------------------------------------------
# Operation: SUBMIT_REVIEW
# ---------------------------------------------------------------------------

def _execute_submit_review(envelope: IngressEnvelope, repo: Path) -> IngressResult:
    run_id = envelope.identity["run_id"]
    payload_str = _payload_to_str(envelope.payload)

    try:
        review = parse_review(payload_str)
    except ReviewValidationError as exc:
        raise AuthoringIngressError(f"invalid REVIEW contract: {exc}") from exc

    expected_candidate_sha = _get_expected_sha(
        envelope.expected_state, "expected_candidate_sha", "expected_reviewed_sha"
    )

    remote = _resolve_remote(repo)
    candidate_ref = f"refs/heads/aios/review/{run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"
    decision_ref = f"refs/heads/aios/review-decision/{run_id}"

    candidate_sha = _resolve_ref_sha(repo, candidate_ref, remote)
    if candidate_sha is None:
        raise AuthoringIngressError(
            f"canonical RUN candidate ref missing: {candidate_ref}"
        )
    _fetch_if_remote(repo, remote, candidate_sha)

    if candidate_sha != expected_candidate_sha:
        raise AuthoringIngressError(
            f"expected candidate SHA mismatch: expected {expected_candidate_sha}, got {candidate_sha}"
        )

    if review.reviewed_sha != candidate_sha:
        raise AuthoringIngressError(
            f"REVIEW.reviewed_sha does not match canonical candidate: review={review.reviewed_sha}, candidate={candidate_sha}"
        )

    # Check for idempotent replay or conflicting decision
    existing_decision_sha = _resolve_ref_sha(repo, decision_ref, remote)
    if existing_decision_sha is not None:
        _fetch_if_remote(repo, remote, existing_decision_sha)
        existing_paths = [
            p
            for p in _ls_tree(repo, existing_decision_sha, ".ai/reviews")
            if p.endswith((".yaml", ".yml"))
        ]
        if existing_paths and len(existing_paths) == 1:
            existing_review_bytes = _read_commit_blob(
                repo, existing_decision_sha, existing_paths[0]
            )
            if existing_review_bytes is not None:
                try:
                    existing_rev = parse_review(existing_review_bytes.decode("utf-8"))
                    if existing_rev == review or existing_review_bytes.strip() == payload_str.strip().encode("utf-8"):
                        return IngressResult(
                            operation="SUBMIT_REVIEW",
                            status="IDEMPOTENT",
                            canonical_destination=decision_ref,
                            canonical_sha=existing_decision_sha,
                            replayed=True,
                            detail=f"identical REVIEW already canonicalized for {run_id}",
                        )
                except ReviewValidationError:
                    pass
        raise AuthoringIngressError(
            f"conflicting review decision already exists on {decision_ref}"
        )

    artifacts_sha = _resolve_ref_sha(repo, artifacts_ref, remote)
    if artifacts_sha is None:
        raise AuthoringIngressError(
            f"canonical RUN artifacts ref missing: {artifacts_ref}"
        )
    _fetch_if_remote(repo, remote, artifacts_sha)

    run_bytes = _read_commit_blob(repo, artifacts_sha, ".ai/transport/run.json")
    result_bytes = _read_commit_blob(repo, artifacts_sha, ".ai/transport/result.json")
    if run_bytes is None or result_bytes is None:
        raise AuthoringIngressError(
            f"canonical artifacts content missing for {run_id}"
        )

    run_data = _json_no_dups(run_bytes, "RUN")
    result_data = _json_no_dups(result_bytes, "ResultPackage")

    remediation = None
    if run_data.get("kind") == "REMEDIATION":
        if "predecessor" in run_data:
            try:
                run, remediation = _parse_remediation_run(run_data, run_id=run_id)
            except (ValueError, TypeError, RunValidationError) as exc:
                raise AuthoringIngressError(f"invalid canonical REMEDIATION RUN: {exc}") from exc
        else:
            exec_data = run_data.get("execution")
            if not isinstance(exec_data, Mapping):
                raise AuthoringIngressError("REMEDIATION execution must be a mapping")
            run = _run_from_data(exec_data.get("run"), "REMEDIATION.execution.run")
            if run.run_id != run_id:
                raise AuthoringIngressError(
                    f"RUN run_id mismatch: identity specified {run_id}, artifacts contain {run.run_id}"
                )
            rem_data = exec_data.get("remediation")
            if not isinstance(rem_data, Mapping):
                raise AuthoringIngressError("REMEDIATION remediation must be a mapping")
            remediation = parse_remediation(yaml.safe_dump(dict(rem_data)))
    elif "kind" not in run_data:
        if run_data.get("run_id") != run_id:
            raise AuthoringIngressError(
                f"RUN run_id mismatch: identity specified {run_id}, artifacts contain {run_data.get('run_id')!r}"
            )
        run = _run_from_data(run_data, "RUN")
    else:
        raise AuthoringIngressError(f"unknown canonical RUN kind: {run_data.get('kind')!r}")

    if run.status != ACTIVE:
        raise AuthoringIngressError(
            f"canonical successful RUN status is invalid: {run.status!r}"
        )

    _fetch_if_remote(repo, remote, run.base_sha)
    code, kind, _ = _git(repo, "cat-file", "-t", run.base_sha, allow_fail=True)
    if code != 0 or kind != "commit":
        raise AuthoringIngressError(f"RUN base_sha is not a canonical commit: {run.base_sha}")

    code, _, _ = _git(
        repo,
        "merge-base",
        "--is-ancestor",
        run.base_sha,
        candidate_sha,
        allow_fail=True,
    )
    if code != 0:
        raise AuthoringIngressError(
            f"reviewed candidate {candidate_sha} does not descend from RUN base_sha {run.base_sha}"
        )

    # Load task from candidate
    task_bytes = _read_commit_blob(repo, candidate_sha, f".ai/tasks/{run.task.id}.yaml")
    if task_bytes is None:
        raise AuthoringIngressError(
            f"canonical TASK {run.task.id} missing from candidate {candidate_sha}"
        )
    try:
        task = parse_task(task_bytes.decode("utf-8"))
    except TaskValidationError as exc:
        raise AuthoringIngressError(f"invalid canonical TASK contract: {exc}") from exc

    if not isinstance(result_data, Mapping) or "result" not in result_data:
        raise AuthoringIngressError("canonical ResultPackage must contain 'result'")
    try:
        result = validate_result(result_data["result"])
    except (ArtifactValidationError, TypeError, ValueError) as exc:
        raise AuthoringIngressError(f"invalid canonical RESULT: {exc}") from exc

    if result.head_sha != candidate_sha:
        raise AuthoringIngressError(
            f"RESULT head_sha does not match candidate: result={result.head_sha}, candidate={candidate_sha}"
        )

    evidence_data = result_data.get("evidence")
    if not isinstance(evidence_data, list):
        raise AuthoringIngressError("canonical ResultPackage evidence must be a list")
    try:
        evidence = tuple(validate_evidence(item) for item in evidence_data)
    except (ArtifactValidationError, TypeError, ValueError) as exc:
        raise AuthoringIngressError(f"invalid canonical EVIDENCE: {exc}") from exc

    prior_review = None
    if review.mode == "DELTA":
        prior_review = _resolve_prior_review(repo, remote, run_data, review)

    try:
        if remediation is None:
            validate_result_package(
                task=task,
                run=run,
                result=result,
                evidence=evidence,
            )
        else:
            if prior_review is None:
                raise AuthoringIngressError("DELTA review requires prior review")
            _validate_remediation_package(
                repo,
                source_sha=candidate_sha,
                task=task,
                run=run,
                remediation=remediation,
                prior_review=prior_review,
                result=result,
                evidence=evidence,
                execution_base_sha=(
                    run.base_sha if "execution_base" in run_data else None
                ),
            )
    except (ArtifactValidationError, ValueError, TypeError) as exc:
        raise AuthoringIngressError(f"result package validation failed: {exc}") from exc

    try:
        validate_review(
            task=task,
            result=result,
            review=review,
            prior_review=prior_review,
        )
    except ReviewValidationError as exc:
        raise AuthoringIngressError(f"review validation failed: {exc}") from exc

    # Create metadata-only commit on decision_ref
    blob_review = _hash_blob(repo, payload_str.encode("utf-8"))
    review_filename = f"{review.review_id}.yaml"
    reviews_tree = _mktree(repo, [("100644", "blob", blob_review, review_filename)])
    ai_tree = _mktree(repo, [("040000", "tree", reviews_tree, "reviews")])
    root_tree = _mktree(repo, [("040000", "tree", ai_tree, ".ai")])

    commit_sha = _commit_tree(
        repo,
        root_tree,
        [candidate_sha],
        f"review: {review.review_id} {review.verdict} {run_id}",
    )

    _publish_ingress_ref(repo, remote, decision_ref, commit_sha)

    return IngressResult(
        operation="SUBMIT_REVIEW",
        status="CANONICALIZED",
        canonical_destination=decision_ref,
        canonical_sha=commit_sha,
        replayed=False,
        detail=f"canonicalized {review.verdict} review decision for {run_id}",
    )


# ---------------------------------------------------------------------------
# Operation: AUTHOR_REMEDIATION
# ---------------------------------------------------------------------------

def _execute_author_remediation(envelope: IngressEnvelope, repo: Path) -> IngressResult:
    source_run_id = envelope.identity["source_run_id"]
    finding_id = envelope.identity["finding_id"]
    payload_str = _payload_to_str(envelope.payload)

    try:
        remediation = parse_remediation(payload_str)
    except ReviewValidationError as exc:
        raise AuthoringIngressError(f"invalid REMEDIATION contract: {exc}") from exc

    if remediation.finding_id != finding_id:
        raise AuthoringIngressError(
            f"REMEDIATION finding_id mismatch: identity specified {finding_id}, payload contains {remediation.finding_id}"
        )

    expected_reviewed_sha = _get_expected_sha(
        envelope.expected_state, "expected_reviewed_sha", "reviewed_sha"
    )

    remote = _resolve_remote(repo)
    remediation_ref = f"refs/heads/aios/remediation/{source_run_id}-{finding_id}"
    decision_ref = f"refs/heads/aios/review-decision/{source_run_id}"

    decision_sha = _resolve_ref_sha(repo, decision_ref, remote)
    if decision_sha is None:
        raise AuthoringIngressError(
            f"canonical review decision missing for source run {source_run_id}"
        )
    _fetch_if_remote(repo, remote, decision_sha)

    review_paths = [
        p
        for p in _ls_tree(repo, decision_sha, ".ai/reviews")
        if p.endswith((".yaml", ".yml"))
    ]
    if len(review_paths) != 1:
        raise AuthoringIngressError(
            f"canonical review decision for {source_run_id} is missing or ambiguous"
        )

    review_bytes = _read_commit_blob(repo, decision_sha, review_paths[0])
    if review_bytes is None:
        raise AuthoringIngressError(
            f"canonical review decision content missing for {source_run_id}"
        )
    review = parse_review(review_bytes.decode("utf-8"))

    if review.verdict != "CHANGES_REQUIRED":
        raise AuthoringIngressError(
            f"source review verdict is {review.verdict}, expected CHANGES_REQUIRED"
        )

    if review.reviewed_sha != expected_reviewed_sha:
        raise AuthoringIngressError(
            f"expected reviewed SHA mismatch: expected {expected_reviewed_sha}, got {review.reviewed_sha}"
        )

    if remediation.reviewed_sha != review.reviewed_sha:
        raise AuthoringIngressError(
            f"REMEDIATION reviewed_sha does not match REVIEW reviewed_sha: remediation={remediation.reviewed_sha}, review={review.reviewed_sha}"
        )

    matching_findings = [f for f in review.findings if f.id == finding_id]
    if not matching_findings:
        raise AuthoringIngressError(
            f"finding {finding_id} not found in source review {review.review_id}"
        )
    finding = matching_findings[0]

    if review.acceptance.get(finding.basis) != "FAIL":
        raise AuthoringIngressError(
            f"finding {finding_id} basis {finding.basis} is not marked FAIL in review"
        )

    if remediation.action != finding.action:
        raise AuthoringIngressError(
            f"REMEDIATION action {remediation.action} does not match finding action {finding.action}"
        )

    # Check for idempotent replay or conflicting remediation
    existing_remediation_sha = _resolve_ref_sha(repo, remediation_ref, remote)
    if existing_remediation_sha is not None:
        _fetch_if_remote(repo, remote, existing_remediation_sha)
        rem_paths = [
            p
            for p in _ls_tree(repo, existing_remediation_sha, ".ai/remediations")
            if p.endswith((".yaml", ".yml"))
        ]
        if rem_paths and len(rem_paths) == 1:
            existing_rem_bytes = _read_commit_blob(
                repo, existing_remediation_sha, rem_paths[0]
            )
            if existing_rem_bytes is not None:
                try:
                    existing_rem = parse_remediation(existing_rem_bytes.decode("utf-8"))
                    if existing_rem == remediation or existing_rem_bytes.strip() == payload_str.strip().encode("utf-8"):
                        return IngressResult(
                            operation="AUTHOR_REMEDIATION",
                            status="IDEMPOTENT",
                            canonical_destination=remediation_ref,
                            canonical_sha=existing_remediation_sha,
                            replayed=True,
                            detail=f"identical REMEDIATION already canonicalized for {source_run_id}-{finding_id}",
                        )
                except ReviewValidationError:
                    pass
        raise AuthoringIngressError(
            f"conflicting canonical remediation already exists on {remediation_ref}"
        )

    # Load task from candidate
    artifacts_ref = f"refs/heads/aios/artifacts/{source_run_id}"
    artifacts_sha = _resolve_ref_sha(repo, artifacts_ref, remote)
    if artifacts_sha is None:
        raise AuthoringIngressError(
            f"canonical source artifacts missing for {source_run_id}"
        )
    _fetch_if_remote(repo, remote, artifacts_sha)
    run_bytes = _read_commit_blob(repo, artifacts_sha, ".ai/transport/run.json")
    if run_bytes is None:
        raise AuthoringIngressError(
            f"canonical source RUN missing for {source_run_id}"
        )
    run_data = _json_no_dups(run_bytes, "RUN")
    task_id = _extract_task_id(run_data)

    task_bytes = _read_commit_blob(repo, review.reviewed_sha, f".ai/tasks/{task_id}.yaml")
    if task_bytes is None:
        raise AuthoringIngressError(
            f"canonical TASK {task_id} missing from reviewed candidate"
        )
    task = parse_task(task_bytes.decode("utf-8"))

    try:
        validate_remediation(review=review, remediation=remediation, task=task)
    except ReviewValidationError as exc:
        raise AuthoringIngressError(f"remediation validation failed: {exc}") from exc

    # Check if finding is already resolved by subsequent published main
    main_sha = _resolve_ref_sha(repo, "refs/heads/main", remote)
    if main_sha is not None:
        _fetch_if_remote(repo, remote, main_sha)
        code, _, _ = _git(
            repo,
            "merge-base",
            "--is-ancestor",
            review.reviewed_sha,
            main_sha,
            allow_fail=True,
        )
        if code == 0 and main_sha != review.reviewed_sha:
            # Main has advanced past reviewed_sha; verify finding isn't already resolved
            try:
                obs = observe_unified_state(task_id, repo=repo)
            except Exception as exc:
                raise AuthoringIngressError(
                    f"failed to resolve canonical correction state: {exc}"
                ) from exc

            is_outstanding = any(
                f.get("source_run_id") == source_run_id
                and f.get("finding_id") == finding_id
                and f.get("review_id") == review.review_id
                and f.get("reviewed_sha") == review.reviewed_sha
                for f in obs.outstanding_findings
            )
            if not is_outstanding:
                raise AuthoringIngressError(
                    f"finding {finding_id} is already resolved, superseded, or otherwise no longer outstanding"
                )

    # Create remediation commit containing both review and remediation
    blob_review = _hash_blob(repo, review_bytes)
    blob_remediation = _hash_blob(repo, payload_str.encode("utf-8"))
    review_filename = review_paths[0].split("/")[-1]
    remediation_filename = f"REMEDIATION-{source_run_id}-{finding_id}.yaml"

    reviews_tree = _mktree(repo, [("100644", "blob", blob_review, review_filename)])
    remediations_tree = _mktree(
        repo, [("100644", "blob", blob_remediation, remediation_filename)]
    )
    ai_tree = _mktree(
        repo,
        [
            ("040000", "tree", remediations_tree, "remediations"),
            ("040000", "tree", reviews_tree, "reviews"),
        ],
    )
    root_tree = _mktree(repo, [("040000", "tree", ai_tree, ".ai")])

    commit_sha = _commit_tree(
        repo,
        root_tree,
        [review.reviewed_sha],
        f"remediation: {source_run_id}-{finding_id}",
    )

    _publish_ingress_ref(repo, remote, remediation_ref, commit_sha)

    return IngressResult(
        operation="AUTHOR_REMEDIATION",
        status="CANONICALIZED",
        canonical_destination=remediation_ref,
        canonical_sha=commit_sha,
        replayed=False,
        detail=f"canonicalized remediation for {source_run_id}-{finding_id}",
    )


# ---------------------------------------------------------------------------
# Operation: AUTHOR_REPAIR
# ---------------------------------------------------------------------------

def _execute_author_repair(envelope: IngressEnvelope, repo: Path) -> IngressResult:
    failed_run_id = envelope.identity["failed_run_id"]
    if isinstance(envelope.payload, Mapping):
        repair_data = dict(envelope.payload)
    else:
        try:
            repair_data = json.loads(envelope.payload)
        except json.JSONDecodeError as exc:
            raise AuthoringIngressError(f"invalid REPAIR JSON: {exc}") from exc

    if not isinstance(repair_data, Mapping):
        raise AuthoringIngressError("REPAIR payload must be a mapping")

    expected_failed_head_sha = _get_expected_sha(
        envelope.expected_state, "expected_failed_head_sha", "failed_head_sha"
    )

    remote = _resolve_remote(repo)
    repair_ref = f"refs/heads/aios/repair/{failed_run_id}"
    failure_artifacts_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"

    artifacts_sha = _resolve_ref_sha(repo, failure_artifacts_ref, remote)
    if artifacts_sha is None:
        raise AuthoringIngressError(
            f"canonical failure artifacts missing for {failed_run_id}"
        )
    _fetch_if_remote(repo, remote, artifacts_sha)

    run_bytes = _read_commit_blob(repo, artifacts_sha, ".ai/transport/run.json")
    failure_bytes = _read_commit_blob(repo, artifacts_sha, ".ai/transport/failure.json")
    if run_bytes is None or failure_bytes is None:
        raise AuthoringIngressError(
            f"canonical failure content missing for {failed_run_id}"
        )

    run_data = _json_no_dups(run_bytes, "RUN")
    failure_data = _json_no_dups(failure_bytes, "FAILURE")

    if failure_data.get("kind") != "FAILURE" or failure_data.get("run_id") != failed_run_id:
        raise AuthoringIngressError("FAILURE artifact identity mismatch")

    failed_head_sha = failure_data.get("failed_head_sha")
    if failed_head_sha != expected_failed_head_sha:
        raise AuthoringIngressError(
            f"expected failed head SHA mismatch: expected {expected_failed_head_sha}, got {failed_head_sha}"
        )

    candidate = failure_data.get("candidate")
    if not isinstance(candidate, Mapping):
        raise AuthoringIngressError("FAILURE candidate must be a mapping")

    if not candidate.get("repairable"):
        raise AuthoringIngressError(
            f"failed RUN {failed_run_id} is not repairable"
        )
    if candidate.get("dirty"):
        raise AuthoringIngressError(
            f"failed RUN {failed_run_id} candidate is dirty"
        )
    if not candidate.get("descends_from_base"):
        raise AuthoringIngressError(
            f"failed RUN {failed_run_id} candidate does not descend from base"
        )

    # Check for idempotent replay or conflicting repair
    existing_repair_sha = _resolve_ref_sha(repo, repair_ref, remote)
    if existing_repair_sha is not None:
        _fetch_if_remote(repo, remote, existing_repair_sha)
        existing_repair_bytes = _read_commit_blob(
            repo, existing_repair_sha, ".ai/transport/repair.json"
        )
        if existing_repair_bytes is not None:
            try:
                existing_data = json.loads(existing_repair_bytes.decode("utf-8"))
                if existing_data == repair_data:
                    return IngressResult(
                        operation="AUTHOR_REPAIR",
                        status="IDEMPOTENT",
                        canonical_destination=repair_ref,
                        canonical_sha=existing_repair_sha,
                        replayed=True,
                        detail=f"identical REPAIR already canonicalized for {failed_run_id}",
                    )
            except (json.JSONDecodeError, UnicodeError):
                pass
        raise AuthoringIngressError(
            f"conflicting canonical repair already exists on {repair_ref}"
        )

    # Check that continuation doesn't already exist
    _check_continuation_does_not_exist(repo, remote, failed_run_id)

    # Load task
    task_id = _extract_task_id(run_data)
    task_revision = _extract_task_revision(run_data)
    base_sha = run_data.get("base_sha")
    if not isinstance(base_sha, str) or not base_sha:
        raise AuthoringIngressError("RUN base_sha is invalid")
    _fetch_if_remote(repo, remote, base_sha)

    task_bytes = _read_commit_blob(repo, base_sha, f".ai/tasks/{task_id}.yaml")
    if task_bytes is None:
        task_bytes = _read_commit_blob(repo, failed_head_sha, f".ai/tasks/{task_id}.yaml")
    if task_bytes is None:
        raise AuthoringIngressError(f"canonical TASK {task_id} missing from base commit")
    task = parse_task(task_bytes.decode("utf-8"))

    failed_changed_files = set(candidate.get("changed_files", []))
    try:
        _validate_repair_authorization(
            repair_data,
            failed_run_id=failed_run_id,
            failed_head_sha=failed_head_sha,
            task=task,
            failed_changed_files=failed_changed_files,
        )
    except ValueError as exc:
        raise AuthoringIngressError(f"repair authorization invalid: {exc}") from exc

    # Create repair commit containing .ai/transport/repair.json
    repair_json_bytes = json.dumps(
        repair_data, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    blob_repair = _hash_blob(repo, repair_json_bytes)
    transport_tree = _mktree(
        repo, [("100644", "blob", blob_repair, "repair.json")]
    )
    ai_tree = _mktree(repo, [("040000", "tree", transport_tree, "transport")])
    root_tree = _mktree(repo, [("040000", "tree", ai_tree, ".ai")])

    commit_sha = _commit_tree(
        repo,
        root_tree,
        [failed_head_sha],
        f"repair authorization for {failed_run_id}",
    )

    _publish_ingress_ref(repo, remote, repair_ref, commit_sha)

    return IngressResult(
        operation="AUTHOR_REPAIR",
        status="CANONICALIZED",
        canonical_destination=repair_ref,
        canonical_sha=commit_sha,
        replayed=False,
        detail=f"canonicalized repair authorization for {failed_run_id}",
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _payload_to_str(payload: str | Mapping[str, Any]) -> str:
    if isinstance(payload, str):
        return payload
    return yaml.safe_dump(dict(payload), sort_keys=False)


def _get_expected_sha(expected_state: Mapping[str, Any], *candidate_keys: str) -> str:
    for key in candidate_keys:
        val = expected_state.get(key)
        if isinstance(val, str) and val.strip() and re.fullmatch(r"^[0-9a-f]{40}$", val.strip()):
            return val.strip()
    keys_str = " or ".join(candidate_keys)
    raise AuthoringIngressError(
        f"expected_state must specify a valid 40-char SHA for {keys_str}"
    )


def _run_from_data(data: Any, document: str = "RUN") -> Run:
    if not isinstance(data, Mapping):
        raise AuthoringIngressError(f"{document} must be a mapping")
    task_data = data.get("task")
    if not isinstance(task_data, Mapping):
        raise AuthoringIngressError(f"{document}.task must be a mapping")
    task_id = task_data.get("id")
    task_rev = task_data.get("revision")
    if not isinstance(task_id, str) or not task_id:
        raise AuthoringIngressError(f"{document}.task.id must be a non-empty string")
    if isinstance(task_rev, bool) or not isinstance(task_rev, int) or task_rev < 1:
        raise AuthoringIngressError(f"{document}.task.revision must be a positive integer")
    try:
        return Run(
            run_id=str(data.get("run_id") or ""),
            task=RunTaskReference(id=task_id, revision=task_rev),
            executor=str(data.get("executor") or ""),
            base_sha=str(data.get("base_sha") or ""),
            workspace=str(data.get("workspace") or ""),
            head_sha=data.get("head_sha"),
            status=str(data.get("status") or ""),
        )
    except (RunValidationError, TypeError, ValueError) as exc:
        raise AuthoringIngressError(f"invalid canonical {document}: {exc}") from exc


def _extract_task_id(run_data: Mapping[str, Any]) -> str:
    if run_data.get("kind") == "REMEDIATION":
        exec_data = run_data.get("execution", {})
        task_data = exec_data.get("run", {}).get("task", {})
    else:
        task_data = run_data.get("task", {})
    task_id = task_data.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise AuthoringIngressError("cannot resolve task_id from RUN")
    return task_id


def _extract_task_revision(run_data: Mapping[str, Any]) -> int:
    if run_data.get("kind") == "REMEDIATION":
        exec_data = run_data.get("execution", {})
        task_data = exec_data.get("run", {}).get("task", {})
    else:
        task_data = run_data.get("task", {})
    rev = task_data.get("revision")
    if isinstance(rev, bool) or not isinstance(rev, int):
        raise AuthoringIngressError("cannot resolve task revision from RUN")
    return rev


def _resolve_prior_review(
    repo: Path,
    remote: str | None,
    run_data: Mapping[str, Any],
    review: Review,
) -> Review | None:
    pred = run_data.get("predecessor")
    if not isinstance(pred, Mapping):
        return None
    source_run_id = pred.get("source_run_id")
    if not isinstance(source_run_id, str):
        return None
    decision_ref = f"refs/heads/aios/review-decision/{source_run_id}"
    decision_sha = _resolve_ref_sha(repo, decision_ref, remote)
    if decision_sha is None:
        return None
    _fetch_if_remote(repo, remote, decision_sha)
    paths = [
        p
        for p in _ls_tree(repo, decision_sha, ".ai/reviews")
        if p.endswith((".yaml", ".yml"))
    ]
    if len(paths) != 1:
        return None
    prior_bytes = _read_commit_blob(repo, decision_sha, paths[0])
    if prior_bytes is None:
        return None
    try:
        return parse_review(prior_bytes.decode("utf-8"))
    except ReviewValidationError:
        return None


def _check_continuation_does_not_exist(
    repo: Path, remote: str | None, failed_run_id: str
) -> None:
    patterns = [
        f"refs/heads/aios/failure-artifacts/*",
        f"refs/heads/aios/artifacts/*",
    ]
    refs = _query_matching_refs(repo, remote, *patterns)
    for ref, sha in refs.items():
        run_id = ref.rsplit("/", 1)[-1]
        if run_id == failed_run_id:
            continue
        _fetch_if_remote(repo, remote, sha)
        failure_bytes = _read_commit_blob(repo, sha, ".ai/transport/failure.json")
        if failure_bytes is not None:
            try:
                f_data = json.loads(failure_bytes.decode("utf-8"))
                if f_data.get("continuation_of") == failed_run_id:
                    raise AuthoringIngressError(
                        f"canonical continuation already exists for failed RUN: {failed_run_id}"
                    )
            except (json.JSONDecodeError, UnicodeError):
                pass
        repair_bytes = _read_commit_blob(repo, sha, ".ai/transport/repair.json")
        if repair_bytes is not None:
            try:
                r_data = json.loads(repair_bytes.decode("utf-8"))
                if r_data.get("failed_run_id") == failed_run_id:
                    raise AuthoringIngressError(
                        f"canonical continuation already exists for failed RUN: {failed_run_id}"
                    )
            except (json.JSONDecodeError, UnicodeError):
                pass


def _query_matching_refs(
    repo: Path, remote: str | None, *patterns: str
) -> dict[str, str]:
    refs: dict[str, str] = {}
    if remote is not None:
        code, output, _ = _git(repo, "ls-remote", "--refs", remote, *patterns, allow_fail=True)
        if code == 0:
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    refs[parts[1]] = parts[0]
            return refs
    for pattern in patterns:
        code, output, _ = _git(repo, "for-each-ref", "--format=%(objectname) %(refname)", pattern, allow_fail=True)
        if code == 0:
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    refs[parts[1]] = parts[0]
    return refs


def _git(
    repo: Path, *args: str, strip_stdout: bool = True, allow_fail: bool = False
) -> tuple[int, str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "AIOS Ingress")
    env.setdefault("GIT_AUTHOR_EMAIL", "aios-ingress@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "AIOS Ingress")
    env.setdefault("GIT_COMMITTER_EMAIL", "aios-ingress@example.invalid")
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=False,
            check=False,
            env=env,
        )
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        if allow_fail:
            return 1, "", str(exc)
        raise AuthoringIngressError(f"Git command failed: {exc}") from exc
    if completed.returncode != 0 and not allow_fail:
        detail = stderr.strip() or stdout.strip() or f"exit {completed.returncode}"
        raise AuthoringIngressError(f"Git command failed: {detail}")
    return completed.returncode, stdout.strip() if strip_stdout else stdout, stderr.strip()


def _git_env(repo: Path, env: Mapping[str, str], *args: str) -> str:
    merged_env = dict(os.environ)
    merged_env.update(env)
    merged_env.setdefault("GIT_AUTHOR_NAME", "AIOS Ingress")
    merged_env.setdefault("GIT_AUTHOR_EMAIL", "aios-ingress@example.invalid")
    merged_env.setdefault("GIT_COMMITTER_NAME", "AIOS Ingress")
    merged_env.setdefault("GIT_COMMITTER_EMAIL", "aios-ingress@example.invalid")
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=True,
            check=True,
            env=merged_env,
        )
        return completed.stdout.strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip()
        raise AuthoringIngressError(f"Git command failed: {detail}") from exc


def _hash_blob(repo: Path, content: bytes) -> str:
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=content,
            capture_output=True,
            check=True,
        )
        return proc.stdout.decode("utf-8", errors="strict").strip()
    except Exception as exc:
        raise AuthoringIngressError(f"failed to hash blob: {exc}") from exc


def _mktree(repo: Path, entries: Sequence[tuple[str, str, str, str]]) -> str:
    # entries: (mode, kind, sha, name)
    lines = "".join(f"{mode} {kind} {sha}\t{name}\n" for mode, kind, sha, name in sorted(entries, key=lambda x: x[3]))
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=lines.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        return proc.stdout.decode("utf-8", errors="strict").strip()
    except Exception as exc:
        raise AuthoringIngressError(f"failed to create tree: {exc}") from exc


def _commit_tree(
    repo: Path, tree_sha: str, parent_shas: Sequence[str], message: str
) -> str:
    args = ["commit-tree", tree_sha]
    for p in parent_shas:
        args.extend(["-p", p])
    args.extend(["-m", message])
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "AIOS Ingress")
    env.setdefault("GIT_AUTHOR_EMAIL", "aios-ingress@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "AIOS Ingress")
    env.setdefault("GIT_COMMITTER_EMAIL", "aios-ingress@example.invalid")
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            check=True,
            env=env,
        )
        return proc.stdout.decode("utf-8", errors="strict").strip()
    except Exception as exc:
        raise AuthoringIngressError(f"failed to create commit: {exc}") from exc


def _resolve_remote(repo: Path) -> str | None:
    try:
        return resolve_transport_remote(repo)
    except ReviewTransportError:
        return None


def _resolve_ref_sha(repo: Path, ref: str, remote: str | None) -> str | None:
    if remote is not None:
        code, output, _ = _git(repo, "ls-remote", "--refs", remote, ref, allow_fail=True)
        if code == 0 and output.strip():
            lines = [line.split() for line in output.splitlines() if line.strip()]
            if lines and len(lines[0]) >= 2 and lines[0][1] == ref:
                return lines[0][0]
    code, output, _ = _git(repo, "rev-parse", "--verify", "--quiet", ref, allow_fail=True)
    if code == 0 and output.strip():
        return output.strip()
    return None


def _fetch_if_remote(repo: Path, remote: str | None, sha: str) -> None:
    if remote is not None:
        _git(repo, "fetch", "--no-tags", remote, sha, allow_fail=True)


def _read_commit_blob(repo: Path, commit_sha: str, rel_path: str) -> bytes | None:
    code, output, _ = _git(
        repo, "show", f"{commit_sha}:{rel_path}", strip_stdout=False, allow_fail=True
    )
    if code == 0:
        return output.encode("utf-8") if isinstance(output, str) else output
    return None


def _ls_tree(repo: Path, commit_sha: str, prefix: str) -> list[str]:
    code, output, _ = _git(
        repo, "ls-tree", "-r", "--name-only", commit_sha, "--", prefix, allow_fail=True
    )
    if code != 0:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def _publish_ingress_ref(
    repo: Path,
    remote: str | None,
    ref: str,
    new_sha: str,
    expected_old_sha: str | None = None,
) -> None:
    # 1. Update local ref
    if expected_old_sha is not None:
        code, _, err = _git(repo, "update-ref", ref, new_sha, expected_old_sha, allow_fail=True)
        if code != 0:
            raise AuthoringIngressError(f"optimistic concurrency failure updating {ref}: {err}")
    else:
        _git(repo, "update-ref", ref, new_sha)

    # 2. Push to remote if configured
    if remote is not None:
        args = ["push", "--porcelain", "--no-tags"]
        if expected_old_sha is not None:
            args.append(f"--force-with-lease={ref}:{expected_old_sha}")
        args.extend([remote, f"{new_sha}:{ref}"])
        code, _, err = _git(repo, *args, allow_fail=True)
        if code != 0:
            if expected_old_sha is not None:
                _git(repo, "update-ref", ref, expected_old_sha, allow_fail=True)
            raise AuthoringIngressError(f"failed to push ingress ref {ref} to {remote}: {err}")


def _json_no_dups(source: bytes, document: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        res: dict[str, Any] = {}
        for k, v in items:
            if k in res:
                raise AuthoringIngressError(f"{document} contains duplicate key: {k}")
            res[k] = v
        return res

    try:
        val = json.loads(source.decode("utf-8", errors="strict"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuthoringIngressError(f"invalid canonical {document} JSON: {exc}") from exc
    if not isinstance(val, Mapping):
        raise AuthoringIngressError(f"canonical {document} must be a mapping")
    return val
