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


def _write_roadmap(repo: Path, roadmap: dict) -> None:
    """Shared byte-equivalent YAML fixture setup; Git/lifecycle setup stays in each case."""
    roadmap_path = repo / ".ai" / "roadmap-state.yaml"
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    roadmap_path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")


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
    assert "LAST PUBLISHED: unavailable" in snapshot.checkpoint()
    assert "history" not in snapshot.roadmap

    historical = observe_brain_sync(repo=repo, include_history=True)
    assert historical.roadmap["history"] == roadmap["sequence"]
    # Historical enumeration does not promote a positional recency guess.
    assert historical.roadmap["last_published_task"] is None


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

    current = observe_brain_sync(repo=repo)
    assert current.selection_status == "SELECTED"
    assert current.roadmap["last_published_task"] is None
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


@pytest.mark.parametrize("include_history", [False, True])
def test_brain_sync_observation_is_strictly_read_only(tmp_path: Path, include_history: bool) -> None:
    repo = make_repo(tmp_path)
    roadmap = {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "sequence": [
            {"id": "historical-done", "status": "DONE", "completed_by": {
                "task_id": "TASK-099", "published_sha": git(repo, "rev-parse", "HEAD"),
            }},
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

    before_roadmap = (repo / ".ai" / "roadmap-state.yaml").read_bytes()
    snapshot = observe_brain_sync(repo=repo, include_history=include_history)

    assert snapshot.run_created is False
    assert snapshot.executor_invoked is False
    assert snapshot.verification_invoked is False
    assert snapshot.state_mutated is False
    assert git(repo, "status", "--porcelain=v1") == before_status
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
    assert (repo / ".ai" / "roadmap-state.yaml").read_bytes() == before_roadmap
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


def _active_roadmap(published_sha: str) -> dict:
    return {
        "version": 1, "active_track": "hardening", "active_track_status": "ACTIVE",
        "next_items": ["cleanup"],
        "sequence": [
            {"id": "cleanup", "status": "NEXT", "task_id": "TASK-101",
             "task_revision": 1, "return_to": "h4d"},
            {"id": "h4d", "status": "LIVE_EXIT_GATE_PENDING", "task_id": "TASK-101",
             "task_revision": 1, "return_to": "h4e",
             "engineering_completion": {"task_id": "TASK-101", "task_revision": 1,
                                        "published_sha": published_sha, "reviewed_sha": published_sha},
             "live_exit_gate": {"status": "WAITING_FOR_NATURAL_SUBJECT", "contract": "H4D",
                                "published_subject_sha": published_sha},
             "current_live_blocker": {"code": "WAITING", "authority": "HUMAN_BRAIN_PLANNING",
                                      "next_action": "OBSERVE_NATURAL_SUBJECT"}},
            {"id": "h4e", "status": "QUEUED_AFTER_H4D", "return_to": "final-h4b"},
            {"id": "final-h4b", "status": "QUEUED_AFTER_H4E", "return_to": "h5"},
            {"id": "h5", "status": "QUEUED_AFTER_H4B"},
        ],
    }


@pytest.mark.parametrize("reverse_history", [False, True])
def test_active_projection_ignores_historical_order_and_bodies(tmp_path, monkeypatch, reverse_history):
    import aios_renew.brain_sync as bs

    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    roadmap = _active_roadmap(head)
    history = [{"id": f"historical-{index}", "status": "DONE",
                "objective": "historical-body-" + "x" * 4096,
                "completed_by": {"task_id": f"TASK-{index + 200}", "published_sha": "d" * 40}}
               for index in range(100)]
    roadmap["sequence"].extend(reversed(history) if reverse_history else history)
    _write_roadmap(repo, roadmap)
    observed = []

    def ancestry(root, ancestor, descendant):
        observed.append((root, ancestor, descendant))
        assert ancestor == head  # Historical objects must never be observed by default.
        return True

    monkeypatch.setattr(bs, "_git_is_ancestor", ancestry)
    snapshot = observe_brain_sync(repo=repo)
    assert snapshot.selection_status == "SELECTED"
    assert snapshot.roadmap["active_item"]["id"] == "cleanup"
    assert [item["id"] for item in snapshot.roadmap["return_path"]] == ["h4d", "h4e"]
    assert snapshot.roadmap["return_path"][0]["live_exit_gate"]["status"] == "WAITING_FOR_NATURAL_SUBJECT"
    assert snapshot.roadmap["return_path"][0]["current_live_blocker"]["next_action"] == "OBSERVE_NATURAL_SUBJECT"
    assert snapshot.roadmap["return_path_truncated"] is True
    assert snapshot.roadmap["publication"]["status"] == "RELEVANT_ACTIVE_LINEAGE"
    assert snapshot.roadmap["publication"]["item_id"] == "h4d"
    assert snapshot.roadmap["last_published_task"] == "TASK-101"
    assert observed == [(repo, head, head)]
    assert "historical-body" not in snapshot.render()
    assert "historical-" not in snapshot.render()
    assert "history" not in snapshot.roadmap
    assert len(json.dumps(snapshot.roadmap).encode()) < 4096


@pytest.mark.parametrize("reverse_history", [False, True])
def test_historical_order_never_supplies_publication_recency(tmp_path, reverse_history):
    repo = make_repo(tmp_path)
    older = git(repo, "rev-parse", "HEAD")
    (repo / "newer.txt").write_text("newer publication\n", encoding="utf-8")
    git(repo, "add", "newer.txt")
    git(repo, "commit", "-m", "newer publication")
    git(repo, "push", "origin", "main")
    newer = git(repo, "rev-parse", "HEAD")
    history = [{"id": name, "status": "DONE", "completed_by": {
        "task_id": task_id, "published_sha": sha,
    }} for name, task_id, sha in [("newer-publication", "TASK-200", newer), ("older-publication", "TASK-099", older)]]
    roadmap = {"version": 1, "active_track_status": "ACTIVE", "sequence": [
        {"id": "current", "status": "NEXT", "task_id": "TASK-101"},
        *(reversed(history) if reverse_history else history),
    ]}
    _write_roadmap(repo, roadmap)
    snapshot = observe_brain_sync(repo=repo)
    assert snapshot.selection_status == "SELECTED"
    assert snapshot.roadmap["last_published_task"] is None
    assert snapshot.roadmap["publication"] == {"status": "UNAVAILABLE"}


def test_unique_live_gate_is_projected_without_selecting_completed_engineering(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = _active_roadmap(git(repo, "rev-parse", "HEAD"))
    roadmap["next_items"] = []
    roadmap["sequence"] = roadmap["sequence"][1:]
    # A retained older milestone's diagnostic field is not an active-gate pointer.
    roadmap["sequence"].append({"id": "earlier-h4b", "status": "IMPLEMENTATION_HARDENING_REQUIRED",
                                "current_live_blocker": {"code": "OLD_BLOCKER"}})
    _write_roadmap(repo, roadmap)
    snapshot = observe_brain_sync(repo=repo)
    assert snapshot.roadmap["active_item"]["id"] == "h4d"
    assert snapshot.roadmap["active_item"]["status"] == "LIVE_EXIT_GATE_PENDING"
    assert snapshot.selection_status == "NO_NEXT_ITEMS"
    assert snapshot.next_action == snapshot.authority == "NONE"
    assert snapshot.selected_task is None
    assert [item["id"] for item in snapshot.roadmap["return_path"]] == ["h4e"]
    assert snapshot.roadmap["return_path_truncated"] is True


@pytest.mark.parametrize("fault", ["missing", "duplicate", "cycle", "duplicate_active", "boundary_missing"])
def test_active_return_identity_fails_closed(tmp_path, fault):
    repo = make_repo(tmp_path)
    roadmap = _active_roadmap(git(repo, "rev-parse", "HEAD"))
    if fault == "missing":
        roadmap["sequence"][0]["return_to"] = "absent"
    elif fault == "duplicate":
        roadmap["sequence"].append(dict(roadmap["sequence"][1]))
    elif fault == "cycle":
        roadmap["sequence"][1]["return_to"] = "cleanup"
    elif fault == "duplicate_active":
        roadmap["sequence"].append({"id": "cleanup", "status": "DONE"})
    else:
        roadmap["sequence"][2]["return_to"] = "absent"
    _write_roadmap(repo, roadmap)
    if fault == "duplicate_active":
        snapshot = observe_brain_sync(repo=repo)
        assert snapshot.selection_status == "AMBIGUOUS_NEXT"
        assert snapshot.next_action == snapshot.authority == "NONE"
    else:
        with pytest.raises(BrainSyncError, match="return_to|boundary pointer"):
            observe_brain_sync(repo=repo)


@pytest.mark.parametrize("fault", ["blob", "worktree", "revision", "return_revision", "return_task_missing"])
def test_active_exact_task_identity_fails_closed(tmp_path, fault):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    roadmap = _active_roadmap(head)
    roadmap["sequence"][0].update(task_blob_sha=git(repo, "rev-parse", "HEAD:.ai/tasks/TASK-101.yaml"),
                                   task_author_commit_sha=head)
    if fault == "blob":
        roadmap["sequence"][0]["task_blob_sha"] = "a" * 40
    elif fault == "worktree":
        (repo / ".ai/tasks/TASK-101.yaml").write_text(TASK_SOURCE.replace("one deterministic", "changed deterministic"), encoding="utf-8")
    elif fault == "revision":
        roadmap["sequence"][0]["task_revision"] = 2
    elif fault == "return_revision":
        roadmap["sequence"][1]["task_revision"] = 2
        roadmap["sequence"][1]["engineering_completion"]["task_revision"] = 2
    else:
        roadmap["sequence"][1]["task_id"] = "TASK-999"
        roadmap["sequence"][1]["engineering_completion"]["task_id"] = "TASK-999"
    _write_roadmap(repo, roadmap)
    snapshot = observe_brain_sync(repo=repo)
    assert snapshot.selection_status == "ROADMAP_LINEAGE_CONFLICT"
    assert snapshot.next_action == snapshot.authority == "NONE"
    assert snapshot.selected_task is None


@pytest.mark.parametrize("fault", ["foreign_author", "author_blob", "main_blob"])
def test_active_author_commit_and_main_identity_fail_closed(tmp_path, fault):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    task_path = ".ai/tasks/TASK-101.yaml"
    if fault == "foreign_author":
        git(repo, "switch", "-c", "foreign")
        (repo / "foreign.txt").write_text("foreign\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "foreign author")
        author = git(repo, "rev-parse", "HEAD")
        git(repo, "switch", "main")
    else:
        author = head
        changed_source = TASK_SOURCE.replace("one deterministic", "new deterministic")
        (repo / task_path).write_text(changed_source, encoding="utf-8")
        if fault == "author_blob":
            git(repo, "add", task_path)
            git(repo, "commit", "-m", "changed task blob without revision")
            git(repo, "push", "origin", "main")
    roadmap = {"sequence": [{"id": "current", "status": "NEXT", "task_id": "TASK-101",
                             "task_revision": 1, "task_author_commit_sha": author}]}
    _write_roadmap(repo, roadmap)
    snapshot = observe_brain_sync(repo=repo)
    assert snapshot.selection_status == "ROADMAP_LINEAGE_CONFLICT"
    assert snapshot.next_action == snapshot.authority == "NONE"
    assert snapshot.selected_task is None


def test_directly_relevant_done_item_projects_pointers_and_hydrates_body_only_on_demand(tmp_path):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    done = {"id": "relevant-done", "status": "DONE", "objective": "exact historical body",
            "arbitrary_history": {"retained": ["whole", "body"]},
            "completed_by": {"task_id": "TASK-099", "published_sha": head}}
    _write_roadmap(repo, {"sequence": [
        {"id": "current", "status": "NEXT", "task_id": "TASK-101", "return_to": done["id"]}, done,
    ]})
    current = observe_brain_sync(repo=repo)
    assert current.roadmap["last_published_task"] == "TASK-099"
    assert current.roadmap["return_path"] == [{"id": "relevant-done", "status": "DONE",
                                             "completed_by": done["completed_by"]}]
    assert "exact historical body" not in current.render()
    historical = observe_brain_sync(repo=repo, include_history=True)
    assert historical.roadmap["history"] == [done]


def test_task_id_next_pointer_preserves_sequence_revision(tmp_path):
    repo = make_repo(tmp_path)
    _write_roadmap(repo, {"next_items": ["TASK-101"], "sequence": [
        {"id": "current", "status": "NEXT", "task_id": "TASK-101", "task_revision": 2},
    ]})
    snapshot = observe_brain_sync(repo=repo)
    assert snapshot.selection_status == "ROADMAP_LINEAGE_CONFLICT"
    assert snapshot.next_action == snapshot.authority == "NONE"


def test_mapping_next_pointer_cannot_override_sequence_identity(tmp_path):
    repo = make_repo(tmp_path)
    _write_roadmap(repo, {"next_items": [{"id": "current", "task_revision": 2}], "sequence": [
        {"id": "current", "status": "NEXT", "task_id": "TASK-101", "task_revision": 1},
    ]})
    snapshot = observe_brain_sync(repo=repo)
    assert snapshot.selection_status == "AMBIGUOUS_NEXT"
    assert snapshot.next_action == snapshot.authority == "NONE"


@pytest.mark.parametrize("fault", ["gate_sha", "reviewed_sha", "task_id"])
def test_relevant_publication_conflicts_fail_closed(tmp_path, fault):
    repo = make_repo(tmp_path)
    roadmap = _active_roadmap(git(repo, "rev-parse", "HEAD"))
    gate = roadmap["sequence"][1]
    if fault == "gate_sha":
        gate["live_exit_gate"]["published_subject_sha"] = "a" * 40
    elif fault == "reviewed_sha":
        gate["engineering_completion"]["reviewed_sha"] = "a" * 40
    else:
        gate["engineering_completion"]["task_id"] = "TASK-999"
    _write_roadmap(repo, roadmap)
    with pytest.raises(BrainSyncError, match="publication|reviewed/published"):
        observe_brain_sync(repo=repo)


@pytest.mark.parametrize("outcome", ["conflict", "observation_error"])
def test_active_publication_ancestry_still_fails_closed(tmp_path, monkeypatch, outcome):
    import aios_renew.brain_sync as bs

    repo = make_repo(tmp_path)
    _write_roadmap(repo, _active_roadmap(git(repo, "rev-parse", "HEAD")))

    def ancestry(*args):
        if outcome == "observation_error":
            raise OperatorError("Git unavailable")
        return False

    monkeypatch.setattr(bs, "_git_is_ancestor", ancestry)
    if outcome == "observation_error":
        with pytest.raises(BrainSyncError, match="cannot observe Git ancestry"):
            observe_brain_sync(repo=repo)
    else:
        snapshot = observe_brain_sync(repo=repo)
        assert snapshot.selection_status == "ROADMAP_LINEAGE_CONFLICT"
        assert snapshot.blocker["item_id"] == "h4d"
        assert snapshot.next_action == snapshot.authority == "NONE"


@pytest.mark.parametrize("fault", ["too_many_next", "oversized_active", "ambiguous_gate"])
def test_active_projection_bounds_and_gate_ambiguity_fail_closed(tmp_path, fault):
    repo = make_repo(tmp_path)
    roadmap = _active_roadmap(git(repo, "rev-parse", "HEAD"))
    if fault == "too_many_next":
        roadmap["next_items"] = [f"TASK-{index + 101}" for index in range(17)]
    elif fault == "oversized_active":
        roadmap["sequence"][0]["objective"] = "x" * 2049
    else:
        roadmap["next_items"] = []
        roadmap["sequence"] = roadmap["sequence"][1:] + [{"id": "other", "status": "BLOCKED"}]
    _write_roadmap(repo, roadmap)
    with pytest.raises(BrainSyncError, match="too many|exceeds|ambiguous active"):
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
