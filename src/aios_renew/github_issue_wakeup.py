"""Fail-closed GitHub-Issue carrier for one fixed A1 PRIMARY wakeup."""

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

from .dispatch_reconciliation import (
    DISPATCH_ID_PATTERN,
    GIT_OBJECT_SHA_PATTERN,
    SUPPORTED_EXECUTORS,
    TASK_ID_PATTERN,
)
from .execution_profile import ExecutionProfileError, bind_execution_profile


class GitHubIssueWakeupError(ValueError):
    """Raised when policy, event framing, or request data fails closed."""


_POLICY_FORMAT = "AIOS_BRAIN_WAKEUP_CARRIERS_POLICY"
_REQUEST_FORMAT = "AIOS_PRIMARY_WAKEUP_REQUEST"
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
        "dispatch_id",
        "task_id",
        "task_revision",
        "task_blob_sha",
        "task_commit_sha",
        "executor",
        "model",
        "reasoning_effort",
    }
)
_MAX_CONFIGURED_BODY_BYTES = 16_384
_MAX_EVENT_FILE_BYTES = 1_048_576
_MAX_RECEIPT_CHARS = 3_500
_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?/[A-Za-z0-9_.-]+$"
)


@dataclass(frozen=True)
class GitHubIssueWakeupPolicy:
    repository: str
    authorized_actors: tuple[str, ...]
    title_marker: str
    max_body_bytes: int


@dataclass(frozen=True)
class WakeupRequest:
    dispatch_id: str
    task_id: str
    task_revision: int
    task_blob_sha: str
    task_commit_sha: str
    executor: str
    model: str
    reasoning_effort: str
    model_source: str
    effort_source: str

    def github_outputs(self) -> str:
        """Render only the six grammar-constrained A1 inputs."""

        return (
            f"dispatch_id={self.dispatch_id}\n"
            f"task_id={self.task_id}\n"
            f"task_revision={self.task_revision}\n"
            f"task_blob_sha={self.task_blob_sha}\n"
            f"task_commit_sha={self.task_commit_sha}\n"
            f"executor={self.executor}\n"
            f"model={self.model}\n"
            f"reasoning_effort={self.reasoning_effort}\n"
            f"model_source={self.model_source}\n"
            f"effort_source={self.effort_source}\n"
        )


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that also rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise GitHubIssueWakeupError("mapping key must be scalar") from exc
        if duplicate:
            raise GitHubIssueWakeupError(f"mapping contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _read_bounded_file(path: Path, *, maximum: int, kind: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise GitHubIssueWakeupError(f"cannot inspect {kind} file") from exc
    if size <= 0 or size > maximum:
        raise GitHubIssueWakeupError(f"{kind} file size is outside the allowed bound")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GitHubIssueWakeupError(f"cannot read {kind} file") from exc


def _mapping(value: Any, kind: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GitHubIssueWakeupError(f"{kind} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise GitHubIssueWakeupError(f"{kind} keys must be strings")
    return value


def _load_yaml(raw: bytes, kind: str) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except GitHubIssueWakeupError:
        raise
    except (UnicodeError, yaml.YAMLError) as exc:
        raise GitHubIssueWakeupError(f"{kind} is not valid UTF-8 YAML/JSON") from exc


def load_policy(path: str | Path) -> GitHubIssueWakeupPolicy:
    """Load the versioned repository-owned carrier policy."""

    raw = _read_bounded_file(Path(path), maximum=65_536, kind="policy")
    root = _mapping(_load_yaml(raw, "carrier policy"), "carrier policy")
    if set(root) != _POLICY_KEYS:
        raise GitHubIssueWakeupError("carrier policy has missing or unknown fields")
    version = root.get("version")
    if (
        root.get("format") != _POLICY_FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
    ):
        raise GitHubIssueWakeupError("unsupported carrier policy format or version")

    issue = _mapping(root.get("github_issue"), "github_issue policy")
    if set(issue) != _ISSUE_POLICY_KEYS:
        raise GitHubIssueWakeupError(
            "github_issue policy has missing or unknown fields"
        )
    if issue.get("enabled") is not True:
        raise GitHubIssueWakeupError("GitHub-Issue wakeup carrier is disabled")

    repository = issue.get("repository")
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(
        repository
    ):
        raise GitHubIssueWakeupError("carrier repository identity is invalid")

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
        raise GitHubIssueWakeupError("carrier actor allowlist is invalid")

    title_marker = issue.get("title_marker")
    if title_marker != "[AIOS BRAIN WAKEUP]":
        raise GitHubIssueWakeupError("carrier title marker is invalid")

    maximum = issue.get("max_body_bytes")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum <= 0
        or maximum > _MAX_CONFIGURED_BODY_BYTES
    ):
        raise GitHubIssueWakeupError("max_body_bytes is outside the carrier bound")

    return GitHubIssueWakeupPolicy(
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
                raise GitHubIssueWakeupError(
                    f"event contains duplicate key: {key}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs
        )
    except GitHubIssueWakeupError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GitHubIssueWakeupError("event is not valid UTF-8 JSON") from exc
    return _mapping(value, "event")


def admit_event(
    path: str | Path, policy: GitHubIssueWakeupPolicy
) -> WakeupRequest:
    """Authenticate one opened Issue and parse its strict request body."""

    raw = _read_bounded_file(
        Path(path), maximum=_MAX_EVENT_FILE_BYTES, kind="event"
    )
    event = _json_no_duplicates(raw)
    if event.get("action") != "opened":
        raise GitHubIssueWakeupError("event action is not opened")

    repository = _mapping(event.get("repository"), "event repository").get(
        "full_name"
    )
    if repository != policy.repository:
        raise GitHubIssueWakeupError(
            "event repository does not match carrier policy"
        )

    issue = _mapping(event.get("issue"), "event issue")
    number = issue.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise GitHubIssueWakeupError("event Issue number is invalid")

    author = _mapping(issue.get("user"), "event Issue author").get("login")
    actor = _mapping(event.get("sender"), "event actor").get("login")
    if (
        author not in policy.authorized_actors
        or actor not in policy.authorized_actors
        or actor != author
    ):
        raise GitHubIssueWakeupError("event Issue actor is not authorized")

    if issue.get("title") != policy.title_marker:
        raise GitHubIssueWakeupError("event Issue title marker is invalid")

    body = issue.get("body")
    if not isinstance(body, str):
        raise GitHubIssueWakeupError("event Issue body must be a UTF-8 string")
    try:
        body_bytes = body.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise GitHubIssueWakeupError(
            "event Issue body is not valid UTF-8"
        ) from exc
    if not body_bytes:
        raise GitHubIssueWakeupError("event Issue body is empty")
    if len(body_bytes) > policy.max_body_bytes:
        raise GitHubIssueWakeupError(
            "event Issue body exceeds the configured bound"
        )
    return parse_request(body_bytes)


def parse_request(raw: bytes | str) -> WakeupRequest:
    """Parse and resolve only the exact version-3 PRIMARY wakeup request."""

    source = raw.encode("utf-8", errors="strict") if isinstance(raw, str) else raw
    request = _mapping(_load_yaml(source, "wakeup request"), "wakeup request")
    if set(request) != _REQUEST_KEYS:
        raise GitHubIssueWakeupError(
            "wakeup request has missing or unknown fields"
        )

    version = request.get("version")
    if (
        request.get("format") != _REQUEST_FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != 3
    ):
        raise GitHubIssueWakeupError(
            "unsupported wakeup request format or version"
        )

    dispatch_id = request.get("dispatch_id")
    task_id = request.get("task_id")
    task_revision = request.get("task_revision")
    task_blob_sha = request.get("task_blob_sha")
    task_commit_sha = request.get("task_commit_sha")
    executor = request.get("executor")
    requested_model = request.get("model")
    requested_effort = request.get("reasoning_effort")
    if not isinstance(dispatch_id, str) or not DISPATCH_ID_PATTERN.fullmatch(
        dispatch_id
    ):
        raise GitHubIssueWakeupError("invalid dispatch_id")
    if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
        raise GitHubIssueWakeupError("invalid task_id")
    if (
        isinstance(task_revision, bool)
        or not isinstance(task_revision, int)
        or task_revision < 1
    ):
        raise GitHubIssueWakeupError("invalid task_revision")
    if not isinstance(task_blob_sha, str) or not GIT_OBJECT_SHA_PATTERN.fullmatch(
        task_blob_sha
    ):
        raise GitHubIssueWakeupError("invalid task_blob_sha")
    if not isinstance(task_commit_sha, str) or not GIT_OBJECT_SHA_PATTERN.fullmatch(
        task_commit_sha
    ):
        raise GitHubIssueWakeupError("invalid task_commit_sha")
    if not isinstance(executor, str) or executor not in SUPPORTED_EXECUTORS:
        raise GitHubIssueWakeupError("unsupported executor")
    if requested_model is not None and not isinstance(requested_model, str):
        raise GitHubIssueWakeupError("invalid model selection")
    if requested_effort is not None and not isinstance(requested_effort, str):
        raise GitHubIssueWakeupError("invalid reasoning_effort selection")
    try:
        profile = bind_execution_profile(
            run_id=dispatch_id,
            executor=executor,
            model=requested_model,
            reasoning_effort=requested_effort,
        )
    except ExecutionProfileError as exc:
        raise GitHubIssueWakeupError(str(exc)) from exc
    return WakeupRequest(
        dispatch_id=dispatch_id,
        task_id=task_id,
        task_revision=task_revision,
        task_blob_sha=task_blob_sha,
        task_commit_sha=task_commit_sha,
        executor=executor,
        model=profile.model,
        reasoning_effort=profile.reasoning_effort,
        model_source=profile.model_source,
        effort_source=profile.effort_source,
    )


def _bounded_text(value: str) -> str:
    cleaned = value.replace("\x00", "?")
    if len(cleaned) <= _MAX_RECEIPT_CHARS:
        return cleaned
    return cleaned[: _MAX_RECEIPT_CHARS - 14] + "\n[truncated]"


def render_rejection(reason: BaseException) -> str:
    detail = " ".join(str(reason).splitlines()) or reason.__class__.__name__
    return _bounded_text(
        "AIOS BRAIN WAKEUP RECEIPT\n"
        "status: REJECTED\n"
        "dispatch_accepted: false\n"
        "execution_outcome: not_observed\n"
        f"reason: {detail}"
    )


def render_admitted(request: WakeupRequest) -> str:
    return (
        "AIOS BRAIN WAKEUP RECEIPT\n"
        "status: ADMITTED\n"
        "dispatch_accepted: pending\n"
        "execution_outcome: not_observed\n"
        f"dispatch_id: {request.dispatch_id}\n"
        f"task_id: {request.task_id}\n"
        f"task_revision: {request.task_revision}\n"
        f"task_blob_sha: {request.task_blob_sha}\n"
        f"task_commit_sha: {request.task_commit_sha}\n"
        f"executor: {request.executor}"
        f"\nmodel: {request.model}\n"
        f"reasoning_effort: {request.reasoning_effort}\n"
        f"model_source: {request.model_source}\n"
        f"effort_source: {request.effort_source}"
    )


def _write_text(path: str | Path, value: str) -> None:
    try:
        Path(path).write_text(value, encoding="utf-8")
    except OSError as exc:
        raise GitHubIssueWakeupError("cannot write bounded carrier output") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Admit one GitHub Issue for the fixed A1 wakeup"
    )
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--policy", default=".ai/brain-wakeup-carriers.yaml")
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    parser.add_argument("--receipt", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.event:
            raise GitHubIssueWakeupError("GITHUB_EVENT_PATH is required")
        if not args.output:
            raise GitHubIssueWakeupError("GITHUB_OUTPUT is required")
        policy = load_policy(args.policy)
        request = admit_event(args.event, policy)
        _write_text(args.output, request.github_outputs())
        _write_text(args.receipt, render_admitted(request))
    except GitHubIssueWakeupError as exc:
        try:
            _write_text(args.receipt, render_rejection(exc))
        except GitHubIssueWakeupError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
