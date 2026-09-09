from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from aios_renew.correction_dispatch import (
    CorrectionDispatchError,
    CorrectionInvocation,
    bind_correction_run,
    execute_correction_dispatch,
)
from aios_renew.remote_surface import ApprovedRemediation


def approval() -> ApprovedRemediation:
    return ApprovedRemediation(
        source_run_id="RUN-082-000",
        task_id="TASK-082",
        task_revision=1,
        review_id="REVIEW-082-001",
        finding_id="F1",
        action="CODE_FIX",
        reviewed_sha="a" * 40,
        remediation_ref="refs/heads/aios/remediation/RUN-082-000-F1",
        remediation_sha="b" * 40,
        approver="human",
    )


def write_run(state: Path, run_id: str, *, terminal: str | None = None) -> None:
    (state / "runs").mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "REMEDIATION",
        "execution": {
            "review_id": "REVIEW-082-001",
            "finding": {"id": "F1"},
            "remediation": {
                "finding_id": "F1",
                "action": "CODE_FIX",
                "reviewed_sha": "a" * 40,
            },
            "run": {
                "run_id": run_id,
                "task": {"id": "TASK-082", "revision": 1},
                "executor": "codex",
            },
        },
    }
    (state / "runs" / f"{run_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    if terminal:
        (state / terminal).mkdir(parents=True, exist_ok=True)
        (state / terminal / f"{run_id}.json").write_text("{}", encoding="utf-8")


def invoke_once(state: Path, calls: list[str]) -> CorrectionInvocation:
    calls.append("called")
    records = list((state / "correction-dispatches").glob("*.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding="utf-8"))["status"] == "STARTED"
    write_run(state, "RUN-082-001", terminal="results")
    bind_correction_run(
        state_root=state,
        correction_dispatch_id="correction-082",
        run_id="RUN-082-001",
    )
    return CorrectionInvocation(0, "RUN-082-001")


def test_first_delivery_persists_binding_then_invokes_once_and_terminal_replays(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"
    calls: list[str] = []
    first = execute_correction_dispatch(
        state_root=state,
        correction_dispatch_id="correction-082",
        source_run_id="RUN-082-000",
        finding_id="F1",
        executor="codex",
        approval=approval(),
        invoke_remediation=lambda: invoke_once(state, calls),
    )
    replay = execute_correction_dispatch(
        state_root=state,
        correction_dispatch_id="correction-082",
        source_run_id="RUN-082-000",
        finding_id="F1",
        executor="codex",
        approval=approval(),
        invoke_remediation=lambda: pytest.fail("replay invoked REMEDIATION"),
    )

    assert calls == ["called"]
    assert first.status == replay.status == "SUCCEEDED"
    assert first.run_id == replay.run_id == "RUN-082-001"
    assert first.replayed is False and replay.replayed is True


def test_dispatch_id_collision_never_overwrites_or_invokes(tmp_path: Path) -> None:
    state = tmp_path / ".git" / "aios"
    execute_correction_dispatch(
        state_root=state,
        correction_dispatch_id="collision-082",
        source_run_id="RUN-082-000",
        finding_id="F1",
        executor="codex",
        approval=approval(),
        invoke_remediation=lambda: CorrectionInvocation(2),
    )
    record = next((state / "correction-dispatches").glob("*.json"))
    before = record.read_bytes()
    with pytest.raises(CorrectionDispatchError, match="collision"):
        execute_correction_dispatch(
            state_root=state,
            correction_dispatch_id="collision-082",
            source_run_id="RUN-082-000",
            finding_id="F1",
            executor="antigravity",
            approval=replace(approval(), remediation_sha="c" * 40),
            invoke_remediation=lambda: pytest.fail("collision invoked REMEDIATION"),
        )
    assert record.read_bytes() == before


def test_restart_reconciles_one_terminal_run_without_reexecution(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"

    def crash() -> CorrectionInvocation:
        raise RuntimeError("host lost")

    with pytest.raises(RuntimeError, match="host lost"):
        execute_correction_dispatch(
            state_root=state,
            correction_dispatch_id="restart-082",
            source_run_id="RUN-082-000",
            finding_id="F1",
            executor="codex",
            approval=approval(),
            invoke_remediation=crash,
        )
    write_run(state, "RUN-082-002", terminal="failures")
    replay = execute_correction_dispatch(
        state_root=state,
        correction_dispatch_id="restart-082",
        source_run_id="RUN-082-000",
        finding_id="F1",
        executor="codex",
        approval=approval(),
        invoke_remediation=lambda: pytest.fail("restart invoked REMEDIATION"),
    )
    assert replay.status == "FAILED"
    assert replay.run_id == "RUN-082-002"
    assert replay.replayed is True
