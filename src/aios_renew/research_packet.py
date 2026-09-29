"""Transient RA-6 research context and bounded architecture handoff.

All research material is supplied by the caller. Only reviewed RA contracts
validate it; neither projection discovers sources nor grants lifecycle authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from .brain_context import BrainContextError, BrainWorkContext, FlowResolution, resolve_flow
from .research_contract import ResearchContractError, validate_research_brief
from .research_record import (
    normalize_research_audit_profile, project_research_reuse,
    research_audit_profile_ref, validate_research_record,
)


class ResearchPacketError(ValueError):
    """Supplied research context cannot form a bounded semantic packet."""


def _json(value: Any, ceiling: int) -> bytes:
    def walk(item: Any, depth: int) -> None:
        if depth > 32:
            raise ResearchPacketError("research projection is too deep")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ResearchPacketError("non-text research key")
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
        elif type(item) is list:
            for child in item:
                walk(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ResearchPacketError("research projection is not strict JSON")
    walk(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ResearchPacketError("invalid UTF-8 research projection") from exc
    if len(encoded) > ceiling:
        raise ResearchPacketError("research projection exceeds byte bound")
    return encoded


def _address(body: dict[str, Any], field: str, ceiling: int) -> dict[str, Any]:
    result = dict(body, **{field: hashlib.sha256(_json(body, ceiling)).hexdigest()})
    _json(result, ceiling)
    return result


@dataclass(frozen=True, slots=True)
class ResearchPacket:
    _body: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self.render())

    def render(self) -> str:
        return _json(self._body, 131072).decode("utf-8")

    @property
    def packet_fingerprint(self) -> str:
        return self._body["packet_fingerprint"]


def compile_research_packet(
    context: BrainWorkContext, resolution: FlowResolution, brief: Any, profile: Any,
    *, existing_record: Any = None, current_basis: Any = None,
    predecessor_validation_witness: Any = None,
) -> ResearchPacket:
    """Bind one explicit fresh Work Context to caller-supplied RA semantic identities."""
    try:
        if not isinstance(context, BrainWorkContext) or not isinstance(resolution, FlowResolution):
            raise ResearchPacketError("exact Work Context and Flow Resolution required")
        expected = resolve_flow(context)
        if resolution.as_dict() != expected.as_dict() or expected.selected_flow != "RESEARCH" or \
                expected.selection_basis != "EXPLICIT_SELECTOR" or \
                context.current_request is None or context.current_request.get("flow_selector") != "RESEARCH":
            raise ResearchPacketError("explicit RESEARCH resolution required")
        bound_brief = validate_research_brief(brief)
        bound_profile = normalize_research_audit_profile(profile)
        ref = research_audit_profile_ref(bound_profile)
        if existing_record is None:
            if predecessor_validation_witness is not None or current_basis not in (None, []):
                raise ResearchPacketError("fresh research forbids reuse material")
            record_fingerprint, basis_fingerprint, reuse = None, None, None
        else:
            record = validate_research_record(existing_record, bound_profile,
                                              predecessor_validation_witness)
            if record["research_brief"] != bound_brief or record["audit_profile_ref"] != ref:
                raise ResearchPacketError("Research Record Brief/profile substitution")
            if current_basis is None:
                raise ResearchPacketError("record reuse requires declared current basis")
            reuse = project_research_reuse(record, bound_profile, current_basis,
                                           predecessor_validation_witness)
            # RA-3 validates closed, bounded, unique basis entries above. Sorting
            # removes caller ordering without changing the declared identities.
            basis = sorted(json.loads(_json(current_basis, 65536)),
                           key=lambda item: (item["kind"], item["locator"]))
            basis_fingerprint = hashlib.sha256(_json(basis, 65536)).hexdigest()
            record_fingerprint = record["record_fingerprint"]
        body = {
            "format": "AIOS_RESEARCH_PACKET", "version": 1, "kind": "RESEARCH_PACKET",
            "work_context_fingerprint": context.invalidation_fingerprint,
            "selected_flow": "RESEARCH", "selection_basis": "EXPLICIT_SELECTOR",
            "authority_owner": "BRAIN", "decision_family_ref": "HUMAN_BRAIN_RESEARCH_ASSURANCE",
            "handoff_target": "RESEARCH_PROTOCOL", "expected_return_shape": "RESEARCH_PACKET",
            "research_brief": bound_brief, "audit_profile": bound_profile,
            "existing_record_fingerprint": record_fingerprint,
            "current_basis_fingerprint": basis_fingerprint, "reuse_projection": reuse,
            "pending_canonical_obligation": resolution.pending_canonical_obligation,
            "pending_canonical_authority_owner": resolution.pending_canonical_authority_owner,
            "canonical_next_action": resolution.canonical_next_action,
            "unified_state_next_action": resolution.unified_state_next_action,
            "requires_fresh_context_for_continuation": True,
        }
        return ResearchPacket(_address(body, "packet_fingerprint", 131072))
    except (BrainContextError, ResearchContractError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ResearchPacketError):
            raise
        raise ResearchPacketError("invalid or stale research packet basis") from exc


@dataclass(frozen=True, slots=True)
class ResearchArchitectureHandoff:
    _body: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self.render())

    def render(self) -> str:
        return _json(self._body, 131072).decode("utf-8")

    @property
    def handoff_fingerprint(self) -> str:
        return self._body["handoff_fingerprint"]


def project_research_architecture_handoff(
    record: Any, profile: Any, *, predecessor_validation_witness: Any = None,
) -> ResearchArchitectureHandoff:
    """Project an exact candidate as cognitive input; do not compose ARCHITECTURE."""
    try:
        bound = validate_research_record(record, profile, predecessor_validation_witness)
        if bound["closure"]["outcome"] != "RESEARCH_CANDIDATE" or \
                bound["access_scope"] != "PUBLIC":
            raise ResearchPacketError("public RESEARCH_CANDIDATE required for handoff")
        material = set(bound["closure"]["material_claim_fingerprints"])
        claims = [{"claim_fingerprint": claim["claim_fingerprint"],
                   "claim_kind": claim["claim_kind"], "statement": claim["statement"],
                   "uncertainty": claim["uncertainty"]}
                  for claim in bound["claims"] if claim["claim_fingerprint"] in material]
        brief = bound["research_brief"]
        text = [brief["question"], brief["decision_context"],
                *brief["scope"]["include"], *brief["scope"]["exclude"],
                bound["project_assessment"]["summary"], bound["closure"]["summary"]]
        text += [claim["statement"] for claim in claims]
        excerpts = [source["retained_excerpt"] for source in bound["sources"]
                    if source["retained_excerpt"]]
        if any(excerpt in item for excerpt in excerpts for item in text):
            raise ResearchPacketError("source excerpt cannot enter architecture handoff")
        body = {
            "format": "AIOS_RESEARCH_ARCHITECTURE_HANDOFF", "version": 1,
            "kind": "RESEARCH_ARCHITECTURE_HANDOFF", "handoff_target": "ARCHITECTURE",
            "record_fingerprint": bound["record_fingerprint"],
            "brief_fingerprint": brief["brief_fingerprint"],
            "research_question": brief["question"],
            "decision_context": brief["decision_context"],
            "brief_scope": brief["scope"],
            "current_as_of": brief["current_as_of"],
            "audit_profile_ref": bound["audit_profile_ref"],
            "closure": bound["closure"], "project_assessment": bound["project_assessment"],
            "material_claims": claims,
        }
        return ResearchArchitectureHandoff(_address(body, "handoff_fingerprint", 131072))
    except (ResearchContractError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ResearchPacketError):
            raise
        raise ResearchPacketError("invalid Research Record handoff basis") from exc


__all__ = ["ResearchPacket", "ResearchPacketError", "compile_research_packet",
           "ResearchArchitectureHandoff", "project_research_architecture_handoff"]
