import inspect
import json
import multiprocessing
import subprocess
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

import aios_renew.operator as operator_module
import aios_renew.runtime as runtime_module
from aios_renew.operator import (
    OperatorError,
    RepositoryLock,
    accept_candidate,
    describe_task,
    load_task,
    resolve_repository,
    retry_transport,
    run_repair,
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
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "--quiet", "-b", "main")
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
    subprocess.run(("git", "init", "--bare", "--quiet", "-b", "main", str(upstream)), check=True)
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


def antigravity_envelope(
    payload: object | None = None,
    *,
    status: object = "SUCCESS",
    response: object = "",
    error: object | None = None,
) -> str:
    envelope = {
        "conversation_id": "conversation-test",
        "status": status,
        "response": response,
        "duration_seconds": 1.0,
        "num_turns": 1,
        "usage": {"total_tokens": 1},
    }
    if error is not None:
        envelope["error"] = error
    if payload is not None:
        envelope["structured_output"] = payload
        envelope["json_schema"] = {"type": "object"}
    return json.dumps(envelope)


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


class InterruptingRunner:
    def __init__(
        self,
        repo: Path,
        *,
        dirty: bool = False,
        stdout: bytes | None = None,
        stderr: bytes | None = None,
    ) -> None:
        self.repo = repo
        self.dirty = dirty
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.dirty:
            (self.repo / "OUTPUT.txt").write_text(
                "partial uncommitted work\n", encoding="utf-8"
            )
        interruption = KeyboardInterrupt()
        if self.stdout is not None:
            interruption.stdout = self.stdout
        if self.stderr is not None:
            interruption.stderr = self.stderr
        raise interruption


class RepairRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.executions = []

    def __call__(self, command, **kwargs):
        execution = json.loads(
            kwargs["input"].decode("utf-8").split("REPAIR_INPUT:\n", 1)[1]
        )
        self.executions.append(execution)
        (self.repo / "OUTPUT.txt").write_text(
            f"repaired by {execution['repair']['repair_id']}\n", encoding="utf-8"
        )
        git(self.repo, "add", "OUTPUT.txt")
        git(self.repo, "commit", "--quiet", "-m", execution["repair"]["repair_id"])
        head_sha = git(self.repo, "rev-parse", "HEAD")
        payload = result_payload(execution["run"]["run_id"], head_sha)
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=json.dumps(payload), stderr=""
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
                stdout=antigravity_envelope(response="done"),
                stderr="",
            )
        if self.mode == "no-result-stderr":
            return subprocess.CompletedProcess(
                command,
                returncode=0,
                stdout=antigravity_envelope(),
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
        if self.mode == "malformed-json":
            stdout = "not JSON"
        elif self.mode == "unsuccessful":
            stdout = antigravity_envelope(status="ERROR", error="native failure")
        elif self.mode == "malformed-metadata":
            stdout = antigravity_envelope(status=7)
        elif self.mode == "malformed-payload":
            stdout = antigravity_envelope([])
        elif self.mode == "invalid":
            stdout = antigravity_envelope({})
        else:
            (self.repo / "OUTPUT.txt").write_text(
                "antigravity output\n",
                encoding="utf-8",
            )
            git(self.repo, "add", "OUTPUT.txt")
            git(self.repo, "commit", "--quiet", "-m", "antigravity executor")
            head_sha = git(self.repo, "rev-parse", "HEAD")
            payload = result_payload(handoff["run"]["run_id"], head_sha)
            stdout = antigravity_envelope(payload)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=stdout,
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
            payload["result"]["head_sha"] = git(self.repo, "rev-parse", "HEAD")
            for item in payload["evidence"]:
                item["run_id"] = run_id
                item["subject_sha"] = payload["result"]["head_sha"]
        else:
            canonical = json.loads(
                kwargs["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
            )
            run_id = canonical["run"]["run_id"]
            payload["result"]["head_sha"] = git(self.repo, "rev-parse", "HEAD")
            for item in payload["evidence"]:
                item["run_id"] = run_id
                item["subject_sha"] = payload["result"]["head_sha"]
        stdout = (
            antigravity_envelope(payload)
            if command[0] == "agy"
            else json.dumps(payload)
        )
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=stdout,
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
        else:
            canonical = json.loads(
                kwargs["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
            )
            run_id = canonical["run"]["run_id"]

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
        stdout = (
            antigravity_envelope(payload)
            if command[0] == "agy"
            else json.dumps(payload)
        )
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=stdout,
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


def admission_failure_records(repo: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(runtime_paths(repo).admission_failures.glob("*.json"))
    ]


def remote_admission_failure_records(upstream: Path) -> list[dict]:
    refs = git(
        upstream,
        "for-each-ref",
        "--format=%(objectname)",
        "refs/heads/aios/admission-failure/",
    ).splitlines()
    return [
        json.loads(
            git(
                upstream,
                "show",
                f"{commit}:.ai/transport/admission-failure.json",
            )
        )
        for commit in refs
    ]


def repair_contract(
    repo: Path, *, action: str = "CODE_FIX"
) -> tuple[str, dict]:
    state = runtime_paths(repo)
    failed_run_id = "RUN-101-000"
    failed_head = git(repo, "rev-parse", "HEAD")
    run_data = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": failed_head,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    (state.runs / f"{failed_run_id}.json").write_text(
        json.dumps(run_data), encoding="utf-8"
    )
    (state.failures / f"{failed_run_id}.json").write_text(
        json.dumps(
            {
                "kind": "FAILURE",
                "run_id": failed_run_id,
                "task": {"id": "TASK-101", "revision": 1},
                "executor": "codex",
                "base_sha": failed_head,
                "failed_head_sha": failed_head,
                "candidate": {"repairable": True, "changed_files": []},
            }
        ),
        encoding="utf-8",
    )
    repair = {
        "repair_id": f"REPAIR-101-{action}",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": action,
        "modification_scope": ["OUTPUT.txt"] if action == "CODE_FIX" else [],
        "instructions": ["Apply only the authorized correction."],
        "constraints": ["Commit the output."],
    }
    return failed_run_id, repair


def write_foreign_run(repo: Path) -> Path:
    state = runtime_paths(repo)
    foreign_run = state.runs / "RUN-999-001.json"
    foreign_run.write_text(
        json.dumps(
            {
                "run_id": "RUN-999-001",
                "task": {"id": "TASK-999", "revision": 1},
                "executor": "codex",
                "base_sha": git(repo, "rev-parse", "HEAD"),
                "workspace": str(repo),
                "head_sha": None,
                "status": "ACTIVE",
            }
        ),
        encoding="utf-8",
    )
    return foreign_run


def inject_foreign_run_on_lock_release(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_lock = operator_module.RepositoryLock

    class InterleavingRepositoryLock(real_lock):
        def __exit__(self, *args):
            result = super().__exit__(*args)
            write_foreign_run(repo)
            return result

    monkeypatch.setattr(operator_module, "RepositoryLock", InterleavingRepositoryLock)


def publish_direct_candidate_lineage(repo: Path, root: Path) -> None:
    """Publish the canonical remote artifacts and REVIEW/REMEDIATION branch."""

    from aios_renew.review_transport import transport_post_pass

    state = runtime_paths(repo)
    reviewed_sha = git(repo, "rev-parse", "HEAD")
    transport_post_pass(
        repo,
        run_id="RUN-101-000",
        head_sha=reviewed_sha,
        run_path=state.runs / "RUN-101-000.json",
        result_path=state.results / "RUN-101-000.json",
    )
    author = root / "review-author"
    subprocess.run(
        ("git", "clone", "--quiet", str(root / "upstream.git"), str(author)),
        check=True,
    )
    git(author, "config", "user.name", "AIOS Reviewer Test")
    git(author, "config", "user.email", "reviewer@example.invalid")
    review_dir = author / ".ai" / "reviews"
    remediation_dir = author / ".ai" / "remediations"
    review_dir.mkdir(parents=True)
    remediation_dir.mkdir(parents=True)
    (review_dir / "REVIEW-101-001.yaml").write_text(
        f"""review_id: REVIEW-101-001
reviewed_sha: {reviewed_sha}
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
""",
        encoding="utf-8",
    )
    git(author, "add", ".ai/reviews")
    git(author, "commit", "--quiet", "-m", "canonical review")
    (remediation_dir / "REMEDIATION-101-001-R1.yaml").write_text(
        f"""finding_id: R1
action: CODE_FIX
reviewed_sha: {reviewed_sha}
modification_scope: [OUTPUT.txt]
affected_verification: [git diff --check]
constraints:
  hard: [Commit the output.]
""",
        encoding="utf-8",
    )
    git(author, "add", ".ai/remediations")
    git(author, "commit", "--quiet", "-m", "canonical remediation")
    git(
        author,
        "push",
        "--quiet",
        "origin",
        "HEAD:refs/heads/aios/remediation/RUN-101-000-R1",
    )


class RemediationRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == "agy":
            handoff_path = next(
                (self.repo / ".git" / "aios" / "handoffs").glob("*.json")
            )
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            execution = handoff["remediation_execution"]
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
        stdout = (
            antigravity_envelope(payload)
            if command[0] == "agy"
            else json.dumps(payload)
        )
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=stdout, stderr=""
        )


class StaticRemediationRunner:
    def __init__(
        self,
        repo: Path,
        *,
        empty_commit: bool = False,
        unresolved: list[str] | None = None,
    ) -> None:
        self.repo = repo
        self.empty_commit = empty_commit
        self.unresolved = [] if unresolved is None else unresolved
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == "agy":
            handoff_path = next(
                (self.repo / ".git" / "aios" / "handoffs").glob("*.json")
            )
            execution = json.loads(
                handoff_path.read_text(encoding="utf-8")
            )["remediation_execution"]
        else:
            execution = json.loads(
                kwargs["input"].decode("utf-8").split("REMEDIATION_INPUT:\n", 1)[1]
            )
        if self.empty_commit:
            git(self.repo, "commit", "--quiet", "--allow-empty", "-m", "empty correction")
        payload = {
            "result": {
                "head_sha": git(self.repo, "rev-parse", "HEAD"),
                "claims": [],
                "changed_files": [],
                "unresolved": self.unresolved,
            },
            "evidence": [],
        }
        stdout = (
            antigravity_envelope(payload)
            if command[0] == "agy"
            else json.dumps(payload)
        )
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=stdout, stderr=""
        )


class StaticRepairRunner:
    def __init__(
        self,
        repo: Path,
        *,
        empty_commit: bool = False,
        unresolved: list[str] | None = None,
    ) -> None:
        self.repo = repo
        self.empty_commit = empty_commit
        self.unresolved = [] if unresolved is None else unresolved
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == "agy":
            handoff_path = next(
                (self.repo / ".git" / "aios" / "handoffs").glob("*.json")
            )
            execution = json.loads(handoff_path.read_text(encoding="utf-8"))
        else:
            execution = json.loads(
                kwargs["input"].decode("utf-8").split("REPAIR_INPUT:\n", 1)[1]
            )
        if self.empty_commit:
            git(self.repo, "commit", "--quiet", "--allow-empty", "-m", "empty repair")
        head_sha = git(self.repo, "rev-parse", "HEAD")
        changed_files = sorted(
            path
            for path in git(
                self.repo,
                "diff",
                "--name-only",
                execution["root_base_sha"],
                head_sha,
            ).splitlines()
            if path
        )
        payload = result_payload(
            execution["run"]["run_id"], head_sha, changed_files=changed_files
        )
        payload["result"]["unresolved"] = self.unresolved
        stdout = (
            antigravity_envelope(payload)
            if command[0] == "agy"
            else json.dumps(payload)
        )
        return subprocess.CompletedProcess(command, returncode=0, stdout=stdout, stderr="")


def assert_native_executor_context(
    context: dict, *, executor: str, operation: str
) -> None:
    assert context["role"] == "NATIVE_EXECUTOR"
    assert context["selected_executor"] == executor
    assert context["operation"] == operation
    assert context["already_admitted"] is True
    assert context["direct_implementation"] is True
    assert context["operator_dispatch_authority"] is False
    assert context["runtime_verification_authority"] is False


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
    assert runner.calls[0][1]["timeout"] == 65 * 60
    assert staged["result"]["claims"] == []
    assert staged["result"]["unresolved"] == []
    assert staged["evidence"] == []
    assert stored["result"]["claims"] == []
    assert stored["result"]["changed_files"] == ["OUTPUT.txt"]
    assert stored["evidence"][0]["source"]["command"] == "git diff --check"
    assert "git status --porcelain" not in json.dumps(stored)
    assert not admission_failure_records(repo)
    assert runner.calls[0][1]["text"] is False
    assert "encoding" not in runner.calls[0][1]
    assert "errors" not in runner.calls[0][1]
    if executor == "antigravity":
        command = runner.calls[0][0]
        assert command[command.index("--print-timeout") + 1] == "60m"
        instruction = runner.calls[0][0][runner.calls[0][0].index("--print") + 1]
        assert "CODE_FIX" in instruction
        assert "EVIDENCE_ONLY" in instruction
        assert "push" in instruction.lower()
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
        assert_native_executor_context(
            handoff["execution_context"],
            executor="antigravity",
            operation="REMEDIATION",
        )
        assert remediation.affected_verification == ("git diff --check",)


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_remediation_failure_preserves_exact_staged_unresolved(
    tmp_path: Path, executor: str
) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    unresolved = ["first bounded fact", "second bounded fact"]
    runner = StaticRemediationRunner(repo, unresolved=unresolved)
    verification_calls = []

    with pytest.raises(OperatorError, match="unresolved"):
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            executor=executor,
            repo=repo,
            native_runner=runner,
            verification_runner=lambda *args, **kwargs: verification_calls.append(args),
        )

    failure = json.loads(
        (runtime_paths(repo).failures / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["error"]["executor_diagnostics"] == {
        "unresolved": unresolved
    }
    assert not admission_failure_records(repo)
    assert len(runner.calls) == 1
    assert verification_calls == []


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
@pytest.mark.parametrize("empty_commit", [False, True])
def test_code_fix_remediation_rejects_noop_and_empty_commit_before_verification(
    tmp_path: Path, empty_commit: bool, executor: str
) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    runner = StaticRemediationRunner(repo, empty_commit=empty_commit)
    verification_calls = []
    message = "committed delta is empty" if empty_commit else "did not advance HEAD"

    with pytest.raises(OperatorError, match=message):
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            executor=executor,
            repo=repo,
            native_runner=runner,
            verification_runner=lambda *args, **kwargs: verification_calls.append(args),
        )

    assert len(runner.calls) == 1
    assert verification_calls == []
    assert not (runtime_paths(repo).results / "RUN-101-001.json").exists()


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_evidence_only_remediation_retains_zero_mutation_contract(
    tmp_path: Path, executor: str
) -> None:
    repo = make_repo(tmp_path)
    reviewed_sha = git(repo, "rev-parse", "HEAD")
    remediation_contract(repo)
    review = operator_module.parse_review(
        f"""
review_id: REVIEW-101-002
reviewed_sha: {reviewed_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: E1
    basis: AC1
    action: EVIDENCE_ONLY
    location: OUTPUT.txt
    issue: The evidence is incomplete.
    expected: Re-run only affected verification.
"""
    )
    remediation = operator_module.parse_remediation(
        f"""
finding_id: E1
action: EVIDENCE_ONLY
reviewed_sha: {reviewed_sha}
modification_scope: []
affected_verification: [git diff --check]
constraints:
  hard: [Commit the output.]
"""
    )
    runner = StaticRemediationRunner(repo)

    summary = run_remediation(
        "TASK-101",
        review=review,
        remediation=remediation,
        executor=executor,
        repo=repo,
        native_runner=runner,
    )

    assert summary.head_sha == reviewed_sha
    assert len(runner.calls) == 1
    assert json.loads(summary.result_path.read_text(encoding="utf-8"))["result"][
        "changed_files"
    ] == []
    observation = json.loads(
        (
            runtime_paths(repo).observations / f"{summary.run_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert observation["operation"] == "REMEDIATION"
    assert observation["terminal_kind"] == "RESULT"


def test_remote_remediation_resolves_lineage_and_uses_normal_boundary(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    remediation_contract(repo)
    publish_direct_candidate_lineage(repo, tmp_path)
    baseline = git(repo, "rev-parse", "HEAD")
    runner = RemediationRunner(repo)

    summary = run_remediation(
        "TASK-101",
        finding_id="R1",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )
    run_data = json.loads(
        (runtime_paths(repo).runs / f"{summary.run_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert summary.review_id == "REVIEW-101-001"
    assert summary.finding_id == "R1"
    assert summary.reviewed_sha == baseline
    assert summary.head_sha != baseline
    assert len(runner.calls) == 1
    assert run_data["kind"] == "REMEDIATION"
    assert run_data["execution"]["run"]["executor"] == "codex"
    assert git(repo, "status", "--porcelain") == ""


def test_remote_remediation_resolution_failure_invokes_no_executor(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    calls = []
    state = runtime_paths(repo)

    with pytest.raises(OperatorError, match="lineage not found"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []
    assert list(state.runs.glob("*.json")) == []


def test_remediation_rejects_mixed_remote_and_explicit_modes(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    calls = []

    with pytest.raises(OperatorError, match="cannot be mixed"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            review=review,
            remediation=remediation,
            executor="codex",
            repo=repo,
            native_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []


def test_direct_candidate_acceptance_resolves_remote_lineage_without_executor(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    remediation_contract(repo)
    publish_direct_candidate_lineage(repo, tmp_path)
    reviewed_sha = git(repo, "rev-parse", "HEAD")
    (repo / "OUTPUT.txt").write_text("direct candidate\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "human-selected executor candidate")
    candidate_head = git(repo, "rev-parse", "HEAD")
    verification_calls = []

    def verification_runner(command, **kwargs):
        verification_calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"affected pass\n", stderr=b"")

    summary = accept_candidate(
        "TASK-101",
        finding_id="R1",
        executor="codex",
        repo=repo,
        verification_runner=verification_runner,
    )
    stored = json.loads(summary.result_path.read_text(encoding="utf-8"))
    run_data = json.loads(
        (runtime_paths(repo).runs / f"{summary.run_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert summary.reviewed_sha == reviewed_sha
    assert summary.head_sha == candidate_head
    assert run_data["acceptance"] == {
        "mode": "DIRECT_CANDIDATE",
        "candidate_head": candidate_head,
    }
    assert run_data["execution"]["run"]["executor"] == "codex"
    assert stored["result"] == {
        "head_sha": candidate_head,
        "claims": [],
        "changed_files": ["OUTPUT.txt"],
        "unresolved": [],
    }
    assert [item["source"]["command"] for item in stored["evidence"]] == [
        "git diff --check"
    ]
    assert len(verification_calls) == 1

    repeated = accept_candidate(
        "TASK-101",
        finding_id="R1",
        executor="antigravity",
        repo=repo,
        verification_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("idempotent acceptance must not rerun verification")
        ),
    )
    assert repeated.run_id == summary.run_id
    assert repeated.executor == "codex"
    assert run_data["execution"]["run"]["executor"] == "codex"


def test_direct_candidate_rejection_precedes_canonical_admission(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    remediation_contract(repo)
    publish_direct_candidate_lineage(repo, tmp_path)
    (repo / "README.md").write_text("outside authority\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "--quiet", "-m", "outside direct scope")
    state = runtime_paths(repo)
    before = {path.name for path in state.runs.glob("*.json")}

    with pytest.raises(OperatorError, match="REMEDIATION modification scope"):
        accept_candidate(
            "TASK-101", finding_id="R1", executor="antigravity", repo=repo
        )

    assert {path.name for path in state.runs.glob("*.json")} == before
    assert not list(state.failures.glob("*.json"))


@pytest.mark.parametrize("empty_commit", [False, True])
def test_direct_candidate_rejects_unchanged_and_empty_commit(
    tmp_path: Path, empty_commit: bool
) -> None:
    repo = make_repo(tmp_path)
    remediation_contract(repo)
    publish_direct_candidate_lineage(repo, tmp_path)
    if empty_commit:
        git(repo, "commit", "--quiet", "--allow-empty", "-m", "empty candidate")
    verification_calls = []
    message = "committed delta is empty" if empty_commit else "did not advance HEAD"

    with pytest.raises(OperatorError, match=message):
        accept_candidate(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            verification_runner=lambda *args, **kwargs: verification_calls.append(args),
        )

    assert verification_calls == []
    assert not (runtime_paths(repo).runs / "RUN-101-001.json").exists()


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


def test_remote_remediation_lineage_failure_publishes_bounded_admission_diagnostic(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    calls = []

    with pytest.raises(
        OperatorError, match="canonical remote remediation lineage not found"
    ) as raised:
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=lambda *args, **kwargs: calls.append(args),
        )

    expected = {
        "kind": "ADMISSION_FAILURE",
        "operation": "REMEDIATION",
        "task": {"id": "TASK-101", "revision": 1},
        "requested_executor": "codex",
        "executor_invoked": False,
        "phase": "REMOTE_LINEAGE_RESOLUTION",
        "finding_id": "R1",
        "error": {
            "type": "OperatorError",
            "message": str(raised.value),
        },
    }
    assert calls == []
    assert admission_failure_records(repo) == [expected]
    assert remote_admission_failure_records(tmp_path / "upstream.git") == [expected]
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list(runtime_paths(repo).observations.glob("*.json"))


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("scope", "CODE_FIX remediation modification scope is empty"),
        ("verification", "REMEDIATION affected verification is empty"),
        ("binding", "invalid REMEDIATION: REMEDIATION reviewed_sha"),
    ],
)
def test_contract_admission_failures_are_executor_neutral_and_exact(
    tmp_path: Path, executor: str, change: str, message: str
) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    if change == "scope":
        remediation = replace(remediation, modification_scope=())
    elif change == "verification":
        remediation = replace(remediation, affected_verification=())
    else:
        remediation = replace(remediation, reviewed_sha="deadbeef")
    calls = []

    with pytest.raises(OperatorError, match=message) as raised:
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            executor=executor,
            repo=repo,
            native_runner=lambda *args, **kwargs: calls.append(args),
        )

    records = admission_failure_records(repo)
    assert calls == []
    assert len(records) == 1
    assert records[0] == {
        "kind": "ADMISSION_FAILURE",
        "operation": "REMEDIATION",
        "task": {"id": "TASK-101", "revision": 1},
        "requested_executor": executor,
        "executor_invoked": False,
        "phase": "CANONICAL_CONTRACT_ADMISSION",
        "finding_id": remediation.finding_id,
        "review_id": review.review_id,
        "reviewed_sha": remediation.reviewed_sha,
        "error": {
            "type": "OperatorError",
            "message": str(raised.value),
        },
    }
    assert not list(runtime_paths(repo).runs.glob("RUN-101-001.json"))


@pytest.mark.parametrize("repository_failure", ["dirty", "reviewed-sha"])
def test_repository_admission_failure_creates_no_execution_state(
    tmp_path: Path, repository_failure: str
) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    if repository_failure == "dirty":
        (repo / "README.md").write_text("dirty\n", encoding="utf-8")
        message = "repository dirty"
    else:
        review, remediation = remediation_contract(repo, reviewed_sha="deadbeef")
        message = "current HEAD does not match REMEDIATION reviewed_sha"
    calls = []
    state = runtime_paths(repo)

    with pytest.raises(OperatorError, match=message) as raised:
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            executor="antigravity",
            repo=repo,
            native_runner=lambda *args, **kwargs: calls.append(args),
        )

    record = admission_failure_records(repo)[0]
    assert record["phase"] == "REPOSITORY_ADMISSION"
    assert record["current_head_sha"] == git(repo, "rev-parse", "HEAD")
    assert record["error"] == {
        "type": "OperatorError",
        "message": str(raised.value),
    }
    assert calls == []
    assert not list(state.runs.glob("RUN-101-001.json"))
    assert not list(state.handoffs.glob("*.json"))
    assert not list(state.staging.glob("*.json"))
    assert not list(state.results.glob("RUN-101-001.json"))
    assert not list(state.verification.rglob("*"))


def test_foreign_run_during_admission_failure_is_not_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    foreign_run = state.runs / "RUN-999-001.json"
    baseline = git(repo, "rev-parse", "HEAD")

    def fail_after_foreign_run(*args, **kwargs):
        assert kwargs["attempt"].run_path is None
        foreign_run.write_text(
            json.dumps(
                {
                    "run_id": "RUN-999-001",
                    "task": {"id": "TASK-999", "revision": 1},
                    "executor": "codex",
                    "base_sha": baseline,
                    "workspace": str(repo),
                    "head_sha": None,
                    "status": "ACTIVE",
                }
            ),
            encoding="utf-8",
        )
        raise OperatorError("current remediation rejected before RUN creation")

    monkeypatch.setattr(
        operator_module, "_run_remediation_impl", fail_after_foreign_run
    )

    with pytest.raises(
        OperatorError, match="current remediation rejected before RUN creation"
    ):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
        )

    records = admission_failure_records(repo)
    assert len(records) == 1
    assert records[0]["phase"] == "REMOTE_LINEAGE_RESOLUTION"
    assert records[0]["finding_id"] == "R1"
    assert records[0]["executor_invoked"] is False
    assert foreign_run.is_file()
    assert not list(state.failures.glob("RUN-999-001*.json"))


def test_foreign_run_during_owned_run_failure_does_not_suppress_failure(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    state = runtime_paths(repo)
    foreign_run = state.runs / "RUN-999-001.json"
    baseline = git(repo, "rev-parse", "HEAD")
    calls = []

    def fail_with_foreign_run(command, **kwargs):
        calls.append(command)
        foreign_run.write_text(
            json.dumps(
                {
                    "run_id": "RUN-999-001",
                    "task": {"id": "TASK-999", "revision": 1},
                    "executor": "codex",
                    "base_sha": baseline,
                    "workspace": str(repo),
                    "head_sha": None,
                    "status": "ACTIVE",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=b"not-json", stderr=b""
        )

    with pytest.raises(OperatorError, match="invalid structural ResultPackage"):
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            executor="codex",
            repo=repo,
            native_runner=fail_with_foreign_run,
        )

    assert len(calls) == 1
    assert (state.failures / "RUN-101-001.json").is_file()
    assert not list(state.failures.glob("RUN-999-001*.json"))
    assert admission_failure_records(repo) == []


def test_primary_pre_run_failure_does_not_claim_foreign_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)

    def fail_before_owned_run(*args, **kwargs):
        assert kwargs["attempt"].run_path is None
        write_foreign_run(repo)
        raise OperatorError("PRIMARY rejected before RUN creation")

    monkeypatch.setattr(operator_module, "_run_task_impl", fail_before_owned_run)

    with pytest.raises(OperatorError, match="PRIMARY rejected before RUN creation"):
        run_task("TASK-101", executor="codex", repo=repo)

    assert (state.runs / "RUN-999-001.json").is_file()
    assert not (state.failures / "RUN-999-001.json").exists()
    assert not (state.observations / "RUN-999-001.json").exists()


def test_primary_owned_run_failure_survives_foreign_run_interleaving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    inject_foreign_run_on_lock_release(repo, monkeypatch)

    def fail_with_foreign_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=b"not-json", stderr=b""
        )

    with pytest.raises(OperatorError, match="invalid structural ResultPackage"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=fail_with_foreign_run,
        )

    observation = json.loads(
        (state.observations / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert (state.failures / "RUN-101-001.json").is_file()
    assert observation["operation"] == "PRIMARY"
    assert observation["terminal_kind"] == "FAILURE"
    assert not (state.failures / "RUN-999-001.json").exists()
    assert not (state.observations / "RUN-999-001.json").exists()


def test_repair_pre_run_failure_does_not_claim_foreign_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(repo)
    state = runtime_paths(repo)

    def fail_before_owned_run(*args, **kwargs):
        assert kwargs["attempt"].run_path is None
        write_foreign_run(repo)
        raise OperatorError("REPAIR rejected before RUN creation")

    monkeypatch.setattr(operator_module, "_run_repair_impl", fail_before_owned_run)

    with pytest.raises(OperatorError, match="REPAIR rejected before RUN creation"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair,
        )

    assert (state.runs / "RUN-999-001.json").is_file()
    assert not (state.failures / "RUN-999-001.json").exists()
    assert not (state.observations / "RUN-999-001.json").exists()


def test_repair_owned_run_failure_survives_foreign_run_interleaving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(repo)
    state = runtime_paths(repo)
    inject_foreign_run_on_lock_release(repo, monkeypatch)

    def fail_with_foreign_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=b"not-json", stderr=b""
        )

    with pytest.raises(OperatorError, match="invalid structural ResultPackage"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair,
            native_runner=fail_with_foreign_run,
        )

    observation = json.loads(
        (state.observations / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert (state.failures / "RUN-101-001.json").is_file()
    assert observation["operation"] == "REPAIR"
    assert observation["terminal_kind"] == "FAILURE"
    assert not (state.failures / "RUN-999-001.json").exists()
    assert not (state.observations / "RUN-999-001.json").exists()


def test_admission_diagnostic_is_content_addressed_idempotent_and_immutable(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)

    for finding_id in ("R1", "R1", "R2"):
        with pytest.raises(OperatorError):
            run_remediation(
                "TASK-101",
                finding_id=finding_id,
                executor="codex",
                repo=repo,
                native_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("executor must not run")
                ),
            )

    local = admission_failure_records(repo)
    remote = remote_admission_failure_records(tmp_path / "upstream.git")
    assert len(local) == len(remote) == 2
    assert local == remote
    assert {record["finding_id"] for record in local} == {"R1", "R2"}


def test_admission_transport_failure_preserves_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)

    def fail_transport(*args, **kwargs):
        raise operator_module.ReviewTransportError("transport unavailable")

    monkeypatch.setattr(operator_module, "transport_admission_failure", fail_transport)

    with pytest.raises(
        OperatorError, match="canonical remote remediation lineage not found"
    ) as raised:
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("executor must not run")
            ),
        )

    assert admission_failure_records(repo)[0]["error"]["message"] == str(
        raised.value
    )
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_unavailable_admission_remote_does_not_mask_local_error(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    branch = git(repo, "symbolic-ref", "--short", "HEAD")
    git(repo, "config", "--unset", f"branch.{branch}.remote")

    with pytest.raises(
        OperatorError,
        match="remote remediation lineage resolution failed: no configured upstream",
    ) as raised:
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="antigravity",
            repo=repo,
            native_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("executor must not run")
            ),
        )

    record = admission_failure_records(repo)[0]
    assert record["error"] == {
        "type": "OperatorError",
        "message": str(raised.value),
    }
    assert record["executor_invoked"] is False
    assert not list(runtime_paths(repo).runs.glob("*.json"))


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
            "-b",
            "main",
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
    prohibited = {"merge", "rebase", "reset", "checkout", "stash", "clean", "pull", "push", "revert"}
    assert not any(args and args[0] in prohibited for args in git_calls)


@pytest.mark.parametrize(
    "state",
    [
        "detached",
        "non-main",
        "missing-upstream",
        "ambiguous-upstream",
        "upstream-not-main",
        "ahead",
        "diverged",
        "dirty",
    ],
)
def test_unsafe_primary_git_states_fail_before_executor(
    tmp_path: Path, state: str
) -> None:
    repo = make_repo(tmp_path)
    if state == "detached":
        git(repo, "checkout", "--quiet", "--detach")
    elif state == "non-main":
        git(repo, "checkout", "--quiet", "-b", "feature")
    elif state == "missing-upstream":
        git(repo, "branch", "--unset-upstream")
    elif state == "ambiguous-upstream":
        git(repo, "config", "--add", "branch.main.remote", "second-remote")
    elif state == "upstream-not-main":
        git(repo, "config", "branch.main.merge", "refs/heads/feature")
    elif state == "ahead":
        (repo / "LOCAL.txt").write_text("local\n", encoding="utf-8")
        git(repo, "add", "LOCAL.txt")
        git(repo, "commit", "--quiet", "-m", "local")
    elif state == "diverged":
        publish_upstream(repo, {"REMOTE.txt": "remote\n"}, "remote")
        (repo / "LOCAL.txt").write_text("local\n", encoding="utf-8")
        git(repo, "add", "LOCAL.txt")
        git(repo, "commit", "--quiet", "-m", "local")
    elif state == "dirty":
        (repo / "DIRTY.txt").write_text("dirty\n", encoding="utf-8")

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


def test_synchronization_failure_fails_closed_before_run_and_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    local_sha = git(repo, "rev-parse", "HEAD")
    publish_upstream(
        repo, {".ai/tasks/TASK-101.yaml": TASK_SOURCE}, "publish task"
    )
    real_git = operator_module._git

    def failing_git(root, *args, **kwargs):
        if args and args[0] == "read-tree":
            raise OperatorError("simulated read-tree failure")
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", failing_git)

    def runner(command, **kwargs):
        raise AssertionError("executor must not be invoked")

    with pytest.raises(OperatorError, match="upstream fast-forward failed"):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)
    assert git(repo, "rev-parse", "HEAD") == local_sha
    assert git(repo, "status", "--porcelain") == ""
    assert not (repo / ".ai/tasks/TASK-101.yaml").exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_synchronization_update_ref_failure_rolls_back_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    (repo / "INITIAL.txt").write_text("initial content\n", encoding="utf-8")
    git(repo, "add", "INITIAL.txt")
    git(repo, "commit", "--quiet", "-m", "add initial file")
    local_sha = git(repo, "rev-parse", "HEAD")
    branch_ref = git(repo, "symbolic-ref", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    published_sha = publish_upstream(
        repo,
        {
            ".ai/tasks/TASK-101.yaml": TASK_SOURCE,
            "INITIAL.txt": "upstream modified content\n",
            "NEW_REMOTE.txt": "remote content\n",
        },
        "publish upstream changes",
    )

    git_calls = []
    real_git = operator_module._git

    def failing_git(root, *args, **kwargs):
        git_calls.append(args)
        if args and args[0] == "update-ref":
            raise OperatorError("simulated update-ref failure")
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", failing_git)
    runner = FakeCodexRunner(repo)

    def runner_wrapper(command, **kwargs):
        return runner(command, **kwargs)

    with pytest.raises(OperatorError, match="upstream fast-forward failed"):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner_wrapper)

    assert git(repo, "rev-parse", "HEAD") == local_sha
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / "INITIAL.txt").read_text(encoding="utf-8") == "initial content\n"
    assert not (repo / "NEW_REMOTE.txt").exists()
    assert not (repo / ".ai/tasks/TASK-101.yaml").exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(runner.calls) == 0

    assert ("read-tree", "-u", "-m", local_sha, published_sha) in git_calls
    assert ("update-ref", branch_ref, published_sha, local_sha) in git_calls
    assert ("read-tree", "-u", "-m", published_sha, local_sha) in git_calls
    prohibited = {"merge", "rebase", "reset", "checkout", "stash", "clean", "pull", "push", "revert"}
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_synchronization_post_update_failure_rolls_back_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    (repo / "INITIAL.txt").write_text("initial content\n", encoding="utf-8")
    git(repo, "add", "INITIAL.txt")
    git(repo, "commit", "--quiet", "-m", "add initial file")
    local_sha = git(repo, "rev-parse", "HEAD")
    branch_ref = git(repo, "symbolic-ref", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    published_sha = publish_upstream(
        repo,
        {
            ".ai/tasks/TASK-101.yaml": TASK_SOURCE,
            "INITIAL.txt": "upstream modified content\n",
            "NEW_REMOTE.txt": "remote content\n",
        },
        "publish upstream changes",
    )

    git_calls = []
    real_git = operator_module._git
    status_checked_after_update = False

    def failing_git(root, *args, **kwargs):
        nonlocal status_checked_after_update
        git_calls.append(args)
        if (
            args
            and args[0] == "status"
            and ("update-ref", branch_ref, published_sha, local_sha) in git_calls
            and not status_checked_after_update
        ):
            status_checked_after_update = True
            return "?? unexpected_untracked_file\n"
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", failing_git)
    runner = FakeCodexRunner(repo)

    with pytest.raises(OperatorError, match="repository dirty after synchronization"):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert git(repo, "rev-parse", "HEAD") == local_sha
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / "INITIAL.txt").read_text(encoding="utf-8") == "initial content\n"
    assert not (repo / "NEW_REMOTE.txt").exists()
    assert not (repo / ".ai/tasks/TASK-101.yaml").exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(runner.calls) == 0

    assert ("read-tree", "-u", "-m", local_sha, published_sha) in git_calls
    assert ("update-ref", branch_ref, published_sha, local_sha) in git_calls
    assert ("update-ref", branch_ref, local_sha, published_sha) in git_calls
    assert ("read-tree", "-u", "-m", published_sha, local_sha) in git_calls
    prohibited = {"merge", "rebase", "reset", "checkout", "stash", "clean", "pull", "push", "revert"}
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_synchronization_restoration_read_tree_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    (repo / "INITIAL.txt").write_text("initial content\n", encoding="utf-8")
    git(repo, "add", "INITIAL.txt")
    git(repo, "commit", "--quiet", "-m", "add initial file")
    local_sha = git(repo, "rev-parse", "HEAD")
    branch_ref = git(repo, "symbolic-ref", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    published_sha = publish_upstream(
        repo,
        {
            ".ai/tasks/TASK-101.yaml": TASK_SOURCE,
            "INITIAL.txt": "upstream modified content\n",
            "NEW_REMOTE.txt": "remote content\n",
        },
        "publish upstream changes",
    )

    git_calls = []
    real_git = operator_module._git

    def failing_git(root, *args, **kwargs):
        git_calls.append(args)
        if args and args[0] == "update-ref" and args[1:] == (branch_ref, published_sha, local_sha):
            raise OperatorError("simulated update-ref forward failure")
        if args and args[0] == "read-tree" and args[1:] == ("-u", "-m", published_sha, local_sha):
            raise OperatorError("simulated read-tree rollback failure")
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", failing_git)
    runner = FakeCodexRunner(repo)

    with pytest.raises(
        OperatorError, match="upstream synchronization restoration failed"
    ):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(runner.calls) == 0

    assert ("read-tree", "-u", "-m", local_sha, published_sha) in git_calls
    assert ("update-ref", branch_ref, published_sha, local_sha) in git_calls
    assert ("read-tree", "-u", "-m", published_sha, local_sha) in git_calls
    prohibited = {"merge", "rebase", "reset", "checkout", "stash", "clean", "pull", "push", "revert"}
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_synchronization_restoration_update_ref_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    (repo / "INITIAL.txt").write_text("initial content\n", encoding="utf-8")
    git(repo, "add", "INITIAL.txt")
    git(repo, "commit", "--quiet", "-m", "add initial file")
    local_sha = git(repo, "rev-parse", "HEAD")
    branch_ref = git(repo, "symbolic-ref", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    published_sha = publish_upstream(
        repo,
        {
            ".ai/tasks/TASK-101.yaml": TASK_SOURCE,
            "INITIAL.txt": "upstream modified content\n",
            "NEW_REMOTE.txt": "remote content\n",
        },
        "publish upstream changes",
    )

    git_calls = []
    real_git = operator_module._git
    status_checked_after_update = False

    def failing_git(root, *args, **kwargs):
        nonlocal status_checked_after_update
        git_calls.append(args)
        if (
            args
            and args[0] == "status"
            and ("update-ref", branch_ref, published_sha, local_sha) in git_calls
            and not status_checked_after_update
        ):
            status_checked_after_update = True
            return "?? unexpected_untracked_file\n"
        if args and args[0] == "update-ref" and args[1:] == (branch_ref, local_sha, published_sha):
            raise OperatorError("simulated update-ref rollback failure")
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", failing_git)
    runner = FakeCodexRunner(repo)

    with pytest.raises(
        OperatorError, match="upstream synchronization restoration failed"
    ):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(runner.calls) == 0

    assert ("read-tree", "-u", "-m", local_sha, published_sha) in git_calls
    assert ("update-ref", branch_ref, published_sha, local_sha) in git_calls
    assert ("update-ref", branch_ref, local_sha, published_sha) in git_calls
    assert ("read-tree", "-u", "-m", published_sha, local_sha) in git_calls
    prohibited = {"merge", "rebase", "reset", "checkout", "stash", "clean", "pull", "push", "revert"}
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_primary_upstream_equal_is_noop_and_admission_proceeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    local_sha = git(repo, "rev-parse", "HEAD")
    git_calls = []
    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", recording_git)
    runner = FakeCodexRunner(repo)
    summary = run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert summary.base_sha == local_sha
    assert not any(args and args[0] in ("read-tree", "update-ref") for args in git_calls)
    canonical = json.loads(
        runner.calls[0][1]["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
    )
    assert canonical["run"]["base_sha"] == local_sha


def test_primary_sync_advancing_source_and_task_restarts_and_consumes_canonical_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    local_sha = git(repo, "rev-parse", "HEAD")
    task_102_source = TASK_SOURCE.replace("TASK-101", "TASK-102")
    published_sha = publish_upstream(
        repo,
        {
            "src/aios_renew/marker.py": "# kernel updated\n",
            ".ai/tasks/TASK-102.yaml": task_102_source,
        },
        "publish kernel update and task",
    )
    runner = FakeCodexRunner(repo)

    with pytest.raises(
        OperatorError, match="cannot continue under stale pre-sync kernel state"
    ):
        run_task("TASK-102", executor="codex", repo=repo, native_runner=runner)
    assert git(repo, "rev-parse", "HEAD") == local_sha
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(runner.calls) == 0

    restarts = []
    real_restart = operator_module._restart_primary_invocation

    def fake_restart(root, *, argv=None, runner=subprocess.run):
        restarts.append((root, argv))
        return 0

    monkeypatch.setattr(operator_module, "_restart_primary_invocation", fake_restart)
    exit_code = operator_module.main(
        ["run", "TASK-102", "--executor", "codex", "--repo", str(repo)]
    )
    assert exit_code == 0
    assert git(repo, "rev-parse", "HEAD") == published_sha
    assert len(restarts) == 1

    runner_calls = []
    def fake_runner(cmd, **kwargs):
        runner_calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    real_restart(
        repo,
        argv=["run", "TASK-102", "--executor", "codex", "--repo", str(repo)],
        runner=fake_runner,
    )
    assert len(runner_calls) == 1
    assert runner_calls[0][1]["env"]["AIOS_RESTART_ATTEMPTED"] == "1"

    summary = run_task("TASK-102", executor="codex", repo=repo, native_runner=runner)
    assert summary.base_sha == published_sha
    assert summary.task_id == "TASK-102"
    canonical = json.loads(
        runner.calls[0][1]["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
    )
    assert canonical["run"]["base_sha"] == published_sha
    assert canonical["task"]["task_id"] == "TASK-102"

    repo_unsafe = make_repo(tmp_path / "unsafe", task_source=None)
    local_unsafe_sha = git(repo_unsafe, "rev-parse", "HEAD")
    publish_upstream(
        repo_unsafe, {"src/aios_renew/marker.py": "# k\n"}, "k"
    )
    monkeypatch.setenv("AIOS_RESTART_ATTEMPTED", "1")
    with pytest.raises(OperatorError, match="unsafe reload/restart condition"):
        operator_module._preflight_primary_sync(
            repo_unsafe,
            argv=["run", "TASK-102", "--executor", "codex", "--repo", str(repo_unsafe)],
        )
    assert git(repo_unsafe, "rev-parse", "HEAD") == local_unsafe_sha


def test_remediation_repair_and_candidate_do_not_auto_sync_upstream_main(
    tmp_path: Path,
) -> None:
    repo_rem = make_repo(tmp_path / "rem")
    review, remediation = remediation_contract(repo_rem)
    reviewed_sha = remediation.reviewed_sha
    upstream_rem_sha = publish_upstream(
        repo_rem, {"ADVANCE.txt": "advance\n"}, "advance upstream main"
    )
    summary_rem = run_remediation(
        "TASK-101",
        review=review,
        remediation=remediation,
        executor="codex",
        repo=repo_rem,
        native_runner=RemediationRunner(repo_rem),
    )
    assert summary_rem.reviewed_sha == reviewed_sha
    assert git(repo_rem, "rev-parse", "HEAD") != upstream_rem_sha

    repo_rep = make_repo(tmp_path / "rep")
    failed_run_id, repair = repair_contract(repo_rep, action="NO_CHANGE")
    failed_head = git(repo_rep, "rev-parse", "HEAD")
    upstream_rep_sha = publish_upstream(
        repo_rep, {"ADVANCE.txt": "advance\n"}, "advance upstream main"
    )
    summary_rep = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo_rep,
        repair=repair,
        native_runner=StaticRepairRunner(repo_rep),
    )
    assert summary_rep.failed_head_sha == failed_head
    assert git(repo_rep, "rev-parse", "HEAD") != upstream_rep_sha

    repo_cand = make_repo(tmp_path / "cand")
    remediation_contract(repo_cand)
    publish_direct_candidate_lineage(repo_cand, tmp_path / "cand")
    (repo_cand / "OUTPUT.txt").write_text("candidate fix\n", encoding="utf-8")
    git(repo_cand, "add", "OUTPUT.txt")
    git(repo_cand, "commit", "--quiet", "-m", "candidate commit")
    cand_head = git(repo_cand, "rev-parse", "HEAD")
    upstream_cand_sha = publish_upstream(
        repo_cand, {"ADVANCE.txt": "advance\n"}, "advance upstream main"
    )
    summary_cand = accept_candidate(
        "TASK-101",
        finding_id="R1",
        executor="codex",
        repo=repo_cand,
        verification_runner=lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=b"pass", stderr=b""),
    )
    assert summary_cand.head_sha == cand_head
    assert git(repo_cand, "rev-parse", "HEAD") != upstream_cand_sha


def test_operator_delegates_one_primary_invocation_to_dispatcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    real_factory = operator_module.primary_dispatcher
    calls = []

    class SpyDispatcher:
        def __init__(self, inner):
            self.inner = inner

        def dispatch_primary(self, **kwargs):
            calls.append(kwargs)
            return self.inner.dispatch_primary(**kwargs)

    def factory(**kwargs):
        return SpyDispatcher(real_factory(**kwargs))

    monkeypatch.setattr(operator_module, "primary_dispatcher", factory)
    run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )

    assert len(calls) == 1
    assert calls[0]["lease"] is not None
    assert calls[0]["leases"] is not None


def test_mutating_codex_capability_is_resolved_before_invocation(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)

    run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    command = runner.calls[0][0]
    assert command[command.index("--sandbox") + 1] == "danger-full-access"
    assert runner.calls[0][1]["timeout"] == 65 * 60
    assert len(runner.calls) == 1


def test_read_only_codex_execution_remains_read_only(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)
    delegate = StaticResultRunner(repo, static_payload())
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return delegate(command, **kwargs)

    run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )

    assert calls[0][calls[0].index("--sandbox") + 1] == "read-only"
    assert len(calls) == 1


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_native_watchdog_expiry_is_one_terminal_execution_failure(
    tmp_path: Path, executor: str
) -> None:
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)
    native_calls = []
    verification_calls = []

    def expire_immediately(command, **kwargs):
        native_calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    with pytest.raises(
        OperatorError, match="60-minute native response deadline"
    ):
        run_task(
            "TASK-101",
            executor=executor,
            repo=repo,
            native_runner=expire_immediately,
            verification_runner=lambda *args, **kwargs: verification_calls.append(
                (args, kwargs)
            ),
        )

    assert len(native_calls) == 1
    assert native_calls[0][1]["timeout"] == 65 * 60
    assert native_calls[0][1]["timeout"] <= 66 * 60
    assert verification_calls == []
    state = runtime_paths(repo)
    failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert failure["run_id"] == "RUN-101-001"
    assert failure["executor"] == executor
    assert failure["phase"] == "EXECUTION"
    assert failure["error"]["type"] == (
        "CodexExecutionError"
        if executor == "codex"
        else "AntigravityExecutionError"
    )
    assert not list(state.results.glob("RUN-101-001.json"))


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_primary_keyboard_interrupt_terminalizes_without_altering_candidate(
    tmp_path: Path, executor: str
) -> None:
    repo = make_repo(tmp_path)
    base_sha = git(repo, "rev-parse", "HEAD")
    runner = InterruptingRunner(
        repo,
        dirty=True,
        stdout=b"x" * 5000,
        stderr=b"provider interrupted",
    )

    with pytest.raises(KeyboardInterrupt):
        run_task(
            "TASK-101",
            executor=executor,
            repo=repo,
            native_runner=runner,
            verification_runner=lambda *args, **kwargs: pytest.fail(
                "interruption must not start Runtime verification"
            ),
        )

    assert len(runner.calls) == 1
    assert git(repo, "rev-parse", "HEAD") == base_sha
    assert (repo / "OUTPUT.txt").read_text(encoding="utf-8") == (
        "partial uncommitted work\n"
    )
    failure = json.loads(
        (runtime_paths(repo).failures / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["run_id"] == "RUN-101-001"
    assert failure["executor"] == executor
    assert failure["base_sha"] == base_sha
    assert failure["failed_head_sha"] == base_sha
    assert failure["phase"] == "EXECUTION"
    assert not (runtime_paths(repo).results / "RUN-101-001.json").exists()
    assert failure["candidate"] == {
        "transportable": False,
        "repairable": False,
        "dirty": True,
        "descends_from_base": True,
        "changed_files": [],
        "outside_task_scope": [],
    }
    diagnostics = failure["error"]["native_diagnostics"]
    assert diagnostics["limit_chars"] == 4096
    assert diagnostics["stdout"] == {
        "availability": "captured",
        "text": "x" * 4096,
        "truncated": True,
    }
    assert diagnostics["stderr"] == {
        "availability": "captured",
        "text": "provider interrupted",
        "truncated": False,
    }
    remote_failure = json.loads(
        git(
            tmp_path / "upstream.git",
            "show",
            "refs/heads/aios/failure-artifacts/RUN-101-001:"
            ".ai/transport/failure.json",
        )
    )
    assert remote_failure == failure
    assert not git(
        tmp_path / "upstream.git",
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads/aios/failure/RUN-101-001",
    )


def test_runtime_verification_interrupt_records_truthful_portable_failure(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)

    with pytest.raises(KeyboardInterrupt):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=runner,
            verification_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt()
            ),
        )

    state = runtime_paths(repo)
    failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert runner.count == 1
    assert failure["phase"] == "VERIFICATION"
    assert failure["error"]["message"] == (
        "Runtime verification interrupted by Human"
    )
    remote_failure = json.loads(
        git(
            tmp_path / "upstream.git",
            "show",
            "refs/heads/aios/failure-artifacts/RUN-101-001:"
            ".ai/transport/failure.json",
        )
    )
    assert remote_failure == failure


def test_remediation_keyboard_interrupt_preserves_exact_lineage(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    base_sha = git(repo, "rev-parse", "HEAD")
    runner = InterruptingRunner(repo)

    with pytest.raises(KeyboardInterrupt):
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            executor="codex",
            repo=repo,
            native_runner=runner,
            verification_runner=lambda *args, **kwargs: pytest.fail(
                "interruption must not start Runtime verification"
            ),
        )

    state = runtime_paths(repo)
    run_record = json.loads(
        (state.runs / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert len(runner.calls) == 1
    assert run_record["kind"] == "REMEDIATION"
    assert run_record["execution"]["review_id"] == review.review_id
    assert run_record["execution"]["remediation"]["finding_id"] == (
        remediation.finding_id
    )
    assert failure["failed_head_sha"] == base_sha
    assert failure["candidate"]["repairable"] is True
    assert failure["error"]["native_diagnostics"] == {
        "limit_chars": 4096,
        "stdout": {"availability": "unavailable"},
        "stderr": {"availability": "unavailable"},
    }
    assert json.loads(
        git(
            tmp_path / "upstream.git",
            "show",
            "refs/heads/aios/failure-artifacts/RUN-101-001:"
            ".ai/transport/failure.json",
        )
    ) == failure


def test_repair_keyboard_interrupt_preserves_continuation_lineage(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(repo, action="NO_CHANGE")
    base_sha = git(repo, "rev-parse", "HEAD")
    runner = InterruptingRunner(repo)

    with pytest.raises(KeyboardInterrupt):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair,
            native_runner=runner,
            verification_runner=lambda *args, **kwargs: pytest.fail(
                "interruption must not start Runtime verification"
            ),
        )

    state = runtime_paths(repo)
    failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    repair_execution = json.loads(
        (state.repairs / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert len(runner.calls) == 1
    assert failure["continuation_of"] == failed_run_id
    assert failure["failed_head_sha"] == base_sha
    assert failure["candidate"]["repairable"] is True
    assert repair_execution["failed_run_id"] == failed_run_id
    assert repair_execution["repair"] == repair
    assert json.loads(
        git(
            tmp_path / "upstream.git",
            "show",
            "refs/heads/aios/failure-artifacts/RUN-101-001:"
            ".ai/transport/failure.json",
        )
    ) == failure

    continuation = dict(repair)
    continuation.update(
        {
            "repair_id": "REPAIR-101-CONTINUATION",
            "failed_run_id": "RUN-101-001",
            "failed_head_sha": base_sha,
        }
    )
    continuation_runner = StaticRepairRunner(repo)
    summary = run_repair(
        "RUN-101-001",
        executor="codex",
        repo=repo,
        repair=continuation,
        native_runner=continuation_runner,
    )
    assert summary.run_id == "RUN-101-002"
    assert len(continuation_runner.calls) == 1


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_native_timeout_failure_retains_bounded_partial_diagnostics(
    tmp_path: Path, executor: str
) -> None:
    repo = make_repo(tmp_path)

    def expire(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            output=b"partial progress",
            stderr=b"z" * 5000,
        )

    with pytest.raises(OperatorError, match="60-minute native response deadline"):
        run_task(
            "TASK-101",
            executor=executor,
            repo=repo,
            native_runner=expire,
        )

    failure = json.loads(
        (runtime_paths(repo).failures / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    diagnostics = failure["error"]["native_diagnostics"]
    assert diagnostics["stdout"] == {
        "availability": "captured",
        "text": "partial progress",
        "truncated": False,
    }
    assert diagnostics["stderr"] == {
        "availability": "captured",
        "text": "z" * 4096,
        "truncated": True,
    }
    assert not (runtime_paths(repo).results / "RUN-101-001.json").exists()


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
    assert command[command.index("--effort") + 1] == "high"
    assert command[command.index("--mode") + 1] == "accept-edits"
    assert "--disable-slash-commands" in command
    assert command[command.index("--output-format") + 1] == "json"
    assert "--json-schema" in command
    assert command[command.index("--print-timeout") + 1] == "60m"
    assert kwargs["timeout"] == 65 * 60
    assert kwargs["cwd"] == workspace
    assert kwargs["text"] is False
    assert "encoding" not in kwargs
    assert "errors" not in kwargs
    assert ".git" in instruction and "handoff" in instruction
    assert "Create one deterministic operator test output" not in instruction
    assert "--dangerously-skip-permissions" in command
    assert command[command.index("--model") + 1] == "gemini-3.8-flash"


def test_antigravity_instruction_returns_structural_package_to_runtime(
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
    assert "only response" in instruction
    assert "Runtime captures" in instruction
    assert "Runtime-owned operational state" in instruction
    assert "Runtime owns canonical verification" in instruction
    assert "do not execute canonical verification commands" in instruction
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
    assert_native_executor_context(
        handoff["execution_context"],
        executor="antigravity",
        operation="PRIMARY",
    )
    assert "structural_result_path" not in handoff


def test_read_only_antigravity_execution_has_no_mutation_capability(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)
    delegate = StaticResultRunner(repo, static_payload())
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return delegate(command, **kwargs)

    run_task(
        "TASK-101", executor="antigravity", repo=repo,
        native_runner=runner,
    )

    command = calls[0]
    assert command[command.index("--mode") + 1] == "plan"
    assert "--dangerously-skip-permissions" not in command


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
    failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert failure["phase"] == "VERIFICATION"
    assert failure["error"]["verification"] == [
        {
            "command": "first-command",
            "exit_code": 9,
            "summary": "failed",
        }
    ]
    assert "raw_path" not in failure["error"]["verification"][0]


def test_executor_failure_transports_compact_boundary_diagnostic(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)

    def timed_out_executor(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=124,
            stdout=b"",
            stderr=b"request timed out after 300s\nraw executor transcript\n",
        )

    with pytest.raises(OperatorError, match="request timed out after 300s"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=timed_out_executor,
        )

    failure = json.loads(
        (
            runtime_paths(repo).failures / "RUN-101-001.json"
        ).read_text(encoding="utf-8")
    )
    assert failure["phase"] == "EXECUTION"
    assert failure["error"] == {
        "type": "CodexExecutionError",
        "message": "Codex CLI exited with code 124: request timed out after 300s",
        "exit_code": 124,
    }
    assert "raw executor transcript" not in json.dumps(failure)
    assert "executor_diagnostics" not in failure["error"]
    observation_path = runtime_paths(repo).observations / "RUN-101-001.json"
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    assert observation["terminal_kind"] == "FAILURE"
    assert observation["executor_invoked"] is True
    remote = tmp_path / "upstream.git"
    remote_observation = git(
        remote,
        "show",
        "refs/heads/aios/failure-artifacts/RUN-101-001:"
        ".ai/transport/observation.json",
    )
    assert remote_observation == observation_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("staged_content", [None, "{malformed"])
def test_failure_persistence_falls_back_without_staged_diagnostics(
    tmp_path: Path, staged_content: str | None
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    run_path = state.runs / "RUN-101-001.json"
    run_path.write_text(
        json.dumps(
            {
                "run_id": "RUN-101-001",
                "task": {"id": "TASK-101", "revision": 1},
                "executor": "codex",
                "base_sha": git(repo, "rev-parse", "HEAD"),
                "workspace": str(repo),
                "head_sha": None,
                "status": "ACTIVE",
            }
        ),
        encoding="utf-8",
    )
    if staged_content is not None:
        (state.staging / "RUN-101-001.json").write_text(
            staged_content, encoding="utf-8"
        )

    operator_module._persist_and_transport_failure(
        repo,
        task_id="TASK-101",
        run_path=run_path,
        failure=OperatorError("original completion failure"),
    )

    failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert failure["error"] == {
        "type": "OperatorError",
        "message": "original completion failure",
    }


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


def test_non_structural_antigravity_stdout_fails_closed(
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

    assert "Antigravity ResultPackage missing" in str(captured.value)
    assert "headless tool action denied" in str(captured.value)


def test_prose_antigravity_result_fails_structural_validation(
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

    assert "Antigravity ResultPackage missing" in str(captured.value)
    assert "done" in str(captured.value)


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


@pytest.mark.parametrize(
    ("mode", "diagnostic"),
    [
        ("malformed-json", "malformed terminal JSON"),
        ("unsuccessful", "terminal status is ERROR: native failure"),
        ("malformed-metadata", "status must be a non-empty string"),
        ("malformed-payload", "structured_output must be a mapping"),
    ],
)
def test_invalid_antigravity_terminal_envelope_fails_once(
    tmp_path: Path, mode: str, diagnostic: str
) -> None:
    repo = make_repo(tmp_path)
    runner = FakeAntigravityRunner(repo, mode=mode)

    with pytest.raises(OperatorError, match=diagnostic):
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=runner,
        )

    assert len(runner.calls) == 1
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
    failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert failure["error"]["executor_diagnostics"] == {
        "unresolved": ["Codex could not complete execution."]
    }
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
    real_write_json = runtime_module._write_json
    canonical_result_writes = []

    def tracking_write_json(path, data):
        if path.parent.name == "results":
            canonical_result_writes.append(path)
        real_write_json(path, data)

    monkeypatch.setattr(runtime_module, "_write_json", tracking_write_json)

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


def test_operator_adds_no_background_or_orchestration_framework(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)
    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )
    # The operator executes synchronously and completes in exactly one shot
    assert summary.run_id == "RUN-101-001"
    assert runner.count == 1



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


def test_primary_run_transports_review_and_artifacts_refs(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)
    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )
    upstream = tmp_path / "upstream.git"
    review_ref = f"refs/heads/aios/review/{summary.run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{summary.run_id}"

    # Verify review ref points exactly to head_sha on upstream
    assert git(upstream, "rev-parse", review_ref) == summary.head_sha

    # Verify artifacts ref contains byte-exact run.json and result.json
    remote_run_json = git(upstream, "show", f"{artifacts_ref}:.ai/transport/run.json")
    remote_result_json = git(upstream, "show", f"{artifacts_ref}:.ai/transport/result.json")
    remote_observation_json = git(
        upstream, "show", f"{artifacts_ref}:.ai/transport/observation.json"
    )

    local_run_json = (runtime_paths(repo).runs / f"{summary.run_id}.json").read_text(encoding="utf-8")
    local_result_json = summary.result_path.read_text(encoding="utf-8")
    local_observation_json = (
        runtime_paths(repo).observations / f"{summary.run_id}.json"
    ).read_text(encoding="utf-8")

    assert remote_run_json == local_run_json
    assert remote_result_json == local_result_json
    assert remote_observation_json == local_observation_json


def test_remediation_run_transports_review_and_artifacts_refs(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    review, remediation = remediation_contract(repo)
    runner = RemediationRunner(repo)
    summary = run_remediation(
        "TASK-101",
        review=review,
        remediation=remediation,
        executor="codex",
        repo=repo,
        native_runner=runner,
    )
    upstream = tmp_path / "upstream.git"
    review_ref = f"refs/heads/aios/review/{summary.run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{summary.run_id}"

    assert git(upstream, "rev-parse", review_ref) == summary.head_sha

    remote_run_json = git(upstream, "show", f"{artifacts_ref}:.ai/transport/run.json")
    remote_result_json = git(upstream, "show", f"{artifacts_ref}:.ai/transport/result.json")

    local_run_json = (runtime_paths(repo).runs / f"{summary.run_id}.json").read_text(encoding="utf-8")
    local_result_json = summary.result_path.read_text(encoding="utf-8")
    local_observation_json = (
        runtime_paths(repo).observations / f"{summary.run_id}.json"
    ).read_text(encoding="utf-8")
    remote_observation_json = git(
        upstream, "show", f"{artifacts_ref}:.ai/transport/observation.json"
    )

    assert remote_run_json == local_run_json
    assert remote_result_json == local_result_json
    assert remote_observation_json == local_observation_json
    assert json.loads(local_observation_json)["operation"] == "REMEDIATION"


def test_transport_does_not_modify_product_head_worktree_or_main(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)
    head_before_run = git(repo, "rev-parse", "HEAD")

    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )

    # Worktree clean
    assert git(repo, "status", "--porcelain") == ""
    # HEAD is at the executor commit
    assert git(repo, "rev-parse", "HEAD") == summary.head_sha
    assert summary.head_sha != head_before_run

    # Main branch (or active branch) points to HEAD
    current_branch = git(repo, "symbolic-ref", "--short", "HEAD")
    assert git(repo, "rev-parse", current_branch) == summary.head_sha


def test_transport_does_not_run_on_verification_failure(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)

    def failing_verifier(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode=1, stdout=b"", stderr=b"verification failed")

    with pytest.raises(OperatorError):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=runner,
            verification_runner=failing_verifier,
        )

    upstream = tmp_path / "upstream.git"
    # Neither review ref nor artifacts ref should exist on upstream
    assert "refs/heads/aios/review" not in git(upstream, "show-ref", "--heads") if (upstream / "refs" / "heads" / "aios").exists() else True


def test_identical_existing_remote_transport_state_is_idempotent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)
    first = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )

    # Transport again explicitly for the exact same run
    from aios_renew.review_transport import transport_post_pass
    transport_post_pass(
        repo,
        run_id=first.run_id,
        head_sha=first.head_sha,
        run_path=runtime_paths(repo).runs / f"{first.run_id}.json",
        result_path=first.result_path,
    )


def test_transport_fails_closed_on_conflicting_remote_review_target(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    upstream = tmp_path / "upstream.git"
    initial_commit = git(repo, "rev-parse", "HEAD")
    git(upstream, "update-ref", "refs/heads/aios/review/RUN-101-001", initial_commit)

    runner = FakeCodexRunner(repo)
    with pytest.raises(OperatorError, match="review transport failed"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=runner,
        )


def test_transport_fails_closed_on_conflicting_remote_artifact_content(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)
    first = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )
    upstream = tmp_path / "upstream.git"
    # Overwrite the artifacts ref with a commit containing different content
    diff_commit = git(repo, "rev-parse", "HEAD~1")
    git(upstream, "update-ref", f"refs/heads/aios/artifacts/{first.run_id}", diff_commit)

    from aios_renew.review_transport import ReviewTransportError, transport_post_pass
    with pytest.raises(ReviewTransportError, match="different artifact content"):
        transport_post_pass(
            repo,
            run_id=first.run_id,
            head_sha=first.head_sha,
            run_path=runtime_paths(repo).runs / f"{first.run_id}.json",
            result_path=first.result_path,
        )


def test_transport_remote_resolution_fails_closed_without_remote_fallback(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    from aios_renew.review_transport import ReviewTransportError, resolve_transport_remote

    # Unset upstream tracking on current branch
    branch = git(repo, "symbolic-ref", "--short", "HEAD")
    git(repo, "config", "--unset", f"branch.{branch}.remote")

    # Ensure remotes (e.g. origin or another remote) exist in repo
    assert git(repo, "remote") != ""

    with pytest.raises(ReviewTransportError, match="no configured upstream Git remote for current branch"):
        resolve_transport_remote(repo)


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
@pytest.mark.parametrize("empty_commit", [False, True])
def test_code_fix_repair_rejects_noop_and_empty_correction_before_verification(
    tmp_path: Path, empty_commit: bool, executor: str
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(repo)
    runner = StaticRepairRunner(repo, empty_commit=empty_commit)
    verification_calls = []
    message = (
        "committed correction delta is empty"
        if empty_commit
        else "did not advance HEAD"
    )

    with pytest.raises(OperatorError, match=message):
        run_repair(
            failed_run_id,
            executor=executor,
            repo=repo,
            repair=repair,
            native_runner=runner,
            verification_runner=lambda *args, **kwargs: verification_calls.append(args),
        )

    assert len(runner.calls) == 1
    assert verification_calls == []
    assert not list(runtime_paths(repo).results.glob("RUN-101-001.json"))


def test_repair_failure_preserves_exact_staged_unresolved(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(repo, action="NO_CHANGE")
    unresolved = ["repair fact one", "repair fact two"]
    runner = StaticRepairRunner(repo, unresolved=unresolved)

    with pytest.raises(OperatorError, match="unresolved"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair,
            native_runner=runner,
        )

    failure = json.loads(
        (runtime_paths(repo).failures / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["error"]["executor_diagnostics"] == {
        "unresolved": unresolved
    }
    assert len(runner.calls) == 1


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_no_change_repair_retains_zero_mutation_contract(
    tmp_path: Path, executor: str
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(repo, action="NO_CHANGE")
    failed_head = git(repo, "rev-parse", "HEAD")
    runner = StaticRepairRunner(repo)

    summary = run_repair(
        failed_run_id,
        executor=executor,
        repo=repo,
        repair=repair,
        native_runner=runner,
    )

    assert summary.head_sha == failed_head
    assert len(runner.calls) == 1
    assert runner.calls[0][1]["timeout"] == 65 * 60
    assert json.loads(summary.result_path.read_text(encoding="utf-8"))["result"][
        "changed_files"
    ] == []
    if executor == "antigravity":
        command = runner.calls[0][0]
        assert command[command.index("--print-timeout") + 1] == "60m"
        handoff = json.loads(
            next(runtime_paths(repo).handoffs.glob("*.json")).read_text(
                encoding="utf-8"
            )
        )
        assert_native_executor_context(
            handoff["execution_context"],
            executor="antigravity",
            operation="REPAIR",
        )


def test_failed_repair_accepts_one_new_repair_with_original_task_root_lineage(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    root_base_sha = git(repo, "rev-parse", "HEAD")
    (repo / "OUTPUT.txt").write_text("failed candidate\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "failed candidate")
    first_failed_head = git(repo, "rev-parse", "HEAD")
    first_run_id = "RUN-101-001"
    (state.runs / f"{first_run_id}.json").write_text(
        json.dumps(
            {
                "run_id": first_run_id,
                "task": {"id": "TASK-101", "revision": 1},
                "executor": "codex",
                "base_sha": root_base_sha,
                "workspace": str(repo),
                "head_sha": None,
                "status": "ACTIVE",
            }
        ),
        encoding="utf-8",
    )
    (state.failures / f"{first_run_id}.json").write_text(
        json.dumps(
            {
                "kind": "FAILURE",
                "run_id": first_run_id,
                "task": {"id": "TASK-101", "revision": 1},
                "executor": "codex",
                "base_sha": root_base_sha,
                "failed_head_sha": first_failed_head,
                "candidate": {
                    "repairable": True,
                    "changed_files": ["OUTPUT.txt"],
                },
            }
        ),
        encoding="utf-8",
    )

    def repair(repair_id: str, failed_run_id: str, failed_head_sha: str) -> dict:
        return {
            "repair_id": repair_id,
            "failed_run_id": failed_run_id,
            "failed_head_sha": failed_head_sha,
            "task": {"id": "TASK-101", "revision": 1},
            "action": "CODE_FIX",
            "modification_scope": ["OUTPUT.txt"],
            "instructions": [f"Apply semantic decision {repair_id}."],
            "constraints": ["Commit the output."],
        }

    runner = RepairRunner(repo)

    def failing_verifier(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=1, stdout=b"", stderr=b"still failing"
        )

    with pytest.raises(OperatorError):
        run_repair(
            first_run_id,
            executor="codex",
            repo=repo,
            repair=repair("REPAIR-1", first_run_id, first_failed_head),
            native_runner=runner,
            verification_runner=failing_verifier,
        )

    continuation_run_id = "RUN-101-002"
    continuation_failure = json.loads(
        (state.failures / f"{continuation_run_id}.json").read_text(encoding="utf-8")
    )
    continuation_head = continuation_failure["failed_head_sha"]
    assert continuation_failure["continuation_of"] == first_run_id

    summary = run_repair(
        continuation_run_id,
        executor="codex",
        repo=repo,
        repair=repair("REPAIR-2", continuation_run_id, continuation_head),
        native_runner=runner,
    )

    lineage = json.loads(
        (state.repairs / f"{summary.run_id}.json").read_text(encoding="utf-8")
    )
    assert summary.run_id == "RUN-101-003"
    assert lineage["failed_run_id"] == continuation_run_id
    assert lineage["root_base_sha"] == root_base_sha
    assert runner.executions[-1]["root_base_sha"] == root_base_sha

    with pytest.raises(OperatorError, match="already been accepted"):
        run_repair(
            continuation_run_id,
            executor="codex",
            repo=repo,
            repair=repair("REPAIR-3", continuation_run_id, continuation_head),
            native_runner=runner,
        )


class ObservationClock:
    def __init__(self, *values: float) -> None:
        self.values = iter(values)

    def __call__(self) -> float:
        return next(self.values)


def test_primary_observation_uses_controlled_monotonic_phase_durations(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
        monotonic_clock=ObservationClock(10.0, 12.0, 19.0, 21.0, 24.0, 30.0),
    )

    state = runtime_paths(repo)
    observation = json.loads(
        (state.observations / f"{summary.run_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert observation["operation"] == "PRIMARY"
    assert observation["terminal_kind"] == "RESULT"
    assert observation["executor_invoked"] is True
    assert observation["durations"] == {
        "admitted_run_seconds": 20.0,
        "executor_seconds": 7.0,
        "verification_seconds": 3.0,
    }
    canonical = json.loads(summary.result_path.read_text(encoding="utf-8"))
    assert set(canonical) == {"result", "evidence"}


def test_verification_failure_observation_retains_both_available_phase_times(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)

    def failing_verifier(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=7, stdout=b"", stderr=b"failed\n"
        )

    with pytest.raises(OperatorError, match="exit code 7"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=StaticResultRunner(repo, static_payload()),
            verification_runner=failing_verifier,
            monotonic_clock=ObservationClock(1.0, 2.0, 5.0, 6.0, 10.0, 12.0),
        )

    observation = json.loads(
        (runtime_paths(repo).observations / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert observation["terminal_kind"] == "FAILURE"
    assert observation["executor_invoked"] is True
    assert observation["durations"] == {
        "admitted_run_seconds": 11.0,
        "executor_seconds": 3.0,
        "verification_seconds": 4.0,
    }


def test_post_admission_pre_executor_failure_records_not_invoked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    calls = []

    def reject_policy(*args, **kwargs):
        raise OperatorError("policy resolution failed")

    def native_runner(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("native Executor must not run")

    monkeypatch.setattr(
        operator_module, "resolve_native_execution_policy", reject_policy
    )
    with pytest.raises(OperatorError, match="policy resolution failed"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=native_runner,
            monotonic_clock=ObservationClock(3.0, 8.0),
        )

    observation = json.loads(
        (runtime_paths(repo).observations / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert calls == []
    assert observation["terminal_kind"] == "FAILURE"
    assert observation["executor_invoked"] is False
    assert observation["durations"] == {
        "admitted_run_seconds": 5.0,
        "executor_seconds": None,
        "verification_seconds": None,
    }


def test_observation_persistence_failure_does_not_repeat_executor_or_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)

    def fail_observation(*args, **kwargs):
        raise OSError("observation store unavailable")

    monkeypatch.setattr(runtime_module, "persist_observation", fail_observation)
    summary = run_task(
        "TASK-101", executor="codex", repo=repo, native_runner=runner
    )

    assert runner.count == 1
    assert summary.result_path.is_file()
    assert not list(runtime_paths(repo).observations.glob("*.json"))


def test_observation_transport_failure_preserves_result_and_does_not_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)
    calls = []

    def fail_observation_transport(*args, **kwargs):
        calls.append(kwargs.get("observation_path"))
        raise operator_module.ReviewTransportError("observation publish failed")

    monkeypatch.setattr(
        runtime_module, "transport_post_pass", fail_observation_transport
    )
    with pytest.raises(OperatorError, match="review transport failed"):
        run_task(
            "TASK-101", executor="codex", repo=repo, native_runner=runner
        )

    state = runtime_paths(repo)
    assert runner.count == 1
    assert (state.results / "RUN-101-001.json").is_file()
    assert not (state.failures / "RUN-101-001.json").exists()
    assert calls == [state.observations / "RUN-101-001.json"]


def test_retry_transport_preserves_optional_observation_sidecar(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )
    upstream = tmp_path / "upstream.git"
    artifacts_ref = f"refs/heads/aios/artifacts/{summary.run_id}"
    git(upstream, "update-ref", "-d", artifacts_ref)

    retry_transport(summary.run_id, repo=repo)

    remote_observation = git(
        upstream, "show", f"{artifacts_ref}:.ai/transport/observation.json"
    )
    local_observation = (
        runtime_paths(repo).observations / f"{summary.run_id}.json"
    ).read_text(encoding="utf-8")
    assert remote_observation == local_observation


def test_historical_remote_artifact_without_observation_remains_compatible(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )
    state = runtime_paths(repo)
    upstream = tmp_path / "upstream.git"
    artifacts_ref = f"refs/heads/aios/artifacts/{summary.run_id}"
    from aios_renew.review_transport import (
        _create_artifacts_commit,
        transport_post_pass,
    )

    legacy_commit = _create_artifacts_commit(
        repo,
        run_path=state.runs / f"{summary.run_id}.json",
        result_path=summary.result_path,
        run_id=summary.run_id,
    )
    git(
        repo,
        "push",
        "--quiet",
        "--force",
        "origin",
        f"{legacy_commit}:{artifacts_ref}",
    )

    transport_post_pass(
        repo,
        run_id=summary.run_id,
        head_sha=summary.head_sha,
        run_path=state.runs / f"{summary.run_id}.json",
        result_path=summary.result_path,
        observation_path=state.observations / f"{summary.run_id}.json",
    )

    assert git(upstream, "rev-parse", artifacts_ref) == legacy_commit


