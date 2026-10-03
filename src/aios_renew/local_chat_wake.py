"""Durable machine-local, attach-only selector doorbells; no lifecycle authority."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import tempfile
import time
from typing import Iterator
from urllib.parse import urlsplit

REPOSITORY = "trung-via/AIOS-renew"
class _EventGrammar:
    """Strict shared registry grammar, including unchanged H4A4 terminal IDs."""

    @staticmethod
    def fullmatch(value):
        from .brain_attention import AttentionError, parse_event_id
        try:
            return parse_event_id(value)
        except (AttentionError, TypeError, ValueError):
            return None


EVENT_PATTERN = _EventGrammar()
CHAT_PATH = re.compile(
    r"(?:/g/g-[A-Za-z0-9-]*)?/c/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/?"
)
MAX_EVENTS = 256
MAX_TOMBSTONES = 4096
MAX_BYTES = 1048576
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
STATUSES = frozenset({"PENDING", "DEFERRED", "AMBIGUOUS", "SUBMITTED", "RESOLVED_NOOP"})
# CSS selector unions return each element once, even when both branches match.
COMPOSER = ('#prompt-textarea[contenteditable="true"], '
            'main form [contenteditable="true"][role="textbox"][aria-multiline="true"]')
ACCOUNT = ('[data-testid="profile-button"], [data-testid="accounts-profile-button"], '
           'button[aria-label*="profile" i]')
SEND = '[data-testid="send-button"], button[type="submit"][aria-label="Send"]'
STOP = '[data-testid="stop-button"]'
NONREGULAR = '[data-workspace-type="team"], [data-workspace-type="enterprise"], [data-workspace-type="business"], [data-testid="work-composer"]'
LOGIN = '[data-testid="login-button"], a[href^="/auth/login"]'
USER_TURN = '[data-message-author-role="user"]'
USER_BUBBLE = '[data-user-message-bubble]'
TURN_CONTAINER = '[data-turn-key]'
REASONS = frozenset({
    "INVALID_INPUT", "INVALID_CHAT_BINDING", "INVALID_LOCAL_PATH",
    "CONFIG_OR_STATE_IN_REPOSITORY", "LOCAL_METADATA_INVALID", "BINDING_MISSING",
    "BINDING_MALFORMED", "INVALID_LOCAL_ENDPOINT", "STATE_LOCKED_OR_UNAVAILABLE",
    "STATE_AMBIGUOUS", "STATE_WRITE_UNCERTAIN", "TARGET_PAGE_NOT_UNIQUE",
    "TARGET_PAGE_CHANGED", "SURFACE_UNPROVEN", "GENERATION_ACTIVE", "DRAFT_PRESENT",
    "OUTBOUND_ALREADY_PRESENT", "INSERT_BLOCKED", "SEND_BLOCKED",
    "SUBMISSION_UNPROVEN", "ATTEMPT_REQUIRES_HUMAN", "STATE_CAPACITY_REQUIRES_HUMAN",
    "LOCAL_FAILURE",
    "CANONICAL_UNKNOWN", "BINDING_GENERATION_CHANGED", "LANE_IN_FLIGHT",
    "REGISTRY_CONFLICT", "ENABLE_GATE_CLOSED",
})
SURFACE_CAUSES = frozenset({
    "TARGET_URL_MISMATCH", "MAIN_NOT_UNIQUE", "ACCOUNT_NOT_UNIQUE",
    "LOGIN_PRESENT", "NON_REGULAR_SURFACE", "COMPOSER_NOT_UNIQUE",
    "COMPOSER_DISABLED", "MULTIPLE_OR_AMBIGUOUS",
})


class WakeBlocked(Exception):
    """Only fixed reason codes may leave the local browser boundary."""

    def __init__(self, reason: str, *, surface_cause: str | None = None):
        reason = reason if reason in REASONS else "LOCAL_FAILURE"
        super().__init__(reason)
        # Operational annotation only; the reason remains the durable retry code.
        self.surface_cause = (
            surface_cause if isinstance(surface_cause, str) and surface_cause in SURFACE_CAUSES
            else "MULTIPLE_OR_AMBIGUOUS"
        ) if reason == "SURFACE_UNPROVEN" else None


def doorbell(event_id: str, repository: str) -> str:
    if (not isinstance(repository, str) or not REPOSITORY_PATTERN.fullmatch(repository)
            or not isinstance(event_id, str)
            or not EVENT_PATTERN.fullmatch(event_id)):
        raise WakeBlocked("INVALID_INPUT")
    return (f"[AIOS LOCAL CHAT WAKE]\nevent_id: {event_id}\n"
            f"repository: {repository}\nfresh_brain_sync_required: true")


def normalize_chat(url: str) -> str:
    try:
        if not isinstance(url, str):
            raise ValueError
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != "chatgpt.com"
                or parts.query or parts.fragment or not CHAT_PATH.fullmatch(parts.path)
                or url != "https://chatgpt.com" + parts.path):
            raise ValueError
        return "https://chatgpt.com" + parts.path.rstrip("/")
    except (ValueError, TypeError):
        raise WakeBlocked("INVALID_CHAT_BINDING") from None


def external_path(value: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise WakeBlocked("INVALID_LOCAL_PATH")
    path = Path(value).resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise WakeBlocked("CONFIG_OR_STATE_IN_REPOSITORY")
    return path


def read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError
        result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (OSError, ValueError, UnicodeError):
        raise WakeBlocked("LOCAL_METADATA_INVALID") from None


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _durable_replace(source, target):
    if os.name == "nt":
        import ctypes
        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move.restype = ctypes.c_int
        # REPLACE_EXISTING | WRITE_THROUGH, after the file contents were fsynced.
        if not move(str(source), str(target), 0x1 | 0x8):
            raise OSError("state replacement unavailable")
    else:
        os.replace(source, target)
        _sync_directory(target.parent)


@dataclass(frozen=True)
class Binding:
    chat_url: str
    cdp_endpoint: str
    state_path: Path
    generation: int = 0
    repository: str = REPOSITORY


def load_binding(repository: str = REPOSITORY) -> Binding:
    location = os.environ.get("AIOS_LOCAL_CHAT_WAKE_CONFIG")
    if not location:
        raise WakeBlocked("BINDING_MISSING")
    config_path = external_path(location)
    data = read_json(config_path)
    if set(data) == {"chat_url", "cdp_endpoint", "state_path"}:
        if repository != REPOSITORY:
            raise WakeBlocked("BINDING_MISSING")
        return _binding(data, config_path, repository, 0)
    if (set(data) != {"version", "lanes"} or type(data["version"]) is not int
            or data["version"] != 2 or not isinstance(data["lanes"], dict)
            or not 1 <= len(data["lanes"]) <= MAX_EVENTS):
        raise WakeBlocked("BINDING_MALFORMED")
    lanes, chats, paths = {}, set(), {config_path}
    for key, item in data["lanes"].items():
        if (not REPOSITORY_PATTERN.fullmatch(key) or not isinstance(item, dict)
                or set(item) != {"chat_url", "cdp_endpoint", "state_path", "generation"}
                or type(item["generation"]) is not int or item["generation"] < 1):
            raise WakeBlocked("BINDING_MALFORMED")
        binding = _binding(item, config_path, key, item["generation"])
        # The conversation UUID is the identity even if a project prefix changes.
        chat = binding.chat_url.rsplit("/", 1)[-1]
        owned = {binding.state_path, binding.state_path.with_name(binding.state_path.name + ".lock"),
                 binding.state_path.with_name(binding.state_path.name + ".pending"),
                 binding.state_path.with_name(binding.state_path.name + ".queue")}
        if chat in chats or any(a == b or a in b.parents or b in a.parents for a in owned for b in paths):
            raise WakeBlocked("REGISTRY_CONFLICT")
        chats.add(chat)
        paths.update(owned)
        lanes[key] = binding
    if repository not in lanes:
        raise WakeBlocked("BINDING_MISSING")
    return lanes[repository]


def _binding(data, config_path, repository, generation):
    chat_url = normalize_chat(data["chat_url"])
    try:
        endpoint = data["cdp_endpoint"]
        if not isinstance(endpoint, str) or any(c.isspace() for c in endpoint):
            raise ValueError
        parts = urlsplit(endpoint)
        host = parts.hostname
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        if (parts.scheme != "http" or not loopback or not parts.port
                or parts.username or parts.password or parts.path not in ("", "/")
                or parts.query or parts.fragment):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise WakeBlocked("INVALID_LOCAL_ENDPOINT") from None
    state_path = external_path(data["state_path"])
    if state_path == config_path:
        raise WakeBlocked("INVALID_LOCAL_PATH")
    return Binding(chat_url, endpoint, state_path, generation, repository)


class State:
    def __init__(self, path: Path, repository: str = REPOSITORY):
        self.path = path
        self.repository = repository

    @contextmanager
    def locked(self) -> Iterator[dict]:
        # O_EXCL blocks concurrent processes and stale/uncertain locks.
        lock = self.path.with_name(self.path.name + ".lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            raise WakeBlocked("STATE_LOCKED_OR_UNAVAILABLE") from None
        try:
            os.close(fd)
            data = read_json(self.path) if self.path.exists() else {
                "version": 2, "repository": self.repository, "bindings": {},
                "events": {}, "flight": None, "tombstones": []}
            data = self.validate(data)
            if self.path.with_name(self.path.name + ".pending").exists():
                raise WakeBlocked("STATE_WRITE_UNCERTAIN")
            yield data
        finally:
            lock.unlink()

    def validate(self, data):
        if (type(data.get("version")) is not int or not isinstance(data.get("events"), dict)
                or len(data["events"]) > MAX_EVENTS):
            raise WakeBlocked("STATE_AMBIGUOUS")
        if data["version"] == 1:
            if (set(data) != {"version", "events"}
                    or self.repository != REPOSITORY
                    or any(not EVENT_PATTERN.fullmatch(k) or v not in ("ATTEMPTING", "SUBMITTED")
                           for k, v in data["events"].items())):
                raise WakeBlocked("STATE_AMBIGUOUS")
            # Historical attempts have no proven binding generation. Never invent one.
            data = {"version": 2, "repository": self.repository, "bindings": {},
                    "events": {k: record("AMBIGUOUS" if v == "ATTEMPTING" else v)
                               for k, v in data["events"].items()}, "flight": None, "tombstones": []}
        if (data["version"] != 2 or set(data) != {"version", "repository", "bindings", "events", "flight", "tombstones"}
                or data["repository"] != self.repository or not isinstance(data["bindings"], dict)
                or len(data["bindings"]) > MAX_EVENTS or not isinstance(data["tombstones"], list)
                or len(data["tombstones"]) > MAX_TOMBSTONES
                or any(not isinstance(k, str) or not re.fullmatch(r"[0-9a-f]{64}", k) for k in data["tombstones"])
                or len(set(data["tombstones"])) != len(data["tombstones"])):
            raise WakeBlocked("STATE_AMBIGUOUS")
        for generation, snapshot in data["bindings"].items():
            if (not generation.isdigit() or str(int(generation)) != generation
                    or not isinstance(snapshot, dict)
                    or set(snapshot) != {"chat_url", "cdp_endpoint", "state_path"}):
                raise WakeBlocked("STATE_AMBIGUOUS")
            bound = _binding(snapshot, self.path.with_name(self.path.name + ".config"),
                             self.repository, int(generation))
            if bound.state_path != self.path:
                raise WakeBlocked("STATE_AMBIGUOUS")
        for key, item in data["events"].items():
            if (not EVENT_PATTERN.fullmatch(key) or not isinstance(item, dict)
                    or set(item) != {"status", "generation", "reason"}
                    or item["status"] not in STATUSES or item["reason"] not in REASONS | {"NONE"}
                    or (item["generation"] is not None and
                        (type(item["generation"]) is not int or str(item["generation"]) not in data["bindings"]))
                    or (item["status"] in {"PENDING", "DEFERRED", "RESOLVED_NOOP"}
                        and item["generation"] is not None)):
                raise WakeBlocked("STATE_AMBIGUOUS")
        flight = data["flight"]
        if flight is not None and (not isinstance(flight, dict) or set(flight) != {"event_id", "seen_busy"}
                or type(flight["seen_busy"]) is not bool or flight["event_id"] not in data["events"]
                or data["events"][flight["event_id"]]["status"] not in {"AMBIGUOUS", "SUBMITTED"}
                or data["events"][flight["event_id"]]["generation"] is None):
            raise WakeBlocked("STATE_AMBIGUOUS")
        attempts = [k for k, v in data["events"].items() if v["status"] == "AMBIGUOUS" and v["generation"] is not None]
        if len(attempts) > 1 or (attempts and (flight is None or flight["event_id"] != attempts[0])):
            raise WakeBlocked("STATE_AMBIGUOUS")
        return data

    def write(self, data: dict) -> None:
        self.validate(data)
        temp = self.path.with_name(self.path.name + ".pending")
        try:
            # A left-over pending write requires Human intervention.
            with temp.open("x", encoding="utf-8") as stream:
                encoded = json.dumps(data)  # Preserve FIFO admission order.
                if len(encoded.encode("utf-8")) > MAX_BYTES:
                    raise WakeBlocked("STATE_CAPACITY_REQUIRES_HUMAN")
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            _durable_replace(temp, self.path)
        except OSError:
            raise WakeBlocked("STATE_WRITE_UNCERTAIN") from None


def record(status="PENDING", generation=None, reason="NONE"):
    return dict(status=status, generation=generation, reason=reason)


def event_digest(event_id):
    return hashlib.sha256(event_id.encode("ascii")).hexdigest()


def _inbox(state):
    return external_path(str(state.path.with_name(state.path.name + ".queue")))


def _admit_inbox(state, event_id):
    """Per-event exclusive files preserve intake even while the lane lock is busy.

    A partial file is an uncertain admission and requires Human reconciliation;
    it is never overwritten. The inbox is part of the lane's unique state ownership.
    """
    directory = _inbox(state)
    directory.mkdir(mode=0o700, exist_ok=True)
    target = directory / (event_digest(event_id) + ".json")
    expected = dict(repository=state.repository, event_id=event_id)
    try:
        with target.open("x", encoding="utf-8") as stream:
            json.dump(expected, stream)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(directory)
    except FileExistsError:
        if read_json(target) != expected:
            raise WakeBlocked("STATE_AMBIGUOUS") from None


def _consume_inbox(state, data):
    directory = _inbox(state)
    if not directory.exists():
        return
    # File mtime orders independent admissions; the digest breaks simultaneous ties.
    paths = sorted(directory.iterdir(), key=lambda p: (p.stat().st_mtime_ns, p.name))
    for path in paths:
        item = read_json(path)
        if (set(item) != {"repository", "event_id"} or item["repository"] != state.repository
                or not isinstance(item["event_id"], str) or not EVENT_PATTERN.fullmatch(item["event_id"])
                or path.name != event_digest(item["event_id"]) + ".json"):
            raise WakeBlocked("STATE_AMBIGUOUS")
        event_id = item["event_id"]
        if event_id not in data["events"] and event_digest(event_id) not in data["tombstones"]:
            if len(data["events"]) >= MAX_EVENTS:
                continue  # Never delete excess attention; it remains in the inbox.
            data["events"][event_id] = record()
        state.write(data)
        path.unlink()  # Only a durable queue record/dedupe digest authorizes removal.


class CanonicalFreshness:
    """Observe exact terminal and successor identities in a disposable Git store.

    Never call a lifecycle reducer or consume a semantic action. All network
    operations are reads from the registry-selected repository, with no ref
    writes in the engineering checkout and no reliance on its tracking cache.
    """

    def __init__(self, repository):
        self.remote = "https://github.com/" + repository + ".git"

    def git(self, path, *args):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise WakeBlocked("CANONICAL_UNKNOWN")
        process = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                                 timeout=min(15, remaining), check=False,
                                 env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if process.returncode or len(process.stdout) > MAX_BYTES or time.monotonic() >= self.deadline:
            raise WakeBlocked("CANONICAL_UNKNOWN")
        return process.stdout.decode("utf-8", errors="strict").strip()

    def observe(self, event_id):
        # Lane passes and pre-send barriers use this reader sequentially. Each
        # exact observation starts afresh; prior subjects cannot spend its budget.
        self.deadline = time.monotonic() + 30
        from .brain_attention import RECOVERY, parse_event_id
        try:
            item = parse_event_id(event_id)
            if item.family == RECOVERY:
                event_id = item.selectors["original_event_id"]
                item = parse_event_id(event_id)
            if not event_id.startswith("terminal:"):
                from .brain_attention import ArtifactSources, GitSources, freshness
                with tempfile.TemporaryDirectory(prefix="aios-attention-observe-") as location:
                    path = Path(location)
                    self.git(path, "init", "--bare", "--quiet")
                    sources = GitSources(path, self.git, self.remote)
                    repository = self.remote.removeprefix("https://github.com/").removesuffix(".git")
                    value = freshness(item, sources, ArtifactSources(repository, self.deadline))
                    return value if time.monotonic() < self.deadline else "UNKNOWN"
        except Exception:
            return "UNKNOWN"
        from .terminal_attention import _load_yaml

        _, kind, run_id, artifact_sha = event_id.split(":")
        root = "refs/heads/aios/"
        terminal = root + ("artifacts/" if kind == "RESULT" else "failure-artifacts/") + run_id
        opposite = root + ("failure-artifacts/" if kind == "RESULT" else "artifacts/") + run_id
        candidate = root + ("review/" if kind == "RESULT" else "failure/") + run_id
        decision = root + "review-decision/" + run_id
        repair = root + "repair/" + run_id
        patterns = (terminal, opposite, candidate, decision, repair, root + "repair-supersession/" + run_id + "/*")
        main = "refs/heads/main"
        if kind == "FAILURE":
            patterns += (main,)
        try:
            with tempfile.TemporaryDirectory(prefix="aios-wake-observe-") as location:
                path = Path(location)
                self.git(path, "init", "--bare", "--quiet")

                def refs():
                    result = {}
                    for line in self.git(path, "ls-remote", "--refs", self.remote, *patterns).splitlines():
                        sha, ref = line.split()
                        if not re.fullmatch(r"[0-9a-f]{40}", sha) or ref in result:
                            raise ValueError
                        result[ref] = sha
                    return result

                observed = refs()
                if observed.get(terminal) != artifact_sha or opposite in observed or candidate not in observed:
                    return "UNKNOWN"
                for sha in {sha for ref, sha in observed.items() if ref != main}:
                    self.git(path, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
                             "--refmap=", self.remote, sha)

                def blob(sha, name):
                    return self.git(path, "show", sha + ":" + name)

                def document(sha, name):
                    # Reuse duplicate-key rejection without exporting any artifact content.
                    return _load_yaml(blob(sha, name), "wake canonical identity")

                run = document(artifact_sha, ".ai/transport/run.json")
                if run.get("kind") == "REMEDIATION":
                    run = run.get("execution", {}).get("run", {})
                elif "kind" in run:
                    return "UNKNOWN"
                task = run.get("task")
                if (run.get("run_id") != run_id or not isinstance(task, dict)
                        or set(task) != {"id", "revision"} or not isinstance(task["id"], str)
                        or not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task["id"])
                        or type(task["revision"]) is not int or task["revision"] < 1):
                    return "UNKNOWN"
                name = "result.json" if kind == "RESULT" else "failure.json"
                package = document(artifact_sha, ".ai/transport/" + name)
                head = package.get("result", {}).get("head_sha") if kind == "RESULT" else package.get("failed_head_sha")
                if (head != observed[candidate] or run.get("head_sha") not in (None, head)
                        or (kind == "FAILURE" and (package.get("run_id") != run_id or package.get("task") != task))):
                    return "UNKNOWN"
                resolved = False
                if decision in observed:
                    if kind != "RESULT":
                        return "UNKNOWN"
                    paths = self.git(path, "ls-tree", "-r", "--name-only", observed[decision], "--", ".ai/reviews").splitlines()
                    if len(paths) != 1 or not paths[0].endswith((".yaml", ".yml")):
                        return "UNKNOWN"
                    review = document(observed[decision], paths[0])
                    if (not {"review_id", "reviewed_sha", "mode", "verdict", "acceptance", "findings"}.issubset(review)
                            or review.get("review_id") != "REVIEW-" + run_id[4:]
                            or Path(paths[0]).stem != review.get("review_id")
                            or review.get("reviewed_sha") != head):
                        return "UNKNOWN"
                    resolved = True  # Any exact canonical decision is successor evidence; no verdict selection.
                if repair in observed:
                    if kind != "FAILURE":
                        return "UNKNOWN"
                    authorization = document(observed[repair], ".ai/transport/repair.json")
                    if (authorization.get("failed_run_id") != run_id
                            or authorization.get("failed_head_sha") != head or authorization.get("task") != task):
                        return "UNKNOWN"
                    resolved = True
                # A supersession without a proven root, or a malformed chain, is never resolution.
                previous = observed.get(repair)
                successors = [r for r in observed if r.startswith(root + "repair-supersession/" + run_id + "/")]
                for revision, ref in enumerate(sorted(successors, key=lambda r: int(r.rsplit("/", 1)[-1])), 2):
                    if previous is None or ref.rsplit("/", 1)[-1] != str(revision):
                        return "UNKNOWN"
                    sha = observed[ref]
                    metadata = document(sha, ".ai/transport/repair-supersession.json")
                    successor = document(sha, ".ai/transport/repair.json")
                    if (set(metadata) != {"format", "version", "failed_run_id", "authorization_revision", "predecessor_repair_sha", "failure_artifacts_sha"}
                            or metadata.get("format") != "AIOS_REPAIR_SUPERSESSION"
                            or type(metadata.get("version")) is not int or metadata["version"] != 1
                            or metadata.get("failed_run_id") != run_id or metadata.get("authorization_revision") != revision
                            or metadata.get("failure_artifacts_sha") != artifact_sha
                            or metadata.get("predecessor_repair_sha") != previous
                            or self.git(path, "rev-parse", sha + "^") != previous
                            or any(successor.get(k) != authorization.get(k) for k in ("failed_run_id", "failed_head_sha", "task"))):
                        return "UNKNOWN"
                    previous = sha
                if kind == "FAILURE" and not resolved:
                    # Exact failure/RUN/head/task and any REPAIR lineage have
                    # already been reconstructed. Only the same canonical TASK
                    # contract can supersede this failed revision; no lifecycle
                    # search or roadmap action supplies that semantic successor.
                    from .task import validate_task
                    if main not in observed:
                        return "UNKNOWN"
                    self.git(path, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
                             "--refmap=", self.remote, observed[main])
                    current_task = validate_task(document(observed[main], ".ai/tasks/" + task["id"] + ".yaml"))
                    if current_task.task_id != task["id"] or current_task.revision < task["revision"]:
                        return "UNKNOWN"
                    resolved = current_task.revision > task["revision"]
                if refs() != observed:
                    return "UNKNOWN"
                if time.monotonic() >= self.deadline:
                    return "UNKNOWN"
                return "RESOLVED" if resolved else "UNRESOLVED"
        except Exception:
            return "UNKNOWN"


# Shared by application acceptance, click, and post-submission proof. Never
# query Send on the document: only the proven composer's unique enclosing form.
_SEND_GUARDS = """
  const visible = selector => [...document.querySelectorAll(selector)].filter(
    e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  const sendControls = box => {
    const boxes = visible(composer);
    if (boxes.length !== 1 || boxes[0] !== box) return null;
    const forms = [];
    for (let parent = box.parentElement; parent; parent = parent.parentElement)
      if (parent.tagName === 'FORM') forms.push(parent);
    if (forms.length !== 1 || !forms[0].contains(box)) return null;
    return [...forms[0].querySelectorAll(send)].filter(e => forms[0].contains(e) &&
      e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  };
  const enabled = button => !button.disabled && button.getAttribute('aria-disabled') !== 'true';
"""

# Shared by staging, application acceptance, and the final click boundary.
# No text is exported, trimmed, or generically whitespace-normalized.
_COMPOSER_GUARDS = _SEND_GUARDS + """
  const equivalent = box => {
    if (box.innerText === text && box.textContent === text) return true;
    // Only one flat P/DIV (or root text node) per exact logical line is
    // supported. Each optional/doubled separator must map to a block boundary.
    const lines = text.split('\\n'), nodes = [...box.childNodes];
    const block = node => node.nodeType === 1 && ['P', 'DIV'].includes(node.tagName);
    if (nodes.length !== lines.length || !nodes.some(block)) return false;
    if (!nodes.every((node, i) => node.textContent === lines[i] &&
        (node.nodeType === 3 || (block(node) &&
          [...node.childNodes].every(child => child.nodeType === 3) &&
          node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden'))))
      return false;
    let offset = 0;
    for (let i = 0; i < lines.length; i++) {
      if (box.innerText.slice(offset, offset + lines[i].length) !== lines[i]) return false;
      offset += lines[i].length;
      if (i + 1 < lines.length) {
        if (!block(nodes[i]) && !block(nodes[i + 1])) return false;
        // Zero, one, or two newlines at this proven boundary only.
        for (let n = 0; n < 2 && box.innerText[offset] === '\\n'; n++) offset++;
      }
    }
    return offset === box.innerText.length;
  };
  const surface = box => location.href.replace(/\\/$/, '') === url &&
    visible(composer).length === 1 && visible(composer)[0] === box &&
    box.getAttribute('aria-disabled') !== 'true' &&
    visible(account).length === 1 && !visible(stop).length &&
    !visible(nonregular).length && !visible(login).length && visible('main').length === 1;
  const ready = () => {
    const boxes = visible(composer);
    if (boxes.length !== 1 || !surface(boxes[0]) || !equivalent(boxes[0])) return null;
    const buttons = sendControls(boxes[0]);
    return buttons && buttons.length === 1 && enabled(buttons[0]) ? buttons[0] : null;
  };
"""

# Inspection and native editing share one event-loop turn. Never fill, replace,
# clear, or retry a draft. Focus handlers must leave the same empty surface.
INSERT = """({url, text, composer, account, stop, nonregular, login, send}) => {
""" + _COMPOSER_GUARDS + """
  const boxes = visible(composer);
  if (boxes.length !== 1 || !surface(boxes[0])) return false;
  const box = boxes[0];
  const empty = () => box.textContent === '' && box.getAttribute('aria-disabled') !== 'true';
  if (!empty()) return false;
  box.focus();
  if (!empty() || !surface(box) || document.activeElement !== box) return false;
  // Bind the native edit to the empty composer, never an unrelated selection.
  const selection = window.getSelection(), range = document.createRange();
  if (!selection) return false;
  range.selectNodeContents(box);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  if (!document.execCommand('insertText', false, text)) return false;
  if (!surface(box) || !equivalent(box)) return false;
  // Notify the application-managed editor as well as performing the native edit.
  // A DOM-only mutation is staged text, not proof of application acceptance.
  box.dispatchEvent(new InputEvent('input', {
    bubbles: true, composed: true, inputType: 'insertText', data: text
  }));
  return surface(box) && equivalent(box);
}"""

ACCEPT_INSERT = """({url, text, composer, account, stop, nonregular, login, send}) => {
""" + _COMPOSER_GUARDS + "return Boolean(ready()); }"

CLICK = """({url, text, composer, account, stop, nonregular, login, send}) => {
""" + _COMPOSER_GUARDS + """
  const button = ready();
  if (!button) return false;
  button.click();
  return true;
}"""

PROVE_SEND = """({composer, send}) => {
""" + _SEND_GUARDS + """
  const boxes = visible(composer);
  if (boxes.length !== 1) return false;
  const buttons = sendControls(boxes[0]);
  return buttons !== null && (buttons.length === 0 ||
    (buttons.length === 1 && !enabled(buttons[0])));
}"""


# One contract for pre-send dedupe and post-send proof. Only user-specific
# containers are read; return a fixed status, never browser text or turn keys.
_USER_TURN_GUARDS = """
  const resolveUserTurn = (completion = false) => {
  const candidates = [...document.querySelectorAll(userTurn + ', ' + userBubble)];
  if (completion && candidates.length > 256) return 'AMBIGUOUS';
  const exact = [];
  for (const candidate of candidates) {
    // A user marker inside an explicitly non-user turn is not user proof.
    for (let node = candidate, depth = 0; node; node = node.parentElement, depth++) {
      if (completion && depth >= 32) return 'AMBIGUOUS';
      const role = node.getAttribute('data-message-author-role');
      if (role !== null && role !== 'user') return 'AMBIGUOUS';
      if (completion && node.hasAttribute('data-turn') && node.getAttribute('data-turn') !== 'user')
        return 'AMBIGUOUS';
    }
    if (candidate.querySelector(
        '[data-message-author-role]:not([data-message-author-role="user"])')) return 'AMBIGUOUS';
    if (completion && candidate.querySelector('[data-turn]:not([data-turn="user"])')) return 'AMBIGUOUS';
    if (candidate.textContent.replace(/\\r\\n/g, '\\n') === text) exact.push(candidate);
  }
  if (!exact.length) return 'ABSENT';
  // CSS union identity permits one element carrying both markers, but never
  // selects between distinct containers (including nested exact containers).
  if (exact.length !== 1) return 'AMBIGUOUS';
  const candidate = exact[0];
  if (candidates.some(e => e !== candidate &&
      (e.contains(candidate) || candidate.contains(e)))) return 'AMBIGUOUS';
  if (!candidate.getClientRects().length ||
      getComputedStyle(candidate).visibility !== 'visible') return 'AMBIGUOUS';
  if (candidate.matches(userBubble)) {
    const turns = [];
    for (let parent = candidate.parentElement; parent; parent = parent.parentElement)
      if (parent.matches(turnContainer)) turns.push(parent);
    if (turns.length !== 1) return 'AMBIGUOUS';
    // A turn with more than one user bubble cannot identify one outbound turn.
    if (candidates.filter(e => e.matches(userBubble) && turns[0].contains(e)).length !== 1)
      return 'AMBIGUOUS';
  }
  return candidate;
  };
"""

RESOLVE_USER_TURN = """({text, userTurn, userBubble, turnContainer}) => {
""" + _USER_TURN_GUARDS + """
  const resolved = resolveUserTurn();
  return typeof resolved === 'string' ? resolved : 'EXACT';
}"""

# The exact outbound resolver supplies an in-page anchor, never a turn ID or
# content export. Only the final adjacent pair of flat, consecutively numbered
# turn containers is supported. Missing/virtualized or unfamiliar shapes hold.
# Assistant nodes are inspected only for role, containment and visibility.
PROVE_WAKE_COMPLETION = """({text, userTurn, userBubble, turnContainer}) => {
""" + _USER_TURN_GUARDS + """
  const candidate = resolveUserTurn(true);
  if (typeof candidate === 'string') return false;
  const mains = document.querySelectorAll('main');
  if (mains.length !== 1) return false;
  const main = mains[0];
  const visibleWithinMain = element => {
    // Bound ancestor inspection, including hidden/aria-hidden wrappers.
    for (let node = element, depth = 0; node && depth < 32; node = node.parentElement, depth++) {
      const style = getComputedStyle(node);
      if (!node.getClientRects().length || style.visibility !== 'visible' ||
          style.display === 'none' || node.hasAttribute('hidden') ||
          node.getAttribute('aria-hidden') === 'true') return false;
      if (node === main) return true;
    }
    return false;
  };
  const ancestors = [];
  for (let node = candidate, depth = 0; node && node !== main && depth < 32;
       node = node.parentElement, depth++) {
    if (node.matches(turnContainer)) ancestors.push(node);
  }
  if (ancestors.length !== 1 || !visibleWithinMain(candidate)) return false;
  const wake = ancestors[0], response = wake.nextElementSibling;
  if (!response || !response.matches(turnContainer) || response.nextElementSibling ||
      wake.querySelector(turnContainer) || response.querySelector(turnContainer) ||
      !visibleWithinMain(wake) || !visibleWithinMain(response)) return false;
  // Do not skip any sibling, even a hidden or unmarked/virtualization placeholder.
  // Consecutive ordinals prove no intervening turn was virtualized away.
  const ordinal = turn => {
    const match = /^conversation-turn-(0|[1-9][0-9]{0,8})$/.exec(turn.getAttribute('data-testid'));
    return match ? Number(match[1]) : null;
  };
  const first = ordinal(wake), second = ordinal(response);
  if (first === null || second !== first + 1 ||
      wake.getAttribute('data-turn') !== 'user' ||
      response.getAttribute('data-turn') !== 'assistant') return false;
  for (const turn of [wake, response]) {
    // Include hidden duplicates; never choose one of multiple structural turns.
    const selector = turnContainer + '[data-testid="' + turn.getAttribute('data-testid') + '"]';
    if (main.querySelectorAll(selector).length !== 1) return false;
    for (let node = turn.parentElement, depth = 0; node && node !== main && depth < 32;
         node = node.parentElement, depth++)
      if (node.matches(turnContainer) || node.hasAttribute('data-message-author-role') ||
          node.hasAttribute('data-turn')) return false;
  }
  const roles = turn => [...(turn.hasAttribute('data-message-author-role') ? [turn] : []),
    ...turn.querySelectorAll('[data-message-author-role]')];
  const users = roles(wake), assistants = roles(response);
  if (users.length > 1 || users.some(e => e.getAttribute('data-message-author-role') !== 'user') ||
      assistants.length !== 1 || assistants[0].getAttribute('data-message-author-role') !== 'assistant' ||
      response.querySelector(userBubble) || !visibleWithinMain(assistants[0])) return false;
  if ([...wake.querySelectorAll('[data-turn]')].some(e => e !== candidate ||
        e.getAttribute('data-turn') !== 'user') ||
      [...response.querySelectorAll('[data-turn]')].some(e => e !== assistants[0] ||
        e.getAttribute('data-turn') !== 'assistant')) return false;
  return true;
}"""

# Wait only for absence to resolve. An observed ambiguity ends proof immediately;
# it must not be silently retried until one candidate happens to remain.
WAIT_USER_TURN = "args => { const resolved = (" + RESOLVE_USER_TURN + """
)(args);
  if (resolved === 'AMBIGUOUS') throw new Error('SUBMISSION_UNPROVEN');
  return resolved === 'EXACT';
}"""


class BrowserAdapter:
    """Attach only; never create pages, navigate, launch, or close a browser."""

    def __init__(self, binding: Binding):
        self.binding = binding
        self.driver = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.driver = sync_playwright().start()
        try:
            self.browser = self.driver.chromium.connect_over_cdp(
                self.binding.cdp_endpoint, timeout=10000)
            self.page = self.select_page(self.browser, self.binding.chat_url)
            self.page.set_default_timeout(3000)
            return self
        except Exception:
            self.driver.stop()
            raise

    def __exit__(self, *args):
        # Disconnect the client transport; preserve the Human's browser.
        self.driver.stop()

    @staticmethod
    def select_page(browser, url):
        pages = []
        for context in browser.contexts:
            for page in context.pages:
                try:
                    if normalize_chat(page.url) == url:
                        pages.append(page)
                except WakeBlocked:
                    pass
        if len(pages) != 1:
            raise WakeBlocked("TARGET_PAGE_NOT_UNIQUE")
        return pages[0]

    def visible(self, selector):
        return self.page.locator(selector).filter(visible=True)

    def arguments(self, text):
        return dict(url=self.binding.chat_url, text=text, composer=COMPOSER,
                    account=ACCOUNT, stop=STOP, nonregular=NONREGULAR, login=LOGIN, send=SEND,
                    userTurn=USER_TURN, userBubble=USER_BUBBLE, turnContainer=TURN_CONTAINER)

    def user_turn(self, text):
        return self.page.evaluate(RESOLVE_USER_TURN, self.arguments(text))

    def _observe_surface(self):
        """Share structural gates; export only one fixed cause, never observations."""
        if self.select_page(self.browser, self.binding.chat_url) is not self.page:
            raise WakeBlocked("TARGET_PAGE_CHANGED")
        composer = self.visible(COMPOSER)
        composer_count = composer.count()
        # Observe every existing predicate rather than prioritizing the first
        # failure. A non-unique composer cannot safely supply a disabled attribute.
        predicates = (
            ("TARGET_URL_MISMATCH", normalize_chat(self.page.url) != self.binding.chat_url),
            ("MAIN_NOT_UNIQUE", self.visible("main").count() != 1),
            ("ACCOUNT_NOT_UNIQUE", self.visible(ACCOUNT).count() != 1),
            ("LOGIN_PRESENT", bool(self.visible(LOGIN).count())),
            ("NON_REGULAR_SURFACE", bool(self.visible(NONREGULAR).count())),
            ("COMPOSER_NOT_UNIQUE", composer_count != 1),
            ("COMPOSER_DISABLED", composer_count == 1 and
             composer.get_attribute("aria-disabled") == "true"),
        )
        causes = [cause for cause, unsafe in predicates if unsafe]
        cause = (causes[0] if len(causes) == 1 else "MULTIPLE_OR_AMBIGUOUS") if causes else None
        return composer, cause

    def check(self, text):
        composer, cause = self._observe_surface()
        if cause is not None:
            raise WakeBlocked("SURFACE_UNPROVEN", surface_cause=cause)
        if self.visible(STOP).count():
            raise WakeBlocked("GENERATION_ACTIVE")
        if composer.text_content() != "":
            raise WakeBlocked("DRAFT_PRESENT")
        if self.user_turn(text) != "ABSENT":
            raise WakeBlocked("OUTBOUND_ALREADY_PRESENT")

    def submit(self, text, before_insert=lambda: None, before_click=lambda: None):
        self.check(text)
        before_insert()
        if not self.page.evaluate(INSERT, self.arguments(text)):
            raise WakeBlocked("INSERT_BLOCKED")
        # Allow the application to render its Send control after the input event.
        # Await scoped readiness, never response content. Missing/ambiguous
        # controls are insertion failure, even if DOM text was staged correctly.
        try:
            self.page.wait_for_function(ACCEPT_INSERT, arg=self.arguments(text), timeout=3000)
        except Exception:
            raise WakeBlocked("INSERT_BLOCKED") from None
        if self.select_page(self.browser, self.binding.chat_url) is not self.page:
            raise WakeBlocked("TARGET_PAGE_CHANGED")
        if not self.page.evaluate(ACCEPT_INSERT, self.arguments(text)):
            raise WakeBlocked("INSERT_BLOCKED")
        if self.select_page(self.browser, self.binding.chat_url) is not self.page:
            raise WakeBlocked("TARGET_PAGE_CHANGED")
        before_click()
        if self.select_page(self.browser, self.binding.chat_url) is not self.page:
            raise WakeBlocked("TARGET_PAGE_CHANGED")
        if not self.page.evaluate(CLICK, self.arguments(text)):
            raise WakeBlocked("SEND_BLOCKED")

    def prove(self, text):
        try:
            self.page.wait_for_function(WAIT_USER_TURN, arg=self.arguments(text), timeout=5000)
        except Exception:
            raise WakeBlocked("SUBMISSION_UNPROVEN") from None
        if (self.select_page(self.browser, self.binding.chat_url) is not self.page
                or self.user_turn(text) != "EXACT" or self.visible(COMPOSER).count() != 1
                or self.visible("main").count() != 1 or self.visible(ACCOUNT).count() != 1
                or self.visible(LOGIN).count() or self.visible(NONREGULAR).count()
                or self.visible(COMPOSER).text_content() != ""):
            raise WakeBlocked("SUBMISSION_UNPROVEN")
        if not self.page.evaluate(PROVE_SEND, self.arguments(text)):
            raise WakeBlocked("SUBMISSION_UNPROVEN")

    def generation_state(self):
        # Only scoped account/composer controls, never assistant output.
        composer, cause = self._observe_surface()
        busy = bool(self.visible(STOP).count())
        # A disabled composer is still expected during proven active generation.
        # Retain that BUSY observation so the existing seen-busy/idle hold can
        # release later. All other structural failures keep the lane held.
        if cause is not None and not (cause == "COMPOSER_DISABLED" and busy):
            raise WakeBlocked("SURFACE_UNPROVEN", surface_cause=cause)
        if busy:
            return "BUSY"
        if composer.text_content() != "":
            raise WakeBlocked("DRAFT_PRESENT")
        return "IDLE"

    def completed_wake(self, text):
        """Exact-target IDLE gates surround a content-free structural witness."""
        if self.generation_state() != "IDLE":
            return False
        # No URL, account/session metadata, endpoint or local path enters the
        # witness. Existing surface gates retain sole exact-target authority.
        arguments = dict(text=text, userTurn=USER_TURN, userBubble=USER_BUBBLE,
                         turnContainer=TURN_CONTAINER)
        if self.page.evaluate(PROVE_WAKE_COMPLETION, arguments) is not True:
            return False
        return self.generation_state() == "IDLE"


def _remember_binding(data, binding):
    generation = str(binding.generation)
    snapshot = dict(chat_url=binding.chat_url, cdp_endpoint=binding.cdp_endpoint,
                    state_path=str(binding.state_path))
    if (binding.repository != data["repository"]
            or (data["bindings"] and binding.generation < max(map(int, data["bindings"])))
            or (generation in data["bindings"] and data["bindings"][generation] != snapshot)):
        raise WakeBlocked("BINDING_GENERATION_CHANGED")
    if generation not in data["bindings"] and len(data["bindings"]) >= MAX_EVENTS:
        raise WakeBlocked("STATE_CAPACITY_REQUIRES_HUMAN")
    data["bindings"][generation] = snapshot


def _original_binding(data, item, repository):
    snapshot = data["bindings"][str(item["generation"])]
    return Binding(snapshot["chat_url"], snapshot["cdp_endpoint"], Path(snapshot["state_path"]),
                   item["generation"], repository)


def _fresh(projection, event_id):
    value = projection.observe(event_id)
    if value not in {"UNRESOLVED", "RESOLVED"}:
        raise WakeBlocked("CANONICAL_UNKNOWN")
    return value


def _drain_locked(state, data, binding, adapter_factory, projection, binding_provider):
    """One finite admission-order pass, at most one submission; no new subjects."""
    receipts = []
    # Reconcile held attempts first. Pending FIFO subjects cross their own fresh
    # barrier below; old submitted history must not starve their observation budget.
    for event_id, item in list(data["events"].items()):
        if item["status"] != "AMBIGUOUS" and not (data["flight"] and data["flight"]["event_id"] == event_id):
            continue
        try:
            if _fresh(projection, event_id) == "RESOLVED":
                data["events"][event_id] = record("RESOLVED_NOOP")
                if data["flight"] and data["flight"]["event_id"] == event_id:
                    data["flight"] = None
                state.write(data)
                receipts.append(dict(event_id=event_id, status="NOOP", reason="CANONICALLY_RESOLVED"))
        except WakeBlocked:
            # Uncertainty is a hold; never infer resolution from unrelated activity.
            if item["status"] in {"PENDING", "DEFERRED"}:
                item.update(status="DEFERRED", reason="CANONICAL_UNKNOWN")
                state.write(data)

    for event_id, item in data["events"].items():
        if item["status"] != "AMBIGUOUS":
            continue
        if item["generation"] is None:
            receipts.append(dict(event_id=event_id, status="BLOCKED", reason="ATTEMPT_REQUIRES_HUMAN"))
            continue
        try:
            with adapter_factory(_original_binding(data, item, binding.repository)) as adapter:
                adapter.prove(doorbell(event_id, binding.repository))
            item.update(status="SUBMITTED", reason="NONE")
            state.write(data)
            receipts.append(dict(event_id=event_id, status="SUBMITTED", reason="EXACT_USER_TURN_PROVEN"))
        except Exception:
            item["reason"] = "ATTEMPT_REQUIRES_HUMAN"
            state.write(data)
            receipts.append(dict(event_id=event_id, status="BLOCKED", reason="ATTEMPT_REQUIRES_HUMAN"))

    flight = data["flight"]
    if flight and data["events"][flight["event_id"]]["status"] == "SUBMITTED":
        try:
            with adapter_factory(_original_binding(data, data["events"][flight["event_id"]], binding.repository)) as adapter:
                generation = adapter.generation_state()
                if generation == "BUSY":
                    flight["seen_busy"] = True
                elif generation == "IDLE" and (flight["seen_busy"] or
                        adapter.completed_wake(doorbell(flight["event_id"], binding.repository)) is True):
                    # Preserve the SUBMITTED holder and its dedupe identity.
                    # The next subject still crosses every fresh send barrier.
                    data["flight"] = None
            state.write(data)
        except Exception:
            pass
    # NON_BLOCKING_PER_EVENT_WAKE_V1: canonical uncertainty belongs to one event.
    # Other pre-submit safety failures block sends for this pass with their own
    # reason; only durable flight/attempt state can mean LANE_IN_FLIGHT.
    pass_blocker = None
    for event_id, item in data["events"].items():
        if item["status"] not in {"PENDING", "DEFERRED"}:
            continue
        receipt = dict(event_id=event_id, status="DEFERRED")
        lane_in_flight = data["flight"] is not None or any(
            v["status"] == "AMBIGUOUS" for v in data["events"].values())
        if lane_in_flight or pass_blocker is not None:
            receipt.update(dict(reason="LANE_IN_FLIGHT") if lane_in_flight else pass_blocker)
            try:
                if _fresh(projection, event_id) == "RESOLVED":
                    data["events"][event_id] = record("RESOLVED_NOOP")
                    state.write(data)
                    receipts.append(dict(event_id=event_id, status="NOOP", reason="CANONICALLY_RESOLVED"))
                    continue
            except WakeBlocked:
                receipt["reason"] = "CANONICAL_UNKNOWN"
                receipt.pop("surface_cause", None)
            item.update(status="DEFERRED", reason=receipt["reason"])
            state.write(data)
            receipts.append(receipt)
            continue
        attempted = False

        def barrier():
            current = binding_provider()
            if current != binding:
                raise WakeBlocked("BINDING_GENERATION_CHANGED")
            if _fresh(projection, event_id) == "RESOLVED":
                data["events"][event_id] = record("RESOLVED_NOOP")
                state.write(data)
                raise _Resolved()
            if binding_provider() != binding:
                raise WakeBlocked("BINDING_GENERATION_CHANGED")

        def before_click():
            nonlocal attempted
            barrier()
            item.update(status="AMBIGUOUS", generation=binding.generation, reason="ATTEMPT_REQUIRES_HUMAN")
            data["flight"] = dict(event_id=event_id, seen_busy=False)
            state.write(data)  # Durable uncertainty precedes the possible click.
            attempted = True

        try:
            barrier()
            with adapter_factory(binding) as adapter:
                text = doorbell(event_id, binding.repository)
                adapter.check(text)
                adapter.submit(text, before_insert=barrier, before_click=before_click)
                if not attempted:
                    raise WakeBlocked("SUBMISSION_UNPROVEN")
                adapter.prove(text)
                item.update(status="SUBMITTED", reason="NONE")
                state.write(data)
            receipt.update(status="SUBMITTED", reason="EXACT_USER_TURN_PROVEN")
        except _Resolved:
            receipt.update(status="NOOP", reason="CANONICALLY_RESOLVED")
        except Exception as exc:
            reason = str(exc) if isinstance(exc, WakeBlocked) else "LOCAL_FAILURE"
            if attempted:
                receipt.update(status="BLOCKED", reason=reason)
            elif data["events"][event_id]["status"] != "AMBIGUOUS":
                item.update(status="DEFERRED", generation=None, reason=reason)
                state.write(data)
                receipt.update(reason=reason)
            else:
                # A failed durable attempt write may have reached storage: never retry.
                raise WakeBlocked("STATE_WRITE_UNCERTAIN")
            if not attempted and isinstance(exc, WakeBlocked) and exc.surface_cause is not None:
                receipt["surface_cause"] = exc.surface_cause
        receipts.append(receipt)
        if receipt["status"] == "DEFERRED" and receipt["reason"] != "CANONICAL_UNKNOWN":
            pass_blocker = {key: receipt[key] for key in ("reason", "surface_cause") if key in receipt}
    return receipts


class _Resolved(Exception):
    pass


def operate(repository, binding, event_id=None, adapter_factory=BrowserAdapter,
            projection=None, binding_provider=None, compact=False):
    projection = projection or CanonicalFreshness(repository)
    binding_provider = binding_provider or (lambda: binding)
    state = State(external_path(str(binding.state_path)), repository)
    if binding.repository != repository:
        raise WakeBlocked("REGISTRY_CONFLICT")
    from .brain_attention import RECOVERY, parse_event_id
    recovery = None
    if event_id is not None:
        doorbell(event_id, repository)
        requested = parse_event_id(event_id)
        if requested.family == RECOVERY:
            recovery = requested.selectors["original_event_id"]
            event_id = recovery
    if event_id is not None:
        doorbell(event_id, repository)
        if recovery is None:
            _admit_inbox(state, event_id)
    with state.locked() as data:
        if recovery is not None:
            original = data["events"].get(recovery)
            if original is None or original["status"] not in {"PENDING", "DEFERRED", "AMBIGUOUS"}:
                raise WakeBlocked("INVALID_INPUT")
            # Missing/conflicting source evidence retains the original untouched.
            if _fresh(projection, recovery) == "RESOLVED":
                data["events"][recovery] = record("RESOLVED_NOOP")
                if data["flight"] and data["flight"]["event_id"] == recovery:
                    data["flight"] = None
                state.write(data)
                return [dict(event_id=recovery, status="NOOP", reason="CANONICALLY_RESOLVED")]
        _remember_binding(data, binding)
        _consume_inbox(state, data)
        if event_id is not None:
            doorbell(event_id, repository)
            if event_digest(event_id) in data["tombstones"]:
                return [dict(event_id=event_id, status="NOOP", reason="COMPACTED_DUPLICATE")]
            previous = data["events"].get(event_id)
            if previous and previous["status"] in {"SUBMITTED", "RESOLVED_NOOP"}:
                state.write(data)  # Also durably migrate historical SUBMITTED dedupe.
                return [dict(event_id=event_id, status="NOOP", reason="ALREADY_SUBMITTED" if previous["status"] == "SUBMITTED" else "CANONICALLY_RESOLVED")]
            if previous is None:
                raise WakeBlocked("STATE_CAPACITY_REQUIRES_HUMAN")
        state.write(data)  # Admission survives target, draft, CDP and process failure.
        receipts = _drain_locked(state, data, binding, adapter_factory, projection, binding_provider)
        if compact:
            # Summarize only freshly resolved records. Permanent exact-event digests
            # preserve dedupe even if canonical successors later become unavailable.
            for key, item in list(data["events"].items()):
                if item["status"] not in {"SUBMITTED", "RESOLVED_NOOP"}:
                    continue
                try:
                    if _fresh(projection, key) == "RESOLVED":
                        digest = event_digest(key)
                        if digest not in data["tombstones"]:
                            if len(data["tombstones"]) >= MAX_TOMBSTONES:
                                continue  # Capacity never evicts operational attention.
                            data["tombstones"].append(digest)
                        if data["flight"] and data["flight"]["event_id"] == key:
                            data["flight"] = None
                        del data["events"][key]
                except WakeBlocked:
                    pass
            state.write(data)
        if event_id is not None and not any(r["event_id"] == event_id for r in receipts):
            item = data["events"].get(event_id)
            receipts.append(dict(event_id=event_id, status="BLOCKED", reason="ATTEMPT_REQUIRES_HUMAN" if item and item["status"] == "AMBIGUOUS" else "LANE_IN_FLIGHT"))
        return receipts


def deliver(event_id: str, repository: str, binding: Binding, adapter_factory=BrowserAdapter,
            projection=None, binding_provider=None) -> dict:
    doorbell(event_id, repository)
    receipt = dict(event_id=event_id, status="BLOCKED", reason="LOCAL_FAILURE")
    try:
        from .brain_attention import RECOVERY, parse_event_id
        item = parse_event_id(event_id)
        original = item.selectors["original_event_id"] if item.family == RECOVERY else event_id
        receipts = operate(repository, binding, event_id, adapter_factory, projection, binding_provider)
        return next(r for r in receipts if r["event_id"] == original)
    except WakeBlocked as exc:
        return dict(receipt, reason=str(exc))
    except Exception:
        return receipt  # Never export private dependency/browser exceptions.


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo malformed arguments into an operational log.
        print(json.dumps({"status": "BLOCKED", "reason": "INVALID_INPUT"}))
        raise SystemExit(1)


def main(argv=None) -> int:
    parser = _Parser(description="Bounded local user doorbell", allow_abbrev=False)
    parser.add_argument("--event-id")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--drain", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--rechecks", type=int, default=1)
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args(argv)
    try:
        if (args.drain == (args.event_id is not None) or (args.compact and not args.drain)
                or not 1 <= args.rechecks <= 8 or not 0 <= args.interval <= 30):
            raise WakeBlocked("INVALID_INPUT")
        if not REPOSITORY_PATTERN.fullmatch(args.repository):
            raise WakeBlocked("INVALID_INPUT")
        if args.event_id is not None:
            doorbell(args.event_id, args.repository)
    except WakeBlocked:
        print(json.dumps({"status": "BLOCKED", "reason": "INVALID_INPUT"}))
        return 1
    try:
        if os.environ.get("AIOS_LOCAL_CHAT_WAKE_ENABLED") != "true":
            raise WakeBlocked("ENABLE_GATE_CLOSED")
        provider = lambda: load_binding(args.repository)
        binding = provider()
        if args.drain:
            for index in range(args.rechecks):
                if os.environ.get("AIOS_LOCAL_CHAT_WAKE_ENABLED") != "true":
                    raise WakeBlocked("ENABLE_GATE_CLOSED")
                receipts = operate(args.repository, provider(), binding_provider=provider, compact=args.compact)
                for receipt in receipts:
                    print(json.dumps(receipt, sort_keys=True))
                # One invocation gets at most one possible submission. A proof
                # or ambiguity hold also ends rechecks; neither permits resend.
                if any(receipt["status"] in {"SUBMITTED", "BLOCKED"} for receipt in receipts):
                    break
                if index + 1 < args.rechecks:
                    time.sleep(args.interval)
            return 0  # Held work is reported, never converted into resend permission.
        receipt = deliver(args.event_id, args.repository, binding, binding_provider=provider)
    except WakeBlocked as exc:
        receipt = {"status": "BLOCKED", "reason": str(exc)}
        if args.event_id is not None:
            receipt["event_id"] = args.event_id
    except Exception:
        receipt = {"status": "BLOCKED", "reason": "LOCAL_FAILURE"}
        if args.event_id is not None:
            receipt["event_id"] = args.event_id
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] in ("SUBMITTED", "NOOP", "DEFERRED") else 1


if __name__ == "__main__":
    sys.exit(main())
