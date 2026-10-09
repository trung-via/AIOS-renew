"""VP-03C v1 handoff with the VP-03D closed Runtime observation boundary.

The canonical entry point accepts no authority, repository, producer or callback.
An explicitly separate offline diagnostic consumes VP-03A/03B content facts;
its caller-selected fixture can NEVER establish canonical issuer legitimacy.
Neither entry point grants authority, discharges proof or persists a receipt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from . import proof_applicability as applicability
from . import proof_checkpoint_lineage as lineage
from .proof_coverage_contract import (
    BR_GATES, ProofCoverageError, ProofDescriptor, ProofObligation,
    validate_proof_mapping,
)

if TYPE_CHECKING:
    from .runtime_provenance_bridge import ProvenanceObservation


SCHEMA = "canonical-proof-handoff-v1"
CONTENT_SCHEMA = "canonical-proof-handoff-content-v1"
MAX_SELECTIONS = lineage.MAX_EVALUATIONS
ISSUER_UNAVAILABLE = "INDEPENDENT_CANONICAL_RUNTIME_ISSUER_UNAVAILABLE"
RecordIdentity = applicability.RecordIdentity
ProofPin = applicability.ProofPin
State = applicability.State


class HandoffInputError(ValueError):
    """Malformed, unsupported or unbounded decoded request."""


@dataclass(frozen=True)
class AdmissionBinding:
    task: RecordIdentity
    task_id: str
    task_revision: int
    task_envelope_digest: str
    run: RecordIdentity
    run_id: str
    base_sha: str
    admission: RecordIdentity


@dataclass(frozen=True)
class CandidateBinding:
    candidate_sha: str
    tree_sha: str
    checkpoint: RecordIdentity
    contract: RecordIdentity
    mapping: RecordIdentity
    contract_pin: ProofPin
    mapping_pin: ProofPin


@dataclass(frozen=True)
class OriginalSelection:
    proof_id: str
    proof_digest: str
    result: RecordIdentity
    evidence: RecordIdentity
    raw: RecordIdentity


@dataclass(frozen=True)
class TargetSelection:
    obligation: ProofPin
    source_proof_id: str
    witness: RecordIdentity


@dataclass(frozen=True)
class HandoffRequest:
    schema: str
    admission: AdmissionBinding
    source: CandidateBinding
    target: CandidateBinding
    originals: tuple[OriginalSelection, ...]
    obligations: tuple[TargetSelection, ...]
    replay: RecordIdentity | None


@dataclass(frozen=True)
class InactiveEffects:
    # Non-init fields cannot be enabled by constructing/replacing a result.
    activation: str = field(default="NOT_ACTIVATED", init=False)
    issuer_authenticated: bool = field(default=False, init=False)
    producer_rights_authenticated: bool = field(default=False, init=False)
    authorization_granted: bool = field(default=False, init=False)
    correction_authorized: bool = field(default=False, init=False)
    runtime_continuation_authorized: bool = field(default=False, init=False)
    lifecycle_mutation_authorized: bool = field(default=False, init=False)
    checkpoint_append_authorized: bool = field(default=False, init=False)
    checkpoint_consumption_authorized: bool = field(default=False, init=False)
    feedback_consumption_authorized: bool = field(default=False, init=False)
    acceptance_discharge_authorized: bool = field(default=False, init=False)
    evidence_reuse_authorized: bool = field(default=False, init=False)
    verification_execution_authorized: bool = field(default=False, init=False)
    terminalization_authorized: bool = field(default=False, init=False)
    scheduler_activated: bool = field(default=False, init=False)
    publisher_activated: bool = field(default=False, init=False)
    target_execution_evidence_created: bool = field(default=False, init=False)
    semantic_verdict_issued: bool = field(default=False, init=False)
    acceptance_pass_asserted: bool = field(default=False, init=False)
    canonical_checkpoint_created: bool = field(default=False, init=False)
    artifact_persistence_performed: bool = field(default=False, init=False)
    budget_reservation_performed: bool = field(default=False, init=False)
    base_replay_required_gates: tuple[str, ...] = field(default=BR_GATES, init=False)


@dataclass(frozen=True)
class CurrentnessFact:
    """Separate local ref reads and the fixture's attested snapshot, never CAS."""

    expected_main_sha: str
    expected_head_sha: str
    observed_main_before: str | None
    observed_head_before: str | None
    observed_main_after: str | None
    observed_head_after: str | None
    current_record: RecordIdentity
    observed_at: int
    state: str


@dataclass(frozen=True)
class TargetFact(InactiveEffects):
    selection: TargetSelection
    state: State
    obligation: ProofObligation | None = None
    mapped_proof: ProofDescriptor | None = None
    original: applicability.OriginalObservation | None = None
    # Kept verbatim, including VALID/INVALIDATED/UNKNOWN and original FAIL.
    # This is relative to the offline catalog, NEVER canonical applicability.
    content_applicability: applicability.ApplicabilityResult | None = None


@dataclass(frozen=True)
class HandoffResult(InactiveEffects):
    schema: str
    request_digest: str
    status: str
    observation: str
    reasons: tuple[str, ...]
    targets: tuple[TargetFact, ...]
    issuer_state: str = "UNAVAILABLE"
    producer_state: str = "NOT_INSTALLED"
    repository_id: str | None = None
    admission: AdmissionBinding | None = None
    admission_status: str = "UNKNOWN"
    source: CandidateBinding | None = None
    target: CandidateBinding | None = None
    currentness: CurrentnessFact | None = None
    content_lineage: lineage.LineageResult | None = None
    runtime_provenance: ProvenanceObservation | None = None


def _record(value, cls):
    return applicability._object(value, set(cls.__dataclass_fields__))


def _admission(value):
    data = _record(value, AdmissionBinding)
    revision = data["task_revision"]
    if type(revision) is not int or not 1 <= revision <= 2_147_483_647:
        raise HandoffInputError("positive bounded TASK revision required")
    return AdmissionBinding(
        applicability.decode_record_identity(data["task"]),
        applicability._match(data["task_id"], applicability._ID), revision,
        applicability._match(data["task_envelope_digest"], applicability._DIGEST),
        applicability.decode_record_identity(data["run"]),
        applicability._match(data["run_id"], applicability._ID),
        applicability._match(data["base_sha"], applicability._SHA),
        applicability.decode_record_identity(data["admission"]))


def _candidate(value):
    data = _record(value, CandidateBinding)
    return CandidateBinding(
        applicability._match(data["candidate_sha"], applicability._SHA),
        applicability._match(data["tree_sha"], applicability._SHA),
        *(applicability.decode_record_identity(data[k]) for k in ("checkpoint", "contract", "mapping")),
        applicability._pin(data["contract_pin"]), applicability._pin(data["mapping_pin"]))


def _plain_input(value):
    # Reject custom classes with identity checks before the shared JSON helpers
    # can perform any type equality or serialization on untrusted values.
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > applicability.MAX_NODES or depth > applicability.MAX_DEPTH:
            raise HandoffInputError("decoded request exceeds bounds")
        if type(item) is dict:
            if len(item) > applicability.MAX_NODES or any(type(k) is not str for k in item):
                raise HandoffInputError("plain bounded string-keyed object required")
            pending.extend((v, depth + 1) for v in item.values())
            pending.extend((k, depth + 1) for k in item)
        elif type(item) is list:
            if len(item) > applicability.MAX_NODES:
                raise HandoffInputError("decoded list exceeds bounds")
            pending.extend((v, depth + 1) for v in item)
        elif item is not None and type(item) is not str and type(item) is not int and type(item) is not bool:
            raise HandoffInputError("only plain decoded JSON values supported")


def decode_request(value: object) -> HandoffRequest:
    """Exact plain JSON facts; no trust, repository, verdict or validity inputs."""
    try:
        _plain_input(value)
        applicability.canonical_digest(value)  # Shared JSON node/depth/byte bounds.
        data = _record(value, HandoffRequest)
        if data["schema"] != SCHEMA:
            raise HandoffInputError("unsupported canonical handoff version")
        originals, obligations = [], []
        for raw in applicability._list(data["originals"], MAX_SELECTIONS):
            item = _record(raw, OriginalSelection)
            originals.append(OriginalSelection(
                applicability._match(item["proof_id"], applicability._ID),
                applicability._match(item["proof_digest"], applicability._DIGEST),
                *(applicability.decode_record_identity(item[k]) for k in ("result", "evidence", "raw"))))
        for raw in applicability._list(data["obligations"], MAX_SELECTIONS):
            item = _record(raw, TargetSelection)
            obligations.append(TargetSelection(applicability._pin(item["obligation"]),
                applicability._match(item["source_proof_id"], applicability._ID),
                applicability.decode_record_identity(item["witness"])))
        if not originals or not obligations:
            raise HandoffInputError("nonempty original and target populations required")
        replay = None if data["replay"] is None else applicability.decode_record_identity(data["replay"])
        return HandoffRequest(SCHEMA, _admission(data["admission"]),
            _candidate(data["source"]), _candidate(data["target"]),
            tuple(originals), tuple(obligations), replay)
    except applicability.ApplicabilityInputError as exc:
        raise HandoffInputError(str(exc)) from None


def _values(value, depth=0, nodes=None):
    # Inspect concrete types before conversion; never invoke caller object hooks.
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if depth > applicability.MAX_DEPTH or nodes[0] > applicability.MAX_NODES:
        raise HandoffInputError("typed request exceeds bounds")
    classes = (HandoffRequest, AdmissionBinding, CandidateBinding, OriginalSelection,
               TargetSelection, RecordIdentity, ProofPin)
    if any(type(value) is cls for cls in classes):
        return {k: _values(getattr(value, k), depth + 1, nodes) for k in value.__dataclass_fields__}
    if type(value) is tuple:
        if len(value) > MAX_SELECTIONS:
            raise HandoffInputError("bounded selection tuple required")
        return [_values(v, depth + 1, nodes) for v in value]
    if value is None or type(value) is str or type(value) is int:
        return value
    raise HandoffInputError("exact decoded handoff types required")


def _checked(request):
    if (type(request) is not HandoffRequest or type(request.admission) is not AdmissionBinding
            or type(request.source) is not CandidateBinding or type(request.target) is not CandidateBinding
            or type(request.originals) is not tuple or type(request.obligations) is not tuple
            or len(request.originals) > MAX_SELECTIONS or len(request.obligations) > MAX_SELECTIONS
            or any(type(v) is not OriginalSelection for v in request.originals)
            or any(type(v) is not TargetSelection for v in request.obligations)):
        raise HandoffInputError("decode_request must precede evaluation")
    values = _values(request)
    return decode_request(values), applicability.canonical_digest(values)


class _Conflict(Exception):
    pass


def _require(condition, reason):
    if not condition:
        raise _Conflict(reason)


def _unique(request):
    _require(len({v.proof_id for v in request.originals}) == len(request.originals), "DUPLICATE_ORIGINAL")
    _require(len({v.evidence for v in request.originals}) == len(request.originals), "DUPLICATE_ORIGINAL_RECORD")
    _require(len({v.obligation.id for v in request.obligations}) == len(request.obligations), "DUPLICATE_TARGET")
    _require(len({v.witness for v in request.obligations}) == len(request.obligations), "DUPLICATE_WITNESS")


def _empty(request, digest, schema=SCHEMA):
    return HandoffResult(schema, digest, "UNKNOWN", "ISSUER_UNAVAILABLE",
        (ISSUER_UNAVAILABLE, "NOT_ACTIVATED"),
        tuple(TargetFact(v, State.UNKNOWN) for v in request.obligations))


def evaluate_handoff(request: HandoffRequest) -> HandoffResult:
    """Canonical API; source acquisition belongs exclusively to Runtime.

    No setter/registration/authority argument exists. Git objects, even coherent
    reviewed-looking ones, cannot substitute for that absent source boundary.
    VP-03D observes the existing boundary's availability without trusting a
    caller-provided fixture or Runtime-looking instance. No independent source
    attestation exists there yet; no terminal fact is projected into C1/C2.
    """
    request, digest = _checked(request)
    result = _empty(request, digest)
    try:
        _unique(request)
    except _Conflict as exc:
        return replace(result, status="BLOCK", observation="CONFLICT",
                       reasons=(str(exc), *result.reasons))
    from .runtime_provenance_bridge import _closed_runtime_observation, observe_handoff_provenance

    provenance = observe_handoff_provenance(request)
    # The bridge accepts no observation/producer argument and exposes no
    # positive issuer-enrollment branch. Offline content is a separate schema.
    if not _closed_runtime_observation(provenance, digest):
        return replace(result, status="BLOCK", observation="CONFLICT",
            reasons=("UNTRUSTED_RUNTIME_SOURCE_OBSERVATION", *result.reasons))
    return replace(result, status=provenance.status, runtime_provenance=provenance,
                   reasons=tuple(dict.fromkeys((*provenance.reasons, *result.reasons))))


def _ref(git, ref):
    try:
        raw = git._read(["rev-parse", "--verify", ref])
        if len(raw) != 41 or raw[-1:] != b"\n":
            return None
        return applicability._match(raw[:-1].decode("ascii"), applicability._SHA)
    except (applicability._Unknown, ValueError, UnicodeError):
        return None


def _coverage(git, seal, admission):
    binding = seal.binding
    expected_task = {"task_id": admission.task_id, "revision": admission.task_revision,
        "envelope_digest": admission.task_envelope_digest, "acceptance_ids": list(binding.acceptance_ids)}
    coverage = validate_proof_mapping(git.record(seal.contract_record), git.record(seal.mapping_record),
        expected_task=expected_task, expected_mapping=asdict(binding.proof_mapping))
    _require(ProofPin(coverage.contract.id, coverage.contract.revision, coverage.contract.digest)
             == ProofPin(**asdict(binding.proof_contract)), "CONTRACT_PIN_CONFLICT")
    return coverage


def _bind(request, catalog, history):
    _require(len(history.seals) >= 2, "SOURCE_TARGET_CHECKPOINTS_MISSING")
    source, target = history.seals[-2:]
    binding = source.binding
    observed_admission = AdmissionBinding(
        applicability.decode_record_identity(catalog["task"]), binding.task_id, binding.task_revision,
        binding.task_envelope_digest, applicability.decode_record_identity(catalog["run"]),
        binding.run_id, binding.base_sha, applicability.decode_record_identity(catalog["admission"]))
    _require(request.admission == observed_admission, "EXACT_ADMISSION_BINDING_CONFLICT")
    for wanted, seal in ((request.source, source), (request.target, target)):
        observed = CandidateBinding(seal.binding.candidate_sha, seal.binding.candidate_tree_sha,
            seal.record, seal.contract_record, seal.mapping_record,
            ProofPin(**asdict(seal.binding.proof_contract)), ProofPin(**asdict(seal.binding.proof_mapping)))
        _require(wanted == observed, "EXACT_CANDIDATE_MAPPING_BINDING_CONFLICT")
    originals = {v.source.source_proof_id: v.source for v in source.originals}
    selected = {v.proof_id: v for v in request.originals}
    _require(selected.keys() == originals.keys(), "ORIGINAL_POPULATION_CONFLICT")
    for proof_id, observation in originals.items():
        _require(selected[proof_id] == OriginalSelection(proof_id, observation.source_proof_digest,
                 observation.result, observation.evidence, observation.raw), "EXACT_ORIGINAL_BINDING_CONFLICT")
    return source, target, originals


def _project(git, request, catalog, history):
    source, target, originals = _bind(request, catalog, history)
    # Only VP-02 parses coverage semantics; there is no new subsumption engine.
    _coverage(git, source, request.admission)
    coverage = _coverage(git, target, request.admission)
    obligations = {v.id: v for v in coverage.contract.obligations}
    selections = {v.obligation.id: v for v in request.obligations}
    _require(selections.keys() == obligations.keys(), "DISTINCT_TARGET_POPULATION_CONFLICT")
    proofs = {v.id: v for v in coverage.mapping.proofs}
    edges = {v.obligation_id: v.proof_id for v in coverage.mapping.entries}
    transition = catalog["transitions"][-1]
    ordinals = (source.checkpoint.ordinal, target.checkpoint.ordinal)
    facts = [v.result for v in history.proofs if (v.source_ordinal, v.target_ordinal) == ordinals]
    pins = applicability._list(transition["catalogs"], MAX_SELECTIONS)
    _require(len(pins) == len(facts) == len(obligations), "DISTINCT_TARGET_FACTS_MISSING")
    by_obligation = {}
    for pin, fact in zip(pins, facts):
        # These are the exact catalogs already evaluated by VP-03B/VP-03A.
        data = git.record(applicability.decode_record_identity(pin))
        obligation_pin = applicability._pin(data["target_obligation"])
        identity = obligation_pin.id
        _require(identity in selections and identity not in by_obligation, "TARGET_FACT_CONFLICT")
        wanted = selections[identity]
        obligation = obligations[identity]
        _require(wanted == TargetSelection(obligation_pin, data["source_proof_id"],
                    applicability.decode_record_identity(data["witness"]))
                 and obligation_pin == ProofPin(obligation.id, obligation.revision, obligation.digest),
                 "EXACT_TARGET_WITNESS_CONFLICT")
        original = originals.get(wanted.source_proof_id)
        _require(original is not None and applicability.decode_record_identity(data["evidence"]) == original.evidence,
                 "EXACT_TARGET_SOURCE_CONFLICT")
        _require(fact.source is None or fact.source == original, "ORIGINAL_OBSERVATION_CHANGED")
        _require(fact.witness is None or fact.witness.record == wanted.witness, "WITNESS_CHANGED")
        by_obligation[identity] = TargetFact(wanted, State.UNKNOWN, obligation, proofs[edges[identity]], original, fact)
    # Retain contract order and every distinct obligation, even shared AC IDs.
    return tuple(by_obligation[v.id] for v in coverage.contract.obligations)


def inspect_content_handoff(request: HandoffRequest, *,
        fixture_authority: lineage.ReadOnlyLineageAuthority | None) -> HandoffResult:
    """Offline diagnostic ONLY; cannot feed/enroll the canonical entry point.

    A concrete fixture selects content to inspect, never a canonical trust root.
    Canonical target states stay UNKNOWN regardless of the relative VP-03 facts.
    Calls evaluate_lineage ONCE; that engine alone calls evaluate_applicability.
    Duplicate delivery is deterministic read-only inspection, not consumption.
    """
    request, digest = _checked(request)
    result = _empty(request, digest, CONTENT_SCHEMA)
    currentness = None
    try:
        _unique(request)
        if (type(fixture_authority) is not lineage.ReadOnlyLineageAuthority
                or type(fixture_authority.repository) is not type(Path())):
            return replace(result, reasons=("CONTENT_SOURCE_UNAVAILABLE", *result.reasons))
        catalog_pin = applicability.decode_record_identity(applicability._record_values(fixture_authority.catalog))
        git = applicability._Git(fixture_authority.repository)
        catalog = applicability._object(git.record(catalog_pin), {
            "schema", "repository_id", "expected_main_sha", "expected_head_sha", "task", "run", "human",
            "budget", "operational_authority", "admission", "current", "checkpoints", "transitions"})
        main = applicability._match(catalog["expected_main_sha"], applicability._SHA)
        head = applicability._match(catalog["expected_head_sha"], applicability._SHA)
        observed_at = fixture_authority.observed_at
        if type(observed_at) is not int or not 0 <= observed_at <= 2_147_483_647:
            raise HandoffInputError("invalid fixture observation time")
        currentness = CurrentnessFact(main, head, _ref(git, "refs/heads/main"), _ref(git, "HEAD"), None, None,
            applicability.decode_record_identity(catalog["current"]), observed_at, "UNKNOWN")
        entries = applicability._list(catalog["checkpoints"], lineage.MAX_CHECKPOINTS)
        content_request = lineage.decode_request({"schema": lineage.SCHEMA, "repository_id": catalog["repository_id"],
            "checkpoints": [v["checkpoint"] for v in entries],
            "replay": None if request.replay is None else asdict(request.replay)})
        history = lineage.evaluate_lineage(content_request, authority=fixture_authority)
        result = replace(result, content_lineage=history, repository_id=catalog["repository_id"],
            observation="CONTENT_" + history.observation, reasons=(*history.reasons, ISSUER_UNAVAILABLE))
        # Partial seals/proofs are retained in content_lineage for diagnosis but
        # never projected as a complete population or a current canonical fact.
        if history.history_authenticated and history.observation in ("CONSISTENT", "SAME_FACT"):
            targets = _project(git, request, catalog, history)
            result = replace(result, targets=targets, admission=request.admission, admission_status="ADMITTED",
                             source=request.source, target=request.target)
        elif history.status == "BLOCK":
            result = replace(result, status="BLOCK")
    except _Conflict as exc:
        result = replace(result, status="BLOCK", observation="CONTENT_CONFLICT", reasons=(str(exc), *result.reasons))
    except ProofCoverageError:
        result = replace(result, status="BLOCK", observation="CONTENT_CONFLICT", reasons=("MAPPING_CONFLICT", *result.reasons))
    except applicability._Unknown as exc:
        result = replace(result, reasons=(exc.reason.code.value, *result.reasons))
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration, UnicodeError, RecursionError):
        result = replace(result, reasons=("CONTENT_RECORD_CORRUPT_OR_MISSING", *result.reasons))
    if currentness is not None:
        currentness = replace(currentness, observed_main_after=_ref(git, "refs/heads/main"),
                              observed_head_after=_ref(git, "HEAD"))
        stable = (currentness.observed_main_before == currentness.observed_main_after == currentness.expected_main_sha
                  and currentness.observed_head_before == currentness.observed_head_after == currentness.expected_head_sha)
        currentness = replace(currentness, state="CONSISTENT" if stable else "UNKNOWN")
        result = replace(result, currentness=currentness)
        if not stable:
            result = replace(result, status="UNKNOWN", observation="CONTENT_CURRENTNESS_UNKNOWN",
                targets=tuple(TargetFact(v, State.UNKNOWN) for v in request.obligations),
                admission=None, admission_status="UNKNOWN", source=None, target=None,
                reasons=("SEPARATELY_OBSERVED_CURRENTNESS_CONFLICT", *result.reasons))
    return result
