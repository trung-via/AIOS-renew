"""Fast, process-local materialization of real Git test sandboxes.

The cached directories are only immutable construction templates.  Every caller
receives ordinary, independently writable repository and bare-remote trees.
"""

from __future__ import annotations

import atexit
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Lock
from typing import Mapping


_cache_root = Path(tempfile.mkdtemp(prefix="aios-git-fixtures-"))
atexit.register(shutil.rmtree, _cache_root, ignore_errors=True)
_cache_lock = Lock()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _key(
    files: Mapping[str, str | bytes],
    *,
    user_name: str,
    user_email: str,
    commit_message: str,
    remote_head_main: bool,
) -> str:
    digest = hashlib.sha256()
    settings = json.dumps(
        [user_name, user_email, commit_message, remote_head_main],
        separators=(",", ":"),
    ).encode()
    digest.update(settings)
    for name, value in sorted(files.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("utf-8") if isinstance(value, str) else value)
        digest.update(b"\0")
    return digest.hexdigest()


def _build_template(
    template: Path,
    files: Mapping[str, str | bytes],
    *,
    user_name: str,
    user_email: str,
    commit_message: str,
    remote_head_main: bool,
) -> None:
    repo = template / "repo"
    remote = template / "upstream.git"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", user_name)
    _git(repo, "config", "user.email", user_email)
    _git(repo, "branch", "-M", "main")
    for name, value in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", commit_message)
    subprocess.run(("git", "init", "--bare", "--quiet", str(remote)), check=True)
    # A relative URL remains correct after the complete template is copied.
    _git(repo, "remote", "add", "origin", "../upstream.git")
    _git(repo, "push", "--quiet", "--set-upstream", "origin", "main")
    if remote_head_main:
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")


def materialize_git_baseline(
    root: Path,
    *,
    files: Mapping[str, str | bytes],
    user_name: str,
    user_email: str,
    commit_message: str,
    remote_head_main: bool = False,
) -> tuple[Path, Path, str]:
    """Copy an equivalent real-Git baseline into an isolated sandbox.

    No alternates, linked worktrees, hardlinks, shared refs, or shared config are
    used.  The process-local template only avoids repeating Git initialization,
    object creation, and the initial push for equivalent baselines.
    """

    cache_key = _key(
        files,
        user_name=user_name,
        user_email=user_email,
        commit_message=commit_message,
        remote_head_main=remote_head_main,
    )
    template = _cache_root / cache_key
    with _cache_lock:
        if not template.exists():
            _build_template(
                template,
                files,
                user_name=user_name,
                user_email=user_email,
                commit_message=commit_message,
                remote_head_main=remote_head_main,
            )

    root.mkdir(parents=True, exist_ok=True)
    repo = root / "repo"
    remote = root / "upstream.git"
    shutil.copytree(template / "repo", repo)
    shutil.copytree(template / "upstream.git", remote)
    config_path = repo / ".git" / "config"
    config = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config.replace("url = ../upstream.git", f"url = {remote.as_posix()}"),
        encoding="utf-8",
    )
    head_sha = (repo / ".git" / "refs" / "heads" / "main").read_text(
        encoding="ascii"
    ).strip()
    return repo, remote, head_sha
