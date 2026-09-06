"""Contract regression tests for A1 GitHub Actions Self-hosted Wakeup."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = Path(".github/workflows/aios-self-hosted-wakeup.yml")
README_PATH = Path("README.md")
ROADMAP_PATH = Path("docs/AIOS-ROADMAP.md")


def load_workflow() -> dict:
    assert WORKFLOW_PATH.is_file(), f"workflow file missing: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def run_powershell_script(
    script: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as f:
        f.write(script)
        temp_path = Path(f.name)
    try:
        return subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(temp_path),
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_workflow_dispatch_only_and_inputs_ac1() -> None:
    workflow = load_workflow()
    raw_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    triggers = workflow.get("on", workflow.get(True, {}))
    assert isinstance(triggers, dict), "trigger must be a mapping"
    assert list(triggers.keys()) == [
        "workflow_dispatch"
    ], f"expected only workflow_dispatch trigger, got {list(triggers.keys())}"

    # Verify no untrusted triggers exist
    for forbidden in (
        "push",
        "pull_request",
        "pull_request_target",
        "issue_comment",
        "issues",
        "schedule",
        "repository_dispatch",
    ):
        assert forbidden not in triggers, f"forbidden trigger {forbidden} present"

    dispatch_config = triggers["workflow_dispatch"]
    assert "inputs" in dispatch_config, "workflow_dispatch must define inputs"
    inputs = dispatch_config["inputs"]
    assert set(inputs.keys()) == {
        "task_id",
        "executor",
    }, f"inputs must contain only task_id and executor, got {set(inputs.keys())}"

    task_id_input = inputs["task_id"]
    assert task_id_input.get("required") is True
    assert task_id_input.get("type") == "string"

    executor_input = inputs["executor"]
    assert executor_input.get("required") is True
    assert executor_input.get("type") == "choice"
    assert sorted(executor_input.get("options", [])) == ["antigravity", "codex"]

    # Verify no arbitrary command/repository/ref/model inputs exist
    for forbidden_input in (
        "command",
        "cmd",
        "repo",
        "repository",
        "ref",
        "model",
        "runner",
        "label",
        "reasoning_effort",
    ):
        assert forbidden_input not in inputs


def test_workflow_routing_and_no_checkout_ac2() -> None:
    workflow = load_workflow()
    jobs = workflow.get("jobs", {})
    assert list(jobs.keys()) == ["wakeup"], f"expected single wakeup job, got {list(jobs.keys())}"

    job = jobs["wakeup"]
    assert job.get("runs-on") == [
        "self-hosted",
        "windows",
        "x64",
        "aios-renew",
    ], "runs-on must strictly route to designated runner"

    # Environment must bind AIOS_REPO_ROOT from repository variable
    env = job.get("env", {})
    assert (
        env.get("AIOS_REPO_ROOT") == "${{ vars.AIOS_REPO_ROOT }}"
    ), "AIOS_REPO_ROOT must be read from repository variable vars.AIOS_REPO_ROOT"

    raw_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "actions/checkout" not in raw_text, "actions/checkout must not be used"
    assert "git clone" not in raw_text, "git clone must not be used"
    assert "git worktree" not in raw_text, "git worktree must not be used"


def test_workflow_authority_and_no_direct_executor_ac3() -> None:
    raw_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    # Exactly one aios run invocation in workflow
    assert raw_text.count("aios run") == 1

    # Dispatched TASK id, executor, and repo passed to aios run via environment variables
    workflow = load_workflow()
    step = workflow["jobs"]["wakeup"]["steps"][1]
    step_env = step.get("env", {})
    assert step_env.get("AIOS_TASK_ID") == "${{ inputs.task_id }}"
    assert step_env.get("AIOS_EXECUTOR") == "${{ inputs.executor }}"
    assert "${{ inputs." not in step.get("run", "")
    assert (
        "aios run $env:AIOS_TASK_ID --executor $env:AIOS_EXECUTOR --repo $env:AIOS_REPO_ROOT"
        in raw_text
    )

    # No direct Executor call
    lines = [line.strip() for line in raw_text.splitlines()]
    for forbidden_call in ("codex ", "antigravity "):
        assert not any(
            line.startswith(forbidden_call) for line in lines
        ), f"direct executor call forbidden: {forbidden_call}"

    # No duplicate AIOS commands
    for duplicate in ("aios task", "aios remediate", "aios accept-candidate", "aios repair", "aios transport"):
        assert duplicate not in raw_text, f"duplicate AIOS command forbidden: {duplicate}"

    # No manual Git synchronization commands
    for git_sync in ("git pull", "git fetch", "git merge", "git reset", "git rebase", "git checkout"):
        assert git_sync not in raw_text, f"manual Git sync forbidden: {git_sync}"

    # No canonical verification commands in workflow
    for verify_cmd in ("pytest", "python -m pytest", "flake8", "ruff"):
        assert verify_cmd not in raw_text, f"verification command forbidden: {verify_cmd}"


def test_workflow_permissions_and_no_secrets_ac4() -> None:
    workflow = load_workflow()
    raw_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    permissions = workflow.get("permissions", {})
    assert permissions == {
        "contents": "read"
    }, f"expected permissions contents: read, got {permissions}"

    assert "write" not in str(permissions)
    assert "secrets." not in raw_text, "no secrets may be referenced"
    assert "GITHUB_TOKEN" not in raw_text, "GITHUB_TOKEN must not be injected"
    assert "DEPLOY_KEY" not in raw_text
    assert "SSH_KEY" not in raw_text


def test_workflow_preflight_missing_repo_variable_fails_ac5(tmp_path: Path) -> None:
    workflow = load_workflow()
    preflight_script = workflow["jobs"]["wakeup"]["steps"][0]["run"]

    # AIOS_REPO_ROOT unset / empty
    env = dict(os.environ)
    env["AIOS_REPO_ROOT"] = ""
    result = run_powershell_script(preflight_script, env=env)
    assert result.returncode != 0
    assert "AIOS_REPO_ROOT repository variable is not set or empty" in (
        result.stdout + result.stderr
    )


def test_workflow_preflight_invalid_git_root_fails_ac5(tmp_path: Path) -> None:
    workflow = load_workflow()
    preflight_script = workflow["jobs"]["wakeup"]["steps"][0]["run"]

    # Case 1: Non-existent directory
    env = dict(os.environ)
    env["AIOS_REPO_ROOT"] = str(tmp_path / "does_not_exist")
    result = run_powershell_script(preflight_script, env=env)
    assert result.returncode != 0

    # Case 2: Existing directory that is not a Git repository
    non_git_dir = tmp_path / "not_git"
    non_git_dir.mkdir()
    env["AIOS_REPO_ROOT"] = str(non_git_dir)
    result = run_powershell_script(preflight_script, env=env)
    assert result.returncode != 0
    assert "not a valid Git repository" in (result.stdout + result.stderr)


def test_workflow_preflight_missing_aios_executable_fails_ac5(tmp_path: Path) -> None:
    workflow = load_workflow()
    preflight_script = workflow["jobs"]["wakeup"]["steps"][0]["run"]

    # Create dummy git repository
    git_dir = tmp_path / "repo"
    git_dir.mkdir()
    subprocess.run(["git", "init", str(git_dir)], check=True, capture_output=True)

    # Empty PATH where aios does not exist
    env = dict(os.environ)
    env["AIOS_REPO_ROOT"] = str(git_dir)
    # Exclude directories containing aios from PATH
    system32 = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"
    env["PATH"] = system32  # Keep only basic Windows commands (where git might not be unless specified)
    # Ensure git is available so it passes the git check but fails at aios check
    git_dir_path = Path(subprocess.run(["where", "git"], capture_output=True, text=True).stdout.strip().splitlines()[0]).parent
    env["PATH"] = f"{git_dir_path};{system32}"

    result = run_powershell_script(preflight_script, env=env)
    assert result.returncode != 0
    assert "aios executable was not found on PATH" in (result.stdout + result.stderr)


def test_workflow_execution_preserves_nonzero_exit_code_ac5(tmp_path: Path) -> None:
    workflow = load_workflow()
    run_script = workflow["jobs"]["wakeup"]["steps"][1]["run"]

    # Create mock aios that exits with 42
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mock_aios = bin_dir / "aios.bat"
    mock_aios.write_text("@echo off\nexit /b 42\n", encoding="utf-8")

    env = dict(os.environ)
    env["AIOS_REPO_ROOT"] = str(tmp_path)
    env["AIOS_TASK_ID"] = "TASK-066"
    env["AIOS_EXECUTOR"] = "antigravity"
    env["PATH"] = f"{bin_dir};{env['PATH']}"

    result = run_powershell_script(run_script, env=env)
    assert (
        result.returncode == 42
    ), f"expected exit code 42 to be preserved, got {result.returncode}"

    # Also verify exit code 1
    mock_aios.write_text("@echo off\nexit /b 1\n", encoding="utf-8")
    result = run_powershell_script(run_script, env=env)
    assert (
        result.returncode == 1
    ), f"expected exit code 1 to be preserved, got {result.returncode}"


def test_workflow_execution_success_invokes_aios_run_once_ac3(tmp_path: Path) -> None:
    workflow = load_workflow()
    preflight_script = workflow["jobs"]["wakeup"]["steps"][0]["run"]
    run_script = workflow["jobs"]["wakeup"]["steps"][1]["run"]

    # Create dummy git repository
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)

    # Create mock aios that logs arguments
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder_file = tmp_path / "aios_invocations.txt"
    mock_aios = bin_dir / "aios.bat"
    mock_aios.write_text(
        f"@echo off\necho %* >> \"{recorder_file}\"\nexit /b 0\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["AIOS_REPO_ROOT"] = str(repo_dir)
    env["AIOS_TASK_ID"] = "TASK-066"
    env["AIOS_EXECUTOR"] = "antigravity"
    env["PATH"] = f"{bin_dir};{env['PATH']}"

    # Run preflight step
    preflight_result = run_powershell_script(preflight_script, env=env)
    assert (
        preflight_result.returncode == 0
    ), f"preflight failed: {preflight_result.stderr}"

    # Run execution step
    run_result = run_powershell_script(run_script, env=env)
    assert run_result.returncode == 0, f"run failed: {run_result.stderr}"

    # Verify exactly one invocation with exact arguments
    invocations = recorder_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(invocations) == 1
    expected_args = f"run TASK-066 --executor antigravity --repo {repo_dir}"
    assert invocations[0].strip() == expected_args


def test_task_id_injection_cannot_execute_second_command_ac1(tmp_path: Path) -> None:
    workflow = load_workflow()
    step = workflow["jobs"]["wakeup"]["steps"][1]
    run_script = step["run"]

    # Step must bind inputs via env and never interpolate inputs into PowerShell script source
    assert "${{ inputs." not in run_script
    step_env = step.get("env", {})
    assert step_env.get("AIOS_TASK_ID") == "${{ inputs.task_id }}"
    assert step_env.get("AIOS_EXECUTOR") == "${{ inputs.executor }}"

    # Create dummy git repository
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)

    # Create mock aios that logs arguments
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder_file = tmp_path / "aios_invocations.txt"
    mock_aios = bin_dir / "aios.bat"
    mock_aios.write_text(
        f"@echo off\necho %* >> \"{recorder_file}\"\nexit /b 0\n",
        encoding="utf-8",
    )

    marker_file = tmp_path / "injection_marker.txt"

    payloads = [
        f'TASK-066; New-Item -Path "{marker_file.as_posix()}" -ItemType File; #',
        f'TASK-066\nNew-Item -Path "{marker_file.as_posix()}" -ItemType File\n#',
        f'$(New-Item -Path "{marker_file.as_posix()}" -ItemType File)',
        f'TASK-066 & New-Item -Path "{marker_file.as_posix()}" -ItemType File & echo',
    ]

    # Baseline: prove direct interpolation would have executed the secondary command
    vulnerable_run = (
        f'aios run {payloads[0]} --executor antigravity --repo $env:AIOS_REPO_ROOT'
    )
    vuln_env = dict(os.environ)
    vuln_env["AIOS_REPO_ROOT"] = str(repo_dir)
    vuln_env["PATH"] = f"{bin_dir};{vuln_env['PATH']}"
    run_powershell_script(vulnerable_run, env=vuln_env)
    assert marker_file.exists(), "Baseline interpolation failed to trigger secondary command"
    marker_file.unlink()
    if recorder_file.exists():
        recorder_file.unlink()

    # Fixed execution: verify that env-boundary and shape validation prevent execution
    for injection_task_id in payloads:
        env = dict(os.environ)
        env["AIOS_REPO_ROOT"] = str(repo_dir)
        env["AIOS_TASK_ID"] = injection_task_id
        env["AIOS_EXECUTOR"] = "antigravity"
        env["PATH"] = f"{bin_dir};{env['PATH']}"

        result = run_powershell_script(run_script, env=env)

        # Secondary command must never execute
        assert not marker_file.exists(), f"Payload {injection_task_id!r} executed a second command!"
        # Format validation must reject non-canonical task_id
        assert result.returncode != 0
        assert "Invalid task_id format" in (result.stdout + result.stderr)
        # aios run must not be invoked
        assert not recorder_file.exists(), f"aios run invoked for payload {injection_task_id!r}"


def test_boundary_preservation_ac6() -> None:
    # No file under src/aios_renew is modified
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    changed_lines = [line.strip() for line in status.splitlines() if line.strip()]
    for line in changed_lines:
        path = line.split()[-1]
        assert not path.startswith("src/aios_renew"), f"src/aios_renew modified: {path}"


def test_readme_documents_self_hosted_wakeup_ac7() -> None:
    assert README_PATH.is_file()
    readme_text = README_PATH.read_text(encoding="utf-8")

    # Runner registration and prerequisites
    assert "aios-renew" in readme_text
    assert "windows" in readme_text.lower()
    assert "x64" in readme_text.lower()
    assert "AIOS_REPO_ROOT" in readme_text

    # Workflow dispatch usage
    assert "workflow_dispatch" in readme_text or "workflow-dispatch" in readme_text
    assert "task_id" in readme_text
    assert "executor" in readme_text

    # Security boundary
    assert "public repository" in readme_text.lower()
    assert "dedicated" in readme_text.lower()

    # Operator authority preservation
    assert "does not replace AIOS Operator authority" in readme_text or (
        "wakes" in readme_text and "Operator authority" in readme_text
    )
    assert "Git credentials" in readme_text or "credentials" in readme_text


def test_roadmap_reconciled_ac8() -> None:
    assert ROADMAP_PATH.is_file()
    roadmap_text = ROADMAP_PATH.read_text(encoding="utf-8")

    # K0.4 is DONE via TASK-056
    assert "K0.4 — Lean Kernel Conformance Gate — DONE" in roadmap_text
    assert "TASK-056" in roadmap_text

    # Post-K0 hardening recognized
    assert "TASK-057 through TASK-065" in roadmap_text or "Post-K0 Hardening" in roadmap_text

    # A8 mapped to TASK-063
    assert "A8 — Safe Publisher" in roadmap_text
    assert "TASK-063" in roadmap_text

    # A1 mapped to TASK-066
    assert "A1 — GitHub Actions Self-hosted Wakeup" in roadmap_text
    assert "TASK-066" in roadmap_text

    # Remaining A-series separately gated
    for gated in ("A2", "A3", "A4", "A5", "A6", "A7", "A9", "A10"):
        assert gated in roadmap_text
    assert "separately gated" in roadmap_text.lower()
