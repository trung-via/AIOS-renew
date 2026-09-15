"""Fail-closed GitHub-Issue carrier for one Human remediation intent."""

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

from .correction_dispatch import (
    CORRECTION_DISPATCH_ID_PATTERN,
    SUPPORTED_EXECUTORS,
)


class GitHubIssueRemediationIntentError(ValueError):
    """Raised when policy, event framing, or request data fails closed."""


_POLICY_FORMAT = "AIOS_BRAIN_REMEDIATION_INTENT_CARRIERS_POLICY"
_REQUEST_FORMAT = "AIOS_REMEDIATION_INTENT_REQUEST"
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
_REQUEST_KEYS = frozenset(
    {
        "format",
        "version",
        "correction_dispatch_id",
        "source_run_id",
        "finding_id",
        "executor",
    }
)
_SOURCE_RUN_PATTERN = re.compile(r"^RUN-[A-Za-z0-9_-]+-\d{3,}$")
_FINDING_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_APPROVER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\[\]]{0,99}$")
_MAX_CONFIGURED_BODY_BYTES = 16_384
_MAX_EVENT_FILE_BYTES = 1_048_576
_MAX_RECEIPT_CHARS = 3_500
_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?/[A-Za-z0-9_.-]+$"
)


@dataclass(frozen=True)
class GitHubIssueRemediationIntentPolicy:
    repository: str
    authorized_actors: tuple[str, ...]
    title_marker: str
    max_body_bytes: int


@dataclass(frozen=True)
class RemediationIntentRequest:
    correction_dispatch_id: str
    source_run_id: str
    finding_id: str
    executor: str
    approver: str

    def github_outputs(self) -> str:
        """Render only the four grammar-constrained intent selectors."""

        return (
            f"correction_dispatch_id={self.correction_dispatch_id}\n"
            f"source_run_id={self.source_run_id}\n"
            f"finding_id={self.finding_id}\n"
            f"executor={self.executor}\n"
        )


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise GitHubIssueRemediationIntentError(
                "mapping key must be scalar"
            ) from exc
        if duplicate:
            raise GitHubIssueRemediationIntentError(
                f"mapping contains duplicate key: {key}"
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _read_bounded_file(path: Path, *, maximum: int, kind: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise GitHubIssueRemediationIntentError(
            f"cannot inspect {kind} file"
        ) from exc
    if size <= 0 or size > maximum:
        raise GitHubIssueRemediationIntentError(
            f"{kind} file size is outside the allowed bound"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GitHubIssueRemediationIntentError(
            f"cannot read {kind} file"
        ) from exc


def _mapping(value: Any, kind: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GitHubIssueRemediationIntentError(f"{kind} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise GitHubIssueRemediationIntentError(f"{kind} keys must be strings")
    return value


def _load_yaml(raw: bytes, kind: str) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except GitHubIssueRemediationIntentError:
        raise
    except (UnicodeError, yaml.YAMLError) as exc:
        raise GitHubIssueRemediationIntentError(
            f"{kind} is not valid UTF-8 YAML/JSON"
        ) from exc


def load_policy(path: str | Path) -> GitHubIssueRemediationIntentPolicy:
    """Load and strictly validate the repository-owned carrier policy."""

    raw = _read_bounded_file(Path(path), maximum=65_536, kind="policy")
    root = _mapping(_load_yaml(raw, "carrier policy"), "carrier policy")
    if set(root) != _POLICY_KEYS:
        raise GitHubIssueRemediationIntentError(
            "carrier policy has missing or unknown fields"
        )
    version = root.get("version")
    if (
        root.get("format") != _POLICY_FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
    ):
        raise GitHubIssueRemediationIntentError(
            "unsupported carrier policy format or version"
        )

    issue = _mapping(root.get("github_issue"), "github_issue policy")
    if set(issue) != _ISSUE_POLICY_KEYS:
        raise GitHubIssueRemediationIntentError(
            "github_issue policy has missing or unknown fields"
        )
    if issue.get("enabled") is not True:
        raise GitHubIssueRemediationIntentError(
            "GitHub-Issue remediation-intent carrier is disabled"
        )
    repository = issue.get("repository")
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(
        repository
    ):
        raise GitHubIssueRemediationIntentError(
            "carrier repository identity is invalid"
        )
    actors = issue.get("authorized_actors")
    if (
        not isinstance(actors, Sequence)
        or isinstance(actors, (str, bytes))
        or not actors
        or any(
            not isinstance(actor, str) or not _LOGIN_PATTERN.fullmatch(actor)
            for actor in actors
        )
        or len(set(actors)) != len(actors)
    ):
        raise GitHubIssueRemediationIntentError(
            "carrier actor allowlist is invalid"
        )
    title_marker = issue.get("title_marker")
    if title_marker != "[AIOS REMEDIATION INTENT]":
        raise GitHubIssueRemediationIntentError(
            "carrier title marker is invalid"
        )
    maximum = issue.get("max_body_bytes")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum <= 0
        or maximum > _MAX_CONFIGURED_BODY_BYTES
    ):
        raise GitHubIssueRemediationIntentError(
            "max_body_bytes is outside the carrier bound"
        )
    return GitHubIssueRemediationIntentPolicy(
        repository=repository,
        authorized_actors=tuple(actors),
        title_marker=title_marker,
        max_body_bytes=maximum,
    )


def _json_no_duplicates(raw: bytes) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise GitHubIssueRemediationIntentError(
                    f"event contains duplicate key: {key}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs
        )
    except GitHubIssueRemediationIntentError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GitHubIssueRemediationIntentError(
            "event is not valid UTF-8 JSON"
        ) from exc
    return _mapping(value, "event")


def admit_event(
    path: str | Path, policy: GitHubIssueRemediationIntentPolicy
) -> RemediationIntentRequest:
    """Authenticate one opened Issue and parse its inert request body."""

    raw = _read_bounded_file(
        Path(path), maximum=_MAX_EVENT_FILE_BYTES, kind="event"
    )
    event = _json_no_duplicates(raw)
    if event.get("action") != "opened":
        raise GitHubIssueRemediationIntentError("event action is not opened")
    repository = _mapping(event.get("repository"), "event repository").get(
        "full_name"
    )
    if repository != policy.repository:
        raise GitHubIssueRemediationIntentError(
            "event repository does not match carrier policy"
        )
    issue = _mapping(event.get("issue"), "event issue")
    number = issue.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise GitHubIssueRemediationIntentError(
            "event Issue number is invalid"
        )
    author = _mapping(issue.get("user"), "event Issue author").get("login")
    actor = _mapping(event.get("sender"), "event actor").get("login")
    if (
        author not in policy.authorized_actors
        or actor not in policy.authorized_actors
        or actor != author
        or not isinstance(actor, str)
        or not _APPROVER_PATTERN.fullmatch(actor)
    ):
        raise GitHubIssueRemediationIntentError(
            "event Issue actor is not authorized"
        )
    if issue.get("title") != policy.title_marker:
        raise GitHubIssueRemediationIntentError(
            "event Issue title marker is invalid"
        )
    body = issue.get("body")
    if not isinstance(body, str):
        raise GitHubIssueRemediationIntentError(
            "event Issue body must be a UTF-8 string"
        )
    try:
        body_bytes = body.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise GitHubIssueRemediationIntentError(
            "event Issue body is not valid UTF-8"
        ) from exc
    if not body_bytes:
        raise GitHubIssueRemediationIntentError("event Issue body is empty")
    if len(body_bytes) > policy.max_body_bytes:
        raise GitHubIssueRemediationIntentError(
            "event Issue body exceeds the configured bound"
        )
    parsed = parse_request(body_bytes)
    return RemediationIntentRequest(
        correction_dispatch_id=parsed.correction_dispatch_id,
        source_run_id=parsed.source_run_id,
        finding_id=parsed.finding_id,
        executor=parsed.executor,
        approver=actor,
    )


def parse_request(raw: bytes | str) -> RemediationIntentRequest:
    """Parse only the versioned four-selector remediation intent."""

    source = raw.encode("utf-8", errors="strict") if isinstance(raw, str) else raw
    request = _mapping(
        _load_yaml(source, "remediation intent request"),
        "remediation intent request",
    )
    if set(request) != _REQUEST_KEYS:
        raise GitHubIssueRemediationIntentError(
            "remediation intent request has missing or unknown fields"
        )
    version = request.get("version")
    if (
        request.get("format") != _REQUEST_FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
    ):
        raise GitHubIssueRemediationIntentError(
            "unsupported remediation intent request format or version"
        )
    correction_dispatch_id = request.get("correction_dispatch_id")
    source_run_id = request.get("source_run_id")
    finding_id = request.get("finding_id")
    executor = request.get("executor")
    if (
        not isinstance(correction_dispatch_id, str)
        or not CORRECTION_DISPATCH_ID_PATTERN.fullmatch(correction_dispatch_id)
    ):
        raise GitHubIssueRemediationIntentError(
            "invalid correction_dispatch_id"
        )
    if (
        not isinstance(source_run_id, str)
        or not _SOURCE_RUN_PATTERN.fullmatch(source_run_id)
    ):
        raise GitHubIssueRemediationIntentError("invalid source_run_id")
    if (
        not isinstance(finding_id, str)
        or not _FINDING_PATTERN.fullmatch(finding_id)
    ):
        raise GitHubIssueRemediationIntentError("invalid finding_id")
    if not isinstance(executor, str) or executor not in SUPPORTED_EXECUTORS:
        raise GitHubIssueRemediationIntentError("unsupported executor")
    return RemediationIntentRequest(
        correction_dispatch_id=correction_dispatch_id,
        source_run_id=source_run_id,
        finding_id=finding_id,
        executor=executor,
        approver="",
    )


def _bounded_text(value: str) -> str:
    cleaned = value.replace("\x00", "?")
    if len(cleaned) <= _MAX_RECEIPT_CHARS:
        return cleaned
    return cleaned[: _MAX_RECEIPT_CHARS - 14] + "\n[truncated]"


def render_rejection(reason: BaseException) -> str:
    detail = " ".join(str(reason).splitlines()) or reason.__class__.__name__
    return _bounded_text(
        "AIOS REMEDIATION INTENT CARRIER RECEIPT\n"
        "status: REJECTED\n"
        "dispatch_accepted: false\n"
        "a3_approval: not_observed\n"
        "remediation_run_outcome: not_observed\n"
        f"reason: {detail}"
    )


def render_admitted(request: RemediationIntentRequest) -> str:
    return (
        "AIOS REMEDIATION INTENT CARRIER RECEIPT\n"
        "status: ADMITTED\n"
        "dispatch_accepted: pending\n"
        "a3_approval: not_observed\n"
        "remediation_run_outcome: not_observed\n"
        f"correction_dispatch_id: {request.correction_dispatch_id}\n"
        f"source_run_id: {request.source_run_id}\n"
        f"finding_id: {request.finding_id}\n"
        f"executor: {request.executor}\n"
        f"approver: {request.approver}"
    )


def _write_text(path: str | Path, value: str) -> None:
    try:
        Path(path).write_text(value, encoding="utf-8")
    except OSError as exc:
        raise GitHubIssueRemediationIntentError(
            "cannot write bounded carrier output"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Admit one GitHub Issue remediation intent"
    )
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument(
        "--policy", default=".ai/brain-remediation-intent-carriers.yaml"
    )
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    parser.add_argument("--receipt", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.event:
            raise GitHubIssueRemediationIntentError(
                "GITHUB_EVENT_PATH is required"
            )
        if not args.output:
            raise GitHubIssueRemediationIntentError("GITHUB_OUTPUT is required")
        policy = load_policy(args.policy)
        request = admit_event(args.event, policy)
        _write_text(args.output, request.github_outputs())
        _write_text(args.receipt, render_admitted(request))
    except GitHubIssueRemediationIntentError as exc:
        try:
            _write_text(args.receipt, render_rejection(exc))
        except GitHubIssueRemediationIntentError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
