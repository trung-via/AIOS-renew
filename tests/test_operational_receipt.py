from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aios_renew.operational_receipt import (
    OperationalReceipt,
    OperationalReceiptError,
    new_admission_blocker,
    project_delivery_receipt,
    workflow_failure_receipt,
)


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
