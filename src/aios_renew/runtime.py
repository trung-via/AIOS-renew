"""Deterministic completion boundary for already-admitted AIOS work."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from .antigravity_adapter import AntigravityExecutionError
from .artifacts import (
    ArtifactValidationError,
    ResultPackage,
    validate_evidence,
    validate_result_package,
    validate_structural_result,
    validate_structural_result_package,
)
from .codex_adapter import CodexExecutionError
from .review import RemediationExecution
from .review_transport import ReviewTransportError, transport_failure, transport_post_pass
from .run import Run
from .run_observation import RunObservationTracker, persist_observation
from .task import Task
from .verification import (
    RuntimeVerificationError,
    VerificationRunner,
    attach_verification_evidence,
    execute_verification,
)


class RuntimeState(Protocol):
    """Filesystem locations admitted and allocated by the operator."""

    staging: Path
    verification: Path
    results: Path
    failures: Path
    observations: Path
    repairs: Path


BoundaryError = type[Exception]
CompletionKind = Literal["PRIMARY", "REMEDIATION", "REPAIR", "DIRECT_CANDIDATE"]
NATIVE_DIAGNOSTIC_LIMIT = 4096


@dataclass(frozen=True)
class CompletionPolicy:
    """Authorized deterministic rules for one already-admitted completion."""

    kind: CompletionKind
    verification_commands: tuple[str, ...]
    result_base_sha: str
    result_scope: tuple[str, ...]
    require_task_completion: bool
    remediation_execution: RemediationExecution | None = None
    mutation_base_sha: str | None = None
    mutation_scope: tuple[str, ...] = ()
    mutation_action: str | None = None
    lineage_path: Path | None = None
    stage_structural_package: bool = True


@dataclass(frozen=True)
class CompletionOutcome:
    """Canonical state produced before returning to a Human-facing summary."""

    head_sha: str
    result_path: Path
    observation_path: Path | None


def primary_completion_policy(task: Task, *, base_sha: str) -> CompletionPolicy:
    return CompletionPolicy(
        kind="PRIMARY",
        verification_commands=task.verification.required,
        result_base_sha=base_sha,
        result_scope=task.scope.modify,
        require_task_completion=True,
    )


def remediation_completion_policy(
    execution: RemediationExecution, *, direct_candidate: bool = False
) -> CompletionPolicy:
    remediation = execution.remediation
    return CompletionPolicy(
        kind="DIRECT_CANDIDATE" if direct_candidate else "REMEDIATION",
        verification_commands=remediation.affected_verification,
        result_base_sha=remediation.reviewed_sha,
        result_scope=remediation.modification_scope,
        require_task_completion=False,
        remediation_execution=execution,
        mutation_action=remediation.action,
        stage_structural_package=not direct_candidate,
    )


def repair_completion_policy(
    task: Task,
    *,
    root_base_sha: str,
    failed_head_sha: str,
    action: str,
    modification_scope: Sequence[str],
    lineage_path: Path,
) -> CompletionPolicy:
    return CompletionPolicy(
        kind="REPAIR",
        verification_commands=task.verification.required,
        result_base_sha=root_base_sha,
        result_scope=task.scope.modify,
        require_task_completion=True,
        mutation_base_sha=failed_head_sha,
        mutation_scope=tuple(modification_scope),
        mutation_action=action,
        lineage_path=lineage_path,
    )


class RuntimeCompletion:
    """Own canonical completion after dispatch or direct-candidate admission.

    This boundary observes repository truth, applies deterministic completion
    gates, runs the authorized command list once, binds canonical EVIDENCE,
    persists terminal state, records subordinate observation, and transports the
    immutable terminal artifacts. It has no dispatch, review, or recovery policy.
    """

    def __init__(
        self,
        *,
        repo: Path,
        state: RuntimeState,
        task: Task,
        run: Run,
        run_path: Path,
        verification_runner: VerificationRunner,
        observation_tracker: RunObservationTracker | None,
        error_type: BoundaryError,
    ) -> None:
        self.repo = repo
        self.state = state
        self.task = task
        self.run = run
        self.run_path = run_path
        self.verification_runner = verification_runner
        self.observation_tracker = observation_tracker
        self.error_type = error_type
        self.interruption_phase = "COMPLETION_GATE"

    def complete(
        self, package: ResultPackage, policy: CompletionPolicy
    ) -> CompletionOutcome:
        """Produce one canonical RESULT or raise before/after terminal persistence."""

        result_path = self.state.results / f"{self.run.run_id}.json"
        if policy.stage_structural_package:
            _write_json(
                self.state.staging / f"{self.run.run_id}.json",
                result_package_data(package),
            )
        self._require_executor_structure(package)

        actual_head = self._git("rev-parse", "HEAD")
        if package.result.head_sha != actual_head:
            self._raise("RESULT.head_sha mismatch")
        dirty_message = (
            "working tree dirty after REPAIR"
            if policy.kind == "REPAIR"
            else "working tree dirty after execution"
        )
        if self._git("status", "--porcelain"):
            self._raise(dirty_message)

        if policy.kind == "REPAIR":
            package = self._canonicalize_repair_changed_files(
                package, policy, actual_head=actual_head
            )

        if policy.kind in ("PRIMARY", "REPAIR"):
            self._require_task_result(package, policy, actual_head=actual_head)
        else:
            self._require_remediation_result(package, policy, actual_head=actual_head)

        self.interruption_phase = "VERIFICATION"
        verification_started = (
            None
            if self.observation_tracker is None
            else self.observation_tracker.begin_verification()
        )
        try:
            runtime_evidence = execute_verification(
                policy.verification_commands,
                run_id=self.run.run_id,
                subject_sha=actual_head,
                repository=self.repo,
                raw_directory=self.state.verification / self.run.run_id,
                runner=self.verification_runner,
            )
        except RuntimeVerificationError as exc:
            self._raise(str(exc), cause=exc)
        finally:
            if self.observation_tracker is not None:
                self.observation_tracker.end_verification(verification_started)
        self.interruption_phase = "COMPLETION_GATE"

        self._require_post_verification_state(expected_head=actual_head)
        if policy.remediation_execution is None:
            canonical_result = attach_verification_evidence(
                package.result, runtime_evidence
            )
            try:
                canonical_package = validate_result_package(
                    task=self.task,
                    run=self.run,
                    result=canonical_result,
                    evidence=runtime_evidence,
                )
            except ArtifactValidationError as exc:
                self._raise(f"invalid canonical ResultPackage: {exc}", cause=exc)
        else:
            canonical_package = ResultPackage(
                result=package.result, evidence=runtime_evidence
            )
            self._require_remediation_package_contract(
                policy.remediation_execution,
                canonical_package,
                actual_head=actual_head,
            )

        _write_json(result_path, result_package_data(canonical_package))
        observation_path = persist_terminal_observation(
            self.state, self.observation_tracker, "RESULT"
        )
        try:
            transport_post_pass(
                self.repo,
                run_id=self.run.run_id,
                head_sha=actual_head,
                run_path=self.run_path,
                result_path=result_path,
                lineage_path=policy.lineage_path,
                observation_path=observation_path,
            )
        except ReviewTransportError as exc:
            self._raise(f"review transport failed: {exc}", cause=exc)
        return CompletionOutcome(actual_head, result_path, observation_path)

    def _canonicalize_repair_changed_files(
        self,
        package: ResultPackage,
        policy: CompletionPolicy,
        *,
        actual_head: str,
    ) -> ResultPackage:
        """Replace the structural REPAIR declaration with complete Git truth."""

        actual_changed = self._committed_changed_files(
            policy.result_base_sha, actual_head
        )
        outside_scope = actual_changed.difference(policy.result_scope)
        if outside_scope:
            self._raise(
                "committed changed paths outside TASK.scope.modify: "
                + ", ".join(sorted(outside_scope))
            )
        canonical_result = replace(
            package.result, changed_files=tuple(sorted(actual_changed))
        )
        return replace(package, result=canonical_result)

    def _require_task_result(
        self,
        package: ResultPackage,
        policy: CompletionPolicy,
        *,
        actual_head: str,
    ) -> None:
        result_base = policy.result_base_sha
        if package.result.changed_files and actual_head == result_base:
            self._raise("final Git HEAD did not advance")
        self._require_changed_files(
            package,
            base_sha=result_base,
            actual_head=actual_head,
            scope=policy.result_scope,
            scope_name="TASK.scope.modify",
        )
        if policy.require_task_completion:
            self._require_complete_result(package)

        if policy.kind == "PRIMARY" and self.task.scope.modify:
            if actual_head == self.run.base_sha:
                self._raise("final Git HEAD did not advance")
        if policy.kind == "REPAIR":
            assert policy.mutation_base_sha is not None
            repair_changed = self._committed_changed_files(
                policy.mutation_base_sha, actual_head
            )
            if repair_changed.difference(policy.mutation_scope):
                self._raise(
                    "REPAIR committed paths outside authorized correction scope"
                )
            if policy.mutation_action == "CODE_FIX":
                if actual_head == policy.mutation_base_sha:
                    self._raise("CODE_FIX REPAIR did not advance HEAD")
                if not repair_changed:
                    self._raise(
                        "CODE_FIX REPAIR committed correction delta is empty"
                    )
            elif actual_head != policy.mutation_base_sha:
                self._raise("NO_CHANGE REPAIR changed HEAD")

    def _require_remediation_result(
        self,
        package: ResultPackage,
        policy: CompletionPolicy,
        *,
        actual_head: str,
    ) -> None:
        execution = policy.remediation_execution
        assert execution is not None
        if package.result.claims:
            self._raise("remediation RESULT claims must be empty")
        if package.result.unresolved:
            self._raise("remediation RESULT has unresolved items")
        self._require_changed_files(
            package,
            base_sha=policy.result_base_sha,
            actual_head=actual_head,
            scope=policy.result_scope,
            scope_name="REMEDIATION modification scope",
        )
        actual_changed = self._committed_changed_files(
            policy.result_base_sha, actual_head
        )
        if policy.mutation_action == "EVIDENCE_ONLY":
            if actual_head != policy.result_base_sha or actual_changed:
                self._raise("EVIDENCE_ONLY remediation changed repository HEAD")
        else:
            if actual_head == policy.result_base_sha:
                self._raise("CODE_FIX remediation did not advance HEAD")
            if not actual_changed:
                self._raise("CODE_FIX remediation committed delta is empty")

    def _require_changed_files(
        self,
        package: ResultPackage,
        *,
        base_sha: str,
        actual_head: str,
        scope: Sequence[str],
        scope_name: str,
    ) -> None:
        actual_changed = self._committed_changed_files(base_sha, actual_head)
        if set(package.result.changed_files) != actual_changed:
            self._raise("RESULT.changed_files mismatch")
        outside_scope = actual_changed.difference(scope)
        if outside_scope:
            self._raise(
                f"committed changed paths outside {scope_name}: "
                + ", ".join(sorted(outside_scope))
            )

    def _require_complete_result(self, package: ResultPackage) -> None:
        if package.result.unresolved:
            self._raise("RESULT has unresolved items")
        satisfied = {
            acceptance_id
            for claim in package.result.claims
            for acceptance_id in claim.satisfies
        }
        missing = [item.id for item in self.task.acceptance if item.id not in satisfied]
        if missing:
            self._raise(
                "RESULT does not satisfy acceptance criteria: " + ", ".join(missing)
            )

    def _require_executor_structure(self, package: ResultPackage) -> None:
        if package.evidence:
            self._raise("executor structural output evidence must be empty")
        if any(claim.evidence for claim in package.result.claims):
            self._raise(
                "executor structural claim evidence references must be empty"
            )

    def _require_remediation_package_contract(
        self,
        execution: RemediationExecution,
        package: ResultPackage,
        *,
        actual_head: str,
    ) -> None:
        if package.result.claims:
            self._raise("remediation RESULT claims must be empty")
        if package.result.unresolved:
            self._raise("remediation RESULT has unresolved items")
        evidence_ids: set[str] = set()
        for item in package.evidence:
            if item.evidence_id in evidence_ids:
                self._raise(f"duplicate evidence_id: {item.evidence_id}")
            evidence_ids.add(item.evidence_id)
            if item.run_id != execution.run.run_id:
                self._raise(
                    f"{item.evidence_id} does not reference RUN {execution.run.run_id}"
                )
            if item.subject_sha != actual_head:
                self._raise(
                    f"{item.evidence_id} subject_sha does not match RESULT head_sha"
                )
        for command in execution.remediation.affected_verification:
            matching = [
                item for item in package.evidence if item.source.command == command
            ]
            if not matching:
                self._raise(
                    "missing affected verification evidence for required command: "
                    + command
                )
            if not any(item.result.exit_code == 0 for item in matching):
                self._raise(
                    "affected verification command has no successful evidence: "
                    + command
                )

    def _require_post_verification_state(self, *, expected_head: str) -> None:
        if self._git("rev-parse", "HEAD") != expected_head:
            self._raise("verification changed Git HEAD")
        if self._git("status", "--porcelain"):
            self._raise("verification dirtied working tree")

    def _committed_changed_files(self, base_sha: str, head_sha: str) -> set[str]:
        output = self._git(
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base_sha,
            head_sha,
            strip_stdout=False,
        )
        return {path for path in output.split("\0") if path}

    def _git(self, *args: str, strip_stdout: bool = True) -> str:
        try:
            completed = subprocess.run(
                ("git", "-C", str(self.repo), *args),
                capture_output=True,
                text=False,
                check=False,
            )
            stdout = _decode_utf8(completed.stdout)
            stderr = _decode_utf8(completed.stderr)
        except (OSError, UnicodeError) as exc:
            self._raise(f"Git invocation failed: {exc}", cause=exc)
        if completed.returncode != 0:
            detail = stderr.strip() or stdout.strip()
            self._raise(f"Git command failed: {detail}")
        return stdout.strip() if strip_stdout else stdout

    def _raise(self, message: str, *, cause: Exception | None = None) -> None:
        error = self.error_type(message)
        if cause is None:
            raise error
        raise error from cause


def persist_terminal_observation(
    state: RuntimeState,
    tracker: RunObservationTracker | None,
    terminal_kind: str,
) -> Path | None:
    """Best-effort one immutable sidecar without changing terminal authority."""

    if tracker is None:
        return None
    try:
        observation = tracker.finalize(terminal_kind)
        if observation is None:
            return None
        path = state.observations / f"{observation.run_id}.json"
        persist_observation(path, observation)
        return path
    except Exception:
        return None


def persist_failure(
    root: Path,
    *,
    state: RuntimeState,
    task: Task,
    run: Run,
    run_path: Path,
    failure: BaseException,
    observation_tracker: RunObservationTracker | None = None,
    observation_path: Path | None = None,
    interruption_phase: str | None = None,
    transport: bool = True,
) -> None:
    """Persist one admitted FAILURE and optionally best-effort transport it."""

    try:
        if observation_path is None:
            observation_path = persist_terminal_observation(
                state, observation_tracker, "FAILURE"
            )
        head_sha = _git(root, "rev-parse", "HEAD")
        dirty = bool(_git(root, "status", "--porcelain"))
        descendant = _git_is_ancestor(root, run.base_sha, head_sha)
        changed = (
            _committed_changed_files(root, run.base_sha, head_sha)
            if descendant
            else set()
        )
        outside_scope = changed.difference(task.scope.modify)
        repairable = not dirty and descendant
        transportable = repairable and not outside_scope
        cause = failure.__cause__
        if isinstance(failure, KeyboardInterrupt) and interruption_phase is not None:
            phase = interruption_phase
        elif isinstance(
            cause, (CodexExecutionError, AntigravityExecutionError)
        ) or isinstance(failure, KeyboardInterrupt):
            phase = "EXECUTION"
        elif isinstance(cause, RuntimeVerificationError):
            phase = "VERIFICATION"
        else:
            phase = "COMPLETION_GATE"
        error_message = str(failure)
        if isinstance(cause, (CodexExecutionError, AntigravityExecutionError)):
            error_message = str(cause).splitlines()[0][:512]
        elif isinstance(failure, KeyboardInterrupt):
            error_message = {
                "EXECUTION": "native execution interrupted by Human",
                "VERIFICATION": "Runtime verification interrupted by Human",
                "COMPLETION_GATE": "Runtime completion interrupted by Human",
            }.get(phase, "admitted run interrupted by Human")
        record: dict[str, Any] = {
            "kind": "FAILURE",
            "run_id": run.run_id,
            "task": {"id": run.task.id, "revision": run.task.revision},
            "executor": run.executor,
            "base_sha": run.base_sha,
            "failed_head_sha": head_sha,
            "phase": phase,
            "error": {
                "type": type(cause or failure).__name__,
                "message": error_message,
            },
            "candidate": {
                "transportable": transportable,
                "repairable": repairable,
                "dirty": dirty,
                "descends_from_base": descendant,
                "changed_files": sorted(changed),
                "outside_task_scope": sorted(outside_scope),
            },
        }
        repair_execution = state.repairs / f"{run.run_id}.json"
        if repair_execution.is_file():
            record["continuation_of"] = json.loads(
                repair_execution.read_text(encoding="utf-8")
            ).get("failed_run_id")
        if isinstance(cause, CodexExecutionError):
            record["error"]["exit_code"] = cause.exit_code
        if _is_native_timeout(cause) or isinstance(failure, KeyboardInterrupt):
            diagnostic_source: BaseException = cause or failure
            record["error"]["native_diagnostics"] = {
                "limit_chars": NATIVE_DIAGNOSTIC_LIMIT,
                "stdout": _bounded_native_stream(diagnostic_source, "stdout"),
                "stderr": _bounded_native_stream(diagnostic_source, "stderr"),
            }
        if isinstance(cause, RuntimeVerificationError):
            record["error"]["verification"] = [
                {
                    "command": item.source.command,
                    "exit_code": item.result.exit_code,
                    "summary": item.result.summary,
                }
                for item in cause.evidence
            ]
        executor_unresolved = _read_staged_executor_unresolved(state, task, run)
        if executor_unresolved:
            record["error"]["executor_diagnostics"] = {
                "unresolved": executor_unresolved
            }
        failure_path = state.failures / f"{run.run_id}.json"
        _write_json(failure_path, record)
        if transport:
            try:
                transport_failure(
                    root,
                    run_id=run.run_id,
                    head_sha=head_sha,
                    run_path=run_path,
                    failure_path=failure_path,
                    publish_candidate=transportable,
                    observation_path=observation_path,
                )
            except ReviewTransportError as transport_error:
                _write_json(
                    state.failures / f"{run.run_id}.transport.json",
                    {"run_id": run.run_id, "error": str(transport_error)},
                )
    except Exception:
        # Failure recording is subordinate and never replaces the original failure.
        return


def result_package_data(package: ResultPackage) -> dict[str, Any]:
    return {
        "result": {
            "head_sha": package.result.head_sha,
            "claims": [asdict(claim) for claim in package.result.claims],
            "changed_files": list(package.result.changed_files),
            "unresolved": list(package.result.unresolved),
        },
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "run_id": item.run_id,
                "subject_sha": item.subject_sha,
                "type": item.type,
                "source": asdict(item.source),
                "result": asdict(item.result),
                "raw": {"path": item.raw_path},
            }
            for item in package.evidence
        ],
    }


def _read_staged_executor_unresolved(
    state: RuntimeState, task: Task, run: Run
) -> list[str] | None:
    try:
        payload = json.loads(
            (state.staging / f"{run.run_id}.json").read_text(encoding="utf-8")
        )
        if not isinstance(payload, Mapping):
            return None
        evidence_data = payload["evidence"]
        if not isinstance(evidence_data, list):
            return None
        package = validate_structural_result_package(
            task=task,
            run=run,
            result=validate_structural_result(payload["result"]),
            evidence=tuple(validate_evidence(item) for item in evidence_data),
        )
        return list(package.result.unresolved)
    except Exception:
        return None


def _is_native_timeout(failure: BaseException | None) -> bool:
    """Recognize the adapter's directly chained process timeout."""

    return isinstance(
        failure, (CodexExecutionError, AntigravityExecutionError)
    ) and isinstance(failure.__cause__, subprocess.TimeoutExpired)


def _bounded_native_stream(
    failure: BaseException, stream: str
) -> dict[str, Any]:
    """Capture only one allowlisted native stream with explicit availability."""

    missing = object()
    value: Any = getattr(failure, stream, missing)
    if value is missing and stream == "stdout":
        value = getattr(failure, "output", missing)
    if value is missing and failure.__cause__ is not None:
        return _bounded_native_stream(failure.__cause__, stream)
    if value is missing or value is None:
        return {"availability": "unavailable"}
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8", errors="strict")
        except UnicodeError:
            return {"availability": "unavailable", "reason": "invalid_utf8"}
    elif isinstance(value, str):
        text = value
    else:
        return {"availability": "unavailable", "reason": "unsupported_type"}
    if not text:
        return {"availability": "empty"}
    return {
        "availability": "captured",
        "text": text[:NATIVE_DIAGNOSTIC_LIMIT],
        "truncated": len(text) > NATIVE_DIAGNOSTIC_LIMIT,
    }


def _committed_changed_files(repo: Path, base_sha: str, head_sha: str) -> set[str]:
    output = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        base_sha,
        head_sha,
        strip_stdout=False,
    )
    return {path for path in output.split("\0") if path}


def _git_is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant),
            capture_output=True,
            text=False,
            check=False,
        )
        _decode_utf8(completed.stdout)
        stderr = _decode_utf8(completed.stderr)
    except (OSError, UnicodeError):
        return False
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise RuntimeError(f"Git command failed: {stderr.strip()}")


def _git(repo: Path, *args: str, strip_stdout: bool = True) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=False,
        check=False,
    )
    stdout = _decode_utf8(completed.stdout)
    stderr = _decode_utf8(completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(f"Git command failed: {stderr.strip() or stdout.strip()}")
    return stdout.strip() if strip_stdout else stdout


def _decode_utf8(value: bytes | str) -> str:
    return value.decode("utf-8", errors="strict") if isinstance(value, bytes) else value


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
