"""Tests for explicit correction integration boundary."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import pytest

from aios_renew.correction_integration import (
    CorrectionIntegrationError,
    CorrectionIntegrationResult,
    derive_integration_identity,
    integration_ref_name,
    integrate_correction,
    resolve_valid_integration,
)
from aios_renew.operator import (
    OperatorError,
    integrate_correction as operator_integrate_correction,
    main as operator_main,
)
from tests.operator_test_support import (
    TASK_SOURCE,
    canonical_result_payload,
    git,
    make_repo,
    publish_test_remediation_lineage,
)
from aios_renew.review_transport import (
    RemoteLifecycleReview,
    RemoteLifecycleTerminal,
    RemoteTaskLifecycle,
)
import aios_renew.operator as operator_module


def test_derive_integration_identity_deterministic() -> None:
    id1 = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", "a" * 40, "b" * 40
    )
    id2 = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", "a" * 40, "b" * 40
    )
    assert id1 == id2
    assert len(id1) == 64

    # Any field variation yields a different id
    assert id1 != derive_integration_identity(
        "TASK-102", 1, "RUN-101-001", "a" * 40, "b" * 40
    )
    assert id1 != derive_integration_identity(
        "TASK-101", 2, "RUN-101-001", "a" * 40, "b" * 40
    )
    assert id1 != derive_integration_identity(
        "TASK-101", 1, "RUN-101-002", "a" * 40, "b" * 40
    )
    assert id1 != derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", "c" * 40, "b" * 40
    )
    assert id1 != derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", "a" * 40, "c" * 40
    )


def test_integration_ref_name() -> None:
    ref = integration_ref_name("abc123def")
    assert ref == "refs/heads/aios/integration/abc123def"


def test_clean_divergence_integration_success(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    base_head = git(repo, "rev-parse", "HEAD")

    # Divergent tip: commit on another branch
    git(repo, "checkout", "-b", "correction-tip")
    (repo / "CORRECTION.txt").write_text("correction\n", encoding="utf-8")
    git(repo, "add", "CORRECTION.txt")
    git(repo, "commit", "-m", "correction commit")
    tip_sha = git(repo, "rev-parse", "HEAD")

    # Main advances independently with non-conflicting change
    git(repo, "checkout", "main")
    (repo / "MAIN_UPDATE.txt").write_text("main update\n", encoding="utf-8")
    git(repo, "add", "MAIN_UPDATE.txt")
    git(repo, "commit", "-m", "advance main")
    main_sha = git(repo, "rev-parse", "HEAD")

    head_before = git(repo, "rev-parse", "HEAD")
    status_before = git(repo, "status", "--porcelain")

    result = integrate_correction(
        "TASK-101",
        task_revision=1,
        cumulative_tip_run_id="RUN-101-001",
        cumulative_tip_candidate_sha=tip_sha,
        authorized_main_sha=main_sha,
        repo=repo,
    )

    # Worktree, branch, HEAD, status must be untouched
    assert git(repo, "rev-parse", "HEAD") == head_before
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(repo, "status", "--porcelain") == status_before

    # Verify result commit properties
    candidate_sha = result.integration_candidate_sha
    assert result.integration_ref == f"refs/heads/aios/integration/{result.integration_id}"
    assert git(repo, "rev-parse", "--verify", result.integration_ref) == candidate_sha

    parents = git(repo, "rev-parse", f"{candidate_sha}^@").split()
    assert parents == [tip_sha, main_sha]

    expected_tree = git(repo, "merge-tree", "--write-tree", tip_sha, main_sha).splitlines()[0]
    actual_tree = git(repo, "rev-parse", f"{candidate_sha}^{{tree}}")
    assert actual_tree == expected_tree
    assert result.tree_sha == expected_tree

    # Verify resolve_valid_integration finds it
    resolved = resolve_valid_integration(
        repo,
        task_id="TASK-101",
        task_revision=1,
        cumulative_tip_run_id="RUN-101-001",
        cumulative_tip_candidate_sha=tip_sha,
        authorized_main_sha=main_sha,
    )
    assert resolved is not None
    assert resolved.integration_candidate_sha == candidate_sha

    # Idempotent replay
    replay_result = integrate_correction(
        "TASK-101",
        task_revision=1,
        cumulative_tip_run_id="RUN-101-001",
        cumulative_tip_candidate_sha=tip_sha,
        authorized_main_sha=main_sha,
        repo=repo,
    )
    assert replay_result.integration_candidate_sha == candidate_sha


def test_integration_stale_main_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    base_head = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "correction-tip")
    (repo / "CORRECTION.txt").write_text("correction\n", encoding="utf-8")
    git(repo, "add", "CORRECTION.txt")
    git(repo, "commit", "-m", "correction commit")
    tip_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "main")
    (repo / "MAIN_UPDATE.txt").write_text("main update\n", encoding="utf-8")
    git(repo, "add", "MAIN_UPDATE.txt")
    git(repo, "commit", "-m", "advance main")
    actual_main_sha = git(repo, "rev-parse", "HEAD")

    # Stale authorized main SHA
    with pytest.raises(CorrectionIntegrationError, match="does not match authorized main SHA"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha=tip_sha,
            authorized_main_sha=base_head,  # stale!
            repo=repo,
        )


def test_integration_stale_tip_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    main_sha = git(repo, "rev-parse", "HEAD")

    with pytest.raises(CorrectionIntegrationError, match="is missing or not a commit"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha="0" * 40,
            authorized_main_sha=main_sha,
            repo=repo,
        )


def test_integration_merge_conflict_fails_closed_without_ref(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    # Conflicting change on branch
    git(repo, "checkout", "-b", "conflict-branch")
    (repo / "SHARED.txt").write_text("content on branch\n", encoding="utf-8")
    git(repo, "add", "SHARED.txt")
    git(repo, "commit", "-m", "branch commit")
    tip_sha = git(repo, "rev-parse", "HEAD")

    # Conflicting change on main
    git(repo, "checkout", "main")
    (repo / "SHARED.txt").write_text("content on main\n", encoding="utf-8")
    git(repo, "add", "SHARED.txt")
    git(repo, "commit", "-m", "main commit")
    main_sha = git(repo, "rev-parse", "HEAD")

    integration_id = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", tip_sha, main_sha
    )
    expected_ref = integration_ref_name(integration_id)

    with pytest.raises(CorrectionIntegrationError, match="merge conflict"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha=tip_sha,
            authorized_main_sha=main_sha,
            repo=repo,
        )

    # No ref created
    proc = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--verify", expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    # Worktree clean
    assert git(repo, "status", "--porcelain") == ""


def test_operator_integrate_correction_wrapper_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)

    git(repo, "checkout", "-b", "correction-tip")
    (repo / "CORRECTION.txt").write_text("correction\n", encoding="utf-8")
    git(repo, "add", "CORRECTION.txt")
    git(repo, "commit", "-m", "correction commit")
    tip_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "main")
    (repo / "MAIN_UPDATE.txt").write_text("main update\n", encoding="utf-8")
    git(repo, "add", "MAIN_UPDATE.txt")
    git(repo, "commit", "-m", "advance main")
    main_sha = git(repo, "rev-parse", "HEAD")

    # Python operator wrapper raises OperatorError on error
    with pytest.raises(OperatorError):
        operator_integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha=tip_sha,
            authorized_main_sha="1" * 40,  # mismatch
            repo=repo,
        )

    # CLI test with --json
    capsys.readouterr()
    exit_code = operator_main([
        "integrate-correction",
        "TASK-101",
        "--task-revision", "1",
        "--cumulative-tip-run", "RUN-101-001",
        "--cumulative-tip-candidate", tip_sha,
        "--authorized-main", main_sha,
        "--repo", str(repo),
        "--json",
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["format"] == "AIOS_CORRECTION_INTEGRATION"
    assert data["kind"] == "CORRECTION_INTEGRATION"
    assert data["cumulative_tip_candidate_sha"] == tip_sha
    assert data["authorized_main_sha"] == main_sha
    assert len(data["parents"]) == 2

    # CLI alias: integrate
    exit_code_alias = operator_main([
        "integrate",
        "TASK-101",
        "--task-revision", "1",
        "--cumulative-tip-run", "RUN-101-001",
        "--cumulative-tip-candidate", tip_sha,
        "--authorized-main", main_sha,
        "--repo", str(repo),
    ])
    assert exit_code_alias == 0
    captured_alias = capsys.readouterr()
    assert "AIOS CORRECTION INTEGRATION SUCCEEDED" in captured_alias.out
