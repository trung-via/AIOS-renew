"""VP-02 design-only semantic proof declarations and mechanical validation.

No I/O, execution, discovery, evidence validity, TASK admission or scheduling.
Trusted TASK/mapping pins and reviewed semantic relationships are supplied by
the caller. A valid declaration is never a verification or Reviewer PASS.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass


CONTRACT_SCHEMA = "proof-coverage-v1"
MAPPING_SCHEMA = "proof-mapping-v1"
REPLAY_SCHEMA = "base-replay-eligibility-v1"
OBSERVATION_SCHEMA = "candidate-disposition-v1"
EXECUTION_DEFAULT = "candidate-first-no-reproduction"
MAX_OBLIGATIONS = 256
MAX_REFERENCES = 64
MAX_POPULATION = 100_000
MAX_TEXT = 2048
MAX_REVISION = 2_147_483_647
BR_GATES = tuple(f"BR-{number}" for number in range(1, 7))
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_COMMAND_TEXT = re.compile(
    r"(?:pytest(?:\s|$)|(?:python(?:\d+(?:\.\d+)?)?|py)\s+(?:-m\b|-c\b|\S+\.py(?:\s|$))"
    r"|(?:powershell|pwsh|cmd|bash|sh)\s+[-/]|git\s+|aios\s+)", re.IGNORECASE | re.ASCII,
)


class ProofCoverageError(ValueError):
    """Malformed, incomplete, stale, ambiguous or conflicting declaration."""


@dataclass(frozen=True)
class Provenance:
    authority_ref: str
    review_ref: str
    source_ref: str


@dataclass(frozen=True)
class TaskBinding:
    task_id: str
    revision: int
    envelope_digest: str
    acceptance_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticClaim:
    id: str
    text: str


@dataclass(frozen=True)
class ProofConditions:
    candidate_sha: str
    base_sha: str | None
    population_ref: str
    population_size: int
    test_ref: str
    fixture_refs: tuple[str, ...]
    shared_state_ref: str
    profile_ref: str
    worker_mode: str
    workers: int
    concurrency_ref: str
    ordering_ref: str
    integration_ref: str
    repetition_ref: str
    toolchain_ref: str
    environment_ref: str
    evidence_kind: str
    evidence_schema: str
    collection_ref: str


@dataclass(frozen=True)
class ProofObligation:
    id: str
    revision: int
    claim: SemanticClaim
    acceptance_ids: tuple[str, ...]
    kind: str
    blocking: bool
    conditions: ProofConditions
    provenance: Provenance

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class ProofContract:
    schema: str
    id: str
    revision: int
    execution_default: str
    task: TaskBinding
    provenance: Provenance
    obligations: tuple[ProofObligation, ...]

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class ProofDescriptor:
    id: str
    kind: str
    claims: tuple[SemanticClaim, ...]
    conditions: ProofConditions
    provenance: Provenance


@dataclass(frozen=True)
class ProofMappingEntry:
    obligation_id: str
    obligation_revision: int
    obligation_digest: str
    proof_id: str


@dataclass(frozen=True)
class ProofMapping:
    schema: str
    id: str
    revision: int
    contract_id: str
    contract_revision: int
    contract_digest: str
    provenance: Provenance
    proofs: tuple[ProofDescriptor, ...]
    entries: tuple[ProofMappingEntry, ...]

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class CoverageDeclaration:
    """Complete declared coverage; no executed/discharged proof is implied."""

    contract: ProofContract
    mapping: ProofMapping


@dataclass(frozen=True)
class BaseReplayEligibility:
    eligible: bool
    failed_gates: tuple[str, ...]
    execution_authorized: bool = False


@dataclass(frozen=True)
class CandidateDisposition:
    status: str
    reason: str
    outstanding_comparison_ids: tuple[str, ...]
    attribution: str = "UNKNOWN"
    reproduction_requested: bool = False
    base_replay_requested: bool = False


def _object(value: object, fields: set[str], path: str) -> dict:
    if (type(value) is not dict or len(value) != len(fields)
            or any(type(key) is not str for key in value) or set(value) != fields):
        raise ProofCoverageError(f"{path}: exact fields required")
    return value


def _list(value: object, limit: int, path: str, *, empty: bool = False) -> list:
    if type(value) is not list or len(value) > limit or (not value and not empty):
        raise ProofCoverageError(f"{path}: nonempty bounded list required")
    return value


def _match(value: object, pattern: re.Pattern, path: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ProofCoverageError(f"{path}: invalid identity")
    return value


def _id(value: object, path: str) -> str:
    return _match(value, _ID, path)


def _int(value: object, limit: int, path: str) -> int:
    if type(value) is not int or not 1 <= value <= limit:
        raise ProofCoverageError(f"{path}: positive bounded integer required")
    return value


def _bool(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise ProofCoverageError(f"{path}: boolean required")
    return value


def _choice(value: object, choices: tuple[str, ...], path: str) -> str:
    if type(value) is not str or value not in choices:
        raise ProofCoverageError(f"{path}: unsupported value")
    return value


def _ids(value: object, limit: int, path: str, *, empty: bool = False) -> tuple[str, ...]:
    items = tuple(_id(item, path) for item in _list(value, limit, path, empty=empty))
    if len(set(items)) != len(items):
        raise ProofCoverageError(f"{path}: duplicate identity")
    return tuple(sorted(items))


def _provenance(value: object, path: str) -> Provenance:
    data = _object(value, {"authority_ref", "review_ref", "source_ref"}, path)
    return Provenance(**{key: _id(data[key], f"{path}.{key}") for key in sorted(data)})


def _task(value: object) -> TaskBinding:
    data = _object(value, {"task_id", "revision", "envelope_digest", "acceptance_ids"}, "task")
    return TaskBinding(
        _id(data["task_id"], "task.task_id"),
        _int(data["revision"], MAX_REVISION, "task.revision"),
        _match(data["envelope_digest"], _DIGEST, "task.envelope_digest"),
        _ids(data["acceptance_ids"], MAX_OBLIGATIONS, "task.acceptance_ids"),
    )


def _claim(value: object) -> SemanticClaim:
    data = _object(value, {"id", "text"}, "claim")
    text = data["text"]
    if (type(text) is not str or not text.strip() or text != text.strip()
            or len(text) > MAX_TEXT
            or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in text)):
        raise ProofCoverageError("claim.text: bounded single-line semantic text required")
    if _COMMAND_TEXT.match(text):
        raise ProofCoverageError("claim.text: executable invocation is not a semantic claim")
    return SemanticClaim(_id(data["id"], "claim.id"), text)


def _conditions(value: object) -> ProofConditions:
    fields = set(ProofConditions.__dataclass_fields__)
    data = _object(value, fields, "conditions")
    parsed = {
        key: _id(data[key], f"conditions.{key}")
        for key in sorted(fields - {"candidate_sha", "base_sha", "population_size", "fixture_refs", "worker_mode", "workers"})
    }
    parsed["candidate_sha"] = _match(data["candidate_sha"], _SHA, "conditions.candidate_sha")
    parsed["base_sha"] = (None if data["base_sha"] is None
                          else _match(data["base_sha"], _SHA, "conditions.base_sha"))
    if parsed["base_sha"] == parsed["candidate_sha"]:
        raise ProofCoverageError("conditions: candidate and base identities must differ")
    parsed["population_size"] = _int(data["population_size"], MAX_POPULATION, "conditions.population_size")
    # Empty fixtures are an explicit declaration of no fixtures, not unknown.
    parsed["fixture_refs"] = _ids(data["fixture_refs"], MAX_REFERENCES, "conditions.fixture_refs", empty=True)
    parsed["worker_mode"] = _choice(data["worker_mode"], ("serial", "parallel"), "conditions.worker_mode")
    parsed["workers"] = _int(data["workers"], 256, "conditions.workers")
    if parsed["worker_mode"] == "serial" and parsed["workers"] != 1:
        raise ProofCoverageError("conditions: contradictory worker mode/count")
    return ProofConditions(**parsed)


def _kind(value: object, conditions: ProofConditions) -> str:
    kind = _choice(value, ("candidate", "comparison"), "kind")
    if (kind == "candidate") != (conditions.base_sha is None):
        raise ProofCoverageError("kind: comparison requires base; candidate must not bind base")
    return kind


def _obligation(value: object) -> ProofObligation:
    data = _object(value, set(ProofObligation.__dataclass_fields__), "obligation")
    conditions = _conditions(data["conditions"])
    return ProofObligation(
        _id(data["id"], "obligation.id"),
        _int(data["revision"], MAX_REVISION, "obligation.revision"),
        _claim(data["claim"]),
        _ids(data["acceptance_ids"], MAX_OBLIGATIONS, "obligation.acceptance_ids"),
        _kind(data["kind"], conditions),
        _bool(data["blocking"], "obligation.blocking"),
        conditions,
        _provenance(data["provenance"], "obligation.provenance"),
    )


def _unique(items: tuple, key: str, path: str) -> tuple:
    if len({getattr(item, key) for item in items}) != len(items):
        raise ProofCoverageError(f"{path}: duplicate {key}")
    return tuple(sorted(items, key=lambda item: getattr(item, key)))


def _check_claims(claims: tuple[SemanticClaim, ...]) -> None:
    texts: dict[str, str] = {}
    for claim in claims:
        if claim.id in texts and texts[claim.id] != claim.text:
            raise ProofCoverageError("claim: conflicting text for stable claim identity")
        texts[claim.id] = claim.text


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()


def validate_proof_contract(data: object, *, expected_task: object) -> ProofContract:
    """Validate complete semantic coverage against an independently supplied TASK pin."""
    root = _object(data, set(ProofContract.__dataclass_fields__), "contract")
    _choice(root["schema"], (CONTRACT_SCHEMA,), "contract.schema")
    _choice(root["execution_default"], (EXECUTION_DEFAULT,), "contract.execution_default")
    task = _task(root["task"])
    if task != _task(expected_task):
        raise ProofCoverageError("contract: stale or conflicting TASK binding")
    obligations = _unique(tuple(_obligation(item) for item in
        _list(root["obligations"], MAX_OBLIGATIONS, "obligations")), "id", "obligations")
    _check_claims(tuple(item.claim for item in obligations))
    covered = {identity for item in obligations for identity in item.acceptance_ids}
    if covered != set(task.acceptance_ids):
        raise ProofCoverageError("contract: incomplete or unknown acceptance coverage")
    if len({item.conditions.candidate_sha for item in obligations}) != 1:
        raise ProofCoverageError("contract: conflicting candidate subjects")
    bases = {item.conditions.base_sha for item in obligations if item.kind == "comparison"}
    if len(bases) > 1:
        raise ProofCoverageError("contract: conflicting comparison bases")
    # Multiple conditions for one semantic claim remain separate obligations.
    signatures = {(item.claim, item.kind, item.conditions) for item in obligations}
    if len(signatures) != len(obligations):
        raise ProofCoverageError("contract: duplicate or contradictory obligations require explicit consolidation")
    return ProofContract(CONTRACT_SCHEMA, _id(root["id"], "contract.id"),
                         _int(root["revision"], MAX_REVISION, "contract.revision"), EXECUTION_DEFAULT, task,
                         _provenance(root["provenance"], "contract.provenance"), obligations)


def proof_conditions_equivalent(left: object, right: object) -> bool:
    """Conservative equality over every declared subject/evidence/execution dimension."""
    return _conditions(left) == _conditions(right)


def obligations_equivalent(left: object, right: object) -> bool:
    """Same semantic claim and conditions; never permission to erase coverage entries."""
    first, second = _obligation(left), _obligation(right)
    return (first.claim, first.kind, first.blocking, first.conditions) == (
        second.claim, second.kind, second.blocking, second.conditions)


def _mapping(data: object) -> ProofMapping:
    root = _object(data, set(ProofMapping.__dataclass_fields__), "mapping")
    _choice(root["schema"], (MAPPING_SCHEMA,), "mapping.schema")
    proofs = []
    for value in _list(root["proofs"], MAX_OBLIGATIONS, "mapping.proofs"):
        item = _object(value, set(ProofDescriptor.__dataclass_fields__), "proof")
        conditions = _conditions(item["conditions"])
        claims = _unique(tuple(_claim(claim) for claim in
            _list(item["claims"], MAX_OBLIGATIONS, "proof.claims")), "id", "proof.claims")
        proofs.append(ProofDescriptor(_id(item["id"], "proof.id"), _kind(item["kind"], conditions),
                                     claims, conditions, _provenance(item["provenance"], "proof.provenance")))
    if sum(len(proof.claims) for proof in proofs) > MAX_OBLIGATIONS:
        raise ProofCoverageError("mapping: total semantic claims exceed bound")
    entries = []
    for value in _list(root["entries"], MAX_OBLIGATIONS, "mapping.entries"):
        item = _object(value, set(ProofMappingEntry.__dataclass_fields__), "mapping.entry")
        entries.append(ProofMappingEntry(
            _id(item["obligation_id"], "entry.obligation_id"),
            _int(item["obligation_revision"], MAX_REVISION, "entry.obligation_revision"),
            _match(item["obligation_digest"], _DIGEST, "entry.obligation_digest"),
            _id(item["proof_id"], "entry.proof_id")))
    return ProofMapping(MAPPING_SCHEMA, _id(root["id"], "mapping.id"),
        _int(root["revision"], MAX_REVISION, "mapping.revision"),
        _id(root["contract_id"], "mapping.contract_id"),
        _int(root["contract_revision"], MAX_REVISION, "mapping.contract_revision"),
        _match(root["contract_digest"], _DIGEST, "mapping.contract_digest"),
        _provenance(root["provenance"], "mapping.provenance"),
        _unique(tuple(proofs), "id", "mapping.proofs"),
        _unique(tuple(entries), "obligation_id", "mapping.entries (ambiguous)"))


def proof_mapping_digest(data: object) -> str:
    """Digest a structurally valid mapping for offline pinning; establishes no trust."""
    return _mapping(data).digest


def validate_proof_mapping(contract_data: object, mapping_data: object, *,
                           expected_task: object, expected_mapping: object) -> CoverageDeclaration:
    """Resolve only explicit reviewed entries; missing/conflicting mapping raises."""
    contract = validate_proof_contract(contract_data, expected_task=expected_task)
    mapping = _mapping(mapping_data)
    pin = _object(expected_mapping, {"id", "revision", "digest"}, "expected_mapping")
    expected = (_id(pin["id"], "expected_mapping.id"),
                _int(pin["revision"], MAX_REVISION, "expected_mapping.revision"),
                _match(pin["digest"], _DIGEST, "expected_mapping.digest"))
    if (mapping.id, mapping.revision, mapping.digest) != expected:
        raise ProofCoverageError("mapping: stale or conflicting reviewed mapping pin")
    if (mapping.contract_id, mapping.contract_revision, mapping.contract_digest) != (
            contract.id, contract.revision, contract.digest):
        raise ProofCoverageError("mapping: stale or conflicting contract binding")
    obligations = {item.id: item for item in contract.obligations}
    proofs = {item.id: item for item in mapping.proofs}
    if {item.obligation_id for item in mapping.entries} != set(obligations):
        raise ProofCoverageError("mapping: missing or unknown obligation coverage")
    if {item.proof_id for item in mapping.entries} != set(proofs):
        raise ProofCoverageError("mapping: unknown or unused proof")
    _check_claims(tuple(item.claim for item in contract.obligations) +
                  tuple(claim for proof in mapping.proofs for claim in proof.claims))
    signatures = {(item.kind, item.conditions) for item in mapping.proofs}
    if len(signatures) != len(mapping.proofs):
        raise ProofCoverageError("mapping: equivalent proof descriptors require one explicit shared proof")
    mapped_claims: dict[str, set[SemanticClaim]] = {identity: set() for identity in proofs}
    for entry in mapping.entries:
        obligation, proof = obligations[entry.obligation_id], proofs[entry.proof_id]
        if (entry.obligation_revision, entry.obligation_digest) != (obligation.revision, obligation.digest):
            raise ProofCoverageError("mapping: stale obligation binding")
        if (obligation.kind, obligation.conditions) != (proof.kind, proof.conditions):
            raise ProofCoverageError("mapping: conflicting proof conditions; no inferred subsumption")
        if obligation.claim not in proof.claims:
            raise ProofCoverageError("mapping: missing semantic claim")
        mapped_claims[proof.id].add(obligation.claim)
    for proof in mapping.proofs:
        if set(proof.claims) != mapped_claims[proof.id]:
            raise ProofCoverageError("mapping: unsupported extra semantic claims")
    return CoverageDeclaration(contract, mapping)


def _bound_obligation(data: dict, coverage: CoverageDeclaration) -> ProofObligation:
    contract_digest = _match(data["contract_digest"], _DIGEST, "decision.contract_digest")
    mapping_digest = _match(data["mapping_digest"], _DIGEST, "decision.mapping_digest")
    obligation_digest = _match(data["obligation_digest"], _DIGEST, "decision.obligation_digest")
    if (contract_digest, mapping_digest) != (
            coverage.contract.digest, coverage.mapping.digest):
        raise ProofCoverageError("decision: stale contract or mapping binding")
    identity = _id(data["obligation_id"], "decision.obligation_id")
    obligation = next((item for item in coverage.contract.obligations if item.id == identity), None)
    if obligation is None or obligation_digest != obligation.digest:
        raise ProofCoverageError("decision: missing or stale obligation binding")
    return obligation


def evaluate_base_replay(contract_data: object, mapping_data: object, replay_data: object, *,
                         expected_task: object, expected_mapping: object) -> BaseReplayEligibility:
    """Six affirmative necessity facts plus exact scope/bindings; never executes base.

    Missing/false/unknown facts deny eligibility. The caller must establish and
    review their truth; strings here cannot authenticate approval or evidence.
    """
    coverage = validate_proof_mapping(contract_data, mapping_data,
                                      expected_task=expected_task, expected_mapping=expected_mapping)
    data = _object(replay_data, {"schema", "contract_digest", "mapping_digest", "obligation_id",
        "obligation_digest", "gates", "decision", "alternative_status", "scope", "conditions",
        "bounds", "already_covered"}, "replay")
    _choice(data["schema"], (REPLAY_SCHEMA,), "replay.schema")
    obligation = _bound_obligation(data, coverage)
    gates = data["gates"]
    if (type(gates) is not dict or len(gates) > 6
            or any(type(key) is not str or key not in BR_GATES for key in gates)):
        raise ProofCoverageError("replay.gates: only BR-1 through BR-6 allowed")
    established = set()
    for identity in BR_GATES:
        if identity not in gates:
            continue
        fact = _object(gates[identity], {"established", "basis_ref"}, identity)
        if fact["established"] is not None:
            _bool(fact["established"], f"{identity}.established")
        if fact["basis_ref"] is not None:
            _id(fact["basis_ref"], f"{identity}.basis_ref")
        if fact["established"] is True:
            if fact["basis_ref"] is None:
                raise ProofCoverageError(f"{identity}: affirmative fact requires basis")
            established.add(identity)
    decision = _choice(data["decision"], ("UNDECIDED_REQUIRED", "MANDATORY_COMPARISON", "DIAGNOSTIC_ONLY"), "replay.decision")
    alternative = _choice(data["alternative_status"], ("ABSENT", "INVALID", "VALID", "UNKNOWN"), "replay.alternative_status")
    scope = _object(data["scope"], {"population_ref", "items", "method_ref"}, "replay.scope")
    population = _id(scope["population_ref"], "scope.population_ref")
    items = _int(scope["items"], MAX_POPULATION, "scope.items")
    _id(scope["method_ref"], "scope.method_ref")
    conditions = _conditions(data["conditions"])
    bounds = _object(data["bounds"], {"authority_ref", "cost_ref", "max_items", "max_seconds", "max_attempts"}, "replay.bounds")
    authority = _id(bounds["authority_ref"], "bounds.authority_ref")
    _id(bounds["cost_ref"], "bounds.cost_ref")
    max_items = _int(bounds["max_items"], MAX_POPULATION, "bounds.max_items")
    _int(bounds["max_seconds"], 3600, "bounds.max_seconds")
    _int(bounds["max_attempts"], 1, "bounds.max_attempts")
    already_covered = _bool(data["already_covered"], "replay.already_covered")
    prerequisites = {
        "BR-1": obligation.kind == "comparison",
        "BR-2": decision != "DIAGNOSTIC_ONLY",
        "BR-3": alternative in {"ABSENT", "INVALID"},
        "BR-4": (population, items) == (obligation.conditions.population_ref, obligation.conditions.population_size),
        "BR-5": conditions == obligation.conditions,
        "BR-6": authority == obligation.provenance.authority_ref and items <= max_items and not already_covered,
    }
    failed = tuple(identity for identity in BR_GATES if identity not in established or not prerequisites[identity])
    return BaseReplayEligibility(not failed, failed)


def candidate_disposition(contract_data: object, mapping_data: object, observation_data: object, *,
                          expected_task: object, expected_mapping: object) -> CandidateDisposition:
    """Describe prospective failure semantics without reproduction/fallback or PASS.

    Observation truth/validity belongs to future VP-03/Runtime. No API here can
    discharge an independent comparison obligation or construct EVIDENCE.
    """
    coverage = validate_proof_mapping(contract_data, mapping_data,
                                      expected_task=expected_task, expected_mapping=expected_mapping)
    data = _object(observation_data, {"schema", "contract_digest", "mapping_digest", "obligation_id",
        "obligation_digest", "subject_sha", "observation_ref", "outcome", "complete", "stable", "conflicting",
        "conditions", "observed_items"}, "observation")
    _choice(data["schema"], (OBSERVATION_SCHEMA,), "observation.schema")
    obligation = _bound_obligation(data, coverage)
    _id(data["observation_ref"], "observation.observation_ref")
    subject = _match(data["subject_sha"], _SHA, "observation.subject_sha")
    if subject != obligation.conditions.candidate_sha or obligation.kind != "candidate":
        raise ProofCoverageError("observation: candidate obligation/subject required")
    if _conditions(data["conditions"]) != obligation.conditions:
        raise ProofCoverageError("observation: conflicting proof conditions")
    outcome = _choice(data["outcome"], ("PASS", "FAIL", "UNKNOWN"), "observation.outcome")
    complete = _bool(data["complete"], "observation.complete")
    stable = _bool(data["stable"], "observation.stable")
    conflicting = _bool(data["conflicting"], "observation.conflicting")
    observed_items = data["observed_items"]
    if type(observed_items) is not int or not 0 <= observed_items <= MAX_POPULATION:
        raise ProofCoverageError("observation.observed_items: bounded nonnegative integer required")
    if observed_items > obligation.conditions.population_size or (complete and observed_items != obligation.conditions.population_size):
        raise ProofCoverageError("observation: contradictory complete population")
    comparisons = tuple(item.id for item in coverage.contract.obligations if item.kind == "comparison")
    decisive = outcome == "FAIL" and complete and stable and not conflicting and obligation.blocking
    if decisive and not comparisons:
        return CandidateDisposition("BLOCK", "complete decisive candidate failure", ())
    reason = ("independent mandatory comparison remains outstanding" if decisive and comparisons
              else "no complete decisive blocking failure; validation cannot establish PASS")
    return CandidateDisposition("UNRESOLVED", reason, comparisons)
