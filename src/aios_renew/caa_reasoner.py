"""CAA-02: transient, source-bound consultation, with no authoring authority.

Models select and compare evidence-backed mechanism hypotheses in a closed
semantic grammar. Source facts, transfer, effects and causes remain distinct.
There is no workflow hook, audit invocation, provider selection or persistence.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
import json
import time
from typing import Any

from .brain_provider import JsonBrainProvider, MappingBrainProvider, ProviderInvocation
from .caa_experience import (
    ExperienceCut, ExperienceError, _digest, _encode, _semantic_features, _shape, reconstruct_episodes,
)


MAX_CANDIDATE_BYTES = 32768
MAX_REQUEST_BYTES = 131072
MAX_RESPONSE_BYTES = 16384
MAX_OUTPUT_BYTES = 98304
MAX_SUGGESTIONS = 3
PROVIDER_SECONDS = 10.0
# One outstanding selected transport, including a timed-out transport. A caller
# cannot accumulate abandoned calls. Transport code must itself enforce its I/O
# deadline; Python cannot cancel an arbitrary supplied callable safely.
_PROVIDER_SLOT = BoundedSemaphore(1)
_OUTCOMES = {"ADVICE", "NO_MATERIAL_ADVICE", "INSUFFICIENT_GROUNDED_EVIDENCE", "UNAVAILABLE"}
_CANDIDATE_FIELDS = {"goal", "invariants", "acceptance", "scope", "consumers", "verification"}

# These are hypotheses/experiments, not hard-coded diagnoses. A model can choose
# a cross-module analogy through related features even without an exact term
# match. Independent checks below constrain what its citations actually support.
_MECHANISMS = {
    "IDENTITY_BEFORE_USE": dict(
        concepts={"SOURCE_IDENTITY", "DATA_AUTHORITY", "PRIVACY"},
        hypothesis="Checking identity before consuming data may prevent data from acquiring privileges.",
        experiment="Compare legitimate and substituted subjects at the same consumer boundary.",
        effect="The substituted subject would be rejected while the legitimate subject remains usable.",
        falsifier="A substituted subject is accepted, or legitimate access fails under the same conditions."),
    "SNAPSHOT_BOUND_READ": dict(
        concepts={"TEMPORAL_ISOLATION", "SOURCE_IDENTITY"},
        hypothesis="Binding reads to one immutable cut may prevent newer information from changing an earlier decision.",
        experiment="Repeat the same cut before and after adding a later source event.",
        effect="The earlier view would remain identical or explicitly fail authentication.",
        falsifier="The earlier view silently acquires a later fact."),
    "BOUNDED_DEGRADATION": dict(
        concepts={"BOUNDED_WORK", "FAILURE_CONTAINMENT"},
        hypothesis="A finite failure boundary may preserve useful work when an optional dependency is unavailable.",
        experiment="Make the optional dependency unavailable at its declared resource limit.",
        effect="The caller would receive a bounded abstention and retain its existing lawful path.",
        falsifier="Work blocks indefinitely, retries outside the budget, or bypasses a genuine authentication failure."),
    "VALIDATE_CONSUMER_INPUT": dict(
        concepts={"SCHEMA_VALIDATION", "SOURCE_IDENTITY", "CONSUMER_COMPATIBILITY"},
        hypothesis="Validation at the consuming boundary may contain well-formed but incorrectly attributed input.",
        experiment="Pair malformed and well-formed wrong-subject inputs with a valid control.",
        effect="Only the valid, correctly attributed control would reach the consumer.",
        falsifier="A wrong-subject or malformed input reaches the consumer, or the control is rejected."),
    "ACCEPTANCE_ACCOUNTING": dict(
        concepts={"ACCEPTANCE_COVERAGE", "VERIFICATION_ISOLATION"},
        hypothesis="Separating acceptance support from command success may expose completion gaps.",
        experiment="Compare a passing command with and without support for one required acceptance criterion.",
        effect="The missing support would remain explicit despite command success.",
        falsifier="A passing command is treated as support for an uncovered criterion."),
    "CONSUMER_COUNTEREXAMPLE": dict(
        concepts={"CONSUMER_COMPATIBILITY", "VERIFICATION_ISOLATION"},
        hypothesis="A consumer-specific negative case may reveal a shared-interface mismatch hidden by a producer success.",
        experiment="Exercise the affected consumer with an incompatible input and a compatible control.",
        effect="The incompatible case would be detected at the actual affected consumer.",
        falsifier="The incompatible case passes, or the compatible control fails for an unrelated reason."),
    "EXACT_CORRECTION_LINEAGE": dict(
        concepts={"CORRECTION_LINEAGE", "SOURCE_IDENTITY"},
        hypothesis="An exact predecessor binding may prevent a correction from borrowing a different failure.",
        experiment="Substitute the predecessor while keeping the apparent correction subject unchanged.",
        effect="The substituted predecessor would be rejected independently of the correction's apparent success.",
        falsifier="The correction is accepted against the substituted predecessor."),
    "HISTORICAL_POLICY_IS_DATA": dict(
        concepts={"DIRECT_AUTHORING", "TEMPORAL_ISOLATION", "DATA_AUTHORITY"},
        hypothesis="Keeping historical policy separate from current authorization may prevent stale precedents becoming mandates.",
        experiment="Present an older policy example alongside the current exact authorization.",
        effect="The older example would remain advisory data and could not select a new gate or action.",
        falsifier="The older example changes current authorization, default authoring, or task selection."),
}


class CandidateContextError(ValueError):
    """The exact caller-owned draft was substituted or exceeds its contract."""


class _InvalidAdvice(ValueError):
    pass


def _check(condition):
    if not condition:
        raise _InvalidAdvice("INVALID_GROUNDED_RESPONSE")


def _candidate(candidate):
    try:
        if type(candidate) is not dict or set(candidate) != _CANDIDATE_FIELDS:
            raise ValueError
        texts = []
        if type(candidate["goal"]) is not str:
            raise ValueError
        texts.append(candidate["goal"])
        for key in ("invariants", "acceptance", "consumers", "verification"):
            values = candidate[key]
            if type(values) is not list or len(values) > 32:
                raise ValueError
            texts.extend(values)
        scope = candidate["scope"]
        if type(scope) is not dict or set(scope) != {"inspect", "modify"}:
            raise ValueError
        for values in scope.values():
            if type(values) is not list or len(values) > 32:
                raise ValueError
            texts.extend(values)
        if any(type(t) is not str or not t or len(t) > 4096 for t in texts):
            raise ValueError
        if len(_encode(candidate).encode("ascii")) > MAX_CANDIDATE_BYTES:
            raise ValueError
        return deepcopy(candidate)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise CandidateContextError("CANDIDATE_INVALID_OR_OVER_BUDGET") from None


def candidate_fingerprint(candidate: dict[str, Any]) -> str:
    """Bind the exact transient draft; this fingerprint grants no authority."""
    return _digest(_candidate(candidate))


def _candidate_features(candidate, fingerprint):
    fields = [("goal", "/goal", candidate["goal"])]
    for key, category in (("invariants", "invariant"), ("acceptance", "acceptance"),
                          ("consumers", "consumers"), ("verification", "verification")):
        fields += [(category, f"/{key}/{i}", text) for i, text in enumerate(candidate[key])]
    for key in ("inspect", "modify"):
        fields += [("scope", f"/scope/{key}/{i}", text) for i, text in enumerate(candidate["scope"][key])]
    return dict(_semantic_features(fields, dict(candidate_digest=fingerprint)),
                scope_digest=_digest(candidate["scope"]), authority="NONE")


def _history(view, candidate_features):
    catalog, history = {}, []
    candidate_concepts = {s["concept"] for s in candidate_features["signals"]}
    for index, episode in enumerate(view["episodes"]):
        episode_id = f"E{index + 1}"
        semantic = episode["semantic_context"]
        features, outcomes = [], []

        def cite(kind, value, sources):
            item = dict(episode_id=episode_id, kind=kind, value=value, sources=sources,
                        cut_digest=view["source_cut"]["digest"], as_of_sha=view["source_cut"]["as_of_sha"],
                        status="OBSERVED")
            citation = "C-" + _digest(item)
            catalog[citation] = dict(id=citation, **item)
            return citation

        for signal in semantic["signals"]:
            features.append(cite("SEMANTIC_MENTION", dict(concept=signal["concept"],
                category=signal["category"], meaning="LEXICAL_MENTION_ONLY", polarity="UNKNOWN"),
                [signal["source"]]))
        outcome = "UNKNOWN"
        for fact in episode["observed"]:
            if fact["name"] == "terminal":
                outcome = fact["value"]["kind"]
            if fact["name"] in {"terminal", "failure_shape", "verification", "result", "review"}:
                outcomes.append(cite(fact["name"], fact["value"], fact["sources"]))
        concepts = {s["concept"] for s in semantic["signals"]}
        shared = sorted(concepts & candidate_concepts)
        related = [key for key, m in _MECHANISMS.items()
                   if concepts & m["concepts"] and candidate_concepts & m["concepts"]]
        relation = ("NOT_APPLICABLE" if not related else "OUTCOME_UNKNOWN" if outcome == "UNKNOWN"
                    else "POSITIVE_OUTCOME_ANALOGY" if outcome == "RESULT" else "NEGATIVE_OUTCOME_ANALOGY")
        history.append(dict(episode_id=episode_id, selection=episode["selection"],
            concepts=sorted(concepts), features=features, outcome=outcome, outcome_citations=outcomes,
            relation=relation, shared_concepts=shared, related_mechanisms=related,
            lexical_score=len(shared), structured_score=int(outcome != "UNKNOWN"),
            scope_digest=semantic["scope_digest"]))
    # Keep all bounded episodes, including negative and disanalogous cases.
    history.sort(key=lambda h: (-h["lexical_score"], -h["structured_score"], h["episode_id"]))
    return catalog, history


def _relevant(history, catalog, mechanism):
    concepts = _MECHANISMS[mechanism]["concepts"]
    return {h["episode_id"]: [c for c in h["features"] if catalog[c]["value"]["concept"] in concepts]
            for h in history if mechanism in h["related_mechanisms"] and h["outcome"] != "UNKNOWN"}


def _counterexamples(history, relevant, support_episodes):
    supported = [h for h in history if h["episode_id"] in support_episodes]
    roles = {h["outcome"] for h in supported}
    candidates = [h for h in history if h["episode_id"] in relevant
                  and h["episode_id"] not in support_episodes
                  and (h["outcome"] not in roles or not h["shared_concepts"])]
    # One strongest witness per observed outcome; a failed requirement mention
    # refutes the inference that a mention establishes successful enforcement.
    chosen = {}
    for h in candidates:
        chosen.setdefault(h["outcome"], relevant[h["episode_id"]][0])
    return list(chosen.values())


def _baseline(request):
    suggestions, signatures = [], set()
    catalog, history = request["citations"], request["history"]
    for mechanism in _MECHANISMS:
        relevant = _relevant(history, catalog, mechanism)
        for h in history:
            if h["episode_id"] not in relevant or not h["shared_concepts"]:
                continue
            # A1 uses exact feature overlap, unlike B1's family-level analogy.
            citations = [c for c in relevant[h["episode_id"]]
                         if catalog[c]["value"]["concept"] in h["shared_concepts"]]
            if not citations:
                continue
            signature = catalog[citations[0]]["value"]["concept"]
            if signature in signatures:
                continue
            signatures.add(signature)
            suggestions.append(dict(mechanism=mechanism, support=citations[:1],
                counterexamples=_counterexamples(history, relevant, {h["episode_id"]}),
                applicability="POSSIBLE", status="INFERRED", caution="NO_CAUSE_ESTABLISHED"))
            break
        if len(suggestions) == MAX_SUGGESTIONS:
            break
    return dict(request_fingerprint=request["request_fingerprint"], candidate_digest=request["candidate_digest"],
                cut_digest=request["source_cut"]["digest"], outcome="ADVICE" if suggestions else "NO_MATERIAL_ADVICE",
                suggestions=suggestions)


def _validate(response, request):
    _shape(response, max_depth=8)
    _check(len(_encode(response).encode("ascii")) <= MAX_RESPONSE_BYTES)
    _check(type(response) is dict and set(response) == {
        "request_fingerprint", "candidate_digest", "cut_digest", "outcome", "suggestions"})
    _check(response["request_fingerprint"] == request["request_fingerprint"]
           and response["candidate_digest"] == request["candidate_digest"]
           and response["cut_digest"] == request["source_cut"]["digest"])
    _check(response["outcome"] in _OUTCOMES)
    suggestions = response["suggestions"]
    _check(type(suggestions) is list and len(suggestions) <= MAX_SUGGESTIONS)
    _check(bool(suggestions) == (response["outcome"] == "ADVICE"))
    catalog, history = request["citations"], request["history"]
    candidate_concepts = {s["concept"] for s in request["candidate_features"]["signals"]}
    used_mechanisms, used_support = set(), set()
    rendered = []
    for suggestion in suggestions:
        _check(type(suggestion) is dict and set(suggestion) == {
            "mechanism", "support", "counterexamples", "applicability", "status", "caution"})
        mechanism = suggestion["mechanism"]
        _check(type(mechanism) is str and mechanism in _MECHANISMS and mechanism not in used_mechanisms)
        used_mechanisms.add(mechanism)
        spec = _MECHANISMS[mechanism]
        _check(suggestion["status"] == "INFERRED" and suggestion["caution"] == "NO_CAUSE_ESTABLISHED")
        _check(suggestion["applicability"] in {"POSSIBLE", "LIMITED"})
        support, counter = suggestion["support"], suggestion["counterexamples"]
        _check(type(support) is list and 1 <= len(support) <= 3
               and type(counter) is list and len(counter) <= 2)
        _check(all(type(c) is str and c in catalog for c in support + counter))
        _check(len(set(support + counter)) == len(support + counter))
        relevant = _relevant(history, catalog, mechanism)
        allowed = {c for cs in relevant.values() for c in cs}
        # A syntactically valid citation to a terminal, unrelated concept or
        # different subject cannot support the proposed mechanism.
        _check(set(support + counter) <= allowed and bool(candidate_concepts & spec["concepts"]))
        episodes = {catalog[c]["episode_id"] for c in support}
        _check(not episodes & {catalog[c]["episode_id"] for c in counter})
        required = _counterexamples(history, relevant, episodes)
        required_roles = {next(h["outcome"] for h in history if h["episode_id"] == catalog[c]["episode_id"])
                          for c in required}
        counter_roles = {h["outcome"] for h in history if h["episode_id"] in
                         {catalog[c]["episode_id"] for c in counter}}
        _check(required_roles <= counter_roles)
        source_concepts = {catalog[c]["value"]["concept"] for c in support}
        signature = (tuple(sorted(source_concepts)), tuple(sorted(candidate_concepts & spec["concepts"])))
        _check(signature not in used_support)
        used_support.add(signature)
        shared = source_concepts & candidate_concepts
        _check(bool(shared) or suggestion["applicability"] == "LIMITED")
        supporting_history = [h for h in history if h["episode_id"] in episodes]
        differences = []
        if any(h["scope_digest"] != request["candidate_features"]["scope_digest"] for h in supporting_history):
            differences.append("DIFFERENT_SCOPED_CONSUMERS")
        if not shared:
            differences.append("RELATED_FEATURES_WITHOUT_EXACT_SHARED_CONCEPT")
        differences.append("REQUIREMENT_MENTION_DOES_NOT_ESTABLISH_IMPLEMENTATION_OR_CAUSE")
        citations = list(support + counter)
        for h in history:
            if h["episode_id"] in episodes | {catalog[c]["episode_id"] for c in counter}:
                citations.extend(h["outcome_citations"])
        rendered.append(dict(mechanism=mechanism, status="INFERRED",
            evidence_support=dict(status="OBSERVED", meaning="MENTIONS_AND_OUTCOMES_ONLY",
                citations=[catalog[c] for c in dict.fromkeys(citations)]),
            shared_mechanism=dict(status="INFERRED", hypothesis=spec["hypothesis"],
                source_concepts=sorted(source_concepts), candidate_concepts=sorted(candidate_concepts & spec["concepts"])),
            applicability=dict(status="INFERRED", transfer=suggestion["applicability"],
                decisive_differences=differences),
            counterexamples=dict(status="OBSERVED" if counter else "UNKNOWN", citations=counter,
                interpretation="Opposing outcomes or different features limit transfer; neither proves a cause."),
            experiment=spec["experiment"], falsifier=spec["falsifier"],
            prospective_effect=dict(status="INFERRED", hypothesis=spec["effect"], measured_effect="UNKNOWN"),
            uncertainty=dict(technical_root_cause="UNKNOWN", actual_transfer="UNKNOWN", efficacy="UNKNOWN",
                reason="Lexical overlap, a test identity, or success after correction is not causal proof.")))
    return response["outcome"], rendered


def _invoke(provider, request):
    """One call, bounded wait, no retry, reroute, tool or authority channel."""
    if type(provider) not in (MappingBrainProvider, JsonBrainProvider):
        return None, "PROVIDER_UNAVAILABLE", 0
    configured = (provider.provider, provider.model)
    if not _PROVIDER_SLOT.acquire(blocking=False):
        return None, "PROVIDER_BUSY", 0
    queue = Queue(maxsize=1)

    def run():
        try:
            value = provider.invoke(deepcopy(request))
            if (provider.provider, provider.model) != configured or not isinstance(value, ProviderInvocation):
                raise ValueError
            if (value.attribution["provider"], value.attribution["model"]) != configured:
                raise ValueError
            queue.put((deepcopy(dict(value.semantic_response)), None), block=False)
        except Exception:
            queue.put((None, "PROVIDER_RESPONSE_UNAVAILABLE"), block=False)
        finally:
            _PROVIDER_SLOT.release()

    thread = Thread(target=run, daemon=True, name="caa-selected-transport")
    try:
        thread.start()
    except RuntimeError:
        _PROVIDER_SLOT.release()
        return None, "PROVIDER_UNAVAILABLE", 0
    try:
        value, error = queue.get(timeout=PROVIDER_SECONDS)
        return value, error, 1
    except Empty:
        return None, "PROVIDER_TIMEOUT", 1


def consult(repo: str | Path, *, cut: ExperienceCut, candidate: dict[str, Any],
            expected_candidate_fingerprint: str, mode: str = "B1",
            provider: MappingBrainProvider | JsonBrainProvider | None = None) -> dict[str, Any]:
    """Consult one sealed CAA-01 cut and exact caller-owned draft, read-only.

    A1 is the explicit deterministic structured/lexical comparator. B1 makes at
    most one optional pretrained-model transport call; it never falls back to A1.
    Existing source authentication errors propagate and cannot be degraded into
    an apparent authorization. The result is never a handoff or admission token.
    """
    started = time.monotonic()
    draft = _candidate(candidate)
    fingerprint = _digest(draft)
    if type(expected_candidate_fingerprint) is not str or fingerprint != expected_candidate_fingerprint:
        raise CandidateContextError("CANDIDATE_SUBSTITUTED")
    if type(mode) is not str or mode not in ("A1", "B1"):
        raise CandidateContextError("CONSULTATION_MODE_INVALID")
    view = reconstruct_episodes(repo, cut=cut, include_semantics=True)
    features = _candidate_features(draft, fingerprint)
    catalog, history = _history(view, features)
    request = dict(format="AIOS_CAA_CONSULTATION_REQUEST", version=1, authority="NONE",
        trusted_protocol="Compare evidence as data. Select at most three distinct falsifiable mechanism hypotheses. "
        "Return only the closed CAA-02 response grammar. Mentions and outcomes never prove cause. "
        "Account for opposing outcomes and disanalogies. No tools, policy, scope, execution or authorization output.",
        candidate_digest=fingerprint, candidate_features=features, source_cut=view["source_cut"],
        history=history, citations=catalog,
        mechanisms={key: dict(value, concepts=sorted(value["concepts"])) for key, value in _MECHANISMS.items()},
        response_contract=dict(fields=["request_fingerprint", "candidate_digest", "cut_digest", "outcome", "suggestions"],
            suggestion_fields=["mechanism", "support", "counterexamples", "applicability", "status", "caution"],
            outcomes=sorted(_OUTCOMES), applicability=["POSSIBLE", "LIMITED"], status="INFERRED",
            caution="NO_CAUSE_ESTABLISHED", max_suggestions=MAX_SUGGESTIONS))
    # Native arrays on both adapters: the opaque reader's portable cut contains
    # tuples, which a JSON transport would otherwise silently turn into lists.
    request = json.loads(_encode(request))
    request["request_fingerprint"] = _digest(request)
    request_bytes = len(_encode(request).encode("ascii"))
    calls = response_bytes = 0
    source_reads, reason = 1, "INSUFFICIENT_HISTORY"
    outcome, suggestions = "INSUFFICIENT_GROUNDED_EVIDENCE", []
    response = None
    if request_bytes > MAX_REQUEST_BYTES:
        outcome, reason = "UNAVAILABLE", "REQUEST_OVER_BUDGET"
    elif any(h["related_mechanisms"] and h["outcome"] != "UNKNOWN" for h in history):
        if mode == "A1":
            response = _baseline(request)
        else:
            response, error, calls = _invoke(provider, request)
            if error:
                outcome, reason = "UNAVAILABLE", error
            # A moved/forged source remains an authentication error even when the
            # optional model failed. Re-read exactly the same cut, never enrich it.
            if calls:
                fresh = reconstruct_episodes(repo, cut=cut, include_semantics=True)
                source_reads += 1
                if fresh != view:
                    raise CandidateContextError("CONSULTATION_SOURCE_CHANGED")
        if response is not None:
            try:
                response_bytes = len(_encode(response).encode("ascii"))
                outcome, suggestions = _validate(response, request)
                reason = "GROUNDED_HYPOTHESES" if suggestions else "MODEL_OR_BASELINE_ABSTENTION"
            except Exception:
                outcome, reason, suggestions = "INSUFFICIENT_GROUNDED_EVIDENCE", "INVALID_GROUNDED_RESPONSE", []
    if candidate_fingerprint(candidate) != fingerprint:
        raise CandidateContextError("CANDIDATE_SUBSTITUTED")
    result = dict(format="AIOS_CAA_ADVISORY", version=1, authority="NONE", mode=mode,
        candidate_digest=fingerprint, source_cut=request["source_cut"], request_fingerprint=request["request_fingerprint"],
        outcome=outcome, reason=reason, suggestions=suggestions, retrieved=history,
        source_text_is_instructions=False, normative_policy="UNKNOWN", technical_root_cause="UNKNOWN",
        run_created=False, executor_invoked=False, verification_invoked=False, state_mutated=False,
        metrics=dict(provider_calls=calls, source_reads=source_reads, request_bytes=request_bytes,
            response_bytes=response_bytes, elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            tokens="UNKNOWN", monetary_cost="UNKNOWN", live_provider_quality="UNKNOWN", production_uplift="UNKNOWN"))
    try:
        _shape(result, max_depth=16)
        over_budget = len(_encode(result).encode("ascii")) > MAX_OUTPUT_BYTES
    except (ExperienceError, ValueError, TypeError, RecursionError):
        over_budget = True
    if over_budget:
        result.update(outcome="UNAVAILABLE", reason="OUTPUT_OVER_BUDGET", suggestions=[], retrieved=[])
    return result
