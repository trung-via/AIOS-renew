"""Shared bounded BP-V4 collection, invocation, and conformance primitives."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any, Callable

PYTEST_RANGE = ((8, 2), (9, 0))
XDIST_RANGE = ((3, 6), (4, 0))
PLUGIN_NAME = "bp_v4_probe_plugin"
PLUGIN_OUTPUT_ENV = "AIOS_BP_V4_PLUGIN_OUTPUT"
MAX_FAILURE_IDENTITIES = 20
MAX_NODEID_DISPLAY_CHARS = 240
FAILURE_PHASES = {"setup", "call", "teardown"}

class ProbeError(RuntimeError):
    """Fail-closed verification construction or observation error."""

def _version_prefix(value: str) -> tuple[int, int]:
    parts = value.split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError) as exc:
        raise ProbeError(f"incompatible package version: {value!r}") from exc


def _in_range(value: str, bounds: tuple[tuple[int, int], tuple[int, int]]) -> bool:
    parsed = _version_prefix(value)
    return bounds[0] <= parsed < bounds[1]


def load_toolchain() -> dict[str, str]:
    """Resolve the declared optional capability without installing anything."""

    try:
        pytest_version = importlib.metadata.version("pytest")
        xdist_version = importlib.metadata.version("pytest-xdist")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ProbeError(
            "parallel verification requires the optional bp-v4-measurement or parallel-verification extra"
        ) from exc
    if not _in_range(pytest_version, PYTEST_RANGE):
        raise ProbeError(f"incompatible pytest version: {pytest_version}")
    if not _in_range(xdist_version, XDIST_RANGE):
        raise ProbeError(f"incompatible pytest-xdist version: {xdist_version}")
    try:
        importlib.import_module("pytest")
        importlib.import_module("xdist.plugin")
    except (ImportError, RuntimeError) as exc:
        raise ProbeError("incompatible pytest/xdist capability") from exc
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "pytest_version": pytest_version,
        "pytest_xdist_version": xdist_version,
    }


def collection_identity(nodeids: object) -> dict[str, Any]:
    """Normalize pytest node ids and return the repository-owned identity.

    Rule v1 converts path separators to ``/``, requires unique non-empty string
    identities, sorts them by Unicode code point, and hashes their UTF-8 form,
    each terminated by one LF, with SHA-256.
    """

    if not isinstance(nodeids, list) or any(
        not isinstance(item, str) or not item for item in nodeids
    ):
        raise ProbeError("malformed pytest collection data")
    normalized = [item.replace("\\", "/") for item in nodeids]
    if len(normalized) != len(set(normalized)):
        raise ProbeError("pytest collection contains duplicate node identities")
    normalized.sort()
    encoded = "".join(f"{item}\n" for item in normalized).encode("utf-8")
    return {
        "rule": "sorted-posix-nodeid-lf-sha256-v1",
        "count": len(normalized),
        "digest": hashlib.sha256(encoded).hexdigest(),
    }


def subject_identity(repository: Path) -> dict[str, Any]:
    """Return exact Git identity when the verification subject provides it."""

    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
    )
    head = completed.stdout.strip()
    if completed.returncode == 0 and len(head) == 40 and all(
        character in "0123456789abcdef" for character in head
    ):
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repository,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if status.returncode != 0 or status.stdout:
            raise ProbeError("verification subject is not a clean exact Git commit")
        return {"kind": "git-commit", "head_sha": head, "worktree_clean": True}
    return {"kind": "unavailable", "head_sha": None}


def _load_observation(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError("missing or malformed structured pytest observation") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != "AIOS_BP_V4_PYTEST_OBSERVATION"
        or value.get("version") != 1
        or not isinstance(value.get("exit_status"), int)
        or isinstance(value.get("exit_status"), bool)
    ):
        raise ProbeError("incompatible structured pytest observation")
    return value


def run_pytest(
    repository: Path,
    temporary_root: Path,
    *,
    label: str,
    workers: int,
    collect_only: bool,
) -> tuple[int, float, dict[str, Any]]:
    """Run one fixed pytest operation and return its structured observation."""

    observation = temporary_root / f"{label}.json"
    basetemp = temporary_root / f"{label}-tmp"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "xdist.plugin",
        "-p",
        PLUGIN_NAME,
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(basetemp),
        "-q",
    ]
    if collect_only:
        command.append("--collect-only")
    elif workers > 1:
        command.extend(
            [
                "-n",
                str(workers),
                "--dist",
                "load",
                "--max-worker-restart",
                "0",
            ]
        )

    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    tests_path = str(repository / "tests")
    current_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        tests_path + os.pathsep + current_pythonpath
        if current_pythonpath
        else tests_path
    )
    environment[PLUGIN_OUTPUT_ENV] = str(observation)

    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=repository,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    elapsed = time.perf_counter() - started
    return completed.returncode, elapsed, _load_observation(observation)


Runner = Callable[..., tuple[int, float, dict[str, Any]]]


def _serial_conformance(
    observation: dict[str, Any], canonical: dict[str, Any]
) -> dict[str, bool]:
    identity = collection_identity(observation.get("controller_collection"))
    return {"collection_matches_canonical": identity == canonical}


def _observation_exit_status(observation: dict[str, Any]) -> int:
    value = observation.get("exit_status")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProbeError("malformed structured pytest exit status")
    return value


def _failure_diagnostics(observation: dict[str, Any]) -> dict[str, Any]:
    """Validate the plugin's bounded identity-only failure summary."""

    value = observation.get("failure_diagnostics")
    if not isinstance(value, dict):
        raise ProbeError("missing structured pytest failure diagnostics")
    count = value.get("reported_count")
    displayed_count = value.get("displayed_count")
    limit = value.get("display_limit")
    identities = value.get("displayed_identities")
    truncated = value.get("truncated")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(displayed_count, int)
        or isinstance(displayed_count, bool)
        or displayed_count < 0
        or displayed_count > count
        or limit != MAX_FAILURE_IDENTITIES
        or not isinstance(identities, list)
        or displayed_count != len(identities)
        or displayed_count > MAX_FAILURE_IDENTITIES
        or not isinstance(truncated, bool)
    ):
        raise ProbeError("malformed structured pytest failure diagnostics")

    previous: tuple[str, int, str] | None = None
    phase_order = {"setup": 0, "call": 1, "teardown": 2}
    for identity in identities:
        if not isinstance(identity, dict) or set(identity) != {
            "nodeid",
            "phase",
            "clipped",
            "fingerprint",
        }:
            raise ProbeError("malformed pytest failure identity")
        nodeid = identity["nodeid"]
        phase = identity["phase"]
        clipped = identity["clipped"]
        fingerprint = identity["fingerprint"]
        if (
            not isinstance(nodeid, str)
            or not nodeid
            or "\\" in nodeid
            or len(nodeid) > MAX_NODEID_DISPLAY_CHARS
            or phase not in FAILURE_PHASES
            or not isinstance(clipped, bool)
            or (not clipped and fingerprint is not None)
            or (
                clipped
                and (
                    not isinstance(fingerprint, str)
                    or not fingerprint.startswith("sha256:")
                    or len(fingerprint) != 71
                    or any(
                        character not in "0123456789abcdef"
                        for character in fingerprint[7:]
                    )
                    or not nodeid.endswith(f"...#{fingerprint}")
                )
            )
        ):
            raise ProbeError("malformed pytest failure identity")
        key = (nodeid, phase_order[phase], fingerprint or "")
        if previous is not None and key <= previous:
            raise ProbeError("unordered or duplicate pytest failure identities")
        previous = key
    return value


def _profile_result(
    *,
    mode: str,
    workers: int,
    elapsed: float,
    status: int,
    observation: dict[str, Any],
    conformance: dict[str, bool],
) -> dict[str, Any]:
    diagnostics = _failure_diagnostics(observation)
    result: dict[str, Any] = {
        "mode": mode,
        "workers": workers,
        "elapsed_seconds": round(elapsed, 6),
        "exit_status": status,
        "pytest_exit_status": _observation_exit_status(observation),
        "conformance": conformance,
    }
    if status != 0 or result["pytest_exit_status"] != 0:
        result["failure_diagnostics"] = diagnostics
    elif diagnostics["reported_count"] != 0:
        raise ProbeError("successful pytest observation reports failures")
    return result


def _parallel_conformance(
    observation: dict[str, Any], canonical: dict[str, Any], expected: int
) -> dict[str, bool]:
    collections = observation.get("worker_collections")
    workers = observation.get("workers")
    if not isinstance(collections, dict) or not isinstance(workers, dict):
        raise ProbeError("malformed structured xdist worker data")
    if set(collections) != set(workers) or len(workers) != expected:
        raise ProbeError("missing or unexpected xdist worker data")

    temp_roots: list[str] = []
    git_roots: list[str] = []
    process_ids: list[int] = []
    for worker_id in sorted(workers):
        worker = workers[worker_id]
        if not isinstance(worker, dict) or worker.get("worker_id") != worker_id:
            raise ProbeError("malformed structured xdist worker identity")
        if worker.get("collection") != collections[worker_id]:
            raise ProbeError("inconsistent structured xdist worker collection")
        if collection_identity(collections[worker_id]) != canonical:
            raise ProbeError("xdist worker collection does not match canonical collection")
        temp_root = worker.get("temporary_root")
        git_root = worker.get("git_fixture_cache_root")
        process_id = worker.get("process_id")
        if (
            not isinstance(temp_root, str)
            or not temp_root
            or not isinstance(git_root, str)
            or not git_root
            or not isinstance(process_id, int)
            or isinstance(process_id, bool)
        ):
            raise ProbeError("missing structured xdist worker isolation data")
        temp_roots.append(os.path.normcase(os.path.abspath(temp_root)))
        git_roots.append(os.path.normcase(os.path.abspath(git_root)))
        process_ids.append(process_id)

    if (
        len(set(temp_roots)) != expected
        or len(set(git_roots)) != expected
        or len(set(process_ids)) != expected
    ):
        raise ProbeError("xdist workers share mutable roots or process identity")
    return {
        "all_workers_reported": True,
        "collections_match_canonical": True,
        "temporary_roots_isolated": True,
        "git_fixture_cache_roots_isolated": True,
        "processes_distinct": True,
    }
