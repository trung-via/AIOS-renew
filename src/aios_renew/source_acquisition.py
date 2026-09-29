"""Pure RA-2 acquisition airlock for caller-supplied, untrusted source material.

These identities describe bounded acquisition material, not source authority,
research conclusions, lifecycle state, or permission to access a source.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import ipaddress
import json
import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit

from .research_contract import (
    ResearchContractError, validate_acquisition_request, validate_research_brief,
)


_OBSERVATION = frozenset({"format", "version", "kind", "brief_fingerprint",
    "request_fingerprint", "request_source_family", "representation_kind",
    "provenance", "content", "instruction_trust", "observation_fingerprint"})
_ATTEMPT = frozenset({"format", "version", "kind", "brief_fingerprint",
    "request_fingerprint", "outcome", "observations", "failure", "attribution",
    "attempt_fingerprint"})
_PROVENANCE = frozenset({"effective_locator", "stable_source_id", "resolution_chain",
                         "version_basis", "retrieved_at", "access_scope"})
_REPRESENTATIONS = frozenset({"SOURCE_CONTENT", "DISCOVERY_SNIPPET", "SOURCE_METADATA"})
_FAILURES = frozenset({"ACQUISITION_UNAVAILABLE", "ACQUISITION_ACCESS_DENIED",
    "ACQUISITION_NOT_FOUND", "ACQUISITION_RESPONSE_INVALID",
    "ACQUISITION_ATTRIBUTION_MISMATCH"})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_KIND = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_LOCAL = re.compile(r"^(?:[A-Za-z]:[\\/]|[/\\]|~[/\\]|\.\.[/\\]|\\\\)")
_SECRET = re.compile(
    r"(?:-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----|"
    r"\b(?:bearer|basic)\s+[A-Za-z0-9+/._~=-]{8,}|"
    r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|"
    r"client[_-]?secret|private[_-]?key|credential|authorization|cookie|session)"
    r"\s*[:=]\s*[^\s,;]{4,}|"
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})\b)",
    re.IGNORECASE,
)
_USERINFO = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@")
_NATIVE_PAYLOAD = re.compile(r"(?:\b(?:traceback|stack trace|exception|set-cookie|"
    r"authorization header|session cookie)\b|\b(?:http/[12](?:\.[01])?)\s+[45][0-9]{2}\b)",
    re.IGNORECASE)
_QUERY_SECRETS = frozenset({"password", "passwd", "secret", "client_secret", "token",
    "access_token", "auth_token", "api_key", "apikey", "key", "auth",
    "authorization", "credential", "access_key", "signature", "sig", "jwt",
    "session_id", "cookie"})


def _mapping(value: Any, fields: frozenset[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or len(value) != len(fields) or set(value) != fields:
        raise ResearchContractError(f"{name} must contain exactly its closed fields")
    return value


def _literal(value: Any, expected: Any, name: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise ResearchContractError(f"invalid {name}")


def _json_bytes(value: Any, ceiling: int) -> bytes:
    """Enforce strict JSON, a shallow bounded shape, and an encoded byte ceiling."""
    def walk(item: Any, depth: int) -> None:
        if depth > 8:
            raise ResearchContractError("material is too deeply nested")
        if type(item) is dict:
            if len(item) > 16 or any(type(key) is not str for key in item):
                raise ResearchContractError("invalid JSON mapping")
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
        elif type(item) is list:
            if len(item) > 32:
                raise ResearchContractError("over-bound JSON list")
            for child in item:
                walk(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ResearchContractError("material must be strict JSON")
    walk(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ResearchContractError("material must be strict UTF-8 JSON") from exc
    if len(encoded) > ceiling:
        raise ResearchContractError("material exceeds JSON byte limit")
    return encoded


def _text(value: Any, name: str, ceiling: int, *, nonempty: bool = True,
          guarded: bool = True) -> str:
    if type(value) is not str:
        raise ResearchContractError(f"{name} must be text")
    result = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    try:
        size = len(result.encode("utf-8", "strict"))
    except UnicodeError as exc:
        raise ResearchContractError(f"{name} has invalid Unicode") from exc
    if size > ceiling or (nonempty and not result.strip()):
        raise ResearchContractError(f"{name} is empty or over-bound")
    if guarded and (any(ord(ch) < 32 and ch not in "\n\t" for ch in result)
                    or "\x7f" in result or _SECRET.search(result) or _USERINFO.search(result)):
        raise ResearchContractError(f"{name} contains unsafe control or credential material")
    return result


def _locator(value: Any) -> str:
    result = _text(value, "locator", 4096)
    # Decode a bounded number of encoding layers to reject obvious disguised
    # local paths, userinfo and credential parameters. This is not DNS safety.
    decoded = result
    for _ in range(3):
        further = unquote(decoded)
        if further == decoded:
            break
        decoded = further
    if (_LOCAL.match(decoded) or re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", decoded)
            or _SECRET.search(decoded) or _USERINFO.search(decoded)
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded)
            or decoded.lower().startswith(("file:", "smb:"))):
        raise ResearchContractError("unsafe or machine-local locator")
    try:
        parts = urlsplit(decoded)
        if parts.scheme:
            if parts.scheme.lower() not in {"http", "https", "git", "ssh"} or not parts.hostname:
                raise ResearchContractError("unsupported locator scheme")
            if any(ch.isspace() for ch in decoded):
                raise ResearchContractError("whitespace in URL locator")
            _ = parts.port
            if parts.username is not None or parts.password is not None:
                raise ResearchContractError("URL userinfo is forbidden")
            host = parts.hostname.lower()
            if host == "localhost" or host.endswith(".localhost"):
                raise ResearchContractError("machine-local locator")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    raise ResearchContractError("non-public literal IP")
            for pair in parts.query.split("&"):
                if pair.split("=", 1)[0].lower() in _QUERY_SECRETS:
                    raise ResearchContractError("credential query parameter")
        elif decoded.startswith("//"):
            raise ResearchContractError("ambiguous network locator")
        elif re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", decoded):
            raise ResearchContractError("unsupported locator scheme")
    except ValueError as exc:
        if isinstance(exc, ResearchContractError):
            raise
        raise ResearchContractError("malformed locator") from exc
    return result


def _provenance(value: Any) -> dict[str, Any]:
    source = _mapping(value, _PROVENANCE, "provenance")
    locator = None if source["effective_locator"] is None else _locator(source["effective_locator"])
    stable = (None if source["stable_source_id"] is None else
              _text(source["stable_source_id"], "stable_source_id", 2048))
    if locator is None and stable is None:
        raise ResearchContractError("provenance requires a locator or stable identity")
    chain = source["resolution_chain"]
    if type(chain) is not list or len(chain) > 8:
        raise ResearchContractError("resolution_chain must be a bounded list")
    chain = [_locator(item) for item in chain]
    if len(chain) != len(set(chain)) or (locator is None and chain) or (locator is not None and
            (not chain or chain[-1] != locator)):
        raise ResearchContractError("resolution_chain is inconsistent or duplicate")
    facts = source["version_basis"]
    if type(facts) is not list or len(facts) > 8:
        raise ResearchContractError("version_basis must be a bounded list")
    normalized_facts = []
    for item in facts:
        record = _mapping(item, frozenset({"kind", "value"}), "version fact")
        kind = record["kind"]
        if type(kind) is not str or _KIND.fullmatch(kind) is None:
            raise ResearchContractError("invalid version kind")
        normalized_facts.append({"kind": kind,
            "value": _text(record["value"], "version value", 2048)})
    keys = [_json_bytes(item, 4096) for item in normalized_facts]
    if len(keys) != len(set(keys)):
        raise ResearchContractError("duplicate normalized version fact")
    normalized_facts = [item for _, item in sorted(zip(keys, normalized_facts), key=lambda pair: pair[0])]
    timestamp = source["retrieved_at"]
    if type(timestamp) is not str or _UTC.fullmatch(timestamp) is None:
        raise ResearchContractError("retrieved_at requires exact UTC seconds")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchContractError("invalid retrieved_at calendar time") from exc
    scope = source["access_scope"]
    if type(scope) is not str or scope not in {"PUBLIC", "AUTHORIZED_PRIVATE"}:
        raise ResearchContractError("invalid access_scope")
    return {"effective_locator": locator, "stable_source_id": stable,
        "resolution_chain": chain, "version_basis": normalized_facts,
        "retrieved_at": timestamp, "access_scope": scope}


def _bound(brief: Any, request: Any, targets: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    valid_brief = validate_research_brief(brief)
    valid_request = validate_acquisition_request(request, valid_brief, targets)
    return valid_brief, valid_request


def _identity(body: dict[str, Any], field: str, ceiling: int) -> dict[str, Any]:
    result = dict(body)
    result[field] = hashlib.sha256(_json_bytes(body, ceiling)).hexdigest()
    _json_bytes(result, ceiling)
    return result


def _observation(value: Any, brief: Any, request: Any, targets: Any,
                 validate: bool) -> dict[str, Any]:
    bound_brief, bound_request = _bound(brief, request, targets)
    fields = _OBSERVATION if validate else _OBSERVATION - {"observation_fingerprint"}
    source = _mapping(value, fields, "Source Observation")
    for name, expected in (("format", "AIOS_SOURCE_OBSERVATION"), ("version", 1),
                           ("kind", "SOURCE_OBSERVATION"),
                           ("brief_fingerprint", bound_brief["brief_fingerprint"]),
                           ("request_fingerprint", bound_request["request_fingerprint"]),
                           ("request_source_family", bound_request["source_family"]),
                           ("instruction_trust", "UNTRUSTED")):
        _literal(source[name], expected, name)
    representation = source["representation_kind"]
    if type(representation) is not str or representation not in _REPRESENTATIONS:
        raise ResearchContractError("unsupported observation representation")
    provenance = _provenance(source["provenance"])
    content = _mapping(source["content"], frozenset({"text", "content_sha256"}), "content")
    observed_text = _text(content["text"], "content.text", 262144,
                          nonempty=False, guarded=False)
    if representation != "SOURCE_METADATA" and not observed_text:
        raise ResearchContractError("source content or snippet must be non-empty")
    if representation == "SOURCE_METADATA" and not observed_text and not (
            provenance["stable_source_id"] or provenance["version_basis"]):
        raise ResearchContractError("empty metadata requires stable identity or version fact")
    digest = hashlib.sha256(observed_text.encode("utf-8")).hexdigest()
    _literal(content["content_sha256"], digest, "content_sha256")
    body = {"format": "AIOS_SOURCE_OBSERVATION", "version": 1,
        "kind": "SOURCE_OBSERVATION", "brief_fingerprint": bound_brief["brief_fingerprint"],
        "request_fingerprint": bound_request["request_fingerprint"],
        "request_source_family": bound_request["source_family"],
        "representation_kind": representation, "provenance": provenance,
        "content": {"text": observed_text, "content_sha256": digest},
        "instruction_trust": "UNTRUSTED"}
    result = _identity(body, "observation_fingerprint", 327680)
    if validate:
        _literal(source["observation_fingerprint"], result["observation_fingerprint"],
                 "observation_fingerprint")
    return result


def construct_source_observation(material: Any, brief: Any, request: Any,
                                 targets: Any = None) -> dict[str, Any]:
    """Normalize fingerprint-free source material bound to exact RA-1 inputs."""
    return _observation(material, brief, request, targets, False)


def validate_source_observation(material: Any, brief: Any, request: Any,
                                targets: Any = None) -> dict[str, Any]:
    """Revalidate and detach an exact content-addressed Source Observation."""
    return _observation(material, brief, request, targets, True)


def _attempt(value: Any, brief: Any, request: Any, targets: Any,
             validate: bool) -> dict[str, Any]:
    bound_brief, bound_request = _bound(brief, request, targets)
    fields = _ATTEMPT if validate else _ATTEMPT - {"attempt_fingerprint"}
    source = _mapping(value, fields, "Acquisition Attempt")
    for name, expected in (("format", "AIOS_ACQUISITION_ATTEMPT"), ("version", 1),
                           ("kind", "ACQUISITION_ATTEMPT"),
                           ("brief_fingerprint", bound_brief["brief_fingerprint"]),
                           ("request_fingerprint", bound_request["request_fingerprint"])):
        _literal(source[name], expected, name)
    outcome = source["outcome"]
    if type(outcome) is not str or outcome not in {"SUCCEEDED", "FAILED"}:
        raise ResearchContractError("invalid acquisition outcome")
    observations = source["observations"]
    if type(observations) is not list or len(observations) > bound_request["bounds"]["max_items"]:
        raise ResearchContractError("observation count exceeds request bound")
    if outcome == "SUCCEEDED":
        if source["failure"] is not None:
            raise ResearchContractError("successful acquisition forbids failure")
        normalized = [validate_source_observation(item, bound_brief, bound_request, targets)
                      for item in observations]
        identities = [item["observation_fingerprint"] for item in normalized]
        if len(identities) != len(set(identities)):
            raise ResearchContractError("duplicate observation")
        if sum(len(item["content"]["text"].encode("utf-8")) for item in normalized) > \
                bound_request["bounds"]["max_total_observation_bytes"]:
            raise ResearchContractError("total observation text exceeds request bound")
        failure = None
    else:
        if observations:
            raise ResearchContractError("failed acquisition forbids observations")
        normalized = []
        failure_source = _mapping(source["failure"], frozenset({"reason_code"}), "failure")
        reason = failure_source["reason_code"]
        if type(reason) is not str or reason not in _FAILURES:
            raise ResearchContractError("invalid operational failure reason")
        failure = {"reason_code": reason}
    attribution = _mapping(source["attribution"],
        frozenset({"adapter_id", "adapter_version", "invocation_id"}), "attribution")
    invocation = attribution["invocation_id"]
    adapter_id = _text(attribution["adapter_id"], "adapter_id", 512)
    adapter_version = _text(attribution["adapter_version"], "adapter_version", 512)
    invocation_id = None if invocation is None else _text(invocation, "invocation_id", 2048)
    if any(_NATIVE_PAYLOAD.search(item) or "\n" in item or "\t" in item for item in
           (adapter_id, adapter_version, invocation_id or "")):
        raise ResearchContractError("attribution contains native payload material")
    body = {"format": "AIOS_ACQUISITION_ATTEMPT", "version": 1,
        "kind": "ACQUISITION_ATTEMPT", "brief_fingerprint": bound_brief["brief_fingerprint"],
        "request_fingerprint": bound_request["request_fingerprint"],
        "outcome": outcome, "observations": normalized, "failure": failure,
        "attribution": {"adapter_id": adapter_id, "adapter_version": adapter_version,
                        "invocation_id": invocation_id}}
    result = _identity(body, "attempt_fingerprint", 786432)
    if validate:
        _literal(source["attempt_fingerprint"], result["attempt_fingerprint"],
                 "attempt_fingerprint")
    return result


def construct_acquisition_attempt(material: Any, brief: Any, request: Any,
                                  targets: Any = None) -> dict[str, Any]:
    """Normalize one fingerprint-free operational acquisition outcome."""
    return _attempt(material, brief, request, targets, False)


def validate_acquisition_attempt(material: Any, brief: Any, request: Any,
                                 targets: Any = None) -> dict[str, Any]:
    """Revalidate an exact same-request content-addressed acquisition outcome."""
    return _attempt(material, brief, request, targets, True)
