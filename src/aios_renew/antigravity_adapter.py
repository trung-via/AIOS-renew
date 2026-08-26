"""Minimal adapter boundary for native Antigravity execution."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from .artifacts import (
    ArtifactValidationError,
    ResultPackage,
    validate_evidence,
    validate_result,
)
from .run import Run
from .task import Task


NativeTransport = Callable[..., Any]


class AntigravityExecutionError(RuntimeError):
    """Raised when the native Antigravity transport fails."""


class AntigravityOutputError(AntigravityExecutionError):
    """Raised when native output is not a canonical result package."""


class AntigravityAdapter:
    """Handoff canonical input to an injected native Antigravity transport."""

    executor = "antigravity"

    def __init__(self, *, transport: NativeTransport) -> None:
        self._transport = transport

    def execute(self, *, task: Task, run: Run) -> ResultPackage:
        """Execute the unchanged TASK/RUN pair through the native transport."""

        try:
            output = self._transport(task=task, run=run)
        except AntigravityExecutionError:
            raise
        except Exception as exc:
            raise AntigravityExecutionError(
                f"Antigravity native invocation failed: {exc}"
            ) from exc

        return self._normalize(output)

    @staticmethod
    def _normalize(output: Any) -> ResultPackage:
        try:
            payload = json.loads(output) if isinstance(output, str) else output
            root = _mapping(payload, "Antigravity output")
            result = validate_result(root["result"])
            evidence_data = root["evidence"]
            if not isinstance(evidence_data, list):
                raise TypeError("evidence must be a list")
            evidence = tuple(validate_evidence(item) for item in evidence_data)
        except (
            ArtifactValidationError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as exc:
            raise AntigravityOutputError(
                f"Antigravity returned invalid canonical output: {exc}"
            ) from exc

        return ResultPackage(result=result, evidence=evidence)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping")
    return value
