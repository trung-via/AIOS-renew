"""Deterministic regressions for A2 outer dispatch identity."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from aios_renew import dispatch_reconciliation as dispatch
from aios_renew import operator as operator_module
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


def test_wakeup_sync_restart_continues_the_same_durable_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = tmp_path / "repo"
    state_root = repo / ".git" / "aios"
    state_root.mkdir(parents=True)
    runtime = SimpleNamespace(
        root=state_root,
        lock=state_root / "operator.lock",
    )
    monkeypatch.setattr(operator_module, "resolve_repository", lambda _repo: repo)
    monkeypatch.setattr(operator_module, "runtime_paths", lambda _repo: runtime)

    sync_calls = 0

    def synchronize(_repo: Path, *, allow_restart: bool = False) -> bool:
        nonlocal sync_calls
        sync_calls += 1
        assert allow_restart is True
        records = list((state_root / "dispatches").glob("*.json"))
        assert len(records) == 1
        record = json.loads(records[0].read_text(encoding="utf-8"))
        assert record["dispatch_id"] == "delivery-073"
        assert record["status"] == "STARTED"
        return sync_calls == 1

    monkeypatch.setattr(
        operator_module, "_synchronize_primary_branch", synchronize
    )
    monkeypatch.setattr(
        operator_module,
        "_git",
        lambda _repo, *args, **_kwargs: "b" * 40
        if args == ("rev-parse", "HEAD")
        else pytest.fail(f"unexpected git invocation: {args}"),
    )

    primary_calls = 0

    def run_task(task_id: str, **kwargs: object) -> SimpleNamespace:
        nonlocal primary_calls
        primary_calls += 1
        assert task_id == "TASK-073"
        assert kwargs["dispatch_id"] == "delivery-073"
        run_id = "RUN-073-001"
        write_run(state_root, run_id, task_id=task_id)
        bind_dispatch_run(
            state_root=state_root,
            dispatch_id="delivery-073",
            task_id=task_id,
            executor="codex",
            run_id=run_id,
        )
        write_terminal(state_root, "results", run_id)
        return SimpleNamespace(run_id=run_id)

    monkeypatch.setattr(operator_module, "run_task", run_task)
    restarted_argv: list[list[str]] = []

    def restart_runner(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        child_argv = cmd[3:]
        restarted_argv.append(child_argv)
        assert child_argv == [
            "wakeup",
            "delivery-073",
            "TASK-073",
            "--executor",
            "codex",
            "--repo",
            str(repo),
        ]
        env = kwargs["env"]
        assert isinstance(env, dict)
        restart_attempted = env["AIOS_RESTART_ATTEMPTED"]
        assert isinstance(restart_attempted, str)
        prior = os.environ.get("AIOS_RESTART_ATTEMPTED")
        os.environ["AIOS_RESTART_ATTEMPTED"] = restart_attempted
        try:
            exit_code = operator_module.main(
                child_argv,
                native_runner=restart_runner,
            )
        finally:
            if prior is None:
                os.environ.pop("AIOS_RESTART_ATTEMPTED", None)
            else:
                os.environ["AIOS_RESTART_ATTEMPTED"] = prior
        return subprocess.CompletedProcess(cmd, exit_code, b"", b"")

    argv = [
        "wakeup",
        "delivery-073",
        "TASK-073",
        "--executor",
        "codex",
        "--repo",
        str(repo),
    ]
    first_exit = operator_module.main(argv, native_runner=restart_runner)
    replay_exit = operator_module.main(argv, native_runner=restart_runner)

    assert first_exit == replay_exit == 0
    assert sync_calls == 2
    assert len(restarted_argv) == 1
    assert primary_calls == 1
    records = list((state_root / "dispatches").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["dispatch_id"] == "delivery-073"
    assert record["run_id"] == "RUN-073-001"
    assert record["status"] == "SUCCEEDED"
    assert len(list((state_root / "runs").glob("RUN-073-*.json"))) == 1


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
