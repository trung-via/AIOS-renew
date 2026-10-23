"""CAA-03: explicit, optional advice for one already resolved Brain subject.

The existing DecisionPacket compiler and caller's canonical readers own subject
authority. CAA-01 owns history authentication; CAA-02 alone validates advice.
Nothing here admits a family, selects a flow/provider/action, or writes state.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .brain_context import BrainWorkContext, FlowResolution
from .brain_provider import JsonBrainProvider, MappingBrainProvider
from .caa_experience import ExperienceCut, _digest, _encode
from .caa_reasoner import candidate_fingerprint, consult
from .decision_packet import compile_decision_packet


FLOWS = frozenset({"ARCHITECTURE", "TASK_AUTHORING", "REPAIR_AUTHORING", "REMEDIATION_AUTHORING"})
MAX_REQUEST_BYTES = 147456
MAX_RESPONSE_BYTES = 18432
MAX_OUTPUT_BYTES = 114688
MAX_BINDING_BYTES = 8192
MAX_MATERIAL_BYTES = 2097152
_OUTCOMES = frozenset({"ADVICE", "NO_MATERIAL_ADVICE", "INSUFFICIENT_GROUNDED_EVIDENCE", "UNAVAILABLE"})
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}(?:/[A-Za-z0-9_.-]{1,100})?\Z")


class IntegrationContextError(ValueError):
    """An original subject/currentness binding failed; never an audit verdict."""


def _require(condition, code):
    if not condition:
        raise IntegrationContextError(code)


def _strict(value, ceiling):
    """Closed native JSON, finite traversal, and an exact serialized byte bound."""
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        _require(count <= 32768 and depth <= 24, "INTEGRATION_OVER_BUDGET")
        if type(item) is dict:
            _require(len(item) <= 32768 and all(type(k) is str for k in item), "INTEGRATION_JSON_INVALID")
            pending.extend((v, depth + 1) for pair in item.items() for v in pair)
        elif type(item) is list:
            _require(len(item) <= 32768, "INTEGRATION_OVER_BUDGET")
            pending.extend((v, depth + 1) for v in item)
        elif type(item) is float:
            _require(math.isfinite(item), "INTEGRATION_JSON_INVALID")
        else:
            _require(item is None or type(item) in (str, int, bool), "INTEGRATION_JSON_INVALID")
    try:
        _require(len(_encode(value).encode("ascii")) <= ceiling, "INTEGRATION_OVER_BUDGET")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise IntegrationContextError("INTEGRATION_JSON_INVALID_OR_OVER_BUDGET") from None


def _subject(body, material, selected):
    """Project identities only; never export finding prose or FAILURE diagnostics."""
    flow, target = body["selected_flow"], body["subject"]
    _require(type(selected) is dict, "EXACT_SUBJECT_REQUIRED")
    if flow in {"ARCHITECTURE", "TASK_AUTHORING"}:
        _require(selected == target == {"kind": flow}, "SUBJECT_SUBSTITUTED")
        return deepcopy(selected)
    task = body["canonical_facts"]["selected_task"]
    if flow == "REPAIR_AUTHORING":
        expected = {k: target[k] for k in ("failed_run_id", "failed_head_sha")}
        expected["executor"] = material["failed_run"]["executor"]
        _require(selected == expected, "FAILED_SUBJECT_SUBSTITUTED")
        result = dict(expected, task=task, failure_digest=_digest(material["failure"]))
    else:
        fields = ("source_run_id", "review_id", "finding_id", "reviewed_sha", "action")
        _require(set(selected) == set(fields), "EXACT_FINDING_REQUIRED")
        findings = target["findings"] if target["kind"] == "CORRECTION_FRONTIER" else [target]
        matches = [f for f in findings if {k: f[k] for k in fields} == selected]
        _require(len(matches) == 1, "OUTSTANDING_FINDING_SUBSTITUTED")
        result = dict(selected, task=task, basis=matches[0]["basis"],
                      finding_contract_digest=_digest(matches[0]),
                      required_review_verdict="CHANGES_REQUIRED")
    # Canonical family validators ran first. These additional transport checks
    # exclude arbitrary identity strings from this deliberately smaller context.
    for key, value in result.items():
        if key == "task":
            _require(type(value) is dict and set(value) == {"id", "revision"}
                     and type(value["revision"]) is int and value["revision"] > 0
                     and type(value["id"]) is str and _IDENTITY.fullmatch(value["id"]), "SUBJECT_INVALID")
        else:
            _require(type(value) is str and _IDENTITY.fullmatch(value), "SUBJECT_INVALID")
    return deepcopy(result)


def _snapshot(repo, context, resolution, material, selected):
    _require(type(context) is BrainWorkContext and type(resolution) is FlowResolution,
             "CALLER_RESOLVED_CONTEXT_REQUIRED")
    _require(resolution.selected_flow in FLOWS, "UNSUPPORTED_FLOW")
    # Invokes existing source guards, including opaque Post-TASK/diagnostic seals.
    # The compiler revalidates the supplied resolution; CAA never chooses one.
    context.as_dict()
    _require(Path(context.operational_repository_root).resolve() == Path(repo).resolve(), "REPOSITORY_SUBSTITUTED")
    _strict(material, MAX_MATERIAL_BYTES)
    packet = compile_decision_packet(context, resolution, material)
    body = packet.as_dict()
    facts = body["canonical_facts"]
    _require(body["authority_owner"] == "BRAIN", "BRAIN_SUBJECT_REQUIRED")
    blocker = body["canonical_blocker"]
    _require(blocker is None or (body["selected_flow"] == "TASK_AUTHORING"
             and blocker.get("code") == "UNAUTHORED_TASK"), "ORIGINAL_CANONICAL_AUTHORITY_BLOCKED")
    _require(type(facts["repository"]) is str and _REPOSITORY.fullmatch(facts["repository"])
             and type(facts["main_sha"]) is str and _SHA.fullmatch(facts["main_sha"]), "CANONICAL_SOURCE_REQUIRED")
    return dict(flow=body["selected_flow"], packet_fingerprint=packet.packet_fingerprint,
                work_context_fingerprint=body["work_context_fingerprint"],
                source=dict(repository=facts["repository"], main_sha=facts["main_sha"]),
                subject=_subject(body, material, selected), material_digest=_digest(material))


def _bridge(provider, binding):
    """Transport envelope only. CAA-02 independently validates its entire return."""
    configured = (provider.provider, provider.model)

    def invoke(inner):
        if type(provider) is JsonBrainProvider:
            inner = json.loads(inner)
        request = dict(format="AIOS_CAA_INTEGRATION_REQUEST", version=1, authority="NONE",
                       binding=deepcopy(binding), binding_fingerprint=_digest(binding),
                       consultation=inner,
                       response_contract=dict(fields=["request_fingerprint", "binding_fingerprint", "consultation_response"],
                           consultation_response="EXACT_CAA02_RESPONSE", max_response_bytes=MAX_RESPONSE_BYTES))
        request["request_fingerprint"] = _digest(request)
        _strict(request, MAX_REQUEST_BYTES)
        invocation = provider.invoke(request)
        _require((provider.provider, provider.model) == configured, "PROVIDER_SUBSTITUTED")
        response = dict(invocation.semantic_response)
        _strict(response, MAX_RESPONSE_BYTES)
        _require(set(response) == {"request_fingerprint", "binding_fingerprint", "consultation_response"}
                 and response["request_fingerprint"] == request["request_fingerprint"]
                 and response["binding_fingerprint"] == request["binding_fingerprint"]
                 and type(response["consultation_response"]) is dict, "INTEGRATION_RESPONSE_SUBSTITUTED")
        wrapper = dict(semantic_response=response["consultation_response"], attribution=dict(invocation.attribution))
        return _encode(wrapper).encode("ascii") if type(provider) is JsonBrainProvider else wrapper

    # Preserve both existing independent native extraction/attribution paths.
    return type(provider)(*configured, invoke)


def _result(binding, outcome, reason, advisory=None):
    result = dict(format="AIOS_CAA_FLOW_ADVISORY", version=1, authority="NONE", binding=binding,
                  binding_fingerprint=_digest(binding), outcome=outcome, reason=reason,
                  advisory=advisory, source_validity="CAA01_REAUTHENTICATED" if advisory else "NOT_CONSULTED",
                  source_text_is_instructions=False, normative_policy="UNKNOWN", technical_root_cause="UNKNOWN",
                  final_semantic_judgment_owner="BRAIN", direct_authoring="EXISTING_CONTRACT_ONLY",
                  run_created=False, executor_invoked=False, verification_invoked=False, state_mutated=False)
    try:
        _strict(result, MAX_OUTPUT_BYTES)
    except IntegrationContextError:
        result.update(outcome="UNAVAILABLE", reason="OUTPUT_OVER_BUDGET", advisory=None)
    return result


def consult_flow(
    repo: str | Path, *, context: BrainWorkContext, resolution: FlowResolution,
    material: Mapping[str, Any], subject: dict[str, Any], candidate: dict[str, Any],
    expected_packet_fingerprint: str, expected_candidate_fingerprint: str,
    cut: ExperienceCut | None = None, enabled: bool = False, human_opt_out: bool = False,
    provider: MappingBrainProvider | JsonBrainProvider | None = None,
    fresh_inputs: Callable[[], tuple[BrainWorkContext, FlowResolution, Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Return optional advice for exactly one caller-resolved subject and draft.

    ``material`` is the existing compiler's canonical material, never model text.
    ``fresh_inputs`` rereads it through the caller's existing canonical readers;
    it must not cache an old observation. Neither a packet nor a digest alone
    authenticates Git. A missing optional reader/history/provider yields no advice.
    Original source-guard errors propagate independently, even on opt-out or a
    failed transport. Final family construction/revalidation stays with Brain.
    """
    _require(type(enabled) is bool and type(human_opt_out) is bool, "EXPLICIT_CONSULTATION_CHOICE_REQUIRED")
    original = _snapshot(repo, context, resolution, material, subject)
    _require(type(expected_packet_fingerprint) is str
             and original["packet_fingerprint"] == expected_packet_fingerprint, "PACKET_SUBSTITUTED")
    fingerprint = candidate_fingerprint(candidate)
    _require(type(expected_candidate_fingerprint) is str and fingerprint == expected_candidate_fingerprint,
             "CANDIDATE_SUBSTITUTED")
    binding = dict(original, candidate_digest=fingerprint, source_cut=None)
    if not enabled or human_opt_out:
        return _result(binding, "NO_MATERIAL_ADVICE", "HUMAN_OPT_OUT" if human_opt_out else "CONSULTATION_DISABLED")
    if cut is None:
        return _result(binding, "INSUFFICIENT_GROUNDED_EVIDENCE", "OPTIONAL_HISTORY_ABSENT")
    _require(type(cut) is ExperienceCut, "SEALED_CUT_REQUIRED")
    portable_cut = json.loads(_encode(cut.as_dict()))
    _require(portable_cut["main_sha"] == original["source"]["main_sha"], "SOURCE_MAIN_SUBSTITUTED")
    # Its sealed digest binds every pin. Do not duplicate the potentially large
    # ref manifest in the outer binding (CAA-02 already carries it verbatim).
    binding["source_cut"] = {k: v for k, v in portable_cut.items() if k != "refs"}
    _strict(binding, MAX_BINDING_BYTES)
    if type(provider) not in (MappingBrainProvider, JsonBrainProvider):
        return _result(binding, "UNAVAILABLE", "OPTIONAL_PROVIDER_ABSENT")
    if not callable(fresh_inputs):
        return _result(binding, "UNAVAILABLE", "OPTIONAL_CURRENTNESS_READER_ABSENT")

    def current():
        fresh = fresh_inputs()
        _require(type(fresh) is tuple and len(fresh) == 3, "FRESH_CANONICAL_INPUTS_REQUIRED")
        _require(_snapshot(repo, *fresh, subject) == original, "ORIGINAL_CANONICAL_SUBJECT_CHANGED")
        _require(_snapshot(repo, context, resolution, material, subject) == original, "ORIGINAL_INPUTS_SUBSTITUTED")
        _require(candidate_fingerprint(candidate) == fingerprint, "CANDIDATE_SUBSTITUTED")
        _require(json.loads(_encode(cut.as_dict())) == portable_cut, "SOURCE_CUT_SUBSTITUTED")

    current()
    try:
        advisory = consult(repo, cut=cut, candidate=candidate, expected_candidate_fingerprint=fingerprint,
                           mode="B1", provider=_bridge(provider, binding))
    finally:
        # No transport failure, malformed response or timeout bypasses the
        # independently owned original subject/currentness checks.
        current()
    _require(advisory["outcome"] in _OUTCOMES, "CAA02_OUTCOME_INVALID")
    return _result(binding, advisory["outcome"], advisory["reason"], advisory)


__all__ = ["consult_flow", "IntegrationContextError"]
