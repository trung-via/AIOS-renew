from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "aios-brain-wakeup.yml"
POLICY_PATH = ROOT / ".ai" / "brain-wakeup-carriers.yaml"
A1_PATH = ROOT / ".github" / "workflows" / "aios-self-hosted-wakeup.yml"


def _workflow() -> tuple[dict, str]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def test_issue_trigger_and_job_are_fixed_to_github_hosted_admission() -> None:
    workflow, text = _workflow()
    assert workflow["on"] == {"issues": {"types": ["opened"]}}
    assert list(workflow["jobs"]) == ["admit-and-dispatch"]
    job = workflow["jobs"]["admit-and-dispatch"]
    assert job["if"] == "github.event.issue.title == '[AIOS BRAIN WAKEUP]'"
    assert job["runs-on"] == "ubuntu-latest"
    assert "self-hosted" not in text
    assert "github.event.issue.body" not in text


def test_permissions_are_the_exact_minimum_and_checkout_is_fixed_main() -> None:
    workflow, _ = _workflow()
    assert workflow["permissions"] == {
        "contents": "read",
        "actions": "write",
        "issues": "write",
    }
    checkout = workflow["jobs"]["admit-and-dispatch"]["steps"][0]
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"] == {
        "ref": "main",
        "fetch-depth": "1",
        "persist-credentials": "false",
    }


def test_one_fixed_dispatch_forwards_only_three_sanitized_outputs() -> None:
    workflow, text = _workflow()
    steps = workflow["jobs"]["admit-and-dispatch"]["steps"]
    dispatch = next(step for step in steps if step.get("id") == "dispatch")
    script = dispatch["with"]["script"]

    assert text.count("createWorkflowDispatch") == 1
    assert "owner: 'trung-via'" in script
    assert "repo: 'AIOS-renew'" in script
    assert "workflow_id: 'aios-self-hosted-wakeup.yml'" in script
    assert "ref: 'main'" in script
    assert set(dispatch["env"]) == {
        "AIOS_DISPATCH_ID",
        "AIOS_TASK_ID",
        "AIOS_EXECUTOR",
    }
    assert set(
        line.split(":", 1)[0].strip()
        for line in script.splitlines()
        if line.strip().startswith(("dispatch_id:", "task_id:", "executor:"))
    ) == {"dispatch_id", "task_id", "executor"}
    for forbidden in (
        "github.event.issue.body",
        "GITHUB_EVENT_PATH",
        "workflow: process.env",
        "ref: process.env",
        "path:",
        "command:",
        "model:",
        "runner:",
        "credentials:",
    ):
        assert forbidden not in script


def test_carrier_never_calls_aios_or_executor_and_has_no_retry_or_polling() -> None:
    _, text = _workflow()
    lowered = text.lower()
    for forbidden in (
        "aios wakeup",
        "aios run",
        "aios remediate",
        "aios repair",
        "codex ",
        "antigravity ",
        "retry",
        "poll",
        "wait-for",
        "fallback",
        "reroute",
    ):
        assert forbidden not in lowered


def test_receipt_is_bounded_and_never_claims_execution_success() -> None:
    _, text = _workflow()
    assert text.count("github.rest.issues.createComment") == 1
    assert ".slice(0, 3500)" in text
    assert "status: DISPATCH_ACCEPTED" in text
    assert "execution_outcome: not_observed" in text
    assert "this is not RUN, verification, review, or publication success" in text
    assert "steps.admission.outcome == 'success'" in text


def test_policy_and_existing_a1_contract_are_separate_and_exact() -> None:
    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    assert policy == {
        "format": "AIOS_BRAIN_WAKEUP_CARRIERS_POLICY",
        "version": 1,
        "github_issue": {
            "enabled": True,
            "repository": "trung-via/AIOS-renew",
            "authorized_actors": ["trung-via"],
            "title_marker": "[AIOS BRAIN WAKEUP]",
            "max_body_bytes": 4096,
        },
    }
    a1 = yaml.load(A1_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert list(a1["on"]) == ["workflow_dispatch"]
    assert set(a1["on"]["workflow_dispatch"]["inputs"]) == {
        "dispatch_id",
        "task_id",
        "executor",
    }
