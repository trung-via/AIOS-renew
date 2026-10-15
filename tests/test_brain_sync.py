"""Deterministic tests for AIOS Brain Sync observation snapshot."""

import json
import os
from pathlib import Path
import pytest
import yaml

from aios_renew.brain_sync import (
    BrainSyncError,
    BrainSyncSnapshot,
    observe_brain_sync,
)
from aios_renew.operator import (
    OperatorError,
    runtime_state_root,
)
from aios_renew.verification import materialize_verification_subject
from tests.git_fixture_support import commit_fixture_state, read_git_ref
from tests.operator_test_support import (
    TASK_SOURCE,
    git,
    make_repo,
)


def test_brain_sync_ready_single_next_rehydration(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    roadmap = {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "sequence": [
            {
                "id": "task-101-execution",
                "status": "NEXT",
                "task_id": "TASK-101",
                "task_revision": 1,
            }
        ],
    }
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    roadmap_path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "add roadmap")
    git(repo, "push", "origin", "main")
    head = git(repo, "rev-parse", "HEAD")

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.format == "AIOS_BRAIN_SYNC_SNAPSHOT"
    assert snapshot.version == 1
    assert snapshot.kind == "BRAIN_SYNC_SNAPSHOT"
    assert snapshot.main_sha == head
    assert snapshot.selection_status == "SELECTED"
    assert snapshot.selected_task == {"id": "TASK-101", "revision": 1}
    assert snapshot.lifecycle_state == "READY"
    assert snapshot.next_action == "EXECUTE_PRIMARY"
    assert snapshot.authority == "HUMAN_RUNTIME"
    assert snapshot.blocker is None
    assert snapshot.unified_state is not None
    assert snapshot.unified_state["next_action"] == "EXECUTE_PRIMARY"
    assert snapshot.run_created is False
    assert snapshot.executor_invoked is False
    assert snapshot.verification_invoked is False
    assert snapshot.state_mutated is False

    # Checkpoint output
    chk = snapshot.checkpoint()
    assert f"MAIN: {head}" in chk
    assert "AUTHORED NEXT TASK: TASK-101" in chk
    assert "STATE: READY" in chk

    # Serialization
    as_dict = snapshot.as_dict()
    assert json.loads(snapshot.render()) == as_dict


def test_failure_diagnostic_handoff_composes_runtime_transport_fresh_sync_and_work_context(tmp_path):
    from aios_renew.operator import load_task, runtime_paths
    from aios_renew.review_transport import transport_failure
    from aios_renew.brain_context import compose_brain_work_context
    from tests.test_review_transport import diagnostic_failure_fixture
    repo = make_repo(tmp_path)
    roadmap = dict(version=1, active_track="synthetic", active_track_status="ACTIVE",
                   sequence=[dict(id="synthetic-failure", status="NEXT", task_id="TASK-101", task_revision=1)])
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".ai/roadmap-state.yaml")
    git(repo, "commit", "-m", "synthetic failure diagnostic selection")
    git(repo, "push", "origin", "main")
    head = git(repo, "rev-parse", "HEAD")
    run, private, original = diagnostic_failure_fixture(run_id="RUN-101-902", task=dict(id="TASK-101", revision=1), subject=head)
    run["workspace"] = str(repo)
    state = runtime_paths(repo)
    run_path, failure_path = state.runs / (run["run_id"] + ".json"), state.failures / (run["run_id"] + ".json")
    run_path.write_text(json.dumps(run), encoding="utf-8")
    failure_path.write_bytes(original)
    local = observe_brain_sync(repo)
    local_diagnostic = local.unified_state["failure_diagnostics"]
    assert local_diagnostic["private_evidence"]["availability"] == "AVAILABLE"
    assert local_diagnostic["source"]["artifact_sha"] is None
    transport_failure(repo, run_id=run["run_id"], head_sha=head, run_path=run_path, failure_path=failure_path, publish_candidate=False)
    remote = tmp_path / "upstream.git"
    artifact = git(remote, "rev-parse", "refs/heads/aios/failure-artifacts/" + run["run_id"])
    event = f"terminal:FAILURE:{run['run_id']}:{artifact}"
    snapshot = observe_brain_sync(repo, attention_event_id=event)
    assert snapshot.next_action == "NONE" and snapshot.blocker["code"] == "NON_REPAIRABLE_FAILURE"
    diagnostic = snapshot.unified_state["failure_diagnostics"]
    assert diagnostic["source"]["artifact_sha"] == artifact and diagnostic["diagnostics"]["native_exit"]["code"] is None
    assert snapshot.diagnostic_attention["failure_diagnostics"]["source"]["artifact_sha"] == artifact
    context = compose_brain_work_context(snapshot, {"flow_selector": "DIAGNOSTIC"})
    assert "synthetic-private-value" not in context.render() and "widgets" in context.render()
    assert context.canonical_observation["unified_state"]["failure_diagnostics"] == diagnostic
    assert failure_path.read_bytes() == original and git(repo, "rev-parse", "HEAD") == head
    assert not git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/failure/")
    failure_path.unlink()
    run_path.unlink()
    fresh = observe_brain_sync(repo, attention_event_id=event)
    assert fresh.unified_state["failure_diagnostics"]["private_evidence"]["availability"] == "RESTRICTED"
    assert fresh.diagnostic_attention == snapshot.diagnostic_attention
    assert fresh.run_created is False and fresh.executor_invoked is False and fresh.verification_invoked is False


def test_failure_diagnostic_runtime_timeout_reaches_fresh_provider_and_private_executor(tmp_path):
    import subprocess
    from dataclasses import asdict
    from aios_renew.codex_adapter import CodexExecutionError
    from aios_renew.run import Run
    from aios_renew.runtime import persist_failure, read_private_failure_diagnostics
    from aios_renew.operator import load_task, runtime_paths
    from aios_renew.brain_context import compose_brain_work_context, resolve_flow
    from aios_renew.decision_packet import compile_decision_packet
    from aios_renew.brain_return_contract import parse_return_contract_registry, select_return_contract
    from aios_renew.brain_provider import MappingBrainProvider, attempt
    from aios_renew.local_chat_wake import doorbell
    from tests.test_brain_provider import wrapper

    repo = make_repo(tmp_path)
    # The synthetic repository needs its own procedural registry, just like a
    # real fresh Brain workspace. Missing registries must never use a fallback.
    (repo / ".ai/flow-cards.yaml").write_bytes(
        (Path(__file__).resolve().parents[1] / ".ai/flow-cards.yaml").read_bytes())
    roadmap = dict(version=1, active_track="synthetic", active_track_status="ACTIVE",
        sequence=[dict(id="synthetic-timeout", status="NEXT", task_id="TASK-101", task_revision=1)])
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".ai/roadmap-state.yaml", ".ai/flow-cards.yaml")
    git(repo, "commit", "-m", "synthetic timeout selection")
    git(repo, "push", "origin", "main")
    task = load_task(repo, "TASK-101")
    head = git(repo, "rev-parse", "HEAD")
    run = Run.from_task(run_id="RUN-101-903", task=task, executor="codex", base_sha=head, workspace=str(repo))
    state = runtime_paths(repo)
    run_path = state.runs / (run.run_id + ".json")
    run_path.write_text(json.dumps(asdict(run)), encoding="utf-8")
    for index in range(23):
        (repo / f"scratch-{index:02}.txt").write_text("synthetic private candidate", encoding="utf-8")
    stdout = b"ModuleNotFoundError: No module named 'widgets'\n" + b"synthetic progress\n" * 400
    stderr = b"token=synthetic-private-value\xff"
    native = CodexExecutionError("native execution timed out", exit_code=None, stdout=stdout, stderr=stderr)
    native.__cause__ = subprocess.TimeoutExpired("synthetic-native", 185 * 60, output=stdout, stderr=stderr)
    boundary = RuntimeError("synthetic invocation failed")
    boundary.__cause__ = native
    persist_failure(repo, state=state, task=task, run=run, run_path=run_path, failure=boundary)
    failure_path = state.failures / (run.run_id + ".json")
    original = failure_path.read_bytes()
    local = read_private_failure_diagnostics(repo, state=state, task=task, run=run, subject_sha=head)
    assert local["failure_bytes"] == original
    assert local["native_captures"]["stdout"]["bytes"] == stdout
    assert local["native_captures"]["stderr"]["bytes"] == stderr
    remote = tmp_path / "upstream.git"
    artifact = git(remote, "rev-parse", "refs/heads/aios/failure-artifacts/" + run.run_id)
    event = f"terminal:FAILURE:{run.run_id}:{artifact}"
    snapshot = observe_brain_sync(repo, attention_event_id=event)
    context = compose_brain_work_context(snapshot,
        {"flow_selector": "DIAGNOSTIC", "human_input": doorbell(event, snapshot.repository["name"])})
    packet = compile_decision_packet(context, resolve_flow(context), {"kind": "DIAGNOSTIC"})
    registry = parse_return_contract_registry((Path(__file__).resolve().parents[1] / ".ai/brain-return-contracts.yaml").read_bytes())
    requests = []

    def invoke(request):
        requests.append(request)
        return wrapper(request)

    result = attempt(MappingBrainProvider("mapping", "one", invoke), packet,
        select_return_contract(registry, packet), {}, work_context=context)
    assert result.decision["selected_flow"] == "DIAGNOSTIC" and len(requests) == 1
    diagnostic = requests[0]["diagnostic_context"]["diagnostic_attention"]["failure_diagnostics"]
    assert diagnostic["source"]["artifact_sha"] == artifact
    assert diagnostic["diagnostics"]["native_exit"] == dict(code=None, state="UNKNOWN")
    assert diagnostic["diagnostics"]["native_timeout_seconds"] == 185 * 60
    assert diagnostic["diagnostics"]["candidate_workspace"]["dirty_entry_count"] == 23
    assert diagnostic["diagnostics"]["native_streams"]["stdout"]["truncated"] is True
    assert diagnostic["diagnostics"]["native_streams"]["stderr"]["availability"] == "UNAVAILABLE"
    assert "widgets" in json.dumps(requests) and "synthetic-private-value" not in json.dumps(requests)
    assert failure_path.read_bytes() == original and git(repo, "rev-parse", "HEAD") == head
    assert not git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/failure/")
    assert git(remote, "ls-tree", "-r", "--name-only", artifact).splitlines() == [".ai/transport/failure.json", ".ai/transport/run.json"]


def test_failure_diagnostic_incomplete_v2_transports_and_rehydrates_without_reclassifying_private_evidence(tmp_path):
    import hashlib
    from dataclasses import asdict
    from aios_renew.run import Run
    from aios_renew.operator import load_task, runtime_paths
    from aios_renew.runtime import read_private_failure_diagnostics
    from aios_renew.review_transport import transport_failure
    from aios_renew.brain_context import compose_brain_work_context
    from tests.test_review_transport import incomplete_failure_fixture

    repo = make_repo(tmp_path)
    roadmap = dict(version=1, active_track="synthetic", active_track_status="ACTIVE",
        sequence=[dict(id="synthetic-incomplete", status="NEXT", task_id="TASK-101", task_revision=1)])
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".ai/roadmap-state.yaml")
    git(repo, "commit", "-m", "synthetic incomplete diagnostic selection")
    git(repo, "push", "origin", "main")
    head = git(repo, "rev-parse", "HEAD")
    task = load_task(repo, "TASK-101")
    run = Run.from_task(run_id="RUN-101-904", task=task, executor="codex", base_sha=head, workspace=str(repo))
    _, private = incomplete_failure_fixture("missing", run_id=run.run_id,
        task=dict(id=task.task_id, revision=task.revision), base_sha=head, head_sha=head, changed_files=())
    private["candidate"].update(dirty=True, repairable=False, transportable=False)
    private["workspace_state"] = dict(dirty_entry_count=23, outside_task_scope_count=0)
    state = runtime_paths(repo)
    run_path = state.runs / (run.run_id + ".json")
    run_path.write_text(json.dumps(asdict(run)), encoding="utf-8")
    directory = state.verification / run.run_id
    directory.mkdir()
    ref = private["error"]["verification"][0]["verification"]
    for prefix in ("", "base_"):
        capture = directory / (prefix + "incomplete.raw")
        content = (prefix + "token=synthetic-private-value").encode()
        capture.write_bytes(content)
        ref[prefix + "raw_path"] = str(capture)
        ref[prefix + "raw_digest"] = hashlib.sha256(content).hexdigest()
    failure_path = state.failures / (run.run_id + ".json")
    original = json.dumps(private, sort_keys=True).encode()
    failure_path.write_bytes(original)
    local = read_private_failure_diagnostics(repo, state=state, task=task, run=run, subject_sha=head)
    assert local["failure_bytes"] == original and len(local["captures"]) == 2
    assert all(item["availability"] == "AVAILABLE" and item["integrity"] == "BOUND_DIGEST" for item in local["captures"])
    transport_failure(repo, run_id=run.run_id, head_sha=head, run_path=run_path, failure_path=failure_path, publish_candidate=False)
    remote = tmp_path / "upstream.git"
    artifact = git(remote, "rev-parse", "refs/heads/aios/failure-artifacts/" + run.run_id)
    event = f"terminal:FAILURE:{run.run_id}:{artifact}"
    snapshot = observe_brain_sync(repo, attention_event_id=event)
    handoff = snapshot.diagnostic_attention["failure_diagnostics"]
    assert snapshot.diagnostic_attention["availability"] == "AVAILABLE"
    assert handoff["diagnostics"]["completeness"] == "PARTIAL"
    assert handoff["source"]["artifact_sha"] == artifact
    assert handoff["evidence_identities"][0]["evidence_id"] == ref["evidence_id"]
    assert snapshot.blocker["code"] == "NON_REPAIRABLE_FAILURE" and snapshot.next_action == "NONE"
    context = compose_brain_work_context(snapshot, {"flow_selector": "DIAGNOSTIC"})
    assert "VERIFICATION_OBSERVATION_INCOMPLETE" in context.render() and "synthetic-private-value" not in context.render()
    transport_failure(repo, run_id=run.run_id, head_sha=head, run_path=run_path, failure_path=failure_path, publish_candidate=False)
    assert failure_path.read_bytes() == original and all(item["classification"] == "UNRESOLVED" for item in ref["attribution"])
    assert len(ref["attribution"]) == 18
    assert not git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/failure/")
    assert git(repo, "rev-parse", "HEAD") == head


def test_brain_sync_completed_no_next(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    roadmap = {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "COMPLETE",
        "next_items": [],
        "sequence": [
            {
                "id": "item-done",
                "status": "DONE",
                "completed_by": {
                    "task_id": "TASK-101",
                    "published_sha": head,
                },
            }
        ],
    }
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    roadmap_path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "COMPLETED_TRACK"
    assert snapshot.selected_task is None
    assert snapshot.unified_state is None
    assert snapshot.lifecycle_state == "COMPLETE"
    assert snapshot.next_action == "NONE"
    assert snapshot.authority == "NONE"
    assert snapshot.blocker is None
    assert snapshot.roadmap["active_track_status"] == "COMPLETE"
    assert snapshot.roadmap["last_published_task"] == "TASK-101"


def test_brain_sync_missing_roadmap(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "MISSING_ROADMAP"
    assert snapshot.selected_task is None
    assert snapshot.unified_state is None
    assert snapshot.lifecycle_state == "BLOCKED"
    assert snapshot.next_action == "NONE"
    assert snapshot.authority == "NONE"
    assert snapshot.blocker is not None
    assert snapshot.blocker["code"] == "MISSING_ROADMAP"


def test_brain_sync_ambiguous_next_multiple_items(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    roadmap = {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "next_items": ["TASK-101", "TASK-102"],
        "sequence": [
            {"id": "t1", "status": "NEXT", "task_id": "TASK-101"},
            {"id": "t2", "status": "NEXT", "task_id": "TASK-102"},
        ],
    }
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    roadmap_path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "AMBIGUOUS_NEXT"
    assert snapshot.selected_task is None
    assert snapshot.unified_state is None
    assert snapshot.lifecycle_state == "BLOCKED"
    assert snapshot.next_action == "NONE"
    assert snapshot.authority == "NONE"
    assert snapshot.blocker is not None
    assert snapshot.blocker["code"] == "AMBIGUOUS_NEXT"


def test_brain_sync_unauthored_next(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    roadmap = {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "sequence": [
            {
                "id": "unauthored-step",
                "status": "NEXT",
                "task_id": "TASK-999",
            }
        ],
    }
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    roadmap_path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "UNAUTHORED_TASK"
    assert snapshot.selected_task is None
    assert snapshot.unified_state is None
    assert snapshot.lifecycle_state == "BLOCKED"
    assert snapshot.next_action == "NONE"
    assert snapshot.authority == "NONE"
    assert snapshot.blocker is not None
    assert snapshot.blocker["code"] == "UNAUTHORED_TASK"


def test_bo_2_3_planning_next_does_not_discover_recent_canonical_tasks(tmp_path: Path) -> None:
    from aios_renew.brain_context import compose_brain_work_context, resolve_flow

    repo = make_repo(tmp_path)
    source_cards = Path(__file__).resolve().parents[1] / ".ai" / "flow-cards.yaml"
    (repo / ".ai/flow-cards.yaml").write_bytes(source_cards.read_bytes())
    for task_id in ("TASK-321", "TASK-322"):
        (repo / f".ai/tasks/{task_id}.yaml").write_text(
            TASK_SOURCE.replace("TASK-101", task_id), encoding="utf-8")
    roadmap = {"version": 1, "active_track": "brain-optimization-v1", "active_track_status": "ACTIVE",
               "sequence": [{"id": "bo-2-3-canonical-context-pipeline", "status": "NEXT"}]}
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".ai")
    git(repo, "commit", "-m", "canonical tasks with unauthored BO-2/3 NEXT")
    git(repo, "push", "origin", "main")
    observed = observe_brain_sync(repo=repo)
    assert observed.selection_status == "UNAUTHORED_TASK"
    assert observed.selected_task is None and observed.unified_state is None
    assert "correction_subject" not in observed.as_dict()
    assert resolve_flow(compose_brain_work_context(observed)).selected_flow == "TASK_AUTHORING"
    assert observed.next_action == "NONE"


def test_brain_sync_roadmap_lineage_conflict_done_sha(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    main_sha = git(repo, "rev-parse", "refs/heads/main")
    git(repo, "switch", "-c", "diverged")
    (repo / "diverged.txt").write_text("diverged\n", encoding="utf-8")
    git(repo, "add", "diverged.txt")
    git(repo, "commit", "-m", "diverged published commit")
    published_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "main")
    assert published_sha != main_sha
    assert git(repo, "merge-base", main_sha, published_sha) == main_sha
    roadmap = {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "sequence": [
            {
                "id": "diverged-done",
                "status": "DONE",
                "completed_by": {
                    "task_id": "TASK-099",
                    "published_sha": published_sha,
                },
            },
            {
                "id": "step-2",
                "status": "NEXT",
                "task_id": "TASK-101",
            },
        ],
    }
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    roadmap_path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.main_sha == main_sha
    assert snapshot.selection_status == "ROADMAP_LINEAGE_CONFLICT"
    assert snapshot.selected_task is None
    assert snapshot.unified_state is None
    assert snapshot.lifecycle_state == "BLOCKED"
    assert snapshot.next_action == "NONE"
    assert snapshot.authority == "NONE"
    assert snapshot.blocker is not None
    assert snapshot.blocker["code"] == "ROADMAP_LINEAGE_CONFLICT"
    assert snapshot.blocker["item_id"] == "diverged-done"
    assert snapshot.blocker["published_sha"] == published_sha
    assert snapshot.blocker["main_sha"] == main_sha


def test_brain_sync_roadmap_ancestry_observation_error_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    main_sha = git(repo, "rev-parse", "refs/heads/main")
    roadmap = {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "sequence": [{
            "id": "observed-done",
            "status": "DONE",
            "completed_by": {
                "task_id": "TASK-099",
                "published_sha": main_sha,
            },
        }],
    }
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    roadmap_path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")

    import aios_renew.brain_sync as bs_module

    observed = []

    def fail_ancestry(root: Path, ancestor: str, descendant: str) -> bool:
        observed.append((root, ancestor, descendant))
        raise OperatorError("Git ancestry observation failed")

    monkeypatch.setattr(bs_module, "_git_is_ancestor", fail_ancestry)

    with pytest.raises(BrainSyncError, match="cannot observe Git ancestry") as error:
        observe_brain_sync(repo=repo)

    assert observed == [(repo, main_sha, main_sha)]
    assert "observed-done" in str(error.value)
    assert main_sha in str(error.value)
    assert "ROADMAP_LINEAGE_CONFLICT" not in str(error.value)
    assert isinstance(error.value.__cause__, OperatorError)


def test_brain_sync_roadmap_lifecycle_conflict_next_already_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    roadmap = {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "sequence": [
            {
                "id": "step-1",
                "status": "NEXT",
                "task_id": "TASK-101",
            },
        ],
    }
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    roadmap_path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")

    import aios_renew.brain_sync as bs_module
    from aios_renew.unified_state import UnifiedStateObservation

    monkeypatch.setattr(
        bs_module,
        "observe_unified_state",
        lambda _task_id, repo=None: UnifiedStateObservation(
            "TASK-101", 1, "DONE", "DONE", candidate_sha=head, reviewed_sha=head
        ),
    )

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "ROADMAP_LIFECYCLE_CONFLICT"
    assert snapshot.selected_task is None
    assert snapshot.lifecycle_state == "BLOCKED"
    assert snapshot.next_action == "NONE"
    assert snapshot.authority == "NONE"
    assert snapshot.blocker is not None
    assert snapshot.blocker["code"] == "ROADMAP_LIFECYCLE_CONFLICT"


def test_brain_sync_observation_is_strictly_read_only(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    roadmap = {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "sequence": [
            {
                "id": "task-101-execution",
                "status": "NEXT",
                "task_id": "TASK-101",
                "task_revision": 1,
            }
        ],
    }
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    roadmap_path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "add roadmap")
    git(repo, "push", "origin", "main")

    before_status = git(repo, "status", "--porcelain=v1")
    before_head = git(repo, "rev-parse", "HEAD")
    before_refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    state_root = runtime_state_root(repo)
    assert not state_root.exists()

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.run_created is False
    assert snapshot.executor_invoked is False
    assert snapshot.verification_invoked is False
    assert snapshot.state_mutated is False
    assert git(repo, "status", "--porcelain=v1") == before_status
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
    assert not state_root.exists()


def test_brain_sync_exact_detached_candidate_observes_published_main(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.write_text(yaml.safe_dump({
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "sequence": [{
            "id": "task-101-execution", "status": "NEXT",
            "task_id": "TASK-101", "task_revision": 1,
        }],
    }), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "publish roadmap")
    git(repo, "push", "origin", "main")
    published = git(repo, "rev-parse", "HEAD")
    (repo / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    assert candidate != published

    with materialize_verification_subject(
        repo, run_id="RUN-101-001", subject_sha=candidate
    ) as subject:
        control_state_root = runtime_state_root(repo)
        subject_state_root = runtime_state_root(subject)
        assert subject_state_root == subject / ".git" / "aios"
        assert subject_state_root.is_dir()
        assert subject_state_root.resolve() != control_state_root.resolve()
        assert list(subject_state_root.iterdir()) == []
        remote = Path(git(subject, "remote", "get-url", "origin"))
        before = (
            git(subject, "rev-parse", "HEAD"),
            git(subject, "branch", "--show-current"),
            git(subject, "status", "--porcelain=v1"),
            git(subject, "for-each-ref", "--format=%(refname) %(objectname)"),
            git(remote, "for-each-ref", "--format=%(refname) %(objectname)"),
            (subject / ".git" / "config").read_bytes(),
            (subject / ".git" / "index").read_bytes(),
        )
        snapshot = observe_brain_sync(repo=subject)

        assert snapshot.main_sha == published
        assert snapshot.repository["main_sha"] == published
        assert snapshot.repository["remote"] == "origin"
        assert snapshot.selected_task == {"id": "TASK-101", "revision": 1}
        assert snapshot.selection_status == "SELECTED"
        assert snapshot.unified_state is not None
        assert snapshot.lifecycle_state == snapshot.unified_state["lifecycle_state"]
        assert snapshot.next_action == snapshot.unified_state["next_action"]
        assert snapshot.authority == snapshot.unified_state["authority"]
        assert snapshot.next_action == "EXECUTE_PRIMARY"
        assert snapshot.authority == "HUMAN_RUNTIME"
        assert not any((snapshot.run_created, snapshot.executor_invoked,
                        snapshot.verification_invoked, snapshot.state_mutated))
        assert (
            git(subject, "rev-parse", "HEAD"),
            git(subject, "branch", "--show-current"),
            git(subject, "status", "--porcelain=v1"),
            git(subject, "for-each-ref", "--format=%(refname) %(objectname)"),
            git(remote, "for-each-ref", "--format=%(refname) %(objectname)"),
            (subject / ".git" / "config").read_bytes(),
            (subject / ".git" / "index").read_bytes(),
        ) == before
        assert before[1] == ""
        assert subject_state_root.is_dir()
        assert list(subject_state_root.iterdir()) == []


@pytest.mark.parametrize("topology", ["missing", "ambiguous"])
def test_brain_sync_detached_remote_identity_fails_closed(
    tmp_path: Path, topology: str
) -> None:
    repo = make_repo(tmp_path)
    git(repo, "checkout", "--detach")
    if topology == "missing":
        git(repo, "config", "--unset", "branch.main.remote")
    else:
        git(repo, "remote", "add", "other", git(repo, "remote", "get-url", "origin"))
        git(repo, "config", "branch.other.remote", "other")
    assert git(repo, "rev-parse", "refs/heads/main") == git(repo, "rev-parse", "HEAD")
    with pytest.raises(BrainSyncError, match="detached canonical remote main"):
        observe_brain_sync(repo=repo)


@pytest.mark.parametrize("layout", ("ordinary", "linked_absolute", "linked_relative"))
@pytest.mark.parametrize("detached", (False, True))
def test_shared_git_fixture_resolves_head_and_common_refs(tmp_path: Path, layout: str, detached: bool) -> None:
    repo = make_repo(tmp_path)
    expected = git(repo, "rev-parse", "HEAD")
    if layout == "ordinary":
        subject = repo
        git(subject, "checkout", "--quiet", "-b", "fixture-subject")
    else:
        subject = tmp_path / "linked-subject"
        git(repo, "worktree", "add", "--quiet", "-b", "fixture-subject", str(subject))
        assert (subject / ".git").is_file()
        if layout == "linked_relative":
            gitfile = subject / ".git"
            target = Path(gitfile.read_text(encoding="utf-8").strip()[8:])
            if not target.is_absolute():
                target = subject / target
            relative_target = os.path.relpath(target, subject)
            assert not Path(relative_target).is_absolute()
            # Git for Windows hides this file; opening it with truncation can
            # fail even when it is writable. Replace it with a new gitfile.
            replacement = subject / "relative-gitdir"
            replacement.write_text(f"gitdir: {relative_target}\n", encoding="utf-8")
            replacement.replace(gitfile)
            assert gitfile.read_text(encoding="utf-8") == f"gitdir: {relative_target}\n"
    git(repo, "pack-refs", "--all")
    if detached:
        git(subject, "checkout", "--quiet", "--detach")
    assert read_git_ref(subject) == expected
    assert read_git_ref(subject, "refs/heads/main") == expected
    assert read_git_ref(subject, "fixture-subject") == expected

    # Object storage and the writable index/HEAD must resolve to their proper
    # common or per-worktree locations as well as the initial read-only HEAD.
    (subject / "OUTPUT.txt").write_text("linked fixture change\n", encoding="utf-8")
    changed = commit_fixture_state(subject, paths=["OUTPUT.txt"], message="fixture change",
                                   user_name="Fixture", user_email="fixture@example.invalid")
    assert changed != expected
    assert read_git_ref(subject) == changed
    assert git(subject, "show", "HEAD:OUTPUT.txt") == "linked fixture change"
    assert git(subject, "status", "--porcelain") == ""
    assert read_git_ref(subject, "refs/heads/main") == expected
    assert read_git_ref(subject, "fixture-subject") == (expected if detached else changed)


def test_brain_sync_live_repository_smoke() -> None:
    repo = Path(__file__).resolve().parents[1]
    roadmap = yaml.safe_load(
        (repo / ".ai" / "roadmap-state.yaml").read_text(encoding="utf-8")
    )
    next_item_ids = roadmap["next_items"]
    next_items = [
        item for item in roadmap["sequence"] if item.get("id") in next_item_ids
    ]
    assert len(next_items) == 1
    expected_next = next_items[0]
    before_status = git(repo, "status", "--porcelain=v1")
    before_head = git(repo, "rev-parse", "HEAD")
    before_refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")

    snapshot = observe_brain_sync()

    assert snapshot.format == "AIOS_BRAIN_SYNC_SNAPSHOT"
    assert snapshot.version == 1
    assert snapshot.repository["name"] == "trung-via/AIOS-renew"
    assert snapshot.roadmap["active_track"] == roadmap["active_track"]
    assert snapshot.roadmap["active_track_status"] == roadmap["active_track_status"]
    assert snapshot.roadmap["next_items"] == next_item_ids
    assert snapshot.selection_status == "SELECTED"
    assert snapshot.selected_task == {
        "id": expected_next["task_id"],
        "revision": expected_next["task_revision"],
    }
    assert snapshot.unified_state is not None
    assert snapshot.lifecycle_state == snapshot.unified_state["lifecycle_state"]
    assert snapshot.next_action == snapshot.unified_state["next_action"]
    assert snapshot.authority == snapshot.unified_state["authority"]
    assert snapshot.run_created is False
    assert snapshot.executor_invoked is False
    assert snapshot.verification_invoked is False
    assert snapshot.state_mutated is False
    assert git(repo, "status", "--porcelain=v1") == before_status
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
