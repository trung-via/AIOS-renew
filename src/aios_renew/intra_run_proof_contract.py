"""VP-02B: bounded offline declarations, never Runtime authority or evidence.

Only decoded plain Python values are accepted. No loader, callback, clock,
environment lookup, scheduler, persistence or production integration exists.
Independent pins detect contradictions; neither pins nor digests authenticate
the caller's canonical provenance. Every returned authorization flag is false.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

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
