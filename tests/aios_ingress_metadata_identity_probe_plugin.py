"""Private, byte-safe observer for the fixed AUTHOR_REMEDIATION diagnostic."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import pytest

import aios_renew.authoring_ingress as ingress


OUTPUT_ENV = "AIOS_METADATA_IDENTITY_OUTPUT"
PROFILE_ENV = "AIOS_METADATA_IDENTITY_PROFILE"
SCHEMA = "AIOS_METADATA_IDENTITY_OBSERVATION"
TARGETS = {
    "control": "tests/test_aios_ingress_metadata_identity_diagnostic.py::test_ordinary_author_remediation_control",
    "fresh": "tests/test_hot_swap_conformance.py::test_scenario_2_fresh_correction_accepts_only_current_finding",
}
_original_validate = ingress._validate_metadata_commit
_original_read = ingress._read_commit_blob
_attempts: list[dict[str, Any]] = []
_collected: list[str] = []
_executed: list[str] = []
_failures: list[dict[str, str]] = []


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _signature(value: bytes) -> dict[str, Any]:
    body = value[3:] if value.startswith(b"\xef\xbb\xbf") else value
    crlf = body.count(b"\r\n")
    return {"bom": value.startswith(b"\xef\xbb\xbf"), "crlf": crlf,
            "lf": body.count(b"\n") - crlf, "cr": body.count(b"\r") - crlf,
            "trailing": "CRLF" if body.endswith(b"\r\n") else "LF" if body.endswith(b"\n")
            else "CR" if body.endswith(b"\r") else "NONE"}


def _transform(expected: bytes, actual: bytes) -> str:
    if expected == actual:
        return "EXACT"
    # The bounded relation is computed from bytes before they are discarded.
    candidates = {expected}
    for _ in range(3):
        expanded = set(candidates)
        for value in candidates:
            body = value[3:] if value.startswith(b"\xef\xbb\xbf") else value
            expanded.add(body)
            expanded.add(b"\xef\xbb\xbf" + body)
            expanded.add(value.replace(b"\r\n", b"\n"))
            expanded.add(value.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            expanded.add(value.rstrip(b"\r\n"))
            expanded.add(value.rstrip(b"\r\n") + b"\n")
            expanded.add(value.rstrip(b"\r\n") + b"\r\n")
        candidates = expanded
        if actual in candidates:
            return "LINE_ENDING_OR_BOM"
    return "OTHER"


def _bytes(value: bytes) -> dict[str, Any]:
    return {"length": len(value), "sha256": _sha(value), "signature": _signature(value)}


def _git(repo: Path, *args: str) -> bytes | None:
    proc = subprocess.run(("git", "-C", str(repo), *args), capture_output=True, check=False)
    return proc.stdout if proc.returncode == 0 else None


def _observe(repo: Path, commit: str, path: str, expected: bytes,
             readback: bytes | None, read_count: int) -> dict[str, Any]:
    # No Git output is serialized; the tree entry is parsed as a binary record.
    entry = _git(repo, "ls-tree", "-z", commit, "--", path)
    oid = None
    if entry is not None:
        match = re.fullmatch(rb"100644 blob ([0-9a-f]{40})\t" + re.escape(path.encode()) + rb"\0", entry)
        if match:
            oid = match.group(1).decode("ascii")
    native = _git(repo, "cat-file", "blob", oid) if oid else None
    expected_oid = hashlib.sha1(b"blob " + str(len(expected)).encode("ascii") + b"\0" + expected).hexdigest()
    return {"expected": _bytes(expected), "expected_object": expected_oid,
            "tree_object": oid, "native": _bytes(native) if native is not None else None,
            "native_object_valid": (hashlib.sha1(b"blob " + str(len(native)).encode("ascii") + b"\0" + native).hexdigest() == oid)
            if native is not None else None,
            "readback": _bytes(readback) if readback is not None else None,
            "read_count": read_count,
            "native_relation": _transform(expected, native) if native is not None else None,
            "readback_relation": _transform(expected, readback) if readback is not None else None,
            "native_readback_equal": native == readback if native is not None and readback is not None else None}


def _validate(repo: Path, commit_sha: str, *, expected_parent_sha: str,
              metadata_path: str, metadata_bytes: bytes, operation: str) -> None:
    if operation != "AUTHOR_REMEDIATION":
        return _original_validate(repo, commit_sha, expected_parent_sha=expected_parent_sha,
                                  metadata_path=metadata_path, metadata_bytes=metadata_bytes,
                                  operation=operation)
    readback = None
    reads = 0

    def observe_read(read_repo: Path, read_commit: str, read_path: str) -> bytes | None:
        nonlocal readback, reads
        result = _original_read(read_repo, read_commit, read_path)
        if read_repo == repo and read_commit == commit_sha and read_path == metadata_path:
            reads += 1
            readback = result
        return result

    record: dict[str, Any] = {"delegations": 0, "operation": operation, "facts": None}
    _attempts.append(record)
    ingress._read_commit_blob = observe_read
    try:
        record["delegations"] += 1
        return _original_validate(repo, commit_sha, expected_parent_sha=expected_parent_sha,
                                  metadata_path=metadata_path, metadata_bytes=metadata_bytes,
                                  operation=operation)
    finally:
        ingress._read_commit_blob = _original_read
        try:
            record["facts"] = _observe(repo, commit_sha, metadata_path, metadata_bytes, readback, reads)
        except Exception:
            record["facts"] = None


def pytest_sessionstart(session: Any) -> None:
    del session
    __import__("tests.git_fixture_support")
    _attempts.clear()
    _collected.clear()
    _executed.clear()
    _failures.clear()
    ingress._validate_metadata_commit = _validate


def pytest_collection_finish(session: Any) -> None:
    _collected.extend(item.nodeid.replace("\\", "/") for item in session.items)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any):
    outcome = yield
    report = outcome.get_result()
    if report.when == "setup":
        _executed.append(report.nodeid.replace("\\", "/"))
    if report.failed:
        exc = call.excinfo.value if call.excinfo is not None else None
        ingress_error = isinstance(exc, ingress.AuthoringIngressError)
        _failures.append({"nodeid": report.nodeid.replace("\\", "/"), "phase": report.when,
                          "type": "AuthoringIngressError" if ingress_error else "OtherException",
                          "message_sha256": _sha(str(exc).encode("utf-8", errors="replace")) if ingress_error else None})


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if OUTPUT_ENV not in os.environ:
        return
    profile = Path(os.environ[PROFILE_ENV]).resolve()
    factory = getattr(session.config, "_tmp_path_factory", None)
    basetemp = Path(factory.getbasetemp()).resolve() if factory is not None else None
    cache = getattr(sys.modules.get("tests.git_fixture_support"), "_cache_root", None)
    cache_path = Path(cache).resolve() if cache is not None else None
    value = {"schema": SCHEMA, "version": 1, "status": int(exitstatus),
             "collection": _collected, "executed": _executed, "failures": _failures,
             "attempts": _attempts, "pid": os.getpid(),
             "basetemp_sha256": _sha(os.path.normcase(str(basetemp)).encode()) if basetemp else None,
             "cache_sha256": _sha(os.path.normcase(str(cache_path)).encode()) if cache_path else None,
             "cache_in_profile": cache_path is not None and cache_path.parent == profile}
    Path(os.environ[OUTPUT_ENV]).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
