from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import aios_renew.publication as publication
from aios_renew.publication import PublicationError, publish_review_decision


RUN_ID = "RUN-063-001"


def git(repo: Path, *args: str, input_bytes: bytes | None = None) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        input=input_bytes,
        capture_output=True,
        check=True,
    )
    return completed.stdout.decode("utf-8").strip()


def blob(repo: Path, content: bytes) -> str:
    return git(repo, "hash-object", "-w", "--stdin", input_bytes=content)


def tree(repo: Path, entries: list[tuple[str, str, str]]) -> str:
    source = "".join(f"{mode} {kind} {sha}\t{name}\n" for mode, kind, sha, name in entries)
    return git(repo, "mktree", input_bytes=source.encode())


def metadata_commit(repo: Path, files: dict[str, bytes], *, parent: str | None = None) -> str:
    hierarchy: dict[str, Any] = {}
    for path, content in files.items():
        cursor = hierarchy
        parts = path.split("/")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = blob(repo, content)

    def make_tree(node: dict[str, Any]) -> str:
        entries = []
        for name, value in sorted(node.items()):
            if isinstance(value, dict):
                entries.append(("040000", "tree", make_tree(value), name))
            else:
                entries.append(("100644", "blob", value, name))
        return tree(repo, entries)

    args = ["commit-tree", make_tree(hierarchy), "-m", "metadata"]
    if parent is not None:
        args.extend(("-p", parent))
    return git(repo, *args)


def valid_review(candidate: str) -> str:
    return f"""review_id: REVIEW-063-001
reviewed_sha: {candidate}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
"""


def valid_run(base: str, *, run_id: str = RUN_ID) -> dict:
    return {
        "run_id": run_id,
        "task": {"id": "TASK-063", "revision": 1},
        "executor": "codex",
        "base_sha": base,
        "workspace": "/workspace",
        "head_sha": None,
        "status": "ACTIVE",
    }


def valid_package(candidate: str, *, run_id: str = RUN_ID) -> dict:
    evidence_id = f"{run_id}-V001"
    return {
        "result": {
            "head_sha": candidate,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "candidate passed Runtime verification",
                    "evidence": [evidence_id],
                }
            ],
            "changed_files": ["product.txt"],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": evidence_id,
                "run_id": run_id,
                "subject_sha": candidate,
                "type": "TEST",
                "source": {"command": "targeted-test"},
                "result": {"exit_code": 0, "summary": "passed"},
                "raw": {"path": ".git/aios/verification/result.log"},
            }
        ],
    }


def arrange(
    tmp_path: Path,
    *,
    review_source: str | None = None,
    run_data: dict | None = None,
    package_data: dict | None = None,
    second_review: bool = False,
    intermediate: bool = False,
) -> dict[str, Path | str]:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "AIOS Test")
    git(repo, "config", "user.email", "aios@example.invalid")
    (repo / "product.txt").write_text("base\n", encoding="utf-8")
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK-063.yaml").write_text(
        """task_id: TASK-063
revision: 1
goal: Publish reviewed source.
problem: Publication needs deterministic gates.
assumptions: []
scope:
  inspect: []
  modify: [product.txt]
non_goals: []
constraints:
  hard: []
acceptance:
  - id: AC1
    condition: Publish the exact reviewed source.
verification:
  required: [targeted-test]
""",
        encoding="utf-8",
    )
    git(repo, "add", "product.txt", ".ai/tasks/TASK-063.yaml")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(tmp_path, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main")

    intermediate_sha = ""
    if intermediate:
        (repo / "product.txt").write_text("intermediate\n", encoding="utf-8")
        git(repo, "commit", "-am", "intermediate")
        intermediate_sha = git(repo, "rev-parse", "HEAD")
    (repo / "product.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "commit", "-am", "candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", f"{candidate}:refs/heads/aios/review/{RUN_ID}")

    run = valid_run(base) if run_data is None else run_data
    package = valid_package(candidate) if package_data is None else package_data
    artifacts = metadata_commit(
        repo,
        {
            ".ai/transport/run.json": json.dumps(run).encode(),
            ".ai/transport/result.json": json.dumps(package).encode(),
        },
    )
    review_files = {
        ".ai/reviews/review.yaml": (
            valid_review(candidate) if review_source is None else review_source
        ).replace("{candidate}", candidate).encode()
    }
    if second_review:
        review_files[".ai/reviews/other.yaml"] = valid_review(candidate).encode()
    decision = metadata_commit(repo, review_files, parent=candidate)
    git(repo, "push", "origin", f"{artifacts}:refs/heads/aios/artifacts/{RUN_ID}")
    git(repo, "push", "origin", f"{decision}:refs/heads/aios/review-decision/{RUN_ID}")
    git(repo, "reset", "--hard", base)
    return {
        "repo": repo,
        "remote": remote,
        "base": base,
        "intermediate": intermediate_sha,
        "candidate": candidate,
        "decision": decision,
    }


def publish(state: dict[str, Path | str]):
    return publish_review_decision(
        state["repo"],
        remote="origin",
        run_id=RUN_ID,
        decision_sha=state["decision"],
    )


def test_pass_event_publishes_exact_source_without_review_commit(tmp_path: Path) -> None:
    state = arrange(tmp_path)

    outcome = publish(state)

    assert outcome.outcome == "PUBLISHED"
    assert outcome.run_id == RUN_ID
    assert outcome.reviewed_sha == state["candidate"]
    assert outcome.prior_main_sha == state["base"]
    assert git(state["remote"], "rev-parse", "main") == state["candidate"]
    assert git(state["remote"], "rev-parse", "main") != state["decision"]
    assert git(
        state["remote"], "ls-tree", "-r", "--name-only", "main", "--", ".ai/reviews"
    ) == ""


@pytest.mark.parametrize(
    "review_source",
    [
        "not: [valid",
        """review_id: REVIEW-063-001
reviewed_sha: {candidate}
mode: PRIMARY
verdict: BLOCKED
acceptance: {AC1: FAIL}
findings: []
""",
        """review_id: REVIEW-063-001
reviewed_sha: {candidate}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {AC1: FAIL}
findings:
  - {id: R1, basis: AC1, action: CODE_FIX, location: product.txt, issue: bad, expected: good}
""",
    ],
)
def test_non_pass_or_malformed_review_never_mutates_main(
    tmp_path: Path, review_source: str
) -> None:
    state = arrange(tmp_path, review_source=review_source)

    with pytest.raises(PublicationError):
        publish(state)

    assert git(state["remote"], "rev-parse", "main") == state["base"]


def test_ambiguous_review_documents_never_mutate_main(tmp_path: Path) -> None:
    state = arrange(tmp_path, second_review=True)
    with pytest.raises(PublicationError, match="exactly one REVIEW"):
        publish(state)
    assert git(state["remote"], "rev-parse", "main") == state["base"]


def test_missing_review_document_never_mutates_main(tmp_path: Path) -> None:
    state = arrange(tmp_path)
    decision = metadata_commit(
        state["repo"], {".ai/notes/decision.txt": b"no review\n"}, parent=state["candidate"]
    )
    decision_ref = f"refs/heads/aios/review-decision/{RUN_ID}"
    git(state["repo"], "push", "origin", f":{decision_ref}")
    git(state["repo"], "push", "origin", f"{decision}:{decision_ref}")
    state["decision"] = decision
    with pytest.raises(PublicationError, match="exactly one REVIEW"):
        publish(state)
    assert git(state["remote"], "rev-parse", "main") == state["base"]


def test_missing_artifact_ref_never_mutates_main(tmp_path: Path) -> None:
    state = arrange(tmp_path)
    git(state["repo"], "push", "origin", f":refs/heads/aios/artifacts/{RUN_ID}")
    with pytest.raises(PublicationError, match="required canonical ref is missing"):
        publish(state)
    assert git(state["remote"], "rev-parse", "main") == state["base"]


def test_run_id_mismatch_never_mutates_main(tmp_path: Path) -> None:
    state = arrange(tmp_path, run_data=valid_run("0" * 40, run_id="RUN-063-999"))
    with pytest.raises(PublicationError, match="RUN-ID mismatch"):
        publish(state)
    assert git(state["remote"], "rev-parse", "main") == state["base"]


def test_missing_successful_evidence_never_mutates_main(tmp_path: Path) -> None:
    state = arrange(tmp_path)
    # Replace the immutable artifact ref with a well-formed RESULT lacking evidence.
    package = valid_package(state["candidate"])
    package["evidence"] = []
    artifacts = metadata_commit(
        state["repo"],
        {
            ".ai/transport/run.json": json.dumps(valid_run(state["base"])).encode(),
            ".ai/transport/result.json": json.dumps(package).encode(),
        },
    )
    artifact_ref = f"refs/heads/aios/artifacts/{RUN_ID}"
    git(state["repo"], "push", "origin", f":{artifact_ref}")
    git(state["repo"], "push", "origin", f"{artifacts}:{artifact_ref}")
    with pytest.raises(PublicationError, match="no verification evidence"):
        publish(state)
    assert git(state["remote"], "rev-parse", "main") == state["base"]


def test_review_result_sha_mismatch_never_mutates_main(tmp_path: Path) -> None:
    state = arrange(tmp_path)
    package = valid_package(state["candidate"])
    package["result"]["head_sha"] = state["base"]
    artifacts = metadata_commit(
        state["repo"],
        {
            ".ai/transport/run.json": json.dumps(valid_run(state["base"])).encode(),
            ".ai/transport/result.json": json.dumps(package).encode(),
        },
    )
    artifact_ref = f"refs/heads/aios/artifacts/{RUN_ID}"
    git(state["repo"], "push", "origin", f":{artifact_ref}")
    git(state["repo"], "push", "origin", f"{artifacts}:{artifact_ref}")
    with pytest.raises(PublicationError, match="REVIEW/RESULT SHA mismatch"):
        publish(state)
    assert git(state["remote"], "rev-parse", "main") == state["base"]


def make_divergent_commit(state: dict[str, Path | str]) -> str:
    repo = state["repo"]
    git(repo, "reset", "--hard", state["base"])
    (repo / "product.txt").write_text("divergent\n", encoding="utf-8")
    git(repo, "commit", "-am", "divergent")
    return git(repo, "rev-parse", "HEAD")


def test_diverged_main_fails_closed(tmp_path: Path) -> None:
    state = arrange(tmp_path)
    divergent = make_divergent_commit(state)
    git(state["repo"], "push", "origin", ":refs/heads/main")
    git(state["repo"], "push", "origin", f"{divergent}:refs/heads/main")
    with pytest.raises(PublicationError, match="diverged"):
        publish(state)
    assert git(state["remote"], "rev-parse", "main") == divergent


def test_compatible_concurrent_main_update_is_rejected_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = arrange(tmp_path, intermediate=True)
    real_git = publication._git
    pushes = 0

    def racing_git(repo: Path, *args: str, **kwargs) -> str:
        nonlocal pushes
        if args and args[0] == "push":
            pushes += 1
            git(state["remote"], "update-ref", "refs/heads/main", state["intermediate"])
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(publication, "_git", racing_git)
    with pytest.raises(PublicationError, match="race-safe fast-forward"):
        publish(state)
    assert pushes == 1
    assert git(state["remote"], "rev-parse", "main") == state["intermediate"]


def test_already_published_is_idempotent_no_op(tmp_path: Path) -> None:
    state = arrange(tmp_path)
    git(state["remote"], "update-ref", "refs/heads/main", state["candidate"])
    before = git(state["remote"], "rev-list", "--count", "main")

    outcome = publish(state)

    assert outcome.outcome == "ALREADY_PUBLISHED"
    assert git(state["remote"], "rev-list", "--count", "main") == before
    assert git(state["remote"], "rev-parse", "main") == state["candidate"]


def test_publication_executes_only_deterministic_git_coordination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = arrange(tmp_path)
    real_run = publication.subprocess.run
    commands: list[tuple[str, ...]] = []

    def recording_run(command, *args, **kwargs):
        commands.append(tuple(command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(publication.subprocess, "run", recording_run)
    publish(state)
    assert commands
    assert all(command[0] == "git" for command in commands)
    push = next(command for command in commands if "push" in command)
    assert "--force" not in push
    assert not any(argument.startswith("+") for argument in push)
    forbidden = ("pytest", "aios run", "executor", "codex", "antigravity", "model", "reviewer")
    assert not any(token in " ".join(command).lower() for command in commands for token in forbidden)


def test_workflow_has_exact_trigger_and_minimum_write_permission() -> None:
    workflow = Path(".github/workflows/aios-auto-publish.yml").read_text(encoding="utf-8")
    assert '"aios/review-decision/**"' in workflow
    assert "contents: write" in workflow
    assert "--decision-sha \"$DECISION_SHA\"" in workflow
    assert "python -m aios_renew.publication" in workflow
    assert "aios run" not in workflow
    assert "pytest" not in workflow
    assert "pull-requests: write" not in workflow
