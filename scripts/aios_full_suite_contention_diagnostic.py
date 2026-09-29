"""One fixed, observational full-suite n12 contention experiment."""

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

from scripts.bp_v4_parallel_diagnostic import DiagnosticError, _cause, subject_identity, toolchain_identity


FORMAT = "AIOS_FULL_SUITE_CONTENTION_DIAGNOSTIC"
SCHEMA = "AIOS_FULL_SUITE_CONTENTION_PYTEST_OBSERVATION"
PLUGIN = "aios_full_suite_contention_plugin"
OUTPUT_ENV = "AIOS_FULL_SUITE_CONTENTION_OUTPUT"
PREFIX_ENV = "AIOS_BP_V4_DIAGNOSTIC_PREFIXES"
LIMIT = 64
PHASE_ORDER = {"setup": 0, "call": 1, "teardown": 2}
FIELDS = {
    "schema", "version", "exit_status", "controller_collection", "worker_collections",
    "workers", "worker_failure_reports", "worker_execution_reports", "failures",
    "over_bound", "executed_nodeids", "controller_process",
}


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nodeid(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 8192 or "\\" in value:
        raise DiagnosticError("unsafe nodeid")
    path = value.split("::", 1)[0]
    if not re.fullmatch(r"tests/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py", path):
        raise DiagnosticError("unsafe nodeid path")
    detail = value[len(path):]
    if any(ord(char) < 32 or ord(char) > 126 for char in value) or any(
        token in value for token in ("..", ":/", "@", "<", ">", "|", "\"", "'", "`", "$", "%")
    ) or re.search(r"(?:^|[=\[,( ])/(?!/)", detail):
        raise DiagnosticError("unsafe nodeid content")
    if not re.fullmatch(r"[A-Za-z0-9_./:\[\], =+(){}!#&;?-]+", value):
        raise DiagnosticError("unsafe nodeid characters")
    return value


def _identities(value: object, *, expected: set[str] | None = None) -> list[str]:
    if not isinstance(value, list) or len(value) > 10000:
        raise DiagnosticError("malformed collection or execution identities")
    ids = [_nodeid(item) for item in value]
    if len(ids) != len(set(ids)) or (expected is not None and set(ids) != expected):
        raise DiagnosticError("duplicate or inconsistent collection or execution identities")
    return sorted(ids)


def _digest(ids: list[str]) -> dict[str, Any]:
    return {"rule": "sorted-posix-nodeid-lf-sha256-v1", "count": len(ids),
            "digest": hashlib.sha256("".join(f"{node}\n" for node in ids).encode()).hexdigest()}


def _path_fact(value: object, prefix: str, *, optional: bool = False) -> str | None:
    if not isinstance(value, dict) or set(value) != {"path", "length"}:
        raise DiagnosticError("malformed root fact")
    path, length = value["path"], value["length"]
    if optional and path is None and length is None:
        return None
    if not isinstance(path, str) or not path.startswith(prefix) or not _integer(length) or not 0 < length <= 32767:
        raise DiagnosticError("unsafe or nonisolated root")
    suffix = path[len(prefix):]
    if suffix and (not suffix.startswith("/") or not re.fullmatch(r"/[A-Za-z0-9_./-]+", suffix)
                   or any(part in {"", ".", ".."} for part in suffix[1:].split("/"))):
        raise DiagnosticError("unsafe root suffix")
    return path


def _process(value: object, *, label: str, worker: str | None) -> tuple[int, str, str | None]:
    fields = {"process_id", "temporary_root", "git_fixture_cache_root"}
    if worker is not None:
        fields |= {"worker_id", "collection"}
    if not isinstance(value, dict) or set(value) != fields:
        raise DiagnosticError("malformed process fact")
    if worker is not None and value["worker_id"] != worker:
        raise DiagnosticError("worker identity mismatch")
    pid = value["process_id"]
    if not _integer(pid) or pid <= 0:
        raise DiagnosticError("invalid process identity")
    temp_prefix = "<pytest_basetemp>" + (f"/popen-{worker}" if worker is not None else "")
    temp = _path_fact(value["temporary_root"], temp_prefix)
    if worker is not None and temp != temp_prefix:
        raise DiagnosticError("unexpected worker temporary root")
    git_root = _path_fact(value["git_fixture_cache_root"], f"<diagnostic_temp>/{label}",
                          optional=True)
    return pid, temp, git_root


def _failures(value: object, *, collection: set[str], workers: set[str], over_bound: object) -> list[dict[str, Any]]:
    if over_bound is not False or not isinstance(value, list) or len(value) > LIMIT * 3:
        raise DiagnosticError("over-bound or malformed failure facts")
    seen: set[tuple[str, str]] = set()
    result = []
    for fact in value:
        if not isinstance(fact, dict) or set(fact) != {"nodeid", "phase", "worker_id", "cause"}:
            raise DiagnosticError("malformed failure fact")
        node = _nodeid(fact["nodeid"])
        phase = fact["phase"]
        if node not in collection or phase not in PHASE_ORDER or fact["worker_id"] not in workers:
            raise DiagnosticError("failure outside execution identity")
        key = (node, phase)
        if key in seen:
            raise DiagnosticError("duplicate failure identity")
        seen.add(key)
        cause = _cause(fact["cause"])
        if cause is None:
            raise DiagnosticError("missing typed failure cause")
        if "source_locus" in cause and cause["source_locus"]["path"] != "<subject>/" + node.split("::", 1)[0]:
            raise DiagnosticError("failure source locus mismatch")
        result.append({"nodeid": node, "phase": phase, "worker_id": fact["worker_id"], "cause": cause})
    if len({fact["nodeid"] for fact in result}) > LIMIT:
        raise DiagnosticError("failure identity bound exceeded")
    return sorted(result, key=lambda fact: (fact["nodeid"], PHASE_ORDER[fact["phase"]]))


def _read(path: Path) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise DiagnosticError("duplicate structured observation key")
            result[key] = item
        return result

    try:
        if path.stat().st_size > 32_000_000:
            raise DiagnosticError("structured observation exceeds size bound")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError("missing or malformed structured observation") from exc
    if not isinstance(value, dict) or set(value) != FIELDS or value["schema"] != SCHEMA or value["version"] != 1:
        raise DiagnosticError("incompatible structured observation")
    return value


def run_pytest(repository: Path, root: Path, *, label: str, workers: int,
               collect_only: bool, nodeids: tuple[str, ...]) -> tuple[int, dict[str, Any]]:
    if (label, workers, collect_only) not in {
        ("collection", 1, True), ("full-n12", 12, False),
        ("serial", 1, False), ("n4", 4, False),
    } or (label in {"collection", "full-n12"} and nodeids) or (
        label in {"serial", "n4"} and not 0 < len(nodeids) <= LIMIT
    ):
        raise DiagnosticError("unsupported diagnostic invocation")
    profile = root / label
    profile.mkdir()
    basetemp = profile / "pytest"
    output = profile / "observation.json"
    command = [sys.executable, "-m", "pytest", "-p", "xdist.plugin", "-p", PLUGIN,
               "-p", "no:cacheprovider", "--basetemp", str(basetemp), "-q"]
    if collect_only:
        command.append("--collect-only")
    elif workers > 1:
        command.extend(["-n", str(workers), "--dist", "load", "--max-worker-restart", "0"])
    command.extend(nodeids)
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


def _phase(label: str, workers: int, status: int, value: dict[str, Any],
           expected: set[str], *, collection_only: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FIELDS or value["schema"] != SCHEMA or value["version"] != 1:
        raise DiagnosticError("malformed phase observation")
    if not _integer(status) or not _integer(value["exit_status"]) or status != value["exit_status"] or status not in ({0} if collection_only else {0, 1}):
        raise DiagnosticError("pytest phase exit inconsistency")
    controller = _process(value["controller_process"], label=label, worker=None)
    if controller[1] != "<pytest_basetemp>":
        raise DiagnosticError("controller temporary root mismatch")
    collections, reported = value["worker_collections"], value["workers"]
    if not isinstance(collections, dict) or not isinstance(reported, dict):
        raise DiagnosticError("malformed worker observations")
    worker_ids = {f"gw{i}" for i in range(workers)} if workers > 1 else set()
    if (set(collections) != worker_ids or set(reported) != worker_ids
            or value["worker_failure_reports"] != sorted(worker_ids)
            or value["worker_execution_reports"] != sorted(worker_ids)):
        raise DiagnosticError("missing or unexpected worker report")
    if workers == 1:
        collected = _identities(value["controller_collection"], expected=expected if expected else None)
        if not collected:
            raise DiagnosticError("empty canonical collection")
    else:
        if value["controller_collection"] is not None:
            raise DiagnosticError("unexpected controller collection")
        collected = sorted(expected)
    processes = [controller]
    for worker in sorted(worker_ids):
        item = reported[worker]
        process = _process(item, label=label, worker=worker)
        if item["collection"] != collections[worker] or _identities(collections[worker], expected=expected) != collected:
            raise DiagnosticError("worker collection mismatch")
        processes.append(process)
    if len({p[0] for p in processes}) != len(processes) or len({p[1] for p in processes}) != len(processes):
        raise DiagnosticError("shared process or pytest root")
    roots = [p[2] for p in processes if p[2] is not None]
    if len(roots) != len(set(roots)):
        raise DiagnosticError("shared Git fixture cache root")
    if len(roots) != len(processes):
        raise DiagnosticError("missing process-local Git fixture cache root")
    failures = _failures(value["failures"], collection=set(collected),
                         workers=worker_ids or {"controller"}, over_bound=value["over_bound"])
    executions = _identities(value["executed_nodeids"], expected=set() if collection_only else expected)
    if collection_only and (failures or executions):
        raise DiagnosticError("collection executed tests")
    if (status == 0 and failures) or (status == 1 and not failures):
        raise DiagnosticError("failure facts contradict pytest exit")
    return {"profile": label, "workers": workers, "pytest_exit_status": status,
            "collection": _digest(collected), "collected_nodeids": collected if collection_only else None,
            "failures": failures}


Runner = Callable[..., tuple[int, dict[str, Any]]]


def diagnose(repository: Path, *, runner: Runner = run_pytest,
             loader: Callable[[], dict[str, str]] = toolchain_identity) -> dict[str, Any]:
    repository = repository.resolve()
    toolchain = loader()
    subject = subject_identity(repository)
    with tempfile.TemporaryDirectory(prefix="aios-full-suite-contention-") as directory:
        root = Path(directory)

        def observe(label: str, workers: int, collect_only: bool, ids: tuple[str, ...],
                    expected: set[str]) -> dict[str, Any]:
            if subject_identity(repository) != subject:
                raise DiagnosticError("exact subject changed before phase")
            try:
                status, value = runner(repository, root, label=label, workers=workers,
                                       collect_only=collect_only, nodeids=ids)
            finally:
                if subject_identity(repository) != subject:
                    raise DiagnosticError("exact subject changed during phase")
            return _phase(label, workers, status, value, expected, collection_only=collect_only)

        collection = observe("collection", 1, True, (), set())
        canonical_ids = collection.pop("collected_nodeids")
        canonical = set(canonical_ids)
        full = observe("full-n12", 12, False, (), canonical)
        full.pop("collected_nodeids")
        failed = sorted({fact["nodeid"] for fact in full["failures"]})
        profiles = [full]
        serial_failed: list[str] = []
        n4_failed: list[str] = []
        if failed:
            serial = observe("serial", 1, False, tuple(failed), set(failed))
            n4 = observe("n4", 4, False, tuple(failed), set(failed))
            serial.pop("collected_nodeids")
            n4.pop("collected_nodeids")
            profiles.extend((serial, n4))
            serial_failed = sorted({fact["nodeid"] for fact in serial["failures"]})
            n4_failed = sorted({fact["nodeid"] for fact in n4["failures"]})
    if not failed:
        classification = "FULL_N12_PASS"
    elif set(serial_failed) == set(failed):
        classification = "SERIAL_REPRODUCIBLE"
    elif not serial_failed and n4_failed:
        classification = "N4_PARALLEL_REPRODUCIBLE"
    elif not serial_failed and not n4_failed:
        classification = "FULL_SUITE_CONTEXT_DEPENDENT"
    else:
        classification = "MIXED"
    return {"format": FORMAT, "version": 1, "subject": subject, "toolchain": toolchain,
            "collection": collection["collection"], "profiles": profiles,
            "full_n12_failed_nodeids": failed, "serial_reproduced_nodeids": serial_failed,
            "n4_reproduced_nodeids": n4_failed, "classification": classification}


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("full-suite contention diagnostic accepts no arguments", file=sys.stderr)
        return 2
    try:
        result = diagnose(Path.cwd())
    except (DiagnosticError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        print("full-suite contention diagnostic integrity failure", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
