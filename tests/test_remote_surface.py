import json
from pathlib import Path

import pytest

from aios_renew.dispatch_reconciliation import DispatchInvocation, execute_dispatch
from aios_renew.remote_surface import RemoteSurfaceError, remote_status


def _write_run(state_root: Path, run_id: str = "RUN-074-001") -> None:
    runs = state_root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "task": {"id": "TASK-074", "revision": 1},
                "executor": "codex",
                "base_sha": "0" * 40,
                "workspace": "ignored-by-status",
                "head_sha": None,
                "status": "ACTIVE",
            }
        ),
        encoding="utf-8",
    )


def test_status_reports_allowlisted_dispatch_and_run_facts_without_mutation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ".git" / "aios"

    def invoke() -> DispatchInvocation:
        from aios_renew.dispatch_reconciliation import bind_dispatch_run

        _write_run(state_root)
        bind_dispatch_run(
            state_root=state_root,
            dispatch_id="delivery-074",
            task_id="TASK-074",
            executor="codex",
            run_id="RUN-074-001",
        )
        results = state_root / "results"
        results.mkdir(parents=True)
        (results / "RUN-074-001.json").write_text("{}", encoding="utf-8")
        return DispatchInvocation(0, "RUN-074-001")

    execute_dispatch(
        state_root=state_root,
        dispatch_id="delivery-074",
        task_id="TASK-074",
        executor="codex",
        invoke_primary=invoke,
    )
    before = {
        path.relative_to(state_root): path.read_bytes()
        for path in state_root.rglob("*")
        if path.is_file()
    }

    summary = remote_status(
        repo=tmp_path, state_root=state_root, dispatch_id="delivery-074"
    )

    assert summary.stored_status == "SUCCEEDED"
    assert summary.run_id == "RUN-074-001"
    assert summary.observed_run_state == "RESULT_AVAILABLE"
    assert "ignored-by-status" not in summary.render()
    after = {
        path.relative_to(state_root): path.read_bytes()
        for path in state_root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_status_missing_or_malformed_dispatch_fails_closed_without_state(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ".git" / "aios"
    with pytest.raises(RemoteSurfaceError, match="status is unavailable"):
        remote_status(repo=tmp_path, state_root=state_root, dispatch_id="missing-074")
    assert not state_root.exists()

    dispatches = state_root / "dispatches"
    dispatches.mkdir(parents=True)
    import hashlib

    key = hashlib.sha256(b"broken-074").hexdigest()
    record = dispatches / f"{key}.json"
    record.write_text('{"version":1,"unexpected":"secret"}', encoding="utf-8")
    before = record.read_bytes()
    with pytest.raises(RemoteSurfaceError, match="status is unavailable"):
        remote_status(repo=tmp_path, state_root=state_root, dispatch_id="broken-074")
    assert record.read_bytes() == before


def test_remote_workflows_are_separate_manual_read_only_surfaces() -> None:
    root = Path(__file__).parents[1]
    status = (root / ".github/workflows/aios-remote-status.yml").read_text(
        encoding="utf-8"
    )
    approval = (root / ".github/workflows/aios-remote-approval.yml").read_text(
        encoding="utf-8"
    )
    for source in (status, approval):
        assert "workflow_dispatch:" in source
        assert "pull_request:" not in source
        assert "push:" not in source
        assert "actions/checkout" not in source
        assert "runs-on: [self-hosted, windows, x64, aios-renew]" in source
        assert "contents: read" in source
    assert status.count("aios remote-status ") == 1
    assert approval.count("aios remote-approve ") == 1
    assert "AIOS_APPROVER: ${{ github.actor }}" in approval
