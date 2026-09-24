"""Private structured observations for the fixed BP-V4 parallel diagnostic."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import pytest


OUTPUT_ENV = "AIOS_BP_V4_DIAGNOSTIC_OUTPUT"
PREFIX_ENV = "AIOS_BP_V4_DIAGNOSTIC_PREFIXES"
PHASES = ("setup", "call", "teardown")
MAX_FAILURES = 18  # Six nodes, three pytest phases.
_collections: dict[str, list[str]] = {}
_workers: dict[str, dict[str, Any]] = {}
_worker_failures: dict[str, list[dict[str, Any]]] = {}
_worker_executions: dict[str, list[str]] = {}
_local_failures: list[dict[str, Any]] = []
_local_executions: list[str] = []


def _prefixes() -> dict[str, str]:
    value = json.loads(os.environ[PREFIX_ENV])
    if not isinstance(value, dict) or set(value) != {"subject", "diagnostic_temp", "pytest_basetemp", "user_home"}:
        raise ValueError("invalid diagnostic path prefixes")
    if any(not isinstance(path, str) or not Path(path).is_absolute() for path in value.values()):
        raise ValueError("invalid diagnostic path prefix")
    return value


def sanitize_path(value: object) -> str | None:
    """Persist only paths under recognized roots, with stable placeholders."""
    if not isinstance(value, (str, os.PathLike)):
        return None
    raw = os.fspath(value)
    if not raw or not Path(raw).is_absolute():
        return None
    normalized = os.path.normcase(os.path.normpath(raw))
    for name, root in sorted(_prefixes().items(), key=lambda pair: -len(pair[1])):
        prefix = os.path.normcase(os.path.normpath(root))
        if normalized == prefix or normalized.startswith(prefix + os.sep):
            suffix = normalized[len(prefix):].replace("\\", "/")
            if any(part in {"..", ""} for part in suffix.strip("/").split("/")) and suffix:
                return None
            return f"<{name}>{suffix}"
    return None


def _path_fact(value: object) -> dict[str, Any]:
    path = sanitize_path(value)
    return {"path": path, "length": len(os.fspath(value)) if path is not None else None}


def _normalized_cause_text(value: str) -> str:
    """Remove recognized absolute paths before deriving a persisted identity."""
    normalized = value
    for name, root in sorted(_prefixes().items(), key=lambda pair: -len(pair[1])):
        # Exception formatting can escape Windows separators. Match those
        # representations only within a registered, boundary-delimited root.
        prefix = r"[/\\]+".join(re.escape(part) for part in re.split(r"[/\\]+", root.rstrip("/\\")))
        pattern = (r"(?<![A-Za-z0-9_./\\])" + prefix
                   + r"(?=$|[/\\\s'\"`:;,()\[\]])(?P<suffix>(?:[/\\]+[^/\\\s'\"`:;,()\[\]]+)*)")
        normalized = re.sub(
            pattern,
            lambda match: f"<{name}>" + re.sub(r"[/\\]+", "/", match.group("suffix")),
            normalized, flags=re.IGNORECASE if os.name == "nt" else 0,
        )
    return normalized


def _git_diagnostic(stderr: str) -> str:
    """Return a fixed label; never persist arbitrary Git output."""
    lowered = stderr.lower()
    if any(phrase in lowered for phrase in ("not a git repository", "not a git directory", "repository does not exist", "unable to read current working directory")):
        return "repository-error"
    if any(phrase in lowered for phrase in ("cannot lock ref", "unable to lock", "cannot lock", "ref lock", "reference is at", ".lock': file exists", ".lock\": file exists")):
        return "ref-lock"
    if any(phrase in lowered for phrase in ("pathspec", "no such file or directory", "does not exist", "outside repository", "not in the working tree")):
        return "path-error"
    return "other"


def _cause(exc: BaseException | None) -> dict[str, Any] | None:
    if exc is None:
        return None
    kind = type(exc).__name__
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", kind):
        kind = "UnknownException"
    result: dict[str, Any] = {
        "exception_type": kind,
        "message_fingerprint": "sha256:" + hashlib.sha256(_normalized_cause_text(str(exc)).encode("utf-8", errors="replace")).hexdigest(),
    }
    if isinstance(exc, OSError):
        result.update({
            "errno": exc.errno if isinstance(exc.errno, int) else None,
            "winerror": getattr(exc, "winerror", None) if isinstance(getattr(exc, "winerror", None), int) else None,
            "filename": sanitize_path(exc.filename),
            "filename_length": len(os.fspath(exc.filename)) if sanitize_path(exc.filename) is not None else None,
        })
    if isinstance(exc, subprocess.CalledProcessError):
        command = exc.cmd
        executable = command[0] if isinstance(command, (list, tuple)) and command else command
        basename = os.path.basename(os.fspath(executable)).lower() if isinstance(executable, (str, os.PathLike)) else ""
        command_kind = "git" if basename in {"git", "git.exe"} else "other"
        result.update({
            "returncode": exc.returncode if isinstance(exc.returncode, int) else None,
            "command_kind": command_kind,
        })
        # Stderr can contain credentials or arbitrary test data. Persist only
        # its normalized identity and a fixed, bounded diagnostic label.
        if command_kind == "git" and exc.stderr is not None:
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
            result["git_stderr_fingerprint"] = "sha256:" + hashlib.sha256(_normalized_cause_text(stderr).encode("utf-8")).hexdigest()
            result["git_stderr_excerpt"] = _git_diagnostic(stderr)
            result["git_stderr_truncated"] = len(stderr) > 1024
    return result


def pytest_sessionstart(session: Any) -> None:
    del session
    _collections.clear()
    _workers.clear()
    _worker_failures.clear()
    _worker_executions.clear()
    _local_failures.clear()
    _local_executions.clear()
    _prefixes()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any):
    outcome = yield
    report = outcome.get_result()
    if report.when == "setup":
        _local_executions.append(report.nodeid.replace("\\", "/"))
    if report.failed and report.when in PHASES:
        _local_failures.append({
            "nodeid": report.nodeid.replace("\\", "/"),
            "phase": report.when,
            "cause": _cause(call.excinfo.value if call.excinfo is not None else None),
        })


def _worker_facts(config: Any) -> dict[str, Any]:
    factory = getattr(config, "_tmp_path_factory", None)
    temporary_root = str(Path(factory.getbasetemp()).resolve()) if factory is not None else None
    git_root = None
    for module_name in ("git_fixture_support", "tests.git_fixture_support"):
        module = sys.modules.get(module_name)
        value = getattr(module, "_cache_root", None) if module is not None else None
        if value is not None:
            git_root = str(Path(value).resolve())
            break
    return {
        "worker_id": config.workerinput["workerid"] if hasattr(config, "workerinput") else None,
        "process_id": os.getpid(),
        "temporary_root": _path_fact(temporary_root),
        "git_fixture_cache_root": _path_fact(git_root),
    }


def pytest_collection_finish(session: Any) -> None:
    config = session.config
    ids = [item.nodeid.replace("\\", "/") for item in session.items]
    if hasattr(config, "workerinput"):
        config.workeroutput["aios_diag_collection"] = ids
    else:
        config._aios_diag_collection = ids


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    config = session.config
    if hasattr(config, "workerinput"):
        config.workeroutput["aios_diag_facts"] = _worker_facts(config)
        config.workeroutput["aios_diag_failures"] = list(_local_failures)
        config.workeroutput["aios_diag_executions"] = list(_local_executions)
        return
    output = os.environ[OUTPUT_ENV]
    payload = {
        "schema": "AIOS_BP_V4_DIAGNOSTIC_PYTEST_OBSERVATION",
        "version": 1,
        "exit_status": int(exitstatus),
        "controller_collection": getattr(config, "_aios_diag_collection", None),
        "worker_collections": _collections,
        "workers": _workers,
        "failures": [fact for worker in sorted(_worker_failures) for fact in _worker_failures[worker]] if _workers else list(_local_failures),
        "worker_failure_reports": sorted(_worker_failures),
        "executed_nodeids": [item for worker in sorted(_worker_executions) for item in _worker_executions[worker]] if _workers else list(_local_executions),
        "worker_execution_reports": sorted(_worker_executions),
        "controller_process_id": os.getpid(),
        "controller_temporary_root": _worker_facts(config)["temporary_root"],
        "controller_git_fixture_cache_root": _worker_facts(config)["git_fixture_cache_root"],
    }
    Path(output).write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def pytest_xdist_node_collection_finished(node: Any, ids: list[str]) -> None:
    _collections[node.gateway.id] = [value.replace("\\", "/") for value in ids]


def pytest_testnodedown(node: Any, error: object) -> None:
    if error is not None:
        return
    output = node.workeroutput
    facts = output.get("aios_diag_facts")
    failures = output.get("aios_diag_failures")
    executions = output.get("aios_diag_executions")
    if isinstance(facts, dict):
        _workers[node.gateway.id] = {**facts, "collection": output.get("aios_diag_collection")}
    if isinstance(failures, list):
        _worker_failures[node.gateway.id] = failures
    if isinstance(executions, list):
        _worker_executions[node.gateway.id] = executions
