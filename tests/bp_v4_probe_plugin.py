"""Structured pytest observations for bounded parallel verification.

This plugin is loaded by the BP-V4 probe and selected full-suite wrapper. Workers
return data through xdist's structured ``workeroutput`` channel; they never
write the controller's observation file.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest


_OUTPUT_ENV = "AIOS_BP_V4_PLUGIN_OUTPUT"
MAX_FAILURE_IDENTITIES = 20
MAX_NODEID_DISPLAY_CHARS = 240
_PHASE_ORDER = {"setup": 0, "call": 1, "teardown": 2}
_node_collections: dict[str, list[str]] = {}
_worker_payloads: dict[str, dict[str, Any]] = {}
_local_failure_count = 0
_local_failure_facts: list[dict[str, Any]] = []
_local_failure_truncated = False
_parallel_failure_count = 0
_parallel_failure_facts: list[dict[str, Any]] = []
_parallel_failure_truncated = False


def _resolved(path: object) -> str:
    return str(Path(path).resolve())


def _failure_identity(nodeid: str, phase: str) -> dict[str, Any]:
    """Return one normalized, bounded identity without report payload text."""

    normalized = nodeid.replace("\\", "/")
    if len(normalized) <= MAX_NODEID_DISPLAY_CHARS:
        return {
            "nodeid": normalized,
            "phase": phase,
            "clipped": False,
            "fingerprint": None,
        }
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    suffix = f"...#sha256:{digest}"
    return {
        "nodeid": normalized[: MAX_NODEID_DISPLAY_CHARS - len(suffix)] + suffix,
        "phase": phase,
        "clipped": True,
        "fingerprint": f"sha256:{digest}",
    }


def _fact_key(fact: dict[str, Any]) -> tuple[str, int, str]:
    return (
        fact["nodeid"],
        _PHASE_ORDER[fact["phase"]],
        fact["fingerprint"] or "",
    )


def _bounded_add(
    facts: list[dict[str, Any]], fact: dict[str, Any]
) -> bool:
    """Keep the deterministic lowest bounded set; return whether anything is omitted."""

    if fact in facts:
        return False
    facts.append(fact)
    facts.sort(key=_fact_key)
    if len(facts) > MAX_FAILURE_IDENTITIES:
        facts.pop()
        return True
    return False


def _summary(
    count: int, facts: list[dict[str, Any]], truncated: bool
) -> dict[str, Any]:
    return {
        "reported_count": count,
        "displayed_count": len(facts),
        "display_limit": MAX_FAILURE_IDENTITIES,
        "displayed_identities": list(facts),
        "truncated": truncated,
    }


def pytest_sessionstart(session: Any) -> None:
    del session
    global _local_failure_count, _local_failure_facts, _local_failure_truncated
    global _parallel_failure_count, _parallel_failure_facts
    global _parallel_failure_truncated
    _local_failure_count = 0
    _local_failure_facts = []
    _local_failure_truncated = False
    _parallel_failure_count = 0
    _parallel_failure_facts = []
    _parallel_failure_truncated = False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any):
    """Capture only failed runtest identity and phase in the executing process."""

    del call
    outcome = yield
    report = outcome.get_result()
    if not report.failed or report.when not in _PHASE_ORDER:
        return
    global _local_failure_count, _local_failure_truncated
    _local_failure_count += 1
    if _bounded_add(
        _local_failure_facts, _failure_identity(report.nodeid, report.when)
    ):
        _local_failure_truncated = True


def _worker_facts(config: Any) -> dict[str, Any]:
    worker_input = getattr(config, "workerinput", {})
    worker_id = worker_input.get("workerid")
    temporary_root = None
    factory = getattr(config, "_tmp_path_factory", None)
    if factory is not None:
        temporary_root = _resolved(factory.getbasetemp())

    git_cache_root = None
    for module_name in ("git_fixture_support", "tests.git_fixture_support"):
        module = sys.modules.get(module_name)
        root = getattr(module, "_cache_root", None) if module is not None else None
        if root is not None:
            git_cache_root = _resolved(root)
            break

    return {
        "worker_id": worker_id,
        "process_id": os.getpid(),
        "temporary_root": temporary_root,
        "git_fixture_cache_root": git_cache_root,
    }


def pytest_collection_finish(session: Any) -> None:
    config = session.config
    nodeids = [item.nodeid for item in session.items]
    if hasattr(config, "workerinput"):
        config.workeroutput["aios_bp_v4_collection"] = nodeids
    else:
        config._aios_bp_v4_collection = nodeids


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    config = session.config
    if hasattr(config, "workerinput"):
        config.workeroutput["aios_bp_v4_facts"] = _worker_facts(config)
        config.workeroutput["aios_bp_v4_failures"] = _summary(
            _local_failure_count,
            _local_failure_facts,
            _local_failure_truncated,
        )
        return

    output = os.environ.get(_OUTPUT_ENV)
    if not output:
        return
    serial_collection = getattr(config, "_aios_bp_v4_collection", None)
    failures = (
        _summary(
            _parallel_failure_count,
            _parallel_failure_facts,
            _parallel_failure_truncated,
        )
        if _worker_payloads
        else _summary(
            _local_failure_count,
            _local_failure_facts,
            _local_failure_truncated,
        )
    )
    payload = {
        "schema": "AIOS_BP_V4_PYTEST_OBSERVATION",
        "version": 1,
        "exit_status": int(exitstatus),
        "controller_collection": serial_collection,
        "worker_collections": _node_collections,
        "workers": _worker_payloads,
        "failure_diagnostics": failures,
    }
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def pytest_xdist_node_collection_finished(node: Any, ids: list[str]) -> None:
    _node_collections[node.gateway.id] = list(ids)


def pytest_testnodedown(node: Any, error: object) -> None:
    del error
    facts = node.workeroutput.get("aios_bp_v4_facts")
    collection = node.workeroutput.get("aios_bp_v4_collection")
    if isinstance(facts, dict):
        payload = dict(facts)
        payload["collection"] = collection
        _worker_payloads[node.gateway.id] = payload
    failures = node.workeroutput.get("aios_bp_v4_failures")
    if not isinstance(failures, dict):
        return
    count = failures.get("reported_count")
    identities = failures.get("displayed_identities")
    if not isinstance(count, int) or isinstance(count, bool) or not isinstance(
        identities, list
    ):
        return
    global _parallel_failure_count, _parallel_failure_truncated
    _parallel_failure_count += count
    if failures.get("truncated") is True:
        _parallel_failure_truncated = True
    for identity in identities:
        if isinstance(identity, dict) and _bounded_add(
            _parallel_failure_facts, identity
        ):
            _parallel_failure_truncated = True
