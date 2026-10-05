"""Deterministic tests for AIOS Brain Sync observation snapshot."""

import json
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
from tests.operator_test_support import (
    TASK_SOURCE,
    git,
    make_repo,
)


def _write_roadmap(repo: Path, roadmap: dict) -> Path:
    """Shared setup only; each named test retains its own semantic assertions."""
    path = repo / ".ai" / "roadmap-state.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    return path


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
    _write_roadmap(repo, roadmap)
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
    _write_roadmap(repo, roadmap)

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "COMPLETED_TRACK"
    assert snapshot.selected_task is None
    assert snapshot.unified_state is None
    assert snapshot.lifecycle_state == "COMPLETE"
    assert snapshot.next_action == "NONE"
    assert snapshot.authority == "NONE"
    assert snapshot.blocker is None
    assert snapshot.roadmap["active_track_status"] == "COMPLETE"
    assert snapshot.roadmap["last_published_task"] is None
    assert snapshot.roadmap["publication"] == {"status": "UNAVAILABLE"}


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
    _write_roadmap(repo, roadmap)

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
    _write_roadmap(repo, roadmap)

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "UNAUTHORED_TASK"
    assert snapshot.selected_task is None
    assert snapshot.unified_state is None
    assert snapshot.lifecycle_state == "BLOCKED"
    assert snapshot.next_action == "NONE"
    assert snapshot.authority == "NONE"
    assert snapshot.blocker is not None
    assert snapshot.blocker["code"] == "UNAUTHORED_TASK"


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
    _write_roadmap(repo, roadmap)

    snapshot = observe_brain_sync(repo=repo, include_history=True)

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
    _write_roadmap(repo, roadmap)

    import aios_renew.brain_sync as bs_module

    observed = []

    def fail_ancestry(root: Path, ancestor: str, descendant: str) -> bool:
        observed.append((root, ancestor, descendant))
        raise OperatorError("Git ancestry observation failed")

    monkeypatch.setattr(bs_module, "_git_is_ancestor", fail_ancestry)

    with pytest.raises(BrainSyncError, match="cannot observe Git ancestry") as error:
        observe_brain_sync(repo=repo, include_history=True)

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
    _write_roadmap(repo, roadmap)

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
    _write_roadmap(repo, roadmap)
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


def _current_roadmap() -> dict:
    return {
        "version": 1, "active_track": "hardening", "active_track_status": "ACTIVE",
        "next_items": ["cleanup"],
        "sequence": [{"id": "cleanup", "status": "NEXT", "task_id": "TASK-101",
                      "task_revision": 1, "return_to": "live-gate"},
                     {"id": "live-gate", "status": "LIVE_EXIT_GATE_PENDING",
                      "return_to": "queued", "live_exit_gate": {
                          "authority": "HUMAN_BRAIN_PLANNING", "status": "WAITING",
                          "contract": "LIVE_EXIT_V1"},
                      "current_live_blocker": {"code": "WAITING_FOR_REAL_SUBJECT",
                                               "next_action": "OBSERVE_REAL_SUBJECT"}},
                     {"id": "queued", "status": "QUEUED_AFTER_GATE", "return_to": "later"},
                     {"id": "later", "status": "QUEUED_AFTER_QUEUED"}],
    }


def test_default_hydration_skips_unrelated_done_ancestry_and_bodies(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    roadmap = _current_roadmap()
    roadmap["sequence"].extend({
        "id": f"historical-{index}", "status": "DONE", "objective": "history" * 1000,
        "completed_by": {"task_id": "TASK-099", "published_sha": "f" * 40},
    } for index in range(200))
    path = _write_roadmap(repo, roadmap)
    before = path.read_bytes()
    monkeypatch.setattr("aios_renew.brain_sync._git_is_ancestor",
                        lambda *args: pytest.fail("unrelated historical ancestry requested"))

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "SELECTED"
    assert snapshot.roadmap["active_item"]["id"] == "cleanup"
    assert [item["id"] for item in snapshot.roadmap["return_path"]] == ["live-gate", "queued"]
    gate = snapshot.roadmap["return_path"][0]
    assert gate["live_exit_gate"]["status"] == "WAITING"
    assert gate["current_live_blocker"]["code"] == "WAITING_FOR_REAL_SUBJECT"
    assert snapshot.roadmap["return_path_truncated"] is True
    assert snapshot.roadmap["return_path"][-1]["return_to"] == "later"
    assert "history" not in snapshot.roadmap
    assert "historical-" not in snapshot.render()
    assert len(snapshot.render().encode()) < 8192
    assert snapshot.roadmap["last_published_task"] is None
    assert "LAST PUBLISHED: unavailable" in snapshot.checkpoint()
    assert path.read_bytes() == before


@pytest.mark.parametrize("reverse", [False, True], ids=["old-last", "old-first"])
def test_publication_relevance_is_independent_of_historical_done_order(tmp_path, reverse):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    roadmap = _current_roadmap()
    gate = roadmap["sequence"][1]
    gate.update(task_id="TASK-101", task_revision=1, engineering_completion={
        "task_id": "TASK-101", "task_revision": 1, "published_sha": head, "reviewed_sha": head,
    })
    gate["live_exit_gate"]["published_subject_sha"] = head
    done = [{"id": "newer-done", "status": "DONE", "completed_by": {
        "task_id": "TASK-100", "published_sha": head}},
        {"id": "older-done", "status": "DONE", "completed_by": {
            "task_id": "TASK-099", "published_sha": head}}]
    roadmap["sequence"].extend(reversed(done) if reverse else done)
    _write_roadmap(repo, roadmap)

    ordinary = observe_brain_sync(repo=repo)
    historical = observe_brain_sync(repo=repo, include_history=True)

    assert ordinary.roadmap["last_published_task"] == "TASK-101"
    assert ordinary.roadmap["publication"] == {
        "status": "RELEVANT_ACTIVE_LINEAGE", "item_id": "live-gate", "task_id": "TASK-101",
        "task_revision": 1, "published_sha": head, "reviewed_sha": head,
    }
    assert historical.roadmap["publication"] == ordinary.roadmap["publication"]
    assert historical.roadmap["history"] == (list(reversed(done)) if reverse else done)


def test_history_is_on_demand_and_does_not_supply_a_recency_guess(tmp_path):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    roadmap = _current_roadmap()
    done = {"id": "old", "status": "DONE", "objective": "Exact retained historical body",
            "completed_by": {"task_id": "TASK-099", "published_sha": head},
            "diagnostic_details": {"retained": [1, 2, 3]}}
    roadmap["sequence"].append(done)
    path = _write_roadmap(repo, roadmap)
    before = path.read_bytes()

    ordinary = observe_brain_sync(repo=repo)
    historical = observe_brain_sync(repo=repo, include_history=True)

    assert "history" not in ordinary.roadmap
    assert historical.roadmap["history"] == [done]
    assert historical.roadmap["last_published_task"] is None
    assert historical.roadmap["publication"] == {"status": "UNAVAILABLE"}
    assert json.loads(historical.render())["roadmap"]["history"] == [done]
    assert path.read_bytes() == before


@pytest.mark.parametrize("status", ["BLOCKED", "LIVE_EXIT_GATE_PENDING"])
def test_active_gate_without_next_remains_observation_only(tmp_path, status):
    repo = make_repo(tmp_path)
    roadmap = _current_roadmap()
    roadmap["next_items"] = []
    roadmap["sequence"].pop(0)
    roadmap["sequence"][0]["status"] = status
    _write_roadmap(repo, roadmap)

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.roadmap["active_item"]["id"] == "live-gate"
    assert snapshot.roadmap["active_item"]["status"] == status
    assert snapshot.selection_status == "NO_NEXT_ITEMS"
    assert snapshot.selected_task is None
    assert snapshot.next_action == snapshot.authority == "NONE"


@pytest.mark.parametrize("fault", [
    "duplicate-active-id", "duplicate-return-id", "missing-return", "cycle", "ambiguous-gates",
    "stale-inline-revision", "done-as-next", "conflicting-publication", "reviewed-sha",
    "related-task-revision", "oversized-active-field", "oversized-next-set", "boundary-cycle",
])
def test_active_projection_conflicts_fail_closed(tmp_path, fault):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    roadmap = _current_roadmap()
    active, gate = roadmap["sequence"][:2]
    if fault == "duplicate-active-id":
        roadmap["sequence"].append(dict(active))
    elif fault == "duplicate-return-id":
        roadmap["sequence"].append(dict(gate))
    elif fault == "missing-return":
        active["return_to"] = "missing"
    elif fault == "cycle":
        gate["return_to"] = "cleanup"
    elif fault == "ambiguous-gates":
        roadmap["next_items"] = []
        roadmap["sequence"].pop(0)
        roadmap["sequence"].append({"id": "other-gate", "status": "BLOCKED"})
    elif fault == "stale-inline-revision":
        roadmap["next_items"] = [{"id": "cleanup", "task_revision": 2}]
    elif fault == "done-as-next":
        active["status"] = "DONE"
    elif fault in ("conflicting-publication", "reviewed-sha"):
        gate["engineering_completion"] = {"task_id": "TASK-101", "published_sha": head,
                                          "reviewed_sha": "f" * 40 if fault == "reviewed-sha" else head}
        if fault == "conflicting-publication":
            gate["live_exit_gate"]["published_subject_sha"] = "f" * 40
    elif fault == "related-task-revision":
        gate.update(task_id="TASK-101", task_revision=2)
    elif fault == "oversized-active-field":
        active["objective"] = "x" * 2049
    elif fault == "oversized-next-set":
        roadmap["next_items"] = [f"TASK-{index}" for index in range(17)]
    elif fault == "boundary-cycle":
        roadmap["sequence"][2]["return_to"] = "cleanup"
    _write_roadmap(repo, roadmap)

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.blocker is not None
    assert snapshot.selection_status in ("ROADMAP_LINEAGE_CONFLICT", "AMBIGUOUS_NEXT")
    assert snapshot.next_action == snapshot.authority == "NONE"
    assert snapshot.selected_task is None


@pytest.mark.parametrize("fault", ["valid", "blob", "main-blob", "author-commit", "unavailable-author-commit", "revision"])
def test_selected_exact_task_pointers_fail_closed(tmp_path, fault):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    blob = git(repo, "rev-parse", f"{head}:.ai/tasks/TASK-101.yaml")
    roadmap = _current_roadmap()
    active = roadmap["sequence"][0]
    active.update(task_blob_sha=blob, task_author_commit_sha=head)
    if fault == "blob":
        active["task_blob_sha"] = "f" * 40
    elif fault == "main-blob":
        path = repo / ".ai/tasks/TASK-101.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        active["task_blob_sha"] = git(repo, "hash-object", ".ai/tasks/TASK-101.yaml")
    elif fault == "author-commit":
        git(repo, "switch", "-c", "unpublished")
        (repo / "unpublished.txt").write_text("unpublished\n", encoding="utf-8")
        git(repo, "add", "unpublished.txt")
        git(repo, "commit", "-m", "unpublished authoring pointer")
        active["task_author_commit_sha"] = git(repo, "rev-parse", "HEAD")
        git(repo, "switch", "main")
    elif fault == "unavailable-author-commit":
        active["task_author_commit_sha"] = "f" * 40
    elif fault == "revision":
        active["task_revision"] = 2
    _write_roadmap(repo, roadmap)

    if fault == "unavailable-author-commit":
        with pytest.raises(BrainSyncError, match="cannot observe Git ancestry"):
            observe_brain_sync(repo=repo)
        return
    snapshot = observe_brain_sync(repo=repo)

    if fault == "valid":
        assert snapshot.selection_status == "SELECTED"
        assert snapshot.selected_task == {"id": "TASK-101", "revision": 1}
    else:
        assert snapshot.selection_status == "ROADMAP_LINEAGE_CONFLICT"
        assert snapshot.next_action == snapshot.authority == "NONE"


def test_active_gate_publication_must_be_on_canonical_main(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "switch", "-c", "unpublished")
    (repo / "candidate.txt").write_text("unpublished\n", encoding="utf-8")
    git(repo, "add", "candidate.txt")
    git(repo, "commit", "-m", "unpublished gate")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "main")
    roadmap = _current_roadmap()
    roadmap["sequence"][1]["engineering_completion"] = {"published_sha": sha}
    _write_roadmap(repo, roadmap)

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "ROADMAP_LINEAGE_CONFLICT"
    assert snapshot.blocker["item_id"] == "live-gate"
    assert snapshot.blocker["published_sha"] == sha
    assert snapshot.next_action == snapshot.authority == "NONE"


def test_cleanup_retention_dependency_witnesses():
    """Runtime-consumable positive witnesses: these candidates are not deletable."""
    repo = Path(__file__).resolve().parents[1]
    diagnostics = (
        "aios_full_suite_contention_diagnostic", "aios_git_fixture_push_root_decomposition_diagnostic",
        "aios_git_fixture_push_threshold_diagnostic", "aios_ingress_metadata_identity_diagnostic",
        "aios_parallel_git_fixture_push_diagnostic", "aios_residual_context_attribution_diagnostic",
        "aios_serial_context_diagnostic", "aios_stable_failure_cause_diagnostic",
        "aios_stable_failure_detail_diagnostic", "bp_v4_parallel_diagnostic",
    )
    for module in diagnostics:
        assert (repo / f"scripts/{module}.py").is_file()
        retained_test = repo / f"tests/test_{module}.py"
        assert retained_test.is_file()
        assert f"from scripts import {module}" in retained_test.read_text(encoding="utf-8")
    assert (repo / "scripts/aios_origin_capture_probe.py").is_file()
    assert "scripts/aios_origin_capture_probe.py" in (
        repo / "docs/AIOS-H4C0-ORIGIN-CAPTURE-CONFORMANCE-v1.md").read_text(encoding="utf-8")
    policy = (repo / ".ai/brain-wake-carriers.yaml").read_text(encoding="utf-8")
    for workflow in ("aios-brain-wakeup", "aios-brain-repair-wakeup",
                     "aios-self-hosted-wakeup", "aios-self-hosted-repair-wakeup"):
        assert (repo / f".github/workflows/{workflow}.yml").is_file()
        assert f".github/workflows/{workflow}.yml" in policy
    carrier = (repo / ".github/workflows/aios-issue-carrier.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/aios-brain-wakeup.yml" in carrier
    assert "uses: ./.github/workflows/aios-brain-repair-wakeup.yml" in carrier
