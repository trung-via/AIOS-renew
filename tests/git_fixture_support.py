"""Fast, process-local materialization of real Git test sandboxes.

The cached directories are only immutable construction templates.  Every caller
receives ordinary, independently writable repository and bare-remote trees.
"""

from __future__ import annotations

import atexit
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Lock
from typing import Mapping
import zlib


_cache_root = Path(tempfile.mkdtemp(prefix="aios-git-fixtures-"))
atexit.register(shutil.rmtree, _cache_root, ignore_errors=True)
_cache_lock = Lock()
_instance_counter = itertools.count()


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
    (template / "tree").write_text(
        _git(repo, "rev-parse", "HEAD^{tree}"), encoding="ascii"
    )


def _materialize_layout(
    *,
    template: Path,
    repo: Path,
    remote: Path,
    files: Mapping[str, str | bytes],
) -> None:
    """Create only the writable layout required by an ordinary real Git repo."""

    git_dir = repo / ".git"
    for directory in (
        git_dir / "objects",
        git_dir / "refs" / "heads",
        git_dir / "refs" / "remotes" / "origin",
        git_dir / "refs" / "tags",
        remote / "objects",
        remote / "refs" / "heads",
        remote / "refs" / "tags",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    for name, value in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")

    template_repo_git = template / "repo" / ".git"
    shutil.copyfile(template_repo_git / "HEAD", git_dir / "HEAD")
    shutil.copyfile(template_repo_git / "index", git_dir / "index")
    shutil.copyfile(template_repo_git / "config", git_dir / "config")
    shutil.copyfile(template / "upstream.git" / "HEAD", remote / "HEAD")
    shutil.copyfile(template / "upstream.git" / "config", remote / "config")


def _write_alternate(objects: Path, template_objects: Path) -> None:
    info = objects / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "alternates").write_bytes(
        f"{template_objects.resolve().as_posix()}\n".encode("utf-8")
    )


def _write_object(objects: Path, kind: str, body: bytes) -> str:
    payload = f"{kind} {len(body)}\0".encode("ascii") + body
    object_id = hashlib.sha1(payload).hexdigest()
    path = objects / object_id[:2] / object_id[2:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zlib.compress(payload))
    return object_id


def _materialize_root_commit(
    *,
    repo: Path,
    remote: Path,
    tree: str,
    user_name: str,
    user_email: str,
    commit_message: str,
    instance: int,
) -> str:
    """Create a cheap, repository-specific root over cached immutable content."""

    timestamp = 946684800 + instance
    identity = f"{user_name} <{user_email}> {timestamp} +0000"
    body = (
        f"tree {tree}\n"
        f"author {identity}\n"
        f"committer {identity}\n"
        f"\n{commit_message}\n"
    ).encode("utf-8")
    head_sha = _write_object(repo / ".git" / "objects", "commit", body)
    _write_object(remote / "objects", "commit", body)

    ref_paths = (
        repo / ".git" / "refs" / "heads" / "main",
        repo / ".git" / "refs" / "remotes" / "origin" / "main",
        remote / "refs" / "heads" / "main",
    )
    for ref_path in ref_paths:
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(f"{head_sha}\n", encoding="ascii")

    # Reflogs are writable per-sandbox state too.  Keep their initial entry
    # internally consistent without invoking another Git process.
    for logs_root in (repo / ".git" / "logs", remote / "logs"):
        if not logs_root.exists():
            continue
        for log_path in logs_root.rglob("*"):
            if log_path.is_file():
                lines = log_path.read_text(encoding="utf-8").splitlines()
                rewritten = []
                for line in lines:
                    fields = line.split(" ", 2)
                    if len(fields) == 3:
                        fields[1] = head_sha
                        line = " ".join(fields)
                    rewritten.append(line)
                log_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return head_sha


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
        instance = next(_instance_counter)

    root.mkdir(parents=True, exist_ok=True)
    repo = root / "repo"
    remote = root / "upstream.git"
    _materialize_layout(
        template=template,
        repo=repo,
        remote=remote,
        files=files,
    )
    _write_alternate(
        repo / ".git" / "objects", template / "repo" / ".git" / "objects"
    )
    _write_alternate(
        remote / "objects", template / "upstream.git" / "objects"
    )
    config_path = repo / ".git" / "config"
    config = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config.replace("url = ../upstream.git", f"url = {remote.as_posix()}"),
        encoding="utf-8",
    )
    head_sha = _materialize_root_commit(
        repo=repo,
        remote=remote,
        tree=(template / "tree").read_text(encoding="ascii").strip(),
        user_name=user_name,
        user_email=user_email,
        commit_message=commit_message,
        instance=instance,
    )
    return repo, remote, head_sha
