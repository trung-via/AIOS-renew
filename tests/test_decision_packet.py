"""Focused BP-4 contract examples. Runtime owns canonical verification."""

from dataclasses import replace
from pathlib import Path
import copy
import shutil

import pytest

from aios_renew.brain_context import compose_brain_work_context, resolve_flow
from aios_renew.brain_sync import BrainSyncSnapshot
from aios_renew.decision_packet import DecisionPacketError, compile_decision_packet


ROOT = Path(__file__).resolve().parents[1]
A = "a" * 40
B = "b" * 40
C = "c" * 40
D = "d" * 40


def task():
    return {
        "task_id": "TASK-002", "revision": 1, "goal": "Decide one task",
        "problem": "One bounded example", "assumptions": ["Canonical material is supplied"],
        "scope": {"inspect": ["src/aios_renew/**"], "modify": ["src/aios_renew/decision_packet.py"]},
        "non_goals": ["Lifecycle mutation"], "constraints": {"hard": ["Stay bounded"]},
        "acceptance": [{"id": "AC1", "condition": "First condition"},
                       {"id": "AC2", "condition": "Second condition"}],
        "verification": {"required": ["pytest tests/test_task.py"]},
    }


def run(run_id, head=A, base=B):
    return {"run_id": run_id, "task": {"id": "TASK-002", "revision": 1},
            "executor": "codex", "base_sha": base, "workspace": "C:/private/workspace",
            "head_sha": head, "status": "COMPLETE"}


def result(head=A, evidence_id="E1"):
    return {"head_sha": head, "claims": [{"id": "C1", "satisfies": ["AC1"],
            "claim": "Implemented one surface", "evidence": [evidence_id]}],
            "changed_files": ["src/aios_renew/decision_packet.py"], "unresolved": []}


def evidence(run_id, head=A, evidence_id="E1"):
    return {"evidence_id": evidence_id, "run_id": run_id, "subject_sha": head,
            "type": "TEST", "source": {"command": "pytest tests/test_task.py"},
            "result": {"exit_code": 0, "summary": "passed"},
            "raw": {"path": "C:/private/runtime/log.txt"}}


def runtime_failure(*, phase="VERIFICATION", message="required check failed"):
    return {"kind": "FAILURE", "run_id": "RUN-002-001", "failed_head_sha": A,
            "task": {"id": "TASK-002", "revision": 1},
            "executor": "codex", "base_sha": B, "phase": phase,
            "error": {"type": "RuntimeVerificationError", "message": message,
                      "verification": [{"command": "pytest tests/test_task.py",
                                        "exit_code": 1, "summary": "failed at C:/private/log.txt"}],
                      "native_diagnostics": {"stdout": "private native stream"},
                      "executor_diagnostics": {"unresolved": ["private raw log"]}},
            "candidate": {"transportable": True, "repairable": True, "dirty": False,
                          "descends_from_base": True,
                          "changed_files": ["src/aios_renew/decision_packet.py"],
                          "outside_task_scope": []}}


def review(review_id, head, finding_id):
    return {"review_id": review_id, "reviewed_sha": head, "mode": "PRIMARY",
            "verdict": "CHANGES_REQUIRED", "acceptance": {"AC1": "FAIL", "AC2": "PASS"},
            "findings": [{"id": finding_id, "basis": "AC1", "action": "CODE_FIX",
                          "location": "src/aios_renew/decision_packet.py",
                          "issue": f"Issue {finding_id}", "expected": f"Expected {finding_id}"}]}


def item(run_id, review_id, finding_id, head=A):
    return {"source_run": run(run_id, head), "result": result(head),
            "review": review(review_id, head, finding_id), "finding_id": finding_id}


def identity(item_value):
    return {"source_run_id": item_value["source_run"]["run_id"],
            "review_id": item_value["review"]["review_id"],
            "finding_id": item_value["finding_id"],
            "reviewed_sha": item_value["review"]["reviewed_sha"]}


def context(action, unified, *, selector=None, blocker=None, root=ROOT):
    snapshot = BrainSyncSnapshot(
        repository={"root": str(root), "name": "AIOS-renew", "main_sha": A,
                    "remote": "private-origin", "remote_url": "https://secret.invalid/repo"},
        main_sha=A, roadmap={"next_items": ["bp-4"]}, selection_status="SELECTED",
        lifecycle_state="CORRECTION", next_action=action, authority="BRAIN",
        selected_task={"id": "TASK-002", "revision": 1}, unified_state=unified,
        blocker=blocker,
    )
    request = {"flow_selector": selector, "human_input": "Human priority"} if selector else None
    work = compose_brain_work_context(snapshot, request)
    return work, resolve_flow(work)


def authoring(items, *, selected=False):
    unified = {"next_action": "AUTHOR_REMEDIATION", "run_id": "RUN-002-009",
               "candidate_sha": C, "execution_base": {"run_id": "RUN-002-009", "candidate_sha": C},
               "source_run_id": None, "review_id": None, "finding_id": None,
               "reviewed_sha": None, "outstanding_findings": [identity(x) for x in items]}
    if selected:
        unified.update(identity(items[0]))
    work, flow = context("AUTHOR_REMEDIATION", unified)
    material = {"kind": "REMEDIATION_AUTHORING",
                "subject_kind": "UNIQUE_FINDING" if selected else "CORRECTION_FRONTIER",
                "task": task(), "findings": items}
    return work, flow, material


def test_frontier_exact_set_and_stable_order_without_priority():
    first = item("RUN-002-001", "REVIEW-002-001", "F1")
    second = item("RUN-002-002", "REVIEW-002-002", "F2", B)
    work, flow, material = authoring([first, second])
    packet = compile_decision_packet(work, flow, material)
    reversed_material = copy.deepcopy(material)
    reversed_material["findings"].reverse()
    assert compile_decision_packet(work, flow, reversed_material).render() == packet.render()
    body = packet.as_dict()
    assert body["format"] == "AIOS_DECISION_PACKET"
    assert body["subject"]["kind"] == "CORRECTION_FRONTIER"
    assert len(body["subject"]["findings"]) == 2
    assert body["subject"]["execution_base"] == work.canonical_observation["unified_state"]["execution_base"]
    assert "workspace" not in packet.render() and "private-origin" not in packet.render()
    assert "raw" not in body["subject"]

    for mutate in (lambda x: x["findings"].pop(),
                   lambda x: x["findings"].append(copy.deepcopy(x["findings"][0]))):
        changed = copy.deepcopy(material)
        mutate(changed)
        with pytest.raises(DecisionPacketError):
            compile_decision_packet(work, flow, changed)
    changed = copy.deepcopy(material)
    changed["findings"][0]["source_run"]["run_id"] = "RUN-002-999"
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(work, flow, changed)


def test_unique_finding_and_unselected_frontier_fail_closed():
    one = item("RUN-002-001", "REVIEW-002-001", "F1")
    work, flow, material = authoring([one], selected=True)
    packet = compile_decision_packet(work, flow, material)
    assert packet.as_dict()["subject"]["source_run_id"] == "RUN-002-001"
    changed = copy.deepcopy(material)
    changed["subject_kind"] = "CORRECTION_FRONTIER"
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(work, flow, changed)
    changed = copy.deepcopy(material)
    changed["findings"][0]["review"]["findings"][0]["issue"] = "Changed issue"
    assert compile_decision_packet(work, flow, changed).packet_fingerprint != packet.packet_fingerprint


def test_semantic_review_binds_exact_remediation_authorization_and_claim_classes():
    source = item("RUN-002-001", "REVIEW-002-001", "F1")
    correction_run = run("RUN-002-003", C, A)
    authorization = {"finding_id": "F1", "action": "CODE_FIX", "reviewed_sha": A,
                     "modification_scope": ["src/aios_renew/decision_packet.py"],
                     "affected_verification": ["pytest tests/test_task.py"], "constraints": []}
    run_doc = {"kind": "REMEDIATION", "remediation_authorization_sha": D,
               "predecessor": identity(source),
               "execution": {"review_id": "REVIEW-002-001",
                             "finding": source["review"]["findings"][0],
                             "remediation": authorization, "run": correction_run,
                             "original_constraints": []}}
    unified = {"next_action": "SEMANTIC_REVIEW", "run_id": "RUN-002-003", "candidate_sha": C}
    work, flow = context("SEMANTIC_REVIEW", unified)
    material = {"kind": "SEMANTIC_REVIEW", "task": task(), "run": run_doc,
                "result": result(C, "E2"), "evidence": [evidence("RUN-002-003", C, "E2")],
                "prior_correction": {"kind": "REMEDIATION", "authorization_sha": D,
                                     "authorization": authorization,
                                     "source_run": source["source_run"],
                                     "source_result": source["result"],
                                     "source_review": source["review"]}}
    packet = compile_decision_packet(work, flow, material)
    body = packet.as_dict()
    assert body["prior_semantic_decisions"]["authorization_sha"] == D
    assert body["executor_claims"][0]["claim"] == "Implemented one surface"
    assert body["bounded_observations"][0]["summary"] == "passed"
    assert "C:/private" not in packet.render()
    for change in (lambda m: m["prior_correction"].__setitem__("authorization_sha", B),
                   lambda m: m["run"].pop("remediation_authorization_sha"),
                   lambda m: m["run"]["predecessor"].__setitem__("finding_id", "other"),
                   lambda m: m["prior_correction"]["authorization"].__setitem__("reviewed_sha", B)):
        altered = copy.deepcopy(material)
        change(altered)
        with pytest.raises(DecisionPacketError):
            compile_decision_packet(work, flow, altered)


def test_side_flow_preserves_pending_obligation_and_blocks_nested_packet():
    unified = {"next_action": "AUTHOR_REMEDIATION", "outstanding_findings": []}
    work, flow = context("AUTHOR_REMEDIATION", unified, selector="DIAGNOSTIC",
                         blocker={"code": "WAITING", "message": "pending"})
    packet = compile_decision_packet(work, flow, {"kind": "DIAGNOSTIC", "observations": ["inspect"]})
    body = packet.as_dict()
    assert body["pending_canonical_obligation"] == "REMEDIATION_AUTHORING"
    assert body["pending_canonical_authority_owner"] == "BRAIN"
    assert body["requires_fresh_context_for_continuation"] is True
    assert body["canonical_blocker"] == {"code": "WAITING", "message": "pending"}
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(work, flow, {"kind": "DIAGNOSTIC", "packet": packet.as_dict()})
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(work, flow, {"kind": "ARCHITECTURE"})


def test_repair_authoring_and_exact_repair_provenance():
    failed_run = run("RUN-002-001", A, B)
    failure = runtime_failure()
    work, flow = context("AUTHOR_REPAIR", {"next_action": "AUTHOR_REPAIR",
                                           "failed_run_id": "RUN-002-001", "failed_head_sha": A})
    packet = compile_decision_packet(work, flow, {"kind": "REPAIR_AUTHORING",
                                                  "task": task(), "failed_run": failed_run,
                                                  "failure": failure})
    assert packet.as_dict()["subject"] == {
        "failed_run_id": "RUN-002-001", "failed_head_sha": A,
        "failed_changed_files": ["src/aios_renew/decision_packet.py"],
    }
    assert packet.as_dict()["bounded_observations"] == {
        "phase": "VERIFICATION",
        "error": {"type": "RuntimeVerificationError"},
    }
    assert "C:/private/log.txt" not in packet.render()
    assert "private raw log" not in packet.render()
    assert "private native stream" not in packet.render()

    repair_run = run("RUN-002-003", C, A)
    authorization = {"repair_id": "REPAIR-002-001", "failed_run_id": "RUN-002-001",
                     "failed_head_sha": A, "task": {"id": "TASK-002", "revision": 1},
                     "action": "CODE_FIX", "modification_scope": ["src/aios_renew/decision_packet.py"],
                     "instructions": ["Fix the failed change"], "constraints": []}
    execution = {"failed_run_id": "RUN-002-001", "root_base_sha": B,
                 "result_base_sha": B, "failed_head_sha": A,
                 "failure": failure, "task": task(), "repair": authorization,
                 "run": repair_run, "repair_authorization_sha": D}
    work, flow = context("SEMANTIC_REVIEW", {"next_action": "SEMANTIC_REVIEW",
                                               "run_id": "RUN-002-003", "candidate_sha": C,
                                               "failed_run_id": "RUN-002-001"})
    material = {"kind": "SEMANTIC_REVIEW", "task": task(), "run": repair_run,
                "result": result(C, "E2"), "evidence": [evidence("RUN-002-003", C, "E2")],
                "prior_correction": {"kind": "REPAIR", "authorization_sha": D,
                                     "authorization": authorization, "execution": execution,
                                     "failed_run": failed_run, "failure": failure}}
    compiled = compile_decision_packet(work, flow, material)
    assert compiled.as_dict()["prior_semantic_decisions"]["authorization_sha"] == D
    altered = copy.deepcopy(material)
    altered["prior_correction"]["authorization_sha"] = B
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(work, flow, altered)
    altered = copy.deepcopy(material)
    altered["prior_correction"]["authorization"] = copy.deepcopy(authorization)
    altered["prior_correction"]["authorization"]["instructions"] = ["Different valid correction"]
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(work, flow, altered)


@pytest.mark.parametrize("phase", ["VERIFICATION", "EXECUTION", "COMPLETION_GATE"])
def test_repair_authoring_projects_runtime_failure_without_diagnostics(phase):
    work, flow = context("AUTHOR_REPAIR", {"next_action": "AUTHOR_REPAIR",
                                           "failed_run_id": "RUN-002-001", "failed_head_sha": A})
    failure = runtime_failure(phase=phase, message="failed at C:/private/log.txt token=private")
    failure["error"]["type"] = {
        "VERIFICATION": "RuntimeVerificationError",
        "EXECUTION": "CodexExecutionError",
        "COMPLETION_GATE": "RuntimeError",
    }[phase]
    packet = compile_decision_packet(work, flow, {"kind": "REPAIR_AUTHORING",
                                                  "task": task(), "failed_run": run("RUN-002-001", A, B),
                                                  "failure": failure})
    assert packet.as_dict()["bounded_observations"] == {
        "phase": phase, "error": {"type": failure["error"]["type"]},
    }
    for private in ("C:/private", "token=private", "private native stream", "private raw log"):
        assert private not in packet.render()


def test_repair_authoring_ignores_unbound_reason_code():
    work, flow = context("AUTHOR_REPAIR", {"next_action": "AUTHOR_REPAIR",
                                           "failed_run_id": "RUN-002-001", "failed_head_sha": A})
    material = {"kind": "REPAIR_AUTHORING", "task": task(),
                "failed_run": run("RUN-002-001", A, B), "failure": runtime_failure()}
    baseline = compile_decision_packet(work, flow, material).as_dict()
    injected = copy.deepcopy(material)
    injected["failure"]["reason_code"] = "CALLER_INJECTED_REASON"
    packet = compile_decision_packet(work, flow, injected).as_dict()
    assert "reason_code" not in packet["bounded_observations"]
    assert packet == baseline
    assert packet["packet_fingerprint"] == baseline["packet_fingerprint"]


def test_repair_authoring_rejects_malformed_or_unbounded_failure_observations():
    failure = runtime_failure()
    work, flow = context("AUTHOR_REPAIR", {"next_action": "AUTHOR_REPAIR",
                                           "failed_run_id": "RUN-002-001", "failed_head_sha": A})
    for bad_error in ("unstructured", {"type": "RuntimeVerificationError"},
                      {"type": 42, "message": "failed"},
                      {"type": "RuntimeVerificationError", "message": ["failed"]},
                      {"type": "RuntimeVerificationError", "message": "x" * 65537}):
        altered = copy.deepcopy(failure)
        altered["error"] = bad_error
        with pytest.raises(DecisionPacketError):
            compile_decision_packet(work, flow, {"kind": "REPAIR_AUTHORING",
                                                  "task": task(), "failed_run": run("RUN-002-001", A, B),
                                                  "failure": altered})
    altered = copy.deepcopy(failure)
    altered["phase"] = "x" * 65537
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(work, flow, {"kind": "REPAIR_AUTHORING",
                                              "task": task(), "failed_run": run("RUN-002-001", A, B),
                                              "failure": altered})


def test_repair_authoring_binds_complete_runtime_failure_contract():
    work, flow = context("AUTHOR_REPAIR", {"next_action": "AUTHOR_REPAIR",
                                           "failed_run_id": "RUN-002-001", "failed_head_sha": A})
    material = {"kind": "REPAIR_AUTHORING", "task": task(),
                "failed_run": run("RUN-002-001", A, B), "failure": runtime_failure()}
    mutations = [
        lambda m: m["failure"].pop("kind"),
        lambda m: m["failure"].__setitem__("kind", "RESULT"),
        lambda m: m["failure"].__setitem__("run_id", "RUN-002-999"),
        lambda m: m["failure"]["task"].__setitem__("id", "TASK-999"),
        lambda m: m["failure"]["task"].__setitem__("revision", 2),
        lambda m: m["failure"].__setitem__("executor", "other"),
        lambda m: m["failure"].pop("executor"),
        lambda m: m["failure"].__setitem__("base_sha", C),
        lambda m: m["failure"].__setitem__("failed_head_sha", C),
        lambda m: m["failure"].pop("phase"),
        lambda m: m["failed_run"].__setitem__("run_id", "RUN-002-999"),
        lambda m: m["failed_run"]["task"].__setitem__("id", "TASK-999"),
        lambda m: m["failed_run"].__setitem__("head_sha", C),
        lambda m: m["failed_run"].__setitem__("base_sha", C),
        lambda m: m["failed_run"].__setitem__("executor", "other"),
        lambda m: m["failure"]["candidate"].pop("transportable"),
        lambda m: m["failure"]["candidate"].pop("repairable"),
        lambda m: m["failure"]["candidate"].pop("dirty"),
        lambda m: m["failure"]["candidate"].pop("descends_from_base"),
        lambda m: m["failure"]["candidate"].__setitem__("repairable", False),
        lambda m: m["failure"]["candidate"].__setitem__("transportable", False),
        lambda m: m["failure"]["candidate"].__setitem__("dirty", "false"),
        lambda m: m["failure"]["candidate"].pop("changed_files"),
        lambda m: m["failure"]["candidate"].__setitem__("changed_files", ["outside.txt"]),
        lambda m: m["failure"]["candidate"].__setitem__("changed_files", ["outside.txt", "outside.txt"]),
        lambda m: m["failure"]["candidate"].pop("outside_task_scope"),
        lambda m: m["failure"]["candidate"].__setitem__("outside_task_scope", ["outside.txt"]),
    ]
    for mutate in mutations:
        altered = copy.deepcopy(material)
        mutate(altered)
        with pytest.raises(DecisionPacketError):
            compile_decision_packet(work, flow, altered)


@pytest.mark.parametrize("executor", ["other", 42])
def test_repair_authoring_rejects_mirrored_invalid_executor(executor):
    work, flow = context("AUTHOR_REPAIR", {"next_action": "AUTHOR_REPAIR",
                                           "failed_run_id": "RUN-002-001", "failed_head_sha": A})
    failed_run = run("RUN-002-001", A, B)
    failure = runtime_failure()
    failed_run["executor"] = executor
    failure["executor"] = executor
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(work, flow, {"kind": "REPAIR_AUTHORING", "task": task(),
                                                  "failed_run": failed_run, "failure": failure})


@pytest.mark.parametrize("revision, valid", [(True, False), (1.0, False), (1, True)])
def test_repair_authoring_validates_supplied_failed_run_task_revision(revision, valid):
    work, flow = context("AUTHOR_REPAIR", {"next_action": "AUTHOR_REPAIR",
                                           "failed_run_id": "RUN-002-001", "failed_head_sha": A})
    failed_run = run("RUN-002-001", A, B)
    failed_run["task"]["revision"] = revision
    material = {"kind": "REPAIR_AUTHORING", "task": task(),
                "failed_run": failed_run, "failure": runtime_failure()}
    if not valid:
        with pytest.raises(DecisionPacketError):
            compile_decision_packet(work, flow, material)
    else:
        packet = compile_decision_packet(work, flow, material)
        assert packet.as_dict()["subject"]["failed_run_id"] == failed_run["run_id"]


def test_none_and_non_json_fail_closed():
    work, flow = context("EXECUTE_PRIMARY", {"next_action": "EXECUTE_PRIMARY"})
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(work, flow, {"kind": "DIAGNOSTIC"})
    selected, resolution = context("AUTHOR_REMEDIATION", {"next_action": "AUTHOR_REMEDIATION"}, selector="DIAGNOSTIC")
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(selected, resolution, {"kind": "DIAGNOSTIC", "observations": [float("nan")]})
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(selected, resolution, {"kind": "DIAGNOSTIC", "observations": ["x" * 65537]})
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(selected, resolution, {"kind": "DIAGNOSTIC",
                                                    "observations": ["x" * 65536, "y" * 65536]})


def test_checkout_remote_and_line_endings_do_not_change_packet_identity(tmp_path):
    other_root = tmp_path / "other-checkout"
    (other_root / ".ai").mkdir(parents=True)
    shutil.copyfile(ROOT / ".ai" / "flow-cards.yaml", other_root / ".ai" / "flow-cards.yaml")
    base = BrainSyncSnapshot(
        repository={"root": str(ROOT), "name": "AIOS-renew", "main_sha": A,
                    "remote": "origin"}, main_sha=A, roadmap={"next_items": ["bp-4"]},
        selection_status="SELECTED", lifecycle_state="READY", next_action="NONE",
        authority="BRAIN", selected_task={"id": "TASK-002", "revision": 1},
        unified_state={"next_action": "NONE"}, blocker=None,
    )
    other = replace(base, repository={**base.repository, "root": str(other_root),
                                      "remote": "upstream"})
    first = compose_brain_work_context(base, {"flow_selector": "DIAGNOSTIC",
                                               "human_input": "first\r\nsecond"})
    second = compose_brain_work_context(other, {"flow_selector": "DIAGNOSTIC",
                                                 "human_input": "first\nsecond"})
    one = compile_decision_packet(first, resolve_flow(first), {"kind": "DIAGNOSTIC",
                                                               "observations": ["same\r\nobservation"]})
    two = compile_decision_packet(second, resolve_flow(second), {"kind": "DIAGNOSTIC",
                                                                  "observations": ["same\nobservation"]})
    assert one.render() == two.render()
