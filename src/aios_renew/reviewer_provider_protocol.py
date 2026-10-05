"""Pure BP6-P3 Reviewer request and transient decision protocol.

Every input is caller supplied. This module has no provider, repository, ingress,
or lifecycle authority.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
from typing import Any, Mapping

from .artifacts import validate_result
from .decision_packet import DecisionPacket
from .review import parse_review, validate_review
from .review_material import validate_review_material_package
from .reviewer_procedure import select_reviewer_procedure
from .reviewer_return_contract import (
    parse_reviewer_semantic_body, select_reviewer_return_contract,
)
from .task import validate_task


class ReviewerProviderProtocolError(ValueError):
    """Invalid caller material or provider semantic response; never a verdict."""


_PACKET_FIELDS = frozenset({
    "format", "version", "kind", "work_context_fingerprint", "selected_flow",
    "selection_basis", "authority_owner", "decision_family_ref", "handoff_target",
    "expected_return_shape", "pending_canonical_obligation",
    "pending_canonical_authority_owner", "requires_fresh_context_for_continuation",
    "canonical_facts", "canonical_blocker", "bounded_observations",
    "executor_claims", "prior_semantic_decisions", "human_input", "subject",
    "run_created", "executor_invoked", "verification_invoked", "state_mutated",
    "packet_fingerprint",
})
_FACT_FIELDS = frozenset({
    "repository", "main_sha", "roadmap", "selected_task", "task_contract",
    "selection_status", "lifecycle_state", "canonical_next_action",
    "unified_state_next_action",
})
_SUBJECT_FIELDS = frozenset({
    "run_id", "task", "base_sha", "head_sha", "changed_files", "unresolved",
})
_CLAIM_FIELDS = frozenset({"id", "satisfies", "claim", "evidence_ids"})
_SCOPE_FIELDS = frozenset({
    "format", "version", "kind", "task", "reviewed_run_id", "review_mode",
    "semantic_origin_run_id", "semantic_base_sha", "latest_delta_base_sha",
    "reviewed_head_sha", "prior_review_run_id", "prior_review_id",
    "prior_finding_id", "scope_fingerprint",
})
_REQUEST_FIELDS = frozenset({
    "format", "version", "kind", "decision_packet", "review_scope",
    "review_material_package", "reviewer_procedure_package",
    "reviewer_return_contract_package", "prior_review", "external_bindings",
    "request_fingerprint",
})
_DECISION_FIELDS = frozenset({
    "format", "version", "kind", "authority_owner", "selected_flow",
    "decision_family_ref", "handoff_target", "expected_return_shape",
    "request_fingerprint", "packet_fingerprint", "review_scope_fingerprint",
    "review_material_fingerprint", "reviewer_procedure_ref",
    "reviewer_return_contract_ref", "review_candidate", "decision_fingerprint",
})
_FLOW = {
    "authority_owner": "REVIEWER", "selected_flow": "SEMANTIC_REVIEW",
    "decision_family_ref": "review.validate_review",
    "handoff_target": "AUTHORING_INGRESS",
    "expected_return_shape": "REVIEW_CONTRACT_PROPOSAL",
}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_OPERATIONAL = frozenset({
    "provider", "model", "session", "invocation", "transport", "endpoint",
    "host", "credential", "credentials", "api_key", "token", "usage",
    "latency", "timestamp", "request_id", "response_id", "finish_reason",
    "raw_response", "operational_metadata", "provider_metadata",
    "model_metadata", "session_metadata", "request_metadata",
    "provider_id", "provider_name", "model_id", "model_name", "session_id",
    "session_name", "invocation_id", "endpoint_url", "host_id", "hostname",
})
_OPERATIONAL_COMPACT = frozenset(key.replace("_", "") for key in _OPERATIONAL)


def _normal(value: Any, depth: int = 0, *, response: bool = False) -> Any:
    if depth > 32:
        raise ReviewerProviderProtocolError("structural depth exceeds 32")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is str:
        value.encode("utf-8", "strict")
        return value
    if type(value) is list:
        return [_normal(item, depth + 1, response=response) for item in value]
    if isinstance(value, Mapping):
        if response and value.get("format") in {"AIOS_REVIEW_REQUEST", "AIOS_REVIEW_DECISION"}:
            raise ReviewerProviderProtocolError("nested Reviewer envelope")
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ReviewerProviderProtocolError("non-string mapping key")
            key.encode("utf-8", "strict")
            folded = key.lower().replace("-", "_")
            if folded in _OPERATIONAL or folded.replace("_", "") in _OPERATIONAL_COMPACT:
                raise ReviewerProviderProtocolError("operational metadata in semantic material")
            if key in result:
                raise ReviewerProviderProtocolError("duplicate mapping key")
            result[key] = _normal(item, depth + 1, response=response)
        return result
    raise ReviewerProviderProtocolError("non-JSON semantic material")


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _bound(value: Any, ceiling: int, name: str) -> None:
    if len(_json(value)) > ceiling:
        raise ReviewerProviderProtocolError(f"{name} exceeds {ceiling} UTF-8 bytes")


def _closed(value: Any, fields: frozenset[str] | set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ReviewerProviderProtocolError(f"{name} fields do not match closed contract")
    return value


def _fingerprint(value: Any, name: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise ReviewerProviderProtocolError(f"invalid {name}")
    return value


def _parse(value: Any, *, response: bool = False) -> Any:
    if type(value) in (bytes, str):
        try:
            text = value.decode("utf-8", "strict") if type(value) is bytes else value
            text.encode("utf-8", "strict")
            value = json.loads(text, object_pairs_hook=_unique_pairs,
                               parse_constant=_invalid_constant)
        except (UnicodeError, ValueError, RecursionError) as exc:
            if isinstance(exc, ReviewerProviderProtocolError):
                raise
            raise ReviewerProviderProtocolError("invalid strict UTF-8 JSON") from exc
    return _normal(value, response=response)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewerProviderProtocolError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ReviewerProviderProtocolError(f"non-JSON constant {value}")


def _packet(value: Any) -> tuple[dict[str, Any], Any, Any]:
    packet = _closed(value, _PACKET_FIELDS, "Decision Packet")
    if (packet["format"] != "AIOS_DECISION_PACKET" or type(packet["version"]) is not int
            or packet["version"] != 1 or packet["kind"] != "DECISION_PACKET"
            or any(packet[key] != expected for key, expected in _FLOW.items())
            or any(packet[key] is not False for key in
                   ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))):
        raise ReviewerProviderProtocolError("not an exact SEMANTIC_REVIEW Decision Packet")
    if (_fingerprint(packet["packet_fingerprint"], "packet fingerprint") !=
            _digest({key: val for key, val in packet.items() if key != "packet_fingerprint"})):
        raise ReviewerProviderProtocolError("Decision Packet fingerprint mismatch")
    _fingerprint(packet["work_context_fingerprint"], "Work Context fingerprint")
    _bound(packet, 131072, "Decision Packet")
    facts = _closed(packet["canonical_facts"], _FACT_FIELDS, "packet canonical facts")
    subject = _closed(packet["subject"], _SUBJECT_FIELDS, "packet subject")
    task_contract = _closed(facts["task_contract"], {
        "task_id", "revision", "goal", "problem", "assumptions", "scope",
        "non_goals", "constraints", "acceptance", "verification", "return_affinity",
    }, "packet Task")
    # Affinity is required projected context. Existing TASK validation and the
    # exact round trip below preserve its closed shape without resolving a route.
    task_input = json.loads(_json(task_contract))
    verification = _closed(task_input["verification"],
                           {"required", "policy", "full_suite_reason"},
                           "packet Task verification")
    for optional in ("policy", "full_suite_reason"):
        if verification[optional] is None:
            del verification[optional]
    task = validate_task(task_input)
    if json.loads(_json(asdict(task))) != task_contract:
        raise ReviewerProviderProtocolError("packet Task canonical projection drift")
    identity = {"id": task.task_id, "revision": task.revision}
    if (_closed(subject["task"], {"id", "revision"}, "subject task") != identity
            or _closed(facts["selected_task"], {"id", "revision"}, "selected task") != identity
            or facts["canonical_next_action"] != "SEMANTIC_REVIEW"
            or facts["unified_state_next_action"] != "SEMANTIC_REVIEW"
            or facts["roadmap"] is not None
            or type(subject["run_id"]) is not str or not subject["run_id"]
            or type(subject["head_sha"]) is not str or _SHA.fullmatch(subject["head_sha"]) is None
            or type(subject["base_sha"]) is not str or _SHA.fullmatch(subject["base_sha"]) is None):
        raise ReviewerProviderProtocolError("packet Task, lifecycle or subject mismatch")
    claims = packet["executor_claims"]
    if type(claims) is not list:
        raise ReviewerProviderProtocolError("packet claims must be a list")
    acceptance_ids = {criterion.id for criterion in task.acceptance}
    for claim in claims:
        _closed(claim, _CLAIM_FIELDS, "packet claim")
        if not set(claim["satisfies"]) <= acceptance_ids:
            raise ReviewerProviderProtocolError("packet claim references unknown acceptance")
    observations = packet["bounded_observations"]
    if type(observations) is not list or len(observations) > 128:
        raise ReviewerProviderProtocolError("packet Runtime observations invalid")
    observed_ids = set()
    for observation in observations:
        _closed(observation, {"evidence_id", "type", "exit_code", "summary"},
                "packet Runtime observation")
        evidence_id = observation["evidence_id"]
        if (type(evidence_id) is not str or not evidence_id or evidence_id in observed_ids
                or type(observation["type"]) is not str
                or type(observation["exit_code"]) is not int
                or type(observation["summary"]) is not str):
            raise ReviewerProviderProtocolError("packet Runtime observation invalid")
        observed_ids.add(evidence_id)
    if any(not set(claim["evidence_ids"]) <= observed_ids for claim in claims):
        raise ReviewerProviderProtocolError("packet claim references missing Runtime observation")
    result = validate_result({
        "head_sha": subject["head_sha"], "changed_files": subject["changed_files"],
        "unresolved": subject["unresolved"],
        "claims": [{"id": c["id"], "satisfies": c["satisfies"], "claim": c["claim"],
                    "evidence": c["evidence_ids"]} for c in claims],
    })
    if (list(result.changed_files) != subject["changed_files"]
            or list(result.unresolved) != subject["unresolved"]
            or any(asdict(c) != {"id": raw["id"], "satisfies": tuple(raw["satisfies"]),
                                      "claim": raw["claim"], "evidence": tuple(raw["evidence_ids"])}
                   for c, raw in zip(result.claims, claims))):
        raise ReviewerProviderProtocolError("packet RESULT projection drift")
    return packet, task, result


def _scope(value: Any, packet: dict[str, Any], task: Any, result: Any) -> dict[str, Any]:
    scope = _closed(value, _SCOPE_FIELDS, "review scope")
    if (scope["format"] != "AIOS_SEMANTIC_REVIEW_SCOPE"
            or type(scope["version"]) is not int or scope["version"] != 1
            or scope["kind"] != "SEMANTIC_REVIEW_SCOPE"
            or _fingerprint(scope["scope_fingerprint"], "scope fingerprint") !=
            hashlib.sha256(json.dumps(
                {k: v for k, v in scope.items() if k != "scope_fingerprint"},
                sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode("utf-8")).hexdigest()
            or _closed(scope["task"], {"id", "revision"}, "scope task") !=
            {"id": task.task_id, "revision": task.revision}
            or scope["reviewed_run_id"] != packet["subject"]["run_id"]
            or scope["reviewed_head_sha"] != result.head_sha
            or scope["latest_delta_base_sha"] != packet["subject"]["base_sha"]
            or scope["review_mode"] not in ("PRIMARY", "DELTA")):
        raise ReviewerProviderProtocolError("scope identity or packet binding mismatch")
    for key in ("semantic_base_sha", "latest_delta_base_sha", "reviewed_head_sha"):
        if type(scope[key]) is not str or re.fullmatch(r"[0-9a-f]{40}", scope[key]) is None:
            raise ReviewerProviderProtocolError("invalid scope SHA")
    for key in ("semantic_origin_run_id",):
        if type(scope[key]) is not str or not scope[key]:
            raise ReviewerProviderProtocolError("invalid scope origin")
    prior = (scope["prior_review_run_id"], scope["prior_review_id"], scope["prior_finding_id"])
    if scope["review_mode"] == "PRIMARY":
        if prior != (None, None, None):
            raise ReviewerProviderProtocolError("PRIMARY has prior review identity")
    elif any(type(item) is not str or not item for item in prior):
        raise ReviewerProviderProtocolError("DELTA prior review identity missing")
    correction = packet["prior_semantic_decisions"]
    if correction is not None and type(correction) is not dict:
        raise ReviewerProviderProtocolError("packet correction projection invalid")
    if correction is not None and correction.get("kind") == "REMEDIATION":
        predecessor = _closed(correction.get("predecessor"),
                              {"source_run_id", "review_id", "finding_id", "reviewed_sha"},
                              "packet semantic predecessor")
        if scope["review_mode"] != "DELTA" or any((
            predecessor["source_run_id"] != scope["prior_review_run_id"],
            predecessor["review_id"] != scope["prior_review_id"],
            predecessor["finding_id"] != scope["prior_finding_id"],
            predecessor["reviewed_sha"] != scope["semantic_base_sha"],
        )):
            raise ReviewerProviderProtocolError("packet and scope semantic predecessor mismatch")
    return scope


def _prior(value: Any, scope: dict[str, Any]) -> tuple[dict[str, Any] | None, Any, str | None]:
    if scope["review_mode"] == "PRIMARY":
        if value is not None:
            raise ReviewerProviderProtocolError("PRIMARY requires null prior_review")
        return None, None, None
    if type(value) is not dict:
        raise ReviewerProviderProtocolError("DELTA requires complete prior REVIEW")
    _bound(value, 196608, "prior REVIEW")
    review = parse_review(_json(value).decode("utf-8"))
    comparable = dict(value)
    if comparable.get("prior_finding_id", object()) is None:
        del comparable["prior_finding_id"]
    if _review_mapping(review) != comparable:
        raise ReviewerProviderProtocolError("prior REVIEW canonical round-trip drift")
    selected = [f for f in review.findings if f.id == scope["prior_finding_id"]]
    if (review.review_id != scope["prior_review_id"]
            or review.reviewed_sha != scope["semantic_base_sha"] or len(selected) != 1):
        raise ReviewerProviderProtocolError("prior REVIEW lineage mismatch")
    return value, review, selected[0].basis


def _identity(value: Any) -> str:
    if (type(value) is not str or not value or value != value.strip()
            or len(value.encode("utf-8", "strict")) > 256):
        raise ReviewerProviderProtocolError("invalid external identity")
    return value


def _bindings(value: Any) -> dict[str, Any]:
    bindings = _closed(value, {"review_id", "finding_id_slots"}, "external bindings")
    review_id = _identity(bindings["review_id"])
    slots = bindings["finding_id_slots"]
    if type(slots) is not list or len(slots) != 32:
        raise ReviewerProviderProtocolError("exactly 32 finding identity slots required")
    if len({_identity(slot) for slot in slots}) != 32 or review_id in slots:
        raise ReviewerProviderProtocolError("duplicate external identity")
    return bindings


def _packages(request: dict[str, Any], packet: dict[str, Any], scope: dict[str, Any]) -> None:
    material = validate_review_material_package(request["review_material_package"])
    if any(material[material_key] != scope[scope_key] for material_key, scope_key in (
        ("review_scope_fingerprint", "scope_fingerprint"), ("review_mode", "review_mode"),
        ("semantic_base_sha", "semantic_base_sha"),
        ("latest_delta_base_sha", "latest_delta_base_sha"),
        ("reviewed_head_sha", "reviewed_head_sha"),
    )):
        raise ReviewerProviderProtocolError("P1B material and P1A scope mismatch")
    procedure = _closed(request["reviewer_procedure_package"],
                        {"profile", "procedure", "bounds", "reviewer_procedure_ref"},
                        "procedure package")
    selected_procedure = select_reviewer_procedure({
        "format": "AIOS_REVIEWER_PROCEDURE_PROFILES", "version": 1,
        "profiles": [procedure["profile"]], "bounds": procedure["bounds"],
    }, scope["review_mode"])
    if procedure != selected_procedure:
        raise ReviewerProviderProtocolError("P2A procedure selection mismatch")
    returns = _closed(request["reviewer_return_contract_package"],
                      {"contract", "bounds", "reviewer_return_contract_ref"},
                      "return contract package")
    selected_return = select_reviewer_return_contract({
        "format": "AIOS_REVIEWER_RETURN_CONTRACTS", "version": 1,
        "contracts": [returns["contract"]], "bounds": returns["bounds"],
    })
    if returns != selected_return:
        raise ReviewerProviderProtocolError("P2B return contract selection mismatch")
    for source in (procedure["profile"], returns["contract"]):
        if any(source[key] != packet[key] for key in _FLOW):
            raise ReviewerProviderProtocolError("Reviewer profile and packet metadata mismatch")


def construct_request(
    packet: DecisionPacket, review_scope: Mapping[str, Any],
    review_material_package: Mapping[str, Any],
    reviewer_procedure_package: Mapping[str, Any],
    reviewer_return_contract_package: Mapping[str, Any],
    external_bindings: Mapping[str, Any], *, prior_review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind an actual freshly compiled packet and exact P1/P2 packages."""
    if not isinstance(packet, DecisionPacket):
        raise ReviewerProviderProtocolError("fresh DecisionPacket object required")
    try:
        request = {
            "format": "AIOS_REVIEW_REQUEST", "version": 1, "kind": "REVIEW_REQUEST",
            "decision_packet": packet.as_dict(), "review_scope": review_scope,
            "review_material_package": review_material_package,
            "reviewer_procedure_package": reviewer_procedure_package,
            "reviewer_return_contract_package": reviewer_return_contract_package,
            "prior_review": prior_review, "external_bindings": external_bindings,
        }
        request = _normal(request)
        request["request_fingerprint"] = _digest(request)
        return revalidate_request(request)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ReviewerProviderProtocolError):
            raise
        raise ReviewerProviderProtocolError("invalid Reviewer request material") from exc


def revalidate_request(value: Mapping[str, Any] | str | bytes) -> dict[str, Any]:
    """Purely revalidate one complete serialized or decoded request."""
    try:
        request = _closed(_parse(value), _REQUEST_FIELDS, "Reviewer request")
        if (request["format"] != "AIOS_REVIEW_REQUEST" or type(request["version"]) is not int
                or request["version"] != 1 or request["kind"] != "REVIEW_REQUEST"):
            raise ReviewerProviderProtocolError("invalid Reviewer request envelope")
        _bound(request, 2097152, "Reviewer request")
        packet, task, result = _packet(request["decision_packet"])
        scope = _scope(request["review_scope"], packet, task, result)
        _packages(request, packet, scope)
        _prior(request["prior_review"], scope)
        _bindings(request["external_bindings"])
        if (_fingerprint(request["request_fingerprint"], "request fingerprint") !=
                _digest({k: v for k, v in request.items() if k != "request_fingerprint"})):
            raise ReviewerProviderProtocolError("Reviewer request fingerprint mismatch")
        return request
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ReviewerProviderProtocolError):
            raise
        raise ReviewerProviderProtocolError("invalid Reviewer request material") from exc


def _review_mapping(review: Any) -> dict[str, Any]:
    candidate = {
        "review_id": review.review_id, "reviewed_sha": review.reviewed_sha,
        "mode": review.mode, "verdict": review.verdict,
        "acceptance": dict(review.acceptance),
        "findings": [asdict(f) for f in review.findings],
    }
    if review.prior_finding_id is not None:
        candidate["prior_finding_id"] = review.prior_finding_id
    return candidate


def _materialize(request: dict[str, Any], semantic: dict[str, Any]) -> dict[str, Any]:
    scope = request["review_scope"]
    bindings = request["external_bindings"]
    findings = semantic["findings"]
    slots = bindings["finding_id_slots"]
    if len(findings) > len(slots):
        raise ReviewerProviderProtocolError("finding identity slots exhausted")
    candidate = {
        "review_id": bindings["review_id"], "reviewed_sha": scope["reviewed_head_sha"],
        "mode": scope["review_mode"], "verdict": semantic["verdict"],
        "acceptance": {entry["id"]: entry["outcome"] for entry in semantic["acceptance"]},
        "findings": [{"id": slots[index], **finding}
                     for index, finding in enumerate(findings)],
    }
    if scope["review_mode"] == "DELTA":
        candidate["prior_finding_id"] = scope["prior_finding_id"]
    parsed = parse_review(_json(candidate).decode("utf-8"))
    if _review_mapping(parsed) != candidate:
        raise ReviewerProviderProtocolError("REVIEW canonical round-trip drift")
    packet, task, result = _packet(request["decision_packet"])
    _, prior, _ = _prior(request["prior_review"], scope)
    validate_review(task=task, result=result, review=parsed, prior_review=prior)
    return candidate


def validate_response(request: Mapping[str, Any] | str | bytes,
                      response: Mapping[str, Any] | str | bytes) -> dict[str, Any]:
    """Validate provider semantics and create one transient REVIEW decision."""
    try:
        req = revalidate_request(request)
        supplied = _closed(_parse(response, response=True),
                           {"request_fingerprint", "semantic_body"}, "provider response")
        _bound(supplied, 196608, "provider response")
        if supplied["request_fingerprint"] != req["request_fingerprint"]:
            raise ReviewerProviderProtocolError("provider request echo mismatch")
        _, task, _ = _packet(req["decision_packet"])
        _, _, basis = _prior(req["prior_review"], req["review_scope"])
        semantic = parse_reviewer_semantic_body(
            supplied["semantic_body"], task=task,
            review_mode=req["review_scope"]["review_mode"], prior_finding_basis=basis,
        )
        candidate = _materialize(req, semantic)
        packet = req["decision_packet"]
        decision = {
            "format": "AIOS_REVIEW_DECISION", "version": 1, "kind": "REVIEW_DECISION",
            **_FLOW, "request_fingerprint": req["request_fingerprint"],
            "packet_fingerprint": packet["packet_fingerprint"],
            "review_scope_fingerprint": req["review_scope"]["scope_fingerprint"],
            "review_material_fingerprint": req["review_material_package"]["package_fingerprint"],
            "reviewer_procedure_ref": req["reviewer_procedure_package"]["reviewer_procedure_ref"],
            "reviewer_return_contract_ref": req["reviewer_return_contract_package"]["reviewer_return_contract_ref"],
            "review_candidate": candidate,
        }
        decision["decision_fingerprint"] = _digest(decision)
        _bound(decision, 262144, "Reviewer decision")
        return decision
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ReviewerProviderProtocolError):
            raise
        raise ReviewerProviderProtocolError("invalid Reviewer provider semantics") from exc


def revalidate_decision(value: Mapping[str, Any] | str | bytes,
                        request: Mapping[str, Any] | str | bytes) -> dict[str, Any]:
    """Revalidate exact request lineage and candidate through the same path."""
    try:
        req = revalidate_request(request)
        decision = _closed(_parse(value), _DECISION_FIELDS, "Reviewer decision")
        if (decision["format"] != "AIOS_REVIEW_DECISION"
                or type(decision["version"]) is not int or decision["version"] != 1
                or decision["kind"] != "REVIEW_DECISION"
                or any(decision[key] != expected for key, expected in _FLOW.items())):
            raise ReviewerProviderProtocolError("invalid Reviewer decision envelope")
        _bound(decision, 262144, "Reviewer decision")
        if (_fingerprint(decision["decision_fingerprint"], "decision fingerprint") !=
                _digest({k: v for k, v in decision.items() if k != "decision_fingerprint"})):
            raise ReviewerProviderProtocolError("decision fingerprint mismatch")
        for key, expected in (
            ("request_fingerprint", req["request_fingerprint"]),
            ("packet_fingerprint", req["decision_packet"]["packet_fingerprint"]),
            ("review_scope_fingerprint", req["review_scope"]["scope_fingerprint"]),
            ("review_material_fingerprint", req["review_material_package"]["package_fingerprint"]),
            ("reviewer_procedure_ref", req["reviewer_procedure_package"]["reviewer_procedure_ref"]),
            ("reviewer_return_contract_ref", req["reviewer_return_contract_package"]["reviewer_return_contract_ref"]),
        ):
            if decision[key] != expected:
                raise ReviewerProviderProtocolError("decision request lineage mismatch")
        candidate = decision["review_candidate"]
        if type(candidate) is not dict:
            raise ReviewerProviderProtocolError("invalid REVIEW candidate")
        _, task, _ = _packet(req["decision_packet"])
        _, _, basis = _prior(req["prior_review"], req["review_scope"])
        semantic_source = {
            "verdict": candidate.get("verdict"),
            "acceptance": [{"id": key, "outcome": outcome}
                           for key, outcome in candidate.get("acceptance", {}).items()],
            "findings": [{key: value for key, value in finding.items() if key != "id"}
                         for finding in candidate.get("findings", [])],
        }
        semantic = parse_reviewer_semantic_body(
            semantic_source, task=task, review_mode=req["review_scope"]["review_mode"],
            prior_finding_basis=basis,
        )
        if _materialize(req, semantic) != candidate:
            raise ReviewerProviderProtocolError("decision REVIEW candidate mismatch")
        return decision
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ReviewerProviderProtocolError):
            raise
        raise ReviewerProviderProtocolError("invalid Reviewer decision material") from exc


__all__ = [
    "ReviewerProviderProtocolError", "construct_request", "revalidate_request",
    "validate_response", "revalidate_decision",
]
