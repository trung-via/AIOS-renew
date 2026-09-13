from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "aios-brain-ingress.yml"
POLICY_PATH = ROOT / ".ai" / "brain-ingress-carriers.yaml"


def _workflow() -> tuple[dict, str]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # BaseLoader preserves the GitHub Actions `on` key rather than applying YAML 1.1 booleans.
    return yaml.load(text, Loader=yaml.BaseLoader), text


def test_workflow_has_only_the_opened_issue_trigger_and_exact_marker_gate() -> None:
    workflow, _ = _workflow()
    assert workflow["on"] == {"issues": {"types": ["opened"]}}
    job = workflow["jobs"]["deliver"]
    assert job["if"] == "github.event.issue.title == '[AIOS BRAIN INGRESS]'"
    assert "issue_comment" not in workflow["on"]
    assert "workflow_dispatch" not in workflow["on"]


def test_workflow_permissions_and_concurrency_are_minimal_and_serial() -> None:
    workflow, _ = _workflow()
    assert workflow["permissions"] == {"contents": "write", "issues": "write"}
    assert workflow["concurrency"] == {
        "group": "aios-brain-ingress-${{ github.repository }}",
        "cancel-in-progress": "false",
    }


def test_workflow_checks_out_canonical_main_with_full_history() -> None:
    workflow, _ = _workflow()
    checkout = workflow["jobs"]["deliver"]["steps"][0]
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"] == {"ref": "main", "fetch-depth": "0"}


def test_workflow_has_one_event_file_carrier_invocation_and_no_body_interpolation() -> None:
    workflow, text = _workflow()
    runs = [step["run"] for step in workflow["jobs"]["deliver"]["steps"] if "run" in step]
    carrier_calls = [run for run in runs if "aios_renew.github_issue_ingress" in run]
    assert len(carrier_calls) == 1
    assert '--event "$GITHUB_EVENT_PATH"' in carrier_calls[0]
    assert "github.event.issue.body" not in text
    assert "aios ingress" not in text
    assert "aios ingest" not in text
    assert "git push" not in text
    assert "git update-ref" not in text
    assert "createOrUpdateFileContents" not in text
    assert "createRef" not in text


def test_workflow_posts_one_bounded_receipt_and_only_closes_success() -> None:
    workflow, text = _workflow()
    receipt_step = workflow["jobs"]["deliver"]["steps"][4]
    assert receipt_step["if"] == "always()"
    assert text.count("github.rest.issues.createComment") == 1
    assert ".slice(0, 3500)" in text
    assert text.count("github.rest.issues.update") == 1
    assert "AIOS_DELIVERY_OUTCOME === 'success'" in text
    assert "always() && steps.ingress.outcome != 'success'" in text


def test_reviewed_policy_binds_exact_repository_actor_and_marker() -> None:
    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    assert policy == {
        "format": "AIOS_BRAIN_INGRESS_CARRIERS_POLICY",
        "version": 1,
        "github_issue": {
            "enabled": True,
            "repository": "trung-via/AIOS-renew",
            "authorized_actors": ["trung-via"],
            "title_marker": "[AIOS BRAIN INGRESS]",
            "max_body_bytes": 131072,
        },
    }
