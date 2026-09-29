"""RA-5 explicit single-adapter acquisition boundary; no discovery or I/O here."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .research_contract import (
    ResearchContractError, validate_research_brief, validate_acquisition_request,
    validate_challenge_target,
)
from .source_acquisition import construct_source_observation, construct_acquisition_attempt


_MANIFEST = {"format", "version", "kind", "adapter_id", "adapter_version",
             "transport_class", "source_families", "access_scopes", "manifest_fingerprint"}
_INVOCATION = {"format", "version", "kind", "research_brief", "acquisition_request",
               "challenge_targets", "adapter_ref", "access_mode", "authorization_context_ref",
               "invocation_id", "invocation_fingerprint"}
_NATIVE = {"format", "version", "kind", "invocation_fingerprint", "request_fingerprint",
           "adapter_ref", "status", "observations", "failure", "receipt"}
_RESULT = {"format", "version", "kind", "invocation_fingerprint", "adapter_ref",
           "adapter_failure_code", "acquisition_attempt", "receipt", "destination_hops",
           "result_fingerprint"}
_RECEIPT = {"native_operation_count", "retry_count", "pagination_count",
            "query_expansion_count", "fallback_count"}
_FAILURES = {"TOOL_UNAVAILABLE": "ACQUISITION_UNAVAILABLE",
             "ACCESS_DENIED": "ACQUISITION_ACCESS_DENIED",
             "UNSAFE_DESTINATION": "ACQUISITION_ACCESS_DENIED",
             "NOT_FOUND": "ACQUISITION_NOT_FOUND",
             "RESPONSE_INVALID": "ACQUISITION_RESPONSE_INVALID",
             "ATTRIBUTION_MISMATCH": "ACQUISITION_ATTRIBUTION_MISMATCH"}
_PUBLIC_FAMILIES = {"OFFICIAL_DOCS", "SPECIFICATION", "PAPER_REPORT", "PUBLIC_WEB"}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SECRET = re.compile(r"(?:\b(?:token|secret|password|passwd|credential|authorization|cookie|session|api[_-]?key)\s*[:=]|\b(?:bearer|basic)\s+|-----BEGIN .*PRIVATE KEY|\b(?:gh[pousr]_|sk-)[A-Za-z0-9]{20,})", re.I)
_QUERY_SECRET = {"password", "passwd", "secret", "client_secret", "token", "access_token",
                 "auth_token", "api_key", "apikey", "key", "auth", "authorization",
                 "credential", "access_key", "signature", "sig", "jwt", "session_id", "cookie"}


def _fields(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ResearchContractError(f"invalid {name} fields")
    return value


def _json(value: Any, ceiling: int, depth_limit: int = 24) -> bytes:
    def walk(item: Any, depth: int) -> None:
        if depth > depth_limit:
            raise ResearchContractError("adapter material too deep")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ResearchContractError("non-text JSON key")
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
        elif type(item) is list:
            for child in item:
                walk(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ResearchContractError("non-JSON adapter material")
    walk(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ResearchContractError("invalid strict JSON") from exc
    if len(encoded) > ceiling:
        raise ResearchContractError("adapter material exceeds byte limit")
    return encoded


def _identity(body: dict[str, Any], field: str, ceiling: int) -> dict[str, Any]:
    result = dict(body, **{field: hashlib.sha256(_json(body, ceiling)).hexdigest()})
    _json(result, ceiling)
    return result


def _safe_text(value: Any, label: str, limit: int) -> str:
    if type(value) is not str or not value:
        raise ResearchContractError(f"invalid {label}")
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeError as exc:
        raise ResearchContractError(f"invalid {label} Unicode") from exc
    if size > limit:
        raise ResearchContractError(f"invalid {label}")
    if (any(ord(ch) < 32 or ord(ch) == 127 for ch in value) or _SECRET.search(value)
            or re.search(r"(?:^[\\/~]|(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\.\.[\\/]|%[A-Za-z_]+%|\$\{)", value)):
        raise ResearchContractError(f"unsafe {label}")
    return value


def _public_url(value: Any) -> str:
    url = _safe_text(value, "public URL", 4096)
    decoded = url
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    try:
        parts = urlsplit(decoded)
        if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or "@" in parts.netloc or any(ch.isspace() for ch in decoded)):
            raise ResearchContractError("unsafe public URL")
        _ = parts.port
        host = parts.hostname.lower()
        if host == "localhost" or host.endswith(".localhost"):
            raise ResearchContractError("localhost is unsafe")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not _global_address(literal):
                raise ResearchContractError("non-global URL literal")
        for query in parts.query.split("&"):
            if unquote(query.split("=", 1)[0]).lower() in _QUERY_SECRET:
                raise ResearchContractError("credential URL query")
        if _SECRET.search(decoded):
            raise ResearchContractError("credential URL")
    except ValueError as exc:
        if isinstance(exc, ResearchContractError):
            raise
        raise ResearchContractError("malformed public URL") from exc
    return url


def _global_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (address.is_global and not address.is_multicast and not address.is_reserved
            and not address.is_unspecified and not address.is_loopback
            and not address.is_link_local and not address.is_private)


def _manifest(material: Any, validate: bool) -> dict[str, Any]:
    item = _fields(material, _MANIFEST if validate else _MANIFEST - {"manifest_fingerprint"},
                   "adapter manifest")
    if (item["format"], item["version"], item["kind"]) != (
            "AIOS_ACQUISITION_ADAPTER_MANIFEST", 1, "ACQUISITION_ADAPTER_MANIFEST"):
        raise ResearchContractError("invalid adapter manifest header")
    adapter_id = _safe_text(item["adapter_id"], "adapter_id", 128)
    version = _safe_text(item["adapter_version"], "adapter_version", 128)
    if not _SAFE_ID.fullmatch(adapter_id) or not _SAFE_ID.fullmatch(version):
        raise ResearchContractError("invalid adapter identifier")
    transport = item["transport_class"]
    grammar = {"PUBLIC_HTTP": (_PUBLIC_FAMILIES, {"PUBLIC"}),
               "PUBLIC_SEARCH": (_PUBLIC_FAMILIES, {"PUBLIC"}),
               "REPOSITORY_READ": ({"REPOSITORY"}, {"PUBLIC", "AUTHORIZED_PRIVATE"}),
               "AUTHORIZED_CONNECTOR": ({"CONNECTED_SOURCE"}, {"AUTHORIZED_PRIVATE"})}
    if type(transport) is not str or transport not in grammar:
        raise ResearchContractError("unsupported adapter transport")
    families, scopes = item["source_families"], item["access_scopes"]
    if (type(families) is not list or not families or
            any(type(family) is not str for family in families) or
            len(families) != len(set(families))
            or not set(families) <= grammar[transport][0]
            or type(scopes) is not list or not scopes or
            any(type(scope) is not str for scope in scopes) or
            len(scopes) != len(set(scopes))
            or not set(scopes) <= grammar[transport][1]):
        raise ResearchContractError("invalid transport capability grammar")
    if transport in {"REPOSITORY_READ", "AUTHORIZED_CONNECTOR"} and set(families) != grammar[transport][0]:
        raise ResearchContractError("transport requires exact source family")
    body = {"format": "AIOS_ACQUISITION_ADAPTER_MANIFEST", "version": 1,
            "kind": "ACQUISITION_ADAPTER_MANIFEST", "adapter_id": adapter_id,
            "adapter_version": version, "transport_class": transport,
            "source_families": sorted(families), "access_scopes": sorted(scopes)}
    result = _identity(body, "manifest_fingerprint", 131072)
    if validate and item["manifest_fingerprint"] != result["manifest_fingerprint"]:
        raise ResearchContractError("adapter manifest fingerprint mismatch")
    return result


def construct_acquisition_adapter_manifest(material: Any) -> dict[str, Any]:
    return _manifest(material, False)


def validate_acquisition_adapter_manifest(material: Any) -> dict[str, Any]:
    return _manifest(material, True)


def _invocation(material: Any, manifest: Any, validate: bool) -> dict[str, Any]:
    item = _fields(material, _INVOCATION if validate else _INVOCATION - {"invocation_fingerprint"},
                   "adapter invocation")
    _json(item, 131072)
    bound_manifest = validate_acquisition_adapter_manifest(manifest)
    if (item["format"], item["version"], item["kind"]) != (
            "AIOS_ACQUISITION_ADAPTER_INVOCATION", 1, "ACQUISITION_ADAPTER_INVOCATION"):
        raise ResearchContractError("invalid adapter invocation header")
    brief = validate_research_brief(item["research_brief"])
    if type(item["challenge_targets"]) is not list or len(item["challenge_targets"]) > 16:
        raise ResearchContractError("invalid invocation targets")
    targets = [validate_challenge_target(t, brief) for t in item["challenge_targets"]]
    if len({t["target_fingerprint"] for t in targets}) != len(targets):
        raise ResearchContractError("duplicate invocation targets")
    request = validate_acquisition_request(item["acquisition_request"], brief, targets)
    ref = {key: bound_manifest[key] for key in ("adapter_id", "adapter_version", "manifest_fingerprint")}
    if _fields(item["adapter_ref"], set(ref), "adapter ref") != ref:
        raise ResearchContractError("adapter ref mismatch")
    mode = item["access_mode"]
    if request["source_family"] not in bound_manifest["source_families"] or mode not in bound_manifest["access_scopes"]:
        raise ResearchContractError("explicit adapter lacks request capability")
    context = item["authorization_context_ref"]
    if mode == "PUBLIC":
        if context is not None:
            raise ResearchContractError("public invocation forbids authorization context")
    elif mode == "AUTHORIZED_PRIVATE":
        context = _safe_text(context, "authorization context reference", 256)
        if not _SAFE_ID.fullmatch(context):
            raise ResearchContractError("authorization context must be opaque reference")
    else:
        raise ResearchContractError("invalid access mode")
    transport = bound_manifest["transport_class"]
    if transport == "PUBLIC_HTTP":
        _public_url(request["locator_or_query"])
    if transport == "REPOSITORY_READ" and re.search(r"(?:^[\\/~]|(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\.\.[\\/]|%[A-Za-z_]+%|\$\{)", request["locator_or_query"]):
        raise ResearchContractError("machine-local repository locator")
    invocation_id = _safe_text(item["invocation_id"], "invocation_id", 128)
    if not _SAFE_ID.fullmatch(invocation_id):
        raise ResearchContractError("invalid invocation id")
    body = {"format": "AIOS_ACQUISITION_ADAPTER_INVOCATION", "version": 1,
            "kind": "ACQUISITION_ADAPTER_INVOCATION", "research_brief": brief,
            "acquisition_request": request,
            "challenge_targets": sorted(targets, key=lambda t: t["target_fingerprint"]),
            "adapter_ref": ref, "access_mode": mode,
            "authorization_context_ref": context, "invocation_id": invocation_id}
    result = _identity(body, "invocation_fingerprint", 131072)
    if validate and item["invocation_fingerprint"] != result["invocation_fingerprint"]:
        raise ResearchContractError("adapter invocation fingerprint mismatch")
    return result


def construct_acquisition_adapter_invocation(material: Any, manifest: Any) -> dict[str, Any]:
    return _invocation(material, manifest, False)


def validate_acquisition_adapter_invocation(material: Any, manifest: Any) -> dict[str, Any]:
    return _invocation(material, manifest, True)


class UnsafeDestination(ResearchContractError):
    """A candidate connection or redirect failed the public destination guard."""


def _receipt(value: Any, success: bool) -> dict[str, int]:
    item = _fields(value, _RECEIPT, "adapter receipt")
    if any(type(number) is not int or number < 0 for number in item.values()):
        raise ResearchContractError("invalid adapter counts")
    if (item["native_operation_count"] > 1 or
            (success and item["native_operation_count"] != 1) or
            any(item[name] != 0 for name in _RECEIPT - {"native_operation_count"})):
        raise ResearchContractError("adapter performed unapproved operations")
    return dict(item)


def _native(value: Any, invocation: dict[str, Any], manifest: dict[str, Any],
            hops: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None, dict[str, int]]:
    item = _fields(value, _NATIVE, "adapter native return")
    _json(item, 1048576)
    if (item["format"], item["version"], item["kind"]) != (
            "AIOS_ACQUISITION_ADAPTER_RETURN", 1, "ACQUISITION_ADAPTER_RETURN"):
        raise ResearchContractError("invalid adapter native header")
    if (item["invocation_fingerprint"] != invocation["invocation_fingerprint"] or
            item["request_fingerprint"] != invocation["acquisition_request"]["request_fingerprint"] or
            item["adapter_ref"] != invocation["adapter_ref"]):
        return [], "ATTRIBUTION_MISMATCH", _zero_receipt()
    status = item["status"]
    if status not in {"SUCCEEDED", "FAILED"}:
        raise ResearchContractError("invalid adapter status")
    receipt = _receipt(item["receipt"], status == "SUCCEEDED")
    if status == "FAILED":
        failure = _fields(item["failure"], {"code"}, "adapter failure")
        if item["observations"] != [] or failure["code"] not in _FAILURES:
            raise ResearchContractError("invalid adapter failure")
        return [], failure["code"], receipt
    if item["failure"] is not None:
        raise ResearchContractError("success forbids failure")
    raw_observations = item["observations"]
    request = invocation["acquisition_request"]
    if type(raw_observations) is not list or len(raw_observations) > request["bounds"]["max_items"]:
        raise ResearchContractError("invalid raw observation count")
    transport = manifest["transport_class"]
    if transport == "PUBLIC_HTTP" and not hops:
        raise ResearchContractError("HTTP success lacks approved destination")
    allowed = ({"DISCOVERY_SNIPPET", "SOURCE_METADATA"} if transport == "PUBLIC_SEARCH" else
               {"SOURCE_CONTENT", "SOURCE_METADATA"} if transport in {"PUBLIC_HTTP", "REPOSITORY_READ"} else
               {"SOURCE_CONTENT", "SOURCE_METADATA", "DISCOVERY_SNIPPET"})
    observations = []
    for raw in raw_observations:
        obs = _fields(raw, {"representation_kind", "provenance", "content"}, "raw observation")
        if obs["representation_kind"] not in allowed:
            raise ResearchContractError("representation forbidden by transport")
        provenance = obs["provenance"]
        if type(provenance) is not dict or provenance.get("access_scope") != invocation["access_mode"]:
            raise ResearchContractError("observation access scope mismatch")
        if transport == "PUBLIC_HTTP":
            urls = [hop["url"] for hop in hops]
            if provenance.get("resolution_chain") != urls or provenance.get("effective_locator") != urls[-1]:
                raise ResearchContractError("HTTP observation destination mismatch")
        content = _fields(obs["content"], {"text"}, "raw content")
        if type(content["text"]) is not str:
            raise ResearchContractError("raw content must be text")
        normalized_text = content["text"].replace("\r\n", "\n").replace("\r", "\n")
        import unicodedata
        normalized_text = unicodedata.normalize("NFC", normalized_text)
        material = {"format": "AIOS_SOURCE_OBSERVATION", "version": 1,
                    "kind": "SOURCE_OBSERVATION",
                    "brief_fingerprint": invocation["research_brief"]["brief_fingerprint"],
                    "request_fingerprint": request["request_fingerprint"],
                    "request_source_family": request["source_family"],
                    "representation_kind": obs["representation_kind"],
                    "provenance": provenance,
                    "content": {"text": content["text"], "content_sha256": hashlib.sha256(normalized_text.encode("utf-8", "strict")).hexdigest()},
                    "instruction_trust": "UNTRUSTED"}
        observations.append(construct_source_observation(material, invocation["research_brief"],
                            request, invocation["challenge_targets"]))
    return observations, None, receipt


def _zero_receipt() -> dict[str, int]:
    return {name: 0 for name in _RECEIPT}


def invoke_acquisition_adapter(invocation: Any, manifest: Any,
                               adapter: Callable[[dict[str, Any], Callable[[str, list[str]], dict[str, Any]]], Any]) -> dict[str, Any]:
    """Call the selected adapter once; normalize its native result into RA-2."""
    bound_manifest = validate_acquisition_adapter_manifest(manifest)
    bound = validate_acquisition_adapter_invocation(invocation, bound_manifest)
    if not callable(adapter):
        raise ResearchContractError("explicit adapter callable required")
    hops: list[dict[str, Any]] = []
    rejected = False
    transport = bound_manifest["transport_class"]
    def guard(url: str, resolved_ip_addresses: list[str]) -> dict[str, Any]:
        nonlocal rejected
        if transport != "PUBLIC_HTTP" or len(hops) >= 8:
            rejected = True
            raise UnsafeDestination("destination guard unavailable or over-bound")
        try:
            approved_url = _public_url(url)
            if not hops and approved_url != bound["acquisition_request"]["locator_or_query"]:
                raise ResearchContractError("first public hop differs from request URL")
            if (type(resolved_ip_addresses) is not list or not resolved_ip_addresses
                    or len(resolved_ip_addresses) > 32 or
                    any(type(address) is not str for address in resolved_ip_addresses)):
                raise ResearchContractError("invalid resolved addresses")
            addresses = []
            for address in resolved_ip_addresses:
                parsed = ipaddress.ip_address(address)
                if not _global_address(parsed):
                    raise ResearchContractError("non-global resolved destination")
                addresses.append(str(parsed))
            if len(addresses) != len(set(addresses)):
                raise ResearchContractError("duplicate resolved destination")
            hop = {"url": approved_url, "resolved_ip_addresses": sorted(addresses)}
        except (ResearchContractError, ValueError, UnicodeError) as exc:
            rejected = True
            raise UnsafeDestination("unsafe public destination") from exc
        hops.append(hop)
        return dict(hop)
    try:
        native = adapter(bound, guard)
    except UnsafeDestination:
        observations, failure_code, receipt = [], "UNSAFE_DESTINATION", _zero_receipt()
    except Exception:
        observations, failure_code, receipt = [], ("UNSAFE_DESTINATION" if rejected else "TOOL_UNAVAILABLE"), _zero_receipt()
    else:
        if rejected:
            observations, failure_code, receipt = [], "UNSAFE_DESTINATION", _zero_receipt()
        else:
            try:
                observations, failure_code, receipt = _native(native, bound, bound_manifest, hops)
            except (ResearchContractError, UnicodeError, ValueError, TypeError, RecursionError):
                observations, failure_code, receipt = [], "RESPONSE_INVALID", _zero_receipt()
    def make_attempt(current_observations: list[dict[str, Any]], current_failure: str | None) -> dict[str, Any]:
        return construct_acquisition_attempt({
        "format": "AIOS_ACQUISITION_ATTEMPT", "version": 1, "kind": "ACQUISITION_ATTEMPT",
        "brief_fingerprint": bound["research_brief"]["brief_fingerprint"],
        "request_fingerprint": bound["acquisition_request"]["request_fingerprint"],
        "outcome": "FAILED" if current_failure else "SUCCEEDED", "observations": current_observations,
        "failure": {"reason_code": _FAILURES[current_failure]} if current_failure else None,
        "attribution": {"adapter_id": bound_manifest["adapter_id"],
                        "adapter_version": bound_manifest["adapter_version"],
                        "invocation_id": bound["invocation_id"]}},
        bound["research_brief"], bound["acquisition_request"], bound["challenge_targets"])
    try:
        attempt = make_attempt(observations, failure_code)
    except ResearchContractError:
        if failure_code is not None:
            raise
        failure_code, receipt = "RESPONSE_INVALID", _zero_receipt()
        attempt = make_attempt([], failure_code)
    body = {"format": "AIOS_ACQUISITION_ADAPTER_RESULT", "version": 1,
            "kind": "ACQUISITION_ADAPTER_RESULT",
            "invocation_fingerprint": bound["invocation_fingerprint"],
            "adapter_ref": bound["adapter_ref"], "adapter_failure_code": failure_code,
            "acquisition_attempt": attempt, "receipt": receipt,
            "destination_hops": hops if transport == "PUBLIC_HTTP" else []}
    return _identity(body, "result_fingerprint", 1048576)


__all__ = ["construct_acquisition_adapter_manifest", "validate_acquisition_adapter_manifest",
           "construct_acquisition_adapter_invocation", "validate_acquisition_adapter_invocation",
           "invoke_acquisition_adapter", "UnsafeDestination"]
