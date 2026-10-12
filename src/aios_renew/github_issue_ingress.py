"""Bounded GitHub-Issue carrier for the canonical authoring ingress."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .authoring_ingress import (AuthoringIngressError, IngressResult, ingest_carrier,
                               parse_envelope, _payload_to_str)
from .origin_authoring_proof import (OriginProofError, authenticate_receipt, binding_for,
                                    admit_local, read_receipt)
from .return_affinity import OriginAffinity
from .task import TaskValidationError, parse_task
from .repair_dispatch import (
    FAILED_RUN_ID_PATTERN,
    REPAIR_DISPATCH_ID_PATTERN,
    REPAIR_SHA_PATTERN,
)


class GitHubIssueIngressError(ValueError):
    """Raised when carrier policy or event framing fails closed."""


_POLICY_FORMAT = "AIOS_BRAIN_INGRESS_CARRIERS_POLICY"
_POLICY_KEYS = frozenset({"format", "version", "github_issue"})
_ISSUE_POLICY_KEYS = frozenset(
    {
        "enabled",
        "repository",
        "authorized_actors",
        "title_marker",
        "max_body_bytes",
    }
)
_MAX_CONFIGURED_BODY_BYTES = 262_144
_MAX_EVENT_FILE_BYTES = 1_048_576
_MAX_RECEIPT_CHARS = 3_500
_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?/[A-Za-z0-9_.-]+$"
)
_RUN_ID_PATTERN = re.compile(r"^RUN-[A-Za-z0-9][A-Za-z0-9._-]*$")
_DECISION_REF_PREFIX = "refs/heads/aios/review-decision/"
_REPAIR_REF_PREFIX = "refs/heads/aios/repair/"
_REPAIR_SUPERSESSION_REF_PREFIX = "refs/heads/aios/repair-supersession/"


def _extract_repair_failed_run_id(destination: str) -> str | None:
    if destination.startswith(_REPAIR_SUPERSESSION_REF_PREFIX):
        remainder = destination[len(_REPAIR_SUPERSESSION_REF_PREFIX) :]
        parts = remainder.split("/")
        if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) >= 2:
            candidate = parts[0]
        else:
            return None
    elif destination.startswith(_REPAIR_REF_PREFIX):
        candidate = destination[len(_REPAIR_REF_PREFIX) :]
    else:
        return None
    if FAILED_RUN_ID_PATTERN.fullmatch(candidate):
        return candidate
    return None


@dataclass(frozen=True)
class GitHubIssuePolicy:
    repository: str
    authorized_actors: tuple[str, ...]
    title_marker: str
    max_body_bytes: int


@dataclass(frozen=True)
class AdmittedIssue:
    repository: str
    number: int
    actor: str
    body_bytes: bytes

    @property
    def identity(self) -> str:
        return f"github-issue:{self.repository}#{self.number}@{self.actor}"


@dataclass(frozen=True)
class IssueDelivery:
    carrier_identity: str
    ingress_result: IngressResult

    @property
    def publication_run_id(self) -> str | None:
        """Extract bounded canonical RUN identity for publication continuation if eligible."""
        if self.ingress_result.operation != "SUBMIT_REVIEW":
            return None
        if self.ingress_result.status not in ("CANONICALIZED", "IDEMPOTENT"):
            return None
        dest = self.ingress_result.canonical_destination
        if not dest.startswith(_DECISION_REF_PREFIX):
            return None
        run_id = dest[len(_DECISION_REF_PREFIX) :]
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            return None
        if self.ingress_result.status == "CANONICALIZED":
            # Avoid publication wakeup when canonical ingress result proves non-PASS
            detail = self.ingress_result.detail
            if "CHANGES_REQUIRED" in detail or "PASS" not in detail:
                return None
        return run_id

    @property
    def repair_failed_run_id(self) -> str | None:
        """Extract bounded canonical failed RUN identity for REPAIR wakeup if eligible."""
        if self.ingress_result.operation != "AUTHOR_REPAIR":
            return None
        if self.ingress_result.status != "CANONICALIZED" or self.ingress_result.replayed:
            return None
        return _extract_repair_failed_run_id(self.ingress_result.canonical_destination)

    @property
    def repair_sha(self) -> str | None:
        """Extract bounded canonical repair authorization SHA if eligible."""
        if self.repair_failed_run_id is None:
            return None
        sha = self.ingress_result.canonical_sha
        if not REPAIR_SHA_PATTERN.fullmatch(sha):
            return None
        return sha

    @property
    def repair_dispatch_id(self) -> str | None:
        """Generate deterministic REPAIR dispatch identity if eligible."""
        failed_run_id = self.repair_failed_run_id
        sha = self.repair_sha
        if failed_run_id is None or sha is None:
            return None
        dispatch_id = f"repair-{failed_run_id}-{sha}"
        if not REPAIR_DISPATCH_ID_PATTERN.fullmatch(dispatch_id):
            return None
        return dispatch_id

    def github_outputs(self) -> str:
        """Render bounded step outputs for outer workflow coordination."""
        run_id = self.publication_run_id
        if run_id is not None:
            return f"publication_run_id={run_id}\nrun_id={run_id}\n"
        dispatch_id = self.repair_dispatch_id
        if dispatch_id is not None:
            failed_run_id = self.repair_failed_run_id
            repair_sha = self.repair_sha
            return (
                f"repair_dispatch_id={dispatch_id}\n"
                f"failed_run_id={failed_run_id}\n"
                f"repair_failed_run_id={failed_run_id}\n"
                f"repair_sha={repair_sha}\n"
            )
        return ""

    def render_receipt(self) -> str:
        receipt = (
            "AIOS BRAIN INGRESS RECEIPT\n"
            f"carrier: {self.carrier_identity}\n"
            f"{self.ingress_result.render()}"
        )
        return _bounded_text(receipt)


def _read_bounded_file(path: Path, *, maximum: int, kind: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise GitHubIssueIngressError(f"cannot inspect {kind} file") from exc
    if size <= 0 or size > maximum:
        raise GitHubIssueIngressError(f"{kind} file size is outside the allowed bound")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GitHubIssueIngressError(f"cannot read {kind} file") from exc


def _mapping(value: Any, kind: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GitHubIssueIngressError(f"{kind} must be a mapping")
    return value


def load_policy(path: str | Path) -> GitHubIssuePolicy:
    """Load and strictly validate the repository-owned carrier policy."""

    policy_path = Path(path)
    raw = _read_bounded_file(policy_path, maximum=65_536, kind="policy")
    try:
        text = raw.decode("utf-8", errors="strict")
        document = yaml.safe_load(text)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise GitHubIssueIngressError("carrier policy is not valid UTF-8 YAML") from exc

    root = _mapping(document, "carrier policy")
    if set(root) != _POLICY_KEYS:
        raise GitHubIssueIngressError("carrier policy has missing or unknown fields")
    version = root.get("version")
    if (
        root.get("format") != _POLICY_FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
    ):
        raise GitHubIssueIngressError("unsupported carrier policy format or version")

    issue = _mapping(root.get("github_issue"), "github_issue policy")
    if set(issue) != _ISSUE_POLICY_KEYS:
        raise GitHubIssueIngressError("github_issue policy has missing or unknown fields")
    if issue.get("enabled") is not True:
        raise GitHubIssueIngressError("GitHub-Issue carrier is disabled")

    repository = issue.get("repository")
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(repository):
        raise GitHubIssueIngressError("carrier repository identity is invalid")

    actors = issue.get("authorized_actors")
    if (
        not isinstance(actors, Sequence)
        or isinstance(actors, (str, bytes))
        or not actors
        or any(not isinstance(actor, str) or not _LOGIN_PATTERN.fullmatch(actor) for actor in actors)
        or len(set(actors)) != len(actors)
    ):
        raise GitHubIssueIngressError("authorized_actors must be a unique non-empty login list")

    title_marker = issue.get("title_marker")
    if not isinstance(title_marker, str) or not title_marker or len(title_marker) > 100:
        raise GitHubIssueIngressError("title_marker must be a bounded non-empty string")

    maximum = issue.get("max_body_bytes")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum <= 0
        or maximum > _MAX_CONFIGURED_BODY_BYTES
    ):
        raise GitHubIssueIngressError("max_body_bytes is outside the carrier bound")

    return GitHubIssuePolicy(
        repository=repository,
        authorized_actors=tuple(actors),
        title_marker=title_marker,
        max_body_bytes=maximum,
    )


def admit_event(path: str | Path, policy: GitHubIssuePolicy) -> AdmittedIssue:
    """Authenticate and frame one immutable GitHub event before semantic ingress."""

    raw = _read_bounded_file(Path(path), maximum=_MAX_EVENT_FILE_BYTES, kind="event")
    try:
        event = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GitHubIssueIngressError("event is not valid UTF-8 JSON") from exc

    root = _mapping(event, "event")
    if root.get("action") != "opened":
        raise GitHubIssueIngressError("event action is not opened")

    repository = _mapping(root.get("repository"), "event repository").get("full_name")
    if not isinstance(repository, str) or repository != policy.repository:
        raise GitHubIssueIngressError("event repository does not match carrier policy")

    issue = _mapping(root.get("issue"), "event issue")
    number = issue.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise GitHubIssueIngressError("event Issue number is invalid")

    actor = _mapping(issue.get("user"), "event Issue author").get("login")
    if not isinstance(actor, str) or actor not in policy.authorized_actors:
        raise GitHubIssueIngressError("event Issue author is not authorized")

    title = issue.get("title")
    if not isinstance(title, str) or title != policy.title_marker:
        raise GitHubIssueIngressError("event Issue title marker is invalid")

    body = issue.get("body")
    if not isinstance(body, str):
        raise GitHubIssueIngressError("event Issue body must be a UTF-8 string")
    try:
        body_bytes = body.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise GitHubIssueIngressError("event Issue body is not valid UTF-8") from exc
    if not body_bytes:
        raise GitHubIssueIngressError("event Issue body is empty")
    if len(body_bytes) > policy.max_body_bytes:
        raise GitHubIssueIngressError("event Issue body exceeds the configured bound")

    return AdmittedIssue(
        repository=repository,
        number=number,
        actor=actor,
        body_bytes=body_bytes,
    )


def deliver_event(
    event_path: str | Path,
    policy_path: str | Path,
    *,
    repo: str | Path,
    origin_admission_path: str | Path | None = None,
    carrier_attempt: str | None = None,
    admission_key: str | None = None,
) -> IssueDelivery:
    """Admit one event and delegate its opaque body to semantic ingress exactly once."""

    policy = load_policy(policy_path)
    issue = admit_event(event_path, policy)
    admission = None
    if origin_admission_path is not None:
        envelope, required = _origin_request(issue)
        if required:
            try:
                admission = authenticate_receipt(read_receipt(origin_admission_path),
                    binding_for(envelope, carrier_attempt or _carrier_attempt(issue)),
                    admission_key or os.environ.get("AIOS_ORIGIN_ADMISSION_KEY"))
            except OriginProofError as exc:
                raise GitHubIssueIngressError(str(exc)) from None
    kwargs = {"origin_admission": admission} if admission is not None else {}
    result = ingest_carrier("-", repo=Path(repo), stdin_bytes=issue.body_bytes, **kwargs)
    return IssueDelivery(carrier_identity=issue.identity, ingress_result=result)


def _origin_request(issue):
    """Frame only; no semantic ingress, canonical writes or selector inference."""
    envelope = parse_envelope(issue.body_bytes)
    required = False
    if envelope.operation == "AUTHOR_TASK":
        try:
            task = parse_task(_payload_to_str(envelope.payload))
        except TaskValidationError:
            raise GitHubIssueIngressError("invalid TASK origin proof request") from None
        required = task.revision == 1 and isinstance(task.return_affinity, OriginAffinity)
    if envelope.origin_authoring_proof is not None and not required:
        raise GitHubIssueIngressError("legacy and revision authoring do not consume origin proof")
    return envelope, required


def _carrier_attempt(issue):
    run_id, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if (os.environ.get("GITHUB_REPOSITORY") != issue.repository
            or not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
            or not re.fullmatch(r"[1-9][0-9]{0,9}", attempt)):
        raise GitHubIssueIngressError("origin proof requires exact GitHub carrier attempt")
    return f"{issue.identity}/run:{run_id}/attempt:{attempt}"


def prepare_origin_admission(event_path, policy_path, receipt_path):
    """Production self-hosted pre-mutation gate. Never invokes semantic ingress."""
    if os.environ.get("RUNNER_ENVIRONMENT") != "self-hosted":
        raise GitHubIssueIngressError("origin proof admission requires self-hosted local state")
    issue = admit_event(event_path, load_policy(policy_path))
    envelope, required = _origin_request(issue)
    if not required:
        raise GitHubIssueIngressError("origin proof admission requires new ORIGIN_AFFINE TASK")
    try:
        from .origin_bootstrap import OriginRegistry, BootstrapBlocked
        from .local_chat_wake import WakeBlocked
        registry_path = os.environ.get("AIOS_ORIGIN_REGISTRY")
        if not registry_path:
            raise OriginProofError("machine-local origin registry is required")
        receipt = admit_local(OriginRegistry(registry_path), binding_for(envelope, _carrier_attempt(issue)),
                             os.environ.get("AIOS_ORIGIN_ADMISSION_KEY"))
        path = Path(receipt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    except (OriginProofError, OSError, ValueError, BootstrapBlocked, WakeBlocked):
        raise GitHubIssueIngressError("origin authoring proof admission failed closed") from None


def _bounded_text(value: str) -> str:
    cleaned = value.replace("\x00", "?")
    if len(cleaned) <= _MAX_RECEIPT_CHARS:
        return cleaned
    return cleaned[: _MAX_RECEIPT_CHARS - 14] + "\n[truncated]"


def render_failure(reason: BaseException) -> str:
    detail = " ".join(str(reason).splitlines()) or reason.__class__.__name__
    return _bounded_text(f"AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nreason: {detail}")


def _write_outputs(output_path: str | Path, outputs: str) -> None:
    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(outputs)
    except OSError as exc:
        raise GitHubIssueIngressError(f"cannot write carrier output: {exc}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deliver one GitHub Issue to AIOS ingress")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--policy", default=".ai/brain-ingress-carriers.yaml")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    parser.add_argument("--mode", choices=("deliver", "frame", "admit-origin"), default="deliver")
    parser.add_argument("--origin-admission")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.event:
        print(render_failure(GitHubIssueIngressError("GITHUB_EVENT_PATH is required")))
        return 1
    try:
        if args.mode == "frame":
            issue = admit_event(args.event, load_policy(args.policy))
            _, required = _origin_request(issue)
            if args.output:
                _write_outputs(args.output, f"origin_proof_required={str(required).lower()}\n")
            print("AIOS origin provenance request framed")
            return 0
        if args.mode == "admit-origin":
            if not args.origin_admission:
                raise GitHubIssueIngressError("origin admission output is required")
            prepare_origin_admission(args.event, args.policy, args.origin_admission)
            print("AIOS origin provenance admitted")
            return 0
        delivery = deliver_event(args.event, args.policy, repo=args.repo,
                                origin_admission_path=args.origin_admission)
        if args.output:
            outputs = delivery.github_outputs()
            if outputs:
                _write_outputs(args.output, outputs)
    except (GitHubIssueIngressError, AuthoringIngressError, OriginProofError, OSError) as exc:
        from .brain_attention import observe_carrier_rejection
        if args.mode == "deliver":
            observe_carrier_rejection(args.event, "INGRESS")
        print(render_failure(exc))
        return 1
    from .brain_attention import observe_ingress_success
    observe_ingress_success(delivery, args.repo)
    print(delivery.render_receipt())
    return 0


if __name__ == "__main__":
    sys.exit(main())
