import subprocess
from pathlib import Path

import pytest

from aios_renew import (
    CodexExecutionError,
    ResultPackage,
    Run,
    Task,
    parse_evidence,
    parse_result,
    parse_task,
)
from scripts.codex_smoke import (
    PROJECT_ROOT,
    SMOKE_CONTENT,
    SMOKE_TASK_SOURCE,
    SmokeFailure,
    initialize_smoke_repository,
    run_smoke,
    verify_smoke_result,
)


def git(workspace: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(workspace), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def result_package(
    run_id: str,
    head_sha: str,
    *,
    satisfies: tuple[str, ...] = ("AC1", "AC2", "AC3"),
    evidence_exit_code: int = 0,
    include_evidence: bool = True,
) -> ResultPackage:
    satisfies_yaml = ", ".join(satisfies)
    result = parse_result(
        f"""
head_sha: {head_sha}
claims:
  - id: C1
    satisfies: [{satisfies_yaml}]
    claim: The smoke target was created and committed.
    evidence: [E1]
changed_files:
  - SMOKE_OK.txt
unresolved: []
"""
    )
    evidence = parse_evidence(
        f"""
evidence_id: E1
run_id: {run_id}
subject_sha: {head_sha}
type: TEST
source:
  command: deterministic smoke verification
result:
  exit_code: {evidence_exit_code}
  summary: file and Git state verified
raw:
  path: .ai/evidence/E1.log
"""
    )
    return ResultPackage(
        result=result,
        evidence=(evidence,) if include_evidence else (),
    )


def empty_result_package(head_sha: str) -> ResultPackage:
    result = parse_result(
        f"""
head_sha: {head_sha}
claims: []
changed_files:
  - SMOKE_OK.txt
unresolved: []
"""
    )
    return ResultPackage(result=result, evidence=())


def commit_smoke_file(workspace: Path, content: bytes = SMOKE_CONTENT) -> str:
    (workspace / "SMOKE_OK.txt").write_bytes(content)
    git(workspace, "add", "SMOKE_OK.txt")
    git(workspace, "commit", "--quiet", "-m", "create smoke marker")
    return git(workspace, "rev-parse", "HEAD")


class FakeCodexAdapter:
    executor = "codex"

    def __init__(
        self,
        *,
        content: bytes = SMOKE_CONTENT,
        reported_head: str | None = None,
    ) -> None:
        self.content = content
        self.reported_head = reported_head
        self.calls: list[tuple[Task, Run]] = []

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        self.calls.append((task, run))
        actual_head = commit_smoke_file(Path(run.workspace), self.content)
        return result_package(run.run_id, self.reported_head or actual_head)


class FailingCodexAdapter:
    executor = "codex"

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        raise CodexExecutionError("native failure", exit_code=7)


def test_temporary_repo_creation_produces_real_baseline_sha(tmp_path: Path) -> None:
    workspace = tmp_path / "smoke-repo"

    base_sha = initialize_smoke_repository(workspace)

    assert base_sha == git(workspace, "rev-parse", "HEAD")
    assert len(base_sha) == 40
    assert git(workspace, "status", "--porcelain") == ""


def test_smoke_task_requests_deterministic_git_head_verification() -> None:
    task = parse_task(SMOKE_TASK_SOURCE)

    assert "git rev-parse HEAD" in task.verification.required


def test_smoke_task_requires_complete_success_claim_coverage() -> None:
    task = parse_task(SMOKE_TASK_SOURCE)
    constraints = "\n".join(task.constraints.hard)

    assert "combined satisfies values cover AC1, AC2, and AC3" in constraints
    assert "Every claim used to cover AC1, AC2, or AC3" in constraints
    assert "MUST reference at least one EVIDENCE item" in constraints
    assert "MUST have exit_code equal to 0" in constraints


def test_smoke_task_derives_result_head_from_final_git_head() -> None:
    task = parse_task(SMOKE_TASK_SOURCE)
    constraints = "\n".join(task.constraints.hard)

    assert "After committing the final repository state" in constraints
    assert "obtain the actual final commit SHA using git rev-parse HEAD" in constraints
    assert "RESULT.head_sha MUST be exactly the actual final commit SHA" in constraints


def test_smoke_task_binds_ac3_to_git_head_evidence() -> None:
    task = parse_task(SMOKE_TASK_SOURCE)
    constraints = "\n".join(task.constraints.hard)

    assert "AC3 MUST be supported" in constraints
    assert "Git HEAD verification EVIDENCE produced by git rev-parse HEAD" in constraints


def test_smoke_task_keeps_all_deterministic_verification_commands() -> None:
    task = parse_task(SMOKE_TASK_SOURCE)

    assert (
        "python -c \"from pathlib import Path; assert "
        "Path('SMOKE_OK.txt').read_bytes() == b'AIOS smoke pass\\n'\""
        in task.verification.required
    )
    assert "git rev-parse HEAD" in task.verification.required
    assert "git status --porcelain" in task.verification.required


def test_smoke_task_requests_clean_worktree_verification() -> None:
    task = parse_task(SMOKE_TASK_SOURCE)

    assert "git status --porcelain" in task.verification.required


def test_expected_file_and_content_verification_passes(tmp_path: Path) -> None:
    workspace = tmp_path / "smoke-repo"
    base_sha = initialize_smoke_repository(workspace)
    head_sha = commit_smoke_file(workspace)

    summary = verify_smoke_result(
        workspace=workspace,
        base_sha=base_sha,
        package=result_package("RUN-009-SMOKE", head_sha),
    )

    assert summary.head_sha == head_sha
    assert summary.working_tree == "clean"


def test_empty_claims_and_evidence_are_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "smoke-repo"
    base_sha = initialize_smoke_repository(workspace)
    head_sha = commit_smoke_file(workspace)

    with pytest.raises(SmokeFailure, match="missing smoke acceptance coverage"):
        verify_smoke_result(
            workspace=workspace,
            base_sha=base_sha,
            package=empty_result_package(head_sha),
        )


def test_missing_ac3_coverage_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "smoke-repo"
    base_sha = initialize_smoke_repository(workspace)
    head_sha = commit_smoke_file(workspace)

    with pytest.raises(SmokeFailure, match="coverage: AC3"):
        verify_smoke_result(
            workspace=workspace,
            base_sha=base_sha,
            package=result_package(
                "RUN-009-SMOKE",
                head_sha,
                satisfies=("AC1", "AC2"),
            ),
        )


def test_nonzero_supporting_evidence_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "smoke-repo"
    base_sha = initialize_smoke_repository(workspace)
    head_sha = commit_smoke_file(workspace)

    with pytest.raises(SmokeFailure, match="E1 must have exit_code 0"):
        verify_smoke_result(
            workspace=workspace,
            base_sha=base_sha,
            package=result_package(
                "RUN-009-SMOKE",
                head_sha,
                evidence_exit_code=1,
            ),
        )


def test_missing_supporting_evidence_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "smoke-repo"
    base_sha = initialize_smoke_repository(workspace)
    head_sha = commit_smoke_file(workspace)

    with pytest.raises(SmokeFailure, match="missing evidence: E1"):
        verify_smoke_result(
            workspace=workspace,
            base_sha=base_sha,
            package=result_package(
                "RUN-009-SMOKE",
                head_sha,
                include_evidence=False,
            ),
        )


def test_wrong_content_fails(tmp_path: Path) -> None:
    with pytest.raises(SmokeFailure, match="content is not exactly"):
        run_smoke(
            tmp_path / "smoke-repo",
            adapter=FakeCodexAdapter(content=b"wrong\n"),
        )


def test_result_head_sha_mismatch_fails(tmp_path: Path) -> None:
    with pytest.raises(SmokeFailure, match="does not match actual final Git HEAD"):
        run_smoke(
            tmp_path / "smoke-repo",
            adapter=FakeCodexAdapter(reported_head="deadbeef"),
        )


def test_executor_native_failure_becomes_smoke_failure(tmp_path: Path) -> None:
    with pytest.raises(SmokeFailure, match="native failure") as captured:
        run_smoke(
            tmp_path / "smoke-repo",
            adapter=FailingCodexAdapter(),
        )

    assert isinstance(captured.value.__cause__, CodexExecutionError)


def test_canonical_repo_path_is_not_used_as_smoke_workspace() -> None:
    with pytest.raises(SmokeFailure, match="outside the canonical repository"):
        run_smoke(PROJECT_ROOT, adapter=FakeCodexAdapter())


def test_successful_fake_execution_produces_compact_pass_result(
    tmp_path: Path,
) -> None:
    adapter = FakeCodexAdapter()
    summary = run_smoke(tmp_path / "smoke-repo", adapter=adapter)

    rendered = summary.render()
    assert rendered.startswith("SMOKE PASS\n")
    assert "run_id: RUN-009-SMOKE" in rendered
    assert f"base_sha: {summary.base_sha}" in rendered
    assert f"head_sha: {summary.head_sha}" in rendered
    assert "changed_files: SMOKE_OK.txt" in rendered
    assert "E1: file and Git state verified" in rendered
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1].base_sha == summary.base_sha
    assert adapter.calls[0][1].workspace != str(PROJECT_ROOT)
