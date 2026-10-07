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
    project_active_planning,
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


def _publish_roadmap(repo: Path, roadmap: dict | str) -> str:
    path = repo / ".ai" / "roadmap-state.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(roadmap if isinstance(roadmap, str) else yaml.safe_dump(roadmap), encoding="utf-8")
    git(repo, "add", ".ai/roadmap-state.yaml")
    git(repo, "commit", "-m", "fixture canonical roadmap")
    git(repo, "push", "origin", "main")
    return git(repo, "rev-parse", "HEAD")


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
    _publish_roadmap(repo, roadmap)
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
    _publish_roadmap(repo, roadmap)

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
    _publish_roadmap(repo, roadmap)

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
    _publish_roadmap(repo, roadmap)

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
    _publish_roadmap(repo, roadmap)

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.main_sha == git(repo, "rev-parse", "HEAD")
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
    assert snapshot.blocker["main_sha"] == snapshot.main_sha


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
    _publish_roadmap(repo, roadmap)

    import aios_renew.brain_sync as bs_module

    observed = []

    def fail_ancestry(root: Path, ancestor: str, descendant: str) -> bool:
        observed.append((root, ancestor, descendant))
        raise OperatorError("Git ancestry observation failed")

    monkeypatch.setattr(bs_module, "_git_is_ancestor", fail_ancestry)

    with pytest.raises(BrainSyncError, match="cannot observe Git ancestry") as error:
        observe_brain_sync(repo=repo)

    assert observed == [(repo, main_sha, git(repo, "rev-parse", "HEAD"))]
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
    _publish_roadmap(repo, roadmap)

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
    _publish_roadmap(repo, roadmap)

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
    _publish_roadmap(repo, {
        "version": 1,
        "active_track": "control-plane-closure",
        "active_track_status": "ACTIVE",
        "sequence": [{
            "id": "task-101-execution", "status": "NEXT",
            "task_id": "TASK-101", "task_revision": 1,
        }],
    })
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
    assert snapshot.roadmap["next_proof"]["status"] == "UNIQUE"
    assert snapshot.roadmap["effective_next"] == expected_next
    if expected_next.get("task_id"):
        assert snapshot.selection_status == "SELECTED"
        assert snapshot.selected_task == {"id": expected_next["task_id"], "revision": expected_next["task_revision"]}
        assert snapshot.unified_state is not None
        assert snapshot.lifecycle_state == snapshot.unified_state["lifecycle_state"]
        assert snapshot.next_action == snapshot.unified_state["next_action"]
        assert snapshot.authority == snapshot.unified_state["authority"]
    else:
        assert snapshot.selection_status == "UNAUTHORED_TASK"
        assert snapshot.selected_task is None and snapshot.unified_state is None
        assert snapshot.next_action == "TASK_AUTHORING"
        assert snapshot.lifecycle_state == "PLANNING"
        assert snapshot.authority == "HUMAN_BRAIN_PLANNING"
        assert snapshot.blocker is None
    assert snapshot.run_created is False
    assert snapshot.executor_invoked is False
    assert snapshot.verification_invoked is False
    assert snapshot.state_mutated is False
    assert git(repo, "status", "--porcelain=v1") == before_status
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs


@pytest.mark.parametrize("selected_status", ["DONE", "BLOCKED", "QUEUED"])
def test_task319_sequence_next_vs_next_items_split_brain(selected_status):
    source = {"active_track_status": "ACTIVE", "next_items": ["TASK-319"], "sequence": [
        {"id": "task316", "status": "NEXT", "task_id": "TASK-316", "human_decision": "resume correction"},
        {"id": "task319", "status": selected_status, "task_id": "TASK-319"},
    ]}
    projected = project_active_planning(source)
    assert projected["effective_next"] is None
    assert projected["next_proof"]["status"] == "CONFLICT"
    assert "NEXT_ITEMS_SEQUENCE_MISMATCH" in projected["next_proof"]["conflicts"]
    assert projected["competing_next_controls"] == [source["sequence"][0]]


def test_next_items_cannot_resurrect_a_completed_sequence():
    projected = project_active_planning({"next_items": ["done"], "sequence": [{"id": "done", "status": "DONE"}]})
    assert projected["next_proof"]["status"] == "CONFLICT"
    assert projected["effective_next"] is None


@pytest.mark.parametrize("mirror", [None, "step", [None], ["step", "step"], [{"id": "step", "status": "DONE"}], []])
def test_malformed_or_competing_next_mirrors_fail_closed(mirror):
    projected = project_active_planning({"next_items": mirror, "sequence": [{"id": "step", "status": "NEXT"}]})
    assert projected["next_proof"]["status"] == "CONFLICT"
    assert projected["effective_next"] is None


@pytest.mark.parametrize("source", [
    {"version": True, "sequence": []},
    {"sequence": None}, {"sequence": [None]},
    {"sequence": [{"id": "x", "status": "NEXT"}, {"id": "x", "status": "DONE"}]},
    {"sequence": [{"id": "x", "status": "NEXT", "task_id": "../TASK-1"}]},
    {"sequence": [{"id": "x", "status": "NEXT", "task_id": "TASK-1", "task_revision": True}]},
])
def test_malformed_planning_source_has_no_projection(source):
    with pytest.raises(BrainSyncError):
        project_active_planning(source)


def test_bounded_projection_elides_history_but_keeps_exact_active_controls():
    next_item = {"id": "bo2-3", "status": "NEXT", "human_decision": "current intent", "risk_acceptance": "exact risk",
                 "predecessor": {"task_id": "TASK-320", "reviewed_and_published_sha": "a" * 40},
                 "return_to": "bo4", "unblocks_on_completion": ["bo4"]}
    source = {"active_track": "brain", "active_track_status": "ACTIVE", "next_items": ["bo2-3"],
              "authority": {"advancement": "HUMAN"}, "human_priority": "preempts H4", "sequence": [
                  *[{"id": f"done-{i}", "status": "DONE", "body": "resolved " * 1000} for i in range(1000)],
                  next_item, {"id": "blocked", "status": "BLOCKED", "current_live_blocker": {"code": "exact"},
                              "objective": "other lineage body", "human_decision": "preserve priority"}]}
    projection = project_active_planning(source)
    assert len(json.dumps(projection)) < 10000
    assert projection["effective_next"] == next_item
    assert projection["planning_controls"]["human_priority"] == "preempts H4"
    assert projection["active_controls"][0]["current_live_blocker"] == {"code": "exact"}
    assert projection["active_controls"][0]["human_decision"] == "preserve priority"
    manifest = projection["elision_manifest"]
    assert {entry["rule"] for entry in manifest} == {"OMIT_DONE_SEQUENCE_BODIES_V1", "OMIT_NONSELECTED_DETAIL_FIELDS_V1"}
    assert all(len(entry["source_digest"]) == 64 and entry["rule_version"] == "RULE_BASED_CONTEXT_ELISION_V1" for entry in manifest)
    assert projection == project_active_planning(source)


def test_priority_and_blocker_mirrors_cannot_choose_a_winner():
    row = {"id": "step", "status": "NEXT"}
    source = {"next_items": ["step"], "sequence": [row, {"id": "other", "status": "QUEUED"}],
              "human_priority_side_track": {"status": "ACTIVE", "next_items": ["other"]}}
    assert project_active_planning(source)["next_proof"]["status"] == "CONFLICT"
    source.pop("human_priority_side_track")
    row["blocked_by"] = ["exact blocker"]
    assert project_active_planning(source)["next_proof"]["conflicts"] == ["BLOCKED_ITEM_SELECTED_AS_EFFECTIVE_NEXT"]


def test_brain_sync_unique_unauthored_next_has_no_task_or_lifecycle(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    source = {"active_track_status": "ACTIVE", "next_items": ["bo2-3"],
              "sequence": [{"id": "bo2-3", "status": "NEXT", "human_decision": "author next"}]}
    _publish_roadmap(repo, source)
    def forbidden(*args, **kwargs):
        raise AssertionError("unauthored NEXT cannot select engineering lineage")
    monkeypatch.setattr("aios_renew.brain_sync.load_task", forbidden)
    monkeypatch.setattr("aios_renew.brain_sync.observe_unified_state", forbidden)
    observed = observe_brain_sync(repo)
    assert observed.selection_status == "UNAUTHORED_TASK"
    assert observed.next_action == "TASK_AUTHORING" and observed.authority == "HUMAN_BRAIN_PLANNING"
    assert observed.selected_task is None and observed.unified_state is None and observed.blocker is None
    assert observed.roadmap["effective_next"] == source["sequence"][0]


def test_brain_sync_reads_canonical_main_not_a_moved_worktree_bookmark(tmp_path):
    repo = make_repo(tmp_path)
    _publish_roadmap(repo, {"next_items": ["canonical"], "sequence": [{"id": "canonical", "status": "NEXT"}]})
    path = repo / ".ai" / "roadmap-state.yaml"
    path.write_text("next_items: [invented]\nsequence: []\n", encoding="utf-8")
    before = git(repo, "status", "--porcelain")
    observed = observe_brain_sync(repo)
    assert observed.roadmap["effective_next"]["id"] == "canonical"
    assert git(repo, "status", "--porcelain") == before


def test_brain_sync_duplicate_yaml_control_keys_fail_closed(tmp_path):
    repo = make_repo(tmp_path)
    _publish_roadmap(repo, "next_items: []\nnext_items: [other]\nsequence: []\n")
    observed = observe_brain_sync(repo)
    assert observed.selection_status == "MALFORMED_ROADMAP"
    assert observed.next_action == "NONE" and observed.authority == "NONE"


def test_nested_control_facts_survive_nonselected_detail_elision():
    nested = {"human_decision": "exact current decision", "risk_acceptance": {"risk": "exact", "authority": "HUMAN"},
              "executor_delegation": {"task_id": "TASK-2", "executor": "codex"}}
    source = {"sequence": [{"id": "next", "status": "NEXT"},
                           {"id": "blocked", "status": "BLOCKED", "audit": {"details": "unrelated", "control": nested}}]}
    projected = project_active_planning(source)
    facts = {fact["selector"].rsplit(".", 1)[1]: fact["value"] for fact in projected["nested_control_facts"]}
    assert all(facts[key] == value for key, value in nested.items())
    assert "audit" not in projected["active_controls"][0]


def test_resolved_control_history_has_bounded_provenance_and_exact_current_controls():
    source = {"sequence": [{"id": "next", "status": "NEXT"}], "research_assurance": {
        "status": "DONE", "authority": "HUMAN_BRAIN_PLANNING", "closure": {"reviewed_sha": "a" * 40},
        "milestones": [{"id": "history", "status": "DONE", "body": "resolved" * 1000}]}}
    projected = project_active_planning(source)
    control = projected["planning_controls"]["research_assurance"]
    assert "milestones" not in control and control["closure"] == source["research_assurance"]["closure"]
    assert projected["elision_manifest"][0]["rule"] == "OMIT_COMPLETED_CONTROL_HISTORY_V1"


def test_canonical_main_movement_during_sync_fails_closed(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    head = _publish_roadmap(repo, {"sequence": [{"id": "next", "status": "NEXT"}]})
    observed = iter((head, "b" * 40))
    monkeypatch.setattr("aios_renew.brain_sync._resolve_main_sha", lambda *args: next(observed))
    with pytest.raises(BrainSyncError, match="main moved"):
        observe_brain_sync(repo)


def test_numeric_historical_diagnostic_keys_are_readable_but_alias_collisions_fail_closed(tmp_path):
    repo = make_repo(tmp_path)
    _publish_roadmap(repo, "sequence:\n- id: history\n  status: DONE\n  diagnostic: {326: bounded}\n- id: next\n  status: NEXT\n")
    observed = observe_brain_sync(repo)
    assert observed.next_action == "TASK_AUTHORING" and observed.roadmap["next_proof"]["status"] == "UNIQUE"
    _publish_roadmap(repo, "sequence:\n- id: next\n  status: NEXT\n  control: {1: first, '1': conflicting}\n")
    observed = observe_brain_sync(repo)
    assert observed.selection_status == "MALFORMED_ROADMAP"


def test_an_author_branch_cannot_substitute_for_missing_canonical_main(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "remote", "remove", "origin")
    git(repo, "switch", "-c", "author-only")
    git(repo, "branch", "-D", "main")
    with pytest.raises(BrainSyncError, match="canonical main SHA"):
        observe_brain_sync(repo)
