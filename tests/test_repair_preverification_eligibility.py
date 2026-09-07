import json
import subprocess
from pathlib import Path

import pytest

from aios_renew.operator import (
    OperatorError,
    _eligible_reusable_repair_package,
    load_task,
    run_repair,
    run_task,
    runtime_paths,
)
from aios_renew.review_transport import transport_failure


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
        target_repo = Path(execution["run"]["workspace"])
        (target_repo / "OUTPUT.txt").write_text("corrected\n", encoding="utf-8")
        git(target_repo, "add", "OUTPUT.txt")
        git(target_repo, "commit", "--quiet", "-m", "code fix")
        head = git(target_repo, "rev-parse", "HEAD")
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


class PrimaryRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls = 0

    def __call__(self, command, **kwargs):
        self.calls += 1
        (self.repo / "OUTPUT.txt").write_text("initial\n", encoding="utf-8")
        git(self.repo, "add", "OUTPUT.txt")
        git(self.repo, "commit", "--quiet", "-m", "initial primary")
        head = git(self.repo, "rev-parse", "HEAD")
        payload = {
            "result": {
                "head_sha": head,
                "claims": [{
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "Initial primary attempt.",
                    "evidence": [],
                }],
                "changed_files": ["OUTPUT.txt"],
                "unresolved": [],
            },
            "evidence": [],
        }
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


@pytest.mark.parametrize(
    "mismatch",
    ["malformed", "incomplete", "run", "task", "subject", "files", "missing_coverage"],
)
def test_code_fix_dispatches_when_predecessor_sidecar_is_unusable_for_reuse(
    tmp_path: Path, mismatch: str
) -> None:
    """AC1: CODE_FIX REPAIR is dispatched to its selected Executor exactly once even
    when predecessor reusable-candidate state is malformed, incomplete, wrong-subject,
    changed-files-inconsistent, wrong-RUN, wrong-TASK, or lacks acceptance coverage.
    """
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    path = runtime_paths(repo).preverification / f"{failed_run_id}.json"
    if mismatch == "malformed":
        content = b"{malformed"
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if mismatch == "incomplete":
            data["package"]["result"]["unresolved"] = ["unresolved issue"]
        elif mismatch == "run":
            data["run_id"] = "RUN-101-999"
        elif mismatch == "task":
            data["task"]["revision"] = 2
        elif mismatch == "subject":
            data["subject_sha"] = "0" * 40
        elif mismatch == "files":
            data["package"]["result"]["changed_files"] = ["OUTPUT.txt"]
        elif mismatch == "missing_coverage":
            data["package"]["result"]["claims"] = []
        content = json.dumps(data).encode()

    path.write_bytes(content)

    # Verification-only reusable package eligibility bypasses CODE_FIX entirely
    assert _eligible_reusable_repair_package(
        content,
        task=load_task(repo, "TASK-101"),
        failed_run_id=failed_run_id,
        failure=failure,
        action="CODE_FIX",
        scope=["OUTPUT.txt"],
    ) is None

    # Ordinary Executor is dispatched exactly once
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


def test_historical_code_fix_ignores_local_remote_sidecar_conflict(
    tmp_path: Path,
) -> None:
    """AC2: CODE_FIX is not rejected by local-versus-transported reusable-candidate
    equality checks during historical REPAIR recovery.
    """
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    state = runtime_paths(repo)

    # Publish canonical failure to remote ref
    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failure["failed_head_sha"],
        run_path=state.runs / f"{failed_run_id}.json",
        failure_path=state.failures / f"{failed_run_id}.json",
        publish_candidate=True,
        preverification_path=state.preverification / f"{failed_run_id}.json",
    )

    # Advance repository HEAD so the repair is historical
    (repo / "ADVANCE.txt").write_text("advance\n", encoding="utf-8")
    git(repo, "add", "ADVANCE.txt")
    git(repo, "commit", "--quiet", "-m", "advance main")

    # Local preverification candidate conflicts with remote transported candidate
    (state.preverification / f"{failed_run_id}.json").write_bytes(
        b'{"conflicting": "local_preverification"}'
    )

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
    assert summary.head_sha != failure["failed_head_sha"]


def test_code_fix_preserves_independent_canonical_gates(tmp_path: Path) -> None:
    """AC2: Independent canonical FAILURE, TASK, failed_head_sha, and REPAIR gates
    remain enforced fail closed for CODE_FIX.
    """
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)

    # Mismatched failed_head_sha in REPAIR
    bad_head_repair = repair(failure, action="CODE_FIX")
    bad_head_repair["failed_head_sha"] = "0" * 40
    with pytest.raises(OperatorError, match="REPAIR does not match failed committed state"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=bad_head_repair,
            native_runner=CodeFixRunner(repo),
            verification_runner=passing_verification,
        )

    # Mismatched TASK revision in REPAIR
    bad_task_repair = repair(failure, action="CODE_FIX")
    bad_task_repair["task"] = {"id": "TASK-101", "revision": 2}
    with pytest.raises(OperatorError, match="REPAIR does not match original TASK lineage"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=bad_task_repair,
            native_runner=CodeFixRunner(repo),
            verification_runner=passing_verification,
        )

    # Candidate not repairable
    bad_cand_failure = dict(failure)
    bad_cand_failure["candidate"] = dict(failure["candidate"])
    bad_cand_failure["candidate"]["repairable"] = False
    (runtime_paths(repo).failures / f"{failed_run_id}.json").write_text(
        json.dumps(bad_cand_failure), encoding="utf-8"
    )
    with pytest.raises(OperatorError, match="failed candidate is not safely bound for REPAIR"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair(failure, action="CODE_FIX"),
            native_runner=CodeFixRunner(repo),
            verification_runner=passing_verification,
        )


@pytest.mark.parametrize(
    "defect",
    [
        "malformed",
        "wrong_run",
        "wrong_task",
        "wrong_subject",
        "files_mismatch",
        "incomplete",
        "coverage",
        "conflicting_historical",
    ],
)
def test_eligible_no_change_fails_closed_on_unusable_or_conflicting_sidecar(
    tmp_path: Path, defect: str
) -> None:
    """AC3: A genuinely eligible NO_CHANGE verification-only reuse request fails closed
    before canonical verification when its reusable state is malformed, conflicting,
    wrong-RUN, wrong-TASK/revision, wrong-subject, changed-files-inconsistent,
    incomplete, or lacks complete TASK acceptance coverage; no Executor fallback occurs.
    """
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    full_claim = [{
        "id": "C1",
        "satisfies": ["AC1"],
        "claim": "The requested repair is complete.",
        "evidence": [],
    }]
    failed_run_id, failure = predecessor(repo, claims=full_claim)
    path = state.preverification / f"{failed_run_id}.json"

    if defect == "conflicting_historical":
        transport_failure(
            repo,
            run_id=failed_run_id,
            head_sha=failure["failed_head_sha"],
            run_path=state.runs / f"{failed_run_id}.json",
            failure_path=state.failures / f"{failed_run_id}.json",
            publish_candidate=True,
            preverification_path=path,
        )
        (repo / "ADVANCE.txt").write_text("advance\n", encoding="utf-8")
        git(repo, "add", "ADVANCE.txt")
        git(repo, "commit", "--quiet", "-m", "advance main")
        path.write_bytes(b'{"conflicting": "different_sidecar"}')
    elif defect == "malformed":
        path.write_bytes(b"{malformed")
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if defect == "wrong_run":
            data["run_id"] = "RUN-101-999"
        elif defect == "wrong_task":
            data["task"]["revision"] = 2
        elif defect == "wrong_subject":
            data["subject_sha"] = "0" * 40
        elif defect == "files_mismatch":
            data["package"]["result"]["changed_files"] = ["OUTPUT.txt"]
        elif defect == "incomplete":
            data["package"]["result"]["unresolved"] = ["unresolved issue"]
        elif defect == "coverage":
            data["package"]["result"]["claims"] = []
        path.write_text(json.dumps(data), encoding="utf-8")

    calls = []
    with pytest.raises(OperatorError):
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


def test_qualifying_no_change_reuse_executes_verification_once_with_zero_executors(
    tmp_path: Path,
) -> None:
    """AC4: Qualifying NO_CHANGE reuse remains zero-Executor and executes canonical
    TASK verification exactly once through the Runtime completion boundary.
    """
    repo = make_repo(tmp_path)
    claim = {
        "id": "C1",
        "satisfies": ["AC1"],
        "claim": "The requested repair is complete.",
        "evidence": [],
    }
    failed_run_id, failure = predecessor(repo, claims=[claim])
    calls = []

    def verification_runner(command, **kwargs):
        calls.append("verification")
        return subprocess.CompletedProcess(command, 0, b"clean\n", b"")

    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair=repair(failure, action="NO_CHANGE"),
        native_runner=lambda *args, **kwargs: calls.append("executor"),
        verification_runner=verification_runner,
    )

    assert calls == ["verification"]
    assert summary.run_id == "RUN-101-001"
    assert summary.head_sha == failure["failed_head_sha"]


def test_continuation_regression_primary_fail_no_change_fail_code_fix_succeeds(
    tmp_path: Path,
) -> None:
    """AC5 & AC6: Full deterministic continuation regression modeling PRIMARY
    canonical verification failure -> eligible NO_CHANGE continuation whose canonical
    verification fails -> authorized CODE_FIX REPAIR that dispatches normally exactly once,
    remains bound to exact latest failed_head_sha and continuation/root lineage, and is
    processed by existing completion/verification gates without reusable package substitution.
    """
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    v_calls = []

    def failing_verification(command, **kwargs):
        v_calls.append("fail")
        return subprocess.CompletedProcess(
            command, 1, b"", b"canonical verification failed\n"
        )

    def passing_verification(command, **kwargs):
        v_calls.append("pass")
        return subprocess.CompletedProcess(command, 0, b"clean\n", b"")

    # Step 1: PRIMARY execution fails at canonical verification
    primary_runner = PrimaryRunner(repo)
    with pytest.raises(OperatorError, match="exit code 1"):
        run_task(
            "TASK-101",
            executor="codex",
            repo=repo,
            native_runner=primary_runner,
            verification_runner=failing_verification,
        )

    assert primary_runner.calls == 1
    assert len(v_calls) == 1
    primary_failure = json.loads(
        (state.failures / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert primary_failure["phase"] == "VERIFICATION"
    assert primary_failure["candidate"]["repairable"] is True
    assert (state.preverification / "RUN-101-001.json").is_file()

    # Step 2: NO_CHANGE continuation reuses pre-verification package; verification fails
    no_change_auth = {
        "repair_id": "REPAIR-101-001",
        "failed_run_id": "RUN-101-001",
        "failed_head_sha": primary_failure["failed_head_sha"],
        "task": {"id": "TASK-101", "revision": 1},
        "action": "NO_CHANGE",
        "modification_scope": [],
        "instructions": ["Re-run verification only."],
        "constraints": ["Commit the output."],
    }
    no_change_executors = []

    def forbidden_executor(*args, **kwargs):
        no_change_executors.append(args)
        raise AssertionError("Executor must not be invoked for eligible NO_CHANGE reuse")

    with pytest.raises(OperatorError, match="exit code 1"):
        run_repair(
            "RUN-101-001",
            executor="codex",
            repo=repo,
            repair=no_change_auth,
            native_runner=forbidden_executor,
            verification_runner=failing_verification,
        )

    assert no_change_executors == []
    assert len(v_calls) == 2
    second_failure = json.loads(
        (state.failures / "RUN-101-002.json").read_text(encoding="utf-8")
    )
    assert second_failure["phase"] == "VERIFICATION"
    assert second_failure["continuation_of"] == "RUN-101-001"
    assert second_failure["failed_head_sha"] == primary_failure["failed_head_sha"]
    assert (state.preverification / "RUN-101-002.json").is_file()

    # Step 3: Authorized CODE_FIX against second verification failure
    code_fix_auth = {
        "repair_id": "REPAIR-101-002",
        "failed_run_id": "RUN-101-002",
        "failed_head_sha": second_failure["failed_head_sha"],
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Apply authorized code fix."],
        "constraints": ["Commit the output."],
    }
    code_fix_runner = CodeFixRunner(repo)

    summary = run_repair(
        "RUN-101-002",
        executor="codex",
        repo=repo,
        repair=code_fix_auth,
        native_runner=code_fix_runner,
        verification_runner=passing_verification,
    )

    # AC5 assertions
    assert code_fix_runner.calls == 1
    assert len(v_calls) == 3
    assert summary.run_id == "RUN-101-003"
    assert summary.failed_head_sha == second_failure["failed_head_sha"]
    assert summary.head_sha != second_failure["failed_head_sha"]

    # Preserved continuation and root lineage
    repair_lineage = json.loads(
        (state.repairs / "RUN-101-003.json").read_text(encoding="utf-8")
    )
    assert repair_lineage["failed_run_id"] == "RUN-101-002"
    assert repair_lineage["failed_head_sha"] == second_failure["failed_head_sha"]
    assert repair_lineage["root_base_sha"] == primary_failure["base_sha"]

    # AC6 assertions: result processed by existing gates; no reusable package substituted
    result_record = json.loads(summary.result_path.read_text(encoding="utf-8"))
    assert result_record["result"]["head_sha"] == summary.head_sha
    assert result_record["result"]["changed_files"] == ["OUTPUT.txt"]
    assert git(repo, "rev-parse", "HEAD") == summary.head_sha
