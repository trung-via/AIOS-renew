from __future__ import annotations

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "aios-brain-ingress.yml"
POLICY_PATH = ROOT / ".ai" / "brain-ingress-carriers.yaml"


def _workflow() -> tuple[dict, str]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # BaseLoader preserves the GitHub Actions `on` key rather than applying YAML 1.1 booleans.
    return yaml.load(text, Loader=yaml.BaseLoader), text


def _assert_required_origin_secret(workflow: dict) -> None:
    assert workflow["on"] == {
        "workflow_call": {
            "secrets": {"AIOS_ORIGIN_ADMISSION_KEY": {"required": "true"}},
        },
    }


def test_workflow_is_reusable_only_and_retains_exact_marker_gate() -> None:
    workflow, _ = _workflow()
    _assert_required_origin_secret(workflow)
    job = workflow["jobs"]["deliver"]
    assert job["if"] == "always() && github.event.issue.title == '[AIOS BRAIN INGRESS]'"
    assert "issue_comment" not in workflow["on"]
    assert "workflow_dispatch" not in workflow["on"]


@pytest.mark.parametrize("secrets", [
    None,
    {"AIOS_ORIGIN_ADMISSION_KEY_RENAMED": {"required": "true"}},
    {"AIOS_ORIGIN_ADMISSION_KEY": {"required": "false"}},
    {"AIOS_ORIGIN_ADMISSION_KEY": {}},
    {
        "AIOS_ORIGIN_ADMISSION_KEY": {"required": "true"},
        "UNRELATED": {"required": "true"},
    },
])
def test_reusable_ingress_rejects_missing_renamed_optional_or_extra_secret(
    secrets: object,
) -> None:
    workflow, _ = _workflow()
    if secrets is None:
        workflow["on"]["workflow_call"] = ""
    else:
        workflow["on"]["workflow_call"]["secrets"] = secrets
    with pytest.raises(AssertionError):
        _assert_required_origin_secret(workflow)


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


def test_origin_provenance_is_self_hosted_bounded_and_precedes_hosted_canonicalization() -> None:
    workflow, text = _workflow()
    jobs = workflow["jobs"]
    frame, admission, delivery = jobs["origin_request"], jobs["origin_provenance"], jobs["deliver"]
    assert frame["runs-on"] == delivery["runs-on"] == "ubuntu-latest"
    assert admission["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert admission["needs"] == "origin_request"
    assert admission["if"] == "needs.origin_request.outputs.required == 'true'"
    assert delivery["needs"] == ["origin_request", "origin_provenance"]
    assert frame["permissions"] == admission["permissions"] == {"contents": "read"}
    assert frame["timeout-minutes"] == admission["timeout-minutes"] == "5"
    framing = next(s for s in frame["steps"] if s.get("id") == "frame")
    assert "--mode frame" in framing["run"]
    gate = next(s for s in admission["steps"] if s.get("name", "").startswith("Admit exact attempt"))
    assert "--mode admit-origin" in gate["run"] and "--event" in gate["run"]
    assert "$LASTEXITCODE" in gate["run"]
    assert gate["env"]["AIOS_ORIGIN_ADMISSION_KEY"] == "${{ secrets.AIOS_ORIGIN_ADMISSION_KEY }}"
    assert "continue-on-error" not in gate
    # Admission never calls deliver/default ingress or an execution launcher.
    assert all("--mode admit-origin" in s["run"] for s in admission["steps"]
               if "aios_renew.github_issue_ingress" in s.get("run", ""))
    for forbidden in ("aios run", "aios repair", "aios remediate", "createWorkflowDispatch"):
        assert all(forbidden not in s.get("run", "") for s in admission["steps"])
    assert "github.event.issue.body" not in text
    # Local registry location comes only from the self-hosted machine environment.
    assert "AIOS_ORIGIN_REGISTRY" not in text


def test_origin_admission_artifact_and_delivery_are_bound_to_this_attempt_and_fail_closed() -> None:
    workflow, _ = _workflow()
    jobs = workflow["jobs"]
    upload = next(s for s in jobs["origin_provenance"]["steps"] if s.get("uses") == "actions/upload-artifact@v4")
    download = next(s for s in jobs["deliver"]["steps"] if s.get("uses") == "actions/download-artifact@v4")
    assert upload["with"]["name"] == download["with"]["name"] == "aios-origin-admission-v1-attempt-${{ github.run_attempt }}"
    assert upload["with"]["retention-days"] == "1" and upload["with"]["if-no-files-found"] == "error"
    assert download["if"] == "needs.origin_provenance.result == 'success'"
    assert not {"run-id", "repository", "github-token"} & set(download["with"])
    ingress = next(s for s in jobs["deliver"]["steps"] if s.get("id") == "ingress")
    assert '--origin-admission "$AIOS_ORIGIN_ADMISSION_PATH"' in ingress["run"]
    assert "github.run_attempt" in ingress["env"]["AIOS_ORIGIN_ADMISSION_PATH"]
    assert ingress["env"]["AIOS_ORIGIN_ADMISSION_KEY"] == "${{ secrets.AIOS_ORIGIN_ADMISSION_KEY }}"
    # A failed provenance job still reaches the existing FAIL receipt/attention
    # path; missing authenticated admission blocks before semantic ingress.
    assert jobs["deliver"]["if"].startswith("always() &&")


def test_deployment_key_is_consumed_only_by_origin_gate_and_hosted_delivery() -> None:
    workflow, text = _workflow()
    key = "AIOS_ORIGIN_ADMISSION_KEY"
    reference = "${{ secrets.AIOS_ORIGIN_ADMISSION_KEY }}"
    consumers = []
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            env = step.get("env", {})
            if key in env:
                assert env[key] == reference
                consumers.append((name, step["name"]))
            # The key stays in the process environment, away from commands,
            # receipts, GitHub outputs and uploaded artifact paths.
            assert key not in step.get("run", "")
            assert "secrets." not in step.get("run", "")
    assert consumers == [
        ("origin_provenance", "Admit exact attempt against machine-local H4C0 state"),
        ("deliver", "Deliver immutable Issue event once"),
    ]
    assert text.count(reference) == 2
    assert "toJSON(secrets)" not in text
    assert "toJSON(env)" not in text


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
        "AIOS_REPAIR_DISPATCH_OUTCOME": "${{ steps.repair_dispatch.outcome }}",
        "AIOS_REPAIR_DISPATCH_ID": "${{ steps.ingress.outputs.repair_dispatch_id }}",
        "AIOS_FAILED_RUN_ID": "${{ steps.ingress.outputs.failed_run_id }}",
        "AIOS_REPAIR_SHA": "${{ steps.ingress.outputs.repair_sha }}",
    }
    assert "if (fullySuccessful)" in text
    assert "(!publicationRequested || dispatchAccepted)" in text
    assert "(!repairRequested || repairAccepted)" in text
    assert (
        "always() && (steps.ingress.outcome != 'success' || (steps.ingress.outputs.publication_run_id != '' && steps.dispatch.outcome != 'success') || (steps.ingress.outputs.repair_sha != '' && steps.repair_dispatch.outcome != 'success'))"
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
    assert "repair_dispatch: ACCEPTED" in text
    assert "detail: GitHub accepted safe REPAIR dispatch;" in text
    assert "repair_dispatch: REJECTED" in text
    assert "reason: GitHub did not accept safe REPAIR dispatch." in text


def test_workflow_fails_closed_when_publication_dispatch_is_not_accepted() -> None:
    workflow, _ = _workflow()
    steps = workflow["jobs"]["deliver"]["steps"]
    fail_step = next(step for step in steps if step.get("name") == "Preserve failed delivery outcome")
    assert fail_step["name"] == "Preserve failed delivery outcome"
    assert "steps.dispatch.outcome != 'success'" in fail_step["if"]
    assert "steps.ingress.outputs.publication_run_id != ''" in fail_step["if"]
    assert "steps.repair_dispatch.outcome != 'success'" in fail_step["if"]
    assert "steps.ingress.outputs.repair_sha != ''" in fail_step["if"]


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
    assert text.count("createWorkflowDispatch") == 2
    assert script.count("createWorkflowDispatch") == 1
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


def test_workflow_dispatches_safe_repair_wakeup_once_with_bounded_canonical_selectors() -> None:
    workflow, _ = _workflow()
    steps = workflow["jobs"]["deliver"]["steps"]
    dispatch = next(
        (step for step in steps if step.get("id") == "repair_dispatch"),
        None,
    )
    assert dispatch is not None
    assert dispatch["uses"] == "actions/github-script@v7"
    assert (
        dispatch["if"]
        == "steps.ingress.outcome == 'success' && steps.ingress.outputs.repair_sha != ''"
    )
    assert dispatch["env"] == {
        "AIOS_REPAIR_DISPATCH_ID": "${{ steps.ingress.outputs.repair_dispatch_id }}",
        "AIOS_FAILED_RUN_ID": "${{ steps.ingress.outputs.failed_run_id }}",
        "AIOS_REPAIR_SHA": "${{ steps.ingress.outputs.repair_sha }}",
    }

    script = dispatch["with"]["script"]
    assert script.count("createWorkflowDispatch") == 1
    assert "workflow_id: 'aios-self-hosted-repair-wakeup.yml'" in script
    assert "ref: 'main'" in script
    assert "repair_dispatch_id: process.env.AIOS_REPAIR_DISPATCH_ID" in script
    assert "failed_run_id: process.env.AIOS_FAILED_RUN_ID" in script
    assert "repair_sha: process.env.AIOS_REPAIR_SHA" in script
    assert "executor: ''" in script
    assert "owner: context.repo.owner" in script
    assert "repo: context.repo.repo" in script

    for forbidden in (
        "github.event.issue.body",
        "GITHUB_EVENT_PATH",
        "publication_run_id",
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


def test_wake_pointer_export_preserves_source_lifecycle_and_attempt_binding() -> None:
    parsed, text = _workflow()
    steps = parsed["jobs"]["deliver"]["steps"]
    upload = next(step for step in steps if step.get("name") == "Export bounded wake source selectors")
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["if"] == "always() && steps.wake_pointer.outputs.ready == 'true'"
    assert upload["with"]["name"] == "aios-wake-source-v1-attempt-${{ github.run_attempt }}"
    assert upload["with"]["retention-days"] == "1"
    assert text.count("source_kind:") == 1
    assert "attention_family" not in text
    assert "[AIOS BRAIN WAKE]" not in text
