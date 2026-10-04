"""Repository-owned reusable handoff, Human gate and minimum permission bounds."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import shlex
import tomllib
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
READ_PERMISSIONS = {"contents": "read", "actions": "read"}
REUSABLE_WORKFLOW = "./.github/workflows/aios-local-chat-wake.yml"


def workflow(name="aios-local-chat-wake.yml"):
    text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def assert_caller_permission_ceiling(caller, callee):
    levels = {"none": 0, "read": 1, "write": 2}
    ceiling = caller["permissions"]
    required = {}
    # GitHub checks declarations even for jobs gated off in workflow_call.
    for job_name, job in callee["jobs"].items():
        for scope, level in job.get("permissions", callee["permissions"]).items():
            assert levels[level] <= levels[ceiling.get(scope, "none")], (
                f"{job_name} requires {scope}: {level} in the caller permission ceiling"
            )
            if levels[level] > levels[required.get(scope, "none")]:
                required[scope] = level
    assert ceiling == required == READ_PERMISSIONS


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
    assert job["permissions"] == READ_PERMISSIONS
    assert job["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert int(job["timeout-minutes"]) <= 5
    for forbidden in ("workflow_dispatch", "repository_dispatch", "createWorkflowDispatch", "issues:", "actions: write",
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


def test_terminal_reusable_handoff_has_no_dispatch_api_or_write_permission():
    parsed, text = workflow("aios-terminal-attention.yml")
    assert parsed["permissions"] == {"contents": "read", "issues": "write"}
    job = parsed["jobs"]["local-chat-wake"]
    assert job["needs"] == "admit-and-notify"
    assert job["uses"] == "./.github/workflows/aios-local-chat-wake.yml"
    assert job["permissions"] == READ_PERMISSIONS
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
    delivery = next(step for step in steps if step.get("id") == "admit")
    assert delivery["env"] == {
        "AIOS_WAKE_EVENT_ID": "${{ inputs.event_id }}", "AIOS_WAKE_REPOSITORY": "${{ inputs.repository }}",
        "AIOS_LOCAL_CHAT_WAKE_ENABLED": "${{ vars.AIOS_LOCAL_CHAT_WAKE_ENABLED }}",
        "GITHUB_TOKEN": "${{ github.token }}",
    }
    assert "python -m aios_renew.local_chat_wake" in delivery["run"]
    assert "exit $LASTEXITCODE" in delivery["run"]
    assert any(".[local-chat-wake]" in step.get("run", "") for step in steps)
    assert "AIOS_LOCAL_CHAT_WAKE_CONFIG" not in delivery["env"]
    assert "chatgpt.com/c/" not in text and "secrets." not in text
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert not any("playwright" in item for item in project["dependencies"])
    assert project["optional-dependencies"]["local-chat-wake"] == ["playwright>=1.51,<2"]


def test_reusable_freshness_token_is_current_run_and_step_local_only():
    parsed, text = workflow()
    job = parsed["jobs"]["deliver"]
    assert "env" not in parsed and "env" not in job
    steps = job["steps"]
    intake = next(step for step in steps if step.get("id") == "admit")
    follow_up = steps[-1]
    assert follow_up["env"] == {
        "AIOS_WAKE_REPOSITORY": "${{ inputs.repository }}",
        "AIOS_LOCAL_CHAT_WAKE_ENABLED": "${{ vars.AIOS_LOCAL_CHAT_WAKE_ENABLED }}",
        "GITHUB_TOKEN": "${{ github.token }}",
    }
    assert [step for step in steps if "GITHUB_TOKEN" in step.get("env", {})] == [intake, follow_up]
    assert intake["run"] == (
        "python -m aios_renew.local_chat_wake --event-id $env:AIOS_WAKE_EVENT_ID "
        "--repository $env:AIOS_WAKE_REPOSITORY\nexit $LASTEXITCODE\n"
    )
    assert follow_up["run"] == (
        "python -m aios_renew.local_chat_wake --drain --rechecks 2 --interval 15 "
        "--repository $env:AIOS_WAKE_REPOSITORY\nexit $LASTEXITCODE\n"
    )
    for step in steps:
        assert "GITHUB_TOKEN" not in step.get("run", "")
        assert "github.token" not in str(step.get("with", {}))
        assert "AIOS_LOCAL_CHAT_WAKE_CONFIG" not in step.get("env", {})
    for forbidden in ("secrets:", "secrets.", "secrets: inherit", "github_token", "GH_TOKEN",
                      "GITHUB_ENV", "curl", "gh api", "cdp_endpoint", "chat_url", "state_path"):
        assert forbidden not in text


@pytest.mark.parametrize("step_index", [-2, -1], ids=["intake", "follow-up"])
@pytest.mark.parametrize("guard,reason", [
    ("disabled", "ENABLE_GATE_CLOSED"),
    ("draft", "DRAFT_PRESENT"),
    ("generation", "GENERATION_ACTIVE"),
    ("binding-change", "BINDING_GENERATION_CHANGED"),
    ("unknown", "CANONICAL_UNKNOWN"),
    ("resolved", "CANONICALLY_RESOLVED"),
])
def test_authenticated_reusable_commands_preserve_local_submission_guards(
    step_index, guard, reason, tmp_path, monkeypatch, capsys,
):
    from aios_renew import local_chat_wake as wake

    parsed, _ = workflow()
    step = parsed["jobs"]["deliver"]["steps"][step_index]
    event_id = "terminal:RESULT:RUN-284-001:" + "a" * 40
    token = "ephemeral-current-workflow-token-fixture"
    expressions = {
        "${{ inputs.event_id }}": event_id,
        "${{ inputs.repository }}": "trung-via/AIOS-renew",
        "${{ vars.AIOS_LOCAL_CHAT_WAKE_ENABLED }}": "false" if guard == "disabled" else "true",
        "${{ github.token }}": token,
    }
    environment = {key: expressions[value] for key, value in step["env"].items()}
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    command = shlex.split(step["run"].splitlines()[0])
    assert command[:3] == ["python", "-m", "aios_renew.local_chat_wake"]
    arguments = [{"$env:" + key: value for key, value in environment.items()}.get(arg, arg)
                 for arg in command[3:]]
    binding = wake.Binding(
        "https://chatgpt.com/c/00000000-0000-0000-0000-000000000284",
        "http://127.0.0.1:9222", tmp_path / "lane.json", 1,
    )
    binding_reads = []
    submissions = []

    def local_binding(repository):
        assert repository == binding.repository
        binding_reads.append(repository)
        return replace(binding, generation=len(binding_reads)) if guard == "binding-change" else binding

    class Freshness:
        def observe(self, observed):
            assert observed == event_id
            return "UNKNOWN" if guard == "unknown" else "RESOLVED" if guard == "resolved" else "UNRESOLVED"

    class LocalSurface:
        # Exercise the production draft/generation check over an inert surface.
        check = wake.BrowserAdapter.check

        def __init__(self, selected_binding):
            self.binding = selected_binding
            self.page = SimpleNamespace(url=selected_binding.chat_url)
            self.browser = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def select_page(self, browser, url):
            return self.page

        def visible(self, selector):
            count = int(selector in {"main", wake.ACCOUNT, wake.COMPOSER}
                        or selector == wake.STOP and guard == "generation")
            return SimpleNamespace(count=lambda: count, get_attribute=lambda name: None,
                                   text_content=lambda: "Human draft" if guard == "draft" else "")

        def user_turn(self, text):
            return "ABSENT"

        def submit(self, *args, **kwargs):
            submissions.append(args)
            raise AssertionError("A guarded subject cannot reach submission")

    real_operate = wake.operate

    def isolated_operate(repository, selected_binding, event_id=None, adapter_factory=None,
                         projection=None, binding_provider=None, compact=False):
        return real_operate(repository, selected_binding, event_id, LocalSurface,
                            Freshness(), binding_provider, compact)

    monkeypatch.setattr(wake, "load_binding", local_binding)
    monkeypatch.setattr(wake, "operate", isolated_operate)
    monkeypatch.setattr(wake.time, "sleep", lambda seconds: None)
    if "--drain" in arguments:
        wake._admit_inbox(wake.State(binding.state_path), event_id)
    assert wake.main(arguments) == (1 if guard == "disabled" else 0)
    output = capsys.readouterr().out
    assert {json.loads(line)["reason"] for line in output.splitlines()} == {reason}
    assert not submissions
    assert token not in output and token not in wake.doorbell(event_id, binding.repository)
    if binding.state_path.exists():
        state_text = binding.state_path.read_text(encoding="utf-8")
        assert token not in state_text
        assert set(json.loads(state_text)["events"]) == {event_id}


@pytest.mark.parametrize("caller_name", ["aios-auto-publish.yml", "aios-terminal-attention.yml"])
def test_reusable_callers_have_exact_read_only_permission_ceiling_and_selectors(caller_name):
    callee, _ = workflow()
    caller_workflow, _ = workflow(caller_name)
    caller = caller_workflow["jobs"]["local-chat-wake"]
    assert caller["uses"] == REUSABLE_WORKFLOW
    assert set(caller) == {"needs", "if", "uses", "permissions", "with"}
    assert caller["with"] == {
        "event_id": "${{ needs." + caller["needs"] + ".outputs.event_id }}",
        "repository": "trung-via/AIOS-renew",
    }
    assert_caller_permission_ceiling(caller, callee)


@pytest.mark.parametrize("caller_name", ["aios-auto-publish.yml", "aios-terminal-attention.yml"])
def test_missing_actions_read_is_rejected_by_the_caller_contract(caller_name):
    callee, _ = workflow()
    caller_workflow, _ = workflow(caller_name)
    caller = deepcopy(caller_workflow["jobs"]["local-chat-wake"])
    caller["permissions"].pop("actions")
    with pytest.raises(AssertionError, match="requires actions: read"):
        assert_caller_permission_ceiling(caller, callee)


def test_all_repository_local_wake_callers_are_covered_by_the_permission_contract():
    callee, _ = workflow()
    callers = set()
    for path in (ROOT / ".github/workflows").iterdir():
        if path.suffix not in {".yml", ".yaml"}:
            continue
        parsed, _ = workflow(path.name)
        for job_name, job in parsed["jobs"].items():
            if job.get("uses") == REUSABLE_WORKFLOW:
                callers.add((path.name, job_name))
                assert_caller_permission_ceiling(job, callee)
    assert callers == {
        ("aios-auto-publish.yml", "local-chat-wake"),
        ("aios-terminal-attention.yml", "local-chat-wake"),
    }


@pytest.mark.parametrize("job_name,token_steps", [
    ("project", ["Reconstruct exact bounded source observations"]),
    ("deliver-projected", ["Admit into the existing durable project lane", "Bounded follow-up for admitted lane work"]),
    ("recheck", ["Recheck admitted lane work"]),
])
def test_projected_and_scheduled_paths_keep_the_same_step_local_actions_read_authentication(job_name, token_steps):
    parsed, _ = workflow()
    job = parsed["jobs"][job_name]
    assert job["permissions"] == READ_PERMISSIONS
    assert "env" not in parsed and "env" not in job
    authenticated = [step for step in job["steps"] if "GITHUB_TOKEN" in step.get("env", {})]
    assert [step["name"] for step in authenticated] == token_steps
    assert all(step["env"]["GITHUB_TOKEN"] == "${{ github.token }}" for step in authenticated)


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
    assert "--all-lanes" in command
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
    assert "aios_renew.local_chat_wake --event-id" in next(
        step for step in local["steps"] if step.get("id") == "admit")["run"]
    assert "aios_renew.local_chat_wake --drain" in parsed["jobs"]["recheck"]["steps"][-1]["run"]
    assert "workflow_run.conclusion" not in text
    assert "createWorkflowDispatch" not in text and "actions: write" not in text
    assert "aios run" not in text and "aios repair" not in text and "aios remediate" not in text
    assert "chatgpt.com" not in text and "cdp_endpoint" not in text and "next_action" not in text


@pytest.mark.parametrize("entry", ["deliver", "deliver-projected"])
@pytest.mark.parametrize("outcome,cancelled,allowed", [
    ("success", False, True), ("failure", False, True),
    ("skipped", False, False), ("success", True, False), ("failure", True, False),
])
def test_admission_entries_offer_finite_follow_up_even_after_durable_intake_delivery_failure(entry, outcome, cancelled, allowed):
    parsed, text = workflow()
    job = parsed["jobs"][entry]
    steps = job["steps"]
    admission_index = next(i for i, step in enumerate(steps) if step.get("id") == "admit")
    follow_up = steps[admission_index + 1]
    expression = follow_up["if"].removeprefix("${{").removesuffix("}}").strip()
    expression = expression.replace("!cancelled()", repr(not cancelled)).replace("steps.admit.outcome", repr(outcome))
    assert bool(eval(expression.replace("&&", " and "), {"__builtins__": {}}, {})) is allowed
    assert "--drain --rechecks 2 --interval 15" in follow_up["run"]
    assert "--event-id" not in follow_up["run"] and "brain_attention" not in follow_up["run"]
    assert "--lane-event-id $env:AIOS_WAKE_LANE_EVENT_ID" in follow_up["run"]
    assert follow_up["env"]["AIOS_WAKE_LANE_EVENT_ID"] == (
        "${{ inputs.event_id }}" if entry == "deliver" else "${{ matrix.event_id }}")
    assert "AIOS_WAKE_EVENT_ID" not in follow_up["env"]
    assert follow_up["env"]["AIOS_LOCAL_CHAT_WAKE_ENABLED"] == "${{ vars.AIOS_LOCAL_CHAT_WAKE_ENABLED }}"
    assert follow_up["env"]["AIOS_WAKE_REPOSITORY"] == (
        "${{ inputs.repository }}" if entry == "deliver" else "${{ github.repository }}")
    assert int(job["timeout-minutes"]) == 5
    assert "schedule" not in job["if"] and "schedule" not in follow_up["if"]
    assert set(job.get("permissions", parsed["permissions"])) <= {"contents", "actions"}
    assert all(value == "read" for value in job.get("permissions", parsed["permissions"]).values())
    for forbidden in ("actions: write", "repository_dispatch", "workflow_dispatch", "createWorkflowDispatch",
                      "secrets.", "next_action", "ChatGPT Work"):
        assert forbidden not in text


def test_schedule_is_documented_as_best_effort_and_both_delivery_entries_have_local_follow_up():
    parsed, text = workflow()
    assert "cron cadence is not a liveness guarantee" in text
    assert all("--drain --rechecks 2 --interval 15" in parsed["jobs"][entry]["steps"][-1]["run"]
               for entry in ("deliver", "deliver-projected"))
    document = (ROOT / "docs/AIOS-H4A5-COMPLETE-ATTENTION-COVERAGE-CONFORMANCE-v1.md").read_text(encoding="utf-8")
    for fact in ("37083846294", "11260375137", "37053912495", "37075944497", "CANONICAL_UNKNOWN",
                 "Human/Brain", "already-handled", "H4A5 closure", "H4B readiness"):
        assert fact in document
