"""VP-03B: bounded offline immutable lineage facts, never continuation authority.

The concrete authority (including its observation time) MUST be delivered by an
independent trusted canonical-source boundary. Constructors do not enroll trust.
All reads use VP-03A's authenticated Git reader and all proof applicability uses
its published evaluator. No checkpoint, feedback, budget or lifecycle is written.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import intra_run_proof_contract as declaration
from . import proof_applicability as applicability
from .proof_coverage_contract import BR_GATES
from .run import Run, RunTaskReference
from .task import validate_task


SCHEMA = "proof-checkpoint-lineage-v1"
CATALOG_SCHEMA = "proof-checkpoint-lineage-authority-v1"
ADMISSION_SCHEMA = "proof-checkpoint-admission-v1"
HUMAN_SCHEMA = "proof-checkpoint-human-v1"
BUDGET_SCHEMA = "proof-checkpoint-budget-v1"
FACTS_SCHEMA = "proof-checkpoint-facts-v1"
CURRENT_SCHEMA = "proof-checkpoint-current-v1"
OPT_IN_VERSION = "KA-01-PROSPECTIVE"
MAX_CHECKPOINTS = declaration.MAX_CHECKPOINTS
MAX_EVALUATIONS = 32
MAX_FILES = 512
RESOURCE_NAMES = tuple(declaration.Resources.__dataclass_fields__)
RecordIdentity = applicability.RecordIdentity


class LineageInputError(ValueError):
    """Malformed or unbounded untrusted decoded request."""


@dataclass(frozen=True)
class LineageRequest:
    schema: str
    repository_id: str
    checkpoints: tuple[RecordIdentity, ...]
    replay: RecordIdentity | None


@dataclass(frozen=True)
class ReadOnlyLineageAuthority:
    """Dependency injection from canonical authority, NOT a caller trust API.

    observed_at is an independently attested evaluation time, not request time,
    a local clock, or an inferred freshness guarantee. No production adapter is
    installed. An attacker selecting any of these fields violates the premise.
    """

    repository: Path
    catalog: RecordIdentity
    observed_at: int


@dataclass(frozen=True)
class SealedFact:
    record: RecordIdentity
    seal_digest: str
    binding: declaration.Binding
    delegation: declaration.Delegation
    checkpoint: declaration.Checkpoint
    feedback_record: RecordIdentity
    feedback: declaration.Feedback
    reserved: declaration.Resources
    facts_record: RecordIdentity
    contract_record: RecordIdentity
    mapping_record: RecordIdentity
    original_catalogs: tuple[RecordIdentity, ...]
    originals: tuple[applicability.ApplicabilityResult, ...]


@dataclass(frozen=True)
class ProofFact:
    source_ordinal: int
    target_ordinal: int
    result: applicability.ApplicabilityResult


@dataclass(frozen=True)
class LineageResult:
    schema: str
    status: str
    observation: str
    reasons: tuple[str, ...]
    history_authenticated: bool
    seals: tuple[SealedFact, ...]
    proofs: tuple[ProofFact, ...]
    activation: str = "NOT_ACTIVATED"
    authorization_granted: bool = False
    runtime_continuation_authorized: bool = False
    lifecycle_mutation_authorized: bool = False
    checkpoint_append_authorized: bool = False
    feedback_consumption_authorized: bool = False
    acceptance_discharge_authorized: bool = False
    evidence_reuse_authorized: bool = False
    verification_execution_authorized: bool = False
    terminalization_authorized: bool = False
    scheduler_activated: bool = False
    target_execution_evidence_created: bool = False
    canonical_checkpoint_created: bool = False
    artifact_persistence_performed: bool = False
    base_replay_required_gates: tuple[str, ...] = BR_GATES


class _Stop(Exception):
    def __init__(self, reason: str, *, unknown: bool = False):
        self.reason = reason
        self.status = "UNKNOWN" if unknown else "BLOCK"


def _require(condition: bool, reason: str, *, unknown: bool = False) -> None:
    if not condition:
        raise _Stop(reason, unknown=unknown)


def _object(value, fields):
    return applicability._object(value, set(fields))


def _record(value, cls):
    return _object(value, cls.__dataclass_fields__)


def _plain(value):
    # Only validated foundation dataclasses/plain JSON reach this conversion.
    return json.loads(json.dumps(value))


def _identities(values, limit=MAX_CHECKPOINTS):
    return tuple(applicability.decode_record_identity(v)
                 for v in applicability._list(values, limit))


def decode_request(value: object) -> LineageRequest:
    """Decode exact plain bounded JSON; neither a trust root nor VALID is input."""
    try:
        applicability._bounded(value)
        data = _record(value, LineageRequest)
        if data["schema"] != SCHEMA:
            raise LineageInputError("unsupported lineage version")
        repository_id = declaration._id(data["repository_id"], "repository_id")
        pins = _identities(data["checkpoints"])
        replay = (None if data["replay"] is None else
                  applicability.decode_record_identity(data["replay"]))
        return LineageRequest(SCHEMA, repository_id, pins, replay)
    except (applicability.ApplicabilityInputError, declaration.IntraRunContractError) as exc:
        raise LineageInputError(str(exc)) from None


def _request_values(request):
    if (type(request) is not LineageRequest or type(request.checkpoints) is not tuple
            or len(request.checkpoints) > MAX_CHECKPOINTS):
        raise LineageInputError("decode_request must precede evaluation")
    try:
        return {"schema": request.schema, "repository_id": request.repository_id,
                "checkpoints": [applicability._record_values(p) for p in request.checkpoints],
                "replay": None if request.replay is None else applicability._record_values(request.replay)}
    except applicability.ApplicabilityInputError as exc:
        raise LineageInputError(str(exc)) from None


def _source(ref, pin):
    return declaration.SourceDeclaration(ref, pin.commit_sha, pin.sha256)


def _resources(value):
    return declaration._resources(value, "accounting", spent=True)


def _numbers(resources):
    return tuple(getattr(resources, key) for key in RESOURCE_NAMES)


def _budget(spent, reserved, limits):
    _require(all(s + r <= bound for s, r, bound in
                 zip(_numbers(spent), _numbers(reserved), _numbers(limits))), "BUDGET_EXHAUSTED")


def _semantic_digest(contract, mapping, base):
    """Pin unchanged meaning after VP-02 validation; only candidate binds vary.

    Derived digests in mapping edges vary with that candidate and are checked by
    VP-02 before normalization. All claims, identities, revisions, conditions,
    provenance, acceptance and distinct mapping edges remain in this identity.
    """
    contract, mapping = _plain(contract), _plain(mapping)
    for obligation in contract["obligations"]:
        obligation["conditions"]["candidate_sha"] = base
    mapping["contract_digest"] = None
    for proof in mapping["proofs"]:
        proof["conditions"]["candidate_sha"] = base
    for edge in mapping["entries"]:
        edge["obligation_digest"] = None
    return applicability.canonical_digest({"contract": contract, "mapping": mapping,
        "invariants": list(declaration.INVARIANTS), "dimensions": list(applicability.DIMENSIONS),
        "base_replay_required_gates": list(BR_GATES)})


def _files(git, candidate):
    """Bounded authenticated full tree comparison, not a caller diff selector."""
    pending = [("", git.commit(candidate)[0])]
    files = {}
    while pending:
        prefix, tree = pending.pop()
        body, offset = git.object(tree, "tree"), 0
        names = set()
        while offset < len(body):
            space = body.index(b" ", offset)
            nul = body.index(b"\0", space)
            mode = body[offset:space].decode("ascii")
            name = body[space + 1:nul].decode("ascii")
            oid = body[nul + 1:nul + 21]
            _require(len(oid) == 20 and name not in names, "TREE_CORRUPT", unknown=True)
            names.add(name)
            path = applicability._path(prefix + name)
            offset = nul + 21
            if mode == "40000":
                pending.append((path + "/", oid.hex()))
            else:
                _require(mode in ("100644", "100755"), "SCOPE_OBSERVER_UNSUPPORTED", unknown=True)
                git.object(oid.hex(), "blob")
                files[path] = (mode, oid.hex())
                _require(len(files) <= MAX_FILES, "RESOURCE_BOUND", unknown=True)
    return files


def _changed(left, right):
    return tuple(sorted(p for p in left.keys() | right.keys() if left.get(p) != right.get(p)))


def _read(git, pin, catalog_pin):
    git.ancestor(pin.commit_sha, catalog_pin.commit_sha)
    return git.record(pin)


def _current_head(git, expected_main, expected_head):
    git.current_main(expected_main)
    _require(git._read(["rev-parse", "--verify", "HEAD"]) == (expected_head + "\n").encode("ascii"),
             "HEAD_MOVED", unknown=True)
    git.commit(expected_head)


def _proof(git, pin, authority, catalog_pin, global_records, source_node, target_node):
    """One invocation of the published VP-03A engine, never another proof engine."""
    catalog = _read(git, pin, catalog_pin)
    _require(catalog["schema"] == applicability.CATALOG_SCHEMA, "PROOF_CATALOG_UNSUPPORTED", unknown=True)
    source, target = source_node["binding"], target_node["binding"]
    _require((catalog["repository_id"], catalog["expected_main_sha"], catalog["base_sha"],
              catalog["source_candidate_sha"], catalog["target_candidate_sha"]) ==
             (global_records["repository_id"], global_records["expected_main_sha"], source.base_sha,
              source.candidate_sha, target.candidate_sha), "PROOF_LINEAGE_CONFLICT")
    for name, expected in (("task", global_records["task"]), ("run", global_records["run"]),
                           ("source_contract", source_node["contract_pin"]),
                           ("source_mapping", source_node["mapping_pin"]),
                           ("target_contract", target_node["contract_pin"]),
                           ("target_mapping", target_node["mapping_pin"])):
        _require(applicability.decode_record_identity(catalog[name]) == expected, "PROOF_SOURCE_CONFLICT")
    request = applicability.decode_request({"schema": applicability.SCHEMA,
        "repository_id": catalog["repository_id"], "evidence": catalog["evidence"],
        "witness": catalog["witness"], "source_proof_id": catalog["source_proof_id"],
        "target_candidate_sha": target.candidate_sha, "target_obligation": catalog["target_obligation"]})
    result = applicability.evaluate_applicability(request,
        authority=applicability.ReadOnlyAuthority(authority.repository, pin))
    if result.source is not None:
        _require((result.source.task, result.source.run, result.source.subject_sha,
                  result.source.task_envelope_digest, result.source.source_contract, result.source.source_mapping) ==
                 (global_records["task"], global_records["run"], source.candidate_sha,
                  source.task_envelope_digest, applicability.ProofPin(**asdict(source.proof_contract)),
                  applicability.ProofPin(**asdict(source.proof_mapping))), "PROOF_SOURCE_CONFLICT")
    if result.witness is not None:
        _require((result.witness.target_candidate_sha, result.witness.target_tree_sha,
                  result.witness.target_contract, result.witness.target_mapping) ==
                 (target.candidate_sha, target.candidate_tree_sha,
                  applicability.ProofPin(**asdict(target.proof_contract)),
                  applicability.ProofPin(**asdict(target.proof_mapping))), "PROOF_TARGET_CONFLICT")
    return result, request


def _authority_records(git, catalog, catalog_pin, authority):
    records = {key: applicability.decode_record_identity(catalog[key]) for key in
               ("task", "run", "human", "budget", "operational_authority", "admission", "current")}
    data = {key: _read(git, pin, catalog_pin) for key, pin in records.items()}
    task = validate_task(data["task"])
    run_data = _record(data["run"], Run)
    run_task = _record(run_data["task"], RunTaskReference)
    run = Run(**dict(run_data, task=RunTaskReference(**run_task)))
    _require(run.status == "ACTIVE" and run.head_sha is None, "TERMINAL_OR_NONADMISSION_RUN")
    _require((run.task.id, run.task.revision) == (task.task_id, task.revision), "TASK_RUN_CONFLICT")
    _require(run.return_affinity == task.return_affinity, "RETURN_AFFINITY_CHANGED")
    base = applicability._match(run.base_sha, applicability._SHA)
    git.ancestor(records["task"].commit_sha, base)
    human = _object(data["human"], {"schema", "task", "run", "executor", "profile", "model", "effort",
        "lease_id", "lease_generation", "lease_issued_at", "lease_expires_at", "operation", "opt_in_version",
        "allowed_paths", "limits"})
    _require(human["schema"] == HUMAN_SCHEMA and human["operation"] == "PRIMARY"
             and human["opt_in_version"] == OPT_IN_VERSION, "HUMAN_OPT_IN_REQUIRED")
    for key in ("task", "run"):
        _require(applicability.decode_record_identity(human[key]) == records[key], "HUMAN_BINDING_CONFLICT")
    delegation = declaration._delegation({key: human[key] for key in
        ("executor", "profile", "model", "effort", "lease_id", "lease_generation")} |
        {"human": asdict(_source("human-authority", records["human"]))})
    _require(run.executor == delegation.executor, "EXECUTOR_CHANGED")
    limits = declaration._resources(human["limits"], "human.limits")
    allowed = declaration._paths(human["allowed_paths"], "human.allowed_paths")
    _require(set(allowed) == set(task.scope.modify), "SCOPE_AUTHORITY_CONFLICT")
    issued = declaration._number(human["lease_issued_at"], 2_147_483_647, "lease.issued", zero=True)
    expires = declaration._number(human["lease_expires_at"], 2_147_483_647, "lease.expires")
    _require(issued <= authority.observed_at < expires, "LEASE_EXPIRED_OR_NOT_YET_LIVE")
    budget = _object(data["budget"], {"schema", "human", "limits", "allowed_paths", "applicable_resources"})
    _require(budget["schema"] == BUDGET_SCHEMA
             and declaration._source(budget["human"], "budget.human") == delegation.human
             and declaration._resources(budget["limits"], "budget.limits") == limits
             and declaration._paths(budget["allowed_paths"], "budget.scope") == allowed,
             "BUDGET_AUTHORITY_CHANGED")
    _require(budget["applicable_resources"] == list(RESOURCE_NAMES), "APPLICABLE_ACCOUNTING_UNKNOWN", unknown=True)
    operational = _object(data["operational_authority"],
        {"binding", "delegation", "opt_in", "budget_authority", "limits", "allowed_paths"})
    expected_binding = _object(operational["binding"], declaration.AUTHORITY_BINDING_FIELDS)
    expected = {"task_id": task.task_id, "task_revision": task.revision,
        "acceptance_ids": [item.id for item in task.acceptance], "task_source_sha": records["task"].commit_sha,
        "task_blob_sha": records["task"].blob_sha, "task_envelope_digest": expected_binding["task_envelope_digest"],
        "run_id": run.run_id, "run_source_sha": records["run"].commit_sha,
        "run_record_digest": records["run"].sha256, "base_sha": base}
    applicability._match(expected["task_envelope_digest"], applicability._DIGEST)
    _require(expected_binding == expected and declaration._delegation(operational["delegation"]) == delegation,
             "OPERATIONAL_BINDING_CONFLICT")
    _require(operational["opt_in"] == {"enabled": True, "human": asdict(delegation.human)}
             and operational["budget_authority"] == {"human": asdict(delegation.human),
                "record": asdict(_source("budget-authority", records["budget"]))}
             and operational["limits"] == asdict(limits) and operational["allowed_paths"] == list(allowed),
             "OPERATIONAL_AUTHORITY_CHANGED")
    admission = _object(data["admission"], {"schema", "task", "run", "operation", "admission", "version",
        "admitted_at", "operational_authority", "semantic_digest"})
    _require(admission["schema"] == ADMISSION_SCHEMA and admission["operation"] == "PRIMARY"
             and admission["admission"] == "ADMITTED" and admission["version"] == OPT_IN_VERSION,
             "PROSPECTIVE_PRIMARY_ADMISSION_REQUIRED")
    for key in ("task", "run", "operational_authority"):
        _require(applicability.decode_record_identity(admission[key]) == records[key], "ADMISSION_CHANGED")
    admitted_at = declaration._number(admission["admitted_at"], 2_147_483_647, "admitted_at", zero=True)
    _require(issued <= admitted_at <= authority.observed_at, "ADMISSION_TIME_CONFLICT")
    applicability._match(admission["semantic_digest"], applicability._DIGEST)
    records.update(repository_id=catalog["repository_id"], expected_main_sha=catalog["expected_main_sha"])
    return records, data, expected, delegation, limits, allowed, admitted_at, expires


def _live_identity(value, records, delegation, authority, expires):
    current = _object(value, {"schema", "task", "run", "admission", "operational_authority",
        "delegation", "operation", "run_state", "terminal_ref", "observed_at", "lease_state", "lease_expires_at",
        "authority_current", "risk", "semantic_choice", "tail_state", "effects_state", "accounting_state",
        "spent", "reserved", "current_tips", "consumed_feedback"})
    _require(current["schema"] == CURRENT_SCHEMA, "CURRENTNESS_UNSUPPORTED", unknown=True)
    for key in ("task", "run", "admission", "operational_authority"):
        _require(applicability.decode_record_identity(current[key]) == records[key], "CURRENT_AUTHORITY_CHANGED")
    _require(declaration._delegation(current["delegation"]) == delegation, "LIVE_DELEGATION_CHANGED")
    _require(current["operation"] == "PRIMARY" and current["run_state"] == "ACTIVE"
             and current["terminal_ref"] is None, "TERMINAL_OR_OPERATION_CHANGED")
    _require(type(current["observed_at"]) is int and current["observed_at"] == authority.observed_at
             and current["authority_current"] is True, "CURRENTNESS_UNCERTAIN", unknown=True)
    _require(current["lease_state"] == "LIVE" and type(current["lease_expires_at"]) is int
             and current["lease_expires_at"] == expires, "LIVE_LEASE_CHANGED_OR_LOST")
    _require(current["risk"] == "UNCHANGED" and current["semantic_choice"] == "UNCHANGED", "LIVE_RISK_OR_SEMANTICS_CHANGED")
    _require(current["tail_state"] == "SEALED" and current["effects_state"] == "ESTABLISHED"
             and current["accounting_state"] == "COMPLETE", "PARTIAL_TAIL_OR_CRASH", unknown=True)
    return current


def evaluate_lineage(request: LineageRequest, *, authority: ReadOnlyLineageAuthority | None) -> LineageResult:
    """Describe one exact cataloged history/replay. Even consistency returns BLOCK.

    Missing/partial/crashed/uncertain state is UNKNOWN; factual conflicts BLOCK.
    No consumption, reservation, append, proof execution or terminal action is
    performed. Before/after currentness reads do not implement atomic CAS/ABA.
    """
    request = decode_request(_request_values(request))
    seals, proofs, nodes = [], [], []
    authenticated = False
    try:
        _require(type(authority) is ReadOnlyLineageAuthority, "AUTHORITY_UNAVAILABLE", unknown=True)
        _require(type(authority.repository) is type(Path()), "AUTHORITY_UNAVAILABLE", unknown=True)
        declaration._number(authority.observed_at, 2_147_483_647, "authority.observed_at", zero=True)
        catalog_pin = applicability.decode_record_identity(applicability._record_values(authority.catalog))
        git = applicability._Git(authority.repository)
        catalog = _object(git.record(catalog_pin), {"schema", "repository_id", "expected_main_sha",
            "expected_head_sha", "task", "run", "human", "budget", "operational_authority", "admission",
            "current", "checkpoints", "transitions"})
        _require(catalog["schema"] == CATALOG_SCHEMA, "AUTHORITY_UNSUPPORTED", unknown=True)
        _require(declaration._id(catalog["repository_id"], "catalog.repository_id") == request.repository_id,
                 "REPOSITORY_CONFLICT")
        main = applicability._match(catalog["expected_main_sha"], applicability._SHA)
        head = applicability._match(catalog["expected_head_sha"], applicability._SHA)
        _current_head(git, main, head)
        (records, data, expected, delegation, limits, allowed, admitted_at, expires) = _authority_records(
            git, catalog, catalog_pin, authority)
        current = _live_identity(data["current"], records, delegation, authority, expires)
        _budget(_resources(current["spent"]), _resources(current["reserved"]), limits)
        git.ancestor(main, expected["base_sha"])
        entries = applicability._list(catalog["checkpoints"], MAX_CHECKPOINTS)
        transitions = applicability._list(catalog["transitions"], MAX_CHECKPOINTS - 1)
        for entry in entries:
            _object(entry, {"checkpoint", "feedback", "facts", "contract", "mapping", "originals"})
        pinned = tuple(applicability.decode_record_identity(e["checkpoint"]) for e in entries)
        _require(bool(pinned), "MISSING_HISTORY", unknown=True)
        _require(len(set(pinned)) == len(pinned), "DUPLICATE_SEAL")
        _require(request.checkpoints == pinned, "HISTORY_NOT_EXACT",
                 unknown=len(request.checkpoints) < len(pinned))
        for transition in transitions:
            _object(transition, {"source_ordinal", "target_ordinal", "catalogs"})
        total = sum(len(applicability._list(e["originals"], declaration.MAX_PROOFS)) for e in entries)
        total += sum(len(applicability._list(t["catalogs"], declaration.MAX_PROOFS)) for t in transitions)
        _require(total <= MAX_EVALUATIONS, "RESOURCE_BOUND", unknown=True)
        previous_files = _files(git, expected["base_sha"])
        seen_candidates, seen_feedback, seen_digests = [], [], []
        seen_original_records = set()
        observed_at = admitted_at
        minimum_items = 0
        for ordinal, (entry, checkpoint_pin) in enumerate(zip(entries, pinned), 1):
            raw = _object(_read(git, checkpoint_pin, catalog_pin),
                          {"schema", "binding", "delegation", "checkpoint", "sources", "seal_digest"})
            _require(raw["schema"] == SCHEMA, "SEAL_VERSION_UNSUPPORTED", unknown=True)
            binding = declaration._binding(raw["binding"])
            _require({k: raw["binding"][k] for k in declaration.AUTHORITY_BINDING_FIELDS} == expected,
                     "FIXED_BINDING_CHANGED")
            _require(declaration._delegation(raw["delegation"]) == delegation, "DELEGATION_CHANGED")
            candidate = binding.candidate_sha
            _require(candidate != binding.base_sha and candidate not in seen_candidates, "CANDIDATE_REUSED")
            git.ancestor(binding.base_sha if ordinal == 1 else seen_candidates[-1], candidate)
            git.ancestor(candidate, checkpoint_pin.commit_sha)
            _require(git.commit(candidate)[0] == binding.candidate_tree_sha, "CANDIDATE_TREE_CONFLICT")
            cp_raw = _record(raw["checkpoint"], declaration.Checkpoint)
            _require(cp_raw["ordinal"] == ordinal, "MISSING_PREDECESSOR" if ordinal == 1 else "ORDINAL_CONFLICT",
                     unknown=ordinal == 1 and type(cp_raw["ordinal"]) is int and cp_raw["ordinal"] > 1)
            sources = _object(raw["sources"], {"feedback", "facts", "contract", "mapping", "originals"})
            _require(sources == {k: entry[k] for k in sources}, "SEALED_SOURCES_CHANGED")
            seal_digest = applicability._match(raw["seal_digest"], applicability._DIGEST)
            _require(seal_digest == applicability.canonical_digest({k: v for k, v in raw.items() if k != "seal_digest"}),
                     "SEALED_CONTENT_CHANGED")
            contract_pin = applicability.decode_record_identity(entry["contract"])
            mapping_pin = applicability.decode_record_identity(entry["mapping"])
            contract, mapping = _read(git, contract_pin, catalog_pin), _read(git, mapping_pin, catalog_pin)
            task_binding = {"task_id": binding.task_id, "revision": binding.task_revision,
                            "envelope_digest": binding.task_envelope_digest,
                            "acceptance_ids": list(binding.acceptance_ids)}
            coverage = applicability._coverage(contract, mapping, task_binding)
            _require(binding.proof_contract == declaration.Pin(**asdict(applicability._proof_pin(coverage.contract)))
                     and binding.proof_mapping == declaration.Pin(**asdict(applicability._proof_pin(coverage.mapping))),
                     "PROOF_CONTRACT_CHANGED")
            _require(all(o.conditions.candidate_sha == candidate and
                         (o.kind != "comparison" or o.conditions.base_sha == binding.base_sha)
                         for o in coverage.contract.obligations), "PROOF_SUBJECT_CONFLICT")
            _require(_semantic_digest(contract, mapping, binding.base_sha) == data["admission"]["semantic_digest"],
                     "SEMANTIC_OR_MAPPING_CHANGED")
            facts_pin = applicability.decode_record_identity(entry["facts"])
            facts = _object(_read(git, facts_pin, catalog_pin), {"schema", "checkpoint_digest", "ordinal",
                "candidate_sha", "candidate_tree_sha", "committed", "clean", "observed_at", "changed_paths",
                "progress", "risk", "semantic_choice", "spent", "reserved", "accounting_state", "effects_state",
                "consumed_feedback", "causal_obligation_ids"})
            _require(facts["schema"] == FACTS_SCHEMA and
                     type(facts["ordinal"]) is int and
                     (facts["checkpoint_digest"], facts["ordinal"], facts["candidate_sha"], facts["candidate_tree_sha"]) ==
                     (cp_raw["content_digest"], ordinal, candidate, binding.candidate_tree_sha), "FACTS_BINDING_CONFLICT")
            _require(facts["committed"] is True and facts["clean"] is True
                     and cp_raw["committed"] is True and cp_raw["clean"] is True, "CLEAN_COMMIT_NOT_ATTESTED")
            _require(facts["accounting_state"] == "COMPLETE" and facts["effects_state"] == "ESTABLISHED",
                     "ACCOUNTING_OR_EFFECTS_UNKNOWN", unknown=True)
            _require(facts["risk"] == "UNCHANGED" and facts["semantic_choice"] == "UNCHANGED", "RISK_OR_SEMANTICS_CHANGED")
            at = declaration._number(facts["observed_at"], 2_147_483_647, "facts.observed_at", zero=True)
            _require(observed_at <= at <= authority.observed_at and at < expires, "STALE_CHECKPOINT_TIME")
            observed_at = at
            spent, reserved = _resources(facts["spent"]), _resources(facts["reserved"])
            _budget(spent, reserved, limits)
            _require(spent.corrections == ordinal - 1 and spent.seconds == at - admitted_at,
                     "CORRECTION_OR_WALL_ACCOUNTING_CONFLICT")
            predecessor = None if ordinal == 1 else {"ordinal": ordinal - 1, "digest": seen_digests[-1],
                                                     "candidate_sha": seen_candidates[-1]}
            context = declaration._context({"binding": raw["binding"], "delegation": asdict(delegation),
                "operation": "PRIMARY", "admission": "ADMITTED", "run_state": "ACTIVE", "terminal_ref": None,
                "lease_state": "LIVE", "authority_current": True, **{k: data["operational_authority"][k] for k in
                    ("opt_in", "budget_authority", "limits", "allowed_paths")},
                "spent": facts["spent"], "next_ordinal": ordinal, "predecessor": predecessor,
                "seen_checkpoint_digests": seen_digests.copy(), "seen_feedback_digests": seen_feedback.copy(),
                "seen_candidate_shas": seen_candidates.copy(), "checkpoint_digest": cp_raw["content_digest"]},
                binding, delegation)
            checkpoint = declaration._checkpoint(cp_raw, binding, delegation, coverage, context)
            feedback_pin = applicability.decode_record_identity(entry["feedback"])
            feedback = declaration._feedback(_read(git, feedback_pin, catalog_pin), binding, checkpoint, coverage, context)
            candidate_files = _files(git, candidate)
            changed = _changed(previous_files, candidate_files)
            _require(bool(changed) and set(changed) <= set(allowed), "EMPTY_DELTA_OR_SCOPE_ESCAPE")
            _require(declaration._paths(facts["changed_paths"], "changed_paths") == changed, "DELTA_FACTS_CONFLICT")
            causal = declaration._strings(facts["causal_obligation_ids"], declaration.MAX_PROOFS, "causal", empty=True)
            if ordinal == 1:
                _require(facts["consumed_feedback"] is None and not causal and facts["progress"] == "INITIAL",
                         "INITIAL_FEEDBACK_CONFLICT")
            else:
                previous = seals[-1]
                _require(applicability.decode_record_identity(facts["consumed_feedback"]) == previous.feedback_record,
                         "FEEDBACK_CONSUMPTION_CONFLICT")
                delta = tuple(b - a for a, b in zip(_numbers(previous.checkpoint.resource_usage), _numbers(spent)))
                _require(delta[0] == 1 and all(0 <= d <= r for d, r in zip(delta, _numbers(previous.reserved)))
                         and previous.reserved.corrections == 1, "BUDGET_RESET_OR_RESERVATION_CONFLICT")
                failed_candidate = {item.obligation.id for item in previous.feedback.items if item.outcome == "FAIL"}
                failed_candidate &= {o.id for o in nodes[-1]["coverage"].contract.obligations if o.kind == "candidate"}
                _require(facts["progress"] == "ESTABLISHED" and bool(causal) and set(causal) <= failed_candidate,
                         "CAUSAL_PROGRESS_NOT_ESTABLISHED")
            node = {"binding": binding, "coverage": coverage, "contract_pin": contract_pin, "mapping_pin": mapping_pin}
            original_pins = _identities(entry["originals"], declaration.MAX_PROOFS)
            _require(len(original_pins) == len(coverage.mapping.proofs) and len(set(original_pins)) == len(original_pins),
                     "ORIGINAL_PROOF_POPULATION_CONFLICT")
            originals, by_proof = [], {}
            for pin in original_pins:
                result, proof_request = _proof(git, pin, authority, catalog_pin, records, node, node)
                _require(result.state == applicability.State.VALID and result.authority_authenticated,
                         "ORIGINAL_PROOF_UNKNOWN", unknown=True)
                source = result.source
                _require(source is not None and source.source_proof_id not in by_proof
                         and source.evidence not in seen_original_records, "DUPLICATE_ORIGINAL_PROOF")
                by_proof[source.source_proof_id] = result
                seen_original_records.add(source.evidence)
                originals.append(result)
            _require(set(by_proof) == {p.id for p in coverage.mapping.proofs}, "ORIGINAL_PROOF_MAPPING_CONFLICT")
            minimum_items += sum(p.conditions.population_size * (2 if p.kind == "comparison" else 1)
                                 for p in coverage.mapping.proofs)
            _require(spent.test_items >= minimum_items, "OBSERVED_ITEMS_NOT_ACCOUNTED")
            for evidence in checkpoint.evidence:
                original = by_proof[evidence.proof_id].source
                _require(evidence.provenance == _source(original.evidence_id, original.evidence)
                         and evidence.outcome == original.outcome and evidence.validity == "VALID"
                         and evidence.complete and evidence.stable and not evidence.conflicting,
                         "SELF_CERTIFIED_OR_RELABELLED_PROOF")
            seals.append(SealedFact(checkpoint_pin, seal_digest, binding, delegation, checkpoint, feedback_pin, feedback,
                                    reserved, facts_pin, contract_pin, mapping_pin, original_pins, tuple(originals)))
            node["originals"] = by_proof
            nodes.append(node)
            previous_files = candidate_files
            seen_candidates.append(candidate)
            seen_digests.append(seal_digest)
            seen_feedback.append(feedback.content_digest)
        _require(head == seen_candidates[-1], "CATALOG_TIP_CONFLICT")
        _require(current["current_tips"] == [seen_digests[-1]], "COMPETING_OR_MISSING_TIP",
                 unknown=not current["current_tips"])
        _require(_identities(current["consumed_feedback"]) == tuple(s.feedback_record for s in seals[:-1]),
                 "UNRECONCILED_FEEDBACK_EFFECT", unknown=True)
        spent, reserved = _resources(current["spent"]), _resources(current["reserved"])
        _budget(spent, reserved, limits)
        _require(spent.corrections == len(seals) - 1 and spent.seconds == authority.observed_at - admitted_at
                 and all(a <= b for a, b in zip(_numbers(seals[-1].checkpoint.resource_usage), _numbers(spent)))
                 and reserved == seals[-1].reserved, "LIVE_ACCOUNTING_RESET_OR_DRIFT")
        _require(len(transitions) == len(nodes) - 1, "MISSING_TRANSITION_PROOFS", unknown=True)
        seen_witnesses = set()
        for ordinal, transition in enumerate(transitions, 1):
            _require(type(transition["source_ordinal"]) is int and type(transition["target_ordinal"]) is int
                     and (transition["source_ordinal"], transition["target_ordinal"]) == (ordinal, ordinal + 1),
                     "TRANSITION_ORDER_CONFLICT")
            left, right = nodes[ordinal - 1:ordinal + 1]
            required = {o.id for o in right["coverage"].contract.obligations}
            covered = set()
            for pin in _identities(transition["catalogs"], declaration.MAX_PROOFS):
                result, proof_request = _proof(git, pin, authority, catalog_pin, records, left, right)
                identity = proof_request.target_obligation.id
                _require(identity in required and identity not in covered and proof_request.witness not in seen_witnesses,
                         "DUPLICATE_OR_COLLAPSED_APPLICABILITY")
                expected_original = left["originals"].get(proof_request.source_proof_id)
                _require(expected_original is not None and proof_request.evidence == expected_original.source.evidence,
                         "APPLICABILITY_ORIGINAL_CONFLICT")
                if result.source is not None:
                    _require(result.source == expected_original.source, "ORIGINAL_OBSERVATION_CHANGED")
                covered.add(identity)
                seen_witnesses.add(proof_request.witness)
                proofs.append(ProofFact(ordinal, ordinal + 1, result))
            _require(covered == required, "DISTINCT_OBLIGATIONS_MISSING", unknown=True)
        authenticated = True
        observation = "CONSISTENT"
        if request.replay is not None:
            if request.replay not in pinned:
                # Authenticate the delivered bytes but never enroll a new seal.
                _read(git, request.replay, catalog_pin)
                raise _Stop("CONFLICTING_OR_UNCATALOGED_REPLAY")
            observation = "SAME_FACT"
        _current_head(git, main, head)
        unknown = any(p.result.state == applicability.State.UNKNOWN for p in proofs)
        invalidated = any(p.result.state == applicability.State.INVALIDATED for p in proofs)
        reasons = (("PROOF_APPLICABILITY_UNKNOWN",) if unknown else ())
        reasons += (("PROOF_INVALIDATED",) if invalidated else ()) + ("NOT_ACTIVATED",)
        return LineageResult(SCHEMA, "UNKNOWN" if unknown else "BLOCK", observation, reasons,
                             authenticated, tuple(seals), tuple(proofs))
    except _Stop as exc:
        status, reason = exc.status, exc.reason
    except applicability._Unknown as exc:
        status = "BLOCK" if exc.reason.code == applicability.ReasonCode.LINEAGE_CONFLICT else "UNKNOWN"
        reason = exc.reason.code.value
    except declaration.IntraRunContractError:
        status, reason = "BLOCK", "DECLARATION_CONFLICT"
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration, UnicodeError, RecursionError):
        status, reason = "UNKNOWN", "SOURCE_RECORD_CORRUPT_OR_MISSING"
    return LineageResult(SCHEMA, status, "INCOMPLETE" if status == "UNKNOWN" else "CONFLICT",
                         (reason, "NOT_ACTIVATED"), authenticated, tuple(seals), tuple(proofs))
