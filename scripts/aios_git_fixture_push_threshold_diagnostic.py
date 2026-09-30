"""Six fixed real-Git observations; invocation accepts no arguments.

This primitive supplies facts only. Executing it on a reviewed subject requires
a separate authorized TASK; regression tests do not run the live experiment.
Dynamic Git operands and the subject identity remain in memory only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAMILIES = ("integration", "admission-failure")
LENGTHS = (326, 327, 328)
CASES = tuple((family, length) for family in FAMILIES for length in LENGTHS)
FORMAT = "AIOS_GIT_FIXTURE_PUSH_THRESHOLD_DIAGNOSTIC"
MIN_RETURN_CODE = -(2**31)
MAX_RETURN_CODE = 2**32 - 1
CATEGORIES = frozenset({
    "LOCK_OR_REF_UPDATE", "PATH_OR_FILENAME", "ACCESS_OR_PERMISSION",
    "REPOSITORY_STATE", "TRANSPORT_OR_REMOTE", "UNKNOWN_REVISION_OR_OBJECT", "OTHER",
})
SIGNATURES = frozenset({
    "REMOTE_UNPACK_FAILED", "CANNOT_LOCK_REF", "UNABLE_TO_CREATE",
    "FILENAME_TOO_LONG", "FAILED_TO_PUSH_REFS", "ACCESS_DENIED", "OTHER",
})


class DiagnosticError(ValueError):
    """Diagnostic integrity failed; exception details must never be published."""


def git_environment() -> dict[str, str]:
    # Prevent inherited Git operands/config overrides from redirecting a case.
    # These are process-local settings, never writes to host Git configuration.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0")
    return env


def git(repository: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repository), *args], capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=True,
        env=git_environment(),
    )


def subject_identity(repository: Path) -> str:
    try:
        top = git(repository, "rev-parse", "--show-toplevel").stdout.strip()
        head = git(repository, "rev-parse", "--verify", "HEAD").stdout.strip()
        dirty = git(repository, "status", "--porcelain=v1", "--untracked-files=all",
                    "--ignore-submodules=none").stdout
    except (OSError, subprocess.SubprocessError):
        raise DiagnosticError("subject inspection failed") from None
    if Path(top).resolve() != repository:
        raise DiagnosticError("subject root mismatch")
    if (not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head)
            or dirty):
        raise DiagnosticError("subject is not an exact clean commit")
    return head


def validate_geometry(family: object, authored: object, observed: object) -> None:
    if (type(family) is not str or family not in FAMILIES
            or type(authored) is not int or authored not in LENGTHS
            or type(observed) is not int or observed != authored):
        raise DiagnosticError("exact geometry mismatch")


def validate_case(value: object, expected: tuple[str, int]) -> dict:
    fields = {"family", "authored_lock_path_length", "observed_lock_path_length",
              "outcome", "return_code"}
    if type(value) is not dict or type(value.get("outcome")) is not str:
        raise DiagnosticError("malformed case")
    outcome = value["outcome"]
    if outcome not in {"success", "failure"}:
        raise DiagnosticError("malformed outcome")
    failed = outcome == "failure"
    if set(value) != fields | ({"stderr_category", "stderr_signatures"} if failed else set()):
        raise DiagnosticError("unsafe case fields")
    validate_geometry(value["family"], value["authored_lock_path_length"],
                      value["observed_lock_path_length"])
    if (value["family"], value["authored_lock_path_length"]) != expected:
        raise DiagnosticError("case identity or order mismatch")
    code = value["return_code"]
    if (type(code) is not int or not MIN_RETURN_CODE <= code <= MAX_RETURN_CODE
            or failed != (code != 0)):
        raise DiagnosticError("inconsistent checked push")
    clean = {key: value[key] for key in fields}
    if failed:
        category, signatures = value["stderr_category"], value["stderr_signatures"]
        if (type(category) is not str or category not in CATEGORIES
                or type(signatures) is not list or not 1 <= len(signatures) <= len(SIGNATURES)
                or any(type(item) is not str or item not in SIGNATURES for item in signatures)
                or signatures != sorted(set(signatures))
                or ("OTHER" in signatures and signatures != ["OTHER"])):
            raise DiagnosticError("unsafe stderr facts")
        clean.update(stderr_category=category, stderr_signatures=list(signatures))
    return clean


def validate_cases(values: object) -> list[dict]:
    if type(values) is not list or len(values) != len(CASES):
        raise DiagnosticError("missing or extra cases")
    return [validate_case(value, expected) for value, expected in zip(values, CASES)]


def relationships(cases: list[dict]) -> dict:
    cases = validate_cases(cases)
    families = []
    for offset, family in enumerate(FAMILIES):
        vector = [case["outcome"] for case in cases[offset * 3:offset * 3 + 3]]
        failures = [length for length, outcome in zip(LENGTHS, vector) if outcome == "failure"]
        # Monotonic means only that failure never returns to success in this
        # three-element vector. It says nothing about any unobserved geometry.
        non_monotonic = any(vector[i] == "failure" and vector[j] == "success"
                            for i in range(3) for j in range(i + 1, 3))
        families.append({
            "family": family, "outcome_vector": vector,
            "first_failed_length": failures[0] if failures else None,
            "relationship": "NON_MONOTONIC" if non_monotonic else "MONOTONIC",
        })
    return {
        "families": families,
        "same_length_agreement": [
            {"lock_path_length": length,
             "agrees": cases[index]["outcome"] == cases[index + 3]["outcome"]}
            for index, length in enumerate(LENGTHS)
        ],
    }


def same_typed_facts(value: object, expected: object) -> bool:
    """Closed shape comparison: JSON coercion must not hide schema defects."""
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return (set(value) == set(expected)
                and all(type(key) is str for key in value)
                and all(same_typed_facts(value[key], item) for key, item in expected.items()))
    if type(expected) is list:
        return len(value) == len(expected) and all(
            same_typed_facts(actual, item) for actual, item in zip(value, expected))
    return value == expected


def validate_envelope(value: object) -> dict:
    fields = {"format", "version", "subject_unchanged", "worktree_clean", "cases", "relationships"}
    if (type(value) is not dict or set(value) != fields or value["format"] != FORMAT
            or type(value["format"]) is not str or type(value["version"]) is not int
            or value["version"] != 1 or value["subject_unchanged"] is not True
            or value["worktree_clean"] is not True):
        raise DiagnosticError("unsafe envelope")
    cases = validate_cases(value["cases"])
    expected = relationships(cases)
    if not same_typed_facts(value["relationships"], expected):
        raise DiagnosticError("contradictory or unsafe relationships")
    return {"format": FORMAT, "version": 1, "subject_unchanged": True,
            "worktree_clean": True, "cases": cases, "relationships": expected}


def diagnose(repository: Path) -> dict:
    from tests.aios_git_fixture_push_threshold_probe import run_case

    repository = repository.resolve()
    subject = subject_identity(repository)
    cases = []
    try:
        with tempfile.TemporaryDirectory(prefix="aios-git-threshold-") as directory:
            root = Path(directory).resolve()
            for family, length in CASES:
                if subject_identity(repository) != subject:
                    raise DiagnosticError("subject changed before case")
                cases.append(validate_case(run_case(root, family, length), (family, length)))
    finally:
        # Also check dirty state and mutation when case instrumentation fails.
        if subject_identity(repository) != subject:
            raise DiagnosticError("subject changed during experiment")
    return validate_envelope({
        "format": FORMAT, "version": 1, "subject_unchanged": True,
        "worktree_clean": True, "cases": cases, "relationships": relationships(cases),
    })


def main(argv: list[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("Git fixture push threshold diagnostic accepts no arguments", file=sys.stderr)
        return 2
    try:
        # Validate again at the only durable-output boundary.
        result = validate_envelope(diagnose(Path.cwd()))
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
    except (Exception, KeyboardInterrupt):
        # All internal/process/schema errors are closed and details stay private.
        print("Git fixture push threshold diagnostic integrity failure", file=sys.stderr)
        return 2
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
