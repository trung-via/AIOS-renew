"""Thin Human-facing operator above the frozen AIOS-renew kernel."""

from __future__ import annotations

import argparse
import hashlib
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
from .dispatch_reconciliation import (
    DispatchError,
    DispatchInvocation,
    bind_dispatch_run,
    execute_dispatch,
)
from .executor import ExecutorBoundaryError
from .performance_observation import (
    PerformanceObservation,
    PerformanceObservationError,
    observe_performance,
)
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
    task_run_prefix,
    transport_admission_failure,
    transport_failure,
    transport_post_pass,
    validate_runtime_failure_binding,
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
    )


@contextmanager
def _remote_observation_repository(control_repo: Path) -> Iterator[Path]:
    """Provide an isolated Git object/config store for read-only remote resolution."""

    branch = _git(control_repo, "rev-parse", "--abbrev-ref", "HEAD")
    common_dir = Path(_git(control_repo, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = control_repo / common_dir
    control_objects = (common_dir.resolve() / "objects").as_posix()
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
) -> RunSummary:
    """Execute a TASK and persist/transport deterministic pre-PASS failure facts."""

    root = resolve_repository(repo)
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
            admission=admission,
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
                )
        else:
            _persist_and_transport_admission_failure(
                root, admission=admission, failure=original
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
    synchronize: bool = True,
    preflight_sha: str | None = None,
    dispatch_id: str | None = None,
    task_revision: int | None = None,
    task_blob_sha: str | None = None,
    task_commit_sha: str | None = None,
    admission: dict[str, Any] | None = None,
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
        if dispatch_id is not None:
            bind_dispatch_run(
                state_root=state.root,
                dispatch_id=dispatch_id,
                task_id=task_id,
                executor=executor,
                run_id=run_id,
                task_revision=task_revision,
                task_blob_sha=task_blob_sha,
                task_commit_sha=task_commit_sha,
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
    failed_run_id: str, *, executor: str | None, repo: str | Path | None = None,
    repair: Mapping[str, Any] | str | Path | None = None,
    required_repair_sha: str | None = None,
    repair_dispatch_id: str | None = None,
    native_runner: NativeRunner = subprocess.run,
    verification_runner: VerificationRunner = subprocess.run,
    monotonic_clock: MonotonicClock = time.monotonic,
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
    if action == "CONTINUE_IMPLEMENTATION":
        if not scope:
            raise OperatorError(
                "CONTINUE_IMPLEMENTATION REPAIR modification scope is empty"
            )
        if failure.get("phase") not in ("EXECUTION", "COMPLETION_GATE"):
            raise OperatorError(
                "CONTINUE_IMPLEMENTATION requires a pre-verification failure"
            )
        if (
            candidate.get("transportable") is not True
            or candidate.get("dirty") is not False
            or candidate.get("descends_from_base") is not True
            or candidate.get("outside_task_scope") != []
        ):
            raise OperatorError(
                "CONTINUE_IMPLEMENTATION requires a clean transportable candidate"
            )
    if action == "FINALIZE_CANDIDATE":
        if failure.get("phase") not in ("EXECUTION", "COMPLETION_GATE"):
            raise OperatorError(
                "FINALIZE_CANDIDATE requires a pre-verification failure"
            )
        if (
            candidate.get("transportable") is not True
            or candidate.get("dirty") is not False
            or candidate.get("descends_from_base") is not True
            or candidate.get("outside_task_scope") != []
        ):
            raise OperatorError(
                "FINALIZE_CANDIDATE requires a clean transportable candidate"
            )
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
) -> RepairSummary:
    if executor is not None and executor not in (
        "codex", "antigravity", "antigravity-minimax"
    ):
        _set_admission_boundary(
            admission, "CANONICAL_CONTRACT_ADMISSION", "TASK_CONTRACT_REJECTED"
        )
        raise OperatorError(f"unsupported executor: {executor}")
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

        if repair_dispatch_id is not None:
            from .repair_dispatch import bind_repair_run

            bind_repair_run(
                state_root=state.root,
                repair_dispatch_id=repair_dispatch_id,
                run_id=run_id,
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
        package = validate_preverification_candidate(
            content,
            task=task,
            run_id=failed_run_id,
            subject_sha=failed_head,
        )
    except ArtifactValidationError as exc:
        raise OperatorError(f"invalid pre-verification candidate: {exc}") from exc

    candidate = failure.get("candidate")
    if not isinstance(candidate, Mapping):
        return None
    changed_files = candidate.get("changed_files")
    if not isinstance(changed_files, list) or not all(
        isinstance(item, str) and item for item in changed_files
    ):
        raise OperatorError("invalid pre-verification candidate changed-files binding")
    if repo is not None:
        resolved_root_base = result_base_sha
        if resolved_root_base is None:
            resolved_root_base = root_base_sha
        if resolved_root_base is None and failure.get("continuation_of") is None:
            resolved_root_base = failure.get("base_sha")
        if not isinstance(resolved_root_base, str) or not resolved_root_base:
            raise OperatorError("invalid REPAIR result-base lineage")
        actual_root_changed = _committed_changed_files(
            repo, resolved_root_base, failed_head
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
        actual_candidate_changed = _committed_changed_files(
            repo, failed_base, failed_head
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
    if (
        failure.get("phase") != "VERIFICATION"
        or candidate.get("repairable") is not True
        or candidate.get("transportable") is not True
        or candidate.get("dirty") is not False
        or candidate.get("descends_from_base") is not True
        or candidate.get("outside_task_scope") != []
    ):
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


def _derive_remediation_source_root(
    repo: Path,
    *,
    task: Task,
    execution: RemediationExecution,
    seen: frozenset[tuple[str, str]] = frozenset(),
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
            if source_execution is None:
                validate_result_package(
                    task=task, run=source_run, result=result, evidence=evidence
                )
                prior_review = None
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
                    repo, task=task, execution=source_execution, seen=seen
                )
            elif remote.repair is None:
                root = source_run.base_sha
            else:
                repair_lineage = _decode_remote_mapping(
                    remote.repair, "source REPAIR execution"
                )
                source_failure = repair_lineage.get("failure")
                source_authorization = repair_lineage.get("repair")
                source_authorization_sha = repair_lineage.get(
                    "repair_authorization_sha"
                )
                source_task = repair_lineage.get("task")
                if (
                    not isinstance(repair_lineage.get("failed_run_id"), str)
                    or repair_lineage.get("failed_head_sha") != source_run.base_sha
                    or repair_lineage.get("run") != dict(run_data)
                    or not isinstance(source_failure, Mapping)
                    or source_failure.get("run_id")
                    != repair_lineage.get("failed_run_id")
                    or source_failure.get("failed_head_sha") != source_run.base_sha
                    or not isinstance(source_failure.get("task"), Mapping)
                    or dict(source_failure["task"])
                    != {"id": task.task_id, "revision": task.revision}
                    or not isinstance(source_authorization, Mapping)
                    or source_authorization.get("failed_run_id")
                    != repair_lineage.get("failed_run_id")
                    or source_authorization.get("failed_head_sha")
                    != source_run.base_sha
                    or not isinstance(source_authorization.get("task"), Mapping)
                    or dict(source_authorization["task"])
                    != {"id": task.task_id, "revision": task.revision}
                    or (
                        source_authorization_sha is not None
                        and (
                            not isinstance(source_authorization_sha, str)
                            or re.fullmatch(
                                r"[0-9a-f]{40}|[0-9a-f]{64}",
                                source_authorization_sha,
                            )
                            is None
                        )
                    )
                    or not isinstance(source_task, Mapping)
                    or source_task.get("task_id") != task.task_id
                    or source_task.get("revision") != task.revision
                ):
                    raise ValueError("source REPAIR lineage mismatch")
                root = repair_lineage.get("root_base_sha")
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

    state = runtime_paths(root)
    with RepositoryLock(state.lock):
        needs_restart = _synchronize_primary_branch(root, allow_restart=True)
        preflight_sha = _git(root, "rev-parse", "HEAD")
    if needs_restart:
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

    state = _runtime_paths_readonly(root)
    lock_existed = state.lock.exists()
    state_root_existed = state.root.exists()
    lock_acquired = False
    try:
        with RepositoryLock(state.lock):
            lock_acquired = True
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
        return PreflightResult(
            restart_code=_restart_primary_invocation(root, argv=argv, runner=runner)
        )
    return PreflightResult(preflight_sha=preflight_sha)


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
    source_run_id: str | None = None,
    approved_remediation_sha: str | None = None,
    correction_dispatch_id: str | None = None,
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
            or phase not in _ADMISSION_PHASES
            or reason_code not in _ADMISSION_REASONS
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
    if executor not in ("codex", "antigravity", "antigravity-minimax"):
        raise OperatorError(f"unsupported executor: {executor}")

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
            "predecessor": predecessor_record,
            "execution": asdict(execution),
        }
        if resolved.cumulative:
            run_document["execution_base"] = execution_base_record
        _write_json(run_path, run_document)
        if attempt is not None:
            attempt.bind_run(run_path)
        if correction_dispatch_id is not None:
            from .correction_dispatch import bind_correction_run

            bind_correction_run(
                state_root=state.root,
                correction_dispatch_id=correction_dispatch_id,
                run_id=run_id,
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
        tuple[
            str,
            tuple[Review, Remediation, Result, Review | None, Task],
        ]
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
            matches.append((remote.source_run_id, parsed))
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
) -> tuple[Any, Any]:
    """Record/replay A3, then enter the existing durable A6 boundary."""

    from .correction_dispatch import (
        CorrectionDispatchError,
        CorrectionInvocation,
        execute_correction_dispatch,
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
        reject_existing_selector_collision(
            state_root=state_root,
            correction_dispatch_id=correction_dispatch_id,
            source_run_id=source_run_id,
            finding_id=finding_id,
            executor=executor,
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
            correction_dispatch_id=correction_dispatch_id,
            source_run_id=source_run_id,
            finding_id=finding_id,
            executor=executor,
            approval=approval,
            invoke_remediation=invoke_remediation,
        )
    except (RemoteSurfaceError, CorrectionDispatchError) as exc:
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
) -> Any:
    """Deliver one immutable REPAIR intent through the durable outer boundary."""

    from .repair_dispatch import (
        RepairDispatchError,
        RepairInvocation,
        execute_repair_dispatch,
        reject_existing_selector_collision,
        replay_existing_repair_dispatch,
    )

    try:
        root = resolve_repository(repo)
        state_root = runtime_state_root(root)
        reject_existing_selector_collision(
            state_root=state_root,
            repair_dispatch_id=repair_dispatch_id,
            failed_run_id=failed_run_id,
            repair_sha=repair_sha,
            executor=executor,
        )
        replay = replay_existing_repair_dispatch(
            state_root=state_root,
            repair_dispatch_id=repair_dispatch_id,
            failed_run_id=failed_run_id,
            repair_sha=repair_sha,
            executor=executor,
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
            repair_dispatch_id=repair_dispatch_id,
            failed_run_id=failed_run_id,
            repair_sha=repair_sha,
            executor=executor,
            task_id=observation.task_id,
            action=action,
            invoke_repair=invoke_repair,
        )
    except RepairDispatchError as exc:
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
    run_parser.add_argument("--repo")
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
    try:
        if args.command == "task":
            print(describe_task(args.task_id, repo=args.repo).render())
        elif args.command == "state":
            print(observe_unified_state(args.task_id, repo=args.repo).render())
        elif args.command == "continue":
            outcome, exit_code = continue_task(
                args.task_id,
                executor=args.executor,
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
                repo=repo_root,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
                synchronize=False,
                preflight_sha=preflight.preflight_sha,
            )
            print(summary.render())
        elif args.command == "wakeup":
            repo_root = resolve_repository(args.repo)
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
            wakeup_argv.extend(
                ["--executor", args.executor, "--repo", str(repo_root)]
            )

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

            outcome = execute_dispatch(
                state_root=runtime_paths(repo_root).root,
                dispatch_id=args.dispatch_id,
                task_id=args.task_id,
                executor=args.executor,
                invoke_primary=invoke_primary,
                task_revision=args.task_revision,
                task_blob_sha=args.task_blob_sha,
                task_commit_sha=args.task_commit_sha,
            )
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
            approval, outcome = run_approved_remediation_intent(
                args.correction_dispatch_id,
                args.source_run_id,
                args.finding_id,
                executor=args.executor,
                approver=args.approver,
                repo=args.repo,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
            intent_root = resolve_repository(args.repo)
            _project_operational_delivery(
                intent_root,
                family="REMEDIATION",
                delivery_id=args.correction_dispatch_id,
                selectors={
                    "source_run_id": args.source_run_id,
                    "finding_id": args.finding_id,
                    "executor": args.executor,
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
                reject_existing_selector_collision,
            )
            from .remote_surface import RemoteSurfaceError, require_current_approval

            try:
                repo_root = resolve_repository(args.repo)
                state_root = runtime_state_root(repo_root)
                reject_existing_selector_collision(
                    state_root=state_root,
                    correction_dispatch_id=args.correction_dispatch_id,
                    source_run_id=args.source_run_id,
                    finding_id=args.finding_id,
                    executor=args.executor,
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
                    correction_dispatch_id=args.correction_dispatch_id,
                    source_run_id=args.source_run_id,
                    finding_id=args.finding_id,
                    executor=args.executor,
                    approval=approval,
                    invoke_remediation=invoke_remediation,
                )
            except (RemoteSurfaceError, CorrectionDispatchError) as exc:
                raise OperatorError(str(exc)) from exc
            _project_operational_delivery(
                repo_root,
                family="REMEDIATION",
                delivery_id=args.correction_dispatch_id,
                selectors={
                    "source_run_id": args.source_run_id,
                    "finding_id": args.finding_id,
                    "executor": args.executor,
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
                repo=args.repo,
                repair=args.repair,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
            print(summary.render())
        elif args.command == "repair-wakeup":
            outcome = run_repair_wakeup(
                args.repair_dispatch_id,
                args.failed_run_id,
                args.repair_sha,
                executor=args.executor,
                repo=args.repo,
                native_runner=native_runner,
                verification_runner=verification_runner,
                monotonic_clock=monotonic_clock,
            )
            repair_root = resolve_repository(args.repo)
            _project_operational_delivery(
                repair_root,
                family="REPAIR",
                delivery_id=args.repair_dispatch_id,
                selectors={
                    "failed_run_id": args.failed_run_id,
                    "repair_sha": args.repair_sha,
                    "executor": args.executor,
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
    except (OperatorError, DispatchError) as exc:
        print(f"AIOS ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
