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
    commit_setup_state,
    git,
    make_repo,
    publish_test_remediation_lineage,
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


def _canonical_repair_of_remediation_lifecycle(
    repo: Path, *, continuation: bool = False,
) -> tuple[RemoteTaskLifecycle, dict[str, object]]:
    """Build the canonical/local shape that exposed TASK-145."""

    head = git(repo, "rev-parse", "HEAD")
    primary_id = "RUN-101-700"
    remediation_id = "RUN-101-900"
    repair_ids = ["RUN-101-100"]
    if continuation:
        # Deliberately sorts before both predecessors: lineage, not RUN numbering,
        # must transport the semantic identity.
        repair_ids.append("RUN-101-050")

    def plain_run(run_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": head,
            "workspace": "bounded-away",
            "head_sha": None,
            "status": "ACTIVE",
        }

    def failure(run_id: str, *, continuation_of: str | None = None) -> dict[str, object]:
        value: dict[str, object] = {
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
        if continuation_of is not None:
            value["continuation_of"] = continuation_of
        return value

    primary_run = plain_run(primary_id)
    primary_result = canonical_result_payload(primary_id, head)
    primary_review = f"""review_id: REVIEW-101-ORIGIN
reviewed_sha: {head}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: FINDING-101-ORIGIN
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: output is incomplete
    expected: output is complete
""".encode()

    finding = {
        "id": "FINDING-101-ORIGIN",
        "basis": "AC1",
        "action": "CODE_FIX",
        "location": "OUTPUT.txt",
        "issue": "output is incomplete",
        "expected": "output is complete",
    }
    remediation_run_value = plain_run(remediation_id)
    remediation_run = {
        "kind": "REMEDIATION",
        "execution": {
            "review_id": "REVIEW-101-ORIGIN",
            "finding": finding,
            "remediation": {
                "finding_id": "FINDING-101-ORIGIN",
                "action": "CODE_FIX",
                "reviewed_sha": head,
                "modification_scope": ["OUTPUT.txt"],
                "affected_verification": ["git diff --check"],
                "constraints": [],
            },
            "run": remediation_run_value,
            "original_constraints": [],
        },
    }
    remediation_failure = failure(remediation_id)
    terminals = [
        RemoteLifecycleTerminal(
            primary_id, "RESULT", head,
            json.dumps(primary_run).encode(), json.dumps(primary_result).encode(),
        ),
        RemoteLifecycleTerminal(
            remediation_id, "FAILURE", head,
            json.dumps(remediation_run).encode(),
            json.dumps(remediation_failure).encode(),
        ),
    ]

    parent_id = remediation_id
    parent_failure = remediation_failure
    final_run: dict[str, object] | None = None
    final_failure: dict[str, object] | None = None
    final_execution: dict[str, object] | None = None
    for index, repair_id in enumerate(repair_ids, start=1):
        repair_run = plain_run(repair_id)
        repair_failure = failure(repair_id, continuation_of=parent_id)
        repair_authorization = {
            "repair_id": f"REPAIR-101-{index:03d}",
            "failed_run_id": parent_id,
            "failed_head_sha": head,
            "task": {"id": "TASK-101", "revision": 1},
            "action": "CONTINUE_IMPLEMENTATION",
            "modification_scope": ["OUTPUT.txt"],
            "instructions": ["Continue implementation."],
            "constraints": ["Commit the output."],
            # These are deliberately untrusted prose.  Semantic identity must
            # still come from the exact failed correction parent chain.
            "review_id": "REVIEW-FORGED",
            "finding_id": "FINDING-FORGED",
        }
        repair_execution = {
            "failed_run_id": parent_id,
            "root_base_sha": head,
            "failed_head_sha": head,
            "failure": parent_failure,
            "task": {"task_id": "TASK-101", "revision": 1},
            "repair": repair_authorization,
            "run": repair_run,
        }
        terminals.append(RemoteLifecycleTerminal(
            repair_id, "FAILURE", head,
            json.dumps(repair_run).encode(), json.dumps(repair_failure).encode(),
            json.dumps(repair_execution).encode(),
        ))
        parent_id = repair_id
        parent_failure = repair_failure
        final_run = repair_run
        final_failure = repair_failure
        final_execution = repair_execution

    assert final_run is not None
    assert final_failure is not None
    assert final_execution is not None
    selector_sha = "d" * 40
    next_repair = {
        "repair_id": "REPAIR-101-NEXT",
        "failed_run_id": parent_id,
        "failed_head_sha": head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue implementation."],
        "constraints": ["Commit the output."],
    }
    lifecycle = RemoteTaskLifecycle(
        head,
        tuple(terminals),
        (RemoteLifecycleReview(primary_id, head, primary_review),),
        (),
        ((parent_id, selector_sha, json.dumps(next_repair).encode()),),
        (),
    )
    return lifecycle, {
        "head": head,
        "final_run_id": parent_id,
        "final_run": final_run,
        "final_failure": final_failure,
        "final_execution": final_execution,
        "selector_sha": selector_sha,
    }


def _persist_local_repair_terminal(repo: Path, shape: dict[str, object]) -> None:
    state = runtime_paths(repo)
    run_id = shape["final_run_id"]
    (state.runs / f"{run_id}.json").write_text(
        json.dumps(shape["final_run"]), encoding="utf-8"
    )
    (state.repairs / f"{run_id}.json").write_text(
        json.dumps(shape["final_execution"]), encoding="utf-8"
    )
    (state.failures / f"{run_id}.json").write_text(
        json.dumps(shape["final_failure"]), encoding="utf-8"
    )


@pytest.mark.parametrize("continuation", [False, True])
def test_unified_state_reconciles_local_repair_of_remediation_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, continuation: bool,
) -> None:
    repo = make_repo(tmp_path)
    lifecycle, shape = _canonical_repair_of_remediation_lifecycle(
        repo, continuation=continuation
    )
    _persist_local_repair_terminal(repo, shape)
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    def ready_preflight(failed_run_id, **_kwargs):
        return CorrectionPreflightResult(
            family="REPAIR", status="READY", phase="READY", reason_code="READY",
            task_id="TASK-101", task_revision=1,
            failed_run_id=failed_run_id, failed_head_sha=shape["head"],
            subject_mode="HISTORICAL", action="CONTINUE_IMPLEMENTATION",
            executor_required=True, authorization_sha=shape["selector_sha"],
        )

    monkeypatch.setattr(operator_module, "preflight_repair", ready_preflight)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "EXECUTE_REPAIR"
    assert observation["failed_run_id"] == shape["final_run_id"]
    assert observation["failed_head_sha"] == shape["head"]
    assert observation["correction_sha"] == shape["selector_sha"]


def test_unified_state_local_repair_of_primary_keeps_semantic_identity_unset(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    lifecycle, _run_id = _local_correction_lifecycle(repo, family="REPAIR")
    task = operator_module.load_task(repo, "TASK-101")
    remote, reviews = unified_state_module._decode_remote_lifecycle(
        repo, task, lifecycle
    )

    terminal, active = unified_state_module._local_pending_runs(
        repo, runtime_paths(repo), task, remote, reviews
    )

    assert terminal == []
    assert len(active) == 1
    assert active[0].family == "REPAIR"
    assert active[0].review_id is None
    assert active[0].finding_id is None


@pytest.mark.parametrize(
    "mismatch",
    ["parent_identity", "parent_sha", "correction_content", "semantic_prose"],
)
def test_unified_state_local_repair_reconciliation_mismatches_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str,
) -> None:
    repo = make_repo(tmp_path)
    lifecycle, shape = _canonical_repair_of_remediation_lifecycle(repo)
    execution = json.loads(json.dumps(shape["final_execution"]))
    if mismatch == "parent_identity":
        execution["failed_run_id"] = "RUN-101-NOT-THE-PARENT"
        execution["failure"]["run_id"] = "RUN-101-NOT-THE-PARENT"
        execution["repair"]["failed_run_id"] = "RUN-101-NOT-THE-PARENT"
    elif mismatch == "parent_sha":
        execution["failed_head_sha"] = "0" * 40
    elif mismatch == "correction_content":
        execution["repair"]["instructions"] = ["Different correction."]
    else:
        execution["repair"]["review_id"] = "REVIEW-DIFFERENT"
        execution["repair"]["finding_id"] = "FINDING-DIFFERENT"
    shape["final_execution"] = execution
    _persist_local_repair_terminal(repo, shape)
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["lifecycle_state"] == "BLOCKED"
    assert observation["next_action"] == "NONE"
    assert observation["blocker"]["code"] == "MALFORMED_CANONICAL_STATE"


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
    assert calls == [(
        run_id,
        {
            "repo": repo,
            "repair": repair,
            "required_repair_sha": repair_sha,
        },
    )]


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


def test_unified_state_exposes_bounded_multi_finding_frontier_without_selector(
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
    review = f"""review_id: REVIEW-101-001
reviewed_sha: {head}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: second
    expected: fixed
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: first
    expected: fixed
""".encode()
    lifecycle = RemoteTaskLifecycle(
        head,
        (RemoteLifecycleTerminal(
            run_id, "RESULT", head, run,
            json.dumps(canonical_result_payload(run_id, head)).encode(),
        ),),
        (RemoteLifecycleReview(run_id, head, review),),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "AUTHOR_REMEDIATION"
    assert observation["authority"] == "BRAIN"
    assert observation["finding_id"] is None
    assert [item["finding_id"] for item in observation["outstanding_findings"]] == [
        "F1", "F2",
    ]
    assert all(set(item) == {
        "source_run_id", "review_id", "finding_id", "reviewed_sha",
    } for item in observation["outstanding_findings"])


def test_unified_state_over_bound_frontier_fails_closed_without_truncation(
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
    findings = "\n".join(
        f"  - id: F{i:02d}\n    basis: AC1\n    action: CODE_FIX\n"
        "    location: OUTPUT.txt\n    issue: issue\n    expected: fixed"
        for i in range(33)
    )
    review = (
        f"review_id: REVIEW-101-001\nreviewed_sha: {head}\nmode: PRIMARY\n"
        f"verdict: CHANGES_REQUIRED\nacceptance: {{AC1: FAIL}}\nfindings:\n{findings}\n"
    ).encode()
    lifecycle = RemoteTaskLifecycle(
        head,
        (RemoteLifecycleTerminal(
            run_id, "RESULT", head, run,
            json.dumps(canonical_result_payload(run_id, head)).encode(),
        ),),
        (RemoteLifecycleReview(run_id, head, review),),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "NONE"
    assert observation["blocker"] == {
        "code": "OUTSTANDING_FINDINGS_BOUND_EXCEEDED", "count": 33, "limit": 32,
    }
    assert observation["outstanding_findings"] == []


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
    failed_sha = commit_setup_state(
        repo, "OUTPUT.txt", message="failed correction candidate"
    )
    (repo / "OUTPUT.txt").write_text("repaired\n", encoding="utf-8")
    repaired_sha = commit_setup_state(
        repo, "OUTPUT.txt", message="repaired correction candidate"
    )
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
            execution_base_run_id=run_id, execution_base_sha=head,
            subject_mode="CURRENT", action="CODE_FIX",
        ),
    )

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "EXECUTE_REMEDIATION"
    assert observation["correction_sha"] == correction_sha
    assert observation["execution_base"] == {
        "run_id": run_id,
        "candidate_sha": head,
    }
    assert observation["correction_preflight"]["status"] == "READY"
    assert observation["correction_preflight"]["execution_base"] == {
        "run_id": run_id,
        "candidate_sha": head,
    }


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


@pytest.mark.parametrize("task_revision", [1, 2])
def test_unified_state_and_preflight_agree_on_cumulative_execution_base_regardless_of_task_revision_ac7(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task_revision: int,
) -> None:
    repo = make_repo(tmp_path)
    if task_revision != 1:
        (repo / ".ai" / "tasks" / "TASK-101.yaml").write_text(
            TASK_SOURCE.replace("revision: 1", f"revision: {task_revision}"),
            encoding="utf-8",
        )
        commit_setup_state(
            repo, ".ai/tasks/TASK-101.yaml", message="bump revision"
        )
    head = git(repo, "rev-parse", "HEAD")

    (repo / "OUTPUT.txt").write_text("sibling content\n", encoding="utf-8")
    sibling_sha = commit_setup_state(
        repo, "OUTPUT.txt", message="advance sibling candidate"
    )

    primary_id = "RUN-101-001"
    sibling_id = "RUN-101-002"
    primary_run = json.dumps({
        "run_id": primary_id,
        "task": {"id": "TASK-101", "revision": task_revision},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    sibling_run = json.dumps({
        "run_id": sibling_id,
        "task": {"id": "TASK-101", "revision": task_revision},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
        "kind": "REMEDIATION",
        "predecessor": {
            "source_run_id": primary_id,
            "review_id": "REVIEW-101-001",
            "finding_id": "F1",
            "reviewed_sha": head,
        },
        "execution_base": {
            "run_id": primary_id,
            "candidate_sha": head,
        },
        "execution": {
            "review_id": "REVIEW-101-001",
            "finding": {
                "id": "F1", "basis": "AC1", "action": "CODE_FIX",
                "location": "OUTPUT.txt", "issue": "first issue", "expected": "fixed",
            },
            "remediation": {
                "finding_id": "F1", "action": "CODE_FIX", "reviewed_sha": head,
                "modification_scope": ["OUTPUT.txt"],
                "affected_verification": ["git diff --check"],
                "constraints": {"hard": ["fix output"]},
            },
            "run": {
                "run_id": sibling_id,
                "task": {"id": "TASK-101", "revision": task_revision},
                "executor": "codex",
                "base_sha": head,
                "workspace": "bounded-away",
                "head_sha": None,
                "status": "ACTIVE",
            },
            "original_constraints": ["fix output"],
        },
    }).encode()

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
    issue: first issue
    expected: fixed
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: second issue
    expected: fixed
""".encode()
    sibling_review = f"""review_id: REVIEW-101-002
reviewed_sha: {sibling_sha}
mode: DELTA
verdict: PASS
prior_finding_id: F1
acceptance: {{AC1: PASS}}
findings: []
""".encode()
    selector_sha = "c" * 40
    sib_result = canonical_result_payload(sibling_id, sibling_sha)
    sib_result["result"]["claims"] = []
    sib_result["evidence"][0]["source"]["command"] = "git diff --check"
    lifecycle = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                primary_id, "RESULT", head, primary_run,
                json.dumps(canonical_result_payload(primary_id, head)).encode(),
            ),
            RemoteLifecycleTerminal(
                sibling_id, "RESULT", sibling_sha, sibling_run,
                json.dumps(sib_result).encode(),
            ),
        ),
        (
            RemoteLifecycleReview(primary_id, head, primary_review),
            RemoteLifecycleReview(sibling_id, sibling_sha, sibling_review),
        ),
        ((primary_id, "F2", selector_sha),), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle)
    monkeypatch.setattr(
        operator_module,
        "preflight_remediation",
        lambda *_args, **_kwargs: CorrectionPreflightResult(
            "REMEDIATION", "READY", "READY", "READY",
            task_id="TASK-101", task_revision=task_revision, source_run_id=primary_id,
            review_id="REVIEW-101-001", finding_id="F2", reviewed_sha=head,
            execution_base_run_id=sibling_id, execution_base_sha=sibling_sha,
            subject_mode="CURRENT", action="CODE_FIX",
        ),
    )

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["next_action"] == "EXECUTE_REMEDIATION"
    assert observation["source_run_id"] == primary_id
    assert observation["review_id"] == "REVIEW-101-001"
    assert observation["finding_id"] == "F2"
    assert observation["reviewed_sha"] == head
    assert observation["execution_base"] == {
        "run_id": sibling_id,
        "candidate_sha": sibling_sha,
    }
    assert observation["correction_preflight"]["execution_base"] == {
        "run_id": sibling_id,
        "candidate_sha": sibling_sha,
    }


def test_unified_state_revision_1_blocks_invalid_and_integration_required_cumulative_state_ac5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    # 1. Non-descendant candidate fails closed with CUMULATIVE_BASE_INVALID
    git(repo, "checkout", "--quiet", "--orphan", "orphan-branch")
    git(repo, "commit", "--allow-empty", "--quiet", "-m", "orphan commit")
    orphan_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--quiet", "main")


    primary_id = "RUN-101-001"
    sibling_id = "RUN-101-002"
    primary_run = json.dumps({
        "run_id": primary_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    sibling_run = json.dumps({
        "run_id": sibling_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
        "kind": "REMEDIATION",
        "predecessor": {
            "source_run_id": primary_id,
            "review_id": "REVIEW-101-001",
            "finding_id": "F1",
            "reviewed_sha": head,
        },
        "execution_base": {
            "run_id": primary_id,
            "candidate_sha": head,
        },
        "execution": {
            "review_id": "REVIEW-101-001",
            "finding": {
                "id": "F1", "basis": "AC1", "action": "CODE_FIX",
                "location": "OUTPUT.txt", "issue": "first issue", "expected": "fixed",
            },
            "remediation": {
                "finding_id": "F1", "action": "CODE_FIX", "reviewed_sha": head,
                "modification_scope": ["OUTPUT.txt"],
                "affected_verification": ["git diff --check"],
                "constraints": {"hard": ["fix output"]},
            },
            "run": {
                "run_id": sibling_id,
                "task": {"id": "TASK-101", "revision": 1},
                "executor": "codex",
                "base_sha": head,
                "workspace": "bounded-away",
                "head_sha": None,
                "status": "ACTIVE",
            },
            "original_constraints": ["fix output"],
        },
    }).encode()
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
    issue: first issue
    expected: fixed
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: second issue
    expected: fixed
""".encode()
    sibling_review = f"""review_id: REVIEW-101-002
reviewed_sha: {orphan_sha}
mode: DELTA
verdict: PASS
prior_finding_id: F1
acceptance: {{AC1: PASS}}
findings: []
""".encode()
    sib_res = canonical_result_payload(sibling_id, orphan_sha)
    sib_res["result"]["claims"] = []
    sib_res["evidence"][0]["source"]["command"] = "git diff --check"

    lifecycle_invalid = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                primary_id, "RESULT", head, primary_run,
                json.dumps(canonical_result_payload(primary_id, head)).encode(),
            ),
            RemoteLifecycleTerminal(
                sibling_id, "RESULT", orphan_sha, sibling_run,
                json.dumps(sib_res).encode(),
            ),
        ),
        (
            RemoteLifecycleReview(primary_id, head, review),
            RemoteLifecycleReview(sibling_id, orphan_sha, sibling_review),
        ),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_invalid)
    obs_invalid = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_invalid["next_action"] == "NONE"
    assert obs_invalid["blocker"] == {"code": "CUMULATIVE_BASE_INVALID"}

    # 2. Tip that does not contain main fails closed with INTEGRATION_REQUIRED
    (repo / "MAIN.txt").write_text("main advance\n", encoding="utf-8")
    git(repo, "add", "MAIN.txt")
    git(repo, "commit", "--quiet", "-m", "advance main ahead of tip")
    new_main = git(repo, "rev-parse", "HEAD")

    lifecycle_integration = RemoteTaskLifecycle(
        new_main,
        (
            RemoteLifecycleTerminal(
                primary_id, "RESULT", head, primary_run,
                json.dumps(canonical_result_payload(primary_id, head)).encode(),
            ),
        ),
        (RemoteLifecycleReview(primary_id, head, review),),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_integration)
    obs_integration = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_integration["next_action"] == "NONE"
    assert obs_integration["blocker"] == {"code": "INTEGRATION_REQUIRED"}

    # 3. After explicit integration, Unified State transitions to AUTHOR_REMEDIATION
    from aios_renew.correction_integration import integrate_correction
    int_result = integrate_correction(
        "TASK-101",
        task_revision=1,
        cumulative_tip_run_id=primary_id,
        cumulative_tip_candidate_sha=head,
        authorized_main_sha=new_main,
        repo=repo,
    )
    obs_integrated = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_integrated["next_action"] == "AUTHOR_REMEDIATION"
    assert obs_integrated["execution_base"] is not None
    assert obs_integrated["execution_base"]["kind"] == "INTEGRATED"
    assert obs_integrated["execution_base"]["integration_candidate_sha"] == int_result.integration_candidate_sha
    assert obs_integrated["execution_base"]["cumulative_tip_run_id"] == primary_id
    assert obs_integrated["execution_base"]["authorized_main_sha"] == new_main

    # 4. If main advances again, the integrated base is invalidated -> returns INTEGRATION_REQUIRED again
    (repo / "MAIN2.txt").write_text("main advance 2\n", encoding="utf-8")
    git(repo, "add", "MAIN2.txt")
    git(repo, "commit", "--quiet", "-m", "advance main again")
    newer_main = git(repo, "rev-parse", "HEAD")

    lifecycle_newer = RemoteTaskLifecycle(
        newer_main,
        (
            RemoteLifecycleTerminal(
                primary_id, "RESULT", head, primary_run,
                json.dumps(canonical_result_payload(primary_id, head)).encode(),
            ),
        ),
        (RemoteLifecycleReview(primary_id, head, review),),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_newer)
    obs_invalidated = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_invalidated["next_action"] == "NONE"
    assert obs_invalidated["blocker"] == {"code": "INTEGRATION_REQUIRED"}


def test_unified_state_and_remediation_remain_integration_required_on_remote_push_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    primary_id = "RUN-101-000"
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id=primary_id,
        finding_id="R1",
        reviewed_sha=head,
    )
    state = runtime_paths(repo)
    if state.runs.is_dir():
        shutil.rmtree(state.runs)
    if state.results.is_dir():
        shutil.rmtree(state.results)

    primary_run = json.dumps({
        "run_id": primary_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    review = f"""review_id: REVIEW-RUN-101-000
reviewed_sha: {head}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: R1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The output is absent.
    expected: Commit only the output.
""".encode()

    (repo / "MAIN.txt").write_text("main advance\n", encoding="utf-8")
    git(repo, "add", "MAIN.txt")
    git(repo, "commit", "--quiet", "-m", "advance main ahead of tip")
    new_main = git(repo, "rev-parse", "HEAD")

    lifecycle_integration = RemoteTaskLifecycle(
        new_main,
        (
            RemoteLifecycleTerminal(
                primary_id, "RESULT", head, primary_run,
                json.dumps(canonical_result_payload(primary_id, head)).encode(),
            ),
        ),
        (RemoteLifecycleReview(primary_id, head, review),),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_integration)

    # 1. Unified state initially reports INTEGRATION_REQUIRED
    obs_initial = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_initial["next_action"] == "NONE"
    assert obs_initial["blocker"] == {"code": "INTEGRATION_REQUIRED"}

    # 2. Break remote push to simulate push failure
    git(repo, "remote", "set-url", "--push", "origin", str(tmp_path / "broken_push.git"))
    from aios_renew.correction_integration import (
        CorrectionIntegrationError,
        derive_integration_identity,
        integrate_correction,
        integration_ref_name,
    )

    with pytest.raises(CorrectionIntegrationError, match="failed to push integration ref"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id=primary_id,
            cumulative_tip_candidate_sha=head,
            authorized_main_sha=new_main,
            repo=repo,
        )

    # Unified state remains INTEGRATION_REQUIRED after push failure
    obs_after_push_failure = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_after_push_failure["next_action"] == "NONE"
    assert obs_after_push_failure["blocker"] == {"code": "INTEGRATION_REQUIRED"}

    # 3. Simulate local-only staging (local ref exists, but missing from remote)
    int_id = derive_integration_identity("TASK-101", 1, primary_id, head, new_main)
    local_ref = integration_ref_name(int_id)
    git(repo, "update-ref", local_ref, head)

    obs_local_only = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_local_only["next_action"] == "NONE"
    assert obs_local_only["blocker"] == {"code": "INTEGRATION_REQUIRED"}

    # 4. Attempting to run remediation fails closed: no RUN is created, no executor invoked
    from aios_renew.operator import run_remediation, OperatorError
    executor_invoked = []
    def fake_native_runner(*_args, **_kwargs):
        executor_invoked.append(True)
        raise AssertionError("Executor must not be invoked when integration is required")

    with pytest.raises(OperatorError, match="cumulative execution base rejected.*require integration"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=fake_native_runner,
        )

    assert len(executor_invoked) == 0

    remediation_runs = list(state.runs.glob("RUN-101-*.json")) if state.runs.is_dir() else []
    assert len(remediation_runs) == 0


def test_unified_state_repaired_primary_result_reduces_to_execute_remediation_and_author_remediation_ac1_ac2_ac3_ac6(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    (repo / "OUTPUT.txt").write_text("repaired candidate content\n", encoding="utf-8")
    candidate_sha = commit_setup_state(
        repo, "OUTPUT.txt", message="repaired candidate"
    )

    primary_id = "RUN-101-001"
    repair_1_id = "RUN-101-002"
    repair_2_id = "RUN-101-003"

    primary_run = json.dumps({
        "run_id": primary_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    primary_failure = json.dumps({
        "kind": "FAILURE",
        "run_id": primary_id,
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
    }).encode()

    repair_1_run = json.dumps({
        "run_id": repair_1_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    repair_1_failure = json.dumps({
        "kind": "FAILURE",
        "run_id": repair_1_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "failed_head_sha": head,
        "continuation_of": primary_id,
        "phase": "COMPLETION_GATE",
        "candidate": {
            "repairable": True,
            "transportable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }).encode()
    repair_1_auth = {
        "repair_id": "REPAIR-101-001",
        "failed_run_id": primary_id,
        "failed_head_sha": head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue implementation."],
        "constraints": ["Commit the output."],
    }
    repair_1_execution = {
        "failed_run_id": primary_id,
        "root_base_sha": head,
        "failed_head_sha": head,
        "failure": json.loads(primary_failure),
        "task": {"task_id": "TASK-101", "revision": 1},
        "repair": repair_1_auth,
        "run": json.loads(repair_1_run),
    }

    repair_2_run = json.dumps({
        "run_id": repair_2_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    repair_2_result = canonical_result_payload(
        repair_2_id, candidate_sha, changed_files=["OUTPUT.txt"]
    )
    repair_2_auth = {
        "repair_id": "REPAIR-101-002",
        "failed_run_id": repair_1_id,
        "failed_head_sha": head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue implementation."],
        "constraints": ["Commit the output."],
    }
    repair_2_execution = {
        "failed_run_id": repair_1_id,
        "root_base_sha": head,
        "failed_head_sha": head,
        "failure": json.loads(repair_1_failure),
        "task": {"task_id": "TASK-101", "revision": 1},
        "repair": repair_2_auth,
        "run": json.loads(repair_2_run),
    }

    repair_2_review_yaml = f"""review_id: REVIEW-{repair_2_id}
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The first issue is present.
    expected: Fix first issue.
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The second issue is present.
    expected: Fix second issue.
""".encode()

    selector_sha = "f" * 40

    lifecycle_with_selector = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                primary_id, "FAILURE", head, primary_run, primary_failure,
            ),
            RemoteLifecycleTerminal(
                repair_1_id, "FAILURE", head, repair_1_run, repair_1_failure,
                json.dumps(repair_1_execution).encode(),
            ),
            RemoteLifecycleTerminal(
                repair_2_id, "RESULT", candidate_sha, repair_2_run,
                json.dumps(repair_2_result).encode(),
                json.dumps(repair_2_execution).encode(),
            ),
        ),
        (
            RemoteLifecycleReview(repair_2_id, candidate_sha, repair_2_review_yaml),
        ),
        ((repair_2_id, "F1", selector_sha),), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_with_selector)

    monkeypatch.setattr(
        operator_module,
        "preflight_remediation",
        lambda *_args, **_kwargs: CorrectionPreflightResult(
            "REMEDIATION", "READY", "READY", "READY",
            task_id="TASK-101", task_revision=1, source_run_id=repair_2_id,
            review_id=f"REVIEW-{repair_2_id}", finding_id="F1", reviewed_sha=candidate_sha,
            execution_base_run_id=repair_2_id, execution_base_sha=candidate_sha,
            subject_mode="CURRENT", action="CODE_FIX",
        ),
    )

    observation = observe_unified_state("TASK-101", repo=repo).as_dict()

    assert observation["lifecycle_state"] == "CORRECTION"
    assert observation["next_action"] == "EXECUTE_REMEDIATION"
    assert observation["source_run_id"] == repair_2_id
    assert observation["review_id"] == f"REVIEW-{repair_2_id}"
    assert observation["finding_id"] == "F1"
    assert observation["reviewed_sha"] == candidate_sha
    assert observation["execution_base"] == {
        "run_id": repair_2_id,
        "candidate_sha": candidate_sha,
    }
    assert observation["correction_preflight"]["status"] == "READY"
    assert observation["correction_preflight"]["execution_base"] == {
        "run_id": repair_2_id,
        "candidate_sha": candidate_sha,
    }
    assert len(observation["outstanding_findings"]) == 2

    # Without selector: reduces to AUTHOR_REMEDIATION
    lifecycle_no_selector = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                primary_id, "FAILURE", head, primary_run, primary_failure,
            ),
            RemoteLifecycleTerminal(
                repair_1_id, "FAILURE", head, repair_1_run, repair_1_failure,
                json.dumps(repair_1_execution).encode(),
            ),
            RemoteLifecycleTerminal(
                repair_2_id, "RESULT", candidate_sha, repair_2_run,
                json.dumps(repair_2_result).encode(),
                json.dumps(repair_2_execution).encode(),
            ),
        ),
        (
            RemoteLifecycleReview(repair_2_id, candidate_sha, repair_2_review_yaml),
        ),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_no_selector)

    obs_no_selector = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_no_selector["lifecycle_state"] == "CORRECTION"
    assert obs_no_selector["next_action"] == "AUTHOR_REMEDIATION"
    assert obs_no_selector["execution_base"] == {
        "run_id": repair_2_id,
        "candidate_sha": candidate_sha,
    }
    assert len(obs_no_selector["outstanding_findings"]) == 2
    assert all(
        item["source_run_id"] == repair_2_id
        for item in obs_no_selector["outstanding_findings"]
    )

    # Competing PRIMARY reviews fail closed with MALFORMED_CANONICAL_STATE
    primary_competing_review = f"""review_id: REVIEW-{primary_id}
reviewed_sha: {head}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: first
    expected: fixed
""".encode()
    competing_lifecycle = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                primary_id, "FAILURE", head, primary_run, primary_failure,
            ),
            RemoteLifecycleTerminal(
                repair_1_id, "FAILURE", head, repair_1_run, repair_1_failure,
                json.dumps(repair_1_execution).encode(),
            ),
            RemoteLifecycleTerminal(
                repair_2_id, "RESULT", candidate_sha, repair_2_run,
                json.dumps(repair_2_result).encode(),
                json.dumps(repair_2_execution).encode(),
            ),
        ),
        (
            RemoteLifecycleReview(primary_id, head, primary_competing_review),
            RemoteLifecycleReview(repair_2_id, candidate_sha, repair_2_review_yaml),
        ),
        (), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, competing_lifecycle)

    obs_competing = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_competing["next_action"] == "NONE"
    assert obs_competing["blocker"]["code"] == "MALFORMED_CANONICAL_STATE"


def test_unified_state_task_140_shaped_repaired_primary_with_divergent_main_requires_integration_then_becomes_remediation_ready_ac6_ac7(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    # Repaired PRIMARY candidate commit on top of head
    (repo / "OUTPUT.txt").write_text("repaired candidate content\n", encoding="utf-8")
    candidate_sha = commit_setup_state(
        repo, "OUTPUT.txt", message="repaired candidate"
    )

    primary_id = "RUN-101-001"
    repair_1_id = "RUN-101-002"
    repair_2_id = "RUN-101-003"

    primary_run = json.dumps({
        "run_id": primary_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    primary_failure = json.dumps({
        "kind": "FAILURE",
        "run_id": primary_id,
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
    }).encode()

    repair_1_run = json.dumps({
        "run_id": repair_1_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    repair_1_failure = json.dumps({
        "kind": "FAILURE",
        "run_id": repair_1_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "failed_head_sha": head,
        "continuation_of": primary_id,
        "phase": "COMPLETION_GATE",
        "candidate": {
            "repairable": True,
            "transportable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }).encode()
    repair_1_auth = {
        "repair_id": "REPAIR-101-001",
        "failed_run_id": primary_id,
        "failed_head_sha": head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue implementation."],
        "constraints": ["Commit the output."],
    }
    repair_1_execution = {
        "failed_run_id": primary_id,
        "root_base_sha": head,
        "failed_head_sha": head,
        "failure": json.loads(primary_failure),
        "task": {"task_id": "TASK-101", "revision": 1},
        "repair": repair_1_auth,
        "run": json.loads(repair_1_run),
    }

    repair_2_run = json.dumps({
        "run_id": repair_2_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    repair_2_result = canonical_result_payload(
        repair_2_id, candidate_sha, changed_files=["OUTPUT.txt"]
    )
    repair_2_auth = {
        "repair_id": "REPAIR-101-002",
        "failed_run_id": repair_1_id,
        "failed_head_sha": head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue implementation."],
        "constraints": ["Commit the output."],
    }
    repair_2_execution = {
        "failed_run_id": repair_1_id,
        "root_base_sha": head,
        "failed_head_sha": head,
        "failure": json.loads(repair_1_failure),
        "task": {"task_id": "TASK-101", "revision": 1},
        "repair": repair_2_auth,
        "run": json.loads(repair_2_run),
    }

    repair_2_review_yaml = f"""review_id: REVIEW-{repair_2_id}
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The first issue is present.
    expected: Fix first issue.
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The second issue is present.
    expected: Fix second issue.
""".encode()

    selector_sha = "f" * 40

    # Advance main after the repaired PRIMARY RESULT
    (repo / "MAIN_ADVANCE.txt").write_text("main advance content\n", encoding="utf-8")
    git(repo, "add", "MAIN_ADVANCE.txt")
    git(repo, "commit", "--quiet", "-m", "advance main ahead of repair result")
    new_main = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    lifecycle_divergent = RemoteTaskLifecycle(
        new_main,
        (
            RemoteLifecycleTerminal(
                primary_id, "FAILURE", head, primary_run, primary_failure,
            ),
            RemoteLifecycleTerminal(
                repair_1_id, "FAILURE", head, repair_1_run, repair_1_failure,
                json.dumps(repair_1_execution).encode(),
            ),
            RemoteLifecycleTerminal(
                repair_2_id, "RESULT", candidate_sha, repair_2_run,
                json.dumps(repair_2_result).encode(),
                json.dumps(repair_2_execution).encode(),
            ),
        ),
        (
            RemoteLifecycleReview(repair_2_id, candidate_sha, repair_2_review_yaml),
        ),
        ((repair_2_id, "F1", selector_sha),), (), (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_divergent)

    # 1. State initially reports INTEGRATION_REQUIRED despite authorized finding selector
    obs_initial = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_initial["next_action"] == "NONE"
    assert obs_initial["blocker"] == {"code": "INTEGRATION_REQUIRED"}

    # 2. Materialize explicit clean integration: no executor is invoked
    from aios_renew.correction_integration import integrate_correction
    int_result = integrate_correction(
        "TASK-101",
        task_revision=1,
        cumulative_tip_run_id=repair_2_id,
        cumulative_tip_candidate_sha=candidate_sha,
        authorized_main_sha=new_main,
        repo=repo,
    )
    assert int_result.task_id == "TASK-101"
    assert int_result.cumulative_tip_run_id == repair_2_id
    assert int_result.parents == (candidate_sha, new_main)

    # 3. After explicit integration, Unified State transitions to EXECUTE_REMEDIATION
    # using the integrated base and the authorized finding selector
    monkeypatch.setattr(
        operator_module,
        "preflight_remediation",
        lambda *_args, **_kwargs: CorrectionPreflightResult(
            "REMEDIATION", "READY", "READY", "READY",
            task_id="TASK-101", task_revision=1, source_run_id=repair_2_id,
            review_id=f"REVIEW-{repair_2_id}", finding_id="F1", reviewed_sha=candidate_sha,
            execution_base_run_id=repair_2_id, execution_base_sha=int_result.integration_candidate_sha,
            integrated_base={
                "version": 1,
                "kind": "INTEGRATED",
                "cumulative_tip_run_id": repair_2_id,
                "cumulative_tip_candidate_sha": candidate_sha,
                "authorized_main_sha": new_main,
                "integration_candidate_sha": int_result.integration_candidate_sha,
                "integration_id": int_result.integration_id,
            },
            subject_mode="CURRENT", action="CODE_FIX",
        ),
    )

    obs_integrated = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_integrated["lifecycle_state"] == "CORRECTION"
    assert obs_integrated["next_action"] == "EXECUTE_REMEDIATION"
    assert obs_integrated["source_run_id"] == repair_2_id
    assert obs_integrated["finding_id"] == "F1"
    assert obs_integrated["execution_base"]["kind"] == "INTEGRATED"
    assert obs_integrated["execution_base"]["integration_candidate_sha"] == int_result.integration_candidate_sha
    assert obs_integrated["execution_base"]["cumulative_tip_run_id"] == repair_2_id
    assert obs_integrated["execution_base"]["authorized_main_sha"] == new_main


def _setup_divergent_integrated_topology(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from aios_renew.correction_integration import integrate_correction

    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    (repo / "OUTPUT.txt").write_text("repaired candidate content\n", encoding="utf-8")
    candidate_sha = commit_setup_state(
        repo, "OUTPUT.txt", message="repaired candidate"
    )

    primary_id = "RUN-101-001"
    repair_1_id = "RUN-101-002"
    repair_2_id = "RUN-101-003"
    remediation_id = "RUN-101-004"

    primary_run = json.dumps({
        "run_id": primary_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    primary_failure = json.dumps({
        "kind": "FAILURE",
        "run_id": primary_id,
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
    }).encode()

    repair_1_run = json.dumps({
        "run_id": repair_1_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    repair_1_failure = json.dumps({
        "kind": "FAILURE",
        "run_id": repair_1_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "failed_head_sha": head,
        "continuation_of": primary_id,
        "phase": "COMPLETION_GATE",
        "candidate": {
            "repairable": True,
            "transportable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }).encode()
    repair_1_auth = {
        "repair_id": "REPAIR-101-001",
        "failed_run_id": primary_id,
        "failed_head_sha": head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue implementation."],
        "constraints": ["Commit the output."],
    }
    repair_1_execution = {
        "failed_run_id": primary_id,
        "root_base_sha": head,
        "failed_head_sha": head,
        "failure": json.loads(primary_failure),
        "task": {"task_id": "TASK-101", "revision": 1},
        "repair": repair_1_auth,
        "run": json.loads(repair_1_run),
    }

    repair_2_run = json.dumps({
        "run_id": repair_2_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    repair_2_result = canonical_result_payload(
        repair_2_id, candidate_sha, changed_files=["OUTPUT.txt"]
    )
    repair_2_auth = {
        "repair_id": "REPAIR-101-002",
        "failed_run_id": repair_1_id,
        "failed_head_sha": head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue implementation."],
        "constraints": ["Commit the output."],
    }
    repair_2_execution = {
        "failed_run_id": repair_1_id,
        "root_base_sha": head,
        "failed_head_sha": head,
        "failure": json.loads(repair_1_failure),
        "task": {"task_id": "TASK-101", "revision": 1},
        "repair": repair_2_auth,
        "run": json.loads(repair_2_run),
    }

    repair_2_review_yaml = f"""review_id: REVIEW-{repair_2_id}
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The first issue is present.
    expected: Fix first issue.
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The second issue is present.
    expected: Fix second issue.
""".encode()

    author = tmp_path / f"author-{repair_2_id}-F1"
    rev_dir = author / ".ai" / "reviews"
    rem_dir = author / ".ai" / "remediations"
    rev_dir.mkdir(parents=True, exist_ok=True)
    rem_dir.mkdir(parents=True, exist_ok=True)
    git(author, "init", "--quiet")
    git(author, "remote", "add", "origin", str(tmp_path / "upstream.git"))
    (rev_dir / f"REVIEW-{repair_2_id}.yaml").write_text(
        repair_2_review_yaml.decode(), encoding="utf-8"
    )
    (rem_dir / f"REMEDIATION-{repair_2_id}-F1.yaml").write_text(
        f"""finding_id: F1
action: CODE_FIX
reviewed_sha: {candidate_sha}
modification_scope: [OUTPUT.txt]
affected_verification: [git diff --check]
constraints: []
""",
        encoding="utf-8",
    )
    git(author, "add", ".ai")
    git(author, "commit", "--quiet", "-m", "author review and remediation")
    git(author, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/remediation/{repair_2_id}-F1")
    selector_sha = git(author, "rev-parse", "HEAD")

    (repo / "MAIN_ADVANCE.txt").write_text("main advance content\n", encoding="utf-8")
    git(repo, "add", "MAIN_ADVANCE.txt")
    git(repo, "commit", "--quiet", "-m", "advance main ahead of repair result")
    new_main = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    base_terminals = (
        RemoteLifecycleTerminal(
            primary_id, "FAILURE", head, primary_run, primary_failure,
        ),
        RemoteLifecycleTerminal(
            repair_1_id, "FAILURE", head, repair_1_run, repair_1_failure,
            json.dumps(repair_1_execution).encode(),
        ),
        RemoteLifecycleTerminal(
            repair_2_id, "RESULT", candidate_sha, repair_2_run,
            json.dumps(repair_2_result).encode(),
            json.dumps(repair_2_execution).encode(),
        ),
    )
    base_reviews = (
        RemoteLifecycleReview(repair_2_id, candidate_sha, repair_2_review_yaml),
    )

    lifecycle_divergent = RemoteTaskLifecycle(
        new_main,
        base_terminals,
        base_reviews,
        ((repair_2_id, "F1", selector_sha),),
        (),
        (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_divergent)

    int_result = integrate_correction(
        "TASK-101",
        task_revision=1,
        cumulative_tip_run_id=repair_2_id,
        cumulative_tip_candidate_sha=candidate_sha,
        authorized_main_sha=new_main,
        repo=repo,
    )

    return {
        "repo": repo,
        "head": head,
        "candidate_sha": candidate_sha,
        "new_main": new_main,
        "primary_id": primary_id,
        "repair_1_id": repair_1_id,
        "repair_2_id": repair_2_id,
        "remediation_id": remediation_id,
        "int_result": int_result,
        "base_terminals": base_terminals,
        "base_reviews": base_reviews,
        "selector_sha": selector_sha,
    }


def _make_integrated_remediation_run(
    *,
    remediation_id: str,
    base_sha: str,
    cumulative_tip_run_id: str,
    cumulative_tip_candidate_sha: str,
    authorized_main_sha: str,
    integration_candidate_sha: str,
    integration_id: str,
    finding_id: str = "F1",
    task_id: str = "TASK-101",
    task_revision: int = 1,
) -> bytes:
    execution = {
        "review_id": f"REVIEW-{cumulative_tip_run_id}",
        "finding": {
            "id": finding_id,
            "basis": "AC1",
            "action": "CODE_FIX",
            "location": "OUTPUT.txt",
            "issue": "The first issue is present.",
            "expected": "Fix first issue.",
        },
        "remediation": {
            "finding_id": finding_id,
            "action": "CODE_FIX",
            "reviewed_sha": cumulative_tip_candidate_sha,
            "modification_scope": ["OUTPUT.txt"],
            "affected_verification": ["git diff --check"],
            "constraints": [],
        },
        "run": {
            "run_id": remediation_id,
            "task": {"id": task_id, "revision": task_revision},
            "executor": "codex",
            "base_sha": base_sha,
            "workspace": "bounded-away",
            "head_sha": None,
            "status": "ACTIVE",
        },
        "original_constraints": [],
    }
    return json.dumps({
        "kind": "REMEDIATION",
        "execution": execution,
        "predecessor": {
            "source_run_id": cumulative_tip_run_id,
            "review_id": f"REVIEW-{cumulative_tip_run_id}",
            "finding_id": finding_id,
            "reviewed_sha": cumulative_tip_candidate_sha,
        },
        "execution_base": {
            "version": 1,
            "kind": "INTEGRATED",
            "cumulative_tip_run_id": cumulative_tip_run_id,
            "cumulative_tip_candidate_sha": cumulative_tip_candidate_sha,
            "authorized_main_sha": authorized_main_sha,
            "integration_candidate_sha": integration_candidate_sha,
            "integration_id": integration_id,
        },
    }).encode()


def test_unified_state_integrated_remediation_failure_equivalent_to_run_140_008_reduces_to_repairable_state_ac1_ac2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    topo = _setup_divergent_integrated_topology(tmp_path, monkeypatch)
    repo = topo["repo"]
    int_result = topo["int_result"]
    remediation_id = topo["remediation_id"]
    new_main = topo["new_main"]

    remediation_run = _make_integrated_remediation_run(
        remediation_id=remediation_id,
        base_sha=int_result.integration_candidate_sha,
        cumulative_tip_run_id=topo["repair_2_id"],
        cumulative_tip_candidate_sha=topo["candidate_sha"],
        authorized_main_sha=new_main,
        integration_candidate_sha=int_result.integration_candidate_sha,
        integration_id=int_result.integration_id,
    )

    remediation_failure = json.dumps({
        "kind": "FAILURE",
        "run_id": remediation_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": int_result.integration_candidate_sha,
        "failed_head_sha": int_result.integration_candidate_sha,
        "phase": "COMPLETION_GATE",
        "candidate": {
            "repairable": True,
            "transportable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }).encode()

    # Advance canonical main past the authorized main used during integration
    # to replicate the real RUN-140-008 chronology where canonical main advanced
    # before observing the integrated REMEDIATION FAILURE.
    (repo / "CANONICAL_MAIN_ADVANCE.txt").write_text("canonical main advance content\n", encoding="utf-8")
    git(repo, "add", "CANONICAL_MAIN_ADVANCE.txt")
    git(repo, "commit", "--quiet", "-m", "advance canonical main past integrated remediation")
    advanced_main = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    # AC1 & AC2 (a): Without repair selector, reduces to AUTHOR_REPAIR instead of MALFORMED_CANONICAL_STATE
    lifecycle_author_repair = RemoteTaskLifecycle(
        advanced_main,
        topo["base_terminals"] + (
            RemoteLifecycleTerminal(
                remediation_id, "FAILURE", int_result.integration_candidate_sha,
                remediation_run, remediation_failure,
            ),
        ),
        topo["base_reviews"],
        ((topo["repair_2_id"], "F1", topo["selector_sha"]),),
        (),
        (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_author_repair)

    obs_author = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_author["lifecycle_state"] == "CORRECTION"
    assert obs_author["next_action"] == "AUTHOR_REPAIR"
    assert obs_author["run_id"] == remediation_id
    assert obs_author["failed_run_id"] == remediation_id
    assert obs_author["failed_head_sha"] == int_result.integration_candidate_sha

    # AC1 & AC2 (b): With authorized repair selector, reduces to EXECUTE_REPAIR
    repair_auth = {
        "repair_id": "REPAIR-101-003",
        "failed_run_id": remediation_id,
        "failed_head_sha": int_result.integration_candidate_sha,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue implementation."],
        "constraints": ["Commit the output."],
    }
    repair_sha = "a" * 40
    lifecycle_execute_repair = RemoteTaskLifecycle(
        advanced_main,
        topo["base_terminals"] + (
            RemoteLifecycleTerminal(
                remediation_id, "FAILURE", int_result.integration_candidate_sha,
                remediation_run, remediation_failure,
            ),
        ),
        topo["base_reviews"],
        ((topo["repair_2_id"], "F1", topo["selector_sha"]),),
        ((remediation_id, repair_sha, json.dumps(repair_auth).encode()),),
        (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_execute_repair)

    def ready_preflight_repair(failed_run_id, **kwargs):
        return CorrectionPreflightResult(
            family="REPAIR",
            status="READY",
            phase="READY",
            reason_code="READY",
            task_id="TASK-101",
            task_revision=1,
            failed_run_id=failed_run_id,
            failed_head_sha=int_result.integration_candidate_sha,
            subject_mode="HISTORICAL",
            action="CONTINUE_IMPLEMENTATION",
            executor_required=True,
            authorization_sha=repair_sha,
        )

    monkeypatch.setattr(operator_module, "preflight_repair", ready_preflight_repair)

    obs_exec = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_exec["lifecycle_state"] == "CORRECTION"
    assert obs_exec["next_action"] == "EXECUTE_REPAIR"
    assert obs_exec["run_id"] == remediation_id
    assert obs_exec["failed_run_id"] == remediation_id
    assert obs_exec["failed_head_sha"] == int_result.integration_candidate_sha
    assert obs_exec["correction_sha"] == repair_sha


def test_unified_state_integrated_remediation_chronology_advancement_reduces_to_repair_ac2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Explicit chronology regression: valid integration is created against an older authorized main,
    # then remote canonical main is advanced before observing the integrated REMEDIATION FAILURE.
    # The valid historical RUN must reduce to AUTHOR_REPAIR / EXECUTE_REPAIR.
    test_unified_state_integrated_remediation_failure_equivalent_to_run_140_008_reduces_to_repairable_state_ac1_ac2(
        tmp_path, monkeypatch
    )


def test_unified_state_integrated_remediation_result_and_subsequent_repair_lineage_ac3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    topo = _setup_divergent_integrated_topology(tmp_path, monkeypatch)
    repo = topo["repo"]
    int_result = topo["int_result"]
    remediation_id = topo["remediation_id"]
    new_main = topo["new_main"]

    remediation_run = _make_integrated_remediation_run(
        remediation_id=remediation_id,
        base_sha=int_result.integration_candidate_sha,
        cumulative_tip_run_id=topo["repair_2_id"],
        cumulative_tip_candidate_sha=topo["candidate_sha"],
        authorized_main_sha=new_main,
        integration_candidate_sha=int_result.integration_candidate_sha,
        integration_id=int_result.integration_id,
    )

    (repo / "OUTPUT.txt").write_text("remediation fixed F1 content\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "fix F1 in remediation")
    rem_candidate_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    rem_result = canonical_result_payload(
        remediation_id, rem_candidate_sha, changed_files=["OUTPUT.txt"]
    )
    rem_terminal = RemoteLifecycleTerminal(
        remediation_id, "RESULT", rem_candidate_sha,
        remediation_run, json.dumps(rem_result).encode(),
    )

    # 1. Unreviewed RESULT gives SEMANTIC_REVIEW preserving operational parent and finding identity
    lifecycle_unreviewed = RemoteTaskLifecycle(
        new_main,
        topo["base_terminals"] + (rem_terminal,),
        topo["base_reviews"],
        ((topo["repair_2_id"], "F1", topo["selector_sha"]),),
        (),
        (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_unreviewed)

    obs_unreviewed = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_unreviewed["lifecycle_state"] == "REVIEW"
    assert obs_unreviewed["next_action"] == "SEMANTIC_REVIEW"
    assert obs_unreviewed["run_id"] == remediation_id
    assert obs_unreviewed["candidate_sha"] == rem_candidate_sha
    assert obs_unreviewed["source_run_id"] == topo["repair_2_id"]
    assert obs_unreviewed["review_id"] == f"REVIEW-{topo['repair_2_id']}"
    assert obs_unreviewed["finding_id"] == "F1"

    # 2. DELTA review resolving F1 preserves frontier and transitions to AUTHOR_REMEDIATION for F2
    delta_review_yaml = f"""review_id: REVIEW-{remediation_id}
reviewed_sha: {rem_candidate_sha}
mode: DELTA
verdict: PASS
acceptance: {{AC1: PASS}}
findings: []
prior_finding_id: F1
""".encode()

    lifecycle_reviewed = RemoteTaskLifecycle(
        new_main,
        topo["base_terminals"] + (rem_terminal,),
        topo["base_reviews"] + (
            RemoteLifecycleReview(remediation_id, rem_candidate_sha, delta_review_yaml),
        ),
        ((topo["repair_2_id"], "F1", topo["selector_sha"]),),
        (),
        (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_reviewed)

    obs_reviewed = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_reviewed["lifecycle_state"] == "CORRECTION"
    assert obs_reviewed["next_action"] == "AUTHOR_REMEDIATION"
    assert obs_reviewed["source_run_id"] == topo["repair_2_id"]
    assert obs_reviewed["finding_id"] == "F2"
    assert obs_reviewed["execution_base"]["run_id"] == remediation_id
    assert obs_reviewed["execution_base"]["candidate_sha"] == rem_candidate_sha
    assert len(obs_reviewed["outstanding_findings"]) == 1
    assert obs_reviewed["outstanding_findings"][0]["finding_id"] == "F2"

    # 3. Subsequent REPAIR of a failed integrated remediation preserves operational parent and DELTA review traversal
    repair_rem_id = "RUN-101-005"
    failed_rem_terminal = RemoteLifecycleTerminal(
        remediation_id, "FAILURE", int_result.integration_candidate_sha,
        remediation_run,
        json.dumps({
            "kind": "FAILURE",
            "run_id": remediation_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": int_result.integration_candidate_sha,
            "failed_head_sha": int_result.integration_candidate_sha,
            "phase": "COMPLETION_GATE",
            "candidate": {
                "repairable": True,
                "transportable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        }).encode(),
    )

    (repo / "OUTPUT.txt").write_text("repaired remediation content\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "repaired remediation F1")
    repair_candidate_sha = git(repo, "rev-parse", "HEAD")

    repair_rem_run = json.dumps({
        "run_id": repair_rem_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": int_result.integration_candidate_sha,
        "workspace": "bounded-away",
        "head_sha": None,
        "status": "ACTIVE",
    }).encode()
    repair_rem_result = canonical_result_payload(
        repair_rem_id, repair_candidate_sha, changed_files=["OUTPUT.txt"]
    )
    repair_rem_auth = {
        "repair_id": "REPAIR-101-003",
        "failed_run_id": remediation_id,
        "failed_head_sha": int_result.integration_candidate_sha,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue implementation."],
        "constraints": ["Commit the output."],
    }
    repair_rem_execution = {
        "failed_run_id": remediation_id,
        "root_base_sha": int_result.integration_candidate_sha,
        "failed_head_sha": int_result.integration_candidate_sha,
        "failure": json.loads(failed_rem_terminal.terminal),
        "task": {"task_id": "TASK-101", "revision": 1},
        "repair": repair_rem_auth,
        "run": json.loads(repair_rem_run),
    }
    repair_rem_review_yaml = f"""review_id: REVIEW-{repair_rem_id}
reviewed_sha: {repair_candidate_sha}
mode: DELTA
verdict: PASS
acceptance: {{AC1: PASS}}
findings: []
prior_finding_id: F1
""".encode()

    lifecycle_repaired_rem = RemoteTaskLifecycle(
        new_main,
        topo["base_terminals"] + (
            failed_rem_terminal,
            RemoteLifecycleTerminal(
                repair_rem_id, "RESULT", repair_candidate_sha,
                repair_rem_run, json.dumps(repair_rem_result).encode(),
                json.dumps(repair_rem_execution).encode(),
            ),
        ),
        topo["base_reviews"] + (
            RemoteLifecycleReview(repair_rem_id, repair_candidate_sha, repair_rem_review_yaml),
        ),
        ((topo["repair_2_id"], "F1", topo["selector_sha"]),),
        (),
        (),
    )
    _stub_unified_remote(monkeypatch, repo, lifecycle_repaired_rem)

    obs_repaired_rem = observe_unified_state("TASK-101", repo=repo).as_dict()
    assert obs_repaired_rem["lifecycle_state"] == "CORRECTION"
    assert obs_repaired_rem["next_action"] == "AUTHOR_REMEDIATION"
    assert obs_repaired_rem["source_run_id"] == topo["repair_2_id"]
    assert obs_repaired_rem["finding_id"] == "F2"
    assert obs_repaired_rem["execution_base"]["run_id"] == repair_rem_id
    assert obs_repaired_rem["execution_base"]["candidate_sha"] == repair_candidate_sha
    assert len(obs_repaired_rem["outstanding_findings"]) == 1
    assert obs_repaired_rem["outstanding_findings"][0]["finding_id"] == "F2"


def test_unified_state_integrated_remediation_fail_closed_on_forged_stale_mismatches_ac5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    topo = _setup_divergent_integrated_topology(tmp_path, monkeypatch)
    repo = topo["repo"]
    int_result = topo["int_result"]
    remediation_id = topo["remediation_id"]
    new_main = topo["new_main"]

    def run_with_override(**kwargs) -> bytes:
        fields = {
            "remediation_id": remediation_id,
            "base_sha": int_result.integration_candidate_sha,
            "cumulative_tip_run_id": topo["repair_2_id"],
            "cumulative_tip_candidate_sha": topo["candidate_sha"],
            "authorized_main_sha": new_main,
            "integration_candidate_sha": int_result.integration_candidate_sha,
            "integration_id": int_result.integration_id,
        }
        fields.update(kwargs)
        return _make_integrated_remediation_run(**fields)

    failure_payload = json.dumps({
        "kind": "FAILURE",
        "run_id": remediation_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": int_result.integration_candidate_sha,
        "failed_head_sha": int_result.integration_candidate_sha,
        "phase": "COMPLETION_GATE",
        "candidate": {
            "repairable": True,
            "transportable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }).encode()

    def check_malformed(run_bytes: bytes, main_sha: str = new_main) -> None:
        lifecycle = RemoteTaskLifecycle(
            main_sha,
            topo["base_terminals"] + (
                RemoteLifecycleTerminal(
                    remediation_id, "FAILURE", int_result.integration_candidate_sha,
                    run_bytes, failure_payload,
                ),
            ),
            topo["base_reviews"],
            ((topo["repair_2_id"], "F1", topo["selector_sha"]),),
            (),
            (),
        )
        _stub_unified_remote(monkeypatch, repo, lifecycle)
        obs = observe_unified_state("TASK-101", repo=repo).as_dict()
        assert obs["lifecycle_state"] == "BLOCKED"
        assert obs["blocker"]["code"] == "MALFORMED_CANONICAL_STATE"

    # 1. Mismatched cumulative tip RUN id
    forged_tip_run = json.loads(run_with_override().decode("utf-8"))
    forged_tip_run["execution_base"]["cumulative_tip_run_id"] = "RUN-101-999"
    check_malformed(json.dumps(forged_tip_run).encode())

    # 2. Mismatched cumulative tip candidate SHA
    forged_tip_sha = json.loads(run_with_override().decode("utf-8"))
    forged_tip_sha["execution_base"]["cumulative_tip_candidate_sha"] = "0" * 40
    check_malformed(json.dumps(forged_tip_sha).encode())

    # 3. Mismatched authorized main SHA
    forged_main_sha = json.loads(run_with_override().decode("utf-8"))
    forged_main_sha["execution_base"]["authorized_main_sha"] = "0" * 40
    check_malformed(json.dumps(forged_main_sha).encode())

    # 4. Canonical remote lifecycle main does not descend from authorized main / forged main
    check_malformed(run_with_override(), main_sha="a" * 40)
    check_malformed(run_with_override(), main_sha=topo["head"])

    # 5. Remote integration ref is missing or deleted from upstream
    upstream = tmp_path / "upstream.git"
    git(upstream, "update-ref", "-d", f"refs/heads/aios/integration/{int_result.integration_id}")
    check_malformed(run_with_override())
