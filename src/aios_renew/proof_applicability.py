"""VP-03A: read-only, Git-authenticated factual proof applicability.

The authority catalog pin is an OUT-OF-BAND trusted input, never a request field.
Git authenticates content/lineage, not the legitimacy of a trust root or review.
No scheduler, execution permission, checkpoint, persistence or target evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from .artifacts import validate_evidence, validate_result
from .intra_run_proof_contract import DIMENSIONS as DECLARED_DIMENSIONS
from .proof_coverage_contract import (
    BR_GATES, ProofCoverageError, proof_mapping_digest, validate_proof_mapping,
)
from .run import Run, RunTaskReference
from .task import validate_task
from .verification_contract import validate_observation


SCHEMA = "proof-applicability-v1"
CATALOG_SCHEMA = "proof-applicability-authority-v1"
REVIEW_SCHEMA = "proof-applicability-review-v1"
WITNESS_SCHEMA = "proof-applicability-witness-v1"
CONTEXT_SCHEMA = "proof-dependency-context-v1"
# VP-02B's complete dimensions are retained; policy is explicitly distinguished.
DIMENSIONS = DECLARED_DIMENSIONS + ("policy",)
MAX_RECORD_BYTES = 1_048_576
MAX_TOTAL_BYTES = 8_388_608
MAX_NODES = 40_000
MAX_DEPTH = 20
MAX_PATHS = 64
MAX_OBJECTS = 256
MAX_GIT_CALLS = 512
MAX_ANCESTORS = 256
GIT_TIMEOUT = 5
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z", re.ASCII)
_FIELDS = {
    "test_code": ("test_ref",), "fixtures": ("fixture_refs",),
    "helpers": ("shared_state_ref",), "shared_state": ("shared_state_ref",),
    "toolchain": ("toolchain_ref",), "environment": ("environment_ref",),
    "profile": ("profile_ref",), "worker_mode": ("worker_mode", "workers"),
    "concurrency": ("concurrency_ref",), "ordering": ("ordering_ref",),
    "collection": ("collection_ref",), "integration": ("integration_ref",),
    "repetition": ("repetition_ref",),
    "population": ("population_ref", "population_size"),
    "evidence_schema": ("evidence_kind", "evidence_schema"), "policy": (),
}
_RECORD_NAMES = (
    "task", "run", "result", "evidence", "raw", "source_contract", "source_mapping",
    "target_contract", "target_mapping", "review", "witness",
)


class ApplicabilityInputError(ValueError):
    """Unsupported, malformed or unbounded decoded input."""


class State(str, Enum):
    VALID = "VALID"
    INVALIDATED = "INVALIDATED"
    UNKNOWN = "UNKNOWN"


class ReasonCode(str, Enum):
    AUTHORITY_UNAVAILABLE = "AUTHORITY_UNAVAILABLE"
    AUTHORITY_NOT_CURRENT = "AUTHORITY_NOT_CURRENT"
    RESOURCE_BOUND = "RESOURCE_BOUND"
    GIT_OBJECT_UNAVAILABLE = "GIT_OBJECT_UNAVAILABLE"
    GIT_OBJECT_CORRUPT = "GIT_OBJECT_CORRUPT"
    LINEAGE_CONFLICT = "LINEAGE_CONFLICT"
    RECORD_CORRUPT = "RECORD_CORRUPT"
    SOURCE_BINDING_CONFLICT = "SOURCE_BINDING_CONFLICT"
    SOURCE_PROOF_UNKNOWN = "SOURCE_PROOF_UNKNOWN"
    MAPPING_CONFLICT = "MAPPING_CONFLICT"
    WITNESS_CONFLICT = "WITNESS_CONFLICT"
    DEPENDENCY_INCOMPLETE = "DEPENDENCY_INCOMPLETE"
    FOOTPRINT_UNKNOWN = "FOOTPRINT_UNKNOWN"
    DEPENDENCY_CHANGED = "DEPENDENCY_CHANGED"


@dataclass(frozen=True)
class Reason:
    code: ReasonCode
    dimension: str | None = None


@dataclass(frozen=True)
class RecordIdentity:
    commit_sha: str
    path: str
    blob_sha: str
    sha256: str


@dataclass(frozen=True)
class ProofPin:
    id: str
    revision: int
    digest: str


@dataclass(frozen=True)
class ApplicabilityRequest:
    schema: str
    repository_id: str
    evidence: RecordIdentity
    witness: RecordIdentity
    source_proof_id: str
    target_candidate_sha: str
    target_obligation: ProofPin


@dataclass(frozen=True)
class ReadOnlyAuthority:
    """Trusted repository location + independently delivered catalog identity.

    Construct only at a trusted boundary, from canonical source authority. This
    is dependency injection, NOT an authentication/approval API for a caller.
    A caller able to choose the authority can invent an entire repository.
    """

    repository: Path
    catalog: RecordIdentity


@dataclass(frozen=True)
class OriginalObservation:
    task: RecordIdentity
    task_id: str
    task_revision: int
    task_envelope_digest: str
    run: RecordIdentity
    run_id: str
    result: RecordIdentity
    evidence: RecordIdentity
    evidence_id: str
    raw: RecordIdentity
    subject_sha: str
    tree_sha: str
    command: str
    exit_code: int
    outcome: str
    source_contract: ProofPin
    source_mapping: ProofPin
    source_proof_id: str
    source_proof_digest: str


@dataclass(frozen=True)
class ApplicabilityWitness:
    record: RecordIdentity
    review: RecordIdentity
    source_subject_sha: str
    target_candidate_sha: str
    target_tree_sha: str
    base_sha: str
    target_contract: ProofPin
    target_mapping: ProofPin
    target_obligation: ProofPin
    dependency_digests: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class ApplicabilityResult:
    schema: str
    state: State
    reasons: tuple[Reason, ...]
    source: OriginalObservation | None
    witness: ApplicabilityWitness | None
    applicable_obligation: ProofPin | None
    authority_authenticated: bool
    # Factual applicability never grants discharge/execution/reuse authority.
    acceptance_discharge_authorized: bool = False
    evidence_reuse_authorized: bool = False
    verification_execution_authorized: bool = False
    target_execution_evidence_created: bool = False
    canonical_checkpoint_created: bool = False
    activation: str = "NOT_ACTIVATED"
    base_replay_required_gates: tuple[str, ...] = BR_GATES


def _object(value: object, fields: set[str]) -> dict:
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) != fields):
        raise ApplicabilityInputError("exact decoded fields required")
    return value


def _match(value: object, pattern: re.Pattern) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ApplicabilityInputError("invalid immutable identity")
    return value


def _path(value: object) -> str:
    path = _match(value, _PATH)
    if len(path) > 512 or any(part in (".", "..") for part in path.split("/")):
        raise ApplicabilityInputError("exact bounded relative path required")
    return path


def _list(value: object, limit: int) -> list:
    if type(value) is not list or len(value) > limit:
        raise ApplicabilityInputError("bounded decoded list required")
    return value


def _bounded(value: object) -> None:
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ApplicabilityInputError("decoded material exceeds bounds")
        if type(item) is dict:
            if len(item) > MAX_NODES or any(type(k) is not str for k in item):
                raise ApplicabilityInputError("plain string-keyed object required")
            pending.extend((k, depth + 1) for k in item)
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            if len(item) > MAX_NODES:
                raise ApplicabilityInputError("decoded list exceeds bounds")
            pending.extend((v, depth + 1) for v in item)
        elif type(item) is str:
            if len(item) > MAX_RECORD_BYTES or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise ApplicabilityInputError("invalid or unbounded string")
        elif item is not None and type(item) not in (int, bool):
            raise ApplicabilityInputError("only plain decoded JSON values supported")
        elif type(item) is int and abs(item) > 2_147_483_647:
            raise ApplicabilityInputError("integer exceeds bounds")


def canonical_digest(value: object) -> str:
    """Content identity only. Does not authenticate source/review authority."""
    _bounded(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(encoded) > MAX_RECORD_BYTES:
        raise ApplicabilityInputError("record exceeds byte bound")
    return hashlib.sha256(encoded).hexdigest()


def decode_record_identity(value: object) -> RecordIdentity:
    data = _object(value, set(RecordIdentity.__dataclass_fields__))
    return RecordIdentity(_match(data["commit_sha"], _SHA), _path(data["path"]),
                          _match(data["blob_sha"], _SHA), _match(data["sha256"], _DIGEST))


def _pin(value: object) -> ProofPin:
    data = _object(value, set(ProofPin.__dataclass_fields__))
    revision = data["revision"]
    if type(revision) is not int or not 1 <= revision <= 2_147_483_647:
        raise ApplicabilityInputError("positive bounded revision required")
    return ProofPin(_match(data["id"], _ID), revision, _match(data["digest"], _DIGEST))


def decode_request(value: object) -> ApplicabilityRequest:
    _bounded(value)
    data = _object(value, set(ApplicabilityRequest.__dataclass_fields__))
    if data["schema"] != SCHEMA:
        raise ApplicabilityInputError("unsupported applicability version")
    return ApplicabilityRequest(SCHEMA, _match(data["repository_id"], _ID),
        decode_record_identity(data["evidence"]), decode_record_identity(data["witness"]),
        _match(data["source_proof_id"], _ID), _match(data["target_candidate_sha"], _SHA),
        _pin(data["target_obligation"]))


def _record_values(value: RecordIdentity) -> dict:
    if type(value) is not RecordIdentity:
        raise ApplicabilityInputError("decoded record identity required")
    return {name: getattr(value, name) for name in RecordIdentity.__dataclass_fields__}


def _request_values(value: ApplicabilityRequest) -> dict:
    if type(value.target_obligation) is not ProofPin:
        raise ApplicabilityInputError("decoded obligation identity required")
    return {"schema": value.schema, "repository_id": value.repository_id,
        "evidence": _record_values(value.evidence), "witness": _record_values(value.witness),
        "source_proof_id": value.source_proof_id, "target_candidate_sha": value.target_candidate_sha,
        "target_obligation": {name: getattr(value.target_obligation, name) for name in ProofPin.__dataclass_fields__}}


class _Unknown(Exception):
    def __init__(self, code: ReasonCode, dimension: str | None = None):
        self.reason = Reason(code, dimension)


def _require(condition: bool, code: ReasonCode) -> None:
    if not condition:
        raise _Unknown(code)


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ApplicabilityInputError("duplicate JSON key")
        result[key] = value
    return result


class _Git:
    """Per-evaluation bounded reads. No refs supplied by the request are resolved."""

    def __init__(self, repository: Path):
        self.repository = repository
        self.objects: dict[str, tuple[str, bytes]] = {}
        self.commits: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.calls = 0
        self.bytes = 0
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        self.env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
                        GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull)

    def _read(self, args: list[str], oid: str | None = None) -> bytes:
        self.calls += 1
        if self.calls > MAX_GIT_CALLS:
            raise _Unknown(ReasonCode.RESOURCE_BOUND)
        try:
            result = subprocess.run(
                ["git", "--no-replace-objects", "-c", "core.useReplaceRefs=false",
                 "-c", "core.commitGraph=false", "-c", "gc.auto=0",
                 "-C", str(self.repository), *args],
                env=self.env, input=None if oid is None else (_match(oid, _SHA) + "\n").encode("ascii"),
                stdin=subprocess.DEVNULL if oid is None else None, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise _Unknown(ReasonCode.GIT_OBJECT_UNAVAILABLE) from None
        if result.returncode != 0:
            raise _Unknown(ReasonCode.GIT_OBJECT_UNAVAILABLE)
        return result.stdout

    def current_main(self, expected: str) -> None:
        # This fixed administrative ref is a freshness check, never proof identity.
        current = self._read(["rev-parse", "--verify", "refs/heads/main"])
        _require(current == (expected + "\n").encode("ascii"), ReasonCode.AUTHORITY_NOT_CURRENT)
        self.commit(expected)

    def object(self, sha: str, expected_kind: str) -> bytes:
        _match(sha, _SHA)
        if sha not in self.objects:
            if len(self.objects) >= MAX_OBJECTS:
                raise _Unknown(ReasonCode.RESOURCE_BOUND)
            # Metadata is bounded before any object content is captured. The
            # trusted Git binary receives one full OID, never rev/path expressions.
            metadata = self._read(["cat-file", "--batch-check"], sha)
            match = re.fullmatch(rb"([0-9a-f]{40}) (commit|tree|blob) ([0-9]{1,12})\n", metadata)
            if len(metadata) > 80 or match is None or match[1].decode("ascii") != sha:
                raise _Unknown(ReasonCode.GIT_OBJECT_CORRUPT)
            kind, size = match[2], int(match[3])
            if size > MAX_RECORD_BYTES or self.bytes + size > MAX_TOTAL_BYTES:
                raise _Unknown(ReasonCode.RESOURCE_BOUND)
            body = self._read(["cat-file", kind.decode("ascii"), sha])
            actual = hashlib.sha1(kind + b" " + str(len(body)).encode("ascii") + b"\0" + body).hexdigest()
            _require(len(body) == size and actual == sha, ReasonCode.GIT_OBJECT_CORRUPT)
            self.bytes += size
            self.objects[sha] = (kind.decode("ascii"), body)
        kind, body = self.objects[sha]
        _require(kind == expected_kind, ReasonCode.GIT_OBJECT_CORRUPT)
        return body

    def commit(self, sha: str) -> tuple[str, tuple[str, ...]]:
        if sha not in self.commits:
            body = self.object(sha, "commit")
            headers = body.split(b"\n\n", 1)[0].split(b"\n")
            trees = [line[5:].decode("ascii") for line in headers if line.startswith(b"tree ")]
            parents = tuple(line[7:].decode("ascii") for line in headers if line.startswith(b"parent "))
            _require(len(trees) == 1 and len(parents) <= 16, ReasonCode.GIT_OBJECT_CORRUPT)
            _match(trees[0], _SHA)
            for parent in parents:
                _match(parent, _SHA)
            self.object(trees[0], "tree")
            self.commits[sha] = (trees[0], parents)
        return self.commits[sha]

    def ancestor(self, older: str, newer: str) -> None:
        # Traverse authenticated raw parents, unaffected by replace refs/grafts.
        self.commit(older)
        pending, seen = [newer], set()
        while pending:
            current = pending.pop()
            if current == older:
                return
            if current in seen:
                continue
            seen.add(current)
            if len(seen) > MAX_ANCESTORS:
                raise _Unknown(ReasonCode.RESOURCE_BOUND)
            pending.extend(reversed(self.commit(current)[1]))
        raise _Unknown(ReasonCode.LINEAGE_CONFLICT)

    def entry(self, candidate: str, path: str) -> tuple[str, str] | None:
        tree, _ = self.commit(candidate)
        parts = _path(path).split("/")
        for index, part in enumerate(parts):
            body = self.object(tree, "tree")
            entries = {}
            offset = 0
            while offset < len(body):
                space = body.index(b" ", offset)
                nul = body.index(b"\0", space)
                mode = body[offset:space].decode("ascii")
                name = body[space + 1:nul]
                oid = body[nul + 1:nul + 21]
                _require(len(oid) == 20 and name not in entries, ReasonCode.GIT_OBJECT_CORRUPT)
                entries[name] = (mode, oid.hex())
                offset = nul + 21
            found = entries.get(part.encode("ascii"))
            if found is None:
                return None
            mode, sha = found
            if index < len(parts) - 1:
                if mode != "40000":
                    raise _Unknown(ReasonCode.FOOTPRINT_UNKNOWN)
                tree = sha
            elif mode in ("100644", "100755"):
                self.object(sha, "blob")
                return found
            else:
                # Directories, symlinks and submodules need reviewed observers;
                # v1 deliberately accepts only complete explicit file closures.
                raise _Unknown(ReasonCode.FOOTPRINT_UNKNOWN)
        return None

    def record(self, identity: RecordIdentity, *, decoded: bool = True) -> object:
        entry = self.entry(identity.commit_sha, identity.path)
        _require(entry is not None and entry[0] in ("100644", "100755")
                 and entry[1] == identity.blob_sha, ReasonCode.RECORD_CORRUPT)
        raw = self.object(identity.blob_sha, "blob")
        _require(hashlib.sha256(raw).hexdigest() == identity.sha256, ReasonCode.RECORD_CORRUPT)
        if not decoded:
            return raw
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
            _bounded(data)
            return data
        except (UnicodeError, ValueError, RecursionError):
            raise _Unknown(ReasonCode.RECORD_CORRUPT) from None


def _proof_pin(value: object) -> ProofPin:
    return ProofPin(value.id, value.revision, value.digest)


def _descriptor_digest(proof) -> str:
    # The foundation returns immutable tuple fields; public decoders still
    # accept only plain decoded JSON, never caller-defined tuple objects.
    return canonical_digest(json.loads(json.dumps(asdict(proof))))


def _coverage(contract: object, mapping: object, task: dict):
    # VP-02 remains the sole decoder of proof and mapping semantics. Independent
    # trust is established by catalog-pinned bytes, not these computed pins.
    try:
        return validate_proof_mapping(contract, mapping, expected_task=task,
            expected_mapping={"id": mapping["id"], "revision": mapping["revision"],
                              "digest": proof_mapping_digest(mapping)})
    except (ProofCoverageError, KeyError, TypeError, ValueError):
        raise _Unknown(ReasonCode.MAPPING_CONFLICT) from None


def _selected(coverage, obligation: ProofPin):
    selected = next((item for item in coverage.contract.obligations if item.id == obligation.id), None)
    _require(selected is not None and _proof_pin(selected) == obligation, ReasonCode.MAPPING_CONFLICT)
    entry = next(item for item in coverage.mapping.entries if item.obligation_id == selected.id)
    proof = next(item for item in coverage.mapping.proofs if item.id == entry.proof_id)
    return selected, proof


def _projection(conditions, dimension: str) -> dict:
    value = asdict(conditions)
    # Decode tuple fields back into the existing foundation's plain list form.
    value["fixture_refs"] = list(conditions.fixture_refs)
    return {key: value[key] for key in _FIELDS[dimension]}


def _contexts(git: _Git, plan: dict, dimension: str, source, target,
              source_proof, target_proof, evidence_pin, catalog_pin, observation: dict):
    contexts = []
    for role, subject, proof, digest in (
        ("source", source, source_proof, evidence_pin.sha256),
        ("target", target, target_proof, None),
    ):
        pin = decode_record_identity(plan[role + "_context"])
        git.ancestor(subject, pin.commit_sha)
        git.ancestor(pin.commit_sha, catalog_pin.commit_sha)
        data = _object(git.record(pin), {"schema", "dimension", "subject_sha", "proof_id",
                                         "evidence_digest", "conditions", "facts"})
        _require(data["schema"] == CONTEXT_SCHEMA and data["dimension"] == dimension
                 and data["subject_sha"] == subject and data["proof_id"] == proof.id
                 and data["evidence_digest"] == digest, ReasonCode.WITNESS_CONFLICT)
        expected = _projection(proof.conditions, dimension)
        _require(canonical_digest(data["conditions"]) == canonical_digest(expected) and type(data["facts"]) is dict
                 and bool(data["facts"]), ReasonCode.DEPENDENCY_INCOMPLETE)
        if role == "source" and dimension in ("toolchain", "profile"):
            key = dimension
            _require(canonical_digest(data["facts"].get(key)) == canonical_digest(observation[key]),
                     ReasonCode.SOURCE_BINDING_CONFLICT)
        if role == "source" and dimension == "worker_mode":
            _require(canonical_digest(data["facts"].get("profile")) == canonical_digest(observation["profile"])
                     and observation["profile"].get("worker_mode") == proof.conditions.worker_mode
                     and type(observation["profile"].get("workers")) is int
                     and observation["profile"]["workers"] == proof.conditions.workers,
                     ReasonCode.SOURCE_BINDING_CONFLICT)
        contexts.append(data)
    return contexts


def _dependencies(git, review, witness, source, target, source_proof, target_proof,
                  evidence_pin, catalog_pin, observation):
    plans = _list(review["dimensions"], len(DIMENSIONS))
    comparisons = _list(witness["dimensions"], len(DIMENSIONS))
    by_name, by_witness = {}, {}
    for item in plans:
        data = _object(item, {"dimension", "coverage", "basis", "paths", "source_context", "target_context"})
        name = data["dimension"]
        _require(type(name) is str and name in DIMENSIONS and name not in by_name,
                 ReasonCode.DEPENDENCY_INCOMPLETE)
        by_name[name] = data
    for item in comparisons:
        data = _object(item, {"dimension", "source_digest", "target_digest"})
        name = data["dimension"]
        _require(type(name) is str and name in DIMENSIONS and name not in by_witness,
                 ReasonCode.WITNESS_CONFLICT)
        _match(data["source_digest"], _DIGEST)
        _match(data["target_digest"], _DIGEST)
        by_witness[name] = data
    reasons, digests = [], []
    for dimension in DIMENSIONS:
        plan, comparison = by_name.get(dimension), by_witness.get(dimension)
        if plan is None or comparison is None:
            reasons.append(Reason(ReasonCode.DEPENDENCY_INCOMPLETE, dimension))
            continue
        try:
            if plan["coverage"] not in ("COMPLETE", "INAPPLICABLE"):
                raise _Unknown(ReasonCode.DEPENDENCY_INCOMPLETE, dimension)
            _match(plan["basis"], _ID)
            paths = tuple(_path(path) for path in _list(plan["paths"], MAX_PATHS))
            _require(len(set(paths)) == len(paths), ReasonCode.DEPENDENCY_INCOMPLETE)
            if plan["coverage"] == "COMPLETE" and dimension in ("test_code", "fixtures", "helpers", "shared_state"):
                _require(bool(paths), ReasonCode.FOOTPRINT_UNKNOWN)
            contexts = _contexts(git, plan, dimension, source, target, source_proof,
                                 target_proof, evidence_pin, catalog_pin, observation)
            if plan["coverage"] == "INAPPLICABLE":
                # Explicit trusted inapplicability cannot erase real execution
                # distinctions or a nonempty fixture obligation.
                _require(not paths and dimension in ("fixtures", "helpers", "shared_state")
                         and (dimension != "fixtures" or not source_proof.conditions.fixture_refs)
                         and all(c["facts"].get("inapplicable") is True for c in contexts),
                         ReasonCode.DEPENDENCY_INCOMPLETE)
            fingerprints = []
            for subject, context in zip((source, target), contexts):
                objects = []
                for path in sorted(paths):
                    entry = git.entry(subject, path)
                    if subject == source and entry is None:
                        raise _Unknown(ReasonCode.FOOTPRINT_UNKNOWN, dimension)
                    objects.append([path, None if entry is None else list(entry)])
                fingerprints.append(canonical_digest({"conditions": context["conditions"],
                    "facts": context["facts"], "objects": objects}))
            _require(fingerprints == [comparison["source_digest"], comparison["target_digest"]],
                     ReasonCode.WITNESS_CONFLICT)
            digests.append((dimension, *fingerprints))
            if fingerprints[0] != fingerprints[1]:
                reasons.append(Reason(ReasonCode.DEPENDENCY_CHANGED, dimension))
        except _Unknown as exc:
            reasons.append(Reason(exc.reason.code, dimension))
    return tuple(reasons), tuple(digests)


def evaluate_applicability(request: ApplicabilityRequest, *,
                           authority: ReadOnlyAuthority | None) -> ApplicabilityResult:
    """Read one trusted snapshot; describe one mapped obligation, never schedule.

    Malformed requests raise ApplicabilityInputError. Missing/inconsistent
    authority, corrupt records, stale refs and incomplete facts return UNKNOWN.
    The authority must be supplied independently of the untrusted request.
    """
    if type(request) is not ApplicabilityRequest:
        raise ApplicabilityInputError("decode_request must precede evaluation")
    # Avoid deepcopy/asdict on unvalidated constructor fields: no caller object
    # hooks are executed while converting a forged typed wrapper for revalidation.
    request = decode_request(_request_values(request))
    source = None
    authenticated_witness = None
    authenticated = False
    try:
        _require(type(authority) is ReadOnlyAuthority, ReasonCode.AUTHORITY_UNAVAILABLE)
        _require(type(authority.repository) is type(Path()),
                 ReasonCode.AUTHORITY_UNAVAILABLE)
        catalog_pin = decode_record_identity(_record_values(authority.catalog))
        git = _Git(authority.repository)
        catalog = _object(git.record(catalog_pin), {"schema", "repository_id", "expected_main_sha", "task_binding",
            "base_sha", "source_candidate_sha", "target_candidate_sha", "source_proof_id",
            "target_obligation", *_RECORD_NAMES})
        _require(catalog["schema"] == CATALOG_SCHEMA, ReasonCode.AUTHORITY_UNAVAILABLE)
        _match(catalog["repository_id"], _ID)
        expected_main = _match(catalog["expected_main_sha"], _SHA)
        git.current_main(expected_main)
        base, original, target = (_match(catalog[key], _SHA) for key in
                                  ("base_sha", "source_candidate_sha", "target_candidate_sha"))
        _require(base != original and target != base, ReasonCode.LINEAGE_CONFLICT)
        for older, newer in ((expected_main, base), (base, original), (original, target),
                             (target, catalog_pin.commit_sha)):
            git.ancestor(older, newer)
        records = {name: decode_record_identity(catalog[name]) for name in _RECORD_NAMES}
        for pin in records.values():
            git.ancestor(pin.commit_sha, catalog_pin.commit_sha)
        target_pin = _pin(catalog["target_obligation"])
        _require((request.repository_id, request.evidence, request.witness, request.source_proof_id,
                  request.target_candidate_sha, request.target_obligation) ==
                 (catalog["repository_id"], records["evidence"], records["witness"],
                  catalog["source_proof_id"], target, target_pin), ReasonCode.SOURCE_BINDING_CONFLICT)
        data = {name: git.record(pin) for name, pin in records.items() if name != "raw"}
        git.record(records["raw"], decoded=False)
        task = data["task"]
        # Reuse the historical decoder without introducing an admission status.
        validated_task = validate_task(task)
        expected_task = _object(catalog["task_binding"], {"task_id", "revision", "envelope_digest", "acceptance_ids"})
        _require(expected_task["task_id"] == validated_task.task_id
                 and type(expected_task["revision"]) is int
                 and expected_task["revision"] == validated_task.revision
                 and type(expected_task["acceptance_ids"]) is list
                 and sorted(expected_task["acceptance_ids"]) == sorted(item.id for item in validated_task.acceptance),
                 ReasonCode.SOURCE_BINDING_CONFLICT)
        _match(expected_task["envelope_digest"], _DIGEST)
        git.ancestor(records["task"].commit_sha, base)
        for name in ("run", "result", "evidence", "raw", "source_contract", "source_mapping"):
            git.ancestor(original, records[name].commit_sha)
        for name in ("target_contract", "target_mapping", "review", "witness"):
            git.ancestor(target, records[name].commit_sha)
        run_data = _object(data["run"], {"run_id", "task", "executor", "base_sha", "workspace",
                                        "head_sha", "status", "return_affinity"})
        run_task = _object(run_data["task"], {"id", "revision"})
        run = Run(**dict(run_data, task=RunTaskReference(**run_task)))
        _require(run.status in ("ACTIVE", "RESULT"), ReasonCode.SOURCE_BINDING_CONFLICT)
        result_data = _object(data["result"], {"head_sha", "claims", "changed_files", "unresolved"})
        result = validate_result(result_data)
        evidence_data = _object(data["evidence"], {"evidence_id", "run_id", "subject_sha", "type",
                                                  "source", "result", "raw", "verification"})
        evidence = validate_evidence(evidence_data)
        _require((run.task.id, run.task.revision, run.base_sha, run.head_sha) ==
                 (task["task_id"], task["revision"], base, original)
                 and evidence.run_id == run.run_id and result.head_sha == original
                 and evidence.subject_sha == original and evidence.raw_path == records["raw"].path,
                 ReasonCode.SOURCE_BINDING_CONFLICT)
        verification = evidence.verification
        _require(type(verification) is dict and not any(key in verification for key in
                 ("reuse", "early_probe")), ReasonCode.SOURCE_PROOF_UNKNOWN)
        observation = validate_observation(verification["candidate"])
        tree, _ = git.commit(original)
        _require(verification.get("evidence_id") == evidence.evidence_id
                 and verification["binding"]["tree_sha"] == tree
                 and verification["binding"]["base_sha"] == base
                 and verification["binding"]["envelope_digest"] == expected_task["envelope_digest"]
                 and observation["subject_sha"] == original
                 and observation["exit_code"] in (0, 1), ReasonCode.SOURCE_BINDING_CONFLICT)
        left = _coverage(data["source_contract"], data["source_mapping"], expected_task)
        right = _coverage(data["target_contract"], data["target_mapping"], expected_task)
        _require(all(o.conditions.candidate_sha == original for o in left.contract.obligations)
                 and all(o.conditions.candidate_sha == target for o in right.contract.obligations)
                 and all(o.kind != "comparison" or o.conditions.base_sha == base
                         for c in (left, right) for o in c.contract.obligations), ReasonCode.MAPPING_CONFLICT)
        proof = next((p for p in left.mapping.proofs if p.id == request.source_proof_id), None)
        _require(proof is not None, ReasonCode.MAPPING_CONFLICT)
        if proof.kind == "comparison":
            _require("base" in verification, ReasonCode.SOURCE_PROOF_UNKNOWN)
            base_observation = validate_observation(verification["base"])
            _require(base_observation["subject_sha"] == base
                     and base_observation["exit_code"] in (0, 1)
                     and all(canonical_digest(base_observation[key]) == canonical_digest(observation[key])
                             for key in ("command", "profile", "toolchain"))
                     and len({r["nodeid"] for r in base_observation["reports"] if r["phase"] == "call"})
                         == proof.conditions.population_size
                     and not any(r["outcome"] == "SKIP" for r in base_observation["reports"]),
                     ReasonCode.SOURCE_PROOF_UNKNOWN)
        obligation, target_proof = _selected(right, target_pin)
        _require(proof.kind == target_proof.kind and obligation.claim in proof.claims,
                 ReasonCode.MAPPING_CONFLICT)
        source_obligations = [o for o in left.contract.obligations if any(
            e.obligation_id == o.id and e.proof_id == proof.id for e in left.mapping.entries)]
        _require(all(any(evidence.evidence_id in claim.evidence
                         and set(o.acceptance_ids) <= set(claim.satisfies) for claim in result.claims)
                     for o in source_obligations), ReasonCode.SOURCE_BINDING_CONFLICT)
        population = {r["nodeid"] for r in observation["reports"] if r["phase"] == "call"}
        _require(evidence.type == proof.conditions.evidence_kind
                 and len(population) == proof.conditions.population_size
                 and not any(r["outcome"] == "SKIP" for r in observation["reports"]),
                 ReasonCode.SOURCE_PROOF_UNKNOWN)
        outcome = "PASS" if observation["exit_code"] == 0 else "FAIL"
        source = OriginalObservation(records["task"], task["task_id"], task["revision"],
            expected_task["envelope_digest"], records["run"], run.run_id, records["result"],
            records["evidence"], evidence.evidence_id, records["raw"], original, tree,
            evidence.source.command, evidence.result.exit_code, outcome, _proof_pin(left.contract),
            _proof_pin(left.mapping), proof.id, _descriptor_digest(proof))
        authenticated = True
        review = _object(data["review"], {"schema", "task", "source_contract", "source_mapping",
            "target_contract", "target_mapping", "source_proof_id", "source_proof_digest",
            "target_obligation", "target_proof_id", "source_observation", "dimensions"})
        witness = _object(data["witness"], {"schema", "repository_id", "task", "run", "result",
            "evidence", "raw", "source_candidate_sha", "source_tree_sha", "target_candidate_sha",
            "target_tree_sha", "base_sha", "source_contract", "source_mapping", "source_proof_id",
            "source_proof_digest", "target_contract", "target_mapping", "target_obligation",
            "target_proof_id", "review", "dimensions"})
        _require(review["schema"] == REVIEW_SCHEMA and witness["schema"] == WITNESS_SCHEMA,
                 ReasonCode.WITNESS_CONFLICT)
        quality = _object(review["source_observation"], {"evidence_digest", "complete", "stable",
            "conflicting", "observed_items", "outcome"})
        _require(quality["evidence_digest"] == source.evidence.sha256
                 and quality["complete"] is True and quality["stable"] is True
                 and quality["conflicting"] is False and type(quality["observed_items"]) is int
                 and quality["observed_items"] == proof.conditions.population_size
                 and quality["outcome"] == outcome
                 and observation.get("conflicting", False) is False, ReasonCode.SOURCE_PROOF_UNKNOWN)
        for name, pin in (("source_contract", source.source_contract), ("source_mapping", source.source_mapping),
                          ("target_contract", _proof_pin(right.contract)),
                          ("target_mapping", _proof_pin(right.mapping)), ("target_obligation", target_pin)):
            _require(_pin(review[name]) == pin and _pin(witness[name]) == pin, ReasonCode.WITNESS_CONFLICT)
        for name in ("task", "run", "result", "evidence", "raw", "review"):
            _require(decode_record_identity(witness[name]) == records[name], ReasonCode.WITNESS_CONFLICT)
        _require(decode_record_identity(review["task"]) == records["task"], ReasonCode.WITNESS_CONFLICT)
        for record in (review, witness):
            _require(record["source_proof_id"] == proof.id
                     and record["source_proof_digest"] == source.source_proof_digest
                     and record["target_proof_id"] == target_proof.id, ReasonCode.WITNESS_CONFLICT)
        target_tree = git.commit(target)[0]
        _require((witness["repository_id"], witness["source_candidate_sha"], witness["source_tree_sha"],
                  witness["target_candidate_sha"], witness["target_tree_sha"], witness["base_sha"]) ==
                 (request.repository_id, original, tree, target, target_tree, base), ReasonCode.WITNESS_CONFLICT)
        reasons, digests = _dependencies(git, review, witness, original, target, proof, target_proof,
                                         records["evidence"], catalog_pin, observation)
        authenticated_witness = ApplicabilityWitness(records["witness"], records["review"], original,
            target, target_tree, base, _proof_pin(right.contract), _proof_pin(right.mapping), target_pin, digests)
        git.current_main(expected_main)
        if any(reason.code != ReasonCode.DEPENDENCY_CHANGED for reason in reasons):
            state = State.UNKNOWN
        else:
            state = State.INVALIDATED if reasons else State.VALID
        return ApplicabilityResult(SCHEMA, state, reasons, source, authenticated_witness,
                                   target_pin if state == State.VALID else None, authenticated)
    except _Unknown as exc:
        reasons = (exc.reason,)
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration, UnicodeError):
        reasons = (Reason(ReasonCode.RECORD_CORRUPT),)
    return ApplicabilityResult(SCHEMA, State.UNKNOWN, reasons, source, authenticated_witness, None, authenticated)
