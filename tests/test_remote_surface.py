import json
from pathlib import Path
import re

import pytest
import yaml

from aios_renew.dispatch_reconciliation import DispatchInvocation, execute_dispatch
from aios_renew.execution_profile import ResolvedExecutionProfile, persist_execution_profile
from aios_renew.remote_surface import RemoteSurfaceError, remote_status


TASK_REVISION = 1
TASK_BLOB_SHA = "a" * 40
TASK_COMMIT_SHA = "b" * 40


def _workflow_run_statements(source: str) -> list[str]:
    """Inspect PowerShell run blocks without YAML metadata or comments."""
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    statements = []
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            for line in step.get("run", "").splitlines():
                quote = None
                start = 0
                for index, char in enumerate(line):
                    if char in ("'", '"'):
                        if quote == char:
                            quote = None
                        elif quote is None:
                            quote = char
                    elif quote is None and char == "#":
                        line = line[:index]
                        break
                    elif quote is None and char in ";|{}":
                        statements.append(line[start:index].strip())
                        start = index + 1
                statements.append(line[start:].strip())
    return [statement for statement in statements if statement]


def _synthetic_profile(run_id: str) -> ResolvedExecutionProfile:
    return ResolvedExecutionProfile(
        run_id=run_id,
        executor="codex",
        model="test/remote-status-fixture-v1",
        reasoning_effort="low",
        model_source="EXPLICIT",
        effort_source="EXPLICIT",
    )


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
        run_profile = _synthetic_profile("RUN-074-001")
        persist_execution_profile(
            state_root / "execution-profiles" / "RUN-074-001.json", run_profile
        )
        bind_dispatch_run(
            state_root=state_root,
            dispatch_id="delivery-074",
            task_id="TASK-074",
            executor="codex",
            run_id="RUN-074-001",
            task_revision=TASK_REVISION,
            task_blob_sha=TASK_BLOB_SHA,
            task_commit_sha=TASK_COMMIT_SHA,
            execution_profile=run_profile,
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
        task_revision=TASK_REVISION,
        task_blob_sha=TASK_BLOB_SHA,
        task_commit_sha=TASK_COMMIT_SHA,
        execution_profile=_synthetic_profile("AUTHORIZATION"),
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
    dispatch_record = next((state_root / "dispatches").glob("*.json"))
    dispatch_data = json.loads(dispatch_record.read_text(encoding="utf-8"))
    assert dispatch_data["version"] == 3
    assert (
        dispatch_data["task_revision"],
        dispatch_data["task_blob_sha"],
        dispatch_data["task_commit_sha"],
    ) == (TASK_REVISION, TASK_BLOB_SHA, TASK_COMMIT_SHA)
    assert (
        dispatch_data["executor"],
        dispatch_data["model"],
        dispatch_data["reasoning_effort"],
        dispatch_data["model_source"],
        dispatch_data["effort_source"],
    ) == (
        "codex",
        "test/remote-status-fixture-v1",
        "low",
        "EXPLICIT",
        "EXPLICIT",
    )
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
    correction = (
        root / ".github/workflows/aios-approved-remediation-wakeup.yml"
    ).read_text(encoding="utf-8")
    for source in (status, approval, correction):
        assert "workflow_dispatch:" in source
        assert "pull_request:" not in source
        assert "push:" not in source
        assert "actions/checkout" not in source
        assert "runs-on: [self-hosted, windows, x64, aios-renew]" in source
        assert "contents: read" in source
    expected_invocations = (
        (
            status,
            "remote-status",
            r"python\s+\$entry\s+\$controlSource\s+remote-status\s+"
            r"\$env:AIOS_DISPATCH_ID\s+--repo\s+\$env:AIOS_REPO_ROOT",
        ),
        (
            approval,
            "remote-approve",
            r"python\s+\$entry\s+\$controlSource\s+remote-approve\s+"
            r"\$env:AIOS_SOURCE_RUN_ID\s+\$env:AIOS_FINDING_ID\s+"
            r"--approver\s+\$env:AIOS_APPROVER\s+--repo\s+\$env:AIOS_REPO_ROOT",
        ),
        (
            correction,
            "approved-remediation-wakeup",
            r"python\s+\$entry\s+\$controlSource\s+approved-remediation-wakeup\s+"
            r"\$env:AIOS_CORRECTION_DISPATCH_ID\s+\$env:AIOS_SOURCE_RUN_ID\s+"
            r"\$env:AIOS_FINDING_ID\b.*\s+--repo\s+\$env:AIOS_REPO_ROOT",
        ),
    )
    bounded_operations = r"remote-status|remote-approve|approved-remediation-wakeup"
    for source, operation, invocation in expected_invocations:
        statements = _workflow_run_statements(source)
        assert "$controlSource = $env:AIOS_CONTROL_ROOT" in statements
        assert "$entry = Join-Path $controlSource 'scripts/aios_control_entry.py'" in statements
        delegated = [
            statement
            for statement in statements
            if re.match(
                rf"^python\s+\$entry\s+\$controlSource\s+(?:{bounded_operations})\b",
                statement,
                flags=re.IGNORECASE,
            )
        ]
        assert len(delegated) == 1
        assert re.fullmatch(invocation, delegated[0]) is not None, operation
        assert not any(
            re.match(
                rf"^(?:&\s*)?aios(?:\.exe)?\s+(?:{bounded_operations})\b",
                statement,
                flags=re.IGNORECASE,
            )
            for statement in statements
        ), operation
    assert "AIOS_APPROVER: ${{ github.actor }}" in approval
