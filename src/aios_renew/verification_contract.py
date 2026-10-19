"""One deterministic minimum-sufficient verification contract.

V1 is the explicit policy required for new TASK identities and revisions.
Historical V1/V2 declarations remain readable; already admitted V2 work still
derives executions, exact covered probes and reuse from bounded canonical
material inside its authored envelope. This module executes nothing and owns
no semantic or lifecycle decisions. Commands outside the deliberately small
coverage grammar remain opaque.
"""

from __future__ import annotations

import re
import shlex
import hashlib
import json
import importlib.metadata
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Sequence

from .proof_coverage_contract import CoverageDeclaration


MINIMUM_SUFFICIENT_V1 = "minimum-sufficient-v1"
MINIMUM_SUFFICIENT_V2 = "minimum-sufficient-v2"
VERIFICATION_POLICIES = frozenset({MINIMUM_SUFFICIENT_V1, MINIMUM_SUFFICIENT_V2})
# Prospective authoring gate only. Historical decoding and admitted execution
# select the exact policy on the TASK, never this current-authoring constant.
CURRENT_VERIFICATION_POLICY = MINIMUM_SUFFICIENT_V1
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
    return (pytest_execution_profile(value["profile"]), value["toolchain"])


def pytest_execution_profile(profile: dict) -> dict:
    """Compare a non-executing collection with its explicitly bound execution."""
    result = {k: v for k, v in profile.items() if k != "collect_only"}
    if result.get("execution_contract") == "fixed-xdist-direct-git-v1" and profile.get("collect_only"):
        result["workers"] = result.pop("collected_for_workers")
        result["distribution"] = result.pop("collected_for_distribution")
    return result


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


def validate_pytest_execution_binding(proof: object, *, profile: dict, toolchain: dict,
                                     reports: list, subject_sha: str | None = None) -> None:
    """Validate prospective process proof without changing historical decoding."""
    if not isinstance(proof, dict) or proof.get("execution_contract") != "fixed-xdist-direct-git-v1":
        raise VerificationContractError("missing actual pytest execution proof")
    if subject_sha is not None and proof.get("subject_sha") != subject_sha:
        raise VerificationContractError("pytest execution subject mismatch")
    identity = pytest_collection_identity(proof.get("controller_collection"))
    workers, collections = proof.get("workers"), proof.get("worker_collections")
    expected = 0 if profile["collect_only"] else profile["workers"]
    if (not isinstance(workers, dict) or not isinstance(collections, dict)
            or len(workers) != expected or set(workers) != set(collections)):
        raise VerificationContractError("missing or unexpected actual xdist workers")
    paths, processes = [], set()
    path_type = PureWindowsPath if toolchain.get("platform_system") == "Windows" else PurePosixPath
    description = {key: profile[key] for key in ("profile", "workers", "distribution", "max_worker_restart", "collect_only")}
    for worker_id, worker in workers.items():
        if (not isinstance(worker_id, str) or not worker_id
                or not isinstance(worker, dict) or worker.get("worker_id") != worker_id
                or worker.get("profile") != description or worker.get("toolchain") != toolchain
                or pytest_collection_identity(worker.get("collection")) != identity
                or pytest_collection_identity(collections[worker_id]) != identity):
            raise VerificationContractError("conflicting xdist worker collection/profile/toolchain")
        pid = worker.get("process_id")
        if type(pid) is not int or pid <= 0 or pid in processes:
            raise VerificationContractError("shared or invalid xdist process identity")
        processes.add(pid)
        roots = []
        for name in ("temporary_root", "git_fixture_cache_root"):
            value = worker.get(name)
            if (not isinstance(value, str) or not value or not path_type(value).is_absolute()
                    or ".." in path_type(value).parts):
                raise VerificationContractError("missing absolute xdist isolation root")
            roots.append(path_type(value))
        for previous in paths:
            if any(a.is_relative_to(b) or b.is_relative_to(a) for a in previous for b in roots):
                raise VerificationContractError("xdist workers share writable roots")
        paths.append(roots)
    if profile["collect_only"]:
        if reports:
            raise VerificationContractError("collection-only observation executed tests")
        return
    phases = {}
    for report in reports:
        phases.setdefault(report["nodeid"], {})[report["phase"]] = report["outcome"]
    if pytest_collection_identity(list(phases)) != identity:
        raise VerificationContractError("incomplete actual pytest report population")
    for observed in phases.values():
        if ("setup" not in observed or "teardown" not in observed or "collect" in observed
                or observed["setup"] == "PASS" and "call" not in observed):
            raise VerificationContractError("incomplete actual pytest phase population")


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
    if value["profile"].get("execution_contract") == "fixed-xdist-direct-git-v1":
        profile = value["profile"]
        if (type(profile.get("workers")) is not int or profile["workers"] < 0
                or profile.get("distribution") != ("load" if profile["workers"] else "no")
                or type(profile.get("max_worker_restart")) is not int or profile["max_worker_restart"] != 0
                or type(profile.get("collect_only")) is not bool
                or not profile.get("observer_blob")
                or not isinstance(profile.get("repository_tool_blobs"), dict)
                or any(not v for v in profile["repository_tool_blobs"].values())):
            raise VerificationContractError("incomplete actual pytest profile binding")
        if profile["collect_only"] and (profile["workers"] != 0
                or type(profile.get("collected_for_workers")) is not int
                or profile["collected_for_workers"] < 0
                or profile.get("collected_for_distribution") != ("load" if profile["collected_for_workers"] else "no")):
            raise VerificationContractError("invalid non-executing collection binding")
        for key in ("git_executable", "git_version", "git_exec_path", "installed_distributions_digest", "git_executable_sha256"):
            if not isinstance(value["toolchain"].get(key), str) or not value["toolchain"][key]:
                raise VerificationContractError("missing direct Git/toolchain provenance")
        for key in ("git_executable_sha256", "installed_distributions_digest"):
            if re.fullmatch(r"[0-9a-f]{64}", value["toolchain"][key]) is None:
                raise VerificationContractError("invalid Git/toolchain digest")
        if value["toolchain"].get("platform_system") == "Windows" and (
                value["toolchain"].get("git_selection") != "installed-git-for-windows-direct-v1"
                or not PureWindowsPath(value["toolchain"]["git_executable"]).is_absolute()
                or PureWindowsPath(value["toolchain"]["git_executable"]).name.lower() != "git.exe"):
            raise VerificationContractError("missing direct installed Windows Git selection")
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
    if value["profile"].get("execution_contract") == "fixed-xdist-direct-git-v1":
        validate_pytest_execution_binding(value.get("execution"), profile=value["profile"],
                                         toolchain=value["toolchain"], reports=reports, subject_sha=value["subject_sha"])
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


# L2-01 is an explicit observation projection, never an EVIDENCE/lifecycle type.
ORIGINAL_PROOF_FORMAT = "AIOS_L2_ORIGINAL_PROOF"
ORIGINAL_PROOF_VERSION = 1
MAX_ORIGINAL_PROOF_BYTES = 2 * 1024 * 1024
MAX_ORIGINAL_RAW_BYTES = 16 * 1024 * 1024
ORIGINAL_PROOF_DIMENSIONS = (
    "verification_policy", "execution_profile", "toolchain", "interpreter",
    "environment_digest", "environment_content", "configuration", "observer",
    "worker_topology", "worker_state", "collection", "population", "phase_outcomes",
    "completeness", "stability", "test_dependencies", "fixtures_helpers_shared_state",
    "concurrency_interference", "ordering", "repetition", "integration_external_boundaries",
    "evidence_kind_schema", "prior_raw_digest", "condition_diagnostics",
)
_ORIGINAL_DIMENSION_SOURCES = {
    "verification_policy": "TASK.verification.policy",
    "execution_profile": "EVIDENCE.verification.candidate.profile",
    "toolchain": ("EVIDENCE.verification.candidate.toolchain", "EVIDENCE.verification.candidate.observed_toolchain"),
    "interpreter": ("EVIDENCE.verification.candidate.toolchain", "EVIDENCE.verification.candidate.observed_toolchain"),
    "environment_digest": "EVIDENCE.verification.candidate.profile.environment_digest",
    "configuration": "EVIDENCE.verification.candidate.profile.repository_tool_blobs",
    "observer": "EVIDENCE.verification.candidate.profile.observer_blob",
    "worker_topology": "EVIDENCE.verification.candidate.profile",
    "worker_state": "EVIDENCE.verification.candidate.execution",
    "collection": "EVIDENCE.verification.candidate.collection",
    "phase_outcomes": "EVIDENCE.verification.candidate.reports",
    "population": "EVIDENCE.verification.candidate.reports.nodeid",
    "completeness": "EVIDENCE.verification.candidate.complete",
    "stability": "EVIDENCE.verification.candidate.unstable",
    "prior_raw_digest": "EVIDENCE.verification.raw_digest",
    "evidence_kind_schema": "canonical RESULT.evidence",
    "condition_diagnostics": "EVIDENCE.verification.candidate",
}


def _original_text(value: object, limit: int = 8192) -> str:
    if (type(value) is not str or not value or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise VerificationContractError("invalid bounded original-proof text")
    value.encode("utf-8", errors="strict")
    return value


def _original_hash(value: object, length: int = 64) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{%d}" % length, value) is None:
        raise VerificationContractError("invalid original-proof digest/identity")
    return value


def _original_json(value: object) -> str:
    """One bounded, strict, independently reproducible encoding."""
    nodes, characters = 0, 0
    def bound(item, depth=0):
        nonlocal nodes, characters
        nodes += 1
        characters += len(item) if type(item) is str else 0
        if nodes > 200000 or characters > MAX_ORIGINAL_PROOF_BYTES:
            raise VerificationContractError("original-proof content exceeds bound")
        if depth > 24:
            raise VerificationContractError("original-proof nesting exceeds bound")
        if item is None or type(item) is bool:
            return
        if type(item) is int and -(2**63) <= item < 2**63:
            return
        if type(item) is str and len(item) <= 65536:
            return
        if type(item) is list and len(item) <= 100000:
            for child in item:
                bound(child, depth + 1)
            return
        if type(item) is dict and len(item) <= 100000:
            for key, child in item.items():
                _original_text(key, 256)
                bound(child, depth + 1)
            return
        raise VerificationContractError("invalid original-proof JSON type/size")
    bound(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False)
    if len(encoded) > MAX_ORIGINAL_PROOF_BYTES:
        raise VerificationContractError("original-proof byte bound exceeded")
    return encoded


def _original_decode(content: bytes) -> object:
    if type(content) is not bytes or len(content) > MAX_ORIGINAL_PROOF_BYTES:
        raise VerificationContractError("original-proof input byte bound exceeded")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise VerificationContractError("duplicate original-proof key")
            result[key] = value
        return result
    try:
        value = json.loads(content.decode("utf-8", errors="strict"), object_pairs_hook=pairs)
        _original_json(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise VerificationContractError("invalid original-proof JSON") from exc


@dataclass(frozen=True, slots=True)
class OriginalEvidenceRequest:
    """Expected pins are locators, authenticated independently by Runtime."""

    task_id: str
    task_revision: int
    run_id: str
    candidate_sha: str
    tree_sha: str
    artifacts_sha: str
    evidence_id: str
    subject_sha: str
    command: str
    exit_code: int

    def __post_init__(self):
        _original_text(self.task_id, 80)
        if re.fullmatch(r"TASK-[A-Za-z0-9_-]+", self.task_id) is None:
            raise VerificationContractError("invalid original TASK identity")
        prefix = "RUN-" + self.task_id[5:] + "-"
        if (type(self.run_id) is not str or len(self.run_id) > 88
                or re.fullmatch(re.escape(prefix) + r"\d{3,}", self.run_id) is None
                or type(self.evidence_id) is not str
                or re.fullmatch(re.escape(self.run_id) + r"-V\d{3,}", self.evidence_id) is None
                or len(self.evidence_id) > 96):
            raise VerificationContractError("invalid original RUN/EVIDENCE identity")
        if type(self.task_revision) is not int or not 1 <= self.task_revision <= 1000000:
            raise VerificationContractError("invalid original TASK revision")
        for value in (self.candidate_sha, self.tree_sha, self.artifacts_sha, self.subject_sha):
            _original_hash(value, 40)
        if self.subject_sha != self.candidate_sha:
            raise VerificationContractError("original evidence must retain its source candidate")
        _original_text(self.command)
        if type(self.exit_code) is not int or not -(2**31) <= self.exit_code < 2**31:
            raise VerificationContractError("invalid original exit status")


@dataclass(frozen=True, slots=True)
class OriginalProofDimension:
    """Immutable canonical JSON retains recorded content without mutable aliases."""

    name: str
    status: str
    value_json: str | None
    source: str
    reason: str

    def __post_init__(self):
        if (type(self.name) is not str or self.name not in ORIGINAL_PROOF_DIMENSIONS
                or type(self.status) is not str or self.status not in {"OBSERVED", "UNKNOWN"}):
            raise VerificationContractError("invalid original-proof dimension")
        _original_text(self.source, 256)
        _original_text(self.reason, 256)
        if self.status == "UNKNOWN":
            if self.value_json is not None or self.reason != "NOT_OBSERVED" or self.source != "SOURCE_RECORDS":
                raise VerificationContractError("unknown dimension cannot contain an assertion")
        elif (self.source not in ((_ORIGINAL_DIMENSION_SOURCES.get(self.name),)
                                 if type(_ORIGINAL_DIMENSION_SOURCES.get(self.name)) is str
                                 else _ORIGINAL_DIMENSION_SOURCES.get(self.name, ()))
              or type(self.value_json) is not str or self.reason != "RECORDED_SOURCE_CONTENT"
              or _original_json(_original_decode(self.value_json.encode("utf-8"))) != self.value_json):
            raise VerificationContractError("noncanonical observed dimension")
        if self.status == "UNKNOWN":
            return
        value = _original_decode(self.value_json.encode("ascii"))
        if value is None:
            raise VerificationContractError("observed dimension requires recorded content")
        if self.name in {"environment_digest", "prior_raw_digest", "observer"}:
            _original_hash(value, 40 if self.name == "observer" else 64)
        if self.name in {"completeness", "stability"} and type(value) is not bool:
            raise VerificationContractError("invalid original completeness/stability")
        if self.name == "verification_policy" and value not in ("minimum-sufficient-v1", "minimum-sufficient-v2"):
            raise VerificationContractError("invalid original verification policy")
        if self.name in {"execution_profile", "toolchain", "interpreter", "configuration", "worker_topology",
                         "worker_state", "collection", "condition_diagnostics", "evidence_kind_schema"}:
            if type(value) is not dict or not value:
                raise VerificationContractError("invalid recorded original context")
        if self.name == "interpreter":
            if set(value) != {"python_implementation", "python_version", "python_executable"}:
                raise VerificationContractError("partial original interpreter")
            for field in value.values():
                _original_text(field)
        if self.name == "configuration":
            for path, sha in value.items():
                if (PurePosixPath(path).is_absolute() or ".." in path.split("/")
                        or "\\" in path or ":" in path):
                    raise VerificationContractError("unsafe original configuration identity")
                if sha is not None:
                    _original_hash(sha, 40)
        if self.name == "worker_topology":
            if (set(value) != {"workers", "distribution", "max_worker_restart", "collect_only"}
                    or type(value["workers"]) is not int or not 0 <= value["workers"] <= 1024
                    or value["distribution"] != ("load" if value["workers"] else "no")
                    or type(value["max_worker_restart"]) is not int or value["max_worker_restart"] != 0
                    or type(value["collect_only"]) is not bool):
                raise VerificationContractError("partial or conflicting original worker topology")
        if self.name in {"population", "phase_outcomes"}:
            if type(value) is not list:
                raise VerificationContractError("invalid original proof population")
            if self.name == "population":
                for node in value:
                    _original_text(node, 16384)
                if value != sorted(set(value)):
                    raise VerificationContractError("unordered original proof population")


@dataclass(frozen=True, slots=True)
class OriginalProofProvenance:
    """Authenticated original source snapshot; contains no target or reuse state."""

    version: int
    identity: OriginalEvidenceRequest
    base_sha: str
    task_blob_sha: str
    task_sha256: str
    run_sha256: str
    local_run_sha256: str
    result_sha256: str
    local_result_sha256: str
    evidence_sha256: str
    raw_sha256: str
    raw_size: int
    outcome: str
    source_refs: tuple[tuple[str, str], ...]
    dimensions: tuple[OriginalProofDimension, ...]
    upstream_sha256: str

    def __post_init__(self):
        if type(self.version) is not int or self.version != ORIGINAL_PROOF_VERSION:
            raise VerificationContractError("unsupported original-proof version")
        if type(self.identity) is not OriginalEvidenceRequest:
            raise VerificationContractError("missing typed original source")
        self.identity.__post_init__()
        for name in ("base_sha", "task_blob_sha"):
            _original_hash(getattr(self, name), 40)
        for name in ("task_sha256", "run_sha256", "local_run_sha256", "result_sha256",
                     "local_result_sha256", "evidence_sha256", "raw_sha256", "upstream_sha256"):
            _original_hash(getattr(self, name))
        if type(self.raw_size) is not int or not 0 < self.raw_size <= MAX_ORIGINAL_RAW_BYTES:
            raise VerificationContractError("invalid original raw size")
        code = self.identity.exit_code
        expected = "FAIL" if code == 1 else "ERROR" if code != 0 else "PASS"
        if type(self.outcome) is not str or self.outcome not in {expected, "UNKNOWN"} or code != 0 and self.outcome != expected:
            raise VerificationContractError("original outcome promotion or relabeling")
        prefix = "refs/heads/aios/"
        expected_refs = tuple(sorted(((prefix + "artifacts/" + self.identity.run_id, self.identity.artifacts_sha),
                                      (prefix + "review/" + self.identity.run_id, self.identity.candidate_sha))))
        if type(self.source_refs) is not tuple or self.source_refs != expected_refs:
            raise VerificationContractError("invalid exact original source refs")
        if (type(self.dimensions) is not tuple or any(type(d) is not OriginalProofDimension for d in self.dimensions)
                or tuple(d.name for d in self.dimensions) != ORIGINAL_PROOF_DIMENSIONS):
            raise VerificationContractError("missing, reordered or extra original dimensions")
        for dimension in self.dimensions:
            dimension.__post_init__()
        if sum(len(d.value_json or "") for d in self.dimensions) + len(self.identity.command) + 16384 > MAX_ORIGINAL_PROOF_BYTES:
            raise VerificationContractError("original-proof dimensions exceed total bound")
        dimensions = {d.name: d for d in self.dimensions}
        prior = dimensions["prior_raw_digest"]
        if prior.status == "OBSERVED" and _original_decode(prior.value_json.encode("ascii")) != self.raw_sha256:
            raise VerificationContractError("original raw digest conflict")
        schema = dimensions["evidence_kind_schema"]
        if schema.status != "OBSERVED":
            raise VerificationContractError("original evidence schema must be observed")
        shape = _original_decode(schema.value_json.encode("ascii"))
        if (type(shape) is not dict or set(shape) != {"type", "shape", "keys"}
                or shape["type"] != "VERIFICATION" or type(shape["shape"]) is not str or shape["shape"] not in {"V1", "V2"}
                or type(shape["keys"]) is not list):
            raise VerificationContractError("invalid original evidence shape")
        keys = {"evidence_id", "run_id", "subject_sha", "type", "source", "result", "raw"}
        if shape["keys"] != sorted(keys | ({"verification"} if shape["shape"] == "V2" else set())):
            raise VerificationContractError("conflicting original evidence schema")
        if self.outcome == "PASS" and shape["shape"] == "V2":
            if dimensions["completeness"].value_json != "true" or dimensions["stability"].value_json != "false":
                raise VerificationContractError("incomplete original observation cannot become PASS")


def original_proof_data(value: OriginalProofProvenance) -> dict:
    """Detached consumer projection. Parsing it does not authenticate authority."""
    if type(value) is not OriginalProofProvenance:
        raise VerificationContractError("typed original-proof record required")
    value.__post_init__()
    data = {key: getattr(value, key) for key in (
        "version", "base_sha", "task_blob_sha", "task_sha256", "run_sha256", "local_run_sha256",
        "result_sha256", "local_result_sha256", "evidence_sha256", "raw_sha256", "raw_size", "outcome", "upstream_sha256")}
    data.update(format=ORIGINAL_PROOF_FORMAT,
                identity={k: getattr(value.identity, k) for k in OriginalEvidenceRequest.__dataclass_fields__},
                source_refs=[dict(ref=ref, sha=sha) for ref, sha in value.source_refs],
                dimensions=[dict(name=d.name, status=d.status,
                                 value=None if d.value_json is None else _original_decode(d.value_json.encode("utf-8")),
                                 source=d.source, reason=d.reason) for d in value.dimensions])
    return data


def format_original_proof(value: OriginalProofProvenance) -> bytes:
    """Explicit pure formatter, no observation, execution or persistence."""
    return _original_json(original_proof_data(value)).encode("ascii")


def parse_original_proof(content: bytes) -> OriginalProofProvenance:
    """Strict structural decoding only; cannot grant source authentication."""
    data = _original_decode(content)
    fields = set(OriginalProofProvenance.__dataclass_fields__) | {"format"}
    if type(data) is not dict or set(data) != fields or data["format"] != ORIGINAL_PROOF_FORMAT:
        raise VerificationContractError("invalid original-proof schema")
    identity = data["identity"]
    if type(identity) is not dict or set(identity) != set(OriginalEvidenceRequest.__dataclass_fields__):
        raise VerificationContractError("invalid original source schema")
    if type(data["dimensions"]) is not list or len(data["dimensions"]) != len(ORIGINAL_PROOF_DIMENSIONS):
        raise VerificationContractError("invalid original dimension schema")
    dimensions = []
    for item in data["dimensions"]:
        if type(item) is not dict or set(item) != {"name", "status", "value", "source", "reason"}:
            raise VerificationContractError("invalid original dimension schema")
        dimensions.append(OriginalProofDimension(item["name"], item["status"],
            None if item["value"] is None else _original_json(item["value"]), item["source"], item["reason"]))
    refs = data["source_refs"]
    if (type(refs) is not list or len(refs) != 2
            or any(type(r) is not dict or set(r) != {"ref", "sha"} for r in refs)):
        raise VerificationContractError("invalid original refs schema")
    values = {k: v for k, v in data.items() if k not in {"format", "identity", "dimensions", "source_refs"}}
    return OriginalProofProvenance(**values, identity=OriginalEvidenceRequest(**identity),
        dimensions=tuple(dimensions), source_refs=tuple((r["ref"], r["sha"]) for r in refs))


# L2-02 is an explicit, non-operative projection. None of these structural
# types, including a parsed VALID witness, authenticates a Runtime observation.
APPLICABILITY_VERSION = 1
APPLICABILITY_REQUEST_FORMAT = "AIOS_L2_APPLICABILITY_REQUEST"
APPLICABILITY_FORMAT = "AIOS_L2_APPLICABILITY"
APPLICABILITY_CONDITIONS_FORMAT = "AIOS_L2_REVIEWED_CONDITIONS"
APPLICABILITY_DIMENSIONS = (
    "implementation_dependencies", "test_dependencies", "population",
    "fixtures_setup_teardown", "helpers", "shared_state", "generated_state",
    "interpreter", "toolchain", "observer", "environment_digest",
    "environment_content", "configuration", "verification_policy",
    "execution_profile", "worker_topology", "worker_state",
    "concurrency_interference", "ordering", "repetition", "collection",
    "completeness", "stability", "phase_outcomes",
    "integration_external_boundaries", "evidence_kind_schema", "condition_diagnostics",
)
APPLICABILITY_GIT_DIMENSIONS = frozenset((
    "implementation_dependencies", "test_dependencies", "fixtures_setup_teardown",
    "helpers", "shared_state", "generated_state",
))
MAX_APPLICABILITY_PATHS = 64


def _app_id(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", value) is None:
        raise VerificationContractError("invalid applicability semantic identity")
    return value


def _app_revision(value: object) -> None:
    if type(value) is not int or not 1 <= value <= 1_000_000:
        raise VerificationContractError("invalid applicability revision")


def _app_ids(value: object) -> None:
    if type(value) is not tuple or not 0 < len(value) <= 256:
        raise VerificationContractError("bounded immutable applicability acceptance required")
    for item in value:
        _app_id(item)
    if value != tuple(sorted(set(value))):
        raise VerificationContractError("unordered or duplicate applicability acceptance")


def _app_path(value: object) -> str:
    if (type(value) is not str or len(value) > 512 or len(value.split("/")) > 32
            or re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value) is None
            or any(p in {".", "..", ".git"} for p in value.split("/"))):
        raise VerificationContractError("unsafe applicability Git path")
    return value


def _app_exact(value: object, fields) -> dict:
    if type(value) is not dict or set(value) != set(fields):
        raise VerificationContractError("missing or unknown applicability fields")
    return value


@dataclass(frozen=True, slots=True)
class ApplicabilityDeclarationPin:
    id: str
    revision: int
    digest: str

    def __post_init__(self):
        _app_id(self.id)
        _app_revision(self.revision)
        _original_hash(self.digest)


@dataclass(frozen=True, slots=True)
class ApplicabilityTargetRequest:
    """Locates one existing Runtime admission; no caller condition assertions."""

    task_id: str
    task_revision: int
    run_id: str
    base_sha: str
    candidate_sha: str
    tree_sha: str
    artifacts_sha: str
    run_sha256: str
    condition_evidence: OriginalEvidenceRequest | None = None

    def __post_init__(self):
        if (type(self.task_id) is not str or len(self.task_id) > 80
                or re.fullmatch(r"TASK-[A-Za-z0-9_-]+", self.task_id) is None
                or type(self.run_id) is not str or len(self.run_id) > 88
                or re.fullmatch(re.escape("RUN-" + self.task_id[5:] + "-") + r"\d{3,}", self.run_id) is None):
            raise VerificationContractError("invalid exact applicability target TASK/RUN")
        _app_revision(self.task_revision)
        for sha in (self.base_sha, self.candidate_sha, self.tree_sha, self.artifacts_sha):
            _original_hash(sha, 40)
        _original_hash(self.run_sha256)
        if self.condition_evidence is not None:
            if type(self.condition_evidence) is not OriginalEvidenceRequest:
                raise VerificationContractError("typed target condition locator required")
            item = self.condition_evidence
            item.__post_init__()
            if (item.task_id, item.task_revision, item.run_id, item.candidate_sha, item.tree_sha, item.artifacts_sha) != (
                    self.task_id, self.task_revision, self.run_id, self.candidate_sha, self.tree_sha, self.artifacts_sha):
                raise VerificationContractError("target condition observation substitution")


@dataclass(frozen=True, slots=True)
class ProofApplicabilityRequest:
    version: int
    source: OriginalEvidenceRequest
    target: ApplicabilityTargetRequest
    expected_main_sha: str
    contract: ApplicabilityDeclarationPin
    mapping: ApplicabilityDeclarationPin
    obligation_id: str
    obligation_revision: int
    obligation_digest: str
    acceptance_ids: tuple[str, ...]

    def __post_init__(self):
        if type(self.version) is not int or self.version != APPLICABILITY_VERSION:
            raise VerificationContractError("unsupported applicability version")
        for value, cls in ((self.source, OriginalEvidenceRequest), (self.target, ApplicabilityTargetRequest),
                           (self.contract, ApplicabilityDeclarationPin), (self.mapping, ApplicabilityDeclarationPin)):
            if type(value) is not cls:
                raise VerificationContractError("exact typed applicability request required")
            value.__post_init__()
        _original_hash(self.expected_main_sha, 40)
        _app_id(self.obligation_id)
        _app_revision(self.obligation_revision)
        _original_hash(self.obligation_digest)
        _app_ids(self.acceptance_ids)


def _app_fields(value) -> dict:
    return {k: getattr(value, k) for k in value.__dataclass_fields__}


def applicability_target_data(value: ApplicabilityTargetRequest) -> dict:
    if type(value) is not ApplicabilityTargetRequest:
        raise VerificationContractError("typed applicability target required")
    value.__post_init__()
    return {**_app_fields(value), "condition_evidence": (
        None if value.condition_evidence is None else _app_fields(value.condition_evidence))}


def applicability_request_data(value: ProofApplicabilityRequest) -> dict:
    if type(value) is not ProofApplicabilityRequest:
        raise VerificationContractError("typed applicability request required")
    value.__post_init__()
    return {**_app_fields(value), "format": APPLICABILITY_REQUEST_FORMAT,
            "source": _app_fields(value.source), "target": applicability_target_data(value.target),
            "contract": _app_fields(value.contract), "mapping": _app_fields(value.mapping),
            "acceptance_ids": list(value.acceptance_ids)}


def format_applicability_request(value: ProofApplicabilityRequest) -> bytes:
    return _original_json(applicability_request_data(value)).encode("ascii")


def _app_target(value: object) -> ApplicabilityTargetRequest:
    item = dict(_app_exact(value, ApplicabilityTargetRequest.__dataclass_fields__))
    if item["condition_evidence"] is not None:
        item["condition_evidence"] = OriginalEvidenceRequest(**_app_exact(
            item["condition_evidence"], OriginalEvidenceRequest.__dataclass_fields__))
    return ApplicabilityTargetRequest(**item)


def parse_applicability_request(content: bytes) -> ProofApplicabilityRequest:
    data = dict(_app_exact(_original_decode(content), (*ProofApplicabilityRequest.__dataclass_fields__, "format")))
    if data.pop("format") != APPLICABILITY_REQUEST_FORMAT or type(data["acceptance_ids"]) is not list:
        raise VerificationContractError("invalid applicability request schema")
    data["source"] = OriginalEvidenceRequest(**_app_exact(data["source"], OriginalEvidenceRequest.__dataclass_fields__))
    data["target"] = _app_target(data["target"])
    for key in ("contract", "mapping"):
        data[key] = ApplicabilityDeclarationPin(**_app_exact(data[key], ApplicabilityDeclarationPin.__dataclass_fields__))
    data["acceptance_ids"] = tuple(data["acceptance_ids"])
    return ProofApplicabilityRequest(**data)


@dataclass(frozen=True, slots=True)
class ApplicabilityCondition:
    status: str
    origin: str
    identity_sha256: str | None
    value_json: str | None

    def __post_init__(self):
        if type(self.status) is not str or self.status not in {"OBSERVED", "UNKNOWN", "INAPPLICABLE"}:
            raise VerificationContractError("invalid applicability condition status")
        if type(self.origin) is not str or self.origin not in {"L2_ORIGINAL", "GIT_FOOTPRINT", "REVIEWED_SEMANTICS", "UNAVAILABLE"}:
            raise VerificationContractError("invalid applicability condition origin")
        if self.status == "UNKNOWN":
            if self.origin != "UNAVAILABLE" or self.identity_sha256 is not None or self.value_json is not None:
                raise VerificationContractError("UNKNOWN applicability condition contains assertions")
        else:
            _original_hash(self.identity_sha256)
            if self.status == "INAPPLICABLE":
                if self.origin != "REVIEWED_SEMANTICS" or self.value_json is not None:
                    raise VerificationContractError("invalid reviewed inapplicability")
            elif (self.origin not in {"L2_ORIGINAL", "GIT_FOOTPRINT"} or type(self.value_json) is not str
                  or _original_decode(self.value_json.encode("utf-8")) is None
                  or _original_json(_original_decode(self.value_json.encode("utf-8"))) != self.value_json):
                raise VerificationContractError("invalid observed applicability condition")


@dataclass(frozen=True, slots=True)
class ApplicabilityDimension:
    name: str
    status: str
    source: ApplicabilityCondition
    target: ApplicabilityCondition
    reason: str
    authority_sha256: str | None

    def __post_init__(self):
        if type(self.name) is not str or self.name not in APPLICABILITY_DIMENSIONS:
            raise VerificationContractError("invalid applicability dimension")
        if type(self.status) is not str or self.status not in {"EQUIVALENT", "CHANGED", "UNKNOWN", "INAPPLICABLE"}:
            raise VerificationContractError("invalid applicability judgment")
        _original_text(self.reason, 2048)
        for fact in (self.source, self.target):
            if type(fact) is not ApplicabilityCondition:
                raise VerificationContractError("typed applicability conditions required")
            fact.__post_init__()
        if self.authority_sha256 is not None:
            _original_hash(self.authority_sha256)
        if self.status != "UNKNOWN" and self.authority_sha256 is None:
            raise VerificationContractError("affirmative applicability lacks semantic authority identity")
        if self.status in {"EQUIVALENT", "CHANGED"}:
            if self.source.status != "OBSERVED" or self.target.status != "OBSERVED":
                raise VerificationContractError("applicability comparison requires both observations")
            if (self.source.value_json == self.target.value_json) != (self.status == "EQUIVALENT"):
                raise VerificationContractError("contradictory applicability comparison")
        if self.status == "INAPPLICABLE" and any(f.status != "INAPPLICABLE" for f in (self.source, self.target)):
            raise VerificationContractError("inapplicability requires reviewed source and target coverage")
        if self.status == "UNKNOWN" and self.source.status == self.target.status == "OBSERVED":
            raise VerificationContractError("observed equality/divergence must be explicit")


@dataclass(frozen=True, slots=True)
class ApplicabilityTargetProvenance:
    identity: ApplicabilityTargetRequest
    task_blob_sha: str
    task_sha256: str
    canonical_run_sha256: str
    lineage_sha256: str
    conditions: OriginalProofProvenance | None

    def __post_init__(self):
        if type(self.identity) is not ApplicabilityTargetRequest:
            raise VerificationContractError("typed target provenance required")
        self.identity.__post_init__()
        _original_hash(self.task_blob_sha, 40)
        _original_hash(self.task_sha256)
        _original_hash(self.canonical_run_sha256)
        _original_hash(self.lineage_sha256)
        if self.conditions is not None:
            if type(self.conditions) is not OriginalProofProvenance:
                raise VerificationContractError("typed target original condition observation required")
            self.conditions.__post_init__()
            if (self.conditions.identity != self.identity.condition_evidence
                    or self.conditions.base_sha != self.identity.base_sha
                    or self.conditions.local_run_sha256 != self.identity.run_sha256):
                raise VerificationContractError("conflicting target condition provenance")


@dataclass(frozen=True, slots=True)
class ApplicabilityAuthority:
    """Exact independently observed L1 selection/REVIEW/publication identities."""

    main_sha: str
    selection_sha: str
    review_candidate_sha: str
    artifacts_sha: str
    decision_sha: str
    manifest_blob_sha: str
    manifest_sha256: str
    binding_json: str

    def __post_init__(self):
        for key in ("main_sha", "selection_sha", "review_candidate_sha", "artifacts_sha", "decision_sha", "manifest_blob_sha"):
            _original_hash(getattr(self, key), 40)
        _original_hash(self.manifest_sha256)
        if (type(self.binding_json) is not str
                or _original_json(_original_decode(self.binding_json.encode("utf-8"))) != self.binding_json):
            raise VerificationContractError("noncanonical applicability authority binding")
        binding = _app_exact(_original_decode(self.binding_json.encode("ascii")), (
            "upstream_sha256", "human_event_identity", "review_id", "review_run_id", "run_sha256",
            "result_sha256", "review_sha256", "scope_sha256", "sources", "manifest_path", "review_acceptance_id"))
        for key in ("upstream_sha256", "run_sha256", "result_sha256", "review_sha256", "scope_sha256"):
            _original_hash(binding[key])
        for key in ("human_event_identity", "review_id", "review_run_id", "review_acceptance_id"):
            _original_text(binding[key], 256)
        _app_path(binding["manifest_path"])
        sources = _app_exact(binding["sources"], ("task", "contract", "mapping", "review_task"))
        for source in sources.values():
            _app_exact(source, ("source_sha", "tree_sha", "path", "blob_sha", "sha256"))
            for key in ("source_sha", "tree_sha", "blob_sha"):
                _original_hash(source[key], 40)
            _original_hash(source["sha256"])
            _app_path(source["path"])


def reduce_applicability(*, authenticated: bool, dimensions: tuple[ApplicabilityDimension, ...]) -> str:
    """Pure reduction; its boolean argument is NOT an authentication API."""
    if (type(authenticated) is not bool or type(dimensions) is not tuple
            or len(dimensions) != len(APPLICABILITY_DIMENSIONS)
            or any(type(d) is not ApplicabilityDimension for d in dimensions)
            or tuple(d.name for d in dimensions) != APPLICABILITY_DIMENSIONS):
        raise VerificationContractError("incomplete applicability reduction input")
    for dimension in dimensions:
        dimension.__post_init__()
    if not authenticated:
        return "UNKNOWN"
    if any(d.status == "CHANGED" for d in dimensions):
        return "INVALIDATED"
    return "VALID" if all(d.status in {"EQUIVALENT", "INAPPLICABLE"} for d in dimensions) else "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ApplicabilitySourceSnapshot:
    """Retained original identity/outcome even when the full output is too big."""

    identity: OriginalEvidenceRequest
    provenance_sha256: str
    evidence_sha256: str
    raw_sha256: str
    raw_size: int
    outcome: str

    def __post_init__(self):
        if type(self.identity) is not OriginalEvidenceRequest:
            raise VerificationContractError("typed original source snapshot required")
        self.identity.__post_init__()
        for sha in (self.provenance_sha256, self.evidence_sha256, self.raw_sha256):
            _original_hash(sha)
        if type(self.raw_size) is not int or not 0 < self.raw_size <= MAX_ORIGINAL_RAW_BYTES:
            raise VerificationContractError("invalid source snapshot raw size")
        code = self.identity.exit_code
        expected = "FAIL" if code == 1 else "ERROR" if code != 0 else "PASS"
        if (type(self.outcome) is not str or self.outcome not in {expected, "UNKNOWN"}
                or code != 0 and self.outcome != expected):
            raise VerificationContractError("source snapshot outcome promotion")


def applicability_source_snapshot(proof: OriginalProofProvenance) -> ApplicabilitySourceSnapshot:
    if type(proof) is not OriginalProofProvenance:
        raise VerificationContractError("typed original provenance required")
    return ApplicabilitySourceSnapshot(proof.identity, hashlib.sha256(format_original_proof(proof)).hexdigest(),
        proof.evidence_sha256, proof.raw_sha256, proof.raw_size, proof.outcome)


@dataclass(frozen=True, slots=True)
class ProofApplicabilityWitness:
    version: int
    request: ProofApplicabilityRequest
    authentication: str
    reason: str
    source: OriginalProofProvenance | None
    target: ApplicabilityTargetProvenance | None
    authority: ApplicabilityAuthority | None
    dimensions: tuple[ApplicabilityDimension, ...]
    source_snapshot: ApplicabilitySourceSnapshot | None

    def __post_init__(self):
        if type(self.version) is not int or self.version != APPLICABILITY_VERSION or type(self.request) is not ProofApplicabilityRequest:
            raise VerificationContractError("invalid typed applicability witness")
        self.request.__post_init__()
        _original_text(self.reason, 256)
        if type(self.authentication) is not str or self.authentication not in {"AUTHENTICATED", "NOT_ESTABLISHED"}:
            raise VerificationContractError("invalid applicability authentication status")
        for item, cls in ((self.source, OriginalProofProvenance), (self.target, ApplicabilityTargetProvenance),
                          (self.authority, ApplicabilityAuthority), (self.source_snapshot, ApplicabilitySourceSnapshot)):
            if item is not None:
                if type(item) is not cls:
                    raise VerificationContractError("invalid applicability provenance type")
                item.__post_init__()
        if self.source is not None and self.source.identity != self.request.source:
            raise VerificationContractError("applicability source relabeling")
        if self.source is not None and self.source_snapshot != applicability_source_snapshot(self.source):
            raise VerificationContractError("applicability source snapshot substitution")
        if self.source_snapshot is not None and self.source_snapshot.identity != self.request.source:
            raise VerificationContractError("applicability source snapshot relabeling")
        if self.target is not None and self.target.identity != self.request.target:
            raise VerificationContractError("applicability target relabeling")
        if self.authority is not None and self.authority.main_sha != self.request.expected_main_sha:
            raise VerificationContractError("applicability main substitution")
        if self.authentication == "AUTHENTICATED" and any(v is None for v in (self.source, self.target, self.authority)):
            raise VerificationContractError("authenticated applicability requires complete identities")
        reduce_applicability(authenticated=self.authentication == "AUTHENTICATED", dimensions=self.dimensions)
        if self.authentication == "AUTHENTICATED":
            authority_digest = hashlib.sha256(_original_json(_app_fields(self.authority)).encode("ascii")).hexdigest()
            if any(d.authority_sha256 != authority_digest for d in self.dimensions):
                raise VerificationContractError("applicability dimension authority substitution")

    @property
    def validity(self) -> str:
        return reduce_applicability(authenticated=self.authentication == "AUTHENTICATED", dimensions=self.dimensions)

    @property
    def source_outcome(self) -> str:
        return "UNKNOWN" if self.source_snapshot is None else self.source_snapshot.outcome

    @property
    def disposition(self) -> str:
        return "APPLICABILITY_ONLY" if self.validity == "VALID" else "BLOCK"

    @property
    def satisfactory(self) -> bool:
        # L2-03, not a read or formatter, owns any future discharge decision.
        return False


def applicability_data(value: ProofApplicabilityWitness) -> dict:
    if type(value) is not ProofApplicabilityWitness:
        raise VerificationContractError("typed applicability witness required")
    value.__post_init__()
    def fact(item):
        return {**_app_fields(item), "value_json": item.value_json}
    target = None if value.target is None else {
        **_app_fields(value.target), "identity": applicability_target_data(value.target.identity),
        "conditions": None if value.target.conditions is None else original_proof_data(value.target.conditions)}
    authority = None if value.authority is None else _app_fields(value.authority)
    snapshot = None if value.source_snapshot is None else {
        **_app_fields(value.source_snapshot), "identity": _app_fields(value.source_snapshot.identity)}
    return dict(format=APPLICABILITY_FORMAT, version=value.version,
        request=applicability_request_data(value.request), authentication=value.authentication, reason=value.reason,
        source=None if value.source is None else original_proof_data(value.source), target=target, authority=authority,
        dimensions=[{**_app_fields(d), "source": fact(d.source), "target": fact(d.target)} for d in value.dimensions],
        source_snapshot=snapshot,
        validity=value.validity, source_outcome=value.source_outcome, disposition=value.disposition,
        satisfactory=False, target_execution="NOT_ASSERTED", reuse="NOT_AUTHORIZED")


def format_applicability(value: ProofApplicabilityWitness) -> bytes:
    """Pure, bounded, detached; never observes, executes or persists anything."""
    return _original_json(applicability_data(value)).encode("ascii")


def parse_applicability(content: bytes) -> ProofApplicabilityWitness:
    """Closed-schema structural interpretation only, including claimed VALID."""
    computed = ("validity", "source_outcome", "disposition", "satisfactory", "target_execution", "reuse")
    data = dict(_app_exact(_original_decode(content), (*ProofApplicabilityWitness.__dataclass_fields__, "format", *computed)))
    if data.pop("format") != APPLICABILITY_FORMAT:
        raise VerificationContractError("invalid applicability format")
    stored = {k: data.pop(k) for k in computed}
    data["request"] = parse_applicability_request(_original_json(data["request"]).encode("ascii"))
    if data["source"] is not None:
        data["source"] = parse_original_proof(_original_json(data["source"]).encode("ascii"))
    if data["source_snapshot"] is not None:
        snapshot = dict(_app_exact(data["source_snapshot"], ApplicabilitySourceSnapshot.__dataclass_fields__))
        snapshot["identity"] = OriginalEvidenceRequest(**_app_exact(snapshot["identity"], OriginalEvidenceRequest.__dataclass_fields__))
        data["source_snapshot"] = ApplicabilitySourceSnapshot(**snapshot)
    if data["target"] is not None:
        target = dict(_app_exact(data["target"], ApplicabilityTargetProvenance.__dataclass_fields__))
        target["identity"] = _app_target(target["identity"])
        if target["conditions"] is not None:
            target["conditions"] = parse_original_proof(_original_json(target["conditions"]).encode("ascii"))
        data["target"] = ApplicabilityTargetProvenance(**target)
    if data["authority"] is not None:
        data["authority"] = ApplicabilityAuthority(**_app_exact(data["authority"], ApplicabilityAuthority.__dataclass_fields__))
    items = data["dimensions"]
    if type(items) is not list or len(items) != len(APPLICABILITY_DIMENSIONS):
        raise VerificationContractError("invalid applicability dimension population")
    dimensions = []
    for value in items:
        value = dict(_app_exact(value, ApplicabilityDimension.__dataclass_fields__))
        for key in ("source", "target"):
            value[key] = ApplicabilityCondition(**_app_exact(value[key], ApplicabilityCondition.__dataclass_fields__))
        dimensions.append(ApplicabilityDimension(**value))
    data["dimensions"] = tuple(dimensions)
    witness = ProofApplicabilityWitness(**data)
    expected = dict(validity=witness.validity, source_outcome=witness.source_outcome, disposition=witness.disposition,
                    satisfactory=False, target_execution="NOT_ASSERTED", reuse="NOT_AUTHORIZED")
    if _original_json(stored) != _original_json(expected):
        raise VerificationContractError("applicability outcome or authority promotion")
    return witness


# L2-03 is a nondefault observation, not a proof discharge or execution policy.
REUSE_VERSION = 1
REUSE_REQUEST_FORMAT = "AIOS_L2_REUSE_REQUEST"
REUSE_FORMAT = "AIOS_L2_REUSE"


@dataclass(frozen=True, slots=True)
class ReuseCorrectionLocator:
    """Expected correction pins only; no field grants correction authority.

    Native correction admissions are not supported by the L2-02 read adapter.
    Keeping exact locators lets this boundary reject rather than reinterpret
    correction requests. Missing locators also produce NOT_ESTABLISHED.
    """

    predecessor_run_id: str
    predecessor_subject_sha: str
    predecessor_artifacts_sha: str
    terminal_sha: str
    authorization_sha: str
    action: str
    scope_sha256: str
    original_verification_sha256: str
    affected_verification_sha256: str
    finding_ids: tuple[str, ...]
    delta_review_sha: str | None

    def __post_init__(self):
        if (type(self.predecessor_run_id) is not str or len(self.predecessor_run_id) > 88
                or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", self.predecessor_run_id) is None):
            raise VerificationContractError("invalid reuse predecessor RUN")
        for sha in (self.predecessor_subject_sha, self.predecessor_artifacts_sha,
                    self.terminal_sha, self.authorization_sha):
            _original_hash(sha, 40)
        for sha in (self.scope_sha256, self.original_verification_sha256, self.affected_verification_sha256):
            _original_hash(sha)
        _app_id(self.action)
        if self.finding_ids:
            _app_ids(self.finding_ids)
        elif type(self.finding_ids) is not tuple:
            raise VerificationContractError("immutable reuse finding identities required")
        if self.delta_review_sha is not None:
            _original_hash(self.delta_review_sha, 40)


@dataclass(frozen=True, slots=True)
class ProofReuseRequest:
    version: int
    applicability: ProofApplicabilityRequest
    original: ApplicabilitySourceSnapshot
    proof_id: str
    proof_sha256: str
    operation: str
    correction: ReuseCorrectionLocator | None = None

    def __post_init__(self):
        if type(self.version) is not int or self.version != REUSE_VERSION:
            raise VerificationContractError("unsupported reuse version")
        for item, cls in ((self.applicability, ProofApplicabilityRequest), (self.original, ApplicabilitySourceSnapshot)):
            if type(item) is not cls:
                raise VerificationContractError("exact typed reuse locators required")
            item.__post_init__()
        if self.original.identity != self.applicability.source:
            raise VerificationContractError("reuse original source substitution")
        _app_id(self.proof_id)
        _original_hash(self.proof_sha256)
        if type(self.operation) is not str or self.operation not in {"PRIMARY", "REPAIR", "REMEDIATION"}:
            raise VerificationContractError("invalid reuse operation")
        if self.correction is not None:
            if type(self.correction) is not ReuseCorrectionLocator or self.operation == "PRIMARY":
                raise VerificationContractError("reuse correction action switching")
            self.correction.__post_init__()


def _reuse_snapshot_data(value):
    return None if value is None else {**_app_fields(value), "identity": _app_fields(value.identity)}


def _reuse_snapshot(value):
    item = dict(_app_exact(value, ApplicabilitySourceSnapshot.__dataclass_fields__))
    item["identity"] = OriginalEvidenceRequest(**_app_exact(item["identity"], OriginalEvidenceRequest.__dataclass_fields__))
    return ApplicabilitySourceSnapshot(**item)


def reuse_request_data(value: ProofReuseRequest) -> dict:
    if type(value) is not ProofReuseRequest:
        raise VerificationContractError("typed reuse request required")
    value.__post_init__()
    correction = None if value.correction is None else {
        **_app_fields(value.correction), "finding_ids": list(value.correction.finding_ids)}
    return dict(format=REUSE_REQUEST_FORMAT, version=value.version,
        applicability=applicability_request_data(value.applicability), original=_reuse_snapshot_data(value.original),
        proof_id=value.proof_id, proof_sha256=value.proof_sha256, operation=value.operation, correction=correction)


def format_reuse_request(value: ProofReuseRequest) -> bytes:
    return _original_json(reuse_request_data(value)).encode("ascii")


def _reuse_decode(content: bytes):
    value = _original_decode(content)
    # L2-03 has one wire encoding: rejects whitespace, alternate escapes,
    # non-ASCII encodings, floats, duplicate keys, BOMs and trailing material.
    if _original_json(value).encode("ascii") != content:
        raise VerificationContractError("noncanonical reuse encoding")
    return value


def parse_reuse_request(content: bytes) -> ProofReuseRequest:
    """Strict structural decoding; neither pins nor parsed witnesses authorize."""
    data = dict(_app_exact(_reuse_decode(content), (*ProofReuseRequest.__dataclass_fields__, "format")))
    if data.pop("format") != REUSE_REQUEST_FORMAT:
        raise VerificationContractError("invalid reuse request format")
    data["applicability"] = parse_applicability_request(_original_json(data["applicability"]).encode("ascii"))
    data["original"] = _reuse_snapshot(data["original"])
    if data["correction"] is not None:
        item = dict(_app_exact(data["correction"], ReuseCorrectionLocator.__dataclass_fields__))
        if type(item["finding_ids"]) is not list:
            raise VerificationContractError("invalid reuse finding population")
        item["finding_ids"] = tuple(item["finding_ids"])
        data["correction"] = ReuseCorrectionLocator(**item)
    return ProofReuseRequest(**data)


def _reuse_primary_bound(app: ProofApplicabilityWitness) -> bool:
    source, target = app.source, app.target
    return (source is not None and target is not None
        and (source.identity.task_id, source.identity.task_revision, source.base_sha,
             source.task_blob_sha, source.task_sha256) == (
             target.identity.task_id, target.identity.task_revision, target.identity.base_sha,
             target.task_blob_sha, target.task_sha256))


def _reuse_reviewed(value: str, request: ProofReuseRequest) -> dict:
    """Check the retained normalized binding; this does not authenticate review."""
    if type(value) is not str:
        raise VerificationContractError("invalid reviewed reuse binding")
    data = _app_exact(_reuse_decode(value.encode("ascii")), ("obligation", "proof", "entry"))
    from .proof_coverage_contract import ProofObligation, ProofDescriptor, ProofMappingEntry, ProofConditions, SemanticClaim, Provenance
    obligation = _app_exact(data["obligation"], ProofObligation.__dataclass_fields__)
    proof = _app_exact(data["proof"], ProofDescriptor.__dataclass_fields__)
    entry = _app_exact(data["entry"], ProofMappingEntry.__dataclass_fields__)
    for item in (obligation, proof):
        _app_exact(item["conditions"], ProofConditions.__dataclass_fields__)
        _app_exact(item["provenance"], Provenance.__dataclass_fields__)
    _app_exact(obligation["claim"], SemanticClaim.__dataclass_fields__)
    if type(proof["claims"]) is not list or not proof["claims"]:
        raise VerificationContractError("invalid reviewed reuse claims")
    for claim in proof["claims"]:
        _app_exact(claim, SemanticClaim.__dataclass_fields__)
    app = request.applicability
    if (proof["id"] != request.proof_id or hashlib.sha256(_original_json(proof).encode("ascii")).hexdigest() != request.proof_sha256
            or hashlib.sha256(_original_json(obligation).encode("ascii")).hexdigest() != app.obligation_digest
            or obligation["id"] != app.obligation_id or type(obligation["revision"]) is not int
            or obligation["revision"] != app.obligation_revision or obligation["acceptance_ids"] != list(app.acceptance_ids)
            or obligation["blocking"] is not True or obligation["kind"] != "candidate" or proof["kind"] != "candidate"
            or _original_json(proof["conditions"]) != _original_json(obligation["conditions"])
            or type(proof["claims"]) is not list or obligation["claim"] not in proof["claims"]
            or _original_json(entry) != _original_json(dict(obligation_id=app.obligation_id,
                obligation_revision=app.obligation_revision, obligation_digest=app.obligation_digest, proof_id=request.proof_id))):
        raise VerificationContractError("reviewed reuse proof substitution")
    return data


def _reuse_source_satisfaction(app: ProofApplicabilityWitness, reviewed: dict) -> tuple[str, str]:
    """Stricter than exit zero or VALID; required original phases cannot be waived."""
    source = app.source
    if source.outcome in {"FAIL", "ERROR"}:
        return "UNSATISFACTORY", "ORIGINAL_" + source.outcome
    facts = {d.name: d for d in source.dimensions}
    def actual(name):
        fact = facts[name]
        return None if fact.status != "OBSERVED" else _original_decode(fact.value_json.encode("ascii"))
    if (source.outcome != "PASS" or actual("completeness") is not True or actual("stability") is not False
            or actual("prior_raw_digest") != source.raw_sha256):
        return "NOT_ESTABLISHED", "COMPLETE_STABLE_ORIGINAL_PASS_NOT_ESTABLISHED"
    conditions = reviewed["proof"]["conditions"]
    required = {"population", "collection", "phase_outcomes", "completeness", "stability", "evidence_kind_schema"}
    if (any(d.status != "EQUIVALENT" for d in app.dimensions if d.name in required)
            or conditions.get("evidence_kind") != "VERIFICATION" or conditions.get("evidence_schema") != "V2"):
        return "NOT_ESTABLISHED", "REQUIRED_ACTUAL_SEMANTIC_COVERAGE_NOT_ESTABLISHED"
    reports, population, collection = actual("phase_outcomes"), actual("population"), actual("collection")
    diagnostics, profile = actual("condition_diagnostics"), actual("execution_profile")
    if (type(diagnostics) is not dict or diagnostics.get("errors") != []
            or type(profile) is not dict or profile.get("collect_only") is not False):
        return "NOT_ESTABLISHED", "ORIGINAL_DIAGNOSTICS_OR_EXECUTION_NOT_ESTABLISHED"
    try:
        nodes = validate_exact_collection(collection)
    except (VerificationContractError, TypeError, KeyError):
        return "NOT_ESTABLISHED", "ORIGINAL_COLLECTION_NOT_ESTABLISHED"
    if (type(reports) is not list or not reports or type(population) is not list or list(nodes) != population
            or type(conditions.get("population_size")) is not int or len(nodes) != conditions["population_size"]):
        return "NOT_ESTABLISHED", "ORIGINAL_POPULATION_NOT_ESTABLISHED"
    phases = {node: {} for node in nodes}
    for report in reports:
        if (type(report) is not dict or type(report.get("nodeid")) is not str or report["nodeid"] not in phases
                or type(report.get("phase")) is not str or report["phase"] not in {"setup", "call", "teardown"}
                or report["phase"] in phases[report["nodeid"]] or report.get("outcome") != "PASS"):
            return "UNSATISFACTORY", "ORIGINAL_PHASE_NOT_SATISFACTORY"
        phases[report["nodeid"]][report["phase"]] = report["outcome"]
    if any(set(value) != {"setup", "call", "teardown"} for value in phases.values()):
        return "NOT_ESTABLISHED", "ORIGINAL_REQUIRED_PHASES_MISSING"
    return "SATISFACTORY", "COMPLETE_STABLE_ORIGINAL_PASS_WITH_EXACT_PHASE_POPULATION"


@dataclass(frozen=True, slots=True)
class ProofReuseWitness:
    version: int
    request: ProofReuseRequest
    authentication: str
    reason: str
    applicability: ProofApplicabilityWitness | None
    reviewed_proof_json: str | None
    source_snapshot: ApplicabilitySourceSnapshot | None

    def __post_init__(self):
        if type(self.version) is not int or self.version != REUSE_VERSION or type(self.request) is not ProofReuseRequest:
            raise VerificationContractError("invalid typed reuse witness")
        self.request.__post_init__()
        _original_text(self.reason, 256)
        if type(self.authentication) is not str or self.authentication not in {"AUTHENTICATED", "NOT_ESTABLISHED"}:
            raise VerificationContractError("invalid reuse authentication")
        if self.applicability is not None:
            if type(self.applicability) is not ProofApplicabilityWitness:
                raise VerificationContractError("typed applicability observation required")
            self.applicability.__post_init__()
            if self.applicability.request != self.request.applicability or self.source_snapshot != self.applicability.source_snapshot:
                raise VerificationContractError("reuse applicability substitution")
        if self.source_snapshot is not None:
            if type(self.source_snapshot) is not ApplicabilitySourceSnapshot:
                raise VerificationContractError("typed reuse source snapshot required")
            self.source_snapshot.__post_init__()
            if self.source_snapshot.identity != self.request.applicability.source:
                raise VerificationContractError("reuse source relabeling")
        if self.reviewed_proof_json is not None:
            _reuse_reviewed(self.reviewed_proof_json, self.request)
        if self.authentication == "AUTHENTICATED" and (
                self.applicability is None or self.applicability.authentication != "AUTHENTICATED"
                or self.source_snapshot != self.request.original or self.reviewed_proof_json is None
                or self.request.operation != "PRIMARY" or not _reuse_primary_bound(self.applicability)):
            raise VerificationContractError("reuse authority or operation promotion")

    @property
    def operation_authorization(self) -> str:
        return "AUTHORIZED_PRIMARY" if self.authentication == "AUTHENTICATED" else "NOT_ESTABLISHED"

    @property
    def source_satisfaction(self) -> tuple[str, str]:
        if self.authentication != "AUTHENTICATED":
            return "NOT_ESTABLISHED", "REUSE_AUTHORITY_NOT_ESTABLISHED"
        return _reuse_source_satisfaction(self.applicability, _reuse_reviewed(self.reviewed_proof_json, self.request))

    @property
    def outcome(self) -> str:
        # A changed target cannot establish a missing/unstable original proof.
        if (self.authentication != "AUTHENTICATED" or self.applicability.validity == "UNKNOWN"
                or self.source_snapshot.outcome == "UNKNOWN"):
            return "BLOCKED"
        if self.applicability.validity == "INVALIDATED":
            return "INVALIDATED"
        return "REUSE_ELIGIBLE" if self.source_satisfaction[0] == "SATISFACTORY" else "BLOCKED"


def reuse_data(value: ProofReuseWitness) -> dict:
    if type(value) is not ProofReuseWitness:
        raise VerificationContractError("typed reuse witness required")
    value.__post_init__()
    app, snapshot = value.applicability, value.source_snapshot
    invalidated = value.outcome == "INVALIDATED"
    source = None if app is None else app.source
    facts = {} if source is None else {d.name: d for d in source.dimensions}
    def observed(name):
        fact = facts.get(name)
        return None if fact is None or fact.status != "OBSERVED" else _original_decode(fact.value_json.encode("ascii"))
    return dict(format=REUSE_FORMAT, version=value.version, request=reuse_request_data(value.request),
        authentication=value.authentication, reason=value.reason,
        applicability=None if app is None else applicability_data(app),
        reviewed_proof_json=value.reviewed_proof_json, source_snapshot=_reuse_snapshot_data(snapshot),
        applicability_state="UNKNOWN" if app is None else app.validity,
        source_outcome="UNKNOWN" if snapshot is None else snapshot.outcome,
        source_complete=observed("completeness"), source_unstable=observed("stability"),
        source_satisfaction=value.source_satisfaction[0], satisfaction_reason=value.source_satisfaction[1],
        satisfactory=value.source_satisfaction[0] == "SATISFACTORY", operation_authorization=value.operation_authorization,
        outcome=value.outcome, target_execution="NOT_ASSERTED", target_acceptance="NOT_ASSERTED",
        execution_permission="NOT_AUTHORIZED", consumer_gate="L3_REAUTHENTICATION_REQUIRED",
        affected_dimensions=[d.name for d in app.dimensions if d.status == "CHANGED"] if invalidated else [],
        affected_proofs=[value.request.proof_id] if invalidated else [],
        outstanding_obligations=[value.request.applicability.obligation_id]
            if value.authentication == "AUTHENTICATED" and value.outcome != "REUSE_ELIGIBLE" else [])


def format_reuse(value: ProofReuseWitness) -> bytes:
    """Pure bounded serialization; a parsed positive is never Runtime authority."""
    return _original_json(reuse_data(value)).encode("ascii")


def parse_reuse(content: bytes) -> ProofReuseWitness:
    data = _reuse_decode(content)
    if type(data) is not dict:
        raise VerificationContractError("invalid reuse witness shape")
    fields = ProofReuseWitness.__dataclass_fields__
    if not set(fields) <= set(data):
        raise VerificationContractError("missing reuse witness fields")
    raw = {key: data[key] for key in fields}
    raw["request"] = parse_reuse_request(_original_json(raw["request"]).encode("ascii"))
    if raw["applicability"] is not None:
        raw["applicability"] = parse_applicability(_original_json(raw["applicability"]).encode("ascii"))
    if raw["source_snapshot"] is not None:
        raw["source_snapshot"] = _reuse_snapshot(raw["source_snapshot"])
    witness = ProofReuseWitness(**raw)
    if format_reuse(witness) != content:
        raise VerificationContractError("reuse fields, outcome or authority promotion")
    return witness


# L3-01 is explicitly invoked, prospective planning only. These types are not
# added to VERIFICATION_POLICIES or any execution/decoder dispatch table.
SCHEDULER_VERSION = 1
SCHEDULER_REQUEST_FORMAT = "AIOS_L3_PROOF_SCHEDULER_REQUEST"
SCHEDULER_PLAN_FORMAT = "AIOS_L3_PROOF_SCHEDULER_PLAN"
MAX_SCHEDULER_SOURCES = 32
SCHEDULER_INVARIANT = "ONE_REVIEWED_EQUIVALENT_PROOF_UNIT_NO_EQUIVALENT_DUPLICATE_EXECUTION"


@dataclass(frozen=True, slots=True)
class ProofSchedulerRequest:
    """Locators only; neither a review assertion nor an execution permit."""

    version: int
    target: ApplicabilityTargetRequest
    expected_main_sha: str
    contract: ApplicabilityDeclarationPin
    mapping: ApplicabilityDeclarationPin
    operation: str
    subject_sha: str
    sources: tuple[ProofReuseRequest, ...] = ()
    correction: ReuseCorrectionLocator | None = None

    def __post_init__(self):
        if type(self.version) is not int or self.version != SCHEDULER_VERSION:
            raise VerificationContractError("unsupported scheduler version")
        for value, cls in ((self.target, ApplicabilityTargetRequest),
                           (self.contract, ApplicabilityDeclarationPin), (self.mapping, ApplicabilityDeclarationPin)):
            if type(value) is not cls:
                raise VerificationContractError("exact typed scheduler locator required")
            value.__post_init__()
        _original_hash(self.expected_main_sha, 40)
        _original_hash(self.subject_sha, 40)
        if self.subject_sha != self.target.candidate_sha:
            raise VerificationContractError("scheduler subject substitution")
        # Unsupported operations are representable for attributable BLOCKED
        # results. No arbitrary text or operation can select a positive path.
        _app_id(self.operation)
        if type(self.sources) is not tuple or len(self.sources) > MAX_SCHEDULER_SOURCES:
            raise VerificationContractError("bounded immutable scheduler source population required")
        identities = []
        for source in self.sources:
            if type(source) is not ProofReuseRequest:
                raise VerificationContractError("scheduler accepts source locators, never witnesses")
            source.__post_init__()
            app = source.applicability
            if (app.target, app.expected_main_sha, app.contract, app.mapping, source.operation, source.correction) != (
                    self.target, self.expected_main_sha, self.contract, self.mapping, self.operation, self.correction):
                raise VerificationContractError("swapped scheduler source target or authority")
            identities.append(app.obligation_id)
        if identities != sorted(set(identities)):
            raise VerificationContractError("unordered or duplicate scheduler source obligation")
        if self.correction is not None:
            if type(self.correction) is not ReuseCorrectionLocator or self.operation == "PRIMARY":
                raise VerificationContractError("scheduler correction authority switching")
            self.correction.__post_init__()


def scheduler_request_data(value: ProofSchedulerRequest) -> dict:
    if type(value) is not ProofSchedulerRequest:
        raise VerificationContractError("exact typed scheduler request required")
    value.__post_init__()
    return dict(format=SCHEDULER_REQUEST_FORMAT, version=value.version,
        target=applicability_target_data(value.target), expected_main_sha=value.expected_main_sha,
        contract=_app_fields(value.contract), mapping=_app_fields(value.mapping), operation=value.operation,
        subject_sha=value.subject_sha, sources=[reuse_request_data(s) for s in value.sources],
        correction=None if value.correction is None else {
            **_app_fields(value.correction), "finding_ids": list(value.correction.finding_ids)})


def format_scheduler_request(value: ProofSchedulerRequest) -> bytes:
    return _original_json(scheduler_request_data(value)).encode("ascii")


def parse_scheduler_request(content: bytes) -> ProofSchedulerRequest:
    data = dict(_app_exact(_reuse_decode(content), (*ProofSchedulerRequest.__dataclass_fields__, "format")))
    if (data.pop("format") != SCHEDULER_REQUEST_FORMAT or type(data["sources"]) is not list
            or len(data["sources"]) > MAX_SCHEDULER_SOURCES):
        raise VerificationContractError("invalid scheduler request schema")
    data["target"] = _app_target(data["target"])
    for key in ("contract", "mapping"):
        data[key] = ApplicabilityDeclarationPin(**_app_exact(data[key], ApplicabilityDeclarationPin.__dataclass_fields__))
    data["sources"] = tuple(parse_reuse_request(_original_json(s).encode("ascii")) for s in data["sources"])
    if data["correction"] is not None:
        item = dict(_app_exact(data["correction"], ReuseCorrectionLocator.__dataclass_fields__))
        if type(item["finding_ids"]) is not list:
            raise VerificationContractError("invalid scheduler finding population")
        item["finding_ids"] = tuple(item["finding_ids"])
        data["correction"] = ReuseCorrectionLocator(**item)
    return ProofSchedulerRequest(**data)


@dataclass(frozen=True, slots=True)
class SchedulerSourceDecision:
    obligation_id: str
    outcome: str
    reason: str
    original: ApplicabilitySourceSnapshot | None
    witness_sha256: str

    def __post_init__(self):
        _app_id(self.obligation_id)
        if type(self.outcome) is not str or self.outcome not in {"REUSE_ELIGIBLE", "INVALIDATED", "BLOCKED"}:
            raise VerificationContractError("invalid scheduler source decision")
        _original_text(self.reason, 256)
        _original_hash(self.witness_sha256)
        if self.original is not None:
            if type(self.original) is not ApplicabilitySourceSnapshot:
                raise VerificationContractError("typed scheduler original snapshot required")
            self.original.__post_init__()
        if self.outcome == "REUSE_ELIGIBLE" and (self.original is None or self.original.outcome != "PASS"):
            raise VerificationContractError("scheduler source outcome promotion")


def _scheduler_plain(value):
    if type(value) in (tuple, list):
        return [_scheduler_plain(v) for v in value]
    if type(value) is dict:
        return {k: _scheduler_plain(v) for k, v in value.items()}
    return value


def _scheduler_coverage(value):
    from dataclasses import asdict
    return None if value is None else _scheduler_plain(asdict(value))


def _scheduler_validate_coverage(data, request):
    from .proof_coverage_contract import validate_proof_mapping
    data = _app_exact(data, ("contract", "mapping"))
    contract = data["contract"]
    coverage = validate_proof_mapping(contract, data["mapping"], expected_task=contract["task"],
                                     expected_mapping=_app_fields(request.mapping))
    task = coverage.contract.task
    if ((task.task_id, task.revision) != (request.target.task_id, request.target.task_revision)
            or (coverage.contract.id, coverage.contract.revision, coverage.contract.digest) != (
                request.contract.id, request.contract.revision, request.contract.digest)
            or any(o.conditions.candidate_sha != request.subject_sha
                   or o.kind == "comparison" and o.conditions.base_sha != request.target.base_sha
                   for o in coverage.contract.obligations)):
        raise VerificationContractError("scheduler reviewed population substitution")
    return coverage


@dataclass(frozen=True, slots=True)
class ProofSchedulerPlan:
    """Immutable inspection result. Parsing can never reauthenticate this plan."""

    version: int
    request: ProofSchedulerRequest
    authentication: str
    reason: str
    coverage: CoverageDeclaration | None
    authority_json: str | None
    sources: tuple[SchedulerSourceDecision, ...]

    def __post_init__(self):
        from .proof_coverage_contract import CoverageDeclaration
        if type(self.version) is not int or self.version != SCHEDULER_VERSION or type(self.request) is not ProofSchedulerRequest:
            raise VerificationContractError("invalid typed scheduler plan")
        self.request.__post_init__()
        _original_text(self.reason, 256)
        if type(self.authentication) is not str or self.authentication not in {"AUTHENTICATED", "NOT_ESTABLISHED"}:
            raise VerificationContractError("invalid scheduler authentication")
        if self.coverage is not None:
            if type(self.coverage) is not CoverageDeclaration:
                raise VerificationContractError("typed scheduler coverage required")
            if _scheduler_validate_coverage(_scheduler_coverage(self.coverage), self.request) != self.coverage:
                raise VerificationContractError("noncanonical scheduler coverage")
        if self.authority_json is not None:
            authority = _app_exact(_reuse_decode(self.authority_json.encode("ascii")), (
                "main_sha", "selection_sha", "review_candidate_sha", "artifacts_sha", "decision_sha",
                "binding", "target"))
            for key in ("main_sha", "selection_sha", "review_candidate_sha", "artifacts_sha", "decision_sha"):
                _original_hash(authority[key], 40)
            if authority["main_sha"] != self.request.expected_main_sha:
                raise VerificationContractError("scheduler main authority substitution")
            binding = _app_exact(authority["binding"], ("upstream_sha256", "human_event_identity", "review_id",
                "review_run_id", "run_sha256", "result_sha256", "review_sha256", "scope_sha256", "sources"))
            for key in ("upstream_sha256", "run_sha256", "result_sha256", "review_sha256", "scope_sha256"):
                _original_hash(binding[key])
            for key in ("human_event_identity", "review_id", "review_run_id"):
                _original_text(binding[key], 256)
            source_pins = _app_exact(binding["sources"], ("task", "contract", "mapping", "review_task"))
            for source in source_pins.values():
                _app_exact(source, ("source_sha", "tree_sha", "path", "blob_sha", "sha256"))
                for key in ("source_sha", "tree_sha", "blob_sha"):
                    _original_hash(source[key], 40)
                _original_hash(source["sha256"])
                _app_path(source["path"])
            target = _app_exact(authority["target"], ("task_blob_sha", "task_sha256", "canonical_run_sha256",
                "lineage_sha256", "condition_proof_sha256"))
            _original_hash(target["task_blob_sha"], 40)
            for key in ("task_sha256", "canonical_run_sha256", "lineage_sha256"):
                _original_hash(target[key])
            if target["condition_proof_sha256"] is not None:
                _original_hash(target["condition_proof_sha256"])
            if (self.coverage is None or self.coverage.contract.task.envelope_digest != target["task_sha256"]
                    or source_pins["task"]["blob_sha"] != target["task_blob_sha"]
                    or source_pins["task"]["sha256"] != target["task_sha256"]):
                raise VerificationContractError("scheduler TASK authority conflict")
        if self.authentication == "AUTHENTICATED" and (
                self.request.operation != "PRIMARY" or self.coverage is None or self.authority_json is None):
            raise VerificationContractError("scheduler operation or review authority promotion")
        if type(self.sources) is not tuple or len(self.sources) > MAX_SCHEDULER_SOURCES:
            raise VerificationContractError("bounded immutable scheduler decisions required")
        requests = {s.applicability.obligation_id: s for s in self.request.sources}
        ids = []
        for source in self.sources:
            if type(source) is not SchedulerSourceDecision:
                raise VerificationContractError("typed scheduler source decision required")
            source.__post_init__()
            ids.append(source.obligation_id)
            locator = requests.get(source.obligation_id)
            if locator is None or source.outcome == "REUSE_ELIGIBLE" and source.original != locator.original:
                raise VerificationContractError("scheduler source substitution")
        if ids != sorted(set(ids)) or self.authentication == "AUTHENTICATED" and set(ids) != set(requests):
            raise VerificationContractError("incomplete or duplicate scheduler source decisions")


def _scheduler_projection(plan):
    """One pure projection for both consumers; no alternate proof scheduler."""
    from dataclasses import asdict
    if plan.coverage is None:
        return [], []
    entries = {e.obligation_id: e for e in plan.coverage.mapping.entries}
    observations = {s.obligation_id: s for s in plan.sources}
    rows, units = [], []
    for proof in sorted(plan.coverage.mapping.proofs, key=lambda p: (p.kind == "comparison", p.id)):
        obligations = tuple(o for o in plan.coverage.contract.obligations if entries[o.id].proof_id == proof.id)
        decisions = [observations.get(o.id) for o in obligations]
        if plan.authentication != "AUTHENTICATED":
            state, reason = "BLOCKED", "CURRENT_PLAN_AUTHORITY_NOT_ESTABLISHED"
        elif proof.kind == "comparison":
            state, reason = "BLOCKED", "BR_1_THROUGH_BR_6_NATIVE_ATTESTATION_NOT_ESTABLISHED"
        elif any(d is not None and d.outcome == "BLOCKED" for d in decisions):
            state, reason = "BLOCKED", "SOURCE_PROOF_UNKNOWN_OR_UNSATISFACTORY"
        elif all(d is not None and d.outcome == "REUSE_ELIGIBLE" for d in decisions):
            state, reason = "REUSE_ELIGIBLE", "EVERY_MAPPED_OBLIGATION_HAS_AUTHENTICATED_SATISFACTORY_SOURCE"
        else:
            state, reason = "OUTSTANDING_PLANNED", "FULL_REVIEWED_UNIT_REQUIRES_ORIGINAL_CANDIDATE_PROOF"
        proof_data = _scheduler_plain(asdict(proof))
        identity = verification_digest(proof_data)
        unit = dict(unit_id=identity, proof_id=proof.id, proof_sha256=identity, proof=proof_data,
            obligation_ids=[o.id for o in obligations],
            acceptance_ids=sorted({a for o in obligations for a in o.acceptance_ids}), state=state, reason=reason,
            execution_permission="NOT_AUTHORIZED", base_replay="NOT_AUTHORIZED")
        units.append(unit)
        for obligation in obligations:
            rows.append(dict(obligation=_scheduler_plain(asdict(obligation)), obligation_sha256=obligation.digest,
                unit_id=identity, proof_id=proof.id, state=state, reason=reason))
    return sorted(rows, key=lambda r: r["obligation"]["id"]), units


def scheduler_plan_data(value: ProofSchedulerPlan) -> dict:
    if type(value) is not ProofSchedulerPlan:
        raise VerificationContractError("exact typed scheduler plan required")
    value.__post_init__()
    rows, units = _scheduler_projection(value)
    authority = None if value.authority_json is None else _reuse_decode(value.authority_json.encode("ascii"))
    data = dict(format=SCHEDULER_PLAN_FORMAT, version=value.version, request=scheduler_request_data(value.request),
        authentication=value.authentication, reason=value.reason, coverage=_scheduler_coverage(value.coverage),
        authority=authority, sources=[{**_app_fields(s), "original": _reuse_snapshot_data(s.original)} for s in value.sources],
        obligations=rows, units=units)
    coverage_identity = verification_digest(dict(request={k: v for k, v in data["request"].items() if k != "sources"},
                                               coverage=data["coverage"], authority=authority))
    plan_identity = verification_digest(data)
    consumer = dict(plan_identity=plan_identity, coverage_identity=coverage_identity, units=units,
        invariant=SCHEDULER_INVARIANT, equivalent_fresh_checkpoint="ELIGIBLE_ONLY_AFTER_SAME_CORE_REAUTHENTICATION",
        checkpoint_sealed="NOT_ASSERTED", actual_execution="NOT_ASSERTED", target_acceptance="NOT_ASSERTED",
        execution_permission="NOT_AUTHORIZED", consumer_gate="FUTURE_RUNTIME_INTEGRATION_AND_REAUTHENTICATION_REQUIRED")
    return {**data, "plan_identity": plan_identity, "coverage_identity": coverage_identity,
        "status": "BLOCKED" if value.authentication != "AUTHENTICATED" or any(u["state"] == "BLOCKED" for u in units)
                  else "PLANNED", "ordering": "CANDIDATE_FIRST_NO_AUTOMATIC_BASE_REPLAY",
        "invariant": SCHEDULER_INVARIANT, "checkpoint": consumer, "terminal": dict(consumer),
        "target_execution": "NOT_ASSERTED", "target_acceptance": "NOT_ASSERTED",
        "execution_permission": "NOT_AUTHORIZED", "production_activation": "NOT_ACTIVATED"}


def format_scheduler_plan(value: ProofSchedulerPlan) -> bytes:
    return _original_json(scheduler_plan_data(value)).encode("ascii")


def parse_scheduler_plan(content: bytes) -> ProofSchedulerPlan:
    """Closed structural inspection only; not accepted by plan_candidate_proofs."""
    data = _app_exact(_reuse_decode(content), ("format", "version", "request", "authentication", "reason",
        "coverage", "authority", "sources", "obligations", "units", "plan_identity", "coverage_identity",
        "status", "ordering", "invariant", "checkpoint", "terminal", "target_execution", "target_acceptance",
        "execution_permission", "production_activation"))
    request = parse_scheduler_request(_original_json(data["request"]).encode("ascii"))
    coverage = None if data["coverage"] is None else _scheduler_validate_coverage(data["coverage"], request)
    if type(data["sources"]) is not list or len(data["sources"]) > MAX_SCHEDULER_SOURCES:
        raise VerificationContractError("invalid scheduler source decisions")
    sources = []
    for value in data["sources"]:
        item = dict(_app_exact(value, SchedulerSourceDecision.__dataclass_fields__))
        item["original"] = None if item["original"] is None else _reuse_snapshot(item["original"])
        sources.append(SchedulerSourceDecision(**item))
    result = ProofSchedulerPlan(data["version"], request, data["authentication"], data["reason"], coverage,
        None if data["authority"] is None else _original_json(data["authority"]), tuple(sources))
    if scheduler_plan_data(result) != data:
        raise VerificationContractError("extra, missing or substituted scheduler projection")
    return result
