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
    assert workflow["permissions"] == {
        "contents": "write",
        "issues": "write",
        "actions": "write",
    }
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
    assert '--output "$GITHUB_OUTPUT"' in carrier_calls[0]
    assert "github.event.issue.body" not in text
    assert "aios ingress" not in text
    assert "aios ingest" not in text
    assert "git push" not in text
    assert "git update-ref" not in text
    assert "createOrUpdateFileContents" not in text
    assert "createRef" not in text


def test_workflow_posts_one_bounded_receipt_and_only_closes_success() -> None:
    workflow, text = _workflow()
    receipt_step = next(
        step
        for step in workflow["jobs"]["deliver"]["steps"]
        if step.get("name") == "Post bounded transport receipt"
    )
    assert receipt_step["if"] == "always()"
    assert text.count("github.rest.issues.createComment") == 1
    assert ".slice(0, 3500)" in text
    assert text.count("github.rest.issues.update") == 1
    assert receipt_step["env"] == {
        "AIOS_RECEIPT_PATH": "${{ runner.temp }}/aios-brain-ingress-receipt.txt",
        "AIOS_INGRESS_OUTCOME": "${{ steps.ingress.outcome }}",
        "AIOS_DISPATCH_OUTCOME": "${{ steps.dispatch.outcome }}",
        "AIOS_PUBLICATION_RUN_ID": "${{ steps.ingress.outputs.publication_run_id }}",
    }
    assert "if (fullySuccessful)" in text
    assert (
        "always() && (steps.ingress.outcome != 'success' || (steps.ingress.outputs.publication_run_id != '' && steps.dispatch.outcome != 'success'))"
        in text
    )


def test_workflow_distinguishes_ingress_success_from_publication_dispatch_acceptance() -> None:
    _, text = _workflow()
    assert "publication_dispatch: ACCEPTED" in text
    assert "dispatch_accepted: true" in text
    assert (
        "detail: GitHub accepted safe publication dispatch; this is not publication success or verdict."
        in text
    )
    assert "publication_dispatch: REJECTED" in text
    assert "dispatch_accepted: false" in text
    assert "reason: GitHub did not accept safe publication dispatch." in text


def test_workflow_fails_closed_when_publication_dispatch_is_not_accepted() -> None:
    workflow, _ = _workflow()
    steps = workflow["jobs"]["deliver"]["steps"]
    fail_step = steps[-1]
    assert fail_step["name"] == "Preserve failed delivery outcome"
    assert "steps.dispatch.outcome != 'success'" in fail_step["if"]
    assert "steps.ingress.outputs.publication_run_id != ''" in fail_step["if"]


def test_workflow_dispatches_safe_publisher_once_with_only_canonical_run_selector() -> None:
    workflow, text = _workflow()
    steps = workflow["jobs"]["deliver"]["steps"]
    dispatch = next(
        (step for step in steps if step.get("id") == "dispatch"),
        None,
    )
    assert dispatch is not None
    assert dispatch["uses"] == "actions/github-script@v7"
    assert (
        dispatch["if"]
        == "steps.ingress.outcome == 'success' && steps.ingress.outputs.publication_run_id != ''"
    )
    assert dispatch["env"] == {
        "AIOS_RUN_ID": "${{ steps.ingress.outputs.publication_run_id }}"
    }

    script = dispatch["with"]["script"]
    assert text.count("createWorkflowDispatch") == 1
    assert "workflow_id: 'aios-auto-publish.yml'" in script
    assert "ref: 'main'" in script
    assert "run_id: process.env.AIOS_RUN_ID" in script
    assert "owner: context.repo.owner" in script
    assert "repo: context.repo.repo" in script

    for forbidden in (
        "github.event.issue.body",
        "GITHUB_EVENT_PATH",
        "candidate_sha",
        "target_ref",
        "force",
        "verdict",
        "command",
    ):
        assert forbidden not in script


def test_carrier_workflow_never_directly_mutates_main_or_invokes_publication_module() -> None:
    _, text = _workflow()
    lowered = text.lower()
    for forbidden in (
        "git push",
        "git update-ref",
        "aios_renew.publication",
        "publication.py",
        "createorupdatefilecontents",
        "createref",
    ):
        assert forbidden not in lowered


def test_auto_publish_workflow_contract_retains_push_and_workflow_dispatch() -> None:
    auto_publish_path = ROOT / ".github" / "workflows" / "aios-auto-publish.yml"
    auto_publish = yaml.load(auto_publish_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert "push" in auto_publish["on"]
    assert auto_publish["on"]["push"]["branches"] == ["aios/review-decision/**"]
    assert "workflow_dispatch" in auto_publish["on"]
    assert "run_id" in auto_publish["on"]["workflow_dispatch"]["inputs"]
    assert auto_publish["on"]["workflow_dispatch"]["inputs"]["run_id"]["required"] == "true"


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
