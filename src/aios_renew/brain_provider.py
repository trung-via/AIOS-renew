"""Transient BP5-P3 invocation shell over the reviewed Brain semantic protocol.

All transport callables, packets, packages and freshness material are caller supplied.
No provider result or attempt retains a native response or conversation history.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from .brain_provider_protocol import BrainProviderProtocolError, construct_request, validate_response
from .decision_packet import DecisionPacket


NATIVE_RESPONSE_BYTES = 786432
ATTRIBUTION_BYTES = 8192
_ATTRIBUTION_FIELDS = frozenset({"provider", "model", "session_id", "invocation_id"})
_WRAPPER_FIELDS = frozenset({"semantic_response", "attribution"})


class ProviderTransportError(Exception):
    """The selected callable failed before yielding a native response."""


class ProviderResponseError(ValueError):
    """A native wrapper or its attribution is invalid."""


class ProviderAttributionMismatch(ProviderResponseError):
    """An invocation reports a different provider or model."""


@dataclass(frozen=True, slots=True)
class ProviderInvocation:
    semantic_response: Mapping[str, Any]
    attribution: Mapping[str, str | None]


class BrainProvider(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def invoke(self, request: Mapping[str, Any]) -> ProviderInvocation: ...


def _text(value: Any, ceiling: int, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or not value:
        raise ProviderResponseError("invalid operational attribution field")
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeError as exc:
        raise ProviderResponseError("invalid operational attribution Unicode") from exc
    if size > ceiling:
        raise ProviderResponseError("operational attribution field exceeds bound")
    return value


def _identity(provider: Any, model: Any) -> None:
    _text(provider, 512)
    _text(model, 512)


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ProviderResponseError("native response requires strict JSON mapping grammar") from exc


def _mapping_json(value: Any, depth: int = 0) -> None:
    """Mapping transport admits only native strict JSON values, before P2B semantics."""
    if depth > 32:
        raise ProviderResponseError("native mapping depth exceeds bound")
    if value is None or type(value) in (bool, int, str):
        if type(value) is str:
            try:
                value.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise ProviderResponseError("invalid native mapping Unicode") from exc
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
                raise ProviderResponseError("native mapping key must be text")
            _mapping_json(key, depth + 1)
            _mapping_json(item, depth + 1)
        return
    raise ProviderResponseError("native mapping requires strict JSON grammar")


def _attribution(value: Any, provider: str, model: str) -> dict[str, str | None]:
    if not isinstance(value, Mapping) or set(value) != _ATTRIBUTION_FIELDS:
        raise ProviderResponseError("operational attribution fields do not match closed contract")
    result = {
        "provider": _text(value["provider"], 512),
        "model": _text(value["model"], 512),
        "session_id": _text(value["session_id"], 2048, nullable=True),
        "invocation_id": _text(value["invocation_id"], 2048, nullable=True),
    }
    if len(_json_bytes(result)) > ATTRIBUTION_BYTES:
        raise ProviderResponseError("operational attribution exceeds bound")
    if result["provider"] != provider or result["model"] != model:
        raise ProviderAttributionMismatch("configured provider/model attribution mismatch")
    return result


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderResponseError("duplicate native JSON key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ProviderResponseError("non-finite native JSON number")


@dataclass(frozen=True, slots=True)
class MappingBrainProvider:
    """One mapping-native callable with its own closed wrapper extraction path."""

    provider: str
    model: str
    callable: Callable[[Mapping[str, Any]], Any]

    def __post_init__(self) -> None:
        _identity(self.provider, self.model)
        if not callable(self.callable):
            raise TypeError("provider callable required")

    def invoke(self, request: Mapping[str, Any]) -> ProviderInvocation:
        try:
            native = self.callable(deepcopy(request))
        except Exception as exc:
            raise ProviderTransportError("mapping provider callable failed") from exc
        if type(native) is not dict:
            raise ProviderResponseError("native mapping wrapper required")
        _mapping_json(native)
        if len(_json_bytes(native)) > NATIVE_RESPONSE_BYTES:
            raise ProviderResponseError("native response exceeds bound")
        if set(native) != _WRAPPER_FIELDS or type(native["semantic_response"]) is not dict:
            raise ProviderResponseError("native mapping wrapper fields invalid")
        attribution = _attribution(native["attribution"], self.provider, self.model)
        return ProviderInvocation(deepcopy(native["semantic_response"]), MappingProxyType(attribution))


@dataclass(frozen=True, slots=True)
class JsonBrainProvider:
    """One UTF-8 JSON callable with an independent native extraction path."""

    provider: str
    model: str
    callable: Callable[[bytes], Any]

    def __post_init__(self) -> None:
        _identity(self.provider, self.model)
        if not callable(self.callable):
            raise TypeError("provider callable required")

    def invoke(self, request: Mapping[str, Any]) -> ProviderInvocation:
        encoded_request = _json_bytes(request)
        try:
            native = self.callable(encoded_request)
        except Exception as exc:
            raise ProviderTransportError("JSON provider callable failed") from exc
        if type(native) is str:
            try:
                raw = native.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise ProviderResponseError("invalid native response Unicode") from exc
        elif type(native) is bytes:
            raw = native
        else:
            raise ProviderResponseError("native UTF-8 JSON response required")
        if len(raw) > NATIVE_RESPONSE_BYTES:
            raise ProviderResponseError("native response exceeds bound")
        try:
            wrapper = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                                 parse_constant=_constant)
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise ProviderResponseError("invalid native UTF-8 JSON response") from exc
        if type(wrapper) is not dict or set(wrapper) != _WRAPPER_FIELDS or type(wrapper["semantic_response"]) is not dict:
            raise ProviderResponseError("native JSON wrapper fields invalid")
        attribution = _attribution(wrapper["attribution"], self.provider, self.model)
        return ProviderInvocation(wrapper["semantic_response"], MappingProxyType(attribution))


@dataclass(frozen=True, slots=True)
class AttemptResult:
    decision: Mapping[str, Any]
    attributions: tuple[Mapping[str, str | None], ...]


class BrainAttemptError(Exception):
    """Bounded pre-handoff failure, with no lifecycle interpretation."""

    def __init__(self, reason_code: str, phase: str, invocation_count: int):
        self.reason_code = reason_code
        self.phase = phase
        self.invocation_count = invocation_count
        super().__init__(f"{reason_code} at {phase} after {invocation_count} invocation(s)")


def attempt(provider: BrainProvider, packet: DecisionPacket,
            return_contract_package: Mapping[str, Any], external_bindings: Mapping[str, Any],
            audit_profile_package: Mapping[str, Any] | None = None,
            fresh_packet_supplier: Callable[[], DecisionPacket] | None = None) -> AttemptResult:
    """Invoke DIRECT once or an audited attempt twice, with one freshness gate."""
    count = 0
    phase = "INPUT"
    try:
        if not isinstance(packet, DecisionPacket):
            raise ValueError("DecisionPacket required")
        configured = (provider.provider, provider.model)
        _identity(*configured)
        if not callable(provider.invoke):
            raise ValueError("provider invocation required")
        mode = "DIRECT" if packet.as_dict()["selected_flow"] == "DIAGNOSTIC" else "AUDIT_CONSTRUCT"
        if mode != "DIRECT" and not callable(fresh_packet_supplier):
            raise ValueError("fresh packet supplier required")
        first_request = construct_request(packet, return_contract_package, external_bindings,
                                          audit_profile_package, request_mode=mode)
    except (BrainProviderProtocolError, ProviderResponseError, AttributeError, KeyError, TypeError, ValueError):
        invalid_input = True
    else:
        invalid_input = False
    if invalid_input:
        raise BrainAttemptError("PROTOCOL_INPUT_INVALID", phase, count)

    def invoke(request: Mapping[str, Any], at_phase: str) -> tuple[Mapping[str, Any], Mapping[str, str | None]]:
        nonlocal count
        def identity_changed() -> bool:
            try:
                return (provider.provider, provider.model) != configured
            except Exception:
                return True

        if identity_changed():
            raise BrainAttemptError("PROVIDER_ATTRIBUTION_MISMATCH", at_phase, count)
        count += 1
        try:
            result = provider.invoke(deepcopy(request))
        except ProviderAttributionMismatch:
            failed = "PROVIDER_ATTRIBUTION_MISMATCH"
        except ProviderResponseError:
            failed = "PROVIDER_RESPONSE_INVALID"
        except Exception:
            failed = "PROVIDER_TRANSPORT_FAILURE"
        else:
            failed = None
        if identity_changed():
            failed = "PROVIDER_ATTRIBUTION_MISMATCH"
        if failed is not None:
            raise BrainAttemptError(failed, at_phase, count)
        try:
            if type(result) is not ProviderInvocation:
                raise ProviderResponseError("provider invocation result required")
            attribution = _attribution(result.attribution, *configured)
            if identity_changed():
                raise ProviderAttributionMismatch("configured provider/model changed")
            decision = validate_response(request, result.semantic_response)
        except ProviderAttributionMismatch:
            failed = "PROVIDER_ATTRIBUTION_MISMATCH"
        except (ProviderResponseError, BrainProviderProtocolError, TypeError, ValueError, RecursionError):
            failed = "PROVIDER_RESPONSE_INVALID"
        else:
            failed = None
        if failed is not None:
            raise BrainAttemptError(failed, at_phase, count)
        return decision, MappingProxyType(attribution)

    phase = mode
    first, attribution1 = invoke(first_request, phase)
    if mode == "DIRECT":
        return AttemptResult(first, (attribution1,))
    phase = "FRESH_PACKET"
    try:
        fresh = fresh_packet_supplier()
    except Exception:
        unavailable = True
    else:
        unavailable = False
    if unavailable:
        raise BrainAttemptError("FRESH_PACKET_UNAVAILABLE", phase, count)
    if not isinstance(fresh, DecisionPacket):
        raise BrainAttemptError("FRESH_PACKET_INVALID", phase, count)
    try:
        if fresh.packet_fingerprint != first_request["decision_packet"]["packet_fingerprint"]:
            raise BrainAttemptError("STALE_BEFORE_STAGE2", phase, count)
        second_request = construct_request(fresh, first_request["return_contract_package"],
                                           first_request["external_bindings"],
                                           first_request["audit_profile_package"], request_mode="AUDIT_RECONCILE",
                                           stage1_decision=first)
    except BrainAttemptError:
        raise
    except (BrainProviderProtocolError, AttributeError, KeyError, TypeError, ValueError):
        invalid_input = True
    else:
        invalid_input = False
    if invalid_input:
        raise BrainAttemptError("PROTOCOL_INPUT_INVALID", phase, count)
    second, attribution2 = invoke(second_request, "AUDIT_RECONCILE")
    return AttemptResult(second, (attribution1, attribution2))


__all__ = ["BrainProvider", "ProviderInvocation", "AttemptResult", "BrainAttemptError",
           "ProviderTransportError", "ProviderResponseError", "ProviderAttributionMismatch",
           "MappingBrainProvider", "JsonBrainProvider", "attempt"]
