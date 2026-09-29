"""Pure, content-addressed RA-4 two-pass research protocol.

Callers supply all acquisition and Brain-authored material. An acquisition pair
has exactly ``request`` and ``attempt`` fields. No source access or state is
performed here. Constructors take fingerprint-free artifact material (omitting
the derived acquisition summaries); validators take complete artifacts.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .research_contract import (
    ResearchContractError, validate_research_brief, validate_challenge_target,
    validate_acquisition_request,
)
from .source_acquisition import validate_acquisition_attempt
from .research_record import (
    _LENSES, _fields, _literal, _choice, _sha, _json,
    _semantic, construct_research_claim, research_audit_profile_ref, validate_research_record,
)


_CONSTRUCT = {"format", "version", "kind", "research_brief", "audit_profile_ref",
              "baseline_acquisitions", "claims", "challenge_targets",
              "counter_evidence_requests", "construct_fingerprint"}
_RECONCILIATION = {"format", "version", "kind", "construct_fingerprint",
                   "counter_acquisitions", "audit_results", "claim_reconciliation",
                   "new_claim_fingerprints", "research_record", "outcome",
                   "reconciliation_fingerprint"}
_SUMMARY = {"request_fingerprint", "attempt_fingerprint", "observation_fingerprints"}


def _identity(body: dict[str, Any], field: str, ceiling: int) -> dict[str, Any]:
    digest = hashlib.sha256(_json(body, ceiling)).hexdigest()
    result = dict(body, **{field: digest})
    _json(result, ceiling)
    return result


def _summaries(value: Any, expected: list[dict[str, Any]]) -> None:
    if type(value) is not list or len(value) != len(expected):
        raise ResearchContractError("invalid acquisition summaries")
    normalized = []
    for raw in value:
        entry = _fields(raw, _SUMMARY, "acquisition summary")
        observations = entry["observation_fingerprints"]
        if type(observations) is not list:
            raise ResearchContractError("invalid acquisition observation identities")
        refs = [_sha(ref, "observation fingerprint") for ref in observations]
        if len(refs) != len(set(refs)):
            raise ResearchContractError("duplicate acquisition observation identity")
        normalized.append({"request_fingerprint": _sha(entry["request_fingerprint"], "request fingerprint"),
                           "attempt_fingerprint": _sha(entry["attempt_fingerprint"], "attempt fingerprint"),
                           "observation_fingerprints": sorted(refs)})
    if sorted(normalized, key=lambda entry: entry["request_fingerprint"]) != expected:
        raise ResearchContractError("acquisition identities do not match exact attempts")


def _acquisitions(pairs: Any, brief: dict[str, Any], expected: dict[str, Any] | None,
                  phase: str, targets: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    ceiling = brief["resource_bounds"]["max_baseline_requests"] if phase == "BASELINE" else len(expected)
    if type(pairs) is not list or not 1 <= len(pairs) <= ceiling:
        raise ResearchContractError("acquisition batch is missing or over-bound")
    summaries, seen, observations = [], set(), set()
    for pair in pairs:
        item = _fields(pair, {"request", "attempt"}, "acquisition pair")
        raw_request = item["request"]
        if type(raw_request) is not dict or type(raw_request.get("challenge_target_fingerprints")) is not list:
            raise ResearchContractError("invalid acquisition request")
        refs = raw_request["challenge_target_fingerprints"]
        if any(type(ref) is not str for ref in refs) or len(refs) != len(set(refs)) or any(ref not in targets for ref in refs):
            raise ResearchContractError("foreign acquisition target")
        bound_targets = [targets[ref] for ref in refs]
        request = validate_acquisition_request(raw_request, brief, bound_targets)
        _literal(request["phase"], phase, "acquisition phase")
        request_id = request["request_fingerprint"]
        if request_id in seen or (expected is not None and
                                  (request_id not in expected or request != expected[request_id])):
            raise ResearchContractError("duplicate, foreign or substituted acquisition request")
        seen.add(request_id)
        attempt = validate_acquisition_attempt(item["attempt"], brief, request, bound_targets)
        _literal(attempt["outcome"], "SUCCEEDED", "acquisition outcome")
        ids = [observation["observation_fingerprint"] for observation in attempt["observations"]]
        observations.update(ids)
        summaries.append({"request_fingerprint": request_id,
                          "attempt_fingerprint": attempt["attempt_fingerprint"],
                          "observation_fingerprints": sorted(ids)})
    if expected is not None and seen != set(expected):
        raise ResearchContractError("acquisition batch does not exactly cover requests")
    return sorted(summaries, key=lambda entry: entry["request_fingerprint"]), observations


def _construct(material: Any, profile: Any, baseline_pairs: Any, validate: bool) -> dict[str, Any]:
    fields = _CONSTRUCT if validate else _CONSTRUCT - {"construct_fingerprint", "baseline_acquisitions"}
    item = _fields(material, fields, "Evidence Construct")
    _json(item, 524288)
    for name, value in (("format", "AIOS_RESEARCH_EVIDENCE_CONSTRUCT"),
                        ("version", 1), ("kind", "RESEARCH_EVIDENCE_CONSTRUCT")):
        _literal(item[name], value, name)
    brief = validate_research_brief(item["research_brief"])
    profile_ref = research_audit_profile_ref(profile)
    _literal(item["audit_profile_ref"], profile_ref, "audit profile ref")
    baseline, source_ids = _acquisitions(baseline_pairs, brief, None, "BASELINE", {})
    source_list = sorted(source_ids)
    raw_claims = item["claims"]
    if type(raw_claims) is not list or not 1 <= len(raw_claims) <= 64:
        raise ResearchContractError("invalid Pass-1 claim set")
    claims = []
    for raw in raw_claims:
        if type(raw) is not dict or "claim_fingerprint" not in raw:
            raise ResearchContractError("Pass-1 claim requires exact fingerprint")
        claim = construct_research_claim({k: v for k, v in raw.items() if k != "claim_fingerprint"}, source_list)
        _literal(raw, claim, "Pass-1 claim")
        claims.append(claim)
    claim_by_id = {claim["claim_fingerprint"]: claim for claim in claims}
    if len(claim_by_id) != len(claims):
        raise ResearchContractError("duplicate Pass-1 claim")
    raw_targets = item["challenge_targets"]
    if type(raw_targets) is not list or not 1 <= len(raw_targets) <= 64:
        raise ResearchContractError("invalid challenge target set")
    targets = [validate_challenge_target(target, brief) for target in raw_targets]
    target_by_id = {target["target_fingerprint"]: target for target in targets}
    if len(target_by_id) != len(targets):
        raise ResearchContractError("duplicate challenge target")
    for target in targets:
        kind, ref = target["target_kind"], target["target_ref"]
        if kind in {"CLAIM", "ASSUMPTION"}:
            if ref not in claim_by_id or (kind == "ASSUMPTION" and
                                          claim_by_id[ref]["claim_kind"] != "ASSUMPTION"):
                raise ResearchContractError("ungrounded claim challenge")
        elif kind in {"SOURCE", "FRESHNESS", "INSTRUCTION_BOUNDARY"} and ref not in source_ids:
            raise ResearchContractError("ungrounded source challenge")
    raw_requests = item["counter_evidence_requests"]
    if type(raw_requests) is not list or not 1 <= len(raw_requests) <= brief["resource_bounds"]["max_counter_evidence_requests"]:
        raise ResearchContractError("invalid counter-evidence request set")
    requests, covered = [], set()
    for raw in raw_requests:
        if type(raw) is not dict or type(raw.get("challenge_target_fingerprints")) is not list:
            raise ResearchContractError("invalid counter-evidence request")
        refs = raw["challenge_target_fingerprints"]
        if any(type(ref) is not str or ref not in target_by_id for ref in refs):
            raise ResearchContractError("foreign counter-evidence target")
        request = validate_acquisition_request(raw, brief, [target_by_id[ref] for ref in refs])
        _literal(request["phase"], "COUNTER_EVIDENCE", "counter request phase")
        covered.update(refs)
        requests.append(request)
    request_ids = [request["request_fingerprint"] for request in requests]
    if len(request_ids) != len(set(request_ids)) or covered != set(target_by_id):
        raise ResearchContractError("counter requests duplicate or omit targets")
    if len(source_ids) + sum(request["bounds"]["max_items"] for request in requests) > 64:
        raise ResearchContractError("planned observation corpus exceeds 64")
    body = {"format": "AIOS_RESEARCH_EVIDENCE_CONSTRUCT", "version": 1,
            "kind": "RESEARCH_EVIDENCE_CONSTRUCT", "research_brief": brief,
            "audit_profile_ref": profile_ref, "baseline_acquisitions": baseline,
            "claims": sorted(claims, key=lambda claim: claim["claim_fingerprint"]),
            "challenge_targets": sorted(targets, key=lambda target: target["target_fingerprint"]),
            "counter_evidence_requests": sorted(requests, key=lambda request: request["request_fingerprint"])}
    result = _identity(body, "construct_fingerprint", 524288)
    if validate:
        _summaries(item["baseline_acquisitions"], result["baseline_acquisitions"])
        _literal(item["construct_fingerprint"], result["construct_fingerprint"], "construct fingerprint")
        # Lists are sets semantically; their original order need not match normalization.
        for name, key in (("claims", "claim_fingerprint"),
                          ("challenge_targets", "target_fingerprint"),
                          ("counter_evidence_requests", "request_fingerprint")):
            if sorted(item[name], key=lambda entry: entry[key]) != result[name]:
                raise ResearchContractError(f"invalid {name}")
    return result


def construct_research_evidence_construct(material: Any, profile: Any,
                                          baseline_acquisitions: Any) -> dict[str, Any]:
    """Freeze Pass 1 from exact successful baseline request/attempt pairs."""
    return _construct(material, profile, baseline_acquisitions, False)


def validate_research_evidence_construct(material: Any, profile: Any,
                                         baseline_acquisitions: Any) -> dict[str, Any]:
    """Revalidate Pass 1 against the same exact baseline acquisition material."""
    return _construct(material, profile, baseline_acquisitions, True)


def _refs(value: Any, name: str, allowed: set[str], ceiling: int) -> list[str]:
    if type(value) is not list or len(value) > ceiling:
        raise ResearchContractError(f"invalid {name}")
    refs = [_sha(ref, name) for ref in value]
    if len(refs) != len(set(refs)) or not set(refs) <= allowed:
        raise ResearchContractError(f"duplicate or foreign {name}")
    return sorted(refs)


def _reconciliation(material: Any, construct: Any, profile: Any, baseline_pairs: Any,
                    counter_pairs: Any, predecessor_record: Any, validate: bool) -> dict[str, Any]:
    fields = _RECONCILIATION if validate else _RECONCILIATION - {"counter_acquisitions", "reconciliation_fingerprint"}
    item = _fields(material, fields, "Research Reconciliation")
    _json(item, 1048576)
    for name, value in (("format", "AIOS_RESEARCH_RECONCILIATION"),
                        ("version", 1), ("kind", "RESEARCH_RECONCILIATION")):
        _literal(item[name], value, name)
    frozen = validate_research_evidence_construct(construct, profile, baseline_pairs)
    _literal(item["construct_fingerprint"], frozen["construct_fingerprint"], "construct fingerprint")
    requests = {request["request_fingerprint"]: request for request in frozen["counter_evidence_requests"]}
    targets = {target["target_fingerprint"]: target for target in frozen["challenge_targets"]}
    counters, counter_ids = _acquisitions(counter_pairs, frozen["research_brief"], requests,
                                          "COUNTER_EVIDENCE", targets)
    source_ids = {ref for summary in frozen["baseline_acquisitions"]
                  for ref in summary["observation_fingerprints"]} | counter_ids
    if len(source_ids) > 64:
        raise ResearchContractError("actual observation corpus exceeds 64")
    record = validate_research_record(item["research_record"], profile, predecessor_record)
    _literal(record["research_brief"], frozen["research_brief"], "final Brief")
    _literal(record["audit_profile_ref"], frozen["audit_profile_ref"], "final profile")
    if not {source["observation_fingerprint"] for source in record["sources"]} <= source_ids:
        raise ResearchContractError("final record references unadmitted source")
    pass1 = {claim["claim_fingerprint"] for claim in frozen["claims"]}
    final = {claim["claim_fingerprint"] for claim in record["claims"]}
    raw_audits = item["audit_results"]
    if type(raw_audits) is not list or len(raw_audits) != len(_LENSES):
        raise ResearchContractError("exactly nine audit results required")
    audits, challenged = [], set()
    for expected, raw in zip(_LENSES, raw_audits):
        audit = _fields(raw, {"lens_id", "disposition", "summary", "claim_fingerprints",
                              "observation_fingerprints", "challenge_target_fingerprints"}, "audit result")
        _literal(audit["lens_id"], expected, "audit lens order")
        refs = _refs(audit["challenge_target_fingerprints"], "audit targets", set(targets), 64)
        challenged.update(refs)
        audits.append({"lens_id": expected,
                       "disposition": _choice(audit["disposition"], {"CLEAR", "LIMITATION", "BLOCKING"}, "audit disposition"),
                       "summary": _semantic(audit["summary"], "audit summary", 8192),
                       "claim_fingerprints": _refs(audit["claim_fingerprints"], "audit claims", pass1 | final, 64),
                       "observation_fingerprints": _refs(audit["observation_fingerprints"], "audit observations", source_ids, 64),
                       "challenge_target_fingerprints": refs})
    if challenged != set(targets):
        raise ResearchContractError("audit omits challenge target")
    raw_mappings = item["claim_reconciliation"]
    if type(raw_mappings) is not list or len(raw_mappings) != len(pass1):
        raise ResearchContractError("incomplete claim reconciliation")
    mappings, consumed, prior = [], set(), set()
    for raw in raw_mappings:
        entry = _fields(raw, {"pass1_claim_fingerprint", "disposition", "final_claim_fingerprint"}, "claim reconciliation")
        old = _sha(entry["pass1_claim_fingerprint"], "Pass-1 claim")
        disposition = _choice(entry["disposition"], {"RETAINED", "REVISED", "REMOVED"}, "claim disposition")
        new = entry["final_claim_fingerprint"]
        if old not in pass1 or old in prior:
            raise ResearchContractError("duplicate or foreign Pass-1 claim")
        prior.add(old)
        if disposition == "REMOVED":
            if new is not None or old in final:
                raise ResearchContractError("removed claim remains")
        else:
            new = _sha(new, "final claim")
            if new not in final or new in consumed or (disposition == "RETAINED") != (old == new):
                raise ResearchContractError("ambiguous claim reconciliation")
            consumed.add(new)
        mappings.append({"pass1_claim_fingerprint": old, "disposition": disposition,
                         "final_claim_fingerprint": new})
    if prior != pass1:
        raise ResearchContractError("Pass-1 claim omitted")
    novel = _refs(item["new_claim_fingerprints"], "new claims", final, 64)
    if set(novel) != final - consumed:
        raise ResearchContractError("final claims not exactly reconciled")
    outcome = _choice(item["outcome"], {"RESEARCH_CANDIDATE", "INSUFFICIENT_EVIDENCE"}, "outcome")
    _literal(outcome, record["closure"]["outcome"], "record closure outcome")
    blocked = any(audit["disposition"] == "BLOCKING" for audit in audits)
    if (outcome == "INSUFFICIENT_EVIDENCE") != blocked:
        raise ResearchContractError("audit blocker and outcome disagree")
    body = {"format": "AIOS_RESEARCH_RECONCILIATION", "version": 1,
            "kind": "RESEARCH_RECONCILIATION", "construct_fingerprint": frozen["construct_fingerprint"],
            "counter_acquisitions": counters, "audit_results": audits,
            "claim_reconciliation": sorted(mappings, key=lambda entry: entry["pass1_claim_fingerprint"]),
            "new_claim_fingerprints": novel, "research_record": record, "outcome": outcome}
    result = _identity(body, "reconciliation_fingerprint", 1048576)
    if validate:
        _literal(item["reconciliation_fingerprint"], result["reconciliation_fingerprint"], "reconciliation fingerprint")
        _summaries(item["counter_acquisitions"], result["counter_acquisitions"])
        for name, key in (("claim_reconciliation", "pass1_claim_fingerprint"),):
            if type(item[name]) is not list or sorted(item[name], key=lambda entry: entry[key]) != result[name]:
                raise ResearchContractError(f"invalid {name}")
        for name in ("audit_results", "new_claim_fingerprints", "research_record"):
            if name == "new_claim_fingerprints":
                if sorted(item[name]) != result[name]:
                    raise ResearchContractError("invalid new claims")
            elif name == "audit_results":
                for raw, normalized in zip(item[name], result[name]):
                    if (raw["lens_id"] != normalized["lens_id"] or
                        any(sorted(raw[refs]) != normalized[refs] for refs in
                            ("claim_fingerprints", "observation_fingerprints", "challenge_target_fingerprints")) or
                        any(raw[field] != normalized[field] for field in ("disposition", "summary"))):
                        raise ResearchContractError("invalid audit results")
            elif item[name] != result[name]:
                raise ResearchContractError(f"invalid {name}")
    return result


def construct_research_reconciliation(material: Any, construct: Any, profile: Any,
                                      baseline_acquisitions: Any, counter_acquisitions: Any,
                                      predecessor_record: Any = None) -> dict[str, Any]:
    """Close Pass 2 over one exact successful counter batch and Brain audit."""
    return _reconciliation(material, construct, profile, baseline_acquisitions,
                           counter_acquisitions, predecessor_record, False)


def validate_research_reconciliation(material: Any, construct: Any, profile: Any,
                                     baseline_acquisitions: Any, counter_acquisitions: Any,
                                     predecessor_record: Any = None) -> dict[str, Any]:
    """Revalidate the complete immutable two-pass closure."""
    return _reconciliation(material, construct, profile, baseline_acquisitions,
                           counter_acquisitions, predecessor_record, True)


__all__ = ["construct_research_evidence_construct", "validate_research_evidence_construct",
           "construct_research_reconciliation", "validate_research_reconciliation"]
