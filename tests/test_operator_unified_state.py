# Tests for Unified State + Next Action observation boundary.
from contextlib import contextmanager
import json
from pathlib import Path

import pytest

import aios_renew.operator as operator_module
import aios_renew.publication as publication_module
import aios_renew.unified_state as unified_state_module
from aios_renew.unified_state import (
    UnifiedStateObservation as DirectUnifiedStateObservation,
    observe_unified_state as direct_observe_unified_state,
)
from aios_renew.operator import (
    CorrectionPreflightResult,
    OperatorError,
    UnifiedStateObservation,
    observe_unified_state,
    runtime_paths,
    runtime_state_root,
)
from aios_renew.review_transport import (
    RemoteLifecycleReview,
    RemoteLifecycleTerminal,
    RemoteTaskLifecycle,
)
from tests.operator_test_support import (
    TASK_SOURCE,
    canonical_result_payload,
    git,
    make_repo,
    publish_upstream,
)

def _stub_unified_remote(
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

def test_unified_state_fresh_task_is_read_only_execute_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    before = git(repo, "status", "--porcelain=v1")
    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["format"] == "AIOS_UNIFIED_STATE"
    assert observation["version"] == 1
    assert observation["task"] == {"id": "TASK-101", "revision": 1}
    assert observation["lifecycle_state"] == "READY"
    assert observation["next_action"] == "EXECUTE_PRIMARY"
    assert observation["run_created"] is False
    assert observation["executor_invoked"] is False
    assert observation["verification_invoked"] is False
    assert not runtime_state_root(repo).exists()
    assert git(repo, "status", "--porcelain=v1") == before


def test_unified_state_remote_observation_leaves_control_git_unchanged(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    git_dir = Path(git(repo, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    fetch_head = git_dir / "FETCH_HEAD"
    before_fetch = fetch_head.read_bytes() if fetch_head.exists() else None
    before_head = git(repo, "rev-parse", "HEAD")
    before_refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    before_objects = git(repo, "count-objects", "-v")

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "EXECUTE_PRIMARY"
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
    assert git(repo, "count-objects", "-v") == before_objects
    assert (fetch_head.read_bytes() if fetch_head.exists() else None) == before_fetch
    assert not runtime_state_root(repo).exists()


def test_unified_state_local_active_wait_and_untransported_result_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_unified_remote(monkeypatch, repo, lifecycle)
    state = runtime_paths(repo)
    run_id = "RUN-101-001"
    run = {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    (state.runs / f"{run_id}.json").write_text(json.dumps(run), encoding="utf-8")

    waiting = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert (waiting["lifecycle_state"], waiting["next_action"], waiting["run_id"]) == (
        "WAIT", "WAIT", run_id,
    )

    (state.results / f"{run_id}.json").write_text(
        json.dumps(canonical_result_payload(run_id, head)),
        encoding="utf-8",
    )
    retry = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert (retry["lifecycle_state"], retry["next_action"], retry["run_id"]) == (
        "TRANSPORT", "RETRY_TRANSPORT", run_id,
    )


def _local_correction_lifecycle(
    repo: Path, *, family: str
) -> tuple[RemoteTaskLifecycle, str]:
    state = runtime_paths(repo)
    head = git(repo, "rev-parse", "HEAD")
    parent_id = "RUN-101-001"
    child_id = "RUN-101-002"
    parent_run = {
        "run_id": parent_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }
    child_run = {
        "run_id": child_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    if family == "REMEDIATION":
        terminal = canonical_result_payload(parent_id, head)
        review = f"""review_id: REVIEW-101-001
reviewed_sha: {head}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: output is incomplete
    expected: output is complete
""".encode()
        lifecycle = RemoteTaskLifecycle(
            head,
            (
                RemoteLifecycleTerminal(
                    parent_id,
                    "RESULT",
                    head,
                    json.dumps(parent_run).encode(),
                    json.dumps(terminal).encode(),
                    None,
                ),
            ),
            (RemoteLifecycleReview(parent_id, head, review),),
            (),
            (),
            (),
        )
        finding = {
            "id": "F1",
            "basis": "AC1",
            "action": "CODE_FIX",
            "location": "OUTPUT.txt",
            "issue": "output is incomplete",
            "expected": "output is complete",
        }
        remediation = {
            "finding_id": "F1",
            "action": "CODE_FIX",
            "reviewed_sha": head,
            "modification_scope": ["OUTPUT.txt"],
            "affected_verification": ["git diff --check"],
            "constraints": {"hard": ["Commit the output."]},
        }
        local_run = {
            "kind": "REMEDIATION",
            "execution": {
                "review_id": "REVIEW-101-001",
                "finding": finding,
                "remediation": remediation,
                "run": child_run,
                "original_constraints": ["Commit the output."],
            },
        }
    else:
        failure = {
            "kind": "FAILURE",
            "run_id": parent_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": head,
            "failed_head_sha": head,
            "candidate": {
                "repairable": True,
                "transportable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        }
        lifecycle = RemoteTaskLifecycle(
            head,
            (
                RemoteLifecycleTerminal(
                    parent_id,
                    "FAILURE",
                    head,
                    json.dumps(parent_run).encode(),
                    json.dumps(failure).encode(),
                    None,
                ),
            ),
            (),
            (),
            (),
            (),
        )
        repair = {
            "repair_id": "REPAIR-101-001",
            "failed_run_id": parent_id,
            "failed_head_sha": head,
            "task": {"id": "TASK-101", "revision": 1},
            "action": "CODE_FIX",
            "modification_scope": ["OUTPUT.txt"],
            "instructions": ["Apply only the authorized correction."],
            "constraints": ["Commit the output."],
        }
        local_run = child_run
        (state.repairs / f"{child_id}.json").write_text(
            json.dumps(
                {
                    "failed_run_id": parent_id,
                    "root_base_sha": head,
                    "failed_head_sha": head,
                    "failure": failure,
                    "task": {"task_id": "TASK-101", "revision": 1},
                    "repair": repair,
                    "run": child_run,
                }
            ),
            encoding="utf-8",
        )
    (state.runs / f"{child_id}.json").write_text(
        json.dumps(local_run), encoding="utf-8"
    )
    return lifecycle, child_id


@pytest.mark.parametrize("family", ["REMEDIATION", "REPAIR"])
def test_unified_state_exact_local_correction_continues_remote_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: str,
) -> None:
    repo = make_repo(tmp_path)
    lifecycle, run_id = _local_correction_lifecycle(repo, family=family)
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    waiting = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert (waiting["lifecycle_state"], waiting["next_action"], waiting["run_id"]) == (
        "WAIT", "WAIT", run_id,
    )

    head = git(repo, "rev-parse", "HEAD")
    (runtime_paths(repo).results / f"{run_id}.json").write_text(
        json.dumps(canonical_result_payload(run_id, head)),
        encoding="utf-8",
    )
    retry = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert (retry["lifecycle_state"], retry["next_action"], retry["run_id"]) == (
        "TRANSPORT", "RETRY_TRANSPORT", run_id,
    )


def test_unified_state_multiple_local_tips_stay_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    lifecycle, _ = _local_correction_lifecycle(repo, family="REMEDIATION")
    state = runtime_paths(repo)
    unrelated_id = "RUN-101-003"
    (state.runs / f"{unrelated_id}.json").write_text(
        json.dumps({
            "run_id": unrelated_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": git(repo, "rev-parse", "HEAD"),
            "workspace": str(repo),
            "head_sha": None,
            "status": "ACTIVE",
        }),
        encoding="utf-8",
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["lifecycle_state"] == "BLOCKED"
    assert observation["blocker"]["code"] == "AMBIGUOUS_ACTIVE_TIPS"


def test_unified_state_single_unrelated_local_run_stays_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    lifecycle, run_id = _local_correction_lifecycle(repo, family="REMEDIATION")
    (runtime_paths(repo).runs / f"{run_id}.json").write_text(
        json.dumps({
            "run_id": run_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": git(repo, "rev-parse", "HEAD"),
            "workspace": str(repo),
            "head_sha": None,
            "status": "ACTIVE",
        }),
        encoding="utf-8",
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["lifecycle_state"] == "BLOCKED"
    assert observation["blocker"]["code"] == "AMBIGUOUS_ACTIVE_TIPS"


def test_unified_state_runtime_pass_never_synthesizes_semantic_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-101-001"
    run = json.dumps({
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    terminal = RemoteLifecycleTerminal(
        run_id, "RESULT", head, run,
        json.dumps(canonical_result_payload(run_id, head)).encode(),
        None,
    )
    lifecycle = RemoteTaskLifecycle(head, (terminal,), (), (), (), ())
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["lifecycle_state"] == "REVIEW"
    assert observation["next_action"] == "SEMANTIC_REVIEW"
    assert observation["review_id"] is None


def test_unified_state_rejects_canonical_result_with_mismatched_evidence_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-101-001"
    run = {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }
    package = canonical_result_payload(run_id, head)
    package["evidence"][0]["subject_sha"] = "0" * 40
    lifecycle = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                run_id,
                "RESULT",
                head,
                json.dumps(run).encode(),
                json.dumps(package).encode(),
            ),
        ),
        (), (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert (observation["lifecycle_state"], observation["next_action"]) == (
        "BLOCKED", "NONE",
    )
    assert observation["blocker"]["code"] == "MALFORMED_CANONICAL_STATE"


def test_unified_state_rejects_local_result_without_required_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_unified_remote(monkeypatch, repo, lifecycle)
    state = runtime_paths(repo)
    run_id = "RUN-101-001"
    run = {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    (state.runs / f"{run_id}.json").write_text(
        json.dumps(run), encoding="utf-8"
    )
    package = canonical_result_payload(run_id, head)
    package["evidence"] = [
        item
        for item in package["evidence"]
        if item["source"]["command"] != "git status --porcelain"
    ]
    (state.results / f"{run_id}.json").write_text(
        json.dumps(package), encoding="utf-8"
    )

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert (observation["lifecycle_state"], observation["next_action"]) == (
        "BLOCKED", "NONE",
    )
    assert observation["blocker"]["code"] == "MALFORMED_CANONICAL_STATE"


def test_unified_state_rejects_contradictory_local_and_canonical_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-101-001"
    run = {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    local_package = canonical_result_payload(run_id, head)
    canonical_package = canonical_result_payload(run_id, head)
    canonical_package["evidence"][0]["result"]["summary"] = "different proof"
    lifecycle = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                run_id,
                "RESULT",
                head,
                json.dumps(run).encode(),
                json.dumps(canonical_package).encode(),
            ),
        ),
        (), (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)
    state = runtime_paths(repo)
    (state.runs / f"{run_id}.json").write_text(
        json.dumps(run), encoding="utf-8"
    )
    (state.results / f"{run_id}.json").write_text(
        json.dumps(local_package), encoding="utf-8"
    )

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert (observation["lifecycle_state"], observation["next_action"]) == (
        "BLOCKED", "NONE",
    )
    assert observation["blocker"]["code"] == "MALFORMED_CANONICAL_STATE"


def test_unified_state_rejects_canonical_failure_with_malformed_run_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-101-001"
    run = {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "antigravity",
        "base_sha": head,
        "failed_head_sha": head,
        "candidate": {
            "repairable": True,
            "transportable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }
    lifecycle = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                run_id,
                "FAILURE",
                head,
                json.dumps(run).encode(),
                json.dumps(failure).encode(),
            ),
        ),
        (), (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert (observation["lifecycle_state"], observation["next_action"]) == (
        "BLOCKED", "NONE",
    )
    assert observation["blocker"]["code"] == "MALFORMED_CANONICAL_STATE"


def test_unified_state_ready_continue_implementation_reduces_to_execute_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-101-001"
    repair_sha = "c" * 40
    run = {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "failed_head_sha": head,
        "phase": "COMPLETION_GATE",
        "candidate": {
            "repairable": True,
            "transportable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }
    repair = {
        "repair_id": "REPAIR-101-CONTINUE",
        "failed_run_id": run_id,
        "failed_head_sha": head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue the unfinished implementation."],
        "constraints": ["Commit the output."],
    }
    lifecycle = RemoteTaskLifecycle(
        head,
        (RemoteLifecycleTerminal(
            run_id,
            "FAILURE",
            head,
            json.dumps(run).encode(),
            json.dumps(failure).encode(),
        ),),
        (),
        (),
        ((run_id, repair_sha, json.dumps(repair).encode()),),
        (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)
    calls = []

    def ready_preflight(failed_run_id, **kwargs):
        calls.append((failed_run_id, kwargs))
        return operator_module.CorrectionPreflightResult(
            family="REPAIR",
            status="READY",
            phase="READY",
            reason_code="READY",
            task_id="TASK-101",
            task_revision=1,
            failed_run_id=run_id,
            failed_head_sha=head,
            subject_mode="CURRENT",
            action="CONTINUE_IMPLEMENTATION",
            executor_required=True,
        )

    monkeypatch.setattr(operator_module, "preflight_repair", ready_preflight)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "EXECUTE_REPAIR"
    assert observation["failed_run_id"] == run_id
    assert observation["failed_head_sha"] == head
    assert observation["correction_sha"] == repair_sha
    assert (
        observation["correction_preflight"]["action"]
        == "CONTINUE_IMPLEMENTATION"
    )
    assert observation["correction_preflight"]["executor_required"] is True
    assert calls == [(run_id, {"repo": repo, "repair": repair})]


def test_unified_state_rejects_local_failure_with_malformed_candidate_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_unified_remote(monkeypatch, repo, lifecycle)
    state = runtime_paths(repo)
    run_id = "RUN-101-001"
    run = {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "failed_head_sha": head,
        "candidate": {
            "repairable": True,
            "transportable": True,
            "dirty": True,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }
    (state.runs / f"{run_id}.json").write_text(
        json.dumps(run), encoding="utf-8"
    )
    (state.failures / f"{run_id}.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert (observation["lifecycle_state"], observation["next_action"]) == (
        "BLOCKED", "NONE",
    )
    assert observation["blocker"]["code"] == "MALFORMED_CANONICAL_STATE"


def test_unified_state_changes_required_preserves_finding_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-101-001"
    run = json.dumps({
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex", "base_sha": head, "workspace": "bounded-away",
        "head_sha": None, "status": "ACTIVE",
    }).encode()
    package = json.dumps(canonical_result_payload(run_id, head)).encode()
    review = f"""review_id: REVIEW-101-001
reviewed_sha: {head}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: output is incomplete
    expected: output is complete
""".encode()
    lifecycle = RemoteTaskLifecycle(
        head,
        (RemoteLifecycleTerminal(run_id, "RESULT", head, run, package),),
        (RemoteLifecycleReview(run_id, head, review),),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "AUTHOR_REMEDIATION"
    assert observation["source_run_id"] == run_id
    assert observation["review_id"] == "REVIEW-101-001"
    assert observation["finding_id"] == "F1"
    assert observation["reviewed_sha"] == head


def test_unified_state_delta_binds_exact_correction_predecessor_with_reused_finding_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    finding_f1 = {
        "id": "F1",
        "basis": "AC1",
        "action": "CODE_FIX",
        "location": "OUTPUT.txt",
        "issue": "output is incomplete",
        "expected": "output is complete",
    }
    finding_f2 = {
        "id": "F2",
        "basis": "AC1",
        "action": "CODE_FIX",
        "location": "OUTPUT.txt",
        "issue": "output still needs a narrow correction",
        "expected": "apply the final correction",
    }

    def run_payload(run_id: str) -> dict:
        return {
            "run_id": run_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": head,
            "workspace": "bounded-away",
            "head_sha": None,
            "status": "ACTIVE",
        }

    def remediation_run(
        run_id: str, review_id: str, finding: dict,
    ) -> bytes:
        return json.dumps({
            "kind": "REMEDIATION",
            "execution": {
                "review_id": review_id,
                "finding": finding,
                "remediation": {
                    "finding_id": finding["id"],
                    "action": "CODE_FIX",
                    "reviewed_sha": head,
                    "modification_scope": ["OUTPUT.txt"],
                    "affected_verification": ["git diff --check"],
                    "constraints": {"hard": ["Commit the output."]},
                },
                "run": run_payload(run_id),
                "original_constraints": ["Commit the output."],
            },
        }).encode()

    primary_id = "RUN-101-001"
    predecessor_id = "RUN-101-002"
    tip_id = "RUN-101-003"
    primary_review = f"""review_id: REVIEW-101-001
reviewed_sha: {head}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: output is incomplete
    expected: output is complete
""".encode()
    predecessor_review = f"""review_id: REVIEW-101-002
reviewed_sha: {head}
mode: DELTA
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: output is incomplete
    expected: output is complete
prior_finding_id: F1
""".encode()
    tip_review = f"""review_id: REVIEW-101-003
reviewed_sha: {head}
mode: DELTA
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: output still needs a narrow correction
    expected: apply the final correction
prior_finding_id: F1
""".encode()
    lifecycle = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                primary_id, "RESULT", head,
                json.dumps(run_payload(primary_id)).encode(),
                json.dumps(canonical_result_payload(primary_id, head)).encode(),
            ),
            RemoteLifecycleTerminal(
                predecessor_id, "RESULT", head,
                remediation_run(predecessor_id, "REVIEW-101-001", finding_f1),
                json.dumps(canonical_result_payload(predecessor_id, head)).encode(),
            ),
            RemoteLifecycleTerminal(
                tip_id, "RESULT", head,
                remediation_run(tip_id, "REVIEW-101-002", finding_f1),
                json.dumps(canonical_result_payload(tip_id, head)).encode(),
            ),
        ),
        (
            RemoteLifecycleReview(primary_id, head, primary_review),
            RemoteLifecycleReview(predecessor_id, head, predecessor_review),
            RemoteLifecycleReview(tip_id, head, tip_review),
        ),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "AUTHOR_REMEDIATION"
    assert observation["source_run_id"] == tip_id
    assert observation["review_id"] == "REVIEW-101-003"
    assert observation["finding_id"] == finding_f2["id"]


def test_unified_state_delta_binds_repair_of_failed_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    reviewed_sha = git(repo, "rev-parse", "HEAD")
    (repo / "OUTPUT.txt").write_text("failed\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "failed correction candidate")
    failed_sha = git(repo, "rev-parse", "HEAD")
    (repo / "OUTPUT.txt").write_text("repaired\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "repaired correction candidate")
    repaired_sha = git(repo, "rev-parse", "HEAD")
    primary_id = "RUN-101-001"
    failed_remediation_id = "RUN-101-002"
    repair_id = "RUN-101-003"
    task_ref = {"id": "TASK-101", "revision": 1}
    finding = {
        "id": "F1",
        "basis": "AC1",
        "action": "CODE_FIX",
        "location": "OUTPUT.txt",
        "issue": "output is incomplete",
        "expected": "output is complete",
    }

    def run_payload(run_id: str, base_sha: str) -> dict:
        return {
            "run_id": run_id,
            "task": task_ref,
            "executor": "codex",
            "base_sha": base_sha,
            "workspace": "bounded-away",
            "head_sha": None,
            "status": "ACTIVE",
        }

    primary_run = run_payload(primary_id, reviewed_sha)
    failed_run = run_payload(failed_remediation_id, reviewed_sha)
    repair_run = run_payload(repair_id, failed_sha)
    failed_remediation = {
        "kind": "REMEDIATION",
        "execution": {
            "review_id": "REVIEW-101-001",
            "finding": finding,
            "remediation": {
                "finding_id": finding["id"],
                "action": "CODE_FIX",
                "reviewed_sha": reviewed_sha,
                "modification_scope": ["OUTPUT.txt"],
                "affected_verification": ["git diff --check"],
                "constraints": {"hard": ["Commit the output."]},
            },
            "run": failed_run,
            "original_constraints": ["Commit the output."],
        },
    }
    failure = {
        "kind": "FAILURE",
        "run_id": failed_remediation_id,
        "task": task_ref,
        "executor": "codex",
        "base_sha": reviewed_sha,
        "failed_head_sha": failed_sha,
        "candidate": {
            "repairable": True,
            "transportable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["OUTPUT.txt"],
            "outside_task_scope": [],
        },
    }
    repair_authorization = {
        "repair_id": "REPAIR-101-001",
        "failed_run_id": failed_remediation_id,
        "failed_head_sha": failed_sha,
        "task": task_ref,
        "action": "CODE_FIX",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Complete the authorized correction."],
        "constraints": ["Commit the output."],
    }
    repair_execution = {
        "failed_run_id": failed_remediation_id,
        "root_base_sha": reviewed_sha,
        "failed_head_sha": failed_sha,
        "failure": failure,
        "task": {"task_id": "TASK-101", "revision": 1},
        "repair": repair_authorization,
        "run": repair_run,
    }
    primary_review = f"""review_id: REVIEW-101-001
reviewed_sha: {reviewed_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: output is incomplete
    expected: output is complete
""".encode()
    repair_review = f"""review_id: REVIEW-101-002
reviewed_sha: {repaired_sha}
mode: DELTA
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: repaired output needs a final correction
    expected: apply the final correction
prior_finding_id: F1
""".encode()
    lifecycle = RemoteTaskLifecycle(
        repaired_sha,
        (
            RemoteLifecycleTerminal(
                primary_id, "RESULT", reviewed_sha,
                json.dumps(primary_run).encode(),
                json.dumps(
                    canonical_result_payload(primary_id, reviewed_sha)
                ).encode(),
            ),
            RemoteLifecycleTerminal(
                failed_remediation_id, "FAILURE", failed_sha,
                json.dumps(failed_remediation).encode(), json.dumps(failure).encode(),
            ),
            RemoteLifecycleTerminal(
                repair_id, "RESULT", repaired_sha,
                json.dumps(repair_run).encode(),
                json.dumps(
                    canonical_result_payload(
                        repair_id, repaired_sha, changed_files=["OUTPUT.txt"]
                    )
                ).encode(),
                json.dumps(repair_execution).encode(),
            ),
        ),
        (
            RemoteLifecycleReview(primary_id, reviewed_sha, primary_review),
            RemoteLifecycleReview(repair_id, repaired_sha, repair_review),
        ),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "AUTHOR_REMEDIATION"
    assert observation["source_run_id"] == repair_id
    assert observation["review_id"] == "REVIEW-101-002"
    assert observation["finding_id"] == "F2"


def test_unified_state_authored_remediation_consumes_exact_ready_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-101-001"
    run = json.dumps({
        "run_id": run_id, "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex", "base_sha": head, "workspace": "bounded-away",
        "head_sha": None, "status": "ACTIVE",
    }).encode()
    package = json.dumps(canonical_result_payload(run_id, head)).encode()
    review = f"""review_id: REVIEW-101-001
reviewed_sha: {head}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: output is incomplete
    expected: output is complete
""".encode()
    correction_sha = "a" * 40
    lifecycle = RemoteTaskLifecycle(
        head,
        (RemoteLifecycleTerminal(run_id, "RESULT", head, run, package),),
        (RemoteLifecycleReview(run_id, head, review),),
        ((run_id, "F1", correction_sha),), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)
    monkeypatch.setattr(
        operator_module,
        "preflight_remediation",
        lambda *_args, **_kwargs: CorrectionPreflightResult(
            "REMEDIATION", "READY", "READY", "READY",
            task_id="TASK-101", task_revision=1, source_run_id=run_id,
            review_id="REVIEW-101-001", finding_id="F1", reviewed_sha=head,
            subject_mode="CURRENT", action="CODE_FIX",
        ),
    )

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "EXECUTE_REMEDIATION"
    assert observation["correction_sha"] == correction_sha
    assert observation["correction_preflight"]["status"] == "READY"


def test_unified_state_pass_is_done_only_when_candidate_is_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-101-001"
    run = json.dumps({
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex", "base_sha": head, "workspace": "bounded-away",
        "head_sha": None, "status": "ACTIVE",
    }).encode()
    package = json.dumps(canonical_result_payload(run_id, head)).encode()
    review = f"""review_id: REVIEW-101-001
reviewed_sha: {head}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""".encode()
    lifecycle = RemoteTaskLifecycle(
        head,
        (RemoteLifecycleTerminal(run_id, "RESULT", head, run, package),),
        (RemoteLifecycleReview(run_id, head, review),),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)
    monkeypatch.setattr(
        publication_module, "_load_success_lineage", lambda *_args, **_kwargs: (head, None)
    )

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["lifecycle_state"] == "DONE"
    assert observation["next_action"] == "DONE"

def test_state_missing_task_remains_read_only_on_stale_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    before = git(repo, "rev-parse", "HEAD")
    task_source = TASK_SOURCE.replace("TASK-101", "TASK-102")
    publish_upstream(repo, {".ai/tasks/TASK-102.yaml": task_source})
    monkeypatch.setattr(
        operator_module,
        "_preflight_primary_sync",
        lambda *_args, **_kwargs: pytest.fail("state attempted synchronization"),
    )

    with pytest.raises(OperatorError, match="TASK not found: TASK-102"):
        observe_unified_state("TASK-102", repo=repo)

    assert git(repo, "rev-parse", "HEAD") == before


def test_unified_state_module_boundary_and_operator_compatibility() -> None:
    assert operator_module.UnifiedStateObservation is DirectUnifiedStateObservation
    assert operator_module.observe_unified_state is direct_observe_unified_state
    assert unified_state_module.UnifiedStateObservation is operator_module.UnifiedStateObservation
    assert unified_state_module.observe_unified_state is operator_module.observe_unified_state


def test_unified_state_direct_module_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lifecycle = RemoteTaskLifecycle(head, (), (), (), (), ())
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = direct_observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["format"] == "AIOS_UNIFIED_STATE"
    assert observation["version"] == 1
    assert observation["task"] == {"id": "TASK-101", "revision": 1}
    assert observation["lifecycle_state"] == "READY"
    assert observation["next_action"] == "EXECUTE_PRIMARY"



def test_unified_state_observes_legacy_and_predecessor_remediation_runs_ac7(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    head = git(repo, "rev-parse", "HEAD")
    parent_id = "RUN-101-000"
    child_id = "RUN-101-001"

    # 1. Legacy REMEDIATION run (no predecessor field)
    legacy_run = {
        "kind": "REMEDIATION",
        "execution": {
            "review_id": "REVIEW-101-000",
            "finding": {
                "id": "R1",
                "basis": "AC1",
                "action": "CODE_FIX",
                "location": "OUTPUT.txt",
                "issue": "Fix needed",
                "expected": "Fixed",
            },
            "remediation": {
                "finding_id": "R1",
                "action": "CODE_FIX",
                "reviewed_sha": head,
                "modification_scope": ["OUTPUT.txt"],
                "affected_verification": ["git diff --check"],
                "constraints": [],
            },
            "run": {
                "run_id": child_id,
                "task": {"id": "TASK-101", "revision": 1},
                "executor": "codex",
                "base_sha": head,
                "workspace": str(repo),
                "head_sha": None,
                "status": "ACTIVE",
            },
            "original_constraints": [],
        },
    }
    (state.runs / f"{child_id}.json").write_text(json.dumps(legacy_run), encoding="utf-8")
    obs_legacy = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_legacy["lifecycle_state"] == "WAIT"
    assert obs_legacy["next_action"] == "WAIT"
    assert obs_legacy["run_id"] == child_id

    # 2. Predecessor-bearing REMEDIATION run (with predecessor field)
    pred_run = dict(legacy_run)
    pred_run["predecessor"] = {
        "source_run_id": parent_id,
        "review_id": "REVIEW-101-000",
        "finding_id": "R1",
        "reviewed_sha": head,
    }
    (state.runs / f"{child_id}.json").write_text(json.dumps(pred_run), encoding="utf-8")
    obs_pred = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_pred["lifecycle_state"] == "WAIT"
    assert obs_pred["next_action"] == "WAIT"
    assert obs_pred["run_id"] == child_id

    # Add result to test RETRY_TRANSPORT
    (state.results / f"{child_id}.json").write_text(
        json.dumps(canonical_result_payload(child_id, head)),
        encoding="utf-8",
    )
    obs_retry = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_retry["lifecycle_state"] == "TRANSPORT"
    assert obs_retry["next_action"] == "RETRY_TRANSPORT"
    assert obs_retry["run_id"] == child_id
