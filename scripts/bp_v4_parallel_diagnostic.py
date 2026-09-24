"""One fixed, observational BP-V4 serial/parallel failure diagnostic."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Sequence


FORMAT = "AIOS_BP_V4_PARALLEL_DIAGNOSTIC"
VERSION = 1
PLUGIN = "bp_v4_diagnostic_plugin"
OUTPUT_ENV = "AIOS_BP_V4_DIAGNOSTIC_OUTPUT"
PREFIX_ENV = "AIOS_BP_V4_DIAGNOSTIC_PREFIXES"
TARGETS = (
    "tests/test_correction_integration.py::test_clean_divergence_integration_success",
    "tests/test_operator.py::test_admission_diagnostic_is_content_addressed_idempotent_and_immutable",
    "tests/test_operator_correction_preflight.py::test_correction_preflight_remediation_fails_closed_on_invalid_cumulative_state_ac5",
    "tests/test_operator_unified_state.py::test_unified_state_integrated_remediation_chronology_advancement_reduces_to_repair_ac2",
    "tests/test_publication.py::test_integrated_remediation_publication_advances_main_ac7",
    "tests/test_authoring_ingress.py::test_submit_review_reconstructs_exact_integrated_remediation_repair_delta",
)
PROFILES = (("serial", 1), ("parallel-2", 2), ("parallel-4", 4))
PHASES = {"setup", "call", "teardown"}
CAUSE_BASE = {"exception_type", "message_fingerprint"}
CAUSE_OS = {"errno", "winerror", "filename", "filename_length"}
CAUSE_PROCESS = {"returncode", "command_kind"}
CAUSE_GIT = {"git_stderr_fingerprint", "git_stderr_excerpt", "git_stderr_truncated"}
CAUSE_INTEGRATION = {"integration_boundary", "integration_git_diagnostic", "integration_detail_fingerprint"}
CAUSE_LOCUS = {"source_locus"}
INTEGRATION_BOUNDARIES = {"git-command", "commit-tree", "push-ref", "merge-conflict", "remote-lifecycle", "task-document", "selector", "ref-collision", "remote-ref", "merge-base", "merge-tree", "commit-object", "lineage", "input", "other"}
INTEGRATION_GIT_BOUNDARIES = {"git-command", "commit-tree", "push-ref", "merge-conflict"}
GIT_LABELS = {"repository-error", "ref-lock", "path-error", "other"}


class DiagnosticError(RuntimeError):
    """The fixed experiment could not be observed faithfully."""


def _version(value: str) -> tuple[int, int]:
    match = re.match(r"^(\d+)\.(\d+)(?:\.|$)", value)
    if not match:
        raise DiagnosticError("malformed toolchain version")
    return int(match[1]), int(match[2])


def toolchain_identity() -> dict[str, str]:
    try:
        pytest_version = importlib.metadata.version("pytest")
        xdist_version = importlib.metadata.version("pytest-xdist")
        importlib.import_module("pytest")
        importlib.import_module("xdist.plugin")
    except (importlib.metadata.PackageNotFoundError, ImportError, RuntimeError) as exc:
        raise DiagnosticError("required pytest/xdist capability unavailable") from exc
    if not ((8, 2) <= _version(pytest_version) < (9, 0) and (3, 6) <= _version(xdist_version) < (4, 0)):
        raise DiagnosticError("incompatible pytest/xdist capability")
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "pytest_version": pytest_version,
        "pytest_xdist_version": xdist_version,
    }


def subject_identity(repository: Path) -> dict[str, Any]:
    head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=repository, capture_output=True, check=False, text=True)
    sha = head.stdout.strip()
    if head.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise DiagnosticError("exact Git subject unavailable")
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repository, capture_output=True, check=False)
    if status.returncode != 0 or status.stdout:
        raise DiagnosticError("verification subject is not a clean exact Git commit")
    return {"kind": "git-commit", "head_sha": sha, "worktree_clean": True}


def collection_identity(nodeids: object) -> dict[str, Any]:
    if not isinstance(nodeids, list) or len(nodeids) != len(TARGETS) or any(not isinstance(x, str) for x in nodeids):
        raise DiagnosticError("malformed or incomplete fixed collection")
    normalized = [x.replace("\\", "/") for x in nodeids]
    if set(normalized) != set(TARGETS) or len(set(normalized)) != len(TARGETS):
        raise DiagnosticError("fixed collection identity mismatch")
    digest = hashlib.sha256("".join(f"{x}\n" for x in sorted(normalized)).encode("utf-8")).hexdigest()
    return {"rule": "sorted-posix-nodeid-lf-sha256-v1", "count": len(TARGETS), "digest": digest}


def _read_observation(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError("missing or malformed structured observation") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema", "version", "exit_status", "controller_collection", "worker_collections",
        "workers", "failures", "worker_failure_reports", "executed_nodeids", "worker_execution_reports",
        "controller_process_id", "controller_temporary_root", "controller_git_fixture_cache_root",
    } or value["schema"] != "AIOS_BP_V4_DIAGNOSTIC_PYTEST_OBSERVATION" or value["version"] != 1:
        raise DiagnosticError("incompatible structured observation")
    return value


def run_pytest(repository: Path, temporary_root: Path, *, label: str, workers: int, collect_only: bool) -> tuple[int, dict[str, Any]]:
    if (label, workers, collect_only) not in {("collection", 1, True), ("serial", 1, False), ("parallel-2", 2, False), ("parallel-4", 4, False)}:
        raise DiagnosticError("unsupported diagnostic invocation")
    profile_root = temporary_root / label
    profile_root.mkdir()
    basetemp = profile_root / "pytest"
    output = profile_root / "observation.json"
    command = [sys.executable, "-m", "pytest", "-p", "xdist.plugin", "-p", PLUGIN,
               "-p", "no:cacheprovider", "--basetemp", str(basetemp), "-q"]
    if collect_only:
        command.append("--collect-only")
    elif workers > 1:
        command.extend(["-n", str(workers), "--dist", "load", "--max-worker-restart", "0"])
    command.extend(TARGETS)
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(repository / "tests") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
        "TMP": str(profile_root), "TEMP": str(profile_root), "TMPDIR": str(profile_root),
        OUTPUT_ENV: str(output),
        PREFIX_ENV: json.dumps({"subject": str(repository), "diagnostic_temp": str(temporary_root),
                                "pytest_basetemp": str(basetemp), "user_home": str(Path.home())}),
    })
    completed = subprocess.run(command, cwd=repository, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return completed.returncode, _read_observation(output)


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _path_fact(value: object, *, required: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"path", "length"}:
        raise DiagnosticError("malformed path fact")
    path, length = value["path"], value["length"]
    if path is None and length is None and not required:
        return value
    if not isinstance(path, str) or not re.fullmatch(r"<(?:subject|diagnostic_temp|pytest_basetemp|user_home)>(?:/[A-Za-z0-9_. /-]*)?", path) or not _integer(length) or length < 1 or length > 32767:
        raise DiagnosticError("unsanitized or malformed path fact")
    if any(part in {".", ".."} for part in path.split("/")):
        raise DiagnosticError("path fact contains traversal")
    return value


def _cause(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not CAUSE_BASE <= set(value) or set(value) - (CAUSE_BASE | CAUSE_OS | CAUSE_PROCESS | CAUSE_GIT | CAUSE_INTEGRATION | CAUSE_LOCUS):
        raise DiagnosticError("malformed failure cause")
    kind, digest = value["exception_type"], value["message_fingerprint"]
    if not isinstance(kind, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", kind) or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise DiagnosticError("malformed failure cause identity")
    if CAUSE_OS & set(value):
        if not CAUSE_OS <= set(value) or kind not in {"OSError", "FileNotFoundError", "PermissionError", "NotADirectoryError", "IsADirectoryError", "TimeoutError", "BlockingIOError", "ProcessLookupError", "ChildProcessError", "FileExistsError", "InterruptedError", "BrokenPipeError", "ConnectionError", "ConnectionRefusedError", "ConnectionResetError", "ConnectionAbortedError"}:
            raise DiagnosticError("invalid OS failure detail")
        if any(value[key] is not None and not _integer(value[key]) for key in ("errno", "winerror", "filename_length")):
            raise DiagnosticError("invalid OS failure number")
        filename = value["filename"]
        if filename is not None and (not isinstance(filename, str) or not re.fullmatch(r"<(?:subject|diagnostic_temp|pytest_basetemp|user_home)>(?:/[A-Za-z0-9_. /-]*)?", filename)):
            raise DiagnosticError("unsanitized OS filename")
        if (filename is None) != (value["filename_length"] is None):
            raise DiagnosticError("inconsistent OS filename length")
    if CAUSE_PROCESS & set(value):
        if not CAUSE_PROCESS <= set(value) or kind != "CalledProcessError" or (value["returncode"] is not None and not _integer(value["returncode"])) or value["command_kind"] not in {"git", "other"}:
            raise DiagnosticError("invalid subprocess failure detail")
    if CAUSE_GIT & set(value):
        if not CAUSE_GIT <= set(value) or value.get("command_kind") != "git" or not isinstance(value["git_stderr_excerpt"], str) or value["git_stderr_excerpt"] not in GIT_LABELS or not isinstance(value["git_stderr_truncated"], bool) or not isinstance(value["git_stderr_fingerprint"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["git_stderr_fingerprint"]):
            raise DiagnosticError("invalid Git failure detail")
    if CAUSE_INTEGRATION & set(value):
        boundary = value.get("integration_boundary")
        if kind != "CorrectionIntegrationError" or not isinstance(boundary, str) or boundary not in INTEGRATION_BOUNDARIES:
            raise DiagnosticError("invalid integration boundary")
        git_fields = {"integration_git_diagnostic", "integration_detail_fingerprint"}
        if boundary in INTEGRATION_GIT_BOUNDARIES:
            if not git_fields <= set(value) or not isinstance(value["integration_git_diagnostic"], str) or value["integration_git_diagnostic"] not in GIT_LABELS or not isinstance(value["integration_detail_fingerprint"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["integration_detail_fingerprint"]):
                raise DiagnosticError("invalid wrapped Git detail")
        elif git_fields & set(value):
            raise DiagnosticError("unexpected wrapped Git detail")
    if kind == "CorrectionIntegrationError" and "integration_boundary" not in value:
        raise DiagnosticError("missing integration boundary")
    if "source_locus" in value:
        locus = value["source_locus"]
        if not isinstance(locus, dict) or set(locus) != {"path", "line"} or not isinstance(locus["path"], str) or len(locus["path"]) > 200 or not re.fullmatch(r"<subject>/tests/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py", locus["path"]) or not _integer(locus["line"]) or not 0 < locus["line"] <= 1_000_000:
            raise DiagnosticError("invalid source locus")
    return value


def _failures(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 18:
        raise DiagnosticError("unbounded failure facts")
    seen: set[tuple[str, str]] = set()
    facts = []
    for fact in value:
        if not isinstance(fact, dict) or set(fact) != {"nodeid", "phase", "cause"} or fact["nodeid"] not in TARGETS or fact["phase"] not in PHASES:
            raise DiagnosticError("invalid fixed failure identity")
        key = (fact["nodeid"], fact["phase"])
        if key in seen:
            raise DiagnosticError("duplicate failure identity")
        seen.add(key)
        cause = _cause(fact["cause"])
        if cause is not None and "source_locus" in cause and cause["source_locus"]["path"] != "<subject>/" + fact["nodeid"].split("::", 1)[0]:
            raise DiagnosticError("source locus is outside fixed target")
        facts.append({**fact, "cause": cause})
    return sorted(facts, key=lambda x: (x["nodeid"], ("setup", "call", "teardown").index(x["phase"])))


def _profile(label: str, workers: int, status: int, observation: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
    if not _integer(status) or not _integer(observation["exit_status"]) or status != observation["exit_status"]:
        raise DiagnosticError("pytest exit status mismatch")
    controller_pid = observation["controller_process_id"]
    if not _integer(controller_pid) or controller_pid <= 0:
        raise DiagnosticError("missing controller process")
    controller_root = _path_fact(observation["controller_temporary_root"])
    controller_git_root = _path_fact(observation["controller_git_fixture_cache_root"], required=workers == 1)
    if controller_root["path"] != "<pytest_basetemp>" or (controller_git_root["path"] is not None and not controller_git_root["path"].startswith(f"<diagnostic_temp>/{label}/")):
        raise DiagnosticError("controller roots do not match isolated profile")
    collections, reported = observation["worker_collections"], observation["workers"]
    if not isinstance(collections, dict) or not isinstance(reported, dict):
        raise DiagnosticError("malformed worker data")
    worker_facts: list[dict[str, Any]] = []
    if workers == 1:
        if collections or reported or observation["worker_failure_reports"] or observation["worker_execution_reports"] or collection_identity(observation["controller_collection"]) != canonical:
            raise DiagnosticError("serial collection or worker mismatch")
    else:
        expected = {f"gw{i}" for i in range(workers)}
        if set(collections) != expected or set(reported) != expected or set(observation["worker_failure_reports"]) != expected or set(observation["worker_execution_reports"]) != expected or observation["controller_collection"] is not None:
            raise DiagnosticError("missing or unexpected workers")
        for key in sorted(expected):
            item = reported[key]
            if not isinstance(item, dict) or set(item) != {"worker_id", "process_id", "temporary_root", "git_fixture_cache_root", "collection"} or item["worker_id"] != key or item["collection"] != collections[key] or collection_identity(item["collection"]) != canonical:
                raise DiagnosticError("worker collection mismatch")
            if not _integer(item["process_id"]) or item["process_id"] <= 0 or item["process_id"] == controller_pid:
                raise DiagnosticError("invalid worker process")
            worker_facts.append({"worker_id": key, "process_id": item["process_id"],
                                 "temporary_root": _path_fact(item["temporary_root"]),
                                 "git_fixture_cache_root": _path_fact(item["git_fixture_cache_root"])})
            if not worker_facts[-1]["temporary_root"]["path"].startswith("<pytest_basetemp>/") or not worker_facts[-1]["git_fixture_cache_root"]["path"].startswith(f"<diagnostic_temp>/{label}/"):
                raise DiagnosticError("worker roots do not match isolated profile")
        for field in ("process_id", "temporary_root", "git_fixture_cache_root"):
            values = [item[field] if field == "process_id" else item[field]["path"] for item in worker_facts]
            if len(set(values)) != workers:
                raise DiagnosticError("workers share process or mutable roots")
        if controller_git_root["path"] is not None and controller_git_root["path"] in {item["git_fixture_cache_root"]["path"] for item in worker_facts}:
            raise DiagnosticError("controller and worker share fixture cache")
    failures = _failures(observation["failures"])
    executions = observation["executed_nodeids"]
    if not isinstance(executions, list) or len(executions) != len(TARGETS) or any(not isinstance(item, str) for item in executions) or set(executions) != set(TARGETS):
        raise DiagnosticError("missing or duplicate target execution")
    if status == 0 and failures:
        raise DiagnosticError("failure facts contradict pytest exit")
    if status == 1 and not failures:
        raise DiagnosticError("pytest failure lacks bounded failure facts")
    return {"profile": label, "workers": workers, "pytest_exit_status": status,
            "controller_process_id": controller_pid, "controller_temporary_root": controller_root,
            "controller_git_fixture_cache_root": controller_git_root,
            "worker_facts": worker_facts, "failures": failures}


Runner = Callable[..., tuple[int, dict[str, Any]]]


def diagnose(repository: Path, *, runner: Runner = run_pytest, loader: Callable[[], dict[str, str]] = toolchain_identity) -> dict[str, Any]:
    repository = repository.resolve()
    toolchain = loader()
    subject = subject_identity(repository)
    profiles: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="aios-bp-v4-diagnostic-") as directory:
        root = Path(directory)
        status, observed = runner(repository, root, label="collection", workers=1, collect_only=True)
        if status != 0 or observed["exit_status"] != 0 or observed["worker_collections"] or observed["workers"]:
            raise DiagnosticError("fixed collection failed")
        canonical = collection_identity(observed["controller_collection"])
        if subject_identity(repository) != subject:
            raise DiagnosticError("collection changed exact subject")
        for label, workers in PROFILES:
            status, observed = runner(repository, root, label=label, workers=workers, collect_only=False)
            profile = _profile(label, workers, status, observed, canonical)
            if subject_identity(repository) != subject:
                raise DiagnosticError("profile changed exact subject")
            profiles.append(profile)
    sets = [{fact["nodeid"] for fact in p["failures"]} for p in profiles]
    serial, worker2, worker4 = sets
    comparisons = {
        "common_failed_nodes": sorted(serial & worker2 & worker4),
        "serial_only_nodes": sorted(serial - worker2 - worker4),
        "parallel_only_nodes": sorted((worker2 | worker4) - serial),
        "worker2_only_nodes": sorted(worker2 - serial - worker4),
        "worker4_only_nodes": sorted(worker4 - serial - worker2),
    }
    return {"format": FORMAT, "version": VERSION, "subject": subject, "toolchain": toolchain,
            "targets": list(TARGETS), "collection": canonical, "profiles": profiles, "comparisons": comparisons}


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("BP-V4 diagnostic accepts no arguments", file=sys.stderr)
        return 2
    try:
        result = diagnose(Path.cwd())
    except (DiagnosticError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as exc:
        del exc
        print("BP-V4 diagnostic integrity failure", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
