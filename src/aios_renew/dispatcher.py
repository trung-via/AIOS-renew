"""Thin deterministic dispatch boundary for admitted native execution."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from .antigravity_adapter import AntigravityAdapter, AntigravityExecutionError
from .artifacts import ResultPackage
from .codex_adapter import (
    RESULT_PACKAGE_SCHEMA_PATH,
    CodexAdapter,
    CodexExecutionError,
    native_execution_context,
    native_executor_instruction,
)
from .executor import ExecutorBoundary
from .review import RemediationExecution
from .run import Run, RunLease, RunLeaseRegistry
from .task import Task


NativeRunner = Callable[..., subprocess.CompletedProcess[bytes]]
Operation = Literal["PRIMARY", "REMEDIATION", "REPAIR"]
NATIVE_RESPONSE_BUDGET_MINUTES = 15
NATIVE_PROCESS_WATCHDOG_SECONDS = NATIVE_RESPONSE_BUDGET_MINUTES * 60


class DispatcherError(RuntimeError):
    """Raised when an admitted execution cannot be dispatched as bound."""


class NativeAdapter(Protocol):
    """Executor-specific mechanics exposed to the Dispatcher."""

    executor: str

    def execute(self, *, task: Task, run: Run) -> ResultPackage: ...

    def execute_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage: ...

    def execute_repair(
        self, *, execution: Mapping[str, Any]
    ) -> ResultPackage: ...


AdapterFactory = Callable[[], NativeAdapter]


@dataclass(frozen=True)
class NativeExecutionCapability:
    """Executor-native capability derived from admitted mutation authority."""

    authorizes_mutation: bool
    codex_sandbox: str
    antigravity_mode: str
    antigravity_skip_permissions: bool


def resolve_native_execution_capability(
    *, authorizes_mutation: bool
) -> NativeExecutionCapability:
    """Resolve one fail-closed native profile before dispatch."""

    if authorizes_mutation:
        return NativeExecutionCapability(
            authorizes_mutation=True,
            codex_sandbox="danger-full-access",
            antigravity_mode="accept-edits",
            antigravity_skip_permissions=True,
        )
    return NativeExecutionCapability(
        authorizes_mutation=False,
        codex_sandbox="read-only",
        antigravity_mode="plan",
        antigravity_skip_permissions=False,
    )


class Dispatcher:
    """Resolve and invoke exactly one already-selected native Executor."""

    def __init__(
        self,
        *,
        selected_executor: str,
        operation: Operation,
        adapter_factories: Mapping[str, AdapterFactory],
    ) -> None:
        try:
            self._adapter_factory = adapter_factories[selected_executor]
        except KeyError as exc:
            raise DispatcherError(
                f"unsupported selected executor: {selected_executor}"
            ) from exc
        self._selected_executor = selected_executor
        self._operation = operation
        self._invoked = False

    def dispatch_primary(
        self,
        *,
        task: Task,
        run: Run,
        lease: RunLease,
        leases: RunLeaseRegistry,
    ) -> ResultPackage:
        """Invoke the selected PRIMARY adapter through the frozen lease boundary."""

        adapter = self._claim_adapter(run=run, operation="PRIMARY")
        return ExecutorBoundary(leases).invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=adapter,
        )

    def dispatch_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage:
        """Invoke the selected adapter for one admitted REMEDIATION."""

        adapter = self._claim_adapter(
            run=execution.run, operation="REMEDIATION"
        )
        return self._require_package(
            adapter.execute_remediation(execution=execution)
        )

    def dispatch_repair(
        self, *, execution: Mapping[str, Any]
    ) -> ResultPackage:
        """Invoke the selected adapter for one admitted REPAIR continuation."""

        run = execution.get("run")
        if not isinstance(run, Run):
            raise DispatcherError("REPAIR execution has no bound RUN")
        adapter = self._claim_adapter(run=run, operation="REPAIR")
        return self._require_package(adapter.execute_repair(execution=execution))

    def _claim_adapter(
        self, *, run: Run, operation: Operation
    ) -> NativeAdapter:
        if operation != self._operation:
            raise DispatcherError(
                f"Dispatcher is bound to {self._operation}, not {operation}"
            )
        if run.executor != self._selected_executor:
            raise DispatcherError(
                f"selected executor {self._selected_executor!r} does not match "
                f"RUN executor {run.executor!r}"
            )
        if self._invoked:
            raise DispatcherError("admitted execution was already dispatched")

        # Claim the one invocation before resolving native mechanics. Any native
        # construction or execution failure is terminal to this Dispatcher.
        self._invoked = True
        adapter = self._adapter_factory()
        if adapter.executor != self._selected_executor:
            raise DispatcherError(
                f"adapter {adapter.executor!r} does not match selected executor "
                f"{self._selected_executor!r}"
            )
        return adapter

    @staticmethod
    def _require_package(package: ResultPackage) -> ResultPackage:
        if not isinstance(package, ResultPackage):
            raise DispatcherError(
                "native Executor must return a structural ResultPackage"
            )
        return package


def primary_dispatcher(
    *,
    selected_executor: str,
    repo: Path,
    handoff_path: Path,
    capability: NativeExecutionCapability,
    native_runner: NativeRunner,
    task: Task,
    run: Run,
) -> Dispatcher:
    """Bind native mechanics for one admitted PRIMARY execution."""

    task_data = asdict(task)
    task_data.pop("verification")
    handoff = {
        "execution_context": native_execution_context(
            run=run, operation="PRIMARY"
        ),
        "task": task_data,
        "run": asdict(run),
    }
    return _native_dispatcher(
        selected_executor=selected_executor,
        operation="PRIMARY",
        repo=repo,
        handoff_path=handoff_path,
        handoff=handoff,
        capability=capability,
        native_runner=native_runner,
    )


def remediation_dispatcher(
    *,
    selected_executor: str,
    repo: Path,
    handoff_path: Path,
    capability: NativeExecutionCapability,
    native_runner: NativeRunner,
    execution: RemediationExecution,
) -> Dispatcher:
    """Bind native mechanics for one admitted REMEDIATION execution."""

    execution_data = asdict(execution)
    execution_data["remediation"].pop("affected_verification")
    handoff = {
        "execution_context": native_execution_context(
            run=execution.run, operation="REMEDIATION"
        ),
        "remediation_execution": execution_data,
    }
    return _native_dispatcher(
        selected_executor=selected_executor,
        operation="REMEDIATION",
        repo=repo,
        handoff_path=handoff_path,
        handoff=handoff,
        capability=capability,
        native_runner=native_runner,
    )


def repair_dispatcher(
    *,
    selected_executor: str,
    repo: Path,
    handoff_path: Path,
    capability: NativeExecutionCapability,
    native_runner: NativeRunner,
    execution: Mapping[str, Any],
) -> Dispatcher:
    """Bind native mechanics for one admitted REPAIR execution."""

    run = execution.get("run")
    if not isinstance(run, Run):
        raise DispatcherError("REPAIR execution has no bound RUN")
    handoff = dict(execution)
    handoff["run"] = asdict(run)
    handoff["execution_context"] = native_execution_context(
        run=run, operation="REPAIR"
    )
    return _native_dispatcher(
        selected_executor=selected_executor,
        operation="REPAIR",
        repo=repo,
        handoff_path=handoff_path,
        handoff=handoff,
        capability=capability,
        native_runner=native_runner,
    )


def _native_dispatcher(
    *,
    selected_executor: str,
    operation: Operation,
    repo: Path,
    handoff_path: Path,
    handoff: Mapping[str, Any],
    capability: NativeExecutionCapability,
    native_runner: NativeRunner,
) -> Dispatcher:
    def codex_factory() -> NativeAdapter:
        return CodexAdapter(runner=_codex_runner(native_runner, capability))

    def antigravity_factory() -> NativeAdapter:
        _write_json(handoff_path, handoff)
        return AntigravityAdapter(
            transport=_antigravity_transport(
                operation=operation,
                repo=repo,
                handoff_path=handoff_path,
                capability=capability,
                native_runner=native_runner,
            ),
            structural_output=True,
        )

    return Dispatcher(
        selected_executor=selected_executor,
        operation=operation,
        adapter_factories={
            "codex": codex_factory,
            "antigravity": antigravity_factory,
        },
    )


def _codex_runner(
    native_runner: NativeRunner, capability: NativeExecutionCapability
) -> NativeRunner:
    def run(
        command: tuple[str, ...], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        updated = list(command)
        try:
            index = updated.index("--sandbox")
        except ValueError as exc:
            raise DispatcherError("Codex command has no sandbox option") from exc
        updated[index + 1] = capability.codex_sandbox
        try:
            return native_runner(
                tuple(updated),
                timeout=NATIVE_PROCESS_WATCHDOG_SECONDS,
                **kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexExecutionError(
                "Codex CLI exceeded the 15-minute native response deadline",
                exit_code=None,
            ) from exc

    return run


def _antigravity_command(
    repo: Path,
    instruction: str,
    capability: NativeExecutionCapability,
) -> tuple[str, ...]:
    command = [
        "agy",
        "--print",
        instruction,
        "--add-dir",
        str(repo),
        "--effort",
        "low",
        "--mode",
        capability.antigravity_mode,
        "--disable-slash-commands",
        "--output-format",
        "json",
        "--json-schema",
        str(RESULT_PACKAGE_SCHEMA_PATH),
        "--print-timeout",
        f"{NATIVE_RESPONSE_BUDGET_MINUTES}m",
    ]
    if capability.antigravity_skip_permissions:
        command.append("--dangerously-skip-permissions")
    return tuple(command)


def _antigravity_transport(
    *,
    operation: Operation,
    repo: Path,
    handoff_path: Path,
    capability: NativeExecutionCapability,
    native_runner: NativeRunner,
) -> Callable[..., str]:
    instruction = _antigravity_instruction(
        operation=operation, handoff_path=handoff_path
    )

    def transport(**execution: Any) -> str:
        del execution
        try:
            completed = native_runner(
                _antigravity_command(repo, instruction, capability),
                cwd=str(repo),
                capture_output=True,
                text=False,
                check=False,
                timeout=NATIVE_PROCESS_WATCHDOG_SECONDS,
            )
            stdout = _decode_utf8(completed.stdout)
            stderr = _decode_utf8(completed.stderr)
        except FileNotFoundError as exc:
            raise AntigravityExecutionError(
                "Antigravity CLI not found: agy"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AntigravityExecutionError(
                "Antigravity CLI exceeded the 15-minute native response deadline"
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise AntigravityExecutionError(
                f"Antigravity CLI invocation failed: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = stderr.strip() or stdout.strip()
            message = f"Antigravity CLI returned nonzero ({completed.returncode})"
            if detail:
                message = f"{message}: {detail}"
            raise AntigravityExecutionError(message)
        return _antigravity_structured_output(stdout, stderr=stderr)

    return transport


def _antigravity_instruction(
    *, operation: Operation, handoff_path: Path
) -> str:
    prefix = native_executor_instruction()
    if operation == "PRIMARY":
        return (
            prefix
            + f"Read the AIOS handoff JSON at {handoff_path}. "
            "Execute its TASK implementation context and RUN exactly within the supplied "
            "repository. Runtime owns canonical verification; do not execute canonical "
            "verification commands and do not generate verification evidence. Minimum "
            "implementation-local sanity checks on the changed surface are permitted when "
            "useful, but they are not canonical verification or EVIDENCE. Commit the final "
            "implementation state when required; do not push. Obtain final Git HEAD, and "
            "return the structural ResultPackage as the only response. Runtime captures and "
            "persists this response; do not write Runtime-owned operational state. The "
            "ResultPackage must be an object with result and evidence. result must contain "
            "head_sha, claims, changed_files, and unresolved. Each claim must contain id, "
            "satisfies, claim, and evidence. Each evidence entry must contain evidence_id, "
            "run_id, subject_sha, type, source.command, result.exit_code, result.summary, "
            "and raw.path when present. Root evidence and every claim.evidence must be empty; "
            "Runtime constructs canonical EVIDENCE. Every claim.satisfies entry must be a "
            "known TASK acceptance ID."
        )
    if operation == "REMEDIATION":
        return (
            prefix
            + f"Read the AIOS remediation handoff JSON at {handoff_path}. Execute exactly "
            "its one remediation_execution contract. Do not run or restart the original "
            "TASK, scan for a different repository, perform semantic review or repeat "
            "unaffected verification. Change only paths in remediation.modification_scope. "
            "For CODE_FIX, commit the permitted remediation delta before returning; for "
            "EVIDENCE_ONLY, do not create a code commit. Do not push. Runtime owns affected "
            "verification; do not execute verification commands and do not generate "
            "verification evidence. Minimum implementation-local sanity checks on the "
            "changed surface are permitted when useful, but they are not canonical "
            "verification or EVIDENCE. Return one structural ResultPackage as the only "
            "response with empty root evidence, result.claims, and result.unresolved. Bind "
            "result.head_sha to final Git HEAD. Runtime captures and persists the response; "
            "do not write Runtime-owned operational state."
        )
    return (
        prefix
        + f"Read the AIOS REPAIR handoff JSON at {handoff_path}. Execute exactly its single "
        "bound continuation. Do not restart PRIMARY discovery, synchronize, retry, review, "
        "or widen the original TASK. Change only repair.modification_scope. For CODE_FIX "
        "commit the final permitted state; for NO_CHANGE do not mutate it. Do not push. "
        "Runtime owns complete original TASK verification; do not execute canonical "
        "verification commands or construct EVIDENCE. Return one structural ResultPackage "
        "for the complete original TASK delta as the only response, with empty root "
        "evidence and every claim.evidence empty. Runtime captures and persists the "
        "response; do not write Runtime-owned operational state."
    )


def _antigravity_structured_output(
    stdout: str, *, stderr: str
) -> Mapping[str, Any]:
    """Extract one schema-constrained payload from the native JSON envelope."""

    if not stdout.strip():
        detail = stderr.strip()
        raise AntigravityExecutionError(
            "Antigravity ResultPackage missing" + (f": {detail}" if detail else "")
        )
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = stderr.strip()
        message = f"Antigravity CLI returned malformed terminal JSON: {exc}"
        if detail:
            message = f"{message}: {detail}"
        raise AntigravityExecutionError(message) from exc
    if not isinstance(envelope, Mapping):
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "response envelope must be a mapping"
        )

    status = envelope.get("status")
    if not isinstance(status, str) or not status:
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "status must be a non-empty string"
        )
    response = envelope.get("response")
    if response is not None and not isinstance(response, str):
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "response must be a string"
        )
    error = envelope.get("error")
    if error is not None and not isinstance(error, str):
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "error must be a string"
        )
    if status != "SUCCESS":
        detail = (error or "").strip() or stderr.strip() or (response or "").strip()
        message = f"Antigravity CLI terminal status is {status}"
        if detail:
            message = f"{message}: {detail}"
        raise AntigravityExecutionError(message)

    if "structured_output" not in envelope:
        detail = stderr.strip() or (error or "").strip() or (response or "").strip()
        message = "Antigravity ResultPackage missing"
        if detail:
            message = f"{message}: {detail}"
        raise AntigravityExecutionError(message)
    payload = envelope["structured_output"]
    if not isinstance(payload, Mapping):
        raise AntigravityExecutionError(
            "Antigravity CLI returned malformed terminal metadata: "
            "structured_output must be a mapping"
        )
    return payload


def _decode_utf8(value: bytes | str) -> str:
    return value.decode("utf-8", errors="strict") if isinstance(value, bytes) else value


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
