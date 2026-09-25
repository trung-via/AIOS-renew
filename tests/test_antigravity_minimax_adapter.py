"""Tests for the native Antigravity+MiniMax (agym) adapter boundary."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from aios_renew import (
    ExecutorBoundary,
    ExecutorBoundaryError,
    ResultPackage,
    Run,
    RunLeaseRegistry,
    parse_remediation,
    parse_review,
    parse_task,
)
from aios_renew.antigravity_minimax_adapter import (
    ANTIGRAVITY_MINIMAX_DEFAULT_MODEL,
    REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH,
    REPAIR_RESULT_PACKAGE_SCHEMA_PATH,
    RESULT_PACKAGE_SCHEMA_PATH,
    AntigravityMinimaxAdapter,
    AntigravityMinimaxExecutionError,
    AntigravityMinimaxOutputError,
    _native_instruction,
    native_instruction,
    resolve_agym_launcher,
    select_agym_launcher,
)
from aios_renew.artifacts import Claim, Result
from aios_renew.dispatcher import NativeExecutionPolicy
from aios_renew.review import Finding, Remediation, RemediationExecution
from aios_renew.task import Task


TASK_SOURCE = """
task_id: TASK-091
revision: 2
goal: Add antigravity-minimax executor.
problem: Test the minimal adapter boundary for agym.
assumptions: []
scope:
  inspect: []
  modify:
    - src/aios_renew/antigravity_minimax_adapter.py
non_goals: []
constraints:
  hard: []
acceptance:
  - id: AC1
    condition: agym transport works.
  - id: AC2
    condition: Envelope normalizes correctly.
verification:
  required:
    - pytest tests/test_antigravity_minimax_adapter.py
"""


def make_execution(
    base_sha: str = "abc123",
    run_id: str = "RUN-091-001",
    workspace: str = "C:/workspace",
) -> tuple[Task, Run, RunLeaseRegistry, ExecutorBoundary]:
    task = parse_task(TASK_SOURCE)
    run = Run.from_task(
        run_id=run_id,
        task=task,
        executor="antigravity-minimax",
        base_sha=base_sha,
        workspace=workspace,
    )
    registry = RunLeaseRegistry()
    return task, run, registry, ExecutorBoundary(registry)


def make_remediation_execution(
    reviewed_sha: str = "abc123",
    run_id: str = "RUN-091-002",
    workspace: str = "C:/workspace",
) -> RemediationExecution:
    task, run, _, _ = make_execution(
        base_sha=reviewed_sha, run_id=run_id, workspace=workspace
    )
    review = parse_review(
        f"""
review_id: REVIEW-091-001
reviewed_sha: {reviewed_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/aios_renew/antigravity_minimax_adapter.py
    issue: Missing implementation.
    expected: Implement adapter.
"""
    )
    return RemediationExecution(
        review_id=review.review_id,
        finding=review.findings[0],
        remediation=parse_remediation(
            f"""
finding_id: F1
action: CODE_FIX
reviewed_sha: {reviewed_sha}
modification_scope: [src/aios_renew/antigravity_minimax_adapter.py]
affected_verification: [pytest tests/test_antigravity_minimax_adapter.py]
"""
        ),
        run=run,
    )


def successful_structural_payload(
    head_sha: str = "def456",
    satisfies: list[str] | None = None,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "result": {
            "head_sha": head_sha,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": satisfies or ["AC1"],
                    "claim": "Adapter completed the task.",
                    "evidence": [],
                }
            ],
            "changed_files": changed_files
            or ["src/aios_renew/antigravity_minimax_adapter.py"],
            "unresolved": [],
        },
        "evidence": [],
    }


def make_agym_envelope(
    *,
    result_package: Mapping[str, Any],
    workspace: str = "C:/workspace",
    head_before: str = "abc123",
    head_after: str = "def456",
    status: str = "PASS",
    exit_code: int = 0,
    state_guard: str = "PASS",
    initial_dirty: bool = False,
    initial_dirty_files: list[str] | None = None,
    changed_files: list[str] | None = None,
    error: str | None = None,
    state_guard_violations: list[str] | None = None,
    model: str = "MiniMax-M3",
    response_str: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "agym.result.v1",
        "agym_version": "0.4.0",
        "status": status,
        "exit_code": exit_code,
        "model": model,
        "workspace": workspace,
        "elapsed_seconds": 1.23,
        "head_before": head_before,
        "head_after": head_after,
        "head_changed": head_before != head_after,
        "initial_dirty": initial_dirty,
        "initial_dirty_files": initial_dirty_files or [],
        "changed_files": changed_files
        or ["diagnostic_file_from_agym_harness.py"],
        "state_guard": state_guard,
        "state_guard_violations": state_guard_violations or [],
        "response": (
            response_str
            if response_str is not None
            else json.dumps(result_package)
        ),
        "structured_response": result_package,
        "error": error,
    }


# ============================================================================
# AC1 / Adapter Identity Tests
# ============================================================================


def test_adapter_identity_and_signature() -> None:
    adapter = AntigravityMinimaxAdapter(transport=lambda **kwargs: {})
    assert adapter.executor == "antigravity-minimax"
    assert list(inspect.signature(adapter.execute).parameters) == ["task", "run"]
    assert list(inspect.signature(adapter.execute_remediation).parameters) == [
        "execution"
    ]
    assert list(inspect.signature(adapter.execute_repair).parameters) == [
        "execution"
    ]


# ============================================================================
# AC3: Command Contract Tests
# ============================================================================


def test_command_contract_primary_flags_and_no_credentials(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    handoff_path = repo / ".git" / "aios" / "handoff.json"
    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=True),
    )
    cmd = adapter.command_for(
        repo=repo,
        instruction="Do work",
        operation="PRIMARY",
        expected_head="abc123",
    )

    assert cmd[0] == resolve_agym_launcher()
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--workspace") + 1] == str(repo)
    assert "--aios-mode" in cmd
    assert cmd[cmd.index("--operation") + 1] == "PRIMARY"
    assert cmd[cmd.index("--response-schema") + 1] == str(
        RESULT_PACKAGE_SCHEMA_PATH
    )
    assert cmd[cmd.index("--model") + 1] == ANTIGRAVITY_MINIMAX_DEFAULT_MODEL
    assert cmd[cmd.index("--expected-head") + 1] == "abc123"
    assert "--allow-commit" in cmd
    assert cmd[cmd.index("--response-timeout") + 1] == "3600"
    assert cmd[cmd.index("--timeout") + 1] == str(65 * 60)

    # Prove no MiniMax credentials or provider URLs in arguments
    joined = " ".join(cmd)
    for forbidden in (
        "minimax_api_key",
        "api.minimax.io",
        "bearer",
        "secret",
        "token",
        "key",
    ):
        assert forbidden not in joined.lower() or forbidden == "key" and "minimax-key" not in joined.lower()
    assert "api.minimax" not in joined


def test_command_contract_mutation_false_omits_allow_commit(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "handoff.json",
        execution_policy=NativeExecutionPolicy(authorizes_mutation=False),
    )
    cmd = adapter.command_for(
        repo=repo, instruction="Read only", operation="PRIMARY"
    )
    assert "--allow-commit" not in cmd


def test_command_contract_remediation_schema_path(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    adapter = AntigravityMinimaxAdapter(repo=repo, handoff_path=repo / "h.json")
    cmd = adapter.command_for(
        repo=repo, instruction="Fix", operation="REMEDIATION"
    )
    assert cmd[cmd.index("--operation") + 1] == "REMEDIATION"
    assert cmd[cmd.index("--response-schema") + 1] == str(
        REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH
    )


def test_command_contract_repair_schema_path(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    adapter = AntigravityMinimaxAdapter(repo=repo, handoff_path=repo / "h.json")
    cmd = adapter.command_for(repo=repo, instruction="Repair", operation="REPAIR")
    assert cmd[cmd.index("--operation") + 1] == "REPAIR"
    assert cmd[cmd.index("--response-schema") + 1] == str(
        REPAIR_RESULT_PACKAGE_SCHEMA_PATH
    )


def test_native_invocation_writes_handoff_excluding_verification(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution(workspace=str(tmp_path.resolve()))
    repo = tmp_path.resolve()
    handoff_path = repo / ".git" / "aios" / "handoff.json"
    calls = []
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
    )

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope),
            stderr="",
        )

    adapter = AntigravityMinimaxAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=True),
    )
    package = adapter.execute(task=task, run=run)

    assert len(calls) == 1
    assert calls[0][1]["cwd"] == str(repo)
    assert calls[0][1]["timeout"] == 65 * 60

    # Verify handoff file was written without Runtime verification
    handoff_content = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert "verification" not in handoff_content["task"]
    assert handoff_content["execution_context"]["selected_executor"] == "antigravity-minimax"
    assert handoff_content["execution_context"]["operation"] == "PRIMARY"
    assert package.result.head_sha == "def456"


# ============================================================================
# AC4: Deterministic Success Fixtures for PRIMARY, REMEDIATION, REPAIR
# ============================================================================


def test_primary_deterministic_success_fixture(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, registry, boundary = make_execution(workspace=str(repo))
    lease = registry.acquire(run)
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "handoff.json",
        transport=lambda **kwargs: envelope,
    )
    package = boundary.invoke(task=task, run=run, lease=lease, adapter=adapter)

    assert isinstance(package, ResultPackage)
    assert package.result.head_sha == "def456"
    assert package.result.claims[0].satisfies == ("AC1",)
    assert package.result.claims[0].evidence == ()
    assert package.evidence == ()


def test_remediation_deterministic_success_fixture(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    execution = make_remediation_execution(workspace=str(repo))
    payload = {
        "result": {
            "head_sha": "def456",
            "claims": [],
            "changed_files": ["src/aios_renew/antigravity_minimax_adapter.py"],
            "unresolved": [],
        },
        "evidence": [],
    }
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=execution.run.base_sha,
        head_after="def456",
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "handoff.json",
        transport=lambda **kwargs: envelope,
    )
    package = adapter.execute_remediation(execution=execution)

    assert package.result.head_sha == "def456"
    assert package.result.claims == ()
    assert package.result.unresolved == ()
    assert package.evidence == ()


@pytest.mark.parametrize(
    ("action", "head_after", "changed_files"),
    [
        ("CODE_FIX", "def456", ["src/fix.py"]),
        ("NO_CHANGE", "abc123", []),
        ("CONTINUE_IMPLEMENTATION", "def456", ["src/unfinished.py"]),
    ],
)
def test_repair_deterministic_success_fixtures_all_three_actions(
    tmp_path: Path,
    action: str,
    head_after: str,
    changed_files: list[str],
) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = {
        "result": {
            "head_sha": head_after,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": f"Repair action {action} completed.",
                    "evidence": [],
                }
            ],
            "changed_files": changed_files,
            "unresolved": [],
        },
        "evidence": [],
    }
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after=head_after,
    )

    repair_execution = {
        "run": run,
        "failed_run_id": "RUN-091-000",
        "failed_head_sha": "abc123",
        "root_base_sha": "abc123",
        "repair": {
            "action": action,
            "instructions": [f"Execute {action}"],
            "modification_scope": changed_files,
        },
    }

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "repair-handoff.json",
        transport=lambda **kwargs: envelope,
    )
    package = adapter.execute_repair(execution=repair_execution)

    assert package.result.head_sha == head_after
    assert package.result.claims[0].satisfies == ("AC1",)
    assert list(package.result.changed_files) == changed_files


def test_repair_instruction_preserves_continue_implementation_semantics(
    tmp_path: Path,
) -> None:
    repo = tmp_path.resolve()
    handoff_path = repo / "repair-handoff.json"
    calls = []
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before="abc123",
        head_after="def456",
    )

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope),
            stderr="",
        )

    task, run, _, _ = make_execution(workspace=str(repo))
    adapter = AntigravityMinimaxAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=True),
    )
    adapter.execute_repair(
        execution={
            "run": run,
            "failed_run_id": "RUN-091-000",
            "failed_head_sha": "abc123",
            "root_base_sha": "abc123",
            "repair": {
                "action": "CONTINUE_IMPLEMENTATION",
                "instructions": ["Resume unfinished work."],
                "modification_scope": ["src/fix.py"],
            },
        }
    )

    instruction = calls[0][calls[0].index("--prompt") + 1]
    assert (
        "CONTINUE_IMPLEMENTATION authorizes mutation to resume the unfinished original TASK implementation"
        in instruction
    )
    assert (
        "necessary bounded repository inspection, discovery, or live capture required by the original TASK is permitted"
        in instruction
    )
    assert (
        "CODE_FIX authorizes mutation only to correct an established defect"
        in instruction
    )
    assert "NO_CHANGE authorizes no repository mutation" in instruction
    assert (
        "Runtime derives and persists canonical result.changed_files"
        in instruction
    )


# ============================================================================
# AC5: Workspace / HEAD Binding and Changed Files Diagnostic Separation
# ============================================================================


def test_agym_changed_files_diagnostics_do_not_alter_result_changed_files(
    tmp_path: Path,
) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(
        head_sha="def456",
        changed_files=["src/aios_renew/antigravity_minimax_adapter.py"],
    )
    # Envelope contains completely different diagnostic changed_files
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
        changed_files=["diagnostics/agym_internal_run.log", "unrelated_tool_trace.json"],
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "handoff.json",
        transport=lambda **kwargs: envelope,
    )
    package = adapter.execute(task=task, run=run)

    # Result.changed_files must be exact payload files, not agym diagnostic files
    assert package.result.changed_files == (
        "src/aios_renew/antigravity_minimax_adapter.py",
    )


# ============================================================================
# AC6: Negative Coverage Fails Closed
# ============================================================================


def test_negative_missing_executable(tmp_path: Path) -> None:
    task, run, _, _ = make_execution(workspace=str(tmp_path.resolve()))
    repo = tmp_path.resolve()

    def runner(command, **kwargs):
        raise FileNotFoundError("No such file or directory: 'agym'")

    adapter = AntigravityMinimaxAdapter(
        runner=runner, repo=repo, handoff_path=repo / "h.json"
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="CLI not found: agym"
    ):
        adapter.execute(task=task, run=run)


def test_negative_watchdog_timeout(tmp_path: Path) -> None:
    task, run, _, _ = make_execution(workspace=str(tmp_path.resolve()))
    repo = tmp_path.resolve()

    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            output=b'{"partial": true}',
            stderr=b"timeout",
        )

    adapter = AntigravityMinimaxAdapter(
        runner=runner, repo=repo, handoff_path=repo / "h.json"
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="response deadline"
    ) as exc_info:
        adapter.execute(task=task, run=run)

    assert exc_info.value.stdout == b'{"partial": true}'
    assert exc_info.value.stderr == b"timeout"


def test_negative_nonzero_process_exit(tmp_path: Path) -> None:
    task, run, _, _ = make_execution(workspace=str(tmp_path.resolve()))
    repo = tmp_path.resolve()

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=1,
            stdout="",
            stderr="agym internal crash",
        )

    adapter = AntigravityMinimaxAdapter(
        runner=runner, repo=repo, handoff_path=repo / "h.json"
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="returned nonzero"
    ):
        adapter.execute(task=task, run=run)


def test_negative_malformed_json(tmp_path: Path) -> None:
    task, run, _, _ = make_execution(workspace=str(tmp_path.resolve()))
    repo = tmp_path.resolve()

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=0, stdout="not-json", stderr=""
        )

    adapter = AntigravityMinimaxAdapter(
        runner=runner, repo=repo, handoff_path=repo / "h.json"
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="malformed terminal JSON"
    ):
        adapter.execute(task=task, run=run)


def test_negative_wrong_schema(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
    )
    envelope["schema_version"] = "agym.result.v999"

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="unsupported schema version"
    ):
        adapter.execute(task=task, run=run)


def test_negative_pass_exit_code_disagreement(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
        status="PASS",
        exit_code=1,  # Disagreement!
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="PASS/exit-code disagreement"
    ):
        adapter.execute(task=task, run=run)


def test_negative_dirty_refusal(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
        status="DIRTY_WORKSPACE_REFUSED",
        initial_dirty=True,
        initial_dirty_files=["dirty.txt"],
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="pre-existing dirty workspace"
    ):
        adapter.execute(task=task, run=run)


def test_negative_state_guard_violation(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
        status="STATE_GUARD_VIOLATION",
        state_guard="VIOLATION",
        state_guard_violations=["pre-existing dirty file was changed"],
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="State Guard violation"
    ):
        adapter.execute(task=task, run=run)


def test_negative_workspace_mismatch(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace="C:/some/other/workspace",
        head_before=run.base_sha,
        head_after="def456",
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="workspace mismatch"
    ):
        adapter.execute(task=task, run=run)


def test_negative_starting_head_mismatch(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before="wrong_starting_head",
        head_after="def456",
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="starting HEAD mismatch"
    ):
        adapter.execute(task=task, run=run)


def test_negative_final_head_mismatch(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="different_head",  # Disagrees with result.head_sha def456
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="final HEAD mismatch"
    ):
        adapter.execute(task=task, run=run)


@pytest.mark.parametrize(
    "bad_response",
    [
        "",
        "   ",
        "```json\n{}\n```",
        "Here is the result: {}",
        '{"result": {}} and some trailing prose',
    ],
)
def test_negative_missing_or_prose_wrapped_response(
    tmp_path: Path, bad_response: str
) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    envelope = make_agym_envelope(
        result_package={},
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
        response_str=bad_response,
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
    )
    with pytest.raises(AntigravityMinimaxExecutionError):
        adapter.execute(task=task, run=run)


def test_negative_structurally_invalid_result_package(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    # Missing result and evidence fields
    bad_payload = {"some_other_field": True}
    envelope = make_agym_envelope(
        result_package=bad_payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
    )

    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
    )
    with pytest.raises(AntigravityMinimaxOutputError):
        adapter.execute(task=task, run=run)


def test_negative_claims_referencing_non_empty_evidence_in_canonical_mode(
    tmp_path: Path,
) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")
    # In canonical validation mode (structural=False), non-empty evidence is validated
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
    )
    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: envelope,
        structural_output=False,
    )
    # Claims have empty evidence, but canonical validation requires evidence
    with pytest.raises(AntigravityMinimaxOutputError):
        adapter.execute(task=task, run=run)


def test_negative_zero_exit_bare_result_package_rejected(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")

    # Zero-exit bare ResultPackage without agym.result.v1 envelope fails closed
    def bare_runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    bare_adapter = AntigravityMinimaxAdapter(
        runner=bare_runner,
        repo=repo,
        handoff_path=repo / "h.json",
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="envelope schema"
    ):
        bare_adapter.execute(task=task, run=run)

    # Bare ResultPackage passed via transport seam also fails closed
    bare_transport_adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: payload,
    )
    with pytest.raises(
        AntigravityMinimaxExecutionError, match="envelope schema"
    ):
        bare_transport_adapter.execute(task=task, run=run)

    # Bare ResultPackage instance passed via transport seam also fails closed
    bare_instance_adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=repo / "h.json",
        transport=lambda **kwargs: ResultPackage(
            result=Result(
                head_sha="def456",
                claims=(),
                changed_files=("src/aios_renew/antigravity_minimax_adapter.py",),
                unresolved=(),
            ),
            evidence=(),
        ),
    )
    with pytest.raises(AntigravityMinimaxExecutionError):
        bare_instance_adapter.execute(task=task, run=run)

    # Valid envelope success remains accepted
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
    )

    def envelope_runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope),
            stderr="",
        )

    valid_adapter = AntigravityMinimaxAdapter(
        runner=envelope_runner,
        repo=repo,
        handoff_path=repo / "h.json",
    )
    package = valid_adapter.execute(task=task, run=run)
    assert package.result.head_sha == "def456"


# ============================================================================
# TASK-131: Platform-Aware Launcher Selection and Subprocess Regressions
# ============================================================================


def test_platform_aware_launcher_selection_and_command_args(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    adapter = AntigravityMinimaxAdapter(repo=repo, handoff_path=repo / "h.json")

    # AC1: Platform-aware launcher selection returns agym.cmd for Windows and agym for non-Windows
    assert resolve_agym_launcher("win32") == "agym.cmd"
    assert resolve_agym_launcher("windows") == "agym.cmd"
    assert resolve_agym_launcher("Windows") == "agym.cmd"
    assert resolve_agym_launcher("linux") == "agym"
    assert resolve_agym_launcher("darwin") == "agym"
    assert resolve_agym_launcher() == (
        "agym.cmd" if sys.platform == "win32" else "agym"
    )

    # select_agym_launcher alias matches resolve_agym_launcher
    assert select_agym_launcher("win32") == "agym.cmd"
    assert select_agym_launcher("linux") == "agym"
    assert select_agym_launcher() == resolve_agym_launcher()

    # command_for uses that selected launcher as argv[0]
    cmd_win = adapter.command_for(
        repo=repo, instruction="Do work", operation="PRIMARY", platform="win32"
    )
    cmd_linux = adapter.command_for(
        repo=repo, instruction="Do work", operation="PRIMARY", platform="linux"
    )
    assert cmd_win[0] == "agym.cmd"
    assert cmd_linux[0] == "agym"

    # Preserves all existing provider-neutral arguments unchanged
    assert cmd_win[1:] == cmd_linux[1:]
    assert cmd_win[cmd_win.index("--prompt") + 1] == "Do work"
    assert cmd_win[cmd_win.index("--output-format") + 1] == "json"
    assert cmd_win[cmd_win.index("--workspace") + 1] == str(repo)
    assert "--aios-mode" in cmd_win
    assert cmd_win[cmd_win.index("--operation") + 1] == "PRIMARY"
    assert cmd_win[cmd_win.index("--response-schema") + 1] == str(
        RESULT_PACKAGE_SCHEMA_PATH
    )
    assert cmd_win[cmd_win.index("--model") + 1] == ANTIGRAVITY_MINIMAX_DEFAULT_MODEL
    assert cmd_win[cmd_win.index("--response-timeout") + 1] == "3600"
    assert cmd_win[cmd_win.index("--timeout") + 1] == str(65 * 60)
    assert "--allow-commit" in cmd_win

    # Custom launcher override via adapter init and command_for
    adapter_custom = AntigravityMinimaxAdapter(
        repo=repo, handoff_path=repo / "h.json", launcher="custom_agym.cmd"
    )
    assert adapter_custom.command_for(repo=repo, instruction="Work")[0] == (
        "custom_agym.cmd"
    )
    assert adapter.command_for(
        repo=repo, instruction="Work", launcher="override.cmd"
    )[0] == "override.cmd"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only subprocess test")
def test_windows_deterministic_real_cmd_subprocess_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    task, run, _, _ = make_execution(workspace=str(repo))
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
    )

    envelope_path = bin_dir / "envelope.json"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")

    # Create temporary .cmd launcher that types the pre-baked envelope
    cmd_launcher = bin_dir / "agym.cmd"
    cmd_launcher.write_text(
        f'@echo off\ntype "{envelope_path}"\n',
        encoding="utf-8",
    )

    # Prepend bin_dir to PATH so agym.cmd is resolved by Python subprocess
    monkeypatch.setenv(
        "PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    )

    # 1. Direct Python subprocess.run with shell=False starts agym.cmd successfully
    launcher_name = resolve_agym_launcher()
    assert launcher_name == "agym.cmd"

    direct_proc = subprocess.run(
        [launcher_name, "--prompt", "test"],
        cwd=str(repo),
        capture_output=True,
        text=False,
        check=False,
        shell=False,
    )
    assert direct_proc.returncode == 0
    direct_envelope = json.loads(direct_proc.stdout.decode("utf-8"))
    assert direct_envelope["schema_version"] == "agym.result.v1"
    assert direct_envelope["status"] == "PASS"

    # 2. Production AntigravityMinimaxAdapter with default runner (subprocess.run) and shell=False
    handoff_path = repo / ".git" / "aios" / "handoff.json"
    adapter = AntigravityMinimaxAdapter(
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=True),
    )
    result_package = adapter.execute(task=task, run=run)

    assert isinstance(result_package, ResultPackage)
    assert result_package.result.head_sha == "def456"
    assert result_package.result.claims[0].satisfies == ("AC1",)
    assert result_package.evidence == ()


def test_native_invocation_runner_contract_and_missing_launcher_fail_closed(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution(workspace=str(tmp_path.resolve()))
    repo = tmp_path.resolve()
    calls: list[tuple[Any, dict[str, Any]]] = []

    def runner(
        command: tuple[str, ...], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        raise FileNotFoundError("Launcher not found")

    adapter = AntigravityMinimaxAdapter(
        runner=runner,
        repo=repo,
        handoff_path=repo / "h.json",
        execution_policy=NativeExecutionPolicy(
            authorizes_mutation=True,
            response_budget_minutes=45,
            process_watchdog_seconds=50 * 60,
        ),
    )

    with pytest.raises(
        AntigravityMinimaxExecutionError, match="CLI not found: agym"
    ) as exc_info:
        adapter.execute(task=task, run=run)

    expected_launcher = resolve_agym_launcher()
    assert str(exc_info.value) == f"Antigravity MiniMax CLI not found: {expected_launcher}"

    # AC3: Missing-launcher failure remains fail-closed with no second invocation
    assert len(calls) == 1

    command_tuple, kwargs = calls[0]
    # AC3: Injected runner receives exact command tuple, preserves cwd/capture/text/check/watchdog
    assert isinstance(command_tuple, tuple)
    assert command_tuple[0] == expected_launcher
    assert kwargs["cwd"] == str(repo)
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is False
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 50 * 60


# ============================================================================
# TASK-132: Canonical Builtin finish Terminal Contract Regressions
# ============================================================================


def _assert_valid_terminal_contract(instruction: str, operation: str) -> None:
    """Validate that native instruction strictly satisfies the hardened finish terminal contract."""
    # Must explicitly name the builtin `finish` tool as the only successful terminal action
    assert "builtin `finish` tool exactly once" in instruction
    assert "only successful terminal action" in instruction

    # Must require satisfying the supplied response schema
    assert "satisfying the supplied response schema" in instruction

    # Must forbid conversational completion prose, markdown, summaries, or second response
    assert "conversational terminal prose" in instruction
    assert "markdown" in instruction
    assert "summaries" in instruction
    assert "second terminal response" in instruction

    # Must bind result.head_sha to actual final Git HEAD
    assert "Bind result.head_sha to actual final Git HEAD" in instruction

    # Must forbid pushing
    assert "do not push" in instruction or "Do not push" in instruction

    # Must not contain generic prose-only completion wording
    assert "return the structural ResultPackage as the only response" not in instruction

    if operation == "PRIMARY":
        assert "Complete all authorized implementation work and required commit completion first" in instruction
        assert "zero-mutation actions must not create a commit merely to satisfy terminal mechanics" in instruction
        assert "Root evidence and every claim.evidence must be empty" in instruction
        assert "Runtime constructs canonical EVIDENCE" in instruction
        assert "Every claim.satisfies entry must be a known TASK acceptance ID" in instruction
    elif operation == "REMEDIATION":
        assert "Complete all authorized remediation work and required commit completion first" in instruction
        assert "For CODE_FIX, commit the permitted remediation delta before invoking `finish`" in instruction
        assert "for EVIDENCE_ONLY, do not create a code commit" in instruction
        assert "Zero-mutation actions must not create a commit merely to satisfy terminal mechanics" in instruction
        assert "Root evidence, result.claims, and result.unresolved must be empty" in instruction
    elif operation == "REPAIR":
        assert "Complete all authorized repair work and required commit completion first" in instruction
        assert "For CODE_FIX and CONTINUE_IMPLEMENTATION, commit the final permitted repository state" in instruction
        assert "for NO_CHANGE, do not create a code commit" in instruction
        assert "Zero-mutation actions must not create a commit merely to satisfy terminal mechanics" in instruction
        assert "Root evidence and every claim.evidence must be empty" in instruction
        assert "Do not create or restart a fresh PRIMARY lineage" in instruction
        assert "retry this admitted continuation" in instruction
        assert "reroute or fall back to another Executor" in instruction
        assert "widen scope" in instruction


def test_task132_primary_instruction_terminal_contract(tmp_path: Path) -> None:
    handoff_path = tmp_path / "handoff.json"
    instruction = _native_instruction(operation="PRIMARY", handoff_path=handoff_path)
    assert instruction == native_instruction(operation="PRIMARY", handoff_path=handoff_path)

    _assert_valid_terminal_contract(instruction, "PRIMARY")
    assert str(handoff_path) in instruction
    assert "Runtime owns canonical verification" in instruction


def test_task132_remediation_instruction_terminal_contract(tmp_path: Path) -> None:
    handoff_path = tmp_path / "remediation_handoff.json"
    instruction = _native_instruction(operation="REMEDIATION", handoff_path=handoff_path)

    _assert_valid_terminal_contract(instruction, "REMEDIATION")
    assert str(handoff_path) in instruction
    assert "Runtime owns affected verification" in instruction
    assert "remediation.modification_scope" in instruction


def test_task132_repair_instruction_terminal_contract(tmp_path: Path) -> None:
    handoff_path = tmp_path / "repair_handoff.json"
    instruction = _native_instruction(operation="REPAIR", handoff_path=handoff_path)

    _assert_valid_terminal_contract(instruction, "REPAIR")
    assert str(handoff_path) in instruction
    assert "CODE_FIX authorizes mutation only to correct an established defect" in instruction
    assert "NO_CHANGE authorizes no repository mutation" in instruction
    assert "CONTINUE_IMPLEMENTATION authorizes mutation to resume the unfinished original TASK implementation" in instruction
    assert "Runtime owns complete original TASK verification" in instruction


def test_task132_terminal_contract_fails_if_weakened_to_generic_prose(tmp_path: Path) -> None:
    handoff_path = tmp_path / "handoff.json"
    canonical = _native_instruction(operation="PRIMARY", handoff_path=handoff_path)

    # 1. Canonical instruction passes contract validation
    _assert_valid_terminal_contract(canonical, "PRIMARY")

    # 2. Legacy prompt with generic "return the structural ResultPackage as the only response" fails
    legacy_weakened = canonical.replace(
        "Complete all authorized implementation work and required commit completion first; "
        "zero-mutation actions must not create a commit merely to satisfy terminal "
        "mechanics. Do not push. Obtain final Git HEAD, then invoke the builtin "
        "`finish` tool exactly once satisfying the supplied response schema as the "
        "only successful terminal action. Do not emit conversational terminal prose, "
        "markdown, summaries, or a second terminal response before or after `finish`. "
        "Bind result.head_sha to actual final Git HEAD.",
        "Commit the final implementation state when required; do not push. Obtain "
        "final Git HEAD, and return the structural ResultPackage as the only response.",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(legacy_weakened, "PRIMARY")

    # 3. Omitting the finish tool naming fails
    no_finish = canonical.replace("builtin `finish` tool exactly once", "model output")
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_finish, "PRIMARY")

    # 4. Omitting the conversational terminal prose prohibition fails
    no_prose_ban = canonical.replace(
        "Do not emit conversational terminal prose, markdown, summaries, or a second terminal response before or after `finish`.",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_prose_ban, "PRIMARY")

    # 5. Omitting commit completion first rule fails
    no_commit_rule = canonical.replace(
        "Complete all authorized implementation work and required commit completion first; ",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_commit_rule, "PRIMARY")

    # 6. Omitting actual final Git HEAD binding fails
    no_head_binding = canonical.replace(
        "Bind result.head_sha to actual final Git HEAD.",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_head_binding, "PRIMARY")

    # 7. Omitting empty evidence semantics fails
    no_evidence_rule = canonical.replace(
        "Root evidence and every claim.evidence must be empty; Runtime constructs canonical EVIDENCE.",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_evidence_rule, "PRIMARY")


def test_task132_command_contract_single_invocation_retains_all_options_and_no_fallback(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution(workspace=str(tmp_path.resolve()))
    repo = tmp_path.resolve()
    handoff_path = repo / ".git" / "aios" / "handoff.json"
    calls: list[tuple[Any, dict[str, Any]]] = []
    payload = successful_structural_payload(head_sha="def456")
    envelope = make_agym_envelope(
        result_package=payload,
        workspace=str(repo),
        head_before=run.base_sha,
        head_after="def456",
    )

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope),
            stderr="",
        )

    adapter = AntigravityMinimaxAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(
            authorizes_mutation=True,
            response_budget_minutes=60,
            process_watchdog_seconds=65 * 60,
        ),
    )
    package = adapter.execute(task=task, run=run)

    # Exactly ONE native invocation, no second call, no fallback
    assert len(calls) == 1
    cmd, kwargs = calls[0]

    # Launcher matches platform-aware launcher
    assert cmd[0] == resolve_agym_launcher()

    # --prompt contains the hardened finish instruction
    prompt_idx = cmd.index("--prompt")
    instruction = cmd[prompt_idx + 1]
    _assert_valid_terminal_contract(instruction, "PRIMARY")

    # Required CLI flags and options preserved
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--workspace") + 1] == str(repo)
    assert "--aios-mode" in cmd
    assert cmd[cmd.index("--operation") + 1] == "PRIMARY"
    assert cmd[cmd.index("--response-schema") + 1] == str(RESULT_PACKAGE_SCHEMA_PATH)
    assert cmd[cmd.index("--model") + 1] == ANTIGRAVITY_MINIMAX_DEFAULT_MODEL
    assert cmd[cmd.index("--expected-head") + 1] == run.base_sha
    assert "--allow-commit" in cmd
    assert cmd[cmd.index("--response-timeout") + 1] == "3600"
    assert cmd[cmd.index("--timeout") + 1] == str(65 * 60)
    assert kwargs["cwd"] == str(repo)

    # ResultPackage normalized properly
    assert package.result.head_sha == "def456"
    assert package.result.claims[0].satisfies == ("AC1",)
    assert package.evidence == ()


def test_task132_remediation_and_repair_invocations_pass_hardened_instruction(
    tmp_path: Path,
) -> None:
    repo = tmp_path.resolve()
    calls: list[tuple[Any, dict[str, Any]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        payload = successful_structural_payload(head_sha="def456")
        envelope = make_agym_envelope(
            result_package=payload,
            workspace=str(repo),
            head_before="abc123",
            head_after="def456",
        )
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope),
            stderr="",
        )

    # 1. REMEDIATION invocation passes hardened instruction
    execution = make_remediation_execution(workspace=str(repo))
    adapter_remediation = AntigravityMinimaxAdapter(
        runner=runner,
        repo=repo,
        handoff_path=repo / "rem_handoff.json",
        execution_policy=NativeExecutionPolicy(authorizes_mutation=True),
    )
    adapter_remediation.execute_remediation(execution=execution)

    assert len(calls) == 1
    rem_cmd, _ = calls[0]
    rem_instruction = rem_cmd[rem_cmd.index("--prompt") + 1]
    _assert_valid_terminal_contract(rem_instruction, "REMEDIATION")
    assert rem_cmd[rem_cmd.index("--operation") + 1] == "REMEDIATION"
    assert rem_cmd[rem_cmd.index("--response-schema") + 1] == str(
        REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH
    )

    # 2. REPAIR invocation passes hardened instruction
    calls.clear()
    task, run, _, _ = make_execution(workspace=str(repo))
    adapter_repair = AntigravityMinimaxAdapter(
        runner=runner,
        repo=repo,
        handoff_path=repo / "rep_handoff.json",
        execution_policy=NativeExecutionPolicy(authorizes_mutation=True),
    )
    repair_execution = {
        "run": run,
        "failed_run_id": "RUN-091-000",
        "failed_head_sha": "abc123",
        "root_base_sha": "abc123",
        "repair": {
            "action": "CONTINUE_IMPLEMENTATION",
            "instructions": ["Resume work."],
            "modification_scope": ["src/fix.py"],
        },
    }
    adapter_repair.execute_repair(execution=repair_execution)

    assert len(calls) == 1
    rep_cmd, _ = calls[0]
    rep_instruction = rep_cmd[rep_cmd.index("--prompt") + 1]
    _assert_valid_terminal_contract(rep_instruction, "REPAIR")
    assert rep_cmd[rep_cmd.index("--operation") + 1] == "REPAIR"
    assert rep_cmd[rep_cmd.index("--response-schema") + 1] == str(
        REPAIR_RESULT_PACKAGE_SCHEMA_PATH
    )


# ============================================================================
# TASK-139: Deterministic Canonical Identity and Remote Carrier Invariance
# ============================================================================


def test_importing_adapter_performs_no_mutation_of_canonical_or_remote_allowlists() -> None:
    code = """
import sys
from aios_renew import (
    run,
    dispatch_reconciliation,
    correction_dispatch,
    repair_dispatch,
    github_issue_wakeup,
    github_issue_remediation_intent,
    github_issue_repair_wakeup,
)

# Baseline allowlists before importing adapter
run_executors_before = frozenset(run.SUPPORTED_EXECUTORS)
dispatch_executors_before = frozenset(dispatch_reconciliation.SUPPORTED_EXECUTORS)
correction_executors_before = frozenset(correction_dispatch.SUPPORTED_EXECUTORS)
repair_executors_before = frozenset(repair_dispatch.SUPPORTED_EXECUTORS)
issue_primary_before = frozenset(github_issue_wakeup.SUPPORTED_EXECUTORS)
issue_remediation_before = frozenset(github_issue_remediation_intent.SUPPORTED_EXECUTORS)
issue_repair_before = frozenset(github_issue_repair_wakeup.SUPPORTED_EXECUTORS)

assert run_executors_before == frozenset({"codex", "antigravity", "antigravity-minimax"})
assert dispatch_executors_before == frozenset({"codex", "antigravity"})
assert correction_executors_before == frozenset({"codex", "antigravity"})
assert repair_executors_before == frozenset({"codex", "antigravity"})
assert issue_primary_before == frozenset({"codex", "antigravity"})
assert issue_remediation_before == frozenset({"codex", "antigravity"})
assert issue_repair_before == frozenset({"codex", "antigravity"})

# Import adapter
import aios_renew.antigravity_minimax_adapter as adapter

# Check allowlists after importing adapter
assert run.SUPPORTED_EXECUTORS == run_executors_before
assert dispatch_reconciliation.SUPPORTED_EXECUTORS == dispatch_executors_before
assert correction_dispatch.SUPPORTED_EXECUTORS == correction_executors_before
assert repair_dispatch.SUPPORTED_EXECUTORS == repair_executors_before
assert github_issue_wakeup.SUPPORTED_EXECUTORS == issue_primary_before
assert github_issue_remediation_intent.SUPPORTED_EXECUTORS == issue_remediation_before
assert github_issue_repair_wakeup.SUPPORTED_EXECUTORS == issue_repair_before

# Also verify that remote carrier allowlists do NOT contain antigravity-minimax
assert "antigravity-minimax" not in dispatch_reconciliation.SUPPORTED_EXECUTORS
assert "antigravity-minimax" not in correction_dispatch.SUPPORTED_EXECUTORS
assert "antigravity-minimax" not in repair_dispatch.SUPPORTED_EXECUTORS
assert "antigravity-minimax" not in github_issue_wakeup.SUPPORTED_EXECUTORS
assert "antigravity-minimax" not in github_issue_remediation_intent.SUPPORTED_EXECUTORS
assert "antigravity-minimax" not in github_issue_repair_wakeup.SUPPORTED_EXECUTORS
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0


def test_remote_carrier_allowlists_remain_codex_and_antigravity_only() -> None:
    from aios_renew import (
        correction_dispatch,
        dispatch_reconciliation,
        repair_dispatch,
        run,
    )
    from aios_renew import (
        github_issue_remediation_intent,
        github_issue_repair_wakeup,
        github_issue_wakeup,
    )

    assert run.SUPPORTED_EXECUTORS == frozenset(
        {"codex", "antigravity", "antigravity-minimax"}
    )
    assert dispatch_reconciliation.SUPPORTED_EXECUTORS == frozenset(
        {"codex", "antigravity"}
    )
    assert correction_dispatch.SUPPORTED_EXECUTORS == frozenset(
        {"codex", "antigravity"}
    )
    assert repair_dispatch.SUPPORTED_EXECUTORS == frozenset(
        {"codex", "antigravity"}
    )
    assert github_issue_wakeup.SUPPORTED_EXECUTORS == frozenset(
        {"codex", "antigravity"}
    )
    assert github_issue_remediation_intent.SUPPORTED_EXECUTORS == frozenset(
        {"codex", "antigravity"}
    )
    assert github_issue_repair_wakeup.SUPPORTED_EXECUTORS == frozenset(
        {"codex", "antigravity"}
    )


def test_github_issue_remote_carriers_reject_antigravity_minimax() -> None:
    import yaml
    from aios_renew.execution_profile import parse_execution_profile_policy
    from aios_renew.github_issue_wakeup import (
        GitHubIssueWakeupError,
        parse_request as parse_primary_request,
    )
    from aios_renew.github_issue_remediation_intent import (
        GitHubIssueRemediationIntentError,
        parse_request as parse_remediation_request,
    )
    from aios_renew.github_issue_repair_wakeup import (
        GitHubIssueRepairWakeupError,
        parse_request as parse_repair_request,
    )

    # Keep profile authority inside this test; no repository or package discovery.
    profile_policy = parse_execution_profile_policy(
        {
            "format": "AIOS_EXECUTOR_PROFILES_POLICY",
            "version": 1,
            "executors": {
                executor: {
                    "default_model": f"test-{executor}-model",
                    "default_reasoning_effort": "test-effort",
                    "supported_reasoning_efforts": ["test-effort"],
                }
                for executor in ("codex", "antigravity")
            },
        }
    )

    primary_request = {
        "format": "AIOS_PRIMARY_WAKEUP_REQUEST",
        "version": 3,
        "dispatch_id": "brain-wakeup-139",
        "task_id": "TASK-139",
        "task_revision": 1,
        "task_blob_sha": "a" * 40,
        "task_commit_sha": "b" * 40,
        "executor": "codex",
        "model": None,
        "reasoning_effort": None,
    }
    primary = parse_primary_request(
        yaml.safe_dump(primary_request), profile_policy=profile_policy
    )
    assert (primary.executor, primary.model, primary.reasoning_effort) == (
        "codex", "test-codex-model", "test-effort"
    )
    assert (primary.model_source, primary.effort_source) == (
        "REPOSITORY_DEFAULT", "REPOSITORY_DEFAULT"
    )
    with pytest.raises(GitHubIssueWakeupError, match="^unsupported executor$"):
        parse_primary_request(
            yaml.safe_dump({**primary_request, "executor": "antigravity-minimax"}),
            profile_policy=profile_policy,
        )

    remediation_request = {
        "format": "AIOS_REMEDIATION_INTENT_REQUEST",
        "version": 2,
        "correction_dispatch_id": "rem-139",
        "source_run_id": "RUN-139-001",
        "finding_id": "F1",
        "executor": "codex",
        "model": None,
        "reasoning_effort": None,
    }
    remediation = parse_remediation_request(
        yaml.safe_dump(remediation_request), profile_policy=profile_policy
    )
    assert (remediation.executor, remediation.model, remediation.reasoning_effort) == (
        "codex", "test-codex-model", "test-effort"
    )
    assert (remediation.model_source, remediation.effort_source) == (
        "REPOSITORY_DEFAULT", "REPOSITORY_DEFAULT"
    )
    with pytest.raises(
        GitHubIssueRemediationIntentError, match="^unsupported executor$"
    ):
        parse_remediation_request(
            yaml.safe_dump({**remediation_request, "executor": "antigravity-minimax"}),
            profile_policy=profile_policy,
        )

    repair_request = {
        "format": "AIOS_REPAIR_WAKEUP_REQUEST",
        "version": 2,
        "repair_dispatch_id": "rep-139",
        "failed_run_id": "RUN-139-001",
        "repair_sha": "a" * 40,
        "executor": "codex",
        "model": None,
        "reasoning_effort": None,
    }
    repair = parse_repair_request(
        yaml.safe_dump(repair_request), profile_policy=profile_policy
    )
    assert (repair.executor, repair.model, repair.reasoning_effort) == (
        "codex", "test-codex-model", "test-effort"
    )
    assert (repair.model_source, repair.effort_source) == (
        "REPOSITORY_DEFAULT", "REPOSITORY_DEFAULT"
    )
    with pytest.raises(GitHubIssueRepairWakeupError, match="^unsupported executor$"):
        parse_repair_request(
            yaml.safe_dump({**repair_request, "executor": "antigravity-minimax"}),
            profile_policy=profile_policy,
        )
