"""Deterministic minimum-sufficient verification contract policy.

This module classifies only a deliberately small pytest command grammar.  It
does not select or execute tests.  Commands outside that grammar remain opaque.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Sequence


MINIMUM_SUFFICIENT_V1 = "minimum-sufficient-v1"
FULL_SUITE_REASON_LIMIT = 512


class VerificationContractError(ValueError):
    """Raised when a v1 verification declaration is structurally invalid."""


@dataclass(frozen=True)
class PytestCoverage:
    """Coverage proven from one command in the supported pytest grammar."""

    launcher: str
    paths: tuple[str, ...]
    filter_expression: str | None

    @property
    def is_full_suite(self) -> bool:
        return not self.paths and self.filter_expression is None


def parse_pytest_coverage(command: str) -> PytestCoverage | None:
    """Return proven coverage for a supported command, otherwise ``None``."""

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
