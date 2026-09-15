"""Fail-closed GitHub-Issue carrier for one canonical REPAIR wakeup."""

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

from .repair_dispatch import (
    FAILED_RUN_ID_PATTERN,
    REPAIR_DISPATCH_ID_PATTERN,
    REPAIR_SHA_PATTERN,
    SUPPORTED_EXECUTORS,
)


class GitHubIssueRepairWakeupError(ValueError):
    """Raised when policy, event framing, or request data fails closed."""


_POLICY_FORMAT = "AIOS_BRAIN_REPAIR_WAKEUP_CARRIERS_POLICY"
_REQUEST_FORMAT = "AIOS_REPAIR_WAKEUP_REQUEST"
_POLICY_KEYS = frozenset({"format", "version", "github_issue"})
_ISSUE_POLICY_KEYS = frozenset(
    {"enabled", "repository", "authorized_actors", "title_marker", "max_body_bytes"}
)
_BASE_REQUEST_KEYS = frozenset(
    {"format", "version", "repair_dispatch_id", "failed_run_id", "repair_sha"}
)
_APPROVER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\[\]]{0,99}$")
_MAX_CONFIGURED_BODY_BYTES = 16_384
_MAX_EVENT_FILE_BYTES = 1_048_576
_MAX_RECEIPT_CHARS = 3_500
_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?/[A-Za-z0-9_.-]+$"
)


@dataclass(frozen=True)
class GitHubIssueRepairWakeupPolicy:
    repository: str
    authorized_actors: tuple[str, ...]
    title_marker: str
    max_body_bytes: int


@dataclass(frozen=True)
class RepairWakeupRequest:
    repair_dispatch_id: str
    failed_run_id: str
    repair_sha: str
    executor: str | None
    actor: str = ""

    def github_outputs(self) -> str:
        """Render only bounded selector values for the fixed reusable workflow."""

        return (
            f"repair_dispatch_id={self.repair_dispatch_id}\n"
            f"failed_run_id={self.failed_run_id}\n"
            f"repair_sha={self.repair_sha}\n"
            f"executor={self.executor or ''}\n"
        )


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise GitHubIssueRepairWakeupError("mapping key must be scalar") from exc
        if duplicate:
            raise GitHubIssueRepairWakeupError(f"mapping contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _read_bounded_file(path: Path, *, maximum: int, kind: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise GitHubIssueRepairWakeupError(f"cannot inspect {kind} file") from exc
    if size <= 0 or size > maximum:
        raise GitHubIssueRepairWakeupError(
            f"{kind} file size is outside the allowed bound"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GitHubIssueRepairWakeupError(f"cannot read {kind} file") from exc


def _mapping(value: Any, kind: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GitHubIssueRepairWakeupError(f"{kind} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise GitHubIssueRepairWakeupError(f"{kind} keys must be strings")
    return value


def _load_yaml(raw: bytes, kind: str) -> Any:
    try:
        return yaml.load(raw.decode("utf-8", errors="strict"), Loader=_UniqueKeyLoader)
    except GitHubIssueRepairWakeupError:
        raise
    except (UnicodeError, yaml.YAMLError) as exc:
        raise GitHubIssueRepairWakeupError(
            f"{kind} is not valid UTF-8 YAML/JSON"
        ) from exc


def load_policy(path: str | Path) -> GitHubIssueRepairWakeupPolicy:
    raw = _read_bounded_file(Path(path), maximum=65_536, kind="policy")
    root = _mapping(_load_yaml(raw, "carrier policy"), "carrier policy")
    if set(root) != _POLICY_KEYS:
        raise GitHubIssueRepairWakeupError(
            "carrier policy has missing or unknown fields"
        )
    version = root.get("version")
    if (
        root.get("format") != _POLICY_FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
    ):
        raise GitHubIssueRepairWakeupError(
            "unsupported carrier policy format or version"
        )
    issue = _mapping(root.get("github_issue"), "github_issue policy")
    if set(issue) != _ISSUE_POLICY_KEYS:
        raise GitHubIssueRepairWakeupError(
            "github_issue policy has missing or unknown fields"
        )
    if issue.get("enabled") is not True:
        raise GitHubIssueRepairWakeupError("GitHub-Issue REPAIR carrier is disabled")
    repository = issue.get("repository")
    actors = issue.get("authorized_actors")
    title = issue.get("title_marker")
    maximum = issue.get("max_body_bytes")
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(
        repository
    ):
        raise GitHubIssueRepairWakeupError("carrier repository identity is invalid")
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
        raise GitHubIssueRepairWakeupError("carrier actor allowlist is invalid")
    if title != "[AIOS REPAIR WAKEUP]":
        raise GitHubIssueRepairWakeupError("carrier title marker is invalid")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum <= 0
        or maximum > _MAX_CONFIGURED_BODY_BYTES
    ):
        raise GitHubIssueRepairWakeupError(
            "max_body_bytes is outside the carrier bound"
        )
    return GitHubIssueRepairWakeupPolicy(repository, tuple(actors), title, maximum)


def _json_no_duplicates(raw: bytes) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise GitHubIssueRepairWakeupError(
                    f"event contains duplicate key: {key}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs)
    except GitHubIssueRepairWakeupError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GitHubIssueRepairWakeupError("event is not valid UTF-8 JSON") from exc
    return _mapping(value, "event")


def admit_event(
    path: str | Path, policy: GitHubIssueRepairWakeupPolicy
) -> RepairWakeupRequest:
    raw = _read_bounded_file(Path(path), maximum=_MAX_EVENT_FILE_BYTES, kind="event")
    event = _json_no_duplicates(raw)
    if event.get("action") != "opened":
        raise GitHubIssueRepairWakeupError("event action is not opened")
    repository = _mapping(event.get("repository"), "event repository").get("full_name")
    if repository != policy.repository:
        raise GitHubIssueRepairWakeupError(
            "event repository does not match carrier policy"
        )
    issue = _mapping(event.get("issue"), "event issue")
    number = issue.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise GitHubIssueRepairWakeupError("event Issue number is invalid")
    author = _mapping(issue.get("user"), "event Issue author").get("login")
    actor = _mapping(event.get("sender"), "event actor").get("login")
    if (
        author not in policy.authorized_actors
        or actor not in policy.authorized_actors
        or actor != author
        or not isinstance(actor, str)
        or not _APPROVER_PATTERN.fullmatch(actor)
    ):
        raise GitHubIssueRepairWakeupError("event Issue actor is not authorized")
    if issue.get("title") != policy.title_marker:
        raise GitHubIssueRepairWakeupError("event Issue title marker is invalid")
    body = issue.get("body")
    if not isinstance(body, str):
        raise GitHubIssueRepairWakeupError("event Issue body must be a UTF-8 string")
    try:
        body_bytes = body.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise GitHubIssueRepairWakeupError(
            "event Issue body is not valid UTF-8"
        ) from exc
    if not body_bytes:
        raise GitHubIssueRepairWakeupError("event Issue body is empty")
    if len(body_bytes) > policy.max_body_bytes:
        raise GitHubIssueRepairWakeupError(
            "event Issue body exceeds the configured bound"
        )
    request = parse_request(body_bytes)
    return RepairWakeupRequest(
        request.repair_dispatch_id,
        request.failed_run_id,
        request.repair_sha,
        request.executor,
        actor,
    )


def parse_request(raw: bytes | str) -> RepairWakeupRequest:
    source = raw.encode("utf-8", errors="strict") if isinstance(raw, str) else raw
    request = _mapping(
        _load_yaml(source, "REPAIR wakeup request"), "REPAIR wakeup request"
    )
    keys = set(request)
    if keys not in (_BASE_REQUEST_KEYS, _BASE_REQUEST_KEYS | {"executor"}):
        raise GitHubIssueRepairWakeupError(
            "REPAIR wakeup request has missing or unknown fields"
        )
    version = request.get("version")
    if (
        request.get("format") != _REQUEST_FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
    ):
        raise GitHubIssueRepairWakeupError(
            "unsupported REPAIR wakeup request format or version"
        )
    repair_dispatch_id = request.get("repair_dispatch_id")
    failed_run_id = request.get("failed_run_id")
    repair_sha = request.get("repair_sha")
    executor = request.get("executor")
    if (
        not isinstance(repair_dispatch_id, str)
        or not REPAIR_DISPATCH_ID_PATTERN.fullmatch(repair_dispatch_id)
    ):
        raise GitHubIssueRepairWakeupError("invalid repair_dispatch_id")
    if not isinstance(failed_run_id, str) or not FAILED_RUN_ID_PATTERN.fullmatch(
        failed_run_id
    ):
        raise GitHubIssueRepairWakeupError("invalid failed_run_id")
    if not isinstance(repair_sha, str) or not REPAIR_SHA_PATTERN.fullmatch(repair_sha):
        raise GitHubIssueRepairWakeupError("invalid repair_sha")
    if executor is not None and (
        not isinstance(executor, str) or executor not in SUPPORTED_EXECUTORS
    ):
        raise GitHubIssueRepairWakeupError("unsupported executor")
    return RepairWakeupRequest(
        repair_dispatch_id, failed_run_id, repair_sha, executor
    )


def _bounded_text(value: str) -> str:
    cleaned = value.replace("\x00", "?")
    return (
        cleaned
        if len(cleaned) <= _MAX_RECEIPT_CHARS
        else cleaned[: _MAX_RECEIPT_CHARS - 14] + "\n[truncated]"
    )


def render_rejection(reason: BaseException) -> str:
    detail = " ".join(str(reason).splitlines()) or reason.__class__.__name__
    return _bounded_text(
        "AIOS REPAIR WAKEUP CARRIER RECEIPT\n"
        "status: REJECTED\n"
        "dispatch_accepted: false\n"
        "repair_run_outcome: not_observed\n"
        "verification: not_observed\n"
        "semantic_review: not_observed\n"
        "publication: not_observed\n"
        f"reason: {detail}"
    )


def render_admitted(request: RepairWakeupRequest) -> str:
    return (
        "AIOS REPAIR WAKEUP CARRIER RECEIPT\n"
        "status: ADMITTED\n"
        "dispatch_accepted: pending\n"
        "repair_run_outcome: not_observed\n"
        "verification: not_observed\n"
        "semantic_review: not_observed\n"
        "publication: not_observed\n"
        f"repair_dispatch_id: {request.repair_dispatch_id}\n"
        f"failed_run_id: {request.failed_run_id}\n"
        f"repair_sha: {request.repair_sha}\n"
        f"executor: {request.executor or 'none'}\n"
        f"actor: {request.actor}"
    )


def _write_text(path: str | Path, value: str) -> None:
    try:
        Path(path).write_text(value, encoding="utf-8")
    except OSError as exc:
        raise GitHubIssueRepairWakeupError(
            "cannot write bounded carrier output"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Admit one GitHub Issue REPAIR wakeup")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--policy", default=".ai/brain-repair-wakeup-carriers.yaml")
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    parser.add_argument("--receipt", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.event:
            raise GitHubIssueRepairWakeupError("GITHUB_EVENT_PATH is required")
        if not args.output:
            raise GitHubIssueRepairWakeupError("GITHUB_OUTPUT is required")
        policy = load_policy(args.policy)
        request = admit_event(args.event, policy)
        _write_text(args.output, request.github_outputs())
        _write_text(args.receipt, render_admitted(request))
    except GitHubIssueRepairWakeupError as exc:
        try:
            _write_text(args.receipt, render_rejection(exc))
        except GitHubIssueRepairWakeupError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
