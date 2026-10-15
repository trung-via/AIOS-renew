"""Deterministic completion boundary for already-admitted AIOS work."""

from __future__ import annotations

import json
import hashlib
import math
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from .antigravity_adapter import AntigravityExecutionError
from .antigravity_minimax_adapter import AntigravityMinimaxExecutionError
from .artifacts import (
    ArtifactValidationError,
    Evidence,
    ResultPackage,
    validate_evidence,
    validate_result_package,
    validate_structural_result,
    validate_structural_result_package,
    verification_requirement_passes,
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
    execute_minimum_verification,
    materialize_verification_subject,
)
from .verification_contract import (
    MINIMUM_SUFFICIENT_V2, VERIFICATION_POLICIES,
    normalize_verification, parse_pytest_coverage,
)


class RuntimeState(Protocol):
    """Filesystem locations admitted and allocated by the operator."""

    staging: Path
    preverification: Path
    verification: Path
    results: Path
    failures: Path
    observations: Path
    repairs: Path


BoundaryError = type[Exception]
CompletionKind = Literal["PRIMARY", "REMEDIATION", "REPAIR", "DIRECT_CANDIDATE"]
NATIVE_DIAGNOSTIC_LIMIT = 4096
_NATIVE_EXECUTION_ERRORS = (CodexExecutionError, AntigravityExecutionError, AntigravityMinimaxExecutionError)


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
    result_base_sha: str | None = None,
    failed_head_sha: str,
    action: str,
    modification_scope: Sequence[str],
    lineage_path: Path,
    origin_affected_verification: Sequence[str] = (),
) -> CompletionPolicy:
    verification_commands = task.verification.required
    if origin_affected_verification:
        combined = (*verification_commands, *origin_affected_verification)
        if task.verification.policy == MINIMUM_SUFFICIENT_V2:
            verification_commands = tuple(dict.fromkeys((*origin_affected_verification, *verification_commands)))
        elif task.verification.policy in VERIFICATION_POLICIES:
            verification_commands = normalize_verification(combined)
        else:
            verification_commands = tuple(dict.fromkeys(combined))
    if task.verification.policy == MINIMUM_SUFFICIENT_V2:
        # Normalization cannot remove an exact authored integration guard.
        verification_commands = tuple(dict.fromkeys((*verification_commands, *(
            c for c in task.verification.required
            if (coverage := parse_pytest_coverage(c)) is not None and coverage.is_full_suite
        ))))
    return CompletionPolicy(
        kind="REPAIR",
        verification_commands=verification_commands,
        result_base_sha=(
            root_base_sha if result_base_sha is None else result_base_sha
        ),
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
        transport_repo: Path | None = None,
    ) -> None:
        self.repo = repo
        self.state = state
        self.task = task
        self.run = run
        self.run_path = run_path
        self.verification_runner = verification_runner
        self.observation_tracker = observation_tracker
        self.error_type = error_type
        self.transport_repo = transport_repo or repo
        self.interruption_phase = "COMPLETION_GATE"
        self.verification_subject_sha: str | None = None

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

        try:
            persist_preverification_candidate(
                self.state.preverification / f"{self.run.run_id}.json",
                task=self.task,
                run=self.run,
                subject_sha=actual_head,
                package=package,
            )
        except (OSError, UnicodeError, ArtifactValidationError) as exc:
            self._raise(
                f"pre-verification candidate persistence failed: {exc}", cause=exc
            )

        self.interruption_phase = "VERIFICATION"
        self.verification_subject_sha = actual_head
        verification_started = (
            None
            if self.observation_tracker is None
            else self.observation_tracker.begin_verification()
        )
        try:
            if self.task.verification.policy == MINIMUM_SUFFICIENT_V2:
                commands = policy.verification_commands
                if policy.remediation_execution is not None:
                    commands = tuple(dict.fromkeys((*commands, *(
                        c for c in self.task.verification.required
                        if (coverage := parse_pytest_coverage(c)) is not None and coverage.is_full_suite
                    ))))
                runtime_evidence = execute_minimum_verification(
                    commands, run_id=self.run.run_id, base_sha=self.run.base_sha,
                    subject_sha=actual_head, repository=self.repo,
                    raw_directory=self.state.verification / self.run.run_id,
                    cache_directory=self.state.verification / MINIMUM_SUFFICIENT_V2,
                    modification_scope=policy.mutation_scope if policy.kind == "REPAIR" else policy.result_scope,
                    operation=policy.kind, runner=self.verification_runner,
                    subject_check=self._require_post_verification_state,
                    correction_base_sha=policy.mutation_base_sha if policy.kind == "REPAIR" else policy.result_base_sha,
                )
            else:
                with materialize_verification_subject(
                    self.repo,
                    run_id=self.run.run_id,
                    subject_sha=actual_head,
                ) as verification_subject:
                    runtime_evidence = execute_verification(
                        policy.verification_commands,
                        run_id=self.run.run_id,
                        subject_sha=actual_head,
                        repository=verification_subject,
                        raw_directory=self.state.verification / self.run.run_id,
                        runner=self.verification_runner,
                    )
                    self._require_post_verification_state(
                        verification_subject,
                        expected_head=actual_head,
                        evidence=runtime_evidence,
                    )
        except RuntimeVerificationError as exc:
            self._raise(str(exc), cause=exc)
        finally:
            if self.observation_tracker is not None:
                self.observation_tracker.end_verification(verification_started)
        self.interruption_phase = "COMPLETION_GATE"
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
                self.transport_repo,
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
            if (
                policy.mutation_action == "CONTINUE_IMPLEMENTATION"
                and not self._is_ancestor(policy.mutation_base_sha, actual_head)
            ):
                self._raise(
                    "CONTINUE_IMPLEMENTATION final HEAD does not descend from failed HEAD"
                )
            repair_changed = self._committed_changed_files(
                policy.mutation_base_sha, actual_head
            )
            if policy.mutation_action in (
                "CODE_FIX", "CONTINUE_IMPLEMENTATION"
            ):
                if repair_changed.difference(policy.mutation_scope):
                    self._raise(
                        "REPAIR committed paths outside authorized correction scope"
                    )
                if actual_head == policy.mutation_base_sha:
                    self._raise(
                        f"{policy.mutation_action} REPAIR did not advance HEAD"
                    )
                if not repair_changed:
                    message = (
                        "CODE_FIX REPAIR committed correction delta is empty"
                        if policy.mutation_action == "CODE_FIX"
                        else "CONTINUE_IMPLEMENTATION REPAIR committed delta is empty"
                    )
                    self._raise(message)
            elif actual_head != policy.mutation_base_sha:
                self._raise(f"{policy.mutation_action} REPAIR changed HEAD")

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
            if (self.task.verification.policy == MINIMUM_SUFFICIENT_V2 and item.verification is not None
                    and item.verification.get("binding", {}).get("base_sha") != self.run.base_sha):
                self._raise("V2 attribution base does not match RUN base_sha")
            if (self.task.verification.policy == MINIMUM_SUFFICIENT_V2 and item.type == "VERIFICATION"
                    and not verification_requirement_passes(item, policy=self.task.verification.policy)):
                self._raise("blocking V2 observation cannot appear in canonical RESULT")
        required = execution.remediation.affected_verification
        if self.task.verification.policy == MINIMUM_SUFFICIENT_V2:
            required = tuple(dict.fromkeys((*required, *(
                c for c in self.task.verification.required
                if (coverage := parse_pytest_coverage(c)) is not None and coverage.is_full_suite
            ))))
        for command in required:
            matching = [
                item for item in package.evidence if item.source.command == command
            ]
            if not matching:
                self._raise(
                    "missing affected verification evidence for required command: "
                    + command
                )
            if not any(verification_requirement_passes(item, policy=self.task.verification.policy) for item in matching):
                self._raise(
                    "affected verification command has no successful evidence: "
                    + command
                )

    def _require_post_verification_state(
        self,
        repository: Path,
        *,
        expected_head: str,
        evidence: Sequence[Evidence] = (),
    ) -> None:
        try:
            if self._git_at(repository, "rev-parse", "HEAD") != expected_head:
                raise RuntimeVerificationError(
                    "verification changed Git HEAD", evidence=evidence
                )
            if self._git_at(repository, "status", "--porcelain"):
                raise RuntimeVerificationError(
                    "verification dirtied working tree", evidence=evidence
                )
        except RuntimeVerificationError:
            raise
        except self.error_type as exc:
            raise RuntimeVerificationError(str(exc), evidence=evidence) from exc

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

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        try:
            return _git_is_ancestor(self.repo, ancestor, descendant)
        except RuntimeError as exc:
            self._raise(str(exc), cause=exc)

    def _git(self, *args: str, strip_stdout: bool = True) -> str:
        return self._git_at(self.repo, *args, strip_stdout=strip_stdout)

    def _git_at(
        self, repository: Path, *args: str, strip_stdout: bool = True
    ) -> str:
        try:
            completed = subprocess.run(
                ("git", "-C", str(repository), *args),
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
    transport_repo: Path | None = None,
    verification_subject_sha: str | None = None,
) -> None:
    """Persist one admitted FAILURE and optionally best-effort transport it."""

    try:
        if observation_path is None:
            observation_path = persist_terminal_observation(
                state, observation_tracker, "FAILURE"
            )
        cause = failure.__cause__
        if isinstance(failure, KeyboardInterrupt) and interruption_phase is not None:
            phase = interruption_phase
        elif isinstance(
            cause, _NATIVE_EXECUTION_ERRORS
        ) or isinstance(failure, KeyboardInterrupt):
            phase = "EXECUTION"
        elif isinstance(cause, RuntimeVerificationError):
            phase = "VERIFICATION"
        else:
            phase = "COMPLETION_GATE"
        exact_verification_subject = (
            verification_subject_sha
            if phase == "VERIFICATION" and verification_subject_sha is not None
            else None
        )
        head_sha = (
            exact_verification_subject
            if exact_verification_subject is not None
            else _git(root, "rev-parse", "HEAD")
        )
        # A verification failure describes the immutable candidate materialized
        # from PRE_VERIFICATION_CANDIDATE, never a later control checkout HEAD.
        dirty = (
            False
            if exact_verification_subject is not None
            else bool(_git(root, "status", "--porcelain"))
        )
        descendant = _git_is_ancestor(root, run.base_sha, head_sha)
        changed = (
            _committed_changed_files(root, run.base_sha, head_sha)
            if descendant
            else set()
        )
        outside_scope = changed.difference(task.scope.modify)
        repairable = not dirty and descendant
        transportable = repairable and not outside_scope
        error_message = str(failure)
        if isinstance(cause, _NATIVE_EXECUTION_ERRORS):
            lines = str(cause).splitlines()
            error_message = lines[0][:512] if lines else ""
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
        # These are workspace observations, never a committed candidate tree.
        # Keep dirty paths private and publish only typed counts.
        workspace = _failure_workspace_state(root, dirty=dirty, modification_scope=task.scope.modify)
        if workspace is not None:
            record["workspace_state"] = workspace
        repair_execution = state.repairs / f"{run.run_id}.json"
        if repair_execution.is_file():
            record["continuation_of"] = json.loads(
                repair_execution.read_text(encoding="utf-8")
            ).get("failed_run_id")
        if isinstance(cause, CodexExecutionError):
            record["error"]["exit_code"] = cause.exit_code
        if _is_native_timeout(cause):
            record["error"]["native_kind"] = "TIMEOUT"
            record["error"].setdefault("exit_code", None)
            timeout = cause.__cause__.timeout
            if type(timeout) in (int, float) and math.isfinite(timeout) and 0 <= timeout <= 31536000:
                record["error"]["native_timeout_seconds"] = timeout
        if isinstance(
            cause, _NATIVE_EXECUTION_ERRORS
        ) or isinstance(failure, KeyboardInterrupt):
            diagnostic_source: BaseException = cause or failure
            record["error"]["native_diagnostics"] = {
                "limit_chars": NATIVE_DIAGNOSTIC_LIMIT,
                "stdout": _bounded_native_stream(diagnostic_source, "stdout"),
                "stderr": _bounded_native_stream(diagnostic_source, "stderr"),
            }
            record["error"]["native_captures"] = _persist_native_captures(state, run, diagnostic_source)
        if isinstance(cause, RuntimeVerificationError):
            record["error"]["verification"] = [
                {
                    "command": item.source.command,
                    "exit_code": item.result.exit_code,
                    "summary": item.result.summary,
                    **({"verification": item.verification} if item.verification is not None else {}),
                }
                for item in cause.evidence
            ]
            # Direct V1 observations have no V2 reference. Retain their original
            # EVIDENCE identity and locator in the private FAILURE so the same
            # admitted local Executor can read the actual capture.
            direct_evidence = [
                dict(evidence_id=item.evidence_id, run_id=item.run_id,
                     subject_sha=item.subject_sha, type=item.type,
                     source=asdict(item.source), result=asdict(item.result),
                     raw={"path": item.raw_path})
                for item in cause.evidence if item.verification is None
            ]
            if direct_evidence:
                record["error"]["evidence"] = direct_evidence
                digests = {}
                for item in direct_evidence:
                    try:
                        owned = (state.verification / run.run_id).resolve()
                        path = Path(item["raw"]["path"])
                        if not path.is_absolute():
                            path = root / path
                        # A missing/restricted capture cannot erase the FAILURE.
                        if (owned == state.verification.parent.resolve() / state.verification.name / run.run_id
                                and path.resolve().is_relative_to(owned) and path.is_file()):
                            digest = hashlib.sha256()
                            with path.open("rb") as raw:
                                for chunk in iter(lambda: raw.read(1024 * 1024), b""):
                                    digest.update(chunk)
                            digests[item["evidence_id"]] = digest.hexdigest()
                    except (OSError, ValueError, RuntimeError):
                        continue
                if digests:
                    record["error"]["evidence_raw_digests"] = digests
        executor_unresolved = _read_staged_executor_unresolved(state, task, run)
        if executor_unresolved:
            record["error"]["executor_diagnostics"] = {
                "unresolved": executor_unresolved
            }
        failure_path = state.failures / f"{run.run_id}.json"
        content = json.dumps(record, sort_keys=True, indent=2).encode("utf-8")
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with failure_path.open("xb") as stream:
                stream.write(content)
        except FileExistsError:
            if json.loads(failure_path.read_bytes()) != record:
                _write_json(state.failures / f"{run.run_id}.transport.json",
                    dict(run_id=run.run_id, error="conflicting immutable Runtime FAILURE", reason_code="ARTIFACT_IDENTITY_CONFLICT"))
                return
            # A replay transports the existing bytes; it never rewrites them.
        preverification_path = state.preverification / f"{run.run_id}.json"
        reusable_candidate_path = (
            preverification_path
            if phase == "VERIFICATION" and preverification_path.is_file()
            else None
        )
        if transport:
            try:
                transport_failure(
                    transport_repo or root,
                    run_id=run.run_id,
                    head_sha=head_sha,
                    run_path=run_path,
                    failure_path=failure_path,
                    publish_candidate=transportable,
                    lineage_path=(
                        repair_execution if repair_execution.is_file() else None
                    ),
                    observation_path=observation_path,
                    preverification_path=reusable_candidate_path,
                )
            except ReviewTransportError as transport_error:
                _write_json(
                    state.failures / f"{run.run_id}.transport.json",
                    {"run_id": run.run_id, "error": str(transport_error),
                     "reason_code": failure_transport_blocker(transport_error)},
                )
    except Exception:
        # Failure recording is subordinate and never replaces the original failure.
        return


def _failure_workspace_state(root: Path, *, dirty: bool, modification_scope: Sequence[str]) -> dict[str, int] | None:
    """A subordinate status read cannot erase the admitted FAILURE on failure."""
    try:
        status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", strip_stdout=False) if dirty else ""
        entries = status.split("\0")
        dirty_entries: list[tuple[str, ...]] = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            if len(entry) < 4 or entry[2] != " ":
                return None
            paths = (entry[3:],)
            if "R" in entry[:2] or "C" in entry[:2]:
                if index >= len(entries) or not entries[index]:
                    return None
                paths += (entries[index],)
                index += 1
            dirty_entries.append(paths)
        if bool(dirty_entries) is not dirty:
            return None  # A racing status snapshot has unknown counts.
        return dict(dirty_entry_count=len(dirty_entries),
                    outside_task_scope_count=sum(any(path not in modification_scope for path in paths) for paths in dirty_entries))
    except Exception:
        return None


def failure_transport_blocker(error: BaseException) -> str:
    """Typed transport facts without forwarding private exceptions or Git stderr."""
    from .review_transport import FAILURE_TRANSPORT_BLOCKERS, _FAILURE_BLOCKER_MESSAGES
    current: BaseException | None = error
    for _ in range(8):
        reason = getattr(current, "reason_code", None)
        if isinstance(reason, str) and reason in FAILURE_TRANSPORT_BLOCKERS:
            return reason
        current = current.__cause__ if current is not None else None
    text = str(error)
    if text in _FAILURE_BLOCKER_MESSAGES:
        return _FAILURE_BLOCKER_MESSAGES[text]
    if "projection" in text or "public FAILURE" in text:
        return "FAILURE_PROJECTION_REJECTED"
    if "attention" in text:
        return "ATTENTION_DELIVERY_FAILED"
    if "conflict" in text or "different" in text:
        return "ARTIFACT_IDENTITY_CONFLICT"
    if "query" in text or "remote" in text and "unavailable" in text:
        return "REMOTE_QUERY_UNAVAILABLE"
    if "push" in text:
        return "REMOTE_PUBLICATION_FAILED"
    return "FAILURE_TRANSPORT_UNAVAILABLE"


def read_private_failure_diagnostics(
    root: Path, *, state: RuntimeState, task: Task, run: Run, subject_sha: str,
) -> dict[str, Any]:
    """Read original Runtime evidence for an admitted RUN in its local workspace.

    This local-only API returns bytes, including sensitive captured text. It must
    never be serialized into Brain/provider/workflow payloads. The RUN, TASK and
    subject supplied by admission are checked before any diagnostic locator is
    opened. Evidence is data and conveys no execution or repair instructions.
    """
    from .review_transport import failure_document, validate_runtime_failure_binding

    if (run.status != "ACTIVE" or run.task.id != task.task_id or run.task.revision != task.revision
            or type(run.task.revision) is not int or type(task.revision) is not int
            or Path(run.workspace).resolve() != root.resolve()
            or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", run.run_id) is None
            or re.fullmatch(r"[0-9a-f]{40}", subject_sha) is None):
        raise ValueError("private diagnostics require the admitted local RUN workspace")
    from .operator import _runtime_paths_readonly
    authorized = _runtime_paths_readonly(root)
    if any(getattr(state, key).resolve() != getattr(authorized, key).resolve() for key in ("failures", "verification")):
        raise ValueError("private diagnostics require the workspace's Runtime-owned state")
    for directory in (state.failures.parent, state.failures, authorized.runs, state.verification):
        if directory.is_symlink() or directory.resolve() != directory.parent.resolve() / directory.name:
            raise ValueError("private diagnostic source substitution")
    run_path = state.failures.parent / "runs" / f"{run.run_id}.json"
    failure_path = state.failures / f"{run.run_id}.json"
    if (run_path.is_symlink() or failure_path.is_symlink()
            or run_path.resolve().parent != authorized.runs.resolve()
            or failure_path.resolve().parent != authorized.failures.resolve()):
        raise ValueError("private diagnostic source substitution")
    from .review_transport import _public_document
    original_run = _public_document(run_path.read_bytes())
    bound_run = original_run.get("execution", {}).get("run") if original_run.get("kind") == "REMEDIATION" else original_run
    expected_run = asdict(run)
    if "return_affinity" not in bound_run:
        from .return_affinity import document_affinity
        if document_affinity(bound_run) != run.return_affinity:
            raise ValueError("private diagnostic RUN affinity mismatch")
        expected_run.pop("return_affinity", None)
    if json.dumps(bound_run, sort_keys=True) != json.dumps(expected_run, sort_keys=True):
        raise ValueError("private diagnostic RUN admission binding mismatch")
    raw_failure = failure_path.read_bytes()
    failure = failure_document(raw_failure)
    if "public_projection" in failure or failure.get("error", {}).get("message") == "Protected Runtime diagnostics remain local.":
        raise ValueError("original private FAILURE is unavailable")
    validate_runtime_failure_binding(failure, run_id=run.run_id, task_id=task.task_id,
        task_revision=task.revision, executor=run.executor, base_sha=run.base_sha,
        candidate_sha=subject_sha, modification_scope=task.scope.modify)
    from .unified_state import _validated_private_public_view
    _validated_private_public_view(failure, "METADATA")
    records = []
    for outcome in failure.get("error", {}).get("verification", []):
        reference = outcome.get("verification")
        if not isinstance(reference, Mapping):
            continue
        for prefix in ("", "base_"):
            path = reference.get(prefix + "raw_path")
            if path is not None:
                records.append((reference["evidence_id"], prefix or "candidate", path, reference[prefix + "raw_digest"], reference["run_id"]))
    def evidence_records(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                evidence_records(item)
        elif isinstance(value, Mapping):
            if "evidence_id" in value and "raw" in value:
                records.append((value["evidence_id"], "evidence", value["raw"]["path"],
                                failure["error"].get("evidence_raw_digests", {}).get(value["evidence_id"]), run.run_id))
            for item in value.values():
                evidence_records(item)
    evidence_records(failure)
    captures = []
    owned_root = state.verification.resolve()

    def local_capture(path: str, expected_digest: str | None, source_run_id: str) -> dict[str, Any]:
        locator = Path(path)
        if not locator.is_absolute():
            locator = root / locator
        resolved = locator.resolve()
        if not resolved.is_relative_to(owned_root):
            raise ValueError("private evidence locator is outside the exact RUN")
        relative = resolved.relative_to(owned_root)
        if not relative.parts or not (relative.parts[0] == source_run_id
                or len(relative.parts) == 1 and resolved.name.startswith(source_run_id + "-")):
            raise ValueError("private evidence locator is outside the exact RUN")
        if not resolved.is_file():
            return dict(availability="UNAVAILABLE", bytes=None)
        raw = resolved.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_digest is not None and expected_digest != digest:
            raise ValueError("private evidence digest mismatch")
        return dict(availability="AVAILABLE", bytes=raw, sha256=digest,
                    integrity="BOUND_DIGEST" if expected_digest is not None else "RUNTIME_LOCAL_ONLY")

    for identity, role, path, expected_digest, source_run_id in records:
        captures.append(dict(evidence_id=identity, role=role, **local_capture(path, expected_digest, source_run_id)))
    from .review_transport import validate_private_native_captures
    native = failure.get("error", {}).get("native_captures", {})
    validate_private_native_captures(native)
    native_captures = {}
    for stream in ("stdout", "stderr"):
        reference = native.get(stream)
        if reference is None:
            native_captures[stream] = dict(availability="UNKNOWN", bytes=None)
        elif reference["availability"] == "UNAVAILABLE":
            native_captures[stream] = dict(availability="UNAVAILABLE", bytes=None)
        else:
            capture = local_capture(reference["raw_path"], reference["raw_digest"], run.run_id)
            if capture["availability"] == "AVAILABLE" and len(capture["bytes"]) != reference["raw_bytes"]:
                raise ValueError("private native capture byte count mismatch")
            native_captures[stream] = capture
    return dict(run_id=run.run_id, subject_sha=subject_sha, authority="RUNTIME_OBSERVATION_DATA",
                availability="AVAILABLE", failure_bytes=raw_failure,
                failure_sha256=hashlib.sha256(raw_failure).hexdigest(), captures=captures, native_captures=native_captures)


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
                **({"verification": item.verification} if item.verification is not None else {}),
            }
            for item in package.evidence
        ],
    }


def persist_preverification_candidate(
    path: Path,
    *,
    task: Task,
    run: Run,
    subject_sha: str,
    package: ResultPackage,
) -> None:
    """Persist exact subordinate state only after deterministic completion gates."""

    data = {
        "kind": "PRE_VERIFICATION_CANDIDATE",
        "run_id": run.run_id,
        "task": {"id": task.task_id, "revision": task.revision},
        "subject_sha": subject_sha,
        "package": result_package_data(package),
    }
    content = json.dumps(data, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    # Validate the exact representation before making it reusable authority.
    validate_preverification_candidate(
        content,
        task=task,
        run_id=run.run_id,
        subject_sha=subject_sha,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ArtifactValidationError(
                f"conflicting pre-verification candidate for {run.run_id}"
            )


def validate_preverification_candidate(
    content: bytes,
    *,
    task: Task,
    run_id: str,
    subject_sha: str,
) -> ResultPackage:
    """Validate one exact Runtime-owned structural candidate for deterministic reuse."""

    try:
        data = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(
            f"invalid pre-verification candidate JSON: {exc}"
        ) from exc
    if not isinstance(data, Mapping) or set(data) != {
        "kind",
        "run_id",
        "task",
        "subject_sha",
        "package",
    }:
        raise ArtifactValidationError(
            "pre-verification candidate fields do not match the contract"
        )
    if data.get("kind") != "PRE_VERIFICATION_CANDIDATE":
        raise ArtifactValidationError("invalid pre-verification candidate kind")
    if data.get("run_id") != run_id or data.get("subject_sha") != subject_sha:
        raise ArtifactValidationError(
            "pre-verification candidate RUN or subject mismatch"
        )
    task_ref = data.get("task")
    expected_task = {"id": task.task_id, "revision": task.revision}
    if not isinstance(task_ref, Mapping) or dict(task_ref) != expected_task:
        raise ArtifactValidationError("pre-verification candidate TASK mismatch")
    payload = data.get("package")
    if not isinstance(payload, Mapping) or set(payload) != {"result", "evidence"}:
        raise ArtifactValidationError(
            "pre-verification candidate package fields do not match the contract"
        )
    result_data = payload.get("result")
    if not isinstance(result_data, Mapping) or set(result_data) != {
        "head_sha",
        "claims",
        "changed_files",
        "unresolved",
    }:
        raise ArtifactValidationError(
            "pre-verification candidate RESULT fields do not match the contract"
        )
    claims_data = result_data.get("claims")
    if not isinstance(claims_data, list) or any(
        not isinstance(claim, Mapping)
        or set(claim) != {"id", "satisfies", "claim", "evidence"}
        for claim in claims_data
    ):
        raise ArtifactValidationError(
            "pre-verification candidate claim fields do not match the contract"
        )
    evidence_data = payload.get("evidence")
    if not isinstance(evidence_data, list):
        raise ArtifactValidationError(
            "pre-verification candidate evidence must be a list"
        )
    run = Run.from_task(
        run_id=run_id,
        task=task,
        executor="codex",
        base_sha=subject_sha,
        workspace="pre-verification-candidate",
    )
    package = validate_structural_result_package(
        task=task,
        run=run,
        result=validate_structural_result(result_data),
        evidence=tuple(validate_evidence(item) for item in evidence_data),
    )
    if package.result.head_sha != subject_sha:
        raise ArtifactValidationError(
            "pre-verification candidate RESULT head mismatch"
        )
    if package.evidence or any(claim.evidence for claim in package.result.claims):
        raise ArtifactValidationError(
            "pre-verification candidate contains semantic evidence"
        )
    return package


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
        failure, _NATIVE_EXECUTION_ERRORS
    ) and isinstance(failure.__cause__, subprocess.TimeoutExpired)


def _persist_native_captures(state: RuntimeState, run: Run, failure: BaseException) -> dict[str, Any]:
    """Keep original capture bytes in existing private RUN verification storage.

    Content-addressed files are immutable and never enter terminal transport.
    Failure to retain a stream affects only its availability, not terminalization.
    """
    captures = {}
    for name in ("stdout", "stderr"):
        value = getattr(failure, name, None)
        if value is None and name == "stdout":
            value = getattr(failure, "output", None)
        if not isinstance(value, (str, bytes)):
            captures[name] = dict(availability="UNAVAILABLE")
            continue
        try:
            raw = value if isinstance(value, bytes) else value.encode("utf-8", errors="strict")
            identity = hashlib.sha256(raw).hexdigest()
            directory = state.verification / run.run_id
            path = directory / ("native-" + name + "-" + identity + ".raw")
            if (state.verification.is_symlink() or directory.is_symlink() or path.is_symlink()
                    or directory.resolve() != state.verification.parent.resolve() / state.verification.name / run.run_id):
                raise ValueError("private native capture source substitution")
            directory.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as capture:
                    capture.write(raw)
            except FileExistsError:
                if path.read_bytes() != raw:
                    raise ValueError("conflicting immutable native capture")
            captures[name] = dict(availability="AVAILABLE", raw_path=str(path.resolve()),
                                  raw_digest=identity, raw_bytes=len(raw))
        except (OSError, UnicodeError, ValueError, AttributeError):
            captures[name] = dict(availability="UNAVAILABLE")
    return captures


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
