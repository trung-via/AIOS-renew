from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import aios_renew.operator as operator_module
import aios_renew.remote_surface as remote_surface_module

from aios_renew.correction_dispatch import (
    CorrectionDispatchError,
    CorrectionInvocation,
    bind_correction_run,
    execute_correction_dispatch,
)
from aios_renew.execution_profile import (
    ResolvedExecutionProfile,
    persist_execution_profile,
)
from aios_renew.remote_surface import ApprovedRemediation


_execute_correction_dispatch = execute_correction_dispatch
_bind_correction_run = bind_correction_run


def profile_for(run_id: str, executor: str = "codex") -> ResolvedExecutionProfile:
    return ResolvedExecutionProfile(
        run_id=run_id,
        executor=executor,
        model=f"test/{executor}-future-v1",
        reasoning_effort="low",
        model_source="EXPLICIT",
        effort_source="EXPLICIT",
    )


def execute_correction_dispatch(**kwargs: object):
    kwargs.setdefault(
        "execution_profile",
        profile_for("AUTHORIZATION", str(kwargs["executor"])),
    )
    return _execute_correction_dispatch(**kwargs)


def bind_correction_run(**kwargs: object) -> None:
    kwargs.setdefault(
        "execution_profile",
        profile_for(str(kwargs["run_id"])),
    )
    _bind_correction_run(**kwargs)


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
    persist_execution_profile(
        state / "execution-profiles" / f"{run_id}.json",
        profile_for(run_id),
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


def test_historical_v1_terminal_replay_never_acquires_current_profile(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"
    dispatch_id = "historical-correction-082"
    record_path = state / "correction-dispatches" / (
        hashlib.sha256(dispatch_id.encode("ascii")).hexdigest() + ".json"
    )
    record_path.parent.mkdir(parents=True)
    legacy = {
        "version": 1,
        "correction_dispatch_id": dispatch_id,
        "source_run_id": "RUN-082-000",
        "finding_id": "F1",
        "executor": "codex",
        **approval().__dict__,
        "status": "FAILED",
        "pre_run_ids": [],
        "run_id": None,
        "exit_code": 1,
        "detail": "historical terminal failure",
    }
    record_path.write_text(json.dumps(legacy), encoding="utf-8")

    outcome = _execute_correction_dispatch(
        state_root=state,
        correction_dispatch_id=dispatch_id,
        source_run_id="RUN-082-000",
        finding_id="F1",
        executor="codex",
        approval=approval(),
        invoke_remediation=lambda: pytest.fail("legacy replay invoked REMEDIATION"),
    )

    assert outcome.replayed is True
    assert json.loads(record_path.read_text(encoding="utf-8")) == legacy
    assert not (state / "execution-profiles").exists()


def test_remediation_wakeup_replays_historical_v1_without_resolving_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / ".git" / "aios"
    dispatch_id = "historical-correction-wakeup-082"
    record_path = state / "correction-dispatches" / (
        hashlib.sha256(dispatch_id.encode("ascii")).hexdigest() + ".json"
    )
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps({
        "version": 1,
        "correction_dispatch_id": dispatch_id,
        "source_run_id": "RUN-082-000",
        "finding_id": "F1",
        "executor": "codex",
        **approval().__dict__,
        "status": "FAILED",
        "pre_run_ids": [],
        "run_id": None,
        "exit_code": 1,
        "detail": "historical terminal failure",
    }), encoding="utf-8")
    monkeypatch.setattr(operator_module, "resolve_repository", lambda _repo: tmp_path)
    monkeypatch.setattr(operator_module, "runtime_state_root", lambda _repo: state)
    monkeypatch.setattr(
        operator_module,
        "bind_execution_profile",
        lambda **_kwargs: pytest.fail("historical REMEDIATION resolved defaults"),
    )
    monkeypatch.setattr(
        remote_surface_module,
        "require_current_approval",
        lambda **_kwargs: approval(),
    )
    monkeypatch.setattr(operator_module, "_project_operational_delivery", lambda *_args, **_kwargs: None)

    exit_code = operator_module.main([
        "approved-remediation-wakeup",
        dispatch_id,
        "RUN-082-000",
        "F1",
        "--executor",
        "codex",
        "--repo",
        str(tmp_path),
    ])

    assert exit_code == 1


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

    changed_profile = ResolvedExecutionProfile(
        run_id="AUTHORIZATION",
        executor="codex",
        model="test/codex-future-v2",
        reasoning_effort="low",
        model_source="EXPLICIT",
        effort_source="EXPLICIT",
    )
    with pytest.raises(CorrectionDispatchError, match="profile binding differs"):
        execute_correction_dispatch(
            state_root=state,
            correction_dispatch_id="collision-082",
            source_run_id="RUN-082-000",
            finding_id="F1",
            executor="codex",
            approval=approval(),
            execution_profile=changed_profile,
            invoke_remediation=lambda: pytest.fail(
                "profile collision invoked REMEDIATION"
            ),
        )
    assert record.read_bytes() == before


def test_restart_does_not_attribute_unbound_later_matching_run(
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

    assert replay.status == "RECONCILIATION_BLOCKED"
    assert replay.run_id is None
    assert replay.replayed is True
    assert replay.detail == "correction dispatch has no durably bound REMEDIATION RUN"


def test_restart_reconciles_durably_bound_terminal_run_without_reexecution(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".git" / "aios"

    def bind_then_crash() -> CorrectionInvocation:
        write_run(state, "RUN-082-002", terminal="failures")
        bind_correction_run(
            state_root=state,
            correction_dispatch_id="restart-bound-082",
            run_id="RUN-082-002",
        )
        raise RuntimeError("host lost after binding")

    with pytest.raises(RuntimeError, match="host lost after binding"):
        execute_correction_dispatch(
            state_root=state,
            correction_dispatch_id="restart-bound-082",
            source_run_id="RUN-082-000",
            finding_id="F1",
            executor="codex",
            approval=approval(),
            invoke_remediation=bind_then_crash,
        )
    replay = execute_correction_dispatch(
        state_root=state,
        correction_dispatch_id="restart-bound-082",
        source_run_id="RUN-082-000",
        finding_id="F1",
        executor="codex",
        approval=approval(),
        invoke_remediation=lambda: pytest.fail("restart invoked REMEDIATION"),
    )

    assert replay.status == "FAILED"
    assert replay.run_id == "RUN-082-002"
    assert replay.replayed is True
