import os
import subprocess
from pathlib import Path

import pytest

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
        f"& {{ {command} }}",
    )
    assert runner.calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "PYTHONUTF8" not in runner.calls[0][1]["env"]
    assert runner.calls[0][1]["env"]["EXISTING_VAR"] == "val"
    assert evidence[0].source.command == command


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
