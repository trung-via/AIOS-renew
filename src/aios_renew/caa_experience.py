"""CAA-01: bounded canonical experience data, with no advisory or lifecycle authority.

Capture a cut from canonical refs, then reconstruct *only* that cut. Historical
TASK-only cuts deliberately cannot discover terminal/correction/review sources.
Portable dictionaries are output data, never authenticated source-cut inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Event, Thread
import time
from typing import Any, Sequence

import yaml

from .artifacts import validate_evidence, validate_result, validate_result_package
from .brain_attention import FAILURE, RESULT, GitSources, attention, resolve_return_affinity
from .review import parse_remediation, parse_review, validate_remediation, validate_review
from .review_transport import (
    RemoteRepairAuthorization, _bind_performance_terminal_identity,
    _decode_repair_supersession, _select_current_repair_authorization,
    failure_context, failure_document, task_run_prefix,
)
from .return_affinity import document_affinity
from .run import Run, RunTaskReference
from .task import validate_task
from .terminal_attention import _load_yaml


MAX_TASKS = MAX_EPISODES = 8
MAX_REFS = 128
MAX_DOCUMENT_BYTES = 262144
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 65536
MAX_SEMANTIC_OUTPUT_BYTES = 131072
MAX_DEPTH = 24
MAX_NODES = 32768
MAX_GIT_CALLS = 512
MAX_SECONDS = 60
COMMAND_SECONDS = 10
_MAIN = "refs/heads/main"
_ROOT = "refs/heads/aios/"
_SEAL = object()
_TASK = re.compile(r"TASK-[A-Za-z0-9_-]{1,90}")
_RUN = re.compile(r"RUN-[A-Za-z0-9_-]{1,90}-\d{3,8}")
_SHA = re.compile(r"[0-9a-f]{40}")

# A feature is an observed lexical mention, never proof of implementation or
# causality. Only these code-owned names leave the reader; no source prose does.
SEMANTIC_TERMS = {
    "SOURCE_IDENTITY": ("authenticated", "provenance", "source bound", "identity", "forged", "seal", "attribution"),
    "TEMPORAL_ISOLATION": ("snapshot", "as of", "historical", "future", "hindsight", "temporal"),
    "BOUNDED_WORK": ("bounded", "finite", "budget", "timeout", "retry", "resource"),
    "DATA_AUTHORITY": ("read only", "untrusted", "prompt injection", "authority", "data only"),
    "PRIVACY": ("private", "redact", "credential", "sensitive", "raw logs"),
    "SCHEMA_VALIDATION": ("schema", "malformed", "parser", "json", "validation"),
    "FAILURE_CONTAINMENT": ("non blocking", "containment", "unavailable", "abstain", "degrade"),
    "ACCEPTANCE_COVERAGE": ("acceptance", "completion gate", "claimed", "coverage"),
    "VERIFICATION_ISOLATION": ("verification", "evidence", "test summary", "pytest"),
    "CONSUMER_COMPATIBILITY": ("consumer", "compatibility", "interface", "adapter", "provider"),
    "DIRECT_AUTHORING": ("human on demand", "direct authoring", "audit policy"),
    "CORRECTION_LINEAGE": ("correction", "supersession", "repair", "remediation"),
}
_UNSAFE_SEMANTIC = re.compile(
    r"ignore\b|override\b|system\s*:|developer\s*:|<\|.*?\|>|"
    r"\b(?:invoke|dispatch|authorize|sudo)\b|aios\s+(?:run|repair|remediate)|"
    r"(?:password|secret|token|credential|api[_ -]?key)\s*[=:]|"
    r"\bsk-[A-Za-z0-9]|[A-Za-z]:[/\\]|\\\\|/(?:home|Users|private|tmp)/|"
    r"https?://|[A-Za-z0-9+/=_-]{48,}", re.IGNORECASE,
)


def _semantic_features(fields, binding):
    """Lossy, closed-vocabulary feature shaping; unsupported meaning is UNKNOWN.

    A witness points to the entire exact field. Matching a term establishes only
    its mention (negation and implementation remain unknown). Suspicious fields
    are excluded, not cleaned into seemingly trustworthy evidence.
    """
    witnesses, excluded, coverage = {}, 0, {}
    for category, pointer, text in fields:
        coverage.setdefault(category, "UNKNOWN")
        if not isinstance(text, str) or _UNSAFE_SEMANTIC.search(text):
            excluded += 1
            continue
        normalized = re.sub(r"[_-]+", " ", text.casefold())
        for concept, terms in SEMANTIC_TERMS.items():
            if any(re.search(r"\b" + re.escape(term) + r"\w*\b", normalized) for term in terms):
                coverage[category] = "FEATURES_ONLY"
                witnesses.setdefault(concept, dict(
                    concept=concept, category=category, status="OBSERVED",
                    meaning="LEXICAL_MENTION_ONLY", polarity="UNKNOWN",
                    source=dict(binding, pointer=pointer)))
    return dict(signals=[witnesses[key] for key in sorted(witnesses)],
                field_coverage=coverage, excluded_field_count=excluded,
                free_text="WITHHELD", technical_root_cause="UNKNOWN",
                implementation="UNKNOWN", normative_policy="UNKNOWN")


def _task_semantics(task, source, cut):
    # Prefer explicit acceptance/invariant witnesses. Non-goals, assumptions,
    # diagnostic prose, claim text and repair instructions are never witnesses.
    fields = [("acceptance", f"/acceptance/{i}/condition", a.condition)
              for i, a in enumerate(task.acceptance)]
    fields += [("invariant", f"/constraints/hard/{i}", text)
               for i, text in enumerate(task.constraints.hard)]
    fields += [("goal", "/goal", task.goal)]
    fields += [("scope", f"/scope/modify/{i}", text) for i, text in enumerate(task.scope.modify)]
    fields += [("consumers", f"/scope/inspect/{i}", text) for i, text in enumerate(task.scope.inspect)]
    fields += [("verification", f"/verification/required/{i}", text)
               for i, text in enumerate(task.verification.required)]
    return dict(_semantic_features(fields, source), task_id=task.task_id, revision=task.revision,
                as_of_sha=cut.as_of_sha, cut_digest=cut._integrity,
                scope_digest=_digest(dict(inspect=task.scope.inspect, modify=task.scope.modify)))


class ExperienceError(ValueError):
    """A bounded reader error. Messages never contain source text or locations."""


def _encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_encode(value).encode("ascii")).hexdigest()


def _require(condition: bool, code: str = "SOURCE_INVALID") -> None:
    if not condition:
        raise ExperienceError(code)


def _shape(value: Any, *, max_depth: int = MAX_DEPTH) -> None:
    """Reject depth/node excess and cycles before downstream canonical parsing."""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        _require(count <= MAX_NODES and depth <= max_depth, "LIMIT_EXHAUSTED")
        if isinstance(item, dict):
            _require(len(item) <= MAX_NODES, "LIMIT_EXHAUSTED")
            pending.extend((v, depth + 1) for pair in item.items() for v in pair)
        elif isinstance(item, (list, tuple)):
            _require(len(item) <= MAX_NODES, "LIMIT_EXHAUSTED")
            pending.extend((v, depth + 1) for v in item)
        else:
            _require(item is None or type(item) in (str, int, bool, float))


class _GitBudget:
    """Read-only Git commands with bounded pipes, wall time and aggregate output."""

    def __init__(self) -> None:
        self.calls = self.bytes = 0
        self.deadline = time.monotonic() + MAX_SECONDS

    def __call__(self, path: Path, *args: str, allow_missing: bool = False) -> str:
        self.calls += 1
        remaining = self.deadline - time.monotonic()
        _require(self.calls <= MAX_GIT_CALLS and remaining > 0, "LIMIT_EXHAUSTED")
        # No fetch, shell, external command, Runtime state or writable Git index.
        _require(bool(args) and args[0] in {
            "rev-parse", "symbolic-ref", "config", "remote", "ls-remote", "cat-file",
            "ls-tree", "show", "rev-list", "diff-tree",
        })
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
        limit = min(MAX_DOCUMENT_BYTES, MAX_SOURCE_BYTES - self.bytes)
        _require(limit > 0, "LIMIT_EXHAUSTED")
        process = subprocess.Popen(
            ["git", "--no-pager", "-C", str(path), *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        overflow = Event()
        outputs: list[bytearray] = [bytearray(), bytearray()]

        def drain(stream, target):
            try:
                while chunk := stream.read(8192):
                    if len(target) + len(chunk) > limit:
                        overflow.set()
                        process.kill()
                        break
                    target.extend(chunk)
            except (OSError, ValueError):
                overflow.set()
            finally:
                stream.close()

        readers = [Thread(target=drain, args=(stream, target), daemon=True)
                   for stream, target in zip((process.stdout, process.stderr), outputs)]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=min(COMMAND_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise ExperienceError("LIMIT_EXHAUSTED") from None
        finally:
            for reader in readers:
                reader.join(timeout=1)
        _require(not any(t.is_alive() for t in readers) and not overflow.is_set(), "LIMIT_EXHAUSTED")
        self.bytes += sum(len(x) for x in outputs)
        _require(self.bytes <= MAX_SOURCE_BYTES, "LIMIT_EXHAUSTED")
        _require(process.returncode == 0 or (allow_missing and process.returncode == 1), "SOURCE_UNAVAILABLE")
        return outputs[0].decode("utf-8", errors="strict").strip()


def _transport(root: Path, git: _GitBudget) -> tuple[str, str]:
    # Same upstream/unanimous-detached policy as review_transport's canonical
    # resolvers, with every subprocess inside this read's finite budget.
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", allow_missing=True)
    if branch:
        remote = git(root, "config", "--get", f"branch.{branch}.remote", allow_missing=True)
    else:
        rows = git(root, "config", "--get-regexp", r"^branch\..*\.remote$", allow_missing=True)
        parts = [line.split(None, 1) for line in rows.splitlines()]
        _require(all(len(p) == 2 for p in parts), "CANONICAL_TRANSPORT_UNAVAILABLE")
        remotes = {p[1].strip() for p in parts}
        _require(len(remotes) == 1, "CANONICAL_TRANSPORT_UNAVAILABLE")
        remote = remotes.pop()
    _require(remote != ".", "CANONICAL_TRANSPORT_UNAVAILABLE")
    _require(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,95}", remote) is not None,
             "CANONICAL_TRANSPORT_UNAVAILABLE")
    return remote, git(root, "remote", "get-url", remote)


def _patterns(task_ids: tuple[str, ...]) -> tuple[str, ...]:
    return (_MAIN, *(f"{_ROOT}{namespace}/{task_run_prefix(task_id)}*"
                     for task_id in task_ids for namespace in (
                         "artifacts", "failure-artifacts", "review", "failure",
                         "review-decision", "repair", "repair-supersession", "remediation")))


def _refs(sources: GitSources, patterns: tuple[str, ...]) -> dict[str, str]:
    values = sources.refs(*patterns)
    _require(len(values) <= MAX_REFS, "LIMIT_EXHAUSTED")
    for ref, sha in values.items():
        _require(_SHA.fullmatch(sha) is not None and len(ref) <= 256
                 and any(fnmatchcase(ref, p) for p in patterns))
        _require(ref == _MAIN or re.fullmatch(
            r"refs/heads/aios/(?:artifacts|failure-artifacts|review|failure|review-decision|repair|"
            r"repair-supersession|remediation)/RUN-[A-Za-z0-9_./-]{1,128}", ref) is not None)
    return values


class _Sources(GitSources):
    """Existing identity reader over immutable pins, without object acquisition."""

    def __init__(self, path, git, remote, pins):
        super().__init__(path, git, remote)
        self.pins, self.documents, self.entries = dict(pins), {}, {}

    def refs(self, *patterns):
        return {ref: sha for ref, sha in self.pins.items()
                if any(fnmatchcase(ref, p) for p in patterns)}

    def fetch(self, sha):
        _require(isinstance(sha, str) and _SHA.fullmatch(sha) is not None)
        if sha not in self._fetched:
            self.git(self.path, "cat-file", "-e", sha + "^{commit}")
            self._fetched.add(sha)

    def acquire(self, *shas):
        for sha in shas:
            self.fetch(sha)

    def _entry(self, sha, name):
        allowed = {".ai/transport/run.json", ".ai/transport/result.json", ".ai/transport/failure.json",
                   ".ai/transport/repair.json", ".ai/transport/repair-supersession.json"}
        _require(name in allowed or re.fullmatch(
            r"\.ai/(?:tasks/TASK-[A-Za-z0-9_-]{1,90}|reviews/REVIEW-[A-Za-z0-9._-]{1,96}|"
            r"remediations/REMEDIATION-[A-Za-z0-9_-]{1,128})\.ya?ml", name) is not None)
        key = sha, name
        if key not in self.entries:
            self.fetch(sha)
            raw = self.git(self.path, "ls-tree", "-l", sha, "--", name)
            if not raw:
                self.entries[key] = None
            else:
                match = re.fullmatch(r"100644 blob ([0-9a-f]{40})\s+(\d+)\t" + re.escape(name), raw)
                _require(match is not None)
                _require(0 < int(match[2]) <= MAX_DOCUMENT_BYTES, "LIMIT_EXHAUSTED")
                self.entries[key] = match[1]
        return self.entries[key]

    def document(self, sha, name):
        key = sha, name
        if key not in self.documents:
            _require(self._entry(sha, name) is not None, "SOURCE_UNAVAILABLE")
            raw = self.git(self.path, "show", sha + ":" + name)
            # Reject YAML aliases and excess nesting before allocating a graph.
            depth = nodes = 0
            for event in yaml.parse(raw, Loader=yaml.SafeLoader):
                nodes += 1
                _require(nodes <= MAX_NODES, "LIMIT_EXHAUSTED")
                _require(not isinstance(event, yaml.AliasEvent))
                if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                    depth += 1
                    _require(depth <= MAX_DEPTH, "LIMIT_EXHAUSTED")
                elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                    depth -= 1
            value = dict(_load_yaml(raw, "canonical experience"))
            _shape(value)
            self.documents[key] = value
        return self.documents[key]

    def optional_document(self, sha, name):
        return self.document(sha, name) if self._entry(sha, name) else None

    def binding(self, sha, name, *, ref=None, pointer=""):
        self.document(sha, name)
        return dict(commit_sha=sha, blob_sha=self._entry(sha, name), path=name,
                    ref=ref, pointer=pointer)


@dataclass(frozen=True)
class ExperienceCut:
    """Opaque authenticated observation. Only capture_experience_cut issues cuts."""

    main_sha: str
    as_of_sha: str
    task_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    task_only: bool
    refs: tuple[tuple[str, str], ...]
    _transport: tuple[str, str] = field(repr=False)
    _seal: object = field(repr=False, compare=False)
    _integrity: str = field(repr=False)

    def _material(self):
        return dict(main_sha=self.main_sha, as_of_sha=self.as_of_sha, task_ids=self.task_ids,
                    run_ids=self.run_ids, task_only=self.task_only, refs=self.refs,
                    repository_id=_digest(self._transport[1]))

    def as_dict(self):
        _require(self._seal is _SEAL and _digest(self._material()) == self._integrity, "CUT_INVALID")
        return dict(format="AIOS_CAA_SOURCE_CUT", version=1, digest=self._integrity,
                    mode="TASK_ONLY" if self.task_only else "CAPTURED_REFS",
                    **self._material())


def _selection(task_ids, task_range, run_ids):
    _require(isinstance(task_ids, (tuple, list)) and isinstance(run_ids, (tuple, list)), "SELECTION_INVALID")
    _require(len(task_ids) <= MAX_TASKS and len(run_ids) <= MAX_EPISODES, "LIMIT_EXHAUSTED")
    if task_range is not None:
        _require(not task_ids and isinstance(task_range, (tuple, list)) and len(task_range) == 2,
                 "SELECTION_INVALID")
        start, end = task_range
        _require(type(start) is int and type(end) is int and 0 <= start <= end <= 99999999,
                 "SELECTION_INVALID")
        _require(end - start < MAX_TASKS, "LIMIT_EXHAUSTED")
        task_ids = tuple(f"TASK-{n}" for n in range(start, end + 1))
    _require(bool(task_ids) and all(isinstance(t, str) and _TASK.fullmatch(t) for t in task_ids),
             "SELECTION_INVALID")
    _require(len(set(task_ids)) == len(task_ids), "SELECTION_INVALID")
    for run in run_ids:
        _require(isinstance(run, str) and _RUN.fullmatch(run) is not None
                 and any(re.fullmatch(re.escape(task_run_prefix(t)) + r"\d{3,8}", run)
                         for t in task_ids), "SELECTION_INVALID")
    _require(len(set(run_ids)) == len(run_ids), "SELECTION_INVALID")
    return tuple(sorted(task_ids)), tuple(sorted(run_ids))


def capture_experience_cut(
    repo: str | Path, *, expected_main_sha: str, task_ids: Sequence[str] = (),
    task_range: tuple[int, int] | None = None, run_ids: Sequence[str] = (),
    task_only: bool = False, as_of_sha: str | None = None,
) -> ExperienceCut:
    """Authenticate a current finite ref cut, or an explicitly TASK-only past cut.

    A historical main SHA does not attest historical remote artifact availability.
    Consequently historical full views require a cut actually captured earlier;
    arbitrary caller-supplied ref manifests and future artifacts are never inputs.
    """
    try:
        tasks, runs = _selection(task_ids, task_range, run_ids)
        _require(type(task_only) is bool and not (task_only and runs), "SELECTION_INVALID")
        _require(isinstance(expected_main_sha, str) and _SHA.fullmatch(expected_main_sha) is not None,
                 "SELECTION_INVALID")
        _require(as_of_sha is None or task_only, "HISTORICAL_CUT_REQUIRES_TASK_ONLY")
        at = expected_main_sha if as_of_sha is None else as_of_sha
        _require(isinstance(at, str) and _SHA.fullmatch(at) is not None, "SELECTION_INVALID")
        root, git = Path(repo).resolve(), _GitBudget()
        transport = _transport(root, git)
        live = GitSources(root, git, transport[0])
        patterns = (_MAIN,) if task_only else _patterns(tasks)
        pins = _refs(live, patterns)
        _require(pins.get(_MAIN) == expected_main_sha, "MAIN_MOVED")
        sources = _Sources(root, git, transport[0], pins)
        _require(sources.included(at, expected_main_sha), "AS_OF_SOURCE_UNPROVEN")
        for task_id in tasks:
            task = validate_task(sources.document(at, f".ai/tasks/{task_id}.yaml"))
            _require(task.task_id == task_id, "TASK_SOURCE_MISMATCH")
        _episode_selections(tasks, runs, pins, task_only)
        _require(_refs(live, patterns) == pins and _transport(root, git) == transport, "SOURCE_MOVED")
        cut = ExperienceCut(expected_main_sha, at, tasks, runs, task_only, tuple(sorted(pins.items())),
                            transport, _SEAL, "")
        _require(time.monotonic() < git.deadline, "LIMIT_EXHAUSTED")
        return ExperienceCut(expected_main_sha, at, tasks, runs, task_only, cut.refs, transport,
                             _SEAL, _digest(cut._material()))
    except ExperienceError:
        raise
    except Exception:
        raise ExperienceError("SOURCE_UNAVAILABLE_OR_INVALID") from None


def _episode_selections(tasks, runs, pins, task_only):
    selections = []
    for task_id in tasks:
        prefix = task_run_prefix(task_id)
        known = sorted({ref.removeprefix(_ROOT).split("/", 1)[1]
                        for ref in pins if ref.startswith(_ROOT)
                        and ref.removeprefix(_ROOT).split("/", 1)[0] in {
                            "artifacts", "failure-artifacts", "review", "failure", "review-decision"}
                        and re.fullmatch(re.escape(prefix) + r"\d{3,8}", ref.split("/")[-1])})
        selected = [run for run in runs if run.startswith(prefix)] if runs else known
        if task_only or not selected:
            selections.append((task_id, None))
        else:
            selections.extend((task_id, run) for run in selected)
    _require(len(selections) <= MAX_EPISODES, "LIMIT_EXHAUSTED")
    return selections


def _observed(facts, name, value, *sources):
    facts.append(dict(name=name, status="OBSERVED", value=value, sources=list(sources)))


def _unknown(episode, name, reason="NOT_OBSERVED_AT_CUT"):
    episode["unknown"].append(dict(name=name, status="UNKNOWN", reason=reason))


def _ids(values, pattern):
    _require(isinstance(values, (tuple, list)) and len(values) <= 32, "LIMIT_EXHAUSTED")
    _require(all(isinstance(x, str) and re.fullmatch(pattern, x) for x in values))
    return list(values)


def _repair_facts(sources, run_id, head, task, failure, facts):
    from .publication import _validate_repair_authorization
    candidates = []
    root_ref = _ROOT + "repair/" + run_id
    prefix = _ROOT + "repair-supersession/" + run_id + "/"
    refs = sources.refs(root_ref, prefix + "*")
    _require(len(refs) <= 8, "LIMIT_EXHAUSTED")
    if not refs:
        return False
    for ref, sha in sorted(refs.items()):
        data = sources.document(sha, ".ai/transport/repair.json")
        _validate_repair_authorization(data, failed_run_id=run_id, failed_head_sha=head,
            task=task, failed_changed_files=set(failure["candidate"]["changed_files"]))
        _require(re.fullmatch(r"REPAIR-[A-Za-z0-9_-]{1,96}", data["repair_id"]) is not None)
        predecessor = failure_sha = None
        revision = 1
        if ref != root_ref:
            suffix = ref.removeprefix(prefix)
            _require(suffix.isdigit() and 2 <= int(suffix) <= 8)
            revision = int(suffix)
            metadata = sources.document(sha, ".ai/transport/repair-supersession.json")
            predecessor, failure_sha = _decode_repair_supersession(
                _encode(metadata).encode(), run_id=run_id, revision=revision)
            _require(sources.git(sources.path, "rev-list", "--parents", "--max-count=1", sha).split()
                     == [sha, predecessor])
        candidates.append(RemoteRepairAuthorization(run_id, ref, sha, revision,
            _encode(data).encode(), predecessor, failure_sha))
    current = _select_current_repair_authorization(candidates,
        failure_artifacts_sha=sources.pins.get(_ROOT + "failure-artifacts/" + run_id))
    for candidate in sorted(candidates, key=lambda c: c.revision):
        data = sources.document(candidate.commit_sha, ".ai/transport/repair.json")
        bindings = [sources.binding(candidate.commit_sha, ".ai/transport/repair.json", ref=candidate.ref)]
        if candidate.revision > 1:
            bindings.append(sources.binding(candidate.commit_sha, ".ai/transport/repair-supersession.json",
                                            ref=candidate.ref))
        _observed(facts, "repair_authorization", dict(repair_id=data["repair_id"], action=data["action"],
            revision=candidate.revision, failed_run_id=run_id, failed_head_sha=head,
            selected_at_cut=candidate.commit_sha == current.commit_sha),
            *bindings)
    return True


def _execution_correction_facts(sources, sha, ref, run_document, run, task, facts):
    """Project a correction only with its exact canonical authorization pin."""
    repair = sources.optional_document(sha, ".ai/transport/repair.json")
    if repair is not None:
        from .publication import _validate_repair_authorization
        failed_id, failed_head = repair["failed_run_id"], repair["failed_head_sha"]
        _require(isinstance(failed_id, str) and _RUN.fullmatch(failed_id) is not None)
        auth_sha = repair.get("repair_authorization_sha")
        auth_refs = sources.refs(_ROOT + "repair/" + failed_id,
                                 _ROOT + "repair-supersession/" + failed_id + "/*")
        auth_ref = next((r for r, s in auth_refs.items() if s == auth_sha), None)
        _require(auth_ref is not None, "CORRECTION_AUTHORIZATION_UNPROVEN")
        authorization = sources.document(auth_sha, ".ai/transport/repair.json")
        _require(isinstance(authorization.get("repair_id"), str) and re.fullmatch(
            r"REPAIR-[A-Za-z0-9_-]{1,96}", authorization["repair_id"]) is not None)
        failure_ref = _ROOT + "failure-artifacts/" + failed_id
        failure_sha = sources.pins.get(failure_ref)
        _require(failure_sha is not None, "CORRECTION_FAILURE_UNPROVEN")
        failure = failure_document(_encode(sources.document(failure_sha, ".ai/transport/failure.json")).encode())
        _require(repair["run"] == run_document and repair["repair"] == authorization
                 and repair["failed_head_sha"] == run.base_sha
                 and failure["failed_head_sha"] == failed_head
                 and failure["task"] == dict(id=task.task_id, revision=task.revision))
        _validate_repair_authorization(authorization, failed_run_id=failed_id, failed_head_sha=failed_head,
            task=task, failed_changed_files=set(failure["candidate"]["changed_files"]))
        authorizations = []
        _require(_repair_facts(sources, failed_id, failed_head, task, failure, authorizations),
                 "CORRECTION_AUTHORIZATION_UNPROVEN")
        auth_fact = next(fact for fact in authorizations if fact["sources"][0]["commit_sha"] == auth_sha)
        _observed(facts, "repair_execution", dict(failed_run_id=failed_id, failed_head_sha=failed_head,
            repair_id=authorization["repair_id"], action=authorization["action"], authorization_sha=auth_sha),
            sources.binding(sha, ".ai/transport/repair.json", ref=ref),
            *auth_fact["sources"],
            sources.binding(failure_sha, ".ai/transport/failure.json", ref=failure_ref))
        return True
    if run_document.get("kind") == "REMEDIATION":
        execution = run_document["execution"]
        review_id = execution["review_id"]
        source_id = "RUN-" + review_id.removeprefix("REVIEW-")
        finding = execution["remediation"]["finding_id"]
        _ids([finding], r"[A-Za-z0-9_-]{1,64}")
        correction_ref = _ROOT + f"remediation/{source_id}-{finding}"
        correction_sha = sources.pins.get(correction_ref)
        _require(correction_sha is not None, "CORRECTION_AUTHORIZATION_UNPROVEN")
        identity, review = sources.review(source_id)
        raw = sources.remediation(correction_sha, source_id + "-" + finding, identity["decision_sha"])
        _require(raw == execution["remediation"]
                 and run_document.get("remediation_authorization_sha", correction_sha) == correction_sha)
        remediation = validate_remediation(review=parse_review(_encode(review)),
                                           remediation=parse_remediation(_encode(raw)), task=task)
        _observed(facts, "remediation_execution", dict(source_run_id=source_id, finding_id=finding,
            reviewed_sha=remediation.reviewed_sha, action=remediation.action),
            sources.binding(sha, ".ai/transport/run.json", ref=ref),
            sources.binding(correction_sha, f".ai/remediations/REMEDIATION-{source_id}-{finding}.yaml",
                            ref=correction_ref))
        return True
    return False


def _episode(sources, cut, task_id, run_id, canonical_task, include_semantics=False):
    episode = dict(selection=dict(task_id=task_id, run_id=run_id), observed=[], inferred=[], unknown=[])
    if include_semantics:
        episode["semantic_context"] = _task_semantics(canonical_task, sources.binding(
            cut.as_of_sha, f".ai/tasks/{task_id}.yaml", ref=_MAIN), cut)
    facts = episode["observed"]
    _unknown(episode, "technical_root_cause", "NO_SEMANTIC_CAUSE_INFERENCE")
    if run_id is None:
        for name in ("run", "terminal", "review", "publication", "correction"):
            _unknown(episode, name)
        return episode
    result_ref, failure_ref = (_ROOT + ns + "/" + run_id for ns in ("artifacts", "failure-artifacts"))
    success, failed = sources.pins.get(result_ref), sources.pins.get(failure_ref)
    _require(not (success and failed), "TERMINAL_CONFLICT")
    if not (success or failed):
        # A candidate/review filename alone is not an authenticated RUN or RESULT.
        _require(not any(sources.pins.get(_ROOT + ns + "/" + run_id)
                         for ns in ("review", "failure", "review-decision")), "TERMINAL_INCOMPLETE")
        for name in ("run", "terminal", "review", "publication", "correction"):
            _unknown(episode, name)
        return episode
    sha, ref, kind = (success, result_ref, "RESULT") if success else (failed, failure_ref, "FAILURE")
    run_document = sources.document(sha, ".ai/transport/run.json")
    path = ".ai/transport/" + ("result.json" if success else "failure.json")
    terminal = sources.document(sha, path)
    revision, executor, base, head = _bind_performance_terminal_identity(
        _encode(run_document).encode(), _encode(terminal).encode(), ref=ref,
        run_id=run_id, task_id=task_id, terminal_kind=kind)
    # A correction may lawfully start at an unpublished failed/reviewed head.
    # Its base must already be on main ancestry or an exact candidate pin in
    # this authenticated cut; never consult a later ref to supply that source.
    candidate_bases = {value for key, value in sources.pins.items()
                       if key.startswith((_ROOT + "review/", _ROOT + "failure/"))}
    _require(sources.included(base, cut.as_of_sha) or base in candidate_bases, "BASE_SOURCE_UNPROVEN")
    base_task_path = f".ai/tasks/{task_id}.yaml"
    task = validate_task(sources.document(base, base_task_path))
    _require(task.task_id == task_id and task.revision == revision, "TASK_REVISION_MISMATCH")
    _require(task.revision != canonical_task.revision or task == canonical_task, "IMMUTABLE_TASK_DRIFT")
    _require(validate_task(sources.document(head, base_task_path)) == task, "IMMUTABLE_TASK_DRIFT")
    event = attention(RESULT if success else FAILURE, dict(run_id=run_id, artifact_sha=sha))
    _require(resolve_return_affinity(event.event_id, sources) == task.return_affinity, "TERMINAL_UNPROVEN")
    run_data = run_document["execution"]["run"] if run_document.get("kind") == "REMEDIATION" else run_document
    run = Run(run_id, RunTaskReference(task_id, revision), executor, base, run_data["workspace"],
              run_data.get("head_sha"), run_data["status"], document_affinity(run_data))
    task_source = sources.binding(base, base_task_path)
    if include_semantics:
        # The RUN's immutable revision, not a newer TASK on current main.
        episode["semantic_context"] = _task_semantics(task, task_source, cut)
    run_source = sources.binding(sha, ".ai/transport/run.json", ref=ref)
    terminal_source = sources.binding(sha, path, ref=ref)
    _observed(facts, "task", dict(id=task_id, revision=revision), task_source, run_source)
    _observed(facts, "run", dict(run_id=run_id, base_sha=base, head_sha=run.head_sha,
              persisted_status=run.status), run_source)
    _observed(facts, "terminal", dict(kind=kind, subject_sha=head), terminal_source)
    execution_correction = _execution_correction_facts(sources, sha, ref, run_document, run, task, facts)

    if failed:
        failure = failure_document(_encode(terminal).encode())
        context = failure_context(failure, artifact_sha=sha,
                                  candidate_available=sources.pins.get(_ROOT + "failure/" + run_id) == head)
        if context:
            detail = context["safe_detail"]
            _observed(facts, "failure_shape", dict(phase=context["phase"],
                reported_cause=detail["cause"], detail_availability=detail["availability"],
                missing_acceptance_ids=_ids(detail["missing_acceptance_ids"], r"AC[A-Za-z0-9_-]{1,64}")),
                terminal_source)
            _require(len(context["verification"]) <= 32 and not context["verification_truncated"], "LIMIT_EXHAUSTED")
            by_index = {item["index"]: item for item in detail["verification"]}
            for index, outcome in enumerate(context["verification"]):
                observed = by_index.get(index, {})
                bindings = [dict(terminal_source, pointer=f"/error/verification/{index}")]
                if observed and "public_projection" in terminal:
                    detail_index = next(i for i, item in enumerate(detail["verification"]) if item["index"] == index)
                    bindings.append(dict(terminal_source, pointer=f"/error/safe_detail/verification/{detail_index}"))
                _observed(facts, "verification", dict(slot=f"V{index + 1:03}",
                    evidence_id=outcome.get("evidence_id"), exit_code=outcome["exit_code"],
                    evidence_status="OBSERVED" if outcome.get("evidence_id") else "UNKNOWN",
                    command_digest=_digest(outcome["command"]),
                    summary_status=observed.get("summary_status", "UNKNOWN"),
                    observed_node_count=len(observed.get("failed_nodes", [])),
                    node_availability=observed.get("availability", "UNKNOWN"),
                    failure_count=observed.get("failure_count"),
                    nodes_truncated=observed.get("nodes_truncated", False)),
                    *bindings)
            if not context["verification"]:
                _unknown(episode, "verification", "NO_VERIFICATION_OBSERVED")
        else:
            _unknown(episode, "failure_shape", "LEGACY_DIAGNOSTIC_UNAVAILABLE")
            _unknown(episode, "verification")
        if not _repair_facts(sources, run_id, head, task, failure, facts) and not execution_correction:
            _unknown(episode, "correction")
        for name in ("result", "review", "publication"):
            _unknown(episode, name, "FAILURE_DOES_NOT_ESTABLISH_" + name.upper())
        return episode

    result = validate_result(terminal["result"])
    evidence = tuple(validate_evidence(item) for item in terminal["evidence"])
    validate_result_package(task=task, run=run, result=result, evidence=evidence)
    _require(len(result.claims) <= 32 and len(evidence) <= 32, "LIMIT_EXHAUSTED")
    covered = sorted({a for claim in result.claims for a in claim.satisfies})
    _require(not result.unresolved and set(covered) == {a.id for a in task.acceptance}, "RESULT_NOT_COMPLETE")
    _observed(facts, "result", dict(claim_count=len(result.claims), unresolved_count=len(result.unresolved),
              claimed_acceptance_ids=_ids(covered, r"AC[A-Za-z0-9_-]{1,64}")), terminal_source)
    for index, item in enumerate(evidence):
        # No command, summary, claim text, raw locator or verification detail text.
        public_id = item.evidence_id if re.fullmatch(re.escape(run_id) + r"-V\d{3}", item.evidence_id) else None
        _observed(facts, "evidence", dict(evidence_id=public_id, record_digest=_digest(terminal["evidence"][index]),
            run_id=item.run_id, subject_sha=item.subject_sha, exit_code=item.result.exit_code,
            command_digest=_digest(item.source.command)), dict(terminal_source, pointer=f"/evidence/{index}"))
    if not evidence:
        _unknown(episode, "evidence")
    decision_ref = _ROOT + "review-decision/" + run_id
    decision = sources.pins.get(decision_ref)
    if decision:
        identity, review_data = sources.review(run_id, decision)
        review = parse_review(_encode(review_data))
        validate_review(task=task, result=result, review=review)
        _require(identity["artifact_sha"] == sha and review.reviewed_sha == head, "REVIEW_MISMATCH")
        review_path = f".ai/reviews/{review.review_id}.yaml"
        if sources.optional_document(decision, review_path) is None:
            review_path = f".ai/reviews/{review.review_id}.yml"
        review_source = sources.binding(decision, review_path, ref=decision_ref)
        _observed(facts, "review", dict(review_id=review.review_id, reviewed_sha=head,
                  mode=review.mode, verdict=review.verdict), review_source, terminal_source)
        if review.verdict == "PASS":
            included = sources.included(head, cut.as_of_sha)
            _observed(facts, "publication", dict(state="MAIN_INCLUSION" if included else "NOT_INCLUDED",
                reviewed_source_sha=head, as_of_main_sha=cut.as_of_sha), review_source,
                dict(commit_sha=cut.as_of_sha, ref=_MAIN, path=None, blob_sha=None, pointer=""))
        else:
            _unknown(episode, "publication", "REVIEW_PASS_NOT_OBSERVED")
        corrections = sources.refs(_ROOT + "remediation/" + run_id + "-*")
        _require(len(corrections) <= 8, "LIMIT_EXHAUSTED")
        for correction_ref, correction_sha in sorted(corrections.items()):
            finding_id = correction_ref.removeprefix(_ROOT + "remediation/" + run_id + "-")
            _require(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", finding_id) is not None)
            raw = sources.remediation(correction_sha, run_id + "-" + finding_id, decision)
            remediation = validate_remediation(review=review, remediation=parse_remediation(_encode(raw)), task=task)
            _require(review.verdict == "CHANGES_REQUIRED" and remediation.finding_id == finding_id)
            name = f".ai/remediations/REMEDIATION-{run_id}-{finding_id}.yaml"
            _observed(facts, "remediation_authorization", dict(finding_id=finding_id,
                reviewed_sha=head, action=remediation.action), sources.binding(correction_sha, name, ref=correction_ref),
                review_source)
        if not corrections and not execution_correction:
            _unknown(episode, "correction")
    else:
        for name in ("review", "publication"):
            _unknown(episode, name)
        if not execution_correction:
            _unknown(episode, "correction")
    return episode


def reconstruct_episodes(repo: str | Path, *, cut: ExperienceCut,
                         include_semantics: bool = False) -> dict[str, Any]:
    """Return finite provider-neutral facts, or a contained reader error.

    No verifier, reducer, provider, audit, admission, correction or publication is
    invoked. Replays may observe a later canonical main descendant but never use
    its new facts. Pins removed/moved since capture fail instead of falling back.
    """
    try:
        _require(type(include_semantics) is bool, "SELECTION_INVALID")
        _require(type(cut) is ExperienceCut, "CUT_INVALID")
        portable_cut = cut.as_dict()
        root, git = Path(repo).resolve(), _GitBudget()
        _require(_transport(root, git) == cut._transport, "REPOSITORY_MISMATCH")
        live = GitSources(root, git, cut._transport[0])
        patterns = tuple(sorted(set(dict(cut.refs)) | {_MAIN}))
        before = _refs(live, patterns)
        _require(all(before.get(ref) == sha for ref, sha in cut.refs if ref != _MAIN), "SOURCE_MOVED")
        _require(_MAIN in before, "MAIN_UNAVAILABLE")
        sources = _Sources(root, git, cut._transport[0], dict(cut.refs))
        _require(sources.included(cut.main_sha, before[_MAIN])
                 and sources.included(cut.as_of_sha, cut.main_sha), "MAIN_REWRITTEN")
        tasks, task_facts = {}, []
        for task_id in cut.task_ids:
            name = f".ai/tasks/{task_id}.yaml"
            task = validate_task(sources.document(cut.as_of_sha, name))
            _require(task.task_id == task_id, "TASK_SOURCE_MISMATCH")
            tasks[task_id] = task
            facts = []
            source = sources.binding(cut.as_of_sha, name, ref=_MAIN)
            _observed(facts, "task", dict(id=task.task_id, revision=task.revision), source)
            _observed(facts, "acceptance_ids", _ids([a.id for a in task.acceptance], r"AC[A-Za-z0-9_-]{1,64}"), source)
            task_facts.append(dict(task_id=task_id, observed=facts, inferred=[], unknown=[]))
        episodes = [_episode(sources, cut, task_id, run_id, tasks[task_id], include_semantics)
                    for task_id, run_id in _episode_selections(cut.task_ids, cut.run_ids, sources.pins, cut.task_only)]
        _require(_refs(live, patterns) == before and _transport(root, git) == cut._transport, "SOURCE_MOVED")
        body = dict(format="AIOS_CAA_EXPERIENCE", version=1, source_cut=portable_cut,
                    tasks=task_facts, episodes=episodes, authority="NONE", source_text_is_instructions=False,
                    run_created=False, executor_invoked=False, verification_invoked=False, state_mutated=False)
        _shape(body, max_depth=16)
        limit = MAX_SEMANTIC_OUTPUT_BYTES if include_semantics else MAX_OUTPUT_BYTES
        _require(len(_encode(body).encode("ascii")) <= limit, "LIMIT_EXHAUSTED")
        _require(time.monotonic() < git.deadline, "LIMIT_EXHAUSTED")
        return body
    except ExperienceError:
        raise
    except Exception:
        raise ExperienceError("SOURCE_UNAVAILABLE_OR_INVALID") from None
