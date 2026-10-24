"""Contract regression tests for A1 GitHub Actions Self-hosted Wakeup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from aios_renew import operator
from aios_renew.execution_profile import default_execution_profile

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


def normalized_process_output(result: subprocess.CompletedProcess[str]) -> str:
    return " ".join((result.stdout + result.stderr).split())


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
        "dispatch_id",
        "task_id",
        "task_revision",
        "task_blob_sha",
        "task_commit_sha",
        "executor",
        "model",
        "reasoning_effort",
        "model_source",
        "effort_source",
    }

    dispatch_id_input = inputs["dispatch_id"]
    assert dispatch_id_input.get("required") is True
    assert dispatch_id_input.get("type") == "string"

    task_id_input = inputs["task_id"]
    assert task_id_input.get("required") is True
    assert task_id_input.get("type") == "string"

    assert inputs["task_revision"].get("required") is True
    assert inputs["task_revision"].get("type") == "number"
    for sha_input in ("task_blob_sha", "task_commit_sha"):
        assert inputs[sha_input].get("required") is True
        assert inputs[sha_input].get("type") == "string"

    executor_input = inputs["executor"]
    assert executor_input.get("required") is True
    assert executor_input.get("type") == "choice"
    assert sorted(executor_input.get("options", [])) == ["antigravity", "codex"]

    for profile_input in (
        "model",
        "reasoning_effort",
        "model_source",
        "effort_source",
    ):
        assert inputs[profile_input].get("required") is True
        assert inputs[profile_input].get("type") == "string"

    # Verify no arbitrary command/repository/ref inputs exist
    for forbidden_input in (
        "command",
        "cmd",
        "repo",
        "repository",
        "ref",
        "runner",
        "label",
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

    # Exactly one thin control-entry wakeup invocation; PRIMARY remains inside the Operator.
    assert raw_text.count("$controlSource wakeup") == 1
    assert raw_text.count("scripts/aios_control_entry.py") >= 2
    assert "aios run" not in raw_text

    # Outer identity and semantic selectors pass through environment data bindings.
    workflow = load_workflow()
    step = workflow["jobs"]["wakeup"]["steps"][1]
    step_env = step.get("env", {})
    assert step_env.get("AIOS_DISPATCH_ID") == "${{ inputs.dispatch_id }}"
    assert step_env.get("AIOS_TASK_ID") == "${{ inputs.task_id }}"
    assert step_env.get("AIOS_TASK_REVISION") == "${{ inputs.task_revision }}"
    assert step_env.get("AIOS_TASK_BLOB_SHA") == "${{ inputs.task_blob_sha }}"
    assert step_env.get("AIOS_TASK_COMMIT_SHA") == "${{ inputs.task_commit_sha }}"
    assert step_env.get("AIOS_EXECUTOR") == "${{ inputs.executor }}"
    job_env = workflow["jobs"]["wakeup"]["env"]
    assert job_env.get("AIOS_MODEL") == "${{ inputs.model }}"
    assert job_env.get("AIOS_REASONING_EFFORT") == "${{ inputs.reasoning_effort }}"
    assert job_env.get("AIOS_MODEL_SOURCE") == "${{ inputs.model_source }}"
    assert job_env.get("AIOS_EFFORT_SOURCE") == "${{ inputs.effort_source }}"
    assert "${{ inputs." not in step.get("run", "")
    assert (
        "python $entry $controlSource wakeup $env:AIOS_DISPATCH_ID $env:AIOS_TASK_ID --task-revision $env:AIOS_TASK_REVISION --task-blob-sha $env:AIOS_TASK_BLOB_SHA --task-commit-sha $env:AIOS_TASK_COMMIT_SHA --executor $env:AIOS_EXECUTOR --model $env:AIOS_MODEL --reasoning-effort $env:AIOS_REASONING_EFFORT --model-source $env:AIOS_MODEL_SOURCE --effort-source $env:AIOS_EFFORT_SOURCE --repo $env:AIOS_REPO_ROOT"
        in raw_text
    )

    # No direct Executor call
    lines = [line.strip() for line in raw_text.splitlines()]
    for forbidden_call in ("codex ", "antigravity "):
        assert not any(
            line.startswith(forbidden_call) for line in lines
        ), f"direct executor call forbidden: {forbidden_call}"

    # No duplicate AIOS commands
    for duplicate in ("aios task", "aios run", "aios remediate", "aios accept-candidate", "aios repair", "aios transport"):
        assert duplicate not in raw_text, f"duplicate AIOS command forbidden: {duplicate}"

    # No bootstrap mutation of the persistent repository.
    for git_sync in ("pull", "fetch", "merge", "reset", "rebase", "checkout"):
        assert f"git -C $env:AIOS_REPO_ROOT {git_sync}" not in raw_text

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


def test_workflow_persists_run_scoped_receipt_artifact() -> None:
    workflow = load_workflow()
    job = workflow["jobs"]["wakeup"]
    receipt_path = "${{ runner.temp }}/aios-primary-operational-receipt-v2.json"
    upload = next(
        step
        for step in job["steps"]
        if step.get("name") == "Persist bounded Operational Receipt v2"
    )

    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["env"] == {"AIOS_OPERATIONAL_RECEIPT_PATH": receipt_path}
    assert upload["with"] == {
        "name": "aios-operational-receipt-v2-primary-attempt-${{ github.run_attempt }}",
        "path": "${{ env.AIOS_OPERATIONAL_RECEIPT_PATH }}",
        "if-no-files-found": "error",
        "retention-days": 90,
    }
    assert "AIOS_OPERATIONAL_RECEIPT_PATH" not in job["env"]
    for step in job["steps"]:
        assert step["env"]["AIOS_OPERATIONAL_RECEIPT_PATH"] == receipt_path
    assert "delivery=@{kind='dispatch_id';id=$env:AIOS_DISPATCH_ID}" in (
        WORKFLOW_PATH.read_text(encoding="utf-8")
    )


def test_workflow_preflight_missing_repo_variable_fails_ac5(tmp_path: Path) -> None:
    workflow = load_workflow()
    preflight_script = workflow["jobs"]["wakeup"]["steps"][0]["run"]

    # AIOS_REPO_ROOT unset / empty
    env = dict(os.environ)
    env["AIOS_REPO_ROOT"] = ""
    receipt_path = tmp_path / "operational-receipt.json"
    env.update(
        {
            "AIOS_OPERATIONAL_RECEIPT_PATH": str(receipt_path),
            "AIOS_DISPATCH_ID": "dispatch-149-preflight",
            "AIOS_TASK_ID": "TASK-149",
            "AIOS_TASK_REVISION": "1",
            "AIOS_TASK_BLOB_SHA": "a" * 40,
            "AIOS_TASK_COMMIT_SHA": "b" * 40,
            "AIOS_EXECUTOR": "codex",
        }
    )
    result = run_powershell_script(preflight_script, env=env)
    assert result.returncode != 0
    assert "AIOS_REPO_ROOT repository variable is not set or empty" in (
        normalized_process_output(result)
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    assert receipt["boundary"] == "OPERATIONAL_FAILED"
    assert receipt["cause"]["reason_code"] == "AIOS_REPO_ROOT_NOT_CONFIGURED"
    assert receipt["run_created"] is False
    assert receipt["executor_invoked"] is False


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
    assert "not a valid Git repository" in normalized_process_output(result)


def test_workflow_preflight_missing_python_executable_fails_ac5(tmp_path: Path) -> None:
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
    assert "Python is unavailable" in normalized_process_output(result)


def test_workflow_execution_preserves_nonzero_exit_code_ac5(tmp_path: Path) -> None:
    workflow = load_workflow()
    run_script = workflow["jobs"]["wakeup"]["steps"][1]["run"]

    # Create mock Python control entry that exits with 42
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mock_python = bin_dir / "python.bat"
    mock_python.write_text("@echo off\nexit /b 42\n", encoding="utf-8")

    env = dict(os.environ)
    env["AIOS_REPO_ROOT"] = str(tmp_path)
    env["AIOS_DISPATCH_ID"] = "dispatch-066-failure"
    env["AIOS_TASK_ID"] = "TASK-066"
    env["AIOS_TASK_REVISION"] = "1"
    env["AIOS_TASK_BLOB_SHA"] = "a" * 40
    env["AIOS_TASK_COMMIT_SHA"] = "b" * 40
    env["AIOS_EXECUTOR"] = "antigravity"
    env["AIOS_MODEL"] = "test/antigravity-remote-v1"
    env["AIOS_REASONING_EFFORT"] = "low"
    env["AIOS_MODEL_SOURCE"] = "EXPLICIT"
    env["AIOS_EFFORT_SOURCE"] = "EXPLICIT"
    env["AIOS_CONTROL_ROOT"] = str(tmp_path / "control")
    env["PATH"] = f"{bin_dir};{env['PATH']}"

    result = run_powershell_script(run_script, env=env)
    assert (
        result.returncode == 42
    ), f"expected exit code 42 to be preserved, got {result.returncode}"

    # Also verify exit code 1
    mock_python.write_text("@echo off\nexit /b 1\n", encoding="utf-8")
    result = run_powershell_script(run_script, env=env)
    assert (
        result.returncode == 1
    ), f"expected exit code 1 to be preserved, got {result.returncode}"


def test_primary_cli_reprojects_exact_delivery_when_dispatch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected: list[tuple[Path, dict[str, object]]] = []

    monkeypatch.setattr(operator, "resolve_repository", lambda repo: tmp_path)
    monkeypatch.setattr(
        operator, "runtime_paths", lambda repo: SimpleNamespace(root=tmp_path)
    )

    def fail_dispatch(**kwargs: object) -> object:
        raise operator.DispatchError("terminal PRIMARY failure")

    monkeypatch.setattr(operator, "execute_dispatch", fail_dispatch)
    monkeypatch.setattr(
        operator,
        "_project_operational_delivery",
        lambda root, **kwargs: projected.append((root, kwargs)),
    )

    profile = default_execution_profile("codex", "AUTHORIZATION", tmp_path)
    exit_code = operator.main(
        [
            "wakeup",
            "dispatch-149-failure",
            "TASK-149",
            "--task-revision",
            "1",
            "--task-blob-sha",
            "a" * 40,
            "--task-commit-sha",
            "b" * 40,
            "--executor",
            "codex",
            "--repo",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert projected == [
        (
            tmp_path,
            {
                "family": "PRIMARY",
                "delivery_id": "dispatch-149-failure",
                "selectors": {
                    "task_id": "TASK-149",
                    "task_revision": 1,
                    "task_blob_sha": "a" * 40,
                    "task_commit_sha": "b" * 40,
                    "executor": "codex",
                    "model": profile.model,
                    "reasoning_effort": profile.reasoning_effort,
                    "model_source": profile.model_source,
                    "effort_source": profile.effort_source,
                },
            },
        )
    ]


def test_workflow_execution_success_invokes_aios_wakeup_once_ac3(tmp_path: Path) -> None:
    workflow = load_workflow()
    run_script = workflow["jobs"]["wakeup"]["steps"][1]["run"]

    # Create dummy git repository
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)

    # Create mock Python that logs the entry and arguments.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder_file = tmp_path / "aios_invocations.txt"
    mock_python = bin_dir / "python.bat"
    mock_python.write_text(
        f"@echo off\necho %* >> \"{recorder_file}\"\nexit /b 0\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["AIOS_REPO_ROOT"] = str(repo_dir)
    env["AIOS_DISPATCH_ID"] = "dispatch-066-success"
    env["AIOS_TASK_ID"] = "TASK-066"
    env["AIOS_TASK_REVISION"] = "1"
    env["AIOS_TASK_BLOB_SHA"] = "a" * 40
    env["AIOS_TASK_COMMIT_SHA"] = "b" * 40
    env["AIOS_EXECUTOR"] = "antigravity"
    env["AIOS_MODEL"] = "test/antigravity-remote-v1"
    env["AIOS_REASONING_EFFORT"] = "low"
    env["AIOS_MODEL_SOURCE"] = "EXPLICIT"
    env["AIOS_EFFORT_SOURCE"] = "EXPLICIT"
    env["AIOS_CONTROL_ROOT"] = str(tmp_path / "control")
    env["PATH"] = f"{bin_dir};{env['PATH']}"

    # Run execution step
    run_result = run_powershell_script(run_script, env=env)
    assert run_result.returncode == 0, f"run failed: {run_result.stderr}"

    # Verify exactly one invocation with exact arguments
    invocations = recorder_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(invocations) == 1
    expected_args = (
        f"{tmp_path / 'control' / 'scripts' / 'aios_control_entry.py'} {tmp_path / 'control'} wakeup dispatch-066-success TASK-066 --task-revision 1 --task-blob-sha {'a' * 40} --task-commit-sha {'b' * 40} --executor antigravity --model test/antigravity-remote-v1 --reasoning-effort low --model-source EXPLICIT --effort-source EXPLICIT --repo {repo_dir}"
    )
    assert invocations[0].strip() == expected_args


def test_task_id_injection_cannot_execute_second_command_ac1(tmp_path: Path) -> None:
    workflow = load_workflow()
    step = workflow["jobs"]["wakeup"]["steps"][1]
    run_script = step["run"]

    # Step must bind inputs via env and never interpolate inputs into PowerShell script source
    assert "${{ inputs." not in run_script
    step_env = step.get("env", {})
    assert step_env.get("AIOS_DISPATCH_ID") == "${{ inputs.dispatch_id }}"
    assert step_env.get("AIOS_TASK_ID") == "${{ inputs.task_id }}"
    assert step_env.get("AIOS_TASK_REVISION") == "${{ inputs.task_revision }}"
    assert step_env.get("AIOS_TASK_BLOB_SHA") == "${{ inputs.task_blob_sha }}"
    assert step_env.get("AIOS_TASK_COMMIT_SHA") == "${{ inputs.task_commit_sha }}"
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
        env["AIOS_DISPATCH_ID"] = "dispatch-066-injection-test"
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
        assert not recorder_file.exists(), f"aios wakeup invoked for payload {injection_task_id!r}"


def test_dispatch_id_injection_is_data_bound_and_rejected_ac1(tmp_path: Path) -> None:
    workflow = load_workflow()
    step = workflow["jobs"]["wakeup"]["steps"][1]
    run_script = step["run"]
    assert "${{ inputs." not in run_script

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker_file = tmp_path / "dispatch_injection_marker.txt"
    mock_aios = bin_dir / "aios.bat"
    mock_aios.write_text("@echo off\nexit /b 0\n", encoding="utf-8")
    env = dict(os.environ)
    env["AIOS_REPO_ROOT"] = str(tmp_path)
    env["AIOS_TASK_ID"] = "TASK-066"
    env["AIOS_TASK_REVISION"] = "1"
    env["AIOS_TASK_BLOB_SHA"] = "a" * 40
    env["AIOS_TASK_COMMIT_SHA"] = "b" * 40
    env["AIOS_EXECUTOR"] = "codex"
    env["PATH"] = f"{bin_dir};{env['PATH']}"

    for payload in (
        "../escape",
        "..\\escape",
        f'dispatch; New-Item -Path "{marker_file}" -ItemType File; #',
        "$(Write-Output injected)",
        "dispatch with spaces",
    ):
        env["AIOS_DISPATCH_ID"] = payload
        result = run_powershell_script(run_script, env=env)
        assert result.returncode != 0
        assert "Invalid dispatch_id format" in result.stdout + result.stderr
        assert not marker_file.exists()


def _capture_nonpass_workflow_delivery(tmp_path, fixture, monkeypatch, dispatch_id):
    """Capture the real workflow step's transport; do not launch a live self-host."""
    profile = default_execution_profile("codex", dispatch_id, fixture.repo)
    from tests.test_operator import git

    blob = git(fixture.remote, "rev-parse", f"{fixture.target}:.ai/tasks/TASK-360.yaml")
    control_source = tmp_path / "transient control"
    control_source.mkdir()
    capture = tmp_path / "captured-argv.json"
    recorder = tmp_path / "record_transport.py"
    recorder.write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['AIOS_TEST_CAPTURE']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "transport-bin"
    bin_dir.mkdir()
    (bin_dir / "python.bat").write_text(
        f'@echo off\n"{sys.executable}" "{recorder}" %*\nexit /b %errorlevel%\n',
        encoding="utf-8",
    )
    receipt_path = tmp_path / "operational-receipt.json"
    selectors = {
        "AIOS_REPO_ROOT": str(fixture.repo), "AIOS_CONTROL_ROOT": str(control_source),
        "AIOS_CONTROL_SHA": fixture.target, "AIOS_DISPATCH_ID": dispatch_id,
        "AIOS_TASK_ID": "TASK-360", "AIOS_TASK_REVISION": "1", "AIOS_TASK_BLOB_SHA": blob,
        "AIOS_TASK_COMMIT_SHA": fixture.target, "AIOS_EXECUTOR": "codex",
        "AIOS_MODEL": profile.model, "AIOS_REASONING_EFFORT": profile.reasoning_effort,
        "AIOS_MODEL_SOURCE": "EXPLICIT", "AIOS_EFFORT_SOURCE": "EXPLICIT",
        "AIOS_OPERATIONAL_RECEIPT_PATH": str(receipt_path), "AIOS_TEST_CAPTURE": str(capture),
    }
    for name, value in selectors.items():
        monkeypatch.setenv(name, value)
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    workflow = load_workflow()
    step = workflow["jobs"]["wakeup"]["steps"][1]
    transported = run_powershell_script(step["run"], env=env)
    assert transported.returncode == 0, transported.stderr
    args = json.loads(capture.read_text(encoding="utf-8"))
    assert args == [
        str(control_source / "scripts/aios_control_entry.py"), str(control_source),
        "wakeup", dispatch_id, "TASK-360", "--task-revision", "1", "--task-blob-sha", blob,
        "--task-commit-sha", fixture.target, "--executor", "codex", "--model", profile.model,
        "--reasoning-effort", profile.reasoning_effort, "--model-source", "EXPLICIT",
        "--effort-source", "EXPLICIT", "--repo", str(fixture.repo),
    ]
    # Successful workflow transport alone makes no RUN or verification assertion.
    progress = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    assert progress["boundary"] == "AIOS_INVOKED"
    assert progress["run_created"] is False and progress["executor_invoked"] is False
    assert not list(fixture.state.runs.glob("RUN-360-*.json"))
    assert not (control_source / ".git/aios").exists()
    return args[2:], receipt_path, blob, profile, control_source


@pytest.mark.parametrize("qualified", [True, False], ids=["qualified", "invalid-proof"])
@pytest.mark.parametrize("preservation", ["reviewed-result", "six-failures"])
def test_nonpass_primary_workflow_bindings_runtime_admission_and_terminal_redelivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qualified: bool, preservation: str,
) -> None:
    """Synthetic real Git and real admission with simulated native/verification I/O.

    This exercises the existing route's layers; it asserts no live self-host result.
    """
    from aios_renew.dispatch_reconciliation import DispatchError, inspect_dispatch
    from tests.test_operator import (
        FakeCodexRunner, _multiple_failure_primary_divergence, _nonpass_primary_divergence,
        _replace_multiple_failure_proof, _replace_nonpass_proof, git,
    )

    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    fixture = (_nonpass_primary_divergence(tmp_path) if preservation == "reviewed-result"
               else _multiple_failure_primary_divergence(tmp_path))
    dispatch_id = f"synthetic-413-{preservation}-{'positive' if qualified else 'negative'}"
    if not qualified:
        # Private error text may be captured locally, never in the public receipt.
        if preservation == "reviewed-result":
            fixture.review["findings"][0]["basis"] = "credential=synthetic-secret C:/private/no-share"
            _replace_nonpass_proof(fixture, review=True)
        else:
            member = fixture.members[-1]
            failure = json.loads(git(fixture.repo, "show", f"{member.artifact}:.ai/transport/failure.json"))
            failure["error"]["message"] = "credential=synthetic-secret C:/private/no-share"
            _replace_multiple_failure_proof(fixture, failure=failure)
    source_ids = ([member.run_id for member in fixture.members] if preservation == "six-failures"
                  else ["RUN-359-002"])
    source_patterns = tuple(f"refs/heads/aios/{family}/{run_id}" for family in (
        "failure", "failure-artifacts", "review", "artifacts", "review-decision",
    ) for run_id in source_ids) + ("refs/heads/main",)
    source_refs = git(fixture.remote, "for-each-ref", "--format=%(refname) %(objectname)", *source_patterns)
    history = {directory: {path.name: path.read_bytes() for path in (fixture.state.root / directory).glob("*.json")}
               for directory in ("runs", "failures", "repairs", "remediations")}
    argv, receipt_path, blob, profile, control_source = _capture_nonpass_workflow_delivery(
        tmp_path, fixture, monkeypatch, dispatch_id,
    )
    native = FakeCodexRunner(fixture.repo)
    restarts = []
    verification_calls = []

    def verify(command, **kwargs):
        verification_calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    def runner(command, **kwargs):
        if command[:3] == [sys.executable, "-m", "aios_renew.operator"]:
            restarts.append((command, kwargs))
            assert command[3:] == argv
            assert git(fixture.repo, "rev-parse", "HEAD") == fixture.target
            assert str(fixture.repo / "src") not in kwargs["env"]["PYTHONPATH"].split(os.pathsep)
            with monkeypatch.context() as child:
                child.setenv("AIOS_RESTART_ATTEMPTED", kwargs["env"]["AIOS_RESTART_ATTEMPTED"])
                code = operator.main(command[3:], native_runner=runner, verification_runner=verify)
            return subprocess.CompletedProcess(command, code)
        return native(command, **kwargs)

    code = operator.main(argv, native_runner=runner, verification_runner=verify)
    assert code == (0 if qualified else 1)
    assert git(fixture.remote, "for-each-ref", "--format=%(refname) %(objectname)", *source_patterns) == source_refs
    journal_path = next((fixture.state.root / "dispatches").glob("*.json"))
    journal_bytes = journal_path.read_bytes()
    journal = json.loads(journal_bytes)
    assert journal["dispatch_id"] == dispatch_id and journal["task_id"] == "TASK-360"
    assert journal["task_revision"] == 1 and journal["task_blob_sha"] == blob
    assert journal["task_commit_sha"] == fixture.target and journal["executor"] == "codex"
    assert journal["model"] == profile.model and journal["reasoning_effort"] == profile.reasoning_effort
    assert journal["model_source"] == journal["effort_source"] == "EXPLICIT"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    assert receipt["format"] == "AIOS_OPERATIONAL_RECEIPT" and receipt["version"] == 2
    if qualified:
        assert len(restarts) == native.count == 1 and verification_calls
        assert journal["status"] == "SUCCEEDED" and journal["run_id"] == "RUN-360-001"
        run = json.loads((fixture.state.runs / "RUN-360-001.json").read_bytes())
        assert run["base_sha"] == fixture.target and run["task"] == {"id": "TASK-360", "revision": 1}
        assert run["workspace"] == str(fixture.repo) and run["executor"] == "codex"
        assert len(list(fixture.state.runs.glob("RUN-360-*.json"))) == 1
        assert receipt["boundary"] == "TERMINAL_POINTER" and receipt["run_created"] is True
        # This receipt reports attribution only; actual invocation was counted
        # independently by the simulated native boundary above.
        assert receipt["executor_invoked"] is False
    else:
        assert not restarts and native.count == 0 and not verification_calls
        assert journal["status"] == "FAILED" and journal["run_id"] is None
        assert git(fixture.repo, "rev-parse", "HEAD") == fixture.candidate
        assert not list(fixture.state.runs.glob("RUN-360-*.json"))
        assert receipt["run_created"] is False and receipt["executor_invoked"] is False
        assert receipt["boundary"] == "ADMISSION_REJECTED"
        assert receipt["cause"]["phase"] == "PRIMARY_SYNCHRONIZATION"
        assert receipt["cause"]["reason_code"] == "PRIMARY_SYNCHRONIZATION_REJECTED"
        public_receipt = json.dumps(receipt)
        assert len(public_receipt) < 4096
        for private in ("synthetic-secret", "C:/private", str(fixture.repo)):
            assert private not in public_receipt
        for directory, originals in history.items():
            assert {path.name: path.read_bytes() for path in (fixture.state.root / directory).glob("*.json")} == originals
        if preservation == "six-failures":
            diagnostics = [json.loads(path.read_bytes()) for path in fixture.state.admission_failures.glob("*.json")]
            assert len(diagnostics) == 1
            assert diagnostics[0]["error"]["message"] == "FAILURE_PUBLIC_INTEGRITY_INVALID"
        # Even after proof is repaired, this delivery remains terminal without RUN.
        if preservation == "reviewed-result":
            fixture.review["findings"][0]["basis"] = "AC4"
            _replace_nonpass_proof(fixture, review=True)
        else:
            git(fixture.remote, "update-ref", f"refs/heads/aios/failure-artifacts/{member.run_id}", member.artifact)

    # A new clean checkout is observable, but cannot replace the configured journal.
    clean = tmp_path / "independent-clean"
    subprocess.run(["git", "clone", "--quiet", str(fixture.remote), str(clean)], check=True)
    clean_head = git(clean, "rev-parse", "HEAD")
    with pytest.raises(DispatchError, match="dispatch record does not exist"):
        inspect_dispatch(repo=clean, state_root=clean / ".git/aios", dispatch_id=dispatch_id)

    def forbidden(*args, **kwargs):
        pytest.fail("terminal re-delivery must not preflight, rearm, migrate or execute")

    monkeypatch.setattr(operator, "_preflight_primary_admission", forbidden)
    monkeypatch.setattr(operator, "run_task", forbidden)
    assert operator.main(argv, native_runner=forbidden, verification_runner=forbidden) == code
    assert journal_path.read_bytes() == journal_bytes
    assert os.environ["AIOS_REPO_ROOT"] == str(fixture.repo)
    assert git(clean, "rev-parse", "HEAD") == clean_head
    assert not (clean / ".git/aios").exists()
    assert not (control_source / ".git/aios").exists()
    assert native.count == int(qualified)


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
    assert "dispatch_id" in readme_text
    assert "no re-execution" in readme_text.lower()

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

    # Post-K0 hardening recognized and mapped to exact canonical TASK lineage
    assert "TASK-057 through TASK-065" in roadmap_text or "Post-K0 Hardening" in roadmap_text
    canonical_post_k0_mappings = {
        "TASK-057": "Runtime-owned REPAIR changed_files authority",
        "TASK-058": "Deterministic historical REPAIR recovery",
        "TASK-059": "Native execution-efficiency/interruption hardening",
        "TASK-060": "60-minute response budget plus 65-minute outer watchdog",
        "TASK-061": "Deterministic model defaults",
        "TASK-062": "PRIMARY auto-sync",
        "TASK-063": "Safe publication",
        "TASK-064": "Verification-only NO_CHANGE continuation",
        "TASK-065": "Native token observation",
    }
    for task_id, expected_purpose in canonical_post_k0_mappings.items():
        matching_lines = [
            line.strip()
            for line in roadmap_text.splitlines()
            if f"**{task_id}**" in line
        ]
        assert matching_lines, f"{task_id} mapping bullet missing in roadmap"
        assert any(
            expected_purpose.lower() in line.lower() for line in matching_lines
        ), f"{task_id} line {matching_lines} does not contain expected canonical purpose '{expected_purpose}'"

    assert "revision 2" in [
        line for line in roadmap_text.splitlines() if "**TASK-058**" in line
    ][0]

    for obsolete_purpose in (
        "Terminal failure observation and structured diagnostics",
        "Single-process run lease registry",
        "Monotonic duration tracking across execution phases",
    ):
        assert obsolete_purpose not in roadmap_text, (
            f"Obsolete/incorrect mapping '{obsolete_purpose}' found in roadmap"
        )

    # A8 mapped to TASK-063
    assert "A8 — Safe Publisher" in roadmap_text
    assert "TASK-063" in roadmap_text

    # A1 mapped to TASK-066
    assert "A1 — GitHub Actions Self-hosted Wakeup" in roadmap_text
    assert "TASK-066" in roadmap_text

    # A2 is implemented while later A-series items remain separately gated.
    assert "A2 — Durable Dispatch Identity + Reconciliation" in roadmap_text
    assert "TASK-068" in roadmap_text
    for gated in ("A3", "A4", "A5", "A6", "A7", "A9", "A10"):
        assert gated in roadmap_text
    assert "separately gated" in roadmap_text.lower()
