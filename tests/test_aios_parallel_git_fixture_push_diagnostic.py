"""Regression contracts for the bounded observer; no diagnostic phases run here."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import aios_parallel_git_fixture_push_diagnostic as diagnostic
import aios_parallel_git_fixture_push_probe_plugin as probe


def observation(phase="serial", outcome="passed", other=0):
    pushes = []
    for ordinal in range(1, 5):
        family = "integration" if ordinal % 2 else "admission-failure"
        ref_length = len(f"refs/heads/aios/{family}/") + 64
        failed = outcome == "failed" and ordinal == 4
        pushes.append({
            "phase": phase, "ordinal": ordinal, "outcome": "failure" if failed else "success",
            "return_code": int(failed), "remote_root_length": 200,
            "ref_length": ref_length, "remote_lock_path_length": 206 + ref_length,
            **({"stderr_category": "OTHER"} if failed else {}),
        })
    return {
        "schema": diagnostic.SCHEMA, "version": 1, "phase": phase,
        "exit_status": int(outcome == "failed" or bool(other)), "integrity": True,
        "collection_valid": True, "execution_valid": True, "process_isolated": True,
        "workers": ["serial"] if phase == "serial" else [f"gw{i}" for i in range(4)],
        "target_worker": "serial" if phase == "serial" else "gw2",
        "target_outcome": outcome,
        "target_reports": {"setup": "passed", "call": outcome, "teardown": "passed"},
        "pushes": pushes, "non_target_failure_count": other,
    }


@pytest.mark.parametrize("serial,n4,other,classification", [
    ("passed", "failed", 0, "PARALLEL_ONLY_REPRODUCED"),
    ("passed", "passed", 0, "PASS_DRIFT"),
    ("failed", "passed", 0, "SERIAL_FAILURE"),
    ("failed", "failed", 1, "SERIAL_FAILURE"),
    ("passed", "passed", 1, "CONTEXT_FAILURE_TARGET_PASS"),
    ("skipped", "passed", 0, "MIXED_OBSERVATION"),
    ("passed", "skipped", 0, "MIXED_OBSERVATION"),
])
def test_fixed_two_phases_and_all_classifications(monkeypatch, serial, n4, other, classification):
    monkeypatch.setattr(diagnostic, "subject_identity", lambda _: "a" * 40)
    calls = []

    def runner(repository, root, *, phase):
        calls.append(phase)
        value = observation(phase, serial if phase == "serial" else n4, 0 if phase == "serial" else other)
        return value["exit_status"], value

    result = diagnostic.diagnose(Path.cwd(), runner=runner)
    assert calls == ["serial", "n4"]
    assert result["classification"] == classification
    assert result["target"] == diagnostic.TARGET
    assert result["phases"][1]["non_target_failure_count"] == other


@pytest.mark.parametrize("args", [["--workers", "4"], ["--help"], ["--"], [diagnostic.TARGET], [""]])
def test_all_arguments_rejected_before_execution(monkeypatch, args):
    monkeypatch.setattr(diagnostic, "diagnose", lambda *_: pytest.fail("execution started"))
    assert diagnostic.main(args) == 2


def test_successful_main_publishes_only_json_even_when_target_fails(monkeypatch, capsys):
    result = {"classification": "SERIAL_FAILURE"}
    monkeypatch.setattr(diagnostic, "diagnose", lambda _: result)
    assert diagnostic.main([]) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_integrity_error_does_not_disclose_exception(monkeypatch, capsys):
    def fail(_):
        raise diagnostic.DiagnosticError("secret path and ref")
    monkeypatch.setattr(diagnostic, "diagnose", fail)
    assert diagnostic.main([]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "secret" not in output.err


def test_subprocess_surface_is_exact_and_independent(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(diagnostic, "_read", lambda _: {})
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(diagnostic.subprocess, "run", run)
    for phase in ("serial", "n4"):
        diagnostic.run_pytest(Path.cwd(), tmp_path, phase=phase)
    assert len(calls) == 2
    serial, n4 = [call[0] for call in calls]
    assert serial[-1] == diagnostic.TARGET and "-n" not in serial
    assert n4[-7:] == ["-n", "4", "--dist", "load", "--max-worker-restart", "0", diagnostic.TARGET_FILE]
    assert all("--collect-only" not in command for command, _ in calls)
    assert len({kwargs["env"]["TMP"] for _, kwargs in calls}) == 2
    assert len({command[command.index("--basetemp") + 1] for command, _ in calls}) == 2
    assert all(kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL for _, kwargs in calls)
    assert all(kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1" for _, kwargs in calls)
    for phase in ("n12", "full", "retry", "fallback"):
        with pytest.raises(diagnostic.DiagnosticError):
            diagnostic.run_pytest(Path.cwd(), tmp_path, phase=phase)
    assert len(calls) == 2


@pytest.mark.parametrize("category,stderr", [
    ("LOCK_OR_REF_UPDATE", "fatal: cannot lock ref 'private'"),
    ("PATH_OR_FILENAME", "fatal: Filename too long"),
    ("ACCESS_OR_PERMISSION", "fatal: Permission denied"),
    ("REPOSITORY_STATE", "fatal: not a git repository"),
    ("TRANSPORT_OR_REMOTE", "fatal: Could not read from remote repository"),
    ("UNKNOWN_REVISION_OR_OBJECT", "fatal: unknown revision or path"),
    ("OTHER", "an unfamiliar error"),
])
def test_every_stderr_category(category, stderr):
    assert probe.stderr_category(stderr, ()) == category
    value = observation(outcome="failed")
    value["pushes"][-1]["stderr_category"] = category
    diagnostic.validate_phase("serial", 1, value)


def test_every_auditable_pattern_and_bounded_precedence():
    for category, patterns in probe.STDERR_PATTERNS:
        for pattern in patterns:
            assert probe.stderr_category(pattern, ()) == category
    assert probe.stderr_category("x" * probe.MAX_STDERR + "permission denied", ()) == "OTHER"
    assert probe.stderr_category("cannot lock ref: permission denied", ()) == "LOCK_OR_REF_UPDATE"
    assert probe.stderr_category(None, ()) == "OTHER"


@pytest.mark.parametrize("operand", [
    "C:/private/permission denied/remote.git", "C:\\private\\filename too long\\remote.git",
    "refs/heads/aios/unknown revision", "https://private.example/not a git repository",
])
def test_dynamic_operand_patterns_are_redacted_before_categorization(operand):
    assert probe.stderr_category(f"fatal: '{operand}'", (operand,)) == "OTHER"
    assert probe.stderr_category(f"permission denied: '{operand}'", (operand,)) == "ACCESS_OR_PERMISSION"


def push_command(tmp_path, ordinal=1):
    depth = tmp_path / ("private-" + "x" * 120)
    repo = depth / ("sandbox-a" if ordinal <= 2 else "sandbox-b") / "repo"
    # The unchanged fixture returns root/repo; sandbox-a is its parent.
    remote = repo.parent / "upstream.git"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = ' + remote.as_posix() + "\n", encoding="utf-8"
    )
    family = "integration" if ordinal % 2 else "admission-failure"
    ref = f"refs/heads/aios/{family}/" + hashlib.sha256(f"{family}-{(ordinal - 1) // 2}".encode()).hexdigest()
    return ("git", "-C", str(repo), "push", "--quiet", "origin", f"{ref}:{ref}")


@pytest.mark.parametrize("failure", [False, True])
def test_exact_once_delegation_preserves_result_exception_and_kwargs(tmp_path, failure):
    command = push_command(tmp_path)
    kwargs = {"capture_output": True, "text": True, "check": True}
    result = SimpleNamespace(returncode=0, stdout="private-output", stderr="")
    exception = subprocess.CalledProcessError(1, command, output="private-output", stderr=f"cannot lock ref '{command[-1]}'")
    calls = []
    def original(*args, **options):
        calls.append((args, options))
        if failure:
            raise exception
        return result
    recorder = probe.Recorder("serial", "serial")
    wrapper = recorder.wrap(original)
    if failure:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            wrapper(command, **kwargs)
        assert caught.value is exception
    else:
        assert wrapper(command, **kwargs) is result
    assert calls == [((command,), kwargs)]
    assert recorder.integrity
    assert len(recorder.pushes) == 1
    durable = json.dumps(recorder.summary())
    for secret in (command[2], command[-1], "private-output", "cannot lock ref"):
        assert secret not in durable
    assert recorder.pushes[0]["remote_lock_path_length"] > 260


def test_non_push_delegates_without_observation_and_overflow_still_delegates(tmp_path):
    calls = []
    recorder = probe.Recorder("serial", "serial")
    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stderr="", stdout="")
    wrapper = recorder.wrap(original)
    arbitrary_env = {"PRIVATE": "secret"}
    wrapper(("git", "status"), cwd=tmp_path, env=arbitrary_env, check=False, text=False)
    assert calls[-1][1]["env"] is arbitrary_env
    assert recorder.pushes == []
    command = push_command(tmp_path)
    recorder.push_count = diagnostic.MAX_PUSHES
    wrapper(command, capture_output=True, text=True, check=True)
    assert len(calls) == 2 and not recorder.integrity and recorder.pushes == []


def test_malformed_push_preserves_environment_cwd_and_unexpected_exception(tmp_path):
    command = push_command(tmp_path)
    recorder = probe.Recorder("serial", "serial")
    options = {"cwd": tmp_path, "env": {"secret": "value"}, "check": False, "text": False}
    calls = []
    error = OSError("private host detail")
    def original(*args, **kwargs):
        calls.append((args, kwargs))
        raise error
    with pytest.raises(OSError) as caught:
        recorder.wrap(original)(command, **options)
    assert caught.value is error and calls == [((command,), options)]
    assert not recorder.integrity and recorder.pushes == []


@pytest.mark.parametrize("defect", [
    "missing", "extra", "version-bool", "raw-stderr", "raw-path", "duplicate-push",
    "ordinal-bool", "ordinal", "code-bool", "code-bound", "code-contradiction",
    "path-bound", "short-path", "length-contradiction", "wrong-ref", "category",
    "success-category", "over-push", "no-push", "missing-report", "outcome",
    "wrong-worker", "missing-worker", "isolation", "collection", "execution", "integrity",
    "other-bound", "other-bool", "exit", "phase", "push-phase", "failure-before-last",
    "pass-missing-push", "checked-failure-pass",
])
def test_schema_privacy_identity_and_bounds_fail_closed(defect):
    value = observation("n4", "failed")
    push = value["pushes"][-1]
    if defect == "missing": value.pop("target_worker")
    elif defect == "extra": value["private"] = "secret"
    elif defect == "version-bool": value["version"] = True
    elif defect == "raw-stderr": push["stderr"] = "secret"
    elif defect == "raw-path": push["remote_root"] = "secret"
    elif defect == "duplicate-push": value["pushes"][2] = deepcopy(value["pushes"][1])
    elif defect == "ordinal-bool": value["pushes"][0]["ordinal"] = True
    elif defect == "ordinal": push["ordinal"] = 3
    elif defect == "code-bool": push["return_code"] = True
    elif defect == "code-bound": push["return_code"] = diagnostic.MAX_RETURN_CODE + 1
    elif defect == "code-contradiction": push["return_code"] = 0
    elif defect == "path-bound": push["remote_root_length"] = 32768
    elif defect == "short-path": push["remote_lock_path_length"] = 260
    elif defect == "length-contradiction": push["remote_lock_path_length"] += 1
    elif defect == "wrong-ref": push["ref_length"] += 1; push["remote_lock_path_length"] += 1
    elif defect == "category": push["stderr_category"] = "PRIVATE"
    elif defect == "success-category": value["pushes"][0]["stderr_category"] = "OTHER"
    elif defect == "over-push": value["pushes"].append(deepcopy(push))
    elif defect == "no-push": value["pushes"] = []
    elif defect == "missing-report": value["target_reports"].pop("call")
    elif defect == "outcome": value["target_outcome"] = "passed"
    elif defect == "wrong-worker": value["target_worker"] = "controller"
    elif defect == "missing-worker": value["workers"].pop()
    elif defect in {"isolation", "collection", "execution", "integrity"}:
        key = {"isolation": "process_isolated", "collection": "collection_valid", "execution": "execution_valid", "integrity": "integrity"}[defect]
        value[key] = False
    elif defect == "other-bound": value["non_target_failure_count"] = len(diagnostic.EXPECTED)
    elif defect == "other-bool": value["non_target_failure_count"] = True
    elif defect == "exit": value["exit_status"] = 0
    elif defect == "phase": value["phase"] = "serial"
    elif defect == "push-phase": push["phase"] = "serial"
    elif defect == "failure-before-last": value["pushes"][0].update(outcome="failure", return_code=1, stderr_category="OTHER")
    elif defect == "pass-missing-push": value = observation("n4"); value["pushes"].pop()
    elif defect == "checked-failure-pass": value["target_reports"]["call"] = "passed"; value["target_reports"]["teardown"] = "failed"
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.validate_phase("n4", value["exit_status"], value)


@pytest.mark.parametrize("change_at", [2, 3, 4, 5])
def test_subject_mutation_rejected_before_between_and_after_phases(monkeypatch, change_at):
    checks = 0
    calls = []
    def identity(_):
        nonlocal checks
        checks += 1
        return ("a" if checks < change_at else "b") * 40
    monkeypatch.setattr(diagnostic, "subject_identity", identity)
    def runner(repository, root, *, phase):
        calls.append(phase)
        return 0, observation(phase)
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose(Path.cwd(), runner=runner)
    assert calls == ([] if change_at == 2 else ["serial"] if change_at < 5 else ["serial", "n4"])


@pytest.mark.parametrize("dirty,sha,root_bad", [(True, "a" * 40, False), (False, "bad", False), (False, "a" * 40, True)])
def test_exact_subject_rejects_dirty_malformed_and_wrong_root(monkeypatch, dirty, sha, root_bad):
    root = Path.cwd()
    def run(command, **kwargs):
        if command[-1] == "--show-toplevel": output = str(root.parent if root_bad else root)
        elif command[-1] == "HEAD": output = sha
        else: output = "?? private\n" if dirty else ""
        return SimpleNamespace(stdout=output)
    monkeypatch.setattr(diagnostic.subprocess, "run", run)
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.subject_identity(root)


@pytest.mark.parametrize("content", [b'{"x":1,"x":2}', b"not json", b"x" * (diagnostic.MAX_BYTES + 1)])
def test_protocol_duplicate_malformed_and_size_bound(tmp_path, content):
    path = tmp_path / "observation.json"
    path.write_bytes(content)
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._read(path)


def test_missing_protocol_fails_closed(tmp_path):
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._read(tmp_path / "missing.json")


def test_exact_target_and_non_target_reports_remain_private():
    recorder = probe.Recorder("n4", "gw2")
    for node in sorted(diagnostic.EXPECTED):
        for stage in probe.STAGES:
            recorder.record_report(SimpleNamespace(nodeid=node, when=stage, outcome="failed" if node != diagnostic.TARGET and stage == "call" else "passed"))
    assert recorder.execution_valid(complete=True)
    assert recorder.summary()["non_target_failure_count"] == len(diagnostic.EXPECTED) - 1
    assert "test_real_git" not in json.dumps(recorder.summary())
    recorder.record_report(SimpleNamespace(nodeid=diagnostic.TARGET, when="call", outcome="passed"))
    assert not recorder.integrity


def test_collection_missing_duplicate_or_substituted_target_fails_closed():
    assert probe._valid_collection([diagnostic.TARGET], "serial")
    assert probe._valid_collection(list(diagnostic.EXPECTED), "n4")
    assert not probe._valid_collection([], "serial")
    assert not probe._valid_collection([diagnostic.TARGET] * 2, "serial")
    assert not probe._valid_collection([diagnostic.TARGET + "[substitute]"], "serial")


def test_unknown_worker_and_reordered_report_fail_closed():
    recorder = probe.Recorder("n4", "gw0")
    recorder.record_report(SimpleNamespace(nodeid=diagnostic.TARGET, worker_id="unknown", when="setup", outcome="passed"))
    assert not recorder.integrity and not recorder.reports
    recorder = probe.Recorder("n4", "gw0")
    recorder.record_report(SimpleNamespace(nodeid=diagnostic.TARGET, worker_id="gw0", when="call", outcome="passed"))
    assert not recorder.integrity


def test_process_cache_and_basetemp_isolation_bounds(tmp_path):
    profile = tmp_path.resolve()
    facts = {"serial": {"pid": 100, "temp": str(profile / "pytest"), "cache": str(profile / "aios-git-fixtures-controller"), "cache_present": True}}
    for index in range(4):
        facts[f"gw{index}"] = {"pid": 200 + index, "temp": str(profile / "pytest" / f"popen-gw{index}"), "cache": str(profile / f"aios-git-fixtures-{index}"), "cache_present": True}
    for process in facts.values():
        Path(process["cache"]).mkdir()
    assert probe._isolated(facts, profile)
    for field in ("pid", "temp", "cache"):
        broken = deepcopy(facts)
        broken["gw1"][field] = broken["gw0"][field]
        assert not probe._isolated(broken, profile)
    # Existence must be affirmatively observed while the owner is alive.
    for invalid in (False, None, 1, "true"):
        broken = deepcopy(facts)
        broken["gw1"]["cache_present"] = invalid
        assert not probe._isolated(broken, profile)
    broken = deepcopy(facts)
    broken["gw1"].pop("cache_present")
    assert not probe._isolated(broken, profile)
    broken = deepcopy(facts)
    broken["gw1"]["cache"] = str(profile.parent / "aios-git-fixtures-outside")
    assert not probe._isolated(broken, profile)


def test_real_xdist_cache_cleanup_preserves_owner_observed_isolation(tmp_path):
    """Exercise actual worker shutdown/atexit, without running diagnostic phases."""
    profile = tmp_path.resolve()
    helper = profile / "cache_cleanup_probe.py"
    helper.write_text('''
import json
import os
from pathlib import Path
import aios_parallel_git_fixture_push_probe_plugin as probe

processes = {}
node_integrity = True

def pytest_testnodedown(node, error):
    global node_integrity
    label = node.gateway.id
    if error is not None or label in processes:
        node_integrity = False
    processes[label] = node.workeroutput.get("cache_process")

def pytest_sessionfinish(session, exitstatus):
    config = session.config
    process = probe._process(config)
    if hasattr(config, "workerinput"):
        config.workeroutput["cache_process"] = process
        return
    profile = Path(os.environ["AIOS_PARALLEL_GIT_PUSH_ROOT"]).resolve()
    workers_complete = set(processes) == {f"gw{i}" for i in range(4)}
    owners_saw_cache = all(p["cache_present"] is True for p in processes.values())
    worker_caches_removed = all(not Path(p["cache"]).exists() for p in processes.values())
    processes["serial"] = process
    # Only typed facts leave this process; paths/PIDs stay in transient IPC.
    (profile / "cleanup-facts.json").write_text(json.dumps({
        "node_integrity": node_integrity,
        "workers_complete": workers_complete,
        "owners_saw_cache": owners_saw_cache,
        "worker_caches_removed": worker_caches_removed,
        "controller_cache_present": Path(process["cache"]).is_dir(),
        "isolated": probe._isolated(processes, profile),
    }))
''', encoding="utf-8")
    target = profile / "test_cache_cleanup.py"
    target.write_text("def test_worker_lifecycle():\n    pass\n", encoding="utf-8")
    repository = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(key, None)
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": os.pathsep.join(map(str, (profile, repository / "tests", repository))),
        "TMP": str(profile), "TEMP": str(profile), "TMPDIR": str(profile),
        diagnostic.ROOT_ENV: str(profile),
    })
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-p", "xdist.plugin", "-p", "cache_cleanup_probe",
        "-p", "no:cacheprovider", "-o", "addopts=", "-o", "log_file=",
        "--confcutdir", str(profile), "--basetemp", str(profile / "pytest"),
        "-n", "4", "--dist", "load", "--max-worker-restart", "0", "-q", str(target),
    ], cwd=repository, env=env, check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert result.returncode == 0
    facts = json.loads((profile / "cleanup-facts.json").read_text())
    assert facts == {
        "node_integrity": True, "workers_complete": True, "owners_saw_cache": True,
        "worker_caches_removed": True, "controller_cache_present": True, "isolated": True,
    }


def test_instrumentation_is_active_only_for_exact_target_and_restores_run(monkeypatch):
    recorder = probe.Recorder("serial", "serial")
    monkeypatch.setattr(probe, "_recorder", recorder)
    original = subprocess.run
    monkeypatch.setattr(subprocess, "run", original)
    for node in (diagnostic.TARGET + "[other]", diagnostic.TARGET):
        hook = probe.pytest_runtest_protocol(SimpleNamespace(nodeid=node), None)
        next(hook)
        assert (subprocess.run is original) == (node != diagnostic.TARGET)
        with pytest.raises(StopIteration):
            next(hook)
        assert subprocess.run is original
    hook = probe.pytest_runtest_protocol(SimpleNamespace(nodeid=diagnostic.TARGET), None)
    next(hook)
    subprocess.run = lambda *_args, **_kwargs: None
    with pytest.raises(StopIteration):
        next(hook)
    assert not recorder.integrity and subprocess.run is original


@pytest.mark.parametrize("defect", [None, "missing-worker", "missing-collection", "shared-pid", "worker-outcome", "raw-operand", "bool-count", "missing-cache", "cache-bool"])
def test_n4_controller_checks_worker_execution_and_discards_raw_process_facts(monkeypatch, tmp_path, defect):
    profile = tmp_path.resolve()
    controller = probe.Recorder("n4", "serial")
    locals_by_worker = {f"gw{i}": probe.Recorder("n4", f"gw{i}") for i in range(4)}
    for local in locals_by_worker.values():
        local.collection_valid = True
    for index, node in enumerate(sorted(diagnostic.EXPECTED)):
        worker = "gw2" if node == diagnostic.TARGET else f"gw{index % 2}"
        for stage in probe.STAGES:
            report = SimpleNamespace(nodeid=node, when=stage, outcome="passed", worker_id=worker)
            controller.record_report(report)
            locals_by_worker[worker].record_report(report)
    locals_by_worker["gw2"].pushes = observation("n4")["pushes"]
    process = {"pid": 100, "temp": str(profile / "pytest"), "cache": str(profile / "aios-git-fixtures-controller"), "cache_present": True}
    workers = {}
    for index, (worker, local) in enumerate(locals_by_worker.items()):
        workers[worker] = {"summary": local.summary(), "process": {
            "pid": 200 + index, "temp": str(profile / "pytest" / f"popen-{worker}"),
            "cache": str(profile / f"aios-git-fixtures-private-{worker}"),
            "cache_present": True,
        }}
    for fact in [process] + [value["process"] for value in workers.values()]:
        Path(fact["cache"]).mkdir()
    collections = {key: True for key in workers}
    if defect == "missing-worker": workers.pop("gw3")
    elif defect == "missing-collection": collections.pop("gw3")
    elif defect == "shared-pid": workers["gw1"]["process"]["pid"] = workers["gw0"]["process"]["pid"]
    elif defect == "worker-outcome": workers["gw2"]["summary"]["target_reports"]["call"] = "failed"
    elif defect == "raw-operand": workers["gw2"]["summary"]["pushes"][0]["stderr"] = "private-output"
    elif defect == "bool-count": workers["gw3"]["summary"]["executed_count"] = False
    elif defect == "missing-cache": workers["gw3"]["process"]["cache_present"] = False
    elif defect == "cache-bool": workers["gw3"]["process"]["cache_present"] = 1
    monkeypatch.setattr(probe, "_recorder", controller)
    monkeypatch.setattr(probe, "_workers", workers)
    monkeypatch.setattr(probe, "_collections", collections)
    monkeypatch.setattr(probe, "_node_error", False)
    monkeypatch.setattr(probe, "_process", lambda _: process)
    monkeypatch.setenv(diagnostic.ROOT_ENV, str(profile))
    output = profile / "observation.json"
    monkeypatch.setenv(diagnostic.OUTPUT_ENV, str(output))
    probe.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()), 0)
    content = output.read_text()
    value = json.loads(content)
    assert str(profile) not in content and "private" not in content
    assert "pid" not in content and "cache" not in content
    if defect:
        with pytest.raises(diagnostic.DiagnosticError):
            diagnostic.validate_phase("n4", 0, value)
    else:
        diagnostic.validate_phase("n4", 0, value)
        assert value["target_worker"] == "gw2"
