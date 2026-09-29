"""Private, bounded pytest observer for the three RUN-234 stable failures."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import pytest


OUTPUT_ENV = "AIOS_STABLE_FAILURE_PROBE_OUTPUT"
PROFILE_ENV = "AIOS_STABLE_FAILURE_PROBE_PROFILE"
SCHEMA = "AIOS_STABLE_FAILURE_PROBE_OBSERVATION"
SAFE_FILES = {"tests/test_hot_swap_conformance.py", "tests/test_operator.py",
              "src/aios_renew/authoring_ingress.py", "src/aios_renew/operator.py"}

# Exact and fixed-prefix messages from authoring_ingress.py:833-977 and
# operator.py:5030-5174. Dynamic suffixes are never included in the output.
GUARDS = (
    ("AuthoringIngressError", "canonical review decision missing for source run ", "CANONICAL_REVIEW_DECISION_MISSING", True),
    ("AuthoringIngressError", "canonical review decision for ", "CANONICAL_REVIEW_DECISION_AMBIGUOUS", True),
    ("AuthoringIngressError", "canonical review decision content missing for ", "CANONICAL_REVIEW_DECISION_CONTENT_MISSING", True),
    ("AuthoringIngressError", "expected reviewed SHA mismatch: ", "EXPECTED_REVIEWED_SHA_MISMATCH", True),
    ("AuthoringIngressError", "REMEDIATION reviewed_sha does not match REVIEW reviewed_sha: ", "REMEDIATION_REVIEWED_SHA_MISMATCH", True),
    ("AuthoringIngressError", "source review verdict is ", "SOURCE_REVIEW_VERDICT_MISMATCH", True),
    ("AuthoringIngressError", "finding ", "FINDING_NOT_OUTSTANDING", True),
    ("AuthoringIngressError", "finding ", "FINDING_NOT_IN_SOURCE_REVIEW", True),
    ("AuthoringIngressError", "finding ", "FINDING_BASIS_NOT_FAILED", True),
    ("AuthoringIngressError", "REMEDIATION action ", "REMEDIATION_ACTION_MISMATCH", True),
    ("AuthoringIngressError", "conflicting canonical remediation already exists on ", "CANONICAL_REMEDIATION_CONFLICT", True),
    ("AuthoringIngressError", "canonical source artifacts missing for ", "CANONICAL_SOURCE_ARTIFACTS_MISSING", True),
    ("AuthoringIngressError", "canonical source RUN missing for ", "CANONICAL_SOURCE_RUN_MISSING", True),
    ("AuthoringIngressError", "canonical TASK ", "CANONICAL_TASK_MISSING", True),
    ("AuthoringIngressError", "Git command failed: ", "GIT_COMMAND_FAILED", True),
    ("OperatorError", "source-REPAIR bootstrap target is not activated", "SOURCE_REPAIR_TARGET_NOT_ACTIVATED", False),
    ("OperatorError", "source-REPAIR bootstrap legacy generation mismatch", "SOURCE_REPAIR_LEGACY_GENERATION_MISMATCH", False),
    ("OperatorError", "source-REPAIR bootstrap lineage identity mismatch", "SOURCE_REPAIR_LINEAGE_MISMATCH", False),
    ("OperatorError", "source-REPAIR bootstrap target source mismatch", "SOURCE_REPAIR_TARGET_SOURCE_MISMATCH", False),
    ("OperatorError", "source-REPAIR bootstrap transport identity mismatch", "SOURCE_REPAIR_TRANSPORT_IDENTITY_MISMATCH", False),
    ("OperatorError", "source-REPAIR bootstrap transport is partial or invalid", "SOURCE_REPAIR_TRANSPORT_INVALID", False),
    ("OperatorError", "source-REPAIR bootstrap requires one exact completed failed v2 edge", "SOURCE_REPAIR_FAILED_EDGE_MISSING", False),
    ("OperatorError", "active or incomplete source-bootstrap history blocks source-REPAIR", "SOURCE_REPAIR_HISTORY_INCOMPLETE", False),
    ("OperatorError", "source-REPAIR bootstrap handoff state mismatch", "SOURCE_REPAIR_HANDOFF_STATE_MISMATCH", False),
    ("OperatorError", "source-REPAIR requires one complete managed profile", "SOURCE_REPAIR_PROFILE_INCOMPLETE", False),
    ("OperatorError", "source-REPAIR canonical failed RUN mismatch", "SOURCE_REPAIR_FAILED_RUN_MISMATCH", False),
    ("OperatorError", "source-REPAIR failed candidate lineage mismatch", "SOURCE_REPAIR_FAILED_CANDIDATE_LINEAGE_MISMATCH", False),
    ("OperatorError", "source-REPAIR canonical execution profile missing", "SOURCE_REPAIR_PROFILE_MISSING", False),
    ("OperatorError", "source-REPAIR canonical execution profile mismatch", "SOURCE_REPAIR_PROFILE_MISMATCH", False),
    ("OperatorError", "source-REPAIR failed candidate policy missing", "SOURCE_REPAIR_POLICY_MISSING", False),
    ("OperatorError", "source-REPAIR failed candidate policy rejected: ", "SOURCE_REPAIR_POLICY_REJECTED", True),
    ("OperatorError", "source-REPAIR bootstrap transport path mismatch", "SOURCE_REPAIR_TRANSPORT_PATH_MISMATCH", False),
    ("OperatorError", "running source differs from bound source-REPAIR target", "SOURCE_REPAIR_RUNNING_SOURCE_MISMATCH", False),
    ("OperatorError", "Git command failed: ", "GIT_COMMAND_FAILED", True),
)


def guard_category(exc: BaseException) -> str:
    kind, message = type(exc).__name__, str(exc)
    for expected, pattern, category, prefix in GUARDS:
        if kind != expected:
            continue
        if category == "FINDING_NOT_OUTSTANDING":
            if re.fullmatch(r"finding [A-Za-z0-9_-]+ is already resolved, superseded, or otherwise no longer outstanding", message):
                return category
        elif category == "FINDING_NOT_IN_SOURCE_REVIEW":
            if re.fullmatch(r"finding [A-Za-z0-9_-]+ not found in source review [A-Za-z0-9_-]+", message):
                return category
        elif category == "FINDING_BASIS_NOT_FAILED":
            if re.fullmatch(r"finding [A-Za-z0-9_-]+ basis [A-Za-z0-9_-]+ is not marked FAIL in review", message):
                return category
        elif category == "CANONICAL_TASK_MISSING":
            if re.fullmatch(r"canonical TASK [A-Za-z0-9_-]+ missing from reviewed candidate", message):
                return category
        elif category == "CANONICAL_REVIEW_DECISION_AMBIGUOUS":
            if re.fullmatch(r"canonical review decision for RUN-[A-Za-z0-9-]+ is missing or ambiguous", message):
                return category
        elif (message.startswith(pattern) and len(message) > len(pattern)) if prefix else message == pattern:
            return category
    return "UNKNOWN"


def _kind(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", name) else "UnknownException"


def _facts(exc: BaseException) -> dict[str, int]:
    facts = {}
    for key in ("errno", "winerror", "returncode"):
        value = getattr(exc, key, None)
        if type(value) is int and -(2**31) <= value < 2**31:
            facts[key] = value
    return facts


def _locus(excinfo: Any, nodeid: str) -> dict[str, Any] | None:
    root = Path(__file__).resolve().parents[1]
    target = nodeid.split("::", 1)[0]
    candidates = []
    for entry in reversed(list(excinfo.traceback)):
        try:
            relative = Path(entry.path).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if relative in SAFE_FILES and 0 < entry.lineno + 1 <= 1_000_000:
            candidates.append({"path": relative, "line": entry.lineno + 1})
    for candidate in candidates:
        if candidate["path"].startswith("src/"):
            return candidate
    return next((item for item in candidates if item["path"] == target), None)


def cause(exc: BaseException, excinfo: Any, nodeid: str) -> dict[str, Any]:
    chain = []
    seen = {id(exc)}
    cursor = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    while cursor is not None and id(cursor) not in seen and len(chain) < 8:
        seen.add(id(cursor))
        chain.append({"exception_type": _kind(cursor), "integer_facts": _facts(cursor)})
        cursor = cursor.__cause__ if cursor.__cause__ is not None else cursor.__context__
    category = guard_category(exc)
    if category == "UNKNOWN":
        chain = [{"exception_type": item["exception_type"], "integer_facts": {}} for item in chain]
    return {"exception_type": _kind(exc),
            "message_fingerprint": "sha256:" + hashlib.sha256(str(exc).encode("utf-8", errors="replace")).hexdigest(),
            "source_locus": _locus(excinfo, nodeid) if category != "UNKNOWN" else None,
            "cause_chain": chain, "integer_facts": _facts(exc) if category != "UNKNOWN" else {},
            "guard_category": category}


def _hash_path(path: Path) -> str:
    return "sha256:" + hashlib.sha256(os.path.normcase(str(path.resolve())).encode("utf-8")).hexdigest()


_collected: list[str] = []
_executed: list[str] = []
_failures: list[dict[str, Any]] = []


def pytest_sessionstart(session: Any) -> None:
    del session
    # Construct the fixture cache in this process, even for collect-only runs.
    __import__("tests.git_fixture_support")
    _collected.clear()
    _executed.clear()
    _failures.clear()


def pytest_collection_finish(session: Any) -> None:
    _collected.extend(item.nodeid.replace("\\", "/") for item in session.items)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any):
    outcome = yield
    report = outcome.get_result()
    if report.when == "setup":
        _executed.append(report.nodeid.replace("\\", "/"))
    if report.failed:
        _failures.append({"nodeid": report.nodeid.replace("\\", "/"), "phase": report.when,
                          "cause": cause(call.excinfo.value, call.excinfo, report.nodeid.replace("\\", "/"))
                          if call.excinfo is not None else None})


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
               "process": {"pid": os.getpid(),
                           "basetemp": _hash_path(basetemp) if basetemp else None,
                           "cache": _hash_path(cache_path) if cache_path else None,
                           "cache_in_profile": cache_path is not None and cache_path.parent == profile}}
    Path(os.environ[OUTPUT_ENV]).write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
