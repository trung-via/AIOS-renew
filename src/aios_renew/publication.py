"""Deterministic publication of one canonical PASS review decision."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .artifacts import (
    ResultPackage,
    validate_evidence,
    validate_result,
    validate_result_package,
)
from .correction_frontier import CorrectionFrontier, CorrectionFrontierError
from .correction_integration import (
    derive_integration_identity,
    integration_ref_name,
)
from .review import (
    Remediation,
    Review,
    parse_remediation,
    parse_review,
    validate_remediation,
    validate_review,
)
from .review_transport import (
    ReviewTransportError,
    resolve_remote_repair_authorization,
    validate_runtime_failure_binding,
)
from .run import ACTIVE, Run, RunTaskReference
from .task import parse_task


_RUN_ID = re.compile(r"RUN-[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA = re.compile(r"[0-9a-f]{40}")
_DECISION_PREFIX = "refs/heads/aios/review-decision/"
_MAX_DECISIONS = 1024
PUBLICATION_CAUSES = frozenset({
    "NONE", "PUBLICATION_GATE_FAILED", "MALFORMED_LINEAGE", "STALE_CONTROL_BINDING",
    "STALE_REVIEW_BINDING", "CONCURRENT_MAIN_MOVEMENT", "MAIN_CAS_FAILED",
    "AMBIGUOUS_ANCESTRY", "MERGE_CONFLICT", "MERGE_CALCULATION_FAILED",
    "RECOVERY_SCOPE_ESCAPE", "FRESH_EXACT_REVIEW_REQUIRED",
    "COMPETING_REVIEWED_SOURCE", "DECISION_SET_CHANGED",
    "RESERVATION_CONTENDED", "RESERVATION_STALE", "RESERVATION_INVALID",
    "RESERVATION_CAS_FAILED", "RESERVATION_UNAVAILABLE", "WRITER_SCOPE_GAP",
    "RECOVERY_MATERIAL_OVERLAP", "RECOVERY_UNSUPPORTED_MODE",
    "RECOVERY_TASK_CHANGED", "HUMAN_KERNEL_AMENDMENT_REQUIRED",
    "RECOVERY_ALREADY_ADMITTED", "RECOVERY_NOT_ACTIVATED",
})

# One repository-wide ordering boundary, independent of workflow/job identity.
# Expiry never grants takeover authority: a stale owner is an actionable conflict.
PUBLICATION_RESERVATION_REF = "refs/heads/aios/publication-reservation/main"
_RESERVATION_PATH = "reservation.json"
_RESERVATION_SECONDS = 900


@dataclass(frozen=True)
class PublicationReservation:
    token_sha: str
    identity: Mapping[str, Any]
    created_at: int
    expires_at: int


def _reservation_tip(repo: Path, remote: str | None, subject: str) -> str | None:
    if remote is not None:
        try:
            return _single_optional_remote_sha(
                repo, remote, PUBLICATION_RESERVATION_REF, run_id=subject,
            )
        except PublicationError as exc:
            raise _failed(subject, "canonical publication reservation unavailable",
                          cause="RESERVATION_UNAVAILABLE") from exc
    code, sha, _ = _git(repo, "rev-parse", "--verify", "--quiet",
                       PUBLICATION_RESERVATION_REF, allow_fail=True)
    if code == 1 and not sha:
        return None
    if code or _SHA.fullmatch(sha) is None:
        raise _failed(subject, "publication reservation unavailable",
                      cause="RESERVATION_UNAVAILABLE")
    return sha


def _reservation_identity(kind: str, subject: str, source_sha: str, main_sha: str,
                          decision_sha: str | None, artifacts_sha: str | None) -> dict:
    valid_subject = (isinstance(subject, str) and re.fullmatch(
        r"(?:RUN|TASK)-[A-Za-z0-9][A-Za-z0-9._-]*", subject) is not None)
    valid_shas = all(isinstance(sha, str) and _SHA.fullmatch(sha)
                     for sha in (source_sha, main_sha))
    review = kind == "REVIEW_TO_PUBLICATION"
    if (not valid_subject or not valid_shas or not isinstance(kind, str)
            or kind not in {"REVIEW_TO_PUBLICATION", "MAIN_MUTATION"}
            or review and (not subject.startswith("RUN-") or not all(
                isinstance(sha, str) and _SHA.fullmatch(sha)
                for sha in (decision_sha, artifacts_sha)))
            or not review and (not subject.startswith("TASK-")
                               or decision_sha is not None or artifacts_sha is not None)):
        raise _failed(str(subject), "malformed publication reservation identity",
                      cause="RESERVATION_INVALID")
    return dict(kind=kind, subject=subject, source_sha=source_sha,
                main_sha=main_sha, decision_sha=decision_sha, artifacts_sha=artifacts_sha)


def _read_reservation(repo: Path, remote: str | None, tip: str,
                      subject: str) -> PublicationReservation:
    try:
        if remote is not None:
            _fetch_object(repo, remote, tip, run_id=subject)
        content = _read_blob(repo, tip, _RESERVATION_PATH, run_id=subject)
        if len(content) > 4096:
            raise ValueError("reservation exceeds bound")
        document = _mapping(_json_no_duplicates(content, document="reservation"), "reservation")
        if set(document) != {"version", "identity", "created_at", "expires_at"} or type(document["version"]) is not int or document["version"] != 1:
            raise ValueError("reservation schema mismatch")
        identity = _mapping(document["identity"], "reservation.identity")
        if set(identity) != {"kind", "subject", "source_sha", "main_sha", "decision_sha", "artifacts_sha"}:
            raise ValueError("reservation identity schema mismatch")
        validated = _reservation_identity(**identity)
        created, expires = document["created_at"], document["expires_at"]
        if (type(created) is not int or type(expires) is not int or created < 0
                or expires - created != _RESERVATION_SECONDS):
            raise ValueError("reservation lifetime mismatch")
        reservation = PublicationReservation(tip, validated, created, expires)
    except (PublicationError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise _failed(subject, "canonical publication reservation is malformed",
                      cause="RESERVATION_INVALID") from exc
    now = int(time.time())
    if now < created or now >= expires:
        raise _failed(subject, "publication reservation owner is stale; no automatic takeover",
                      cause="RESERVATION_STALE")
    return reservation


def _reservation_object(repo: Path, document: dict, subject: str) -> str:
    # Fixed author and timestamps make one exact identity/lifetime reproducible.
    def write(args: tuple[str, ...], content: bytes) -> str:
        import os
        env = dict(os.environ)
        env.update(GIT_AUTHOR_NAME="AIOS Coordination", GIT_AUTHOR_EMAIL="coordination@aios.invalid",
                   GIT_COMMITTER_NAME="AIOS Coordination", GIT_COMMITTER_EMAIL="coordination@aios.invalid",
                   GIT_AUTHOR_DATE=f"@{document['created_at']} +0000",
                   GIT_COMMITTER_DATE=f"@{document['created_at']} +0000")
        try:
            result = subprocess.run(("git", "-C", str(repo), *args), input=content,
                                    capture_output=True, timeout=30, check=False, env=env)
            sha = result.stdout.decode("ascii").strip()
            if result.returncode or _SHA.fullmatch(sha) is None:
                raise ValueError("coordination object write failed")
            return sha
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired) as exc:
            raise _failed(subject, "cannot materialize publication reservation",
                          cause="RESERVATION_UNAVAILABLE") from exc
    blob = write(("hash-object", "-w", "--stdin"), json.dumps(
        document, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    tree = write(("mktree",), f"100644 blob {blob}\t{_RESERVATION_PATH}\n".encode("ascii"))
    return write(("commit-tree", tree), b"publication ordering reservation\n")


def reserve_publication(repo: Path, remote: str | None, *, kind: str, subject: str,
                        source_sha: str, main_sha: str, decision_sha: str | None = None,
                        artifacts_sha: str | None = None) -> PublicationReservation:
    """Acquire once or resume the same exact owner; never wait, steal or select work."""
    if remote is not None and (not isinstance(remote, str) or not remote or remote.startswith("-")):
        raise _failed(str(subject), "invalid publication reservation remote",
                      cause="RESERVATION_INVALID")
    identity = _reservation_identity(kind, subject, source_sha, main_sha, decision_sha, artifacts_sha)
    tip = _reservation_tip(repo, remote, subject)
    if tip is not None:
        reservation = _read_reservation(repo, remote, tip, subject)
        if reservation.identity != identity:
            if all(reservation.identity[key] == value for key, value in identity.items()
                   if key != "main_sha"):
                raise _failed(subject, "reserved canonical main moved",
                              cause="CONCURRENT_MAIN_MOVEMENT")
            raise _failed(subject, "canonical publication boundary has a different exact owner",
                          cause="RESERVATION_CONTENDED")
        check_publication_reservation(repo, remote, reservation)
        return reservation
    created = int(time.time())
    expires = created + _RESERVATION_SECONDS
    token = _reservation_object(repo, dict(version=1, identity=identity,
                                          created_at=created, expires_at=expires), subject)
    if remote is None:
        code, _, _ = _git(repo, "update-ref", PUBLICATION_RESERVATION_REF, token,
                          "0" * 40, allow_fail=True)
    else:
        code, _, _ = _git(repo, "push", "--porcelain", "--no-tags",
                          f"--force-with-lease={PUBLICATION_RESERVATION_REF}:", remote,
                          f"{token}:{PUBLICATION_RESERVATION_REF}", allow_fail=True)
    if code:
        # One bounded observation handles identical concurrent acquisition.
        tip = _reservation_tip(repo, remote, subject)
        if tip is not None:
            reservation = _read_reservation(repo, remote, tip, subject)
            if reservation.identity == identity:
                check_publication_reservation(repo, remote, reservation)
                return reservation
            raise _failed(subject, "publication reservation contention", cause="RESERVATION_CONTENDED")
        raise _failed(subject, "publication reservation acquisition CAS failed",
                      cause="RESERVATION_CAS_FAILED")
    reservation = PublicationReservation(token, identity, created, expires)
    check_publication_reservation(repo, remote, reservation)
    return reservation


def check_publication_reservation(repo: Path, remote: str | None,
                                  reservation: PublicationReservation) -> None:
    subject = reservation.identity["subject"]
    now = int(time.time())
    if now < reservation.created_at or now >= reservation.expires_at:
        raise _failed(subject, "publication reservation expired; no automatic takeover",
                      cause="RESERVATION_STALE")
    if _reservation_tip(repo, remote, subject) != reservation.token_sha:
        raise _failed(subject, "publication reservation ownership changed",
                      cause="RESERVATION_CAS_FAILED")
    if _read_reservation(repo, remote, reservation.token_sha, subject) != reservation:
        raise _failed(subject, "publication reservation token/identity mismatch",
                      cause="RESERVATION_INVALID")


def release_publication_reservation(repo: Path, remote: str | None,
                                    reservation: PublicationReservation) -> None:
    """Delete only the exact owner token; repeated release is a no-op."""
    subject = reservation.identity["subject"]
    if _reservation_tip(repo, remote, subject) is None:
        return
    check_publication_reservation(repo, remote, reservation)
    if remote is None:
        code, _, _ = _git(repo, "update-ref", "-d", PUBLICATION_RESERVATION_REF,
                          reservation.token_sha, allow_fail=True)
    else:
        code, _, _ = _git(repo, "push", "--porcelain", "--no-tags",
                          f"--force-with-lease={PUBLICATION_RESERVATION_REF}:{reservation.token_sha}",
                          remote, f":{PUBLICATION_RESERVATION_REF}", allow_fail=True)
    if code and _reservation_tip(repo, remote, subject) is not None:
        raise _failed(subject, "publication reservation release CAS failed",
                      cause="RESERVATION_CAS_FAILED")


def _finish_review_reservation(repo: Path, remote: str, *, run_id: str,
                               reviewed_sha: str, decision_sha: str, artifacts_sha: str,
                               main_sha: str, contained: bool = False) -> None:
    try:
        _finish_review_reservation_owner(repo, remote, run_id=run_id, reviewed_sha=reviewed_sha,
                                         decision_sha=decision_sha, artifacts_sha=artifacts_sha,
                                         main_sha=main_sha, contained=contained)
    except PublicationError as exc:
        raise _failed(run_id, str(exc), reviewed_sha=reviewed_sha, prior_main_sha=main_sha,
                      cause=exc.report.cause) from exc


def _finish_review_reservation_owner(repo: Path, remote: str, *, run_id: str,
                                     reviewed_sha: str, decision_sha: str, artifacts_sha: str,
                                     main_sha: str, contained: bool) -> None:
    tip = _reservation_tip(repo, remote, run_id)
    if tip is None:
        return
    reservation = _read_reservation(repo, remote, tip, run_id)
    identity = reservation.identity
    if (identity["kind"] == "REVIEW_TO_PUBLICATION" and identity["subject"] == run_id
            and identity["source_sha"] == reviewed_sha and identity["decision_sha"] == decision_sha
            and identity["artifacts_sha"] == artifacts_sha):
        if identity["main_sha"] != main_sha and not contained:
            raise _failed(run_id, "reserved canonical main moved before terminal classification",
                          cause="CONCURRENT_MAIN_MOVEMENT")
        release_publication_reservation(repo, remote, reservation)


def finish_main_publication_reservation(repo: Path, remote: str, *, subject: str,
                                        published_sha: str) -> None:
    """A proven identical TASK replay can complete an interrupted owner release."""
    tip = _reservation_tip(repo, remote, subject)
    if tip is None:
        return
    reservation = _read_reservation(repo, remote, tip, subject)
    if (reservation.identity["kind"] == "MAIN_MUTATION"
            and reservation.identity["subject"] == subject
            and reservation.identity["source_sha"] == published_sha):
        release_publication_reservation(repo, remote, reservation)


class PublicationError(RuntimeError):
    """Raised after a publication gate fails closed."""

    def __init__(self, message: str, report: PublicationReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class PublicationRecovery:
    """Mechanical tree observation only; it is never a reviewed source commit."""

    merge_base_sha: str
    tree_sha: str
    publication_eligible: bool = False


@dataclass(frozen=True)
class PublicationBlocker:
    source_run: str
    decision_sha: str
    reviewed_sha: str


@dataclass(frozen=True)
class PublicationLineage:
    decision_sha: str
    artifacts_sha: str
    reservation_sha: str | None = None


@dataclass(frozen=True)
class PublicationReport:
    """Attributable outcome emitted for every publication attempt."""

    source_run: str
    reviewed_sha: str
    prior_main_sha: str
    outcome: str
    detail: str
    cause: str = "NONE"
    recovery: PublicationRecovery | None = None
    blocker: PublicationBlocker | None = None
    lineage: PublicationLineage | None = None

    @property
    def classification(self) -> str:
        """One closed classification, independent of transport/terminal wording."""
        if self.outcome in {"PUBLISHED", "PUBLISHABLE"}:
            return "PUBLISHABLE"
        if self.outcome in {"ALREADY_PUBLISHED", "ALREADY_INCLUDED"}:
            return "ALREADY_INCLUDED"
        if self.recovery is not None or self.outcome == "AWAITING_REVIEW":
            return "CLEAN_RECOVERY_ELIGIBLE"
        if self.cause in {"COMPETING_REVIEWED_SOURCE", "RESERVATION_CONTENDED"}:
            return "COMPETING_SOURCE"
        if self.cause in {"STALE_CONTROL_BINDING", "STALE_REVIEW_BINDING",
                          "CONCURRENT_MAIN_MOVEMENT", "DECISION_SET_CHANGED",
                          "RESERVATION_STALE", "RESERVATION_CAS_FAILED",
                          "RECOVERY_TASK_CHANGED", "RECOVERY_ALREADY_ADMITTED"}:
            return "STALE_BINDING"
        return "CONFLICT_OR_UNKNOWN"


def _git(
    repo: Path, *args: str, allow_fail: bool = False
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=False,
            check=False,
            timeout=30,
        )
        stdout = completed.stdout.decode("utf-8", errors="strict")
        if "-z" not in args:
            stdout = stdout.strip()
        stderr = completed.stderr.decode("utf-8", errors="strict").strip()
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        if allow_fail:
            return 128, "", "Git acquisition failed"
        raise RuntimeError(f"Git command failed: {exc}") from exc
    if completed.returncode and not allow_fail:
        detail = stderr or stdout or f"exit {completed.returncode}"
        raise RuntimeError(f"Git command failed: {detail}")
    return completed.returncode, stdout, stderr


def _failed(
    run_id: str,
    message: str,
    *,
    reviewed_sha: str = "UNKNOWN",
    prior_main_sha: str = "UNKNOWN",
    cause: str = "PUBLICATION_GATE_FAILED",
) -> PublicationError:
    message = message[:512]
    return PublicationError(
        message,
        PublicationReport(
            source_run=run_id,
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
            outcome="FAILED",
            detail=message[:512],
            cause=cause,
        ),
    )


def _recovery_required(
    run_id: str, *, reviewed_sha: str, prior_main_sha: str, cause: str,
    detail: str, recovery: PublicationRecovery | None = None,
    blocker: PublicationBlocker | None = None,
) -> PublicationError:
    detail = detail[:512]
    return PublicationError(
        detail,
        PublicationReport(
            source_run=run_id,
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
            outcome=("RECOVERY_REVIEW_REQUIRED" if recovery else "RECOVERY_BLOCKED"),
            detail=detail[:512],
            cause=cause,
            recovery=recovery,
            blocker=blocker,
        ),
    )


def _single_remote_sha(
    repo: Path, remote: str, ref: str, *, run_id: str
) -> str:
    code, output, _ = _git(
        repo, "ls-remote", "--refs", remote, ref, allow_fail=True
    )
    if code:
        raise _failed(run_id, f"cannot query canonical ref {ref}")
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if (
        len(lines) != 1
        or len(lines[0]) != 2
        or lines[0][1] != ref
        or _SHA.fullmatch(lines[0][0]) is None
    ):
        raise _failed(run_id, f"canonical ref {ref} is missing or ambiguous")
    return lines[0][0]


def _fetch_object(repo: Path, remote: str, sha: str, *, run_id: str) -> None:
    code, _, _ = _git(
        repo, "fetch", "--no-tags", remote, sha, allow_fail=True
    )
    if code:
        raise _failed(run_id, f"cannot fetch canonical object {sha}")
    code, kind, _ = _git(repo, "cat-file", "-t", sha, allow_fail=True)
    if code or kind != "commit":
        raise _failed(run_id, f"canonical object {sha} is not a commit")


def _read_blob(repo: Path, commit_sha: str, path: str, *, run_id: str) -> bytes:
    code, output, _ = _git(
        repo, "show", f"{commit_sha}:{path}", allow_fail=True
    )
    if code:
        raise _failed(run_id, f"canonical content missing: {path}")
    return output.encode("utf-8")


def _read_optional_blob(repo: Path, commit_sha: str, path: str) -> bytes | None:
    code, output, _ = _git(
        repo, "show", f"{commit_sha}:{path}", allow_fail=True
    )
    return output.encode("utf-8") if code == 0 else None


def _json_no_duplicates(source: bytes, *, document: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{document} contains duplicate key: {key}")
            result[key] = value
        return result

    return json.loads(source.decode("utf-8", errors="strict"), object_pairs_hook=object_pairs)


def _mapping(value: Any, document: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{document} must be a mapping")
    return value


def _run_from_data(data: Any, document: str) -> Run:
    root = _mapping(data, document)
    task_data = _mapping(root.get("task"), f"{document}.task")
    return Run(
        run_id=root["run_id"],
        task=RunTaskReference(
            id=task_data["id"], revision=task_data["revision"]
        ),
        executor=root["executor"],
        base_sha=root["base_sha"],
        workspace=root["workspace"],
        head_sha=root.get("head_sha"),
        status=root["status"],
    )


def _single_optional_remote_sha(
    repo: Path, remote: str, ref: str, *, run_id: str
) -> str | None:
    code, output, _ = _git(
        repo, "ls-remote", "--refs", remote, ref, allow_fail=True
    )
    if code:
        raise _failed(run_id, f"cannot query canonical ref {ref}")
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    if (
        len(lines) != 1
        or len(lines[0]) != 2
        or lines[0][1] != ref
        or _SHA.fullmatch(lines[0][0]) is None
    ):
        raise _failed(run_id, f"canonical ref {ref} is missing or ambiguous")
    return lines[0][0]


_CANONICAL_PREDECESSOR_FIELDS = frozenset(
    {"source_run_id", "review_id", "finding_id", "reviewed_sha"}
)
_CANONICAL_EXECUTION_BASE_FIELDS = frozenset({"run_id", "candidate_sha"})
_CANONICAL_INTEGRATED_EXECUTION_BASE_FIELDS = frozenset({
    "version",
    "kind",
    "cumulative_tip_run_id",
    "cumulative_tip_candidate_sha",
    "authorized_main_sha",
    "integration_candidate_sha",
    "integration_id",
})


@dataclass(frozen=True)
class RemediationPredecessor:
    """Exact bounded predecessor identity for one canonical REMEDIATION RUN."""

    source_run_id: str
    review_id: str
    finding_id: str
    reviewed_sha: str


@dataclass(frozen=True)
class RemediationExecutionBase:
    run_id: str
    candidate_sha: str
    version: int = 1
    kind: str = "CUMULATIVE"
    cumulative_tip_run_id: str | None = None
    cumulative_tip_candidate_sha: str | None = None
    authorized_main_sha: str | None = None
    integration_candidate_sha: str | None = None
    integration_id: str | None = None
    is_integrated: bool = False


def _parse_remediation_execution_base(
    data: Any, document: str = "REMEDIATION execution_base"
) -> RemediationExecutionBase:
    root = _mapping(data, document)
    root_keys = set(root)
    if root_keys == _CANONICAL_EXECUTION_BASE_FIELDS:
        run_id = root.get("run_id")
        candidate_sha = root.get("candidate_sha")
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ValueError(f"{document} RUN identity is invalid")
        if not isinstance(candidate_sha, str) or _SHA.fullmatch(candidate_sha) is None:
            raise ValueError(f"{document} candidate SHA is invalid")
        return RemediationExecutionBase(run_id=run_id, candidate_sha=candidate_sha)
    if _CANONICAL_INTEGRATED_EXECUTION_BASE_FIELDS.issubset(root_keys) and root_keys.issubset(
        _CANONICAL_INTEGRATED_EXECUTION_BASE_FIELDS
        | {"run_id", "candidate_sha", "authorized_main_sha", "current_main_sha", "expected_main_sha"}
    ):
        version = root.get("version")
        if version != 1 or isinstance(version, bool):
            raise ValueError(f"{document} version is invalid")
        kind = root.get("kind")
        if kind not in ("INTEGRATED", "INTEGRATION"):
            raise ValueError(f"{document} kind is invalid")
        tip_run_id = root.get("cumulative_tip_run_id")
        if not isinstance(tip_run_id, str) or _RUN_ID.fullmatch(tip_run_id) is None:
            raise ValueError(f"{document} cumulative_tip_run_id is invalid")
        tip_candidate_sha = root.get("cumulative_tip_candidate_sha")
        if not isinstance(tip_candidate_sha, str) or _SHA.fullmatch(tip_candidate_sha) is None:
            raise ValueError(f"{document} cumulative_tip_candidate_sha is invalid")
        main_sha = root.get("authorized_main_sha") or root.get("current_main_sha") or root.get("expected_main_sha")
        if not isinstance(main_sha, str) or _SHA.fullmatch(main_sha) is None:
            raise ValueError(f"{document} authorized_main_sha is invalid")
        int_candidate_sha = root.get("integration_candidate_sha")
        if not isinstance(int_candidate_sha, str) or _SHA.fullmatch(int_candidate_sha) is None:
            raise ValueError(f"{document} integration_candidate_sha is invalid")
        int_id = root.get("integration_id")
        if not isinstance(int_id, str) or not int_id:
            raise ValueError(f"{document} integration_id is invalid")
        if "run_id" in root and root["run_id"] != tip_run_id:
            raise ValueError(f"{document} run_id does not match cumulative_tip_run_id")
        if "candidate_sha" in root and root["candidate_sha"] != int_candidate_sha:
            raise ValueError(f"{document} candidate_sha does not match integration_candidate_sha")
        return RemediationExecutionBase(
            run_id=tip_run_id,
            candidate_sha=int_candidate_sha,
            version=version,
            kind=kind,
            cumulative_tip_run_id=tip_run_id,
            cumulative_tip_candidate_sha=tip_candidate_sha,
            authorized_main_sha=main_sha,
            integration_candidate_sha=int_candidate_sha,
            integration_id=int_id,
            is_integrated=True,
        )
    raise ValueError(f"{document} fields do not match the contract")


def _parse_remediation_predecessor(
    data: Any, document: str = "REMEDIATION predecessor"
) -> RemediationPredecessor:
    root = _mapping(data, document)
    if set(root).difference(_CANONICAL_PREDECESSOR_FIELDS):
        raise ValueError(f"{document} contains unexpected fields")
    source_run_id = root.get("source_run_id")
    review_id = root.get("review_id")
    finding_id = root.get("finding_id")
    reviewed_sha = root.get("reviewed_sha")
    if not isinstance(source_run_id, str) or _RUN_ID.fullmatch(source_run_id) is None:
        raise ValueError(f"{document} source RUN identity is invalid")
    if not isinstance(review_id, str) or not review_id or "/" in review_id or "\\" in review_id:
        raise ValueError(f"{document} source REVIEW identity is invalid")
    if not isinstance(finding_id, str) or not finding_id or "/" in finding_id or "\\" in finding_id:
        raise ValueError(f"{document} selected finding identity is invalid")
    if not isinstance(reviewed_sha, str) or _SHA.fullmatch(reviewed_sha) is None:
        raise ValueError(f"{document} reviewed_sha is invalid")
    return RemediationPredecessor(
        source_run_id=source_run_id,
        review_id=review_id,
        finding_id=finding_id,
        reviewed_sha=reviewed_sha,
    )


def _parse_remediation_run(
    run_data: Mapping[str, Any], *, run_id: str
) -> tuple[Run, Remediation]:
    if "execution_base" in run_data and "predecessor" not in run_data:
        base = _parse_remediation_execution_base(run_data["execution_base"])
        if base.is_integrated:
            raise ValueError("integrated execution_base requires canonical predecessor")
    execution = _mapping(run_data.get("execution"), "REMEDIATION.execution")
    run = _run_from_data(execution.get("run"), "REMEDIATION.execution.run")
    if run.run_id != run_id:
        raise ValueError("RUN-ID mismatch between decision ref and REMEDIATION RUN")

    remediation_data = _mapping(
        execution.get("remediation"), "REMEDIATION.execution.remediation"
    )
    remediation = parse_remediation(json.dumps(remediation_data))
    original_constraints = execution.get("original_constraints", [])
    if (
        not isinstance(original_constraints, list)
        or not all(isinstance(item, str) for item in original_constraints)
        or tuple(original_constraints) != remediation.constraints
    ):
        raise ValueError(
            "REMEDIATION original_constraints do not match its constraints"
        )
    return run, remediation


def _validate_execution_base(
    repo: Path,
    *,
    remote: str,
    publication_run_id: str,
    run_data: Mapping[str, Any],
    run: Run,
    semantic_reviewed_sha: str,
) -> RemediationExecutionBase:
    if "execution_base" not in run_data:
        if run.base_sha != semantic_reviewed_sha:
            raise ValueError("legacy REMEDIATION base_sha does not match reviewed_sha")
        predecessor = _parse_remediation_predecessor(run_data.get("predecessor"))
        return RemediationExecutionBase(predecessor.source_run_id, semantic_reviewed_sha)
    base = _parse_remediation_execution_base(run_data["execution_base"])
    if base.candidate_sha != run.base_sha:
        raise ValueError("execution_base candidate SHA does not match RUN base_sha")
    code, _, _ = _git(
        repo, "merge-base", "--is-ancestor", semantic_reviewed_sha,
        base.candidate_sha, allow_fail=True,
    )
    if code:
        raise ValueError("execution_base does not descend from semantic reviewed SHA")

    if base.is_integrated:
        expected_integration_id = derive_integration_identity(
            run.task.id,
            run.task.revision,
            base.cumulative_tip_run_id,
            base.cumulative_tip_candidate_sha,
            base.authorized_main_sha,
        )
        if base.integration_id != expected_integration_id:
            raise ValueError("integrated execution_base integration_id does not match canonical binding")

        current_main_sha = _single_remote_sha(
            repo, remote, "refs/heads/main", run_id=publication_run_id
        )
        _fetch_object(repo, remote, current_main_sha, run_id=publication_run_id)
        code, _, _ = _git(
            repo,
            "merge-base",
            "--is-ancestor",
            base.authorized_main_sha,
            current_main_sha,
            allow_fail=True,
        )
        if code:
            raise ValueError(
                "integrated execution_base authorized main is not canonical history"
            )

        expected_ref = integration_ref_name(base.integration_id)
        remote_int_sha = _single_remote_sha(
            repo, remote, expected_ref, run_id=publication_run_id
        )
        if remote_int_sha != base.integration_candidate_sha:
            raise ValueError("remote integration ref does not match integration candidate SHA")
        _fetch_object(repo, remote, base.integration_candidate_sha, run_id=publication_run_id)

        code, kind, _ = _git(repo, "cat-file", "-t", base.integration_candidate_sha, allow_fail=True)
        if code or kind != "commit":
            raise ValueError("integration candidate is not a commit")
        code, parents_out, _ = _git(repo, "rev-parse", f"{base.integration_candidate_sha}^@", allow_fail=True)
        if code:
            raise ValueError("cannot inspect integration candidate parents")
        parents = parents_out.split()
        if (
            len(parents) != 2
            or parents[0] != base.cumulative_tip_candidate_sha
            or parents[1] != base.authorized_main_sha
        ):
            raise ValueError("integration candidate parents do not match [cumulative_tip, authorized_main]")

        code, mb_out, _ = _git(
            repo, "merge-base", "--all", base.cumulative_tip_candidate_sha, base.authorized_main_sha, allow_fail=True
        )
        if code or len([line for line in mb_out.splitlines() if line.strip()]) != 1:
            raise ValueError("ambiguous or missing merge base between cumulative tip and authorized main")

        code, tree_out, _ = _git(
            repo, "merge-tree", "--write-tree", base.cumulative_tip_candidate_sha, base.authorized_main_sha, allow_fail=True
        )
        if code:
            raise ValueError("merge tree calculation failed or detected conflict")
        expected_tree = tree_out.strip().splitlines()[0]
        code, actual_tree, _ = _git(repo, "rev-parse", f"{base.integration_candidate_sha}^{{tree}}", allow_fail=True)
        if code or actual_tree != expected_tree:
            raise ValueError("integration candidate tree does not match clean merge tree")

        cumulative_run_id = base.cumulative_tip_run_id
        expected_cumulative_sha = base.cumulative_tip_candidate_sha
    else:
        cumulative_run_id = base.run_id
        expected_cumulative_sha = base.candidate_sha

    artifacts_sha = _single_remote_sha(
        repo, remote, f"refs/heads/aios/artifacts/{cumulative_run_id}",
        run_id=publication_run_id,
    )
    _fetch_object(repo, remote, artifacts_sha, run_id=publication_run_id)
    base_run_data = _mapping(
        _json_no_duplicates(
            _read_blob(repo, artifacts_sha, ".ai/transport/run.json", run_id=publication_run_id),
            document="execution-base RUN",
        ),
        "execution-base RUN",
    )
    if base_run_data.get("kind") == "REMEDIATION":
        base_run, _ = _parse_remediation_run(base_run_data, run_id=cumulative_run_id)
    elif "kind" not in base_run_data:
        base_run = _run_from_data(base_run_data, "execution-base RUN")
    else:
        raise ValueError("execution-base RUN kind is invalid")
    if base_run.run_id != cumulative_run_id or base_run.task != run.task:
        raise ValueError("execution-base RUN identity mismatch")
    base_result_data = _mapping(
        _json_no_duplicates(
            _read_blob(repo, artifacts_sha, ".ai/transport/result.json", run_id=publication_run_id),
            document="execution-base ResultPackage",
        ),
        "execution-base ResultPackage",
    )
    if validate_result(base_result_data.get("result")).head_sha != expected_cumulative_sha:
        raise ValueError("execution-base candidate does not match canonical RESULT")
    return base


def _remediation_lineage(
    run_data: Mapping[str, Any], *, run_id: str
) -> tuple[Run, Remediation, Review]:
    run, remediation = _parse_remediation_run(run_data, run_id=run_id)
    execution = _mapping(run_data.get("execution"), "REMEDIATION.execution")
    finding_data = _mapping(
        execution.get("finding"), "REMEDIATION.execution.finding"
    )
    prior_review = parse_review(
        json.dumps(
            {
                "review_id": execution["review_id"],
                "reviewed_sha": remediation.reviewed_sha,
                "mode": "PRIMARY",
                "verdict": "CHANGES_REQUIRED",
                "acceptance": {finding_data["basis"]: "FAIL"},
                "findings": [finding_data],
            }
        )
    )
    return run, remediation, prior_review


def _validate_predecessor_lineage(
    repo: Path,
    *,
    remote: str,
    publication_run_id: str,
    run_data: Mapping[str, Any],
    run: Run,
    remediation: Remediation,
    task: Any,
) -> Review:
    pred = _parse_remediation_predecessor(run_data.get("predecessor"))
    execution = _mapping(run_data.get("execution"), "REMEDIATION.execution")

    if pred.reviewed_sha != remediation.reviewed_sha:
        raise ValueError("predecessor reviewed_sha does not match REMEDIATION reviewed_sha")
    _validate_execution_base(
        repo,
        remote=remote,
        publication_run_id=publication_run_id,
        run_data=run_data,
        run=run,
        semantic_reviewed_sha=pred.reviewed_sha,
    )
    if pred.review_id != execution.get("review_id"):
        raise ValueError("predecessor review_id does not match REMEDIATION review_id")
    if pred.finding_id != remediation.finding_id:
        raise ValueError("predecessor finding_id does not match REMEDIATION finding_id")
    execution_finding = _mapping(execution.get("finding"), "REMEDIATION.execution.finding")
    if pred.finding_id != execution_finding.get("id"):
        raise ValueError("predecessor finding_id does not match REMEDIATION finding.id")

    pred_artifacts_ref = f"refs/heads/aios/artifacts/{pred.source_run_id}"
    pred_artifacts_sha = _single_remote_sha(repo, remote, pred_artifacts_ref, run_id=publication_run_id)
    _fetch_object(repo, remote, pred_artifacts_sha, run_id=publication_run_id)
    pred_run_bytes = _read_blob(repo, pred_artifacts_sha, ".ai/transport/run.json", run_id=publication_run_id)
    pred_run_data = _mapping(_json_no_duplicates(pred_run_bytes, document="predecessor RUN"), "predecessor RUN")
    if "kind" not in pred_run_data:
        pred_run = _run_from_data(pred_run_data, "predecessor RUN")
    elif pred_run_data.get("kind") == "REMEDIATION":
        pred_execution = _mapping(pred_run_data.get("execution"), "predecessor REMEDIATION.execution")
        pred_run = _run_from_data(pred_execution.get("run"), "predecessor REMEDIATION.execution.run")
    else:
        raise ValueError("unknown predecessor RUN kind")

    if pred_run.run_id != pred.source_run_id:
        raise ValueError("predecessor RUN run_id mismatch")
    if pred_run.task.id != task.task_id or pred_run.task.revision != task.revision:
        raise ValueError("predecessor RUN task mismatch")
    if pred_run.status != ACTIVE:
        raise ValueError("predecessor RUN status is not ACTIVE")

    pred_result_bytes = _read_blob(repo, pred_artifacts_sha, ".ai/transport/result.json", run_id=publication_run_id)
    pred_result_data = _mapping(_json_no_duplicates(pred_result_bytes, document="predecessor ResultPackage"), "predecessor ResultPackage")
    pred_result = validate_result(pred_result_data["result"])
    if pred_result.head_sha != pred.reviewed_sha:
        raise ValueError("predecessor RESULT head_sha does not match reviewed_sha")

    pred_review_ref = f"refs/heads/aios/review/{pred.source_run_id}"
    pred_review_sha = _single_remote_sha(repo, remote, pred_review_ref, run_id=publication_run_id)
    if pred_review_sha != pred.reviewed_sha:
        raise ValueError("predecessor candidate review ref does not match reviewed_sha")

    remediation_ref = f"refs/heads/aios/remediation/{pred.source_run_id}-{pred.finding_id}"
    decision_ref = f"refs/heads/aios/review-decision/{pred.source_run_id}"
    review_commit_sha = _single_optional_remote_sha(repo, remote, remediation_ref, run_id=publication_run_id)
    if review_commit_sha is None:
        review_commit_sha = _single_optional_remote_sha(repo, remote, decision_ref, run_id=publication_run_id)
    if review_commit_sha is None:
        raise ValueError(f"canonical predecessor review ref is missing for {pred.source_run_id}")

    _fetch_object(repo, remote, review_commit_sha, run_id=publication_run_id)
    code, tree, _ = _git(repo, "ls-tree", "-r", "--name-only", review_commit_sha, "--", ".ai/reviews", allow_fail=True)
    if code:
        raise ValueError(f"cannot inspect predecessor review decision for {pred.source_run_id}")
    review_paths = [
        path
        for path in tree.splitlines()
        if path.startswith(".ai/reviews/")
        and path.endswith((".yaml", ".yml"))
    ]
    if len(review_paths) != 1:
        raise ValueError(
            f"predecessor review decision must contain exactly one REVIEW document for {pred.source_run_id}"
        )
    review_bytes = _read_blob(repo, review_commit_sha, review_paths[0], run_id=publication_run_id)
    prior_review = parse_review(review_bytes.decode("utf-8", errors="strict"))

    if prior_review.review_id != pred.review_id:
        raise ValueError("predecessor REVIEW review_id mismatch")
    if prior_review.reviewed_sha != pred.reviewed_sha:
        raise ValueError("predecessor REVIEW reviewed_sha mismatch")
    if prior_review.verdict != "CHANGES_REQUIRED":
        raise ValueError("predecessor REVIEW verdict is not CHANGES_REQUIRED")
    if not any(f.id == pred.finding_id for f in prior_review.findings):
        raise ValueError("predecessor REVIEW does not contain selected finding")

    from .operator import _validated_repair_remediation_source
    from .review_transport import _read_remote_blob
    evidence = pred_result_data.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("predecessor ResultPackage evidence is invalid")
    _validated_repair_remediation_source(
        repo, remote=remote, task=task, run_data=pred_run_data, run=pred_run,
        package=ResultPackage(result=pred_result, evidence=tuple(validate_evidence(item) for item in evidence)),
        review=prior_review,
        repair=_read_remote_blob(repo, remote, pred_artifacts_sha, ".ai/transport/repair.json"),
    )
    return prior_review


def _derive_publication_frontier(
    repo: Path,
    *,
    remote: str,
    publication_run_id: str,
    run_data: Mapping[str, Any],
    delta_review: Review,
) -> CorrectionFrontier:
    current_pred = _parse_remediation_predecessor(run_data["predecessor"])
    steps: list[tuple[str, RemediationPredecessor, Review]] = [
        (publication_run_id, current_pred, delta_review)
    ]
    if "execution_base" in run_data:
        current_base = _parse_remediation_execution_base(run_data["execution_base"])
    else:
        current_base = RemediationExecutionBase(
            current_pred.source_run_id, current_pred.reviewed_sha
        )
    seen = {publication_run_id}

    while True:
        source_id = current_base.run_id
        if source_id in seen:
            raise ValueError("cyclic cumulative correction lineage")
        seen.add(source_id)

        pred_artifacts_ref = f"refs/heads/aios/artifacts/{source_id}"
        pred_artifacts_sha = _single_remote_sha(
            repo, remote, pred_artifacts_ref, run_id=publication_run_id
        )
        _fetch_object(repo, remote, pred_artifacts_sha, run_id=publication_run_id)
        pred_run_bytes = _read_blob(
            repo, pred_artifacts_sha, ".ai/transport/run.json", run_id=publication_run_id
        )
        pred_run_data = _mapping(
            _json_no_duplicates(pred_run_bytes, document="predecessor RUN"),
            "predecessor RUN",
        )
        pred_result = validate_result(
            _mapping(
                _json_no_duplicates(
                    _read_blob(
                        repo, pred_artifacts_sha, ".ai/transport/result.json",
                        run_id=publication_run_id,
                    ),
                    document="predecessor ResultPackage",
                ),
                "predecessor ResultPackage",
            ).get("result")
        )
        expected_head = (
            current_base.cumulative_tip_candidate_sha
            if current_base.is_integrated
            else current_base.candidate_sha
        )
        if pred_result.head_sha != expected_head:
            raise ValueError("cumulative execution-base candidate mismatch")

        # A successful REPAIR keeps its own RUN identity. Its DELTA semantics
        # continue the exact failed REMEDIATION origin, including integrated bases.
        semantic_run_data = pred_run_data
        semantic_run_id = source_id
        repair_bytes = _read_optional_blob(repo, pred_artifacts_sha, ".ai/transport/repair.json")
        if repair_bytes is not None:
            lineage = _mapping(_json_no_duplicates(repair_bytes, document="source REPAIR"), "source REPAIR")
            repair_seen: set[str] = set()
            while True:
                failed_id = lineage.get("failed_run_id")
                if not isinstance(failed_id, str) or failed_id in repair_seen:
                    raise ValueError("cyclic or malformed successful REPAIR source lineage")
                repair_seen.add(failed_id)
                failed_sha = _single_remote_sha(repo, remote,
                    f"refs/heads/aios/failure-artifacts/{failed_id}", run_id=publication_run_id)
                _fetch_object(repo, remote, failed_sha, run_id=publication_run_id)
                origin = _mapping(_json_no_duplicates(_read_blob(repo, failed_sha,
                    ".ai/transport/run.json", run_id=publication_run_id), document="REPAIR origin RUN"),
                    "REPAIR origin RUN")
                if origin.get("kind") == "REMEDIATION":
                    semantic_run_data, semantic_run_id = origin, failed_id
                    break
                previous = _read_optional_blob(repo, failed_sha, ".ai/transport/repair.json")
                if previous is None:
                    break
                lineage = _mapping(_json_no_duplicates(previous, document="prior REPAIR"), "prior REPAIR")
        source_pred = (
            _parse_remediation_predecessor(semantic_run_data["predecessor"])
            if "predecessor" in semantic_run_data
            else None
        )
        remediation_ref = (
            f"refs/heads/aios/remediation/{source_id}-{source_pred.finding_id}"
            if source_pred is not None
            else f"refs/heads/aios/remediation/{source_id}-{current_pred.finding_id}"
        )
        decision_ref = f"refs/heads/aios/review-decision/{source_id}"
        review_commit_sha = _single_optional_remote_sha(
            repo, remote, remediation_ref, run_id=publication_run_id
        )
        if review_commit_sha is None:
            review_commit_sha = _single_optional_remote_sha(
                repo, remote, decision_ref, run_id=publication_run_id
            )
        if review_commit_sha is None:
            raise ValueError(f"canonical predecessor review ref is missing for {source_id}")

        _fetch_object(repo, remote, review_commit_sha, run_id=publication_run_id)
        code, tree, _ = _git(
            repo,
            "ls-tree",
            "-r",
            "--name-only",
            review_commit_sha,
            "--",
            ".ai/reviews",
            allow_fail=True,
        )
        if code:
            raise ValueError(f"cannot inspect predecessor review decision for {source_id}")
        review_paths = [
            path
            for path in tree.splitlines()
            if path.startswith(".ai/reviews/")
            and path.endswith((".yaml", ".yml"))
        ]
        if len(review_paths) != 1:
            raise ValueError(
                f"predecessor review decision must contain exactly one REVIEW document for {source_id}"
            )
        review_bytes = _read_blob(
            repo, review_commit_sha, review_paths[0], run_id=publication_run_id
        )
        prior_review = parse_review(review_bytes.decode("utf-8", errors="strict"))

        if repair_bytes is not None:
            from .operator import _validated_repair_remediation_source
            from .review_transport import _read_remote_blob
            source_package = _mapping(_json_no_duplicates(
                _read_blob(repo, pred_artifacts_sha, ".ai/transport/result.json", run_id=publication_run_id),
                document="source ResultPackage"), "source ResultPackage")
            source_run = _run_from_data(pred_run_data, "source REPAIR RUN")
            source_task = parse_task(_read_blob(repo, pred_result.head_sha,
                f".ai/tasks/{source_run.task.id}.yaml", run_id=publication_run_id).decode("utf-8"))
            _validated_repair_remediation_source(
                repo, remote=remote, task=source_task, run_data=pred_run_data, run=source_run,
                package=ResultPackage(result=pred_result, evidence=tuple(
                    validate_evidence(item) for item in source_package["evidence"])),
                review=prior_review,
                repair=_read_remote_blob(repo, remote, pred_artifacts_sha, ".ai/transport/repair.json"),
            )
        if source_pred is not None:
            pred_run, pred_remediation = _parse_remediation_run(
                semantic_run_data, run_id=semantic_run_id
            )
            if source_pred.reviewed_sha != pred_remediation.reviewed_sha:
                raise ValueError("cumulative semantic predecessor mismatch")
            steps.append((source_id, source_pred, prior_review))
            current_pred = source_pred
            if "execution_base" in semantic_run_data:
                current_base = _parse_remediation_execution_base(
                    semantic_run_data["execution_base"]
                )
                if current_base.candidate_sha != pred_run.base_sha:
                    raise ValueError("cumulative execution-base RUN mismatch")
            else:
                if pred_run.base_sha != source_pred.reviewed_sha:
                    raise ValueError("legacy cumulative base mismatch")
                current_base = RemediationExecutionBase(
                    source_pred.source_run_id, source_pred.reviewed_sha
                )
        else:
            primary_run_id = source_id
            primary_review = prior_review
            break

    try:
        frontier = CorrectionFrontier.from_primary(primary_run_id, primary_review)
        for step_run_id, step_pred, step_review in reversed(steps):
            frontier = frontier.advance(
                delta_run_id=step_run_id,
                delta_review=step_review,
                predecessor=step_pred,
            )
    except CorrectionFrontierError as exc:
        raise ValueError(f"invalid correction frontier advancement: {exc}") from exc

    return frontier


def _validate_remediation_package(
    repo: Path,
    *,
    source_sha: str,
    task: Any,
    run: Run,
    remediation: Remediation,
    prior_review: Review,
    result: Any,
    evidence: tuple[Any, ...],
    execution_base_sha: str | None = None,
) -> ResultPackage:
    if run.task.id != task.task_id or run.task.revision != task.revision:
        raise ValueError("REMEDIATION RUN does not reference the supplied TASK")
    expected_base = (
        remediation.reviewed_sha
        if execution_base_sha is None
        else execution_base_sha
    )
    if run.base_sha != expected_base:
        raise ValueError("legacy REMEDIATION RUN base_sha does not match reviewed_sha")
    validate_remediation(
        review=prior_review, remediation=remediation, task=task
    )
    if remediation.action == "CODE_FIX" and not remediation.modification_scope:
        raise ValueError("CODE_FIX remediation modification scope is empty")
    if not remediation.affected_verification:
        raise ValueError("REMEDIATION affected verification is empty")
    if result.claims:
        raise ValueError("remediation RESULT claims must be empty")
    if result.unresolved:
        raise ValueError("remediation RESULT has unresolved items")

    evidence_by_id: set[str] = set()
    for item in evidence:
        if item.evidence_id in evidence_by_id:
            raise ValueError(f"duplicate evidence_id: {item.evidence_id}")
        evidence_by_id.add(item.evidence_id)
        if item.run_id != run.run_id:
            raise ValueError(
                f"{item.evidence_id} does not reference RUN {run.run_id}"
            )
        if item.subject_sha != result.head_sha:
            raise ValueError(
                f"{item.evidence_id} subject_sha does not match RESULT head_sha"
            )
    for command in remediation.affected_verification:
        matching = [item for item in evidence if item.source.command == command]
        if not matching:
            raise ValueError(
                "missing affected verification evidence for required command: "
                + command
            )
        if not any(item.result.exit_code == 0 for item in matching):
            raise ValueError(
                "affected verification command has no successful evidence: "
                + command
            )

    code, changed_output, _ = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        run.base_sha,
        source_sha,
        allow_fail=True,
    )
    if code:
        raise ValueError("cannot inspect REMEDIATION committed delta")
    changed_files = {path for path in changed_output.split("\0") if path}
    if set(result.changed_files) != changed_files:
        raise ValueError("RESULT.changed_files mismatch")
    outside_scope = changed_files.difference(remediation.modification_scope)
    if outside_scope:
        raise ValueError(
            "committed changed paths outside REMEDIATION modification scope: "
            + ", ".join(sorted(outside_scope))
        )
    if remediation.action == "EVIDENCE_ONLY":
        if source_sha != run.base_sha or changed_files:
            raise ValueError("EVIDENCE_ONLY remediation changed repository HEAD")
    elif source_sha == run.base_sha or not changed_files:
        raise ValueError("CODE_FIX remediation committed delta is empty")
    return ResultPackage(result=result, evidence=evidence)


def _changed_files(repo: Path, base_sha: str, head_sha: str) -> set[str]:
    code, output, _ = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        base_sha,
        head_sha,
        allow_fail=True,
    )
    if code:
        raise ValueError("cannot inspect committed correction delta")
    return {path for path in output.split("\0") if path}


def _validate_repair_authorization(
    data: Any,
    *,
    failed_run_id: str,
    failed_head_sha: str,
    task: Any,
    failed_changed_files: set[str],
) -> Mapping[str, Any]:
    authorization = _mapping(data, "REPAIR authorization")
    required = {
        "repair_id",
        "failed_run_id",
        "failed_head_sha",
        "task",
        "action",
        "modification_scope",
        "instructions",
        "constraints",
    }
    if set(authorization) != required:
        raise ValueError("REPAIR authorization fields do not match the contract")
    task_ref = _mapping(authorization.get("task"), "REPAIR authorization.task")
    expected_task = {"id": task.task_id, "revision": task.revision}
    if (
        authorization.get("failed_run_id") != failed_run_id
        or authorization.get("failed_head_sha") != failed_head_sha
        or dict(task_ref) != expected_task
    ):
        raise ValueError("REPAIR authorization identity mismatch")
    action = authorization.get("action")
    if action not in (
        "CODE_FIX", "NO_CHANGE", "CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE"
    ):
        raise ValueError("REPAIR authorization action is invalid")
    scope = authorization.get("modification_scope")
    instructions = authorization.get("instructions")
    constraints = authorization.get("constraints")
    if not isinstance(scope, list) or not all(
        isinstance(item, str) and item for item in scope
    ):
        raise ValueError("REPAIR modification_scope must be a string list")
    if not isinstance(instructions, list) or not instructions or not all(
        isinstance(item, str) and item for item in instructions
    ):
        raise ValueError("REPAIR instructions must be a non-empty string list")
    if not isinstance(constraints, list) or not all(
        isinstance(item, str) and item for item in constraints
    ):
        raise ValueError("REPAIR constraints must be a string list")
    correction_scope = set(task.scope.modify).union(failed_changed_files)
    if set(scope).difference(correction_scope):
        raise ValueError("REPAIR modification scope exceeds correction authority")
    if set(constraints).difference(task.constraints.hard):
        raise ValueError("REPAIR constraints introduce new Human intent")
    if action == "NO_CHANGE" and scope:
        raise ValueError("NO_CHANGE REPAIR modification scope must be empty")
    if action == "FINALIZE_CANDIDATE" and scope:
        raise ValueError(
            "FINALIZE_CANDIDATE REPAIR modification scope must be empty"
        )
    if action == "CONTINUE_IMPLEMENTATION" and not scope:
        raise ValueError(
            "CONTINUE_IMPLEMENTATION REPAIR modification scope is empty"
        )
    return authorization


def _canonical_repair_authorization(
    repo: Path,
    *,
    remote: str,
    failed_run_id: str,
    run_id: str,
) -> tuple[str, Mapping[str, Any]]:
    ref = f"refs/heads/aios/repair/{failed_run_id}"
    _single_remote_sha(repo, remote, ref, run_id=run_id)
    try:
        current = resolve_remote_repair_authorization(
            repo, failed_run_id, remote=remote
        )
    except ReviewTransportError as exc:
        raise ValueError(f"canonical REPAIR authorization is invalid: {exc}") from exc
    _fetch_object(repo, remote, current.commit_sha, run_id=run_id)
    return current.commit_sha, _mapping(
        _json_no_duplicates(current.repair, document="canonical REPAIR"),
        "canonical REPAIR",
    )


def _validate_zero_delta_historical_failure(
    repo: Path,
    *,
    action: str,
    failure: Mapping[str, Any] | None,
    run: Run,
    failed_head_sha: str,
    task: Any,
) -> None:
    """Bind a zero-delta historical REPAIR attempt to its canonical FAILURE."""

    if failure is None:
        raise ValueError(f"{action} REPAIR committed delta is empty")
    if failure.get("phase") not in ("EXECUTION", "COMPLETION_GATE"):
        raise ValueError(
            f"zero-delta {action} requires a pre-verification failure"
        )
    code, _, _ = _git(
        repo,
        "merge-base",
        "--is-ancestor",
        run.base_sha,
        failed_head_sha,
        allow_fail=True,
    )
    actual_descends_from_base = code == 0
    actual_changed_files = _changed_files(repo, run.base_sha, failed_head_sha)
    validate_runtime_failure_binding(
        failure,
        run_id=run.run_id,
        task_id=task.task_id,
        task_revision=task.revision,
        executor=run.executor,
        base_sha=run.base_sha,
        candidate_sha=failed_head_sha,
        modification_scope=tuple(task.scope.modify),
        actual_descends_from_base=actual_descends_from_base,
        actual_changed_files=actual_changed_files,
    )
    candidate = _mapping(
        failure.get("candidate"), "historical FAILURE.candidate"
    )
    if (
        run.base_sha != failed_head_sha
        or actual_changed_files
        or candidate.get("changed_files") != []
        or candidate.get("outside_task_scope") != []
        or candidate.get("dirty") is not False
        or candidate.get("descends_from_base") is not True
        or candidate.get("repairable") is not True
        or candidate.get("transportable") is not True
    ):
        raise ValueError(
            f"zero-delta {action} failure facts are invalid"
        )


def _repair_review_lineage(
    repo: Path,
    *,
    remote: str,
    publication_run_id: str,
    child_run_data: Mapping[str, Any],
    child_run: Run,
    child_head_sha: str,
    lineage_bytes: bytes,
    task: Any,
    historical_child_failure: Mapping[str, Any] | None = None,
    seen: frozenset[str] = frozenset(),
    strict_source: bool = False,
    repair_sources: frozenset[str] = frozenset(),
    integrated_origins: list[Mapping[str, Any]] | None = None,
) -> tuple[str, str, Review | None, str | None]:
    """Validate persisted REPAIR links and recover the applicable prior REVIEW."""

    lineage = _mapping(
        _json_no_duplicates(lineage_bytes, document="REPAIR execution"),
        "REPAIR execution",
    )
    required = {
        "failed_run_id",
        "root_base_sha",
        "failed_head_sha",
        "failure",
        "task",
        "repair",
        "run",
    }
    keys = set(lineage)
    optional = {"repair_authorization_sha", "result_base_sha"}
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise ValueError("REPAIR execution fields do not match persisted lineage")
    repair_authorization_sha = lineage.get("repair_authorization_sha")
    if repair_authorization_sha is not None:
        repair_authorization_sha = lineage.get("repair_authorization_sha")
        if (
            not isinstance(repair_authorization_sha, str)
            or _SHA.fullmatch(repair_authorization_sha) is None
        ):
            raise ValueError("REPAIR authorization SHA is invalid")
    persisted_result_base = lineage.get("result_base_sha")
    if persisted_result_base is not None and (
        not isinstance(persisted_result_base, str)
        or _SHA.fullmatch(persisted_result_base) is None
    ):
        raise ValueError("REPAIR result-base SHA is invalid")
    failed_run_id = lineage.get("failed_run_id")
    if not isinstance(failed_run_id, str) or _RUN_ID.fullmatch(failed_run_id) is None:
        raise ValueError("REPAIR failed RUN identity is invalid")
    if failed_run_id in seen:
        raise ValueError("cyclic REPAIR predecessor lineage")
    seen = seen.union((failed_run_id,))

    root_base_sha = lineage.get("root_base_sha")
    failed_head_sha = lineage.get("failed_head_sha")
    if (
        not isinstance(root_base_sha, str)
        or _SHA.fullmatch(root_base_sha) is None
        or not isinstance(failed_head_sha, str)
        or _SHA.fullmatch(failed_head_sha) is None
    ):
        raise ValueError("REPAIR root or failed-head SHA is invalid")
    embedded_run = _mapping(lineage.get("run"), "REPAIR execution.run")
    if dict(embedded_run) != dict(child_run_data):
        raise ValueError("REPAIR execution RUN does not match successful RUN")
    if child_run.base_sha != failed_head_sha:
        raise ValueError("REPAIR RUN base_sha does not match failed_head_sha")
    execution_task = _mapping(lineage.get("task"), "REPAIR execution.task")
    if (
        execution_task.get("task_id") != task.task_id
        or execution_task.get("revision") != task.revision
    ):
        raise ValueError("REPAIR execution TASK identity or revision mismatch")

    artifacts_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    failed_ref = f"refs/heads/aios/failure/{failed_run_id}"
    artifacts_sha = _single_remote_sha(
        repo, remote, artifacts_ref, run_id=publication_run_id
    )
    canonical_failed_sha = _single_remote_sha(
        repo, remote, failed_ref, run_id=publication_run_id
    )
    _fetch_object(repo, remote, artifacts_sha, run_id=publication_run_id)
    _fetch_object(repo, remote, canonical_failed_sha, run_id=publication_run_id)
    if canonical_failed_sha != failed_head_sha:
        raise ValueError("REPAIR failed_head_sha does not match canonical failure ref")
    predecessor_run_bytes = _read_blob(
        repo, artifacts_sha, ".ai/transport/run.json", run_id=publication_run_id
    )
    failure_bytes = _read_blob(
        repo, artifacts_sha, ".ai/transport/failure.json", run_id=publication_run_id
    )
    predecessor_repair = _read_optional_blob(
        repo, artifacts_sha, ".ai/transport/repair.json"
    )
    predecessor_run_data = _mapping(
        _json_no_duplicates(predecessor_run_bytes, document="predecessor RUN"),
        "predecessor RUN",
    )
    failure = _mapping(
        _json_no_duplicates(failure_bytes, document="predecessor FAILURE"),
        "predecessor FAILURE",
    )
    embedded_failure = _mapping(lineage.get("failure"), "REPAIR execution.failure")
    expected_task = {"id": task.task_id, "revision": task.revision}
    failure_task = _mapping(failure.get("task"), "predecessor FAILURE.task")
    if (
        dict(embedded_failure) != dict(failure)
        or failure.get("kind") != "FAILURE"
        or failure.get("run_id") != failed_run_id
        or dict(failure_task) != expected_task
        or failure.get("failed_head_sha") != failed_head_sha
    ):
        raise ValueError("REPAIR predecessor FAILURE identity mismatch")
    candidate = _mapping(failure.get("candidate"), "predecessor FAILURE.candidate")
    if candidate.get("repairable") is not True:
        raise ValueError("REPAIR predecessor is not authorized as repairable")
    failed_changed_data = candidate.get("changed_files")
    if not isinstance(failed_changed_data, list) or not all(
        isinstance(item, str) and item for item in failed_changed_data
    ):
        raise ValueError("REPAIR predecessor changed_files are invalid")
    failed_changed = set(failed_changed_data)

    embedded_authorization = _validate_repair_authorization(
        lineage.get("repair"),
        failed_run_id=failed_run_id,
        failed_head_sha=failed_head_sha,
        task=task,
        failed_changed_files=failed_changed,
    )
    canonical_sha, canonical_authorization = _canonical_repair_authorization(
        repo,
        remote=remote,
        failed_run_id=failed_run_id,
        run_id=publication_run_id,
    )
    if dict(canonical_authorization) != dict(embedded_authorization):
        raise ValueError("persisted REPAIR authorization is not canonical")
    if (
        repair_authorization_sha is not None
        and repair_authorization_sha != canonical_sha
    ):
        raise ValueError(
            "persisted repair_authorization_sha does not match current canonical authorization"
        )

    mutation = _changed_files(repo, failed_head_sha, child_head_sha)
    scope = set(embedded_authorization["modification_scope"])
    if mutation.difference(scope):
        raise ValueError("REPAIR committed delta exceeds modification scope")
    if embedded_authorization["action"] == "CODE_FIX":
        if child_head_sha == failed_head_sha or not mutation:
            _validate_zero_delta_historical_failure(
                repo,
                action="CODE_FIX",
                failure=historical_child_failure,
                run=child_run,
                failed_head_sha=child_head_sha,
                task=task,
            )
    elif embedded_authorization["action"] == "CONTINUE_IMPLEMENTATION":
        if failure.get("phase") not in ("EXECUTION", "COMPLETION_GATE"):
            raise ValueError(
                "CONTINUE_IMPLEMENTATION requires a pre-verification failure"
            )
        if (
            candidate.get("transportable") is not True
            or candidate.get("dirty") is not False
            or candidate.get("descends_from_base") is not True
            or candidate.get("outside_task_scope") != []
        ):
            raise ValueError(
                "CONTINUE_IMPLEMENTATION requires a clean transportable candidate"
            )
        code, _, _ = _git(
            repo,
            "merge-base",
            "--is-ancestor",
            failed_head_sha,
            child_head_sha,
            allow_fail=True,
        )
        if code:
            raise ValueError(
                "CONTINUE_IMPLEMENTATION candidate does not descend from failed head"
            )
        if child_head_sha == failed_head_sha or not mutation:
            _validate_zero_delta_historical_failure(
                repo,
                action="CONTINUE_IMPLEMENTATION",
                failure=historical_child_failure,
                run=child_run,
                failed_head_sha=child_head_sha,
                task=task,
            )
    elif embedded_authorization["action"] == "FINALIZE_CANDIDATE":
        if failure.get("phase") not in ("EXECUTION", "COMPLETION_GATE"):
            raise ValueError(
                "FINALIZE_CANDIDATE requires a pre-verification failure"
            )
        if (
            candidate.get("transportable") is not True
            or candidate.get("dirty") is not False
            or candidate.get("descends_from_base") is not True
            or candidate.get("outside_task_scope") != []
        ):
            raise ValueError(
                "FINALIZE_CANDIDATE requires a clean transportable candidate"
            )
        if child_head_sha != failed_head_sha or mutation:
            raise ValueError(
                "FINALIZE_CANDIDATE REPAIR changed repository HEAD"
            )
    elif child_head_sha != failed_head_sha or mutation:
        raise ValueError("NO_CHANGE REPAIR changed repository HEAD")

    if predecessor_run_data.get("kind") == "REMEDIATION":
        if predecessor_repair is not None:
            raise ValueError("REMEDIATION predecessor has conflicting REPAIR lineage")
        if "predecessor" in predecessor_run_data:
            predecessor_run, remediation = _parse_remediation_run(
                predecessor_run_data, run_id=failed_run_id
            )
            prior_review = _validate_predecessor_lineage(
                repo,
                remote=remote,
                publication_run_id=publication_run_id,
                run_data=predecessor_run_data,
                run=predecessor_run,
                remediation=remediation,
                task=task,
            )
        else:
            predecessor_run, remediation, prior_review = _remediation_lineage(
                predecessor_run_data, run_id=failed_run_id
            )
        if (
            predecessor_run.task.id != task.task_id
            or predecessor_run.task.revision != task.revision
            or predecessor_run.status != ACTIVE
            or failure.get("base_sha") != predecessor_run.base_sha
        ):
            raise ValueError("failed REMEDIATION predecessor identity mismatch")
        if "execution_base" in predecessor_run_data:
            predecessor_base = _parse_remediation_execution_base(
                predecessor_run_data["execution_base"]
            )
            if predecessor_base.is_integrated and integrated_origins is not None:
                integrated_origins.append(predecessor_run_data)
            result_base_sha = (
                predecessor_base.integration_candidate_sha
                if predecessor_base.is_integrated
                else root_base_sha
            )
        else:
            if predecessor_run.base_sha != remediation.reviewed_sha:
                raise ValueError("failed REMEDIATION predecessor identity mismatch")
            result_base_sha = root_base_sha
        validate_remediation(review=prior_review, remediation=remediation, task=task)
        semantic_review = prior_review
        if strict_source:
            from .operator import _derive_remediation_source_root, _remediation_execution_from_data
            from .review_transport import resolve_remote_remediation_lineages
            origin_execution = _remediation_execution_from_data(predecessor_run_data["execution"])
            canonical_root = _derive_remediation_source_root(
                repo, task=task, execution=origin_execution, repair_sources=repair_sources,
            )
            if canonical_root != root_base_sha:
                raise ValueError("REPAIR root_base_sha does not match REMEDIATION origin")
            reviews = [parse_review(item.review.decode("utf-8")) for item in
                       resolve_remote_remediation_lineages(
                           repo, finding_id=remediation.finding_id,
                           task_id=task.task_id, task_revision=task.revision,
                       )]
            reviews = [item for item in reviews if item.review_id == origin_execution.review_id
                       and item.reviewed_sha == remediation.reviewed_sha]
            if len(reviews) != 1:
                raise ValueError("REPAIR prior semantic REVIEW is missing or ambiguous")
            semantic_review = reviews[0]
        semantic_finding_id = remediation.finding_id
    elif "kind" not in predecessor_run_data:
        predecessor_run = _run_from_data(predecessor_run_data, "predecessor RUN")
        if (
            predecessor_run.run_id != failed_run_id
            or predecessor_run.task.id != task.task_id
            or predecessor_run.task.revision != task.revision
            or predecessor_run.status != ACTIVE
            or failure.get("base_sha") != predecessor_run.base_sha
        ):
            raise ValueError("failed PRIMARY/REPAIR predecessor identity mismatch")
        if predecessor_repair is None:
            if root_base_sha != predecessor_run.base_sha:
                raise ValueError("REPAIR root_base_sha does not match PRIMARY root")
            semantic_review = None
            semantic_finding_id = None
            result_base_sha = root_base_sha
        else:
            (
                predecessor_root,
                result_base_sha,
                semantic_review,
                semantic_finding_id,
            ) = _repair_review_lineage(
                repo,
                remote=remote,
                publication_run_id=publication_run_id,
                child_run_data=predecessor_run_data,
                child_run=predecessor_run,
                child_head_sha=failed_head_sha,
                lineage_bytes=predecessor_repair,
                task=task,
                historical_child_failure=failure,
                seen=seen,
                strict_source=strict_source,
                repair_sources=repair_sources,
                integrated_origins=integrated_origins,
            )
            if predecessor_root != root_base_sha:
                raise ValueError("conflicting REPAIR root_base_sha lineage")
    else:
        raise ValueError("unknown predecessor RUN kind")

    if strict_source:
        code, _, _ = _git(repo, "merge-base", "--is-ancestor",
                          predecessor_run.base_sha, failed_head_sha, allow_fail=True)
        validate_runtime_failure_binding(
            failure, run_id=failed_run_id, task_id=task.task_id, task_revision=task.revision,
            executor=predecessor_run.executor, base_sha=predecessor_run.base_sha,
            candidate_sha=failed_head_sha, modification_scope=task.scope.modify,
            actual_descends_from_base=code == 0,
            actual_changed_files=_changed_files(repo, predecessor_run.base_sha, failed_head_sha),
        )
    code, _, _ = _git(
        repo,
        "merge-base",
        "--is-ancestor",
        root_base_sha,
        child_head_sha,
        allow_fail=True,
    )
    if code:
        raise ValueError("repaired candidate does not descend from TASK root")
    if (
        persisted_result_base is not None
        and persisted_result_base != result_base_sha
    ):
        raise ValueError("conflicting REPAIR result-base lineage")
    return root_base_sha, result_base_sha, semantic_review, semantic_finding_id


def _validate_repair_package(
    repo: Path,
    *,
    source_sha: str,
    result_base_sha: str,
    task: Any,
    run: Run,
    result: Any,
    evidence: tuple[Any, ...],
) -> ResultPackage:
    package = validate_result_package(
        task=task, run=run, result=result, evidence=evidence
    )
    changed_files = _changed_files(repo, result_base_sha, source_sha)
    if set(result.changed_files) != changed_files:
        raise ValueError("REPAIR RESULT.changed_files mismatch")
    outside_scope = changed_files.difference(task.scope.modify)
    if outside_scope:
        raise ValueError(
            "REPAIR result changed paths outside TASK scope: "
            + ", ".join(sorted(outside_scope))
        )
    return package


def _decision_review_bytes(repo: Path, decision_sha: str, *, run_id: str) -> bytes:
    code, tree, _ = _git(
        repo, "ls-tree", "-r", "--name-only", decision_sha, "--", ".ai/reviews",
        allow_fail=True,
    )
    if code:
        raise _failed(run_id, "cannot inspect canonical review decision")
    paths = [path for path in tree.splitlines() if path.startswith(".ai/reviews/")
             and path.endswith((".yaml", ".yml"))]
    if len(paths) != 1:
        raise _failed(run_id, "review decision must contain exactly one REVIEW document")
    return _read_blob(repo, decision_sha, paths[0], run_id=run_id)


def _load_success_lineage(
    repo: Path,
    *,
    remote: str,
    run_id: str,
    decision_sha: str,
    publication_main_sha: str | None = None,
) -> tuple[str, ResultPackage, tuple[str, ...]]:
    review_bytes = _decision_review_bytes(repo, decision_sha, run_id=run_id)
    artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"
    source_ref = f"refs/heads/aios/review/{run_id}"
    artifacts_sha = _single_remote_sha(
        repo, remote, artifacts_ref, run_id=run_id
    )
    source_sha = _single_remote_sha(repo, remote, source_ref, run_id=run_id)
    _fetch_object(repo, remote, artifacts_sha, run_id=run_id)
    _fetch_object(repo, remote, source_sha, run_id=run_id)

    run_bytes = _read_blob(
        repo, artifacts_sha, ".ai/transport/run.json", run_id=run_id
    )
    result_bytes = _read_blob(
        repo, artifacts_sha, ".ai/transport/result.json", run_id=run_id
    )
    repair_bytes = _read_optional_blob(
        repo, artifacts_sha, ".ai/transport/repair.json"
    )
    recovery_bytes = _read_optional_blob(repo, artifacts_sha, ".ai/transport/publication-recovery.json")
    try:
        run_data = _mapping(
            _json_no_duplicates(run_bytes, document="RUN"), "RUN"
        )
        remediation = None
        prior_review = None
        if "kind" not in run_data:
            if run_data.get("run_id") != run_id:
                raise ValueError("RUN-ID mismatch between decision ref and RUN")
            run = _run_from_data(run_data, "RUN")
        elif run_data.get("kind") == "REMEDIATION":
            if "predecessor" in run_data:
                run, remediation = _parse_remediation_run(
                    run_data, run_id=run_id
                )
            else:
                run, remediation, prior_review = _remediation_lineage(
                    run_data, run_id=run_id
                )
        else:
            raise ValueError("unknown canonical RUN kind")
        if run.status != ACTIVE:
            raise ValueError("canonical successful RUN status is invalid")
        code, kind, _ = _git(
            repo, "cat-file", "-t", run.base_sha, allow_fail=True
        )
        if code or kind != "commit":
            raise ValueError("RUN base_sha is not a canonical commit")
        code, _, _ = _git(
            repo,
            "merge-base",
            "--is-ancestor",
            run.base_sha,
            source_sha,
            allow_fail=True,
        )
        if code:
            raise ValueError("reviewed candidate does not descend from RUN base_sha")

        result_data = _mapping(
            _json_no_duplicates(result_bytes, document="ResultPackage"),
            "ResultPackage",
        )
        result = validate_result(result_data["result"])
        evidence_data = result_data["evidence"]
        if not isinstance(evidence_data, list):
            raise ValueError("ResultPackage.evidence must be a list")
        evidence = tuple(validate_evidence(item) for item in evidence_data)

        task_bytes = _read_blob(
            repo,
            source_sha,
            f".ai/tasks/{run.task.id}.yaml",
            run_id=run_id,
        )
        task = parse_task(task_bytes.decode("utf-8", errors="strict"))
        repair_prior_review = None
        correction_origins: list[Mapping[str, Any]] = []
        if "predecessor" in run_data:
            assert remediation is not None
            prior_review = _validate_predecessor_lineage(
                repo,
                remote=remote,
                publication_run_id=run_id,
                run_data=run_data,
                run=run,
                remediation=remediation,
                task=task,
            )
            correction_origins.append(run_data)
        if repair_bytes is not None:
            if remediation is not None:
                raise ValueError(
                    "successful artifacts contain conflicting REMEDIATION/REPAIR lineage"
                )
            (
                _root_base_sha,
                repair_result_base_sha,
                repair_prior_review,
                repaired_finding_id,
            ) = _repair_review_lineage(
                repo,
                remote=remote,
                publication_run_id=run_id,
                child_run_data=run_data,
                child_run=run,
                child_head_sha=source_sha,
                lineage_bytes=repair_bytes,
                task=task,
                integrated_origins=correction_origins,
            )
            package = _validate_repair_package(
                repo,
                source_sha=source_sha,
                result_base_sha=repair_result_base_sha,
                task=task,
                run=run,
                result=result,
                evidence=evidence,
            )
        elif remediation is None:
            package = validate_result_package(
                task=task, run=run, result=result, evidence=evidence
            )
        else:
            assert prior_review is not None
            package = _validate_remediation_package(
                repo,
                source_sha=source_sha,
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
        if package.result.unresolved:
            raise ValueError("successful RESULT contains unresolved items")
        if recovery_bytes is not None:
            if remediation is not None or repair_bytes is not None:
                raise ValueError("publication recovery cannot impersonate a correction family")
            validate_publication_recovery(repo, remote=remote, raw=recovery_bytes,
                run=run, task=task, candidate_sha=source_sha, package=package,
                current_main_sha=publication_main_sha)

        review = parse_review(review_bytes.decode("utf-8", errors="strict"))
        if repair_bytes is not None:
            if repair_prior_review is None:
                if review.mode != "PRIMARY" or review.prior_finding_id is not None:
                    raise ValueError(
                        "REPAIR before a semantic finding requires a PRIMARY REVIEW"
                    )
                validate_review(task=task, result=result, review=review)
            else:
                if review.mode != "DELTA":
                    raise ValueError(
                        "REPAIR of REMEDIATION candidate requires a DELTA REVIEW"
                    )
                if review.prior_finding_id != repaired_finding_id:
                    raise ValueError(
                        "DELTA REVIEW prior finding does not match repaired REMEDIATION"
                    )
                validate_review(
                    task=task,
                    result=result,
                    review=review,
                    prior_review=repair_prior_review,
                )
        elif remediation is None:
            validate_review(task=task, result=result, review=review)
        else:
            if review.mode != "DELTA":
                raise ValueError("REMEDIATION candidate requires a DELTA REVIEW")
            if review.prior_finding_id != remediation.finding_id:
                raise ValueError(
                    "DELTA REVIEW prior finding does not match REMEDIATION"
                )
            validate_review(
                task=task,
                result=result,
                review=review,
                prior_review=prior_review,
            )
        if review.verdict != "PASS":
            raise ValueError(f"REVIEW verdict is {review.verdict}, not PASS")
        if review.reviewed_sha != source_sha:
            raise ValueError(
                "REVIEW.reviewed_sha does not match canonical source ref"
            )
        if result.head_sha != source_sha:
            raise ValueError(
                "RESULT head_sha does not match canonical source ref"
            )
        for origin in correction_origins:
            frontier = _derive_publication_frontier(
                repo,
                remote=remote,
                publication_run_id=run_id,
                run_data=origin,
                delta_review=review,
            )
            if not frontier.is_empty:
                outstanding_ids = ", ".join(
                    sorted(f.finding_id for f in frontier.findings)
                )
                raise ValueError(
                    f"predecessor lineage contains outstanding findings in correction frontier: {outstanding_ids}"
                )
        for origin in correction_origins:
            if "execution_base" not in origin:
                continue
            cumulative_base = _parse_remediation_execution_base(origin["execution_base"])
            # Use the same main snapshot as no-op classification and the push lease.
            # Historical integration identity/ref/parents/tree and correction lineage
            # remain mandatory above; only the mutation freshness gate is conditional.
            main_sha = publication_main_sha or _single_remote_sha(
                repo, remote, "refs/heads/main", run_id=run_id
            )
            _fetch_object(repo, remote, main_sha, run_id=run_id)
            candidate_already_contained, _, _ = _git(
                repo, "merge-base", "--is-ancestor", source_sha, main_sha,
                allow_fail=True,
            )
            if (
                cumulative_base.is_integrated
                and cumulative_base.authorized_main_sha != main_sha
                and candidate_already_contained != 0
            ):
                raise ValueError(
                    f"authorized main SHA {cumulative_base.authorized_main_sha} is stale (current main is {main_sha})"
                )
            main_is_safe_base, _, _ = _git(
                repo, "merge-base", "--is-ancestor", main_sha,
                cumulative_base.candidate_sha, allow_fail=True,
            )
            if main_is_safe_base and candidate_already_contained:
                raise ValueError(
                    "current main is not safely contained by cumulative execution base"
                )
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        reviewed_sha = locals().get("reviewed_sha", source_sha)
        raise _failed(
            run_id,
            f"invalid canonical publication lineage: {exc}",
            reviewed_sha=reviewed_sha,
            cause="MALFORMED_LINEAGE",
        ) from exc
    return source_sha, package, tuple(task.scope.modify)


def _decision_snapshot(repo: Path, remote: str, *, run_id: str) -> dict[str, str]:
    code, output, _ = _git(
        repo, "ls-remote", "--refs", remote, f"{_DECISION_PREFIX}*", allow_fail=True,
    )
    lines = output.splitlines()
    if code or len(lines) > _MAX_DECISIONS:
        raise _failed(run_id, "canonical decision snapshot unavailable or over capacity")
    refs: dict[str, str] = {}
    for line in lines:
        pair = line.split()
        if (len(pair) != 2 or _SHA.fullmatch(pair[0]) is None
                or not pair[1].startswith(_DECISION_PREFIX)
                or _RUN_ID.fullmatch(pair[1][len(_DECISION_PREFIX):]) is None
                or pair[1] in refs):
            raise _failed(run_id, "canonical decision snapshot is malformed or ambiguous")
        refs[pair[1]] = pair[0]
    return refs


def _ancestor(repo: Path, ancestor: str, descendant: str, *, run_id: str,
              reviewed_sha: str, main_sha: str) -> bool:
    code, _, _ = _git(
        repo, "merge-base", "--is-ancestor", ancestor, descendant, allow_fail=True,
    )
    if code not in (0, 1):
        raise _failed(run_id, "cannot classify canonical ancestry", reviewed_sha=reviewed_sha,
                      prior_main_sha=main_sha, cause="AMBIGUOUS_ANCESTRY")
    return code == 0


def _recheck_binding(repo: Path, remote: str, binding: Mapping[str, str], *,
                     run_id: str, reviewed_sha: str, main_sha: str,
                     decisions: Mapping[str, str] | None = None) -> None:
    """Re-read immutable lineage, then main immediately before a terminal gate."""
    try:
        for ref, sha in binding.items():
            if _single_remote_sha(repo, remote, ref, run_id=run_id) != sha:
                raise _failed(run_id, "canonical review binding changed during publication",
                              cause="STALE_REVIEW_BINDING")
        if decisions is not None and _decision_snapshot(repo, remote, run_id=run_id) != decisions:
            raise _failed(run_id, "canonical review decision set changed before mutation",
                          cause="DECISION_SET_CHANGED")
        if _single_remote_sha(repo, remote, "refs/heads/main", run_id=run_id) != main_sha:
            raise _failed(run_id, "canonical main moved during publication",
                          cause="CONCURRENT_MAIN_MOVEMENT")
    except PublicationError as exc:
        raise _failed(run_id, str(exc), reviewed_sha=reviewed_sha, prior_main_sha=main_sha,
                      cause=exc.report.cause) from exc


def _pending_review_guard(repo: Path, remote: str, *, run_id: str, reviewed_sha: str,
                          main_sha: str) -> tuple[dict[str, str], dict[str, str]]:
    """Hold a mutation that would strand an observed eligible exact PASS source.

    Retain the cumulative observation guard for decisions predating reservations.
    New PASS writers and main writers also share PUBLICATION_RESERVATION_REF.
    """
    decisions = _decision_snapshot(repo, remote, run_id=run_id)
    source_binding: dict[str, str] = {}
    for ref, decision in sorted(decisions.items()):
        other_run = ref[len(_DECISION_PREFIX):]
        if other_run == run_id:
            continue
        source_ref = f"refs/heads/aios/review/{other_run}"
        source = _single_remote_sha(repo, remote, source_ref, run_id=run_id)
        source_binding[source_ref] = source
        _fetch_object(repo, remote, source, run_id=run_id)
        if (source == main_sha or not _ancestor(
                repo, main_sha, source, run_id=run_id, reviewed_sha=reviewed_sha, main_sha=main_sha)
                or _ancestor(repo, source, reviewed_sha, run_id=run_id,
                             reviewed_sha=reviewed_sha, main_sha=main_sha)):
            continue
        _fetch_object(repo, remote, decision, run_id=run_id)
        try:
            review = parse_review(_decision_review_bytes(
                repo, decision, run_id=other_run).decode("utf-8", errors="strict"))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise _failed(run_id, "pending canonical review is malformed") from exc
        if review.verdict != "PASS":
            continue
        validated_source, _, _ = _load_success_lineage(
            repo, remote=remote, run_id=other_run, decision_sha=decision,
            publication_main_sha=main_sha,
        )
        if validated_source != source:
            raise _failed(run_id, "pending reviewed source changed", cause="STALE_REVIEW_BINDING")
        raise _recovery_required(
            run_id, reviewed_sha=reviewed_sha, prior_main_sha=main_sha,
            cause="COMPETING_REVIEWED_SOURCE",
            detail="main mutation would strand another eligible exact PASS source; "
                   "shared mutation-boundary scope reconciliation is required",
            blocker=PublicationBlocker(other_run, decision, source),
        )
    return decisions, source_binding


def _classify_recovery(repo: Path, *, run_id: str, reviewed_sha: str, main_sha: str,
                       modification_scope: tuple[str, ...]) -> PublicationError:
    """Compute bounded recovery observations without making a source commit or ref."""
    def blocked(cause: str, detail: str) -> PublicationError:
        return _recovery_required(run_id, reviewed_sha=reviewed_sha, prior_main_sha=main_sha,
                                  cause=cause, detail=detail)

    code, bases, _ = _git(repo, "merge-base", "--all", main_sha, reviewed_sha, allow_fail=True)
    merge_bases = bases.splitlines()
    if code or len(merge_bases) != 1 or _SHA.fullmatch(merge_bases[0]) is None:
        return blocked("AMBIGUOUS_ANCESTRY", "recovery has no unique canonical merge base")
    from .correction_integration import publication_tree_entries, CorrectionIntegrationError
    try:
        original = publication_tree_entries(repo, reviewed_sha)
        main = publication_tree_entries(repo, main_sha)
        base = publication_tree_entries(repo, merge_bases[0])
        source_delta = {path for path in base.keys() | original.keys()
                        if base.get(path) != original.get(path)}
        main_delta = {path for path in base.keys() | main.keys()
                      if base.get(path) != main.get(path)}
    except (CorrectionIntegrationError, ValueError):
        return blocked("RECOVERY_UNSUPPORTED_MODE", "recovery cannot preserve unsupported tracked identities")
    if source_delta.difference(modification_scope):
        return blocked("RECOVERY_SCOPE_ESCAPE", "original reviewed source escapes the current TASK modify scope")
    code, output, _ = _git(repo, "merge-tree", "--write-tree", main_sha, reviewed_sha,
                           allow_fail=True)
    if code == 1:
        return blocked("MERGE_CONFLICT", "recovery requires semantic conflict resolution")
    if code:
        return blocked("MERGE_CALCULATION_FAILED", "mechanical recovery tree is unavailable")
    lines = output.splitlines()
    if len(lines) != 1 or _SHA.fullmatch(lines[0]) is None:
        return blocked("MERGE_CALCULATION_FAILED", "mechanical recovery tree is malformed")
    tree_sha = lines[0]
    code, kind, _ = _git(repo, "cat-file", "-t", tree_sha, allow_fail=True)
    if code or kind != "tree":
        return blocked("MERGE_CALCULATION_FAILED", "mechanical recovery did not produce a tree")
    try:
        changed = _changed_files(repo, main_sha, tree_sha)
    except ValueError:
        return blocked("MERGE_CALCULATION_FAILED", "recovery delta cannot be inspected")
    if changed.difference(modification_scope):
        return blocked("RECOVERY_SCOPE_ESCAPE", "recovery delta escapes the canonical TASK modify scope")
    if source_delta.intersection(main_delta):
        return blocked("RECOVERY_MATERIAL_OVERLAP", "main and reviewed source overlap; textual merge success is insufficient")
    try:
        merged = publication_tree_entries(repo, tree_sha)
        expected = dict(main)
        for path in source_delta:
            if path in original:
                expected[path] = original[path]
            else:
                expected.pop(path, None)
        if merged != expected or changed != source_delta:
            return blocked("RECOVERY_MATERIAL_OVERLAP", "merge does not preserve exact original blobs and unrelated main content")
    except (CorrectionIntegrationError, ValueError):
        return blocked("RECOVERY_UNSUPPORTED_MODE", "recovery tree contains unsupported tracked identities")
    return _recovery_required(
        run_id, reviewed_sha=reviewed_sha, prior_main_sha=main_sha,
        cause="FRESH_EXACT_REVIEW_REQUIRED",
        detail="clean mechanical recovery tree observed; a separately authorized new source "
               "candidate requires Runtime verification and fresh Reviewer PASS for its exact SHA; "
               "the existing review authorizes only the original reviewed source",
        recovery=PublicationRecovery(merge_bases[0], tree_sha),
    )


def _recovery_competing_guard(repo: Path, remote: str, *, run_id: str,
        reviewed_sha: str, main_sha: str, delta: set[str], decisions: Mapping[str, str]) -> dict[str, str]:
    """An overlapping divergent PASS cannot be stranded by recovering a winner.

    Ordinary fast-forward gates are unchanged. This bounded recovery-only guard
    also covers PASS sources that became divergent through the same planning
    preemption and hence cannot appear in the old fast-forward pending guard.
    """
    binding = {}
    for ref, decision in sorted(decisions.items()):
        other = ref[len(_DECISION_PREFIX):]
        if other == run_id:
            continue
        source_ref = f"refs/heads/aios/review/{other}"
        source = _single_remote_sha(repo, remote, source_ref, run_id=run_id)
        binding[source_ref] = source
        _fetch_object(repo, remote, source, run_id=run_id)
        if (_ancestor(repo, source, main_sha, run_id=run_id, reviewed_sha=reviewed_sha, main_sha=main_sha)
                or _ancestor(repo, source, reviewed_sha, run_id=run_id, reviewed_sha=reviewed_sha, main_sha=main_sha)):
            continue
        code, bases, _ = _git(repo, "merge-base", "--all", main_sha, source, allow_fail=True)
        if code or len(bases.splitlines()) != 1:
            raise _failed(run_id, "competing source ancestry is ambiguous", reviewed_sha=reviewed_sha,
                          prior_main_sha=main_sha, cause="AMBIGUOUS_ANCESTRY")
        if not delta.intersection(_changed_files(repo, bases, source)):
            continue
        _fetch_object(repo, remote, decision, run_id=run_id)
        review = parse_review(_decision_review_bytes(repo, decision, run_id=other).decode("utf-8"))
        if review.verdict != "PASS":
            continue
        validated, *_ = _load_success_lineage(repo, remote=remote, run_id=other,
            decision_sha=decision, publication_main_sha=main_sha)
        if validated != source:
            raise _failed(run_id, "competing PASS source moved", cause="STALE_REVIEW_BINDING")
        raise _recovery_required(run_id, reviewed_sha=reviewed_sha, prior_main_sha=main_sha,
            cause="COMPETING_REVIEWED_SOURCE", detail="overlapping divergent PASS sources require explicit reconciliation; no automatic winner",
            blocker=PublicationBlocker(other, decision, source))
    return binding


def prepare_publication_recovery(repo: Path, *, run_id: str, decision_sha: str,
                                 expected_main_sha: str, remote: str = "origin"):
    """Read-only exact admission input, using the Publisher's existing gates."""
    from .correction_integration import PublicationSourcePlan, publication_tree_entries
    binding = {f"{_DECISION_PREFIX}{run_id}": decision_sha,
               "refs/heads/main": expected_main_sha}
    for namespace in ("review", "artifacts"):
        ref = f"refs/heads/aios/{namespace}/{run_id}"
        binding[ref] = _single_remote_sha(repo, remote, ref, run_id=run_id)
    _recheck_binding(repo, remote, binding, run_id=run_id,
        reviewed_sha=binding[f"refs/heads/aios/review/{run_id}"], main_sha=expected_main_sha)
    _fetch_object(repo, remote, decision_sha, run_id=run_id)
    _fetch_object(repo, remote, expected_main_sha, run_id=run_id)
    source, package, scope = _load_success_lineage(repo, remote=remote, run_id=run_id,
        decision_sha=decision_sha, publication_main_sha=expected_main_sha)
    if (_ancestor(repo, expected_main_sha, source, run_id=run_id, reviewed_sha=source,
                  main_sha=expected_main_sha)
            or _ancestor(repo, source, expected_main_sha, run_id=run_id,
                         reviewed_sha=source, main_sha=expected_main_sha)):
        raise _failed(run_id, "source recovery is unnecessary for ordinary publication")
    artifacts = binding[f"refs/heads/aios/artifacts/{run_id}"]
    if _read_optional_blob(repo, artifacts, ".ai/transport/publication-recovery.json") is not None:
        raise _failed(run_id, "an admitted recovered source cannot construct another candidate automatically",
                      cause="RECOVERY_ALREADY_ADMITTED")
    run_data = _mapping(_json_no_duplicates(_read_blob(repo, artifacts,
        ".ai/transport/run.json", run_id=run_id), document="RUN"), "RUN")
    source_run = (_run_from_data(run_data, "RUN") if "kind" not in run_data
                  else _parse_remediation_run(run_data, run_id=run_id)[0])
    task_path = f".ai/tasks/{source_run.task.id}.yaml"
    task_bytes = _read_blob(repo, source, task_path, run_id=run_id)
    if _read_optional_blob(repo, expected_main_sha, task_path) != task_bytes:
        raise _failed(run_id, "TASK or return affinity changed on current main",
                      cause="RECOVERY_TASK_CHANGED")
    task = parse_task(task_bytes.decode("utf-8"))
    from .return_affinity import require_same_affinity
    require_same_affinity(task, source_run)
    if ("kind" not in run_data and _read_optional_blob(repo, artifacts, ".ai/transport/repair.json") is None
            and _changed_files(repo, source_run.base_sha, source) != set(package.result.changed_files)):
        raise _failed(run_id, "original reviewed RESULT changed_files do not match its exact source delta",
                      cause="MALFORMED_LINEAGE")
    observation = _classify_recovery(repo, run_id=run_id, reviewed_sha=source,
        main_sha=expected_main_sha, modification_scope=scope)
    if observation.report.recovery is None:
        raise observation
    decisions, competing = _pending_review_guard(repo, remote, run_id=run_id,
        reviewed_sha=source, main_sha=expected_main_sha)
    binding.update(competing)
    binding.update(_recovery_competing_guard(repo, remote, run_id=run_id,
        reviewed_sha=source, main_sha=expected_main_sha,
        delta=_changed_files(repo, expected_main_sha, observation.report.recovery.tree_sha), decisions=decisions))
    _recheck_binding(repo, remote, binding, run_id=run_id, reviewed_sha=source,
                     main_sha=expected_main_sha, decisions=decisions)
    recovery = observation.report.recovery
    entries = publication_tree_entries(repo, recovery.tree_sha)
    delta = tuple((path, *entries[path]) if path in entries else (path, None, None)
                  for path in sorted(_changed_files(repo, expected_main_sha, recovery.tree_sha)))
    plan = PublicationSourcePlan(run_id, artifacts, decision_sha, source,
        expected_main_sha, recovery.merge_base_sha, recovery.tree_sha,
        task.task_id, task.revision, delta)
    return plan, task, source_run, package, binding, decisions


PUBLICATION_RECOVERY_PATH = ".ai/transport/publication-recovery.json"
PUBLICATION_RECOVERY_PREFIX = "refs/heads/aios/publication-recovery/"


def publication_plan_from_data(value):
    from .correction_integration import PublicationSourcePlan
    from dataclasses import fields
    if not isinstance(value, dict) or set(value) != {field.name for field in fields(PublicationSourcePlan)}:
        raise ValueError("publication recovery plan schema mismatch")
    copied = dict(value)
    delta = copied["delta"]
    if (not isinstance(delta, list) or len(delta) > 4096
            or any(not isinstance(entry, list) or len(entry) != 3 for entry in delta)):
        raise ValueError("publication recovery delta schema mismatch")
    copied["delta"] = tuple(tuple(entry) for entry in delta)
    plan = PublicationSourcePlan(**copied)
    if (_RUN_ID.fullmatch(plan.source_run_id) is None
            or re.fullmatch(r"TASK-[A-Za-z0-9][A-Za-z0-9._-]*", plan.task_id) is None
            or type(plan.task_revision) is not int or plan.task_revision < 1
            or any(not isinstance(sha, str) or _SHA.fullmatch(sha) is None for sha in (
                plan.artifacts_sha, plan.decision_sha, plan.reviewed_sha, plan.main_sha,
                plan.merge_base_sha, plan.tree_sha))
            or [entry[0] for entry in plan.delta] != sorted({entry[0] for entry in plan.delta})
            or any(not isinstance(path, str) or not path or path.startswith("/")
                   or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/"))
                   or not ((mode is None and blob is None)
                           or (mode in {"100644", "100755"} and isinstance(blob, str)
                               and _SHA.fullmatch(blob))) for path, mode, blob in plan.delta)):
        raise ValueError("invalid exact publication recovery identity")
    return plan


def validate_publication_recovery(repo: Path, *, remote: str, raw: bytes,
        run: Run, task, candidate_sha: str, package: ResultPackage | None = None,
        current_main_sha: str | None = None):
    """Bind implementation metadata to ordinary frozen RUN/RESULT authority.

    Prior PASS is immutable provenance, never the recovered source's verdict.
    This validator is shared by Publisher, ingress and the read-only reducer.
    """
    from .correction_integration import publication_tree_entries, publication_source_ref
    if current_main_sha is not None:
        _fetch_object(repo, remote, current_main_sha, run_id=run.run_id)
    document = _mapping(_json_no_duplicates(raw, document="publication recovery"), "publication recovery")
    if (set(document) != {"format", "version", "plan", "identity", "run_id",
                          "candidate_sha", "admission_sha", "evidence_decision"}
            or document["format"] != "AIOS_PUBLICATION_RECOVERY"
            or type(document["version"]) is not int or document["version"] != 1):
        raise ValueError("publication recovery metadata schema mismatch")
    plan = publication_plan_from_data(document["plan"])
    if _single_remote_sha(repo, remote, publication_source_ref(plan.identity), run_id=run.run_id) != candidate_sha:
        raise ValueError("publication recovered source ref changed")
    if (document["identity"] != plan.identity or document["run_id"] != run.run_id
            or document["candidate_sha"] != candidate_sha or plan.source_run_id == run.run_id
            or plan.task_id != task.task_id or plan.task_revision != task.revision
            or run.base_sha != plan.main_sha):
        raise ValueError("publication continuation exact RUN identity mismatch")
    admission_ref = PUBLICATION_RECOVERY_PREFIX + plan.source_run_id
    admission_sha = _single_remote_sha(repo, remote, admission_ref, run_id=run.run_id)
    if admission_sha != document["admission_sha"]:
        raise ValueError("publication admission ref changed")
    _fetch_object(repo, remote, admission_sha, run_id=run.run_id)
    admission = _mapping(_json_no_duplicates(_read_blob(repo, admission_sha,
        PUBLICATION_RECOVERY_PATH, run_id=run.run_id), document="publication admission"), "publication admission")
    admitted_run = _run_from_data(_json_no_duplicates(_read_blob(repo, admission_sha,
        ".ai/transport/run.json", run_id=run.run_id), document="admitted RUN"), "admitted RUN")
    if (set(admission) != {"format", "version", "plan", "identity", "reservation_sha"}
            or admission["format"] != "AIOS_PUBLICATION_RECOVERY_ADMISSION"
            or admission["version"] != 1 or type(admission["version"]) is not int
            or admission["identity"] != plan.identity
            or publication_plan_from_data(admission["plan"]) != plan
            or not isinstance(admission["reservation_sha"], str)
            or _SHA.fullmatch(admission["reservation_sha"]) is None or admitted_run != run):
        raise ValueError("publication recovery lacks exact Runtime admission")
    if _single_remote_sha(repo, remote, f"refs/heads/aios/artifacts/{plan.source_run_id}", run_id=run.run_id) != plan.artifacts_sha:
        raise ValueError("original immutable RESULT provenance changed")
    _fetch_object(repo, remote, plan.artifacts_sha, run_id=run.run_id)
    if _read_optional_blob(repo, plan.artifacts_sha, PUBLICATION_RECOVERY_PATH) is not None:
        raise ValueError("automatic repeated publication recovery is prohibited")
    _fetch_object(repo, remote, plan.decision_sha, run_id=run.run_id)
    if _single_remote_sha(repo, remote, _DECISION_PREFIX + plan.source_run_id, run_id=run.run_id) != plan.decision_sha:
        raise ValueError("original immutable PASS provenance changed")
    original_sha, original_package, _ = _load_success_lineage(repo, remote=remote,
        run_id=plan.source_run_id, decision_sha=plan.decision_sha,
        publication_main_sha=plan.main_sha)
    if original_sha != plan.reviewed_sha:
        raise ValueError("original reviewed source provenance changed")
    observation = _classify_recovery(repo, run_id=plan.source_run_id,
        reviewed_sha=plan.reviewed_sha, main_sha=plan.main_sha, modification_scope=tuple(task.scope.modify))
    if (observation.report.recovery is None or observation.report.recovery.tree_sha != plan.tree_sha
            or observation.report.recovery.merge_base_sha != plan.merge_base_sha):
        raise ValueError("publication integration is not uniquely source preserving")
    _, parents, _ = _git(repo, "rev-parse", f"{candidate_sha}^@")
    _, tree, _ = _git(repo, "rev-parse", f"{candidate_sha}^{{tree}}")
    _, message, _ = _git(repo, "log", "-1", "--format=%B", candidate_sha)
    entries = publication_tree_entries(repo, candidate_sha)
    if (parents.splitlines() != [plan.main_sha, plan.reviewed_sha] or tree != plan.tree_sha
            or message != "AIOS publication source recovery " + plan.identity
            or _changed_files(repo, plan.main_sha, candidate_sha) != {p for p, _, _ in plan.delta}
            or any(entries.get(path) != (None if mode is None else (mode, blob)) for path, mode, blob in plan.delta)
            or _read_optional_blob(repo, candidate_sha, f".ai/tasks/{task.task_id}.yaml")
               != _read_optional_blob(repo, plan.reviewed_sha, f".ai/tasks/{task.task_id}.yaml")):
        raise ValueError("recovered candidate/source/blob/parent binding mismatch")
    if current_main_sha is not None and current_main_sha != plan.main_sha and not _ancestor(
            repo, candidate_sha, current_main_sha, run_id=run.run_id,
            reviewed_sha=candidate_sha, main_sha=current_main_sha):
        raise _failed(run.run_id, "recovery current-main binding is stale",
                      cause="CONCURRENT_MAIN_MOVEMENT")
    if package is not None:
        if set(package.result.changed_files) != {p for p, _, _ in plan.delta}:
            raise ValueError("recovered RESULT changed_files do not match exact preserved source delta")
        audit = document["evidence_decision"]
        expected = dict(recovery_identity=plan.identity, source_run_id=plan.source_run_id,
            source_artifacts_sha=plan.artifacts_sha, source_decision_sha=plan.decision_sha,
            source_sha=plan.reviewed_sha, candidate_sha=candidate_sha, main_sha=plan.main_sha)
        if (not isinstance(audit, dict) or any(audit.get(k) != v for k, v in expected.items())
                or audit.get("format") != "AIOS_PUBLICATION_EVIDENCE_DECISION"
                or audit.get("version") != 1 or audit.get("read_set") != "ENTIRE_TRACKED_TREE"
                or audit.get("integration_obligation") != "EXACT_SOURCE_AND_MAIN_PRESERVATION"
                or not isinstance(audit.get("records"), list)
                or len(audit["records"]) != len(set(task.verification.required))):
            raise ValueError("recovered evidence validity decision is missing or malformed")
        commands = set()
        for record in audit["records"]:
            if (set(record) != {"command", "validity", "disposition", "source_evidence_ids"}
                    or record["command"] in commands
                    or record["command"] not in task.verification.required
                    or record["validity"] not in {"VALID", "INVALIDATED", "UNKNOWN"}
                    or record["disposition"] != ("REUSED" if record["validity"] == "VALID" else "EXECUTED")):
                raise ValueError("unproven publication evidence disposition")
            commands.add(record["command"])
            originals = [item for item in original_package.evidence if item.source.command == record["command"]]
            if record["source_evidence_ids"] != [item.evidence_id for item in originals]:
                raise ValueError("publication evidence lost original immutable provenance")
            matching = [item for item in package.evidence if item.source.command == record["command"]]
            if not matching:
                raise ValueError("missing recovered canonical EVIDENCE")
            if record["disposition"] == "REUSED":
                if (len(originals) != 1 or len(matching) != 1
                        or originals[0].verification is None or matching[0].verification is None
                        or publication_tree_entries(repo, plan.reviewed_sha) != entries):
                    raise ValueError("unknown material proof cannot be reused")
                old, new = originals[0].verification, matching[0].verification
                import hashlib
                receipt = {"decision": "REUSED", "source_evidence_id": originals[0].evidence_id,
                    "source_run_id": originals[0].run_id, "source_artifacts_sha": plan.artifacts_sha,
                    "source_verification": old, "source_raw_digest": old.get("raw_digest"),
                    "candidate_sha": candidate_sha, "conditions": "EXACT_CANDIDATE_AND_BASE_TREES_AND_CONTEXT"}
                expected_digest = hashlib.sha256((json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")).hexdigest()
                if (old["candidate"].get("failure_count") != 0 or originals[0].result.exit_code != 0
                        or publication_tree_entries(repo, old["binding"]["base_sha"])
                           != publication_tree_entries(repo, run.base_sha)
                        or new.get("raw_digest") != expected_digest
                        or old["binding"]["changed_files_digest"] != new["binding"]["changed_files_digest"]
                        or {k: v for k, v in old["candidate"].items() if k != "subject_sha"}
                           != {k: v for k, v in new["candidate"].items() if k != "subject_sha"}
                        or any(old["binding"][key] != new["binding"][key]
                               for key in ("command", "profile", "toolchain", "envelope_digest"))
                        or not matching[0].result.summary.startswith("Runtime VALID: reused ")):
                    raise ValueError("publication reused proof conditions are incompatible")
        if not any(item.type == "PUBLICATION_INTEGRATION"
                   and item.source.command == "aios-publication-source-preservation-v1"
                   and item.subject_sha == candidate_sha and item.result.exit_code == 0
                   for item in package.evidence):
            raise ValueError("distinct publication integration proof is absent")
    return plan


def publish_review_decision(
    repo: str | Path,
    *,
    run_id: str,
    decision_sha: str,
    control_sha: str | None = None,
    remote: str = "origin",
) -> PublicationReport:
    """Validate one immutable decision lineage and fast-forward remote main."""

    root = Path(repo).resolve()
    if _RUN_ID.fullmatch(run_id) is None or "/" in run_id or "\\" in run_id:
        raise _failed(run_id, "invalid source RUN id")
    if _SHA.fullmatch(decision_sha) is None:
        raise _failed(run_id, "invalid review-decision event SHA")
    if control_sha is not None and _SHA.fullmatch(control_sha) is None:
        raise _failed(run_id, "invalid publication control SHA")
    if not remote or remote.startswith("-"):
        raise _failed(run_id, "invalid publication remote")

    main_ref = "refs/heads/main"
    try:
        prior_main_sha = _single_remote_sha(
            root, remote, main_ref, run_id=run_id
        )
    except PublicationError as exc:
        raise _failed(
            run_id,
            str(exc),
        ) from exc
    expected_reviewed_sha = "UNKNOWN"
    try:
        expected_reviewed_sha = _single_remote_sha(
            root,
            remote,
            f"refs/heads/aios/review/{run_id}",
            run_id=run_id,
        )
        if control_sha is not None and control_sha != prior_main_sha:
            raise _failed(
                run_id, "publication control SHA does not match current canonical main",
                cause="STALE_CONTROL_BINDING",
            )
        decision_ref = f"{_DECISION_PREFIX}{run_id}"
        remote_decision_sha = _single_remote_sha(
            root, remote, decision_ref, run_id=run_id
        )
        if remote_decision_sha != decision_sha:
            raise _failed(
                run_id,
                "review-decision event SHA does not match canonical remote ref",
                cause="STALE_REVIEW_BINDING",
            )
        artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"
        binding = {
            decision_ref: decision_sha,
            f"refs/heads/aios/review/{run_id}": expected_reviewed_sha,
            artifacts_ref: _single_remote_sha(root, remote, artifacts_ref, run_id=run_id),
        }
        _fetch_object(root, remote, decision_sha, run_id=run_id)
        reviewed_sha, _, modification_scope = _load_success_lineage(
            root,
            remote=remote,
            run_id=run_id,
            decision_sha=decision_sha,
            publication_main_sha=prior_main_sha,
        )
        recovery_bytes = _read_optional_blob(root, binding[artifacts_ref], PUBLICATION_RECOVERY_PATH)
        if recovery_bytes is not None:
            document = _json_no_duplicates(recovery_bytes, document="publication recovery")
            plan = publication_plan_from_data(document["plan"])
            from .correction_integration import publication_source_ref
            binding.update({PUBLICATION_RECOVERY_PREFIX + plan.source_run_id: document["admission_sha"],
                publication_source_ref(plan.identity): reviewed_sha,
                _DECISION_PREFIX + plan.source_run_id: plan.decision_sha,
                f"refs/heads/aios/artifacts/{plan.source_run_id}": plan.artifacts_sha,
                f"refs/heads/aios/review/{plan.source_run_id}": plan.reviewed_sha})
            # A distinct overlapping PASS may have arrived while this source was
            # awaiting its independent semantic review. Do not publish a winner.
            if not _ancestor(root, reviewed_sha, prior_main_sha, run_id=run_id,
                             reviewed_sha=reviewed_sha, main_sha=prior_main_sha):
                decisions = _decision_snapshot(root, remote, run_id=run_id)
                binding.update(_recovery_competing_guard(root, remote, run_id=run_id,
                    reviewed_sha=reviewed_sha, main_sha=prior_main_sha,
                    delta={path for path, _, _ in plan.delta}, decisions=decisions))
    except PublicationError as exc:
        if exc.report.outcome == "RECOVERY_BLOCKED" and exc.report.blocker is not None:
            blocker = exc.report.blocker
            binding.update({_DECISION_PREFIX + blocker.source_run: blocker.decision_sha,
                            f"refs/heads/aios/review/{blocker.source_run}": blocker.reviewed_sha})
            _recheck_binding(root, remote, binding, run_id=run_id,
                reviewed_sha=exc.report.reviewed_sha, main_sha=prior_main_sha)
            raise
        reviewed = exc.report.reviewed_sha
        if reviewed == "UNKNOWN":
            reviewed = expected_reviewed_sha
        raise _failed(
            run_id,
            str(exc),
            reviewed_sha=reviewed,
            prior_main_sha=prior_main_sha,
            cause=exc.report.cause,
        ) from exc
    try:
        _fetch_object(root, remote, prior_main_sha, run_id=run_id)
    except PublicationError as exc:
        raise _failed(
            run_id,
            str(exc),
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
        ) from exc

    _recheck_binding(root, remote, binding, run_id=run_id,
                     reviewed_sha=reviewed_sha, main_sha=prior_main_sha)

    if prior_main_sha == reviewed_sha:
        _finish_review_reservation(root, remote, run_id=run_id, reviewed_sha=reviewed_sha,
                                   decision_sha=decision_sha, artifacts_sha=binding[artifacts_ref],
                                   main_sha=prior_main_sha, contained=True)
        return PublicationReport(
            source_run=run_id,
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
            outcome="ALREADY_PUBLISHED",
            detail="remote main already equals the reviewed candidate",
            lineage=PublicationLineage(decision_sha, binding[artifacts_ref]),
        )

    code, _, _ = _git(
        root,
        "merge-base",
        "--is-ancestor",
        prior_main_sha,
        reviewed_sha,
        allow_fail=True,
    )
    if code not in (0, 1):
        raise _failed(
            run_id,
            "cannot classify reviewed candidate against remote main",
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
            cause="AMBIGUOUS_ANCESTRY",
        )
    if code == 1:
        included_code, _, _ = _git(
            root,
            "merge-base",
            "--is-ancestor",
            reviewed_sha,
            prior_main_sha,
            allow_fail=True,
        )
        if included_code == 0:
            _recheck_binding(root, remote, binding, run_id=run_id,
                             reviewed_sha=reviewed_sha, main_sha=prior_main_sha)
            _finish_review_reservation(root, remote, run_id=run_id, reviewed_sha=reviewed_sha,
                                       decision_sha=decision_sha, artifacts_sha=binding[artifacts_ref],
                                       main_sha=prior_main_sha, contained=True)
            return PublicationReport(
                source_run=run_id,
                reviewed_sha=reviewed_sha,
                prior_main_sha=prior_main_sha,
                outcome="ALREADY_INCLUDED",
                detail="reviewed candidate is already contained in remote main",
                lineage=PublicationLineage(decision_sha, binding[artifacts_ref]),
            )
        if included_code != 1:
            raise _failed(
                run_id,
                "cannot classify reviewed candidate against remote main",
                reviewed_sha=reviewed_sha,
                prior_main_sha=prior_main_sha,
                cause="AMBIGUOUS_ANCESTRY",
            )
        recovery = _classify_recovery(
            root, run_id=run_id, reviewed_sha=reviewed_sha, main_sha=prior_main_sha,
            modification_scope=modification_scope,
        )
        from dataclasses import replace
        recovery.report = replace(recovery.report,
            lineage=PublicationLineage(decision_sha, binding[artifacts_ref], _reservation_tip(root, remote, run_id)))
        _recheck_binding(root, remote, binding, run_id=run_id,
                         reviewed_sha=reviewed_sha, main_sha=prior_main_sha)
        _finish_review_reservation(root, remote, run_id=run_id, reviewed_sha=reviewed_sha,
                                   decision_sha=decision_sha, artifacts_sha=binding[artifacts_ref],
                                   main_sha=prior_main_sha)
        raise recovery

    try:
        # An existing exact PASS owner continues deterministically. A competing
        # attempt emits contention, not a semantic competing-source doorbell.
        if _reservation_tip(root, remote, run_id) is not None:
            reserve_publication(
                root, remote, kind="REVIEW_TO_PUBLICATION", subject=run_id,
                source_sha=reviewed_sha, main_sha=prior_main_sha, decision_sha=decision_sha,
                artifacts_sha=binding[artifacts_ref],
            )
        decisions, pending_binding = _pending_review_guard(
            root, remote, run_id=run_id, reviewed_sha=reviewed_sha, main_sha=prior_main_sha,
        )
        binding.update(pending_binding)
    except PublicationError as exc:
        # Actionable competing-source outcomes are already bound to this attempt.
        if exc.report.outcome == "RECOVERY_BLOCKED":
            blocker = exc.report.blocker
            if blocker is not None:
                binding[f"{_DECISION_PREFIX}{blocker.source_run}"] = blocker.decision_sha
                binding[f"refs/heads/aios/review/{blocker.source_run}"] = blocker.reviewed_sha
            _recheck_binding(root, remote, binding, run_id=run_id,
                             reviewed_sha=reviewed_sha, main_sha=prior_main_sha)
            raise
        raise _failed(run_id, str(exc), reviewed_sha=reviewed_sha,
                      prior_main_sha=prior_main_sha, cause=exc.report.cause) from exc
    _recheck_binding(root, remote, binding, run_id=run_id,
                     reviewed_sha=reviewed_sha, main_sha=prior_main_sha, decisions=decisions)

    try:
        reservation = reserve_publication(
            root, remote, kind="REVIEW_TO_PUBLICATION", subject=run_id,
            source_sha=reviewed_sha, main_sha=prior_main_sha, decision_sha=decision_sha,
            artifacts_sha=binding[artifacts_ref],
        )
        _recheck_binding(root, remote, binding, run_id=run_id,
                         reviewed_sha=reviewed_sha, main_sha=prior_main_sha, decisions=decisions)
        check_publication_reservation(root, remote, reservation)
    except PublicationError as exc:
        raise _failed(run_id, str(exc), reviewed_sha=reviewed_sha,
                      prior_main_sha=prior_main_sha, cause=exc.report.cause) from exc

    code, output, stderr = _git(
        root,
        "push",
        "--porcelain",
        "--no-tags",
        f"--force-with-lease={main_ref}:{prior_main_sha}",
        remote,
        f"{reviewed_sha}:{main_ref}",
        allow_fail=True,
    )
    if code:
        detail = stderr or output or "remote rejected publication"
        raise _failed(
            run_id,
            f"fast-forward publication failed: {detail}",
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
            cause="MAIN_CAS_FAILED",
        )

    try:
        final_main_sha = _single_remote_sha(
            root, remote, main_ref, run_id=run_id
        )
    except PublicationError as exc:
        raise _failed(
            run_id,
            str(exc),
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
        ) from exc
    if final_main_sha != reviewed_sha:
        raise _failed(
            run_id,
            "remote main postcondition does not equal reviewed candidate",
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
        )
    try:
        release_publication_reservation(root, remote, reservation)
    except PublicationError as exc:
        raise _failed(run_id, str(exc), reviewed_sha=reviewed_sha,
                      prior_main_sha=prior_main_sha, cause=exc.report.cause) from exc
    return PublicationReport(
        source_run=run_id,
        reviewed_sha=reviewed_sha,
        prior_main_sha=prior_main_sha,
        outcome="PUBLISHED",
        detail="remote main advanced by fast-forward to reviewed candidate",
        lineage=PublicationLineage(decision_sha, binding[artifacts_ref], reservation.token_sha),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish one canonical AIOS PASS review decision"
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--decision-sha", required=True)
    parser.add_argument("--control-sha")
    parser.add_argument("--recover-clean", action="store_true",
                        help="Continue uniquely eligible divergence through Runtime and fresh review")
    return parser


def continue_publication(repo: str | Path, *, run_id: str, decision_sha: str,
                         control_sha: str, remote: str = "origin") -> PublicationReport:
    """One durable publication lane; no generic retry or semantic verdict."""
    if (not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None
            or not isinstance(decision_sha, str) or _SHA.fullmatch(decision_sha) is None
            or not isinstance(control_sha, str) or _SHA.fullmatch(control_sha) is None
            or not isinstance(remote, str) or not remote or remote.startswith("-")):
        raise _failed(str(run_id), "invalid exact publication continuation selectors", cause="MALFORMED_LINEAGE")
    root = Path(repo).resolve()
    # An origin replay selects its one admitted destination, never numeric RUN
    # ordering. A recovered-source decision event enters ordinary Publisher.
    existing = _single_optional_remote_sha(root, remote,
        PUBLICATION_RECOVERY_PREFIX + run_id, run_id=run_id)
    if existing is not None:
        _fetch_object(root, remote, existing, run_id=run_id)
        admission = _mapping(_json_no_duplicates(_read_blob(root, existing,
            PUBLICATION_RECOVERY_PATH, run_id=run_id), document="publication admission"), "publication admission")
        plan = publication_plan_from_data(admission["plan"])
        admitted_run = _run_from_data(_json_no_duplicates(_read_blob(root, existing,
            ".ai/transport/run.json", run_id=run_id), document="admitted RUN"), "admitted RUN")
        if (plan.source_run_id != run_id or plan.decision_sha != decision_sha
                or admission.get("identity") != plan.identity):
            raise _failed(run_id, "publication replay changed its exact origin", cause="STALE_REVIEW_BINDING")
        decision = _single_optional_remote_sha(root, remote,
            _DECISION_PREFIX + admitted_run.run_id, run_id=run_id)
        if decision is not None:
            # New canonical PASS remains mandatory in _load_success_lineage.
            return publish_review_decision(root, run_id=admitted_run.run_id,
                decision_sha=decision, control_sha=control_sha, remote=remote)
    else:
        try:
            return publish_review_decision(root, run_id=run_id, decision_sha=decision_sha,
                                           control_sha=control_sha, remote=remote)
        except PublicationError as exc:
            if exc.report.classification != "CLEAN_RECOVERY_ELIGIBLE":
                raise
    if remote != "origin":
        raise _failed(run_id, "Runtime source recovery requires the repository-owned transport remote")
    from .operator import recover_publication_source
    original_source = _single_remote_sha(root, remote, f"refs/heads/aios/review/{run_id}", run_id=run_id)
    try:
        recovered = recover_publication_source(run_id, decision_sha=decision_sha,
                                               expected_main_sha=control_sha, repo=root)
    except PublicationError as exc:
        if (exc.report.source_run == run_id and exc.report.reviewed_sha == original_source
                and exc.report.prior_main_sha == control_sha):
            raise
        raise _failed(run_id, str(exc), reviewed_sha=original_source,
                      prior_main_sha=control_sha, cause=exc.report.cause) from exc
    except (RuntimeError, ValueError, OSError) as exc:
        raise _failed(run_id, "Runtime publication continuation failed: " + str(exc),
            reviewed_sha=original_source,
            prior_main_sha=control_sha) from exc
    return PublicationReport(run_id, _single_remote_sha(root, remote,
        f"refs/heads/aios/review/{run_id}", run_id=run_id), control_sha, "AWAITING_REVIEW",
        f"Runtime RESULT for {recovered.run_id} at {recovered.head_sha}; fresh exact PRIMARY REVIEW required; original PASS is provenance only")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        publisher = continue_publication if args.recover_clean else publish_review_decision
        if args.recover_clean and args.control_sha is None:
            raise _failed(args.run_id, "continuation requires exact expected current main")
        report = publisher(
            args.repo,
            run_id=args.run_id,
            decision_sha=args.decision_sha,
            control_sha=args.control_sha,
            remote=args.remote,
        )
    except PublicationError as exc:
        print(json.dumps({**asdict(exc.report), "classification": exc.report.classification}, sort_keys=True))
        print(str(exc), file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
        # Acquisition/schema failures still have a closed operational outcome;
        # they cannot masquerade as canonical publication or Runtime proof.
        source = "UNKNOWN"
        try:
            source = _single_remote_sha(Path(args.repo).resolve(), args.remote,
                f"refs/heads/aios/review/{args.run_id}", run_id=args.run_id)
        except (PublicationError, RuntimeError, ValueError):
            pass
        failed = _failed(args.run_id, "publication continuation could not bind canonical state: " + str(exc),
            reviewed_sha=source, prior_main_sha=args.control_sha or "UNKNOWN", cause="MALFORMED_LINEAGE")
        print(json.dumps({**asdict(failed.report), "classification": failed.report.classification}, sort_keys=True))
        print(str(failed), file=sys.stderr)
        return 1
    print(json.dumps({**asdict(report), "classification": report.classification}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
