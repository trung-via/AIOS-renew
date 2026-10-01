"""Thin Human-facing operator above the frozen AIOS-renew kernel."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from .antigravity_adapter import AntigravityExecutionError, AntigravityOutputError
from .antigravity_minimax_adapter import (
    AntigravityMinimaxExecutionError,
    AntigravityMinimaxOutputError,
)
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
from .correction_dispatch import CorrectionDispatchError as _CorrectionDispatchError
from .correction_frontier import CorrectionFrontierError
from .correction_integration import (
    CorrectionIntegrationError,
    CorrectionIntegrationResult,
    integrate_correction as _integrate_correction_impl,
    resolve_valid_integration,
)
from .dispatcher import (
    DispatcherError,
    primary_dispatcher,
    remediation_dispatcher,
    repair_dispatcher,
    resolve_native_execution_policy,
)
from .execution_profile import (
    ExecutionProfilePolicy,
    ExecutionProfileConflictError,
    ExecutionProfileError,
    ExecutionProfileValidationError,
    ResolvedExecutionProfile,
    bind_execution_profile,
    is_profile_managed_executor,
    load_execution_profile_policy,
    parse_execution_profile,
    parse_execution_profile_policy,
    persist_execution_profile,
    validate_execution_profile,
)
from .dispatch_reconciliation import (
    DispatchError,
    DispatchInvocation,
    bind_dispatch_run,
    execute_dispatch,
    existing_dispatch_profile,
)
from .executor import ExecutorBoundaryError
from .performance_observation import (
    PerformanceObservation,
    PerformanceObservationError,
    observe_performance,
)
from .repair_dispatch import RepairDispatchError as _RepairDispatchError
from .review_transport import (
    RemoteFailureArtifacts,
    RemoteRemediationLineage,
    ReviewTransportError,
    RemoteQueryError,
    RemoteTaskLifecycle,
    RemoteLifecycleTerminal,
    read_remote_repair,
    resolve_remote_repair_authorization,
    read_remote_task,
    resolve_remote_primary_recovery,
    resolve_remote_repair_recovery,
    resolve_remote_remediation_lineages,
    resolve_remote_run_namespace,
    resolve_remote_task_lifecycle,
    resolve_detached_observation_remote,
    task_run_prefix,
    transport_admission_failure,
    transport_failure,
    transport_post_pass,
    validate_runtime_failure_binding,
    prove_remote_failed_candidate,
    _exact_remote_refs,
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
    validate_preverification_candidate,
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
_ACTIVE_RESTART_SOURCE: ContextVar[Path | None] = ContextVar("active_restart_source", default=None)
_SOURCE_REPAIR_POLICY: ContextVar[ExecutionProfilePolicy | None] = ContextVar(
    "source_repair_policy", default=None
)


@dataclass
class _RunAttempt:
    """Invocation-local owner of one persisted RUN."""

    run_path: Path | None = None
    completion: RuntimeCompletion | None = None
    subject_repo: Path | None = None
    task: Task | None = None
    historical_workspace: Path | None = None

    def bind_run(self, run_path: Path) -> None:
        if self.run_path is not None:
            raise OperatorError("invocation attempt already owns a RUN")
        self.run_path = run_path

    def bind_completion(self, completion: RuntimeCompletion) -> None:
        self.completion = completion

    def bind_subject(
        self, repo: Path, task: Task, historical_workspace: Path | None = None
    ) -> None:
        self.subject_repo = repo
        self.task = task
        self.historical_workspace = historical_workspace

    @property
    def interruption_phase(self) -> str:
        if self.completion is None:
            return "EXECUTION"
        return self.completion.interruption_phase

    @property
    def verification_subject_sha(self) -> str | None:
        if self.completion is None:
            return None
        return self.completion.verification_subject_sha


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
    preverification: Path
    verification: Path
    results: Path
    failures: Path
    observations: Path
    admission_failures: Path
    repairs: Path
    lock: Path
    execution_profiles: Path | None = None


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


from .correction_preflight import (
    CorrectionPreflightResult,
    _ADMISSION_PHASES,
    _ADMISSION_REASONS,
    _blocked_correction_preflight,
    _find_remote_query_error,
    preflight_remediation,
    preflight_repair,
)
from .unified_state import (
    UnifiedStateObservation,
    _UNIFIED_AUTHORITIES,
    _UNIFIED_NEXT_ACTIONS,
    observe_unified_state,
)
from . import unified_state as _unified_state_module

_unified_state_module.op = sys.modules[__name__]



from .human_surface import (
    HumanSurfaceResult,
    _HUMAN_AUTHORITIES,
    _HUMAN_DISPOSITIONS,
    continue_task,
)


@dataclass(frozen=True)
class RecoverySummary:
    task_id: str
    source_run_id: str
    run_id: str
    executor: str
    base_sha: str
    head_sha: str
    result_path: Path

    def render(self) -> str:
        return (
            "AIOS RECOVER PRIMARY PASS\n"
            f"task: {self.task_id}\nsource_run: {self.source_run_id}\n"
            f"run: {self.run_id}\nexecutor: {self.executor}\n"
            f"base_sha: {self.base_sha}\nhead_sha: {self.head_sha}\n"
            f"result: {self.result_path}"
        )


@dataclass(frozen=True)
class _HistoricalRepairAdmission:
    failure: Mapping[str, Any]
    task: Task
    root_base_sha: str
    result_base_sha: str
    remote_run_ids: tuple[str, ...]
    preverification: bytes | None
    origin_affected_verification: tuple[str, ...]


@dataclass(frozen=True)
class _RepairAdmission:
    failure: Mapping[str, Any]
    task: Task
    root_base_sha: str
    result_base_sha: str
    remote_run_ids: tuple[str, ...]
    historical: bool
    repair: Mapping[str, Any]
    authorization_sha: str | None
    action: str
    scope: list[str]
    reusable_package: ResultPackage | None
    origin_affected_verification: tuple[str, ...]


@dataclass(frozen=True)
class _RemediationAdmission:
    review: Review
    remediation: Remediation
    prior_result: Result
    prior_review: Review | None
    task: Task
    remote_mode: bool
    source_run_id: str
    execution_base_run_id: str
    execution_base_sha: str
    cumulative: bool = False
    integrated_base: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class _PrimaryRecoveryAdmission:
    task: Task
    source_run: Run
    structural_package: ResultPackage
    remote_run_ids: tuple[str, ...]


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


def _canonical_task_path(repo: str | Path, task_id: str) -> Path:
    """Validate one TASK identity before resolving its exact local path."""

    if (
        not isinstance(task_id, str)
        or not task_id
        or "/" in task_id
        or "\\" in task_id
    ):
        raise OperatorError(f"invalid TASK id: {task_id!r}")
    return Path(repo) / ".ai" / "tasks" / f"{task_id}.yaml"


def load_task(repo: str | Path, task_id: str) -> Task:
    """Load one canonical TASK from the repository-local task store."""

    task_path = _canonical_task_path(repo, task_id)
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


def _admit_authorized_task_identity(
    repo: Path,
    *,
    task_id: str,
    task_revision: int | None,
    task_blob_sha: str | None,
    task_commit_sha: str | None,
    current_head: str,
    admission: dict[str, Any],
) -> Task:
    """Prove one immutable v2 TASK authorization against synchronized main."""

    if (
        isinstance(task_revision, bool)
        or not isinstance(task_revision, int)
        or task_revision < 1
        or not isinstance(task_blob_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", task_blob_sha)
        or not isinstance(task_commit_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", task_commit_sha)
    ):
        raise OperatorError(
            "authorized TASK identity mismatch: incomplete or malformed authorization"
        )

    task_path = f".ai/tasks/{task_id}.yaml"
    try:
        _git(repo, "cat-file", "-e", f"{task_commit_sha}^{{commit}}")
        _git(repo, "merge-base", "--is-ancestor", task_commit_sha, current_head)
    except OperatorError as exc:
        raise OperatorError(
            "authorized TASK identity mismatch: authorization commit is unavailable "
            "or is not an ancestor of current HEAD"
        ) from exc

    try:
        authorized_blob = _git(repo, "rev-parse", f"{task_commit_sha}:{task_path}")
        current_blob = _git(repo, "rev-parse", f"{current_head}:{task_path}")
    except OperatorError as exc:
        raise OperatorError(
            "authorized TASK identity mismatch: TASK blob is unavailable"
        ) from exc

    admission["current_task_blob_sha"] = current_blob
    if authorized_blob != task_blob_sha or current_blob != task_blob_sha:
        raise OperatorError(
            "authorized TASK identity mismatch: TASK blob does not match authorization"
        )
    try:
        task = load_task(repo, task_id)
    except OperatorError as exc:
        raise OperatorError(
            "authorized TASK identity mismatch: current TASK is missing or malformed"
        ) from exc
    _bind_admission_task(admission, task)
    if task.task_id != task_id or task.revision != task_revision:
        raise OperatorError(
            "authorized TASK identity mismatch: TASK id or revision does not match authorization"
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
    paths = _runtime_paths_readonly(root)
    for path in (
        paths.runs,
        paths.handoffs,
        paths.staging,
        paths.preverification,
        paths.verification,
        paths.results,
        paths.failures,
        paths.observations,
        paths.admission_failures,
        paths.repairs,
        paths.execution_profiles,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _runtime_paths_readonly(repo: str | Path) -> RuntimePaths:
    """Describe Runtime-owned paths without creating any operational state."""

    root = Path(repo).resolve()
    state_root = runtime_state_root(root)
    return RuntimePaths(
        root=state_root,
        runs=state_root / "runs",
        handoffs=state_root / "handoffs",
        staging=state_root / "staging",
        preverification=state_root / "pre-verification",
        verification=state_root / "verification",
        results=state_root / "results",
        failures=state_root / "failures",
        observations=state_root / "observations",
        admission_failures=state_root / "admission-failures",
        repairs=state_root / "repairs",
        lock=state_root / "operator.lock",
        execution_profiles=state_root / "execution-profiles",
    )


@contextmanager
def _remote_observation_repository(control_repo: Path) -> Iterator[Path]:
    """Provide an isolated Git object/config store for read-only remote resolution."""

    branch = _git(control_repo, "rev-parse", "--abbrev-ref", "HEAD")
    common_dir = Path(_git(control_repo, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = control_repo / common_dir
    control_objects = (common_dir.resolve() / "objects").as_posix()
    if branch == "HEAD":
        remote = resolve_detached_observation_remote(control_repo)
        # This branch exists only in the disposable observer's config. The
        # control subject remains detached and none of its refs are changed.
        branch = "aios-observation"
    else:
        remote = _git(control_repo, "config", "--get", f"branch.{branch}.remote")
    remote_name = "origin" if remote == "." else remote
    remote_url = (
        str(control_repo)
        if remote == "."
        else _git(control_repo, "remote", "get-url", remote)
    )
    with tempfile.TemporaryDirectory(prefix="aios-correction-preflight-") as raw:
        observer = Path(raw)
        _git(observer, "init", "--quiet")
        (observer / ".git" / "objects" / "info" / "alternates").write_bytes(
            f"{control_objects}\n".encode("utf-8")
        )
        _git(observer, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        _git(observer, "remote", "add", remote_name, remote_url)
        _git(observer, "config", f"branch.{branch}.remote", remote_name)
        yield observer


def runtime_state_root(repo: str | Path) -> Path:
    """Resolve repository-local Git runtime state without creating it."""

    root = Path(repo).resolve()
    git_dir_value = _git(root, "rev-parse", "--git-dir")
    git_dir = Path(git_dir_value)
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    return git_dir.resolve() / "aios"


def next_run_id(
    task_id: str, runs_path: Path, *, reserved: tuple[str, ...] = ()
) -> str:
    """Return the next compact local RUN id for one TASK."""

    prefix = task_run_prefix(task_id)
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{3,}})\.json$")
    numbers = []
    for path in runs_path.glob(f"{prefix}*.json"):
        match = pattern.match(path.name)
        if match:
            numbers.append(int(match.group(1)))
    for run_id in reserved:
        match = pattern.match(f"{run_id}.json")
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}{max(numbers, default=0) + 1:03d}"


def _remote_run_reservations(
    repo: Path, task: Task, *, admission: dict[str, Any] | None = None
) -> tuple[str, ...]:
    """Return validated remote reservations or fail closed on terminal conflict."""

    try:
        namespace = resolve_remote_run_namespace(
            repo,
            task_id=task.task_id,
            task_revision=task.revision,
        )
    except ReviewTransportError as exc:
        if admission is not None:
            observed_refs = getattr(exc, "observed_refs", ())
            if isinstance(observed_refs, tuple):
                _record_observed_refs(admission, observed_refs)
        raise OperatorError(f"canonical RUN namespace rejected: {exc}") from exc
    if admission is not None:
        _record_observed_refs(admission, namespace.observed_refs)
    if namespace.conflicts:
        raise OperatorError(
            "canonical RUN has conflicting terminal artifacts: "
            + ", ".join(namespace.conflicts)
        )
    return namespace.run_ids


_RUN_ID_PATTERN = re.compile(r"^RUN-[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_CANONICAL_PREDECESSOR_FIELDS = frozenset(
    {"source_run_id", "review_id", "finding_id", "reviewed_sha"}
)
_CANONICAL_EXECUTION_BASE_FIELDS = frozenset({"run_id", "candidate_sha"})
_CANONICAL_INTEGRATED_EXECUTION_BASE_FIELDS = frozenset({
    "version",
    "kind",
    "cumulative_tip_run_id",
    "cumulative_tip_candidate_sha",
    "authorized_main_sha",
    "integration_candidate_sha",
    "integration_id",
})


@dataclass(frozen=True)
class RemediationPredecessor:
    """Exact bounded predecessor identity for one canonical REMEDIATION RUN."""

    source_run_id: str
    review_id: str
    finding_id: str
    reviewed_sha: str


@dataclass(frozen=True)
class RemediationExecutionBase:
    """Exact operational candidate from which a prospective correction starts."""

    run_id: str
    candidate_sha: str
    version: int = 1
    kind: str = "CUMULATIVE"
    cumulative_tip_run_id: str | None = None
    cumulative_tip_candidate_sha: str | None = None
    authorized_main_sha: str | None = None
    integration_candidate_sha: str | None = None
    integration_id: str | None = None
    is_integrated: bool = False

    def as_dict(self) -> dict[str, Any]:
        if self.is_integrated:
            return {
                "version": self.version,
                "kind": self.kind,
                "cumulative_tip_run_id": self.cumulative_tip_run_id,
                "cumulative_tip_candidate_sha": self.cumulative_tip_candidate_sha,
                "authorized_main_sha": self.authorized_main_sha,
                "integration_candidate_sha": self.integration_candidate_sha,
                "integration_id": self.integration_id,
            }
        return {
            "run_id": self.run_id,
            "candidate_sha": self.candidate_sha,
        }


def _parse_remediation_execution_base(data: Any) -> RemediationExecutionBase:
    root = data if isinstance(data, Mapping) else None
    if root is None:
        raise TypeError("REMEDIATION execution_base must be a mapping")
    root_keys = set(root)
    if root_keys == _CANONICAL_EXECUTION_BASE_FIELDS:
        run_id = root.get("run_id")
        candidate_sha = root.get("candidate_sha")
        if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("REMEDIATION execution_base RUN identity is invalid")
        if not isinstance(candidate_sha, str) or not _SHA_PATTERN.fullmatch(candidate_sha):
            raise ValueError("REMEDIATION execution_base candidate SHA is invalid")
        return RemediationExecutionBase(run_id=run_id, candidate_sha=candidate_sha)
    if _CANONICAL_INTEGRATED_EXECUTION_BASE_FIELDS.issubset(root_keys) and root_keys.issubset(
        _CANONICAL_INTEGRATED_EXECUTION_BASE_FIELDS | {"run_id", "candidate_sha", "authorized_main_sha", "current_main_sha", "expected_main_sha"}
    ):
        version = root.get("version")
        if version != 1 or isinstance(version, bool):
            raise ValueError("REMEDIATION execution_base version is invalid")
        kind = root.get("kind")
        if kind not in ("INTEGRATED", "INTEGRATION"):
            raise ValueError("REMEDIATION execution_base kind is invalid")
        tip_run_id = root.get("cumulative_tip_run_id")
        if not isinstance(tip_run_id, str) or not _RUN_ID_PATTERN.fullmatch(tip_run_id):
            raise ValueError("REMEDIATION execution_base cumulative_tip_run_id is invalid")
        tip_candidate_sha = root.get("cumulative_tip_candidate_sha")
        if not isinstance(tip_candidate_sha, str) or not _SHA_PATTERN.fullmatch(tip_candidate_sha):
            raise ValueError("REMEDIATION execution_base cumulative_tip_candidate_sha is invalid")
        main_sha = root.get("authorized_main_sha") or root.get("current_main_sha") or root.get("expected_main_sha")
        if not isinstance(main_sha, str) or not _SHA_PATTERN.fullmatch(main_sha):
            raise ValueError("REMEDIATION execution_base authorized_main_sha is invalid")
        int_candidate_sha = root.get("integration_candidate_sha")
        if not isinstance(int_candidate_sha, str) or not _SHA_PATTERN.fullmatch(int_candidate_sha):
            raise ValueError("REMEDIATION execution_base integration_candidate_sha is invalid")
        int_id = root.get("integration_id")
        if not isinstance(int_id, str) or not int_id:
            raise ValueError("REMEDIATION execution_base integration_id is invalid")
        if "run_id" in root and root["run_id"] != tip_run_id:
            raise ValueError("REMEDIATION execution_base run_id does not match cumulative_tip_run_id")
        if "candidate_sha" in root and root["candidate_sha"] != int_candidate_sha:
            raise ValueError("REMEDIATION execution_base candidate_sha does not match integration_candidate_sha")
        return RemediationExecutionBase(
            run_id=tip_run_id,
            candidate_sha=int_candidate_sha,
            version=version,
            kind=kind,
            cumulative_tip_run_id=tip_run_id,
            cumulative_tip_candidate_sha=tip_candidate_sha,
            authorized_main_sha=main_sha,
            integration_candidate_sha=int_candidate_sha,
            integration_id=int_id,
            is_integrated=True,
        )
    raise ValueError("REMEDIATION execution_base fields do not match the contract")


def _parse_remediation_predecessor(data: Any) -> RemediationPredecessor:
    root = data if isinstance(data, Mapping) else None
    if root is None:
        raise TypeError("REMEDIATION predecessor must be a mapping")
    if set(root).difference(_CANONICAL_PREDECESSOR_FIELDS):
        raise ValueError("REMEDIATION predecessor contains unexpected fields")
    source_run_id = root.get("source_run_id")
    review_id = root.get("review_id")
    finding_id = root.get("finding_id")
    reviewed_sha = root.get("reviewed_sha")
    if not isinstance(source_run_id, str) or not _RUN_ID_PATTERN.fullmatch(source_run_id):
        raise ValueError("REMEDIATION predecessor source RUN identity is invalid")
    if not isinstance(review_id, str) or not review_id or "/" in review_id or "\\" in review_id:
        raise ValueError("REMEDIATION predecessor source REVIEW identity is invalid")
    if not isinstance(finding_id, str) or not finding_id or "/" in finding_id or "\\" in finding_id:
        raise ValueError("REMEDIATION predecessor selected finding identity is invalid")
    if not isinstance(reviewed_sha, str) or not _SHA_PATTERN.fullmatch(reviewed_sha):
        raise ValueError("REMEDIATION predecessor reviewed_sha is invalid")
    return RemediationPredecessor(
        source_run_id=source_run_id,
        review_id=review_id,
        finding_id=finding_id,
        reviewed_sha=reviewed_sha,
    )


def _load_authoritative_prior_result(
    state: RuntimePaths,
    task: Task,
    reviewed_sha: str,
    *,
    repo: Path,
) -> tuple[Result, str]:
    """Load one persisted primary or remediation result with canonical lineage."""

    matches: list[tuple[Result, str]] = []
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
                if "predecessor" in run_data:
                    pred = _parse_remediation_predecessor(run_data["predecessor"])
                    if pred.review_id != execution.review_id:
                        raise ValueError("predecessor review_id mismatch")
                    if pred.finding_id != execution.finding.id:
                        raise ValueError("predecessor finding_id mismatch")
                    if pred.reviewed_sha != execution.remediation.reviewed_sha:
                        raise ValueError("predecessor reviewed_sha mismatch")
                _validate_persisted_remediation_result(
                    repo=repo,
                    task=task,
                    execution=execution,
                    package=ResultPackage(result=result, evidence=evidence),
                    run_document=run_data,
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
        matches.append((result, run_id))

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
    run_document: Mapping[str, Any] | None = None,
) -> None:
    """Validate persisted remediation lineage against its actual result contract."""

    run = execution.run
    if run.task.id != task.task_id or run.task.revision != task.revision:
        raise ValueError("REMEDIATION RUN does not reference the supplied TASK")
    if run_document is not None and "execution_base" in run_document:
        execution_base = _parse_remediation_execution_base(
            run_document["execution_base"]
        )
        if execution_base.candidate_sha != run.base_sha:
            raise ValueError("REMEDIATION execution_base does not match RUN base_sha")
    elif run.base_sha != execution.remediation.reviewed_sha:
        raise ValueError("legacy REMEDIATION RUN base_sha does not match reviewed_sha")
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
    synchronize: bool = True,
    preflight_sha: str | None = None,
    dispatch_id: str | None = None,
    task_revision: int | None = None,
    task_blob_sha: str | None = None,
    task_commit_sha: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    model_source: str | None = None,
    effort_source: str | None = None,
    _migration_handoff: str | None = None,
    _successor_transport: str | None = None,
) -> RunSummary:
    """Execute a TASK and persist/transport deterministic pre-PASS failure facts."""

    root = resolve_repository(repo)
    if _successor_transport is not None:
        if _migration_handoff is not None:
            raise OperatorError("conflicting PRIMARY admission authorities")
        _validate_successor_execution(root, _successor_transport, task_id, executor,
                                      synchronize, preflight_sha, dispatch_id,
                                      task_revision, task_blob_sha, task_commit_sha)
    elif _migration_handoff is None:
        _require_no_active_migration(root)
    else:
        _validate_migration_execution(
            _require_migration_target(root, _migration_handoff),
            task_id=task_id, executor=executor, synchronize=synchronize,
            preflight_sha=preflight_sha, dispatch_id=dispatch_id,
            task_revision=task_revision, task_blob_sha=task_blob_sha,
            task_commit_sha=task_commit_sha,
        )
    state = runtime_paths(root)
    observation_tracker = RunObservationTracker(
        "PRIMARY", monotonic_clock=monotonic_clock
    )
    attempt = _RunAttempt()
    admission = _new_admission(
        "PRIMARY",
        phase="PRIMARY_SYNCHRONIZATION" if synchronize else "REPOSITORY_ADMISSION",
        reason_code=(
            "PRIMARY_SYNCHRONIZATION_REJECTED"
            if synchronize
            else "REPOSITORY_ADMISSION_REJECTED"
        ),
        task_id=task_id,
        executor=executor,
        dispatch_id=dispatch_id,
        requested_task_revision=task_revision,
        task_blob_sha=task_blob_sha,
        task_commit_sha=task_commit_sha,
    )
    try:
        return _run_task_impl(
            task_id, executor=executor, repo=root,
            native_runner=native_runner, verification_runner=verification_runner,
            attempt=attempt,
            observation_tracker=observation_tracker,
            synchronize=synchronize,
            preflight_sha=preflight_sha,
            dispatch_id=dispatch_id,
            task_revision=task_revision,
            task_blob_sha=task_blob_sha,
            task_commit_sha=task_commit_sha,
            model=model,
            reasoning_effort=reasoning_effort,
            model_source=model_source,
            effort_source=effort_source,
            admission=admission,
            migration_handoff=_migration_handoff,
            successor_transport=_successor_transport,
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
                    verification_subject_sha=attempt.verification_subject_sha,
                )
        else:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
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
                    interruption_phase=attempt.interruption_phase,
                    verification_subject_sha=attempt.verification_subject_sha,
                )
        else:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
            )
        raise


def _bind_and_persist_execution_profile(
    *,
    state: RuntimePaths,
    repo: Path,
    run_id: str,
    executor: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    model_source: str | None = None,
    effort_source: str | None = None,
) -> ResolvedExecutionProfile | None:
    """Resolve, validate, and durably persist one execution profile sidecar."""
    if not is_profile_managed_executor(executor):
        if any(
            value is not None
            for value in (model, reasoning_effort, model_source, effort_source)
        ):
            raise OperatorError(
                "model/effort options require a profile-managed Executor"
            )
        return None

    profiles_dir = state.execution_profiles or (state.root / "execution-profiles")
    profile_path = profiles_dir / f"{run_id}.json"
    if profile_path.is_file():
        try:
            existing = parse_execution_profile(profile_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise OperatorError(f"persisted execution profile is invalid: {exc}") from exc
        if existing.run_id != run_id or existing.executor != executor:
            raise OperatorError(
                f"persisted execution profile mismatch for RUN {run_id}: "
                f"profile.executor={existing.executor!r}, expected {executor!r}"
            )
        if any(
            value is not None
            for value in (model, reasoning_effort, model_source, effort_source)
        ):
            expected = bind_execution_profile(
                run_id=run_id,
                executor=executor,
                model=model,
                reasoning_effort=reasoning_effort,
                model_source=model_source,
                effort_source=effort_source,
                repo=repo,
                policy=_SOURCE_REPAIR_POLICY.get(),
            )
            if existing != expected:
                raise OperatorError(
                    f"persisted execution profile mismatch for RUN {run_id}"
                )
        try:
            policy = _SOURCE_REPAIR_POLICY.get() or load_execution_profile_policy(repo)
            validate_execution_profile(existing, policy, repo=repo)
        except ExecutionProfileError as exc:
            raise OperatorError(f"persisted execution profile is invalid: {exc}") from exc
        except Exception as exc:
            raise OperatorError(f"failed to validate persisted execution profile: {exc}") from exc
        return existing

    try:
        policy = _SOURCE_REPAIR_POLICY.get() or load_execution_profile_policy(repo)
        profile = bind_execution_profile(
            policy=policy,
            run_id=run_id,
            executor=executor,
            model=model,
            reasoning_effort=reasoning_effort,
            model_source=model_source,
            effort_source=effort_source,
            repo=repo,
        )
        persist_execution_profile(profile_path, profile)
    except ExecutionProfileConflictError as exc:
        raise OperatorError(f"execution profile conflict: {exc}") from exc
    except ExecutionProfileError as exc:
        raise OperatorError(f"execution profile resolution failed: {exc}") from exc
    except Exception as exc:
        raise OperatorError(f"failed to persist execution profile: {exc}") from exc

    if not profile_path.is_file():
        raise OperatorError(
            f"execution profile sidecar missing before native runner: {profile_path}"
        )
    return profile


def _authorization_profile(
    *,
    repo: Path,
    authorization_id: str,
    executor: str | None,
    existing: bool,
    bound_profile: ResolvedExecutionProfile | None,
    model: str | None,
    reasoning_effort: str | None,
    model_source: str | None,
    effort_source: str | None,
) -> ResolvedExecutionProfile | None:
    """Resolve a new authorization or reuse one exact durable profile."""

    requested = (model, reasoning_effort, model_source, effort_source)
    if executor is None:
        if any(value is not None for value in requested):
            raise OperatorError("model/effort options require a coding Executor")
        if bound_profile is not None:
            raise OperatorError("executor-less authorization has a profile binding")
        return None
    if existing:
        if not any(value is not None for value in requested):
            return bound_profile
        if bound_profile is None:
            raise OperatorError("historical authorization has no execution profile")
        if not all(value is not None for value in requested):
            raise OperatorError(
                "existing authorization requires the complete bound execution profile"
            )
        attempted = bind_execution_profile(
            run_id=authorization_id,
            executor=executor,
            model=model,
            reasoning_effort=reasoning_effort,
            model_source=model_source,
            effort_source=effort_source,
            repo=repo,
            policy=_SOURCE_REPAIR_POLICY.get(),
        )
        if attempted != bound_profile:
            raise OperatorError("authorization execution profile collision")
        return bound_profile
    return bind_execution_profile(
        run_id=authorization_id,
        executor=executor,
        model=model,
        reasoning_effort=reasoning_effort,
        model_source=model_source,
        effort_source=effort_source,
        repo=repo,
        policy=_SOURCE_REPAIR_POLICY.get(),
    )


def _run_task_impl(
    task_id: str,
    *,
    executor: str,
    repo: str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    attempt: _RunAttempt,
    observation_tracker: RunObservationTracker,
    synchronize: bool = True,
    preflight_sha: str | None = None,
    dispatch_id: str | None = None,
    task_revision: int | None = None,
    task_blob_sha: str | None = None,
    task_commit_sha: str | None = None,
    admission: dict[str, Any] | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    model_source: str | None = None,
    effort_source: str | None = None,
    migration_handoff: str | None = None,
    successor_transport: str | None = None,
) -> RunSummary:
    """Execute a stored TASK through the frozen kernel boundary."""

    root = resolve_repository(repo)
    admission = admission if admission is not None else _new_admission(
        "PRIMARY",
        phase="PRIMARY_SYNCHRONIZATION",
        reason_code="PRIMARY_SYNCHRONIZATION_REJECTED",
        task_id=task_id,
        executor=executor,
        dispatch_id=dispatch_id,
        requested_task_revision=task_revision,
        task_blob_sha=task_blob_sha,
        task_commit_sha=task_commit_sha,
    )
    if executor not in ("codex", "antigravity", "antigravity-minimax"):
        _set_admission_boundary(
            admission, "CANONICAL_CONTRACT_ADMISSION", "TASK_CONTRACT_REJECTED"
        )
        raise OperatorError(f"unsupported executor: {executor}")
    state = runtime_paths(root)

    with RepositoryLock(state.lock):
        if successor_transport is not None:
            _validate_successor_execution(root, successor_transport, task_id, executor,
                                          synchronize, preflight_sha, dispatch_id,
                                          task_revision, task_blob_sha, task_commit_sha)
        elif migration_handoff is None:
            _require_no_active_migration(root)
        else:
            _validate_migration_execution(
                _require_migration_target(root, migration_handoff),
                task_id=task_id, executor=executor, synchronize=synchronize,
                preflight_sha=preflight_sha, dispatch_id=dispatch_id,
                task_revision=task_revision, task_blob_sha=task_blob_sha,
                task_commit_sha=task_commit_sha,
            )
        if synchronize:
            _set_admission_boundary(
                admission,
                "PRIMARY_SYNCHRONIZATION",
                "PRIMARY_SYNCHRONIZATION_REJECTED",
            )
            _synchronize_primary_branch(root)
        else:
            _set_admission_boundary(
                admission, "REPOSITORY_ADMISSION", "REPOSITORY_ADMISSION_REJECTED"
            )
            if _git(root, "status", "--porcelain"):
                raise OperatorError("repository dirty")
            try:
                branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
            except OperatorError as exc:
                raise OperatorError("repository HEAD is detached") from exc
            if branch != "main":
                raise OperatorError("current branch is not main")
        current_sha = _git(root, "rev-parse", "HEAD")
        if preflight_sha is not None and current_sha != preflight_sha:
            raise OperatorError("current HEAD does not match preflight state")
        admission["current_head_sha"] = current_sha
        if any(
            value is not None
            for value in (task_revision, task_blob_sha, task_commit_sha)
        ):
            _set_admission_boundary(
                admission, "TASK_ADMISSION", "AUTHORIZED_TASK_IDENTITY_MISMATCH"
            )
            task = _admit_authorized_task_identity(
                root,
                task_id=task_id,
                task_revision=task_revision,
                task_blob_sha=task_blob_sha,
                task_commit_sha=task_commit_sha,
                current_head=current_sha,
                admission=admission,
            )
        else:
            _set_admission_boundary(
                admission, "TASK_ADMISSION", "TASK_CONTRACT_REJECTED"
            )
            task = load_task(root, task_id)
        _bind_admission_task(admission, task)
        base_sha = current_sha
        _set_admission_boundary(
            admission, "RUN_RESERVATION", "RUN_NAMESPACE_CONFLICT"
        )
        remote_run_ids = _remote_run_reservations(
            root, task, admission=admission
        )
        run_id = next_run_id(task_id, state.runs, reserved=remote_run_ids)
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
        execution_profile = _bind_and_persist_execution_profile(
            state=state,
            repo=root,
            run_id=run_id,
            executor=executor,
            model=model,
            reasoning_effort=reasoning_effort,
            model_source=model_source,
            effort_source=effort_source,
        )
        if dispatch_id is not None:
            bind_dispatch_run(
                repo=root,
                state_root=state.root,
                dispatch_id=dispatch_id,
                task_id=task_id,
                executor=executor,
                run_id=run_id,
                task_revision=task_revision,
                task_blob_sha=task_blob_sha,
                task_commit_sha=task_commit_sha,
                execution_profile=execution_profile,
            )
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
            execution_profile=execution_profile,
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
        except (
            CodexOutputError,
            AntigravityOutputError,
            AntigravityMinimaxOutputError,
            ArtifactValidationError,
        ) as exc:
            raise OperatorError(f"invalid structural ResultPackage: {exc}") from exc
        except CodexExecutionError as exc:
            raise OperatorError(f"Codex invocation failed: {exc}") from exc
        except AntigravityExecutionError as exc:
            raise OperatorError(str(exc)) from exc
        except AntigravityMinimaxExecutionError as exc:
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
    verification_subject_sha: str | None = None,
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
            verification_subject_sha=verification_subject_sha,
        )
    except Exception:
        # Delegation setup is also subordinate to the original failure.
        return


def run_repair(
    failed_run_id: str, *, executor: str | None, repo: str | Path | None = None,
    repair: Mapping[str, Any] | str | Path | None = None,
    required_repair_sha: str | None = None,
    repair_dispatch_id: str | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
    model: str | None = None,
    reasoning_effort: str | None = None,
    model_source: str | None = None,
    effort_source: str | None = None,
) -> RepairSummary:
    """Accept and execute one GitHub-authored REPAIR as a continuation RUN."""

    root = resolve_repository(repo)
    if repair_dispatch_id is not None and required_repair_sha is None:
        raise OperatorError("repair dispatch requires the exact canonical REPAIR SHA")
    state = runtime_paths(root)
    observation_tracker = RunObservationTracker(
        "REPAIR", monotonic_clock=monotonic_clock
    )
    attempt = _RunAttempt()
    admission = _new_admission(
        "REPAIR",
        phase="FAILED_RUN_RESOLUTION",
        reason_code="CANONICAL_LINEAGE_MISSING",
        executor=executor,
        failed_run_id=failed_run_id,
        repair_dispatch_id=repair_dispatch_id,
    )
    try:
        return _run_repair_impl(
            failed_run_id, executor=executor, repo=root, repair=repair,
            required_repair_sha=required_repair_sha,
            repair_dispatch_id=repair_dispatch_id,
            model=model,
            reasoning_effort=reasoning_effort,
            model_source=model_source,
            effort_source=effort_source,
            native_runner=native_runner,
            verification_runner=verification_runner,
            attempt=attempt,
            observation_tracker=observation_tracker,
            admission=admission,
        )
    except KeyboardInterrupt as original:
        if attempt.run_path is not None:
            if not (state.results / attempt.run_path.name).is_file():
                _persist_repair_failure(
                    root, state=state, attempt=attempt, failure=original,
                    observation_tracker=observation_tracker,
                    interruption_phase=attempt.interruption_phase,
                )
        else:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
            )
        raise
    except Exception as original:
        if attempt.run_path is not None:
            if not (state.results / attempt.run_path.name).is_file():
                try:
                    _persist_repair_failure(
                        root, state=state, attempt=attempt, failure=original,
                        observation_tracker=observation_tracker,
                        interruption_phase=attempt.interruption_phase,
                    )
                except Exception:
                    pass
        else:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
            )
        raise
    finally:
        _remove_historical_workspace(root, attempt.historical_workspace)


def _persist_repair_failure(
    control_repo: Path,
    *,
    state: RuntimePaths,
    attempt: _RunAttempt,
    failure: BaseException,
    observation_tracker: RunObservationTracker,
    interruption_phase: str | None = None,
) -> None:
    if attempt.run_path is None or attempt.subject_repo is None or attempt.task is None:
        return
    run_data = json.loads(attempt.run_path.read_text(encoding="utf-8"))
    transfer_error = _prepare_historical_failure_transport(control_repo, attempt)
    persist_failure(
        attempt.subject_repo,
        state=state,
        task=attempt.task,
        run=_run_from_data(run_data),
        run_path=attempt.run_path,
        failure=failure,
        observation_tracker=observation_tracker,
        interruption_phase=interruption_phase,
        transport=transfer_error is None,
        transport_repo=control_repo,
        verification_subject_sha=attempt.verification_subject_sha,
    )
    _persist_historical_transfer_error(
        state, attempt.run_path.stem, transfer_error
    )


def _remove_historical_workspace(
    _control_repo: Path, workspace: Path | None
) -> None:
    if workspace is None or not workspace.exists():
        return

    def make_writable_and_retry(remove, path: str, exc_info) -> None:
        error = exc_info[1]
        if not isinstance(error, PermissionError):
            raise error
        current_mode = os.stat(path, follow_symlinks=False).st_mode
        writable_mode = current_mode | stat.S_IREAD | stat.S_IWRITE
        if stat.S_ISDIR(current_mode):
            writable_mode |= stat.S_IEXEC
        os.chmod(path, writable_mode)
        remove(path)

    shutil.rmtree(workspace, onerror=make_writable_and_retry)
    if workspace.exists():
        raise OperatorError("historical subject cleanup failed")


def _prepare_historical_terminalization(
    control_repo: Path, attempt: _RunAttempt
) -> None:
    """Make an isolated historical candidate visible to control transport."""

    if attempt.historical_workspace is None:
        return
    if attempt.subject_repo is None:
        raise OperatorError("historical subject is unavailable for terminalization")
    _transfer_historical_candidate_objects(control_repo, attempt.subject_repo)


def _prepare_historical_failure_transport(
    control_repo: Path, attempt: _RunAttempt
) -> OperatorError | None:
    """Prepare candidate transport without masking the admitted failure."""

    try:
        _prepare_historical_terminalization(control_repo, attempt)
    except OperatorError as exc:
        return exc
    return None


def _persist_historical_transfer_error(
    state: RuntimePaths, run_id: str, error: OperatorError | None
) -> None:
    if error is None:
        return
    try:
        _write_json(
            state.failures / f"{run_id}.transport.json",
            {"run_id": run_id, "error": str(error)},
        )
    except (OSError, UnicodeError):
        # Failure persistence remains subordinate to the admitted failure.
        return


def _transfer_historical_candidate_objects(
    control_repo: Path, subject_repo: Path
) -> None:
    """Copy one candidate's object graph locally without creating control refs."""

    control_head = _git(control_repo, "rev-parse", "HEAD")
    control_branch = _git(
        control_repo, "rev-parse", "--abbrev-ref", "HEAD"
    )
    _require_control_checkout_unchanged(
        control_repo, head_sha=control_head, branch=control_branch
    )
    candidate_sha = _git(
        subject_repo, "rev-parse", "--verify", "HEAD^{commit}"
    )
    if candidate_sha != _git(subject_repo, "rev-parse", "HEAD"):
        raise OperatorError("historical candidate commit mismatch")

    try:
        with tempfile.TemporaryFile() as pack_stream:
            packed = subprocess.run(
                (
                    "git",
                    "-C",
                    str(subject_repo),
                    "pack-objects",
                    "--stdout",
                    "--revs",
                ),
                input=f"{candidate_sha}\n".encode("ascii"),
                stdout=pack_stream,
                stderr=subprocess.PIPE,
                check=False,
            )
            packed_stderr = _decode_utf8(packed.stderr)
            if packed.returncode != 0:
                raise OperatorError(
                    "historical candidate object packing failed: "
                    f"{packed_stderr.strip()}"
                )
            pack_stream.seek(0)
            unpacked = subprocess.run(
                (
                    "git",
                    "-C",
                    str(control_repo),
                    "unpack-objects",
                    "-r",
                ),
                stdin=pack_stream,
                capture_output=True,
                check=False,
            )
            unpacked_stdout = _decode_utf8(unpacked.stdout)
            unpacked_stderr = _decode_utf8(unpacked.stderr)
            if unpacked.returncode != 0:
                detail = unpacked_stderr.strip() or unpacked_stdout.strip()
                raise OperatorError(
                    f"historical candidate object transfer failed: {detail}"
                )
    except (OSError, UnicodeError) as exc:
        raise OperatorError(
            f"historical candidate object transfer failed: {exc}"
        ) from exc

    resolved_candidate = _git(
        control_repo,
        "rev-parse",
        "--verify",
        f"{candidate_sha}^{{commit}}",
    )
    if resolved_candidate != candidate_sha:
        raise OperatorError("historical candidate is not object-exact in control")
    _require_control_checkout_unchanged(
        control_repo, head_sha=control_head, branch=control_branch
    )


def retry_transport(run_id: str, *, repo: str | Path | None = None) -> None:
    """Retry GitHub transport for persisted terminal state without execution."""

    root = resolve_repository(repo)
    state = runtime_paths(root)
    run_path = state.runs / f"{run_id}.json"
    result_path = state.results / f"{run_id}.json"
    failure_path = state.failures / f"{run_id}.json"
    observation_path = state.observations / f"{run_id}.json"
    optional_observation = observation_path if observation_path.is_file() else None
    preverification_path = state.preverification / f"{run_id}.json"
    optional_preverification = (
        preverification_path if preverification_path.is_file() else None
    )
    profile_path = (
        state.execution_profiles or (state.root / "execution-profiles")
    ) / f"{run_id}.json"
    optional_profile = profile_path if profile_path.is_file() else None
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
                execution_profile_path=optional_profile,
            )
        elif failure_path.is_file():
            payload = json.loads(failure_path.read_text(encoding="utf-8"))
            publish_candidate = payload.get("candidate", {}).get("transportable") is True
            failure_preverification = (
                optional_preverification
                if payload.get("phase") == "VERIFICATION"
                else None
            )
            if failure_preverification is not None:
                task_ref = payload.get("task")
                if not isinstance(task_ref, Mapping):
                    raise OperatorError(
                        "transport retry has invalid FAILURE TASK binding"
                    )
                try:
                    retry_task = parse_task(
                        _git(
                            root,
                            "show",
                            f"{payload['failed_head_sha']}:"
                            f".ai/tasks/{task_ref['id']}.yaml",
                            strip_stdout=False,
                        )
                    )
                    if dict(task_ref) != {
                        "id": retry_task.task_id,
                        "revision": retry_task.revision,
                    }:
                        raise ArtifactValidationError(
                            "FAILURE TASK does not match historical subject TASK"
                        )
                    validate_preverification_candidate(
                        failure_preverification.read_bytes(),
                        task=retry_task,
                        run_id=run_id,
                        subject_sha=payload["failed_head_sha"],
                    )
                except (
                    ArtifactValidationError,
                    KeyError,
                    OSError,
                    TypeError,
                    TaskValidationError,
                ) as exc:
                    raise OperatorError(
                        f"transport retry has invalid pre-verification candidate: {exc}"
                    ) from exc
            transport_failure(
                root, run_id=run_id, head_sha=payload["failed_head_sha"],
                run_path=run_path, failure_path=failure_path,
                publish_candidate=publish_candidate,
                lineage_path=(state.repairs / f"{run_id}.json")
                if (state.repairs / f"{run_id}.json").is_file() else None,
                observation_path=optional_observation,
                preverification_path=failure_preverification,
                execution_profile_path=optional_profile,
            )
        else:
            raise OperatorError(f"persisted terminal state not found: {run_id}")
    except (ReviewTransportError, OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        if isinstance(exc, OperatorError):
            raise
        raise OperatorError(f"transport retry failed: {exc}") from exc


def recover_primary(
    source_run_id: str,
    *,
    repo: str | Path | None = None,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
) -> RecoverySummary:
    """Re-admit one exact successful candidate from a conflicting PRIMARY RUN."""

    root = resolve_repository(repo)
    state = runtime_paths(root)
    observation_tracker = RunObservationTracker(
        "PRIMARY", monotonic_clock=monotonic_clock
    )
    attempt = _RunAttempt()
    admission = _new_admission(
        "RECOVER_PRIMARY",
        phase="FAILED_RUN_RESOLUTION",
        reason_code="CANONICAL_LINEAGE_MISSING",
        source_run_id=source_run_id,
    )
    try:
        return _recover_primary_impl(
            source_run_id,
            repo=root,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
            attempt=attempt,
            admission=admission,
        )
    except KeyboardInterrupt as original:
        _persist_primary_recovery_failure(
            root,
            state=state,
            attempt=attempt,
            failure=original,
            observation_tracker=observation_tracker,
            interruption_phase=attempt.interruption_phase,
        )
        if attempt.run_path is None:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
            )
        raise
    except Exception as original:
        try:
            _persist_primary_recovery_failure(
                root,
                state=state,
                attempt=attempt,
                failure=original,
                observation_tracker=observation_tracker,
                interruption_phase=attempt.interruption_phase,
            )
        except Exception:
            pass
        if attempt.run_path is None:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
            )
        raise
    finally:
        _remove_historical_workspace(root, attempt.historical_workspace)


def _persist_primary_recovery_failure(
    control_repo: Path,
    *,
    state: RuntimePaths,
    attempt: _RunAttempt,
    failure: BaseException,
    observation_tracker: RunObservationTracker,
    interruption_phase: str | None = None,
) -> None:
    if (
        attempt.run_path is None
        or attempt.subject_repo is None
        or attempt.task is None
        or (state.results / attempt.run_path.name).is_file()
    ):
        return
    run_data = json.loads(attempt.run_path.read_text(encoding="utf-8"))
    transfer_error = _prepare_historical_failure_transport(control_repo, attempt)
    persist_failure(
        attempt.subject_repo,
        state=state,
        task=attempt.task,
        run=_run_from_data(run_data),
        run_path=attempt.run_path,
        failure=failure,
        observation_tracker=observation_tracker,
        interruption_phase=interruption_phase,
        transport=transfer_error is None,
        transport_repo=control_repo,
        verification_subject_sha=attempt.verification_subject_sha,
    )
    _persist_historical_transfer_error(
        state, attempt.run_path.stem, transfer_error
    )


def _recover_primary_impl(
    source_run_id: str,
    *,
    repo: Path,
    verification_runner: VerificationRunner,
    observation_tracker: RunObservationTracker,
    attempt: _RunAttempt,
    admission: dict[str, Any],
) -> RecoverySummary:
    state = runtime_paths(repo)
    with RepositoryLock(state.lock):
        _set_admission_boundary(
            admission, "REPOSITORY_ADMISSION", "REPOSITORY_ADMISSION_REJECTED"
        )
        control_head = _git(repo, "rev-parse", "HEAD")
        admission["control_head_sha"] = control_head
        if _git(repo, "status", "--porcelain"):
            raise OperatorError("repository dirty")
        _set_admission_boundary(
            admission, "FAILED_RUN_RESOLUTION", "CANONICAL_LINEAGE_MISSING"
        )
        resolved_admission = _resolve_primary_recovery_admission(
            repo, source_run_id, admission=admission
        )
        source_run = resolved_admission.source_run
        task = resolved_admission.task
        _bind_admission_task(admission, task)
        candidate_sha = resolved_admission.structural_package.result.head_sha
        _set_admission_boundary(
            admission,
            "HISTORICAL_SUBJECT_ADMISSION",
            "HISTORICAL_SUBJECT_REJECTED",
        )
        subject_repo = _create_historical_workspace(repo, candidate_sha)
        attempt.bind_subject(subject_repo, task, subject_repo)
        if load_task(subject_repo, task.task_id) != task:
            raise OperatorError("historical candidate TASK content mismatch")
        if _git(subject_repo, "rev-parse", "HEAD") != candidate_sha:
            raise OperatorError("historical candidate HEAD mismatch")
        if _git(subject_repo, "status", "--porcelain"):
            raise OperatorError("historical candidate workspace is dirty")
        if _git(repo, "rev-parse", "HEAD") != control_head:
            raise OperatorError("recovery changed control HEAD before admission")
        if _git(repo, "status", "--porcelain"):
            raise OperatorError("recovery changed control repository before admission")

        _set_admission_boundary(
            admission, "RUN_RESERVATION", "RUN_NAMESPACE_CONFLICT"
        )
        run_id = next_run_id(
            task.task_id,
            state.runs,
            reserved=resolved_admission.remote_run_ids,
        )
        run = Run.from_task(
            run_id=run_id,
            task=task,
            executor=source_run.executor,
            base_sha=source_run.base_sha,
            workspace=str(subject_repo),
        )
        run_path = state.runs / f"{run_id}.json"
        _write_json(run_path, asdict(run))
        attempt.bind_run(run_path)
        observation_tracker.admit(run)
        completion = RuntimeCompletion(
            repo=subject_repo,
            state=state,
            task=task,
            run=run,
            run_path=run_path,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
            error_type=OperatorError,
            transport_repo=repo,
        )
        attempt.bind_completion(completion)
        _prepare_historical_terminalization(repo, attempt)
        outcome = completion.complete(
            resolved_admission.structural_package,
            primary_completion_policy(task, base_sha=source_run.base_sha),
        )
        if _git(repo, "rev-parse", "HEAD") != control_head:
            raise OperatorError("recovery changed control HEAD")
        if _git(repo, "status", "--porcelain"):
            raise OperatorError("recovery changed control repository")
        return RecoverySummary(
            task_id=task.task_id,
            source_run_id=source_run_id,
            run_id=run_id,
            executor=source_run.executor,
            base_sha=source_run.base_sha,
            head_sha=outcome.head_sha,
            result_path=outcome.result_path,
        )


def _resolve_primary_recovery_admission(
    repo: Path,
    source_run_id: str,
    *,
    admission: dict[str, Any] | None = None,
) -> _PrimaryRecoveryAdmission:
    try:
        remote = resolve_remote_primary_recovery(repo, run_id=source_run_id)
    except ReviewTransportError as exc:
        if admission is not None:
            observed_refs = getattr(exc, "observed_refs", ())
            if isinstance(observed_refs, tuple):
                _record_observed_refs(admission, observed_refs)
        raise OperatorError(f"PRIMARY recovery source rejected: {exc}") from exc
    if admission is not None:
        _record_observed_refs(admission, remote.observed_refs)
        _set_admission_boundary(
            admission,
            "CANONICAL_CONTRACT_ADMISSION",
            "CANONICAL_LINEAGE_INVALID",
        )
    try:
        success_data = _decode_remote_mapping(remote.success_run, "successful RUN")
        failure_run_data = _decode_remote_mapping(remote.failure_run, "failed RUN")
        if "kind" in success_data or "kind" in failure_run_data:
            raise ValueError("recovery accepts only PRIMARY RUN records")
        source_run = _run_from_data(success_data)
        failed_run = _run_from_data(failure_run_data)
        if source_run.run_id != source_run_id or failed_run.run_id != source_run_id:
            raise ValueError("terminal RUN identity mismatch")
        expected_task = {
            "id": source_run.task.id,
            "revision": source_run.task.revision,
        }
        if {
            "id": failed_run.task.id,
            "revision": failed_run.task.revision,
        } != expected_task:
            raise ValueError("terminal TASK identity mismatch")
        if source_run.base_sha != failed_run.base_sha:
            raise ValueError("conflicting terminal RUN base mismatch")

        failure = _decode_remote_mapping(remote.failure, "FAILURE")
        if (
            failure.get("kind") != "FAILURE"
            or failure.get("run_id") != source_run_id
            or not isinstance(failure.get("task"), Mapping)
            or dict(failure["task"]) != expected_task
            or failure.get("executor") != failed_run.executor
            or failure.get("base_sha") != failed_run.base_sha
            or not isinstance(failure.get("failed_head_sha"), str)
            or not failure.get("failed_head_sha")
        ):
            raise ValueError("canonical FAILURE binding mismatch")
        if not _git_is_ancestor(
            repo, failed_run.base_sha, failure["failed_head_sha"]
        ):
            raise ValueError("failed terminal head does not descend from RUN base")

        task_source = read_remote_task(
            repo,
            commit_sha=remote.candidate_sha,
            task_id=source_run.task.id,
        )
        task = parse_task(task_source.decode("utf-8", errors="strict"))
        if task.task_id != source_run.task.id or task.revision != source_run.task.revision:
            raise ValueError("historical TASK identity or revision mismatch")

        result_data = _decode_remote_mapping(remote.result, "successful ResultPackage")
        result = validate_result(result_data["result"])
        evidence_data = result_data["evidence"]
        if not isinstance(evidence_data, list):
            raise TypeError("successful ResultPackage evidence must be a list")
        evidence = tuple(validate_evidence(item) for item in evidence_data)
        validate_result_package(
            task=task,
            run=source_run,
            result=result,
            evidence=evidence,
        )
        if result.head_sha != remote.candidate_sha:
            raise ValueError("successful RESULT head does not match candidate ref")
        if result.unresolved:
            raise ValueError("successful RESULT has unresolved items")
        satisfied = {
            acceptance_id
            for claim in result.claims
            for acceptance_id in claim.satisfies
        }
        missing = [item.id for item in task.acceptance if item.id not in satisfied]
        if missing:
            raise ValueError(
                "successful RESULT lacks TASK acceptance coverage: "
                + ", ".join(missing)
            )
        if not _git_is_ancestor(repo, source_run.base_sha, remote.candidate_sha):
            raise ValueError("successful candidate does not descend from RUN base")
        changed_files = _committed_changed_files(
            repo, source_run.base_sha, remote.candidate_sha
        )
        if changed_files != set(result.changed_files):
            raise ValueError("successful RESULT changed_files mismatch")
        outside_scope = changed_files.difference(task.scope.modify)
        if outside_scope:
            raise ValueError(
                "successful candidate changed paths outside TASK.scope.modify: "
                + ", ".join(sorted(outside_scope))
            )
        structural_result = replace(
            result,
            claims=tuple(replace(claim, evidence=()) for claim in result.claims),
        )
        return _PrimaryRecoveryAdmission(
            task=task,
            source_run=source_run,
            structural_package=ResultPackage(
                result=structural_result,
                evidence=(),
            ),
            remote_run_ids=remote.remote_run_ids,
        )
    except (
        ArtifactValidationError,
        KeyError,
        OperatorError,
        ReviewTransportError,
        TaskValidationError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        if isinstance(exc, OperatorError):
            raise
        raise OperatorError(f"PRIMARY recovery source rejected: {exc}") from exc


def _resolve_local_repair_remediation_origin(
    state: RuntimePaths,
    *,
    failed_run_id: str,
    task: Task,
    failure: Mapping[str, Any],
) -> tuple[Mapping[str, Any], RemediationExecution] | None:
    """Recover an exact REMEDIATION origin without trusting REPAIR prose."""

    current_run_id = failed_run_id
    seen: set[str] = set()
    while current_run_id not in seen:
        seen.add(current_run_id)
        run_path = state.runs / f"{current_run_id}.json"
        if not run_path.is_file():
            raise OperatorError("persisted failed RUN lineage not found")
        try:
            run_data = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OperatorError(f"invalid persisted failed RUN lineage: {exc}") from exc
        if not isinstance(run_data, Mapping):
            raise OperatorError("invalid persisted failed RUN lineage")
        try:
            if run_data.get("kind") == "REMEDIATION":
                execution = _remediation_execution_from_data(run_data["execution"])
                if (
                    execution.run.run_id != current_run_id
                    or execution.run.task.id != task.task_id
                    or execution.run.task.revision != task.revision
                    or not execution.remediation.affected_verification
                ):
                    raise ValueError("REMEDIATION origin identity mismatch")
                if (
                    current_run_id == failed_run_id
                    and (
                        failure.get("run_id") != current_run_id
                        or failure.get("base_sha") != execution.run.base_sha
                    )
                ):
                    raise ValueError("FAILURE does not match failed REMEDIATION RUN")
                return run_data, execution
            if "kind" in run_data:
                raise ValueError("unknown failed RUN kind")
            run = _run_from_data(run_data)
            if (
                run.run_id != current_run_id
                or run.task.id != task.task_id
                or run.task.revision != task.revision
            ):
                raise ValueError("failed RUN identity mismatch")
            if (
                current_run_id == failed_run_id
                and (
                    failure.get("run_id") != current_run_id
                    or failure.get("base_sha") != run.base_sha
                )
            ):
                raise ValueError("FAILURE does not match failed RUN")
        except (KeyError, TypeError, ValueError, ReviewValidationError) as exc:
            raise OperatorError(f"invalid persisted failed RUN lineage: {exc}") from exc

        repair_path = state.repairs / f"{current_run_id}.json"
        if not repair_path.is_file():
            return None
        try:
            lineage = json.loads(repair_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OperatorError(f"invalid persisted REPAIR lineage: {exc}") from exc
        task_data = lineage.get("task") if isinstance(lineage, Mapping) else None
        predecessor = lineage.get("failed_run_id") if isinstance(lineage, Mapping) else None
        if (
            not isinstance(lineage, Mapping)
            or lineage.get("run") != dict(run_data)
            or not isinstance(task_data, Mapping)
            or task_data.get("task_id") != task.task_id
            or task_data.get("revision") != task.revision
            or not isinstance(predecessor, str)
            or not predecessor
        ):
            raise OperatorError("invalid persisted REPAIR lineage")
        current_run_id = predecessor
    raise OperatorError("cyclic persisted REPAIR lineage")


def _resolve_repair_admission(
    failed_run_id: str,
    *,
    repo: Path,
    state: RuntimePaths,
    repair: Mapping[str, Any] | str | Path | None,
    required_repair_sha: str | None = None,
    admission: dict[str, Any],
    remote_repo: Path | None = None,
) -> _RepairAdmission:
    """Resolve the shared REPAIR contract through the last pre-RUN boundary."""

    _set_admission_boundary(
        admission, "FAILED_RUN_RESOLUTION", "CANONICAL_LINEAGE_INVALID"
    )
    if any(
        json.loads(path.read_text(encoding="utf-8")).get("failed_run_id")
        == failed_run_id
        for path in state.repairs.glob("*.json")
    ):
        raise OperatorError("REPAIR has already been accepted for failed RUN")
    failure_path = state.failures / f"{failed_run_id}.json"
    local_failure: Any = None
    if failure_path.is_file():
        local_failure = json.loads(failure_path.read_text(encoding="utf-8"))
        if (
            not isinstance(local_failure, Mapping)
            or local_failure.get("kind") != "FAILURE"
            or local_failure.get("run_id") != failed_run_id
        ):
            raise OperatorError("invalid persisted FAILURE lineage")
    current_head = _git(repo, "rev-parse", "HEAD")
    admission["current_head_sha"] = current_head
    historical = (
        local_failure is None
        or local_failure.get("failed_head_sha") != current_head
    )
    remote_run_ids: tuple[str, ...] = ()
    transported_preverification: bytes | None = None
    if historical:
        _set_admission_boundary(
            admission, "FAILED_RUN_RESOLUTION", "CANONICAL_LINEAGE_MISSING"
        )
        resolved_admission = _resolve_historical_repair_admission(
            remote_repo or repo, failed_run_id, admission=admission
        )
        failure = resolved_admission.failure
        task = resolved_admission.task
        root_base_sha = resolved_admission.root_base_sha
        result_base_sha = resolved_admission.result_base_sha
        remote_run_ids = resolved_admission.remote_run_ids
        transported_preverification = resolved_admission.preverification
        origin_affected_verification = (
            resolved_admission.origin_affected_verification
        )
        if local_failure is not None and dict(local_failure) != dict(failure):
            raise OperatorError("local and canonical remote FAILURE conflict")
    else:
        failure = local_failure
        assert isinstance(failure, Mapping)
        _set_admission_boundary(
            admission, "TASK_ADMISSION", "TASK_CONTRACT_REJECTED"
        )
        task = load_task(repo, failure["task"]["id"])
        continuation_of = failure.get("continuation_of")
        origin = _resolve_local_repair_remediation_origin(
            state, failed_run_id=failed_run_id, task=task, failure=failure
        )
        origin_affected_verification = (
            () if origin is None else origin[1].remediation.affected_verification
        )
        if origin is not None:
            origin_run_data, execution = origin
            if (
                "execution_base" not in origin_run_data
                and execution.run.base_sha != execution.remediation.reviewed_sha
            ):
                raise OperatorError(
                    "legacy REMEDIATION origin base does not match reviewed SHA"
                )
            root_base_sha = _derive_remediation_source_root(
                repo, task=task, execution=execution
            )
            result_base_sha = _repair_result_base_from_origin(
                repo,
                task=task,
                run_data=origin_run_data,
                run=execution.run,
                root_base_sha=root_base_sha,
            )
        elif continuation_of is None:
            root_base_sha = failure.get("base_sha")
            result_base_sha = root_base_sha
        if continuation_of is not None:
            prior_execution_path = state.repairs / f"{failed_run_id}.json"
            if not prior_execution_path.is_file():
                raise OperatorError("persisted REPAIR lineage not found")
            prior_execution = json.loads(
                prior_execution_path.read_text(encoding="utf-8")
            )
            if prior_execution.get("failed_run_id") != continuation_of:
                raise OperatorError("invalid persisted REPAIR lineage")
            persisted_root = prior_execution.get("root_base_sha")
            persisted_result_base = prior_execution.get(
                "result_base_sha", persisted_root
            )
            if origin is None:
                root_base_sha = persisted_root
                result_base_sha = persisted_result_base
            elif (
                persisted_root != root_base_sha
                or persisted_result_base != result_base_sha
            ):
                raise OperatorError("conflicting REPAIR origin lineage")
        if not isinstance(root_base_sha, str) or not root_base_sha:
            raise OperatorError("invalid original TASK root lineage")
        if not isinstance(result_base_sha, str) or not result_base_sha:
            raise OperatorError("invalid REPAIR result-base lineage")
    _bind_admission_task(admission, task)
    if isinstance(failure.get("failed_head_sha"), str):
        admission["failed_head_sha"] = failure["failed_head_sha"]
    candidate = failure.get("candidate")
    if not isinstance(candidate, Mapping) or candidate.get("repairable") is not True:
        raise OperatorError("failed candidate is not safely bound for REPAIR")
    _set_admission_boundary(
        admission, "CANONICAL_CONTRACT_ADMISSION", "TASK_CONTRACT_REJECTED"
    )
    canonical_authorization = None
    if required_repair_sha is not None or repair is None:
        _set_admission_boundary(
            admission,
            "CANONICAL_CONTRACT_ADMISSION",
            "CANONICAL_LINEAGE_INVALID",
        )
        try:
            canonical_authorization = resolve_remote_repair_authorization(
                remote_repo or repo, failed_run_id
            )
        except ReviewTransportError as exc:
            raise OperatorError(
                "canonical REPAIR selector is unavailable"
            ) from exc
        _record_observed_refs(
            admission,
            ((canonical_authorization.ref, canonical_authorization.commit_sha),),
        )
        if (
            required_repair_sha is not None
            and canonical_authorization.commit_sha != required_repair_sha
        ):
            raise OperatorError("canonical REPAIR selector changed after observation")
    if repair is None:
        try:
            assert canonical_authorization is not None
            repair_data: Any = json.loads(canonical_authorization.repair)
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
    if canonical_authorization is not None:
        canonical_data = json.loads(canonical_authorization.repair)
        if not isinstance(canonical_data, Mapping) or dict(repair_data) != dict(canonical_data):
            raise OperatorError("REPAIR does not match current canonical authorization")
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
    if task.revision != failure["task"]["revision"]:
        raise OperatorError("TASK revision does not match failed RUN")
    action = repair_data.get("action")
    if action not in (
        "CODE_FIX", "NO_CHANGE", "CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE"
    ):
        raise OperatorError(
            "REPAIR action must be CODE_FIX, NO_CHANGE, "
            "CONTINUE_IMPLEMENTATION, or FINALIZE_CANDIDATE"
        )
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
    if action == "FINALIZE_CANDIDATE" and scope:
        raise OperatorError(
            "FINALIZE_CANDIDATE REPAIR modification scope must be empty"
        )
    if action in ("CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE"):
        if action == "CONTINUE_IMPLEMENTATION" and not scope:
            raise OperatorError("CONTINUE_IMPLEMENTATION REPAIR modification scope is empty")
        if failure.get("phase") not in ("EXECUTION", "COMPLETION_GATE"):
            raise OperatorError(f"{action} requires a pre-verification failure")
        if not _repair_action_structure(failure)[action]:
            raise OperatorError(f"{action} requires a clean transportable candidate")
    admission["action"] = action

    reusable_package = None
    if action == "NO_CHANGE":
        _set_admission_boundary(
            admission, "REUSABLE_STATE_ADMISSION", "REUSABLE_STATE_REJECTED"
        )
        local_preverification = _read_optional_bytes(
            state.preverification / f"{failed_run_id}.json"
        )
        reusable_source = (
            transported_preverification
            if historical
            else local_preverification
        )
        reusable_package = _eligible_reusable_repair_package(
            reusable_source,
            task=task,
            failed_run_id=failed_run_id,
            failure=failure,
            action=action,
            scope=scope,
            local_content=local_preverification if historical else None,
            repo=(remote_repo or repo) if historical else repo,
            root_base_sha=root_base_sha,
            result_base_sha=result_base_sha,
        )

    return _RepairAdmission(
        failure=failure,
        task=task,
        root_base_sha=root_base_sha,
        result_base_sha=result_base_sha,
        remote_run_ids=remote_run_ids,
        historical=historical,
        repair=repair_data,
        authorization_sha=(
            canonical_authorization.commit_sha
            if canonical_authorization is not None
            else required_repair_sha
        ),
        action=action,
        scope=scope,
        reusable_package=reusable_package,
        origin_affected_verification=origin_affected_verification,
    )


def _run_repair_impl(
    failed_run_id: str, *, executor: str | None, repo: Path,
    repair: Mapping[str, Any] | str | Path | None,
    required_repair_sha: str | None,
    repair_dispatch_id: str | None,
    native_runner: NativeRunner,
    verification_runner: VerificationRunner,
    attempt: _RunAttempt,
    observation_tracker: RunObservationTracker,
    admission: dict[str, Any],
    model: str | None,
    reasoning_effort: str | None,
    model_source: str | None,
    effort_source: str | None,
) -> RepairSummary:
    if executor is not None and executor not in (
        "codex", "antigravity", "antigravity-minimax"
    ):
        _set_admission_boundary(
            admission, "CANONICAL_CONTRACT_ADMISSION", "TASK_CONTRACT_REJECTED"
        )
        raise OperatorError(f"unsupported executor: {executor}")
    if any(
        value is not None
        for value in (model, reasoning_effort, model_source, effort_source)
    ) and (executor is None or not is_profile_managed_executor(executor)):
        raise OperatorError("model/effort options require a profile-managed Executor")
    state = runtime_paths(repo)
    resolved = _resolve_repair_admission(
        failed_run_id,
        repo=repo,
        state=state,
        repair=repair,
        required_repair_sha=required_repair_sha,
        admission=admission,
    )
    failure = resolved.failure
    task = resolved.task
    root_base_sha = resolved.root_base_sha
    result_base_sha = resolved.result_base_sha
    remote_run_ids = resolved.remote_run_ids
    historical = resolved.historical
    repair_data = resolved.repair
    repair_authorization_sha = resolved.authorization_sha
    action = resolved.action
    scope = resolved.scope
    reusable_package = resolved.reusable_package
    origin_affected_verification = resolved.origin_affected_verification
    if executor is None and reusable_package is None:
        raise OperatorError("coding Executor is required for REPAIR")
    # TASK-064 reusable verification state bypasses dispatcher invocation.  Its
    # schema-compatible RUN label preserves the frozen failed-RUN lineage; it is
    # not a defaulted, selected, or invoked coding Executor.
    inherited_executor = failure.get("executor")
    if inherited_executor not in (
        "codex", "antigravity", "antigravity-minimax"
    ):
        raise OperatorError("failed RUN has invalid Executor lineage")
    run_executor = executor if executor is not None else inherited_executor

    _set_admission_boundary(
        admission, "REPOSITORY_ADMISSION", "REPOSITORY_ADMISSION_REJECTED"
    )
    with RepositoryLock(state.lock):
        if any(
            json.loads(path.read_text(encoding="utf-8")).get("failed_run_id")
            == failed_run_id
            for path in state.repairs.glob("*.json")
        ):
            raise OperatorError("REPAIR has already been accepted for failed RUN")
        if _git(repo, "status", "--porcelain"):
            raise OperatorError("repository dirty")
        failed_head = failure["failed_head_sha"]
        subject_repo = repo
        workspace = None
        if historical:
            _set_admission_boundary(
                admission,
                "HISTORICAL_SUBJECT_ADMISSION",
                "HISTORICAL_SUBJECT_REJECTED",
            )
            subject_repo = _create_historical_workspace(repo, failed_head)
            workspace = subject_repo
        elif _git(repo, "rev-parse", "HEAD") != failed_head:
            raise OperatorError("current HEAD does not match failed committed state")
        attempt.bind_subject(subject_repo, task, workspace)
        _set_admission_boundary(
            admission, "RUN_RESERVATION", "RUN_NAMESPACE_CONFLICT"
        )
        canonical_run_ids = _remote_run_reservations(
            repo, task, admission=admission
        )
        reserved_run_ids = tuple(
            sorted(set(remote_run_ids).union(canonical_run_ids))
        )
        run_id = next_run_id(
            task.task_id, state.runs, reserved=reserved_run_ids
        )
        run = Run.from_task(
            run_id=run_id, task=task, executor=run_executor,
            base_sha=failed_head, workspace=str(subject_repo),
        )
        run_path = state.runs / f"{run_id}.json"
        execution = {
            "failed_run_id": failed_run_id,
            "root_base_sha": root_base_sha,
            "result_base_sha": result_base_sha,
            "failed_head_sha": failed_head,
            "failure": failure,
            "task": _executor_task_data(task),
            "repair": dict(repair_data),
            "run": run,
        }
        if repair_authorization_sha is not None:
            execution["repair_authorization_sha"] = repair_authorization_sha
        _write_json(run_path, asdict(run))
        attempt.bind_run(run_path)
        observation_tracker.admit(run)
        persisted_execution = dict(execution)
        persisted_execution["run"] = asdict(run)
        _write_json(state.repairs / f"{run_id}.json", persisted_execution)

        execution_profile = None
        if reusable_package is None:
            execution_profile = _bind_and_persist_execution_profile(
                state=state,
                repo=repo,
                run_id=run_id,
                executor=run_executor,
                model=model,
                reasoning_effort=reasoning_effort,
                model_source=model_source,
                effort_source=effort_source,
            )

        if repair_dispatch_id is not None:
            from .repair_dispatch import bind_repair_run

            bind_repair_run(
                state_root=state.root,
                repo_root=repo,
                repair_dispatch_id=repair_dispatch_id,
                run_id=run_id,
                execution_profile=execution_profile,
                source_repair_policy=_SOURCE_REPAIR_POLICY.get(),
            )

        if reusable_package is None:
            observed_native_runner = observation_tracker.wrap_native_runner(
                native_runner
            )
            execution_policy = resolve_native_execution_policy(
                authorizes_mutation=action in (
                    "CODE_FIX", "CONTINUE_IMPLEMENTATION"
                )
            )
            dispatcher = repair_dispatcher(
                selected_executor=run_executor,
                repo=subject_repo,
                handoff_path=state.handoffs / f"{run_id}.json",
                execution_policy=execution_policy,
                native_runner=observed_native_runner,
                execution_profile=execution_profile,
            )
            try:
                package = dispatcher.dispatch_repair(execution=execution)
            except (
                CodexOutputError,
                AntigravityOutputError,
                AntigravityMinimaxOutputError,
                ArtifactValidationError,
            ) as exc:
                raise OperatorError(f"invalid structural ResultPackage: {exc}") from exc
            except CodexExecutionError as exc:
                raise OperatorError(f"Codex invocation failed: {exc}") from exc
            except AntigravityExecutionError as exc:
                raise OperatorError(str(exc)) from exc
            except AntigravityMinimaxExecutionError as exc:
                raise OperatorError(str(exc)) from exc
            except DispatcherError as exc:
                raise OperatorError(f"dispatcher failed: {exc}") from exc
        else:
            package = reusable_package

        runtime_completion = RuntimeCompletion(
            repo=subject_repo,
            state=state,
            task=task,
            run=run,
            run_path=run_path,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
            error_type=OperatorError,
            transport_repo=repo,
        )
        attempt.bind_completion(runtime_completion)
        _prepare_historical_terminalization(repo, attempt)
        completion = runtime_completion.complete(
            package,
            repair_completion_policy(
                task,
                root_base_sha=root_base_sha,
                result_base_sha=result_base_sha,
                failed_head_sha=failed_head,
                action=action,
                modification_scope=scope,
                lineage_path=state.repairs / f"{run_id}.json",
                origin_affected_verification=origin_affected_verification,
            ),
        )
        return RepairSummary(
            task_id=task.task_id, failed_run_id=failed_run_id, run_id=run_id,
            executor=run_executor, failed_head_sha=failed_head,
            head_sha=completion.head_sha, result_path=completion.result_path,
        )


def _resolve_historical_repair_admission(
    repo: Path,
    failed_run_id: str,
    *,
    admission: dict[str, Any] | None = None,
) -> _HistoricalRepairAdmission:
    try:
        recovery = resolve_remote_repair_recovery(
            repo, failed_run_id=failed_run_id
        )
    except ReviewTransportError as exc:
        if admission is not None:
            observed_refs = getattr(exc, "observed_refs", ())
            if isinstance(observed_refs, tuple):
                _record_observed_refs(admission, observed_refs)
        raise OperatorError(f"historical REPAIR lineage rejected: {exc}") from exc
    if admission is not None:
        _record_observed_refs(admission, recovery.observed_refs)
        _set_admission_boundary(
            admission,
            "CANONICAL_CONTRACT_ADMISSION",
            "CANONICAL_LINEAGE_INVALID",
        )
    if not recovery.failures:
        raise OperatorError("historical REPAIR lineage is empty")

    target_artifact = recovery.failures[0]
    target_failure = _decode_remote_mapping(target_artifact.failure, "FAILURE")
    if admission is not None:
        admission["failed_head_sha"] = target_artifact.candidate_sha
    task_ref = target_failure.get("task")
    if not isinstance(task_ref, Mapping):
        raise OperatorError("historical FAILURE TASK reference is invalid")
    task_id = task_ref.get("id")
    revision = task_ref.get("revision")
    if (
        not isinstance(task_id, str)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
    ):
        raise OperatorError("historical FAILURE TASK reference is invalid")
    try:
        task_source = read_remote_task(
            repo, commit_sha=target_artifact.candidate_sha, task_id=task_id
        )
        task = parse_task(task_source.decode("utf-8", errors="strict"))
    except (ReviewTransportError, UnicodeError, TaskValidationError) as exc:
        raise OperatorError(f"historical TASK rejected: {exc}") from exc
    if task.task_id != task_id or task.revision != revision:
        raise OperatorError("historical TASK identity or revision mismatch")

    parsed: list[
        tuple[RemoteFailureArtifacts, Mapping[str, Any], Mapping[str, Any], Run, Any]
    ] = []
    expected_task = {"id": task_id, "revision": revision}
    for artifact in recovery.failures:
        failure = _decode_remote_mapping(artifact.failure, "FAILURE")
        failure_task = failure.get("task")
        if not isinstance(failure_task, Mapping) or dict(failure_task) != expected_task:
            raise OperatorError("historical failed RUN TASK lineage mismatch")
        if failure.get("failed_head_sha") != artifact.candidate_sha:
            raise OperatorError("historical failed-head lineage mismatch")
        run_data = _decode_remote_mapping(artifact.run, "RUN")
        try:
            if run_data.get("kind") == "REMEDIATION":
                execution = _remediation_execution_from_data(run_data["execution"])
                run = execution.run
            elif "kind" not in run_data:
                execution = None
                run = _run_from_data(run_data)
            else:
                raise ValueError("unknown RUN kind")
        except (KeyError, TypeError, ValueError, ReviewValidationError) as exc:
            raise OperatorError(f"invalid historical RUN lineage: {exc}") from exc
        if run.run_id != artifact.run_id:
            raise OperatorError("historical RUN identity mismatch")
        if {"id": run.task.id, "revision": run.task.revision} != expected_task:
            raise OperatorError("historical RUN TASK lineage mismatch")
        if failure.get("base_sha") != run.base_sha:
            raise OperatorError("historical FAILURE base does not match RUN")
        parsed.append((artifact, failure, run_data, run, execution))

    for index, (artifact, failure, _, run, execution) in enumerate(parsed[:-1]):
        if execution is not None:
            raise OperatorError("REMEDIATION cannot contain REPAIR continuation metadata")
        predecessor = parsed[index + 1]
        continuation = failure.get("continuation_of")
        if continuation != predecessor[3].run_id:
            raise OperatorError("historical continuation chain mismatch")
        if run.base_sha != predecessor[1].get("failed_head_sha"):
            raise OperatorError("historical REPAIR base does not match failed head")
        if artifact.repair is not None:
            repair_execution = _decode_remote_mapping(
                artifact.repair, "REPAIR execution"
            )
            embedded_run = repair_execution.get("run")
            embedded_failure = repair_execution.get("failure")
            authorization_sha = repair_execution.get("repair_authorization_sha")
            if (
                repair_execution.get("failed_run_id") != continuation
                or repair_execution.get("failed_head_sha") != run.base_sha
                or not isinstance(embedded_run, Mapping)
                or dict(embedded_run) != dict(_decode_remote_mapping(artifact.run, "RUN"))
                or not isinstance(embedded_failure, Mapping)
                or dict(embedded_failure) != dict(predecessor[1])
                or (
                    authorization_sha is not None
                    and (
                        not isinstance(authorization_sha, str)
                        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", authorization_sha)
                        is None
                    )
                )
            ):
                raise OperatorError("historical REPAIR execution lineage conflict")
            authorization = repair_execution.get("repair")
        else:
            try:
                authorization = json.loads(
                    read_remote_repair(repo, continuation).decode(
                        "utf-8", errors="strict"
                    )
                )
            except (ReviewTransportError, UnicodeError, json.JSONDecodeError) as exc:
                raise OperatorError(
                    f"legacy REPAIR authorization rejected: {exc}"
                ) from exc
        if (
            not isinstance(authorization, Mapping)
            or authorization.get("failed_run_id") != continuation
            or authorization.get("failed_head_sha") != predecessor[1].get("failed_head_sha")
            or not isinstance(authorization.get("task"), Mapping)
            or dict(authorization["task"]) != expected_task
        ):
            raise OperatorError("historical REPAIR authorization mismatch")

    (
        terminal_artifact,
        terminal_failure,
        terminal_run_data,
        terminal_run,
        terminal_execution,
    ) = parsed[-1]
    if terminal_failure.get("continuation_of") is not None:
        raise OperatorError("historical correction lineage terminates incompletely")
    if terminal_execution is None:
        root_base_sha = terminal_run.base_sha
    else:
        if not terminal_execution.remediation.affected_verification:
            raise OperatorError(
                "historical REMEDIATION origin affected verification is empty"
            )
        if (
            "execution_base" not in terminal_run_data
            and terminal_run.base_sha
            != terminal_execution.remediation.reviewed_sha
        ):
            raise OperatorError(
                "legacy REMEDIATION origin base does not match reviewed SHA"
            )
        root_base_sha = _derive_remediation_source_root(
            repo, task=task, execution=terminal_execution
        )
    result_base_sha = _repair_result_base_from_origin(
        repo,
        task=task,
        run_data=terminal_run_data,
        run=terminal_run,
        root_base_sha=root_base_sha,
    )
    if not isinstance(root_base_sha, str) or not root_base_sha:
        raise OperatorError("invalid original TASK root lineage")
    if not _git_is_ancestor(repo, root_base_sha, target_artifact.candidate_sha):
        raise OperatorError("historical failed head does not descend from TASK root")
    for artifact, _, _, _, _ in parsed[:-1]:
        if artifact.repair is None:
            continue
        lineage = _decode_remote_mapping(artifact.repair, "REPAIR execution")
        if lineage.get("root_base_sha") != root_base_sha:
            raise OperatorError("conflicting original TASK root lineage")
        persisted_result_base = lineage.get("result_base_sha")
        if (
            persisted_result_base is not None
            and persisted_result_base != result_base_sha
        ):
            raise OperatorError("conflicting REPAIR result-base lineage")
    candidate = target_failure.get("candidate")
    if not isinstance(candidate, Mapping) or candidate.get("repairable") is not True:
        raise OperatorError("failed candidate is not safely bound for REPAIR")
    return _HistoricalRepairAdmission(
        target_failure,
        task,
        root_base_sha,
        result_base_sha,
        recovery.remote_run_ids,
        target_artifact.preverification,
        (
            ()
            if terminal_execution is None
            else terminal_execution.remediation.affected_verification
        ),
    )


def _repair_result_base_from_origin(
    repo: Path,
    *,
    task: Task,
    run_data: Mapping[str, Any],
    run: Run,
    root_base_sha: str,
) -> str:
    """Derive the Git attribution base without changing semantic TASK lineage."""

    if run_data.get("kind") != "REMEDIATION" or "execution_base" not in run_data:
        return root_base_sha
    raw_base = run_data["execution_base"]
    if not isinstance(raw_base, Mapping):
        raise OperatorError("integrated REMEDIATION execution_base is invalid")
    try:
        base = _parse_remediation_execution_base(raw_base)
    except (TypeError, ValueError) as exc:
        raise OperatorError(
            f"integrated REMEDIATION execution_base rejected: {exc}"
        ) from exc
    if base.candidate_sha != run.base_sha:
        qualifier = "integrated REMEDIATION" if base.is_integrated else "REMEDIATION"
        raise OperatorError(f"{qualifier} execution_base does not match RUN base")
    if not base.is_integrated:
        return root_base_sha
    if set(raw_base) != _CANONICAL_INTEGRATED_EXECUTION_BASE_FIELDS:
        raise OperatorError(
            "integrated REMEDIATION execution_base fields are not canonical"
        )
    try:
        lifecycle = resolve_remote_task_lifecycle(
            repo, task_id=task.task_id, task_revision=task.revision
        )
    except ReviewTransportError as exc:
        raise OperatorError(
            f"integrated REMEDIATION lifecycle evidence rejected: {exc}"
        ) from exc
    if not _git_is_ancestor(repo, base.authorized_main_sha, lifecycle.main_sha):
        raise OperatorError(
            "integrated REMEDIATION authorized main is not canonical history"
        )
    cumulative = [
        item
        for item in lifecycle.terminals
        if item.run_id == base.cumulative_tip_run_id
        and item.kind == "RESULT"
        and item.candidate_sha == base.cumulative_tip_candidate_sha
    ]
    if len(cumulative) != 1:
        raise OperatorError(
            "integrated REMEDIATION cumulative tip evidence is missing or ambiguous"
        )
    cumulative_run_data = _decode_remote_mapping(
        cumulative[0].run, "integrated cumulative RUN"
    )
    if cumulative_run_data.get("kind") == "REMEDIATION":
        cumulative_run = _remediation_execution_from_data(
            cumulative_run_data["execution"]
        ).run
    elif "kind" not in cumulative_run_data:
        cumulative_run = _run_from_data(cumulative_run_data)
    else:
        raise OperatorError("integrated cumulative RUN kind is invalid")
    cumulative_result = _decode_remote_mapping(
        cumulative[0].terminal, "integrated cumulative RESULT"
    )
    try:
        cumulative_head = validate_result(cumulative_result["result"]).head_sha
    except (ArtifactValidationError, KeyError, TypeError, ValueError) as exc:
        raise OperatorError("integrated cumulative RESULT is invalid") from exc
    if (
        cumulative_run.run_id != base.cumulative_tip_run_id
        or cumulative_run.task.id != task.task_id
        or cumulative_run.task.revision != task.revision
        or cumulative_head != base.cumulative_tip_candidate_sha
    ):
        raise OperatorError("integrated cumulative evidence identity mismatch")
    integration = resolve_valid_integration(
        repo,
        task_id=task.task_id,
        task_revision=task.revision,
        cumulative_tip_run_id=base.cumulative_tip_run_id,
        cumulative_tip_candidate_sha=base.cumulative_tip_candidate_sha,
        authorized_main_sha=base.authorized_main_sha,
        require_remote=True,
    )
    if (
        integration is None
        or integration.integration_id != base.integration_id
        or integration.integration_candidate_sha != base.integration_candidate_sha
    ):
        raise OperatorError(
            "integrated REMEDIATION integration evidence is invalid"
        )
    return base.integration_candidate_sha


def _read_optional_bytes(path: Path) -> bytes | None:
    try:
        if path.exists() and not path.is_file():
            raise OperatorError(
                f"pre-verification candidate could not be read: {path} is not a regular file"
            )
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OperatorError(
            f"pre-verification candidate could not be read: {exc}"
        ) from exc


def _repair_action_structure(failure: Mapping[str, Any]) -> dict[str, bool]:
    """Action-neutral prerequisites shared by authoring and execution admission.

    Authorization, nonempty mutation scope and exact NO_CHANGE reuse are separate
    admission gates. These facts grant no execution authority.
    """
    candidate = failure.get("candidate", {})
    repairable = candidate.get("repairable") is True
    clean = (
        candidate.get("transportable") is True
        and candidate.get("dirty") is False
        and candidate.get("descends_from_base") is True
        and candidate.get("outside_task_scope") == []
    )
    preverification = failure.get("phase") in ("EXECUTION", "COMPLETION_GATE")
    return {
        "NO_CHANGE": repairable and clean and failure.get("phase") == "VERIFICATION",
        "FINALIZE_CANDIDATE": repairable and clean and preverification,
        "CONTINUE_IMPLEMENTATION": repairable and clean and preverification,
        "CODE_FIX": repairable,
    }


def _eligible_reusable_repair_package(
    content: bytes | None,
    *,
    task: Task,
    failed_run_id: str,
    failure: Mapping[str, Any],
    action: str,
    scope: list[str],
    local_content: bytes | None = None,
    repo: Path | None = None,
    root_base_sha: str | None = None,
    result_base_sha: str | None = None,
    committed_deltas: Mapping[str, list[str]] | None = None,
) -> ResultPackage | None:
    """Fail closed on present state and admit only exact verification reuse."""

    if action != "NO_CHANGE" or scope:
        return None

    if local_content is not None and local_content != content:
        raise OperatorError(
            "local and canonical remote pre-verification candidate conflict"
        )
    if content is None:
        return None
    failed_head = failure.get("failed_head_sha")
    if not isinstance(failed_head, str) or not failed_head:
        raise OperatorError("invalid failed subject for pre-verification candidate")
    try:
        from .publication import _json_no_duplicates
        _json_no_duplicates(content, document="pre-verification candidate")
        package = validate_preverification_candidate(
            content,
            task=task,
            run_id=failed_run_id,
            subject_sha=failed_head,
        )
    except (ArtifactValidationError, ValueError) as exc:
        raise OperatorError(f"invalid pre-verification candidate: {exc}") from exc

    candidate = failure.get("candidate")
    if not isinstance(candidate, Mapping):
        return None
    changed_files = candidate.get("changed_files")
    if not isinstance(changed_files, list) or not all(
        isinstance(item, str) and item for item in changed_files
    ):
        raise OperatorError("invalid pre-verification candidate changed-files binding")
    if repo is not None or committed_deltas is not None:
        resolved_root_base = result_base_sha
        if resolved_root_base is None:
            resolved_root_base = root_base_sha
        if resolved_root_base is None and failure.get("continuation_of") is None:
            resolved_root_base = failure.get("base_sha")
        if not isinstance(resolved_root_base, str) or not resolved_root_base:
            raise OperatorError("invalid REPAIR result-base lineage")
        actual_root_changed = (
            set(committed_deltas["result"])
            if committed_deltas is not None else
            _committed_changed_files(repo, resolved_root_base, failed_head)
        )
        if set(package.result.changed_files) != actual_root_changed or len(
            package.result.changed_files
        ) != len(actual_root_changed):
            raise OperatorError("pre-verification candidate changed-files mismatch")
        if actual_root_changed.difference(task.scope.modify):
            raise OperatorError("pre-verification candidate changed-files mismatch")
        failed_base = failure.get("base_sha")
        if not isinstance(failed_base, str) or not failed_base:
            raise OperatorError("invalid failed candidate base")
        actual_candidate_changed = (
            set(committed_deltas["candidate"])
            if committed_deltas is not None else
            _committed_changed_files(repo, failed_base, failed_head)
        )
        if set(changed_files) != actual_candidate_changed or len(changed_files) != len(
            actual_candidate_changed
        ):
            raise OperatorError(
                "invalid pre-verification candidate changed-files binding"
            )
    else:
        if set(package.result.changed_files).difference(task.scope.modify):
            raise OperatorError("pre-verification candidate changed-files mismatch")
        if (
            failure.get("continuation_of") is None
            and (root_base_sha is None or root_base_sha == failure.get("base_sha"))
        ):
            if list(package.result.changed_files) != changed_files:
                raise OperatorError("pre-verification candidate changed-files mismatch")
    if not _repair_action_structure(failure)["NO_CHANGE"]:
        return None
    if package.result.unresolved:
        raise OperatorError("pre-verification candidate is incomplete")
    satisfied = {
        acceptance_id
        for claim in package.result.claims
        for acceptance_id in claim.satisfies
    }
    missing = [item.id for item in task.acceptance if item.id not in satisfied]
    if missing:
        raise OperatorError(
            "pre-verification candidate lacks TASK acceptance coverage: "
            + ", ".join(missing)
        )
    return package


def _validated_repair_remediation_source(
    repo: Path, *, task: Task, run_data: Mapping[str, Any], run: Run,
    package: ResultPackage, review: Review, repair: bytes | None,
    repair_sources: frozenset[str] = frozenset(),
    remote: str | None = None,
) -> tuple[str, Review | None] | None:
    """Validate exact successful REPAIR provenance before remediation authority.

    A missing wrapper on a RUN based on a canonical failed candidate cannot become
    ordinary PRIMARY provenance. No new lifecycle kind is inferred or persisted.
    Publisher callers may supply their already validated remote; other callers
    retain the current branch's fail-closed upstream resolution.
    """
    from . import publication as pub
    from .review_transport import resolve_transport_remote, _read_remote_blob

    if remote is None:
        remote = resolve_transport_remote(repo)
    if repair is None:
        if "kind" not in run_data:
            if review.mode == "DELTA":
                raise ValueError("DELTA source is missing validated correction lineage")
            _, failures, _ = pub._git(
                repo, "ls-remote", "--refs", remote,
                f"refs/heads/aios/failure/{task_run_prefix(task.task_id)}*",
            )
            if any(line.split()[0] == run.base_sha for line in failures.splitlines()):
                raise ValueError("successful REPAIR source is missing REPAIR lineage")
        return None
    if "kind" in run_data:
        raise ValueError("successful source has conflicting REMEDIATION/REPAIR lineage")
    if run.status != "ACTIVE" or run.task.id != task.task_id or run.task.revision != task.revision:
        raise ValueError("successful REPAIR RUN status or TASK identity mismatch")
    source_id = run.run_id
    if source_id in repair_sources:
        raise ValueError("cyclic successful REPAIR source lineage")
    repair_sources = repair_sources.union((source_id,))
    artifacts_sha = pub._single_remote_sha(
        repo, remote, f"refs/heads/aios/artifacts/{source_id}", run_id=source_id,
    )
    candidate_sha = pub._single_remote_sha(
        repo, remote, f"refs/heads/aios/review/{source_id}", run_id=source_id,
    )
    if candidate_sha != package.result.head_sha or review.reviewed_sha != candidate_sha:
        raise ValueError("successful REPAIR source candidate/REVIEW mismatch")
    pub._fetch_object(repo, remote, candidate_sha, run_id=source_id)
    if not _git_is_ancestor(repo, run.base_sha, candidate_sha):
        raise ValueError("successful REPAIR candidate does not descend from failed head")
    if pub._single_optional_remote_sha(
        repo, remote, f"refs/heads/aios/failure-artifacts/{source_id}", run_id=source_id,
    ) is not None:
        raise ValueError("successful REPAIR source has conflicting terminal lineage")
    pub._fetch_object(repo, remote, artifacts_sha, run_id=source_id)
    canonical_repair = _read_remote_blob(repo, remote, artifacts_sha, ".ai/transport/repair.json")
    canonical_run = pub._json_no_duplicates(
        pub._read_blob(repo, artifacts_sha, ".ai/transport/run.json", run_id=source_id), document="RUN",
    )
    canonical_result = pub._json_no_duplicates(
        pub._read_blob(repo, artifacts_sha, ".ai/transport/result.json", run_id=source_id),
        document="ResultPackage",
    )
    if canonical_repair != repair or canonical_run != dict(run_data) or (
        validate_result(canonical_result["result"]) != package.result
        or tuple(validate_evidence(item) for item in canonical_result["evidence"]) != package.evidence
    ):
        raise ValueError("substituted successful REPAIR source transport")
    decision_sha = pub._single_remote_sha(
        repo, remote, f"refs/heads/aios/review-decision/{source_id}", run_id=source_id,
    )
    pub._fetch_object(repo, remote, decision_sha, run_id=source_id)
    _, tree, _ = pub._git(repo, "ls-tree", "-r", "--name-only", decision_sha, "--", ".ai/reviews")
    paths = [path for path in tree.splitlines() if path.endswith((".yaml", ".yml"))]
    if len(paths) != 1 or parse_review(pub._read_blob(
        repo, decision_sha, paths[0], run_id=source_id,
    ).decode("utf-8")) != review:
        raise ValueError("successful REPAIR source REVIEW is not the exact canonical decision")
    root, result_base, prior, finding_id = pub._repair_review_lineage(
        repo, remote=remote, publication_run_id=source_id, child_run_data=run_data,
        child_run=run, child_head_sha=candidate_sha, lineage_bytes=repair, task=task,
        strict_source=True, repair_sources=repair_sources,
    )
    pub._validate_repair_package(
        repo, source_sha=candidate_sha, result_base_sha=result_base, task=task,
        run=run, result=package.result, evidence=package.evidence,
    )
    if prior is not None and (review.mode != "DELTA" or review.prior_finding_id != finding_id):
        raise ValueError("successful REPAIR requires the exact prior DELTA finding")
    if prior is not None:
        # The prior semantic REVIEW copied in an authorization carrier must also
        # be the exact canonical Reviewer decision, not an unrelated substitute.
        origins = resolve_remote_remediation_lineages(
            repo, finding_id=finding_id, task_id=task.task_id, task_revision=task.revision,
        )
        origins = [item for item in origins
                   if parse_review(item.review.decode("utf-8")) == prior]
        if len(origins) != 1:
            raise ValueError("successful REPAIR prior REVIEW source is missing or ambiguous")
        origin_id = origins[0].source_run_id
        prior_sha = pub._single_remote_sha(
            repo, remote, f"refs/heads/aios/review-decision/{origin_id}", run_id=source_id,
        )
        pub._fetch_object(repo, remote, prior_sha, run_id=source_id)
        _, tree, _ = pub._git(repo, "ls-tree", "-r", "--name-only", prior_sha, "--", ".ai/reviews")
        paths = [path for path in tree.splitlines() if path.endswith((".yaml", ".yml"))]
        if len(paths) != 1 or parse_review(pub._read_blob(
            repo, prior_sha, paths[0], run_id=source_id,
        ).decode("utf-8")) != prior:
            raise ValueError("successful REPAIR prior REVIEW differs from canonical decision")
    validate_review(task=task, result=package.result, review=review, prior_review=prior)
    return root, prior


def _derive_remediation_source_root(
    repo: Path,
    *,
    task: Task,
    execution: RemediationExecution,
    seen: frozenset[tuple[str, str]] = frozenset(),
    repair_sources: frozenset[str] = frozenset(),
) -> str:
    if execution.original_constraints != execution.remediation.constraints:
        raise OperatorError("REMEDIATION origin constraints mismatch")
    identity = (execution.review_id, execution.remediation.reviewed_sha)
    if identity in seen:
        raise OperatorError("cyclic reviewed source lineage")
    seen = seen.union((identity,))
    try:
        lineages = resolve_remote_remediation_lineages(
            repo,
            finding_id=execution.finding.id,
            task_id=task.task_id,
            task_revision=task.revision,
        )
    except ReviewTransportError as exc:
        raise OperatorError(f"reviewed source lineage rejected: {exc}") from exc
    matches: list[str] = []
    for remote in lineages:
        try:
            review = parse_review(remote.review.decode("utf-8", errors="strict"))
            if review.review_id != execution.review_id:
                continue
            remediation = parse_remediation(
                remote.remediation.decode("utf-8", errors="strict")
            )
            run_data = _decode_remote_mapping(remote.run, "source RUN")
            if run_data.get("kind") == "REMEDIATION":
                source_execution = _remediation_execution_from_data(
                    run_data["execution"]
                )
                source_run = source_execution.run
            elif "kind" not in run_data:
                source_execution = None
                source_run = _run_from_data(run_data)
            else:
                raise ValueError("unknown source RUN kind")
            if source_run.task.id != task.task_id:
                continue
            if source_run.task.revision != task.revision:
                continue
            package_data = _decode_remote_mapping(remote.result, "source RESULT")
            result = validate_result(package_data["result"])
            evidence_data = package_data["evidence"]
            if not isinstance(evidence_data, list):
                raise TypeError("source evidence must be a list")
            evidence = tuple(validate_evidence(item) for item in evidence_data)
            package = ResultPackage(result=result, evidence=evidence)
            repaired_source = _validated_repair_remediation_source(
                repo, task=task, run_data=run_data, run=source_run,
                package=package, review=review, repair=remote.repair, repair_sources=repair_sources,
            )
            if source_execution is None:
                validate_result_package(
                    task=task, run=source_run, result=result, evidence=evidence
                )
                prior_review = None if repaired_source is None else repaired_source[1]
            else:
                _validate_persisted_remediation_result(
                    repo=repo,
                    task=task,
                    execution=source_execution,
                    package=package,
                    run_document=run_data,
                )
                prior_review = Review(
                    review_id=source_execution.review_id,
                    reviewed_sha=source_execution.remediation.reviewed_sha,
                    mode="PRIMARY",
                    verdict="CHANGES_REQUIRED",
                    acceptance={},
                    findings=(source_execution.finding,),
                )
            validate_review(
                task=task, result=result, review=review, prior_review=prior_review
            )
            validate_remediation(review=review, remediation=remediation, task=task)
            canonical_finding = next(
                item for item in review.findings if item.id == remediation.finding_id
            )
            if review.review_id != execution.review_id:
                continue
            if (
                remote.source_run_id != source_run.run_id
                or review.reviewed_sha != execution.remediation.reviewed_sha
                or remediation != execution.remediation
                or canonical_finding != execution.finding
                or result.head_sha != execution.remediation.reviewed_sha
            ):
                raise ValueError("reviewed source identity or SHA mismatch")
            if source_execution is not None:
                root = _derive_remediation_source_root(
                    repo, task=task, execution=source_execution, seen=seen, repair_sources=repair_sources,
                )
            elif repaired_source is not None:
                root = repaired_source[0]
            else:
                root = source_run.base_sha
            if not isinstance(root, str) or not root:
                raise ValueError("source root_base_sha is invalid")
            matches.append(root)
        except (
            ArtifactValidationError,
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            ReviewValidationError,
        ) as exc:
            raise OperatorError(
                f"contract-invalid reviewed source lineage at {remote.ref}: {exc}"
            ) from exc
    if len(matches) != 1:
        qualifier = "not found" if not matches else "ambiguous"
        raise OperatorError(f"exact reviewed source lineage is {qualifier}")
    return matches[0]


def _decode_remote_mapping(content: bytes | None, name: str) -> Mapping[str, Any]:
    if content is None:
        raise OperatorError(f"canonical {name} is missing")
    try:
        value = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OperatorError(f"invalid canonical {name}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise OperatorError(f"canonical {name} must be a mapping")
    return value


def _create_historical_workspace(repo: Path, failed_head: str) -> Path:
    control_head = _git(repo, "rev-parse", "HEAD")
    control_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    exact_commit = _git(
        repo, "rev-parse", "--verify", f"{failed_head}^{{commit}}"
    )
    if exact_commit != failed_head:
        raise OperatorError("historical subject commit mismatch")
    origin_url = _git(repo, "remote", "get-url", "origin")
    workspace = Path(tempfile.mkdtemp(prefix="aios-historical-repair-"))
    try:
        _git(
            repo,
            "clone",
            "--local",
            "--no-hardlinks",
            "--no-checkout",
            "--no-tags",
            str(repo),
            str(workspace),
        )
        _git(workspace, "remote", "set-url", "origin", origin_url)
        _git(workspace, "checkout", "--detach", failed_head)
        if not (workspace / ".git").is_dir():
            raise OperatorError("historical subject Git directory is unavailable")
        if _git(workspace, "rev-parse", "HEAD") != failed_head:
            raise OperatorError("historical subject HEAD mismatch")
        if _git(workspace, "status", "--porcelain"):
            raise OperatorError("historical subject workspace is dirty")
        _require_control_checkout_unchanged(
            repo, head_sha=control_head, branch=control_branch
        )
        return workspace
    except Exception:
        _remove_historical_workspace(repo, workspace)
        raise


def _require_control_checkout_unchanged(
    repo: Path, *, head_sha: str, branch: str
) -> None:
    """Fail closed if isolated execution races with the control checkout."""

    if _git(repo, "rev-parse", "HEAD") != head_sha:
        raise OperatorError("historical execution changed control HEAD")
    if _git(repo, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise OperatorError("historical execution changed control branch")
    if _git(repo, "status", "--porcelain"):
        raise OperatorError("historical execution changed control index or worktree")


def _is_kernel_source(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    return (
        normalized == "pyproject.toml"
        or normalized.startswith("src/")
    )


def _is_kernel_source_or_task_state(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    return (
        _is_kernel_source(normalized)
        or normalized.startswith(".ai/tasks/")
    )


def reconcile_control_main(
    failed_run_id: str, *, expected_failed_head: str,
    expected_canonical_main: str, repo: str | Path | None = None,
) -> dict[str, Any]:
    """Explicit Human operation; no admission, Executor or lifecycle delegation."""
    if (
        not isinstance(failed_run_id, str) or len(failed_run_id) > 128
        or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", failed_run_id) is None
        or not isinstance(expected_failed_head, str)
        or re.fullmatch(r"[0-9a-f]{40}", expected_failed_head) is None
        or not isinstance(expected_canonical_main, str)
        or re.fullmatch(r"[0-9a-f]{40}", expected_canonical_main) is None
    ):
        raise OperatorError("reconciliation requires exact RUN and commit identities")
    root = resolve_repository(repo)

    def require_subject() -> None:
        if _git(root, "symbolic-ref", "--quiet", "HEAD") != "refs/heads/main":
            raise OperatorError("reconciliation requires attached main")
        if _git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all"):
            raise OperatorError("reconciliation requires completely clean status")
        if _git(root, "rev-parse", "HEAD") != expected_failed_head:
            raise OperatorError("reconciliation failed-head drift")

    with RepositoryLock(_runtime_paths_readonly(root).lock):
        require_subject()
        remotes = _git(root, "config", "--get-all", "branch.main.remote").splitlines()
        merges = _git(root, "config", "--get-all", "branch.main.merge").splitlines()
        if len(remotes) != 1 or not remotes[0] or remotes[0] == "." or merges != ["refs/heads/main"]:
            raise OperatorError("missing or ambiguous upstream main")
        remote = remotes[0]
        remote_url = _git(root, "remote", "get-url", remote)
        # Resolve the configured tracking binding without fetching into control refs.
        upstream = _git(root, "rev-parse", "--symbolic-full-name", "@{upstream}")
        if not upstream.startswith("refs/remotes/") or not upstream.endswith("/main"):
            raise OperatorError("configured upstream does not resolve to main")

        def require_upstream() -> None:
            if (
                _git(root, "config", "--get-all", "branch.main.remote").splitlines() != remotes
                or _git(root, "config", "--get-all", "branch.main.merge").splitlines() != merges
                or _git(root, "rev-parse", "--symbolic-full-name", "@{upstream}") != upstream
                or _git(root, "remote", "get-url", remote) != remote_url
            ):
                raise OperatorError("configured upstream drift")

        try:
            with _remote_observation_repository(root) as observer:
                target_refs = _exact_remote_refs(observer, remote, "refs/heads/main")
                if target_refs != {"refs/heads/main": expected_canonical_main}:
                    raise OperatorError("canonical main target drift")
                _git(observer, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", remote, expected_canonical_main)
                base, failure_refs = prove_remote_failed_candidate(
                    observer, failed_run_id=failed_run_id, failed_head_sha=expected_failed_head,
                )
                if (
                    not _git_is_ancestor(observer, base, expected_failed_head)
                    or not _git_is_ancestor(observer, base, expected_canonical_main)
                    or _git_is_ancestor(observer, expected_canonical_main, expected_failed_head)
                    or _git_is_ancestor(observer, expected_failed_head, expected_canonical_main)
                ):
                    raise OperatorError("reconciliation requires diverged common failure-base lineage")
                # Submodule worktrees have their own mutation authority.
                trees = (
                    _git(observer, "ls-tree", "-r", sha).splitlines()
                    for sha in (expected_failed_head, expected_canonical_main)
                )
                if any(line.startswith("160000 ") for tree in trees for line in tree):
                    raise OperatorError("reconciliation does not mutate submodule worktrees")
                ignored = _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", strip_stdout=False).split("\0")
                target_paths = _git(observer, "ls-tree", "-r", "--name-only", "-z", expected_canonical_main, strip_stdout=False).split("\0")
                if any(
                    path and target and (path == target or path.startswith(target + "/") or target.startswith(path + "/"))
                    for path in ignored for target in target_paths
                ):
                    raise OperatorError("reconciliation would overwrite ignored worktree content")
                patterns = (*failure_refs, f"refs/heads/aios/review/{failed_run_id}",
                            f"refs/heads/aios/artifacts/{failed_run_id}", "refs/heads/main")
                if _exact_remote_refs(observer, remote, *patterns) != {**failure_refs, **target_refs}:
                    raise OperatorError("canonical reconciliation proof drift")
                require_subject()
                require_upstream()
                # Only after every proof succeeds import the target objects. No
                # tracking refs or FETCH_HEAD are written in the control repository.
                _git(observer, "update-ref", "refs/heads/main", expected_canonical_main)
                _git(root, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", str(observer), expected_canonical_main)
                require_subject()
                require_upstream()
                if _exact_remote_refs(observer, remote, *patterns) != {**failure_refs, **target_refs}:
                    raise OperatorError("canonical reconciliation proof drift")
                _git(root, "reset", "--hard", expected_canonical_main)
        except ReviewTransportError as exc:
            raise OperatorError(str(exc)) from exc
        if (
            _git(root, "symbolic-ref", "--quiet", "HEAD") != "refs/heads/main"
            or _git(root, "rev-parse", "HEAD") != expected_canonical_main
            or _git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all")
        ):
            raise OperatorError("repository-integrity BLOCKED: reconciliation postconditions failed")
    return {"failed_run_id": failed_run_id, "prior_head": expected_failed_head,
            "restored_head": expected_canonical_main, "status": "SUCCESS"}


def _require_primary_reconciliation_worktree(
    root: Path, observer: Path, local_sha: str, target_sha: str,
) -> None:
    """Check safety again at the destructive edge, including ignored content."""
    if _git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all"):
        raise OperatorError("reconciliation requires completely clean status")
    # Status may hide modifications under assume-unchanged or sparse entries.
    if any(line and (line[0].islower() or line[0] == "S")
           for line in _git(root, "ls-files", "-v").splitlines()):
        raise OperatorError("reconciliation requires fully observed tracked content")
    for sha in (local_sha, target_sha):
        if any(line.startswith("160000 ")
               for line in _git(observer, "ls-tree", "-r", sha).splitlines()):
            raise OperatorError("reconciliation does not mutate submodule worktrees")
    ignored = _git(
        root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z",
        strip_stdout=False,
    ).split("\0")
    targets = _git(
        observer, "ls-tree", "-r", "--name-only", "-z", target_sha,
        strip_stdout=False,
    ).split("\0")
    if any(
        path and target and (
            os.path.normcase(path) == os.path.normcase(target)
            or os.path.normcase(path).startswith(os.path.normcase(target + "/"))
            or os.path.normcase(target).startswith(os.path.normcase(path + "/"))
        ) for path in ignored for target in targets
    ):
        raise OperatorError("reconciliation would overwrite ignored worktree content")
    if _git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all"):
        raise OperatorError("reconciliation requires completely clean status")


def _prove_primary_reviewed_result(
    observer: Path, *, remote: str, run_id: str, local_sha: str,
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    """Read-only preservation proof; no publication eligibility is inferred."""
    from . import publication as pub
    from .authoring_ingress import _validate_metadata_commit, _validate_repair_review_semantics
    from .review_transport import (
        REPAIR_SUPERSESSION_PREFIX, _bind_result_identity,
        _decode_run_task_identity, _performance_json_mapping, _run_task_prefix,
    )

    if re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", run_id) is None:
        raise OperatorError("invalid reviewed RESULT RUN identity")
    prefix = _run_task_prefix(run_id)
    task_id = "TASK-" + prefix[4:-1]
    # Include the correction inputs read by the existing lineage validators.
    # Capture their immutable identities too, rather than only their contents.
    patterns = tuple(f"refs/heads/aios/{family}/{prefix}*" for family in (
        "review", "artifacts", "review-decision", "failure", "failure-artifacts",
        "remediation", "repair",
    )) + (f"{REPAIR_SUPERSESSION_PREFIX}{prefix}*/*",)
    refs = _exact_remote_refs(observer, remote, *patterns)
    candidate_ref = f"refs/heads/aios/review/{run_id}"
    artifact_ref = f"refs/heads/aios/artifacts/{run_id}"
    decision_ref = f"refs/heads/aios/review-decision/{run_id}"
    if (
        refs.get(candidate_ref) != local_sha
        or artifact_ref not in refs or decision_ref not in refs
        or any(
            f"refs/heads/aios/{family}/{ref.rsplit('/', 1)[-1]}" in refs
            for ref in refs if ref.startswith("refs/heads/aios/artifacts/")
            for family in ("failure", "failure-artifacts")
        )
        or any(ref.startswith(decision_ref + "/") for ref in refs)
    ):
        raise OperatorError("missing or conflicting canonical reviewed RESULT transport")
    try:
        for ref in (candidate_ref, artifact_ref, decision_ref):
            sha = refs[ref]
            _git(observer, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", remote, sha)
            if _git(observer, "rev-parse", "--verify", f"{sha}^{{commit}}") != sha:
                raise ValueError("reviewed RESULT transport is not an exact commit")
        artifact_sha = refs[artifact_ref]
        entries = _git(observer, "ls-tree", "-r", artifact_sha).splitlines()
        required_paths = {".ai/transport/run.json", ".ai/transport/result.json"}
        allowed_paths = required_paths | {
            ".ai/transport/repair.json", ".ai/transport/observation.json",
            ".ai/transport/execution-profile.json",
        }
        paths = {entry.partition("\t")[2] for entry in entries}
        if (
            _git(observer, "show", "-s", "--format=%P", artifact_sha)
            or not required_paths.issubset(paths) or paths.difference(allowed_paths)
            or any(not entry.startswith("100644 blob ") for entry in entries)
        ):
            raise ValueError("substituted canonical RESULT artifacts tree")
        run_bytes = pub._read_blob(observer, refs[artifact_ref], ".ai/transport/run.json", run_id=run_id)
        result_bytes = pub._read_blob(observer, refs[artifact_ref], ".ai/transport/result.json", run_id=run_id)
        bound_task, revision, bound_run = _decode_run_task_identity(run_bytes, artifact_ref)
        if bound_task != task_id or bound_run != run_id:
            raise ValueError("reviewed RESULT TASK/RUN identity mismatch")
        run_data = _performance_json_mapping(run_bytes, "RUN")
        remediation = None
        prior = None
        if "kind" not in run_data:
            run = pub._run_from_data(run_data, "RUN")
        elif run_data.get("kind") == "REMEDIATION":
            if "predecessor" in run_data:
                run, remediation = pub._parse_remediation_run(run_data, run_id=run_id)
            else:
                run, remediation, prior = pub._remediation_lineage(run_data, run_id=run_id)
        else:
            raise ValueError("unknown canonical RUN kind")
        if run.status != "ACTIVE" or run.run_id != run_id or run.task.id != task_id:
            raise ValueError("reviewed RESULT RUN identity mismatch")
        if re.fullmatch(r"[0-9a-f]{40}", run.base_sha) is None or not _git_is_ancestor(
            observer, run.base_sha, local_sha,
        ):
            raise ValueError("reviewed RESULT does not descend from exact RUN base")
        lineage_base = run.base_sha
        data = _performance_json_mapping(result_bytes, "ResultPackage")
        if set(data) != {"result", "evidence"} or not isinstance(data["evidence"], list):
            raise ValueError("invalid canonical ResultPackage")
        result = validate_result(data["result"])
        evidence = tuple(validate_evidence(item) for item in data["evidence"])
        _bind_result_identity(run, result, evidence)
        if result.head_sha != local_sha or result.unresolved:
            raise ValueError("reviewed RESULT candidate binding conflicts")
        task = parse_task(pub._read_blob(
            observer, local_sha, f".ai/tasks/{task_id}.yaml", run_id=run_id,
        ).decode("utf-8", errors="strict"))
        if task.task_id != task_id or task.revision != revision:
            raise ValueError("reviewed RESULT TASK revision mismatch")

        def canonical_review(source_run_id: str, source_sha: str) -> Review:
            ref = f"refs/heads/aios/review-decision/{source_run_id}"
            if ref not in refs or any(name.startswith(ref + "/") for name in refs):
                raise ValueError("canonical review decision is missing or ambiguous")
            decision_sha = refs[ref]
            _git(observer, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", remote, decision_sha)
            paths = _git(observer, "ls-tree", "-r", "--name-only", decision_sha, "--", ".ai/reviews").splitlines()
            paths = [p for p in paths if p.endswith((".yaml", ".yml"))]
            if len(paths) != 1:
                raise ValueError("canonical review decision is missing or ambiguous")
            content = pub._read_blob(observer, decision_sha, paths[0], run_id=source_run_id)
            decision = parse_review(content.decode("utf-8", errors="strict"))
            if decision.reviewed_sha != source_sha:
                raise ValueError("canonical review decision candidate mismatch")
            _validate_metadata_commit(
                observer, decision_sha, expected_parent_sha=source_sha,
                metadata_path=f".ai/reviews/{decision.review_id}.yaml",
                metadata_bytes=content, operation="SUBMIT_REVIEW",
            )
            return decision

        review = canonical_review(run_id, local_sha)
        if review.reviewed_sha != local_sha or review.verdict != "PASS":
            raise ValueError("canonical reviewed RESULT requires exact PASS decision")
        repair = pub._read_optional_blob(observer, refs[artifact_ref], ".ai/transport/repair.json")
        if repair is not None:
            if remediation is not None:
                raise ValueError("conflicting REMEDIATION/REPAIR lineage")
            lineage_base, result_base, prior, finding_id = pub._repair_review_lineage(
                observer, remote=remote, publication_run_id=run_id,
                child_run_data=run_data, child_run=run, child_head_sha=local_sha,
                lineage_bytes=repair, task=task, strict_source=True,
            )
            _validate_repair_review_semantics(review, prior_review=prior, repaired_finding_id=finding_id)
            pub._validate_repair_package(
                observer, source_sha=local_sha, result_base_sha=result_base,
                task=task, run=run, result=result, evidence=evidence,
            )
        elif remediation is not None:
            if "predecessor" in run_data:
                prior = pub._validate_predecessor_lineage(
                    observer, remote=remote, publication_run_id=run_id,
                    run_data=run_data, run=run, remediation=remediation, task=task,
                )
                predecessor = run_data["predecessor"]
                if canonical_review(predecessor["source_run_id"], predecessor["reviewed_sha"]) != prior:
                    raise ValueError("substituted canonical predecessor review decision")
            if prior is None or review.mode != "DELTA" or review.prior_finding_id != remediation.finding_id:
                raise ValueError("reviewed RESULT requires exact DELTA finding")
            pub._validate_remediation_package(
                observer, source_sha=local_sha, task=task, run=run,
                remediation=remediation, prior_review=prior, result=result, evidence=evidence,
                execution_base_sha=run.base_sha if "execution_base" in run_data else None,
            )
            # A DELTA execution base can itself be unpublished local-only history.
            # Retain H1R's common-base gate using the fully validated source root.
            lineage_base = _derive_remediation_source_root(
                observer, task=task,
                execution=_remediation_execution_from_data(run_data["execution"]),
            )
        else:
            _validated_repair_remediation_source(
                observer, task=task, run_data=run_data, run=run,
                package=ResultPackage(result=result, evidence=evidence), review=review, repair=None,
            )
            validate_result_package(task=task, run=run, result=result, evidence=evidence)
        validate_review(task=task, result=result, review=review, prior_review=prior)
        if _exact_remote_refs(observer, remote, *patterns) != refs:
            raise ValueError("canonical reviewed RESULT proof drift")
        return lineage_base, refs, patterns
    except (KeyError, TypeError, ValueError, UnicodeError, RuntimeError) as exc:
        raise OperatorError(f"invalid canonical reviewed RESULT preservation: {exc}") from exc


def _reconcile_primary_divergence(
    root: Path, *, remote: str, allow_restart: bool,
) -> bool | None:
    """Locked PRIMARY pre-RUN edge; one canonical tip preserves its full ancestry.

    None leaves non-divergence to the established non-destructive/FF path. There
    is no Human-command delegation, lifecycle result, dispatch or retry here.
    """
    local_sha = _git(root, "rev-parse", "HEAD")
    remotes = _git(root, "config", "--get-all", "branch.main.remote").splitlines()
    merges = _git(root, "config", "--get-all", "branch.main.merge").splitlines()
    upstream = _git(root, "rev-parse", "--symbolic-full-name", "@{upstream}")
    remote_url = _git(root, "remote", "get-url", remote)
    remote_urls = _git(root, "remote", "get-url", "--all", remote).splitlines()
    fetch_specs = _git(root, "config", "--get-all", f"remote.{remote}.fetch").splitlines()
    if remotes != [remote] or merges != ["refs/heads/main"] or not (
        upstream.startswith("refs/remotes/") and upstream.endswith("/main")
    ) or remote_urls != [remote_url]:
        raise OperatorError("missing or ambiguous upstream main")

    def require_binding() -> None:
        if (
            _git(root, "symbolic-ref", "--quiet", "HEAD") != "refs/heads/main"
            or _git(root, "rev-parse", "HEAD") != local_sha
        ):
            raise OperatorError("reconciliation local HEAD drift")
        if (
            _git(root, "config", "--get-all", "branch.main.remote").splitlines() != remotes
            or _git(root, "config", "--get-all", "branch.main.merge").splitlines() != merges
            or _git(root, "rev-parse", "--symbolic-full-name", "@{upstream}") != upstream
            or _git(root, "remote", "get-url", remote) != remote_url
            or _git(root, "remote", "get-url", "--all", remote).splitlines() != remote_urls
            or _git(root, "config", "--get-all", f"remote.{remote}.fetch").splitlines() != fetch_specs
        ):
            raise OperatorError("configured upstream drift")

    try:
        with _remote_observation_repository(root) as observer:
            if _git(observer, "remote", "get-url", remote) != remote_url:
                raise OperatorError("configured upstream drift")
            try:
                targets = _exact_remote_refs(observer, remote, "refs/heads/main")
            except RemoteQueryError as exc:
                raise OperatorError(f"upstream fetch failed: {exc}") from exc
            target_sha = targets.get("refs/heads/main")
            if set(targets) != {"refs/heads/main"} or not isinstance(target_sha, str) or (
                re.fullmatch(r"[0-9a-f]{40}", target_sha) is None
            ):
                raise OperatorError("missing or malformed canonical main target")
            try:
                _git(observer, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", remote, target_sha)
            except OperatorError as exc:
                raise OperatorError(f"upstream fetch failed: {exc}") from exc
            if _git(observer, "rev-parse", "--verify", f"{target_sha}^{{commit}}") != target_sha:
                raise OperatorError("canonical main target is not an exact commit")
            require_binding()
            if local_sha == target_sha or _git_is_ancestor(observer, target_sha, local_sha) or (
                _git_is_ancestor(observer, local_sha, target_sha)
            ):
                return None
            if os.environ.get("AIOS_RESTART_ATTEMPTED") == "1":
                raise OperatorError("unsafe reload/restart condition")

            failure_patterns = (
                "refs/heads/aios/failure/*", "refs/heads/aios/failure-artifacts/*",
            )
            discovery_patterns = (*failure_patterns, "refs/heads/aios/review/*")

            def preservation_candidates(refs: dict[str, str]) -> list[str]:
                return sorted(ref for ref, sha in refs.items()
                              if ref.startswith(("refs/heads/aios/failure/", "refs/heads/aios/review/"))
                              and sha == local_sha)

            discovery = _exact_remote_refs(observer, remote, *discovery_patterns)
            candidates = preservation_candidates(discovery)
            if len(candidates) != 1:
                raise OperatorError("diverged main has missing or ambiguous canonical preservation")
            preserved_run_id = candidates[0].rsplit("/", 1)[-1]
            reviewed = candidates[0].startswith("refs/heads/aios/review/")
            if reviewed:
                base, proof_refs, proof_patterns = _prove_primary_reviewed_result(
                    observer, remote=remote, run_id=preserved_run_id, local_sha=local_sha,
                )
            else:
                base, proof_refs = prove_remote_failed_candidate(
                    observer, failed_run_id=preserved_run_id, failed_head_sha=local_sha,
                )
                proof_patterns = ()
            if any(discovery.get(ref) != sha for ref, sha in proof_refs.items()
                   if ref.startswith(("refs/heads/aios/failure/", "refs/heads/aios/failure-artifacts/",
                                      "refs/heads/aios/review/"))):
                raise OperatorError("canonical preservation identity drift")
            if not _git_is_ancestor(observer, base, local_sha) or not _git_is_ancestor(observer, base, target_sha):
                lineage = "reviewed-result" if reviewed else "failure-base"
                raise OperatorError(f"reconciliation requires diverged common {lineage} lineage")
            # The validated canonical candidate is exactly local HEAD. Thus every
            # local-only commit, including merge ancestry, remains reachable there.
            local_only = _git(observer, "rev-list", local_sha, "--not", target_sha).splitlines()
            if not local_only or any(not _git_is_ancestor(observer, sha, proof_refs[candidates[0]])
                                     for sha in local_only):
                raise OperatorError("local-only history lacks complete canonical preservation")
            changed_paths = _git(
                observer, "diff", "--name-only", "--no-renames", "-z", local_sha, target_sha,
                strip_stdout=False,
            ).split("\0")
            requires_restart = any(_is_kernel_source_or_task_state(p) for p in changed_paths if p)
            if requires_restart and not allow_restart:
                raise OperatorError("cannot continue under stale pre-sync kernel state")

            # Discovery binds only competing tips at local HEAD. Keep FAILURE's
            # original snapshot identity; reviewed RESULT binds its lineage refs.
            preservation = {} if reviewed else {
                ref: sha for ref, sha in discovery.items()
                if ref.startswith(("refs/heads/aios/failure/", "refs/heads/aios/failure-artifacts/"))
            }
            patterns = (*(proof_patterns if reviewed else failure_patterns), f"refs/heads/aios/review/{preserved_run_id}",
                        f"refs/heads/aios/artifacts/{preserved_run_id}", "refs/heads/main")
            expected_proof = {**preservation, **proof_refs, **targets}

            def require_proof() -> None:
                if _exact_remote_refs(observer, remote, *patterns) != expected_proof:
                    raise OperatorError("canonical reconciliation proof drift")
                if preservation_candidates(_exact_remote_refs(observer, remote, *discovery_patterns)) != candidates:
                    raise OperatorError("diverged main has missing or ambiguous canonical preservation")

            require_proof()
            require_binding()
            _require_primary_reconciliation_worktree(root, observer, local_sha, target_sha)

            # Import only objects, never tracking refs or FETCH_HEAD. All failures
            # before reset leave the control refs/index/worktree unchanged.
            _git(observer, "update-ref", "refs/heads/main", target_sha)
            _git(root, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", str(observer), target_sha)
            if reviewed:
                edge_proof = _prove_primary_reviewed_result(
                    observer, remote=remote, run_id=preserved_run_id, local_sha=local_sha,
                )
                proof_matches = edge_proof == (base, proof_refs, proof_patterns)
            else:
                proof_matches = prove_remote_failed_candidate(
                    observer, failed_run_id=preserved_run_id, failed_head_sha=local_sha,
                ) == (base, proof_refs)
            if not proof_matches:
                raise OperatorError("canonical preservation identity drift")
            if reviewed and (
                _git(observer, "rev-list", local_sha, "--not", target_sha).splitlines() != local_only
                or any(not _git_is_ancestor(observer, sha, proof_refs[candidates[0]]) for sha in local_only)
            ):
                raise OperatorError("local-only history lacks complete canonical preservation")
            require_proof()
            require_binding()
            _require_primary_reconciliation_worktree(root, observer, local_sha, target_sha)
            require_binding()
            _git(root, "reset", "--hard", target_sha)
            if (
                _git(root, "symbolic-ref", "--quiet", "HEAD") != "refs/heads/main"
                or _git(root, "rev-parse", "HEAD") != target_sha
                or _git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all")
            ):
                raise OperatorError("repository-integrity BLOCKED: reconciliation postconditions failed")
            return requires_restart
    except ReviewTransportError as exc:
        raise OperatorError(str(exc)) from exc


def _synchronize_primary_branch(
    root: Path,
    *,
    allow_restart: bool = False,
    only_if_behind: bool = False,
) -> bool:
    """Align a clean attached main branch to its configured upstream main by exact native FF."""

    if _git(root, "status", "--porcelain"):
        if only_if_behind:
            return False
        raise OperatorError("repository dirty")
    try:
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    except OperatorError as exc:
        if only_if_behind:
            return False
        raise OperatorError("repository HEAD is detached") from exc
    if branch != "main":
        if only_if_behind:
            return False
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
        if only_if_behind:
            return False
        raise OperatorError("current branch has no resolved upstream") from exc
    try:
        remotes = _git(
            root, "config", "--get-all", f"branch.{branch}.remote"
        ).splitlines()
        merge_refs = _git(
            root, "config", "--get-all", f"branch.{branch}.merge"
        ).splitlines()
    except OperatorError as exc:
        if only_if_behind:
            return False
        raise OperatorError("current branch has no resolved upstream") from exc

    if len(remotes) != 1 or len(merge_refs) != 1:
        if only_if_behind:
            return False
        raise OperatorError("configured upstream is ambiguous")
    remote = remotes[0].strip()
    merge_ref = merge_refs[0].strip()
    if not remote or not merge_ref:
        if only_if_behind:
            return False
        raise OperatorError("current branch has no resolved upstream")
    if merge_ref != "refs/heads/main" or not upstream.endswith("/main"):
        if only_if_behind:
            return False
        raise OperatorError("configured upstream does not resolve to main")

    # PRIMARY callers hold the mutation lock; CONTINUE is behind-only.
    # Observe divergence before the ordinary fetch can change control refs.
    if not only_if_behind and remote != ".":
        reconciled = _reconcile_primary_divergence(
            root, remote=remote, allow_restart=allow_restart,
        )
        if reconciled is not None:
            return reconciled

    try:
        _git(root, "fetch", "--no-tags", remote, merge_ref)
    except OperatorError as exc:
        raise OperatorError(f"upstream fetch failed: {exc}") from exc

    local_sha = _git(root, "rev-parse", "HEAD")
    upstream_sha = _git(root, "rev-parse", upstream)
    if local_sha == upstream_sha:
        return False

    if os.environ.get("AIOS_RESTART_ATTEMPTED") == "1":
        raise OperatorError("unsafe reload/restart condition")

    if _git_is_ancestor(root, upstream_sha, local_sha):
        if only_if_behind:
            return False
        raise OperatorError("local branch is ahead of upstream")
    if not _git_is_ancestor(root, local_sha, upstream_sha):
        if only_if_behind:
            return False
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
    requires_restart = any(
        _is_kernel_source_or_task_state(p) for p in changed_paths
    )

    if requires_restart and not allow_restart:
        raise OperatorError("cannot continue under stale pre-sync kernel state")

    if requires_restart and allow_restart:
        if os.environ.get("AIOS_RESTART_ATTEMPTED") == "1":
            raise OperatorError("unsafe reload/restart condition")

    try:
        _git(root, "merge", "--ff-only", upstream)
        if _git(root, "status", "--porcelain"):
            raise OperatorError("repository dirty after synchronization")
        if _git(root, "rev-parse", "HEAD") != upstream_sha:
            raise OperatorError(
                "repository HEAD does not match upstream after synchronization"
            )
        try:
            current_branch = _git(
                root, "symbolic-ref", "--quiet", "--short", "HEAD"
            )
        except OperatorError as exc:
            raise OperatorError(
                "repository HEAD is detached after synchronization"
            ) from exc
        if current_branch != "main":
            raise OperatorError(
                "repository branch is not main after synchronization"
            )
    except OperatorError as exc:
        try:
            current_sha = _git(root, "rev-parse", "HEAD")
            current_branch = _git(
                root, "symbolic-ref", "--quiet", "--short", "HEAD"
            )
            is_dirty = bool(_git(root, "status", "--porcelain"))
            is_safe = (
                current_sha == local_sha
                and current_branch == "main"
                and not is_dirty
            )
        except Exception:
            is_safe = False

        if is_safe:
            raise OperatorError(f"upstream fast-forward failed: {exc}") from exc
        raise OperatorError(
            f"repository-integrity BLOCKED: safe pre-sync state lost after fast-forward failure: {exc}"
        ) from exc

    return requires_restart


@dataclass(frozen=True)
class PreflightResult:
    restart_code: int | None = None
    preflight_sha: str | None = None


def _preflight_primary_sync(
    root: Path,
    *,
    argv: list[str] | None = None,
    runner: NativeRunner = subprocess.run,
) -> PreflightResult:
    """Safely synchronize clean local main before TASK loading and admission."""

    _require_no_active_migration(root)
    with _active_restart_source(root) as restart_source:
        state = runtime_paths(root)
        with RepositoryLock(state.lock):
            _require_no_active_migration(root)
            needs_restart = _synchronize_primary_branch(root, allow_restart=True)
            preflight_sha = _git(root, "rev-parse", "HEAD")
        if needs_restart:
            with _restart_source_environment(restart_source):
                return PreflightResult(
                    restart_code=_restart_primary_invocation(root, argv=argv, runner=runner)
                )
        return PreflightResult(preflight_sha=preflight_sha)


def _preflight_continue_sync(
    root: Path,
    *,
    argv: list[str] | None = None,
    runner: NativeRunner = subprocess.run,
) -> PreflightResult:
    """Refresh only a proven clean stale-behind control main before observation."""

    _require_no_active_migration(root)
    with _active_restart_source(root) as restart_source:
        state = _runtime_paths_readonly(root)
        lock_existed = state.lock.exists()
        state_root_existed = state.root.exists()
        lock_acquired = False
        try:
            with RepositoryLock(state.lock):
                lock_acquired = True
                _require_no_active_migration(root)
                needs_restart = _synchronize_primary_branch(
                    root, allow_restart=True, only_if_behind=True
                )
                preflight_sha = _git(root, "rev-parse", "HEAD")
        finally:
            if lock_acquired and not lock_existed:
                state.lock.unlink(missing_ok=True)
            if lock_acquired and not state_root_existed:
                try:
                    state.root.rmdir()
                except OSError:
                    pass
        if needs_restart:
            with _restart_source_environment(restart_source):
                return PreflightResult(
                    restart_code=_restart_primary_invocation(root, argv=argv, runner=runner)
                )
        return PreflightResult(preflight_sha=preflight_sha)


@contextmanager
def _active_restart_source(root: Path) -> Iterator[Path | None]:
    """Freeze an editable active package before control synchronization."""

    source = Path(__file__).resolve().parent.parent
    if source != (root / "src").resolve():
        yield None
        return
    snapshot = Path(tempfile.mkdtemp(prefix="aios-active-generation-"))
    try:
        shutil.copytree(source / "aios_renew", snapshot / "aios_renew")
        yield snapshot
    finally:
        shutil.rmtree(snapshot)


@contextmanager
def _restart_source_environment(source: Path | None) -> Iterator[None]:
    token = _ACTIVE_RESTART_SOURCE.set(source)
    try:
        yield
    finally:
        _ACTIVE_RESTART_SOURCE.reset(token)


def _preflight_primary_admission(
    root: Path,
    *,
    task_id: str,
    executor: str,
    dispatch_id: str | None = None,
    argv: list[str] | None = None,
    runner: NativeRunner = subprocess.run,
) -> PreflightResult:
    """Expose TASK-062 rejection through the operation-neutral v2 boundary."""

    admission = _new_admission(
        "PRIMARY",
        phase="PRIMARY_SYNCHRONIZATION",
        reason_code="PRIMARY_SYNCHRONIZATION_REJECTED",
        task_id=task_id,
        executor=executor,
        dispatch_id=dispatch_id,
    )
    try:
        return _preflight_primary_sync(root, argv=argv, runner=runner)
    except BaseException as original:
        _persist_and_transport_admission_failure(
            root, admission=admission, failure=original
        )
        raise



def _restart_primary_invocation(
    root: Path,
    *,
    argv: list[str] | None = None,
    runner: NativeRunner = subprocess.run,
) -> int:
    """Re-invoke under the active installed package, never the refreshed checkout."""

    env = dict(os.environ)
    env["AIOS_RESTART_ATTEMPTED"] = "1"
    # A downstream control checkout is data until an explicit migration handoff.
    # In particular, an inherited PYTHONPATH can already contain its src tree.
    source = (_ACTIVE_RESTART_SOURCE.get() or Path(__file__)).resolve()
    if source.is_file():
        source = source.parent.parent
    checkout_source = (root / "src").resolve()
    if source == checkout_source:
        raise OperatorError(
            "unsafe reload/restart condition: active package is the mutable control checkout"
        )
    inherited = env.get("PYTHONPATH", "").split(os.pathsep)
    inherited = [entry for entry in inherited if entry and Path(entry).resolve() != checkout_source]
    env["PYTHONPATH"] = os.pathsep.join([str(source), *inherited])
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


_MIGRATION_FIELDS = frozenset({
    "version", "source_generation_sha", "target_generation_sha", "target_url",
    "repository", "source_control_sha", "target_control_sha", "pin_path",
    "source_pin_blob_sha", "target_pin_blob_sha", "task_id", "task_revision",
    "task_blob_sha", "task_commit_sha", "executor",
})

# The first reviewed generation with migrate-primary. This compatibility entry
# is deliberately narrower than the reusable N-to-N+1 migration protocol.
_BOOTSTRAP_TARGET_SHA = "83115b26df85a7ad6643f317833e18b18586bdbe"

# Exact reviewed and published TASK-199 consumer-capable generation.
_SOURCE_BOOTSTRAP_TARGET_SHA: str | None = "31fd2482cd87d97fd818e05eb5b4dcec69ffeee6"
_SOURCE_BOOTSTRAP_FIELDS = (_MIGRATION_FIELDS - {"target_control_sha", "target_pin_blob_sha"}) | {"format"}

# One separately activated migration-capable source-control staging edge.
_SOURCE_UPGRADE_SOURCE_SHA = "31fd2482cd87d97fd818e05eb5b4dcec69ffeee6"
_SOURCE_UPGRADE_TARGET_SHA = "44eee353eda376c9db8cd88d97184d3122651bf5"

# Activated only by a separate, post-publication successor. Never inferred from
# this checkout, the requested target, or the source-PRIMARY activation.
_SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA: str | None = "44eee353eda376c9db8cd88d97184d3122651bf5"
_SOURCE_REPAIR_BOOTSTRAP_FIELDS = frozenset({
    "format", "version", "repository", "bootstrap_fingerprint",
    "legacy_generation_sha", "target_generation_sha", "target_url",
    "source_control_sha", "pin_path", "source_pin_blob_sha",
    "failed_run_id", "repair_sha", "repair_dispatch_id", "executor",
    "model", "reasoning_effort", "model_source", "effort_source",
})

# A distinct, post-terminal PRIMARY consumer requires separate exact activation.
_SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA: str | None = "5d8ac589cbb4f611816d2926cff1989eda4eb74d"
_SOURCE_BOOTSTRAP_SUCCESSOR_FIELDS = frozenset({
    "format", "version", "repository", "bootstrap_fingerprint", "failed_run_id",
    "legacy_generation_sha", "prior_target_generation_sha",
    "target_generation_sha", "target_url", "source_control_sha",
    "current_control_sha", "pin_path", "source_pin_blob_sha", "task_id",
    "prior_task_revision", "task_revision", "task_blob_sha", "task_commit_sha",
    "successor_delivery_id", "executor",
})


def _exact_source_bootstrap_successor_intent(document: Any) -> dict[str, Any]:
    if (not isinstance(document, dict) or set(document) != _SOURCE_BOOTSTRAP_SUCCESSOR_FIELDS
            or type(document["version"]) is not int or document["version"] != 1
            or document["format"] != "AIOS_SOURCE_BOOTSTRAP_SUCCESSOR_INTENT"):
        raise OperatorError("source-bootstrap successor intent has missing or unexpected fields")
    for field in ("legacy_generation_sha", "prior_target_generation_sha",
                  "target_generation_sha", "source_control_sha", "current_control_sha",
                  "source_pin_blob_sha", "task_blob_sha", "task_commit_sha"):
        if not isinstance(document[field], str) or not re.fullmatch(r"[0-9a-f]{40}", document[field]):
            raise OperatorError(f"source-bootstrap successor {field} is not an exact SHA")
    if (not isinstance(document["bootstrap_fingerprint"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", document["bootstrap_fingerprint"])):
        raise OperatorError("source-bootstrap successor fingerprint is invalid")
    from .repair_dispatch import FAILED_RUN_ID_PATTERN, REPAIR_DISPATCH_ID_PATTERN
    if (not isinstance(document["failed_run_id"], str)
            or not FAILED_RUN_ID_PATTERN.fullmatch(document["failed_run_id"])
            or not isinstance(document["successor_delivery_id"], str)
            or not REPAIR_DISPATCH_ID_PATTERN.fullmatch(document["successor_delivery_id"])):
        raise OperatorError("source-bootstrap successor delivery or RUN identity is invalid")
    if (type(document["prior_task_revision"]) is not int
            or type(document["task_revision"]) is not int
            or document["prior_task_revision"] < 1
            or document["task_revision"] <= document["prior_task_revision"]):
        raise OperatorError("source-bootstrap successor revision is not strictly newer")
    if (not isinstance(document["repository"], str)
            or not Path(document["repository"]).is_absolute()
            or not isinstance(document["target_url"], str) or not document["target_url"]
            or document["target_url"].startswith("-")
            or not isinstance(document["pin_path"], str)
            or not isinstance(document["task_id"], str)
            or not re.fullmatch(r"TASK-[0-9]+", document["task_id"])
            or not isinstance(document["executor"], str)
            or document["executor"] not in {"codex", "antigravity", "antigravity-minimax"}):
        raise OperatorError("source-bootstrap successor identity is invalid")
    pin = Path(document["pin_path"])
    if pin.is_absolute() or not pin.parts or ".." in pin.parts or ".git" in pin.parts:
        raise OperatorError("source-bootstrap successor pin path is unsafe")
    if len({document["legacy_generation_sha"], document["prior_target_generation_sha"],
            document["target_generation_sha"]}) != 3:
        raise OperatorError("source-bootstrap successor generations are not distinct")
    return document


def _exact_source_repair_bootstrap_intent(document: Any) -> dict[str, Any]:
    if (not isinstance(document, dict) or set(document) != _SOURCE_REPAIR_BOOTSTRAP_FIELDS
            or type(document["version"]) is not int or document["version"] != 1
            or document["format"] != "AIOS_SOURCE_REPAIR_BOOTSTRAP_INTENT"):
        raise OperatorError("source-REPAIR bootstrap intent has missing or unexpected fields")
    for field in ("bootstrap_fingerprint",):
        if not isinstance(document[field], str) or not re.fullmatch(r"[0-9a-f]{64}", document[field]):
            raise OperatorError(f"source-REPAIR bootstrap {field} is invalid")
    for field in ("legacy_generation_sha", "target_generation_sha", "source_control_sha",
                  "source_pin_blob_sha", "repair_sha"):
        if not isinstance(document[field], str) or not re.fullmatch(r"[0-9a-f]{40}", document[field]):
            raise OperatorError(f"source-REPAIR bootstrap {field} is not an exact SHA")
    from .repair_dispatch import REPAIR_DISPATCH_ID_PATTERN, FAILED_RUN_ID_PATTERN
    if (not isinstance(document["repair_dispatch_id"], str)
            or not REPAIR_DISPATCH_ID_PATTERN.fullmatch(document["repair_dispatch_id"])
            or not isinstance(document["failed_run_id"], str)
            or not FAILED_RUN_ID_PATTERN.fullmatch(document["failed_run_id"])):
        raise OperatorError("source-REPAIR bootstrap delivery or failed RUN is invalid")
    if (not isinstance(document["repository"], str) or not Path(document["repository"]).is_absolute()
            or not isinstance(document["target_url"], str) or not document["target_url"]
            or document["target_url"].startswith("-")
            or not isinstance(document["pin_path"], str)):
        raise OperatorError("source-REPAIR bootstrap source or repository is invalid")
    pin = Path(document["pin_path"])
    if pin.is_absolute() or not pin.parts or ".." in pin.parts or ".git" in pin.parts:
        raise OperatorError("source-REPAIR bootstrap pin path is unsafe")
    if document["legacy_generation_sha"] == document["target_generation_sha"]:
        raise OperatorError("source-REPAIR bootstrap does not change generation")
    if document["executor"] not in (None, "codex", "antigravity", "antigravity-minimax"):
        raise OperatorError("source-REPAIR bootstrap Executor is invalid")
    for field in ("model", "reasoning_effort", "model_source", "effort_source"):
        if document[field] is not None and (not isinstance(document[field], str) or not document[field]):
            raise OperatorError(f"source-REPAIR bootstrap {field} is invalid")
    return document


def _exact_source_bootstrap_intent(document: Any) -> dict[str, Any]:
    """A distinct source-control bootstrap contract, never migration intent v1."""

    if (not isinstance(document, dict) or set(document) != _SOURCE_BOOTSTRAP_FIELDS
            or type(document.get("version")) is not int or document["version"] != 2
            or document.get("format") != "AIOS_SOURCE_CONTROL_BOOTSTRAP_INTENT"):
        raise OperatorError("source-control bootstrap intent has missing or unexpected fields")
    translated = {key: value for key, value in document.items() if key != "format"}
    translated.update({"version": 1,
                       "target_control_sha": document["source_control_sha"],
                       "target_pin_blob_sha": document["source_pin_blob_sha"]})
    _exact_migration_intent(translated)
    return document


def _source_bootstrap_control(intent: Mapping[str, Any]) -> dict[str, Any]:
    """Use the existing exact control checker with no prospective control commit."""

    return {**intent, "target_control_sha": intent["source_control_sha"],
            "target_pin_blob_sha": intent["source_pin_blob_sha"]}


def _source_bootstrap_check_control(intent: Mapping[str, Any]) -> dict[str, Any]:
    # The unchanged control pin still names the legacy generation. The target
    # generation is carried by the durable record, never inferred from this pin.
    return {**_source_bootstrap_control(intent),
            "target_generation_sha": intent["source_generation_sha"]}


def _check_source_bootstrap_control(
    root: Path, intent: Mapping[str, Any], *, transport: Path | None = None
) -> None:
    _check_migration_control(root, _source_bootstrap_check_control(intent), transport=transport)
    control = transport or root
    pin_path = intent["pin_path"].replace("\\", "/")
    content = _git(control, "show", f"{intent['source_control_sha']}:{pin_path}")
    if intent["target_generation_sha"] in content:
        raise OperatorError("source-control pin also names prospective target generation")


def _source_bootstrap_record(
    intent: Mapping[str, Any], fingerprint: str, bundle: str
) -> dict[str, Any]:
    record = _migration_record(_source_bootstrap_control(intent), fingerprint, bundle)
    record.update(format="AIOS_SOURCE_CONTROL_BOOTSTRAP_HANDOFF", version=2)
    return record


def _handoff_control_sha(intent: Mapping[str, Any]) -> str:
    return intent.get("target_control_sha", intent["source_control_sha"])


def _exact_migration_intent(document: Any) -> dict[str, Any]:
    """Reject incomplete, floating, and ambiguous cross-generation intent."""

    if not isinstance(document, dict) or set(document) != _MIGRATION_FIELDS:
        raise OperatorError("migration intent has missing or unexpected fields")
    if type(document["version"]) is not int or document["version"] != 1:
        raise OperatorError("unsupported migration intent version")
    for field in _MIGRATION_FIELDS:
        if field in {"version", "task_revision"}:
            continue
        if not isinstance(document[field], str) or not document[field]:
            raise OperatorError(f"migration intent {field} must be a nonempty string")
    for field in _MIGRATION_FIELDS:
        if field.endswith("_sha") and not re.fullmatch(r"[0-9a-f]{40}", document[field]):
            raise OperatorError(f"migration intent {field} must be an exact Git SHA")
    if type(document["task_revision"]) is not int or document["task_revision"] < 1:
        raise OperatorError("migration intent task_revision is invalid")
    if document["executor"] not in {"codex", "antigravity", "antigravity-minimax"}:
        raise OperatorError("migration intent executor is invalid")
    if document["source_generation_sha"] == document["target_generation_sha"]:
        raise OperatorError("migration intent does not change generation")
    pin = Path(document["pin_path"])
    if pin.is_absolute() or ".." in pin.parts or ".git" in pin.parts or not pin.parts:
        raise OperatorError("migration intent pin_path is unsafe")
    if not re.fullmatch(r"TASK-[0-9]+", document["task_id"]):
        raise OperatorError("migration intent task_id is invalid")
    return document


def _installed_generation_sha() -> str:
    """Read the immutable VCS commit of the actually imported distribution."""

    try:
        distribution = importlib.metadata.distribution("aios-renew")
        direct_url = json.loads(distribution.read_text("direct_url.json") or "null")
        sha = direct_url["vcs_info"]["commit_id"]
        installed = Path(distribution.locate_file("aios_renew/operator.py")).resolve()
    except (KeyError, TypeError, ValueError, OSError, importlib.metadata.PackageNotFoundError) as exc:
        raise OperatorError("active installed generation has no exact VCS identity") from exc
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise OperatorError("active installed generation has no exact VCS identity")
    if installed != Path(__file__).resolve():
        raise OperatorError("imported package differs from active installed generation")
    if (direct_url.get("dir_info") or {}).get("editable"):
        raise OperatorError("editable package cannot anchor migration authority")
    return sha


def _legacy_installed_generation_sha(
    *, runner: NativeRunner = subprocess.run,
) -> str:
    """Attest the imported *installed* legacy operator in an isolated interpreter.

    The bootstrap module may be loaded from the reviewed target checkout. It
    therefore cannot use its own __file__ or metadata as the legacy witness.
    Isolated mode discards PYTHONPATH, including synchronized downstream source.
    """

    probe = """import importlib.metadata as metadata
import json
import pathlib
import re
import aios_renew.operator as active
dist = metadata.distribution('aios-renew')
origin = json.loads(dist.read_text('direct_url.json') or 'null')
sha = origin['vcs_info']['commit_id']
installed = pathlib.Path(dist.locate_file('aios_renew/operator.py')).resolve()
actual = pathlib.Path(active.__file__).resolve()
source = actual.read_text(encoding='utf-8')
if (installed != actual or origin['vcs_info']['vcs'] != 'git'
        or not isinstance(origin.get('url'), str) or not origin['url']
        or (origin.get('dir_info') or {}).get('editable')
        or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha)
        or 'migrate-primary' in source or hasattr(active, 'migrate_primary')):
    raise SystemExit(2)
print(json.dumps({'generation': sha, 'operator': str(actual)}))
"""
    try:
        completed = runner(
            [sys.executable, "-I", "-c", probe], capture_output=True,
            text=True, check=False,
        )
        if completed.returncode != 0:
            raise OperatorError("legacy installed generation attestation failed")
        witness = json.loads(completed.stdout)
        sha = witness["generation"]
        operator = Path(witness["operator"])
        if (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
                or not operator.is_absolute() or not operator.is_file()):
            raise OperatorError("legacy installed generation attestation is invalid")
        return sha
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OperatorError("legacy installed generation attestation failed") from exc


def _upgrade_installed_generation_sha(
    *, runner: NativeRunner = subprocess.run,
) -> str:
    """Witness the exact migration-capable installed source outside control code."""

    probe = """import importlib.metadata as metadata
import json
import pathlib
import re
import aios_renew.operator as active
dist = metadata.distribution('aios-renew')
origin = json.loads(dist.read_text('direct_url.json') or 'null')
sha = origin['vcs_info']['commit_id']
installed = pathlib.Path(dist.locate_file('aios_renew/operator.py')).resolve()
actual = pathlib.Path(active.__file__).resolve()
if (installed != actual or not actual.is_file()
        or origin['vcs_info']['vcs'] != 'git'
        or not isinstance(origin.get('url'), str) or not origin['url']
        or (origin.get('dir_info') or {}).get('editable')
        or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha)
        or sha != '31fd2482cd87d97fd818e05eb5b4dcec69ffeee6'
        or not callable(getattr(active, 'migrate_primary', None))
        or 'migrate-primary' not in actual.read_text(encoding='utf-8')):
    raise SystemExit(2)
print(json.dumps({'generation': sha, 'operator': str(actual)}))
"""
    try:
        completed = runner(
            [sys.executable, "-I", "-c", probe], capture_output=True,
            text=True, check=False,
        )
        if completed.returncode != 0:
            raise OperatorError("upgrade installed generation attestation failed")
        witness = json.loads(completed.stdout)
        sha = witness["generation"]
        operator = Path(witness["operator"])
        if (sha != _SOURCE_UPGRADE_SOURCE_SHA or not operator.is_absolute()
                or not operator.is_file()):
            raise OperatorError("upgrade installed generation attestation is invalid")
        return sha
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OperatorError("upgrade installed generation attestation failed") from exc


def _successor_installed_generation_sha(
    *, runner: NativeRunner = subprocess.run,
) -> str:
    """Attest installed provenance; the failed lineage decides the required SHA.

    The reviewed control module and staged target may each live outside the
    isolated interpreter's installed distribution. Neither source path or
    migration capability classifies the installed generation here.
    """

    probe = """import importlib.metadata as metadata
import json
import pathlib
import re
import aios_renew.operator as active
matches = [candidate for candidate in metadata.distributions()
           if re.sub('[-_.]+', '-', candidate.metadata.get('Name', '')).lower() == 'aios-renew']
if len(matches) != 1:
    raise SystemExit(2)
dist = metadata.distribution('aios-renew')
origin = json.loads(dist.read_text('direct_url.json') or 'null')
sha = origin['vcs_info']['commit_id']
installed = pathlib.Path(dist.locate_file('aios_renew/operator.py')).resolve()
actual = pathlib.Path(active.__file__).resolve()
if (installed != actual or not actual.is_file()
        or origin['vcs_info']['vcs'] != 'git'
        or not isinstance(origin.get('url'), str) or not origin['url']
        or 'dir_info' in origin or 'archive_info' in origin
        or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha)):
    raise SystemExit(2)
print(json.dumps({'generation': sha, 'operator': str(actual)}))
"""
    try:
        completed = runner(
            [sys.executable, "-I", "-c", probe], capture_output=True,
            text=True, check=False,
        )
        if completed.returncode != 0:
            raise OperatorError("successor installed generation attestation failed")
        witness = json.loads(completed.stdout)
        sha = witness["generation"]
        operator = Path(witness["operator"])
        if (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
                or not operator.is_absolute() or not operator.is_file()):
            raise OperatorError("successor installed generation attestation is invalid")
        return sha
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OperatorError("successor installed generation attestation failed") from exc


def _migration_fingerprint(intent: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(intent, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _migration_marker(root: Path, fingerprint: str) -> Path:
    return runtime_state_root(root) / "migration-handoffs" / f"{fingerprint}.json"


def _migration_record(
    intent: Mapping[str, Any], fingerprint: str, bundle: str
) -> dict[str, Any]:
    return {
        "format": "AIOS_MIGRATION_HANDOFF",
        "version": 1,
        "fingerprint": fingerprint,
        "bundle": bundle,
        **{key: intent[key] for key in (
            "source_generation_sha", "target_generation_sha", "repository",
            "source_control_sha", "target_control_sha", "pin_path",
            "source_pin_blob_sha", "target_pin_blob_sha", "task_id",
            "task_revision", "task_blob_sha", "task_commit_sha", "executor",
        )},
        "target_url_sha256": hashlib.sha256(intent["target_url"].encode("utf-8")).hexdigest(),
    }


def _migration_storage_no_links(path: Path) -> None:
    """Reject links and Windows junctions, including in storage ancestors."""

    for component in (path, *path.parents):
        try:
            mode = component.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(mode.st_mode)
                or getattr(mode, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise OperatorError("migration transport contains a linked path")


def _migration_storage(marker: Path, *, create: bool = False) -> Path:
    """A profile-local locator, owned by the full repository Git state path.

    Checkout depth must not amplify either the repository cwd or pytest/TEMP
    depth. The digest addresses storage only; an exclusive owner record binds
    the complete state path and makes a collision or interrupted allocation
    fail closed. No environment or Git path configuration is changed.
    """

    state = marker.parent.parent
    _migration_storage_no_links(marker)
    identity = {"format": "AIOS_MIGRATION_STORAGE", "version": 1,
                "state_root": str(state.resolve())}
    key = hashlib.sha256(identity["state_root"].encode("utf-8")).hexdigest()[:16]
    _migration_storage_no_links(Path.home())
    parent = Path.home().resolve() / ".aios-m"
    storage = parent / key
    _migration_storage_no_links(storage)
    # Reserve at least 99 characters for real Git internals (including split
    # commit-graph/pack lock filenames) beneath the control clone.
    if len(str(storage / "c")) > 160:
        raise OperatorError("migration transport exceeds bounded checkout path budget")
    if create:
        parent.mkdir(exist_ok=True)
        if not parent.is_dir():
            raise OperatorError("migration storage parent is not a directory")
        try:
            storage.mkdir()
        except FileExistsError:
            pass
        else:
            with (storage / "owner.json").open("x", encoding="utf-8") as owner:
                json.dump(identity, owner, sort_keys=True)
    _migration_storage_no_links(storage / "owner.json")
    try:
        owner = json.loads((storage / "owner.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OperatorError("migration storage owner is partial or invalid") from exc
    if (owner != identity or not isinstance(owner, dict)
            or type(owner.get("version")) is not int):
        raise OperatorError("migration storage owner mismatch")
    if {entry.name for entry in storage.iterdir()} - {"owner.json", "c", "t"}:
        raise OperatorError("invalid migration storage entry")
    return storage


def _migration_storage_tree_no_links(storage: Path) -> None:
    pending = [storage]
    while pending:
        directory = pending.pop()
        _migration_storage_no_links(directory)
        for entry in directory.iterdir():
            _migration_storage_no_links(entry)
            if entry.is_dir():
                pending.append(entry)


def _migration_target_storage(
    marker: Path, intent: Mapping[str, Any], bundle: Path, *, create: bool = False,
) -> Path:
    """Back the unchanged bundle/source worktree with a short real Git dir.

    Git's ordinary gitfile is not a filesystem link. Immutable target readers
    still see the historical bundle, intent and exact detached Git worktree.
    The truncated address grants no authority: allocation is exclusive and
    the owner binds the full runtime path, fingerprint, bundle and intent.
    """

    fingerprint = _migration_fingerprint(intent)
    _migration_storage_no_links(marker)
    _migration_storage_no_links(bundle)
    identity = {"format": "AIOS_MIGRATION_TARGET_STORAGE", "version": 1,
                "state_root": str(marker.parent.parent.resolve()),
                "fingerprint": fingerprint, "bundle": str(bundle.resolve()),
                "intent": dict(intent)}
    storage = _migration_storage(marker) / "t" / fingerprint[:16]
    _migration_storage_no_links(storage / "owner.json")
    if len(str(storage / "g")) > 160:
        raise OperatorError("migration target exceeds bounded checkout path budget")
    if create:
        storage.parent.mkdir(exist_ok=True)
        try:
            storage.mkdir()
        except FileExistsError as exc:
            raise OperatorError("orphaned or colliding migration target storage") from exc
        with (storage / "owner.json").open("x", encoding="utf-8") as owner:
            json.dump(identity, owner, sort_keys=True)
    try:
        owner = json.loads((storage / "owner.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OperatorError("migration target storage owner is partial or invalid") from exc
    if (not isinstance(owner, dict)
            or json.dumps(owner, sort_keys=True) != json.dumps(identity, sort_keys=True)):
        raise OperatorError("migration target storage owner mismatch")
    if {entry.name for entry in storage.iterdir()} - {"owner.json", "g"}:
        raise OperatorError("invalid migration target storage entry")
    _migration_storage_tree_no_links(storage)
    if any((storage / "g" / name).exists() for name in (
        "commondir", "objects/info/alternates", "objects/info/http-alternates",
    )):
        raise OperatorError("migration target Git storage is ambiguous")
    return storage


def _migration_target_gitfile(target: Path, storage: Path) -> None:
    """Reject a substituted gitfile before reading or cleaning owned storage."""

    gitfile = target / ".git"
    _migration_storage_no_links(gitfile)
    try:
        locator = gitfile.read_text(encoding="utf-8").splitlines()
        if len(locator) != 1 or not locator[0].startswith("gitdir: "):
            raise ValueError("invalid gitfile")
        git_dir = Path(locator[0][8:])
        _migration_storage_no_links(git_dir)
        if not git_dir.is_absolute() or git_dir.resolve() != (storage / "g").resolve():
            raise ValueError("mismatched gitfile")
    except (OSError, ValueError) as exc:
        raise OperatorError("migration target Git storage mismatch") from exc


def _remove_migration_target(
    root: Path, marker: Path, intent: Mapping[str, Any], bundle: Path, storage: Path | None,
) -> None:
    """Clean only this allocation, and retain ambiguous or substituted state."""

    _migration_storage_no_links(bundle)
    if storage is not None:
        if _migration_target_storage(marker, intent, bundle) != storage:
            raise OperatorError("migration target cleanup owner mismatch")
        gitfile = bundle / "source" / ".git"
        _migration_storage_no_links(gitfile)
        if gitfile.exists():
            _migration_target_gitfile(bundle / "source", storage)
        _remove_historical_workspace(root, storage)
    _remove_historical_workspace(root, bundle)


@contextmanager
def _migration_control_checkout(root: Path, marker: Path) -> Iterator[Path]:
    """Use one short, exclusively created control checkout under the repo lock."""

    control = _migration_storage(marker, create=True) / "c"
    _migration_storage_no_links(control)
    try:
        control.mkdir()
    except FileExistsError as exc:
        raise OperatorError("orphaned migration control requires reconciliation") from exc
    try:
        # The normal Git transport avoids stat/copy traversal of depth-amplified
        # source object metadata as well as keeping destination paths bounded.
        _git(root, "clone", "--no-local", "--no-checkout", "--no-tags",
             str(root), str(control))
        yield control
    finally:
        _migration_storage_no_links(control)
        _remove_historical_workspace(root, control)


def _migration_bundle_path(marker: Path, fingerprint: str, name: str) -> Path:
    """Keep transport short while retaining the full fingerprint in durable state."""

    if re.fullmatch(rf"{fingerprint[:12]}-[a-zA-Z0-9_-]+", name):
        parent = marker.parent.parent / "m"
    elif re.fullmatch(rf"{fingerprint}-[a-zA-Z0-9_-]+", name):
        # Existing handoffs used the full fingerprint under migration-handoffs.
        parent = marker.parent
    else:
        raise OperatorError("migration handoff bundle name mismatch")
    bundle = parent / name
    if bundle.resolve().parent != parent.resolve():
        raise OperatorError("migration handoff bundle escapes runtime state")
    return bundle


def _migration_bundle(
    marker: Path, record: Mapping[str, Any], intent: Mapping[str, Any]
) -> tuple[Path, Path]:
    """Resolve only the durable source and intent bound before handoff."""

    fingerprint = _migration_fingerprint(intent)
    if not isinstance(record, Mapping):
        raise OperatorError("migration handoff record mismatch")
    bundle_name = record.get("bundle")
    if not isinstance(bundle_name, str):
        raise OperatorError("migration handoff record mismatch")
    expected = (_source_bootstrap_record(intent, fingerprint, bundle_name)
                if intent.get("version") == 2 else
                _migration_record(intent, fingerprint, bundle_name))
    if record != expected:
        raise OperatorError("migration handoff record mismatch")
    bundle = _migration_bundle_path(marker, fingerprint, bundle_name)
    bound_intent = bundle / "intent.json"
    _migration_storage_no_links(bound_intent)
    try:
        parser = (_exact_source_bootstrap_intent if intent.get("version") == 2
                  else _exact_migration_intent)
        stored_intent = parser(json.loads(bound_intent.read_text(encoding="utf-8")))
    except (OSError, ValueError, OperatorError) as exc:
        raise OperatorError("migration bound intent is unavailable or invalid") from exc
    if stored_intent != intent:
        raise OperatorError("migration bound intent mismatch")
    target = bundle / "source"
    _migration_storage_no_links(target / ".git")
    if (target / ".git").is_file():
        storage = _migration_target_storage(marker, intent, bundle)
        _migration_target_gitfile(target, storage)
        if (not (storage / "g").is_dir()
                or Path(_git(target, "rev-parse", "--absolute-git-dir")).resolve() != (storage / "g").resolve()
                or Path(_git(target, "rev-parse", "--show-toplevel")).resolve() != target.resolve()):
            raise OperatorError("migration target Git storage mismatch")
    elif not (target / ".git").is_dir():
        raise OperatorError("migration bound target source mismatch")
    if (_git(target, "rev-parse", "HEAD") != intent["target_generation_sha"]
            or _git(target, "status", "--porcelain")
            or not (target / "src" / "aios_renew" / "operator.py").is_file()):
        raise OperatorError("migration bound target source mismatch")
    return bound_intent, target


def _migration_run_terminal(root: Path, intent: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Identify the one admitted migration RUN without creating another RUN."""

    state = runtime_paths(root)
    matches: list[str] = []
    for path in state.runs.glob("*.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(document, dict) and document.get("kind") == "REMEDIATION":
                execution = document["execution"]
                if not isinstance(execution, dict):
                    raise TypeError("invalid remediation execution")
                document = execution["run"]
            elif not isinstance(document, dict) or "kind" in document:
                raise TypeError("unknown RUN document shape")
            run = _run_from_data(document)
            if (not isinstance(run.run_id, str) or not _RUN_ID_PATTERN.fullmatch(run.run_id)
                    or path.stem != run.run_id or not isinstance(run.base_sha, str)
                    or not re.fullmatch(r"[0-9a-f]{40}", run.base_sha)
                    or not isinstance(run.task.id, str)
                    or not re.fullmatch(r"TASK-[0-9]+", run.task.id)
                    or type(run.task.revision) is not int or run.task.revision < 1
                    or run.executor not in {"codex", "antigravity", "antigravity-minimax"}
                    or not isinstance(run.workspace, str) or not Path(run.workspace).is_absolute()
                    or run.status not in {"ACTIVE", "SUCCESS", "FAILURE"}):
                raise ValueError("invalid RUN identity")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise OperatorError("invalid migration RUN record") from exc
        if (intent.get("version") == 2
                and run.base_sha == intent["source_control_sha"]
                and run.task.id == intent["task_id"]
                and Path(run.workspace).resolve() == root.resolve()
                and (run.task.revision != intent["task_revision"]
                     or run.executor != intent["executor"])):
            raise OperatorError("mismatched source-control bootstrap RUN history")
        if (run.base_sha == _handoff_control_sha(intent)
                and run.task.id == intent["task_id"]
                and run.task.revision == intent["task_revision"]
                and run.executor == intent["executor"]
                and Path(run.workspace).resolve() == root.resolve()):
            matches.append(run.run_id)
    if len(matches) > 1:
        raise OperatorError("ambiguous migration RUN history")
    if not matches:
        return None, None
    run_id = matches[0]
    result = (state.results / f"{run_id}.json").is_file()
    failure = (state.failures / f"{run_id}.json").is_file()
    if result and failure:
        raise OperatorError("migration RUN has conflicting terminal state")
    if result:
        try:
            payload = json.loads((state.results / f"{run_id}.json").read_text(encoding="utf-8"))
            canonical = validate_result(payload["result"])
            if not re.fullmatch(r"[0-9a-f]{40}", canonical.head_sha):
                raise ValueError("invalid RESULT head")
        except (OSError, ValueError, KeyError, TypeError, ArtifactValidationError) as exc:
            raise OperatorError("invalid migration RESULT artifact") from exc
    if failure:
        try:
            payload = json.loads((state.failures / f"{run_id}.json").read_text(encoding="utf-8"))
            if (payload["kind"] != "FAILURE" or payload["run_id"] != run_id
                    or payload["task"] != {"id": intent["task_id"], "revision": intent["task_revision"]}
                    or payload["executor"] != intent["executor"]
                    or payload["base_sha"] != _handoff_control_sha(intent)):
                raise ValueError("FAILURE identity mismatch")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise OperatorError("invalid migration FAILURE artifact") from exc
    return run_id, "RESULT" if result else "FAILURE" if failure else None


def _complete_migration(marker: Path, fingerprint: str) -> None:
    completed = marker.with_suffix(".completed")
    if completed.is_file():
        if completed.read_text(encoding="utf-8") != fingerprint:
            raise OperatorError("migration completion marker mismatch")
        return
    _write_migration_atomic(completed, fingerprint)


def _write_migration_atomic(path: Path, content: str) -> None:
    """Publish a complete handoff transition in one filesystem rename."""

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f"{path.stem}-", suffix=".pending", delete=False,
    ) as stream:
        pending = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def _launch_migration_target(
    root: Path, marker: Path, bound_intent: Path, target: Path,
    runner: NativeRunner, *, source_bootstrap: bool = False,
) -> int:
    """Transport the existing handoff; all target entry is serialized there."""

    env = dict(os.environ)
    checkout_source = str((root / "src").resolve())
    inherited = [part for part in env.get("PYTHONPATH", "").split(os.pathsep)
                 if part and str(Path(part).resolve()) != checkout_source]
    env["PYTHONPATH"] = os.pathsep.join([str(target / "src"), *inherited])
    command = [sys.executable, "-m", "aios_renew.operator",
               "bootstrap-source-primary" if source_bootstrap else "migrate-primary",
               str(bound_intent), "--accept-handoff", str(marker)]
    return runner(command, env=env).returncode


def _migration_history(root: Path) -> tuple[
    dict[str, tuple[Path, dict[str, Any], dict[str, Any]]], set[str], set[str]
]:
    """Validate durable edges and both sides of each committed supersession."""

    directory = runtime_state_root(root) / "migration-handoffs"
    edges: dict[str, tuple[Path, dict[str, Any], dict[str, Any]]] = {}
    if not directory.is_dir():
        return edges, set(), set()
    for marker in directory.glob("*.json"):
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
            if (not isinstance(record, dict) or record.get("fingerprint") != marker.stem
                    or not re.fullmatch(r"[0-9a-f]{64}", marker.stem)
                    or not isinstance(record.get("repository"), str)
                    or Path(record["repository"]).resolve() != root.resolve()
                    or (record.get("format"), record.get("version")) not in {
                        ("AIOS_MIGRATION_HANDOFF", 1),
                        ("AIOS_SOURCE_CONTROL_BOOTSTRAP_HANDOFF", 2),
                    }):
                raise ValueError("invalid handoff record")
            bundle = _migration_bundle_path(marker, marker.stem, record["bundle"])
            parser = (_exact_source_bootstrap_intent if record["version"] == 2
                      else _exact_migration_intent)
            intent = parser(json.loads((bundle / "intent.json").read_text(encoding="utf-8")))
            _migration_bundle(marker, record, intent)
            for suffix in (".consumed", ".completed"):
                artifact = marker.with_suffix(suffix)
                if artifact.exists() and artifact.read_text(encoding="utf-8") != marker.stem:
                    raise ValueError("handoff state mismatch")
        except OperatorError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise OperatorError("invalid migration handoff history") from exc
        edges[marker.stem] = marker, record, intent
    superseded: set[str] = set()
    predecessors: set[str] = set()
    successors: set[str] = set()
    pending: set[str] = set()
    pending_olds: set[str] = set()
    for prior in directory.glob("*.predecessor"):
        try:
            payload = json.loads(prior.read_text(encoding="utf-8"))
            old_fp = payload["old_fingerprint"]
            if (payload != {"format": "AIOS_SOURCE_BOOTSTRAP_SUPERSESSION",
                            "old_fingerprint": old_fp,
                            "replacement_fingerprint": prior.stem}
                    or not re.fullmatch(r"[0-9a-f]{64}", prior.stem)
                    or old_fp not in edges or old_fp == prior.stem
                    or old_fp in pending_olds):
                raise ValueError("invalid predecessor")
            pending_olds.add(old_fp)
            if not _migration_marker(root, old_fp).with_suffix(".superseded").exists():
                old_marker, _, old_intent = edges[old_fp]
                if (old_intent.get("version") != 2
                        or not old_marker.with_suffix(".consumed").is_file()):
                    raise ValueError("pending predecessor has no consumed v2 edge")
                if prior.stem in edges:
                    new_marker, _, new_intent = edges[prior.stem]
                    if (new_intent.get("version") != 2
                            or not _recovery_identity_matches(old_intent, new_intent)
                            or new_intent["target_generation_sha"] == old_intent["target_generation_sha"]
                            or new_marker.with_suffix(".consumed").exists()
                            or new_marker.with_suffix(".completed").exists()):
                        raise ValueError("pending replacement is not admissible")
                pending.add(prior.stem)
        except (OSError, ValueError, KeyError, TypeError, OperatorError) as exc:
            raise OperatorError("invalid migration predecessor evidence") from exc
    for marker, _, _ in edges.values():
        prior = marker.with_suffix(".predecessor")
        if prior.exists():
            predecessors.add(marker.stem)
        link = marker.with_suffix(".superseded")
        if not link.exists():
            continue
        try:
            payload = json.loads(link.read_text(encoding="utf-8"))
            successor = payload["replacement_fingerprint"]
            if (payload != {"format": "AIOS_SOURCE_BOOTSTRAP_SUPERSESSION",
                            "old_fingerprint": marker.stem,
                            "replacement_fingerprint": successor}
                    or successor not in edges or successor == marker.stem
                    or successor in successors
                    or marker.with_suffix(".predecessor").exists()
                    or edges[successor][0].with_suffix(".superseded").exists()
                    or not edges[successor][0].with_suffix(".predecessor").is_file()
                    or json.loads(edges[successor][0].with_suffix(".predecessor").read_text(encoding="utf-8")) != payload
                    or marker.stem in superseded):
                raise ValueError("supersession linkage mismatch")
            old_intent = edges[marker.stem][2]
            new_intent = edges[successor][2]
            if (old_intent.get("version") != 2 or new_intent.get("version") != 2
                    or not marker.with_suffix(".consumed").is_file()
                    or marker.with_suffix(".completed").exists()
                    or new_intent["target_generation_sha"] == old_intent["target_generation_sha"]
                    or not _recovery_identity_matches(old_intent, new_intent)):
                raise ValueError("supersession edge mismatch")
            if not _git_is_ancestor(root, old_intent["source_control_sha"],
                                    new_intent["source_control_sha"]):
                raise ValueError("supersession control is not a fast-forward")
            pin = new_intent["pin_path"].replace("\\", "/")
            old_control, new_control = (old_intent["source_control_sha"],
                                        new_intent["source_control_sha"])
            if (_git(root, "rev-parse", f"{old_control}:{pin}") != old_intent["source_pin_blob_sha"]
                    or _git(root, "rev-parse", f"{new_control}:{pin}") != new_intent["source_pin_blob_sha"]
                    or _git(root, "show", f"{old_control}:{pin}") != _git(root, "show", f"{new_control}:{pin}")):
                raise ValueError("supersession legacy pin changed")
            task_path = f".ai/tasks/{new_intent['task_id']}.yaml"
            if (not _git_is_ancestor(root, new_intent["task_commit_sha"], new_control)
                    or _git(root, "rev-parse", f"{new_control}:{task_path}") != new_intent["task_blob_sha"]
                    or _git(root, "rev-parse", f"{new_intent['task_commit_sha']}:{task_path}") != new_intent["task_blob_sha"]):
                raise ValueError("supersession TASK authorization mismatch")
            task = parse_task(_git(root, "show", f"{new_control}:{task_path}"))
            if task.task_id != new_intent["task_id"] or task.revision != new_intent["task_revision"]:
                raise ValueError("supersession TASK revision mismatch")
            superseded.add(marker.stem)
            successors.add(successor)
        except (OSError, ValueError, KeyError, TypeError, OperatorError, TaskValidationError) as exc:
            raise OperatorError("invalid migration supersession evidence") from exc
    if predecessors != successors | (pending & set(edges)):
        raise OperatorError("orphaned migration supersession evidence")
    for artifact in (*directory.glob("*.superseded"), *directory.glob("*.predecessor"),
                     *directory.glob("*.consumed"), *directory.glob("*.completed")):
        if artifact.stem not in edges and not (
            artifact.suffix == ".predecessor" and artifact.stem in pending
        ):
            raise OperatorError("orphaned migration handoff evidence")
    active_sources: set[str] = set()
    active_targets: set[str] = set()
    for fingerprint, (_, record, _) in edges.items():
        if fingerprint in superseded or fingerprint in pending:
            continue
        source = record["source_generation_sha"]
        target = record["target_generation_sha"]
        if source in active_sources or target in active_targets:
            raise OperatorError("ambiguous active migration handoff history")
        active_sources.add(source)
        active_targets.add(target)
    return edges, superseded, pending


def _recovery_identity_matches(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    preserved = ("source_generation_sha", "target_url", "repository", "pin_path",
                 "source_pin_blob_sha", "task_id", "executor")
    if any(old[key] != new[key] for key in preserved):
        return False
    if new["task_revision"] == old["task_revision"]:
        return all(new[key] == old[key] for key in ("task_blob_sha", "task_commit_sha"))
    return new["task_revision"] > old["task_revision"]


def _require_no_active_migration(
    root: Path, *, installed_generation_sha: str | None = None,
    replay_fingerprint: str | None = None,
) -> None:
    edges, superseded, pending = _migration_history(root)
    if installed_generation_sha is not None and pending:
        raise OperatorError("pending migration handoff history")
    if replay_fingerprint is not None:
        if (installed_generation_sha is None or replay_fingerprint not in edges
                or replay_fingerprint in superseded or replay_fingerprint in pending
                or edges[replay_fingerprint][0].with_suffix(".completed").exists()):
            raise OperatorError("ambiguous source-control upgrade handoff state")
    links: dict[str, str] = {}
    markers: dict[str, Path] = {}
    targets: set[str] = set()
    for fingerprint, (marker, record, _) in edges.items():
        if fingerprint in superseded or fingerprint in pending:
            continue
        source = record["source_generation_sha"]
        target = record["target_generation_sha"]
        if (not isinstance(source, str) or not isinstance(target, str)
                or (record.get("format"), record.get("version")) not in {
                    ("AIOS_MIGRATION_HANDOFF", 1),
                    ("AIOS_SOURCE_CONTROL_BOOTSTRAP_HANDOFF", 2),
                }
                or record.get("fingerprint") != marker.stem
                or not isinstance(record.get("repository"), str)
                or Path(record.get("repository", "")).resolve() != root.resolve()
                or not re.fullmatch(r"[0-9a-f]{40}", source)
                or not re.fullmatch(r"[0-9a-f]{40}", target)
                or source in links or target in targets or source == target):
            raise OperatorError("ambiguous migration handoff history")
        if fingerprint == replay_fingerprint:
            if source != installed_generation_sha:
                raise OperatorError("active generation was superseded by migration handoff")
            continue
        links[source] = target
        markers[source] = marker
        targets.add(target)
    if not links:
        return
    roots = set(links) - targets
    if len(roots) != 1:
        raise OperatorError("ambiguous migration handoff history")
    visited: set[str] = set()
    current = next(iter(roots))
    final_marker: Path | None = None
    while current in links:
        if current in visited:
            raise OperatorError("cyclic migration handoff history")
        visited.add(current)
        final_marker = markers[current]
        current = links[current]
    if len(visited) != len(links):
        raise OperatorError("ambiguous migration handoff history")
    if (installed_generation_sha if installed_generation_sha is not None
            else _installed_generation_sha()) != current:
        raise OperatorError("active generation was superseded by migration handoff")
    if final_marker is None or not final_marker.with_suffix(".completed").is_file():
        raise OperatorError("migration target has not completed exact handoff")
    if final_marker.with_suffix(".completed").read_text(encoding="utf-8") != final_marker.stem:
        raise OperatorError("migration completion marker mismatch")


def _require_migration_target(root: Path, fingerprint: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise OperatorError("invalid migration handoff fingerprint")
    marker = _migration_marker(root, fingerprint)
    edges, superseded, pending = _migration_history(root)
    if fingerprint not in edges or fingerprint in superseded or fingerprint in pending:
        raise OperatorError("migration target is not the active exact handoff")
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        consumed = marker.with_suffix(".consumed").read_text(encoding="utf-8")
        target_sha = record["target_generation_sha"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OperatorError("migration target has no consumed exact handoff") from exc
    if ((record.get("format"), record.get("version")) not in {
                ("AIOS_MIGRATION_HANDOFF", 1),
                ("AIOS_SOURCE_CONTROL_BOOTSTRAP_HANDOFF", 2),
            }
            or record.get("fingerprint") != fingerprint
            or not isinstance(record.get("repository"), str)
            or Path(record.get("repository", "")).resolve() != root.resolve()
            or consumed != fingerprint):
        raise OperatorError("migration target handoff mismatch")
    bundle_name = record.get("bundle")
    if not isinstance(bundle_name, str):
        raise OperatorError("migration target handoff mismatch")
    bundle_path = _migration_bundle_path(marker, fingerprint, bundle_name)
    try:
        parser = (_exact_source_bootstrap_intent
                  if record.get("version") == 2 else _exact_migration_intent)
        intent = parser(json.loads((bundle_path / "intent.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, OperatorError) as exc:
        raise OperatorError("migration bound intent is unavailable or invalid") from exc
    _, target = _migration_bundle(marker, record, intent)
    target_source = Path(__file__).resolve().parent.parent.parent
    if target_source != target.resolve() or _git(target_source, "rev-parse", "HEAD") != target_sha:
        raise OperatorError("running source differs from admitted migration target")
    if marker.with_suffix(".completed").exists():
        raise OperatorError("migration handoff already completed")
    return record


def _validate_migration_execution(
    record: Mapping[str, Any], *, task_id: str, executor: str,
    synchronize: bool, preflight_sha: str | None, dispatch_id: str | None,
    task_revision: int | None, task_blob_sha: str | None,
    task_commit_sha: str | None,
) -> None:
    if (synchronize or dispatch_id is not None
            or record.get("task_id") != task_id
            or record.get("executor") != executor
            or record.get("target_control_sha") != preflight_sha
            or record.get("task_revision") != task_revision
            or record.get("task_blob_sha") != task_blob_sha
            or record.get("task_commit_sha") != task_commit_sha):
        raise OperatorError("migration execution differs from exact admitted TASK")


def _check_migration_control(
    root: Path, intent: Mapping[str, Any], *, transport: Path | None = None
) -> None:
    if Path(intent["repository"]).resolve() != root.resolve():
        raise OperatorError("migration repository mismatch")
    if _git(root, "status", "--porcelain"):
        raise OperatorError("migration control repository is dirty")
    if _git(root, "symbolic-ref", "--quiet", "--short", "HEAD") != "main":
        raise OperatorError("migration control branch is not main")
    if _git(root, "rev-parse", "HEAD") != intent["source_control_sha"]:
        raise OperatorError("migration source control SHA is stale")
    if _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}").split("/")[-1] != "main":
        raise OperatorError("migration upstream is not main")
    remote = _git(root, "config", "--get-all", "branch.main.remote").splitlines()
    merge_ref = _git(root, "config", "--get-all", "branch.main.merge").splitlines()
    if len(remote) != 1 or len(merge_ref) != 1 or merge_ref[0] != "refs/heads/main":
        raise OperatorError("migration upstream is ambiguous")
    control = transport or root
    fetch_remote = remote[0]
    if transport is not None:
        upstream_url = _git(root, "remote", "get-url", remote[0])
        _git(transport, "remote", "set-url", "origin", upstream_url)
        fetch_remote = "origin"
    _git(control, "fetch", "--no-tags", fetch_remote, merge_ref[0])
    upstream = _git(control, "rev-parse", f"{fetch_remote}/main")
    if upstream != intent["target_control_sha"]:
        raise OperatorError("migration target control SHA is stale")
    if not _git_is_ancestor(control, intent["source_control_sha"], upstream):
        raise OperatorError("migration target control is not a fast-forward")
    pin_path = intent["pin_path"].replace("\\", "/")
    for field, commit in (("source_pin_blob_sha", intent["source_control_sha"]),
                          ("target_pin_blob_sha", upstream)):
        try:
            blob = _git(control, "rev-parse", f"{commit}:{pin_path}")
            content = _git(control, "show", f"{commit}:{pin_path}")
        except OperatorError as exc:
            raise OperatorError("migration pin artifact is unavailable") from exc
        if blob != intent[field]:
            raise OperatorError("migration pin artifact mismatch")
        generation = intent["source_generation_sha" if field.startswith("source") else "target_generation_sha"]
        other = intent["target_generation_sha" if field.startswith("source") else "source_generation_sha"]
        if content.count(generation) != 1 or (other != generation and other in content):
            raise OperatorError("migration pin does not uniquely name exact generation")
    task_path = f".ai/tasks/{intent['task_id']}.yaml"
    try:
        task_blob = _git(control, "rev-parse", f"{upstream}:{task_path}")
        authorized_blob = _git(control, "rev-parse", f"{intent['task_commit_sha']}:{task_path}")
        task = parse_task(_git(control, "show", f"{upstream}:{task_path}"))
    except (OperatorError, TaskValidationError) as exc:
        raise OperatorError("migration TASK artifact is unavailable or invalid") from exc
    if not _git_is_ancestor(control, intent["task_commit_sha"], upstream):
        raise OperatorError("migration TASK authorization commit is stale")
    if (task_blob != intent["task_blob_sha"] or authorized_blob != task_blob
            or task.task_id != intent["task_id"] or task.revision != intent["task_revision"]):
        raise OperatorError("migration TASK identity mismatch")


def _stage_migration_handoff(
    root: Path, intent: Mapping[str, Any], marker: Path, *,
    allow_historical: bool = False,
    installed_generation_sha: str | None = None,
) -> tuple[Path, Path]:
    """Bind exact transport before granting the target entry."""

    fingerprint = _migration_fingerprint(intent)
    source_bootstrap = intent.get("version") == 2
    control_intent = _source_bootstrap_check_control(intent) if source_bootstrap else intent
    _require_no_active_migration(root, installed_generation_sha=installed_generation_sha)
    with RepositoryLock(runtime_paths(root).lock), _migration_control_checkout(root, marker) as control:
        if source_bootstrap:
            _check_source_bootstrap_control(root, intent, transport=control)
        else:
            _check_migration_control(root, control_intent, transport=control)
        marker.parent.mkdir(parents=True, exist_ok=True)
        transport_dir = runtime_state_root(root) / "m"
        transport_dir.mkdir(parents=True, exist_ok=True)
        if list(transport_dir.glob(f"{fingerprint[:12]}-*")):
            raise OperatorError("orphaned migration transport requires reconciliation")
        bundle = Path(tempfile.mkdtemp(prefix=f"{fingerprint[:12]}-", dir=transport_dir))
        target_storage = None
        try:
            target = bundle / "source"
            target_storage = _migration_target_storage(marker, intent, bundle, create=True)
            _git(root, "clone", "--no-local", "--no-checkout", "--no-tags",
                 "--separate-git-dir", str(target_storage / "g"), intent["target_url"], str(target))
            _git(target, "checkout", "--detach", intent["target_generation_sha"])
            if (_git(target, "rev-parse", "HEAD") != intent["target_generation_sha"]
                    or _git(target, "status", "--porcelain")
                    or not (target / "src" / "aios_renew" / "operator.py").is_file()):
                raise OperatorError("target generation checkout mismatch")
            bound_intent = bundle / "intent.json"
            bound_intent.write_text(json.dumps(intent, sort_keys=True), encoding="utf-8")
            if source_bootstrap:
                _check_source_bootstrap_control(root, intent, transport=control)
            else:
                _check_migration_control(root, control_intent, transport=control)
            if marker.exists():
                raise OperatorError("migration handoff already exists")
            if not allow_historical and any(path != marker for path in marker.parent.glob("*.json")):
                raise OperatorError("ambiguous migration handoff history")
            _write_migration_atomic(
                marker,
                json.dumps(
                    _source_bootstrap_record(intent, fingerprint, bundle.name)
                    if source_bootstrap else _migration_record(intent, fingerprint, bundle.name),
                    sort_keys=True,
                ),
            )
            return bound_intent, target
        except BaseException:
            if not marker.exists():
                _remove_migration_target(root, marker, intent, bundle, target_storage)
            raise


def bootstrap_primary(
    intent_path: str | Path,
    *,
    runner: NativeRunner = subprocess.run,
    legacy_runner: NativeRunner = subprocess.run,
) -> int:
    """One-time legacy admission into the existing TASK-197 target handoff."""

    intent = _exact_migration_intent(json.loads(Path(intent_path).read_text(encoding="utf-8")))
    root = resolve_repository(intent["repository"])
    fingerprint = _migration_fingerprint(intent)
    marker = _migration_marker(root, fingerprint)
    admission = _new_admission(
        "PRIMARY", phase="MIGRATION_PRE_HANDOFF", reason_code="MIGRATION_PRE_HANDOFF_REJECTED",
        task_id=intent["task_id"], executor=intent["executor"],
    )
    try:
        if intent["target_generation_sha"] != _BOOTSTRAP_TARGET_SHA:
            raise OperatorError("bootstrap target is not the first reviewed migration-capable generation")
        if _legacy_installed_generation_sha(runner=legacy_runner) != intent["source_generation_sha"]:
            raise OperatorError("bootstrap legacy generation mismatch")
        # A completed edge cannot be used as a second migration entry point.
        if marker.with_suffix(".completed").exists():
            raise OperatorError("bootstrap handoff already completed")
        if marker.is_file():
            siblings = list(marker.parent.glob("*.json"))
            if siblings != [marker]:
                raise OperatorError("ambiguous bootstrap handoff state")
            record = json.loads(marker.read_text(encoding="utf-8"))
            bound_intent, target = _migration_bundle(marker, record, intent)
        else:
            bound_intent, target = _stage_migration_handoff(root, intent, marker)
        return _launch_migration_target(root, marker, bound_intent, target, runner)
    except BaseException as exc:
        if not marker.exists():
            _persist_and_transport_admission_failure(root, admission=admission, failure=exc)
        raise


def bootstrap_source_upgrade_primary(
    intent_path: str | Path,
    *,
    runner: NativeRunner = subprocess.run,
    witness_runner: NativeRunner = subprocess.run,
) -> int:
    """Stage only the activated migration-capable v2 source-control edge."""

    intent = _exact_source_bootstrap_intent(
        json.loads(Path(intent_path).read_text(encoding="utf-8"))
    )
    if (intent["source_generation_sha"] != _SOURCE_UPGRADE_SOURCE_SHA
            or intent["target_generation_sha"] != _SOURCE_UPGRADE_TARGET_SHA):
        raise OperatorError("source-control upgrade edge is not activated")
    installed_source_sha = _upgrade_installed_generation_sha(runner=witness_runner)
    if installed_source_sha != intent["source_generation_sha"]:
        raise OperatorError("source-control upgrade installed generation mismatch")

    root = resolve_repository(intent["repository"])
    fingerprint = _migration_fingerprint(intent)
    marker = _migration_marker(root, fingerprint)
    if marker.with_suffix(".completed").exists():
        raise OperatorError("source-control upgrade handoff already completed")
    if marker.is_file():
        edges, superseded, pending = _migration_history(root)
        active = {edge_fp for edge_fp, (edge_marker, _, _) in edges.items()
                  if edge_fp not in superseded and not edge_marker.with_suffix(".completed").is_file()}
        if pending or fingerprint in superseded or active != {fingerprint}:
            raise OperatorError("ambiguous source-control upgrade handoff state")
        _require_no_active_migration(
            root, installed_generation_sha=installed_source_sha,
            replay_fingerprint=fingerprint,
        )
        record = edges[fingerprint][1]
        bound_intent, target = _migration_bundle(marker, record, intent)
    else:
        bound_intent, target = _stage_migration_handoff(
            root, intent, marker, allow_historical=True,
            installed_generation_sha=installed_source_sha,
        )
    return _launch_migration_target(
        root, marker, bound_intent, target, runner, source_bootstrap=True
    )


def bootstrap_source_primary(
    intent_path: str | Path,
    *,
    runner: NativeRunner = subprocess.run,
    legacy_runner: NativeRunner = subprocess.run,
    handoff_path: str | Path | None = None,
) -> int:
    """Stage or consume one source-control legacy bootstrap authority edge."""

    intent = _exact_source_bootstrap_intent(
        json.loads(Path(intent_path).read_text(encoding="utf-8"))
    )
    root = resolve_repository(intent["repository"])
    fingerprint = _migration_fingerprint(intent)
    marker = _migration_marker(root, fingerprint)
    if handoff_path is None:
        # Only the reviewed target bound in the single activation slot is admitted.
        if (_SOURCE_BOOTSTRAP_TARGET_SHA is None
                or intent["target_generation_sha"] != _SOURCE_BOOTSTRAP_TARGET_SHA):
            raise OperatorError("source-control bootstrap target is not activated")
        if _legacy_installed_generation_sha(runner=legacy_runner) != intent["source_generation_sha"]:
            raise OperatorError("source-control bootstrap legacy generation mismatch")
        admission = _new_admission(
            "PRIMARY", phase="MIGRATION_PRE_HANDOFF",
            reason_code="MIGRATION_PRE_HANDOFF_REJECTED",
            task_id=intent["task_id"], executor=intent["executor"],
        )
        try:
            if marker.with_suffix(".completed").exists():
                raise OperatorError("source-control bootstrap handoff already completed")
            if marker.is_file():
                siblings = list(marker.parent.glob("*.json"))
                if siblings != [marker]:
                    raise OperatorError("ambiguous source-control bootstrap handoff state")
                record = json.loads(marker.read_text(encoding="utf-8"))
                bound_intent, target = _migration_bundle(marker, record, intent)
            else:
                bound_intent, target = _stage_migration_handoff(root, intent, marker)
            return _launch_migration_target(
                root, marker, bound_intent, target, runner, source_bootstrap=True
            )
        except BaseException as exc:
            if not marker.exists():
                _persist_and_transport_admission_failure(root, admission=admission, failure=exc)
            raise

    if Path(handoff_path).resolve() != marker.resolve():
        raise OperatorError("source-control bootstrap handoff marker mismatch")
    with RepositoryLock(marker.with_suffix(".lock")):
        edges, superseded, pending = _migration_history(root)
        if fingerprint not in edges or fingerprint in superseded or fingerprint in pending:
            raise OperatorError("source-control bootstrap handoff was superseded")
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OperatorError("source-control bootstrap handoff is unavailable") from exc
        _, target = _migration_bundle(marker, record, intent)
        source_root = Path(__file__).resolve().parent.parent.parent
        if (source_root != target.resolve()
                or _git(source_root, "rev-parse", "HEAD") != intent["target_generation_sha"]):
            raise OperatorError("running source differs from bound bootstrap target")
        consumed = marker.with_suffix(".consumed")
        if consumed.is_file():
            if consumed.read_text(encoding="utf-8") != fingerprint:
                raise OperatorError("source-control bootstrap consumed marker mismatch")
        else:
            _write_migration_atomic(consumed, fingerprint)
        run_id, terminal = _migration_run_terminal(root, intent)
        if run_id is not None:
            if terminal is None:
                raise OperatorError(f"migration RUN {run_id} is active; reconcile its persisted state")
            _complete_migration(marker, fingerprint)
            return 0 if terminal == "RESULT" else 1
        if marker.with_suffix(".completed").exists():
            raise OperatorError("bootstrap completion has no bound RUN")
        with RepositoryLock(runtime_paths(root).lock):
            _check_source_bootstrap_control(root, intent)
        try:
            summary = run_task(
                intent["task_id"], executor=intent["executor"], repo=root,
                synchronize=False, preflight_sha=intent["source_control_sha"],
                task_revision=intent["task_revision"], task_blob_sha=intent["task_blob_sha"],
                task_commit_sha=intent["task_commit_sha"],
                _migration_handoff=fingerprint,
            )
        except BaseException:
            _, terminal = _migration_run_terminal(root, intent)
            if terminal is not None:
                _complete_migration(marker, fingerprint)
            raise
        _, terminal = _migration_run_terminal(root, intent)
        if terminal != "RESULT":
            raise OperatorError("bootstrap execution returned without a terminal RESULT")
        _complete_migration(marker, fingerprint)
        print(summary.render())
        return 0


def recover_source_bootstrap(
    old_intent_path: str | Path, replacement_intent_path: str | Path,
    *, legacy_runner: NativeRunner = subprocess.run,
) -> int:
    """Commit one exact pre-RUN v2 supersession without launching a target."""

    old = _exact_source_bootstrap_intent(json.loads(Path(old_intent_path).read_text(encoding="utf-8")))
    new = _exact_source_bootstrap_intent(json.loads(Path(replacement_intent_path).read_text(encoding="utf-8")))
    root = resolve_repository(old["repository"])
    old_fp, new_fp = _migration_fingerprint(old), _migration_fingerprint(new)
    old_marker, new_marker = _migration_marker(root, old_fp), _migration_marker(root, new_fp)
    if (new_fp == old_fp or not _recovery_identity_matches(old, new)
            or new["target_generation_sha"] == old["target_generation_sha"]):
        raise OperatorError("replacement source-bootstrap identity is unauthorized")
    # Match the target's lock order so a consumed old target cannot race RUN
    # reservation against the pre-RUN supersession check.
    with RepositoryLock(old_marker.with_suffix(".lock")), RepositoryLock(runtime_paths(root).lock):
        edges, superseded, pending = _migration_history(root)
        if old_fp not in edges or edges[old_fp][2] != old:
            raise OperatorError("selected old source-bootstrap edge is unavailable")
        if pending:
            raise OperatorError("pending migration supersession evidence")
        successors = {record["source_generation_sha"]: record["target_generation_sha"]
                      for fingerprint, (_, record, _) in edges.items() if fingerprint not in superseded}
        for source in successors:
            seen: set[str] = set()
            current = source
            while current in successors:
                if current in seen:
                    raise OperatorError("cyclic migration handoff history")
                seen.add(current)
                current = successors[current]
        if old_fp in superseded:
            link = json.loads(old_marker.with_suffix(".superseded").read_text(encoding="utf-8"))
            if link["replacement_fingerprint"] == new_fp and edges[new_fp][2] == new:
                return 0
            raise OperatorError("source-bootstrap edge already has another successor")
        if (_SOURCE_BOOTSTRAP_TARGET_SHA is None
                or new["target_generation_sha"] != _SOURCE_BOOTSTRAP_TARGET_SHA):
            raise OperatorError("replacement source-bootstrap target is not activated")
        # Completed handoffs and committed supersessions are immutable history.
        # The selected edge must be the sole uncompleted authority edge.
        active = {fingerprint for fingerprint, (marker, _, _) in edges.items()
                  if fingerprint not in superseded and not marker.with_suffix(".completed").is_file()}
        if active != {old_fp}:
            raise OperatorError("source-bootstrap edge is not the sole active handoff")
        if (not old_marker.with_suffix(".consumed").is_file()
                or old_marker.with_suffix(".completed").exists()
                or old_marker.with_suffix(".consumed").read_text(encoding="utf-8") != old_fp):
            raise OperatorError("source-bootstrap edge is not one consumed pre-RUN edge")
        if _migration_run_terminal(root, old)[0] is not None:
            raise OperatorError("source-bootstrap edge already has a bound RUN")
        if _legacy_installed_generation_sha(runner=legacy_runner) != old["source_generation_sha"]:
            raise OperatorError("source-bootstrap legacy generation mismatch")
        if (not _git_is_ancestor(root, old["source_control_sha"], new["source_control_sha"])
                or Path(new["repository"]).resolve() != root.resolve()):
            raise OperatorError("replacement control is not a fast-forward of old control")
        if new_marker.exists() or new_marker.with_suffix(".predecessor").exists():
            raise OperatorError("replacement handoff already exists")
        with _migration_control_checkout(root, new_marker) as control:
            _check_source_bootstrap_control(root, new, transport=control)
            old_pin = _git(control, "show", f"{old['source_control_sha']}:{old['pin_path']}")
            new_pin = _git(control, "show", f"{new['source_control_sha']}:{new['pin_path']}")
            if old_pin != new_pin:
                raise OperatorError("replacement legacy pin content changed")
            transport = runtime_state_root(root) / "m"
            transport.mkdir(parents=True, exist_ok=True)
            if list(transport.glob(f"{new_fp[:12]}-*")):
                raise OperatorError("orphaned replacement transport")
            bundle = Path(tempfile.mkdtemp(prefix=f"{new_fp[:12]}-", dir=transport))
            target_storage = None
            try:
                target = bundle / "source"
                target_storage = _migration_target_storage(new_marker, new, bundle, create=True)
                _git(root, "clone", "--no-local", "--no-checkout", "--no-tags",
                     "--separate-git-dir", str(target_storage / "g"), new["target_url"], str(target))
                _git(target, "checkout", "--detach", new["target_generation_sha"])
                bound = bundle / "intent.json"
                _write_migration_atomic(bound, json.dumps(new, sort_keys=True))
                record = _source_bootstrap_record(new, new_fp, bundle.name)
                if (_git(target, "rev-parse", "HEAD") != new["target_generation_sha"]
                        or _git(target, "status", "--porcelain")
                        or not (target / "src" / "aios_renew" / "operator.py").is_file()):
                    raise OperatorError("replacement target source mismatch")
                # Recheck every mutable authority before publishing staged evidence.
                _check_source_bootstrap_control(root, new, transport=control)
                if (_legacy_installed_generation_sha(runner=legacy_runner) != old["source_generation_sha"]
                        or _migration_run_terminal(root, old)[0] is not None
                        or old_marker.with_suffix(".completed").exists()):
                    raise OperatorError("source-bootstrap recovery authority changed")
                link = {"format": "AIOS_SOURCE_BOOTSTRAP_SUPERSESSION",
                        "old_fingerprint": old_fp, "replacement_fingerprint": new_fp}
                _write_migration_atomic(new_marker.with_suffix(".predecessor"), json.dumps(link, sort_keys=True))
                _write_migration_atomic(new_marker, json.dumps(record, sort_keys=True))
                _migration_bundle(new_marker, record, new)
                # This last atomic write is the only authority transition.
                _write_migration_atomic(old_marker.with_suffix(".superseded"), json.dumps(link, sort_keys=True))
            except BaseException:
                if not new_marker.exists() and not new_marker.with_suffix(".predecessor").exists():
                    _remove_migration_target(root, new_marker, new, bundle, target_storage)
                raise
    return 0


def _source_repair_bootstrap_lineage(root: Path, intent: Mapping[str, Any]) -> None:
    """Read the completed failed v2 edge without changing migration history."""

    edges, superseded, pending = _migration_history(root)
    eligible: list[str] = []
    for fingerprint, (marker, record, bootstrap) in edges.items():
        if (bootstrap.get("version") != 2 or fingerprint in superseded
                or fingerprint in pending):
            continue
        consumed = marker.with_suffix(".consumed")
        completed = marker.with_suffix(".completed")
        if not consumed.is_file() or not completed.is_file():
            raise OperatorError("active or incomplete source-bootstrap history blocks source-REPAIR")
        if (consumed.read_text(encoding="utf-8") != fingerprint
                or completed.read_text(encoding="utf-8") != fingerprint):
            raise OperatorError("source-REPAIR bootstrap handoff state mismatch")
        run_id, terminal = _migration_run_terminal(root, bootstrap)
        if run_id is not None and terminal == "FAILURE":
            eligible.append(fingerprint)
    if eligible != [intent["bootstrap_fingerprint"]]:
        raise OperatorError("source-REPAIR bootstrap requires one exact completed failed v2 edge")
    _, record, bootstrap = edges[eligible[0]]
    if (record["format"] != "AIOS_SOURCE_CONTROL_BOOTSTRAP_HANDOFF"
            or bootstrap["repository"] != intent["repository"]
            or bootstrap["source_generation_sha"] != intent["legacy_generation_sha"]
            or bootstrap["source_control_sha"] != intent["source_control_sha"]
            or bootstrap["pin_path"] != intent["pin_path"]
            or bootstrap["source_pin_blob_sha"] != intent["source_pin_blob_sha"]
            or _migration_run_terminal(root, bootstrap) != (intent["failed_run_id"], "FAILURE")):
        raise OperatorError("source-REPAIR bootstrap lineage identity mismatch")
    with tempfile.TemporaryDirectory(prefix="aios-source-repair-control-") as temporary:
        control = Path(temporary) / "control"
        _git(root, "clone", "--local", "--no-hardlinks", "--no-checkout", "--no-tags",
             str(root), str(control))
        _check_source_bootstrap_control(root, bootstrap, transport=control)


def _source_repair_bootstrap_state(root: Path, intent: Mapping[str, Any]) -> tuple[Path, Path]:
    # The digest is a bounded storage locator only. The full dispatch id and
    # every other transport binding remain authoritative in intent.json.
    key = hashlib.sha256(intent["repair_dispatch_id"].encode("utf-8")).hexdigest()[:16]
    bundle = runtime_state_root(root).parent / "r" / key
    return bundle, bundle / "s"


def _source_repair_bootstrap_record(root: Path, intent: Mapping[str, Any]) -> tuple[Path, Path]:
    bundle, target = _source_repair_bootstrap_state(root, intent)
    record_path = bundle / "intent.json"
    try:
        stored = _exact_source_repair_bootstrap_intent(
            json.loads(record_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, OperatorError) as exc:
        raise OperatorError("source-REPAIR bootstrap transport is partial or invalid") from exc
    if (stored != intent or bundle.is_symlink() or target.is_symlink()
            or record_path.is_symlink() or not target.is_dir()
            or {path.name for path in bundle.iterdir()} != {"s", "intent.json"}
            or _git(target, "rev-parse", "HEAD") != intent["target_generation_sha"]
            or _git(target, "remote", "get-url", "origin") != intent["target_url"]
            or _git(target, "status", "--porcelain")
            or not (target / "src" / "aios_renew" / "operator.py").is_file()):
        raise OperatorError("source-REPAIR bootstrap transport identity mismatch")
    return record_path, target


def _source_repair_failed_candidate_policy(
    root: Path, intent: Mapping[str, Any]
) -> ExecutionProfilePolicy:
    """Bind one policy blob and profile to the canonical failed RUN, without checkout."""

    from .review_transport import _read_remote_blob, resolve_transport_remote

    failed_run_id = intent["failed_run_id"]
    requested = tuple(intent[field] for field in (
        "executor", "model", "reasoning_effort", "model_source", "effort_source"
    ))
    if any(value is None for value in requested):
        raise OperatorError("source-REPAIR requires one complete managed profile")
    try:
        with _remote_observation_repository(root) as observer:
            recovery = resolve_remote_repair_recovery(
                observer, failed_run_id=failed_run_id
            )
            if not recovery.failures or recovery.failures[0].run_id != failed_run_id:
                raise OperatorError("source-REPAIR canonical failed RUN mismatch")
            artifact = recovery.failures[0]
            run = _run_from_data(_decode_remote_mapping(artifact.run, "RUN"))
            failure = _decode_remote_mapping(artifact.failure, "FAILURE")
            if (run.run_id != failed_run_id or run.executor != intent["executor"]
                    or failure.get("run_id") != failed_run_id
                    or failure.get("executor") != run.executor
                    or failure.get("base_sha") != run.base_sha
                    or failure.get("task") != {"id": run.task.id, "revision": run.task.revision}
                    or failure.get("failed_head_sha") != artifact.candidate_sha):
                raise OperatorError("source-REPAIR failed candidate lineage mismatch")
            if artifact.execution_profile is None:
                raise OperatorError("source-REPAIR canonical execution profile missing")
            profile = parse_execution_profile(artifact.execution_profile)
            actual = (profile.executor, profile.model, profile.reasoning_effort,
                      profile.model_source, profile.effort_source)
            if profile.run_id != failed_run_id or actual != requested:
                raise OperatorError("source-REPAIR canonical execution profile mismatch")
            policy_blob = _read_remote_blob(
                observer, resolve_transport_remote(observer),
                artifact.candidate_sha, ".ai/executor-profiles.yaml",
            )
            if policy_blob is None:
                raise OperatorError("source-REPAIR failed candidate policy missing")
            policy = parse_execution_profile_policy(policy_blob)
            validate_execution_profile(profile, policy)
            return policy
    except (ReviewTransportError, ExecutionProfileError, KeyError, TypeError,
            ValueError, UnicodeError) as exc:
        raise OperatorError(f"source-REPAIR failed candidate policy rejected: {exc}") from exc


def bootstrap_source_repair(
    intent_path: str | Path, *, runner: NativeRunner = subprocess.run,
    legacy_runner: NativeRunner = subprocess.run,
    transport_path: str | Path | None = None,
) -> int:
    """Transport one failed bootstrap candidate to the canonical REPAIR wakeup."""

    intent = _exact_source_repair_bootstrap_intent(
        json.loads(Path(intent_path).read_text(encoding="utf-8"))
    )
    root = resolve_repository(intent["repository"])
    bundle, target = _source_repair_bootstrap_state(root, intent)
    if transport_path is not None:
        if Path(transport_path).resolve() != (bundle / "intent.json").resolve():
            raise OperatorError("source-REPAIR bootstrap transport path mismatch")
        _source_repair_bootstrap_record(root, intent)
        source_root = Path(__file__).resolve().parent.parent.parent
        if source_root != target.resolve() or _git(source_root, "rev-parse", "HEAD") != intent["target_generation_sha"]:
            raise OperatorError("running source differs from bound source-REPAIR target")
        _source_repair_bootstrap_lineage(root, intent)
        policy = _source_repair_failed_candidate_policy(root, intent)
        token = _SOURCE_REPAIR_POLICY.set(policy)
        try:
            outcome = run_repair_wakeup(
                intent["repair_dispatch_id"], intent["failed_run_id"], intent["repair_sha"],
                executor=intent["executor"], repo=root, model=intent["model"],
                reasoning_effort=intent["reasoning_effort"], model_source=intent["model_source"],
                effort_source=intent["effort_source"],
            )
        finally:
            _SOURCE_REPAIR_POLICY.reset(token)
        print(outcome.render())
        return outcome.exit_code

    if (_SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA is None
            or intent["target_generation_sha"] != _SOURCE_REPAIR_BOOTSTRAP_TARGET_SHA):
        raise OperatorError("source-REPAIR bootstrap target is not activated")
    with RepositoryLock(runtime_state_root(root) / "source-repair-bootstrap.lock"):
        if _legacy_installed_generation_sha(runner=legacy_runner) != intent["legacy_generation_sha"]:
            raise OperatorError("source-REPAIR bootstrap legacy generation mismatch")
        _source_repair_bootstrap_lineage(root, intent)
        if bundle.exists():
            bound_intent, target = _source_repair_bootstrap_record(root, intent)
        else:
            bundle.mkdir(parents=True)
            _git(root, "clone", "--no-checkout", "--no-tags", intent["target_url"], str(target))
            _git(target, "checkout", "--detach", intent["target_generation_sha"])
            if (_git(target, "rev-parse", "HEAD") != intent["target_generation_sha"]
                    or _git(target, "status", "--porcelain")
                    or not (target / "src" / "aios_renew" / "operator.py").is_file()):
                raise OperatorError("source-REPAIR bootstrap target source mismatch")
            _source_repair_bootstrap_lineage(root, intent)
            bound_intent = bundle / "intent.json"
            _write_migration_atomic(bound_intent, json.dumps(intent, sort_keys=True))
        env = dict(os.environ)
        checkout_source = str((root / "src").resolve())
        inherited = [part for part in env.get("PYTHONPATH", "").split(os.pathsep)
                     if part and str(Path(part).resolve()) != checkout_source]
        env["PYTHONPATH"] = os.pathsep.join([str(target / "src"), *inherited])
        return runner([
            sys.executable, "-m", "aios_renew.operator", "bootstrap-source-repair",
            str(bound_intent), "--accept-transport", str(bound_intent),
        ], env=env).returncode


def _source_bootstrap_successor_state(root: Path, intent: Mapping[str, Any]) -> tuple[Path, Path]:
    # Keep the durable checkout below Win32's legacy path budget even when the
    # repository lives below a verification workspace. The digest is only a
    # directory locator: the full delivery id and intent remain authoritative.
    key = hashlib.sha256(intent["successor_delivery_id"].encode("utf-8")).hexdigest()[:16]
    bundle = runtime_state_root(root).parent / "s" / key
    return bundle, bundle / "s"


def _source_bootstrap_successor_record(root: Path, intent: Mapping[str, Any]) -> tuple[Path, Path]:
    bundle, target = _source_bootstrap_successor_state(root, intent)
    bound = bundle / "intent.json"
    try:
        stored = _exact_source_bootstrap_successor_intent(
            json.loads(bound.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, OperatorError) as exc:
        raise OperatorError("source-bootstrap successor transport is partial or invalid") from exc
    operator_source = target / "src" / "aios_renew" / "operator.py"
    if (stored != intent or bundle.is_symlink() or bound.is_symlink() or target.is_symlink()
            or not target.is_dir() or {path.name for path in bundle.iterdir()} != {"s", "intent.json"}
            or _git(target, "rev-parse", "HEAD") != intent["target_generation_sha"]
            or _git(target, "remote", "get-url", "origin") != intent["target_url"]
            or _git(target, "status", "--porcelain")
            or not operator_source.is_file() or operator_source.is_symlink()
            or _git(target, "cat-file", "-t", "HEAD:src/aios_renew/operator.py") != "blob"):
        raise OperatorError("source-bootstrap successor transport identity mismatch")
    return bound, target


def _source_bootstrap_successor_lineage(
    root: Path, intent: Mapping[str, Any], *, check_control: bool = True,
) -> None:
    """Select only the requested failed edge; other historical failures are independent."""

    edges, superseded, pending = _migration_history(root)
    fingerprint = intent["bootstrap_fingerprint"]
    if fingerprint not in edges or fingerprint in superseded or fingerprint in pending:
        raise OperatorError("source-bootstrap successor edge is unavailable")
    marker, record, old = edges[fingerprint]
    if (old.get("version") != 2
            or record["format"] != "AIOS_SOURCE_CONTROL_BOOTSTRAP_HANDOFF"
            or marker.with_suffix(".predecessor").exists()
            or marker.with_suffix(".superseded").exists()
            or not marker.with_suffix(".consumed").is_file()
            or not marker.with_suffix(".completed").is_file()
            or marker.with_suffix(".consumed").read_text(encoding="utf-8") != fingerprint
            or marker.with_suffix(".completed").read_text(encoding="utf-8") != fingerprint
            or _migration_run_terminal(root, old) != (intent["failed_run_id"], "FAILURE")):
        raise OperatorError("source-bootstrap successor requires one completed failed v2 edge")
    bindings = {
        "repository": "repository", "source_generation_sha": "legacy_generation_sha",
        "target_generation_sha": "prior_target_generation_sha",
        "source_control_sha": "source_control_sha", "pin_path": "pin_path",
        "source_pin_blob_sha": "source_pin_blob_sha", "task_id": "task_id",
        "task_revision": "prior_task_revision", "executor": "executor",
    }
    if any(old[prior] != intent[successor] for prior, successor in bindings.items()):
        raise OperatorError("source-bootstrap successor failed lineage identity mismatch")
    _require_no_active_migration(
        root, installed_generation_sha=intent["prior_target_generation_sha"]
    )
    directory = runtime_state_root(root).parent / "s"
    for bundle in directory.iterdir() if directory.is_dir() else ():
        if not bundle.is_dir() or bundle.is_symlink():
            raise OperatorError("source-bootstrap successor transport history is invalid")
        path = bundle / "intent.json"
        if not path.is_file():
            raise OperatorError("source-bootstrap successor transport history is partial")
        try:
            other = _exact_source_bootstrap_successor_intent(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, OperatorError) as exc:
            raise OperatorError("source-bootstrap successor transport history is invalid") from exc
        expected_bundle, _ = _source_bootstrap_successor_state(root, other)
        if (expected_bundle != bundle
                or other["bootstrap_fingerprint"] == fingerprint and other != intent):
            raise OperatorError("competing source-bootstrap successor evidence")
        _source_bootstrap_successor_record(root, other)
    if Path(intent["repository"]).resolve() != root.resolve():
        raise OperatorError("source-bootstrap successor repository mismatch")
    if not check_control:
        return
    if (_git(root, "status", "--porcelain")
            or _git(root, "symbolic-ref", "--quiet", "--short", "HEAD") != "main"
            or _git(root, "rev-parse", "HEAD") != intent["current_control_sha"]):
        raise OperatorError("source-bootstrap successor control is not clean attached main")
    if intent["current_control_sha"] == old["source_control_sha"]:
        raise OperatorError("source-bootstrap successor control has no newer TASK commit")
    with tempfile.TemporaryDirectory(prefix="aios-successor-control-") as temporary:
        control = Path(temporary) / "control"
        _git(root, "clone", "--local", "--no-hardlinks", "--no-checkout", "--no-tags",
             str(root), str(control))
        check = {
            **_source_bootstrap_check_control(old),
            "source_control_sha": intent["current_control_sha"],
            "target_control_sha": intent["current_control_sha"],
            "task_revision": intent["task_revision"],
            "task_blob_sha": intent["task_blob_sha"],
            "task_commit_sha": intent["task_commit_sha"],
        }
        _check_migration_control(root, check, transport=control)
        if not _git_is_ancestor(control, old["source_control_sha"], intent["current_control_sha"]):
            raise OperatorError("source-bootstrap successor control is not a fast-forward")
        pin = intent["pin_path"].replace("\\", "/")
        if (_git(control, "rev-parse", f"{old['source_control_sha']}:{pin}") != intent["source_pin_blob_sha"]
                or _git(control, "show", f"{old['source_control_sha']}:{pin}")
                != _git(control, "show", f"{intent['current_control_sha']}:{pin}")
                or intent["prior_target_generation_sha"] in _git(control, "show", f"{intent['current_control_sha']}:{pin}")
                or intent["target_generation_sha"] in _git(control, "show", f"{intent['current_control_sha']}:{pin}")):
            raise OperatorError("source-bootstrap successor pin authority changed")


def _source_bootstrap_successor_run(root: Path, intent: Mapping[str, Any]) -> tuple[str | None, str | None]:
    state = runtime_paths(root)
    matches = []
    for path in state.runs.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("kind") == "REMEDIATION":
                continue
            run = _run_from_data(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise OperatorError("invalid successor RUN history") from exc
        if (run.task.id == intent["task_id"]
                and run.task.revision > intent["prior_task_revision"]
                and Path(run.workspace).resolve() == root.resolve()):
            if (run.run_id != path.stem or run.task.revision != intent["task_revision"]
                    or run.executor != intent["executor"]
                    or run.base_sha != intent["current_control_sha"]):
                raise OperatorError("competing source-bootstrap successor RUN")
            matches.append(run.run_id)
    if len(matches) > 1:
        raise OperatorError("ambiguous source-bootstrap successor RUN")
    if not matches:
        return None, None
    run_id = matches[0]
    result = (state.results / f"{run_id}.json").is_file()
    failure = (state.failures / f"{run_id}.json").is_file()
    if result and failure:
        raise OperatorError("source-bootstrap successor RUN has conflicting terminals")
    if result:
        try:
            payload = json.loads((state.results / f"{run_id}.json").read_text(encoding="utf-8"))
            canonical = validate_result(payload["result"])
            if not re.fullmatch(r"[0-9a-f]{40}", canonical.head_sha):
                raise ValueError("invalid successor RESULT head")
        except (OSError, ValueError, KeyError, TypeError, ArtifactValidationError) as exc:
            raise OperatorError("invalid source-bootstrap successor RESULT") from exc
    if failure:
        try:
            payload = json.loads((state.failures / f"{run_id}.json").read_text(encoding="utf-8"))
            if (payload["kind"] != "FAILURE" or payload["run_id"] != run_id
                    or payload["task"] != {"id": intent["task_id"], "revision": intent["task_revision"]}
                    or payload["executor"] != intent["executor"]
                    or payload["base_sha"] != intent["current_control_sha"]
                    or not isinstance(payload["failed_head_sha"], str)
                    or not re.fullmatch(r"[0-9a-f]{40}", payload["failed_head_sha"])):
                raise ValueError("successor FAILURE identity mismatch")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise OperatorError("invalid source-bootstrap successor FAILURE") from exc
    return run_id, "RESULT" if result else "FAILURE" if failure else None


def _source_bootstrap_successor_replay_control(
    root: Path, run_id: str, terminal: str,
) -> None:
    state = runtime_paths(root)
    path = (state.results if terminal == "RESULT" else state.failures) / f"{run_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    head = (payload["result"]["head_sha"] if terminal == "RESULT"
            else payload["failed_head_sha"])
    if (not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head)
            or _git(root, "status", "--porcelain")
            or _git(root, "symbolic-ref", "--quiet", "--short", "HEAD") != "main"
            or _git(root, "rev-parse", "HEAD") != head):
        raise OperatorError("source-bootstrap successor replay control changed")


def _validate_successor_execution(
    root: Path, transport: str, task_id: str, executor: str,
    synchronize: bool, preflight_sha: str | None, dispatch_id: str | None,
    task_revision: int | None, task_blob_sha: str | None, task_commit_sha: str | None,
) -> None:
    path = Path(transport)
    try:
        intent = _exact_source_bootstrap_successor_intent(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, OperatorError) as exc:
        raise OperatorError("source-bootstrap successor bound intent is invalid") from exc
    bound, target = _source_bootstrap_successor_record(root, intent)
    source_root = Path(__file__).resolve().parent.parent.parent
    if (source_root != target.resolve() or path.resolve() != bound.resolve()
            or synchronize or dispatch_id is not None
            or (task_id, executor, preflight_sha, task_revision, task_blob_sha, task_commit_sha)
            != (intent["task_id"], intent["executor"], intent["current_control_sha"],
                intent["task_revision"], intent["task_blob_sha"], intent["task_commit_sha"])):
        raise OperatorError("source-bootstrap successor PRIMARY admission mismatch")
    _source_bootstrap_successor_lineage(root, intent)
    if _source_bootstrap_successor_run(root, intent)[0] is not None:
        raise OperatorError("source-bootstrap successor RUN already admitted")


def bootstrap_source_successor_primary(
    intent_path: str | Path, *, runner: NativeRunner = subprocess.run,
    legacy_runner: NativeRunner = subprocess.run,
    transport_path: str | Path | None = None,
) -> int:
    """Transport one completed failed bootstrap into a newer TASK PRIMARY."""

    intent = _exact_source_bootstrap_successor_intent(
        json.loads(Path(intent_path).read_text(encoding="utf-8"))
    )
    root = resolve_repository(intent["repository"])
    bundle, target = _source_bootstrap_successor_state(root, intent)
    if transport_path is None and (_SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA is None
            or intent["target_generation_sha"] != _SOURCE_BOOTSTRAP_SUCCESSOR_TARGET_SHA):
        raise OperatorError("source-bootstrap successor target is not activated")
    lock = runtime_state_root(root) / "source-bootstrap-successor.lock"
    if transport_path is not None:
        with RepositoryLock(lock):
            if Path(transport_path).resolve() != (bundle / "intent.json").resolve():
                raise OperatorError("source-bootstrap successor transport path mismatch")
            bound, target = _source_bootstrap_successor_record(root, intent)
            source_root = Path(__file__).resolve().parent.parent.parent
            if source_root != target.resolve():
                raise OperatorError("running source differs from bound successor target")
            if _successor_installed_generation_sha(runner=legacy_runner) != intent["legacy_generation_sha"]:
                raise OperatorError("source-bootstrap successor installed generation mismatch")
            run_id, terminal = _source_bootstrap_successor_run(root, intent)
            _source_bootstrap_successor_lineage(root, intent, check_control=run_id is None)
            if run_id is not None:
                if terminal is None:
                    raise OperatorError(f"source-bootstrap successor RUN {run_id} is active")
                _source_bootstrap_successor_replay_control(root, run_id, terminal)
                return 0 if terminal == "RESULT" else 1
            summary = run_task(
                intent["task_id"], executor=intent["executor"], repo=root,
                synchronize=False, preflight_sha=intent["current_control_sha"],
                task_revision=intent["task_revision"], task_blob_sha=intent["task_blob_sha"],
                task_commit_sha=intent["task_commit_sha"], _successor_transport=str(bound),
            )
            print(summary.render())
            return 0
    with RepositoryLock(lock):
        if _successor_installed_generation_sha(runner=legacy_runner) != intent["legacy_generation_sha"]:
            raise OperatorError("source-bootstrap successor installed generation mismatch")
        run_id, terminal = _source_bootstrap_successor_run(root, intent)
        _source_bootstrap_successor_lineage(root, intent, check_control=run_id is None)
        if run_id is not None and terminal is not None:
            _source_bootstrap_successor_replay_control(root, run_id, terminal)
        if bundle.exists():
            bound, target = _source_bootstrap_successor_record(root, intent)
        else:
            bundle.mkdir(parents=True)
            _git(root, "clone", "--no-checkout", "--no-tags", intent["target_url"], str(target))
            _git(target, "checkout", "--detach", intent["target_generation_sha"])
            bound = bundle / "intent.json"
            _write_migration_atomic(bound, json.dumps(intent, sort_keys=True))
            _source_bootstrap_successor_record(root, intent)
        _source_bootstrap_successor_lineage(root, intent, check_control=run_id is None)
        env = dict(os.environ)
        checkout_source = str((root / "src").resolve())
        inherited = [part for part in env.get("PYTHONPATH", "").split(os.pathsep)
                     if part and str(Path(part).resolve()) != checkout_source]
        env["PYTHONPATH"] = os.pathsep.join([str(target / "src"), *inherited])
    return runner([
        sys.executable, "-m", "aios_renew.operator", "bootstrap-source-successor-primary",
        str(bound), "--accept-transport", str(bound),
    ], env=env).returncode


def migrate_primary(
    intent_path: str | Path,
    *,
    runner: NativeRunner = subprocess.run,
    handoff_path: str | Path | None = None,
) -> int:
    """Admit under N, then transfer once to an exact isolated N+1 source."""

    intent = _exact_migration_intent(json.loads(Path(intent_path).read_text(encoding="utf-8")))
    root = resolve_repository(intent["repository"])
    fingerprint = _migration_fingerprint(intent)
    marker = _migration_marker(root, fingerprint)
    if handoff_path is not None:
        if Path(handoff_path).resolve() != marker.resolve():
            raise OperatorError("migration handoff marker mismatch")
        with RepositoryLock(marker.with_suffix(".lock")):
            try:
                record = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise OperatorError("migration handoff record is unavailable") from exc
            _, target = _migration_bundle(marker, record, intent)
            source_root = Path(__file__).resolve().parent.parent.parent
            if source_root != target.resolve():
                raise OperatorError("running source differs from bound migration target")
            if _git(source_root, "rev-parse", "HEAD") != intent["target_generation_sha"]:
                raise OperatorError("target source does not match admitted generation")
            consumed = marker.with_suffix(".consumed")
            if consumed.is_file():
                if consumed.read_text(encoding="utf-8") != fingerprint:
                    raise OperatorError("migration consumed marker mismatch")
            else:
                _write_migration_atomic(consumed, fingerprint)
            run_id, terminal = _migration_run_terminal(root, intent)
            if run_id is not None:
                if terminal is None:
                    raise OperatorError(f"migration RUN {run_id} is active; reconcile its persisted state")
                _complete_migration(marker, fingerprint)
                return 0 if terminal == "RESULT" else 1
            if marker.with_suffix(".completed").exists():
                raise OperatorError("migration completion has no bound RUN")
            with RepositoryLock(runtime_paths(root).lock):
                head = _git(root, "rev-parse", "HEAD")
                if head == intent["source_control_sha"]:
                    _check_migration_control(root, intent)
                    _git(root, "merge", "--ff-only", intent["target_control_sha"])
                elif head != intent["target_control_sha"]:
                    raise OperatorError("migration control HEAD differs from admitted handoff")
                if (_git(root, "rev-parse", "HEAD") != intent["target_control_sha"]
                        or _git(root, "status", "--porcelain")
                        or _git(root, "symbolic-ref", "--quiet", "--short", "HEAD") != "main"):
                    raise OperatorError("repository-integrity BLOCKED: migration fast-forward state is unsafe")
            try:
                summary = run_task(
                    intent["task_id"], executor=intent["executor"], repo=root,
                    synchronize=False, preflight_sha=intent["target_control_sha"],
                    task_revision=intent["task_revision"], task_blob_sha=intent["task_blob_sha"],
                    task_commit_sha=intent["task_commit_sha"],
                    _migration_handoff=fingerprint,
                )
            except BaseException:
                run_id, terminal = _migration_run_terminal(root, intent)
                if terminal is not None:
                    _complete_migration(marker, fingerprint)
                raise
            run_id, terminal = _migration_run_terminal(root, intent)
            if terminal != "RESULT":
                raise OperatorError("migration execution returned without a terminal RESULT")
            _complete_migration(marker, fingerprint)
            print(summary.render())
            return 0

    admission = _new_admission(
        "PRIMARY", phase="MIGRATION_PRE_HANDOFF", reason_code="MIGRATION_PRE_HANDOFF_REJECTED",
        task_id=intent["task_id"], executor=intent["executor"],
    )
    try:
        if _installed_generation_sha() != intent["source_generation_sha"]:
            raise OperatorError("migration source generation mismatch")
        if marker.is_file():
            record = json.loads(marker.read_text(encoding="utf-8"))
            bound_intent, target = _migration_bundle(marker, record, intent)
            return _launch_migration_target(root, marker, bound_intent, target, runner)
        bound_intent, target = _stage_migration_handoff(root, intent, marker)
        return _launch_migration_target(root, marker, bound_intent, target, runner)
    except BaseException as exc:
        if not marker.exists():
            _persist_and_transport_admission_failure(root, admission=admission, failure=exc)
        raise



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
    source_run_id: str | None = None,
    approved_remediation_sha: str | None = None,
    correction_dispatch_id: str | None = None,
    executor: str,
    repo: str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
    model: str | None = None,
    reasoning_effort: str | None = None,
    model_source: str | None = None,
    effort_source: str | None = None,
) -> RemediationSummary:
    """Execute one remediation and persist deterministic pre-PASS failures."""

    root = resolve_repository(repo)
    state = runtime_paths(root)
    observation_tracker = RunObservationTracker(
        "REMEDIATION", monotonic_clock=monotonic_clock
    )
    attempt = _RunAttempt()
    task: Task | None = None
    admission = _new_admission(
        "REMEDIATION",
        phase=(
            "REMOTE_LINEAGE_RESOLUTION"
            if finding_id is not None
            else "CANONICAL_CONTRACT_ADMISSION"
        ),
        reason_code=(
            "CANONICAL_LINEAGE_MISSING"
            if finding_id is not None
            else "TASK_CONTRACT_REJECTED"
        ),
        task_id=task_id,
        executor=executor,
        correction_dispatch_id=correction_dispatch_id,
    )
    if finding_id is not None:
        admission["finding_id"] = finding_id
    if source_run_id is not None:
        admission["source_run_id"] = source_run_id
    try:
        _set_admission_boundary(
            admission, "TASK_ADMISSION", "TASK_CONTRACT_REJECTED"
        )
        task = load_task(root, task_id)
        _bind_admission_task(admission, task)
        _set_admission_boundary(
            admission,
            "REMOTE_LINEAGE_RESOLUTION"
            if finding_id is not None
            else "CANONICAL_CONTRACT_ADMISSION",
            "CANONICAL_LINEAGE_MISSING"
            if finding_id is not None
            else "TASK_CONTRACT_REJECTED",
        )
        return _run_remediation_impl(
            task_id,
            review=review,
            remediation=remediation,
            prior_review=prior_review,
            finding_id=finding_id,
            source_run_id=source_run_id,
            approved_remediation_sha=approved_remediation_sha,
            correction_dispatch_id=correction_dispatch_id,
            executor=executor,
            model=model,
            reasoning_effort=reasoning_effort,
            model_source=model_source,
            effort_source=effort_source,
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
                _persist_remediation_failure(
                    root,
                    state=state,
                    attempt=attempt,
                    failure=original,
                    observation_tracker=observation_tracker,
                    interruption_phase=attempt.interruption_phase,
                )
        else:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
            )
        raise
    except Exception as original:
        if attempt.run_path is not None:
            if not (state.results / attempt.run_path.name).is_file():
                _persist_remediation_failure(
                    root,
                    state=state,
                    attempt=attempt,
                    failure=original,
                    observation_tracker=observation_tracker,
                    interruption_phase=attempt.interruption_phase,
                )
        else:
            _persist_and_transport_admission_failure(
                root,
                admission=admission,
                failure=original,
            )
        raise
    finally:
        _remove_historical_workspace(root, attempt.historical_workspace)


def _persist_remediation_failure(
    control_repo: Path,
    *,
    state: RuntimePaths,
    attempt: _RunAttempt,
    failure: BaseException,
    observation_tracker: RunObservationTracker,
    interruption_phase: str | None = None,
) -> None:
    """Persist an admitted FIX failure against its exact mutation subject."""

    if (
        attempt.run_path is None
        or attempt.subject_repo is None
        or attempt.task is None
    ):
        return
    try:
        run_data = json.loads(attempt.run_path.read_text(encoding="utf-8"))
        run = _remediation_execution_from_data(run_data["execution"]).run
        transfer_error = _prepare_historical_failure_transport(
            control_repo, attempt
        )
        persist_failure(
            attempt.subject_repo,
            state=state,
            task=attempt.task,
            run=run,
            run_path=attempt.run_path,
            failure=failure,
            observation_tracker=observation_tracker,
            interruption_phase=interruption_phase,
            transport=transfer_error is None,
            transport_repo=control_repo,
            verification_subject_sha=attempt.verification_subject_sha,
        )
        _persist_historical_transfer_error(
            state, attempt.run_path.stem, transfer_error
        )
    except Exception:
        # Failure recording is subordinate and never masks the admitted failure.
        return


def _persist_and_transport_admission_failure(
    root: Path,
    *,
    admission: Mapping[str, Any],
    failure: BaseException,
) -> None:
    """Best-effort one bounded v2 pre-RUN diagnostic without changing authority."""

    try:
        operation = admission.get("operation")
        phase = admission.get("phase")
        reason_code = admission.get("reason_code")
        if (
            operation not in _ADMISSION_OPERATIONS
            or phase not in (_ADMISSION_PHASES | {"MIGRATION_PRE_HANDOFF"})
            or reason_code not in (_ADMISSION_REASONS | {"MIGRATION_PRE_HANDOFF_REJECTED"})
        ):
            return
        message = _bounded_admission_message(failure)
        record: dict[str, Any] = {
            "format": "AIOS_ADMISSION_FAILURE",
            "version": 2,
            "kind": "ADMISSION_FAILURE",
            "operation": operation,
            "executor_invoked": False,
            "phase": phase,
            "reason_code": reason_code,
            "error": {
                "type": type(failure).__name__[:128],
                "message": message,
            },
        }
        requested_executor = admission.get("requested_executor")
        if requested_executor in ("codex", "antigravity", "antigravity-minimax"):
            record["requested_executor"] = requested_executor
        task = admission.get("task")
        if (
            isinstance(task, Mapping)
            and isinstance(task.get("id"), str)
            and isinstance(task.get("revision"), int)
        ):
            record["task"] = {
                "id": task["id"],
                "revision": task["revision"],
            }
        for name in (
            "requested_task_id",
            "finding_id",
            "review_id",
            "reviewed_sha",
            "failed_head_sha",
            "current_head_sha",
            "control_head_sha",
            "failed_run_id",
            "source_run_id",
            "dispatch_id",
            "correction_dispatch_id",
            "repair_dispatch_id",
            "task_blob_sha",
            "task_commit_sha",
            "current_task_blob_sha",
            "observed_ref",
            "observed_sha",
            "observed_snapshot_sha256",
        ):
            value = admission.get(name)
            if isinstance(value, str) and len(value) <= 256:
                record[name] = value
        requested_task_revision = admission.get("requested_task_revision")
        if (
            isinstance(requested_task_revision, int)
            and not isinstance(requested_task_revision, bool)
            and requested_task_revision > 0
        ):
            record["requested_task_revision"] = requested_task_revision
        observed_ref_count = admission.get("observed_ref_count")
        if isinstance(observed_ref_count, int) and observed_ref_count >= 0:
            record["observed_ref_count"] = observed_ref_count
        observed_refs = admission.get("observed_refs")
        if isinstance(observed_refs, list):
            bounded_refs = []
            for item in observed_refs[:8]:
                if (
                    isinstance(item, Mapping)
                    and isinstance(item.get("ref"), str)
                    and isinstance(item.get("sha"), str)
                    and len(item["ref"]) <= 256
                    and len(item["sha"]) <= 64
                ):
                    bounded_refs.append({"ref": item["ref"], "sha": item["sha"]})
            if bounded_refs:
                record["observed_refs"] = bounded_refs
        remote_error = _find_remote_query_error(failure)
        if remote_error is not None:
            record["reason_code"] = "REMOTE_TRANSPORT_UNAVAILABLE"
            record["remote_query"] = {
                "operation": "LS_REMOTE",
                "outcome": "UNAVAILABLE",
                "category": remote_error.category,
            }
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


def _new_admission(
    operation: str,
    *,
    phase: str,
    reason_code: str,
    task_id: str | None = None,
    executor: str | None = None,
    **facts: Any,
) -> dict[str, Any]:
    admission: dict[str, Any] = {
        "operation": operation,
        "phase": phase,
        "reason_code": reason_code,
    }
    if isinstance(task_id, str):
        admission["requested_task_id"] = task_id
    if executor in ("codex", "antigravity", "antigravity-minimax"):
        admission["requested_executor"] = executor
    admission.update(facts)
    return admission


def _bounded_admission_message(failure: BaseException) -> str:
    """Bound one operator error line and redact common credential shapes."""

    message = str(failure).splitlines()[0][:2048]
    message = re.sub(
        r"(?i)(https?://)[^/@\s]+@",
        r"\1[REDACTED]@",
        message,
    )
    message = re.sub(
        r"(?i)\b(token|password|passwd|secret|authorization|credential)"
        r"\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        message,
    )
    return message[:512]


def _bind_admission_task(admission: dict[str, Any], task: Task) -> None:
    admission["task"] = {"id": task.task_id, "revision": task.revision}


def _set_admission_boundary(
    admission: dict[str, Any], phase: str, reason_code: str
) -> None:
    admission["phase"] = phase
    admission["reason_code"] = reason_code


def _record_observed_refs(
    admission: dict[str, Any], refs: tuple[tuple[str, str], ...]
) -> None:
    """Bind one immutable bounded ref observation without retaining an unbounded list."""

    normalized = tuple(sorted((str(ref), str(sha)) for ref, sha in refs))
    snapshot = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
    admission["observed_snapshot_sha256"] = hashlib.sha256(snapshot).hexdigest()
    admission["observed_ref_count"] = len(normalized)
    requested_ids = {
        admission.get("failed_run_id"),
        admission.get("source_run_id"),
    }
    selected = [
        {"ref": ref, "sha": sha}
        for ref, sha in normalized
        if any(isinstance(identity, str) and identity in ref for identity in requested_ids)
    ][:8]
    if selected:
        admission["observed_refs"] = selected




_ADMISSION_OPERATIONS = frozenset(
    {"PRIMARY", "REMEDIATION", "REPAIR", "DIRECT_CANDIDATE", "RECOVER_PRIMARY"}
)


def _terminal_has_execution_base(terminal: RemoteLifecycleTerminal) -> bool:
    try:
        data = json.loads(terminal.run.decode("utf-8", errors="strict"))
        return isinstance(data, Mapping) and "execution_base" in data
    except Exception:
        return False


def _terminal_is_correction(terminal: RemoteLifecycleTerminal) -> bool:
    if terminal.correction is not None:
        return True
    try:
        data = json.loads(terminal.run.decode("utf-8", errors="strict"))
        if not isinstance(data, Mapping):
            return False
        return data.get("kind") in ("REMEDIATION", "REPAIR") or "predecessor" in data
    except Exception:
        return False


def _resolve_remediation_admission(
    task_id: str,
    *,
    review: Review | str | Path | None = None,
    remediation: Remediation | str | Path | None = None,
    prior_review: Review | str | Path | None = None,
    finding_id: str | None = None,
    source_run_id: str | None = None,
    approved_remediation_sha: str | None = None,
    repo: Path,
    state: RuntimePaths,
    resolved_task: Task | None = None,
    admission: dict[str, Any],
) -> _RemediationAdmission:
    """Resolve the shared REMEDIATION contract through the last pre-RUN boundary."""

    task = resolved_task or load_task(repo, task_id)
    explicit_mode = (
        review is not None or remediation is not None or prior_review is not None
    )
    remote_mode = finding_id is not None
    if source_run_id is not None and not remote_mode:
        raise OperatorError("source RUN binding requires remote finding mode")
    if explicit_mode and remote_mode:
        _set_admission_boundary(
            admission, "CANONICAL_CONTRACT_ADMISSION", "TASK_CONTRACT_REJECTED"
        )
        raise OperatorError(
            "remote finding mode cannot be mixed with explicit REVIEW/REMEDIATION artifacts"
        )
    if remote_mode:
        (
            canonical_review,
            canonical_remediation,
            prior_result,
            canonical_prior_review,
            task,
        ) = (
            _resolve_remote_remediation_lineage_with_reviewed_task(
                repo,
                task=task,
                finding_id=finding_id,
                source_run_id=source_run_id,
                required_remediation_sha=approved_remediation_sha,
                admission=admission,
            )
        )
        resolved_source_run_id = admission.get("source_run_id")
        if not resolved_source_run_id:
            raise OperatorError("missing canonical source RUN identity")
        if source_run_id is not None and source_run_id != resolved_source_run_id:
            raise OperatorError("mismatched source RUN identity")
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
        prior_result, resolved_source_run_id = _load_authoritative_prior_result(
            state,
            task,
            canonical_review.reviewed_sha,
            repo=repo,
        )
        if not resolved_source_run_id:
            raise OperatorError("missing canonical source RUN identity")
        if source_run_id is not None and source_run_id != resolved_source_run_id:
            raise OperatorError("mismatched source RUN identity")
        admission["source_run_id"] = resolved_source_run_id
    _record_admission_artifact_facts(
        admission, canonical_review, canonical_remediation
    )
    _bind_admission_task(admission, task)
    _set_admission_boundary(
        admission, "CANONICAL_CONTRACT_ADMISSION", "TASK_CONTRACT_REJECTED"
    )
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
    if (
        canonical_remediation.action == "CODE_FIX"
        and not canonical_remediation.modification_scope
    ):
        raise OperatorError("CODE_FIX remediation modification scope is empty")
    if not canonical_remediation.affected_verification:
        raise OperatorError("REMEDIATION affected verification is empty")

    execution_base_run_id = resolved_source_run_id
    execution_base_sha = canonical_remediation.reviewed_sha
    cumulative = False
    integrated_base = None
    if remote_mode:
        lifecycle = resolve_remote_task_lifecycle(
            repo, task_id=task.task_id, task_revision=task.revision
        )
        has_explicit_cumulative_signal = bool(lifecycle.reviews) or any(
            _terminal_has_execution_base(t) for t in lifecycle.terminals
        )
        first_correction_aligned_with_main = (
            not any(_terminal_is_correction(t) for t in lifecycle.terminals)
            and _git_is_ancestor(repo, lifecycle.main_sha, canonical_remediation.reviewed_sha)
        )
        if has_explicit_cumulative_signal or first_correction_aligned_with_main:
            try:
                from .unified_state import _resolve_cumulative_execution_base

                resolved_base = _resolve_cumulative_execution_base(
                    repo,
                    task,
                    lifecycle,
                    source_run_id=resolved_source_run_id,
                    review_id=canonical_review.review_id,
                    finding_id=canonical_remediation.finding_id,
                    reviewed_sha=canonical_remediation.reviewed_sha,
                    semantic_review=canonical_review,
                )
                execution_base_run_id = resolved_base[0]
                execution_base_sha = resolved_base[1]
                integrated_base = getattr(resolved_base, "integrated_base", None)
                cumulative = True
            except (
                CorrectionFrontierError, OperatorError, ReviewTransportError,
                TypeError, ValueError,
            ) as exc:
                reason = (
                    "INTEGRATION_REQUIRED"
                    if "require integration" in str(exc)
                    else "CUMULATIVE_BASE_REJECTED"
                )
                _set_admission_boundary(
                    admission, "REPOSITORY_ADMISSION", reason
                )
                raise OperatorError(f"cumulative execution base rejected: {exc}") from exc
    admission["execution_base_run_id"] = execution_base_run_id
    admission["execution_base_sha"] = execution_base_sha

    return _RemediationAdmission(
        review=canonical_review,
        remediation=canonical_remediation,
        prior_result=prior_result,
        prior_review=canonical_prior_review,
        task=task,
        remote_mode=remote_mode,
        source_run_id=resolved_source_run_id,
        execution_base_run_id=execution_base_run_id,
        execution_base_sha=execution_base_sha,
        cumulative=cumulative,
        integrated_base=integrated_base,
    )


def _run_remediation_impl(
    task_id: str,
    *,
    review: Review | str | Path | None = None,
    remediation: Remediation | str | Path | None = None,
    prior_review: Review | str | Path | None = None,
    finding_id: str | None = None,
    source_run_id: str | None = None,
    approved_remediation_sha: str | None = None,
    correction_dispatch_id: str | None = None,
    executor: str,
    repo: str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    resolved_task: Task | None = None,
    admission: dict[str, Any] | None = None,
    attempt: _RunAttempt | None = None,
    observation_tracker: RunObservationTracker,
    model: str | None = None,
    reasoning_effort: str | None = None,
    model_source: str | None = None,
    effort_source: str | None = None,
) -> RemediationSummary:
    """Execute one bound remediation without entering the TASK execution path."""

    root = resolve_repository(repo)
    admission = admission if admission is not None else {}
    state = runtime_paths(root)
    if correction_dispatch_id is not None and (
        source_run_id is None or approved_remediation_sha is None
    ):
        raise OperatorError(
            "correction dispatch requires exact source RUN and approval SHA"
        )
    resolved = _resolve_remediation_admission(
        task_id,
        review=review,
        remediation=remediation,
        prior_review=prior_review,
        finding_id=finding_id,
        source_run_id=source_run_id,
        approved_remediation_sha=approved_remediation_sha,
        repo=root,
        state=state,
        resolved_task=resolved_task,
        admission=admission,
    )
    canonical_review = resolved.review
    canonical_remediation = resolved.remediation
    task = resolved.task
    remote_mode = resolved.remote_mode
    remediation_authorization_sha = admission.get("remediation_authorization_sha")
    if not isinstance(remediation_authorization_sha, str) or re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", remediation_authorization_sha
    ) is None:
        raise OperatorError("exact canonical REMEDIATION authorization SHA is required")
    if executor not in ("codex", "antigravity", "antigravity-minimax"):
        raise OperatorError(f"unsupported executor: {executor}")
    if any(
        value is not None
        for value in (model, reasoning_effort, model_source, effort_source)
    ) and not is_profile_managed_executor(executor):
        raise OperatorError("model/effort options require a profile-managed Executor")

    _set_admission_boundary(
        admission, "REPOSITORY_ADMISSION", "REPOSITORY_ADMISSION_REJECTED"
    )
    with RepositoryLock(state.lock):
        actual_baseline = _git(root, "rev-parse", "HEAD")
        control_branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        admission["current_head_sha"] = actual_baseline
        if _git(root, "status", "--porcelain"):
            raise OperatorError("repository dirty")
        historical = (
            remote_mode
            and actual_baseline != resolved.execution_base_sha
        )
        if (
            not remote_mode
            and actual_baseline != canonical_remediation.reviewed_sha
        ):
            raise OperatorError("current HEAD does not match REMEDIATION reviewed_sha")

        subject_repo = root
        historical_workspace = None
        if historical:
            _set_admission_boundary(
                admission,
                "HISTORICAL_SUBJECT_ADMISSION",
                "HISTORICAL_SUBJECT_REJECTED",
            )
            historical_workspace = _create_historical_workspace(
                root, resolved.execution_base_sha
            )
            subject_repo = historical_workspace
            _require_control_checkout_unchanged(
                root, head_sha=actual_baseline, branch=control_branch
            )
        if attempt is not None:
            attempt.bind_subject(
                subject_repo,
                task,
                historical_workspace=historical_workspace,
            )

        _set_admission_boundary(
            admission, "RUN_RESERVATION", "RUN_NAMESPACE_CONFLICT"
        )
        remote_run_ids = _remote_run_reservations(
            root, task, admission=admission
        )
        run_id = next_run_id(task_id, state.runs, reserved=remote_run_ids)
        run = Run.from_task(
            run_id=run_id,
            task=task,
            executor=executor,
            base_sha=resolved.execution_base_sha,
            workspace=str(subject_repo),
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
        predecessor_record = {
            "source_run_id": resolved.source_run_id,
            "review_id": canonical_review.review_id,
            "finding_id": canonical_remediation.finding_id,
            "reviewed_sha": canonical_remediation.reviewed_sha,
        }
        execution_base_record = (
            dict(resolved.integrated_base)
            if resolved.integrated_base is not None
            else {
                "run_id": resolved.execution_base_run_id,
                "candidate_sha": resolved.execution_base_sha,
            }
        )
        run_path = state.runs / f"{run_id}.json"
        run_document = {
            "kind": "REMEDIATION",
            "remediation_authorization_sha": remediation_authorization_sha,
            "predecessor": predecessor_record,
            "execution": asdict(execution),
        }
        if resolved.cumulative:
            run_document["execution_base"] = execution_base_record
        _write_json(run_path, run_document)
        if attempt is not None:
            attempt.bind_run(run_path)
        execution_profile = _bind_and_persist_execution_profile(
            state=state,
            repo=root,
            run_id=run_id,
            executor=executor,
            model=model,
            reasoning_effort=reasoning_effort,
            model_source=model_source,
            effort_source=effort_source,
        )
        if correction_dispatch_id is not None:
            from .correction_dispatch import bind_correction_run

            bind_correction_run(
                state_root=state.root,
                repo_root=root,
                correction_dispatch_id=correction_dispatch_id,
                run_id=run_id,
                execution_profile=execution_profile,
            )
        observation_tracker.admit(run)
        observed_native_runner = observation_tracker.wrap_native_runner(
            native_runner
        )

        execution_policy = resolve_native_execution_policy(
            authorizes_mutation=canonical_remediation.action == "CODE_FIX"
        )
        dispatcher = remediation_dispatcher(
            selected_executor=executor,
            repo=subject_repo,
            handoff_path=state.handoffs / f"{run_id}.json",
            execution_policy=execution_policy,
            native_runner=observed_native_runner,
            execution_profile=execution_profile,
        )

        try:
            package = dispatcher.dispatch_remediation(execution=execution)
        except (
            CodexOutputError,
            AntigravityOutputError,
            AntigravityMinimaxOutputError,
            ArtifactValidationError,
        ) as exc:
            raise OperatorError(f"invalid structural ResultPackage: {exc}") from exc
        except CodexExecutionError as exc:
            raise OperatorError(f"Codex invocation failed: {exc}") from exc
        except AntigravityExecutionError as exc:
            raise OperatorError(str(exc)) from exc
        except AntigravityMinimaxExecutionError as exc:
            raise OperatorError(str(exc)) from exc
        except DispatcherError as exc:
            raise OperatorError(f"dispatcher failed: {exc}") from exc

        if historical:
            _require_control_checkout_unchanged(
                root, head_sha=actual_baseline, branch=control_branch
            )
            historical_candidate = _git(subject_repo, "rev-parse", "HEAD")
            if not _git_is_ancestor(subject_repo, run.base_sha, historical_candidate):
                raise OperatorError(
                    "historical remediation candidate does not descend from execution base"
                )
            if attempt is None:
                raise OperatorError("historical remediation attempt is unavailable")
            _prepare_historical_terminalization(root, attempt)

        runtime_completion = RuntimeCompletion(
            repo=subject_repo,
            state=state,
            task=task,
            run=run,
            run_path=run_path,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
            error_type=OperatorError,
            transport_repo=root,
        )
        if attempt is not None:
            attempt.bind_completion(runtime_completion)
        completion_policy = remediation_completion_policy(execution)
        if resolved.cumulative:
            completion_policy = replace(
                completion_policy, result_base_sha=run.base_sha
            )
        completion = runtime_completion.complete(package, completion_policy)
        if historical:
            _require_control_checkout_unchanged(
                root, head_sha=actual_baseline, branch=control_branch
            )

        return RemediationSummary(
            task_id=task_id,
            review_id=canonical_review.review_id,
            finding_id=canonical_remediation.finding_id,
            run_id=run_id,
            executor=executor,
            reviewed_sha=canonical_remediation.reviewed_sha,
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
    attempt = _RunAttempt()
    admission = _new_admission(
        "DIRECT_CANDIDATE",
        phase="TASK_ADMISSION",
        reason_code="TASK_CONTRACT_REJECTED",
        task_id=task_id,
        executor=executor,
        finding_id=finding_id,
    )
    try:
        return _accept_candidate_impl(
            task_id,
            finding_id=finding_id,
            executor=executor,
            repo=root,
            verification_runner=verification_runner,
            observation_tracker=observation_tracker,
            attempt=attempt,
            admission=admission,
        )
    except KeyboardInterrupt as original:
        if (
            attempt.run_path is not None
            and not (state.results / attempt.run_path.name).is_file()
        ):
            _persist_and_transport_failure(
                root,
                task_id=task_id,
                run_path=attempt.run_path,
                failure=original,
                observation_tracker=observation_tracker,
                interruption_phase=attempt.interruption_phase,
                verification_subject_sha=attempt.verification_subject_sha,
            )
        elif attempt.run_path is None:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
            )
        raise
    except Exception as original:
        if (
            attempt.run_path is not None
            and not (state.results / attempt.run_path.name).is_file()
        ):
            _persist_and_transport_failure(
                root,
                task_id=task_id,
                run_path=attempt.run_path,
                failure=original,
                observation_tracker=observation_tracker,
                interruption_phase=attempt.interruption_phase,
                verification_subject_sha=attempt.verification_subject_sha,
            )
        elif attempt.run_path is None:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
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
    attempt: _RunAttempt,
    admission: dict[str, Any],
) -> RemediationSummary:
    if executor not in ("codex", "antigravity", "antigravity-minimax"):
        raise OperatorError(f"unsupported executor: {executor}")
    task = load_task(repo, task_id)
    _bind_admission_task(admission, task)
    state = runtime_paths(repo)

    with RepositoryLock(state.lock):
        _set_admission_boundary(
            admission, "REPOSITORY_ADMISSION", "REPOSITORY_ADMISSION_REJECTED"
        )
        if _git(repo, "status", "--porcelain"):
            raise OperatorError("repository dirty")
        candidate_head = _git(repo, "rev-parse", "HEAD")
        admission["current_head_sha"] = candidate_head
        _set_admission_boundary(
            admission,
            "REMOTE_LINEAGE_RESOLUTION",
            "CANONICAL_LINEAGE_MISSING",
        )
        review, remediation, prior_result, prior_review = _resolve_direct_lineage(
            repo,
            task=task,
            finding_id=finding_id,
            admission=admission,
        )
        remediation_authorization_sha = admission.get("remediation_authorization_sha")
        if not isinstance(remediation_authorization_sha, str) or re.fullmatch(
            r"[0-9a-f]{40}|[0-9a-f]{64}", remediation_authorization_sha
        ) is None:
            raise OperatorError("exact canonical REMEDIATION authorization SHA is required")
        _set_admission_boundary(
            admission, "CANONICAL_CONTRACT_ADMISSION", "TASK_CONTRACT_REJECTED"
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
        execution_base_run_id = admission.get("source_run_id")
        execution_base_sha = remediation.reviewed_sha
        if not isinstance(execution_base_run_id, str):
            raise OperatorError("missing canonical source RUN identity")
        cumulative = False
        integrated_base = None
        lifecycle = resolve_remote_task_lifecycle(
            repo, task_id=task.task_id, task_revision=task.revision
        )
        has_explicit_cumulative_signal = bool(lifecycle.reviews) or any(
            _terminal_has_execution_base(t) for t in lifecycle.terminals
        )
        if has_explicit_cumulative_signal:
            try:
                from .unified_state import _resolve_cumulative_execution_base

                resolved_base = _resolve_cumulative_execution_base(
                    repo, task, lifecycle,
                    source_run_id=execution_base_run_id,
                    review_id=review.review_id,
                    finding_id=finding_id,
                    reviewed_sha=remediation.reviewed_sha,
                    semantic_review=review,
                )
                execution_base_run_id = resolved_base[0]
                execution_base_sha = resolved_base[1]
                integrated_base = getattr(resolved_base, "integrated_base", None)
                cumulative = True
            except (
                CorrectionFrontierError, OperatorError, ReviewTransportError,
                TypeError, ValueError,
            ) as exc:
                raise OperatorError(f"cumulative execution base rejected: {exc}") from exc
        if candidate_head == execution_base_sha:
            raise OperatorError("CODE_FIX candidate did not advance HEAD")
        if not _git_is_ancestor(repo, execution_base_sha, candidate_head):
            raise OperatorError("candidate HEAD does not descend from execution base")

        changed_files = _committed_changed_files(
            repo, execution_base_sha, candidate_head
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
            execution_base_run_id=(
                execution_base_run_id if cumulative else None
            ),
            execution_base_sha=(execution_base_sha if cumulative else None),
        )
        if existing_summary is not None:
            return existing_summary

        _set_admission_boundary(
            admission, "RUN_RESERVATION", "RUN_NAMESPACE_CONFLICT"
        )
        remote_run_ids = _remote_run_reservations(
            repo, task, admission=admission
        )
        run_id = next_run_id(task_id, state.runs, reserved=remote_run_ids)
        run = Run.from_task(
            run_id=run_id,
            task=task,
            executor=executor,
            base_sha=execution_base_sha,
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
        resolved_source_run_id = admission.get("source_run_id")
        if not resolved_source_run_id:
            raise OperatorError("missing canonical source RUN identity")
        predecessor_record = {
            "source_run_id": resolved_source_run_id,
            "review_id": review.review_id,
            "finding_id": finding_id,
            "reviewed_sha": remediation.reviewed_sha,
        }
        run_path = state.runs / f"{run_id}.json"
        run_document = {
            "kind": "REMEDIATION",
            "remediation_authorization_sha": remediation_authorization_sha,
            "acceptance": {
                "mode": "DIRECT_CANDIDATE",
                "candidate_head": candidate_head,
            },
            "predecessor": predecessor_record,
            "execution": asdict(execution),
        }
        if cumulative:
            run_document["execution_base"] = (
                dict(integrated_base)
                if integrated_base is not None
                else {
                    "run_id": execution_base_run_id,
                    "candidate_sha": execution_base_sha,
                }
            )
        _write_json(run_path, run_document)
        attempt.bind_run(run_path)
        observation_tracker.admit(run)

        structural_result = Result(
            head_sha=candidate_head,
            claims=(),
            changed_files=tuple(sorted(changed_files)),
            unresolved=(),
        )
        structural_package = ResultPackage(result=structural_result, evidence=())
        completion_policy = (
            replace(
                remediation_completion_policy(execution, direct_candidate=True),
                result_base_sha=execution_base_sha,
            )
            if cumulative
            else remediation_completion_policy(execution, direct_candidate=True)
        )
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
            structural_package,
            completion_policy,
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
    repo: Path,
    *,
    task: Task,
    finding_id: str,
    admission: dict[str, Any] | None = None,
) -> tuple[Review, Remediation, Result, Review | None]:
    return _resolve_remote_remediation_lineage(
        repo,
        task=task,
        finding_id=finding_id,
        context="direct candidate",
        admission=admission,
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

    resolved = _resolve_remote_remediation_lineage_impl(
        repo,
        task=task,
        finding_id=finding_id,
        context=context,
        admission=admission,
        bind_reviewed_task=False,
    )
    return resolved[:4]


def _resolve_remote_remediation_lineage_with_reviewed_task(
    repo: Path,
    *,
    task: Task,
    finding_id: str,
    source_run_id: str | None = None,
    required_remediation_sha: str | None = None,
    admission: dict[str, Any] | None = None,
) -> tuple[Review, Remediation, Result, Review | None, Task]:
    """Resolve remote FIX lineage against its immutable reviewed TASK."""

    return _resolve_remote_remediation_lineage_impl(
        repo,
        task=task,
        finding_id=finding_id,
        source_run_id=source_run_id,
        required_remediation_sha=required_remediation_sha,
        context="remote remediation",
        admission=admission,
        bind_reviewed_task=True,
    )


def _resolve_remote_remediation_lineage_impl(
    repo: Path,
    *,
    task: Task,
    finding_id: str,
    source_run_id: str | None = None,
    required_remediation_sha: str | None = None,
    context: str,
    admission: dict[str, Any] | None,
    bind_reviewed_task: bool,
) -> tuple[Review, Remediation, Result, Review | None, Task]:
    try:
        remote_lineages = resolve_remote_remediation_lineages(
            repo,
            finding_id=finding_id,
            task_id=task.task_id,
            task_revision=task.revision,
            source_run_id=source_run_id,
        )
    except ReviewTransportError as exc:
        if admission is not None:
            observed_refs = getattr(exc, "observed_refs", ())
            if isinstance(observed_refs, tuple):
                _record_observed_refs(admission, observed_refs)
                if len(observed_refs) == 1:
                    admission["observed_ref"], admission["observed_sha"] = (
                        observed_refs[0]
                    )
        raise OperatorError(f"{context} lineage resolution failed: {exc}") from exc
    if admission is not None and remote_lineages:
        _record_observed_refs(
            admission,
            tuple((remote.ref, remote.commit_sha) for remote in remote_lineages),
        )

    matches: list[
        tuple[str, tuple[Review, Remediation, Result, Review | None, Task], str]
    ] = []
    for remote in remote_lineages:
        if admission is not None:
            admission["observed_ref"] = remote.ref
            admission["observed_sha"] = remote.commit_sha
        if (
            required_remediation_sha is not None
            and remote.commit_sha != required_remediation_sha
        ):
            raise OperatorError("approved remediation SHA is no longer current")
        parsed = _parse_remote_direct_lineage_impl(
            repo,
            task=task,
            remote=remote,
            admission=admission,
            bind_reviewed_task=bind_reviewed_task,
        )
        if parsed is not None:
            if parsed[1].finding_id != finding_id:
                raise OperatorError(
                    f"contract-invalid canonical lineage at {remote.ref}: "
                    "REMEDIATION finding does not match requested finding"
                )
            matches.append((remote.source_run_id, parsed, remote.commit_sha))
    if not matches:
        if admission is not None:
            _set_admission_boundary(
                admission,
                "REMOTE_LINEAGE_RESOLUTION",
                "CANONICAL_LINEAGE_MISSING",
            )
        raise OperatorError(f"canonical {context} lineage not found")
    if len(matches) != 1:
        if admission is not None:
            _set_admission_boundary(
                admission,
                "REMOTE_LINEAGE_RESOLUTION",
                "CANONICAL_LINEAGE_AMBIGUOUS",
            )
        raise OperatorError(f"canonical {context} lineage is ambiguous")
    if admission is not None:
        admission["source_run_id"] = matches[0][0]
        admission["remediation_authorization_sha"] = matches[0][2]
    return matches[0][1]


def _parse_remote_direct_lineage(
    repo: Path,
    *,
    task: Task,
    remote: RemoteRemediationLineage,
    admission: dict[str, Any] | None = None,
) -> tuple[Review, Remediation, Result, Review | None] | None:
    """Parse the stable four-value remote remediation lineage contract."""

    parsed = _parse_remote_direct_lineage_impl(
        repo,
        task=task,
        remote=remote,
        admission=admission,
        bind_reviewed_task=False,
    )
    return None if parsed is None else parsed[:4]


def _parse_remote_direct_lineage_impl(
    repo: Path,
    *,
    task: Task,
    remote: RemoteRemediationLineage,
    admission: dict[str, Any] | None = None,
    bind_reviewed_task: bool,
) -> tuple[Review, Remediation, Result, Review | None, Task] | None:
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
            return None

        if admission is not None:
            _set_admission_boundary(
                admission,
                "CANONICAL_CONTRACT_ADMISSION",
                "CANONICAL_LINEAGE_INVALID",
            )
        review = parse_review(remote.review.decode("utf-8", errors="strict"))
        if admission is not None:
            admission.update(
                {
                    "review_id": review.review_id,
                    "reviewed_sha": review.reviewed_sha,
                }
            )
        lineage_task = task
        if bind_reviewed_task:
            try:
                lineage_task = parse_task(
                    read_remote_task(
                        repo,
                        commit_sha=review.reviewed_sha,
                        task_id=task.task_id,
                    ).decode("utf-8", errors="strict")
                )
            except (ReviewTransportError, TaskValidationError, UnicodeError) as exc:
                raise ValueError(f"historical TASK rejected: {exc}") from exc
            if (
                lineage_task.task_id != task.task_id
                or lineage_task.revision != task.revision
                or lineage_task.task_id != source_run.task.id
                or lineage_task.revision != source_run.task.revision
            ):
                raise ValueError("historical TASK identity or revision mismatch")
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
        repaired_source = _validated_repair_remediation_source(
            repo, task=lineage_task, run_data=run_data, run=source_run,
            package=package, review=review, repair=remote.repair,
        )
        if prior_execution is None:
            validate_result_package(
                task=lineage_task,
                run=source_run,
                result=result,
                evidence=evidence,
            )
        else:
            _validate_persisted_remediation_result(
                repo=repo,
                task=lineage_task,
                execution=prior_execution,
                package=package,
                run_document=run_data,
            )
        if result.head_sha != review.reviewed_sha:
            raise ValueError("REVIEW does not bind to authoritative source RESULT")
        if remediation.finding_id not in {item.id for item in review.findings}:
            raise ValueError("REMEDIATION finding is absent from REVIEW")
        prior_review = None if repaired_source is None else repaired_source[1]
        if review.prior_finding_id is not None and repaired_source is None:
            if prior_execution is None:
                raise ValueError("DELTA REVIEW source has no validated correction lineage")
            if review.prior_finding_id != prior_execution.finding.id:
                raise ValueError("DELTA REVIEW prior finding lineage mismatch")
            prior_review = Review(
                review_id=prior_execution.review_id,
                reviewed_sha=prior_execution.remediation.reviewed_sha,
                mode="PRIMARY", verdict="CHANGES_REQUIRED", acceptance={},
                findings=(prior_execution.finding,),
            )
        return review, remediation, result, prior_review, lineage_task
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
    execution_base_run_id: str | None = None,
    execution_base_sha: str | None = None,
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
        predecessor_data = data.get("predecessor")
        if predecessor_data is not None:
            try:
                pred = _parse_remediation_predecessor(predecessor_data)
                if (
                    pred.review_id != review.review_id
                    or pred.finding_id != finding_id
                    or pred.reviewed_sha != execution.remediation.reviewed_sha
                ):
                    continue
            except (TypeError, ValueError):
                continue
        if execution_base_run_id is not None or execution_base_sha is not None:
            try:
                execution_base = _parse_remediation_execution_base(
                    data.get("execution_base")
                )
            except (TypeError, ValueError):
                continue
            if (
                execution_base.run_id != execution_base_run_id
                or execution_base.candidate_sha != execution_base_sha
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
                run_document=data,
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
        execution.run.base_sha,
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
        if actual_head != execution.run.base_sha or actual_changed:
            raise OperatorError("EVIDENCE_ONLY remediation changed repository HEAD")
    else:
        if actual_head == execution.run.base_sha:
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


def cmd_performance(
    task_ids: list[str], *, repo: str | Path | None = None
) -> str:
    """Render the read-only AIOS_PERFORMANCE_OBSERVATION v1 surface."""

    return observe_performance(task_ids, repo=repo).render()


def run_approved_remediation_intent(
    correction_dispatch_id: str,
    source_run_id: str,
    finding_id: str,
    *,
    executor: str,
    approver: str,
    repo: str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
    model: str | None = None,
    reasoning_effort: str | None = None,
    model_source: str | None = None,
    effort_source: str | None = None,
) -> tuple[Any, Any]:
    """Record/replay A3, then enter the existing durable A6 boundary."""

    from .correction_dispatch import (
        CorrectionDispatchError,
        CorrectionInvocation,
        execute_correction_dispatch,
        existing_correction_profile,
        reject_existing_selector_collision,
    )
    from .remote_surface import (
        RemoteSurfaceError,
        record_remote_approval,
        require_current_approval,
    )

    try:
        repo_root = resolve_repository(repo)
        state_root = runtime_state_root(repo_root)
        exists, remote_profile = existing_correction_profile(
            state_root=state_root,
            repo_root=repo_root,
            correction_dispatch_id=correction_dispatch_id,
        )
        remote_profile = _authorization_profile(
            repo=repo_root,
            authorization_id=correction_dispatch_id,
            executor=executor,
            existing=exists,
            bound_profile=remote_profile,
            model=model,
            reasoning_effort=reasoning_effort,
            model_source=model_source,
            effort_source=effort_source,
        )
        reject_existing_selector_collision(
            state_root=state_root,
            repo_root=repo_root,
            correction_dispatch_id=correction_dispatch_id,
            source_run_id=source_run_id,
            finding_id=finding_id,
            executor=executor,
            execution_profile=remote_profile,
        )

        # An existing A6 identity must still resolve through its old exact A3
        # authority before this delivery may record anything. This prevents a
        # stable correction id from being rebound after a remediation ref moves.
        correction_key = hashlib.sha256(
            correction_dispatch_id.encode("ascii")
        ).hexdigest()
        correction_record = (
            state_root / "correction-dispatches" / f"{correction_key}.json"
        )
        if correction_record.is_file():
            require_current_approval(
                repo=repo_root,
                state_root=state_root,
                source_run_id=source_run_id,
                finding_id=finding_id,
            )

        approval_summary = record_remote_approval(
            repo=repo_root,
            state_root=state_root,
            source_run_id=source_run_id,
            finding_id=finding_id,
            approver=approver,
        )
        approval = require_current_approval(
            repo=repo_root,
            state_root=state_root,
            source_run_id=source_run_id,
            finding_id=finding_id,
        )

        def invoke_remediation() -> CorrectionInvocation:
            try:
                summary = run_remediation(
                    approval.task_id,
                    finding_id=finding_id,
                    source_run_id=source_run_id,
                    approved_remediation_sha=approval.remediation_sha,
                    correction_dispatch_id=correction_dispatch_id,
                    executor=executor,
                    model=remote_profile.model if remote_profile is not None else None,
                    reasoning_effort=(
                        remote_profile.reasoning_effort
                        if remote_profile is not None else None
                    ),
                    model_source=(
                        remote_profile.model_source if remote_profile is not None else None
                    ),
                    effort_source=(
                        remote_profile.effort_source if remote_profile is not None else None
                    ),
                    repo=repo_root,
                    native_runner=native_runner,
                    verification_runner=verification_runner,
                    monotonic_clock=monotonic_clock,
                )
                return CorrectionInvocation(0, summary.run_id)
            except OperatorError as exc:
                print(f"AIOS ERROR: {exc}", file=sys.stderr)
                return CorrectionInvocation(1)

        dispatch_outcome = execute_correction_dispatch(
            state_root=state_root,
            repo_root=repo_root,
            correction_dispatch_id=correction_dispatch_id,
            source_run_id=source_run_id,
            finding_id=finding_id,
            executor=executor,
            approval=approval,
            invoke_remediation=invoke_remediation,
            execution_profile=remote_profile,
        )
    except (RemoteSurfaceError, CorrectionDispatchError, ExecutionProfileError) as exc:
        raise OperatorError(str(exc)) from exc
    return approval_summary, dispatch_outcome


def run_repair_wakeup(
    repair_dispatch_id: str,
    failed_run_id: str,
    repair_sha: str,
    *,
    executor: str | None,
    repo: str | Path | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
    model: str | None = None,
    reasoning_effort: str | None = None,
    model_source: str | None = None,
    effort_source: str | None = None,
) -> Any:
    """Deliver one immutable REPAIR intent through the durable outer boundary."""

    from .repair_dispatch import (
        RepairDispatchError,
        RepairInvocation,
        execute_repair_dispatch,
        existing_repair_profile,
        reject_existing_selector_collision,
        replay_existing_repair_dispatch,
    )

    try:
        root = resolve_repository(repo)
        state_root = runtime_state_root(root)
        source_repair_policy = _SOURCE_REPAIR_POLICY.get()
        exists, remote_profile = existing_repair_profile(
            state_root=state_root,
            repo_root=root,
            repair_dispatch_id=repair_dispatch_id,
            source_repair_policy=source_repair_policy,
        )
        remote_profile = _authorization_profile(
            repo=root,
            authorization_id=repair_dispatch_id,
            executor=executor,
            existing=exists,
            bound_profile=remote_profile,
            model=model,
            reasoning_effort=reasoning_effort,
            model_source=model_source,
            effort_source=effort_source,
        )
        reject_existing_selector_collision(
            state_root=state_root,
            repo_root=root,
            repair_dispatch_id=repair_dispatch_id,
            failed_run_id=failed_run_id,
            repair_sha=repair_sha,
            executor=executor,
            execution_profile=remote_profile,
            source_repair_policy=source_repair_policy,
        )
        replay = replay_existing_repair_dispatch(
            state_root=state_root,
            repo_root=root,
            repair_dispatch_id=repair_dispatch_id,
            failed_run_id=failed_run_id,
            repair_sha=repair_sha,
            executor=executor,
            execution_profile=remote_profile,
            source_repair_policy=source_repair_policy,
        )
        if replay is not None:
            return replay

        # This is observation only. It discovers no authority: the exact current
        # canonical REPAIR and Unified State must already authorize continuation.
        preflight = preflight_repair(
            failed_run_id, repo=root, required_repair_sha=repair_sha
        )
        if preflight.status != "READY" or preflight.task_id is None:
            _project_blocked_correction_delivery(
                family="REPAIR",
                delivery_id=repair_dispatch_id,
                preflight=preflight.as_dict(),
                selectors={
                    "failed_run_id": failed_run_id,
                    "repair_sha": repair_sha,
                    "executor": executor,
                },
            )
            raise OperatorError("canonical REPAIR preflight does not authorize execution")
        observation = observe_unified_state(preflight.task_id, repo=root)
        if (
            observation.next_action != "EXECUTE_REPAIR"
            or observation.failed_run_id != failed_run_id
            or observation.correction_sha != repair_sha
            or observation.correction_document is None
        ):
            raise OperatorError(
                "Unified State does not authorize the requested exact REPAIR"
            )
        action = observation.correction_document.get("action")
        if action not in (
            "CODE_FIX",
            "CONTINUE_IMPLEMENTATION",
            "FINALIZE_CANDIDATE",
            "NO_CHANGE",
        ):
            raise OperatorError("canonical REPAIR action is invalid")
        correction = observation.correction
        executor_required = (
            correction.get("executor_required")
            if isinstance(correction, Mapping)
            else None
        )
        if action in (
            "CODE_FIX", "CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE"
        ):
            if executor is None:
                raise OperatorError("coding REPAIR requires an explicit Executor")
            if executor_required is not True:
                raise OperatorError("canonical coding REPAIR authority is inconsistent")
        else:
            if executor is not None:
                raise OperatorError("NO_CHANGE REPAIR forbids a coding Executor")
            if executor_required is not False:
                raise OperatorError("NO_CHANGE REPAIR lacks reusable verification state")

        def invoke_repair() -> RepairInvocation:
            try:
                summary = run_repair(
                    failed_run_id,
                    executor=executor,
                    repo=root,
                    repair=observation.correction_document,
                    required_repair_sha=repair_sha,
                    repair_dispatch_id=repair_dispatch_id,
                    model=remote_profile.model if remote_profile is not None else None,
                    reasoning_effort=(
                        remote_profile.reasoning_effort
                        if remote_profile is not None else None
                    ),
                    model_source=(
                        remote_profile.model_source if remote_profile is not None else None
                    ),
                    effort_source=(
                        remote_profile.effort_source if remote_profile is not None else None
                    ),
                    native_runner=native_runner,
                    verification_runner=verification_runner,
                    monotonic_clock=monotonic_clock,
                )
                return RepairInvocation(0, summary.run_id)
            except (OperatorError, RepairDispatchError) as exc:
                print(f"AIOS ERROR: {exc}", file=sys.stderr)
                return RepairInvocation(1)

        return execute_repair_dispatch(
            state_root=state_root,
            repo_root=root,
            repair_dispatch_id=repair_dispatch_id,
            failed_run_id=failed_run_id,
            repair_sha=repair_sha,
            executor=executor,
            task_id=observation.task_id,
            action=action,
            invoke_repair=invoke_repair,
            execution_profile=remote_profile,
            source_repair_policy=source_repair_policy,
        )
    except (RepairDispatchError, ExecutionProfileError) as exc:
        raise OperatorError(str(exc)) from exc


def integrate_correction(
    task_id: str,
    *,
    task_revision: int,
    cumulative_tip_run_id: str,
    cumulative_tip_candidate_sha: str,
    authorized_main_sha: str | None = None,
    current_main_sha: str | None = None,
    expected_main_sha: str | None = None,
    repo: str | Path | None = None,
) -> CorrectionIntegrationResult:
    """Authorize and materialize one deterministic correction integration candidate."""
    root = resolve_repository(repo)
    try:
        return _integrate_correction_impl(
            task_id,
            task_revision=task_revision,
            cumulative_tip_run_id=cumulative_tip_run_id,
            cumulative_tip_candidate_sha=cumulative_tip_candidate_sha,
            authorized_main_sha=authorized_main_sha,
            current_main_sha=current_main_sha,
            expected_main_sha=expected_main_sha,
            repo=root,
        )
    except CorrectionIntegrationError as exc:
        raise OperatorError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aios")
    commands = parser.add_subparsers(dest="command", required=True)

    reconcile_parser = commands.add_parser(
        "reconcile-control-main", help="Human reconciliation of an exact transported failed candidate"
    )
    reconcile_parser.add_argument("failed_run_id")
    reconcile_parser.add_argument("--expected-failed-head", required=True)
    reconcile_parser.add_argument("--expected-canonical-main", required=True)
    reconcile_parser.add_argument("--repo")

    task_parser = commands.add_parser("task", help="Show a stored canonical TASK")
    task_parser.add_argument("task_id")
    task_parser.add_argument("--repo")

    state_parser = commands.add_parser(
        "state", help="Derive Unified State + Next Action without mutation"
    )
    state_parser.add_argument("task_id")
    state_parser.add_argument("--repo")

    continue_parser = commands.add_parser(
        "continue", help="Delegate the exact Unified State next action once"
    )
    continue_parser.add_argument("task_id")
    continue_parser.add_argument(
        "--executor", choices=("codex", "antigravity", "antigravity-minimax")
    )
    continue_parser.add_argument("--model")
    continue_parser.add_argument("--reasoning-effort")
    continue_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the AIOS_HUMAN_SURFACE v1 machine result",
    )
    continue_parser.add_argument("--repo")

    run_parser = commands.add_parser("run", help="Execute a stored canonical TASK")
    run_parser.add_argument("task_id")
    run_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity", "antigravity-minimax")
    )
    run_parser.add_argument("--model")
    run_parser.add_argument("--reasoning-effort")
    run_parser.add_argument("--repo")
    migration_parser = commands.add_parser(
        "migrate-primary", help="Handoff one exact cross-generation PRIMARY migration"
    )
    migration_parser.add_argument("intent", help="Exact reviewed migration intent JSON")
    migration_parser.add_argument("--accept-handoff", help=argparse.SUPPRESS)
    bootstrap_parser = commands.add_parser(
        "bootstrap-primary", help="Bridge one exact legacy installed generation into migrate-primary"
    )
    bootstrap_parser.add_argument("intent", help="Exact reviewed migration intent JSON")
    source_bootstrap_parser = commands.add_parser(
        "bootstrap-source-primary",
        help="Bridge one legacy generation through an exact source-control handoff",
    )
    source_bootstrap_parser.add_argument("intent", help="Exact source-control bootstrap intent JSON")
    source_bootstrap_parser.add_argument("--accept-handoff", help=argparse.SUPPRESS)
    source_upgrade_parser = commands.add_parser(
        "bootstrap-source-upgrade-primary",
        help="Stage the exact activated migration-capable source-control upgrade",
    )
    source_upgrade_parser.add_argument("intent", help="Exact source-control bootstrap intent JSON")
    recovery_parser = commands.add_parser(
        "recover-source-bootstrap", help="Supersede one consumed pre-RUN source bootstrap edge"
    )
    recovery_parser.add_argument("old_intent", help="Exact bound old v2 intent JSON")
    recovery_parser.add_argument("replacement_intent", help="Exact authorized replacement v2 intent JSON")
    source_repair_parser = commands.add_parser(
        "bootstrap-source-repair", help="Transport one completed failed source bootstrap into REPAIR"
    )
    source_repair_parser.add_argument("intent", help="Exact source-REPAIR bootstrap intent JSON")
    source_repair_parser.add_argument("--accept-transport", help=argparse.SUPPRESS)
    successor_parser = commands.add_parser(
        "bootstrap-source-successor-primary",
        help="Transport one completed failed source bootstrap into a newer TASK PRIMARY",
    )
    successor_parser.add_argument("intent", help="Exact post-terminal successor intent JSON")
    successor_parser.add_argument("--accept-transport", help=argparse.SUPPRESS)
    wakeup_parser = commands.add_parser(
        "wakeup", help="Idempotently wake one canonical PRIMARY execution"
    )
    wakeup_parser.add_argument("dispatch_id")
    wakeup_parser.add_argument("task_id")
    wakeup_parser.add_argument("--task-revision", type=int)
    wakeup_parser.add_argument("--task-blob-sha")
    wakeup_parser.add_argument("--task-commit-sha")
    wakeup_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity", "antigravity-minimax")
    )
    wakeup_parser.add_argument("--model")
    wakeup_parser.add_argument("--reasoning-effort")
    wakeup_parser.add_argument(
        "--model-source",
        choices=("EXPLICIT", "REPOSITORY_DEFAULT"),
    )
    wakeup_parser.add_argument(
        "--effort-source",
        choices=("EXPLICIT", "REPOSITORY_DEFAULT"),
    )
    wakeup_parser.add_argument("--repo")
    status_parser = commands.add_parser(
        "remote-status", help="Inspect one existing dispatch without mutation"
    )
    status_parser.add_argument("dispatch_id")
    status_parser.add_argument("--repo")
    approval_parser = commands.add_parser(
        "remote-approve", help="Record exact Human remediation approval"
    )
    approval_parser.add_argument("source_run_id")
    approval_parser.add_argument("finding_id")
    approval_parser.add_argument("--approver", required=True)
    approval_parser.add_argument("--repo")
    correction_parser = commands.add_parser(
        "approved-remediation-wakeup",
        help="Idempotently wake one exactly approved REMEDIATION",
    )
    correction_parser.add_argument("correction_dispatch_id")
    correction_parser.add_argument("source_run_id")
    correction_parser.add_argument("finding_id")
    correction_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity", "antigravity-minimax")
    )
    correction_parser.add_argument("--model")
    correction_parser.add_argument("--reasoning-effort")
    correction_parser.add_argument(
        "--model-source",
        choices=("EXPLICIT", "REPOSITORY_DEFAULT"),
    )
    correction_parser.add_argument(
        "--effort-source",
        choices=("EXPLICIT", "REPOSITORY_DEFAULT"),
    )
    correction_parser.add_argument("--repo")
    intent_parser = commands.add_parser(
        "approved-remediation-intent",
        help="Record exact Human approval and wake that REMEDIATION once",
    )
    intent_parser.add_argument("correction_dispatch_id")
    intent_parser.add_argument("source_run_id")
    intent_parser.add_argument("finding_id")
    intent_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity")
    )
    intent_parser.add_argument("--model")
    intent_parser.add_argument("--reasoning-effort")
    intent_parser.add_argument(
        "--model-source",
        choices=("EXPLICIT", "REPOSITORY_DEFAULT"),
    )
    intent_parser.add_argument(
        "--effort-source",
        choices=("EXPLICIT", "REPOSITORY_DEFAULT"),
    )
    intent_parser.add_argument("--approver", required=True)
    intent_parser.add_argument("--repo")
    remediation_parser = commands.add_parser(
        "remediate", help="Execute one canonical narrow REMEDIATION"
    )
    remediation_parser.add_argument("task_id")
    remediation_parser.add_argument("--finding")
    remediation_parser.add_argument("--review")
    remediation_parser.add_argument("--remediation")
    remediation_parser.add_argument("--prior-review")
    remediation_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity", "antigravity-minimax")
    )
    remediation_parser.add_argument("--model")
    remediation_parser.add_argument("--reasoning-effort")
    remediation_parser.add_argument("--repo")
    remediation_preflight_parser = commands.add_parser(
        "preflight-remediation",
        help="Inspect exact REMEDIATION readiness without execution",
    )
    remediation_preflight_parser.add_argument("task_id")
    remediation_preflight_parser.add_argument("--finding", required=True)
    remediation_preflight_parser.add_argument("--source-run")
    remediation_preflight_parser.add_argument("--approved-remediation-sha")
    remediation_preflight_parser.add_argument("--repo")
    candidate_parser = commands.add_parser(
        "accept-candidate",
        help="Accept one already committed CODE_FIX candidate",
    )
    candidate_parser.add_argument("task_id")
    candidate_parser.add_argument("--finding", required=True)
    candidate_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity", "antigravity-minimax")
    )
    candidate_parser.add_argument("--repo")
    repair_parser = commands.add_parser(
        "repair", help="Execute one GitHub-authored pre-PASS REPAIR"
    )
    repair_parser.add_argument("failed_run_id")
    repair_parser.add_argument("--repair")
    repair_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity", "antigravity-minimax")
    )
    repair_parser.add_argument("--model")
    repair_parser.add_argument("--reasoning-effort")
    repair_parser.add_argument("--repo")
    repair_wakeup_parser = commands.add_parser(
        "repair-wakeup",
        help="Idempotently deliver one exact canonical REPAIR authorization",
    )
    repair_wakeup_parser.add_argument("repair_dispatch_id")
    repair_wakeup_parser.add_argument("failed_run_id")
    repair_wakeup_parser.add_argument("repair_sha")
    repair_wakeup_parser.add_argument(
        "--executor", choices=("codex", "antigravity")
    )
    repair_wakeup_parser.add_argument("--model")
    repair_wakeup_parser.add_argument("--reasoning-effort")
    repair_wakeup_parser.add_argument(
        "--model-source", choices=("EXPLICIT", "REPOSITORY_DEFAULT")
    )
    repair_wakeup_parser.add_argument(
        "--effort-source", choices=("EXPLICIT", "REPOSITORY_DEFAULT")
    )
    repair_wakeup_parser.add_argument("--repo")
    repair_preflight_parser = commands.add_parser(
        "preflight-repair",
        help="Inspect exact REPAIR readiness without execution",
    )
    repair_preflight_parser.add_argument("failed_run_id")
    repair_preflight_parser.add_argument("--repair")
    repair_preflight_parser.add_argument("--repo")
    recovery_parser = commands.add_parser(
        "recover-primary",
        help="Recover one exact conflicting PRIMARY terminal RUN",
    )
    recovery_parser.add_argument("run_id")
    recovery_parser.add_argument("--repo")
    transport_parser = commands.add_parser(
        "transport", help="Retry transport of one persisted terminal RUN"
    )
    transport_parser.add_argument("run_id")
    transport_parser.add_argument("--repo")
    ingress_parser = commands.add_parser(
        "ingress",
        aliases=["ingest"],
        help="Ingest one Brain/Reviewer-authored control-plane artifact envelope",
    )
    ingress_parser.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Path to envelope file (or '-' for stdin)",
    )
    ingress_parser.add_argument("--file", help="Path to envelope file")
    ingress_parser.add_argument(
        "--stdin", action="store_true", help="Read envelope from stdin"
    )
    ingress_parser.add_argument("--repo", help="Target Git repository path")
    performance_parser = commands.add_parser(
        "performance",
        help="Aggregate canonical RUN observations for selected TASKs",
    )
    performance_parser.add_argument(
        "task_id",
        nargs="+",
        help="One or more exact bare TASK identities",
    )
    performance_parser.add_argument("--repo", help="Target Git repository path")

    integration_parser = commands.add_parser(
        "integrate-correction",
        aliases=["integrate", "correction-integration"],
        help="Materialize one authorized deterministic correction integration candidate",
    )
    integration_parser.add_argument("task_id")
    integration_parser.add_argument("--task-revision", type=int, required=True)
    integration_parser.add_argument("--cumulative-tip-run", required=True)
    integration_parser.add_argument("--cumulative-tip-candidate", required=True)
    integration_parser.add_argument(
        "--authorized-main",
        "--current-main",
        dest="authorized_main",
        required=True,
        help="Authorized current main commit SHA",
    )
    integration_parser.add_argument("--repo")
    integration_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON machine output",
    )
    return parser


def _project_operational_delivery(
    root: Path,
    *,
    family: str,
    delivery_id: str,
    selectors: Mapping[str, Any],
) -> None:
    """Best-effort workflow projection; never changes command authority/outcome."""

    try:
        from .operational_receipt import (
            project_delivery_receipt,
            write_environment_receipt,
        )

        receipt = project_delivery_receipt(
            runtime_state_root(root),
            family=family,
            delivery_id=delivery_id,
            selectors=selectors,
        )
        write_environment_receipt(receipt)
    except Exception:
        return


def _project_blocked_correction_delivery(
    *,
    family: str,
    delivery_id: str,
    preflight: Mapping[str, Any],
    selectors: Mapping[str, Any],
) -> None:
    """Best-effort projection of the exact preflight returned by this invocation."""

    try:
        from .operational_receipt import (
            correction_preflight_receipt,
            write_environment_receipt,
        )

        write_environment_receipt(
            correction_preflight_receipt(
                family,
                delivery_id,
                preflight,
                selectors=selectors,
            )
        )
    except Exception:
        return


def main(
    argv: list[str] | None = None,
    *,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "reconcile-control-main":
        prior_head = None
        try:
            prior_head = _git(resolve_repository(args.repo), "rev-parse", "HEAD")
            summary = reconcile_control_main(
                args.failed_run_id, expected_failed_head=args.expected_failed_head,
                expected_canonical_main=args.expected_canonical_main, repo=args.repo,
            )
        except (OperatorError, OSError):
            print(json.dumps({"failed_run_id": args.failed_run_id[:128],
                              "prior_head": prior_head, "restored_head": None,
                              "status": "FAILURE"}, sort_keys=True))
            return 1
        print(json.dumps(summary, sort_keys=True))
        return 0
    try:
        if args.command == "task":
            print(describe_task(args.task_id, repo=args.repo).render())
        elif args.command == "state":
            print(observe_unified_state(args.task_id, repo=args.repo).render())
        elif args.command == "continue":
            outcome, exit_code = continue_task(
                args.task_id,
                executor=args.executor,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                repo=args.repo,
                argv=argv,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
            if outcome is not None:
                # Preserve the established explicit-repository machine surface;
                # the cwd-oriented interactive surface keeps its concise view.
                print(
                    outcome.render()
                    if args.json or args.repo is not None
                    else outcome.render_human()
                )
            return exit_code
        elif args.command == "run":
            repo_root = resolve_repository(args.repo)
            preflight = _preflight_primary_admission(
                repo_root,
                task_id=args.task_id,
                executor=args.executor,
                argv=argv,
                runner=native_runner,
            )
            if preflight.restart_code is not None:
                return preflight.restart_code
            summary = run_task(
                args.task_id,
                executor=args.executor,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                repo=repo_root,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
                synchronize=False,
                preflight_sha=preflight.preflight_sha,
            )
            print(summary.render())
        elif args.command == "migrate-primary":
            return migrate_primary(
                args.intent, runner=native_runner, handoff_path=args.accept_handoff
            )
        elif args.command == "bootstrap-primary":
            return bootstrap_primary(args.intent, runner=native_runner)
        elif args.command == "bootstrap-source-primary":
            return bootstrap_source_primary(
                args.intent, runner=native_runner, handoff_path=args.accept_handoff
            )
        elif args.command == "bootstrap-source-upgrade-primary":
            return bootstrap_source_upgrade_primary(args.intent, runner=native_runner)
        elif args.command == "recover-source-bootstrap":
            return recover_source_bootstrap(args.old_intent, args.replacement_intent)
        elif args.command == "bootstrap-source-repair":
            return bootstrap_source_repair(
                args.intent, runner=native_runner, transport_path=args.accept_transport
            )
        elif args.command == "bootstrap-source-successor-primary":
            return bootstrap_source_successor_primary(
                args.intent, runner=native_runner, transport_path=args.accept_transport
            )
        elif args.command == "wakeup":
            repo_root = resolve_repository(args.repo)
            dispatch_state_root = runtime_paths(repo_root).root
            exists, remote_profile = existing_dispatch_profile(
                repo=repo_root,
                state_root=dispatch_state_root,
                dispatch_id=args.dispatch_id,
            )
            remote_profile = _authorization_profile(
                repo=repo_root,
                authorization_id=args.dispatch_id,
                executor=args.executor,
                existing=exists,
                bound_profile=remote_profile,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                model_source=args.model_source,
                effort_source=args.effort_source,
            )
            wakeup_argv = [
                "wakeup",
                args.dispatch_id,
                args.task_id,
            ]
            if args.task_revision is not None:
                wakeup_argv.extend(["--task-revision", str(args.task_revision)])
            if args.task_blob_sha is not None:
                wakeup_argv.extend(["--task-blob-sha", args.task_blob_sha])
            if args.task_commit_sha is not None:
                wakeup_argv.extend(["--task-commit-sha", args.task_commit_sha])
            wakeup_argv.extend(["--executor", args.executor])
            if remote_profile is not None:
                wakeup_argv.extend([
                    "--model", remote_profile.model,
                    "--reasoning-effort", remote_profile.reasoning_effort,
                    "--model-source", remote_profile.model_source,
                    "--effort-source", remote_profile.effort_source,
                ])
            wakeup_argv.extend(["--repo", str(repo_root)])

            def invoke_primary() -> DispatchInvocation:
                try:
                    preflight = _preflight_primary_admission(
                        repo_root,
                        task_id=args.task_id,
                        executor=args.executor,
                        dispatch_id=args.dispatch_id,
                        argv=wakeup_argv,
                        runner=native_runner,
                    )
                    if preflight.restart_code is not None:
                        return DispatchInvocation(preflight.restart_code)
                    summary = run_task(
                        args.task_id,
                        executor=args.executor,
                        repo=repo_root,
                        native_runner=native_runner,
                        verification_runner=verification_runner,
                        monotonic_clock=monotonic_clock,
                        synchronize=False,
                        preflight_sha=preflight.preflight_sha,
                        dispatch_id=args.dispatch_id,
                        task_revision=args.task_revision,
                        task_blob_sha=args.task_blob_sha,
                        task_commit_sha=args.task_commit_sha,
                        model=remote_profile.model if remote_profile is not None else None,
                        reasoning_effort=(
                            remote_profile.reasoning_effort
                            if remote_profile is not None else None
                        ),
                        model_source=(
                            remote_profile.model_source
                            if remote_profile is not None else None
                        ),
                        effort_source=(
                            remote_profile.effort_source
                            if remote_profile is not None else None
                        ),
                    )
                    return DispatchInvocation(0, summary.run_id)
                except OperatorError as exc:
                    print(f"AIOS ERROR: {exc}", file=sys.stderr)
                    return DispatchInvocation(1)

            if os.environ.get("AIOS_RESTART_ATTEMPTED") == "1":
                # The original wakeup still owns its durable invocation guard while
                # it waits for this synchronized child. Continue that invocation
                # directly so admission binds the RUN to the same dispatch record;
                # the parent will perform the single dispatch finalization.
                return invoke_primary().exit_code

            try:
                outcome = execute_dispatch(
                    repo=repo_root,
                    state_root=dispatch_state_root,
                    dispatch_id=args.dispatch_id,
                    task_id=args.task_id,
                    executor=args.executor,
                    invoke_primary=invoke_primary,
                    task_revision=args.task_revision,
                    task_blob_sha=args.task_blob_sha,
                    task_commit_sha=args.task_commit_sha,
                    execution_profile=remote_profile,
                )
            finally:
                _project_operational_delivery(
                    repo_root,
                    family="PRIMARY",
                    delivery_id=args.dispatch_id,
                    selectors={
                        "task_id": args.task_id,
                        "task_revision": args.task_revision,
                        "task_blob_sha": args.task_blob_sha,
                        "task_commit_sha": args.task_commit_sha,
                        "executor": args.executor,
                        **(
                            {
                                "model": remote_profile.model,
                                "reasoning_effort": remote_profile.reasoning_effort,
                                "model_source": remote_profile.model_source,
                                "effort_source": remote_profile.effort_source,
                            }
                            if remote_profile is not None else {}
                        ),
                    },
                )
            print(outcome.render())
            return outcome.exit_code
        elif args.command == "remote-status":
            from .remote_surface import RemoteSurfaceError, remote_status

            try:
                repo_root = resolve_repository(args.repo)
                summary = remote_status(
                    repo=repo_root,
                    state_root=runtime_state_root(repo_root),
                    dispatch_id=args.dispatch_id,
                )
            except (OperatorError, RemoteSurfaceError) as exc:
                message = (
                    str(exc)
                    if isinstance(exc, RemoteSurfaceError)
                    else "status repository is unavailable"
                )
                raise OperatorError(message) from exc
            print(summary.render())
        elif args.command == "remote-approve":
            from .remote_surface import RemoteSurfaceError, record_remote_approval

            try:
                repo_root = resolve_repository(args.repo)
                summary = record_remote_approval(
                    repo=repo_root,
                    state_root=runtime_state_root(repo_root),
                    source_run_id=args.source_run_id,
                    finding_id=args.finding_id,
                    approver=args.approver,
                )
            except (OperatorError, RemoteSurfaceError) as exc:
                message = (
                    str(exc)
                    if isinstance(exc, RemoteSurfaceError)
                    else "approval repository is unavailable"
                )
                raise OperatorError(message) from exc
            print(summary.render())
        elif args.command == "approved-remediation-intent":
            intent_root = resolve_repository(args.repo)
            from .correction_dispatch import existing_correction_profile
            exists, intent_profile = existing_correction_profile(
                state_root=runtime_state_root(intent_root),
                repo_root=intent_root,
                correction_dispatch_id=args.correction_dispatch_id,
            )
            intent_profile = _authorization_profile(
                repo=intent_root,
                authorization_id=args.correction_dispatch_id,
                executor=args.executor,
                existing=exists,
                bound_profile=intent_profile,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                model_source=args.model_source,
                effort_source=args.effort_source,
            )
            try:
                approval, outcome = run_approved_remediation_intent(
                    args.correction_dispatch_id,
                    args.source_run_id,
                    args.finding_id,
                    executor=args.executor,
                    approver=args.approver,
                    model=intent_profile.model if intent_profile is not None else None,
                    reasoning_effort=(intent_profile.reasoning_effort if intent_profile is not None else None),
                    model_source=(intent_profile.model_source if intent_profile is not None else None),
                    effort_source=(intent_profile.effort_source if intent_profile is not None else None),
                    repo=intent_root,
                    native_runner=native_runner,
                    verification_runner=verification_runner,
                    monotonic_clock=monotonic_clock,
                )
            finally:
                _project_operational_delivery(
                    intent_root,
                    family="REMEDIATION",
                    delivery_id=args.correction_dispatch_id,
                    selectors={
                        "source_run_id": args.source_run_id,
                        "finding_id": args.finding_id,
                        "executor": args.executor,
                        **({
                            "model": intent_profile.model,
                            "reasoning_effort": intent_profile.reasoning_effort,
                            "model_source": intent_profile.model_source,
                            "effort_source": intent_profile.effort_source,
                        } if intent_profile is not None else {}),
                    },
                )
            print(approval.render())
            print(outcome.render())
            return outcome.exit_code
        elif args.command == "approved-remediation-wakeup":
            from .correction_dispatch import (
                CorrectionDispatchError,
                CorrectionInvocation,
                execute_correction_dispatch,
                existing_correction_profile,
                reject_existing_selector_collision,
            )
            from .remote_surface import RemoteSurfaceError, require_current_approval

            repo_root = resolve_repository(args.repo)
            state_root = runtime_state_root(repo_root)
            exists, remote_profile = existing_correction_profile(
                state_root=state_root,
                repo_root=repo_root,
                correction_dispatch_id=args.correction_dispatch_id,
            )
            remote_profile = _authorization_profile(
                repo=repo_root,
                authorization_id=args.correction_dispatch_id,
                executor=args.executor,
                existing=exists,
                bound_profile=remote_profile,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                model_source=args.model_source,
                effort_source=args.effort_source,
            )
            try:
                try:
                    reject_existing_selector_collision(
                        state_root=state_root,
                        repo_root=repo_root,
                        correction_dispatch_id=args.correction_dispatch_id,
                        source_run_id=args.source_run_id,
                        finding_id=args.finding_id,
                        executor=args.executor,
                        execution_profile=remote_profile,
                    )
                    approval = require_current_approval(
                        repo=repo_root,
                        state_root=state_root,
                        source_run_id=args.source_run_id,
                        finding_id=args.finding_id,
                    )

                    def invoke_remediation() -> CorrectionInvocation:
                        try:
                            summary = run_remediation(
                                approval.task_id,
                                finding_id=args.finding_id,
                                source_run_id=args.source_run_id,
                                approved_remediation_sha=approval.remediation_sha,
                                correction_dispatch_id=args.correction_dispatch_id,
                                executor=args.executor,
                                model=(
                                    remote_profile.model
                                    if remote_profile is not None else None
                                ),
                                reasoning_effort=(
                                    remote_profile.reasoning_effort
                                    if remote_profile is not None else None
                                ),
                                model_source=(
                                    remote_profile.model_source
                                    if remote_profile is not None else None
                                ),
                                effort_source=(
                                    remote_profile.effort_source
                                    if remote_profile is not None else None
                                ),
                                repo=repo_root,
                                native_runner=native_runner,
                                verification_runner=verification_runner,
                                monotonic_clock=monotonic_clock,
                            )
                            return CorrectionInvocation(0, summary.run_id)
                        except OperatorError as exc:
                            print(f"AIOS ERROR: {exc}", file=sys.stderr)
                            return CorrectionInvocation(1)

                    outcome = execute_correction_dispatch(
                        state_root=state_root,
                        repo_root=repo_root,
                        correction_dispatch_id=args.correction_dispatch_id,
                        source_run_id=args.source_run_id,
                        finding_id=args.finding_id,
                        executor=args.executor,
                        approval=approval,
                        invoke_remediation=invoke_remediation,
                        execution_profile=remote_profile,
                    )
                except (RemoteSurfaceError, CorrectionDispatchError) as exc:
                    raise OperatorError(str(exc)) from exc
            finally:
                _project_operational_delivery(
                    repo_root,
                    family="REMEDIATION",
                    delivery_id=args.correction_dispatch_id,
                    selectors={
                        "source_run_id": args.source_run_id,
                        "finding_id": args.finding_id,
                        "executor": args.executor,
                        **(
                            {
                                "model": remote_profile.model,
                                "reasoning_effort": remote_profile.reasoning_effort,
                                "model_source": remote_profile.model_source,
                                "effort_source": remote_profile.effort_source,
                            }
                            if remote_profile is not None else {}
                        ),
                    },
                )
            print(outcome.render())
            return outcome.exit_code
        elif args.command == "remediate":
            summary = run_remediation(
                args.task_id,
                review=args.review,
                remediation=args.remediation,
                prior_review=args.prior_review,
                finding_id=args.finding,
                executor=args.executor,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                repo=args.repo,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
            print(summary.render())
        elif args.command == "preflight-remediation":
            observation = preflight_remediation(
                args.task_id,
                finding_id=args.finding,
                source_run_id=args.source_run,
                approved_remediation_sha=args.approved_remediation_sha,
                repo=args.repo,
            )
            print(observation.render())
            return 0 if observation.status == "READY" else 1
        elif args.command == "accept-candidate":
            summary = accept_candidate(
                args.task_id,
                finding_id=args.finding,
                executor=args.executor,
                repo=args.repo,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
            print(summary.render())
        elif args.command == "repair":
            summary = run_repair(
                args.failed_run_id,
                executor=args.executor,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                repo=args.repo,
                repair=args.repair,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
            print(summary.render())
        elif args.command == "repair-wakeup":
            repair_root = resolve_repository(args.repo)
            from .repair_dispatch import existing_repair_profile
            exists, repair_profile = existing_repair_profile(
                state_root=runtime_state_root(repair_root),
                repo_root=repair_root,
                repair_dispatch_id=args.repair_dispatch_id,
            )
            repair_profile = _authorization_profile(
                repo=repair_root,
                authorization_id=args.repair_dispatch_id,
                executor=args.executor,
                existing=exists,
                bound_profile=repair_profile,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                model_source=args.model_source,
                effort_source=args.effort_source,
            )
            try:
                outcome = run_repair_wakeup(
                    args.repair_dispatch_id,
                    args.failed_run_id,
                    args.repair_sha,
                    executor=args.executor,
                    model=repair_profile.model if repair_profile is not None else None,
                    reasoning_effort=(repair_profile.reasoning_effort if repair_profile is not None else None),
                    model_source=(repair_profile.model_source if repair_profile is not None else None),
                    effort_source=(repair_profile.effort_source if repair_profile is not None else None),
                    repo=repair_root,
                    native_runner=native_runner,
                    verification_runner=verification_runner,
                    monotonic_clock=monotonic_clock,
                )
            finally:
                _project_operational_delivery(
                    repair_root,
                    family="REPAIR",
                    delivery_id=args.repair_dispatch_id,
                    selectors={
                        "failed_run_id": args.failed_run_id,
                        "repair_sha": args.repair_sha,
                        "executor": args.executor,
                        **({
                            "model": repair_profile.model,
                            "reasoning_effort": repair_profile.reasoning_effort,
                            "model_source": repair_profile.model_source,
                            "effort_source": repair_profile.effort_source,
                        } if repair_profile is not None else {}),
                    },
                )
            print(outcome.render())
            return outcome.exit_code
        elif args.command == "preflight-repair":
            observation = preflight_repair(
                args.failed_run_id,
                repo=args.repo,
                repair=args.repair,
            )
            print(observation.render())
            return 0 if observation.status == "READY" else 1
        elif args.command == "recover-primary":
            summary = recover_primary(
                args.run_id,
                repo=args.repo,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
        elif args.command in ("ingress", "ingest"):
            from .authoring_ingress import AuthoringIngressError, ingest_carrier

            try:
                repo_root = resolve_repository(args.repo)
                if args.file and args.stdin:
                    raise OperatorError("cannot specify both --file and --stdin")
                if args.file and args.source:
                    raise OperatorError("cannot specify both positional source and --file")
                if args.stdin and args.source and args.source != "-":
                    raise OperatorError("cannot specify both positional source and --stdin")
                source = args.file if args.file is not None else args.source
                if args.stdin:
                    source = "-"
                summary = ingest_carrier(source=source, repo=repo_root)
            except (OperatorError, AuthoringIngressError) as exc:
                raise OperatorError(str(exc)) from exc
            print(summary.render())
        elif args.command == "performance":
            try:
                print(cmd_performance(args.task_id, repo=args.repo))
            except PerformanceObservationError as exc:
                raise OperatorError(str(exc)) from exc
        elif args.command in ("integrate-correction", "integrate", "correction-integration"):
            result = integrate_correction(
                args.task_id,
                task_revision=args.task_revision,
                cumulative_tip_run_id=args.cumulative_tip_run,
                cumulative_tip_candidate_sha=args.cumulative_tip_candidate,
                authorized_main_sha=args.authorized_main,
                repo=args.repo,
            )
            print(result.render() if args.json else result.render_human())
        else:
            retry_transport(args.run_id, repo=args.repo)
            print(f"AIOS TRANSPORT PASS\nrun: {args.run_id}")
    except (
        OperatorError,
        DispatchError,
        _CorrectionDispatchError,
        _RepairDispatchError,
        ExecutionProfileError,
    ) as exc:
        print(f"AIOS ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
