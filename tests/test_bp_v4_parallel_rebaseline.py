"""Focused checks for the fixed BP-V4 rebaseline experiment."""

from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from scripts import bp_v4_parallel_rebaseline as rebaseline
from test_bp_v4_parallel_probe import observation
import bp_v4_probe_plugin as plugin
from aios_renew.parallel_verification import (
    MAX_FAILURE_IDENTITIES, PLUGIN_OUTPUT_ENV, run_pytest,
)


SUBJECT = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
TOOLCHAIN = {
    "python_implementation": "CPython", "python_version": "3.11.0",
    "python_executable": "C:/Python/python.exe", "platform_system": "Windows",
    "platform_machine": "AMD64", "pytest_version": "8.2.0",
    "pytest_xdist_version": "3.6.0",
}
EXPECTED_CALLS = [
    ("collection", 1, True),
    ("parallel-4", 4, False),
    ("parallel-8", 8, False),
    ("parallel-12", 12, False),
    ("parallel-16", 16, False),
]


@pytest.mark.parametrize("values", [
    (), ("4",), ("4", "8", "12"), ("8", "4", "12", "16"),
    ("4", "8", "8", "16"), ("4", "8", "12", "16", "20"),
    ("auto",), ("4", "8", "12", "16;echo"),
])
def test_candidate_grammar_rejects_every_nonexact_sequence(values) -> None:
    with pytest.raises(rebaseline.ProbeError):
        rebaseline.parse_workers(values)


@pytest.mark.parametrize("arguments", [
    (), ("--workers",), ("--workers=4", "8", "12", "16"),
    ("--workers", "4", "8", "12"),
    ("--workers", "4", "8", "12", "16", "--extra"),
    ("--workers", "4", "8", "12", "16", "&&", "echo"),
])
def test_cli_rejects_malformed_input_before_measurement(monkeypatch, arguments) -> None:
    monkeypatch.setattr(rebaseline, "measure", lambda *a, **k: pytest.fail("measured"))
    assert rebaseline.main(arguments) == 2


def test_one_collection_and_four_profiles_bind_subject_toolchain_and_collection(
    monkeypatch,
) -> None:
    calls = []
    subjects = []

    def subject(_):
        subjects.append(SUBJECT)
        return dict(SUBJECT)

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        assert temporary_root.is_dir()
        return 0, {"parallel-4": 2.0, "parallel-8": 9.0}.get(label, 1.0), observation(
            workers=0 if collect_only else workers
        )

    monkeypatch.setattr(rebaseline, "subject_identity", subject)
    envelope, success = rebaseline.measure(
        Path.cwd(), (4, 8, 12, 16), runner=runner,
        toolchain_loader=lambda: dict(TOOLCHAIN),
    )
    assert calls == EXPECTED_CALLS
    assert len(subjects) == 6
    assert success is True
    assert envelope["format"] == rebaseline.FORMAT
    assert envelope["version"] == 2
    assert envelope["subject"] == SUBJECT
    assert envelope["toolchain"] == TOOLCHAIN
    assert envelope["collection"] == rebaseline.collection_identity(
        observation()["controller_collection"]
    )
    assert [profile["workers"] for profile in envelope["profiles"]] == [4, 8, 12, 16]
    assert envelope["profiles"][1]["elapsed_seconds"] > envelope["profiles"][0]["elapsed_seconds"]
    assert all(profile["conformance"]["subject_unchanged"] for profile in envelope["profiles"])
    assert all(profile["correctness_passed"] and profile["conformance_passed"] for profile in envelope["profiles"])

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert {"selected", "winner", "recommended", "best", "score", "adaptive"}.isdisjoint(keys(envelope))


def test_each_parallel_invocation_uses_load_and_zero_restart(monkeypatch, tmp_path) -> None:
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        Path(kwargs["env"][PLUGIN_OUTPUT_ENV]).write_text(
            json.dumps(observation(workers=int(command[command.index("-n") + 1]))),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("aios_renew.parallel_verification.subprocess.run", run)
    for workers in rebaseline.WORKERS:
        run_pytest(
            tmp_path, tmp_path, label=f"parallel-{workers}",
            workers=workers, collect_only=False,
        )
    assert [command[-6:] for command in commands] == [
        ["-n", str(workers), "--dist", "load", "--max-worker-restart", "0"]
        for workers in rebaseline.WORKERS
    ]


def test_pytest_failure_is_bounded_and_timing_is_neutral(monkeypatch) -> None:
    identity = plugin._failure_identity("tests\\test_bad.py::test_broken", "call")
    diagnostics = {
        "reported_count": 1, "displayed_count": 1,
        "display_limit": MAX_FAILURE_IDENTITIES,
        "displayed_identities": [identity], "truncated": False,
    }

    def runner(repository, temporary_root, *, label, workers, collect_only):
        failed = label == "parallel-8"
        return (1 if failed else 0), (0.01 if failed else 100.0), observation(
            workers=0 if collect_only else workers,
            exit_status=1 if failed else 0,
            failures=diagnostics if failed else None,
        )

    monkeypatch.setattr(rebaseline, "subject_identity", lambda _: dict(SUBJECT))
    envelope, success = rebaseline.measure(
        Path.cwd(), rebaseline.WORKERS, runner=runner,
        toolchain_loader=lambda: dict(TOOLCHAIN),
    )
    assert success is False
    assert len(envelope["profiles"]) == 4
    assert envelope["profiles"][1]["failure_diagnostics"] == diagnostics
    assert all("failure_diagnostics" not in profile for profile in envelope["profiles"] if profile["workers"] != 8)
    assert envelope["profiles"][1]["elapsed_seconds"] < envelope["profiles"][0]["elapsed_seconds"]


@pytest.mark.parametrize("defect", [
    "collection", "missing-worker", "temporary-root", "git-root", "process-id",
    "malformed-observation", "malformed-failure",
])
def test_parallel_defect_fails_closed_after_all_profiles(monkeypatch, defect) -> None:
    calls = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        data = observation(workers=0 if collect_only else workers)
        if label == "parallel-8":
            if defect == "collection":
                data["worker_collections"]["gw1"] = ["tests/other.py::test_other"]
                data["workers"]["gw1"]["collection"] = ["tests/other.py::test_other"]
            elif defect == "missing-worker":
                del data["workers"]["gw1"]
            elif defect == "temporary-root":
                data["workers"]["gw1"]["temporary_root"] = data["workers"]["gw0"]["temporary_root"]
            elif defect == "git-root":
                data["workers"]["gw1"]["git_fixture_cache_root"] = data["workers"]["gw0"]["git_fixture_cache_root"]
            elif defect == "process-id":
                data["workers"]["gw1"]["process_id"] = data["workers"]["gw0"]["process_id"]
            elif defect == "malformed-observation":
                data["workers"] = None
            else:
                data["failure_diagnostics"] = None
        return 0, 0.1, data

    monkeypatch.setattr(rebaseline, "subject_identity", lambda _: dict(SUBJECT))
    with pytest.raises(rebaseline.ProbeError, match="parallel-8"):
        rebaseline.measure(
            Path.cwd(), rebaseline.WORKERS, runner=runner,
            toolchain_loader=lambda: dict(TOOLCHAIN),
        )
    assert calls == EXPECTED_CALLS


def test_subject_change_fails_closed(monkeypatch) -> None:
    subjects = [dict(SUBJECT) for _ in range(6)]
    subjects[3] = {**SUBJECT, "head_sha": "b" * 40}
    monkeypatch.setattr(rebaseline, "subject_identity", lambda _: subjects.pop(0))

    def runner(repository, temporary_root, *, label, workers, collect_only):
        return 0, 0.1, observation(workers=0 if collect_only else workers)

    with pytest.raises(rebaseline.ProbeError, match="changed the verification subject"):
        rebaseline.measure(
            Path.cwd(), rebaseline.WORKERS, runner=runner,
            toolchain_loader=lambda: dict(TOOLCHAIN),
        )
