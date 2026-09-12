# Tests for Correction Preflight readiness boundary.
import json
from pathlib import Path
import subprocess

import pytest

import aios_renew.correction_preflight as correction_preflight_module
from aios_renew.correction_preflight import (
    CorrectionPreflightResult as DirectCorrectionPreflightResult,
    preflight_remediation as direct_preflight_remediation,
    preflight_repair as direct_preflight_repair,
)
import aios_renew.operator as operator_module
from aios_renew.operator import (
    CorrectionPreflightResult,
    preflight_remediation,
    preflight_repair,
    runtime_paths,
)
from aios_renew.review_transport import (
    RemoteFailureArtifacts,
    RemoteRepairRecovery,
)
from tests.operator_test_support import (
    TASK_SOURCE,
    _runtime_bytes,
    git,
    make_repo,
    publish_test_remediation_lineage,
    repair_contract,
)

def _control_repository_snapshot(repo: Path) -> dict[str, object]:
    git_dir = repo / ".git"
    return {
        "head": git(repo, "rev-parse", "HEAD"),
        "index_tree": git(repo, "write-tree"),
        "status": git(repo, "status", "--porcelain"),
        "control_git": {
            path.relative_to(git_dir).as_posix(): path.read_bytes()
            for path in git_dir.rglob("*")
            if path.is_file() and path.relative_to(git_dir).parts[0] != "aios"
        },
        "runtime": _runtime_bytes(repo),
        "worktree": {
            path.relative_to(repo).as_posix(): path.read_bytes()
            for path in repo.rglob("*")
            if path.is_file() and path.relative_to(repo).parts[0] != ".git"
        },
    }

def test_correction_preflight_remediation_is_read_only_for_current_and_historical_subjects(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    reviewed_sha = git(repo, "rev-parse", "HEAD")
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        reviewed_sha=reviewed_sha,
    )
    before_runtime = _runtime_bytes(repo)

    current = preflight_remediation("TASK-101", finding_id="R1", repo=repo)

    assert current.status == "READY"
    assert current.subject_mode == "CURRENT"
    assert current.source_run_id == "RUN-101-000"
    assert current.reviewed_sha == reviewed_sha
    assert current.as_dict()["run_created"] is False
    assert current.as_dict()["executor_invoked"] is False
    assert _runtime_bytes(repo) == before_runtime

    (repo / "README.md").write_text("# current control\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "--quiet", "-m", "advance control")
    control = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        git(repo, "status", "--porcelain"),
    )
    before_runtime = _runtime_bytes(repo)

    historical = preflight_remediation("TASK-101", finding_id="R1", repo=repo)

    assert historical.status == "READY"
    assert historical.subject_mode == "HISTORICAL"
    assert historical.reviewed_sha == reviewed_sha
    assert (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        git(repo, "status", "--porcelain"),
    ) == control
    assert _runtime_bytes(repo) == before_runtime


def test_correction_preflight_remediation_blocks_malformed_lineage_without_state(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        extra_reviews=1,
    )
    before_runtime = _runtime_bytes(repo)

    observation = preflight_remediation("TASK-101", finding_id="R1", repo=repo)

    assert observation.status == "BLOCKED"
    assert observation.phase in operator_module._ADMISSION_PHASES
    assert observation.reason_code in operator_module._ADMISSION_REASONS
    assert observation.as_dict()["run_created"] is False
    assert _runtime_bytes(repo) == before_runtime


def test_correction_preflight_repair_preserves_reuse_action_order_and_state(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, no_change = repair_contract(repo, action="NO_CHANGE")
    state = runtime_paths(repo)

    ordinary = preflight_repair(failed_run_id, repo=repo, repair=no_change)

    assert ordinary.status == "READY"
    assert ordinary.action == "NO_CHANGE"
    assert ordinary.executor_required is True

    (state.preverification / f"{failed_run_id}.json").write_bytes(b"not-json")
    before_runtime = _runtime_bytes(repo)

    blocked = preflight_repair(failed_run_id, repo=repo, repair=no_change)

    assert blocked.status == "BLOCKED"
    assert blocked.phase == "REUSABLE_STATE_ADMISSION"
    assert blocked.reason_code == "REUSABLE_STATE_REJECTED"
    assert blocked.action == "NO_CHANGE"
    assert _runtime_bytes(repo) == before_runtime

    code_fix = dict(no_change)
    code_fix.update(
        {
            "repair_id": "REPAIR-101-CODE-FIX",
            "action": "CODE_FIX",
            "modification_scope": ["OUTPUT.txt"],
        }
    )
    ready = preflight_repair(failed_run_id, repo=repo, repair=code_fix)

    assert ready.status == "READY", ready.as_dict()
    assert ready.subject_mode == "CURRENT"
    assert ready.failed_run_id == failed_run_id
    assert ready.action == "CODE_FIX"
    assert ready.as_dict()["executor_invoked"] is False
    assert _runtime_bytes(repo) == before_runtime


def test_correction_preflight_continue_implementation_is_ready_and_bypasses_reuse(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, continuation = repair_contract(
        repo, action="CONTINUE_IMPLEMENTATION"
    )
    state = runtime_paths(repo)
    (state.preverification / f"{failed_run_id}.json").write_bytes(b"not-json")
    before_runtime = _runtime_bytes(repo)

    ready = preflight_repair(
        failed_run_id, repo=repo, repair=continuation
    )

    assert ready.status == "READY", ready.as_dict()
    assert ready.action == "CONTINUE_IMPLEMENTATION"
    assert ready.executor_required is True
    assert ready.failed_run_id == failed_run_id
    assert ready.failed_head_sha == continuation["failed_head_sha"]
    assert ready.subject_mode == "CURRENT"
    assert ready.as_dict()["executor_invoked"] is False
    assert _runtime_bytes(repo) == before_runtime


def test_correction_preflight_remote_repair_is_observational_for_ready_and_blocked(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(repo)
    author = tmp_path / "repair-author"
    subprocess.run(
        ("git", "clone", "--quiet", str(tmp_path / "upstream.git"), str(author)),
        check=True,
    )
    git(author, "config", "user.name", "AIOS Repair Author Test")
    git(author, "config", "user.email", "repair-author@example.invalid")
    repair_path = author / ".ai" / "transport" / "repair.json"
    repair_path.parent.mkdir(parents=True, exist_ok=True)

    def publish_remote_repair(payload: dict) -> None:
        repair_path.write_text(json.dumps(payload), encoding="utf-8")
        git(author, "add", ".ai/transport/repair.json")
        git(author, "commit", "--quiet", "-m", "author canonical repair")
        git(
            author,
            "push",
            "--quiet",
            "--force",
            "origin",
            f"HEAD:refs/heads/aios/repair/{failed_run_id}",
        )

    publish_remote_repair(repair)
    before_ready = _control_repository_snapshot(repo)

    ready = preflight_repair(failed_run_id, repo=repo)

    assert ready.as_dict() == {
        "format": "AIOS_CORRECTION_PREFLIGHT",
        "version": 1,
        "kind": "CORRECTION_PREFLIGHT",
        "family": "REPAIR",
        "status": "READY",
        "phase": "READY",
        "reason_code": "READY",
        "task": {"id": "TASK-101", "revision": 1},
        "source_run_id": None,
        "failed_run_id": failed_run_id,
        "review_id": None,
        "finding_id": None,
        "reviewed_sha": None,
        "failed_head_sha": repair["failed_head_sha"],
        "execution_base": None,
        "subject_mode": "CURRENT",
        "action": "CODE_FIX",
        "executor_required": True,
        "run_created": False,
        "executor_invoked": False,
    }
    assert _control_repository_snapshot(repo) == before_ready

    invalid_repair = dict(repair)
    invalid_repair["failed_head_sha"] = "different-failed-head"
    publish_remote_repair(invalid_repair)
    before_blocked = _control_repository_snapshot(repo)

    blocked = preflight_repair(failed_run_id, repo=repo)

    assert blocked.as_dict() == {
        "format": "AIOS_CORRECTION_PREFLIGHT",
        "version": 1,
        "kind": "CORRECTION_PREFLIGHT",
        "family": "REPAIR",
        "status": "BLOCKED",
        "phase": "CANONICAL_CONTRACT_ADMISSION",
        "reason_code": "TASK_CONTRACT_REJECTED",
        "task": {"id": "TASK-101", "revision": 1},
        "source_run_id": None,
        "failed_run_id": failed_run_id,
        "review_id": None,
        "finding_id": None,
        "reviewed_sha": None,
        "failed_head_sha": repair["failed_head_sha"],
        "execution_base": None,
        "subject_mode": None,
        "action": None,
        "executor_required": None,
        "run_created": False,
        "executor_invoked": False,
    }
    assert _control_repository_snapshot(repo) == before_blocked


def test_correction_preflight_historical_repair_preserves_subject_and_blocks_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    root_base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "--quiet", "-c", "failed-subject")
    (repo / "OUTPUT.txt").write_text("failed\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "historical failed subject")
    failed_head = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "--quiet", "main")
    (repo / "README.md").write_text("# advanced control\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "--quiet", "-m", "advance control")
    failed_run_id = "RUN-101-004"
    run = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": root_base_sha,
        "workspace": "historical-machine-path",
        "head_sha": None,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": root_base_sha,
        "failed_head_sha": failed_head,
        "phase": "COMPLETION_GATE",
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["OUTPUT.txt"],
            "outside_task_scope": [],
        },
    }
    artifact = RemoteFailureArtifacts(
        failed_run_id,
        failed_head,
        json.dumps(run).encode(),
        json.dumps(failure).encode(),
        None,
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_repair_recovery",
        lambda repo, *, failed_run_id: RemoteRepairRecovery(
            (artifact,), (failed_run_id,)
        ),
    )
    monkeypatch.setattr(
        operator_module,
        "read_remote_task",
        lambda repo, *, commit_sha, task_id: TASK_SOURCE.encode(),
    )
    repair = {
        "repair_id": "REPAIR-101-HISTORICAL-PREFLIGHT",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Correct only the historical subject."],
        "constraints": ["Commit the output."],
    }
    control = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "branch", "--show-current"),
        git(repo, "status", "--porcelain"),
    )
    before_runtime = _runtime_bytes(repo)

    ready = preflight_repair(failed_run_id, repo=repo, repair=repair)

    assert ready.status == "READY", json.dumps(ready.as_dict(), sort_keys=True)
    assert ready.subject_mode == "HISTORICAL"
    assert ready.failed_head_sha == failed_head
    assert (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "branch", "--show-current"),
        git(repo, "status", "--porcelain"),
    ) == control
    assert _runtime_bytes(repo) == before_runtime

    def duplicate(repo, *, failed_run_id):
        raise operator_module.ReviewTransportError(
            "canonical continuation already exists for failed RUN"
        )

    monkeypatch.setattr(operator_module, "resolve_remote_repair_recovery", duplicate)
    blocked = preflight_repair(failed_run_id, repo=repo, repair=repair)

    assert blocked.status == "BLOCKED"
    assert blocked.phase == "FAILED_RUN_RESOLUTION"
    assert blocked.reason_code == "CANONICAL_LINEAGE_MISSING"
    assert _runtime_bytes(repo) == before_runtime


def test_correction_preflight_module_boundary_and_operator_compatibility() -> None:
    assert operator_module.CorrectionPreflightResult is DirectCorrectionPreflightResult
    assert operator_module.preflight_remediation is direct_preflight_remediation
    assert operator_module.preflight_repair is direct_preflight_repair
    assert correction_preflight_module.CorrectionPreflightResult is operator_module.CorrectionPreflightResult
    assert correction_preflight_module.preflight_remediation is operator_module.preflight_remediation
    assert correction_preflight_module.preflight_repair is operator_module.preflight_repair
