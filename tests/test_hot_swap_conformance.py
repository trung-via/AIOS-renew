"""BP-7: offline continuation across independently reconstructed contexts.

Each test owns its mutable repository and provider callbacks.  Git objects and
canonical transport refs, rather than Python objects or chat history, carry
authority between checkpoints.  BP-8 live provider replacement is out of scope.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from aios_renew.authoring_ingress import (
    AuthoringIngressError, IngressEnvelope, execute_ingress,
)
from aios_renew.brain_context import compose_brain_work_context, resolve_flow
from aios_renew.brain_provider import BrainAttemptError, MappingBrainProvider, attempt as brain_attempt
from aios_renew.brain_return_contract import parse_return_contract_registry, select_return_contract
from aios_renew.decision_packet import DecisionPacketError, compile_decision_packet
from aios_renew.operator import OperatorError, recover_primary, run_task, runtime_paths
from aios_renew.review_material import construct_review_material_package
from aios_renew.review_transport import (
    resolve_remote_repair_authorization, resolve_remote_task_lifecycle,
    transport_failure,
)
from aios_renew.reviewer_procedure import select_reviewer_procedure
from aios_renew.reviewer_provider import (
    MappingReviewerProvider, ReviewerAttemptError, attempt as reviewer_attempt,
)
from aios_renew.reviewer_provider_protocol import construct_request as construct_review_request
from aios_renew.reviewer_return_contract import select_reviewer_return_contract
from aios_renew.unified_state import observe_semantic_review_scope, observe_unified_state
from tests.operator_test_support import TASK_SOURCE, commit_setup_state, git, make_repo, publish_upstream
from test_operator import (
    FakeCodexRunner, admission_failure_records, clone_runtime_fresh,
    publish_conflicting_primary_failure,
)
from test_authoring_ingress import V1_TASK_105_SOURCE, V1_TASK_105_R2_SOURCE, setup_test_repo


ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "TASK-101"


def _selected_repo(root: Path) -> Path:
    repo = make_repo(root)
    for name in ("flow-cards.yaml", "reviewer-procedure-profiles.yaml",
                 "reviewer-return-contracts.yaml", "brain-return-contracts.yaml"):
        source = ROOT / ".ai" / name
        target = repo / ".ai" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    roadmap = {"version": 1, "active_track": "bp-7", "active_track_status": "ACTIVE",
               "sequence": [{"id": "bp-7-fixture", "status": "NEXT",
                             "task_id": TASK_ID, "task_revision": 1}]}
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", "canonical planning and provider contracts")
    git(repo, "push", "--quiet", "origin", "main")
    return repo


def _authored_repo(root: Path) -> Path:
    repo, _, _ = setup_test_repo(root)
    for name in ("flow-cards.yaml", "reviewer-procedure-profiles.yaml",
                 "reviewer-return-contracts.yaml", "brain-return-contracts.yaml"):
        target = repo / ".ai" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / ".ai" / name, target)
    roadmap = {"version": 1, "active_track": "bp-7", "active_track_status": "ACTIVE",
               "sequence": [{"id": "authored-fixture", "status": "NEXT",
                             "task_id": TASK_ID, "task_revision": 1}]}
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", "canonical planning and provider contracts")
    git(repo, "push", "--quiet", "origin", "main")
    before = git(repo, "rev-parse", "HEAD")
    task_source = TASK_SOURCE.replace("verification:\n",
                                      "verification:\n  policy: minimum-sufficient-v1\n")
    authored = execute_ingress(IngressEnvelope(
        "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_TASK", {"task_id": TASK_ID},
        {"expected_main_sha": before}, task_source), repo=repo)
    assert authored.status == "CANONICALIZED"
    assert authored.canonical_sha == git(repo, "rev-parse", "HEAD")
    return repo


def _fresh(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    fresh = clone_runtime_fresh(source, destination / source.name)
    git(fresh, "checkout", "--quiet", "main")
    return fresh


def _checkpoint(repo: Path):
    context = compose_brain_work_context(repo=repo)
    return context, resolve_flow(context)


def _terminal(repo: Path, run_id: str):
    lifecycle = resolve_remote_task_lifecycle(repo, task_id=TASK_ID, task_revision=1)
    return next(item for item in lifecycle.terminals if item.run_id == run_id)


def _review_packet(repo: Path, run_id: str):
    context, flow = _checkpoint(repo)
    assert flow.selected_flow == "SEMANTIC_REVIEW"
    terminal = _terminal(repo, run_id)
    package = json.loads(terminal.terminal)
    material = {"kind": "SEMANTIC_REVIEW",
                "task": yaml.safe_load((repo / ".ai/tasks/TASK-101.yaml").read_text(encoding="utf-8")),
                "run": json.loads(terminal.run), "result": package["result"],
                "evidence": package["evidence"]}
    return compile_decision_packet(context, flow, material)


def _review_parts(repo: Path, run_id: str):
    packet = _review_packet(repo, run_id)
    scope = observe_semantic_review_scope(TASK_ID, repo=repo)
    material = construct_review_material_package(scope, repo=repo)
    procedure = select_reviewer_procedure(
        (repo / ".ai/reviewer-procedure-profiles.yaml").read_bytes(), scope["review_mode"])
    returns = select_reviewer_return_contract(
        (repo / ".ai/reviewer-return-contracts.yaml").read_bytes())
    bindings = {"review_id": "REVIEW-101-001",
                "finding_id_slots": [f"F-{i:02d}" for i in range(32)]}
    return packet, scope, material, procedure, returns, bindings


def _review_callback(request):
    assert set(request) == {"format", "version", "kind", "decision_packet",
                            "review_scope", "review_material_package",
                            "reviewer_procedure_package", "reviewer_return_contract_package",
                            "prior_review", "external_bindings", "request_fingerprint"}
    assert all(key not in request for key in ("provider", "model", "session_id", "repo", "github"))
    criteria = request["decision_packet"]["canonical_facts"]["task_contract"]["acceptance"]
    return {"semantic_response": {"request_fingerprint": request["request_fingerprint"],
                                  "semantic_body": {"verdict": "PASS", "findings": [],
                                                    "acceptance": [{"id": item["id"],
                                                                    "outcome": "PASS"}
                                                                   for item in criteria]}},
            "attribution": {"provider": "review-local", "model": "review-model",
                            "session_id": None, "invocation_id": None}}


def _review(repo: Path, run_id: str):
    # The callback has only the complete structured request; the fresh caller
    # owns repository observation, source material, and any later ingress.
    return reviewer_attempt(MappingReviewerProvider("review-local", "review-model", _review_callback),
                            *_review_parts(repo, run_id))


def _brain_parts(repo: Path):
    context = compose_brain_work_context(
        repo=repo, current_request={"flow_selector": "DIAGNOSTIC"})
    packet = compile_decision_packet(context, resolve_flow(context),
                                     {"kind": "DIAGNOSTIC", "observations": []})
    registry = parse_return_contract_registry((repo / ".ai/brain-return-contracts.yaml").read_bytes())
    return packet, select_return_contract(registry, packet)


def _brain_callback(request):
    assert set(request) == {"format", "version", "kind", "request_mode",
                            "decision_packet", "return_contract_package",
                            "external_bindings", "audit_profile_package", "stage1_lineage",
                            "request_fingerprint"}
    assert request["request_mode"] == "DIRECT"
    return {"semantic_response": {
        "request_fingerprint": request["request_fingerprint"],
        "candidate": {"proposal": "Inspect the canonical subject.",
                      "uncertainty": {"status": "NONE", "summary": None}}},
        "attribution": {"provider": "brain-local", "model": "brain-model",
                        "session_id": None, "invocation_id": None}}


def test_scenario_1_fresh_result_review_uses_canonical_run_and_packet(tmp_path: Path):
    source = _authored_repo(tmp_path / "source")
    execution = run_task(TASK_ID, executor="codex", repo=source,
                         native_runner=FakeCodexRunner(source))
    fresh = _fresh(source, tmp_path / "reviewer")
    packet = _review_packet(fresh, execution.run_id)
    parts = _review_parts(fresh, execution.run_id)
    request = construct_review_request(*parts)
    made = _review(fresh, execution.run_id)
    assert packet.as_dict()["subject"]["run_id"] == execution.run_id
    assert parts[0].packet_fingerprint == packet.packet_fingerprint
    assert request["decision_packet"]["packet_fingerprint"] == packet.packet_fingerprint
    assert made.decision["request_fingerprint"] == request["request_fingerprint"]
    assert made.decision["review_candidate"]["reviewed_sha"] == execution.head_sha
    assert made.decision["review_candidate"]["verdict"] == "PASS"
    assert made.decision["request_fingerprint"]
    assert observe_unified_state(TASK_ID, repo=fresh).next_action == "SEMANTIC_REVIEW"


def test_scenario_2_fresh_correction_accepts_only_current_finding(tmp_path: Path):
    source = _selected_repo(tmp_path / "source")
    execution = run_task(TASK_ID, executor="codex", repo=source,
                         native_runner=FakeCodexRunner(source))
    review = f"""review_id: REVIEW-101-001
reviewed_sha: {execution.head_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F-CURRENT
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: Output needs a correction.
    expected: Correct the output.
"""
    execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW",
                                    {"run_id": execution.run_id},
                                    {"expected_candidate_sha": execution.head_sha}, review), repo=source)
    fresh = _fresh(source, tmp_path / "correction")
    state = observe_unified_state(TASK_ID, repo=fresh)
    assert state.next_action == "AUTHOR_REMEDIATION"
    assert state.source_run_id == execution.run_id
    assert state.review_id == "REVIEW-101-001"
    assert state.finding_id == "F-CURRENT"
    payload = f"""finding_id: F-CURRENT
action: CODE_FIX
reviewed_sha: {execution.head_sha}
modification_scope: [OUTPUT.txt]
affected_verification: [git diff --check]
constraints:
  hard: [Commit the output.]
"""
    def envelope(finding_id: str, body: str):
        return IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REMEDIATION",
                               {"source_run_id": state.source_run_id, "finding_id": finding_id},
                               {"expected_reviewed_sha": state.reviewed_sha}, body)
    with pytest.raises(AuthoringIngressError):
        execute_ingress(envelope("F-STALE", payload.replace("F-CURRENT", "F-STALE")), repo=fresh)
    context, flow = _checkpoint(fresh)
    assert flow.selected_flow == "REMEDIATION_AUTHORING"
    lifecycle = resolve_remote_task_lifecycle(fresh, task_id=TASK_ID, task_revision=1)
    canonical_review = yaml.safe_load(next(item.review for item in lifecycle.reviews
                                           if item.run_id == execution.run_id))
    finding = canonical_review["findings"][0]
    item = {"source_run_id": state.source_run_id, "review_id": state.review_id,
            "reviewed_sha": state.reviewed_sha, "finding": finding}
    material = {"kind": "REMEDIATION_AUTHORING", "subject_kind": "UNIQUE_FINDING",
                "task": yaml.safe_load((fresh / ".ai/tasks/TASK-101.yaml").read_text(encoding="utf-8")),
                "findings": [item]}
    packet = compile_decision_packet(context, flow, material)
    assert packet.as_dict()["subject"]["finding_id"] == state.finding_id
    stale = deepcopy(material)
    stale["findings"][0]["finding"]["id"] = "F-STALE"
    with pytest.raises(DecisionPacketError):
        compile_decision_packet(context, flow, stale)
    authorized = execute_ingress(envelope(state.finding_id, payload), repo=fresh)
    assert authorized.status == "CANONICALIZED"
    assert authorized.canonical_destination.endswith(f"/{execution.run_id}-F-CURRENT")


def test_scenario_3_fresh_repair_resolves_current_failure_authorization(tmp_path: Path):
    repo, _, _ = setup_test_repo(tmp_path / "source")
    task_path = repo / ".ai/tasks/TASK-105.yaml"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(V1_TASK_105_SOURCE, encoding="utf-8")
    shutil.copyfile(ROOT / ".ai/flow-cards.yaml", repo / ".ai/flow-cards.yaml")
    roadmap = {"version": 1, "active_track": "bp-7", "active_track_status": "ACTIVE",
               "sequence": [{"id": "repair-fixture", "status": "NEXT",
                             "task_id": "TASK-105", "task_revision": 1}]}
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    task_main = commit_setup_state(repo, ".ai", message="canonical task and planning")
    git(repo, "push", "--quiet", "origin", "main")
    candidate = repo / "src/sample.py"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("# repairable\n", encoding="utf-8")
    head = commit_setup_state(repo, "src/sample.py", message="failed candidate")
    run_id = "RUN-105-001"
    run = {"run_id": run_id, "task": {"id": "TASK-105", "revision": 1},
           "executor": "codex", "base_sha": task_main, "workspace": str(repo),
           "head_sha": head, "status": "ACTIVE"}
    failure = {"kind": "FAILURE", "run_id": run_id, "task": run["task"],
               "executor": "codex", "base_sha": task_main, "failed_head_sha": head,
               "phase": "VERIFICATION", "error": {"type": "RuntimeVerificationError",
                                                  "message": "failed"},
               "candidate": {"transportable": True, "repairable": True,
                             "dirty": False, "descends_from_base": True,
                             "changed_files": ["src/sample.py"], "outside_task_scope": []}}
    run_file, failure_file = tmp_path / "run.json", tmp_path / "failure.json"
    run_file.write_text(json.dumps(run), encoding="utf-8")
    failure_file.write_text(json.dumps(failure), encoding="utf-8")
    transport_failure(repo, run_id=run_id, head_sha=head, run_path=run_file,
                      failure_path=failure_file)
    repair = {"repair_id": "REPAIR-105-001", "failed_run_id": run_id,
              "failed_head_sha": head, "task": run["task"], "action": "CODE_FIX",
              "modification_scope": ["src/sample.py"],
              "instructions": ["Repair the exact failed candidate."], "constraints": []}
    first = execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                                            {"failed_run_id": run_id},
                                            {"expected_failed_head_sha": head}, repair), repo=repo)
    failure_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    failure_sha = git(repo, "ls-remote", "--refs", "origin", failure_ref).split()[0]
    successor = {**repair, "instructions": ["Repair the current failed candidate precisely."]}
    second = execute_ingress(IngressEnvelope(
        "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR", {"failed_run_id": run_id},
        {"expected_failed_head_sha": head, "expected_current_repair_sha": first.canonical_sha,
         "expected_failure_artifacts_sha": failure_sha}, successor), repo=repo)
    fresh = _fresh(repo, tmp_path / "repair")
    current = resolve_remote_repair_authorization(fresh, run_id)
    state = observe_unified_state("TASK-105", repo=fresh)
    assert state.next_action == "EXECUTE_REPAIR"
    assert state.failed_run_id == run_id
    assert state.failed_head_sha == head
    context, flow = _checkpoint(fresh)
    assert context.canonical_observation["unified_state"]["failed_run_id"] == run_id
    assert flow.canonical_next_action == "EXECUTE_REPAIR"
    assert flow.selected_flow == "NONE"
    assert current.failed_run_id == run_id
    assert json.loads(current.repair) == successor
    assert current.commit_sha == second.canonical_sha
    assert current.predecessor_sha == first.canonical_sha
    assert current.revision == 2
    lifecycle = resolve_remote_task_lifecycle(fresh, task_id="TASK-105", task_revision=1)
    assert lifecycle.repair_selectors == ((run_id, second.canonical_sha, current.repair),)
    with pytest.raises(AuthoringIngressError):
        execute_ingress(IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR", {"failed_run_id": run_id},
            {"expected_failed_head_sha": head,
             "expected_current_repair_sha": first.canonical_sha,
             "expected_failure_artifacts_sha": failure_sha},
            {**successor, "instructions": ["Stale repair authoring."]}), repo=fresh)
    with pytest.raises(AuthoringIngressError):
        execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                                        {"failed_run_id": run_id},
                                        {"expected_failed_head_sha": "0" * 40}, repair), repo=fresh)


def test_scenario_4_canonical_human_priority_invalidates_old_handoff(tmp_path: Path):
    repo, _, base = setup_test_repo(tmp_path)
    for name in ("flow-cards.yaml",):
        target = repo / ".ai" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / ".ai" / name, target)
    roadmap = {"version": 1, "active_track": "bp-7", "active_track_status": "ACTIVE",
               "sequence": [{"id": "human-priority", "status": "NEXT", "task_id": "TASK-105"}]}
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", "canonical priority")
    git(repo, "push", "--quiet", "origin", "main")
    prior = git(repo, "rev-parse", "HEAD")
    author = lambda expected, payload: IngressEnvelope(
        "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_TASK", {"task_id": "TASK-105"},
        {"expected_main_sha": expected}, payload)
    first = execute_ingress(author(prior, V1_TASK_105_SOURCE), repo=repo)
    before = _fresh(repo, tmp_path / "before")
    request = {"flow_selector": "TASK_AUTHORING", "human_input": "Current canonical priority"}
    old_context = compose_brain_work_context(repo=before, current_request=request)
    old_packet = compile_decision_packet(old_context, resolve_flow(old_context),
                                         {"kind": "TASK_AUTHORING"})
    revised = V1_TASK_105_R2_SOURCE.replace("Revise generic ingress capability.",
                                            "Human priority changed to revised work.")
    second = execute_ingress(author(first.canonical_sha, revised), repo=repo)
    fresh = _fresh(repo, tmp_path / "after")
    new_context = compose_brain_work_context(repo=fresh, current_request=request)
    new_packet = compile_decision_packet(new_context, resolve_flow(new_context),
                                         {"kind": "TASK_AUTHORING"})
    assert second.canonical_sha != first.canonical_sha
    assert new_context.invalidation_fingerprint != old_context.invalidation_fingerprint
    assert new_packet.packet_fingerprint != old_packet.packet_fingerprint
    with pytest.raises(AuthoringIngressError, match="expected main SHA mismatch"):
        execute_ingress(author(first.canonical_sha,
                               revised.replace("revision: 2", "revision: 3")), repo=repo)


def test_scenario_5_old_primary_authorization_rejects_new_task_revision(tmp_path: Path):
    repo = make_repo(tmp_path)
    old_commit = git(repo, "rev-parse", "HEAD")
    old_blob = git(repo, "rev-parse", "HEAD:.ai/tasks/TASK-101.yaml")
    task_path = repo / ".ai/tasks/TASK-101.yaml"
    task_path.write_text(TASK_SOURCE.replace("revision: 1", "revision: 2").replace(
        "Create one deterministic operator test output.", "New Human-selected work."), encoding="utf-8")
    git(repo, "add", ".ai/tasks/TASK-101.yaml")
    git(repo, "commit", "--quiet", "-m", "canonical task revision")
    git(repo, "push", "--quiet", "origin", "main")
    calls = []
    def no_executor(*args, **kwargs):
        calls.append(1)
        raise AssertionError("executor invoked before exact TASK admission")
    with pytest.raises(OperatorError, match="authorized TASK identity mismatch"):
        run_task(TASK_ID, executor="codex", repo=repo, native_runner=no_executor,
                 synchronize=False, preflight_sha=git(repo, "rev-parse", "HEAD"),
                 task_revision=1, task_blob_sha=old_blob, task_commit_sha=old_commit)
    assert calls == []
    assert list(runtime_paths(repo).runs.glob("*.json")) == []
    assert admission_failure_records(repo)[0]["reason_code"] == "AUTHORIZED_TASK_IDENTITY_MISMATCH"


def test_scenario_6_unrelated_main_uses_latest_base_with_same_task_blob(tmp_path: Path):
    repo = make_repo(tmp_path / "source")
    authorized_commit = git(repo, "rev-parse", "HEAD")
    task_blob = git(repo, "rev-parse", "HEAD:.ai/tasks/TASK-101.yaml")
    advanced = publish_upstream(repo, {"UNRELATED.txt": "new planning-neutral content\n"})
    runner = FakeCodexRunner(repo)
    execution = run_task(TASK_ID, executor="codex", repo=repo, native_runner=runner,
                         task_revision=1, task_blob_sha=task_blob,
                         task_commit_sha=authorized_commit)
    assert advanced != authorized_commit
    assert execution.base_sha == advanced
    assert len(runner.calls) == 1
    assert git(repo, "rev-parse", f"{execution.base_sha}:.ai/tasks/TASK-101.yaml") == task_blob


def test_scenario_7_operational_failure_is_pre_aios_and_receipt_is_not_success(tmp_path: Path):
    repo = make_repo(tmp_path)
    workflow = yaml.safe_load((ROOT / ".github/workflows/aios-self-hosted-wakeup.yml").read_text(
        encoding="utf-8"))
    steps = workflow["jobs"]["wakeup"]["steps"]
    assert steps[0]["name"] == "Prepare exact transient control source"
    assert "$controlSource wakeup" not in steps[0]["run"]
    assert "$controlSource wakeup" in steps[1]["run"]
    script = tmp_path / "preflight.ps1"
    script.write_text(steps[0]["run"], encoding="utf-8")
    receipt_path = tmp_path / "operational-receipt.json"
    env = dict(os.environ)
    env.update({
        "AIOS_REPO_ROOT": "",
        "AIOS_OPERATIONAL_RECEIPT_PATH": str(receipt_path),
        "AIOS_DISPATCH_ID": "delivery-1",
        "AIOS_TASK_ID": TASK_ID,
        "AIOS_TASK_REVISION": "1",
        "AIOS_TASK_BLOB_SHA": git(repo, "rev-parse", f"HEAD:.ai/tasks/{TASK_ID}.yaml"),
        "AIOS_TASK_COMMIT_SHA": git(repo, "rev-parse", "HEAD"),
        "AIOS_EXECUTOR": "codex",
    })
    started = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)],
        env=env, capture_output=True, text=True, check=False,
    )
    assert started.returncode != 0
    assert "AIOS_REPO_ROOT repository variable is not set or empty" in (
        started.stdout + started.stderr)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    assert receipt["format"] == "AIOS_OPERATIONAL_RECEIPT"
    assert receipt["version"] == 2
    assert receipt["kind"] == "OPERATIONAL_RECEIPT"
    assert receipt["family"] == "PRIMARY"
    assert receipt["delivery"] == {"kind": "dispatch_id", "id": "delivery-1"}
    assert receipt["boundary"] == "OPERATIONAL_FAILED"
    assert receipt["cause"] == {
        "authority": "WORKFLOW", "phase": "PRE_AIOS",
        "reason_code": "AIOS_REPO_ROOT_NOT_CONFIGURED",
    }
    assert receipt["run_created"] is False
    assert receipt["executor_invoked"] is False
    assert "run_id" not in receipt and "terminal_pointer" not in receipt
    state = runtime_paths(repo)
    assert not list(state.runs.glob("*.json"))
    assert not list(state.failures.glob("*.json"))
    assert admission_failure_records(repo) == []
    assert resolve_remote_task_lifecycle(repo, task_id=TASK_ID, task_revision=1).terminals == ()


def test_scenario_8_pre_handoff_failure_and_canonical_recovery_are_distinct(tmp_path: Path):
    source = _selected_repo(tmp_path / "source")
    success = run_task(TASK_ID, executor="codex", repo=source,
                       native_runner=FakeCodexRunner(source))
    fresh_review = _fresh(source, tmp_path / "review-failure")
    calls = []
    def offline(_request):
        calls.append(1)
        raise OSError("provider offline")
    with pytest.raises(ReviewerAttemptError) as caught:
        reviewer_attempt(MappingReviewerProvider("review-local", "review-model", offline),
                         *_review_parts(fresh_review, success.run_id))
    assert caught.value.reason_code == "PROVIDER_TRANSPORT_FAILURE"
    assert caught.value.invocation_count == len(calls) == 1
    assert observe_unified_state(TASK_ID, repo=fresh_review).next_action == "SEMANTIC_REVIEW"
    assert _terminal(fresh_review, success.run_id).kind == "RESULT"
    assert resolve_remote_task_lifecycle(fresh_review, task_id=TASK_ID,
                                         task_revision=1).reviews == ()
    fresh_brain = _fresh(source, tmp_path / "brain-failure")
    brain_calls = []
    def invalid(_request):
        brain_calls.append(1)
        return {"semantic_response": {}, "attribution": {
            "provider": "brain-local", "model": "brain-model",
            "session_id": None, "invocation_id": None}}
    with pytest.raises(BrainAttemptError) as brain_error:
        brain_attempt(MappingBrainProvider("brain-local", "brain-model", invalid),
                      *_brain_parts(fresh_brain), {})
    assert brain_error.value.reason_code == "PROVIDER_RESPONSE_INVALID"
    assert brain_error.value.invocation_count == len(brain_calls) == 1
    assert observe_unified_state(TASK_ID, repo=fresh_brain).next_action == "SEMANTIC_REVIEW"
    publish_conflicting_primary_failure(source, run_id=success.run_id,
                                        base_sha=success.base_sha, root=tmp_path)
    recovery = _fresh(source, tmp_path / "recovery")
    verified = []
    def verify(command, **kwargs):
        verified.append((command, git(kwargs["cwd"], "rev-parse", "HEAD")))
        return subprocess.run(command, **kwargs)
    recovered = recover_primary(success.run_id, repo=recovery, verification_runner=verify)
    result = json.loads(recovered.result_path.read_text(encoding="utf-8"))
    observation = json.loads((runtime_paths(recovery).observations /
                              f"{recovered.run_id}.json").read_text(encoding="utf-8"))
    assert recovered.run_id != success.run_id
    assert recovered.head_sha == success.head_sha
    assert verified and {sha for _, sha in verified} == {success.head_sha}
    assert {item["run_id"] for item in result["evidence"]} == {recovered.run_id}
    assert observation["executor_invoked"] is False


def test_scenario_9_role_attribution_does_not_change_subject_fingerprints(tmp_path: Path):
    repo = _selected_repo(tmp_path / "source")
    success = run_task(TASK_ID, executor="codex", repo=repo,
                       native_runner=FakeCodexRunner(repo))
    fingerprints = []
    for index in (1, 2):
        fresh = _fresh(repo, tmp_path / f"provider-{index}")
        parts = _review_parts(fresh, success.run_id)
        provider, model = f"review-{index}", f"review-model-{index}"
        def native(request, p=provider, m=model):
            wrapped = _review_callback(request)
            wrapped["attribution"].update(provider=p, model=m)
            return wrapped
        decision = reviewer_attempt(MappingReviewerProvider(provider, model, native), *parts)
        fingerprints.append((parts[0].packet_fingerprint, decision.decision["request_fingerprint"],
                             decision.decision["decision_fingerprint"], decision.attribution))
    assert fingerprints[0][:3] == fingerprints[1][:3]
    assert fingerprints[0][3] != fingerprints[1][3]
    brain_repo = _fresh(repo, tmp_path / "brain-authority")
    brain_packet, brain_contract = _brain_parts(brain_repo)
    brain = brain_attempt(MappingBrainProvider("brain-local", "brain-model", _brain_callback),
                          brain_packet, brain_contract, {})
    second_brain_repo = _fresh(repo, tmp_path / "other-brain-authority")
    other_packet, other_contract = _brain_parts(second_brain_repo)
    def other_brain(request):
        wrapped = _brain_callback(request)
        wrapped["attribution"].update(provider="other-brain", model="other-model")
        return wrapped
    other = brain_attempt(MappingBrainProvider("other-brain", "other-model", other_brain),
                          other_packet, other_contract, {})
    assert brain_packet.as_dict()["authority_owner"] == "BRAIN"
    assert _review_parts(_fresh(repo, tmp_path / "review-authority"), success.run_id)[0].as_dict()[
        "authority_owner"] == "REVIEWER"
    assert brain.decision["packet_fingerprint"] == brain_packet.packet_fingerprint
    assert other_packet.packet_fingerprint == brain_packet.packet_fingerprint
    assert other.decision["request_fingerprint"] == brain.decision["request_fingerprint"]
    assert other.decision["decision_fingerprint"] == brain.decision["decision_fingerprint"]
    assert other.attributions != brain.attributions
    assert brain.attributions[0]["provider"] != fingerprints[0][3]["provider"]


def test_scenario_10_repository_blind_provider_has_complete_bounded_request(tmp_path: Path):
    repo = _selected_repo(tmp_path / "source")
    success = run_task(TASK_ID, executor="codex", repo=repo,
                       native_runner=FakeCodexRunner(repo))
    fresh = _fresh(repo, tmp_path / "blind")
    before = git(fresh, "rev-parse", "HEAD")
    received = []
    def blind(request):
        received.append(deepcopy(request))
        return _review_callback(request)
    made = reviewer_attempt(MappingReviewerProvider("review-local", "review-model", blind),
                            *_review_parts(fresh, success.run_id))
    assert len(received) == 1
    assert received[0]["review_scope"]["reviewed_head_sha"] == success.head_sha
    assert received[0]["review_material_package"]["package_fingerprint"]
    assert made.decision["review_candidate"]["verdict"] == "PASS"
    assert git(fresh, "rev-parse", "HEAD") == before
    assert resolve_remote_task_lifecycle(fresh, task_id=TASK_ID, task_revision=1).reviews == ()


def test_bp7_surface_is_test_only_and_never_claims_bp8():
    task = yaml.safe_load((ROOT / ".ai/tasks/TASK-190.yaml").read_text(encoding="utf-8"))
    assert task["scope"]["modify"] == ["tests/test_hot_swap_conformance.py"]
    assert set(task["scope"]["modify"]).isdisjoint({
        "src/aios_renew/operator.py", "tests/test_operator.py", ".github/workflows/aios-brain-wakeup.yml",
        ".ai/roadmap-state.yaml", "docs/AIOS-BRAIN-PORTABILITY.md"})
    assert "BP-8 live provider replacement is out of scope" in __doc__
