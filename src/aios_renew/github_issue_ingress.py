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

from .authoring_ingress import AuthoringIngressError, IngressResult, ingest_carrier


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
) -> IssueDelivery:
    """Admit one event and delegate its opaque body to semantic ingress exactly once."""

    policy = load_policy(policy_path)
    issue = admit_event(event_path, policy)
    result = ingest_carrier("-", repo=Path(repo), stdin_bytes=issue.body_bytes)
    return IssueDelivery(carrier_identity=issue.identity, ingress_result=result)


def _bounded_text(value: str) -> str:
    cleaned = value.replace("\x00", "?")
    if len(cleaned) <= _MAX_RECEIPT_CHARS:
        return cleaned
    return cleaned[: _MAX_RECEIPT_CHARS - 14] + "\n[truncated]"


def render_failure(reason: BaseException) -> str:
    detail = " ".join(str(reason).splitlines()) or reason.__class__.__name__
    return _bounded_text(f"AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nreason: {detail}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deliver one GitHub Issue to AIOS ingress")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--policy", default=".ai/brain-ingress-carriers.yaml")
    parser.add_argument("--repo", default=".")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.event:
        print(render_failure(GitHubIssueIngressError("GITHUB_EVENT_PATH is required")))
        return 1
    try:
        delivery = deliver_event(args.event, args.policy, repo=args.repo)
    except (GitHubIssueIngressError, AuthoringIngressError, OSError) as exc:
        print(render_failure(exc))
        return 1
    print(delivery.render_receipt())
    return 0


if __name__ == "__main__":
    sys.exit(main())
