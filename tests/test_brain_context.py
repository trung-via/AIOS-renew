"""Offline BP-3 contracts over bounded Brain Sync observations."""

from dataclasses import replace
import json
from pathlib import Path

import pytest
import yaml

from aios_renew.brain_context import (
    BrainContextError,
    compose_brain_work_context,
    load_flow_cards,
    resolve_flow,
)
from aios_renew.brain_sync import BrainSyncSnapshot


def snapshot(action="EXECUTE_PRIMARY", *, blocker=None, status="SELECTED", roadmap=None):
    unified = None if status != "SELECTED" else {
        "format": "AIOS_UNIFIED_STATE", "version": 1,
        "task": {"id": "TASK-177", "revision": 2},
        "next_action": action, "run_id": "RUN-177-001",
        "finding_id": "FINDING-177-001", "blocker": blocker,
    }
    return BrainSyncSnapshot(
        repository={"root": "/repo", "name": "AIOS-renew", "main_sha": "a" * 40,
                    "remote": "origin", "remote_url": "https://secret@example.test/repo"},
        main_sha="a" * 40,
        roadmap=roadmap or {"present": True, "next_items": ["bp-3"]},
        selection_status=status, lifecycle_state="READY" if blocker is None else "BLOCKED",
        next_action=action, authority="REVIEWER" if action == "SEMANTIC_REVIEW" else "BRAIN",
        selected_task={"id": "TASK-177", "revision": 2} if unified else None,
        unified_state=unified, blocker=blocker,
    )


@pytest.mark.parametrize("action,expected", [
    ("EXECUTE_PRIMARY", "NONE"), ("EXECUTE_REMEDIATION", "NONE"),
    ("EXECUTE_REPAIR", "NONE"), ("RETRY_TRANSPORT", "NONE"),
    ("RECOVER_PRIMARY", "NONE"), ("PUBLICATION", "NONE"),
    ("WAIT", "NONE"), ("DONE", "NONE"), ("NONE", "NONE"),
    ("SEMANTIC_REVIEW", "SEMANTIC_REVIEW"),
    ("AUTHOR_REMEDIATION", "REMEDIATION_AUTHORING"),
    ("AUTHOR_REPAIR", "REPAIR_AUTHORING"),
])
def test_exact_unified_state_selection(action, expected):
    source = snapshot(action)
    before = source.as_dict()
    context = compose_brain_work_context(source)
    resolution = resolve_flow(context)
    assert resolution.selected_flow == expected
    assert resolution.pending_canonical_obligation == (None if expected == "NONE" else expected)
    assert resolution.canonical_next_action == action
    assert source.as_dict() == before
    assert context.canonical_observation["next_action"] == action
    assert "remote_url" not in context.canonical_observation["repository"]
    assert "secret" not in context.render()
    for output in (context.as_dict(), resolution.as_dict()):
        assert all(output[key] is False for key in
                   ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))
        json.dumps(output)


def test_unique_unauthored_next_and_other_blockers():
    blocker = {"code": "UNAUTHORED_TASK", "task_id": "TASK-999", "message": "missing"}
    context = compose_brain_work_context(snapshot("NONE", blocker=blocker, status="UNAUTHORED_TASK"))
    result = resolve_flow(context)
    assert result.selected_flow == "TASK_AUTHORING"
    assert result.selection_basis == "UNIQUE_UNAUTHORED_NEXT"
    assert result.canonical_blocker == blocker
    ambiguous = {"code": "AMBIGUOUS_NEXT", "candidates": ["a", "b"]}
    context = compose_brain_work_context(snapshot("NONE", blocker=ambiguous, status="AMBIGUOUS_NEXT"))
    assert resolve_flow(context).selected_flow == "NONE"
    assert resolve_flow(context, cards_path=None).canonical_blocker == ambiguous


def test_human_text_does_not_infer_a_flow():
    context = compose_brain_work_context(snapshot(), {"human_input": "please review and repair"})
    assert resolve_flow(context).selected_flow == "NONE"
    assert resolve_flow(context).requires_fresh_context_for_continuation is False


@pytest.mark.parametrize("selector", ["ARCHITECTURE", "TASK_AUTHORING", "DIAGNOSTIC"])
def test_explicit_side_flow_preserves_pending_obligation(selector):
    blocker = {"code": "CANONICAL_BLOCKER", "message": "preserve exactly"}
    source = snapshot("SEMANTIC_REVIEW", blocker=blocker)
    context = compose_brain_work_context(source, {"flow_selector": selector, "human_input": "current priority"})
    result = resolve_flow(context)
    assert result.selected_flow == selector
    assert result.authority_owner == "HUMAN_BRAIN"
    assert result.pending_canonical_obligation == "SEMANTIC_REVIEW"
    assert result.pending_canonical_authority_owner == "REVIEWER"
    assert result.canonical_blocker == blocker
    assert result.requires_fresh_context_for_continuation is True
    assert context.current_request == {"flow_selector": selector, "human_input": "current priority"}
    assert context.canonical_observation["blocker"] == blocker
    assert context.canonical_observation["unified_state"] == source.as_dict()["unified_state"]


def test_diagnostic_on_ambiguous_state():
    blocker = {"code": "AMBIGUOUS_NEXT", "candidates": ["a", "b"]}
    context = compose_brain_work_context(snapshot("NONE", blocker=blocker, status="AMBIGUOUS_NEXT"),
                                         {"flow_selector": "DIAGNOSTIC"})
    result = resolve_flow(context)
    assert result.selected_flow == "DIAGNOSTIC"
    assert result.canonical_blocker == blocker
    assert result.pending_canonical_obligation is None


def test_diagnostic_preserves_brain_sync_conflict_and_unified_projection():
    blocker = {"code": "ROADMAP_LIFECYCLE_CONFLICT", "task_id": "TASK-177"}
    source = replace(snapshot("DONE"), selection_status="ROADMAP_LIFECYCLE_CONFLICT",
                     next_action="NONE", blocker=blocker)
    context = compose_brain_work_context(source, {"flow_selector": "DIAGNOSTIC"})
    result = resolve_flow(context)
    assert result.selected_flow == "DIAGNOSTIC"
    assert result.canonical_blocker == blocker
    assert result.canonical_next_action == "NONE"
    assert result.unified_state_next_action == "DONE"


@pytest.mark.parametrize("request", [
    {}, {"flow_selector": "SEMANTIC_REVIEW"}, {"flow_selector": ""},
    {"flow_selector": None}, {"human_input": 1}, {"human_input": "x", "extra": 1},
    {"human_input": "\ud800"}, {"human_input": "🙂" * 4097},
])
def test_current_request_fails_closed(request):
    with pytest.raises(BrainContextError):
        compose_brain_work_context(snapshot(), request)


def test_utf8_bound_and_invalidation():
    source = snapshot("AUTHOR_REPAIR")
    valid = {"human_input": "🙂" * 4096}
    first = compose_brain_work_context(source, valid)
    assert len(first.current_request["human_input"].encode("utf-8")) == 16384
    assert first.invalidation_fingerprint == compose_brain_work_context(source, valid).invalidation_fingerprint
    variants = [
        compose_brain_work_context(source, {"human_input": "changed"}),
        compose_brain_work_context(replace(source, main_sha="b" * 40), valid),
        compose_brain_work_context(replace(source, roadmap={"next_items": ["other"]}), valid),
        compose_brain_work_context(replace(source, selected_task={"id": "TASK-177", "revision": 3}), valid),
        compose_brain_work_context(snapshot("SEMANTIC_REVIEW"), valid),
        compose_brain_work_context(replace(source, blocker={"code": "BLOCKED"}), valid),
    ]
    assert len({first.invalidation_fingerprint, *(item.invalidation_fingerprint for item in variants)}) == 7
    assert resolve_flow(first).invalidation_fingerprint == first.invalidation_fingerprint


def test_altered_context_fails_closed():
    context = compose_brain_work_context(snapshot())
    context.canonical_observation["roadmap"]["next_items"].append("another")
    with pytest.raises(BrainContextError):
        resolve_flow(context)


def test_composition_and_resolution_have_no_mutating_call_path(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected mutation or dispatch")

    monkeypatch.setattr("aios_renew.brain_context.observe_brain_sync", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    context = compose_brain_work_context(snapshot("SEMANTIC_REVIEW"))
    assert resolve_flow(context).selected_flow == "SEMANTIC_REVIEW"


def test_registry_exact_six_and_contracts():
    cards = load_flow_cards()
    assert set(cards) == {"ARCHITECTURE", "TASK_AUTHORING", "SEMANTIC_REVIEW",
                          "REMEDIATION_AUTHORING", "REPAIR_AUTHORING", "DIAGNOSTIC"}
    assert cards["SEMANTIC_REVIEW"]["authority_owner"] == "REVIEWER"
    assert cards["SEMANTIC_REVIEW"]["decision_family_ref"] == "review.validate_review"
    assert cards["REPAIR_AUTHORING"]["decision_family_ref"] == "publication._validate_repair_authorization"


@pytest.mark.parametrize("mutation", [
    lambda data: data["cards"].pop(),
    lambda data: data["cards"].__setitem__(5, dict(data["cards"][0])),
    lambda data: data["cards"][0].__setitem__("id", "EXECUTE_PRIMARY"),
    lambda data: data["cards"][0].pop("required_context"),
    lambda data: data["cards"][0].__setitem__("authority_owner", "REVIEWER"),
    lambda data: data["cards"][2].__setitem__("decision_family_ref", "PASS_OR_FAIL"),
    lambda data: data["cards"][2].__setitem__("verdicts", ["PASS", "BLOCKED"]),
    lambda data: data["cards"][0].__setitem__("handoff_target", "EXECUTE_ANYTHING"),
    lambda data: data["cards"][0]["entry_conditions"].append("EXECUTE_PRIMARY"),
    lambda data: data["cards"][0]["required_context"].pop(),
])
def test_registry_rejects_malformed_cards(tmp_path: Path, mutation):
    registry = {"format": "AIOS_FLOW_CARDS", "version": 1,
                "cards": list(load_flow_cards().values())}
    mutation(registry)
    path = tmp_path / "cards.yaml"
    path.write_text(yaml.safe_dump(registry), encoding="utf-8")
    with pytest.raises(BrainContextError):
        load_flow_cards(path)


def test_registry_rejects_duplicate_yaml_keys(tmp_path: Path):
    path = tmp_path / "cards.yaml"
    path.write_text("format: AIOS_FLOW_CARDS\nformat: AIOS_FLOW_CARDS\n", encoding="utf-8")
    with pytest.raises(BrainContextError):
        load_flow_cards(path)
