from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aios_renew import operator


ROOT = Path(__file__).resolve().parents[1]
CARRIER_PATH = ROOT / ".github/workflows/aios-brain-remediation-intent.yml"
INTENT_PATH = ROOT / ".github/workflows/aios-approved-remediation-intent.yml"
WAKEUP_PATH = ROOT / ".github/workflows/aios-approved-remediation-wakeup.yml"
POLICY_PATH = ROOT / ".ai/brain-remediation-intent-carriers.yaml"


def _workflow(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def test_issue_carrier_is_exact_opened_title_and_github_hosted_admission() -> None:
    workflow, text = _workflow(CARRIER_PATH)
    assert workflow["on"] == {"issues": {"types": ["opened"]}}
    assert workflow["jobs"]["admit"]["if"] == (
        "github.event.issue.title == '[AIOS REMEDIATION INTENT]'"
    )
    assert workflow["jobs"]["admit"]["runs-on"] == "ubuntu-latest"
    assert "github.event.issue.body" not in text
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}


def test_carrier_calls_one_fixed_workflow_with_only_four_sanitized_selectors() -> None:
    workflow, text = _workflow(CARRIER_PATH)
    dispatch = workflow["jobs"]["dispatch"]
    assert dispatch["uses"] == (
        "./.github/workflows/aios-approved-remediation-intent.yml"
    )
    assert set(dispatch["with"]) == {
        "correction_dispatch_id",
        "source_run_id",
        "finding_id",
        "executor",
    }
    assert text.count("aios_renew.github_issue_remediation_intent") == 1
    for forbidden in (
        "aios remote-approve",
        "aios approved-remediation-wakeup",
        "aios remediate",
        "aios repair",
        "codex ",
        "antigravity ",
        "secrets: inherit",
    ):
        assert forbidden not in text.lower()


def test_fixed_intent_workflow_preserves_self_hosted_boundary_and_a3_a6_command() -> None:
    workflow, text = _workflow(INTENT_PATH)
    assert set(workflow["on"]) == {"workflow_dispatch", "workflow_call"}
    for trigger in ("workflow_dispatch", "workflow_call"):
        assert set(workflow["on"][trigger]["inputs"]) == {
            "correction_dispatch_id",
            "source_run_id",
            "finding_id",
            "executor",
        }
    job = workflow["jobs"]["approve-and-wake"]
    assert job["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "actions/checkout" not in text
    assert "AIOS_REPO_ROOT: ${{ vars.AIOS_REPO_ROOT }}" in text
    assert "AIOS_APPROVER: ${{ github.actor }}" in text
    assert text.count("aios approved-remediation-intent ") == 1
    assert "aios remote-approve " not in text
    assert "aios approved-remediation-wakeup " not in text
    assert "$aiosExitCode = $LASTEXITCODE" in text
    assert "exit $aiosExitCode" in text


def test_intent_cli_reprojects_exact_delivery_when_execution_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected: list[tuple[Path, dict[str, object]]] = []

    monkeypatch.setattr(operator, "resolve_repository", lambda repo: tmp_path)

    def fail_intent(*args: object, **kwargs: object) -> object:
        raise operator.OperatorError("terminal REMEDIATION failure")

    monkeypatch.setattr(operator, "run_approved_remediation_intent", fail_intent)
    monkeypatch.setattr(
        operator,
        "_project_operational_delivery",
        lambda root, **kwargs: projected.append((root, kwargs)),
    )

    exit_code = operator.main(
        [
            "approved-remediation-intent",
            "correction-149-failure",
            "RUN-149-003",
            "F3",
            "--executor",
            "codex",
            "--approver",
            "trung-via",
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
                },
            },
        )
    ]


def test_receipt_never_fabricates_downstream_semantic_success() -> None:
    workflow, text = _workflow(CARRIER_PATH)
    assert text.count("github.rest.issues.createComment") == 1
    assert ".slice(0, 3500)" in text
    assert "const dispatched = process.env.AIOS_DISPATCH_RESULT === 'success';" in text
    assert "status: ${admitted && dispatched ? 'SELF_HOST_COMPLETED' : 'REJECTED'}" in text
    assert "boundary: dispatched ? 'SELF_HOST_COMPLETED' : 'OPERATIONAL_FAILED'" in text
    assert "DOWNSTREAM_WORKFLOW_FAILED" in text
    assert "AIOS_OPERATIONAL_RECEIPT_V2=" in text
    assert "a3_approval: not_asserted_by_carrier" in text
    assert "remediation_run_outcome: not_asserted_by_carrier" in text
    assert "does not itself assert A3 approval, RUN, verification, DELTA review, or publication success" in text
    preserve_rejected = workflow["jobs"]["receipt"]["steps"][1]
    assert preserve_rejected["if"] == (
        "needs.admit.result != 'success' || needs.dispatch.result != 'success'"
    )


def test_remediation_workflows_persist_exact_run_receipt_artifact() -> None:
    for path, job_name, receipt_file in (
        (
            INTENT_PATH,
            "approve-and-wake",
            "aios-remediation-intent-operational-receipt-v2.json",
        ),
        (
            WAKEUP_PATH,
            "wakeup",
            "aios-remediation-operational-receipt-v2.json",
        ),
    ):
        workflow, text = _workflow(path)
        job = workflow["jobs"][job_name]
        upload = next(
            step
            for step in job["steps"]
            if step.get("name") == "Persist bounded Operational Receipt v2"
        )
        assert upload == {
            "name": "Persist bounded Operational Receipt v2",
            "if": "always()",
            "uses": "actions/upload-artifact@v4",
            "with": {
                "name": "aios-operational-receipt-v2",
                "path": "${{ env.AIOS_OPERATIONAL_RECEIPT_PATH }}",
                "if-no-files-found": "error",
                "retention-days": "30",
            },
        }
        assert job["env"]["AIOS_OPERATIONAL_RECEIPT_PATH"] == (
            f"${{{{ runner.temp }}}}/{receipt_file}"
        )
        assert (
            "delivery=@{kind='correction_dispatch_id';id=$env:AIOS_CORRECTION_DISPATCH_ID}"
            in text
        )
        assert "$aiosExitCode = $LASTEXITCODE" in text
        assert "exit $aiosExitCode" in text


def test_policy_is_distinct_and_exact() -> None:
    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    assert policy == {
        "format": "AIOS_BRAIN_REMEDIATION_INTENT_CARRIERS_POLICY",
        "version": 1,
        "github_issue": {
            "enabled": True,
            "repository": "trung-via/AIOS-renew",
            "authorized_actors": ["trung-via"],
            "title_marker": "[AIOS REMEDIATION INTENT]",
            "max_body_bytes": 4096,
        },
    }
