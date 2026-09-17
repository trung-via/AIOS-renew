"""Tests for AIOS runtime identity and self-host source drift guard."""

import re
import subprocess
from pathlib import Path

import pytest

import aios_renew
from aios_renew.runtime_identity import (
    RuntimeDriftReport,
    RuntimeIdentity,
    RuntimeIdentityError,
    evaluate_runtime_drift,
    guard_runtime_drift,
    is_aios_self_host_repo,
    normalize_remote_url,
    resolve_repository_identities,
    resolve_runtime_identity,
    resolve_tree_fingerprint,
)


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "-C", str(path), "init", "--quiet"), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.name", "AIOS Test"), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.email", "test@example.invalid"), check=True)
    subprocess.run(("git", "-C", str(path), "branch", "-M", "main"), check=True)
    return path


def _commit_file(repo: Path, rel_path: str, content: str, message: str = "commit") -> str:
    file_path = repo / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    subprocess.run(("git", "-C", str(repo), "add", rel_path), check=True)
    subprocess.run(("git", "-C", str(repo), "commit", "--quiet", "-m", message), check=True)
    return subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_normalize_remote_url_formats() -> None:
    """AC1: normalize_remote_url normalizes ordinary local Git remote forms platform-neutrally."""

    # HTTPS with .git and without
    assert normalize_remote_url("https://github.com/trung-via/AIOS-renew.git") == "github.com/trung-via/aios-renew"
    assert normalize_remote_url("https://github.com/trung-via/AIOS-renew") == "github.com/trung-via/aios-renew"
    assert normalize_remote_url("https://github.com/trung-via/aios-renew.git/") == "github.com/trung-via/aios-renew"

    # HTTPS with token/auth and port
    assert normalize_remote_url("https://token:x-oauth-basic@github.com/trung-via/AIOS-renew.git") == "github.com/trung-via/aios-renew"

    # HTTP
    assert normalize_remote_url("http://github.com/trung-via/AIOS-renew.git") == "github.com/trung-via/aios-renew"

    # SCP-style SSH
    assert normalize_remote_url("git@github.com:trung-via/AIOS-renew.git") == "github.com/trung-via/aios-renew"
    assert normalize_remote_url("git@github.com:trung-via/AIOS-renew") == "github.com/trung-via/aios-renew"

    # URL-style SSH
    assert normalize_remote_url("ssh://git@github.com/trung-via/AIOS-renew.git") == "github.com/trung-via/aios-renew"
    assert normalize_remote_url("ssh://git@github.com:22/trung-via/AIOS-renew.git") == "github.com/trung-via/aios-renew"

    # Git protocol
    assert normalize_remote_url("git://github.com/trung-via/AIOS-renew.git") == "github.com/trung-via/aios-renew"

    # Case insensitivity
    assert normalize_remote_url("GIT@GITHUB.COM:Trung-Via/AIOS-Renew.GIT") == "github.com/trung-via/aios-renew"

    # Empty
    assert normalize_remote_url("") == ""


def test_resolve_runtime_identity_current_package() -> None:
    """AC1: Dedicated runtime-identity helper resolves the actually imported AIOS package's Git checkout and tree fingerprint."""

    identity = resolve_runtime_identity(aios_renew)

    assert isinstance(identity, RuntimeIdentity)
    assert identity.checkout_root.is_dir()
    assert (identity.checkout_root / ".git").exists()
    assert identity.package_path.is_dir()
    assert identity.package_path.name == "aios_renew"

    # Deterministic 40-character hex tree fingerprint
    assert re.fullmatch(r"^[0-9a-f]{40}$", identity.tree_fingerprint)

    # Repository identities include normalized github repo and package identity
    assert any("aios-renew" in ident for ident in identity.repository_identities)


def test_identical_tree_fingerprints_admitted_with_commit_drift(tmp_path: Path) -> None:
    """AC2: Two checkouts recognized as the same repository identity with identical src/aios_renew tree fingerprints are admitted even when HEAD SHAs differ."""

    upstream = _init_git_repo(tmp_path / "upstream")
    _commit_file(upstream, "src/aios_renew/__init__.py", '"""kernel"""\n')
    _commit_file(upstream, "pyproject.toml", '[project]\nname = "aios-renew"\n')

    # Checkout 1 (simulating loaded runtime checkout)
    runtime_repo = tmp_path / "runtime_checkout"
    subprocess.run(("git", "clone", "--quiet", str(upstream), str(runtime_repo)), check=True)

    # Checkout 2 (simulating subject repo where TASK/docs were added)
    subject_repo = tmp_path / "subject_checkout"
    subprocess.run(("git", "clone", "--quiet", str(upstream), str(subject_repo)), check=True)

    # Advance subject repo with TASK and docs commits only (no changes to src/aios_renew)
    subject_head_1 = _commit_file(subject_repo, "docs/README.md", "# Docs update\n", "docs: update")
    subject_head_2 = _commit_file(subject_repo, ".ai/tasks/TASK-133.yaml", "task_id: TASK-133\n", "task: add TASK-133")

    # Runtime checkout HEAD and subject checkout HEAD differ!
    runtime_head = subprocess.run(
        ("git", "-C", str(runtime_repo), "rev-parse", "HEAD"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert runtime_head != subject_head_2

    # But their src/aios_renew tree fingerprints are identical!
    runtime_tree = resolve_tree_fingerprint(runtime_repo, "src/aios_renew")
    subject_tree = resolve_tree_fingerprint(subject_repo, "src/aios_renew")
    assert runtime_tree == subject_tree

    # Evaluate drift
    report = evaluate_runtime_drift(
        subject_repo,
        runtime_package=runtime_repo / "src" / "aios_renew",
    )
    assert report.disposition == "MATCH"
    assert report.subject_fingerprint == runtime_tree
    assert report.runtime_fingerprint == runtime_tree

    # guard_runtime_drift returns report and does not raise
    guarded = guard_runtime_drift(
        subject_repo,
        runtime_package=runtime_repo / "src" / "aios_renew",
    )
    assert guarded.disposition == "MATCH"


def test_different_tree_fingerprints_fail_closed_with_diagnostic(tmp_path: Path) -> None:
    """AC3: Two checkouts recognized as the same repository identity with different src/aios_renew fingerprints fail closed before dispatch with both roots and fingerprints in diagnostic."""

    upstream = _init_git_repo(tmp_path / "upstream")
    _commit_file(upstream, "src/aios_renew/__init__.py", '"""kernel v1"""\n')
    _commit_file(upstream, "pyproject.toml", '[project]\nname = "aios-renew"\n')

    # Stale runtime checkout
    runtime_repo = tmp_path / "runtime_checkout"
    subprocess.run(("git", "clone", "--quiet", str(upstream), str(runtime_repo)), check=True)

    # Subject checkout gets new code in src/aios_renew
    subject_repo = tmp_path / "subject_checkout"
    subprocess.run(("git", "clone", "--quiet", str(upstream), str(subject_repo)), check=True)
    _commit_file(subject_repo, "src/aios_renew/__init__.py", '"""kernel v2 with launcher repair"""\n', "feat: repair launcher")

    runtime_tree = resolve_tree_fingerprint(runtime_repo, "src/aios_renew")
    subject_tree = resolve_tree_fingerprint(subject_repo, "src/aios_renew")
    assert runtime_tree != subject_tree

    report = evaluate_runtime_drift(
        subject_repo,
        runtime_package=runtime_repo / "src" / "aios_renew",
    )
    assert report.disposition == "DRIFT_DETECTED"
    assert report.diagnostic is not None

    # Verify both roots and both fingerprints are present in bounded diagnostic
    diagnostic = report.diagnostic
    assert "runtime source drift detected" in diagnostic
    assert str(subject_repo) in diagnostic
    assert str(runtime_repo) in diagnostic
    assert subject_tree in diagnostic
    assert runtime_tree in diagnostic

    # guard_runtime_drift raises RuntimeIdentityError with that exact diagnostic
    with pytest.raises(RuntimeIdentityError) as exc_info:
        guard_runtime_drift(
            subject_repo,
            runtime_package=runtime_repo / "src" / "aios_renew",
        )
    assert str(exc_info.value) == diagnostic


def test_downstream_repository_is_not_applicable(tmp_path: Path) -> None:
    """AC4: A downstream subject repository with a different normalized repository identity is treated as NOT_APPLICABLE and existing admission remains unchanged."""

    # Downstream app repo
    app_repo = _init_git_repo(tmp_path / "downstream_app")
    _commit_file(app_repo, "app.py", 'print("hello world")\n')
    _commit_file(app_repo, "pyproject.toml", '[project]\nname = "my-web-app"\n')
    subprocess.run(
        ("git", "-C", str(app_repo), "remote", "add", "origin", "https://github.com/customer/my-web-app.git"),
        check=True,
    )

    # Self-host AIOS runtime checkout
    aios_upstream = _init_git_repo(tmp_path / "aios_upstream")
    _commit_file(aios_upstream, "src/aios_renew/__init__.py", '"""kernel"""\n')
    _commit_file(aios_upstream, "pyproject.toml", '[project]\nname = "aios-renew"\n')
    runtime_repo = tmp_path / "aios_runtime"
    subprocess.run(("git", "clone", "--quiet", str(aios_upstream), str(runtime_repo)), check=True)
    subprocess.run(
        ("git", "-C", str(runtime_repo), "remote", "set-url", "origin", "https://github.com/trung-via/AIOS-renew.git"),
        check=True,
    )

    report = evaluate_runtime_drift(
        app_repo,
        runtime_package=runtime_repo / "src" / "aios_renew",
    )
    assert report.disposition == "NOT_APPLICABLE"

    # Guard succeeds cleanly without error
    guarded = guard_runtime_drift(
        app_repo,
        runtime_package=runtime_repo / "src" / "aios_renew",
    )
    assert guarded.disposition == "NOT_APPLICABLE"


def test_applicable_self_host_without_runtime_git_identity_fails_closed(tmp_path: Path) -> None:
    """AC5: An applicable AIOS self-host case whose loaded runtime source cannot establish the required Git/tree identity fails closed rather than silently proceeding."""

    # Subject is AIOS self-host repository
    subject_repo = _init_git_repo(tmp_path / "self_host_subject")
    _commit_file(subject_repo, "src/aios_renew/__init__.py", '"""kernel"""\n')
    _commit_file(subject_repo, "pyproject.toml", '[project]\nname = "aios-renew"\n')

    # Runtime package is located outside of a Git repo (e.g. site-packages from wheel)
    non_git_package = tmp_path / "site-packages" / "aios_renew"
    non_git_package.mkdir(parents=True, exist_ok=True)
    (non_git_package / "__init__.py").write_text('"""installed wheel without git"""\n', encoding="utf-8")

    report = evaluate_runtime_drift(
        subject_repo,
        runtime_package=non_git_package,
    )
    assert report.disposition == "IDENTITY_UNAVAILABLE"
    assert report.diagnostic is not None
    assert "runtime source identity unavailable" in report.diagnostic
    assert str(subject_repo) in report.diagnostic

    with pytest.raises(RuntimeIdentityError) as exc_info:
        guard_runtime_drift(
            subject_repo,
            runtime_package=non_git_package,
        )
    assert "runtime source identity unavailable" in str(exc_info.value)


def test_worktrees_recognized_as_same_repository(tmp_path: Path) -> None:
    """AC1/AC2: Git worktrees sharing git-common-dir are recognized as the same repository identity."""

    main_repo = _init_git_repo(tmp_path / "main_repo")
    _commit_file(main_repo, "src/aios_renew/__init__.py", '"""kernel"""\n')
    _commit_file(main_repo, "pyproject.toml", '[project]\nname = "aios-renew"\n')

    worktree_path = tmp_path / "linked_worktree"
    subprocess.run(
        ("git", "-C", str(main_repo), "worktree", "add", "-b", "feature", str(worktree_path)),
        check=True,
    )

    identities_main = resolve_repository_identities(main_repo)
    identities_worktree = resolve_repository_identities(worktree_path)

    # They share the common-dir identity
    common_identities = identities_main & identities_worktree
    assert any(ident.startswith("common-dir:") for ident in common_identities)

    # Identical trees match
    report = evaluate_runtime_drift(
        worktree_path,
        runtime_package=main_repo / "src" / "aios_renew",
    )
    assert report.disposition == "MATCH"
