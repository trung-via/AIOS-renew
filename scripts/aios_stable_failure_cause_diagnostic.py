"""One fixed, cold serial cause probe for the three RUN-234 stable failures."""

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
from tests.aios_stable_failure_probe_plugin import GUARDS, SCHEMA, OUTPUT_ENV, PROFILE_ENV, SAFE_FILES


TARGETS = (
    "tests/test_hot_swap_conformance.py::test_scenario_2_fresh_correction_accepts_only_current_finding",
    "tests/test_operator.py::test_source_repair_bootstrap_closed_and_exact_replay",
    "tests/test_operator.py::test_source_repair_bound_target_consumes_without_matching_activation",
)
FIELDS = {"schema", "version", "exit_status", "collection", "executed", "failures", "process"}
HEX = re.compile(r"sha256:[0-9a-f]{64}\Z")
KIND = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}\Z")
CATEGORIES = {item[2] for item in GUARDS} | {"UNKNOWN"}


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticError("duplicate observation key")
        result[key] = value
    return result


def _read(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 32_768:
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
    basetemp = profile / "pytest"
    command = [sys.executable, "-m", "pytest", "-p", "aios_stable_failure_probe_plugin",
               "-p", "no:cacheprovider", "--basetemp", str(basetemp), "-q"]
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


def _integer_facts(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or not set(value) <= {"errno", "winerror", "returncode"}:
        raise DiagnosticError("malformed integer facts")
    if any(type(item) is not int or not -(2**31) <= item < 2**31 for item in value.values()):
        raise DiagnosticError("unsafe integer fact")
    return value


def _cause(value: object, target: str) -> dict[str, Any]:
    fields = {"exception_type", "message_fingerprint", "source_locus", "cause_chain",
              "integer_facts", "guard_category"}
    if not isinstance(value, dict) or set(value) != fields:
        raise DiagnosticError("malformed cause")
    if not isinstance(value["exception_type"], str) or not KIND.fullmatch(value["exception_type"]):
        raise DiagnosticError("unsafe exception type")
    if not isinstance(value["message_fingerprint"], str) or not HEX.fullmatch(value["message_fingerprint"]):
        raise DiagnosticError("unsafe fingerprint")
    if value["guard_category"] not in CATEGORIES:
        raise DiagnosticError("unsafe guard category")
    if value["guard_category"] != "UNKNOWN" and value["exception_type"] not in {"AuthoringIngressError", "OperatorError"}:
        raise DiagnosticError("guard type mismatch")
    locus = value["source_locus"]
    if locus is not None and (not isinstance(locus, dict) or set(locus) != {"path", "line"}
                              or locus["path"] not in SAFE_FILES or type(locus["line"]) is not int
                              or not 0 < locus["line"] <= 1_000_000):
        raise DiagnosticError("unsafe source locus")
    if locus is not None and locus["path"].startswith("tests/") and locus["path"] != target.split("::", 1)[0]:
        raise DiagnosticError("wrong test locus")
    _integer_facts(value["integer_facts"])
    chain = value["cause_chain"]
    if not isinstance(chain, list) or len(chain) > 8:
        raise DiagnosticError("over-bound cause chain")
    for item in chain:
        if not isinstance(item, dict) or set(item) != {"exception_type", "integer_facts"} or not isinstance(item["exception_type"], str) or not KIND.fullmatch(item["exception_type"]):
            raise DiagnosticError("unsafe cause chain")
        _integer_facts(item["integer_facts"])
    return value


def _validate(label: str, status: int, value: dict[str, Any], root: Path,
              seen: set[tuple[Any, ...]]) -> dict[str, Any] | None:
    if (set(value) != FIELDS or value["schema"] != SCHEMA or type(value["version"]) is not int
            or value["version"] != 1 or type(status) is not int or type(value["exit_status"]) is not int
            or status != value["exit_status"] or status not in ({0} if label == "collection" else {0, 1})):
        raise DiagnosticError("probe status or schema mismatch")
    expected = list(TARGETS if label == "collection" else (TARGETS[int(label[-1])],))
    if value["collection"] != expected or value["executed"] != ([] if label == "collection" else expected):
        raise DiagnosticError("exact collection or execution mismatch")
    process = value["process"]
    if not isinstance(process, dict) or set(process) != {"pid", "basetemp", "cache", "cache_in_profile"}:
        raise DiagnosticError("malformed process identity")
    expected_temp = "sha256:" + hashlib.sha256(os.path.normcase(str((root / label / "pytest").resolve())).encode()).hexdigest()
    if (type(process["pid"]) is not int or process["pid"] <= 0
            or process["basetemp"] != expected_temp or process["cache_in_profile"] is not True
            or not isinstance(process["cache"], str) or not HEX.fullmatch(process["cache"])):
        raise DiagnosticError("nonisolated process roots")
    identity = (process["pid"], process["basetemp"], process["cache"])
    if any(set(identity) & set(previous) for previous in seen):
        raise DiagnosticError("reused process or root")
    seen.add(identity)
    failures = value["failures"]
    if not isinstance(failures, list) or len(failures) != (0 if status == 0 else 1):
        raise DiagnosticError("missing or duplicate failure")
    if label == "collection":
        return None
    if status == 0:
        return {"target": expected[0], "outcome": "PASS", "cause": None}
    failure = failures[0]
    if not isinstance(failure, dict) or set(failure) != {"nodeid", "phase", "cause"} or failure["nodeid"] != expected[0] or failure["phase"] != "call":
        raise DiagnosticError("non-call or wrong-target failure")
    return {"target": expected[0], "outcome": "FAIL", "cause": _cause(failure["cause"], expected[0])}


Runner = Callable[..., tuple[int, dict[str, Any]]]


def diagnose(repository: Path, *, runner: Runner = run_pytest,
             identity: Callable[[Path], dict[str, Any]] = subject_identity) -> dict[str, Any]:
    repository = repository.resolve()
    subject = identity(repository)
    seen: set[tuple[Any, ...]] = set()
    records = []
    with tempfile.TemporaryDirectory(prefix="aios-stable-failure-") as directory:
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
    categories = [record["cause"]["guard_category"] for record in records if record["cause"]]
    classification = ("PASS" if not categories else "ALL_UNKNOWN" if all(item == "UNKNOWN" for item in categories)
                      else "MIXED_KNOWN_UNKNOWN" if "UNKNOWN" in categories else "KNOWN_GUARDS")
    return {"format": "AIOS_STABLE_FAILURE_CAUSE_DIAGNOSTIC", "version": 1,
            "subject": subject, "targets": records, "classification": classification}


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("stable-failure cause diagnostic accepts no arguments", file=sys.stderr)
        return 2
    try:
        result = diagnose(Path.cwd())
    except (DiagnosticError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        print("stable-failure cause diagnostic integrity failure", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
