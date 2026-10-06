import ast
import inspect
import json
import multiprocessing
import os
import shutil
import site
import subprocess
import sys
import tempfile
import tomllib
import venv
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

import aios_renew.operator as operator_module
import aios_renew.publication as publication_module
import aios_renew.runtime as runtime_module
import aios_renew.review_transport as transport_module

from aios_renew.codex_adapter import CodexAdapter
from aios_renew.review_transport import (
    RemoteFailureArtifacts,
    RemoteLifecycleReview,
    RemoteLifecycleTerminal,
    RemoteRunNamespace,
    RemoteRemediationLineage,
    RemoteRepairRecovery,
    RemoteTaskLifecycle,
)
from aios_renew.operator import (
    CorrectionPreflightResult,
    OperatorError,
    RepositoryLock,
    accept_candidate,
    describe_task,
    load_task,
    observe_unified_state,
    preflight_remediation,
    preflight_repair,
    recover_primary,
    resolve_repository,
    retry_transport,
    run_approved_remediation_intent,
    run_repair,
    run_repair_wakeup,
    run_remediation,
    run_task,
    runtime_paths,
    runtime_state_root,
)
from aios_renew.remote_surface import (
    RemoteSurfaceError,
    record_remote_approval,
    require_current_approval,
)
from aios_renew.execution_profile import (
    ResolvedExecutionProfile,
    default_execution_profile,
    load_execution_profile_policy,
    persist_execution_profile,
)


def hold_repository_lock(lock_path: str, ready, release) -> None:
    with RepositoryLock(Path(lock_path)):
        ready.set()
        release.wait()


from tests.operator_test_support import (
    MULTI_ACCEPTANCE_TASK_SOURCE,
    READONLY_MULTI_ACCEPTANCE_TASK_SOURCE,
    READONLY_TASK_SOURCE,
    TASK_SOURCE,
    canonical_result_payload,
    commit_setup_state,
    git as fixture_git,
    make_repo,
    publish_test_remediation_lineage,
    publish_upstream,
    repair_contract,
    static_payload,
)

def git(repo: Path, *args: str) -> str:
    # The shared fixture's HEAD shortcut assumes an inline .git directory.
    # Separate migration Git storage must be read through real Git instead.
    if args == ("rev-parse", "HEAD") and (repo / ".git").is_file():
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True, encoding="utf-8",
        ).strip()
    return fixture_git(repo, *args)


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


def test_repair_completion_policy_keeps_semantic_and_result_bases_distinct(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    task = load_task(repo, "TASK-101")
    semantic_root = "1" * 40
    integrated_result_base = "2" * 40
    failed_head = "3" * 40

    policy = runtime_module.repair_completion_policy(
        task,
        root_base_sha=semantic_root,
        result_base_sha=integrated_result_base,
        failed_head_sha=failed_head,
        action="CODE_FIX",
        modification_scope=("OUTPUT.txt",),
        lineage_path=tmp_path / "repair.json",
    )

    assert policy.result_base_sha == integrated_result_base
    assert policy.mutation_base_sha == failed_head
    assert policy.result_scope == task.scope.modify
    assert policy.mutation_scope == ("OUTPUT.txt",)


def test_repair_completion_policy_appends_origin_verification_once() -> None:
    task = operator_module.parse_task(TASK_SOURCE)
    reviewed_sha = "a" * 40
    execution_base = "b" * 40
    review = operator_module.parse_review(
        f"""review_id: REVIEW-101-EVIDENCE
reviewed_sha: {reviewed_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: R-EVIDENCE
    basis: AC1
    action: EVIDENCE_ONLY
    location: OUTPUT.txt
    issue: Independent evidence is required.
    expected: Collect the exact live proof.
"""
    )
    remediation = operator_module.parse_remediation(
        f"""finding_id: R-EVIDENCE
action: EVIDENCE_ONLY
reviewed_sha: {reviewed_sha}
modification_scope: []
affected_verification:
  - git status --porcelain
  - git diff --check
  - git diff --check
constraints:
  hard: [Commit the output.]
"""
    )
    execution = operator_module.RemediationExecution(
        review_id=review.review_id,
        finding=review.findings[0],
        remediation=remediation,
        run=operator_module.Run(
            run_id="RUN-101-009",
            task=operator_module.RunTaskReference(id="TASK-101", revision=1),
            executor="codex",
            base_sha=execution_base,
            workspace="bounded",
            head_sha=None,
            status="ACTIVE",
        ),
        original_constraints=remediation.constraints,
    )

    policy = runtime_module.repair_completion_policy(
        task,
        root_base_sha=reviewed_sha,
        result_base_sha=execution_base,
        failed_head_sha=execution_base,
        action="FINALIZE_CANDIDATE",
        modification_scope=(),
        lineage_path=Path("repair.json"),
        origin_affected_verification=execution.remediation.affected_verification,
    )

    assert policy.verification_commands == (
        "git status --porcelain",
        "git diff --check",
    )


def test_codex_evidence_only_prompt_binds_head_to_admitted_base() -> None:
    task = operator_module.parse_task(TASK_SOURCE)
    reviewed_sha = "a" * 40
    execution_base = "b" * 40
    review = operator_module.parse_review(
        f"""review_id: REVIEW-101-EVIDENCE
reviewed_sha: {reviewed_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: R-EVIDENCE
    basis: AC1
    action: EVIDENCE_ONLY
    location: OUTPUT.txt
    issue: Independent evidence is required.
    expected: Collect the exact live proof.
"""
    )
    remediation = operator_module.parse_remediation(
        f"""finding_id: R-EVIDENCE
action: EVIDENCE_ONLY
reviewed_sha: {reviewed_sha}
modification_scope: []
affected_verification: [git diff --check]
constraints:
  hard: [Commit the output.]
"""
    )
    execution = operator_module.RemediationExecution(
        review_id=review.review_id,
        finding=review.findings[0],
        remediation=remediation,
        run=operator_module.Run.from_task(
            run_id="RUN-101-009",
            task=task,
            executor="codex",
            base_sha=execution_base,
            workspace="bounded",
        ),
        original_constraints=remediation.constraints,
    )

    prompt = CodexAdapter.remediation_prompt_for(execution=execution)
    serialized = json.loads(prompt.split("REMEDIATION_INPUT:\n", 1)[1])

    assert "actual final Git HEAD" in prompt
    assert "unchanged admitted run.base_sha" in prompt
    assert "not remediation.reviewed_sha" in prompt
    assert serialized["run"]["base_sha"] == execution_base
    assert serialized["remediation"]["reviewed_sha"] == reviewed_sha
    assert "affected_verification" not in serialized["remediation"]


def canonical_result_payload(
    run_id: str,
    head_sha: str,
    *,
    changed_files: list[str] | None = None,
) -> dict:
    """Build a canonical package valid for PRIMARY and correction terminals."""

    commands = ("git status --porcelain", "git diff --check")
    evidence = [
        {
            "evidence_id": f"E-{run_id}-{index}",
            "run_id": run_id,
            "subject_sha": head_sha,
            "type": "TEST",
            "source": {"command": command},
            "result": {"exit_code": 0, "summary": "verified"},
            "raw": {"path": f".ai/evidence/{run_id}-{index}.log"},
        }
        for index, command in enumerate(commands, start=1)
    ]
    return {
        "result": {
            "head_sha": head_sha,
            "claims": [],
            "changed_files": [] if changed_files is None else changed_files,
            "unresolved": [],
        },
        "evidence": evidence,
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
        actual_head = commit_setup_state(
            self.repo, "OUTPUT.txt", message=f"executor {self.count}"
        )
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
        head_sha = commit_setup_state(
            self.repo,
            "OUTPUT.txt",
            message=execution["repair"]["repair_id"],
        )
        payload = result_payload(execution["run"]["run_id"], head_sha)
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=json.dumps(payload), stderr=""
        )


class HistoricalRepairRunner:
    def __init__(self, control_repo: Path | None = None) -> None:
        self.control_repo = control_repo
        self.calls = 0
        self.initial_head = None
        self.workspace = None
        self.initial_pin = None
        self.workspace_has_git_directory = False
        self.control_resolved_candidate_before_transfer = None

    def __call__(self, command, **kwargs):
        self.calls += 1
        execution = json.loads(
            kwargs["input"].decode("utf-8").split("REPAIR_INPUT:\n", 1)[1]
        )
        subject = Path(execution["run"]["workspace"])
        self.workspace = subject
        self.workspace_has_git_directory = (subject / ".git").is_dir()
        self.initial_head = git(subject, "rev-parse", "HEAD")
        self.initial_pin = (subject / "AIOS_PIN").read_text(encoding="utf-8")
        (subject / "OUTPUT.txt").write_text("historically repaired\n", encoding="utf-8")
        git(subject, "add", "OUTPUT.txt")
        git(subject, "commit", "--quiet", "-m", "historical repair")
        head_sha = git(subject, "rev-parse", "HEAD")
        if self.control_repo is not None:
            resolved = subprocess.run(
                (
                    "git",
                    "-C",
                    str(self.control_repo),
                    "cat-file",
                    "-e",
                    f"{head_sha}^{{commit}}",
                ),
                capture_output=True,
                check=False,
            )
            self.control_resolved_candidate_before_transfer = (
                resolved.returncode == 0
            )
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                result_payload(execution["run"]["run_id"], head_sha)
            ),
            stderr="",
        )


class WorkspaceRepairRunner:
    def __call__(self, command, **kwargs):
        execution = json.loads(
            kwargs["input"].decode("utf-8").split("REPAIR_INPUT:\n", 1)[1]
        )
        workspace = Path(execution["run"]["workspace"])
        repair_id = execution["repair"]["repair_id"]
        (workspace / "OUTPUT.txt").write_text(
            f"repaired by {repair_id}\n", encoding="utf-8"
        )
        git(workspace, "add", "OUTPUT.txt")
        git(workspace, "commit", "--quiet", "-m", repair_id)
        head_sha = git(workspace, "rev-parse", "HEAD")
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                result_payload(execution["run"]["run_id"], head_sha)
            ),
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
            head_sha = commit_setup_state(
                self.repo, "OUTPUT.txt", message="antigravity executor"
            )
            payload = result_payload(handoff["run"]["run_id"], head_sha)
            stdout = antigravity_envelope(payload)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=stdout,
            stderr="",
        )


class FakeAntigravityMinimaxRunner:
    def __init__(
        self,
        repo: Path,
        *,
        reported_head: str | None = None,
    ) -> None:
        self.repo = repo
        self.reported_head = reported_head
        self.calls = []
        self.count = 0

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.count += 1
        handoff_path = next(
            (self.repo / ".git" / "aios" / "handoffs").glob("*.json")
        )
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        run_id = handoff["run"]["run_id"]
        base_sha = handoff["run"]["base_sha"]
        (self.repo / "OUTPUT.txt").write_text(
            f"minimax output {self.count}\n",
            encoding="utf-8",
        )
        actual_head = commit_setup_state(
            self.repo, "OUTPUT.txt", message=f"minimax executor {self.count}"
        )
        head_sha = self.reported_head or actual_head
        payload = result_payload(run_id, head_sha)
        envelope = {
            "schema_version": "agym.result.v1",
            "agym_version": "0.4.0",
            "status": "PASS",
            "exit_code": 0,
            "model": "MiniMax-M3",
            "workspace": str(self.repo.resolve()),
            "head_before": base_sha,
            "head_after": head_sha,
            "state_guard": "PASS",
            "response": json.dumps(payload),
            "structured_response": payload,
        }
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope),
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
        if self.renames:
            # Rename-sensitive cases exercise the same real Git index/commit
            # boundary as an executor.  The fixture-only commit fast path does
            # not model deletion discovery for a directory-wide pathspec.
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "--quiet", "-m", "executor changes")
            head_sha = git(self.repo, "rev-parse", "HEAD")
        else:
            head_sha = commit_setup_state(
                self.repo, ".", message="executor changes"
            )
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


def run_authorized_remediation(*args, **kwargs):
    """Supply fixture authorization after valid lineage resolution for downstream tests."""

    resolve = operator_module._resolve_remediation_admission

    def resolve_with_authorization(*resolve_args, **resolve_kwargs):
        resolved = resolve(*resolve_args, **resolve_kwargs)
        assert resolved.remote_mode is False
        assert "remediation_authorization_sha" not in resolve_kwargs["admission"]
        resolve_kwargs["admission"]["remediation_authorization_sha"] = "a" * 40
        return resolved

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            operator_module,
            "_resolve_remediation_admission",
            resolve_with_authorization,
        )
        return run_remediation(*args, **kwargs)


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
    remediation_contract(repo)
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
        head_sha = commit_setup_state(
            self.repo, "OUTPUT.txt", message="narrow remediation"
        )
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


class IsolatedRemediationRunner:
    """Apply a remediation to the --cd subject selected by the Runtime."""

    def __init__(self, reviewed_sha: str) -> None:
        self.reviewed_sha = reviewed_sha
        self.calls = []
        self.subjects: list[Path] = []
        self.task_sources: list[str] = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        subject = Path(command[command.index("--cd") + 1])
        self.subjects.append(subject)
        assert git(subject, "rev-parse", "HEAD") == self.reviewed_sha
        self.task_sources.append(
            (subject / ".ai" / "tasks" / "TASK-101.yaml").read_text(
                encoding="utf-8"
            )
        )
        execution = json.loads(
            kwargs["input"].decode("utf-8").split("REMEDIATION_INPUT:\n", 1)[1]
        )
        assert execution["run"]["base_sha"] == self.reviewed_sha
        (subject / "OUTPUT.txt").write_text(
            "historical correction\n", encoding="utf-8"
        )
        git(subject, "add", "OUTPUT.txt")
        git(subject, "commit", "--quiet", "-m", "historical narrow remediation")
        head_sha = git(subject, "rev-parse", "HEAD")
        payload = {
            "result": {
                "head_sha": head_sha,
                "claims": [],
                "changed_files": ["OUTPUT.txt"],
                "unresolved": [],
            },
            "evidence": [],
        }
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
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

    summary = run_authorized_remediation(
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
        run_authorized_remediation(
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
        run_authorized_remediation(
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

    summary = run_authorized_remediation(
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
    diagnostic = admission_failure_records(repo)[0]
    assert diagnostic["operation"] == "DIRECT_CANDIDATE"
    assert diagnostic["phase"] == "CANONICAL_CONTRACT_ADMISSION"
    assert diagnostic["reason_code"] == "TASK_CONTRACT_REJECTED"
    assert diagnostic["executor_invoked"] is False


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
    first = run_authorized_remediation(
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

    second = run_authorized_remediation(
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
        run_authorized_remediation(
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
        "format": "AIOS_ADMISSION_FAILURE",
        "version": 2,
        "kind": "ADMISSION_FAILURE",
        "operation": "REMEDIATION",
        "requested_task_id": "TASK-101",
        "task": {"id": "TASK-101", "revision": 1},
        "requested_executor": "codex",
        "executor_invoked": False,
        "phase": "REMOTE_LINEAGE_RESOLUTION",
        "reason_code": "CANONICAL_LINEAGE_MISSING",
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
        "format": "AIOS_ADMISSION_FAILURE",
        "version": 2,
        "kind": "ADMISSION_FAILURE",
        "operation": "REMEDIATION",
        "requested_task_id": "TASK-101",
        "task": {"id": "TASK-101", "revision": 1},
        "requested_executor": executor,
        "executor_invoked": False,
        "phase": "CANONICAL_CONTRACT_ADMISSION",
        "reason_code": "TASK_CONTRACT_REJECTED",
        "source_run_id": "RUN-101-000",
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
        run_authorized_remediation(
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
        run_authorized_remediation(
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
    diagnostic = admission_failure_records(repo)[0]
    assert diagnostic["operation"] == "PRIMARY"
    assert diagnostic["requested_task_id"] == "TASK-101"
    assert diagnostic["executor_invoked"] is False


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
    diagnostic = admission_failure_records(repo)[0]
    assert diagnostic["operation"] == "REPAIR"
    assert diagnostic["failed_run_id"] == failed_run_id
    assert diagnostic["executor_invoked"] is False


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


def test_recover_primary_pre_run_rejection_has_no_executor_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)

    def reject_before_run(*args, **kwargs):
        assert kwargs["attempt"].run_path is None
        raise OperatorError("recovery rejected before RUN creation")

    monkeypatch.setattr(operator_module, "_recover_primary_impl", reject_before_run)

    with pytest.raises(OperatorError, match="recovery rejected"):
        recover_primary("RUN-101-001", repo=repo)

    diagnostic = admission_failure_records(repo)[0]
    assert diagnostic["operation"] == "RECOVER_PRIMARY"
    assert diagnostic["source_run_id"] == "RUN-101-001"
    assert diagnostic["executor_invoked"] is False
    assert "requested_executor" not in diagnostic
    assert not list(state.runs.glob("RUN-101-*.json"))


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


def test_admission_diagnostic_binds_ref_sha_observed_before_later_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    publish_direct_candidate_lineage(repo, tmp_path)
    upstream = tmp_path / "upstream.git"
    ref = "refs/heads/aios/remediation/RUN-101-000-R1"
    observed_sha = git(repo, "ls-remote", "origin", ref).split()[0]
    replacement_sha = git(repo, "rev-parse", "HEAD")

    def reject_after_observation(*args, **kwargs):
        git(upstream, "update-ref", ref, replacement_sha)
        raise OperatorError("canonical contract rejected after ref observation")

    monkeypatch.setattr(
        operator_module, "_parse_remote_direct_lineage_impl", reject_after_observation
    )
    with pytest.raises(OperatorError, match="after ref observation"):
        run_remediation(
            "TASK-101", finding_id="R1", executor="codex", repo=repo
        )

    record = admission_failure_records(repo)[0]
    assert record["observed_ref"] == ref
    assert record["observed_sha"] == observed_sha
    assert git(repo, "ls-remote", "origin", ref).split()[0] == replacement_sha
    assert record["observed_sha"] != replacement_sha


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


def test_admission_local_persistence_failure_preserves_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    real_write_bytes = Path.write_bytes

    def reject_diagnostic_write(path: Path, content: bytes) -> int:
        if path.parent.name == "admission-failures":
            raise OSError("diagnostic persistence unavailable")
        return real_write_bytes(path, content)

    monkeypatch.setattr(Path, "write_bytes", reject_diagnostic_write)
    with pytest.raises(
        OperatorError, match="canonical remote remediation lineage not found"
    ):
        run_remediation(
            "TASK-101", finding_id="R1", executor="codex", repo=repo
        )

    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not admission_failure_records(repo)


def test_remote_query_failure_is_distinct_from_successful_missing_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)

    def unavailable(*args, **kwargs):
        raise operator_module.RemoteQueryError(
            "canonical ref query unavailable", category="CONNECTIVITY"
        )

    monkeypatch.setattr(
        operator_module, "resolve_remote_remediation_lineages", unavailable
    )
    with pytest.raises(OperatorError, match="lineage resolution failed"):
        run_remediation(
            "TASK-101", finding_id="R1", executor="codex", repo=repo
        )

    record = admission_failure_records(repo)[0]
    assert record["reason_code"] == "REMOTE_TRANSPORT_UNAVAILABLE"
    assert record["remote_query"] == {
        "operation": "LS_REMOTE",
        "outcome": "UNAVAILABLE",
        "category": "CONNECTIVITY",
    }
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


def test_primary_fast_forwards_before_task_load_and_binds_synchronized_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path)
    local_sha = git(repo, "rev-parse", "HEAD")
    upstream = git(
        repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    published_sha = publish_upstream(
        repo, {"UPSTREAM_DOC.txt": "upstream doc\n"}, "advance upstream"
    )
    runner = FakeCodexRunner(repo)
    git_calls = []
    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", recording_git)

    exit_code = operator_module.main(
        ["run", "TASK-101", "--executor", "codex", "--repo", str(repo)],
        native_runner=runner,
    )
    assert exit_code == 0

    canonical = json.loads(
        runner.calls[0][1]["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
    )
    assert canonical["run"]["base_sha"] == published_sha
    assert canonical["task"]["task_id"] == "TASK-101"
    ff_calls = [
        args for args in git_calls if args and args[0] == "merge" and "--ff-only" in args
    ]
    assert len(ff_calls) == 1
    assert ("merge", "--ff-only", upstream) in git_calls
    prohibited = {
        "read-tree",
        "update-ref",
        "rebase",
        "reset",
        "checkout",
        "stash",
        "clean",
        "pull",
        "push",
        "revert",
    }
    assert not any(args and args[0] in prohibited for args in git_calls)
    assert git(repo, "rev-list", "--merges", f"{local_sha}..HEAD") == ""


def test_primary_sync_upstream_race_between_preflight_and_admission_does_not_reintegrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path)
    local_sha = git(repo, "rev-parse", "HEAD")
    upstream = git(
        repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    published_sha_1 = publish_upstream(
        repo, {"NOTE1.txt": "first upstream note\n"}, "publish upstream 1"
    )
    runner = FakeCodexRunner(repo)
    git_calls = []
    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", recording_git)

    raced_sha = None
    real_run_task = operator_module.run_task

    def racing_run_task(*args, **kwargs):
        nonlocal raced_sha
        raced_sha = publish_upstream(
            repo, {"NOTE2.txt": "second upstream note (race)\n"}, "publish upstream 2 (race)"
        )
        return real_run_task(*args, **kwargs)

    monkeypatch.setattr(operator_module, "run_task", racing_run_task)

    exit_code = operator_module.main(
        ["run", "TASK-101", "--executor", "codex", "--repo", str(repo)],
        native_runner=runner,
    )
    assert exit_code == 0
    assert raced_sha is not None
    canonical = json.loads(
        runner.calls[0][1]["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
    )
    assert canonical["run"]["base_sha"] == published_sha_1
    assert canonical["run"]["base_sha"] != raced_sha
    ff_calls = [
        args for args in git_calls if args and args[0] == "merge" and "--ff-only" in args
    ]
    assert len(ff_calls) == 1
    assert ("merge", "--ff-only", upstream) in git_calls


@pytest.mark.parametrize(
    ("mutation", "error_match"),
    [
        (
            lambda repo: git(repo, "commit", "--allow-empty", "-m", "intervening local commit"),
            "current HEAD does not match preflight state",
        ),
        (
            lambda repo: (repo / "DIRTY.txt").write_text("dirty\n", encoding="utf-8"),
            "repository dirty",
        ),
        (
            lambda repo: git(repo, "checkout", "--detach"),
            "repository HEAD is detached",
        ),
        (
            lambda repo: git(repo, "checkout", "-b", "other-branch"),
            "current branch is not main",
        ),
    ],
)
def test_primary_sync_local_mutation_between_preflight_and_admission_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutation,
    error_match: str,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path)
    publish_upstream(
        repo, {"UPSTREAM_DOC.txt": "upstream doc\n"}, "advance upstream"
    )
    runner = FakeCodexRunner(repo)
    real_run_task = operator_module.run_task

    def mutating_run_task(*args, **kwargs):
        mutation(repo)
        return real_run_task(*args, **kwargs)

    monkeypatch.setattr(operator_module, "run_task", mutating_run_task)

    exit_code = operator_module.main(
        ["run", "TASK-101", "--executor", "codex", "--repo", str(repo)],
        native_runner=runner,
    )
    assert exit_code == 1
    assert error_match in capsys.readouterr().err
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(runner.calls) == 0


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
    assert not any(
        args and args[0] in ("merge", "read-tree", "update-ref", "rebase", "reset")
        for args in git_calls
    )
    assert git(repo, "status", "--porcelain") == ""
    canonical = json.loads(
        runner.calls[0][1]["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
    )
    assert canonical["run"]["base_sha"] == local_sha


@pytest.mark.parametrize(
    "state",
    [
        "detached",
        "non-main",
        "missing-upstream",
        "ambiguous-upstream",
        "upstream-not-main",
        "dirty",
        "ahead",
        "diverged",
    ],
)
def test_unsafe_primary_git_states_fail_before_executor(
    tmp_path: Path, state: str, monkeypatch: pytest.MonkeyPatch
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
    elif state == "dirty":
        (repo / "DIRTY.txt").write_text("dirty\n", encoding="utf-8")
    elif state == "ahead":
        (repo / "LOCAL.txt").write_text("local\n", encoding="utf-8")
        git(repo, "add", "LOCAL.txt")
        git(repo, "commit", "--quiet", "-m", "local")
    else:
        publish_upstream(repo, {"REMOTE.txt": "remote\n"}, "remote")
        (repo / "LOCAL.txt").write_text("local\n", encoding="utf-8")
        git(repo, "add", "LOCAL.txt")
        git(repo, "commit", "--quiet", "-m", "local")

    git_calls = []
    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", recording_git)

    def runner(command, **kwargs):
        raise AssertionError("executor must not be invoked")

    with pytest.raises(OperatorError):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert not list(runtime_paths(repo).runs.glob("*.json"))
    prohibited = {
        "merge",
        "read-tree",
        "update-ref",
        "rebase",
        "reset",
        "checkout",
        "stash",
        "clean",
    }
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_fetch_failure_fails_before_run_persistence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    def runner(command, **kwargs):
        raise AssertionError("executor must not be invoked")

    with pytest.raises(OperatorError, match="upstream fetch failed"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=runner,
        )

    assert not list(runtime_paths(repo).runs.glob("*.json"))
    diagnostic = admission_failure_records(repo)[0]
    assert diagnostic["operation"] == "PRIMARY"
    assert diagnostic["phase"] == "PRIMARY_SYNCHRONIZATION"
    assert diagnostic["reason_code"] == "REMOTE_TRANSPORT_UNAVAILABLE"


def test_wakeup_preflight_admission_failure_retains_dispatch_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)

    def reject_preflight(*args, **kwargs):
        raise OperatorError("wakeup preflight rejected")

    monkeypatch.setattr(operator_module, "_preflight_primary_sync", reject_preflight)

    with pytest.raises(OperatorError, match="wakeup preflight rejected"):
        operator_module._preflight_primary_admission(
            repo,
            task_id="TASK-101",
            executor="codex",
            dispatch_id="delivery-081",
        )

    diagnostic = admission_failure_records(repo)[0]
    assert diagnostic["operation"] == "PRIMARY"
    assert diagnostic["dispatch_id"] == "delivery-081"
    assert diagnostic["executor_invoked"] is False
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_synchronization_failure_with_safe_state_produces_admission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path)
    local_sha = git(repo, "rev-parse", "HEAD")
    published_sha = publish_upstream(
        repo, {"UPSTREAM_DOC.txt": "upstream doc\n"}, "advance upstream"
    )
    git_calls = []
    real_git = operator_module._git

    def failing_git(root, *args, **kwargs):
        git_calls.append(args)
        if args and args[0] == "merge" and "--ff-only" in args:
            raise OperatorError("simulated native fast-forward failure")
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", failing_git)
    runner = FakeCodexRunner(repo)

    with pytest.raises(OperatorError, match="upstream fast-forward failed"):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert git(repo, "rev-parse", "HEAD") == local_sha
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(repo, "status", "--porcelain") == ""
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(runner.calls) == 0

    ff_calls = [
        args for args in git_calls if args and args[0] == "merge" and "--ff-only" in args
    ]
    assert len(ff_calls) == 1

    prohibited = {
        "read-tree",
        "update-ref",
        "rebase",
        "reset",
        "checkout",
        "stash",
        "clean",
        "pull",
        "push",
        "revert",
    }
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_synchronization_failure_with_unsafe_state_produces_blocked_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path)
    local_sha = git(repo, "rev-parse", "HEAD")
    published_sha = publish_upstream(
        repo, {"UPSTREAM_DOC.txt": "upstream doc\n"}, "advance upstream"
    )
    git_calls = []
    real_git = operator_module._git

    def failing_and_corrupting_git(root, *args, **kwargs):
        git_calls.append(args)
        if args and args[0] == "merge" and "--ff-only" in args:
            (root / "CORRUPT.txt").write_text("corrupted\n", encoding="utf-8")
            raise OperatorError("simulated partial merge failure")
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", failing_and_corrupting_git)
    runner = FakeCodexRunner(repo)

    with pytest.raises(OperatorError, match="repository-integrity BLOCKED"):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(runner.calls) == 0

    ff_calls = [
        args for args in git_calls if args and args[0] == "merge" and "--ff-only" in args
    ]
    assert len(ff_calls) == 1

    prohibited = {
        "read-tree",
        "update-ref",
        "rebase",
        "reset",
        "checkout",
        "stash",
        "clean",
        "pull",
        "push",
        "revert",
    }
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_primary_sync_advancing_source_and_task_restarts_and_consumes_canonical_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
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


def test_primary_sync_advancing_task_only_restarts_and_consumes_canonical_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path, task_source=None)
    local_sha = git(repo, "rev-parse", "HEAD")
    task_103_source = TASK_SOURCE.replace("TASK-101", "TASK-103")
    published_sha = publish_upstream(
        repo,
        {
            ".ai/tasks/TASK-103.yaml": task_103_source,
        },
        "publish task only",
    )
    runner = FakeCodexRunner(repo)

    with pytest.raises(
        OperatorError, match="cannot continue under stale pre-sync kernel state"
    ):
        run_task("TASK-103", executor="codex", repo=repo, native_runner=runner)
    assert git(repo, "rev-parse", "HEAD") == local_sha
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(runner.calls) == 0

    restarts = []

    def fake_restart(root, *, argv=None, runner=subprocess.run):
        restarts.append((root, argv))
        return 0

    monkeypatch.setattr(operator_module, "_restart_primary_invocation", fake_restart)
    exit_code = operator_module.main(
        ["run", "TASK-103", "--executor", "codex", "--repo", str(repo)]
    )
    assert exit_code == 0
    assert git(repo, "rev-parse", "HEAD") == published_sha
    assert len(restarts) == 1

    summary = run_task("TASK-103", executor="codex", repo=repo, native_runner=runner)
    assert summary.base_sha == published_sha
    assert summary.task_id == "TASK-103"
    canonical = json.loads(
        runner.calls[0][1]["input"].decode("utf-8").split("CANONICAL_INPUT:\n", 1)[1]
    )
    assert canonical["run"]["base_sha"] == published_sha
    assert canonical["task"]["task_id"] == "TASK-103"


def test_remediation_repair_and_candidate_do_not_auto_sync_upstream_main(
    tmp_path: Path,
) -> None:
    repo_rem = make_repo(tmp_path / "rem")
    review, remediation = remediation_contract(repo_rem)
    reviewed_sha = remediation.reviewed_sha
    upstream_rem_sha = publish_upstream(
        repo_rem, {"ADVANCE.txt": "advance\n"}, "advance upstream main"
    )
    summary_rem = run_authorized_remediation(
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
        verification_runner=lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout=b"pass", stderr=b""
        ),
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
        run_authorized_remediation(
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
    profile = default_execution_profile("antigravity", "AUTHORIZATION", repo)

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
    assert command[command.index("--effort") + 1] == profile.reasoning_effort
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
    assert command[command.index("--model") + 1] == profile.model


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
    assert "finish tool exactly once" in instruction
    assert "only successful terminal action" in instruction
    assert "Conversational completion prose" in instruction
    assert "Runtime captures" in instruction
    assert "Runtime-owned operational state" in instruction
    assert "Runtime owns canonical verification" in instruction
    assert "do not execute canonical verification commands" in instruction
    assert (
        "Complete all authorized implementation work and required commit completion first"
        in instruction
    )
    assert "zero-mutation actions must not create a commit" in instruction
    assert "Do not push" in instruction
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
    assert "--mode" not in command
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
        "native_diagnostics": {
            "limit_chars": 4096,
            "stdout": {"availability": "empty"},
            "stderr": {
                "availability": "captured",
                "text": "request timed out after 300s\nraw executor transcript\n",
                "truncated": False,
            },
        },
    }
    assert "raw executor transcript" in json.dumps(failure)
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


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_ordinary_provider_failure_retains_bounded_native_diagnostics(
    tmp_path: Path, executor: str
) -> None:
    repo = make_repo(tmp_path)
    stdout = b"progress:" + b"x" * 5000
    stderr = b"provider UNAVAILABLE 503"

    def provider_failure(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=1, stdout=stdout, stderr=stderr
        )

    with pytest.raises(OperatorError, match="503"):
        run_task(
            "TASK-101",
            executor=executor,
            repo=repo,
            native_runner=provider_failure,
        )

    failure = json.loads(
        (runtime_paths(repo).failures / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    diagnostics = failure["error"]["native_diagnostics"]
    assert diagnostics["limit_chars"] == 4096
    assert diagnostics["stdout"] == {
        "availability": "captured",
        "text": stdout.decode()[:4096],
        "truncated": True,
    }
    assert diagnostics["stderr"] == {
        "availability": "captured",
        "text": "provider UNAVAILABLE 503",
        "truncated": False,
    }


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
def test_successful_verification_subject_mutation_fails_closed(
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
    subjects = []

    def mutating_verification(command, **kwargs):
        subject = kwargs["cwd"]
        subjects.append(subject)
        heads_before_mutation.append(git(subject, "rev-parse", "HEAD"))
        if mutation == "dirty":
            (subject / "VERIFICATION_DIRTY.txt").write_text(
                "verification mutation\n", encoding="utf-8"
            )
        else:
            (subject / "VERIFICATION_COMMIT.txt").write_text(
                "verification mutation\n", encoding="utf-8"
            )
            git(subject, "add", "VERIFICATION_COMMIT.txt")
            git(
                subject,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "verification mutation",
            )
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
            run_authorized_remediation(
                "TASK-101",
                review=review,
                remediation=remediation,
                executor="codex",
                repo=repo,
                native_runner=RemediationRunner(repo),
                verification_runner=mutating_verification,
            )

    assert not (state.results / "RUN-101-001.json").exists()
    assert len(subjects) == 1
    assert not subjects[0].exists()
    assert not (repo / "VERIFICATION_DIRTY.txt").exists()
    assert not (repo / "VERIFICATION_COMMIT.txt").exists()
    assert git(repo, "status", "--porcelain") == ""
    failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert failure["phase"] == "VERIFICATION"
    assert failure["failed_head_sha"] == heads_before_mutation[0]


def test_control_head_movement_does_not_change_exact_verification_subject(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    verification_subjects = []
    candidate_sha = None

    def move_control_after_materialization(command, **kwargs):
        nonlocal candidate_sha
        subject = kwargs["cwd"]
        verification_subjects.append(subject)
        observed_subject = git(subject, "rev-parse", "HEAD")
        if candidate_sha is None:
            candidate_sha = observed_subject
            git(repo, "reset", "--quiet", "--hard", f"{candidate_sha}^")
        assert git(subject, "rev-parse", "HEAD") == candidate_sha
        assert git(subject, "status", "--porcelain") == ""
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=b"passed\n", stderr=b""
        )

    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=CommitResultRunner(
            repo,
            writes={"OUTPUT.txt": "candidate\n"},
            changed_files=["OUTPUT.txt"],
        ),
        verification_runner=move_control_after_materialization,
    )

    assert candidate_sha is not None
    assert summary.head_sha == candidate_sha
    assert git(repo, "rev-parse", "HEAD") != candidate_sha
    assert len(set(verification_subjects)) == 1
    assert not verification_subjects[0].exists()
    stored = json.loads(summary.result_path.read_text(encoding="utf-8"))
    assert {item["subject_sha"] for item in stored["evidence"]} == {candidate_sha}


def test_direct_candidate_verification_failure_stays_bound_to_candidate_after_control_moves(
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
    state = runtime_paths(repo)
    verification_subjects = []
    moved_head = None

    def move_control_and_fail_verification(command, **kwargs):
        nonlocal moved_head
        subject = kwargs["cwd"]
        verification_subjects.append(subject)
        assert git(subject, "rev-parse", "HEAD") == candidate_head
        assert git(subject, "status", "--porcelain") == ""
        git(repo, "reset", "--quiet", "--hard", f"{candidate_head}^")
        (repo / "DIVERGENT.txt").write_text("divergent\n", encoding="utf-8")
        git(repo, "add", "DIVERGENT.txt")
        git(repo, "commit", "--quiet", "-m", "divergent control head")
        moved_head = git(repo, "rev-parse", "HEAD")
        return subprocess.CompletedProcess(
            command, returncode=1, stdout=b"", stderr=b"direct candidate verification failed\n"
        )

    with pytest.raises(OperatorError, match="verification command failed"):
        accept_candidate(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            verification_runner=move_control_and_fail_verification,
        )

    assert moved_head is not None
    assert git(repo, "rev-parse", "HEAD") == moved_head
    assert moved_head != candidate_head
    assert len(set(verification_subjects)) == 1
    assert not verification_subjects[0].exists()
    assert not (state.results / "RUN-101-001.json").exists()

    failure_path = state.failures / "RUN-101-001.json"
    assert failure_path.is_file()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["phase"] == "VERIFICATION"
    assert failure["failed_head_sha"] == candidate_head
    assert failure["failed_head_sha"] != moved_head
    assert failure["candidate"]["changed_files"] == ["OUTPUT.txt"]
    assert failure["candidate"]["changed_files"] != ["DIVERGENT.txt"]
    assert failure["candidate"]["dirty"] is False


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
    summary = run_authorized_remediation(
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


def test_continue_implementation_repair_binds_exact_failed_run_and_preserves_runtime_boundary(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(
        repo, action="CONTINUE_IMPLEMENTATION"
    )
    failed_head = repair["failed_head_sha"]
    sequence = []
    executions = []

    def native_runner(command, **kwargs):
        sequence.append("executor")
        prompt = kwargs["input"].decode("utf-8")
        execution = json.loads(prompt.split("REPAIR_INPUT:\n", 1)[1])
        executions.append(execution)
        assert execution["execution_context"]["operation"] == "REPAIR"
        assert execution["execution_context"]["selected_executor"] == "codex"
        assert execution["failed_run_id"] == failed_run_id
        assert execution["failed_head_sha"] == failed_head
        assert execution["failure"]["phase"] == "COMPLETION_GATE"
        assert execution["run"]["base_sha"] == failed_head
        assert execution["repair"]["action"] == "CONTINUE_IMPLEMENTATION"
        assert execution["repair"]["modification_scope"] == ["OUTPUT.txt"]
        assert (
            "necessary bounded repository inspection, discovery, or live capture" in prompt
        )

        (repo / "OUTPUT.txt").write_text(
            "continued after bounded capture\n", encoding="utf-8"
        )
        git(repo, "add", "OUTPUT.txt")
        git(repo, "commit", "--quiet", "-m", "continue unfinished implementation")
        head_sha = git(repo, "rev-parse", "HEAD")
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(result_payload("RUN-101-001", head_sha)),
            stderr="",
        )

    def verification_runner(command, **kwargs):
        sequence.append("verification")
        assert len(executions) == 1
        assert git(repo, "rev-parse", "HEAD") != failed_head
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=b"clean\n", stderr=b""
        )

    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair=repair,
        native_runner=native_runner,
        verification_runner=verification_runner,
    )

    state = runtime_paths(repo)
    repair_execution = json.loads(
        (state.repairs / f"{summary.run_id}.json").read_text(encoding="utf-8")
    )
    observation = json.loads(
        (state.observations / f"{summary.run_id}.json").read_text(encoding="utf-8")
    )
    assert sequence == ["executor", "verification"]
    assert len(executions) == 1
    assert summary.run_id == "RUN-101-001"
    assert summary.failed_head_sha == failed_head
    assert repair_execution["failed_run_id"] == failed_run_id
    assert repair_execution["failed_head_sha"] == failed_head
    assert repair_execution["repair"] == repair
    assert observation["operation"] == "REPAIR"
    assert observation["executor_invoked"] is True
    assert observation["durations"]["verification_seconds"] is not None
    assert sorted(path.name for path in state.runs.glob("*.json")) == [
        "RUN-101-000.json",
        "RUN-101-001.json",
    ]


@pytest.mark.parametrize("executor", ["codex", "antigravity"])
def test_finalize_candidate_recovers_structural_package_without_mutation(
    tmp_path: Path, executor: str
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    root_base_sha = git(repo, "rev-parse", "HEAD")
    (repo / "OUTPUT.txt").write_text("complete candidate\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "complete candidate before signal loss")
    failed_head = git(repo, "rev-parse", "HEAD")
    failed_run_id = "RUN-101-000"
    failed_run = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": executor,
        "base_sha": root_base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": executor,
        "base_sha": root_base_sha,
        "failed_head_sha": failed_head,
        "phase": "EXECUTION",
        "error": {"type": "AntigravityExecutionError", "message": "503"},
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["OUTPUT.txt"],
            "outside_task_scope": [],
        },
    }
    (state.runs / f"{failed_run_id}.json").write_text(
        json.dumps(failed_run), encoding="utf-8"
    )
    (state.failures / f"{failed_run_id}.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )
    repair = {
        "repair_id": "REPAIR-101-FINALIZE",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "FINALIZE_CANDIDATE",
        "modification_scope": [],
        "instructions": ["Inspect the unchanged candidate and return its package."],
        "constraints": ["Commit the output."],
    }
    sequence = []

    def native_runner(command, **kwargs):
        sequence.append("executor")
        assert git(repo, "rev-parse", "HEAD") == failed_head
        if executor == "codex":
            assert command[command.index("--sandbox") + 1] == "read-only"
            execution = json.loads(
                kwargs["input"].decode().split("REPAIR_INPUT:\n", 1)[1]
            )
        else:
            assert "--mode" not in command
            assert "--dangerously-skip-permissions" not in command
            execution = json.loads(
                next(state.handoffs.glob("*.json")).read_text(encoding="utf-8")
            )
        assert execution["failed_run_id"] == failed_run_id
        assert execution["failed_head_sha"] == failed_head
        assert execution["repair"]["action"] == "FINALIZE_CANDIDATE"
        payload = result_payload(
            execution["run"]["run_id"], failed_head, changed_files=[]
        )
        stdout = (
            antigravity_envelope(payload)
            if executor == "antigravity"
            else json.dumps(payload)
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    def verification_runner(command, **kwargs):
        sequence.append("verification")
        assert git(repo, "rev-parse", "HEAD") == failed_head
        return subprocess.CompletedProcess(command, 0, stdout=b"clean\n", stderr=b"")

    summary = run_repair(
        failed_run_id,
        executor=executor,
        repo=repo,
        repair=repair,
        native_runner=native_runner,
        verification_runner=verification_runner,
    )

    result = json.loads(summary.result_path.read_text(encoding="utf-8"))
    observation = json.loads(
        (state.observations / f"{summary.run_id}.json").read_text(encoding="utf-8")
    )
    assert sequence == ["executor", "verification"]
    assert summary.head_sha == failed_head == git(repo, "rev-parse", "HEAD")
    assert not (state.preverification / f"{failed_run_id}.json").exists()
    assert result["result"]["changed_files"] == ["OUTPUT.txt"]
    assert {item["subject_sha"] for item in result["evidence"]} == {failed_head}
    assert observation["executor_invoked"] is True


def test_finalize_candidate_incomplete_package_terminalizes_without_verification(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(
        repo, action="CONTINUE_IMPLEMENTATION"
    )
    repair["action"] = "FINALIZE_CANDIDATE"
    repair["modification_scope"] = []
    runner = StaticRepairRunner(repo, unresolved=["candidate is incomplete"])
    verification_calls = []

    with pytest.raises(OperatorError, match="unresolved"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair,
            native_runner=runner,
            verification_runner=lambda *args, **kwargs: verification_calls.append(args),
        )

    assert len(runner.calls) == 1
    assert verification_calls == []
    assert (runtime_paths(repo).failures / "RUN-101-001.json").is_file()


def test_no_change_verification_only_continuations_reuse_exact_candidate(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)
    state = runtime_paths(repo)
    verification_calls = []

    def fail_verification(command, *, cwd, env, capture_output, text, check):
        verification_calls.append((command, cwd, env, capture_output, text, check))
        return subprocess.CompletedProcess(
            command, returncode=9, stdout=b"", stderr=b"external unavailable\n"
        )

    with pytest.raises(OperatorError, match="exit code 9"):
        run_task(
            "TASK-101",
            executor="antigravity",
            repo=repo,
            native_runner=StaticResultRunner(repo, static_payload()),
            verification_runner=fail_verification,
        )

    first_sidecar = state.preverification / "RUN-101-001.json"
    first_failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert first_sidecar.is_file()
    assert first_failure["phase"] == "VERIFICATION"
    assert first_failure["executor"] == "antigravity"
    assert git(
        tmp_path / "upstream.git",
        "show",
        "refs/heads/aios/failure-artifacts/RUN-101-001:"
        ".ai/transport/pre-verification-candidate.json",
    ).encode() == first_sidecar.read_bytes()

    def authorization(run_id: str) -> dict:
        failure = json.loads(
            (state.failures / f"{run_id}.json").read_text(encoding="utf-8")
        )
        return {
            "repair_id": f"REPAIR-{run_id}",
            "failed_run_id": run_id,
            "failed_head_sha": failure["failed_head_sha"],
            "task": {"id": "TASK-101", "revision": 1},
            "action": "NO_CHANGE",
            "modification_scope": [],
            "instructions": ["Re-run canonical verification only."],
            "constraints": ["Commit the output."],
        }

    native_calls = []

    def forbidden_native(*args, **kwargs):
        native_calls.append((args, kwargs))
        raise AssertionError("verification-only continuation invoked an Executor")

    eligible = preflight_repair(
        "RUN-101-001", repo=repo, repair=authorization("RUN-101-001")
    )
    assert eligible.status == "READY"
    assert eligible.executor_required is False

    with pytest.raises(OperatorError, match="exit code 9"):
        run_repair(
            "RUN-101-001",
            executor=None,
            repo=repo,
            repair=authorization("RUN-101-001"),
            native_runner=forbidden_native,
            verification_runner=fail_verification,
        )

    repeated_sidecar = state.preverification / "RUN-101-002.json"
    repeated_failure = json.loads(
        (state.failures / "RUN-101-002.json").read_text(encoding="utf-8")
    )
    failed_observation = json.loads(
        (state.observations / "RUN-101-002.json").read_text(encoding="utf-8")
    )
    assert native_calls == []
    assert len(verification_calls) == 2
    assert repeated_failure["continuation_of"] == "RUN-101-001"
    assert repeated_failure["executor"] == "antigravity"
    assert repeated_failure["failed_head_sha"] == first_failure["failed_head_sha"]
    assert repeated_sidecar.is_file()
    assert failed_observation["executor_invoked"] is False
    assert failed_observation["durations"]["executor_seconds"] is None
    assert failed_observation["durations"]["verification_seconds"] is not None

    success_calls = []

    def pass_verification(command, *, cwd, env, capture_output, text, check):
        success_calls.append((command, cwd, env, capture_output, text, check))
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=b"clean\n", stderr=b""
        )

    summary = run_repair(
        "RUN-101-002",
        executor=None,
        repo=repo,
        repair=authorization("RUN-101-002"),
        native_runner=forbidden_native,
        verification_runner=pass_verification,
    )
    result = json.loads(summary.result_path.read_text(encoding="utf-8"))
    success_observation = json.loads(
        (state.observations / summary.result_path.name).read_text(encoding="utf-8")
    )
    assert summary.run_id == "RUN-101-003"
    assert summary.executor == "antigravity"
    assert native_calls == []
    assert len(success_calls) == 1
    _, verification_cwd, _, capture_output, text, check = success_calls[0]
    assert verification_cwd != repo.resolve()
    assert not verification_cwd.exists()
    assert capture_output is True
    assert text is False
    assert check is False
    assert result["result"]["head_sha"] == first_failure["failed_head_sha"]
    assert result["result"]["changed_files"] == []
    assert result["evidence"][0]["run_id"] == summary.run_id
    assert result["evidence"][0]["subject_sha"] == summary.head_sha
    assert success_observation["executor_invoked"] is False
    assert success_observation["durations"]["executor_seconds"] is None
    assert success_observation["durations"]["verification_seconds"] is not None


def test_malformed_present_preverification_candidate_fails_before_continuation(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(repo, action="NO_CHANGE")
    state = runtime_paths(repo)
    (state.preverification / f"{failed_run_id}.json").write_bytes(b"{malformed")
    calls = []

    with pytest.raises(OperatorError, match="invalid pre-verification candidate"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair,
            native_runner=lambda *args, **kwargs: calls.append("executor"),
            verification_runner=lambda *args, **kwargs: calls.append("verification"),
        )

    assert calls == []
    assert not (state.runs / "RUN-101-001.json").exists()


def test_retry_failure_transport_preserves_preverification_sidecar(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, task_source=READONLY_TASK_SOURCE)

    def fail_verification(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=3, stdout=b"", stderr=b"temporary failure\n"
        )

    with pytest.raises(OperatorError, match="exit code 3"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=StaticResultRunner(repo, static_payload()),
            verification_runner=fail_verification,
        )

    state = runtime_paths(repo)
    sidecar = state.preverification / "RUN-101-001.json"
    upstream = tmp_path / "upstream.git"
    git(upstream, "update-ref", "-d", "refs/heads/aios/failure/RUN-101-001")
    git(
        upstream,
        "update-ref",
        "-d",
        "refs/heads/aios/failure-artifacts/RUN-101-001",
    )

    retry_transport("RUN-101-001", repo=repo)

    assert git(
        upstream,
        "show",
        "refs/heads/aios/failure-artifacts/RUN-101-001:"
        ".ai/transport/pre-verification-candidate.json",
    ).encode() == sidecar.read_bytes()


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
    transported_lineage = git(
        tmp_path / "upstream.git",
        "show",
        "refs/heads/aios/failure-artifacts/RUN-101-002:"
        ".ai/transport/repair.json",
    )
    persisted_lineage = (state.repairs / "RUN-101-002.json").read_text(
        encoding="utf-8"
    ).strip()
    assert transported_lineage == persisted_lineage

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


def test_historical_repair_isolates_subject_and_preserves_control_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    (repo / "AIOS_PIN").write_text("old-runtime\n", encoding="utf-8")
    git(repo, "add", "AIOS_PIN")
    git(repo, "commit", "--quiet", "-m", "historical runtime pin")
    root_base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "--quiet", "-c", "historical-failure")
    (repo / "OUTPUT.txt").write_text("failed historical output\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "historical failed candidate")
    failed_head = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "--quiet", "main")
    (repo / "AIOS_PIN").write_text("new-control-runtime\n", encoding="utf-8")
    (repo / "CONTROL.txt").write_text("newer runtime control\n", encoding="utf-8")
    git(repo, "add", "AIOS_PIN", "CONTROL.txt")
    git(repo, "commit", "--quiet", "-m", "newer control main")
    control_head = git(repo, "rev-parse", "HEAD")

    failed_run_id = "RUN-101-004"
    remote_run = {
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
        json.dumps(remote_run).encode(),
        json.dumps(failure).encode(),
        None,
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_repair_recovery",
        lambda repo, *, failed_run_id: RemoteRepairRecovery(
            (artifact,), ("RUN-101-004", "RUN-101-009")
        ),
    )
    monkeypatch.setattr(
        operator_module,
        "read_remote_task",
        lambda repo, *, commit_sha, task_id: TASK_SOURCE.encode(),
    )
    repair = {
        "repair_id": "REPAIR-101-HISTORICAL",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Correct the historical candidate only."],
        "constraints": ["Commit the output."],
    }
    runner = HistoricalRepairRunner(repo)

    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair=repair,
        native_runner=runner,
    )

    assert summary.run_id == "RUN-101-010"
    assert runner.calls == 1
    assert runner.initial_head == failed_head
    assert runner.initial_pin == "old-runtime\n"
    assert runner.workspace_has_git_directory is True
    assert runner.control_resolved_candidate_before_transfer is False
    assert runner.workspace is not None and not runner.workspace.exists()
    assert git(repo, "rev-parse", "HEAD") == control_head
    assert git(repo, "branch", "--show-current") == "main"
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / "AIOS_PIN").read_text(encoding="utf-8") == "new-control-runtime\n"
    assert git(repo, "show", f"{summary.head_sha}:AIOS_PIN") == "old-runtime"
    git(repo, "merge-base", "--is-ancestor", failed_head, summary.head_sha)
    assert git(repo, "rev-list", "--count", f"{failed_head}..{summary.head_sha}") == "1"
    upstream = tmp_path / "upstream.git"
    review_ref = f"refs/heads/aios/review/{summary.run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{summary.run_id}"
    assert git(upstream, "rev-parse", review_ref) == summary.head_sha
    assert git(
        upstream, "show", f"{artifacts_ref}:.ai/transport/result.json"
    ) == summary.result_path.read_text(encoding="utf-8")


def test_remote_remediation_executes_historical_reviewed_subject_in_isolation(
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
    (repo / "README.md").write_text("# advanced control main\n", encoding="utf-8")
    (repo / ".ai" / "tasks" / "TASK-101.yaml").write_text(
        TASK_SOURCE.replace("    - OUTPUT.txt", "    - CONTROL.txt"),
        encoding="utf-8",
    )
    git(repo, "add", "README.md", ".ai/tasks/TASK-101.yaml")
    git(repo, "commit", "--quiet", "-m", "advance control main")
    git(repo, "push", "--quiet", "origin", "main")
    control_head = git(repo, "rev-parse", "HEAD")
    control_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    runner = IsolatedRemediationRunner(reviewed_sha)

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

    assert len(runner.calls) == 1
    assert len(runner.subjects) == 1
    assert "    - OUTPUT.txt" in runner.task_sources[0]
    assert "    - CONTROL.txt" in (
        repo / ".ai" / "tasks" / "TASK-101.yaml"
    ).read_text(encoding="utf-8")
    assert not runner.subjects[0].exists()
    assert summary.review_id == "REVIEW-RUN-101-000"
    assert summary.reviewed_sha == reviewed_sha
    assert summary.head_sha != reviewed_sha
    assert (
        git(repo, "merge-base", "--is-ancestor", reviewed_sha, summary.head_sha)
        == ""
    )
    assert run_data["kind"] == "REMEDIATION"
    assert run_data["execution"]["run"]["task"] == {
        "id": "TASK-101",
        "revision": 1,
    }
    assert run_data["execution"]["run"]["base_sha"] == reviewed_sha
    assert Path(run_data["execution"]["run"]["workspace"]) != repo
    assert git(repo, "rev-parse", "HEAD") == control_head
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == control_branch
    assert git(repo, "status", "--porcelain") == ""
    upstream = tmp_path / "upstream.git"
    review_ref = f"refs/heads/aios/review/{summary.run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{summary.run_id}"
    assert git(upstream, "rev-parse", review_ref) == summary.head_sha
    assert git(
        upstream, "show", f"{artifacts_ref}:.ai/transport/run.json"
    ) == (runtime_paths(repo).runs / f"{summary.run_id}.json").read_text(
        encoding="utf-8"
    )
    assert git(
        upstream, "show", f"{artifacts_ref}:.ai/transport/result.json"
    ) == summary.result_path.read_text(encoding="utf-8")


def test_historical_remediation_failure_persists_exact_subject_candidate(
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
    (repo / "README.md").write_text("# advanced control main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "--quiet", "-m", "advance control main")
    git(repo, "push", "--quiet", "origin", "main")
    control_head = git(repo, "rev-parse", "HEAD")
    runner = IsolatedRemediationRunner(reviewed_sha)

    def failing_verification(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=1, stdout=b"", stderr=b"historical failure"
        )

    with pytest.raises(OperatorError, match="verification command failed"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=runner,
            verification_runner=failing_verification,
        )

    failure = json.loads(
        (runtime_paths(repo).failures / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(runner.calls) == 1
    assert failure["kind"] == "FAILURE"
    assert failure["run_id"] == "RUN-101-001"
    assert failure["task"] == {"id": "TASK-101", "revision": 1}
    assert failure["executor"] == "codex"
    assert failure["base_sha"] == reviewed_sha
    assert failure["failed_head_sha"] != reviewed_sha
    assert failure["phase"] == "VERIFICATION"
    assert failure["candidate"]["transportable"] is True
    assert git(repo, "rev-parse", "HEAD") == control_head
    assert git(repo, "status", "--porcelain") == ""
    assert git(
        tmp_path / "upstream.git",
        "rev-parse",
        "refs/heads/aios/failure/RUN-101-001",
    ) == failure["failed_head_sha"]
    assert not (runtime_paths(repo).results / "RUN-101-001.json").exists()


def test_historical_remediation_requires_task_at_reviewed_sha_before_run(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    reviewed_sha = git(repo, "rev-parse", "HEAD")
    task_path = repo / ".ai" / "tasks" / "TASK-101.yaml"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(TASK_SOURCE, encoding="utf-8")
    git(repo, "add", ".ai/tasks/TASK-101.yaml")
    git(repo, "commit", "--quiet", "-m", "add current task only")
    git(repo, "push", "--quiet", "origin", "main")
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        reviewed_sha=reviewed_sha,
    )
    runner = IsolatedRemediationRunner(reviewed_sha)
    runs_before = {
        path.name for path in runtime_paths(repo).runs.glob("*.json")
    }

    with pytest.raises(OperatorError, match="historical TASK rejected"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=runner,
        )

    assert runner.calls == []
    assert {
        path.name for path in runtime_paths(repo).runs.glob("*.json")
    } == runs_before


def test_historical_remediation_workspace_failure_precedes_run_and_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    (repo / "README.md").write_text("# advanced control main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "--quiet", "-m", "advance control main")
    git(repo, "push", "--quiet", "origin", "main")
    control_head = git(repo, "rev-parse", "HEAD")
    runner = IsolatedRemediationRunner(reviewed_sha)
    runs_before = {
        path.name for path in runtime_paths(repo).runs.glob("*.json")
    }

    def fail_workspace(repo: Path, head_sha: str) -> Path:
        assert head_sha == reviewed_sha
        raise OperatorError("historical workspace setup failed")

    monkeypatch.setattr(
        operator_module, "_create_historical_workspace", fail_workspace
    )

    with pytest.raises(OperatorError, match="historical workspace setup failed"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=runner,
        )

    assert runner.calls == []
    assert {
        path.name for path in runtime_paths(repo).runs.glob("*.json")
    } == runs_before
    assert git(repo, "rev-parse", "HEAD") == control_head
    assert git(repo, "status", "--porcelain") == ""


def test_historical_workspace_rejects_missing_object_before_temp_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    control_head = git(repo, "rev-parse", "HEAD")
    worktrees_before = git(repo, "worktree", "list", "--porcelain")

    monkeypatch.setattr(
        operator_module.tempfile,
        "mkdtemp",
        lambda *args, **kwargs: pytest.fail(
            "missing historical object must fail before allocation"
        ),
    )

    with pytest.raises(OperatorError, match="Git command failed"):
        operator_module._create_historical_workspace(repo, "f" * 40)

    assert git(repo, "rev-parse", "HEAD") == control_head
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "worktree", "list", "--porcelain") == worktrees_before


def test_historical_verification_only_repair_uses_transported_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical_command = (
        "python basetemp_probe.py "
        "--basetemp=.git/aios/pytest-historical-repair"
    )
    historical_task_source = TASK_SOURCE.replace(
        "git status --porcelain", canonical_command
    )
    repo = make_repo(tmp_path, task_source=historical_task_source)
    (repo / "basetemp_probe.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1].split('=', 1)[1]).mkdir(parents=True)\n",
        encoding="utf-8",
    )
    git(repo, "add", "basetemp_probe.py")
    git(repo, "commit", "--quiet", "-m", "add historical verification probe")
    git(repo, "push", "--quiet", "origin", "main")
    root_base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "--quiet", "-c", "historical-verification")
    (repo / "OUTPUT.txt").write_text("historical candidate\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "historical candidate")
    failed_head = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "--quiet", "main")
    (repo / "CONTROL.txt").write_text("current runtime\n", encoding="utf-8")
    git(repo, "add", "CONTROL.txt")
    git(repo, "commit", "--quiet", "-m", "current control")
    control_head = git(repo, "rev-parse", "HEAD")

    failed_run_id = "RUN-101-004"
    remote_run = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": root_base_sha,
        "workspace": "discarded-historical-machine",
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
        "phase": "VERIFICATION",
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["OUTPUT.txt"],
            "outside_task_scope": [],
        },
    }
    structural = result_payload(
        failed_run_id, failed_head, changed_files=["OUTPUT.txt"]
    )
    sidecar = json.dumps(
        {
            "kind": "PRE_VERIFICATION_CANDIDATE",
            "run_id": failed_run_id,
            "task": {"id": "TASK-101", "revision": 1},
            "subject_sha": failed_head,
            "package": structural,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    artifact = RemoteFailureArtifacts(
        failed_run_id,
        failed_head,
        json.dumps(remote_run).encode(),
        json.dumps(failure).encode(),
        None,
        sidecar,
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_repair_recovery",
        lambda repo, *, failed_run_id: RemoteRepairRecovery(
            (artifact,), ("RUN-101-004",)
        ),
    )
    monkeypatch.setattr(
        operator_module,
        "read_remote_task",
        lambda repo, *, commit_sha, task_id: historical_task_source.encode(),
    )
    verification_workspaces = []
    worktrees_before = git(repo, "worktree", "list", "--porcelain")

    def verify(command, **kwargs):
        subject_repo = kwargs["cwd"]
        verification_workspaces.append(subject_repo)
        assert git(subject_repo, "rev-parse", "HEAD") == failed_head
        assert (subject_repo / ".git").is_dir()
        subject_aios = subject_repo / ".git" / "aios"
        assert subject_aios.is_dir()
        assert subject_aios.resolve() != (repo / ".git" / "aios").resolve()
        assert not any(subject_aios.iterdir())
        completed = subprocess.run(command, **kwargs)
        assert (
            subject_repo / ".git" / "aios" / "pytest-historical-repair"
        ).is_dir()
        cleanup_probe = subject_repo / ".git" / "aios" / "readonly-probe"
        cleanup_probe.write_text("cleanup must handle read-only Git metadata\n")
        cleanup_probe.chmod(0o444)
        return completed

    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair={
            "repair_id": "REPAIR-101-HISTORICAL-VERIFY",
            "failed_run_id": failed_run_id,
            "failed_head_sha": failed_head,
            "task": {"id": "TASK-101", "revision": 1},
            "action": "NO_CHANGE",
            "modification_scope": [],
            "instructions": ["Re-run canonical verification only."],
            "constraints": ["Commit the output."],
        },
        native_runner=lambda *args, **kwargs: pytest.fail(
            "historical verification-only continuation invoked an Executor"
        ),
        verification_runner=verify,
    )

    assert summary.run_id == "RUN-101-005"
    assert summary.head_sha == failed_head
    assert len(verification_workspaces) == 1
    assert not verification_workspaces[0].exists()
    assert git(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert git(repo, "rev-parse", "HEAD") == control_head
    assert git(repo, "status", "--porcelain") == ""
    persisted = json.loads(summary.result_path.read_text(encoding="utf-8"))
    assert persisted["evidence"][0]["source"]["command"] == canonical_command


def test_historical_repair_rejects_remote_duplicate_before_workspace_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)

    def reject_duplicate(repo, *, failed_run_id):
        raise operator_module.ReviewTransportError(
            "canonical continuation already exists for failed RUN"
        )

    monkeypatch.setattr(
        operator_module, "resolve_remote_repair_recovery", reject_duplicate
    )
    monkeypatch.setattr(
        operator_module,
        "_create_historical_workspace",
        lambda *args: pytest.fail("workspace must not be created"),
    )

    with pytest.raises(OperatorError, match="continuation already exists"):
        run_repair("RUN-101-004", executor="codex", repo=repo, repair={})


def assert_historical_repair_rejected_before_mutation(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed_run_id: str,
    message: str,
) -> None:
    monkeypatch.setattr(
        operator_module,
        "_create_historical_workspace",
        lambda *args: pytest.fail("workspace must not be created"),
    )

    def reject_executor(*args, **kwargs):
        raise AssertionError("Executor must not be invoked")

    with pytest.raises(OperatorError, match=message):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair={},
            native_runner=reject_executor,
        )


def historical_failure_artifact(
    *,
    run_id: str,
    base_sha: str,
    failed_head_sha: str,
    continuation_of: str | None = None,
) -> RemoteFailureArtifacts:
    task_ref = {"id": "TASK-101", "revision": 1}
    run = {
        "run_id": run_id,
        "task": task_ref,
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": "historical-machine-path",
        "head_sha": None,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": run_id,
        "task": task_ref,
        "executor": "codex",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "candidate": {"repairable": True, "changed_files": ["OUTPUT.txt"]},
    }
    if continuation_of is not None:
        failure["continuation_of"] = continuation_of
    return RemoteFailureArtifacts(
        run_id,
        failed_head_sha,
        json.dumps(run).encode(),
        json.dumps(failure).encode(),
        None,
    )


def historical_remediation_source_fixture(
    repo: Path,
    *,
    source_reviewed_sha: str | None = None,
) -> tuple[RemoteRepairRecovery, RemoteRemediationLineage]:
    task_ref = {"id": "TASK-101", "revision": 1}
    reviewed_sha = git(repo, "rev-parse", "HEAD")
    source_sha = source_reviewed_sha or reviewed_sha
    root_base_sha = "historical-task-root"
    failed_run_id = "RUN-101-004"
    source_run_id = "RUN-101-003"
    finding = {
        "id": "R1",
        "basis": "AC1",
        "action": "CODE_FIX",
        "location": "OUTPUT.txt",
        "issue": "The output is absent.",
        "expected": "Commit only the output.",
    }
    remediation = {
        "finding_id": "R1",
        "action": "CODE_FIX",
        "reviewed_sha": reviewed_sha,
        "modification_scope": ["OUTPUT.txt"],
        "affected_verification": ["git status --porcelain"],
        "constraints": ["Commit the output."],
    }
    failed_run = {
        "run_id": failed_run_id,
        "task": task_ref,
        "executor": "codex",
        "base_sha": reviewed_sha,
        "workspace": "historical-machine-path",
        "head_sha": None,
        "status": "ACTIVE",
    }
    execution = {
        "review_id": "REVIEW-101-001",
        "finding": finding,
        "remediation": remediation,
        "run": failed_run,
        "original_constraints": ["Commit the output."],
    }
    failed_artifact = RemoteFailureArtifacts(
        failed_run_id,
        "historical-failed-head",
        json.dumps({"kind": "REMEDIATION", "execution": execution}).encode(),
        json.dumps(
            {
                "kind": "FAILURE",
                "run_id": failed_run_id,
                "task": task_ref,
                "executor": "codex",
                "base_sha": reviewed_sha,
                "failed_head_sha": "historical-failed-head",
                "candidate": {
                    "repairable": True,
                    "changed_files": ["OUTPUT.txt"],
                },
            }
        ).encode(),
        None,
    )
    source_run = {
        "run_id": source_run_id,
        "task": task_ref,
        "executor": "codex",
        "base_sha": root_base_sha,
        "workspace": "historical-machine-path",
        "head_sha": None,
        "status": "ACTIVE",
    }
    source_remediation = dict(remediation)
    source_remediation["reviewed_sha"] = source_sha
    review = {
        "review_id": "REVIEW-101-001",
        "reviewed_sha": source_sha,
        "mode": "PRIMARY",
        "verdict": "CHANGES_REQUIRED",
        "acceptance": {"AC1": "FAIL"},
        "findings": [finding],
    }
    source_result = result_payload(source_run_id, source_sha)
    source_result["result"]["claims"][0]["evidence"] = ["E-SOURCE"]
    source_result["evidence"] = [
        {
            "evidence_id": "E-SOURCE",
            "run_id": source_run_id,
            "subject_sha": source_sha,
            "type": "TEST",
            "source": {"command": "git status --porcelain"},
            "result": {"exit_code": 0, "summary": "verified"},
            "raw": {"path": ".ai/evidence/E-SOURCE.log"},
        }
    ]
    source = RemoteRemediationLineage(
        ref=f"refs/heads/aios/remediation/{source_run_id}-R1",
        source_run_id=source_run_id,
        review=json.dumps(review).encode(),
        remediation=json.dumps(source_remediation).encode(),
        run=json.dumps(source_run).encode(),
        result=json.dumps(source_result).encode(),
        repair=None,
    )
    return RemoteRepairRecovery((failed_artifact,), (failed_run_id,)), source


def test_cyclic_failed_continuation_is_rejected_before_workspace_or_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)

    def reject_cycle(repo, *, failed_run_id):
        raise operator_module.ReviewTransportError(
            "cyclic failed RUN continuation lineage"
        )

    monkeypatch.setattr(
        operator_module, "resolve_remote_repair_recovery", reject_cycle
    )
    assert_historical_repair_rejected_before_mutation(
        repo,
        monkeypatch,
        failed_run_id="RUN-101-004",
        message="cyclic failed RUN continuation lineage",
    )


def test_historical_task_revision_mismatch_is_rejected_before_workspace_or_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    artifact = historical_failure_artifact(
        run_id="RUN-101-004", base_sha=head, failed_head_sha="historical-head"
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
        lambda repo, *, commit_sha, task_id: TASK_SOURCE.replace(
            "revision: 1", "revision: 2"
        ).encode(),
    )
    assert_historical_repair_rejected_before_mutation(
        repo,
        monkeypatch,
        failed_run_id="RUN-101-004",
        message="historical TASK identity or revision mismatch",
    )


@pytest.mark.parametrize("authorization", ["missing", "conflicting"])
def test_legacy_repair_authorization_is_rejected_before_workspace_or_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
) -> None:
    repo = make_repo(tmp_path)
    root = git(repo, "rev-parse", "HEAD")
    predecessor = historical_failure_artifact(
        run_id="RUN-101-003",
        base_sha=root,
        failed_head_sha="historical-head-003",
    )
    target = historical_failure_artifact(
        run_id="RUN-101-004",
        base_sha="historical-head-003",
        failed_head_sha="historical-head-004",
        continuation_of="RUN-101-003",
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_repair_recovery",
        lambda repo, *, failed_run_id: RemoteRepairRecovery(
            (target, predecessor), ("RUN-101-003", "RUN-101-004")
        ),
    )
    monkeypatch.setattr(
        operator_module,
        "read_remote_task",
        lambda repo, *, commit_sha, task_id: TASK_SOURCE.encode(),
    )
    if authorization == "missing":
        def read_repair(repo, failed_run_id):
            raise operator_module.ReviewTransportError(
                "remote REPAIR not found for RUN-101-003"
            )

        monkeypatch.setattr(operator_module, "read_remote_repair", read_repair)
        message = "legacy REPAIR authorization rejected"
    else:
        monkeypatch.setattr(
            operator_module,
            "read_remote_repair",
            lambda repo, failed_run_id: json.dumps(
                {
                    "failed_run_id": failed_run_id,
                    "failed_head_sha": "conflicting-head",
                    "task": {"id": "TASK-101", "revision": 1},
                }
            ).encode(),
        )
        message = "historical REPAIR authorization mismatch"
    assert_historical_repair_rejected_before_mutation(
        repo,
        monkeypatch,
        failed_run_id="RUN-101-004",
        message=message,
    )


def test_reviewed_sha_mismatch_is_rejected_before_workspace_or_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    recovery, source = historical_remediation_source_fixture(
        repo, source_reviewed_sha="different-reviewed-sha"
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_repair_recovery",
        lambda repo, *, failed_run_id: recovery,
    )
    monkeypatch.setattr(
        operator_module,
        "read_remote_task",
        lambda repo, *, commit_sha, task_id: TASK_SOURCE.encode(),
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_remediation_lineages",
        lambda repo, *, finding_id, **kwargs: (source,),
    )
    assert_historical_repair_rejected_before_mutation(
        repo,
        monkeypatch,
        failed_run_id="RUN-101-004",
        message="reviewed source identity or SHA mismatch",
    )


def test_forged_origin_affected_verification_is_rejected_before_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    recovery, source = historical_remediation_source_fixture(repo)
    artifact = recovery.failures[0]
    run_data = json.loads(artifact.run)
    run_data["execution"]["remediation"]["affected_verification"] = [
        "forged verification command"
    ]
    forged = replace(artifact, run=json.dumps(run_data).encode())
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_repair_recovery",
        lambda repo, *, failed_run_id: RemoteRepairRecovery(
            (forged,), recovery.remote_run_ids
        ),
    )
    monkeypatch.setattr(
        operator_module,
        "read_remote_task",
        lambda repo, *, commit_sha, task_id: TASK_SOURCE.encode(),
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_remediation_lineages",
        lambda repo, *, finding_id, **kwargs: (source,),
    )

    assert_historical_repair_rejected_before_mutation(
        repo,
        monkeypatch,
        failed_run_id="RUN-101-004",
        message="reviewed source identity or SHA mismatch",
    )


def test_ambiguous_reviewed_source_is_rejected_before_workspace_or_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    recovery, source = historical_remediation_source_fixture(repo)
    duplicate = replace(source, ref=source.ref + "-duplicate")
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_repair_recovery",
        lambda repo, *, failed_run_id: recovery,
    )
    monkeypatch.setattr(
        operator_module,
        "read_remote_task",
        lambda repo, *, commit_sha, task_id: TASK_SOURCE.encode(),
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_remediation_lineages",
        lambda repo, *, finding_id, **kwargs: (source, duplicate),
    )
    assert_historical_repair_rejected_before_mutation(
        repo,
        monkeypatch,
        failed_run_id="RUN-101-004",
        message="exact reviewed source lineage is ambiguous",
    )


def test_legacy_run_125_lineage_resolves_remediation_to_successful_repair_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    task_source = TASK_SOURCE.replace("TASK-101", "TASK-125")
    # Genuine historical lineage uses the explicit repository-default class.
    legacy = {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}
    task_source += "\nreturn_affinity: " + json.dumps(legacy) + "\n"
    task_ref = {"id": "TASK-125", "revision": 1}
    root_base_sha = "d47ed9c048cacd152452cf2a7054c662e1043c82"
    reviewed_sha = "11fa6d596434f6fd3ee100b2bb481c4a2093b8bd"
    heads = {
        "RUN-125-006": "head-006",
        "RUN-125-007": "head-007",
        "RUN-125-008": "head-008",
        "RUN-125-009": "bde5ac1e781254920ec7bb6bdb3246e1a758ebb1",
    }
    remediation = {
        "finding_id": "F-125-001",
        "action": "CODE_FIX",
        "reviewed_sha": reviewed_sha,
        "modification_scope": ["OUTPUT.txt"],
        "affected_verification": ["git status --porcelain"],
        "constraints": ["Commit the output."],
    }
    finding = {
        "id": "F-125-001",
        "basis": "AC1",
        "action": "CODE_FIX",
        "location": "OUTPUT.txt",
        "issue": "The output needs correction.",
        "expected": "The output is corrected.",
    }

    artifacts = []
    run_006 = {
        "kind": "REMEDIATION",
        "execution": {
            "review_id": "REVIEW-125-001",
            "finding": finding,
            "remediation": remediation,
            "run": {
                "run_id": "RUN-125-006",
                "task": task_ref,
                "executor": "codex",
                "base_sha": reviewed_sha,
                "workspace": "legacy",
                "head_sha": None,
                "status": "ACTIVE",
            },
            "original_constraints": ["Commit the output."],
        },
    }
    prior_head = heads["RUN-125-006"]
    for number in (6, 7, 8, 9):
        run_id = f"RUN-125-{number:03d}"
        continuation = None if number == 6 else f"RUN-125-{number - 1:03d}"
        run_data = run_006 if number == 6 else {
            "run_id": run_id,
            "task": task_ref,
            "executor": "codex",
            "base_sha": prior_head,
            "workspace": "legacy",
            "head_sha": None,
            "status": "ACTIVE",
        }
        failure = {
            "kind": "FAILURE",
            "run_id": run_id,
            "task": task_ref,
            "executor": "codex",
            "base_sha": reviewed_sha if number == 6 else prior_head,
            "failed_head_sha": heads[run_id],
            "candidate": {"repairable": True, "changed_files": ["OUTPUT.txt"]},
        }
        if continuation is not None:
            failure["continuation_of"] = continuation
        artifacts.append(
            RemoteFailureArtifacts(
                run_id,
                heads[run_id],
                json.dumps(run_data).encode(),
                json.dumps(failure).encode(),
                None,
            )
        )
        prior_head = heads[run_id]
    artifacts.reverse()
    recovery = RemoteRepairRecovery(tuple(artifacts), tuple(heads))
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_repair_recovery",
        lambda repo, *, failed_run_id: recovery,
    )
    monkeypatch.setattr(
        operator_module,
        "read_remote_task",
        lambda repo, *, commit_sha, task_id: task_source.encode(),
    )
    failures = {
        artifact.run_id: json.loads(artifact.failure) for artifact in artifacts
    }

    def legacy_repair(repo, failed_run_id):
        return json.dumps(
            {
                "repair_id": f"REPAIR-{failed_run_id}",
                "failed_run_id": failed_run_id,
                "failed_head_sha": failures[failed_run_id]["failed_head_sha"],
                "task": task_ref,
                "action": "CODE_FIX",
                "modification_scope": ["OUTPUT.txt"],
                "instructions": ["Continue the immutable correction."],
                "constraints": ["Commit the output."],
            }
        ).encode()

    monkeypatch.setattr(operator_module, "read_remote_repair", legacy_repair)
    source_run = {
        "run_id": "RUN-125-005",
        "task": task_ref,
        "executor": "codex",
        "base_sha": "f" * 40,
        "workspace": "legacy",
        "head_sha": None,
        "status": "ACTIVE",
        "return_affinity": legacy,
    }
    review = {
        "review_id": "REVIEW-125-001",
        "reviewed_sha": reviewed_sha,
        "mode": "PRIMARY",
        "verdict": "CHANGES_REQUIRED",
        "acceptance": {"AC1": "FAIL"},
        "findings": [finding],
    }
    source_result = result_payload("RUN-125-005", reviewed_sha)
    source_result["result"]["claims"][0]["evidence"] = ["E-125-005"]
    source_result["evidence"] = [
        {
            "evidence_id": "E-125-005",
            "run_id": "RUN-125-005",
            "subject_sha": reviewed_sha,
            "type": "TEST",
            "source": {"command": "git status --porcelain"},
            "result": {"exit_code": 0, "summary": "verified"},
            "raw": {"path": ".ai/evidence/E-125-005.log"},
        }
    ]
    source_failure_run = dict(source_run, run_id="RUN-125-004", base_sha=root_base_sha)
    source_failure = {
        "kind": "FAILURE", "run_id": "RUN-125-004", "task": task_ref,
        "executor": "codex", "base_sha": root_base_sha, "failed_head_sha": source_run["base_sha"],
        "candidate": {"transportable": True, "repairable": True, "dirty": False,
                      "descends_from_base": True, "changed_files": ["OUTPUT.txt"], "outside_task_scope": []},
    }
    source_authorization = {
        "repair_id": "REPAIR-RUN-125-004", "failed_run_id": "RUN-125-004",
        "failed_head_sha": source_run["base_sha"], "task": task_ref, "action": "CODE_FIX",
        "modification_scope": ["OUTPUT.txt"], "instructions": ["Correct the committed output."],
        "constraints": ["Commit the output."],
    }
    source_repair = json.dumps({
        "failed_run_id": "RUN-125-004", "root_base_sha": root_base_sha,
        "failed_head_sha": source_run["base_sha"], "run": source_run,
        "task": {"task_id": "TASK-125", "revision": 1, "return_affinity": legacy},
        "failure": source_failure, "repair": source_authorization,
    }).encode()
    source_lineage = RemoteRemediationLineage(
        ref="refs/heads/aios/remediation/RUN-125-005-F-125-001",
        source_run_id="RUN-125-005",
        review=json.dumps(review).encode(),
        remediation=json.dumps(remediation).encode(),
        run=json.dumps(source_run).encode(),
        result=json.dumps(source_result).encode(),
        repair=source_repair,
    )
    # Supply the exact canonical transport, decision and predecessor provenance
    # at the Git boundary; keep the production lineage validators in the path.
    refs = {
        "refs/heads/aios/artifacts/RUN-125-005": "a" * 40,
        "refs/heads/aios/review/RUN-125-005": reviewed_sha,
        "refs/heads/aios/review-decision/RUN-125-005": "b" * 40,
        "refs/heads/aios/failure-artifacts/RUN-125-004": "c" * 40,
        "refs/heads/aios/failure/RUN-125-004": source_run["base_sha"],
        "refs/heads/aios/repair/RUN-125-004": "e" * 40,
    }
    review_path = ".ai/reviews/REVIEW-125-001.yaml"
    blobs = {
        (refs["refs/heads/aios/artifacts/RUN-125-005"], ".ai/transport/run.json"): json.dumps(source_run).encode(),
        (refs["refs/heads/aios/artifacts/RUN-125-005"], ".ai/transport/result.json"): json.dumps(source_result).encode(),
        (refs["refs/heads/aios/artifacts/RUN-125-005"], ".ai/transport/repair.json"): source_repair,
        (refs["refs/heads/aios/review-decision/RUN-125-005"], review_path): json.dumps(review).encode(),
        (refs["refs/heads/aios/failure-artifacts/RUN-125-004"], ".ai/transport/run.json"): json.dumps(source_failure_run).encode(),
        (refs["refs/heads/aios/failure-artifacts/RUN-125-004"], ".ai/transport/failure.json"): json.dumps(source_failure).encode(),
    }
    ancestors = {(root_base_sha, source_run["base_sha"]), (root_base_sha, reviewed_sha)}
    deltas = ancestors | {(source_run["base_sha"], reviewed_sha)}

    def canonical_git(root, *args, **kwargs):
        assert root == repo
        if args[0] == "ls-remote":
            ref = args[-1]
            return 0, f"{refs[ref]}\t{ref}" if ref in refs else "", ""
        if args[0] in {"fetch", "cat-file"}:
            assert args[-1] in set(refs.values())
            return 0, "commit" if args[0] == "cat-file" else "", ""
        if args[0] == "show":
            key = tuple(args[1].split(":", 1))
            return (0, blobs[key].decode(), "") if key in blobs else (1, "", "missing")
        if args[0] == "ls-tree":
            assert args[3] == refs["refs/heads/aios/review-decision/RUN-125-005"]
            return 0, review_path, ""
        if args[0] == "merge-base":
            assert tuple(args[-2:]) in ancestors
            return 0, "", ""
        if args[0] == "diff":
            assert tuple(args[-2:]) in deltas
            return 0, "OUTPUT.txt\0", ""
        raise AssertionError(f"unexpected canonical provenance operation: {args}")

    monkeypatch.setattr(publication_module, "_git", canonical_git)
    monkeypatch.setattr(transport_module, "_read_remote_blob", lambda root, remote, sha, path: blobs.get((sha, path)))
    monkeypatch.setattr(publication_module, "resolve_remote_repair_authorization", lambda *args, **kwargs: SimpleNamespace(
        commit_sha=refs["refs/heads/aios/repair/RUN-125-004"], repair=json.dumps(source_authorization).encode()))
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_remediation_lineages",
        lambda repo, *, finding_id, **kwargs: (source_lineage,),
    )
    monkeypatch.setattr(operator_module, "_git_is_ancestor", lambda *args: True)

    admission = operator_module._resolve_historical_repair_admission(
        repo, "RUN-125-009"
    )

    assert admission.root_base_sha == root_base_sha
    assert admission.task.task_id == "TASK-125"
    assert admission.task.revision == 1
    assert admission.failure["failed_head_sha"] == heads["RUN-125-009"]


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
    assert observation["soft_budget"] == {
        "threshold_seconds": 1800,
        "status": "WITHIN",
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
    assert observation["soft_budget"] == {
        "threshold_seconds": 1800,
        "status": "NOT_APPLICABLE",
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


def test_repair_transport_failure_preserves_result_without_admission_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, repair = repair_contract(repo)
    runner = RepairRunner(repo)

    def fail_transport(*args, **kwargs):
        raise operator_module.ReviewTransportError("repair publish failed")

    monkeypatch.setattr(runtime_module, "transport_post_pass", fail_transport)

    with pytest.raises(OperatorError, match="review transport failed"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair,
            native_runner=runner,
        )

    state = runtime_paths(repo)
    assert len(runner.executions) == 1
    assert (state.results / "RUN-101-001.json").is_file()
    assert not (state.failures / "RUN-101-001.json").exists()
    assert admission_failure_records(repo) == []
    assert remote_admission_failure_records(tmp_path / "upstream.git") == []


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
        execution_profile_path=state.execution_profiles / f"{summary.run_id}.json",
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


def clone_runtime_fresh(repo: Path, destination: Path) -> Path:
    upstream = Path(git(repo, "remote", "get-url", "origin"))
    subprocess.run(
        ("git", "clone", "--quiet", str(upstream), str(destination)), check=True
    )
    git(destination, "config", "user.name", "Fresh Runtime Test")
    git(destination, "config", "user.email", "fresh@example.invalid")
    return destination


def complete_four_digit_run_namespace() -> tuple[str, ...]:
    return tuple(f"RUN-101-{number:03d}" for number in range(1, 1001))


def test_next_run_id_recognizes_four_digit_local_identity(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "RUN-101-1000.json").write_text("{}", encoding="utf-8")

    assert operator_module.next_run_id("TASK-101", runs) == "RUN-101-1001"


def test_fresh_primary_allocates_after_four_digit_remote_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    remote_run_ids = complete_four_digit_run_namespace()
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_run_namespace",
        lambda *args, **kwargs: RemoteRunNamespace(remote_run_ids, ()),
    )

    summary = run_task(
        "TASK-101",
        executor="codex",
        repo=repo,
        native_runner=FakeCodexRunner(repo),
    )

    assert summary.run_id == "RUN-101-1001"


def publish_conflicting_primary_failure(
    repo: Path, *, run_id: str, base_sha: str, root: Path
) -> None:
    failure_path = root / f"{run_id}-failure.json"
    failure_path.write_text(
        json.dumps(
            {
                "kind": "FAILURE",
                "run_id": run_id,
                "task": {"id": "TASK-101", "revision": 1},
                "executor": "codex",
                "base_sha": base_sha,
                "failed_head_sha": base_sha,
                "phase": "EXECUTION",
                "candidate": {
                    "repairable": False,
                    "transportable": False,
                    "dirty": False,
                    "descends_from_base": True,
                    "changed_files": [],
                    "outside_task_scope": [],
                },
                "error": {"type": "OperatorError", "message": "source failed"},
            }
        ),
        encoding="utf-8",
    )
    state = runtime_paths(repo)
    with pytest.raises(
        operator_module.ReviewTransportError,
        match="canonical RUN has competing RESULT and FAILURE terminals",
    ):
        operator_module.transport_failure(
            repo,
            run_id=run_id,
            head_sha=base_sha,
            run_path=state.runs / f"{run_id}.json",
            failure_path=failure_path,
            publish_candidate=False,
        )


def test_fresh_primary_reserves_remote_terminal_run_identity(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "source")
    first = run_task(
        "TASK-101", executor="codex", repo=source, native_runner=FakeCodexRunner(source)
    )
    fresh = clone_runtime_fresh(source, tmp_path / "fresh")
    second_runner = FakeCodexRunner(fresh)

    second = run_task(
        "TASK-101", executor="codex", repo=fresh, native_runner=second_runner
    )

    assert first.run_id == "RUN-101-001"
    assert second.run_id == "RUN-101-002"
    assert len(second_runner.calls) == 1


def test_normal_primary_conflict_fails_before_run_or_executor(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "source")
    runner = FakeCodexRunner(source)
    success = run_task(
        "TASK-101", executor="codex", repo=source, native_runner=runner
    )
    publish_conflicting_primary_failure(
        source,
        run_id=success.run_id,
        base_sha=success.base_sha,
        root=tmp_path,
    )
    fresh = clone_runtime_fresh(source, tmp_path / "fresh")
    rejected_runner = FakeCodexRunner(fresh)

    with pytest.raises(
        OperatorError,
        match="canonical RUN has conflicting terminal artifacts: RUN-101-001",
    ):
        run_task(
            "TASK-101",
            executor="codex",
            repo=fresh,
            native_runner=rejected_runner,
        )

    assert rejected_runner.calls == []
    assert list(runtime_paths(fresh).runs.glob("*.json")) == []


def test_recover_primary_rebinds_exact_candidate_with_fresh_evidence(
    tmp_path: Path,
) -> None:
    source = make_repo(tmp_path / "source")
    success = run_task(
        "TASK-101", executor="codex", repo=source, native_runner=FakeCodexRunner(source)
    )
    publish_conflicting_primary_failure(
        source,
        run_id=success.run_id,
        base_sha=success.base_sha,
        root=tmp_path,
    )
    fresh = clone_runtime_fresh(source, tmp_path / "fresh")
    control_head = git(fresh, "rev-parse", "HEAD")
    control_status = git(fresh, "status", "--porcelain")
    control_origin = git(fresh, "remote", "get-url", "origin")
    worktrees_before = git(fresh, "worktree", "list", "--porcelain")
    verification_calls = []

    def counting_verification(command, **kwargs):
        verification_calls.append(command)
        subject_repo = kwargs["cwd"]
        assert (subject_repo / ".git").is_dir()
        subject_aios = subject_repo / ".git" / "aios"
        assert subject_aios.is_dir()
        assert subject_aios.resolve() != (fresh / ".git" / "aios").resolve()
        assert not any(subject_aios.iterdir())
        assert git(subject_repo, "remote", "get-url", "origin") == control_origin
        assert git(subject_repo, "rev-parse", "HEAD") == success.head_sha
        return subprocess.run(command, **kwargs)

    recovered = recover_primary(
        success.run_id,
        repo=fresh,
        verification_runner=counting_verification,
    )

    assert recovered.run_id == "RUN-101-002"
    assert recovered.head_sha == success.head_sha
    assert len(verification_calls) == 1
    assert git(fresh, "rev-parse", "HEAD") == control_head
    assert git(fresh, "status", "--porcelain") == control_status
    assert git(fresh, "worktree", "list", "--porcelain") == worktrees_before
    state = runtime_paths(fresh)
    result = json.loads(recovered.result_path.read_text(encoding="utf-8"))
    assert {item["run_id"] for item in result["evidence"]} == {"RUN-101-002"}
    assert {item["subject_sha"] for item in result["evidence"]} == {
        success.head_sha
    }
    assert result["result"]["claims"][0]["evidence"] == [
        result["evidence"][0]["evidence_id"]
    ]
    observation = json.loads(
        (state.observations / "RUN-101-002.json").read_text(encoding="utf-8")
    )
    assert observation["executor_invoked"] is False
    assert git(
        Path(git(fresh, "remote", "get-url", "origin")),
        "rev-parse",
        "refs/heads/aios/artifacts/RUN-101-001",
    )
    assert git(
        Path(git(fresh, "remote", "get-url", "origin")),
        "rev-parse",
        "refs/heads/aios/failure-artifacts/RUN-101-001",
    )


def test_recover_primary_allocates_after_four_digit_remote_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_repo(tmp_path / "source")
    success = run_task(
        "TASK-101", executor="codex", repo=source, native_runner=FakeCodexRunner(source)
    )
    publish_conflicting_primary_failure(
        source,
        run_id=success.run_id,
        base_sha=success.base_sha,
        root=tmp_path,
    )
    fresh = clone_runtime_fresh(source, tmp_path / "fresh")
    resolve_remote_primary_recovery = operator_module.resolve_remote_primary_recovery

    def resolve_with_four_digit_namespace(*args, **kwargs):
        recovery = resolve_remote_primary_recovery(*args, **kwargs)
        return replace(
            recovery,
            remote_run_ids=complete_four_digit_run_namespace(),
        )

    monkeypatch.setattr(
        operator_module,
        "resolve_remote_primary_recovery",
        resolve_with_four_digit_namespace,
    )

    recovered = recover_primary(success.run_id, repo=fresh)

    assert recovered.run_id == "RUN-101-1001"


def test_remediation_ignores_unrelated_cross_task_collision_and_preserves_admission(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    baseline = git(repo, "rev-parse", "HEAD")

    # Unrelated TASK-041 lineage with structurally incompatible layout (2 reviews)
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-041-002",
        finding_id="R1",
        task_id="TASK-041",
        task_revision=1,
        extra_reviews=1,
    )

    # Valid TASK-101 lineage for current task and revision
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        task_id="TASK-101",
        task_revision=1,
    )

    runner = RemediationRunner(repo)
    summary = run_remediation(
        "TASK-101",
        finding_id="R1",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )

    assert summary.run_id == "RUN-101-001"
    assert summary.review_id == "REVIEW-RUN-101-000"
    assert summary.finding_id == "R1"
    assert summary.reviewed_sha == baseline
    assert summary.head_sha != baseline
    assert len(runner.calls) == 1
    assert git(repo, "status", "--porcelain") == ""


def test_remote_approval_binds_exact_source_lineage_and_is_idempotent(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    ref = publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
    )
    state_root = runtime_state_root(repo)

    first = record_remote_approval(
        repo=repo,
        state_root=state_root,
        source_run_id="RUN-101-000",
        finding_id="R1",
        approver="human-reviewer",
    )
    approval_path = next((state_root / "approvals").glob("*.json"))
    before = approval_path.read_bytes()
    second = record_remote_approval(
        repo=repo,
        state_root=state_root,
        source_run_id="RUN-101-000",
        finding_id="R1",
        approver="human-reviewer",
    )
    record = json.loads(before)

    assert first.replayed is False
    assert second.replayed is True
    assert approval_path.read_bytes() == before
    assert record == {
        "version": 1,
        "source_run_id": "RUN-101-000",
        "task_id": "TASK-101",
        "task_revision": 1,
        "review_id": "REVIEW-RUN-101-000",
        "finding_id": "R1",
        "action": "CODE_FIX",
        "reviewed_sha": first.reviewed_sha,
        "remediation_ref": ref,
        "remediation_sha": first.remediation_sha,
        "approver": "human-reviewer",
    }


def _write_external_governed_policy(repo: Path) -> None:
    policy = repo / ".ai" / "executor-profiles.yaml"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        "format: AIOS_EXECUTOR_PROFILES_POLICY\n"
        "version: 1\n"
        "executors:\n"
        "  codex:\n"
        "    default_model: test/codex-external\n"
        "    default_reasoning_effort: low\n"
        "    supported_reasoning_efforts: [low, repo_only]\n"
        "  antigravity:\n"
        "    default_model: test/antigravity-external\n"
        "    default_reasoning_effort: low\n"
        "    supported_reasoning_efforts: [low, repo_only]\n",
        encoding="utf-8",
    )
    if (repo / ".git").exists():
        commit_setup_state(repo, ".ai/executor-profiles.yaml", message="external policy")


def test_one_remediation_intent_records_a3_then_replays_terminal_a6_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path)
    _write_external_governed_policy(repo)
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
    )
    runner = RemediationRunner(repo)

    first_approval, first_dispatch = run_approved_remediation_intent(
        "intent-101-r1",
        "RUN-101-000",
        "R1",
        executor="codex",
        approver="human-reviewer",
        reasoning_effort="repo_only",
        repo=repo,
        native_runner=runner,
    )
    second_approval, second_dispatch = run_approved_remediation_intent(
        "intent-101-r1",
        "RUN-101-000",
        "R1",
        executor="codex",
        approver="human-reviewer",
        repo=repo,
        native_runner=runner,
    )

    assert first_approval.replayed is False
    assert second_approval.replayed is True
    assert first_dispatch.replayed is False
    assert second_dispatch.replayed is True
    assert second_dispatch.run_id == first_dispatch.run_id
    assert second_dispatch.remediation_sha == first_approval.remediation_sha
    assert len(runner.calls) == 1
    record = next((runtime_state_root(repo) / "correction-dispatches").glob("*.json"))
    assert json.loads(record.read_text(encoding="utf-8"))["reasoning_effort"] == "repo_only"
    policy = repo / ".ai" / "executor-profiles.yaml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "[low, repo_only]", "[low]"
        ), encoding="utf-8",
    )
    with pytest.raises(OperatorError, match="profile binding"):
        run_approved_remediation_intent(
            "intent-101-r1", "RUN-101-000", "R1", executor="codex",
            approver="human-reviewer", repo=repo, native_runner=runner,
        )
    assert len(runner.calls) == 1
    assert operator_module.main([
        "approved-remediation-intent", "intent-101-r1", "RUN-101-000", "R1",
        "--executor", "codex", "--approver", "human-reviewer", "--repo", str(repo),
    ]) == 1
    assert "profile binding" in capsys.readouterr().err


def test_remediation_intent_a3_persistence_can_be_replayed_into_a6(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aios_renew.correction_dispatch as correction_module

    repo = make_repo(tmp_path)
    _write_external_governed_policy(repo)
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
    )
    real_execute = correction_module.execute_correction_dispatch

    def interrupt_after_a3(**_kwargs):
        raise correction_module.CorrectionDispatchError("interrupted after A3")

    monkeypatch.setattr(
        correction_module, "execute_correction_dispatch", interrupt_after_a3
    )
    with pytest.raises(OperatorError, match="interrupted after A3"):
        run_approved_remediation_intent(
            "intent-101-replay",
            "RUN-101-000",
            "R1",
            executor="antigravity",
            approver="human-reviewer",
            repo=repo,
        )
    approval_path = next((runtime_state_root(repo) / "approvals").glob("*.json"))
    before = approval_path.read_bytes()

    monkeypatch.setattr(correction_module, "execute_correction_dispatch", real_execute)
    runner = RemediationRunner(repo)
    approval, dispatch = run_approved_remediation_intent(
        "intent-101-replay",
        "RUN-101-000",
        "R1",
        executor="antigravity",
        approver="human-reviewer",
        repo=repo,
        native_runner=runner,
    )

    assert approval.replayed is True
    assert approval_path.read_bytes() == before
    assert dispatch.status == "SUCCEEDED"
    assert len(runner.calls) == 1


def test_remediation_intent_parser_requires_explicit_supported_executor_and_approver() -> None:
    parser = operator_module._parser()
    argv = [
        "approved-remediation-intent",
        "intent-112",
        "RUN-110-001",
        "F1",
        "--executor",
        "codex",
        "--approver",
        "trung-via",
    ]
    args = parser.parse_args(argv)
    assert args.executor == "codex"
    assert args.approver == "trung-via"

    with pytest.raises(SystemExit):
        parser.parse_args([*argv[:-4], "--executor", "antigravity-minimax"])


def test_current_approval_resolution_rejects_stale_remediation_ref(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
    )
    state_root = runtime_state_root(repo)
    approved = record_remote_approval(
        repo=repo,
        state_root=state_root,
        source_run_id="RUN-101-000",
        finding_id="R1",
        approver="human-reviewer",
    )
    current = require_current_approval(
        repo=repo,
        state_root=state_root,
        source_run_id="RUN-101-000",
        finding_id="R1",
    )
    assert current.remediation_sha == approved.remediation_sha

    author = tmp_path / "author-RUN-101-000-R1"
    (author / "lineage-note.txt").write_text("changed\n", encoding="utf-8")
    git(author, "add", "lineage-note.txt")
    git(author, "commit", "--quiet", "-m", "move remediation ref")
    git(
        author,
        "push",
        "--quiet",
        "--force",
        "origin",
        "HEAD:refs/heads/aios/remediation/RUN-101-000-R1",
    )
    with pytest.raises(RemoteSurfaceError, match="missing, stale"):
        require_current_approval(
            repo=repo,
            state_root=state_root,
            source_run_id="RUN-101-000",
            finding_id="R1",
        )


def test_remote_approval_ignores_unrelated_same_finding_and_sha_change_is_new_authority(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-041-002",
        finding_id="R1",
        task_id="TASK-041",
        extra_reviews=1,
    )
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
    )
    state_root = runtime_state_root(repo)
    first = record_remote_approval(
        repo=repo,
        state_root=state_root,
        source_run_id="RUN-101-000",
        finding_id="R1",
        approver="human-reviewer",
    )

    author = tmp_path / "author-RUN-101-000-R1"
    (author / "lineage-note.txt").write_text("new immutable content\n", encoding="utf-8")
    git(author, "add", "lineage-note.txt")
    git(author, "commit", "--quiet", "-m", "advance exact remediation ref")
    git(
        author,
        "push",
        "--quiet",
        "--force",
        "origin",
        "HEAD:refs/heads/aios/remediation/RUN-101-000-R1",
    )

    second = record_remote_approval(
        repo=repo,
        state_root=state_root,
        source_run_id="RUN-101-000",
        finding_id="R1",
        approver="human-reviewer",
    )
    records = [json.loads(path.read_text(encoding="utf-8")) for path in (state_root / "approvals").glob("*.json")]
    assert second.remediation_sha != first.remediation_sha
    assert len(records) == 2
    assert {item["remediation_sha"] for item in records} == {
        first.remediation_sha,
        second.remediation_sha,
    }


def test_remote_approval_malformed_exact_lineage_fails_without_authority(
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
    state_root = runtime_state_root(repo)

    with pytest.raises(RemoteSurfaceError, match="lineage is missing"):
        record_remote_approval(
            repo=repo,
            state_root=state_root,
            source_run_id="RUN-101-000",
            finding_id="R1",
            approver="human-reviewer",
        )
    assert not (state_root / "approvals").exists()


def test_remediation_lineage_from_different_revision_is_not_candidate(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    sha_r1 = git(repo, "rev-parse", "HEAD")

    # Publish revision 1 lineage
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        task_id="TASK-101",
        task_revision=1,
        reviewed_sha=sha_r1,
    )

    # Advance task to revision 2
    (repo / ".ai" / "tasks" / "TASK-101.yaml").write_text(
        TASK_SOURCE.replace("revision: 1", "revision: 2"), encoding="utf-8"
    )
    git(repo, "add", ".ai/tasks/TASK-101.yaml")
    git(repo, "commit", "--quiet", "-m", "advance to revision 2")
    git(repo, "push", "--quiet", "origin", "main")
    sha_r2 = git(repo, "rev-parse", "HEAD")

    # Publish revision 2 lineage
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-001",
        finding_id="R1",
        task_id="TASK-101",
        task_revision=2,
        reviewed_sha=sha_r2,
    )

    runner = RemediationRunner(repo)
    summary = run_remediation(
        "TASK-101",
        finding_id="R1",
        executor="codex",
        repo=repo,
        native_runner=runner,
    )

    # Must select the exact revision 2 lineage, ignoring revision 1
    assert summary.run_id == "RUN-101-002"
    assert summary.review_id == "REVIEW-RUN-101-001"
    assert summary.reviewed_sha == sha_r2
    assert len(runner.calls) == 1
    run_data = json.loads(
        (runtime_paths(repo).runs / f"{summary.run_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert run_data["predecessor"] == {
        "source_run_id": "RUN-101-001",
        "review_id": "REVIEW-RUN-101-001",
        "finding_id": "R1",
        "reviewed_sha": sha_r2,
    }
    assert run_data["execution_base"] == {
        "run_id": "RUN-101-001",
        "candidate_sha": sha_r2,
    }


def test_remediation_malformed_lineage_for_exact_task_fails_closed_before_executor(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)

    # Lineage attributable to exact task and revision, but structurally malformed (2 reviews)
    ref = publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        task_id="TASK-101",
        task_revision=1,
        extra_reviews=1,
    )
    observed_sha = git(repo, "ls-remote", "origin", ref).split()[0]

    runner = RemediationRunner(repo)
    with pytest.raises(
        OperatorError, match="must contain exactly one REVIEW and REMEDIATION"
    ):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=runner,
        )

    replacement_sha = git(repo, "rev-parse", "HEAD")
    git(tmp_path / "upstream.git", "update-ref", ref, replacement_sha)
    diagnostic = admission_failure_records(repo)[0]

    assert len(runner.calls) == 0
    assert diagnostic["observed_ref"] == ref
    assert diagnostic["observed_sha"] == observed_sha
    assert git(repo, "ls-remote", "origin", ref).split()[0] == replacement_sha
    assert diagnostic["observed_sha"] != replacement_sha


def test_remediation_ambiguous_lineages_for_exact_task_fails_closed_before_executor(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    sha = git(repo, "rev-parse", "HEAD")

    # Two valid lineages for exact task and revision
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        task_id="TASK-101",
        task_revision=1,
        reviewed_sha=sha,
    )
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-002",
        finding_id="R1",
        task_id="TASK-101",
        task_revision=1,
        reviewed_sha=sha,
    )

    runner = RemediationRunner(repo)
    with pytest.raises(
        OperatorError, match="canonical remote remediation lineage is ambiguous"
    ):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=runner,
        )

    # Neither candidate selected by heuristics; executor never invoked
    assert len(runner.calls) == 0


def test_direct_candidate_acceptance_with_cross_task_collision(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    baseline = git(repo, "rev-parse", "HEAD")

    # Unrelated TASK-041 lineage with structurally incompatible layout
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-041-002",
        finding_id="R1",
        task_id="TASK-041",
        task_revision=1,
        extra_reviews=1,
    )

    # Valid TASK-101 lineage
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        task_id="TASK-101",
        task_revision=1,
    )

    # Produce candidate committed at HEAD
    (repo / "OUTPUT.txt").write_text("direct fix\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "direct candidate fix")

    summary = accept_candidate(
        "TASK-101",
        finding_id="R1",
        executor="antigravity",
        repo=repo,
    )

    assert summary.run_id == "RUN-101-001"
    assert summary.review_id == "REVIEW-RUN-101-000"
    assert summary.finding_id == "R1"
    assert summary.reviewed_sha == baseline
    assert summary.executor == "antigravity"


@pytest.mark.parametrize(
    ("subcommand", "extra_args"),
    [
        ("continue", ["TASK-101"]),
        ("run", ["TASK-101"]),
        ("wakeup", ["DISPATCH-001", "TASK-101"]),
        ("approved-remediation-wakeup", ["CORR-001", "RUN-001", "F1"]),
        ("remediate", ["TASK-101"]),
        ("accept-candidate", ["TASK-101", "--finding", "F1"]),
        ("repair", ["RUN-001"]),
    ],
)
def test_cli_admits_antigravity_minimax_and_rejects_unsupported(
    subcommand: str, extra_args: list[str]
) -> None:
    parser = operator_module._parser()

    args = parser.parse_args(
        [subcommand, *extra_args, "--executor", "antigravity-minimax"]
    )
    assert args.executor == "antigravity-minimax"

    with pytest.raises(SystemExit):
        parser.parse_args(
            [subcommand, *extra_args, "--executor", "unsupported-executor"]
        )


def test_operator_admission_fails_closed_on_unsupported_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(
        OperatorError, match="unsupported executor: unsupported-executor"
    ):
        run_task("TASK-101", executor="unsupported-executor", repo=repo)

    with pytest.raises(
        OperatorError, match="unsupported executor: unsupported-executor"
    ):
        run_repair("RUN-101-001", executor="unsupported-executor", repo=repo)

    with pytest.raises(
        OperatorError, match="unsupported executor: unsupported-executor"
    ):
        accept_candidate(
            "TASK-101",
            executor="unsupported-executor",
            finding_id="F1",
            repo=repo,
        )

    monkeypatch.setattr(
        operator_module,
        "_resolve_remediation_admission",
        lambda *args, **kwargs: SimpleNamespace(
            review=None, remediation=None, task=None, remote_mode=False
        ),
    )
    with pytest.raises(
        OperatorError, match="unsupported executor: unsupported-executor"
    ):
        run_authorized_remediation(
            "TASK-101",
            executor="unsupported-executor",
            finding_id="F1",
            repo=repo,
        )


def test_new_admission_records_antigravity_minimax() -> None:
    admission = operator_module._new_admission(
        "PRIMARY",
        phase="ADMISSION",
        reason_code="READY",
        task_id="TASK-101",
        executor="antigravity-minimax",
    )
    assert admission["requested_executor"] == "antigravity-minimax"

    admission_unsupported = operator_module._new_admission(
        "PRIMARY",
        phase="ADMISSION",
        reason_code="READY",
        task_id="TASK-101",
        executor="unsupported-executor",
    )
    assert "requested_executor" not in admission_unsupported


def test_operator_admits_antigravity_minimax_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    dispatched = []

    class DummyDispatcher:
        def dispatch_primary(self, **kwargs):
            dispatched.append(kwargs)
            raise operator_module.OperatorError("stop after admission")

    monkeypatch.setattr(
        operator_module,
        "primary_dispatcher",
        lambda *, selected_executor, **kwargs: DummyDispatcher()
        if selected_executor == "antigravity-minimax"
        else pytest.fail(f"unexpected executor {selected_executor}"),
    )

    with pytest.raises(operator_module.OperatorError, match="stop after admission"):
        run_task(
            "TASK-101",
            executor="antigravity-minimax",
            repo=repo,
        )
    assert len(dispatched) == 1


def test_remediation_admission_persists_exact_predecessor_identity_ac1(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    sha = git(repo, "rev-parse", "HEAD")
    review, remediation = remediation_contract(repo, reviewed_sha=sha)
    runner = RemediationRunner(repo)

    summary = run_authorized_remediation(
        "TASK-101",
        review=review,
        remediation=remediation,
        executor="antigravity",
        repo=repo,
        native_runner=runner,
    )
    run_file = runtime_paths(repo).runs / f"{summary.run_id}.json"
    run_data = json.loads(run_file.read_text(encoding="utf-8"))

    assert "predecessor" in run_data
    assert run_data["predecessor"] == {
        "source_run_id": "RUN-101-000",
        "review_id": "REVIEW-101-001",
        "finding_id": "R1",
        "reviewed_sha": sha,
    }


def test_remediation_admission_rejects_mismatching_source_run_id(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        task_id="TASK-101",
        task_revision=1,
    )

    with pytest.raises(OperatorError, match="canonical remote remediation lineage not found"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            source_run_id="RUN-101-999",
            executor="antigravity",
            repo=repo,
        )

    sha = git(repo, "rev-parse", "HEAD")
    review, remediation = remediation_contract(repo, reviewed_sha=sha)
    runner = RemediationRunner(repo)
    with pytest.raises(OperatorError, match="source RUN binding requires remote finding mode"):
        run_remediation(
            "TASK-101",
            review=review,
            remediation=remediation,
            source_run_id="RUN-101-999",
            executor="antigravity",
            repo=repo,
            native_runner=runner,
        )


def test_direct_candidate_and_approved_remediation_persist_predecessor_identity_ac2(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    baseline = git(repo, "rev-parse", "HEAD")
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        task_id="TASK-101",
        task_revision=1,
    )

    # 1. Direct candidate acceptance
    (repo / "OUTPUT.txt").write_text("direct candidate output\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "direct candidate fix")

    direct_summary = accept_candidate(
        "TASK-101",
        finding_id="R1",
        executor="antigravity",
        repo=repo,
    )
    direct_run_file = runtime_paths(repo).runs / f"{direct_summary.run_id}.json"
    direct_run_data = json.loads(direct_run_file.read_text(encoding="utf-8"))

    assert direct_run_data["predecessor"] == {
        "source_run_id": "RUN-101-000",
        "review_id": "REVIEW-RUN-101-000",
        "finding_id": "R1",
        "reviewed_sha": baseline,
    }

    # 2. Approved remediation execution
    approval = record_remote_approval(
        repo=repo,
        state_root=runtime_state_root(repo),
        source_run_id="RUN-101-000",
        finding_id="R1",
        approver="human-reviewer",
    )
    assert direct_run_data["remediation_authorization_sha"] == approval.remediation_sha
    git(repo, "reset", "--hard", "--quiet", baseline)
    runner = RemediationRunner(repo)
    approved_summary = run_remediation(
        "TASK-101",
        source_run_id="RUN-101-000",
        finding_id="R1",
        approved_remediation_sha=approval.remediation_sha,
        executor="antigravity",
        repo=repo,
        native_runner=runner,
    )
    approved_run_file = runtime_paths(repo).runs / f"{approved_summary.run_id}.json"
    approved_run_data = json.loads(approved_run_file.read_text(encoding="utf-8"))
    assert approved_run_data["remediation_authorization_sha"] == approval.remediation_sha

    assert approved_run_data["predecessor"] == {
        "source_run_id": "RUN-101-000",
        "review_id": "REVIEW-RUN-101-000",
        "finding_id": "R1",
        "reviewed_sha": baseline,
    }


def test_remediation_revision_1_sibling_resolves_from_cumulative_tip_ac2_ac3(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    primary_sha = git(repo, "rev-parse", "HEAD")
    primary_run_id = "RUN-101-000"
    sibling_run_id = "RUN-101-001"

    prim_run_file = state.runs / f"{primary_run_id}.json"
    prim_res_file = state.results / f"{primary_run_id}.json"
    prim_run_file.write_text(
        json.dumps({
            "run_id": primary_run_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": primary_sha,
            "workspace": str(repo),
            "head_sha": None,
            "status": "ACTIVE",
        }),
        encoding="utf-8",
    )
    payload = static_payload()
    payload["result"]["head_sha"] = primary_sha
    payload["result"]["claims"][0]["evidence"] = ["E1"]
    payload["evidence"] = [{
        "evidence_id": "E1",
        "run_id": primary_run_id,
        "subject_sha": primary_sha,
        "type": "TEST",
        "source": {"command": "git status --porcelain"},
        "result": {"exit_code": 0, "summary": "verified"},
        "raw": {"path": ".ai/evidence/E1.log"},
    }]
    prim_res_file.write_text(json.dumps(payload), encoding="utf-8")
    from aios_renew.review_transport import transport_post_pass
    transport_post_pass(
        repo,
        run_id=primary_run_id,
        head_sha=primary_sha,
        run_path=prim_run_file,
        result_path=prim_res_file,
    )

    author = tmp_path / f"author-{primary_run_id}"
    subprocess.run(
        ("git", "clone", "--quiet", str(tmp_path / "upstream.git"), str(author)),
        check=True,
    )
    git(author, "config", "user.name", "AIOS Reviewer Test")
    git(author, "config", "user.email", "reviewer@example.invalid")
    review_content = f"""review_id: REVIEW-{primary_run_id}
reviewed_sha: {primary_sha}
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
  - id: R2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The second output is absent.
    expected: Commit only the second output.
"""
    for finding in ("R1", "R2"):
        git(author, "checkout", "--quiet", "-B", f"branch-{finding}", "origin/main")
        rev_dir = author / ".ai" / "reviews"
        rem_dir = author / ".ai" / "remediations"
        rev_dir.mkdir(parents=True, exist_ok=True)
        rem_dir.mkdir(parents=True, exist_ok=True)
        (rev_dir / f"REVIEW-{primary_run_id}.yaml").write_text(
            review_content, encoding="utf-8"
        )
        (rem_dir / f"REMEDIATION-{primary_run_id}-{finding}.yaml").write_text(
            f"""finding_id: {finding}
action: CODE_FIX
reviewed_sha: {primary_sha}
modification_scope: [OUTPUT.txt]
affected_verification: [git diff --check]
constraints:
  hard: [Commit the output.]
""",
            encoding="utf-8",
        )
        git(author, "add", ".ai")
        git(author, "commit", "--quiet", "-m", f"remediation {finding}")
        git(author, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/remediation/{primary_run_id}-{finding}")

    git(author, "checkout", "--quiet", "-B", f"decision-{primary_run_id}", "origin/main")
    dec_dir = author / ".ai" / "reviews"
    dec_dir.mkdir(parents=True, exist_ok=True)
    (dec_dir / f"REVIEW-{primary_run_id}.yaml").write_text(review_content, encoding="utf-8")
    git(author, "add", ".ai")
    git(author, "commit", "--quiet", "-m", f"review decision {primary_run_id}")
    git(author, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/review-decision/{primary_run_id}")


    (repo / "OUTPUT.txt").write_text("first correction for R1\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "first correction candidate")
    sibling_sha = git(repo, "rev-parse", "HEAD")

    sib_run_file = state.runs / f"{sibling_run_id}.json"
    sib_res_file = state.results / f"{sibling_run_id}.json"
    sib_run_file.write_text(
        json.dumps({
            "run_id": sibling_run_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "antigravity",
            "base_sha": primary_sha,
            "workspace": str(repo),
            "head_sha": None,
            "status": "ACTIVE",
            "kind": "REMEDIATION",
            "predecessor": {
                "source_run_id": primary_run_id,
                "review_id": f"REVIEW-{primary_run_id}",
                "finding_id": "R1",
                "reviewed_sha": primary_sha,
            },
            "execution_base": {
                "run_id": primary_run_id,
                "candidate_sha": primary_sha,
            },
            "execution": {
                "review_id": f"REVIEW-{primary_run_id}",
                "finding": {
                    "id": "R1", "basis": "AC1", "action": "CODE_FIX",
                    "location": "OUTPUT.txt", "issue": "The output is absent.",
                    "expected": "Commit only the output.",
                },
                "remediation": {
                    "finding_id": "R1", "action": "CODE_FIX",
                    "reviewed_sha": primary_sha,
                    "modification_scope": ["OUTPUT.txt"],
                    "affected_verification": ["git diff --check"],
                    "constraints": {"hard": ["Commit the output."]},
                },
                "run": {
                    "run_id": sibling_run_id,
                    "task": {"id": "TASK-101", "revision": 1},
                    "executor": "antigravity",
                    "base_sha": primary_sha,
                    "workspace": str(repo),
                    "head_sha": None,
                    "status": "ACTIVE",
                },
                "original_constraints": ["Commit the output."],
            },
        }),
        encoding="utf-8",
    )
    sib_payload = static_payload()
    sib_payload["result"]["head_sha"] = sibling_sha
    sib_payload["result"]["claims"] = []
    sib_payload["evidence"] = [{
        "evidence_id": "E2",
        "run_id": sibling_run_id,
        "subject_sha": sibling_sha,
        "type": "TEST",
        "source": {"command": "git diff --check"},
        "result": {"exit_code": 0, "summary": "verified"},
        "raw": {"path": ".ai/evidence/E2.log"},
    }]
    sib_res_file.write_text(json.dumps(sib_payload), encoding="utf-8")
    transport_post_pass(
        repo,
        run_id=sibling_run_id,
        head_sha=sibling_sha,
        run_path=sib_run_file,
        result_path=sib_res_file,
    )

    author_decision = tmp_path / f"decision-{sibling_run_id}"
    subprocess.run(
        ("git", "clone", "--quiet", str(tmp_path / "upstream.git"), str(author_decision)),
        check=True,
    )
    git(author_decision, "config", "user.name", "AIOS Reviewer Test")
    git(author_decision, "config", "user.email", "reviewer@example.invalid")
    dec_review_dir = author_decision / ".ai" / "reviews"
    dec_review_dir.mkdir(parents=True, exist_ok=True)
    (dec_review_dir / f"REVIEW-{sibling_run_id}.yaml").write_text(
        f"""review_id: REVIEW-{sibling_run_id}
reviewed_sha: {sibling_sha}
mode: DELTA
verdict: PASS
prior_finding_id: R1
acceptance:
  AC1: PASS
findings: []
""",
        encoding="utf-8",
    )
    git(author_decision, "add", ".ai")
    git(author_decision, "commit", "--quiet", "-m", f"review decision {sibling_run_id}")
    git(author_decision, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/review-decision/{sibling_run_id}")

    runner = RemediationRunner(repo)
    summary = run_remediation(
        "TASK-101",
        finding_id="R2",
        executor="antigravity",
        repo=repo,
        native_runner=runner,
    )

    assert summary.run_id == "RUN-101-002"
    assert summary.review_id == f"REVIEW-{primary_run_id}"
    assert summary.reviewed_sha == primary_sha
    assert len(runner.calls) == 1

    run_data = json.loads(
        (state.runs / f"{summary.run_id}.json").read_text(encoding="utf-8")
    )
    assert run_data["predecessor"] == {
        "source_run_id": primary_run_id,
        "review_id": f"REVIEW-{primary_run_id}",
        "finding_id": "R2",
        "reviewed_sha": primary_sha,
    }
    assert run_data["execution_base"] == {
        "run_id": sibling_run_id,
        "candidate_sha": sibling_sha,
    }
    assert operator_module._git_is_ancestor(repo, sibling_sha, summary.head_sha)
    assert operator_module._git_is_ancestor(repo, primary_sha, summary.head_sha)


def test_integrated_remediation_repair_completion_preserves_result_base_ac5_ac6(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    publish_test_remediation_lineage(
        repo,
        tmp_path,
        source_run_id="RUN-101-000",
        finding_id="R1",
        reviewed_sha=head,
    )

    orphan = make_repo(tmp_path / "orphan")
    orphan_sha = git(orphan, "rev-parse", "HEAD")
    primary_id = "RUN-101-000"
    primary_run = json.dumps({
        "run_id": primary_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": "bounded-away",
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
    lifecycle_invalid = RemoteTaskLifecycle(
        head,
        (
            RemoteLifecycleTerminal(
                primary_id, "RESULT", orphan_sha, primary_run,
                json.dumps(canonical_result_payload(primary_id, orphan_sha)).encode(),
            ),
        ),
        (RemoteLifecycleReview(primary_id, orphan_sha, review),),
        (), (), (),
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_task_lifecycle",
        lambda *_args, **_kwargs: lifecycle_invalid,
    )

    runner = RemediationRunner(repo)
    with pytest.raises(OperatorError, match="cumulative execution base rejected"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="antigravity",
            repo=repo,
            native_runner=runner,
        )
    assert len(runner.calls) == 0

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
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_task_lifecycle",
        lambda *_args, **_kwargs: lifecycle_integration,
    )

    with pytest.raises(OperatorError, match="cumulative execution base rejected"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="antigravity",
            repo=repo,
            native_runner=runner,
        )
    assert len(runner.calls) == 0

    # After explicit integration, run_remediation succeeds using integrated base
    from aios_renew.correction_integration import integrate_correction
    int_result = integrate_correction(
        "TASK-101",
        task_revision=1,
        cumulative_tip_run_id=primary_id,
        cumulative_tip_candidate_sha=head,
        authorized_main_sha=new_main,
        repo=repo,
    )
    isolated_runner = IsolatedRemediationRunner(int_result.integration_candidate_sha)

    def fail_verification(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=9, stdout=b"", stderr=b"repair required\n"
        )

    with pytest.raises(OperatorError, match="exit code 9"):
        run_remediation(
            "TASK-101",
            finding_id="R1",
            executor="codex",
            repo=repo,
            native_runner=isolated_runner,
            verification_runner=fail_verification,
        )
    assert len(isolated_runner.calls) == 1
    state = runtime_paths(repo)
    run_doc = json.loads(
        (state.runs / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert run_doc["execution_base"]["kind"] == "INTEGRATED"
    assert run_doc["execution_base"]["integration_candidate_sha"] == int_result.integration_candidate_sha
    assert run_doc["execution"]["run"]["base_sha"] == int_result.integration_candidate_sha

    failed_remediation = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    repair = {
        "repair_id": "REPAIR-101-001",
        "failed_run_id": "RUN-101-001",
        "failed_head_sha": failed_remediation["failed_head_sha"],
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Correct the integrated remediation candidate."],
        "constraints": ["Commit the output."],
    }
    first_repair_runner = WorkspaceRepairRunner()
    with pytest.raises(OperatorError, match="exit code 9"):
        run_repair(
            "RUN-101-001",
            executor="codex",
            repo=repo,
            repair=repair,
            native_runner=first_repair_runner,
            verification_runner=fail_verification,
        )

    first_repair = json.loads(
        (state.repairs / "RUN-101-002.json").read_text(encoding="utf-8")
    )
    assert first_repair["root_base_sha"] == head
    assert first_repair["result_base_sha"] == int_result.integration_candidate_sha
    assert first_repair["run"]["base_sha"] == failed_remediation["failed_head_sha"]

    continuation_failure = json.loads(
        (state.failures / "RUN-101-002.json").read_text(encoding="utf-8")
    )
    continuation = {
        **repair,
        "repair_id": "REPAIR-101-002",
        "failed_run_id": "RUN-101-002",
        "failed_head_sha": continuation_failure["failed_head_sha"],
    }
    continuation_repair_runner = WorkspaceRepairRunner()
    completed = run_repair(
        "RUN-101-002",
        executor="codex",
        repo=repo,
        repair=continuation,
        native_runner=continuation_repair_runner,
    )

    continued_repair = json.loads(
        (state.repairs / f"{completed.run_id}.json").read_text(encoding="utf-8")
    )
    result = json.loads(completed.result_path.read_text(encoding="utf-8"))
    assert completed.run_id == "RUN-101-003"
    assert continued_repair["root_base_sha"] == head
    assert continued_repair["result_base_sha"] == int_result.integration_candidate_sha
    assert result["result"]["changed_files"] == ["OUTPUT.txt"]
    assert git(
        repo,
        "diff",
        "--name-only",
        int_result.integration_candidate_sha,
        completed.head_sha,
    ).splitlines() == ["OUTPUT.txt"]


def test_integrated_evidence_only_finalize_preserves_origin_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    reviewed_sha = git(repo, "rev-parse", "HEAD")
    source_run_id = "RUN-101-000"
    source_run = {
        "run_id": source_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": reviewed_sha,
        "workspace": "canonical-source",
        "head_sha": None,
        "status": "ACTIVE",
    }
    source_result = result_payload(source_run_id, reviewed_sha, changed_files=[])
    source_result["result"]["claims"][0]["evidence"] = ["E-SOURCE"]
    source_result["evidence"] = [{
        "evidence_id": "E-SOURCE",
        "run_id": source_run_id,
        "subject_sha": reviewed_sha,
        "type": "TEST",
        "source": {"command": "git status --porcelain"},
        "result": {"exit_code": 0, "summary": "verified"},
        "raw": {"path": ".ai/evidence/E-SOURCE.log"},
    }]
    review = {
        "review_id": "REVIEW-101-EVIDENCE",
        "reviewed_sha": reviewed_sha,
        "mode": "PRIMARY",
        "verdict": "CHANGES_REQUIRED",
        "acceptance": {"AC1": "FAIL"},
        "findings": [{
            "id": "R-EVIDENCE",
            "basis": "AC1",
            "action": "EVIDENCE_ONLY",
            "location": "OUTPUT.txt",
            "issue": "Independent live evidence is required.",
            "expected": "Collect the exact live proof.",
        }],
    }
    remediation = {
        "finding_id": "R-EVIDENCE",
        "action": "EVIDENCE_ONLY",
        "reviewed_sha": reviewed_sha,
        "modification_scope": [],
        "affected_verification": ["git diff --check"],
        "constraints": ["Commit the output."],
    }
    source_lineage = RemoteRemediationLineage(
        ref="refs/heads/aios/remediation/RUN-101-000-R-EVIDENCE",
        source_run_id=source_run_id,
        review=json.dumps(review).encode(),
        remediation=json.dumps(remediation).encode(),
        run=json.dumps(source_run).encode(),
        result=json.dumps(source_result).encode(),
        repair=None,
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_remediation_lineages",
        lambda repo, *, finding_id, **kwargs: (source_lineage,),
    )

    (repo / "MAIN.txt").write_text("integrated main\n", encoding="utf-8")
    git(repo, "add", "MAIN.txt")
    git(repo, "commit", "--quiet", "-m", "integrated execution base")
    integrated_sha = git(repo, "rev-parse", "HEAD")
    integration_id = "INTEGRATION-101-EVIDENCE"
    lifecycle = RemoteTaskLifecycle(
        integrated_sha,
        (RemoteLifecycleTerminal(
            source_run_id,
            "RESULT",
            reviewed_sha,
            json.dumps(source_run).encode(),
            json.dumps(source_result).encode(),
        ),),
        (), (), (), (),
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_remote_task_lifecycle",
        lambda *_args, **_kwargs: lifecycle,
    )
    monkeypatch.setattr(
        operator_module,
        "resolve_valid_integration",
        lambda *_args, **_kwargs: SimpleNamespace(
            integration_id=integration_id,
            integration_candidate_sha=integrated_sha,
        ),
    )

    failed_run_id = "RUN-101-001"
    origin_run = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": integrated_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    (state.runs / f"{failed_run_id}.json").write_text(
        json.dumps({
            "kind": "REMEDIATION",
            "execution_base": {
                "version": 1,
                "kind": "INTEGRATED",
                "cumulative_tip_run_id": source_run_id,
                "cumulative_tip_candidate_sha": reviewed_sha,
                "authorized_main_sha": integrated_sha,
                "integration_candidate_sha": integrated_sha,
                "integration_id": integration_id,
            },
            "execution": {
                "review_id": review["review_id"],
                "finding": review["findings"][0],
                "remediation": remediation,
                "run": origin_run,
                "original_constraints": remediation["constraints"],
            },
        }),
        encoding="utf-8",
    )
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": integrated_sha,
        "failed_head_sha": integrated_sha,
        "phase": "COMPLETION_GATE",
        "error": {"type": "OperatorError", "message": "RESULT.head_sha mismatch"},
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }
    (state.failures / f"{failed_run_id}.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )
    repair = {
        "repair_id": "REPAIR-101-EVIDENCE",
        "failed_run_id": failed_run_id,
        "failed_head_sha": integrated_sha,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "FINALIZE_CANDIDATE",
        "modification_scope": [],
        "instructions": ["Return the complete structural TASK package."],
        "constraints": ["Commit the output."],
    }
    executor_payloads = []

    def finalize_runner(command, **kwargs):
        payload = kwargs["input"].decode().split("REPAIR_INPUT:\n", 1)[1]
        executor_payloads.append(payload)
        execution = json.loads(payload)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(result_payload(
                execution["run"]["run_id"], integrated_sha, changed_files=[]
            )),
            stderr="",
        )

    verification_calls = []

    def verify(command, **kwargs):
        verification_calls.append(command)
        verification_subject = kwargs["cwd"]
        assert verification_subject != repo
        assert git(verification_subject, "rev-parse", "HEAD") == integrated_sha
        assert git(repo, "rev-parse", "HEAD") == integrated_sha
        return subprocess.CompletedProcess(command, 0, stdout=b"ok\n", stderr=b"")

    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair=repair,
        native_runner=finalize_runner,
        verification_runner=verify,
    )

    repaired = json.loads(summary.result_path.read_text(encoding="utf-8"))
    lineage = json.loads(
        (state.repairs / f"{summary.run_id}.json").read_text(encoding="utf-8")
    )
    assert summary.head_sha == integrated_sha == git(repo, "rev-parse", "HEAD")
    assert lineage["result_base_sha"] == integrated_sha
    assert repaired["result"]["changed_files"] == []
    assert len(verification_calls) == 2
    assert [item["source"]["command"] for item in repaired["evidence"]] == [
        "git status --porcelain",
        "git diff --check",
    ]
    assert {item["run_id"] for item in repaired["evidence"]} == {summary.run_id}
    assert {item["subject_sha"] for item in repaired["evidence"]} == {integrated_sha}
    assert "affected_verification" not in executor_payloads[0]
    assert "git diff --check" not in executor_payloads[0]


def test_v2_task_authorization_allows_newer_main_with_same_task_blob(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    authorized_commit = git(repo, "rev-parse", "HEAD")
    task_blob = git(repo, "rev-parse", "HEAD:.ai/tasks/TASK-101.yaml")
    (repo / "UNRELATED.txt").write_text("new main content\n", encoding="utf-8")
    git(repo, "add", "UNRELATED.txt")
    git(repo, "commit", "-m", "advance unrelated main")
    current_head = git(repo, "rev-parse", "HEAD")
    admission: dict[str, object] = {}

    task = operator_module._admit_authorized_task_identity(
        repo,
        task_id="TASK-101",
        task_revision=1,
        task_blob_sha=task_blob,
        task_commit_sha=authorized_commit,
        current_head=current_head,
        admission=admission,
    )

    assert task.task_id == "TASK-101"
    assert task.revision == 1
    assert admission["current_task_blob_sha"] == task_blob


def test_v2_task_authorization_rejects_revision_content_and_provenance_drift(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    authorized_commit = git(repo, "rev-parse", "HEAD")
    task_blob = git(repo, "rev-parse", "HEAD:.ai/tasks/TASK-101.yaml")

    with pytest.raises(OperatorError, match="id or revision"):
        operator_module._admit_authorized_task_identity(
            repo,
            task_id="TASK-101",
            task_revision=2,
            task_blob_sha=task_blob,
            task_commit_sha=authorized_commit,
            current_head=authorized_commit,
            admission={},
        )

    task_path = repo / ".ai" / "tasks" / "TASK-101.yaml"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "Create one deterministic operator test output.",
            "Create changed deterministic operator semantics.",
        ),
        encoding="utf-8",
    )
    git(repo, "add", ".ai/tasks/TASK-101.yaml")
    git(repo, "commit", "-m", "drift task content")
    current_head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(OperatorError, match="blob does not match"):
        operator_module._admit_authorized_task_identity(
            repo,
            task_id="TASK-101",
            task_revision=1,
            task_blob_sha=task_blob,
            task_commit_sha=authorized_commit,
            current_head=current_head,
            admission={},
        )

    tree = git(repo, "rev-parse", f"{authorized_commit}^{{tree}}")
    non_ancestor = git(repo, "commit-tree", tree, "-m", "unrelated provenance")
    with pytest.raises(OperatorError, match="not an ancestor"):
        operator_module._admit_authorized_task_identity(
            repo,
            task_id="TASK-101",
            task_revision=1,
            task_blob_sha=task_blob,
            task_commit_sha=non_ancestor,
            current_head=current_head,
            admission={},
        )


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("revision", "id or revision"),
        ("content", "blob does not match"),
        ("provenance", "not an ancestor"),
    ],
)
def test_v2_authorized_task_identity_drift_fails_before_run_and_executor(
    tmp_path: Path,
    drift: str,
    message: str,
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    authorized_commit = git(repo, "rev-parse", "HEAD")
    task_blob = git(repo, "rev-parse", "HEAD:.ai/tasks/TASK-101.yaml")
    task_revision = 1
    task_commit = authorized_commit

    if drift == "revision":
        task_revision = 2
    elif drift == "content":
        task_path = repo / ".ai" / "tasks" / "TASK-101.yaml"
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace(
                "Create one deterministic operator test output.",
                "Create changed deterministic operator semantics.",
            ),
            encoding="utf-8",
        )
        git(repo, "add", ".ai/tasks/TASK-101.yaml")
        git(repo, "commit", "-m", "drift authorized task content")
    else:
        tree = git(repo, "rev-parse", f"{authorized_commit}^{{tree}}")
        task_commit = git(repo, "commit-tree", tree, "-m", "unrelated provenance")

    executor_calls = []

    def runner(command, **kwargs):
        executor_calls.append((command, kwargs))
        raise AssertionError("executor must not be invoked")

    preflight_sha = git(repo, "rev-parse", "HEAD")
    with pytest.raises(OperatorError, match=message):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=runner,
            synchronize=False,
            preflight_sha=preflight_sha,
            task_revision=task_revision,
            task_blob_sha=task_blob,
            task_commit_sha=task_commit,
        )

    assert executor_calls == []
    assert not list(state.runs.glob("*.json"))
    records = admission_failure_records(repo)
    assert len(records) == 1
    assert records[0]["format"] == "AIOS_ADMISSION_FAILURE"
    assert records[0]["version"] == 2
    assert records[0]["kind"] == "ADMISSION_FAILURE"
    assert records[0]["executor_invoked"] is False
    assert records[0]["phase"] == "TASK_ADMISSION"
    assert records[0]["reason_code"] == "AUTHORIZED_TASK_IDENTITY_MISMATCH"


def test_remediation_direct_candidate_revision_1_sibling_preserves_cumulative_tip_ac2_ac3(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    primary_sha = git(repo, "rev-parse", "HEAD")
    primary_run_id = "RUN-101-000"
    sibling_run_id = "RUN-101-001"

    prim_run_file = state.runs / f"{primary_run_id}.json"
    prim_res_file = state.results / f"{primary_run_id}.json"
    prim_run_file.write_text(
        json.dumps({
            "run_id": primary_run_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex",
            "base_sha": primary_sha,
            "workspace": str(repo),
            "head_sha": None,
            "status": "ACTIVE",
        }),
        encoding="utf-8",
    )
    payload = static_payload()
    payload["result"]["head_sha"] = primary_sha
    payload["result"]["claims"][0]["evidence"] = ["E1"]
    payload["evidence"] = [{
        "evidence_id": "E1",
        "run_id": primary_run_id,
        "subject_sha": primary_sha,
        "type": "TEST",
        "source": {"command": "git status --porcelain"},
        "result": {"exit_code": 0, "summary": "verified"},
        "raw": {"path": ".ai/evidence/E1.log"},
    }]
    prim_res_file.write_text(json.dumps(payload), encoding="utf-8")
    from aios_renew.review_transport import transport_post_pass
    transport_post_pass(
        repo,
        run_id=primary_run_id,
        head_sha=primary_sha,
        run_path=prim_run_file,
        result_path=prim_res_file,
    )

    author = tmp_path / f"author-{primary_run_id}"
    subprocess.run(
        ("git", "clone", "--quiet", str(tmp_path / "upstream.git"), str(author)),
        check=True,
    )
    git(author, "config", "user.name", "AIOS Reviewer Test")
    git(author, "config", "user.email", "reviewer@example.invalid")
    review_content = f"""review_id: REVIEW-{primary_run_id}
reviewed_sha: {primary_sha}
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
  - id: R2
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The second output is absent.
    expected: Commit only the second output.
"""
    for finding in ("R1", "R2"):
        git(author, "checkout", "--quiet", "-B", f"branch-{finding}", "origin/main")
        rev_dir = author / ".ai" / "reviews"
        rem_dir = author / ".ai" / "remediations"
        rev_dir.mkdir(parents=True, exist_ok=True)
        rem_dir.mkdir(parents=True, exist_ok=True)
        (rev_dir / f"REVIEW-{primary_run_id}.yaml").write_text(
            review_content, encoding="utf-8"
        )
        (rem_dir / f"REMEDIATION-{primary_run_id}-{finding}.yaml").write_text(
            f"""finding_id: {finding}
action: CODE_FIX
reviewed_sha: {primary_sha}
modification_scope: [OUTPUT.txt]
affected_verification: [git diff --check]
constraints:
  hard: [Commit the output.]
""",
            encoding="utf-8",
        )
        git(author, "add", ".ai")
        git(author, "commit", "--quiet", "-m", f"remediation {finding}")
        git(author, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/remediation/{primary_run_id}-{finding}")

    git(author, "checkout", "--quiet", "-B", f"decision-{primary_run_id}", "origin/main")
    dec_dir = author / ".ai" / "reviews"
    dec_dir.mkdir(parents=True, exist_ok=True)
    (dec_dir / f"REVIEW-{primary_run_id}.yaml").write_text(review_content, encoding="utf-8")
    git(author, "add", ".ai")
    git(author, "commit", "--quiet", "-m", f"review decision {primary_run_id}")
    git(author, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/review-decision/{primary_run_id}")



    (repo / "OUTPUT.txt").write_text("first correction for R1\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "first correction candidate")
    sibling_sha = git(repo, "rev-parse", "HEAD")

    sib_run_file = state.runs / f"{sibling_run_id}.json"
    sib_res_file = state.results / f"{sibling_run_id}.json"
    sib_run_file.write_text(
        json.dumps({
            "run_id": sibling_run_id,
            "task": {"id": "TASK-101", "revision": 1},
            "executor": "antigravity",
            "base_sha": primary_sha,
            "workspace": str(repo),
            "head_sha": None,
            "status": "ACTIVE",
            "kind": "REMEDIATION",
            "predecessor": {
                "source_run_id": primary_run_id,
                "review_id": f"REVIEW-{primary_run_id}",
                "finding_id": "R1",
                "reviewed_sha": primary_sha,
            },
            "execution_base": {
                "run_id": primary_run_id,
                "candidate_sha": primary_sha,
            },
            "execution": {
                "review_id": f"REVIEW-{primary_run_id}",
                "finding": {
                    "id": "R1", "basis": "AC1", "action": "CODE_FIX",
                    "location": "OUTPUT.txt", "issue": "The output is absent.",
                    "expected": "Commit only the output.",
                },
                "remediation": {
                    "finding_id": "R1", "action": "CODE_FIX",
                    "reviewed_sha": primary_sha,
                    "modification_scope": ["OUTPUT.txt"],
                    "affected_verification": ["git diff --check"],
                    "constraints": {"hard": ["Commit the output."]},
                },
                "run": {
                    "run_id": sibling_run_id,
                    "task": {"id": "TASK-101", "revision": 1},
                    "executor": "antigravity",
                    "base_sha": primary_sha,
                    "workspace": str(repo),
                    "head_sha": None,
                    "status": "ACTIVE",
                },
                "original_constraints": ["Commit the output."],
            },
        }),
        encoding="utf-8",
    )
    sib_payload = static_payload()
    sib_payload["result"]["head_sha"] = sibling_sha
    sib_payload["result"]["claims"] = []
    sib_payload["evidence"] = [{
        "evidence_id": "E2",
        "run_id": sibling_run_id,
        "subject_sha": sibling_sha,
        "type": "TEST",
        "source": {"command": "git diff --check"},
        "result": {"exit_code": 0, "summary": "verified"},
        "raw": {"path": ".ai/evidence/E2.log"},
    }]
    sib_res_file.write_text(json.dumps(sib_payload), encoding="utf-8")
    transport_post_pass(
        repo,
        run_id=sibling_run_id,
        head_sha=sibling_sha,
        run_path=sib_run_file,
        result_path=sib_res_file,
    )

    author_decision = tmp_path / f"decision-{sibling_run_id}"
    subprocess.run(
        ("git", "clone", "--quiet", str(tmp_path / "upstream.git"), str(author_decision)),
        check=True,
    )
    git(author_decision, "config", "user.name", "AIOS Reviewer Test")
    git(author_decision, "config", "user.email", "reviewer@example.invalid")
    dec_review_dir = author_decision / ".ai" / "reviews"
    dec_review_dir.mkdir(parents=True, exist_ok=True)
    (dec_review_dir / f"REVIEW-{sibling_run_id}.yaml").write_text(
        f"""review_id: REVIEW-{sibling_run_id}
reviewed_sha: {sibling_sha}
mode: DELTA
verdict: PASS
prior_finding_id: R1
acceptance:
  AC1: PASS
findings: []
""",
        encoding="utf-8",
    )
    git(author_decision, "add", ".ai")
    git(author_decision, "commit", "--quiet", "-m", f"review decision {sibling_run_id}")
    git(author_decision, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/review-decision/{sibling_run_id}")

    (repo / "OUTPUT.txt").write_text("second correction for R2\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "second direct candidate fix")

    direct_summary = accept_candidate(
        "TASK-101",
        finding_id="R2",
        executor="antigravity",
        repo=repo,
    )
    direct_run_file = state.runs / f"{direct_summary.run_id}.json"
    direct_run_data = json.loads(direct_run_file.read_text(encoding="utf-8"))

    assert direct_run_data["predecessor"] == {
        "source_run_id": primary_run_id,
        "review_id": f"REVIEW-{primary_run_id}",
        "finding_id": "R2",
        "reviewed_sha": primary_sha,
    }
    assert direct_run_data["execution_base"] == {
        "run_id": sibling_run_id,
        "candidate_sha": sibling_sha,
    }
    assert operator_module._git_is_ancestor(repo, sibling_sha, direct_summary.head_sha)
    assert operator_module._git_is_ancestor(repo, primary_sha, direct_summary.head_sha)


def test_operator_execution_base_parser_is_exact_and_rejects_aliases() -> None:
    parsed = operator_module._parse_remediation_execution_base({
        "run_id": "RUN-101-001", "candidate_sha": "a" * 40,
    })
    assert parsed.run_id == "RUN-101-001"
    assert parsed.candidate_sha == "a" * 40
    with pytest.raises(ValueError, match="fields do not match"):
        operator_module._parse_remediation_execution_base({
            "run_id": "RUN-101-001", "candidate_sha": "a" * 40,
            "base_sha": "a" * 40,
        })
    with pytest.raises(ValueError, match="fields do not match"):
        operator_module._parse_remediation_execution_base({
            "source_run_id": "RUN-101-001", "candidate_sha": "a" * 40,
        })


def test_operator_predecessor_parser_rejects_alias_and_conflicting_fields() -> None:
    valid_payload = {
        "source_run_id": "RUN-101-000",
        "review_id": "REVIEW-101-001",
        "finding_id": "R1",
        "reviewed_sha": "0" * 40,
    }
    parsed = operator_module._parse_remediation_predecessor(valid_payload)
    assert parsed.source_run_id == "RUN-101-000"
    assert parsed.review_id == "REVIEW-101-001"
    assert parsed.finding_id == "R1"
    assert parsed.reviewed_sha == "0" * 40

    # Non-mapping input fails closed
    with pytest.raises(TypeError, match="REMEDIATION predecessor must be a mapping"):
        operator_module._parse_remediation_predecessor("not-a-mapping")

    # Alternate alias keys fail closed
    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        operator_module._parse_remediation_predecessor({
            "run_id": "RUN-101-000",
            "review_id": "REVIEW-101-001",
            "finding_id": "R1",
            "reviewed_sha": "0" * 40,
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        operator_module._parse_remediation_predecessor({
            "source_run_id": "RUN-101-000",
            "source_review_id": "REVIEW-101-001",
            "finding_id": "R1",
            "reviewed_sha": "0" * 40,
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        operator_module._parse_remediation_predecessor({
            "source_run_id": "RUN-101-000",
            "review_id": "REVIEW-101-001",
            "selected_finding_id": "R1",
            "reviewed_sha": "0" * 40,
        })

    # Conflicting keys fail closed
    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        operator_module._parse_remediation_predecessor({
            **valid_payload,
            "run_id": "RUN-101-999",
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        operator_module._parse_remediation_predecessor({
            **valid_payload,
            "source_review_id": "REVIEW-101-999",
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        operator_module._parse_remediation_predecessor({
            **valid_payload,
            "selected_finding_id": "R2",
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        operator_module._parse_remediation_predecessor({
            **valid_payload,
            "extra_field": "disallowed",
        })


def test_operator_predecessor_canonical_run_identity_consistent_with_repository_grammar() -> None:
    # Canonically admissible RUN identities supported by publication and task_run_prefix
    admissible_run_ids = [
        "RUN-066-1",
        "RUN-066-001",
        "RUN-task.1-001",
        "RUN-101-candidate",
        "RUN-A_B-001",
        "RUN-TASK-999-1",
    ]
    for run_id in admissible_run_ids:
        payload = {
            "source_run_id": run_id,
            "review_id": "REVIEW-066-001",
            "finding_id": "R1",
            "reviewed_sha": "a" * 40,
        }
        parsed = operator_module._parse_remediation_predecessor(payload)
        assert parsed.source_run_id == run_id

    # Inadmissible / invalid RUN identities fail closed
    invalid_run_ids = [
        "NOT-A-RUN",
        "RUN-",
        "RUN-/slash",
        "RUN-\\backslash",
        "TASK-101",
        "RUN-@bad",
    ]
    for invalid_id in invalid_run_ids:
        payload = {
            "source_run_id": invalid_id,
            "review_id": "REVIEW-066-001",
            "finding_id": "R1",
            "reviewed_sha": "a" * 40,
        }
        with pytest.raises(
            ValueError,
            match="REMEDIATION predecessor source RUN identity is invalid",
        ):
            operator_module._parse_remediation_predecessor(payload)


def test_operator_prior_result_fails_closed_on_conflicting_or_alias_predecessor(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    task = load_task(repo, "TASK-101")
    sha = git(repo, "rev-parse", "HEAD")
    review, remediation = remediation_contract(repo, reviewed_sha=sha)
    runner = RemediationRunner(repo)

    summary = run_authorized_remediation(
        "TASK-101",
        review=review,
        remediation=remediation,
        executor="antigravity",
        repo=repo,
        native_runner=runner,
    )
    run_file = state.runs / f"{summary.run_id}.json"
    run_data = json.loads(run_file.read_text(encoding="utf-8"))

    # Legacy path: omitting predecessor entirely succeeds
    legacy_data = dict(run_data)
    del legacy_data["predecessor"]
    run_file.write_text(json.dumps(legacy_data), encoding="utf-8")
    result, run_id = operator_module._load_authoritative_prior_result(
        state, task, summary.head_sha, repo=repo
    )
    assert run_id == summary.run_id

    # Corrupt predecessor with conflicting alias field fails closed
    conflicted_data = dict(run_data)
    conflicted_data["predecessor"] = {
        **run_data["predecessor"],
        "run_id": "RUN-101-999",
    }
    run_file.write_text(json.dumps(conflicted_data), encoding="utf-8")
    with pytest.raises(
        OperatorError, match="authoritative prior RESULT lineage mismatch"
    ):
        operator_module._load_authoritative_prior_result(
            state, task, summary.head_sha, repo=repo
        )


@pytest.mark.parametrize(
    "local_task_source",
    [
        "task_id: TASK-101\n",
        TASK_SOURCE,
    ],
    ids=["malformed-local-task", "older-valid-local-task"],
)
def test_continue_refreshes_existing_stale_task_before_parsing_and_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_task_source: str,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path, task_source=local_task_source)
    synchronized_task = TASK_SOURCE.replace("revision: 1", "revision: 2")
    synchronized_sha = publish_upstream(
        repo,
        {
            ".ai/tasks/TASK-101.yaml": synchronized_task,
            "src/aios_renew/synchronized_marker.py": "# synchronized kernel\n",
        },
        "publish corrected current task and kernel",
    )
    argv = ["continue", "TASK-101", "--repo", str(repo)]
    events: list[tuple[str, str]] = []
    child_results = []
    git_calls = []
    real_git = operator_module._git
    real_observe = operator_module.observe_unified_state

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    def observe(task_id, *, repo=None):
        events.append(("observe", git(repo, "rev-parse", "HEAD")))
        return real_observe(task_id, repo=repo)

    def restart(root, *, argv=None, runner=subprocess.run):
        events.append(("restart", git(root, "rev-parse", "HEAD")))
        monkeypatch.setenv("AIOS_RESTART_ATTEMPTED", "1")
        child_results.append(
            operator_module.continue_task("TASK-101", repo=root, argv=argv)
        )
        return child_results[-1][1]

    monkeypatch.setattr(operator_module, "_git", recording_git)
    monkeypatch.setattr(operator_module, "observe_unified_state", observe)
    monkeypatch.setattr(operator_module, "_restart_primary_invocation", restart)

    parent, exit_code = operator_module.continue_task(
        "TASK-101", repo=repo, argv=argv
    )

    assert parent is None
    assert exit_code == 0
    assert events == [
        ("restart", synchronized_sha),
        ("observe", synchronized_sha),
    ]
    assert len(child_results) == 1
    child, child_code = child_results[0]
    assert child_code == 0
    assert child is not None
    assert child.task_revision == 2
    assert child.disposition == "EXECUTOR_REQUIRED"
    assert load_task(repo, "TASK-101").revision == 2
    assert len(
        [args for args in git_calls if args[:2] == ("merge", "--ff-only")]
    ) == 1
    assert not list(runtime_paths(repo).runs.glob("*.json"))


@pytest.mark.parametrize(
    "state", ["equal", "ahead", "diverged", "dirty"]
)
def test_continue_pre_observation_preserves_non_behind_valid_task_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path)
    if state == "ahead":
        git(repo, "commit", "--allow-empty", "--quiet", "-m", "local ahead")
    elif state == "diverged":
        publish_upstream(repo, {"REMOTE.txt": "remote\n"}, "remote advance")
        git(repo, "commit", "--allow-empty", "--quiet", "-m", "local advance")
    elif state == "dirty":
        (repo / "DIRTY.txt").write_text("dirty\n", encoding="utf-8")
    before_head = git(repo, "rev-parse", "HEAD")
    before_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    before_status = git(repo, "status", "--porcelain")
    git_calls = []
    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", recording_git)

    outcome, exit_code = operator_module.continue_task("TASK-101", repo=repo)

    assert exit_code == 0
    assert outcome is not None
    assert outcome.task_revision == 1
    assert outcome.disposition == "EXECUTOR_REQUIRED"
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    assert git(repo, "status", "--porcelain") == before_status
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not any(args[:2] == ("merge", "--ff-only") for args in git_calls)
    prohibited = {
        "read-tree", "update-ref", "rebase", "reset", "checkout", "stash",
        "clean", "pull", "push",
    }
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_continue_detached_observes_then_rejects_primary_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path)
    git(repo, "checkout", "--quiet", "--detach")

    before_head = git(repo, "rev-parse", "HEAD")
    before_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    before_status = git(repo, "status", "--porcelain")
    assert before_branch == "HEAD"
    assert before_status == ""
    git_calls = []
    observations = []
    real_git = operator_module._git
    real_observe = operator_module.observe_unified_state

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    def observe(task_id, *, repo=None):
        observation = real_observe(task_id, repo=repo)
        observations.append(observation)
        return observation

    monkeypatch.setattr(operator_module, "_git", recording_git)
    monkeypatch.setattr(operator_module, "observe_unified_state", observe)
    monkeypatch.setattr(
        operator_module,
        "run_task",
        lambda *args, **kwargs: pytest.fail("Executor must not be invoked"),
    )

    outcome, exit_code = operator_module.continue_task(
        "TASK-101", executor="codex", repo=repo
    )

    assert len(observations) == 1
    assert observations[0].next_action == "EXECUTE_PRIMARY"
    assert exit_code == 1
    assert outcome is not None
    assert outcome.next_action == "EXECUTE_PRIMARY"
    assert outcome.disposition == "DELEGATED"
    assert outcome.authority == "RUNTIME"
    assert outcome.delegated_operation == "PRIMARY"
    assert outcome.resulting_run_id is None
    assert outcome.resulting_head_sha is None
    assert outcome.blocker == {
        "code": "PRIMARY_SYNCHRONIZATION_REJECTED",
        "phase": "PRIMARY_SYNCHRONIZATION",
        "reason_code": "PRIMARY_SYNCHRONIZATION_REJECTED",
        "source": "AIOS_ADMISSION_FAILURE",
    }

    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    assert git(repo, "status", "--porcelain") == before_status
    state = runtime_paths(repo)
    for directory in (state.runs, state.results, state.failures, state.verification):
        assert not list(directory.glob("*.json"))
    diagnostics = admission_failure_records(repo)
    assert len(diagnostics) == 1
    assert diagnostics[0]["format"] == "AIOS_ADMISSION_FAILURE"
    assert diagnostics[0]["version"] == 2
    assert diagnostics[0]["kind"] == "ADMISSION_FAILURE"
    assert diagnostics[0]["operation"] == "PRIMARY"
    assert diagnostics[0]["phase"] == "PRIMARY_SYNCHRONIZATION"
    assert diagnostics[0]["reason_code"] == "PRIMARY_SYNCHRONIZATION_REJECTED"
    assert diagnostics[0]["executor_invoked"] is False
    assert not any(args[:2] == ("merge", "--ff-only") for args in git_calls)
    prohibited = {
        "read-tree", "update-ref", "rebase", "reset", "checkout", "stash",
        "clean", "pull", "push",
    }
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_continue_pre_observation_preserves_non_main_branch_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path)
    git(repo, "checkout", "--quiet", "-b", "feature")

    before_head = git(repo, "rev-parse", "HEAD")
    before_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    before_status = git(repo, "status", "--porcelain")
    assert before_branch == "feature"
    assert before_status == ""
    git_calls = []
    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", recording_git)
    monkeypatch.setattr(
        operator_module,
        "run_task",
        lambda *args, **kwargs: pytest.fail("Executor must not be invoked"),
    )

    with pytest.raises(OperatorError, match="Git command failed"):
        operator_module.continue_task("TASK-101", executor="codex", repo=repo)

    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    assert git(repo, "status", "--porcelain") == before_status
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not admission_failure_records(repo)
    assert not any(args[:2] == ("merge", "--ff-only") for args in git_calls)
    prohibited = {
        "read-tree", "update-ref", "rebase", "reset", "checkout", "stash",
        "clean", "pull", "push",
    }
    assert not any(args and args[0] in prohibited for args in git_calls)


def _migration_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Two unrelated Git repositories model control and exact package source."""

    target_source = tmp_path / "reviewed-generation"
    target_source.mkdir()
    git(target_source, "init", "-b", "main")
    git(target_source, "config", "user.name", "Migration Test")
    git(target_source, "config", "user.email", "migration@example.invalid")
    operator_path = target_source / "src" / "aios_renew" / "operator.py"
    operator_path.parent.mkdir(parents=True)
    operator_path.write_text("# exact reviewed target\n", encoding="utf-8")
    git(target_source, "add", ".")
    git(target_source, "commit", "-m", "reviewed generation")
    target_generation = git(target_source, "rev-parse", "HEAD")

    repo = make_repo(tmp_path / "control")
    source_generation = "1" * 40
    source_control = publish_upstream(
        repo, {"AIOS_PIN": f"aios-renew @ {source_generation}\n"}, "active pin"
    )
    git(repo, "fetch", "origin", "main")
    git(repo, "merge", "--ff-only", "origin/main")
    target_task = TASK_SOURCE.replace("revision: 1", "revision: 2")
    target_control = publish_upstream(
        repo,
        {
            "AIOS_PIN": f"aios-renew @ {target_generation}\n",
            ".ai/tasks/TASK-101.yaml": target_task,
            "src/aios_renew/target_marker.py": "# newer package source\n",
        },
        "reviewed downstream migration",
    )
    publisher = repo.parent / "publisher"
    intent = {
        "version": 1,
        "source_generation_sha": source_generation,
        "target_generation_sha": target_generation,
        "target_url": str(target_source),
        "repository": str(repo.resolve()),
        "source_control_sha": source_control,
        "target_control_sha": target_control,
        "pin_path": "AIOS_PIN",
        "source_pin_blob_sha": git(repo, "rev-parse", f"{source_control}:AIOS_PIN"),
        "target_pin_blob_sha": git(publisher, "rev-parse", f"{target_control}:AIOS_PIN"),
        "task_id": "TASK-101",
        "task_revision": 2,
        "task_blob_sha": git(publisher, "rev-parse", f"{target_control}:.ai/tasks/TASK-101.yaml"),
        "task_commit_sha": target_control,
        "executor": "codex",
    }
    return repo, target_source, intent


def _source_bootstrap_fixture(
    tmp_path: Path, *, consumer_source: bool = False,
    source_generation: str = "1" * 40,
    published_target_sha: str | None = None,
    task_id: str = "TASK-101", task_revision: int = 2,
) -> tuple[Path, Path, dict]:
    target_source = tmp_path / "reviewed-generation"
    if published_target_sha is not None:
        git(tmp_path, "clone", "--no-checkout", "--no-tags",
            str(Path(operator_module.__file__).resolve().parents[2]), str(target_source))
        git(target_source, "checkout", "--detach", published_target_sha)
    else:
        target_source.mkdir()
        git(target_source, "init", "-b", "main")
        git(target_source, "config", "user.name", "Bootstrap Test")
        git(target_source, "config", "user.email", "bootstrap@example.invalid")
        operator_path = target_source / "src" / "aios_renew" / "operator.py"
        if consumer_source:
            shutil.copytree(
                Path(operator_module.__file__).resolve().parent, operator_path.parent,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            operator_path.parent.mkdir(parents=True)
            operator_path.write_text("# exact consumer-capable target\n", encoding="utf-8")
        git(target_source, "add", ".")
        git(target_source, "commit", "-m", "reviewed target generation")
    target_generation = git(target_source, "rev-parse", "HEAD")

    # The complete consumer package includes schema filenames that hit Win32's
    # path limit when cloned below the source-REPAIR transport state directory.
    repo = make_repo(tmp_path / ("c" if consumer_source else "control"))
    legacy = source_generation
    task_path = f".ai/tasks/{task_id}.yaml"
    task_source = TASK_SOURCE.replace("TASK-101", task_id).replace(
        "revision: 1", f"revision: {task_revision}",
    )
    source_control = publish_upstream(
        repo,
        {"AIOS_PIN": f"aios-renew @ {legacy}\n",
         task_path: task_source},
        "authorized source-control migration task",
    )
    git(repo, "fetch", "origin", "main")
    git(repo, "merge", "--ff-only", "origin/main")
    intent = {
        "format": "AIOS_SOURCE_CONTROL_BOOTSTRAP_INTENT", "version": 2,
        "source_generation_sha": legacy,
        "target_generation_sha": target_generation,
        "target_url": str(target_source),
        "repository": str(repo.resolve()),
        "source_control_sha": source_control,
        "pin_path": "AIOS_PIN",
        "source_pin_blob_sha": git(repo, "rev-parse", f"{source_control}:AIOS_PIN"),
        "task_id": task_id, "task_revision": task_revision,
        "task_blob_sha": git(repo, "rev-parse", f"{source_control}:{task_path}"),
        "task_commit_sha": source_control,
        "executor": "codex",
    }
    return repo, target_source, intent


@pytest.fixture
def migration_storage_root(monkeypatch: pytest.MonkeyPatch):
    with tempfile.TemporaryDirectory(prefix="a243-", dir=Path.home()) as directory:
        base = Path(directory).resolve()
        profile = base / "p"
        profile.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: profile))
        yield base


@pytest.fixture
def migration_depth_root(migration_storage_root: Path, monkeypatch: pytest.MonkeyPatch):
    # Model TASK-241's actual cwd and CLONE operand regime, independently of
    # pytest's wrapper path. This fixture changes no host or Git configuration.
    base = migration_storage_root
    padding = 178 - len(str(base / "control" / "repo")) - 1
    assert 1 <= padding <= 200
    depth = base / ("d" * padding)
    depth.mkdir()
    temporary = depth / ("t" * 12)
    temporary.mkdir()
    assert len(str(depth / "control" / "repo")) == 178
    assert len(str(temporary)) == 178
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    return depth


@pytest.mark.parametrize("version", [1, 2])
def test_migration_control_checkout_at_task241_depth(
    migration_depth_root: Path, monkeypatch: pytest.MonkeyPatch, version: int,
) -> None:
    fixture = _migration_fixture if version == 1 else _source_bootstrap_fixture
    repo, source, intent = fixture(migration_depth_root)
    fingerprint = operator_module._migration_fingerprint(intent)
    marker = operator_module._migration_marker(repo, fingerprint)
    before = (git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "origin/main"))
    # Write a real split commit-graph in a short Git checkout, then retain its
    # immutable files in the depth-amplified source (Python supports long paths).
    seed = Path.home() / "g"
    git(repo, "clone", "--local", "--no-hardlinks", str(repo), str(seed))
    git(seed, "commit-graph", "write", "--reachable", "--split=replace")
    graphs = Path(".git/objects/info/commit-graphs")
    shutil.copytree(seed / graphs, repo / graphs, dirs_exist_ok=True)
    graph = next((repo / graphs).glob("graph-*.graph")).relative_to(repo)
    former_control = Path(tempfile.gettempdir()) / ("aios-target-generation-" + "0" * 8) / "control"
    assert 218 <= len(str(former_control)) <= 219
    assert len(str(former_control / graph)) > 260
    # Preserve this locator, but move Git internals out of its amplified depth.
    former_target = runtime_state_root(repo) / "m" / (fingerprint[:12] + "-abcdefgh") / "source"
    assert len(str(former_target / graph)) > 260
    with RepositoryLock(runtime_paths(repo).lock), operator_module._migration_control_checkout(repo, marker) as control:
        git(control, "commit-graph", "write", "--reachable", "--split=replace")
        graph = next((control / graphs).glob("graph-*.graph")).relative_to(control)
        assert len(str(control / graph)) < 260
        assert len(str(control / graph.with_suffix(".graph.lock"))) < 260
        assert (control / graph).is_file()
        if version == 1:
            operator_module._check_migration_control(repo, intent, transport=control)
        else:
            operator_module._check_source_bootstrap_control(repo, intent, transport=control)
        assert git(control, "rev-parse", "HEAD") == intent["source_control_sha"]
    assert not control.exists()
    assert before == (git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "origin/main"))
    assert operator_module._migration_fingerprint(intent) == fingerprint
    assert not marker.exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    # Mechanically cover both production callers of this real-Git boundary.
    for caller in (operator_module._stage_migration_handoff, operator_module.recover_source_bootstrap):
        assert "_migration_control_checkout(" in inspect.getsource(caller)
        assert "aios-target-generation-" not in inspect.getsource(caller)
        assert "aios-recovery-control-" not in inspect.getsource(caller)

    target_seed = Path.home() / "h"
    git(repo, "clone", "--no-hardlinks", "--no-checkout", str(source), str(target_seed))
    git(target_seed, "commit-graph", "write", "--reachable", "--split=replace")
    shutil.copytree(target_seed / graphs, source / graphs, dirs_exist_ok=True)
    target_graph = next((source / graphs).glob("graph-*.graph")).relative_to(source / ".git")
    assert len(str(former_target / ".git" / target_graph)) > 260
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["source_generation_sha"])
    bound, target = operator_module._stage_migration_handoff(repo, intent, marker)
    record = json.loads(marker.read_text(encoding="utf-8"))
    assert target == runtime_state_root(repo) / "m" / record["bundle"] / "source"
    assert record["bundle"].startswith(fingerprint[:12] + "-")
    assert json.loads(bound.read_text(encoding="utf-8")) == intent
    git_dir = Path(git(target, "rev-parse", "--absolute-git-dir"))
    assert list((git_dir / "objects/pack").glob("pack-*.pack"))
    git(target, "commit-graph", "write", "--reachable", "--split=replace")
    target_graph = next((git_dir / "objects/info/commit-graphs").glob("graph-*.graph")).relative_to(git_dir)
    assert len(str(git_dir / target_graph.with_suffix(".graph.lock"))) < 260
    assert (git_dir / target_graph).read_bytes() == (source / ".git" / target_graph).read_bytes()
    assert (target / ".git").is_file() and not (target / ".git").is_symlink()
    assert git(target, "rev-parse", "HEAD") == intent["target_generation_sha"]
    assert git(target, "status", "--porcelain") == ""
    assert git(target, "show", "HEAD:src/aios_renew/operator.py") == (target / "src/aios_renew/operator.py").read_text().strip()
    owner = git_dir.parent / "owner.json"
    before_read = (marker.read_bytes(), bound.read_bytes(), owner.read_bytes())
    assert operator_module._migration_bundle(marker, record, intent) == (bound, target)
    assert operator_module._migration_bundle(marker, record, intent) == (bound, target)
    assert before_read == (marker.read_bytes(), bound.read_bytes(), owner.read_bytes())
    assert before == (git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "origin/main"))
    assert operator_module._migration_fingerprint(intent) == fingerprint
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_source_bootstrap_recovery_target_checkout_at_task241_depth(
    migration_depth_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source, old = _source_bootstrap_fixture(migration_depth_root)
    old_fp = operator_module._migration_fingerprint(old)
    old_marker = operator_module._migration_marker(repo, old_fp)
    operator_module._stage_migration_handoff(repo, old, old_marker)
    old_marker.with_suffix(".consumed").write_text(old_fp, encoding="utf-8")
    old_record = json.loads(old_marker.read_text(encoding="utf-8"))
    old_bound, old_target = operator_module._migration_bundle(old_marker, old_record, old)
    old_storage = operator_module._migration_target_storage(old_marker, old, old_bound.parent)
    old_bytes = (old_marker.read_bytes(), old_bound.read_bytes(), (old_storage / "owner.json").read_bytes())
    (source / "src/aios_renew/operator.py").write_text("# activated replacement\n", encoding="utf-8")
    git(source, "add", ".")
    git(source, "commit", "-m", "activated replacement")
    new = dict(old, target_generation_sha=git(source, "rev-parse", "HEAD"))
    seed = Path.home() / "g"
    git(repo, "clone", "--no-hardlinks", "--no-checkout", str(source), str(seed))
    git(seed, "commit-graph", "write", "--reachable", "--split=replace")
    graphs = Path("objects/info/commit-graphs")
    shutil.copytree(seed / ".git" / graphs, source / ".git" / graphs, dirs_exist_ok=True)
    graph = next((source / ".git" / graphs).glob("graph-*.graph")).relative_to(source / ".git")
    old_path, new_path = migration_depth_root / "old.json", migration_depth_root / "new.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    new_path.write_text(json.dumps(new), encoding="utf-8")
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", new["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha", lambda **kwargs: old["source_generation_sha"])
    assert operator_module.recover_source_bootstrap(old_path, new_path) == 0
    new_marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(new))
    record = json.loads(new_marker.read_text(encoding="utf-8"))
    bound, target = operator_module._migration_bundle(new_marker, record, new)
    git_dir = Path(git(target, "rev-parse", "--absolute-git-dir"))
    assert list((git_dir / "objects/pack").glob("pack-*.pack"))
    git(target, "commit-graph", "write", "--reachable", "--split=replace")
    graph = next((git_dir / graphs).glob("graph-*.graph")).relative_to(git_dir)
    assert len(str(target / ".git" / graph)) > 260
    assert len(str(git_dir / graph.with_suffix(".graph.lock"))) < 260
    assert (git_dir / graph).read_bytes() == (source / ".git" / graph).read_bytes()
    assert target == runtime_state_root(repo) / "m" / record["bundle"] / "source"
    assert git(target, "rev-parse", "HEAD") == new["target_generation_sha"]
    assert git(target, "status", "--porcelain") == ""
    assert operator_module.recover_source_bootstrap(old_path, new_path) == 0
    assert old_bytes == (old_marker.read_bytes(), old_bound.read_bytes(), (old_storage / "owner.json").read_bytes())
    assert git(old_target, "rev-parse", "HEAD") == old["target_generation_sha"]
    assert not list(runtime_paths(repo).runs.glob("*.json"))


@pytest.mark.parametrize("generation,version", [
    ("83115b26df85a7ad6643f317833e18b18586bdbe", 1),
    ("31fd2482cd87d97fd818e05eb5b4dcec69ffeee6", 1),
    ("44eee353eda376c9db8cd88d97184d3122651bf5", 2),
])
def test_migration_separate_git_dir_is_consumable_by_immutable_readers(
    migration_storage_root: Path, generation: str, version: int,
) -> None:
    fixture = _migration_fixture if version == 1 else _source_bootstrap_fixture
    repo, _, intent = fixture(migration_storage_root)
    marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(intent))
    bound, target = operator_module._stage_migration_handoff(repo, intent, marker)
    record = json.loads(marker.read_text(encoding="utf-8"))
    repository = Path(operator_module.__file__).resolve().parents[2]
    source = git(repository, "show", f"{generation}:src/aios_renew/operator.py")
    # Run the exact historical reader and its identity/parser helpers. Only
    # unrelated lifecycle functions are omitted; no old reader is rewritten.
    names = {"_migration_bundle", "_migration_bundle_path", "_migration_record",
             "_migration_fingerprint", "_exact_migration_intent",
             "_exact_source_bootstrap_intent", "_source_bootstrap_record", "_git"}
    definitions = [node for node in ast.parse(source).body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = dict(vars(operator_module))
    exec(compile(ast.Module(body=definitions, type_ignores=[]), generation, "exec"), namespace)
    before = (marker.read_bytes(), bound.read_bytes())
    assert namespace["_migration_bundle"](marker, record, intent) == (bound, target)
    assert before == (marker.read_bytes(), bound.read_bytes())


@pytest.mark.parametrize("state", ["partial", "malformed", "owner", "gitfile", "ambiguous"])
def test_migration_target_storage_rejects_substituted_state_without_mutation(
    migration_storage_root: Path, state: str,
) -> None:
    repo, _, intent = _migration_fixture(migration_storage_root)
    marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(intent))
    bound, target = operator_module._stage_migration_handoff(repo, intent, marker)
    record = json.loads(marker.read_text(encoding="utf-8"))
    storage = operator_module._migration_target_storage(marker, intent, bound.parent)
    owner = storage / "owner.json"
    if state == "partial":
        owner.unlink()
    elif state == "malformed":
        owner.write_text("{", encoding="utf-8")
    elif state == "owner":
        document = json.loads(owner.read_text(encoding="utf-8"))
        document["state_root"] += "-other"
        owner.write_text(json.dumps(document), encoding="utf-8")
    elif state == "gitfile":
        unrelated = migration_storage_root / "unrelated"
        git(repo, "clone", "--no-checkout", intent["target_url"], str(unrelated))
        # Git marks the gitfile hidden on Windows; update the existing file
        # instead of asking CREATE_ALWAYS to replace a hidden file.
        with (target / ".git").open("r+", encoding="utf-8") as gitfile:
            gitfile.write(f"gitdir: {unrelated / '.git'}\n")
            gitfile.truncate()
    else:
        (storage / "g/commondir").write_text(str(repo / ".git"), encoding="utf-8")
    before = {p: p.read_bytes() for p in storage.rglob("*") if p.is_file()}
    durable = (marker.read_bytes(), bound.read_bytes(), (target / ".git").read_bytes())
    with pytest.raises(OperatorError, match="target.*(owner|storage)"):
        operator_module._migration_bundle(marker, record, intent)
    with pytest.raises(OperatorError, match="target.*(owner|storage)"):
        operator_module._remove_migration_target(repo, marker, intent, bound.parent, storage)
    assert before == {p: p.read_bytes() for p in storage.rglob("*") if p.is_file()}
    assert durable == (marker.read_bytes(), bound.read_bytes(), (target / ".git").read_bytes())
    if state == "gitfile":
        assert git(unrelated, "rev-parse", "HEAD") == intent["target_generation_sha"]


@pytest.mark.parametrize("state", ["partial", "stale", "collision"])
def test_migration_target_allocation_does_not_reuse_or_delete_existing_storage(
    migration_storage_root: Path, state: str,
) -> None:
    repo, _, intent = _migration_fixture(migration_storage_root)
    fingerprint = operator_module._migration_fingerprint(intent)
    marker = operator_module._migration_marker(repo, fingerprint)
    storage = operator_module._migration_storage(marker, create=True) / "t" / fingerprint[:16]
    storage.mkdir(parents=True)
    if state != "partial":
        identity = {"format": "AIOS_MIGRATION_TARGET_STORAGE", "version": 1,
                    "state_root": str(marker.parent.parent.resolve()),
                    "fingerprint": fingerprint if state == "stale" else "f" * 64,
                    "bundle": "unrelated durable bundle", "intent": intent}
        (storage / "owner.json").write_text(json.dumps(identity), encoding="utf-8")
    (storage / "retain.txt").write_text("unrelated data", encoding="utf-8")
    before = {p: p.read_bytes() for p in storage.iterdir() if p.is_file()}
    with pytest.raises(OperatorError, match="orphaned or colliding migration target storage"):
        operator_module._stage_migration_handoff(repo, intent, marker)
    assert before == {p: p.read_bytes() for p in storage.iterdir() if p.is_file()}
    assert not list((runtime_state_root(repo) / "m").iterdir())
    assert not marker.exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_migration_target_clone_interruption_cleans_only_new_allocation(
    migration_storage_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, intent = _migration_fixture(migration_storage_root)
    marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(intent))
    storage = operator_module._migration_storage(marker, create=True)
    unrelated = storage / "t" / ("0" * 16)
    unrelated.mkdir(parents=True)
    (unrelated / "retain.txt").write_text("durable", encoding="utf-8")
    real_git = operator_module._git
    def interrupt(root, *args, **kwargs):
        value = real_git(root, *args, **kwargs)
        if args[0] == "clone" and "--separate-git-dir" in args:
            raise OSError("interrupted after real target clone")
        return value
    monkeypatch.setattr(operator_module, "_git", interrupt)
    with pytest.raises(OSError, match="interrupted after real target clone"):
        operator_module._stage_migration_handoff(repo, intent, marker)
    assert list((storage / "t").iterdir()) == [unrelated]
    assert (unrelated / "retain.txt").read_text(encoding="utf-8") == "durable"
    assert not (storage / "c").exists()
    assert not list((runtime_state_root(repo) / "m").iterdir())
    assert not marker.exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_migration_target_linked_git_storage_is_rejected_and_retained(
    migration_storage_root: Path,
) -> None:
    repo, _, intent = _migration_fixture(migration_storage_root)
    marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(intent))
    bound, target = operator_module._stage_migration_handoff(repo, intent, marker)
    record = json.loads(marker.read_text(encoding="utf-8"))
    storage = operator_module._migration_target_storage(marker, intent, bound.parent)
    backing = storage / "g"
    unrelated = migration_storage_root / "retain-git"
    assert backing.resolve().is_relative_to(migration_storage_root.resolve())
    assert unrelated.resolve().is_relative_to(migration_storage_root.resolve())
    backing.rename(unrelated)
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(backing), str(unrelated)],
                       check=True, capture_output=True)
    else:
        backing.symlink_to(unrelated, target_is_directory=True)
    try:
        before = (unrelated / "HEAD").read_bytes()
        with pytest.raises(OperatorError, match="linked path"):
            operator_module._migration_bundle(marker, record, intent)
        with pytest.raises(OperatorError, match="linked path"):
            operator_module._remove_migration_target(repo, marker, intent, bound.parent, storage)
        assert (unrelated / "HEAD").read_bytes() == before
        assert marker.is_file() and bound.is_file() and (target / ".git").is_file()
    finally:
        if sys.platform == "win32":
            backing.rmdir()  # Remove only the junction, leaving its target intact.
        else:
            backing.unlink()
        unrelated.rename(backing)


def test_migration_control_interruption_removes_only_owned_checkout(
    migration_storage_root: Path,
) -> None:
    repo, _, intent = _migration_fixture(migration_storage_root)
    marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(intent))
    with pytest.raises(OSError, match="control interruption"):
        with RepositoryLock(runtime_paths(repo).lock), operator_module._migration_control_checkout(repo, marker) as control:
            assert git(control, "rev-parse", "HEAD") == intent["source_control_sha"]
            raise OSError("control interruption")
    assert not control.exists()
    owner = operator_module._migration_storage(marker) / "owner.json"
    assert json.loads(owner.read_text(encoding="utf-8"))["state_root"] == str(runtime_state_root(repo))
    assert not marker.exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("historical_format", ["full", "task197"])
def test_migration_historical_bundle_formats_remain_exactly_consumable(
    migration_storage_root: Path, version: int, historical_format: str,
) -> None:
    fixture = _migration_fixture if version == 1 else _source_bootstrap_fixture
    repo, source, intent = fixture(migration_storage_root)
    fingerprint = operator_module._migration_fingerprint(intent)
    marker = operator_module._migration_marker(repo, fingerprint)
    marker.parent.mkdir(parents=True)
    parent = marker.parent if historical_format == "full" else marker.parent.parent / "m"
    name = f"{fingerprint if historical_format == 'full' else fingerprint[:12]}-historical"
    bundle = parent / name
    bundle.mkdir(parents=True)
    bound = bundle / "intent.json"
    bound.write_text(json.dumps(intent), encoding="utf-8")
    git(repo, "clone", "--no-checkout", "--no-tags", str(source), str(bundle / "source"))
    git(bundle / "source", "checkout", "--detach", intent["target_generation_sha"])
    record = (operator_module._migration_record if version == 1 else
              operator_module._source_bootstrap_record)(intent, fingerprint, name)
    marker.write_text(json.dumps(record), encoding="utf-8")
    before = (marker.read_bytes(), bound.read_bytes())
    assert operator_module._migration_bundle(marker, record, intent) == (bound, bundle / "source")
    edges, superseded, pending = operator_module._migration_history(repo)
    assert edges == {fingerprint: (marker, record, intent)}
    assert not superseded and not pending
    assert before == (marker.read_bytes(), bound.read_bytes())
    # Historical reading does not require allocating a new storage namespace.
    assert not (Path.home() / ".aios-m").exists()


@pytest.mark.parametrize("state", ["partial_owner", "malformed_owner", "owner_collision", "control"])
def test_migration_storage_rejects_partial_colliding_and_stale_control(
    migration_storage_root: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    repo, _, intent = _migration_fixture(migration_storage_root)
    marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(intent))
    storage = operator_module._migration_storage(marker, create=True)
    owner = storage / "owner.json"
    if state == "partial_owner":
        owner.unlink()
    elif state == "malformed_owner":
        owner.write_text("{", encoding="utf-8")
    elif state == "owner_collision":
        document = json.loads(owner.read_text(encoding="utf-8"))
        document["state_root"] += "-other-repository"
        owner.write_text(json.dumps(document), encoding="utf-8")
    else:
        (storage / "c").mkdir()
        (storage / "c" / "partial.txt").write_text("retain", encoding="utf-8")
    before = {path: path.read_bytes() for path in storage.rglob("*") if path.is_file()}
    path = migration_storage_root / "intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["source_generation_sha"])
    with pytest.raises(OperatorError, match="storage owner|orphaned migration control"):
        operator_module.migrate_primary(path, runner=lambda *a, **k: pytest.fail("target launched"))
    assert before == {path: path.read_bytes() for path in storage.rglob("*") if path.is_file()}
    assert not marker.exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def _source_repair_bootstrap_intent(bootstrap: dict, target_sha: str) -> dict:
    return {
        "format": "AIOS_SOURCE_REPAIR_BOOTSTRAP_INTENT", "version": 1,
        "repository": bootstrap["repository"],
        "bootstrap_fingerprint": operator_module._migration_fingerprint(bootstrap),
        "legacy_generation_sha": bootstrap["source_generation_sha"],
        "target_generation_sha": target_sha, "target_url": bootstrap["target_url"],
        "source_control_sha": bootstrap["source_control_sha"],
        "pin_path": bootstrap["pin_path"],
        "source_pin_blob_sha": bootstrap["source_pin_blob_sha"],
        "failed_run_id": "RUN-101-001", "repair_sha": "a" * 40,
        "repair_dispatch_id": "repair-101-001", "executor": "codex",
        "model": None, "reasoning_effort": None,
        "model_source": None, "effort_source": None,
    }


@pytest.fixture
def source_repair_depth_root():
    # A complete consumer checkout fits at the bounded locator, while the
    # former dispatch-id/source locator would cross the Win32 path budget.
    short_parent = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Temp"
                    if sys.platform == "win32" else None)
    with tempfile.TemporaryDirectory(prefix="a237-", dir=short_parent) as directory:
        base = Path(directory)
        longest = Path("src/aios_renew/schemas/gemini_reviewer_semantic_response.json")
        windows_legacy_path_budget = 260
        new_leaf = base / "c" / "repo" / ".git" / "r" / ("0" * 16) / "s" / longest
        padding = windows_legacy_path_budget - 25 - len(str(new_leaf)) - 1
        assert 1 <= padding <= 200
        depth_root = base / ("d" * padding)
        repo = depth_root / "c" / "repo"
        bounded_leaf = repo / ".git" / "r" / ("0" * 16) / "s" / longest
        former_leaf = (repo / ".git" / "aios" / "source-repair-transports"
                       / "repair-101-001" / "source" / longest)
        assert len(str(bounded_leaf)) < windows_legacy_path_budget
        assert len(str(former_leaf)) > windows_legacy_path_budget
        depth_root.mkdir()
        yield depth_root


@pytest.fixture
def source_successor_short_root():
    # Git copies commit-graph files beneath the migration bundle. Keep that
    # checkout independent of pytest's deeply nested Windows verification root.
    short_parent = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Temp"
                    if sys.platform == "win32" else None)
    with tempfile.TemporaryDirectory(prefix="a220-", dir=short_parent) as directory:
        yield Path(directory)


def _source_successor_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, published_target_sha: str | None = None,
    real_lineage: bool = False,
) -> tuple[Path, dict, dict]:
    installed_sha = "31fd2482cd87d97fd818e05eb5b4dcec69ffeee6"
    prior_target = "44eee353eda376c9db8cd88d97184d3122651bf5"
    task_id = "TASK-259" if real_lineage else "TASK-101"
    prior_revision = 1 if real_lineage else 2
    successor_revision = prior_revision + 1
    failed_run_id = "RUN-259-001" if real_lineage else "RUN-101-001"
    task_path = f".ai/tasks/{task_id}.yaml"
    repo, source, bootstrap = _source_bootstrap_fixture(
        tmp_path, source_generation=installed_sha, published_target_sha=published_target_sha,
        task_id=task_id, task_revision=prior_revision,
    )
    control_source = Path(operator_module.__file__).resolve().parents[2]
    bootstrap["target_generation_sha"] = prior_target
    bootstrap["target_url"] = str(control_source)
    fingerprint = operator_module._migration_fingerprint(bootstrap)
    marker = operator_module._migration_marker(repo, fingerprint)
    marker.parent.mkdir(parents=True, exist_ok=True)
    bundle = runtime_state_root(repo) / "m" / f"{fingerprint[:12]}-source"
    bundle.mkdir(parents=True)
    (bundle / "intent.json").write_text(json.dumps(bootstrap), encoding="utf-8")
    git(repo, "clone", "--no-checkout", str(control_source), str(bundle / "source"))
    git(bundle / "source", "checkout", "--detach", prior_target)
    marker.write_text(json.dumps(operator_module._source_bootstrap_record(
        bootstrap, fingerprint, bundle.name)), encoding="utf-8")
    marker.with_suffix(".consumed").write_text(fingerprint, encoding="utf-8")
    marker.with_suffix(".completed").write_text(fingerprint, encoding="utf-8")
    environment = tmp_path / "installed-source"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip())
    package = site / "aios_renew"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "operator.py").write_text(
        "def migrate_primary(): pass\n# migrate-primary\n", encoding="utf-8",
    )
    metadata = site / "aios_renew-0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: aios-renew\nVersion: 0.0\n", encoding="utf-8",
    )
    (metadata / "direct_url.json").write_text(json.dumps({
        "url": "https://example.invalid/aios-renew.git",
        "vcs_info": {"vcs": "git", "commit_id": installed_sha},
    }), encoding="utf-8")
    monkeypatch.setattr(operator_module.sys, "executable", str(python))
    state = runtime_paths(repo)
    run_id = failed_run_id
    (state.runs / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id, "task": {"id": task_id, "revision": prior_revision},
        "executor": "codex", "base_sha": bootstrap["source_control_sha"],
        "workspace": str(repo), "status": "ACTIVE",
    }), encoding="utf-8")
    (state.failures / f"{run_id}.json").write_text(json.dumps({
        "kind": "FAILURE", "run_id": run_id,
        "task": {"id": task_id, "revision": prior_revision},
        "executor": "codex", "base_sha": bootstrap["source_control_sha"],
    }), encoding="utf-8")
    successor_control = publish_upstream(
        repo, {task_path: TASK_SOURCE.replace("TASK-101", task_id).replace(
            "revision: 1", f"revision: {successor_revision}")},
        "Brain-authored successor TASK",
    )
    current_control = publish_upstream(
        repo, {"SUCCESSOR_CONTROL": "fast-forward descendant\n"},
        "later clean control commit",
    )
    git(repo, "fetch", "origin", "main")
    git(repo, "merge", "--ff-only", "origin/main")
    if published_target_sha is None:
        shutil.copytree(
            Path(operator_module.__file__).resolve().parent,
            source / "src" / "aios_renew", dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (source / "successor-capability").write_text("published", encoding="utf-8")
        git(source, "add", ".")
        git(source, "commit", "-m", "published successor source")
    intent = {
        "format": "AIOS_SOURCE_BOOTSTRAP_SUCCESSOR_INTENT", "version": 1,
        "repository": bootstrap["repository"], "bootstrap_fingerprint": fingerprint,
        "failed_run_id": run_id, "legacy_generation_sha": bootstrap["source_generation_sha"],
        "prior_target_generation_sha": bootstrap["target_generation_sha"],
        "target_generation_sha": git(source, "rev-parse", "HEAD"),
        "target_url": str(source), "source_control_sha": bootstrap["source_control_sha"],
        "current_control_sha": current_control, "pin_path": bootstrap["pin_path"],
        "source_pin_blob_sha": bootstrap["source_pin_blob_sha"],
        "task_id": task_id, "prior_task_revision": prior_revision,
        "task_revision": successor_revision,
        "task_blob_sha": git(repo, "rev-parse", f"{successor_control}:{task_path}"),
        "task_commit_sha": successor_control, "successor_delivery_id": "successor-101-001",
        "executor": "codex",
    }
    return repo, bootstrap, intent


def test_source_successor_production_activation_and_exact_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_successor_short_root: Path,
) -> None:
    repo, bootstrap, intent = _source_successor_fixture(source_successor_short_root, monkeypatch)
    path = tmp_path / "successor.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    published = "5d8ac589cbb4f611816d2926cff1989eda4eb74d"
    source = Path(operator_module.__file__).read_text(encoding="utf-8")
    assignments = [node for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.AnnAssign)
                   and isinstance(node.target, ast.Name)
                   and node.target.id == "_SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA"]
    assert len(assignments) == 1
    assert isinstance(assignments[0].value, ast.Constant)
    assert assignments[0].value.value == published
    assert operator_module._SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA == published
    for target_sha in (intent["target_generation_sha"], "f" * 40):
        rejected = {**intent, "target_generation_sha": target_sha}
        path.write_text(json.dumps(rejected), encoding="utf-8")
        with pytest.raises(OperatorError, match="not activated"):
            operator_module.bootstrap_source_successor_primary(
                path, runner=lambda *args, **kwargs: pytest.fail("target launched"),
            )
    for target_sha in (bootstrap["target_generation_sha"], bootstrap["source_generation_sha"]):
        path.write_text(json.dumps({**intent, "target_generation_sha": target_sha}), encoding="utf-8")
        with pytest.raises(OperatorError, match="generations are not distinct"):
            operator_module.bootstrap_source_successor_primary(path)
    assert not (runtime_state_root(repo).parent / "s").exists()
    assert not (runtime_paths(repo).runs / "RUN-101-002.json").exists()
    path.write_text(json.dumps(intent), encoding="utf-8")
    operator_module._source_bootstrap_successor_lineage(repo, intent)
    for change in ({"failed_run_id": "RUN-101-002"}, {"task_revision": 2},
                   {"task_revision": 1}, {"task_id": "TASK-102"},
                   {"source_pin_blob_sha": "f" * 40}, {"executor": "antigravity"}):
        altered = {**intent, **change}
        if "task_revision" in change:
            with pytest.raises(OperatorError, match="strictly newer"):
                operator_module._exact_source_bootstrap_successor_intent(altered)
        else:
            with pytest.raises(OperatorError):
                operator_module._source_bootstrap_successor_lineage(repo, altered)
    dirty = repo / "uncommitted-control"
    dirty.write_text("dirty", encoding="utf-8")
    with pytest.raises(OperatorError, match="clean attached main"):
        operator_module._source_bootstrap_successor_lineage(repo, intent)
    dirty.unlink()
    git(repo, "checkout", "--detach", intent["current_control_sha"])
    with pytest.raises(OperatorError, match="Git command failed|clean attached main"):
        operator_module._source_bootstrap_successor_lineage(repo, intent)
    git(repo, "checkout", "main")
    disconnected = git(repo, "commit-tree", "HEAD^{tree}", "-m", "unrelated control")
    git(repo, "update-ref", "refs/heads/main", disconnected)
    try:
        with pytest.raises(OperatorError):
            operator_module._source_bootstrap_successor_lineage(
                repo, {**intent, "current_control_sha": disconnected,
                       "task_commit_sha": disconnected},
            )
    finally:
        git(repo, "update-ref", "refs/heads/main", intent["current_control_sha"])
    state = runtime_paths(repo)
    failure = state.failures / "RUN-101-001.json"
    failure.unlink()
    result = state.results / "RUN-101-001.json"
    result.write_text(json.dumps({
        "result": {"head_sha": bootstrap["source_control_sha"], "claims": [],
                   "changed_files": [], "unresolved": []}, "evidence": [],
    }), encoding="utf-8")
    with pytest.raises(OperatorError, match="completed failed v2 edge"):
        operator_module._source_bootstrap_successor_lineage(repo, intent)
    result.unlink()
    failure.write_text(json.dumps({
        "kind": "FAILURE", "run_id": "RUN-101-001",
        "task": {"id": "TASK-101", "revision": 2},
        "executor": "codex", "base_sha": bootstrap["source_control_sha"],
    }), encoding="utf-8")
    assert operator_module._migration_marker(repo, intent["bootstrap_fingerprint"]).with_suffix(".completed").is_file()
    assert failure.is_file()


def test_source_successor_final_published_activation_composes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_successor_short_root: Path,
) -> None:
    published = "5d8ac589cbb4f611816d2926cff1989eda4eb74d"
    installed = "31fd2482cd87d97fd818e05eb5b4dcec69ffeee6"
    prior_target = "44eee353eda376c9db8cd88d97184d3122651bf5"
    assert operator_module._SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA == published
    repo, bootstrap, intent = _source_successor_fixture(
        source_successor_short_root, monkeypatch, published_target_sha=published,
        real_lineage=True,
    )
    assert (intent["target_generation_sha"], intent["legacy_generation_sha"],
            intent["prior_target_generation_sha"]) == (published, installed, prior_target)
    assert bootstrap["source_generation_sha"] == installed
    assert bootstrap["target_generation_sha"] == prior_target
    assert intent["task_id"] == bootstrap["task_id"] == "TASK-259"
    assert intent["failed_run_id"] == "RUN-259-001"
    assert intent["task_revision"] > intent["prior_task_revision"] == bootstrap["task_revision"]
    assert intent["executor"] == bootstrap["executor"] == "codex"
    assert intent["current_control_sha"] != intent["task_commit_sha"]
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", intent["source_control_sha"],
         intent["current_control_sha"]], cwd=repo, check=False,
    ).returncode == 0
    assert (git(repo, "rev-parse", f"{intent['source_control_sha']}:{intent['pin_path']}")
            == git(repo, "rev-parse", f"{intent['current_control_sha']}:{intent['pin_path']}")
            == intent["source_pin_blob_sha"])
    assert (git(repo, "rev-parse", f"{intent['task_commit_sha']}:.ai/tasks/TASK-259.yaml")
            == git(repo, "rev-parse", f"{intent['current_control_sha']}:.ai/tasks/TASK-259.yaml")
            == intent["task_blob_sha"])
    assert operator_module._successor_installed_generation_sha() == installed

    path = tmp_path / "successor.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    launched = []

    def stage_runner(command, **kwargs):
        launched.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0)

    with pytest.raises(OperatorError, match="not activated"):
        wrong = tmp_path / "wrong-target.json"
        wrong.write_text(json.dumps({**intent, "target_generation_sha": "f" * 40}),
                         encoding="utf-8")
        operator_module.bootstrap_source_successor_primary(wrong, runner=stage_runner)
    assert not launched

    python = Path(sys.executable)
    installed_site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip())
    direct_url = installed_site / "aios_renew-0.0.dist-info" / "direct_url.json"
    original = direct_url.read_text(encoding="utf-8")
    direct_url.write_text(json.dumps({
        "url": "https://example.invalid/aios-renew.git",
        "vcs_info": {"vcs": "git", "commit_id": "a" * 40},
    }), encoding="utf-8")
    try:
        with pytest.raises(OperatorError, match="installed generation mismatch"):
            operator_module.bootstrap_source_successor_primary(path, runner=stage_runner)
    finally:
        direct_url.write_text(original, encoding="utf-8")
    assert not launched

    assert operator_module.bootstrap_source_successor_primary(path, runner=stage_runner) == 0
    assert len(launched) == 1
    bundle, target = operator_module._source_bootstrap_successor_state(repo, intent)
    bound = bundle / "intent.json"
    assert launched[0][0][-3:] == [str(bound), "--accept-transport", str(bound)]
    assert git(target, "rev-parse", "HEAD") == published
    assert json.loads(bound.read_text(encoding="utf-8")) == intent
    control_operator = Path(operator_module.__file__).resolve()
    target_operator = (target / "src" / "aios_renew" / "operator.py").resolve()
    installed_operator = (installed_site / "aios_renew" / "operator.py").resolve()
    assert len({control_operator, target_operator, installed_operator}) == 3
    assert git(control_operator.parents[2], "rev-parse", "HEAD") != published

    child = """
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from aios_renew import operator

intent = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if sys.argv[5] == "exact":
    assert Path(operator.__file__).resolve() == Path(sys.argv[3]).resolve()
    assert operator._SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA is None
assert operator._successor_installed_generation_sha() == intent["legacy_generation_sha"]
counter = Path(sys.argv[4])
def primary(task_id, **kwargs):
    operator._validate_successor_execution(
        Path(intent["repository"]), kwargs["_successor_transport"], task_id,
        kwargs["executor"], kwargs["synchronize"], kwargs["preflight_sha"],
        None, kwargs["task_revision"], kwargs["task_blob_sha"],
        kwargs["task_commit_sha"],
    )
    assert not counter.exists()
    counter.write_text("1", encoding="utf-8")
    state = operator.runtime_paths(Path(intent["repository"]))
    (state.runs / "RUN-259-002.json").write_text(json.dumps({
        "run_id": "RUN-259-002", "task": {"id": task_id, "revision": intent["task_revision"]},
        "executor": intent["executor"], "base_sha": intent["current_control_sha"],
        "workspace": intent["repository"], "status": "ACTIVE",
    }), encoding="utf-8")
    (state.results / "RUN-259-002.json").write_text(json.dumps({
        "result": {"head_sha": intent["current_control_sha"], "claims": [],
                   "changed_files": [], "unresolved": []}, "evidence": [],
    }), encoding="utf-8")
    return SimpleNamespace(render=lambda: "SUCCESSOR_PRIMARY_DELEGATED")
operator.run_task = primary
raise SystemExit(operator.bootstrap_source_successor_primary(
    sys.argv[1], transport_path=sys.argv[2],
))
"""
    env = dict(launched[0][1])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] += os.pathsep + site.getsitepackages()[-1]
    counter = tmp_path / "delegations.txt"

    def consume(*, intent_file=path, transport_file=bound, source=target):
        child_env = dict(env)
        child_env["PYTHONPATH"] = (str(source / "src") + os.pathsep
                                   + os.pathsep.join(env["PYTHONPATH"].split(os.pathsep)[1:]))
        return subprocess.run(
            [str(python), "-c", child, str(intent_file), str(transport_file),
             str(target_operator), str(counter), "exact" if source == target else "other"],
            cwd=tmp_path, env=child_env, capture_output=True, text=True, check=False,
        )

    wrong_path = consume(transport_file=tmp_path / "wrong.json")
    assert wrong_path.returncode != 0
    assert "transport path mismatch" in wrong_path.stderr
    wrong_source = consume(source=control_operator.parents[2])
    assert wrong_source.returncode != 0
    assert "running source differs" in wrong_source.stderr
    bound.write_text(json.dumps({**intent, "successor_delivery_id": "successor-101-conflict"}),
                     encoding="utf-8")
    altered_transport = consume()
    assert altered_transport.returncode != 0
    assert "transport identity mismatch" in altered_transport.stderr
    bound.write_text(json.dumps(intent), encoding="utf-8")
    failure = runtime_paths(repo).failures / "RUN-259-001.json"
    original_failure = failure.read_text(encoding="utf-8")
    failure.unlink()
    try:
        missing_lineage = consume()
        assert missing_lineage.returncode != 0
        assert "completed failed v2 edge" in missing_lineage.stderr
    finally:
        failure.write_text(original_failure, encoding="utf-8")
    assert not counter.exists()

    admitted = consume()
    assert admitted.returncode == 0, admitted.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" in admitted.stdout
    assert counter.read_text(encoding="utf-8") == "1"
    replay = consume()
    assert replay.returncode == 0, replay.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in replay.stdout
    assert counter.read_text(encoding="utf-8") == "1"


def test_source_successor_stage_replay_and_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_successor_short_root: Path,
) -> None:
    repo, _, intent = _source_successor_fixture(source_successor_short_root, monkeypatch)
    intent["successor_delivery_id"] = "successor-" + "x" * 118
    # A future published source is represented by the exact test checkout.
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA",
                        intent["target_generation_sha"])
    path = tmp_path / "successor.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    launched = []
    def child(command, **kwargs):
        launched.append(command)
        return subprocess.CompletedProcess(command, 0)
    assert operator_module.bootstrap_source_successor_primary(path, runner=child) == 0
    assert operator_module.bootstrap_source_successor_primary(path, runner=child) == 0
    assert len(launched) == 2 and launched[0] == launched[1]
    bound = Path(launched[0][-1])
    assert bound.parent.parent == runtime_state_root(repo).parent / "s"
    assert (json.loads(bound.read_text(encoding="utf-8"))["successor_delivery_id"]
            == intent["successor_delivery_id"])
    bound.write_text(json.dumps({**intent, "successor_delivery_id": "successor-101-002"}),
                     encoding="utf-8")
    with pytest.raises(OperatorError, match="transport identity mismatch"):
        operator_module._source_bootstrap_successor_record(repo, intent)
    bound.write_text(json.dumps(intent), encoding="utf-8")
    conflicting = {**intent, "successor_delivery_id": "successor-101-002"}
    with pytest.raises(OperatorError, match="competing"):
        operator_module._source_bootstrap_successor_lineage(repo, conflicting)
    (bound.parent / "s" / "src" / "aios_renew" / "operator.py").write_text(
        "# changed target source\n", encoding="utf-8"
    )
    with pytest.raises(OperatorError, match="transport identity mismatch"):
        operator_module.bootstrap_source_successor_primary(path, transport_path=bound)


def test_source_successor_isolated_installed_provenance_rejects_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_successor_short_root: Path,
) -> None:
    repo, _, intent = _source_successor_fixture(source_successor_short_root, monkeypatch)
    assert intent["legacy_generation_sha"] == "31fd2482cd87d97fd818e05eb5b4dcec69ffeee6"
    assert intent["prior_target_generation_sha"] == "44eee353eda376c9db8cd88d97184d3122651bf5"
    assert intent["task_commit_sha"] != intent["current_control_sha"]
    assert (git(repo, "rev-parse", f"{intent['current_control_sha']}:.ai/tasks/TASK-101.yaml")
            == intent["task_blob_sha"])
    assert (git(repo, "show", f"{intent['source_control_sha']}:{intent['pin_path']}")
            == git(repo, "show", f"{intent['current_control_sha']}:{intent['pin_path']}"))
    assert operator_module._successor_installed_generation_sha() == intent["legacy_generation_sha"]
    with pytest.raises(OperatorError, match="legacy installed generation attestation failed"):
        operator_module._legacy_installed_generation_sha()
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA",
                        intent["target_generation_sha"])
    path = tmp_path / "successor.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    python = Path(sys.executable)
    installed_site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip())
    direct_url = installed_site / "aios_renew-0.0.dist-info" / "direct_url.json"
    original = json.loads(direct_url.read_text(encoding="utf-8"))
    for malformed in (
        {**original, "vcs_info": {"vcs": "git", "commit_id": "a" * 40}},
        {**original, "dir_info": {"editable": True}},
        {**original, "vcs_info": {"vcs": "hg", "commit_id": intent["legacy_generation_sha"]}},
        {**original, "vcs_info": {"vcs": "git", "commit_id": "floating"}},
        {"url": original["url"]},
    ):
        direct_url.write_text(json.dumps(malformed), encoding="utf-8")
        with pytest.raises(OperatorError, match="attestation failed|generation mismatch"):
            operator_module.bootstrap_source_successor_primary(
                path, runner=lambda *args, **kwargs: pytest.fail("target launched"),
            )
        assert not (runtime_paths(repo).runs / "RUN-101-002.json").exists()
    direct_url.write_text(json.dumps(original), encoding="utf-8")
    shadow = tmp_path / "shadow" / "aios_renew"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "operator.py").write_text("# wrong imported path\n", encoding="utf-8")
    sitecustomize = installed_site / "sitecustomize.py"
    sitecustomize.write_text(
        f"import sys\nsys.path.insert(0, {str(shadow.parent)!r})\n", encoding="utf-8",
    )
    try:
        with pytest.raises(OperatorError, match="attestation failed"):
            operator_module.bootstrap_source_successor_primary(
                path, runner=lambda *args, **kwargs: pytest.fail("target launched"),
            )
    finally:
        sitecustomize.unlink()
    duplicate = installed_site / "aios_renew-0.1.dist-info"
    duplicate.mkdir()
    (duplicate / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: aios-renew\nVersion: 0.1\n", encoding="utf-8",
    )
    (duplicate / "direct_url.json").write_text(json.dumps(original), encoding="utf-8")
    try:
        with pytest.raises(OperatorError, match="attestation failed"):
            operator_module.bootstrap_source_successor_primary(
                path, runner=lambda *args, **kwargs: pytest.fail("target launched"),
            )
    finally:
        shutil.rmtree(duplicate)
    assert not (runtime_paths(repo).runs / "RUN-101-002.json").exists()


def test_source_successor_published_target_consumes_without_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_successor_short_root: Path,
) -> None:
    published = "5d8ac589cbb4f611816d2926cff1989eda4eb74d"
    repo, _, intent = _source_successor_fixture(
        source_successor_short_root, monkeypatch, published_target_sha=published,
    )
    path = tmp_path / "successor.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    launched = []

    def stage_runner(command, **kwargs):
        launched.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0)

    assert operator_module.bootstrap_source_successor_primary(path, runner=stage_runner) == 0
    assert len(launched) == 1
    bundle, target = operator_module._source_bootstrap_successor_state(repo, intent)
    bound = bundle / "intent.json"
    assert launched[0][0][-3:] == [str(bound), "--accept-transport", str(bound)]
    assert git(target, "rev-parse", "HEAD") == published
    assert json.loads(bound.read_text(encoding="utf-8")) == intent

    # The published TASK-220 target has a closed activation slot. Import it
    # afresh while the TASK-221 control module uses its committed activation.
    child = """
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from aios_renew import operator

intent = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if sys.argv[3] == "exact":
    assert Path(operator.__file__).resolve().parents[2] == Path(sys.argv[4]).resolve()
    if sys.argv[5] == "published":
        assert operator._SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA is None
def primary(task_id, **kwargs):
    operator._validate_successor_execution(
        Path(intent["repository"]), kwargs["_successor_transport"], task_id,
        kwargs["executor"], kwargs["synchronize"], kwargs["preflight_sha"],
        None, kwargs["task_revision"], kwargs["task_blob_sha"],
        kwargs["task_commit_sha"],
    )
    assert task_id == intent["task_id"]
    assert kwargs["_successor_transport"] == sys.argv[2]
    if sys.argv[6] == "record":
        state = operator.runtime_paths(Path(intent["repository"]))
        (state.runs / "RUN-101-002.json").write_text(json.dumps({
            "run_id": "RUN-101-002", "task": {"id": task_id, "revision": intent["task_revision"]},
            "executor": intent["executor"], "base_sha": intent["current_control_sha"],
            "workspace": intent["repository"], "status": "ACTIVE",
        }))
        (state.results / "RUN-101-002.json").write_text(json.dumps({
            "result": {"head_sha": intent["current_control_sha"], "claims": [],
                       "changed_files": [], "unresolved": []}, "evidence": [],
        }))
    print("SUCCESSOR_PRIMARY_DELEGATED")
    return SimpleNamespace(render=lambda: "delegated")
operator.run_task = primary
raise SystemExit(operator.main([
    "bootstrap-source-successor-primary", sys.argv[1],
    "--accept-transport", sys.argv[2],
]))
"""

    def consume(intent_file: Path, transport_file: Path, *, exact_source: bool = True,
                published_source: bool = True, record: bool = False):
        env = dict(launched[0][1])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] += os.pathsep + site.getsitepackages()[-1]
        if not exact_source:
            env["PYTHONPATH"] = (str(Path(operator_module.__file__).resolve().parents[1])
                                 + os.pathsep + site.getsitepackages()[-1])
        return subprocess.run(
            [sys.executable, "-c", child, str(intent_file), str(transport_file),
             "exact" if exact_source else "wrong", str(target),
             "published" if published_source else "wrong_head",
             "record" if record else "observe"],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
        )

    python = Path(sys.executable)
    installed_site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip())
    control_operator = Path(operator_module.__file__).resolve()
    target_operator = (target / "src" / "aios_renew" / "operator.py").resolve()
    installed_operator = (installed_site / "aios_renew" / "operator.py").resolve()
    assert len({control_operator, target_operator, installed_operator}) == 3
    assert published != git(control_operator.parents[2], "rev-parse", "HEAD")
    direct_url = installed_site / "aios_renew-0.0.dist-info" / "direct_url.json"
    original = direct_url.read_text(encoding="utf-8")
    direct_url.write_text(json.dumps({
        "url": "https://example.invalid/aios-renew.git",
        "vcs_info": {"vcs": "git", "commit_id": "a" * 40},
    }), encoding="utf-8")
    rejected_installed = consume(path, bound)
    assert rejected_installed.returncode != 0
    assert "installed generation mismatch" in rejected_installed.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in rejected_installed.stdout
    direct_url.write_text(original, encoding="utf-8")

    success = consume(path, bound)
    assert success.returncode == 0, success.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" in success.stdout

    wrong_path = consume(path, tmp_path / "wrong.json")
    assert wrong_path.returncode != 0
    assert "transport path mismatch" in wrong_path.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in wrong_path.stdout

    altered = {**intent, "successor_delivery_id": "successor-101-conflict"}
    bound.write_text(json.dumps(altered), encoding="utf-8")
    changed_stored_intent = consume(path, bound)
    assert changed_stored_intent.returncode != 0
    assert "transport identity mismatch" in changed_stored_intent.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in changed_stored_intent.stdout
    bound.write_text(json.dumps(intent), encoding="utf-8")

    wrong_source = consume(path, bound, exact_source=False)
    assert wrong_source.returncode != 0
    assert "running source differs" in wrong_source.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in wrong_source.stdout

    git(target, "commit", "--allow-empty", "-m", "wrong successor HEAD")
    wrong_head = consume(path, bound, published_source=False)
    assert wrong_head.returncode != 0
    assert "transport identity mismatch" in wrong_head.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in wrong_head.stdout
    git(target, "checkout", "--detach", published)

    conflicting_path = tmp_path / "conflicting-successor.json"
    conflicting_path.write_text(json.dumps({**intent, "executor": "other"}), encoding="utf-8")
    conflicting = consume(conflicting_path, bound)
    assert conflicting.returncode != 0
    assert "successor identity is invalid" in conflicting.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in conflicting.stdout

    conflicting_path.write_text(json.dumps({**intent, "source_pin_blob_sha": "f" * 40}),
                                encoding="utf-8")
    conflicting_binding = consume(conflicting_path, bound)
    assert conflicting_binding.returncode != 0
    assert "transport identity mismatch" in conflicting_binding.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in conflicting_binding.stdout

    (runtime_paths(repo).failures / "RUN-101-001.json").unlink()
    wrong_lineage = consume(path, bound)
    assert wrong_lineage.returncode != 0
    assert "completed failed v2 edge" in wrong_lineage.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in wrong_lineage.stdout
    assert not (runtime_paths(repo).runs / "RUN-101-002.json").exists()
    (runtime_paths(repo).failures / "RUN-101-001.json").write_text(json.dumps({
        "kind": "FAILURE", "run_id": "RUN-101-001",
        "task": {"id": "TASK-101", "revision": 2},
        "executor": "codex", "base_sha": intent["source_control_sha"],
    }), encoding="utf-8")
    admitted = consume(path, bound, record=True)
    assert admitted.returncode == 0, admitted.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" in admitted.stdout
    replay = consume(path, bound)
    assert replay.returncode == 0, replay.stderr
    assert "SUCCESSOR_PRIMARY_DELEGATED" not in replay.stdout


def test_source_successor_transport_path_budget_and_full_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A long verification checkout plus Git's loose-object suffix must still
    # fit the default Win32 path limit; the full 128-character id must not be
    # used as a filesystem component or shortened into an authority token.
    base = Path("C:/verification")
    root = base / ("r" * (170 - len(str(base)) - 1))
    assert len(str(root)) == 170
    monkeypatch.setattr(operator_module, "runtime_state_root",
                        lambda repo: Path(repo) / ".git" / "aios")
    delivery = "successor-" + "x" * 118
    intent = {"successor_delivery_id": delivery}
    bundle, target = operator_module._source_bootstrap_successor_state(root, intent)
    assert bundle == operator_module._source_bootstrap_successor_state(root, intent)[0]
    assert bundle != operator_module._source_bootstrap_successor_state(
        root, {"successor_delivery_id": delivery[:-1] + "y"})[0]
    assert len(str(target / ".git" / "objects" / "ff" / ("f" * 38))) < 260
    assert len(str(target / "src" / "aios_renew" / "schemas" /
                   "finalize_candidate_result_package.json")) < 260
    assert delivery not in str(bundle)


def test_source_repair_uses_canonical_failed_candidate_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aios_renew.review_transport as transport

    root = tmp_path / "control"
    root.mkdir()
    state = root / ".git" / "aios"
    candidate_sha = "c" * 40
    failed_run_id = "RUN-255-003"
    policy_blob = (Path(operator_module.__file__).parents[2] / ".ai" /
                   "executor-profiles.yaml").read_bytes()
    requested = {
        "executor": "codex", "model": "gpt-6-sol", "reasoning_effort": "high",
        "model_source": "REPOSITORY_DEFAULT", "effort_source": "REPOSITORY_DEFAULT",
    }
    intent = {"failed_run_id": failed_run_id, **requested}
    run = {
        "run_id": failed_run_id, "task": {"id": "TASK-255", "revision": 1},
        "executor": "codex", "base_sha": "b" * 40, "workspace": str(root),
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE", "run_id": failed_run_id,
        "task": run["task"], "executor": "codex",
        "base_sha": run["base_sha"], "failed_head_sha": candidate_sha,
    }
    profile = {
        "format": "AIOS_EXECUTION_PROFILE", "version": 1,
        "run_id": failed_run_id, **requested,
    }

    def artifact(candidate=candidate_sha, profile_data=profile):
        return RemoteFailureArtifacts(
            failed_run_id, candidate, json.dumps(run).encode(),
            json.dumps(failure).encode(), None,
            execution_profile=(None if profile_data is None else
                               json.dumps(profile_data).encode()),
        )

    selected = artifact()
    blob = policy_blob

    @contextmanager
    def observer(_root):
        yield root

    monkeypatch.setattr(operator_module, "_remote_observation_repository", observer)
    monkeypatch.setattr(operator_module, "resolve_remote_repair_recovery",
                        lambda repo, *, failed_run_id: RemoteRepairRecovery((selected,), (failed_run_id,)))
    monkeypatch.setattr(transport, "resolve_transport_remote", lambda repo: "origin")

    def read_blob(repo, remote, sha, path):
        assert (repo, remote, path) == (root, "origin", ".ai/executor-profiles.yaml")
        assert sha == selected.candidate_sha
        return blob

    monkeypatch.setattr(transport, "_read_remote_blob", read_blob)
    assert not (root / ".ai/executor-profiles.yaml").exists()
    policy = operator_module._source_repair_failed_candidate_policy(root, intent)
    assert policy.default_model("codex") == "gpt-6-sol"

    monkeypatch.setattr(operator_module, "resolve_repository", lambda repo: root)
    monkeypatch.setattr(operator_module, "runtime_state_root", lambda repo: state)
    monkeypatch.setattr(operator_module, "preflight_repair", lambda *args, **kwargs:
                        CorrectionPreflightResult(
                            family="REPAIR", status="READY", phase="READY",
                            reason_code="READY", task_id="TASK-255", task_revision=1,
                            failed_run_id=failed_run_id, action="CODE_FIX",
                            executor_required=True))
    monkeypatch.setattr(operator_module, "observe_unified_state", lambda *args, **kwargs:
                        SimpleNamespace(
                            next_action="EXECUTE_REPAIR", failed_run_id=failed_run_id,
                            correction_sha="a" * 40,
                            correction_document={"action": "CODE_FIX"},
                            correction={"executor_required": True}, task_id="TASK-255"))
    calls = []
    monkeypatch.setattr(operator_module, "run_repair",
                        lambda *args, **kwargs: calls.append(kwargs) or
                        (_ for _ in ()).throw(OperatorError("stub stopped before RUN")))
    token = operator_module._SOURCE_REPAIR_POLICY.set(policy)
    try:
        outcome = run_repair_wakeup(
            "repair-255-003", failed_run_id, "a" * 40, repo=root, **requested
        )
    finally:
        operator_module._SOURCE_REPAIR_POLICY.reset(token)
    assert outcome.status == "FAILED" and len(calls) == 1
    assert not (state / "runs").exists()
    record = next((state / "repair-dispatches").glob("*.json"))
    assert json.loads(record.read_text(encoding="utf-8"))["model"] == "gpt-6-sol"
    before = record.read_bytes()
    token = operator_module._SOURCE_REPAIR_POLICY.set(policy)
    try:
        replay = run_repair_wakeup(
            "repair-255-003", failed_run_id, "a" * 40, repo=root, **requested
        )
        with pytest.raises(OperatorError, match="collision"):
            run_repair_wakeup(
                "repair-255-003", failed_run_id, "b" * 40, repo=root, **requested
            )
    finally:
        operator_module._SOURCE_REPAIR_POLICY.reset(token)
    assert replay.replayed and len(calls) == 1 and record.read_bytes() == before
    with pytest.raises(OperatorError, match="profile binding"):
        run_repair_wakeup("repair-255-003", failed_run_id, "a" * 40,
                          executor="codex", repo=root)

    for bad_artifact, bad_blob, error in (
        (artifact(profile_data=None), policy_blob, "profile missing"),
        (artifact(profile_data={**profile, "model": "other"}), policy_blob, "profile mismatch"),
        (artifact(candidate="d" * 40), policy_blob, "lineage mismatch"),
        (replace(artifact(), run_id="RUN-255-004"), policy_blob, "failed RUN mismatch"),
        (artifact(), None, "policy missing"),
        (artifact(), b"malformed: [", "policy rejected"),
        (artifact(), policy_blob.replace(b"default_reasoning_effort: high", b"default_reasoning_effort: low")
         .replace(b"      - high\n", b""), "unsupported reasoning effort"),
    ):
        selected, blob = bad_artifact, bad_blob
        with pytest.raises(OperatorError, match=error):
            operator_module._source_repair_failed_candidate_policy(root, intent)
    assert not (state / "runs").exists()


@pytest.mark.parametrize("target_kind", [
    "fixture", "task_206_target", "failed_task_206", "task_208_target",
    "prior_task_210", "source_primary", "task_213_candidate_current_source",
    "future_source",
])
def test_source_repair_bootstrap_production_rejects_other_targets_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str,
) -> None:
    activated = "44eee353eda376c9db8cd88d97184d3122651bf5"
    assert operator_module._SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA == activated
    source = Path(operator_module.__file__).read_text(encoding="utf-8")
    assert source.count("_SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA: str | None =") == 1
    assert source.count(f'_SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA: str | None = "{activated}"') == 1
    repo, _, bootstrap = _source_bootstrap_fixture(tmp_path)
    current_source = Path(operator_module.__file__).resolve().parents[2]
    target_sha = {
        "fixture": bootstrap["target_generation_sha"],
        "task_206_target": "37437be4e43d07d5c818022cb20d19d9c347da7c",
        "failed_task_206": "1669ef080f82862d5e7d4f607eb0d3b592011872",
        "task_208_target": "ce56528487e1f521d0c458dc4e48575620d87a42",
        "prior_task_210": "062031ab91118ecf784944e8dd7e3d76b0553c74",
        "source_primary": "31fd2482cd87d97fd818e05eb5b4dcec69ffeee6",
        "task_213_candidate_current_source": git(current_source, "rev-parse", "HEAD"),
        "future_source": "f" * 40,
    }[target_kind]
    assert target_sha != activated
    intent = _source_repair_bootstrap_intent(bootstrap, target_sha)
    path = tmp_path / "unactivated-source-repair.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: pytest.fail("legacy attestation reached"))
    monkeypatch.setattr(operator_module, "_source_repair_bootstrap_lineage",
                        lambda *args: pytest.fail("REPAIR lineage reached"))
    monkeypatch.setattr(operator_module, "run_repair_wakeup",
                        lambda *args, **kwargs: pytest.fail("REPAIR wakeup reached"))
    with pytest.raises(OperatorError, match="target is not activated"):
        operator_module.bootstrap_source_repair(
            path, runner=lambda *args, **kwargs: pytest.fail("target launched"),
        )
    bundle, target = operator_module._source_repair_bootstrap_state(repo, intent)
    assert not bundle.exists() and not target.exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_source_repair_bootstrap_closed_and_exact_replay(
    source_repair_depth_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = source_repair_depth_root
    repo, source, bootstrap = _source_bootstrap_fixture(tmp_path)
    intent = _source_repair_bootstrap_intent(bootstrap, bootstrap["target_generation_sha"])
    path = tmp_path / "source-repair.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    assert operator_module._SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA == "44eee353eda376c9db8cd88d97184d3122651bf5"
    with pytest.raises(OperatorError, match="not activated"):
        operator_module.bootstrap_source_repair(path)
    monkeypatch.setattr(operator_module, "_SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA", intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: intent["legacy_generation_sha"])
    monkeypatch.setattr(operator_module, "_source_repair_bootstrap_lineage",
                        lambda root, document: None)
    launched = []

    def runner(command, **kwargs):
        launched.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    assert operator_module.bootstrap_source_repair(path, runner=runner) == 0
    bundle, target = operator_module._source_repair_bootstrap_state(repo, intent)
    assert git(target, "rev-parse", "HEAD") == intent["target_generation_sha"]
    assert json.loads((bundle / "intent.json").read_text(encoding="utf-8")) == intent
    assert target == bundle / "s"
    other = dict(intent, repair_dispatch_id="repair-101-002")
    assert operator_module._source_repair_bootstrap_state(repo, other)[0] != bundle
    stored = json.loads((bundle / "intent.json").read_text(encoding="utf-8"))
    assert stored["repair_dispatch_id"] == intent["repair_dispatch_id"]
    other_path = tmp_path / "other-source-repair.json"
    other_path.write_text(json.dumps(other), encoding="utf-8")
    with monkeypatch.context() as collision:
        collision.setattr(operator_module, "_source_repair_bootstrap_state",
                          lambda root, document: (bundle, target))
        with pytest.raises(OperatorError, match="transport identity mismatch"):
            operator_module.bootstrap_source_repair(other_path, runner=runner)
    assert len(launched) == 1
    assert operator_module.bootstrap_source_repair(path, runner=runner) == 0
    assert len(launched) == 2
    assert all(call[0][3] == "bootstrap-source-repair" for call in launched)
    changed = dict(intent, repair_sha="b" * 40)
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(OperatorError, match="transport identity mismatch"):
        operator_module.bootstrap_source_repair(path, runner=runner)
    assert len(launched) == 2
    (bundle / "intent.json").unlink()
    path.write_text(json.dumps(intent), encoding="utf-8")
    with pytest.raises(OperatorError, match="partial or invalid"):
        operator_module.bootstrap_source_repair(path, runner=runner)
    assert len(launched) == 2


def test_source_repair_bound_target_consumes_without_matching_activation(
    source_repair_depth_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = source_repair_depth_root
    repo, _, bootstrap = _source_bootstrap_fixture(tmp_path, consumer_source=True)
    intent = _source_repair_bootstrap_intent(bootstrap, bootstrap["target_generation_sha"])
    path = tmp_path / "source-repair.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    launched = []
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: intent["legacy_generation_sha"])
    monkeypatch.setattr(operator_module, "_source_repair_bootstrap_lineage",
                        lambda root, document: None)
    monkeypatch.setattr(operator_module, "_SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA", "f" * 40)
    with pytest.raises(OperatorError, match="not activated"):
        operator_module.bootstrap_source_repair(
            path, runner=lambda *args, **kwargs: pytest.fail("target launched"),
        )
    bundle, target = operator_module._source_repair_bootstrap_state(repo, intent)
    assert not bundle.exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))

    monkeypatch.setattr(operator_module, "_SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA",
                        intent["target_generation_sha"])

    def stage_runner(command, **kwargs):
        launched.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0)

    assert operator_module.bootstrap_source_repair(path, runner=stage_runner) == 0
    assert len(launched) == 1
    bound = bundle / "intent.json"
    assert launched[0][0][-3:] == [str(bound), "--accept-transport", str(bound)]
    assert git(target, "rev-parse", "HEAD") == intent["target_generation_sha"]
    assert operator_module._source_repair_bootstrap_record(repo, intent) == (bound, target)
    assert git(target, "branch", "--show-current") == ""
    assert operator_module._git(target, "status", "--porcelain") == ""
    assert (target / "src" / "aios_renew" / "operator.py").is_file()
    longest = target / "src/aios_renew/schemas/gemini_reviewer_semantic_response.json"
    assert len(str(longest)) < 260 and longest.is_file()
    assert json.loads(bound.read_text(encoding="utf-8")) == intent
    assert operator_module.bootstrap_source_repair(path, runner=stage_runner) == 0
    assert len(launched) == 2
    assert launched[1][0] == launched[0][0]
    monkeypatch.setattr(operator_module, "_SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA", None)

    # Import the separately committed target package in a fresh interpreter.
    # The staging module's fixture activation monkeypatch cannot cross this boundary.
    child = """
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from aios_renew import operator

intent = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert operator._SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA == "44eee353eda376c9db8cd88d97184d3122651bf5"
assert intent["target_generation_sha"] != operator._SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA
lineage_checked = []
operator._source_repair_bootstrap_lineage = lambda root, document: lineage_checked.append((root, document))
operator._source_repair_failed_candidate_policy = lambda root, document: "exact-policy"
def wakeup(*args, **kwargs):
    assert Path(operator.__file__).resolve().parents[2] == Path(sys.argv[2]).resolve().parent / "s"
    assert operator._git(Path(operator.__file__).resolve().parents[2], "rev-parse", "HEAD") == intent["target_generation_sha"]
    assert lineage_checked == [(Path(intent["repository"]), intent)]
    assert operator._SOURCE_REPAIR_POLICY.get() == "exact-policy"
    assert args == (intent["repair_dispatch_id"], intent["failed_run_id"], intent["repair_sha"])
    assert kwargs == {
        "executor": intent["executor"], "repo": Path(intent["repository"]),
        "model": intent["model"], "reasoning_effort": intent["reasoning_effort"],
        "model_source": intent["model_source"], "effort_source": intent["effort_source"],
    }
    print("REPAIR_DELEGATED")
    return SimpleNamespace(render=lambda: "delivered", exit_code=0)
operator.run_repair_wakeup = wakeup
raise SystemExit(operator.main([
    "bootstrap-source-repair", sys.argv[1], "--accept-transport", sys.argv[2],
]))
"""

    def consume(intent_file: Path, transport_file: Path, *, exact_source: bool = True):
        env = dict(launched[0][1])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if not exact_source:
            env["PYTHONPATH"] = str(Path(operator_module.__file__).resolve().parents[1])
        return subprocess.run(
            [sys.executable, "-c", child, str(intent_file), str(transport_file)],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
        )

    success = consume(path, bound)
    assert success.returncode == 0, success.stderr
    assert "REPAIR_DELEGATED" in success.stdout
    for intent_file, transport_file, exact_source, error in (
        (path, tmp_path / "wrong.json", True, "transport path mismatch"),
        (path, bound, False, "running source differs"),
    ):
        rejected = consume(intent_file, transport_file, exact_source=exact_source)
        assert rejected.returncode != 0 and error in rejected.stderr
        assert "REPAIR_DELEGATED" not in rejected.stdout
        assert not list(runtime_paths(repo).runs.glob("*.json"))
    changed = dict(intent, repair_sha="b" * 40)
    bound.write_text(json.dumps(changed), encoding="utf-8")
    rejected = consume(path, bound)
    assert rejected.returncode != 0 and "transport identity mismatch" in rejected.stderr
    assert "REPAIR_DELEGATED" not in rejected.stdout
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    bound.write_text(json.dumps(intent), encoding="utf-8")
    git(target, "commit", "--allow-empty", "-m", "different target HEAD")
    rejected = consume(path, bound)
    assert rejected.returncode != 0 and "transport identity mismatch" in rejected.stderr
    assert "REPAIR_DELEGATED" not in rejected.stdout
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_source_repair_bootstrap_requires_completed_failed_v2_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, bootstrap = _source_bootstrap_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", bootstrap["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: bootstrap["source_generation_sha"])
    path = tmp_path / "source-primary.json"
    path.write_text(json.dumps(bootstrap), encoding="utf-8")
    assert operator_module.bootstrap_source_primary(
        path, runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
    ) == 0
    fingerprint = operator_module._migration_fingerprint(bootstrap)
    marker = operator_module._migration_marker(repo, fingerprint)
    intent = _source_repair_bootstrap_intent(bootstrap, bootstrap["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_migration_run_terminal",
                        lambda root, document: (intent["failed_run_id"], "FAILURE"))
    with pytest.raises(OperatorError, match="active or incomplete"):
        operator_module._source_repair_bootstrap_lineage(repo, intent)
    marker.with_suffix(".consumed").write_text(fingerprint, encoding="utf-8")
    marker.with_suffix(".completed").write_text(fingerprint, encoding="utf-8")
    operator_module._source_repair_bootstrap_lineage(repo, intent)
    monkeypatch.setattr(operator_module, "_migration_run_terminal",
                        lambda root, document: (intent["failed_run_id"], "RESULT"))
    with pytest.raises(OperatorError, match="one exact completed failed"):
        operator_module._source_repair_bootstrap_lineage(repo, intent)


@pytest.mark.parametrize("target_kind", [
    "fixture", "old_task_199", "maintenance", "current_source", "future_source",
])
def test_source_bootstrap_production_allowlist_rejects_other_targets_before_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    activated = "31fd2482cd87d97fd818e05eb5b4dcec69ffeee6"
    assert operator_module._SOURCE_BOOTSTRAP_TARGET_SHA == activated
    assert operator_module._BOOTSTRAP_TARGET_SHA == "83115b26df85a7ad6643f317833e18b18586bdbe"
    repo, _, intent = _source_bootstrap_fixture(tmp_path)
    current_source = Path(operator_module.__file__).resolve().parents[2]
    intent["target_generation_sha"] = {
        "fixture": intent["target_generation_sha"],
        "old_task_199": "ff29666d50eaf9276ab62d944018f2bbeb91f073",
        "maintenance": "f248416cf4ac8f41203898f530bc21d0500a5479",
        "current_source": git(current_source, "rev-parse", "HEAD"),
        "future_source": "f" * 40,
    }[target_kind]
    assert intent["target_generation_sha"] != activated
    path = tmp_path / "unactivated-source-intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: pytest.fail("legacy attestation reached"))
    with pytest.raises(OperatorError, match="not activated"):
        operator_module.bootstrap_source_primary(
            path, runner=lambda *a, **k: pytest.fail("target launched")
        )
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))


def test_source_bootstrap_injected_exact_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _source_bootstrap_fixture(tmp_path)
    path = tmp_path / "source-intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    launched = []
    child = lambda command, **kwargs: (
        launched.append((command, kwargs["env"])), subprocess.CompletedProcess(command, 0)
    )[1]
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: intent["source_generation_sha"])
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA",
                        intent["target_generation_sha"])
    assert operator_module.bootstrap_source_primary(path, runner=child) == 0
    assert operator_module.bootstrap_source_primary(path, runner=child) == 0
    assert len(launched) == 2 and launched[0][0] == launched[1][0]
    assert launched[0][0][3] == "bootstrap-source-primary"
    marker = Path(launched[0][0][-1])
    record = json.loads(marker.read_text(encoding="utf-8"))
    assert record["format"] == "AIOS_SOURCE_CONTROL_BOOTSTRAP_HANDOFF"
    assert record["target_control_sha"] == intent["source_control_sha"]
    assert record["source_pin_blob_sha"] == intent["source_pin_blob_sha"]
    assert record["task_blob_sha"] == intent["task_blob_sha"]
    assert record["executor"] == intent["executor"]
    assert git(repo, "rev-parse", "origin/main") == intent["source_control_sha"]
    assert intent["target_generation_sha"] not in git(
        repo, "show", f"{intent['source_control_sha']}:AIOS_PIN"
    )
    assert Path(launched[0][0][-3]).parent.parent == runtime_state_root(repo) / "m"
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_source_upgrade_stages_exact_v2_edge_without_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Git copies commit-graph filenames into the historical bundle. Keep the
    # real checkout outside pytest's deeply nested verification temp root.
    with tempfile.TemporaryDirectory(prefix="au-", dir=Path.home()) as temporary:
        _exercise_source_upgrade_stages_exact_v2_edge_without_run(
            Path(temporary), monkeypatch,
        )


def _exercise_source_upgrade_stages_exact_v2_edge_without_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_sha = operator_module._SOURCE_UPGRADE_SOURCE_SHA
    target_sha = operator_module._SOURCE_UPGRADE_TARGET_SHA
    control_source = Path(operator_module.__file__).resolve()
    environment = tmp_path / "installed-source"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip())
    package = site / "aios_renew"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    installed_operator = package / "operator.py"
    installed_operator.write_text("def migrate_primary(): pass\n# migrate-primary\n", encoding="utf-8")
    metadata = site / "aios_renew-0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: aios-renew\nVersion: 0.0\n", encoding="utf-8",
    )
    (metadata / "direct_url.json").write_text(json.dumps({
        "url": "https://example.invalid/aios-renew.git",
        "vcs_info": {"vcs": "git", "commit_id": source_sha},
    }), encoding="utf-8")
    assert installed_operator.resolve() != control_source
    monkeypatch.setattr(operator_module.sys, "executable", str(python))
    monkeypatch.setattr(operator_module.importlib.metadata, "distribution",
                        lambda name: operator_module.importlib.metadata.PathDistribution(metadata))
    assert operator_module._upgrade_installed_generation_sha() == source_sha
    with pytest.raises(OperatorError, match="imported package differs from active installed generation"):
        operator_module._installed_generation_sha()
    repo, _, intent = _source_bootstrap_fixture(
        tmp_path, source_generation=source_sha,
    )
    intent["target_generation_sha"] = target_sha
    intent["target_url"] = str(Path(operator_module.__file__).resolve().parents[2])
    path = tmp_path / "upgrade-intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    parsed = operator_module._parser().parse_args([
        "bootstrap-source-upgrade-primary", str(path),
    ])
    assert parsed.command == "bootstrap-source-upgrade-primary"
    assert parsed.intent == str(path)
    historical = dict(intent, source_generation_sha="1" * 40,
                      target_generation_sha=source_sha)
    historical_fp = operator_module._migration_fingerprint(historical)
    historical_marker = operator_module._migration_marker(repo, historical_fp)
    historical_marker.parent.mkdir(parents=True, exist_ok=True)
    historical_bundle = runtime_state_root(repo) / "m" / f"{historical_fp[:12]}-historical"
    historical_bundle.mkdir(parents=True)
    (historical_bundle / "intent.json").write_text(json.dumps(historical), encoding="utf-8")
    git(repo, "clone", str(Path(operator_module.__file__).resolve().parents[2]),
        str(historical_bundle / "source"))
    git(historical_bundle / "source", "checkout", "--detach", source_sha)
    assert git(historical_bundle / "source", "rev-parse", "HEAD") == source_sha
    assert (historical_bundle / "source" / "src" / "aios_renew" / "operator.py").is_file()
    historical_marker.write_text(json.dumps(operator_module._source_bootstrap_record(
        historical, historical_fp, historical_bundle.name)), encoding="utf-8")
    for suffix in (".consumed", ".completed"):
        historical_marker.with_suffix(suffix).write_text(historical_fp, encoding="utf-8")
    historical_files = (historical_marker, historical_marker.with_suffix(".consumed"),
                        historical_marker.with_suffix(".completed"), historical_bundle / "intent.json")
    historical_bytes = {artifact: artifact.read_bytes() for artifact in historical_files}
    with pytest.raises(OperatorError, match="imported package differs from active installed generation"):
        operator_module._require_no_active_migration(repo)
    launched = []

    def child(command, **kwargs):
        launched.append(command)
        return subprocess.CompletedProcess(command, 0)

    assert operator_module.bootstrap_source_upgrade_primary(path, runner=child) == 0
    assert operator_module.bootstrap_source_upgrade_primary(path, runner=child) == 0
    assert all(artifact.read_bytes() == content for artifact, content in historical_bytes.items())
    assert launched[0] == launched[1]
    assert launched[0][3] == "bootstrap-source-primary"
    marker = Path(launched[0][-1])
    record = json.loads(marker.read_text(encoding="utf-8"))
    assert record["format"] == "AIOS_SOURCE_CONTROL_BOOTSTRAP_HANDOFF"
    assert record["version"] == 2
    assert record["source_generation_sha"] == source_sha
    assert record["target_generation_sha"] == target_sha
    assert record["task_blob_sha"] == intent["task_blob_sha"]
    assert record["executor"] == intent["executor"]
    assert json.loads(Path(launched[0][-3]).read_text(encoding="utf-8")) == intent
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list(runtime_paths(repo).failures.glob("*.json"))

    conflict = dict(intent, executor="antigravity")
    path.write_text(json.dumps(conflict), encoding="utf-8")
    with pytest.raises(OperatorError):
        operator_module.bootstrap_source_upgrade_primary(path, runner=child)
    assert len(launched) == 2
    path.write_text(json.dumps(intent), encoding="utf-8")
    historical_marker.with_suffix(".completed").unlink()
    with pytest.raises(OperatorError, match="ambiguous source-control upgrade handoff state"):
        operator_module.bootstrap_source_upgrade_primary(path, runner=child)
    assert len(launched) == 2
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list(runtime_paths(repo).failures.glob("*.json"))


@pytest.mark.parametrize("mutation", ["source", "target", "witness", "schema"])
def test_source_upgrade_rejects_identity_before_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    source_sha = operator_module._SOURCE_UPGRADE_SOURCE_SHA
    repo, _, intent = _source_bootstrap_fixture(
        tmp_path, source_generation=source_sha,
    )
    intent["target_generation_sha"] = operator_module._SOURCE_UPGRADE_TARGET_SHA
    if mutation == "source":
        intent["source_generation_sha"] = "2" * 40
    elif mutation == "target":
        intent["target_generation_sha"] = "3" * 40
    elif mutation == "schema":
        intent["target_control_sha"] = "4" * 40
    path = tmp_path / "rejected-upgrade.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    monkeypatch.setattr(operator_module, "_upgrade_installed_generation_sha",
                        lambda **kwargs: "5" * 40 if mutation == "witness" else source_sha)
    with pytest.raises(OperatorError):
        operator_module.bootstrap_source_upgrade_primary(
            path, runner=lambda *args, **kwargs: pytest.fail("target launched"),
        )
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list(runtime_paths(repo).failures.glob("*.json"))


def test_source_upgrade_witness_isolated_installed_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "upgrade-env"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip())
    package = site / "aios_renew"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    active = package / "operator.py"
    active.write_text("def migrate_primary(): pass\n# migrate-primary\n", encoding="utf-8")
    metadata = site / "aios_renew-0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: aios-renew\nVersion: 0.0\n")
    origin = {
        "url": "https://example.invalid/aios-renew.git",
        "vcs_info": {"vcs": "git", "commit_id": operator_module._SOURCE_UPGRADE_SOURCE_SHA},
    }
    direct_url = metadata / "direct_url.json"
    direct_url.write_text(json.dumps(origin), encoding="utf-8")
    synchronized = tmp_path / "synchronized" / "aios_renew"
    synchronized.mkdir(parents=True)
    (synchronized / "__init__.py").write_text("", encoding="utf-8")
    (synchronized / "operator.py").write_text("# no migration capability\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(synchronized.parent))
    monkeypatch.setattr(operator_module.sys, "executable", str(python))
    assert operator_module._upgrade_installed_generation_sha() == operator_module._SOURCE_UPGRADE_SOURCE_SHA
    with pytest.raises(OperatorError, match="legacy installed generation attestation failed"):
        operator_module._legacy_installed_generation_sha()

    for invalid in ("editable", "alternate", "legacy", "path_mismatch"):
        if invalid == "editable":
            origin["dir_info"] = {"editable": True}
            direct_url.write_text(json.dumps(origin), encoding="utf-8")
        elif invalid == "alternate":
            origin.pop("dir_info")
            origin["vcs_info"]["commit_id"] = "a" * 40
            direct_url.write_text(json.dumps(origin), encoding="utf-8")
        elif invalid == "legacy":
            origin["vcs_info"]["commit_id"] = operator_module._SOURCE_UPGRADE_SOURCE_SHA
            direct_url.write_text(json.dumps(origin), encoding="utf-8")
            active.write_text("# legacy-only source\n", encoding="utf-8")
        else:
            active.write_text("def migrate_primary(): pass\n# migrate-primary\n", encoding="utf-8")
            shadow = tmp_path / "shadow" / "aios_renew"
            shadow.mkdir(parents=True)
            (shadow / "__init__.py").write_text("", encoding="utf-8")
            (shadow / "operator.py").write_text(
                "def migrate_primary(): pass\n# migrate-primary\n", encoding="utf-8",
            )
            (site / "sitecustomize.py").write_text(
                f"import sys\nsys.path.insert(0, {str(shadow.parent)!r})\n",
                encoding="utf-8",
            )
        with pytest.raises(OperatorError, match="upgrade installed generation attestation failed"):
            operator_module._upgrade_installed_generation_sha()


def test_source_upgrade_rejects_dirty_control_without_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_sha = operator_module._SOURCE_UPGRADE_SOURCE_SHA
    repo, _, intent = _source_bootstrap_fixture(
        tmp_path, source_generation=source_sha,
    )
    intent["target_generation_sha"] = operator_module._SOURCE_UPGRADE_TARGET_SHA
    path = tmp_path / "dirty-upgrade.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    (repo / "untracked.txt").write_text("dirty", encoding="utf-8")
    monkeypatch.setattr(operator_module, "_upgrade_installed_generation_sha",
                        lambda **kwargs: source_sha)
    with pytest.raises(OperatorError):
        operator_module.bootstrap_source_upgrade_primary(
            path, runner=lambda *args, **kwargs: pytest.fail("target launched"),
        )
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list(runtime_paths(repo).failures.glob("*.json"))


@pytest.mark.parametrize("mutation", ["legacy", "source", "pin", "task", "target"])
def test_source_bootstrap_rejects_unbound_identity_before_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    repo, _, intent = _source_bootstrap_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA",
                        intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: "1" * 40)
    field = {"legacy": "source_generation_sha", "source": "source_control_sha",
             "pin": "source_pin_blob_sha", "task": "task_blob_sha",
             "target": "target_generation_sha"}[mutation]
    intent[field] = "2" * 40
    path = tmp_path / "bad-source-intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    with pytest.raises(OperatorError):
        operator_module.bootstrap_source_primary(
            path, runner=lambda *a, **k: pytest.fail("target launched")
        )
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))


@pytest.mark.parametrize("unsafe", ["dirty", "detached", "upstream"])
def test_source_bootstrap_rejects_unsafe_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    repo, _, intent = _source_bootstrap_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA",
                        intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: intent["source_generation_sha"])
    if unsafe == "dirty":
        (repo / "untracked.txt").write_text("dirty")
    elif unsafe == "detached":
        git(repo, "checkout", "--detach", intent["source_control_sha"])
    else:
        git(repo, "config", "--add", "branch.main.remote", "origin")
    path = tmp_path / "unsafe-source-intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    with pytest.raises(OperatorError):
        operator_module.bootstrap_source_primary(
            path, runner=lambda *a, **k: pytest.fail("target launched")
        )
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))


@pytest.mark.parametrize("terminal", [None, "RESULT", "FAILURE"])
def test_source_bootstrap_target_reconciles_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: str | None
) -> None:
    repo, _, intent = _source_bootstrap_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA",
                        intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: intent["source_generation_sha"])
    path = tmp_path / "source-intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    launched = []
    def child(command, **kwargs):
        launched.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0)
    assert operator_module.bootstrap_source_primary(path, runner=child) == 0
    marker = Path(launched[0][0][-1])
    target_package = Path(launched[0][1]["PYTHONPATH"].split(operator_module.os.pathsep)[0])
    with pytest.raises(OperatorError, match="running source differs"):
        operator_module.bootstrap_source_primary(path, handoff_path=marker)
    monkeypatch.setattr(operator_module, "__file__", str(target_package / "aios_renew" / "operator.py"))
    invocations = []
    def admitted(*args, **kwargs):
        invocations.append(kwargs)
        record = operator_module._require_migration_target(repo, marker.stem)
        assert record["format"] == "AIOS_SOURCE_CONTROL_BOOTSTRAP_HANDOFF"
        with pytest.raises(OperatorError, match="differs from exact admitted TASK"):
            operator_module._validate_migration_execution(
                record, task_id=intent["task_id"], executor="antigravity",
                synchronize=False, preflight_sha=intent["source_control_sha"],
                dispatch_id=None, task_revision=intent["task_revision"],
                task_blob_sha=intent["task_blob_sha"],
                task_commit_sha=intent["task_commit_sha"],
            )
        state = runtime_paths(repo)
        run_id = "RUN-101-001"
        (state.runs / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "task": {"id": intent["task_id"], "revision": 2},
            "executor": intent["executor"], "base_sha": intent["source_control_sha"],
            "workspace": str(repo), "status": "ACTIVE",
        }))
        if terminal is not None:
            artifact = (
                {"result": {"head_sha": intent["source_control_sha"], "claims": [],
                            "changed_files": [], "unresolved": []}, "evidence": []}
                if terminal == "RESULT" else
                {"kind": "FAILURE", "run_id": run_id,
                 "task": {"id": intent["task_id"], "revision": 2},
                 "executor": intent["executor"], "base_sha": intent["source_control_sha"]}
            )
            destination = state.results if terminal == "RESULT" else state.failures
            (destination / f"{run_id}.json").write_text(json.dumps(artifact), encoding="utf-8")
        return SimpleNamespace(render=lambda: "ADMITTED")
    monkeypatch.setattr(operator_module, "run_task", admitted)
    if terminal is None:
        with pytest.raises(OperatorError, match="without a terminal RESULT"):
            operator_module.bootstrap_source_primary(path, handoff_path=marker)
        with pytest.raises(OperatorError, match="is active"):
            operator_module.bootstrap_source_primary(path, handoff_path=marker)
    elif terminal == "RESULT":
        assert operator_module.bootstrap_source_primary(path, handoff_path=marker) == 0
        assert operator_module.bootstrap_source_primary(path, handoff_path=marker) == 0
    else:
        with pytest.raises(OperatorError, match="without a terminal RESULT"):
            operator_module.bootstrap_source_primary(path, handoff_path=marker)
        assert operator_module.bootstrap_source_primary(path, handoff_path=marker) == 1
    assert len(invocations) == 1
    assert invocations[0]["synchronize"] is False
    assert invocations[0]["preflight_sha"] == intent["source_control_sha"]
    assert invocations[0]["_migration_handoff"] == marker.stem


def test_source_bootstrap_rejects_replayed_or_mismatched_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _source_bootstrap_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA",
                        intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: intent["source_generation_sha"])
    path = tmp_path / "source-intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    launches = []
    def child(command, **kwargs):
        launches.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0)
    assert operator_module.bootstrap_source_primary(path, runner=child) == 0
    marker = Path(launches[0][0][-1])
    target_package = Path(launches[0][1]["PYTHONPATH"].split(operator_module.os.pathsep)[0])
    monkeypatch.setattr(operator_module, "__file__", str(target_package / "aios_renew" / "operator.py"))
    run_called = lambda *a, **k: pytest.fail("second Executor invocation")
    monkeypatch.setattr(operator_module, "run_task", run_called)
    record = json.loads(marker.read_text(encoding="utf-8"))
    record["executor"] = "antigravity"
    marker.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(OperatorError, match="record mismatch"):
        operator_module.bootstrap_source_primary(path, handoff_path=marker)
    record["executor"] = intent["executor"]
    marker.write_text(json.dumps(record), encoding="utf-8")
    state = runtime_paths(repo)
    (state.runs / "RUN-101-001.json").write_text(json.dumps({
        "run_id": "RUN-101-001", "task": {"id": intent["task_id"], "revision": 2},
        "executor": "antigravity", "base_sha": intent["source_control_sha"],
        "workspace": str(repo), "status": "ACTIVE",
    }), encoding="utf-8")
    with pytest.raises(OperatorError, match="mismatched source-control bootstrap RUN"):
        operator_module.bootstrap_source_primary(path, handoff_path=marker)
    assert not marker.with_suffix(".completed").exists()


def test_source_bootstrap_stale_upstream_after_edge_has_no_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _source_bootstrap_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA",
                        intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: intent["source_generation_sha"])
    path = tmp_path / "source-intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    launched = []
    def child(command, **kwargs):
        launched.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0)
    assert operator_module.bootstrap_source_primary(path, runner=child) == 0
    marker = Path(launched[0][0][-1])
    target_package = Path(launched[0][1]["PYTHONPATH"].split(operator_module.os.pathsep)[0])
    monkeypatch.setattr(operator_module, "__file__", str(target_package / "aios_renew" / "operator.py"))
    publish_upstream(repo, {"LATER.txt": "upstream advanced\n"}, "later upstream")
    monkeypatch.setattr(operator_module, "run_task",
                        lambda *a, **k: pytest.fail("Executor invoked"))
    with pytest.raises(OperatorError, match="source control SHA is stale|target control SHA is stale"):
        operator_module.bootstrap_source_primary(path, handoff_path=marker)
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not marker.with_suffix(".completed").exists()


def test_migration_run_history_accepts_nested_remediation_and_rejects_unknown(
    tmp_path: Path,
) -> None:
    repo, _, intent = _source_bootstrap_fixture(tmp_path)
    state = runtime_paths(repo)
    run = {"run_id": "RUN-999-001", "task": {"id": "TASK-999", "revision": 1},
           "executor": "codex", "base_sha": "a" * 40,
           "workspace": str(repo), "status": "ACTIVE"}
    (state.runs / "RUN-999-001.json").write_text(json.dumps(run), encoding="utf-8")
    remediation = {"kind": "REMEDIATION", "execution": {"run": {
        **run, "run_id": "RUN-998-001"}}}
    path = state.runs / "RUN-998-001.json"
    path.write_text(json.dumps(remediation), encoding="utf-8")
    assert operator_module._migration_run_terminal(repo, intent) == (None, None)
    matching = {**run, "run_id": "RUN-998-001", "base_sha": intent["source_control_sha"],
                "task": {"id": intent["task_id"], "revision": intent["task_revision"]}}
    path.write_text(json.dumps({"kind": "REMEDIATION", "execution": {"run": matching}}),
                    encoding="utf-8")
    assert operator_module._migration_run_terminal(repo, intent) == ("RUN-998-001", None)
    second = {**matching, "run_id": "RUN-999-001"}
    (state.runs / "RUN-999-001.json").write_text(json.dumps(second), encoding="utf-8")
    with pytest.raises(OperatorError, match="ambiguous migration RUN history"):
        operator_module._migration_run_terminal(repo, intent)
    (state.runs / "RUN-999-001.json").write_text(json.dumps(run), encoding="utf-8")
    path.write_text(json.dumps({"kind": "UNKNOWN", "execution": {"run": run}}), encoding="utf-8")
    with pytest.raises(OperatorError, match="invalid migration RUN"):
        operator_module._migration_run_terminal(repo, intent)
    path.write_text(json.dumps({"kind": "REMEDIATION", "execution": {"run": {}}}), encoding="utf-8")
    with pytest.raises(OperatorError, match="invalid migration RUN"):
        operator_module._migration_run_terminal(repo, intent)


def test_source_bootstrap_production_rejects_retired_replacement_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    activated = "31fd2482cd87d97fd818e05eb5b4dcec69ffeee6"
    assert operator_module._SOURCE_BOOTSTRAP_TARGET_SHA == activated
    repo, _, old = _source_bootstrap_fixture(tmp_path)
    old_path = tmp_path / "old.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA",
                      old["target_generation_sha"])
        patch.setattr(operator_module, "_legacy_installed_generation_sha",
                      lambda **kwargs: old["source_generation_sha"])
        assert operator_module.bootstrap_source_primary(
            old_path, runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
        ) == 0
    old_marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(old))
    old_marker.with_suffix(".consumed").write_text(old_marker.stem, encoding="utf-8")
    old_bytes = old_marker.read_bytes()
    replacement = dict(old, target_generation_sha="ff29666d50eaf9276ab62d944018f2bbeb91f073")
    replacement_path = tmp_path / "replacement.json"
    replacement_path.write_text(json.dumps(replacement), encoding="utf-8")
    with pytest.raises(OperatorError, match="target is not activated"):
        operator_module.recover_source_bootstrap(old_path, replacement_path)
    assert old_marker.read_bytes() == old_bytes
    assert old_marker.with_suffix(".consumed").read_text(encoding="utf-8") == old_marker.stem
    assert not old_marker.with_suffix(".superseded").exists()
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(list(old_marker.parent.glob("*.json"))) == 1


@pytest.mark.parametrize("new_revision", [2, 3])
def test_source_bootstrap_recovery_exact_replay_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, new_revision: int,
) -> None:
    repo, target_source, old = _source_bootstrap_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", old["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: old["source_generation_sha"])
    installed = [old["target_generation_sha"]]
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: installed[0])
    old_path = tmp_path / "old.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    assert operator_module.bootstrap_source_primary(
        old_path, runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
    ) == 0
    old_marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(old))
    old_marker.with_suffix(".consumed").write_text(old_marker.stem, encoding="utf-8")
    old_bytes = {p: p.read_bytes() for p in (
        old_marker, old_marker.with_suffix(".consumed"))}
    assert (old_marker.parent.parent / "m").is_dir()
    with pytest.raises(OperatorError, match="not completed"):
        operator_module._require_no_active_migration(repo)

    new = dict(old)
    if new_revision > 2:
        control_sha = publish_upstream(
            repo, {".ai/tasks/TASK-101.yaml": TASK_SOURCE.replace("revision: 1", "revision: 3")},
            "authorized successor revision",
        )
        git(repo, "fetch", "origin", "main")
        git(repo, "merge", "--ff-only", "origin/main")
        new.update(source_control_sha=control_sha, task_revision=3,
                   task_blob_sha=git(repo, "rev-parse", f"{control_sha}:.ai/tasks/TASK-101.yaml"),
                   task_commit_sha=control_sha)
    operator_file = target_source / "src" / "aios_renew" / "operator.py"
    operator_file.write_text("# separately activated successor target\n", encoding="utf-8")
    git(target_source, "add", ".")
    git(target_source, "commit", "-m", "activated replacement target")
    new["target_generation_sha"] = git(target_source, "rev-parse", "HEAD")
    installed[0] = new["target_generation_sha"]
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", new["target_generation_sha"])
    new_path = tmp_path / "replacement.json"
    new_path.write_text(json.dumps(new), encoding="utf-8")
    with RepositoryLock(runtime_paths(repo).lock):
        with pytest.raises(OperatorError, match="another AIOS run"):
            operator_module.recover_source_bootstrap(old_path, new_path)
    assert not old_marker.with_suffix(".superseded").exists()
    assert operator_module.recover_source_bootstrap(old_path, new_path) == 0
    assert operator_module.recover_source_bootstrap(old_path, new_path) == 0
    assert all(p.read_bytes() == content for p, content in old_bytes.items())
    new_marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(new))
    link = json.loads(old_marker.with_suffix(".superseded").read_text(encoding="utf-8"))
    assert link == json.loads(new_marker.with_suffix(".predecessor").read_text(encoding="utf-8"))
    assert link["replacement_fingerprint"] == new_marker.stem
    assert new_marker.is_file()
    with pytest.raises(OperatorError, match="not completed"):
        operator_module._require_no_active_migration(repo)
    with pytest.raises(OperatorError, match="superseded"):
        operator_module.bootstrap_source_primary(old_path, handoff_path=old_marker)
    record = json.loads(new_marker.read_text(encoding="utf-8"))
    bound, target = operator_module._migration_bundle(new_marker, record, new)
    assert bound.is_file()
    monkeypatch.setattr(operator_module, "__file__", str(target / "src" / "aios_renew" / "operator.py"))
    invocations = []
    def admitted(*args, **kwargs):
        invocations.append(kwargs)
        assert operator_module._require_migration_target(repo, new_marker.stem) == record
        state = runtime_paths(repo)
        run_id = "RUN-101-002"
        (state.runs / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "task": {"id": new["task_id"], "revision": new_revision},
            "executor": new["executor"], "base_sha": new["source_control_sha"],
            "workspace": str(repo), "status": "ACTIVE",
        }), encoding="utf-8")
        (state.results / f"{run_id}.json").write_text(json.dumps({
            "result": {"head_sha": new["source_control_sha"], "claims": [],
                       "changed_files": [], "unresolved": []}, "evidence": [],
        }), encoding="utf-8")
        return SimpleNamespace(render=lambda: "ADMITTED")
    monkeypatch.setattr(operator_module, "run_task", admitted)
    assert operator_module.bootstrap_source_primary(new_path, handoff_path=new_marker) == 0
    assert operator_module.bootstrap_source_primary(new_path, handoff_path=new_marker) == 0
    assert len(invocations) == 1
    assert invocations[0]["synchronize"] is False
    assert invocations[0]["task_revision"] == new_revision
    operator_module._require_no_active_migration(repo)
    installed[0] = old["target_generation_sha"]
    with pytest.raises(OperatorError, match="superseded"):
        operator_module._require_no_active_migration(repo)
    old_marker.with_suffix(".superseded").unlink()
    with pytest.raises(OperatorError):
        operator_module._require_no_active_migration(repo)


def test_source_bootstrap_recovery_preserves_completed_migration_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, migration_storage_root: Path,
) -> None:
    # Cover RUN-244-003's cwd/CLONE depth without inheriting pytest/TEMP depth
    # for the real Git storage. The existing fixture owns the short profile.
    padding = 184 - len(str(migration_storage_root / "control" / "repo")) - 1
    assert 1 <= padding <= 200
    depth = migration_storage_root / ("d" * padding)
    depth.mkdir()
    repo, target_source, old = _source_bootstrap_fixture(depth)
    assert len(str(repo)) == 184
    assert len(str(target_source)) >= 184
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", old["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: old["source_generation_sha"])
    old_path = tmp_path / "old.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    assert operator_module.bootstrap_source_primary(
        old_path, runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
    ) == 0
    old_marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(old))
    old_marker.with_suffix(".consumed").write_text(old_marker.stem, encoding="utf-8")

    # A distinct v1 migration has a valid bound source and durable completion.
    operator_file = target_source / "src" / "aios_renew" / "operator.py"
    operator_file.write_text("# historical migration target\n", encoding="utf-8")
    git(target_source, "add", ".")
    git(target_source, "commit", "-m", "historical migration target")
    historical = {key: value for key, value in old.items() if key != "format"}
    historical.update(version=1, source_generation_sha="2" * 40,
                      target_generation_sha=git(target_source, "rev-parse", "HEAD"),
                      target_control_sha=old["source_control_sha"],
                      target_pin_blob_sha=old["source_pin_blob_sha"])
    historical_fp = operator_module._migration_fingerprint(historical)
    historical_marker = operator_module._migration_marker(repo, historical_fp)
    bundle = runtime_state_root(repo) / "m" / f"{historical_fp[:12]}-historical"
    bundle.mkdir()
    (bundle / "intent.json").write_text(json.dumps(historical), encoding="utf-8")
    # Keep the historical reader-visible worktree, but bound Git's internal
    # paths using the same owner-validated layout already accepted by readers.
    storage = operator_module._migration_target_storage(
        historical_marker, historical, bundle, create=True,
    )
    assert len(str(bundle / "source")) >= 220
    assert len(str(storage / "g")) + 99 < 260
    git(target_source, "clone", "--no-local", "--no-checkout", "--no-tags",
        "--separate-git-dir", str(storage / "g"),
        str(target_source), str(bundle / "source"))
    git(bundle / "source", "checkout", "--detach", historical["target_generation_sha"])
    historical_marker.write_text(json.dumps(operator_module._migration_record(
        historical, historical_fp, bundle.name)), encoding="utf-8")
    assert operator_module._migration_bundle(
        historical_marker, json.loads(historical_marker.read_text(encoding="utf-8")),
        historical,
    ) == (bundle / "intent.json", bundle / "source")
    historical_marker.with_suffix(".consumed").write_text(historical_fp, encoding="utf-8")

    new = dict(old)
    operator_file.write_text("# activated replacement target\n", encoding="utf-8")
    git(target_source, "add", ".")
    git(target_source, "commit", "-m", "activated replacement target")
    new["target_generation_sha"] = git(target_source, "rev-parse", "HEAD")
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", new["target_generation_sha"])
    new_path = tmp_path / "replacement.json"
    new_path.write_text(json.dumps(new), encoding="utf-8")

    # An uncompleted second edge remains conflicting active authority.
    with pytest.raises(OperatorError, match="sole active handoff"):
        operator_module.recover_source_bootstrap(old_path, new_path)
    historical_marker.with_suffix(".completed").write_text(historical_fp, encoding="utf-8")
    historical_bytes = {path: path.read_bytes() for path in (
        historical_marker, historical_marker.with_suffix(".consumed"),
        historical_marker.with_suffix(".completed"),
        bundle / "intent.json")}
    assert operator_module.recover_source_bootstrap(old_path, new_path) == 0
    assert all(path.read_bytes() == content for path, content in historical_bytes.items())
    assert not list(runtime_paths(repo).runs.glob("*.json"))


@pytest.mark.parametrize("change", [
    "task", "executor", "pin", "control", "bound_run", "dirty", "stale",
    "completed", "nonfastforward", "pin_change", "same_revision_blob", "new_revision_unbound",
])
def test_source_bootstrap_recovery_rejects_cross_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    repo, target_source, old = _source_bootstrap_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", old["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: old["source_generation_sha"])
    old_path = tmp_path / "old.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    operator_module.bootstrap_source_primary(
        old_path, runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
    )
    old_marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(old))
    old_marker.with_suffix(".consumed").write_text(old_marker.stem, encoding="utf-8")
    operator_file = target_source / "src" / "aios_renew" / "operator.py"
    operator_file.write_text("# new target\n", encoding="utf-8")
    git(target_source, "add", ".")
    git(target_source, "commit", "-m", "new target")
    new = dict(old, target_generation_sha=git(target_source, "rev-parse", "HEAD"))
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", new["target_generation_sha"])
    if change == "task":
        new["task_id"] = "TASK-102"
    elif change == "executor":
        new["executor"] = "antigravity"
    elif change == "pin":
        new["source_pin_blob_sha"] = "a" * 40
    elif change == "control":
        new["source_control_sha"] = "a" * 40
    elif change == "dirty":
        (repo / "untracked.txt").write_text("dirty", encoding="utf-8")
    elif change == "stale":
        publish_upstream(repo, {"LATER.txt": "later\n"}, "later upstream")
    elif change == "completed":
        old_marker.with_suffix(".completed").write_text(old_marker.stem, encoding="utf-8")
    elif change == "nonfastforward":
        new["source_control_sha"] = new["target_generation_sha"]
    elif change == "pin_change":
        changed = publish_upstream(repo, {"AIOS_PIN": f"other @ {old['source_generation_sha']}\n"},
                                   "changed legacy pin")
        git(repo, "fetch", "origin", "main")
        git(repo, "merge", "--ff-only", "origin/main")
        new["source_control_sha"] = changed
        new["source_pin_blob_sha"] = git(repo, "rev-parse", f"{changed}:AIOS_PIN")
    elif change == "same_revision_blob":
        new["task_blob_sha"] = "a" * 40
    elif change == "new_revision_unbound":
        new["task_revision"] = 3
    else:
        state = runtime_paths(repo)
        (state.runs / "RUN-101-001.json").write_text(json.dumps({
            "run_id": "RUN-101-001", "task": {"id": "TASK-101", "revision": 2},
            "executor": "codex", "base_sha": old["source_control_sha"],
            "workspace": str(repo), "status": "ACTIVE",
        }), encoding="utf-8")
    new_path = tmp_path / "replacement.json"
    new_path.write_text(json.dumps(new), encoding="utf-8")
    with pytest.raises(OperatorError):
        operator_module.recover_source_bootstrap(old_path, new_path)
    assert not old_marker.with_suffix(".superseded").exists()
    assert len(list(old_marker.parent.glob("*.json"))) == 1


def test_source_bootstrap_recovery_interruption_keeps_old_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, target_source, old = _source_bootstrap_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", old["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha",
                        lambda **kwargs: old["source_generation_sha"])
    old_path = tmp_path / "old.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    operator_module.bootstrap_source_primary(
        old_path, runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
    )
    old_marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(old))
    old_marker.with_suffix(".consumed").write_text(old_marker.stem, encoding="utf-8")
    operator_file = target_source / "src" / "aios_renew" / "operator.py"
    operator_file.write_text("# replacement target\n", encoding="utf-8")
    git(target_source, "add", ".")
    git(target_source, "commit", "-m", "replacement target")
    new = dict(old, target_generation_sha=git(target_source, "rev-parse", "HEAD"))
    monkeypatch.setattr(operator_module, "_SOURCE_BOOTSTRAP_TARGET_SHA", new["target_generation_sha"])
    new_path = tmp_path / "replacement.json"
    new_path.write_text(json.dumps(new), encoding="utf-8")
    original_write = operator_module._write_migration_atomic
    def interrupt_commit(path, content):
        if path == old_marker.with_suffix(".superseded"):
            raise OSError("simulated interruption before commit")
        return original_write(path, content)
    monkeypatch.setattr(operator_module, "_write_migration_atomic", interrupt_commit)
    with pytest.raises(OSError, match="simulated interruption"):
        operator_module.recover_source_bootstrap(old_path, new_path)
    assert old_marker.with_suffix(".consumed").read_text(encoding="utf-8") == old_marker.stem
    assert not old_marker.with_suffix(".superseded").exists()
    new_marker = operator_module._migration_marker(repo, operator_module._migration_fingerprint(new))
    assert new_marker.is_file() and new_marker.with_suffix(".predecessor").is_file()
    edges, superseded, pending = operator_module._migration_history(repo)
    assert old_marker.stem in edges and old_marker.stem not in superseded
    assert new_marker.stem in pending
    old_record = json.loads(old_marker.read_text(encoding="utf-8"))
    _, old_target = operator_module._migration_bundle(old_marker, old_record, old)
    monkeypatch.setattr(operator_module, "__file__",
                        str(old_target / "src" / "aios_renew" / "operator.py"))
    assert operator_module._require_migration_target(repo, old_marker.stem) == old_record
    with pytest.raises(OperatorError, match="not the active exact handoff"):
        operator_module._require_migration_target(repo, new_marker.stem)
    monkeypatch.setattr(operator_module, "_installed_generation_sha",
                        lambda: old["target_generation_sha"])
    with pytest.raises(OperatorError, match="not completed"):
        operator_module._require_no_active_migration(repo)
    with pytest.raises(OperatorError):
        operator_module.recover_source_bootstrap(old_path, new_path)
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_bootstrap_attests_imported_legacy_install_without_control_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = tmp_path / "legacy-env"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip())
    package = site / "aios_renew"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    active = package / "operator.py"
    active.write_text("LEGACY = True\n", encoding="utf-8")
    metadata = site / "aios_renew-0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: aios-renew\nVersion: 0.0\n")
    (metadata / "direct_url.json").write_text(json.dumps({
        "url": "https://example.invalid/aios-renew.git",
        "vcs_info": {"vcs": "git", "commit_id": "a" * 40},
    }))
    synchronized = tmp_path / "synchronized" / "aios_renew"
    synchronized.mkdir(parents=True)
    (synchronized / "__init__.py").write_text("", encoding="utf-8")
    (synchronized / "operator.py").write_text("def migrate_primary(): pass\n")
    monkeypatch.setenv("PYTHONPATH", str(synchronized.parent))
    monkeypatch.setattr(operator_module.sys, "executable", str(python))
    assert operator_module._legacy_installed_generation_sha() == "a" * 40
    active.write_text("def migrate_primary(): pass\n# migrate-primary\n", encoding="utf-8")
    with pytest.raises(OperatorError, match="attestation failed"):
        operator_module._legacy_installed_generation_sha()


def test_bootstrap_exact_edge_reentry_and_target_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_BOOTSTRAP_TARGET_SHA", intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha", lambda **kwargs: intent["source_generation_sha"])
    intent_path = tmp_path / "intent.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    launched = []

    def child(command, **kwargs):
        launched.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0)

    assert operator_module.bootstrap_primary(intent_path, runner=child) == 0
    assert operator_module.bootstrap_primary(intent_path, runner=child) == 0
    assert len(launched) == 2
    assert launched[0][0] == launched[1][0]
    marker = Path(launched[0][0][-1])
    assert marker.is_file()
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert len(list(marker.parent.glob("*.json"))) == 1
    target_package = Path(launched[0][1]["PYTHONPATH"].split(operator_module.os.pathsep)[0])
    monkeypatch.setattr(operator_module, "__file__", str(target_package / "aios_renew" / "operator.py"))
    invocations = []

    def admitted(*args, **kwargs):
        invocations.append(kwargs)
        state = runtime_paths(repo)
        run_id = "RUN-101-001"
        (state.runs / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "task": {"id": intent["task_id"], "revision": 2},
            "executor": intent["executor"], "base_sha": intent["target_control_sha"],
            "workspace": str(repo), "status": "ACTIVE",
        }))
        (state.results / f"{run_id}.json").write_text(json.dumps({
            "result": {"head_sha": intent["target_control_sha"], "claims": [],
                       "changed_files": [], "unresolved": []}, "evidence": [],
        }))
        return SimpleNamespace(render=lambda: "ADMITTED")

    monkeypatch.setattr(operator_module, "run_task", admitted)
    assert operator_module.migrate_primary(intent_path, handoff_path=marker) == 0
    assert operator_module.migrate_primary(intent_path, handoff_path=marker) == 0
    assert len(invocations) == 1
    with pytest.raises(OperatorError, match="already completed"):
        operator_module.bootstrap_primary(intent_path, runner=child)


@pytest.mark.parametrize("mutation", ["target", "source", "control", "pin", "task"])
def test_bootstrap_rejects_unbound_intent_before_target_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_BOOTSTRAP_TARGET_SHA", intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha", lambda **kwargs: "1" * 40)
    if mutation == "target":
        intent["target_generation_sha"] = "2" * 40
    elif mutation == "source":
        intent["source_generation_sha"] = "2" * 40
    elif mutation == "control":
        intent["target_control_sha"] = "2" * 40
    elif mutation == "pin":
        intent["target_pin_blob_sha"] = "2" * 40
    else:
        intent["task_blob_sha"] = "2" * 40
    path = tmp_path / "intent.json"
    path.write_text(json.dumps(intent))
    launched = []
    with pytest.raises(OperatorError):
        operator_module.bootstrap_primary(
            path, runner=lambda *a, **k: launched.append(a)
        )
    assert launched == []
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))


def test_target_cannot_self_authorize_legacy_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    path = tmp_path / "intent.json"
    path.write_text(json.dumps(intent))
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["target_generation_sha"])
    with pytest.raises(OperatorError, match="source generation mismatch"):
        operator_module.migrate_primary(path)
    assert not list(runtime_paths(repo).runs.glob("*.json"))


@pytest.mark.parametrize("unsafe", ["dirty", "detached"])
def test_bootstrap_rejects_unsafe_control_without_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_BOOTSTRAP_TARGET_SHA", intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha", lambda **kwargs: intent["source_generation_sha"])
    if unsafe == "dirty":
        (repo / "untracked.txt").write_text("dirty")
    else:
        git(repo, "checkout", "--detach", intent["source_control_sha"])
    path = tmp_path / "intent.json"
    path.write_text(json.dumps(intent))
    with pytest.raises(OperatorError):
        operator_module.bootstrap_primary(path, runner=lambda *a, **k: pytest.fail("target launched"))
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))


def test_bootstrap_ambiguous_reentry_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_BOOTSTRAP_TARGET_SHA", intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha", lambda **kwargs: intent["source_generation_sha"])
    path = tmp_path / "intent.json"
    path.write_text(json.dumps(intent))
    launches = []

    def child(command, **kwargs):
        launches.append(command)
        return subprocess.CompletedProcess(command, 0)

    assert operator_module.bootstrap_primary(path, runner=child) == 0
    marker = Path(launches[0][-1])
    (marker.parent / ("f" * 64 + ".json")).write_text("{}")
    with pytest.raises(OperatorError, match="ambiguous bootstrap"):
        operator_module.bootstrap_primary(path, runner=child)
    assert len(launches) == 1
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_bootstrap_interrupted_pre_edge_transport_stays_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_BOOTSTRAP_TARGET_SHA", intent["target_generation_sha"])
    monkeypatch.setattr(operator_module, "_legacy_installed_generation_sha", lambda **kwargs: intent["source_generation_sha"])
    path = tmp_path / "intent.json"
    path.write_text(json.dumps(intent))
    transport = runtime_state_root(repo) / "m"
    transport.mkdir(parents=True)
    orphan = transport / (operator_module._migration_fingerprint(intent)[:12] + "-orphan")
    orphan.mkdir()
    with pytest.raises(OperatorError, match="orphaned migration transport"):
        operator_module.bootstrap_primary(path, runner=lambda *a, **k: pytest.fail("target launched"))
    assert list(transport.iterdir()) == [orphan]
    assert not list(runtime_paths(repo).runs.glob("*.json"))


def test_restart_keeps_active_source_when_control_moves_task_and_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    package = repo / "src" / "aios_renew"
    package.mkdir(parents=True)
    (package / "operator.py").write_text("# generation N\n", encoding="utf-8")
    monkeypatch.setattr(operator_module, "__file__", str(package / "operator.py"))
    observed = []
    with operator_module._active_restart_source(repo) as snapshot:
        assert snapshot is not None
        (package / "operator.py").write_text("# synchronized N+1\n", encoding="utf-8")
        monkeypatch.setenv("PYTHONPATH", str(repo / "src"))

        def child(command, **kwargs):
            path = Path(kwargs["env"]["PYTHONPATH"].split(operator_module.os.pathsep)[0])
            observed.append((path, (path / "aios_renew" / "operator.py").read_text()))
            assert str(repo / "src") not in kwargs["env"]["PYTHONPATH"]
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with operator_module._restart_source_environment(snapshot):
            assert operator_module._restart_primary_invocation(repo, runner=child) == 0
    assert observed[0][1] == "# generation N\n"
    assert not observed[0][0].exists()


def test_migration_exact_handoff_and_replay_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, target_source, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["source_generation_sha"])
    intent_path = tmp_path / "intent.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    observed = []

    def child(command, **kwargs):
        observed.append((command, kwargs["env"]))
        package_root = Path(kwargs["env"]["PYTHONPATH"].split(operator_module.os.pathsep)[0])
        assert git(package_root.parent, "rev-parse", "HEAD") == intent["target_generation_sha"]
        assert str(repo / "src") not in kwargs["env"]["PYTHONPATH"]
        assert git(repo, "rev-parse", "HEAD") == intent["source_control_sha"]
        assert git(repo, "rev-parse", "origin/main") == intent["source_control_sha"]
        assert not list(runtime_paths(repo).runs.glob("*.json"))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    assert operator_module.migrate_primary(intent_path, runner=child) == 0
    assert len(observed) == 1
    marker = Path(observed[0][0][-1])
    assert marker.is_file()
    bound_intent = Path(observed[0][0][-3])
    assert bound_intent.is_file()
    assert json.loads(bound_intent.read_text(encoding="utf-8")) == intent
    fingerprint = operator_module._migration_fingerprint(intent)
    bundle = bound_intent.parent
    assert bundle.parent == runtime_state_root(repo) / "m"
    assert bundle.name.startswith(f"{fingerprint[:12]}-")
    assert len(bundle.name) <= 24

    # Model a long Windows workspace independently of this machine's tmp_path.
    long_root = PureWindowsPath("C:/") / ("w" * 150)
    old_source = (long_root / ".git" / "aios" / "migration-handoffs"
                  / f"{fingerprint}-abcdefgh" / "source" / "src"
                  / "aios_renew" / "operator.py")
    bounded_source = (long_root / ".git" / "aios" / "m" / bundle.name
                      / "source" / "src" / "aios_renew" / "operator.py")
    bounded_marker = (long_root / ".git" / "aios" / "migration-handoffs"
                      / f"{fingerprint}.json")
    assert len(str(old_source)) > 260
    assert len(str(bounded_marker)) < 260
    assert len(str(bounded_source)) < 240
    wrong_fingerprint = ("f" if fingerprint[0] != "f" else "e") + fingerprint[1:]
    with pytest.raises(OperatorError, match="bundle name mismatch"):
        operator_module._migration_bundle_path(marker, wrong_fingerprint, bundle.name)
    assert operator_module.migrate_primary(intent_path, runner=child) == 0
    assert len(observed) == 2
    assert observed[1][0] == observed[0][0]
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    with pytest.raises(OperatorError, match="superseded"):
        operator_module._require_no_active_migration(repo)
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["target_generation_sha"])
    with pytest.raises(OperatorError, match="not completed"):
        operator_module._require_no_active_migration(repo)
    with pytest.raises(OperatorError, match="no consumed exact handoff"):
        operator_module._require_migration_target(
            repo, operator_module._migration_fingerprint(intent)
        )

    bound_target = Path(observed[0][1]["PYTHONPATH"].split(operator_module.os.pathsep)[0])
    monkeypatch.setattr(operator_module, "__file__", str(bound_target / "aios_renew" / "operator.py"))
    admitted = []

    def admitted_run(*args, **kwargs):
        admitted.append((args, kwargs))
        record = operator_module._require_migration_target(
            repo, operator_module._migration_fingerprint(intent)
        )
        with pytest.raises(OperatorError, match="differs from exact admitted TASK"):
            operator_module._validate_migration_execution(
                record, task_id="TASK-999", executor="codex", synchronize=False,
                preflight_sha=intent["target_control_sha"], dispatch_id=None,
                task_revision=2, task_blob_sha=intent["task_blob_sha"],
                task_commit_sha=intent["task_commit_sha"],
            )
        run_id = "RUN-101-001"
        state = runtime_paths(repo)
        (state.runs / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "task": {"id": intent["task_id"], "revision": 2},
            "executor": "codex", "base_sha": intent["target_control_sha"],
            "workspace": str(repo), "status": "ACTIVE",
        }), encoding="utf-8")
        (state.results / f"{run_id}.json").write_text(json.dumps({
            "result": {"head_sha": intent["target_control_sha"], "claims": [],
                       "changed_files": [], "unresolved": []}, "evidence": [],
        }), encoding="utf-8")
        return SimpleNamespace(render=lambda: "ADMITTED")

    monkeypatch.setattr(operator_module, "run_task", admitted_run)
    assert operator_module.migrate_primary(intent_path, handoff_path=marker) == 0
    assert marker.with_suffix(".completed").read_text(encoding="utf-8") == operator_module._migration_fingerprint(intent)
    with pytest.raises(OperatorError, match="already completed"):
        operator_module._require_migration_target(
            repo, operator_module._migration_fingerprint(intent)
        )
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["target_generation_sha"])
    operator_module._require_no_active_migration(repo)
    assert git(repo, "rev-parse", "HEAD") == intent["target_control_sha"]
    assert admitted[0][1]["task_blob_sha"] == intent["task_blob_sha"]
    assert admitted[0][1]["_migration_handoff"] == operator_module._migration_fingerprint(intent)
    assert operator_module.migrate_primary(intent_path, handoff_path=marker) == 0
    assert len(admitted) == 1


@pytest.mark.parametrize("control_advanced", [False, True])
def test_migration_resumes_consumed_handoff_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, control_advanced: bool
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["source_generation_sha"])
    intent_path = tmp_path / "intent.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    commands = []

    def child(command, **kwargs):
        commands.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    assert operator_module.migrate_primary(intent_path, runner=child) == 0
    marker = Path(commands[0][0][-1])
    marker.with_suffix(".consumed").write_text(marker.stem, encoding="utf-8")
    if control_advanced:
        git(repo, "fetch", "origin", "main")
        git(repo, "merge", "--ff-only", intent["target_control_sha"])
    bound_target = Path(commands[0][1]["PYTHONPATH"].split(operator_module.os.pathsep)[0])
    monkeypatch.setattr(operator_module, "__file__", str(bound_target / "aios_renew" / "operator.py"))
    calls = []

    def admitted_run(*args, **kwargs):
        calls.append((args, kwargs))
        run_id = "RUN-101-001"
        state = runtime_paths(repo)
        (state.runs / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "task": {"id": intent["task_id"], "revision": 2},
            "executor": "codex", "base_sha": intent["target_control_sha"],
            "workspace": str(repo), "status": "ACTIVE",
        }), encoding="utf-8")
        (state.results / f"{run_id}.json").write_text(json.dumps({
            "result": {"head_sha": intent["target_control_sha"], "claims": [],
                       "changed_files": [], "unresolved": []}, "evidence": [],
        }), encoding="utf-8")
        return SimpleNamespace(render=lambda: "ADMITTED")

    monkeypatch.setattr(operator_module, "run_task", admitted_run)
    assert operator_module.migrate_primary(intent_path, handoff_path=marker) == 0
    assert len(calls) == 1
    assert git(repo, "rev-parse", "HEAD") == intent["target_control_sha"]
    assert marker.with_suffix(".completed").read_text(encoding="utf-8") == marker.stem


@pytest.mark.parametrize("terminal, expected", [(None, None), ("RESULT", 0), ("FAILURE", 1)])
def test_migration_reconciles_reserved_run_without_executor_reentry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    terminal: str | None, expected: int | None,
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["source_generation_sha"])
    intent_path = tmp_path / "intent.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    commands = []

    def child(command, **kwargs):
        commands.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    assert operator_module.migrate_primary(intent_path, runner=child) == 0
    marker = Path(commands[0][0][-1])
    marker.with_suffix(".consumed").write_text(marker.stem, encoding="utf-8")
    git(repo, "fetch", "origin", "main")
    git(repo, "merge", "--ff-only", intent["target_control_sha"])
    state = runtime_paths(repo)
    run_id = "RUN-101-001"
    (state.runs / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id, "task": {"id": intent["task_id"], "revision": 2},
        "executor": "codex", "base_sha": intent["target_control_sha"],
        "workspace": str(repo), "status": "ACTIVE",
    }), encoding="utf-8")
    if terminal is not None:
        path = state.results if terminal == "RESULT" else state.failures
        artifact = (
            {"result": {"head_sha": intent["target_control_sha"], "claims": [],
                        "changed_files": [], "unresolved": []}, "evidence": []}
            if terminal == "RESULT" else
            {"kind": "FAILURE", "run_id": run_id,
             "task": {"id": intent["task_id"], "revision": 2},
             "executor": "codex", "base_sha": intent["target_control_sha"]}
        )
        (path / f"{run_id}.json").write_text(json.dumps(artifact), encoding="utf-8")
    bound_target = Path(commands[0][1]["PYTHONPATH"].split(operator_module.os.pathsep)[0])
    monkeypatch.setattr(operator_module, "__file__", str(bound_target / "aios_renew" / "operator.py"))
    invocations = []
    monkeypatch.setattr(operator_module, "run_task", lambda *a, **k: invocations.append(a))
    if terminal is None:
        with pytest.raises(OperatorError, match=f"migration RUN {run_id} is active"):
            operator_module.migrate_primary(intent_path, handoff_path=marker)
        assert not marker.with_suffix(".completed").exists()
    else:
        assert operator_module.migrate_primary(intent_path, handoff_path=marker) == expected
        assert marker.with_suffix(".completed").read_text(encoding="utf-8") == marker.stem
    assert invocations == []


def test_migration_stale_target_fails_before_handoff_or_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["source_generation_sha"])
    intent["target_control_sha"] = "0" * 40
    intent_path = tmp_path / "stale-intent.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    calls = []
    with pytest.raises(OperatorError, match="target control SHA is stale"):
        operator_module.migrate_primary(intent_path, runner=lambda *a, **k: calls.append(a))
    assert calls == []
    assert git(repo, "rev-parse", "HEAD") == intent["source_control_sha"]
    assert git(repo, "rev-parse", "origin/main") == intent["source_control_sha"]
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))
    diagnostics = list(runtime_paths(repo).admission_failures.glob("*.json"))
    assert diagnostics
    record = json.loads(diagnostics[-1].read_text(encoding="utf-8"))
    assert record["kind"] == "ADMISSION_FAILURE"
    assert record["phase"] == "MIGRATION_PRE_HANDOFF"
    assert record["executor_invoked"] is False


def test_migration_rejects_wrong_target_source_before_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: intent["source_generation_sha"])
    wrong = tmp_path / "wrong-generation"
    wrong.mkdir()
    git(wrong, "init", "-b", "main")
    git(wrong, "config", "user.name", "Migration Test")
    git(wrong, "config", "user.email", "migration@example.invalid")
    (wrong / "README.md").write_text("different source\n", encoding="utf-8")
    git(wrong, "add", ".")
    git(wrong, "commit", "-m", "wrong generation")
    intent["target_url"] = str(wrong)
    intent_path = tmp_path / "wrong-target.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    calls = []
    with pytest.raises(OperatorError, match="Git command failed"):
        operator_module.migrate_primary(intent_path, runner=lambda *a, **k: calls.append(a))
    assert calls == []
    assert git(repo, "rev-parse", "HEAD") == intent["source_control_sha"]
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))
    assert not list((runtime_state_root(repo) / "m").glob("*"))


def test_migration_rejects_mismatched_installed_source_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, intent = _migration_fixture(tmp_path)
    monkeypatch.setattr(operator_module, "_installed_generation_sha", lambda: "f" * 40)
    intent_path = tmp_path / "wrong-source.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    with pytest.raises(OperatorError, match="source generation mismatch"):
        operator_module.migrate_primary(intent_path)
    assert git(repo, "rev-parse", "HEAD") == intent["source_control_sha"]
    assert git(repo, "rev-parse", "origin/main") == intent["source_control_sha"]
    assert not list(runtime_paths(repo).runs.glob("*.json"))
    assert not list((runtime_state_root(repo) / "migration-handoffs").glob("*.json"))


def _continuation_presentation_result(**changes):
    values = {
        "task_id": "TASK-106",
        "task_revision": 1,
        "next_action": "EXECUTE_PRIMARY",
        "disposition": "DELEGATED",
        "authority": "RUNTIME",
        "delegated_operation": "PRIMARY",
        "executor_required": True,
        "executor_supplied": True,
        "executor": "codex",
        "resulting_run_id": "RUN-106-001",
        "resulting_head_sha": "a" * 40,
    }
    values.update(changes)
    return operator_module.HumanSurfaceResult(**values)


def test_continue_default_presents_delegated_primary_as_stable_human_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _continuation_presentation_result()
    calls = []

    def continue_once(*args, **kwargs):
        calls.append((args, kwargs))
        return result, 0

    monkeypatch.setattr(operator_module, "continue_task", continue_once)

    exit_code = operator_module.main(
        ["continue", "TASK-106", "--executor", "codex"]
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert capsys.readouterr().out == (
        "AIOS CONTINUE\n"
        "task: TASK-106\n"
        "revision: 1\n"
        "observed_next_action: EXECUTE_PRIMARY\n"
        "disposition: DELEGATED\n"
        "authority: RUNTIME\n"
        "delegated_operation: PRIMARY\n"
        "executor_required: true\n"
        "executor: codex\n"
        "resulting_run_id: RUN-106-001\n"
        f"resulting_head_sha: {'a' * 40}\n"
    )


@pytest.mark.parametrize(
    ("result", "exit_code", "required_lines", "absent_text"),
    [
        (
            _continuation_presentation_result(
                disposition="EXECUTOR_REQUIRED",
                authority="HUMAN",
                delegated_operation=None,
                executor_supplied=False,
                executor=None,
                resulting_run_id=None,
                resulting_head_sha=None,
            ),
            0,
            ("disposition: EXECUTOR_REQUIRED", "authority: HUMAN", "executor_required: true"),
            "executor: ",
        ),
        (
            _continuation_presentation_result(
                next_action="SEMANTIC_REVIEW",
                disposition="EXTERNAL_AUTHORITY_REQUIRED",
                authority="REVIEWER",
                delegated_operation=None,
                run_id="RUN-106-001",
                candidate_sha="b" * 40,
                outstanding_findings=(
                    {"id": "R1", "action": "CODE_FIX"},
                    {"id": "R2", "action": "CODE_FIX"},
                ),
                executor_required=False,
                executor_supplied=False,
                executor=None,
                resulting_run_id=None,
                resulting_head_sha=None,
            ),
            0,
            (
                "disposition: EXTERNAL_AUTHORITY_REQUIRED",
                "authority: REVIEWER",
                "selector_run_id: RUN-106-001",
                f"selector_candidate_sha: {'b' * 40}",
                "outstanding_findings: 2",
            ),
            "CODE_FIX",
        ),
        (
            _continuation_presentation_result(
                next_action="WAIT",
                disposition="NO_ACTION",
                authority="NONE",
                delegated_operation=None,
                executor_required=False,
                executor_supplied=False,
                executor=None,
                resulting_run_id=None,
                resulting_head_sha=None,
            ),
            0,
            ("disposition: NO_ACTION", "authority: NONE"),
            "resulting_run_id:",
        ),
        (
            _continuation_presentation_result(
                next_action="NONE",
                disposition="BLOCKED",
                authority="NONE",
                delegated_operation=None,
                executor_required=False,
                executor_supplied=False,
                executor=None,
                resulting_run_id=None,
                resulting_head_sha=None,
                blocker={"code": "BOUNDED_BLOCKER", "detail": "must stay hidden"},
            ),
            0,
            ("disposition: BLOCKED", "authority: NONE", "blocker_code: BOUNDED_BLOCKER"),
            "must stay hidden",
        ),
        (
            _continuation_presentation_result(
                disposition="DELEGATED",
                authority="RUNTIME",
                delegated_operation="PRIMARY",
                resulting_run_id=None,
                resulting_head_sha=None,
                blocker={
                    "code": "DELEGATED_OPERATION_FAILED",
                    "detail": "canonical exception text must stay hidden",
                },
            ),
            1,
            (
                "disposition: DELEGATED",
                "authority: RUNTIME",
                "delegated_operation: PRIMARY",
                "blocker_code: DELEGATED_OPERATION_FAILED",
            ),
            "canonical exception text",
        ),
    ],
    ids=(
        "executor-required",
        "external-authority-required",
        "no-action",
        "blocked",
        "delegated-failure",
    ),
)
def test_continue_human_outcomes_report_only_bounded_result_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result,
    exit_code: int,
    required_lines: tuple[str, ...],
    absent_text: str,
) -> None:
    calls = []

    def continue_once(*args, **kwargs):
        calls.append((args, kwargs))
        return result, exit_code

    monkeypatch.setattr(operator_module, "continue_task", continue_once)

    observed_exit = operator_module.main(["continue", "TASK-106"])
    output = capsys.readouterr().out

    assert observed_exit == exit_code
    assert len(calls) == 1
    assert all(line in output.splitlines() for line in required_lines)
    assert absent_text not in output
    assert "AIOS_HUMAN_SURFACE" not in output
    assert not output.lstrip().startswith("{")


def test_continue_json_is_same_result_and_presentation_does_not_change_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _continuation_presentation_result(
        run_id="RUN-105-001",
        source_run_id="RUN-105-001",
        correction_sha="c" * 40,
    )
    calls = []

    def continue_once(*args, **kwargs):
        calls.append((args, kwargs))
        return result, 0

    monkeypatch.setattr(operator_module, "continue_task", continue_once)

    assert operator_module.main(
        ["continue", "TASK-106", "--executor", "codex"]
    ) == 0
    human_output = capsys.readouterr().out
    assert operator_module.main(
        ["continue", "TASK-106", "--executor", "codex", "--json"]
    ) == 0
    machine_output = capsys.readouterr().out

    assert len(calls) == 2
    assert [call[1]["executor"] for call in calls] == ["codex", "codex"]
    assert json.loads(machine_output) == result.as_dict()
    assert "AIOS CONTINUE\n" in human_output
    assert "AIOS_HUMAN_SURFACE" not in human_output


def test_continue_json_selection_survives_existing_restart_argv_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []

    def restart_result(*args, **kwargs):
        calls.append((args, kwargs))
        return None, 17

    monkeypatch.setattr(operator_module, "continue_task", restart_result)
    argv = ["continue", "TASK-106", "--executor", "codex", "--json"]

    assert operator_module.main(argv) == 17
    assert len(calls) == 1
    assert calls[0][1]["argv"] == argv
    assert capsys.readouterr().out == ""


def test_operator_cli_ingress_and_ingest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from dataclasses import asdict
    from aios_renew.authoring_ingress import parse_envelope
    from tests.test_authoring_ingress import audited_envelope, setup_test_repo

    repo, _, _ = setup_test_repo(tmp_path, task_id="TASK-200")
    main_sha = git(repo, "rev-parse", "HEAD")

    task_payload = """\
task_id: TASK-200
revision: 1
return_affinity: {kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}
goal: CLI ingress test.
problem: Test operator CLI.
assumptions:
  - Canonical main is established.
scope:
  inspect: []
  modify: [README.md]
non_goals: []
constraints:
  hard:
    - Hard constraint.
acceptance:
  - id: AC1
    condition: Works.
verification:
  policy: minimum-sufficient-v1
  required:
    - git diff --check
"""
    envelope = {
        "format": "AIOS_INGRESS_ENVELOPE",
        "version": 1,
        "operation": "AUTHOR_TASK",
        "identity": {"task_id": "TASK-200"},
        "expected_state": {"expected_main_sha": main_sha},
        "payload": task_payload,
    }
    envelope = asdict(audited_envelope(parse_envelope(envelope), repo))
    # Optional proof carriers must be absent, rather than serialized as null.
    assert envelope.pop("origin_authoring_proof") is None

    envelope_file = tmp_path / "envelope.json"
    envelope_file.write_text(json.dumps(envelope), encoding="utf-8")

    # Test positional file argument with 'ingress'
    code = operator_module.main(["ingress", str(envelope_file), "--repo", str(repo)])
    assert code == 0
    captured = capsys.readouterr()
    assert "AIOS INGRESS PASS" in captured.out
    assert "operation: AUTHOR_TASK" in captured.out
    assert "status: CANONICALIZED" in captured.out

    # Test alias 'ingest' with idempotent replay
    code = operator_module.main(["ingest", "--file", str(envelope_file), "--repo", str(repo)])
    assert code == 0
    captured = capsys.readouterr()
    assert "AIOS INGRESS PASS" in captured.out
    assert "status: IDEMPOTENT" in captured.out

    # Test ambiguous arguments
    code = operator_module.main(["ingress", "--file", str(envelope_file), "--stdin", "--repo", str(repo)])
    assert code == 1
    captured = capsys.readouterr()
    assert "AIOS ERROR:" in captured.err

    # Test failure on invalid envelope
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text(json.dumps({"format": "INVALID"}), encoding="utf-8")
    code = operator_module.main(["ingress", str(invalid_file), "--repo", str(repo)])
    assert code == 1
    captured = capsys.readouterr()
    assert "AIOS ERROR:" in captured.err


def test_performance_cli_emits_v1_without_mutating_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(tmp_path)
    before = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "status", "--porcelain"),
        git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
    )
    assert operator_module.main(
        ["performance", "TASK-101", "--repo", str(repo)]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "AIOS_PERFORMANCE_OBSERVATION"
    assert payload["version"] == 1
    assert payload["task_selectors"] == ["TASK-101"]
    assert payload["coverage"]["terminal_runs"] == 0
    after = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "status", "--porcelain"),
        git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
    )
    assert after == before


def test_performance_cli_rejects_duplicate_and_revision_qualified_selectors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(tmp_path)
    assert operator_module.main(
        ["performance", "TASK-101", "TASK-101", "--repo", str(repo)]
    ) == 1
    assert "duplicate identities" in capsys.readouterr().err
    assert operator_module.main(
        ["performance", "TASK-101:4", "--repo", str(repo)]
    ) == 1
    assert "AIOS ERROR:" in capsys.readouterr().err


def test_repair_wakeup_delegates_once_then_replays_without_new_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aios_renew.repair_dispatch import bind_repair_run

    root = tmp_path / "repo"
    root.mkdir()
    _write_external_governed_policy(root)
    state = root / ".git" / "aios"
    repair_document = {
        "failed_run_id": "RUN-111-001",
        "action": "CODE_FIX",
    }
    observation = SimpleNamespace(
        next_action="EXECUTE_REPAIR",
        failed_run_id="RUN-111-001",
        correction_sha="a" * 40,
        correction_document=repair_document,
        correction={"executor_required": True},
        task_id="TASK-111",
    )
    monkeypatch.setattr(operator_module, "resolve_repository", lambda repo: root)
    monkeypatch.setattr(operator_module, "runtime_state_root", lambda repo: state)
    monkeypatch.setattr(
        operator_module,
        "preflight_repair",
        lambda failed_run_id, repo, **_kwargs: CorrectionPreflightResult(
            family="REPAIR",
            status="READY",
            phase="READY",
            reason_code="READY",
            task_id="TASK-111",
            task_revision=2,
            failed_run_id=failed_run_id,
            action="CODE_FIX",
            executor_required=True,
        ),
    )
    monkeypatch.setattr(operator_module, "observe_unified_state", lambda task, repo: observation)
    calls: list[str] = []

    def repair_once(failed_run_id: str, **kwargs):
        calls.append(failed_run_id)
        (state / "runs").mkdir(parents=True, exist_ok=True)
        (state / "repairs").mkdir(parents=True, exist_ok=True)
        run = {
            "run_id": "RUN-111-002",
            "task": {"id": "TASK-111", "revision": 2},
            "executor": "codex",
        }
        (state / "runs/RUN-111-002.json").write_text(
            json.dumps(run), encoding="utf-8"
        )
        (state / "repairs/RUN-111-002.json").write_text(
            json.dumps(
                {
                    "failed_run_id": failed_run_id,
                    "repair_authorization_sha": kwargs["required_repair_sha"],
                    "repair": repair_document,
                    "run": run,
                }
            ),
            encoding="utf-8",
        )
        profile = ResolvedExecutionProfile(
            run_id="RUN-111-002",
            executor="codex",
            model=kwargs["model"],
            reasoning_effort=kwargs["reasoning_effort"],
            model_source=kwargs["model_source"],
            effort_source=kwargs["effort_source"],
        )
        persist_execution_profile(
            state / "execution-profiles/RUN-111-002.json",
            profile,
        )
        bind_repair_run(
            state_root=state,
            repo_root=root,
            repair_dispatch_id=kwargs["repair_dispatch_id"],
            run_id="RUN-111-002",
            execution_profile=profile,
        )
        (state / "results").mkdir(parents=True, exist_ok=True)
        (state / "results/RUN-111-002.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(run_id="RUN-111-002")

    monkeypatch.setattr(operator_module, "run_repair", repair_once)
    first = run_repair_wakeup(
        "repair-111",
        "RUN-111-001",
        "a" * 40,
        executor="codex",
        reasoning_effort="repo_only",
        repo=root,
    )
    monkeypatch.setattr(
        operator_module,
        "preflight_repair",
        lambda *args, **kwargs: pytest.fail("terminal replay repeated preflight"),
    )
    replay = run_repair_wakeup(
        "repair-111",
        "RUN-111-001",
        "a" * 40,
        executor="codex",
        repo=root,
    )
    assert calls == ["RUN-111-001"]
    assert first.status == replay.status == "SUCCEEDED"
    assert replay.replayed is True
    record = next((state / "repair-dispatches").glob("*.json"))
    assert json.loads(record.read_text(encoding="utf-8"))["reasoning_effort"] == "repo_only"
    policy = root / ".ai" / "executor-profiles.yaml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("[low, repo_only]", "[low]"),
        encoding="utf-8",
    )
    with pytest.raises(OperatorError, match="profile binding"):
        run_repair_wakeup(
            "repair-111", "RUN-111-001", "a" * 40, executor="codex", repo=root
        )
    assert calls == ["RUN-111-001"]
    assert operator_module.main([
        "repair-wakeup", "repair-111", "RUN-111-001", "a" * 40,
        "--executor", "codex", "--repo", str(root),
    ]) == 1
    assert "profile binding" in capsys.readouterr().err


def test_repair_wakeup_rejects_superseded_sha_before_state_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    state = root / ".git" / "aios"
    seen: list[str | None] = []
    monkeypatch.setattr(operator_module, "resolve_repository", lambda repo: root)
    monkeypatch.setattr(operator_module, "runtime_state_root", lambda repo: state)

    def stale_preflight(failed_run_id, repo, required_repair_sha=None):
        seen.append(required_repair_sha)
        return CorrectionPreflightResult(
            family="REPAIR",
            status="BLOCKED",
            phase="CANONICAL_CONTRACT_ADMISSION",
            reason_code="CANONICAL_LINEAGE_INVALID",
            failed_run_id=failed_run_id,
        )

    monkeypatch.setattr(operator_module, "preflight_repair", stale_preflight)
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *args, **kwargs: pytest.fail("stale selector reached Unified State"),
    )
    with pytest.raises(OperatorError, match="preflight does not authorize"):
        run_repair_wakeup(
            "repair-stale-111",
            "RUN-111-001",
            "a" * 40,
            executor="codex",
            repo=root,
        )
    assert seen == ["a" * 40]
    assert not state.exists()


def test_repair_wakeup_rejects_executor_authority_inconsistent_with_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(operator_module, "resolve_repository", lambda repo: root)
    monkeypatch.setattr(
        operator_module, "runtime_state_root", lambda repo: root / ".git" / "aios"
    )
    monkeypatch.setattr(
        operator_module,
        "preflight_repair",
        lambda failed_run_id, repo, **_kwargs: CorrectionPreflightResult(
            family="REPAIR",
            status="READY",
            phase="READY",
            reason_code="READY",
            task_id="TASK-111",
            task_revision=2,
        ),
    )
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda task, repo: SimpleNamespace(
            next_action="EXECUTE_REPAIR",
            failed_run_id="RUN-111-001",
            correction_sha="a" * 40,
            correction_document={
                "failed_run_id": "RUN-111-001",
                "action": "NO_CHANGE",
            },
            correction={"executor_required": False},
            task_id="TASK-111",
        ),
    )
    with pytest.raises(OperatorError, match="forbids"):
        run_repair_wakeup(
            "repair-no-change-111",
            "RUN-111-001",
            "a" * 40,
            executor="codex",
            repo=root,
        )


def test_operator_persists_execution_profile_before_native_runner(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    sidecar = paths.execution_profiles / "RUN-101-001.json"

    observed_during_runner = []

    class AssertingRunner(FakeCodexRunner):
        def __call__(self, command, **kwargs):
            observed_during_runner.append(sidecar.is_file())
            return super().__call__(command, **kwargs)

    runner = AssertingRunner(repo)
    run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert observed_during_runner == [True]
    assert sidecar.is_file()
    profile_data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert profile_data["format"] == "AIOS_EXECUTION_PROFILE"
    assert profile_data["version"] == 1
    assert profile_data["run_id"] == "RUN-101-001"
    assert profile_data["executor"] == "codex"
    assert profile_data["model"] == "gpt-6-sol"
    assert profile_data["reasoning_effort"] == "high"
    assert profile_data["model_source"] == "REPOSITORY_DEFAULT"
    assert profile_data["effort_source"] == "REPOSITORY_DEFAULT"
    assert runner.calls[0][0][runner.calls[0][0].index("-c") + 1] == 'model_reasoning_effort="high"'


def test_operator_partial_explicit_profile_reaches_native_command_exactly(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)
    run_task(
        "TASK-101",
        executor="codex",
        model="provider/future-v9",
        repo=repo,
        native_runner=runner,
    )

    profile_data = json.loads(
        (runtime_paths(repo).execution_profiles / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    policy = load_execution_profile_policy(repo)
    assert profile_data["model"] == "provider/future-v9"
    assert profile_data["model_source"] == "EXPLICIT"
    assert policy.default_reasoning_effort("codex") == "high"
    assert profile_data["reasoning_effort"] == "high"
    assert profile_data["effort_source"] == "REPOSITORY_DEFAULT"
    command = runner.calls[0][0]
    assert command[command.index("-m") + 1] == "provider/future-v9"
    assert command[command.index("-c") + 1] == 'model_reasoning_effort="high"'


def test_operator_explicit_codex_effort_stays_exact_and_attributed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeCodexRunner(repo)
    run_task(
        "TASK-101",
        executor="codex",
        reasoning_effort="xhigh",
        repo=repo,
        native_runner=runner,
    )

    profile_data = json.loads(
        (runtime_paths(repo).execution_profiles / "RUN-101-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert profile_data["model"] == "gpt-6-sol"
    assert profile_data["model_source"] == "REPOSITORY_DEFAULT"
    assert profile_data["reasoning_effort"] == "xhigh"
    assert profile_data["effort_source"] == "EXPLICIT"
    command = runner.calls[0][0]
    assert command[command.index("-c") + 1] == 'model_reasoning_effort="xhigh"'


def test_operator_conflicting_preexisting_sidecar_invokes_zero_native_runners(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    sidecar = paths.execution_profiles / "RUN-101-001.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # Pre-existing sidecar with different executor
    sidecar.write_text(
        json.dumps({
            "format": "AIOS_EXECUTION_PROFILE",
            "version": 1,
            "run_id": "RUN-101-001",
            "executor": "antigravity",
            "model": "gemini-3.8-flash",
            "reasoning_effort": "high",
            "model_source": "REPOSITORY_DEFAULT",
            "effort_source": "REPOSITORY_DEFAULT",
        }),
        encoding="utf-8",
    )

    runner = FakeCodexRunner(repo)
    with pytest.raises(OperatorError, match="persisted execution profile mismatch"):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert runner.count == 0


def test_operator_antigravity_minimax_creates_no_execution_profile(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    sidecar = paths.execution_profiles / "RUN-101-001.json"

    runner = FakeAntigravityMinimaxRunner(repo)
    summary = run_task("TASK-101", executor="antigravity-minimax", repo=repo, native_runner=runner)

    assert runner.count == 1
    assert summary.executor == "antigravity-minimax"
    assert not sidecar.is_file()


def test_operator_antigravity_minimax_profile_options_invoke_zero_native(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    runner = FakeAntigravityMinimaxRunner(repo)

    with pytest.raises(OperatorError, match="profile-managed Executor"):
        run_task(
            "TASK-101",
            executor="antigravity-minimax",
            model="provider/future-v9",
            repo=repo,
            native_runner=runner,
        )

    assert runner.count == 0


def test_operator_preexisting_sidecar_with_unsupported_effort_invokes_zero_native_runners(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    sidecar = paths.execution_profiles / "RUN-101-001.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # Pre-existing sidecar for same RUN and same Executor, but unsupported effort
    sidecar.write_text(
        json.dumps({
            "format": "AIOS_EXECUTION_PROFILE",
            "version": 1,
            "run_id": "RUN-101-001",
            "executor": "codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "ultra",
            "model_source": "REPOSITORY_DEFAULT",
            "effort_source": "EXPLICIT",
        }),
        encoding="utf-8",
    )

    runner = FakeCodexRunner(repo)
    with pytest.raises(
        OperatorError,
        match="persisted execution profile is invalid: unsupported reasoning effort",
    ):
        run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert runner.count == 0


def test_operator_reusing_preexisting_sidecar_preserves_bound_profile_values(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    paths = runtime_paths(repo)
    sidecar = paths.execution_profiles / "RUN-101-001.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # Pre-existing sidecar retains the medium default resolved before this policy change.
    sidecar.write_text(
        json.dumps({
            "format": "AIOS_EXECUTION_PROFILE",
            "version": 1,
            "run_id": "RUN-101-001",
            "executor": "codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "model_source": "REPOSITORY_DEFAULT",
            "effort_source": "REPOSITORY_DEFAULT",
        }),
        encoding="utf-8",
    )

    runner = FakeCodexRunner(repo)
    summary = run_task("TASK-101", executor="codex", repo=repo, native_runner=runner)

    assert runner.count == 1
    assert summary.executor == "codex"
    command = runner.calls[0][0]
    assert 'model_reasoning_effort="medium"' in command
    persisted = json.loads(sidecar.read_text(encoding="utf-8"))
    assert persisted["reasoning_effort"] == "medium"
    assert persisted["effort_source"] == "REPOSITORY_DEFAULT"



# TASK-242: explicit Human reconciliation is separate from automatic synchronization.
def _transported_diverged_failure(
    root: Path, *, candidate_changes: dict[str, str] | None = None,
) -> tuple[Path, Path, str, str, dict]:
    repo = make_repo(root)
    remote = root / "upstream.git"
    base = git(repo, "rev-parse", "HEAD")
    target = publish_upstream(repo, {"NEW_MAIN.txt": "new canonical main\n"})
    for path, content in (candidate_changes or {}).items():
        destination = repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        commit_setup_state(repo, path, message="local candidate ancestry")
    (repo / "FAILED_CANDIDATE.txt").write_text("failed candidate\n", encoding="utf-8")
    failed = commit_setup_state(repo, "FAILED_CANDIDATE.txt", message="failed candidate")
    run = {
        "run_id": "RUN-231-004", "task": {"id": "TASK-231", "revision": 2},
        "executor": "codex", "base_sha": base, "workspace": str(repo),
        "head_sha": None, "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE", "run_id": "RUN-231-004", "task": run["task"],
        "executor": "codex", "base_sha": base, "failed_head_sha": failed,
        "candidate": {
            "transportable": True, "repairable": True, "dirty": False,
            "descends_from_base": True,
            "changed_files": sorted(["FAILED_CANDIDATE.txt", *(candidate_changes or {})]),
            "outside_task_scope": [],
        },
    }
    state = runtime_paths(repo)
    run_path = state.runs / "RUN-231-004.json"
    failure_path = state.failures / "RUN-231-004.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    _publish_reconciliation_artifact(repo)
    git(repo, "push", "--quiet", "origin", f"{failed}:refs/heads/aios/failure/RUN-231-004")
    (state.root / "runtime-evidence.bin").write_bytes(b"immutable Runtime evidence")
    with RepositoryLock(state.lock):
        pass
    return repo, remote, failed, target, failure


def _publish_reconciliation_artifact(repo: Path) -> str:
    state = runtime_paths(repo)
    commit = transport_module._create_named_artifacts_commit(
        repo, run_path=state.runs / "RUN-231-004.json",
        artifact_path=state.failures / "RUN-231-004.json",
        artifact_name="failure.json", run_id="RUN-231-004",
    )
    git(repo, "push", "--quiet", "--force", "origin",
        f"{commit}:refs/heads/aios/failure-artifacts/RUN-231-004")
    return commit


def _reconciliation_snapshot(repo: Path) -> tuple:
    state = runtime_paths(repo)
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
    return (
        git(repo, "symbolic-ref", "--quiet", "HEAD") if git(repo, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD" else "DETACHED",
        git(repo, "rev-parse", "HEAD"),
        git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
        git(repo, "status", "--porcelain", "--untracked-files=all"),
        (git_dir / "index").read_bytes(),
        (git_dir / "FETCH_HEAD").read_bytes() if (git_dir / "FETCH_HEAD").exists() else None,
        {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*")
         if p.is_file() and ".git" not in p.relative_to(repo).parts},
        # Windows denies byte reads of the transient lock while it is held.
        {str(p.relative_to(state.root)): p.read_bytes() for p in state.root.rglob("*")
         if p.is_file() and p != state.lock},
    )


def test_human_reconciliation_restores_diverged_main_and_preserves_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, remote, failed, target, _ = _transported_diverged_failure(tmp_path)
    remote_before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    evidence_before = _reconciliation_snapshot(repo)[-1]

    def forbidden(*args, **kwargs):
        raise AssertionError("reconciliation must not invoke lifecycle authority")

    for name in ("run_task", "run_repair", "run_remediation", "retry_transport"):
        monkeypatch.setattr(operator_module, name, forbidden)
    calls = []
    real_git = operator_module._git

    def record_git(root, *args, **kwargs):
        calls.append((Path(root), args))
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", record_git)
    summary = operator_module.reconcile_control_main(
        "RUN-231-004", repo=repo, expected_failed_head=failed,
        expected_canonical_main=target,
    )
    assert summary == {
        "failed_run_id": "RUN-231-004", "prior_head": failed,
        "restored_head": target, "status": "SUCCESS",
    }
    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert git(repo, "rev-parse", "HEAD") == target
    assert git(repo, "status", "--porcelain") == ""
    assert _reconciliation_snapshot(repo)[-1] == evidence_before
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == remote_before
    assert not (repo / "FAILED_CANDIDATE.txt").exists()
    assert (repo / "NEW_MAIN.txt").read_text() == "new canonical main\n"
    control_mutations = [args for root, args in calls if root == repo
                         and args[0] in {"reset", "merge", "rebase", "cherry-pick", "stash", "clean", "commit", "push"}]
    assert control_mutations == [("reset", "--hard", target)]


@pytest.mark.parametrize("gate", [
    "dirty", "staged", "untracked", "ignored-collision", "detached", "non-main", "head-drift",
    "upstream-drift", "missing-upstream", "ambiguous-upstream", "wrong-upstream",
    "fetch-failure", "missing-candidate", "missing-artifact", "candidate-mismatch",
    "competing-result", "competing-review", "missing-content", "malformed-json",
    "wrong-run", "wrong-head", "dirty-record", "untransportable-record",
    "malformed-flags", "missing-base", "unrelated-base", "ahead-only", "equal",
    "wrong-task", "wrong-run-status", "malformed-run-json", "artifact-head-drift",
])
def test_human_reconciliation_proof_gates_preserve_repository(
    tmp_path: Path, gate: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, remote, failed, target, failure = _transported_diverged_failure(tmp_path)
    state = runtime_paths(repo)
    expected_failed, expected_target = failed, target
    if gate == "dirty":
        (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    elif gate == "staged":
        (repo / "README.md").write_text("staged\n", encoding="utf-8")
        git(repo, "add", "README.md")
    elif gate == "untracked":
        (repo / "UNTRACKED.txt").write_text("untracked\n", encoding="utf-8")
    elif gate == "ignored-collision":
        exclude = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("ab") as exclude_file:
            exclude_file.write(b"\nNEW_MAIN.txt\n")
        (repo / "NEW_MAIN.txt").write_text("ignored content must survive\n", encoding="utf-8")
    elif gate == "detached":
        git(repo, "checkout", "--quiet", "--detach")
    elif gate == "non-main":
        git(repo, "checkout", "--quiet", "-b", "feature")
    elif gate == "head-drift":
        expected_failed = failure["base_sha"]
    elif gate == "upstream-drift":
        expected_target = failure["base_sha"]
    elif gate == "missing-upstream":
        git(repo, "branch", "--unset-upstream")
    elif gate == "ambiguous-upstream":
        git(repo, "config", "--add", "branch.main.remote", "other")
    elif gate == "wrong-upstream":
        git(repo, "config", "branch.main.merge", "refs/heads/other")
    elif gate == "fetch-failure":
        git(repo, "remote", "set-url", "origin", str(tmp_path / "unavailable.git"))
    elif gate in {"missing-candidate", "missing-artifact"}:
        namespace = "failure" if gate == "missing-candidate" else "failure-artifacts"
        git(remote, "update-ref", "-d", f"refs/heads/aios/{namespace}/RUN-231-004")
    elif gate == "candidate-mismatch":
        git(remote, "update-ref", "refs/heads/aios/failure/RUN-231-004", failure["base_sha"])
    elif gate in {"competing-result", "competing-review", "missing-content"}:
        namespace = {"competing-result": "artifacts", "competing-review": "review",
                     "missing-content": "failure-artifacts"}[gate]
        git(remote, "update-ref", f"refs/heads/aios/{namespace}/RUN-231-004", failure["base_sha"])
    elif gate in {"ahead-only", "equal"}:
        expected_target = failure["base_sha"] if gate == "ahead-only" else failed
        git(remote, "update-ref", "refs/heads/main", expected_target)
    else:
        if gate in {"wrong-task", "wrong-run-status", "malformed-run-json", "artifact-head-drift"}:
            run_path = state.runs / "RUN-231-004.json"
            run = json.loads(run_path.read_text())
            if gate == "wrong-task":
                run["task"]["id"] = "TASK-242"
            elif gate == "wrong-run-status":
                run["status"] = "FAILURE"
            elif gate == "artifact-head-drift":
                run["head_sha"] = target
            run_path.write_text("{" if gate == "malformed-run-json" else json.dumps(run), encoding="utf-8")
        if gate == "wrong-run":
            failure["run_id"] = "RUN-231-005"
        elif gate == "wrong-head":
            failure["failed_head_sha"] = target
        elif gate == "dirty-record":
            failure["candidate"].update(dirty=True, repairable=False, transportable=False)
        elif gate == "untransportable-record":
            failure["candidate"].update(transportable=False, outside_task_scope=["FAILED_CANDIDATE.txt"])
        elif gate == "malformed-flags":
            failure["candidate"]["dirty"] = "false"
        elif gate in {"missing-base", "unrelated-base"}:
            failure["base_sha"] = "f" * 40 if gate == "missing-base" else target
            run_path = state.runs / "RUN-231-004.json"
            run = json.loads(run_path.read_text())
            run["base_sha"] = failure["base_sha"]
            run_path.write_text(json.dumps(run), encoding="utf-8")
        (state.failures / "RUN-231-004.json").write_text(
            "{" if gate == "malformed-json" else json.dumps(failure), encoding="utf-8",
        )
        _publish_reconciliation_artifact(repo)

    def forbidden(*args, **kwargs):
        raise AssertionError("proof rejection must not invoke lifecycle authority")

    monkeypatch.setattr(operator_module, "run_task", forbidden)
    before = _reconciliation_snapshot(repo)
    remote_before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    with pytest.raises(OperatorError):
        operator_module.reconcile_control_main(
            "RUN-231-004", repo=repo, expected_failed_head=expected_failed,
            expected_canonical_main=expected_target,
        )
    assert _reconciliation_snapshot(repo) == before
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == remote_before


def test_human_reconciliation_conflicting_lock_preserves_repository(tmp_path: Path) -> None:
    repo, _, failed, target, _ = _transported_diverged_failure(tmp_path)
    before = _reconciliation_snapshot(repo)
    with RepositoryLock(runtime_paths(repo).lock):
        with pytest.raises(OperatorError, match="active"):
            operator_module.reconcile_control_main(
                "RUN-231-004", repo=repo, expected_failed_head=failed,
                expected_canonical_main=target,
            )
    assert _reconciliation_snapshot(repo) == before


def test_continue_sync_never_reconciles_transported_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, failed, _, _ = _transported_diverged_failure(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("automatic synchronization has no Human reconciliation authority")

    monkeypatch.setattr(operator_module, "reconcile_control_main", forbidden)
    monkeypatch.setattr(operator_module, "run_task", forbidden)
    assert operator_module._synchronize_primary_branch(repo, only_if_behind=True) is False
    outcome = operator_module._preflight_continue_sync(repo, argv=["continue", "TASK-101"])
    assert outcome.restart_code is None
    assert git(repo, "rev-parse", "HEAD") == failed
    assert list(runtime_paths(repo).runs.glob("*.json")) == [
        runtime_paths(repo).runs / "RUN-231-004.json"
    ]


def test_reconciliation_cli_requires_exact_selectors_and_emits_bounded_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    repo, _, failed, target, _ = _transported_diverged_failure(tmp_path)
    with pytest.raises(SystemExit):
        operator_module._parser().parse_args(["reconcile-control-main", "RUN-231-004"])
    capsys.readouterr()
    code = operator_module.main([
        "reconcile-control-main", "RUN-231-004", "--repo", str(repo),
        "--expected-failed-head", failed, "--expected-canonical-main", target,
    ])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert set(summary) == {"failed_run_id", "prior_head", "restored_head", "status"}
    assert summary["status"] == "SUCCESS"
    code = operator_module.main([
        "reconcile-control-main", "RUN-231-004", "--repo", str(repo),
        "--expected-failed-head", failed, "--expected-canonical-main", target,
    ])
    assert code == 1
    assert json.loads(capsys.readouterr().out) == {
        "failed_run_id": "RUN-231-004", "prior_head": target,
        "restored_head": None, "status": "FAILURE",
    }


def test_reconciliation_has_only_the_explicit_cli_call_path() -> None:
    source = ast.parse(inspect.getsource(operator_module))
    owners = []

    class Calls(ast.NodeVisitor):
        def __init__(self):
            self.owner = None

        def visit_FunctionDef(self, node):
            previous = self.owner
            self.owner = node.name
            self.generic_visit(node)
            self.owner = previous

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "reconcile_control_main":
                owners.append(self.owner)
            self.generic_visit(node)

    Calls().visit(source)
    assert owners == ["main"]


@pytest.mark.parametrize("drift", ["head", "upstream", "remote-proof"])
def test_reconciliation_rechecks_proofs_before_any_reset(
    tmp_path: Path, drift: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, failed, target, failure = _transported_diverged_failure(tmp_path)
    real_query = operator_module._exact_remote_refs
    calls = []
    fired = False

    def drifting_query(observer, remote, *patterns):
        nonlocal fired
        refs = real_query(observer, remote, *patterns)
        if not fired and "refs/heads/main" in patterns and len(patterns) > 1:
            fired = True
            if drift == "head":
                git(repo, "reset", "--hard", failure["base_sha"])
            elif drift == "upstream":
                git(repo, "config", "branch.main.merge", "refs/heads/other")
            else:
                refs["refs/heads/main"] = failure["base_sha"]
        return refs

    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        calls.append((Path(root), args))
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_exact_remote_refs", drifting_query)
    monkeypatch.setattr(operator_module, "_git", recording_git)
    with pytest.raises(OperatorError):
        operator_module.reconcile_control_main(
            "RUN-231-004", repo=repo, expected_failed_head=failed,
            expected_canonical_main=target,
        )
    assert fired
    assert not any(root == repo and args[0] in {"reset", "merge", "rebase", "clean", "push"}
                   for root, args in calls)
    assert git(repo, "rev-parse", "HEAD") == (failure["base_sha"] if drift == "head" else failed)


# TASK-257: automatic authority exists only at the locked PRIMARY pre-RUN edge.
def test_primary_reconciles_exact_canonical_failure_and_all_local_only_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, remote, failed, target, _ = _transported_diverged_failure(
        tmp_path, candidate_changes={"EARLIER.txt": "earlier local commit\n"},
    )
    # Materialize the upstream object without changing refs or FETCH_HEAD before
    # the snapshot; the ancestry assertion below requires both commits locally.
    git(repo, "fetch", "--quiet", "--no-write-fetch-head", "origin", target)
    before = _reconciliation_snapshot(repo)
    remote_before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    local_only = git(repo, "rev-list", failed, "--not", target).splitlines()
    assert len(local_only) == 2
    calls = []
    real_git = operator_module._git

    def record_git(root, *args, **kwargs):
        calls.append((Path(root), args))
        return real_git(root, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("reconciliation cannot delegate or create lifecycle truth")

    monkeypatch.setattr(operator_module, "_git", record_git)
    for name in ("reconcile_control_main", "run_task", "run_repair", "run_remediation",
                 "retry_transport", "_restart_primary_invocation"):
        monkeypatch.setattr(operator_module, name, forbidden)
    outcome = operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"])
    assert outcome.preflight_sha == target
    assert outcome.restart_code is None
    after = _reconciliation_snapshot(repo)
    assert after[0:2] == ("refs/heads/main", target)
    assert after[3] == ""
    assert after[5] == before[5]  # FETCH_HEAD is not written.
    assert after[-1] == before[-1]  # No new RUN or other Runtime artifact.
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == remote_before
    assert git(remote, "rev-parse", "refs/heads/aios/failure/RUN-231-004") == failed
    for sha in local_only:
        assert operator_module._git_is_ancestor(remote, sha, failed)
    mutations = [args for root, args in calls if root == repo and args[0] in {
        "reset", "merge", "rebase", "cherry-pick", "stash", "clean", "commit", "push", "update-ref",
    }]
    assert mutations == [("reset", "--hard", target)]


@pytest.mark.parametrize("stage", ["proof", "import"])
@pytest.mark.parametrize("drift", ["unrelated-review", "competing-review"])
def test_primary_failure_reconciliation_bounds_review_ref_drift_to_local_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, drift: str,
) -> None:
    repo, remote, failed, target, failure = _transported_diverged_failure(tmp_path)
    review_ref = "refs/heads/aios/review/RUN-999-001"
    if drift == "unrelated-review":
        git(remote, "update-ref", review_ref, failure["base_sha"])
    before = _reconciliation_snapshot(repo)
    failure_refs = git(remote, "for-each-ref", "--format=%(refname) %(objectname)",
                       "refs/heads/aios/failure", "refs/heads/aios/failure-artifacts")
    real_query = operator_module._exact_remote_refs
    real_git = operator_module._git
    calls = []
    mutation_snapshot = None

    def mutate_review():
        nonlocal mutation_snapshot
        assert mutation_snapshot is None
        git(remote, "update-ref", review_ref, failed if drift == "competing-review" else target)
        mutation_snapshot = _reconciliation_snapshot(repo)

    def drift_at_proof(observer, remote_name, *patterns):
        if stage == "proof" and mutation_snapshot is None and "refs/heads/main" in patterns and len(patterns) > 1:
            mutate_review()
        return real_query(observer, remote_name, *patterns)

    def drift_at_import(root, *args, **kwargs):
        calls.append((Path(root), args))
        result = real_git(root, *args, **kwargs)
        if stage == "import" and Path(root) == repo and args[0] == "fetch":
            mutate_review()
        return result

    def forbidden(*args, **kwargs):
        raise AssertionError("preservation cannot admit or invoke lifecycle authority")

    monkeypatch.setattr(operator_module, "_exact_remote_refs", drift_at_proof)
    monkeypatch.setattr(operator_module, "_git", drift_at_import)
    for name in ("reconcile_control_main", "run_task", "run_repair", "run_remediation",
                 "retry_transport", "_restart_primary_invocation"):
        monkeypatch.setattr(operator_module, name, forbidden)
    if drift == "competing-review":
        with pytest.raises(OperatorError, match="ambiguous canonical preservation"):
            operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"])
        assert mutation_snapshot is not None
        assert _reconciliation_snapshot(repo) == mutation_snapshot
        assert not any(root == repo and args[0] == "reset" for root, args in calls)
    else:
        outcome = operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"])
        assert mutation_snapshot is not None
        assert outcome.preflight_sha == target and outcome.restart_code is None
        after = _reconciliation_snapshot(repo)
        assert after[:2] == ("refs/heads/main", target) and after[3] == ""
        assert after[5] == before[5] and after[-1] == before[-1]
        mutations = [args for root, args in calls if root == repo and args[0] in {
            "reset", "merge", "rebase", "cherry-pick", "stash", "clean", "commit", "push", "update-ref",
        }]
        assert mutations == [("reset", "--hard", target)]
    assert git(remote, "rev-parse", review_ref) == (failed if drift == "competing-review" else target)
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)",
               "refs/heads/aios/failure", "refs/heads/aios/failure-artifacts") == failure_refs
    assert not list(runtime_paths(repo).runs.glob("RUN-101-*.json"))


def test_primary_after_reconciliation_admits_once_on_exact_restored_base(tmp_path: Path) -> None:
    repo, _, _, target, _ = _transported_diverged_failure(tmp_path)
    runner = FakeCodexRunner(repo)
    preflight = operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"])
    summary = run_task(
        "TASK-101", executor="codex", repo=repo, native_runner=runner,
        synchronize=False, preflight_sha=preflight.preflight_sha,
    )
    assert summary.base_sha == target
    assert summary.task_id == "TASK-101"
    assert runner.count == 1
    assert len(list(runtime_paths(repo).runs.glob("RUN-101-*.json"))) == 1


@pytest.mark.parametrize("gate", [
    "dirty", "staged", "untracked", "detached", "non-main", "missing-upstream",
    "ambiguous-upstream", "wrong-upstream", "remote-failure", "missing-candidate",
    "missing-artifact", "candidate-mismatch", "ambiguous-preservation", "extra-local-commit",
    "competing-result", "competing-review", "missing-content", "malformed-json",
    "wrong-run", "wrong-head", "dirty-record", "untransportable-record", "malformed-flags",
    "missing-base", "unrelated-base", "ignored-collision", "submodule", "hidden-tracked",
])
def test_primary_reconciliation_rejects_unproven_states_without_reset_or_run(
    tmp_path: Path, gate: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, remote, failed, target, failure = _transported_diverged_failure(tmp_path)
    state = runtime_paths(repo)
    if gate in {"dirty", "staged"}:
        (repo / "README.md").write_text("changed\n", encoding="utf-8")
        if gate == "staged":
            git(repo, "add", "README.md")
    elif gate == "untracked":
        (repo / "untracked.txt").write_text("keep\n", encoding="utf-8")
    elif gate == "detached":
        git(repo, "checkout", "--quiet", "--detach")
    elif gate == "non-main":
        git(repo, "checkout", "--quiet", "-b", "feature")
    elif gate == "missing-upstream":
        git(repo, "branch", "--unset-upstream")
    elif gate == "ambiguous-upstream":
        git(repo, "config", "--add", "branch.main.remote", "other")
    elif gate == "wrong-upstream":
        git(repo, "config", "branch.main.merge", "refs/heads/other")
    elif gate == "remote-failure":
        git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    elif gate in {"missing-candidate", "missing-artifact"}:
        namespace = "failure" if gate == "missing-candidate" else "failure-artifacts"
        git(remote, "update-ref", "-d", f"refs/heads/aios/{namespace}/RUN-231-004")
    elif gate == "candidate-mismatch":
        git(remote, "update-ref", "refs/heads/aios/failure/RUN-231-004", target)
    elif gate == "ambiguous-preservation":
        git(remote, "update-ref", "refs/heads/aios/failure/RUN-231-005", failed)
    elif gate == "extra-local-commit":
        (repo / "unpreserved.txt").write_text("unpreserved\n", encoding="utf-8")
        commit_setup_state(repo, "unpreserved.txt", message="unpreserved local commit")
    elif gate in {"competing-result", "competing-review", "missing-content"}:
        namespace = {"competing-result": "artifacts", "competing-review": "review",
                     "missing-content": "failure-artifacts"}[gate]
        git(remote, "update-ref", f"refs/heads/aios/{namespace}/RUN-231-004", target)
    elif gate == "ignored-collision":
        exclude = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("ab") as stream:
            stream.write(b"\nNEW_MAIN.txt\n")
        (repo / "NEW_MAIN.txt").write_text("preserve ignored\n", encoding="utf-8")
    elif gate == "submodule":
        # A gitlink in the canonical target is enough to forbid this authority.
        publisher = repo.parent / "publisher"
        git(publisher, "update-index", "--add", "--cacheinfo", f"160000,{failed},module")
        tree = git(publisher, "write-tree")
        submodule_target = git(publisher, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                               "commit-tree", tree, "-p", target, "-m", "submodule target")
        git(publisher, "push", "--quiet", "origin", f"{submodule_target}:refs/heads/main")
    elif gate == "hidden-tracked":
        git(repo, "update-index", "--assume-unchanged", "README.md")
        (repo / "README.md").write_text("hidden changes\n", encoding="utf-8")
    else:
        if gate == "wrong-run":
            failure["run_id"] = "RUN-231-005"
        elif gate == "wrong-head":
            failure["failed_head_sha"] = target
        elif gate == "dirty-record":
            failure["candidate"].update(dirty=True, repairable=False, transportable=False)
        elif gate == "untransportable-record":
            failure["candidate"].update(transportable=False, outside_task_scope=["FAILED_CANDIDATE.txt"])
        elif gate == "malformed-flags":
            failure["candidate"]["dirty"] = "false"
        elif gate in {"missing-base", "unrelated-base"}:
            failure["base_sha"] = "f" * 40 if gate == "missing-base" else target
            run_path = state.runs / "RUN-231-004.json"
            run = json.loads(run_path.read_text())
            run["base_sha"] = failure["base_sha"]
            run_path.write_text(json.dumps(run), encoding="utf-8")
        (state.failures / "RUN-231-004.json").write_text(
            "{" if gate == "malformed-json" else json.dumps(failure), encoding="utf-8",
        )
        _publish_reconciliation_artifact(repo)
    calls = []
    real_git = operator_module._git

    def record_git(root, *args, **kwargs):
        calls.append((Path(root), args))
        return real_git(root, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("rejected preflight must not invoke Executor or Human reconciliation")

    monkeypatch.setattr(operator_module, "_git", record_git)
    monkeypatch.setattr(operator_module, "reconcile_control_main", forbidden)
    monkeypatch.setattr(operator_module, "_restart_primary_invocation", forbidden)
    before = _reconciliation_snapshot(repo)
    with pytest.raises(OperatorError):
        operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"], runner=forbidden)
    assert _reconciliation_snapshot(repo) == before
    assert not any(root == repo and args[0] in {"reset", "merge", "push", "update-ref"}
                   for root, args in calls)


@pytest.mark.parametrize("drift", [
    "head", "upstream", "url", "target", "candidate", "artifact", "competing-result",
    "ambiguous-preservation", "dirty", "untracked", "ignored-collision",
])
def test_primary_reconciliation_rechecks_after_target_import_before_reset(
    tmp_path: Path, drift: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, remote, failed, target, failure = _transported_diverged_failure(tmp_path)
    real_git = operator_module._git
    calls = []
    edge_snapshot = None

    def drift_at_import(root, *args, **kwargs):
        nonlocal edge_snapshot
        calls.append((Path(root), args))
        result = real_git(root, *args, **kwargs)
        if Path(root) == repo and args[0] == "fetch":
            if drift == "head":
                git(repo, "reset", "--hard", failure["base_sha"])
            elif drift == "upstream":
                git(repo, "config", "branch.main.merge", "refs/heads/other")
            elif drift == "url":
                git(repo, "remote", "set-url", "origin", str(tmp_path / "other.git"))
            elif drift in {"target", "candidate", "artifact", "competing-result", "ambiguous-preservation"}:
                ref = {"target": "refs/heads/main",
                       "candidate": "refs/heads/aios/failure/RUN-231-004",
                       "artifact": "refs/heads/aios/failure-artifacts/RUN-231-004",
                       "competing-result": "refs/heads/aios/artifacts/RUN-231-004",
                       "ambiguous-preservation": "refs/heads/aios/failure/RUN-231-005"}[drift]
                git(remote, "update-ref", ref, failed if drift == "ambiguous-preservation" else failure["base_sha"])
            elif drift == "dirty":
                (repo / "README.md").write_text("edge dirty\n", encoding="utf-8")
            elif drift == "untracked":
                (repo / "untracked.txt").write_text("edge untracked\n", encoding="utf-8")
            else:
                exclude = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "info" / "exclude"
                exclude.parent.mkdir(parents=True, exist_ok=True)
                with exclude.open("ab") as stream:
                    stream.write(b"\nNEW_MAIN.txt\n")
                (repo / "NEW_MAIN.txt").write_text("edge ignored\n", encoding="utf-8")
            edge_snapshot = _reconciliation_snapshot(repo)
        return result

    monkeypatch.setattr(operator_module, "_git", drift_at_import)
    with pytest.raises(OperatorError):
        operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"])
    assert edge_snapshot is not None
    assert _reconciliation_snapshot(repo) == edge_snapshot
    assert not any(root == repo and args[0] == "reset" for root, args in calls)


@pytest.mark.parametrize("path", ["src/aios_renew/marker.py", ".ai/tasks/TASK-999.yaml"])
def test_primary_reconciliation_restart_retains_exact_pending_wakeup_selectors(
    tmp_path: Path, path: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo, _, _, target, _ = _transported_diverged_failure(
        tmp_path, candidate_changes={path: "# old candidate state\n"},
    )
    before = _reconciliation_snapshot(repo)[-1]
    argv = ["wakeup", "pending-257", "TASK-101", "--task-revision", "1",
            "--task-blob-sha", git(repo, "rev-parse", "HEAD:.ai/tasks/TASK-101.yaml"),
            "--task-commit-sha", target, "--executor", "codex", "--model", "gpt-5.6-sol",
            "--reasoning-effort", "high", "--model-source", "EXPLICIT",
            "--effort-source", "EXPLICIT", "--repo", str(repo)]
    restarted = []

    def restart_runner(cmd, **kwargs):
        restarted.append((cmd, kwargs))
        assert git(repo, "rev-parse", "HEAD") == target
        assert git(repo, "status", "--porcelain") == ""
        return subprocess.CompletedProcess(cmd, 23)

    outcome = operator_module._preflight_primary_sync(repo, argv=argv, runner=restart_runner)
    assert outcome.restart_code == 23
    assert outcome.preflight_sha is None
    assert len(restarted) == 1
    assert restarted[0][0] == [sys.executable, "-m", "aios_renew.operator", *argv]
    assert restarted[0][1]["env"]["AIOS_RESTART_ATTEMPTED"] == "1"
    assert _reconciliation_snapshot(repo)[-1] == before


def test_primary_reconciliation_stale_generation_rejects_before_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _, _, _ = _transported_diverged_failure(
        tmp_path, candidate_changes={"src/aios_renew/marker.py": "# candidate kernel\n"},
    )
    before = _reconciliation_snapshot(repo)
    with RepositoryLock(runtime_paths(repo).lock):
        with pytest.raises(OperatorError, match="stale pre-sync kernel"):
            operator_module._synchronize_primary_branch(repo)
    assert _reconciliation_snapshot(repo) == before
    monkeypatch.setenv("AIOS_RESTART_ATTEMPTED", "1")
    with pytest.raises(OperatorError, match="unsafe reload/restart"):
        operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"])
    assert _reconciliation_snapshot(repo) == before


def test_primary_reconciliation_failed_postconditions_block_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _, _, _ = _transported_diverged_failure(tmp_path)
    before = _reconciliation_snapshot(repo)[-1]
    real_git = operator_module._git

    def break_postcondition(root, *args, **kwargs):
        result = real_git(root, *args, **kwargs)
        if Path(root) == repo and args[0] == "reset":
            (repo / "postcondition.txt").write_text("unexpected dirty\n", encoding="utf-8")
        return result

    def forbidden(*args, **kwargs):
        raise AssertionError("failed postconditions cannot restart or admit execution")

    monkeypatch.setattr(operator_module, "_git", break_postcondition)
    monkeypatch.setattr(operator_module, "_restart_primary_invocation", forbidden)
    with pytest.raises(OperatorError, match="reconciliation postconditions failed"):
        operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"], runner=forbidden)
    assert _reconciliation_snapshot(repo)[-1] == before


def test_primary_reconciliation_lock_conflict_leaves_subject_unchanged(tmp_path: Path) -> None:
    repo, _, _, _, _ = _transported_diverged_failure(tmp_path)
    before = _reconciliation_snapshot(repo)
    with RepositoryLock(runtime_paths(repo).lock):
        with pytest.raises(OperatorError, match="active"):
            operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"])
    assert _reconciliation_snapshot(repo) == before


def test_primary_reconciliation_does_not_add_workflow_git_recovery() -> None:
    workflow = (Path(__file__).parents[1] / ".github/workflows/aios-self-hosted-wakeup.yml").read_text(
        encoding="utf-8",
    )
    for command in ("fetch", "reset", "rebase", "merge", "cherry-pick", "stash", "clean"):
        assert f"git -C $env:AIOS_REPO_ROOT {command}" not in workflow
    assert "reconcile-control-main" not in workflow


# TASK-264: reviewed transport proves preservation, never publication.
def _reviewed_preservation_metadata(repo: Path, parent: str, documents: dict[str, bytes]) -> str:
    from aios_renew.authoring_ingress import _commit_tree, _tree_with_metadata

    tree_parent = parent
    for path, content in documents.items():
        tree = _tree_with_metadata(repo, tree_parent, path, content)
        tree_parent = _commit_tree(repo, tree, [parent], "review preservation fixture")
    return tree_parent


def _reviewed_divergence(root: Path, *, delta: bool = True, merge_ancestry: bool = False) -> tuple:
    """RUN-262-004-shaped DELTA PASS with separate source/artifacts/decision."""
    repo = make_repo(root)
    remote = root / "upstream.git"
    task_path = repo / ".ai/tasks/TASK-262.yaml"
    task_path.write_text(TASK_SOURCE.replace("TASK-101", "TASK-262").replace("revision: 1", "revision: 2"), encoding="utf-8")
    common_base = commit_setup_state(repo, ".ai/tasks/TASK-262.yaml", message="authorized TASK")
    git(repo, "push", "--quiet", "origin", f"{common_base}:refs/heads/main")
    (repo / "OUTPUT.txt").write_text("primary candidate\n", encoding="utf-8")
    base = commit_setup_state(repo, "OUTPUT.txt", message="primary candidate")
    target = publish_upstream(repo, {"NEW_MAIN.txt": "canonical advancement\n"})
    state = runtime_paths(repo)
    run = {
        "run_id": "RUN-262-003", "task": {"id": "TASK-262", "revision": 2},
        "executor": "codex", "base_sha": common_base, "workspace": str(repo),
        "head_sha": None, "status": "ACTIVE",
    }
    primary = canonical_result_payload("RUN-262-003", base, changed_files=["OUTPUT.txt"])
    primary["result"]["claims"] = [{
        "id": "C1", "satisfies": ["AC1"], "claim": "Primary output exists.",
        "evidence": [primary["evidence"][0]["evidence_id"]],
    }]

    def artifacts(run_id, run_data, package):
        run_path = state.runs / f"{run_id}.json"
        result_path = state.results / f"{run_id}.json"
        run_path.write_text(json.dumps(run_data), encoding="utf-8")
        result_path.write_text(json.dumps(package), encoding="utf-8")
        transport_module.transport_post_pass(
            repo, run_id=run_id, head_sha=package["result"]["head_sha"],
            run_path=run_path, result_path=result_path,
        )

    artifacts("RUN-262-003", run, primary)
    finding = {
        "id": "FINDING-262-001", "basis": "AC1", "action": "CODE_FIX",
        "location": "OUTPUT.txt", "issue": "The output needs correction.",
        "expected": "Correct the output.",
    }
    prior = {
        "review_id": "REVIEW-262-001", "reviewed_sha": base, "mode": "PRIMARY",
        "verdict": "CHANGES_REQUIRED", "acceptance": {"AC1": "FAIL"}, "findings": [finding],
    }
    prior_bytes = json.dumps(prior).encode()
    prior_sha = _reviewed_preservation_metadata(repo, base, {".ai/reviews/REVIEW-262-001.yaml": prior_bytes})
    git(repo, "push", "--quiet", "origin", f"{prior_sha}:refs/heads/aios/review-decision/RUN-262-003")
    remediation = {
        "finding_id": finding["id"], "action": "CODE_FIX", "reviewed_sha": base,
        "modification_scope": ["OUTPUT.txt"], "affected_verification": ["git diff --check"],
        "constraints": {"hard": ["Commit the output."]},
    }
    authorization_sha = _reviewed_preservation_metadata(repo, base, {
        ".ai/reviews/REVIEW-262-001.yaml": prior_bytes,
        ".ai/remediations/FINDING-262-001.yaml": json.dumps(remediation).encode(),
    })
    git(repo, "push", "--quiet", "origin", f"{authorization_sha}:refs/heads/aios/remediation/RUN-262-003-FINDING-262-001")
    (repo / "OUTPUT.txt").write_text("earlier correction\n", encoding="utf-8")
    commit_setup_state(repo, "OUTPUT.txt", message="earlier local-only commit")
    (repo / "OUTPUT.txt").write_text("complete correction\n", encoding="utf-8")
    candidate = commit_setup_state(repo, "OUTPUT.txt", message="reviewed candidate")
    if merge_ancestry:
        from aios_renew.authoring_ingress import _commit_tree

        side = _commit_tree(repo, git(repo, "rev-parse", f"{common_base}^{{tree}}"), [common_base], "side ancestry")
        git(repo, "merge", "--quiet", "--no-ff", "--strategy=ours", side, "-m", "preserved merge ancestry")
        candidate = git(repo, "rev-parse", "HEAD")
    run = {**run, "run_id": "RUN-262-004", "base_sha": base if delta else common_base}
    if delta:
        run = {
            "kind": "REMEDIATION", "remediation_authorization_sha": authorization_sha,
            "predecessor": {
                "source_run_id": "RUN-262-003", "review_id": "REVIEW-262-001",
                "finding_id": finding["id"], "reviewed_sha": base,
            },
            "execution_base": {"run_id": "RUN-262-003", "candidate_sha": base},
            "execution": {
                "review_id": "REVIEW-262-001", "finding": finding,
                "remediation": remediation, "original_constraints": ["Commit the output."],
                "run": run,
            },
        }
    package = canonical_result_payload("RUN-262-004", candidate, changed_files=["OUTPUT.txt"])
    if not delta:
        package["result"]["claims"] = [{
            "id": "C1", "satisfies": ["AC1"], "claim": "The output is corrected.",
            "evidence": [package["evidence"][0]["evidence_id"]],
        }]
    artifacts("RUN-262-004", run, package)
    review = {
        "review_id": "REVIEW-262-002", "reviewed_sha": candidate,
        "mode": "DELTA" if delta else "PRIMARY", "verdict": "PASS",
        "acceptance": {"AC1": "PASS"}, "findings": [],
    }
    if delta:
        review["prior_finding_id"] = finding["id"]
    decision = _reviewed_preservation_metadata(repo, candidate, {
        ".ai/reviews/REVIEW-262-002.yaml": json.dumps(review).encode(),
    })
    git(repo, "push", "--quiet", "origin", f"{decision}:refs/heads/aios/review-decision/RUN-262-004")
    with RepositoryLock(state.lock):
        pass
    return repo, remote, candidate, target, base, run, package, review, decision


@pytest.mark.parametrize(("delta", "merge_ancestry"), [(True, False), (False, False), (True, True)])
def test_primary_reviewed_result_preserves_complete_history_without_lifecycle_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delta: bool, merge_ancestry: bool,
) -> None:
    repo, remote, candidate, target, *_ = _reviewed_divergence(tmp_path, delta=delta, merge_ancestry=merge_ancestry)
    before = _reconciliation_snapshot(repo)
    refs_before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    local_only = git(repo, "rev-list", candidate, "--not", target).splitlines()
    assert len(local_only) == (5 if merge_ancestry else 3)
    calls = []
    real_git = operator_module._git

    def record_git(root, *args, **kwargs):
        calls.append((Path(root), args))
        return real_git(root, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("preservation cannot invoke lifecycle or publication authority")

    monkeypatch.setattr(operator_module, "_git", record_git)
    for name in ("run_task", "run_repair", "run_remediation", "retry_transport", "reconcile_control_main"):
        monkeypatch.setattr(operator_module, name, forbidden)
    monkeypatch.setattr(publication_module, "publish_review_decision", forbidden)
    monkeypatch.setattr(publication_module, "_derive_publication_frontier", forbidden)
    outcome = operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"])
    assert outcome.preflight_sha == target and outcome.restart_code is None
    after = _reconciliation_snapshot(repo)
    assert after[:2] == ("refs/heads/main", target) and after[3] == ""
    assert after[5] == before[5] and after[-1] == before[-1]
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == refs_before
    for sha in local_only:
        assert operator_module._git_is_ancestor(remote, sha, candidate)
    mutations = [args for root, args in calls if root == repo and args[0] in {
        "reset", "merge", "rebase", "cherry-pick", "stash", "clean", "commit", "push", "update-ref",
    }]
    assert mutations == [("reset", "--hard", target)]
    # The complete history proof is evaluated before import and again at reset.
    history_checks = [args for _, args in calls if args == ("rev-list", candidate, "--not", target)]
    assert len(history_checks) == 2


@pytest.mark.parametrize("gate", [
    "unreviewed", "missing-artifacts", "substituted-artifacts", "source-tree-artifacts", "missing-result", "malformed-result",
    "duplicate-json", "wrong-result-head", "wrong-evidence-run", "wrong-run", "wrong-task", "wrong-revision",
    "non-pass", "wrong-reviewed-sha", "substituted-decision", "ambiguous-decision", "decision-alias",
    "ambiguous-run", "mixed-families", "conflicting-terminal", "wrong-prior-finding",
    "extra-local-commit", "malformed-run", "wrong-decision-path", "unrelated-base", "substituted-prior",
])
def test_primary_reviewed_result_rejects_invalid_proofs_without_reset_or_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: str,
) -> None:
    repo, remote, candidate, _, base, run, package, review, decision = _reviewed_divergence(tmp_path)
    artifact_ref = "refs/heads/aios/artifacts/RUN-262-004"
    decision_ref = "refs/heads/aios/review-decision/RUN-262-004"
    state = runtime_paths(repo)
    if gate in {"unreviewed", "missing-artifacts"}:
        git(remote, "update-ref", "-d", decision_ref if gate == "unreviewed" else artifact_ref)
    elif gate == "substituted-artifacts":
        git(remote, "update-ref", artifact_ref, git(remote, "rev-parse", "refs/heads/aios/artifacts/RUN-262-003"))
    elif gate == "substituted-prior":
        replacement = _reviewed_preservation_metadata(repo, base, {
            ".ai/reviews/REVIEW-262-OTHER.yaml": json.dumps({
                "review_id": "REVIEW-262-OTHER", "reviewed_sha": base, "mode": "PRIMARY",
                "verdict": "PASS", "acceptance": {"AC1": "PASS"}, "findings": [],
            }).encode(),
        })
        # Import the replacement's object closure without changing other refs.
        git(remote, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--refmap=", str(repo), replacement)
        git(remote, "update-ref", "refs/heads/aios/review-decision/RUN-262-003", replacement)
    elif gate in {"non-pass", "wrong-reviewed-sha", "substituted-decision", "ambiguous-decision",
                  "wrong-prior-finding", "wrong-decision-path"}:
        if gate == "non-pass":
            review["verdict"] = "CHANGES_REQUIRED"
            review["acceptance"] = {"AC1": "FAIL"}
        elif gate == "wrong-reviewed-sha":
            review["reviewed_sha"] = base
        elif gate == "wrong-prior-finding":
            review["prior_finding_id"] = "OTHER"
        documents = {".ai/reviews/REVIEW-262-002.yaml": json.dumps(review).encode()}
        if gate == "ambiguous-decision":
            documents[".ai/reviews/OTHER.yaml"] = json.dumps(review).encode()
        elif gate == "wrong-decision-path":
            documents = {".ai/reviews/SUBSTITUTE.yaml": json.dumps(review).encode()}
        replacement = _reviewed_preservation_metadata(repo, base if gate == "substituted-decision" else candidate, documents)
        git(remote, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--refmap=", str(repo), replacement)
        git(remote, "update-ref", decision_ref, replacement)
    elif gate == "decision-alias":
        git(remote, "update-ref", "-d", decision_ref)
        git(remote, "update-ref", decision_ref + "/extra", decision)
    elif gate == "ambiguous-run":
        git(remote, "update-ref", "refs/heads/aios/review/RUN-262-005", candidate)
    elif gate in {"mixed-families", "conflicting-terminal"}:
        git(remote, "update-ref", "refs/heads/aios/failure/RUN-262-005" if gate == "mixed-families"
            else "refs/heads/aios/failure-artifacts/RUN-262-004", candidate)
    elif gate == "extra-local-commit":
        (repo / "OUTPUT.txt").write_text("unpreserved\n", encoding="utf-8")
        commit_setup_state(repo, "OUTPUT.txt", message="unpreserved commit")
    else:
        if gate == "wrong-result-head":
            package["result"]["head_sha"] = base
        elif gate == "wrong-evidence-run":
            package["evidence"][0]["run_id"] = "RUN-262-003"
        elif gate == "wrong-run":
            run["execution"]["run"]["run_id"] = "RUN-262-005"
        elif gate == "wrong-task":
            run["execution"]["run"]["task"]["id"] = "TASK-101"
        elif gate == "wrong-revision":
            run["execution"]["run"]["task"]["revision"] = 1
        elif gate == "unrelated-base":
            run["execution"]["run"]["base_sha"] = decision
        run_bytes = b"{" if gate == "malformed-run" else json.dumps(run).encode()
        result_bytes = b"{" if gate == "malformed-result" else json.dumps(package).encode()
        if gate == "duplicate-json":
            result_bytes = b'{"result":{},"result":{},"evidence":[]}'
        documents = {".ai/transport/run.json": run_bytes}
        if gate != "missing-result":
            documents[".ai/transport/result.json"] = result_bytes
        if gate in {"source-tree-artifacts", "missing-result"}:
            replacement = _reviewed_preservation_metadata(repo, base, documents)
        else:
            # Keep the canonical artifact tree shape so these cases exercise
            # JSON and identity validation, rather than a tree-shape rejection.
            run_path = state.runs / "RUN-262-004.json"
            result_path = state.results / "RUN-262-004.json"
            run_path.write_bytes(run_bytes)
            result_path.write_bytes(result_bytes)
            replacement = transport_module._create_artifacts_commit(
                repo, run_path=run_path, result_path=result_path, run_id="RUN-262-004",
            )
        git(remote, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--refmap=", str(repo), replacement)
        git(remote, "update-ref", artifact_ref, replacement)
    before = _reconciliation_snapshot(repo)
    refs_before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    calls = []
    real_git = operator_module._git

    def record_git(root, *args, **kwargs):
        calls.append((Path(root), args))
        return real_git(root, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("rejected preservation cannot admit or invoke")

    monkeypatch.setattr(operator_module, "_git", record_git)
    monkeypatch.setattr(operator_module, "run_task", forbidden)
    monkeypatch.setattr(operator_module, "_restart_primary_invocation", forbidden)
    with pytest.raises(OperatorError):
        operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"], runner=forbidden)
    assert _reconciliation_snapshot(repo) == before
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == refs_before
    assert not any(root == repo and args[0] == "reset" for root, args in calls)
    assert not list(state.runs.glob("RUN-101-*.json"))


@pytest.mark.parametrize("drift", ["candidate", "artifact", "decision", "predecessor", "target", "mixed-proof"])
def test_primary_reviewed_result_reproves_moved_transport_at_mutation_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    repo, remote, candidate, _, base, *_ = _reviewed_divergence(tmp_path)
    real_git = operator_module._git
    calls = []
    edge_snapshot = None

    def drift_at_import(root, *args, **kwargs):
        nonlocal edge_snapshot
        calls.append((Path(root), args))
        result = real_git(root, *args, **kwargs)
        if Path(root) == repo and args[0] == "fetch":
            ref = {
                "candidate": "refs/heads/aios/review/RUN-262-004",
                "artifact": "refs/heads/aios/artifacts/RUN-262-004",
                "decision": "refs/heads/aios/review-decision/RUN-262-004",
                "predecessor": "refs/heads/aios/review-decision/RUN-262-003",
                "target": "refs/heads/main",
                "mixed-proof": "refs/heads/aios/failure/RUN-262-005",
            }[drift]
            git(remote, "update-ref", ref, candidate if drift == "mixed-proof" else base)
            edge_snapshot = _reconciliation_snapshot(repo)
        return result

    monkeypatch.setattr(operator_module, "_git", drift_at_import)
    with pytest.raises(OperatorError):
        operator_module._preflight_primary_sync(repo, argv=["run", "TASK-101"])
    assert edge_snapshot is not None
    assert _reconciliation_snapshot(repo) == edge_snapshot
    assert not any(root == repo and args[0] == "reset" for root, args in calls)


# TASK-270: RUN-268 preservation before TASK-269 must retain exact REVIEW bytes.
def _reviewed_blob_divergence(
    root: Path, *, line_ending: bytes = b"\n", trailing_space: bytes = b"",
) -> tuple:
    repo = make_repo(root)
    remote = root / "upstream.git"
    task_path = repo / ".ai/tasks/TASK-268.yaml"
    task_path.write_text(TASK_SOURCE.replace("TASK-101", "TASK-268"), encoding="utf-8")
    base = commit_setup_state(repo, ".ai/tasks/TASK-268.yaml", message="authorized TASK-268")
    git(repo, "push", "--quiet", "origin", f"{base}:refs/heads/main")
    (repo / "OUTPUT.txt").write_text("reviewed output\n", encoding="utf-8")
    candidate = commit_setup_state(repo, "OUTPUT.txt", message="RUN-268 candidate")
    target = publish_upstream(repo, {"NEW_MAIN.txt": "canonical advancement\n"})
    state = runtime_paths(repo)
    run = {
        "run_id": "RUN-268-001", "task": {"id": "TASK-268", "revision": 1},
        "executor": "codex", "base_sha": base, "workspace": str(repo),
        "head_sha": None, "status": "ACTIVE",
    }
    package = canonical_result_payload("RUN-268-001", candidate, changed_files=["OUTPUT.txt"])
    package["result"]["claims"] = [{
        "id": "C1", "satisfies": ["AC1"], "claim": "The output exists.",
        "evidence": [package["evidence"][0]["evidence_id"]],
    }]
    run_path = state.runs / "RUN-268-001.json"
    result_path = state.results / "RUN-268-001.json"
    run_path.write_bytes(json.dumps(run).encode("utf-8"))
    result_path.write_bytes(json.dumps(package).encode("utf-8"))
    transport_module.transport_post_pass(
        repo, run_id="RUN-268-001", head_sha=candidate,
        run_path=run_path, result_path=result_path,
    )
    review = {
        "review_id": "REVIEW-268-001", "reviewed_sha": candidate, "mode": "PRIMARY",
        "verdict": "PASS", "acceptance": {"AC1": "PASS"}, "findings": [],
    }
    content = json.dumps(review, indent=2).encode("utf-8").replace(b"\n", line_ending)
    content += trailing_space + line_ending
    decision = _reviewed_preservation_metadata(repo, candidate, {
        ".ai/reviews/REVIEW-268-001.yaml": content,
    })
    git(repo, "push", "--quiet", "origin", f"{decision}:refs/heads/aios/review-decision/RUN-268-001")
    with RepositoryLock(state.lock):
        pass
    return repo, remote, candidate, target, content, decision


@pytest.mark.parametrize(("line_ending", "trailing_space"), [
    pytest.param(b"\n", b"", id="terminal-lf"),
    pytest.param(b"\n", b"  ", id="trailing-spaces"),
    pytest.param(b"\r\n", b"", id="crlf"),
])
def test_primary_reviewed_result_preserves_exact_review_blob_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line_ending: bytes, trailing_space: bytes,
) -> None:
    from aios_renew import authoring_ingress as ingress

    repo, remote, candidate, target, content, decision = _reviewed_blob_divergence(
        tmp_path, line_ending=line_ending, trailing_space=trailing_space,
    )
    review_path = ".ai/reviews/REVIEW-268-001.yaml"
    assert ingress._read_commit_blob(repo, decision, review_path) == content
    # The old reader loses bytes even though the canonical decision is valid.
    assert publication_module._read_blob(repo, decision, review_path, run_id="RUN-268-001") != content
    before = _reconciliation_snapshot(repo)
    refs_before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    validations = []
    calls = []
    real_validate = ingress._validate_metadata_commit
    real_git = operator_module._git

    def record_validation(root, commit_sha, **kwargs):
        validations.append((commit_sha, kwargs))
        return real_validate(root, commit_sha, **kwargs)

    def record_git(root, *args, **kwargs):
        calls.append((Path(root), args))
        return real_git(root, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("preservation cannot admit, invoke or publish")

    monkeypatch.setattr(ingress, "_validate_metadata_commit", record_validation)
    monkeypatch.setattr(operator_module, "_git", record_git)
    for name in ("run_task", "run_repair", "run_remediation", "_restart_primary_invocation"):
        monkeypatch.setattr(operator_module, name, forbidden)
    monkeypatch.setattr(publication_module, "publish_review_decision", forbidden)
    outcome = operator_module._preflight_primary_sync(repo, argv=["run", "TASK-269"], runner=forbidden)
    assert outcome.preflight_sha == target and outcome.restart_code is None
    # Both the initial proof and the mutation-edge reproof use the unchanged validator.
    assert validations == [(decision, {
        "expected_parent_sha": candidate, "metadata_path": review_path,
        "metadata_bytes": content, "operation": "SUBMIT_REVIEW",
    })] * 2
    after = _reconciliation_snapshot(repo)
    assert after[:2] == ("refs/heads/main", target) and after[3] == ""
    assert after[5] == before[5] and after[-1] == before[-1]
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == refs_before
    assert ingress._read_commit_blob(remote, decision, review_path) == content
    mutations = [args for root, args in calls if root == repo and args[0] in {
        "reset", "merge", "rebase", "cherry-pick", "stash", "clean", "commit", "push", "update-ref",
    }]
    assert mutations == [("reset", "--hard", target)]
    assert not list(runtime_paths(repo).runs.glob("RUN-269-*.json"))


@pytest.mark.parametrize("substitution", ["whitespace", "acceptance-content"])
@pytest.mark.parametrize("proof_read", [1, 3], ids=["initial-proof", "mutation-edge"])
def test_primary_reviewed_result_rejects_different_review_blob_bytes_non_destructively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, substitution: str, proof_read: int,
) -> None:
    from aios_renew import authoring_ingress as ingress

    repo, remote, candidate, _, content, decision = _reviewed_blob_divergence(tmp_path)
    review_path = ".ai/reviews/REVIEW-268-001.yaml"
    if substitution == "whitespace":
        substituted_content = content[:-1] + b"  \r\n"
        assert operator_module.parse_review(substituted_content.decode("utf-8")) == operator_module.parse_review(
            content.decode("utf-8"),
        )
    else:
        substituted_review = json.loads(content)
        substituted_review["acceptance"]["AC1"] = "FAIL"
        substituted_content = json.dumps(substituted_review, indent=2).encode("utf-8") + b"\n"
    substituted_decision = _reviewed_preservation_metadata(repo, candidate, {review_path: substituted_content})
    assert substituted_decision != decision and substituted_content != content
    real_read = ingress._read_commit_blob
    assert real_read(repo, substituted_decision, review_path) == substituted_content
    before = _reconciliation_snapshot(repo)
    refs_before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    reads = []
    calls = []
    real_git = operator_module._git

    def substitute_proof_read(root, commit_sha, path):
        actual = real_read(root, commit_sha, path)
        if commit_sha == decision and path == review_path:
            # Inject only the proof input; the real validator independently reads
            # the canonical blob and must reject the unequal supplied bytes.
            supplied = substituted_content if len(reads) + 1 == proof_read else actual
            reads.append((actual, supplied))
            return supplied
        return actual

    def record_git(root, *args, **kwargs):
        calls.append((Path(root), args))
        return real_git(root, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("rejected preservation cannot admit or invoke")

    monkeypatch.setattr(ingress, "_read_commit_blob", substitute_proof_read)
    monkeypatch.setattr(operator_module, "_git", record_git)
    monkeypatch.setattr(operator_module, "run_task", forbidden)
    monkeypatch.setattr(operator_module, "_restart_primary_invocation", forbidden)
    with pytest.raises(OperatorError, match="SUBMIT_REVIEW structural validation failed: metadata content mismatch"):
        operator_module._preflight_primary_sync(repo, argv=["run", "TASK-269"], runner=forbidden)
    assert len(reads) == proof_read + 1
    assert reads[proof_read - 1] == (content, substituted_content)
    assert reads[proof_read] == (content, content)
    assert _reconciliation_snapshot(repo) == before
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == refs_before
    assert not any(root == repo and args[0] == "reset" for root, args in calls)
    assert not list(runtime_paths(repo).runs.glob("RUN-269-*.json"))

def test_h4c1_persisted_run_decoding_and_task_mismatch_fail_closed(monkeypatch):
    from dataclasses import asdict
    from aios_renew.return_affinity import OriginAffinity
    from aios_renew.run import Run
    from aios_renew.decision_packet import _run as packet_run
    from aios_renew.unified_state import _decode_lifecycle_run
    task = operator_module.parse_task(TASK_SOURCE + "\nreturn_affinity: " + json.dumps(
        asdict(OriginAffinity("page-origin-v1:" + "a" * 64, 1))))
    run = Run.from_task(run_id="RUN-101-001", task=task, executor="codex", base_sha="b" * 40, workspace="fixture")
    document = asdict(run)
    assert operator_module._run_from_data(document) == run
    assert _decode_lifecycle_run(json.dumps(document).encode(), run_id=run.run_id)[0] == run
    packet_run(document, task)
    monkeypatch.setattr(transport_module, "_read_local_blob", lambda *args: (TASK_SOURCE + "\nreturn_affinity: " + json.dumps(asdict(task.return_affinity))).encode())
    transport_module._validate_transport_affinity(Path("."), json.dumps(document).encode(), "c" * 40)
    for changed in (dict(document, return_affinity={"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}),
                    {key: value for key, value in document.items() if key != "return_affinity"},
                    dict(document, return_affinity=asdict(OriginAffinity(task.return_affinity.route_handle, 2)))):
        with pytest.raises(ValueError, match="affinity"):
            packet_run(changed, task)
        with pytest.raises(transport_module.ReviewTransportError, match="affinity"):
            transport_module._validate_transport_affinity(Path("."), json.dumps(changed).encode(), "c" * 40)
