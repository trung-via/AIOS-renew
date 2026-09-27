"""Opt-in, repository-blind Antigravity transport for one Gemini Reviewer call.

This is a BP8-P1 proof surface, not provider registration or REVIEW authority.
Callers bind it explicitly to JsonReviewerProvider("antigravity", "gemini-3.8-flash", ...).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable


PROVIDER = "antigravity"
MODEL = "gemini-3.8-flash"
REQUEST_BYTES = 2097152
NATIVE_OUTPUT_BYTES = 262144
SEMANTIC_RESPONSE_BYTES = 196608
WRAPPER_BYTES = 262144
MAX_TOKEN_COUNT = 2**31 - 1
SCHEMA = Path(__file__).parent / "schemas" / "gemini_reviewer_semantic_response.json"
_EFFORTS = frozenset({"low", "medium", "high"})
_INSTRUCTION = (
    "You are the transient AIOS REVIEWER. Read only the exact AIOS_REVIEW_REQUEST "
    "UTF-8 bytes in reviewer_request.json in the supplied isolated workspace. "
    "Use its embedded procedure and return contract. Return only the provider "
    "semantic response shape: request_fingerprint and semantic_body. "
    "Do not discover or mutate any repository, Git, GitHub, or other paths."
)


class GeminiReviewerTransportError(RuntimeError):
    """Pre-REVIEW transport/response failure without native output attached."""


@dataclass(frozen=True, slots=True)
class NativeUsageWitness:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int


@dataclass(frozen=True, slots=True)
class GeminiReviewerObservation:
    request_sha256: str
    provider: str
    model: str
    effort: str
    invocation_count: int
    native_usage: NativeUsageWitness


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise GeminiReviewerTransportError("duplicate native JSON key")
        result[key] = value
    return result


def _bad_constant(_value: str) -> None:
    raise GeminiReviewerTransportError("invalid native JSON number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise GeminiReviewerTransportError("invalid native JSON number")
    return parsed


def _bounded_count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TOKEN_COUNT:
        raise GeminiReviewerTransportError("invalid native usage witness")
    return value


def _witness(value: Any) -> NativeUsageWitness:
    if type(value) is not dict or not {"input_tokens", "output_tokens"} <= set(value):
        raise GeminiReviewerTransportError("missing native usage witness")
    source = _bounded_count(value["input_tokens"])
    output = _bounded_count(value["output_tokens"])
    cache_values = [_bounded_count(value[key]) for key in (
        "cache_read_tokens", "cached_input_tokens", "cached_tokens"
    ) if key in value]
    details = value.get("prompt_tokens_details")
    if type(details) is dict and "cached_tokens" in details:
        cache_values.append(_bounded_count(details["cached_tokens"]))
    if not cache_values or len(set(cache_values)) != 1:
        raise GeminiReviewerTransportError("invalid native usage witness")
    cached = cache_values[0]
    if source == 0 or output == 0 or cached > source:
        raise GeminiReviewerTransportError("invalid native usage witness")
    return NativeUsageWitness(source, output, cached)


def _native_payload(raw: Any) -> tuple[dict[str, Any], NativeUsageWitness]:
    if type(raw) is not bytes or len(raw) > NATIVE_OUTPUT_BYTES:
        raise GeminiReviewerTransportError("native output exceeds bound or is not bytes")
    try:
        envelope = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                              parse_constant=_bad_constant, parse_float=_finite_float)
    except (UnicodeError, ValueError, RecursionError):
        raise GeminiReviewerTransportError("invalid native UTF-8 JSON") from None
    if type(envelope) is not dict or envelope.get("status") != "SUCCESS":
        raise GeminiReviewerTransportError("native status invalid")
    semantic = envelope.get("structured_output")
    if type(semantic) is not dict or set(semantic) != {
        "request_fingerprint", "semantic_body"
    } or type(semantic["semantic_body"]) is not dict:
        raise GeminiReviewerTransportError("native structured output invalid")
    witness = _witness(envelope.get("usage"))
    try:
        size = len(json.dumps(semantic, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8", "strict"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise GeminiReviewerTransportError("native structured output invalid") from None
    if size > SEMANTIC_RESPONSE_BYTES:
        raise GeminiReviewerTransportError("native semantic response exceeds bound")
    return semantic, witness


class GeminiReviewerTransport:
    """Callable byte transport; no implicit provider selection or process retries."""

    def __init__(self, *, effort: str, runner: Callable[..., Any] = subprocess.run,
                 timeout_seconds: int = 300) -> None:
        if type(effort) is not str or effort not in _EFFORTS:
            raise ValueError("explicit supported Reviewer effort required")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
            raise ValueError("bounded native timeout required")
        if not callable(runner):
            raise TypeError("native process runner required")
        self._effort = effort
        self._runner = runner
        self._timeout_seconds = timeout_seconds
        self._observation: GeminiReviewerObservation | None = None

    @property
    def last_observation(self) -> GeminiReviewerObservation | None:
        return self._observation

    def __call__(self, request: bytes) -> bytes:
        self._observation = None
        if type(request) is not bytes or len(request) > REQUEST_BYTES:
            raise GeminiReviewerTransportError("Reviewer request byte bound invalid")
        try:
            request.decode("utf-8", "strict")
        except UnicodeError:
            raise GeminiReviewerTransportError("Reviewer request must be UTF-8") from None
        digest = hashlib.sha256(request).hexdigest()
        # TemporaryDirectory is OS-temporary, outside the repository workspace.
        # Only the request and schema are made visible to the native process.
        with tempfile.TemporaryDirectory(prefix="aios-gemini-reviewer-") as directory:
            workspace = Path(directory)
            (workspace / "reviewer_request.json").write_bytes(request)
            schema_path = workspace / "response_schema.json"
            schema_path.write_bytes(SCHEMA.read_bytes())
            command = (
                "agy", "--print", _INSTRUCTION, "--add-dir", str(workspace),
                "--mode", "plan", "--model", MODEL, "--effort", self._effort,
                "--disable-slash-commands", "--output-format", "json",
                "--json-schema", str(schema_path), "--print-timeout",
                f"{self._timeout_seconds}s",
            )
            try:
                completed = self._runner(command, cwd=str(workspace),
                                         capture_output=True, text=False, check=False,
                                         timeout=self._timeout_seconds)
            except (OSError, subprocess.TimeoutExpired):
                raise GeminiReviewerTransportError("native Reviewer process failed") from None
            if type(completed.returncode) is not int or completed.returncode != 0:
                raise GeminiReviewerTransportError("native Reviewer process returned nonzero")
            semantic, witness = _native_payload(completed.stdout)
            wrapper = {
                "semantic_response": semantic,
                "attribution": {"provider": PROVIDER, "model": MODEL,
                                "session_id": None, "invocation_id": None},
            }
            try:
                encoded = json.dumps(wrapper, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8", "strict")
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise GeminiReviewerTransportError("native wrapper invalid") from None
            if len(encoded) > WRAPPER_BYTES:
                raise GeminiReviewerTransportError("native wrapper exceeds bound")
        self._observation = GeminiReviewerObservation(
            digest, PROVIDER, MODEL, self._effort, 1, witness,
        )
        return encoded


__all__ = ["GeminiReviewerTransport", "GeminiReviewerTransportError",
           "GeminiReviewerObservation", "NativeUsageWitness", "PROVIDER", "MODEL"]
