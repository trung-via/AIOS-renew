"""Repository-owned reusable handoff, Human gate and minimum permission bounds."""

from pathlib import Path
import re
import tomllib

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name="aios-local-chat-wake.yml"):
    text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def condition(expression, context):
    # Evaluate the actual bounded workflow comparison/conjunction expression.
    expression = re.sub(
        r"\b(?:needs|vars|github|inputs)(?:\.[A-Za-z0-9_-]+)+",
        lambda match: repr(context.get(match[0], "")), expression,
    )
    return eval(expression.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}}, {})


def test_reusable_entry_accepts_only_bounded_identity_and_fixed_repository():
    parsed, text = workflow()
    assert set(parsed["on"]) == {"workflow_call"}
    call = parsed["on"]["workflow_call"]
    assert set(call) == {"inputs"}
    assert set(call["inputs"]) == {"event_id", "repository"}
    assert all(value["type"] == "string" and value["required"] == "true" for value in call["inputs"].values())
    assert parsed["permissions"] == {"contents": "read"}
    assert parsed["concurrency"] == {"group": "aios-local-chat-wake", "cancel-in-progress": "false"}
    job = parsed["jobs"]["deliver"]
    assert job["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert int(job["timeout-minutes"]) <= 5
    for forbidden in ("workflow_dispatch", "workflow_run", "schedule:", "issues:", "actions: write",
                      "aios run", "aios repair", "aios remediate", "upload-artifact", "playwright install",
                      "new_page", "launch_persistent_context", "secrets."):
        assert forbidden not in text


@pytest.mark.parametrize("enabled,allowed", [(None, False), ("", False), ("false", False), ("TRUE", False), ("true", True)])
def test_human_enable_gate_blocks_both_jobs_when_absent_or_false(enabled, allowed):
    local, _ = workflow()
    terminal, _ = workflow("aios-terminal-attention.yml")
    context = {
        "needs.admit-and-notify.result": "success",
        "github.repository": "trung-via/AIOS-renew",
        "inputs.repository": "trung-via/AIOS-renew",
    }
    if enabled is not None:
        context["vars.AIOS_LOCAL_CHAT_WAKE_ENABLED"] = enabled
    assert bool(condition(local["jobs"]["deliver"]["if"], context)) is allowed
    assert bool(condition(terminal["jobs"]["local-chat-wake"]["if"], context)) is allowed


@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled", ""])
def test_terminal_handoff_requires_successful_existing_admission_job(result):
    parsed, _ = workflow("aios-terminal-attention.yml")
    context = {"needs.admit-and-notify.result": result,
               "vars.AIOS_LOCAL_CHAT_WAKE_ENABLED": "true", "github.repository": "trung-via/AIOS-renew"}
    assert not condition(parsed["jobs"]["local-chat-wake"]["if"], context)


@pytest.mark.parametrize("field", ["github.repository", "inputs.repository"])
def test_reusable_delivery_rejects_repository_substitution(field):
    parsed, _ = workflow()
    context = {"vars.AIOS_LOCAL_CHAT_WAKE_ENABLED": "true", "github.repository": "trung-via/AIOS-renew",
               "inputs.repository": "trung-via/AIOS-renew"}
    context[field] = "other/repository"
    assert not condition(parsed["jobs"]["deliver"]["if"], context)


def test_terminal_reusable_handoff_has_no_dispatch_api_or_added_permission():
    parsed, text = workflow("aios-terminal-attention.yml")
    assert parsed["permissions"] == {"contents": "read", "issues": "write"}
    job = parsed["jobs"]["local-chat-wake"]
    assert job["needs"] == "admit-and-notify"
    assert job["uses"] == "./.github/workflows/aios-local-chat-wake.yml"
    assert job["permissions"] == {"contents": "read"}
    assert job["with"] == {
        "event_id": "${{ needs.admit-and-notify.outputs.event_id }}",
        "repository": "trung-via/AIOS-renew",
    }
    assert "steps" not in job and "secrets" not in job
    assert parsed["jobs"]["admit-and-notify"]["outputs"] == {
        "event_id": "terminal:${{ steps.admission.outputs.terminal_kind }}:${{ steps.admission.outputs.run_id }}:${{ steps.admission.outputs.artifact_sha }}",
    }
    for forbidden in ("createWorkflowDispatch", "actions: write", "workflow_run", "schedule:", "issues: [", "next_action"):
        assert forbidden not in text


def test_binding_is_machine_local_and_dependency_is_isolated():
    parsed, text = workflow()
    steps = parsed["jobs"]["deliver"]["steps"]
    assert steps[0]["with"] == {"ref": "main", "persist-credentials": "false"}
    delivery = steps[-1]
    assert delivery["env"] == {
        "AIOS_WAKE_EVENT_ID": "${{ inputs.event_id }}", "AIOS_WAKE_REPOSITORY": "${{ inputs.repository }}",
    }
    assert "python -m aios_renew.local_chat_wake" in delivery["run"]
    assert "exit $LASTEXITCODE" in delivery["run"]
    assert ".[local-chat-wake]" in steps[-2]["run"]
    assert "AIOS_LOCAL_CHAT_WAKE_CONFIG" not in delivery["env"]
    assert "chatgpt.com/c/" not in text and "secrets." not in text
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert not any("playwright" in item for item in project["dependencies"])
    assert project["optional-dependencies"]["local-chat-wake"] == ["playwright>=1.51,<2"]
