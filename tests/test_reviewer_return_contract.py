"""Focused local examples for the pure BP6-P2B return contract."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from aios_renew.reviewer_return_contract import (
    ReviewerReturnContractError, normalize_reviewer_return_registry,
    parse_reviewer_return_registry, parse_reviewer_semantic_body,
    reviewer_return_contract_ref, select_reviewer_return_contract,
)
from aios_renew.task import validate_task


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / ".ai" / "reviewer-return-contracts.yaml"


@pytest.fixture
def registry():
    return parse_reviewer_return_registry(REGISTRY.read_bytes())


@pytest.fixture
def task():
    return validate_task({
        "task_id": "TASK-187", "revision": 1, "goal": "Review the result.",
        "problem": "A result needs review.", "assumptions": [],
        "scope": {"inspect": [], "modify": ["src/example.py"]},
        "non_goals": [], "constraints": {"hard": []},
        "acceptance": [{"id": name, "condition": f"Check {name}."}
                       for name in ("AC1", "AC2", "AC3")],
        "verification": {"required": ["python -m pytest -q tests/test_example.py"]},
    })


def finding(basis="AC2", **changes):
    return {"basis": basis, "action": "CODE_FIX", "location": "semantic locus: reviewer flow",
            "issue": "Incorrect behavior.", "expected": "Correct behavior.", **changes}


def body(verdict="PASS", acceptance=None, findings=None):
    return {"verdict": verdict,
            "acceptance": acceptance if acceptance is not None else [
                {"id": name, "outcome": "PASS"} for name in ("AC1", "AC2", "AC3")],
            "findings": findings if findings is not None else []}


def parse(value, task, mode="PRIMARY", prior=None):
    return parse_reviewer_semantic_body(value, task=task, review_mode=mode,
                                        prior_finding_basis=prior)


def test_registry_identity_metadata_and_exact_bounds(registry):
    selected = select_reviewer_return_contract(registry)
    contract = selected["contract"]
    assert registry["format"] == "AIOS_REVIEWER_RETURN_CONTRACTS"
    assert registry["version"] == 1 and len(registry["contracts"]) == 1
    assert (contract["id"], contract["version"]) == ("reviewer-semantic-return-v1", 1)
    cards = yaml.safe_load((ROOT / ".ai" / "flow-cards.yaml").read_text(encoding="utf-8"))["cards"]
    card = next(card for card in cards if card["id"] == "SEMANTIC_REVIEW")
    for key in ("authority_owner", "decision_family_ref", "handoff_target", "expected_return_shape"):
        assert contract[key] == card[key]
    assert contract["selected_flow"] == "SEMANTIC_REVIEW"
    assert selected["bounds"] == {
        "raw_registry_bytes": 65536, "selected_contract_bytes": 32768,
        "semantic_body_bytes": 131072, "acceptance_entries": 256,
        "findings": 32, "location_bytes": 2048, "issue_bytes": 8192,
        "expected_bytes": 8192, "max_depth": 32,
    }
    assert selected["reviewer_return_contract_ref"]["id"] == contract["id"]
    assert len(selected["reviewer_return_contract_ref"]["digest"]) == 64


def test_registry_serialization_invariance_and_content_sensitivity(registry):
    original = select_reviewer_return_contract(registry)["reviewer_return_contract_ref"]
    reordered = {"contracts": registry["contracts"], "bounds": registry["bounds"],
                 "version": 1, "format": registry["format"]}
    source = yaml.safe_dump(reordered, sort_keys=True, allow_unicode=True)
    assert select_reviewer_return_contract(source.replace("\n", "\r\n"))[
        "reviewer_return_contract_ref"] == original
    assert select_reviewer_return_contract(REGISTRY.read_bytes())[
        "reviewer_return_contract_ref"] == original
    for path, replacement in (
        (("contracts", 0, "grammar", "body_fields"), ["verdict", "findings", "acceptance"]),
        (("contracts", 0, "rules", 0), "CHANGED_RULE"),
        (("contracts", 0, "authority_owner"), "BRAIN"),
        (("bounds", "findings"), 33),
    ):
        changed = deepcopy(registry)
        node = changed
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = replacement
        assert reviewer_return_contract_ref(changed["contracts"][0], changed["bounds"])[
            "digest"] != original["digest"]
        with pytest.raises(ReviewerReturnContractError):
            select_reviewer_return_contract(changed)
    assert not set(original) & {"provider", "model", "session", "review_id", "reviewed_sha", "prior_finding_id"}


@pytest.mark.parametrize("change", [
    lambda d: d.update(extra="x"),
    lambda d: d["contracts"].append(deepcopy(d["contracts"][0])),
    lambda d: d["contracts"][0].update(id="other"),
    lambda d: d["contracts"][0].update(version=True),
    lambda d: d["contracts"][0].update(representation="YAML"),
    lambda d: d["contracts"][0]["grammar"].update(extra="x"),
    lambda d: d["contracts"][0]["grammar"].update(finding_actions=["REPAIR"]),
    lambda d: d["contracts"][0].update(rules=[]),
    lambda d: d["bounds"].update(max_depth=33),
    lambda d: d["contracts"][0].update(provider="model"),
    lambda d: d["contracts"][0].update(review_id="REVIEW-1"),
    lambda d: d["contracts"][0].update(selected_flow="\ud800"),
    lambda d: d["contracts"][0].update(grammar=float("nan")),
])
def test_registry_closed_material(registry, change):
    changed = deepcopy(registry)
    change(changed)
    with pytest.raises(ReviewerReturnContractError):
        normalize_reviewer_return_registry(changed)


def test_registry_raw_yaml_bounds_duplicates_aliases_and_depth(registry):
    source = REGISTRY.read_text(encoding="utf-8")
    padded = source + " " * (65536 - len(source.encode("utf-8")))
    assert parse_reviewer_return_registry(padded) == registry
    for invalid in (
        b"\xff", b" " * 65537,
        source.replace("version: 1", "version: 1\nversion: 1", 1),
        source.replace("  raw_registry_bytes: 65536", "  raw_registry_bytes: &b 65536", 1)
              .replace("  selected_contract_bytes: 32768", "  selected_contract_bytes: *b", 1),
        source + "\n---\nformat: other\n",
        source.replace("format: AIOS_REVIEWER_RETURN_CONTRACTS", "format: !!binary YQ==", 1),
    ):
        with pytest.raises(ReviewerReturnContractError):
            parse_reviewer_return_registry(invalid)
    changed = deepcopy(registry)
    changed["contracts"][0]["grammar"]["body_fields"] = [[[[[["x"]]]]]]
    with pytest.raises(ReviewerReturnContractError):
        normalize_reviewer_return_registry(changed)
    with pytest.raises(ReviewerReturnContractError):
        normalize_reviewer_return_registry({"nested": 0})


def test_primary_delta_and_deterministic_acceptance_order(task):
    reversed_body = body(acceptance=list(reversed(body()["acceptance"])))
    assert parse(reversed_body, task) == body()
    assert parse(json.dumps(reversed_body).encode(), task) == body()
    delta = body(acceptance=[{"id": "AC3", "outcome": "PASS"},
                             {"id": "AC2", "outcome": "PASS"}])
    assert [entry["id"] for entry in parse(delta, task, "DELTA", "AC2")["acceptance"]] == ["AC2", "AC3"]
    changed = body("CHANGES_REQUIRED", [
        {"id": "AC2", "outcome": "FAIL"}, {"id": "AC1", "outcome": "PASS"},
        {"id": "AC3", "outcome": "FAIL"}], [finding("AC3"), finding("AC2")])
    assert [item["basis"] for item in parse(changed, task)["findings"]] == ["AC3", "AC2"]
    assert parse(body("BLOCKED"), task)["verdict"] == "BLOCKED"
    assert parse(body("BLOCKED", findings=[finding()]), task)["findings"] == [finding()]
    prose = finding(location="Reviewer scope:\r\nline 2", issue="Issue\r\ntext")
    blocked = body("BLOCKED", findings=[prose])
    assert parse(json.dumps(blocked), task)["findings"][0] == prose


def test_acceptance_entry_boundary_at_256(task):
    values = {"task_id": task.task_id, "revision": task.revision,
              "goal": task.goal, "problem": task.problem, "assumptions": [],
              "scope": {"inspect": [], "modify": ["src/example.py"]},
              "non_goals": [], "constraints": {"hard": []},
              "acceptance": [{"id": f"AC{number}", "condition": "Check."}
                             for number in range(1, 257)],
              "verification": {"required": ["python -m pytest -q tests/test_example.py"]}}
    large_task = validate_task(values)
    entries = [{"id": f"AC{number}", "outcome": "PASS"} for number in range(1, 257)]
    assert len(parse_reviewer_semantic_body(body(acceptance=entries), task=large_task,
                                            review_mode="PRIMARY")["acceptance"]) == 256
    with pytest.raises(ReviewerReturnContractError):
        parse_reviewer_semantic_body(body(acceptance=entries + [entries[0]]),
                                     task=large_task, review_mode="PRIMARY")


@pytest.mark.parametrize("value,mode,prior", [
    (body(acceptance=[{"id": "AC1", "outcome": "PASS"}]), "PRIMARY", None),
    (body(acceptance=body()["acceptance"] + [{"id": "AC1", "outcome": "PASS"}]), "PRIMARY", None),
    (body(acceptance=[{"id": "OTHER", "outcome": "PASS"}]), "DELTA", "AC1"),
    (body(acceptance=[{"id": "AC1", "outcome": "PASS"}]), "DELTA", "AC2"),
    (body(), "PRIMARY", "AC1"),
    (body(), "DELTA", None),
    (body(), "DELTA", "OTHER"),
    (body(), "OTHER", None),
    (body("PASS", [{"id": "AC1", "outcome": "FAIL"},
                   {"id": "AC2", "outcome": "PASS"}, {"id": "AC3", "outcome": "PASS"}]), "PRIMARY", None),
    (body("PASS", findings=[finding()]), "PRIMARY", None),
    (body("CHANGES_REQUIRED"), "PRIMARY", None),
    (body("CHANGES_REQUIRED", [{"id": "AC1", "outcome": "PASS"},
                               {"id": "AC2", "outcome": "FAIL"},
                               {"id": "AC3", "outcome": "PASS"}]), "PRIMARY", None),
    (body("CHANGES_REQUIRED", [{"id": "AC1", "outcome": "PASS"},
                               {"id": "AC2", "outcome": "FAIL"},
                               {"id": "AC3", "outcome": "PASS"}], [finding("AC1")]), "PRIMARY", None),
    (body("CHANGES_REQUIRED", [{"id": "AC1", "outcome": "PASS"},
                               {"id": "AC2", "outcome": "FAIL"},
                               {"id": "AC3", "outcome": "FAIL"}], [finding("AC2")]), "PRIMARY", None),
])
def test_acceptance_context_and_verdict_consistency(task, value, mode, prior):
    with pytest.raises(ReviewerReturnContractError):
        parse(value, task, mode, prior)


@pytest.mark.parametrize("path,value", [
    (("review_id",), "REVIEW-187-001"),
    (("reviewed_sha",), "a" * 40),
    (("mode",), "PRIMARY"),
    (("prior_finding_id",), "F1"),
    (("provider",), "x"),
    (("model",), "x"),
    (("session",), "x"),
    (("acceptance", 0, "review_id"), "x"),
    (("acceptance", 0, "provider"), "x"),
    (("findings", 0, "id"), "F1"),
    (("findings", 0, "retry"), True),
])
def test_identity_and_operational_smuggling(task, path, value):
    changed = body("CHANGES_REQUIRED", [
        {"id": "AC1", "outcome": "PASS"}, {"id": "AC2", "outcome": "FAIL"},
        {"id": "AC3", "outcome": "PASS"}], [finding()])
    node = changed
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    with pytest.raises(ReviewerReturnContractError):
        parse(changed, task)


@pytest.mark.parametrize("invalid", [
    '{"verdict":"PASS","verdict":"BLOCKED","acceptance":[],"findings":[]}',
    '{"verdict":"PASS","acceptance":[{"id":"AC1","id":"AC2","outcome":"PASS"}],"findings":[]}',
    '{"verdict":NaN,"acceptance":[],"findings":[]}',
    '{"verdict":Infinity,"acceptance":[],"findings":[]}',
    '{"verdict":"PASS","acceptance":[],"findings":[]}//comment',
    '{"verdict":"PASS","acceptance":[],"findings":[],"model":"x"}',
    '{"verdict":"PASS","acceptance":[],"findings":[],"review_id":"R1"}',
    b"\xff",
    '{"verdict":"\ud800","acceptance":[],"findings":[]}',
])
def test_raw_json_failures_are_typed_contract_errors(task, invalid):
    with pytest.raises(ReviewerReturnContractError):
        parse(invalid, task)


def test_exact_bounds_and_non_json_mapping_values(task, registry):
    valid = body("CHANGES_REQUIRED", [
        {"id": "AC1", "outcome": "PASS"}, {"id": "AC2", "outcome": "FAIL"},
        {"id": "AC3", "outcome": "PASS"}], [finding(location="x" * 2048,
                                                       issue="x" * 8192,
                                                       expected="x" * 8192)])
    assert parse(valid, task)["findings"] == valid["findings"]
    assert len(parse(body("BLOCKED", findings=[finding() for _ in range(32)]), task)[
        "findings"]) == 32
    for field, size in (("location", 2049), ("issue", 8193), ("expected", 8193)):
        changed = deepcopy(valid)
        changed["findings"][0][field] = "x" * size
        with pytest.raises(ReviewerReturnContractError):
            parse(changed, task)
    for invalid in (
        body(findings=[finding() for _ in range(33)]),
        body(acceptance=[{"id": "AC1", "outcome": "PASS"} for _ in range(257)]),
        body(findings=[finding(action="REPAIR")]),
        body(findings=[finding(location="  ")]),
        {**body(), "extra": {"nested": "x"}},
        {**body(), "verdict": float("nan")},
        {**body(), "verdict": {1: "PASS"}},
        {**body(), "verdict": ("PASS",)},
        {**body(), "verdict": "\ud800"},
        {**body(), "acceptance": [1, 2]},
        {**body(), "findings": [["x"]]},
    ):
        with pytest.raises(ReviewerReturnContractError):
            parse(invalid, task)
    with pytest.raises(ReviewerReturnContractError):
        parse(b" " * 131073, task)
    oversized_closed = body("BLOCKED", findings=[finding(
        location="x" * 2048, issue="x" * 8192, expected="x" * 8192,
    ) for _ in range(8)])
    assert len(json.dumps(oversized_closed).encode("utf-8")) > 131072
    for representation in (oversized_closed, json.dumps(oversized_closed)):
        with pytest.raises(ReviewerReturnContractError):
            parse(representation, task)
    with pytest.raises(ReviewerReturnContractError):
        parse({**body(), "findings": [], "acceptance": body()["acceptance"],
               "verdict": "PASS", "x": "x" * 131072}, task)
    deep = body()
    deep["verdict"] = []
    item = deep["verdict"]
    for _ in range(33):
        child = []
        item.append(child)
        item = child
    with pytest.raises(ReviewerReturnContractError):
        parse(deep, task)
    changed = deepcopy(registry)
    changed["contracts"][0]["grammar"]["body_fields"] = ["x" * 32769]
    with pytest.raises(ReviewerReturnContractError):
        normalize_reviewer_return_registry(changed)
    with pytest.raises(ReviewerReturnContractError):
        parse_reviewer_return_registry(b" " * 65537)


def test_p2b_has_no_p3_p4_authority(task):
    parsed = parse(body(), task)
    assert set(parsed) == {"verdict", "acceptance", "findings"}
    assert all(set(entry) == {"id", "outcome"} for entry in parsed["acceptance"])
    selected = select_reviewer_return_contract(REGISTRY.read_bytes())
    assert set(selected) == {"contract", "bounds", "reviewer_return_contract_ref"}
    assert not set(parsed) & {"review_id", "reviewed_sha", "mode", "prior_finding_id"}
    assert not set(selected) & {"request", "decision", "provider", "model", "session"}
