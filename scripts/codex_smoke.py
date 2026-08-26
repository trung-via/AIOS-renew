"""Manual end-to-end smoke harness for the native Codex adapter."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aios_renew import (  # noqa: E402
    CodexAdapter,
    ExecutorAdapter,
    ExecutorBoundary,
    ResultPackage,
    Run,
    RunLeaseRegistry,
    parse_task,
)


SMOKE_CONTENT = b"AIOS smoke pass\n"
SMOKE_FILE = "SMOKE_OK.txt"
SMOKE_RUN_ID = "RUN-009-SMOKE"
SMOKE_TASK_SOURCE = """
task_id: TASK-009-SMOKE
revision: 1
goal: Create file SMOKE_OK.txt.
problem: Verify the real Codex execution path end to end.
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
    - After committing the final repository state, obtain the actual final commit SHA using git rev-parse HEAD.
    - RESULT.head_sha MUST be exactly the actual final commit SHA returned by git rev-parse HEAD.
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


class SmokeFailure(RuntimeError):
    """Raised when any smoke invariant fails."""


@dataclass(frozen=True)
class SmokeSummary:
    run_id: str
    base_sha: str
    head_sha: str
    changed_files: tuple[str, ...]
    evidence_summaries: tuple[str, ...]
    working_tree: str

    def render(self) -> str:
        changed = ", ".join(self.changed_files) or "(none)"
        evidence = "\n".join(
            f"- {summary}" for summary in self.evidence_summaries
        ) or "- (none)"
        return (
            "SMOKE PASS\n"
            f"run_id: {self.run_id}\n"
            f"base_sha: {self.base_sha}\n"
            f"head_sha: {self.head_sha}\n"
            f"changed_files: {changed}\n"
            f"working_tree: {self.working_tree}\n"
            "evidence summaries:\n"
            f"{evidence}"
        )


def initialize_smoke_repository(
    workspace: str | Path,
    *,
    canonical_repo: str | Path = PROJECT_ROOT,
) -> str:
    """Create an independent repository and return its real baseline SHA."""

    target = _isolated_workspace(workspace, canonical_repo=canonical_repo)
    target.mkdir(parents=True, exist_ok=False)
    _git(target, "init", "--quiet")
    _git(target, "config", "user.name", "AIOS Smoke")
    _git(target, "config", "user.email", "aios-smoke@example.invalid")
    (target / "README.md").write_bytes(b"# AIOS Codex smoke\n")
    _git(target, "add", "README.md")
    _git(target, "commit", "--quiet", "-m", "baseline")
    return _git(target, "rev-parse", "HEAD")


def verify_smoke_result(
    *,
    workspace: str | Path,
    base_sha: str,
    package: ResultPackage,
) -> SmokeSummary:
    """Independently verify filesystem and Git state after execution."""

    target = Path(workspace).resolve()
    smoke_file = target / SMOKE_FILE
    if not smoke_file.is_file():
        raise SmokeFailure(f"{SMOKE_FILE} does not exist")
    if smoke_file.read_bytes() != SMOKE_CONTENT:
        raise SmokeFailure(f"{SMOKE_FILE} content is not exactly AIOS smoke pass\\n")

    actual_head = _git(target, "rev-parse", "HEAD")
    if actual_head == base_sha:
        raise SmokeFailure("final Git HEAD did not advance beyond the baseline")
    if package.result.head_sha != actual_head:
        raise SmokeFailure(
            "RESULT.head_sha does not match actual final Git HEAD: "
            f"{package.result.head_sha} != {actual_head}"
        )

    status = _git(target, "status", "--porcelain")
    if status:
        raise SmokeFailure(f"final working tree is not clean: {status}")

    return SmokeSummary(
        run_id=package.evidence[0].run_id if package.evidence else SMOKE_RUN_ID,
        base_sha=base_sha,
        head_sha=actual_head,
        changed_files=package.result.changed_files,
        evidence_summaries=tuple(
            f"{item.evidence_id}: {item.result.summary}"
            for item in package.evidence
        ),
        working_tree="clean",
    )


def run_smoke(
    workspace: str | Path,
    *,
    adapter: ExecutorAdapter | None = None,
    canonical_repo: str | Path = PROJECT_ROOT,
) -> SmokeSummary:
    """Run the canonical execution path once in an independent repository."""

    base_sha = initialize_smoke_repository(
        workspace,
        canonical_repo=canonical_repo,
    )
    target = Path(workspace).resolve()
    task = parse_task(SMOKE_TASK_SOURCE)
    run = Run.from_task(
        run_id=SMOKE_RUN_ID,
        task=task,
        executor="codex",
        base_sha=base_sha,
        workspace=str(target),
    )
    leases = RunLeaseRegistry()
    lease = leases.acquire(run)
    selected_adapter = adapter if adapter is not None else CodexAdapter()

    try:
        package = ExecutorBoundary(leases).invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=selected_adapter,
        )
    except Exception as exc:
        raise SmokeFailure(f"executor smoke invocation failed: {exc}") from exc

    return verify_smoke_result(
        workspace=target,
        base_sha=base_sha,
        package=package,
    )


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
    raise SmokeFailure("smoke workspace must be outside the canonical repository")


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
        raise SmokeFailure(f"Git invocation failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SmokeFailure(
            f"Git command failed with code {completed.returncode}: {detail}"
        )
    return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Preserve the temporary Git repository for inspection.",
    )
    args = parser.parse_args(argv)

    try:
        if args.keep_workspace:
            root = Path(tempfile.mkdtemp(prefix="aios-codex-smoke-"))
            workspace = root / "repo"
            summary = run_smoke(workspace)
            print(summary.render())
            print(f"workspace: {workspace}")
        else:
            with tempfile.TemporaryDirectory(prefix="aios-codex-smoke-") as root:
                summary = run_smoke(Path(root) / "repo")
                print(summary.render())
    except Exception as exc:
        print(f"SMOKE FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
