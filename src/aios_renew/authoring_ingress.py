"""Generic Brain-to-repository authoring ingress for AIOS-renew."""

from __future__ import annotations

import copy
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
from typing import TYPE_CHECKING, Any

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
    _repair_review_lineage,
    _validate_remediation_package,
    _validate_repair_package,
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
from .review_transport import (
    REPAIR_SUPERSESSION_FORMAT,
    REPAIR_SUPERSESSION_PATH,
    REPAIR_SUPERSESSION_PREFIX,
    ReviewTransportError,
    resolve_remote_repair_authorization,
    resolve_transport_remote,
    validate_runtime_failure_binding,
)
from .run import (
    ACTIVE,
    Run,
    RunTaskReference,
    RunValidationError,
    SUPPORTED_EXECUTORS,
)
from .return_affinity import (AffinityError, document_affinity, require_same_affinity,
                              require_authored_affinity, OriginAffinity)
from .origin_authoring_proof import AdmittedOrigin, OriginProofError, PROOF
from .task import Task, TaskValidationError, _TaskLoader, parse_task
from .verification_contract import MINIMUM_SUFFICIENT_V1
from .unified_state import observe_unified_state

if TYPE_CHECKING:
    from .decision_packet import DecisionPacket


class AuthoringIngressError(ValueError):
    """Raised when an ingress envelope or semantic payload fails closed."""


_ALLOWED_OPERATIONS = frozenset(
    {"AUTHOR_TASK", "SUBMIT_REVIEW", "AUTHOR_REMEDIATION", "AUTHOR_REPAIR"}
)

_CANONICAL_ENVELOPE_FORMAT = "AIOS_INGRESS_ENVELOPE"
_ALLOWED_ENVELOPE_FORMATS = frozenset({_CANONICAL_ENVELOPE_FORMAT})

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
        "audited_handoff",
        "origin_authoring_proof",
    }
)

_FORBIDDEN_TOP_LEVEL_IDENTITY_KEYS = frozenset(
    {"task_id", "run_id", "source_run_id", "finding_id", "failed_run_id"}
)

_OPERATION_REQUIRED_IDENTITY_KEYS = {
    "AUTHOR_TASK": frozenset({"task_id"}),
    "SUBMIT_REVIEW": frozenset({"run_id"}),
    "AUTHOR_REMEDIATION": frozenset({"source_run_id", "finding_id"}),
    "AUTHOR_REPAIR": frozenset({"failed_run_id"}),
}


@dataclass(frozen=True)
class IngressEnvelope:
    """Bounded, carrier-neutral ingress envelope."""

    format: str
    version: int
    operation: str
    identity: Mapping[str, Any]
    expected_state: Mapping[str, Any]
    payload: str | Mapping[str, Any]
    audited_handoff: Mapping[str, Any] | None = None
    origin_authoring_proof: str | None = None


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
            data = yaml.load(raw, Loader=_TaskLoader)
        except (yaml.YAMLError, TaskValidationError) as exc:
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
        if unknown & _FORBIDDEN_TOP_LEVEL_IDENTITY_KEYS:
            raise AuthoringIngressError(
                f"envelope contains unknown or misplaced identity field(s): {fields} (subject identity must be located within 'identity' mapping)"
            )
        raise AuthoringIngressError(f"envelope contains unknown field(s): {fields}")

    envelope_format = data.get("format")
    if not isinstance(envelope_format, str) or envelope_format != _CANONICAL_ENVELOPE_FORMAT:
        raise AuthoringIngressError(
            f"invalid envelope format: expected {_CANONICAL_ENVELOPE_FORMAT!r}, got {envelope_format!r}"
        )

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise AuthoringIngressError(f"invalid envelope version: expected 1, got {version!r}")

    operation = data.get("operation")
    if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
        raise AuthoringIngressError(
            f"invalid envelope operation: expected one of {sorted(_ALLOWED_OPERATIONS)}, got {operation!r}"
        )

    if "identity" not in data or not isinstance(data.get("identity"), Mapping):
        raise AuthoringIngressError("identity is required and must be a mapping")
    identity = dict(data["identity"])

    expected_state = data.get("expected_state")
    if not isinstance(expected_state, Mapping):
        raise AuthoringIngressError("expected_state is required and must be a mapping")

    payload = data.get("payload")
    if payload is None or (isinstance(payload, str) and not payload.strip()):
        raise AuthoringIngressError("payload is required and must not be empty")
    if not isinstance(payload, (str, Mapping)):
        raise AuthoringIngressError("payload must be a string or mapping")

    _validate_operation_identity(operation, identity)

    handoff = data.get("audited_handoff")
    if handoff is not None:
        _validate_handoff_shape(handoff)
        if operation == "SUBMIT_REVIEW":
            raise AuthoringIngressError("SUBMIT_REVIEW does not accept Brain audited handoff")

    origin_proof = data.get("origin_authoring_proof")
    if "origin_authoring_proof" in data and (
            operation != "AUTHOR_TASK" or type(origin_proof) is not str or not PROOF.fullmatch(origin_proof)):
        raise AuthoringIngressError("invalid origin_authoring_proof carrier")

    return IngressEnvelope(
        format=envelope_format,
        version=version,
        operation=operation,
        identity=identity,
        expected_state=dict(expected_state),
        payload=payload,
        audited_handoff=handoff,
        origin_authoring_proof=origin_proof,
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
    expected_keys = _OPERATION_REQUIRED_IDENTITY_KEYS.get(operation, frozenset())
    extra_keys = set(identity) - expected_keys
    if extra_keys:
        fields = ", ".join(sorted(extra_keys))
        raise AuthoringIngressError(f"{operation} identity contains unexpected key(s): {fields}")

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
    origin_admission: AdmittedOrigin | None = None,
) -> IngressResult:
    """Convenience entry point reading from carrier and executing semantic ingress."""

    envelope = read_carrier_input(source, stdin_bytes=stdin_bytes)
    return execute_ingress(envelope, repo=repo, origin_admission=origin_admission)


def execute_ingress(envelope: IngressEnvelope, *, repo: Path,
                    origin_admission: AdmittedOrigin | None = None) -> IngressResult:
    """Execute one carrier-neutral ingress envelope against the target repository."""

    repo_root = Path(repo).resolve()
    if not (repo_root / ".git").exists():
        raise AuthoringIngressError(f"not a Git repository: {repo}")

    operation = envelope.operation
    if operation in _AUTHORING_FLOWS:
        # Caller-owned mappings cannot change the audited payload mid-ingress.
        envelope = copy.deepcopy(envelope)
    if operation == "AUTHOR_TASK":
        return _execute_author_task(envelope, repo_root, origin_admission)
    if envelope.origin_authoring_proof is not None or origin_admission is not None:
        raise AuthoringIngressError("origin admission is only valid for AUTHOR_TASK")
    if operation == "SUBMIT_REVIEW":
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

def _execute_author_task(envelope: IngressEnvelope, repo: Path,
                         origin_admission: AdmittedOrigin | None = None) -> IngressResult:
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

    needs_origin = isinstance(task.return_affinity, OriginAffinity) and task.revision == 1
    def recheck_origin():
        try:
            if needs_origin:
                if not isinstance(origin_admission, AdmittedOrigin):
                    raise OriginProofError("new ORIGIN_AFFINE TASK requires admitted origin authoring proof")
                origin_admission.require_envelope(envelope)
            elif envelope.origin_authoring_proof is not None or origin_admission is not None:
                raise OriginProofError("revisions and legacy TASKs cannot consume current-chat origin proof")
        except OriginProofError as exc:
            raise AuthoringIngressError(str(exc)) from exc

    # This gate also precedes identical revision-1 replay: another carrier
    # attempt cannot use semantic equality to bypass proof admission.
    recheck_origin()

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

    if task.verification.policy != MINIMUM_SUFFICIENT_V1:
        raise AuthoringIngressError(
            "new TASK identities and revisions require verification.policy "
            f"{MINIMUM_SUFFICIENT_V1}"
        )

    try:
        require_authored_affinity(
            yaml.safe_load(payload_str), existing_task if existing_bytes is not None else None
        )
    except AffinityError as exc:
        raise AuthoringIngressError(str(exc)) from exc

    if current_main_sha != expected_main_sha:
        raise AuthoringIngressError(
            f"expected main SHA mismatch (stale predecessor): expected {expected_main_sha}, current is {current_main_sha}"
        )

    audit_binding = _validate_authoring_handoff(envelope, repo)

    # Check that working tree is clean if currently checked out on main
    code, status_out, _ = _git(repo, "status", "--porcelain", allow_fail=True)
    if code == 0 and status_out.strip():
        # Only error if currently checked out branch is main
        curr_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", allow_fail=True)[1].strip()
        if curr_branch == "main":
            raise AuthoringIngressError("working tree is dirty: cannot mutate main")

    # Create new tree using temporary index
    _recheck_authoring_binding(envelope, repo, audit_binding)
    recheck_origin()
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

    _recheck_authoring_binding(envelope, repo, audit_binding)
    recheck_origin()
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
        review_path = f".ai/reviews/{review.review_id}.yaml"
        review_bytes = payload_str.encode("utf-8")
        try:
            _validate_metadata_commit(
                repo,
                existing_decision_sha,
                expected_parent_sha=candidate_sha,
                metadata_path=review_path,
                metadata_bytes=review_bytes,
                operation="SUBMIT_REVIEW replay",
            )
        except AuthoringIngressError:
            pass
        else:
            return IngressResult(
                operation="SUBMIT_REVIEW",
                status="IDEMPOTENT",
                canonical_destination=decision_ref,
                canonical_sha=existing_decision_sha,
                replayed=True,
                detail=f"identical REVIEW already canonicalized for {run_id}",
            )
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
    repair_bytes = _read_commit_blob(repo, artifacts_sha, ".ai/transport/repair.json")
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
                _, remediation = _parse_remediation_run(run_data, run_id=run_id)
                run = _run_from_data(run_data["execution"]["run"])
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

    try:
        require_same_affinity(task, run)
    except AffinityError as exc:
        raise AuthoringIngressError(str(exc)) from exc

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
    repaired_finding_id = None
    repair_result_base_sha = None
    if repair_bytes is not None:
        if remediation is not None:
            raise AuthoringIngressError(
                "canonical artifacts contain conflicting REMEDIATION/REPAIR lineage"
            )
        if remote is None:
            raise AuthoringIngressError(
                "canonical remote is required for REPAIR review lineage"
            )
        try:
            (
                _,
                repair_result_base_sha,
                prior_review,
                repaired_finding_id,
            ) = _repair_review_lineage(
                repo,
                remote=remote,
                publication_run_id=run_id,
                child_run_data=run_data,
                child_run=run,
                child_head_sha=candidate_sha,
                lineage_bytes=repair_bytes,
                task=task,
            )
        except (
            ArtifactValidationError,
            ReviewValidationError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            UnicodeError,
        ) as exc:
            raise AuthoringIngressError(
                f"invalid canonical REPAIR lineage: {exc}"
            ) from exc
        _validate_repair_review_semantics(
            review,
            prior_review=prior_review,
            repaired_finding_id=repaired_finding_id,
        )
    elif review.mode == "DELTA":
        prior_review = _resolve_prior_review(repo, remote, run_data, review)

    try:
        if repair_bytes is not None:
            assert repair_result_base_sha is not None
            _validate_repair_package(
                repo,
                source_sha=candidate_sha,
                result_base_sha=repair_result_base_sha,
                task=task,
                run=run,
                result=result,
                evidence=evidence,
            )
        elif remediation is None:
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

    review_bytes = payload_str.encode("utf-8")
    review_path = f".ai/reviews/{review.review_id}.yaml"
    root_tree = _tree_with_metadata(
        repo, candidate_sha, review_path, review_bytes
    )

    commit_sha = _commit_tree(
        repo,
        root_tree,
        [candidate_sha],
        f"review: {review.review_id} {review.verdict} {run_id}",
    )

    _validate_metadata_commit(
        repo,
        commit_sha,
        expected_parent_sha=candidate_sha,
        metadata_path=review_path,
        metadata_bytes=review_bytes,
        operation="SUBMIT_REVIEW",
    )
    _publish_ingress_ref(
        repo, remote, decision_ref, commit_sha, expect_missing=True
    )

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
        remediation_path = (
            f".ai/remediations/REMEDIATION-{source_run_id}-{finding_id}.yaml"
        )
        remediation_bytes = payload_str.encode("utf-8")
        try:
            _validate_metadata_commit(
                repo,
                existing_remediation_sha,
                expected_parent_sha=decision_sha,
                metadata_path=remediation_path,
                metadata_bytes=remediation_bytes,
                operation="AUTHOR_REMEDIATION replay",
            )
        except AuthoringIngressError:
            pass
        else:
            return IngressResult(
                operation="AUTHOR_REMEDIATION",
                status="IDEMPOTENT",
                canonical_destination=remediation_ref,
                canonical_sha=existing_remediation_sha,
                replayed=True,
                detail=f"identical REMEDIATION already canonicalized for {source_run_id}-{finding_id}",
            )
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
        from .operator import (
            _run_from_data as source_run_from_data,
            _remediation_execution_from_data,
            _validated_repair_remediation_source,
        )
        result_bytes = _read_commit_blob(repo, artifacts_sha, ".ai/transport/result.json")
        if result_bytes is None:
            raise ValueError("canonical source RESULT is missing")
        result_data = _json_no_dups(result_bytes, "source ResultPackage")
        package = ResultPackage(
            result=validate_result(result_data["result"]),
            evidence=tuple(validate_evidence(item) for item in result_data["evidence"]),
        )
        source_run = (
            _remediation_execution_from_data(run_data["execution"]).run
            if run_data.get("kind") == "REMEDIATION" else source_run_from_data(run_data)
        )
        if source_run.run_id != source_run_id or source_run.task.id != task.task_id or source_run.task.revision != task.revision:
            raise ValueError("canonical source RUN/TASK identity mismatch")
        _validated_repair_remediation_source(
            repo, task=task, run_data=run_data, run=source_run, package=package,
            review=review, repair=_read_commit_blob(repo, artifacts_sha, ".ai/transport/repair.json"),
        )
        validate_remediation(review=review, remediation=remediation, task=task)
    except (ReviewValidationError, ValueError, TypeError, KeyError) as exc:
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

    remediation_bytes = payload_str.encode("utf-8")
    remediation_path = (
        f".ai/remediations/REMEDIATION-{source_run_id}-{finding_id}.yaml"
    )
    audit_binding = _validate_authoring_handoff(envelope, repo)
    _recheck_authoring_binding(envelope, repo, audit_binding)
    root_tree = _tree_with_metadata(
        repo, decision_sha, remediation_path, remediation_bytes
    )

    commit_sha = _commit_tree(
        repo,
        root_tree,
        [decision_sha],
        f"remediation: {source_run_id}-{finding_id}",
    )

    _validate_metadata_commit(
        repo,
        commit_sha,
        expected_parent_sha=decision_sha,
        metadata_path=remediation_path,
        metadata_bytes=remediation_bytes,
        operation="AUTHOR_REMEDIATION",
    )
    _recheck_authoring_binding(envelope, repo, audit_binding)
    _publish_ingress_ref(
        repo, remote, remediation_ref, commit_sha, expect_missing=True
    )

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

    run_view = _resolve_underlying_run_view(run_data, expected_run_id=failed_run_id)
    base_sha = _extract_base_sha(run_view)
    if failure_data.get("base_sha") is not None and failure_data.get("base_sha") != base_sha:
        raise AuthoringIngressError("FAILURE base_sha does not match RUN")

    task_id = _extract_task_id(run_view)
    task_revision = _extract_task_revision(run_view)
    if isinstance(failure_data.get("task"), Mapping):
        fail_task = failure_data["task"]
        if fail_task.get("id") != task_id or fail_task.get("revision") != task_revision:
            raise AuthoringIngressError("FAILURE task identity mismatch")

    repair_json_bytes = json.dumps(
        repair_data, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    # A legacy authorization is revision 1.  Later authorizations are separate,
    # immutable refs whose metadata binds the exact predecessor and FAILURE.
    existing_repair_sha = _resolve_ref_sha(repo, repair_ref, remote)
    current_authorization = None
    if existing_repair_sha is not None:
        try:
            current_authorization = resolve_remote_repair_authorization(
                repo, failed_run_id
            )
        except ReviewTransportError as exc:
            raise AuthoringIngressError(
                f"canonical repair authorization lineage is invalid: {exc}"
            ) from exc
        if current_authorization.revision == 1:
            try:
                _validate_metadata_commit(
                    repo,
                    current_authorization.commit_sha,
                    expected_parent_sha=failed_head_sha,
                    metadata_path=".ai/transport/repair.json",
                    metadata_bytes=current_authorization.repair,
                    operation="AUTHOR_REPAIR replay",
                )
            except AuthoringIngressError as exc:
                raise AuthoringIngressError(
                    f"conflicting canonical repair already exists on {repair_ref}"
                ) from exc
        if (
            current_authorization.repair == repair_json_bytes
            and current_authorization.revision == 1
        ):
            return IngressResult(
                operation="AUTHOR_REPAIR",
                status="IDEMPOTENT",
                canonical_destination=current_authorization.ref,
                canonical_sha=current_authorization.commit_sha,
                replayed=True,
                detail=f"identical REPAIR already canonicalized for {failed_run_id}",
            )
        if current_authorization.repair == repair_json_bytes:
            replay_predecessor = _get_expected_sha(
                envelope.expected_state,
                "expected_current_repair_sha",
                "expected_repair_sha",
                "predecessor_repair_sha",
            )
            replay_failure = _get_expected_sha(
                envelope.expected_state,
                "expected_failure_artifacts_sha",
                "expected_failure_sha",
                "failure_artifacts_sha",
            )
            if replay_predecessor != current_authorization.commit_sha:
                raise AuthoringIngressError(
                    "expected current REPAIR SHA does not match canonical authorization"
                )
            if replay_failure != artifacts_sha:
                raise AuthoringIngressError(
                    "expected canonical FAILURE identity does not match failed RUN"
                )
            return IngressResult(
                operation="AUTHOR_REPAIR",
                status="IDEMPOTENT",
                canonical_destination=current_authorization.ref,
                canonical_sha=current_authorization.commit_sha,
                replayed=True,
                detail=f"identical REPAIR already canonicalized for {failed_run_id}",
            )

    # Check that continuation doesn't already exist
    _check_continuation_does_not_exist(repo, remote, failed_run_id)

    # Load task
    _fetch_if_remote(repo, remote, base_sha)
    code, kind, _ = _git(repo, "cat-file", "-t", base_sha, allow_fail=True)
    if code != 0 or kind != "commit":
        raise AuthoringIngressError(f"RUN base_sha is not a canonical commit: {base_sha}")

    task_bytes = _read_commit_blob(repo, base_sha, f".ai/tasks/{task_id}.yaml")
    if task_bytes is None:
        task_bytes = _read_commit_blob(repo, failed_head_sha, f".ai/tasks/{task_id}.yaml")
    if task_bytes is None:
        raise AuthoringIngressError(f"canonical TASK {task_id} missing from base commit")
    task = parse_task(task_bytes.decode("utf-8"))
    if task.task_id != task_id or task.revision != task_revision:
        raise AuthoringIngressError("RUN does not reference the supplied TASK")

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
    if (
        current_authorization is not None
        and repair_data.get("action")
        in ("CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE")
        and failure_data.get("phase") not in ("EXECUTION", "COMPLETION_GATE")
    ):
        raise AuthoringIngressError(
            "repair authorization action requires a pre-verification FAILURE"
        )

    if current_authorization is not None:
        expected_predecessor = _get_expected_sha(
            envelope.expected_state, "expected_current_repair_sha", "expected_repair_sha", "predecessor_repair_sha"
        )
        if expected_predecessor != current_authorization.commit_sha:
            raise AuthoringIngressError("expected current REPAIR SHA does not match canonical authorization")
        expected_failure_sha = _get_expected_sha(
            envelope.expected_state, "expected_failure_artifacts_sha", "expected_failure_sha", "failure_artifacts_sha"
        )
        if expected_failure_sha != artifacts_sha:
            raise AuthoringIngressError("expected canonical FAILURE identity does not match failed RUN")

    audit_binding = _validate_authoring_handoff(envelope, repo)
    _recheck_authoring_binding(envelope, repo, audit_binding)
    if current_authorization is None:
        root_tree = _tree_with_metadata(
            repo, failed_head_sha, ".ai/transport/repair.json", repair_json_bytes
        )
        commit_sha = _commit_tree(
            repo,
            root_tree,
            [failed_head_sha],
            f"repair authorization for {failed_run_id}",
        )
        _validate_metadata_commit(
            repo,
            commit_sha,
            expected_parent_sha=failed_head_sha,
            metadata_path=".ai/transport/repair.json",
            metadata_bytes=repair_json_bytes,
            operation="AUTHOR_REPAIR",
        )
        destination_ref = repair_ref
    else:
        revision = current_authorization.revision + 1
        destination_ref = (
            f"{REPAIR_SUPERSESSION_PREFIX}{failed_run_id}/{revision}"
        )
        supersession = {
            "format": REPAIR_SUPERSESSION_FORMAT,
            "version": 1,
            "failed_run_id": failed_run_id,
            "authorization_revision": revision,
            "predecessor_repair_sha": current_authorization.commit_sha,
            "failure_artifacts_sha": artifacts_sha,
        }
        supersession_bytes = json.dumps(
            supersession, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        repair_tree = _tree_with_metadata(
            repo,
            current_authorization.commit_sha,
            ".ai/transport/repair.json",
            repair_json_bytes,
            replace_existing=True,
        )
        # Build the second metadata delta from a temporary commit so the tree
        # helper can remain the single path-authoring primitive.
        repair_commit = _commit_tree(
            repo, repair_tree, [current_authorization.commit_sha],
            f"staged repair supersession for {failed_run_id}",
        )
        root_tree = _tree_with_metadata(
            repo,
            repair_commit,
            REPAIR_SUPERSESSION_PATH,
            supersession_bytes,
            replace_existing=True,
        )
        commit_sha = _commit_tree(
            repo,
            root_tree,
            [current_authorization.commit_sha],
            f"repair authorization revision {revision} for {failed_run_id}",
        )
        if _read_commit_blob(repo, commit_sha, ".ai/transport/repair.json") != repair_json_bytes:
            raise AuthoringIngressError("AUTHOR_REPAIR successor content mismatch")
        if _read_commit_blob(repo, commit_sha, REPAIR_SUPERSESSION_PATH) != supersession_bytes:
            raise AuthoringIngressError("AUTHOR_REPAIR supersession metadata mismatch")

    _recheck_authoring_binding(envelope, repo, audit_binding)
    _publish_ingress_ref(
        repo, remote, destination_ref, commit_sha, expect_missing=True
    )

    return IngressResult(
        operation="AUTHOR_REPAIR",
        status="CANONICALIZED",
        canonical_destination=destination_ref,
        canonical_sha=commit_sha,
        replayed=False,
        detail=f"canonicalized repair authorization for {failed_run_id}",
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

_AUTHORING_FLOWS = {
    "AUTHOR_TASK": "TASK_AUTHORING",
    "AUTHOR_REMEDIATION": "REMEDIATION_AUTHORING",
    "AUTHOR_REPAIR": "REPAIR_AUTHORING",
}
_AuthoringRefs = tuple[str | None, dict[str, str]]
_AuthoringBinding = tuple[str, dict[str, Any], _AuthoringRefs]


def _validate_handoff_shape(value: Any) -> None:
    """The sole transient carrier contains BP-4A input, never canonical facts."""
    if not isinstance(value, Mapping) or set(value) != {"format", "version", "stage1", "stage2"}:
        raise AuthoringIngressError("complete two-stage audited_handoff is required")
    if value["format"] != "AIOS_AUDITED_AUTHORING_HANDOFF" or (
        type(value["version"]) is not int or value["version"] != 1
    ):
        raise AuthoringIngressError("invalid audited authoring handoff format/version")
    if not all(isinstance(value[key], Mapping) for key in ("stage1", "stage2")):
        raise AuthoringIngressError("audited handoff requires Stage 1 and Stage 2 mappings")


def _authoring_refs(repo: Path) -> _AuthoringRefs:
    remote = _resolve_remote(repo)
    # Fresh authoring must not fall back to stale local refs on remote failure.
    refs = _query_matching_refs(repo, remote, "refs/heads/main", "refs/heads/aios/*")
    if "refs/heads/main" not in refs:
        raise AuthoringIngressError("cannot resolve exact canonical main for audited authoring")
    return remote, refs


def _prove_authoring_inputs(repo: Path, main_sha: str, remote: str | None) -> None:
    """Prove every file input of the working-tree projections verbatim.

    Registries, roadmap and TASK lookup inputs live in .ai. Include ignored
    additions: Git status and clean filters alone do not prove byte equality.
    Local Runtime observations cannot substitute for canonical remote lineage.
    """
    _fetch_if_remote(repo, remote, main_sha)
    tree = subprocess.run(
        ("git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", main_sha, "--", ".ai"),
        capture_output=True, check=True,
    )
    entries = {}
    for raw in tree.stdout.split(b"\0"):
        if not raw:
            continue
        header, relative = raw.split(b"\t", 1)
        _, kind, object_sha = header.split()
        if kind != b"blob" or b"\n" in relative or b"\r" in relative:
            raise AuthoringIngressError("invalid canonical authoring input tree entry")
        entries[relative.decode("utf-8")] = object_sha.decode("ascii")
    paths = list(entries)
    actual = set()
    ai_root = repo / ".ai"
    if ai_root.is_symlink():
        raise AuthoringIngressError("divergent canonical authoring inputs: .ai symlink")
    if ai_root.exists():
        for path in ai_root.rglob("*"):
            if path.is_symlink():
                raise AuthoringIngressError("divergent canonical authoring inputs: symlink")
            if path.is_file():
                actual.add(path.relative_to(repo).as_posix())
    if actual != set(paths):
        raise AuthoringIngressError("divergent working-tree/canonical authoring input set")
    # Git computes raw blob identities in one read-only batch. --no-filters
    # ensures CRLF conversion, clean drivers and index state cannot hide drift.
    hashes = subprocess.run(
        ("git", "-C", str(repo), "hash-object", "--no-filters", "--stdin-paths"),
        input=("".join(f"{path}\n" for path in paths)).encode("utf-8"),
        capture_output=True, check=True,
    ).stdout.decode("ascii").splitlines()
    if hashes != list(entries.values()):
        raise AuthoringIngressError("divergent canonical authoring input bytes")
    from .operator import _runtime_paths_readonly
    state = _runtime_paths_readonly(repo)
    for directory in (state.runs, state.admission_failures):
        if directory.exists() and any(directory.iterdir()):
            raise AuthoringIngressError("local Runtime observations cannot supply canonical authoring inputs")


def _authoring_blob(repo: Path, sha: str, path: str, remote: str | None) -> bytes:
    _fetch_if_remote(repo, remote, sha)
    content = _read_commit_blob(repo, sha, path)
    if content is None:
        raise AuthoringIngressError(f"missing canonical authoring material: {path}")
    return content


def _canonical_repair_supersession(observed: Mapping[str, Any]) -> dict[str, Any]:
    """Read the one unexecuted authorization; never select a replacement strategy.

    BP-3 uses this same canonical reconstruction as ingress. No handoff or
    Human text can supply the prior strategy or the failed subject identity.
    """
    from .brain_sync import observe_brain_sync

    unified, selected = observed.get("unified_state"), observed.get("selected_task")
    if (observed.get("next_action") != "EXECUTE_REPAIR"
            or observed.get("selection_status") != "SELECTED"
            or observed.get("blocker") is not None or not isinstance(unified, Mapping)
            or unified.get("next_action") != "EXECUTE_REPAIR"
            or unified.get("blocker") is not None or not isinstance(selected, Mapping)):
        raise AuthoringIngressError("explicit REPAIR_AUTHORING is outside unexecuted supersession state")
    failed_run_id = unified.get("failed_run_id")
    if not isinstance(failed_run_id, str) or unified.get("run_id") != failed_run_id:
        raise AuthoringIngressError("supersession failed RUN identity is missing or mismatched")
    repo = Path(observed["repository"]["root"])
    remote, refs = _authoring_refs(repo)
    main_sha = refs["refs/heads/main"]
    if main_sha != observed.get("main_sha"):
        raise AuthoringIngressError("supersession canonical main is stale")
    _prove_authoring_inputs(repo, main_sha, remote)
    fresh = observe_brain_sync(repo=repo)
    if (fresh.main_sha != main_sha or fresh.next_action != "EXECUTE_REPAIR"
            or fresh.selected_task != selected or fresh.unified_state != unified
            or fresh.blocker != observed.get("blocker")):
        raise AuthoringIngressError("supersession canonical lifecycle observation is stale")
    current = resolve_remote_repair_authorization(repo, failed_run_id, remote=remote)
    if current.commit_sha != unified.get("correction_sha") or refs.get(current.ref) != current.commit_sha:
        raise AuthoringIngressError("supersession current REPAIR authorization is stale or mismatched")
    failure_sha = refs.get(f"refs/heads/aios/failure-artifacts/{failed_run_id}")
    if failure_sha is None:
        raise AuthoringIngressError("supersession canonical FAILURE is missing")
    _check_continuation_does_not_exist(repo, remote, failed_run_id)
    task_bytes = _authoring_blob(repo, main_sha, f".ai/tasks/{selected['id']}.yaml", remote)
    task = parse_task(task_bytes.decode("utf-8"))
    if {"id": task.task_id, "revision": task.revision} != selected:
        raise AuthoringIngressError("supersession canonical TASK identity mismatch")
    run_doc = _json_no_dups(_authoring_blob(repo, failure_sha, ".ai/transport/run.json", remote), "RUN")
    failed = dict(_resolve_underlying_run_view(run_doc, expected_run_id=failed_run_id))
    run = _run_from_data(failed)
    failure = _json_no_dups(_authoring_blob(repo, failure_sha, ".ai/transport/failure.json", remote), "FAILURE")
    failed_head = unified.get("failed_head_sha")
    if run.head_sha is not None and run.head_sha != failed_head:
        raise AuthoringIngressError("supersession failed candidate identity mismatch")
    validate_runtime_failure_binding(
        failure, run_id=failed_run_id, task_id=task.task_id, task_revision=task.revision,
        executor=run.executor, base_sha=run.base_sha, candidate_sha=failed_head,
        modification_scope=task.scope.modify,
    )
    if run.task != RunTaskReference(task.task_id, task.revision):
        raise AuthoringIngressError("supersession failed RUN TASK identity mismatch")
    authorization = _json_no_dups(current.repair, "current REPAIR")
    _validate_repair_authorization(
        authorization, failed_run_id=failed_run_id, failed_head_sha=failed_head,
        task=task, failed_changed_files=set(failure["candidate"]["changed_files"]),
    )
    if _authoring_refs(repo) != (remote, refs):
        raise AuthoringIngressError("supersession canonical inputs moved during reconstruction")
    return {
        "kind": "REPAIR_AUTHORING", "task": yaml.safe_load(task_bytes),
        "failed_run": failed, "failure": failure, "failure_artifacts_sha": failure_sha,
        "current_authorization": {"kind": "REPAIR", "authorization_sha": current.commit_sha,
                                  "authorization": authorization},
    }


def _compose_authoring_packet(
    envelope: IngressEnvelope, repo: Path
) -> tuple[DecisionPacket, dict[str, Any], dict[str, Any], _AuthoringRefs]:
    """Deterministic ingress glue over existing projection/compiler owners."""
    from .brain_sync import observe_brain_sync
    from .brain_context import compose_brain_work_context, resolve_flow
    from .decision_packet import compile_decision_packet
    from .brain_audit import parse_profile_registry
    from .brain_return_contract import parse_return_contract_registry, select_return_contract

    remote, refs = _authoring_refs(repo)
    main_sha = refs["refs/heads/main"]
    _prove_authoring_inputs(repo, main_sha, remote)
    snapshot = observe_brain_sync(repo=repo)
    if snapshot.main_sha != main_sha:
        raise AuthoringIngressError("Brain Sync canonical main moved during authoring")
    flow = _AUTHORING_FLOWS[envelope.operation]
    request = {"flow_selector": flow} if envelope.operation == "AUTHOR_TASK" else None
    unified = snapshot.unified_state
    if (envelope.operation == "AUTHOR_REPAIR"
            and snapshot.next_action == "EXECUTE_REPAIR"
            and unified is not None
            and unified.get("failed_run_id") == envelope.identity["failed_run_id"]
            and refs.get(f"refs/heads/aios/repair/{envelope.identity['failed_run_id']}") is not None):
        # EXECUTE_REPAIR alone does not identify supersession. The explicit
        # side-flow reconstructs this exact RUN's current unexecuted authority
        # and then checks the envelope's predecessor and FAILURE CAS bindings.
        request = {"flow_selector": "REPAIR_AUTHORING"}
    context = compose_brain_work_context(snapshot, current_request=request)
    resolution = resolve_flow(context)
    if resolution.selected_flow != flow:
        raise AuthoringIngressError(f"canonical selected flow is not {flow}")
    if envelope.operation == "AUTHOR_TASK":
        task = parse_task(_payload_to_str(envelope.payload))
        material = {"kind": flow, "observations": [f"AUTHOR_TASK {task.task_id} revision {task.revision}"]}
    else:
        selected, unified = snapshot.selected_task, snapshot.unified_state
        if selected is None or unified is None:
            raise AuthoringIngressError("canonical authoring subject is absent")
        task_bytes = _authoring_blob(repo, main_sha, f".ai/tasks/{selected['id']}.yaml", remote)
        # Feed the compiler the canonical family document. Dataclass defaults
        # (notably legacy verification.policy=None) are not authored fields.
        canonical_task = parse_task(task_bytes.decode("utf-8"))
        task_data = yaml.safe_load(task_bytes)
        if envelope.operation == "AUTHOR_REMEDIATION":
            findings = []
            outstanding = unified.get("outstanding_findings", [])
            if not any(item["source_run_id"] == envelope.identity["source_run_id"]
                       and item["finding_id"] == envelope.identity["finding_id"] for item in outstanding):
                raise AuthoringIngressError("REMEDIATION target is not canonically outstanding")
            for identity in outstanding:
                ref = f"refs/heads/aios/review-decision/{identity['source_run_id']}"
                sha = refs.get(ref)
                if sha is None:
                    raise AuthoringIngressError("canonical finding REVIEW ref is missing")
                _fetch_if_remote(repo, remote, sha)
                paths = [p for p in _ls_tree(repo, sha, ".ai/reviews") if p.endswith((".yaml", ".yml"))]
                if len(paths) != 1:
                    raise AuthoringIngressError("canonical finding REVIEW is ambiguous")
                review = parse_review(_authoring_blob(repo, sha, paths[0], remote).decode("utf-8"))
                if review.review_id != identity["review_id"] or review.reviewed_sha != identity["reviewed_sha"]:
                    raise AuthoringIngressError("canonical finding REVIEW lineage mismatch")
                finding = next((f for f in review.findings if f.id == identity["finding_id"]), None)
                if finding is None:
                    raise AuthoringIngressError("canonical outstanding finding is missing from REVIEW")
                findings.append({"source_run_id": identity["source_run_id"], "review_id": review.review_id,
                                 "reviewed_sha": review.reviewed_sha, "finding": asdict(finding)})
            material = {"kind": flow, "task": task_data, "findings": findings,
                        "subject_kind": "UNIQUE_FINDING" if len(findings) == 1 else "CORRECTION_FRONTIER"}
        else:
            failed_run_id = envelope.identity["failed_run_id"]
            if unified.get("failed_run_id") != failed_run_id:
                raise AuthoringIngressError("REPAIR target differs from canonical failed RUN")
            sha = refs.get(f"refs/heads/aios/failure-artifacts/{failed_run_id}")
            if sha is None:
                raise AuthoringIngressError("canonical FAILURE ref is missing")
            run_data = _json_no_dups(_authoring_blob(repo, sha, ".ai/transport/run.json", remote), "RUN")
            failure = _json_no_dups(_authoring_blob(repo, sha, ".ai/transport/failure.json", remote), "FAILURE")
            material = {"kind": flow, "task": task_data, "failure": failure,
                        "failed_run": dict(_resolve_underlying_run_view(run_data, expected_run_id=failed_run_id))}
            if resolution.selection_basis == "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION":
                material = dict(context.repair_supersession_material)
                expected_repair = _get_expected_sha(envelope.expected_state, "expected_current_repair_sha",
                                                   "expected_repair_sha", "predecessor_repair_sha")
                expected_failure = _get_expected_sha(envelope.expected_state, "expected_failure_artifacts_sha",
                                                    "expected_failure_sha", "failure_artifacts_sha")
                if expected_repair != material["current_authorization"]["authorization_sha"]:
                    raise AuthoringIngressError("expected current REPAIR SHA does not match canonical authorization")
                if expected_failure != material["failure_artifacts_sha"] or sha != material["failure_artifacts_sha"]:
                    raise AuthoringIngressError("expected canonical FAILURE identity does not match failed RUN")
            from .correction_preflight import canonical_repair_strategy_state
            material["strategy_state"] = canonical_repair_strategy_state(
                repo, failed_run_id=failed_run_id, task=canonical_task, failure=failure,
                expected_refs=refs,
            )
    packet = compile_decision_packet(context, resolution, material)
    profile = parse_profile_registry((repo / ".ai/brain-audit-profiles.yaml").read_bytes())["profiles"][0]
    if profile["id"] != "brain-high-value-v3" or profile["version"] != 3:
        raise AuthoringIngressError("new audited authoring requires brain-high-value-v3")
    contract = select_return_contract(
        parse_return_contract_registry((repo / ".ai/brain-return-contracts.yaml").read_bytes()), packet)
    _prove_authoring_inputs(repo, main_sha, remote)
    if _authoring_refs(repo) != (remote, refs):
        raise AuthoringIngressError("canonical authoring inputs moved during packet composition")
    return packet, profile, contract, (remote, refs)


def _authoring_family_body(operation: str, value: Any, packet: DecisionPacket, repo: Path) -> Any:
    from .brain_return_contract import _normalize as normalize_return_value

    # Strict Brain representation plus family normalization; no new normalizer.
    if isinstance(value, str):
        value = yaml.safe_load(value)
    body = normalize_return_value(dict(value))
    if operation == "AUTHOR_TASK":
        return parse_task(json.dumps(body))
    if operation == "AUTHOR_REMEDIATION":
        return parse_remediation(json.dumps(body))
    facts = packet.as_dict()
    task_bytes = _read_commit_blob(repo, facts["canonical_facts"]["main_sha"],
                                   f".ai/tasks/{facts['canonical_facts']['selected_task']['id']}.yaml")
    if task_bytes is None:
        raise AuthoringIngressError("canonical REPAIR TASK is missing")
    task = parse_task(task_bytes.decode("utf-8"))
    return _validate_repair_authorization(
        body, failed_run_id=facts["subject"]["failed_run_id"],
        failed_head_sha=facts["subject"]["failed_head_sha"], task=task,
        failed_changed_files=set(facts["subject"]["failed_changed_files"]))


def _validate_authoring_handoff(envelope: IngressEnvelope, repo: Path) -> _AuthoringBinding:
    from .brain_sync import BrainSyncError
    from .brain_audit import validate_stage2

    if envelope.audited_handoff is None:
        raise AuthoringIngressError("new authoring mutation requires audited_handoff")
    try:
        _validate_handoff_shape(envelope.audited_handoff)
        packet, profile, contract, refs = _compose_authoring_packet(envelope, repo)
        audit = validate_stage2(packet, profile, envelope.audited_handoff["stage1"], envelope.audited_handoff["stage2"])
        if audit["outcome"] != "CANDIDATE":
            raise AuthoringIngressError("audited authoring requires Stage-2 CANDIDATE")
        candidate = _authoring_family_body(envelope.operation, audit["handoff_candidate"], packet, repo)
        if candidate != _authoring_family_body(envelope.operation, envelope.payload, packet, repo):
            raise AuthoringIngressError("audited candidate differs from canonical family payload")
        if envelope.operation == "AUTHOR_TASK":
            _validate_task_acceptance_phases(candidate, audit.get("acceptance_phase_ledger"))
        return packet.packet_fingerprint, contract["return_contract_ref"], refs
    except (ValueError, TypeError, KeyError, OSError, RecursionError, yaml.YAMLError,
            BrainSyncError, ReviewTransportError, subprocess.SubprocessError) as exc:
        raise AuthoringIngressError(f"audited authoring rejected: {exc}") from exc


def _validate_task_acceptance_phases(task: Task, ledger: Any) -> None:
    """Enforce exact Brain-declared coverage without interpreting acceptance prose."""
    if ledger is None:
        raise AuthoringIngressError("new TASK authoring requires acceptance_phase_ledger")
    ids = [entry["id"] for entry in ledger]
    if len(ids) != len(set(ids)):
        raise AuthoringIngressError("acceptance_phase_ledger contains duplicate ids")
    if set(ids) != {criterion.id for criterion in task.acceptance}:
        raise AuthoringIngressError("acceptance_phase_ledger must exactly cover final TASK acceptance ids")
    if any(entry["phase"] != "CLAIM_NOW" for entry in ledger):
        raise AuthoringIngressError("final TASK acceptance requires only CLAIM_NOW; reconcile PROOF_LATER before ingress")


def _recheck_authoring_binding(envelope: IngressEnvelope, repo: Path, binding: _AuthoringBinding) -> None:
    from .brain_sync import BrainSyncError

    try:
        packet, _, contract, refs = _compose_authoring_packet(envelope, repo)
        if (packet.packet_fingerprint, contract["return_contract_ref"], refs) != binding:
            raise AuthoringIngressError("audited authoring binding is no longer current")
    except (ValueError, TypeError, KeyError, OSError, BrainSyncError,
            ReviewTransportError, subprocess.SubprocessError) as exc:
        raise AuthoringIngressError(f"audited authoring freshness rejected: {exc}") from exc


def _validate_repair_review_semantics(
    review: Review,
    *,
    prior_review: Review | None,
    repaired_finding_id: str | None,
) -> None:
    if prior_review is None:
        if review.mode != "PRIMARY" or review.prior_finding_id is not None:
            raise AuthoringIngressError(
                "REPAIR before a semantic finding requires a PRIMARY review"
            )
        return
    if (
        repaired_finding_id is None
        or review.mode != "DELTA"
        or review.prior_finding_id != repaired_finding_id
    ):
        raise AuthoringIngressError(
            "REPAIR of REMEDIATION requires the exact repaired DELTA finding"
        )


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
            return_affinity=document_affinity(data),
        )
    except (RunValidationError, TypeError, ValueError) as exc:
        raise AuthoringIngressError(f"invalid canonical {document}: {exc}") from exc


def _resolve_underlying_run_view(
    run_data: Mapping[str, Any], *, expected_run_id: str | None = None
) -> Mapping[str, Any]:
    if not isinstance(run_data, Mapping):
        raise AuthoringIngressError("canonical RUN must be a mapping")
    kind = run_data.get("kind")
    if kind == "REMEDIATION":
        exec_data = run_data.get("execution")
        if not isinstance(exec_data, Mapping):
            raise AuthoringIngressError("canonical REMEDIATION execution must be a mapping")
        run_view = exec_data.get("run")
        if not isinstance(run_view, Mapping):
            raise AuthoringIngressError("canonical REMEDIATION execution.run must be a mapping")
        if "predecessor" in run_data and not isinstance(run_data["predecessor"], Mapping):
            raise AuthoringIngressError("canonical REMEDIATION predecessor must be a mapping")
        if "execution_base" in run_data and not isinstance(run_data["execution_base"], Mapping):
            raise AuthoringIngressError("canonical REMEDIATION execution_base must be a mapping")
    elif "kind" not in run_data:
        run_view = run_data
    else:
        raise AuthoringIngressError(f"unknown canonical RUN kind: {kind!r}")

    if expected_run_id is not None:
        actual_run_id = run_view.get("run_id")
        if actual_run_id != expected_run_id:
            raise AuthoringIngressError(
                f"RUN run_id mismatch: identity specified {expected_run_id}, artifacts contain {actual_run_id!r}"
            )
    return run_view


def _extract_task_id(run_data: Mapping[str, Any]) -> str:
    run_view = _resolve_underlying_run_view(run_data)
    task_data = run_view.get("task")
    if not isinstance(task_data, Mapping):
        raise AuthoringIngressError("cannot resolve task_id from RUN")
    task_id = task_data.get("id")
    if not isinstance(task_id, str) or not task_id or not re.fullmatch(r"^TASK-[A-Za-z0-9_-]+$", task_id):
        raise AuthoringIngressError("cannot resolve task_id from RUN")
    return task_id


def _extract_task_revision(run_data: Mapping[str, Any]) -> int:
    run_view = _resolve_underlying_run_view(run_data)
    task_data = run_view.get("task")
    if not isinstance(task_data, Mapping):
        raise AuthoringIngressError("cannot resolve task revision from RUN")
    rev = task_data.get("revision")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
        raise AuthoringIngressError("cannot resolve task revision from RUN")
    return rev


def _extract_base_sha(run_data: Mapping[str, Any]) -> str:
    run_view = _resolve_underlying_run_view(run_data)
    base_sha = run_view.get("base_sha")
    if (
        not isinstance(base_sha, str)
        or not base_sha
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base_sha)
    ):
        raise AuthoringIngressError("RUN base_sha is invalid")
    return base_sha


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
                if not isinstance(f_data, Mapping):
                    raise AuthoringIngressError(
                        "canonical continuation FAILURE observation is invalid"
                    )
                if f_data.get("continuation_of") == failed_run_id:
                    raise AuthoringIngressError(
                        f"canonical continuation already exists for failed RUN: {failed_run_id}"
                    )
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise AuthoringIngressError(
                    "canonical continuation FAILURE observation is invalid"
                ) from exc
        repair_bytes = _read_commit_blob(repo, sha, ".ai/transport/repair.json")
        if repair_bytes is not None:
            try:
                r_data = json.loads(repair_bytes.decode("utf-8"))
                if not isinstance(r_data, Mapping):
                    raise AuthoringIngressError(
                        "canonical continuation REPAIR observation is invalid"
                    )
                if r_data.get("failed_run_id") == failed_run_id:
                    raise AuthoringIngressError(
                        f"canonical continuation already exists for failed RUN: {failed_run_id}"
                    )
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise AuthoringIngressError(
                    "canonical continuation REPAIR observation is invalid"
                ) from exc


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
        raise AuthoringIngressError(
            "canonical continuation observation is unavailable"
        )
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


def _tree_with_metadata(
    repo: Path,
    predecessor_sha: str,
    metadata_path: str,
    metadata_bytes: bytes,
    *,
    replace_existing: bool = False,
) -> str:
    """Return the predecessor tree with one metadata blob added or replaced."""

    if (
        not replace_existing
        and _read_commit_blob(repo, predecessor_sha, metadata_path) is not None
    ):
        raise AuthoringIngressError(
            f"metadata destination already exists on predecessor: {metadata_path}"
        )

    blob_sha = _hash_blob(repo, metadata_bytes)
    with tempfile.TemporaryDirectory(prefix="aios-ingress-index-") as temp_dir:
        index_path = Path(temp_dir) / "index"
        env = {"GIT_INDEX_FILE": str(index_path)}
        _git_env(repo, env, "read-tree", predecessor_sha)
        _git_env(
            repo,
            env,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{blob_sha},{metadata_path}",
        )
        return _git_env(repo, env, "write-tree")


def _validate_metadata_commit(
    repo: Path,
    commit_sha: str,
    *,
    expected_parent_sha: str,
    metadata_path: str,
    metadata_bytes: bytes,
    operation: str,
) -> None:
    """Fail closed unless a commit is the exact authorized metadata mutation."""

    code, kind, _ = _git(repo, "cat-file", "-t", commit_sha, allow_fail=True)
    if code != 0 or kind != "commit":
        raise AuthoringIngressError(
            f"{operation} structural validation failed: destination is not a commit"
        )

    _, parent_output, _ = _git(repo, "show", "-s", "--format=%P", commit_sha)
    parents = parent_output.split()
    if parents != [expected_parent_sha]:
        raise AuthoringIngressError(
            f"{operation} structural validation failed: expected sole parent "
            f"{expected_parent_sha}, got {parents}"
        )

    _, changed_output, _ = _git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        expected_parent_sha,
        commit_sha,
        "--",
    )
    changed_paths = [line for line in changed_output.splitlines() if line]
    if changed_paths != [metadata_path]:
        raise AuthoringIngressError(
            f"{operation} structural validation failed: expected changed paths "
            f"{[metadata_path]}, got {changed_paths}"
        )

    _, entry_output, _ = _git(
        repo, "ls-tree", commit_sha, "--", metadata_path
    )
    entry_prefix = "100644 blob "
    if not entry_output.startswith(entry_prefix) or not entry_output.endswith(
        f"\t{metadata_path}"
    ):
        raise AuthoringIngressError(
            f"{operation} structural validation failed: metadata is not an exact 100644 blob"
        )

    actual_bytes = _read_commit_blob(repo, commit_sha, metadata_path)
    if actual_bytes != metadata_bytes:
        raise AuthoringIngressError(
            f"{operation} structural validation failed: metadata content mismatch"
        )


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
    # Resolve the exact repository-relative tree entry before reading its object.
    # `git show <commit>:<path>` uses revision/path parsing and `_git` decodes
    # stdout as text, neither of which is suitable for an exact blob read.
    code, kind, _ = _git(repo, "cat-file", "-t", commit_sha, allow_fail=True)
    if code != 0 or kind != "commit":
        return None
    try:
        tree = subprocess.run(
            ("git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", commit_sha),
            capture_output=True,
            check=False,
        )
        if tree.returncode != 0:
            return None
        path_bytes = rel_path.encode("utf-8")
        blob_sha = None
        for entry in tree.stdout.split(b"\0"):
            if not entry:
                continue
            header, separator, entry_path = entry.partition(b"\t")
            if separator and entry_path == path_bytes:
                fields = header.split(b" ")
                if len(fields) != 3 or fields[1] != b"blob":
                    return None
                blob_sha = fields[2].decode("ascii")
                break
        if blob_sha is None:
            return None
        blob = subprocess.run(
            ("git", "-C", str(repo), "cat-file", "blob", blob_sha),
            capture_output=True,
            check=False,
        )
        return blob.stdout if blob.returncode == 0 else None
    except (OSError, UnicodeError):
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
    *,
    expect_missing: bool = False,
) -> None:
    if expect_missing and expected_old_sha is not None:
        raise AuthoringIngressError(
            "ingress ref publication cannot expect both a missing and existing ref"
        )

    # 1. Update local ref
    local_expected_sha = "0" * 40 if expect_missing else expected_old_sha
    if local_expected_sha is not None:
        code, _, err = _git(
            repo,
            "update-ref",
            ref,
            new_sha,
            local_expected_sha,
            allow_fail=True,
        )
        if code != 0:
            raise AuthoringIngressError(f"optimistic concurrency failure updating {ref}: {err}")
    else:
        _git(repo, "update-ref", ref, new_sha)

    # 2. Push to remote if configured
    if remote is not None:
        args = ["push", "--porcelain", "--no-tags"]
        if expect_missing:
            args.append(f"--force-with-lease={ref}:")
        elif expected_old_sha is not None:
            args.append(f"--force-with-lease={ref}:{expected_old_sha}")
        args.extend([remote, f"{new_sha}:{ref}"])
        code, _, err = _git(repo, *args, allow_fail=True)
        if code != 0:
            if expect_missing:
                _git(repo, "update-ref", "-d", ref, new_sha, allow_fail=True)
            elif expected_old_sha is not None:
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
