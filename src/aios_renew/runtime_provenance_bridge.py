"""VP-03D v1 read-only terminal provenance and closed Runtime issuer bridge.

Content authentication, independent producer authentication and execution
authority are distinct. No public API can enroll an issuer. The existing
Runtime boundary currently reports independent issuance unavailable. Offline
content inspection is deliberately a different schema and cannot feed the
canonical handoff. No terminal fact is a live preterminal checkpoint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from . import proof_applicability as proof
from .artifacts import validate_evidence, validate_result
from .proof_authority_handoff import InactiveEffects, _plain_input
from .review import parse_review, validate_review
from .review_transport import _bind_performance_terminal_identity, task_run_prefix
from .return_affinity import document_affinity
from .task import _TaskLoader, parse_task


SCHEMA = "runtime-canonical-provenance-v1"
CONTENT_SCHEMA = "runtime-terminal-provenance-content-v1"
REQUEST_SCHEMA = "runtime-terminal-selection-v1"
ISSUER_UNAVAILABLE = "INDEPENDENT_CANONICAL_RUNTIME_ISSUER_UNAVAILABLE"
MAX_EVIDENCE = 32
RecordIdentity = proof.RecordIdentity


class ProvenanceInputError(ValueError):
    """Unsupported, non-plain, malformed or unbounded selection."""


@dataclass(frozen=True)
class EvidenceSelection:
    evidence_id: str
    evidence_digest: str
    raw: RecordIdentity


@dataclass(frozen=True)
class TerminalSelection:
    schema: str
    task: RecordIdentity
    task_id: str
    task_revision: int
    run: RecordIdentity
    run_id: str
    base_sha: str
    candidate_sha: str
    tree_sha: str
    result: RecordIdentity
    reviewed_source: RecordIdentity
    review: RecordIdentity
    evidence: tuple[EvidenceSelection, ...]


@dataclass(frozen=True)
class EvidenceFact:
    selection: EvidenceSelection
    subject_sha: str
    command: str
    exit_code: int
    outcome: str
    # Exact existing bytes, never new target execution EVIDENCE.
    evidence_bytes: bytes
    raw_bytes: bytes


@dataclass(frozen=True)
class TerminalFacts:
    selection: TerminalSelection
    task_bytes: bytes
    run_bytes: bytes
    result_bytes: bytes
    review_bytes: bytes
    evidence: tuple[EvidenceFact, ...]
    review_id: str
    review_verdict: str
    review_acceptance: tuple[tuple[str, str], ...]
    recorded_run_status: str
    terminal_kind: str = "RESULT"
    source_phase: str = "TERMINAL"


@dataclass(frozen=True)
class PublicationCurrentness:
    """Separately observed administrative refs; never CAS or upstream attestation."""

    expected_main_sha: str
    expected_refs: tuple[tuple[str, str], ...]
    before: tuple[tuple[str, str | None], ...]
    after: tuple[tuple[str, str | None], ...]
    state: str
    publication_state: str


@dataclass(frozen=True)
class ProvenanceObservation(InactiveEffects):
    schema: str
    selection_digest: str
    status: str
    reasons: tuple[str, ...]
    content_state: str = "UNKNOWN"
    source_authentication: str = "UNAVAILABLE"
    terminal: TerminalFacts | None = None
    currentness: PublicationCurrentness | None = None
    # A terminal RESULT and the persisted ACTIVE RUN encoding grant no C1/C2.
    preterminal_state: str = "UNKNOWN"
    lease_state: str = "UNKNOWN"
    cas_state: str = "UNKNOWN"


def decode_selection(value: object) -> TerminalSelection:
    """Exact factual selection; no repository, issuer, currentness or rights."""
    try:
        _plain_input(value)
        proof.canonical_digest(value)
        data = proof._object(value, set(TerminalSelection.__dataclass_fields__))
        if data["schema"] != REQUEST_SCHEMA:
            raise ProvenanceInputError("unsupported terminal selection version")
        revision = data["task_revision"]
        if type(revision) is not int or not 1 <= revision <= 2_147_483_647:
            raise ProvenanceInputError("positive bounded TASK revision required")
        evidence = []
        for item in proof._list(data["evidence"], MAX_EVIDENCE):
            item = proof._object(item, set(EvidenceSelection.__dataclass_fields__))
            evidence.append(EvidenceSelection(proof._match(item["evidence_id"], proof._ID),
                proof._match(item["evidence_digest"], proof._DIGEST),
                proof.decode_record_identity(item["raw"])))
        if not evidence:
            raise ProvenanceInputError("terminal EVIDENCE population required")
        return TerminalSelection(REQUEST_SCHEMA, proof.decode_record_identity(data["task"]),
            proof._match(data["task_id"], proof._ID), revision, proof.decode_record_identity(data["run"]),
            proof._match(data["run_id"], proof._ID), proof._match(data["base_sha"], proof._SHA),
            proof._match(data["candidate_sha"], proof._SHA), proof._match(data["tree_sha"], proof._SHA),
            *(proof.decode_record_identity(data[k]) for k in ("result", "reviewed_source", "review")),
            tuple(evidence))
    except ValueError as exc:
        raise ProvenanceInputError(str(exc)) from None


def _values(value, depth=0, nodes=None):
    # Inspect types before conversion; never invoke caller serialization hooks.
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if depth > proof.MAX_DEPTH or nodes[0] > proof.MAX_NODES:
        raise ProvenanceInputError("typed terminal selection exceeds bounds")
    if any(type(value) is cls for cls in (TerminalSelection, EvidenceSelection, RecordIdentity)):
        return {k: _values(getattr(value, k), depth + 1, nodes) for k in value.__dataclass_fields__}
    if type(value) is tuple and len(value) <= MAX_EVIDENCE:
        return [_values(v, depth + 1, nodes) for v in value]
    if type(value) is str or type(value) is int:
        return value
    raise ProvenanceInputError("exact decoded terminal selection types required")


def _checked(selection):
    if (type(selection) is not TerminalSelection or type(selection.evidence) is not tuple
            or len(selection.evidence) > MAX_EVIDENCE
            or any(type(v) is not EvidenceSelection for v in selection.evidence)):
        raise ProvenanceInputError("decode_selection must precede observation")
    values = _values(selection)
    return decode_selection(values), proof.canonical_digest(values)


def _runtime_observation(digest):
    # Only Runtime's existing boundary is consulted. A returned object, exact
    # class name, Git root, or Runtime-looking constructor never creates rights.
    from .runtime import CanonicalProvenanceAvailability, observe_canonical_provenance_availability

    availability = observe_canonical_provenance_availability()
    if (type(availability) is not CanonicalProvenanceAvailability
            or any(type(getattr(availability, k)) is not str for k in (
                "schema", "terminal_source", "runtime_issuer", "reviewer_issuer", "preterminal_source", "activation"))
            or type(availability.reasons) is not tuple
            or any(type(v) is not str for v in availability.reasons)
            or availability != CanonicalProvenanceAvailability()):
        return ProvenanceObservation(SCHEMA, digest, "BLOCK",
            ("UNTRUSTED_RUNTIME_SOURCE_OBSERVATION", ISSUER_UNAVAILABLE, "NOT_ACTIVATED"))
    return ProvenanceObservation(SCHEMA, digest, "UNKNOWN", (*availability.reasons, "NOT_ACTIVATED"))


def observe_terminal_provenance(selection: TerminalSelection) -> ProvenanceObservation:
    """Canonical consumer: no source/authority/instance/callback arguments.

    Existing transport snapshots lack independent producer authentication and
    complete raw issuance. Source reads are consequently unavailable, rather
    than selecting a Git root and authenticating it against itself.
    """
    selection, digest = _checked(selection)
    result = _runtime_observation(digest)
    if (len({v.evidence_id for v in selection.evidence}) != len(selection.evidence)
            or len({v.raw for v in selection.evidence}) != len(selection.evidence)):
        return replace(result, status="BLOCK", reasons=("DUPLICATE_EVIDENCE_SOURCE", *result.reasons))
    return result


def observe_handoff_provenance(request) -> ProvenanceObservation:
    """Runtime-only acquisition for VP-03C; fixture observations are not inputs."""
    from .proof_authority_handoff import _checked as checked_handoff

    _, digest = checked_handoff(request)
    return _runtime_observation(digest)


def _closed_runtime_observation(value, digest):
    """Content/constructor lookalikes cannot substitute for the closed source."""
    return (type(value) is ProvenanceObservation
        and all(type(getattr(value, k)) is str for k in ("schema", "selection_digest", "status", "source_authentication",
                                                       "content_state", "preterminal_state", "lease_state", "cas_state"))
        and value.schema == SCHEMA
        and value.selection_digest == digest and value.status in ("UNKNOWN", "BLOCK")
        and value.source_authentication == "UNAVAILABLE" and value.content_state == "UNKNOWN"
        and value.terminal is None and value.currentness is None
        and value.preterminal_state == value.lease_state == value.cas_state == "UNKNOWN"
        and type(value.reasons) is tuple and all(type(v) is str for v in value.reasons)
        and ISSUER_UNAVAILABLE in value.reasons
        and all(type(getattr(value, k)) is type(getattr(InactiveEffects(), k))
                and getattr(value, k) == getattr(InactiveEffects(), k) for k in InactiveEffects.__dataclass_fields__))


class _Conflict(Exception):
    pass


def _require(condition, reason):
    if not condition:
        raise _Conflict(reason)


def _refs(git, expected):
    from .proof_authority_handoff import _ref
    return tuple((name, _ref(git, name)) for name, _ in expected)


def _terminal_content(git, selection):
    """Immutable byte/binding validation, NOT independent producer attestation."""
    task_bytes = git.record(selection.task, decoded=False)
    task = parse_task(task_bytes.decode("utf-8"))
    _require((task.task_id, task.revision) == (selection.task_id, selection.task_revision), "TASK_CONFLICT")
    _require(selection.task.path == f".ai/tasks/{task.task_id}.yaml", "TASK_PATH_CONFLICT")
    _require(selection.run.path == ".ai/transport/run.json"
             and selection.result.path == ".ai/transport/result.json", "TRANSPORT_PATH_CONFLICT")
    _require(selection.run.commit_sha == selection.result.commit_sha, "TORN_TERMINAL_SOURCE")
    _require(selection.run_id.startswith(task_run_prefix(selection.task_id)), "RUN_NAMESPACE_CONFLICT")
    run_bytes = git.record(selection.run, decoded=False)
    result_bytes = git.record(selection.result, decoded=False)
    revision, _, base, candidate = _bind_performance_terminal_identity(run_bytes, result_bytes,
        ref=f"refs/heads/aios/artifacts/{selection.run_id}", run_id=selection.run_id,
        task_id=selection.task_id, terminal_kind="RESULT")
    _require((revision, base, candidate) == (selection.task_revision, selection.base_sha, selection.candidate_sha),
             "RUN_BASE_CANDIDATE_CONFLICT")
    tree, _ = git.commit(candidate)
    _require(tree == selection.tree_sha, "CANDIDATE_TREE_CONFLICT")
    git.ancestor(selection.task.commit_sha, base)
    admitted_task = git.entry(base, selection.task.path)
    _require(admitted_task is not None and admitted_task[1] == selection.task.blob_sha, "ADMITTED_TASK_BLOB_CONFLICT")
    git.ancestor(base, candidate)
    # Existing transport uses isolated artifact/decision commits. They need
    # exact independently issued bindings, not invented candidate ancestry.
    # REVIEW's reviewed_sha binds the candidate; the independently supplied
    # reviewed-source pin must additionally name this exact terminal package.
    _require(selection.reviewed_source == selection.result, "REVIEWED_SOURCE_CONFLICT")
    git.record(selection.reviewed_source, decoded=False)
    review_bytes = git.record(selection.review, decoded=False)
    review_document = yaml.load(review_bytes.decode("utf-8"), Loader=_TaskLoader)
    _plain_input(review_document)
    proof.canonical_digest(review_document)
    review = parse_review(review_bytes.decode("utf-8"))
    _require(selection.review.path in (f".ai/reviews/{review.review_id}.yaml",
                                      f".ai/reviews/{review.review_id}.yml"), "REVIEW_PATH_CONFLICT")
    package = git.record(selection.result)
    result = validate_result(package["result"])
    validate_review(task=task, result=result, review=review)
    raw_evidence = proof._list(package["evidence"], MAX_EVIDENCE)
    evidence = tuple(validate_evidence(v) for v in raw_evidence)
    _require(len(evidence) == len(selection.evidence), "EVIDENCE_POPULATION_CONFLICT")
    selected = {v.evidence_id: v for v in selection.evidence}
    _require(len(selected) == len(selection.evidence)
             and len({v.raw for v in selection.evidence}) == len(selection.evidence), "DUPLICATE_EVIDENCE_SOURCE")
    _require(set(selected) == {v.evidence_id for v in evidence}, "EVIDENCE_ID_CONFLICT")
    facts = []
    for item, decoded in zip(evidence, raw_evidence):
        wanted = selected[item.evidence_id]
        _require(proof.canonical_digest(decoded) == wanted.evidence_digest, "EVIDENCE_DIGEST_CONFLICT")
        _require(wanted.raw.path == item.raw_path, "RAW_PATH_CONFLICT")
        _require(wanted.raw.commit_sha == selection.result.commit_sha, "RAW_SOURCE_CONFLICT")
        raw_bytes = git.record(wanted.raw, decoded=False)
        facts.append(EvidenceFact(wanted, item.subject_sha, item.source.command, item.result.exit_code,
            "PASS" if item.result.exit_code == 0 else "FAIL",
            json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii"), raw_bytes))
    run_document = git.record(selection.run)
    recorded_run = run_document["execution"]["run"] if run_document.get("kind") == "REMEDIATION" else run_document
    _require(document_affinity(recorded_run) == task.return_affinity, "TASK_RUN_AFFINITY_CONFLICT")
    return TerminalFacts(selection, task_bytes, run_bytes, result_bytes, review_bytes,
        tuple(facts), review.review_id, review.verdict, tuple(review.acceptance.items()), recorded_run["status"])


def inspect_terminal_content(selection: TerminalSelection, *, fixture_repository: Path,
                             fixture_main_sha: str) -> ProvenanceObservation:
    """Offline diagnostic only, conditionally authenticated fixture bytes.

    The fixture root/main are NEVER consumed by the canonical entry point.
    Stable local refs attest neither their upstream nor Runtime/Reviewer rights.
    This decoder can inspect preexisting real-format terminal artifacts without
    inventing preterminal seals, lease, Human consent or checkpoint authority.
    """
    selection, digest = _checked(selection)
    result = ProvenanceObservation(CONTENT_SCHEMA, digest, "UNKNOWN",
        (ISSUER_UNAVAILABLE, "CONDITIONALLY_AUTHENTICATED_FIXTURE_CONTENT", "NOT_ACTIVATED"))
    if type(fixture_repository) is not type(Path()):
        return replace(result, reasons=("CONTENT_SOURCE_UNAVAILABLE", *result.reasons))
    try:
        main = proof._match(fixture_main_sha, proof._SHA)
    except ValueError as exc:
        raise ProvenanceInputError(str(exc)) from None
    expected = (("refs/heads/main", main),
        (f"refs/heads/aios/review/{selection.run_id}", selection.candidate_sha),
        (f"refs/heads/aios/artifacts/{selection.run_id}", selection.result.commit_sha),
        (f"refs/heads/aios/review-decision/{selection.run_id}", selection.review.commit_sha))
    git = proof._Git(fixture_repository)
    currentness = None
    try:
        currentness = PublicationCurrentness(main, expected, _refs(git, expected), (), "UNKNOWN", "UNKNOWN")
        git.commit(main)
        terminal = _terminal_content(git, selection)
        publication = "PUBLISHED"
        try:
            git.ancestor(selection.candidate_sha, main)
        except proof._Unknown as exc:
            if exc.reason.code is not proof.ReasonCode.LINEAGE_CONFLICT:
                raise
            publication = "UNPUBLISHED"
        result = replace(result, terminal=terminal, content_state="CONSISTENT",
                         source_authentication="CONDITIONAL_FIXTURE_CONTENT")
        currentness = replace(currentness, publication_state=publication)
    except _Conflict as exc:
        result = replace(result, status="BLOCK", content_state="CONFLICT", reasons=(str(exc), *result.reasons))
    except proof._Unknown as exc:
        result = replace(result, reasons=(exc.reason.code.value, *result.reasons))
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, yaml.YAMLError):
        result = replace(result, status="BLOCK", content_state="CONFLICT",
                         reasons=("TERMINAL_SOURCE_INCOMPLETE_OR_CONFLICTING", *result.reasons))
    if currentness is not None:
        after = _refs(git, expected)
        stable = currentness.before == after == expected
        currentness = replace(currentness, after=after, state="CONSISTENT" if stable else "UNKNOWN")
        result = replace(result, currentness=currentness)
        if not stable:
            result = replace(result, terminal=None, content_state="UNKNOWN",
                currentness=replace(currentness, publication_state="UNKNOWN"), status="UNKNOWN",
                reasons=("SEPARATE_MAIN_PUBLICATION_CURRENTNESS_CONFLICT", *result.reasons))
    return result
