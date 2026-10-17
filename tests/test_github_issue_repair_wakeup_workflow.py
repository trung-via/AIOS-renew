from __future__ import annotations

import re
import shlex
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
    module = "aios_renew.github_issue_repair_wakeup"
    invocations = [
        (job_id, step)
        for job_id, job in workflow["jobs"].items()
        for step in job.get("steps", [])
        if module in step.get("run", "")
    ]
    admissions = [(job_id, step) for job_id, step in invocations
                  if "--mode workflow-receipt" not in step["run"]]
    assert len(admissions) == 1
    assert admissions[0][0] == "admit" and admissions[0][1]["id"] == "admission"
    observations = [(job_id, shlex.split(step["run"])) for job_id, step in invocations
                    if "--mode workflow-receipt" in step["run"]]
    assert [(job_id, args[args.index("--phase") + 1]) for job_id, args in observations] == [
        ("admit", "waiting"), ("receipt", "completed"),
    ]
    assert text.count(module) == len(invocations) == 3
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
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == expected
    assert set(workflow["on"]["workflow_call"]["inputs"]) == expected
    job = workflow["jobs"]["execute-repair"]
    assert job["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "actions/checkout" not in text
    assert "AIOS_REPO_ROOT: ${{ vars.AIOS_REPO_ROOT }}" in text
    assert text.count("$controlSource repair-wakeup ") == 1
    command_text = "\n".join(step.get("run", "") for step in job["steps"])
    for forbidden in ("aios continue", "aios repair ", "codex ", "antigravity "):
        assert forbidden not in command_text.lower()
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
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == expected_inputs
    assert set(workflow["on"]["workflow_call"]["inputs"]) == expected_inputs

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
    initial = next(s for s in workflow['jobs']['admit']['steps'] if s.get('id') == 'repair_observation')
    final = next(s for s in workflow['jobs']['receipt']['steps'] if s.get('id') == 'repair_observation')
    assert '--phase waiting' in initial['run'] and '--phase completed' in final['run']
    assert initial['continue-on-error'] == 'true'  # reporting cannot revoke Human authority
    assert final['env']['AIOS_DOWNSTREAM_RESULT'] == '${{ needs.dispatch.result }}'
    assert '--downstream-result "$AIOS_DOWNSTREAM_RESULT"' in final['run']
    rejection = next(s for s in workflow['jobs']['receipt']['steps'] if s.get('name') == 'Preserve rejected carrier outcome')
    assert rejection['if'] == "needs.admit.result != 'success' || needs.dispatch.result != 'success'"


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
    for job in ('admit', 'receipt'):
        steps = parsed['jobs'][job]['steps']
        upload = next(s for s in steps if s.get('name') == 'Export bounded wake source selectors')
        assert upload['with']['name'] == 'aios-wake-source-v1-attempt-${{ github.run_attempt }}'
        assert upload['with']['retention-days'] == '90'
    fallback = next(s for s in parsed['jobs']['receipt']['steps'] if s.get('id') == 'wake_pointer')
    assert "needs.admit.result != 'success'" in fallback['if']
    assert 'attention_family' not in text and '[AIOS BRAIN WAKE]' not in text


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
    from aios_renew import github_issue_repair_wakeup as carrier
    import inspect
    receipt_code = inspect.getsource(carrier.publish_workflow_receipt)
    assert 'repair_run_outcome: not_asserted_by_carrier' in receipt_code
    assert 'verification: not_asserted_by_carrier' in receipt_code
    assert 'semantic_review: not_asserted_by_carrier' in receipt_code
    assert 'publication: not_asserted_by_carrier' in receipt_code
    assert 'name: Expose bounded admission reason' in text


# The real incident is an operational failure, not a failed canonical RUN.
INCIDENT_168 = dict(
    issue_number=2075, workflow_run_id=39026366429, run_attempt=1,
    job_id=117378039859, event="workflow_dispatch", actor="github-actions[bot]",
    control_sha="491903e00630abb9e39e91310358fe4464bf487a",
    repair_sha="800e7dabd1e91d07407d9c745201ce322404beb7", action="CODE_FIX", executor="",
    step="Deliver one immutable REPAIR intent", error="coding REPAIR requires an explicit Executor",
    published_task365_source="9f70b6b1dabad4234794db74c4d7c1f43f9a4a60", review="REVIEW-365-001",
)


class WorkflowGitHubFixture:
    """HTTP-shaped GitHub boundary; production writer/reader/ZIP parsers stay real.

    New controls are exercised prospectively with the #168 topology/identifiers;
    this does not assert that historical #168 published the new receipt fields.
    """

    def __init__(self, event, control_sha):
        import copy
        from aios_renew.brain_attention import RepairWorkflowSources
        self.api = RepairWorkflowSources()
        self.api.request = self.request
        self.api.write_comment = self.write_comment
        self.api.close_issue = lambda number: self.issue.update(state="closed")
        self.issue = copy.deepcopy(event["issue"])
        self.issue["url"] = f"https://api.github.com/repos/trung-via/AIOS-renew/issues/{self.issue['number']}"
        self.comments = []
        self.artifacts = {}
        self.archives = {}
        self.logs_requested = []
        self.run = dict(id=INCIDENT_168["workflow_run_id"], run_attempt=1, status="in_progress", conclusion=None,
            path=".github/workflows/aios-issue-carrier.yml", event="issues", head_branch="main",
            head_repository={"full_name": "trung-via/AIOS-renew"}, head_sha=control_sha,
            actor={"login": "trung-via", "type": "User"})
        self.jobs = []
        self.diagnostics_denied = False

    def request(self, path, binary=False):
        from aios_renew.brain_attention import AttentionError
        if path == f"/issues/{self.issue['number']}":
            return self.issue
        if path == f"/issues/{self.issue['number']}/comments?per_page=100":
            return self.comments
        if path == f"/actions/runs/{self.run['id']}/attempts/{self.run['run_attempt']}":
            return self.run
        if path.endswith("/jobs?per_page=100"):
            return dict(total_count=len(self.jobs), jobs=self.jobs)
        if path.endswith("/artifacts?per_page=100"):
            return dict(total_count=len(self.artifacts), artifacts=list(self.artifacts.values()))
        if path == "/actions/workflows/aios-self-hosted-repair-wakeup.yml/runs?event=workflow_dispatch&per_page=100":
            return dict(total_count=1, workflow_runs=[self.run])
        if path.startswith("/actions/artifacts/"):
            artifact_id = int(path.split("/")[3])
            if artifact_id == 1 and path.endswith("/zip") and self.diagnostics_denied:
                raise AttentionError("SOURCE_UNAVAILABLE")
            return self.archives[artifact_id] if path.endswith("/zip") else self.artifacts[artifact_id]
        if path == f"/actions/jobs/{INCIDENT_168['job_id']}/logs":
            self.logs_requested.append(path)
            return ("AIOS ERROR: " + INCIDENT_168["error"]).encode()
        raise AssertionError(f"unexpected GitHub read: {path}")

    def write_comment(self, number, body, comment_id=None):
        assert number == self.issue["number"] and len(body) <= 3500
        if comment_id is not None:
            comment = next(c for c in self.comments if c["id"] == comment_id)
            comment["body"] = body
        else:
            comment = dict(id=5000 + len(self.comments), body=body,
                           user={"login": "github-actions[bot]", "type": "Bot"})
            self.comments.append(comment)
        return comment

    def artifact(self, artifact_id, name, filename, payload):
        import io
        import json
        import zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as zipped:
            zipped.writestr(filename, json.dumps(payload))
        self.archives[artifact_id] = stream.getvalue()
        self.artifacts[artifact_id] = dict(id=artifact_id, name=name, expired=False,
            size_in_bytes=len(stream.getvalue()), workflow_run={"id": self.run["id"]})

    def pointer(self, comment_id):
        self.artifact(2, "aios-wake-source-v1-attempt-1", "source.json", dict(version=1,
            source_kind="comment", issue_id=self.issue["id"], issue_number=self.issue["number"], comment_id=comment_id))

    def outcome(self, receipt, *, success):
        self.artifact(1, "aios-operational-receipt-v2-repair-attempt-1",
                      "aios-repair-operational-receipt-v2.json", receipt)
        result = "success" if success else "failure"
        self.jobs = [dict(id=INCIDENT_168["job_id"], run_id=self.run["id"], run_attempt=1,
            name="repair_dispatch / execute-repair", status="completed", conclusion=result,
            steps=[dict(number=3, name=INCIDENT_168["step"], status="completed", conclusion=result)])]


def authoring_workflow_fixture(tmp_path, monkeypatch, *, action="CODE_FIX", reusable=False):
    """Real authoring, canonical failure transport and Runtime read-only preflight."""
    import json
    from dataclasses import asdict, replace
    from tests import test_authoring_ingress as author
    from aios_renew import authoring_ingress, github_issue_ingress
    from aios_renew.execution_profile import default_execution_profile

    original_setup = author.setup_test_repo
    def setup(*args, **kwargs):
        repo, remote, _ = original_setup(*args, **kwargs)
        (repo / ".ai/executor-profiles.yaml").write_bytes((ROOT / ".ai/executor-profiles.yaml").read_bytes())
        author.git(repo, "add", ".ai/executor-profiles.yaml")
        author.git(repo, "commit", "--quiet", "-m", "fixture Human profile policy")
        author.git(repo, "push", "--quiet", "origin", "main")
        return repo, remote, author.git(repo, "rev-parse", "HEAD")
    with monkeypatch.context() as fixture_builder:
        fixture_builder.setattr(author, "setup_test_repo", setup)
        repo, remote, main, failure_sha, envelope = author.completion_gate_supersession_fixture(
            tmp_path, phase="VERIFICATION" if action in {"CODE_FIX", "NO_CHANGE"} else "COMPLETION_GATE",
            task_revision=1)
    failed = envelope.identity["failed_run_id"]
    head = envelope.payload["failed_head_sha"]
    source_profile = default_execution_profile("codex", failed, repo)
    additions = {".ai/transport/execution-profile.json": source_profile.as_dict()}
    if reusable:
        additions[".ai/transport/pre-verification-candidate.json"] = dict(
            kind="PRE_VERIFICATION_CANDIDATE", run_id=failed, task=envelope.payload["task"], subject_sha=head,
            package=dict(result=dict(head_sha=head, changed_files=["src/sample.py"], unresolved=[],
                claims=[dict(id="C1", satisfies=["AC1"], claim="src/sample.py contains the committed candidate.", evidence=[])]), evidence=[]))
    # Fixture authority creates immutable transport objects before AUTHOR_REPAIR.
    # Production preflight reads these through real Git, never a mocked READY bool.
    staged = failure_sha
    for path, document in additions.items():
        tree = authoring_ingress._tree_with_metadata(repo, staged, path, json.dumps(document).encode())
        staged = authoring_ingress._commit_tree(repo, tree, [head], "bounded failure fixture material")
    author.git(remote, "fetch", "--quiet", str(repo), staged)
    author.git(remote, "update-ref", f"refs/heads/aios/failure-artifacts/{failed}", staged, failure_sha)
    envelope = replace(envelope, payload={**envelope.payload, "action": action,
        "modification_scope": [] if action in {"NO_CHANGE", "FINALIZE_CANDIDATE"} else ["src/sample.py"]})
    envelope_body = asdict(envelope)
    # AUTHOR_REPAIR omits the AUTHOR_TASK-only proof instead of sending null.
    assert envelope_body.pop("origin_authoring_proof") is None
    event = dict(action="opened", repository={"full_name": "trung-via/AIOS-renew"},
        sender={"login": "trung-via", "type": "User"}, issue=dict(id=2075000, number=2075,
            title="[AIOS BRAIN INGRESS]", user={"login": "trung-via", "type": "User"}, body=json.dumps(envelope_body)))
    event_path = tmp_path / "workflow-event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    delivery = github_issue_ingress.deliver_event(event_path, ROOT / ".ai/brain-ingress-carriers.yaml", repo=repo)
    env = dict(GITHUB_EVENT_PATH=str(event_path), GITHUB_REPOSITORY="trung-via/AIOS-renew", GITHUB_EVENT_NAME="issues",
        GITHUB_REF="refs/heads/main", GITHUB_SHA=main, GITHUB_RUN_ID=str(INCIDENT_168["workflow_run_id"]), GITHUB_RUN_ATTEMPT="1",
        AIOS_CONTROL_SHA=main, AIOS_CONTROL_REF="refs/heads/main", AIOS_DELIVERY_ACTOR="github-actions[bot]",
        AIOS_DELIVERY_EVENT="issues", AIOS_REPO_ROOT=str(repo), AIOS_REPAIR_DISPATCH_ID=delivery.repair_dispatch_id,
        AIOS_FAILED_RUN_ID=failed, AIOS_REPAIR_SHA=delivery.repair_sha,
        AIOS_EXECUTOR="", AIOS_MODEL="", AIOS_REASONING_EFFORT="", AIOS_MODEL_SOURCE="", AIOS_EFFORT_SOURCE="")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return repo, event, env, delivery, source_profile


def test_real_authoring_coding_repair_posts_human_action_without_dispatch(tmp_path, monkeypatch):
    from aios_renew.github_issue_repair_wakeup import publish_workflow_receipt
    from aios_renew import github_issue_ingress
    repo, event, env, delivery, _ = authoring_workflow_fixture(tmp_path, monkeypatch)
    assert delivery.repair_route == "HUMAN_WAKEUP_REQUIRED"
    assert delivery.repair_route_reason == "EXECUTOR_REQUIRED"
    github = WorkflowGitHubFixture(event, env["GITHUB_SHA"])
    args = dict(phase="waiting", route=delivery.repair_route, route_reason=delivery.repair_route_reason,
                sources=github.api, environ=env)
    output = publish_workflow_receipt(**args)
    publish_workflow_receipt(**args)
    github.pointer(output["comment_id"])
    result = github.api.from_issue(2075)[0]
    assert result["workflow"]["workflow_run_id"] == INCIDENT_168["workflow_run_id"]
    assert result["state"] == "HUMAN_WAKEUP_REQUIRED" and not result["jobs"]
    assert len(github.comments) == 1
    body = github.comments[0]["body"]
    assert "status: HUMAN_WAKEUP_REQUIRED" in body and "dispatch_attempted: false" in body
    assert "Human-authenticated manual Executor or exact-subject AUTO" in body
    assert env["AIOS_REPAIR_SHA"] in body and env["AIOS_FAILED_RUN_ID"] in body
    # Identical authoring delivery cannot schedule a second execution.
    replay = github_issue_ingress.deliver_event(env["GITHUB_EVENT_PATH"], ROOT / ".ai/brain-ingress-carriers.yaml", repo=repo)
    assert replay.github_outputs() == ""


@pytest.mark.parametrize("action,reusable,expected", [
    ("CONTINUE_IMPLEMENTATION", False, "HUMAN_WAKEUP_REQUIRED"),
    ("FINALIZE_CANDIDATE", False, "HUMAN_WAKEUP_REQUIRED"),
    ("FINALIZE_CANDIDATE", True, "EXECUTORLESS_READY"),
    ("NO_CHANGE", True, "EXECUTORLESS_READY"),
])
def test_real_authoring_runtime_preflight_selects_only_verified_executorless_route(
    tmp_path, monkeypatch, action, reusable, expected,
):
    import json
    from aios_renew.github_issue_ingress import repair_wakeup_route
    from aios_renew.github_issue_repair_wakeup import delivery_main, publish_workflow_receipt
    from aios_renew.operational_receipt import finalize_repair_workflow_receipt
    from tests.test_authoring_ingress import git
    repo, event, env, delivery, _ = authoring_workflow_fixture(tmp_path, monkeypatch, action=action, reusable=reusable)
    assert delivery.repair_route == expected
    if expected != "EXECUTORLESS_READY":
        return
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert delivery_main() == 0  # real self-host ingress event authentication/preflight
    github = WorkflowGitHubFixture(event, env["GITHUB_SHA"])
    output = publish_workflow_receipt(phase="waiting", route=expected, route_reason="READY", sources=github.api, environ=env)
    github.pointer(output["comment_id"])
    path = tmp_path / "operational.json"
    monkeypatch.setenv("AIOS_OPERATIONAL_RECEIPT_PATH", str(path))
    native_calls = []
    def no_native(*args, **kwargs):
        native_calls.append(args)
        pytest.fail("executorless REPAIR invoked native Executor")
    assert operator.main(["repair-wakeup", env["AIOS_REPAIR_DISPATCH_ID"], env["AIOS_FAILED_RUN_ID"],
                          env["AIOS_REPAIR_SHA"], "--repo", str(repo)], native_runner=no_native) == 0
    receipt = finalize_repair_workflow_receipt(path, "success", environ=env)
    github.outcome(receipt, success=True)
    publish_workflow_receipt(phase="completed", route=expected, route_reason="READY", downstream_result="success", sources=github.api, environ=env)
    github.run.update(status="completed", conclusion="success")
    observed = github.api.from_issue(2075)[0]
    assert observed["state"] == "SELF_HOST_COMPLETED"
    assert github.issue["state"] == "closed"
    assert observed["operational_receipt"]["boundary"] == "TERMINAL_POINTER"
    assert observed["operational_receipt"]["run_created"] is True
    assert len(github.comments) == 1 and not native_calls
    # After a terminal, fresh canonical state suppresses another automatic route.
    assert repair_wakeup_route(repo, env["AIOS_FAILED_RUN_ID"], env["AIOS_REPAIR_SHA"])[0] == "HUMAN_WAKEUP_REQUIRED"
    journals = list((repo / ".git/aios/repair-dispatches").glob("*.json"))
    before = journals[0].read_bytes()
    assert operator.main(["repair-wakeup", env["AIOS_REPAIR_DISPATCH_ID"], env["AIOS_FAILED_RUN_ID"],
                          env["AIOS_REPAIR_SHA"], "--repo", str(repo)], native_runner=no_native) == 0
    assert journals[0].read_bytes() == before
    assert git(repo, "status", "--porcelain") == ""


def test_incident168_pre_run_rejection_survives_finally_and_is_discoverable(tmp_path, monkeypatch, capsys):
    import json
    from aios_renew.github_issue_repair_wakeup import publish_workflow_receipt
    from aios_renew.operational_receipt import finalize_repair_workflow_receipt
    from aios_renew import brain_attention
    repo, event, env, delivery, _ = authoring_workflow_fixture(tmp_path, monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "operational.json"
    monkeypatch.setenv("AIOS_OPERATIONAL_RECEIPT_PATH", str(path))
    github = WorkflowGitHubFixture(event, env["GITHUB_SHA"])
    # Model the formerly unconditional caller, not the new safe ingress gate.
    output = publish_workflow_receipt(phase="waiting", route="EXECUTORLESS_READY", route_reason="READY", sources=github.api, environ=env)
    github.pointer(output["comment_id"])
    assert operator.main(["repair-wakeup", env["AIOS_REPAIR_DISPATCH_ID"], env["AIOS_FAILED_RUN_ID"],
                          env["AIOS_REPAIR_SHA"], "--repo", str(repo)]) == 1
    assert INCIDENT_168["error"] in capsys.readouterr().err
    receipt = finalize_repair_workflow_receipt(path, "failure", environ=env)
    assert receipt["cause"]["reason_code"] == "EXECUTOR_REQUIRED"
    assert receipt["boundary"] == "ADMISSION_REJECTED" and receipt["run_created"] is False
    assert "run_id" not in receipt and "terminal_pointer" not in receipt
    github.outcome(receipt, success=False)
    publish_workflow_receipt(phase="completed", route="EXECUTORLESS_READY", route_reason="READY", downstream_result="failure", sources=github.api, environ=env)
    github.run.update(status="completed", conclusion="failure")
    observed = github.api.from_issue(2075)[0]
    assert observed["state"] == "OPERATIONAL_FAILED"
    assert observed["diagnostic"]["reason_code"] == "EXECUTOR_REQUIRED"
    assert observed["jobs"][0]["id"] == INCIDENT_168["job_id"]
    assert observed["jobs"][0]["steps"][0]["name"] == INCIDENT_168["step"]
    assert github.api.job_logs(observed, INCIDENT_168["job_id"]).endswith(INCIDENT_168["error"].encode())
    assert len(github.comments) == 1 and "AIOS ERROR" not in github.comments[0]["body"]
    source = brain_attention.source_document([brain_attention.operational_observation(receipt)])
    events = brain_attention.project_source(source, dict(workflow_run_id=github.run["id"], run_attempt=1, artifact_id=1, source_digest=brain_attention.digest(source)))
    assert len(events) == 1 and events[0].family == brain_attention.DISPATCH


@pytest.mark.parametrize("selection", ["codex", "AUTO"])
def test_real_human_manual_and_auto_keep_profile_through_runtime_and_origin_observation(tmp_path, monkeypatch, selection):
    import json
    import subprocess
    from aios_renew import github_issue_repair_wakeup as carrier
    from aios_renew.operational_receipt import finalize_repair_workflow_receipt
    from tests.test_authoring_ingress import git
    repo, _, env, delivery, inherited = authoring_workflow_fixture(tmp_path, monkeypatch)
    body = dict(format="AIOS_REPAIR_WAKEUP_REQUEST", version=3 if selection == "AUTO" else 2,
        repair_dispatch_id="human-task370-fixture", failed_run_id=env["AIOS_FAILED_RUN_ID"],
        repair_sha=env["AIOS_REPAIR_SHA"], executor=selection,
        model=None if selection == "AUTO" else "provider/explicit-codex",
        reasoning_effort=None if selection == "AUTO" else "low")
    event = dict(action="opened", repository={"full_name": "trung-via/AIOS-renew"},
        sender={"login": "trung-via", "type": "User"}, issue=dict(id=2076000, number=2076,
            title="[AIOS REPAIR WAKEUP]", user={"login": "trung-via", "type": "User"}, body=json.dumps(body)))
    Path(env["GITHUB_EVENT_PATH"]).write_text(json.dumps(event), encoding="utf-8")
    request = carrier.admit_event(env["GITHUB_EVENT_PATH"], carrier.load_policy(POLICY),
                                 profile_policy=repo / ".ai/executor-profiles.yaml")
    env.update({"AIOS_" + name.upper(): str(value) for name, value in
                (line.split("=", 1) for line in request.github_outputs().splitlines())})
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert carrier.delivery_main() == 0
    github = WorkflowGitHubFixture(event, env["GITHUB_SHA"])
    output = carrier.publish_workflow_receipt(phase="waiting", route="HUMAN_AUTHENTICATED", route_reason="READY", sources=github.api, environ=env)
    github.pointer(output["comment_id"])
    native_calls = []
    def native(command, **kwargs):
        # Only the native process boundary is a fixture. Authoring, Runtime,
        # journal lock/binding, verification and terminal transport remain real.
        execution = json.loads(kwargs["input"].decode().split("REPAIR_INPUT:\n", 1)[1])
        subject = Path(execution["run"]["workspace"])
        native_calls.append(command)
        (subject / "src/sample.py").write_text("# corrected candidate\n", encoding="utf-8")
        git(subject, "add", "src/sample.py")
        git(subject, "commit", "--quiet", "-m", "bounded fixture correction")
        head = git(subject, "rev-parse", "HEAD")
        package = dict(result=dict(head_sha=head, changed_files=["src/sample.py"], unresolved=[],
            claims=[dict(id="C1", satisfies=["AC1"], claim="src/sample.py contains the corrected candidate.", evidence=[])]), evidence=[])
        return subprocess.CompletedProcess(command, 0, json.dumps(package), "")
    path = tmp_path / "human-operational.json"
    monkeypatch.setenv("AIOS_OPERATIONAL_RECEIPT_PATH", str(path))
    arguments = ["repair-wakeup", request.repair_dispatch_id, request.failed_run_id, request.repair_sha,
                 "--executor", selection, "--repo", str(repo)]
    if selection != "AUTO":
        for name in ("model", "reasoning_effort", "model_source", "effort_source"):
            arguments += ["--" + name.replace("_", "-"), getattr(request, name)]
    assert operator.main(arguments, native_runner=native) == 0
    receipt = finalize_repair_workflow_receipt(path, "success", environ=env)
    github.outcome(receipt, success=True)
    carrier.publish_workflow_receipt(phase="completed", route="HUMAN_AUTHENTICATED", route_reason="READY", downstream_result="success", sources=github.api, environ=env)
    github.run.update(status="completed", conclusion="success")
    observed = github.api.from_issue(2076)[0]
    assert observed["state"] == "SELF_HOST_COMPLETED" and len(github.comments) == 1
    assert github.issue["state"] == "closed"
    profile = observed["operational_receipt"]["selectors"]
    expected = inherited if selection == "AUTO" else request
    for field in ("model", "reasoning_effort", "model_source", "effort_source"):
        assert profile[field] == getattr(expected, field)
    assert profile["executor"] == "codex" and len(native_calls) == 1
    assert operator.main(arguments, native_runner=native) == 0
    assert len(native_calls) == 1
    # Same-id changed SHA is not a retry opportunity.
    collision = arguments.copy()
    collision[3] = "0" * 40
    assert operator.main(collision, native_runner=native) == 1
    assert len(native_calls) == 1


@pytest.mark.parametrize("fault", ["issue_id", "issue_body", "head_sha", "pointer", "job_run", "receipt_issue", "receipt_sha", "forged_comment"])
def test_fresh_origin_consumer_rejects_moved_or_forged_github_mapping(tmp_path, monkeypatch, fault):
    import json
    from aios_renew.brain_attention import AttentionError, REPAIR_WORKFLOW_MARKER
    from aios_renew.github_issue_repair_wakeup import publish_workflow_receipt
    from aios_renew.operational_receipt import OperationalReceipt, finalize_repair_workflow_receipt
    _, event, env, delivery, _ = authoring_workflow_fixture(tmp_path, monkeypatch)
    github = WorkflowGitHubFixture(event, env["GITHUB_SHA"])
    output = publish_workflow_receipt(phase="waiting", route=delivery.repair_route, route_reason=delivery.repair_route_reason, sources=github.api, environ=env)
    github.pointer(output["comment_id"])
    receipt = OperationalReceipt("REPAIR", env["AIOS_REPAIR_DISPATCH_ID"], "ADMISSION_REJECTED",
        dict(failed_run_id=env["AIOS_FAILED_RUN_ID"], repair_sha=env["AIOS_REPAIR_SHA"]),
        cause=dict(authority="CORRECTION_PREFLIGHT", phase="EXECUTOR_SELECTION", reason_code="EXECUTOR_REQUIRED"), control_sha=env["GITHUB_SHA"])
    path = tmp_path / "forgery-receipt.json"
    path.write_text(receipt.render(), encoding="utf-8")
    payload = finalize_repair_workflow_receipt(path, "failure", environ=env)
    github.outcome(payload, success=False)
    if fault in {"issue_id", "head_sha"}:
        lines = github.comments[0]["body"].splitlines()
        for index, line in enumerate(lines):
            if line.startswith(REPAIR_WORKFLOW_MARKER):
                link = json.loads(line[len(REPAIR_WORKFLOW_MARKER):])
                link["origin_issue_id" if fault == "issue_id" else "control_sha"] = 99 if fault == "issue_id" else "f" * 40
                lines[index] = REPAIR_WORKFLOW_MARKER + json.dumps(link)
        github.comments[0]["body"] = "\n".join(lines)
    elif fault == "issue_body":
        github.issue["body"] += "\nChanged after admission"
    elif fault == "pointer":
        github.artifact(2, "aios-wake-source-v1-attempt-1", "source.json", dict(version=1, source_kind="comment", issue_id=99, issue_number=2075, comment_id=output["comment_id"]))
    elif fault == "job_run":
        github.jobs[0]["run_id"] += 1
    elif fault in {"receipt_issue", "receipt_sha"}:
        if fault == "receipt_issue": payload["workflow"]["origin_issue_number"] += 1
        else: payload["selectors"]["repair_sha"] = "0" * 40
        github.outcome(payload, success=False)
    else:
        github.comments[0]["user"] = {"login": "trung-via", "type": "User"}
    with pytest.raises(AttentionError):
        github.api.from_issue(2075)
    assert not github.logs_requested


def test_completion_unknown_diagnostics_and_misleading_carrier_success_remain_operational(tmp_path, monkeypatch):
    from aios_renew.github_issue_repair_wakeup import publish_workflow_receipt
    from aios_renew.operational_receipt import invoked_receipt, finalize_repair_workflow_receipt
    _, event, env, _, _ = authoring_workflow_fixture(tmp_path, monkeypatch)
    github = WorkflowGitHubFixture(event, env["GITHUB_SHA"])
    output = publish_workflow_receipt(phase="waiting", route="EXECUTORLESS_READY", route_reason="READY", sources=github.api, environ=env)
    github.pointer(output["comment_id"])
    path = tmp_path / "unknown-receipt.json"
    path.write_text(invoked_receipt("REPAIR", env["AIOS_REPAIR_DISPATCH_ID"], selectors=dict(
        failed_run_id=env["AIOS_FAILED_RUN_ID"], repair_sha=env["AIOS_REPAIR_SHA"])).render(), encoding="utf-8")
    # No error text or arbitrary URL is promoted into a cause.
    payload = finalize_repair_workflow_receipt(path, "failure", environ=env)
    assert payload["boundary"] == "OPERATIONAL_FAILED"
    assert payload["cause"]["reason_code"] == "DIAGNOSTIC_UNAVAILABLE"
    github.outcome(payload, success=False)
    github.run.update(status="completed", conclusion="success")  # misleading parent/carrier success
    publish_workflow_receipt(phase="completed", route="EXECUTORLESS_READY", route_reason="READY", downstream_result="success", sources=github.api, environ=env)
    observed = github.api.from_issue(2075)[0]
    assert observed["state"] == "OPERATIONAL_FAILED"
    assert observed["diagnostic"]["availability"] == "UNAVAILABLE"
    assert "status: OPERATIONAL_FAILED" in github.comments[0]["body"]
    github.diagnostics_denied = True
    observed = github.api.from_issue(2075)[0]
    assert observed["state"] == "OPERATIONAL_FAILED"
    assert observed["diagnostic"] == dict(availability="UNAVAILABLE", reason_code="DIAGNOSTIC_UNAVAILABLE")
    # Exact dispatch identity can discover a direct workflow without an Issue URL.
    github.diagnostics_denied = False
    github.run.update(path=".github/workflows/aios-self-hosted-repair-wakeup.yml", event="workflow_dispatch",
                      display_title="AIOS REPAIR " + env["AIOS_REPAIR_DISPATCH_ID"])
    payload["workflow"].update(event="workflow_dispatch", origin_issue_id=None, origin_issue_number=None)
    github.outcome(payload, success=False)
    assert github.api.from_dispatch(env["AIOS_REPAIR_DISPATCH_ID"])[0]["jobs"][0]["id"] == INCIDENT_168["job_id"]
