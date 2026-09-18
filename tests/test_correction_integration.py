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


def _publish_terminal_result(
    repo: Path,
    run_id: str,
    head_sha: str,
    *,
    task_id: str = "TASK-101",
    task_revision: int = 1,
    base_sha: str | None = None,
) -> None:
    from aios_renew.operator import runtime_paths
    from aios_renew.review_transport import transport_post_pass

    state = runtime_paths(repo)
    run_file = state.runs / f"{run_id}.json"
    res_file = state.results / f"{run_id}.json"
    run_file.parent.mkdir(parents=True, exist_ok=True)
    res_file.parent.mkdir(parents=True, exist_ok=True)
    run_file.write_text(
        json.dumps({
            "run_id": run_id,
            "task": {"id": task_id, "revision": task_revision},
            "executor": "antigravity",
            "base_sha": base_sha or head_sha,
            "workspace": str(repo),
            "head_sha": None,
            "status": "ACTIVE",
        }),
        encoding="utf-8",
    )
    payload = canonical_result_payload(run_id, head_sha)
    res_file.write_text(json.dumps(payload), encoding="utf-8")
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_file,
        result_path=res_file,
    )


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
    git(repo, "push", "--quiet", "origin", "main")

    _publish_terminal_result(repo, "RUN-101-001", tip_sha, base_sha=base_head)

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
    git(repo, "push", "--quiet", "origin", "main")

    _publish_terminal_result(repo, "RUN-101-001", tip_sha, base_sha=base_head)

    integration_id = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", tip_sha, base_head
    )
    expected_ref = integration_ref_name(integration_id)

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

    # No ref created
    proc = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--verify", expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_integration_stale_tip_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    base_head = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "correction-tip")
    (repo / "CORRECTION.txt").write_text("correction\n", encoding="utf-8")
    git(repo, "add", "CORRECTION.txt")
    git(repo, "commit", "-m", "correction commit")
    tip_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "main")
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    _publish_terminal_result(repo, "RUN-101-001", tip_sha, base_sha=base_head)

    with pytest.raises(CorrectionIntegrationError, match="cumulative tip selector is stale"):
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
    base_head = git(repo, "rev-parse", "HEAD")

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
    git(repo, "push", "--quiet", "origin", "main")

    _publish_terminal_result(repo, "RUN-101-001", tip_sha, base_sha=base_head)

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


def test_integration_remote_main_ahead_fails_closed_without_ref(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    base_head = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "correction-tip")
    (repo / "CORRECTION.txt").write_text("correction\n", encoding="utf-8")
    git(repo, "add", "CORRECTION.txt")
    git(repo, "commit", "-m", "correction commit")
    tip_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "main")
    (repo / "MAIN1.txt").write_text("main 1\n", encoding="utf-8")
    git(repo, "add", "MAIN1.txt")
    git(repo, "commit", "-m", "main 1")
    authorized_main_sha = git(repo, "rev-parse", "HEAD")

    (repo / "MAIN2.txt").write_text("main 2\n", encoding="utf-8")
    git(repo, "add", "MAIN2.txt")
    git(repo, "commit", "-m", "main 2 ahead")
    git(repo, "push", "--quiet", "origin", "main")

    _publish_terminal_result(repo, "RUN-101-001", tip_sha, base_sha=base_head)

    integration_id = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", tip_sha, authorized_main_sha
    )
    expected_ref = integration_ref_name(integration_id)

    with pytest.raises(CorrectionIntegrationError, match="canonical remote main SHA"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha=tip_sha,
            authorized_main_sha=authorized_main_sha,
            repo=repo,
        )

    # Local ref must not exist
    proc = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--verify", expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0

    # Remote ref must not exist
    proc_ls = subprocess.run(
        ("git", "-C", str(repo), "ls-remote", "--refs", "origin", expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_ls.stdout.strip() == ""


def test_integration_remote_lifecycle_unavailable_fails_closed_without_ref(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    base_head = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "correction-tip")
    (repo / "CORRECTION.txt").write_text("correction\n", encoding="utf-8")
    git(repo, "add", "CORRECTION.txt")
    git(repo, "commit", "-m", "correction commit")
    tip_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "main")
    main_sha = git(repo, "rev-parse", "HEAD")

    # Point origin to a nonexistent remote repository path
    git(repo, "remote", "set-url", "origin", str(tmp_path / "nonexistent.git"))

    integration_id = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", tip_sha, main_sha
    )
    expected_ref = integration_ref_name(integration_id)

    with pytest.raises(CorrectionIntegrationError, match="failed to resolve canonical remote task lifecycle"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha=tip_sha,
            authorized_main_sha=main_sha,
            repo=repo,
        )

    proc = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--verify", expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_integration_remote_lifecycle_malformed_fails_closed_without_ref(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    base_head = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "correction-tip")
    (repo / "CORRECTION.txt").write_text("correction\n", encoding="utf-8")
    git(repo, "add", "CORRECTION.txt")
    git(repo, "commit", "-m", "correction commit")
    tip_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "main")
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    # Push malformed json into upstream artifact ref
    corrupt_author = tmp_path / "corrupt_author"
    subprocess.run(
        ("git", "clone", "--quiet", str(tmp_path / "upstream.git"), str(corrupt_author)),
        check=True,
    )
    git(corrupt_author, "config", "user.name", "Corrupter")
    git(corrupt_author, "config", "user.email", "corrupt@example.invalid")
    corrupt_transport = corrupt_author / ".ai" / "transport"
    corrupt_transport.mkdir(parents=True, exist_ok=True)
    (corrupt_transport / "run.json").write_bytes(b"INVALID_JSON{{{")
    (corrupt_transport / "result.json").write_bytes(b"INVALID_JSON{{{")
    git(corrupt_author, "add", ".ai")
    git(corrupt_author, "commit", "-m", "corrupt artifact")
    git(corrupt_author, "push", "--quiet", "origin", "HEAD:refs/heads/aios/artifacts/RUN-101-001")
    git(repo, "push", "--quiet", "origin", f"{tip_sha}:refs/heads/aios/review/RUN-101-001")

    integration_id = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", tip_sha, main_sha
    )
    expected_ref = integration_ref_name(integration_id)

    with pytest.raises(CorrectionIntegrationError, match="failed to resolve canonical remote task lifecycle|failed to decode canonical remote task lifecycle"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha=tip_sha,
            authorized_main_sha=main_sha,
            repo=repo,
        )

    proc = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--verify", expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_integration_missing_tip_fails_closed_without_ref(tmp_path: Path) -> None:
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
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    # No terminal published on origin for this task
    integration_id = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", tip_sha, main_sha
    )
    expected_ref = integration_ref_name(integration_id)

    with pytest.raises(CorrectionIntegrationError, match="canonical remote task lifecycle has no operational tips"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha=tip_sha,
            authorized_main_sha=main_sha,
            repo=repo,
        )

    proc = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--verify", expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_integration_competing_tips_fails_closed_without_ref(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    base_head = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "correction-tip-1")
    (repo / "CORRECTION1.txt").write_text("correction 1\n", encoding="utf-8")
    git(repo, "add", "CORRECTION1.txt")
    git(repo, "commit", "-m", "correction 1")
    tip_1 = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "correction-tip-2", base_head)
    (repo / "CORRECTION2.txt").write_text("correction 2\n", encoding="utf-8")
    git(repo, "add", "CORRECTION2.txt")
    git(repo, "commit", "-m", "correction 2")
    tip_2 = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "main")
    (repo / "MAIN_UPDATE.txt").write_text("main update\n", encoding="utf-8")
    git(repo, "add", "MAIN_UPDATE.txt")
    git(repo, "commit", "-m", "advance main")
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    _publish_terminal_result(repo, "RUN-101-001", tip_1, base_sha=base_head)
    _publish_terminal_result(repo, "RUN-101-002", tip_2, base_sha=base_head)

    integration_id = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", tip_1, main_sha
    )
    expected_ref = integration_ref_name(integration_id)

    with pytest.raises(CorrectionIntegrationError, match="competing operational tips"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha=tip_1,
            authorized_main_sha=main_sha,
            repo=repo,
        )

    proc = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--verify", expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_operator_integrate_correction_wrapper_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    _publish_terminal_result(repo, "RUN-101-001", tip_sha, base_sha=base_head)

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


def test_integration_remote_push_failure_fails_closed_and_rolls_back_local_ref(tmp_path: Path) -> None:
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
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    _publish_terminal_result(repo, "RUN-101-001", tip_sha, base_sha=base_head)

    integration_id = derive_integration_identity(
        "TASK-101", 1, "RUN-101-001", tip_sha, main_sha
    )
    expected_ref = integration_ref_name(integration_id)

    # Force remote push failure by setting an unreachable push URL on origin
    git(repo, "remote", "set-url", "--push", "origin", str(tmp_path / "broken_push.git"))

    with pytest.raises(CorrectionIntegrationError, match="failed to push integration ref"):
        integrate_correction(
            "TASK-101",
            task_revision=1,
            cumulative_tip_run_id="RUN-101-001",
            cumulative_tip_candidate_sha=tip_sha,
            authorized_main_sha=main_sha,
            repo=repo,
        )

    # Local ref must be rolled back and not exist
    proc_loc = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--verify", expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_loc.returncode != 0

    # Remote ref must not exist on origin
    proc_rem = subprocess.run(
        ("git", "-C", str(repo), "ls-remote", "--refs", str(tmp_path / "upstream.git"), expected_ref),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_rem.stdout.strip() == ""

    # resolve_valid_integration fails closed
    assert resolve_valid_integration(
        repo,
        task_id="TASK-101",
        task_revision=1,
        cumulative_tip_run_id="RUN-101-001",
        cumulative_tip_candidate_sha=tip_sha,
        authorized_main_sha=main_sha,
    ) is None

    # CLI also fails closed with non-zero exit code
    exit_code = operator_main([
        "integrate-correction",
        "TASK-101",
        "--task-revision", "1",
        "--cumulative-tip-run", "RUN-101-001",
        "--cumulative-tip-candidate", tip_sha,
        "--authorized-main", main_sha,
        "--repo", str(repo),
    ])
    assert exit_code != 0

    # Local-only staging test: if a local ref exists but canonical remote integration ref is missing,
    # resolve_valid_integration must refuse it
    git(repo, "remote", "set-url", "--push", "origin", str(tmp_path / "upstream.git"))
    dummy_commit = tip_sha
    git(repo, "update-ref", expected_ref, dummy_commit)
    assert resolve_valid_integration(
        repo,
        task_id="TASK-101",
        task_revision=1,
        cumulative_tip_run_id="RUN-101-001",
        cumulative_tip_candidate_sha=tip_sha,
        authorized_main_sha=main_sha,
    ) is None
