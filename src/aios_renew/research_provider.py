"""One-shot transient ResearchProvider invocation over the reviewed RA-5 protocol."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from typing import Any, Callable, Mapping, Protocol

from .research_contract import ResearchContractError
from .research_provider_protocol import (
    construct_research_provider_return, validate_research_provider_request,
)


NATIVE_RESPONSE_BYTES = 4194304
_ATTRIBUTION_FIELDS = {"provider", "model", "session_id", "invocation_id"}
_WRAPPER_FIELDS = {"semantic_response", "attribution"}


class ResearchProviderResponseError(ValueError):
    """Native response or attribution violates the closed wrapper contract."""


class ResearchProviderAttributionMismatch(ResearchProviderResponseError):
    """The configured provider/model differs from returned attribution."""


class ResearchProviderTransportError(Exception):
    """The supplied native callable failed before returning."""


class ResearchInvocationError(Exception):
    """Bounded failure of this invocation only, without semantic closure."""

    def __init__(self, reason_code: str, phase: str, invocation_count: int):
        self.reason_code = reason_code
        self.phase = phase
        self.invocation_count = invocation_count
        super().__init__(f"{reason_code} at {phase} after {invocation_count} invocation(s)")


def _text(value: Any, limit: int, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or not value:
        raise ResearchProviderResponseError("invalid attribution text")
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeError as exc:
        raise ResearchProviderResponseError("invalid attribution Unicode") from exc
    if size > limit:
        raise ResearchProviderResponseError("attribution exceeds bound")
    return value


def _json(value: Any, limit: int) -> bytes:
    def walk(item: Any, depth: int) -> None:
        if depth > 32:
            raise ResearchProviderResponseError("native response too deep")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ResearchProviderResponseError("native key must be text")
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
        elif type(item) is list:
            for child in item:
                walk(child, depth + 1)
        elif type(item) is float:
            if not math.isfinite(item):
                raise ResearchProviderResponseError("non-finite native number")
        elif type(item) not in (str, int, bool, type(None)):
            raise ResearchProviderResponseError("native response is not strict JSON")
    walk(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ResearchProviderResponseError("invalid native UTF-8 JSON") from exc
    if len(encoded) > limit:
        raise ResearchProviderResponseError("native response exceeds byte bound")
    return encoded


def _attribution(value: Any, provider: str, model: str) -> dict[str, str | None]:
    if type(value) is not dict or set(value) != _ATTRIBUTION_FIELDS:
        raise ResearchProviderResponseError("closed attribution fields required")
    result = {"provider": _text(value["provider"], 512),
              "model": _text(value["model"], 512),
              "session_id": _text(value["session_id"], 2048, True),
              "invocation_id": _text(value["invocation_id"], 2048, True)}
    _json(result, 8192)
    if (result["provider"], result["model"]) != (provider, model):
        raise ResearchProviderAttributionMismatch("provider/model attribution mismatch")
    return result


def _extract(native: Any, provider: str, model: str) -> "ResearchProviderInvocation":
    if type(native) is not dict:
        raise ResearchProviderResponseError("native wrapper must be a mapping")
    _json(native, NATIVE_RESPONSE_BYTES)
    if set(native) != _WRAPPER_FIELDS or type(native["semantic_response"]) is not dict:
        raise ResearchProviderResponseError("native wrapper fields invalid")
    attribution = _attribution(native["attribution"], provider, model)
    return ResearchProviderInvocation(deepcopy(native["semantic_response"]), attribution)


@dataclass(frozen=True, slots=True)
class ResearchProviderInvocation:
    semantic_response: Mapping[str, Any]
    attribution: Mapping[str, str | None]


class ResearchProvider(Protocol):
    provider: str
    model: str

    def invoke(self, request: Mapping[str, Any]) -> ResearchProviderInvocation: ...


@dataclass(frozen=True, slots=True)
class MappingResearchProvider:
    provider: str
    model: str
    callable: Callable[[Mapping[str, Any]], Any]

    def __post_init__(self) -> None:
        _text(self.provider, 512)
        _text(self.model, 512)
        if not callable(self.callable):
            raise TypeError("provider callable required")

    def invoke(self, request: Mapping[str, Any]) -> ResearchProviderInvocation:
        try:
            native = self.callable(deepcopy(request))
        except Exception as exc:
            raise ResearchProviderTransportError("mapping provider failed") from exc
        return _extract(native, self.provider, self.model)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResearchProviderResponseError("duplicate native JSON key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ResearchProviderResponseError("non-finite native JSON number")


@dataclass(frozen=True, slots=True)
class JsonResearchProvider:
    provider: str
    model: str
    callable: Callable[[bytes], Any]

    def __post_init__(self) -> None:
        _text(self.provider, 512)
        _text(self.model, 512)
        if not callable(self.callable):
            raise TypeError("provider callable required")

    def invoke(self, request: Mapping[str, Any]) -> ResearchProviderInvocation:
        encoded = _json(request, 16777216)
        try:
            native = self.callable(encoded)
        except Exception as exc:
            raise ResearchProviderTransportError("JSON provider failed") from exc
        if type(native) is str:
            try:
                raw = native.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise ResearchProviderResponseError("invalid native Unicode") from exc
        elif type(native) is bytes:
            raw = native
        else:
            raise ResearchProviderResponseError("native UTF-8 JSON required")
        if len(raw) > NATIVE_RESPONSE_BYTES:
            raise ResearchProviderResponseError("native response exceeds byte bound")
        try:
            decoded = json.loads(raw.decode("utf-8", "strict"),
                                 object_pairs_hook=_pairs, parse_constant=_constant)
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise ResearchProviderResponseError("invalid native UTF-8 JSON") from exc
        return _extract(decoded, self.provider, self.model)


@dataclass(frozen=True, slots=True)
class ResearchInvocationResult:
    semantic_return: Mapping[str, Any]
    attribution: Mapping[str, str | None]


def invoke_research_provider(
    provider: ResearchProvider, request: Any, *, stage1_request: Any = None,
    stage1_return: Any = None, predecessor_validation_witness: Any = None,
) -> ResearchInvocationResult:
    """Validate exact RA-5 request, invoke one provider once, derive RA-5 return."""
    count = 0
    try:
        configured = (provider.provider, provider.model)
        _text(configured[0], 512)
        _text(configured[1], 512)
        if not callable(provider.invoke):
            raise TypeError("provider invoke required")
        bound = validate_research_provider_request(
            request, stage1_request=stage1_request, stage1_return=stage1_return,
            predecessor_validation_witness=predecessor_validation_witness)
    except (ResearchContractError, ResearchProviderResponseError, AttributeError, TypeError, ValueError):
        raise ResearchInvocationError("PROTOCOL_INPUT_INVALID", "INPUT", count) from None
    phase = bound["request_mode"]
    def identity_changed() -> bool:
        try:
            return (provider.provider, provider.model) != configured
        except Exception:
            return True

    if identity_changed():
        raise ResearchInvocationError("PROVIDER_ATTRIBUTION_MISMATCH", phase, count)
    count = 1
    try:
        native = provider.invoke(deepcopy(bound))
    except ResearchProviderAttributionMismatch:
        raise ResearchInvocationError("PROVIDER_ATTRIBUTION_MISMATCH", phase, count) from None
    except ResearchProviderResponseError:
        raise ResearchInvocationError("PROVIDER_RESPONSE_INVALID", phase, count) from None
    except Exception:
        raise ResearchInvocationError("PROVIDER_TRANSPORT_FAILURE", phase, count) from None
    if identity_changed():
        raise ResearchInvocationError("PROVIDER_ATTRIBUTION_MISMATCH", phase, count)
    try:
        if type(native) is not ResearchProviderInvocation:
            raise ResearchProviderResponseError("invocation wrapper required")
        attribution = _attribution(dict(native.attribution), *configured)
        _json({"semantic_response": native.semantic_response, "attribution": attribution},
              NATIVE_RESPONSE_BYTES)
        if identity_changed():
            raise ResearchProviderAttributionMismatch("provider/model changed")
        semantic = construct_research_provider_return(
            native.semantic_response, bound, stage1_request=stage1_request,
            stage1_return=stage1_return,
            predecessor_validation_witness=predecessor_validation_witness)
    except ResearchProviderAttributionMismatch:
        raise ResearchInvocationError("PROVIDER_ATTRIBUTION_MISMATCH", phase, count) from None
    except (ResearchProviderResponseError, ResearchContractError, TypeError, ValueError):
        raise ResearchInvocationError("PROVIDER_RESPONSE_INVALID", phase, count) from None
    return ResearchInvocationResult(semantic, attribution)


__all__ = ["ResearchProvider", "ResearchProviderInvocation", "MappingResearchProvider",
           "JsonResearchProvider", "ResearchInvocationResult", "ResearchInvocationError",
           "ResearchProviderResponseError", "ResearchProviderAttributionMismatch",
           "ResearchProviderTransportError", "invoke_research_provider"]
