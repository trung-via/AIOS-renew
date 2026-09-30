import hashlib
import os
from pathlib import Path
import tempfile

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


def test_fast_materialization_supports_real_git_and_immutable_object_reuse(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "sandbox")

    baseline = git(repo, "rev-parse", "HEAD")
    assert git(repo, "cat-file", "-t", baseline) == "commit"
    assert git(repo, "show", f"{baseline}:README.md") == "# operator test"

    (repo / "ordinary-commit.txt").write_text(
        "committed by real Git\n", encoding="utf-8"
    )
    git(repo, "add", "ordinary-commit.txt")
    git(repo, "commit", "--quiet", "-m", "ordinary real Git commit")
    committed = git(repo, "rev-parse", "HEAD")
    assert committed != baseline
    assert git(repo, "show", f"{committed}:ordinary-commit.txt") == (
        "committed by real Git"
    )

    source = tmp_path / "source-objects"
    destination = tmp_path / "destination-objects"
    object_id = git_fixture_support._write_object(source, "blob", b"immutable\n")
    source_path = source / object_id[:2] / object_id[2:]
    fixed_time = 946684800_000_000_000
    os.utime(source_path, ns=(fixed_time, fixed_time))

    assert (
        git_fixture_support._write_object(source, "blob", b"immutable\n")
        == object_id
    )
    assert source_path.stat().st_mtime_ns == fixed_time

    git_fixture_support._copy_loose_objects(source, destination)
    destination_path = destination / object_id[:2] / object_id[2:]
    os.utime(destination_path, ns=(fixed_time, fixed_time))
    git_fixture_support._copy_loose_objects(source, destination)
    assert destination_path.stat().st_mtime_ns == fixed_time

    destination_path.write_bytes(b"not a Git object")
    with pytest.raises(ValueError, match="corrupt Git object"):
        git_fixture_support._copy_loose_objects(source, destination)
    assert destination_path.read_bytes() == b"not a Git object"


def test_packed_fixture_objects_remain_readable_and_committable(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "packed-sandbox")
    packed_file = repo / "nested" / "packed.txt"
    packed_file.parent.mkdir()
    packed_file.write_text("packed content\n", encoding="utf-8")
    git(repo, "add", "nested/packed.txt")
    git(repo, "commit", "--quiet", "-m", "state to pack")
    packed_head = git(repo, "rev-parse", "HEAD")
    packed_tree = git(repo, "rev-parse", "HEAD^{tree}")
    packed_blob = git(repo, "rev-parse", "HEAD:nested/packed.txt")

    git(repo, "repack", "-a", "-d", "--quiet")
    objects = repo / ".git" / "objects"
    assert list((objects / "pack").glob("*.pack"))
    for object_id in (packed_head, packed_tree, packed_blob):
        assert not (objects / object_id[:2] / object_id[2:]).exists()
    assert git_fixture_support._read_object(repo, packed_blob) == (
        "blob", b"packed content\n"
    )
    assert git_fixture_support._head_files(repo)[0] == packed_head
    assert git_fixture_support._head_files(repo)[1]["nested/packed.txt"] == (
        b"packed content\n"
    )

    packed_file.write_text("updated content\n", encoding="utf-8")
    new_head = git_fixture_support.commit_fixture_state(
        repo,
        paths=("nested/packed.txt",),
        message="state after packing",
        user_name="AIOS Operator Test",
        user_email="operator@example.invalid",
    )
    assert git(repo, "rev-parse", "HEAD") == new_head
    assert git(repo, "rev-parse", "HEAD^") == packed_head
    assert git(repo, "show", "HEAD:nested/packed.txt") == "updated content"
    assert git(repo, "status", "--porcelain") == ""


def test_fixture_object_inspection_rejects_missing_corrupt_and_wrong_type(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "invalid-sandbox")
    with pytest.raises(ValueError, match="^missing Git object: 0{40}$"):
        git_fixture_support._read_object(repo, "0" * 40)

    objects = repo / ".git" / "objects"
    corrupt_id = git_fixture_support._write_object(objects, "blob", b"corrupt me")
    (objects / corrupt_id[:2] / corrupt_id[2:]).write_bytes(b"not a Git object")
    with pytest.raises(ValueError, match=f"^corrupt Git object: {corrupt_id}$"):
        git_fixture_support._read_object(repo, corrupt_id)

    blob_id = git_fixture_support._write_object(objects, "blob", b"not a commit")
    (repo / ".git" / "refs" / "heads" / "main").write_text(
        f"{blob_id}\n", encoding="ascii"
    )
    with pytest.raises(ValueError, match=f"^fixture HEAD is not a commit: {blob_id}$"):
        git_fixture_support._head_files(repo)

    wrong_tree_id = git_fixture_support._write_object(
        objects, "commit", f"tree {blob_id}\n\nwrong tree\n".encode("ascii")
    )
    (repo / ".git" / "refs" / "heads" / "main").write_text(
        f"{wrong_tree_id}\n", encoding="ascii"
    )
    with pytest.raises(ValueError, match="^expected tree object, got blob$"):
        git_fixture_support._head_files(repo)

    tree_id = git_fixture_support._write_object(
        objects, "tree", b"100644 wrong-blob\0" + bytes.fromhex(wrong_tree_id)
    )
    wrong_blob_id = git_fixture_support._write_object(
        objects, "commit", f"tree {tree_id}\n\nwrong blob\n".encode("ascii")
    )
    (repo / ".git" / "refs" / "heads" / "main").write_text(
        f"{wrong_blob_id}\n", encoding="ascii"
    )
    with pytest.raises(ValueError, match="^expected blob object, got commit$"):
        git_fixture_support._head_files(repo)


def test_depth_amplified_sandboxes_push_and_resolve_full_aios_refs() -> None:
    # Own the physical storage independently of pytest/xdist wrapper depth.
    # Root 224 retains long-lock-path coverage for both canonical ref families.
    remote_root_length = 224
    with tempfile.TemporaryDirectory(prefix="aios-ref-") as directory:
        storage = Path(directory).resolve(strict=True)
        nested = storage / "runtime-isolation" / "bp-v4-diagnostic" / "nested-"
        padding = remote_root_length - len(
            str(nested / "sandbox-a" / "upstream.git")
        )
        assert padding >= 0, "temporary storage exceeds the bare-remote root budget"
        depth = nested.with_name(nested.name + "x" * padding)
        sandboxes = [
            make_repo(depth / "sandbox-a"),
            make_repo(depth / "sandbox-b"),
        ]
        families = ("integration", "admission-failure")

        for index, repo in enumerate(sandboxes):
            remote = (repo.parent / "upstream.git").resolve(strict=True)
            assert len(str(remote)) == remote_root_length
            other = sandboxes[1 - index]
            head = git(repo, "rev-parse", "HEAD")
            for family in families:
                identity = hashlib.sha256(f"{family}-{index}".encode()).hexdigest()
                ref = f"refs/heads/aios/{family}/{identity}"
                assert len(identity) == 64
                assert len(str(remote / f"{ref}.lock")) > 260

                git(repo, "update-ref", ref, head)
                git(repo, "push", "--quiet", "origin", f"{ref}:{ref}")
                assert git(remote, "show-ref", "--verify", ref) == f"{head} {ref}"
                assert git(repo, "ls-remote", "--refs", "origin", ref) == (
                    f"{head}\t{ref}"
                )
                assert git(other, "ls-remote", "--refs", "origin", ref) == ""
