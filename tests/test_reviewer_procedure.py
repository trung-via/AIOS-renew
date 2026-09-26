"""Local examples for the pure BP6-P2A procedure contract."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from aios_renew.reviewer_procedure import (
    ReviewerProcedureError, normalize_profile_registry, parse_profile_registry,
    select_reviewer_procedure,
)


REGISTRY = Path(__file__).resolve().parents[1] / ".ai" / "reviewer-procedure-profiles.yaml"


@pytest.fixture
def raw():
    return REGISTRY.read_bytes()


@pytest.fixture
def registry(raw):
    return parse_profile_registry(raw)


def test_registry_selection_and_closed_order(registry):
    primary = select_reviewer_procedure(registry, "PRIMARY")
    delta = select_reviewer_procedure(registry, "DELTA")
    assert primary["profile"] == registry["profiles"][0]
    assert primary["bounds"] == registry["bounds"]
    assert [mode["mode"] for mode in primary["profile"]["modes"]] == ["PRIMARY", "DELTA"]
    assert [step["id"] for step in primary["procedure"]["steps"]] == [
        "CONTRACT_COVERAGE", "SEMANTIC_DELTA_INSPECTION",
        "CLAIM_EVIDENCE_CROSSCHECK", "MATERIAL_DEFECT_SWEEP", "VERDICT_CLOSURE",
    ]
    assert [step["id"] for step in delta["procedure"]["steps"]] == [
        "PRIOR_FINDING_RESOLUTION", "LATEST_CORRECTION_INSPECTION",
        "PRIOR_CONCLUSION_INVALIDATION", "VERDICT_CLOSURE",
    ]
    assert primary["reviewer_procedure_ref"] == delta["reviewer_procedure_ref"]
    assert len(primary["reviewer_procedure_ref"]["digest"]) == 64


def test_equivalent_serialization_has_same_ref(raw, registry):
    expected = select_reviewer_procedure(raw, "PRIMARY")["reviewer_procedure_ref"]
    crlf = raw.replace(b"\n", b"\r\n")
    assert select_reviewer_procedure(crlf, "PRIMARY")["reviewer_procedure_ref"] == expected
    reordered = {
        "profiles": registry["profiles"], "bounds": registry["bounds"],
        "version": registry["version"], "format": registry["format"],
    }
    assert select_reviewer_procedure(yaml.safe_dump(reordered), "PRIMARY")[
        "reviewer_procedure_ref"] == expected


@pytest.mark.parametrize("path,replacement", [
    (("profiles", 0, "selected_flow"), "OTHER"),
    (("profiles", 0, "modes", 0, "steps", 0, "check"), "Changed check."),
    (("profiles", 0, "semantic_verdict_boundary"), "Changed boundary."),
    (("profiles", 0, "invocation_boundary"), "Changed invocation."),
])
def test_content_address_changes_with_profile_material(registry, path, replacement):
    before = select_reviewer_procedure(registry, "PRIMARY")["reviewer_procedure_ref"]
    changed = deepcopy(registry)
    node = changed
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = replacement
    if path[-1] == "selected_flow":
        with pytest.raises(ReviewerProcedureError):
            select_reviewer_procedure(changed, "PRIMARY")
    else:
        assert select_reviewer_procedure(changed, "PRIMARY")[
            "reviewer_procedure_ref"] != before


def test_closed_identity_mode_order_and_effective_bounds(registry):
    changed = deepcopy(registry)
    changed["bounds"]["steps_per_mode"] = 9
    with pytest.raises(ReviewerProcedureError):
        normalize_profile_registry(changed)
    changed = deepcopy(registry)
    changed["profiles"][0]["modes"].reverse()
    with pytest.raises(ReviewerProcedureError):
        normalize_profile_registry(changed)
    changed = deepcopy(registry)
    changed["profiles"][0]["modes"][0]["steps"][0]["id"] = "VERDICT_CLOSURE"
    with pytest.raises(ReviewerProcedureError):
        normalize_profile_registry(changed)
    changed = deepcopy(registry)
    changed["profiles"][0]["modes"][0]["steps"][0]["extra"] = "forbidden"
    with pytest.raises(ReviewerProcedureError):
        normalize_profile_registry(changed)
    for mode in ("primary", "REPAIR", None, 1):
        with pytest.raises(ReviewerProcedureError):
            select_reviewer_procedure(registry, mode)


def test_bounded_material_and_invalid_values(registry):
    changed = deepcopy(registry)
    changed["profiles"][0]["modes"][0]["steps"][0]["check"] = "x" * 4097
    with pytest.raises(ReviewerProcedureError):
        normalize_profile_registry(changed)
    changed = deepcopy(registry)
    changed["profiles"][0]["semantic_verdict_boundary"] = "\ud800"
    with pytest.raises(ReviewerProcedureError):
        normalize_profile_registry(changed)
    changed = deepcopy(registry)
    changed["profiles"][0]["version"] = True
    with pytest.raises(ReviewerProcedureError):
        normalize_profile_registry(changed)
    with pytest.raises(ReviewerProcedureError):
        parse_profile_registry(b" " * 65537)


@pytest.mark.parametrize("payload", [
    b"format: x\nformat: y\n",
    b"format: &a x\nversion: *a\n",
    b"format: !!binary YQ==\n",
    b"\xff",
    b"format: x\n---\nformat: y\n",
])
def test_invalid_yaml_is_rejected(payload):
    with pytest.raises(ReviewerProcedureError):
        parse_profile_registry(payload)
