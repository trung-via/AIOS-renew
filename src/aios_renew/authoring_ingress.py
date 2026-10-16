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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
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
    PublicationError,
    PublicationReservation,
    check_publication_reservation,
    reserve_publication,
    release_publication_reservation,
    finish_main_publication_reservation,
    _pending_review_guard,
    _recheck_binding,
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
    RemoteRepairAuthorization,
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
from .verification_contract import CURRENT_VERIFICATION_POLICY
from .unified_state import observe_unified_state

if TYPE_CHECKING:
    from .brain_sync import BrainSyncSnapshot
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

    # Check every active envelope and payload field for forbidden authority.
    # Retired REMEDIATION audit support is never consumed, persisted or echoed.
    # Its shape, private contents and serialization cannot grant or deny final
    # contract admission. All active envelope/payload fields keep their gates.
    _scan_for_prohibited_fields(
        {key: value for key, value in data.items() if key != "audited_handoff"}
        if data.get("operation") == "AUTHOR_REMEDIATION" else data
    )

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

    if operation == "AUTHOR_TASK" and "audited_handoff" in data:
        raise AuthoringIngressError("AUTHOR_TASK does not accept audited_handoff")
    handoff = data.get("audited_handoff")
    # Legacy correction audit serialization is inert compatibility data. Only
    # the final contract and Runtime's canonical subject authorize mutation.
    if handoff is not None and operation not in _AUTHORING_FLOWS:
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
    if operation == "AUTHOR_TASK" or operation in _AUTHORING_FLOWS:
        # Caller-owned mappings cannot change the validated payload mid-ingress.
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
    if envelope.audited_handoff is not None:
        raise AuthoringIngressError("AUTHOR_TASK does not accept audited_handoff")
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
    transport = _resolve_author_task_transport(repo)
    remote, _ = transport
    current_main_sha = _resolve_author_task_main(repo, remote)

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
                    try:
                        finish_main_publication_reservation(
                            repo, remote, subject=task_id, published_sha=current_main_sha,
                        )
                    except PublicationError as exc:
                        raise AuthoringIngressError(f"{exc.report.cause}: {exc}") from exc
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

    if task.verification.policy != CURRENT_VERIFICATION_POLICY:
        raise AuthoringIngressError(
            "new TASK identities and revisions require verification.policy "
            f"{CURRENT_VERIFICATION_POLICY}"
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

    # Check that working tree is clean if currently checked out on main
    code, status_out, _ = _git(repo, "status", "--porcelain", allow_fail=True)
    if code == 0 and status_out.strip():
        # Only error if currently checked out branch is main
        curr_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", allow_fail=True)[1].strip()
        if curr_branch == "main":
            raise AuthoringIngressError("working tree is dirty: cannot mutate main")

    # Create new tree using temporary index
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

    recheck_origin()
    if _resolve_author_task_transport(repo) != transport:
        raise AuthoringIngressError("canonical main transport authority changed during authoring")
    try:
        # Historical eligible PASS decisions must also remain publishable.
        decisions, pending = _pending_review_guard(
            repo, remote, run_id=task_id, reviewed_sha=commit_sha, main_sha=current_main_sha,
        )
        reservation = reserve_publication(
            repo, remote, kind="MAIN_MUTATION", subject=task_id,
            source_sha=commit_sha, main_sha=current_main_sha,
        )
        _recheck_binding(repo, remote, pending, run_id=task_id, reviewed_sha=commit_sha,
                         main_sha=current_main_sha, decisions=decisions)
        recheck_origin()
        if _resolve_author_task_transport(repo) != transport:
            raise AuthoringIngressError("canonical main transport authority changed during reservation")
        _publish_ingress_ref(
            repo, remote, "refs/heads/main", commit_sha,
            expected_old_sha=current_main_sha, reservation=reservation,
        )
        if _resolve_author_task_main(repo, remote) != commit_sha:
            raise AuthoringIngressError("canonical main publication postcondition failed")
        release_publication_reservation(repo, remote, reservation)
    except PublicationError as exc:
        raise AuthoringIngressError(f"publication concurrency failure ({exc.report.cause}): {exc}") from exc

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
            if review.verdict == "PASS":
                _reserve_review_submission(
                    repo, remote, run_id, candidate_sha, existing_decision_sha,
                    decision_exists=True,
                )
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
    recovery_bytes = _read_commit_blob(repo, artifacts_sha, ".ai/transport/publication-recovery.json")
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

    if recovery_bytes is not None:
        from .publication import validate_publication_recovery
        if remote is None or remediation is not None or repair_bytes is not None or review.mode != "PRIMARY":
            raise AuthoringIngressError("recovered source requires an independent PRIMARY review and canonical remote")
        try:
            validate_publication_recovery(repo, remote=remote, raw=recovery_bytes,
                run=run, task=task, candidate_sha=candidate_sha,
                package=ResultPackage(result=result, evidence=evidence),
                current_main_sha=_resolve_ref_sha(repo, "refs/heads/main", remote))
        except (PublicationError, ValueError, TypeError, KeyError) as exc:
            raise AuthoringIngressError(f"invalid publication recovery lineage: {exc}") from exc

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
    reservation = None
    if review.verdict == "PASS":
        reservation = _reserve_review_submission(
            repo, remote, run_id, candidate_sha, commit_sha, artifacts_sha=artifacts_sha,
        )
    _publish_ingress_ref(
        repo, remote, decision_ref, commit_sha, expect_missing=True, reservation=reservation,
    )
    # A PASS owner is intentionally retained across the durable write and the
    # asynchronous Publisher continuation. Ingress has no main publication authority.

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

@dataclass(frozen=True)
class _RemediationBinding:
    transport: tuple[str, str]
    refs: Mapping[str, str]
    task: Task
    run: Run
    package: ResultPackage
    review: Review
    existing_sha: str | None
    correction_state: Mapping[str, Any] | None = None
    checkout_sha: str | None = None


def _direct_remediation_payload(payload: str | Mapping[str, Any]) -> tuple[str, Remediation]:
    """Decode the final family contract once, without silently dropping authority."""
    try:
        raw = _payload_to_str(payload)
        data = yaml.load(raw, Loader=_TaskLoader)
        allowed = {"finding_id", "action", "reviewed_sha", "modification_scope", "scope",
                   "affected_verification", "verification", "verification_policy", "full_suite_reason", "constraints"}
        if not isinstance(data, Mapping) or set(data) - allowed:
            raise ValueError("final REMEDIATION contains unsupported fields")
        for key, fields in (("scope", {"modify"}), ("verification", {"policy", "affected", "required", "full_suite_reason"}),
                            ("constraints", {"hard"})):
            if key in data and (key != "constraints" or isinstance(data[key], Mapping)):
                if not isinstance(data[key], Mapping) or set(data[key]) - fields:
                    raise ValueError(f"final REMEDIATION {key} contains unsupported fields")
        # Family aliases may agree; none may hide a competing contract.
        scope = data.get("scope", {})
        verification = data.get("verification", {})
        for values in ([data[k] for k in ("modification_scope",) if k in data]
                       + ([scope["modify"]] if "modify" in scope else []),
                       [data[k] for k in ("affected_verification",) if k in data]
                       + [verification[k] for k in ("affected", "required") if k in verification],
                       [data[k] for k in ("verification_policy",) if data.get(k) is not None]
                       + ([verification["policy"]] if "policy" in verification else []),
                       [data[k] for k in ("full_suite_reason",) if data.get(k) is not None]
                       + ([verification["full_suite_reason"]] if "full_suite_reason" in verification else [])):
            if values and any(value != values[0] for value in values):
                raise ValueError("conflicting final REMEDIATION aliases")
        remediation = parse_remediation(raw)
        for container, key, actual in ((data, "verification_policy", remediation.verification_policy),
                                       (data, "full_suite_reason", remediation.full_suite_reason),
                                       (verification, "full_suite_reason", remediation.full_suite_reason)):
            if container.get(key) is not None and container[key] != actual:
                raise ValueError(f"final REMEDIATION {key} is not bound by its family contract")
        return raw, remediation
    except (ValueError, TypeError, RecursionError, yaml.YAMLError) as exc:
        raise AuthoringIngressError(f"invalid REMEDIATION contract: {exc}") from exc


def _direct_remediation_blob(repo: Path, sha: str, path: str) -> bytes:
    # The caller has fetched this exact authenticated canonical commit. Reject
    # symlinks and missing blobs rather than consulting a working-tree fallback.
    _, entry, _ = _git(repo, "ls-tree", sha, "--", path)
    content = _read_commit_blob(repo, sha, path)
    if not entry.startswith("100644 blob ") or not entry.endswith(f"\t{path}") or content is None:
        raise AuthoringIngressError(f"missing or invalid canonical REMEDIATION source: {path}")
    return content


def _prove_remediation_checkout(repo: Path, task_path: str, task_bytes: bytes,
                                main_sha: str, reviewed_sha: str) -> str:
    # Unified State consumes exactly this local TASK and local Runtime records.
    # Prove those inputs, never parity of the unrelated .ai directory.
    path = repo / task_path
    if any(part.is_symlink() for part in (repo / ".ai", path.parent, path)) or path.read_bytes() != task_bytes:
        raise AuthoringIngressError("local correction TASK differs from canonical contract")
    from .operator import _runtime_paths_readonly
    state = _runtime_paths_readonly(repo)
    for directory in (state.runs, state.admission_failures):
        if directory.exists() and any(directory.iterdir()):
            raise AuthoringIngressError("local Runtime observations cannot supply canonical authoring inputs")
    _, dirty, _ = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", ".", ":(exclude).ai")
    if dirty:
        raise AuthoringIngressError("dirty executable checkout cannot author REMEDIATION")
    _, head, _ = _git(repo, "rev-parse", "HEAD")
    if not any(_git(repo, "merge-base", "--is-ancestor", head, canonical, allow_fail=True)[0] == 0
               for canonical in (main_sha, reviewed_sha)):
        raise AuthoringIngressError("executable checkout has no authenticated canonical ancestry")
    return head


def _remediation_transport(repo: Path) -> tuple[str, str]:
    transport = _resolve_author_task_transport(repo)
    code, _, _ = _git(repo, "symbolic-ref", "--quiet", "HEAD", allow_fail=True)
    if code == 0:
        if _repair_transport(repo) != transport:
            raise AuthoringIngressError("correction checkout conflicts with canonical main transport")
    else:
        from .review_transport import resolve_detached_observation_remote
        if resolve_detached_observation_remote(repo) != transport[0]:
            raise AuthoringIngressError("detached correction observer conflicts with canonical main transport")
    return transport


def _read_direct_remediation_binding(envelope: IngressEnvelope, repo: Path,
                                     *, require_current: bool = True) -> _RemediationBinding:
    """Reconstruct the chosen finding through existing canonical lifecycle owners.

    Only this TASK namespace and its exact TASK contract enter the witness. Main
    movement with unchanged relevant inputs is harmless. Historical identical
    replay reads immutable sources without opening a mutation interval.
    """
    try:
        _validate_operation_identity("AUTHOR_REMEDIATION", envelope.identity)
        source_id, finding_id = envelope.identity["source_run_id"], envelope.identity["finding_id"]
        if (re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", source_id) is None
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", finding_id) is None):
            raise AuthoringIngressError("invalid canonical REMEDIATION subject identity")
        expected = envelope.expected_state
        if not isinstance(expected, Mapping) or set(expected) - {
                "expected_reviewed_sha", "reviewed_sha", "expected_review_decision_sha", "review_decision_sha"}:
            raise AuthoringIngressError("AUTHOR_REMEDIATION expected_state contains unsupported authority fields")
        expected_head = _repair_expected_sha(expected, "expected_reviewed_sha", "reviewed_sha")
        expected_decision = _repair_expected_sha(expected, "expected_review_decision_sha", "review_decision_sha", required=False)
        transport = _remediation_transport(repo)
        remote = transport[0]
        prefix = source_id.rsplit("-", 1)[0] + "-"
        patterns = ("refs/heads/main", f"refs/heads/aios/*/{prefix}*")
        refs = _query_matching_refs(repo, remote, *patterns)
        main_sha = refs.get("refs/heads/main")
        artifacts_sha = refs.get(f"refs/heads/aios/artifacts/{source_id}")
        decision_sha = refs.get(f"refs/heads/aios/review-decision/{source_id}")
        if not all((main_sha, artifacts_sha, decision_sha)):
            raise AuthoringIngressError("canonical source artifacts, review decision or main missing")
        if expected_decision is not None and expected_decision != decision_sha:
            raise AuthoringIngressError("expected canonical REVIEW decision identity mismatch")
        if refs.get(f"refs/heads/aios/review/{source_id}") != expected_head:
            raise AuthoringIngressError("canonical reviewed candidate identity mismatch")
        # Fetch failure is fatal even if stale objects happen to be cached locally.
        _git(repo, "fetch", "--no-tags", remote, *sorted(set(refs.values())))
        run_data = _json_no_dups(_direct_remediation_blob(repo, artifacts_sha, ".ai/transport/run.json"), "RUN")
        run_view = _resolve_underlying_run_view(run_data, expected_run_id=source_id)
        task_id = _extract_task_id(run_view)
        if prefix != "RUN-" + task_id.removeprefix("TASK-") + "-":
            raise AuthoringIngressError("canonical source RUN/TASK namespace mismatch")
        run = _run_from_data(run_view)
        code, kind, _ = _git(repo, "cat-file", "-t", run.base_sha, allow_fail=True)
        if code or kind != "commit" or _git(repo, "merge-base", "--is-ancestor", run.base_sha,
                                            expected_head, allow_fail=True)[0]:
            raise AuthoringIngressError("canonical source candidate does not descend from RUN base")
        task_path = f".ai/tasks/{task_id}.yaml"
        task = parse_task(_direct_remediation_blob(repo, expected_head, task_path).decode("utf-8"))
        if run.task != RunTaskReference(task.task_id, task.revision) or run.status != ACTIVE:
            raise AuthoringIngressError("canonical source RUN/TASK identity or status mismatch")
        require_same_affinity(task, run)
        result_doc = _json_no_dups(_direct_remediation_blob(repo, artifacts_sha, ".ai/transport/result.json"), "ResultPackage")
        if not isinstance(result_doc.get("evidence"), list):
            raise AuthoringIngressError("canonical ResultPackage evidence must be a list")
        package = ResultPackage(result=validate_result(result_doc["result"]),
            evidence=tuple(validate_evidence(item) for item in result_doc["evidence"]))
        if package.result.head_sha != expected_head or run.head_sha not in (None, expected_head):
            raise AuthoringIngressError("canonical source RESULT/RUN reviewed SHA mismatch")
        paths = [p for p in _ls_tree(repo, decision_sha, ".ai/reviews") if p.endswith((".yaml", ".yml"))]
        if len(paths) != 1:
            raise AuthoringIngressError("canonical review decision is missing or ambiguous")
        review_bytes = _direct_remediation_blob(repo, decision_sha, paths[0])
        yaml.load(review_bytes, Loader=_TaskLoader)
        review = parse_review(review_bytes.decode("utf-8"))
        _validate_metadata_commit(repo, decision_sha, expected_parent_sha=expected_head,
            metadata_path=f".ai/reviews/{review.review_id}.yaml", metadata_bytes=review_bytes,
            operation="canonical REVIEW decision")
        if review.verdict != "CHANGES_REQUIRED" or review.reviewed_sha != expected_head:
            raise AuthoringIngressError("source review must bind the exact CHANGES_REQUIRED candidate")
        if sum(f.id == finding_id for f in review.findings) != 1:
            raise AuthoringIngressError(f"finding {finding_id} not found in source review {review.review_id}")
        from .operator import _validated_repair_remediation_source
        repaired = _validated_repair_remediation_source(repo, task=task, run_data=run_data, run=run,
            package=package, review=review, repair=_read_commit_blob(repo, artifacts_sha, ".ai/transport/repair.json"),
            remote=remote)
        prior = None if repaired is None else repaired[1]
        if repaired is None:
            if run_data.get("kind") == "REMEDIATION":
                _, origin_remediation = _parse_remediation_run(run_data, run_id=source_id)
                prior = _resolve_prior_review(repo, remote, run_data, review)
                if prior is None:
                    raise AuthoringIngressError("canonical REMEDIATION source requires prior REVIEW")
                _validate_remediation_package(repo, source_sha=expected_head, task=task, run=run,
                    remediation=origin_remediation, prior_review=prior, result=package.result,
                    evidence=package.evidence, execution_base_sha=run.base_sha if "execution_base" in run_data else None)
            else:
                validate_result_package(task=task, run=run, result=package.result, evidence=package.evidence)
        validate_review(task=task, result=package.result, review=review, prior_review=prior)
        state = checkout = None
        if require_current:
            current_bytes = _direct_remediation_blob(repo, main_sha, task_path)
            if parse_task(current_bytes.decode("utf-8")) != task:
                raise AuthoringIngressError("canonical reviewed TASK differs from current TASK contract")
            checkout = _prove_remediation_checkout(repo, task_path, current_bytes, main_sha, expected_head)
            snapshot = _canonical_correction_snapshot("AUTHOR_REMEDIATION", envelope.identity, repo, (remote, refs))
            state = snapshot.unified_state
            if _prove_remediation_checkout(repo, task_path, current_bytes, main_sha, expected_head) != checkout:
                raise AuthoringIngressError("executable checkout moved during reconstruction")
        if _remediation_transport(repo) != transport or _query_matching_refs(repo, remote, *patterns) != refs:
            raise AuthoringIngressError("canonical REMEDIATION inputs moved during reconstruction")
        return _RemediationBinding(transport, {ref: sha for ref, sha in refs.items() if ref != "refs/heads/main"},
            task, run, package, review, refs.get(f"refs/heads/aios/remediation/{source_id}-{finding_id}"), state, checkout)
    except AuthoringIngressError:
        raise
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, yaml.YAMLError, subprocess.SubprocessError) as exc:
        raise AuthoringIngressError(f"canonical REMEDIATION subject rejected: {exc}") from exc


def _recheck_direct_remediation_binding(envelope: IngressEnvelope, repo: Path, binding: _RemediationBinding) -> None:
    try:
        if _read_direct_remediation_binding(envelope, repo, require_current=binding.correction_state is not None) != binding:
            raise AuthoringIngressError("binding is no longer current")
    except AuthoringIngressError as exc:
        raise AuthoringIngressError(f"canonical REMEDIATION freshness rejected: {exc}") from exc


def _execute_author_remediation(envelope: IngressEnvelope, repo: Path) -> IngressResult:
    payload_str, remediation = _direct_remediation_payload(envelope.payload)
    binding = _read_direct_remediation_binding(envelope, repo, require_current=False)
    source_id, finding_id = envelope.identity["source_run_id"], envelope.identity["finding_id"]
    if remediation.finding_id != finding_id:
        raise AuthoringIngressError("REMEDIATION finding_id mismatch")
    decision_sha = binding.refs[f"refs/heads/aios/review-decision/{source_id}"]
    remediation_ref = f"refs/heads/aios/remediation/{source_id}-{finding_id}"
    remediation_path = f".ai/remediations/REMEDIATION-{source_id}-{finding_id}.yaml"
    if binding.existing_sha is not None:
        # Native historical metadata bytes, modes and parent remain immutable.
        # Equivalent final family serialization is a read-only replay as well.
        try:
            content = _direct_remediation_blob(repo, binding.existing_sha, remediation_path)
            _validate_metadata_commit(repo, binding.existing_sha, expected_parent_sha=decision_sha,
                metadata_path=remediation_path, metadata_bytes=content, operation="AUTHOR_REMEDIATION replay")
            if parse_remediation(content.decode("utf-8")) != remediation:
                raise AuthoringIngressError("different final contract")
        except ValueError as exc:
            raise AuthoringIngressError("conflicting canonical remediation already exists") from exc
        _recheck_direct_remediation_binding(envelope, repo, binding)
        return IngressResult(operation="AUTHOR_REMEDIATION", status="IDEMPOTENT",
            canonical_destination=remediation_ref, canonical_sha=binding.existing_sha, replayed=True,
            detail=f"identical REMEDIATION already canonicalized for {source_id}-{finding_id}")
    try:
        validate_remediation(review=binding.review, remediation=remediation, task=binding.task)
        if remediation.action == "CODE_FIX" and not remediation.modification_scope:
            raise ValueError("CODE_FIX remediation modification scope is empty")
        if not remediation.affected_verification or set(remediation.affected_verification) - set(binding.task.verification.required):
            raise ValueError("REMEDIATION affected verification must select authorized TASK commands")
    except ValueError as exc:
        raise AuthoringIngressError(f"remediation validation failed: {exc}") from exc
    fresh = _read_direct_remediation_binding(envelope, repo)
    if replace(fresh, correction_state=None, checkout_sha=None) != binding:
        raise AuthoringIngressError("canonical REMEDIATION inputs moved before mutation admission")
    binding = fresh
    remediation_bytes = payload_str.encode("utf-8")
    root_tree = _tree_with_metadata(repo, decision_sha, remediation_path, remediation_bytes)
    _recheck_direct_remediation_binding(envelope, repo, binding)
    commit_sha = _commit_tree(repo, root_tree, [decision_sha], f"remediation: {source_id}-{finding_id}")
    _validate_metadata_commit(repo, commit_sha, expected_parent_sha=decision_sha,
        metadata_path=remediation_path, metadata_bytes=remediation_bytes, operation="AUTHOR_REMEDIATION")
    _publish_ingress_ref(repo, binding.transport[0], remediation_ref, commit_sha, expect_missing=True,
        push_destination=binding.transport[1],
        before_publish=lambda: _recheck_direct_remediation_binding(envelope, repo, binding))
    return IngressResult(operation="AUTHOR_REMEDIATION", canonical_destination=remediation_ref,
        canonical_sha=commit_sha, detail=f"canonicalized remediation for {source_id}-{finding_id}")


# ---------------------------------------------------------------------------
# Operation: AUTHOR_REPAIR
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _RepairBinding:
    transport: tuple[str, str]
    refs: Mapping[str, str]
    task: Task
    run: Run
    failure: Mapping[str, Any]
    authorization: RemoteRepairAuthorization | None
    correction_state: tuple[Any, ...]


def _repair_expected_sha(expected: Mapping[str, Any], *keys: str,
                         required: bool = True) -> str | None:
    values = [expected[key] for key in keys if key in expected]
    if not values and not required:
        return None
    if (not values or any(not isinstance(value, str)
                         or re.fullmatch(r"[0-9a-f]{40}", value.strip()) is None
                         for value in values)):
        raise AuthoringIngressError(f"expected_state must specify a valid 40-char SHA for {' or '.join(keys)}")
    shas = {value.strip() for value in values}
    if len(shas) != 1:
        raise AuthoringIngressError("conflicting REPAIR expected-state aliases")
    return shas.pop()


def _repair_transport(repo: Path) -> tuple[str, str]:
    """Bind correction writes to one configured upstream and exact destination."""
    _, branch, _ = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    code, output, _ = _git(repo, "config", "--null", "--get-all",
                           f"branch.{branch}.remote", allow_fail=True, strip_stdout=False)
    remotes = output.removesuffix("\0").split("\0")
    if (code or len(remotes) != 1 or not remotes[0] or remotes[0] == "."
            or remotes[0].startswith("-") or remotes[0] != remotes[0].strip()):
        raise AuthoringIngressError("canonical REPAIR transport is missing or ambiguous")
    remote = remotes[0]
    code, output, _ = _git(repo, "config", "--null", "--get-all",
                           f"remote.{remote}.url", allow_fail=True, strip_stdout=False)
    configured = output.removesuffix("\0").split("\0")
    if code or len(configured) != 1 or not configured[0]:
        raise AuthoringIngressError("canonical REPAIR transport URL is missing or ambiguous")
    urls = []
    for options in ((), ("--push",)):
        code, output, _ = _git(repo, "remote", "get-url", *options, "--all", remote,
                               allow_fail=True, strip_stdout=False)
        values = output.splitlines()
        if (code or len(values) != 1 or not values[0] or values[0].startswith("-")
                or values[0] != values[0].strip()):
            raise AuthoringIngressError("canonical REPAIR transport URL is missing or ambiguous")
        urls.append(values[0])
    if urls[0] != urls[1]:
        raise AuthoringIngressError("canonical REPAIR transport has conflicting fetch/push URLs")
    return remote, urls[0]


def _read_direct_repair_binding(envelope: IngressEnvelope, repo: Path,
                                *, require_current: bool = True) -> _RepairBinding:
    """Observe only this TASK's canonical correction namespace and contract.

    Main supplies the current TASK for mutation admission. Historical identical
    replay reads its immutable TASK and authorization without opening a new
    mutation interval. Unrelated main commits and other TASK namespaces are
    outside the freshness witness. Existing lifecycle/recovery owners remain.
    """
    try:
        _validate_operation_identity("AUTHOR_REPAIR", envelope.identity)
        failed_run_id = envelope.identity["failed_run_id"]
        if re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", failed_run_id) is None:
            raise AuthoringIngressError("invalid canonical failed RUN id")
        transport = _repair_transport(repo)
        remote = transport[0]
        prefix = failed_run_id.rsplit("-", 1)[0] + "-"
        patterns = ("refs/heads/main", f"refs/heads/aios/*/{prefix}*")
        refs = _query_matching_refs(repo, remote, *patterns)
        main_sha = refs.get("refs/heads/main")
        artifacts_sha = refs.get(f"refs/heads/aios/failure-artifacts/{failed_run_id}")
        if main_sha is None or artifacts_sha is None:
            raise AuthoringIngressError(f"canonical failure artifacts or main missing for {failed_run_id}")
        run_data = _json_no_dups(_authoring_blob(repo, artifacts_sha, ".ai/transport/run.json", remote), "RUN")
        run_view = _resolve_underlying_run_view(run_data, expected_run_id=failed_run_id)
        _extract_task_id(run_view)
        _extract_task_revision(run_view)
        _extract_base_sha(run_view)
        run = _run_from_data(run_view)
        _fetch_if_remote(repo, remote, run.base_sha)
        code, kind, _ = _git(repo, "cat-file", "-t", run.base_sha, allow_fail=True)
        if code or kind != "commit":
            raise AuthoringIngressError(f"RUN base_sha is not a canonical commit: {run.base_sha}")
        task_path = f".ai/tasks/{run.task.id}.yaml"
        failure = _json_no_dups(_authoring_blob(repo, artifacts_sha, ".ai/transport/failure.json", remote), "FAILURE")
        if failure.get("base_sha") is not None and failure["base_sha"] != run.base_sha:
            raise AuthoringIngressError("FAILURE base_sha does not match RUN")
        head = failure.get("failed_head_sha")
        expected_head = _repair_expected_sha(envelope.expected_state, "expected_failed_head_sha", "failed_head_sha")
        if head != expected_head:
            raise AuthoringIngressError("expected failed head SHA mismatch")
        if (refs.get(f"refs/heads/aios/failure/{failed_run_id}") != head
                or run.head_sha is not None and run.head_sha != head):
            raise AuthoringIngressError("canonical failed candidate identity is missing or mismatched")
        task = parse_task(_authoring_blob(repo, head, task_path, remote).decode("utf-8"))
        if (run.task != RunTaskReference(task.task_id, task.revision)
                or run.status not in ("ACTIVE", "FAILED")):
            raise AuthoringIngressError("canonical failed RUN TASK or status mismatch")
        require_same_affinity(task, run)
        actual = {}
        if require_current:
            current_task = parse_task(_authoring_blob(repo, main_sha, task_path, remote).decode("utf-8"))
            if parse_task((repo / task_path).read_text(encoding="utf-8")) != current_task:
                raise AuthoringIngressError("local correction TASK differs from canonical contract")
            if task != current_task:
                raise AuthoringIngressError("canonical failed TASK differs from current TASK contract")
            code, _, _ = _git(repo, "merge-base", "--is-ancestor", run.base_sha, head, allow_fail=True)
            _, changed, _ = _git(repo, "diff", "--name-only", run.base_sha, head, "--")
            actual = {"actual_descends_from_base": code == 0, "actual_changed_files": set(changed.splitlines())}
        validate_runtime_failure_binding(
            failure, run_id=failed_run_id, task_id=task.task_id, task_revision=task.revision,
            executor=run.executor, base_sha=run.base_sha, candidate_sha=head,
            modification_scope=task.scope.modify, **actual,
        )
        if (failure["candidate"]["repairable"] is not True
                or failure["candidate"]["transportable"] is not True
                or (failure.get("phase") not in ("EXECUTION", "COMPLETION_GATE", "VERIFICATION")
                    and (require_current or "phase" in failure))):
            raise AuthoringIngressError("canonical FAILURE is not repairable/transportable or has an invalid phase")
        # Some immutable historical FAILUREs predate phase serialization. Only
        # an identical existing authorization can return through this read path;
        # every new write reconstructs again with require_current=True.
        expected_failure = _repair_expected_sha(envelope.expected_state, "expected_failure_artifacts_sha",
                                                "expected_failure_sha", "failure_artifacts_sha", required=False)
        if expected_failure is not None and expected_failure != artifacts_sha:
            raise AuthoringIngressError("expected canonical FAILURE identity does not match failed RUN")

        repair_ref = f"refs/heads/aios/repair/{failed_run_id}"
        successors = f"{REPAIR_SUPERSESSION_PREFIX}{failed_run_id}/"
        authorization = None
        if repair_ref in refs:
            authorization = resolve_remote_repair_authorization(repo, failed_run_id, remote=remote)
            if refs.get(authorization.ref) != authorization.commit_sha:
                raise AuthoringIngressError("canonical repair authorization moved during observation")
            # The resolver owns continuity and exact predecessor/FAILURE identity.
            # Additionally prove every historical commit only authored metadata.
            for ref, sha in refs.items():
                if ref != repair_ref and not ref.startswith(successors):
                    continue
                content = _authoring_blob(repo, sha, ".ai/transport/repair.json", remote)
                prior = _json_no_dups(content, "current REPAIR")
                if (not isinstance(prior.get("task"), Mapping)
                        or type(prior["task"].get("revision")) is not int):
                    raise AuthoringIngressError("canonical REPAIR TASK revision is invalid")
                _validate_repair_authorization(
                    prior, failed_run_id=failed_run_id,
                    failed_head_sha=head, task=task,
                    failed_changed_files=set(failure["candidate"]["changed_files"]),
                )
                if ref == repair_ref:
                    try:
                        _validate_metadata_commit(repo, sha, expected_parent_sha=head,
                            metadata_path=".ai/transport/repair.json", metadata_bytes=content,
                            operation="AUTHOR_REPAIR replay")
                    except AuthoringIngressError as exc:
                        raise AuthoringIngressError("conflicting canonical repair already exists") from exc
                else:
                    metadata = _json_no_dups(_authoring_blob(repo, sha, REPAIR_SUPERSESSION_PATH, remote),
                                             "REPAIR supersession")
                    if type(metadata["version"]) is not int or type(metadata["authorization_revision"]) is not int:
                        raise AuthoringIngressError("canonical REPAIR supersession revision is invalid")
                    _, parents, _ = _git(repo, "show", "-s", "--format=%P", sha)
                    _, paths, _ = _git(repo, "diff", "--name-only", metadata["predecessor_repair_sha"], sha, "--")
                    if (parents.split() != [metadata["predecessor_repair_sha"]]
                            or REPAIR_SUPERSESSION_PATH not in paths.splitlines()
                            or set(paths.splitlines()) - {".ai/transport/repair.json", REPAIR_SUPERSESSION_PATH}):
                        raise AuthoringIngressError("canonical REPAIR supersession has unauthorized metadata delta")
                    for path in (".ai/transport/repair.json", REPAIR_SUPERSESSION_PATH):
                        _, entry, _ = _git(repo, "ls-tree", sha, "--", path)
                        if not entry.startswith("100644 blob ") or not entry.endswith(f"\t{path}"):
                            raise AuthoringIngressError("canonical REPAIR supersession metadata mode is invalid")
        elif any(ref.startswith(successors) for ref in refs):
            raise AuthoringIngressError("canonical REPAIR supersession has no root authorization")

        state = ()
        if require_current:
            _check_continuation_does_not_exist(repo, remote, failed_run_id, observed_refs=refs)
            snapshot = _canonical_correction_snapshot("AUTHOR_REPAIR", envelope.identity, repo, (remote, refs))
            unified = snapshot.unified_state
            if authorization is not None and unified.get("correction_sha") != authorization.commit_sha:
                raise AuthoringIngressError("canonical current REPAIR differs from correction state")
            if authorization is None and snapshot.next_action != "AUTHOR_REPAIR":
                raise AuthoringIngressError("canonical correction subject is not awaiting REPAIR authoring")
            state = tuple(unified.get(key) for key in ("task", "lifecycle_state", "next_action", "authority",
                         "run_id", "failed_run_id", "failed_head_sha", "correction_sha", "blocker"))
        if _repair_transport(repo) != transport or _query_matching_refs(repo, remote, *patterns) != refs:
            raise AuthoringIngressError("canonical REPAIR inputs moved during reconstruction")
        return _RepairBinding(transport, {ref: sha for ref, sha in refs.items() if ref != "refs/heads/main"},
                              task, run, failure, authorization, state)
    except AuthoringIngressError:
        raise
    except (ValueError, TypeError, KeyError, OSError, ReviewTransportError, subprocess.SubprocessError) as exc:
        raise AuthoringIngressError(f"canonical REPAIR subject rejected: {exc}") from exc


def _recheck_direct_repair_binding(envelope: IngressEnvelope, repo: Path, binding: _RepairBinding) -> None:
    try:
        if _read_direct_repair_binding(envelope, repo, require_current=bool(binding.correction_state)) != binding:
            raise AuthoringIngressError("binding is no longer current")
    except AuthoringIngressError as exc:
        raise AuthoringIngressError(f"canonical REPAIR freshness rejected: {exc}") from exc


def _execute_author_repair(envelope: IngressEnvelope, repo: Path) -> IngressResult:
    # A supplied handoff never enters this authority boundary, even on replay.
    allowed_expected = {"expected_failed_head_sha", "failed_head_sha", "expected_current_repair_sha",
                        "expected_repair_sha", "predecessor_repair_sha", "expected_failure_artifacts_sha",
                        "expected_failure_sha", "failure_artifacts_sha"}
    if not isinstance(envelope.expected_state, Mapping) or set(envelope.expected_state) - allowed_expected:
        raise AuthoringIngressError("AUTHOR_REPAIR expected_state contains unsupported authority fields")
    try:
        repair_data = (dict(envelope.payload) if isinstance(envelope.payload, Mapping)
                       else _json_no_dups(envelope.payload.encode("utf-8"), "REPAIR"))
        repair_id = repair_data.get("repair_id")
        task_ref = repair_data.get("task")
        if (not isinstance(repair_id, str) or re.fullmatch(r"REPAIR-[A-Za-z0-9_-]+", repair_id) is None
                or not isinstance(task_ref, Mapping) or type(task_ref.get("revision")) is not int):
            raise AuthoringIngressError("malformed final REPAIR identity")
        repair_json_bytes = json.dumps(repair_data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, AttributeError) as exc:
        raise AuthoringIngressError(f"invalid final REPAIR payload: {exc}") from exc
    binding = _read_direct_repair_binding(envelope, repo, require_current=False)
    failed_run_id = binding.run.run_id
    failed_head_sha = binding.failure["failed_head_sha"]
    artifacts_sha = binding.refs[f"refs/heads/aios/failure-artifacts/{failed_run_id}"]
    current = binding.authorization
    try:
        _validate_repair_authorization(repair_data, failed_run_id=failed_run_id,
            failed_head_sha=failed_head_sha, task=binding.task,
            failed_changed_files=set(binding.failure["candidate"]["changed_files"]))
    except ValueError as exc:
        raise AuthoringIngressError(f"repair authorization invalid: {exc}") from exc
    expected_current = _repair_expected_sha(envelope.expected_state, "expected_current_repair_sha",
                                           "expected_repair_sha", "predecessor_repair_sha", required=False)
    identical = current is not None and _json_no_dups(current.repair, "current REPAIR") == repair_data
    if current is None and expected_current is not None:
        raise AuthoringIngressError("expected current REPAIR SHA has no canonical authorization")
    if current is not None:
        # The exact successful supersession request is replayable with its old
        # predecessor; an explicit current-tip selector is also read-only replay.
        replay_shas = {current.commit_sha, current.predecessor_sha} if identical else {current.commit_sha}
        if ((current.revision > 1 or not identical or expected_current is not None)
                and expected_current not in replay_shas):
            raise AuthoringIngressError("expected current REPAIR SHA does not match canonical authorization")
        if current.revision > 1 or not identical:
            expected_failure = _repair_expected_sha(envelope.expected_state, "expected_failure_artifacts_sha",
                                                    "expected_failure_sha", "failure_artifacts_sha")
            if expected_failure != artifacts_sha:
                raise AuthoringIngressError("expected canonical FAILURE identity does not match failed RUN")
    if identical:
        _recheck_direct_repair_binding(envelope, repo, binding)
        return IngressResult(operation="AUTHOR_REPAIR", status="IDEMPOTENT",
            canonical_destination=current.ref, canonical_sha=current.commit_sha, replayed=True,
            detail=f"identical REPAIR already canonicalized for {failed_run_id}")

    fresh = _read_direct_repair_binding(envelope, repo)
    if replace(fresh, correction_state=()) != binding:
        raise AuthoringIngressError("canonical REPAIR inputs moved before mutation admission")
    binding = fresh

    # Existing Runtime owners reconstruct historical origin, actual deltas and
    # optional reusable structural state. No supplied support facts are consumed.
    from .correction_preflight import canonical_repair_strategy_state, repair_strategy_facts
    try:
        strategy = canonical_repair_strategy_state(repo, failed_run_id=failed_run_id,
            task=binding.task, failure=binding.failure, expected_refs=binding.refs)
        facts = repair_strategy_facts(task=binding.task, failure=binding.failure, state=strategy)
        if facts["action_structural_eligibility"][repair_data["action"]] != "ELIGIBLE":
            raise AuthoringIngressError("repair authorization action is ineligible for canonical FAILURE/state")
    except (ValueError, TypeError, KeyError, RuntimeError, ReviewTransportError) as exc:
        raise AuthoringIngressError(f"repair authorization strategy rejected: {exc}") from exc
    _recheck_direct_repair_binding(envelope, repo, binding)
    if current is None:
        destination_ref = f"refs/heads/aios/repair/{failed_run_id}"
        root_tree = _tree_with_metadata(repo, failed_head_sha, ".ai/transport/repair.json", repair_json_bytes)
        commit_sha = _commit_tree(repo, root_tree, [failed_head_sha], f"repair authorization for {failed_run_id}")
        _validate_metadata_commit(repo, commit_sha, expected_parent_sha=failed_head_sha,
            metadata_path=".ai/transport/repair.json", metadata_bytes=repair_json_bytes, operation="AUTHOR_REPAIR")
    else:
        revision = current.revision + 1
        destination_ref = f"{REPAIR_SUPERSESSION_PREFIX}{failed_run_id}/{revision}"
        supersession = {"format": REPAIR_SUPERSESSION_FORMAT, "version": 1,
            "failed_run_id": failed_run_id, "authorization_revision": revision,
            "predecessor_repair_sha": current.commit_sha, "failure_artifacts_sha": artifacts_sha}
        supersession_bytes = json.dumps(supersession, sort_keys=True, separators=(",", ":")).encode("utf-8")
        repair_tree = _tree_with_metadata(repo, current.commit_sha, ".ai/transport/repair.json",
                                          repair_json_bytes, replace_existing=True)
        staged = _commit_tree(repo, repair_tree, [current.commit_sha], f"staged repair supersession for {failed_run_id}")
        root_tree = _tree_with_metadata(repo, staged, REPAIR_SUPERSESSION_PATH,
                                        supersession_bytes, replace_existing=True)
        commit_sha = _commit_tree(repo, root_tree, [current.commit_sha], f"repair authorization revision {revision} for {failed_run_id}")
        if (_read_commit_blob(repo, commit_sha, ".ai/transport/repair.json") != repair_json_bytes
                or _read_commit_blob(repo, commit_sha, REPAIR_SUPERSESSION_PATH) != supersession_bytes):
            raise AuthoringIngressError("AUTHOR_REPAIR supersession content mismatch")
    # The immutable destination's expected-missing CAS reserves this revision.
    # Recheck the subject at the writer, including after local CAS and before push.
    try:
        _publish_ingress_ref(repo, binding.transport[0], destination_ref, commit_sha, expect_missing=True,
            push_destination=binding.transport[1],
            before_publish=lambda: _recheck_direct_repair_binding(envelope, repo, binding))
    except AuthoringIngressError as admission_error:
        # One read classifies an identical concurrent winner. This grants no
        # retry or replacement authority, and all other subject witnesses agree.
        try:
            fresh = _read_direct_repair_binding(envelope, repo)
        except AuthoringIngressError:
            raise admission_error
        winner = fresh.authorization
        if (winner is None or winner.ref != destination_ref
                or winner.revision != (1 if current is None else current.revision + 1)
                or winner.predecessor_sha != (None if current is None else current.commit_sha)
                or _json_no_dups(winner.repair, "current REPAIR") != repair_data
                or fresh.transport != binding.transport or fresh.task != binding.task
                or fresh.run != binding.run or fresh.failure != binding.failure
                or fresh.refs != {**binding.refs, destination_ref: winner.commit_sha}):
            raise
        return IngressResult(operation="AUTHOR_REPAIR", status="IDEMPOTENT",
            canonical_destination=winner.ref, canonical_sha=winner.commit_sha, replayed=True,
            detail=f"identical concurrent REPAIR already canonicalized for {failed_run_id}")
    return IngressResult(operation="AUTHOR_REPAIR", canonical_destination=destination_ref,
        canonical_sha=commit_sha, detail=f"canonicalized repair authorization for {failed_run_id}")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _reserve_review_submission(repo: Path, remote: str | None, run_id: str,
                                candidate_sha: str, decision_sha: str, *,
                                artifacts_sha: str | None = None,
                                decision_exists: bool = False) -> PublicationReservation | None:
    candidate_ref = f"refs/heads/aios/review/{run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"
    main_sha = _resolve_ref_sha(repo, "refs/heads/main", remote)
    canonical_artifacts = _resolve_ref_sha(repo, artifacts_ref, remote)
    if main_sha is None or canonical_artifacts is None:
        raise AuthoringIngressError("WRITER_SCOPE_GAP: SUBMIT_REVIEW canonical main/artifacts boundary unavailable")
    if artifacts_sha is not None and canonical_artifacts != artifacts_sha:
        raise AuthoringIngressError("STALE_REVIEW_BINDING: review artifacts moved before reservation")
    _fetch_if_remote(repo, remote, main_sha)
    _fetch_if_remote(repo, remote, canonical_artifacts)
    recovery_bytes = _read_commit_blob(repo, canonical_artifacts, ".ai/transport/publication-recovery.json")
    if recovery_bytes is not None:
        from .publication import validate_publication_recovery
        if remote is None:
            raise AuthoringIngressError("canonical remote is required for publication recovery")
        try:
            run = _run_from_data(_json_no_dups(_read_commit_blob(repo, canonical_artifacts,
                ".ai/transport/run.json"), "RUN"))
            task = parse_task(_read_commit_blob(repo, candidate_sha,
                f".ai/tasks/{run.task.id}.yaml").decode("utf-8"))
            data = _json_no_dups(_read_commit_blob(repo, canonical_artifacts,
                ".ai/transport/result.json"), "ResultPackage")
            package = ResultPackage(validate_result(data["result"]), tuple(validate_evidence(e) for e in data["evidence"]))
            validate_publication_recovery(repo, remote=remote, raw=recovery_bytes,
                run=run, task=task, candidate_sha=candidate_sha, package=package,
                current_main_sha=main_sha)
        except (PublicationError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise AuthoringIngressError(f"stale publication recovery review reservation: {exc}") from exc
    if decision_exists:
        code, _, _ = _git(repo, "merge-base", "--is-ancestor", candidate_sha, main_sha, allow_fail=True)
        if code == 0:
            return None  # Immutable replay after publication does not open a new interval.
        if code != 1:
            raise AuthoringIngressError("cannot classify exact review replay ancestry")
    try:
        reservation = reserve_publication(
            repo, remote, kind="REVIEW_TO_PUBLICATION", subject=run_id,
            source_sha=candidate_sha, main_sha=main_sha, decision_sha=decision_sha,
            artifacts_sha=canonical_artifacts,
        )
        check_publication_reservation(repo, remote, reservation)
    except PublicationError as exc:
        raise AuthoringIngressError(f"{exc.report.cause}: {exc}") from exc
    if (_resolve_ref_sha(repo, candidate_ref, remote) != candidate_sha
            or _resolve_ref_sha(repo, artifacts_ref, remote) != canonical_artifacts):
        raise AuthoringIngressError("STALE_REVIEW_BINDING: exact review lineage moved during reservation")
    if _resolve_ref_sha(repo, "refs/heads/main", remote) != main_sha:
        raise AuthoringIngressError("CONCURRENT_MAIN_MOVEMENT: canonical main moved during review reservation")
    if decision_exists and _resolve_ref_sha(repo, f"refs/heads/aios/review-decision/{run_id}", remote) != decision_sha:
        raise AuthoringIngressError("STALE_REVIEW_BINDING: durable review decision moved during replay")
    return reservation


_AUTHORING_FLOWS = {
    "AUTHOR_REMEDIATION": "REMEDIATION_AUTHORING",
    "AUTHOR_REPAIR": "REPAIR_AUTHORING",
}
# The Human-approved prospective identity is immutable for this migration.
# Canonical policy must still authorize it; registry presence is not selection.
_ACTIVE_AUDIT_PROFILE_REF = {
    "id": "aios-adaptive-brain-audit-v1",
    "version": 1,
    "digest": "982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718",
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


def _canonical_authoring_review(repo: Path, sha: str, remote: str | None) -> Review:
    """Read the sole REVIEW document at an already selected immutable identity."""
    _fetch_if_remote(repo, remote, sha)
    paths = [p for p in _ls_tree(repo, sha, ".ai/reviews") if p.endswith((".yaml", ".yml"))]
    if len(paths) != 1:
        raise AuthoringIngressError("canonical finding REVIEW is missing or ambiguous")
    return parse_review(_authoring_blob(repo, sha, paths[0], remote).decode("utf-8"))


def _canonical_correction_snapshot(
    operation: str, identity: Mapping[str, Any], repo: Path, inputs: _AuthoringRefs,
) -> BrainSyncSnapshot:
    """Reconstruct EXACT_CORRECTION_SUBJECT_LINEAGE_V1 without planning selection.

    Selectors locate only canonical correction artifacts. Their RUN supplies
    the TASK identity, which must match canonical main and Unified State.
    This observation neither creates authority nor mutates lifecycle state.
    The caller proves canonical inputs and rechecks refs before any mutation.
    """
    from .brain_sync import BrainSyncSnapshot, _resolve_repository_name

    if operation not in _AUTHORING_FLOWS:
        raise AuthoringIngressError("exact correction observation requires a correction operation")
    _validate_operation_identity(operation, identity)
    remote, refs = inputs
    main_sha = refs["refs/heads/main"]
    repair = operation == "AUTHOR_REPAIR"
    run_id = identity["failed_run_id" if repair else "source_run_id"]
    family = "failure-artifacts" if repair else "artifacts"
    artifacts_sha = refs.get(f"refs/heads/aios/{family}/{run_id}")
    if artifacts_sha is None:
        raise AuthoringIngressError("canonical correction artifacts ref is missing")
    run_doc = _json_no_dups(
        _authoring_blob(repo, artifacts_sha, ".ai/transport/run.json", remote), "RUN")
    run_view = _resolve_underlying_run_view(run_doc, expected_run_id=run_id)
    task_id = _extract_task_id(run_view)
    run = _run_from_data(run_view)
    task_bytes = _authoring_blob(repo, main_sha, f".ai/tasks/{task_id}.yaml", remote)
    task = parse_task(task_bytes.decode("utf-8"))
    selected = {"id": task.task_id, "revision": task.revision}
    if run.task != RunTaskReference(task.task_id, task.revision):
        raise AuthoringIngressError("correction RUN does not match canonical TASK id/revision")
    require_same_affinity(task, run)
    lineage = {
        "policy": "EXACT_CORRECTION_SUBJECT_LINEAGE_V1", "operation": operation,
        "selectors": dict(identity), "task": selected, "artifacts_sha": artifacts_sha,
        "executor": run.executor, "base_sha": run.base_sha,
    }
    if repair:
        failure = _json_no_dups(
            _authoring_blob(repo, artifacts_sha, ".ai/transport/failure.json", remote), "FAILURE")
        head = failure.get("failed_head_sha")
        if (not isinstance(head, str) or re.fullmatch(r"[0-9a-f]{40}", head) is None
                or refs.get(f"refs/heads/aios/failure/{run_id}") != head
                or (run.head_sha is not None and run.head_sha != head)):
            raise AuthoringIngressError("canonical failed candidate identity is missing or mismatched")
        validate_runtime_failure_binding(
            failure, run_id=run_id, task_id=task.task_id, task_revision=task.revision,
            executor=run.executor, base_sha=run.base_sha, candidate_sha=head,
            modification_scope=task.scope.modify,
        )
        if not all(failure["candidate"][flag] is True for flag in ("repairable", "transportable")):
            raise AuthoringIngressError("canonical FAILURE is not repairable/transportable")
        lineage.update(failed_run_id=run_id, failed_head_sha=head)
    else:
        decision_sha = refs.get(f"refs/heads/aios/review-decision/{run_id}")
        if decision_sha is None:
            raise AuthoringIngressError("canonical finding REVIEW ref is missing")
        review = _canonical_authoring_review(repo, decision_sha, remote)
        result_doc = _json_no_dups(
            _authoring_blob(repo, artifacts_sha, ".ai/transport/result.json", remote), "ResultPackage")
        package = ResultPackage(
            result=validate_result(result_doc["result"]),
            evidence=tuple(validate_evidence(item) for item in result_doc["evidence"]),
        )
        head = package.result.head_sha
        if (refs.get(f"refs/heads/aios/review/{run_id}") != head or review.reviewed_sha != head
                or (run.head_sha is not None and run.head_sha != head)):
            raise AuthoringIngressError("canonical reviewed candidate identity mismatch")
        # Preserve the source-family validator used by ordinary ingress,
        # including prior correction provenance for repaired/remediated RUNs.
        from .operator import _validated_repair_remediation_source
        _fetch_if_remote(repo, remote, head)
        _validated_repair_remediation_source(
            repo, task=task, run_data=run_doc, run=run, package=package, review=review,
            repair=_read_commit_blob(repo, artifacts_sha, ".ai/transport/repair.json"),
        )
        if (review.verdict != "CHANGES_REQUIRED"
                or len([f for f in review.findings if f.id == identity["finding_id"]]) != 1):
            raise AuthoringIngressError("canonical correction finding is absent from CHANGES_REQUIRED REVIEW")
        lineage.update(source_run_id=run_id, finding_id=identity["finding_id"],
                       review_id=review.review_id, reviewed_sha=head, review_decision_sha=decision_sha)

    unified = observe_unified_state(task.task_id, repo=repo).as_dict()
    allowed = {"AUTHOR_REPAIR", "EXECUTE_REPAIR"} if repair else {"AUTHOR_REMEDIATION"}
    if (unified.get("task") != selected or unified.get("blocker") is not None
            or unified.get("next_action") not in allowed):
        raise AuthoringIngressError("canonical correction subject differs from Unified State")
    if repair:
        if (unified.get("run_id") != run_id or unified.get("failed_run_id") != run_id
                or unified.get("failed_head_sha") != head):
            raise AuthoringIngressError("REPAIR target differs from canonical failed RUN")
    else:
        target = {key: lineage[key] for key in ("source_run_id", "review_id", "finding_id", "reviewed_sha")}
        if sum(item == target for item in unified.get("outstanding_findings", [])) != 1:
            raise AuthoringIngressError("REMEDIATION target is not canonically outstanding")
        # Frontier audits also bind every outstanding REVIEW's immutable ref.
        lineage["outstanding_reviews"] = []
        for item in unified["outstanding_findings"]:
            sha = refs.get(f"refs/heads/aios/review-decision/{item['source_run_id']}")
            if sha is None:
                raise AuthoringIngressError("canonical outstanding REVIEW ref is missing")
            lineage["outstanding_reviews"].append({**item, "review_decision_sha": sha})
    return BrainSyncSnapshot(
        repository={"root": str(repo), "name": _resolve_repository_name(repo),
                    "main_sha": main_sha, "remote": remote},
        main_sha=main_sha, roadmap={}, selection_status="SELECTED", selected_task=selected,
        unified_state=unified, lifecycle_state=unified["lifecycle_state"],
        next_action=unified["next_action"], authority=unified["authority"],
        blocker=None, correction_subject=lineage,
    )


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
    subject = observed.get("correction_subject")
    fresh = (
        _canonical_correction_snapshot("AUTHOR_REPAIR", {"failed_run_id": failed_run_id}, repo, (remote, refs))
        if subject is not None else observe_brain_sync(repo=repo)
    )
    if (fresh.main_sha != main_sha or fresh.next_action != "EXECUTE_REPAIR"
            or fresh.selected_task != selected or fresh.unified_state != unified
            or fresh.blocker != observed.get("blocker") or fresh.correction_subject != subject):
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
    if envelope.operation not in _AUTHORING_FLOWS:
        raise AuthoringIngressError("audited authoring is only valid for correction operations")
    from .brain_context import compose_brain_work_context, resolve_flow
    from .decision_packet import compile_decision_packet
    from .brain_audit import parse_profile_registry, profile_ref
    from .brain_return_contract import parse_return_contract_registry, select_return_contract

    remote, refs = _authoring_refs(repo)
    main_sha = refs["refs/heads/main"]
    _prove_authoring_inputs(repo, main_sha, remote)
    snapshot = _canonical_correction_snapshot(envelope.operation, envelope.identity, repo, (remote, refs))
    subject = snapshot.correction_subject
    if envelope.operation == "AUTHOR_REPAIR":
        expected_head = _get_expected_sha(envelope.expected_state, "expected_failed_head_sha", "failed_head_sha")
        if expected_head != subject["failed_head_sha"]:
            raise AuthoringIngressError("expected failed head SHA mismatch")
    else:
        expected_head = _get_expected_sha(envelope.expected_state, "expected_reviewed_sha", "reviewed_sha")
        if expected_head != subject["reviewed_sha"]:
            raise AuthoringIngressError("expected reviewed SHA mismatch")
    flow = _AUTHORING_FLOWS[envelope.operation]
    request = None
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
            review = _canonical_authoring_review(repo, sha, remote)
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
    # Strictly decode the selected policy, without imposing TASK mapping-key
    # rules on unrelated historical roadmap sections (which have numeric keys).
    roadmap = yaml.compose(_authoring_blob(repo, main_sha, ".ai/roadmap-state.yaml", remote), Loader=yaml.SafeLoader)
    policy_nodes = [value for key, value in roadmap.value
                    if isinstance(key, yaml.ScalarNode) and key.tag == "tag:yaml.org,2002:str"
                    and key.value == "adaptive_brain_audit_v1_priority_20261012"] if isinstance(roadmap, yaml.MappingNode) else []
    if len(policy_nodes) != 1:
        raise AuthoringIngressError("missing or ambiguous canonical prospective audit profile policy")
    policy = yaml.load(yaml.serialize(policy_nodes[0]), Loader=_TaskLoader)
    cutover = policy.get("exclusive_prospective_cutover_20261012") if isinstance(policy, Mapping) else None
    if not isinstance(cutover, Mapping):
        raise AuthoringIngressError("missing canonical prospective audit profile policy")
    active_ref = {"id": policy.get("active_audit_profile"), "version": policy.get("profile_version"),
                  "digest": policy.get("profile_digest")}
    cutover_ref = {"id": cutover.get("profile_id"), "version": cutover.get("profile_version"),
                   "digest": cutover.get("profile_digest")}
    if (active_ref != _ACTIVE_AUDIT_PROFILE_REF or cutover_ref != active_ref
            or type(active_ref["version"]) is not int or type(cutover_ref["version"]) is not int
            or policy.get("status") != "ADAPTIVE_V1_EXCLUSIVE_PROSPECTIVE_ACTIVE_AFTER_HUMAN_CUTOVER"
            or policy.get("authority") != "HUMAN_BRAIN_PLANNING"
            or cutover.get("status") != "ACTIVE_FROM_THIS_CANONICAL_PLANNING_COMMIT_PROSPECTIVE_ONLY"
            or cutover.get("authority") != "EXPLICIT_HUMAN_BRAIN_SEMANTIC_POLICY_DECISION"
            or cutover.get("human_intent") != "V1_ONLY_NO_PARALLEL_V3_SEMANTIC_REASONING_FOR_NEW_SUBJECTS"
            or cutover.get("selection") != "EXACT_PROFILE_ID_VERSION_DIGEST_ONLY_NO_V3_FALLBACK"
            or cutover.get("legacy_protection") != "V2_V3_IN_FLIGHT_EXACT_MATCH_OR_FAIL_CLOSED_RESTART_HISTORY_READ_ONLY"
            or cutover.get("prohibited") != "NO_PARALLEL_V3_V1_REASONING_NO_SECOND_SEMANTIC_VERDICT_NO_NEW_ROUTER"
            or cutover.get("new_flows") != ["ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING", "REPAIR_AUTHORING"]
            or cutover.get("required_stages") != ["CONSTRUCT", "ADVERSARIAL_AUDIT_AND_RECONCILE"]
            or type(cutover.get("mandatory_lenses")) is not int or cutover["mandatory_lenses"] != 8):
        raise AuthoringIngressError("canonical prospective audit profile policy mismatch: exact V1 cutover required")
    profiles = parse_profile_registry(_authoring_blob(repo, main_sha, ".ai/brain-audit-profiles.yaml", remote))["profiles"]
    selected_profiles = [item for item in profiles if profile_ref(item) == active_ref]
    if len(selected_profiles) != 1:
        raise AuthoringIngressError("canonical prospective audit profile mismatch: exact V1 material required")
    profile, = selected_profiles
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
    from .brain_audit import profile_ref, validate_stage2

    if envelope.audited_handoff is None:
        raise AuthoringIngressError("new authoring mutation requires audited_handoff")
    try:
        _validate_handoff_shape(envelope.audited_handoff)
        packet, profile, contract, refs = _compose_authoring_packet(envelope, repo)
        selected_ref = profile_ref(profile)
        if any(envelope.audited_handoff[stage].get("audit_profile_ref") != selected_ref
               for stage in ("stage1", "stage2")):
            raise AuthoringIngressError("audited authoring profile mismatch: new mutations require the exact V1 profile in both stages")
        audit = validate_stage2(packet, profile, envelope.audited_handoff["stage1"], envelope.audited_handoff["stage2"])
        if audit["outcome"] != "CANDIDATE":
            raise AuthoringIngressError("audited authoring requires Stage-2 CANDIDATE")
        candidate = _authoring_family_body(envelope.operation, audit["handoff_candidate"], packet, repo)
        if candidate != _authoring_family_body(envelope.operation, envelope.payload, packet, repo):
            raise AuthoringIngressError("audited candidate differs from canonical family payload")
        return packet.packet_fingerprint, contract["return_contract_ref"], refs
    except (ValueError, TypeError, KeyError, OSError, RecursionError, yaml.YAMLError,
            BrainSyncError, ReviewTransportError, subprocess.SubprocessError) as exc:
        raise AuthoringIngressError(f"audited authoring rejected: {exc}") from exc


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
    repo: Path, remote: str | None, failed_run_id: str,
    *, observed_refs: Mapping[str, str] | None = None,
) -> None:
    patterns = [
        f"refs/heads/aios/failure-artifacts/*",
        f"refs/heads/aios/artifacts/*",
    ]
    refs = (_query_matching_refs(repo, remote, *patterns) if observed_refs is None else
            {ref: sha for ref, sha in observed_refs.items()
             if ref.startswith(("refs/heads/aios/failure-artifacts/", "refs/heads/aios/artifacts/"))})
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


def _resolve_author_task_transport(repo: Path) -> tuple[str, str]:
    """Bind TASK writes to main's upstream, independent of checkout/projections."""
    def one_config(key: str) -> str:
        code, output, _ = _git(
            repo, "config", "--null", "--get-all", key,
            strip_stdout=False, allow_fail=True,
        )
        values = output.removesuffix("\0").split("\0")
        if code != 0 or len(values) != 1 or not values[0]:
            raise AuthoringIngressError(
                f"canonical main transport authority is missing or ambiguous: {key}"
            )
        return values[0]

    remote = one_config("branch.main.remote")
    if remote == "." or remote.startswith("-") or remote != remote.strip():
        raise AuthoringIngressError("canonical main transport requires a named upstream remote")
    if one_config("branch.main.merge") != "refs/heads/main":
        raise AuthoringIngressError("canonical main transport has a conflicting upstream ref")
    # A remote's presence (including origin) alone grants no TASK write authority.
    one_config(f"remote.{remote}.url")
    urls = []
    for options in ((), ("--push",)):
        code, output, _ = _git(
            repo, "remote", "get-url", *options, "--all", remote,
            strip_stdout=False, allow_fail=True,
        )
        values = output.splitlines()
        if code != 0 or len(values) != 1 or not values[0]:
            raise AuthoringIngressError("canonical main transport URL is missing or ambiguous")
        urls.append(values[0])
    if urls[0] != urls[1]:
        raise AuthoringIngressError("canonical main transport has conflicting fetch/push URLs")
    return remote, urls[0]


def _resolve_author_task_main(repo: Path, remote: str) -> str:
    """Require exact upstream main; unavailable transport never falls back locally."""
    ref = "refs/heads/main"
    code, output, _ = _git(repo, "ls-remote", "--refs", remote, ref, allow_fail=True)
    entries = [line.split() for line in output.splitlines()]
    if (code != 0 or len(entries) != 1 or len(entries[0]) != 2
            or entries[0][1] != ref or re.fullmatch(r"[0-9a-f]{40}", entries[0][0]) is None):
        raise AuthoringIngressError("canonical main transport ref is unavailable, missing or ambiguous")
    main_sha = entries[0][0]
    code, local_sha, _ = _git(repo, "rev-parse", "--verify", "--quiet", ref, allow_fail=True)
    if code != 0 or local_sha != main_sha:
        raise AuthoringIngressError(
            "canonical main transport conflicts with local main (stale state or concurrency)"
        )
    return main_sha


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
    reservation: PublicationReservation | None = None,
    before_publish: Callable[[], None] | None = None,
    push_destination: str | None = None,
) -> None:
    if expect_missing and expected_old_sha is not None:
        raise AuthoringIngressError(
            "ingress ref publication cannot expect both a missing and existing ref"
        )
    if push_destination is not None and (
        remote is None or not expect_missing or before_publish is None
        or not ref.startswith(("refs/heads/aios/repair/", REPAIR_SUPERSESSION_PREFIX,
                               "refs/heads/aios/remediation/"))
    ):
        raise AuthoringIngressError("captured push destination requires a bound immutable correction writer")

    critical = ref == "refs/heads/main"
    if ref.startswith("refs/heads/aios/review-decision/"):
        paths = _ls_tree(repo, new_sha, ".ai/reviews")
        paths = [path for path in paths if path.endswith((".yaml", ".yml"))]
        if len(paths) != 1:
            raise AuthoringIngressError("review decision writer requires exactly one REVIEW")
        content = _read_commit_blob(repo, new_sha, paths[0])
        if content is None:
            raise AuthoringIngressError("review decision writer cannot read exact REVIEW")
        critical = parse_review(content.decode("utf-8")).verdict == "PASS"
    if critical:
        if reservation is None:
            raise AuthoringIngressError(
                f"WRITER_SCOPE_GAP: src/aios_renew/authoring_ingress.py::_publish_ingress_ref({ref}) requires shared reservation"
            )
        identity = reservation.identity
        if ref == "refs/heads/main":
            matches = (identity["kind"] == "MAIN_MUTATION" and identity["source_sha"] == new_sha
                       and identity["main_sha"] == expected_old_sha)
        else:
            matches = (identity["kind"] == "REVIEW_TO_PUBLICATION"
                       and identity["subject"] == ref.rsplit("/", 1)[1]
                       and identity["decision_sha"] == new_sha)
        if not matches:
            raise AuthoringIngressError("RESERVATION_INVALID: ingress writer identity mismatch")
        try:
            check_publication_reservation(repo, remote, reservation)
            if _resolve_ref_sha(repo, "refs/heads/main", remote) != identity["main_sha"]:
                raise AuthoringIngressError("CONCURRENT_MAIN_MOVEMENT: reserved canonical main moved before ingress write")
        except PublicationError as exc:
            raise AuthoringIngressError(f"{exc.report.cause}: {exc}") from exc

    if before_publish is not None:
        before_publish()

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
        if before_publish is not None:
            try:
                before_publish()
            except Exception:
                if expect_missing:
                    _git(repo, "update-ref", "-d", ref, new_sha, allow_fail=True)
                elif expected_old_sha is not None:
                    _git(repo, "update-ref", ref, expected_old_sha, new_sha, allow_fail=True)
                raise
        if critical:
            try:
                check_publication_reservation(repo, remote, reservation)
                if _resolve_ref_sha(repo, "refs/heads/main", remote) != identity["main_sha"]:
                    raise AuthoringIngressError("CONCURRENT_MAIN_MOVEMENT: reserved canonical main moved before ingress push")
            except (PublicationError, AuthoringIngressError) as exc:
                if expect_missing:
                    _git(repo, "update-ref", "-d", ref, new_sha, allow_fail=True)
                elif expected_old_sha is not None:
                    _git(repo, "update-ref", ref, expected_old_sha, new_sha, allow_fail=True)
                if isinstance(exc, PublicationError):
                    raise AuthoringIngressError(f"{exc.report.cause}: {exc}") from exc
                raise
        args = ["push", "--porcelain", "--no-tags"]
        if expect_missing:
            args.append(f"--force-with-lease={ref}:")
        elif expected_old_sha is not None:
            args.append(f"--force-with-lease={ref}:{expected_old_sha}")
        args.extend([remote if push_destination is None else push_destination, f"{new_sha}:{ref}"])
        code, _, err = _git(repo, *args, allow_fail=True)
        if code != 0:
            if expect_missing:
                _git(repo, "update-ref", "-d", ref, new_sha, allow_fail=True)
            elif expected_old_sha is not None:
                _git(repo, "update-ref", ref, expected_old_sha, new_sha, allow_fail=True)
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
