"""One deterministic minimum-sufficient verification contract.

V1 declarations remain readable. V2 derives executions, exact covered probes
and reuse from bounded canonical material inside the authored envelope. This
module executes nothing and owns no semantic or lifecycle decisions. Commands
outside the deliberately small coverage grammar remain opaque.
"""

from __future__ import annotations

import re
import shlex
import hashlib
import json
import importlib.metadata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Sequence


MINIMUM_SUFFICIENT_V1 = "minimum-sufficient-v1"
MINIMUM_SUFFICIENT_V2 = "minimum-sufficient-v2"
VERIFICATION_POLICIES = frozenset({MINIMUM_SUFFICIENT_V1, MINIMUM_SUFFICIENT_V2})
CURRENT_VERIFICATION_POLICY = MINIMUM_SUFFICIENT_V2
MAX_CANONICAL_REPORTS = 100_000
MAX_CANONICAL_FAILURES = 10_000
MAX_CANONICAL_NODEID_CHARS = 16_384
MAX_FAILURE_DETAIL_CHARS = 16_384
MAX_CANONICAL_BYTES = 16 * 1024 * 1024
COLLECTION_IDENTITY_RULE = "sorted-posix-nodeid-lf-sha256-v1"
FULL_SUITE_REASON_LIMIT = 512
BP_V4_PROBE_PATH = "scripts/bp_v4_parallel_probe.py"
BP_V4_WORKERS = (2, 3, 4)
REBASELINE_PATH = "scripts/bp_v4_parallel_rebaseline.py"
REBASELINE_COMMAND = "python scripts/bp_v4_parallel_rebaseline.py --workers 4 8 12 16"
REBASELINE_WORKERS = (4, 8, 12, 16)
SELECTED_FULL_SUITE_PATH = "scripts/aios_parallel_full_suite.py"
SELECTED_FULL_SUITE_COMMAND = "python scripts/aios_parallel_full_suite.py"
# Fixed cost bounds, not test-impact or proof-selection policy. A PRIMARY
# delta may duplicate at most a quarter of the authored candidate population.
MAX_DELTA_PROJECTION_NODES = 64
DELTA_PROJECTION_MAX_FRACTION = 4
MAX_EXACT_PROJECTION_NODES = 128
MAX_EXACT_PROJECTION_COMMAND_CHARS = 8_192


class VerificationContractError(ValueError):
    """Raised when verification declarations or canonical material are invalid."""


@dataclass(frozen=True)
class PytestCoverage:
    """Coverage proven from one command in the supported pytest grammar."""

    launcher: str
    paths: tuple[str, ...]
    filter_expression: str | None
    measurement: bool = False
    measurement_family: str | None = None
    selected_parallel: bool = False

    @property
    def is_full_suite(self) -> bool:
        return not self.paths and self.filter_expression is None


def parse_pytest_coverage(command: str) -> PytestCoverage | None:
    """Return proven coverage for a supported command, otherwise ``None``."""

    if parse_rebaseline_workers(command) is not None:
        return PytestCoverage(
            launcher="python -m pytest", paths=(), filter_expression=None,
            measurement=True, measurement_family="bp-v4-rebaseline-v2",
        )
    if command == SELECTED_FULL_SUITE_COMMAND:
        return PytestCoverage(
            launcher="python -m pytest", paths=(), filter_expression=None,
            selected_parallel=True,
        )
    probe_workers = parse_bp_v4_probe_workers(command)
    if probe_workers is not None:
        return PytestCoverage(
            launcher="python -m pytest",
            paths=(),
            filter_expression=None,
            measurement=True,
            measurement_family="bp-v4-v1",
        )

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None

    if tokens[0] == "pytest":
        launcher = "pytest"
        arguments = tokens[1:]
    elif tokens[:3] == ["python", "-m", "pytest"]:
        launcher = "python -m pytest"
        arguments = tokens[3:]
    else:
        return None

    paths: list[str] = []
    filter_expression: str | None = None
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in {"-q", "--quiet"} or (
            token.startswith("-")
            and len(token) > 1
            and set(token[1:]) == {"q"}
        ):
            index += 1
            continue
        if token == "-k":
            if filter_expression is not None or index + 1 >= len(arguments):
                return None
            filter_expression = arguments[index + 1]
            if not filter_expression:
                return None
            index += 2
            continue
        if token.startswith("-k="):
            if filter_expression is not None or not token[3:]:
                return None
            filter_expression = token[3:]
            index += 1
            continue
        if token.startswith("-") or not _is_supported_test_path(token):
            return None
        paths.append(_normalize_test_path(token))
        index += 1

    # Repeated paths add no coverage and are normalized mechanically.
    return PytestCoverage(
        launcher=launcher,
        paths=tuple(dict.fromkeys(paths)),
        filter_expression=filter_expression,
    )


def parse_bp_v4_probe_workers(command: str) -> tuple[int, ...] | None:
    """Return the explicitly authorized workers for one exact BP-V4 probe."""

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    prefix = ["python", BP_V4_PROBE_PATH, "--workers"]
    if tokens[:3] != prefix or not 4 <= len(tokens) <= 6:
        return None
    encoded = tokens[3:]
    allowed = {str(worker) for worker in BP_V4_WORKERS}
    if any(value not in allowed for value in encoded):
        return None
    workers = tuple(int(value) for value in encoded)
    if workers != tuple(sorted(set(workers))):
        return None
    return workers


def parse_rebaseline_workers(command: str) -> tuple[int, int, int, int] | None:
    """Recognize only the complete fixed rebaseline invocation."""

    return REBASELINE_WORKERS if command == REBASELINE_COMMAND else None


def _is_rebaseline_family(command: str) -> bool:
    """Catch malformed rebaseline invocations before opaque classification."""

    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return re.match(
            r'^\s*["\']?(?:python|py)["\']?\s+["\']?(?:\.[\\/])?scripts[\\/]bp_v4_parallel_rebaseline\.py(?=["\']|\s|$)',
            command, flags=re.IGNORECASE,
        ) is not None
    if len(tokens) < 2 or tokens[0].strip('"\'').casefold() not in {"python", "py"}:
        return False
    path = tokens[1].strip('"\'').replace("\\", "/").casefold()
    if path.startswith("./"):
        path = path[2:]
    return path == REBASELINE_PATH.casefold()


def _is_bp_v4_probe_family(command: str) -> bool:
    """Identify probe-like input so malformed forms cannot become opaque."""

    try:
        # Preserve Windows path separators while identifying the invocation
        # family; the exact valid grammar above remains POSIX-normalized.
        tokens = shlex.split(command, posix=False)
    except ValueError:
        # A broken quote after an invocation prefix must still fail closed.
        return re.match(
            r'^\s*(?:["\']?(?:python|py)["\']?)\s+'
            r'["\']?(?:\.[\\/])?scripts[\\/]bp_v4_parallel_probe\.py'
            r'(?=["\']|\s|$)',
            command,
            flags=re.IGNORECASE,
        ) is not None

    if len(tokens) < 2 or tokens[0].casefold() not in {"python", "py"}:
        return False
    probe_path = tokens[1].replace("\\", "/").casefold()
    if probe_path.startswith("./"):
        probe_path = probe_path[2:]
    return probe_path == BP_V4_PROBE_PATH.casefold()


def _is_selected_full_suite_family(command: str) -> bool:
    """Catch malformed invocations of the exact selected wrapper family."""

    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return re.match(
            r'^\s*["\']?(?:python|py)["\']?\s+["\']?(?:\.[\\/])?scripts[\\/]aios_parallel_full_suite\.py(?=["\']|\s|$)',
            command, flags=re.IGNORECASE,
        ) is not None
    if len(tokens) < 2 or tokens[0].casefold() not in {"python", "py"}:
        return False
    path = tokens[1].strip('"\'').replace("\\", "/").casefold()
    if path.startswith("./"):
        path = path[2:]
    return path == SELECTED_FULL_SUITE_PATH.casefold()


def validate_v1_verification(
    commands: Sequence[str],
    *,
    full_suite_reason: str | None,
    path: str,
) -> None:
    """Validate one author-declared v1 list without rewriting it."""

    if not commands:
        raise VerificationContractError(f"{path} must contain at least one command")

    reason = full_suite_reason
    if reason is not None:
        if not isinstance(reason, str) or not reason.strip():
            raise VerificationContractError(
                "full_suite_reason must be a non-empty string"
            )
        if len(reason) > FULL_SUITE_REASON_LIMIT:
            raise VerificationContractError(
                f"full_suite_reason must be at most {FULL_SUITE_REASON_LIMIT} characters"
            )

    for command in commands:
        if _is_rebaseline_family(command) and parse_rebaseline_workers(command) is None:
            raise VerificationContractError(
                f"{path} contains malformed BP-V4 rebaseline command: {command!r}"
            )
        if _is_selected_full_suite_family(command) and command != SELECTED_FULL_SUITE_COMMAND:
            raise VerificationContractError(
                f"{path} contains malformed selected full-suite command: {command!r}"
            )
        if (
            _is_bp_v4_probe_family(command)
            and parse_bp_v4_probe_workers(command) is None
        ):
            raise VerificationContractError(
                f"{path} contains malformed BP-V4 probe command: {command!r}"
            )

    coverages = [parse_pytest_coverage(command) for command in commands]
    has_full_suite = any(
        coverage is not None and coverage.is_full_suite
        for coverage in coverages
    )
    if has_full_suite and reason is None:
        raise VerificationContractError(
            "full_suite_reason is required for a recognized full-suite command"
        )
    if not has_full_suite and reason is not None:
        raise VerificationContractError(
            "full_suite_reason is allowed only with a recognized full-suite command"
        )

    for later_index, later in enumerate(commands):
        for earlier_index in range(later_index):
            earlier = commands[earlier_index]
            if earlier == later:
                raise VerificationContractError(
                    f"{path} contains exact duplicate command: {later}"
                )
            relation = _coverage_relation(
                coverages[earlier_index], coverages[later_index]
            )
            if relation == "equivalent":
                raise VerificationContractError(
                    f"{path} contains coverage-equivalent commands: "
                    f"{earlier!r} and {later!r}"
                )
            if relation in {"left-subsumes", "right-subsumes"}:
                raise VerificationContractError(
                    f"{path} contains provably subsumed commands: "
                    f"{earlier!r} and {later!r}"
                )


def normalize_verification(commands: Sequence[str]) -> tuple[str, ...]:
    """Normalize overlap introduced by combining valid v1 sources.

    Equivalent commands retain their earliest occurrence.  A command strictly
    subsumed by another recognized command is removed.  Opaque commands are
    affected only by exact string duplication.
    """

    for command in commands:
        if _is_rebaseline_family(command) and parse_rebaseline_workers(command) is None:
            raise VerificationContractError(
                f"verification contains malformed BP-V4 rebaseline command: {command!r}"
            )
        if _is_selected_full_suite_family(command) and command != SELECTED_FULL_SUITE_COMMAND:
            raise VerificationContractError(
                f"verification contains malformed selected full-suite command: {command!r}"
            )
        if (
            _is_bp_v4_probe_family(command)
            and parse_bp_v4_probe_workers(command) is None
        ):
            raise VerificationContractError(
                f"verification contains malformed BP-V4 probe command: {command!r}"
            )

    unique: list[str] = []
    for command in commands:
        if command not in unique:
            unique.append(command)

    coverages = [parse_pytest_coverage(command) for command in unique]
    removed: set[int] = set()
    for left_index in range(len(unique)):
        if left_index in removed:
            continue
        for right_index in range(left_index + 1, len(unique)):
            if right_index in removed:
                continue
            relation = _coverage_relation(
                coverages[left_index], coverages[right_index]
            )
            if relation == "equivalent":
                removed.add(right_index)
            elif relation == "left-subsumes":
                removed.add(right_index)
            elif relation == "right-subsumes":
                removed.add(left_index)
                break
    return tuple(
        command for index, command in enumerate(unique) if index not in removed
    )


def _coverage_relation(
    left: PytestCoverage | None, right: PytestCoverage | None
) -> str | None:
    if left is None or right is None or left.launcher != right.launcher:
        return None

    left_subsumes = _subsumes(left, right)
    right_subsumes = _subsumes(right, left)
    if left_subsumes and right_subsumes:
        return "equivalent"
    if left_subsumes:
        return "left-subsumes"
    if right_subsumes:
        return "right-subsumes"
    return None


def _subsumes(broader: PytestCoverage, narrower: PytestCoverage) -> bool:
    if broader.launcher != narrower.launcher:
        return False
    # A probe contains the ordinary module-launched full-suite proof as well as
    # its measurement profiles.  The ordinary proof cannot replace the probe.
    if narrower.measurement and not broader.measurement:
        return False
    if (
        broader.measurement and narrower.measurement
        and broader.measurement_family != narrower.measurement_family
    ):
        return False
    if narrower.selected_parallel and not broader.selected_parallel:
        return False
    if broader.measurement and narrower.selected_parallel:
        return False
    if broader.selected_parallel and narrower.measurement:
        return False
    if broader.filter_expression is not None:
        if broader.filter_expression != narrower.filter_expression:
            return False
    # An unfiltered scope covers the same scope with any -k narrowing.
    return _path_scope_subsumes(broader.paths, narrower.paths)


def _path_scope_subsumes(
    broader_paths: tuple[str, ...], narrower_paths: tuple[str, ...]
) -> bool:
    if not broader_paths:
        return True
    if not narrower_paths:
        return False
    return all(
        any(_path_subsumes(broader, narrower) for broader in broader_paths)
        for narrower in narrower_paths
    )


def _path_subsumes(broader: str, narrower: str) -> bool:
    broader_file, broader_nodes = _split_node_path(broader)
    narrower_file, narrower_nodes = _split_node_path(narrower)
    if broader_nodes:
        return (
            broader_file == narrower_file
            and narrower_nodes[: len(broader_nodes)] == broader_nodes
        )
    if broader_file == narrower_file:
        return True
    prefix = broader_file.rstrip("/") + "/"
    return narrower_file.startswith(prefix)


def _split_node_path(path: str) -> tuple[str, tuple[str, ...]]:
    file_path, *nodes = path.split("::")
    return file_path, tuple(nodes)


def _is_supported_test_path(token: str) -> bool:
    if any(character in token for character in "*?[]\\;&|<>$`()"):
        return False
    file_path, *nodes = token.split("::")
    if not file_path or any(not node for node in nodes):
        return False
    pure = PurePosixPath(file_path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return False
    return True


def _normalize_test_path(token: str) -> str:
    file_path, *nodes = token.split("::")
    normalized = PurePosixPath(file_path).as_posix().rstrip("/")
    if nodes:
        return normalized + "::" + "::".join(nodes)
    return normalized


def exact_pytest_nodeids(value: object) -> tuple[str, ...]:
    """Validate lossless positional identities; never guess a partial selector.

    Only the file portion is POSIX-normalized by the observer. Parameter text
    (including quotes, brackets, spaces and backslashes) remains literal.
    Unsupported collector identities use the broader attribution path.
    """
    if not isinstance(value, (list, tuple)) or not value or len(value) > MAX_CANONICAL_REPORTS:
        raise VerificationContractError("missing or oversized exact pytest population")
    for nodeid in value:
        if (not isinstance(nodeid, str) or len(nodeid) > MAX_CANONICAL_NODEID_CHARS
                or any(ord(c) < 32 or ord(c) == 127 for c in nodeid)):
            raise VerificationContractError("malformed exact pytest nodeid")
        file_path, separator, selector = nodeid.partition("::")
        stem, parameter, suffix = selector.partition("[")
        if (not separator or not file_path.endswith(".py") or ":" in file_path
                or not _is_supported_test_path(file_path)
                or _normalize_test_path(file_path) != file_path
                or any(not part.isidentifier() for part in stem.split("::"))
                or (parameter and not suffix.endswith("]"))):
            raise VerificationContractError("ambiguous exact pytest nodeid")
        try:
            nodeid.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise VerificationContractError("invalid exact pytest nodeid encoding") from exc
    if tuple(value) != tuple(sorted(set(value))):
        raise VerificationContractError("duplicate or unordered exact pytest population")
    return tuple(value)


def validate_exact_collection(value: object) -> tuple[str, ...]:
    """Require the complete list and its historical compact identity together."""
    if not isinstance(value, dict) or set(value) != {"identity", "nodeids"}:
        raise VerificationContractError("missing exact pytest collection truth")
    nodes = value["nodeids"]
    if nodes == []:
        exact = ()
    else:
        exact = exact_pytest_nodeids(nodes)
    if pytest_collection_identity(value["identity"]) != pytest_collection_identity(list(exact)):
        raise VerificationContractError("exact pytest collection disagrees with its identity")
    verification_digest(value)
    return exact


def pytest_projection_command(command: str, nodeids: Sequence[str]) -> str:
    """Encode an exact serial projection of a recognized authored requirement."""
    coverage = parse_pytest_coverage(command)
    if (coverage is None or coverage.measurement or coverage.selected_parallel
            or coverage.filter_expression is not None):
        raise VerificationContractError("authored command has no comparable serial projection")
    nodes = exact_pytest_nodeids(nodeids)
    if (len(nodes) > MAX_EXACT_PROJECTION_NODES
            or not _path_scope_subsumes(coverage.paths, nodes)):
        raise VerificationContractError("exact pytest projection is outside its bounded coverage")
    # Always quote each positional identity. Windows Runtime submits this same
    # argv directly rather than using PowerShell's lossy native marshalling.
    encoded = " ".join("'" + n.replace("'", "'\"'\"'") + "'" for n in nodes)
    probe = coverage.launcher + " -q " + encoded
    if len(probe) > MAX_EXACT_PROJECTION_COMMAND_CHARS:
        raise VerificationContractError("exact pytest projection exceeds command bound")
    return probe


def pytest_collection_command(command: str) -> str:
    """Collect the exact authored argv without changing its selection."""
    coverage = parse_pytest_coverage(command)
    if (coverage is None or coverage.measurement or coverage.selected_parallel
            or coverage.filter_expression is not None):
        raise VerificationContractError("authored command has no exact serial collection")
    return command + " --collect-only"


def _execution_conditions(value: dict) -> tuple[dict, dict]:
    return ({k: v for k, v in value["profile"].items() if k != "collect_only"}, value["toolchain"])


def exact_collection_observation(value: object, *, command: str, subject_sha: str) -> tuple[str, ...]:
    observed = validate_observation(value)
    if (observed["command"] != pytest_collection_command(command)
            or observed["subject_sha"] != subject_sha or observed["exit_code"] != 0
            or observed["profile"].get("collect_only") is not True or observed["reports"]):
        raise VerificationContractError("collection observation is not bound to authored coverage")
    return validate_exact_collection(observed.get("collection"))


def candidate_delta_projection(base: object, candidate: object, *, command: str,
                               base_sha: str, candidate_sha: str) -> tuple[str, ...]:
    """Derive only a bounded mechanical set difference; absence never proves PASS."""
    coverage = parse_pytest_coverage(command)
    if coverage is None or coverage.is_full_suite:
        raise VerificationContractError("full-suite guards are not delta-first requirements")
    before = exact_collection_observation(base, command=command, subject_sha=base_sha)
    after = exact_collection_observation(candidate, command=command, subject_sha=candidate_sha)
    if _execution_conditions(base) != _execution_conditions(candidate):
        raise VerificationContractError("collection execution conditions are not comparable")
    nodes = tuple(sorted(set(after).difference(before)))
    if (not nodes or len(nodes) > MAX_DELTA_PROJECTION_NODES
            or len(nodes) * DELTA_PROJECTION_MAX_FRACTION > len(after)):
        raise VerificationContractError("candidate delta is empty or uneconomical")
    pytest_projection_command(command, nodes)
    return nodes


def validate_exact_projection(value: object, *, command: str, nodeids: Sequence[str],
                              subject_sha: str) -> dict:
    observed = validate_observation(value)
    nodes = exact_pytest_nodeids(nodeids)
    if (observed["subject_sha"] != subject_sha
            or observed["command"] != pytest_projection_command(command, nodes)
            or observed["exit_code"] not in {0, 1}
            or observed["profile"].get("collect_only") is not False
            or validate_exact_collection(observed.get("collection")) != nodes
            or {r["nodeid"] for r in observed["reports"]} != set(nodes)
            or any(r["phase"] == "collect" for r in observed["reports"])):
        raise VerificationContractError("exact pytest projection did not execute its complete population")
    return observed


def validate_failure_reproduction(candidate: object, projection: object, *, command: str,
                                  candidate_sha: str) -> tuple[str, ...]:
    broad = validate_observation(candidate)
    nodes = tuple(sorted({node for node, _ in failed_identities(broad)}))
    probe = validate_exact_projection(projection, command=command, nodeids=nodes, subject_sha=candidate_sha)
    if (broad["subject_sha"] != candidate_sha or broad["command"] != command
            or broad["exit_code"] != 1 or probe["exit_code"] != 1
            or broad["profile"] != probe["profile"] or broad["toolchain"] != probe["toolchain"]
            or not set(nodes) <= set(validate_exact_collection(broad.get("collection")))
            or failed_identities(broad) != failed_identities(probe)):
        raise VerificationContractError("candidate failure projection did not reproduce exact failures")
    facts = {(r["nodeid"], r["phase"]): r for r in probe["reports"]}
    for report in broad["reports"]:
        if report["outcome"] == "FAIL" and any(
                report[k] != facts[(report["nodeid"], report["phase"])][k] for k in ("detail", "fingerprint")):
            raise VerificationContractError("candidate projection failure detail changed")
    return nodes


def projected_failure_attribution(projection: object, candidate: object, *, command: str,
                                  base_sha: str, candidate_sha: str) -> tuple[dict, ...]:
    """Recompute projection proof; never trust cached classifications."""
    if not isinstance(projection, dict):
        raise VerificationContractError("missing bound failure projection")
    probe = projection["candidate"]
    collection = projection["base_collection"]
    for key in ("candidate", "base_collection"):
        if projection.get(key + "_digest") != verification_digest(projection[key]):
            raise VerificationContractError("failure projection digest mismatch")
    nodes = validate_failure_reproduction(candidate, probe, command=command, candidate_sha=candidate_sha)
    if projection.get("nodeids") != list(nodes) or projection.get("command") != probe["command"]:
        raise VerificationContractError("failure projection identity mismatch")
    base_nodes = exact_collection_observation(collection, command=command, subject_sha=base_sha)
    if _execution_conditions(collection) != _execution_conditions(probe):
        raise VerificationContractError("base collection conditions are not comparable")
    absent = set(nodes).difference(base_nodes)
    classifications = {}
    if not absent:
        base = projection["base"]
        if projection.get("base_digest") != verification_digest(base):
            raise VerificationContractError("base projection digest mismatch")
        validate_exact_projection(base, command=command, nodeids=nodes, subject_sha=base_sha)
        if _execution_conditions(base) != _execution_conditions(collection):
            raise VerificationContractError("base projection conditions changed after collection")
        classifications = {(r["nodeid"], r["phase"]): r["classification"] for r in attribute_failures(
            base, probe, base_sha=base_sha, candidate_sha=candidate_sha)}
    return tuple({"nodeid": node, "phase": phase,
                  "classification": "CANDIDATE_ONLY" if node in absent else classifications.get((node, phase), "UNRESOLVED")}
                 for node, phase in failed_identities(candidate))


def pytest_collection_identity(value: object) -> dict:
    """Build or validate bounded collection truth, independently of reports.

    Historical lists normalize separators, sort unique nodeids by Unicode code
    point, and hash UTF-8 nodeids terminated by LF. Compact observations carry
    only this rule, count and digest; display identities never enter the hash.
    """
    if isinstance(value, dict):
        if (set(value) != {"rule", "count", "digest"}
                or value.get("rule") != COLLECTION_IDENTITY_RULE
                or type(value.get("count")) is not int
                or not 0 <= value["count"] <= MAX_CANONICAL_REPORTS
                or not isinstance(value.get("digest"), str)
                or re.fullmatch(r"[0-9a-f]{64}", value["digest"]) is None
                or ((value["count"] == 0)
                    != (value["digest"] == hashlib.sha256(b"").hexdigest()))):
            raise VerificationContractError("malformed pytest collection identity")
        return dict(value)
    if (not isinstance(value, list) or len(value) > MAX_CANONICAL_REPORTS
            or any(not isinstance(item, str) or not item
                   or len(item) > MAX_CANONICAL_NODEID_CHARS for item in value)):
        raise VerificationContractError("malformed pytest collection data")
    normalized = [item.replace("\\", "/") for item in value]
    if len(normalized) != len(set(normalized)):
        raise VerificationContractError("pytest collection contains duplicate node identities")
    digest = hashlib.sha256()
    for item in sorted(normalized):
        digest.update((item + "\n").encode("utf-8"))
    return {"rule": COLLECTION_IDENTITY_RULE, "count": len(normalized),
            "digest": digest.hexdigest()}


def verification_digest(value: object) -> str:
    """Hash bounded canonical JSON, never a clipped display representation."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_CANONICAL_BYTES:
        raise VerificationContractError("canonical verification material exceeds byte bound")
    return hashlib.sha256(encoded).hexdigest()


def toolchain_inventory_digest() -> str:
    """Bind installed dependency/plugin versions without exposing host paths."""
    inventory = sorted((distribution.metadata.get("Name", ""), distribution.version)
                       for distribution in importlib.metadata.distributions())
    return verification_digest(inventory)


def validate_observation(value: object) -> dict:
    """Require complete exact phase outcomes before permitting attribution."""
    if not isinstance(value, dict):
        raise VerificationContractError("missing canonical observation")
    verification_digest(value)
    if value.get("complete") is not True or value.get("unstable") is not False:
        raise VerificationContractError("incomplete or unstable canonical observation")
    for key in ("subject_sha", "command"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise VerificationContractError("missing observation binding")
    if re.fullmatch(r"[0-9a-f]{40}", value["subject_sha"]) is None:
        raise VerificationContractError("observation requires an exact commit")
    for key in ("profile", "toolchain"):
        if not isinstance(value.get(key), dict) or not value[key]:
            raise VerificationContractError("missing observation conditions")
    if any(not isinstance(value["toolchain"].get(k), str) or not value["toolchain"][k] for k in (
        "python_implementation", "python_version", "python_executable", "platform_system",
        "platform_machine", "pytest_version", "pytest_xdist_version")):
        raise VerificationContractError("incomplete comparable toolchain identity")
    status = value.get("exit_code")
    if type(status) is not int:
        raise VerificationContractError("invalid raw observation exit")
    reports = value.get("reports")
    if not isinstance(reports, list) or len(reports) > MAX_CANONICAL_REPORTS:
        raise VerificationContractError("canonical report population exceeds bound")
    previous = None
    failures = 0
    for report in reports:
        if not isinstance(report, dict):
            raise VerificationContractError("invalid canonical report")
        nodeid, phase = report.get("nodeid"), report.get("phase")
        if (not isinstance(nodeid, str) or not nodeid or "\\" in nodeid.split("::", 1)[0]
                or len(nodeid) > MAX_CANONICAL_NODEID_CHARS
                or not isinstance(phase, str) or phase not in {"collect", "setup", "call", "teardown"}):
            raise VerificationContractError("invalid exact canonical identity")
        key = (nodeid, {"collect": -1, "setup": 0, "call": 1, "teardown": 2}[phase])
        if previous is not None and key <= previous:
            raise VerificationContractError("duplicate or unordered canonical report")
        previous = key
        if report.get("outcome") not in ("PASS", "FAIL", "SKIP"):
            raise VerificationContractError("invalid canonical phase outcome")
        if report["outcome"] == "FAIL":
            failures += 1
            detail = report.get("detail")
            if (not isinstance(detail, str) or not detail
                    or len(detail) > MAX_FAILURE_DETAIL_CHARS
                    or report.get("fingerprint") != verification_digest(detail)):
                raise VerificationContractError("missing or invalid bounded failure fingerprint")
            if report.get("profile") != value["profile"] or report.get("toolchain") != value["toolchain"]:
                raise VerificationContractError("failure conditions do not match observation")
    if failures > MAX_CANONICAL_FAILURES or type(value.get("failure_count")) is not int or value.get("failure_count") != failures:
        raise VerificationContractError("incomplete canonical failed identity population")
    if status == 0 and failures:
        raise VerificationContractError("successful command reports canonical failures")
    if status == 1 and not failures:
        raise VerificationContractError("failure exit has no canonical failed identity population")
    if "collection" in value:
        validate_exact_collection(value["collection"])
    return value


def failed_identities(observation: dict) -> tuple[tuple[str, str], ...]:
    value = validate_observation(observation)
    return tuple((r["nodeid"], r["phase"]) for r in value["reports"] if r["outcome"] == "FAIL")


def attribute_failures(base: object, candidate: object, *, base_sha: str,
                       candidate_sha: str) -> tuple[dict, ...]:
    """Fail closed: same identity alone never proves a baseline failure."""
    candidate_reports = candidate.get("reports", []) if isinstance(candidate, dict) else []
    identities = sorted({(r.get("nodeid"), r.get("phase")) for r in candidate_reports
                         if isinstance(r, dict) and r.get("outcome") == "FAIL"
                         and isinstance(r.get("nodeid"), str) and isinstance(r.get("phase"), str)})
    comparable = False
    try:
        left, right = validate_observation(base), validate_observation(candidate)
        comparable = (left["subject_sha"] == base_sha and right["subject_sha"] == candidate_sha
                      and left["exit_code"] in {0, 1} and right["exit_code"] == 1
                      and all(left[k] == right[k] for k in ("command", "profile", "toolchain")))
    except (VerificationContractError, TypeError, ValueError):
        left, right = {}, {}
    base_reports = {(r["nodeid"], r["phase"]): r for r in left.get("reports", [])}
    candidate_by_identity = {(r["nodeid"], r["phase"]): r for r in right.get("reports", [])}
    result = []
    for nodeid, phase in identities:
        classification = "UNRESOLVED"
        previous = base_reports.get((nodeid, phase))
        current = candidate_by_identity.get((nodeid, phase))
        if comparable and previous and current:
            if previous["outcome"] == "PASS":
                classification = "CANDIDATE_REGRESSION"
            elif (previous["outcome"] == "FAIL"
                  and previous["fingerprint"] == current["fingerprint"]
                  and previous["detail"] == current["detail"]):
                classification = "PRE_EXISTING_BASELINE"
        result.append({"nodeid": nodeid, "phase": phase, "classification": classification})
    return tuple(result)


def attributed_task_passes(record: object, *, subject_sha: str,
                           command: str, exit_code: int) -> bool:
    """Recompute the higher-level outcome without changing raw command status."""
    if not isinstance(record, dict) or record.get("policy") != MINIMUM_SUFFICIENT_V2:
        return False
    if "early_probe" in record:
        # An optimization observation has no authored proof authority, even
        # when the exact-node execution succeeds.
        return False
    try:
        candidate = validate_observation(record.get("candidate"))
        if record.get("candidate_digest") != verification_digest(candidate):
            return False
        binding = record["binding"]
        validate_evidence_binding(binding)
        observed_sha = binding["subject_sha"]
        if "reuse" in record:
            reuse = record["reuse"]
            target = reuse["binding"]
            validate_evidence_binding(target)
            if (target["subject_sha"] != subject_sha
                    or {k: v for k, v in target.items() if k != "subject_sha"}
                    != {k: v for k, v in binding.items() if k != "subject_sha"}
                    or reuse.get("source_evidence_id") != record.get("evidence_id")):
                return False
        elif observed_sha != subject_sha:
            return False
        if (candidate["subject_sha"] != observed_sha or candidate["command"] != command
                or candidate["exit_code"] != exit_code
                or binding["command"] != command
                or binding["profile"] != candidate["profile"]
                or binding["toolchain"] != candidate["toolchain"]):
            return False
        if exit_code == 0:
            return True
        # pytest exit 1 alone means runtest failures. Collection/internal errors,
        # interrupts, worker crashes and empty failure populations cannot pass.
        if exit_code != 1 or not failed_identities(candidate):
            return False
        base_sha = binding["base_sha"]
        if "failure_projection" in record:
            projection = record["failure_projection"]
            digest = verification_digest(projection)
            if (record.get("failure_projection_digest") != digest
                    or binding["failure_set_digest"] != digest):
                return False
            classifications = projected_failure_attribution(projection, candidate,
                command=command, base_sha=base_sha, candidate_sha=observed_sha)
            if tuple(record.get("attribution", ())) != classifications:
                return False
            return bool(classifications) and all(
                item["classification"] == "PRE_EXISTING_BASELINE" for item in classifications)
        if (record.get("base_digest") != verification_digest(record.get("base"))
                or binding["failure_set_digest"] != record["base_digest"]):
            return False
        classifications = attribute_failures(record.get("base"), candidate,
                                             base_sha=base_sha, candidate_sha=observed_sha)
        if "attribution" in record and tuple(record["attribution"]) != classifications:
            return False
        return bool(classifications) and all(
            item["classification"] == "PRE_EXISTING_BASELINE" for item in classifications)
    except (KeyError, TypeError, ValueError, VerificationContractError):
        return False


def validate_evidence_binding(binding: object) -> dict:
    """Require all reuse invalidators, including the complete tracked tree."""
    if not isinstance(binding, dict):
        raise VerificationContractError("missing verification evidence binding")
    for key in ("subject_sha", "base_sha", "tree_sha"):
        if not isinstance(binding.get(key), str) or re.fullmatch(r"[0-9a-f]{40}", binding[key]) is None:
            raise VerificationContractError("evidence requires exact Git identities")
    if "correction_base_sha" in binding and (not isinstance(binding["correction_base_sha"], str)
            or re.fullmatch(r"[0-9a-f]{40}", binding["correction_base_sha"]) is None):
        raise VerificationContractError("correction evidence requires an exact admitted subject")
    for key in ("envelope_digest", "changed_files_digest", "failure_set_digest"):
        if not isinstance(binding.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", binding[key]) is None:
            raise VerificationContractError("missing evidence invalidation binding")
    if "correction_changed_files_digest" in binding and (not isinstance(binding["correction_changed_files_digest"], str)
            or re.fullmatch(r"[0-9a-f]{64}", binding["correction_changed_files_digest"]) is None):
        raise VerificationContractError("missing correction delta binding")
    for key in ("profile", "toolchain"):
        if not isinstance(binding.get(key), dict) or not binding[key]:
            raise VerificationContractError("missing bound verification conditions")
    if not isinstance(binding.get("command"), str) or not binding["command"]:
        raise VerificationContractError("missing exact verification command")
    verification_digest(binding)
    return binding


@dataclass(frozen=True)
class VerificationPlan:
    """One V2 derivation result: explicit executions, reuse and fallback reasons."""
    commands: tuple[str, ...]
    reused: tuple[dict, ...]
    affected_first: tuple[str, ...]
    fallback: tuple[str, ...]
    affected_requirements: tuple[tuple[str, str], ...] = ()


def derive_minimum_verification(
    authored: Sequence[str], *, base_sha: str, candidate_sha: str,
    changed_files: Sequence[str], modification_scope: Sequence[str],
    operation: str, bindings: dict[str, dict], failed_observations: Sequence[dict] = (),
    still_valid_evidence: Sequence[dict] = (),
    correction_base_sha: str | None = None,
    correction_changed_files: Sequence[str] | None = None,
) -> VerificationPlan:
    """The single deterministic boundary; no path-to-dependency inference.

    Cross-candidate reuse requires the entire tracked tree and execution context
    to match. Failed identities authorize only an exact covered first probe;
    they never discharge the authored broader requirement or integration guard.
    """
    commands, reused, fallback = [], [], []
    exact = all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base_sha, candidate_sha))
    correction_delta = changed_files if correction_changed_files is None else correction_changed_files
    failure_subject = base_sha if correction_base_sha is None else correction_base_sha
    if not isinstance(failure_subject, str) or re.fullmatch(r"[0-9a-f]{40}", failure_subject) is None:
        exact = False
    scope_valid = operation not in {"REPAIR", "REMEDIATION", "DIRECT_CANDIDATE"} or set(correction_delta) <= set(modification_scope)
    for command in authored:
        target = bindings.get(command)
        matches = []
        if exact and scope_valid and target and parse_pytest_coverage(command) is not None:
            for item in still_valid_evidence:
                old = item.get("binding", {})
                try:
                    validate_evidence_binding(old)
                    validate_evidence_binding(target)
                except VerificationContractError:
                    continue
                # subject and raw proof are rebound explicitly in Runtime; all
                # other bindings, including complete-tree identity, must match.
                if ({k: v for k, v in old.items() if k != "subject_sha"}
                        == {k: v for k, v in target.items() if k != "subject_sha"}
                        and attributed_task_passes(item, subject_sha=old.get("subject_sha", ""),
                                                   command=command, exit_code=item.get("candidate", {}).get("exit_code"))):
                    matches.append(item)
        if matches:
            reused.append(sorted(matches, key=lambda r: r["evidence_id"])[0])
        else:
            commands.append(command)
            fallback.append(command)
    affected, affected_requirements = [], []
    moved_authored = set()
    if exact and scope_valid and operation in {"REPAIR", "REMEDIATION", "DIRECT_CANDIDATE"}:
        for command in commands:
            coverage = parse_pytest_coverage(command)
            if coverage is None or coverage.measurement or coverage.filter_expression is not None:
                continue
            try:
                target = validate_evidence_binding(bindings.get(command))
                if (target["subject_sha"] != candidate_sha or target["base_sha"] != base_sha or target["command"] != command
                        or target.get("correction_base_sha", failure_subject) != failure_subject):
                    continue
            except VerificationContractError:
                continue
            nodes = set()
            observations = [o for o in failed_observations if isinstance(o, dict)
                            and o.get("subject_sha") == failure_subject and o.get("command") == command]
            try:
                conflict = len({verification_digest(o) for o in observations}) > 1
            except (VerificationContractError, TypeError, ValueError):
                conflict = True
            if conflict:
                continue
            for observation in observations:
                try:
                    value = validate_observation(observation)
                    if value["exit_code"] != 1:
                        continue
                    if value["profile"] != bindings[command]["profile"] or value["toolchain"] != bindings[command]["toolchain"]:
                        continue
                    for nodeid, _ in failed_identities(value):
                        file_path = nodeid.split("::", 1)[0]
                        if (file_path in modification_scope and "::" in nodeid
                                and _path_scope_subsumes(coverage.paths, (nodeid,))
                                and not any(c in nodeid for c in "\r\n\x00")):
                            nodes.add(nodeid)
                except (VerificationContractError, KeyError, TypeError, ValueError):
                    continue
            if nodes:
                try:
                    probe = pytest_projection_command(
                        coverage.launcher + " -q" if coverage.selected_parallel else command,
                        tuple(sorted(nodes)))
                except VerificationContractError:
                    continue
                # Only an authored exact-node requirement can be moved in
                # place of this probe. A covering file/directory command still
                # runs after exact-probe success.
                covering = next((c for c in authored if (
                    (known_coverage := parse_pytest_coverage(c)) is not None
                    and known_coverage.paths and not known_coverage.measurement
                    and known_coverage.filter_expression is None
                    and known_coverage.launcher == coverage.launcher
                    and tuple(sorted(known_coverage.paths)) == tuple(sorted(nodes))
                )), None)
                if covering is not None:
                    if covering in commands and covering not in affected:
                        affected.append(covering)
                        moved_authored.add(covering)
                        affected_requirements.append((covering, command))
                    continue
                if probe not in authored and probe not in affected:
                    affected.append(probe)
                    affected_requirements.append((probe, command))
    return VerificationPlan(tuple(c for c in commands if c not in moved_authored),
                            tuple(reused), tuple(affected), tuple(fallback), tuple(affected_requirements))
