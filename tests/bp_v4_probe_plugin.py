"""Structured pytest observations for bounded parallel verification.

This plugin is loaded by the BP-V4 probe and selected full-suite wrapper. Workers
return data through xdist's structured ``workeroutput`` channel; they never
write the controller's observation file.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from aios_renew.verification_contract import (
    MAX_CANONICAL_BYTES, MAX_CANONICAL_FAILURES, MAX_CANONICAL_NODEID_CHARS,
    MAX_CANONICAL_REPORTS, MAX_FAILURE_DETAIL_CHARS, verification_digest,
    pytest_collection_identity as _pytest_collection_identity, VerificationContractError,
    validate_exact_collection,
)
from aios_renew.parallel_verification import (
    EXECUTION_CONTRACT, ProbeError, git_executable, load_toolchain,
    validate_pytest_execution,
)
import subprocess


_OUTPUT_ENV = "AIOS_BP_V4_PLUGIN_OUTPUT"
MAX_FAILURE_IDENTITIES = 20
MAX_NODEID_DISPLAY_CHARS = 240
_PHASE_ORDER = {"collect": -1, "setup": 0, "call": 1, "teardown": 2}
_node_collections: dict[str, dict[str, Any] | None] = {}
_expected_nodeids: set[str] | None = None
_worker_payloads: dict[str, dict[str, Any]] = {}
_local_failure_count = 0
_local_failure_facts: list[dict[str, Any]] = []
_local_failure_truncated = False
_parallel_failure_count = 0
_parallel_failure_facts: list[dict[str, Any]] = []
_parallel_failure_truncated = False
_reports: dict[tuple[str, str], dict[str, Any]] = {}
_canonical_errors: list[str] = []
_canonical_bytes = 0
_canonical_failure_count = 0
_subject_root = ""
_condition_snapshot = None
_session_config = None
_subject_sha = None


def _resolve_conditions() -> tuple[dict, dict]:
    profile = json.loads(os.environ.get("AIOS_V2_PROFILE", '{"profile":"pytest-observed-v2"}'))
    actual = getattr(_session_config, "_aios_execution_profile", None)
    if actual is not None:
        if any(key in profile and profile[key] != value for key, value in actual.items()):
            raise ProbeError("bound pytest profile disagrees with actual worker options")
        profile = {"profile": profile.get("profile", "pytest-observed-v2"), **actual}
    return profile, load_toolchain()


def _conditions() -> tuple[dict, dict]:
    global _condition_snapshot
    if _condition_snapshot is None:
        _condition_snapshot = _resolve_conditions()
    return _condition_snapshot


def _canonical_add(report: dict) -> None:
    global _canonical_bytes, _canonical_failure_count
    key = (report["nodeid"], report["phase"])
    if key in _reports:
        # Even equal repeated reports can mask retries or unstable execution.
        _canonical_errors.append("duplicate canonical phase observation")
        return
    size = len(json.dumps(report, sort_keys=True, ensure_ascii=True).encode("utf-8"))
    if report["outcome"] == "FAIL":
        _canonical_failure_count += 1
    if (len(_reports) >= MAX_CANONICAL_REPORTS
            or _canonical_failure_count > MAX_CANONICAL_FAILURES
            or len(report["nodeid"]) > MAX_CANONICAL_NODEID_CHARS
            or _canonical_bytes + size > MAX_CANONICAL_BYTES):
        _canonical_errors.append("canonical material exceeds declared bound")
        return
    _reports[key] = report
    _canonical_bytes += size


def _canonical_summary() -> dict:
    return {
        "complete": not _canonical_errors, "unstable": bool(_canonical_errors),
        "errors": sorted(set(_canonical_errors)),
        "reports": sorted(_reports.values(), key=lambda r: (r["nodeid"], _PHASE_ORDER[r["phase"]])),
        "failure_count": _canonical_failure_count,
    }


def _resolved(path: object) -> str:
    return str(Path(path).resolve())


def _exact_nodeid(nodeid: str) -> str:
    file_path, separator, nodes = nodeid.partition("::")
    return file_path.replace("\\", "/") + separator + nodes


def _failure_identity(nodeid: str, phase: str) -> dict[str, Any]:
    """Return one normalized, bounded identity without report payload text."""

    normalized = nodeid.replace("\\", "/")
    if len(normalized) <= MAX_NODEID_DISPLAY_CHARS:
        return {
            "nodeid": normalized,
            "phase": phase,
            "clipped": False,
            "fingerprint": None,
        }
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    suffix = f"...#sha256:{digest}"
    return {
        "nodeid": normalized[: MAX_NODEID_DISPLAY_CHARS - len(suffix)] + suffix,
        "phase": phase,
        "clipped": True,
        "fingerprint": f"sha256:{digest}",
    }


def _fact_key(fact: dict[str, Any]) -> tuple[str, int, str]:
    return (
        fact["nodeid"],
        _PHASE_ORDER[fact["phase"]],
        fact["fingerprint"] or "",
    )


def _bounded_add(
    facts: list[dict[str, Any]], fact: dict[str, Any]
) -> bool:
    """Keep the deterministic lowest bounded set; return whether anything is omitted."""

    if fact in facts:
        return False
    facts.append(fact)
    facts.sort(key=_fact_key)
    if len(facts) > MAX_FAILURE_IDENTITIES:
        facts.pop()
        return True
    return False


def _summary(
    count: int, facts: list[dict[str, Any]], truncated: bool
) -> dict[str, Any]:
    return {
        "reported_count": count,
        "displayed_count": len(facts),
        "display_limit": MAX_FAILURE_IDENTITIES,
        "displayed_identities": list(facts),
        "truncated": truncated,
        "canonical": _canonical_summary(),
    }


def pytest_sessionstart(session: Any) -> None:
    global _session_config, _subject_sha
    _session_config = getattr(session, "config", None)
    global _subject_root
    _subject_root = str(session.config.rootpath) if session is not None else ""
    global _condition_snapshot
    _condition_snapshot = _resolve_conditions()
    _subject_sha = None
    if getattr(_session_config, "_aios_execution_profile", None) is not None:
        result = subprocess.run((git_executable(), "-C", _subject_root, "rev-parse", "--verify", "HEAD"),
                                capture_output=True, text=True, check=False)
        if result.returncode == 0:
            _subject_sha = result.stdout.strip()
        expected = os.environ.get("AIOS_VERIFY_SUBJECT_SHA")
        if expected is not None and expected != _subject_sha:
            raise pytest.UsageError("actual pytest subject disagrees with bound commit")
    global _local_failure_count, _local_failure_facts, _local_failure_truncated
    global _parallel_failure_count, _parallel_failure_facts
    global _parallel_failure_truncated
    _local_failure_count = 0
    _local_failure_facts = []
    _local_failure_truncated = False
    _parallel_failure_count = 0
    _parallel_failure_facts = []
    _parallel_failure_truncated = False
    global _reports, _canonical_errors, _canonical_bytes, _canonical_failure_count
    _reports = {}
    _canonical_errors = []
    _canonical_bytes = 0
    _canonical_failure_count = 0
    global _expected_nodeids
    _expected_nodeids = None
    _node_collections.clear()
    _worker_payloads.clear()


def pytest_collectreport(report: Any) -> None:
    """Preserve collection failures too; exit 2 can never become nonblocking."""
    if not report.failed:
        return
    global _local_failure_count, _local_failure_truncated
    _local_failure_count += 1
    profile, toolchain = _conditions()
    detail = str(report.longreprtext)
    if _subject_root:
        detail = detail.replace(_subject_root, "<SUBJECT>").replace(_subject_root.replace("\\", "/"), "<SUBJECT>")
    fact = {"nodeid": _exact_nodeid(report.nodeid), "phase": "collect", "outcome": "FAIL",
            "profile": profile, "toolchain": toolchain}
    if not detail or len(detail) > MAX_FAILURE_DETAIL_CHARS:
        _canonical_errors.append("collection failure detail exceeds declared bound")
        fact.update(detail=None, fingerprint=None)
    else:
        fact.update(detail=detail, fingerprint=verification_digest(detail))
    _canonical_add(fact)
    if _bounded_add(_local_failure_facts, _failure_identity(report.nodeid, "collect")):
        _local_failure_truncated = True


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any):
    """Capture only failed runtest identity and phase in the executing process."""

    del call
    outcome = yield
    report = outcome.get_result()
    if report.when not in _PHASE_ORDER:
        return
    profile, toolchain = _conditions()
    fact = {"nodeid": _exact_nodeid(report.nodeid), "phase": report.when,
            "outcome": "FAIL" if report.failed else "SKIP" if report.skipped else "PASS"}
    if report.failed:
        # Normalize only the exact checkout root. Volatile temp paths, values,
        # captured output and divergent details stay visible and fail closed.
        detail = str(report.longreprtext) + "\n" + json.dumps(report.sections, ensure_ascii=True)
        root = str(item.config.rootpath)
        detail = detail.replace(root, "<SUBJECT>").replace(root.replace("\\", "/"), "<SUBJECT>")
        if not detail or len(detail) > MAX_FAILURE_DETAIL_CHARS:
            _canonical_errors.append("failure detail exceeds declared bound")
            fact.update(detail=None, fingerprint=None)
        else:
            fact.update(detail=detail, fingerprint=verification_digest(detail))
        fact.update(profile=profile, toolchain=toolchain)
    _canonical_add(fact)
    if not report.failed:
        return
    global _local_failure_count, _local_failure_truncated
    _local_failure_count += 1
    if _bounded_add(
        _local_failure_facts, _failure_identity(report.nodeid, report.when)
    ):
        _local_failure_truncated = True


def _worker_facts(config: Any) -> dict[str, Any]:
    worker_input = getattr(config, "workerinput", {})
    worker_id = worker_input.get("workerid")
    temporary_root = None
    factory = getattr(config, "_tmp_path_factory", None)
    if factory is not None:
        temporary_root = _resolved(factory.getbasetemp())

    # Import even for a narrow scope that does not materialize a Git fixture.
    # Each process owns its cache, including otherwise idle xdist workers.
    from tests.git_fixture_support import _cache_root
    git_cache_root = _resolved(_cache_root)

    return {
        "worker_id": worker_id,
        "process_id": os.getpid(),
        "temporary_root": temporary_root,
        "git_fixture_cache_root": git_cache_root,
        "profile": _conditions()[0], "toolchain": _conditions()[1],
    }


def pytest_collection_finish(session: Any) -> None:
    global _expected_nodeids
    config = session.config
    nodeids = [item.nodeid for item in session.items]
    identity = _collection_identity(nodeids)
    _expected_nodeids = {_exact_nodeid(n) for n in nodeids}
    if hasattr(config, "workerinput"):
        config.workeroutput["aios_bp_v4_collection"] = identity
    else:
        config._aios_bp_v4_collection = identity
        config._aios_v2_exact_collection = None
        if os.environ.get("AIOS_V2_EXACT_COLLECTION") == "1":
            # Opt-in serial truth only. Parallel controller/worker payloads
            # retain the compact conformance representation from TASK-319.
            exact = {"identity": identity, "nodeids": sorted(_exact_nodeid(n) for n in nodeids)}
            try:
                validate_exact_collection(exact)
                config._aios_v2_exact_collection = exact
            except (VerificationContractError, UnicodeError):
                # Optimization unavailable; preserve the ordinary phase and
                # failure population rather than emitting a partial collection.
                config._aios_v2_exact_collection = None


def _collection_identity(value: object) -> dict[str, Any] | None:
    try:
        return _pytest_collection_identity(value)
    except (VerificationContractError, UnicodeError):
        _canonical_errors.append("malformed pytest collection identity")
        return None


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    config = session.config
    try:
        if _resolve_conditions() != _conditions():
            _canonical_errors.append("profile or toolchain changed during verification")
    except (ProbeError, OSError, ValueError):
        _canonical_errors.append("profile or toolchain became unavailable during verification")
    if ("collect_only" in _conditions()[0]
            and _conditions()[0]["collect_only"] != bool(getattr(getattr(config, "option", None), "collectonly", False))):
        _canonical_errors.append("actual collection mode disagrees with bound profile")
    if hasattr(config, "workerinput"):
        config.workeroutput["aios_bp_v4_facts"] = _worker_facts(config)
        config.workeroutput["aios_bp_v4_failures"] = _summary(
            _local_failure_count,
            _local_failure_facts,
            _local_failure_truncated,
        )
        return

    serial_collection = getattr(config, "_aios_bp_v4_collection", None)
    controller_collection = serial_collection
    if _node_collections:
        controller_collection = _node_collections[sorted(_node_collections)[0]]
    controller_collection = _collection_identity(controller_collection)
    if not getattr(getattr(config, "option", None), "collectonly", False):
        expected = _expected_nodeids
        # Preserve completion checking for historical injected list fixtures.
        if expected is None and isinstance(serial_collection, list):
            expected = {_exact_nodeid(n) for n in serial_collection}
        if expected is not None:
            observed = {nodeid for nodeid, phase in _reports if phase != "collect"}
            if expected.difference(observed):
                _canonical_errors.append("pytest did not execute the complete selected population")
            if getattr(config, "_aios_execution_profile", None) is not None:
                for nodeid in expected.intersection(observed):
                    setup = _reports.get((nodeid, "setup"))
                    if (setup is None or (nodeid, "teardown") not in _reports
                            or setup["outcome"] == "PASS" and (nodeid, "call") not in _reports):
                        _canonical_errors.append("incomplete pytest phase reports")
    failures = (
        _summary(
            _parallel_failure_count,
            _parallel_failure_facts,
            _parallel_failure_truncated,
        )
        if _worker_payloads
        else _summary(
            _local_failure_count,
            _local_failure_facts,
            _local_failure_truncated,
        )
    )
    payload = {
        "schema": "AIOS_BP_V4_PYTEST_OBSERVATION",
        "version": 1,
        "exit_status": int(exitstatus),
        "controller_collection": controller_collection,
        "worker_collections": _node_collections,
        "workers": _worker_payloads,
        "failure_diagnostics": failures,
        "profile": _conditions()[0], "toolchain": _conditions()[1],
    }
    actual = getattr(config, "_aios_execution_profile", None)
    if actual is not None:
        payload.update(execution_contract=EXECUTION_CONTRACT, subject_sha=_subject_sha)
        try:
            if not hasattr(config, "workerinput") and not actual["collect_only"]:
                if (config.option.numprocesses != actual["workers"]
                        or config.option.dist != actual["distribution"]
                        or str(config.option.maxworkerrestart) != "0"):
                    raise ProbeError("actual pytest worker options changed during execution")
            validate_pytest_execution(payload, _conditions()[0], _conditions()[1])
            result = subprocess.run((git_executable(), "-C", _subject_root, "rev-parse", "--verify", "HEAD"),
                                    capture_output=True, text=True, check=False)
            if _subject_sha is not None and (result.returncode != 0 or result.stdout.strip() != _subject_sha):
                raise ProbeError("pytest changed its bound subject commit")
        except (ProbeError, OSError, ValueError) as exc:
            _canonical_errors.append(str(exc))
        payload["failure_diagnostics"]["canonical"] = _canonical_summary()
        if _canonical_errors and int(exitstatus) == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            payload["exit_status"] = int(session.exitstatus)
            reporter = config.pluginmanager.getplugin("terminalreporter")
            if reporter is not None:
                reporter.write_line("AIOS pytest execution failed: " + "; ".join(sorted(set(_canonical_errors))), red=True)
    exact = getattr(config, "_aios_v2_exact_collection", None)
    if os.environ.get("AIOS_V2_EXACT_COLLECTION") == "1" and _expected_nodeids is not None:
        exact = {"identity": controller_collection, "nodeids": sorted(_expected_nodeids)}
    if exact is not None:
        try:
            verification_digest({**payload, "exact_collection": exact})
            payload["exact_collection"] = exact
        except VerificationContractError:
            # No truncated identity list is ever presented as exact truth.
            pass
    output = os.environ.get(_OUTPUT_ENV)
    if not output:
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def pytest_xdist_node_collection_finished(node: Any, ids: list[str]) -> None:
    global _expected_nodeids
    identity = _collection_identity(ids)
    if node.gateway.id in _node_collections:
        _canonical_errors.append("duplicate worker collection observation")
    if _node_collections and identity != next(iter(_node_collections.values())):
        _canonical_errors.append("xdist worker collections disagree")
    _node_collections[node.gateway.id] = identity
    if _expected_nodeids is None and identity is not None:
        # One in-memory exact population suffices for completeness checking;
        # no full collection list is serialized in the final observation.
        _expected_nodeids = {_exact_nodeid(n) for n in ids}


def pytest_testnodedown(node: Any, error: object) -> None:
    if error is not None:
        _canonical_errors.append("worker terminated with an error")
    facts = node.workeroutput.get("aios_bp_v4_facts")
    collection = _collection_identity(node.workeroutput.get("aios_bp_v4_collection"))
    if isinstance(facts, dict):
        if node.gateway.id in _worker_payloads:
            _canonical_errors.append("duplicate or restarted xdist worker")
        payload = dict(facts)
        payload["collection"] = collection
        _worker_payloads[node.gateway.id] = payload
    elif getattr(_session_config, "_aios_execution_profile", None) is not None:
        _canonical_errors.append("missing worker execution facts")
    failures = node.workeroutput.get("aios_bp_v4_failures")
    if not isinstance(failures, dict):
        _canonical_errors.append("missing worker canonical failure data")
        return
    canonical = failures.get("canonical")
    if not isinstance(canonical, dict) or not isinstance(canonical.get("reports"), list):
        _canonical_errors.append("missing worker canonical population")
    else:
        if canonical.get("complete") is not True or canonical.get("unstable") is not False:
            _canonical_errors.append("incomplete or unstable worker population")
        for report in canonical["reports"]:
            _canonical_add(report)
        if canonical.get("failure_count") != sum(r.get("outcome") == "FAIL" for r in canonical["reports"]):
            _canonical_errors.append("incomplete worker failed identity population")
    count = failures.get("reported_count")
    identities = failures.get("displayed_identities")
    if not isinstance(count, int) or isinstance(count, bool) or not isinstance(
        identities, list
    ):
        return
    global _parallel_failure_count, _parallel_failure_truncated
    _parallel_failure_count += count
    if failures.get("truncated") is True:
        _parallel_failure_truncated = True
    for identity in identities:
        if isinstance(identity, dict) and _bounded_add(
            _parallel_failure_facts, identity
        ):
            _parallel_failure_truncated = True
