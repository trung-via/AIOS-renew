"""Bounded, read-only source material for an already selected semantic review scope.

This module has no lifecycle or Reviewer authority. Git is used only by the
constructor; ``validate_review_material_package`` is a pure package validator.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any


MAX_BLOB_BYTES = 262144
MAX_DIFF_BYTES = 262144
MAX_PACKAGE_BYTES = 1048576
MAX_CHANGES = 64
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MODES = {"100644", "100755"}
_SCOPE_FIELDS = {
    "format", "version", "kind", "task", "reviewed_run_id", "review_mode",
    "semantic_origin_run_id", "semantic_base_sha", "latest_delta_base_sha",
    "reviewed_head_sha", "prior_review_run_id", "prior_review_id",
    "prior_finding_id", "scope_fingerprint",
}
_PACKAGE_FIELDS = {
    "format", "version", "kind", "review_scope_fingerprint", "review_mode",
    "semantic_base_sha", "latest_delta_base_sha", "reviewed_head_sha",
    "semantic_view", "latest_delta_view", "sources", "package_fingerprint",
}
_VIEW_FIELDS = {"base_sha", "head_sha", "changes"}
_CHANGE_FIELDS = {
    "path", "status", "base_mode", "head_mode", "base_content_sha256",
    "head_content_sha256", "review_source_ref", "unified_diff",
}
_SOURCE_FIELDS = {"source_ref", "content_sha256", "byte_length", "text"}


class ReviewMaterialError(ValueError):
    """A stable pre-Reviewer material rejection."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _fail(code: str, detail: str = "") -> None:
    raise ReviewMaterialError(code, detail)


def _closed(value: Any, fields: set[str], code: str) -> None:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code, "closed field set")


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        _fail("MATERIAL_INCONSISTENT", str(exc))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hex(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _scope(scope: Any) -> None:
    _closed(scope, _SCOPE_FIELDS, "REVIEW_SCOPE_INVALID")
    task = scope["task"]
    if (
        scope["format"] != "AIOS_SEMANTIC_REVIEW_SCOPE"
        or type(scope["version"]) is not int or scope["version"] != 1
        or scope["kind"] != "SEMANTIC_REVIEW_SCOPE"
        or not isinstance(task, Mapping) or set(task) != {"id", "revision"}
        or not isinstance(task["id"], str) or not task["id"]
        or type(task["revision"]) is not int or task["revision"] < 1
        or not isinstance(scope["review_mode"], str)
        or scope["review_mode"] not in ("PRIMARY", "DELTA")
        or any(not isinstance(scope[k], str) or not scope[k]
               for k in ("reviewed_run_id", "semantic_origin_run_id"))
        or any(not _hex(scope[k], _HEX40) for k in
               ("semantic_base_sha", "latest_delta_base_sha", "reviewed_head_sha"))
        or not _hex(scope["scope_fingerprint"], _HEX64)
        or any(value is not None and (not isinstance(value, str) or not value)
               for value in (scope["prior_review_run_id"], scope["prior_review_id"],
                             scope["prior_finding_id"]))
    ):
        _fail("REVIEW_SCOPE_INVALID", "invalid P1A scope fields")
    body = {k: v for k, v in scope.items() if k != "scope_fingerprint"}
    try:
        fingerprint = _sha(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ReviewMaterialError("REVIEW_SCOPE_INVALID", "scope encoding") from exc
    if fingerprint != scope["scope_fingerprint"]:
        _fail("REVIEW_SCOPE_INVALID", "scope fingerprint")


def _path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        return False
    if re.match(r"[A-Za-z]:/", value):
        return False
    if any(part in ("", ".", "..") for part in value.split("/")):
        return False
    if any(unicodedata.category(char) == "Cc" for char in value):
        return False
    try:
        value.encode("utf-8", "strict")
    except UnicodeError:
        return False
    return True


def _strict_text(data: bytes) -> str:
    if len(data) > MAX_BLOB_BYTES:
        _fail("MATERIAL_BOUND_EXCEEDED", "blob")
    if b"\0" in data:
        _fail("UNSUPPORTED_MATERIAL", "NUL content")
    try:
        return data.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise ReviewMaterialError("UNSUPPORTED_MATERIAL", "non-UTF-8 content") from exc


def _lines(text: str) -> list[str]:
    if not text:
        return []
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _diff(path: str, base: str, head: str) -> str:
    output: list[str] = []
    for line in difflib.unified_diff(_lines(base), _lines(head),
                                     fromfile="a/" + path, tofile="b/" + path,
                                     lineterm="\n"):
        if line.endswith("\n"):
            output.append(line)
        else:
            output.append(line + "\n\\ No newline at end of file\n")
    return "".join(output)


def _git(repo: Path, *args: str, input_data: bytes | None = None) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
               GIT_ATTR_NOSYSTEM="1")
    try:
        result = subprocess.run(["git", *args], cwd=repo, env=env, input=input_data,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False)
    except OSError as exc:
        raise ReviewMaterialError("GIT_MATERIAL_UNAVAILABLE", str(exc)) from exc
    if result.returncode:
        _fail("GIT_MATERIAL_UNAVAILABLE", "bound Git object unavailable")
    return result.stdout


def _tree(repo: Path, commit: str) -> dict[str, tuple[str, str, str]]:
    if _git(repo, "cat-file", "-t", commit).strip() != b"commit":
        _fail("UNSUPPORTED_MATERIAL", "bound SHA is not a commit")
    # Recursive leaf entries only; directory trees are structural, not changes.
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries: dict[str, tuple[str, str, str]] = {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        try:
            metadata, raw_path = row.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise ReviewMaterialError("UNSUPPORTED_MATERIAL", "Git tree entry") from exc
        if not _path(path) or path in entries:
            _fail("UNSUPPORTED_MATERIAL", "Git path")
        entries[path] = mode, kind, oid
    return entries


def _blob(repo: Path, oid: str) -> tuple[str, str]:
    if _git(repo, "cat-file", "-t", oid).strip() != b"blob":
        _fail("UNSUPPORTED_MATERIAL", "non-blob object")
    try:
        size = int(_git(repo, "cat-file", "-s", oid).strip())
    except ValueError as exc:
        raise ReviewMaterialError("GIT_MATERIAL_UNAVAILABLE", "blob size") from exc
    if size > MAX_BLOB_BYTES:
        _fail("MATERIAL_BOUND_EXCEEDED", "blob")
    data = _git(repo, "cat-file", "blob", oid)
    if len(data) != size:
        _fail("MATERIAL_INCONSISTENT", "blob size changed")
    return _strict_text(data), _sha(data)


def _view(repo: Path, base_sha: str, head_sha: str,
          sources: dict[str, dict[str, Any]], cache: dict[str, tuple[str, str]]) -> dict[str, Any]:
    base_tree, head_tree = _tree(repo, base_sha), _tree(repo, head_sha)
    paths = sorted(set(base_tree) | set(head_tree), key=lambda p: p.encode("utf-8"))
    changes: list[dict[str, Any]] = []
    diff_bytes = 0

    def read(entry: tuple[str, str, str] | None) -> tuple[str, str] | None:
        if entry is None:
            return None
        mode, kind, oid = entry
        if mode not in _MODES or kind != "blob":
            _fail("UNSUPPORTED_MATERIAL", "changed non-regular object")
        if oid not in cache:
            cache[oid] = _blob(repo, oid)
        return cache[oid]

    for path in paths:
        before, after = base_tree.get(path), head_tree.get(path)
        if before == after:
            continue
        if len(changes) >= MAX_CHANGES:
            _fail("MATERIAL_BOUND_EXCEEDED", "view change count")
        old, new = read(before), read(after)
        status = "MODIFY" if old and new else "DELETE" if old else "ADD"
        selected = new if new is not None else old
        assert selected is not None
        text, digest = selected
        ref = "sha256:" + digest
        source = {"source_ref": ref, "content_sha256": digest,
                  "byte_length": len(text.encode("utf-8")), "text": text}
        if ref in sources and sources[ref] != source:
            _fail("MATERIAL_INCONSISTENT", "source hash collision")
        sources[ref] = source
        unified = _diff(path, old[0] if old else "", new[0] if new else "")
        diff_bytes += len(unified.encode("utf-8"))
        if diff_bytes > MAX_DIFF_BYTES:
            _fail("MATERIAL_BOUND_EXCEEDED", "view diff")
        changes.append({
            "path": path, "status": status,
            "base_mode": before[0] if before else None,
            "head_mode": after[0] if after else None,
            "base_content_sha256": old[1] if old else None,
            "head_content_sha256": new[1] if new else None,
            "review_source_ref": ref, "unified_diff": unified,
        })
    return {"base_sha": base_sha, "head_sha": head_sha, "changes": changes}


def construct_review_material_package(scope: Mapping[str, Any], *,
                                      repo: str | Path) -> dict[str, Any]:
    """Construct exact bounded material from already-local objects in a P1A scope."""
    _scope(scope)
    root = Path(repo)
    sources: dict[str, dict[str, Any]] = {}
    cache: dict[str, tuple[str, str]] = {}
    semantic = _view(root, scope["semantic_base_sha"], scope["reviewed_head_sha"],
                     sources, cache)
    latest = None
    if scope["latest_delta_base_sha"] != scope["semantic_base_sha"]:
        latest = _view(root, scope["latest_delta_base_sha"], scope["reviewed_head_sha"],
                       sources, cache)
    package = {
        "format": "AIOS_REVIEW_MATERIAL_PACKAGE", "version": 1,
        "kind": "REVIEW_MATERIAL_PACKAGE",
        "review_scope_fingerprint": scope["scope_fingerprint"],
        "review_mode": scope["review_mode"],
        "semantic_base_sha": scope["semantic_base_sha"],
        "latest_delta_base_sha": scope["latest_delta_base_sha"],
        "reviewed_head_sha": scope["reviewed_head_sha"],
        "semantic_view": semantic, "latest_delta_view": latest,
        "sources": [sources[key] for key in sorted(sources)],
    }
    if len(_json_bytes(package)) > MAX_PACKAGE_BYTES:
        _fail("MATERIAL_BOUND_EXCEEDED", "package")
    package["package_fingerprint"] = _sha(_json_bytes(package))
    if len(_json_bytes(package)) > MAX_PACKAGE_BYTES:
        _fail("MATERIAL_BOUND_EXCEEDED", "package")
    validate_review_material_package(package)
    return package


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("MATERIAL_INCONSISTENT", "duplicate JSON key")
        result[key] = value
    return result


def validate_review_material_package(package: Mapping[str, Any] | str | bytes) -> dict[str, Any]:
    """Purely revalidate the complete serialized v1 package; return its normalized mapping."""
    if isinstance(package, (str, bytes)):
        try:
            package = json.loads(package, object_pairs_hook=_pairs)
        except (ValueError, UnicodeError, TypeError) as exc:
            if isinstance(exc, ReviewMaterialError):
                raise
            raise ReviewMaterialError("MATERIAL_INCONSISTENT", "invalid JSON") from exc
    _closed(package, _PACKAGE_FIELDS, "MATERIAL_INCONSISTENT")
    if (package["format"] != "AIOS_REVIEW_MATERIAL_PACKAGE"
        or type(package["version"]) is not int or package["version"] != 1
        or package["kind"] != "REVIEW_MATERIAL_PACKAGE"
        or not isinstance(package["review_mode"], str)
        or package["review_mode"] not in ("PRIMARY", "DELTA")
        or not _hex(package["review_scope_fingerprint"], _HEX64)
        or any(not _hex(package[k], _HEX40) for k in
               ("semantic_base_sha", "latest_delta_base_sha", "reviewed_head_sha"))):
        _fail("MATERIAL_INCONSISTENT", "package identity")
    refs: set[str] = set()

    def check_view(view: Any, base: str) -> None:
        _closed(view, _VIEW_FIELDS, "MATERIAL_INCONSISTENT")
        if (view["base_sha"] != base or view["head_sha"] != package["reviewed_head_sha"]
            or not isinstance(view["changes"], list)):
            _fail("MATERIAL_INCONSISTENT", "view binding")
        if len(view["changes"]) > MAX_CHANGES:
            _fail("MATERIAL_BOUND_EXCEEDED", "view change count")
        previous: bytes | None = None
        diff_bytes = 0
        for record in view["changes"]:
            _closed(record, _CHANGE_FIELDS, "MATERIAL_INCONSISTENT")
            path = record["path"]
            if not _path(path):
                _fail("MATERIAL_INCONSISTENT", "path")
            order = path.encode("utf-8")
            if previous is not None and order <= previous:
                _fail("MATERIAL_INCONSISTENT", "duplicate or unsorted paths")
            previous = order
            status = record["status"]
            if not isinstance(status, str) or status not in ("ADD", "MODIFY", "DELETE"):
                _fail("UNSUPPORTED_MATERIAL", "status")
            bm, hm = record["base_mode"], record["head_mode"]
            bh, hh = record["base_content_sha256"], record["head_content_sha256"]
            if ((status == "ADD" and (bm is not None or bh is not None or not isinstance(hm, str) or hm not in _MODES or not _hex(hh, _HEX64)))
                or (status == "DELETE" and (hm is not None or hh is not None or not isinstance(bm, str) or bm not in _MODES or not _hex(bh, _HEX64)))
                or (status == "MODIFY" and (not isinstance(bm, str) or bm not in _MODES or not isinstance(hm, str) or hm not in _MODES or not _hex(bh, _HEX64) or not _hex(hh, _HEX64)))):
                _fail("UNSUPPORTED_MATERIAL", "status/mode/hash matrix")
            if status == "MODIFY" and bm == hm and bh == hh:
                _fail("MATERIAL_INCONSISTENT", "unchanged record")
            ref = record["review_source_ref"]
            if not isinstance(ref, str) or ref != "sha256:" + (bh if status == "DELETE" else hh):
                _fail("MATERIAL_INCONSISTENT", "source reference")
            refs.add(ref)
            diff = record["unified_diff"]
            if not isinstance(diff, str):
                _fail("MATERIAL_INCONSISTENT", "diff type")
            diff_data = _json_text_bytes(diff)
            if b"\0" in diff_data:
                _fail("UNSUPPORTED_MATERIAL", "NUL diff")
            diff_bytes += len(diff_data)
            if diff_bytes > MAX_DIFF_BYTES:
                _fail("MATERIAL_BOUND_EXCEEDED", "view diff")

    check_view(package["semantic_view"], package["semantic_base_sha"])
    latest = package["latest_delta_view"]
    if package["latest_delta_base_sha"] == package["semantic_base_sha"]:
        if latest is not None:
            _fail("MATERIAL_INCONSISTENT", "duplicate latest view")
    else:
        check_view(latest, package["latest_delta_base_sha"])
    sources = package["sources"]
    if not isinstance(sources, list):
        _fail("MATERIAL_INCONSISTENT", "source table")
    observed: set[str] = set()
    previous_ref: str | None = None
    for source in sources:
        _closed(source, _SOURCE_FIELDS, "MATERIAL_INCONSISTENT")
        ref, digest, length, content = (source[k] for k in
                                        ("source_ref", "content_sha256", "byte_length", "text"))
        if not _hex(digest, _HEX64) or ref != "sha256:" + digest or not isinstance(content, str):
            _fail("MATERIAL_INCONSISTENT", "source identity")
        data = _json_text_bytes(content)
        if b"\0" in data:
            _fail("UNSUPPORTED_MATERIAL", "NUL source")
        if len(data) > MAX_BLOB_BYTES:
            _fail("MATERIAL_BOUND_EXCEEDED", "source")
        if type(length) is not int or length != len(data) or _sha(data) != digest:
            _fail("MATERIAL_INCONSISTENT", "source bytes/hash/length")
        if previous_ref is not None and ref <= previous_ref:
            _fail("MATERIAL_INCONSISTENT", "duplicate or unsorted sources")
        previous_ref = ref
        observed.add(ref)
    if observed != refs:
        _fail("MATERIAL_INCONSISTENT", "source membership")
    body = {k: v for k, v in package.items() if k != "package_fingerprint"}
    if len(_json_bytes(package)) > MAX_PACKAGE_BYTES:
        _fail("MATERIAL_BOUND_EXCEEDED", "package")
    if not _hex(package["package_fingerprint"], _HEX64) or _sha(_json_bytes(body)) != package["package_fingerprint"]:
        _fail("MATERIAL_INCONSISTENT", "package fingerprint")
    return dict(package)


def _json_text_bytes(text: str) -> bytes:
    try:
        return text.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise ReviewMaterialError("MATERIAL_INCONSISTENT", "invalid UTF-8 text") from exc


__all__ = ["ReviewMaterialError", "construct_review_material_package",
           "validate_review_material_package"]
