from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ENTRY = WORKFLOWS / "aios-issue-carrier.yml"
INGRESS_WORKFLOW = "aios-brain-ingress.yml"
INGRESS_SECRETS = {
    "AIOS_ORIGIN_ADMISSION_KEY": "${{ secrets.AIOS_ORIGIN_ADMISSION_KEY }}",
}
INGRESS_WORKFLOW_CALL = {
    "secrets": {"AIOS_ORIGIN_ADMISSION_KEY": {"required": "true"}},
}
ROUTES = {
    "[AIOS BRAIN INGRESS]": "aios-brain-ingress.yml",
    "[AIOS BRAIN WAKEUP]": "aios-brain-wakeup.yml",
    "[AIOS REPAIR WAKEUP]": "aios-brain-repair-wakeup.yml",
    "[AIOS REMEDIATION INTENT]": "aios-brain-remediation-intent.yml",
}
PERMISSIONS = {
    "aios-brain-ingress.yml": {
        "contents": "write", "issues": "write", "actions": "write",
    },
    "aios-brain-wakeup.yml": {
        "contents": "read", "actions": "write", "issues": "write",
    },
    "aios-brain-repair-wakeup.yml": {"contents": "read", "issues": "write"},
    "aios-brain-remediation-intent.yml": {"contents": "read", "issues": "write"},
}
ADMISSION_JOBS = {
    "aios-brain-ingress.yml": "deliver",
    "aios-brain-wakeup.yml": "admit-and-dispatch",
    "aios-brain-repair-wakeup.yml": "admit",
    "aios-brain-remediation-intent.yml": "admit",
}


def _workflow(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def _assert_reusable_trigger(workflow: dict, name: str) -> None:
    interface = INGRESS_WORKFLOW_CALL if name == INGRESS_WORKFLOW else ""
    assert workflow["on"] == {"workflow_call": interface}


def _assert_native_call_interface(job: dict, name: str) -> None:
    expected_keys = {"needs", "if", "permissions", "uses"}
    if name == INGRESS_WORKFLOW:
        expected_keys.add("secrets")
        assert job.get("secrets") == INGRESS_SECRETS
    assert set(job) == expected_keys


def _bash() -> str:
    # Git for Windows supplies Bash; avoid the WindowsApps WSL launcher.
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    bash = shutil.which("bash")
    assert bash is not None, "Bash is required to exercise the hosted title selector"
    return bash


def _select(title: str, tmp_path: Path, body: str = "") -> str:
    workflow, _ = _workflow(ENTRY)
    step = workflow["jobs"]["route"]["steps"][0]
    # Supply the original opened-Issue event unchanged, as native workflow reuse does.
    event_path = tmp_path / "event.json"
    original = json.dumps({
        "action": "opened",
        "repository": {"full_name": "trung-via/AIOS-renew"},
        "sender": {"login": "trung-via"},
        "issue": {
            "id": 12345, "number": 42, "title": title, "body": body,
            "user": {"login": "trung-via"},
        },
    }).encode("utf-8")
    event_path.write_bytes(original)
    output_path = tmp_path / "output.txt"
    output_path.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "AIOS_ISSUE_TITLE": title,
        "GITHUB_EVENT_NAME": "issues",
        "GITHUB_EVENT_PATH": event_path.as_posix(),
        "GITHUB_OUTPUT": output_path.as_posix(),
    }
    completed = subprocess.run(
        [_bash(), "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert event_path.read_bytes() == original
    output = output_path.read_text(encoding="utf-8")
    assert output.startswith("carrier=") and output.count("\n") == 1
    return output.removeprefix("carrier=").removesuffix("\n")


def _activated_carriers(selected: str) -> list[str]:
    workflow, _ = _workflow(ENTRY)
    activated = []
    for job in workflow["jobs"].values():
        if "uses" not in job:
            continue
        match = re.fullmatch(r"needs\.route\.outputs\.carrier == '([^']+)'", job["if"])
        assert match is not None
        # GitHub's comparison is case-insensitive; selected is a fixed script output.
        if selected.casefold() == match[1].casefold():
            activated.append(job["uses"])
    return activated


def test_family_has_one_opened_subscription_and_four_reusable_only_carriers() -> None:
    subscriptions = []
    for path in [ENTRY, *(WORKFLOWS / name for name in ROUTES.values())]:
        workflow, _ = _workflow(path)
        if "issues" in workflow["on"]:
            subscriptions.append(path.name)
            assert workflow["on"] == {"issues": {"types": ["opened"]}}
        else:
            # No manual, comment, schedule, polling or workflow_run entry path.
            _assert_reusable_trigger(workflow, path.name)
    assert subscriptions == [ENTRY.name]


def test_entry_has_only_four_fixed_native_calls_with_route_scoped_permissions() -> None:
    workflow, _ = _workflow(ENTRY)
    assert set(workflow) == {"name", "on", "permissions", "jobs"}
    assert workflow["permissions"] == {}
    assert len(workflow["jobs"]) == 5
    calls = [job for job in workflow["jobs"].values() if "uses" in job]
    assert len(calls) == 4
    assert {job["uses"] for job in calls} == {
        f"./.github/workflows/{name}" for name in ROUTES.values()
    }
    for job in calls:
        # Only ingress receives its named deployment key; no replacement event,
        # semantic inputs, inherited secrets or caller concurrency are admitted.
        name = Path(job["uses"]).name
        _assert_native_call_interface(job, name)
        assert job["needs"] == "route"
        assert job["if"] == f"needs.route.outputs.carrier == '{name}'"
        carrier, _ = _workflow(WORKFLOWS / name)
        assert job["permissions"] == carrier["permissions"] == PERMISSIONS[name]


@pytest.mark.parametrize("secrets", [
    None,
    "inherit",
    {"AIOS_ORIGIN_ADMISSION_KEY_RENAMED": INGRESS_SECRETS["AIOS_ORIGIN_ADMISSION_KEY"]},
    {"AIOS_ORIGIN_ADMISSION_KEY": "${{ secrets.AIOS_ORIGIN_ADMISSION_KEY_RENAMED }}"},
    {**INGRESS_SECRETS, "UNRELATED": "${{ secrets.UNRELATED }}"},
])
def test_ingress_call_rejects_missing_renamed_or_broadened_secret_interface(
    secrets: object,
) -> None:
    workflow, _ = _workflow(ENTRY)
    job = workflow["jobs"]["ingress"]
    job.pop("secrets", None)
    if secrets is not None:
        job["secrets"] = secrets
    with pytest.raises(AssertionError):
        _assert_native_call_interface(job, INGRESS_WORKFLOW)


def test_selector_reads_only_title_and_has_four_literal_equality_routes() -> None:
    workflow, text = _workflow(ENTRY)
    route = workflow["jobs"]["route"]
    assert set(route) == {"runs-on", "timeout-minutes", "outputs", "steps"}
    assert route["runs-on"] == "ubuntu-latest"
    assert route["timeout-minutes"] == "1"
    assert route["outputs"] == {"carrier": "${{ steps.title.outputs.carrier }}"}
    assert len(route["steps"]) == 1
    step = route["steps"][0]
    assert set(step) == {"name", "id", "shell", "env", "run"}
    assert step["id"] == "title"
    assert step["shell"] == "bash"
    assert step["env"] == {"AIOS_ISSUE_TITLE": "${{ github.event.issue.title }}"}
    pairs = re.findall(
        r"(?:if|elif) \[\[ \"\$AIOS_ISSUE_TITLE\" == '([^']+)' \]\]; then\s+"
        r"carrier='([^']+)'", step["run"],
    )
    assert len(pairs) == 4 and dict(pairs) == ROUTES
    assert "${{" not in step["run"]
    assert step["run"].startswith("carrier=''\n")
    assert step["run"].endswith('printf \'carrier=%s\\n\' "$carrier" >> "$GITHUB_OUTPUT"\n')
    # Exempt only the exact ingress secret forwarding block from the existing
    # whole-entry prohibition; the selector and other three calls stay secret-free.
    secret_block = (
        "    secrets:\n"
        "      AIOS_ORIGIN_ADMISSION_KEY: ${{ secrets.AIOS_ORIGIN_ADMISSION_KEY }}\n"
    )
    assert text.count(secret_block) == 1
    boundary_text = text.replace(secret_block, "", 1)
    for forbidden in (
        "github.event.issue.body", "GITHUB_EVENT_PATH", "next_action", "NEXT",
        "correction_strategy", "provider", "model", "executor", "runtime",
        "review", "publication", "roadmap", "TASK", "RUN", "Unified State",
        "python", "checkout", "createWorkflowDispatch", "createComment",
        "workflow_run", "schedule", "secrets", "inputs.", "curl", "wget",
    ):
        assert forbidden not in boundary_text


@pytest.mark.parametrize(("title", "name"), list(ROUTES.items()))
def test_recognized_title_activates_only_its_native_reusable_carrier(
    title: str, name: str, tmp_path: Path,
) -> None:
    # Conflicting body content cannot influence this syntactic boundary.
    selected = _select(title, tmp_path, body="\n".join(reversed(ROUTES)))
    assert selected == name
    assert _activated_carriers(selected) == [f"./.github/workflows/{name}"]


@pytest.mark.parametrize("title", [
    "", "ordinary issue", "__proto__", "constructor",
    "$(exit 17)", "`exit 17`", "' ; exit 17 #", "\n[AIOS BRAIN INGRESS]",
    *(
        variant
        for marker in ROUTES
        for variant in (
            marker.lower(), marker.title(), " " + marker, marker + " ",
            marker + "\n", marker + " suffix", "prefix " + marker,
        )
    ),
])
def test_unknown_and_non_exact_titles_activate_no_carrier(title: str, tmp_path: Path) -> None:
    selected = _select(title, tmp_path, body="\n".join(ROUTES))
    assert selected == ""
    assert _activated_carriers(selected) == []


@pytest.mark.parametrize(("title", "name"), list(ROUTES.items()))
def test_native_reuse_preserves_original_issue_event_admission_and_receipt_contract(
    title: str, name: str,
) -> None:
    # GitHub binds reusable workflows to the caller's github context:
    # https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations#github-context
    carrier, text = _workflow(WORKFLOWS / name)
    _assert_reusable_trigger(carrier, name)
    admission_job = carrier["jobs"][ADMISSION_JOBS[name]]
    title_gate = f"github.event.issue.title == '{title}'"
    assert admission_job["if"] == (
        f"always() && {title_gate}" if name == INGRESS_WORKFLOW else title_gate
    )
    admission_steps = [
        step for step in admission_job["steps"]
        if "aios_renew.github_issue_" in step.get("run", "")
    ]
    assert len(admission_steps) == 1
    assert '--event "$GITHUB_EVENT_PATH"' in admission_steps[0]["run"]
    assert "context.issue.number" in text
    assert "inputs." not in text

    def check_environment(node: object) -> None:
        if isinstance(node, dict):
            assert not any(key.startswith("GITHUB_") for key in node.get("env", {}))
            for value in node.values():
                check_environment(value)
        elif isinstance(node, list):
            for value in node:
                check_environment(value)

    check_environment(carrier)
    if name == INGRESS_WORKFLOW:
        # Framing, local admission and hosted delivery all use the same native
        # event file; none serializes a replacement Issue or caller context.
        assert text.count("GITHUB_EVENT_PATH") == 3
        frame = carrier["jobs"]["origin_request"]
        assert frame["if"] == title_gate
        frame_step = next(step for step in frame["steps"] if step.get("id") == "frame")
        assert '--event "$GITHUB_EVENT_PATH"' in frame_step["run"]
        gate_steps = [
            step for step in carrier["jobs"]["origin_provenance"]["steps"]
            if "aios_renew.github_issue_ingress" in step.get("run", "")
        ]
        assert len(gate_steps) == 1
        assert '--event "$env:GITHUB_EVENT_PATH"' in gate_steps[0]["run"]
    else:
        assert text.count("GITHUB_EVENT_PATH") == 1
    checkout = admission_job["steps"][0]
    assert checkout["with"]["ref"] == (
        "main" if name == "aios-brain-ingress.yml" else "${{ github.sha }}"
    )
    group = name.removesuffix(".yml")
    assert carrier["concurrency"] == {
        "group": f"{group}-${{{{ github.repository }}}}",
        "cancel-in-progress": "false",
    }
