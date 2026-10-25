"""VP-02B: bounded offline declarations, never Runtime authority or evidence.

The historical VP-02B declarations accept only decoded plain Python values.
Native K_i/F_i and local receipt codecs below use the original canonical
inspection validators through ordinary local imports, independently of Runtime.
No loader, callback execution, clock or production integration exists here.
Independent pins detect contradictions; neither pins nor digests authenticate
the caller's canonical provenance. Every returned authorization flag is false.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .verification_contract import ProofCheckpointRequest

from .proof_coverage_contract import (
    BR_GATES, CoverageDeclaration, ProofCoverageError,
    proof_conditions_equivalent, validate_proof_mapping,
)


SCHEMA = "intra-run-proof-v1"
LIFECYCLE_SCHEMA = "intra-run-lifecycle-v1"
MAX_CHECKPOINTS = 17
MAX_CORRECTIONS = 16
MAX_SECONDS = 3600
MAX_ITEMS = 100_000
MAX_TOKENS = 1_000_000
MAX_PATHS = 128
MAX_PROOFS = 256
DIMENSIONS = (
    "test_code", "fixtures", "helpers", "shared_state", "toolchain",
    "environment", "profile", "worker_mode", "concurrency", "ordering",
    "collection", "integration", "repetition", "population", "evidence_schema",
)
INVARIANTS = (
    "retain_source_subject", "full_immutable_applicability_witness",
    "unknown_never_discharges", "preserve_distinct_integration_concurrency_ordering",
    "no_equivalent_double_execution", "no_automatic_failure_reproduction",
    "no_automatic_base_replay", "no_broad_fallback", "budget_never_waives_proof",
)
AUTHORITY_BINDING_FIELDS = (
    "task_id", "task_revision", "acceptance_ids", "task_source_sha", "task_blob_sha",
    "task_envelope_digest", "run_id", "run_source_sha", "run_record_digest", "base_sha",
)
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z", re.ASCII)
_COMMAND_NAMES = frozenset(("pytest", "python", "python3", "py", "git", "aios", "powershell",
                            "pwsh", "cmd", "bash", "sh", "curl", "wget", "node"))


class IntraRunContractError(ValueError):
    """Malformed, stale, contradictory or terminal preterminal declaration."""


@dataclass(frozen=True)
class Pin:
    id: str
    revision: int
    digest: str


@dataclass(frozen=True)
class SourceDeclaration:
    ref: str
    source_sha: str
    record_digest: str


@dataclass(frozen=True)
class Binding:
    task_id: str
    task_revision: int
    acceptance_ids: tuple[str, ...]
    task_source_sha: str
    task_blob_sha: str
    task_envelope_digest: str
    run_id: str
    run_source_sha: str
    run_record_digest: str
    base_sha: str
    candidate_sha: str
    candidate_tree_sha: str
    operational_authority_digest: str
    proof_contract: Pin
    proof_mapping: Pin


@dataclass(frozen=True)
class Delegation:
    human: SourceDeclaration
    executor: str
    profile: str
    model: str
    effort: str
    lease_id: str
    lease_generation: int


@dataclass(frozen=True)
class Resources:
    corrections: int
    seconds: int
    test_items: int
    tokens: int


@dataclass(frozen=True)
class EvidenceDeclaration:
    proof_id: str
    provenance: SourceDeclaration
    subject_sha: str
    conditions_digest: str
    outcome: str
    validity: str
    complete: bool
    stable: bool
    conflicting: bool
    observed_items: int


@dataclass(frozen=True)
class Checkpoint:
    kind: str
    owner: str
    ordinal: int
    predecessor_digest: str | None
    committed: bool
    clean: bool
    resource_usage: Resources
    evidence: tuple[EvidenceDeclaration, ...]
    content_digest: str


@dataclass(frozen=True)
class FeedbackItem:
    obligation: Pin
    acceptance_ids: tuple[str, ...]
    evidence: SourceDeclaration
    outcome: str


@dataclass(frozen=True)
class Feedback:
    kind: str
    binding: Binding
    checkpoint_digest: str
    items: tuple[FeedbackItem, ...]
    content_digest: str


@dataclass(frozen=True)
class Continuation:
    binding: Binding
    delegation: Delegation
    checkpoint_digest: str
    feedback_digest: str
    target_candidate_sha: str
    changed_paths: tuple[str, ...]
    change_digest: str
    causal_obligation_ids: tuple[str, ...]
    progress: str
    risk: str
    semantic_choice: str
    requested: Resources


@dataclass(frozen=True)
class ApplicabilityRequirement:
    obligation: Pin
    source_evidence: SourceDeclaration
    source_candidate_sha: str
    target_candidate_sha: str
    target_conditions: tuple
    declared_state: str
    changed_dimensions: tuple[str, ...]
    witness: SourceDeclaration | None
    witness_dimensions: tuple[str, ...]


@dataclass(frozen=True)
class ContinuationDescription:
    prospective_conditions_declared: bool
    prerequisite_blocks: tuple[str, ...]
    status: str = "BLOCK"
    activation: str = "NOT_ACTIVATED"
    kernel_gate: str = "KERNEL_AMENDMENT_REQUIRED"
    provenance_authenticated: bool = False
    runtime_continuation_authorized: bool = False
    evidence_reuse_authorized: bool = False
    verification_execution_authorized: bool = False


@dataclass(frozen=True)
class IntraRunDeclaration:
    schema: str
    binding: Binding
    delegation: Delegation
    coverage: CoverageDeclaration
    checkpoint: Checkpoint
    feedback: Feedback
    continuation: Continuation
    applicability: tuple[ApplicabilityRequirement, ...]
    description: ContinuationDescription
    invariants: tuple[str, ...] = INVARIANTS
    base_replay_required_gates: tuple[str, ...] = BR_GATES
    verified_evidence: bool = False
    canonical_checkpoint_created: bool = False


@dataclass(frozen=True)
class LifecycleDescription:
    route: str
    compatibility: str
    reason: str
    execution_authorized: bool = False
    lifecycle_mutation_authorized: bool = False


def _object(value: object, fields: set[str], path: str) -> dict:
    if (type(value) is not dict or len(value) != len(fields)
            or any(type(key) is not str for key in value) or set(value) != fields):
        raise IntraRunContractError(f"{path}: exact fields required")
    return value


def _record(value: object, cls: type, path: str) -> dict:
    return _object(value, set(cls.__dataclass_fields__), path)


def _match(value: object, pattern: re.Pattern, path: str) -> str:
    if type(value) is not str or len(value) > 512 or pattern.fullmatch(value) is None:
        raise IntraRunContractError(f"{path}: invalid identity")
    return value


def _id(value: object, path: str) -> str:
    identity = _match(value, _ID, path)
    if identity.lower() in _COMMAND_NAMES:
        raise IntraRunContractError(f"{path}: executable token is not an identity")
    return identity


def _number(value: object, limit: int, path: str, *, zero: bool = False) -> int:
    if type(value) is not int or not (0 if zero else 1) <= value <= limit:
        raise IntraRunContractError(f"{path}: bounded integer required")
    return value


def _bool(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise IntraRunContractError(f"{path}: boolean required")
    return value


def _choice(value: object, choices: tuple[str, ...], path: str) -> str:
    if type(value) is not str or value not in choices:
        raise IntraRunContractError(f"{path}: unsupported value")
    return value


def _list(value: object, limit: int, path: str, *, empty: bool = False) -> list:
    if type(value) is not list or len(value) > limit or (not value and not empty):
        raise IntraRunContractError(f"{path}: bounded list required")
    return value


def _strings(value: object, limit: int, path: str, *, empty: bool = False,
             pattern: re.Pattern = _ID) -> tuple[str, ...]:
    parser = _id if pattern is _ID else lambda item, name: _match(item, pattern, name)
    items = tuple(parser(item, path) for item in _list(value, limit, path, empty=empty))
    if len(set(items)) != len(items):
        raise IntraRunContractError(f"{path}: duplicate identity")
    return items


def _pin(value: object, path: str) -> Pin:
    data = _record(value, Pin, path)
    return Pin(_id(data["id"], path), _number(data["revision"], 2_147_483_647, path),
               _match(data["digest"], _DIGEST, path))


def _source(value: object, path: str) -> SourceDeclaration:
    data = _record(value, SourceDeclaration, path)
    return SourceDeclaration(_id(data["ref"], path), _match(data["source_sha"], _SHA, path),
                             _match(data["record_digest"], _DIGEST, path))


def _binding(value: object) -> Binding:
    data = _record(value, Binding, "binding")
    sha_fields = {"task_source_sha", "task_blob_sha", "run_source_sha", "base_sha",
                  "candidate_sha", "candidate_tree_sha"}
    parsed = {key: _match(data[key], _SHA, f"binding.{key}") for key in sha_fields}
    for key in ("task_envelope_digest", "run_record_digest", "operational_authority_digest"):
        parsed[key] = _match(data[key], _DIGEST, f"binding.{key}")
    parsed.update(task_id=_id(data["task_id"], "binding.task_id"),
                  task_revision=_number(data["task_revision"], 2_147_483_647, "binding.task_revision"),
                  acceptance_ids=_strings(data["acceptance_ids"], MAX_PROOFS, "binding.acceptance_ids"),
                  run_id=_id(data["run_id"], "binding.run_id"),
                  proof_contract=_pin(data["proof_contract"], "binding.proof_contract"),
                  proof_mapping=_pin(data["proof_mapping"], "binding.proof_mapping"))
    if parsed["base_sha"] == parsed["candidate_sha"]:
        raise IntraRunContractError("binding: candidate must differ from admitted base")
    return Binding(**parsed)


def _delegation(value: object) -> Delegation:
    data = _record(value, Delegation, "delegation")
    return Delegation(_source(data["human"], "delegation.human"),
                      _choice(data["executor"], ("codex", "antigravity"), "delegation.executor"),
                      _id(data["profile"], "delegation.profile"), _id(data["model"], "delegation.model"),
                      _choice(data["effort"], ("low", "medium", "high", "xhigh", "max", "ultra"), "delegation.effort"),
                      _id(data["lease_id"], "delegation.lease_id"),
                      _number(data["lease_generation"], 2_147_483_647, "delegation.lease_generation"))


def _resources(value: object, path: str, *, spent: bool = False) -> Resources:
    data = _record(value, Resources, path)
    limits = (MAX_CORRECTIONS, MAX_SECONDS, MAX_ITEMS, MAX_TOKENS)
    return Resources(*(_number(data[key], limit, f"{path}.{key}", zero=spent)
                       for key, limit in zip(Resources.__dataclass_fields__, limits)))


def _paths(value: object, path: str) -> tuple[str, ...]:
    paths = _strings(value, MAX_PATHS, path, pattern=_PATH)
    if any(len(item) > 512 or any(part in (".", "..") for part in item.split("/")) for item in paths):
        raise IntraRunContractError(f"{path}: exact relative file paths required")
    return paths


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()


def _freeze(value: object) -> tuple | str | int | None:
    # Called only after the foundation validates the complete bounded conditions.
    if type(value) is dict:
        return tuple((key, _freeze(value[key])) for key in sorted(value))
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _context(value: object, binding: Binding, delegation: Delegation) -> dict:
    fields = {"binding", "delegation", "operation", "admission", "run_state", "terminal_ref", "lease_state",
              "authority_current", "opt_in", "budget_authority", "limits", "spent", "allowed_paths",
              "next_ordinal", "predecessor", "seen_checkpoint_digests", "seen_feedback_digests",
              "seen_candidate_shas", "checkpoint_digest"}
    data = _object(value, fields, "expected_context")
    if _binding(data["binding"]) != binding or _delegation(data["delegation"]) != delegation:
        raise IntraRunContractError("context: stale binding or changed Human/Executor/profile/lease")
    _choice(data["operation"], ("PRIMARY",), "context.operation")
    _choice(data["admission"], ("ADMITTED",), "context.admission")
    _choice(data["run_state"], ("ACTIVE",), "context.run_state")
    if data["terminal_ref"] is not None:
        raise IntraRunContractError("context: terminal lineage cannot reenter preterminal work")
    _choice(data["lease_state"], ("LIVE", "LOST", "EXPIRED", "UNKNOWN"), "context.lease_state")
    _bool(data["authority_current"], "context.authority_current")
    opt = _object(data["opt_in"], {"enabled", "human"}, "context.opt_in")
    _bool(opt["enabled"], "context.opt_in.enabled")
    if _source(opt["human"], "context.opt_in.human") != delegation.human:
        raise IntraRunContractError("context: opt-in Human authority differs from delegation")
    budget = _object(data["budget_authority"], {"human", "record"}, "context.budget_authority")
    if _source(budget["human"], "context.budget_authority.human") != delegation.human:
        raise IntraRunContractError("context: budget Human differs from delegation")
    _source(budget["record"], "context.budget_authority.record")
    _resources(data["limits"], "context.limits")
    _resources(data["spent"], "context.spent", spent=True)
    _paths(data["allowed_paths"], "context.allowed_paths")
    authority = {"binding": {key: asdict(binding)[key] for key in AUTHORITY_BINDING_FIELDS},
                 "delegation": asdict(delegation), "opt_in": data["opt_in"],
                 "budget_authority": data["budget_authority"], "limits": data["limits"],
                 "allowed_paths": data["allowed_paths"]}
    if binding.operational_authority_digest != _digest(authority):
        raise IntraRunContractError("context: opt-in, scope or immutable Human budget binding changed")
    ordinal = _number(data["next_ordinal"], MAX_CHECKPOINTS, "context.next_ordinal")
    seen = _strings(data["seen_checkpoint_digests"], MAX_CORRECTIONS, "context.seen_checkpoints",
                    empty=True, pattern=_DIGEST)
    feedback = _strings(data["seen_feedback_digests"], MAX_CORRECTIONS, "context.seen_feedback",
                        empty=True, pattern=_DIGEST)
    candidates = _strings(data["seen_candidate_shas"], MAX_CORRECTIONS, "context.seen_candidates",
                          empty=True, pattern=_SHA)
    if len(seen) != ordinal - 1 or len(feedback) != ordinal - 1 or len(candidates) != ordinal - 1:
        raise IntraRunContractError("context: complete monotonic predecessor history required")
    if binding.candidate_sha in candidates:
        raise IntraRunContractError("context: candidate already observed in this lineage")
    if ordinal == 1:
        if data["predecessor"] is not None:
            raise IntraRunContractError("context: first checkpoint has no predecessor")
    else:
        previous = _object(data["predecessor"], {"ordinal", "digest", "candidate_sha"}, "context.predecessor")
        previous_ordinal = _number(previous["ordinal"], MAX_CHECKPOINTS, "context.predecessor.ordinal")
        previous_digest = _match(previous["digest"], _DIGEST, "context.predecessor.digest")
        previous_sha = _match(previous["candidate_sha"], _SHA, "context.predecessor.candidate_sha")
        if previous_ordinal != ordinal - 1 or previous_digest != seen[-1] or previous_sha != candidates[-1]:
            raise IntraRunContractError("context: stale predecessor or unchanged candidate")
    _match(data["checkpoint_digest"], _DIGEST, "context.checkpoint_digest")
    return data


def _checkpoint(value: object, binding: Binding, delegation: Delegation,
                coverage: CoverageDeclaration, context: dict) -> Checkpoint:
    data = _record(value, Checkpoint, "checkpoint")
    _choice(data["kind"], ("PRETERMINAL_OBSERVATION",), "checkpoint.kind")
    _choice(data["owner"], ("RUNTIME_DECLARATION",), "checkpoint.owner")
    ordinal = _number(data["ordinal"], MAX_CHECKPOINTS, "checkpoint.ordinal")
    predecessor = None if data["predecessor_digest"] is None else _match(
        data["predecessor_digest"], _DIGEST, "checkpoint.predecessor_digest")
    expected_previous = None if context["predecessor"] is None else context["predecessor"]["digest"]
    if ordinal != context["next_ordinal"] or predecessor != expected_previous:
        raise IntraRunContractError("checkpoint: stale ordinal or predecessor")
    _bool(data["committed"], "checkpoint.committed")
    _bool(data["clean"], "checkpoint.clean")
    usage = _resources(data["resource_usage"], "checkpoint.resource_usage", spent=True)
    if usage != _resources(context["spent"], "context.spent", spent=True):
        raise IntraRunContractError("checkpoint: stale bound resource counters")
    evidence = []
    proofs = {proof.id: proof for proof in coverage.mapping.proofs}
    for raw in _list(data["evidence"], MAX_PROOFS, "checkpoint.evidence"):
        item = _record(raw, EvidenceDeclaration, "evidence")
        proof_id = _id(item["proof_id"], "evidence.proof_id")
        if proof_id not in proofs:
            raise IntraRunContractError("evidence: unmapped proof")
        subject = _match(item["subject_sha"], _SHA, "evidence.subject_sha")
        conditions_digest = _match(item["conditions_digest"], _DIGEST, "evidence.conditions_digest")
        proof = proofs[proof_id]
        if subject != binding.candidate_sha or conditions_digest != _digest(asdict(proof.conditions)):
            raise IntraRunContractError("evidence: stale subject or distinguishing conditions")
        count = _number(item["observed_items"], MAX_ITEMS, "evidence.observed_items", zero=True)
        complete = _bool(item["complete"], "evidence.complete")
        if count > proof.conditions.population_size or (complete and count != proof.conditions.population_size):
            raise IntraRunContractError("evidence: contradictory population completeness")
        evidence.append(EvidenceDeclaration(
            proof_id, _source(item["provenance"], "evidence.provenance"), subject, conditions_digest,
            _choice(item["outcome"], ("PASS", "FAIL", "ERROR", "UNKNOWN"), "evidence.outcome"),
            _choice(item["validity"], ("VALID", "INVALIDATED", "UNKNOWN"), "evidence.validity"),
            complete, _bool(item["stable"], "evidence.stable"),
            _bool(item["conflicting"], "evidence.conflicting"), count))
    if (len(evidence) != len(proofs) or {item.proof_id for item in evidence} != set(proofs)
            or len({item.provenance.ref for item in evidence}) != len(evidence)
            or len({item.provenance.record_digest for item in evidence}) != len(evidence)):
        raise IntraRunContractError("checkpoint: complete unique mapped evidence declarations required")
    digest = _match(data["content_digest"], _DIGEST, "checkpoint.content_digest")
    content = {key: data[key] for key in data if key != "content_digest"}
    computed = _digest({"schema": SCHEMA, "binding": asdict(binding),
                        "delegation": asdict(delegation), "checkpoint": content})
    if digest != computed or digest != context["checkpoint_digest"] or digest in context["seen_checkpoint_digests"]:
        raise IntraRunContractError("checkpoint: content tampering, stale pin or repeated checkpoint")
    return Checkpoint(data["kind"], data["owner"], ordinal, predecessor, data["committed"],
                      data["clean"], usage, tuple(evidence), digest)


def _feedback(value: object, binding: Binding, checkpoint: Checkpoint,
              coverage: CoverageDeclaration, context: dict) -> Feedback:
    data = _record(value, Feedback, "feedback")
    _choice(data["kind"], ("FACTUAL_FAILED_PROOF",), "feedback.kind")
    checkpoint_digest = _match(data["checkpoint_digest"], _DIGEST, "feedback.checkpoint_digest")
    if _binding(data["binding"]) != binding or checkpoint_digest != checkpoint.content_digest:
        raise IntraRunContractError("feedback: different TASK/RUN/candidate/checkpoint")
    obligations = {item.id: item for item in coverage.contract.obligations}
    mapped = {entry.obligation_id: entry.proof_id for entry in coverage.mapping.entries}
    evidence = {item.proof_id: item for item in checkpoint.evidence}
    items = []
    for raw in _list(data["items"], MAX_PROOFS, "feedback.items", empty=True):
        item = _record(raw, FeedbackItem, "feedback.item")
        pin = _pin(item["obligation"], "feedback.obligation")
        obligation = obligations.get(pin.id)
        if obligation is None or pin != Pin(obligation.id, obligation.revision, obligation.digest):
            raise IntraRunContractError("feedback: stale or unknown obligation")
        acceptance = _strings(item["acceptance_ids"], MAX_PROOFS, "feedback.acceptance_ids")
        source = _source(item["evidence"], "feedback.evidence")
        observed = evidence[mapped[pin.id]]
        outcome = _choice(item["outcome"], ("FAIL", "ERROR", "UNKNOWN"), "feedback.outcome")
        if (set(acceptance) != set(obligation.acceptance_ids) or source != observed.provenance
                or outcome != observed.outcome):
            raise IntraRunContractError("feedback: conflicting factual acceptance/evidence outcome")
        items.append(FeedbackItem(pin, acceptance, source, outcome))
    required = {identity for identity, proof in mapped.items() if evidence[proof].outcome != "PASS"}
    if len(items) != len(required) or {item.obligation.id for item in items} != required:
        raise IntraRunContractError("feedback: complete unique nonpassing obligations required")
    digest = _match(data["content_digest"], _DIGEST, "feedback.content_digest")
    if digest != _digest({key: data[key] for key in data if key != "content_digest"}) or digest in context["seen_feedback_digests"]:
        raise IntraRunContractError("feedback: tampered or already consumed declaration")
    return Feedback(data["kind"], binding, checkpoint.content_digest, tuple(items), digest)


def _continuation(value: object, binding: Binding, delegation: Delegation,
                  checkpoint: Checkpoint, feedback: Feedback) -> Continuation:
    data = _record(value, Continuation, "continuation")
    if _binding(data["binding"]) != binding or _delegation(data["delegation"]) != delegation:
        raise IntraRunContractError("continuation: changed TASK/RUN/base/candidate/delegation/lease")
    checkpoint_digest = _match(data["checkpoint_digest"], _DIGEST, "continuation.checkpoint_digest")
    feedback_digest = _match(data["feedback_digest"], _DIGEST, "continuation.feedback_digest")
    if checkpoint_digest != checkpoint.content_digest or feedback_digest != feedback.content_digest:
        raise IntraRunContractError("continuation: stale checkpoint or feedback")
    target = _match(data["target_candidate_sha"], _SHA, "continuation.target_candidate_sha")
    requested = _resources(data["requested"], "continuation.requested")
    if requested.corrections != 1:
        raise IntraRunContractError("continuation: exactly one prospective correction per feedback")
    return Continuation(
        binding, delegation, checkpoint.content_digest, feedback.content_digest, target,
        _paths(data["changed_paths"], "continuation.changed_paths"),
        _match(data["change_digest"], _DIGEST, "continuation.change_digest"),
        _strings(data["causal_obligation_ids"], MAX_PROOFS, "continuation.causal_obligation_ids", empty=True),
        _choice(data["progress"], ("ESTABLISHED", "NO_PROGRESS", "UNKNOWN"), "continuation.progress"),
        _choice(data["risk"], ("UNCHANGED", "HUMAN_REQUIRED", "UNKNOWN"), "continuation.risk"),
        _choice(data["semantic_choice"], ("UNCHANGED", "BRAIN_REQUIRED", "UNKNOWN"), "continuation.semantic_choice"),
        requested)


def _applicability(value: object, coverage: CoverageDeclaration, checkpoint: Checkpoint,
                   continuation: Continuation) -> tuple[ApplicabilityRequirement, ...]:
    data = _object(value, {"invariants", "required_dimensions", "base_replay_required_gates", "entries"}, "applicability")
    for key, required in (("invariants", INVARIANTS), ("required_dimensions", DIMENSIONS),
                          ("base_replay_required_gates", BR_GATES)):
        if _strings(data[key], 32, f"applicability.{key}") != required:
            raise IntraRunContractError(f"applicability.{key}: all prospective invariants required")
    obligations = {item.id: item for item in coverage.contract.obligations}
    mapped = {entry.obligation_id: entry.proof_id for entry in coverage.mapping.entries}
    evidence = {item.proof_id: item for item in checkpoint.evidence}
    entries = []
    for raw in _list(data["entries"], MAX_PROOFS, "applicability.entries"):
        item = _record(raw, ApplicabilityRequirement, "applicability.entry")
        pin = _pin(item["obligation"], "applicability.obligation")
        obligation = obligations.get(pin.id)
        if obligation is None or pin != Pin(obligation.id, obligation.revision, obligation.digest):
            raise IntraRunContractError("applicability: stale or unknown obligation")
        source = _source(item["source_evidence"], "applicability.source_evidence")
        source_sha = _match(item["source_candidate_sha"], _SHA, "applicability.source_candidate_sha")
        target_sha = _match(item["target_candidate_sha"], _SHA, "applicability.target_candidate_sha")
        if (source != evidence[mapped[pin.id]].provenance
                or source_sha != continuation.binding.candidate_sha
                or target_sha != continuation.target_candidate_sha):
            raise IntraRunContractError("applicability: changed source-to-target binding")
        # Foundation checks every distinguishing condition; no filename/nodeid selector.
        target = item["target_conditions"]
        proof_conditions_equivalent(target, target)
        if target["candidate_sha"] != continuation.target_candidate_sha:
            raise IntraRunContractError("applicability: stale target conditions")
        state = _choice(item["declared_state"], ("VALID", "INVALIDATED", "UNKNOWN"), "applicability.state")
        changed = _strings(item["changed_dimensions"], len(DIMENSIONS), "applicability.changed", empty=True)
        dimensions = _strings(item["witness_dimensions"], len(DIMENSIONS), "applicability.witness_dimensions", empty=True)
        if not set(changed).issubset(DIMENSIONS) or not set(dimensions).issubset(DIMENSIONS):
            raise IntraRunContractError("applicability: unknown dependency dimension")
        witness = None if item["witness"] is None else _source(item["witness"], "applicability.witness")
        normalized_target = dict(target, candidate_sha=continuation.binding.candidate_sha)
        source_conditions = asdict(obligation.conditions)
        source_conditions["fixture_refs"] = list(obligation.conditions.fixture_refs)
        equal = proof_conditions_equivalent(source_conditions, normalized_target)
        if state == "VALID" and (changed or witness is None or dimensions != DIMENSIONS or not equal):
            raise IntraRunContractError("applicability: VALID needs full witness declaration and unchanged conditions")
        if state == "INVALIDATED" and not changed:
            raise IntraRunContractError("applicability: INVALIDATED needs an explicit changed dependency")
        entries.append(ApplicabilityRequirement(pin, source, item["source_candidate_sha"],
            item["target_candidate_sha"], _freeze(target), state, changed, witness, dimensions))
    if len(entries) != len(obligations) or {item.obligation.id for item in entries} != set(obligations):
        raise IntraRunContractError("applicability: each distinct obligation must remain separately covered")
    return tuple(entries)


def validate_intra_run_contract(data: object, *, expected_context: object,
                                proof_contract: object, proof_mapping: object,
                                expected_mapping: object) -> IntraRunDeclaration:
    """Validate a prospective PRIMARY description. Never permit a live continuation.

    Bad shape/binding raises. Well-formed unsafe prerequisites are described as
    BLOCK. Even complete declarations retain the frozen amendment/activation and
    unauthenticated-provenance blockers; this is not a production decision API.
    """
    root = _object(data, {"schema", "operation", "binding", "delegation", "checkpoint", "feedback",
                          "continuation", "applicability", "governance"}, "contract")
    _choice(root["schema"], (SCHEMA,), "contract.schema")
    _choice(root["operation"], ("PROSPECTIVE_PRIMARY_OPT_IN",), "contract.operation")
    binding, delegation = _binding(root["binding"]), _delegation(root["delegation"])
    context = _context(expected_context, binding, delegation)
    try:
        task = {"task_id": binding.task_id, "revision": binding.task_revision,
                "envelope_digest": binding.task_envelope_digest,
                "acceptance_ids": list(binding.acceptance_ids)}
        coverage = validate_proof_mapping(proof_contract, proof_mapping,
                                          expected_task=task, expected_mapping=expected_mapping)
        if (binding.proof_contract != Pin(coverage.contract.id, coverage.contract.revision, coverage.contract.digest)
                or binding.proof_mapping != Pin(coverage.mapping.id, coverage.mapping.revision, coverage.mapping.digest)):
            raise IntraRunContractError("coverage: stale contract or mapping identity")
        if any(item.conditions.candidate_sha != binding.candidate_sha
               or (item.kind == "comparison" and item.conditions.base_sha != binding.base_sha)
               for item in coverage.contract.obligations):
            raise IntraRunContractError("coverage: different admitted candidate/base")
        checkpoint = _checkpoint(root["checkpoint"], binding, delegation, coverage, context)
        feedback = _feedback(root["feedback"], binding, checkpoint, coverage, context)
        continuation = _continuation(root["continuation"], binding, delegation, checkpoint, feedback)
        applicability = _applicability(root["applicability"], coverage, checkpoint, continuation)
    except ProofCoverageError as exc:
        raise IntraRunContractError(f"proof foundation: {exc}") from exc
    except (KeyError, TypeError) as exc:
        raise IntraRunContractError("coverage: malformed foundation input") from exc
    governance = _object(root["governance"], {"kernel", "activation", "authority_gate", "human_amendment"}, "governance")
    _choice(governance["kernel"], ("FROZEN_V0_1",), "governance.kernel")
    _choice(governance["activation"], ("NOT_ACTIVATED",), "governance.activation")
    _choice(governance["authority_gate"], ("KERNEL_AMENDMENT_REQUIRED",), "governance.authority_gate")
    if governance["human_amendment"] is not None:
        _source(governance["human_amendment"], "governance.human_amendment")
    blocks = []
    if not context["opt_in"]["enabled"]:
        blocks.append("HUMAN_OPT_IN_REQUIRED")
    if context["lease_state"] != "LIVE":
        blocks.append("LIVE_SAME_EXECUTOR_LEASE_REQUIRED")
    if not context["authority_current"]:
        blocks.append("AUTHORITY_CHANGED_OR_UNKNOWN")
    if not checkpoint.committed or not checkpoint.clean:
        blocks.append("COMMITTED_CLEAN_CANDIDATE_REQUIRED")
    complete = all(item.complete and item.stable and not item.conflicting and item.validity == "VALID"
                   and item.outcome in ("PASS", "FAIL") for item in checkpoint.evidence)
    failed = {item.obligation.id for item in feedback.items if item.outcome == "FAIL"}
    candidate_failed = {item.id for item in coverage.contract.obligations if item.kind == "candidate"} & failed
    if not complete or not candidate_failed:
        blocks.append("COMPLETE_STABLE_FAILED_CANDIDATE_PROOF_REQUIRED")
    if not set(continuation.changed_paths).issubset(context["allowed_paths"]):
        blocks.append("SCOPE_ESCAPE")
    if continuation.risk != "UNCHANGED":
        blocks.append("HUMAN_RISK_DECISION_REQUIRED")
    if continuation.semantic_choice != "UNCHANGED":
        blocks.append("BRAIN_SEMANTIC_DECISION_REQUIRED")
    if (continuation.target_candidate_sha in (binding.candidate_sha, binding.base_sha, *context["seen_candidate_shas"])
            or continuation.progress != "ESTABLISHED" or not continuation.causal_obligation_ids
            or not set(continuation.causal_obligation_ids).issubset(candidate_failed)):
        blocks.append("FINITE_CAUSAL_PROGRESS_REQUIRED")
    limits = _resources(context["limits"], "context.limits")
    spent = _resources(context["spent"], "context.spent", spent=True)
    if spent.corrections != checkpoint.ordinal - 1:
        blocks.append("CORRECTION_COUNT_HISTORY_CONFLICT")
    if any(used + requested > limit for used, requested, limit in
           zip(asdict(spent).values(), asdict(continuation.requested).values(), asdict(limits).values())):
        blocks.append("BUDGET_EXHAUSTED")
    # No canonical authentication can be established by a pure caller-data parser.
    mandatory = ("CANONICAL_PROVENANCE_NOT_AUTHENTICATED", "KERNEL_AMENDMENT_REQUIRED", "NOT_ACTIVATED")
    if governance["human_amendment"] is None:
        blocks.append("HUMAN_AMENDMENT_AUTHORITY_MISSING")
    description = ContinuationDescription(not any(item != "HUMAN_AMENDMENT_AUTHORITY_MISSING" for item in blocks),
                                          tuple(blocks) + mandatory)
    return IntraRunDeclaration(SCHEMA, binding, delegation, coverage, checkpoint, feedback,
                               continuation, applicability, description)


def describe_lifecycle(data: object, *, expected_lineage: object) -> LifecycleDescription:
    """Describe frozen/local versus proposed/terminal routes without mutating a RUN."""
    root = _object(data, {"schema", "lineage", "request", "next_run_id"}, "lifecycle")
    _choice(root["schema"], (LIFECYCLE_SCHEMA,), "lifecycle.schema")
    fields = {"task_id", "task_revision", "task_source_sha", "admission", "run_id", "run_state",
              "base_sha", "candidate_sha", "terminal_kind", "terminal_ref", "terminal_digest"}
    lineage = _object(root["lineage"], fields, "lifecycle.lineage")
    expected = _object(expected_lineage, fields, "expected_lineage")
    for value in (lineage, expected):
        _id(value["task_id"], "lineage.task_id")
        _number(value["task_revision"], 2_147_483_647, "lineage.task_revision")
        _match(value["task_source_sha"], _SHA, "lineage.task_source_sha")
        _choice(value["admission"], ("ADMITTED", "NOT_ADMITTED"), "lineage.admission")
        _choice(value["run_state"], ("NONE", "ACTIVE", "RESULT", "FAILURE"), "lineage.run_state")
        if value["terminal_kind"] is not None:
            _choice(value["terminal_kind"], ("RESULT", "FAILURE"), "lineage.terminal_kind")
        for key, pattern in (("run_id", _ID), ("base_sha", _SHA), ("candidate_sha", _SHA),
                             ("terminal_ref", _ID), ("terminal_digest", _DIGEST)):
            if value[key] is not None:
                _match(value[key], pattern, f"lineage.{key}")
        if value["admission"] == "NOT_ADMITTED":
            if value["run_state"] != "NONE" or any(value[key] is not None for key in
                ("run_id", "base_sha", "candidate_sha", "terminal_kind", "terminal_ref", "terminal_digest")):
                raise IntraRunContractError("lineage: pre-AIOS failure cannot fabricate RUN/FAILURE")
        elif value["run_id"] is None or value["base_sha"] is None or value["run_state"] == "NONE":
            raise IntraRunContractError("lineage: admitted RUN identity required")
        elif value["run_state"] == "ACTIVE":
            if any(value[key] is not None for key in ("terminal_kind", "terminal_ref", "terminal_digest")):
                raise IntraRunContractError("lineage: active RUN cannot impersonate terminal")
        elif (value["terminal_kind"] != value["run_state"] or value["terminal_ref"] is None
              or value["terminal_digest"] is None):
            raise IntraRunContractError("lineage: exactly one bound immutable terminal required")
        if value["run_state"] == "RESULT" and value["candidate_sha"] is None:
            raise IntraRunContractError("lineage: RESULT requires its exact candidate subject")
    if lineage != expected:
        raise IntraRunContractError("lifecycle: stale or impersonated canonical lineage")
    request = _choice(root["request"], ("EXECUTOR_LOCAL_ITERATION", "PRETERMINAL_OBSERVATION",
                      "TERMINAL_RESULT", "TERMINAL_FAILURE", "AUTHOR_REPAIR"), "lifecycle.request")
    next_run = None if root["next_run_id"] is None else _id(root["next_run_id"], "lifecycle.next_run_id")
    if request != "AUTHOR_REPAIR" and next_run is not None:
        raise IntraRunContractError("lifecycle: hidden new RUN is forbidden")
    if lineage["admission"] == "NOT_ADMITTED":
        return LifecycleDescription("BLOCK", "NO_CANONICAL_RUN", "PRE_AIOS_OPERATIONAL_FAILURE_ONLY")
    if request == "AUTHOR_REPAIR":
        if lineage["run_state"] != "FAILURE" or next_run is None or next_run == lineage["run_id"]:
            return LifecycleDescription("BLOCK", "TERMINAL_EXCLUSION", "BOUND_FAILURE_AND_DISTINCT_REPAIR_RUN_REQUIRED")
        return LifecycleDescription("AUTHOR_REPAIR", "EXISTING_CORRECTION_ROUTE", "SEPARATE_AUTHORING_AND_ADMISSION_REQUIRED")
    if lineage["run_state"] != "ACTIVE":
        return LifecycleDescription("BLOCK", "TERMINAL_EXCLUSION", "TERMINAL_RUN_CANNOT_REOPEN_OR_EMIT_AGAIN")
    if request == "EXECUTOR_LOCAL_ITERATION":
        return LifecycleDescription("EXECUTOR_LOCAL_ITERATION", "FROZEN_KERNEL_SECTION_5", "EXECUTOR_OWNS_HOW_WITHIN_ADMITTED_SCOPE")
    if request == "PRETERMINAL_OBSERVATION":
        return LifecycleDescription("BLOCK", "KERNEL_AMENDMENT_REQUIRED", "RUNTIME_ORCHESTRATION_NOT_ACTIVATED")
    return LifecycleDescription("TERMINAL_ONCE", "EXISTING_TERMINAL_ROUTE", "RUNTIME_MUST_ESTABLISH_EXACT_RESULT_OR_FAILURE")

# L3-02 subordinate immutable records. These codecs are deliberately pure;
# only RuntimeCompletion can authenticate, persist or consume a record.
RUNTIME_CHECKPOINT_FORMAT = "AIOS_L3_RUNTIME_CHECKPOINT"
CHECKPOINT_RESOURCE_LIMITS = dict(checkpoints=17, executions=256,
                                 raw_bytes=32 * 1024 * 1024, elapsed_ms=3_600_000)


@dataclass(frozen=True, slots=True)
class RuntimeCheckpoint:
    content_json: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content_json.encode("ascii")).hexdigest()

    @property
    def data(self) -> dict:
        # A detached projection cannot mutate the sealed record.
        return json.loads(self.content_json)


@dataclass(frozen=True, slots=True)
class TerminalProofDecision:
    eligible: bool
    reason: str
    checkpoint_digest: str
    candidate_sha: str
    # Inspection only: completion always independently observes the final K_i.
    execution_permission: str = "NOT_AUTHORIZED"
    production_activation: str = "NOT_ACTIVATED"


def checkpoint_resource_facts(value: object) -> dict:
    from .verification_contract import VerificationContractError, _app_exact
    data = dict(_app_exact(value, CHECKPOINT_RESOURCE_LIMITS))
    for name, limit in CHECKPOINT_RESOURCE_LIMITS.items():
        if type(data[name]) is not int or not 0 <= data[name] <= limit:
            raise VerificationContractError("unknown or exhausted checkpoint accounting: " + name)
    return data


def checkpoint_population_identity(plan) -> str:
    """Retain original mandatory semantics across current-candidate review pins.

    Only the two candidate-specific locators are erased. Every claim, purpose,
    condition, mapping assignment and original TASK identity remains exact.
    """
    from .verification_contract import scheduler_plan_data, verification_digest
    data = scheduler_plan_data(plan)["coverage"]
    data = json.loads(json.dumps(data))
    for row in data["contract"]["obligations"] + data["mapping"]["proofs"]:
        row["conditions"]["candidate_sha"] = None
        row["conditions"]["test_ref"] = "L3_CURRENT_REVIEWED_EXECUTION_LOCATOR"
    # Digests depend on the candidate-specific locators erased above.
    data["mapping"]["contract_digest"] = None
    for entry in data["mapping"]["entries"]:
        entry["obligation_digest"] = None
    return verification_digest(data)


def format_runtime_checkpoint(value: RuntimeCheckpoint) -> bytes:
    if type(value) is not RuntimeCheckpoint:
        from .verification_contract import VerificationContractError
        raise VerificationContractError("exact typed checkpoint required")
    content = value.content_json.encode("ascii")
    parse_runtime_checkpoint(content)
    return content


def parse_runtime_checkpoint(content: bytes) -> RuntimeCheckpoint:
    """Closed inspection codec; a caller-built record has no sealing authority."""
    from .verification_contract import (
        VerificationContractError, _reuse_decode, _app_exact, _original_hash,
        _original_json, parse_checkpoint_request, parse_scheduler_plan,
    )
    data = _app_exact(_reuse_decode(content), ("format", "version", "request", "plan", "population_identity", "execution_identity",
        "evidence", "proofs", "outstanding", "affected", "resources_before", "resources_after"))
    if data["format"] != RUNTIME_CHECKPOINT_FORMAT or type(data["version"]) is not int or data["version"] != 1:
        raise VerificationContractError("invalid runtime checkpoint format")
    request = parse_checkpoint_request(_original_json(data["request"]).encode("ascii"))
    plan = parse_scheduler_plan(_original_json(data["plan"]).encode("ascii"))
    if plan.request != request.scheduler or plan.authentication != "AUTHENTICATED":
        raise VerificationContractError("checkpoint plan substitution")
    _original_hash(data["population_identity"])
    _original_hash(data["execution_identity"])
    if checkpoint_population_identity(plan) != data["population_identity"]:
        raise VerificationContractError("checkpoint mandatory population substitution")
    before, after = (checkpoint_resource_facts(data[key]) for key in ("resources_before", "resources_after"))
    if (before["checkpoints"] != request.ordinal - 1 or after["checkpoints"] != request.ordinal
            or any(after[k] < before[k] for k in before)):
        raise VerificationContractError("checkpoint resource history contradiction")
    if (type(data["evidence"]) is not list or not 0 < len(data["evidence"]) <= MAX_PROOFS
            or type(data["proofs"]) is not list or not 0 < len(data["proofs"]) <= MAX_PROOFS):
        raise VerificationContractError("checkpoint proof population bound")
    from .artifacts import validate_evidence
    for item in data["evidence"]:
        _app_exact(item, ("evidence_id", "run_id", "subject_sha", "type", "source", "result", "raw", "verification"))
        _app_exact(item["source"], ("command",))
        _app_exact(item["result"], ("exit_code", "summary"))
        _app_exact(item["raw"], ("path",))
        _app_exact(item["verification"], ("policy", "disposition", "binding", "candidate", "candidate_digest",
            "evidence_id", "run_id", "raw_path", "raw_digest"))
    evidence = [validate_evidence(item) for item in data["evidence"]]
    ids = [item.evidence_id for item in evidence]
    if (len(set(ids)) != len(ids) or any((e.run_id, e.subject_sha) != (
            request.scheduler.target.run_id, request.scheduler.subject_sha) for e in evidence)
            or ids != [f"{request.scheduler.target.run_id}-V{before['executions'] + i + 1:03d}" for i in range(len(evidence))]
            or after["executions"] - before["executions"] != len(evidence)
            or after["elapsed_ms"] <= before["elapsed_ms"]):
        raise VerificationContractError("checkpoint original execution identity conflict")
    units = {u["proof_id"]: u for u in data["plan"]["units"]}
    seen, outstanding = [], []
    used_evidence = []
    execution_population = []
    for fact in data["proofs"]:
        _app_exact(fact, ("proof_id", "unit_id", "evidence_id", "evidence_sha256", "raw_sha256", "raw_size",
            "native_sha256", "native_size", "manifest_sha256", "complete", "stable", "outcome", "satisfactory", "source_run_id", "subject_sha"))
        unit = units.get(fact["proof_id"])
        if (unit is None or unit["unit_id"] != fact["unit_id"] or fact["evidence_id"] not in ids
                or fact["complete"] is not True or fact["stable"] is not True
                or type(fact["outcome"]) is not str or fact["outcome"] not in {"PASS", "FAIL", "ERROR"}
                or type(fact["satisfactory"]) is not bool or fact["satisfactory"] != (fact["outcome"] == "PASS")
                or (fact["source_run_id"], fact["subject_sha"]) != (
                    request.scheduler.target.run_id, request.scheduler.subject_sha)
                or any(type(fact[key]) is not int or not 0 < fact[key] <= CHECKPOINT_RESOURCE_LIMITS["raw_bytes"]
                       for key in ("raw_size", "native_size"))):
            raise VerificationContractError("checkpoint proof fact substitution")
        for key in ("evidence_sha256", "raw_sha256", "native_sha256", "manifest_sha256"):
            _original_hash(fact[key])
        item = data["evidence"][ids.index(fact["evidence_id"])]
        if hashlib.sha256(_original_json(item).encode("ascii")).hexdigest() != fact["evidence_sha256"]:
            raise VerificationContractError("checkpoint evidence substitution")
        candidate = item["verification"]["candidate"]
        execution_population.append(dict(proof_id=fact["proof_id"], command=item["source"]["command"],
            profile=candidate["profile"], toolchain=candidate["toolchain"], collection=candidate.get("collection")))
        seen.append(fact["proof_id"])
        used_evidence.append(fact["evidence_id"])
        if not fact["satisfactory"]:
            outstanding.extend(unit["obligation_ids"])
    from .verification_contract import verification_digest
    if (data["execution_identity"] != verification_digest(execution_population)
            or seen != sorted(units) or len(set(used_evidence)) != len(used_evidence) or set(used_evidence) != set(ids)
            or data["outstanding"] != sorted(set(outstanding)) or data["affected"] != []
            or after["raw_bytes"] - before["raw_bytes"] != sum(p["raw_size"] + p["native_size"] for p in data["proofs"])):
        raise VerificationContractError("checkpoint incomplete coverage or accounting")
    return RuntimeCheckpoint(content.decode("ascii"))


# L3-03 codecs are inspection data, just like K_i. Only the existing
# RuntimeCompletion owner authenticates native roots and prepares durable F_i.
FACTUAL_FEEDBACK_REQUEST_FORMAT = "AIOS_L3_FACTUAL_FEEDBACK_REQUEST"
RUNTIME_FEEDBACK_FORMAT = "AIOS_L3_FACTUAL_FEEDBACK"
FEEDBACK_AUTHORITY = dict(
    dispatch="NOT_AUTHORIZED", delivery="NOT_AUTHORIZED", ack_consumption="NOT_AUTHORIZED",
    c2_admission="NOT_AUTHORIZED", continuation="NOT_AUTHORIZED", executor_selection="NOT_AUTHORIZED",
    correction_strategy="NOT_AUTHORIZED", policy_selection="NOT_AUTHORIZED", new_run="NOT_AUTHORIZED",
    production_activation="NOT_ACTIVATED", live_executor_lease="NOT_ESTABLISHED",
    human_risk_acceptance="NOT_ESTABLISHED", human_budget="NOT_ESTABLISHED",
)
_FEEDBACK_REASONS = {
    "PREPARED": {"COMPLETE_STABLE_NATIVE_CANDIDATE_FAIL"},
    "NO_FAILED_PROOF": {"COMPLETE_STABLE_NATIVE_PASS"},
    "BLOCKED": {"NATIVE_PROOF_UNRESOLVED", "NATIVE_CHECKPOINT_NOT_ESTABLISHED",
                "CORRECTION_NATIVE_AUTHORITY_NOT_ESTABLISHED", "OPERATION_NOT_SUPPORTED"},
}


@dataclass(frozen=True, slots=True)
class FactualFeedbackRequest:
    version: int
    checkpoint: ProofCheckpointRequest
    checkpoint_digest: str

    def __post_init__(self):
        from .verification_contract import VerificationContractError, format_checkpoint_request, _original_hash
        if type(self.version) is not int or self.version != 1:
            raise VerificationContractError("invalid factual feedback request version")
        format_checkpoint_request(self.checkpoint)
        _original_hash(self.checkpoint_digest)


def factual_feedback_request_data(value: FactualFeedbackRequest) -> dict:
    from .verification_contract import VerificationContractError, checkpoint_request_data
    if type(value) is not FactualFeedbackRequest:
        raise VerificationContractError("exact typed factual feedback request required")
    value.__post_init__()
    return dict(format=FACTUAL_FEEDBACK_REQUEST_FORMAT, version=value.version,
                checkpoint=checkpoint_request_data(value.checkpoint), checkpoint_digest=value.checkpoint_digest)


def format_factual_feedback_request(value: FactualFeedbackRequest) -> bytes:
    from .verification_contract import _original_json
    return _original_json(factual_feedback_request_data(value)).encode("ascii")


def parse_factual_feedback_request(content: bytes) -> FactualFeedbackRequest:
    from .verification_contract import VerificationContractError, _reuse_decode, _app_exact, _original_json, parse_checkpoint_request
    data = _app_exact(_reuse_decode(content), ("format", "version", "checkpoint", "checkpoint_digest"))
    if data["format"] != FACTUAL_FEEDBACK_REQUEST_FORMAT:
        raise VerificationContractError("invalid factual feedback request format")
    return FactualFeedbackRequest(data["version"],
        parse_checkpoint_request(_original_json(data["checkpoint"]).encode("ascii")), data["checkpoint_digest"])


@dataclass(frozen=True, slots=True)
class RuntimeFactualFeedback:
    content_json: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content_json.encode("ascii")).hexdigest()

    @property
    def data(self) -> dict:
        return json.loads(self.content_json)


def format_runtime_feedback(value: RuntimeFactualFeedback) -> bytes:
    from .verification_contract import VerificationContractError
    if type(value) is not RuntimeFactualFeedback:
        raise VerificationContractError("exact typed runtime factual feedback required")
    content = value.content_json.encode("ascii")
    parse_runtime_feedback(content)
    return content


def _feedback_summary(proofs, obligations):
    outcomes = {p["outcome"] for p in proofs}
    if outcomes - {"PASS", "FAIL"} or not all(p["complete"] and p["stable"] for p in proofs):
        status, reason = "BLOCKED", "NATIVE_PROOF_UNRESOLVED"
    elif "FAIL" in outcomes:
        status, reason = "PREPARED", "COMPLETE_STABLE_NATIVE_CANDIDATE_FAIL"
    else:
        status, reason = "NO_FAILED_PROOF", "COMPLETE_STABLE_NATIVE_PASS"
    affected = [o for o in obligations if o["outcome"] == "FAIL"]
    outstanding = [o for o in obligations if o["outcome"] != "PASS"]
    return dict(status=status, reason=reason,
        acceptance_ids=sorted({a for o in obligations for a in o["acceptance_ids"]}),
        affected_obligation_ids=sorted(o["obligation_id"] for o in affected),
        affected_acceptance_ids=sorted({a for o in affected for a in o["acceptance_ids"]}),
        outstanding_obligation_ids=sorted(o["obligation_id"] for o in outstanding),
        outstanding_acceptance_ids=sorted({a for o in outstanding for a in o["acceptance_ids"]}))


def _blocked_runtime_feedback(reason: str) -> RuntimeFactualFeedback:
    from .verification_contract import _original_json
    data = dict(format=RUNTIME_FEEDBACK_FORMAT, version=1, status="BLOCKED", reason=reason,
        feedback_id=None, binding=None, proofs=[], obligations=[], resources=None,
        acceptance_ids=[], affected_obligation_ids=[], affected_acceptance_ids=[],
        outstanding_obligation_ids=[], outstanding_acceptance_ids=[], authority=dict(FEEDBACK_AUTHORITY))
    return parse_runtime_feedback(_original_json(data).encode("ascii"))


def _checkpoint_factual_feedback(record: RuntimeCheckpoint, executor: str) -> RuntimeFactualFeedback:
    """Deterministic projection, never native authentication or permission.

    No raw text, nodeids, commands, filesystem/user paths, claim prose or
    implementation advice crosses this boundary. Exact identities remain pinned.
    """
    from .verification_contract import verification_digest, _original_json
    data = record.data
    request, plan = data["request"], data["plan"]
    scheduler = request["scheduler"]
    target = scheduler["target"]
    binding = dict(task_id=target["task_id"], task_revision=target["task_revision"], run_id=target["run_id"],
        operation=scheduler["operation"], base_sha=target["base_sha"], candidate_sha=target["candidate_sha"],
        candidate_tree_sha=target["tree_sha"], admitted_executor=executor, admitted_run_sha256=target["run_sha256"],
        task_sha256=plan["authority"]["target"]["task_sha256"],
        authorization_sha256=verification_digest(dict(operation=scheduler["operation"],
            admitted_run_sha256=target["run_sha256"], reviewed_authority=plan["authority"])),
        plan_sha256=verification_digest(plan), contract=scheduler["contract"], mapping=scheduler["mapping"],
        checkpoint_ordinal=request["ordinal"], predecessor_digest=request["predecessor_digest"], checkpoint_digest=record.digest,
        population_identity=data["population_identity"], execution_identity=data["execution_identity"],
        currentness="EXACT_LATEST_AT_PREPARATION")
    units = {u["proof_id"]: u for u in plan["units"]}
    evidence = {e["evidence_id"]: e for e in data["evidence"]}
    proofs = []
    for fact in data["proofs"]:
        item, unit = evidence[fact["evidence_id"]], units[fact["proof_id"]]
        observation = item["verification"]["candidate"]
        reports, exit_code = observation["reports"], item["result"]["exit_code"]
        # Exit 1 alone includes fixture/collection failures. A decisive candidate
        # FAIL requires observed call failure, passing setup/teardown and no skip.
        if (any(r["phase"] != "call" and r["outcome"] == "FAIL" for r in reports)
                or exit_code not in (0, 1)):
            outcome = "ERROR"
        elif (exit_code == 1 and any(r["phase"] == "call" and r["outcome"] == "FAIL" for r in reports)
                and all(r["outcome"] == "PASS" if r["phase"] != "call" else r["outcome"] in ("PASS", "FAIL") for r in reports)):
            outcome = "FAIL"
        elif exit_code == 0 and all(r["outcome"] == "PASS" for r in reports):
            outcome = "PASS"
        else:
            outcome = "UNKNOWN"
        if not fact["complete"] or not fact["stable"] or observation.get("errors") != []:
            outcome = "UNKNOWN"
        order = int(fact["evidence_id"].rsplit("-V", 1)[1])
        proofs.append(dict(**{key: fact[key] for key in ("proof_id", "unit_id", "evidence_id", "evidence_sha256",
            "raw_sha256", "raw_size", "native_sha256", "native_size", "manifest_sha256", "source_run_id", "subject_sha",
            "complete", "stable")}, outcome=outcome, checkpoint_outcome=fact["outcome"],
            proof_sha256=unit["proof_sha256"], conditions_sha256=verification_digest(unit["proof"]["conditions"]),
            source_command_sha256=verification_digest(item["source"]["command"]),
            observation_sha256=item["verification"]["candidate_digest"],
            raw_locator=f"l3-02/k{request['ordinal']:03d}/{fact['evidence_id']}.raw",
            native_locator=f"l3-02/k{request['ordinal']:03d}/observation-{order:03d}.json"))
    by_proof = {p["proof_id"]: p for p in proofs}
    obligations = []
    for row in plan["obligations"]:
        obligation, proof = row["obligation"], by_proof[row["proof_id"]]
        obligations.append(dict(obligation_id=obligation["id"], revision=obligation["revision"],
            obligation_sha256=row["obligation_sha256"], claim_id=obligation["claim"]["id"],
            claim_sha256=verification_digest(obligation["claim"]), kind=obligation["kind"], blocking=obligation["blocking"],
            conditions_sha256=verification_digest(obligation["conditions"]),
            provenance_sha256=verification_digest(obligation["provenance"]), acceptance_ids=sorted(obligation["acceptance_ids"]),
            proof_id=proof["proof_id"], unit_id=proof["unit_id"], outcome=proof["outcome"],
            complete=proof["complete"], stable=proof["stable"]))
    result = dict(format=RUNTIME_FEEDBACK_FORMAT, version=1, feedback_id=verification_digest(binding), binding=binding,
        proofs=proofs, obligations=obligations, resources=data["resources_after"], authority=dict(FEEDBACK_AUTHORITY),
        **_feedback_summary(proofs, obligations))
    return parse_runtime_feedback(_original_json(result).encode("ascii"))


def parse_runtime_feedback(content: bytes) -> RuntimeFactualFeedback:
    """Strict bounded inspection codec. Parsed PREPARED is never authority."""
    from .verification_contract import VerificationContractError, _reuse_decode, _app_exact, _original_hash, verification_digest
    data = _app_exact(_reuse_decode(content), ("format", "version", "status", "reason", "feedback_id", "binding",
        "proofs", "obligations", "resources", "acceptance_ids", "affected_obligation_ids", "affected_acceptance_ids",
        "outstanding_obligation_ids", "outstanding_acceptance_ids", "authority"))
    def require(condition):
        if not condition:
            raise VerificationContractError("invalid bounded factual feedback")
    def identity(value):
        require(type(value) is str and _ID.fullmatch(value) is not None and value.lower() not in _COMMAND_NAMES)
    def identities(values, *, empty=True):
        require(type(values) is list and len(values) <= MAX_PROOFS and (empty or bool(values)))
        for value in values:
            identity(value)
        require(values == sorted(set(values)))
    require(data["format"] == RUNTIME_FEEDBACK_FORMAT and type(data["version"]) is int and data["version"] == 1)
    require(type(data["status"]) is str and data["status"] in _FEEDBACK_REASONS
            and type(data["reason"]) is str and data["reason"] in _FEEDBACK_REASONS[data["status"]])
    authority = _app_exact(data["authority"], FEEDBACK_AUTHORITY)
    require(authority == FEEDBACK_AUTHORITY)
    for key in ("acceptance_ids", "affected_obligation_ids", "affected_acceptance_ids", "outstanding_obligation_ids", "outstanding_acceptance_ids"):
        identities(data[key])
    require(type(data["proofs"]) is list and len(data["proofs"]) <= MAX_PROOFS
            and type(data["obligations"]) is list and len(data["obligations"]) <= MAX_PROOFS)
    if data["binding"] is None:
        require(data["status"] == "BLOCKED" and data["reason"] != "NATIVE_PROOF_UNRESOLVED"
                and data["feedback_id"] is None and data["resources"] is None
                and all(data[key] == [] for key in ("proofs", "obligations", "acceptance_ids", "affected_obligation_ids",
                    "affected_acceptance_ids", "outstanding_obligation_ids", "outstanding_acceptance_ids")))
        return RuntimeFactualFeedback(content.decode("ascii"))
    binding = _app_exact(data["binding"], ("task_id", "task_revision", "run_id", "operation", "base_sha", "candidate_sha",
        "candidate_tree_sha", "admitted_executor", "admitted_run_sha256", "task_sha256", "authorization_sha256", "plan_sha256",
        "contract", "mapping", "checkpoint_ordinal", "predecessor_digest", "checkpoint_digest", "population_identity",
        "execution_identity", "currentness"))
    require(type(binding["task_id"]) is str and re.fullmatch(r"TASK-[A-Za-z0-9_-]{1,75}", binding["task_id"]) is not None)
    require(type(binding["run_id"]) is str and re.fullmatch(re.escape("RUN-" + binding["task_id"][5:] + "-") + r"[0-9]{3,}", binding["run_id"]) is not None)
    identity(binding["run_id"])
    require(type(binding["task_revision"]) is int and 1 <= binding["task_revision"] <= 2_147_483_647
            and type(binding["checkpoint_ordinal"]) is int and 1 <= binding["checkpoint_ordinal"] <= 17
            and binding["operation"] == "PRIMARY" and binding["admitted_executor"] in ("codex", "antigravity", "antigravity-minimax")
            and binding["currentness"] == "EXACT_LATEST_AT_PREPARATION")
    for key in ("base_sha", "candidate_sha", "candidate_tree_sha"):
        _original_hash(binding[key], 40)
    require(binding["candidate_sha"] != binding["base_sha"])
    for key in ("admitted_run_sha256", "task_sha256", "authorization_sha256", "plan_sha256", "checkpoint_digest", "population_identity", "execution_identity"):
        _original_hash(binding[key])
    if binding["checkpoint_ordinal"] == 1:
        require(binding["predecessor_digest"] is None)
    else:
        _original_hash(binding["predecessor_digest"])
    for key in ("contract", "mapping"):
        pin = _app_exact(binding[key], ("id", "revision", "digest"))
        identity(pin["id"])
        require(type(pin["revision"]) is int and 1 <= pin["revision"] <= 2_147_483_647)
        _original_hash(pin["digest"])
    require(data["feedback_id"] == verification_digest(binding))
    checkpoint_resource_facts(data["resources"])
    require(data["resources"]["checkpoints"] == binding["checkpoint_ordinal"]
            and data["resources"]["executions"] >= binding["checkpoint_ordinal"]
            and data["resources"]["elapsed_ms"] > 0 and bool(data["proofs"]) and bool(data["obligations"]))
    by_proof, evidence_ids = {}, set()
    for proof in data["proofs"]:
        _app_exact(proof, ("proof_id", "unit_id", "evidence_id", "evidence_sha256", "raw_sha256", "raw_size", "native_sha256",
            "native_size", "manifest_sha256", "source_run_id", "subject_sha", "complete", "stable", "outcome", "checkpoint_outcome",
            "proof_sha256", "conditions_sha256", "source_command_sha256", "observation_sha256", "raw_locator", "native_locator"))
        identity(proof["proof_id"])
        require(type(proof["evidence_id"]) is str and re.fullmatch(re.escape(binding["run_id"]) + r"-V[0-9]{3}", proof["evidence_id"]) is not None)
        require(proof["proof_id"] not in by_proof and proof["evidence_id"] not in evidence_ids)
        order = int(proof["evidence_id"].rsplit("-V", 1)[1])
        require(1 <= order <= data["resources"]["executions"]
                and (proof["source_run_id"], proof["subject_sha"]) == (binding["run_id"], binding["candidate_sha"]))
        for key in ("unit_id", "evidence_sha256", "raw_sha256", "native_sha256", "manifest_sha256", "proof_sha256",
                    "conditions_sha256", "source_command_sha256", "observation_sha256"):
            _original_hash(proof[key])
        require(proof["unit_id"] == proof["proof_sha256"])
        for key in ("raw_size", "native_size"):
            require(type(proof[key]) is int and 0 < proof[key] <= CHECKPOINT_RESOURCE_LIMITS["raw_bytes"])
        require(type(proof["complete"]) is bool and type(proof["stable"]) is bool
                and type(proof["outcome"]) is str and proof["outcome"] in ("PASS", "FAIL", "ERROR", "UNKNOWN")
                and type(proof["checkpoint_outcome"]) is str and proof["checkpoint_outcome"] in ("PASS", "FAIL", "ERROR"))
        require(proof["outcome"] not in ("PASS", "FAIL") or (proof["complete"] and proof["stable"] and proof["checkpoint_outcome"] == proof["outcome"]))
        require(proof["raw_locator"] == f"l3-02/k{binding['checkpoint_ordinal']:03d}/{proof['evidence_id']}.raw"
                and proof["native_locator"] == f"l3-02/k{binding['checkpoint_ordinal']:03d}/observation-{order:03d}.json")
        by_proof[proof["proof_id"]] = proof
        evidence_ids.add(proof["evidence_id"])
    require(list(by_proof) == sorted(by_proof))
    require(data["resources"]["raw_bytes"] >= sum(p["raw_size"] + p["native_size"] for p in data["proofs"]))
    seen, used = [], set()
    for obligation in data["obligations"]:
        _app_exact(obligation, ("obligation_id", "revision", "obligation_sha256", "claim_id", "claim_sha256", "kind", "blocking",
            "conditions_sha256", "provenance_sha256", "acceptance_ids", "proof_id", "unit_id", "outcome", "complete", "stable"))
        for key in ("obligation_id", "claim_id", "proof_id"):
            identity(obligation[key])
        require(type(obligation["revision"]) is int and 1 <= obligation["revision"] <= 2_147_483_647
                and obligation["kind"] == "candidate" and type(obligation["blocking"]) is bool)
        for key in ("obligation_sha256", "claim_sha256", "conditions_sha256", "provenance_sha256", "unit_id"):
            _original_hash(obligation[key])
        identities(obligation["acceptance_ids"], empty=False)
        proof = by_proof.get(obligation["proof_id"])
        require(proof is not None and all(type(obligation[k]) is type(proof[k]) and obligation[k] == proof[k]
            for k in ("unit_id", "outcome", "complete", "stable", "conditions_sha256")))
        seen.append(obligation["obligation_id"])
        used.add(obligation["proof_id"])
    require(seen == sorted(set(seen)) and used == set(by_proof))
    summary = _feedback_summary(data["proofs"], data["obligations"])
    require(all(data[k] == v for k, v in summary.items()))
    return RuntimeFactualFeedback(content.decode("ascii"))


# L3-04 closed inspection codecs. These import the same canonical validators
# as K_i/F_i directly; no Runtime initialization or validator binding exists.
CONSUMPTION_REQUEST_FORMAT = "AIOS_L3_LOCAL_CONSUMPTION_REQUEST"
CONSUMPTION_FORMAT = "AIOS_L3_LOCAL_CONSUMPTION"
_CONSUMPTION_BLOCKS = frozenset({
    "STOP_LATCHED", "LIVE_INVOCATION_NOT_ESTABLISHED",
    "NATIVE_SOURCE_OR_EFFECT_NOT_ESTABLISHED", "NATIVE_PROOF_UNRESOLVED",
    "CORRECTION_NATIVE_AUTHORITY_NOT_ESTABLISHED", "CHECKPOINT_RESOURCE_LIMIT_REACHED",
    "CALLBACK_CONFIRMATION_NOT_ESTABLISHED",
})
_CONTINUATION_STOPS = frozenset({
    "NO_VERIFIED_COMMITTED_PROGRESS", "FINITE_HUMAN_OPT_IN_AND_TOKEN_BUDGET_NOT_ESTABLISHED",
    "CHECKPOINT_RESOURCE_LIMIT_REACHED", "SOURCE_OR_SESSION_NOT_ESTABLISHED",
})
_CONSUMPTION_BINDING = (
    "task_id", "task_revision", "run_id", "operation", "base_sha", "candidate_sha", "candidate_tree_sha",
    "admitted_executor", "admitted_run_sha256", "authorization_sha256", "checkpoint_ordinal",
    "checkpoint_digest", "feedback_id", "feedback_digest", "population_identity", "execution_identity",
    "runtime_identity", "invocation_identity", "profile_sha256",
)


@dataclass(frozen=True, slots=True)
class FeedbackConsumptionRequest:
    version: int
    source: FactualFeedbackRequest
    feedback_digest: str

    def __post_init__(self):
        from .verification_contract import VerificationContractError, _original_hash
        if type(self.version) is not int or self.version != 1:
            raise VerificationContractError("invalid local consumption version")
        format_factual_feedback_request(self.source)
        _original_hash(self.feedback_digest)


def format_feedback_consumption_request(value: FeedbackConsumptionRequest) -> bytes:
    from .verification_contract import VerificationContractError, _original_json
    if type(value) is not FeedbackConsumptionRequest:
        raise VerificationContractError("exact typed local consumption request required")
    value.__post_init__()
    return _original_json(dict(format=CONSUMPTION_REQUEST_FORMAT, version=value.version,
        source=factual_feedback_request_data(value.source), feedback_digest=value.feedback_digest)).encode("ascii")


def parse_feedback_consumption_request(content: bytes) -> FeedbackConsumptionRequest:
    from .verification_contract import VerificationContractError, _reuse_decode, _app_exact, _original_json
    data = _app_exact(_reuse_decode(content), ("format", "version", "source", "feedback_digest"))
    if data["format"] != CONSUMPTION_REQUEST_FORMAT:
        raise VerificationContractError("invalid local consumption request format")
    return FeedbackConsumptionRequest(data["version"],
        parse_factual_feedback_request(_original_json(data["source"]).encode("ascii")), data["feedback_digest"])


@dataclass(frozen=True, slots=True)
class LocalFeedbackConfirmation:
    """Exact adapter echo; accepted only as a direct in-invocation return."""
    receipt_id: str
    feedback_digest: str

    def __post_init__(self):
        from .verification_contract import _original_hash
        _original_hash(self.receipt_id)
        _original_hash(self.feedback_digest)


@dataclass(frozen=True, slots=True)
class RuntimeFeedbackConsumption:
    content_json: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content_json.encode("ascii")).hexdigest()

    @property
    def data(self) -> dict:
        return json.loads(self.content_json)


def format_feedback_consumption(value: RuntimeFeedbackConsumption) -> bytes:
    from .verification_contract import VerificationContractError
    if type(value) is not RuntimeFeedbackConsumption:
        raise VerificationContractError("exact typed local consumption required")
    content = value.content_json.encode("ascii")
    parse_feedback_consumption(content)
    return content


def parse_feedback_consumption(content: bytes) -> RuntimeFeedbackConsumption:
    from .verification_contract import (
        VerificationContractError, _reuse_decode, _app_exact, _original_hash, verification_digest,
    )
    data = _app_exact(_reuse_decode(content), ("format", "version", "status", "reason", "receipt_id", "binding",
        "acknowledgment", "lease_observation", "progress", "resources", "stop", "authority"))

    def require(condition):
        if not condition:
            raise VerificationContractError("invalid bounded local consumption")

    require(data["format"] == CONSUMPTION_FORMAT and type(data["version"]) is int and data["version"] == 1)
    require(_app_exact(data["authority"], FEEDBACK_AUTHORITY) == FEEDBACK_AUTHORITY)
    stop = _app_exact(data["stop"], ("status", "reason", "c2_admission", "human_opt_in", "token_budget"))
    require(stop["status"] == "STOP" and type(stop["reason"]) is str and stop["reason"] in _CONTINUATION_STOPS
            and stop["c2_admission"] == "NOT_AUTHORIZED" and stop["human_opt_in"] == "NOT_ESTABLISHED"
            and stop["token_budget"] == "NOT_ESTABLISHED")
    require(type(data["status"]) is str and type(data["reason"]) is str)
    if data["status"] == "BLOCKED":
        require(data["reason"] in _CONSUMPTION_BLOCKS and data["receipt_id"] is None and data["binding"] is None
                and data["acknowledgment"] == data["lease_observation"] == data["progress"] == "NOT_ESTABLISHED"
                and data["resources"] is None and stop["reason"] == "SOURCE_OR_SESSION_NOT_ESTABLISHED")
        return RuntimeFeedbackConsumption(content.decode("ascii"))
    require(data["status"] == "LOCAL_ACKNOWLEDGED" and data["reason"] == "SAME_INVOCATION_ADAPTER_CONFIRMED"
            and data["acknowledgment"] == "IN_INVOCATION_ADAPTER_CONFIRMED"
            and data["lease_observation"] == "CURRENT_DURING_LOCAL_CALLBACK"
            and data["progress"] in ("NOT_ESTABLISHED", "VERIFIED_SCOPED_DESCENDANT_PROGRESS"))
    binding = _app_exact(data["binding"], _CONSUMPTION_BINDING)
    require(type(binding["task_id"]) is str and re.fullmatch(r"TASK-[A-Za-z0-9_-]{1,75}", binding["task_id"]) is not None)
    require(type(binding["run_id"]) is str and len(binding["run_id"]) <= 128
            and re.fullmatch(re.escape("RUN-" + binding["task_id"][5:] + "-") + r"[0-9]{3,}", binding["run_id"]) is not None)
    require(type(binding["task_revision"]) is int and 1 <= binding["task_revision"] <= 2_147_483_647
            and type(binding["checkpoint_ordinal"]) is int and 1 <= binding["checkpoint_ordinal"] <= 17
            and binding["operation"] == "PRIMARY"
            and binding["admitted_executor"] in ("codex", "antigravity", "antigravity-minimax"))
    for key in ("base_sha", "candidate_sha", "candidate_tree_sha"):
        _original_hash(binding[key], 40)
    require(binding["candidate_sha"] != binding["base_sha"])
    for key in ("admitted_run_sha256", "authorization_sha256", "checkpoint_digest", "feedback_id", "feedback_digest",
                "population_identity", "execution_identity", "runtime_identity", "invocation_identity"):
        _original_hash(binding[key])
    if binding["profile_sha256"] is not None:
        _original_hash(binding["profile_sha256"])
    require(data["progress"] != "VERIFIED_SCOPED_DESCENDANT_PROGRESS" or binding["checkpoint_ordinal"] >= 2)
    require(data["receipt_id"] == verification_digest(binding))
    resources = checkpoint_resource_facts(data["resources"])
    require(resources["checkpoints"] == binding["checkpoint_ordinal"] and resources["executions"] >= resources["checkpoints"]
            and resources["raw_bytes"] > 0 and resources["elapsed_ms"] > 0)
    exhausted = any(resources[k] >= limit for k, limit in CHECKPOINT_RESOURCE_LIMITS.items())
    expected_stop = ("CHECKPOINT_RESOURCE_LIMIT_REACHED" if exhausted else
        "NO_VERIFIED_COMMITTED_PROGRESS" if data["progress"] == "NOT_ESTABLISHED" else
        "FINITE_HUMAN_OPT_IN_AND_TOKEN_BUDGET_NOT_ESTABLISHED")
    require(stop["reason"] == expected_stop)
    return RuntimeFeedbackConsumption(content.decode("ascii"))


def _consumption_result(*, reason=None, binding=None, resources=None, progress="NOT_ESTABLISHED"):
    from .verification_contract import _original_json, verification_digest
    acknowledged = binding is not None
    exhausted = resources is not None and any(resources[k] >= v for k, v in CHECKPOINT_RESOURCE_LIMITS.items())
    stop_reason = ("SOURCE_OR_SESSION_NOT_ESTABLISHED" if not acknowledged else
        "CHECKPOINT_RESOURCE_LIMIT_REACHED" if exhausted else
        "NO_VERIFIED_COMMITTED_PROGRESS" if progress == "NOT_ESTABLISHED" else
        "FINITE_HUMAN_OPT_IN_AND_TOKEN_BUDGET_NOT_ESTABLISHED")
    return parse_feedback_consumption(_original_json(dict(format=CONSUMPTION_FORMAT, version=1,
        status="LOCAL_ACKNOWLEDGED" if acknowledged else "BLOCKED",
        reason="SAME_INVOCATION_ADAPTER_CONFIRMED" if acknowledged else reason,
        receipt_id=verification_digest(binding) if acknowledged else None, binding=binding,
        acknowledgment="IN_INVOCATION_ADAPTER_CONFIRMED" if acknowledged else "NOT_ESTABLISHED",
        lease_observation="CURRENT_DURING_LOCAL_CALLBACK" if acknowledged else "NOT_ESTABLISHED",
        progress=progress, resources=resources, stop=dict(status="STOP", reason=stop_reason,
            c2_admission="NOT_AUTHORIZED", human_opt_in="NOT_ESTABLISHED", token_budget="NOT_ESTABLISHED"),
        authority=dict(FEEDBACK_AUTHORITY))).encode("ascii"))


@dataclass(frozen=True, slots=True)
class NativePrimaryCheckpoint:
    """V3 original-RUN observation, distinct from historical reviewed L3 K_i.

    A codec supplies no authority. Only its original live Runtime can seal or
    authenticate it. Native observer evidence keeps its actual source subject.
    """
    content_json: str

    @property
    def digest(self):
        return hashlib.sha256(self.content_json.encode("ascii")).hexdigest()

    @property
    def data(self):
        return json.loads(self.content_json)


@dataclass(frozen=True, slots=True)
class NativeOperationCheckpoint(NativePrimaryCheckpoint):
    """Prospective default K_i, explicitly bound to the original operation."""


def parse_native_primary_checkpoint(content: bytes) -> NativePrimaryCheckpoint:
    return _parse_native_checkpoint(content, default=False)


def parse_native_operation_checkpoint(content: bytes) -> NativeOperationCheckpoint:
    return _parse_native_checkpoint(content, default=True)


def _parse_native_checkpoint(content: bytes, *, default):
    from .verification_contract import _reuse_decode, _app_exact, _original_hash, _original_json, VerificationContractError
    from .artifacts import validate_evidence
    data = _app_exact(_reuse_decode(content), ("format", "version", "ordinal", "predecessor_digest",
        "binding", "package_sha256", "population_identity", "evidence", "proofs", "failed_identities"))
    if _original_json(data).encode("ascii") != content:
        raise VerificationContractError("KA01_NATIVE_CHECKPOINT_NONCANONICAL")
    expected_format = "AIOS_KA01_NATIVE_OPERATION_CHECKPOINT" if default else "AIOS_KA01_NATIVE_PRIMARY_CHECKPOINT"
    if (data["format"] != expected_format or type(data["version"]) is not int
            or data["version"] not in ((1, 2) if default else (1,))
            or type(data["ordinal"]) is not int or not 1 <= data["ordinal"] <= 256):
        raise VerificationContractError("KA01_NATIVE_CHECKPOINT_FORMAT")
    if data["predecessor_digest"] is not None:
        _original_hash(data["predecessor_digest"])
    for key in ("package_sha256", "population_identity"):
        _original_hash(data[key])
    binding = _app_exact(data["binding"], ("task", "run_id", "base_sha", "candidate_sha", "tree_sha",
        "admitted_run_sha256", "authority_sha256", "execution_profile_sha256", "session", "turn",
        "commands_sha256", "scope_sha256", *(("operation", "operation_source_sha256", "completion_policy_sha256") if default else ()),
        *(("original_proof",) if data["version"] == 2 else ())))
    if data["version"] == 2:
        from .verification_contract import validate_kernel_original_proof
        validate_kernel_original_proof(binding["original_proof"])
    if default:
        if binding["operation"] not in {"PRIMARY", "REPAIR", "REMEDIATION"}:
            raise VerificationContractError("KA01_NATIVE_CHECKPOINT_OPERATION")
        for key in ("operation_source_sha256", "completion_policy_sha256"):
            _original_hash(binding[key])
    for key in ("admitted_run_sha256", "authority_sha256", "execution_profile_sha256", "commands_sha256", "scope_sha256"):
        _original_hash(binding[key])
    for key in ("base_sha", "candidate_sha", "tree_sha"):
        if type(binding[key]) is not str or re.fullmatch(r"[0-9a-f]{40}", binding[key]) is None:
            raise VerificationContractError("KA01_NATIVE_CHECKPOINT_SOURCE")
    session = _app_exact(binding["session"], ("connection_id", "process_id", "thread_id", "binary_sha256", "execution_profile_sha256"))
    turn = _app_exact(binding["turn"], ("connection_id", "thread_id", "turn_id", "request_id", "input_sha256",
        "user_item_id", "output_item_id", "output_sha256"))
    for obj, names in ((session, ("connection_id", "thread_id")),
                       (turn, ("connection_id", "thread_id", "turn_id", "user_item_id", "output_item_id"))):
        for name in names:
            if type(obj[name]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", obj[name]) is None:
                raise VerificationContractError("KA01_NATIVE_CHECKPOINT_SESSION")
    for obj, names in ((session, ("binary_sha256", "execution_profile_sha256")),
                       (turn, ("input_sha256", "output_sha256"))):
        for name in names:
            _original_hash(obj[name])
    if (type(session["process_id"]) is not int or session["process_id"] <= 0
            or type(turn["request_id"]) is not int or turn["request_id"] <= 0
            or any(turn[k] != session[k] for k in ("connection_id", "thread_id"))
            or session["execution_profile_sha256"] != binding["execution_profile_sha256"]):
        raise VerificationContractError("KA01_NATIVE_CHECKPOINT_SESSION_CORRELATION")
    if (type(data["evidence"]) is not list or not 1 <= len(data["evidence"]) <= 256
            or type(data["proofs"]) is not list or len(data["proofs"]) != len(data["evidence"])):
        raise VerificationContractError("KA01_NATIVE_CHECKPOINT_POPULATION")
    for index, (item, proof) in enumerate(zip(data["evidence"], data["proofs"])):
        evidence = validate_evidence(item)
        if data["version"] == 2 and (evidence.verification is None
                or evidence.verification.get("binding", {}).get("original_proof") != binding["original_proof"]):
            raise VerificationContractError("KA01_KERNEL_ORIGINAL_PROOF_MISMATCH")
        _app_exact(proof, ("command_index", "command_sha256", "evidence_sha256", "raw_sha256", "native_sha256",
            "collection", "profile_sha256", "toolchain_sha256", "outcome", "failed_identities"))
        if (proof["command_index"] != index or type(proof["command_index"]) is not int
                or proof["outcome"] not in ("PASS", "FAIL")
                or (evidence.run_id, evidence.subject_sha) != (binding["run_id"], binding["candidate_sha"])):
            raise VerificationContractError("KA01_NATIVE_CHECKPOINT_PROOF")
        for key in ("command_sha256", "evidence_sha256", "raw_sha256", "native_sha256", "profile_sha256", "toolchain_sha256"):
            _original_hash(proof[key])
        if hashlib.sha256(_original_json(item).encode("ascii")).hexdigest() != proof["evidence_sha256"]:
            raise VerificationContractError("KA01_NATIVE_CHECKPOINT_EVIDENCE")
        if type(proof["failed_identities"]) is not list:
            raise VerificationContractError("KA01_NATIVE_CHECKPOINT_FAILURES")
        for identity in proof["failed_identities"]:
            _original_hash(identity)
        if (proof["failed_identities"] != sorted(set(proof["failed_identities"]))
                or bool(proof["failed_identities"]) != (proof["outcome"] == "FAIL")):
            raise VerificationContractError("KA01_NATIVE_CHECKPOINT_OUTCOME")
    if data["failed_identities"] != sorted({v for p in data["proofs"] for v in p["failed_identities"]}):
        raise VerificationContractError("KA01_NATIVE_CHECKPOINT_OUTSTANDING")
    return (NativeOperationCheckpoint if default else NativePrimaryCheckpoint)(content.decode("ascii"))
