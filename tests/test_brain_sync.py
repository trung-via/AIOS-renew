# Tests for AIOS Brain Sync Snapshot observation boundary.
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import pytest

import aios_renew.operator as operator_module
from aios_renew.brain_sync import (
    BrainSyncError,
    BrainSyncSnapshot,
    observe_brain_sync,
    observe_brain_sync_snapshot,
)
from aios_renew.operator import runtime_state_root
from aios_renew.review_transport import (
    RemoteLifecycleReview,
    RemoteLifecycleTerminal,
    RemoteTaskLifecycle,
)
from tests.operator_test_support import (
    TASK_SOURCE,
    git,
    make_repo,
)


def _stub_remote_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    lifecycle: RemoteTaskLifecycle,
) -> None:
    @contextmanager
    def observer(_root: Path):
        yield repo

    monkeypatch.setattr(operator_module, "_remote_observation_repository", observer)
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_task_lifecycle",
        lambda *_args, **_kwargs: lifecycle,
    )


def test_brain_sync_ready_single_next_rehydration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    roadmap_content = f"""version: 1
active_track: control-plane-closure
active_track_status: IN_PROGRESS
next_items:
  - TASK-101
sequence:
  - id: baseline-item
    status: DONE
    completed_by:
      task_id: TASK-100
      published_sha: "{head}"
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["format"] == "AIOS_BRAIN_SYNC_SNAPSHOT"
    assert data["version"] == 1
    assert data["kind"] == "BRAIN_SYNC_SNAPSHOT"
    assert data["repository"]["head_sha"] == head
    assert data["repository"]["main_sha"] == head
    assert data["roadmap"]["present"] is True
    assert data["roadmap"]["active_track"] == "control-plane-closure"
    assert data["roadmap"]["active_track_status"] == "IN_PROGRESS"
    assert data["roadmap"]["next_items"] == ["TASK-101"]
    assert data["roadmap"]["last_published"]["task_id"] == "TASK-100"
    assert data["roadmap"]["last_published"]["published_sha"] == head
    assert data["task"] == {"id": "TASK-101", "revision": 1}
    assert data["selection_mode"] == "ROADMAP_NEXT"
    assert data["lifecycle_state"] == "READY"
    assert data["next_action"] == "EXECUTE_PRIMARY"
    assert data["authority"] == "HUMAN_RUNTIME"
    assert data["blocker"] is None
    assert data["run_created"] is False
    assert data["executor_invoked"] is False
    assert data["verification_invoked"] is False
    assert data["state_mutated"] is False

    # Check rendered forms
    rendered_json = json.loads(snapshot.render())
    assert rendered_json == data
    checkpoint = snapshot.render_checkpoint()
    assert "PROJECT: AIOS-renew" in checkpoint
    assert f"MAIN: {head}" in checkpoint
    assert "LAST PUBLISHED: TASK-100" in checkpoint
    assert "AUTHORED NEXT TASK: TASK-101" in checkpoint
    assert "STATE: READY" in checkpoint


def test_brain_sync_completed_track_no_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    roadmap_content = """version: 1
active_track: control-plane-closure
active_track_status: COMPLETE
next_items: []
sequence: []
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["roadmap"]["active_track_status"] == "COMPLETE"
    assert data["task"] is None
    assert data["lifecycle_state"] == "BLOCKED"
    assert data["next_action"] == "NONE"
    assert data["authority"] == "NONE"
    assert data["blocker"]["code"] == "COMPLETED_TRACK_NO_NEXT"
    assert "STATE: BLOCKED" in snapshot.render_checkpoint()


def test_brain_sync_missing_roadmap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["roadmap"]["present"] is False
    assert data["task"] is None
    assert data["lifecycle_state"] == "BLOCKED"
    assert data["next_action"] == "NONE"
    assert data["authority"] == "NONE"
    assert data["blocker"]["code"] == "MISSING_ROADMAP"
    assert "STATE: BLOCKED" in snapshot.render_checkpoint()


def test_brain_sync_ambiguous_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    roadmap_content = """version: 1
active_track: control-plane-closure
active_track_status: IN_PROGRESS
next_items:
  - TASK-101
  - TASK-102
sequence: []
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["task"] is None
    assert data["lifecycle_state"] == "BLOCKED"
    assert data["next_action"] == "NONE"
    assert data["authority"] == "NONE"
    assert data["blocker"]["code"] == "AMBIGUOUS_NEXT_ITEMS"
    assert data["blocker"]["candidates"] == ["TASK-101", "TASK-102"]
    assert "STATE: BLOCKED" in snapshot.render_checkpoint()


def test_brain_sync_unauthored_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    roadmap_content = """version: 1
active_track: control-plane-closure
active_track_status: IN_PROGRESS
next_items:
  - TASK-999
sequence: []
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["task"] is None
    assert data["lifecycle_state"] == "BLOCKED"
    assert data["next_action"] == "NONE"
    assert data["authority"] == "NONE"
    assert data["blocker"]["code"] == "UNAUTHORED_NEXT_TASK"
    assert data["blocker"]["task_id"] == "TASK-999"
    assert "STATE: BLOCKED" in snapshot.render_checkpoint()


def test_brain_sync_roadmap_lifecycle_conflict_nonexistent_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    roadmap_content = """version: 1
active_track: control-plane-closure
active_track_status: IN_PROGRESS
next_items:
  - TASK-101
sequence:
  - id: fake-baseline
    status: DONE
    completed_by:
      task_id: TASK-099
      published_sha: "0123456789abcdef0123456789abcdef01234567"
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["task"] is None
    assert data["lifecycle_state"] == "BLOCKED"
    assert data["blocker"]["code"] == "ROADMAP_LINEAGE_CONTRADICTION"
    assert "does not exist in repository" in data["blocker"]["message"]


def test_brain_sync_roadmap_lifecycle_conflict_task_already_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    from aios_renew.unified_state import UnifiedStateObservation
    monkeypatch.setattr(
        "aios_renew.brain_sync.observe_unified_state",
        lambda *_args, **_kwargs: UnifiedStateObservation(
            task_id="TASK-101",
            task_revision=1,
            lifecycle_state="DONE",
            next_action="DONE",
        ),
    )

    roadmap_content = """version: 1
active_track: control-plane-closure
active_track_status: IN_PROGRESS
next_items:
  - TASK-101
sequence: []
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["task"] is None
    assert data["lifecycle_state"] == "BLOCKED"
    assert data["blocker"]["code"] == "ROADMAP_LINEAGE_CONTRADICTION"
    assert "already DONE in engineering lineage" in data["blocker"]["message"]


def test_brain_sync_roadmap_conflict_task_already_marked_done_in_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    roadmap_content = f"""version: 1
active_track: control-plane-closure
active_track_status: IN_PROGRESS
next_items:
  - TASK-101
sequence:
  - id: baseline-item
    status: DONE
    completed_by:
      task_id: TASK-101
      published_sha: "{head}"
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["task"] is None
    assert data["lifecycle_state"] == "BLOCKED"
    assert data["blocker"]["code"] == "ROADMAP_LINEAGE_CONTRADICTION"
    assert "already marked DONE in roadmap sequence" in data["blocker"]["message"]


def test_brain_sync_observation_performs_no_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    roadmap_content = """version: 1
active_track: control-plane-closure
active_track_status: IN_PROGRESS
next_items:
  - TASK-101
sequence: []
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")
    git(repo, "add", ".ai/roadmap-state.yaml")
    git(repo, "commit", "-m", "add roadmap")
    new_head = git(repo, "rev-parse", "HEAD")
    lifecycle_new = RemoteTaskLifecycle(new_head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle_new)

    before_status = git(repo, "status", "--porcelain=v1")
    before_head = git(repo, "rev-parse", "HEAD")
    before_refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    before_objects = git(repo, "count-objects", "-v")

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.run_created is False
    assert snapshot.executor_invoked is False
    assert snapshot.verification_invoked is False
    assert snapshot.state_mutated is False
    assert git(repo, "status", "--porcelain=v1") == before_status
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
    assert git(repo, "count-objects", "-v") == before_objects
    assert not runtime_state_root(repo).exists()


def test_brain_sync_explicit_task_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    # Roadmap has zero NEXT items and is complete
    roadmap_content = """version: 1
active_track: control-plane-closure
active_track_status: COMPLETE
next_items: []
sequence: []
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")

    # Explicit override for TASK-101
    snapshot = observe_brain_sync(repo=repo, task_id="TASK-101")
    data = snapshot.as_dict()

    assert data["selection_mode"] == "EXPLICIT"
    assert data["task"] == {"id": "TASK-101", "revision": 1}
    assert data["lifecycle_state"] == "READY"
    assert data["next_action"] == "EXECUTE_PRIMARY"
    assert data["authority"] == "HUMAN_RUNTIME"
    assert data["blocker"] is None


def test_brain_sync_malformed_roadmap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    (repo / ".ai" / "roadmap-state.yaml").write_text("not: a: valid: yaml: [", encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["roadmap"]["present"] is True
    assert data["lifecycle_state"] == "BLOCKED"
    assert data["blocker"]["code"] == "MALFORMED_ROADMAP"


def test_brain_sync_sequence_next_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_remote_lifecycle(monkeypatch, repo, lifecycle)

    roadmap_content = """version: 1
active_track: control-plane-closure
active_track_status: IN_PROGRESS
sequence:
  - id: test-item
    status: NEXT
    task_id: TASK-101
"""
    (repo / ".ai" / "roadmap-state.yaml").write_text(roadmap_content, encoding="utf-8")

    snapshot = observe_brain_sync(repo=repo)
    data = snapshot.as_dict()

    assert data["task"] == {"id": "TASK-101", "revision": 1}
    assert data["next_action"] == "EXECUTE_PRIMARY"
    assert data["authority"] == "HUMAN_RUNTIME"
    assert data["blocker"] is None
