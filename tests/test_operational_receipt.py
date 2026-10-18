from __future__ import annotations

import hashlib
import json
import copy
import io
import zipfile
from pathlib import Path

import pytest

from aios_renew.operational_receipt import (
    OperationalReceipt,
    OperationalReceiptError,
    new_admission_blocker,
    project_delivery_receipt,
    workflow_failure_receipt,
)
from aios_renew.brain_attention import ArtifactSources, AttentionError


class Task372Actions(ArtifactSources):
    """Synthetic GitHub-owned API/artifact shapes, including the observed no-RUN job.

    Only HTTP is substituted: correlation, ZIP/schema, attempt, receipt, jobs,
    source digest and portable-output gates are all production consumers.
    """
    run_id = 39026366429
    job_id = 117378039859
    delivery_id = "repair-RUN-369-001-" + "a" * 40
    issue_number = 2075

    def __init__(self):
        super().__init__("trung-via/AIOS-renew")
        self.calls = []
        self.issue = dict(id=20075, number=self.issue_number, body="immutable synthetic AUTHOR_REPAIR",
            user={"login": "trung-via"}, repository_url="https://api.github.com/repos/" + self.repository)
        self.origin = dict(format="AIOS_REPAIR_WORKFLOW_SOURCE", version=1, lane="ingress", repository=self.repository,
            workflow_run_id=1001, run_attempt=1, control_sha="c" * 40, event="issues", actor="trung-via",
            triggering_actor="trung-via", original_actor="trung-via", delivery_id=self.delivery_id,
            failed_run_id="RUN-369-001", repair_sha="a" * 40,
            issue=dict(id=20075, number=self.issue_number, body_digest=hashlib.sha256(self.issue["body"].encode()).hexdigest()), parent=None)
        self.source = dict(self.origin, lane="delivery", workflow_run_id=self.run_id, event="workflow_dispatch",
            actor="github-actions[bot]", triggering_actor="github-actions[bot]", original_actor="github-actions[bot]", issue=None,
            parent=dict(workflow_run_id=1001, run_attempt=1, artifact_id=10))
        self.receipt = OperationalReceipt("REPAIR", self.delivery_id, "ADMISSION_REJECTED",
            {"failed_run_id": self.origin["failed_run_id"], "repair_sha": self.origin["repair_sha"]},
            cause=dict(authority="CORRECTION_PREFLIGHT", phase="REPAIR_EXECUTOR_REQUIREMENT",
                       reason_code="CODING_REPAIR_EXECUTOR_REQUIRED"), control_sha="c" * 40).as_dict()
        self.runs = {1001: self.run_document(self.origin), self.run_id: self.run_document(self.source)}
        self.runs[1001]["conclusion"] = "success"
        self.jobs = [dict(id=self.job_id, run_id=self.run_id, run_attempt=1, name="execute-repair", status="completed", conclusion="failure",
            steps=[dict(number=2, name="Deliver one immutable REPAIR intent", status="completed", conclusion="failure")])]
        self.extra_sources = {}
        self.defect = None

    @staticmethod
    def run(source):
        return dict(id=source["workflow_run_id"], run_attempt=source["run_attempt"], head_sha=source["control_sha"],
            path=".github/workflows/aios-brain-ingress.yml" if source["lane"] == "ingress" else ".github/workflows/aios-self-hosted-repair-wakeup.yml",
            head_repository={"full_name": source["repository"]}, head_branch="main", event=source["event"],
            actor={"login": source["actor"]}, triggering_actor={"login": source["triggering_actor"]}, status="completed", conclusion="failure")

    def request(self, path, binary=False):
        # The production run authenticator calls self.run: retain it below;
        # fixtures construct run JSON through run_document instead.
        self.calls.append(path)
        if self.defect == "permission":
            raise AttentionError("SOURCE_UNAVAILABLE")
        payloads = {10: self.origin, 21: self.source, **self.extra_sources}
        if self.receipt is not None:
            payloads[22] = self.receipt
        artifacts = {}
        archives = {}
        for identifier, payload in payloads.items():
            attempt = payload.get("run_attempt", 1)
            name = (f"aios-repair-correlation-v1-{payload['lane']}-attempt-{attempt}" if "lane" in payload
                    else f"aios-operational-receipt-v2-repair-attempt-{self.source['run_attempt']}")
            filename = "source.json" if "lane" in payload else "aios-repair-operational-receipt-v2.json"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as zipped:
                raw = b"{" if self.defect == "invalid-json" and identifier == 21 else json.dumps(payload).encode()
                zipped.writestr("../private" if self.defect == "zip-path" and identifier == 21 else filename, raw)
            archives[identifier] = buffer.getvalue()
            artifacts[identifier] = dict(id=identifier, name=name, expired=self.defect == "expired" and identifier == 21,
                size_in_bytes=len(archives[identifier]), workflow_run={"id": payload.get("workflow_run_id", self.run_id)},
                digest="sha256:" + hashlib.sha256(archives[identifier]).hexdigest())
        if self.defect == "digest":
            artifacts[21]["digest"] = "sha256:" + "0" * 64
        if self.defect == "receipt-expired" and 22 in artifacts:
            artifacts[22]["expired"] = True
        if self.defect == "artifact-run":
            artifacts[21]["workflow_run"]["id"] = 999
        if self.defect == "lane":
            artifacts[21]["name"] = "aios-repair-correlation-v1-wakeup-attempt-1"
        artifacts[23] = dict(id=23, name="aios-repair-delivery-v1-" + self.delivery_id,
            expired=False, workflow_run={"id": self.run_id})
        pointer = dict(workflow_run_id=1001, run_attempt=1, artifact_id=10)
        if path == f"/issues/{self.issue_number}":
            return copy.deepcopy(self.issue)
        if path == f"/issues/{self.issue_number}/comments?per_page=100":
            comment = dict(user={"login": "github-actions[bot]"}, body="AIOS_REPAIR_WORKFLOW_POINTER=" + json.dumps(pointer))
            return [comment, copy.deepcopy(comment)]  # Duplicate hints do not create an attempt.
        if path == f"/actions/artifacts?name=aios-repair-delivery-v1-{self.delivery_id}&per_page=100":
            return dict(total_count=1, artifacts=[artifacts[23]])
        for identifier in self.runs:
            if path == f"/actions/runs/{identifier}/artifacts?per_page=100":
                items = [a for a in artifacts.values() if a["workflow_run"]["id"] == identifier]
                # An inconsistent API list must not conceal a substituted artifact.
                if self.defect == "artifact-run" and identifier == self.run_id:
                    items.append(artifacts[21])
                return dict(total_count=len(items), artifacts=items)
            for attempt in (1, 2):
                if path == f"/actions/runs/{identifier}/attempts/{attempt}":
                    return copy.deepcopy(self.runs[identifier])
        if path == f"/actions/runs/{self.run_id}/attempts/{self.source['run_attempt']}/jobs?per_page=100":
            if self.defect == "jobs-permission":
                raise AttentionError("SOURCE_UNAVAILABLE")
            return dict(total_count=len(self.jobs), jobs=copy.deepcopy(self.jobs))
        for identifier in payloads:
            if path == f"/actions/artifacts/{identifier}":
                return copy.deepcopy(artifacts[identifier])
            if path == f"/actions/artifacts/{identifier}/zip":
                return archives[identifier]
        raise AttentionError("SOURCE_UNAVAILABLE")

    run_document = run
    run = ArtifactSources.run


def test_task372_original_issue_finds_exact_missing_executor_job_without_run_or_logs():
    from aios_renew.operational_receipt import observe_repair_delivery, validate_repair_delivery_context
    backend = Task372Actions()
    result = observe_repair_delivery(issue_number=backend.issue_number, sources=backend)
    validate_repair_delivery_context(result)
    assert result["availability"] == "AVAILABLE", result
    attempt = result["attempt"]
    assert (attempt["workflow_run_id"], attempt["run_attempt"]) == (39026366429, 1)
    assert attempt["jobs"][0]["job_id"] == 117378039859
    assert attempt["jobs"][0]["steps"][0] == dict(number=2, name="Deliver one immutable REPAIR intent", status="completed", conclusion="failure")
    assert attempt["cause"]["reason_code"] == "CODING_REPAIR_EXECUTOR_REQUIRED"
    assert attempt["boundary"] == "ADMISSION_REJECTED" and result["run_created"] is False
    assert attempt["original_actor"] == "trung-via" and attempt["actor"] == "github-actions[bot]"
    assert attempt["run_id"] is None and attempt["failed_run_id"] == "RUN-369-001"
    assert attempt["repair_sha"] == "a" * 40 and attempt["delivery_id"] == backend.delivery_id
    assert not any("logs" in path or "/actions/runs?" in path for path in backend.calls)
    assert "immutable synthetic" not in json.dumps(result)


@pytest.mark.parametrize("defect", ["permission", "expired", "invalid-json", "zip-path", "digest", "artifact-run", "lane",
    "wrong-job-run", "wrong-job-attempt", "wrong-attempt", "wrong-issue", "moved-issue", "wrong-sha", "parent", "receipt", "secret-cause"])
def test_task372_substitutions_fail_closed_with_typed_blocker(defect):
    from aios_renew.operational_receipt import observe_repair_delivery, validate_repair_delivery_context
    backend = Task372Actions()
    backend.defect = defect
    if defect == "wrong-job-run": backend.jobs[0]["run_id"] = 999
    if defect == "wrong-job-attempt": backend.jobs[0]["run_attempt"] = 2
    if defect == "wrong-attempt": backend.runs[backend.run_id]["run_attempt"] = 2
    if defect == "wrong-issue": backend.issue["id"] += 1
    if defect == "moved-issue": backend.issue["body"] += " edited"
    if defect == "wrong-sha": backend.source["control_sha"] = "d" * 40
    if defect == "parent": backend.source["repair_sha"] = "d" * 40
    if defect == "receipt": backend.receipt["selectors"]["failed_run_id"] = "RUN-foreign-001"
    if defect == "secret-cause": backend.receipt["cause"]["reason_code"] = "credential=synthetic-secret"
    result = observe_repair_delivery(issue_number=backend.issue_number, sources=backend)
    validate_repair_delivery_context(result)
    assert result["availability"] == "UNAVAILABLE" and result["attempt"] is None, result
    assert result["blocker"]["code"] and result["run_created"] is None
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.parametrize("status,conclusion,receipt", [("queued", None, False), ("in_progress", None, False),
    ("completed", "failure", False), ("completed", "failure", True), ("completed", "success", True)])
def test_task372_delivery_lookup_preserves_real_workflow_state_over_transient_marker(status, conclusion, receipt):
    from aios_renew.operational_receipt import observe_repair_delivery
    backend = Task372Actions()
    backend.runs[backend.run_id].update(status=status, conclusion=conclusion)
    backend.jobs[0].update(status=status, conclusion=conclusion)
    backend.jobs[0]["steps"] = [] if status == "queued" else [dict(number=2, name="Deliver one immutable REPAIR intent", status=status, conclusion=conclusion)]
    if receipt:
        backend.receipt = OperationalReceipt("REPAIR", backend.delivery_id, "AIOS_INVOKED", backend.receipt["selectors"], control_sha="c" * 40).as_dict()
    else:
        backend.receipt = None
    result = observe_repair_delivery(delivery_id=backend.delivery_id, sources=backend)
    assert result["availability"] == "AVAILABLE", result
    assert result["attempt"]["status"] == {"in_progress": "running"}.get(status, status)
    assert result["run_created"] is None  # A marker never establishes absence or existence of a RUN.
    assert result["attempt"]["cause"]["reason_code"] == "UNKNOWN"
    if conclusion == "failure": assert result["attempt"]["boundary"] == "OPERATIONAL_FAILED"


def test_task372_missing_source_and_cross_attempt_ambiguity_are_not_matches():
    from aios_renew.operational_receipt import observe_repair_delivery
    backend = Task372Actions()
    backend.extra_sources[24] = dict(backend.source, run_attempt=2)
    # Both attempt endpoints authenticate their own immutable attempt, as GitHub does.
    real = backend.request
    def request(path, binary=False):
        value = real(path, binary)
        if path == f"/actions/runs/{backend.run_id}/attempts/2": value["run_attempt"] = 2
        return value
    backend.request = request
    result = observe_repair_delivery(delivery_id=backend.delivery_id, sources=backend)
    assert result["blocker"]["code"] == "REPAIR_ATTEMPT_AMBIGUOUS" and result["attempt"] is None
    backend.defect = "permission"
    result = observe_repair_delivery(delivery_id=backend.delivery_id, sources=backend)
    assert result["availability"] == "UNAVAILABLE" and result["attempt"] is None


@pytest.mark.parametrize("defect", ["jobs-permission", "receipt-expired", "withheld-names"])
def test_task372_missing_detail_preserves_authenticated_attempt_and_strongest_failure(defect):
    from aios_renew.operational_receipt import observe_repair_delivery
    backend = Task372Actions()
    backend.defect = defect
    backend.runs[backend.run_id].update(status="in_progress", conclusion=None)
    if defect == "withheld-names":
        backend.jobs[0]["name"] = "private-token=synthetic-secret"
        backend.jobs[0]["steps"][0]["name"] = "C:/private/synthetic-secret"
    result = observe_repair_delivery(issue_number=backend.issue_number, sources=backend)
    assert result["availability"] == "AVAILABLE", result
    attempt = result["attempt"]
    assert attempt["workflow_run_id"] == backend.run_id and "synthetic-secret" not in json.dumps(result)
    if defect == "jobs-permission":
        assert attempt["jobs_availability"] == "UNAVAILABLE" and attempt["jobs_blocker"]["code"] == "SOURCE_UNAVAILABLE"
        assert attempt["boundary"] == "ADMISSION_REJECTED"  # Existing owner-observed cause survives missing jobs.
    else:
        assert attempt["workflow_failure_observed"] is True
        if defect == "receipt-expired":
            assert attempt["boundary"] == "OPERATIONAL_FAILED" and attempt["receipt_availability"] == "UNAVAILABLE"
            assert attempt["receipt_blocker"]["code"] == "REPAIR_ARTIFACT_EXPIRED" and result["run_created"] is None
        else:
            assert attempt["jobs"][0]["name"] == attempt["jobs"][0]["steps"][0]["name"] == "WITHHELD"


def test_task372_stronger_preflight_receipt_survives_finally_projection(tmp_path, monkeypatch):
    from aios_renew.operational_receipt import write_environment_receipt
    backend = Task372Actions()
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(backend.receipt), encoding="utf-8")
    monkeypatch.setenv("AIOS_OPERATIONAL_RECEIPT_PATH", str(path))
    write_environment_receipt(OperationalReceipt("REPAIR", backend.delivery_id, "AIOS_INVOKED", backend.receipt["selectors"], control_sha="c" * 40))
    assert json.loads(path.read_bytes()) == backend.receipt


def test_task372_old_invocation_receipt_without_attribution_flags_stays_unknown():
    from aios_renew.operational_receipt import observe_repair_delivery
    backend = Task372Actions()
    backend.receipt = OperationalReceipt("REPAIR", backend.delivery_id, "AIOS_INVOKED", backend.receipt["selectors"], control_sha="c" * 40).as_dict()
    backend.receipt.pop("run_created")
    backend.receipt.pop("executor_invoked")
    original = copy.deepcopy(backend.receipt)
    context = observe_repair_delivery(delivery_id=backend.delivery_id, sources=backend)
    assert context["availability"] == "AVAILABLE" and context["run_created"] is None
    assert context["attempt"]["boundary"] == "OPERATIONAL_FAILED"
    assert backend.receipt == original


def test_operational_flags_cannot_substitute_for_canonical_run_creation():
    for flags in ({"run_created": 1}, {"executor_invoked": "false"}, {"run_id": "RUN-fixture-001"}):
        receipt = OperationalReceipt("PRIMARY", "fixture-delivery", "ADMISSION_REJECTED", {}, **flags)
        with pytest.raises(OperationalReceiptError):
            receipt.as_dict()


def test_attention_projection_excludes_execution_profile_and_retains_explicit_no_run():
    receipt = workflow_failure_receipt("REPAIR", "fixture-delivery", "CONTROL_SOURCE_DIRTY",
                                       selectors={"executor": "codex", "model": "private-model", "reasoning_effort": "private-effort"})
    observation = receipt.attention_observation()
    assert observation["boundary"] == "PRE_AIOS_FAILED"
    assert observation["run_created"] is False and observation["operation"] == "REPAIR"
    assert "run_id" not in observation and "executor" not in observation
    assert "private" not in json.dumps(observation)


def _journal(root: Path, family_dir: str, delivery_id: str, payload: dict) -> None:
    key = hashlib.sha256(delivery_id.encode("ascii")).hexdigest()
    path = root / family_dir / f"{key}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_pre_aios_failure_is_operational_only() -> None:
    receipt = workflow_failure_receipt(
        "PRIMARY",
        "delivery-149",
        "AIOS_REPO_ROOT_NOT_CONFIGURED",
        selectors={"task_id": "TASK-149", "task_revision": 1, "executor": "codex"},
    ).as_dict()

    assert receipt["boundary"] == "OPERATIONAL_FAILED"
    assert receipt["run_created"] is False
    assert receipt["executor_invoked"] is False
    assert receipt["cause"] == {
        "authority": "WORKFLOW",
        "phase": "PRE_AIOS",
        "reason_code": "AIOS_REPO_ROOT_NOT_CONFIGURED",
    }
    assert "run_id" not in receipt and "terminal_pointer" not in receipt


def test_receipt_binds_exact_control_sha_from_workflow_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_sha = "a" * 40
    monkeypatch.setenv("AIOS_CONTROL_SHA", control_sha)

    receipt = workflow_failure_receipt(
        "PRIMARY", "delivery-158", "CONTROL_SOURCE_DIRTY"
    ).as_dict()

    assert receipt["control_sha"] == control_sha
    assert receipt["run_created"] is False
    assert receipt["executor_invoked"] is False


def test_exact_journal_binding_is_required_for_run_attribution(tmp_path: Path) -> None:
    root = tmp_path / "aios"
    (root / "runs").mkdir(parents=True)
    (root / "runs" / "RUN-149-999.json").write_text("{}", encoding="utf-8")
    _journal(
        root,
        "dispatches",
        "delivery-149",
        {
            "dispatch_id": "delivery-149",
            "task_id": "TASK-149",
            "executor": "codex",
            "run_id": None,
        },
    )

    receipt = project_delivery_receipt(
        root,
        family="PRIMARY",
        delivery_id="delivery-149",
        selectors={"task_id": "TASK-149", "executor": "codex"},
    ).as_dict()

    assert receipt["boundary"] == "AIOS_INVOKED"
    assert receipt["run_created"] is False
    assert "run_id" not in receipt


def test_run_attribution_does_not_claim_executor_invocation(tmp_path: Path) -> None:
    root = tmp_path / "aios"
    run_id = "RUN-149-001"
    _journal(
        root,
        "dispatches",
        "delivery-149",
        {
            "dispatch_id": "delivery-149",
            "task_id": "TASK-149",
            "executor": "codex",
            "run_id": run_id,
        },
    )

    receipt = project_delivery_receipt(
        root,
        family="PRIMARY",
        delivery_id="delivery-149",
        selectors={"task_id": "TASK-149", "executor": "codex"},
    ).as_dict()

    assert receipt["boundary"] == "RUN_ATTRIBUTED"
    assert receipt["run_id"] == run_id
    assert receipt["run_created"] is True
    assert receipt["executor_invoked"] is False


def test_terminal_projection_contains_pointer_not_terminal_body(tmp_path: Path) -> None:
    root = tmp_path / "aios"
    run_id = "RUN-149-001"
    _journal(
        root,
        "correction-dispatches",
        "correction-149",
        {
            "correction_dispatch_id": "correction-149",
            "source_run_id": "RUN-148-001",
            "finding_id": "F1",
            "executor": "codex",
            "run_id": run_id,
        },
    )
    terminal = root / "failures" / f"{run_id}.json"
    terminal.parent.mkdir(parents=True)
    terminal.write_text('{"error":{"secret":"must-not-copy"}}', encoding="utf-8")

    receipt = project_delivery_receipt(
        root,
        family="REMEDIATION",
        delivery_id="correction-149",
        selectors={
            "source_run_id": "RUN-148-001",
            "finding_id": "F1",
            "executor": "codex",
        },
    ).as_dict()

    assert receipt["boundary"] == "TERMINAL_POINTER"
    assert "run_id" not in receipt
    assert receipt["terminal_pointer"] == {
        "kind": "FAILURE",
        "run_id": run_id,
        "artifact": f".git/aios/failures/{run_id}.json",
    }
    assert receipt["executor_invoked"] is False
    assert "secret" not in json.dumps(receipt)


def test_exact_admission_failure_cause_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "aios"
    _journal(
        root,
        "repair-dispatches",
        "repair-149",
        {"repair_dispatch_id": "repair-149", "run_id": None},
    )
    diagnostic = root / "admission-failures" / "exact.json"
    diagnostic.parent.mkdir(parents=True)
    diagnostic.write_text(
        json.dumps(
            {
                "format": "AIOS_ADMISSION_FAILURE",
                "version": 2,
                "operation": "REPAIR",
                "repair_dispatch_id": "repair-149",
                "phase": "FAILED_RUN_RESOLUTION",
                "reason_code": "CANONICAL_LINEAGE_MISSING",
            }
        ),
        encoding="utf-8",
    )

    receipt = project_delivery_receipt(
        root, family="REPAIR", delivery_id="repair-149"
    ).as_dict()

    assert receipt["boundary"] == "ADMISSION_REJECTED"
    assert receipt["cause"]["phase"] == "FAILED_RUN_RESOLUTION"
    assert receipt["cause"]["reason_code"] == "CANONICAL_LINEAGE_MISSING"


def test_human_blocker_uses_only_new_exact_diagnostic(tmp_path: Path) -> None:
    directory = tmp_path / "admission-failures"
    directory.mkdir()
    old = directory / "old.json"
    old.write_text("{}", encoding="utf-8")
    current = directory / "current.json"
    current.write_text(
        json.dumps(
            {
                "format": "AIOS_ADMISSION_FAILURE",
                "version": 2,
                "operation": "PRIMARY",
                "requested_task_id": "TASK-149",
                "requested_executor": "codex",
                "phase": "TASK_ADMISSION",
                "reason_code": "TASK_CONTRACT_REJECTED",
            }
        ),
        encoding="utf-8",
    )

    blocker = new_admission_blocker(
        directory,
        before={old.name},
        operation="PRIMARY",
        exact_facts={"requested_task_id": "TASK-149", "requested_executor": "codex"},
    )

    assert blocker == {
        "code": "TASK_CONTRACT_REJECTED",
        "phase": "TASK_ADMISSION",
        "reason_code": "TASK_CONTRACT_REJECTED",
        "source": "AIOS_ADMISSION_FAILURE",
    }


@pytest.mark.parametrize("family", ["PRIMARY", "REMEDIATION", "REPAIR"])
def test_receipt_remote_delivery_lookup_reports_only_pre_run_rejection(
    tmp_path: Path, family: str,
) -> None:
    from aios_renew.review_transport import transport_admission_failure
    from tests.operator_test_support import make_repo, git

    repo = make_repo(tmp_path)
    delivery = "task241-r1-primary-codex61-001"
    field = {
        "PRIMARY": "dispatch_id", "REMEDIATION": "correction_dispatch_id",
        "REPAIR": "repair_dispatch_id",
    }[family]
    diagnostic = {
        "format": "AIOS_ADMISSION_FAILURE", "version": 2, "kind": "ADMISSION_FAILURE",
        "operation": family, field: delivery, "executor_invoked": False,
        "phase": "PRIMARY_SYNCHRONIZATION", "reason_code": "PRIMARY_SYNCHRONIZATION_REJECTED",
        "error": {"type": "OperatorError", "message": "local branch has diverged from upstream"},
    }
    content = json.dumps(diagnostic).encode()
    identity = hashlib.sha256(content).hexdigest()
    path = tmp_path / "diagnostic.json"
    path.write_bytes(content)
    transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    head_before = git(repo, "rev-parse", "HEAD")
    refs_before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    root = tmp_path / "absent-runtime-state"
    receipt = project_delivery_receipt(
        root, family=family, delivery_id=delivery, remote_repo=repo,
    ).as_dict()
    assert receipt["boundary"] == "ADMISSION_REJECTED"
    assert receipt["cause"] == {
        "authority": "AIOS_ADMISSION_FAILURE", "phase": "PRIMARY_SYNCHRONIZATION",
        "reason_code": "PRIMARY_SYNCHRONIZATION_REJECTED",
    }
    assert receipt["run_created"] is False
    assert receipt["executor_invoked"] is False
    assert not {"run_id", "terminal_pointer", "verification", "review", "publication"} & receipt.keys()
    assert "local branch has diverged" not in json.dumps(receipt)
    assert not root.exists()
    assert git(repo, "rev-parse", "HEAD") == head_before
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == refs_before
    absent = project_delivery_receipt(
        root, family=family, delivery_id="absent", remote_repo=repo,
    ).as_dict()
    assert absent["boundary"] == "AIOS_INVOKED"
    assert absent["run_created"] is False


@pytest.mark.parametrize("terminal", [False, True])
def test_receipt_local_run_attribution_precedes_remote_admission_diagnostic(
    tmp_path: Path, terminal: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aios_renew.operational_receipt as receipts

    root = tmp_path / "aios"
    _journal(root, "dispatches", "exact", {"dispatch_id": "exact", "run_id": "RUN-242-001"})
    if terminal:
        artifact = root / "failures" / "RUN-242-001.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("{}", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("exact journal/RUN attribution must take precedence")

    monkeypatch.setattr(receipts, "resolve_admission_failure_delivery", forbidden)
    payload = project_delivery_receipt(
        root, family="PRIMARY", delivery_id="exact", remote_repo=tmp_path,
    ).as_dict()
    assert payload["boundary"] == ("TERMINAL_POINTER" if terminal else "RUN_ATTRIBUTED")
    assert payload["run_created"] is True
    assert payload["executor_invoked"] is False
    assert "cause" not in payload


def test_receipt_local_admission_precedes_remote_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aios_renew.operational_receipt as receipts

    _journal(tmp_path, "dispatches", "exact", {"dispatch_id": "exact", "run_id": None})
    directory = tmp_path / "admission-failures"
    directory.mkdir()
    (directory / "local.json").write_text(json.dumps({
        "format": "AIOS_ADMISSION_FAILURE", "version": 2, "operation": "PRIMARY",
        "dispatch_id": "exact", "phase": "TASK_ADMISSION", "reason_code": "TASK_CONTRACT_REJECTED",
    }), encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("exact local diagnostic takes precedence")

    monkeypatch.setattr(receipts, "resolve_admission_failure_delivery", forbidden)
    payload = project_delivery_receipt(
        tmp_path, family="PRIMARY", delivery_id="exact", remote_repo=tmp_path,
    ).as_dict()
    assert payload["cause"]["phase"] == "TASK_ADMISSION"
    assert payload["run_created"] is False


def test_receipt_remote_invalid_diagnostic_is_not_absence_or_run_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aios_renew.operational_receipt as receipts
    from aios_renew.review_transport import ReviewTransportError

    def reject(*args, **kwargs):
        raise ReviewTransportError("admission delivery content identity conflict")

    monkeypatch.setattr(receipts, "resolve_admission_failure_delivery", reject)
    with pytest.raises(receipts.OperationalReceiptError, match="identity conflict"):
        project_delivery_receipt(
            tmp_path, family="PRIMARY", delivery_id="exact", remote_repo=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []
