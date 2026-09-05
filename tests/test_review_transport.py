import json
import subprocess
from pathlib import Path

import pytest

from aios_renew.review_transport import (
    ReviewTransportError,
    read_remote_task,
    resolve_remote_repair_recovery,
    transport_failure,
    transport_post_pass,
)


TASK = {"id": "TASK-058", "revision": 2}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    remote = root / "upstream.git"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "Review Transport Test")
    git(repo, "config", "user.email", "transport@example.invalid")
    git(repo, "branch", "-M", "main")
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK-058.yaml").write_text(
        "task_id: TASK-058\nrevision: 2\ngoal: historical contract\n",
        encoding="utf-8",
    )
    (repo / "subject.txt").write_text("root\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "root")
    subprocess.run(("git", "init", "--bare", "--quiet", str(remote)), check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "main")
    return repo, remote


def commit_candidate(repo: Path, label: str) -> str:
    (repo / "subject.txt").write_text(f"{label}\n", encoding="utf-8")
    git(repo, "add", "subject.txt")
    git(repo, "commit", "--quiet", "-m", label)
    return git(repo, "rev-parse", "HEAD")


def write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(content)
    return content


def publish_failure(
    repo: Path,
    files: Path,
    *,
    run_id: str,
    candidate_sha: str,
    root_base_sha: str,
    continuation_of: str | None = None,
    failure_run_id: str | None = None,
) -> tuple[bytes, bytes, bytes | None]:
    run = {
        "run_id": run_id,
        "task": TASK,
        "executor": "codex",
        "base_sha": root_base_sha,
        "workspace": "discarded-historical-workspace",
        "head_sha": None,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": failure_run_id or run_id,
        "task": TASK,
        "executor": "codex",
        "base_sha": root_base_sha,
        "failed_head_sha": candidate_sha,
        "candidate": {"repairable": True, "changed_files": ["subject.txt"]},
    }
    lineage = None
    if continuation_of is not None:
        failure["continuation_of"] = continuation_of
        lineage = {
            "failed_run_id": continuation_of,
            "root_base_sha": root_base_sha,
            "failed_head_sha": candidate_sha,
            "failure": {"run_id": continuation_of, "task": TASK},
            "task": {"task_id": "TASK-058", "revision": 2},
            "repair": {
                "repair_id": f"REPAIR-{run_id}",
                "failed_run_id": continuation_of,
                "task": TASK,
            },
            "run": run,
        }

    directory = files / run_id
    run_path = directory / "run.json"
    failure_path = directory / "failure.json"
    lineage_path = directory / "repair.json"
    run_bytes = write_json(run_path, run)
    failure_bytes = write_json(failure_path, failure)
    lineage_bytes = write_json(lineage_path, lineage) if lineage is not None else None
    transport_failure(
        repo,
        run_id=run_id,
        head_sha=candidate_sha,
        run_path=run_path,
        failure_path=failure_path,
        lineage_path=lineage_path if lineage is not None else None,
    )
    return run_bytes, failure_bytes, lineage_bytes


def publish_success(
    repo: Path,
    files: Path,
    *,
    run_id: str,
    head_sha: str,
    root_base_sha: str,
    failed_run_id: str | None = None,
) -> None:
    run = {
        "run_id": run_id,
        "task": TASK,
        "executor": "codex",
        "base_sha": head_sha,
        "workspace": "discarded-workspace",
        "head_sha": None,
        "status": "ACTIVE",
    }
    result = {
        "result": {
            "head_sha": head_sha,
            "claims": [],
            "changed_files": [],
            "unresolved": [],
        },
        "evidence": [],
    }
    directory = files / run_id
    run_path = directory / "run.json"
    result_path = directory / "result.json"
    lineage_path = directory / "repair.json"
    write_json(run_path, run)
    write_json(result_path, result)
    if failed_run_id is not None:
        write_json(
            lineage_path,
            {
                "failed_run_id": failed_run_id,
                "root_base_sha": root_base_sha,
                "failed_head_sha": head_sha,
                "failure": {"run_id": failed_run_id, "task": TASK},
                "task": {"task_id": "TASK-058", "revision": 2},
                "repair": {
                    "repair_id": f"REPAIR-{run_id}",
                    "failed_run_id": failed_run_id,
                    "task": TASK,
                },
                "run": run,
            },
        )
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        result_path=result_path,
        lineage_path=lineage_path if failed_run_id is not None else None,
    )


def test_resolves_canonical_failed_correction_chain_with_exact_transported_facts(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    first_head = commit_candidate(repo, "failed primary")
    first = publish_failure(
        repo,
        files,
        run_id="RUN-058-001",
        candidate_sha=first_head,
        root_base_sha=root,
    )
    second_head = commit_candidate(repo, "failed repair one")
    second = publish_failure(
        repo,
        files,
        run_id="RUN-058-002",
        candidate_sha=second_head,
        root_base_sha=root,
        continuation_of="RUN-058-001",
    )
    third_head = commit_candidate(repo, "failed repair two")
    third = publish_failure(
        repo,
        files,
        run_id="RUN-058-003",
        candidate_sha=third_head,
        root_base_sha=root,
        continuation_of="RUN-058-002",
    )

    recovery = resolve_remote_repair_recovery(
        repo, failed_run_id="RUN-058-003"
    )

    assert tuple(item.run_id for item in recovery.failures) == (
        "RUN-058-003",
        "RUN-058-002",
        "RUN-058-001",
    )
    assert tuple(item.candidate_sha for item in recovery.failures) == (
        third_head,
        second_head,
        first_head,
    )
    assert recovery.remote_run_ids == (
        "RUN-058-001",
        "RUN-058-002",
        "RUN-058-003",
    )
    by_run = {item.run_id: item for item in recovery.failures}
    assert (by_run["RUN-058-001"].run, by_run["RUN-058-001"].failure) == first[:2]
    assert (by_run["RUN-058-002"].run, by_run["RUN-058-002"].failure) == second[:2]
    assert (by_run["RUN-058-003"].run, by_run["RUN-058-003"].failure) == third[:2]
    assert by_run["RUN-058-002"].repair == second[2]
    assert by_run["RUN-058-003"].repair == third[2]
    assert git(
        remote,
        "show",
        "refs/heads/aios/failure-artifacts/RUN-058-003:.ai/transport/repair.json",
    ).encode() == third[2]


@pytest.mark.parametrize("corruption", ["candidate-ref", "failure-identity"])
def test_rejects_failed_head_or_failure_identity_mismatch(
    tmp_path: Path, corruption: str
) -> None:
    repo, remote = make_repo(tmp_path)
    root = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, "failed candidate")
    publish_failure(
        repo,
        tmp_path / "facts",
        run_id="RUN-058-004",
        candidate_sha=candidate,
        root_base_sha=root,
        failure_run_id=(
            "RUN-058-999" if corruption == "failure-identity" else None
        ),
    )
    if corruption == "candidate-ref":
        git(
            remote,
            "update-ref",
            "refs/heads/aios/failure/RUN-058-004",
            root,
        )

    message = (
        "failed-head ref mismatch"
        if corruption == "candidate-ref"
        else "FAILURE identity mismatch"
    )
    with pytest.raises(ReviewTransportError, match=message):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-004")


def test_remote_run_namespace_includes_successes_and_rejects_remote_duplicate(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    failed_head = commit_candidate(repo, "failed candidate")
    publish_failure(
        repo,
        files,
        run_id="RUN-058-004",
        candidate_sha=failed_head,
        root_base_sha=root,
    )
    publish_success(
        repo,
        files,
        run_id="RUN-058-008",
        head_sha=failed_head,
        root_base_sha=root,
    )

    recovery = resolve_remote_repair_recovery(
        repo, failed_run_id="RUN-058-004"
    )
    assert recovery.remote_run_ids == ("RUN-058-004", "RUN-058-008")

    publish_success(
        repo,
        files,
        run_id="RUN-058-009",
        head_sha=failed_head,
        root_base_sha=root,
        failed_run_id="RUN-058-004",
    )
    with pytest.raises(
        ReviewTransportError,
        match="canonical continuation already exists for failed RUN: RUN-058-009",
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-004")


def test_reads_exact_historical_task_without_mutating_current_checkout(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    historical_head = git(repo, "rev-parse", "HEAD")
    historical_task = (repo / ".ai" / "tasks" / "TASK-058.yaml").read_bytes()
    (repo / ".ai" / "tasks" / "TASK-058.yaml").write_text(
        "task_id: TASK-058\nrevision: 99\ngoal: current contract\n",
        encoding="utf-8",
    )
    git(repo, "add", ".ai/tasks/TASK-058.yaml")
    git(repo, "commit", "--quiet", "-m", "new current contract")
    git(repo, "push", "--quiet", "origin", "main")
    current_head = git(repo, "rev-parse", "HEAD")

    assert read_remote_task(
        repo, commit_sha=historical_head, task_id="TASK-058"
    ) == historical_task
    assert git(repo, "rev-parse", "HEAD") == current_head
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("task_id", ["../TASK-058", "TASK/058", "TASK\\058"])
def test_historical_task_reader_rejects_noncanonical_identity(
    tmp_path: Path, task_id: str
) -> None:
    repo, _ = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ReviewTransportError, match="invalid TASK id"):
        read_remote_task(repo, commit_sha=head, task_id=task_id)
