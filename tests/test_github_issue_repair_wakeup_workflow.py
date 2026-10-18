from __future__ import annotations

import re
import json
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
ORIGIN_FIELDS = {"origin_workflow_run_id", "origin_run_attempt", "origin_artifact_id"}


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


def _assert_observer_job_env_contexts(observer: dict) -> None:
    # YAML parsing accepts runner expressions even where Actions cannot evaluate them.
    for name, value in observer.get("env", {}).items():
        assert re.search(r"\$\{\{[^}]*\brunner\s*(?:\.|\[)", value) is None, (
            f"runner context is unavailable in observe-repair-source job-level env: {name}"
        )


def test_task372_observation_job_is_independent_read_only_and_attempt_bound():
    workflow, text = _workflow(TARGET)
    observer = workflow["jobs"]["observe-repair-source"]
    execution = workflow["jobs"]["execute-repair"]
    assert observer["runs-on"] == "ubuntu-latest"
    assert "needs" not in execution and "needs" not in observer
    assert workflow["permissions"] == {"contents": "read"}
    assert observer["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    assert observer["env"] == {
        "AIOS_REPAIR_DISPATCH_ID": "${{ inputs.repair_dispatch_id }}",
        "AIOS_FAILED_RUN_ID": "${{ inputs.failed_run_id }}",
        "AIOS_REPAIR_SHA": "${{ inputs.repair_sha }}",
    }
    uploads = [s["with"] for s in observer["steps"] if s.get("uses") == "actions/upload-artifact@v4"]
    assert uploads == [
        {
            "name": "aios-repair-correlation-v1-delivery-attempt-${{ github.run_attempt }}",
            "path": "${{ env.AIOS_REPAIR_SOURCE_PATH }}",
            "if-no-files-found": "error",
            "retention-days": "90",
        },
        {
            "name": "aios-repair-delivery-v1-${{ inputs.repair_dispatch_id }}",
            "path": "${{ env.AIOS_REPAIR_SOURCE_PATH }}",
            "overwrite": "true",
            "if-no-files-found": "error",
            "retention-days": "90",
        },
    ]
    assert text.count("$controlSource repair-wakeup ") == 1
    for event in ("workflow_dispatch", "workflow_call"):
        assert all(workflow["on"][event]["inputs"][field]["required"] == "false" for field in ORIGIN_FIELDS)


def test_task375_observation_and_uploads_resolve_the_same_step_level_source_path() -> None:
    workflow, _ = _workflow(TARGET)
    observer = workflow["jobs"]["observe-repair-source"]
    _assert_observer_job_env_contexts(observer)
    assert "AIOS_REPAIR_SOURCE_PATH" not in observer["env"]
    steps = observer["steps"]
    observation = next(step for step in steps if step.get("name") == "Observe exact REPAIR workflow event")
    assert observation["run"] == (
        'python -c "from aios_renew.github_issue_repair_wakeup import source_delivery_main; '
        'raise SystemExit(source_delivery_main())"'
    )
    uploads = [step for step in steps if step.get("uses") == "actions/upload-artifact@v4"]
    assert len(uploads) == 2
    for step in [observation, *uploads]:
        assert step["env"] == {
            "AIOS_REPAIR_SOURCE_PATH": "${{ runner.temp }}/aios-repair-source/source.json",
        }
        assert "if" not in step and "continue-on-error" not in step
    for upload in uploads:
        assert steps.index(observation) < steps.index(upload)
        assert upload["with"]["path"] == "${{ env.AIOS_REPAIR_SOURCE_PATH }}"


def test_task375_regression_rejects_runner_temp_in_observer_job_env() -> None:
    workflow, _ = _workflow(TARGET)
    observer = workflow["jobs"]["observe-repair-source"]
    observer["env"]["AIOS_REPAIR_SOURCE_PATH"] = (
        "${{ runner.temp }}/aios-repair-source/source.json"
    )
    with pytest.raises(AssertionError, match="runner context is unavailable.*job-level env"):
        _assert_observer_job_env_contexts(observer)


def test_task375_repair_workflow_reuse_remains_in_the_canonical_issue_carrier_graph() -> None:
    issue, _ = _workflow(ROOT / ".github/workflows/aios-issue-carrier.yml")
    assert issue["on"] == {"issues": {"types": ["opened"]}}
    for job_id, filename in (
        ("ingress", "aios-brain-ingress.yml"),
        ("repair-wakeup", "aios-brain-repair-wakeup.yml"),
    ):
        reuse = issue["jobs"][job_id]
        assert reuse["needs"] == "route"
        assert reuse["if"] == f"needs.route.outputs.carrier == '{filename}'"
        assert reuse["uses"] == f"./.github/workflows/{filename}"
        carrier, _ = _workflow(ROOT / reuse["uses"])
        assert set(carrier["on"]) == {"workflow_call"}
    repair, _ = _workflow(CARRIER)
    dispatch = repair["jobs"]["dispatch"]
    assert dispatch["needs"] == "admit"
    assert dispatch["uses"] == "./.github/workflows/aios-self-hosted-repair-wakeup.yml"
    assert ROOT / dispatch["uses"] == TARGET
    assert sum("uses" in job for job in repair["jobs"].values()) == 1


def test_task372_manual_receipt_retains_exact_source_pointer_without_new_dispatch():
    workflow, text = _workflow(CARRIER)
    assert "AIOS_REPAIR_WORKFLOW_POINTER=" in text
    assert "aios-repair-correlation-v1-wakeup-attempt-${{ github.run_attempt }}" in text
    assert workflow["jobs"]["dispatch"]["uses"] == "./.github/workflows/aios-self-hosted-repair-wakeup.yml"
    assert "createWorkflowDispatch" not in text


def test_reusable_issue_admission_is_exact_and_github_hosted() -> None:
    workflow, text = _workflow(CARRIER)
    assert workflow["on"] == {"workflow_call": ""}
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
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == expected | ORIGIN_FIELDS
    assert set(workflow["on"]["workflow_call"]["inputs"]) == expected | ORIGIN_FIELDS
    job = workflow["jobs"]["execute-repair"]
    assert job["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert workflow["permissions"] == {"contents": "read"}
    assert all("actions/checkout" not in step.get("uses", "") for step in job["steps"])
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
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == expected_inputs | ORIGIN_FIELDS
    assert set(workflow["on"]["workflow_call"]["inputs"]) == expected_inputs | ORIGIN_FIELDS

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
    assert "status: ${accepted ? 'SELF_HOST_COMPLETED' : admitted ? 'OPERATIONAL_FAILED' : 'REJECTED'}" in text
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
            "name": "aios-operational-receipt-v2-repair-attempt-${{ github.run_attempt }}",
            "path": "${{ env.AIOS_OPERATIONAL_RECEIPT_PATH }}",
            "if-no-files-found": "error",
            "retention-days": "90",
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


def test_admission_step_binds_explicit_trusted_profile_policy() -> None:
    _, text = _workflow(CARRIER)
    assert "--policy .ai/brain-repair-wakeup-carriers.yaml" in text
    assert (ROOT / ".ai" / "executor-profiles.yaml").is_file()

def test_hosted_policy_is_from_exact_checked_out_invocation() -> None:
    workflow, text = _workflow(CARRIER)
    steps = workflow["jobs"]["admit"]["steps"]
    checkout = steps[0]
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert "python -m pip install --disable-pip-version-check ." in text
    admission = next(step for step in steps if step.get("id") == "admission")
    assert admission["env"]["AIOS_PROFILE_POLICY"] == (
        "${{ github.workspace }}/.ai/executor-profiles.yaml"
    )
    assert '--profile-policy "$AIOS_PROFILE_POLICY"' in admission["run"]
    assert "--profile-policy .ai/executor-profiles.yaml" not in text


def test_wake_pointer_export_preserves_source_lifecycle_and_attempt_binding() -> None:
    parsed, text = _workflow(CARRIER)
    steps = parsed["jobs"]["receipt"]["steps"]
    upload = next(step for step in steps if step.get("name") == "Export bounded wake source selectors")
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["if"] == "always() && steps.wake_pointer.outputs.ready == 'true'"
    assert upload["with"]["name"] == "aios-wake-source-v1-attempt-${{ github.run_attempt }}"
    assert upload["with"]["retention-days"] == "1"
    assert text.count("source_kind:") == 1
    assert "attention_family" not in text
    assert "[AIOS BRAIN WAKE]" not in text


def test_delegated_issue_gate_rechecks_proof_before_one_canonical_command() -> None:
    _, text = _workflow(TARGET)
    proof = text.index('from aios_renew.github_issue_repair_wakeup import delivery_main')
    command = text.index('$controlSource repair-wakeup ')
    assert proof < command
    assert "$env:AIOS_DELIVERY_EVENT -ceq 'issues'" in text
    assert "$env:AIOS_EXECUTOR -ceq 'AUTO'" in text
    assert "ls-remote --heads" in text and "refs/heads/main" in text
    assert "-cnotin @('codex', 'antigravity', 'AUTO')" in text
    assert "'--executor', $env:AIOS_EXECUTOR" in text
    assert "if (-not [string]::IsNullOrEmpty($option[1]))" in text


def test_carrier_auto_receipt_does_not_claim_resolved_coding_executor() -> None:
    _, text = _workflow(CARRIER)
    assert "requested_executor:" in text
    assert "['codex', 'antigravity'].includes(process.env.AIOS_EXECUTOR)" in text
    assert "name: Expose bounded admission reason" in text
    assert "admitted ? 'OPERATIONAL_FAILED' : 'REJECTED'" in text


@pytest.mark.parametrize("mode", ["codex", "AUTO"])
def test_ingress_human_action_preserves_authenticated_manual_auto_profile_and_journal(tmp_path, monkeypatch, mode):
    from aios_renew import github_issue_ingress, github_issue_repair_wakeup as repair_carrier
    from tests.test_github_issue_ingress import repair_ingress_fixture
    from tests.test_github_issue_repair_wakeup import _body, _event, _delivery_environment

    repo, event, policy = repair_ingress_fixture(tmp_path, action="CODE_FIX", coding_profile=True)
    delivery = github_issue_ingress.deliver_event(event, policy, repo=repo)
    assert delivery.repair_wakeup_route == "HUMAN_WAKEUP_REQUIRED"
    human = _event(_body(
        version=3, executor=mode, repair_dispatch_id=delivery.repair_dispatch_id,
        failed_run_id=delivery.repair_failed_run_id, repair_sha=delivery.repair_sha,
        model="human/explicit-model" if mode == "codex" else None,
        reasoning_effort="high" if mode == "codex" else None,
    ))
    env = _delivery_environment(tmp_path, human)
    request = repair_carrier.authenticated_delivery(policy_path=POLICY, environ=env)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    expected = ("codex", "human/explicit-model", "high", "EXPLICIT", "EXPLICIT") if mode == "codex" else (
        "codex", "source/authenticated-model", "low", "REPOSITORY_DEFAULT", "EXPLICIT",
    )
    if mode == "codex":
        monkeypatch.setattr(operator, "_resolve_auto_repair_profile", lambda *a, **k: pytest.fail("manual choice reached AUTO"))
        assert (request.executor, request.model, request.reasoning_effort, request.model_source, request.effort_source) == expected
    else:
        assert request.executor == "AUTO" and request.model is request.model_source is None
    calls = []
    def stop_before_run(*args, **kwargs):
        calls.append(kwargs)
        raise operator.OperatorError("fixture stops before RUN and verification")
    monkeypatch.setattr(operator, "run_repair", stop_before_run)
    selectors = (delivery.repair_dispatch_id, delivery.repair_failed_run_id, delivery.repair_sha)
    profile = {key: getattr(request, key) for key in ("model", "reasoning_effort", "model_source", "effort_source")}
    outcome = operator.run_repair_wakeup(*selectors, executor=mode, repo=repo, **profile)
    assert outcome.run_id is None and len(calls) == 1
    fields = ("executor", "model", "reasoning_effort", "model_source", "effort_source")
    assert tuple(calls[0][key] for key in fields) == expected
    state = operator.runtime_state_root(repo)
    record_path = next((state / "repair-dispatches").glob("*.json"))
    before = record_path.read_bytes()
    record = json.loads(before)
    assert record["repair_sha"] == delivery.repair_sha
    assert record["failed_run_id"] == delivery.repair_failed_run_id
    if mode == "AUTO":
        assert record["auto_authorization"]["actor"] == "trung-via"
        assert record["auto_authorization"]["source_run_id"] == delivery.repair_failed_run_id
    monkeypatch.setattr(operator, "preflight_repair", lambda *a, **k: pytest.fail("journal replay repeated admission"))
    monkeypatch.setattr(operator, "_resolve_auto_repair_profile", lambda *a, **k: pytest.fail("journal replay changed profile"))
    replay = operator.run_repair_wakeup(*selectors, executor=mode, repo=repo, **profile)
    assert replay.replayed and len(calls) == 1 and record_path.read_bytes() == before
    conflicting_body = yaml.safe_load(human["issue"]["body"])
    conflicting_body["repair_sha"] = "f" * 40
    conflicting = _event(yaml.safe_dump(conflicting_body))
    conflicting["issue"]["number"] = 112
    for key, value in _delivery_environment(tmp_path, conflicting).items():
        monkeypatch.setenv(key, value)
    with pytest.raises(operator.OperatorError, match="collision"):
        operator.run_repair_wakeup(selectors[0], selectors[1], "f" * 40, executor=mode, repo=repo, **profile)
    assert record_path.read_bytes() == before and not operator._runtime_paths_readonly(repo).runs.exists()


@pytest.mark.parametrize("executor", ["codex", "AUTO"])
def test_bare_bot_dispatch_cannot_use_ingress_subject_to_mint_human_coding_selection(tmp_path, executor):
    from aios_renew import github_issue_repair_wakeup as repair_carrier
    from tests.test_github_issue_repair_wakeup import _body, _event, _delivery_environment, _write

    event = _event(_body(version=3, executor=executor))
    env = _delivery_environment(tmp_path, event, actor="github-actions[bot]", event_name="workflow_dispatch")
    forged = {"sender": {"login": "trung-via", "type": "User"},
              "repository": event["repository"], "ref": "refs/heads/main", "inputs": {
                  key: env.get("AIOS_" + key.upper(), "") for key in (
                      "repair_dispatch_id", "failed_run_id", "repair_sha", "executor", "model",
                      "reasoning_effort", "model_source", "effort_source",
                  )}}
    _write(tmp_path, "delivery.json", forged)
    with pytest.raises(repair_carrier.GitHubIssueRepairWakeupError, match="Human event proof"):
        repair_carrier.authenticated_delivery(policy_path=POLICY, environ=env)
