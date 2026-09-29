"""Bounded, diagnostic-only pytest observer for the three stable targets."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import pytest

from tests.aios_stable_failure_probe_plugin import _hash_path, _kind, guard_category

OUTPUT_ENV = "AIOS_STABLE_FAILURE_DETAIL_OUTPUT"
PROFILE_ENV = "AIOS_STABLE_FAILURE_DETAIL_PROFILE"
SCHEMA = "AIOS_STABLE_FAILURE_DETAIL_OBSERVATION"
INGRESS = "src/aios_renew/authoring_ingress.py"
OPERATOR = "src/aios_renew/operator.py"
ROOT = Path(__file__).resolve().parents[1]

FAMILIES = {
    "rev-parse": "REV_PARSE", "clone": "CLONE", "checkout": "CHECKOUT",
    "status": "STATUS", "fetch": "FETCH", "ls-remote": "LS_REMOTE",
    "show": "SHOW", "ls-tree": "LS_TREE", "merge-base": "MERGE_BASE",
    "update-ref": "UPDATE_REF", "cat-file": "CAT_FILE", "push": "PUSH",
}
STDERR_RULES = (
    ("NOT_A_REPOSITORY", (r"not a git repository",)),
    ("UNKNOWN_REVISION_OR_OBJECT", (r"unknown revision", r"ambiguous argument", r"bad object", r"not a valid object name")),
    ("MISSING_REF", (r"couldn't find remote ref", r"unknown ref", r"reference is not a tree", r"pathspec .* did not match")),
    ("LOCK_OR_REF_UPDATE", (r"cannot lock ref", r"unable to create .*\.lock", r"failed to update ref", r"reference update failed")),
    ("REPOSITORY_STATE", (r"unmerged files", r"you need to resolve", r"would be overwritten by", r"not possible because you have unmerged")),
    ("PATH_OR_FILENAME", (r"no such file or directory", r"filename too long", r"invalid path", r"does not exist")),
    ("ACCESS_OR_PERMISSION", (r"permission denied", r"access is denied", r"could not read from remote repository.*permission")),
    ("TRANSPORT_OR_REMOTE", (r"could not resolve host", r"unable to access", r"failed to connect", r"repository .* not found")),
)


def command_family(command: object) -> str:
    if not isinstance(command, (list, tuple)) or len(command) < 2 or command[0] != "git":
        return "OTHER"
    words = command[1:]
    # Only the known -C prefix is skipped; its following operand is never inspected.
    if len(words) >= 3 and words[0] == "-C":
        words = words[2:]
    if not words or not isinstance(words[0], str):
        return "OTHER"
    if words[0] == "remote" and len(words) > 1 and words[1] == "get-url":
        return "REMOTE_GET_URL"
    return FAMILIES.get(words[0], "OTHER")


def stderr_category(stderr: object) -> str:
    if not isinstance(stderr, (bytes, str)):
        return "OTHER"
    sample = stderr[:4096]
    if isinstance(sample, bytes):
        sample = sample.decode("utf-8", errors="replace")
    sample = sample.lower()
    for category, patterns in STDERR_RULES:
        if any(re.search(pattern, sample) for pattern in patterns):
            return category
    return "OTHER"


def ingress_locus(excinfo: Any) -> dict[str, Any] | str:
    if excinfo is None:
        return "UNKNOWN_LOCUS"
    for entry in reversed(list(excinfo.traceback)):
        try:
            relative = Path(entry.path).resolve().relative_to(ROOT).as_posix()
            line = entry.lineno + 1
            function = entry.frame.code.name
        except (OSError, ValueError, AttributeError):
            continue
        if relative == INGRESS and 0 < line <= 1_000_000 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", function):
            return {"path": INGRESS, "function": function, "line": line}
    return "UNKNOWN_LOCUS"


def cause_chain(exc: BaseException) -> list[str]:
    chain = []
    seen = {id(exc)}
    cursor = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    while cursor is not None and id(cursor) not in seen and len(chain) < 8:
        seen.add(id(cursor))
        chain.append(_kind(cursor))
        cursor = cursor.__cause__ if cursor.__cause__ is not None else cursor.__context__
    return chain


def ingress_detail(exc: BaseException, excinfo: Any) -> dict[str, Any]:
    locus = ingress_locus(excinfo)
    return {"exception_type": _kind(exc),
            "message_fingerprint": "sha256:" + hashlib.sha256(str(exc).encode("utf-8", errors="replace")).hexdigest(),
            "cause_chain": cause_chain(exc), "source_locus": locus,
            "detail_status": "RESOLVED" if isinstance(locus, dict) else "UNKNOWN_LOCUS"}


def native_git_call(original: Any, observations: list[tuple[Any, dict[str, Any]]], caller: Any,
                    *args: Any, **kwargs: Any) -> Any:
    try:
        result = original(*args, **kwargs)
        try:
            if (caller is not None and caller.f_code.co_name == "_git"
                    and Path(caller.f_code.co_filename).resolve() == ROOT / OPERATOR
                    and args and isinstance(args[0], (list, tuple)) and tuple(args[0][:1]) == ("git",)
                    and type(result.returncode) is int and result.returncode != 0):
                observations.append((caller, {"command_family": command_family(args[0]),
                                              "return_code": result.returncode,
                                              "stderr_category": stderr_category(result.stderr)}))
        except Exception:
            # Observation failure must not change the subprocess result.
            observations.append((caller, {}))
        return result
    finally:
        del caller


def git_detail(excinfo: Any, observations: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    frames = set()
    traceback = excinfo.value.__traceback__ if excinfo is not None else None
    while traceback is not None:
        frames.add(traceback.tb_frame)
        traceback = traceback.tb_next
    candidates = [detail for frame, detail in observations if frame in frames]
    if len(candidates) != 1 or set(candidates[0]) != {"command_family", "return_code", "stderr_category"}:
        return {"detail_status": "AMBIGUOUS", "git": None}
    return {"detail_status": "RESOLVED", "git": candidates[0]}


_collected: list[str] = []
_executed: list[str] = []
_failures: list[dict[str, Any]] = []
_observations: list[tuple[Any, dict[str, Any]]] = []


def pytest_sessionstart(session: Any) -> None:
    del session
    __import__("tests.git_fixture_support")
    _collected.clear()
    _executed.clear()
    _failures.clear()
    _observations.clear()


def pytest_collection_finish(session: Any) -> None:
    _collected.extend(item.nodeid.replace("\\", "/") for item in session.items)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: Any):
    del item
    original = subprocess.run
    def observed(*args: Any, **kwargs: Any) -> Any:
        return native_git_call(original, _observations, inspect.currentframe().f_back, *args, **kwargs)
    subprocess.run = observed
    try:
        yield
    finally:
        subprocess.run = original


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any):
    outcome = yield
    report = outcome.get_result()
    nodeid = report.nodeid.replace("\\", "/")
    if report.when == "setup":
        _executed.append(nodeid)
    if report.failed:
        excinfo = call.excinfo
        exc = excinfo.value if excinfo is not None else None
        if exc is None:
            detail = {"exception_type": "UnknownException", "detail_status": "AMBIGUOUS"}
        elif nodeid.startswith("tests/test_hot_swap_conformance.py::") and _kind(exc) == "AuthoringIngressError":
            detail = ingress_detail(exc, excinfo)
        elif nodeid.startswith("tests/test_operator.py::") and _kind(exc) == "OperatorError" and guard_category(exc) == "GIT_COMMAND_FAILED":
            detail = {"exception_type": "OperatorError", "guard_category": "GIT_COMMAND_FAILED", **git_detail(excinfo, _observations)}
        else:
            detail = {"exception_type": _kind(exc), "detail_status": "UNEXPECTED_FAILURE"}
        _failures.append({"nodeid": nodeid, "phase": report.when, "detail": detail})
    _observations.clear()


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if OUTPUT_ENV not in os.environ:
        return
    profile = Path(os.environ[PROFILE_ENV]).resolve()
    factory = getattr(session.config, "_tmp_path_factory", None)
    cache = getattr(sys.modules.get("tests.git_fixture_support"), "_cache_root", None)
    basetemp = Path(factory.getbasetemp()).resolve() if factory is not None else None
    cache_path = Path(cache).resolve() if cache is not None else None
    payload = {"schema": SCHEMA, "version": 1, "exit_status": int(exitstatus),
               "collection": _collected, "executed": _executed, "failures": _failures,
               "process": {"pid": os.getpid(), "basetemp": _hash_path(basetemp) if basetemp else None,
                           "cache": _hash_path(cache_path) if cache_path else None,
                           "cache_in_profile": cache_path is not None and cache_path.parent == profile}}
    Path(os.environ[OUTPUT_ENV]).write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
