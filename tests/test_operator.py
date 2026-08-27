import inspect
import json
import multiprocessing
import subprocess
import tomllib
from pathlib import Path

import pytest

import aios_renew.operator as operator_module
from aios_renew.operator import (
    OperatorError,
    RepositoryLock,
    describe_task,
    load_task,
    resolve_repository,
    run_remediation,
    run_task,
    runtime_paths,
)


def hold_repository_lock(lock_path: str, ready, release) -> None:
    with RepositoryLock(Path(lock_path)):
        ready.set()
        release.wait()


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


class CommitResultRunner:
    def __init__(
        self,
        repo: Path,
        *,
        writes: dict[str, str] | None = None,
        renames: dict[str, str] | None = None,
        changed_files: list[str],
    ) -> None:
        self.repo = repo
        self.writes = {} if writes is None else writes
        self.renames = {} if renames is None else renames
        self.changed_files = changed_files

    def __call__(self, command, **kwargs):
        if command[0] == "agy":
            handoff_path = next(
                (self.repo / ".git" / "aios" / "handoffs").glob("*.json")
            )
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            run_id = handoff["run"]["run_id"]
            result_path = Path(handoff["result_package_path"])
        else:
            canonical = json.loads(kwargs["input"].split("CANONICAL_INPUT:\n", 1)[1])
            run_id = canonical["run"]["run_id"]
            result_path = None

        for source, destination in self.renames.items():
            (self.repo / source).rename(self.repo / destination)
        for path, content in self.writes.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "executor changes")
        head_sha = git(self.repo, "rev-parse", "HEAD")
        payload = result_payload(
            run_id,
            head_sha,
            changed_files=self.changed_files,
        )
        if result_path is not None:
            result_path.write_text(json.dumps(payload), encoding="utf-8")
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
    evidence = [
        {
            "evidence_id": "E1",
            "run_id": "replaced-by-runner",
            "subject_sha": "replaced-by-runner",
            "type": "TEST",
            "source": {"command": "git status --porcelain"},
            "result": {"exit_code": 0, "summary": "verified"},
            "raw": {"path": ".ai/evidence/E1.log"},
        }
    ]
    if criteria:
        claims.append(
            {
                "id": "C1",
                "satisfies": criteria,
                "claim": "The stated acceptance criteria are satisfied.",
                "evidence": ["E1"],
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


def remediation_contract(
    repo: Path,
    *,
    reviewed_sha: str | None = None,
    persist_prior_result: bool = True,
):
    sha = reviewed_sha or git(repo, "rev-parse", "HEAD")
    if persist_prior_result:
        state = runtime_paths(repo)
        run_id = "RUN-101-000"
        (state.runs / f"{run_id}.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "task": {"id": "TASK-101", "revision": 1},
                    "executor": "codex",
                    "base_sha": sha,
                    "workspace": str(repo),
                    "head_sha": None,
                    "status": "ACTIVE",
                }
            ),
            encoding="utf-8",
        )
        payload = static_payload()
        payload["result"]["head_sha"] = sha
        payload["evidence"][0]["run_id"] = run_id
        payload["evidence"][0]["subject_sha"] = sha
        (state.results / f"{run_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    review = operator_module.parse_review(
        f"""
review_id: REVIEW-101-001
reviewed_sha: {sha}
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
"""
    )
    remediation = operator_module.parse_remediation(
        f"""
finding_id: R1
action: CODE_FIX
reviewed_sha: {sha}
modification_scope: [OUTPUT.txt]
affected_verification: [git diff --check]
constraints:
  hard: [Commit the output.]
"""
    )
    return review, remediation


class RemediationRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        result_path = None
        if command[0] == "agy":
            handoff_path = next(
                (self.repo / ".git" / "aios" / "handoffs").glob("*.json")
            )
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            execution = handoff["remediation_execution"]
            result_path = Path(handoff["result_package_path"])
        else:
            execution = json.loads(
                kwargs["input"].split("REMEDIATION_INPUT:\n", 1)[1]
            )
        (self.repo / "OUTPUT.txt").write_text(
            f"remediated by {execution['run']['run_id']}\n", encoding="utf-8"
        )
        git(self.repo, "add", "OUTPUT.txt")
        git(self.repo, "commit", "--quiet", "-m", "narrow remediation")
        head_sha = git(self.repo, "rev-parse", "HEAD")
        payload = {
            "result": {
                "head_sha": head_sha,
                "claims": [],
                "changed_files": ["OUTPUT.txt"],
                "unresolved": [],
            },
            "evidence": [
                {
                    "evidence_id": "ER1",
                    "run_id": execution["run"]["run_id"],
                    "subject_sha": head_sha,
                    "type": "TEST",
                    "source": {"command": "git diff --check"},
                    "result": {"exit_code": 0, "summary": "clean diff"},
                    "raw": {"path": ".ai/evidence/ER1.log"},
                }
            ],
        }
        if result_path is not None:
            result_path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=json.dumps(payload), stderr=""
        )


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_narrow_remediation_uses_shared_completion_policy(
    tmp_path: Path, executor: str
) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    runner = RemediationRunner(repo)

    summary = run_remediation(
        "TASK-101",
        review=review,
        remediation=remediation,
        executor=executor,
        repo=repo,
        native_runner=runner,
    )
    stored = json.loads(summary.result_path.read_text(encoding="utf-8"))

    assert len(runner.calls) == 1
    assert stored["result"]["claims"] == []
    assert stored["result"]["changed_files"] == ["OUTPUT.txt"]
    assert stored["evidence"][0]["source"]["command"] == "git diff --check"
    assert "git status --porcelain" not in json.dumps(stored)
    assert runner.calls[0][1]["encoding"] == "utf-8"
    assert runner.calls[0][1]["errors"] == "strict"
    if executor == "antigravity":
        instruction = runner.calls[0][0][runner.calls[0][0].index("--print") + 1]
        assert "CODE_FIX, commit the permitted remediation delta" in instruction
        assert "EVIDENCE_ONLY, do not create a code commit" in instruction


def test_persisted_remediation_result_is_authoritative_lineage(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    primary_review, first_remediation = remediation_contract(repo)
    first_runner = RemediationRunner(repo)
    first = run_remediation(
        "TASK-101",
        review=primary_review,
        remediation=first_remediation,
        executor="codex",
        repo=repo,
        native_runner=first_runner,
    )
    delta_review = operator_module.parse_review(
        f"""
review_id: REVIEW-101-002
reviewed_sha: {first.head_sha}
mode: DELTA
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: R2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The first remediation needs one further narrow correction.
    expected: Commit only the corrected output.
prior_finding_id: R1
"""
    )
    second_remediation = operator_module.parse_remediation(
        f"""
finding_id: R2
action: CODE_FIX
reviewed_sha: {first.head_sha}
modification_scope: [OUTPUT.txt]
affected_verification: [git diff --check]
constraints:
  hard: [Commit the output.]
"""
    )
    second_runner = RemediationRunner(repo)

    second = run_remediation(
        "TASK-101",
        review=delta_review,
        remediation=second_remediation,
        prior_review=primary_review,
        executor="codex",
        repo=repo,
        native_runner=second_runner,
    )

    assert first.run_id == "RUN-101-001"
    assert second.run_id == "RUN-101-002"
    assert second.reviewed_sha == first.head_sha
    assert second.head_sha != first.head_sha
    assert len(first_runner.calls) == len(second_runner.calls) == 1


def test_remediation_sha_mismatch_fails_before_executor(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo, reviewed_sha="deadbeef")
    calls = []

    with pytest.raises(OperatorError, match="current HEAD"):
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            executor="codex",
            repo=repo,
            native_runner=lambda *args, **kwargs: calls.append(args),
        )

    assert calls == []


def test_remediation_missing_prior_result_fails_before_executor(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo, persist_prior_result=False)
    calls = []

    with pytest.raises(OperatorError, match="authoritative prior RESULT not found"):
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            executor="codex",
            repo=repo,
            native_runner=lambda *args, **kwargs: calls.append(args),
        )

    assert calls == []


def test_remediation_mismatched_prior_result_lineage_fails_before_executor(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    state = runtime_paths(repo)
    run_path = state.runs / "RUN-101-000.json"
    run_data = json.loads(run_path.read_text(encoding="utf-8"))
    run_data["task"]["id"] = "TASK-999"
    run_path.write_text(json.dumps(run_data), encoding="utf-8")
    calls = []

    with pytest.raises(OperatorError, match="authoritative prior RESULT lineage mismatch"):
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            executor="codex",
            repo=repo,
            native_runner=lambda *args, **kwargs: calls.append(args),
        )

    assert calls == []


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


def test_repository_discovery_uses_strict_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def runner(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=str(tmp_path), stderr=""
        )

    monkeypatch.setattr(operator_module.subprocess, "run", runner)

    assert resolve_repository(tmp_path) == tmp_path.resolve()
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "strict"


def test_git_output_preserves_utf8_nul_delimited_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}
    raw_output = "普通.txt\0emoji-🚀.txt\0"

    def runner(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=raw_output, stderr=""
        )

    monkeypatch.setattr(operator_module.subprocess, "run", runner)

    assert operator_module._git(
        tmp_path, "diff", "--name-status", "-z", strip_stdout=False
    ) == raw_output
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "strict"


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
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "strict"
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
    assert "task.verification.required exactly as written" in instruction
    assert "evidence.source.command must exactly equal" in instruction
    assert "evidence.result.exit_code must be zero" in instruction


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_missing_verification_evidence_fails_shared_boundary(
    tmp_path: Path,
    executor: str,
) -> None:
    repo = make_repo(tmp_path)
    payload = static_payload()
    payload["evidence"][0]["source"]["command"] = "git diff --check"

    with pytest.raises(OperatorError, match="missing verification evidence"):
        run_task(
            "TASK-101",
            executor=executor,
            repo=repo,
            native_runner=StaticResultRunner(repo, payload),
        )


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
def test_changed_files_exact_committed_delta_passes_shared_gate(
    tmp_path: Path,
    executor: str,
) -> None:
    task_source = TASK_SOURCE.replace(
        "    - OUTPUT.txt",
        "    - FIRST.txt\n    - SECOND.txt",
    )
    repo = make_repo(tmp_path, task_source=task_source)
    runner = CommitResultRunner(
        repo,
        writes={"FIRST.txt": "first\n", "SECOND.txt": "second\n"},
        changed_files=["SECOND.txt", "FIRST.txt"],
    )

    summary = run_task(
        "TASK-101", executor=executor, repo=repo, native_runner=runner
    )

    assert summary.render().startswith("AIOS RUN PASS\n")


def test_committed_file_omitted_from_result_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = CommitResultRunner(
        repo,
        writes={"OUTPUT.txt": "output\n"},
        changed_files=[],
    )

    with pytest.raises(OperatorError, match="RESULT.changed_files mismatch"):
        run_task(
            "TASK-101", executor="codex", repo=repo, native_runner=runner
        )


def test_declared_file_absent_from_committed_delta_fails_closed(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    runner = CommitResultRunner(
        repo,
        writes={"OUTPUT.txt": "output\n"},
        changed_files=["OUTPUT.txt", "ABSENT.txt"],
    )

    with pytest.raises(OperatorError, match="RESULT.changed_files mismatch"):
        run_task(
            "TASK-101", executor="codex", repo=repo, native_runner=runner
        )


def test_truthfully_declared_out_of_scope_file_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = CommitResultRunner(
        repo,
        writes={"OUTSIDE.txt": "outside\n"},
        changed_files=["OUTSIDE.txt", "OUTSIDE.txt"],
    )

    with pytest.raises(OperatorError, match="outside TASK.scope.modify"):
        run_task(
            "TASK-101", executor="codex", repo=repo, native_runner=runner
        )


def test_whitespace_path_cannot_be_normalized_into_declared_scope(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    runner = CommitResultRunner(
        repo,
        writes={" OUTPUT.txt": "outside scope\n"},
        changed_files=["OUTPUT.txt"],
    )

    with pytest.raises(OperatorError, match="RESULT.changed_files mismatch"):
        run_task(
            "TASK-101", executor="codex", repo=repo, native_runner=runner
        )


def test_rename_checks_old_and_new_paths_with_rename_detection_disabled(
    tmp_path: Path,
) -> None:
    task_source = TASK_SOURCE.replace(
        "    - OUTPUT.txt",
        "    - README.md\n    - RENAMED.md",
    )
    repo = make_repo(tmp_path, task_source=task_source)
    runner = CommitResultRunner(
        repo,
        renames={"README.md": "RENAMED.md"},
        changed_files=["README.md", "RENAMED.md"],
    )

    summary = run_task(
        "TASK-101", executor="codex", repo=repo, native_runner=runner
    )

    assert summary.render().startswith("AIOS RUN PASS\n")


def test_rename_old_path_cannot_bypass_scope_enforcement(tmp_path: Path) -> None:
    task_source = TASK_SOURCE.replace("    - OUTPUT.txt", "    - RENAMED.md")
    repo = make_repo(tmp_path, task_source=task_source)
    runner = CommitResultRunner(
        repo,
        renames={"README.md": "RENAMED.md"},
        changed_files=["README.md", "RENAMED.md"],
    )

    with pytest.raises(OperatorError, match="README.md"):
        run_task(
            "TASK-101", executor="codex", repo=repo, native_runner=runner
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


def test_successful_antigravity_execution_stores_canonical_result_package(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    payload = static_payload()
    payload["result"]["claims"][0]["satisfies"] = "AC1"

    summary = run_task(
        "TASK-101",
        executor="antigravity",
        repo=repo,
        native_runner=StaticResultRunner(repo, payload),
    )
    stored = json.loads(summary.result_path.read_text(encoding="utf-8"))

    assert stored["result"]["head_sha"] == summary.head_sha
    assert stored["result"]["claims"][0]["satisfies"] == ["AC1"]
    assert stored["evidence"][0]["raw"]["path"] == ".ai/evidence/E1.log"


def test_antigravity_result_is_not_canonically_rewritten_before_completion_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    payload = static_payload(satisfies=[])
    real_write_json = operator_module._write_json
    canonical_result_writes = []

    def tracking_write_json(path, data):
        if path.parent.name == "results":
            canonical_result_writes.append(path)
        real_write_json(path, data)

    monkeypatch.setattr(operator_module, "_write_json", tracking_write_json)

    with pytest.raises(OperatorError, match="does not satisfy acceptance criteria"):
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=StaticResultRunner(repo, payload),
        )

    assert canonical_result_writes == []


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


def test_preexisting_lock_file_without_owner_does_not_block_acquisition(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    paths.lock.write_text("locked", encoding="utf-8")

    with RepositoryLock(paths.lock):
        pass


def test_concurrent_second_acquisition_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    owner = context.Process(
        target=hold_repository_lock,
        args=(str(paths.lock), ready, release),
    )
    owner.start()
    assert ready.wait(timeout=10)

    try:
        with pytest.raises(
            OperatorError, match="another AIOS run is active in this repository"
        ):
            RepositoryLock(paths.lock).acquire()
    finally:
        release.set()
        owner.join(timeout=10)
        if owner.is_alive():
            owner.terminate()
            owner.join(timeout=10)

    assert owner.exitcode == 0


def test_lock_is_acquirable_after_owner_process_terminates_abnormally(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    owner = context.Process(
        target=hold_repository_lock,
        args=(str(paths.lock), ready, release),
    )
    owner.start()
    assert ready.wait(timeout=10)

    owner.terminate()
    owner.join(timeout=10)
    assert not owner.is_alive()

    with RepositoryLock(paths.lock):
        pass


def test_lock_released_after_successful_execution(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )
    with RepositoryLock(paths.lock):
        pass


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
    with RepositoryLock(paths.lock):
        pass


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
    with RepositoryLock(paths.lock):
        pass


def test_base_sha_is_captured_and_bound_while_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    real_git = operator_module._git
    head_capture_lock_states = []

    def lock_checking_git(repo_path, *args, **kwargs):
        if args == ("rev-parse", "HEAD"):
            head_capture_lock_states.append(paths.lock.exists())
        return real_git(repo_path, *args, **kwargs)

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
    with RepositoryLock(paths.lock):
        pass


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
