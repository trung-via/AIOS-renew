from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from aios_renew import operator


ROOT = Path(__file__).resolve().parents[1]
CARRIER = ROOT / ".github/workflows/aios-brain-repair-wakeup.yml"
TARGET = ROOT / ".github/workflows/aios-self-hosted-repair-wakeup.yml"
POLICY = ROOT / ".ai/brain-repair-wakeup-carriers.yaml"


def _workflow(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def test_issue_admission_is_exact_and_github_hosted() -> None:
    workflow, text = _workflow(CARRIER)
    assert workflow["on"] == {"issues": {"types": ["opened"]}}
    admit = workflow["jobs"]["admit"]
    assert admit["if"] == "github.event.issue.title == '[AIOS REPAIR WAKEUP]'"
    assert admit["runs-on"] == "ubuntu-latest"
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert "github.event.issue.body" not in text


def test_carrier_calls_exactly_one_fixed_target_with_only_selectors() -> None:
    workflow, text = _workflow(CARRIER)
    dispatch = workflow["jobs"]["dispatch"]
    assert dispatch["uses"] == "./.github/workflows/aios-self-hosted-repair-wakeup.yml"
    assert set(dispatch["with"]) == {
        "repair_dispatch_id",
        "failed_run_id",
        "repair_sha",
        "executor",
    }
    assert text.count("aios_renew.github_issue_repair_wakeup") == 1
    forbidden_command = re.compile(
        r"^\s*(?:run:\s*)?(?:&\s*)?(?:python(?:\.exe)?\s+-m\s+)?"
        r"aios\s+(?:repair(?:\s|$)|remediate(?:\s|$)|run(?:\s|$))",
        re.IGNORECASE | re.MULTILINE,
    )
    assert forbidden_command.search(text) is None
    assert "aios repair-wakeup" not in text.lower()
    assert "secrets: inherit" not in text.lower()


def test_fixed_target_has_manual_and_reusable_carriers_and_one_command_surface() -> None:
    workflow, text = _workflow(TARGET)
    assert set(workflow["on"]) == {"workflow_dispatch", "workflow_call"}
    expected = {"repair_dispatch_id", "failed_run_id", "repair_sha", "executor"}
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == expected
    assert set(workflow["on"]["workflow_call"]["inputs"]) == expected
    job = workflow["jobs"]["execute-repair"]
    assert job["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "actions/checkout" not in text
    assert "AIOS_REPO_ROOT: ${{ vars.AIOS_REPO_ROOT }}" in text
    assert text.count("aios repair-wakeup ") == 1
    for forbidden in ("aios continue", "aios repair ", "codex ", "antigravity "):
        assert forbidden not in text.lower()
    assert "$aiosExitCode = $LASTEXITCODE" in text
    assert "exit $aiosExitCode" in text


def test_repair_cli_reprojects_exact_delivery_when_execution_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected: list[tuple[Path, dict[str, object]]] = []

    monkeypatch.setattr(operator, "resolve_repository", lambda repo: tmp_path)

    def fail_repair(*args: object, **kwargs: object) -> object:
        raise operator.OperatorError("terminal REPAIR failure")

    monkeypatch.setattr(operator, "run_repair_wakeup", fail_repair)
    monkeypatch.setattr(
        operator,
        "_project_operational_delivery",
        lambda root, **kwargs: projected.append((root, kwargs)),
    )

    exit_code = operator.main(
        [
            "repair-wakeup",
            "repair-149-failure",
            "RUN-149-003",
            "a" * 40,
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
                "family": "REPAIR",
                "delivery_id": "repair-149-failure",
                "selectors": {
                    "failed_run_id": "RUN-149-003",
                    "repair_sha": "a" * 40,
                    "executor": "codex",
                },
            },
        )
    ]


def test_receipt_accepts_only_successful_admission_and_dispatch() -> None:
    workflow, text = _workflow(CARRIER)
    assert "const accepted = admitted && dispatched;" in text
    assert "if (accepted)" in text
    assert "repair_run_outcome: not_asserted_by_carrier" in text
    assert "verification: not_asserted_by_carrier" in text
    assert "semantic_review: not_asserted_by_carrier" in text
    assert "publication: not_asserted_by_carrier" in text
    assert "status: ${accepted ? 'SELF_HOST_COMPLETED' : 'REJECTED'}" in text
    assert "boundary: dispatched ? 'SELF_HOST_COMPLETED' : 'OPERATIONAL_FAILED'" in text
    assert "DOWNSTREAM_WORKFLOW_FAILED" in text
    assert "AIOS_OPERATIONAL_RECEIPT_V2=" in text
    rejected = workflow["jobs"]["receipt"]["steps"][1]
    assert rejected["if"] == (
        "needs.admit.result != 'success' || needs.dispatch.result != 'success'"
    )


def test_repair_workflow_persists_exact_run_receipt_artifact() -> None:
    workflow, text = _workflow(TARGET)
    job = workflow["jobs"]["execute-repair"]
    receipt_path = "${{ runner.temp }}/aios-repair-operational-receipt-v2.json"
    upload = next(
        step
        for step in job["steps"]
        if step.get("name") == "Persist bounded Operational Receipt v2"
    )

    assert upload == {
        "name": "Persist bounded Operational Receipt v2",
        "if": "always()",
        "uses": "actions/upload-artifact@v4",
        "env": {"AIOS_OPERATIONAL_RECEIPT_PATH": receipt_path},
        "with": {
            "name": "aios-operational-receipt-v2",
            "path": "${{ env.AIOS_OPERATIONAL_RECEIPT_PATH }}",
            "if-no-files-found": "error",
            "retention-days": "30",
        },
    }
    assert "AIOS_OPERATIONAL_RECEIPT_PATH" not in job["env"]
    for step in job["steps"]:
        assert step["env"]["AIOS_OPERATIONAL_RECEIPT_PATH"] == receipt_path
    assert "delivery=@{kind='repair_dispatch_id';id=$env:AIOS_REPAIR_DISPATCH_ID}" in text


def test_policy_is_dedicated_and_exact() -> None:
    assert yaml.safe_load(POLICY.read_text(encoding="utf-8")) == {
        "format": "AIOS_BRAIN_REPAIR_WAKEUP_CARRIERS_POLICY",
        "version": 1,
        "github_issue": {
            "enabled": True,
            "repository": "trung-via/AIOS-renew",
            "authorized_actors": ["trung-via"],
            "title_marker": "[AIOS REPAIR WAKEUP]",
            "max_body_bytes": 4096,
        },
    }
