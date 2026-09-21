"""Fast, process-local materialization of real Git test sandboxes.

The cached directories are only immutable construction templates.  Every caller
receives ordinary, independently writable repository and bare-remote trees.
"""

from __future__ import annotations

import atexit
import hashlib
import itertools
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from threading import Lock
from typing import Mapping
import zlib


_cache_root = Path(tempfile.mkdtemp(prefix="aios-git-fixtures-"))
atexit.register(shutil.rmtree, _cache_root, ignore_errors=True)
_cache_lock = Lock()
_instance_counter = itertools.count()


def _key(
    files: Mapping[str, str | bytes],
    *,
    user_name: str,
    user_email: str,
    commit_message: str,
    remote_head_main: bool,
) -> str:
    del user_name, user_email, commit_message, remote_head_main
    digest = hashlib.sha256()
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
    """Build immutable object material without starting Git processes.

    Git's loose-object and tree formats are stable boundary formats.  Producing
    the baseline objects directly avoids init/config/add/commit/push process
    startup for every distinct fixture shape; Git still performs all behavior
    exercised by the tests against the materialized repositories.
    """

    del user_name, user_email, commit_message, remote_head_main
    objects = template / "objects"
    objects.mkdir(parents=True)
    tree: dict[str, object] = {}
    for name, value in files.items():
        parts = Path(name).as_posix().split("/")
        node = tree
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"fixture path collision at {name}")
            node = child
        body = value.encode("utf-8") if isinstance(value, str) else value
        node[parts[-1]] = bytes.fromhex(_write_object(objects, "blob", body))

    tree_sha = _write_tree(objects, tree)
    (template / "tree").write_text(tree_sha, encoding="ascii")


def _write_tree(objects: Path, entries: Mapping[str, object]) -> str:
    body = bytearray()
    ordered: list[tuple[bytes, bool, bytes]] = []
    for name, value in entries.items():
        encoded_name = name.encode("utf-8")
        is_tree = isinstance(value, dict)
        object_id = (
            bytes.fromhex(_write_tree(objects, value))
            if is_tree
            else value
        )
        if not isinstance(object_id, bytes):
            raise TypeError(f"invalid fixture tree entry: {name}")
        ordered.append((encoded_name, is_tree, object_id))
    ordered.sort(key=lambda entry: entry[0] + (b"/" if entry[1] else b""))
    for name, is_tree, object_id in ordered:
        body.extend(b"40000 " if is_tree else b"100644 ")
        body.extend(name)
        body.append(0)
        body.extend(object_id)
    return _write_object(objects, "tree", bytes(body))


def _write_index(repo: Path, files: Mapping[str, str | bytes]) -> None:
    """Write a v2 index whose stat data already matches the new worktree."""

    entries = bytearray()
    for name in sorted(files, key=lambda item: item.encode("utf-8")):
        path = repo / name
        stat = path.stat()
        value = files[name]
        body = value.encode("utf-8") if isinstance(value, str) else value
        object_id = bytes.fromhex(
            hashlib.sha1(f"blob {len(body)}\0".encode("ascii") + body).hexdigest()
        )
        encoded_name = name.encode("utf-8")
        fixed = struct.pack(
            "!10L20sH",
            int(stat.st_ctime) & 0xFFFFFFFF,
            stat.st_ctime_ns % 1_000_000_000,
            int(stat.st_mtime) & 0xFFFFFFFF,
            stat.st_mtime_ns % 1_000_000_000,
            stat.st_dev & 0xFFFFFFFF,
            stat.st_ino & 0xFFFFFFFF,
            0o100644,
            getattr(stat, "st_uid", 0) & 0xFFFFFFFF,
            getattr(stat, "st_gid", 0) & 0xFFFFFFFF,
            stat.st_size & 0xFFFFFFFF,
            object_id,
            min(len(encoded_name), 0xFFF),
        )
        entry = fixed + encoded_name + b"\0"
        entries.extend(entry)
        entries.extend(b"\0" * (-len(entry) % 8))
    content = b"DIRC" + struct.pack("!2L", 2, len(files)) + entries
    (repo / ".git" / "index").write_bytes(content + hashlib.sha1(content).digest())


def _repo_config(remote: Path, user_name: str, user_email: str) -> str:
    return f"""[core]
\trepositoryformatversion = 0
\tfilemode = false
\tbare = false
\tlogallrefupdates = true
\tautocrlf = true
[user]
\tname = {user_name}
\temail = {user_email}
[remote \"origin\"]
\turl = {remote.as_posix()}
\tfetch = +refs/heads/*:refs/remotes/origin/*
[branch \"main\"]
\tremote = origin
\tmerge = refs/heads/main
[gc]
\tauto = 0
"""


def _materialize_layout(
    *,
    repo: Path,
    remote: Path,
    files: Mapping[str, str | bytes],
    user_name: str,
    user_email: str,
    remote_head_main: bool,
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

    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
    (git_dir / "config").write_text(
        _repo_config(remote, user_name, user_email), encoding="utf-8"
    )
    _write_index(repo, files)
    remote_head = "main" if remote_head_main else "master"
    (remote / "HEAD").write_text(
        f"ref: refs/heads/{remote_head}\n", encoding="ascii"
    )
    (remote / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n"
        "\tbare = true\n[gc]\n\tauto = 0\n",
        encoding="utf-8",
    )


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

    No linked worktrees, hardlinks, shared refs, or shared config are used.  A
    process-local immutable object baseline avoids repeated Git construction;
    each sandbox retains its own writable object directory and Git state.
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
        repo=repo,
        remote=remote,
        files=files,
        user_name=user_name,
        user_email=user_email,
        remote_head_main=remote_head_main,
    )
    _write_alternate(
        repo / ".git" / "objects", template / "objects"
    )
    _write_alternate(
        remote / "objects", template / "objects"
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
