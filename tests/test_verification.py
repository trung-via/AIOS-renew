import os
import stat
import subprocess
from pathlib import Path

import pytest

import aios_renew.verification as verification_module
from aios_renew.artifacts import Claim, Result
from aios_renew.verification import (
    RuntimeVerificationError,
    attach_verification_evidence,
    execute_verification,
)


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
