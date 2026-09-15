"""Operational handoff from canonical Runtime terminals to Brain attention."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class TerminalAttentionError(RuntimeError):
    """Raised when terminal-attention publication or admission fails closed."""


POLICY_FORMAT = "AIOS_BRAIN_TERMINAL_ATTENTION_CARRIERS_POLICY"
BODY_FORMAT = "AIOS_TERMINAL_ATTENTION"
TITLE_MARKER = "[AIOS TERMINAL ATTENTION]"
SIGNAL_PREFIX = "refs/heads/aios/terminal-attention"
RUN_PATTERN = re.compile(r"^RUN-[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_KINDS = frozenset({"RESULT", "FAILURE"})
_POLICY_KEYS = frozenset({"format", "version", "github_issue"})
_ISSUE_POLICY_KEYS = frozenset(
    {"enabled", "repository", "main_ref", "signal_prefix", "title_marker"}
)
_BODY_KEYS = frozenset(
    {"format", "version", "run_id", "terminal_kind", "artifact_sha"}
)
_REVIEWED_PATHS = (
    ".github/workflows/aios-terminal-attention.yml",
    ".ai/brain-terminal-attention-carriers.yaml",
    "src/aios_renew/terminal_attention.py",
)
_MAX_EVENT_BYTES = 1_048_576
_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?/[A-Za-z0-9_.-]+$"
)


@dataclass(frozen=True)
class AttentionPolicy:
    repository: str
    main_ref: str
    signal_prefix: str
    title_marker: str


@dataclass(frozen=True)
class TerminalSelector:
    run_id: str
    terminal_kind: str
    artifact_sha: str

    @property
    def terminal_ref(self) -> str:
        namespace = "artifacts" if self.terminal_kind == "RESULT" else "failure-artifacts"
        return f"refs/heads/aios/{namespace}/{self.run_id}"

    @property
    def opposite_ref(self) -> str:
        namespace = "failure-artifacts" if self.terminal_kind == "RESULT" else "artifacts"
        return f"refs/heads/aios/{namespace}/{self.run_id}"

    @property
    def signal_ref(self) -> str:
        return (
            f"{SIGNAL_PREFIX}/{self.terminal_kind}/"
            f"{self.run_id}/{self.artifact_sha}"
        )

    def body(self) -> str:
        return (
            f"format: {BODY_FORMAT}\n"
            "version: 1\n"
            f"run_id: {self.run_id}\n"
            f"terminal_kind: {self.terminal_kind}\n"
            f"artifact_sha: {self.artifact_sha}\n"
        )


@dataclass(frozen=True)
class AttentionAdmission:
    selector: TerminalSelector
    signal_ref: str
    signal_sha: str
    delivery: str

    def github_outputs(self) -> str:
        return (
            f"run_id={self.selector.run_id}\n"
            f"terminal_kind={self.selector.terminal_kind}\n"
            f"artifact_sha={self.selector.artifact_sha}\n"
            f"signal_ref={self.signal_ref}\n"
            f"signal_sha={self.signal_sha}\n"
            f"attention_body<<AIOS_ATTENTION_BODY\n{self.selector.body()}"
            "AIOS_ATTENTION_BODY\n"
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
            raise TerminalAttentionError("mapping key must be scalar") from exc
        if duplicate:
            raise TerminalAttentionError(f"mapping contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _mapping(value: Any, kind: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise TerminalAttentionError(f"{kind} must be a string-keyed mapping")
    return value


def _load_yaml(raw: bytes | str, kind: str) -> Mapping[str, Any]:
    source = raw if isinstance(raw, bytes) else raw.encode("utf-8", errors="strict")
    try:
        value = yaml.load(source.decode("utf-8", errors="strict"), Loader=_UniqueKeyLoader)
    except TerminalAttentionError:
        raise
    except (UnicodeError, yaml.YAMLError) as exc:
        raise TerminalAttentionError(f"{kind} is not valid UTF-8 YAML") from exc
    return _mapping(value, kind)


def load_policy(path: str | Path) -> AttentionPolicy:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise TerminalAttentionError("cannot read terminal-attention policy") from exc
    if not raw or len(raw) > 65_536:
        raise TerminalAttentionError("terminal-attention policy size is invalid")
    root = _load_yaml(raw, "terminal-attention policy")
    version = root.get("version")
    if (
        set(root) != _POLICY_KEYS
        or root.get("format") != POLICY_FORMAT
        or isinstance(version, bool)
        or version != 1
    ):
        raise TerminalAttentionError("unsupported terminal-attention policy")
    issue = _mapping(root.get("github_issue"), "github_issue policy")
    if set(issue) != _ISSUE_POLICY_KEYS or issue.get("enabled") is not True:
        raise TerminalAttentionError("github_issue policy is invalid or disabled")
    repository = issue.get("repository")
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(
        repository
    ):
        raise TerminalAttentionError("terminal-attention repository is invalid")
    if issue.get("main_ref") != "refs/heads/main":
        raise TerminalAttentionError("terminal-attention main ref is invalid")
    if issue.get("signal_prefix") != SIGNAL_PREFIX:
        raise TerminalAttentionError("terminal-attention signal prefix is invalid")
    if issue.get("title_marker") != TITLE_MARKER:
        raise TerminalAttentionError("terminal-attention title marker is invalid")
    return AttentionPolicy(
        repository=repository,
        main_ref=str(issue["main_ref"]),
        signal_prefix=str(issue["signal_prefix"]),
        title_marker=str(issue["title_marker"]),
    )


def selector(run_id: str, terminal_kind: str, artifact_sha: str) -> TerminalSelector:
    if not isinstance(run_id, str) or not RUN_PATTERN.fullmatch(run_id):
        raise TerminalAttentionError("invalid terminal-attention run_id")
    if terminal_kind not in _KINDS:
        raise TerminalAttentionError("invalid terminal-attention terminal_kind")
    if not isinstance(artifact_sha, str) or not SHA_PATTERN.fullmatch(artifact_sha):
        raise TerminalAttentionError("invalid terminal-attention artifact_sha")
    return TerminalSelector(run_id, terminal_kind, artifact_sha)


def parse_signal_ref(ref: str, *, prefix: str = SIGNAL_PREFIX) -> TerminalSelector:
    if not isinstance(ref, str) or not ref.startswith(prefix + "/"):
        raise TerminalAttentionError("invalid terminal-attention signal ref")
    parts = ref[len(prefix) + 1 :].split("/")
    if len(parts) != 3:
        raise TerminalAttentionError("invalid terminal-attention signal ref grammar")
    parsed = selector(parts[1], parts[0], parts[2])
    if ref != parsed.signal_ref:
        raise TerminalAttentionError("non-canonical terminal-attention signal ref")
    return parsed


def parse_body(raw: bytes | str) -> TerminalSelector:
    body = _load_yaml(raw, "terminal-attention body")
    version = body.get("version")
    if (
        set(body) != _BODY_KEYS
        or body.get("format") != BODY_FORMAT
        or isinstance(version, bool)
        or version != 1
    ):
        raise TerminalAttentionError("terminal-attention body has unknown or missing fields")
    return selector(body.get("run_id"), body.get("terminal_kind"), body.get("artifact_sha"))


def _json_event(path: str | Path) -> Mapping[str, Any]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise TerminalAttentionError("cannot read GitHub event") from exc
    if not raw or len(raw) > _MAX_EVENT_BYTES:
        raise TerminalAttentionError("GitHub event size is invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise TerminalAttentionError(f"GitHub event contains duplicate key: {key}")
            result[key] = value
        return result

    try:
        return _mapping(
            json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs),
            "GitHub event",
        )
    except TerminalAttentionError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TerminalAttentionError("GitHub event is not valid UTF-8 JSON") from exc


def _git(repo: Path, *args: str, allow_fail: bool = False) -> tuple[int, str, str]:
    try:
        process = subprocess.run(
            ("git", "-C", str(repo), *args), capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise TerminalAttentionError("cannot execute Git for terminal attention") from exc
    if process.returncode and not allow_fail:
        raise TerminalAttentionError(process.stderr.strip() or "terminal-attention Git command failed")
    return process.returncode, process.stdout.strip(), process.stderr.strip()


def _remote_refs(repo: Path, remote: str, *patterns: str) -> dict[str, str]:
    code, output, _ = _git(repo, "ls-remote", "--refs", remote, *patterns, allow_fail=True)
    if code:
        raise TerminalAttentionError("cannot query terminal-attention remote refs")
    refs: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2 or not SHA_PATTERN.fullmatch(parts[0]) or parts[1] in refs:
            raise TerminalAttentionError("malformed or duplicate terminal-attention remote ref")
        refs[parts[1]] = parts[0]
    return refs


def _require_main_ancestor(repo: Path, commit: str, main_sha: str) -> None:
    if not SHA_PATTERN.fullmatch(commit) or not SHA_PATTERN.fullmatch(main_sha):
        raise TerminalAttentionError("invalid published-main commit identity")
    code, _, _ = _git(repo, "merge-base", "--is-ancestor", commit, main_sha, allow_fail=True)
    if code:
        raise TerminalAttentionError("terminal-attention code or signal is not on published main")


def _reviewed_surface_available(repo: Path, main_sha: str) -> bool:
    for path in _REVIEWED_PATHS:
        code, _, _ = _git(repo, "cat-file", "-e", f"{main_sha}:{path}", allow_fail=True)
        if code:
            return False
    return True


def _ensure_remote_main_object(
    repo: Path, *, remote: str, main_ref: str, expected_sha: str
) -> None:
    code, _, _ = _git(repo, "cat-file", "-e", f"{expected_sha}^{{commit}}", allow_fail=True)
    if not code:
        return
    code, _, _ = _git(
        repo, "fetch", "--quiet", "--no-tags", remote, main_ref, allow_fail=True
    )
    if code:
        raise TerminalAttentionError("cannot fetch the observed published-main commit")
    current = _remote_refs(repo, remote, main_ref).get(main_ref)
    if current != expected_sha:
        raise TerminalAttentionError("published main moved during attention admission")
    code, _, _ = _git(repo, "cat-file", "-e", f"{expected_sha}^{{commit}}", allow_fail=True)
    if code:
        raise TerminalAttentionError("observed published-main commit is unavailable")


def publish_terminal_attention(
    repo: Path,
    *,
    remote: str,
    run_id: str,
    terminal_kind: str,
    artifact_sha: str,
) -> str:
    """Publish/reuse one operational ref after exact canonical terminal publication.

    ``UNAVAILABLE`` is returned only for a published main that predates this reviewed
    carrier surface. This preserves transport compatibility during rollout; a
    subsequent terminal transport replay emits the signal after main is upgraded.
    """

    item = selector(run_id, terminal_kind, artifact_sha)
    refs = _remote_refs(
        repo,
        remote,
        "refs/heads/main",
        item.terminal_ref,
        item.opposite_ref,
        f"{SIGNAL_PREFIX}/*",
    )
    main_sha = refs.get("refs/heads/main")
    if main_sha is None:
        raise TerminalAttentionError("published main ref is missing")
    if refs.get(item.terminal_ref) != artifact_sha:
        raise TerminalAttentionError("canonical terminal ref does not match attention selector")
    if item.opposite_ref in refs:
        raise TerminalAttentionError("canonical RUN has competing RESULT and FAILURE terminals")

    _ensure_remote_main_object(
        repo, remote=remote, main_ref="refs/heads/main", expected_sha=main_sha
    )
    if not _reviewed_surface_available(repo, main_sha):
        return "UNAVAILABLE"

    matching_ref: str | None = None
    for ref, target in refs.items():
        if not ref.startswith(SIGNAL_PREFIX + "/"):
            continue
        existing = parse_signal_ref(ref)
        if existing.run_id != run_id:
            continue
        if existing != item:
            raise TerminalAttentionError("RUN already has a conflicting attention identity")
        if target != main_sha:
            _require_main_ancestor(repo, target, main_sha)
        if not _reviewed_surface_available(repo, target):
            raise TerminalAttentionError("existing attention ref is not reviewed carrier code")
        matching_ref = ref
    if matching_ref is not None:
        return "REUSED"

    code, _, stderr = _git(
        repo,
        "push",
        "--no-tags",
        f"--force-with-lease={item.signal_ref}:",
        remote,
        f"{main_sha}:{item.signal_ref}",
        allow_fail=True,
    )
    if code:
        raise TerminalAttentionError(
            "failed to publish terminal-attention ref" + (f": {stderr}" if stderr else "")
        )
    return "PUBLISHED"


def admit_event(
    *,
    event_name: str,
    event: Mapping[str, Any],
    repository: str,
    event_sha: str,
    policy: AttentionPolicy,
) -> tuple[TerminalSelector, str, str | None, str]:
    event_repo = _mapping(event.get("repository"), "event repository").get("full_name")
    if repository != policy.repository or event_repo != policy.repository:
        raise TerminalAttentionError("GitHub repository does not match terminal-attention policy")
    if not SHA_PATTERN.fullmatch(event_sha):
        raise TerminalAttentionError("GitHub event SHA is invalid")
    if event_name == "push":
        ref = event.get("ref")
        after = event.get("after")
        if (
            event.get("created") is not True
            or event.get("deleted") is True
            or event.get("forced") is True
            or event.get("before") != "0" * 40
            or not isinstance(ref, str)
            or after != event_sha
        ):
            raise TerminalAttentionError("push event is not an immutable attention-ref creation/update")
        return parse_signal_ref(ref, prefix=policy.signal_prefix), ref, event_sha, "REF_EVENT"
    if event_name == "workflow_dispatch":
        if event.get("ref") != policy.main_ref.removeprefix("refs/heads/"):
            raise TerminalAttentionError("workflow_dispatch must execute from main")
        inputs = _mapping(event.get("inputs"), "workflow_dispatch inputs")
        if set(inputs) != {"run_id", "terminal_kind", "artifact_sha"}:
            raise TerminalAttentionError("workflow_dispatch inputs are not exact")
        item = selector(inputs.get("run_id"), inputs.get("terminal_kind"), inputs.get("artifact_sha"))
        return item, item.signal_ref, None, "WORKFLOW_DISPATCH_REPLAY"
    raise TerminalAttentionError("unsupported terminal-attention event family")


def validate_remote_admission(
    repo: Path,
    *,
    remote: str,
    item: TerminalSelector,
    signal_ref: str,
    push_signal_sha: str | None,
    event_sha: str,
    policy: AttentionPolicy,
) -> AttentionAdmission:
    refs = _remote_refs(
        repo,
        remote,
        policy.main_ref,
        item.terminal_ref,
        item.opposite_ref,
        signal_ref,
        f"{policy.signal_prefix}/*",
    )
    main_sha = refs.get(policy.main_ref)
    signal_sha = refs.get(signal_ref)
    if main_sha is None or signal_sha is None:
        raise TerminalAttentionError("published main or exact attention ref is missing")
    if refs.get(item.terminal_ref) != item.artifact_sha:
        raise TerminalAttentionError("canonical terminal ref no longer matches attention selector")
    if item.opposite_ref in refs:
        raise TerminalAttentionError("canonical RUN has competing RESULT and FAILURE terminals")
    if push_signal_sha is not None and signal_sha != push_signal_sha:
        raise TerminalAttentionError("push event no longer matches the exact attention ref")
    _ensure_remote_main_object(
        repo, remote=remote, main_ref=policy.main_ref, expected_sha=main_sha
    )
    if not _reviewed_surface_available(repo, signal_sha):
        raise TerminalAttentionError("attention ref does not contain reviewed carrier code")
    _require_main_ancestor(repo, signal_sha, main_sha)
    _require_main_ancestor(repo, event_sha, main_sha)
    for ref in refs:
        if not ref.startswith(policy.signal_prefix + "/"):
            continue
        existing = parse_signal_ref(ref, prefix=policy.signal_prefix)
        if existing.run_id == item.run_id and existing != item:
            raise TerminalAttentionError("RUN has a conflicting attention identity")
    delivery = "REF_EVENT" if push_signal_sha is not None else "WORKFLOW_DISPATCH_REPLAY"
    return AttentionAdmission(item, signal_ref, signal_sha, delivery)


def _write_output(path: str | Path, content: str) -> None:
    try:
        Path(path).write_text(content, encoding="utf-8")
    except OSError as exc:
        raise TerminalAttentionError("cannot write terminal-attention output") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Admit one exact AIOS terminal attention")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME"))
    parser.add_argument("--event-sha", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--policy", default=".ai/brain-terminal-attention-carriers.yaml")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not all((args.event, args.event_name, args.event_sha, args.repository, args.output)):
            raise TerminalAttentionError("GitHub terminal-attention framing is incomplete")
        policy = load_policy(args.policy)
        item, signal_ref, push_sha, _ = admit_event(
            event_name=args.event_name,
            event=_json_event(args.event),
            repository=args.repository,
            event_sha=args.event_sha,
            policy=policy,
        )
        admission = validate_remote_admission(
            Path(args.repo), remote=args.remote, item=item, signal_ref=signal_ref,
            push_signal_sha=push_sha, event_sha=args.event_sha, policy=policy,
        )
        _write_output(args.output, admission.github_outputs())
    except TerminalAttentionError as exc:
        print(f"terminal attention rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
