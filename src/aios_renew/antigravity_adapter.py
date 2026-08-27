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
from .review import RemediationExecution
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

    def execute_remediation(
        self, *, execution: RemediationExecution
    ) -> ResultPackage:
        """Hand off the same narrow contract used by Codex."""

        try:
            output = self._transport(execution=execution)
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
            result = validate_result(_normalize_satisfies(root["result"]))
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


def _normalize_satisfies(result: Any) -> Any:
    """Wrap singleton string ``satisfies`` values without interpreting them."""

    if not isinstance(result, Mapping):
        return result
    claims = result.get("claims")
    if not isinstance(claims, list):
        return result

    normalized_claims: list[Any] = []
    changed = False
    for claim in claims:
        if isinstance(claim, Mapping) and isinstance(claim.get("satisfies"), str):
            claim = dict(claim)
            claim["satisfies"] = [claim["satisfies"]]
            changed = True
        normalized_claims.append(claim)

    if not changed:
        return result
    normalized_result = dict(result)
    normalized_result["claims"] = normalized_claims
    return normalized_result
