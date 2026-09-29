"""Pure RA-3 research profile, immutable record, and transient reuse contracts.

Inputs are supplied by the caller. Structural validation does not judge research
truth, select a closure, acquire sources, or store a record.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import yaml

from .research_contract import ResearchContractError, validate_research_brief, _FAMILIES
from .source_acquisition import (
    _REPRESENTATIONS, _provenance, _text, _locator, _SECRET, _USERINFO,
    _NATIVE_PAYLOAD, validate_source_observation,
)


_LENSES = (
    "SOURCE_AUTHORITY_PROVENANCE", "SOURCE_FRESHNESS_VERSION",
    "CLAIM_EVIDENCE_BINDING", "COVERAGE_INDEPENDENCE",
    "CONTRADICTION_COUNTEREVIDENCE", "PROJECT_APPLICABILITY_NOVELTY",
    "ASSUMPTION_UNCERTAINTY", "UNTRUSTED_CONTENT_INSTRUCTION_ISOLATION",
    "AUTHORITY_HANDOFF_BOUNDARY",
)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KIND = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_LOCAL_TEXT = re.compile(r"(?:[A-Za-z]:[\\/]|(?:^|\s)(?:/|\\\\|~/|\.\./))")
_NON_SEMANTIC = re.compile(
    r"(?:\b(?:chat history|chain[- ]of[- ]thought|raw (?:tool )?logs?)\b|"
    r"(?:^|\n)\s*(?:system|developer|assistant|tool)\s*:|"
    r"\b(?:provider|model|session|workspace|workspace_root|root_path)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_RECORD = frozenset({"format", "version", "kind", "research_brief", "audit_profile_ref",
    "predecessor", "access_scope", "sources", "claims", "project_assessment",
    "closure", "record_fingerprint"})
_SOURCE = frozenset({"observation_fingerprint", "request_fingerprint",
    "request_source_family", "representation_kind", "provenance", "content_sha256",
    "instruction_trust", "retained_excerpt", "assessment"})
_CLAIM = frozenset({"claim_kind", "statement", "source_bindings", "uncertainty",
    "invalidation_basis", "claim_fingerprint"})


def _fields(value: Any, fields: frozenset[str] | set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or len(value) != len(fields) or set(value) != fields:
        raise ResearchContractError(f"{name} must contain exactly its closed fields")
    return value


def _literal(value: Any, expected: Any, name: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise ResearchContractError(f"invalid {name}")


def _choice(value: Any, choices: set[str], name: str) -> str:
    if type(value) is not str or value not in choices:
        raise ResearchContractError(f"invalid {name}")
    return value


def _sha(value: Any, name: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise ResearchContractError(f"invalid {name}")
    return value


def _json(value: Any, ceiling: int, depth_limit: int = 16) -> bytes:
    def walk(item: Any, depth: int) -> None:
        if depth > depth_limit:
            raise ResearchContractError("material is too deeply nested")
        if type(item) is dict:
            if any(type(k) is not str for k in item):
                raise ResearchContractError("non-text mapping key")
            for k, v in item.items():
                walk(k, depth + 1)
                walk(v, depth + 1)
        elif type(item) is list:
            for v in item:
                walk(v, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ResearchContractError("material is not strict JSON")
    walk(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ResearchContractError("material is not strict UTF-8 JSON") from exc
    if len(encoded) > ceiling:
        raise ResearchContractError("material exceeds JSON byte limit")
    return encoded


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value, 786432)).hexdigest()


def _semantic(value: Any, name: str, ceiling: int) -> str:
    result = _text(value, name, ceiling)
    if _LOCAL_TEXT.search(result) or _NATIVE_PAYLOAD.search(result) or _NON_SEMANTIC.search(result):
        raise ResearchContractError(f"{name} contains machine-local or native payload material")
    return result


class _UniqueLoader(yaml.SafeLoader):
    pass


def _yaml_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if type(key) is not str or key in result:
            raise ResearchContractError("duplicate or non-text YAML key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def normalize_research_audit_profile(value: Any) -> dict[str, Any]:
    """Validate the sole closed v1 research lens profile."""
    profile = _fields(value, {"id", "version", "lenses"}, "research audit profile")
    _literal(profile["id"], "research-high-value-v1", "profile id")
    _literal(profile["version"], 1, "profile version")
    lenses = profile["lenses"]
    if type(lenses) is not list or len(lenses) != len(_LENSES):
        raise ResearchContractError("profile requires nine ordered lenses")
    result = []
    for expected, item in zip(_LENSES, lenses):
        lens = _fields(item, {"id", "check"}, "research lens")
        _literal(lens["id"], expected, "lens id")
        result.append({"id": expected, "check": _semantic(lens["check"], "lens check", 2048)})
    normalized = {"id": "research-high-value-v1", "version": 1, "lenses": result}
    _json(normalized, 32768)
    return normalized


def parse_research_audit_profiles(raw: str | bytes) -> dict[str, Any]:
    """Parse only caller-supplied UTF-8 YAML; no registry is auto-loaded."""
    try:
        encoded = raw.encode("utf-8", "strict") if type(raw) is str else raw
        if type(encoded) is not bytes or len(encoded) > 32768:
            raise ResearchContractError("invalid or over-bound profile YAML")
        loaded = yaml.load(encoded.decode("utf-8", "strict"), Loader=_UniqueLoader)
    except (UnicodeError, yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, ResearchContractError):
            raise
        raise ResearchContractError("invalid profile YAML") from exc
    _json(loaded, 32768)
    registry = _fields(loaded, {"format", "version", "profiles"}, "profile registry")
    _literal(registry["format"], "AIOS_RESEARCH_AUDIT_PROFILES", "registry format")
    _literal(registry["version"], 1, "registry version")
    if type(registry["profiles"]) is not list or len(registry["profiles"]) != 1:
        raise ResearchContractError("registry requires exactly one profile")
    return {"format": "AIOS_RESEARCH_AUDIT_PROFILES", "version": 1,
            "profiles": [normalize_research_audit_profile(registry["profiles"][0])]}


def research_audit_profile_ref(profile: Any) -> dict[str, Any]:
    normalized = normalize_research_audit_profile(profile)
    return {"id": normalized["id"], "version": 1, "digest": _digest(normalized)}


def _profile_ref(value: Any, expected: dict[str, Any]) -> dict[str, Any]:
    ref = _fields(value, {"id", "version", "digest"}, "audit profile ref")
    for name in ("id", "version", "digest"):
        _literal(ref[name], expected[name], "audit profile " + name)
    return dict(expected)


def _basis(value: Any, name: str, *, required: bool, limit: int) -> list[dict[str, str]]:
    if type(value) is not list or len(value) > limit or (required and not value):
        raise ResearchContractError(f"{name} must be a bounded non-empty list")
    result = []
    keys = set()
    for item in value:
        part = _fields(item, {"kind", "locator", "identity"}, name + " item")
        kind = part["kind"]
        if type(kind) is not str or _KIND.fullmatch(kind) is None:
            raise ResearchContractError("invalid basis kind")
        locator = _locator(part["locator"])
        if len(locator.encode("utf-8")) > 2048:
            raise ResearchContractError("basis locator exceeds bound")
        identity = _semantic(part["identity"], "basis identity", 512)
        key = (kind, locator)
        if key in keys:
            raise ResearchContractError("duplicate basis key")
        keys.add(key)
        result.append({"kind": kind, "locator": locator, "identity": identity})
    return sorted(result, key=lambda item: (item["kind"], item["locator"]))


def _fingerprints(value: Any, name: str, limit: int, allowed: set[str],
                  *, required: bool = False) -> list[str]:
    if type(value) is not list or len(value) > limit or (required and not value):
        raise ResearchContractError(f"invalid {name} list")
    result = [_sha(item, name) for item in value]
    if len(result) != len(set(result)) or not set(result) <= allowed:
        raise ResearchContractError(f"duplicate or unknown {name}")
    return sorted(result)


def _source(value: Any, observations: dict[str, Any], brief: dict[str, Any],
            *, verify_excerpt: bool) -> dict[str, Any]:
    item = _fields(value, _SOURCE, "source")
    fingerprint = _sha(item["observation_fingerprint"], "observation fingerprint")
    request = _sha(item["request_fingerprint"], "request fingerprint")
    family = _choice(item["request_source_family"], set(_FAMILIES), "source family")
    representation = _choice(item["representation_kind"], set(_REPRESENTATIONS), "representation")
    provenance = _provenance(item["provenance"])
    content_sha = _sha(item["content_sha256"], "content digest")
    _literal(item["instruction_trust"], "UNTRUSTED", "instruction trust")
    excerpt = item["retained_excerpt"]
    if excerpt is not None:
        if provenance["access_scope"] != "PUBLIC":
            raise ResearchContractError("private source excerpt is forbidden")
        excerpt = _text(excerpt, "retained excerpt", 4096, nonempty=True, guarded=False)
    if fingerprint in observations:
        supplied = _fields(observations[fingerprint], {"observation", "request", "targets"},
                           "supplied observation material")
        observed = validate_source_observation(supplied["observation"], brief,
                                                supplied["request"], supplied["targets"])
        for name, expected in (("observation_fingerprint", fingerprint),
                               ("request_fingerprint", request),
                               ("request_source_family", family),
                               ("representation_kind", representation),
                               ("provenance", provenance),
                               ("instruction_trust", "UNTRUSTED")):
            _literal(observed[name], expected, name)
        _literal(observed["content"]["content_sha256"], content_sha, "content digest")
        if excerpt is not None and excerpt not in observed["content"]["text"]:
            raise ResearchContractError("retained excerpt is not exact observation content")
    elif excerpt is not None and verify_excerpt:
        raise ResearchContractError("retained excerpt requires supplied exact observation")
    assessment = _fields(item["assessment"], {"authority", "independence", "freshness"},
                         "source assessment")
    normalized_assessment = {}
    for name, choices in (("authority", {"PRIMARY", "OFFICIAL", "SECONDARY", "COMMUNITY", "UNKNOWN"}),
                          ("independence", {"INDEPENDENT", "DERIVED", "UNKNOWN"}),
                          ("freshness", {"CURRENT_FOR_BRIEF", "LIMITED", "STALE", "UNKNOWN"})):
        fields = {"class", "rationale", "related_observation_fingerprints"} if name == "independence" else {"class", "rationale"}
        part = _fields(assessment[name], fields, name)
        cls = _choice(part["class"], choices, name + " class")
        entry = {"class": cls, "rationale": _semantic(part["rationale"], name + " rationale", 4096)}
        if name == "independence":
            related = part["related_observation_fingerprints"]
            if type(related) is not list or len(related) > 8 or (cls == "DERIVED") != bool(related):
                raise ResearchContractError("invalid independence references")
            refs = [_sha(ref, "related observation") for ref in related]
            if len(refs) != len(set(refs)):
                raise ResearchContractError("duplicate related observation")
            entry["related_observation_fingerprints"] = sorted(refs)
        normalized_assessment[name] = entry
    return {"observation_fingerprint": fingerprint, "request_fingerprint": request,
        "request_source_family": family, "representation_kind": representation,
        "provenance": provenance, "content_sha256": content_sha,
        "instruction_trust": "UNTRUSTED", "retained_excerpt": excerpt,
        "assessment": normalized_assessment}


def _claim(value: Any, source_ids: set[str], *, validate: bool) -> dict[str, Any]:
    item = _fields(value, _CLAIM if validate else _CLAIM - {"claim_fingerprint"}, "claim")
    kind = _choice(item["claim_kind"], {"OBSERVATION", "INFERENCE", "ASSUMPTION", "UNKNOWN"}, "claim kind")
    bindings = item["source_bindings"]
    if type(bindings) is not list or len(bindings) > 16 or (kind in {"OBSERVATION", "INFERENCE"} and not bindings):
        raise ResearchContractError("invalid source bindings")
    normalized_bindings = []
    for binding in bindings:
        part = _fields(binding, {"observation_fingerprint", "role"}, "source binding")
        ref = _sha(part["observation_fingerprint"], "binding fingerprint")
        if ref not in source_ids:
            raise ResearchContractError("binding references absent source")
        normalized_bindings.append({"observation_fingerprint": ref,
                                    "role": _choice(part["role"], {"SUPPORT", "CONTRADICT", "LIMIT", "CONTEXT"}, "binding role")})
    keys = [(b["observation_fingerprint"], b["role"]) for b in normalized_bindings]
    if len(keys) != len(set(keys)):
        raise ResearchContractError("duplicate source binding")
    uncertainty = _fields(item["uncertainty"], {"status", "summary"}, "uncertainty")
    body = {"claim_kind": kind, "statement": _semantic(item["statement"], "claim statement", 8192),
        "source_bindings": sorted(normalized_bindings, key=lambda b: (b["observation_fingerprint"], b["role"])),
        "uncertainty": {"status": _choice(uncertainty["status"],
            {"NO_MATERIAL_UNCERTAINTY", "BOUNDED_UNCERTAINTY", "UNRESOLVED_UNCERTAINTY"}, "uncertainty status"),
            "summary": _semantic(uncertainty["summary"], "uncertainty summary", 4096)},
        "invalidation_basis": _basis(item["invalidation_basis"], "invalidation basis",
                                     required=True, limit=16)}
    result = dict(body, claim_fingerprint=_digest(body))
    if validate:
        _literal(item["claim_fingerprint"], result["claim_fingerprint"], "claim fingerprint")
    return result


def construct_research_claim(material: Any, source_fingerprints: list[str]) -> dict[str, Any]:
    """Content-address one claim against caller-supplied registry identities."""
    if type(source_fingerprints) is not list or len(source_fingerprints) > 64:
        raise ResearchContractError("invalid source identities")
    ids = {_sha(item, "source fingerprint") for item in source_fingerprints}
    if len(ids) != len(source_fingerprints):
        raise ResearchContractError("duplicate source identities")
    return _claim(material, ids, validate=False)


def _record(value: Any, profile: Any, predecessor_record: Any,
            observations: Any, *, validate: bool, lineage_check: bool = True) -> dict[str, Any]:
    if type(observations) is not dict or len(observations) > 64:
        raise ResearchContractError("observations must be a bounded caller-supplied mapping")
    _json(value, 786432)
    item = _fields(value, _RECORD if validate else _RECORD - {"record_fingerprint"}, "Research Record")
    for name, expected in (("format", "AIOS_RESEARCH_RECORD"), ("version", 1),
                           ("kind", "RESEARCH_RECORD")):
        _literal(item[name], expected, name)
    brief = validate_research_brief(item["research_brief"])
    ref = research_audit_profile_ref(profile)
    _profile_ref(item["audit_profile_ref"], ref)
    sources = item["sources"]
    if type(sources) is not list or len(sources) > 64:
        raise ResearchContractError("invalid source registry")
    normalized_sources = [_source(source, observations, brief, verify_excerpt=not validate)
                          for source in sources]
    source_ids = [source["observation_fingerprint"] for source in normalized_sources]
    if len(source_ids) != len(set(source_ids)):
        raise ResearchContractError("duplicate source fingerprint")
    if (not validate and set(observations) != set(source_ids)) or (validate and observations):
        raise ResearchContractError("construction requires exact supplied observation material")
    source_set = set(source_ids)
    for source in normalized_sources:
        related = source["assessment"]["independence"]["related_observation_fingerprints"]
        if not set(related) <= source_set or source["observation_fingerprint"] in related:
            raise ResearchContractError("invalid related source reference")
    scope = "AUTHORIZED_PRIVATE" if any(source["provenance"]["access_scope"] == "AUTHORIZED_PRIVATE"
                                        for source in normalized_sources) else "PUBLIC"
    _literal(item["access_scope"], scope, "record access scope")
    claims = item["claims"]
    if type(claims) is not list or not 1 <= len(claims) <= 64:
        raise ResearchContractError("invalid claim registry")
    normalized_claims = [_claim(claim, source_set, validate=True) for claim in claims]
    claim_ids = [claim["claim_fingerprint"] for claim in normalized_claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ResearchContractError("duplicate claim fingerprint")
    claim_set = set(claim_ids)
    project = _fields(item["project_assessment"],
                      {"applicability", "novelty", "summary", "basis_claim_fingerprints"}, "project assessment")
    normalized_project = {"applicability": _choice(project["applicability"],
        {"APPLIES", "PARTIAL", "DOES_NOT_APPLY", "UNRESOLVED"}, "applicability"),
        "novelty": _choice(project["novelty"],
            {"NOVEL", "PARTIAL", "ALREADY_PRESENT", "UNRESOLVED"}, "novelty"),
        "summary": _semantic(project["summary"], "project summary", 8192),
        "basis_claim_fingerprints": _fingerprints(project["basis_claim_fingerprints"],
            "project basis claims", 16, claim_set, required=True)}
    closure = _fields(item["closure"], {"outcome", "summary", "material_claim_fingerprints",
                                      "unresolved_claim_fingerprints"}, "closure")
    normalized_closure = {"outcome": _choice(closure["outcome"],
        {"RESEARCH_CANDIDATE", "INSUFFICIENT_EVIDENCE"}, "closure outcome"),
        "summary": _semantic(closure["summary"], "closure summary", 8192),
        "material_claim_fingerprints": _fingerprints(closure["material_claim_fingerprints"],
            "material claims", 64, claim_set),
        "unresolved_claim_fingerprints": _fingerprints(closure["unresolved_claim_fingerprints"],
            "unresolved claims", 64, claim_set)}
    predecessor = item["predecessor"]
    if predecessor is None:
        if predecessor_record is not None:
            raise ResearchContractError("fresh lineage forbids predecessor record")
    else:
        part = _fields(predecessor, {"record_fingerprint", "retained_claim_fingerprints",
                                      "invalidated_claim_fingerprints"}, "predecessor")
        if predecessor_record is None and lineage_check:
            raise ResearchContractError("refresh requires exact predecessor record")
        prior_ids = set()
        if lineage_check:
            # Validate all immediate predecessor fields and identity without
            # requiring its own predecessor record (there is no resolver).
            prior = _record(predecessor_record, profile, None, {}, validate=True,
                            lineage_check=False)
            if prior["research_brief"]["brief_fingerprint"] != brief["brief_fingerprint"] or prior["audit_profile_ref"] != ref:
                raise ResearchContractError("changed Brief or profile requires fresh lineage")
            _literal(part["record_fingerprint"], prior["record_fingerprint"], "predecessor fingerprint")
            prior_ids = {claim["claim_fingerprint"] for claim in prior["claims"]}
        else:
            _sha(part["record_fingerprint"], "predecessor fingerprint")
            prior_ids = {_sha(x, "retained fingerprint") for x in part["retained_claim_fingerprints"]} | \
                        {_sha(x, "invalidated fingerprint") for x in part["invalidated_claim_fingerprints"]}
        retained = _fingerprints(part["retained_claim_fingerprints"], "retained claims", 64, prior_ids)
        invalidated = _fingerprints(part["invalidated_claim_fingerprints"], "invalidated claims", 64, prior_ids)
        if set(retained) & set(invalidated) or set(retained) | set(invalidated) != prior_ids:
            raise ResearchContractError("predecessor claims require exact disjoint partition")
        if lineage_check and (not set(retained) <= claim_set or set(invalidated) & claim_set):
            raise ResearchContractError("refreshed claims violate predecessor partition")
        predecessor = {"record_fingerprint": part["record_fingerprint"],
            "retained_claim_fingerprints": retained, "invalidated_claim_fingerprints": invalidated}
    body = {"format": "AIOS_RESEARCH_RECORD", "version": 1, "kind": "RESEARCH_RECORD",
        "research_brief": brief, "audit_profile_ref": ref, "predecessor": predecessor,
        "access_scope": scope, "sources": sorted(normalized_sources, key=lambda s: s["observation_fingerprint"]),
        "claims": sorted(normalized_claims, key=lambda c: c["claim_fingerprint"]),
        "project_assessment": normalized_project, "closure": normalized_closure}
    result = dict(body, record_fingerprint=_digest(body))
    _json(result, 786432)
    if validate:
        _literal(item["record_fingerprint"], result["record_fingerprint"], "record fingerprint")
    return result


def construct_research_record(material: Any, profile: Any, predecessor_record: Any = None,
                              observations: Any = None) -> dict[str, Any]:
    """Build an immutable record from bounded supplied semantic material."""
    return _record(material, profile, predecessor_record, {} if observations is None else observations,
                   validate=False)


def validate_research_record(material: Any, profile: Any, predecessor_record: Any = None) -> dict[str, Any]:
    """Revalidate a record and its exact immediate predecessor, when present."""
    return _record(material, profile, predecessor_record, {}, validate=True)


def project_research_reuse(record: Any, profile: Any, current_basis: Any,
                           predecessor_record: Any = None) -> dict[str, Any]:
    """Compare caller-supplied basis identities; make no freshness judgment."""
    bound = validate_research_record(record, profile, predecessor_record)
    declared_keys = {(basis["kind"], basis["locator"])
                     for claim in bound["claims"] for basis in claim["invalidation_basis"]}
    current = _basis(current_basis, "current basis", required=False, limit=len(declared_keys))
    lookup = {(item["kind"], item["locator"]): item["identity"] for item in current}
    if not lookup.keys() <= declared_keys:
        raise ResearchContractError("current basis contains undeclared key")
    valid, refresh = [], []
    for claim in bound["claims"]:
        reusable = all(lookup.get((basis["kind"], basis["locator"])) == basis["identity"]
                       for basis in claim["invalidation_basis"])
        (valid if reusable else refresh).append(claim["claim_fingerprint"])
    return {"record_fingerprint": bound["record_fingerprint"],
            "status": "VALID" if not refresh else "REFRESH_REQUIRED",
            "valid_claim_fingerprints": valid,
            "refresh_required_claim_fingerprints": refresh}


__all__ = ["parse_research_audit_profiles", "normalize_research_audit_profile",
    "research_audit_profile_ref", "construct_research_claim", "construct_research_record",
    "validate_research_record", "project_research_reuse"]
