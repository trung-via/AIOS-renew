import os
import stat
import subprocess
import json
import hashlib
from contextlib import contextmanager
from pathlib import Path

import pytest

import aios_renew.verification as verification_module
from aios_renew.artifacts import Claim, Result
from aios_renew.verification import (
    RuntimeVerificationError,
    attach_verification_evidence,
    execute_verification,
    materialize_verification_subject,
    execute_minimum_verification,
)


@pytest.fixture
def v2_injected_execution(tmp_path, monkeypatch):
    from tests.test_verification_contract import V2_BASE, V2_CANDIDATE, V2_TOOLCHAIN, v2_observation
    from aios_renew.verification_contract import SELECTED_FULL_SUITE_COMMAND
    state = {"tree": "c" * 40, "observer": "f" * 40, "detail": "AssertionError: expected 1",
             "base_pass": False, "probe_divergent": False, "calls": []}

    def observe_git(repository, *args, **kwargs):
        if args[:2] == ("rev-parse", "--verify"):
            return args[2].split("^")[0]
        if args[:2] == ("diff", "--name-only"):
            return "tests/test_sample.py\0"
        if args[0] == "rev-parse" and args[1].endswith("^{tree}"):
            return "c" * 40 if args[1].startswith(V2_CANDIDATE) else state["tree"]
        if args[0] == "rev-parse" and ":tests/bp_v4_probe_plugin.py" in args[1]:
            return state["observer"]
        if args[0] == "rev-parse" and ":.ai/verification-profiles.yaml" in args[1]:
            return "e" * 40
        if args[0] == "rev-parse" and ":" in args[1]:
            return "1" * 40
        if args[0] == "show":
            return json.dumps(dict(format="AIOS_VERIFICATION_PROFILE_POLICY", version=1,
                ordinary_canonical_full_suite=dict(profile="bounded-parallel-full-suite-v1", command=SELECTED_FULL_SUITE_COMMAND,
                    workers=12, distribution="load", max_worker_restart=0,
                    selection_provenance=dict(authority="HUMAN", task_id="TASK-231"))))
        raise AssertionError(args)

    @contextmanager
    def materialize(repository, *, subject_sha, **kwargs):
        checkout = tmp_path / subject_sha
        checkout.mkdir(exist_ok=True)
        yield checkout

    def runner(shell, **kwargs):
        sha = kwargs["cwd"].name
        command = shell[-1]
        state["calls"].append((sha, command))
        profile = json.loads(kwargs["env"]["AIOS_V2_PROFILE"])
        outcome = "PASS" if sha == V2_BASE and state["base_pass"] else "FAIL"
        detail = state["detail"]
        if state["probe_divergent"] and command != SELECTED_FULL_SUITE_COMMAND and sha != V2_BASE:
            detail = "AssertionError: divergent candidate probe"
        value = v2_observation(sha, outcome=outcome, count=51, detail=detail, command=command)
        for report in value["reports"]:
            if report["outcome"] == "FAIL":
                report.update(profile=profile, toolchain=V2_TOOLCHAIN)
        canonical = {k: value[k] for k in ("complete", "unstable", "reports", "failure_count")}
        status = value["exit_code"]
        if command == SELECTED_FULL_SUITE_COMMAND:
            envelope = {"result": dict(canonical=canonical, observation_profile=profile,
                observation_toolchain=V2_TOOLCHAIN, conformance={"all_workers_reported": True},
                exit_status=status, pytest_exit_status=status)}
            stdout = json.dumps(envelope).encode("utf-8")
        else:
            output = Path(kwargs["env"]["AIOS_BP_V4_PLUGIN_OUTPUT"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(dict(schema="AIOS_BP_V4_PYTEST_OBSERVATION", version=1,
                profile=profile, toolchain=V2_TOOLCHAIN, exit_status=status,
                failure_diagnostics=dict(canonical=canonical, reported_count=canonical["failure_count"]))), encoding="utf-8")
            stdout = b"synthetic pytest output\n"
        return completed(status, stdout=stdout)

    monkeypatch.setattr(verification_module, "_git", observe_git)
    monkeypatch.setattr(verification_module, "_optional_git", observe_git)
    monkeypatch.setattr(verification_module, "_v2_toolchain", lambda: dict(V2_TOOLCHAIN))
    monkeypatch.setattr(verification_module, "materialize_verification_subject", materialize)

    def execute(run_id, *, candidate=V2_CANDIDATE, operation="PRIMARY", scope=("tests/test_sample.py",)):
        return execute_minimum_verification((SELECTED_FULL_SUITE_COMMAND,), run_id=run_id,
            base_sha=V2_BASE, subject_sha=candidate, repository=tmp_path,
            raw_directory=tmp_path / run_id, cache_directory=tmp_path / "cache", modification_scope=scope,
            operation=operation, runner=runner, platform="posix", environment={}, subject_check=lambda *a, **k: None)
    return execute, state


def test_v2_full_guard_preserves_51_raw_failures_and_reuses_exact_evidence(v2_injected_execution, tmp_path):
    execute, state = v2_injected_execution
    first = execute("RUN-first")
    assert len(state["calls"]) == 2
    assert first[0].result.exit_code == 1
    assert len(first[0].verification["attribution"]) == 51
    assert {r["classification"] for r in first[0].verification["attribution"]} == {"PRE_EXISTING_BASELINE"}
    raw = Path(first[0].raw_path).read_bytes()
    second = execute("RUN-second")
    assert len(state["calls"]) == 2 and second[0].result.exit_code == 1
    assert second[0].verification["disposition"] == "REUSED"
    assert Path(second[0].raw_path).read_bytes() == raw
    audit = json.loads((tmp_path / "RUN-second" / "minimum-sufficient-v2-plan.json").read_text(encoding="utf-8"))
    assert audit["records"][0]["disposition"] == "REUSED"
    third = execute("RUN-third", candidate="d" * 40)
    assert len(state["calls"]) == 2
    assert third[0].subject_sha == "d" * 40
    assert third[0].verification["candidate"]["subject_sha"] == first[0].subject_sha


@pytest.mark.parametrize("change", ["tree", "observer", "raw"])
def test_v2_changed_relevant_material_executes_the_authored_guard_again(v2_injected_execution, change):
    execute, state = v2_injected_execution
    first = execute("RUN-first")
    if change == "raw":
        Path(first[0].raw_path).write_bytes(b"tampered raw output")
    else:
        state[change] = "9" * 40
    execute("RUN-next", candidate="d" * 40)
    assert len(state["calls"]) == (3 if change == "tree" else 4)


def test_v2_base_pass_candidate_failure_is_blocking(v2_injected_execution):
    execute, state = v2_injected_execution
    state["base_pass"] = True
    with pytest.raises(RuntimeVerificationError) as failure:
        execute("RUN-regression")
    item = failure.value.evidence[0]
    assert item.result.exit_code == 1
    assert {r["classification"] for r in item.verification["attribution"]} == {"CANDIDATE_REGRESSION"}


def test_v2_affected_probe_runs_first_and_never_skips_full_guard(v2_injected_execution, tmp_path):
    from aios_renew.verification_contract import SELECTED_FULL_SUITE_COMMAND
    execute, state = v2_injected_execution
    execute("RUN-prior")
    state["calls"].clear()
    state["probe_divergent"] = True
    with pytest.raises(RuntimeVerificationError) as failure:
        execute("RUN-correction", operation="REPAIR")
    candidate_calls = [command for sha, command in state["calls"] if sha == "b" * 40]
    assert len(candidate_calls) == 2
    assert "tests/test_sample.py::test_050" in candidate_calls[0]
    assert candidate_calls[-1] == SELECTED_FULL_SUITE_COMMAND
    assert failure.value.evidence[-1].result.exit_code == 1
    assert {r["classification"] for r in failure.value.evidence[0].verification["attribution"]} == {"UNRESOLVED"}
    audit = json.loads((tmp_path / "RUN-correction" / "minimum-sufficient-v2-plan.json").read_text(encoding="utf-8"))
    assert audit["modification_scope"] == ["tests/test_sample.py"]


def test_v2_canonical_cache_rejects_missing_raw_or_digest_tampering(tmp_path):
    from tests.test_verification_contract import v2_record
    raw = tmp_path / "source.raw"
    raw.write_bytes(b"immutable verification output")
    record = {**v2_record(), "raw_path": str(raw), "raw_digest": hashlib.sha256(raw.read_bytes()).hexdigest()}
    cache = tmp_path / "cache"
    cache.mkdir()
    path = cache / "proof.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    assert verification_module._v2_cache_records(cache) == (record,)
    record["candidate_digest"] = "0" * 64
    path.write_text(json.dumps(record), encoding="utf-8")
    assert verification_module._v2_cache_records(cache) == ()
    raw.unlink()
    assert verification_module._v2_cache_records(cache) == ()


class RecordingRunner:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.outcomes[len(self.calls) - 1]


def completed(returncode=0, stdout=b"ok\n", stderr=b""):
    return subprocess.CompletedProcess(
        ("shell",), returncode=returncode, stdout=stdout, stderr=stderr
    )


def git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *args),
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def test_materializes_one_clean_exact_subject_with_independent_git_state(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    (control / "tracked.txt").write_text("candidate\n", encoding="utf-8")
    git(control, "add", "tracked.txt")
    git(control, "commit", "-m", "candidate")
    candidate = git(control, "rev-parse", "HEAD")

    with materialize_verification_subject(
        control, run_id="RUN-156-001", subject_sha=candidate
    ) as subject:
        assert subject != control
        assert (subject / ".git").is_dir()
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert git(subject, "status", "--porcelain") == ""
        git(subject, "config", "aios.subject", "isolated")
        git(subject, "branch", "subject-only")
        control_config = subprocess.run(
            ("git", "-C", str(control), "config", "--get", "aios.subject"),
            capture_output=True,
            text=True,
            check=False,
        )
        assert control_config.returncode == 1
        assert "subject-only" not in git(control, "branch", "--list")
        subject_root = subject.parent

    assert not subject_root.exists()


def test_materialized_subject_ignores_ambient_and_control_checkout_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    canonical = b"first\nsecond\n"
    (control / "sentinel.txt").write_bytes(canonical)
    git(control, "add", "sentinel.txt")
    git(control, "commit", "-m", "LF-only candidate")
    candidate = git(control, "rev-parse", "HEAD")

    host_attributes = tmp_path / "host-attributes"
    host_attributes.write_bytes(b"*.txt text eol=crlf\n")
    host_config = tmp_path / "host-gitconfig"
    git(control, "config", "--file", str(host_config), "core.autocrlf", "true")
    git(control, "config", "--file", str(host_config), "core.eol", "crlf")
    git(
        control,
        "config",
        "--file",
        str(host_config),
        "core.attributesFile",
        str(host_attributes),
    )
    git(control, "config", "--local", "core.autocrlf", "true")
    git(control, "config", "--local", "core.eol", "crlf")
    git(control, "config", "--local", "core.attributesFile", str(host_attributes))
    control_config = (control / ".git" / "config").read_bytes()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(host_config))

    with materialize_verification_subject(
        control, run_id="RUN-205-001", subject_sha=candidate
    ) as subject:
        blob = subprocess.run(
            ("git", "-C", str(control), "show", f"{candidate}:sentinel.txt"),
            capture_output=True,
            check=True,
        )
        assert blob.stdout == canonical
        assert (subject / "sentinel.txt").read_bytes() == canonical
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert git(subject, "status", "--porcelain") == ""
        assert git(subject, "config", "--local", "core.autocrlf") == "false"
        assert git(subject, "config", "--local", "core.eol") == "lf"
        assert git(subject, "config", "--local", "core.attributesFile") != str(
            host_attributes
        )
        assert (control / ".git" / "config").read_bytes() == control_config


def test_materialized_subject_honors_tracked_checkout_attributes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    (control / ".gitattributes").write_bytes(
        b"explicit-crlf.txt text eol=crlf\nexplicit-lf.txt text eol=lf\n"
    )
    canonical = b"first\nsecond\n"
    (control / "explicit-crlf.txt").write_bytes(canonical)
    (control / "explicit-lf.txt").write_bytes(canonical)
    git(control, "add", ".gitattributes", "explicit-crlf.txt", "explicit-lf.txt")
    git(control, "commit", "-m", "tracked checkout policy")
    candidate = git(control, "rev-parse", "HEAD")
    host_config = tmp_path / "host-gitconfig"
    git(control, "config", "--file", str(host_config), "core.autocrlf", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(host_config))

    with materialize_verification_subject(
        control, run_id="RUN-205-002", subject_sha=candidate
    ) as subject:
        assert (subject / "explicit-crlf.txt").read_bytes() == b"first\r\nsecond\r\n"
        assert (subject / "explicit-lf.txt").read_bytes() == canonical
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert git(subject, "status", "--porcelain") == ""


def test_materialized_subject_stays_exact_when_control_head_moves(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    (control / "tracked.txt").write_text("candidate\n", encoding="utf-8")
    git(control, "add", "tracked.txt")
    git(control, "commit", "-m", "candidate")
    candidate = git(control, "rev-parse", "HEAD")

    with materialize_verification_subject(
        control, run_id="RUN-156-002", subject_sha=candidate
    ) as subject:
        (control / "tracked.txt").write_text("later\n", encoding="utf-8")
        git(control, "add", "tracked.txt")
        git(control, "commit", "-m", "move control")

        assert git(control, "rev-parse", "HEAD") != candidate
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert (subject / "tracked.txt").read_text(encoding="utf-8") == "candidate\n"
        assert git(subject, "status", "--porcelain") == ""


def test_materialized_subject_has_empty_local_scratch_for_relative_command(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    (control / "basetemp_probe.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "target = Path(sys.argv[1].split('=', 1)[1])\n"
        "target.mkdir()\n"
        "(target / 'probe.txt').write_text('ready', encoding='utf-8')\n",
        encoding="utf-8",
    )
    git(control, "add", "basetemp_probe.py")
    git(control, "commit", "-m", "candidate")
    candidate = git(control, "rev-parse", "HEAD")
    control_scratch = control / ".git" / "aios"
    control_scratch.mkdir()
    (control_scratch / "control-sentinel").write_text("control only", encoding="utf-8")
    command = "python basetemp_probe.py --basetemp=.git/aios/pytest-task204"

    with materialize_verification_subject(
        control, run_id="RUN-204-001", subject_sha=candidate
    ) as subject:
        subject_root = subject.parent
        scratch = subject / ".git" / "aios"
        assert scratch.is_dir()
        assert list(scratch.iterdir()) == []
        assert not (scratch / "control-sentinel").exists()

        evidence = execute_verification(
            (command,),
            run_id="RUN-204-001",
            subject_sha=candidate,
            repository=subject,
            raw_directory=control_scratch / "verification",
        )

        assert evidence[0].source.command == command
        assert evidence[0].subject_sha == candidate
        assert evidence[0].result.exit_code == 0
        assert list(scratch.iterdir()) == [scratch / "pytest-task204"]
        assert (scratch / "pytest-task204" / "probe.txt").read_text(
            encoding="utf-8"
        ) == "ready"
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert git(subject, "status", "--porcelain") == ""

    assert not subject_root.exists()
    assert (control_scratch / "control-sentinel").read_text(
        encoding="utf-8"
    ) == "control only"


def test_relative_git_basetemp_command_runs_unchanged_from_subject_repo(
    tmp_path: Path,
) -> None:
    subject = tmp_path / "historical-subject"
    subject.mkdir()
    (subject / ".git").mkdir()
    (subject / "basetemp_probe.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1].split('=', 1)[1]).mkdir(parents=True)\n",
        encoding="utf-8",
    )
    command = (
        "python basetemp_probe.py "
        "--basetemp=.git/aios/pytest-historical-subject"
    )

    evidence = execute_verification(
        (command,),
        run_id="RUN-148-TEST",
        subject_sha="a" * 40,
        repository=subject,
        raw_directory=tmp_path / "control-runtime" / "verification",
    )

    assert evidence[0].source.command == command
    assert evidence[0].result.exit_code == 0
    assert (
        subject / ".git" / "aios" / "pytest-historical-subject"
    ).is_dir()


def test_executes_posix_commands_once_in_exact_order_and_builds_evidence(
    tmp_path: Path,
) -> None:
    commands = (
        "printf 'one  two'",
        "git status --porcelain",
        "printf 'one  two'",
    )
    runner = RecordingRunner(
        [completed(stdout=b"one  two"), completed(), completed(stdout=b"one  two")]
    )
    raw = tmp_path / ".git" / "aios" / "verification" / "RUN-027-001"

    evidence = execute_verification(
        commands,
        run_id="RUN-027-001",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=raw,
        runner=runner,
        platform="posix",
    )

    assert [call[0] for call in runner.calls] == [
        ("/bin/sh", "-c", commands[0]),
        ("/bin/sh", "-c", commands[1]),
        ("/bin/sh", "-c", commands[2]),
    ]
    assert all(call[1]["cwd"] == tmp_path.resolve() for call in runner.calls)
    assert all(call[1]["capture_output"] is True for call in runner.calls)
    assert all(call[1]["text"] is False for call in runner.calls)
    assert all(call[1]["check"] is False for call in runner.calls)
    assert [item.source.command for item in evidence] == list(commands)
    assert [item.evidence_id for item in evidence] == [
        "RUN-027-001-V001",
        "RUN-027-001-V002",
        "RUN-027-001-V003",
    ]
    assert all(item.run_id == "RUN-027-001" for item in evidence)
    assert all(item.subject_sha == "abc123" for item in evidence)
    assert all(Path(item.raw_path).is_relative_to(raw) for item in evidence)


def test_windows_uses_one_noninteractive_powershell_wrapper_with_utf8_preamble(
    tmp_path: Path,
) -> None:
    command = "git diff --check"
    runner = RecordingRunner([completed()])

    evidence = execute_verification(
        (command,),
        run_id="RUN-027-002",
        subject_sha="def456",
        repository=tmp_path,
        raw_directory=tmp_path / ".git" / "aios" / "verification",
        runner=runner,
        platform="nt",
        environment={"EXISTING_VAR": "val"},
    )

    assert runner.calls[0][0] == (
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        f"& {{ {command} }}; exit $LASTEXITCODE",
    )
    assert runner.calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "PYTHONUTF8" not in runner.calls[0][1]["env"]
    assert runner.calls[0][1]["env"]["EXISTING_VAR"] == "val"
    isolated = runner.calls[0][1]["env"]["TEMP"]
    assert runner.calls[0][1]["env"]["TMP"] == isolated
    assert runner.calls[0][1]["env"]["TMPDIR"] == isolated
    isolated_path = Path(isolated)
    assert isolated_path.name.startswith("aios-verification-RUN-027-002-")
    assert not isolated_path.is_relative_to(tmp_path.resolve())
    assert not isolated_path.exists()
    assert evidence[0].source.command == command


def test_windows_temp_isolation_overrides_conflicting_ambient_values_for_all_commands(
    tmp_path: Path,
) -> None:
    commands = ("first --unchanged", "second --unchanged")
    runner = RecordingRunner([completed(), completed()])
    ambient = {
        "Temp": "ambient-temp",
        "TMP": "ambient-tmp",
        "TMPDIR": "ambient-tmpdir",
        "UNCHANGED": "preserved",
    }

    execute_verification(
        commands,
        run_id="RUN-120-001",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / "runtime" / "RUN-120-001",
        runner=runner,
        platform="nt",
        environment=ambient,
    )

    assert len(runner.calls) == 2
    assert [call[0][-1] for call in runner.calls] == [
        f"[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        f"& {{ {command} }}; exit $LASTEXITCODE"
        for command in commands
    ]
    environments = [call[1]["env"] for call in runner.calls]
    isolated = environments[0]["TEMP"]
    assert all(
        env["TEMP"] == env["TMP"] == env["TMPDIR"] == isolated
        for env in environments
    )
    assert all("Temp" not in env for env in environments)
    assert all(env["UNCHANGED"] == "preserved" for env in environments)
    assert ambient == {
        "Temp": "ambient-temp",
        "TMP": "ambient-tmp",
        "TMPDIR": "ambient-tmpdir",
        "UNCHANGED": "preserved",
    }
    assert not Path(isolated).exists()


def test_windows_temp_isolation_does_not_mutate_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEMP", "process-temp")
    monkeypatch.setenv("TMP", "process-tmp")
    monkeypatch.setenv("TMPDIR", "process-tmpdir")
    runner = RecordingRunner([completed()])

    execute_verification(
        ("one-command",),
        run_id="RUN-120-006",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / "runtime" / "RUN-120-006",
        runner=runner,
        platform="nt",
    )

    assert os.environ["TEMP"] == "process-temp"
    assert os.environ["TMP"] == "process-tmp"
    assert os.environ["TMPDIR"] == "process-tmpdir"
    assert runner.calls[0][1]["env"]["TEMP"] != "process-temp"


def test_windows_verification_executions_use_distinct_temp_roots(
    tmp_path: Path,
) -> None:
    first_runner = RecordingRunner([completed()])
    second_runner = RecordingRunner([completed()])

    execute_verification(
        ("same-command",),
        run_id="RUN-120-002",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / "runtime" / "RUN-120-002",
        runner=first_runner,
        platform="nt",
        environment={},
    )
    execute_verification(
        ("same-command",),
        run_id="RUN-120-003",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / "runtime" / "RUN-120-003",
        runner=second_runner,
        platform="nt",
        environment={},
    )

    first_temp = first_runner.calls[0][1]["env"]["TEMP"]
    second_temp = second_runner.calls[0][1]["env"]["TEMP"]
    assert first_temp != second_temp
    assert not Path(first_temp).exists()
    assert not Path(second_temp).exists()


def test_windows_temp_isolation_failure_stops_before_command_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "runtime" / "RUN-120-004"
    runner = RecordingRunner([completed()])

    def fail_create(*args, **kwargs):
        raise OSError("isolated create denied")

    monkeypatch.setattr(verification_module.tempfile, "mkdtemp", fail_create)

    with pytest.raises(RuntimeVerificationError, match="could not be isolated"):
        execute_verification(
            ("must-not-run",),
            run_id="RUN-120-004",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=raw,
            runner=runner,
            platform="nt",
            environment={},
        )

    assert runner.calls == []


def test_windows_temp_is_outside_repository_git_ancestry_and_shared_until_cleanup(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "subject"
    (repository / ".git").mkdir(parents=True)
    seen: list[Path] = []

    def runner(command, **kwargs):
        isolated = Path(kwargs["env"]["TEMP"])
        assert isolated.is_dir()
        assert not isolated.is_relative_to(repository.resolve())
        assert not any((parent / ".git").exists() for parent in isolated.parents)
        seen.append(isolated)
        return completed()

    raw = repository / ".git" / "aios" / "verification" / "RUN-120-007"
    execute_verification(
        ("first", "second"),
        run_id="RUN-120-007",
        subject_sha="abc123",
        repository=repository,
        raw_directory=raw,
        runner=runner,
        platform="nt",
        environment={},
    )

    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert not seen[0].exists()
    assert (raw / "RUN-120-007-V001.raw").is_file()
    assert (raw / "RUN-120-007-V002.raw").is_file()


def test_windows_temp_cleanup_failure_fails_closed_and_preserves_raw_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner([completed(stdout=b"captured\n")])
    raw = tmp_path / "runtime" / "RUN-120-008"
    real_rmtree = verification_module.shutil.rmtree
    isolated: Path | None = None

    def fail_cleanup(path, *args, **kwargs):
        nonlocal isolated
        isolated = Path(path)
        raise OSError("isolated cleanup denied")

    monkeypatch.setattr(verification_module.shutil, "rmtree", fail_cleanup)
    try:
        with pytest.raises(
            RuntimeVerificationError, match="could not be cleaned"
        ) as caught:
            execute_verification(
                ("one-command",),
                run_id="RUN-120-008",
                subject_sha="abc123",
                repository=tmp_path,
                raw_directory=raw,
                runner=runner,
                platform="nt",
                environment={},
            )
    finally:
        if isolated is not None and isolated.exists():
            real_rmtree(isolated)

    assert len(caught.value.evidence) == 1
    assert caught.value.evidence[0].result.exit_code == 0
    assert (raw / "RUN-120-008-V001.raw").read_bytes().endswith(
        b"captured\n\nSTDERR\n"
    )


def test_temp_cleanup_recovers_read_only_git_object_tree(tmp_path: Path) -> None:
    temp_root = tmp_path / "isolated-verification-root"
    object_directory = temp_root / "smoke-repo" / ".git" / "objects" / "0a"
    object_directory.mkdir(parents=True)
    object_file = object_directory / "76e58f038b4db6cd072967d84f862c896a01f2"
    object_file.write_bytes(b"git-object")
    object_file.chmod(stat.S_IREAD)
    object_directory.chmod(stat.S_IREAD)

    verification_module._remove_temp_root(temp_root)

    assert not temp_root.exists()


def test_temp_cleanup_retries_transient_permission_failure_until_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_root = tmp_path / "isolated-verification-root"
    temp_root.mkdir()
    real_rmtree = verification_module.shutil.rmtree
    attempts = 0
    sleeps: list[float] = []

    def transient_rmtree(path, *args, **kwargs):
        error = PermissionError("Git object is temporarily locked")

        def transient_remove(locked_path):
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                raise PermissionError("Git object is temporarily locked")
            real_rmtree(locked_path)

        kwargs["onerror"](
            transient_remove,
            os.fspath(path),
            (PermissionError, error, None),
        )

    monkeypatch.setattr(verification_module.shutil, "rmtree", transient_rmtree)
    monkeypatch.setattr(verification_module.time, "sleep", sleeps.append)

    verification_module._remove_temp_root(temp_root)

    assert attempts == 4
    assert sleeps == [0.05, 0.1, 0.2]
    assert not temp_root.exists()


def test_temp_cleanup_persistent_permission_failure_exhausts_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_root = tmp_path / "isolated-verification-root"
    temp_root.mkdir()
    attempts = 0
    now = 0.0

    def persistent_rmtree(path, *args, **kwargs):
        error = PermissionError("Git object remains locked")

        def persistent_remove(locked_path):
            nonlocal attempts
            attempts += 1
            raise PermissionError("Git object remains locked")

        kwargs["onerror"](
            persistent_remove,
            os.fspath(path),
            (PermissionError, error, None),
        )

    def advance(delay: float) -> None:
        nonlocal now
        now += delay

    monkeypatch.setattr(verification_module.shutil, "rmtree", persistent_rmtree)
    monkeypatch.setattr(verification_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(verification_module.time, "sleep", advance)
    monkeypatch.setattr(
        verification_module, "_TEMP_CLEANUP_MAX_ELAPSED_SECONDS", 0.2
    )

    with pytest.raises(PermissionError, match="remains locked"):
        verification_module._remove_temp_root(temp_root)

    assert attempts == 4
    assert now == pytest.approx(0.2)
    assert temp_root.exists()
    temp_root.rmdir()


def test_windows_nonzero_remains_canonical_failure_without_retry(
    tmp_path: Path,
) -> None:
    command = "tool-neutral-command"
    runner = RecordingRunner([completed(returncode=9, stderr=b"failed\n")])

    with pytest.raises(RuntimeVerificationError) as caught:
        execute_verification(
            (command,),
            run_id="RUN-120-005",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=tmp_path / "runtime" / "RUN-120-005",
            runner=runner,
            platform="nt",
            environment={},
        )

    assert len(runner.calls) == 1
    assert caught.value.evidence[0].source.command == command
    assert caught.value.evidence[0].result.exit_code == 9
    assert not Path(runner.calls[0][1]["env"]["TEMP"]).exists()


def test_posix_environment_not_mutated_with_windows_encoding(
    tmp_path: Path,
) -> None:
    command = "git diff --check"
    runner = RecordingRunner([completed()])

    execute_verification(
        (command,),
        run_id="RUN-027-007",
        subject_sha="def456",
        repository=tmp_path,
        raw_directory=tmp_path / ".git" / "aios" / "verification",
        runner=runner,
        platform="posix",
        environment={"CUSTOM": "123"},
    )

    assert runner.calls[0][0] == ("/bin/sh", "-c", command)
    assert runner.calls[0][1]["env"] == {"CUSTOM": "123"}


def test_first_nonzero_records_raw_evidence_and_stops(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [completed(), completed(returncode=7, stderr=b"failed\n"), completed()]
    )
    raw = tmp_path / ".git" / "aios" / "verification"

    with pytest.raises(RuntimeVerificationError) as caught:
        execute_verification(
            ("first", "second", "never"),
            run_id="RUN-027-003",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=raw,
            runner=runner,
            platform="posix",
        )

    assert len(runner.calls) == 2
    assert [item.source.command for item in caught.value.evidence] == [
        "first",
        "second",
    ]
    assert caught.value.evidence[-1].result.exit_code == 7
    assert (raw / "RUN-027-003-V002.raw").read_bytes().endswith(b"failed\n")


def test_invalid_utf8_fails_closed_after_storing_raw_bytes(tmp_path: Path) -> None:
    runner = RecordingRunner([completed(stdout=b"\xff")])
    raw = tmp_path / ".git" / "aios" / "verification"

    with pytest.raises(RuntimeVerificationError, match="strict UTF-8"):
        execute_verification(
            ("bad-output",),
            run_id="RUN-027-004",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=raw,
            runner=runner,
            platform="posix",
        )

    assert (raw / "RUN-027-004-V001.raw").read_bytes() == (
        b"STDOUT\n\xff\nSTDERR\n"
    )


def test_invalid_utf8_command_fails_before_shell_invocation(tmp_path: Path) -> None:
    runner = RecordingRunner([completed()])

    with pytest.raises(RuntimeVerificationError, match="command must be strict UTF-8"):
        execute_verification(
            ("bad-\udcff",),
            run_id="RUN-027-006",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=tmp_path / ".git" / "aios" / "verification",
            runner=runner,
            platform="posix",
        )

    assert runner.calls == []


def test_attaches_complete_evidence_set_without_changing_claim_semantics(
    tmp_path: Path,
) -> None:
    result = Result(
        head_sha="abc123",
        claims=(Claim("C1", ("AC1", "AC2"), "semantic claim", ()),),
        changed_files=("file.py",),
        unresolved=(),
    )
    runner = RecordingRunner([completed(), completed()])
    evidence = execute_verification(
        ("one", "two"),
        run_id="RUN-027-005",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / ".git" / "aios" / "verification-test",
        runner=runner,
        platform="posix",
    )

    attached = attach_verification_evidence(result, evidence)

    assert attached.claims[0].id == "C1"
    assert attached.claims[0].satisfies == ("AC1", "AC2")
    assert attached.claims[0].claim == "semantic claim"
    assert attached.claims[0].evidence == (
        "RUN-027-005-V001",
        "RUN-027-005-V002",
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell integration test")
def test_windows_real_subprocess_captures_non_ascii_unicode_as_utf8(
    tmp_path: Path,
) -> None:
    command = "python -c \"print('Tést Unicode: 🚀 — こんにちは')\""
    raw = tmp_path / ".git" / "aios" / "verification"

    evidence = execute_verification(
        (command,),
        run_id="RUN-034-REG",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=raw,
        platform="nt",
    )

    assert len(evidence) == 1
    assert evidence[0].result.exit_code == 0
    assert "Tést Unicode: 🚀 — こんにちは" in evidence[0].result.summary
    raw_content = (raw / "RUN-034-REG-V001.raw").read_bytes()
    assert "Tést Unicode: 🚀 — こんにちは".encode("utf-8") in raw_content


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell integration test")
def test_windows_real_subprocess_propagates_native_nonzero_exit_code(
    tmp_path: Path,
) -> None:
    command = "python -c \"import sys; sys.stderr.write('fatal boom\\n'); sys.exit(42)\""
    raw = tmp_path / ".git" / "aios" / "verification"

    with pytest.raises(RuntimeVerificationError) as caught:
        execute_verification(
            (command,),
            run_id="RUN-036-REG",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=raw,
            platform="nt",
        )

    assert "exit code 42" in str(caught.value)
    assert len(caught.value.evidence) == 1
    assert caught.value.evidence[0].result.exit_code == 42
    assert caught.value.evidence[0].source.command == command
    assert "fatal boom" in caught.value.evidence[0].result.summary
    raw_content = (raw / "RUN-036-REG-V001.raw").read_bytes()
    assert b"fatal boom" in raw_content
