"""Pure transport-origin capture; no route, persistence, or lifecycle authority."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Literal

ORIGIN_HANDLE_CONTRACT = "ORIGIN_HANDLE_V1"
OPENAI_CAPTURE_CONTRACT = "OPENAI_SESSION_ORIGIN_CAPTURE_V1"
OPENAI_SESSION_FIELD = "openai/session"
MAX_METADATA_FIELDS = 64
MAX_METADATA_KEY_CHARS = 128
MAX_SESSION_BYTES = 1024
HANDLE_PREFIX = "origin-v1:"
# Both the algorithm version and the input namespace are part of the digest.
OPENAI_SESSION_DOMAIN = b"AIOS\x00ORIGIN_HANDLE_V1\x00openai/session\x00"

Reason = Literal[
    "ACCEPTED", "METADATA_ABSENT", "METADATA_INVALID", "ORIGIN_INPUT_ABSENT",
    "ORIGIN_INPUT_INVALID", "ORIGIN_INPUT_OVER_BOUND", "PROBE_INPUT_INVALID",
]


@dataclass(frozen=True, slots=True)
class OriginCaptureResult:
    """Provider-neutral return contract. Never holds the source metadata."""

    status: Literal["CAPTURED", "UNPROVED"]
    reason: Reason
    origin_handle: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "contract": ORIGIN_HANDLE_CONTRACT,
            "status": self.status,
            "reason": self.reason,
            "origin_handle": self.origin_handle,
        }


def unproved(reason: Reason) -> OriginCaptureResult:
    """Fail closed with a fixed code, without interpolating rejected input."""
    return OriginCaptureResult("UNPROVED", reason)


def _openai_session_input(host_metadata: object) -> bytes | OriginCaptureResult:
    """Provider-specific extraction from the host's tool-call _meta object.

    V1 accepts only a plain JSON object with bounded string keys and an exact
    openai/session field containing 1..1024 visible ASCII bytes (no whitespace
    or controls). This is a local transport safety grammar, not a claimed
    provider identifier format. Values are never trimmed, decoded or normalized.
    Unrelated metadata values are ignored, never traversed or used as fallback.
    The caller must supply metadata from a trusted MCP host boundary; a string
    or opaque handle by itself is neither authentication nor authority.
    """
    if host_metadata is None:
        return unproved("METADATA_ABSENT")
    if type(host_metadata) is not dict or len(host_metadata) > MAX_METADATA_FIELDS:
        return unproved("METADATA_INVALID")
    if any(type(key) is not str or not 1 <= len(key) <= MAX_METADATA_KEY_CHARS
           for key in host_metadata):
        return unproved("METADATA_INVALID")
    if OPENAI_SESSION_FIELD not in host_metadata:
        return unproved("ORIGIN_INPUT_ABSENT")
    session = host_metadata[OPENAI_SESSION_FIELD]
    if type(session) is not str or not session:
        return unproved("ORIGIN_INPUT_INVALID")
    # Check length before scanning/encoding so processing is bounded.
    if len(session) > MAX_SESSION_BYTES:
        return unproved("ORIGIN_INPUT_OVER_BOUND")
    if any(not "!" <= char <= "~" for char in session):
        return unproved("ORIGIN_INPUT_INVALID")
    return session.encode("ascii")


def capture_origin(host_metadata: object) -> OriginCaptureResult:
    """Provider-neutral primitive over a fixed, replaceable metadata adapter.

    Only host metadata enters this API. The v1 adapter recognizes openai/session;
    replacing it and its derivation domain requires an explicit contract change,
    without adding provider-specific fields to the returned origin contract.
    """
    exact_input = _openai_session_input(host_metadata)
    if isinstance(exact_input, OriginCaptureResult):
        return exact_input
    # SHA-256 is an opaque, one-way selector, not encryption or authorization.
    digest = hashlib.sha256(OPENAI_SESSION_DOMAIN + exact_input).hexdigest()
    return OriginCaptureResult("CAPTURED", "ACCEPTED", HANDLE_PREFIX + digest)
