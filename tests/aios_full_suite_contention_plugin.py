"""Private structured observer for the fixed full-suite contention experiment."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest

from bp_v4_diagnostic_plugin import _cause, _path_fact


OUTPUT_ENV = "AIOS_FULL_SUITE_CONTENTION_OUTPUT"
LIMIT = 64
_collections: dict[str, list[str]] = {}
_workers: dict[str, dict[str, Any]] = {}
_worker_failures: dict[str, dict[str, Any]] = {}
_worker_executions: dict[str, list[str]] = {}
_failures: list[dict[str, Any]] = []
_executions: list[str] = []
_overflow = False


def _record(fact: dict[str, Any]) -> None:
    global _overflow
    identities = {item["nodeid"] for item in _failures}
    if fact["nodeid"] not in identities and len(identities) == LIMIT:
        _overflow = True
    elif fact not in _failures:
        _failures.append(fact)


def pytest_sessionstart(session: Any) -> None:
    del session
    importlib.import_module("tests.git_fixture_support")
    global _overflow
    _collections.clear()
    _workers.clear()
    _worker_failures.clear()
    _worker_executions.clear()
    _failures.clear()
    _executions.clear()
    _overflow = False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any):
    outcome = yield
    report = outcome.get_result()
    nodeid = report.nodeid
    if report.when == "setup":
        _executions.append(nodeid)
    if report.failed and report.when in {"setup", "call", "teardown"}:
        _record({
            "nodeid": nodeid,
            "phase": report.when,
            "worker_id": item.config.workerinput["workerid"] if hasattr(item.config, "workerinput") else "controller",
            "cause": _cause(call.excinfo.value if call.excinfo is not None else None,
                            excinfo=call.excinfo, nodeid=nodeid),
        })


def _process_facts(config: Any) -> dict[str, Any]:
    factory = getattr(config, "_tmp_path_factory", None)
    temporary_root = str(Path(factory.getbasetemp()).resolve()) if factory is not None else None
    git_root = None
    for module_name in ("git_fixture_support", "tests.git_fixture_support"):
        module = sys.modules.get(module_name)
        value = getattr(module, "_cache_root", None) if module is not None else None
        if value is not None:
            git_root = str(Path(value).resolve())
            break
    return {
        "process_id": os.getpid(),
        "temporary_root": _path_fact(temporary_root),
        "git_fixture_cache_root": _path_fact(git_root),
    }


def pytest_collection_finish(session: Any) -> None:
    ids = [item.nodeid for item in session.items]
    config = session.config
    if hasattr(config, "workerinput"):
        config.workeroutput["aios_contention_collection"] = ids
    else:
        config._aios_contention_collection = ids


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    config = session.config
    if hasattr(config, "workerinput"):
        config.workeroutput["aios_contention_process"] = _process_facts(config)
        config.workeroutput["aios_contention_failures"] = {"facts": _failures, "over_bound": _overflow}
        config.workeroutput["aios_contention_executions"] = _executions
        return
    payload = {
        "schema": "AIOS_FULL_SUITE_CONTENTION_PYTEST_OBSERVATION",
        "version": 1,
        "exit_status": int(exitstatus),
        "controller_collection": getattr(config, "_aios_contention_collection", None),
        "nodeid_fingerprints": {
            node: "sha256:" + hashlib.sha256(node.encode("utf-8")).hexdigest()
            for node in (next(iter(_collections.values())) if _workers
                         else getattr(config, "_aios_contention_collection", None) or [])
        },
        "worker_collections": _collections,
        "workers": _workers,
        "worker_failure_reports": sorted(_worker_failures),
        "worker_execution_reports": sorted(_worker_executions),
        "failures": [fact for worker in sorted(_worker_failures) for fact in _worker_failures[worker]["facts"]] if _workers else _failures,
        "over_bound": _overflow or any(report["over_bound"] for report in _worker_failures.values()),
        "executed_nodeids": [node for worker in sorted(_worker_executions) for node in _worker_executions[worker]] if _workers else _executions,
        "controller_process": _process_facts(config),
    }
    Path(os.environ[OUTPUT_ENV]).write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def pytest_xdist_node_collection_finished(node: Any, ids: list[str]) -> None:
    _collections[node.gateway.id] = list(ids)


def pytest_testnodedown(node: Any, error: object) -> None:
    if error is not None:
        return
    output = node.workeroutput
    key = node.gateway.id
    process = output.get("aios_contention_process")
    failures = output.get("aios_contention_failures")
    executions = output.get("aios_contention_executions")
    if isinstance(process, dict):
        _workers[key] = {"worker_id": key, **process, "collection": output.get("aios_contention_collection")}
    if isinstance(failures, dict):
        _worker_failures[key] = failures
    if isinstance(executions, list):
        _worker_executions[key] = executions
