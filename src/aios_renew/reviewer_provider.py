"""Transient, one-call Reviewer provider boundary over the BP6-P3 protocol.

Selection, semantic material, and identity are supplied by the caller. This module
has no canonical REVIEW, lifecycle, repository, or transport discovery authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from .decision_packet import DecisionPacket
from .reviewer_provider_protocol import (
    ReviewerProviderProtocolError, construct_request, validate_response,
)


NATIVE_RESPONSE_BYTES = 262144
ATTRIBUTION_BYTES = 8192
_WRAPPER_FIELDS = frozenset({"semantic_response", "attribution"})
_ATTRIBUTION_FIELDS = frozenset({"provider", "model", "session_id", "invocation_id"})


class ReviewerProviderTransportError(Exception):
    """The selected native callable failed before returning a response."""


class ReviewerProviderResponseError(ValueError):
    """A native response or operational attribution is malformed."""


class ReviewerProviderAttributionMismatch(ReviewerProviderResponseError):
    """Configured provider/model and invocation attribution differ."""


@dataclass(frozen=True, slots=True)
class ReviewerProviderInvocation:
    semantic_response: Mapping[str, Any]
    attribution: Mapping[str, str | None]


class ReviewerProvider(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def invoke(self, request: Mapping[str, Any]) -> ReviewerProviderInvocation: ...


def _text(value: Any, ceiling: int, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or not value:
        raise ReviewerProviderResponseError("invalid operational attribution field")
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeError as exc:
        raise ReviewerProviderResponseError("invalid operational attribution Unicode") from exc
    if size > ceiling:
        raise ReviewerProviderResponseError("operational attribution field exceeds bound")
    return value


def _identity(provider: Any, model: Any) -> None:
    _text(provider, 512)
    _text(model, 512)


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ReviewerProviderResponseError("strict UTF-8 JSON required") from exc


def _mapping_json(value: Any, depth: int = 0) -> None:
    """Validate the mapping-native grammar independently of P3 semantics."""
    if depth > 32:
        raise ReviewerProviderResponseError("native mapping depth exceeds 32")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        try:
            value.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise ReviewerProviderResponseError("invalid native mapping Unicode") from exc
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _mapping_json(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ReviewerProviderResponseError("native mapping key must be text")
            _mapping_json(key, depth + 1)
            _mapping_json(item, depth + 1)
        return
    raise ReviewerProviderResponseError("native mapping requires strict JSON values")


def _attribution(value: Any, provider: str, model: str) -> dict[str, str | None]:
    if not isinstance(value, Mapping) or set(value) != _ATTRIBUTION_FIELDS:
        raise ReviewerProviderResponseError("attribution fields do not match closed contract")
    result = {
        "provider": _text(value["provider"], 512),
        "model": _text(value["model"], 512),
        "session_id": _text(value["session_id"], 2048, nullable=True),
        "invocation_id": _text(value["invocation_id"], 2048, nullable=True),
    }
    if len(_json_bytes(result)) > ATTRIBUTION_BYTES:
        raise ReviewerProviderResponseError("operational attribution exceeds bound")
    if result["provider"] != provider or result["model"] != model:
        raise ReviewerProviderAttributionMismatch("configured provider/model mismatch")
    return result


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewerProviderResponseError("duplicate native JSON key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ReviewerProviderResponseError("non-finite native JSON number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ReviewerProviderResponseError("non-finite native JSON number")
    return parsed


@dataclass(frozen=True, slots=True)
class MappingReviewerProvider:
    """One mapping-native callable with its own closed extraction path."""

    provider: str
    model: str
    callable: Callable[[Mapping[str, Any]], Any]

    def __post_init__(self) -> None:
        _identity(self.provider, self.model)
        if not callable(self.callable):
            raise TypeError("provider callable required")

    def invoke(self, request: Mapping[str, Any]) -> ReviewerProviderInvocation:
        try:
            native = self.callable(deepcopy(request))
        except Exception as exc:
            raise ReviewerProviderTransportError("mapping provider callable failed") from exc
        if type(native) is not dict:
            raise ReviewerProviderResponseError("native mapping wrapper required")
        _mapping_json(native)
        if len(_json_bytes(native)) > NATIVE_RESPONSE_BYTES:
            raise ReviewerProviderResponseError("native response exceeds bound")
        if set(native) != _WRAPPER_FIELDS or type(native["semantic_response"]) is not dict:
            raise ReviewerProviderResponseError("native mapping wrapper fields invalid")
        attribution = _attribution(native["attribution"], self.provider, self.model)
        return ReviewerProviderInvocation(deepcopy(native["semantic_response"]),
                                          MappingProxyType(attribution))


@dataclass(frozen=True, slots=True)
class JsonReviewerProvider:
    """One strict UTF-8 JSON callable with an independent extraction path."""

    provider: str
    model: str
    callable: Callable[[bytes], Any]

    def __post_init__(self) -> None:
        _identity(self.provider, self.model)
        if not callable(self.callable):
            raise TypeError("provider callable required")

    def invoke(self, request: Mapping[str, Any]) -> ReviewerProviderInvocation:
        encoded_request = _json_bytes(request)
        try:
            native = self.callable(encoded_request)
        except Exception as exc:
            raise ReviewerProviderTransportError("JSON provider callable failed") from exc
        if type(native) is str:
            try:
                raw = native.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise ReviewerProviderResponseError("invalid native response Unicode") from exc
        elif type(native) is bytes:
            raw = native
        else:
            raise ReviewerProviderResponseError("native UTF-8 JSON response required")
        if len(raw) > NATIVE_RESPONSE_BYTES:
            raise ReviewerProviderResponseError("native response exceeds bound")
        try:
            wrapper = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                                 parse_constant=_constant, parse_float=_finite_float)
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise ReviewerProviderResponseError("invalid native UTF-8 JSON response") from exc
        if (type(wrapper) is not dict or set(wrapper) != _WRAPPER_FIELDS
                or type(wrapper["semantic_response"]) is not dict):
            raise ReviewerProviderResponseError("native JSON wrapper fields invalid")
        attribution = _attribution(wrapper["attribution"], self.provider, self.model)
        return ReviewerProviderInvocation(wrapper["semantic_response"], MappingProxyType(attribution))


@dataclass(frozen=True, slots=True)
class ReviewerAttemptResult:
    decision: Mapping[str, Any]
    attribution: Mapping[str, str | None]


class ReviewerAttemptError(Exception):
    """Bounded pre-REVIEW operational failure, with no lifecycle meaning."""

    def __init__(self, reason_code: str, phase: str, invocation_count: int):
        self.reason_code = reason_code
        self.phase = phase
        self.invocation_count = invocation_count
        super().__init__(f"{reason_code} at {phase} after {invocation_count} invocation(s)")


def attempt(
    provider: ReviewerProvider, packet: DecisionPacket,
    review_scope: Mapping[str, Any], review_material_package: Mapping[str, Any],
    reviewer_procedure_package: Mapping[str, Any],
    reviewer_return_contract_package: Mapping[str, Any],
    external_bindings: Mapping[str, Any], *,
    prior_review: Mapping[str, Any] | None = None,
) -> ReviewerAttemptResult:
    """Construct one P3 request, invoke once, and validate one transient decision."""
    try:
        request = construct_request(
            packet, review_scope, review_material_package,
            reviewer_procedure_package, reviewer_return_contract_package,
            external_bindings, prior_review=prior_review,
        )
        configured = (provider.provider, provider.model)
        _identity(*configured)
        invoke = provider.invoke
        if not callable(invoke):
            raise TypeError("provider invocation required")
    except Exception:
        raise ReviewerAttemptError("PROTOCOL_INPUT_INVALID", "INPUT", 0) from None

    def identity_changed() -> bool:
        try:
            return (provider.provider, provider.model) != configured
        except Exception:
            return True

    if identity_changed():
        raise ReviewerAttemptError("PROVIDER_ATTRIBUTION_MISMATCH", "INVOKE", 0)
    # The sole invocation is counted before control enters provider code.
    try:
        invocation = invoke(deepcopy(request))
    except ReviewerProviderAttributionMismatch:
        failure = "PROVIDER_ATTRIBUTION_MISMATCH"
    except ReviewerProviderResponseError:
        failure = "PROVIDER_RESPONSE_INVALID"
    except Exception:
        failure = "PROVIDER_TRANSPORT_FAILURE"
    else:
        failure = None
    if identity_changed():
        failure = "PROVIDER_ATTRIBUTION_MISMATCH"
    if failure is not None:
        raise ReviewerAttemptError(failure, "INVOKE", 1)

    try:
        if type(invocation) is not ReviewerProviderInvocation:
            raise ReviewerProviderResponseError("provider invocation result required")
        attribution = _attribution(invocation.attribution, *configured)
        if identity_changed():
            raise ReviewerProviderAttributionMismatch("configured identity changed")
        decision = validate_response(request, invocation.semantic_response)
    except ReviewerProviderAttributionMismatch:
        failure = "PROVIDER_ATTRIBUTION_MISMATCH"
    except Exception:
        failure = "PROVIDER_RESPONSE_INVALID"
    else:
        failure = None
    if identity_changed():
        failure = "PROVIDER_ATTRIBUTION_MISMATCH"
    if failure is not None:
        raise ReviewerAttemptError(failure, "RESPONSE", 1)
    return ReviewerAttemptResult(decision, MappingProxyType(attribution))


__all__ = [
    "ReviewerProvider", "ReviewerProviderInvocation", "ReviewerAttemptResult",
    "ReviewerAttemptError", "ReviewerProviderTransportError",
    "ReviewerProviderResponseError", "ReviewerProviderAttributionMismatch",
    "MappingReviewerProvider", "JsonReviewerProvider", "attempt",
]
