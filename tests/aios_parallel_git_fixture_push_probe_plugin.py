"""Private probe: original PUSH calls are always delegated once, unchanged.

Raw process/root identities travel only through in-memory xdist workeroutput to
check isolation. Neither those identities nor test failure text enter the file
protocol. Nodeids are retained transiently solely to check the fixed collection.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import aios_parallel_git_fixture_push_diagnostic as contract


# Ordered, bounded literal patterns: overlapping messages use the first category.
# Operands are removed before matching so private names cannot supply signatures.
STDERR_PATTERNS = (
    ("LOCK_OR_REF_UPDATE", ("cannot lock ref", "unable to lock", "could not lock", "failed to update ref", "cannot update ref", "unable to create", "file exists")),
    ("PATH_OR_FILENAME", ("filename too long", "file name too long", "path too long", "invalid path", "invalid argument")),
    ("ACCESS_OR_PERMISSION", ("permission denied", "access is denied", "access denied", "operation not permitted", "read-only file system")),
    ("REPOSITORY_STATE", ("not a git repository", "does not appear to be a git repository", "repository not found", "bad repository", "remote unpack failed")),
    ("TRANSPORT_OR_REMOTE", ("unable to access", "could not read from remote repository", "could not resolve host", "connection refused", "connection reset", "failed to push some refs")),
    ("UNKNOWN_REVISION_OR_OBJECT", ("unknown revision", "bad object", "invalid object", "src refspec", "does not match any")),
)
MAX_STDERR = 8192
STAGES = ("setup", "call", "teardown")


def stderr_category(stderr: object, operands: tuple[str, ...]) -> str:
    text = stderr if isinstance(stderr, str) else ""
    # Redact before bounding: a long operand must not leave a signature fragment.
    variants = {variant for operand in operands if operand for variant in (
        operand, operand.replace("\\", "/"), operand.replace("/", "\\")
    )}
    for operand in sorted(variants, key=len, reverse=True):
        text = text.replace(operand, "<operand>")
    text = text[:MAX_STDERR].lower()
    for category, patterns in STDERR_PATTERNS:
        if any(pattern in text for pattern in patterns):
            return category
    return "OTHER"


class Recorder:
    def __init__(self, phase: str, worker: str):
        self.phase = phase
        self.worker = worker
        self.integrity = True
        self.pushes: list[dict] = []
        self.push_count = 0
        self.reports: dict[str, dict[str, str]] = {}
        self.assignments: dict[str, str] = {}
        self.collection_valid = False

    def record_report(self, report) -> None:
        node = report.nodeid
        if node not in ({contract.TARGET} if self.phase == "serial" else contract.EXPECTED):
            self.integrity = False
            return
        worker = getattr(report, "worker_id", self.worker)
        allowed_workers = {"serial"} if self.phase == "serial" else {f"gw{i}" for i in range(4)}
        if (worker not in allowed_workers or report.when not in STAGES
                or report.outcome not in {"passed", "failed", "skipped"}
                or node in self.assignments and self.assignments[node] != worker):
            self.integrity = False
            return
        stages = self.reports.setdefault(node, {})
        expected_stage = ("setup" if not stages else "call" if stages == {"setup": "passed"}
                          else "teardown" if "teardown" not in stages else None)
        if report.when in stages or report.when != expected_stage:
            self.integrity = False
            return
        stages[report.when] = report.outcome
        self.assignments[node] = worker

    def execution_valid(self, *, complete: bool) -> bool:
        expected = {contract.TARGET} if self.phase == "serial" else contract.EXPECTED
        if complete and set(self.reports) != expected:
            return False
        for reports in self.reports.values():
            if set(reports) != (set(STAGES) if reports.get("setup") == "passed" else {"setup", "teardown"}):
                return False
        return True

    def summary(self) -> dict:
        target = self.reports.get(contract.TARGET, {})
        return {
            "integrity": self.integrity,
            "collection_valid": self.collection_valid,
            "execution_valid": self.execution_valid(complete=self.phase == "serial"),
            "executed_count": len(self.reports),
            "non_target_failure_count": sum(
                node != contract.TARGET and "failed" in reports.values()
                for node, reports in self.reports.items()
            ),
            "target_reports": target,
            "pushes": self.pushes,
        }

    def _shape(self, command, args: tuple, kwargs: dict) -> tuple[dict, tuple[str, ...]]:
        if (args or not isinstance(command, (tuple, list)) or len(command) != 7
                or command[:2] not in (("git", "-C"), ["git", "-C"])
                or tuple(command[3:6]) != ("push", "--quiet", "origin")
                or kwargs != {"capture_output": True, "text": True, "check": True}
                or any(not isinstance(part, str) for part in command)):
            raise contract.DiagnosticError("push invocation shape mismatch")
        ordinal = self.push_count
        if not 1 <= ordinal <= contract.MAX_PUSHES:
            raise contract.DiagnosticError("push bound exceeded")
        repo = Path(command[2])
        remote = repo.parent / "upstream.git"
        family = "integration" if ordinal % 2 else "admission-failure"
        identity = hashlib.sha256(f"{family}-{(ordinal - 1) // 2}".encode()).hexdigest()
        ref = f"refs/heads/aios/{family}/{identity}"
        if (command[6] != f"{ref}:{ref}" or repo.name != "repo"
                or repo.parent.name != ("sandbox-a" if ordinal <= 2 else "sandbox-b")):
            raise contract.DiagnosticError("target operand identity mismatch")
        config = configparser.RawConfigParser()
        with (repo / ".git" / "config").open(encoding="utf-8") as stream:
            config.read_file(stream)
        remote_operand = config.get('remote "origin"', "url")
        if Path(remote_operand).resolve() != remote.resolve():
            raise contract.DiagnosticError("remote operand identity mismatch")
        lengths = {
            "phase": self.phase, "ordinal": ordinal,
            "remote_root_length": len(str(remote)), "ref_length": len(ref),
            "remote_lock_path_length": len(str(remote / f"{ref}.lock")),
        }
        if (lengths["remote_lock_path_length"] <= 260 or
                any(lengths[key] > contract.MAX_LENGTH for key in (
                    "remote_root_length", "ref_length", "remote_lock_path_length"
                ))):
            raise contract.DiagnosticError("path length contract mismatch")
        return lengths, (str(repo), str(remote), remote_operand, ref, command[6], identity)

    def wrap(self, original):
        def observe(command, *args, **kwargs):
            is_push = (isinstance(command, (list, tuple)) and len(command) >= 4
                       and str(command[0]).lower() in {"git", "git.exe"}
                       and command[3] == "push")
            if not is_push:
                return original(command, *args, **kwargs)
            self.push_count += 1
            shape = None
            operands = ()
            try:
                shape, operands = self._shape(command, args, kwargs)
            except Exception:
                # An observer defect must not prevent the original invocation.
                self.integrity = False

            def record(code, stderr):
                if shape is None or not contract.integer(code, contract.MIN_RETURN_CODE, contract.MAX_RETURN_CODE):
                    self.integrity = False
                    return
                fact = {**shape, "outcome": "failure" if code else "success", "return_code": code}
                if code:
                    fact["stderr_category"] = stderr_category(stderr, operands)
                self.pushes.append(fact)

            def safely_record(code, stderr):
                try:
                    record(code, stderr)
                except Exception:
                    self.integrity = False

            # Exactly one original call; result and original exception propagate.
            try:
                result = original(command, *args, **kwargs)
            except subprocess.CalledProcessError as exc:
                safely_record(exc.returncode, exc.stderr)
                raise
            except BaseException:
                self.integrity = False
                raise
            safely_record(getattr(result, "returncode", None), getattr(result, "stderr", None))
            if getattr(result, "returncode", None):  # check=True should have raised instead.
                self.integrity = False
            return result
        return observe


_recorder: Recorder | None = None
_config = None
_workers: dict[str, dict] = {}
_collections: dict[str, bool] = {}
_node_error = False


def pytest_sessionstart(session) -> None:
    from tests import git_fixture_support  # Fresh process-local cache, unchanged.
    del git_fixture_support
    global _recorder, _config, _node_error
    _config = session.config
    worker = _config.workerinput["workerid"] if hasattr(_config, "workerinput") else "serial"
    _recorder = Recorder(os.environ[contract.PHASE_ENV], worker)
    _workers.clear()
    _collections.clear()
    _node_error = False


def _valid_collection(ids, phase: str) -> bool:
    expected = {contract.TARGET} if phase == "serial" else contract.EXPECTED
    return len(ids) == len(expected) and set(ids) == expected


def pytest_collection_finish(session) -> None:
    _recorder.collection_valid = _valid_collection([item.nodeid for item in session.items], _recorder.phase)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if item.nodeid != contract.TARGET:
        yield
        return
    original = subprocess.run
    wrapper = _recorder.wrap(original)
    subprocess.run = wrapper
    try:
        yield
    finally:
        if subprocess.run is not wrapper:
            _recorder.integrity = False
        subprocess.run = original


def pytest_runtest_logreport(report) -> None:
    _recorder.record_report(report)


def _process(config) -> dict:
    from tests import git_fixture_support
    return {
        "pid": os.getpid(),
        "temp": str(config._tmp_path_factory.getbasetemp().resolve()),
        "cache": str(git_fixture_support._cache_root.resolve()),
        # Workers remove their process-local caches at exit, before the
        # controller's sessionfinish. Check existence in the owning process.
        "cache_present": git_fixture_support._cache_root.is_dir(),
    }


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids) -> None:
    key = node.gateway.id
    if key in _collections:
        _recorder.integrity = False
    _collections[key] = _valid_collection(ids, "n4")


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    global _node_error
    key = node.gateway.id
    if error is not None or key in _workers:
        _node_error = True
        return
    output = node.workeroutput.get("aios_parallel_git_push")
    if not isinstance(output, dict):
        _node_error = True
        return
    _workers[key] = output


def _isolated(processes: dict[str, dict], profile: Path) -> bool:
    # These dynamic facts are consumed here and never enter durable JSON.
    if any(not isinstance(process, dict) or set(process) != {"pid", "temp", "cache", "cache_present"}
           or process["cache_present"] is not True
           or any(not isinstance(process[key], str) or not 0 < len(process[key]) <= contract.MAX_LENGTH
                  for key in ("temp", "cache")) for process in processes.values()):
        return False
    if (len({p["pid"] for p in processes.values()}) != len(processes)
            or len({p["cache"] for p in processes.values()}) != len(processes)):
        return False
    for label, process in processes.items():
        expected = profile / "pytest"
        if label.startswith("gw"):
            expected /= "popen-" + label
        cache = Path(process["cache"])
        if (not contract.integer(process["pid"], 1, 2**32 - 1)
                or Path(process["temp"]) != expected
                or cache.parent != profile
                or not cache.name.startswith("aios-git-fixtures-")):
            return False
    return True


def pytest_sessionfinish(session, exitstatus) -> None:
    config = session.config
    summary = _recorder.summary()
    process = _process(config)
    if hasattr(config, "workerinput"):
        config.workeroutput["aios_parallel_git_push"] = {"summary": summary, "process": process}
        return
    phase = _recorder.phase
    labels = ["serial"] if phase == "serial" else [f"gw{i}" for i in range(4)]
    target_worker = _recorder.assignments.get(contract.TARGET)
    integrity = summary["integrity"] and not _node_error
    collection = summary["collection_valid"]
    pushes = summary["pushes"]
    processes = {"serial": process}
    if phase == "n4":
        collection = set(_collections) == set(labels) and all(value is True for value in _collections.values())
        integrity = integrity and set(_workers) == set(labels)
        pushes = []
        for label in labels:
            output = _workers.get(label)
            if not output or set(output) != {"summary", "process"}:
                integrity = False
                continue
            local = output["summary"]
            processes[label] = output["process"]
            if (not isinstance(local, dict)
                    or any(local.get(key) is not True for key in (
                        "integrity", "collection_valid", "execution_valid"
                    )) or not contract.integer(local.get("executed_count"), 0, len(contract.EXPECTED))
                    or not contract.integer(local.get("non_target_failure_count"), 0, len(contract.EXPECTED) - 1)):
                integrity = False
                continue
            assigned = {node: reports for node, reports in _recorder.reports.items()
                        if _recorder.assignments[node] == label}
            local_expected = {
                "integrity": True, "collection_valid": True, "execution_valid": True,
                "executed_count": len(assigned),
                "non_target_failure_count": sum(node != contract.TARGET and "failed" in reports.values()
                                                for node, reports in assigned.items()),
                "target_reports": assigned.get(contract.TARGET, {}),
                "pushes": local.get("pushes"),
            }
            if local != local_expected or (label != target_worker and local.get("pushes")):
                integrity = False
            if label == target_worker:
                pushes = local.get("pushes")
    reports = summary["target_reports"]
    outcome = ("failed" if "failed" in reports.values() else
               "skipped" if "skipped" in reports.values() else "passed")
    try:
        isolated = _isolated(processes, Path(os.environ[contract.ROOT_ENV]).resolve())
    except (KeyError, TypeError, ValueError):
        isolated = False
    payload = {
        "schema": contract.SCHEMA, "version": 1, "phase": phase,
        "exit_status": int(exitstatus), "integrity": integrity,
        "collection_valid": collection,
        "execution_valid": _recorder.execution_valid(complete=True),
        "process_isolated": isolated, "workers": labels, "target_worker": target_worker,
        "target_outcome": outcome, "target_reports": reports, "pushes": pushes,
        "non_target_failure_count": summary["non_target_failure_count"],
    }
    # Validate privacy/shape even when an integrity flag prevents publication.
    try:
        contract.validate_phase(phase, int(exitstatus), payload)
    except (contract.DiagnosticError, KeyError, TypeError, ValueError):
        payload = {"schema": contract.SCHEMA, "integrity": False}
    Path(os.environ[contract.OUTPUT_ENV]).write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
