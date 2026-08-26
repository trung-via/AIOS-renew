"""Manual two-phase conformance smoke for Google Antigravity."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aios_renew import (  # noqa: E402
    AntigravityAdapter,
    ExecutorBoundary,
    ResultPackage,
    Run,
    RunLeaseRegistry,
    RunTaskReference,
    Task,
    validate_task,
)


SMOKE_CONTENT = b"AIOS smoke pass\n"
SMOKE_FILE = "SMOKE_OK.txt"
SMOKE_RUN_ID = "RUN-010-SMOKE"
SMOKE_TASK_SOURCE = """
task_id: TASK-010-SMOKE
revision: 1
goal: Create file SMOKE_OK.txt.
problem: Verify that an executor can complete the canonical task end to end.
assumptions: []
scope:
  inspect:
    - README.md
  modify:
    - SMOKE_OK.txt
non_goals:
  - Modify any file other than SMOKE_OK.txt.
constraints:
  hard:
    - The exact SMOKE_OK.txt bytes must be AIOS smoke pass followed by LF.
    - Commit the final repository state.
    - After committing, obtain the final commit SHA using git rev-parse HEAD.
    - RESULT.head_sha must equal the final Git HEAD.
acceptance:
  - id: AC1
    condition: SMOKE_OK.txt exists.
  - id: AC2
    condition: SMOKE_OK.txt contains exactly AIOS smoke pass followed by LF.
  - id: AC3
    condition: RESULT.head_sha matches the committed final Git HEAD.
verification:
  required:
    - python -c "from pathlib import Path; assert Path('SMOKE_OK.txt').read_bytes() == b'AIOS smoke pass\\n'"
    - git rev-parse HEAD
    - git status --porcelain
"""


class AntigravitySmokeFailure(RuntimeError):
    """Raised when preparation or deterministic verification fails."""


@dataclass(frozen=True)
class PreparedHandoff:
    workspace: Path
    handoff_path: Path
    result_path: Path
    base_sha: str


@dataclass(frozen=True)
class SmokeSummary:
    run_id: str
    base_sha: str
    head_sha: str
    changed_files: tuple[str, ...]

    def render(self) -> str:
        changed = ", ".join(self.changed_files) or "(none)"
        return (
            "ANTIGRAVITY SMOKE PASS\n"
            f"run_id: {self.run_id}\n"
            f"base_sha: {self.base_sha}\n"
            f"head_sha: {self.head_sha}\n"
            f"changed_files: {changed}"
        )


def prepare_handoff(
    workspace: str | Path,
    *,
    canonical_repo: str | Path = PROJECT_ROOT,
) -> PreparedHandoff:
    """Create the isolated repo and export canonical TASK/RUN handoff JSON."""

    target = _isolated_workspace(workspace, canonical_repo=canonical_repo)
    target.mkdir(parents=True, exist_ok=False)
    _git(target, "init", "--quiet")
    _git(target, "config", "user.name", "AIOS Smoke")
    _git(target, "config", "user.email", "aios-smoke@example.invalid")
    (target / "README.md").write_bytes(b"# AIOS executor smoke\n")
    _git(target, "add", "README.md")
    _git(target, "commit", "--quiet", "-m", "baseline")
    base_sha = _git(target, "rev-parse", "HEAD")

    task = _task()
    run = Run.from_task(
        run_id=SMOKE_RUN_ID,
        task=task,
        executor="antigravity",
        base_sha=base_sha,
        workspace=str(target),
    )
    handoff_path = _handoff_path(target)
    result_path = _result_path(target)
    payload = {
        "task": asdict(task),
        "run": asdict(run),
        "result_package": {
            "format": "canonical ResultPackage JSON",
            "write_to": str(result_path),
        },
    }
    try:
        with handoff_path.open("x", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, sort_keys=True)
            file.write("\n")
    except OSError as exc:
        raise AntigravitySmokeFailure(
            f"could not write handoff file: {exc}"
        ) from exc

    return PreparedHandoff(
        workspace=target,
        handoff_path=handoff_path,
        result_path=result_path,
        base_sha=base_sha,
    )


def verify_handoff(
    *,
    workspace: str | Path,
    result_path: str | Path,
    canonical_repo: str | Path = PROJECT_ROOT,
) -> SmokeSummary:
    """Validate manual output and deterministic repository state."""

    target = _isolated_workspace(workspace, canonical_repo=canonical_repo)
    task, run = _load_handoff(target)
    if Path(run.workspace).resolve() != target:
        raise AntigravitySmokeFailure("handoff RUN workspace does not match")
    if run.executor != "antigravity":
        raise AntigravitySmokeFailure("handoff RUN executor is not antigravity")

    try:
        output = Path(result_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise AntigravitySmokeFailure(
            f"could not read ResultPackage JSON: {exc}"
        ) from exc

    leases = RunLeaseRegistry()
    lease = leases.acquire(run)
    adapter = AntigravityAdapter(
        transport=lambda *, task, run: output,
    )
    try:
        package = ExecutorBoundary(leases).invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=adapter,
        )
    except Exception as exc:
        raise AntigravitySmokeFailure(
            f"invalid canonical ResultPackage: {exc}"
        ) from exc

    return _verify_repository(
        workspace=target,
        base_sha=run.base_sha,
        package=package,
        run_id=run.run_id,
    )


def _verify_repository(
    *,
    workspace: Path,
    base_sha: str,
    package: ResultPackage,
    run_id: str,
) -> SmokeSummary:
    smoke_file = workspace / SMOKE_FILE
    if not smoke_file.is_file():
        raise AntigravitySmokeFailure(f"{SMOKE_FILE} does not exist")
    if smoke_file.read_bytes() != SMOKE_CONTENT:
        raise AntigravitySmokeFailure(
            f"{SMOKE_FILE} content is not exactly AIOS smoke pass\\n"
        )

    actual_head = _git(workspace, "rev-parse", "HEAD")
    if actual_head == base_sha:
        raise AntigravitySmokeFailure(
            "final Git HEAD did not advance beyond the baseline"
        )
    if package.result.head_sha != actual_head:
        raise AntigravitySmokeFailure(
            "RESULT.head_sha does not match actual final Git HEAD: "
            f"{package.result.head_sha} != {actual_head}"
        )
    status = _git(workspace, "status", "--porcelain")
    if status:
        raise AntigravitySmokeFailure(f"final working tree is not clean: {status}")

    return SmokeSummary(
        run_id=run_id,
        base_sha=base_sha,
        head_sha=actual_head,
        changed_files=package.result.changed_files,
    )


def _load_handoff(workspace: Path) -> tuple[Task, Run]:
    try:
        payload = json.loads(_handoff_path(workspace).read_text(encoding="utf-8"))
        root = _mapping(payload, "handoff")
        task = validate_task(root["task"])
        run_data = _mapping(root["run"], "run")
        task_data = _mapping(run_data["task"], "run.task")
        run = Run(
            run_id=run_data["run_id"],
            task=RunTaskReference(
                id=task_data["id"],
                revision=task_data["revision"],
            ),
            executor=run_data["executor"],
            base_sha=run_data["base_sha"],
            workspace=run_data["workspace"],
            head_sha=run_data.get("head_sha"),
            status=run_data["status"],
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AntigravitySmokeFailure(f"invalid handoff file: {exc}") from exc
    return task, run


def _task() -> Task:
    import yaml

    return validate_task(yaml.safe_load(SMOKE_TASK_SOURCE))


def _isolated_workspace(
    workspace: str | Path,
    *,
    canonical_repo: str | Path,
) -> Path:
    target = Path(workspace).resolve()
    canonical = Path(canonical_repo).resolve()
    try:
        target.relative_to(canonical)
    except ValueError:
        return target
    raise AntigravitySmokeFailure(
        "smoke workspace must be outside the canonical repository"
    )


def _handoff_path(workspace: Path) -> Path:
    return workspace.parent / f"{workspace.name}.antigravity-handoff.json"


def _result_path(workspace: Path) -> Path:
    return workspace.parent / f"{workspace.name}.result-package.json"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping")
    return value


def _git(workspace: Path, *args: str) -> str:
    command = ("git", "-C", str(workspace), *args)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise AntigravitySmokeFailure(f"Git invocation failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise AntigravitySmokeFailure(
            f"Git command failed with code {completed.returncode}: {detail}"
        )
    return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--workspace", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--workspace", required=True)
    verify.add_argument("--result", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "prepare":
            prepared = prepare_handoff(args.workspace)
            print("ANTIGRAVITY SMOKE PREPARED")
            print(f"workspace: {prepared.workspace}")
            print(f"handoff: {prepared.handoff_path}")
            print(f"result: {prepared.result_path}")
            print(f"base_sha: {prepared.base_sha}")
        else:
            summary = verify_handoff(
                workspace=args.workspace,
                result_path=args.result,
            )
            print(summary.render())
    except Exception as exc:
        print(f"ANTIGRAVITY SMOKE FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
