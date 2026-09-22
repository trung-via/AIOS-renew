from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aios_renew.operational_receipt import (
    new_admission_blocker,
    project_delivery_receipt,
    workflow_failure_receipt,
)


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
