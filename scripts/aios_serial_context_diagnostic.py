"""Fixed, bounded serial context observation for RUN-233-002-V002 targets."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Sequence

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.aios_full_suite_contention_diagnostic import (
    DiagnosticError, _fingerprint, _nodeid, _safe_nodeid, _path_fact,
    subject_identity,
)
from scripts.bp_v4_parallel_diagnostic import _cause


FORMAT = "AIOS_SERIAL_CONTEXT_DIAGNOSTIC"
SCHEMA = "AIOS_SERIAL_CONTEXT_PYTEST_OBSERVATION"
OUTPUT_ENV = "AIOS_SERIAL_CONTEXT_OUTPUT"
PREFIX_ENV = "AIOS_BP_V4_DIAGNOSTIC_PREFIXES"
FILES = ("tests/test_hot_swap_conformance.py", "tests/test_operator.py")
FAILURE_LIMIT = 128
PHASE_ORDER = {"setup": 0, "call": 1, "teardown": 2}
TARGETS_DIGEST = "dee57adf8d0f606bee41eed302dbd2a961af2b3fff52e631df018a578df82fad"
# Bound verbatim from the serial_reproduced_nodeids field of RUN-233-002-V002
# in RESULT artifact f4db25e086cc41388c0c4bb198378969bea6f0c3.
TARGETS = (
    "tests/test_hot_swap_conformance.py::test_scenario_2_fresh_correction_accepts_only_current_finding#sha256:6ddd555377a73d5550ca6121173981d9ee2d8eeb5b6a4ef76ba68700debc9579",
    "tests/test_operator.py::test_bootstrap_ambiguous_reentry_fails_closed#sha256:ccf3f5a23963446dec354d1deaa9cff3b8a484ac4d90899b38c9eab1b2a42af0",
    "tests/test_operator.py::test_bootstrap_exact_edge_reentry_and_target_ownership#sha256:518f53515fcde7399f1bb16cb2921b90a68f74adf73a003ed7d789c4cd16c76e",
    "tests/test_operator.py::test_migration_exact_handoff_and_replay_rejection#sha256:11da76f7e59bc2f9a77e0aa42515db54f6c512a1e8ca26e0d02827068c13a9c9",
    "tests/test_operator.py::test_migration_reconciles_reserved_run_without_executor_reentry#sha256:39ccb5e352a4c0304d2994dfbaa104544a166daf5af80e25c44f6ec7fea21d47",
    "tests/test_operator.py::test_migration_reconciles_reserved_run_without_executor_reentry#sha256:a64d496475ab5277a1d9fa53c7b0f9a91b4d30cf40a916c7d9c5d758ad8ac568",
    "tests/test_operator.py::test_migration_reconciles_reserved_run_without_executor_reentry#sha256:fab7b4db43499a0b1a294925bd8baef21864e7c032ad0d116151448e09d5f5be",
    "tests/test_operator.py::test_migration_resumes_consumed_handoff_before_run#sha256:500b4245521ef8bb43083ea1969b203a37ce008764be4256b054b7b0985902bb",
    "tests/test_operator.py::test_migration_resumes_consumed_handoff_before_run#sha256:f053e319cd4429886b210584eab559200672947877dd7004769ded2bfd915d80",
    "tests/test_operator.py::test_source_bootstrap_injected_exact_staging#sha256:07b0b390ae8981b52f2358bd6b9a543fc3b752f8f9165ebd27752f626561f65a",
    "tests/test_operator.py::test_source_bootstrap_production_rejects_retired_replacement_target#sha256:cab8be81070545f971d866fadd8d30fe0f0c11bc68925ed4ebbba38928a5e4e9",
    "tests/test_operator.py::test_source_bootstrap_recovery_exact_replay_and_history#sha256:5d7d9bec58b1c73b51724061609da22debdc86990b672f66e93ac25b9339d22b",
    "tests/test_operator.py::test_source_bootstrap_recovery_exact_replay_and_history#sha256:613c1e4b49282b5b96b2faee88945529e77a16c1432b148b18f1453bd2e0867d",
    "tests/test_operator.py::test_source_bootstrap_recovery_interruption_keeps_old_authority#sha256:f2b8a13e5a3c46d59d08c3967377fca8d1307245bef234e1206b6e58d9c0ab57",
    "tests/test_operator.py::test_source_bootstrap_recovery_preserves_completed_migration_history#sha256:4bb046fc913874cd49d4508a5c7f1c99975c476e780cb0d4a6cfdb5fea61dee7",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:1633d54bc82b03494a6b2bdde16b249628f08b87b0b596db5c80d7c3cfd3510e",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:1a41811d5d1cbea16f93376fcea61153e30a3767b93f1823f5d12a855d63bda0",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:2a9536e6225cf9b79ed8bec9e718e22113751ccd175af7bf383ffcc978689608",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:3072dc33ab3c8fc966d509425a5c2efdd43d8907e3b466d1dfc4405d7f7ae6e8",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:507b3ac4dad6642ee845ab8ff8b7435562e0abc41463b4d27a8cfccbc55a1972",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:7f751a31366e865ca263e2b71a2c73bd6d503986235d58af62ade085aca6f7c7",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:ad37ee143dceacab00bac6b5fe543378d05582abd0cee39aa39436b2cbf9b656",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:b58ab6ed3fda7d1af5440ad40d7d813350ad1ced4dd6e21bac7841916599d882",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:ba65bd02a377e0d4ce20a31219a1f34fd4a7bdba8aaebddcf2905434a4a043fd",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:c0155bb174815fb00236ba9b3d2660293c68825ecef69b933c0368cb19f593cc",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:c45cf9fec37bafec93cf68f253b9ef01ed7347a2db37c4789a68820c8675c681",
    "tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:ccd3947f64ff07997523913f752496b35b85bfdd8ac6bd083c9f69c70048912b",
    "tests/test_operator.py::test_source_bootstrap_rejects_replayed_or_mismatched_handoff#sha256:88077a0303444750ffd8f283e3bacc8eb692f5db9894b9eb2b03869479bb021e",
    "tests/test_operator.py::test_source_bootstrap_stale_upstream_after_edge_has_no_run#sha256:63a41b73d7ef5fc3ad33e970c17a3e99b032e38956dc3bacea11517931d267ca",
    "tests/test_operator.py::test_source_bootstrap_target_reconciles_one_run#sha256:035796171affea24d68846133412af0946e0b62ee02f13ca27246698faefb737",
    "tests/test_operator.py::test_source_bootstrap_target_reconciles_one_run#sha256:e3a3e5f375f09ef834bccf4f6ed69d440c5173a68710373b96dd5f8b2a2e869d",
    "tests/test_operator.py::test_source_bootstrap_target_reconciles_one_run#sha256:ffe970b59f63ae64d3a11d0b8ac584488f8f16b42b49b9406e37a7f44b0ea16d",
    "tests/test_operator.py::test_source_repair_bootstrap_closed_and_exact_replay#sha256:6a39907343d56414e42e85d6eb786eb8d25628580ab235403baa75dc05536977",
    "tests/test_operator.py::test_source_repair_bootstrap_requires_completed_failed_v2_lineage#sha256:0d5b3fc36d52c0957a3c613eb534d2c61b7d2d0010d488c31dff9fa894bf108d",
    "tests/test_operator.py::test_source_repair_bound_target_consumes_without_matching_activation#sha256:be60bee57256685b75d55b33155692fa7e2d39afc87e75ade33e78e16f798a7a",
)


def _targets() -> set[str]:
    if (len(TARGETS) != 35 or len(set(TARGETS)) != 35
            or any(not isinstance(identity, str)
                   or not re.fullmatch(r"tests/test_(?:operator|hot_swap_conformance)\.py::(?:[A-Za-z_][A-Za-z0-9_]*)#sha256:[0-9a-f]{64}", identity)
                   for identity in TARGETS)
            or hashlib.sha256("".join(identity + "\n" for identity in TARGETS).encode()).hexdigest()
            != TARGETS_DIGEST):
        raise DiagnosticError("invalid fixed target identity binding")
    return set(TARGETS)


def _ordered_ids(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 10000:
        raise DiagnosticError("malformed collection or execution identities")
    ids = [_nodeid(item) for item in value]
    if len(ids) != len(set(ids)) or len({_safe_nodeid(item) for item in ids}) != len(ids):
        raise DiagnosticError("duplicated or ambiguous collection or execution identity")
    return ids


def _resolve(collected: list[str]) -> list[str]:
    targets = _targets()
    bound: dict[str, str] = {}
    for node in collected:
        safe = _safe_nodeid(node)
        if safe in targets:
            if safe in bound:
                raise DiagnosticError("ambiguous target resolution")
            bound[safe] = node
    if set(bound) != targets:
        raise DiagnosticError("missing or changed target identity")
    return [node for node in collected if _safe_nodeid(node) in targets]


# This module is also the private pytest observer. Its raw observation exists
# only under the automatically removed experiment temporary directory.
_collection: list[str] | None = None
_executions: list[str] = []
_failures: list[dict[str, Any]] = []
_overflow = False


def pytest_sessionstart(session: Any) -> None:
    del session
    importlib.import_module("tests.git_fixture_support")
    global _collection, _overflow
    _collection = None
    _executions.clear()
    _failures.clear()
    _overflow = False


def pytest_collection_finish(session: Any) -> None:
    global _collection
    _collection = [item.nodeid for item in session.items]


def _record(fact: dict[str, Any]) -> None:
    global _overflow
    identities = {item["nodeid"] for item in _failures}
    if fact["nodeid"] not in identities and len(identities) >= FAILURE_LIMIT:
        _overflow = True
    elif fact not in _failures:
        _failures.append(fact)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any):
    outcome = yield
    report = outcome.get_result()
    if report.when == "setup":
        _executions.append(report.nodeid)
    if report.failed and report.when in PHASE_ORDER:
        from bp_v4_diagnostic_plugin import _cause as sanitize_cause

        _record({"nodeid": report.nodeid, "phase": report.when,
                 "cause": sanitize_cause(call.excinfo.value if call.excinfo is not None else None,
                                          excinfo=call.excinfo, nodeid=report.nodeid)})


def _observer_process(config: Any) -> dict[str, Any]:
    from bp_v4_diagnostic_plugin import _path_fact as sanitize_path

    factory = getattr(config, "_tmp_path_factory", None)
    module = sys.modules.get("tests.git_fixture_support")
    cache = getattr(module, "_cache_root", None) if module is not None else None
    return {
        "process_id": os.getpid(),
        "temporary_root": sanitize_path(str(Path(factory.getbasetemp()).resolve()) if factory else None),
        "git_fixture_cache_root": sanitize_path(str(Path(cache).resolve()) if cache else None),
    }


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if OUTPUT_ENV not in os.environ:
        return
    collection = _collection
    payload = {
        "schema": SCHEMA, "version": 1, "exit_status": int(exitstatus),
        "collection": collection,
        "nodeid_fingerprints": {node: _fingerprint(node) for node in collection or []},
        "executed_nodeids": _executions, "failures": _failures,
        "over_bound": _overflow, "process": _observer_process(session.config),
    }
    Path(os.environ[OUTPUT_ENV]).write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


FIELDS = {"schema", "version", "exit_status", "collection", "nodeid_fingerprints",
          "executed_nodeids", "failures", "over_bound", "process"}


def _read(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise DiagnosticError("duplicate observation key")
            value[key] = item
        return value

    try:
        if path.stat().st_size > 32_000_000:
            raise DiagnosticError("observation size bound exceeded")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError("missing or malformed observation") from exc
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise DiagnosticError("incompatible observation")
    return value


def run_pytest(repository: Path, root: Path, *, label: str,
               selectors: tuple[str, ...]) -> tuple[int, dict[str, Any]]:
    if (label == "collection" and selectors) or (label == "phase-a" and len(selectors) != 35) or (
        label == "phase-b" and selectors != FILES
    ) or label not in {"collection", "phase-a", "phase-b"}:
        raise DiagnosticError("unsupported diagnostic invocation")
    profile = root / label
    profile.mkdir()
    basetemp = profile / "pytest"
    output = profile / "observation.json"
    command = [sys.executable, "-m", "pytest", "-p", "scripts.aios_serial_context_diagnostic",
               "-p", "no:cacheprovider", "--basetemp", str(basetemp), "-q"]
    if label == "collection":
        command.append("--collect-only")
    command.extend(selectors)
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(repository / "tests") + os.pathsep + str(repository) +
                      (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
        "TMP": str(profile), "TEMP": str(profile), "TMPDIR": str(profile),
        OUTPUT_ENV: str(output),
        PREFIX_ENV: json.dumps({"subject": str(repository), "diagnostic_temp": str(root),
                                "pytest_basetemp": str(basetemp), "user_home": str(Path.home())}),
    })
    completed = subprocess.run(command, cwd=repository, env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False)
    return completed.returncode, _read(output)


def _phase(label: str, status: int, value: dict[str, Any],
           expected: list[str] | None) -> tuple[dict[str, Any], list[str], tuple[int, str, str]]:
    if (not isinstance(value, dict) or set(value) != FIELDS or value["schema"] != SCHEMA
            or value["version"] != 1 or type(status) is not int
            or type(value["exit_status"]) is not int or status != value["exit_status"]
            or status not in ({0} if label == "collection" else {0, 1})):
        raise DiagnosticError("phase exit or schema mismatch")
    collected = _ordered_ids(value["collection"])
    if not collected or (expected is not None and collected != expected):
        raise DiagnosticError("collection identity or order mismatch")
    fingerprints = value["nodeid_fingerprints"]
    if (not isinstance(fingerprints, dict) or set(fingerprints) != set(collected)
            or len(set(fingerprints.values())) != len(collected)
            or any(fingerprints[node] != _fingerprint(node) for node in collected)):
        raise DiagnosticError("collection fingerprint mismatch")
    executed = _ordered_ids(value["executed_nodeids"])
    if executed != ([] if label == "collection" else collected):
        raise DiagnosticError("execution identity or order mismatch")
    process = value["process"]
    if not isinstance(process, dict) or set(process) != {"process_id", "temporary_root", "git_fixture_cache_root"}:
        raise DiagnosticError("missing process identity")
    pid = process["process_id"]
    if type(pid) is not int or pid <= 0:
        raise DiagnosticError("invalid process identity")
    temporary = _path_fact(process["temporary_root"], "<pytest_basetemp>")
    if temporary != "<pytest_basetemp>":
        raise DiagnosticError("pytest root mismatch")
    cache = _path_fact(process["git_fixture_cache_root"], f"<diagnostic_temp>/{label}")
    if cache is None:
        raise DiagnosticError("missing process-local Git fixture root")
    if value["over_bound"] is not False or not isinstance(value["failures"], list) or len(value["failures"]) > FAILURE_LIMIT * 3:
        raise DiagnosticError("over-bound or malformed failure observation")
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for fact in value["failures"]:
        if not isinstance(fact, dict) or set(fact) != {"nodeid", "phase", "cause"}:
            raise DiagnosticError("malformed failure fact")
        node = _nodeid(fact["nodeid"])
        phase = fact["phase"]
        if node not in collected or phase not in PHASE_ORDER or (node, phase) in seen:
            raise DiagnosticError("inconsistent failure identity")
        seen.add((node, phase))
        cause = _cause(fact["cause"])
        if cause is None or ("source_locus" in cause and
                             cause["source_locus"]["path"] != "<subject>/" + node.split("::", 1)[0]):
            raise DiagnosticError("unsafe failure cause")
        facts.append({"nodeid": _safe_nodeid(node), "phase": phase, "cause": cause})
    if len({fact["nodeid"] for fact in facts}) > FAILURE_LIMIT:
        raise DiagnosticError("failure identity bound exceeded")
    if (status == 0 and facts) or (status == 1 and not facts) or (label == "collection" and facts):
        raise DiagnosticError("pytest status contradicts failure observation")
    facts.sort(key=lambda fact: (fact["nodeid"], PHASE_ORDER[fact["phase"]]))
    return ({"profile": label, "pytest_exit_status": status,
             "collection": {"rule": "sorted-posix-nodeid-lf-sha256-v1", "count": len(collected),
                            "digest": hashlib.sha256("".join(f"{node}\n" for node in sorted(collected)).encode()).hexdigest()},
             "failures": facts}, collected, (pid, temporary, cache))


Runner = Callable[..., tuple[int, dict[str, Any]]]


def diagnose(repository: Path, *, runner: Runner = run_pytest) -> dict[str, Any]:
    repository = repository.resolve()
    _targets()
    subject = subject_identity(repository)
    processes: list[tuple[int, str, str]] = []
    with tempfile.TemporaryDirectory(prefix="aios-serial-context-") as directory:
        root = Path(directory)

        def observe(label: str, selectors: tuple[str, ...], expected: list[str] | None):
            if subject_identity(repository) != subject:
                raise DiagnosticError("exact subject changed before phase")
            try:
                status, value = runner(repository, root, label=label, selectors=selectors)
            finally:
                if subject_identity(repository) != subject:
                    raise DiagnosticError("exact subject changed during phase")
            profile, collected, process = _phase(label, status, value, expected)
            if any(prior[0] == process[0] for prior in processes):
                raise DiagnosticError("phase process was reused")
            processes.append(process)
            return profile, collected

        collection, canonical = observe("collection", (), None)
        targets_raw = _resolve(canonical)
        phase_a, _ = observe("phase-a", tuple(targets_raw), targets_raw)
        file_context_expected = [node for node in canonical if node.split("::", 1)[0] in FILES]
        phase_b, b_collected = observe("phase-b", FILES, file_context_expected)
        if any(node.split("::", 1)[0] not in FILES for node in b_collected):
            raise DiagnosticError("file-context collection escaped fixed files")
        # File context may collect the same targets in a different order.
        if set(_resolve(b_collected)) != set(targets_raw):
            raise DiagnosticError("file-context target identity mismatch")
        if subject_identity(repository) != subject:
            raise DiagnosticError("exact subject changed after experiment")
    target_set = _targets()
    a_failed = {fact["nodeid"] for fact in phase_a["failures"]}
    b_target = {fact["nodeid"] for fact in phase_b["failures"] if fact["nodeid"] in target_set}
    b_other = {fact["nodeid"] for fact in phase_b["failures"] if fact["nodeid"] not in target_set}
    if not a_failed:
        classification = "COLD_TARGET_PASS"
    elif not b_target:
        classification = "FILE_CONTEXT_RESOLVES"
    elif a_failed == b_target:
        classification = "SERIAL_STABLE_FAILURE"
    else:
        classification = "PARTIAL_FILE_CONTEXT_EFFECT"
    return {
        "format": FORMAT, "version": 1, "subject": subject,
        "collection": collection["collection"],
        "target_nodeids": sorted(target_set), "target_count": 35,
        "phase_a": phase_a, "phase_b": phase_b,
        "phase_a_target_failed_nodeids": sorted(a_failed),
        "phase_b_target_failed_nodeids": sorted(b_target),
        "phase_b_non_target_failed_nodeids": sorted(b_other),
        "phase_a_target_failure_count": len(a_failed),
        "phase_b_target_failure_count": len(b_target),
        "phase_b_non_target_failure_count": len(b_other),
        "classification": classification,
    }


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("serial-context diagnostic accepts no arguments", file=sys.stderr)
        return 2
    try:
        result = diagnose(Path.cwd())
    except (DiagnosticError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        print("serial-context diagnostic integrity failure", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
