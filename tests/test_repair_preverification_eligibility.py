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
        "modification_scope": (
            ["OUTPUT.txt"]
            if action in ("CODE_FIX", "CONTINUE_IMPLEMENTATION")
            else []
        ),
        "instructions": ["Apply the authorized correction."],
        "constraints": ["Commit the output."],
    }


class CodeFixRunner:
    def __init__(self, repo: Path, *, expected_action: str = "CODE_FIX") -> None:
        self.repo = repo
        self.expected_action = expected_action
        self.calls = 0

    def __call__(self, command, **kwargs):
        self.calls += 1
        execution = json.loads(
            kwargs["input"].decode("utf-8").split("REPAIR_INPUT:\n", 1)[1]
        )
        target_repo = Path(execution["run"]["workspace"])
        continuing = self.expected_action == "CONTINUE_IMPLEMENTATION"
        (target_repo / "OUTPUT.txt").write_text(
            "continued\n" if continuing else "corrected\n", encoding="utf-8"
        )
        git(target_repo, "add", "OUTPUT.txt")
        git(
            target_repo,
            "commit",
            "--quiet",
            "-m",
            "continue implementation" if continuing else "code fix",
        )
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
        assert execution["repair"]["action"] == self.expected_action
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")


class UnchangedContinuationRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls = 0

    def __call__(self, command, **kwargs):
        self.calls += 1
        head = git(self.repo, "rev-parse", "HEAD")
        payload = {
            "result": {
                "head_sha": head,
                "claims": [{
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "The continuation is complete.",
                    "evidence": [],
                }],
                "changed_files": [],
                "unresolved": [],
            },
            "evidence": [],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")


def test_finalize_candidate_reuses_complete_direct_sidecar(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    run_id, failure = predecessor(
        repo,
        claims=[{
            "id": "C1",
            "satisfies": ["AC1"],
            "claim": "The candidate is complete.",
            "evidence": [],
        }],
    )
    failure["phase"] = "EXECUTION"
    (runtime_paths(repo).failures / f"{run_id}.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )
    runner = UnchangedContinuationRunner(repo)

    summary = run_repair(
        run_id,
        executor="codex",
        repo=repo,
        repair=repair(failure, action="FINALIZE_CANDIDATE"),
        native_runner=runner,
        verification_runner=passing_verification,
    )

    assert runner.calls == 0
    assert summary.head_sha == failure["failed_head_sha"]


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


def persist_failure(repo: Path, failed_run_id: str, failure: dict) -> None:
    (runtime_paths(repo).failures / f"{failed_run_id}.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )


@pytest.mark.parametrize("phase", ["EXECUTION", "COMPLETION_GATE"])
def test_continue_implementation_dispatches_once_and_ignores_reuse_state(
    tmp_path: Path, phase: str
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    failure["phase"] = phase
    persist_failure(repo, failed_run_id, failure)
    (runtime_paths(repo).preverification / f"{failed_run_id}.json").write_bytes(
        b"{malformed reusable state"
    )
    runner = CodeFixRunner(repo, expected_action="CONTINUE_IMPLEMENTATION")
    verification_calls = 0

    def verify(command, **kwargs):
        nonlocal verification_calls
        verification_calls += 1
        return passing_verification(command, **kwargs)

    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair=repair(failure, action="CONTINUE_IMPLEMENTATION"),
        native_runner=runner,
        verification_runner=verify,
    )

    assert runner.calls == 1
    assert verification_calls == 1
    assert summary.head_sha != failure["failed_head_sha"]
    result = json.loads(summary.result_path.read_text(encoding="utf-8"))["result"]
    assert result["changed_files"] == ["OUTPUT.txt"]


def test_continue_implementation_requires_executor_scope_and_preverification_phase(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    failure["phase"] = "COMPLETION_GATE"
    persist_failure(repo, failed_run_id, failure)
    continuation = repair(failure, action="CONTINUE_IMPLEMENTATION")

    with pytest.raises(OperatorError, match="coding Executor is required"):
        run_repair(
            failed_run_id,
            executor=None,
            repo=repo,
            repair=continuation,
            native_runner=CodeFixRunner(
                repo, expected_action="CONTINUE_IMPLEMENTATION"
            ),
            verification_runner=passing_verification,
        )

    continuation["modification_scope"] = []
    with pytest.raises(OperatorError, match="modification scope is empty"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=continuation,
            native_runner=CodeFixRunner(
                repo, expected_action="CONTINUE_IMPLEMENTATION"
            ),
            verification_runner=passing_verification,
        )

    continuation["modification_scope"] = ["OUTPUT.txt"]
    failure["phase"] = "VERIFICATION"
    persist_failure(repo, failed_run_id, failure)
    with pytest.raises(OperatorError, match="pre-verification failure"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=continuation,
            native_runner=CodeFixRunner(
                repo, expected_action="CONTINUE_IMPLEMENTATION"
            ),
            verification_runner=passing_verification,
        )


def test_continue_implementation_unchanged_completion_fails_before_verification(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    failure["phase"] = "COMPLETION_GATE"
    persist_failure(repo, failed_run_id, failure)
    runner = UnchangedContinuationRunner(repo)
    verification_calls = 0

    def verify(command, **kwargs):
        nonlocal verification_calls
        verification_calls += 1
        return passing_verification(command, **kwargs)

    with pytest.raises(
        OperatorError, match="CONTINUE_IMPLEMENTATION REPAIR did not advance HEAD"
    ):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair(failure, action="CONTINUE_IMPLEMENTATION"),
            native_runner=runner,
            verification_runner=verify,
        )

    assert runner.calls == 1
    assert verification_calls == 0


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


def test_historical_continue_implementation_uses_exact_failed_subject(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    failed_run_id, failure = predecessor(repo)
    failure["phase"] = "COMPLETION_GATE"
    persist_failure(repo, failed_run_id, failure)
    state = runtime_paths(repo)
    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failure["failed_head_sha"],
        run_path=state.runs / f"{failed_run_id}.json",
        failure_path=state.failures / f"{failed_run_id}.json",
        publish_candidate=True,
    )

    (repo / "ADVANCE.txt").write_text("control advance\n", encoding="utf-8")
    git(repo, "add", "ADVANCE.txt")
    git(repo, "commit", "--quiet", "-m", "advance current control")
    control = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "branch", "--show-current"),
        git(repo, "status", "--porcelain"),
    )
    runner = CodeFixRunner(repo, expected_action="CONTINUE_IMPLEMENTATION")

    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair=repair(failure, action="CONTINUE_IMPLEMENTATION"),
        native_runner=runner,
        verification_runner=passing_verification,
    )

    assert runner.calls == 1
    assert summary.head_sha != failure["failed_head_sha"]
    assert (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "branch", "--show-current"),
        git(repo, "status", "--porcelain"),
    ) == control


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
        "directory",
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

    if defect == "directory":
        path.unlink()
        path.mkdir()
    elif defect == "conflicting_historical":
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


@pytest.mark.parametrize(
    "unusable_kind",
    ["directory", "unreadable"],
)
def test_code_fix_dispatches_when_predecessor_sidecar_unreadable_or_invalid_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unusable_kind: str
) -> None:
    """AC1: CODE_FIX REPAIR does not read, decode, compare, or depend on the local
    reusable sidecar before entering ordinary REPAIR dispatch, dispatching the selected
    Executor exactly once even when the sidecar is unreadable or has an invalid
    filesystem shape (e.g. directory). An actually eligible NO_CHANGE request encountering
    the same state fails closed without Executor dispatch.
    """
    repo = make_repo(tmp_path)
    full_claim = [{
        "id": "C1",
        "satisfies": ["AC1"],
        "claim": "The requested repair is complete.",
        "evidence": [],
    }]
    failed_run_id, failure = predecessor(repo, claims=full_claim)
    state = runtime_paths(repo)
    path = state.preverification / f"{failed_run_id}.json"

    if unusable_kind == "directory":
        path.unlink()
        path.mkdir()
    elif unusable_kind == "unreadable":
        original_read_bytes = Path.read_bytes

        def failing_read(self):
            if self == path:
                raise OSError("Permission denied")
            return original_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", failing_read)

    # Preserve fail-closed behavior for an actually eligible NO_CHANGE reuse request
    # encountering the same unusable state; no Executor dispatch occurs.
    no_change_calls = []
    with pytest.raises(OperatorError, match="pre-verification candidate could not be read"):
        run_repair(
            failed_run_id,
            executor="codex",
            repo=repo,
            repair=repair(failure, action="NO_CHANGE"),
            native_runner=lambda *args, **kwargs: no_change_calls.append("executor"),
            verification_runner=lambda *args, **kwargs: no_change_calls.append("verification"),
        )
    assert no_change_calls == []
    assert not (state.runs / "RUN-101-001.json").exists()

    # Otherwise valid CODE_FIX REPAIR dispatches selected Executor exactly once
    code_fix_runner = CodeFixRunner(repo)
    v_calls = []

    def verification_runner(command, **kwargs):
        v_calls.append("verification")
        return subprocess.CompletedProcess(command, 0, b"clean\n", b"")

    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair=repair(failure, action="CODE_FIX"),
        native_runner=code_fix_runner,
        verification_runner=verification_runner,
    )

    assert code_fix_runner.calls == 1
    assert len(v_calls) == 1
    assert summary.run_id == "RUN-101-001"
    assert summary.failed_head_sha == failure["failed_head_sha"]
    assert summary.head_sha != failure["failed_head_sha"]

    # Preserved exact failed-head lineage
    repair_lineage = json.loads(
        (state.repairs / "RUN-101-001.json").read_text(encoding="utf-8")
    )
    assert repair_lineage["failed_run_id"] == failed_run_id
    assert repair_lineage["failed_head_sha"] == failure["failed_head_sha"]
    assert repair_lineage["root_base_sha"] == failure["base_sha"]

    # Runtime completion and verification
    result_record = json.loads(summary.result_path.read_text(encoding="utf-8"))
    assert result_record["result"]["head_sha"] == summary.head_sha
    assert result_record["result"]["changed_files"] == ["OUTPUT.txt"]
    assert git(repo, "rev-parse", "HEAD") == summary.head_sha


@pytest.mark.parametrize("unusable_kind", ["directory", "unreadable"])
def test_historical_code_fix_with_unusable_local_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unusable_kind: str
) -> None:
    """Historical CODE_FIX does not read or depend on local reusable sidecar even
    when it is unreadable or has an invalid filesystem shape, dispatching the selected
    Executor exactly once and preserving lineage.
    """
    repo = make_repo(tmp_path)
    full_claim = [{
        "id": "C1",
        "satisfies": ["AC1"],
        "claim": "The requested repair is complete.",
        "evidence": [],
    }]
    failed_run_id, failure = predecessor(repo, claims=full_claim)
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

    path = state.preverification / f"{failed_run_id}.json"
    if unusable_kind == "directory":
        path.unlink()
        path.mkdir()
    elif unusable_kind == "unreadable":
        original_read_bytes = Path.read_bytes

        def failing_read(self):
            if self == path:
                raise OSError("Permission denied")
            return original_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", failing_read)

    code_fix_runner = CodeFixRunner(repo)
    summary = run_repair(
        failed_run_id,
        executor="codex",
        repo=repo,
        repair=repair(failure, action="CODE_FIX"),
        native_runner=code_fix_runner,
        verification_runner=passing_verification,
    )

    assert code_fix_runner.calls == 1
    assert summary.run_id == "RUN-101-001"
    assert summary.failed_head_sha == failure["failed_head_sha"]
    assert summary.head_sha != failure["failed_head_sha"]

MULTI_TASK_SOURCE = """
task_id: TASK-102
revision: 1
goal: Exercise REPAIR pre-verification eligibility with distinct changed-files authorities.
problem: Preserve NO_CHANGE reuse after CODE_FIX failure.
assumptions: []
scope:
  inspect: []
  modify: [DOC.txt, OUTPUT.txt]
non_goals: []
constraints:
  hard: [Commit the output.]
acceptance:
  - id: AC1
    condition: The requested repair is complete.
verification:
  required: [git status --porcelain]
"""


def make_multi_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "Eligibility Test")
    git(repo, "config", "user.email", "eligibility@example.invalid")
    git(repo, "branch", "-M", "main")
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK-102.yaml").write_text(MULTI_TASK_SOURCE, encoding="utf-8")
    (repo / "README.md").write_text("# multi eligibility test\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "baseline")
    upstream = root / "upstream.git"
    subprocess.run(("git", "init", "--bare", "--quiet", str(upstream)), check=True)
    git(repo, "remote", "add", "origin", str(upstream))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "main")
    return repo


class MultiPrimaryRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls = 0

    def __call__(self, command, **kwargs):
        self.calls += 1
        (self.repo / "DOC.txt").write_text("initial doc\n", encoding="utf-8")
        git(self.repo, "add", "DOC.txt")
        git(self.repo, "commit", "--quiet", "-m", "initial primary doc")
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
                "changed_files": ["DOC.txt"],
                "unresolved": [],
            },
            "evidence": [],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")


class MultiCodeFixRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls = 0

    def __call__(self, command, **kwargs):
        self.calls += 1
        execution = json.loads(
            kwargs["input"].decode("utf-8").split("REPAIR_INPUT:\n", 1)[1]
        )
        target_repo = Path(execution["run"]["workspace"])
        (target_repo / "OUTPUT.txt").write_text("corrected output\n", encoding="utf-8")
        git(target_repo, "add", "OUTPUT.txt")
        git(target_repo, "commit", "--quiet", "-m", "code fix output")
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


def _setup_multi_continuation_topology(
    tmp_path: Path,
) -> tuple[Path, list[str]]:
    """Helper to set up Steps 1 to 3 of the AC5 continuation topology."""
    repo = make_multi_repo(tmp_path)
    v_calls = []

    def failing_verification(command, **kwargs):
        v_calls.append("fail")
        return subprocess.CompletedProcess(
            command, 1, b"", b"canonical verification failed\n"
        )

    # Step 1: PRIMARY fails at canonical verification
    primary_runner = MultiPrimaryRunner(repo)
    with pytest.raises(OperatorError, match="exit code 1"):
        run_task(
            "TASK-102",
            executor="codex",
            repo=repo,
            native_runner=primary_runner,
            verification_runner=failing_verification,
        )

    state = runtime_paths(repo)
    primary_failure = json.loads(
        (state.failures / "RUN-102-001.json").read_text(encoding="utf-8")
    )

    # Step 2: NO_CHANGE fails at canonical verification
    no_change_auth_1 = {
        "repair_id": "REPAIR-102-001",
        "failed_run_id": "RUN-102-001",
        "failed_head_sha": primary_failure["failed_head_sha"],
        "task": {"id": "TASK-102", "revision": 1},
        "action": "NO_CHANGE",
        "modification_scope": [],
        "instructions": ["Re-run verification only."],
        "constraints": ["Commit the output."],
    }

    def forbidden_executor(*args, **kwargs):
        raise AssertionError("Executor must not be invoked for eligible NO_CHANGE reuse")

    with pytest.raises(OperatorError, match="exit code 1"):
        run_repair(
            "RUN-102-001",
            executor="codex",
            repo=repo,
            repair=no_change_auth_1,
            native_runner=forbidden_executor,
            verification_runner=failing_verification,
        )

    second_failure = json.loads(
        (state.failures / "RUN-102-002.json").read_text(encoding="utf-8")
    )

    # Step 3: CODE_FIX dispatches once and fails during canonical verification
    code_fix_auth = {
        "repair_id": "REPAIR-102-002",
        "failed_run_id": "RUN-102-002",
        "failed_head_sha": second_failure["failed_head_sha"],
        "task": {"id": "TASK-102", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Apply authorized code fix."],
        "constraints": ["Commit the output."],
    }
    code_fix_runner = MultiCodeFixRunner(repo)

    with pytest.raises(OperatorError, match="exit code 1"):
        run_repair(
            "RUN-102-002",
            executor="codex",
            repo=repo,
            repair=code_fix_auth,
            native_runner=code_fix_runner,
            verification_runner=failing_verification,
        )

    assert code_fix_runner.calls == 1
    return repo, v_calls


def test_continuation_topology_primary_fail_no_change_fail_code_fix_fail_no_change_succeeds(
    tmp_path: Path,
) -> None:
    """AC1, AC2, AC3, AC5, AC6, AC7: Full deterministic continuation topology:
    PRIMARY canonical verification failure -> eligible NO_CHANGE continuation whose canonical
    verification fails -> authorized CODE_FIX REPAIR that dispatches its selected Executor
    exactly once and itself fails only during canonical verification -> final explicitly
    authorized NO_CHANGE continuation that is admissible when its preserved pre-verification
    candidate is valid despite correction-relative versus root-relative changed-files inequality.
    """
    repo, v_calls = _setup_multi_continuation_topology(tmp_path)
    state = runtime_paths(repo)

    primary_failure = json.loads(
        (state.failures / "RUN-102-001.json").read_text(encoding="utf-8")
    )
    second_failure = json.loads(
        (state.failures / "RUN-102-002.json").read_text(encoding="utf-8")
    )
    third_failure = json.loads(
        (state.failures / "RUN-102-003.json").read_text(encoding="utf-8")
    )

    # Verify Step 3 properties
    assert third_failure["phase"] == "VERIFICATION"
    assert third_failure["continuation_of"] == "RUN-102-002"
    assert third_failure["failed_head_sha"] != second_failure["failed_head_sha"]
    assert third_failure["base_sha"] == second_failure["failed_head_sha"]
    assert third_failure["candidate"]["repairable"] is True
    assert third_failure["candidate"]["transportable"] is True
    # AC3: FAILURE candidate.changed_files retains narrow per-RUN correction-relative authority
    assert third_failure["candidate"]["changed_files"] == ["OUTPUT.txt"]

    third_sidecar = json.loads(
        (state.preverification / "RUN-102-003.json").read_text(encoding="utf-8")
    )
    # AC1: Preserved pre-verification ResultPackage has full root_base_sha-to-subject authority
    assert third_sidecar["package"]["result"]["changed_files"] == ["DOC.txt", "OUTPUT.txt"]
    # Distinct changed-files authorities: inequality between correction delta and root delta
    assert (
        third_failure["candidate"]["changed_files"]
        != third_sidecar["package"]["result"]["changed_files"]
    )

    # Step 4: Final explicitly authorized NO_CHANGE continuation
    no_change_auth_2 = {
        "repair_id": "REPAIR-102-003",
        "failed_run_id": "RUN-102-003",
        "failed_head_sha": third_failure["failed_head_sha"],
        "task": {"id": "TASK-102", "revision": 1},
        "action": "NO_CHANGE",
        "modification_scope": [],
        "instructions": ["Re-run canonical verification only."],
        "constraints": ["Commit the output."],
    }
    final_executors = []

    def final_forbidden_executor(*args, **kwargs):
        final_executors.append(args)
        raise AssertionError(
            "Executor must not be invoked for final eligible NO_CHANGE reuse"
        )

    def passing_verification(command, **kwargs):
        v_calls.append("pass")
        return subprocess.CompletedProcess(command, 0, b"clean\n", b"")

    summary = run_repair(
        "RUN-102-003",
        executor="codex",
        repo=repo,
        repair=no_change_auth_2,
        native_runner=final_forbidden_executor,
        verification_runner=passing_verification,
    )

    # AC7: Final eligible NO_CHANGE continuation invokes zero native Executors
    assert final_executors == []
    # AC7: Runs the canonical TASK verification list exactly once
    assert v_calls[-1] == "pass"
    assert len(v_calls) == 4
    assert summary.run_id == "RUN-102-004"
    assert summary.failed_head_sha == third_failure["failed_head_sha"]
    assert summary.head_sha == third_failure["failed_head_sha"]

    # AC2: Canonical REPAIR Result.changed_files remains complete stable original TASK delta
    final_result_record = json.loads(summary.result_path.read_text(encoding="utf-8"))
    assert final_result_record["result"]["head_sha"] == summary.head_sha
    assert final_result_record["result"]["changed_files"] == ["DOC.txt", "OUTPUT.txt"]
    assert git(repo, "rev-parse", "HEAD") == summary.head_sha

    # AC3: Predecessor FAILURE candidate.changed_files retains narrow delta without expansion
    predecessor_record = json.loads(
        (state.failures / "RUN-102-003.json").read_text(encoding="utf-8")
    )
    assert predecessor_record["candidate"]["changed_files"] == ["OUTPUT.txt"]

    # AC6: Preserves exact latest failed_head_sha, continuation_of, original root_base_sha,
    # TASK id/revision, historical/correction lineage, and subject identity at every step
    r1 = json.loads((state.runs / "RUN-102-001.json").read_text(encoding="utf-8"))
    assert r1["base_sha"] == primary_failure["base_sha"]
    assert r1["task"] == {"id": "TASK-102", "revision": 1}

    rep2 = json.loads((state.repairs / "RUN-102-002.json").read_text(encoding="utf-8"))
    assert rep2["failed_run_id"] == "RUN-102-001"
    assert rep2["failed_head_sha"] == primary_failure["failed_head_sha"]
    assert rep2["root_base_sha"] == primary_failure["base_sha"]
    assert rep2["task"]["task_id"] == "TASK-102"
    assert rep2["task"]["revision"] == 1

    rep3 = json.loads((state.repairs / "RUN-102-003.json").read_text(encoding="utf-8"))
    assert rep3["failed_run_id"] == "RUN-102-002"
    assert rep3["failed_head_sha"] == second_failure["failed_head_sha"]
    assert rep3["root_base_sha"] == primary_failure["base_sha"]
    assert rep3["task"]["task_id"] == "TASK-102"
    assert rep3["task"]["revision"] == 1

    rep4 = json.loads((state.repairs / "RUN-102-004.json").read_text(encoding="utf-8"))
    assert rep4["failed_run_id"] == "RUN-102-003"
    assert rep4["failed_head_sha"] == third_failure["failed_head_sha"]
    assert rep4["root_base_sha"] == primary_failure["base_sha"]
    assert rep4["task"]["task_id"] == "TASK-102"
    assert rep4["task"]["revision"] == 1


@pytest.mark.parametrize(
    "defect",
    [
        "narrow_files_only",
        "outside_task_scope",
        "wrong_subject",
        "wrong_task",
        "incomplete",
        "coverage",
        "conflicting_historical",
    ],
)
def test_no_change_after_code_fix_fails_closed_on_invalid_reusable_package(
    tmp_path: Path, defect: str
) -> None:
    """AC4: Genuine NO_CHANGE reuse fails closed before canonical verification when the
    preserved pre-verification package itself has an invalid full-TASK changed-files set,
    wrong subject/head, wrong TASK/revision, malformed or conflicting local/transported state,
    incomplete ResultPackage, or incomplete TASK acceptance coverage; no Executor fallback occurs.
    """
    repo, _ = _setup_multi_continuation_topology(tmp_path)
    state = runtime_paths(repo)
    sidecar_path = state.preverification / "RUN-102-003.json"
    third_failure = json.loads(
        (state.failures / "RUN-102-003.json").read_text(encoding="utf-8")
    )

    if defect == "conflicting_historical":
        transport_failure(
            repo,
            run_id="RUN-102-003",
            head_sha=third_failure["failed_head_sha"],
            run_path=state.runs / "RUN-102-003.json",
            failure_path=state.failures / "RUN-102-003.json",
            publish_candidate=True,
            preverification_path=sidecar_path,
        )
        (repo / "ADVANCE.txt").write_text("advance\n", encoding="utf-8")
        git(repo, "add", "ADVANCE.txt")
        git(repo, "commit", "--quiet", "-m", "advance main")
        sidecar_path.write_bytes(b'{"conflicting": "different_sidecar"}')
    else:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if defect == "narrow_files_only":
            # Invalid full-TASK changed files: only the narrow correction delta, not full delta
            data["package"]["result"]["changed_files"] = ["OUTPUT.txt"]
        elif defect == "outside_task_scope":
            data["package"]["result"]["changed_files"] = ["DOC.txt", "OUTPUT.txt", "FOREIGN.txt"]
        elif defect == "wrong_subject":
            data["subject_sha"] = "0" * 40
        elif defect == "wrong_task":
            data["task"]["revision"] = 2
        elif defect == "incomplete":
            data["package"]["result"]["unresolved"] = ["unresolved issue"]
        elif defect == "coverage":
            data["package"]["result"]["claims"] = []
        sidecar_path.write_text(json.dumps(data), encoding="utf-8")

    no_change_auth = {
        "repair_id": "REPAIR-102-003",
        "failed_run_id": "RUN-102-003",
        "failed_head_sha": third_failure["failed_head_sha"],
        "task": {"id": "TASK-102", "revision": 1},
        "action": "NO_CHANGE",
        "modification_scope": [],
        "instructions": ["Re-run canonical verification only."],
        "constraints": ["Commit the output."],
    }
    calls = []
    with pytest.raises(OperatorError):
        run_repair(
            "RUN-102-003",
            executor="codex",
            repo=repo,
            repair=no_change_auth,
            native_runner=lambda *args, **kwargs: calls.append("executor"),
            verification_runner=lambda *args, **kwargs: calls.append("verification"),
        )

    # Fails closed: zero native executors, zero verification runs, no RUN-102-004 created
    assert calls == []
    assert not (state.runs / "RUN-102-004.json").exists()


def test_eligible_reusable_repair_package_direct_check_with_distinct_authorities(
    tmp_path: Path,
) -> None:
    """AC1: Direct validation of _eligible_reusable_repair_package proves an eligible
    NO_CHANGE continuation after CODE_FIX failure is admitted when package.result.changed_files
    matches full root-to-subject Git authority while candidate.changed_files matches
    narrow per-RUN Git authority.
    """
    repo, _ = _setup_multi_continuation_topology(tmp_path)
    state = runtime_paths(repo)
    sidecar_content = (state.preverification / "RUN-102-003.json").read_bytes()
    third_failure = json.loads(
        (state.failures / "RUN-102-003.json").read_text(encoding="utf-8")
    )
    primary_failure = json.loads(
        (state.failures / "RUN-102-001.json").read_text(encoding="utf-8")
    )
    task = load_task(repo, "TASK-102")

    # Valid pre-verification package returned despite inequality between authorities
    package = _eligible_reusable_repair_package(
        sidecar_content,
        task=task,
        failed_run_id="RUN-102-003",
        failure=third_failure,
        action="NO_CHANGE",
        scope=[],
        repo=repo,
        root_base_sha=primary_failure["base_sha"],
    )
    assert package is not None
    assert list(package.result.changed_files) == ["DOC.txt", "OUTPUT.txt"]
    assert third_failure["candidate"]["changed_files"] == ["OUTPUT.txt"]

    # Fails closed if package.result.changed_files contains only narrow delta
    tampered_data = json.loads(sidecar_content.decode("utf-8"))
    tampered_data["package"]["result"]["changed_files"] = ["OUTPUT.txt"]
    with pytest.raises(OperatorError, match="pre-verification candidate changed-files mismatch"):
        _eligible_reusable_repair_package(
            json.dumps(tampered_data).encode(),
            task=task,
            failed_run_id="RUN-102-003",
            failure=third_failure,
            action="NO_CHANGE",
            scope=[],
            repo=repo,
            root_base_sha=primary_failure["base_sha"],
        )




@pytest.mark.parametrize("defect", ["valid", "absent", "wrong_task", "unresolved", "coverage", "duplicate"])
def test_h2_projection_matches_authoritative_no_change_reuse(defect, tmp_path):
    from aios_renew.correction_preflight import repair_strategy_facts

    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    (repo / "OUTPUT.txt").write_text("committed candidate\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "candidate")
    head = git(repo, "rev-parse", "HEAD")
    task = load_task(repo, "TASK-101")
    failure = {"kind": "FAILURE", "run_id": "RUN-101-001", "base_sha": base,
               "failed_head_sha": head, "phase": "VERIFICATION",
               "candidate": {"dirty": False, "transportable": True, "repairable": True,
                             "descends_from_base": True, "changed_files": ["OUTPUT.txt"],
                             "outside_task_scope": []}}
    sidecar = {"kind": "PRE_VERIFICATION_CANDIDATE", "run_id": "RUN-101-001",
               "task": {"id": "TASK-101", "revision": 1}, "subject_sha": head,
               "package": {"result": {"head_sha": head, "claims": [{"id": "C1",
                   "satisfies": ["AC1"], "claim": "Committed implementation", "evidence": []}],
                   "changed_files": ["OUTPUT.txt"], "unresolved": []}, "evidence": []}}
    if defect == "wrong_task":
        sidecar["task"]["revision"] = 2
    elif defect == "unresolved":
        sidecar["package"]["result"]["unresolved"] = ["Remaining implementation"]
    elif defect == "coverage":
        sidecar["package"]["result"]["claims"] = []
    content = json.dumps(sidecar).encode()
    if defect == "absent":
        content = None
    elif defect == "duplicate":
        content = content.replace(b'{"kind":', b'{"kind":"CONFLICT","kind":', 1)
    try:
        reused = _eligible_reusable_repair_package(content, task=task, failed_run_id="RUN-101-001",
            failure=failure, action="NO_CHANGE", scope=[], repo=repo, root_base_sha=base)
        eligible = reused is not None
    except OperatorError:
        eligible = False
    facts = repair_strategy_facts(task=task, failure=failure, state={
        "preverification_hex": None if content is None else content.hex(),
        "root_base_sha": base, "result_base_sha": base,
        "committed_deltas": {"candidate": ["OUTPUT.txt"], "result": ["OUTPUT.txt"]}})
    assert facts["action_structural_eligibility"]["NO_CHANGE"] == ("ELIGIBLE" if eligible else "INELIGIBLE")
    assert eligible == (defect == "valid")
    # Mutation actions never reuse; FINALIZE cannot reuse a VERIFICATION target.
    for action in ("CODE_FIX", "CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE"):
        assert _eligible_reusable_repair_package(content, task=task, failed_run_id="RUN-101-001",
            failure=failure, action=action, scope=[], repo=repo, root_base_sha=base) is None


def same_head_finalize_lineage(tmp_path: Path) -> tuple[Path, dict]:
    """Complete structure followed by two claimless same-head gate failures."""
    from aios_renew.operator import _executor_task_data

    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    base = git(repo, "rev-parse", "HEAD")
    (repo / "OUTPUT.txt").write_text("complete candidate\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "candidate")
    head = git(repo, "rev-parse", "HEAD")
    task_data = _executor_task_data(load_task(repo, "TASK-101"))
    previous = None
    for number in (1, 2, 3):
        run_id = f"RUN-101-{number:03d}"
        run = {
            "run_id": run_id, "task": {"id": "TASK-101", "revision": 1},
            "executor": "codex", "base_sha": base if previous is None else head,
            "workspace": str(repo), "head_sha": None, "status": "ACTIVE",
        }
        failure = {
            "kind": "FAILURE", "run_id": run_id, "task": run["task"],
            "executor": "codex", "base_sha": run["base_sha"],
            "failed_head_sha": head,
            "phase": "VERIFICATION" if previous is None else "COMPLETION_GATE",
            "candidate": {
                "transportable": True, "repairable": True, "dirty": False,
                "descends_from_base": True, "outside_task_scope": [],
                "changed_files": ["OUTPUT.txt"] if previous is None else [],
            },
        }
        if previous is not None:
            failure["continuation_of"] = previous["run_id"]
            execution = {
                "failed_run_id": previous["run_id"], "failed_head_sha": head,
                "root_base_sha": base, "result_base_sha": base,
                "task": task_data, "failure": previous, "run": run,
                "repair": repair(previous, action=(
                    "CODE_FIX" if previous["phase"] == "VERIFICATION" else "FINALIZE_CANDIDATE"
                )),
            }
            (state.repairs / f"{run_id}.json").write_text(json.dumps(execution), encoding="utf-8")
        (state.runs / f"{run_id}.json").write_text(json.dumps(run), encoding="utf-8")
        persist_failure(repo, run_id, failure)
        previous = failure
    sidecar = {
        "kind": "PRE_VERIFICATION_CANDIDATE", "run_id": "RUN-101-001",
        "task": {"id": "TASK-101", "revision": 1}, "subject_sha": head,
        "package": {"result": {
            "head_sha": head, "changed_files": ["OUTPUT.txt"], "unresolved": [],
            "claims": [{"id": "PRESERVED", "satisfies": ["AC1"],
                        "claim": "The candidate is complete.", "evidence": []}],
        }, "evidence": []},
    }
    (state.preverification / "RUN-101-001.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return repo, failure


def publish_finalize_lineage(repo: Path, *, include_source: bool = True) -> None:
    state = runtime_paths(repo)
    for number in (1, 2, 3):
        run_id = f"RUN-101-{number:03d}"
        failure = json.loads((state.failures / f"{run_id}.json").read_text(encoding="utf-8"))
        transport_failure(
            repo, run_id=run_id, head_sha=failure["failed_head_sha"],
            run_path=state.runs / f"{run_id}.json",
            failure_path=state.failures / f"{run_id}.json", publish_candidate=True,
            lineage_path=None if number == 1 else state.repairs / f"{run_id}.json",
            preverification_path=(state.preverification / f"{run_id}.json")
            if number == 1 and include_source else None,
        )


@pytest.mark.parametrize("historical", [False, True])
def test_finalize_reuses_nearest_canonical_same_head_structure(tmp_path, historical):
    repo, failure = same_head_finalize_lineage(tmp_path)
    publish_finalize_lineage(repo)
    if historical:
        # A fresh control checkout has no old runtime cache or workspace.
        clone = tmp_path / "fresh"
        subprocess.run(("git", "clone", "--quiet", "--branch", "main",
                        str(tmp_path / "upstream.git"), str(clone)), check=True)
        git(clone, "config", "user.name", "Fresh Runtime")
        git(clone, "config", "user.email", "fresh@example.invalid")
        repo = clone
    control_head = git(repo, "rev-parse", "HEAD")
    calls = []

    def forbidden_executor(*args, **kwargs):
        calls.append("executor")
        raise AssertionError("eligible FINALIZE must elide the Executor")

    def verify(command, **kwargs):
        calls.append(command)
        assert tuple(command) == ("git", "status", "--porcelain")
        assert git(Path(kwargs["cwd"]), "rev-parse", "HEAD") == failure["failed_head_sha"]
        return subprocess.CompletedProcess(command, 0, b"fresh verification\n", b"")

    summary = run_repair(
        failure["run_id"], executor=None, repo=repo,
        repair=repair(failure, action="FINALIZE_CANDIDATE"),
        native_runner=forbidden_executor, verification_runner=verify,
    )
    state = runtime_paths(repo)
    record = json.loads(summary.result_path.read_text(encoding="utf-8"))
    structural = json.loads((state.preverification / f"{summary.run_id}.json").read_text(encoding="utf-8"))
    continuation = json.loads((state.repairs / f"{summary.run_id}.json").read_text(encoding="utf-8"))
    assert len(calls) == 1 and "executor" not in calls
    assert summary.run_id == "RUN-101-004"
    assert summary.head_sha == failure["failed_head_sha"]
    assert git(repo, "rev-parse", "HEAD") == control_head
    assert continuation["failed_run_id"] == "RUN-101-003"
    assert continuation["failure"] == failure
    assert continuation["repair"]["action"] == "FINALIZE_CANDIDATE"
    assert continuation["root_base_sha"] == continuation["result_base_sha"]
    assert structural["package"]["evidence"] == []
    assert structural["package"]["result"]["claims"][0]["id"] == "PRESERVED"
    assert structural["package"]["result"]["claims"][0]["evidence"] == []
    assert record["result"]["changed_files"] == ["OUTPUT.txt"]
    assert record["evidence"]
    assert {item["run_id"] for item in record["evidence"]} == {summary.run_id}
    assert {item["subject_sha"] for item in record["evidence"]} == {summary.head_sha}


@pytest.mark.parametrize("defect", [
    "malformed", "duplicate", "task", "revision", "boolean_revision", "run", "subject", "head",
    "coverage", "unresolved", "files", "scope", "evidence", "claim_evidence",
    "lineage", "canonical_conflict", "canonical_missing",
])
def test_finalize_present_ancestor_fails_closed_before_execution(tmp_path, defect):
    repo, failure = same_head_finalize_lineage(tmp_path)
    state = runtime_paths(repo)
    path = state.preverification / "RUN-101-001.json"
    if defect in ("canonical_conflict", "canonical_missing"):
        publish_finalize_lineage(repo, include_source=defect != "canonical_missing")
        if defect == "canonical_conflict":
            path.write_bytes(b"{conflicting local state")
    elif defect == "malformed":
        path.write_bytes(b"{malformed")
    elif defect == "duplicate":
        path.write_bytes(path.read_bytes().replace(b'{"kind":', b'{"kind":"CONFLICT","kind":', 1))
    elif defect == "lineage":
        lineage_path = state.repairs / "RUN-101-002.json"
        data = json.loads(lineage_path.read_text(encoding="utf-8"))
        data["failure"]["failed_head_sha"] = "0" * 40
        lineage_path.write_text(json.dumps(data), encoding="utf-8")
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = data["package"]["result"]
        if defect == "task":
            data["task"]["id"] = "TASK-999"
        elif defect == "revision":
            data["task"]["revision"] = 2
        elif defect == "boolean_revision":
            data["task"]["revision"] = True
        elif defect == "run":
            data["run_id"] = "RUN-101-999"
        elif defect == "subject":
            data["subject_sha"] = "0" * 40
        elif defect == "head":
            result["head_sha"] = "0" * 40
        elif defect == "coverage":
            result["claims"] = []
        elif defect == "unresolved":
            result["unresolved"] = ["remaining implementation"]
        elif defect == "files":
            result["changed_files"] = []
        elif defect == "scope":
            result["changed_files"] = ["OUTPUT.txt", "FOREIGN.txt"]
        elif defect == "evidence":
            data["package"]["evidence"] = [{"evidence_id": "EXECUTOR-EVIDENCE"}]
        elif defect == "claim_evidence":
            result["claims"][0]["evidence"] = ["OLD-EVIDENCE"]
        path.write_text(json.dumps(data), encoding="utf-8")
    calls = []
    with pytest.raises(OperatorError):
        run_repair(
            failure["run_id"], executor="codex", repo=repo,
            repair=repair(failure, action="FINALIZE_CANDIDATE"),
            native_runner=lambda *args, **kwargs: calls.append("executor"),
            verification_runner=lambda *args, **kwargs: calls.append("verification"),
        )
    assert calls == []
    assert not (state.runs / "RUN-101-004.json").exists()


def test_finalize_nearest_present_package_cannot_be_skipped(tmp_path):
    repo, failure = same_head_finalize_lineage(tmp_path)
    state = runtime_paths(repo)
    (state.preverification / "RUN-101-002.json").write_bytes(b"{malformed nearer package")
    calls = []
    with pytest.raises(OperatorError, match="pre-verification candidate"):
        run_repair(
            failure["run_id"], executor="codex", repo=repo,
            repair=repair(failure, action="FINALIZE_CANDIDATE"),
            native_runner=lambda *args, **kwargs: calls.append("executor"),
            verification_runner=lambda *args, **kwargs: calls.append("verification"),
        )
    assert calls == []


def test_finalize_selects_nearest_valid_structure(tmp_path):
    repo, failure = same_head_finalize_lineage(tmp_path)
    state = runtime_paths(repo)
    old_path = state.preverification / "RUN-101-001.json"
    nearer = json.loads(old_path.read_text(encoding="utf-8"))
    nearer["run_id"] = "RUN-101-002"
    nearer["package"]["result"]["claims"][0]["id"] = "NEAREST"
    (state.preverification / "RUN-101-002.json").write_text(json.dumps(nearer), encoding="utf-8")
    old_path.write_bytes(b"{older state must not be read")
    summary = run_repair(
        failure["run_id"], executor=None, repo=repo,
        repair=repair(failure, action="FINALIZE_CANDIDATE"),
        native_runner=lambda *args, **kwargs: pytest.fail("Executor invoked"),
        verification_runner=passing_verification,
    )
    result = json.loads(summary.result_path.read_text(encoding="utf-8"))["result"]
    assert result["claims"][0]["id"] == "NEAREST"


@pytest.mark.parametrize("action", ["CODE_FIX", "CONTINUE_IMPLEMENTATION", "NO_CHANGE"])
def test_other_actions_never_read_preserved_ancestor_structure(tmp_path, action):
    repo, failure = same_head_finalize_lineage(tmp_path)
    state = runtime_paths(repo)
    (state.preverification / "RUN-101-001.json").write_bytes(b"{unusable ancestor structure")
    if action == "NO_CHANGE":
        failure["phase"] = "VERIFICATION"
        persist_failure(repo, failure["run_id"], failure)
        runner = UnchangedContinuationRunner(repo)
    else:
        runner = CodeFixRunner(repo, expected_action=action)
    summary = run_repair(
        failure["run_id"], executor="codex", repo=repo,
        repair=repair(failure, action=action), native_runner=runner,
        verification_runner=passing_verification,
    )
    assert runner.calls == 1
    assert summary.run_id == "RUN-101-004"


def test_finalize_stops_at_candidate_mutation_and_uses_executor_fallback(tmp_path):
    repo, failure = same_head_finalize_lineage(tmp_path)
    state = runtime_paths(repo)
    # RUN-002 changed the candidate. RUN-003 is a same-head gate failure.
    (repo / "OUTPUT.txt").write_text("mutated candidate\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "candidate mutation")
    mutated = git(repo, "rev-parse", "HEAD")
    second_path = state.failures / "RUN-101-002.json"
    second = json.loads(second_path.read_text(encoding="utf-8"))
    second["failed_head_sha"] = mutated
    second["candidate"]["changed_files"] = ["OUTPUT.txt"]
    second_path.write_text(json.dumps(second), encoding="utf-8")
    third_run_path = state.runs / "RUN-101-003.json"
    third_run = json.loads(third_run_path.read_text(encoding="utf-8"))
    third_run["base_sha"] = mutated
    third_run_path.write_text(json.dumps(third_run), encoding="utf-8")
    failure["base_sha"] = failure["failed_head_sha"] = mutated
    persist_failure(repo, failure["run_id"], failure)
    third_lineage_path = state.repairs / "RUN-101-003.json"
    third = json.loads(third_lineage_path.read_text(encoding="utf-8"))
    third.update(run=third_run, failure=second, failed_head_sha=mutated)
    third["repair"]["failed_head_sha"] = mutated
    third_lineage_path.write_text(json.dumps(third), encoding="utf-8")
    # Crossing the boundary would incorrectly reject this older malformed source.
    (state.preverification / "RUN-101-001.json").write_bytes(b"{old malformed package")
    runner = UnchangedContinuationRunner(repo)
    summary = run_repair(
        failure["run_id"], executor="codex", repo=repo,
        repair=repair(failure, action="FINALIZE_CANDIDATE"),
        native_runner=runner, verification_runner=passing_verification,
    )
    assert runner.calls == 1
    assert summary.head_sha == mutated == git(repo, "rev-parse", "HEAD")
