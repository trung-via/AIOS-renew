"""One fixed cold serial, privacy-bounded detail observation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bp_v4_parallel_diagnostic import DiagnosticError, subject_identity
from tests.aios_stable_failure_detail_probe_plugin import (
    FAMILIES, INGRESS, OUTPUT_ENV, PROFILE_ENV, SCHEMA, STDERR_RULES,
)

TARGETS = (
    "tests/test_hot_swap_conformance.py::test_scenario_2_fresh_correction_accepts_only_current_finding",
    "tests/test_operator.py::test_source_repair_bootstrap_closed_and_exact_replay",
    "tests/test_operator.py::test_source_repair_bound_target_consumes_without_matching_activation",
)
FIELDS = {"schema", "version", "exit_status", "collection", "executed", "failures", "process"}
HEX = re.compile(r"sha256:[0-9a-f]{64}\Z")
KIND = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")
FAMILY = set(FAMILIES.values()) | {"REMOTE_GET_URL", "OTHER"}
STDERR_CATEGORY = {item[0] for item in STDERR_RULES} | {"OTHER"}


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticError("duplicate observation key")
        result[key] = value
    return result


def _read(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 16_384:
            raise DiagnosticError("observation exceeds bound")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError("missing or malformed observation") from exc
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise DiagnosticError("incompatible observation")
    return value


def run_pytest(repository: Path, root: Path, *, label: str,
               selectors: tuple[str, ...]) -> tuple[int, dict[str, Any]]:
    if label not in {"collection", "target-0", "target-1", "target-2"} or (
        selectors != (TARGETS if label == "collection" else (TARGETS[int(label[-1])],))
    ):
        raise DiagnosticError("unsupported probe invocation")
    profile = root / label
    profile.mkdir()
    output = profile / "observation.json"
    command = [sys.executable, "-m", "pytest", "-p", "aios_stable_failure_detail_probe_plugin",
               "-p", "no:cacheprovider", "--basetemp", str(profile / "pytest"), "-q"]
    if label == "collection":
        command.append("--collect-only")
    command.extend(selectors)
    env = os.environ.copy()
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_XDIST_AUTO_NUM_WORKERS"):
        env.pop(key, None)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONPATH": str(repository / "tests") + os.pathsep + str(repository),
                "TMP": str(profile), "TEMP": str(profile), "TMPDIR": str(profile),
                OUTPUT_ENV: str(output), PROFILE_ENV: str(profile)})
    completed = subprocess.run(command, cwd=repository, env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False)
    return completed.returncode, _read(output)


def _detail(value: object, target_index: int) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("exception_type"), str) or not KIND.fullmatch(value["exception_type"]):
        raise DiagnosticError("unsafe exception type")
    status = value.get("detail_status")
    if target_index == 0 and value["exception_type"] == "AuthoringIngressError":
        if set(value) != {"exception_type", "message_fingerprint", "cause_chain", "source_locus", "detail_status"}:
            raise DiagnosticError("malformed ingress detail")
        if not isinstance(value["message_fingerprint"], str) or not HEX.fullmatch(value["message_fingerprint"]):
            raise DiagnosticError("unsafe fingerprint")
        chain = value["cause_chain"]
        if not isinstance(chain, list) or len(chain) > 8 or any(not isinstance(x, str) or not KIND.fullmatch(x) for x in chain):
            raise DiagnosticError("unsafe cause chain")
        locus = value["source_locus"]
        if locus == "UNKNOWN_LOCUS":
            if status != "UNKNOWN_LOCUS":
                raise DiagnosticError("locus status mismatch")
        elif (not isinstance(locus, dict) or set(locus) != {"path", "function", "line"}
              or locus["path"] != INGRESS or not isinstance(locus["function"], str)
              or not KIND.fullmatch(locus["function"]) or type(locus["line"]) is not int
              or not 0 < locus["line"] <= 1_000_000 or status != "RESOLVED"):
            raise DiagnosticError("unsafe ingress locus")
    elif target_index in (1, 2) and value["exception_type"] == "OperatorError":
        if set(value) == {"exception_type", "guard_category", "detail_status", "git"}:
            if value["guard_category"] != "GIT_COMMAND_FAILED" or status not in {"RESOLVED", "AMBIGUOUS"}:
                raise DiagnosticError("invalid Git status")
            git = value["git"]
            if status == "AMBIGUOUS":
                if git is not None:
                    raise DiagnosticError("ambiguous Git detail leaked")
            elif (not isinstance(git, dict) or set(git) != {"command_family", "return_code", "stderr_category"}
                  or git["command_family"] not in FAMILY or type(git["return_code"]) is not int
                  or not -(2**31) <= git["return_code"] < 2**31 or git["return_code"] == 0
                  or git["stderr_category"] not in STDERR_CATEGORY):
                raise DiagnosticError("unsafe Git detail")
        elif set(value) != {"exception_type", "detail_status"} or status != "UNEXPECTED_FAILURE":
            raise DiagnosticError("malformed operator detail")
    elif set(value) != {"exception_type", "detail_status"} or status not in {"UNEXPECTED_FAILURE", "AMBIGUOUS"}:
        raise DiagnosticError("malformed unexpected detail")
    return value


def _validate(label: str, status: int, value: dict[str, Any], root: Path,
              seen: set[str]) -> dict[str, Any] | None:
    if (set(value) != FIELDS or value["schema"] != SCHEMA or type(value["version"]) is not int
            or value["version"] != 1 or type(status) is not int or type(value["exit_status"]) is not int
            or status != value["exit_status"] or status not in ({0} if label == "collection" else {0, 1})):
        raise DiagnosticError("probe status or schema mismatch")
    expected = list(TARGETS if label == "collection" else (TARGETS[int(label[-1])],))
    if value["collection"] != expected or value["executed"] != ([] if label == "collection" else expected):
        raise DiagnosticError("exact collection or execution mismatch")
    process = value["process"]
    expected_temp = "sha256:" + hashlib.sha256(os.path.normcase(str((root / label / "pytest").resolve())).encode()).hexdigest()
    if (not isinstance(process, dict) or set(process) != {"pid", "basetemp", "cache", "cache_in_profile"}
            or type(process["pid"]) is not int or process["pid"] <= 0
            or process["basetemp"] != expected_temp or process["cache_in_profile"] is not True
            or not isinstance(process["cache"], str) or not HEX.fullmatch(process["cache"])):
        raise DiagnosticError("nonisolated process roots")
    identity = {str(process["pid"]), process["basetemp"], process["cache"]}
    if seen & identity:
        raise DiagnosticError("reused process or root")
    seen.update(identity)
    failures = value["failures"]
    if not isinstance(failures, list) or len(failures) != (0 if status == 0 else 1):
        raise DiagnosticError("missing or duplicate failure")
    if label == "collection":
        return None
    if status == 0:
        return {"target": expected[0], "outcome": "PASS", "detail": None}
    failure = failures[0]
    if (not isinstance(failure, dict) or set(failure) != {"nodeid", "phase", "detail"}
            or failure["nodeid"] != expected[0] or failure["phase"] != "call"):
        raise DiagnosticError("wrong-target or non-call failure")
    return {"target": expected[0], "outcome": "FAIL", "detail": _detail(failure["detail"], int(label[-1]))}


Runner = Callable[..., tuple[int, dict[str, Any]]]


def diagnose(repository: Path, *, runner: Runner = run_pytest,
             identity: Callable[[Path], dict[str, Any]] = subject_identity) -> dict[str, Any]:
    repository = repository.resolve()
    subject = identity(repository)
    if (not isinstance(subject, dict) or set(subject) != {"kind", "head_sha", "worktree_clean"}
            or subject["kind"] != "git-commit" or not isinstance(subject["head_sha"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", subject["head_sha"])
            or subject["worktree_clean"] is not True):
        raise DiagnosticError("invalid initial subject")
    seen: set[str] = set()
    records = []
    with tempfile.TemporaryDirectory(prefix="aios-stable-detail-") as directory:
        root = Path(directory)
        for label, selectors in [("collection", TARGETS), *[(f"target-{i}", (node,)) for i, node in enumerate(TARGETS)]]:
            if identity(repository) != subject:
                raise DiagnosticError("subject changed before subprocess")
            try:
                status, value = runner(repository, root, label=label, selectors=selectors)
            finally:
                if identity(repository) != subject:
                    raise DiagnosticError("subject changed during subprocess")
            record = _validate(label, status, value, root, seen)
            if record is not None:
                records.append(record)
        if identity(repository) != subject:
            raise DiagnosticError("subject changed after experiment")
    passed = sum(item["outcome"] == "PASS" for item in records)
    if passed == 3:
        classification = "PASS_DRIFT"
    elif passed:
        classification = "MIXED_PASS_FAIL"
    elif (records[0]["detail"].get("detail_status") == "RESOLVED"
          and all(item["detail"].get("detail_status") == "RESOLVED" for item in records[1:])):
        classification = "DETAILS_RESOLVED"
    else:
        classification = "PARTIAL_DETAILS"
    return {"subject": subject, "targets": records, "classification": classification}


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("stable-failure detail diagnostic accepts no arguments", file=sys.stderr)
        return 2
    try:
        result = diagnose(Path.cwd())
    except (DiagnosticError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        print("stable-failure detail diagnostic integrity failure", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
