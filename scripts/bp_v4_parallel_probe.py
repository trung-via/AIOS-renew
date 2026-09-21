"""Run one bounded serial/parallel BP-V4 full-suite measurement experiment."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Sequence


FORMAT = "AIOS_BP_V4_MEASUREMENT"
VERSION = 1
SUPPORTED_WORKERS = (2, 3, 4)
PYTEST_RANGE = ((8, 2), (9, 0))
XDIST_RANGE = ((3, 6), (4, 0))
PLUGIN_NAME = "bp_v4_probe_plugin"
PLUGIN_OUTPUT_ENV = "AIOS_BP_V4_PLUGIN_OUTPUT"


class ProbeError(RuntimeError):
    """A fail-closed probe construction or observation error."""


def parse_workers(values: Sequence[str]) -> tuple[int, ...]:
    """Validate one to three explicit, unique, ascending candidates."""

    if not 1 <= len(values) <= 3 or any(
        value not in {str(worker) for worker in SUPPORTED_WORKERS}
        for value in values
    ):
        raise ProbeError("workers must be one to three values drawn from 2, 3, and 4")
    workers = tuple(int(value) for value in values)
    if workers != tuple(sorted(set(workers))):
        raise ProbeError("workers must be unique and ascending")
    return workers


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
            "BP-V4 measurement requires the optional bp-v4-measurement extra"
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


def measure(
    repository: Path,
    workers: tuple[int, ...],
    *,
    runner: Runner = run_pytest,
    toolchain_loader: Callable[[], dict[str, str]] = load_toolchain,
) -> tuple[dict[str, Any], bool]:
    """Execute exactly one canonical collection and each authorized profile."""

    workers = parse_workers(tuple(str(worker) for worker in workers))
    toolchain = toolchain_loader()
    subject = subject_identity(repository)
    profiles: list[dict[str, Any]] = []
    profile_defects: list[str] = []
    with tempfile.TemporaryDirectory(prefix="aios-bp-v4-") as directory:
        temporary_root = Path(directory)
        collection_status, _collection_elapsed, collection_observation = runner(
            repository,
            temporary_root,
            label="collection",
            workers=1,
            collect_only=True,
        )
        if (
            collection_status != 0
            or _observation_exit_status(collection_observation) != 0
        ):
            raise ProbeError("canonical pytest collection failed")
        canonical = collection_identity(
            collection_observation.get("controller_collection")
        )
        if subject_identity(repository) != subject:
            raise ProbeError("canonical collection changed the verification subject")

        try:
            serial_status, serial_elapsed, serial_observation = runner(
                repository,
                temporary_root,
                label="serial",
                workers=1,
                collect_only=False,
            )
            serial_facts = _serial_conformance(serial_observation, canonical)
            if subject_identity(repository) != subject:
                raise ProbeError("serial profile changed the verification subject")
            serial_facts["subject_unchanged"] = True
            profiles.append(
                {
                    "mode": "serial",
                    "workers": 1,
                    "elapsed_seconds": round(serial_elapsed, 6),
                    "exit_status": serial_status,
                    "pytest_exit_status": _observation_exit_status(
                        serial_observation
                    ),
                    "conformance": serial_facts,
                }
            )
        except ProbeError as exc:
            profile_defects.append(f"serial: {exc}")

        for candidate in workers:
            try:
                status, elapsed, observation = runner(
                    repository,
                    temporary_root,
                    label=f"parallel-{candidate}",
                    workers=candidate,
                    collect_only=False,
                )
                facts = _parallel_conformance(observation, canonical, candidate)
                if subject_identity(repository) != subject:
                    raise ProbeError(
                        f"parallel-{candidate} changed the verification subject"
                    )
                facts["subject_unchanged"] = True
                profiles.append(
                    {
                        "mode": "parallel",
                        "workers": candidate,
                        "elapsed_seconds": round(elapsed, 6),
                        "exit_status": status,
                        "pytest_exit_status": _observation_exit_status(observation),
                        "conformance": facts,
                    }
                )
            except ProbeError as exc:
                profile_defects.append(f"parallel-{candidate}: {exc}")

    if profile_defects:
        raise ProbeError("; ".join(profile_defects))

    success = all(
        profile["exit_status"] == 0
        and profile["pytest_exit_status"] == 0
        and all(profile["conformance"].values())
        for profile in profiles
    )
    envelope = {
        "format": FORMAT,
        "version": VERSION,
        "subject": subject,
        "toolchain": toolchain,
        "collection": canonical,
        "profiles": profiles,
        "conformance": {
            "authorized_parallel_workers": list(workers),
            "profiles_executed_once_in_authorized_order": True,
            "canonical_collection_consistent": all(
                all(profile["conformance"].values()) for profile in profiles
            ),
            "correctness_passed": all(
                profile["exit_status"] == 0
                and profile["pytest_exit_status"] == 0
                for profile in profiles
            ),
        },
    }
    return envelope, success


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one bounded BP-V4 serial/parallel measurement experiment."
    )
    parser.add_argument("--workers", nargs="+", required=True, metavar="N")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if not 2 <= len(raw_arguments) <= 4 or raw_arguments[0] != "--workers":
        parser.error("exact syntax is --workers followed by one to three candidates")
    try:
        workers = parse_workers(raw_arguments[1:])
    except ProbeError as exc:
        parser.error(str(exc))
    try:
        envelope, success = measure(Path.cwd().resolve(), workers)
    except ProbeError as exc:
        print(f"BP-V4 probe failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
