"""Pure BP6-P2B Reviewer return grammar over caller-supplied material.

The provider authors semantic values only. Canonical REVIEW and finding identities,
materialization, and lifecycle validation belong to later authority.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

import yaml

from .task import Task


class ReviewerReturnContractError(ValueError):
    """Invalid registry, provider semantic body, or supplied validation context."""


_BOUNDS = {
    "raw_registry_bytes": 65536,
    "selected_contract_bytes": 32768,
    "semantic_body_bytes": 131072,
    "acceptance_entries": 256,
    "findings": 32,
    "location_bytes": 2048,
    "issue_bytes": 8192,
    "expected_bytes": 8192,
    "max_depth": 32,
}
_METADATA = {
    "selected_flow": "SEMANTIC_REVIEW",
    "authority_owner": "REVIEWER",
    "decision_family_ref": "review.validate_review",
    "handoff_target": "AUTHORING_INGRESS",
    "expected_return_shape": "REVIEW_CONTRACT_PROPOSAL",
}
_GRAMMAR = {
    "body_fields": ["verdict", "acceptance", "findings"],
    "verdicts": ["PASS", "CHANGES_REQUIRED", "BLOCKED"],
    "acceptance_fields": ["id", "outcome"],
    "acceptance_outcomes": ["PASS", "FAIL"],
    "finding_fields": ["basis", "action", "location", "issue", "expected"],
    "finding_actions": ["CODE_FIX", "EVIDENCE_ONLY"],
}
_RULES = [
    "PRIMARY_ALL_TASK_ACCEPTANCE_IDS_ONCE",
    "DELTA_EXACT_PRIOR_BASIS_ONCE_ADDITIONAL_TASK_IDS_ONLY",
    "ACCEPTANCE_NORMALIZED_IN_TASK_ORDER",
    "FINDINGS_RETAIN_PROVIDER_ORDER_WITHOUT_IDENTITIES",
    "PASS_REQUIRES_NO_FINDINGS_AND_ALL_SUPPLIED_PASS",
    "CHANGES_REQUIRED_REQUIRES_FAIL_AND_FINDING_PER_FAIL",
    "BLOCKED_HAS_NO_EXTRA_PROVIDER_CONSISTENCY_RULES",
    "MALFORMED_MATERIAL_IS_CONTRACT_ERROR_NOT_SEMANTIC_VERDICT",
]
_CONTRACT_FIELDS = frozenset({"id", "version", "representation", "grammar", "rules", *_METADATA})
_BODY_FIELDS = frozenset(_GRAMMAR["body_fields"])
_ACCEPTANCE_FIELDS = frozenset(_GRAMMAR["acceptance_fields"])
_FINDING_FIELDS = frozenset(_GRAMMAR["finding_fields"])


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ReviewerReturnContractError("strict UTF-8 JSON required") from exc


def _normalize(value: Any, *, registry: bool, depth: int = 0) -> Any:
    if depth > _BOUNDS["max_depth"]:
        raise ReviewerReturnContractError("structural depth exceeded")
    if type(value) is str:
        try:
            value.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise ReviewerReturnContractError("invalid Unicode") from exc
        return value.replace("\r\n", "\n").replace("\r", "\n") if registry else value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ReviewerReturnContractError("mapping keys must be text")
            normalized_key = _normalize(key, registry=registry, depth=depth + 1)
            if normalized_key in result:
                raise ReviewerReturnContractError("duplicate normalized mapping key")
            result[normalized_key] = _normalize(item, registry=registry, depth=depth + 1)
        return result
    if type(value) is list:
        return [_normalize(item, registry=registry, depth=depth + 1) for item in value]
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and value not in (float("inf"), float("-inf")) and value == value:
        return value
    raise ReviewerReturnContractError("non-JSON semantic value")


def _fields(value: Any, expected: frozenset[str] | set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise ReviewerReturnContractError(f"{name} has unknown or missing fields")
    return value


class _RegistryLoader(yaml.SafeLoader):
    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent: Any, index: Any) -> yaml.Node:
        if self.check_event(yaml.AliasEvent):
            raise ReviewerReturnContractError("YAML aliases are forbidden")
        if self._depth > _BOUNDS["max_depth"]:
            raise ReviewerReturnContractError("structural depth exceeded")
        self._depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _unique_mapping(loader: _RegistryLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if type(key) is not str or key in result:
            raise ReviewerReturnContractError("duplicate or non-text YAML mapping key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_RegistryLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def normalize_reviewer_return_registry(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one decoded v1 registry and return detached normalized material."""
    root = _fields(_normalize(value, registry=True),
                   {"format", "version", "bounds", "contracts"}, "registry")
    if root["format"] != "AIOS_REVIEWER_RETURN_CONTRACTS" or type(root["version"]) is not int or root["version"] != 1:
        raise ReviewerReturnContractError("unknown registry format or version")
    bounds = _fields(root["bounds"], set(_BOUNDS), "bounds")
    if any(type(bounds[key]) is not int or bounds[key] != expected
           for key, expected in _BOUNDS.items()):
        raise ReviewerReturnContractError("effective v1 bounds substituted")
    contracts = root["contracts"]
    if type(contracts) is not list or len(contracts) != 1:
        raise ReviewerReturnContractError("exactly one contract required")
    contract = _fields(contracts[0], _CONTRACT_FIELDS, "contract")
    if (contract["id"] != "reviewer-semantic-return-v1"
            or type(contract["version"]) is not int or contract["version"] != 1
            or contract["representation"] != "STRICT_JSON_MAPPING"
            or any(type(contract[key]) is not str or contract[key] != expected
                   for key, expected in _METADATA.items())):
        raise ReviewerReturnContractError("contract identity or metadata mismatch")
    grammar = _fields(contract["grammar"], set(_GRAMMAR), "grammar")
    if any(grammar[key] != expected or type(grammar[key]) is not list
           for key, expected in _GRAMMAR.items()):
        raise ReviewerReturnContractError("contract grammar mismatch")
    if type(contract["rules"]) is not list or contract["rules"] != _RULES:
        raise ReviewerReturnContractError("contract rules mismatch")
    if len(_json_bytes({"contract": contract, "bounds": bounds})) > bounds["selected_contract_bytes"]:
        raise ReviewerReturnContractError("selected contract exceeds byte bound")
    if len(_json_bytes(root)) > bounds["raw_registry_bytes"]:
        raise ReviewerReturnContractError("normalized registry exceeds byte bound")
    return root


def parse_reviewer_return_registry(raw: bytes | str) -> dict[str, Any]:
    """Parse caller-supplied UTF-8 YAML; never discover or open a registry path."""
    if type(raw) is str:
        try:
            encoded = raw.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise ReviewerReturnContractError("invalid registry Unicode") from exc
    elif type(raw) is bytes:
        encoded = raw
    else:
        raise ReviewerReturnContractError("registry must be UTF-8 YAML")
    if len(encoded) > _BOUNDS["raw_registry_bytes"]:
        raise ReviewerReturnContractError("raw registry exceeds byte bound")
    try:
        loaded = yaml.load(encoded.decode("utf-8", "strict"), Loader=_RegistryLoader)
    except (UnicodeError, yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise ReviewerReturnContractError("invalid registry YAML") from exc
    return normalize_reviewer_return_registry(loaded)


def select_reviewer_return_contract(registry: bytes | str | Mapping[str, Any]) -> dict[str, Any]:
    """Return full validated contract, exact bounds, and its content address."""
    root = (parse_reviewer_return_registry(registry) if type(registry) in (bytes, str)
            else normalize_reviewer_return_registry(registry))
    contract = root["contracts"][0]
    bounds = root["bounds"]
    reference = reviewer_return_contract_ref(contract, bounds)
    return {"contract": contract, "bounds": bounds,
            "reviewer_return_contract_ref": reference}


def reviewer_return_contract_ref(contract: Mapping[str, Any], bounds: Mapping[str, Any]) -> dict[str, Any]:
    """Hash full caller-supplied contract and bounds; selection validates them first."""
    if not isinstance(contract, Mapping) or not isinstance(bounds, Mapping):
        raise ReviewerReturnContractError("contract and bounds mappings required")
    material = _normalize({"contract": contract, "bounds": bounds}, registry=True)
    selected = material["contract"]
    return {"id": selected["id"], "version": selected["version"],
            "digest": hashlib.sha256(_json_bytes(material)).hexdigest()}


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewerReturnContractError("duplicate JSON mapping key")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> Any:
    raise ReviewerReturnContractError(f"non-JSON constant: {value}")


def _nonempty_text(value: Any, name: str, limit: int) -> str:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > limit:
        raise ReviewerReturnContractError(f"{name} must be non-empty bounded UTF-8 text")
    return value


def parse_reviewer_semantic_body(
    source: bytes | str | Mapping[str, Any], *, task: Task,
    review_mode: str, prior_finding_basis: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize identity-free provider semantics for an exact Task."""
    if not isinstance(task, Task):
        raise ReviewerReturnContractError("an already-validated Task is required")
    if type(review_mode) is not str or review_mode not in ("PRIMARY", "DELTA"):
        raise ReviewerReturnContractError("review_mode must be PRIMARY or DELTA")
    task_order = [criterion.id for criterion in task.acceptance]
    task_ids = set(task_order)
    if review_mode == "PRIMARY":
        if prior_finding_basis is not None:
            raise ReviewerReturnContractError("PRIMARY cannot supply prior_finding_basis")
    elif (type(prior_finding_basis) is not str or not prior_finding_basis
          or prior_finding_basis not in task_ids):
        raise ReviewerReturnContractError("DELTA requires exact Task prior_finding_basis")

    if type(source) in (bytes, str):
        try:
            encoded = source if type(source) is bytes else source.encode("utf-8", "strict")
            if len(encoded) > _BOUNDS["semantic_body_bytes"]:
                raise ReviewerReturnContractError("raw semantic body exceeds byte bound")
            value = json.loads(encoded.decode("utf-8", "strict"),
                               object_pairs_hook=_unique_json_object,
                               parse_constant=_invalid_json_constant)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ReviewerReturnContractError("invalid strict UTF-8 JSON body") from exc
    else:
        value = source
    body = _fields(_normalize(value, registry=False), _BODY_FIELDS, "semantic body")
    if len(_json_bytes(body)) > _BOUNDS["semantic_body_bytes"]:
        raise ReviewerReturnContractError("normalized semantic body exceeds byte bound")
    verdict = body["verdict"]
    if type(verdict) is not str or verdict not in _GRAMMAR["verdicts"]:
        raise ReviewerReturnContractError("unknown semantic verdict")
    acceptance = body["acceptance"]
    if type(acceptance) is not list or len(acceptance) > _BOUNDS["acceptance_entries"]:
        raise ReviewerReturnContractError("invalid acceptance entries")
    supplied: dict[str, str] = {}
    for entry in acceptance:
        item = _fields(entry, _ACCEPTANCE_FIELDS, "acceptance entry")
        criterion_id, outcome = item["id"], item["outcome"]
        if type(criterion_id) is not str or criterion_id not in task_ids or criterion_id in supplied:
            raise ReviewerReturnContractError("unknown or duplicate acceptance id")
        if type(outcome) is not str or outcome not in _GRAMMAR["acceptance_outcomes"]:
            raise ReviewerReturnContractError("unknown acceptance outcome")
        supplied[criterion_id] = outcome
    if review_mode == "PRIMARY" and set(supplied) != task_ids:
        raise ReviewerReturnContractError("PRIMARY requires complete Task acceptance")
    if review_mode == "DELTA" and prior_finding_basis not in supplied:
        raise ReviewerReturnContractError("DELTA requires exact prior finding basis")
    findings = body["findings"]
    if type(findings) is not list or len(findings) > _BOUNDS["findings"]:
        raise ReviewerReturnContractError("invalid findings")
    for finding in findings:
        item = _fields(finding, _FINDING_FIELDS, "finding")
        if type(item["basis"]) is not str or item["basis"] not in task_ids:
            raise ReviewerReturnContractError("finding basis must be a Task acceptance id")
        if type(item["action"]) is not str or item["action"] not in _GRAMMAR["finding_actions"]:
            raise ReviewerReturnContractError("unknown finding action")
        for field in ("location", "issue", "expected"):
            _nonempty_text(item[field], field, _BOUNDS[f"{field}_bytes"])
    failed = {criterion_id for criterion_id, outcome in supplied.items() if outcome == "FAIL"}
    if verdict == "PASS" and (findings or failed):
        raise ReviewerReturnContractError("PASS requires no findings or acceptance FAIL")
    if verdict == "CHANGES_REQUIRED":
        if not failed or not findings:
            raise ReviewerReturnContractError("CHANGES_REQUIRED requires FAIL and findings")
        finding_bases = {finding["basis"] for finding in findings}
        if not finding_bases <= failed or not failed <= finding_bases:
            raise ReviewerReturnContractError("findings must cover exactly supplied FAIL bases")
    return {"verdict": verdict,
            "acceptance": [{"id": criterion_id, "outcome": supplied[criterion_id]}
                           for criterion_id in task_order if criterion_id in supplied],
            "findings": findings}


__all__ = [
    "ReviewerReturnContractError", "normalize_reviewer_return_registry",
    "parse_reviewer_return_registry", "select_reviewer_return_contract",
    "reviewer_return_contract_ref", "parse_reviewer_semantic_body",
]
