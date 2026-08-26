"""Canonical TASK contract parsing and validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml


class TaskValidationError(ValueError):
    """Raised when TASK input does not satisfy the canonical contract."""


@dataclass(frozen=True)
class TaskScope:
    inspect: tuple[str, ...]
    modify: tuple[str, ...]


@dataclass(frozen=True)
class TaskConstraints:
    hard: tuple[str, ...]


@dataclass(frozen=True)
class AcceptanceCriterion:
    id: str
    condition: str


@dataclass(frozen=True)
class TaskVerification:
    required: tuple[str, ...]


@dataclass(frozen=True)
class Task:
    task_id: str
    revision: int
    goal: str
    problem: str
    assumptions: tuple[str, ...]
    scope: TaskScope
    non_goals: tuple[str, ...]
    constraints: TaskConstraints
    acceptance: tuple[AcceptanceCriterion, ...]
    verification: TaskVerification


def parse_task(source: str) -> Task:
    """Parse a YAML TASK document and validate its canonical fields."""

    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise TaskValidationError(f"Invalid TASK YAML: {exc}") from exc

    return validate_task(data)


def validate_task(data: Any) -> Task:
    """Validate decoded TASK data and return its immutable representation."""

    root = _mapping(data, "TASK")
    scope = _mapping(_required(root, "scope", "TASK"), "scope")
    constraints = _mapping(
        _required(root, "constraints", "TASK"), "constraints"
    )
    verification = _mapping(
        _required(root, "verification", "TASK"), "verification"
    )

    acceptance_data = _list(
        _required(root, "acceptance", "TASK"), "acceptance"
    )
    if not acceptance_data:
        raise TaskValidationError("acceptance must contain at least one criterion")

    acceptance: list[AcceptanceCriterion] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(acceptance_data):
        path = f"acceptance[{index}]"
        criterion = _mapping(item, path)
        criterion_id = _string(_required(criterion, "id", path), f"{path}.id")
        if criterion_id in seen_ids:
            raise TaskValidationError(
                f"acceptance contains duplicate id: {criterion_id}"
            )
        seen_ids.add(criterion_id)
        acceptance.append(
            AcceptanceCriterion(
                id=criterion_id,
                condition=_string(
                    _required(criterion, "condition", path),
                    f"{path}.condition",
                ),
            )
        )

    revision = _required(root, "revision", "TASK")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise TaskValidationError("revision must be a positive integer")

    return Task(
        task_id=_string(_required(root, "task_id", "TASK"), "task_id"),
        revision=revision,
        goal=_string(_required(root, "goal", "TASK"), "goal"),
        problem=_string(_required(root, "problem", "TASK"), "problem"),
        assumptions=_string_list(
            _required(root, "assumptions", "TASK"), "assumptions"
        ),
        scope=TaskScope(
            inspect=_string_list(
                _required(scope, "inspect", "scope"), "scope.inspect"
            ),
            modify=_string_list(
                _required(scope, "modify", "scope"), "scope.modify"
            ),
        ),
        non_goals=_string_list(
            _required(root, "non_goals", "TASK"), "non_goals"
        ),
        constraints=TaskConstraints(
            hard=_string_list(
                _required(constraints, "hard", "constraints"),
                "constraints.hard",
            )
        ),
        acceptance=tuple(acceptance),
        verification=TaskVerification(
            required=_string_list(
                _required(verification, "required", "verification"),
                "verification.required",
            )
        ),
    )


def _required(mapping: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise TaskValidationError(f"{path}.{key} is required")
    return mapping[key]


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TaskValidationError(f"{path} must be a mapping")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise TaskValidationError(f"{path} must be a list")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, path: str) -> tuple[str, ...]:
    items = _list(value, path)
    return tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(items))
