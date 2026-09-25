"""Run the explicitly selected four-worker canonical full-suite profile once."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aios_renew.parallel_verification import (
    ProbeError, Runner, collection_identity, load_toolchain,
    run_pytest, subject_identity, _observation_exit_status,
    _parallel_conformance, _profile_result,
)
from aios_renew.verification_profile import (
    OBSERVATION_FORMAT, load_policy, performance_guard,
)


def execute(
    repository: Path, *, runner: Runner = run_pytest,
    toolchain_loader: Callable[[], dict[str, str]] = load_toolchain,
) -> tuple[dict[str, Any], bool]:
    """One canonical collection followed by one fixed selected profile."""

    profile = load_policy(repository)
    toolchain = toolchain_loader()
    subject = subject_identity(repository)
    if subject.get("kind") != "git-commit" or subject.get("worktree_clean") is not True:
        raise ProbeError("selected verification requires a clean exact Git commit")
    with tempfile.TemporaryDirectory(prefix="aios-parallel-full-suite-") as directory:
        root = Path(directory)
        collection_status, _, collection_observation = runner(
            repository, root, label="collection", workers=1, collect_only=True,
        )
        if collection_status != 0 or _observation_exit_status(collection_observation) != 0:
            raise ProbeError("canonical pytest collection failed")
        canonical = collection_identity(collection_observation.get("controller_collection"))
        if subject_identity(repository) != subject:
            raise ProbeError("canonical collection changed the verification subject")
        status, elapsed, observation = runner(
            repository, root, label="parallel-4", workers=4, collect_only=False,
        )
        facts = _parallel_conformance(observation, canonical, 4)
        if subject_identity(repository) != subject:
            raise ProbeError("selected profile changed the verification subject")
        facts["subject_unchanged"] = True
        result = _profile_result(
            mode="parallel", workers=4, elapsed=elapsed, status=status,
            observation=observation, conformance=facts,
        )
    success = (
        result["exit_status"] == 0
        and result["pytest_exit_status"] == 0
        and all(result["conformance"].values())
    )
    envelope = {
        "format": OBSERVATION_FORMAT,
        "version": 1,
        "subject": subject,
        "toolchain": toolchain,
        "collection": canonical,
        "selected_profile": {
            "profile": profile["profile"], "workers": 4,
            "distribution": "load", "max_worker_restart": 0,
            "selection_provenance": profile["selection_provenance"],
        },
        "result": result,
        "correctness_and_conformance_passed": success,
        "performance_guard": performance_guard(
            profile, canonical, toolchain, result["elapsed_seconds"]
        ),
    }
    return envelope, success


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print("selected full-suite command accepts no arguments", file=sys.stderr)
        return 2
    try:
        envelope, success = execute(Path.cwd().resolve())
    except ProbeError as exc:
        print(f"selected full-suite failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
