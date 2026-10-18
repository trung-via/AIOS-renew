"""Synthetic VP-02 contracts only; no verification child process or real lineage."""

import builtins
import copy
import os
import socket
import subprocess
import time
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

from aios_renew.proof_coverage_contract import (
    BR_GATES, CONTRACT_SCHEMA, EXECUTION_DEFAULT, MAPPING_SCHEMA,
    MAX_OBLIGATIONS, MAX_POPULATION, MAX_REFERENCES, MAX_REVISION, MAX_TEXT,
    OBSERVATION_SCHEMA, REPLAY_SCHEMA, ProofCoverageError,
    candidate_disposition, evaluate_base_replay, obligations_equivalent,
    proof_conditions_equivalent, proof_mapping_digest,
    validate_proof_contract, validate_proof_mapping,
    SEMANTIC_INTAKE_SCHEMA, SemanticProofIntake, validate_semantic_proof_declaration,
)
from aios_renew.task import validate_semantic_proof_intake


CANDIDATE = "a" * 40
BASE = "b" * 40
PROVENANCE = {"authority_ref": "approval-v1", "review_ref": "review-v1", "source_ref": "source-v1"}


def conditions(**changes):
    data = {
        "candidate_sha": CANDIDATE, "base_sha": None,
        "population_ref": "population-v1", "population_size": 8,
        "test_ref": "test-code-v1", "fixture_refs": ["fixture-a-v1", "fixture-b-v1"],
        "shared_state_ref": "isolated-state-v1", "profile_ref": "serial-profile-v1",
        "worker_mode": "serial", "workers": 1, "concurrency_ref": "no-concurrency-v1",
        "ordering_ref": "declared-order-v1", "integration_ref": "unit-v1",
        "repetition_ref": "single-observation-v1", "toolchain_ref": "toolchain-v1",
        "environment_ref": "environment-v1", "evidence_kind": "conformance-v1",
        "evidence_schema": "observation-v1", "collection_ref": "collection-v1",
    }
    data.update(changes)
    return data


def obligation(identity="proof-unit", **changes):
    data = {
        "id": identity, "revision": 1,
        "claim": {"id": "semantic-safety-v1", "text": "The admitted operation preserves required safety."},
        "acceptance_ids": ["AC1"], "kind": "candidate", "blocking": True,
        "conditions": conditions(), "provenance": copy.deepcopy(PROVENANCE),
    }
    data.update(changes)
    return data


def contract(*extra):
    return {
        "schema": CONTRACT_SCHEMA, "id": "contract-v1", "revision": 1,
        "execution_default": EXECUTION_DEFAULT,
        "task": {"task_id": "TASK-SYNTHETIC", "revision": 1,
                 "envelope_digest": "c" * 64, "acceptance_ids": ["AC1"]},
        "provenance": copy.deepcopy(PROVENANCE), "obligations": [obligation(), *extra],
    }


def mapping_for(data, *, groups=None):
    """Build explicit synthetic entries; groups are supplied, never guessed."""
    parsed = validate_proof_contract(data, expected_task=data["task"])
    raw = {item["id"]: item for item in data["obligations"]}
    proofs = {}
    entries = []
    for item in parsed.obligations:
        identity = groups[item.id] if groups else f"mapped-{item.id}"
        if identity not in proofs:
            proofs[identity] = {"id": identity, "kind": item.kind, "claims": [],
                                "conditions": copy.deepcopy(raw[item.id]["conditions"]),
                                "provenance": copy.deepcopy(PROVENANCE)}
        if raw[item.id]["claim"] not in proofs[identity]["claims"]:
            proofs[identity]["claims"].append(copy.deepcopy(raw[item.id]["claim"]))
        entries.append({"obligation_id": item.id, "obligation_revision": item.revision,
                        "obligation_digest": item.digest, "proof_id": identity})
    return {"schema": MAPPING_SCHEMA, "id": "reviewed-map-v1", "revision": 1,
            "contract_id": parsed.id, "contract_revision": parsed.revision,
            "contract_digest": parsed.digest, "provenance": copy.deepcopy(PROVENANCE),
            "proofs": list(proofs.values()), "entries": entries}


def pin(data):
    return {"id": data["id"], "revision": data["revision"], "digest": proof_mapping_digest(data)}


def test_prospective_requirements_reuse_contract_rules_and_preserve_every_condition():
    from aios_renew.proof_coverage_contract import semantic_requirement, validate_semantic_task_requirements
    data = contract(obligation("parallel-condition", conditions=conditions(
        fixture_refs=["integration-fixture"], shared_state_ref="shared-integration",
        profile_ref="parallel-profile", worker_mode="parallel", workers=2,
        concurrency_ref="concurrent", ordering_ref="ordered", integration_ref="integration",
        repetition_ref="repeated", toolchain_ref="alternate-toolchain", environment_ref="alternate-env",
        population_ref="integration-population", collection_ref="integration-collection",
        evidence_kind="integration-kind", evidence_schema="integration-schema")))
    requirements = [{key: copy.deepcopy(value) for key, value in item.items() if key != "provenance"}
                    for item in data["obligations"]]
    parsed = validate_semantic_task_requirements(requirements, expected_task=data["task"])
    canonical = validate_proof_contract(data, expected_task=data["task"])
    assert parsed == tuple(semantic_requirement(item) for item in canonical.obligations)
    assert {item.conditions.workers for item in parsed} == {1, 2}
    assert validate_semantic_task_requirements(list(reversed(requirements)), expected_task=data["task"]) == parsed
    requirements[0]["conditions"]["fixture_refs"].append("later-mutation")
    assert all("later-mutation" not in item.conditions.fixture_refs for item in parsed)
    with pytest.raises(FrozenInstanceError):
        parsed[0].conditions.environment_ref = "mutated"
    # Future-only lexical restrictions never change the historical validator.
    legacy = contract()
    legacy["obligations"][0]["claim"]["text"] = "echo legacy-opaque"
    assert validate_proof_contract(legacy, expected_task=legacy["task"])
    with pytest.raises(ProofCoverageError, match="executable"):
        validate_semantic_task_requirements(
            [{key: value for key, value in legacy["obligations"][0].items() if key != "provenance"}],
            expected_task=legacy["task"])


def covered(data, mapping):
    return validate_proof_mapping(data, mapping, expected_task=data["task"], expected_mapping=pin(mapping))


def comparison():
    return obligation("proof-comparison", kind="comparison", conditions=conditions(base_sha=BASE))


def bound_record(data, mapping, identity):
    parsed = covered(data, mapping)
    item = next(item for item in parsed.contract.obligations if item.id == identity)
    return {"contract_digest": parsed.contract.digest, "mapping_digest": parsed.mapping.digest,
            "obligation_id": item.id, "obligation_digest": item.digest}


def replay(data, mapping, identity="proof-comparison"):
    item = next(item for item in data["obligations"] if item["id"] == identity)
    return {
        **bound_record(data, mapping, identity), "schema": REPLAY_SCHEMA,
        "gates": {gate: {"established": True, "basis_ref": f"basis-{gate}-v1"} for gate in BR_GATES},
        "decision": "MANDATORY_COMPARISON", "alternative_status": "ABSENT",
        "scope": {"population_ref": item["conditions"]["population_ref"], "items": 8,
                  "method_ref": "bounded-comparison-v1"},
        "conditions": copy.deepcopy(item["conditions"]),
        "bounds": {"authority_ref": "approval-v1", "cost_ref": "approved-cost-v1",
                   "max_items": 8, "max_seconds": 30, "max_attempts": 1},
        "already_covered": False,
    }


def observation(data, mapping):
    return {**bound_record(data, mapping, "proof-unit"), "schema": OBSERVATION_SCHEMA,
            "subject_sha": CANDIDATE, "observation_ref": "synthetic-failure-v1", "outcome": "FAIL",
            "complete": True, "stable": True, "conflicting": False,
            "conditions": copy.deepcopy(data["obligations"][0]["conditions"]), "observed_items": 8}


def decide(data, mapping, record):
    return candidate_disposition(data, mapping, record, expected_task=data["task"], expected_mapping=pin(mapping))


def eligibility(data, mapping, record):
    return evaluate_base_replay(data, mapping, record, expected_task=data["task"], expected_mapping=pin(mapping))


def test_valid_contract_mapping_is_immutable_complete_declaration_without_pass():
    data = contract()
    original = copy.deepcopy(data)
    mapping = mapping_for(data)
    result = covered(data, mapping)
    assert data == original
    assert result.contract.schema == CONTRACT_SCHEMA
    assert result.contract.execution_default == EXECUTION_DEFAULT
    assert result.contract.obligations[0].acceptance_ids == ("AC1",)
    assert result.mapping.entries[0].proof_id == "mapped-proof-unit"
    with pytest.raises(FrozenInstanceError):
        result.contract.revision = 2
    data["obligations"][0]["conditions"]["fixture_refs"].clear()
    assert result.contract.obligations[0].conditions.fixture_refs == ("fixture-a-v1", "fixture-b-v1")


@pytest.mark.parametrize("defect", [
    "schema", "empty", "duplicate", "equivalent", "contradictory", "missing-ac", "extra-ac", "unknown-field",
    "missing-field", "unbounded", "claim-text", "claim-conflict", "subject-conflict", "base-conflict",
    "revision-bool", "revision-bound", "missing-review", "commands", "default", "dict-obligations",
])
def test_malformed_contracts_fail_closed(defect):
    data = contract()
    if defect == "schema":
        data["schema"] = "proof-coverage-v2"
    elif defect == "empty":
        data["obligations"] = []
    elif defect == "duplicate":
        data["obligations"].append(copy.deepcopy(data["obligations"][0]))
    elif defect == "equivalent":
        data["obligations"].append(obligation("duplicate-proof"))
    elif defect == "contradictory":
        data["obligations"].append(obligation("contradictory-proof", blocking=False))
    elif defect == "missing-ac":
        data["task"]["acceptance_ids"].append("AC2")
    elif defect == "extra-ac":
        data["obligations"][0]["acceptance_ids"].append("AC2")
    elif defect == "unknown-field":
        data["planner"] = {}
    elif defect == "missing-field":
        del data["execution_default"]
    elif defect == "unbounded":
        data["obligations"] *= MAX_OBLIGATIONS + 1
    elif defect == "claim-text":
        data["obligations"][0]["claim"]["text"] = "x" * (MAX_TEXT + 1)
    elif defect == "claim-conflict":
        data["obligations"].append(obligation("different-proof", conditions=conditions(integration_ref="integration-v1")))
        data["obligations"][1]["claim"]["text"] = "Different semantic meaning."
    elif defect == "subject-conflict":
        data["obligations"].append(obligation("different-subject", conditions=conditions(candidate_sha="d" * 40)))
    elif defect == "base-conflict":
        data["obligations"].extend([comparison(), obligation("other-base", kind="comparison", conditions=conditions(base_sha="d" * 40))])
    elif defect == "revision-bool":
        data["revision"] = True
    elif defect == "revision-bound":
        data["revision"] = MAX_REVISION + 1
    elif defect == "missing-review":
        del data["obligations"][0]["provenance"]["review_ref"]
    elif defect == "commands":
        data["obligations"][0]["conditions"]["command"] = "python -m pytest"
    elif defect == "default":
        data["execution_default"] = "automatic-base-fallback"
    else:
        data["obligations"] = {"proof-unit": data["obligations"][0]}
    with pytest.raises(ProofCoverageError):
        validate_proof_contract(data, expected_task=data["task"])


@pytest.mark.parametrize("field,value", [
    ("population_size", True), ("population_size", 0), ("population_size", MAX_POPULATION + 1),
    ("workers", True), ("workers", 0), ("workers", 257), ("workers", 2),
    ("worker_mode", "auto"), ("candidate_sha", "A" * 40), ("base_sha", CANDIDATE),
    ("toolchain_ref", "python -m pytest"), ("environment_ref", "env;command"),
    ("test_ref", "tests/test_fixture.py::test_case"), ("profile_ref", "*"),
    ("fixture_refs", ["f-v1"] * 2), ("fixture_refs", [f"f-{n}" for n in range(MAX_REFERENCES + 1)]),
    ("fixture_refs", None), ("ordering_ref", None),
])
def test_conditions_reject_unknown_unbounded_command_shaped_and_contradictory_values(field, value):
    data = conditions(**{field: value})
    with pytest.raises(ProofCoverageError):
        proof_conditions_equivalent(data, data)


@pytest.mark.parametrize("text", ["python -m pytest -q", "pytest tests/test_sample.py",
                                 "pwsh -Command Invoke-Thing", "git checkout base", "aios run TASK-1"])
def test_executable_invocations_cannot_masquerade_as_semantic_claim_text(text):
    data = contract()
    data["obligations"][0]["claim"]["text"] = text
    with pytest.raises(ProofCoverageError, match="executable invocation"):
        validate_proof_contract(data, expected_task=data["task"])


@pytest.mark.parametrize("field", ["task_id", "revision", "envelope_digest", "acceptance_ids"])
def test_contract_requires_exact_independent_task_pin(field):
    data = contract()
    expected = copy.deepcopy(data["task"])
    expected[field] = {"task_id": "TASK-OTHER", "revision": 2, "envelope_digest": "e" * 64,
                       "acceptance_ids": ["AC2"]}[field]
    with pytest.raises(ProofCoverageError, match="TASK binding"):
        validate_proof_contract(data, expected_task=expected)


def test_unordered_declarations_have_deterministic_digest_without_rewriting_execution_order():
    data = contract(obligation("integration-proof", conditions=conditions(integration_ref="integration-v1")))
    data["task"]["acceptance_ids"] = ["AC1", "AC2"]
    data["obligations"][0]["acceptance_ids"] = ["AC1", "AC2"]
    mapping = mapping_for(data)
    before = covered(data, mapping)
    data["task"]["acceptance_ids"].reverse()
    data["obligations"].reverse()
    for item in data["obligations"]:
        item["acceptance_ids"].reverse()
        item["conditions"]["fixture_refs"].reverse()
    mapping["proofs"].reverse()
    mapping["entries"].reverse()
    for item in mapping["proofs"]:
        item["conditions"]["fixture_refs"].reverse()
    after = covered(data, mapping)
    assert before == after
    assert before.contract.digest == after.contract.digest
    assert before.mapping.digest == after.mapping.digest
    changed_order = conditions(ordering_ref="reversed-execution-order-v1")
    assert not proof_conditions_equivalent(conditions(), changed_order)


@pytest.mark.parametrize("field,value", [
    ("candidate_sha", "d" * 40), ("base_sha", BASE),
    ("population_ref", "population-v2"), ("population_size", 9),
    ("test_ref", "test-code-v2"), ("fixture_refs", ["fixture-v2"]),
    ("shared_state_ref", "shared-state-v2"), ("profile_ref", "profile-v2"),
    ("worker_mode", "parallel"), ("concurrency_ref", "concurrency-v2"),
    ("ordering_ref", "ordering-v2"), ("integration_ref", "integration-v2"),
    ("repetition_ref", "repeatability-v2"), ("toolchain_ref", "toolchain-v2"),
    ("environment_ref", "environment-v2"), ("evidence_kind", "conformance-v2"),
    ("evidence_schema", "observation-v2"), ("collection_ref", "collection-v2"),
])
def test_same_population_is_insufficient_for_equivalence_under_any_distinguishing_condition(field, value):
    assert not proof_conditions_equivalent(conditions(), conditions(**{field: value}))


def test_worker_count_and_semantic_meaning_are_distinct_proofs():
    assert not proof_conditions_equivalent(conditions(worker_mode="parallel", workers=2),
                                          conditions(worker_mode="parallel", workers=3))
    left, right = obligation(), obligation("another-id", revision=2)
    assert obligations_equivalent(left, right)
    right["claim"] = {"id": "other-claim-v1", "text": "A different property is required."}
    assert not obligations_equivalent(left, right)
    right = obligation("another-id", blocking=False)
    assert not obligations_equivalent(left, right)


@pytest.mark.parametrize("defect", [
    "missing", "unknown-obligation", "ambiguous", "duplicate-proof", "unknown-proof", "unused-proof",
    "contract-id", "contract-revision", "contract-digest", "obligation-revision", "obligation-digest",
    "conditions", "claim", "claim-conflict", "extra-claim", "schema", "command",
])
def test_mapping_rejects_incomplete_ambiguous_stale_and_conflicting_relationships(defect):
    data = contract()
    mapping = mapping_for(data)
    if defect == "missing":
        data = contract(obligation("integration-proof", conditions=conditions(integration_ref="integration-v1")))
        mapping = mapping_for(data)
        mapping["entries"].pop()
    elif defect == "unknown-obligation":
        mapping["entries"][0]["obligation_id"] = "absent-proof"
    elif defect == "ambiguous":
        mapping["entries"].append(copy.deepcopy(mapping["entries"][0]))
    elif defect == "duplicate-proof":
        mapping["proofs"].append(copy.deepcopy(mapping["proofs"][0]))
    elif defect == "unknown-proof":
        mapping["entries"][0]["proof_id"] = "absent-mapped-proof"
    elif defect == "unused-proof":
        extra = copy.deepcopy(mapping["proofs"][0])
        extra["id"] = "unused-proof"
        extra["conditions"]["integration_ref"] = "other-integration-v1"
        mapping["proofs"].append(extra)
    elif defect in {"contract-id", "contract-revision", "contract-digest"}:
        key = defect.replace("-", "_")
        mapping[key] = {"contract_id": "other-contract", "contract_revision": 2, "contract_digest": "e" * 64}[key]
    elif defect in {"obligation-revision", "obligation-digest"}:
        key = defect.replace("-", "_")
        mapping["entries"][0][key] = 2 if key.endswith("revision") else "e" * 64
    elif defect == "conditions":
        mapping["proofs"][0]["conditions"]["fixture_refs"] = []
    elif defect == "claim":
        mapping["proofs"][0]["claims"] = [{"id": "wrong-claim-v1", "text": "Other property."}]
    elif defect == "claim-conflict":
        mapping["proofs"][0]["claims"][0]["text"] = "Conflicting meaning."
    elif defect == "extra-claim":
        mapping["proofs"][0]["claims"].append({"id": "extra-v1", "text": "Unsupported property."})
    elif defect == "schema":
        mapping["schema"] = "future-map-v2"
    else:
        mapping["proofs"][0]["command"] = "python -m pytest"
    with pytest.raises(ProofCoverageError):
        covered(data, mapping)


@pytest.mark.parametrize("field", ["id", "revision", "digest"])
def test_mapping_requires_independently_pinned_reviewed_version(field):
    data = contract()
    mapping = mapping_for(data)
    expected = pin(mapping)
    expected[field] = {"id": "other-map-v1", "revision": 2, "digest": "e" * 64}[field]
    with pytest.raises(ProofCoverageError, match="reviewed mapping pin"):
        validate_proof_mapping(data, mapping, expected_task=data["task"], expected_mapping=expected)


def test_changed_provenance_invalidates_pinned_contract_mapping_and_obligation():
    data = contract()
    mapping = mapping_for(data)
    expected = pin(mapping)
    mapping["provenance"]["review_ref"] = "review-v2"
    with pytest.raises(ProofCoverageError, match="reviewed mapping pin"):
        validate_proof_mapping(data, mapping, expected_task=data["task"], expected_mapping=expected)
    mapping = mapping_for(data)
    data["obligations"][0]["provenance"]["source_ref"] = "source-v2"
    with pytest.raises(ProofCoverageError, match="contract binding"):
        covered(data, mapping)


def test_explicit_shared_proof_covers_two_semantic_claims_without_erasing_entries():
    extra = obligation("second-claim", claim={"id": "semantic-order-v1", "text": "Required order is preserved."}, acceptance_ids=["AC2"])
    data = contract(extra)
    data["task"]["acceptance_ids"].append("AC2")
    mapping = mapping_for(data, groups={"proof-unit": "shared-proof", "second-claim": "shared-proof"})
    result = covered(data, mapping)
    assert len(result.mapping.proofs) == 1
    assert len(result.mapping.entries) == 2
    assert len(result.mapping.proofs[0].claims) == 2
    separate = mapping_for(data)
    with pytest.raises(ProofCoverageError, match="equivalent proof descriptors"):
        covered(data, separate)


def test_distinct_integration_proof_cannot_be_merged_with_same_test_population():
    integration = obligation("integration-proof", conditions=conditions(
        integration_ref="whole-suite-v1", shared_state_ref="shared-fixture-v1",
        worker_mode="parallel", workers=12, ordering_ref="parallel-order-v1"))
    data = contract(integration)
    mapping = mapping_for(data)
    result = covered(data, mapping)
    assert len(result.contract.obligations) == len(result.mapping.proofs) == 2
    assert not obligations_equivalent(data["obligations"][0], integration)
    merged = mapping_for(data, groups={"proof-unit": "shared-proof", "integration-proof": "shared-proof"})
    with pytest.raises(ProofCoverageError, match="conflicting proof conditions"):
        covered(data, merged)


@pytest.mark.parametrize("gate", BR_GATES)
@pytest.mark.parametrize("state", ["missing", False, None])
def test_each_br_gate_must_be_affirmatively_established(gate, state):
    data = contract(comparison())
    mapping = mapping_for(data)
    record = replay(data, mapping)
    if state == "missing":
        del record["gates"][gate]
    else:
        record["gates"][gate] = {"established": state, "basis_ref": None}
    result = eligibility(data, mapping, record)
    assert not result.eligible and result.failed_gates == (gate,)
    assert not result.execution_authorized


def test_all_br_gates_allow_only_design_eligibility_with_exact_minimum_scope():
    data = contract(comparison())
    mapping = mapping_for(data)
    record = replay(data, mapping)
    result = eligibility(data, mapping, record)
    assert result.eligible and result.failed_gates == ()
    assert not result.execution_authorized
    record["alternative_status"] = "INVALID"
    record["decision"] = "UNDECIDED_REQUIRED"
    assert eligibility(data, mapping, record).eligible


@pytest.mark.parametrize("field,value,gate", [
    ("decision", "DIAGNOSTIC_ONLY", "BR-2"),
    ("alternative_status", "VALID", "BR-3"), ("alternative_status", "UNKNOWN", "BR-3"),
    ("already_covered", True, "BR-6"),
])
def test_affirmative_br_labels_cannot_override_contradictory_prerequisites(field, value, gate):
    data = contract(comparison())
    mapping = mapping_for(data)
    record = replay(data, mapping)
    record[field] = value
    assert eligibility(data, mapping, record).failed_gates == (gate,)


@pytest.mark.parametrize("defect,gate", [
    ("wider-scope", "BR-4"), ("population", "BR-4"), ("conditions", "BR-5"),
    ("authority", "BR-6"), ("cost", "BR-6"),
])
def test_base_scope_comparability_and_authority_cost_are_mechanically_bound(defect, gate):
    data = contract(comparison())
    mapping = mapping_for(data)
    record = replay(data, mapping)
    if defect == "wider-scope":
        record["scope"]["items"] = 9
        record["bounds"]["max_items"] = 9
    elif defect == "population":
        record["scope"]["population_ref"] = "broad-population-v1"
    elif defect == "conditions":
        record["conditions"]["toolchain_ref"] = "toolchain-v2"
    elif defect == "authority":
        record["bounds"]["authority_ref"] = "other-approval-v1"
    else:
        record["bounds"]["max_items"] = 7
    assert eligibility(data, mapping, record).failed_gates == (gate,)


@pytest.mark.parametrize("defect", ["missing-basis", "extra-gate", "bool-fact", "command-method",
                                    "attempts", "seconds", "schema", "stale-contract", "stale-map", "stale-obligation"])
def test_replay_record_rejects_malformed_unbounded_or_stale_input(defect):
    data = contract(comparison())
    mapping = mapping_for(data)
    record = replay(data, mapping)
    if defect == "missing-basis":
        record["gates"]["BR-1"]["basis_ref"] = None
    elif defect == "extra-gate":
        record["gates"]["BR-7"] = {"established": True, "basis_ref": "basis-v1"}
    elif defect == "bool-fact":
        record["gates"]["BR-1"]["established"] = 1
    elif defect == "command-method":
        record["scope"]["method_ref"] = "python -m pytest"
    elif defect == "attempts":
        record["bounds"]["max_attempts"] = 2
    elif defect == "seconds":
        record["bounds"]["max_seconds"] = 3601
    elif defect == "schema":
        record["schema"] = "unapproved-v2"
    else:
        field = {"stale-contract": "contract_digest", "stale-map": "mapping_digest", "stale-obligation": "obligation_digest"}[defect]
        record[field] = "e" * 64
    with pytest.raises(ProofCoverageError):
        eligibility(data, mapping, record)


def test_candidate_conformance_is_not_baseline_necessity_even_with_all_six_labels():
    data = contract()
    mapping = mapping_for(data)
    record = replay(data, mapping, "proof-unit")
    assert eligibility(data, mapping, record).failed_gates == ("BR-1",)


def test_complete_decisive_candidate_failure_blocks_with_unknown_attribution_and_zero_fallback():
    data = contract()
    mapping = mapping_for(data)
    result = decide(data, mapping, observation(data, mapping))
    assert result.status == "BLOCK" and result.attribution == "UNKNOWN"
    assert result.outstanding_comparison_ids == ()
    assert not result.reproduction_requested and not result.base_replay_requested


def test_independent_comparison_is_never_discharged_by_candidate_failure():
    data = contract(comparison())
    mapping = mapping_for(data)
    result = decide(data, mapping, observation(data, mapping))
    assert result.status == "UNRESOLVED"
    assert result.outstanding_comparison_ids == ("proof-comparison",)
    assert not result.reproduction_requested and not result.base_replay_requested


@pytest.mark.parametrize("changes", [
    {"outcome": "PASS"}, {"outcome": "UNKNOWN"}, {"complete": False, "observed_items": 3},
    {"outcome": "UNKNOWN", "complete": False, "observed_items": 0},
    {"stable": False}, {"conflicting": True},
])
def test_pass_incomplete_unstable_and_conflicting_observations_never_manufacture_pass_or_replay(changes):
    data = contract()
    mapping = mapping_for(data)
    record = observation(data, mapping)
    record.update(changes)
    result = decide(data, mapping, record)
    assert result.status == "UNRESOLVED" and result.attribution == "UNKNOWN"
    assert not result.reproduction_requested and not result.base_replay_requested


def test_nonblocking_failure_does_not_authorize_short_circuit():
    data = contract()
    data["obligations"][0]["blocking"] = False
    mapping = mapping_for(data)
    assert decide(data, mapping, observation(data, mapping)).status == "UNRESOLVED"


@pytest.mark.parametrize("defect", ["subject", "conditions", "complete-population", "over-population",
                                    "stale", "bool-count", "missing", "schema"])
def test_candidate_failure_requires_complete_exact_bindings(defect):
    data = contract()
    mapping = mapping_for(data)
    record = observation(data, mapping)
    if defect == "subject":
        record["subject_sha"] = BASE
    elif defect == "conditions":
        record["conditions"]["ordering_ref"] = "other-order-v1"
    elif defect == "complete-population":
        record["observed_items"] = 7
    elif defect == "over-population":
        record["observed_items"] = 9
    elif defect == "stale":
        record["obligation_digest"] = "e" * 64
    elif defect == "bool-count":
        record["observed_items"] = True
    elif defect == "missing":
        del record["complete"]
    else:
        record["schema"] = "unknown-v2"
    with pytest.raises(ProofCoverageError):
        decide(data, mapping, record)


def test_every_public_validation_and_decision_surface_performs_zero_execution_or_io(monkeypatch):
    data = contract()
    mapping = mapping_for(data)
    failed = observation(data, mapping)
    comparative = contract(comparison())
    comparative_mapping = mapping_for(comparative)
    base = replay(comparative, comparative_mapping)
    calls = []

    def prohibited(*args, **kwargs):
        calls.append("prohibited effect")
        raise AssertionError("VP-02 must not execute, discover, fetch, or mutate evidence")

    # Traps use synthetic data; no child, checkout, file, network or credentials.
    with monkeypatch.context() as traps:
        for owner, names in (
            (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
            (os, ("system", "popen", "listdir", "scandir", "getenv")),
            (Path, ("open", "read_text", "write_text", "read_bytes", "write_bytes", "iterdir", "glob", "rglob")),
            (builtins, ("open",)), (socket, ("socket", "create_connection")), (pytest, ("main",)),
        ):
            for name in names:
                traps.setattr(owner, name, prohibited)
        first = covered(data, mapping)
        second = covered(data, mapping)
        fail = decide(data, mapping, failed)
        allowed = eligibility(comparative, comparative_mapping, base)
        equal = proof_conditions_equivalent(conditions(), conditions())
        equivalent = obligations_equivalent(obligation(), obligation("other-id"))
    assert calls == [] and first == second
    assert fail.status == "BLOCK" and allowed.eligible and not allowed.execution_authorized
    assert equal and equivalent


# Future intake fixtures are wholly synthetic, including all Git/review pins.
# Recomputing pins here prepares test data; production callers need independently
# authenticated/current reviewed pins, which these fixtures cannot establish.
INTAKE_VALIDATORS = (validate_semantic_proof_declaration, validate_semantic_proof_intake)


def synthetic_intake():
    data = contract(obligation("integration-proof", acceptance_ids=["AC2"], conditions=conditions(
        integration_ref="whole-suite-v1", shared_state_ref="shared-fixture-v1",
        worker_mode="parallel", workers=12, ordering_ref="parallel-order-v1")), comparison())
    data["task"]["acceptance_ids"] = ["AC1", "AC2"]
    for index, item in enumerate(data["obligations"]):
        item["provenance"]["source_ref"] = f"synthetic-obligation-{index}"
    mapping = mapping_for(data)
    for index, proof in enumerate(mapping["proofs"]):
        proof["provenance"]["review_ref"] = f"synthetic-proof-review-{index}"
    declaration = {
        "schema": SEMANTIC_INTAKE_SCHEMA, "task": copy.deepcopy(data["task"]),
        "candidate_sha": CANDIDATE,
        "sources": {name: {"source_sha": sha * 40, "blob_sha": blob * 40}
                    for name, sha, blob in (("task", "1", "2"), ("contract", "3", "4"), ("mapping", "5", "6"))},
        "contract": data, "mapping": mapping,
    }
    return declaration, synthetic_intake_pins(declaration)


def synthetic_intake_pins(declaration):
    parsed = validate_proof_contract(declaration["contract"], expected_task=declaration["task"])
    return {
        "expected_task": copy.deepcopy(declaration["task"]),
        "expected_candidate_sha": declaration["candidate_sha"],
        "expected_sources": copy.deepcopy(declaration["sources"]),
        "expected_contract": {"id": parsed.id, "revision": parsed.revision, "digest": parsed.digest},
        "expected_mapping": pin(declaration["mapping"]),
    }


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
def test_synthetic_future_intake_is_only_immutable_structural_readiness(validate):
    data, pins = synthetic_intake()
    original = copy.deepcopy((data, pins))
    result = validate(data, **pins)
    assert isinstance(result, SemanticProofIntake)
    assert result.schema == SEMANTIC_INTAKE_SCHEMA
    assert result.readiness == "STRUCTURALLY_COMPLETE_ONLY"
    assert result.canonical_verification == "NOT_PERFORMED"
    assert not result.production_eligible and not result.execution_authorized
    assert asdict(result)["readiness"] == "STRUCTURALLY_COMPLETE_ONLY"
    assert asdict(result)["production_eligible"] is False
    assert result.unestablished == ("source_authentication", "source_currentness", "author_review_authority",
                                    "semantic_adequacy", "evidence_validity", "actual_applicability")
    assert result.coverage.contract.task.acceptance_ids == ("AC1", "AC2")
    assert len(result.coverage.contract.obligations) == len(result.coverage.mapping.entries) == 3
    assert result.contract_pin.digest == result.coverage.contract.digest
    assert result.mapping_pin.digest == result.coverage.mapping.digest
    assert result.sources.contract.blob_sha == "4" * 40
    assert (data, pins) == original
    with pytest.raises(FrozenInstanceError):
        result.production_eligible = True
    with pytest.raises(FrozenInstanceError):
        result.contract_pin.digest = "f" * 64
    with pytest.raises(FrozenInstanceError):
        result.sources.task.source_sha = "f" * 40
    for item in result.coverage.contract.obligations:
        raw = next(raw for raw in data["contract"]["obligations"] if raw["id"] == item.id)
        assert item.provenance.source_ref == raw["provenance"]["source_ref"]
    for proof in result.coverage.mapping.proofs:
        raw = next(raw for raw in data["mapping"]["proofs"] if raw["id"] == proof.id)
        assert proof.provenance.review_ref == raw["provenance"]["review_ref"]
    data["sources"]["task"]["blob_sha"] = "f" * 40
    data["contract"]["obligations"][0]["conditions"]["fixture_refs"].clear()
    pins["expected_contract"]["digest"] = "f" * 64
    assert result.sources.task.blob_sha == "2" * 40
    assert result.contract_pin.digest != "f" * 64
    assert all(item.conditions.fixture_refs for item in result.coverage.contract.obligations)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
def test_future_intake_equivalent_declaration_order_is_stable(validate):
    data, pins = synthetic_intake()
    before = validate(data, **pins)
    data["task"]["acceptance_ids"].reverse()
    data["contract"]["task"]["acceptance_ids"].reverse()
    data["contract"]["obligations"].reverse()
    for item in data["contract"]["obligations"]:
        item["conditions"]["fixture_refs"].reverse()
        item["acceptance_ids"].reverse()
    for proof in data["mapping"]["proofs"]:
        proof["conditions"]["fixture_refs"].reverse()
        proof["claims"].reverse()
    data["mapping"]["proofs"].reverse()
    data["mapping"]["entries"].reverse()
    data["sources"] = dict(reversed(list(data["sources"].items())))
    assert validate(data, **pins) == before


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
def test_future_intake_preserves_explicit_equal_condition_sharing_and_all_claims(validate):
    data, _ = synthetic_intake()
    data["contract"]["obligations"].append(obligation("proof-second", acceptance_ids=["AC2"], claim={
        "id": "semantic-additional-v1", "text": "The admitted operation preserves the additional property."}))
    groups = {item["id"]: f"mapped-{item['id']}" for item in data["contract"]["obligations"]}
    groups.update({"proof-unit": "shared-proof", "proof-second": "shared-proof"})
    data["mapping"] = mapping_for(data["contract"], groups=groups)
    pins = synthetic_intake_pins(data)
    before = validate(data, **pins)
    assert len(before.coverage.contract.obligations) == len(before.coverage.mapping.entries) == 4
    assert len(before.coverage.mapping.proofs) == 3
    shared = next(proof for proof in data["mapping"]["proofs"] if proof["id"] == "shared-proof")
    assert len(shared["claims"]) == 2
    shared["claims"].reverse()
    assert validate(data, **pins) == before
    # Separate equivalent descriptors cannot replace that explicit sharing.
    data["mapping"] = mapping_for(data["contract"])
    pins["expected_mapping"] = pin(data["mapping"])
    with pytest.raises(ProofCoverageError, match="equivalent proof descriptors"):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("location", [(), ("contract",), ("mapping",)])
@pytest.mark.parametrize("version", [None, True, "", "minimum-sufficient-v1", "task-semantic-proof-intake-v2",
                                     "proof-coverage-v2", "proof-mapping-v2"])
def test_future_intake_versions_are_explicit_and_closed(validate, location, version):
    data, pins = synthetic_intake()
    target = data if not location else data[location[0]]
    target["schema"] = version
    with pytest.raises(ProofCoverageError):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("argument", ["expected_task", "expected_candidate_sha", "expected_sources",
                                      "expected_contract", "expected_mapping"])
def test_future_intake_requires_every_independent_caller_input(validate, argument):
    data, pins = synthetic_intake()
    del pins[argument]
    with pytest.raises(TypeError):
        validate(data, **pins)
    pins[argument] = None
    with pytest.raises(ProofCoverageError):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("field", ["schema", "task", "candidate_sha", "sources", "contract", "mapping"])
def test_future_intake_missing_root_fields_fail_closed(validate, field):
    data, pins = synthetic_intake()
    del data[field]
    with pytest.raises(ProofCoverageError, match="exact fields"):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("field,value", [("authenticated", True), ("reviewed", True), ("policy", "future"),
                                       ("verification", {"required": ["proof-unit"]}), ("run_id", "RUN-SYNTHETIC")])
def test_future_intake_cannot_self_certify_trust_or_request_execution(validate, field, value):
    data, pins = synthetic_intake()
    data[field] = value
    with pytest.raises(ProofCoverageError, match="exact fields"):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("field,value", [("task_id", "TASK-OTHER"), ("revision", 2),
                                       ("envelope_digest", "e" * 64), ("acceptance_ids", ["AC1"]),
                                       ("acceptance_ids", ["AC1", "AC2", "AC3"]),
                                       ("acceptance_ids", ["AC1", "AC1"]), ("revision", True)])
def test_future_intake_requires_exact_complete_task_identity(validate, field, value):
    data, pins = synthetic_intake()
    data["task"][field] = value
    with pytest.raises(ProofCoverageError):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("source", ["task", "contract", "mapping"])
@pytest.mark.parametrize("defect", ["missing", "extra", "sha", "blob", "malformed", "swapped"])
def test_future_intake_source_pins_reject_partial_stale_and_cross_source_inputs(validate, source, defect):
    data, pins = synthetic_intake()
    if defect == "missing":
        del data["sources"][source]
    elif defect == "extra":
        data["sources"][source]["authenticated"] = True
    elif defect == "sha":
        data["sources"][source]["source_sha"] = "f" * 40
    elif defect == "blob":
        data["sources"][source]["blob_sha"] = "f" * 40
    elif defect == "malformed":
        data["sources"][source]["source_sha"] = "main"
    else:
        other = "mapping" if source != "mapping" else "contract"
        data["sources"][source], data["sources"][other] = data["sources"][other], data["sources"][source]
    with pytest.raises(ProofCoverageError):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("which", ["expected_contract", "expected_mapping"])
@pytest.mark.parametrize("defect", ["id", "revision", "digest", "missing", "extra", "swapped"])
def test_future_intake_independent_contract_and_mapping_pins_cannot_be_substituted(validate, which, defect):
    data, pins = synthetic_intake()
    if defect == "missing":
        del pins[which]["digest"]
    elif defect == "extra":
        pins[which]["reviewed"] = True
    elif defect == "swapped":
        pins[which] = pins["expected_mapping" if which == "expected_contract" else "expected_contract"]
    else:
        pins[which][defect] = {"id": "other-v1", "revision": 2, "digest": "e" * 64}[defect]
    with pytest.raises(ProofCoverageError):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("defect", ["outer-candidate", "obligation-candidate", "other-task", "contract-provenance",
                                    "missing-acceptance", "duplicate-obligation", "missing-entry", "duplicate-entry",
                                    "extra-entry", "stale-entry", "unused-proof", "mapping-provenance", "merged-conditions"])
def test_future_intake_hostile_stale_or_ambiguous_declarations_fail_closed(validate, defect):
    data, pins = synthetic_intake()
    contract_data, mapping = data["contract"], data["mapping"]
    if defect == "outer-candidate":
        data["candidate_sha"] = "d" * 40
    elif defect == "obligation-candidate":
        for item in contract_data["obligations"]:
            item["conditions"]["candidate_sha"] = "d" * 40
        data["mapping"] = mapping_for(contract_data)
        pins = synthetic_intake_pins(data)
    elif defect == "other-task":
        contract_data["task"]["task_id"] = "TASK-OTHER"
    elif defect == "contract-provenance":
        contract_data["provenance"]["review_ref"] = "other-review-v1"
        data["mapping"] = mapping_for(contract_data)
        pins["expected_mapping"] = pin(data["mapping"])
    elif defect == "missing-acceptance":
        contract_data["obligations"][1]["acceptance_ids"] = ["AC1"]
    elif defect == "duplicate-obligation":
        contract_data["obligations"].append(copy.deepcopy(contract_data["obligations"][0]))
    elif defect == "missing-entry":
        mapping["entries"].pop()
    elif defect == "duplicate-entry":
        mapping["entries"].append(copy.deepcopy(mapping["entries"][0]))
    elif defect == "extra-entry":
        mapping["entries"].append({**mapping["entries"][0], "obligation_id": "unknown-v1"})
    elif defect == "stale-entry":
        mapping["entries"][0]["obligation_digest"] = "e" * 64
    elif defect == "unused-proof":
        mapping["proofs"].append({**mapping["proofs"][0], "id": "unused-v1"})
    elif defect == "mapping-provenance":
        mapping["provenance"]["review_ref"] = "other-review-v1"
    else:
        mapping["entries"][1]["proof_id"] = mapping["entries"][0]["proof_id"]
        mapping["proofs"].pop(1)
    if defect in {"missing-entry", "extra-entry", "stale-entry", "unused-proof", "merged-conditions"}:
        pins["expected_mapping"] = pin(mapping)
    with pytest.raises(ProofCoverageError):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("field,value", [
    ("candidate_sha", "d" * 40), ("base_sha", "d" * 40),
    ("population_ref", "other-population-v1"), ("population_size", 9),
    ("test_ref", "other-test-v1"), ("fixture_refs", ["other-fixture-v1"]),
    ("shared_state_ref", "other-shared-state-v1"), ("profile_ref", "other-profile-v1"),
    ("worker_mode", "parallel"), ("workers", 2), ("concurrency_ref", "other-concurrency-v1"),
    ("ordering_ref", "other-order-v1"), ("integration_ref", "other-integration-v1"),
    ("repetition_ref", "other-repetition-v1"), ("toolchain_ref", "other-toolchain-v1"),
    ("environment_ref", "other-environment-v1"), ("evidence_kind", "other-kind-v1"),
    ("evidence_schema", "other-schema-v1"), ("collection_ref", "other-collection-v1"),
])
def test_future_intake_never_merges_different_full_proof_conditions(validate, field, value):
    data, pins = synthetic_intake()
    proof = next(proof for proof in data["mapping"]["proofs"] if proof["id"] == "mapped-proof-unit")
    proof["conditions"][field] = value
    if field == "workers":
        proof["conditions"]["worker_mode"] = "parallel"
    elif field == "base_sha":
        proof["kind"] = "comparison"
    # Supply an independently changed synthetic mapping pin so the condition
    # comparison, rather than merely a stale mapping digest, must reject it.
    pins["expected_mapping"] = pin(data["mapping"])
    with pytest.raises(ProofCoverageError, match="conflicting proof conditions"):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("field,value", [("fixture_refs", ["replacement-fixture-v1"]), ("workers", 2),
                                       ("integration_ref", "replacement-integration-v1"),
                                       ("ordering_ref", "replacement-order-v1")])
def test_coherent_semantic_replacement_still_requires_independent_contract_pin(validate, field, value):
    data, pins = synthetic_intake()
    item = data["contract"]["obligations"][0]
    item["conditions"][field] = value
    if field == "workers":
        item["conditions"]["worker_mode"] = "parallel"
    data["mapping"] = mapping_for(data["contract"])
    pins["expected_mapping"] = pin(data["mapping"])
    with pytest.raises(ProofCoverageError, match="contract pin"):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("text", ["curl https://invalid.example", "node runner.js", "echo claimed-pass",
                                 "uv run pytest", "./runner", "C:\\runner.exe", "& runner",
                                 "The claim $(runner) holds.", "The claim `runner` holds."])
def test_intake_command_guard_adds_no_new_language_to_historical_vp02(validate, text):
    data, _ = synthetic_intake()
    for item in data["contract"]["obligations"]:
        item["claim"]["text"] = text
    data["mapping"] = mapping_for(data["contract"])
    pins = synthetic_intake_pins(data)
    # The unchanged VP-02 v1 validator may accept opaque text; the explicit
    # future intake imposes its narrower command-shaped-substitution guard.
    validate_proof_mapping(data["contract"], data["mapping"], expected_task=pins["expected_task"],
                           expected_mapping=pins["expected_mapping"])
    with pytest.raises(ProofCoverageError, match="executable invocation"):
        validate(data, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("bad", [None, [], "schema: task-semantic-proof-intake-v1", b"{}", lambda: {}])
def test_future_intake_rejects_loaders_and_non_plain_roots(validate, bad):
    _, pins = synthetic_intake()
    with pytest.raises(ProofCoverageError):
        validate(bad, **pins)


@pytest.mark.parametrize("validate", INTAKE_VALIDATORS)
@pytest.mark.parametrize("defect", ["acceptance-bound", "obligation-bound", "proof-bound", "entry-bound",
                                    "text-bound", "fixture-bound", "duplicate-fixture", "cycle", "dict-subclass"])
def test_future_intake_rejects_unbounded_duplicate_or_non_plain_data(validate, defect):
    data, pins = synthetic_intake()
    if defect == "acceptance-bound":
        data["task"]["acceptance_ids"] = [f"AC{index}" for index in range(MAX_OBLIGATIONS + 1)]
    elif defect == "obligation-bound":
        data["contract"]["obligations"] *= MAX_OBLIGATIONS + 1
    elif defect == "proof-bound":
        data["mapping"]["proofs"] *= MAX_OBLIGATIONS + 1
    elif defect == "entry-bound":
        data["mapping"]["entries"] *= MAX_OBLIGATIONS + 1
    elif defect == "text-bound":
        data["contract"]["obligations"][0]["claim"]["text"] = "x" * (MAX_TEXT + 1)
    elif defect == "fixture-bound":
        data["contract"]["obligations"][0]["conditions"]["fixture_refs"] *= MAX_REFERENCES + 1
    elif defect == "duplicate-fixture":
        data["contract"]["obligations"][0]["conditions"]["fixture_refs"] *= 2
    elif defect == "cycle":
        data["contract"]["obligations"] = [data["contract"]]
    else:
        class HostileDictionary(dict):
            def __iter__(self):
                raise AssertionError("input callbacks must not be invoked")
        data = HostileDictionary(data)
    with pytest.raises(ProofCoverageError):
        validate(data, **pins)


def test_future_intake_boundaries_perform_no_filesystem_git_network_clock_or_proof_execution(monkeypatch):
    import aios_renew.proof_coverage_contract as proof_module
    import aios_renew.verification as verification

    data, pins = synthetic_intake()
    calls = []

    def prohibited(*args, **kwargs):
        calls.append("prohibited effect")
        raise AssertionError("semantic intake is in-memory declaration validation only")

    with monkeypatch.context() as traps:
        for owner, names in (
            (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
            (os, ("system", "popen", "listdir", "scandir", "getenv", "stat", "mkdir", "remove", "rename")),
            (Path, ("open", "read_text", "write_text", "read_bytes", "write_bytes", "iterdir", "glob", "rglob", "stat")),
            (builtins, ("open",)), (socket, ("socket", "create_connection")),
            (time, ("time", "monotonic", "perf_counter", "sleep")), (pytest, ("main",)),
            (verification, ("execute_verification", "execute_minimum_verification", "materialize_verification_subject", "_git")),
            (proof_module, ("evaluate_base_replay", "candidate_disposition")),
        ):
            for name in names:
                traps.setattr(owner, name, prohibited)
        first = validate_semantic_proof_intake(data, **pins)
        second = validate_semantic_proof_declaration(data, **pins)
        invalid = dict(data, authenticated=True)
        try:
            validate_semantic_proof_intake(invalid, **pins)
        except ProofCoverageError:
            rejected = True
        else:
            rejected = False
    assert calls == [] and first == second and rejected
    assert not first.execution_authorized and not first.production_eligible
