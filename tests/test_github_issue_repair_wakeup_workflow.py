from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from aios_renew import operator
from aios_renew.execution_profile import default_execution_profile
from aios_renew.operational_receipt import WORKFLOW_REASONS


ROOT = Path(__file__).resolve().parents[1]
CARRIER = ROOT / ".github/workflows/aios-brain-repair-wakeup.yml"
TARGET = ROOT / ".github/workflows/aios-self-hosted-repair-wakeup.yml"
POLICY = ROOT / ".ai/brain-repair-wakeup-carriers.yaml"


def _self_host_provenance_admits(
    *,
    actor: str,
    event: str,
    repair_dispatch_id: str,
    failed_run_id: str,
    repair_sha: str,
    executor: str,
) -> bool:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", repair_dispatch_id) is None:
        return False
    if re.fullmatch(r"RUN-[A-Za-z0-9_-]+-[0-9]{3,}", failed_run_id) is None:
        return False
    if re.fullmatch(r"[0-9a-f]{40}", repair_sha) is None:
        return False
    if executor not in {"", "codex", "antigravity"}:
        return False
    if actor == "trung-via":
        return True
    return (
        actor == "github-actions[bot]"
        and event == "workflow_dispatch"
        and executor == ""
        and repair_dispatch_id == f"repair-{failed_run_id}-{repair_sha}"
    )


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
        "model",
        "reasoning_effort",
        "model_source",
        "effort_source",
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
    expected = {
        "repair_dispatch_id",
        "failed_run_id",
        "repair_sha",
        "executor",
        "model",
        "reasoning_effort",
        "model_source",
        "effort_source",
    }
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == expected
    assert set(workflow["on"]["workflow_call"]["inputs"]) == expected
    job = workflow["jobs"]["execute-repair"]
    assert job["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "actions/checkout" not in text
    assert "AIOS_REPO_ROOT: ${{ vars.AIOS_REPO_ROOT }}" in text
    assert text.count("$controlSource repair-wakeup ") == 1
    for forbidden in ("aios continue", "aios repair ", "codex ", "antigravity "):
        assert forbidden not in text.lower()
    assert "$aiosExitCode = $LASTEXITCODE" in text
    assert "exit $aiosExitCode" in text


def test_fixed_target_uses_exact_github_owned_provenance_gate() -> None:
    workflow, text = _workflow(TARGET)
    expected_inputs = {
        "repair_dispatch_id",
        "failed_run_id",
        "repair_sha",
        "executor",
        "model",
        "reasoning_effort",
        "model_source",
        "effort_source",
    }
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == expected_inputs
    assert set(workflow["on"]["workflow_call"]["inputs"]) == expected_inputs

    job = workflow["jobs"]["execute-repair"]
    assert job["env"]["AIOS_DELIVERY_ACTOR"] == "${{ github.actor }}"
    assert job["env"]["AIOS_DELIVERY_EVENT"] == "${{ github.event_name }}"
    assert "$env:AIOS_DELIVERY_ACTOR -ceq 'trung-via'" in text
    assert "$env:AIOS_DELIVERY_ACTOR -ceq 'github-actions[bot]'" in text
    assert "$env:AIOS_DELIVERY_EVENT -cne 'workflow_dispatch'" in text
    assert "-not [string]::IsNullOrEmpty($env:AIOS_EXECUTOR)" in text
    assert (
        '$expectedRepairDispatchId = "repair-$($env:AIOS_FAILED_RUN_ID)-$($env:AIOS_REPAIR_SHA)"'
        in text
    )
    assert "$env:AIOS_REPAIR_DISPATCH_ID -cne $expectedRepairDispatchId" in text

    gate_position = text.index("$env:AIOS_DELIVERY_ACTOR -ceq 'trung-via'")
    command_position = text.index("$controlSource repair-wakeup ")
    assert gate_position < command_position


def test_provenance_gate_emits_only_canonical_workflow_reasons() -> None:
    _, text = _workflow(TARGET)
    gate_start = text.index("if ($env:AIOS_DELIVERY_ACTOR -ceq 'trung-via')")
    gate_end = text.index(
        "if ([string]::IsNullOrWhiteSpace($env:AIOS_REPO_ROOT))", gate_start
    )
    emitted_reasons = set(
        re.findall(r"Write-OperationalFailure '([A-Z_]+)'", text[gate_start:gate_end])
    )

    assert emitted_reasons == {"INVALID_BOUNDED_INPUT", "UNAUTHORIZED_DELIVERY_ACTOR"}
    assert emitted_reasons <= WORKFLOW_REASONS


@pytest.mark.parametrize(
    (
        "actor",
        "event",
        "dispatch_mutation",
        "failed_run_id",
        "repair_sha",
        "executor",
        "expected",
    ),
    [
        ("trung-via", "issues", "human", "RUN-160-001", "a" * 40, "codex", True),
        (
            "trung-via",
            "workflow_dispatch",
            "human",
            "RUN-160-001",
            "a" * 40,
            "",
            True,
        ),
        (
            "github-actions[bot]",
            "workflow_dispatch",
            "exact",
            "RUN-160-001",
            "a" * 40,
            "",
            True,
        ),
        (
            "github-actions[bot]",
            "workflow_dispatch",
            "exact",
            "RUN-160-001",
            "a" * 40,
            "codex",
            False,
        ),
        (
            "github-actions[bot]",
            "issues",
            "exact",
            "RUN-160-001",
            "a" * 40,
            "",
            False,
        ),
        (
            "github-actions[bot]",
            "workflow_dispatch",
            "mismatch",
            "RUN-160-001",
            "a" * 40,
            "",
            False,
        ),
        (
            "github-actions[bot]",
            "workflow_dispatch",
            "exact",
            "bad-run",
            "a" * 40,
            "",
            False,
        ),
        (
            "github-actions[bot]",
            "workflow_dispatch",
            "exact",
            "RUN-160-001",
            "A" * 40,
            "",
            False,
        ),
        (
            "intruder",
            "workflow_dispatch",
            "exact",
            "RUN-160-001",
            "a" * 40,
            "",
            False,
        ),
    ],
)
def test_self_host_provenance_truth_table_is_fail_closed(
    actor: str,
    event: str,
    dispatch_mutation: str,
    failed_run_id: str,
    repair_sha: str,
    executor: str,
    expected: bool,
) -> None:
    if dispatch_mutation == "exact":
        repair_dispatch_id = f"repair-{failed_run_id}-{repair_sha}"
    elif dispatch_mutation == "mismatch":
        repair_dispatch_id = f"repair-{failed_run_id}-{'b' * 40}"
    else:
        repair_dispatch_id = "repair-human-selected"

    assert _self_host_provenance_admits(
        actor=actor,
        event=event,
        repair_dispatch_id=repair_dispatch_id,
        failed_run_id=failed_run_id,
        repair_sha=repair_sha,
        executor=executor,
    ) is expected


def test_repair_cli_reprojects_exact_delivery_when_execution_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected: list[tuple[Path, dict[str, object]]] = []

    monkeypatch.setattr(operator, "resolve_repository", lambda repo: tmp_path)
    monkeypatch.setattr(
        operator, "runtime_state_root", lambda repo: tmp_path / "runtime"
    )

    def fail_repair(*args: object, **kwargs: object) -> object:
        raise operator.OperatorError("terminal REPAIR failure")

    monkeypatch.setattr(operator, "run_repair_wakeup", fail_repair)
    monkeypatch.setattr(
        operator,
        "_project_operational_delivery",
        lambda root, **kwargs: projected.append((root, kwargs)),
    )

    profile = default_execution_profile("codex", "AUTHORIZATION", tmp_path)
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
                    "model": profile.model,
                    "reasoning_effort": profile.reasoning_effort,
                    "model_source": profile.model_source,
                    "effort_source": profile.effort_source,
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
