"""Thin Human-facing operator above the frozen AIOS-renew kernel."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .antigravity_adapter import (
    AntigravityAdapter,
    AntigravityExecutionError,
    AntigravityOutputError,
)
from .artifacts import (
    ArtifactValidationError,
    Result,
    ResultPackage,
    validate_evidence,
    validate_result,
    validate_result_package,
)
from .codex_adapter import CodexAdapter, CodexExecutionError, CodexOutputError
from .executor import ExecutorBoundary, ExecutorBoundaryError
from .run import Run, RunLeaseRegistry, RunTaskReference
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


NativeRunner = Callable[..., subprocess.CompletedProcess[str]]
CODEX_SANDBOXES = ("workspace-write", "danger-full-access")


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
    results: Path
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


def resolve_repository(path: str | Path | None = None) -> Path:
    """Resolve an explicit path or current directory to its real Git root."""

    candidate = Path.cwd() if path is None else Path(path)
    try:
        completed = subprocess.run(
            ("git", "-C", str(candidate), "rev-parse", "--show-toplevel"),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise OperatorError(f"Git invocation failed: {exc}") from exc
    if completed.returncode != 0:
        raise OperatorError(f"not a Git repository: {candidate}")
    return Path(completed.stdout.strip()).resolve()


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
        results=state_root / "results",
        lock=state_root / "operator.lock",
    )
    for path in (paths.runs, paths.handoffs, paths.results):
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
    codex_sandbox: str = "workspace-write",
    native_runner: NativeRunner = subprocess.run,
) -> RunSummary:
    """Execute a stored TASK through the frozen kernel boundary."""

    root = resolve_repository(repo)
    task = load_task(root, task_id)
    if executor not in ("codex", "antigravity"):
        raise OperatorError(f"unsupported executor: {executor}")
    if executor == "codex" and codex_sandbox not in CODEX_SANDBOXES:
        raise OperatorError(f"unsupported Codex sandbox: {codex_sandbox}")
    state = runtime_paths(root)

    with RepositoryLock(state.lock):
        dirty = _git(root, "status", "--porcelain")
        if dirty:
            raise OperatorError("repository dirty")
        base_sha = _git(root, "rev-parse", "HEAD")
        run_id = next_run_id(task_id, state.runs)
        run = Run.from_task(
            run_id=run_id,
            task=task,
            executor=executor,
            base_sha=base_sha,
            workspace=str(root),
        )
        result_path = state.results / f"{run_id}.json"
        _write_json(state.runs / f"{run_id}.json", asdict(run))

        if executor == "codex":
            selected_adapter = CodexAdapter(
                runner=_codex_runner(native_runner, codex_sandbox)
            )
        else:
            handoff_path = state.handoffs / f"{run_id}.json"
            _write_json(
                handoff_path,
                {
                    "task": asdict(task),
                    "run": asdict(run),
                    "result_package_path": str(result_path),
                },
            )
            selected_adapter = AntigravityAdapter(
                transport=_antigravity_transport(
                    repo=root,
                    handoff_path=handoff_path,
                    result_path=result_path,
                    native_runner=native_runner,
                )
            )

        leases = RunLeaseRegistry()
        lease = leases.acquire(run)
        try:
            package = ExecutorBoundary(leases).invoke(
                task=task,
                run=run,
                lease=lease,
                adapter=selected_adapter,
            )
        except (CodexOutputError, AntigravityOutputError, ArtifactValidationError) as exc:
            raise OperatorError(f"invalid canonical ResultPackage: {exc}") from exc
        except CodexExecutionError as exc:
            raise OperatorError(f"Codex invocation failed: {exc}") from exc
        except AntigravityExecutionError as exc:
            raise OperatorError(str(exc)) from exc
        except ExecutorBoundaryError as exc:
            raise OperatorError(f"executor boundary failed: {exc}") from exc

        actual_head = _git(root, "rev-parse", "HEAD")
        if package.result.head_sha != actual_head:
            raise OperatorError("RESULT.head_sha mismatch")
        post_status = _git(root, "status", "--porcelain")
        if post_status:
            raise OperatorError("working tree dirty after execution")
        if package.result.changed_files and actual_head == base_sha:
            raise OperatorError("final Git HEAD did not advance")

        _require_changed_files(
            root,
            task,
            package,
            base_sha=base_sha,
            actual_head=actual_head,
        )

        _require_complete_result(task, package)

        _write_json(result_path, result_package_data(package))

        return RunSummary(
            task_id=task_id,
            run_id=run_id,
            executor=executor,
            base_sha=base_sha,
            head_sha=actual_head,
            result_path=result_path,
        )


def run_remediation(
    task_id: str,
    *,
    review: Review | str | Path,
    remediation: Remediation | str | Path,
    prior_review: Review | str | Path | None = None,
    executor: str,
    repo: str | Path | None = None,
    codex_sandbox: str = "workspace-write",
    native_runner: NativeRunner = subprocess.run,
) -> RemediationSummary:
    """Execute one bound remediation without entering the TASK execution path."""

    root = resolve_repository(repo)
    task = load_task(root, task_id)
    canonical_review = (
        review if isinstance(review, Review) else load_review(review)
    )
    canonical_remediation = (
        remediation
        if isinstance(remediation, Remediation)
        else load_remediation(remediation)
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
    if executor == "codex" and codex_sandbox not in CODEX_SANDBOXES:
        raise OperatorError(f"unsupported Codex sandbox: {codex_sandbox}")
    if (
        canonical_remediation.action == "CODE_FIX"
        and not canonical_remediation.modification_scope
    ):
        raise OperatorError("CODE_FIX remediation modification scope is empty")
    if not canonical_remediation.affected_verification:
        raise OperatorError("REMEDIATION affected verification is empty")

    with RepositoryLock(state.lock):
        if _git(root, "status", "--porcelain"):
            raise OperatorError("repository dirty")
        actual_baseline = _git(root, "rev-parse", "HEAD")
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
        result_path = state.results / f"{run_id}.json"
        _write_json(
            state.runs / f"{run_id}.json",
            {"kind": "REMEDIATION", "execution": asdict(execution)},
        )

        if executor == "codex":
            selected_adapter = CodexAdapter(
                runner=_codex_runner(native_runner, codex_sandbox)
            )
        else:
            handoff_path = state.handoffs / f"{run_id}.json"
            _write_json(
                handoff_path,
                {
                    "remediation_execution": asdict(execution),
                    "result_package_path": str(result_path),
                },
            )
            selected_adapter = AntigravityAdapter(
                transport=_antigravity_remediation_transport(
                    repo=root,
                    handoff_path=handoff_path,
                    result_path=result_path,
                    native_runner=native_runner,
                )
            )

        try:
            package = selected_adapter.execute_remediation(execution=execution)
        except (CodexOutputError, AntigravityOutputError, ArtifactValidationError) as exc:
            raise OperatorError(f"invalid canonical ResultPackage: {exc}") from exc
        except CodexExecutionError as exc:
            raise OperatorError(f"Codex invocation failed: {exc}") from exc
        except AntigravityExecutionError as exc:
            raise OperatorError(str(exc)) from exc

        actual_head = _git(root, "rev-parse", "HEAD")
        if package.result.head_sha != actual_head:
            raise OperatorError("RESULT.head_sha mismatch")
        if _git(root, "status", "--porcelain"):
            raise OperatorError("working tree dirty after execution")
        _require_remediation_result(
            root, execution, package, actual_head=actual_head
        )
        _write_json(result_path, result_package_data(package))
        return RemediationSummary(
            task_id=task_id,
            review_id=canonical_review.review_id,
            finding_id=canonical_remediation.finding_id,
            run_id=run_id,
            executor=executor,
            reviewed_sha=actual_baseline,
            head_sha=actual_head,
            result_path=result_path,
        )


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
    elif actual_changed and actual_head == execution.remediation.reviewed_sha:
        raise OperatorError("CODE_FIX committed changes did not advance HEAD")


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



def _require_changed_files(
    repo: Path,
    task: Task,
    package: ResultPackage,
    *,
    base_sha: str,
    actual_head: str,
) -> None:
    """Bind declared files to the committed delta and the exact TASK scope."""

    output = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        base_sha,
        actual_head,
        strip_stdout=False,
    )
    actual_changed_files = {path for path in output.split("\0") if path}
    declared_changed_files = set(package.result.changed_files)
    if declared_changed_files != actual_changed_files:
        raise OperatorError("RESULT.changed_files mismatch")

    out_of_scope = actual_changed_files.difference(task.scope.modify)
    if out_of_scope:
        raise OperatorError(
            "committed changed paths outside TASK.scope.modify: "
            + ", ".join(sorted(out_of_scope))
        )


def _require_complete_result(task: Task, package: ResultPackage) -> None:
    """Reject canonical packages that do not establish TASK completion."""

    if package.result.unresolved:
        raise OperatorError("RESULT has unresolved items")

    satisfied = {
        acceptance_id
        for claim in package.result.claims
        for acceptance_id in claim.satisfies
    }
    missing = [item.id for item in task.acceptance if item.id not in satisfied]
    if missing:
        raise OperatorError(
            "RESULT does not satisfy acceptance criteria: " + ", ".join(missing)
        )


def result_package_data(package: ResultPackage) -> dict[str, Any]:
    """Serialize the canonical package using its public JSON field names."""

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


def _codex_runner(native_runner: NativeRunner, sandbox: str) -> NativeRunner:
    def run(command: tuple[str, ...], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        updated = list(command)
        try:
            index = updated.index("--sandbox")
        except ValueError as exc:
            raise OperatorError("Codex command has no sandbox option") from exc
        updated[index + 1] = sandbox
        return native_runner(tuple(updated), **kwargs)

    return run


def _antigravity_transport(
    *,
    repo: Path,
    handoff_path: Path,
    result_path: Path,
    native_runner: NativeRunner,
) -> Callable[..., str]:
    instruction = (
        f"Read the AIOS handoff JSON at {handoff_path}. "
        "Execute its canonical TASK and RUN exactly within the supplied repository. "
        "Commit the final implementation state when required, obtain final Git HEAD, "
        "and write canonical ResultPackage JSON to the result_package_path specified "
        "in the handoff. The ResultPackage must be an object with result and evidence. "
        "result must contain head_sha, claims, changed_files, and unresolved. Each "
        "claim must contain id, satisfies, claim, and evidence. Each evidence entry "
        "must contain evidence_id, run_id, subject_sha, type, source.command, "
        "result.exit_code, result.summary, and raw.path. evidence.run_id must reference "
        "the current RUN; evidence.subject_sha must equal result.head_sha; every "
        "claim.satisfies entry must be a known TASK acceptance ID; and every claim "
        "evidence reference must name an existing evidence_id. Execute every command "
        "in task.verification.required exactly as written and include successful "
        "evidence for each: evidence.source.command must exactly equal the required "
        "command and evidence.result.exit_code must be zero. Finish only after the "
        "ResultPackage file exists."
    )

    def transport(*, task: Task, run: Run) -> str:
        del task, run
        try:
            completed = native_runner(
                (
                    "agy",
                    "--print",
                    instruction,
                    "--add-dir",
                    str(repo),
                    "--effort",
                    "low",
                    "--mode",
                    "accept-edits",
                    "--disable-slash-commands",
                    "--output-format",
                    "json",
                    "--print-timeout",
                    "5m",
                ),
                cwd=str(repo),
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AntigravityExecutionError("Antigravity CLI not found: agy") from exc
        except OSError as exc:
            raise AntigravityExecutionError(
                f"Antigravity CLI invocation failed: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            message = (
                f"Antigravity CLI returned nonzero ({completed.returncode})"
            )
            if detail:
                message = f"{message}: {detail}"
            raise AntigravityExecutionError(message)
        if not result_path.is_file():
            detail = completed.stderr.strip() or completed.stdout.strip()
            message = "Antigravity ResultPackage missing"
            if detail:
                message = f"{message}: {detail}"
            raise AntigravityExecutionError(message)
        try:
            return result_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AntigravityExecutionError(
                f"Antigravity ResultPackage unreadable: {exc}"
            ) from exc

    return transport


def _antigravity_remediation_transport(
    *,
    repo: Path,
    handoff_path: Path,
    result_path: Path,
    native_runner: NativeRunner,
) -> Callable[..., str]:
    instruction = (
        f"Read the AIOS remediation handoff JSON at {handoff_path}. "
        "Execute exactly its one remediation_execution contract. Do not run or "
        "restart the original TASK, scan for a different repository, perform semantic "
        "review or repeat unaffected verification. Change only paths in "
        "remediation.modification_scope. For CODE_FIX, commit the permitted "
        "remediation delta before returning; for EVIDENCE_ONLY, do not create a "
        "code commit. Execute only commands in "
        "remediation.affected_verification exactly as written. Write one canonical "
        "ResultPackage to result_package_path with empty result.claims and "
        "result.unresolved. Include exact successful evidence for every affected "
        "command; bind evidence.run_id to the remediation RUN and both "
        "evidence.subject_sha and result.head_sha to final Git HEAD. Finish only "
        "after the ResultPackage file exists."
    )

    def transport(*, execution: RemediationExecution) -> str:
        del execution
        try:
            completed = native_runner(
                (
                    "agy",
                    "--print",
                    instruction,
                    "--add-dir",
                    str(repo),
                    "--effort",
                    "low",
                    "--mode",
                    "accept-edits",
                    "--disable-slash-commands",
                    "--output-format",
                    "json",
                    "--print-timeout",
                    "5m",
                ),
                cwd=str(repo),
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AntigravityExecutionError(
                "Antigravity CLI not found: agy"
            ) from exc
        except OSError as exc:
            raise AntigravityExecutionError(
                f"Antigravity CLI invocation failed: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            message = f"Antigravity CLI returned nonzero ({completed.returncode})"
            if detail:
                message = f"{message}: {detail}"
            raise AntigravityExecutionError(message)
        if not result_path.is_file():
            detail = completed.stderr.strip() or completed.stdout.strip()
            message = "Antigravity ResultPackage missing"
            if detail:
                message = f"{message}: {detail}"
            raise AntigravityExecutionError(message)
        try:
            return result_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AntigravityExecutionError(
                f"Antigravity ResultPackage unreadable: {exc}"
            ) from exc

    return transport


def _git(repo: Path, *args: str, strip_stdout: bool = True) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise OperatorError(f"Git invocation failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise OperatorError(f"Git command failed: {detail}")
    return completed.stdout.strip() if strip_stdout else completed.stdout


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
    run_parser.add_argument("--codex-sandbox", choices=CODEX_SANDBOXES)
    remediation_parser = commands.add_parser(
        "remediate", help="Execute one canonical narrow REMEDIATION"
    )
    remediation_parser.add_argument("task_id")
    remediation_parser.add_argument("--review", required=True)
    remediation_parser.add_argument("--remediation", required=True)
    remediation_parser.add_argument("--prior-review")
    remediation_parser.add_argument(
        "--executor", required=True, choices=("codex", "antigravity")
    )
    remediation_parser.add_argument("--repo")
    remediation_parser.add_argument("--codex-sandbox", choices=CODEX_SANDBOXES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "task":
            print(describe_task(args.task_id, repo=args.repo).render())
        elif args.command == "run":
            if args.executor != "codex" and args.codex_sandbox is not None:
                raise OperatorError("--codex-sandbox is only valid for Codex")
            summary = run_task(
                args.task_id,
                executor=args.executor,
                repo=args.repo,
                codex_sandbox=args.codex_sandbox or "workspace-write",
            )
            print(summary.render())
        else:
            if args.executor != "codex" and args.codex_sandbox is not None:
                raise OperatorError("--codex-sandbox is only valid for Codex")
            summary = run_remediation(
                args.task_id,
                review=args.review,
                remediation=args.remediation,
                prior_review=args.prior_review,
                executor=args.executor,
                repo=args.repo,
                codex_sandbox=args.codex_sandbox or "workspace-write",
            )
            print(summary.render())
    except OperatorError as exc:
        print(f"AIOS ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
