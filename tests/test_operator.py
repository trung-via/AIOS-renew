import inspect
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

import aios_renew.operator as operator_module
from aios_renew.operator import (
    OperatorError,
    describe_task,
    load_task,
    resolve_repository,
    run_task,
    runtime_paths,
)


TASK_SOURCE = """
task_id: TASK-101
revision: 1
goal: Create one deterministic operator test output.
problem: Exercise the thin operator without a real executor.
assumptions: []
scope:
  inspect: []
  modify:
    - OUTPUT.txt
non_goals:
  - Change the frozen kernel.
constraints:
  hard:
    - Commit the output.
acceptance:
  - id: AC1
    condition: OUTPUT.txt is committed.
verification:
  required:
    - git status --porcelain
"""

MULTI_ACCEPTANCE_TASK_SOURCE = TASK_SOURCE.replace(
    "verification:\n",
    "  - id: AC2\n    condition: The second criterion is satisfied.\nverification:\n",
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(
    root: Path,
    *,
    task_source: str | None = TASK_SOURCE,
) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "AIOS Operator Test")
    git(repo, "config", "user.email", "operator@example.invalid")
    (repo / "README.md").write_text("# operator test\n", encoding="utf-8")
    if task_source is not None:
        task_dir = repo / ".ai" / "tasks"
        task_dir.mkdir(parents=True)
        (task_dir / "TASK-101.yaml").write_text(task_source, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "baseline")
    return repo


def result_payload(
    run_id: str,
    head_sha: str,
    *,
    changed_files: list[str] | None = None,
) -> dict:
    files = ["OUTPUT.txt"] if changed_files is None else changed_files
    return {
        "result": {
            "head_sha": head_sha,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "The operator output was committed.",
                    "evidence": ["E1"],
                }
            ],
            "changed_files": files,
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id,
                "subject_sha": head_sha,
                "type": "TEST",
                "source": {"command": "git status --porcelain"},
                "result": {"exit_code": 0, "summary": "verified"},
                "raw": {"path": ".ai/evidence/E1.log"},
            }
        ],
    }


class FakeCodexRunner:
    def __init__(
        self,
        repo: Path,
        *,
        reported_head: str | None = None,
        dirty_after: bool = False,
    ) -> None:
        self.repo = repo
        self.reported_head = reported_head
        self.dirty_after = dirty_after
        self.calls = []
        self.count = 0

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.count += 1
        canonical = json.loads(kwargs["input"].split("CANONICAL_INPUT:\n", 1)[1])
        run_id = canonical["run"]["run_id"]
        (self.repo / "OUTPUT.txt").write_text(
            f"operator output {self.count}\n",
            encoding="utf-8",
        )
        git(self.repo, "add", "OUTPUT.txt")
        git(self.repo, "commit", "--quiet", "-m", f"executor {self.count}")
        actual_head = git(self.repo, "rev-parse", "HEAD")
        if self.dirty_after:
            (self.repo / "DIRTY.txt").write_text("dirty\n", encoding="utf-8")
        payload = result_payload(run_id, self.reported_head or actual_head)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )


class FakeAntigravityRunner:
    def __init__(
        self,
        repo: Path,
        *,
        mode: str = "success",
    ) -> None:
        self.repo = repo
        self.mode = mode
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.mode == "missing":
            raise FileNotFoundError("agy")
        if self.mode == "nonzero":
            return subprocess.CompletedProcess(
                command,
                returncode=9,
                stdout="",
                stderr="agy failed",
            )
        if self.mode == "no-result":
            return subprocess.CompletedProcess(
                command,
                returncode=0,
                stdout="done",
                stderr="",
            )
        if self.mode == "no-result-stderr":
            return subprocess.CompletedProcess(
                command,
                returncode=0,
                stdout="less useful stdout",
                stderr="headless tool action denied",
            )
        if self.mode == "no-result-empty":
            return subprocess.CompletedProcess(
                command,
                returncode=0,
                stdout="",
                stderr="",
            )

        handoff_path = next(
            (self.repo / ".git" / "aios" / "handoffs").glob("*.json")
        )
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        result_path = Path(handoff["result_package_path"])
        if self.mode == "invalid":
            result_path.write_text("{}", encoding="utf-8")
        else:
            (self.repo / "OUTPUT.txt").write_text(
                "antigravity output\n",
                encoding="utf-8",
            )
            git(self.repo, "add", "OUTPUT.txt")
            git(self.repo, "commit", "--quiet", "-m", "antigravity executor")
            head_sha = git(self.repo, "rev-parse", "HEAD")
            payload = result_payload(handoff["run"]["run_id"], head_sha)
            result_path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout="operator prose is not authoritative",
            stderr="",
        )


class StaticResultRunner:
    def __init__(self, repo: Path, result: dict) -> None:
        self.repo = repo
        self.result = result

    def __call__(self, command, **kwargs):
        payload = json.loads(json.dumps(self.result))
        if command[0] == "agy":
            handoff_path = next(
                (self.repo / ".git" / "aios" / "handoffs").glob("*.json")
            )
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            run_id = handoff["run"]["run_id"]
            result_path = Path(handoff["result_package_path"])
            payload["result"]["head_sha"] = git(self.repo, "rev-parse", "HEAD")
            for item in payload["evidence"]:
                item["run_id"] = run_id
                item["subject_sha"] = payload["result"]["head_sha"]
            result_path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            canonical = json.loads(kwargs["input"].split("CANONICAL_INPUT:\n", 1)[1])
            run_id = canonical["run"]["run_id"]
            payload["result"]["head_sha"] = git(self.repo, "rev-parse", "HEAD")
            for item in payload["evidence"]:
                item["run_id"] = run_id
                item["subject_sha"] = payload["result"]["head_sha"]
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )


def static_payload(
    *,
    satisfies: list[str] | None = None,
    unresolved: list[str] | None = None,
) -> dict:
    criteria = ["AC1"] if satisfies is None else satisfies
    claims = []
    evidence = []
    if criteria:
        claims.append(
            {
                "id": "C1",
                "satisfies": criteria,
                "claim": "The stated acceptance criteria are satisfied.",
                "evidence": ["E1"],
            }
        )
        evidence.append(
            {
                "evidence_id": "E1",
                "run_id": "replaced-by-runner",
                "subject_sha": "replaced-by-runner",
                "type": "TEST",
                "source": {"command": "git status --porcelain"},
                "result": {"exit_code": 0, "summary": "verified"},
                "raw": {"path": ".ai/evidence/E1.log"},
            }
        )
    return {
        "result": {
            "head_sha": "replaced-by-runner",
            "claims": claims,
            "changed_files": [],
            "unresolved": [] if unresolved is None else unresolved,
        },
        "evidence": evidence,
    }


def test_task_resolution_and_compact_description(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    task = load_task(repo, "TASK-101")
    rendered = describe_task("TASK-101", repo=repo).render()

    assert task.task_id == "TASK-101"
    assert rendered.startswith("TASK-101\nrevision: 1")
    assert "acceptance: AC1" in rendered
    assert "- git status --porcelain" in rendered


def test_missing_task_fails(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, task_source=None)

    with pytest.raises(OperatorError, match="TASK not found"):
        load_task(repo, "TASK-101")


def test_requested_task_id_mismatch_fails(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        task_source=TASK_SOURCE.replace("task_id: TASK-101", "task_id: TASK-999"),
    )

    with pytest.raises(OperatorError, match="TASK id mismatch"):
        load_task(repo, "TASK-101")


def test_invalid_task_uses_canonical_parser(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, task_source="task_id: TASK-101\n")

    with pytest.raises(OperatorError, match="invalid TASK"):
        load_task(repo, "TASK-101")


def test_non_git_directory_fails(tmp_path: Path) -> None:
    with pytest.raises(OperatorError, match="not a Git repository"):
        resolve_repository(tmp_path)


def test_dirty_repository_fails_before_executor_invocation(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "DIRTY.txt").write_text("dirty\n", encoding="utf-8")

    def runner(command, **kwargs):
        raise AssertionError("executor must not be invoked")

    with pytest.raises(OperatorError, match="repository dirty"):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)


def test_base_sha_comes_from_real_git_head(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    base_sha = git(repo, "rev-parse", "HEAD")

    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )

    assert summary.base_sha == base_sha


def test_runtime_files_under_git_dir_do_not_dirty_worktree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    (paths.runs / "RUN-TEST-001.json").write_text("{}", encoding="utf-8")
    (paths.handoffs / "RUN-TEST-001.json").write_text("{}", encoding="utf-8")
    (paths.results / "RUN-TEST-001.json").write_text("{}", encoding="utf-8")

    assert git(repo, "status", "--porcelain") == ""


def test_sequential_run_ids_increment(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)

    first = run_task(
        "TASK-101", executor="codex", repo=repo, native_runner=runner
    )
    second = run_task(
        "TASK-101", executor="codex", repo=repo, native_runner=runner
    )

    assert first.run_id == "RUN-101-001"
    assert second.run_id == "RUN-101-002"


def test_codex_path_uses_executor_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    real_boundary = operator_module.ExecutorBoundary
    calls = []

    class SpyBoundary:
        def __init__(self, leases):
            self.inner = real_boundary(leases)

        def invoke(self, **kwargs):
            calls.append(kwargs)
            return self.inner.invoke(**kwargs)

    monkeypatch.setattr(operator_module, "ExecutorBoundary", SpyBoundary)
    run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )

    assert len(calls) == 1
    assert calls[0]["lease"] is not None


def test_default_codex_sandbox_is_workspace_write(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)

    run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    command = runner.calls[0][0]
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert "danger-full-access" not in command


def test_danger_full_access_requires_explicit_request(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)

    run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        codex_sandbox="danger-full-access",
        native_runner=runner,
    )

    command = runner.calls[0][0]
    assert command[command.index("--sandbox") + 1] == "danger-full-access"


def test_antigravity_invocation_contract(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeAntigravityRunner(repo)

    run_task(
        "TASK-101",
        executor="antigravity",
        repo=repo,
        native_runner=runner,
    )

    command, kwargs = runner.calls[0]
    instruction = command[command.index("--print") + 1]
    workspace = command[command.index("--add-dir") + 1]
    assert command[0] == "agy"
    assert workspace == str(repo.resolve())
    assert command[command.index("--effort") + 1] == "low"
    assert command[command.index("--mode") + 1] == "accept-edits"
    assert "--disable-slash-commands" in command
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--print-timeout") + 1] == "5m"
    assert kwargs["cwd"] == workspace
    assert ".git" in instruction and "handoff" in instruction
    assert "Create one deterministic operator test output" not in instruction
    assert "--dangerously-skip-permissions" not in command
    assert "--model" not in command


def test_antigravity_instruction_defines_canonical_result_package(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    runner = FakeAntigravityRunner(repo)

    run_task(
        "TASK-101",
        executor="antigravity",
        repo=repo,
        native_runner=runner,
    )

    command = runner.calls[0][0]
    instruction = command[command.index("--print") + 1]
    for field in (
        "head_sha",
        "claims",
        "changed_files",
        "unresolved",
        "evidence_id",
        "run_id",
        "subject_sha",
        "type",
        "source.command",
        "result.exit_code",
        "result.summary",
        "raw.path",
    ):
        assert field in instruction
    assert "evidence.run_id must reference the current RUN" in instruction
    assert "evidence.subject_sha must equal result.head_sha" in instruction
    assert "known TASK acceptance ID" in instruction
    assert "existing evidence_id" in instruction


def test_missing_agy_executable_fails_clearly(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError, match="Antigravity CLI not found"):
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=FakeAntigravityRunner(repo, mode="missing"),
        )


def test_nonzero_agy_exit_fails_clearly(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError, match="CLI returned nonzero"):
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=FakeAntigravityRunner(repo, mode="nonzero"),
        )


def test_headless_soft_denial_preserves_stderr_when_result_missing(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError) as captured:
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=FakeAntigravityRunner(repo, mode="no-result-stderr"),
        )

    assert str(captured.value) == (
        "Antigravity ResultPackage missing: headless tool action denied"
    )
    assert "less useful stdout" not in str(captured.value)


def test_missing_antigravity_result_preserves_stdout_fallback(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError) as captured:
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=FakeAntigravityRunner(repo, mode="no-result"),
        )

    assert str(captured.value) == "Antigravity ResultPackage missing: done"


def test_missing_antigravity_result_without_diagnostic_is_generic(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError) as captured:
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=FakeAntigravityRunner(repo, mode="no-result-empty"),
        )

    assert str(captured.value) == "Antigravity ResultPackage missing"


def test_invalid_antigravity_result_fails_canonical_validation(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError, match="invalid canonical ResultPackage"):
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=FakeAntigravityRunner(repo, mode="invalid"),
        )


def test_result_head_sha_mismatch_fails(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError, match="RESULT.head_sha mismatch"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=FakeCodexRunner(repo, reported_head="deadbeef"),
        )


def test_dirty_post_execution_worktree_fails(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError, match="working tree dirty after execution"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=FakeCodexRunner(repo, dirty_after=True),
        )


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_run_013_false_pass_shape_fails_closed(
    tmp_path: Path,
    executor: str,
) -> None:
    repo = make_repo(tmp_path)
    payload = static_payload(
        satisfies=[],
        unresolved=["Codex could not complete execution."],
    )

    with pytest.raises(OperatorError, match="RESULT has unresolved items"):
        run_task(
            "TASK-101",
            executor=executor,
            repo=repo,
            native_runner=StaticResultRunner(repo, payload),
        )


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_missing_acceptance_coverage_fails_closed(
    tmp_path: Path,
    executor: str,
) -> None:
    repo = make_repo(tmp_path, task_source=MULTI_ACCEPTANCE_TASK_SOURCE)
    payload = static_payload(satisfies=["AC1"])

    with pytest.raises(
        OperatorError,
        match="RESULT does not satisfy acceptance criteria: AC2",
    ):
        run_task(
            "TASK-101",
            executor=executor,
            repo=repo,
            native_runner=StaticResultRunner(repo, payload),
        )


def test_acceptance_coverage_is_union_of_claim_satisfies(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, task_source=MULTI_ACCEPTANCE_TASK_SOURCE)
    payload = static_payload(satisfies=["AC1"])
    payload["result"]["claims"].append(
        {
            "id": "C2",
            "satisfies": ["AC2"],
            "claim": "The second criterion is satisfied.",
            "evidence": ["E2"],
        }
    )
    payload["evidence"].append(
        {
            "evidence_id": "E2",
            "run_id": "replaced-by-runner",
            "subject_sha": "replaced-by-runner",
            "type": "TEST",
            "source": {"command": "git status --porcelain"},
            "result": {"exit_code": 0, "summary": "verified"},
            "raw": {"path": ".ai/evidence/E2.log"},
        }
    )

    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=StaticResultRunner(repo, payload),
    )

    assert summary.render().startswith("AIOS RUN PASS\n")


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_complete_result_retains_pass_without_head_advancement(
    tmp_path: Path,
    executor: str,
) -> None:
    repo = make_repo(tmp_path)
    initial_head = git(repo, "rev-parse", "HEAD")

    summary = run_task(
        "TASK-101",
        executor=executor,
        repo=repo,
        native_runner=StaticResultRunner(repo, static_payload()),
    )

    assert summary.head_sha == initial_head
    assert summary.render().startswith("AIOS RUN PASS\n")


def test_successful_codex_execution_stores_result_package(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )
    stored = json.loads(summary.result_path.read_text(encoding="utf-8"))

    assert stored["result"]["head_sha"] == summary.head_sha
    assert stored["evidence"][0]["raw"]["path"] == ".ai/evidence/E1.log"
    assert summary.result_path.parent == repo / ".git" / "aios" / "results"


def test_successful_antigravity_execution_returns_pass_summary(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)

    summary = run_task(
        "TASK-101",
        executor="antigravity",
        repo=repo,
        native_runner=FakeAntigravityRunner(repo),
    )

    assert summary.render().startswith("AIOS RUN PASS\n")
    assert "executor: antigravity" in summary.render()
    assert summary.result_path.is_file()


def test_pyproject_registers_aios_entry_point() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["scripts"]["aios"] == "aios_renew.operator:main"


def test_operator_adds_no_background_or_orchestration_framework() -> None:
    source = inspect.getsource(operator_module).lower()

    for forbidden in (
        "while ",
        "polling",
        "watcher",
        "daemon",
        "retry",
        "router",
        "database",
        "redis",
        "message broker",
    ):
        assert forbidden not in source


def test_first_operator_run_can_acquire_repository_lock(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )
    assert summary.run_id == "RUN-101-001"


def test_concurrent_second_acquisition_fails_before_executor_invocation(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    paths.lock.write_text("locked", encoding="utf-8")

    def runner(command, **kwargs):
        raise AssertionError("executor must not be invoked when lock is held")

    with pytest.raises(
        OperatorError, match="another AIOS run is active in this repository"
    ):
        run_task(
            "TASK-101", executor="codex", repo=repo, native_runner=runner
        )


def test_lock_released_after_successful_execution(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )
    assert not paths.lock.exists()


def test_lock_released_after_executor_failure(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)

    with pytest.raises(OperatorError, match="Antigravity CLI not found"):
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=FakeAntigravityRunner(repo, mode="missing"),
        )
    assert not paths.lock.exists()


def test_run_id_allocation_happens_while_lock_is_held(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    lock_was_held = False
    run_file_existed = False

    class LockCheckingRunner:
        def __call__(self, command, **kwargs):
            nonlocal lock_was_held, run_file_existed
            lock_was_held = paths.lock.exists()
            run_file_existed = (paths.runs / "RUN-101-001.json").is_file()
            return FakeCodexRunner(repo)(command, **kwargs)

    run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=LockCheckingRunner(),
    )
    assert lock_was_held is True
    assert run_file_existed is True
    assert not paths.lock.exists()


def test_base_sha_is_captured_and_bound_while_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    real_git = operator_module._git
    head_capture_lock_states = []

    def lock_checking_git(repo_path, *args):
        if args == ("rev-parse", "HEAD"):
            head_capture_lock_states.append(paths.lock.exists())
        return real_git(repo_path, *args)

    monkeypatch.setattr(operator_module, "_git", lock_checking_git)
    runner = FakeCodexRunner(repo)
    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )

    canonical = json.loads(
        runner.calls[0][1]["input"].split("CANONICAL_INPUT:\n", 1)[1]
    )
    assert head_capture_lock_states[0] is True
    assert canonical["run"]["base_sha"] == summary.base_sha
    assert not paths.lock.exists()


def test_runtime_lock_remains_under_git_dir_and_does_not_dirty_git_status(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)

    assert paths.lock.is_relative_to(repo / ".git")
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    paths.lock.write_text("active", encoding="utf-8")

    assert git(repo, "status", "--porcelain") == ""


def test_aios_task_remains_readonly_and_does_not_require_lock(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    paths.lock.write_text("locked", encoding="utf-8")

    summary = describe_task("TASK-101", repo=repo)
    assert summary.task.task_id == "TASK-101"
