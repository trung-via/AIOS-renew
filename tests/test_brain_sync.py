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
    load_task,
    runtime_state_root,
)
from aios_renew.verification import materialize_verification_subject
from tests.git_fixture_support import commit_fixture_state, read_git_ref
from tests.operator_test_support import (
    TASK_SOURCE,
    git,
    make_repo,
)


def test_task394_git_only_sync_discovers_and_requests_later_page(tmp_path, monkeypatch):
    from aios_renew import brain_sync
    from tests.test_failure_detail_pagination import GitFailure, summary_failure
    canonical = GitFailure(tmp_path / "canonical-pages", *summary_failure(33))
    monkeypatch.setattr(brain_sync, "_resolve_main_sha", lambda repo: "d" * 40)
    monkeypatch.setattr(brain_sync, "_resolve_repository_name", lambda repo: "trung-via/AIOS-renew")
    def denied_raw(*args):
        raise AssertionError("portable pagination must use Git safe artifacts")
    monkeypatch.setattr(brain_sync.op, "observe_runtime_failure_detail", denied_raw)
    discovery = observe_brain_sync(repo=canonical.reader, run_id=canonical.run["run_id"], canonical_sources=canonical.sources)
    first_selector = discovery.diagnostics["failure_context"]["paging"]["commands"][0]["first_selector"]
    first = observe_brain_sync(repo=canonical.reader, failure_page_selector=first_selector, canonical_sources=canonical.sources)
    later = observe_brain_sync(repo=canonical.reader, failure_page_selector=first.failure_page["next_selector"], canonical_sources=canonical.sources)
    exported = later.as_dict()
    assert discovery.as_dict()["repository"]["root"] == exported["repository"]["root"] == "protected-repository-root"
    assert exported["failure_page"]["page"]["start"] == 32
    assert len(exported["failure_page"]["page"]["failed_nodes"]) == 1
    assert "diagnostics" not in exported and exported["unified_state"] is None
    assert exported["authority"] == "NONE" and exported["next_action"] == "NONE"
    assert all(exported[k] is False for k in ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))
    assert "synthetic-secret" not in later.render() and "C:/private" not in later.render()
    assert not list(canonical.reader.glob("*.json"))
    with pytest.raises(BrainSyncError, match="FAILURE_SUBJECT_MISMATCH"):
        observe_brain_sync(repo=canonical.reader, run_id=canonical.run["run_id"], failure_page_selector=first_selector)


@pytest.mark.parametrize("selector", ["issue", "delivery", "run"])
def test_task372_exact_sync_composes_real_readers_without_human_url(tmp_path, monkeypatch, selector):
    from aios_renew import brain_sync
    from tests.test_brain_attention import Task372CanonicalSources
    from tests.test_operational_receipt import Task372Actions
    actions, canonical = Task372Actions(), Task372CanonicalSources()
    monkeypatch.setattr(brain_sync, "resolve_repository", lambda repo: tmp_path)
    monkeypatch.setattr(brain_sync, "_resolve_main_sha", lambda repo: "d" * 40)
    monkeypatch.setattr(brain_sync, "_resolve_repository_name", lambda repo: "trung-via/AIOS-renew")
    selection = {"issue_number": actions.issue_number} if selector == "issue" else (
        {"delivery_id": actions.delivery_id} if selector == "delivery" else {"run_id": canonical.run_id})
    snapshot = observe_brain_sync(repo=tmp_path, operational_sources=actions, canonical_sources=canonical, **selection)
    observed = snapshot.as_dict()
    diagnostic = observed["diagnostics"]
    assert diagnostic["blocker"] is None and diagnostic["failure_context"]["run_id"] == canonical.run_id
    assert diagnostic["failure_context"]["safe_detail"]["verification"][0]["command"] == "python -m pytest -q tests/test_fixture.py"
    if selector != "run":
        assert diagnostic["operational_context"]["attempt"]["workflow_run_id"] == actions.run_id
        assert diagnostic["operational_context"]["run_created"] is False
        assert diagnostic["attributed_failure_context"] is None
    assert observed["next_action"] == "NONE" and observed["authority"] == "NONE"
    assert all(observed[k] is False for k in ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))
    assert "synthetic-secret" not in snapshot.render() and not list(tmp_path.iterdir())


@pytest.mark.parametrize("fault", ["moved", "run", "unavailable", "wrong-task"])
def test_task372_exact_sync_rejects_untrusted_or_absent_canonical_source(tmp_path, monkeypatch, fault):
    from aios_renew import brain_sync
    from tests.test_brain_attention import Task372CanonicalSources
    sources = Task372CanonicalSources()
    monkeypatch.setattr(brain_sync, "resolve_repository", lambda repo: tmp_path)
    monkeypatch.setattr(brain_sync, "_resolve_main_sha", lambda repo: "d" * 40)
    monkeypatch.setattr(brain_sync, "_resolve_repository_name", lambda repo: "trung-via/AIOS-renew")
    if fault == "moved": sources.moved = True
    if fault == "run": sources.run["run_id"] = "RUN-foreign-001"
    if fault == "unavailable": sources.values.clear()
    if fault == "wrong-task": sources.run["task"] = {"id": "TASK-foreign", "revision": 1}
    snapshot = observe_brain_sync(repo=tmp_path, run_id=sources.run_id, canonical_sources=sources)
    assert snapshot.diagnostics["failure_context"] is None
    assert snapshot.diagnostics["blocker"]["action"] == "READ_EXACT_CANONICAL_FAILURE"


@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("moved", [False, True])
def test_task393_sync_and_terminal_attention_keep_long_summary_redacted_and_source_bound(
    tmp_path, monkeypatch, closed, moved,
):
    from aios_renew import brain_sync, review_transport
    from aios_renew.verification_contract import verification_digest
    from tests.test_brain_attention import Task372CanonicalSources
    from tests.test_review_transport import _task393_summary, _task393_summary_failure
    canonical = Task372CanonicalSources()
    spoof = _task393_summary(["FAILED tests/test_spoof.py::test_false"])
    final = "================ short test summary info ================\n" + (
        "ERROR tests/test_actual.py::test_real[synthetic-secret] - ImportError: private\n")
    if closed:
        final += "================ 1 error in 0.12s ================\n"
    private = _task393_summary_failure(spoof + "private traceback C:/private/ synthetic-secret\n" * 12000 + final)
    for key in ("run_id", "task", "executor", "base_sha", "failed_head_sha", "candidate"):
        private[key] = canonical.failure[key]
    canonical.failure = json.loads(review_transport.public_failure(json.dumps(private).encode()))
    canonical.moved = moved
    monkeypatch.setattr(brain_sync, "resolve_repository", lambda repo: tmp_path)
    monkeypatch.setattr(brain_sync, "_resolve_main_sha", lambda repo: "d" * 40)
    monkeypatch.setattr(brain_sync, "_resolve_repository_name", lambda repo: "trung-via/AIOS-renew")
    # An unavailable local detail read cannot promote an older public identity.
    private_reads = []
    def unavailable_detail(repo, context):
        private_reads.append(context["source"]["private_failure_digest"])
        return context
    monkeypatch.setattr(brain_sync.op, "observe_runtime_failure_detail", unavailable_detail)
    snapshot = observe_brain_sync(repo=tmp_path, run_id=canonical.run_id, canonical_sources=canonical)
    observed = snapshot.as_dict()
    if moved:
        assert observed["diagnostics"]["failure_context"] is None
        assert observed["diagnostics"]["blocker"]["code"] == "FAILURE_SOURCE_MOVED"
    else:
        context = observed["diagnostics"]["failure_context"]
        review_transport.validate_failure_context(context)
        assert context["source"]["artifact_sha"] == canonical.artifact_sha
        assert context["source"]["failure_digest"] == verification_digest(canonical.failure)
        assert context["source"]["private_failure_digest"] == verification_digest(private)
        item = context["safe_detail"]["verification"][0]
        assert item["summary_status"] == ("PARSED" if closed else "FINAL_SUMMARY_UNCLOSED")
        assert [node["identity"] for node in item["failed_nodes"]] == (
            ["tests/test_actual.py::test_real"] if closed else [])
        assert private_reads == ([] if closed else [verification_digest(private)])
        assert context["proof"] == "NON_PASS_DIAGNOSTIC"
    assert all(observed[k] is False for k in ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))
    portable = json.dumps(observed) + snapshot.render()
    assert all(text not in portable for text in ("synthetic-secret", "C:/private", "traceback", "test_spoof"))
    assert not list(tmp_path.iterdir())


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


_TASK396_V13_NEXT = "lean-vp-intra-v13-budget-defaults-successor"
_TASK396_RETIRED_NEXT = "lean-vp-intelligent-intra-primary-after-r21"


def _task396_source_planning_roadmap() -> dict:
    """Exercise TASK-396's immutable pre-pause selectors as historical policy.

    TASK-398 prospectively changes the live selectors; it does not change the
    meaning of this historical positive/negative regression.
    """
    source = yaml.safe_load(
        git(Path(__file__).resolve().parents[1], "show",
            "ce498546c68dce7196cc821a9d895dedce5b2f54:.ai/roadmap-state.yaml")
    )
    assert source["next_items"] == [_TASK396_V13_NEXT]
    successor = [
        item for item in source["sequence"] if item["id"] == _TASK396_V13_NEXT
    ]
    retired = [
        item for item in source["sequence"] if item["id"] == _TASK396_RETIRED_NEXT
    ]
    assert len(successor) == len(retired) == 1
    assert [item for item in source["sequence"] if item.get("status") == "NEXT"] == successor
    assert retired[0]["status"].startswith("SUPERSEDED_")
    assert retired[0]["superseded_by"] == _TASK396_V13_NEXT
    assert source["planning_document"] == successor[0]["planning_document"] == (
        "docs/AIOS-LEAN-VP-INTRA-V13-ROADMAP.md"
    )
    assert successor[0]["ka01_document_version"] == "1.3"
    return {
        "version": source["version"],
        "active_track": source["active_track"],
        "active_track_status": source["active_track_status"],
        "planning_document": source["planning_document"],
        "next_items": source["next_items"],
        "sequence": [retired[0], successor[0]],
    }


def test_task396_v13_source_single_next_resolves_successor_planning_only(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    roadmap = _task396_source_planning_roadmap()
    successor = roadmap["sequence"][1]
    assert successor["normative_task_id"] == "TASK-396"
    assert "task_id" not in successor and "task_revision" not in successor
    (repo / ".ai/roadmap-state.yaml").write_text(
        yaml.safe_dump(roadmap), encoding="utf-8"
    )

    snapshot = observe_brain_sync(repo=repo)

    # One planning successor is resolved; a normative lineage pointer does not
    # authorize execution or discover/resume an unrelated fixture TASK-101.
    assert snapshot.roadmap["next_items"] == [_TASK396_V13_NEXT]
    assert snapshot.selection_status == "UNAUTHORED_TASK"
    assert snapshot.blocker == {
        "code": "UNAUTHORED_TASK",
        "item_id": _TASK396_V13_NEXT,
        "message": "NEXT item has no associated task_id",
    }
    assert snapshot.selected_task is None and snapshot.unified_state is None
    assert snapshot.lifecycle_state == "BLOCKED"
    assert snapshot.next_action == "NONE" and snapshot.authority == "NONE"
    assert not any((snapshot.run_created, snapshot.executor_invoked,
                    snapshot.verification_invoked, snapshot.state_mutated))


@pytest.mark.parametrize(
    "fault", ["stale_old_next_items", "dual_next", "dual_next_and_next_items"]
)
def test_task396_v13_source_selector_conflict_never_resumes_retired_plan(
    tmp_path: Path, fault: str,
) -> None:
    repo = make_repo(tmp_path)
    roadmap = _task396_source_planning_roadmap()
    if fault == "stale_old_next_items":
        # The partial mutation that changes sequence alone must fail closed.
        roadmap["next_items"] = [_TASK396_RETIRED_NEXT]
    else:
        roadmap["sequence"][0]["status"] = "NEXT"
        if fault == "dual_next_and_next_items":
            roadmap["next_items"] = [_TASK396_RETIRED_NEXT, _TASK396_V13_NEXT]
    (repo / ".ai/roadmap-state.yaml").write_text(
        yaml.safe_dump(roadmap), encoding="utf-8"
    )

    snapshot = observe_brain_sync(repo=repo)

    assert snapshot.selection_status == "AMBIGUOUS_NEXT"
    assert snapshot.blocker is not None
    assert snapshot.blocker["code"] == "AMBIGUOUS_NEXT"
    assert set(snapshot.blocker["candidates"]) == {
        _TASK396_RETIRED_NEXT, _TASK396_V13_NEXT,
    }
    assert snapshot.selected_task is None and snapshot.unified_state is None
    assert snapshot.lifecycle_state == "BLOCKED"
    assert snapshot.next_action == "NONE" and snapshot.authority == "NONE"
    assert not any((snapshot.run_created, snapshot.executor_invoked,
                    snapshot.verification_invoked, snapshot.state_mutated))


_TASK398_NEXT = "git-decision-guard-priority-20261022"
_TASK398_RESIDUAL = "publisher-residual-after-git-guard-20261022"
_TASK398_PLAN = "docs/AIOS-GIT-GUARD-PUBLISH-PRIORITY-v1.md"
_TASK398_PLANNING_SOURCE_SHA = "8fc9ef2784b7781c287d760ec4597ab203052768"
_TASK399_NEXT = "bounded-unresolved-diagnostic-handoff-20261022"
_TASK399_PLAN = "docs/AIOS-BOUNDED-UNRESOLVED-DIAGNOSTIC-HANDOFF-v1.md"


def _task398_source_planning_roadmap() -> dict:
    """Keep G1 regressions bound to canonical main observed at TASK-399 authoring.

    TASK-399 changes the current priority prospectively; the historical G1
    selectors and their negative regressions retain their original meaning.
    """
    root = Path(__file__).resolve().parents[1]
    source = yaml.safe_load(git(root, "show", f"{_TASK398_PLANNING_SOURCE_SHA}:.ai/roadmap-state.yaml"))
    by_id = {item["id"]: item for item in source["sequence"]}
    assert source["next_items"] == [_TASK398_NEXT]
    assert source["active_track"] == _TASK398_NEXT
    assert source["active_track_status"] == "ACTIVE"
    assert [item["id"] for item in source["sequence"] if item["status"] == "NEXT"] == [_TASK398_NEXT]
    selected = by_id[_TASK398_NEXT]
    assert source["planning_document"] == selected["planning_document"] == _TASK398_PLAN
    assert selected["planning_owner"] == _TASK398_NEXT
    assert selected["implementation_task_id"] == "TASK-398"
    assert selected["implementation_task_revision"] == selected["implementation_task_count"] == 1
    assert "task_id" not in selected and "task_revision" not in selected
    paused = by_id[_TASK396_V13_NEXT]
    assert paused["status"] == "PAUSED_BY_HUMAN_PRIORITY_20261022"
    assert paused["disposition"] == "PAUSED_RESUMABLE_NOT_COMPLETED_NOT_RETIRED"
    assert paused["paused_for"] == _TASK398_NEXT
    assert paused["remaining_obligations"] == ["L3-05", "L3-06", "G3", "G6", "L4"]
    assert paused["original_lean_vp_slot_count"] == 16
    assert paused["ka01_activation"] == "NOT_ACTIVATED"
    retired = by_id[_TASK396_RETIRED_NEXT]
    assert retired["status"].startswith("SUPERSEDED_")
    assert retired["superseded_by"] == _TASK396_V13_NEXT
    residuals = [item for item in source["sequence"] if item.get("phase") == "G2_RESIDUAL_PUBLISHER_PERFORMANCE_ONLY"]
    assert len(residuals) == 1 and residuals[0]["id"] == _TASK398_RESIDUAL
    assert residuals[0]["status"] == "CONDITIONAL_QUEUE"
    assert residuals[0]["future_task_count"] == "ZERO_OR_ONE"
    assert residuals[0]["planning_owner"] == _TASK398_NEXT
    assert residuals[0]["shared_guard_gain"] == "MUST_NOT_BE_DOUBLE_COUNTED"
    return {**source, "sequence": [retired, paused, selected, residuals[0]]}


def test_task398_source_single_next_and_task397_transport_do_not_dispatch(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = _task398_source_planning_roadmap()
    transport = roadmap["human_git_guard_publish_priority_20261022"]["task397_reconciliation"]
    assert transport["canonical_task_run_status"] == "NOT_INFERRED_FROM_TRANSPORT_REQUIRES_EXACT_CANONICAL_RECONCILIATION"
    assert not (repo / ".ai/tasks/TASK-397.yaml").exists()
    before = git(repo, "show-ref")
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    snapshot = observe_brain_sync(repo=repo)
    assert snapshot.roadmap["next_items"] == [_TASK398_NEXT]
    assert snapshot.selection_status == "UNAUTHORED_TASK"
    assert snapshot.blocker == {"code": "UNAUTHORED_TASK", "item_id": _TASK398_NEXT,
                                "message": "NEXT item has no associated task_id"}
    assert snapshot.selected_task is None and snapshot.unified_state is None
    assert snapshot.next_action == snapshot.authority == "NONE"
    assert not any((snapshot.run_created, snapshot.executor_invoked,
                    snapshot.verification_invoked, snapshot.state_mutated))
    assert git(repo, "show-ref") == before
    assert not (repo / ".ai/tasks/TASK-397.yaml").exists()


@pytest.mark.parametrize("fault", ["stale_v13_next_items", "dual_v13_next", "dual_residual_next", "retired_old_next"])
def test_task398_source_partial_priority_mutation_fails_closed(tmp_path, fault):
    repo = make_repo(tmp_path)
    roadmap = _task398_source_planning_roadmap()
    other = _TASK396_V13_NEXT
    if fault == "stale_v13_next_items":
        roadmap["next_items"] = [other]
    else:
        if fault == "dual_residual_next":
            other = _TASK398_RESIDUAL
        elif fault == "retired_old_next":
            other = _TASK396_RETIRED_NEXT
        next(item for item in roadmap["sequence"] if item["id"] == other)["status"] = "NEXT"
    (repo / ".ai/roadmap-state.yaml").write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    snapshot = observe_brain_sync(repo=repo)
    assert snapshot.selection_status == "AMBIGUOUS_NEXT"
    assert snapshot.blocker["code"] == "AMBIGUOUS_NEXT"
    assert set(snapshot.blocker["candidates"]) == {_TASK398_NEXT, other}
    assert snapshot.selected_task is None and snapshot.unified_state is None
    assert snapshot.next_action == snapshot.authority == "NONE"
    assert not any((snapshot.run_created, snapshot.executor_invoked,
                    snapshot.verification_invoked, snapshot.state_mutated))


def test_task398_planning_documents_and_dated_records_are_coherent():
    root = Path(__file__).resolve().parents[1]
    source = _task398_source_planning_roadmap()
    current = (root / _TASK398_PLAN).read_text(encoding="utf-8")
    paused = (root / "docs/AIOS-LEAN-VP-INTRA-V13-ROADMAP.md").read_text(encoding="utf-8")
    assert "PAUSED, RESUMABLE, NOT COMPLETED, NOT RETIRED" in paused
    assert "AIOS-GIT-GUARD-PUBLISH-PRIORITY-v1.md" in paused
    assert "sole selected successor" not in paused
    assert "Both canonical selectors must identify exactly this one successor" not in paused
    for text in (current, paused):
        assert _TASK398_NEXT in text and "TASK-397" in text
        assert "Human/Brain" in text and "L3-05" in text and "L3-06" in text
        assert "G3" in text and "G6" in text and "L4" in text
        assert "NOT_ACTIVATED" in text
    assert "zero or one" in current and "non-overlapping" in current
    original = yaml.safe_load(git(root, "show", "ce498546c68dce7196cc821a9d895dedce5b2f54:.ai/roadmap-state.yaml"))
    # New prospective authority leaves every older dated top-level record exact.
    for key, value in original.items():
        if key.startswith("human_"):
            assert source[key] == value
    live = yaml.safe_load((root / ".ai/roadmap-state.yaml").read_text(encoding="utf-8"))
    untouched = {item["id"]: item for item in live["sequence"]}
    for item in original["sequence"]:
        if item["id"] != _TASK396_V13_NEXT:
            assert untouched[item["id"]] == item


def test_task399_current_priority_preserves_historical_g1_and_paused_plan():
    root = Path(__file__).resolve().parents[1]
    source = yaml.safe_load((root / ".ai/roadmap-state.yaml").read_text(encoding="utf-8"))
    historical = yaml.safe_load(git(root, "show", f"{_TASK398_PLANNING_SOURCE_SHA}:.ai/roadmap-state.yaml"))
    by_id = {item["id"]: item for item in source["sequence"]}
    assert len(by_id) == len(source["sequence"])
    assert source["next_items"] == [source["active_track"]] == [_TASK399_NEXT]
    assert source["active_track_status"] == "ACTIVE"
    assert [item["id"] for item in source["sequence"] if item["status"] == "NEXT"] == [_TASK399_NEXT]
    selected = by_id[_TASK399_NEXT]
    assert source["planning_document"] == selected["planning_document"] == _TASK399_PLAN
    assert selected["planning_owner"] == _TASK399_NEXT
    assert selected["implementation_task_id"] == "TASK-399"
    assert selected["implementation_task_revision"] == selected["implementation_task_count"] == 1
    assert "task_id" not in selected and "task_revision" not in selected
    assert selected["predecessor"] == _TASK398_NEXT
    assert selected["conditional_successor"] == _TASK398_RESIDUAL
    assert selected["next_transition_authority"] == "EXPLICIT_HUMAN_BRAIN_ONLY_NO_AUTOMATIC_LIFECYCLE_ADVANCEMENT"
    record = source["human_bounded_unresolved_handoff_priority_20261022"]
    assert record["planning_only"] is True
    assert record["source_main_sha_at_authoring"] == _TASK398_PLANNING_SOURCE_SHA
    assert record["sole_high_level_next"] == record["planning_owner"] == _TASK399_NEXT
    assert record["planning_document"] == _TASK399_PLAN
    assert record["next_transition_authority"] == "EXPLICIT_SUBSEQUENT_HUMAN_BRAIN_RECONCILIATION_NOT_RESULT_REVIEW_OR_PUBLISH"
    assert record["g2"]["status"] == "NOT_STARTED_CONDITIONAL"
    assert record["g2"]["implementation_authorization"] == "NONE_FROM_TASK399"
    assert record["g2"]["reconsideration"] == "EXPLICIT_SUBSEQUENT_HUMAN_BRAIN_RECONCILIATION"
    assert record["paused_plan"]["id"] == _TASK396_V13_NEXT
    assert record["paused_plan"]["disposition"] == "PAUSED_RESUMABLE_NOT_COMPLETED_NOT_RETIRED"
    assert record["paused_plan"]["remaining"] == ["L3-05", "L3-06", "G3", "G6", "L4"]
    assert record["historical_failure"] == {
        "run_id": "RUN-398-004", "task_id": "TASK-398", "task_revision": 1,
        "artifact_sha": "70fec2ab3f681f51c3bdfa40f51bcba50fa33852",
        "failed_head_sha": "2e97623c2ac283e151ffdc41485a90b8df14b19d",
        "private_failure_digest": "b2e60c61f1d6885802e68c81612355445b290c936754f7eda6faba77b17b0455",
        "staged_byte_digest": "UNKNOWN_NOT_BACKFILLED",
        "disposition": "IMMUTABLE_HISTORICAL_NON_PASS",
    }
    # Every older Human record and sequence entry retains its exact history;
    # only G1's prospective disposition and G2's explicit deferral are added.
    for key, value in historical.items():
        if key.startswith("human_"):
            assert source[key] == value
    assert [item["id"] for item in source["sequence"] if item["id"] != _TASK399_NEXT] == [
        item["id"] for item in historical["sequence"]
    ]
    for item in historical["sequence"]:
        expected = dict(item)
        if item["id"] == _TASK398_NEXT:
            expected.update(status="HISTORICAL_ONLY", historical_status="NEXT",
                            prospective_priority_owner=_TASK399_NEXT)
        elif item["id"] == _TASK398_RESIDUAL:
            expected.update(implementation_status="NOT_STARTED",
                            reconsider_after="EXPLICIT_HUMAN_BRAIN_RECONCILIATION_AFTER_TASK399")
        assert by_id[item["id"]] == expected


@pytest.mark.parametrize("fault", [None, "stale_g1_next_items", _TASK398_NEXT,
                                   _TASK398_RESIDUAL, _TASK396_V13_NEXT])
def test_task399_current_priority_observation_never_dispatches_or_advances(tmp_path, fault):
    repo = make_repo(tmp_path)
    root = Path(__file__).resolve().parents[1]
    roadmap = yaml.safe_load((root / ".ai/roadmap-state.yaml").read_text(encoding="utf-8"))
    # As with the G1 fixture, exercise only the relevant planning selectors;
    # unrelated DONE entries name publication commits absent from this temp repo.
    planning_ids = {_TASK399_NEXT, _TASK398_NEXT, _TASK398_RESIDUAL,
                    _TASK396_V13_NEXT, _TASK396_RETIRED_NEXT}
    roadmap["sequence"] = [item for item in roadmap["sequence"] if item["id"] in planning_ids]
    other = _TASK398_NEXT if fault == "stale_g1_next_items" else fault
    if fault == "stale_g1_next_items":
        roadmap["next_items"] = [other]
    elif fault is not None:
        next(item for item in roadmap["sequence"] if item["id"] == other)["status"] = "NEXT"
    path = repo / ".ai/roadmap-state.yaml"
    path.write_text(yaml.safe_dump(roadmap), encoding="utf-8")
    before, refs = path.read_bytes(), git(repo, "show-ref")

    snapshot = observe_brain_sync(repo=repo)

    if fault is None:
        assert snapshot.roadmap["next_items"] == [_TASK399_NEXT]
        assert snapshot.selection_status == "UNAUTHORED_TASK"
        assert snapshot.blocker == {"code": "UNAUTHORED_TASK", "item_id": _TASK399_NEXT,
                                    "message": "NEXT item has no associated task_id"}
    else:
        assert snapshot.selection_status == "AMBIGUOUS_NEXT"
        assert snapshot.blocker["code"] == "AMBIGUOUS_NEXT"
        assert set(snapshot.blocker["candidates"]) == {_TASK399_NEXT, other}
    assert snapshot.selected_task is None and snapshot.unified_state is None
    assert snapshot.lifecycle_state == "BLOCKED"
    assert snapshot.next_action == snapshot.authority == "NONE"
    assert not any((snapshot.run_created, snapshot.executor_invoked,
                    snapshot.verification_invoked, snapshot.state_mutated))
    assert path.read_bytes() == before and git(repo, "show-ref") == refs


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


def test_brain_sync_checkpoint_exposes_typed_failure_and_rejects_substituted_context():
    from dataclasses import replace
    from aios_renew.review_transport import failure_context
    from tests.test_brain_context import snapshot
    from tests.test_review_transport import _native_failure_fixture
    source = snapshot("AUTHOR_REPAIR")
    _, private = _native_failure_fixture()
    private.update(run_id="RUN-177-001", task=dict(id="TASK-177", revision=2))
    unified = dict(source.unified_state, failed_run_id=private["run_id"], failed_head_sha=private["failed_head_sha"],
                   failure_context=failure_context(private, artifact_sha="c" * 40))
    fresh = replace(source, unified_state=unified)
    assert "FAILURE CAUSE: NATIVE_TIMEOUT" in fresh.checkpoint()
    assert "synthetic-secret" not in fresh.render()
    mismatched = dict(unified, failed_run_id="RUN-177-002")
    with pytest.raises(BrainSyncError, match="does not bind Unified State"):
        replace(fresh, unified_state=mismatched).as_dict()


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
    assert isinstance(roadmap, dict), "live roadmap must be a mapping"
    next_item_ids = roadmap["next_items"]
    assert isinstance(next_item_ids, list) and len(next_item_ids) == 1, (
        "live roadmap must identify exactly one NEXT item"
    )
    assert isinstance(next_item_ids[0], str) and next_item_ids[0]
    assert isinstance(roadmap["sequence"], list)
    assert all(isinstance(item, dict) for item in roadmap["sequence"])
    next_items = [
        item for item in roadmap["sequence"] if item.get("id") in next_item_ids
    ]
    assert len(next_items) == 1
    assert [item for item in roadmap["sequence"] if item.get("status") == "NEXT"] == next_items, (
        "live roadmap next_items and sequence NEXT must agree"
    )
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

    # Derive authorship from the planning binding and canonical TASK file, not
    # from the observed status: malformed TASKs must not count as unauthored.
    task_id = expected_next.get("task_id")
    if task_id is None and expected_next["id"].startswith("TASK-"):
        task_id = expected_next["id"]
    task_revision = expected_next.get("task_revision")
    if task_revision is not None:
        assert type(task_revision) is int and task_revision > 0
    if task_id is None:
        assert task_revision is None, "NEXT has a task_revision without a TASK identity"
        task_file = None
    else:
        assert isinstance(task_id, str) and task_id
        assert "/" not in task_id and "\\" not in task_id, "invalid NEXT TASK identity"
        task_file = repo / ".ai" / "tasks" / f"{task_id}.yaml"

    if task_file is None or not task_file.exists():
        assert snapshot.selection_status == "UNAUTHORED_TASK"
        assert snapshot.selected_task is None
        assert snapshot.unified_state is None
        assert snapshot.lifecycle_state == "BLOCKED"
        assert snapshot.next_action == "NONE"
        assert snapshot.authority == "NONE"
        if task_id is None:
            assert snapshot.blocker == {
                "code": "UNAUTHORED_TASK",
                "item_id": expected_next["id"],
                "message": "NEXT item has no associated task_id",
            }
        else:
            assert snapshot.blocker == {
                "code": "UNAUTHORED_TASK",
                "task_id": task_id,
                "message": f"task file {task_file} does not exist",
            }
    else:
        assert task_file.is_file(), "NEXT TASK path must be a file"
        task = load_task(repo, task_id)
        if task_revision is not None:
            assert task.revision == task_revision, "NEXT TASK revision conflicts with canonical TASK"
        assert snapshot.selection_status == "SELECTED"
        assert snapshot.selected_task == {"id": task.task_id, "revision": task.revision}
        assert snapshot.unified_state is not None
        assert snapshot.unified_state["task"] == snapshot.selected_task
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
