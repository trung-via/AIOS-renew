"""Fixed serial, privacy-bounded AUTHOR_REMEDIATION byte identity diagnostic."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.aios_ingress_metadata_identity_probe_plugin import OUTPUT_ENV, PROFILE_ENV, SCHEMA, TARGETS


class DiagnosticError(Exception):
    pass


HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
RELATIONS = {"EXACT", "LINE_ENDING_OR_BOM", "OTHER"}
CLASSIFICATIONS = {"PASS_EXACT", "WRITE_OR_TREE_BINDING_MISMATCH", "NATIVE_BLOB_MISMATCH",
                   "PRODUCTION_READBACK_MISMATCH", "LINE_ENDING_OR_BOM_TRANSFORM",
                   "OTHER_MISMATCH", "OBSERVATION_INCOMPLETE"}


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(("git", "-C", str(repo), *args), capture_output=True, check=False)
    if proc.returncode:
        raise DiagnosticError("subject identity unavailable")
    return proc.stdout


def subject_identity(repo: Path) -> dict[str, Any]:
    head = _git(repo, "rev-parse", "--verify", "HEAD").strip().decode("ascii", errors="ignore")
    if not HEX40.fullmatch(head) or _git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise DiagnosticError("subject identity or cleanliness failure")
    return {"kind": "git-commit", "head_sha": head, "worktree_clean": True}


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticError("duplicate observation field")
        result[key] = value
    return result


def _read(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 16_384:
            raise DiagnosticError("observation exceeds bound")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError("observation unavailable") from exc
    if not isinstance(value, dict):
        raise DiagnosticError("malformed observation")
    return value


def _signature(value: Any) -> None:
    if (not isinstance(value, dict) or set(value) != {"bom", "crlf", "lf", "cr", "trailing"}
            or type(value["bom"]) is not bool or value["trailing"] not in {"NONE", "LF", "CRLF", "CR"}
            or any(type(value[k]) is not int or not 0 <= value[k] <= 10_000_000 for k in ("crlf", "lf", "cr"))):
        raise DiagnosticError("unsafe byte signature")


def _byte_fact(value: Any) -> None:
    if (not isinstance(value, dict) or set(value) != {"length", "sha256", "signature"}
            or type(value["length"]) is not int or not 0 <= value["length"] <= 100_000_000
            or not isinstance(value["sha256"], str) or not HEX64.fullmatch(value["sha256"])):
        raise DiagnosticError("unsafe byte fact")
    _signature(value["signature"])


def _facts(value: Any) -> None:
    fields = {"expected", "expected_object", "tree_object", "native", "native_object_valid",
              "readback", "read_count", "native_relation", "readback_relation", "native_readback_equal"}
    if not isinstance(value, dict) or set(value) != fields:
        raise DiagnosticError("malformed boundary facts")
    _byte_fact(value["expected"])
    for key in ("native", "readback"):
        if value[key] is not None:
            _byte_fact(value[key])
    if not isinstance(value["expected_object"], str) or not HEX40.fullmatch(value["expected_object"]):
        raise DiagnosticError("unsafe expected object")
    if value["tree_object"] is not None and (not isinstance(value["tree_object"], str)
                                               or not HEX40.fullmatch(value["tree_object"])):
        raise DiagnosticError("unsafe tree object")
    if type(value["read_count"]) is not int or not 0 <= value["read_count"] <= 2:
        raise DiagnosticError("unsafe read count")
    for key in ("native_object_valid", "native_readback_equal"):
        if value[key] is not None and type(value[key]) is not bool:
            raise DiagnosticError("unsafe equality fact")
    for key in ("native_relation", "readback_relation"):
        if value[key] is not None and value[key] not in RELATIONS:
            raise DiagnosticError("unsafe relation")
    if (value["native"] is None) != (value["native_relation"] is None) or (value["readback"] is None) != (value["readback_relation"] is None):
        raise DiagnosticError("inconsistent relation")
    if value["native"] is None and value["native_object_valid"] is not None:
        raise DiagnosticError("inconsistent native object")
    if value["native"] is None and value["native_readback_equal"] is not None:
        raise DiagnosticError("inconsistent native/readback equality")
    for key, relation in (("native", "native_relation"), ("readback", "readback_relation")):
        if value[relation] == "EXACT" and value[key] != value["expected"]:
            raise DiagnosticError("inconsistent exact byte relation")
    if value["native_readback_equal"] is True and value["native"] != value["readback"]:
        raise DiagnosticError("inconsistent native/readback identity")
    if value["native_readback_equal"] is False and value["native"] == value["readback"]:
        raise DiagnosticError("inconsistent native/readback difference")


def classify(facts: dict[str, Any] | None, *, status: int) -> str:
    if facts is None or facts["tree_object"] is None or facts["native"] is None or facts["read_count"] != 1:
        return "OBSERVATION_INCOMPLETE"
    if facts["readback"] is None:
        if (status == 1 and facts["expected_object"] == facts["tree_object"]
                and facts["native_object_valid"] is True and facts["native_relation"] == "EXACT"):
            return "PRODUCTION_READBACK_MISMATCH"
        return "OBSERVATION_INCOMPLETE"
    if (facts["expected_object"] == facts["tree_object"] and facts["native_object_valid"] is True
            and facts["native_relation"] == "EXACT" and facts["readback_relation"] == "EXACT"
            and facts["native_readback_equal"] is True):
        return "PASS_EXACT" if status == 0 else "OTHER_MISMATCH"
    if facts["tree_object"] != facts["expected_object"]:
        if facts["native_relation"] == "LINE_ENDING_OR_BOM":
            return "LINE_ENDING_OR_BOM_TRANSFORM"
        return "WRITE_OR_TREE_BINDING_MISMATCH"
    if facts["native_object_valid"] is not True or facts["native_relation"] != "EXACT":
        if facts["native_relation"] == "LINE_ENDING_OR_BOM":
            return "LINE_ENDING_OR_BOM_TRANSFORM"
        return "NATIVE_BLOB_MISMATCH"
    if facts["native_readback_equal"] is False or facts["readback_relation"] != "EXACT":
        if facts["readback_relation"] == "LINE_ENDING_OR_BOM":
            return "LINE_ENDING_OR_BOM_TRANSFORM"
        return "PRODUCTION_READBACK_MISMATCH"
    return "OTHER_MISMATCH"


def _validate(label: str, status: int, value: dict[str, Any], profile: Path, seen: set[tuple[Any, ...]]) -> dict[str, Any]:
    fields = {"schema", "version", "status", "collection", "executed", "failures", "attempts", "pid",
              "basetemp_sha256", "cache_sha256", "cache_in_profile"}
    target = TARGETS[label]
    if (set(value) != fields or value["schema"] != SCHEMA or type(value["version"]) is not int
            or value["version"] != 1 or type(value["status"]) is not int or status != value["status"]
            or status not in (0, 1) or value["collection"] != [target] or value["executed"] != [target]
            or type(value["pid"]) is not int or value["pid"] <= 0
            or value["cache_in_profile"] is not True):
        raise DiagnosticError("identity, execution, or schema failure")
    expected_temp = hashlib.sha256(os.path.normcase(str((profile / "pytest").resolve())).encode()).hexdigest()
    if value["basetemp_sha256"] != expected_temp or not isinstance(value["cache_sha256"], str) or not HEX64.fullmatch(value["cache_sha256"]):
        raise DiagnosticError("observation root mismatch")
    identity = (value["pid"], value["basetemp_sha256"], value["cache_sha256"])
    if any(set(identity) & set(previous) for previous in seen):
        raise DiagnosticError("reused observation context")
    seen.add(identity)
    attempts = value["attempts"]
    if not isinstance(attempts, list) or len(attempts) != 1:
        raise DiagnosticError("missing or duplicate metadata boundary")
    attempt = attempts[0]
    if not isinstance(attempt, dict) or set(attempt) != {"delegations", "operation", "facts"} or attempt["delegations"] != 1 or attempt["operation"] != "AUTHOR_REMEDIATION":
        raise DiagnosticError("delegation failure")
    facts = attempt["facts"]
    if facts is not None:
        _facts(facts)
    failures = value["failures"]
    if not isinstance(failures, list) or len(failures) != status:
        raise DiagnosticError("failure record mismatch")
    for failure in failures:
        if (not isinstance(failure, dict) or set(failure) != {"nodeid", "phase", "type", "message_sha256"}
                or failure["nodeid"] != target or failure["phase"] != "call"
                or failure["type"] not in {"AuthoringIngressError", "OtherException"}
                or (failure["type"] == "AuthoringIngressError" and
                    (not isinstance(failure["message_sha256"], str) or not HEX64.fullmatch(failure["message_sha256"])))
                or (failure["type"] == "OtherException" and failure["message_sha256"] is not None)):
            raise DiagnosticError("unsafe failure record")
    return {"context": label, "target": target, "outcome": "PASS" if status == 0 else "FAIL",
            "failure": failures[0] if failures else None, "facts": facts,
            "classification": classify(facts, status=status)}


def _run(repo: Path, root: Path, label: str) -> tuple[int, dict[str, Any]]:
    profile = root / label
    profile.mkdir()
    output = profile / "observation.json"
    env = os.environ.copy()
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_XDIST_AUTO_NUM_WORKERS"):
        env.pop(key, None)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONPATH": str(repo / "tests") + os.pathsep + str(repo),
                "TMP": str(profile), "TEMP": str(profile), "TMPDIR": str(profile),
                OUTPUT_ENV: str(output), PROFILE_ENV: str(profile)})
    cmd = (sys.executable, "-m", "pytest", "-p", "aios_ingress_metadata_identity_probe_plugin",
           "-p", "no:cacheprovider", "--basetemp", str(profile / "pytest"), "-q", TARGETS[label])
    proc = subprocess.run(cmd, cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return proc.returncode, _read(output)


def diagnose(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    subject = subject_identity(repo)
    seen: set[tuple[Any, ...]] = set()
    records = []
    with tempfile.TemporaryDirectory(prefix="aios-metadata-identity-") as directory:
        root = Path(directory)
        for label in ("control", "fresh"):
            if subject_identity(repo) != subject:
                raise DiagnosticError("subject changed before context")
            try:
                status, observation = _run(repo, root, label)
            finally:
                if subject_identity(repo) != subject:
                    raise DiagnosticError("subject changed during context")
            records.append(_validate(label, status, observation, root / label, seen))
        if subject_identity(repo) != subject:
            raise DiagnosticError("subject changed after contexts")
    control, fresh = records
    categories = (control["classification"], fresh["classification"])
    if "OBSERVATION_INCOMPLETE" in categories:
        aggregate = "INCOMPLETE_OBSERVATION"
    elif categories == ("PASS_EXACT", "PASS_EXACT"):
        aggregate = "BOTH_EXACT_PASS"
    elif categories[0] == "PASS_EXACT" and categories[1] != "PASS_EXACT" and fresh["outcome"] == "FAIL":
        aggregate = "FRESH_CLONE_ONLY_MISMATCH_RESOLVED_BOUNDARY"
    elif categories[0] != "PASS_EXACT" and categories[1] != "PASS_EXACT":
        aggregate = "MISMATCH_IN_BOTH_CONTEXTS"
    else:
        aggregate = "PASS_DRIFT"
    return {"format": "AIOS_INGRESS_METADATA_IDENTITY_DIAGNOSTIC", "version": 1,
            "subject": subject, "contexts": records, "classification": aggregate}


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("metadata identity diagnostic accepts no arguments", file=sys.stderr)
        return 2
    try:
        result = diagnose(Path.cwd())
    except (DiagnosticError, OSError, ValueError, TypeError, subprocess.SubprocessError):
        print("metadata identity diagnostic integrity failure", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
