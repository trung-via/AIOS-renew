import json
import subprocess
from pathlib import Path

import pytest

import aios_renew.review_transport as review_transport
from aios_renew.review_transport import (
    ReviewTransportError,
    read_remote_task,
    resolve_remote_performance_snapshot,
    resolve_remote_primary_recovery,
    resolve_remote_repair_recovery,
    resolve_remote_remediation_lineages,
    resolve_remote_run_namespace,
    resolve_remote_task_lifecycle,
    task_run_prefix,
    transport_failure,
    transport_post_pass,
    validate_runtime_failure_binding,
)


TASK = {"id": "TASK-058", "revision": 2}


def test_runtime_failure_binding_rejects_candidate_facts_that_cannot_be_repairable(
) -> None:
    failure = {
        "kind": "FAILURE",
        "run_id": "RUN-058-004",
        "task": TASK,
        "executor": "codex",
        "base_sha": "a" * 40,
        "failed_head_sha": "b" * 40,
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": True,
            "descends_from_base": True,
            "changed_files": ["subject.txt"],
            "outside_task_scope": [],
        },
    }

    with pytest.raises(ValueError, match="repair binding"):
        validate_runtime_failure_binding(
            failure,
            run_id="RUN-058-004",
            task_id="TASK-058",
            task_revision=2,
            executor="codex",
            base_sha="a" * 40,
            candidate_sha="b" * 40,
            modification_scope=("subject.txt",),
            actual_descends_from_base=True,
            actual_changed_files={"subject.txt"},
        )


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


def test_unified_lifecycle_snapshot_is_bounded_and_read_only(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    before_head = git(repo, "rev-parse", "HEAD")
    before_refs = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")

    snapshot = resolve_remote_task_lifecycle(
        repo, task_id="TASK-058", task_revision=2
    )

    assert snapshot.main_sha == before_head
    assert snapshot.terminals == ()
    assert snapshot.reviews == ()
    assert snapshot.remediation_selectors == ()
    assert snapshot.repair_selectors == ()
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs


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
    preverification: bytes | None = None,
) -> tuple[bytes, bytes, bytes | None, bytes | None]:
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
    preverification_path = directory / "pre-verification.json"
    run_bytes = write_json(run_path, run)
    failure_bytes = write_json(failure_path, failure)
    lineage_bytes = write_json(lineage_path, lineage) if lineage is not None else None
    if preverification is not None:
        preverification_path.parent.mkdir(parents=True, exist_ok=True)
        preverification_path.write_bytes(preverification)
    transport_failure(
        repo,
        run_id=run_id,
        head_sha=candidate_sha,
        run_path=run_path,
        failure_path=failure_path,
        lineage_path=lineage_path if lineage is not None else None,
        preverification_path=(
            preverification_path if preverification is not None else None
        ),
    )
    return run_bytes, failure_bytes, lineage_bytes, preverification


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
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

    original_git_cmd = review_transport._git_cmd
    snapshot_calls: list[tuple[str, ...]] = []

    def track_git_cmd(repo: Path, *args: str, **kwargs: object):
        result = original_git_cmd(repo, *args, **kwargs)
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls.append(args)
            code, output, stderr = result
            return code, "\n".join(reversed(output.splitlines())), stderr
        return result

    monkeypatch.setattr(review_transport, "_git_cmd", track_git_cmd)

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
    assert snapshot_calls == [
        (
            "ls-remote",
            "--refs",
            "origin",
            "refs/heads/aios/failure-artifacts/RUN-058-*",
            "refs/heads/aios/artifacts/RUN-058-*",
            "refs/heads/aios/failure/RUN-058-*",
        )
    ]
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


def test_historical_repair_snapshot_failure_has_bounded_operational_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    snapshot_calls = 0
    original_git_cmd = review_transport._git_cmd

    def fail_snapshot(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls += 1
            return 128, "", "fatal: Authentication failed for credential-bearing URL"
        return original_git_cmd(repo, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", fail_snapshot)

    with pytest.raises(
        ReviewTransportError,
        match=(
            r"^historical REPAIR snapshot acquisition failed: "
            r"exit_status=128 category=AUTH$"
        ),
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-001")

    assert snapshot_calls == 1


def test_successful_snapshot_missing_failed_candidate_is_lineage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    root = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, "failed candidate")
    publish_failure(
        repo,
        tmp_path / "facts",
        run_id="RUN-058-001",
        candidate_sha=candidate,
        root_base_sha=root,
    )
    original_git_cmd = review_transport._git_cmd
    snapshot_calls = 0

    def omit_candidate(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        result = original_git_cmd(repo, *args, **kwargs)
        if args[:2] != ("ls-remote", "--refs"):
            return result
        snapshot_calls += 1
        code, output, stderr = result
        output = "\n".join(
            line
            for line in output.splitlines()
            if "refs/heads/aios/failure/RUN-058-001" not in line
        )
        return code, output, stderr

    monkeypatch.setattr(review_transport, "_git_cmd", omit_candidate)

    with pytest.raises(
        ReviewTransportError,
        match=r"^canonical failed RUN refs missing for RUN-058-001$",
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-001")

    assert snapshot_calls == 1


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("not-a-ref-result", "malformed"),
        (
            "a" * 40 + "\trefs/heads/aios/failure/RUN-058-001\n"
            + "b" * 40 + "\trefs/heads/aios/failure/RUN-058-001",
            "ambiguous",
        ),
        ("a" * 40 + "\trefs/heads/aios/review/RUN-058-001", "prefix"),
        ("a" * 40 + "\trefs/heads/aios/failure/RUN-999-001", "identity"),
        ("not-an-object\trefs/heads/aios/failure/RUN-058-001", "identity"),
    ],
)
def test_historical_repair_snapshot_rejects_invalid_ref_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    message: str,
) -> None:
    repo, _ = make_repo(tmp_path)
    snapshot_calls = 0
    original_git_cmd = review_transport._git_cmd

    def invalid_snapshot(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls += 1
            return 0, output, ""
        return original_git_cmd(repo, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", invalid_snapshot)

    with pytest.raises(ReviewTransportError, match=message):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-001")

    assert snapshot_calls == 1


def test_failure_transport_recovers_byte_exact_optional_preverification_candidate(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)
    root = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, "verification failed")
    sidecar = b'{"exact":"pre-verification-candidate","version":1}'

    published = publish_failure(
        repo,
        tmp_path / "facts",
        run_id="RUN-058-010",
        candidate_sha=candidate,
        root_base_sha=root,
        preverification=sidecar,
    )
    recovery = resolve_remote_repair_recovery(
        repo, failed_run_id="RUN-058-010"
    )

    assert published[3] == sidecar
    assert recovery.failures[0].preverification == sidecar
    assert git(
        remote,
        "show",
        "refs/heads/aios/failure-artifacts/RUN-058-010:"
        ".ai/transport/pre-verification-candidate.json",
    ).encode() == sidecar


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


def test_rejects_cyclic_failed_run_continuation_lineage(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    first_head = commit_candidate(repo, "cyclic repair one")
    publish_failure(
        repo,
        files,
        run_id="RUN-058-004",
        candidate_sha=first_head,
        root_base_sha=root,
        continuation_of="RUN-058-005",
    )
    second_head = commit_candidate(repo, "cyclic repair two")
    publish_failure(
        repo,
        files,
        run_id="RUN-058-005",
        candidate_sha=second_head,
        root_base_sha=root,
        continuation_of="RUN-058-004",
    )

    with pytest.raises(
        ReviewTransportError, match="cyclic failed RUN continuation lineage"
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-005")


def test_remote_run_namespace_includes_successes_and_rejects_remote_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
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

    original_git_cmd = review_transport._git_cmd
    snapshot_calls = 0

    def track_git_cmd(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls += 1
        return original_git_cmd(repo, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", track_git_cmd)

    recovery = resolve_remote_repair_recovery(
        repo, failed_run_id="RUN-058-004"
    )
    assert recovery.remote_run_ids == ("RUN-058-004", "RUN-058-008")
    assert snapshot_calls == 1

    publish_success(
        repo,
        files,
        run_id="RUN-058-009",
        head_sha=failed_head,
        root_base_sha=root,
        failed_run_id="RUN-058-004",
    )
    snapshot_calls = 0
    with pytest.raises(
        ReviewTransportError,
        match="canonical continuation already exists for failed RUN: RUN-058-009",
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-004")
    assert snapshot_calls == 1


def test_historical_repair_terminal_conflict_uses_one_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    root = git(repo, "rev-parse", "HEAD")
    failed_head = commit_candidate(repo, "conflicting terminal candidate")
    publish_failure(
        repo,
        tmp_path / "facts",
        run_id="RUN-058-004",
        candidate_sha=failed_head,
        root_base_sha=root,
    )
    publish_success(
        repo,
        tmp_path / "facts",
        run_id="RUN-058-004",
        head_sha=failed_head,
        root_base_sha=root,
    )
    original_git_cmd = review_transport._git_cmd
    snapshot_calls = 0

    def track_git_cmd(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls += 1
        return original_git_cmd(repo, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", track_git_cmd)

    with pytest.raises(
        ReviewTransportError,
        match="canonical RUN has conflicting terminal artifacts: RUN-058-004",
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-004")

    assert snapshot_calls == 1


def test_reads_exact_historical_task_without_mutating_current_checkout(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    historical_head = git(repo, "rev-parse", "HEAD")
    historical_task = subprocess.run(
        (
            "git",
            "-C",
            str(repo),
            "show",
            f"{historical_head}:.ai/tasks/TASK-058.yaml",
        ),
        capture_output=True,
        check=True,
    ).stdout
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


@pytest.mark.parametrize(
    "commit_sha", ["", "HEAD", "--help", "a" * 39, "g" * 40]
)
def test_historical_task_reader_rejects_noncanonical_commit_sha(
    tmp_path: Path, commit_sha: str
) -> None:
    repo, _ = make_repo(tmp_path)

    with pytest.raises(
        ReviewTransportError, match="invalid historical TASK commit SHA"
    ):
        read_remote_task(repo, commit_sha=commit_sha, task_id="TASK-058")


def publish_remediation_artifacts(
    repo: Path,
    files: Path,
    *,
    run_id: str,
    task_id: str = "TASK-058",
    task_revision: int = 2,
    reviewed_sha: str,
    corrupt_run: bool = False,
) -> None:
    run = {
        "run_id": run_id,
        "task": {"id": task_id, "revision": task_revision},
        "executor": "codex",
        "base_sha": reviewed_sha,
        "workspace": "historical-workspace",
        "head_sha": None,
        "status": "ACTIVE",
    }
    result_data = {
        "result": {
            "head_sha": reviewed_sha,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "historical fix",
                    "evidence": ["E1"],
                }
            ],
            "changed_files": ["subject.txt"],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id,
                "subject_sha": reviewed_sha,
                "type": "TEST",
                "source": {"command": "git status --porcelain"},
                "result": {"exit_code": 0, "summary": "verified"},
                "raw": {"path": ".ai/evidence/E1.log"},
            }
        ],
    }
    directory = files / run_id
    run_path = directory / "run.json"
    result_path = directory / "result.json"
    if corrupt_run:
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_bytes(b"not json")
    else:
        write_json(run_path, run)
    write_json(result_path, result_data)
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=reviewed_sha,
        run_path=run_path,
        result_path=result_path,
    )


def publish_remediation_ref(
    repo: Path,
    *,
    source_run_id: str,
    finding_id: str,
    reviewed_sha: str,
    extra_reviews: int = 0,
    extra_remediations: int = 0,
) -> str:
    branch_name = f"branch-{source_run_id}-{finding_id}"
    git(repo, "checkout", "--quiet", "-b", branch_name, reviewed_sha)
    reviews_dir = repo / ".ai" / "reviews"
    remediations_dir = repo / ".ai" / "remediations"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    remediations_dir.mkdir(parents=True, exist_ok=True)
    (reviews_dir / f"REVIEW-{source_run_id}.yaml").write_text(
        f"review_id: REVIEW-{source_run_id}\nreviewed_sha: {reviewed_sha}\nmode: PRIMARY\nverdict: CHANGES_REQUIRED\nacceptance:\n  AC1: FAIL\nfindings:\n  - id: {finding_id}\n    basis: AC1\n    action: CODE_FIX\n    location: subject.txt\n    issue: issue\n    expected: expected\n",
        encoding="utf-8",
    )
    for i in range(extra_reviews):
        (reviews_dir / f"REVIEW-{source_run_id}-extra-{i}.yaml").write_text(
            f"review_id: REVIEW-{source_run_id}-extra-{i}\nreviewed_sha: {reviewed_sha}\nmode: PRIMARY\nverdict: CHANGES_REQUIRED\nacceptance:\n  AC1: FAIL\nfindings:\n  - id: {finding_id}\n    basis: AC1\n    action: CODE_FIX\n    location: subject.txt\n    issue: issue\n    expected: expected\n",
            encoding="utf-8",
        )
    (remediations_dir / f"REMEDIATION-{source_run_id}-{finding_id}.yaml").write_text(
        f"finding_id: {finding_id}\naction: CODE_FIX\nreviewed_sha: {reviewed_sha}\nmodification_scope:\n  - subject.txt\naffected_verification:\n  - git status --porcelain\nconstraints:\n  - hard: [c]\n",
        encoding="utf-8",
    )
    for i in range(extra_remediations):
        (remediations_dir / f"REMEDIATION-{source_run_id}-extra-{i}.yaml").write_text(
            f"finding_id: {finding_id}\naction: CODE_FIX\nreviewed_sha: {reviewed_sha}\nmodification_scope:\n  - subject.txt\naffected_verification:\n  - git status --porcelain\nconstraints:\n  - hard: [c]\n",
            encoding="utf-8",
        )
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", f"remediation {source_run_id}-{finding_id}")
    ref = f"refs/heads/aios/remediation/{source_run_id}-{finding_id}"
    git(repo, "push", "--quiet", "origin", f"HEAD:{ref}")
    git(repo, "checkout", "--quiet", "main")
    git(repo, "branch", "--quiet", "-D", branch_name)
    return ref


def test_task_run_prefix_deterministic_derivation() -> None:
    assert task_run_prefix("TASK-066") == "RUN-066-"
    assert task_run_prefix("TASK-101") == "RUN-101-"
    assert task_run_prefix("066") == "RUN-066-"
    for invalid in ["", "TASK-", "TASK/066", "TASK\\066"]:
        with pytest.raises(ReviewTransportError, match="invalid TASK id"):
            task_run_prefix(invalid)


def test_remote_namespace_reports_exact_terminal_conflict(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, "successful candidate")
    publish_failure(
        repo,
        files,
        run_id="RUN-058-001",
        candidate_sha=root,
        root_base_sha=root,
    )
    publish_success(
        repo,
        files,
        run_id="RUN-058-001",
        head_sha=candidate,
        root_base_sha=root,
    )

    namespace = resolve_remote_run_namespace(
        repo, task_id="TASK-058", task_revision=2
    )
    recovery = resolve_remote_primary_recovery(repo, run_id="RUN-058-001")

    assert namespace.run_ids == ("RUN-058-001",)
    assert namespace.conflicts == ("RUN-058-001",)
    assert recovery.candidate_sha == candidate
    assert recovery.remote_run_ids == ("RUN-058-001",)


def test_remote_namespace_rejects_cross_task_terminal_content(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)
    commit = git(repo, "rev-parse", "HEAD")
    wrong_run = json.dumps(
        {
            "run_id": "RUN-058-009",
            "task": {"id": "TASK-999", "revision": 2},
            "executor": "codex",
            "base_sha": commit,
            "workspace": "historical",
            "head_sha": None,
            "status": "ACTIVE",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    blob = subprocess.run(
        ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
        input=wrong_run,
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    tree_input = f"100644 blob {blob}\trun.json\n".encode()
    tree = subprocess.run(
        ("git", "-C", str(repo), "mktree"),
        input=tree_input,
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    transport_tree = subprocess.run(
        ("git", "-C", str(repo), "mktree"),
        input=f"040000 tree {tree}\ttransport\n".encode(),
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    root_tree = subprocess.run(
        ("git", "-C", str(repo), "mktree"),
        input=f"040000 tree {transport_tree}\t.ai\n".encode(),
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    artifact_commit = git(repo, "commit-tree", root_tree, "-m", "wrong task")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"{artifact_commit}:refs/heads/aios/artifacts/RUN-058-009",
    )

    with pytest.raises(ReviewTransportError, match="TASK identity mismatch"):
        resolve_remote_run_namespace(
            repo, task_id="TASK-058", task_revision=2
        )


def test_cross_task_collision_ignored_during_discovery(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "files"
    sha = git(repo, "rev-parse", "HEAD")

    # Unrelated TASK-041 lineage with structurally incompatible review layout (2 reviews)
    publish_remediation_ref(
        repo,
        source_run_id="RUN-041-002",
        finding_id="F1",
        reviewed_sha=sha,
        extra_reviews=1,
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-041-002",
        task_id="TASK-041",
        task_revision=1,
        reviewed_sha=sha,
    )

    # Valid TASK-066 revision 1 lineage
    publish_remediation_ref(
        repo,
        source_run_id="RUN-066-001",
        finding_id="F1",
        reviewed_sha=sha,
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-001",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )

    lineages = resolve_remote_remediation_lineages(
        repo, finding_id="F1", task_id="TASK-066", task_revision=1
    )
    assert len(lineages) == 1
    assert lineages[0].source_run_id == "RUN-066-001"
    assert lineages[0].ref == "refs/heads/aios/remediation/RUN-066-001-F1"


def test_different_revision_lineage_not_candidate_during_discovery(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "files"
    sha = git(repo, "rev-parse", "HEAD")

    publish_remediation_ref(
        repo, source_run_id="RUN-066-001", finding_id="F1", reviewed_sha=sha
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-001",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )

    publish_remediation_ref(
        repo, source_run_id="RUN-066-002", finding_id="F1", reviewed_sha=sha
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-002",
        task_id="TASK-066",
        task_revision=2,
        reviewed_sha=sha,
    )

    # Resolving for revision 2 ignores revision 1
    lineages_r2 = resolve_remote_remediation_lineages(
        repo, finding_id="F1", task_id="TASK-066", task_revision=2
    )
    assert len(lineages_r2) == 1
    assert lineages_r2[0].source_run_id == "RUN-066-002"

    # Resolving for revision 1 ignores revision 2
    lineages_r1 = resolve_remote_remediation_lineages(
        repo, finding_id="F1", task_id="TASK-066", task_revision=1
    )
    assert len(lineages_r1) == 1
    assert lineages_r1[0].source_run_id == "RUN-066-001"


def test_malformed_lineage_for_exact_task_revision_fails_closed(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)
    files = tmp_path / "files"
    sha = git(repo, "rev-parse", "HEAD")

    # Attributable to requested exact TASK revision, but has 2 reviews
    ref = publish_remediation_ref(
        repo,
        source_run_id="RUN-066-001",
        finding_id="F1",
        reviewed_sha=sha,
        extra_reviews=1,
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-001",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )
    observed_sha = git(repo, "ls-remote", "origin", ref).split()[0]
    with pytest.raises(
        ReviewTransportError,
        match="canonical lineage at .* must contain exactly one REVIEW and REMEDIATION",
    ) as raised:
        resolve_remote_remediation_lineages(
            repo, finding_id="F1", task_id="TASK-066", task_revision=1
        )
    git(remote, "update-ref", ref, sha)

    assert raised.value.observed_refs == ((ref, observed_sha),)
    assert git(repo, "ls-remote", "origin", ref).split()[0] == sha
    assert raised.value.observed_refs[0][1] != sha


def test_missing_artifacts_for_exact_task_revision_fails_closed(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    sha = git(repo, "rev-parse", "HEAD")

    # Attributable to requested exact TASK revision, but artifacts are missing
    publish_remediation_ref(
        repo, source_run_id="RUN-066-001", finding_id="F1", reviewed_sha=sha
    )
    with pytest.raises(
        ReviewTransportError,
        match="canonical source artifacts missing or ambiguous for RUN-066-001",
    ):
        resolve_remote_remediation_lineages(
            repo, finding_id="F1", task_id="TASK-066", task_revision=1
        )


def test_two_valid_lineages_for_exact_task_revision_returned(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "files"
    sha = git(repo, "rev-parse", "HEAD")

    publish_remediation_ref(
        repo, source_run_id="RUN-066-001", finding_id="F1", reviewed_sha=sha
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-001",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )

    publish_remediation_ref(
        repo, source_run_id="RUN-066-003", finding_id="F1", reviewed_sha=sha
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-003",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )

    lineages = resolve_remote_remediation_lineages(
        repo, finding_id="F1", task_id="TASK-066", task_revision=1
    )
    assert len(lineages) == 2


def test_resolve_remote_performance_snapshot_success(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    c1 = commit_candidate(repo, "primary candidate")

    obs = {
        "kind": "RUN_OBSERVATION",
        "run_id": "RUN-058-001",
        "task": {"id": "TASK-058", "revision": 2},
        "operation": "PRIMARY",
        "executor": "codex",
        "base_sha": root,
        "terminal_kind": "RESULT",
        "executor_invoked": True,
        "durations": {
            "admitted_run_seconds": 12.0,
            "executor_seconds": 8.0,
            "verification_seconds": 3.0,
        },
        "token_usage": None,
    }
    dir1 = files / "RUN-058-001"
    dir1.mkdir(parents=True)
    obs_path = dir1 / "observation.json"
    write_json(obs_path, obs)
    run_path = dir1 / "run.json"
    result_path = dir1 / "result.json"
    write_json(
        run_path,
        {
            "run_id": "RUN-058-001",
            "task": {"id": "TASK-058", "revision": 2},
            "executor": "codex",
            "base_sha": root,
            "workspace": "w",
            "status": "ACTIVE",
        },
    )
    write_json(
        result_path,
        {
            "result": {
                "head_sha": c1,
                "claims": [],
                "changed_files": [],
                "unresolved": [],
            },
            "evidence": [],
        },
    )
    transport_post_pass(
        repo,
        run_id="RUN-058-001",
        head_sha=c1,
        run_path=run_path,
        result_path=result_path,
        observation_path=obs_path,
    )

    c2 = commit_candidate(repo, "failed primary candidate")
    publish_failure(
        repo,
        files,
        run_id="RUN-058-002",
        candidate_sha=c2,
        root_base_sha=root,
    )

    snapshot = resolve_remote_performance_snapshot(repo, task_ids=["TASK-058"])
    assert snapshot.task_selectors == ("TASK-058",)
    assert len(snapshot.terminals) == 2
    t1, t2 = snapshot.terminals
    assert t1.run_id == "RUN-058-001"
    assert t1.terminal_kind == "RESULT"
    assert t1.observation is not None
    assert t2.run_id == "RUN-058-002"
    assert t2.terminal_kind == "FAILURE"
    assert t2.observation is None


def test_resolve_remote_performance_snapshot_competing_terminals_fails_closed(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    c1 = commit_candidate(repo, "cand1")

    dir1 = files / "RUN-058-001"
    dir1.mkdir(parents=True)
    run_path = dir1 / "run.json"
    result_path = dir1 / "result.json"
    write_json(
        run_path,
        {
            "run_id": "RUN-058-001",
            "task": {"id": "TASK-058", "revision": 2},
            "executor": "codex",
            "base_sha": root,
            "workspace": "w",
            "status": "ACTIVE",
        },
    )
    write_json(
        result_path,
        {
            "result": {
                "head_sha": c1,
                "claims": [],
                "changed_files": [],
                "unresolved": [],
            },
            "evidence": [],
        },
    )
    transport_post_pass(
        repo,
        run_id="RUN-058-001",
        head_sha=c1,
        run_path=run_path,
        result_path=result_path,
    )

    publish_failure(
        repo,
        files,
        run_id="RUN-058-001",
        candidate_sha=c1,
        root_base_sha=root,
    )

    with pytest.raises(ReviewTransportError, match="competing RESULT/FAILURE"):
        resolve_remote_performance_snapshot(repo, task_ids=["TASK-058"])


def test_resolve_remote_performance_snapshot_selector_validation(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)

    with pytest.raises(ReviewTransportError, match="between 1 and 32"):
        resolve_remote_performance_snapshot(repo, task_ids=[])

    with pytest.raises(ReviewTransportError, match="maximum bound of 32"):
        resolve_remote_performance_snapshot(
            repo, task_ids=[f"TASK-{i:03d}" for i in range(33)]
        )

    with pytest.raises(ReviewTransportError, match="duplicate identities"):
        resolve_remote_performance_snapshot(
            repo, task_ids=["TASK-001", "TASK-001"]
        )

    with pytest.raises(ReviewTransportError, match="malformed TASK selector identity"):
        resolve_remote_performance_snapshot(repo, task_ids=["invalid_task"])


def test_resolve_remote_performance_snapshot_overflow_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, remote = make_repo(tmp_path)
    fake_refs = {
        f"refs/heads/aios/artifacts/RUN-058-{i:03d}": "a" * 40
        for i in range(257)
    }
    monkeypatch.setattr(
        review_transport,
        "_exact_remote_refs",
        lambda *args, **kwargs: fake_refs,
    )
    with pytest.raises(ReviewTransportError, match="exceeds maximum bound of 256"):
        resolve_remote_performance_snapshot(repo, task_ids=["TASK-058"])
