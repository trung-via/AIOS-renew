"""Pure RA-1 research intent, challenge-target, and acquisition-request contracts.

Every input is caller supplied. Fingerprints identify semantic content, never
research truth, engineering evidence, or permission to acquire or mutate state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
import hashlib
import ipaddress
import json
import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit


class ResearchContractError(ValueError):
    """Research contract material is malformed, unsafe, or incorrectly bound."""


_BRIEF = frozenset({"format", "version", "kind", "question", "decision_context", "scope",
                    "current_as_of", "project_basis", "source_policy", "resource_bounds",
                    "handoff_target", "invalidation_basis", "brief_fingerprint"})
_TARGET = frozenset({"format", "version", "kind", "brief_fingerprint", "target_kind",
                     "target_ref", "challenge", "rationale", "target_fingerprint"})
_REQUEST = frozenset({"format", "version", "kind", "brief_fingerprint", "phase",
                      "purpose", "locator_or_query", "source_family",
                      "challenge_target_fingerprints", "bounds", "request_fingerprint"})
_FAMILIES = frozenset({"REPOSITORY", "OFFICIAL_DOCS", "SPECIFICATION", "PAPER_REPORT",
                       "PUBLIC_WEB", "CONNECTED_SOURCE"})
_TARGET_KINDS = frozenset({"CLAIM", "SOURCE", "ASSUMPTION", "FRESHNESS", "COVERAGE",
                           "APPLICABILITY", "INSTRUCTION_BOUNDARY", "GAP"})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_KIND = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|[/\\]|~[/\\]|\.\.[/\\]|\$\{|%[A-Za-z_]+%)")
_SECRET = re.compile(
    r"(?:-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----|"
    r"\b(?:bearer|basic)\s+[A-Za-z0-9+/._~=-]{8,}|"
    r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|"
    r"client[_-]?secret|private[_-]?key)\s*[:=]\s*[^\s,;]{4,}|"
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})\b)",
    re.IGNORECASE,
)
_SENSITIVE_QUERY = frozenset({"password", "passwd", "secret", "client_secret", "token",
                              "access_token", "auth_token", "api_key", "apikey", "key",
                              "auth", "authorization", "credential", "access_key",
                              "signature", "sig", "jwt", "session_id"})


def _strict_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ResearchContractError("material must be strict UTF-8 JSON") from exc


def parse_research_json(raw: str | bytes) -> Any:
    """Decode strict JSON, rejecting duplicate object keys before normalization."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ResearchContractError("duplicate JSON field")
            result[key] = value
        return result

    def bad_constant(value: str) -> Any:
        raise ResearchContractError(f"non-JSON constant: {value}")

    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        if not isinstance(raw, str) or len(raw.encode("utf-8", errors="strict")) > 131072:
            raise ResearchContractError("JSON input is invalid or over-bound")
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ResearchContractError):
            raise
        raise ResearchContractError("invalid strict JSON input") from exc


def _mapping(value: Any, name: str, fields: frozenset[str]) -> Mapping[str, Any]:
    if type(value) is not dict or len(value) != len(fields) or set(value) != fields:
        raise ResearchContractError(f"{name} must contain exactly its closed fields")
    return value


def _text(value: Any, name: str, limit: int, *, nonempty: bool = True,
          portable: bool = False) -> str:
    if type(value) is not str:
        raise ResearchContractError(f"{name} must be text")
    result = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    try:
        size = len(result.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise ResearchContractError(f"{name} contains invalid Unicode") from exc
    if size > limit or (nonempty and not result.strip()):
        raise ResearchContractError(f"{name} is empty or exceeds {limit} UTF-8 bytes")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in result) or "\x7f" in result:
        raise ResearchContractError(f"{name} contains control characters")
    if _SECRET.search(result):
        raise ResearchContractError(f"{name} contains credential material")
    if portable:
        _portable(result, name)
    return result


def _portable(value: str, name: str) -> None:
    decoded = unquote(value)
    if (_LOCAL_PATH.match(decoded) or re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", decoded)
            or decoded.lower().startswith(("file:", "smb:", "ssh://localhost"))):
        raise ResearchContractError(f"{name} is machine-local")
    if _SECRET.search(decoded):
        raise ResearchContractError(f"{name} contains credential material")
    try:
        parts = urlsplit(decoded)
        if parts.scheme:
            if parts.scheme.lower() not in {"https", "http", "git", "ssh"} or not parts.hostname:
                raise ResearchContractError(f"{name} has a non-portable URL")
            if parts.username is not None or parts.password is not None:
                raise ResearchContractError(f"{name} has URL credentials")
            host = parts.hostname.lower()
            if host == "localhost" or host.endswith(".localhost"):
                raise ResearchContractError(f"{name} is machine-local")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    raise ResearchContractError(f"{name} is machine-local")
            for pair in parts.query.split("&"):
                if pair.split("=", 1)[0].lower() in _SENSITIVE_QUERY:
                    raise ResearchContractError(f"{name} has a credential query")
        elif re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", decoded):
            raise ResearchContractError(f"{name} has a non-portable scheme")
    except ValueError as exc:
        if isinstance(exc, ResearchContractError):
            raise
        raise ResearchContractError(f"{name} is not a valid portable locator") from exc


def _list(value: Any, name: str, limit: int) -> Sequence[Any]:
    if type(value) is not list or len(value) > limit:
        raise ResearchContractError(f"{name} must be a bounded list")
    return value


def _unique_texts(value: Any, name: str, count: int, size: int, *,
                  portable: bool = False, allowed: frozenset[str] | None = None,
                  nonempty_list: bool = False) -> list[str]:
    items = [_text(item, name, size, portable=portable) for item in _list(value, name, count)]
    if len(items) != len(set(items)) or (nonempty_list and not items):
        raise ResearchContractError(f"{name} is empty or contains duplicate normalized items")
    if allowed is not None and not set(items) <= allowed:
        raise ResearchContractError(f"{name} contains an unsupported value")
    return sorted(items)


def _positive(value: Any, name: str, ceiling: int) -> int:
    if type(value) is not int or not 1 <= value <= ceiling:
        raise ResearchContractError(f"{name} must be a positive integer at most {ceiling}")
    return value


def _literal(value: Any, expected: Any, name: str) -> Any:
    if type(value) is not type(expected) or value != expected:
        raise ResearchContractError(f"{name} must be {expected!r}")
    return expected


def _fingerprint(body: dict[str, Any], field: str, maximum: int) -> dict[str, Any]:
    result = dict(body)
    result[field] = hashlib.sha256(_strict_json(body)).hexdigest()
    if len(_strict_json(result)) > maximum:
        raise ResearchContractError("normalized contract exceeds JSON byte limit")
    return result


def _check_fingerprint(source: Mapping[str, Any], normalized: dict[str, Any], field: str) -> None:
    if not isinstance(source[field], str) or _HEX64.fullmatch(source[field]) is None or source[field] != normalized[field]:
        raise ResearchContractError(f"invalid or stale {field}")


def _brief(value: Any, *, validate: bool) -> dict[str, Any]:
    fields = _BRIEF if validate else _BRIEF - {"brief_fingerprint"}
    source = _mapping(value, "Research Brief", fields)
    _literal(source["format"], "AIOS_RESEARCH_BRIEF", "format")
    _literal(source["version"], 1, "version")
    _literal(source["kind"], "RESEARCH_BRIEF", "kind")
    scope = _mapping(source["scope"], "scope", frozenset({"include", "exclude"}))
    current = _mapping(source["current_as_of"], "current_as_of", frozenset({"mode", "value"}))
    mode, as_of = current["mode"], current["value"]
    if mode == "NOT_APPLICABLE":
        if as_of is not None:
            raise ResearchContractError("NOT_APPLICABLE requires null")
    elif mode == "DATE":
        if not isinstance(as_of, str) or _DATE.fullmatch(as_of) is None:
            raise ResearchContractError("DATE requires YYYY-MM-DD")
        try:
            date.fromisoformat(as_of)
        except ValueError as exc:
            raise ResearchContractError("invalid calendar date") from exc
    elif mode == "TIMESTAMP":
        if not isinstance(as_of, str) or _TIME.fullmatch(as_of) is None:
            raise ResearchContractError("TIMESTAMP requires UTC seconds")
        try:
            datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ResearchContractError("invalid calendar timestamp") from exc
    else:
        raise ResearchContractError("unsupported current_as_of mode")
    basis: dict[str, list[dict[str, str]]] = {}
    for name in ("project_basis", "invalidation_basis"):
        records = []
        for item in _list(source[name], name, 16):
            record = _mapping(item, name + " record", frozenset({"kind", "locator", "identity"}))
            kind = record["kind"]
            if type(kind) is not str or _KIND.fullmatch(kind) is None:
                raise ResearchContractError(f"invalid {name} kind")
            records.append({"kind": kind,
                            "locator": _text(record["locator"], name + " locator", 2048, portable=True),
                            "identity": _text(record["identity"], name + " identity", 512)})
        keys = [_strict_json(item) for item in records]
        if len(keys) != len(set(keys)):
            raise ResearchContractError(f"duplicate {name} record")
        basis[name] = [item for _, item in sorted(zip(keys, records), key=lambda pair: pair[0])]
    policy = _mapping(source["source_policy"], "source_policy", frozenset({
        "allowed_source_families", "independence_required", "excluded_locators"}))
    if type(policy["independence_required"]) is not bool:
        raise ResearchContractError("independence_required must be boolean")
    bounds = _mapping(source["resource_bounds"], "resource_bounds", frozenset({
        "max_baseline_requests", "max_sources_per_request", "max_observation_bytes",
        "max_counter_evidence_requests"}))
    _literal(source["handoff_target"], "ARCHITECTURE", "handoff_target")
    body = {
        "format": "AIOS_RESEARCH_BRIEF", "version": 1, "kind": "RESEARCH_BRIEF",
        "question": _text(source["question"], "question", 8192),
        "decision_context": _text(source["decision_context"], "decision_context", 8192),
        "scope": {key: _unique_texts(scope[key], "scope." + key, 32, 2048)
                  for key in ("include", "exclude")},
        "current_as_of": {"mode": mode, "value": as_of},
        **basis,
        "source_policy": {
            "allowed_source_families": _unique_texts(policy["allowed_source_families"],
                "allowed_source_families", 6, 32, allowed=_FAMILIES, nonempty_list=True),
            "independence_required": policy["independence_required"],
            "excluded_locators": _unique_texts(policy["excluded_locators"],
                "excluded_locators", 16, 2048, portable=True),
        },
        "resource_bounds": {key: _positive(bounds[key], key, ceiling) for key, ceiling in (
            ("max_baseline_requests", 32), ("max_sources_per_request", 32),
            ("max_observation_bytes", 262144), ("max_counter_evidence_requests", 16))},
        "handoff_target": "ARCHITECTURE",
    }
    result = _fingerprint(body, "brief_fingerprint", 65536)
    if validate:
        _check_fingerprint(source, result, "brief_fingerprint")
    return result


def construct_research_brief(material: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a complete fingerprint-free Research Brief."""
    return _brief(material, validate=False)


def validate_research_brief(material: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate and detach one exact content-addressed Research Brief."""
    return _brief(material, validate=True)


def _target(value: Any, brief: Mapping[str, Any], *, validate: bool) -> dict[str, Any]:
    bound = validate_research_brief(brief)
    fields = _TARGET if validate else _TARGET - {"target_fingerprint"}
    source = _mapping(value, "Challenge Target", fields)
    _literal(source["format"], "AIOS_RESEARCH_CHALLENGE_TARGET", "format")
    _literal(source["version"], 1, "version")
    _literal(source["kind"], "RESEARCH_CHALLENGE_TARGET", "kind")
    _literal(source["brief_fingerprint"], bound["brief_fingerprint"], "brief_fingerprint")
    if type(source["target_kind"]) is not str or source["target_kind"] not in _TARGET_KINDS:
        raise ResearchContractError("unsupported target_kind")
    body = {
        "format": "AIOS_RESEARCH_CHALLENGE_TARGET", "version": 1,
        "kind": "RESEARCH_CHALLENGE_TARGET", "brief_fingerprint": bound["brief_fingerprint"],
        "target_kind": source["target_kind"],
        "target_ref": _text(source["target_ref"], "target_ref", 1024, nonempty=False),
        "challenge": _text(source["challenge"], "challenge", 4096),
        "rationale": _text(source["rationale"], "rationale", 4096),
    }
    result = _fingerprint(body, "target_fingerprint", 16384)
    if validate:
        _check_fingerprint(source, result, "target_fingerprint")
    return result


def construct_challenge_target(material: Mapping[str, Any], brief: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a challenge description to an exact valid Research Brief."""
    return _target(material, brief, validate=False)


def validate_challenge_target(material: Mapping[str, Any], brief: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate an exact same-brief challenge target."""
    return _target(material, brief, validate=True)


def _request(value: Any, brief: Mapping[str, Any], targets: Sequence[Mapping[str, Any]], *,
             validate: bool) -> dict[str, Any]:
    bound = validate_research_brief(brief)
    fields = _REQUEST if validate else _REQUEST - {"request_fingerprint"}
    source = _mapping(value, "Acquisition Request", fields)
    _literal(source["format"], "AIOS_ACQUISITION_REQUEST", "format")
    _literal(source["version"], 1, "version")
    _literal(source["kind"], "ACQUISITION_REQUEST", "kind")
    _literal(source["brief_fingerprint"], bound["brief_fingerprint"], "brief_fingerprint")
    phase = source["phase"]
    if type(phase) is not str or phase not in {"BASELINE", "COUNTER_EVIDENCE"}:
        raise ResearchContractError("unsupported acquisition phase")
    fingerprints = _unique_texts(source["challenge_target_fingerprints"],
                                 "challenge_target_fingerprints", 16, 64)
    if any(_HEX64.fullmatch(item) is None for item in fingerprints):
        raise ResearchContractError("invalid challenge target fingerprint")
    if targets is None:
        targets = []
    if type(targets) is not list or len(targets) > 16:
        raise ResearchContractError("targets must be a bounded explicit list")
    if phase == "BASELINE":
        if fingerprints or targets:
            raise ResearchContractError("BASELINE forbids challenge targets")
    else:
        actual = [validate_challenge_target(target, bound)["target_fingerprint"] for target in targets]
        if not fingerprints or len(actual) != len(set(actual)) or sorted(actual) != fingerprints:
            raise ResearchContractError("COUNTER_EVIDENCE requires exact same-brief targets")
    family = source["source_family"]
    if type(family) is not str or family not in bound["source_policy"]["allowed_source_families"]:
        raise ResearchContractError("source_family is not allowed by brief")
    limits = _mapping(source["bounds"], "request bounds", frozenset({
        "max_items", "max_total_observation_bytes"}))
    body = {
        "format": "AIOS_ACQUISITION_REQUEST", "version": 1, "kind": "ACQUISITION_REQUEST",
        "brief_fingerprint": bound["brief_fingerprint"], "phase": phase,
        "purpose": _text(source["purpose"], "purpose", 4096),
        "locator_or_query": _text(source["locator_or_query"], "locator_or_query", 8192,
                                  portable=True),
        "source_family": family, "challenge_target_fingerprints": fingerprints,
        "bounds": {
            "max_items": _positive(limits["max_items"], "max_items",
                                   bound["resource_bounds"]["max_sources_per_request"]),
            "max_total_observation_bytes": _positive(limits["max_total_observation_bytes"],
                "max_total_observation_bytes", bound["resource_bounds"]["max_observation_bytes"]),
        },
    }
    result = _fingerprint(body, "request_fingerprint", 32768)
    if validate:
        _check_fingerprint(source, result, "request_fingerprint")
    return result


def construct_acquisition_request(material: Mapping[str, Any], brief: Mapping[str, Any],
                                  targets: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Normalize one bounded request with explicit target material."""
    return _request(material, brief, targets, validate=False)


def validate_acquisition_request(material: Mapping[str, Any], brief: Mapping[str, Any],
                                 targets: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Revalidate its exact brief, budgets, and optional challenge targets."""
    return _request(material, brief, targets, validate=True)
