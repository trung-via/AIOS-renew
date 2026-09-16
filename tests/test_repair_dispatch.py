from __future__ import annotations

import json
from pathlib import Path

import pytest

from aios_renew.repair_dispatch import (
    RepairDispatchError,
    RepairInvocation,
    bind_repair_run,
    execute_repair_dispatch,
)


def _write_run(
    state: Path,
    run_id: str,
    *,
    action: str = "CODE_FIX",
    executor: str = "codex",
    terminal: str | None = None,
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
        "repair": {"failed_run_id": "RUN-111-001", "action": action},
        "run": run,
    }
    (state / "runs" / f"{run_id}.json").write_text(
        json.dumps(run), encoding="utf-8"
    )
    (state / "repairs" / f"{run_id}.json").write_text(
        json.dumps(execution), encoding="utf-8"
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
):
    return execute_repair_dispatch(
        state_root=state,
        repair_dispatch_id=dispatch_id,
        failed_run_id="RUN-111-001",
        repair_sha=repair_sha,
        executor=executor,
        task_id="TASK-111",
        action=action,
        invoke_repair=invoke,
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
            repair_dispatch_id="repair-111",
            run_id="RUN-111-002",
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
            repair_dispatch_id="repair-bound-111",
            run_id="RUN-111-002",
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


def test_finalize_candidate_requires_executor_and_remains_at_most_once(
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
            repair_dispatch_id="repair-finalize-111",
            run_id="RUN-111-002",
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
