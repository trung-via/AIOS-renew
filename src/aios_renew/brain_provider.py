"""Transient BP5-P3 invocation shell over the reviewed Brain semantic protocol.

Transport callables, packets and freshness material are caller supplied. Ordinary
new audits obtain their approved policy and profile from canonical Git.
No provider result or attempt retains a native response or conversation history.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from .brain_provider_protocol import BrainProviderProtocolError, construct_request, validate_response
from .brain_audit import _ADAPTIVE_ID, _ADAPTIVE_DIGEST, _packet
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
    advisory: Mapping[str, Any] | None = None


def _advisory_draft(packet, *, task_source_context=None, architecture_source_context=None):
    """Project the composed decision subject into the existing six-field draft.

    This is source/draft composition, not a proposal from CAA. No advice, cause,
    consumer declaration or missing obligation is synthesized here.
    """
    body = _packet(packet)
    task = ((task_source_context or {}).get("task") or body["canonical_facts"].get("task_contract") or {})
    architecture = architecture_source_context or {}
    goal = task.get("goal") or architecture.get("objective") or body.get("human_input") or body["expected_return_shape"]
    if type(goal) is not str:
        goal = _json_bytes(goal).decode("utf-8")
    return dict(goal=goal, invariants=deepcopy(task.get("constraints", {}).get("hard", [])),
        acceptance=[entry["condition"] for entry in task.get("acceptance", [])],
        scope=deepcopy(task.get("scope", dict(inspect=[], modify=[]))), consumers=[],
        verification=deepcopy(task.get("verification", {}).get("required", [])))


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
            fresh_packet_supplier: Callable[[], DecisionPacket] | None = None,
            *, conformance_only: bool = False,
            moa_source_context: Mapping[str, Any] | None = None,
            task_source_context: Mapping[str, Any] | None = None,
            architecture_source_context: Mapping[str, Any] | None = None,
            human_v1_option: Mapping[str, Any] | None = None,
            audit_policy: Mapping[str, Any] | None = None,
            selection_authenticator: Callable[[Mapping[str, Any], str], bool] | None = None,
            human_v1_deep_support: Mapping[str, Any] | None = None,
            human_audit_choice: Mapping[str, Any] | None = None,
            repo: str | Path | None = None,
            human_choice_authenticator: Callable[[Mapping[str, Any], str], bool] | None = None,
            advisory_candidate: Mapping[str, Any] | None = None) -> AttemptResult:
    """Invoke one normative procedure, or explicit isolated compatibility support.

    Canonical policy selects direct authoring, soft AOA, or a trusted Human's
    exact on-demand audit. Explicit isolated historical conformance remains
    decodable. No provider result admits or mutates a canonical artifact.
    """
    count = 0
    phase = "INPUT"
    try:
        if not isinstance(packet, DecisionPacket):
            raise ValueError("DecisionPacket required")
        if type(conformance_only) is not bool:
            raise ValueError("conformance_only must be explicit boolean")
        configured = (provider.provider, provider.model)
        _identity(*configured)
        if not callable(provider.invoke):
            raise ValueError("provider invocation required")
        flow = packet.as_dict()["selected_flow"]
        if not conformance_only and flow != "DIAGNOSTIC" and audit_policy is None and selection_authenticator is None:
            from .brain_context import canonical_audit_inputs
            # The caller-owned fresh composition is independent of the submitted
            # packet. Check the work/subject before the first provider call too.
            try:
                current = fresh_packet_supplier()
                if not isinstance(current, DecisionPacket) or _packet(current) != _packet(packet):
                    raise ValueError("changed Brain subject before admission")
            except Exception as exc:
                raise ValueError("fresh Brain composition required before admission") from exc
            audit_policy, audit_profile_package, selection_authenticator = canonical_audit_inputs(
                packet, return_contract_package, external_bindings, audit_profile_package, repo=repo,
                moa_source_context=moa_source_context, task_source_context=task_source_context,
                architecture_source_context=architecture_source_context, human_v1_option=human_v1_option,
                human_v1_deep_support=human_v1_deep_support,
                human_audit_choice=human_audit_choice,
                human_choice_authenticator=human_choice_authenticator)
        elif human_choice_authenticator is not None:
            raise ValueError("individual Human authentication requires the canonical first-party policy path")
        if not conformance_only and flow != "DIAGNOSTIC" and (
                audit_policy is None or not callable(selection_authenticator)):
            raise ValueError("production audit policy and authenticator required; no fallback")
        prospective = (audit_policy is not None or selection_authenticator is not None
                       or human_v1_deep_support is not None)
        if prospective and conformance_only:
            raise ValueError("prospective policy cannot be activated by isolated conformance")
        mode = ("PROSPECTIVE" if prospective else
                "V1_OPTION_CONSTRUCT" if conformance_only and human_v1_option is not None else
                "AUDIT_CONSTRUCT" if conformance_only and audit_profile_package is not None
                    and audit_profile_package.get("audit_profile_ref") == {
                        "id": _ADAPTIVE_ID, "version": 1, "digest": _ADAPTIVE_DIGEST} else
                "AOA_CONSTRUCT" if conformance_only and packet.as_dict()["selected_flow"] == "ARCHITECTURE" else
                "TCA_CONSTRUCT" if conformance_only and packet.as_dict()["selected_flow"] == "TASK_AUTHORING" else
                "MOA_CONSTRUCT" if conformance_only and packet.as_dict()["selected_flow"] == "REMEDIATION_AUTHORING" else
                "ROA_CONFORMANCE" if conformance_only else
                "DIRECT" if packet.as_dict()["selected_flow"] == "DIAGNOSTIC" else "AUDIT_CONSTRUCT")
        if mode != "DIRECT" and not callable(fresh_packet_supplier):
            raise ValueError("fresh packet supplier required")
        first_request = construct_request(packet, return_contract_package, external_bindings,
                                          audit_profile_package, request_mode=mode,
                                          moa_source_context=moa_source_context, task_source_context=task_source_context,
                                          architecture_source_context=architecture_source_context,
                                          human_v1_option=human_v1_option, audit_policy=audit_policy,
                                          selection_authenticator=selection_authenticator,
                                          human_v1_deep_support=human_v1_deep_support,
                                          human_audit_choice=human_audit_choice)
        mode = first_request["request_mode"]
        selected = first_request.get("audit_selection")
        continuation = {} if selected is None else {
            "audit_policy": selected["policy"], "selection_authenticator": selection_authenticator,
            "selection_phase": "CONTINUE", "human_v1_deep_support": selected["human_v1_deep_support"],
            "human_audit_choice": selected.get("human_audit_choice"),
        }
    except (BrainProviderProtocolError, ProviderResponseError, AttributeError, KeyError, TypeError, ValueError):
        invalid_input = True
    else:
        invalid_input = False
    if invalid_input:
        raise BrainAttemptError("PROTOCOL_INPUT_INVALID", phase, count)

    advisory = None
    if not conformance_only and flow in {"ARCHITECTURE", "TASK_AUTHORING", "REPAIR_AUTHORING", "REMEDIATION_AUTHORING"}:
        from .caa_integration import IntegrationContextError, decision_subject
        from .caa_reasoner import CandidateContextError, candidate_fingerprint
        from .caa_experience import ExperienceError
        try:
            # Flow and canonical policy are already resolved, and the relevant
            # subject/draft has been composed. No opt-in, pilot identity, profile
            # supplier, charge-reservation callback or new provider is involved.
            subject = decision_subject(packet)
            draft = (deepcopy(dict(advisory_candidate)) if advisory_candidate is not None else
                     _advisory_draft(packet, task_source_context=first_request.get("task_source_context", task_source_context),
                                     architecture_source_context=first_request.get("architecture_source_context", architecture_source_context)))
            advisory = consult_advisory(repo or Path.cwd(), flow=flow, packet=packet, subject=subject,
                candidate=draft, expected_candidate_fingerprint=candidate_fingerprint(draft),
                fresh_packet_supplier=fresh_packet_supplier, provider=provider).advisory
        except IntegrationContextError as exc:
            # Older integrations omit CAA repository/selection/correction
            # projections accepted by their existing normative protocol. Missing
            # optional projections cannot add an authoring prerequisite. The
            # distinct missing-field codes never cover invalid supplied values;
            # the existing protocol and fresh-packet guards still apply.
            facts = _packet(packet)["canonical_facts"]
            original, selected_task = facts.get("task_contract"), facts.get("selected_task")
            missing_legacy_projection = (
                str(exc) in {"MANDATORY_REPOSITORY_MISSING", "MANDATORY_CORRECTION_PROJECTION_MISSING"}
                or str(exc) == "MANDATORY_TASK_SUBJECT_MISSING" and selected_task is None
                and "selection_status" not in facts
                or str(exc) == "MANDATORY_CORRECTION_SUBJECT_MISSING" and facts.get("correction_subject") is None
                or str(exc) == "MANDATORY_TASK_CONTRACT_MISSING" and type(original) is dict
                and type(selected_task) is dict and original.get("task_id") == selected_task.get("id")
                and original.get("revision") == selected_task.get("revision")
                and not {"scope", "acceptance", "verification", "constraints"} <= set(original))
            if not missing_legacy_projection:
                raise BrainAttemptError("CAA_SOURCE_INVALID", "CAA_CONSULTATION", count) from None
            advisory = dict(authority="NONE", outcome="UNAVAILABLE", reason=str(exc), consultation=None)
        except CandidateContextError:
            if advisory_candidate is not None:
                raise BrainAttemptError("CAA_CANDIDATE_INVALID", "CAA_CONSULTATION", count) from None
            # CAA's existing draft byte/list limits are not authoring limits.
            advisory = dict(authority="NONE", outcome="UNAVAILABLE", reason="CAA_DRAFT_OVER_BUDGET", consultation=None)
        except ExperienceError:
            raise BrainAttemptError("CAA_SOURCE_INVALID", "CAA_CONSULTATION", count) from None
        try:
            if _packet(fresh_packet_supplier()) != _packet(packet):
                raise ValueError
        except Exception:
            raise BrainAttemptError("STALE_BEFORE_DECISION", "FRESH_PACKET", count) from None

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
            # The same existing transport has already handled any eligible
            # CAA-03 exchange. Keep the Brain request/response grammar exact;
            # return bound advice separately for Brain's final disposition.
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
        if continuation:
            # Direct work gets the same subject/CAS currentness protection,
            # without invoking an audit or fabricating a stage result.
            try:
                final_packet = fresh_packet_supplier()
                final_request = construct_request(
                    final_packet, first_request["return_contract_package"], first_request["external_bindings"],
                    request_mode="DIRECT", **continuation)
                if final_request["request_fingerprint"] != first_request["request_fingerprint"]:
                    raise ValueError("changed direct authoring subject")
            except Exception:
                raise BrainAttemptError("STALE_BEFORE_DIRECT_RETURN", "FRESH_PACKET", count) from None
        return AttemptResult(first, (attribution1,), advisory)
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
    if mode == "ROA_CONFORMANCE":
        try:
            fresh_request = construct_request(fresh, first_request["return_contract_package"],
                                               first_request["external_bindings"],
                                               first_request["audit_profile_package"], request_mode=mode, **continuation)
        except (BrainProviderProtocolError, AttributeError, KeyError, TypeError, ValueError):
            raise BrainAttemptError("FRESH_PACKET_INVALID", phase, count) from None
        if fresh_request["request_fingerprint"] != first_request["request_fingerprint"]:
            raise BrainAttemptError("STALE_BEFORE_ROA_RETURN", phase, count)
        return AttemptResult(first, (attribution1,), advisory)
    if mode in {"MOA_CONSTRUCT", "TCA_CONSTRUCT", "AOA_CONSTRUCT", "V1_OPTION_CONSTRUCT"} or continuation:
        try:
            fresh_request = construct_request(fresh, first_request["return_contract_package"],
                                               first_request["external_bindings"], first_request["audit_profile_package"],
                                               request_mode=mode, moa_source_context=first_request.get("moa_source_context"),
                                               task_source_context=first_request.get("task_source_context"),
                                               architecture_source_context=first_request.get("architecture_source_context"),
                                               human_v1_option=first_request.get("human_v1_option"), **continuation)
        except (BrainProviderProtocolError, AttributeError, KeyError, TypeError, ValueError):
            raise BrainAttemptError("FRESH_PACKET_INVALID", phase, count) from None
        if fresh_request["request_fingerprint"] != first_request["request_fingerprint"]:
            raise BrainAttemptError("STALE_BEFORE_STAGE2", phase, count)
    try:
        if fresh.packet_fingerprint != first_request["decision_packet"]["packet_fingerprint"]:
            raise BrainAttemptError("STALE_BEFORE_STAGE2", phase, count)
        second_mode = {"MOA_CONSTRUCT": "MOA_RECONCILE", "TCA_CONSTRUCT": "TCA_RECONCILE",
                       "AOA_CONSTRUCT": "AOA_RECONCILE",
                       "V1_OPTION_CONSTRUCT": "V1_OPTION_RECONCILE"}.get(mode, "AUDIT_RECONCILE")
        second_request = construct_request(fresh, first_request["return_contract_package"],
                                           first_request["external_bindings"],
                                           first_request["audit_profile_package"], request_mode=second_mode,
                                           stage1_decision=first,
                                           moa_source_context=first_request.get("moa_source_context"),
                                           task_source_context=first_request.get("task_source_context"),
                                           architecture_source_context=first_request.get("architecture_source_context"),
                                           human_v1_option=first_request.get("human_v1_option"), **continuation)
    except BrainAttemptError:
        raise
    except (BrainProviderProtocolError, AttributeError, KeyError, TypeError, ValueError):
        invalid_input = True
    else:
        invalid_input = False
    if invalid_input:
        raise BrainAttemptError("PROTOCOL_INPUT_INVALID", phase, count)
    second, attribution2 = invoke(second_request, second_mode)
    if mode in {"MOA_CONSTRUCT", "TCA_CONSTRUCT", "AOA_CONSTRUCT", "V1_OPTION_CONSTRUCT"} or continuation:
        # Rebuild a request, never rerun an audit, after the adversarial pass.
        try:
            final_packet = fresh_packet_supplier()
        except Exception:
            raise BrainAttemptError("FRESH_PACKET_UNAVAILABLE", "FRESH_PACKET", count) from None
        try:
            final_request = construct_request(final_packet, first_request["return_contract_package"],
                                               first_request["external_bindings"], first_request["audit_profile_package"],
                                               request_mode=mode, moa_source_context=first_request.get("moa_source_context"),
                                               task_source_context=first_request.get("task_source_context"),
                                               architecture_source_context=first_request.get("architecture_source_context"),
                                               human_v1_option=first_request.get("human_v1_option"), **continuation)
        except (BrainProviderProtocolError, AttributeError, KeyError, TypeError, ValueError):
            raise BrainAttemptError("FRESH_PACKET_INVALID", "FRESH_PACKET", count) from None
        if final_request["request_fingerprint"] != first_request["request_fingerprint"]:
            code = {"MOA_CONSTRUCT": "STALE_BEFORE_MOA_RETURN", "TCA_CONSTRUCT": "STALE_BEFORE_TCA_RETURN",
                    "AOA_CONSTRUCT": "STALE_BEFORE_AOA_RETURN",
                    "V1_OPTION_CONSTRUCT": "STALE_BEFORE_V1_OPTION_RETURN"}.get(mode, "STALE_BEFORE_AUDIT_RETURN")
            raise BrainAttemptError(code, "FRESH_PACKET", count)
    return AttemptResult(second, (attribution1, attribution2), advisory)


def consult_advisory(repo: str | Path, *, flow: str, packet: DecisionPacket, subject: dict[str, Any],
                     candidate: dict[str, Any], expected_candidate_fingerprint: str,
                     fresh_packet_supplier: Callable[[], DecisionPacket], cut=None,
                     enabled: bool | None = None, human_opt_out: bool = False,
                     provider: MappingBrainProvider | JsonBrainProvider | None = None,
                     profile_supplier: Callable | None = None, reserve_call: Callable | None = None):
    """Default CAA-03 caller, also invoked automatically by ordinary attempt().

    The existing lawful Brain transport is reused through CAA-02/03 unchanged.
    Legacy enabled/profile/reserve arguments are accepted for compatibility only
    and impose no eligibility or provider gates. No pilot receipt is fabricated.
    """
    from .caa_integration import PilotConsultation, consult_flow

    advisory = consult_flow(repo, flow=flow, packet=packet, subject=subject, candidate=candidate,
        expected_candidate_fingerprint=expected_candidate_fingerprint, fresh_packet_supplier=fresh_packet_supplier,
        cut=cut, enabled=enabled, human_opt_out=human_opt_out, provider=provider)
    return PilotConsultation(advisory)


__all__ = ["BrainProvider", "ProviderInvocation", "AttemptResult", "BrainAttemptError",
           "ProviderTransportError", "ProviderResponseError", "ProviderAttributionMismatch",
           "MappingBrainProvider", "JsonBrainProvider", "attempt", "consult_advisory"]
