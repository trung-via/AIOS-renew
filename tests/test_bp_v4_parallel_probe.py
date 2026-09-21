"""Focused tests for the bounded BP-V4 measurement primitive."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bp_v4_parallel_probe as probe


NODEIDS = ["tests/test_b.py::test_two", "tests\\test_a.py::test_one"]


def observation(
    *,
    workers: int = 0,
    nodeids: list[str] | None = None,
    exit_status: int = 0,
    shared_root: bool = False,
) -> dict[str, object]:
    collection = NODEIDS if nodeids is None else nodeids
    worker_collections = {
        f"gw{index}": list(collection) for index in range(workers)
    }
    worker_data = {
        f"gw{index}": {
            "worker_id": f"gw{index}",
            "process_id": 100 + index,
            "temporary_root": "root/shared" if shared_root else f"root/tmp-{index}",
            "git_fixture_cache_root": (
                "root/git-shared" if shared_root else f"root/git-{index}"
            ),
            "collection": list(collection),
        }
        for index in range(workers)
    }
    return {
        "schema": "AIOS_BP_V4_PYTEST_OBSERVATION",
        "version": 1,
        "exit_status": exit_status,
        "controller_collection": list(collection) if workers == 0 else None,
        "worker_collections": worker_collections,
        "workers": worker_data,
    }


@pytest.mark.parametrize("values", [[], ["1"], ["auto"], ["2", "2"], ["3", "2"], ["4", "5"], ["2", "&&"]])
def test_workers_reject_defaults_duplicates_order_and_injection(values) -> None:
    with pytest.raises(probe.ProbeError):
        probe.parse_workers(values)


@pytest.mark.parametrize(
    "arguments",
    [[], ["--workers=2"], ["--workers", "2", "--"], ["--workers", "2", "--workers", "3"]],
)
def test_cli_rejects_noncanonical_construction_before_measurement(
    arguments, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def measure(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("measurement must not start")

    monkeypatch.setattr(probe, "measure", measure)
    with pytest.raises(SystemExit):
        probe.main(arguments)
    assert called is False


def test_collection_identity_is_normalized_and_deterministic() -> None:
    first = probe.collection_identity(NODEIDS)
    second = probe.collection_identity(list(reversed(NODEIDS)))

    assert first == second
    assert first["count"] == 2
    assert first["rule"] == "sorted-posix-nodeid-lf-sha256-v1"
    assert len(first["digest"]) == 64


def test_pytest_command_is_fixed_and_uses_only_requested_workers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        Path(kwargs["env"][probe.PLUGIN_OUTPUT_ENV]).write_text(
            json.dumps(observation(workers=3)), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(probe.subprocess, "run", run)
    status, _elapsed, _payload = probe.run_pytest(
        tmp_path,
        tmp_path,
        label="parallel-3",
        workers=3,
        collect_only=False,
    )

    assert status == 0
    assert captured["command"][-6:] == [
        "-n",
        "3",
        "--dist",
        "load",
        "--max-worker-restart",
        "0",
    ]
    assert "auto" not in captured["command"]
    assert captured["environment"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_missing_optional_toolchain_fails_without_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(probe.importlib.metadata, "version", missing)
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("measurement must not start")

    with pytest.raises(probe.ProbeError, match="optional bp-v4-measurement"):
        probe.measure(Path.cwd(), (2,), runner=runner)
    assert called is False


def test_one_collection_and_each_profile_execute_once_in_authored_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int, bool]] = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        del repository, temporary_root
        calls.append((label, workers, collect_only))
        return 0, {"serial": 9.0, "parallel-2": 12.0, "parallel-4": 7.0}.get(label, 0.1), observation(workers=0 if workers == 1 else workers)

    monkeypatch.setattr(
        probe, "subject_identity", lambda _repository: {"kind": "git-commit", "head_sha": "a" * 40}
    )
    envelope, success = probe.measure(
        Path.cwd(),
        (2, 4),
        runner=runner,
        toolchain_loader=lambda: {
            "python_implementation": "CPython",
            "python_version": "3.11.0",
            "pytest_version": "8.2.0",
            "pytest_xdist_version": "3.6.0",
        },
    )

    assert calls == [
        ("collection", 1, True),
        ("serial", 1, False),
        ("parallel-2", 2, False),
        ("parallel-4", 4, False),
    ]
    assert success is True
    assert envelope["format"] == "AIOS_BP_V4_MEASUREMENT"
    assert envelope["version"] == 1
    assert [item["workers"] for item in envelope["profiles"]] == [1, 2, 4]
    # A slower parallel profile is still a successful observation.
    assert envelope["profiles"][1]["elapsed_seconds"] > envelope["profiles"][0]["elapsed_seconds"]
    forbidden = {"selected", "recommended", "best", "winner", "score", "adaptive"}

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert forbidden.isdisjoint(keys(envelope))


def test_test_failure_changes_outcome_but_timing_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def runner(repository, temporary_root, *, label, workers, collect_only):
        del repository, temporary_root, collect_only
        failed = label == "parallel-2"
        return (1 if failed else 0), 0.01, observation(
            workers=0 if workers == 1 else workers,
            exit_status=1 if failed else 0,
        )

    monkeypatch.setattr(probe, "subject_identity", lambda _repository: {"kind": "unavailable", "head_sha": None})
    _envelope, success = probe.measure(
        Path.cwd(),
        (2,),
        runner=runner,
        toolchain_loader=lambda: {},
    )
    assert success is False


@pytest.mark.parametrize("defect", ["collection", "shared-roots", "missing-worker"])
def test_parallel_conformance_fails_closed(defect: str) -> None:
    canonical = probe.collection_identity(NODEIDS)
    data = observation(workers=2, shared_root=defect == "shared-roots")
    if defect == "collection":
        data["worker_collections"]["gw1"] = ["tests/test_other.py::test_other"]
        data["workers"]["gw1"]["collection"] = ["tests/test_other.py::test_other"]
    elif defect == "missing-worker":
        del data["workers"]["gw1"]

    with pytest.raises(probe.ProbeError):
        probe._parallel_conformance(data, canonical, 2)


def test_profile_conformance_defect_does_not_skip_later_authorized_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        del repository, temporary_root, collect_only
        calls.append(label)
        return 0, 0.01, observation(
            workers=0 if workers == 1 else workers,
            shared_root=label == "parallel-2",
        )

    monkeypatch.setattr(
        probe,
        "subject_identity",
        lambda _repository: {"kind": "git-commit", "head_sha": "a" * 40},
    )
    with pytest.raises(probe.ProbeError, match="parallel-2"):
        probe.measure(
            Path.cwd(),
            (2, 3),
            runner=runner,
            toolchain_loader=lambda: {},
        )
    assert calls == ["collection", "serial", "parallel-2", "parallel-3"]


def test_incompatible_toolchain_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    versions = {"pytest": "9.0.0", "pytest-xdist": "3.6.0"}
    monkeypatch.setattr(probe.importlib.metadata, "version", versions.__getitem__)
    with pytest.raises(probe.ProbeError, match="incompatible pytest version"):
        probe.load_toolchain()
