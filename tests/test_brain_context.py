"""Offline BP-3 contracts over bounded Brain Sync observations."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from aios_renew.brain_context import (
    BrainContextError,
    ContextBudgetError,
    DerivedContextCache,
    ExactExpansionRequired,
    compose_brain_work_context,
    expand_exact_context,
    load_flow_cards,
    require_exact_context,
    resolve_flow,
)
from aios_renew.brain_sync import BrainSyncSnapshot, project_active_planning


def planning(item_id="bp-3", *, authored=True, ambiguous=False, revision=2):
    row = {"id": item_id, "status": "NEXT"}
    if authored:
        row.update(task_id="TASK-177", task_revision=revision)
    rows = [row, {"id": "other", "status": "NEXT"}] if ambiguous else [row]
    return project_active_planning({"version": 1, "active_track": "brain", "active_track_status": "ACTIVE",
                                    "next_items": [item["id"] for item in rows], "sequence": rows})


def snapshot(action="EXECUTE_PRIMARY", *, blocker=None, status="SELECTED", roadmap=None):
    unified = None if status != "SELECTED" else {
        "format": "AIOS_UNIFIED_STATE", "version": 1,
        "task": {"id": "TASK-177", "revision": 2},
        "next_action": action, "run_id": "RUN-177-001",
        "finding_id": "FINDING-177-001", "blocker": blocker,
    }
    return BrainSyncSnapshot(
        repository={"root": str(Path(__file__).resolve().parents[1]),
                    "name": "AIOS-renew", "main_sha": "a" * 40,
                    "remote": "origin", "remote_url": "https://secret@example.test/repo"},
        main_sha="a" * 40,
        roadmap=roadmap or planning(authored=status == "SELECTED", ambiguous=status == "AMBIGUOUS_NEXT"),
        selection_status=status, lifecycle_state="READY" if blocker is None else "BLOCKED",
        next_action=action, authority="NONE" if status == "AMBIGUOUS_NEXT" else
        "REVIEWER" if action == "SEMANTIC_REVIEW" else "BRAIN",
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
    context = compose_brain_work_context(snapshot(), {"human_input": "please research, review and repair"})
    assert resolve_flow(context).selected_flow == "NONE"
    assert resolve_flow(context).requires_fresh_context_for_continuation is False


@pytest.mark.parametrize("selector", ["ARCHITECTURE", "TASK_AUTHORING", "DIAGNOSTIC", "RESEARCH"])
def test_explicit_side_flow_preserves_pending_obligation(selector):
    blocker = {"code": "CANONICAL_BLOCKER", "message": "preserve exactly"}
    source = snapshot("SEMANTIC_REVIEW", blocker=blocker)
    context = compose_brain_work_context(source, {"flow_selector": selector, "human_input": "current priority"})
    result = resolve_flow(context)
    assert result.selected_flow == selector
    assert result.authority_owner == "BRAIN"
    assert result.card["authority_owner"] == "BRAIN"
    assert result.pending_canonical_obligation == "SEMANTIC_REVIEW"
    assert result.pending_canonical_authority_owner == "REVIEWER"
    assert result.canonical_next_action == "SEMANTIC_REVIEW"
    assert result.unified_state_next_action == "SEMANTIC_REVIEW"
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


@pytest.mark.parametrize("invalid_request", [
    {}, {"flow_selector": "SEMANTIC_REVIEW"}, {"flow_selector": ""},
    {"flow_selector": None}, {"human_input": 1}, {"human_input": "x", "extra": 1},
    {"human_input": "\ud800"}, {"human_input": "🙂" * 4097},
])
def test_current_request_fails_closed(invalid_request):
    with pytest.raises(BrainContextError):
        compose_brain_work_context(snapshot(), invalid_request)


def test_utf8_bound_and_invalidation():
    source = snapshot("AUTHOR_REPAIR")
    valid = {"human_input": "🙂" * 4096}
    first = compose_brain_work_context(source, valid)
    assert len(first.current_request["human_input"].encode("utf-8")) == 16384
    assert first.invalidation_fingerprint == compose_brain_work_context(source, valid).invalidation_fingerprint
    variants = [
        compose_brain_work_context(source, {"human_input": "changed"}),
        compose_brain_work_context(replace(source, main_sha="b" * 40, repository={**source.repository, "main_sha": "b" * 40}), valid),
        compose_brain_work_context(replace(source, roadmap=planning("other")), valid),
        compose_brain_work_context(replace(source, selected_task={"id": "TASK-177", "revision": 3},
            roadmap=planning(revision=3),
            unified_state={**source.unified_state, "task": {"id": "TASK-177", "revision": 3}}), valid),
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


def test_checkout_root_and_remote_alias_are_not_semantic_identity(tmp_path):
    source = snapshot("AUTHOR_REPAIR")
    human_request = {"flow_selector": "DIAGNOSTIC", "human_input": "inspect"}
    first = compose_brain_work_context(source, human_request)
    registry = tmp_path / ".ai" / "flow-cards.yaml"
    registry.parent.mkdir()
    registry.write_bytes((Path(source.repository["root"]) / ".ai" / "flow-cards.yaml").read_bytes())
    other = replace(source, repository={**source.repository, "root": str(tmp_path),
                                        "remote": "upstream", "remote_url": "https://other.example/repo"})
    second = compose_brain_work_context(other, human_request)
    assert first.invalidation_basis == second.invalidation_basis
    assert first.invalidation_fingerprint == second.invalidation_fingerprint
    assert first.canonical_observation["repository"] != second.canonical_observation["repository"]


def test_resolver_uses_observed_repository_registry(tmp_path: Path):
    source = snapshot("SEMANTIC_REVIEW")
    repo = tmp_path / "checkout"
    registry = repo / ".ai" / "flow-cards.yaml"
    registry.parent.mkdir(parents=True)
    source_cards = Path(__file__).resolve().parents[1] / ".ai" / "flow-cards.yaml"
    registry.write_bytes(source_cards.read_bytes())
    context = compose_brain_work_context(replace(source, repository={**source.repository, "root": str(repo)}))
    assert resolve_flow(context).selected_flow == "SEMANTIC_REVIEW"
    assert resolve_flow(context, cards_path=registry).selected_flow == "SEMANTIC_REVIEW"
    unrelated = tmp_path / "unrelated.yaml"
    unrelated.write_text(registry.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(BrainContextError, match="unrelated"):
        resolve_flow(context, cards_path=unrelated)
    registry.unlink()
    with pytest.raises(BrainContextError, match="cannot load"):
        resolve_flow(context)


def test_altered_operational_root_cannot_redirect_registry(tmp_path: Path):
    context = compose_brain_work_context(snapshot())
    context.canonical_observation["repository"]["root"] = str(tmp_path)
    with pytest.raises(BrainContextError, match="altered"):
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


def test_registry_exact_seven_and_contracts():
    cards = load_flow_cards()
    assert set(cards) == {"ARCHITECTURE", "TASK_AUTHORING", "SEMANTIC_REVIEW",
                          "REMEDIATION_AUTHORING", "REPAIR_AUTHORING", "DIAGNOSTIC", "RESEARCH"}
    assert cards["RESEARCH"]["entry_conditions"] == ["EXPLICIT_SELECTOR"]
    assert cards["RESEARCH"]["decision_family_ref"] == "HUMAN_BRAIN_RESEARCH_ASSURANCE"
    assert cards["RESEARCH"]["handoff_target"] == "RESEARCH_PROTOCOL"
    assert cards["RESEARCH"]["expected_return_shape"] == "RESEARCH_PACKET"
    assert cards["SEMANTIC_REVIEW"]["authority_owner"] == "REVIEWER"
    assert cards["SEMANTIC_REVIEW"]["decision_family_ref"] == "review.validate_review"
    assert cards["REPAIR_AUTHORING"]["decision_family_ref"] == "publication._validate_repair_authorization"
    assert cards["REPAIR_AUTHORING"]["entry_conditions"] == [
        "UNIFIED_STATE_AUTHOR_REPAIR", "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION"]


@pytest.mark.parametrize("action", ["AUTHOR_REPAIR", "AUTHOR_REMEDIATION", "SEMANTIC_REVIEW",
                                    "EXECUTE_PRIMARY", "EXECUTE_REMEDIATION", "WAIT", "DONE", "NONE"])
def test_explicit_repair_is_not_a_generic_override(action):
    with pytest.raises(BrainContextError, match="supersession"):
        compose_brain_work_context(snapshot(action), {"flow_selector": "REPAIR_AUTHORING"})


def test_explicit_repair_requires_canonical_material_even_in_execute_state():
    with pytest.raises(BrainContextError, match="supersession"):
        compose_brain_work_context(snapshot("EXECUTE_REPAIR"), {"flow_selector": "REPAIR_AUTHORING"})


@pytest.mark.parametrize("fault", ["main", "lifecycle", "authorization", "failure", "competing", "executed"])
def test_explicit_repair_rejects_changed_canonical_authority_before_material_compilation(monkeypatch, fault):
    from types import SimpleNamespace
    from aios_renew import authoring_ingress as ingress
    from aios_renew.review_transport import ReviewTransportError

    source = snapshot("EXECUTE_REPAIR")
    source = replace(source, unified_state={**source.unified_state,
                     "failed_run_id": "RUN-177-001", "failed_head_sha": "b" * 40,
                     "correction_sha": "c" * 40})
    repair_ref = "refs/heads/aios/repair/RUN-177-001"
    failure_ref = "refs/heads/aios/failure-artifacts/RUN-177-001"
    refs = {"refs/heads/main": source.main_sha, repair_ref: "c" * 40, failure_ref: "d" * 40}
    if fault == "main":
        refs["refs/heads/main"] = "e" * 40
    if fault == "failure":
        refs.pop(failure_ref)
    monkeypatch.setattr(ingress, "_authoring_refs", lambda repo: ("origin", refs))
    monkeypatch.setattr(ingress, "_prove_authoring_inputs", lambda *args: None)
    fresh = replace(source, next_action="WAIT") if fault == "lifecycle" else source
    monkeypatch.setattr("aios_renew.brain_sync.observe_brain_sync", lambda **kwargs: fresh)

    def authorization(*args, **kwargs):
        if fault == "competing":
            raise ReviewTransportError("competing current authorization")
        return SimpleNamespace(commit_sha="e" * 40 if fault == "authorization" else "c" * 40,
                               ref=repair_ref)

    monkeypatch.setattr(ingress, "resolve_remote_repair_authorization", authorization)

    def continuation(*args):
        if fault == "executed":
            raise ingress.AuthoringIngressError("canonical continuation already exists")

    monkeypatch.setattr(ingress, "_check_continuation_does_not_exist", continuation)
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid authority reached semantic material or mutation")
    monkeypatch.setattr(ingress, "_authoring_blob", forbidden)
    monkeypatch.setattr(ingress, "_commit_tree", forbidden)
    monkeypatch.setattr(ingress, "_publish_ingress_ref", forbidden)
    with pytest.raises(BrainContextError, match="supersession"):
        compose_brain_work_context(source, {"flow_selector": "REPAIR_AUTHORING"})


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
                "context_pipeline": yaml.safe_load((Path(__file__).resolve().parents[1] / ".ai" / "flow-cards.yaml").read_text(encoding="utf-8"))["context_pipeline"],
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


def material_snapshot(body="current task background"):
    source = snapshot("SEMANTIC_REVIEW")
    contract = {"task_id": "TASK-177", "revision": 2, "goal": "exact Human intent",
                "acceptance": [{"id": "AC1", "condition": "exact decisive requirement"}],
                "scope": {"modify": ["permitted.py"]}, "constraints": {"hard": ["retain authority"]},
                "human_priority": "exact priority", "risk_acceptance": {"authority": "HUMAN", "risk": "exact"},
                "executor_delegation": {"task_id": "TASK-177", "revision": 2, "executor": "codex"}}
    return replace(source, task_contract=contract, supporting_context={"task_problem": body}, source_bindings={
        "roadmap": {"commit": source.main_sha, "path": ".ai/roadmap-state.yaml", "source_digest": "1" * 64},
        "task": {"commit": source.main_sha, "path": ".ai/tasks/TASK-177.yaml", "source_digest": "2" * 64},
    }, unified_state={**source.unified_state, "failed_run_id": "RUN-177-000", "failed_head_sha": "b" * 40,
                      "correction_sha": "c" * 40, "review_id": "REVIEW-177-001", "reviewed_sha": "d" * 40,
                      "execution_base": {"run_id": "RUN-177-000", "candidate_sha": "b" * 40}})


def overflow_case():
    small = material_snapshot("tiny")
    floor = compose_brain_work_context(small).compilation["budget"]["compiled_bytes"]
    source = material_snapshot("background detail " * 20000)
    context = compose_brain_work_context(source, budget_bytes=floor + 2048)
    return source, context


def test_context_pipeline_order_closed_inputs_and_exact_fit():
    source = material_snapshot()
    context = compose_brain_work_context(source, {"human_input": "research words are just text"})
    pipeline = context.compilation
    assert pipeline["id"] == "CANONICAL_CONTEXT_PIPELINE_V1"
    assert pipeline["order"] == ["FRESH_CANONICAL_ANCHORS", "DETERMINISTIC_RELEVANCE_PROJECTION", "RULE_BASED_ELISION",
                                 "EXACT_DIGEST_BOUND_REUSE", "DETERMINISTIC_CONTEXT_BUDGET",
                                 "BOUNDED_SUMMARIZATION_ONLY_IF_STILL_OVER_BUDGET", "EXACT_EXPANSION_ON_DECISION_DEPENDENCY",
                                 "BRAIN_REASONING"]
    assert pipeline["projection_inputs"]["selected_flow"] == "SEMANTIC_REVIEW"
    assert pipeline["projection_inputs"]["request_class"] == "CONTINUATION"
    assert pipeline["projection_inputs"]["subject"] == source.selected_task
    assert pipeline["budget"]["status"] == "EXACT" and pipeline["overflow"] == {}
    assert context.canonical_observation["task_contract"] == source.task_contract
    assert context.canonical_observation["supporting_context"] == source.supporting_context
    assert pipeline["summary_authority"] == pipeline["reuse_authority"] == "NONE"


def test_unique_unauthored_next_compiles_to_task_authoring_without_engineering_subject():
    source = snapshot("TASK_AUTHORING", status="UNAUTHORED_TASK")
    source = replace(source, authority="HUMAN_BRAIN_PLANNING", lifecycle_state="PLANNING")
    context = compose_brain_work_context(source)
    resolution = resolve_flow(context)
    assert resolution.selected_flow == "TASK_AUTHORING" and resolution.selection_basis == "UNIQUE_UNAUTHORED_NEXT"
    assert resolution.canonical_next_action == "TASK_AUTHORING"
    assert context.canonical_observation["selected_task"] is None
    assert context.canonical_observation["unified_state"] is None
    assert context.compilation["projection_inputs"]["subject_type"] == "PLANNING"
    assert context.compilation["projection_inputs"]["subject"] == "bp-3"


def test_rule_elision_is_provenanced_and_expansion_gated():
    source = replace(material_snapshot(), next_action="EXECUTE_PRIMARY", unified_state={
        **material_snapshot().unified_state, "next_action": "EXECUTE_PRIMARY"})
    context = compose_brain_work_context(source)
    selector = "canonical_observation.supporting_context.task_problem"
    assert context.canonical_observation["supporting_context"] is None
    assert context.compilation["material_index"][selector]["representation"] == "ELIDED"
    entry = next(entry for entry in context.compilation["elision_manifest"] if entry["selector"] == selector)
    assert entry["class"] == "NONSELECTED_FLOW_DETAIL" and entry["rule"] == "OMIT_NONSELECTED_TASK_PROBLEM_V1"
    assert entry["rule_version"] == "RULE_BASED_CONTEXT_ELISION_V1" and len(entry["source_digest"]) == 64
    with pytest.raises(ExactExpansionRequired):
        require_exact_context(context, [selector])
    with pytest.raises(ExactExpansionRequired):
        require_exact_context(context, ["canonical_observation.supporting_context"])
    expansion = expand_exact_context(context, [selector], fresh_snapshot=source)
    assert require_exact_context(context, [selector], expansion=expansion, fresh_snapshot=source)[selector] == source.supporting_context["task_problem"]


def test_digest_cache_deletion_changes_cost_only_and_poisoning_reconstructs_exact():
    source = material_snapshot()
    cache = DerivedContextCache()
    first = compose_brain_work_context(source, reuse_cache=cache)
    second = compose_brain_work_context(source, reuse_cache=cache)
    assert cache.hits == 1 and cache.misses == 1
    assert first.as_dict() == second.as_dict()
    entry = next(iter(cache._entries.values()))
    entry[1]["task_problem"] = "poisoned cache has no authority"
    reconstructed = compose_brain_work_context(source, reuse_cache=cache)
    assert reconstructed.as_dict() == first.as_dict()
    assert cache.misses == 2
    cache.clear()
    after_deletion = compose_brain_work_context(source, reuse_cache=cache)
    assert after_deletion.as_dict() == first.as_dict()
    assert compose_brain_work_context(source).as_dict() == first.as_dict()
    assert cache.misses == 3


@pytest.mark.parametrize("movement", ["main", "source_digest", "repository", "roadmap", "request", "request_class", "scope", "lifecycle"])
def test_required_binding_movement_invalidates_exact_reuse(movement):
    source = material_snapshot()
    cache = DerivedContextCache()
    first = compose_brain_work_context(source, reuse_cache=cache)
    request = None
    if movement == "main":
        source = replace(source, main_sha="b" * 40, repository={**source.repository, "main_sha": "b" * 40},
                         source_bindings={key: {**value, "commit": "b" * 40} for key, value in source.source_bindings.items()})
    elif movement == "source_digest":
        source = replace(source, source_bindings={**source.source_bindings, "task": {
            **source.source_bindings["task"], "source_digest": "3" * 64}})
    elif movement == "repository":
        source = replace(source, repository={**source.repository, "name": "other/repository"})
    elif movement == "roadmap":
        source = replace(source, roadmap=planning("new-planning-subject"))
    elif movement == "request":
        request = {"human_input": "current Human priority changed"}
    elif movement == "request_class":
        request = {"request_class": "REVIEW"}
    elif movement == "scope":
        source = replace(source, task_contract={**source.task_contract, "scope": {"modify": ["other.py"]}})
    elif movement == "lifecycle":
        source = replace(source, next_action="AUTHOR_REMEDIATION", unified_state={
            **source.unified_state, "next_action": "AUTHOR_REMEDIATION"})
    moved = compose_brain_work_context(source, request, reuse_cache=cache)
    assert cache.misses == 2 and cache.hits == 0
    assert moved.invalidation_fingerprint != first.invalidation_fingerprint
    assert moved.as_dict() == compose_brain_work_context(source, request).as_dict()


@pytest.mark.parametrize("field", ["source_identity", "source_digest", "structural_selector", "flow_card_digest",
                                    "flow_card_version", "projection_rule_version", "context_pipeline_version"])
def test_cache_binds_all_versioned_source_and_projection_fields(field):
    context = compose_brain_work_context(material_snapshot())
    binding = context.compilation["bindings"]
    cache = DerivedContextCache()
    value = {"task_problem": "exact body"}
    assert cache.exact(binding, value) == value
    assert cache.exact({**binding, field: "moved"}, value) == value
    assert cache.misses == 2 and cache.hits == 0


def test_exact_reuse_requires_complete_current_bindings():
    source = material_snapshot()
    with pytest.raises(BrainContextError, match="complete"):
        compose_brain_work_context(replace(source, source_bindings={"roadmap": source.source_bindings["roadmap"]}),
                                   reuse_cache=DerivedContextCache())
    with pytest.raises(BrainContextError, match="moved"):
        compose_brain_work_context(replace(source, source_bindings={**source.source_bindings, "task": {
            **source.source_bindings["task"], "commit": "b" * 40}}))


def test_overflow_preserves_every_control_fact_and_requires_exact_decision_expansion():
    source, context = overflow_case()
    pipeline = context.compilation
    assert pipeline["budget"]["status"] == "OVERFLOW_SUMMARIZED"
    assert pipeline["budget"]["exact_projected_bytes"] > pipeline["budget"]["limit_bytes"]
    assert pipeline["budget"]["compiled_bytes"] <= pipeline["budget"]["limit_bytes"]
    for field in ("task_contract", "unified_state", "selected_task", "roadmap", "blocker", "authority", "next_action"):
        assert context.canonical_observation[field] == source.as_dict()[field]
    selector = "canonical_observation.supporting_context.task_problem"
    summary = pipeline["overflow"][selector]
    assert len(summary["summary"].encode("utf-8")) <= 256 and summary["canonical_authority"] == "NONE"
    with pytest.raises(ExactExpansionRequired):
        require_exact_context(context, [selector])
    expansion = expand_exact_context(context, [selector], fresh_snapshot=source, max_bytes=1048576)
    assert expansion.values[selector] == source.supporting_context["task_problem"]
    # Rebinding also needs an explicit bounded expansion envelope for this large dependency.
    with pytest.raises(ContextBudgetError):
        require_exact_context(context, [selector], expansion=expansion, fresh_snapshot=source)
    assert require_exact_context(context, [selector], expansion=expansion, fresh_snapshot=source, max_bytes=1048576)[selector] == source.supporting_context["task_problem"]
    assert require_exact_context(context, ["canonical_observation.task_contract.scope"], fresh_snapshot=source) == {
        "canonical_observation.task_contract.scope": source.task_contract["scope"]}
    assert resolve_flow(context).selected_flow == "SEMANTIC_REVIEW"


def test_control_floor_fails_closed_instead_of_summarizing_human_intent():
    source = snapshot()
    before = source.as_dict()
    with pytest.raises(ContextBudgetError, match="non-elidable"):
        compose_brain_work_context(source, {"human_input": "x" * 16384}, budget_bytes=8192)
    assert source.as_dict() == before


def test_expansion_checks_current_binding_and_detects_altered_material():
    source = material_snapshot("bounded exact source " * 200)
    baseline = compose_brain_work_context(material_snapshot("tiny")).compilation["budget"]["compiled_bytes"]
    context = compose_brain_work_context(source, budget_bytes=baseline + 2048)
    selector = "canonical_observation.supporting_context.task_problem"
    expansion = expand_exact_context(context, [selector], fresh_snapshot=source)
    assert require_exact_context(context, [selector], expansion=expansion, fresh_snapshot=source)[selector] == source.supporting_context["task_problem"]
    moved = replace(source, source_bindings={**source.source_bindings, "task": {**source.source_bindings["task"], "source_digest": "3" * 64}})
    with pytest.raises(BrainContextError, match="moved"):
        expand_exact_context(context, [selector], fresh_snapshot=moved)
    expansion.values[selector] = "tampered"
    with pytest.raises(BrainContextError, match="altered"):
        require_exact_context(context, [selector], expansion=expansion, fresh_snapshot=source)


def test_registry_movement_invalidates_resolution_and_reuse(tmp_path):
    source = material_snapshot()
    registry = tmp_path / ".ai" / "flow-cards.yaml"
    registry.parent.mkdir()
    registry.write_bytes((Path(source.repository["root"]) / ".ai" / "flow-cards.yaml").read_bytes())
    source = replace(source, repository={**source.repository, "root": str(tmp_path)})
    cache = DerivedContextCache()
    first = compose_brain_work_context(source, reuse_cache=cache)
    registry.write_bytes(registry.read_bytes() + b"\n# exact registry source movement\n")
    with pytest.raises(BrainContextError, match="binding moved"):
        resolve_flow(first)
    second = compose_brain_work_context(source, reuse_cache=cache)
    assert first.invalidation_fingerprint != second.invalidation_fingerprint
    assert cache.misses == 2 and cache.hits == 0


@pytest.mark.parametrize("request_value", [{"request_class": "LLM_RANKED"}, {"request_class": []},
                                     {"flow_selector": "RESEARCH", "relevance": "looks useful"}])
def test_relevance_request_inputs_are_closed(request_value):
    with pytest.raises(BrainContextError):
        compose_brain_work_context(snapshot(), request_value)


@pytest.mark.parametrize("fault", ["lifecycle", "subject", "support_class", "proof", "mirror", "missing_proof"])
def test_malformed_or_competing_relevance_anchors_fail_closed(fault):
    source = material_snapshot()
    if fault == "lifecycle":
        source = replace(source, unified_state={**source.unified_state, "next_action": []})
    elif fault == "subject":
        source = replace(source, selected_task={"id": "TASK-178", "revision": 2})
    elif fault == "support_class":
        source = replace(source, supporting_context={"authority": "looks like supporting text"})
    elif fault == "proof":
        source = replace(source, roadmap={**source.roadmap, "effective_next": {"id": "other", "status": "NEXT"}})
    elif fault == "mirror":
        source = replace(source, roadmap={**source.roadmap, "next_items": ["other"]})
    elif fault == "missing_proof":
        source = replace(source, roadmap={"present": True, "next_items": ["unproven"]})
    with pytest.raises(BrainContextError):
        compose_brain_work_context(source)


def test_semantic_looking_optional_rule_cannot_elide_material(tmp_path):
    registry = yaml.safe_load((Path(__file__).resolve().parents[1] / ".ai" / "flow-cards.yaml").read_text(encoding="utf-8"))
    registry["cards"][0]["optional_context_rules"]["canonical_observation.supporting_context"] = "MODEL_DEEMS_IRRELEVANT"
    path = tmp_path / "cards.yaml"
    path.write_text(yaml.safe_dump(registry), encoding="utf-8")
    with pytest.raises(BrainContextError, match="relevance rule"):
        load_flow_cards(path)


def test_historical_expansion_is_explicit_bounded_and_current_source_rebound(monkeypatch):
    raw = {"sequence": [{"id": "old", "status": "DONE", "details": "bounded exact history"},
                        {"id": "bp-3", "status": "NEXT", "task_id": "TASK-177", "task_revision": 2}]}
    text = yaml.safe_dump(raw)
    source = material_snapshot()
    source = replace(source, roadmap=project_active_planning(raw), source_bindings={**source.source_bindings,
        "roadmap": {**source.source_bindings["roadmap"], "source_digest": hashlib.sha256(text.encode("utf-8")).hexdigest()}})
    reads = []
    def read(root, commit, path):
        reads.append((root, commit, path))
        return text
    monkeypatch.setattr("aios_renew.brain_context._read_main_source", read)
    context = compose_brain_work_context(source)
    assert reads == [] and "sequence" not in context.canonical_observation["roadmap"]
    selector = "canonical_observation.roadmap.sequence[id=old]"
    with pytest.raises(ExactExpansionRequired):
        require_exact_context(context, [selector])
    expansion = expand_exact_context(context, [selector], fresh_snapshot=source)
    assert expansion.values[selector] == raw["sequence"][0] and len(reads) == 1
    assert require_exact_context(context, [selector], expansion=expansion, fresh_snapshot=source)[selector] == raw["sequence"][0]
    monkeypatch.setattr("aios_renew.brain_context._read_main_source", lambda *args: text + "# moved source\n")
    with pytest.raises(BrainContextError, match="digest moved"):
        expand_exact_context(context, [selector], fresh_snapshot=source)
