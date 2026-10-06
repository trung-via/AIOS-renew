from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import aios_renew.operator as operator_module

from aios_renew.repair_dispatch import (
    RepairDispatchError,
    RepairInvocation,
    bind_repair_run,
    execute_repair_dispatch,
    existing_repair_profile,
    reject_existing_selector_collision,
    replay_existing_repair_dispatch,
)
from aios_renew.execution_profile import (
    ResolvedExecutionProfile,
    parse_execution_profile_policy,
    persist_execution_profile,
)


@pytest.fixture(autouse=True)
def external_repository_policy(tmp_path: Path) -> None:
    policy_path = tmp_path / ".ai" / "executor-profiles.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        """format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1
executors:
  codex:
    default_model: test/codex-future-v1
    default_reasoning_effort: low
    supported_reasoning_efforts: [low, repo_only]
  antigravity:
    default_model: test/antigravity-future-v1
    default_reasoning_effort: low
    supported_reasoning_efforts: [low, repo_only]
""",
        encoding="utf-8",
    )


def profile_for(run_id: str, executor: str = "codex") -> ResolvedExecutionProfile:
    return ResolvedExecutionProfile(
        run_id=run_id,
        executor=executor,
        model=f"test/{executor}-future-v1",
        reasoning_effort="low",
        model_source="EXPLICIT",
        effort_source="EXPLICIT",
    )


def test_external_policy_governs_existing_repair_paths(tmp_path: Path) -> None:
    state = tmp_path / ".git" / "aios"
    repo = tmp_path
    policy_path = repo / ".ai" / "executor-profiles.yaml"
    supported = policy_path.read_text(encoding="utf-8")
    external_only = replace(profile_for("AUTHORIZATION"), reasoning_effort="repo_only")
    called: list[str] = []
    request = dict(
        state_root=state, repo_root=repo, repair_dispatch_id="external-repair",
        failed_run_id="RUN-111-001", repair_sha="a" * 40, executor="codex",
        task_id="TASK-111", action="CODE_FIX", executor_required=True,
        execution_profile=external_only,
    )
    first = execute_repair_dispatch(
        **request, invoke_repair=lambda: (called.append("once"), RepairInvocation(1))[1]
    )
    assert first.status == "FAILED" and called == ["once"]
    exists, bound = existing_repair_profile(
        state_root=state, repo_root=repo, repair_dispatch_id="external-repair"
    )
    assert exists and bound == replace(external_only, run_id="external-repair")
    reject_existing_selector_collision(
        state_root=state, repo_root=repo, repair_dispatch_id="external-repair",
        failed_run_id="RUN-111-001", repair_sha="a" * 40, executor="codex",
        execution_profile=bound,
    )
    replay = replay_existing_repair_dispatch(
        state_root=state, repo_root=repo, repair_dispatch_id="external-repair",
        failed_run_id="RUN-111-001", repair_sha="a" * 40, executor="codex",
        execution_profile=bound,
    )
    assert replay is not None and replay.replayed and called == ["once"]
    record_path = next((state / "repair-dispatches").glob("*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.update(status="STARTED", exit_code=None, detail="interrupted")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    reconciled = replay_existing_repair_dispatch(
        state_root=state, repo_root=repo, repair_dispatch_id="external-repair",
        failed_run_id="RUN-111-001", repair_sha="a" * 40, executor="codex",
        execution_profile=bound,
    )
    assert reconciled is not None and reconciled.status == "RECONCILIATION_BLOCKED"

    policy_path.write_text(supported.replace("[low, repo_only]", "[repo_only]").replace(
        "default_reasoning_effort: low", "default_reasoning_effort: repo_only"
    ), encoding="utf-8")
    low_request = {**request, "execution_profile": profile_for("AUTHORIZATION")}
    before = record_path.read_bytes()
    with pytest.raises(RepairDispatchError, match="profile binding"):
        execute_repair_dispatch(
            **low_request, invoke_repair=lambda: pytest.fail("unsupported profile invoked REPAIR")
        )
    assert record_path.read_bytes() == before
    policy_path.unlink()
    with pytest.raises(RepairDispatchError, match="profile binding"):
        existing_repair_profile(
            state_root=state, repo_root=repo, repair_dispatch_id="external-repair"
        )
    policy_path.write_text("not: a valid policy\n", encoding="utf-8")
    with pytest.raises(RepairDispatchError, match="profile binding"):
        existing_repair_profile(
            state_root=state, repo_root=repo, repair_dispatch_id="external-repair"
        )


def test_exact_source_repair_policy_admits_policyless_control_and_replays(
    tmp_path: Path,
) -> None:
    repo = tmp_path
    state = repo / ".git" / "aios"
    policy_path = repo / ".ai" / "executor-profiles.yaml"
    policy = parse_execution_profile_policy(policy_path.read_bytes())
    policy_path.unlink()
    profile = replace(profile_for("repair-source-111"), reasoning_effort="repo_only")
    request = dict(
        state_root=state, repo_root=repo, repair_dispatch_id="repair-source-111",
        failed_run_id="RUN-111-001", repair_sha="a" * 40,
        executor="codex", task_id="TASK-111", action="CODE_FIX",
        executor_required=True,
        execution_profile=profile,
    )
    called: list[str] = []
    with pytest.raises(RepairDispatchError, match="profile binding"):
        execute_repair_dispatch(
            **request, invoke_repair=lambda: pytest.fail("ordinary REPAIR invoked")
        )
    assert not (state / "repair-dispatches").exists()
    first = execute_repair_dispatch(
        **request, source_repair_policy=policy,
        invoke_repair=lambda: (called.append("once"), RepairInvocation(1))[1],
    )
    assert first.status == "FAILED" and called == ["once"]
    exists, bound = existing_repair_profile(
        state_root=state, repo_root=repo, repair_dispatch_id="repair-source-111",
        source_repair_policy=policy,
    )
    assert exists and bound == profile
    replay = replay_existing_repair_dispatch(
        state_root=state, repo_root=repo, repair_dispatch_id="repair-source-111",
        failed_run_id="RUN-111-001", repair_sha="a" * 40,
        executor="codex", execution_profile=bound, source_repair_policy=policy,
    )
    assert replay is not None and replay.replayed and called == ["once"]
    record_path = next((state / "repair-dispatches").glob("*.json"))
    before = record_path.read_bytes()
    with pytest.raises(RepairDispatchError, match="profile binding"):
        existing_repair_profile(
            state_root=state, repo_root=repo, repair_dispatch_id="repair-source-111"
        )
    with pytest.raises(RepairDispatchError, match="collision"):
        reject_existing_selector_collision(
            state_root=state, repo_root=repo, repair_dispatch_id="repair-source-111",
            failed_run_id="RUN-111-001", repair_sha="b" * 40,
            executor="codex", execution_profile=bound,
            source_repair_policy=policy,
        )
    assert record_path.read_bytes() == before and called == ["once"]


def _write_run(
    state: Path,
    run_id: str,
    *,
    action: str = "CODE_FIX",
    executor: str = "codex",
    terminal: str | None = None,
    repair_sha: str = "a" * 40,
    executor_required: bool = True,
) -> None:
    (state / "runs").mkdir(parents=True, exist_ok=True)
    (state / "repairs").mkdir(parents=True, exist_ok=True)
    run = {
        "run_id": run_id,
        "task": {"id": "TASK-111", "revision": 2},
        "executor": executor,
    }
    execution = {
        "failed_run_id": "RUN-111-001",
        "repair_authorization_sha": repair_sha,
        "repair": {"failed_run_id": "RUN-111-001", "action": action, "task": run["task"]},
        "run": run,
        "failure": {"run_id": "RUN-111-001", "executor": executor, "task": run["task"]},
    }
    (state / "runs" / f"{run_id}.json").write_text(
        json.dumps(run), encoding="utf-8"
    )
    (state / "repairs" / f"{run_id}.json").write_text(
        json.dumps(execution), encoding="utf-8"
    )
    if executor_required and action != "NO_CHANGE":
        persist_execution_profile(
            state / "execution-profiles" / f"{run_id}.json",
            profile_for(run_id, executor),
        )
    if terminal is not None:
        (state / terminal).mkdir(parents=True, exist_ok=True)
        (state / terminal / f"{run_id}.json").write_text("{}", encoding="utf-8")


def _execute(
    state: Path,
    invoke,
    *,
    dispatch_id: str = "repair-111",
    repair_sha: str = "a" * 40,
    executor: str | None = "codex",
    action: str = "CODE_FIX",
    executor_required: bool | None = None,
):
    return execute_repair_dispatch(
        state_root=state,
        repo_root=state.parents[1],
        repair_dispatch_id=dispatch_id,
        failed_run_id="RUN-111-001",
        repair_sha=repair_sha,
        executor=executor,
        task_id="TASK-111",
        action=action,
        executor_required=(action != "NO_CHANGE" if executor_required is None else executor_required),
        invoke_repair=invoke,
        execution_profile=(
            profile_for("AUTHORIZATION", executor)
            if executor is not None
            else None
        ),
    )


def test_first_delivery_binds_before_invocation_returns_and_terminal_replays(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"
    calls: list[str] = []

    def invoke() -> RepairInvocation:
        calls.append("called")
        _write_run(state, "RUN-111-002", terminal="results")
        bind_repair_run(
            state_root=state,
            repo_root=state.parents[1],
            repair_dispatch_id="repair-111",
            run_id="RUN-111-002",
            execution_profile=profile_for("RUN-111-002"),
        )
        return RepairInvocation(0, "RUN-111-002")

    first = _execute(state, invoke)
    replay = _execute(state, lambda: pytest.fail("duplicate invoked REPAIR"))

    assert calls == ["called"]
    assert first.status == replay.status == "SUCCEEDED"
    assert first.run_id == replay.run_id == "RUN-111-002"
    assert first.replayed is False and replay.replayed is True


def test_conflicting_dispatch_id_reuse_fails_without_invocation(tmp_path: Path) -> None:
    state = tmp_path / ".git" / "aios"
    _execute(state, lambda: RepairInvocation(1))
    record = next((state / "repair-dispatches").glob("*.json"))
    before = record.read_bytes()
    with pytest.raises(RepairDispatchError, match="collision"):
        _execute(
            state,
            lambda: pytest.fail("collision invoked REPAIR"),
            repair_sha="b" * 40,
        )
    assert record.read_bytes() == before

    changed_profile = ResolvedExecutionProfile(
        run_id="AUTHORIZATION",
        executor="codex",
        model="test/codex-future-v2",
        reasoning_effort="low",
        model_source="EXPLICIT",
        effort_source="EXPLICIT",
    )
    with pytest.raises(RepairDispatchError, match="binding differs"):
        execute_repair_dispatch(
            state_root=state,
            repo_root=state.parents[1],
            repair_dispatch_id="repair-111",
            failed_run_id="RUN-111-001",
            repair_sha="a" * 40,
            executor="codex",
            task_id="TASK-111",
            action="CODE_FIX",
            executor_required=True,
            execution_profile=changed_profile,
            invoke_repair=lambda: pytest.fail("profile collision invoked REPAIR"),
        )
    assert record.read_bytes() == before


def test_restart_never_claims_a_later_run_when_dispatch_is_unbound(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"

    def crash() -> RepairInvocation:
        raise RuntimeError("host lost")

    with pytest.raises(RuntimeError, match="host lost"):
        _execute(state, crash)
    _write_run(state, "RUN-111-009", terminal="results")
    replay = _execute(state, lambda: pytest.fail("restart invoked REPAIR"))
    assert replay.status == "RECONCILIATION_BLOCKED"
    assert replay.run_id is None
    assert "no durably bound REPAIR RUN" in replay.detail


def test_restart_reconciles_only_the_exact_bound_terminal_run(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"

    def bind_then_crash() -> RepairInvocation:
        _write_run(state, "RUN-111-002", terminal="failures")
        bind_repair_run(
            state_root=state,
            repo_root=state.parents[1],
            repair_dispatch_id="repair-bound-111",
            run_id="RUN-111-002",
            execution_profile=profile_for("RUN-111-002"),
        )
        raise RuntimeError("host lost after admission")

    with pytest.raises(RuntimeError, match="after admission"):
        _execute(state, bind_then_crash, dispatch_id="repair-bound-111")
    replay = _execute(
        state,
        lambda: pytest.fail("bound restart invoked REPAIR"),
        dispatch_id="repair-bound-111",
    )
    assert replay.status == "FAILED"
    assert replay.run_id == "RUN-111-002"
    assert replay.replayed is True


def test_no_change_requires_absent_executor_and_uses_same_dispatch_family(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"

    def invoke() -> RepairInvocation:
        _write_run(
            state,
            "RUN-111-002",
            action="NO_CHANGE",
            executor="antigravity-minimax",
            terminal="results",
        )
        bind_repair_run(
            state_root=state,
            repo_root=state.parents[1],
            repair_dispatch_id="repair-no-change-111",
            run_id="RUN-111-002",
        )
        return RepairInvocation(0, "RUN-111-002")

    outcome = _execute(
        state,
        invoke,
        dispatch_id="repair-no-change-111",
        executor=None,
        action="NO_CHANGE",
    )
    assert outcome.status == "SUCCEEDED"
    assert outcome.executor is None
    with pytest.raises(RepairDispatchError, match="forbids"):
        _execute(
            tmp_path / "other",
            lambda: pytest.fail("invalid NO_CHANGE invoked"),
            executor="codex",
            action="NO_CHANGE",
        )


def test_nonreusable_finalize_candidate_requires_executor_and_remains_at_most_once(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"
    calls = []

    def invoke() -> RepairInvocation:
        calls.append("executor")
        _write_run(
            state,
            "RUN-111-002",
            action="FINALIZE_CANDIDATE",
            terminal="results",
        )
        bind_repair_run(
            state_root=state,
            repo_root=state.parents[1],
            repair_dispatch_id="repair-finalize-111",
            run_id="RUN-111-002",
            execution_profile=profile_for("RUN-111-002"),
        )
        return RepairInvocation(0, "RUN-111-002")

    first = _execute(
        state,
        invoke,
        dispatch_id="repair-finalize-111",
        action="FINALIZE_CANDIDATE",
    )
    replay = _execute(
        state,
        lambda: pytest.fail("FINALIZE_CANDIDATE was invoked twice"),
        dispatch_id="repair-finalize-111",
        action="FINALIZE_CANDIDATE",
    )

    assert calls == ["executor"]
    assert first.status == replay.status == "SUCCEEDED"
    assert replay.replayed is True
    with pytest.raises(RepairDispatchError, match="explicit Executor"):
        _execute(
            tmp_path / "missing-executor",
            lambda: pytest.fail("executor-less FINALIZE_CANDIDATE invoked"),
            executor=None,
            action="FINALIZE_CANDIDATE",
        )


@pytest.mark.parametrize("terminal", ["results", "failures"])
def test_executorless_finalize_binds_and_reconciles_exact_run(
    tmp_path: Path, terminal: str,
) -> None:
    state = tmp_path / ".git" / "aios"

    def bind_then_crash() -> RepairInvocation:
        _write_run(
            state, "RUN-111-002", action="FINALIZE_CANDIDATE",
            executor="antigravity-minimax", executor_required=False, terminal=terminal,
        )
        bind_repair_run(
            state_root=state, repo_root=tmp_path,
            repair_dispatch_id="repair-elided-111", run_id="RUN-111-002",
        )
        raise RuntimeError("interrupted after binding")

    request = dict(
        dispatch_id="repair-elided-111", action="FINALIZE_CANDIDATE",
        executor=None, executor_required=False,
    )
    with pytest.raises(RuntimeError, match="after binding"):
        _execute(state, bind_then_crash, **request)
    replay = _execute(state, lambda: pytest.fail("replay invoked REPAIR"), **request)
    assert replay.status == ("SUCCEEDED" if terminal == "results" else "FAILED")
    assert replay.replayed and replay.run_id == "RUN-111-002"
    assert replay.executor is None and replay.action == "FINALIZE_CANDIDATE"
    exists, profile = existing_repair_profile(
        state_root=state, repo_root=tmp_path, repair_dispatch_id="repair-elided-111",
    )
    assert exists and profile is None
    record_path = next((state / "repair-dispatches").glob("*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["version"] == 3 and record["executor_required"] is False
    assert record["model"] is None and record["reasoning_effort"] is None
    assert not (state / "execution-profiles").exists()
    before = record_path.read_bytes()
    for changes in (
        {"repair_sha": "b" * 40}, {"action": "NO_CHANGE"},
        {"executor": "codex", "executor_required": True},
    ):
        with pytest.raises(RepairDispatchError, match="collision"):
            _execute(state, lambda: pytest.fail("collision invoked"), **(request | changes))
    assert record_path.read_bytes() == before
    replay = replay_existing_repair_dispatch(
        state_root=state, repo_root=tmp_path, repair_dispatch_id="repair-elided-111",
        failed_run_id="RUN-111-001", repair_sha="a" * 40, executor=None,
    )
    assert replay is not None and replay.replayed


@pytest.mark.parametrize("tamper", ["sha", "action", "executor", "profile"])
def test_executorless_finalize_rejects_invalid_run_attribution(
    tmp_path: Path, tamper: str,
) -> None:
    state = tmp_path / ".git" / "aios"

    def invoke() -> RepairInvocation:
        _write_run(
            state, "RUN-111-002", action="FINALIZE_CANDIDATE",
            executor_required=False, terminal="results",
        )
        if tamper == "profile":
            persist_execution_profile(
                state / "execution-profiles/RUN-111-002.json", profile_for("RUN-111-002"),
            )
        elif tamper == "executor":
            path = state / "runs/RUN-111-002.json"
            run = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps(run | {"executor": "antigravity"}), encoding="utf-8")
        else:
            path = state / "repairs/RUN-111-002.json"
            execution = json.loads(path.read_text(encoding="utf-8"))
            if tamper == "sha":
                execution["repair_authorization_sha"] = "b" * 40
            else:
                execution["repair"]["action"] = "NO_CHANGE"
            path.write_text(json.dumps(execution), encoding="utf-8")
        with pytest.raises(RepairDispatchError, match="does not match dispatch"):
            bind_repair_run(
                state_root=state, repo_root=tmp_path,
                repair_dispatch_id="repair-111", run_id="RUN-111-002",
            )
        return RepairInvocation(0, "RUN-111-002")

    outcome = _execute(
        state, invoke, action="FINALIZE_CANDIDATE", executor=None, executor_required=False,
    )
    assert outcome.status == "RECONCILIATION_BLOCKED" and outcome.run_id is None


@pytest.mark.parametrize("action, required, executor", [
    ("CODE_FIX", True, None), ("CONTINUE_IMPLEMENTATION", True, None),
    ("FINALIZE_CANDIDATE", True, None), ("FINALIZE_CANDIDATE", False, "codex"),
    ("CODE_FIX", False, None), ("CONTINUE_IMPLEMENTATION", False, None),
    ("NO_CHANGE", False, "codex"), ("NO_CHANGE", True, "codex"),
    ("FINALIZE_CANDIDATE", None, None),
])
def test_dispatch_requirement_inconsistency_never_admits(
    tmp_path: Path, action: str, required: bool | None, executor: str | None,
) -> None:
    state = tmp_path / ".git" / "aios"
    with pytest.raises(RepairDispatchError):
        execute_repair_dispatch(
            state_root=state, repo_root=tmp_path, repair_dispatch_id="invalid-111",
            failed_run_id="RUN-111-001", repair_sha="a" * 40, task_id="TASK-111",
            action=action, executor=executor, executor_required=required,
            invoke_repair=lambda: pytest.fail("invalid requirement invoked REPAIR"),
        )
    assert not (state / "repair-dispatches").exists()
    assert not (state / "runs").exists()


def test_executorless_finalize_forbids_profile_and_coding_finalize_requires_profile(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"
    for executor, required, profile in (
        (None, False, profile_for("AUTHORIZATION")), ("codex", True, None),
    ):
        with pytest.raises(RepairDispatchError, match="profile"):
            execute_repair_dispatch(
                state_root=state, repo_root=tmp_path, repair_dispatch_id="profile-111",
                failed_run_id="RUN-111-001", repair_sha="a" * 40, task_id="TASK-111",
                action="FINALIZE_CANDIDATE", executor=executor, executor_required=required,
                execution_profile=profile, invoke_repair=lambda: pytest.fail("invalid profile invoked"),
            )
    assert not (state / "repair-dispatches").exists()


@pytest.mark.parametrize("version", [1, 2])
def test_historical_terminal_replay_preserves_record_version(
    tmp_path: Path, version: int,
) -> None:
    state = tmp_path / ".git" / "aios"
    dispatch_id = "historical-repair-111"
    record_path = state / "repair-dispatches" / (
        hashlib.sha256(dispatch_id.encode("ascii")).hexdigest() + ".json"
    )
    record_path.parent.mkdir(parents=True)
    legacy = {
        "version": version,
        "repair_dispatch_id": dispatch_id,
        "failed_run_id": "RUN-111-001",
        "repair_sha": "a" * 40,
        "executor": "codex",
        "task_id": "TASK-111",
        "action": "CODE_FIX",
        "status": "FAILED",
        "run_id": None,
        "exit_code": 1,
        "detail": "historical terminal failure",
    }
    if version == 2:
        legacy.update(
            model="test/codex-future-v1", reasoning_effort="low",
            model_source="EXPLICIT", effort_source="EXPLICIT",
        )
    record_path.write_text(json.dumps(legacy), encoding="utf-8")

    outcome = execute_repair_dispatch(
        state_root=state,
        repo_root=state.parents[1],
        repair_dispatch_id=dispatch_id,
        failed_run_id="RUN-111-001",
        repair_sha="a" * 40,
        executor="codex",
        task_id="TASK-111",
        action="CODE_FIX",
        executor_required=True,
        execution_profile=profile_for("AUTHORIZATION") if version == 2 else None,
        invoke_repair=lambda: pytest.fail("legacy replay invoked REPAIR"),
    )

    assert outcome.replayed is True
    assert json.loads(record_path.read_text(encoding="utf-8")) == legacy
    assert not (state / "execution-profiles").exists()


def test_repair_wakeup_replays_historical_v1_without_resolving_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / ".git" / "aios"
    dispatch_id = "historical-repair-wakeup-111"
    record_path = state / "repair-dispatches" / (
        hashlib.sha256(dispatch_id.encode("ascii")).hexdigest() + ".json"
    )
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps({
        "version": 1,
        "repair_dispatch_id": dispatch_id,
        "failed_run_id": "RUN-111-001",
        "repair_sha": "a" * 40,
        "executor": "codex",
        "task_id": "TASK-111",
        "action": "CODE_FIX",
        "status": "FAILED",
        "run_id": None,
        "exit_code": 1,
        "detail": "historical terminal failure",
    }), encoding="utf-8")
    monkeypatch.setattr(operator_module, "resolve_repository", lambda _repo: tmp_path)
    monkeypatch.setattr(operator_module, "runtime_state_root", lambda _repo: state)
    monkeypatch.setattr(
        operator_module,
        "bind_execution_profile",
        lambda **_kwargs: pytest.fail("historical REPAIR resolved current defaults"),
    )

    outcome = operator_module.run_repair_wakeup(
        dispatch_id,
        "RUN-111-001",
        "a" * 40,
        executor="codex",
        repo=tmp_path,
    )

    assert outcome.replayed is True
    assert outcome.status == "FAILED"
