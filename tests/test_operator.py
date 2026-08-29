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

READONLY_TASK_SOURCE = TASK_SOURCE.replace(
    "  modify:\n    - OUTPUT.txt\n",
    "  modify: []\n",
)

READONLY_MULTI_ACCEPTANCE_TASK_SOURCE = MULTI_ACCEPTANCE_TASK_SOURCE.replace(
    "  modify:\n    - OUTPUT.txt\n",
    "  modify: []\n",
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
    upstream = root / "upstream.git"
    subprocess.run(("git", "init", "--bare", "--quiet", str(upstream)), check=True)
    git(repo, "remote", "add", "origin", str(upstream))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "HEAD")
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
                    "evidence": [],
                }
            ],
            "changed_files": files,
            "unresolved": [],
        },
        "evidence": [],
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
        canonical = json.loads(
            kwargs["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
        )
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
        result_path = Path(handoff["structural_result_path"])
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
            result_path = Path(handoff["structural_result_path"])
            payload["result"]["head_sha"] = git(self.repo, "rev-parse", "HEAD")
            for item in payload["evidence"]:
                item["run_id"] = run_id
                item["subject_sha"] = payload["result"]["head_sha"]
            result_path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            canonical = json.loads(
                kwargs["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
            )
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
            result_path = Path(handoff["structural_result_path"])
        else:
            canonical = json.loads(
                kwargs["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
            )
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
    evidence = []
    if criteria:
        claims.append(
            {
                "id": "C1",
                "satisfies": criteria,
                "claim": "The stated acceptance criteria are satisfied.",
                "evidence": [],
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
        payload["result"]["claims"][0]["evidence"] = ["E1"]
        payload["evidence"] = [
            {
                "evidence_id": "E1",
                "run_id": run_id,
                "subject_sha": sha,
                "type": "TEST",
                "source": {"command": "git status --porcelain"},
                "result": {"exit_code": 0, "summary": "verified"},
                "raw": {"path": ".ai/evidence/E1.log"},
            }
        ]
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
            result_path = Path(handoff["structural_result_path"])
        else:
            execution = json.loads(
                kwargs["input"].decode("utf-8").split("REMEDIATION_INPUT:\n", 1)[1]
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
            "evidence": [],
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
    staged = json.loads(
        (runtime_paths(repo).staging / f"{summary.run_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(runner.calls) == 1
    assert staged["result"]["claims"] == []
    assert staged["result"]["unresolved"] == []
    assert staged["evidence"] == []
    assert stored["result"]["claims"] == []
    assert stored["result"]["changed_files"] == ["OUTPUT.txt"]
    assert stored["evidence"][0]["source"]["command"] == "git diff --check"
    assert "git status --porcelain" not in json.dumps(stored)
    assert runner.calls[0][1]["text"] is False
    assert "encoding" not in runner.calls[0][1]
    assert "errors" not in runner.calls[0][1]
    if executor == "antigravity":
        instruction = runner.calls[0][0][runner.calls[0][0].index("--print") + 1]
        assert "CODE_FIX, commit the permitted remediation delta" in instruction
        assert "EVIDENCE_ONLY, do not create a code commit" in instruction
        assert "Do not push" in instruction
        handoff = json.loads(
            next((repo / ".git" / "aios" / "handoffs").glob("*.json")).read_text(
                encoding="utf-8"
            )
        )
        serialized = json.dumps(handoff)
        assert "affected_verification" not in serialized
        assert "git diff --check" not in serialized
        assert handoff["remediation_execution"]["finding"]["issue"]
        assert handoff["remediation_execution"]["remediation"][
            "modification_scope"
        ] == ["OUTPUT.txt"]
        assert remediation.affected_verification == ("git diff --check",)


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
    assert captured["text"] is False
    assert "encoding" not in captured
    assert "errors" not in captured


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
    assert captured["text"] is False
    assert "encoding" not in captured
    assert "errors" not in captured


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
    git(repo, "push", "--quiet")
    second = run_task(
        "TASK-101", executor="codex", repo=repo, native_runner=runner
    )

    assert first.run_id == "RUN-101-001"
    assert second.run_id == "RUN-101-002"


def publish_upstream(
    repo: Path, files: dict[str, str], message: str = "publish"
) -> str:
    publisher = repo.parent / "publisher"
    subprocess.run(
        (
            "git",
            "clone",
            "--quiet",
            git(repo, "remote", "get-url", "origin"),
            str(publisher),
        ),
        check=True,
    )
    git(publisher, "config", "user.name", "AIOS Publisher")
    git(publisher, "config", "user.email", "publisher@example.invalid")
    for name, content in files.items():
        path = publisher / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(publisher, "add", ".")
    git(publisher, "commit", "--quiet", "-m", message)
    git(publisher, "push", "--quiet")
    return git(publisher, "rev-parse", "HEAD")


def test_primary_fast_forwards_before_task_load_and_binds_synchronized_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    local_sha = git(repo, "rev-parse", "HEAD")
    branch_ref = git(repo, "symbolic-ref", "HEAD")
    published_sha = publish_upstream(
        repo, {".ai/tasks/TASK-101.yaml": TASK_SOURCE}, "publish task"
    )
    runner = FakeCodexRunner(repo)
    git_calls = []
    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", recording_git)

    summary = run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    canonical = json.loads(
        runner.calls[0][1]["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
    )
    assert summary.base_sha == published_sha
    assert canonical["run"]["base_sha"] == published_sha
    assert canonical["task"]["task_id"] == "TASK-101"
    assert ("read-tree", "-u", "-m", local_sha, published_sha) in git_calls
    assert ("update-ref", branch_ref, published_sha, local_sha) in git_calls
    prohibited = {"merge", "rebase", "reset", "checkout", "stash", "clean"}
    assert not any(args and args[0] in prohibited for args in git_calls)


@pytest.mark.parametrize(
    "state", ["detached", "missing-upstream", "ahead", "diverged"]
)
def test_unsafe_primary_git_states_fail_before_executor(
    tmp_path: Path, state: str
) -> None:
    repo = make_repo(tmp_path)
    if state == "detached":
        git(repo, "checkout", "--quiet", "--detach")
    elif state == "missing-upstream":
        git(repo, "branch", "--unset-upstream")
    elif state == "ahead":
        (repo / "LOCAL.txt").write_text("local\n", encoding="utf-8")
        git(repo, "add", "LOCAL.txt")
        git(repo, "commit", "--quiet", "-m", "local")
    else:
        publish_upstream(repo, {"REMOTE.txt": "remote\n"}, "remote")
        (repo / "LOCAL.txt").write_text("local\n", encoding="utf-8")
        git(repo, "add", "LOCAL.txt")
        git(repo, "commit", "--quiet", "-m", "local")

    def runner(command, **kwargs):
        raise AssertionError("executor must not be invoked")

    with pytest.raises(OperatorError):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_fetch_failure_fails_before_run_persistence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    with pytest.raises(OperatorError, match="upstream fetch failed"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=lambda *a, **k: None,
        )

    assert not list(runtime_paths(repo).runs.glob("*.json"))


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
    assert kwargs["text"] is False
    assert "encoding" not in kwargs
    assert "errors" not in kwargs
    assert ".git" in instruction and "handoff" in instruction
    assert "Create one deterministic operator test output" not in instruction
    assert "--dangerously-skip-permissions" not in command
    assert "--model" not in command


def test_antigravity_instruction_defines_structural_staging_package(
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
    assert "known TASK acceptance ID" in instruction
    assert "structural_result_path" in instruction
    assert "staging, not the canonical results store" in instruction
    assert "Runtime owns canonical verification" in instruction
    assert "do not execute verification commands" in instruction
    assert "Commit the final implementation state when required" in instruction
    assert "do not push" in instruction
    handoff = json.loads(
        next((repo / ".git" / "aios" / "handoffs").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert "verification" not in handoff["task"]
    assert "git status --porcelain" not in json.dumps(handoff)
    assert handoff["task"]["acceptance"][0]["id"] == "AC1"
    assert handoff["task"]["scope"]["modify"] == ["OUTPUT.txt"]
    assert handoff["task"]["constraints"]["hard"] == ["Commit the output."]


def test_operator_contains_no_antigravity_structural_normalizer() -> None:
    source = inspect.getsource(operator_module)

    assert "_StructuralAntigravityAdapter" not in source
    assert "_normalize_structural_satisfies" not in source


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_empty_executor_verification_evidence_succeeds_via_runtime(
    tmp_path: Path,
    executor: str,
) -> None:
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)
    summary = run_task(
        "TASK-101",
        executor=executor,
        repo=repo,
        native_runner=StaticResultRunner(repo, static_payload()),
    )
    stored = json.loads(summary.result_path.read_text(encoding="utf-8"))

    assert stored["evidence"][0]["source"]["command"] == (
        "git status --porcelain"
    )
    assert stored["result"]["claims"][0]["evidence"] == [
        f"{summary.run_id}-V001"
    ]


def test_preverification_gate_failure_executes_no_commands(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    payload = static_payload(unresolved=["not complete"])
    calls = []

    with pytest.raises(OperatorError, match="unresolved"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=StaticResultRunner(repo, payload),
            verification_runner=lambda *args, **kwargs: calls.append(args),
        )

    assert calls == []
    assert list((repo / ".git" / "aios" / "results").glob("*.json")) == []


def test_first_runtime_verification_failure_stops_and_persists_no_result(
    tmp_path: Path,
) -> None:
    task_source = READONLY_TASK_SOURCE.replace(
        "    - git status --porcelain",
        "    - first-command\n    - never-command",
    )
    repo = make_repo(tmp_path, task_source=task_source)
    calls = []

    def failing_verification(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, returncode=9, stdout=b"", stderr=b"failed\n"
        )

    with pytest.raises(OperatorError, match="exit code 9"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=StaticResultRunner(repo, static_payload()),
            verification_runner=failing_verification,
        )

    assert len(calls) == 1
    state = runtime_paths(repo)
    assert (state.verification / "RUN-101-001" / "RUN-101-001-V001.raw").is_file()
    assert list(state.results.glob("*.json")) == []


def test_runtime_verification_decode_failure_persists_no_result(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)

    def invalid_utf8(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=b"\xff", stderr=b""
        )

    with pytest.raises(OperatorError, match="strict UTF-8"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=StaticResultRunner(repo, static_payload()),
            verification_runner=invalid_utf8,
        )

    assert list(runtime_paths(repo).results.glob("*.json")) == []


@pytest.mark.parametrize("flow", ["primary", "remediation"])
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("dirty", "verification dirtied working tree"),
        ("head", "verification changed Git HEAD"),
    ],
)
def test_successful_verification_repository_mutation_fails_closed(
    tmp_path: Path,
    flow: str,
    mutation: str,
    message: str,
) -> None:
    repo = make_repo(
        tmp_path,
        task_source=READONLY_TASK_SOURCE if flow == "primary" else TASK_SOURCE,
    )
    state = runtime_paths(repo)
    heads_before_mutation = []

    def mutating_verification(command, **kwargs):
        heads_before_mutation.append(git(repo, "rev-parse", "HEAD"))
        if mutation == "dirty":
            (repo / "VERIFICATION_DIRTY.txt").write_text(
                "verification mutation\n", encoding="utf-8"
            )
        else:
            (repo / "VERIFICATION_COMMIT.txt").write_text(
                "verification mutation\n", encoding="utf-8"
            )
            git(repo, "add", "VERIFICATION_COMMIT.txt")
            git(repo, "commit", "--quiet", "-m", "verification mutation")
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=b"passed\n", stderr=b""
        )

    with pytest.raises(OperatorError, match=message):
        if flow == "primary":
            run_task(
                "TASK-101",
                executor="codex",
                repo=repo,
                native_runner=StaticResultRunner(repo, static_payload()),
                verification_runner=mutating_verification,
            )
        else:
            review, remediation = remediation_contract(repo)
            run_remediation(
                "TASK-101",
                review=review,
                remediation=remediation,
                executor="codex",
                repo=repo,
                native_runner=RemediationRunner(repo),
                verification_runner=mutating_verification,
            )

    assert not (state.results / "RUN-101-001.json").exists()
    if mutation == "dirty":
        assert (repo / "VERIFICATION_DIRTY.txt").is_file()
        assert git(repo, "status", "--porcelain")
    else:
        assert git(repo, "rev-parse", "HEAD") != heads_before_mutation[0]


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

    with pytest.raises(OperatorError, match="invalid structural ResultPackage"):
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=FakeAntigravityRunner(repo, mode="invalid"),
        )
    assert list(runtime_paths(repo).results.glob("*.json")) == []


def test_result_head_sha_mismatch_fails(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError, match="RESULT.head_sha mismatch"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=FakeCodexRunner(repo, reported_head="deadbeef"),
        )
    assert list(runtime_paths(repo).results.glob("*.json")) == []


def test_dirty_post_execution_worktree_fails(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(OperatorError, match="working tree dirty after execution"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=FakeCodexRunner(repo, dirty_after=True),
        )
    assert list(runtime_paths(repo).results.glob("*.json")) == []


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
        unresolved=["Codex could not complete execution."],
    )
    if executor == "antigravity":
        payload["result"]["claims"][0]["satisfies"] = "AC1"

    with pytest.raises(OperatorError, match="RESULT has unresolved items"):
        run_task(
            "TASK-101",
            executor=executor,
            repo=repo,
            native_runner=StaticResultRunner(repo, payload),
        )

    state = runtime_paths(repo)
    staged = json.loads(
        (state.staging / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert staged["result"]["unresolved"] == [
        "Codex could not complete execution."
    ]
    assert staged["result"]["claims"][0]["satisfies"] == ["AC1"]
    assert staged["result"]["claims"][0]["evidence"] == []
    assert staged["evidence"] == []
    assert not (state.results / "RUN-101-001.json").exists()


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
    repo = make_repo(tmp_path, task_source=READONLY_MULTI_ACCEPTANCE_TASK_SOURCE)
    payload = static_payload(satisfies=["AC1"])
    payload["result"]["claims"].append(
        {
            "id": "C2",
            "satisfies": ["AC2"],
            "claim": "The second criterion is satisfied.",
            "evidence": [],
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
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)
    initial_head = git(repo, "rev-parse", "HEAD")

    summary = run_task(
        "TASK-101",
        executor=executor,
        repo=repo,
        native_runner=StaticResultRunner(repo, static_payload()),
    )

    assert summary.head_sha == initial_head
    assert summary.render().startswith("AIOS RUN PASS\n")


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_mutation_bearing_primary_without_head_advancement_fails_closed(
    tmp_path: Path,
    executor: str,
) -> None:
    repo = make_repo(tmp_path)
    verification_calls = []

    with pytest.raises(OperatorError, match="final Git HEAD did not advance"):
        run_task(
            "TASK-101",
            executor=executor,
            repo=repo,
            native_runner=StaticResultRunner(repo, static_payload()),
            verification_runner=lambda *args, **kwargs: verification_calls.append(args),
        )

    assert verification_calls == []
    assert not list(runtime_paths(repo).results.glob("*.json"))


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
    assert stored["evidence"][0]["source"]["command"] == "git status --porcelain"
    assert Path(stored["evidence"][0]["raw"]["path"]).is_relative_to(
        repo / ".git" / "aios" / "verification"
    )
    assert summary.result_path.parent == repo / ".git" / "aios" / "results"


def test_successful_antigravity_execution_stores_canonical_result_package(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)
    payload = static_payload()
    payload["result"]["claims"][0]["satisfies"] = "AC1"
    observed_preverification_state = []

    def verification_runner(command, **kwargs):
        state = runtime_paths(repo)
        observed_preverification_state.append(
            (
                list(state.staging.glob("*.json")),
                list(state.results.glob("*.json")),
            )
        )
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=b"", stderr=b""
        )

    summary = run_task(
        "TASK-101",
        executor="antigravity",
        repo=repo,
        native_runner=StaticResultRunner(repo, payload),
        verification_runner=verification_runner,
    )
    stored = json.loads(summary.result_path.read_text(encoding="utf-8"))

    assert stored["result"]["head_sha"] == summary.head_sha
    assert stored["result"]["claims"][0]["satisfies"] == ["AC1"]
    assert stored["evidence"][0]["source"]["command"] == "git status --porcelain"
    assert len(observed_preverification_state[0][0]) == 1
    assert observed_preverification_state[0][1] == []


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
    assert list((repo / ".git" / "aios" / "staging").glob("*.json"))
    assert list((repo / ".git" / "aios" / "results").glob("*.json")) == []


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
        runner.calls[0][1]["input"]
        .decode("utf-8")
        .split("CANONICAL_INPUT:\n", 1)[1]
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
