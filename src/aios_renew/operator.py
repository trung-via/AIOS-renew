"""Thin Human-facing operator above the frozen AIOS-renew kernel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .antigravity_adapter import AntigravityExecutionError, AntigravityOutputError
from .artifacts import (
    ArtifactValidationError,
    Result,
    ResultPackage,
    validate_evidence,
    validate_result,
    validate_result_package,
)
from .codex_adapter import (
    CodexExecutionError,
    CodexOutputError,
)
from .dispatcher import (
    DispatcherError,
    primary_dispatcher,
    remediation_dispatcher,
    repair_dispatcher,
    resolve_native_execution_policy,
)
from .executor import ExecutorBoundaryError
from .review_transport import (
    RemoteRemediationLineage,
    ReviewTransportError,
    read_remote_repair,
    resolve_remote_remediation_lineages,
    transport_admission_failure,
    transport_failure,
    transport_post_pass,
)
from .run import Run, RunLeaseRegistry, RunTaskReference
from .run_observation import (
    MonotonicClock,
    RunObservationTracker,
)
from .runtime import (
    RuntimeCompletion,
    persist_failure,
    primary_completion_policy,
    remediation_completion_policy,
    repair_completion_policy,
)
from .review import (
    Finding,
    Remediation,
    RemediationExecution,
    Review,
    ReviewValidationError,
    parse_remediation,
    parse_review,
    validate_remediation,
    validate_review,
)
from .task import Task, TaskValidationError, parse_task
from .verification import VerificationRunner


NativeRunner = Callable[..., subprocess.CompletedProcess[bytes]]


@dataclass
class _RunAttempt:
    """Invocation-local owner of one persisted RUN."""

    run_path: Path | None = None
    completion: RuntimeCompletion | None = None

    def bind_run(self, run_path: Path) -> None:
        if self.run_path is not None:
            raise OperatorError("invocation attempt already owns a RUN")
        self.run_path = run_path

    def bind_completion(self, completion: RuntimeCompletion) -> None:
        self.completion = completion

    @property
    def interruption_phase(self) -> str:
        if self.completion is None:
            return "EXECUTION"
        return self.completion.interruption_phase


class OperatorError(RuntimeError):
    """Raised for a clear operator-level failure."""


class RepositoryLock:
    """Process-safe local repository mutation guard."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+b")
        try:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            _acquire_file_lock(lock_file)
        except OSError as exc:
            lock_file.close()
            raise OperatorError(
                "another AIOS run is active in this repository"
            ) from exc
        self._file = lock_file

    def release(self) -> None:
        if self._file is not None:
            lock_file = self._file
            self._file = None
            try:
                _release_file_lock(lock_file)
            finally:
                lock_file.close()

    def __enter__(self) -> RepositoryLock:
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


def _acquire_file_lock(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_file_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    runs: Path
    handoffs: Path
    staging: Path
    verification: Path
    results: Path
    failures: Path
    observations: Path
    admission_failures: Path
    repairs: Path
    lock: Path


@dataclass(frozen=True)
class TaskSummary:
    task: Task

    def render(self) -> str:
        acceptance = ", ".join(item.id for item in self.task.acceptance)
        verification = "\n".join(
            f"- {command}" for command in self.task.verification.required
        )
        return (
            f"{self.task.task_id}\n"
            f"revision: {self.task.revision}\n"
            f"goal: {self.task.goal}\n"
            f"acceptance: {acceptance}\n"
            "verification:\n"
            f"{verification}"
        )


@dataclass(frozen=True)
class RunSummary:
    task_id: str
    run_id: str
    executor: str
    base_sha: str
    head_sha: str
    result_path: Path

    def render(self) -> str:
        return (
            "AIOS RUN PASS\n"
            f"task: {self.task_id}\n"
            f"run: {self.run_id}\n"
            f"executor: {self.executor}\n"
            f"base_sha: {self.base_sha}\n"
            f"head_sha: {self.head_sha}\n"
            f"result: {self.result_path}"
        )


@dataclass(frozen=True)
class RemediationSummary:
    task_id: str
    review_id: str
    finding_id: str
    run_id: str
    executor: str
    reviewed_sha: str
    head_sha: str
    result_path: Path

    def render(self) -> str:
        return (
            "AIOS REMEDIATION PASS\n"
            f"task: {self.task_id}\n"
            f"review: {self.review_id}\n"
            f"finding: {self.finding_id}\n"
            f"run: {self.run_id}\n"
            f"executor: {self.executor}\n"
            f"reviewed_sha: {self.reviewed_sha}\n"
            f"head_sha: {self.head_sha}\n"
            f"result: {self.result_path}"
        )


@dataclass(frozen=True)
class RepairSummary:
    task_id: str
    failed_run_id: str
    run_id: str
    executor: str
    failed_head_sha: str
    head_sha: str
    result_path: Path

    def render(self) -> str:
        return (
            "AIOS REPAIR PASS\n"
            f"task: {self.task_id}\nfailed_run: {self.failed_run_id}\n"
            f"run: {self.run_id}\nexecutor: {self.executor}\n"
            f"failed_head_sha: {self.failed_head_sha}\nhead_sha: {self.head_sha}\n"
            f"result: {self.result_path}"
        )


def resolve_repository(path: str | Path | None = None) -> Path:
    """Resolve an explicit path or current directory to its real Git root."""

    candidate = Path.cwd() if path is None else Path(path)
    try:
        completed = subprocess.run(
            ("git", "-C", str(candidate), "rev-parse", "--show-toplevel"),
            capture_output=True,
            text=False,
            check=False,
        )
        stdout = _decode_utf8(completed.stdout)
        stderr = _decode_utf8(completed.stderr)
    except (OSError, UnicodeError) as exc:
        raise OperatorError(f"Git invocation failed: {exc}") from exc
    if completed.returncode != 0:
        raise OperatorError(f"not a Git repository: {candidate}")
    return Path(stdout.strip()).resolve()


def load_task(repo: str | Path, task_id: str) -> Task:
    """Load one canonical TASK from the repository-local task store."""

    if not task_id or "/" in task_id or "\\" in task_id:
        raise OperatorError(f"invalid TASK id: {task_id!r}")
    task_path = Path(repo) / ".ai" / "tasks" / f"{task_id}.yaml"
    if not task_path.is_file():
        raise OperatorError(f"TASK not found: {task_id}")
    try:
        task = parse_task(task_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TaskValidationError) as exc:
        raise OperatorError(f"invalid TASK {task_id}: {exc}") from exc
    if task.task_id != task_id:
        raise OperatorError(
            f"TASK id mismatch: requested {task_id}, document contains {task.task_id}"
        )
    return task


def load_review(path: str | Path) -> Review:
    """Load and structurally validate one canonical REVIEW file."""

    try:
        return parse_review(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ReviewValidationError) as exc:
        raise OperatorError(f"invalid REVIEW: {exc}") from exc


def load_remediation(path: str | Path) -> Remediation:
    """Load and structurally validate one canonical REMEDIATION file."""

    try:
        return parse_remediation(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ReviewValidationError) as exc:
        raise OperatorError(f"invalid REMEDIATION: {exc}") from exc


def describe_task(task_id: str, *, repo: str | Path | None = None) -> TaskSummary:
    root = resolve_repository(repo)
    return TaskSummary(load_task(root, task_id))


def runtime_paths(repo: str | Path) -> RuntimePaths:
    root = Path(repo).resolve()
    git_dir_value = _git(root, "rev-parse", "--git-dir")
    git_dir = Path(git_dir_value)
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    state_root = git_dir.resolve() / "aios"
    paths = RuntimePaths(
        root=state_root,
        runs=state_root / "runs",
        handoffs=state_root / "handoffs",
        staging=state_root / "staging",
        verification=state_root / "verification",
        results=state_root / "results",
        failures=state_root / "failures",
        observations=state_root / "observations",
        admission_failures=state_root / "admission-failures",
        repairs=state_root / "repairs",
        lock=state_root / "operator.lock",
    )
    for path in (
        paths.runs,
        paths.handoffs,
        paths.staging,
        paths.verification,
        paths.results,
        paths.failures,
        paths.observations,
        paths.admission_failures,
        paths.repairs,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def next_run_id(task_id: str, runs_path: Path) -> str:
    """Return the next compact local RUN id for one TASK."""

    task_part = task_id.removeprefix("TASK-")
    prefix = f"RUN-{task_part}-"
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{3}})\.json$")
    numbers = []
    for path in runs_path.glob(f"{prefix}*.json"):
        match = pattern.match(path.name)
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}{max(numbers, default=0) + 1:03d}"


def _load_authoritative_prior_result(
    state: RuntimePaths,
    task: Task,
    reviewed_sha: str,
    *,
    repo: Path,
) -> Result:
    """Load one persisted primary or remediation result with canonical lineage."""

    matches: list[Result] = []
    lineage_mismatch = False
    for result_path in sorted(state.results.glob("*.json")):
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        result_data = payload.get("result")
        if not isinstance(result_data, Mapping):
            continue
        if result_data.get("head_sha") != reviewed_sha:
            continue

        try:
            result = validate_result(result_data)
            evidence_data = payload["evidence"]
            if not isinstance(evidence_data, list):
                raise ArtifactValidationError("evidence must be a list")
            evidence = tuple(validate_evidence(item) for item in evidence_data)

            run_id = result_path.stem
            run_data = json.loads(
                (state.runs / f"{run_id}.json").read_text(encoding="utf-8")
            )
            if not isinstance(run_data, Mapping):
                raise TypeError("RUN must be a mapping")
            if "kind" not in run_data:
                run = _run_from_data(run_data)
                if run.run_id != run_id:
                    raise ValueError("RESULT filename does not match RUN id")
                validate_result_package(
                    task=task,
                    run=run,
                    result=result,
                    evidence=evidence,
                )
            elif run_data.get("kind") == "REMEDIATION":
                execution = _remediation_execution_from_data(run_data["execution"])
                if execution.run.run_id != run_id:
                    raise ValueError("RESULT filename does not match RUN id")
                _validate_persisted_remediation_result(
                    repo=repo,
                    task=task,
                    execution=execution,
                    package=ResultPackage(result=result, evidence=evidence),
                )
            else:
                raise ValueError("unknown RUN kind")
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            OperatorError,
        ):
            lineage_mismatch = True
            continue
        matches.append(result)

    if len(matches) == 1 and not lineage_mismatch:
        return matches[0]
    if len(matches) > 1:
        raise OperatorError("authoritative prior RESULT lineage is ambiguous")
    if lineage_mismatch:
        raise OperatorError("authoritative prior RESULT lineage mismatch")
    raise OperatorError("authoritative prior RESULT not found")


def _run_from_data(data: Any) -> Run:
    root = data if isinstance(data, Mapping) else None
    if root is None:
        raise TypeError("RUN must be a mapping")
    task_data = root["task"]
    if not isinstance(task_data, Mapping):
        raise TypeError("RUN.task must be a mapping")
    return Run(
        run_id=root["run_id"],
        task=RunTaskReference(id=task_data["id"], revision=task_data["revision"]),
        executor=root["executor"],
        base_sha=root["base_sha"],
        workspace=root["workspace"],
        head_sha=root.get("head_sha"),
        status=root["status"],
    )


def _remediation_execution_from_data(data: Any) -> RemediationExecution:
    root = data if isinstance(data, Mapping) else None
    if root is None:
        raise TypeError("REMEDIATION execution must be a mapping")
    finding_data = root["finding"]
    if not isinstance(finding_data, Mapping):
        raise TypeError("REMEDIATION finding must be a mapping")
    finding = Finding(
        id=finding_data["id"],
        basis=finding_data["basis"],
        action=finding_data["action"],
        location=finding_data["location"],
        issue=finding_data["issue"],
        expected=finding_data["expected"],
    )
    remediation = parse_remediation(json.dumps(root["remediation"]))
    if remediation.finding_id != finding.id or remediation.action != finding.action:
        raise ValueError("REMEDIATION execution does not match its finding")
    return RemediationExecution(
        review_id=root["review_id"],
        finding=finding,
        remediation=remediation,
        run=_run_from_data(root["run"]),
        original_constraints=tuple(root.get("original_constraints", ())),
    )


def _validate_persisted_remediation_result(
    *,
    repo: Path,
    task: Task,
    execution: RemediationExecution,
    package: ResultPackage,
) -> None:
    """Validate persisted remediation lineage against its actual result contract."""

    run = execution.run
    if run.task.id != task.task_id or run.task.revision != task.revision:
        raise ValueError("REMEDIATION RUN does not reference the supplied TASK")
    if run.base_sha != execution.remediation.reviewed_sha:
        raise ValueError("REMEDIATION RUN base_sha does not match reviewed_sha")
    _require_remediation_result(
        repo,
        execution,
        package,
        actual_head=package.result.head_sha,
    )


def run_task(
    task_id: str,
    *,
    executor: str,
    repo: str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
) -> RunSummary:
    """Execute a TASK and persist/transport deterministic pre-PASS failure facts."""

    root = resolve_repository(repo)
    state = runtime_paths(root)
    observation_tracker = RunObservationTracker(
        "PRIMARY", monotonic_clock=monotonic_clock
    )
    attempt = _RunAttempt()
    try:
        return _run_task_impl(
            task_id, executor=executor, repo=root,
            native_runner=native_runner, verification_runner=verification_runner,
            attempt=attempt,
            observation_tracker=observation_tracker,
        )
    except KeyboardInterrupt as original:
        if attempt.run_path is not None:
            run_path = attempt.run_path
            if not (state.results / run_path.name).is_file():
                _persist_and_transport_failure(
                    root,
                    task_id=task_id,
                    run_path=run_path,
                    failure=original,
                    observation_tracker=observation_tracker,
                    interruption_phase=attempt.interruption_phase,
                )
        raise
    except Exception as original:
        if attempt.run_path is not None:
            run_path = attempt.run_path
            run_id = run_path.stem
            # A canonical RESULT means implementation and verification already passed;
            # only its transport failed, so it is not rewritten as an execution failure.
            if not (state.results / f"{run_id}.json").is_file():
                _persist_and_transport_failure(
                    root,
                    task_id=task_id,
                    run_path=run_path,
                    failure=original,
                    observation_tracker=observation_tracker,
                )
        raise


def _run_task_impl(
    task_id: str,
    *,
    executor: str,
    repo: str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    attempt: _RunAttempt,
    observation_tracker: RunObservationTracker,
) -> RunSummary:
    """Execute a stored TASK through the frozen kernel boundary."""

    root = resolve_repository(repo)
    if executor not in ("codex", "antigravity"):
        raise OperatorError(f"unsupported executor: {executor}")
    state = runtime_paths(root)

    with RepositoryLock(state.lock):
        _synchronize_primary_branch(root)
        task = load_task(root, task_id)
        base_sha = _git(root, "rev-parse", "HEAD")
        run_id = next_run_id(task_id, state.runs)
        run = Run.from_task(
            run_id=run_id,
            task=task,
            executor=executor,
            base_sha=base_sha,
            workspace=str(root),
        )
        run_path = state.runs / f"{run_id}.json"
        _write_json(run_path, asdict(run))
        attempt.bind_run(run_path)
        observation_tracker.admit(run)
        observed_native_runner = observation_tracker.wrap_native_runner(
            native_runner
        )

        execution_policy = resolve_native_execution_policy(
            authorizes_mutation=bool(task.scope.modify)
        )

        dispatcher = primary_dispatcher(
            selected_executor=executor,
            repo=root,
            handoff_path=state.handoffs / f"{run_id}.json",
            execution_policy=execution_policy,
            native_runner=observed_native_runner,
        )

        leases = RunLeaseRegistry()
        lease = leases.acquire(run)
        try:
            package = dispatcher.dispatch_primary(
                task=task,
                run=run,
                lease=lease,
                leases=leases,
            )
        except (CodexOutputError, AntigravityOutputError, ArtifactValidationError) as exc:
            raise OperatorError(f"invalid structural ResultPackage: {exc}") from exc
        except CodexExecutionError as exc:
            raise OperatorError(f"Codex invocation failed: {exc}") from exc
        except AntigravityExecutionError as exc:
            raise OperatorError(str(exc)) from exc
        except ExecutorBoundaryError as exc:
            raise OperatorError(f"executor boundary failed: {exc}") from exc
        except DispatcherError as exc:
            raise OperatorError(f"dispatcher failed: {exc}") from exc

        runtime_completion = RuntimeCompletion(
            repo=root,
            state=state,
            task=task,
            run=run,
            run_path=run_path,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
            error_type=OperatorError,
        )
        attempt.bind_completion(runtime_completion)
        completion = runtime_completion.complete(
            package, primary_completion_policy(task, base_sha=base_sha)
        )

        return RunSummary(
            task_id=task_id,
            run_id=run_id,
            executor=executor,
            base_sha=base_sha,
            head_sha=completion.head_sha,
            result_path=completion.result_path,
        )


def _persist_and_transport_failure(
    root: Path,
    *,
    task_id: str,
    run_path: Path,
    failure: BaseException,
    observation_tracker: RunObservationTracker | None = None,
    observation_path: Path | None = None,
    interruption_phase: str | None = None,
    transport: bool = True,
) -> None:
    """Delegate admitted FAILURE terminalization and optional transport to Runtime."""
    state = runtime_paths(root)
    try:
        run_data = json.loads(run_path.read_text(encoding="utf-8"))
        run = (
            _remediation_execution_from_data(run_data["execution"]).run
            if isinstance(run_data, Mapping)
            and run_data.get("kind") == "REMEDIATION"
            else _run_from_data(run_data)
        )
        persist_failure(
            root,
            state=state,
            task=load_task(root, task_id),
            run=run,
            run_path=run_path,
            failure=failure,
            observation_tracker=observation_tracker,
            observation_path=observation_path,
            interruption_phase=interruption_phase,
            transport=transport,
        )
    except Exception:
        # Delegation setup is also subordinate to the original failure.
        return


def run_repair(
    failed_run_id: str, *, executor: str, repo: str | Path | None = None,
    repair: Mapping[str, Any] | str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
) -> RepairSummary:
    """Accept and execute one GitHub-authored REPAIR as a continuation RUN."""

    root = resolve_repository(repo)
    state = runtime_paths(root)
    observation_tracker = RunObservationTracker(
        "REPAIR", monotonic_clock=monotonic_clock
    )
    attempt = _RunAttempt()
    try:
        return _run_repair_impl(
            failed_run_id, executor=executor, repo=root, repair=repair,
            native_runner=native_runner,
            verification_runner=verification_runner,
            attempt=attempt,
            observation_tracker=observation_tracker,
        )
    except KeyboardInterrupt as original:
        if (
            attempt.run_path is not None
            and not (state.results / attempt.run_path.name).is_file()
        ):
            run_data = json.loads(attempt.run_path.read_text(encoding="utf-8"))
            _persist_and_transport_failure(
                root,
                task_id=run_data["task"]["id"],
                run_path=attempt.run_path,
                failure=original,
                observation_tracker=observation_tracker,
                interruption_phase=attempt.interruption_phase,
            )
        raise
    except Exception as original:
        if (
            attempt.run_path is not None
            and not (state.results / attempt.run_path.name).is_file()
        ):
            try:
                run_data = json.loads(attempt.run_path.read_text(encoding="utf-8"))
                _persist_and_transport_failure(
                    root, task_id=run_data["task"]["id"],
                    run_path=attempt.run_path, failure=original,
                    observation_tracker=observation_tracker,
                )
            except Exception:
                pass
        raise


def retry_transport(run_id: str, *, repo: str | Path | None = None) -> None:
    """Retry GitHub transport for persisted terminal state without execution."""

    root = resolve_repository(repo)
    state = runtime_paths(root)
    run_path = state.runs / f"{run_id}.json"
    result_path = state.results / f"{run_id}.json"
    failure_path = state.failures / f"{run_id}.json"
    observation_path = state.observations / f"{run_id}.json"
    optional_observation = observation_path if observation_path.is_file() else None
    if result_path.is_file() and failure_path.is_file():
        raise OperatorError("RUN has conflicting terminal state")
    try:
        if result_path.is_file():
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            transport_post_pass(
                root, run_id=run_id, head_sha=payload["result"]["head_sha"],
                run_path=run_path, result_path=result_path,
                lineage_path=(state.repairs / f"{run_id}.json")
                if (state.repairs / f"{run_id}.json").is_file() else None,
                observation_path=optional_observation,
            )
        elif failure_path.is_file():
            payload = json.loads(failure_path.read_text(encoding="utf-8"))
            publish_candidate = payload.get("candidate", {}).get("transportable") is True
            transport_failure(
                root, run_id=run_id, head_sha=payload["failed_head_sha"],
                run_path=run_path, failure_path=failure_path,
                publish_candidate=publish_candidate,
                observation_path=optional_observation,
            )
        else:
            raise OperatorError(f"persisted terminal state not found: {run_id}")
    except (ReviewTransportError, OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        if isinstance(exc, OperatorError):
            raise
        raise OperatorError(f"transport retry failed: {exc}") from exc


def _run_repair_impl(
    failed_run_id: str, *, executor: str, repo: Path,
    repair: Mapping[str, Any] | str | Path | None,
    native_runner: NativeRunner,
    verification_runner: VerificationRunner,
    attempt: _RunAttempt,
    observation_tracker: RunObservationTracker,
) -> RepairSummary:
    if executor not in ("codex", "antigravity"):
        raise OperatorError(f"unsupported executor: {executor}")
    state = runtime_paths(repo)
    failure_path = state.failures / f"{failed_run_id}.json"
    if not failure_path.is_file():
        raise OperatorError(f"persisted FAILURE not found: {failed_run_id}")
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    if failure.get("kind") != "FAILURE" or failure.get("run_id") != failed_run_id:
        raise OperatorError("invalid persisted FAILURE lineage")
    continuation_of = failure.get("continuation_of")
    if continuation_of is None:
        root_base_sha = failure.get("base_sha")
    else:
        prior_execution_path = state.repairs / f"{failed_run_id}.json"
        if not prior_execution_path.is_file():
            raise OperatorError("persisted REPAIR lineage not found")
        prior_execution = json.loads(
            prior_execution_path.read_text(encoding="utf-8")
        )
        if prior_execution.get("failed_run_id") != continuation_of:
            raise OperatorError("invalid persisted REPAIR lineage")
        root_base_sha = prior_execution.get("root_base_sha")
    if not isinstance(root_base_sha, str) or not root_base_sha:
        raise OperatorError("invalid original TASK root lineage")
    candidate = failure.get("candidate")
    if not isinstance(candidate, Mapping) or candidate.get("repairable") is not True:
        raise OperatorError("failed candidate is not safely bound for REPAIR")
    if any(
        json.loads(path.read_text(encoding="utf-8")).get("failed_run_id") == failed_run_id
        for path in state.repairs.glob("*.json")
    ):
        raise OperatorError("REPAIR has already been accepted for failed RUN")

    if repair is None:
        try:
            repair_data: Any = json.loads(read_remote_repair(repo, failed_run_id))
        except (ReviewTransportError, json.JSONDecodeError, UnicodeError) as exc:
            raise OperatorError(f"invalid remote REPAIR: {exc}") from exc
    elif isinstance(repair, Mapping):
        repair_data = dict(repair)
    else:
        try:
            repair_data = json.loads(Path(repair).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OperatorError(f"invalid REPAIR: {exc}") from exc
    if not isinstance(repair_data, Mapping):
        raise OperatorError("REPAIR must be a mapping")
    task_ref = repair_data.get("task")
    required = {"repair_id", "failed_run_id", "failed_head_sha", "task", "action", "modification_scope", "instructions", "constraints"}
    if set(repair_data) != required:
        raise OperatorError("REPAIR fields do not match the authorized contract")
    if repair_data.get("failed_run_id") != failed_run_id:
        raise OperatorError("REPAIR does not match failed RUN")
    if repair_data.get("failed_head_sha") != failure.get("failed_head_sha"):
        raise OperatorError("REPAIR does not match failed committed state")
    if not isinstance(task_ref, Mapping) or dict(task_ref) != dict(failure["task"]):
        raise OperatorError("REPAIR does not match original TASK lineage")
    task = load_task(repo, failure["task"]["id"])
    if task.revision != failure["task"]["revision"]:
        raise OperatorError("current TASK revision does not match failed RUN")
    action = repair_data.get("action")
    if action not in ("CODE_FIX", "NO_CHANGE"):
        raise OperatorError("REPAIR action must be CODE_FIX or NO_CHANGE")
    scope = repair_data.get("modification_scope")
    instructions = repair_data.get("instructions")
    constraints = repair_data.get("constraints")
    if not isinstance(scope, list) or not all(isinstance(item, str) and item for item in scope):
        raise OperatorError("REPAIR modification_scope must be a string list")
    if not isinstance(instructions, list) or not instructions or not all(isinstance(item, str) and item for item in instructions):
        raise OperatorError("REPAIR instructions must be a non-empty string list")
    if not isinstance(constraints, list) or not all(isinstance(item, str) and item for item in constraints):
        raise OperatorError("REPAIR constraints must be a string list")
    failed_changed = set(candidate.get("changed_files", ()))
    correction_authority = set(task.scope.modify).union(failed_changed)
    if set(scope).difference(correction_authority):
        raise OperatorError("REPAIR modification scope exceeds correction authority")
    if set(constraints).difference(task.constraints.hard):
        raise OperatorError("REPAIR constraints introduce new Human intent")
    if action == "NO_CHANGE" and scope:
        raise OperatorError("NO_CHANGE REPAIR modification scope must be empty")

    with RepositoryLock(state.lock):
        if _git(repo, "status", "--porcelain"):
            raise OperatorError("repository dirty")
        failed_head = failure["failed_head_sha"]
        if _git(repo, "rev-parse", "HEAD") != failed_head:
            raise OperatorError("current HEAD does not match failed committed state")
        run_id = next_run_id(task.task_id, state.runs)
        run = Run.from_task(
            run_id=run_id, task=task, executor=executor,
            base_sha=failed_head, workspace=str(repo),
        )
        run_path = state.runs / f"{run_id}.json"
        execution = {
            "failed_run_id": failed_run_id,
            "root_base_sha": root_base_sha,
            "failed_head_sha": failed_head,
            "failure": failure,
            "task": _executor_task_data(task),
            "repair": dict(repair_data),
            "run": run,
        }
        _write_json(run_path, asdict(run))
        attempt.bind_run(run_path)
        observation_tracker.admit(run)
        observed_native_runner = observation_tracker.wrap_native_runner(
            native_runner
        )
        persisted_execution = dict(execution)
        persisted_execution["run"] = asdict(run)
        _write_json(state.repairs / f"{run_id}.json", persisted_execution)

        execution_policy = resolve_native_execution_policy(
            authorizes_mutation=action == "CODE_FIX"
        )
        dispatcher = repair_dispatcher(
            selected_executor=executor,
            repo=repo,
            handoff_path=state.handoffs / f"{run_id}.json",
            execution_policy=execution_policy,
            native_runner=observed_native_runner,
        )
        try:
            package = dispatcher.dispatch_repair(execution=execution)
        except (CodexOutputError, AntigravityOutputError, ArtifactValidationError) as exc:
            raise OperatorError(f"invalid structural ResultPackage: {exc}") from exc
        except CodexExecutionError as exc:
            raise OperatorError(f"Codex invocation failed: {exc}") from exc
        except AntigravityExecutionError as exc:
            raise OperatorError(str(exc)) from exc
        except DispatcherError as exc:
            raise OperatorError(f"dispatcher failed: {exc}") from exc

        runtime_completion = RuntimeCompletion(
            repo=repo,
            state=state,
            task=task,
            run=run,
            run_path=run_path,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
            error_type=OperatorError,
        )
        attempt.bind_completion(runtime_completion)
        completion = runtime_completion.complete(
            package,
            repair_completion_policy(
                task,
                root_base_sha=root_base_sha,
                failed_head_sha=failed_head,
                action=action,
                modification_scope=scope,
                lineage_path=state.repairs / f"{run_id}.json",
            ),
        )
        return RepairSummary(
            task_id=task.task_id, failed_run_id=failed_run_id, run_id=run_id,
            executor=executor, failed_head_sha=failed_head,
            head_sha=completion.head_sha, result_path=completion.result_path,
        )


def _is_kernel_source(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    return (
        normalized == "pyproject.toml"
        or normalized.startswith("src/")
    )


def _synchronize_primary_branch(
    root: Path,
    *,
    allow_restart: bool = False,
) -> bool:
    """Align a clean attached main branch to its configured upstream main by exact FF."""

    if _git(root, "status", "--porcelain"):
        raise OperatorError("repository dirty")
    try:
        branch_ref = _git(root, "symbolic-ref", "--quiet", "HEAD")
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    except OperatorError as exc:
        raise OperatorError("repository HEAD is detached") from exc
    if branch != "main":
        raise OperatorError("current branch is not main")
    try:
        upstream = _git(
            root,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        )
    except OperatorError as exc:
        raise OperatorError("current branch has no resolved upstream") from exc
    try:
        remotes = _git(
            root, "config", "--get-all", f"branch.{branch}.remote"
        ).splitlines()
        merge_refs = _git(
            root, "config", "--get-all", f"branch.{branch}.merge"
        ).splitlines()
    except OperatorError as exc:
        raise OperatorError("current branch has no resolved upstream") from exc

    if len(remotes) != 1 or len(merge_refs) != 1:
        raise OperatorError("configured upstream is ambiguous")
    remote = remotes[0].strip()
    merge_ref = merge_refs[0].strip()
    if not remote or not merge_ref:
        raise OperatorError("current branch has no resolved upstream")
    if merge_ref != "refs/heads/main" or not upstream.endswith("/main"):
        raise OperatorError("configured upstream does not resolve to main")

    try:
        _git(root, "fetch", "--no-tags", remote, merge_ref)
    except OperatorError as exc:
        raise OperatorError(f"upstream fetch failed: {exc}") from exc

    local_sha = _git(root, "rev-parse", "HEAD")
    upstream_sha = _git(root, "rev-parse", upstream)
    if local_sha == upstream_sha:
        return False

    if _git_is_ancestor(root, upstream_sha, local_sha):
        raise OperatorError("local branch is ahead of upstream")
    if not _git_is_ancestor(root, local_sha, upstream_sha):
        raise OperatorError("local branch has diverged from upstream")

    diff_output = _git(
        root,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        local_sha,
        upstream_sha,
        strip_stdout=False,
    )
    changed_paths = {p for p in diff_output.split("\0") if p}
    kernel_changed = any(_is_kernel_source(p) for p in changed_paths)

    if kernel_changed and not allow_restart:
        raise OperatorError("cannot continue under stale pre-sync kernel state")

    if kernel_changed and allow_restart:
        if os.environ.get("AIOS_RESTART_ATTEMPTED") == "1":
            raise OperatorError("unsafe reload/restart condition")

    tree_updated = False
    ref_updated = False
    try:
        try:
            _git(root, "read-tree", "-u", "-m", local_sha, upstream_sha)
            tree_updated = True
            _git(root, "update-ref", branch_ref, upstream_sha, local_sha)
            ref_updated = True
        except OperatorError as exc:
            raise OperatorError(f"upstream fast-forward failed: {exc}") from exc

        if _git(root, "status", "--porcelain"):
            raise OperatorError("repository dirty after synchronization")
        if _git(root, "rev-parse", "HEAD") != upstream_sha:
            raise OperatorError(
                "repository HEAD does not match upstream after synchronization"
            )
    except Exception as exc:
        rollback_errors: list[str] = []
        if ref_updated:
            try:
                _git(root, "update-ref", branch_ref, local_sha, upstream_sha)
            except Exception as rollback_err:
                rollback_errors.append(f"update-ref rollback failed: {rollback_err}")
        if tree_updated:
            try:
                _git(root, "read-tree", "-u", "-m", upstream_sha, local_sha)
            except Exception as rollback_err:
                rollback_errors.append(f"read-tree rollback failed: {rollback_err}")

        if ref_updated or tree_updated:
            if not rollback_errors:
                try:
                    if _git(root, "rev-parse", "HEAD") != local_sha:
                        rollback_errors.append("HEAD not restored to pre-sync state")
                    if _git(root, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
                        rollback_errors.append("branch not restored to pre-sync branch")
                    if _git(root, "status", "--porcelain"):
                        rollback_errors.append("worktree or index dirty after rollback")
                except Exception as verify_err:
                    rollback_errors.append(f"restoration verification failed: {verify_err}")

        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise OperatorError(
                f"upstream synchronization restoration failed: {details}"
            ) from exc

        raise

    return kernel_changed


def _preflight_primary_sync(
    root: Path,
    *,
    argv: list[str] | None = None,
    runner: NativeRunner = subprocess.run,
) -> int | None:
    """Safely synchronize clean local main before TASK loading and admission."""

    state = runtime_paths(root)
    with RepositoryLock(state.lock):
        needs_restart = _synchronize_primary_branch(root, allow_restart=True)
    if needs_restart:
        return _restart_primary_invocation(root, argv=argv, runner=runner)
    return None


def _restart_primary_invocation(
    root: Path,
    *,
    argv: list[str] | None = None,
    runner: NativeRunner = subprocess.run,
) -> int:
    """Re-invoke the Human-facing command under synchronized kernel code."""

    env = dict(os.environ)
    env["AIOS_RESTART_ATTEMPTED"] = "1"
    pythonpath = env.get("PYTHONPATH", "")
    src_str = str(root / "src")
    if src_str not in pythonpath:
        env["PYTHONPATH"] = (
            f"{src_str}{os.pathsep}{pythonpath}" if pythonpath else src_str
        )
    cmd = [sys.executable, "-m", "aios_renew.operator"]
    if argv is not None:
        cmd.extend(argv)
    else:
        cmd.extend(sys.argv[1:])
    if "--repo" not in cmd:
        cmd.extend(["--repo", str(root)])
    try:
        completed = runner(cmd, env=env)
    except (OSError, UnicodeError) as exc:
        raise OperatorError(f"unsafe reload/restart condition: {exc}") from exc
    return completed.returncode


def _git_is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            (
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ),
            capture_output=True,
            text=False,
            check=False,
        )
        _decode_utf8(completed.stdout)
        stderr = _decode_utf8(completed.stderr)
    except (OSError, UnicodeError) as exc:
        raise OperatorError(f"Git invocation failed: {exc}") from exc
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise OperatorError(f"Git command failed: {stderr.strip()}")


def run_remediation(
    task_id: str,
    *,
    review: Review | str | Path | None = None,
    remediation: Remediation | str | Path | None = None,
    prior_review: Review | str | Path | None = None,
    finding_id: str | None = None,
    executor: str,
    repo: str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
) -> RemediationSummary:
    """Execute one remediation and persist deterministic pre-PASS failures."""

    root = resolve_repository(repo)
    state = runtime_paths(root)
    observation_tracker = RunObservationTracker(
        "REMEDIATION", monotonic_clock=monotonic_clock
    )
    task = load_task(root, task_id)
    attempt = _RunAttempt()
    admission: dict[str, Any] = {
        "phase": (
            "REMOTE_LINEAGE_RESOLUTION"
            if finding_id is not None
            else "CANONICAL_CONTRACT_ADMISSION"
        ),
    }
    if finding_id is not None:
        admission["finding_id"] = finding_id
    try:
        return _run_remediation_impl(
            task_id,
            review=review,
            remediation=remediation,
            prior_review=prior_review,
            finding_id=finding_id,
            executor=executor,
            repo=root,
            native_runner=native_runner,
            verification_runner=verification_runner,
            resolved_task=task,
            admission=admission,
            attempt=attempt,
            observation_tracker=observation_tracker,
        )
    except KeyboardInterrupt as original:
        if attempt.run_path is not None:
            if not (state.results / attempt.run_path.name).is_file():
                _persist_and_transport_failure(
                    root,
                    task_id=task_id,
                    run_path=attempt.run_path,
                    failure=original,
                    observation_tracker=observation_tracker,
                    interruption_phase=attempt.interruption_phase,
                )
        raise
    except Exception as original:
        if attempt.run_path is not None:
            if not (state.results / attempt.run_path.name).is_file():
                _persist_and_transport_failure(
                    root,
                    task_id=task_id,
                    run_path=attempt.run_path,
                    failure=original,
                    observation_tracker=observation_tracker,
                )
        else:
            _persist_and_transport_admission_failure(
                root,
                task=task,
                executor=executor,
                admission=admission,
                failure=original,
            )
        raise


def _persist_and_transport_admission_failure(
    root: Path,
    *,
    task: Task,
    executor: str,
    admission: Mapping[str, Any],
    failure: Exception,
) -> None:
    """Best-effort one bounded pre-RUN diagnostic without changing authority."""

    try:
        message = str(failure).splitlines()[0][:512]
        record: dict[str, Any] = {
            "kind": "ADMISSION_FAILURE",
            "operation": "REMEDIATION",
            "task": {"id": task.task_id, "revision": task.revision},
            "requested_executor": executor,
            "executor_invoked": False,
            "phase": admission["phase"],
            "error": {
                "type": type(failure).__name__,
                "message": message,
            },
        }
        for name in (
            "finding_id",
            "review_id",
            "reviewed_sha",
            "current_head_sha",
        ):
            value = admission.get(name)
            if isinstance(value, str):
                record[name] = value
        content = json.dumps(
            record, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        identity = hashlib.sha256(content).hexdigest()
        path = runtime_paths(root).admission_failures / f"{identity}.json"
        if path.exists():
            if path.read_bytes() != content:
                return
        else:
            path.write_bytes(content)
        try:
            transport_admission_failure(
                root,
                identity=identity,
                diagnostic_path=path,
            )
        except ReviewTransportError:
            pass
    except Exception:
        # Diagnostic state can never replace or mask the admission exception.
        return


def _run_remediation_impl(
    task_id: str,
    *,
    review: Review | str | Path | None = None,
    remediation: Remediation | str | Path | None = None,
    prior_review: Review | str | Path | None = None,
    finding_id: str | None = None,
    executor: str,
    repo: str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    resolved_task: Task | None = None,
    admission: dict[str, Any] | None = None,
    attempt: _RunAttempt | None = None,
    observation_tracker: RunObservationTracker,
) -> RemediationSummary:
    """Execute one bound remediation without entering the TASK execution path.

    Explicit artifact mode accepts REVIEW and REMEDIATION inputs. Remote canonical
    mode accepts a finding id and resolves those inputs from immutable remote refs.
    """

    root = resolve_repository(repo)
    task = resolved_task or load_task(root, task_id)
    admission = admission if admission is not None else {}
    explicit_mode = (
        review is not None or remediation is not None or prior_review is not None
    )
    remote_mode = finding_id is not None
    if explicit_mode and remote_mode:
        admission["phase"] = "CANONICAL_CONTRACT_ADMISSION"
        raise OperatorError(
            "remote finding mode cannot be mixed with explicit REVIEW/REMEDIATION artifacts"
        )
    if remote_mode:
        canonical_review, canonical_remediation, prior_result, canonical_prior_review = (
            _resolve_remote_remediation_lineage(
                root,
                task=task,
                finding_id=finding_id,
                admission=admission,
            )
        )
    else:
        if review is None or remediation is None:
            raise OperatorError(
                "remediation requires either --finding or both --review and --remediation"
            )
        canonical_review = (
            review if isinstance(review, Review) else load_review(review)
        )
        admission.update(
            {
                "review_id": canonical_review.review_id,
                "reviewed_sha": canonical_review.reviewed_sha,
            }
        )
        canonical_remediation = (
            remediation
            if isinstance(remediation, Remediation)
            else load_remediation(remediation)
        )
        _record_admission_artifact_facts(
            admission, canonical_review, canonical_remediation
        )
        canonical_prior_review = (
            None
            if prior_review is None
            else (
                prior_review
                if isinstance(prior_review, Review)
                else load_review(prior_review)
            )
        )
        state = runtime_paths(root)
        prior_result = _load_authoritative_prior_result(
            state,
            task,
            canonical_review.reviewed_sha,
            repo=root,
        )
    _record_admission_artifact_facts(
        admission, canonical_review, canonical_remediation
    )
    admission["phase"] = "CANONICAL_CONTRACT_ADMISSION"
    state = runtime_paths(root)
    try:
        validate_review(
            task=task,
            result=prior_result,
            review=canonical_review,
            prior_review=canonical_prior_review,
        )
    except ReviewValidationError as exc:
        raise OperatorError(f"invalid REVIEW: {exc}") from exc
    try:
        validate_remediation(
            review=canonical_review,
            remediation=canonical_remediation,
            task=task,
        )
    except ReviewValidationError as exc:
        raise OperatorError(f"invalid REMEDIATION: {exc}") from exc
    if executor not in ("codex", "antigravity"):
        raise OperatorError(f"unsupported executor: {executor}")
    if (
        canonical_remediation.action == "CODE_FIX"
        and not canonical_remediation.modification_scope
    ):
        raise OperatorError("CODE_FIX remediation modification scope is empty")
    if not canonical_remediation.affected_verification:
        raise OperatorError("REMEDIATION affected verification is empty")

    admission["phase"] = "REPOSITORY_ADMISSION"
    with RepositoryLock(state.lock):
        actual_baseline = _git(root, "rev-parse", "HEAD")
        admission["current_head_sha"] = actual_baseline
        if _git(root, "status", "--porcelain"):
            raise OperatorError("repository dirty")
        if actual_baseline != canonical_remediation.reviewed_sha:
            raise OperatorError("current HEAD does not match REMEDIATION reviewed_sha")

        run_id = next_run_id(task_id, state.runs)
        run = Run.from_task(
            run_id=run_id,
            task=task,
            executor=executor,
            base_sha=actual_baseline,
            workspace=str(root),
        )
        finding = next(
            item
            for item in canonical_review.findings
            if item.id == canonical_remediation.finding_id
        )
        execution = RemediationExecution(
            review_id=canonical_review.review_id,
            finding=finding,
            remediation=canonical_remediation,
            run=run,
            original_constraints=canonical_remediation.constraints,
        )
        run_path = state.runs / f"{run_id}.json"
        _write_json(
            run_path,
            {"kind": "REMEDIATION", "execution": asdict(execution)},
        )
        observation_tracker.admit(run)
        observed_native_runner = observation_tracker.wrap_native_runner(
            native_runner
        )
        if attempt is not None:
            attempt.bind_run(run_path)

        execution_policy = resolve_native_execution_policy(
            authorizes_mutation=canonical_remediation.action == "CODE_FIX"
        )
        dispatcher = remediation_dispatcher(
            selected_executor=executor,
            repo=root,
            handoff_path=state.handoffs / f"{run_id}.json",
            execution_policy=execution_policy,
            native_runner=observed_native_runner,
        )

        try:
            package = dispatcher.dispatch_remediation(execution=execution)
        except (CodexOutputError, AntigravityOutputError, ArtifactValidationError) as exc:
            raise OperatorError(f"invalid structural ResultPackage: {exc}") from exc
        except CodexExecutionError as exc:
            raise OperatorError(f"Codex invocation failed: {exc}") from exc
        except AntigravityExecutionError as exc:
            raise OperatorError(str(exc)) from exc
        except DispatcherError as exc:
            raise OperatorError(f"dispatcher failed: {exc}") from exc

        runtime_completion = RuntimeCompletion(
            repo=root,
            state=state,
            task=task,
            run=run,
            run_path=run_path,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
            error_type=OperatorError,
        )
        if attempt is not None:
            attempt.bind_completion(runtime_completion)
        completion = runtime_completion.complete(
            package, remediation_completion_policy(execution)
        )

        return RemediationSummary(
            task_id=task_id,
            review_id=canonical_review.review_id,
            finding_id=canonical_remediation.finding_id,
            run_id=run_id,
            executor=executor,
            reviewed_sha=actual_baseline,
            head_sha=completion.head_sha,
            result_path=completion.result_path,
        )


def accept_candidate(
    task_id: str,
    *,
    finding_id: str,
    executor: str,
    repo: str | Path | None = None,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
) -> RemediationSummary:
    """Admit an already committed CODE_FIX candidate without invoking an Executor."""

    root = resolve_repository(repo)
    state = runtime_paths(root)
    observation_tracker = RunObservationTracker(
        "REMEDIATION", monotonic_clock=monotonic_clock
    )
    existing = {path.name for path in state.runs.glob("*.json")}
    try:
        return _accept_candidate_impl(
            task_id,
            finding_id=finding_id,
            executor=executor,
            repo=root,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
        )
    except Exception as original:
        created = [
            path for path in state.runs.glob("*.json") if path.name not in existing
        ]
        if len(created) == 1 and not (state.results / created[0].name).is_file():
            _persist_and_transport_failure(
                root,
                task_id=task_id,
                run_path=created[0],
                failure=original,
                observation_tracker=observation_tracker,
            )
        raise


def _accept_candidate_impl(
    task_id: str,
    *,
    finding_id: str,
    executor: str,
    repo: Path,
    verification_runner: VerificationRunner,
    observation_tracker: RunObservationTracker,
) -> RemediationSummary:
    if executor not in ("codex", "antigravity"):
        raise OperatorError(f"unsupported executor: {executor}")
    task = load_task(repo, task_id)
    state = runtime_paths(repo)

    with RepositoryLock(state.lock):
        if _git(repo, "status", "--porcelain"):
            raise OperatorError("repository dirty")
        candidate_head = _git(repo, "rev-parse", "HEAD")
        review, remediation, prior_result, prior_review = _resolve_direct_lineage(
            repo, task=task, finding_id=finding_id
        )
        try:
            validate_review(
                task=task,
                result=prior_result,
                review=review,
                prior_review=prior_review,
            )
            validate_remediation(review=review, remediation=remediation, task=task)
        except ReviewValidationError as exc:
            raise OperatorError(f"invalid direct candidate lineage: {exc}") from exc
        if review.verdict != "CHANGES_REQUIRED":
            raise OperatorError("direct candidate REVIEW is not CHANGES_REQUIRED")
        if remediation.action != "CODE_FIX":
            raise OperatorError("direct candidate requires CODE_FIX remediation")
        if not remediation.modification_scope:
            raise OperatorError("CODE_FIX remediation modification scope is empty")
        if not remediation.affected_verification:
            raise OperatorError("REMEDIATION affected verification is empty")
        if candidate_head == remediation.reviewed_sha:
            raise OperatorError("CODE_FIX candidate did not advance HEAD")
        if not _git_is_ancestor(repo, remediation.reviewed_sha, candidate_head):
            raise OperatorError("candidate HEAD does not descend from reviewed_sha")

        changed_files = _committed_changed_files(
            repo, remediation.reviewed_sha, candidate_head
        )
        if not changed_files:
            raise OperatorError("CODE_FIX candidate committed delta is empty")
        outside_remediation = changed_files.difference(
            remediation.modification_scope
        )
        if outside_remediation:
            raise OperatorError(
                "committed changed paths outside REMEDIATION modification scope: "
                + ", ".join(sorted(outside_remediation))
            )
        outside_task = changed_files.difference(task.scope.modify)
        if outside_task:
            raise OperatorError(
                "committed changed paths outside TASK.scope.modify: "
                + ", ".join(sorted(outside_task))
            )
        existing_summary = _accepted_candidate_summary(
            state,
            repo=repo,
            task=task,
            review=review,
            finding_id=finding_id,
            candidate_head=candidate_head,
        )
        if existing_summary is not None:
            return existing_summary

        run_id = next_run_id(task_id, state.runs)
        run = Run.from_task(
            run_id=run_id,
            task=task,
            executor=executor,
            base_sha=remediation.reviewed_sha,
            workspace=str(repo),
        )
        finding = next(item for item in review.findings if item.id == finding_id)
        execution = RemediationExecution(
            review_id=review.review_id,
            finding=finding,
            remediation=remediation,
            run=run,
            original_constraints=remediation.constraints,
        )
        run_path = state.runs / f"{run_id}.json"
        _write_json(
            run_path,
            {
                "kind": "REMEDIATION",
                "acceptance": {
                    "mode": "DIRECT_CANDIDATE",
                    "candidate_head": candidate_head,
                },
                "execution": asdict(execution),
            },
        )
        observation_tracker.admit(run)

        structural_result = Result(
            head_sha=candidate_head,
            claims=(),
            changed_files=tuple(sorted(changed_files)),
            unresolved=(),
        )
        structural_package = ResultPackage(result=structural_result, evidence=())
        completion = RuntimeCompletion(
            repo=repo,
            state=state,
            task=task,
            run=run,
            run_path=run_path,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
            error_type=OperatorError,
        ).complete(
            structural_package,
            remediation_completion_policy(execution, direct_candidate=True),
        )
        return RemediationSummary(
            task_id=task_id,
            review_id=review.review_id,
            finding_id=finding_id,
            run_id=run_id,
            executor=executor,
            reviewed_sha=remediation.reviewed_sha,
            head_sha=completion.head_sha,
            result_path=completion.result_path,
        )


def _resolve_direct_lineage(
    repo: Path, *, task: Task, finding_id: str
) -> tuple[Review, Remediation, Result, Review | None]:
    return _resolve_remote_remediation_lineage(
        repo, task=task, finding_id=finding_id, context="direct candidate"
    )


def _resolve_remote_remediation_lineage(
    repo: Path,
    *,
    task: Task,
    finding_id: str,
    context: str = "remote remediation",
    admission: dict[str, Any] | None = None,
) -> tuple[Review, Remediation, Result, Review | None]:
    """Resolve exactly one contract-valid remote lineage without heuristics."""

    try:
        remote_lineages = resolve_remote_remediation_lineages(
            repo, finding_id=finding_id
        )
    except ReviewTransportError as exc:
        raise OperatorError(f"{context} lineage resolution failed: {exc}") from exc

    matches: list[tuple[Review, Remediation, Result, Review | None]] = []
    for remote in remote_lineages:
        parsed = _parse_remote_direct_lineage(
            repo, task=task, remote=remote, admission=admission
        )
        if parsed is not None:
            if parsed[1].finding_id != finding_id:
                raise OperatorError(
                    f"contract-invalid canonical lineage at {remote.ref}: "
                    "REMEDIATION finding does not match requested finding"
                )
            matches.append(parsed)
    if not matches:
        raise OperatorError(f"canonical {context} lineage not found")
    if len(matches) != 1:
        raise OperatorError(f"canonical {context} lineage is ambiguous")
    return matches[0]


def _parse_remote_direct_lineage(
    repo: Path,
    *,
    task: Task,
    remote: RemoteRemediationLineage,
    admission: dict[str, Any] | None = None,
) -> tuple[Review, Remediation, Result, Review | None] | None:
    try:
        run_data = json.loads(remote.run.decode("utf-8", errors="strict"))
        if not isinstance(run_data, Mapping):
            raise TypeError("RUN must be a mapping")
        if run_data.get("kind") == "REMEDIATION":
            prior_execution = _remediation_execution_from_data(run_data["execution"])
            source_run = prior_execution.run
        elif "kind" not in run_data:
            prior_execution = None
            source_run = _run_from_data(run_data)
        else:
            raise ValueError("unknown source RUN kind")
        if source_run.run_id != remote.source_run_id:
            raise ValueError("remote ref source RUN mismatch")
        if source_run.task.id != task.task_id:
            return None
        if source_run.task.revision != task.revision:
            raise ValueError("source RUN TASK revision mismatch")

        if admission is not None:
            admission["phase"] = "CANONICAL_CONTRACT_ADMISSION"
        review = parse_review(remote.review.decode("utf-8", errors="strict"))
        if admission is not None:
            admission.update(
                {
                    "review_id": review.review_id,
                    "reviewed_sha": review.reviewed_sha,
                }
            )
        remediation = parse_remediation(
            remote.remediation.decode("utf-8", errors="strict")
        )
        if admission is not None:
            _record_admission_artifact_facts(admission, review, remediation)
        result_data = json.loads(remote.result.decode("utf-8", errors="strict"))
        if not isinstance(result_data, Mapping):
            raise TypeError("ResultPackage must be a mapping")
        result = validate_result(result_data["result"])
        evidence_data = result_data["evidence"]
        if not isinstance(evidence_data, list):
            raise TypeError("evidence must be a list")
        evidence = tuple(validate_evidence(item) for item in evidence_data)
        package = ResultPackage(result=result, evidence=evidence)
        if prior_execution is None:
            validate_result_package(
                task=task, run=source_run, result=result, evidence=evidence
            )
        else:
            _validate_persisted_remediation_result(
                repo=repo, task=task, execution=prior_execution, package=package
            )
        if result.head_sha != review.reviewed_sha:
            raise ValueError("REVIEW does not bind to authoritative source RESULT")
        if remediation.finding_id not in {item.id for item in review.findings}:
            raise ValueError("REMEDIATION finding is absent from REVIEW")
        prior_review = None
        if review.prior_finding_id is not None:
            if prior_execution is None:
                raise ValueError("DELTA REVIEW source is not a REMEDIATION RUN")
            if review.prior_finding_id != prior_execution.finding.id:
                raise ValueError("DELTA REVIEW prior finding lineage mismatch")
            prior_review = Review(
                review_id=prior_execution.review_id,
                reviewed_sha=prior_execution.remediation.reviewed_sha,
                mode="PRIMARY",
                verdict="CHANGES_REQUIRED",
                acceptance={},
                findings=(prior_execution.finding,),
            )
        return review, remediation, result, prior_review
    except (
        ArtifactValidationError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        ReviewValidationError,
    ) as exc:
        raise OperatorError(
            f"contract-invalid canonical lineage at {remote.ref}: {exc}"
        ) from exc


def _record_admission_artifact_facts(
    admission: dict[str, Any], review: Review, remediation: Remediation
) -> None:
    """Retain only authoritative, allowlisted facts known at admission time."""

    admission.update(
        {
            "finding_id": remediation.finding_id,
            "review_id": review.review_id,
            "reviewed_sha": remediation.reviewed_sha,
        }
    )


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


def _accepted_candidate_summary(
    state: RuntimePaths,
    *,
    repo: Path,
    task: Task,
    review: Review,
    finding_id: str,
    candidate_head: str,
) -> RemediationSummary | None:
    for run_path in sorted(state.runs.glob("*.json")):
        try:
            data = json.loads(run_path.read_text(encoding="utf-8"))
            acceptance = data.get("acceptance", {})
            execution = _remediation_execution_from_data(data["execution"])
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        if (
            data.get("kind") != "REMEDIATION"
            or acceptance.get("mode") != "DIRECT_CANDIDATE"
            or acceptance.get("candidate_head") != candidate_head
            or execution.review_id != review.review_id
            or execution.finding.id != finding_id
        ):
            continue
        result_path = state.results / run_path.name
        if not result_path.is_file():
            raise OperatorError("matching direct candidate RUN has no canonical RESULT")
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            result = validate_result(payload["result"])
            evidence = tuple(validate_evidence(item) for item in payload["evidence"])
            _validate_persisted_remediation_result(
                repo=repo,
                task=task,
                execution=execution,
                package=ResultPackage(result=result, evidence=evidence),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OperatorError(f"invalid accepted direct candidate state: {exc}") from exc
        return RemediationSummary(
            task_id=task.task_id,
            review_id=review.review_id,
            finding_id=finding_id,
            run_id=execution.run.run_id,
            executor=execution.run.executor,
            reviewed_sha=execution.remediation.reviewed_sha,
            head_sha=candidate_head,
            result_path=result_path,
        )
    return None


def _require_remediation_result(
    repo: Path,
    execution: RemediationExecution,
    package: ResultPackage,
    *,
    actual_head: str,
) -> None:
    """Apply one shared completion policy to either native executor."""

    _require_remediation_package_contract(
        execution, package, actual_head=actual_head
    )

    _require_remediation_repository_state(
        repo, execution, package, actual_head=actual_head
    )


def _require_remediation_repository_state(
    repo: Path,
    execution: RemediationExecution,
    package: ResultPackage,
    *,
    actual_head: str,
) -> None:
    """Validate remediation structure and committed scope before verification."""

    if package.result.claims:
        raise OperatorError("remediation RESULT claims must be empty")
    if package.result.unresolved:
        raise OperatorError("remediation RESULT has unresolved items")

    output = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        execution.remediation.reviewed_sha,
        actual_head,
        strip_stdout=False,
    )
    actual_changed = {path for path in output.split("\0") if path}
    if set(package.result.changed_files) != actual_changed:
        raise OperatorError("RESULT.changed_files mismatch")
    outside_scope = actual_changed.difference(
        execution.remediation.modification_scope
    )
    if outside_scope:
        raise OperatorError(
            "committed changed paths outside REMEDIATION modification scope: "
            + ", ".join(sorted(outside_scope))
        )
    if execution.remediation.action == "EVIDENCE_ONLY":
        if actual_head != execution.remediation.reviewed_sha or actual_changed:
            raise OperatorError("EVIDENCE_ONLY remediation changed repository HEAD")
    else:
        if actual_head == execution.remediation.reviewed_sha:
            raise OperatorError("CODE_FIX remediation did not advance HEAD")
        if not actual_changed:
            raise OperatorError("CODE_FIX remediation committed delta is empty")


def _require_remediation_package_contract(
    execution: RemediationExecution,
    package: ResultPackage,
    *,
    actual_head: str,
) -> None:
    """Bind a remediation package without applying the primary TASK contract."""

    if package.result.claims:
        raise OperatorError("remediation RESULT claims must be empty")
    if package.result.unresolved:
        raise OperatorError("remediation RESULT has unresolved items")

    evidence_ids: set[str] = set()
    for item in package.evidence:
        if item.evidence_id in evidence_ids:
            raise OperatorError(f"duplicate evidence_id: {item.evidence_id}")
        evidence_ids.add(item.evidence_id)
        if item.run_id != execution.run.run_id:
            raise OperatorError(
                f"{item.evidence_id} does not reference RUN {execution.run.run_id}"
            )
        if item.subject_sha != actual_head:
            raise OperatorError(
                f"{item.evidence_id} subject_sha does not match RESULT head_sha"
            )
    for command in execution.remediation.affected_verification:
        matching = [
            item for item in package.evidence if item.source.command == command
        ]
        if not matching:
            raise OperatorError(
                f"missing affected verification evidence for required command: {command}"
            )
        if not any(item.result.exit_code == 0 for item in matching):
            raise OperatorError(
                "affected verification command has no successful evidence: "
                + command
            )



def _executor_task_data(task: Task) -> dict[str, Any]:
    data = asdict(task)
    data.pop("verification")
    return data


def _git(repo: Path, *args: str, strip_stdout: bool = True) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=False,
            check=False,
        )
        stdout = _decode_utf8(completed.stdout)
        stderr = _decode_utf8(completed.stderr)
    except (OSError, UnicodeError) as exc:
        raise OperatorError(f"Git invocation failed: {exc}") from exc
    if completed.returncode != 0:
        detail = stderr.strip() or stdout.strip()
        raise OperatorError(f"Git command failed: {detail}")
    return stdout.strip() if strip_stdout else stdout


def _decode_utf8(value: bytes | str) -> str:
    """Decode captured subprocess bytes synchronously and fail closed."""

    return value.decode("utf-8", errors="strict") if isinstance(value, bytes) else value


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aios")
    commands = parser.add_subparsers(dest="command", required=True)

    task_parser = commands.add_parser("task", help="Show a stored canonical TASK")
    task_parser.add_argument("task_id")
    task_parser.add_argument("--repo")

    run_parser = commands.add_parser("run", help="Execute a stored canonical TASK")
    run_parser.add_argument("task_id")
    run_parser.add_argument("--executor", required=True, choices=("codex", "antigravity"))
    run_parser.add_argument("--repo")
    remediation_parser = commands.add_parser(
        "remediate", help="Execute one canonical narrow REMEDIATION"
    )
    remediation_parser.add_argument("task_id")
    remediation_parser.add_argument("--finding")
    remediation_parser.add_argument("--review")
    remediation_parser.add_argument("--remediation")
    remediation_parser.add_argument("--prior-review")
    remediation_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity")
    )
    remediation_parser.add_argument("--repo")
    candidate_parser = commands.add_parser(
        "accept-candidate",
        help="Accept one already committed CODE_FIX candidate",
    )
    candidate_parser.add_argument("task_id")
    candidate_parser.add_argument("--finding", required=True)
    candidate_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity")
    )
    candidate_parser.add_argument("--repo")
    repair_parser = commands.add_parser(
        "repair", help="Execute one GitHub-authored pre-PASS REPAIR"
    )
    repair_parser.add_argument("failed_run_id")
    repair_parser.add_argument("--repair")
    repair_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity")
    )
    repair_parser.add_argument("--repo")
    transport_parser = commands.add_parser(
        "transport", help="Retry transport of one persisted terminal RUN"
    )
    transport_parser.add_argument("run_id")
    transport_parser.add_argument("--repo")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "task":
            print(describe_task(args.task_id, repo=args.repo).render())
        elif args.command == "run":
            repo_root = resolve_repository(args.repo)
            restart_code = _preflight_primary_sync(repo_root, argv=argv)
            if restart_code is not None:
                return restart_code
            summary = run_task(
                args.task_id,
                executor=args.executor,
                repo=repo_root,
            )
            print(summary.render())
        elif args.command == "remediate":
            summary = run_remediation(
                args.task_id,
                review=args.review,
                remediation=args.remediation,
                prior_review=args.prior_review,
                finding_id=args.finding,
                executor=args.executor,
                repo=args.repo,
            )
            print(summary.render())
        elif args.command == "accept-candidate":
            summary = accept_candidate(
                args.task_id,
                finding_id=args.finding,
                executor=args.executor,
                repo=args.repo,
            )
            print(summary.render())
        elif args.command == "repair":
            summary = run_repair(
                args.failed_run_id, executor=args.executor, repo=args.repo,
                repair=args.repair,
            )
            print(summary.render())
        else:
            retry_transport(args.run_id, repo=args.repo)
            print(f"AIOS TRANSPORT PASS\nrun: {args.run_id}")
    except OperatorError as exc:
        print(f"AIOS ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
