"""Bounded terminal source capture; independent issuance remains blocked.

Nothing in this module enrolls an issuer. In particular, a RuntimeCompletion
constructor, protected-looking filename, upstream URL or Git object is not an
admission credential. See the v1 contract for the required authority revision.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FORMAT = "AIOS_RUNTIME_TERMINAL_SOURCE_CONTENT"
VERSION = 1
SOURCE_PATH = ".ai/transport/runtime-source-v1.json"
LOCAL_NAME = "terminal-source-v1.json"
ADMISSION_BLOCKER = "RUNTIME_ADMISSION_AUTHORITY_UNAVAILABLE"
TRANSPORT_BLOCKER = "AUTHENTICATED_TRANSPORT_CONTEXT_UNAVAILABLE"
REVIEW_BLOCKER = "INDEPENDENT_REVIEW_INGRESS_AUTHORITY_UNAVAILABLE"
MAX_RECORD = 1024 * 1024
MAX_SOURCE = 64 * 1024
MAX_EVIDENCE = 32
MAX_RAW = 8 * 1024 * 1024
MAX_TOTAL_RAW = 32 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_EFFECTS = {
    "state": "NOT_ACTIVATED", "acceptance_discharge": False,
    "proof_reuse": False, "continuation": False, "scheduling": False,
    "publication": False, "policy_replacement": False,
}


class SourceError(ValueError):
    """A safe, typed reason; never contains command output or a local path."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SourceObservation:
    status: str
    reasons: tuple[str, ...]
    source_path: Path | None = None  # Protected local observation only.
    issuer_authenticated: bool = False
    reviewer_pass: bool = False
    activation: str = "NOT_ACTIVATED"


@dataclass(frozen=True)
class ContentObservation:
    """Offline facts, deliberately incompatible with an issuance receipt."""

    status: str
    reasons: tuple[str, ...]
    main_sha: str | None = None
    artifact_sha: str | None = None
    decision_sha: str | None = None
    review_content: str = "UNREVIEWED"
    recorded_verdict: str | None = None
    raw_state: str = "RAW_UNAVAILABLE"
    issuer_authenticated: bool = False
    reviewer_pass: bool = False
    atomic_currentness: bool = False
    activation: str = "NOT_ACTIVATED"


def _dump(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise SourceError("DUPLICATE_FIELD")
        result[key] = value
    return result


def _json(content: bytes, limit: int = MAX_RECORD) -> dict[str, Any]:
    if type(content) is not bytes or len(content) > limit:
        raise SourceError("RECORD_LIMIT")
    try:
        value = json.loads(content, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(SourceError("INVALID_JSON")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("INVALID_JSON") from None
    if type(value) is not dict:
        raise SourceError("INVALID_RECORD")
    return value


def _fields(value: Any, names: set[str]) -> None:
    if type(value) is not dict or set(value) != names:
        raise SourceError("INVALID_SOURCE_FIELDS")


def _identifier(value: Any) -> None:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise SourceError("INVALID_IDENTITY")


def _sha(value: Any) -> None:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise SourceError("INVALID_GIT_IDENTITY")


def _identity(value: Any) -> None:
    _fields(value, {"blob_sha", "sha256", "size"})
    _sha(value["blob_sha"])
    if (type(value["sha256"]) is not str or not _DIGEST.fullmatch(value["sha256"])
            or type(value["size"]) is not int or not 0 <= value["size"] <= MAX_RECORD):
        raise SourceError("INVALID_RECORD_IDENTITY")


def _git(repo: Path, *args: str, content: bytes | None = None, optional: bool = False) -> bytes:
    # Local-only plumbing. No inherited Git overrides, replacements or lazy fetch.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    if args[0] == "show":
        size = _git(repo, "cat-file", "-s", args[1])
        if not size.strip().isdigit() or int(size) > MAX_RECORD:
            raise SourceError("RECORD_LIMIT")
    try:
        completed = subprocess.run(("git", "--no-replace-objects", "-C", str(repo), *args),
                                   input=content, capture_output=True, env=env, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise SourceError("LOCAL_GIT_UNAVAILABLE") from None
    if completed.returncode:
        if optional and completed.returncode == 1:
            return b""
        raise SourceError("LOCAL_GIT_RECORD_UNAVAILABLE")
    if len(completed.stdout) > MAX_RECORD:
        raise SourceError("RECORD_LIMIT")
    return completed.stdout


def _record(repo: Path, content: bytes) -> dict[str, Any]:
    if len(content) > MAX_RECORD:
        raise SourceError("RECORD_LIMIT")
    return {"blob_sha": _git(repo, "hash-object", "--stdin", content=content).decode("ascii").strip(),
            "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


def _protected_root(repo: Path) -> Path:
    directory = Path(_git(repo, "rev-parse", "--git-dir").decode("utf-8").strip())
    if not directory.is_absolute():
        directory = repo / directory
    return directory.resolve() / "aios"


def _confined(path: Path, root: Path) -> Path:
    # Refuse symlinks/junctions on the entire confined path, including root.
    if not path.is_absolute() or ".." in path.parts:
        raise SourceError("UNPROTECTED_LOCAL_PATH")
    path = path.absolute()
    root = root.absolute()
    if not path.is_relative_to(root):
        raise SourceError("UNPROTECTED_LOCAL_PATH")
    for component in (root, *reversed(list(path.parents)[:len(path.parts) - len(root.parts) - 1]), path):
        if component.exists() and (component.is_symlink() or
                getattr(component.lstat(), "st_file_attributes", 0) & 0x400):
            raise SourceError("UNPROTECTED_LOCAL_PATH")
    if not path.resolve().is_relative_to(root.resolve()):
        raise SourceError("UNPROTECTED_LOCAL_PATH")
    return path


def _read(path: Path, root: Path, limit: int = MAX_RECORD) -> bytes:
    path = _confined(path, root)
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            content = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        current = path.stat()
    except OSError:
        raise SourceError("LOCAL_RECORD_UNAVAILABLE") from None
    if not stat.S_ISREG(before.st_mode) or len(content) > limit:
        raise SourceError("RECORD_LIMIT")
    fingerprint = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(current):
        raise SourceError("TORN_LOCAL_RECORD")
    return content


def _raw_identity(path: Path, root: Path) -> dict[str, Any]:
    content = _read(path, root, MAX_RAW)
    return {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
            "availability": "LOCAL_CAPTURED"}


def decode_content_source(content: bytes) -> dict[str, Any]:
    """Strict safe metadata decoder; successful decoding confers no authority."""
    value = _json(content, MAX_SOURCE)
    _fields(value, {"format", "version", "authority", "phase", "operation", "task", "run",
                    "candidate", "result", "execution_profile", "evidence", "effects"})
    if (value["format"] != FORMAT or type(value["version"]) is not int or value["version"] != VERSION
            or value["phase"] != "TERMINAL_RESULT"
            or value["operation"] not in ("PRIMARY", "REMEDIATION", "REPAIR", "DIRECT_CANDIDATE")):
        raise SourceError("UNSUPPORTED_SOURCE_VERSION_OR_PHASE")
    if value["authority"] != "CONDITIONAL_CONTENT_ONLY":
        raise SourceError("FORGED_ISSUER_ASSERTION")
    _fields(value["effects"], set(_EFFECTS))
    if any(type(value["effects"][key]) is not type(expected) or value["effects"][key] != expected
           for key, expected in _EFFECTS.items()):
        raise SourceError("UNAUTHORIZED_EFFECT")
    task = value["task"]
    _fields(task, {"id", "revision", "commit_sha", "record"})
    _identifier(task["id"])
    if type(task["revision"]) is not int or task["revision"] < 1:
        raise SourceError("INVALID_TASK_REVISION")
    _sha(task["commit_sha"])
    _identity(task["record"])
    run = value["run"]
    _fields(run, {"id", "base_sha", "executor", "record"})
    _identifier(run["id"])
    _sha(run["base_sha"])
    if run["executor"] not in ("codex", "antigravity", "antigravity-minimax"):
        raise SourceError("INVALID_EXECUTOR")
    _identity(run["record"])
    _fields(value["candidate"], {"subject_sha", "candidate_sha", "tree_sha"})
    for sha in value["candidate"].values():
        _sha(sha)
    if value["candidate"]["subject_sha"] != value["candidate"]["candidate_sha"]:
        raise SourceError("SWAPPED_VERIFICATION_SUBJECT")
    _identity(value["result"])
    _identity(value["execution_profile"])
    evidence = value["evidence"]
    if type(evidence) is not list or not 0 < len(evidence) <= MAX_EVIDENCE:
        raise SourceError("EVIDENCE_POPULATION_LIMIT")
    ids = set()
    total = 0
    for item in evidence:
        _fields(item, {"id", "type", "record", "command_sha256", "exit_code", "raw"})
        _identifier(item["id"])
        if item["id"] in ids:
            raise SourceError("AMBIGUOUS_EVIDENCE")
        ids.add(item["id"])
        if item["type"] != "VERIFICATION" or type(item["exit_code"]) is not int:
            raise SourceError("UNSUPPORTED_PROOF_TYPE_OR_OUTCOME")
        _identity(item["record"])
        if type(item["command_sha256"]) is not str or not _DIGEST.fullmatch(item["command_sha256"]):
            raise SourceError("INVALID_COMMAND_DIGEST")
        raw = item["raw"]
        _fields(raw, {"sha256", "size", "availability"})
        if (raw["availability"] != "LOCAL_CAPTURED" or type(raw["sha256"]) is not str
                or not _DIGEST.fullmatch(raw["sha256"]) or type(raw["size"]) is not int
                or not 0 <= raw["size"] <= MAX_RAW):
            raise SourceError("INVALID_RAW_IDENTITY")
        total += raw["size"]
    if total > MAX_TOTAL_RAW:
        raise SourceError("RAW_POPULATION_LIMIT")
    return value


def _persist(path: Path, root: Path, content: bytes) -> None:
    if len(content) > MAX_RECORD:
        raise SourceError("RECORD_LIMIT")
    path = _confined(path, root)
    if path.exists():
        if _read(path, root) != content:
            raise SourceError("SOURCE_CAPTURE_REPLAY_CONFLICT")
        return
    # Existing verification storage only; no new trust registry or public raw.
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if _read(path, root) != content:
            raise SourceError("SOURCE_CAPTURE_REPLAY_CONFLICT") from None
    except OSError:
        raise SourceError("SOURCE_CAPTURE_PERSISTENCE_FAILED") from None


def _capture_completion(completion: Any, package: Any, operation: str) -> SourceObservation:
    """Observe real completion output without pretending its caller is admitted.

    Called after canonical RESULT persistence, before existing transport. This
    never emits a trusted source, even for an exact RuntimeCompletion instance.
    """
    from .runtime import RuntimeCompletion, result_package_data
    from .task import parse_task

    blockers = (ADMISSION_BLOCKER, TRANSPORT_BLOCKER)
    try:
        if type(completion) is not RuntimeCompletion:
            raise SourceError("UNOWNED_RUNTIME_INSTANCE")
        run, task = completion.run, completion.task
        _identifier(run.run_id)
        _identifier(task.task_id)
        _sha(run.base_sha)
        subject = package.result.head_sha
        _sha(subject)
        if completion.verification_subject_sha != subject:
            raise SourceError("PRETERMINAL_OR_SWAPPED_SUBJECT")
        root = _protected_root(completion.transport_repo)
        if (completion.state.results.absolute() != root / "results"
                or completion.state.verification.absolute() != root / "verification"
                or completion.run_path.absolute() != root / "runs" / f"{run.run_id}.json"):
            raise SourceError("UNPROTECTED_RUNTIME_STATE")
        run_bytes = _read(completion.run_path, root)
        run_data = _json(run_bytes)
        carrier = run_data
        if run_data.get("kind") == "REMEDIATION":
            carrier = run_data.get("execution", {}).get("run")
        if _dump(carrier) != _dump(asdict(run)):
            raise SourceError("SWAPPED_RUN")
        if (run.task.id != task.task_id or run.task.revision != task.revision
                or run.status != "ACTIVE" or run.head_sha is not None
                or Path(run.workspace).resolve() != completion.repo.resolve()):
            raise SourceError("SWAPPED_TASK_OR_RUN")
        name = f".ai/tasks/{task.task_id}.yaml"
        task_bytes = _git(completion.repo, "show", f"{run.base_sha}:{name}")
        if (parse_task(task_bytes.decode("utf-8")) != task
                or _git(completion.repo, "show", f"{subject}:{name}") != task_bytes):
            raise SourceError("SWAPPED_TASK_BLOB")
        result_path = root / "results" / f"{run.run_id}.json"
        result_bytes = _read(result_path, root)
        if _dump(_json(result_bytes)) != _dump(result_package_data(package)):
            raise SourceError("SWAPPED_RESULT_OR_EVIDENCE")
        result_data = _json(result_bytes)
        if not 0 < len(package.evidence) <= MAX_EVIDENCE:
            raise SourceError("EVIDENCE_POPULATION_LIMIT")
        evidence, private, total_raw = [], [], 0
        for item, record in zip(package.evidence, result_data["evidence"], strict=True):
            if item.run_id != run.run_id or item.subject_sha != subject:
                raise SourceError("SWAPPED_EVIDENCE_SUBJECT")
            raw_identity = _raw_identity(Path(item.raw_path), root / "verification")
            total_raw += raw_identity["size"]
            if total_raw > MAX_TOTAL_RAW:
                raise SourceError("RAW_POPULATION_LIMIT")
            evidence.append({"id": item.evidence_id, "type": item.type,
                             "record": _record(completion.repo, _dump(record)),
                             "command_sha256": hashlib.sha256(item.source.command.encode("utf-8")).hexdigest(),
                             "exit_code": item.result.exit_code,
                             "raw": raw_identity})
            private.append({"id": item.evidence_id, "command": item.source.command,
                            "outcome": asdict(item.result), "raw_path": item.raw_path})
        profile = _read(root / "execution-profiles" / f"{run.run_id}.json", root)
        profile_data = _json(profile)
        if profile_data.get("run_id") != run.run_id or profile_data.get("executor") != run.executor:
            raise SourceError("SWAPPED_EXECUTION_PROFILE")
        source = {
            "format": FORMAT, "version": VERSION, "authority": "CONDITIONAL_CONTENT_ONLY",
            "phase": "TERMINAL_RESULT", "operation": operation,
            "task": {"id": task.task_id, "revision": task.revision, "commit_sha": run.base_sha,
                     "record": _record(completion.repo, task_bytes)},
            "run": {"id": run.run_id, "base_sha": run.base_sha, "executor": run.executor,
                    "record": _record(completion.repo, run_bytes)},
            "candidate": {"subject_sha": subject, "candidate_sha": subject,
                          "tree_sha": _git(completion.repo, "rev-parse", f"{subject}^{{tree}}").decode("ascii").strip()},
            "result": _record(completion.repo, result_bytes),
            "execution_profile": _record(completion.repo, profile),
            "evidence": evidence, "effects": dict(_EFFECTS),
        }
        decode_content_source(_dump(source))
        local = root / "verification" / run.run_id / LOCAL_NAME
        _persist(local, root, _dump({"source": source, "private": private,
                                    "issuance": "BLOCK", "reasons": list(blockers)}))
        return SourceObservation("BLOCK", blockers, local)
    except SourceError as exc:
        return SourceObservation("BLOCK", (*blockers, exc.reason))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        return SourceObservation("BLOCK", (*blockers, "INVALID_TERMINAL_SOURCE"))


def observe_issued_source(run_id: str) -> SourceObservation:
    """Canonical entry point has no caller-selected repo, remote or issuer.

    A separate bounded authority revision must supply the existing admission
    and transport observation. No registry, setter or fixture fallback exists.
    """
    _identifier(run_id)
    return SourceObservation("UNKNOWN", (ADMISSION_BLOCKER, TRANSPORT_BLOCKER, REVIEW_BLOCKER))


def refuse_unowned_transport_source(content: bytes) -> None:
    """Prevent a public transport helper from laundering a source candidate."""
    decode_content_source(content)
    raise SourceError(ADMISSION_BLOCKER)


def _refs(repo: Path, run_id: str) -> dict[str, str]:
    names = ("refs/heads/main", *(f"refs/heads/aios/{kind}/{run_id}" for kind in
             ("review", "artifacts", "review-decision", "failure-artifacts")))
    result = {}
    for name in names:
        content = _git(repo, "show-ref", "--verify", "--hash", name, optional=True)
        if content:
            sha = content.decode("ascii").strip()
            _sha(sha)
            result[name] = sha
    return result


def inspect_content_source(repo: Path, run_id: str) -> ContentObservation:
    """Separate offline local-Git diagnostic, never an authenticated consumer.

    Refs are fixed, there is no network or raw callback. Even exact matching
    content retains UNKNOWN issuer and Reviewer authority. Missing public raw
    is RAW_UNAVAILABLE; no digest is advertised as an authenticated comparison.
    """
    from .artifacts import validate_result
    from .review import parse_review, validate_review
    from .task import parse_task

    _identifier(run_id)
    reasons = (ADMISSION_BLOCKER, TRANSPORT_BLOCKER, REVIEW_BLOCKER, "RAW_UNAVAILABLE")
    facts: dict[str, Any] = {}
    try:
        before = _refs(repo, run_id)
        artifact = before.get(f"refs/heads/aios/artifacts/{run_id}")
        if artifact is None:
            return ContentObservation("UNKNOWN", (*reasons, "TERMINAL_SOURCE_MISSING"))
        facts["artifact_sha"] = artifact
        facts["main_sha"] = before.get("refs/heads/main")
        paths = _git(repo, "ls-tree", "-r", "--name-only", artifact, "--", SOURCE_PATH).decode("utf-8").splitlines()
        if SOURCE_PATH not in paths:
            return ContentObservation("UNKNOWN", (*reasons, "HISTORIC_SOURCE_UNISSUED"), **facts)
        source = decode_content_source(_git(repo, "show", f"{artifact}:{SOURCE_PATH}"))
        subject = source["candidate"]["candidate_sha"]
        if (source["run"]["id"] != run_id
                or before.get(f"refs/heads/aios/review/{run_id}") != subject
                or f"refs/heads/aios/failure-artifacts/{run_id}" in before):
            raise SourceError("CONFLICTING_TERMINAL_SOURCE")
        for path, identity in (("run.json", source["run"]["record"]),
                               ("result.json", source["result"]),
                               ("execution-profile.json", source["execution_profile"])):
            content = _git(repo, "show", f"{artifact}:.ai/transport/{path}")
            if _record(repo, content) != identity:
                raise SourceError("SWAPPED_TRANSPORT_RECORD")
        run_data = _json(_git(repo, "show", f"{artifact}:.ai/transport/run.json"))
        carrier = run_data.get("execution", {}).get("run") if run_data.get("kind") == "REMEDIATION" else run_data
        if (type(carrier) is not dict or carrier.get("run_id") != run_id
                or _dump(carrier.get("task")) != _dump({"id": source["task"]["id"], "revision": source["task"]["revision"]})
                or carrier.get("base_sha") != source["run"]["base_sha"]
                or carrier.get("executor") != source["run"]["executor"]
                or carrier.get("status") != "ACTIVE" or carrier.get("head_sha") is not None):
            raise SourceError("SWAPPED_TASK_OR_RUN")
        profile = _json(_git(repo, "show", f"{artifact}:.ai/transport/execution-profile.json"))
        if profile.get("run_id") != run_id or profile.get("executor") != source["run"]["executor"]:
            raise SourceError("SWAPPED_EXECUTION_PROFILE")
        task_path = f".ai/tasks/{source['task']['id']}.yaml"
        task_bytes = _git(repo, "show", f"{source['task']['commit_sha']}:{task_path}")
        if (source["task"]["commit_sha"] != source["run"]["base_sha"]
                or _record(repo, task_bytes) != source["task"]["record"]
                or _git(repo, "show", f"{subject}:{task_path}") != task_bytes):
            raise SourceError("SWAPPED_TASK_BLOB")
        task = parse_task(task_bytes.decode("utf-8"))
        if task.task_id != source["task"]["id"] or task.revision != source["task"]["revision"]:
            raise SourceError("SWAPPED_TASK_REVISION")
        tree = _git(repo, "rev-parse", f"{subject}^{{tree}}").decode("ascii").strip()
        if tree != source["candidate"]["tree_sha"]:
            raise SourceError("SWAPPED_CANDIDATE_TREE")
        result_data = _json(_git(repo, "show", f"{artifact}:.ai/transport/result.json"))
        result = validate_result(result_data["result"])
        if result.head_sha != subject or len(result_data["evidence"]) != len(source["evidence"]):
            raise SourceError("SWAPPED_RESULT_OR_EVIDENCE")
        for item, binding in zip(result_data["evidence"], source["evidence"], strict=True):
            if (item["evidence_id"] != binding["id"] or item["run_id"] != run_id
                    or item["subject_sha"] != subject or item["type"] != binding["type"]
                    or _record(repo, _dump(item)) != binding["record"]
                    or hashlib.sha256(item["source"]["command"].encode("utf-8")).hexdigest() != binding["command_sha256"]
                    or item["result"]["exit_code"] != binding["exit_code"]):
                raise SourceError("SWAPPED_EVIDENCE")
        main = facts["main_sha"]
        if main is None or _git(repo, "show", f"{main}:{task_path}") != task_bytes:
            raise SourceError("STALE_MAIN_TASK")
        decision = before.get(f"refs/heads/aios/review-decision/{run_id}")
        if decision is not None:
            facts["decision_sha"] = decision
            review_paths = _git(repo, "ls-tree", "-r", "--name-only", decision, "--", ".ai/reviews").decode("utf-8").splitlines()
            review_paths = [path for path in review_paths if path.endswith((".yaml", ".yml"))]
            if len(review_paths) != 1:
                raise SourceError("AMBIGUOUS_REVIEW_HISTORY")
            review = parse_review(_git(repo, "show", f"{decision}:{review_paths[0]}").decode("utf-8"))
            if review.mode != "PRIMARY":
                raise SourceError("UNSUPPORTED_REVIEW_HISTORY")
            if review.reviewed_sha != subject:
                raise SourceError("STALE_REVIEW_CANDIDATE")
            validate_review(task=task, result=result, review=review)
            facts.update(review_content="CONTENT_CONSISTENT", recorded_verdict=review.verdict)
        if _refs(repo, run_id) != before:
            raise SourceError("REF_SNAPSHOT_DRIFT")
        return ContentObservation("UNKNOWN", reasons, **facts)
    except SourceError as exc:
        # Retain pins for diagnosis, never a positive review conclusion on drift.
        facts.pop("recorded_verdict", None)
        facts["review_content"] = "BLOCKED"
        return ContentObservation("BLOCK", (*reasons, exc.reason), **facts)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        facts.pop("recorded_verdict", None)
        facts["review_content"] = "BLOCKED"
        return ContentObservation("BLOCK", (*reasons, "INVALID_SOURCE_OR_REVIEW"), **facts)
