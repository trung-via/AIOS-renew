"""Fail-closed GitHub-Issue carrier for one canonical REPAIR wakeup."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from .repair_dispatch import (
    FAILED_RUN_ID_PATTERN,
    REPAIR_DISPATCH_ID_PATTERN,
    REPAIR_SHA_PATTERN,
    SUPPORTED_EXECUTORS,
)
from .execution_profile import (
    ExecutionProfileError,
    ExecutionProfilePolicy,
    bind_execution_profile,
    load_execution_profile_policy,
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
    {
        "format", "version", "repair_dispatch_id", "failed_run_id", "repair_sha",
        "executor", "model", "reasoning_effort",
    }
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


def _resolve_profile_policy(
    profile_policy: ExecutionProfilePolicy | Path | str | None = None,
) -> ExecutionProfilePolicy:
    if isinstance(profile_policy, ExecutionProfilePolicy):
        return profile_policy
    if profile_policy is not None:
        return load_execution_profile_policy(profile_policy)
    return load_execution_profile_policy()


@dataclass(frozen=True)
class RepairWakeupRequest:
    repair_dispatch_id: str
    failed_run_id: str
    repair_sha: str
    executor: str | None
    actor: str = ""
    model: str | None = None
    reasoning_effort: str | None = None
    model_source: str | None = None
    effort_source: str | None = None

    def github_outputs(self) -> str:
        """Render only bounded selector values for the fixed reusable workflow."""

        return (
            f"repair_dispatch_id={self.repair_dispatch_id}\n"
            f"failed_run_id={self.failed_run_id}\n"
            f"repair_sha={self.repair_sha}\n"
            f"executor={self.executor or ''}\n"
            f"model={self.model or ''}\n"
            f"reasoning_effort={self.reasoning_effort or ''}\n"
            f"model_source={self.model_source or ''}\n"
            f"effort_source={self.effort_source or ''}\n"
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
    return GitHubIssueRepairWakeupPolicy(
        repository,
        tuple(actors),
        title,
        maximum,
    )


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
    path: str | Path,
    policy: GitHubIssueRepairWakeupPolicy,
    *,
    profile_policy: ExecutionProfilePolicy | Path | str | None = None,
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
    request = parse_request(body_bytes, profile_policy=profile_policy)
    if request.executor == "AUTO" and (
        event["sender"].get("type") != "User" or issue["user"].get("type") != "User"
    ):
        raise GitHubIssueRepairWakeupError("AUTO requires an authenticated Human Issue sender/author")
    return RepairWakeupRequest(
        request.repair_dispatch_id,
        request.failed_run_id,
        request.repair_sha,
        request.executor,
        actor,
        request.model,
        request.reasoning_effort,
        request.model_source,
        request.effort_source,
    )


def parse_request(
    raw: bytes | str,
    *,
    profile_policy: ExecutionProfilePolicy | Path | str | None = None,
) -> RepairWakeupRequest:
    source = raw.encode("utf-8", errors="strict") if isinstance(raw, str) else raw
    request = _mapping(
        _load_yaml(source, "REPAIR wakeup request"), "REPAIR wakeup request"
    )
    keys = set(request)
    if keys != _BASE_REQUEST_KEYS:
        raise GitHubIssueRepairWakeupError(
            "REPAIR wakeup request has missing or unknown fields"
        )
    version = request.get("version")
    if (
        request.get("format") != _REQUEST_FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version not in (2, 3)
    ):
        raise GitHubIssueRepairWakeupError(
            "unsupported REPAIR wakeup request format or version"
        )
    repair_dispatch_id = request.get("repair_dispatch_id")
    failed_run_id = request.get("failed_run_id")
    repair_sha = request.get("repair_sha")
    executor = request.get("executor")
    requested_model = request.get("model")
    requested_effort = request.get("reasoning_effort")
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
        not isinstance(executor, str)
        or executor not in SUPPORTED_EXECUTORS | ({"AUTO"} if version == 3 else set())
    ):
        raise GitHubIssueRepairWakeupError("unsupported executor")
    if requested_model is not None and not isinstance(requested_model, str):
        raise GitHubIssueRepairWakeupError("invalid model selection")
    if requested_effort is not None and not isinstance(requested_effort, str):
        raise GitHubIssueRepairWakeupError("invalid reasoning_effort selection")
    if executor in (None, "AUTO") and (requested_model is not None or requested_effort is not None):
        raise GitHubIssueRepairWakeupError(
            "AUTO forbids manual model/effort; choose an explicit coding Executor"
            if executor == "AUTO" else "model/effort selection requires a coding Executor"
        )
    profile = None
    if executor not in (None, "AUTO"):
        try:
            resolved_policy = _resolve_profile_policy(profile_policy)
            profile = bind_execution_profile(
                run_id=repair_dispatch_id,
                executor=executor,
                model=requested_model,
                reasoning_effort=requested_effort,
                policy=resolved_policy,
            )
        except ExecutionProfileError as exc:
            raise GitHubIssueRepairWakeupError(str(exc)) from exc
    return RepairWakeupRequest(
        repair_dispatch_id,
        failed_run_id,
        repair_sha,
        executor,
        model=profile.model if profile is not None else None,
        reasoning_effort=profile.reasoning_effort if profile is not None else None,
        model_source=profile.model_source if profile is not None else None,
        effort_source=profile.effort_source if profile is not None else None,
    )


def authenticated_delivery(
    *, policy_path: Path, profile_policy: ExecutionProfilePolicy | Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> RepairWakeupRequest:
    """Recheck GitHub-owned event proof; input strings never confer Human authority.

    A reusable Issue delivery may have a Bot runner actor, but its original opened
    event must still be owned by the allowlisted Human. A Bot workflow_dispatch
    cannot manufacture that event or opt in to coding AUTO.
    """
    env = os.environ if environ is None else environ
    policy = load_policy(policy_path)
    if (env.get("GITHUB_REPOSITORY") != policy.repository
            or env.get("AIOS_CONTROL_REF", env.get("GITHUB_REF")) != "refs/heads/main"
            or not REPAIR_SHA_PATTERN.fullmatch(env.get("AIOS_CONTROL_SHA", ""))):
        raise GitHubIssueRepairWakeupError("delivery repository/main provenance mismatch")
    actor = env.get("AIOS_DELIVERY_ACTOR", "")
    event_name = env.get("AIOS_DELIVERY_EVENT", "")
    event_path = Path(env.get("GITHUB_EVENT_PATH", ""))
    if event_name == "issues":
        if actor not in (*policy.authorized_actors, "github-actions[bot]"):
            raise GitHubIssueRepairWakeupError("delivery actor is not authorized")
        request = admit_event(event_path, policy, profile_policy=profile_policy)
        event = _json_no_duplicates(_read_bounded_file(
            event_path, maximum=_MAX_EVENT_FILE_BYTES, kind="event"
        ))
        if (event["sender"].get("type") != "User"
                or event["issue"]["user"].get("type") != "User"):
            raise GitHubIssueRepairWakeupError("AUTO requires authenticated Human Issue proof")
    elif event_name == "workflow_dispatch" and actor in policy.authorized_actors:
        event = _json_no_duplicates(_read_bounded_file(
            event_path, maximum=_MAX_EVENT_FILE_BYTES, kind="event"
        ))
        sender = _mapping(event.get("sender"), "dispatch sender")
        inputs = _mapping(event.get("inputs"), "dispatch inputs")
        if (sender.get("login") != actor or sender.get("type") != "User"
                or _mapping(event.get("repository"), "dispatch repository").get("full_name") != policy.repository
                or event.get("ref") not in ("main", "refs/heads/main")):
            raise GitHubIssueRepairWakeupError("dispatch Human proof mismatch")
        if set(inputs) - (_BASE_REQUEST_KEYS - {"format", "version"} | {"model_source", "effort_source"}):
            raise GitHubIssueRepairWakeupError("dispatch contains unsupported selection inputs")
        if any(value is not None and not isinstance(value, str) for value in inputs.values()):
            raise GitHubIssueRepairWakeupError("dispatch selection inputs must be strings")
        # workflow_dispatch includes empty optional strings rather than YAML null.
        body = {"format": _REQUEST_FORMAT, "version": 3}
        for key in _BASE_REQUEST_KEYS - {"format", "version"}:
            body[key] = inputs.get(key) or None
        request = parse_request(yaml.safe_dump(body), profile_policy=profile_policy)
        request = replace(request, actor=actor)
        # Exact source fields, when carried, are validated below as well.
        for key in ("model", "reasoning_effort", "model_source", "effort_source"):
            if (inputs.get(key) or "") != env.get("AIOS_" + key.upper(), ""):
                raise GitHubIssueRepairWakeupError("dispatch profile provenance mismatch")
        if request.executor in (None, "AUTO"):
            if inputs.get("model_source") or inputs.get("effort_source"):
                raise GitHubIssueRepairWakeupError("AUTO/executorless delivery forbids profile provenance inputs")
        else:
            try:
                bind_execution_profile(
                    run_id=request.repair_dispatch_id, executor=request.executor,
                    model=inputs.get("model") or None,
                    reasoning_effort=inputs.get("reasoning_effort") or None,
                    model_source=inputs.get("model_source") or None,
                    effort_source=inputs.get("effort_source") or None,
                    policy=_resolve_profile_policy(profile_policy),
                )
            except ExecutionProfileError as exc:
                raise GitHubIssueRepairWakeupError("dispatch profile provenance is invalid") from exc
    else:
        raise GitHubIssueRepairWakeupError("coding selection requires current Human event proof")
    for key in ("repair_dispatch_id", "failed_run_id", "repair_sha", "executor"):
        if (getattr(request, key) or "") != env.get("AIOS_" + key.upper(), ""):
            raise GitHubIssueRepairWakeupError("Human request/REPAIR selector mismatch")
    for key in ("model", "reasoning_effort", "model_source", "effort_source"):
        supplied = env.get("AIOS_" + key.upper(), "")
        expected = getattr(request, key) or ""
        if event_name == "issues":
            if supplied != expected:
                raise GitHubIssueRepairWakeupError("Human request/profile mismatch")
    return request


def delivery_main() -> int:
    """Self-host pre-AIOS proof check, using the exact materialized control source."""
    try:
        event = _json_no_duplicates(_read_bounded_file(
            Path(os.environ.get("GITHUB_EVENT_PATH", "")), maximum=_MAX_EVENT_FILE_BYTES, kind="event",
        ))
        if (os.environ.get("AIOS_DELIVERY_EVENT") == "issues"
                and event.get("issue", {}).get("title") == "[AIOS BRAIN INGRESS]"):
            authenticate_ingress_executorless_delivery()
            return 0
        authenticated_delivery(
            policy_path=Path(__file__).resolve().parents[2] / ".ai/brain-repair-wakeup-carriers.yaml",
        )
    except GitHubIssueRepairWakeupError as exc:
        # Bounded parser reasons carry no private payload, provider trace, or RUN claim.
        print(render_rejection(exc), file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as stream:
                stream.write(render_rejection(exc) + "\n")
        return 1
    return 0


def _origin_request(event_path, failed_run_id, *, profile_policy=None):
    """Authenticate one original Human Issue and its exact failed-RUN selector."""
    from . import github_issue_ingress as ingress
    event = _json_no_duplicates(_read_bounded_file(Path(event_path), maximum=_MAX_EVENT_FILE_BYTES, kind="event"))
    if event.get("issue", {}).get("title") == "[AIOS BRAIN INGRESS]":
        try:
            issue = ingress.admit_event(event_path, ingress.load_policy(
                Path(__file__).resolve().parents[2] / ".ai/brain-ingress-carriers.yaml",
            ))
            sender = event.get("sender", {})
            if (sender.get("login") != issue.actor or sender.get("type") != "User"
                    or event["issue"]["user"].get("type") != "User"):
                raise GitHubIssueRepairWakeupError("origin requires authenticated Human Issue")
            envelope = ingress.parse_envelope(issue.body_bytes)
            if envelope.operation != "AUTHOR_REPAIR" or envelope.identity.get("failed_run_id") != failed_run_id:
                raise GitHubIssueRepairWakeupError("origin REPAIR subject mismatch")
        except (ingress.GitHubIssueIngressError, ingress.AuthoringIngressError) as exc:
            raise GitHubIssueRepairWakeupError("origin REPAIR proof rejected") from exc
    else:
        request = admit_event(event_path, load_policy(
            Path(__file__).resolve().parents[2] / ".ai/brain-repair-wakeup-carriers.yaml",
        ), profile_policy=profile_policy)
        if request.failed_run_id != failed_run_id:
            raise GitHubIssueRepairWakeupError("origin REPAIR subject mismatch")
    return event


def authenticate_ingress_executorless_delivery():
    """The existing ingress may transport only Runtime-proved executorless work."""
    from .github_issue_ingress import repair_wakeup_route
    env = os.environ
    if (env.get("GITHUB_REPOSITORY") != "trung-via/AIOS-renew"
            or env.get("AIOS_CONTROL_REF") != "refs/heads/main"
            or env.get("AIOS_DELIVERY_ACTOR") not in {"trung-via", "github-actions[bot]"}
            or not REPAIR_SHA_PATTERN.fullmatch(env.get("AIOS_CONTROL_SHA", ""))
            or any(env.get("AIOS_" + key) for key in (
                "EXECUTOR", "MODEL", "REASONING_EFFORT", "MODEL_SOURCE", "EFFORT_SOURCE"))):
        raise GitHubIssueRepairWakeupError("ingress cannot select a coding Executor")
    failed, sha = env.get("AIOS_FAILED_RUN_ID", ""), env.get("AIOS_REPAIR_SHA", "")
    if (not FAILED_RUN_ID_PATTERN.fullmatch(failed) or not REPAIR_SHA_PATTERN.fullmatch(sha)
            or env.get("AIOS_REPAIR_DISPATCH_ID") != f"repair-{failed}-{sha}"):
        raise GitHubIssueRepairWakeupError("ingress delivery selectors mismatch")
    _origin_request(env["GITHUB_EVENT_PATH"], failed)
    if repair_wakeup_route(Path(env["AIOS_REPO_ROOT"]), failed, sha)[0] != "EXECUTORLESS_READY":
        raise GitHubIssueRepairWakeupError("current Runtime executorless authority required")


def publish_workflow_receipt(*, phase, route, route_reason, downstream_result=None, sources=None, environ=None):
    """The existing hosted Issue writer's one bounded, updatable observation.

    The self-hosted runner exports only Operational Receipt v2. It receives no
    Issue-write token. This writer has no dispatch or engineering-state authority.
    """
    import hashlib
    from .brain_attention import (AttentionError, RepairWorkflowSources, REPAIR_WORKFLOW_MARKER,
                                 repair_workflow_link, canonical)
    env = os.environ if environ is None else environ
    if phase not in {"waiting", "completed"} or route not in {"HUMAN_WAKEUP_REQUIRED", "EXECUTORLESS_READY", "HUMAN_AUTHENTICATED"}:
        raise GitHubIssueRepairWakeupError("invalid workflow observation phase")
    if route_reason not in {"READY", "EXECUTOR_REQUIRED", "CURRENTNESS_UNAVAILABLE"}:
        raise GitHubIssueRepairWakeupError("invalid workflow route reason")
    failed, sha, delivery = (env.get("AIOS_FAILED_RUN_ID", ""), env.get("AIOS_REPAIR_SHA", ""),
                             env.get("AIOS_REPAIR_DISPATCH_ID", ""))
    if (not FAILED_RUN_ID_PATTERN.fullmatch(failed) or not REPAIR_SHA_PATTERN.fullmatch(sha)
            or not REPAIR_DISPATCH_ID_PATTERN.fullmatch(delivery)):
        raise GitHubIssueRepairWakeupError("invalid workflow observation selectors")
    event = _origin_request(env["GITHUB_EVENT_PATH"], failed)
    issue = event["issue"]
    if (env.get("GITHUB_REPOSITORY") != "trung-via/AIOS-renew"
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "issues"):
        raise GitHubIssueRepairWakeupError("workflow observation origin mismatch")
    if issue["title"] == "[AIOS REPAIR WAKEUP]":
        request = admit_event(env["GITHUB_EVENT_PATH"], load_policy(
            Path(__file__).resolve().parents[2] / ".ai/brain-repair-wakeup-carriers.yaml"))
        if request.repair_dispatch_id != delivery or request.repair_sha != sha or route != "HUMAN_AUTHENTICATED":
            raise GitHubIssueRepairWakeupError("workflow observation Human request mismatch")
    elif delivery != f"repair-{failed}-{sha}" or route == "HUMAN_AUTHENTICATED":
        raise GitHubIssueRepairWakeupError("workflow observation ingress request mismatch")
    link = repair_workflow_link(dict(
        format="AIOS_REPAIR_WORKFLOW", version=1, repository=env["GITHUB_REPOSITORY"],
        workflow_run_id=int(env["GITHUB_RUN_ID"]), run_attempt=int(env["GITHUB_RUN_ATTEMPT"]),
        control_sha=env["GITHUB_SHA"], origin_issue_id=issue["id"], origin_issue_number=issue["number"],
        origin_body_digest=hashlib.sha256(issue["body"].encode("utf-8")).hexdigest(),
        repair_dispatch_id=delivery, failed_run_id=failed, repair_sha=sha,
        route=route, route_reason=route_reason,
    ))
    api = sources or RepairWorkflowSources()
    # Bind the immutable event to the current GitHub-owned Issue before writing.
    current = api.request(f"/issues/{issue['number']}")
    if (any(current.get(k) != issue.get(k) for k in ("id", "number", "body", "title"))
            or current.get("url") != f"https://api.github.com/repos/{link['repository']}/issues/{issue['number']}"
            or current.get("user", {}).get("login") != "trung-via"
            or current.get("user", {}).get("type") != "User" or "pull_request" in current):
        raise GitHubIssueRepairWakeupError("workflow observation Issue moved")
    try:
        observation = api.observe(link)
    except AttentionError as exc:
        if str(exc) not in {"SOURCE_UNAVAILABLE", "SOURCE_TIMEOUT"}:
            raise
        # This producer still has GitHub-owned context for its own attempt.
        # Restricted Actions reads do not require more credentials or block a
        # separately authenticated Human delivery. Fresh readers revalidate it.
        observation = dict(state="PENDING", jobs=[],
            workflow=dict(status="UNAVAILABLE", url=f"https://github.com/{link['repository']}/actions/runs/{link['workflow_run_id']}/attempts/{link['run_attempt']}"),
            diagnostic=dict(availability="UNAVAILABLE", reason_code="DIAGNOSTIC_UNAVAILABLE"))
    if downstream_result in {"failure", "cancelled", "skipped"} and observation["state"] == "PENDING":
        observation["state"] = "OPERATIONAL_FAILED"
    elif downstream_result == "success" and observation["state"] == "PENDING":
        observation["state"] = "SELF_HOST_COMPLETED"
    state = "HUMAN_WAKEUP_REQUIRED" if route == "HUMAN_WAKEUP_REQUIRED" else observation["state"]
    marker = REPAIR_WORKFLOW_MARKER + canonical(link)
    reason = route_reason if route == "HUMAN_WAKEUP_REQUIRED" else observation["diagnostic"]["reason_code"]
    lines = ["AIOS REPAIR WORKFLOW RECEIPT v1", f"phase: {phase}", f"status: {state}",
             f"repair_dispatch_id: {delivery}", f"failed_run_id: {failed}", f"repair_sha: {sha}",
             f"workflow_run_id: {link['workflow_run_id']}", f"run_attempt: {link['run_attempt']}",
             f"workflow_url: {observation['workflow']['url']}",
             f"workflow_status: {observation['workflow']['status']}",
             f"reason_code: {reason}",
             f"cause_availability: {'AVAILABLE' if route == 'HUMAN_WAKEUP_REQUIRED' and route_reason == 'EXECUTOR_REQUIRED' else observation['diagnostic']['availability']}"]
    if route == "HUMAN_WAKEUP_REQUIRED":
        lines += ["dispatch_attempted: false", "required_action: Human-authenticated manual Executor or exact-subject AUTO wakeup.",
                  "run_created: false", "executor_invoked: false"]
    for job in observation["jobs"]:
        lines.append(f"job_id: {job['id']}; status: {job['status']}; conclusion: {job['conclusion']}")
        for step in job["steps"]:
            if step["conclusion"] in {"failure", "cancelled", "timed_out"}:
                lines.append(f"step: {step['number']} {step['name']}; conclusion: {step['conclusion']}")
    receipt = observation.get("operational_receipt")
    if receipt is not None:
        lines.append("AIOS_OPERATIONAL_RECEIPT_V2=" + canonical(receipt))
    lines += ["repair_run_outcome: not_asserted_by_carrier", "verification: not_asserted_by_carrier",
              "semantic_review: not_asserted_by_carrier", "publication: not_asserted_by_carrier", marker]
    body = "\n".join(lines)
    if len(body) > 3500:
        # Preserve identity and typed outcome; the exact artifact remains queryable.
        body = "\n".join(line for line in lines if not line.startswith("AIOS_OPERATIONAL_RECEIPT_V2="))
    if len(body) > 3500:
        raise GitHubIssueRepairWakeupError("workflow observation exceeds bound")
    matches = [c for c in api.comments(issue["number"])
               if c.get("user", {}).get("login") == "github-actions[bot]"
               and c.get("user", {}).get("type") == "Bot" and marker in c.get("body", "").splitlines()]
    if len(matches) > 1:
        raise GitHubIssueRepairWakeupError("duplicate workflow observation")
    if matches:
        comment = matches[0]
        if phase != "waiting" or "phase: completed" not in comment["body"].splitlines():
            api.write_comment(issue["number"], body, comment_id=comment["id"])
    else:
        comment = api.write_comment(issue["number"], body)
    if phase == "completed" and downstream_result == "success" and state == "SELF_HOST_COMPLETED":
        api.close_issue(issue["number"])
    return {"issue_id": issue["id"], "issue_number": issue["number"], "comment_id": comment["id"]}


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
        f"model: {request.model or 'none'}\n"
        f"reasoning_effort: {request.reasoning_effort or 'none'}\n"
        f"model_source: {request.model_source or 'none'}\n"
        f"effort_source: {request.effort_source or 'none'}\n"
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
    parser.add_argument(
        "--profile-policy",
        "--execution-profile-policy",
        dest="profile_policy",
        default=None,
        help="Path to repository-owned execution-profile policy file",
    )
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--mode", choices=("admit", "workflow-receipt", "finalize-operational"), default="admit")
    parser.add_argument("--phase", choices=("waiting", "completed"), default="waiting")
    parser.add_argument("--route", default="HUMAN_AUTHENTICATED")
    parser.add_argument("--route-reason", default="READY")
    parser.add_argument("--job-result", default="failure")
    parser.add_argument("--downstream-result", choices=("success", "failure", "cancelled", "skipped"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode != "admit":
        try:
            if args.mode == "finalize-operational":
                from .operational_receipt import finalize_repair_workflow_receipt
                finalize_repair_workflow_receipt(args.receipt, args.job_result)
            else:
                outputs = publish_workflow_receipt(phase=args.phase, route=args.route, route_reason=args.route_reason,
                                                   downstream_result=args.downstream_result)
                if args.output:
                    _write_text(args.output, "".join(f"{name}={value}\n" for name, value in outputs.items()))
        except Exception:
            # API failures must not disclose tokens, response bodies or local paths.
            print("AIOS REPAIR workflow observation unavailable", file=sys.stderr)
            return 1
        return 0
    try:
        if not args.event:
            raise GitHubIssueRepairWakeupError("GITHUB_EVENT_PATH is required")
        if not args.output:
            raise GitHubIssueRepairWakeupError("GITHUB_OUTPUT is required")
        policy = load_policy(args.policy)
        request = admit_event(
            args.event, policy, profile_policy=args.profile_policy
        )
        _write_text(args.output, request.github_outputs())
        _write_text(args.receipt, render_admitted(request))
    except GitHubIssueRepairWakeupError as exc:
        from .brain_attention import observe_carrier_rejection
        observe_carrier_rejection(args.event, "REPAIR")
        print(render_rejection(exc), file=sys.stderr)
        try:
            _write_text(args.receipt, render_rejection(exc))
        except GitHubIssueRepairWakeupError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
