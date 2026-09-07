"""Deterministic regressions for A2 outer dispatch identity."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from aios_renew import dispatch_reconciliation as dispatch
from aios_renew.dispatch_reconciliation import (
    DispatchError,
    DispatchInvocation,
    bind_dispatch_run,
    execute_dispatch,
)


def write_run(
    state_root: Path,
    run_id: str,
    *,
    task_id: str = "TASK-068",
    executor: str = "codex",
) -> None:
    runs = state_root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "task": {"id": task_id, "revision": 1},
                "executor": executor,
                "base_sha": "a" * 40,
                "workspace": "C:/repo",
                "head_sha": None,
                "status": "ACTIVE",
            }
        ),
        encoding="utf-8",
    )


def write_terminal(state_root: Path, directory: str, run_id: str) -> None:
    target = state_root / directory
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{run_id}.json").write_text("{}", encoding="utf-8")


def admit_dispatch_run(
    state_root: Path, dispatch_id: str, run_id: str
) -> None:
    write_run(state_root, run_id)
    bind_dispatch_run(
        state_root=state_root,
        dispatch_id=dispatch_id,
        task_id="TASK-068",
        executor="codex",
        run_id=run_id,
    )


def crash_after_started(state_root: Path, dispatch_id: str = "delivery-068") -> None:
    def crash() -> DispatchInvocation:
        raise RuntimeError("simulated host loss")

    with pytest.raises(RuntimeError, match="simulated host loss"):
        execute_dispatch(
            state_root=state_root,
            dispatch_id=dispatch_id,
            task_id="TASK-068",
            executor="codex",
            invoke_primary=crash,
        )


def test_first_seen_is_durable_before_one_primary_invocation_and_replays(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ".git" / "aios"
    write_run(state_root, "RUN-068-001")
    calls = 0

    def invoke() -> DispatchInvocation:
        nonlocal calls
        calls += 1
        records = list((state_root / "dispatches").glob("*.json"))
        assert len(records) == 1
        started = json.loads(records[0].read_text(encoding="utf-8"))
        assert started["status"] == "STARTED"
        assert started["dispatch_id"] == "delivery-068"
        assert started["task_id"] == "TASK-068"
        assert started["executor"] == "codex"
        assert started["pre_run_ids"] == ["RUN-068-001"]
        admit_dispatch_run(state_root, "delivery-068", "RUN-068-002")
        write_terminal(state_root, "results", "RUN-068-002")
        return DispatchInvocation(0, "RUN-068-002")

    first = execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=invoke,
    )
    replay = execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=lambda: pytest.fail("duplicate invoked PRIMARY"),
    )

    assert calls == 1
    assert first.status == replay.status == "SUCCEEDED"
    assert first.run_id == replay.run_id == "RUN-068-002"
    assert first.exit_code == replay.exit_code == 0
    assert first.replayed is False
    assert replay.replayed is True


def test_terminal_nonzero_replay_preserves_failure_without_reexecution(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ".git" / "aios"
    calls = 0

    def fail() -> DispatchInvocation:
        nonlocal calls
        calls += 1
        return DispatchInvocation(42)

    first = execute_dispatch(
        state_root=state_root,
        dispatch_id="failed-delivery",
        task_id="TASK-068",
        executor="antigravity",
        invoke_primary=fail,
    )
    replay = execute_dispatch(
        state_root=state_root,
        dispatch_id="failed-delivery",
        task_id="TASK-068",
        executor="antigravity",
        invoke_primary=lambda: pytest.fail("failed duplicate invoked PRIMARY"),
    )

    assert calls == 1
    assert first.status == replay.status == "FAILED"
    assert first.exit_code == replay.exit_code == 42
    assert first.run_id is replay.run_id is None


def test_duplicate_while_original_invocation_is_active_reports_in_progress(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ".git" / "aios"
    entered = threading.Event()
    release = threading.Event()
    completed: list[object] = []

    def invoke() -> DispatchInvocation:
        entered.set()
        assert release.wait(timeout=5)
        admit_dispatch_run(state_root, "active-delivery", "RUN-068-001")
        write_terminal(state_root, "results", "RUN-068-001")
        return DispatchInvocation(0, "RUN-068-001")

    def first_delivery() -> None:
        try:
            completed.append(
                execute_dispatch(
                    state_root=state_root,
                    dispatch_id="active-delivery",
                    task_id="TASK-068",
                    executor="codex",
                    invoke_primary=invoke,
                )
            )
        except BaseException as exc:  # surfaced by the owning test thread
            completed.append(exc)

    thread = threading.Thread(target=first_delivery)
    thread.start()
    assert entered.wait(timeout=5)
    replay = execute_dispatch(
        state_root=state_root,
        dispatch_id="active-delivery",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=lambda: pytest.fail("active duplicate invoked PRIMARY"),
    )
    assert replay.status == "IN_PROGRESS"
    assert replay.exit_code == dispatch.IN_PROGRESS_EXIT_CODE
    active_record = json.loads(
        next((state_root / "dispatches").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert active_record["status"] == "STARTED"
    assert active_record["run_id"] is None

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(completed) == 1
    assert not isinstance(completed[0], BaseException)
    assert completed[0].status == "SUCCEEDED"
    assert completed[0].run_id == "RUN-068-001"


def test_dispatch_binding_collision_fails_closed_without_mutation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ".git" / "aios"
    execute_dispatch(
        state_root=state_root,
        dispatch_id="bound-delivery",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=lambda: DispatchInvocation(7),
    )
    record_path = next((state_root / "dispatches").glob("*.json"))
    before = record_path.read_bytes()

    for task_id, executor in (
        ("TASK-069", "codex"),
        ("TASK-068", "antigravity"),
    ):
        with pytest.raises(DispatchError, match="binding does not match"):
            execute_dispatch(
                state_root=state_root,
                dispatch_id="bound-delivery",
                task_id=task_id,
                executor=executor,
                invoke_primary=lambda: pytest.fail("collision invoked PRIMARY"),
            )
        assert record_path.read_bytes() == before


@pytest.mark.parametrize(
    "dispatch_id",
    [
        "",
        "../escape",
        "..\\escape",
        "delivery.with.dot",
        "delivery;Write-Output-pwned",
        "$(command)",
        "with space",
        "x" * 129,
    ],
)
def test_malformed_dispatch_ids_create_no_state_or_authority(
    tmp_path: Path, dispatch_id: str
) -> None:
    state_root = tmp_path / ".git" / "aios"
    with pytest.raises(DispatchError, match="dispatch_id"):
        execute_dispatch(
            state_root=state_root,
            dispatch_id=dispatch_id,
            task_id="TASK-068",
            executor="codex",
            invoke_primary=lambda: pytest.fail("invalid dispatch invoked PRIMARY"),
        )
    assert not state_root.exists()


def test_dispatch_id_is_hashed_not_used_as_a_path(tmp_path: Path) -> None:
    state_root = tmp_path / ".git" / "aios"
    execute_dispatch(
        state_root=state_root,
        dispatch_id="opaque-delivery_068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=lambda: DispatchInvocation(3),
    )
    record_path = next((state_root / "dispatches").glob("*.json"))
    assert record_path.stem != "opaque-delivery_068"
    assert len(record_path.stem) == 64


@pytest.mark.parametrize(
    "terminal_directory",
    ["results", "failures"],
)
def test_restart_blocks_unowned_terminal_run_without_invocation(
    tmp_path: Path,
    terminal_directory: str,
) -> None:
    state_root = tmp_path / ".git" / "aios"
    write_run(state_root, "RUN-068-001")
    crash_after_started(state_root)
    write_run(state_root, "RUN-068-002")
    write_terminal(state_root, terminal_directory, "RUN-068-002")

    outcome = execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=lambda: pytest.fail("restart invoked PRIMARY"),
    )
    assert outcome.status == "RECONCILIATION_BLOCKED"
    assert outcome.exit_code == dispatch.RECONCILIATION_BLOCKED_EXIT_CODE
    assert outcome.run_id is None
    assert outcome.replayed is True


def test_competing_direct_primary_cannot_be_attributed_to_dispatch(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ".git" / "aios"

    def invoke() -> DispatchInvocation:
        write_run(state_root, "RUN-068-001")
        write_terminal(state_root, "results", "RUN-068-001")
        admit_dispatch_run(state_root, "delivery-068", "RUN-068-002")
        write_terminal(state_root, "results", "RUN-068-002")
        return DispatchInvocation(0, "RUN-068-002")

    outcome = execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=invoke,
    )

    assert outcome.status == "SUCCEEDED"
    assert outcome.run_id == "RUN-068-002"


def test_distinct_dispatch_between_snapshot_and_admission_keeps_ownership(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ".git" / "aios"

    def invoke_outer() -> DispatchInvocation:
        def invoke_inner() -> DispatchInvocation:
            admit_dispatch_run(state_root, "other-delivery", "RUN-068-001")
            write_terminal(state_root, "results", "RUN-068-001")
            return DispatchInvocation(0, "RUN-068-001")

        inner = execute_dispatch(
            state_root=state_root,
            dispatch_id="other-delivery",
            task_id="TASK-068",
            executor="codex",
            invoke_primary=invoke_inner,
        )
        assert inner.run_id == "RUN-068-001"
        admit_dispatch_run(state_root, "delivery-068", "RUN-068-002")
        write_terminal(state_root, "results", "RUN-068-002")
        return DispatchInvocation(0, "RUN-068-002")

    outer = execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=invoke_outer,
    )

    assert outer.status == "SUCCEEDED"
    assert outer.run_id == "RUN-068-002"


def test_restart_reconciles_only_run_owned_at_admission_after_competing_run(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ".git" / "aios"

    def crash_after_admission() -> DispatchInvocation:
        write_run(state_root, "RUN-068-001")
        write_terminal(state_root, "results", "RUN-068-001")
        admit_dispatch_run(state_root, "delivery-068", "RUN-068-002")
        write_terminal(state_root, "failures", "RUN-068-002")
        raise RuntimeError("simulated host loss after admission")

    with pytest.raises(RuntimeError, match="after admission"):
        execute_dispatch(
            state_root=state_root,
            dispatch_id="delivery-068",
            task_id="TASK-068",
            executor="codex",
            invoke_primary=crash_after_admission,
        )

    outcome = execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=lambda: pytest.fail("restart invoked PRIMARY"),
    )

    assert outcome.status == "FAILED"
    assert outcome.exit_code == 1
    assert outcome.run_id == "RUN-068-002"
    assert outcome.replayed is True


def test_restart_reports_in_progress_for_one_incomplete_run_with_active_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / ".git" / "aios"
    def crash_after_admission() -> DispatchInvocation:
        admit_dispatch_run(state_root, "delivery-068", "RUN-068-001")
        raise RuntimeError("simulated host loss")

    with pytest.raises(RuntimeError, match="simulated host loss"):
        execute_dispatch(
            state_root=state_root,
            dispatch_id="delivery-068",
            task_id="TASK-068",
            executor="codex",
            invoke_primary=crash_after_admission,
        )
    monkeypatch.setattr(dispatch, "_operator_lock_is_held", lambda _path: True)

    outcome = execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=lambda: pytest.fail("in-progress replay invoked PRIMARY"),
    )
    assert outcome.status == "IN_PROGRESS"
    assert outcome.exit_code == dispatch.IN_PROGRESS_EXIT_CODE
    assert outcome.run_id == "RUN-068-001"


def test_restart_blocks_one_incomplete_run_without_recovery(tmp_path: Path) -> None:
    state_root = tmp_path / ".git" / "aios"
    def crash_after_admission() -> DispatchInvocation:
        admit_dispatch_run(state_root, "delivery-068", "RUN-068-001")
        raise RuntimeError("simulated host loss")

    with pytest.raises(RuntimeError, match="simulated host loss"):
        execute_dispatch(
            state_root=state_root,
            dispatch_id="delivery-068",
            task_id="TASK-068",
            executor="codex",
            invoke_primary=crash_after_admission,
        )

    outcome = execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=lambda: pytest.fail("blocked replay invoked PRIMARY"),
    )
    assert outcome.status == "RECONCILIATION_BLOCKED"
    assert outcome.exit_code == dispatch.RECONCILIATION_BLOCKED_EXIT_CODE
    assert outcome.run_id == "RUN-068-001"
    assert not (state_root / "results" / "RUN-068-001.json").exists()
    assert not (state_root / "failures" / "RUN-068-001.json").exists()


@pytest.mark.parametrize("new_run_count", [0, 2])
def test_restart_blocks_zero_or_multiple_attributable_runs(
    tmp_path: Path, new_run_count: int
) -> None:
    state_root = tmp_path / ".git" / "aios"
    crash_after_started(state_root)
    for index in range(1, new_run_count + 1):
        write_run(state_root, f"RUN-068-{index:03d}")

    outcome = execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-068",
        task_id="TASK-068",
        executor="codex",
        invoke_primary=lambda: pytest.fail("uncertain replay invoked PRIMARY"),
    )
    assert outcome.status == "RECONCILIATION_BLOCKED"
    assert outcome.exit_code == dispatch.RECONCILIATION_BLOCKED_EXIT_CODE
    assert outcome.run_id is None
