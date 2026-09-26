"""Focused implementation-local checks for bounded review material."""

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from aios_renew.review_material import (
    ReviewMaterialError,
    construct_review_material_package,
    validate_review_material_package,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo).decode().strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.test")
    git(repo, "config", "core.autocrlf", "false")
    return repo


def commit(repo: Path) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "test")
    return git(repo, "rev-parse", "HEAD")


def scope(base: str, latest: str, head: str, mode: str = "PRIMARY") -> dict:
    body = {
        "format": "AIOS_SEMANTIC_REVIEW_SCOPE", "version": 1,
        "kind": "SEMANTIC_REVIEW_SCOPE", "task": {"id": "TASK-185", "revision": 1},
        "reviewed_run_id": "RUN-185-001", "review_mode": mode,
        "semantic_origin_run_id": "RUN-185-001", "semantic_base_sha": base,
        "latest_delta_base_sha": latest, "reviewed_head_sha": head,
        "prior_review_run_id": None, "prior_review_id": None,
        "prior_finding_id": None,
    }
    body["scope_fingerprint"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body


def rejection(code: str, fn, *args, **kwargs):
    with pytest.raises(ReviewMaterialError) as caught:
        fn(*args, **kwargs)
    assert caught.value.reason_code == code


def test_direct_package_uses_exact_sources_and_is_worktree_independent(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "old.txt").write_bytes(b"old\n")
    (repo / "edit.txt").write_bytes(b"before\n")
    base = commit(repo)
    (repo / "old.txt").unlink()
    (repo / "new.txt").write_bytes(b"same\n")
    (repo / "twin.txt").write_bytes(b"same\n")
    (repo / "edit.txt").write_bytes(b"after\n")
    head = commit(repo)
    identity = scope(base, base, head)
    package = construct_review_material_package(identity, repo=repo)
    assert package["latest_delta_view"] is None
    changes = package["semantic_view"]["changes"]
    assert [(item["path"], item["status"]) for item in changes] == [
        ("edit.txt", "MODIFY"), ("new.txt", "ADD"),
        ("old.txt", "DELETE"), ("twin.txt", "ADD")]
    assert changes[1]["review_source_ref"] == changes[3]["review_source_ref"]
    assert len(package["sources"]) == 3
    assert changes[2]["review_source_ref"] == "sha256:" + changes[2]["base_content_sha256"]
    assert validate_review_material_package(json.dumps(package)) == package
    (repo / "edit.txt").write_text("dirty", encoding="utf-8")
    (repo / "untracked").write_text("unrelated", encoding="utf-8")
    git(repo, "config", "diff.renames", "true")
    assert construct_review_material_package(identity, repo=repo) == package


def test_two_views_mode_only_and_newline_material(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "code.txt").write_bytes(b"one\n")
    base = commit(repo)
    (repo / "code.txt").write_bytes(b"two\n")
    latest = commit(repo)
    (repo / "code.txt").write_bytes(b"two")
    (repo / "empty.txt").write_bytes(b"")
    head = commit(repo)
    identity = scope(base, latest, head, "DELTA")
    package = construct_review_material_package(identity, repo=repo)
    assert package["semantic_view"]["base_sha"] == base
    assert package["latest_delta_view"]["base_sha"] == latest
    assert package["latest_delta_view"]["head_sha"] == head
    assert "\\ No newline at end of file" in package["latest_delta_view"]["changes"][0]["unified_diff"]
    assert any(source["text"] == "" for source in package["sources"])
    assert validate_review_material_package(package) == package
    # Git's index can represent an executable bit on all supported platforms.
    git(repo, "update-index", "--chmod=+x", "code.txt")
    git(repo, "commit", "-qm", "mode")
    mode_head = git(repo, "rev-parse", "HEAD")
    mode_package = construct_review_material_package(scope(head, head, mode_head), repo=repo)
    change = mode_package["semantic_view"]["changes"][0]
    assert change["status"] == "MODIFY"
    assert change["base_mode"] == "100644" and change["head_mode"] == "100755"
    assert change["base_content_sha256"] == change["head_content_sha256"]
    assert change["unified_diff"] == ""


def test_rename_is_delete_plus_add_and_changed_symlink_fails(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "before").write_bytes(b"same\n")
    base = commit(repo)
    git(repo, "mv", "before", "after")
    head = commit(repo)
    changes = construct_review_material_package(scope(base, base, head), repo=repo)[
        "semantic_view"]["changes"]
    assert [(change["path"], change["status"]) for change in changes] == [
        ("after", "ADD"), ("before", "DELETE")]
    assert changes[0]["review_source_ref"] == changes[1]["review_source_ref"]
    oid = subprocess.check_output(["git", "hash-object", "-w", "--stdin"],
                                  input=b"after", cwd=repo).decode().strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{oid},link")
    git(repo, "commit", "-qm", "symlink")
    link_head = git(repo, "rev-parse", "HEAD")
    rejection("UNSUPPORTED_MATERIAL", construct_review_material_package,
              scope(head, head, link_head), repo=repo)


def test_revalidator_and_constructor_fail_closed(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "one").write_bytes(b"a\n")
    base = commit(repo)
    (repo / "one").write_bytes(b"b\n")
    head = commit(repo)
    identity = scope(base, base, head)
    package = construct_review_material_package(identity, repo=repo)

    bad = copy.deepcopy(identity)
    bad["semantic_base_sha"] = head
    rejection("REVIEW_SCOPE_INVALID", construct_review_material_package, bad, repo=repo)
    missing = scope("0" * 40, "0" * 40, head)
    rejection("GIT_MATERIAL_UNAVAILABLE", construct_review_material_package, missing, repo=repo)
    for mutate in (
        lambda p: p.update(extra=True),
        lambda p: p["sources"].append({**p["sources"][0]}),
        lambda p: p["semantic_view"]["changes"][0].update(path="../one"),
        lambda p: p["semantic_view"]["changes"][0].update(head_mode="120000"),
        lambda p: p["sources"][0].update(text="tampered"),
        lambda p: p.update(latest_delta_view=p["semantic_view"]),
    ):
        bad = copy.deepcopy(package)
        mutate(bad)
        with pytest.raises(ReviewMaterialError):
            validate_review_material_package(bad)
    rejection("MATERIAL_INCONSISTENT", validate_review_material_package,
              '{"format":1,"format":2}')
    (repo / "one").write_bytes(b"\xff")
    binary_head = commit(repo)
    rejection("UNSUPPORTED_MATERIAL", construct_review_material_package,
              scope(head, head, binary_head), repo=repo)
    (repo / "one").write_bytes(b"x" * 262145)
    large_head = commit(repo)
    rejection("MATERIAL_BOUND_EXCEEDED", construct_review_material_package,
              scope(head, head, large_head), repo=repo)
