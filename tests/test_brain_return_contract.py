"""Focused conformance for the caller-supplied Brain return-contract projection."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from aios_renew.brain_return_contract import (
    BrainReturnContractError,
    normalize_return_contract_registry,
    parse_return_contract_registry,
    return_contract_ref,
    select_return_contract,
)
from aios_renew.decision_packet import DecisionPacket
from aios_renew.publication import _validate_repair_authorization
from aios_renew.review import parse_remediation, parse_review, validate_remediation
from aios_renew.task import validate_task


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / ".ai" / "brain-return-contracts.yaml"
FLOW_CARDS = ROOT / ".ai" / "flow-cards.yaml"


def registry():
    return parse_return_contract_registry(REGISTRY.read_bytes())


def fingerprint(body):
    material = {key: value for key, value in body.items() if key != "packet_fingerprint"}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def packet(contract):
    body = {
        "format": "AIOS_DECISION_PACKET", "version": 1,
        "kind": "DECISION_PACKET",
        "work_context_fingerprint": "a" * 64,
        **{key: contract[key] for key in (
            "selected_flow", "authority_owner", "decision_family_ref",
            "handoff_target", "expected_return_shape",
        )},
        "selection_basis": "EXPLICIT_SELECTOR",
        "pending_canonical_obligation": None,
        "pending_canonical_authority_owner": None,
        "requires_fresh_context_for_continuation": True,
        "canonical_facts": {}, "canonical_blocker": None,
        "bounded_observations": [], "executor_claims": None,
        "prior_semantic_decisions": None, "human_input": None,
        "subject": {"kind": contract["selected_flow"]},
        "run_created": False, "executor_invoked": False,
        "verification_invoked": False, "state_mutated": False,
    }
    body["packet_fingerprint"] = fingerprint(body)
    return body


def test_registry_matches_current_flow_cards_and_excludes_reviewer():
    cards = {card["id"]: card for card in yaml.safe_load(FLOW_CARDS.read_text(encoding="utf-8"))["cards"]}
    contracts = registry()["contracts"]
    assert [c["selected_flow"] for c in contracts] == [
        "ARCHITECTURE", "TASK_AUTHORING", "REMEDIATION_AUTHORING",
        "REPAIR_AUTHORING", "DIAGNOSTIC",
    ]
    for contract in contracts:
        card = cards[contract["selected_flow"]]
        assert contract["version"] == 1
        for field in ("authority_owner", "decision_family_ref", "handoff_target", "expected_return_shape"):
            assert contract[field] == card[field]
        assert contract["candidate_contract"]["representation"] == "STRICT_JSON_MAPPING"
        assert select_return_contract(registry(), packet(contract))["contract"] == contract
    reviewer = dict(packet(contracts[0]), selected_flow="SEMANTIC_REVIEW")
    with pytest.raises(BrainReturnContractError):
        select_return_contract(registry(), reviewer)


def test_packet_metadata_substitution_fails_closed():
    data = registry()
    contract = data["contracts"][1]
    for field in ("authority_owner", "decision_family_ref", "handoff_target", "expected_return_shape"):
        supplied = packet(contract)
        supplied[field] = "SUBSTITUTED"
        supplied["packet_fingerprint"] = fingerprint(supplied)
        with pytest.raises(BrainReturnContractError):
            select_return_contract(data, supplied)
    with pytest.raises(BrainReturnContractError):
        select_return_contract(data, {"selected_flow": "TASK_AUTHORING"})


def test_exact_packet_mapping_and_object_select_same_contract_and_ref():
    data = registry()
    contract = data["contracts"][1]
    supplied = packet(contract)
    selected = select_return_contract(data, supplied)
    assert selected["contract"] == contract
    assert select_return_contract(data, DecisionPacket(supplied)) == selected


def test_partial_extra_and_stale_fingerprint_packets_fail_closed():
    data = registry()
    supplied = packet(data["contracts"][1])
    partial = {key: supplied[key] for key in (
        "format", "version", "kind", "selected_flow", "authority_owner",
        "decision_family_ref", "handoff_target", "expected_return_shape",
    )}
    partial["packet_fingerprint"] = fingerprint(partial)
    extra = dict(supplied, extra="forbidden")
    extra["packet_fingerprint"] = fingerprint(extra)
    stale = dict(supplied, human_input="changed")
    mismatch = dict(supplied, packet_fingerprint="0" * 64)
    for invalid in (partial, extra, stale, mismatch):
        with pytest.raises(BrainReturnContractError):
            select_return_contract(data, invalid)


def test_content_identity_is_semantic_and_representation_independent():
    source = REGISTRY.read_text(encoding="utf-8")
    data = registry()
    contract = data["contracts"][2]
    original = select_return_contract(data, packet(contract))["return_contract_ref"]
    reordered = yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
    assert select_return_contract(parse_return_contract_registry(reordered.replace("\n", "\r\n")), packet(contract))["return_contract_ref"] == original
    assert parse_return_contract_registry(source.encode("utf-8")) == data
    for mutate in (
        lambda c, b: c.update(handoff_target="HUMAN_BRAIN_PLANNING"),
        lambda c, b: c["candidate_contract"]["bindings"][0].update(instruction="Changed binding"),
        lambda c, b: c["candidate_contract"].update(shape="Changed shape"),
        lambda c, b: c["candidate_contract"]["requirements"].append("Changed requirement"),
        lambda c, b: b.update(shape_bytes=b["shape_bytes"] - 1),
    ):
        changed_contract, changed_bounds = deepcopy(contract), deepcopy(data["bounds"])
        mutate(changed_contract, changed_bounds)
        assert return_contract_ref(changed_contract, changed_bounds)["digest"] != original["digest"]


@pytest.mark.parametrize("change", [
    lambda d: d.update(extra="forbidden"),
    lambda d: d["contracts"].reverse(),
    lambda d: d["contracts"][1]["candidate_contract"].update(shape=""),
    lambda d: d["contracts"][1]["candidate_contract"].update(requirements=[]),
    lambda d: d["contracts"][1]["candidate_contract"]["bindings"].append(deepcopy(d["contracts"][1]["candidate_contract"]["bindings"][0])),
    lambda d: d["contracts"][1]["candidate_contract"]["bindings"][0].update(source="INFERRED"),
    lambda d: d["contracts"][1]["candidate_contract"].update(shape="x" * 8193),
    lambda d: d["contracts"][1]["candidate_contract"].update(requirements=["x" * 2049]),
    lambda d: d["contracts"][1]["candidate_contract"].update(requirements=["x" * 1100] * 16),
    lambda d: d["contracts"][1]["candidate_contract"].update(shape=float("nan")),
    lambda d: d["contracts"][1]["candidate_contract"].update(shape="\ud800"),
    lambda d: d.update(bounds={**d["bounds"], "max_depth": 33}),
])
def test_closed_grammar_and_bounds(change):
    data = deepcopy(registry())
    change(data)
    with pytest.raises(BrainReturnContractError):
        normalize_return_contract_registry(data)


def test_parser_rejects_raw_and_yaml_malformed_material():
    source = REGISTRY.read_text(encoding="utf-8")
    for invalid in (
        b"\xff", "x" * 65537,
        source.replace("format: AIOS_BRAIN_RETURN_CONTRACTS", "format: AIOS_BRAIN_RETURN_CONTRACTS\nformat: AIOS_BRAIN_RETURN_CONTRACTS", 1),
        source.replace("format: AIOS_BRAIN_RETURN_CONTRACTS", "? [non, string]\n: value\nformat: AIOS_BRAIN_RETURN_CONTRACTS", 1),
        source.replace("  raw_registry_bytes: 65536", "  raw_registry_bytes: &ceiling 65536", 1).replace("  selected_contract_bytes: 16384", "  selected_contract_bytes: *ceiling", 1),
        source + "\n---\nformat: SECOND\n",
    ):
        with pytest.raises(BrainReturnContractError):
            parse_return_contract_registry(invalid)


def test_family_candidates_use_only_existing_canonical_validators():
    data = registry()
    task_candidate = {
        "task_id": "TASK-181", "revision": 1,
        "goal": "Author a bounded candidate.", "problem": "A candidate is needed.",
        "assumptions": [], "scope": {"inspect": [], "modify": ["src/example.py"]},
        "non_goals": [], "constraints": {"hard": ["Keep scope bounded."]},
        "acceptance": [{"id": "AC1", "condition": "The bounded candidate exists."}],
        "verification": {"policy": "minimum-sufficient-v1", "required": ["python -m pytest -q tests/test_example.py"]},
    }
    task = validate_task(task_candidate)
    assert task.task_id == "TASK-181"
    task_contract = data["contracts"][1]
    assert [b["source"] for b in task_contract["candidate_contract"]["bindings"]] == [
        "EXTERNAL_REQUEST_BINDING_REQUIRED", "EXTERNAL_REQUEST_BINDING_REQUIRED",
    ]

    review = parse_review(json.dumps({
        "review_id": "REVIEW-181-001", "reviewed_sha": "a" * 40,
        "mode": "PRIMARY", "verdict": "CHANGES_REQUIRED",
        "acceptance": {"AC1": "FAIL"},
        "findings": [{"id": "R1", "basis": "AC1", "action": "CODE_FIX",
                      "location": "src/example.py", "issue": "Missing candidate.",
                      "expected": "Add candidate."}],
    }))
    remediation = parse_remediation(json.dumps({
        "finding_id": "R1", "action": "CODE_FIX", "reviewed_sha": "a" * 40,
        "modification_scope": ["src/example.py"],
        "verification": {"policy": "minimum-sufficient-v1", "affected": ["python -m pytest -q tests/test_example.py"]},
        "constraints": {"hard": ["Keep scope bounded."]},
    }))
    assert validate_remediation(review=review, remediation=remediation, task=task) is remediation
    repair = {
        "repair_id": "REPAIR-181-001", "failed_run_id": "RUN-181-001",
        "failed_head_sha": "a" * 40, "task": {"id": task.task_id, "revision": task.revision},
        "action": "CODE_FIX", "modification_scope": ["src/example.py"],
        "instructions": ["Correct the failed candidate."],
        "constraints": ["Keep scope bounded."],
    }
    assert _validate_repair_authorization(
        repair, failed_run_id="RUN-181-001", failed_head_sha="a" * 40,
        task=task, failed_changed_files=set(),
    ) == repair
    assert [b["source"] for b in data["contracts"][3]["candidate_contract"]["bindings"]] == [
        "EXTERNAL_REQUEST_BINDING_REQUIRED", "DECISION_PACKET", "DECISION_PACKET",
        "DECISION_PACKET", "DECISION_PACKET",
    ]
