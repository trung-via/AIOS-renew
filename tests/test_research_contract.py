"""Focused RA-1 structural examples; Runtime owns canonical verification."""

from copy import deepcopy
import json

import pytest

from aios_renew.research_contract import (
    ResearchContractError, construct_acquisition_request, construct_challenge_target,
    construct_research_brief, parse_research_json, validate_acquisition_request,
    validate_challenge_target, validate_research_brief,
)


def brief_material():
    return {
        "format": "AIOS_RESEARCH_BRIEF", "version": 1, "kind": "RESEARCH_BRIEF",
        "question": "Which interface is current?", "decision_context": "Choose an architecture.",
        "scope": {"include": ["API", "specification"], "exclude": ["marketing"]},
        "current_as_of": {"mode": "TIMESTAMP", "value": "2024-02-29T12:30:59Z"},
        "project_basis": [{"kind": "COMMIT", "locator": "repo/module", "identity": "abc123"}],
        "source_policy": {"allowed_source_families": ["OFFICIAL_DOCS", "REPOSITORY"],
                          "independence_required": True,
                          "excluded_locators": ["https://example.org/old"]},
        "resource_bounds": {"max_baseline_requests": 3, "max_sources_per_request": 4,
                            "max_observation_bytes": 4096, "max_counter_evidence_requests": 2},
        "handoff_target": "ARCHITECTURE", "invalidation_basis": [],
    }


def target_material(brief):
    return {"format": "AIOS_RESEARCH_CHALLENGE_TARGET", "version": 1,
            "kind": "RESEARCH_CHALLENGE_TARGET", "brief_fingerprint": brief["brief_fingerprint"],
            "target_kind": "FRESHNESS", "target_ref": "API", "challenge": "Check the date.",
            "rationale": "The reference may be old."}


def request_material(brief, phase="BASELINE", fingerprints=None):
    return {"format": "AIOS_ACQUISITION_REQUEST", "version": 1,
            "kind": "ACQUISITION_REQUEST", "brief_fingerprint": brief["brief_fingerprint"],
            "phase": phase, "purpose": "Find current documentation.",
            "locator_or_query": "https://example.org/docs", "source_family": "OFFICIAL_DOCS",
            "challenge_target_fingerprints": fingerprints or [],
            "bounds": {"max_items": 2, "max_total_observation_bytes": 2048}}


def rejects(fn, value, *args):
    with pytest.raises(ResearchContractError):
        fn(value, *args)


def test_brief_revalidation_identity_and_semantic_changes():
    material = brief_material()
    first = construct_research_brief(material)
    assert validate_research_brief(first) == first
    assert material == brief_material()
    equivalent = deepcopy(material)
    equivalent["question"] = "Which interface is current?\r\n"
    material["question"] += "\n"
    equivalent["scope"]["include"].reverse()
    equivalent["source_policy"]["allowed_source_families"].reverse()
    equivalent["project_basis"] = list(reversed(equivalent["project_basis"]))
    assert construct_research_brief(equivalent)["brief_fingerprint"] == \
        construct_research_brief(material)["brief_fingerprint"]
    for path, value in (("question", "Different question"), ("decision_context", "Different use"),
                        ("handoff_target", "TASK_AUTHORING")):
        changed = deepcopy(material)
        changed[path] = value
        if path == "handoff_target":
            rejects(construct_research_brief, changed)
        else:
            assert construct_research_brief(changed)["brief_fingerprint"] != \
                construct_research_brief(material)["brief_fingerprint"]
    stale = deepcopy(first)
    stale["question"] = "Changed"
    rejects(validate_research_brief, stale)


@pytest.mark.parametrize("mode,value,valid", [
    ("DATE", "2024-02-29", True), ("DATE", "2023-02-29", False),
    ("DATE", "2024-2-9", False), ("TIMESTAMP", "2024-02-29T23:59:59Z", True),
    ("TIMESTAMP", "2024-02-30T12:00:00Z", False),
    ("TIMESTAMP", "2024-02-29T12:00:00+00:00", False),
    ("TIMESTAMP", "2024-02-29T12:00:60Z", False),
    ("NOT_APPLICABLE", None, True), ("NOT_APPLICABLE", "", False),
])
def test_calendar_grammar(mode, value, valid):
    material = brief_material()
    material["current_as_of"] = {"mode": mode, "value": value}
    if valid:
        assert construct_research_brief(material)["current_as_of"]["value"] == value
    else:
        rejects(construct_research_brief, material)


def test_brief_fails_closed_on_fields_duplicates_portability_privacy_and_bounds():
    changes = [
        lambda b: b.update(extra="not allowed"),
        lambda b: b["scope"]["include"].append("API"),
        lambda b: b["scope"]["exclude"].extend(str(i) for i in range(33)),
        lambda b: b["project_basis"].append(deepcopy(b["project_basis"][0])),
        lambda b: b["project_basis"][0].update(locator="C:\\Users\\private"),
        lambda b: b["project_basis"][0].update(locator="https://user:pass@example.org"),
        lambda b: b["source_policy"]["excluded_locators"].append("/home/alice/file"),
        lambda b: b["source_policy"]["allowed_source_families"].append("RANDOM"),
        lambda b: b["resource_bounds"].update(max_sources_per_request=True),
        lambda b: b["resource_bounds"].update(max_observation_bytes=262145),
        lambda b: b.update(question="bad\ud800"),
        lambda b: b.update(question="password=topsecret"),
        lambda b: b.update(question="x" * 8193),
        lambda b: b["project_basis"][0].update(identity="x" * 513),
        lambda b: b["project_basis"][0].update(kind="lowercase"),
        lambda b: b["source_policy"].update(independence_required=1),
    ]
    for change in changes:
        material = brief_material()
        change(material)
        rejects(construct_research_brief, material)
    rejects(parse_research_json, '{"a":1,"a":2}')
    rejects(parse_research_json, '{"a":NaN}')


def test_order_insensitive_basis_and_normalized_duplicates():
    material = brief_material()
    material["project_basis"].append({"kind": "SPEC", "locator": "docs/spec", "identity": "v1"})
    first = construct_research_brief(material)
    material["project_basis"].reverse()
    assert construct_research_brief(material)["brief_fingerprint"] == first["brief_fingerprint"]
    material["scope"]["include"].append("API\r\n")
    material["scope"]["include"].append("API\n")
    rejects(construct_research_brief, material)
    material = brief_material()
    material["scope"]["include"] = ["caf\u00e9", "cafe\u0301"]
    rejects(construct_research_brief, material)


def test_normalized_brief_json_size_limit():
    material = brief_material()
    material["scope"]["include"] = [f"{index:02d}" + "x" * 1900 for index in range(32)]
    material["scope"]["exclude"] = [f"{index:02d}" + "y" * 1900 for index in range(32)]
    rejects(construct_research_brief, material)


def test_target_binding_and_closed_contract():
    brief = construct_research_brief(brief_material())
    target = construct_challenge_target(target_material(brief), brief)
    assert validate_challenge_target(target, brief) == target
    alternative = deepcopy(brief_material())
    alternative["question"] = "Another subject?"
    foreign = construct_research_brief(alternative)
    rejects(validate_challenge_target, target, foreign)
    for key, value in (("target_kind", "VERDICT"), ("challenge", ""),
                       ("target_ref", "x" * 1025), ("rationale", "x" * 4097)):
        changed = target_material(brief)
        changed[key] = value
        rejects(construct_challenge_target, changed, brief)
    changed = target_material(brief)
    changed["source_content"] = "untrusted"
    rejects(construct_challenge_target, changed, brief)
    stale = deepcopy(target)
    stale["rationale"] = "Other reason"
    rejects(validate_challenge_target, stale, brief)


def test_acquisition_phases_identity_and_exact_target_binding():
    brief = construct_research_brief(brief_material())
    target = construct_challenge_target(target_material(brief), brief)
    baseline = construct_acquisition_request(request_material(brief), brief)
    assert validate_acquisition_request(baseline, brief) == baseline
    counter = construct_acquisition_request(request_material(
        brief, "COUNTER_EVIDENCE", [target["target_fingerprint"]]), brief, [target])
    assert validate_acquisition_request(counter, brief, [target]) == counter
    rejects(validate_acquisition_request, counter, brief)
    rejects(construct_acquisition_request, request_material(brief, "COUNTER_EVIDENCE"), brief)
    rejects(construct_acquisition_request, request_material(
        brief, "BASELINE", [target["target_fingerprint"]]), brief, [target])
    changed = request_material(brief)
    changed["purpose"] = "Another purpose"
    assert construct_acquisition_request(changed, brief)["request_fingerprint"] != baseline["request_fingerprint"]
    changed = request_material(brief)
    changed["purpose"] += "\r\n"
    normalized = request_material(brief)
    normalized["purpose"] += "\n"
    assert construct_acquisition_request(changed, brief)["request_fingerprint"] == \
        construct_acquisition_request(normalized, brief)["request_fingerprint"]
    foreign_material = brief_material()
    foreign_material["question"] = "Foreign"
    foreign = construct_research_brief(foreign_material)
    rejects(validate_acquisition_request, baseline, foreign)
    rejects(validate_acquisition_request, counter, foreign, [target])
    reordered = dict(reversed(list(request_material(brief).items())))
    assert construct_acquisition_request(reordered, brief)["request_fingerprint"] == \
        baseline["request_fingerprint"]


def test_request_rejects_widening_private_material_and_substitutions():
    brief = construct_research_brief(brief_material())
    target = construct_challenge_target(target_material(brief), brief)
    base = request_material(brief)
    mutations = [
        lambda r: r["bounds"].update(max_items=5),
        lambda r: r["bounds"].update(max_total_observation_bytes=4097),
        lambda r: r["bounds"].update(max_items=True),
        lambda r: r.update(source_family="PUBLIC_WEB"),
        lambda r: r.update(locator_or_query="/tmp/secrets"),
        lambda r: r.update(locator_or_query="https://example.org/?token=abc"),
        lambda r: r.update(locator_or_query="http://127.0.0.1/a"),
        lambda r: r.update(provider="model-x"),
        lambda r: r.update(source_content="retrieved text"),
        lambda r: r.update(command="fetch all"),
        lambda r: r.update(purpose="x" * 4097),
    ]
    for mutation in mutations:
        changed = deepcopy(base)
        mutation(changed)
        rejects(construct_acquisition_request, changed, brief)
    counter = request_material(brief, "COUNTER_EVIDENCE", [target["target_fingerprint"]])
    changed_target = deepcopy(target)
    changed_target["challenge"] = "Substituted"
    rejects(construct_acquisition_request, counter, brief, [changed_target])
    counter["challenge_target_fingerprints"].append(target["target_fingerprint"])
    rejects(construct_acquisition_request, counter, brief, [target])


def test_contract_functions_have_no_io_or_lifecycle_surface(monkeypatch):
    import builtins
    import pathlib
    import socket
    import subprocess
    import time

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected I/O or clock access")

    brief = construct_research_brief(brief_material())
    target = construct_challenge_target(target_material(brief), brief)
    request = request_material(brief, "COUNTER_EVIDENCE", [target["target_fingerprint"]])
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(pathlib.Path, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    assert validate_research_brief(brief) == brief
    assert validate_challenge_target(target, brief) == target
    assert validate_acquisition_request(
        construct_acquisition_request(request, brief, [target]), brief, [target])
    assert json.loads(json.dumps(brief)) == brief
