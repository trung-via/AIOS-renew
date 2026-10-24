"""Lean VP Intra v1.3 L3-06: native Codex feedback delivery and receipt capability.

Focused verification of the authentic native Codex recipient path, source-correlated
receipt generation, explicit native-probe absence reporting, negative authority/continuity
guards, and legacy one-shot behavior.
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from aios_renew.codex_adapter import (
    AuthenticCodexProbe,
    CodexAdapter,
    CodexNativeDeliveryError,
    CodexNativeProbeAbsentError,
    CodexNativeReceipt,
    CodexRecipientAcknowledgment,
)
from aios_renew.dispatcher import Dispatcher
from aios_renew.execution_profile import ResolvedExecutionProfile
from aios_renew.intra_run_proof_contract import (
    FEEDBACK_AUTHORITY,
    FeedbackConsumptionRequest,
    format_feedback_consumption,
    parse_feedback_consumption,
)
from aios_renew.run import RunLeaseRegistry
from tests.test_lean_vp_l3_factual_feedback import FeedbackFixture


def prepared(f: FeedbackFixture) -> tuple[FeedbackConsumptionRequest, Any, Any]:
    source, checkpoint = f.sealed()
    feedback = f.prepare(source)
    return FeedbackConsumptionRequest(1, source, feedback.digest), checkpoint, feedback


def successful_output(run_id: str) -> str:
    return json.dumps(
        {
            "result": {
                "head_sha": "def456",
                "claims": [
                    {
                        "id": "C1",
                        "satisfies": ["AC1"],
                        "claim": "Codex adapter completed the task.",
                        "evidence": ["E1"],
                    }
                ],
                "changed_files": ["src/aios_renew/codex_adapter.py"],
                "unresolved": [],
            },
            "evidence": [
                {
                    "evidence_id": "E1",
                    "run_id": run_id,
                    "subject_sha": "def456",
                    "type": "TEST",
                    "source": {"command": "pytest tests/test_codex_adapter.py"},
                    "result": {"exit_code": 0, "summary": "tests passed"},
                    "raw": {"path": ".ai/evidence/E1.log"},
                }
            ],
        }
    )


def test_native_codex_feedback_probe_absence_blocks_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "probe-absence", monkeypatch)
    request, checkpoint, feedback = prepared(f)

    observed_results: list[Any] = []
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        res = adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        )
        observed_results.append(res)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner)
    assert adapter.has_native_probe is False
    assert adapter.native_probe is None
    assert adapter.last_native_receipt is None

    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )

    pkg = dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)
    assert pkg is not None
    assert len(observed_results) == 1
    consumption = observed_results[0]

    # Inability to prove live native delivery yields bounded unavailable / STOP outcome
    assert consumption.data["status"] == "BLOCKED"
    assert consumption.data["reason"] == "CALLBACK_CONFIRMATION_NOT_ESTABLISHED"
    assert consumption.data["binding"] is None
    assert consumption.data["receipt_id"] is None
    assert consumption.data["stop"]["status"] == "STOP"
    assert consumption.data["stop"]["c2_admission"] == "NOT_AUTHORIZED"
    assert consumption.data["stop"]["human_opt_in"] == "NOT_ESTABLISHED"
    assert consumption.data["stop"]["token_budget"] == "NOT_ESTABLISHED"
    assert consumption.data["authority"] == FEEDBACK_AUTHORITY
    assert adapter.last_native_receipt is None


def test_native_codex_feedback_injected_synthetic_callback_rejected_blocks_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = FeedbackFixture(tmp_path / "synthetic-callback", monkeypatch)
    request, checkpoint, feedback = prepared(f)

    observed_results: list[Any] = []
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)

    synthetic_calls: list[Any] = []

    def synthetic_callback(fb: Any, receipt_id: str) -> bool:
        synthetic_calls.append((fb, receipt_id))
        return True

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        res = adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        )
        observed_results.append(res)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=synthetic_callback)
    assert adapter.has_native_probe is False
    assert adapter.native_probe is synthetic_callback
    assert adapter.last_native_receipt is None

    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )

    pkg = dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)
    assert pkg is not None
    assert len(observed_results) == 1
    consumption = observed_results[0]

    # Injected synthetic callback fails closed: cannot prove authentic native recipient delivery
    assert consumption.data["status"] == "BLOCKED"
    assert consumption.data["reason"] == "CALLBACK_CONFIRMATION_NOT_ESTABLISHED"
    assert consumption.data["binding"] is None
    assert consumption.data["receipt_id"] is None
    assert consumption.data["stop"]["status"] == "STOP"
    assert consumption.data["stop"]["c2_admission"] == "NOT_AUTHORIZED"
    assert adapter.last_native_receipt is None
    assert synthetic_calls == []


def test_native_codex_feedback_with_authentic_probe_delivers_source_correlated_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = FeedbackFixture(tmp_path / "probe-success", monkeypatch)
    request, checkpoint, feedback = prepared(f)

    probe = AuthenticCodexProbe()
    observed_results: list[Any] = []
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        res = adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        )
        observed_results.append(res)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    assert adapter.has_native_probe is True

    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )

    pkg = dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)
    assert pkg is not None
    assert len(observed_results) == 1
    consumption = observed_results[0]

    # Successful native delivery yields LOCAL_ACKNOWLEDGED and bounded STOP (no production C2)
    assert consumption.data["status"] == "LOCAL_ACKNOWLEDGED"
    assert consumption.data["binding"]["feedback_digest"] == feedback.digest
    assert consumption.data["binding"]["checkpoint_digest"] == checkpoint.digest
    assert consumption.data["binding"]["admitted_executor"] == "codex"
    assert consumption.data["stop"]["status"] == "STOP"
    assert consumption.data["stop"]["c2_admission"] == "NOT_AUTHORIZED"

    # Authentic probe was invoked exactly once with matching source feedback and receipt_id
    assert len(probe.acknowledged_deliveries) == 1
    assert probe.acknowledged_deliveries[0][0] == feedback
    assert probe.acknowledged_deliveries[0][1] == consumption.data["receipt_id"]

    # Source-correlated receipt is produced on the adapter
    receipt = adapter.last_native_receipt
    assert receipt is not None
    assert isinstance(receipt, CodexNativeReceipt)
    assert receipt.receipt_id == consumption.data["receipt_id"]
    assert receipt.feedback_digest == feedback.digest
    assert receipt.run_id == f.run.run_id
    assert receipt.checkpoint_ordinal == checkpoint.ordinal
    assert receipt.executor == "codex"
    assert receipt.delivery_mode == "AUTHENTIC_NATIVE_RECIPIENT"
    assert len(receipt.correlation_digest) == 64

    # Receipt round-trips through format/parse
    assert parse_feedback_consumption(format_feedback_consumption(consumption)) == consumption


def test_native_codex_feedback_rejected_outside_execution_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "wrong-thread", monkeypatch)
    _, _, feedback = prepared(f)

    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    thread_errors: list[Exception] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        def cross_thread_call() -> None:
            try:
                adapter.confirm_local_feedback(feedback=feedback, receipt_id="1" * 64)
            except Exception as exc:
                thread_errors.append(exc)

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(cross_thread_call).result()

        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    assert len(thread_errors) == 1
    assert isinstance(thread_errors[0], CodexNativeDeliveryError)
    assert "outside native execution thread" in str(thread_errors[0])
    assert probe.acknowledged_deliveries == []
    assert adapter.last_native_receipt is None


def test_native_codex_feedback_rejected_detached_after_execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "detached-call", monkeypatch)
    _, _, feedback = prepared(f)

    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    with pytest.raises(CodexNativeDeliveryError, match="outside native execution thread"):
        adapter.confirm_local_feedback(feedback=feedback, receipt_id="1" * 64)
    assert probe.acknowledged_deliveries == []
    assert adapter.last_native_receipt is None


def test_native_codex_feedback_rejected_cross_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "cross-run-fb", monkeypatch)
    _, _, feedback = prepared(f)

    class CrossRunFeedback:
        digest = "1" * 64
        data = {
            "binding": {
                "run_id": "RUN-999-999",
                "task_id": f.task.task_id,
                "checkpoint_ordinal": 1,
                "checkpoint_digest": "a" * 64,
            }
        }

    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    delivery_errors: list[Exception] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        try:
            adapter.confirm_local_feedback(feedback=CrossRunFeedback(), receipt_id="2" * 64)
        except Exception as exc:
            delivery_errors.append(exc)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    assert len(delivery_errors) == 1
    assert isinstance(delivery_errors[0], CodexNativeDeliveryError)
    assert "cross-RUN feedback rejected" in str(delivery_errors[0])
    assert adapter.last_native_receipt is None


def test_native_codex_feedback_rejected_cross_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "cross-task-fb", monkeypatch)
    _, _, feedback = prepared(f)

    class CrossTaskFeedback:
        digest = "1" * 64
        data = {
            "binding": {
                "run_id": f.run.run_id,
                "task_id": "TASK-999",
                "checkpoint_ordinal": 1,
                "checkpoint_digest": "a" * 64,
            }
        }

    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    delivery_errors: list[Exception] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        try:
            adapter.confirm_local_feedback(feedback=CrossTaskFeedback(), receipt_id="2" * 64)
        except Exception as exc:
            delivery_errors.append(exc)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    assert len(delivery_errors) == 1
    assert isinstance(delivery_errors[0], CodexNativeDeliveryError)
    assert "cross-TASK feedback rejected" in str(delivery_errors[0])
    assert adapter.last_native_receipt is None


def test_native_codex_feedback_rejected_malformed_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "malformed-fb", monkeypatch)
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    delivery_errors: list[Exception] = []

    class BadFeedback:
        digest = "1" * 64
        data = None

    probe = AuthenticCodexProbe()

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        try:
            adapter.confirm_local_feedback(feedback=BadFeedback(), receipt_id="2" * 64)
        except Exception as exc:
            delivery_errors.append(exc)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    assert len(delivery_errors) == 1
    assert isinstance(delivery_errors[0], CodexNativeDeliveryError)
    assert "malformed feedback" in str(delivery_errors[0])


def test_native_codex_feedback_rejected_wrong_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "wrong-checkpoint", monkeypatch)
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    delivery_errors: list[Exception] = []

    class BadCheckpointFeedback:
        digest = "1" * 64
        data = {
            "binding": {
                "run_id": f.run.run_id,
                "task_id": f.task.task_id,
                "checkpoint_ordinal": 0,
                "checkpoint_digest": "",
            }
        }

    probe = AuthenticCodexProbe()

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        try:
            adapter.confirm_local_feedback(feedback=BadCheckpointFeedback(), receipt_id="2" * 64)
        except Exception as exc:
            delivery_errors.append(exc)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    assert len(delivery_errors) == 1
    assert isinstance(delivery_errors[0], CodexNativeDeliveryError)
    assert "checkpoint" in str(delivery_errors[0])
    assert adapter.last_native_receipt is None


def test_native_codex_feedback_rejected_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "replay-feedback", monkeypatch)
    request, checkpoint, feedback = prepared(f)
    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    replay_errors: list[Exception] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        res1 = adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        )
        assert res1.data["status"] == "LOCAL_ACKNOWLEDGED"
        try:
            adapter.confirm_local_feedback(feedback=feedback, receipt_id=res1.data["receipt_id"])
        except Exception as exc:
            replay_errors.append(exc)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    assert len(replay_errors) == 1
    assert isinstance(replay_errors[0], CodexNativeDeliveryError)
    assert "replay rejected" in str(replay_errors[0])


def test_native_codex_feedback_rejected_lease_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "lease-loss", monkeypatch)
    request, checkpoint, feedback = prepared(f)
    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    observed_results: list[Any] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        lease.release()
        res = adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        )
        observed_results.append(res)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    assert len(observed_results) == 1
    assert observed_results[0].data["status"] == "BLOCKED"
    assert adapter.last_native_receipt is None


def test_native_codex_probe_rejection_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "probe-reject", monkeypatch)
    request, checkpoint, feedback = prepared(f)

    probe = AuthenticCodexProbe(reject=True)
    observed_results: list[Any] = []
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        res = adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        )
        observed_results.append(res)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    assert len(observed_results) == 1
    assert observed_results[0].data["status"] == "BLOCKED"
    assert observed_results[0].data["reason"] == "CALLBACK_CONFIRMATION_NOT_ESTABLISHED"
    assert observed_results[0].data["stop"]["status"] == "STOP"
    assert adapter.last_native_receipt is None


def test_native_codex_probe_interruption_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "probe-interrupt", monkeypatch)
    request, checkpoint, feedback = prepared(f)

    probe = AuthenticCodexProbe(interrupt=True)
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        with pytest.raises(KeyboardInterrupt):
            adapter.consume_same_executor_feedback(
                request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
            )
        second = adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        )
        assert second.data["status"] == "BLOCKED"
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)
    assert adapter.last_native_receipt is None


def test_native_codex_feedback_secret_and_raw_log_exclusion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "secret-exclusion", monkeypatch)
    request, checkpoint, feedback = prepared(f)
    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    observed_results: list[Any] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        res = adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        )
        observed_results.append(res)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)
    receipt = adapter.last_native_receipt
    assert receipt is not None

    receipt_dict = asdict(receipt)
    receipt_str = json.dumps(receipt_dict)
    assert "secret" not in receipt_str.lower()
    assert "token" not in receipt_str.lower()
    assert "traceback" not in receipt_str.lower()
    assert "/" not in receipt.correlation_digest
    assert "\\" not in receipt.correlation_digest
    assert len(receipt.correlation_digest) == 64


def test_native_codex_ka01_c2_remains_not_activated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "ka01-c2-not-activated", monkeypatch)
    request, checkpoint, feedback = prepared(f)
    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    observed_results: list[Any] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        res = adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        )
        observed_results.append(res)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)
    consumption = observed_results[0]

    assert consumption.data["stop"]["status"] == "STOP"
    assert consumption.data["stop"]["c2_admission"] == "NOT_AUTHORIZED"
    assert consumption.data["stop"]["human_opt_in"] == "NOT_ESTABLISHED"
    assert consumption.data["stop"]["token_budget"] == "NOT_ESTABLISHED"


def test_native_codex_legacy_one_shot_preserves_behavior(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "legacy-one-shot", monkeypatch)
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    calls: list[Any] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = CodexAdapter(runner=runner)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
    )
    pkg = dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)
    assert pkg is not None
    assert len(calls) == 1
    assert calls[0][0] == "codex"
    assert calls[0][1] == "exec"
    assert adapter.has_native_probe is False
    assert adapter.last_native_receipt is None


def test_native_codex_roadmap_state_selects_task418_r1_sole_next() -> None:
    from aios_renew.brain_sync import _extract_next_candidates

    roadmap_path = Path(__file__).resolve().parents[1] / ".ai" / "roadmap-state.yaml"
    roadmap_data = yaml.safe_load(roadmap_path.read_bytes())

    priority_record = roadmap_data.get("human_lean_vp_l3_06_task418_priority_20261025")
    assert priority_record is not None
    assert priority_record["authority"] == "EXPLICIT_HUMAN_DECISION_AND_BRAIN_PROSPECTIVE_SELECTION"
    assert priority_record["planning_only"] is True
    assert priority_record["implementation_task"] == "TASK-418"
    assert priority_record["implementation_revision"] == 1
    assert priority_record["selected_slice"] == "L3_06_NATIVE_CODEX_FEEDBACK_FOUNDATION"
    assert priority_record["sole_high_level_next"] == "lean-vp-intra-v13-budget-defaults-successor"

    assert priority_record["former_next"]["task_id"] == "TASK-417"
    assert priority_record["caa_pilot"]["status"] == "PRESERVED_ACTIVE_PILOT_POLICY"
    assert priority_record["caa_pilot"]["task418_cohort_eligibility"] == "ELIGIBLE_UNDER_EXISTING_EXACT_POLICY"
    assert priority_record["task416"] == "PTE_INFORMATION_ONLY_POLICY_RETAINED_NOT_A_SECOND_NEXT"
    assert priority_record["ka01_activation"] == "NOT_ACTIVATED"

    candidates = _extract_next_candidates(roadmap_data)
    assert len(candidates) == 1
    assert candidates[0]["task_id"] == "TASK-418"
    assert candidates[0]["task_revision"] == 1
    assert candidates[0]["id"] == "lean-vp-intra-v13-budget-defaults-successor"

    seq_next = [item for item in roadmap_data["sequence"] if item.get("status") == "NEXT"]
    assert len(seq_next) == 1
    assert seq_next[0]["task_id"] == "TASK-418"
    assert seq_next[0]["task_revision"] == 1
