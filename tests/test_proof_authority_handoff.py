"""VP-03C bounded disposable real-Git diagnostics, never production issuers.

Reuse only VP-03B's minimal object fixture helpers, not its test suite. Fixture
snapshots are preexisting-looking immutable specimens, not checkpoint writes
through Runtime. No network, live proof, admission or execution is invoked.
"""

import copy
import hashlib
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from aios_renew import proof_applicability as applicability
from aios_renew import proof_authority_handoff as module
from aios_renew import proof_checkpoint_lineage as lineage
from aios_renew.proof_coverage_contract import proof_mapping_digest
from test_proof_checkpoint_lineage import build_case, digest, mapping_for, pin


@pytest.fixture(scope="module")
def specimens(tmp_path_factory):
    cache = {}
    def get(distinct=False):
        if distinct not in cache:
            cache[distinct] = build_case(tmp_path_factory.mktemp("handoff") / "repository", distinct=distinct)
        return cache[distinct]
    return get


def request_values(fixture, authority=None, replay=None):
    authority = authority or fixture.authority
    catalog = fixture.value(asdict(authority.catalog))
    source, target = catalog["checkpoints"][-2:]
    binding = fixture.value(source["checkpoint"])["binding"]
    admission = {"task": catalog["task"], "task_id": binding["task_id"], "task_revision": binding["task_revision"],
        "task_envelope_digest": binding["task_envelope_digest"], "run": catalog["run"], "run_id": binding["run_id"],
        "base_sha": binding["base_sha"], "admission": catalog["admission"]}
    def candidate(entry):
        current = fixture.value(entry["checkpoint"])["binding"]
        return {"candidate_sha": current["candidate_sha"], "tree_sha": current["candidate_tree_sha"],
            "checkpoint": entry["checkpoint"], "contract": entry["contract"], "mapping": entry["mapping"],
            "contract_pin": current["proof_contract"], "mapping_pin": current["proof_mapping"]}
    originals = []
    for identity in source["originals"]:
        proof = fixture.value(identity)
        review = fixture.value(proof["review"])
        originals.append({"proof_id": proof["source_proof_id"], "proof_digest": review["source_proof_digest"],
            **{k: proof[k] for k in ("result", "evidence", "raw")}})
    obligations = []
    for identity in catalog["transitions"][-1]["catalogs"]:
        proof = fixture.value(identity)
        obligations.append({"obligation": proof["target_obligation"],
            "source_proof_id": proof["source_proof_id"], "witness": proof["witness"]})
    return {"schema": module.SCHEMA, "admission": admission, "source": candidate(source), "target": candidate(target),
        "originals": originals, "obligations": obligations, "replay": replay}


def inspect(fixture, values=None, authority=None):
    return module.inspect_content_handoff(module.decode_request(values or request_values(fixture, authority)),
        fixture_authority=authority or fixture.authority)


def inactive(result):
    assert result.status in ("BLOCK", "UNKNOWN")
    assert module.ISSUER_UNAVAILABLE in result.reasons
    assert result.issuer_state == "UNAVAILABLE" and result.producer_state == "NOT_INSTALLED"
    for value in (result, *result.targets):
        for name, descriptor in module.InactiveEffects.__dataclass_fields__.items():
            observed = getattr(value, name)
            if name == "activation":
                assert observed == "NOT_ACTIVATED"
            elif name == "base_replay_required_gates":
                assert observed == module.BR_GATES
            else:
                assert observed is False, name
        if type(value) is module.TargetFact:
            assert value.state == module.State.UNKNOWN


def snapshot(repo):
    return {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in repo.rglob("*") if p.is_file()}


def test_coherent_fake_repository_is_not_an_independent_canonical_issuer(specimens, monkeypatch):
    fixture = specimens()
    request = module.decode_request(request_values(fixture))
    def forbidden(*args, **kwargs):
        raise AssertionError("missing producer must not read caller Git objects")
    monkeypatch.setattr(applicability._Git, "__init__", forbidden)
    monkeypatch.setattr(lineage, "evaluate_lineage", forbidden)
    result = module.evaluate_handoff(request)
    assert result.schema == module.SCHEMA and result.status == "UNKNOWN"
    assert result.admission_status == "UNKNOWN" and result.content_lineage is None
    assert result.currentness is None and result.repository_id is None
    assert all(v.content_applicability is None for v in result.targets)
    assert result == module.evaluate_handoff(request)
    inactive(result)
    with pytest.raises(TypeError):
        module.evaluate_handoff(request, authority=fixture.authority)
    with pytest.raises(TypeError):
        module.evaluate_handoff(request, producer=lambda: fixture.authority)


@pytest.mark.parametrize("field,value", [
    ("repository", "attacker"), ("repository_id", "invented-root"), ("catalog", {}),
    ("trusted_issuer", "Human"), ("expected_main_sha", "0" * 40), ("approved", True),
    ("declared_state", "VALID"), ("acceptance_verdict", "PASS"), ("observed_at", 1010),
])
def test_request_cannot_select_authority_or_self_attest(specimens, field, value):
    values = request_values(specimens())
    values[field] = value
    with pytest.raises(module.HandoffInputError):
        module.decode_request(values)


def test_strict_decoding_versions_types_identities_and_bounds(specimens):
    values = request_values(specimens())
    variants = []
    for schema in ("canonical-proof-handoff-v0", module.CONTENT_SCHEMA):
        variants.append(dict(values, schema=schema))
    for field, value in (("task_revision", True), ("task_id", "TASK bad"), ("base_sha", "main")):
        altered = copy.deepcopy(values)
        altered["admission"][field] = value
        variants.append(altered)
    for field, value in (("path", "../escape"), ("commit_sha", "HEAD:records/task.json"), ("sha256", "g" * 64)):
        altered = copy.deepcopy(values)
        altered["admission"]["task"][field] = value
        variants.append(altered)
    variants.extend((dict(values, originals=[]), dict(values, obligations=values["obligations"] * (module.MAX_SELECTIONS + 1)),
                     dict(values, replay=object()), dict(values, schema="x" * (applicability.MAX_RECORD_BYTES + 1))))
    for altered in variants:
        with pytest.raises(module.HandoffInputError):
            module.decode_request(altered)
    typed = module.decode_request(values)
    with pytest.raises(module.HandoffInputError):
        module.evaluate_handoff(replace(typed, admission=object()))
    with pytest.raises(module.HandoffInputError):
        module.evaluate_handoff(replace(typed, obligations=list(typed.obligations)))
    with pytest.raises(module.HandoffInputError):
        module.evaluate_handoff(replace(typed, source=replace(typed.source, candidate_sha="main")))
    with pytest.raises(FrozenInstanceError):
        typed.admission.task_revision = 2


def test_content_projection_exact_original_fail_and_separate_currentness(specimens, monkeypatch):
    fixture = specimens()
    before = snapshot(fixture.repo)
    calls = []
    original = lineage.evaluate_lineage
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(lineage, "evaluate_lineage", counted)
    result = inspect(fixture)
    assert len(calls) == 1
    assert result.schema == module.CONTENT_SCHEMA and result.observation == "CONTENT_CONSISTENT", result.reasons
    assert result.content_lineage.history_authenticated
    assert result.admission_status == "ADMITTED"
    assert result.admission == module.decode_request(request_values(fixture)).admission
    assert result.currentness.state == "CONSISTENT"
    assert result.currentness.observed_at == fixture.authority.observed_at
    assert result.currentness.observed_main_before == result.currentness.observed_main_after == fixture.catalog["expected_main_sha"]
    fact = result.targets[0]
    original = result.content_lineage.seals[0].originals[0].source
    assert fact.original == original == fact.content_applicability.source
    assert fact.content_applicability == result.content_lineage.proofs[0].result
    assert fact.content_applicability.state == module.State.VALID
    assert fact.original.outcome == "FAIL" and fact.original.exit_code == 1
    assert fact.original.subject_sha == result.source.candidate_sha != result.target.candidate_sha
    assert fact.original.evidence != fact.selection.witness
    assert fact.content_applicability.witness.record == fact.selection.witness
    assert fact.content_applicability.witness.target_candidate_sha == result.target.candidate_sha
    assert fact.obligation.blocking and fact.mapped_proof.id == "proof-unit"
    assert before == snapshot(fixture.repo)
    inactive(result)
    with pytest.raises((TypeError, ValueError)):
        replace(result, issuer_authenticated=True)
    with pytest.raises(FrozenInstanceError):
        fact.content_applicability.source.outcome = "PASS"


@pytest.mark.parametrize("field", ["task", "run", "admission", "task_revision", "run_id", "base_sha", "task_envelope_digest"])
def test_exact_task_run_and_admission_binding_cannot_be_swapped(specimens, field):
    fixture = specimens()
    values = request_values(fixture)
    if field in ("task", "run", "admission"):
        values["admission"][field]["sha256"] = "0" * 64
    else:
        values["admission"][field] = {"task_revision": 2, "run_id": "RUN-SWAPPED", "base_sha": "0" * 40,
            "task_envelope_digest": "0" * 64}[field]
    result = inspect(fixture, values)
    assert result.status == "BLOCK" and "EXACT_ADMISSION_BINDING_CONFLICT" in result.reasons
    assert all(v.original is None for v in result.targets)
    inactive(result)


@pytest.mark.parametrize("where,field", [
    ("source", "candidate_sha"), ("target", "tree_sha"), ("source", "checkpoint"),
    ("target", "contract"), ("target", "mapping_pin"), ("original", "evidence"),
    ("original", "result"), ("original", "raw"), ("original", "proof_digest"),
    ("obligation", "witness"), ("obligation", "obligation"),
])
def test_exact_candidate_mapping_evidence_raw_and_witness_identity(specimens, where, field):
    fixture = specimens()
    values = request_values(fixture)
    parent = (values["originals"][0] if where == "original" else values["obligations"][0]
              if where == "obligation" else values[where])
    if type(parent[field]) is dict:
        key = "sha256" if "sha256" in parent[field] else "digest"
        parent[field][key] = "0" * 64
    else:
        parent[field] = "0" * (64 if field == "proof_digest" else 40)
    result = inspect(fixture, values)
    assert result.status == "BLOCK" and result.observation == "CONTENT_CONFLICT"
    inactive(result)


def mixed_authority(fixture):
    """Alter only cross-candidate content, retaining every original observation."""
    catalog = copy.deepcopy(fixture.catalog)
    parent = fixture.catalog_pin["commit_sha"]
    for index, old_pin in enumerate(catalog["transitions"][0]["catalogs"]):
        proof = fixture.value(old_pin)
        name = proof["target_obligation"]["id"]
        if name not in ("integration", "ordering"):
            continue
        if name == "ordering":
            proof["witness"]["sha256"] = "0" * 64
        else:
            review = fixture.value(proof["review"])
            plan = next(v for v in review["dimensions"] if v["dimension"] == "profile")
            context = fixture.value(plan["target_context"])
            context["facts"]["state"] = "independently-observed-profile-drift"
            parent, pins = fixture.commit(parent, {plan["target_context"]["path"]: context})
            plan["target_context"] = pins[plan["target_context"]["path"]]
            parent, pins = fixture.commit(parent, {proof["review"]["path"]: review})
            proof["review"] = pins[proof["review"]["path"]]
            witness = fixture.value(proof["witness"])
            witness["review"] = proof["review"]
            comparison = next(v for v in witness["dimensions"] if v["dimension"] == "profile")
            objects = [[p, ["100644", fixture.trees[proof["target_candidate_sha"]][p]]] for p in sorted(plan["paths"])]
            comparison["target_digest"] = digest({"conditions": context["conditions"], "facts": context["facts"], "objects": objects})
            parent, pins = fixture.commit(parent, {proof["witness"]["path"]: witness})
            proof["witness"] = pins[proof["witness"]["path"]]
        parent, pins = fixture.commit(parent, {old_pin["path"]: proof})
        catalog["transitions"][0]["catalogs"][index] = pins[old_pin["path"]]
    parent, pins = fixture.commit(parent, {"lineage/mixed-catalog.json": catalog})
    return replace(fixture.authority, catalog=applicability.decode_record_identity(pins["lineage/mixed-catalog.json"]))


def test_mixed_content_states_keep_all_distinct_obligations_and_fail(specimens):
    fixture = specimens(distinct=True)
    authority = mixed_authority(fixture)
    result = inspect(fixture, authority=authority)
    assert result.content_lineage.history_authenticated, result.reasons
    by_id = {v.obligation.id: v for v in result.targets}
    assert set(by_id) == {"unit", "integration", "concurrency", "ordering", "comparison"}
    assert {v.content_applicability.state for v in result.targets} == set(module.State)
    assert by_id["unit"].content_applicability.state == module.State.VALID
    assert by_id["unit"].original.outcome == "FAIL"
    assert by_id["integration"].content_applicability.state == module.State.INVALIDATED
    assert by_id["ordering"].content_applicability.state == module.State.UNKNOWN
    assert by_id["comparison"].obligation.kind == "comparison"
    assert by_id["comparison"].obligation.conditions.base_sha == fixture.catalog["expected_main_sha"]
    assert by_id["concurrency"].obligation.conditions.worker_mode == "parallel"
    assert by_id["concurrency"].obligation.conditions.workers == 2
    assert by_id["ordering"].obligation.conditions.ordering_ref == "opposite-v1"
    conditions = by_id["unit"].obligation.conditions
    for value in (conditions.fixture_refs, conditions.shared_state_ref, conditions.environment_ref,
                  conditions.toolchain_ref, conditions.profile_ref, conditions.repetition_ref):
        assert value
    for fact in result.targets:
        assert fact.obligation.acceptance_ids == ("AC1",) and fact.obligation.blocking
        if fact.content_applicability.witness is not None:
            assert {v[0] for v in fact.content_applicability.witness.dependency_digests} == set(applicability.DIMENSIONS)
    inactive(result)


def test_missing_target_and_duplicate_receipt_cannot_collapse_coverage(specimens):
    fixture = specimens(distinct=True)
    values = request_values(fixture)
    values["obligations"].pop()
    result = inspect(fixture, values)
    assert result.status == "BLOCK" and "DISTINCT_TARGET_POPULATION_CONFLICT" in result.reasons
    inactive(result)
    values = request_values(specimens())
    for key in ("originals", "obligations"):
        duplicated = copy.deepcopy(values)
        duplicated[key].append(copy.deepcopy(duplicated[key][0]))
        result = module.evaluate_handoff(module.decode_request(duplicated))
        assert result.status == "BLOCK"
        inactive(result)


@pytest.mark.parametrize("defect", ["issuer", "catalog-pin", "torn", "feedback", "cost-reset", "lease"])
def test_forged_issuer_catalog_crash_feedback_cost_and_lease_negatives(specimens, defect):
    fixture = specimens()
    request = request_values(fixture)
    if defect == "catalog-pin":
        authority = replace(fixture.authority, catalog=replace(fixture.authority.catalog, sha256="0" * 64))
    elif defect == "issuer":
        authority, _ = fixture.variant(edit_catalog=lambda c: c.update(issuer="Runtime", trusted=True))
    else:
        identity = fixture.catalog["current"]
        current = fixture.value(identity)
        if defect == "torn":
            current["tail_state"] = "TORN"
        elif defect == "feedback":
            current["consumed_feedback"].append(current["consumed_feedback"][0])
        elif defect == "cost-reset":
            current["spent"]["tokens"] = 0
        elif defect == "lease":
            current["delegation"]["lease_generation"] += 1
        authority, _ = fixture.variant([(identity, current)])
    before = snapshot(fixture.repo)
    result = inspect(fixture, request, authority)
    assert result.observation != "CONTENT_CONSISTENT" and all(v.original is None for v in result.targets)
    assert before == snapshot(fixture.repo)
    inactive(result)


@pytest.mark.parametrize("which", ["main", "HEAD", "during-read"])
def test_stale_and_torn_currentness_is_separately_observed(specimens, monkeypatch, which):
    fixture = specimens()
    main, head = fixture.catalog["expected_main_sha"], fixture.catalog["expected_head_sha"]
    try:
        if which == "main":
            fixture.git("update-ref", "refs/heads/main", head)
        elif which == "HEAD":
            fixture.git("update-ref", "--no-deref", "HEAD", main)
        else:
            original = applicability._Git._read
            calls = []
            def moved(reader, args, oid=None):
                value = original(reader, args, oid)
                if args == ["rev-parse", "--verify", "refs/heads/main"]:
                    calls.append(1)
                    if len(calls) == 3:
                        fixture.git("update-ref", "refs/heads/main", head)
                return value
            monkeypatch.setattr(applicability._Git, "_read", moved)
        result = inspect(fixture)
        assert result.status == "UNKNOWN" and result.currentness.state == "UNKNOWN"
        assert "SEPARATELY_OBSERVED_CURRENTNESS_CONFLICT" in result.reasons
        assert result.admission_status == "UNKNOWN"
        assert all(v.content_applicability is None for v in result.targets)
        inactive(result)
    finally:
        fixture.git("update-ref", "refs/heads/main", main)
        fixture.git("update-ref", "--no-deref", "HEAD", head)


@pytest.mark.parametrize("dimension", [None, "integration_ref", "repetition_ref", "environment_ref"])
def test_validly_reissued_mapping_or_condition_drift_still_blocks(specimens, dimension):
    fixture = specimens()
    entry = fixture.catalog["checkpoints"][-1]
    contract = fixture.value(entry["contract"])
    if dimension is None:
        contract["obligations"][0]["claim"]["text"] = "A different semantic obligation."
    else:
        contract["obligations"][0]["conditions"][dimension] = "drifted-condition-v2"
    parsed, mapping = mapping_for(contract)
    seal = fixture.value(entry["checkpoint"])
    seal["binding"]["proof_contract"] = pin(parsed)
    seal["binding"]["proof_mapping"] = {"id": mapping["id"], "revision": mapping["revision"], "digest": proof_mapping_digest(mapping)}
    authority, _ = fixture.variant([(entry["contract"], contract), (entry["mapping"], mapping),
                                   (entry["checkpoint"], seal)], reissue_seals=True)
    result = inspect(fixture, request_values(fixture, authority), authority)
    assert result.status == "BLOCK" and "SEMANTIC_OR_MAPPING_CHANGED" in result.reasons
    inactive(result)


def test_replay_missing_conflicting_duplicate_deliveries_and_no_effects(specimens, monkeypatch):
    fixture = specimens()
    replay = fixture.catalog["checkpoints"][-1]["checkpoint"]
    values = request_values(fixture, replay=replay)
    # Build conflicting immutable specimen bytes, never append a live checkpoint.
    parent, pins = fixture.commit(fixture.catalog_pin["commit_sha"], {"specimens/replay.json": {"forged": "seal"}})
    parent, roots = fixture.commit(parent, {"specimens/catalog.json": fixture.catalog})
    conflict_authority = replace(fixture.authority, catalog=applicability.decode_record_identity(roots["specimens/catalog.json"]))
    conflict_values = request_values(fixture, conflict_authority, pins["specimens/replay.json"])
    missing_values = request_values(fixture, replay=dict(replay, blob_sha="0" * 40))
    before = snapshot(fixture.repo)
    real_run = applicability.subprocess.run
    calls = []
    def readonly(args, **kwargs):
        assert args[0] == "git"
        command = args[args.index("-C") + 2]
        assert command in ("cat-file", "rev-parse"), args
        calls.append(command)
        return real_run(args, **kwargs)
    monkeypatch.setattr(applicability.subprocess, "run", readonly)
    replay_result = inspect(fixture, values)
    assert replay_result.observation == "CONTENT_SAME_FACT"
    assert replay_result == inspect(fixture, values)
    assert replay_result.content_lineage.seals[-1].checkpoint.resource_usage.corrections == 1
    conflict = inspect(fixture, conflict_values, conflict_authority)
    assert conflict.status == "BLOCK" and "CONFLICTING_OR_UNCATALOGED_REPLAY" in conflict.reasons
    missing = inspect(fixture, missing_values)
    assert missing.status == "UNKNOWN"
    assert before == snapshot(fixture.repo) and calls
    for result in (replay_result, conflict, missing):
        inactive(result)
    count = len(calls)
    canonical = module.evaluate_handoff(module.decode_request(values))
    assert count == len(calls)
    inactive(canonical)


def test_arbitrary_duck_typed_sources_and_missing_content_roots_are_unavailable(specimens):
    fixture = specimens()
    request = module.decode_request(request_values(fixture))
    class InventedIssuer:
        repository = fixture.repo
        catalog = fixture.authority.catalog
        observed_at = 1010
        trusted = True
    for authority in (None, InventedIssuer()):
        result = module.inspect_content_handoff(request, fixture_authority=authority)
        assert result.content_lineage is None and "CONTENT_SOURCE_UNAVAILABLE" in result.reasons
        inactive(result)
    class HookMeta(type):
        def __eq__(self, other):
            raise AssertionError("untrusted type equality hook was invoked")
    class Hooked(metaclass=HookMeta):
        pass
    with pytest.raises(module.HandoffInputError):
        module.decode_request(dict(request_values(fixture), replay=Hooked()))
    with pytest.raises(module.HandoffInputError):
        module.evaluate_handoff(replace(request, source=replace(request.source, contract=Hooked())))


def test_incomplete_mapping_cannot_project_target_coverage(specimens):
    fixture = specimens()
    entry = fixture.catalog["checkpoints"][-1]
    mapping = fixture.value(entry["mapping"])
    mapping["entries"][0]["obligation_id"] = "unmapped-extra"
    seal = fixture.value(entry["checkpoint"])
    seal["binding"]["proof_mapping"]["digest"] = proof_mapping_digest(mapping)
    authority, _ = fixture.variant([(entry["mapping"], mapping), (entry["checkpoint"], seal)], reissue_seals=True)
    result = inspect(fixture, request_values(fixture, authority), authority)
    assert "MAPPING_CONFLICT" in result.reasons
    assert all(v.obligation is None and v.content_applicability is None for v in result.targets)
    inactive(result)


def test_incomplete_independently_pinned_footprint_stays_unknown(specimens):
    fixture = specimens()
    original_pin = fixture.catalog["transitions"][0]["catalogs"][0]
    proof = fixture.value(original_pin)
    review = fixture.value(proof["review"])
    next(v for v in review["dimensions"] if v["dimension"] == "helpers")["coverage"] = "UNKNOWN"
    parent, pins = fixture.commit(fixture.catalog_pin["commit_sha"], {proof["review"]["path"]: review})
    proof["review"] = pins[proof["review"]["path"]]
    witness = fixture.value(proof["witness"])
    witness["review"] = proof["review"]
    parent, pins = fixture.commit(parent, {proof["witness"]["path"]: witness})
    proof["witness"] = pins[proof["witness"]["path"]]
    parent, pins = fixture.commit(parent, {original_pin["path"]: proof})
    catalog = copy.deepcopy(fixture.catalog)
    catalog["transitions"][0]["catalogs"][0] = pins[original_pin["path"]]
    parent, pins = fixture.commit(parent, {"specimens/unknown-footprint-catalog.json": catalog})
    authority = replace(fixture.authority,
        catalog=applicability.decode_record_identity(pins["specimens/unknown-footprint-catalog.json"]))
    result = inspect(fixture, request_values(fixture, authority), authority)
    assert result.content_lineage.history_authenticated
    fact = result.targets[0]
    assert fact.content_applicability.state == module.State.UNKNOWN
    assert fact.original.outcome == "FAIL" and fact.content_applicability.applicable_obligation is None
    assert any(v.code == applicability.ReasonCode.DEPENDENCY_INCOMPLETE and v.dimension == "helpers"
               for v in fact.content_applicability.reasons)
    inactive(result)


def test_swapped_original_evidence_cannot_impersonate_source_subject(specimens):
    fixture = specimens()
    original_pin = fixture.catalog["checkpoints"][0]["originals"][0]
    proof = fixture.value(original_pin)
    other = fixture.value(fixture.catalog["checkpoints"][1]["originals"][0])
    proof["evidence"] = other["evidence"]
    authority, _ = fixture.variant([(original_pin, proof)], reissue_seals=True)
    result = inspect(fixture, request_values(fixture), authority)
    assert not result.content_lineage.history_authenticated
    assert "ORIGINAL_PROOF_UNKNOWN" in result.reasons
    assert all(v.original is None for v in result.targets)
    inactive(result)
