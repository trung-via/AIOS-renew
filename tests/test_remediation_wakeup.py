from pathlib import Path
import re

import pytest

from aios_renew import correction_dispatch, operator, remote_surface
from aios_renew.execution_profile import default_execution_profile


def test_approved_remediation_workflow_is_bounded_manual_self_hosted_surface() -> None:
    root = Path(__file__).parents[1]
    source = (root / ".github/workflows/aios-approved-remediation-wakeup.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in source
    assert "pull_request:" not in source and "push:" not in source
    assert "actions/checkout" not in source
    assert "runs-on: [self-hosted, windows, x64, aios-renew]" in source
    assert "contents: read" in source
    assert "${{ inputs.correction_dispatch_id }}" in source
    assert "${{ inputs.source_run_id }}" in source
    assert "${{ inputs.finding_id }}" in source
    assert "${{ inputs.executor }}" in source
    assert "${{ inputs.model }}" in source
    assert "${{ inputs.reasoning_effort }}" in source
    assert "${{ inputs.model_source }}" in source
    assert "${{ inputs.effort_source }}" in source
    assert "$aiosExitCode = $LASTEXITCODE" in source
    assert "exit $aiosExitCode" in source

    # Transient control-source contract: the workflow must materialize the
    # bounded control source and invoke scripts/aios_control_entry.py against
    # it instead of a directly-installed `aios` binary. The bounded command
    # must forward the full executor/profile argument surface, including
    # --model, --reasoning-effort, --model-source, and --effort-source.
    assert (
        "Join-Path $controlSource 'scripts/aios_control_entry.py'" in source
    ), (
        "aios-approved-remediation-wakeup.yml must materialize the bounded "
        "control source via scripts/aios_control_entry.py"
    )
    controlled_invocation = re.search(
        r"python\s+\$entry\s+\$controlSource\s+approved-remediation-wakeup\b[^\r\n]*",
        source,
    )
    assert controlled_invocation is not None, (
        "aios-approved-remediation-wakeup.yml must invoke "
        "`python $entry $controlSource approved-remediation-wakeup ...`"
    )
    assert (
        len(
            re.findall(
                r"python\s+\$entry\s+\$controlSource\s+approved-remediation-wakeup\b",
                source,
            )
        )
        == 1
    ), (
        "aios-approved-remediation-wakeup.yml must invoke "
        "`python $entry $controlSource approved-remediation-wakeup ...` exactly once"
    )
    command_line = controlled_invocation.group(0)
    for required_arg in (
        "--executor",
        "--model",
        "--reasoning-effort",
        "--model-source",
        "--effort-source",
    ):
        assert required_arg in command_line, (
            "aios-approved-remediation-wakeup.yml transient command must "
            f"forward bounded profile argument {required_arg}"
        )

    # Regression guard: the workflow must not regress to a directly-installed
    # `aios approved-remediation-wakeup ...` invocation that bypasses the
    # transient control-source entry.
    installed_invocation = re.compile(
        r"^\s*aios\s+approved-remediation-wakeup\b",
        re.MULTILINE,
    )
    assert installed_invocation.search(source) is None, (
        "aios-approved-remediation-wakeup.yml must not regress to a direct "
        "installed `aios approved-remediation-wakeup ...` invocation"
    )


def test_remediation_wakeup_cli_reprojects_exact_delivery_when_dispatch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected: list[tuple[Path, dict[str, object]]] = []
    approval = type("Approval", (), {"task_id": "TASK-149"})()

    monkeypatch.setattr(operator, "resolve_repository", lambda repo: tmp_path)
    monkeypatch.setattr(
        operator, "runtime_state_root", lambda repo: tmp_path / "runtime"
    )
    monkeypatch.setattr(
        correction_dispatch,
        "reject_existing_selector_collision",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        remote_surface, "require_current_approval", lambda **kwargs: approval
    )

    def fail_dispatch(**kwargs: object) -> object:
        raise correction_dispatch.CorrectionDispatchError(
            "terminal REMEDIATION failure"
        )

    monkeypatch.setattr(
        correction_dispatch, "execute_correction_dispatch", fail_dispatch
    )
    monkeypatch.setattr(
        operator,
        "_project_operational_delivery",
        lambda root, **kwargs: projected.append((root, kwargs)),
    )

    profile = default_execution_profile("codex", "AUTHORIZATION", tmp_path)
    exit_code = operator.main(
        [
            "approved-remediation-wakeup",
            "correction-149-failure",
            "RUN-149-003",
            "F3",
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
                "family": "REMEDIATION",
                "delivery_id": "correction-149-failure",
                "selectors": {
                    "source_run_id": "RUN-149-003",
                    "finding_id": "F3",
                    "executor": "codex",
                    "model": profile.model,
                    "reasoning_effort": profile.reasoning_effort,
                    "model_source": profile.model_source,
                    "effort_source": profile.effort_source,
                },
            },
        )
    ]
