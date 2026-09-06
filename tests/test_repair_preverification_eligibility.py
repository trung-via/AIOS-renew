import json
import subprocess
from pathlib import Path

import pytest

from aios_renew.operator import (
    OperatorError,
    _eligible_reusable_repair_package,
    load_task,
    run_repair,
    runtime_paths,
)


TASK_SOURCE = """
task_id: TASK-101
revision: 1
goal: Exercise REPAIR pre-verification eligibility.
problem: Preserve ordinary repair dispatch when reuse is not authorized.
assumptions: []
scope:
  inspect: []
  modify: [OUTPUT.txt]
non_goals: []
constraints:
  hard: [Commit the output.]
acceptance:
  - id: AC1
    condition: The requested repair is complete.
verification:
  required: [git status --porcelain]
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "Eligibility Test")
    git(repo, "config", "user.email", "eligibility@example.invalid")
    git(repo, "branch", "-M", "main")
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK-101.yaml").write_text(TASK_SOURCE, encoding="utf-8")
    (repo / "README.md").write_text("# eligibility test\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "baseline")
    upstream = root / "upstream.git"
    subprocess.run(("git", "init", "--bare", "--quiet", str(upstream)), check=True)
    git(repo, "remote", "add", "origin", str(upstream))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "main")
    return repo


def predecessor(repo: Path, *, claims: list[dict] | None = None) -> tuple[str, dict]:
    state = runtime_paths(repo)
    run_id = "RUN-101-000"
    head = git(repo, "rev-parse", "HEAD")
    run = {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    candidate = {
        "transportable": True,
        "repairable": True,
        "dirty": False,
        "descends_from_base": True,
        "changed_files": [],
        "outside_task_scope": [],
    }
    failure = {
        "kind": "FAILURE",
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": head,
        "failed_head_sha": head,
        "phase": "VERIFICATION",
        "candidate": candidate,
    }
    sidecar = {
        "kind": "PRE_VERIFICATION_CANDIDATE",
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "subject_sha": head,
        "package": {
            "result": {
                "head_sha": head,
                "claims": [] if claims is None else claims,
                "changed_files": [],
                "unresolved": [],
            },
            "evidence": [],
        },
    }
    (state.runs / f"{run_id}.json").write_text(json.dumps(run), encoding="utf-8")
    (state.failures / f"{run_id}.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )
    (state.preverification / f"{run_id}.json").write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    return run_id, failure


def repair(failure: dict, *, action: str) -> dict:
    return {
        "repair_id": f"REPAIR-101-{action}",
        "failed_run_id": failure["run_id"],
        "failed_head_sha": failure["failed_head_sha"],
        "task": failure["task"],
        "action": action,
        "modification_scope": ["OUTPUT.txt"] if action == "CODE_FIX" else [],
        "instructions": ["Apply the authorized correction."],
        "constraints": ["Commit the output."],
    }


class CodeFixRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls = 0

    def __call__(self, command, **kwargs):
        self.calls += 1
        execution = json.loads(
            kwargs["input"].decode("utf-8").split("REPAIR_INPUT:\n", 1)[1]
        )
        (self.repo / "OUTPUT.txt").write_text("corrected\n", encoding="utf-8")
        git(self.repo, "add", "OUTPUT.txt")
        git(self.repo, "commit", "--quiet", "-m", "code fix")
        head = git(self.repo, "rev-parse", "HEAD")
        payload = {
            "result": {
                "head_sha": head,
                "claims": [{
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "The repair is complete.",
                    "evidence": [],
                }],
                "changed_files": ["OUTPUT.txt"],
                "unresolved": [],
            },
            "evidence": [],
        }
        assert execution["repair"]["action"] == "CODE_FIX"
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")


def passing_verification(command, **kwargs):
    return subprocess.CompletedProcess(command, 0, b"clean\n", b"")


def test_code_fix_with_valid_claimless_sidecar_uses_ordinary_executor_path(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    runner = CodeFixRunner(repo)

    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair=repair(failure, action="CODE_FIX"),
        native_runner=runner,
        verification_runner=passing_verification,
    )

    assert runner.calls == 1
    assert summary.run_id == "RUN-101-001"
    assert summary.head_sha != failure["failed_head_sha"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("phase", "EXECUTION"),
        ("transportable", False),
        ("dirty", True),
        ("descends_from_base", False),
        ("outside_task_scope", ["FOREIGN.txt"]),
    ],
)
def test_other_fast_path_disqualifiers_do_not_impose_acceptance_coverage(
    tmp_path: Path, field: str, value: object
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    if field == "phase":
        failure[field] = value
    else:
        failure["candidate"][field] = value
    content = (runtime_paths(repo).preverification / f"{failed_run_id}.json").read_bytes()

    assert _eligible_reusable_repair_package(
        content,
        task=load_task(repo, "TASK-101"),
        failed_run_id=failed_run_id,
        failure=failure,
        action="NO_CHANGE",
        scope=[],
    ) is None


@pytest.mark.parametrize("mismatch", ["malformed", "run", "task", "subject", "files"])
def test_invalid_or_mismatched_sidecar_still_fails_closed_for_code_fix(
    tmp_path: Path, mismatch: str
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    path = runtime_paths(repo).preverification / f"{failed_run_id}.json"
    if mismatch == "malformed":
        content = b"{malformed"
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if mismatch == "run":
            data["run_id"] = "RUN-101-999"
        elif mismatch == "task":
            data["task"]["revision"] = 2
        elif mismatch == "subject":
            data["subject_sha"] = "0" * 40
        else:
            data["package"]["result"]["changed_files"] = ["OUTPUT.txt"]
        content = json.dumps(data).encode()

    with pytest.raises(OperatorError, match="pre-verification candidate"):
        _eligible_reusable_repair_package(
            content,
            task=load_task(repo, "TASK-101"),
            failed_run_id=failed_run_id,
            failure=failure,
            action="CODE_FIX",
            scope=["OUTPUT.txt"],
        )


def test_qualifying_claimless_no_change_reuse_rejects_before_run_or_calls(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    state = runtime_paths(repo)
    calls = []

    with pytest.raises(OperatorError, match="lacks TASK acceptance coverage: AC1"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair(failure, action="NO_CHANGE"),
            native_runner=lambda *args, **kwargs: calls.append("executor"),
            verification_runner=lambda *args, **kwargs: calls.append("verification"),
        )

    assert calls == []
    assert not (state.runs / "RUN-101-001.json").exists()


def test_qualifying_full_coverage_sidecar_remains_reusable(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    claim = {
        "id": "C1",
        "satisfies": ["AC1"],
        "claim": "The requested repair is complete.",
        "evidence": [],
    }
    failed_run_id, failure = predecessor(repo, claims=[claim])
    content = (runtime_paths(repo).preverification / f"{failed_run_id}.json").read_bytes()

    package = _eligible_reusable_repair_package(
        content,
        task=load_task(repo, "TASK-101"),
        failed_run_id=failed_run_id,
        failure=failure,
        action="NO_CHANGE",
        scope=[],
    )

    assert package is not None
    assert package.result.claims[0].satisfies == ("AC1",)
