"""BP-4 contracts over already-selected, offline BP-3 observations."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from aios_renew.artifacts import (Claim, Evidence, EvidenceOutcome, EvidenceSource, Result)
from aios_renew.brain_context import compose_brain_work_context, resolve_flow
from aios_renew.brain_sync import BrainSyncSnapshot
from aios_renew.decision_packet import (
    ArchitecturePayload, DecisionPacketError, DiagnosticPayload, PlanningReference,
    PriorRemediationLineage, PriorRepairLineage, RemediationAuthoringPayload,
    RepairAuthoringPayload, SemanticReviewPayload,
    TaskAuthoringPayload, compile_decision_packet,
)
from aios_renew.review import Finding, Remediation, Review
from aios_renew.run import Run, RunTaskReference
from aios_renew.task import validate_task


ROOT = str(Path(__file__).resolve().parents[1])
SHA = "a" * 40
HEAD = "b" * 40


def snapshot(action="SEMANTIC_REVIEW", *, root=ROOT, remote="origin", blocker=None):
    return BrainSyncSnapshot(
        repository={"root": root, "name": "AIOS-renew", "main_sha": SHA,
                    "remote": remote}, main_sha=SHA,
        roadmap={"present": True, "next_items": ["bp-4"]},
        selection_status="SELECTED", lifecycle_state="READY", next_action=action,
        authority="REVIEWER" if action == "SEMANTIC_REVIEW" else "BRAIN",
        selected_task={"id": "TASK-1", "revision": 1},
        unified_state={"task": {"id": "TASK-1", "revision": 1},
                       "next_action": action, "run_id": "RUN-1",
                       "finding_id": "F-1",
                       "failed_run_id": "RUN-1" if action == "AUTHOR_REPAIR" else None},
        blocker=blocker,
    )


def materials():
    task = validate_task({
        "task_id": "TASK-1", "revision": 1, "goal": "Implement feature",
        "problem": "Missing feature", "assumptions": [],
        "scope": {"inspect": [], "modify": ["src/example.py"]}, "non_goals": [],
        "constraints": {"hard": []},
        "acceptance": [{"id": "AC1", "condition": "Works"}],
        "verification": {"required": ["check"]},
    })
    run = Run("RUN-1", RunTaskReference("TASK-1", 1), "codex", SHA,
              "C:\\local\\checkout", HEAD)
    result = Result(HEAD, (Claim("C1", ("AC1",), "Implemented", ("E1",)),),
                    ("src/example.py",), ())
    evidence = (Evidence("E1", "RUN-1", HEAD, "TEST", EvidenceSource("check"),
                         EvidenceOutcome(0, "passed"), "C:\\local\\raw.log"),)
    finding = Finding("F-1", "AC1", "CODE_FIX", "src/example.py", "issue", "fix")
    review = Review("REVIEW-1", HEAD, "PRIMARY", "CHANGES_REQUIRED",
                    {"AC1": "FAIL"}, (finding,))
    return task, run, result, evidence, review


def compile_for(flow, payload, *, source=None):
    source = source or snapshot()
    context = compose_brain_work_context(source, {"flow_selector": flow} if flow in
                                         {"ARCHITECTURE", "TASK_AUTHORING", "DIAGNOSTIC"} else None)
    return compile_decision_packet(context, resolve_flow(context), payload)


def test_all_six_closed_payload_families():
    ref = (PlanningReference("design", "d" * 64, "bounded plan"),)
    task, run, result, evidence, review = materials()
    failure = {"kind": "FAILURE", "run_id": "RUN-1", "task": {"id": "TASK-1", "revision": 1},
               "executor": "codex", "base_sha": SHA, "failed_head_sha": HEAD,
               "phase": "VERIFICATION", "error": {"type": "RuntimeError", "message": "failed"},
               "candidate": {"transportable": True, "repairable": True, "dirty": False,
                             "descends_from_base": True, "changed_files": ["src/example.py"],
                             "outside_task_scope": []}}
    cases = [
        ("ARCHITECTURE", ArchitecturePayload("bp-4", "decision context", ref), snapshot()),
        ("TASK_AUTHORING", TaskAuthoringPayload("bp-4", "task context", ref), snapshot()),
        ("SEMANTIC_REVIEW", SemanticReviewPayload(task, run, result, evidence,
         SHA, HEAD, "one bounded change"), snapshot()),
        ("REMEDIATION_AUTHORING", RemediationAuthoringPayload(task, run, result,
         evidence, review, "F-1"), snapshot("AUTHOR_REMEDIATION")),
        ("REPAIR_AUTHORING", RepairAuthoringPayload(task, run, failure, HEAD,
         "failed verification"), snapshot("AUTHOR_REPAIR")),
        ("DIAGNOSTIC", DiagnosticPayload("BLOCKER", "BLOCKED", "inspect blocker"),
         snapshot(blocker={"code": "BLOCKED", "message": "blocked"})),
    ]
    for flow, payload, source in cases:
        packet = compile_for(flow, payload, source=source)
        data = json.loads(packet.render())
        assert data["selected_flow"] == flow
        assert data["packet_fingerprint"] == packet.packet_fingerprint
        assert all(data[key] is False for key in
                   ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))
        assert len(packet.render().encode()) <= 131072
        assert "C:\\local" not in packet.render()


def test_exact_binding_and_payload_discrimination():
    task, run, result, evidence, review = materials()
    payload = SemanticReviewPayload(task, run, result, evidence, SHA, HEAD, "delta")
    first = compile_for("SEMANTIC_REVIEW", payload)
    assert first.render() == compile_for("SEMANTIC_REVIEW", payload).render()
    assert first.packet_fingerprint != compile_for("SEMANTIC_REVIEW",
        replace(payload, implementation_delta="different delta")).packet_fingerprint
    for bad in [replace(payload, delta_base_sha=HEAD),
                replace(payload, delta_head_sha=SHA),
                replace(payload, evidence=(replace(evidence[0], subject_sha=SHA),)),
                replace(payload, run=replace(run, run_id="RUN-2"))]:
        with pytest.raises(DecisionPacketError):
            compile_for("SEMANTIC_REVIEW", bad)
    with pytest.raises(DecisionPacketError):
        compile_for("SEMANTIC_REVIEW", ArchitecturePayload("bp-4", "plan",
                    (PlanningReference("x", "d" * 64, "excerpt"),)))


def test_review_of_correction_binds_prior_semantic_identity():
    task, source_run, source_result, _, review = materials()
    corrected_head = "c" * 40
    run = replace(source_run, run_id="RUN-2", base_sha=HEAD, head_sha=corrected_head)
    result = replace(source_result, head_sha=corrected_head,
                     claims=(Claim("C2", ("AC1",), "Corrected", ("E2",)),))
    evidence = (Evidence("E2", "RUN-2", corrected_head, "TEST", EvidenceSource("check"),
                         EvidenceOutcome(0, "passed"), "C:\\local\\raw2.log"),)
    source = snapshot()
    source = replace(source, unified_state={**source.unified_state, "run_id": "RUN-2",
        "candidate_sha": corrected_head, "source_run_id": "RUN-1", "review_id": "REVIEW-1",
        "finding_id": "F-1"})
    remediation = Remediation("F-1", "CODE_FIX", HEAD, ("src/example.py",))
    prior = PriorRemediationLineage(source_run, source_result, review, remediation)
    payload = SemanticReviewPayload(task, run, result, evidence, HEAD, corrected_head,
                                    "correction delta", prior_remediation=prior)
    packet = compile_for("SEMANTIC_REVIEW", payload, source=source).as_dict()
    assert packet["prior_canonical_decisions"]["review_id"] == "REVIEW-1"
    with pytest.raises(DecisionPacketError):
        compile_for("SEMANTIC_REVIEW", replace(payload, prior_remediation=None), source=source)

    failure = {"kind": "FAILURE", "run_id": "RUN-1", "task": {"id": "TASK-1", "revision": 1},
               "executor": "codex", "base_sha": SHA, "failed_head_sha": HEAD,
               "phase": "VERIFICATION", "error": {"type": "RuntimeError", "message": "failed"},
               "candidate": {"transportable": True, "repairable": True, "dirty": False,
                             "descends_from_base": True, "changed_files": ["src/example.py"],
                             "outside_task_scope": []}}
    authorization = {"repair_id": "REPAIR-1", "failed_run_id": "RUN-1",
                     "failed_head_sha": HEAD, "task": {"id": "TASK-1", "revision": 1},
                     "action": "CONTINUE_IMPLEMENTATION", "modification_scope": ["src/example.py"],
                     "instructions": ["complete the change"], "constraints": []}
    repair_source = replace(source, unified_state={**source.unified_state,
        "source_run_id": None, "failed_run_id": "RUN-1", "review_id": None, "finding_id": None})
    repair_payload = replace(payload, prior_remediation=None,
                             prior_repair=PriorRepairLineage(source_run, failure, authorization))
    repaired = compile_for("SEMANTIC_REVIEW", repair_payload, source=repair_source).as_dict()
    assert repaired["prior_canonical_decisions"]["authorization"]["repair_id"] == "REPAIR-1"


def test_portability_and_size_bounds():
    ref = (PlanningReference("design", "d" * 64, "excerpt"),)
    payload = ArchitecturePayload("bp-4", "planning context", ref)
    first = compile_for("ARCHITECTURE", payload)
    context = compose_brain_work_context(snapshot(), {"flow_selector": "ARCHITECTURE"})
    resolution = resolve_flow(context)
    other_context = compose_brain_work_context(
        snapshot(root="/different/checkout", remote="upstream"),
        {"flow_selector": "ARCHITECTURE"})
    other = compile_decision_packet(other_context, resolution, payload)
    assert first.render() == other.render()
    assert compile_for("ARCHITECTURE", replace(payload, planning_excerpt="planning\ncontext")).render() == \
        compile_for("ARCHITECTURE", replace(payload, planning_excerpt="planning\r\ncontext")).render()
    reordered_card = dict(resolution.card)
    reordered_card["required_context"] = list(reversed(reordered_card["required_context"]))
    assert first.render() == compile_decision_packet(
        context, replace(resolution, card=reordered_card), payload).render()
    assert "remote" not in first.render()
    with pytest.raises(DecisionPacketError):
        compile_for("ARCHITECTURE", replace(payload, planning_excerpt="x" * 65537))
    with pytest.raises(DecisionPacketError):
        compile_for("ARCHITECTURE", replace(payload, planning_excerpt="\ud800"))
    with pytest.raises(DecisionPacketError):
        compile_for("ARCHITECTURE", replace(payload, planning_excerpt="C:\\private\\key"))


def test_altered_bp3_inputs_fail_closed():
    payload = ArchitecturePayload("bp-4", "plan",
        (PlanningReference("design", "d" * 64, "excerpt"),))
    context = compose_brain_work_context(snapshot(), {"flow_selector": "ARCHITECTURE"})
    resolution = resolve_flow(context)
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(context, replace(resolution, authority_owner="REVIEWER"), payload)
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(context, replace(resolution, selected_flow="NONE"), payload)
    context.canonical_observation["roadmap"]["next_items"].append("stale")
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(context, resolution, payload)


def test_preserves_side_flow_obligation_and_blocker():
    source = snapshot(blocker={"code": "BLOCKED", "message": "still blocked"})
    context = compose_brain_work_context(source, {"flow_selector": "DIAGNOSTIC",
                                                   "human_input": "inspect"})
    packet = compile_decision_packet(context, resolve_flow(context),
        DiagnosticPayload("BLOCKER", "BLOCKED", "inspect blocker")).as_dict()
    assert packet["authority"]["pending_canonical_obligation"] == "SEMANTIC_REVIEW"
    assert packet["authority"]["pending_canonical_authority_owner"] == "REVIEWER"
    assert packet["authority"]["requires_fresh_context_for_continuation"] is True
    assert packet["canonical_facts"]["canonical_blocker"] == source.blocker
    assert packet["current_human_input"] == "inspect"


def test_compiler_is_observation_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected I/O")
    context = compose_brain_work_context(snapshot(), {"flow_selector": "ARCHITECTURE"})
    resolution = resolve_flow(context)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    packet = compile_decision_packet(context, resolution,
        ArchitecturePayload("bp-4", "plan", (PlanningReference("design", "d" * 64, "excerpt"),)))
    assert packet.as_dict()["selected_flow"] == "ARCHITECTURE"
