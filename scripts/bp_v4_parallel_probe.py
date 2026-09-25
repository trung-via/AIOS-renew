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
# Allow direct script execution from a source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


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


# Shared primitives retain the historical probe's public import surface.
from aios_renew.parallel_verification import (
    ProbeError, load_toolchain, collection_identity, subject_identity,
    run_pytest, Runner, _serial_conformance, _observation_exit_status,
    _failure_diagnostics, _profile_result, _parallel_conformance,
    PLUGIN_OUTPUT_ENV, MAX_FAILURE_IDENTITIES, MAX_NODEID_DISPLAY_CHARS,
)


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
                _profile_result(
                    mode="serial",
                    workers=1,
                    elapsed=serial_elapsed,
                    status=serial_status,
                    observation=serial_observation,
                    conformance=serial_facts,
                )
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
                    _profile_result(
                        mode="parallel",
                        workers=candidate,
                        elapsed=elapsed,
                        status=status,
                        observation=observation,
                        conformance=facts,
                    )
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
