import json
import subprocess
from pathlib import Path

import pytest

from scripts.antigravity_smoke import (
    PROJECT_ROOT,
    SMOKE_CONTENT,
    AntigravitySmokeFailure,
    prepare_handoff,
    verify_handoff,
)


def git(workspace: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(workspace), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def load_handoff(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def commit_smoke_file(workspace: Path, content: bytes = SMOKE_CONTENT) -> str:
    (workspace / "SMOKE_OK.txt").write_bytes(content)
    git(workspace, "add", "SMOKE_OK.txt")
    git(workspace, "commit", "--quiet", "-m", "create smoke marker")
    return git(workspace, "rev-parse", "HEAD")


def write_result(path: Path, *, run_id: str, head_sha: str) -> None:
    commands = [
        "python -c \"from pathlib import Path; assert "
        "Path('SMOKE_OK.txt').read_bytes() == b'AIOS smoke pass\\n'\"",
        "git rev-parse HEAD",
        "git status --porcelain",
    ]
    path.write_text(
        json.dumps(
            {
                "result": {
                    "head_sha": head_sha,
                    "claims": [],
                    "changed_files": ["SMOKE_OK.txt"],
                    "unresolved": [],
                },
                "evidence": [
                    {
                        "evidence_id": f"E{index}",
                        "run_id": run_id,
                        "subject_sha": head_sha,
                        "type": "TEST",
                        "source": {"command": command},
                        "result": {"exit_code": 0, "summary": "verified"},
                        "raw": {"path": f".ai/evidence/E{index}.log"},
                    }
                    for index, command in enumerate(commands, start=1)
                ],
            }
        ),
        encoding="utf-8",
    )


def prepared_smoke(tmp_path: Path):
    return prepare_handoff(tmp_path / "smoke-repo")


def test_prepare_creates_isolated_real_git_repo_and_baseline(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)

    assert (prepared.workspace / ".git").is_dir()
    assert prepared.base_sha == git(prepared.workspace, "rev-parse", "HEAD")
    assert len(prepared.base_sha) == 40
    assert git(prepared.workspace, "status", "--porcelain") == ""
    assert prepared.handoff_path.parent == prepared.workspace.parent
    assert not prepared.handoff_path.is_relative_to(prepared.workspace)


def test_exported_task_is_executor_neutral_and_compact(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)
    handoff = load_handoff(prepared.handoff_path)

    assert set(handoff) == {"task", "run", "result_package"}
    assert "antigravity" not in json.dumps(handoff["task"]).lower()
    assert handoff["task"]["goal"] == "Create file SMOKE_OK.txt."
    assert handoff["task"]["scope"]["modify"] == ["SMOKE_OK.txt"]
    assert handoff["task"]["verification"]["required"] == [
        "python -c \"from pathlib import Path; assert "
        "Path('SMOKE_OK.txt').read_bytes() == b'AIOS smoke pass\\n'\"",
        "git rev-parse HEAD",
        "git status --porcelain",
    ]


def test_exported_run_selects_antigravity_and_real_baseline(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)
    run = load_handoff(prepared.handoff_path)["run"]

    assert run["executor"] == "antigravity"
    assert run["base_sha"] == prepared.base_sha
    assert Path(run["workspace"]) == prepared.workspace
    assert run["task"] == {"id": "TASK-010-SMOKE", "revision": 1}


def test_canonical_repo_cannot_be_smoke_workspace() -> None:
    with pytest.raises(AntigravitySmokeFailure, match="outside the canonical"):
        prepare_handoff(PROJECT_ROOT)


def test_valid_result_and_repository_state_pass(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)
    head_sha = commit_smoke_file(prepared.workspace)
    write_result(
        prepared.result_path,
        run_id="RUN-010-SMOKE",
        head_sha=head_sha,
    )

    summary = verify_handoff(
        workspace=prepared.workspace,
        result_path=prepared.result_path,
    )

    assert summary.render().startswith("ANTIGRAVITY SMOKE PASS\n")
    assert summary.run_id == "RUN-010-SMOKE"
    assert summary.base_sha == prepared.base_sha
    assert summary.head_sha == head_sha
    assert summary.changed_files == ("SMOKE_OK.txt",)


def test_missing_smoke_file_fails(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)
    (prepared.workspace / "README.md").write_bytes(b"# changed\n")
    git(prepared.workspace, "add", "README.md")
    git(prepared.workspace, "commit", "--quiet", "-m", "advance")
    head_sha = git(prepared.workspace, "rev-parse", "HEAD")
    write_result(prepared.result_path, run_id="RUN-010-SMOKE", head_sha=head_sha)

    with pytest.raises(AntigravitySmokeFailure, match="does not exist"):
        verify_handoff(
            workspace=prepared.workspace,
            result_path=prepared.result_path,
        )


def test_wrong_smoke_bytes_fail(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)
    head_sha = commit_smoke_file(prepared.workspace, b"wrong\n")
    write_result(prepared.result_path, run_id="RUN-010-SMOKE", head_sha=head_sha)

    with pytest.raises(AntigravitySmokeFailure, match="content is not exactly"):
        verify_handoff(
            workspace=prepared.workspace,
            result_path=prepared.result_path,
        )


def test_head_not_advanced_fails(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)
    (prepared.workspace / "SMOKE_OK.txt").write_bytes(SMOKE_CONTENT)
    write_result(
        prepared.result_path,
        run_id="RUN-010-SMOKE",
        head_sha=prepared.base_sha,
    )

    with pytest.raises(AntigravitySmokeFailure, match="did not advance"):
        verify_handoff(
            workspace=prepared.workspace,
            result_path=prepared.result_path,
        )


def test_result_head_mismatch_fails(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)
    commit_smoke_file(prepared.workspace)
    write_result(
        prepared.result_path,
        run_id="RUN-010-SMOKE",
        head_sha="deadbeef",
    )

    with pytest.raises(AntigravitySmokeFailure, match="does not match actual"):
        verify_handoff(
            workspace=prepared.workspace,
            result_path=prepared.result_path,
        )


def test_dirty_worktree_fails(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)
    head_sha = commit_smoke_file(prepared.workspace)
    (prepared.workspace / "DIRTY.txt").write_text("dirty", encoding="utf-8")
    write_result(prepared.result_path, run_id="RUN-010-SMOKE", head_sha=head_sha)

    with pytest.raises(AntigravitySmokeFailure, match="working tree is not clean"):
        verify_handoff(
            workspace=prepared.workspace,
            result_path=prepared.result_path,
        )


def test_invalid_result_package_fails(tmp_path: Path) -> None:
    prepared = prepared_smoke(tmp_path)
    commit_smoke_file(prepared.workspace)
    prepared.result_path.write_text(
        json.dumps({"result": {"claims": []}, "evidence": []}),
        encoding="utf-8",
    )

    with pytest.raises(AntigravitySmokeFailure, match="invalid canonical"):
        verify_handoff(
            workspace=prepared.workspace,
            result_path=prepared.result_path,
        )


def test_harness_has_no_polling_background_or_watcher_behavior() -> None:
    import ast

    source = Path("scripts/antigravity_smoke.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, (ast.While, ast.For)):
            # loops if any must not contain sleep
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and child.attr == "sleep":
                    raise AssertionError("sleep in loop is forbidden")
        if isinstance(node, ast.Attribute) and node.attr == "Popen":
            raise AssertionError("Popen is forbidden")
