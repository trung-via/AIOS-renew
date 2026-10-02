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
    # Model GitHub's implicit success() gate unless an explicit status function
    # overrides it, then evaluate the actual bounded workflow expression.
    if "always()" not in expression and context.get("needs.admit-and-notify.result", "success") != "success":
        return False
    expression = expression.replace("always()", "True")
    expression = re.sub(
        r"\b(?:needs|vars|github|inputs)(?:\.[A-Za-z0-9_-]+)+",
        lambda match: repr(context.get(match[0], "")), expression,
    )
    translated = expression.replace("&&", " and ").replace("||", " or ")
    return eval(f"({translated})", {"__builtins__": {}}, {})


def admitted_context(job_result="success", terminal_kind="RESULT"):
    return {
        "needs.admit-and-notify.result": job_result,
        "needs.admit-and-notify.outputs.admission_status": "success",
        "needs.admit-and-notify.outputs.run_id": "RUN-262-004",
        "needs.admit-and-notify.outputs.terminal_kind": terminal_kind,
        "needs.admit-and-notify.outputs.artifact_sha": "a" * 40,
        "vars.AIOS_LOCAL_CHAT_WAKE_ENABLED": "true",
        "github.repository": "trung-via/AIOS-renew",
    }


def test_reusable_entry_accepts_only_bounded_identity_and_fixed_repository():
    parsed, text = workflow()
    assert set(parsed["on"]) == {"workflow_call", "schedule", "workflow_run"}
    call = parsed["on"]["workflow_call"]
    assert set(call) == {"inputs"}
    assert set(call["inputs"]) == {"event_id", "repository"}
    assert all(value["type"] == "string" and value["required"] == "true" for value in call["inputs"].values())
    assert parsed["permissions"] == {"contents": "read"}
    assert "concurrency" not in parsed  # GitHub's replaceable pending slot loses intake.
    job = parsed["jobs"]["deliver"]
    assert job["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert int(job["timeout-minutes"]) <= 5
    for forbidden in ("workflow_dispatch", "issues:", "actions: write",
                      "aios run", "aios repair", "aios remediate", "upload-artifact", "playwright install",
                      "new_page", "launch_persistent_context", "secrets."):
        assert forbidden not in text


@pytest.mark.parametrize("enabled,allowed", [(None, False), ("", False), ("false", False), ("TRUE", False), ("true", True)])
def test_human_enable_gate_blocks_both_jobs_when_absent_or_false(enabled, allowed):
    local, _ = workflow()
    terminal, _ = workflow("aios-terminal-attention.yml")
    context = admitted_context()
    context["inputs.repository"] = "trung-via/AIOS-renew"
    context.pop("vars.AIOS_LOCAL_CHAT_WAKE_ENABLED")
    if enabled is not None:
        context["vars.AIOS_LOCAL_CHAT_WAKE_ENABLED"] = enabled
    assert bool(condition(local["jobs"]["deliver"]["if"], context)) is allowed
    assert bool(condition(terminal["jobs"]["local-chat-wake"]["if"], context)) is allowed


@pytest.mark.parametrize("status", ["failure", "skipped", "cancelled", "", "unknown"])
@pytest.mark.parametrize("job_result", ["success", "failure"])
def test_terminal_handoff_rejects_failed_absent_or_ambiguous_admission(status, job_result):
    parsed, _ = workflow("aios-terminal-attention.yml")
    context = admitted_context(job_result)
    context["needs.admit-and-notify.outputs.admission_status"] = status
    assert not condition(parsed["jobs"]["local-chat-wake"]["if"], context)


@pytest.mark.parametrize("field,value", [
    ("run_id", ""), ("artifact_sha", ""),
    ("terminal_kind", ""), ("terminal_kind", "UNKNOWN"),
])
def test_terminal_handoff_rejects_incomplete_or_ambiguous_admission_identity(field, value):
    parsed, _ = workflow("aios-terminal-attention.yml")
    context = admitted_context()
    context[f"needs.admit-and-notify.outputs.{field}"] = value
    assert not condition(parsed["jobs"]["local-chat-wake"]["if"], context)


def test_terminal_handoff_rejects_missing_admission_outputs():
    parsed, _ = workflow("aios-terminal-attention.yml")
    context = {"needs.admit-and-notify.result": "failure",
               "vars.AIOS_LOCAL_CHAT_WAKE_ENABLED": "true", "github.repository": "trung-via/AIOS-renew"}
    assert not condition(parsed["jobs"]["local-chat-wake"]["if"], context)


@pytest.mark.parametrize("failed_step", [
    "Create or reuse the strict Brain-attention Issue",
    "Write exact wake source selectors",
    "Export bounded wake source selectors",
])
@pytest.mark.parametrize("terminal_kind", ["RESULT", "FAILURE"])
def test_post_admission_observability_failure_cannot_suppress_local_wake(failed_step, terminal_kind):
    parsed, _ = workflow("aios-terminal-attention.yml")
    steps = parsed["jobs"]["admit-and-notify"]["steps"]
    names = [step["name"] for step in steps]
    admission_index = next(index for index, step in enumerate(steps) if step.get("id") == "admission")
    assert names.index(failed_step) > admission_index
    context = admitted_context(job_result="failure", terminal_kind=terminal_kind)
    assert condition(parsed["jobs"]["local-chat-wake"]["if"], context)


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
        "admission_status": "${{ steps.admission.outcome }}",
        "run_id": "${{ steps.admission.outputs.run_id }}",
        "terminal_kind": "${{ steps.admission.outputs.terminal_kind }}",
        "artifact_sha": "${{ steps.admission.outputs.artifact_sha }}",
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
        "AIOS_LOCAL_CHAT_WAKE_ENABLED": "${{ vars.AIOS_LOCAL_CHAT_WAKE_ENABLED }}",
    }
    assert "python -m aios_renew.local_chat_wake" in delivery["run"]
    assert "exit $LASTEXITCODE" in delivery["run"]
    assert ".[local-chat-wake]" in steps[-2]["run"]
    assert "AIOS_LOCAL_CHAT_WAKE_CONFIG" not in delivery["env"]
    assert "chatgpt.com/c/" not in text and "secrets." not in text
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert not any("playwright" in item for item in project["dependencies"])
    assert project["optional-dependencies"]["local-chat-wake"] == ["playwright>=1.51,<2"]


@pytest.mark.parametrize("enabled,allowed", [(None, False), ("false", False), ("true", True)])
def test_timer_rechecks_only_admitted_local_work_under_human_gate(enabled, allowed):
    parsed, text = workflow()
    job = parsed["jobs"]["recheck"]
    context = {"vars.AIOS_LOCAL_CHAT_WAKE_ENABLED": enabled,
               "github.repository": "trung-via/AIOS-renew", "github.event_name": "schedule"}
    assert bool(condition(job["if"], context)) is allowed
    context["github.event_name"] = "push"
    assert not condition(job["if"], context)
    context["github.event_name"] = "schedule"
    context["github.repository"] = "other/project"
    assert not condition(job["if"], context)
    assert parsed["on"]["schedule"] == [{"cron": "*/5 * * * *"}]
    assert parsed["permissions"] == {"contents": "read"}
    assert int(job["timeout-minutes"]) == 5
    command = job["steps"][-1]["run"]
    assert "--drain --compact --rechecks 4 --interval 15" in command
    assert "--event-id" not in command
    assert "AIOS_WAKE_EVENT_ID" not in job["steps"][-1]["env"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    for forbidden in ("actions: write", "createWorkflowDispatch", "next_action", "ChatGPT Work", "upload-artifact"):
        assert forbidden not in text


def test_source_fan_in_is_read_only_bounded_and_enters_the_existing_local_lane():
    parsed, text = workflow()
    trigger = parsed["on"]["workflow_run"]
    assert trigger["types"] == ["completed"]
    assert set(trigger["workflows"]) == {
        "AIOS Issue carrier entry", "AIOS self-hosted primary wakeup", "AIOS self-hosted REPAIR wakeup",
        "AIOS approved remediation wakeup", "AIOS auto-publish reviewed candidate",
    }
    assert parsed["jobs"]["project"]["permissions"] == {"contents": "read", "actions": "read"}
    project = parsed["jobs"]["project"]
    assert "head_repository.full_name == 'trung-via/AIOS-renew'" in project["if"]
    assert "vars.AIOS_LOCAL_CHAT_WAKE_ENABLED == 'true'" in project["if"]
    steps = project["steps"]
    assert steps[0]["with"] == {"ref": "main", "persist-credentials": "false"}
    assert "aios_renew.brain_attention collect" in steps[-1]["run"]
    assert steps[-1]["env"]["AIOS_SOURCE_ATTEMPT"] == "${{ github.event.workflow_run.run_attempt }}"
    local = parsed["jobs"]["deliver-projected"]
    assert local["needs"] == "project"
    assert "has_events == 'true'" in local["if"]
    assert local["strategy"]["fail-fast"] == "false"
    assert "aios_renew.local_chat_wake --event-id" in local["steps"][-1]["run"]
    assert "aios_renew.local_chat_wake --drain" in parsed["jobs"]["recheck"]["steps"][-1]["run"]
    assert "workflow_run.conclusion" not in text
    assert "createWorkflowDispatch" not in text and "actions: write" not in text
    assert "aios run" not in text and "aios repair" not in text and "aios remediate" not in text
    assert "chatgpt.com" not in text and "cdp_endpoint" not in text and "next_action" not in text
