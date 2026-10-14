"""Self-contained synthetic regressions for parent-owned observation exports.

Runtime command:
    python -m pytest -q tests/test_parallel_verification_export_boundary.py -k observation_export_boundary
"""

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from aios_renew import parallel_verification as parallel
from aios_renew.verification_contract import verification_digest


EXPORT_ENV = "AIOS_V2_OBSERVATION_DIRECTORY"
SENTINEL_ENV = "AIOS_EXPORT_BOUNDARY_SENTINEL"
PROFILE = {
    "profile": "bounded-parallel-full-suite-v1", "workers": 12,
    "distribution": "load", "max_worker_restart": 0, "collect_only": False,
}
TOOLCHAIN = {
    "python_implementation": "CPython", "python_version": "3.11.0",
    "python_executable": "synthetic-python", "platform_system": "synthetic",
    "platform_machine": "synthetic", "pytest_version": "8.2.0",
    "pytest_xdist_version": "3.6.0",
}


def _observation(*, failing=False):
    nodeid = "tests/test_synthetic.py::test_boundary"
    reports = [
        {"nodeid": nodeid, "phase": phase, "outcome": "PASS"}
        for phase in ("setup", "call", "teardown")
    ]
    identities = []
    if failing:
        detail = "AssertionError: authentic parent diagnostic"
        reports[1].update(
            outcome="FAIL", detail=detail,
            fingerprint=verification_digest(detail),
            profile=PROFILE, toolchain=TOOLCHAIN,
        )
        identities.append({
            "nodeid": nodeid, "phase": "call", "clipped": False, "fingerprint": None,
        })
    return {
        "schema": "AIOS_BP_V4_PYTEST_OBSERVATION", "version": 1,
        "exit_status": int(failing), "profile": PROFILE, "toolchain": TOOLCHAIN,
        "failure_diagnostics": {
            "reported_count": int(failing), "displayed_count": int(failing),
            "display_limit": 20, "displayed_identities": identities, "truncated": False,
            "canonical": {
                "complete": True, "unstable": False,
                "reports": reports, "failure_count": int(failing),
            },
        },
    }


def _encoded(value):
    # Deliberately preserve formatting, not just equivalent decoded JSON.
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


# This temporary pytest module replaces actual pytest only inside the synthetic
# repository. It launches one worker and one nested run_pytest, never a real suite.
SYNTHETIC_PYTEST = r'''
import json
import os
from pathlib import Path
import subprocess
import sys

repository = Path(__file__).resolve().parents[1]
output = Path(os.environ["AIOS_BP_V4_PLUGIN_OUTPUT"])
root = output.parent
snapshot = {
    "export": os.environ.get("AIOS_V2_OBSERVATION_DIRECTORY"),
    "plugin_output": str(output),
    "profile": json.loads(os.environ["AIOS_V2_PROFILE"]),
    "sentinel": os.environ.get("AIOS_EXPORT_BOUNDARY_SENTINEL"),
    "arguments": sys.argv[1:],
}

if sys.argv[1:] == ["worker"]:
    from aios_renew.parallel_verification import run_pytest
    (root / "worker.json").write_text(json.dumps(snapshot), encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    status, _, observation = run_pytest(
        repository, nested, label="parallel-12", workers=12, collect_only=False,
    )
    (root / "nested-result.json").write_text(
        json.dumps({"status": status, "observation": observation}), encoding="utf-8",
    )
elif root.name == "nested":
    (root / "child.json").write_text(json.dumps(snapshot), encoding="utf-8")
    output.write_bytes((repository / "nested-observation.json").read_bytes())
else:
    (root / "child.json").write_text(json.dumps(snapshot), encoding="utf-8")
    subprocess.run([sys.executable, str(Path(__file__).resolve()), "worker"],
                   check=True, timeout=20)
    output.write_bytes((repository / "parent-observation.json").read_bytes())
    raise SystemExit(1)
'''


def test_observation_export_boundary_parent_child_nested_collision(monkeypatch, tmp_path):
    repository = tmp_path / "subject"
    (repository / "src").mkdir(parents=True)
    (repository / "src" / "pytest.py").write_text(SYNTHETIC_PYTEST, encoding="utf-8")
    parent = _observation(failing=True)
    nested = _observation()
    parent_bytes, nested_bytes = _encoded(parent), _encoded(nested)
    (repository / "parent-observation.json").write_bytes(parent_bytes)
    (repository / "nested-observation.json").write_bytes(nested_bytes)
    monkeypatch.setenv("PYTHONPATH", str(Path(parallel.__file__).resolve().parents[1]))
    monkeypatch.setenv(SENTINEL_ENV, "preserved")
    real_run = parallel.subprocess.run

    for inherit_export in (True, False):
        root = tmp_path / ("inherited-control" if inherit_export else "isolated")
        root.mkdir()
        export = root / "export"
        destination = export / "parallel-12.json"
        monkeypatch.setenv(EXPORT_ENV, str(export))
        calls = []

        def run_child(command, **kwargs):
            calls.append((list(command), dict(kwargs["env"])))
            if inherit_export:
                # Recreate the old process boundary: the worker becomes a writer
                # before the parent has exported its own parallel-12 observation.
                kwargs["env"] = {**kwargs["env"], EXPORT_ENV: str(export)}
            return real_run(command, timeout=30, **kwargs)

        monkeypatch.setattr(parallel.subprocess, "run", run_child)
        if inherit_export:
            with pytest.raises(parallel.ProbeError, match="^conflicting immutable pytest observation$"):
                parallel.run_pytest(
                    repository, root, label="parallel-12", workers=12, collect_only=False,
                )
            assert destination.read_bytes() == nested_bytes
        else:
            status, elapsed, loaded = parallel.run_pytest(
                repository, root, label="parallel-12", workers=12, collect_only=False,
            )
            assert status == loaded["exit_status"] == 1
            assert elapsed >= 0
            assert loaded == parent
            result = parallel._profile_result(
                mode="parallel", workers=12, elapsed=elapsed, status=status,
                observation=loaded, conformance={"synthetic": True},
            )
            assert result["failure_diagnostics"] == parent["failure_diagnostics"]
            assert result["canonical"] == parent["failure_diagnostics"]["canonical"]
            assert destination.read_bytes() == parent_bytes
            assert EXPORT_ENV not in calls[0][1]

        assert (root / "parallel-12.json").read_bytes() == parent_bytes
        assert (root / "nested" / "parallel-12.json").read_bytes() == nested_bytes
        assert json.loads((root / "nested-result.json").read_text(encoding="utf-8")) == {
            "status": 0, "observation": nested,
        }
        assert len(calls) == 1  # A conflict is never retried.
        command, environment = calls[0]
        expected_arguments = [
            "-p", "xdist.plugin", "-p", parallel.PLUGIN_NAME,
            "-p", "no:cacheprovider", "--basetemp", str(root / "parallel-12-tmp"),
            "-q", "-n", "12", "--dist", "load", "--max-worker-restart", "0",
        ]
        assert command == [sys.executable, "-m", "pytest", *expected_arguments]
        assert json.loads(environment["AIOS_V2_PROFILE"]) == PROFILE
        for relative in ("child.json", "worker.json", "nested/child.json"):
            snapshot = json.loads((root / relative).read_text(encoding="utf-8"))
            expected_export = str(export) if inherit_export and relative != "nested/child.json" else None
            assert snapshot["export"] == expected_export
            assert snapshot["profile"] == PROFILE
            assert snapshot["sentinel"] == "preserved"
        child = json.loads((root / "child.json").read_text(encoding="utf-8"))
        assert child["arguments"] == expected_arguments
        assert child["plugin_output"] == str(root / "parallel-12.json")
        assert os.environ[EXPORT_ENV] == str(export)
        assert sorted(path.name for path in export.iterdir()) == ["parallel-12.json"]


def test_observation_export_boundary_immutable_conflict_and_invalid_observations(monkeypatch, tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    monkeypatch.setenv(EXPORT_ENV, str(export))
    observation = _observation()
    original = _encoded(observation)
    destination = export / "parallel-12.json"
    destination.write_bytes(original)
    real_write_bytes = Path.write_bytes

    def protect_destination(path, data):
        assert path != destination, "existing immutable exports must never be rewritten"
        return real_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", protect_destination)
    payload = original
    calls = []

    def run_child(command, **kwargs):
        assert EXPORT_ENV not in kwargs["env"]
        calls.append(command)
        if payload is not None:
            Path(kwargs["env"][parallel.PLUGIN_OUTPUT_ENV]).write_bytes(payload)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(parallel.subprocess, "run", run_child)
    root = tmp_path / "equal"
    root.mkdir()
    status, _, loaded = parallel.run_pytest(
        tmp_path, root, label="parallel-12", workers=12, collect_only=False,
    )
    assert status == 0 and loaded == observation
    assert destination.read_bytes() == original

    # Even semantically identical JSON with different raw bytes must conflict.
    payload = original + b"\n"
    root = tmp_path / "conflict"
    root.mkdir()
    with pytest.raises(parallel.ProbeError, match="^conflicting immutable pytest observation$"):
        parallel.run_pytest(tmp_path, root, label="parallel-12", workers=12, collect_only=False)
    assert destination.read_bytes() == original
    assert (root / "parallel-12.json").read_bytes() == payload

    incomplete = _observation()
    incomplete["failure_diagnostics"]["canonical"]["complete"] = False
    missing_diagnostics = _observation()
    del missing_diagnostics["failure_diagnostics"]
    defects = {
        "missing": None, "malformed": b"{", "invalid": b"[]",
        "incomplete": _encoded(incomplete),
        "missing-diagnostics": _encoded(missing_diagnostics),
    }
    for label, payload in defects.items():
        root = tmp_path / label
        root.mkdir()
        with pytest.raises(parallel.ProbeError):
            status, elapsed, loaded = parallel.run_pytest(
                tmp_path, root, label=label, workers=12, collect_only=False,
            )
            parallel._profile_result(
                mode="parallel", workers=12, elapsed=elapsed, status=status,
                observation=loaded, conformance={"synthetic": True},
            )
        assert destination.read_bytes() == original
    assert len(calls) == 2 + len(defects)
    assert os.environ[EXPORT_ENV] == str(export)
