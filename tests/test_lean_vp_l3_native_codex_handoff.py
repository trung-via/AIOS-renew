"""Lean VP Intra v1.3 L3-06: native Codex feedback delivery and receipt capability.

The current one-shot CLI has no authenticated live feedback channel. Exercise
that production path's explicit unavailability, rejection of synthetic receipt
claims, Runtime authority/continuity guards and legacy one-shot behavior. Local
fixtures and simulated callback failures never prove live Codex deployment.
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
    LocalFeedbackConfirmation,
    format_feedback_consumption,
    parse_feedback_consumption,
)
from aios_renew.run import RunLeaseRegistry
from tests.test_lean_vp_l3_factual_feedback import FeedbackFixture, PRIVATE_LOG


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


@pytest.mark.parametrize("probe_kind", ["legacy-fixture", "local-echo", "typed-ack", "forged-receipt", "runner-attribute"])
def test_native_codex_feedback_fixture_cannot_establish_authentic_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_kind: str
) -> None:
    f = FeedbackFixture(tmp_path / "probe-success", monkeypatch)
    request, checkpoint, feedback = prepared(f)

    probe_calls: list[Any] = []

    class MethodNamedProbe:
        def confirm_authentic_native_delivery(self, **kwargs: Any) -> Any:
            probe_calls.append(kwargs)
            return {
                "local-echo": LocalFeedbackConfirmation("1" * 64, feedback.digest),
                "typed-ack": CodexRecipientAcknowledgment("1" * 64, feedback.digest),
                "forged-receipt": CodexNativeReceipt(
                    "1" * 64, feedback.digest, f.run.run_id, request.source.checkpoint.ordinal,
                    delivery_mode="AUTHENTIC_NATIVE_RECIPIENT", correlation_digest="2" * 64,
                ),
            }.get(probe_kind, True)

        confirm_native_delivery = confirm_authentic_native_delivery

    probe = AuthenticCodexProbe() if probe_kind == "legacy-fixture" else MethodNamedProbe()
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

    if probe_kind == "runner-attribute":
        runner.native_probe = probe
        adapter = CodexAdapter(runner=runner)
    else:
        adapter = CodexAdapter(runner=runner, native_probe=probe)
    assert adapter.native_probe is probe
    assert adapter.has_native_probe is False
    assert adapter.native_feedback_unavailable_reason == "ONE_SHOT_CODEX_CLI_HAS_NO_AUTHENTICATED_LIVE_FEEDBACK_CHANNEL"

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

    # Method names, local typed echoes and generated digests authenticate no recipient.
    assert consumption.data["status"] == "BLOCKED"
    assert consumption.data["reason"] == "CALLBACK_CONFIRMATION_NOT_ESTABLISHED"
    assert consumption.data["binding"] is None
    assert consumption.data["receipt_id"] is None
    assert consumption.data["stop"]["status"] == "STOP"
    assert consumption.data["stop"]["c2_admission"] == "NOT_AUTHORIZED"

    assert probe_calls == []
    if isinstance(probe, AuthenticCodexProbe):
        assert probe.acknowledged_deliveries == []
    assert adapter.last_native_receipt is None
    assert adapter.receipt_history == ()
    # Use the authoritative request ordinal, never RuntimeCheckpoint.ordinal.
    assert feedback.data["binding"]["checkpoint_ordinal"] == request.source.checkpoint.ordinal
    assert feedback.data["binding"]["checkpoint_digest"] == checkpoint.digest
    assert len(f.calls) == 1

    # Only the bounded unavailable observation round-trips, with no receipt.
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
    observed_results: list[Any] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        for _ in range(2):
            observed_results.append(adapter.consume_same_executor_feedback(
                request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
            ))
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

    assert [r.data["status"] for r in observed_results] == ["BLOCKED", "BLOCKED"]
    assert observed_results[0].data["reason"] == "CALLBACK_CONFIRMATION_NOT_ESTABLISHED"
    assert observed_results[1].data["reason"] == "STOP_LATCHED"
    assert all(r.data["receipt_id"] is None for r in observed_results)
    assert probe.acknowledged_deliveries == []
    assert adapter.last_native_receipt is None
    assert adapter.receipt_history == ()
    assert len(f.calls) == 1


def test_native_codex_feedback_rejected_lease_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "lease-loss", monkeypatch)
    request, checkpoint, feedback = prepared(f)
    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    observed_results: list[Any] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        leases.release(lease)
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


@pytest.mark.parametrize("defect", ["feedback-digest", "checkpoint-digest", "profile", "lease-holder"])
def test_native_codex_runtime_rejects_changed_source_before_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    f = FeedbackFixture(tmp_path / "changed-native-source", monkeypatch)
    request, _, _ = prepared(f)
    profile = ResolvedExecutionProfile(
        f.run.run_id, "codex", "explicit-test-model", "high", "EXPLICIT", "EXPLICIT"
    )
    probe = AuthenticCodexProbe()
    leases = RunLeaseRegistry()
    lease = leases.acquire(f.run)
    observed_results: list[Any] = []

    def runner(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        current_request = request
        current_lease = lease
        if defect == "feedback-digest":
            current_request = replace(request, feedback_digest="1" * 64)
        elif defect == "checkpoint-digest":
            current_request = replace(request, source=replace(request.source, checkpoint_digest="1" * 64))
        elif defect == "profile":
            adapter._execution_profile = replace(profile, model="substituted-model")
        else:
            leases.release(lease)
            current_lease = leases.acquire(f.run)
        observed_results.append(adapter.consume_same_executor_feedback(
            current_request, boundary=dispatcher.active_boundary, lease=current_lease, runtime=f.owner
        ))
        observed_results.append(adapter.consume_same_executor_feedback(
            request, boundary=dispatcher.active_boundary, lease=lease, runtime=f.owner
        ))
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=successful_output(f.run.run_id).encode("utf-8"), stderr=b""
        )

    adapter = CodexAdapter(runner=runner, native_probe=probe, execution_profile=profile)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor, operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        execution_profile=profile, prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)

    first, repeated = observed_results
    assert first.data["status"] == "BLOCKED"
    assert first.data["reason"] == (
        "NATIVE_SOURCE_OR_EFFECT_NOT_ESTABLISHED"
        if defect.endswith("digest") else "LIVE_INVOCATION_NOT_ESTABLISHED"
    )
    assert repeated.data["status"] == "BLOCKED"
    assert repeated.data["reason"] == "STOP_LATCHED"
    assert all(r.data["receipt_id"] is None for r in observed_results)
    assert probe.acknowledged_deliveries == []
    assert adapter.last_native_receipt is None
    assert adapter.receipt_history == ()
    assert len(f.calls) == 1


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

    callback_calls: list[Any] = []

    class InterruptedCallbackAdapter(CodexAdapter):
        """Simulate callback failure only; this supplies no native success proof."""

        def confirm_local_feedback(self, *, feedback: Any, receipt_id: str) -> Any:
            callback_calls.append((feedback, receipt_id))
            raise KeyboardInterrupt("simulated contract callback interruption")

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
        assert second.data["reason"] == "STOP_LATCHED"
        assert second.data["receipt_id"] is None
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(f.run.run_id).encode("utf-8"),
            stderr=b"",
        )

    adapter = InterruptedCallbackAdapter(runner=runner)
    dispatcher = Dispatcher(
        selected_executor=f.run.executor,
        operation="PRIMARY",
        adapter_factories={f.run.executor: lambda: adapter},
        prospective_runtime=f.owner,
    )
    dispatcher.dispatch_primary(task=f.task, run=f.run, lease=lease, leases=leases)
    assert len(callback_calls) == 1
    assert callback_calls[0][0] == feedback
    assert len(f.calls) == 1
    assert adapter.last_native_receipt is None
    assert adapter.receipt_history == ()


def test_native_codex_feedback_secret_and_raw_log_exclusion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FeedbackFixture(tmp_path / "secret-exclusion", monkeypatch)
    request, checkpoint, feedback = prepared(f)
    original_feedback = feedback.content_json
    original_checkpoint = checkpoint.content_json
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
    assert consumption.data["status"] == "BLOCKED"
    assert consumption.data["receipt_id"] is None
    assert adapter.last_native_receipt is None
    assert adapter.receipt_history == ()
    assert probe.acknowledged_deliveries == []
    assert feedback.content_json == original_feedback
    assert checkpoint.content_json == original_checkpoint
    for portable_record in (feedback.content_json, format_feedback_consumption(consumption).decode("ascii")):
        for excluded in (PRIVATE_LOG, "Traceback", str(tmp_path), tmp_path.as_posix()):
            assert excluded not in portable_record


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
