"""Tests for the native Antigravity+MiniMax (agym) adapter boundary."""

from __future__ import annotations

import inspect
import json
import subprocess
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

    assert cmd[0] == "agym"
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

