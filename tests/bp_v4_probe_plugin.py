"""Structured pytest observations for the bounded BP-V4 measurement probe.

This plugin is loaded only by ``scripts/bp_v4_parallel_probe.py``.  Workers
return data through xdist's structured ``workeroutput`` channel; they never
write the controller's observation file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any


_OUTPUT_ENV = "AIOS_BP_V4_PLUGIN_OUTPUT"
_node_collections: dict[str, list[str]] = {}
_worker_payloads: dict[str, dict[str, Any]] = {}


def _resolved(path: object) -> str:
    return str(Path(path).resolve())


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
        return

    output = os.environ.get(_OUTPUT_ENV)
    if not output:
        return
    serial_collection = getattr(config, "_aios_bp_v4_collection", None)
    payload = {
        "schema": "AIOS_BP_V4_PYTEST_OBSERVATION",
        "version": 1,
        "exit_status": int(exitstatus),
        "controller_collection": serial_collection,
        "worker_collections": _node_collections,
        "workers": _worker_payloads,
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
