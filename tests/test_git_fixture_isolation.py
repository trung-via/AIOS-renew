from pathlib import Path

import pytest

from aios_renew.operator import runtime_state_root
import tests.git_fixture_support as git_fixture_support
from tests.operator_test_support import git, make_repo


def _runtime_files(repo: Path) -> dict[str, bytes]:
    root = runtime_state_root(repo)
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_real_git_sandboxes_isolate_all_writable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutable Git and Runtime state must remain local to each sandbox.

    The contract intentionally makes no assertion about Git object-directory
    identity: immutable objects or baseline material may be reused later.
    """
    repo_a = make_repo(tmp_path / "sandbox-a")

    def fail_if_rebuilt(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("equivalent baseline was rebuilt")

    with monkeypatch.context() as cache_guard:
        cache_guard.setattr(
            git_fixture_support.subprocess,
            "run",
            fail_if_rebuilt,
        )
        repo_b = make_repo(tmp_path / "sandbox-b")
    remote_ref = "refs/heads/sandbox-a-only"
    runtime_before_b = _runtime_files(repo_b)

    assert Path(git(repo_a, "remote", "get-url", "origin")) == (
        tmp_path / "sandbox-a" / "upstream.git"
    )
    assert Path(git(repo_b, "remote", "get-url", "origin")) == (
        tmp_path / "sandbox-b" / "upstream.git"
    )
    assert (repo_a / ".git").resolve() != (repo_b / ".git").resolve()
    assert git(repo_a, "rev-parse", "HEAD") != git(repo_b, "rev-parse", "HEAD")

    (repo_a / "README.md").write_text(
        "# sandbox A worktree mutation\n", encoding="utf-8"
    )
    (repo_a / "indexed-only-in-a.txt").write_text("staged in A\n", encoding="utf-8")
    git(repo_a, "add", "indexed-only-in-a.txt")
    git(repo_a, "branch", "sandbox-a-only")
    git(repo_a, "push", "--quiet", "origin", f"HEAD:{remote_ref}")
    git(repo_a, "config", "--local", "aios.sandbox-marker", "sandbox-a")

    runtime_a = runtime_state_root(repo_a)
    run_state = runtime_a / "runs" / "RUN-ISOLATION-A.json"
    lock_state = runtime_a / "locks" / "executor.lock"
    run_state.parent.mkdir(parents=True)
    lock_state.parent.mkdir(parents=True)
    run_state.write_text('{"sandbox":"a"}\n', encoding="utf-8")
    lock_state.write_text("sandbox-a-lock\n", encoding="utf-8")

    assert git(repo_a, "diff", "--name-only") == "README.md"
    assert git(repo_a, "diff", "--cached", "--name-only") == "indexed-only-in-a.txt"
    assert git(repo_b, "diff", "--name-only") == ""
    assert git(repo_b, "diff", "--cached", "--name-only") == ""

    assert remote_ref in git(
        repo_a, "for-each-ref", "--format=%(refname)", "refs/heads"
    ).splitlines()
    assert remote_ref not in git(
        repo_b, "for-each-ref", "--format=%(refname)", "refs/heads"
    ).splitlines()
    assert git(repo_a, "ls-remote", "--refs", "origin", remote_ref)
    assert git(repo_b, "ls-remote", "--refs", "origin", remote_ref) == ""

    assert git(repo_a, "config", "--local", "--get", "aios.sandbox-marker") == (
        "sandbox-a"
    )
    assert "aios.sandbox-marker=sandbox-a" not in git(
        repo_b, "config", "--local", "--list"
    ).splitlines()

    assert git(repo_a, "status", "--porcelain") != ""
    assert git(repo_b, "status", "--porcelain") == ""
    runtime_after_a = _runtime_files(repo_a)
    assert set(runtime_after_a) == {
        "locks/executor.lock",
        "runs/RUN-ISOLATION-A.json",
    }
    assert runtime_after_a["locks/executor.lock"].strip() == b"sandbox-a-lock"
    assert runtime_after_a["runs/RUN-ISOLATION-A.json"].strip() == b'{"sandbox":"a"}'
    assert _runtime_files(repo_b) == runtime_before_b
