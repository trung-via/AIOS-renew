"""Fixed serial then n4/load observation of the unchanged long-path Git test.

This command reports observations only. It neither retries nor chooses a fix.
Pytest output is discarded; the private protocol contains no dynamic operands.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TARGET_FILE = "tests/test_git_fixture_isolation.py"
TARGET = TARGET_FILE + "::test_depth_amplified_sandboxes_push_and_resolve_full_aios_refs"
TEST_NAMES = (
    "test_real_git_sandboxes_isolate_all_writable_state",
    "test_fast_materialization_supports_real_git_and_immutable_object_reuse",
    "test_packed_fixture_objects_remain_readable_and_committable",
    "test_fixture_object_inspection_rejects_missing_corrupt_and_wrong_type",
    "test_depth_amplified_sandboxes_push_and_resolve_full_aios_refs",
)
EXPECTED = frozenset(TARGET_FILE + "::" + name for name in TEST_NAMES)
SCHEMA = "AIOS_PARALLEL_GIT_FIXTURE_PUSH_OBSERVATION"
FORMAT = "AIOS_PARALLEL_GIT_FIXTURE_PUSH_DIAGNOSTIC"
PLUGIN = "aios_parallel_git_fixture_push_probe_plugin"
OUTPUT_ENV = "AIOS_PARALLEL_GIT_PUSH_OUTPUT"
PHASE_ENV = "AIOS_PARALLEL_GIT_PUSH_PHASE"
ROOT_ENV = "AIOS_PARALLEL_GIT_PUSH_ROOT"
MAX_BYTES = 16384
MAX_LENGTH = 32767
MAX_PUSHES = 4
MIN_RETURN_CODE = -(2**31)
MAX_RETURN_CODE = 2**32 - 1
CATEGORIES = frozenset({
    "LOCK_OR_REF_UPDATE", "PATH_OR_FILENAME", "ACCESS_OR_PERMISSION",
    "REPOSITORY_STATE", "TRANSPORT_OR_REMOTE", "UNKNOWN_REVISION_OR_OBJECT", "OTHER",
})


class DiagnosticError(ValueError):
    """An observation cannot be faithfully published."""


def integer(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def subject_identity(repository: Path) -> str:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *args], capture_output=True,
            text=True, check=True,
        )
        return result.stdout.strip()

    # A nested working directory must not silently select some other subject.
    if Path(git("rev-parse", "--show-toplevel")).resolve() != repository.resolve():
        raise DiagnosticError("subject root mismatch")
    head = git("rev-parse", "--verify", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", head) or git(
        "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"
    ):
        raise DiagnosticError("subject is not an exact clean commit")
    return head


def _read(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DiagnosticError("duplicate protocol field")
            result[key] = value
        return result

    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise DiagnosticError("observation size bound exceeded")
        return json.loads(content, object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError("missing or malformed observation") from exc


def run_pytest(repository: Path, root: Path, *, phase: str) -> tuple[int, dict]:
    if phase not in {"serial", "n4"}:
        raise DiagnosticError("unsupported phase")
    profile = root / phase
    profile.mkdir()
    output = profile / "observation.json"
    command = [
        sys.executable, "-m", "pytest", "-p", "xdist.plugin", "-p", PLUGIN,
        "-p", "no:cacheprovider", "-o", "addopts=", "-o", "log_file=",
        "--basetemp", str(profile / "pytest"), "-q",
    ]
    if phase == "n4":
        command += ["-n", "4", "--dist", "load", "--max-worker-restart", "0"]
    command.append(TARGET if phase == "serial" else TARGET_FILE)
    env = os.environ.copy()
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(key, None)
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(repository / "tests") + os.pathsep + str(repository),
        "TMP": str(profile), "TEMP": str(profile), "TMPDIR": str(profile),
        OUTPUT_ENV: str(output), PHASE_ENV: phase, ROOT_ENV: str(profile),
    })
    result = subprocess.run(command, cwd=repository, env=env, check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode, _read(output)


def validate_phase(phase: str, status: int, value: object) -> dict:
    fields = {"schema", "version", "phase", "exit_status", "integrity",
              "collection_valid", "execution_valid", "process_isolated", "workers",
              "target_worker", "target_outcome", "target_reports", "pushes",
              "non_target_failure_count"}
    if (not isinstance(value, dict) or set(value) != fields or value["schema"] != SCHEMA
            or type(value["version"]) is not int or value["version"] != 1
            or value["phase"] != phase):
        raise DiagnosticError("incompatible observation schema")
    if (not integer(status, 0, 1) or type(value["exit_status"]) is not int
            or value["exit_status"] != status):
        raise DiagnosticError("inconsistent pytest exit")
    if any(value[key] is not True for key in (
        "integrity", "collection_valid", "execution_valid", "process_isolated"
    )):
        raise DiagnosticError("observation identity or instrumentation failure")
    workers = ["serial"] if phase == "serial" else [f"gw{i}" for i in range(4)]
    if value["workers"] != workers or value["target_worker"] not in workers:
        raise DiagnosticError("missing or invalid execution worker")
    failures = value["non_target_failure_count"]
    if not integer(failures, 0, 0 if phase == "serial" else len(EXPECTED) - 1):
        raise DiagnosticError("failure count bound exceeded")
    reports = value["target_reports"]
    if (not isinstance(reports, dict) or set(reports) != {"setup", "call", "teardown"}
            or any(item not in {"passed", "failed", "skipped"} for item in reports.values())
            or reports["setup"] != "passed"):
        raise DiagnosticError("missing or inconsistent target reports")
    outcome = ("failed" if "failed" in reports.values() else
               "skipped" if "skipped" in reports.values() else "passed")
    if value["target_outcome"] != outcome or status != int(outcome == "failed" or failures > 0):
        raise DiagnosticError("contradictory target outcome")
    pushes = value["pushes"]
    if not isinstance(pushes, list) or not 1 <= len(pushes) <= MAX_PUSHES:
        raise DiagnosticError("missing or over-bound push observations")
    for ordinal, push in enumerate(pushes, 1):
        required = {"phase", "ordinal", "outcome", "return_code", "remote_root_length",
                    "ref_length", "remote_lock_path_length"}
        if not isinstance(push, dict) or push.get("outcome") not in {"success", "failure"}:
            raise DiagnosticError("malformed push")
        failed = push["outcome"] == "failure"
        if set(push) != required | ({"stderr_category"} if failed else set()):
            raise DiagnosticError("unsafe push fields")
        if (push["phase"] != phase or type(push["ordinal"]) is not int
                or push["ordinal"] != ordinal
                or not integer(push["return_code"], MIN_RETURN_CODE, MAX_RETURN_CODE)
                or failed != (push["return_code"] != 0)
                or (failed and push["stderr_category"] not in CATEGORIES)):
            raise DiagnosticError("inconsistent push result")
        if any(not integer(push[key], 1, MAX_LENGTH) for key in (
            "remote_root_length", "ref_length", "remote_lock_path_length"
        )) or (push["remote_lock_path_length"] <= 260 or
               push["remote_lock_path_length"] != push["remote_root_length"] + 1 + push["ref_length"] + 5):
            raise DiagnosticError("long lock path contract violated")
        # Ref lengths and order match the unchanged two families in two sandboxes.
        expected_ref_length = len("refs/heads/aios/" + ("integration" if ordinal % 2 else "admission-failure") + "/") + 64
        if push["ref_length"] != expected_ref_length:
            raise DiagnosticError("target ref shape mismatch")
        if failed and (outcome != "failed" or ordinal != len(pushes) or reports["call"] != "failed"):
            raise DiagnosticError("failed checked push contradicts target")
    if outcome == "passed" and len(pushes) != MAX_PUSHES:
        raise DiagnosticError("passing target omitted pushes")
    return value


def classify(serial: str, n4: str, non_target_failures: int) -> str:
    if serial == "failed":
        return "SERIAL_FAILURE"
    if serial == "passed" and n4 == "failed":
        return "PARALLEL_ONLY_REPRODUCED"
    if serial == n4 == "passed":
        return "CONTEXT_FAILURE_TARGET_PASS" if non_target_failures else "PASS_DRIFT"
    return "MIXED_OBSERVATION"


def diagnose(repository: Path, *, runner: Callable = run_pytest) -> dict:
    repository = repository.resolve()
    subject = subject_identity(repository)
    with tempfile.TemporaryDirectory(prefix="aios-parallel-git-push-") as directory:
        root = Path(directory)
        observations = []
        for phase in ("serial", "n4"):
            if subject_identity(repository) != subject:
                raise DiagnosticError("subject changed before phase")
            try:
                status, value = runner(repository, root, phase=phase)
            finally:
                if subject_identity(repository) != subject:
                    raise DiagnosticError("subject changed during phase")
            observations.append(validate_phase(phase, status, value))
    serial, n4 = observations
    return {
        "format": FORMAT, "version": 1, "subject_sha": subject, "worktree_clean": True,
        "target": TARGET, "phases": observations,
        "classification": classify(serial["target_outcome"], n4["target_outcome"], n4["non_target_failure_count"]),
    }


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("parallel Git fixture push diagnostic accepts no arguments", file=sys.stderr)
        return 2
    try:
        result = diagnose(Path.cwd())
    except (DiagnosticError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        print("parallel Git fixture push diagnostic integrity failure", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
