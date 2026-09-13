from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/aios-terminal-attention.yml"
POLICY_PATH = ROOT / ".ai/brain-terminal-attention-carriers.yaml"


def workflow() -> tuple[dict, str]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def test_triggers_are_bounded_to_attention_ref_and_exact_manual_replay() -> None:
    parsed, _ = workflow()
    assert set(parsed["on"]) == {"push", "workflow_dispatch"}
    assert parsed["on"]["push"]["branches"] == ["aios/terminal-attention/**"]
    assert set(parsed["on"]["workflow_dispatch"]["inputs"]) == {
        "run_id",
        "terminal_kind",
        "artifact_sha",
    }
    assert parsed["permissions"] == {"contents": "read", "issues": "write"}
    assert parsed["jobs"]["admit-and-notify"]["runs-on"] == "ubuntu-latest"


def test_privileged_workflow_checks_out_main_and_runs_only_reviewed_admission() -> None:
    parsed, text = workflow()
    steps = parsed["jobs"]["admit-and-notify"]["steps"]
    checkout = steps[0]
    assert checkout["with"] == {
        "ref": "main",
        "fetch-depth": "0",
        "persist-credentials": "false",
    }
    admission = next(step for step in steps if step.get("id") == "admission")
    assert "python -m aios_renew.terminal_attention" in admission["run"]
    assert "--event-sha \"$GITHUB_SHA\"" in admission["run"]
    assert text.count("github.rest.issues.create") == 1
    assert "createWorkflowDispatch" not in text
    assert "aios run" not in text.lower()
    assert "aios remediate" not in text.lower()
    assert "aios repair" not in text.lower()


def test_issue_body_contains_only_strict_inert_selector_fields() -> None:
    parsed, text = workflow()
    notify = next(
        step
        for step in parsed["jobs"]["admit-and-notify"]["steps"]
        if step.get("id") == "notify"
    )
    script = notify["with"]["script"]
    assert "const title = '[AIOS TERMINAL ATTENTION]'" in script
    assert "format: AIOS_TERMINAL_ATTENTION" in script
    assert set(notify["env"]) == {
        "AIOS_RUN_ID",
        "AIOS_TERMINAL_KIND",
        "AIOS_ARTIFACT_SHA",
    }
    forbidden = re.compile(
        r"\b(verdict|correction|executor|approval|verification|publication|command|task_text)\s*:",
        re.IGNORECASE,
    )
    assert forbidden.search(script) is None
    assert "state: 'all'" in script
    assert "REUSED" in script
    assert "conflicting terminal-attention Issue" in script
    assert "createComment" not in text


def test_policy_is_exact_and_dedicated() -> None:
    assert yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) == {
        "format": "AIOS_BRAIN_TERMINAL_ATTENTION_CARRIERS_POLICY",
        "version": 1,
        "github_issue": {
            "enabled": True,
            "repository": "trung-via/AIOS-renew",
            "main_ref": "refs/heads/main",
            "signal_prefix": "refs/heads/aios/terminal-attention",
            "title_marker": "[AIOS TERMINAL ATTENTION]",
        },
    }
