"""Local contract tests for the separate, fixed BP-V4 diagnostic."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from aios_renew.correction_integration import CorrectionIntegrationError
from scripts import bp_v4_parallel_diagnostic as diagnostic
import bp_v4_diagnostic_plugin as plugin


def path_fact(path: str) -> dict[str, object]:
    return {"path": path, "length": 80}


def observation(workers: int = 0, failures: list[dict] | None = None, status: int = 0) -> dict:
    worker_data = {
        f"gw{i}": {
            "worker_id": f"gw{i}", "process_id": 200 + i,
            "temporary_root": path_fact(f"<pytest_basetemp>/popen-gw{i}"),
            "git_fixture_cache_root": path_fact(f"<diagnostic_temp>/parallel-{workers}/aios-git-fixtures-{i}"),
            "collection": list(diagnostic.TARGETS),
        }
        for i in range(workers)
    }
    return {
        "schema": "AIOS_BP_V4_DIAGNOSTIC_PYTEST_OBSERVATION", "version": 1,
        "exit_status": status,
        "controller_collection": list(diagnostic.TARGETS) if workers == 0 else None,
        "worker_collections": {key: list(diagnostic.TARGETS) for key in worker_data},
        "workers": worker_data, "failures": failures or [],
        "worker_failure_reports": sorted(worker_data),
        "executed_nodeids": list(diagnostic.TARGETS),
        "worker_execution_reports": sorted(worker_data),
        "controller_process_id": 100,
        "controller_temporary_root": path_fact("<pytest_basetemp>"),
        "controller_git_fixture_cache_root": path_fact(f"<diagnostic_temp>/{'parallel-' + str(workers) if workers else 'serial'}/aios-git-fixtures-controller"),
    }


def test_fixed_profiles_preserve_failed_test_as_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    target = diagnostic.TARGETS[0]
    failure = {"nodeid": target, "phase": "call", "cause": {
        "exception_type": "AssertionError", "message_fingerprint": "sha256:" + "a" * 64,
    }}

    def runner(_repository, _root, *, label, workers, collect_only):
        calls.append((label, workers, collect_only))
        failed = label == "parallel-2"
        return (1 if failed else 0), observation(
            workers if workers > 1 else 0,
            [failure] if failed else [], 1 if failed else 0,
        )

    monkeypatch.setattr(diagnostic, "subject_identity", lambda _path: {
        "kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True,
    })
    envelope = diagnostic.diagnose(Path.cwd(), runner=runner, loader=lambda: {})
    assert calls == [("collection", 1, True), ("serial", 1, False),
                     ("parallel-2", 2, False), ("parallel-4", 4, False)]
    assert envelope["format"] == diagnostic.FORMAT
    assert envelope["targets"] == list(diagnostic.TARGETS)
    assert envelope["comparisons"]["parallel_only_nodes"] == [target]
    assert envelope["profiles"][1]["pytest_exit_status"] == 1
    assert set(envelope["comparisons"]) == {
        "common_failed_nodes", "serial_only_nodes", "parallel_only_nodes",
        "worker2_only_nodes", "worker4_only_nodes",
    }


@pytest.mark.parametrize("arguments", [["--workers", "2"], ["-k", "foo"], [diagnostic.TARGETS[0]]])
def test_cli_rejects_all_parameters_before_execution(arguments, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostic, "diagnose", lambda *_args: pytest.fail("diagnostic started"))
    assert diagnostic.main(arguments) == 2


@pytest.mark.parametrize("defect", ["missing", "duplicate-pid", "shared-cache", "collection", "path"])
def test_worker_integrity_fails_closed(defect: str) -> None:
    value = observation(2)
    if defect == "missing":
        del value["workers"]["gw1"]
    elif defect == "duplicate-pid":
        value["workers"]["gw1"]["process_id"] = 200
    elif defect == "shared-cache":
        value["workers"]["gw1"]["git_fixture_cache_root"] = value["workers"]["gw0"]["git_fixture_cache_root"]
    elif defect == "collection":
        value["worker_collections"]["gw1"] = ["unexpected"]
    else:
        value["workers"]["gw1"]["temporary_root"]["path"] = "C:/private/secret"
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._profile("parallel-2", 2, 0, value, diagnostic.collection_identity(list(diagnostic.TARGETS)))


def test_plugin_sanitizes_path_and_keeps_only_typed_cause(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    roots = {"subject": str(tmp_path / "subject"), "diagnostic_temp": str(tmp_path / "diagnostic"),
             "pytest_basetemp": str(tmp_path / "diagnostic" / "pytest"), "user_home": str(tmp_path / "home")}
    monkeypatch.setenv(plugin.PREFIX_ENV, json.dumps(roots))
    filename = str(tmp_path / "diagnostic" / "pytest" / "long" / "file")
    cause = plugin._cause(FileNotFoundError(2, "secret message", filename))
    assert cause == {
        "exception_type": "FileNotFoundError",
        "message_fingerprint": cause["message_fingerprint"],
        "errno": 2, "winerror": None, "filename": "<pytest_basetemp>/long/file",
        "filename_length": len(filename),
    }
    assert plugin.sanitize_path(str(tmp_path / "other" / "token")) is None
    git = plugin._cause(subprocess.CalledProcessError(128, ["git", "fetch"], stderr=b"credential=secret"))
    assert git["command_kind"] == "git"
    assert git["git_stderr_excerpt"] == "other"
    assert "credential=secret" not in json.dumps(git)


def test_cause_fingerprints_ignore_transient_roots_and_git_detail_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    causes = []
    root_fingerprints = []
    for run in ("first", "second"):
        root = tmp_path / run
        roots = {"subject": str(root / "subject"), "diagnostic_temp": str(root / "diagnostic"),
                 "pytest_basetemp": str(root / "diagnostic" / "pytest"), "user_home": str(root / "home")}
        monkeypatch.setenv(plugin.PREFIX_ENV, json.dumps(roots))
        fingerprints = {}
        for name, registered_root in roots.items():
            root_path = str(Path(registered_root) / "fixture" / "index.lock")
            escaped_root_path = root_path.replace("\\", "\\\\") if os.name == "nt" else root_path.replace("/", "\\\\")
            fingerprint = plugin._cause(FileNotFoundError(2, "missing", root_path))["message_fingerprint"]
            assert plugin._cause(FileNotFoundError(2, "missing", escaped_root_path))["message_fingerprint"] == fingerprint
            fingerprints[name] = fingerprint
        root_fingerprints.append(fingerprints)
        path = root / "diagnostic" / "pytest" / "fixture" / "index.lock"
        os_cause = plugin._cause(FileNotFoundError(2, "missing", str(path)))
        distinct_cause = plugin._cause(FileNotFoundError(2, "different missing detail", str(path)))
        assert distinct_cause["message_fingerprint"] != os_cause["message_fingerprint"]
        git_cause = plugin._cause(subprocess.CalledProcessError(
            128, ["git", "update-ref", str(path)],
            stderr=f"fatal: cannot lock ref 'refs/heads/main': Unable to create '{path}': File exists\ncredential=secret".encode(),
        ))
        causes.append((os_cause, git_cause))
        assert git_cause["git_stderr_excerpt"] == "ref-lock"
        assert git_cause["git_stderr_truncated"] is False
        assert "secret" not in json.dumps(git_cause)
        assert diagnostic._cause(git_cause) == git_cause
    assert causes[0][0]["message_fingerprint"] == causes[1][0]["message_fingerprint"]
    assert root_fingerprints[0] == root_fingerprints[1]
    assert causes[0][1]["message_fingerprint"] == causes[1][1]["message_fingerprint"]
    assert causes[0][1]["git_stderr_fingerprint"] == causes[1][1]["git_stderr_fingerprint"]

    for stderr, label in (("fatal: not a git repository", "repository-error"),
                          ("fatal: pathspec 'missing' did not match any files", "path-error")):
        cause = plugin._cause(subprocess.CalledProcessError(128, ["git", "status"], stderr=stderr * 100))
        assert cause["git_stderr_excerpt"] == label
        assert cause["git_stderr_truncated"] is True
        assert len(cause["git_stderr_excerpt"]) <= 1024
        assert diagnostic._cause(cause) == cause


def test_command_is_fixed_and_profile_temp_is_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        Path(kwargs["env"][diagnostic.OUTPUT_ENV]).write_text(json.dumps(observation(4)), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(diagnostic.subprocess, "run", run)
    status, _ = diagnostic.run_pytest(tmp_path, tmp_path, label="parallel-4", workers=4, collect_only=False)
    assert status == 0
    assert captured["command"][-len(diagnostic.TARGETS):] == list(diagnostic.TARGETS)
    assert captured["command"].count("--max-worker-restart") == 1
    assert captured["environment"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert captured["environment"]["TMP"] == str(tmp_path / "parallel-4")


def test_cause_validator_rejects_raw_message_and_arbitrary_stderr() -> None:
    cause = {"exception_type": "CalledProcessError", "message_fingerprint": "sha256:" + "0" * 64,
             "returncode": 128, "command_kind": "git", "stderr": "secret"}
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._cause(cause)
    cause.pop("stderr")
    cause.update({"git_stderr_fingerprint": "sha256:" + "0" * 64,
                  "git_stderr_excerpt": "credential=secret", "git_stderr_truncated": False})
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._cause(cause)


def test_wrapped_integration_facts_are_bounded_and_normalized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    observed = []
    for run in ("first", "second"):
        root = tmp_path / run
        monkeypatch.setenv(plugin.PREFIX_ENV, json.dumps({
            "subject": str(root / "subject"), "diagnostic_temp": str(root / "diagnostic"),
            "pytest_basetemp": str(root / "diagnostic" / "pytest"), "user_home": str(root / "home"),
        }))
        path = root / "diagnostic" / "pytest" / "fixture" / "index.lock"
        message = f"Git command failed: fatal: cannot lock ref 'refs/heads/main': Unable to create '{path}': File exists credential=secret"
        cause = plugin._cause(CorrectionIntegrationError(message))
        assert cause["integration_boundary"] == "git-command"
        assert cause["integration_git_diagnostic"] == "ref-lock"
        assert "secret" not in json.dumps(cause)
        assert str(root) not in json.dumps(cause)
        assert diagnostic._cause(cause) == cause
        observed.append(cause)
    assert observed[0]["message_fingerprint"] == observed[1]["message_fingerprint"]
    assert observed[0]["integration_detail_fingerprint"] == observed[1]["integration_detail_fingerprint"]
    assert plugin._cause(CorrectionIntegrationError(
        "Git command failed: fatal: not a git repository"))["integration_git_diagnostic"] == "repository-error"
    assert plugin._cause(CorrectionIntegrationError(
        "Git command failed: fatal: cannot lock ref: The filename or extension is too long"))[
            "integration_git_diagnostic"] == "path-error"
    for detail in ("The filename or extension is too long", "The system cannot find the path specified",
                   "unable to open C:\\private\\secret.txt", "File name too long"):
        cause = plugin._cause(CorrectionIntegrationError(f"failed to create commit-tree: {detail}"))
        assert cause["integration_boundary"] == "commit-tree"
        assert cause["integration_git_diagnostic"] == "path-error"
        assert detail not in json.dumps(cause)
        assert diagnostic._cause(cause) == cause
    unknown = plugin._cause(CorrectionIntegrationError(
        "credential=secret https://example.invalid/private C:\\private\\secret.txt"))
    assert unknown["integration_boundary"] == "other"
    assert set(unknown) == {"exception_type", "message_fingerprint", "integration_boundary"}
    assert plugin._cause(CorrectionIntegrationError(
        "authorized main SHA is required by a future rule"))["integration_boundary"] == "other"


@pytest.mark.parametrize("message,boundary", [
    ("Git command failed: fatal: not a git repository", "git-command"),
    ("failed to create commit-tree: fatal: unable to create file", "commit-tree"),
    ("failed to push integration ref refs/aios/x to remote origin: fatal: cannot lock ref", "push-ref"),
    ("merge conflict between cumulative tip and main: fatal: conflict", "merge-conflict"),
    ("failed to resolve canonical remote task lifecycle: secret", "remote-lifecycle"),
    ("failed to decode canonical remote task lifecycle: secret", "remote-lifecycle"),
    ("canonical remote task lifecycle has no main SHA", "remote-lifecycle"),
    ("canonical remote main SHA (a) does not match authorized main SHA (b)", "remote-lifecycle"),
    ("TASK document not found: TASK-168", "task-document"),
    ("cannot load TASK TASK-168: secret", "task-document"),
    ("TASK identity or revision mismatch: requested TASK-168 r2, found TASK-168 r1", "task-document"),
    ("canonical operational tip RUN-1 is not a valid RESULT terminal (found FAILURE)", "lineage"),
    ("cumulative correction lineage has competing continuations", "lineage"),
    ("canonical remote task lifecycle has no operational tips", "lineage"),
    ("canonical remote task lifecycle has competing operational tips: ['RUN-1']", "lineage"),
    ("cumulative tip selector is stale: expected RUN-1 (a), authorized RUN-2 (b)", "selector"),
    ("cumulative tip candidate commit a is missing or not a commit", "commit-object"),
    ("authorized main commit a is missing or not a commit", "commit-object"),
    ("existing integration ref refs/aios/x collides with a different or corrupted candidate commit", "ref-collision"),
    ("canonical remote integration ref verification failed: expected a for refs/aios/x on origin", "remote-ref"),
    ("cannot find merge base between cumulative tip and main", "merge-base"),
    ("ambiguous or missing merge base between cumulative tip and main (found 2)", "merge-base"),
    ("git merge-tree produced invalid tree SHA", "merge-tree"),
    ("authorized main SHA is required", "input"),
    ("invalid authorized main SHA: secret", "input"),
    ("invalid cumulative tip candidate SHA: secret", "input"),
    ("invalid cumulative tip RUN id: secret", "input"),
    ("invalid task revision: 0", "input"),
])
def test_known_correction_integration_messages_have_bounded_categories(message: str, boundary: str) -> None:
    cause = plugin._cause(CorrectionIntegrationError(message))
    assert cause["integration_boundary"] == boundary
    assert diagnostic._cause(cause) == cause
    assert message not in json.dumps(cause)


def test_source_locus_is_one_fixed_repo_test_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    subject = tmp_path / "subject"
    monkeypatch.setenv(plugin.PREFIX_ENV, json.dumps({
        "subject": str(subject), "diagnostic_temp": str(tmp_path / "diagnostic"),
        "pytest_basetemp": str(tmp_path / "diagnostic" / "pytest"), "user_home": str(tmp_path / "home"),
    }))
    target = diagnostic.TARGETS[0]
    target_path = subject / target.split("::", 1)[0]
    frames = [SimpleNamespace(path=target_path, lineno=41),
              SimpleNamespace(path=tmp_path / "private" / "secret.py", lineno=99)]
    cause = plugin._cause(AssertionError("credential=secret"),
                          excinfo=SimpleNamespace(traceback=frames), nodeid=target)
    assert cause["source_locus"] == {"path": "<subject>/tests/test_correction_integration.py", "line": 42}
    assert "secret" not in json.dumps(cause)
    assert diagnostic._cause(cause) == cause
    diagnostic._failures([{"nodeid": target, "phase": "call", "cause": cause}])
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._failures([{"nodeid": diagnostic.TARGETS[1], "phase": "call", "cause": cause}])
    assert "source_locus" not in plugin._cause(AssertionError("secret"),
        excinfo=SimpleNamespace(traceback=frames[1:]), nodeid=target)


@pytest.mark.parametrize("field,value", [
    ("message", "secret"), ("stderr", "secret"), ("root_cause", "git"),
    ("integration_boundary", "credential=secret"),
    ("integration_git_diagnostic", "https://example.invalid/private"),
    ("integration_detail_fingerprint", "sha256:" + "0" * 1000),
    ("source_locus", {"path": "C:/private/secret.py", "line": 42}),
    ("source_locus", {"path": "<subject>/tests/../private.py", "line": 42}),
    ("source_locus", {"path": "<subject>/tests//private.py", "line": 42}),
    ("source_locus", {"path": "<subject>/tests/test_operator.py", "line": 0}),
])
def test_integration_validator_rejects_unsafe_fields(field: str, value: object) -> None:
    cause = {"exception_type": "CorrectionIntegrationError", "message_fingerprint": "sha256:" + "0" * 64,
             "integration_boundary": "git-command", "integration_git_diagnostic": "other",
             "integration_detail_fingerprint": "sha256:" + "1" * 64}
    cause[field] = value
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._cause(cause)
