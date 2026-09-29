"""Measure the explicit BP-V4 rebaseline candidates on one exact subject."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, Sequence

# Allow direct execution from a source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aios_renew.parallel_verification import (
    ProbeError, Runner, collection_identity, load_toolchain, run_pytest,
    subject_identity, _observation_exit_status, _parallel_conformance,
    _profile_result,
)


FORMAT = "AIOS_BP_V4_REBASELINE_MEASUREMENT"
VERSION = 2
WORKERS = (4, 8, 12, 16)
ARGUMENTS = ("--workers", "4", "8", "12", "16")


def parse_workers(values: Sequence[str]) -> tuple[int, int, int, int]:
    """Require the complete authored candidate sequence, without coercion."""

    if tuple(values) != ARGUMENTS[1:]:
        raise ProbeError("workers must be exactly 4 8 12 16 in ascending order")
    return WORKERS


def measure(
    repository: Path,
    workers: tuple[int, ...],
    *,
    runner: Runner = run_pytest,
    toolchain_loader: Callable[[], dict[str, str]] = load_toolchain,
) -> tuple[dict[str, Any], bool]:
    """Collect once, then measure each fixed parallel profile once in order."""

    parse_workers(tuple(str(worker) for worker in workers))
    toolchain = toolchain_loader()
    subject = subject_identity(repository)
    if subject.get("kind") != "git-commit" or subject.get("worktree_clean") is not True:
        raise ProbeError("rebaseline requires a clean exact Git commit")

    profiles: list[dict[str, Any]] = []
    defects: list[str] = []
    with tempfile.TemporaryDirectory(prefix="aios-bp-v4-rebaseline-") as directory:
        root = Path(directory)
        collection_status, _, collection_observation = runner(
            repository, root, label="collection", workers=1, collect_only=True,
        )
        if collection_status != 0 or _observation_exit_status(collection_observation) != 0:
            raise ProbeError("canonical pytest collection failed")
        canonical = collection_identity(collection_observation.get("controller_collection"))
        if subject_identity(repository) != subject:
            raise ProbeError("canonical collection changed the verification subject")

        for candidate in WORKERS:
            label = f"parallel-{candidate}"
            try:
                status, elapsed, observation = runner(
                    repository, root, label=label, workers=candidate,
                    collect_only=False,
                )
                facts = _parallel_conformance(observation, canonical, candidate)
                if subject_identity(repository) != subject:
                    raise ProbeError(f"{label} changed the verification subject")
                facts["subject_unchanged"] = True
                result = _profile_result(
                    mode="parallel", workers=candidate, elapsed=elapsed,
                    status=status, observation=observation, conformance=facts,
                )
                result["correctness_passed"] = (
                    result["exit_status"] == 0
                    and result["pytest_exit_status"] == 0
                )
                result["conformance_passed"] = all(facts.values())
                profiles.append(result)
            except ProbeError as exc:
                defects.append(f"{label}: {exc}")

    if defects:
        raise ProbeError("; ".join(defects))
    success = all(
        profile["correctness_passed"] and profile["conformance_passed"]
        for profile in profiles
    )
    return {
        "format": FORMAT,
        "version": VERSION,
        "subject": subject,
        "toolchain": toolchain,
        "collection": canonical,
        "profiles": profiles,
        "conformance": {
            "explicit_parallel_workers": list(WORKERS),
            "profiles_executed_once_in_authored_order": True,
            "canonical_collection_consistent": all(
                profile["conformance_passed"] for profile in profiles
            ),
            "correctness_passed": all(
                profile["correctness_passed"]
                for profile in profiles
            ),
        },
    }, success


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments != ARGUMENTS:
        print("exact syntax is --workers 4 8 12 16", file=sys.stderr)
        return 2
    try:
        envelope, success = measure(Path.cwd().resolve(), parse_workers(arguments[1:]))
    except ProbeError as exc:
        print(f"BP-V4 rebaseline failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
