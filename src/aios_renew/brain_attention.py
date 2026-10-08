"""Versioned selector-only attention projection. No semantic routing authority.

Source workflows export bounded observations, never a chosen attention family.
Immutable attempt artifacts are subordinate evidence, not RUN/FAILURE truth.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import MappingProxyType
from urllib.request import Request, urlopen
import zipfile

from .terminal_attention import _load_yaml

FORMAT = "AIOS_BRAIN_ATTENTION"
VERSION = 1
MAX_BYTES = 8192
MAX_SOURCE_BYTES = 65536
REPOSITORY = "trung-via/AIOS-renew"
RESULT = "RUN_RESULT_REQUIRING_SEMANTIC_REVIEW"
FAILURE = "RUN_FAILURE_REQUIRING_CORRECTION_REASONING"
INGRESS = "INGRESS_OR_CARRIER_REJECTION"
DISPATCH = "PRIMARY_REMEDIATION_REPAIR_DISPATCH_REJECTION"
PRE_AIOS = "PRE_AIOS_OPERATIONAL_FAILURE_REQUIRING_DIAGNOSIS"
REVIEW_INGRESS = "REVIEW_INGRESS_REJECTION"
REVIEW = "REVIEW_CHANGES_REQUIRED_OR_BLOCKED"
AUTHORING = "REMEDIATION_OR_REPAIR_AUTHORING_REJECTION"
PUBLICATION_FAILURE = "PUBLICATION_DISPATCH_OR_EXECUTION_FAILURE"
PUBLICATION_SUCCESS = "PUBLICATION_SUCCESS_REQUIRING_HUMAN_BRAIN_PLANNING"
CONFLICT = "CANONICAL_CONFLICT_OR_STALENESS"
RECOVERY = "WAKE_DELIVERY_RECOVERY_FOR_UNRESOLVED_ATTENTION"

# Every field is mandatory. No additional fields, nested values or free text.
_POINTER = dict(workflow_run_id="positive", run_attempt="positive",
                artifact_id="positive", source_digest="digest")
_ISSUE = dict(issue_id="positive", issue_number="positive", body_digest="digest",
              operation="ingress_operation", subject_id="subject", subject_sha="sha", finding_id="delivery")
_REVIEW = dict(run_id="run", artifact_sha="sha", decision_sha="sha", reviewed_sha="sha")
_DELIVERY = dict(operation="execution_operation", delivery_id="delivery", run_created="false",
                 subject_id="subject", subject_sha="sha", finding_id="delivery")
SCHEMAS = MappingProxyType({
    RESULT: dict(run_id="run", artifact_sha="sha"),
    FAILURE: dict(run_id="run", artifact_sha="sha"),
    INGRESS: {**_POINTER, **_ISSUE, "carrier": "carrier", "run_created": "false"},
    REVIEW_INGRESS: {**_POINTER, **_ISSUE, "operation": "review_operation", "run_created": "false"},
    AUTHORING: {**_POINTER, **_ISSUE, "operation": "correction_operation", "run_created": "false"},
    DISPATCH: {**_POINTER, **_DELIVERY},
    PRE_AIOS: {**_POINTER, **_DELIVERY},
    REVIEW: dict(_REVIEW),
    PUBLICATION_FAILURE: {**_POINTER, **_REVIEW, "stage": "publication_stage"},
    PUBLICATION_SUCCESS: {**_REVIEW, "published_sha": "sha"},
    CONFLICT: {**_POINTER, "prepared_sha": "sha", "prepared_digest": "digest", "predecessor_ref": "canonical_ref",
               "predecessor_sha": "sha", "observed_sha": "sha"},
    RECOVERY: dict(original_event_id="original"),
})
_SOURCE_BOUNDARIES = {
    INGRESS: "CARRIER_REJECTED", REVIEW_INGRESS: "CARRIER_REJECTED",
    AUTHORING: "CARRIER_REJECTED", DISPATCH: "DISPATCH_REJECTED", PRE_AIOS: "PRE_AIOS_FAILED",
    REVIEW: "REVIEW_FOLLOWUP", PUBLICATION_FAILURE: "PUBLICATION_FAILED",
    PUBLICATION_SUCCESS: "PUBLICATION_PROVEN", CONFLICT: "CANONICAL_CONFLICT",
}
for _family, _boundary in _SOURCE_BOUNDARIES.items():
    SCHEMAS[_family]["source_boundary"] = "literal:" + _boundary
SCHEMAS = MappingProxyType({name: MappingProxyType(schema) for name, schema in SCHEMAS.items()})
NON_WAKE = frozenset({"CARRIER_ADMITTED", "DISPATCH_ACCEPTED", "DISPATCH_REQUEST_ACCEPTED",
                     "RUNNER_STARTED", "AIOS_INVOKED", "ADMISSION_ACCEPTED", "RUN_ATTRIBUTED",
                     "TERMINAL_POINTER", "SELF_HOST_COMPLETED", "EXECUTOR_IN_PROGRESS",
                     "VERIFICATION_IN_PROGRESS", "AUTO_PUBLICATION_STARTED"})
_ENUMS = {
    "execution_operation": {"PRIMARY", "REMEDIATION", "REPAIR"},
    "carrier": {"INGRESS", "PRIMARY", "REMEDIATION", "REPAIR"},
    "ingress_operation": {"UNKNOWN", "AUTHOR_TASK", "SUBMIT_REVIEW", "AUTHOR_REMEDIATION", "AUTHOR_REPAIR"},
    "review_operation": {"SUBMIT_REVIEW"},
    "correction_operation": {"AUTHOR_REMEDIATION", "AUTHOR_REPAIR"},
    "publication_stage": {"DISPATCH", "EXECUTION"},
}
_PATTERNS = {
    "sha": r"[0-9a-f]{40}", "digest": r"[0-9a-f]{64}",
    "run": r"RUN-[A-Za-z0-9][A-Za-z0-9._-]{0,95}",
    "delivery": r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}",
    "subject": r"(?:NONE|(?:RUN|TASK)-[A-Za-z0-9][A-Za-z0-9._-]{0,95})",
    "canonical_ref": r"refs/heads/(?:main|aios/(?:review|failure|artifacts|failure-artifacts|review-decision|repair|remediation|task)/[A-Za-z0-9._-]{1,128})",
}


class AttentionError(ValueError):
    """Fixed, content-free rejection; never export source or browser exceptions."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load_json(raw, limit=MAX_BYTES):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AttentionError("DUPLICATE_KEY")
            result[key] = value
        return result
    try:
        raw = raw.encode("utf-8") if isinstance(raw, str) else raw
        if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
            raise AttentionError("INVALID_SIZE")
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise AttentionError("INVALID_MAPPING")
        return value
    except (UnicodeError, ValueError, TypeError) as exc:
        raise AttentionError("INVALID_JSON") from exc


def _value(value, kind):
    if kind.startswith("literal:"):
        valid = value == kind.removeprefix("literal:")
    elif kind == "positive":
        valid = type(value) is int and 1 <= value <= 2**53 - 1
    elif kind == "false":
        valid = value is False
    elif kind == "original":
        if not isinstance(value, str) or value.startswith("attention:v1:" + RECOVERY + ":"):
            raise AttentionError("RECURSIVE_RECOVERY")
        original = parse_event_id(value)
        valid = original.family != RECOVERY
    elif kind in _ENUMS:
        valid = isinstance(value, str) and value in _ENUMS[kind]
    else:
        valid = isinstance(value, str) and re.fullmatch(_PATTERNS[kind], value) is not None
    if not valid:
        raise AttentionError("INVALID_SELECTOR")


@dataclass(frozen=True)
class Attention:
    family: str
    selectors: object

    def as_dict(self):
        return dict(format=FORMAT, version=VERSION, family=self.family, selectors=dict(self.selectors))

    def render(self):
        return canonical(self.as_dict()).decode("ascii")

    @property
    def event_id(self):
        if self.family in {RESULT, FAILURE}:
            kind = "RESULT" if self.family == RESULT else "FAILURE"
            return f"terminal:{kind}:{self.selectors['run_id']}:{self.selectors['artifact_sha']}"
        encoded = base64.urlsafe_b64encode(canonical(dict(self.selectors))).decode("ascii").rstrip("=")
        return f"attention:v1:{self.family}:{encoded}"


def attention(family, selectors):
    if not isinstance(family, str) or family not in SCHEMAS or not isinstance(selectors, dict):
        raise AttentionError("UNKNOWN_FAMILY")
    schema = SCHEMAS[family]
    if set(selectors) != set(schema):
        raise AttentionError("SELECTOR_SCHEMA_MISMATCH")
    for name, kind in schema.items():
        _value(selectors[name], kind)
    if family == REVIEW_INGRESS and not selectors["subject_id"].startswith("RUN-"):
        raise AttentionError("SELECTOR_SUBSTITUTION")
    if family == REVIEW_INGRESS and selectors["finding_id"] != "NONE":
        raise AttentionError("SELECTOR_SUBSTITUTION")
    if family == AUTHORING and not selectors["subject_id"].startswith("RUN-"):
        raise AttentionError("SELECTOR_SUBSTITUTION")
    if family == AUTHORING and selectors["operation"] == "AUTHOR_REPAIR" and selectors["finding_id"] != "NONE":
        raise AttentionError("SELECTOR_SUBSTITUTION")
    if family == INGRESS:
        if (selectors["carrier"] == "INGRESS" and selectors["operation"] in {"SUBMIT_REVIEW", "AUTHOR_REMEDIATION", "AUTHOR_REPAIR"}
                or selectors["carrier"] != "INGRESS" and selectors["operation"] != "UNKNOWN"):
            raise AttentionError("SELECTOR_SUBSTITUTION")
        if selectors["operation"] == "UNKNOWN" and (selectors["subject_id"] != "NONE" or selectors["subject_sha"] != "0" * 40 or selectors["finding_id"] != "NONE"):
            raise AttentionError("SELECTOR_SUBSTITUTION")
        if selectors["operation"] == "AUTHOR_TASK" and (not selectors["subject_id"].startswith("TASK-") or selectors["finding_id"] != "NONE"):
            raise AttentionError("SELECTOR_SUBSTITUTION")
    if family in {DISPATCH, PRE_AIOS} and selectors["subject_id"] != "NONE":
        prefix = "TASK-" if selectors["operation"] == "PRIMARY" else "RUN-"
        if not selectors["subject_id"].startswith(prefix):
            raise AttentionError("SELECTOR_SUBSTITUTION")
    if family in {DISPATCH, PRE_AIOS} and selectors["operation"] != "REMEDIATION" and selectors["finding_id"] != "NONE":
        raise AttentionError("SELECTOR_SUBSTITUTION")
    if family == PUBLICATION_SUCCESS and selectors["published_sha"] != selectors["reviewed_sha"]:
        raise AttentionError("SELECTOR_SUBSTITUTION")
    item = Attention(family, MappingProxyType(dict(selectors)))
    if len(item.render().encode("ascii")) > MAX_BYTES or len(item.event_id) > MAX_BYTES:
        raise AttentionError("INVALID_SIZE")
    return item


def parse_body(raw):
    value = load_json(raw)
    if (set(value) != {"format", "version", "family", "selectors"}
            or value["format"] != FORMAT or type(value["version"]) is not int or value["version"] != VERSION):
        raise AttentionError("ENVELOPE_SCHEMA_MISMATCH")
    return attention(value["family"], value["selectors"])


def parse_event_id(event_id):
    if not isinstance(event_id, str) or not 0 < len(event_id) <= MAX_BYTES:
        raise AttentionError("INVALID_IDENTITY")
    parts = event_id.split(":")
    if len(parts) == 4 and parts[0] == "terminal" and parts[1] in {"RESULT", "FAILURE"}:
        item = attention(RESULT if parts[1] == "RESULT" else FAILURE,
                         dict(run_id=parts[2], artifact_sha=parts[3]))
    elif len(parts) == 4 and parts[:2] == ["attention", "v1"] and parts[2] not in {RESULT, FAILURE}:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", parts[3]):
            raise AttentionError("INVALID_IDENTITY")
        try:
            raw = base64.b64decode(parts[3] + "=" * (-len(parts[3]) % 4), altchars=b"-_", validate=True)
            item = attention(parts[2], load_json(raw))
        except (ValueError, TypeError) as exc:
            raise AttentionError("INVALID_IDENTITY") from exc
    else:
        raise AttentionError("INVALID_IDENTITY")
    if item.event_id != event_id:
        raise AttentionError("NONCANONICAL_IDENTITY")
    return item


def validate_registry(path):
    raw = Path(path).read_bytes()
    if not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise AttentionError("REGISTRY_SIZE")
    registry = _load_yaml(raw, "attention registry")
    expected = registry_document()
    if canonical(registry) != canonical(expected) or type(registry.get("version")) is not int:
        raise AttentionError("REGISTRY_MISMATCH")
    return registry


def registry_document():
    types = {name: dict(type="string", pattern=pattern) for name, pattern in _PATTERNS.items()}
    types.update({name: dict(type="string", enum=sorted(values)) for name, values in _ENUMS.items()})
    types.update(positive=dict(type="integer", minimum=1, maximum=2**53 - 1),
                 false=dict(type="literal", value=False),
                 original=dict(type="attention_identity", maximum_bytes=MAX_BYTES, excluded_family=RECOVERY))
    for boundary in sorted(set(_SOURCE_BOUNDARIES.values())):
        types["literal:" + boundary] = dict(type="literal", value=boundary)
    return dict(format="AIOS_BRAIN_ATTENTION_FAMILIES", version=VERSION,
                selector_types=types, families={name: dict(schema) for name, schema in SCHEMAS.items()},
                non_wake=sorted(NON_WAKE))


def _publication_attempt(attempt):
    """Closed, selector-only Publisher context; no procedure or semantic choice."""
    from .publication import PUBLICATION_CAUSES
    required = set(_REVIEW) | {"sampled_main_sha", "outcome", "cause", "recovery", "blocker"}
    if not isinstance(attempt, dict) or set(attempt) != required:
        raise AttentionError("PUBLICATION_CONTEXT_SCHEMA_MISMATCH")
    for name, kind in _REVIEW.items():
        _value(attempt[name], kind)
    _value(attempt["sampled_main_sha"], "sha")
    cause, outcome = attempt["cause"], attempt["outcome"]
    if not isinstance(cause, str) or cause not in PUBLICATION_CAUSES:
        raise AttentionError("UNKNOWN_PUBLICATION_CAUSE")
    recovery, blocker = attempt["recovery"], attempt["blocker"]
    if outcome == "RECOVERY_REVIEW_REQUIRED":
        if (cause != "FRESH_EXACT_REVIEW_REQUIRED" or blocker is not None
                or not isinstance(recovery, dict)
                or set(recovery) != {"merge_base_sha", "tree_sha", "publication_eligible"}
                or recovery["publication_eligible"] is not False):
            raise AttentionError("UNREVIEWED_RECOVERY_AUTHORITY")
        _value(recovery["merge_base_sha"], "sha")
        _value(recovery["tree_sha"], "sha")
    elif outcome == "RECOVERY_BLOCKED":
        if recovery is not None or cause not in {
                "AMBIGUOUS_ANCESTRY", "MERGE_CONFLICT", "MERGE_CALCULATION_FAILED",
                "RECOVERY_SCOPE_ESCAPE", "COMPETING_REVIEWED_SOURCE"}:
            raise AttentionError("PUBLICATION_CONTEXT_SCHEMA_MISMATCH")
        if cause == "COMPETING_REVIEWED_SOURCE":
            if not isinstance(blocker, dict) or set(blocker) != {"source_run", "decision_sha", "reviewed_sha"}:
                raise AttentionError("PUBLICATION_CONTEXT_SCHEMA_MISMATCH")
            _value(blocker["source_run"], "run")
            _value(blocker["decision_sha"], "sha")
            _value(blocker["reviewed_sha"], "sha")
        elif blocker is not None:
            raise AttentionError("PUBLICATION_CONTEXT_SCHEMA_MISMATCH")
    elif outcome == "FAILED":
        if recovery is not None or blocker is not None or cause in {
                "NONE", "FRESH_EXACT_REVIEW_REQUIRED", "COMPETING_REVIEWED_SOURCE"}:
            raise AttentionError("PUBLICATION_CONTEXT_SCHEMA_MISMATCH")
    else:
        raise AttentionError("NONACTIONABLE_PUBLICATION_CONTEXT")
    return attempt


def project_observation(observation, pointer):
    """The sole family classifier. Source status is a fact, never a flow choice."""
    if not isinstance(observation, dict) or not isinstance(observation.get("boundary"), str):
        raise AttentionError("INVALID_OBSERVATION")
    boundary = observation["boundary"]
    fields = {k: v for k, v in observation.items() if k != "boundary"}
    # Optional bounded source context is digest-bound in the existing conflict
    # selectors. It adds no family, routing decision, or free-text selector.
    if "publication_attempt" in fields:
        attempt = _publication_attempt(fields.pop("publication_attempt"))
        if boundary == "CANONICAL_CONFLICT":
            if attempt["outcome"] not in {"RECOVERY_BLOCKED", "RECOVERY_REVIEW_REQUIRED"}:
                raise AttentionError("NONACTIONABLE_PUBLICATION_CONTEXT")
            expected = dict(prepared_sha=attempt["reviewed_sha"], prepared_digest=digest(attempt),
                            predecessor_ref="refs/heads/main", predecessor_sha=attempt["sampled_main_sha"])
            if any(fields.get(key) != value for key, value in expected.items()):
                raise AttentionError("PUBLICATION_CONTEXT_SUBSTITUTION")
        elif boundary == "PUBLICATION_FAILED":
            if attempt["outcome"] != "FAILED" or any(fields.get(key) != attempt[key] for key in _REVIEW):
                raise AttentionError("PUBLICATION_CONTEXT_SUBSTITUTION")
        else:
            raise AttentionError("NONACTIONABLE_PUBLICATION_CONTEXT")
    if boundary in NON_WAKE:
        if fields:
            raise AttentionError("PROGRESS_SCHEMA_MISMATCH")
        return None
    if boundary == "CARRIER_REJECTED":
        if set(fields) != set(_ISSUE) | {"carrier", "run_created"}:
            raise AttentionError("INVALID_OBSERVATION")
        family = (REVIEW_INGRESS if fields["carrier"] == "INGRESS" and fields["operation"] == "SUBMIT_REVIEW"
                  else AUTHORING if fields["carrier"] == "INGRESS" and fields["operation"] in _ENUMS["correction_operation"]
                  else INGRESS)
        if family != INGRESS:
            fields.pop("carrier")
    elif boundary in {"DISPATCH_REJECTED", "PRE_AIOS_FAILED"}:
        family = DISPATCH if boundary == "DISPATCH_REJECTED" else PRE_AIOS
    elif boundary == "REVIEW_FOLLOWUP":
        family = REVIEW
    elif boundary == "PUBLICATION_FAILED":
        family = PUBLICATION_FAILURE
    elif boundary == "PUBLICATION_PROVEN":
        family = PUBLICATION_SUCCESS
    elif boundary == "CANONICAL_CONFLICT":
        family = CONFLICT
    else:
        raise AttentionError("UNKNOWN_BOUNDARY")
    expected_fields = set(SCHEMAS[family]) - set(_POINTER) - {"source_boundary"}
    if set(fields) != expected_fields:
        raise AttentionError("OBSERVATION_SCHEMA_MISMATCH")
    if family in {INGRESS, REVIEW_INGRESS, AUTHORING, DISPATCH, PRE_AIOS, PUBLICATION_FAILURE, CONFLICT}:
        fields = {**fields, **pointer}
    fields["source_boundary"] = boundary
    return attention(family, fields)


def source_document(observations):
    if not isinstance(observations, list) or len(observations) > 16:
        raise AttentionError("SOURCE_CAPACITY")
    return dict(format="AIOS_ATTENTION_SOURCE", version=1, observations=observations)


def project_source(source, pointer):
    if (not isinstance(source, dict) or set(source) != {"format", "version", "observations"}
            or source["format"] != "AIOS_ATTENTION_SOURCE" or type(source["version"]) is not int
            or source["version"] != 1 or source_document(source["observations"]) != source):
        raise AttentionError("SOURCE_SCHEMA_MISMATCH")
    if set(pointer) != set(_POINTER) or pointer["source_digest"] != digest(source):
        raise AttentionError("SOURCE_IDENTITY_MISMATCH")
    for name, kind in _POINTER.items():
        _value(pointer[name], kind)
    items = [project_observation(obs, pointer) for obs in source["observations"]]
    items = [item for item in items if item is not None]
    if len({item.event_id for item in items}) != len(items):
        raise AttentionError("DUPLICATE_OBSERVATION")
    return items


def _publication_context(source, item):
    """Recover only the context already bound to a projected conflict identity."""
    if item.family != CONFLICT:
        return None
    for observation in source["observations"]:
        attempt = observation.get("publication_attempt")
        if (observation["boundary"] == "CANONICAL_CONFLICT" and attempt is not None
                and digest(attempt) == item.selectors["prepared_digest"]):
            return attempt
    return None


def _current_publication_context(sources, context):
    try:
        identity, review = sources.review(context["run_id"], context["decision_sha"])
        if review["verdict"] != "PASS" or any(identity[key] != context[key] for key in _REVIEW):
            return False
        blocker = context["blocker"]
        if blocker is not None:
            identity, review = sources.review(blocker["source_run"], blocker["decision_sha"])
            if review["verdict"] != "PASS" or identity["reviewed_sha"] != blocker["reviewed_sha"]:
                return False
        return True
    except AttentionError:
        return False


def operational_observation(receipt):
    """Strip execution profile and cause text before the transport boundary."""
    from .operational_receipt import BOUNDARIES, FAMILIES, WORKFLOW_REASONS, _bounded_selectors
    required = {"format", "version", "kind", "family", "delivery", "boundary", "selectors"}
    allowed = required | {"run_created", "executor_invoked", "cause", "run_id", "terminal_pointer", "control_sha"}
    if (not isinstance(receipt, dict) or not required.issubset(receipt) or not set(receipt).issubset(allowed)
            or receipt.get("format") != "AIOS_OPERATIONAL_RECEIPT" or type(receipt.get("version")) is not int
            or receipt["version"] != 2 or receipt.get("kind") != "OPERATIONAL_RECEIPT"
            or receipt.get("family") not in FAMILIES or receipt.get("boundary") not in BOUNDARIES):
        raise AttentionError("INVALID_OPERATIONAL_RECEIPT")
    if not isinstance(receipt["selectors"], dict):
        raise AttentionError("INVALID_OPERATIONAL_RECEIPT")
    _bounded_selectors(receipt["selectors"])
    if not isinstance(receipt["delivery"], dict):
        raise AttentionError("INVALID_OPERATIONAL_RECEIPT")
    for flag in ("run_created", "executor_invoked"):
        if flag in receipt and type(receipt[flag]) is not bool:
            raise AttentionError("INVALID_OPERATIONAL_RECEIPT")
    boundary = receipt["boundary"]
    if boundary in NON_WAKE:
        return dict(boundary=boundary)
    delivery = receipt.get("delivery", {})
    cause = receipt.get("cause", {})
    expected_kind = {"PRIMARY": "dispatch_id", "REMEDIATION": "correction_dispatch_id", "REPAIR": "repair_dispatch_id"}[receipt["family"]]
    if (set(delivery) != {"kind", "id"} or delivery["kind"] != expected_kind
            or receipt.get("run_created") is not False or receipt.get("run_id") is not None
            or receipt.get("terminal_pointer") is not None or receipt.get("executor_invoked") is not False):
        raise AttentionError("UNPROVEN_PRE_RUN_BOUNDARY")
    if (not isinstance(cause, dict) or set(cause) != {"authority", "phase", "reason_code"}
            or cause.get("authority") not in {"WORKFLOW", "AIOS_ADMISSION_FAILURE", "CORRECTION_PREFLIGHT", "CARRIER"}
            or not isinstance(cause.get("phase"), str) or not 0 < len(cause["phase"]) <= 64
            or not isinstance(cause.get("reason_code"), str) or not 0 < len(cause["reason_code"]) <= 128):
        raise AttentionError("INVALID_OPERATIONAL_CAUSE")
    if boundary == "ADMISSION_REJECTED" and cause.get("authority") in {"AIOS_ADMISSION_FAILURE", "CORRECTION_PREFLIGHT"}:
        projected = "DISPATCH_REJECTED"
    elif (boundary == "OPERATIONAL_FAILED" and cause.get("authority") == "WORKFLOW"
          and cause.get("phase") == "PRE_AIOS" and cause.get("reason_code") in WORKFLOW_REASONS):
        projected = "PRE_AIOS_FAILED"
    elif (boundary == "OPERATIONAL_FAILED" and cause == {"authority": "CARRIER", "phase": "DISPATCH", "reason_code": "DISPATCH_REJECTED"}):
        projected = "DISPATCH_REJECTED"
    else:
        raise AttentionError("UNPROVEN_OPERATIONAL_FAILURE")
    subject = _delivery_subject(receipt["family"], receipt.get("selectors", {}))
    return dict(boundary=projected, operation=receipt["family"], delivery_id=delivery["id"], run_created=False, **subject)


def _delivery_subject(operation, selectors):
    if operation == "PRIMARY":
        return dict(subject_id=selectors.get("task_id", "NONE"), subject_sha=selectors.get("task_commit_sha", "0" * 40), finding_id="NONE")
    if operation == "REPAIR":
        return dict(subject_id=selectors.get("failed_run_id", "NONE"), subject_sha=selectors.get("repair_sha", "0" * 40), finding_id="NONE")
    return dict(subject_id=selectors.get("source_run_id", "NONE"), subject_sha=selectors.get("reviewed_sha", "0" * 40), finding_id=selectors.get("finding_id", "NONE"))


def write_source(path, observations):
    source = source_document(observations)
    # Validate even before immutable artifact selectors are available.
    project_source(source, dict(workflow_run_id=1, run_attempt=1, artifact_id=1, source_digest=digest(source)))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_bytes(canonical(source))
    os.replace(temp, target)
    return source


def _issue_observation(event, carrier, operation="UNKNOWN", subject_id="NONE"):
    issue = event.get("issue", {})
    body = issue.get("body")
    if (event.get("repository", {}).get("full_name") != REPOSITORY or event.get("action") != "opened"
            or not isinstance(body, str) or len(body.encode("utf-8")) > 262144):
        raise AttentionError("UNPROVEN_ISSUE_SOURCE")
    return dict(boundary="CARRIER_REJECTED", carrier=carrier, operation=operation, subject_id=subject_id,
                issue_id=issue.get("id"), issue_number=issue.get("number"),
                body_digest=hashlib.sha256(body.encode("utf-8")).hexdigest(), run_created=False)


def record_carrier_rejection(event_path, carrier, operation="UNKNOWN", subject_id="NONE"):
    """Opt-in bounded facts from the exact rejected call; never copy its body."""
    path = os.environ.get("AIOS_ATTENTION_SOURCE_PATH")
    if path:
        event = load_json(Path(event_path).read_bytes(), 1048576)
        observation = _issue_observation(event, carrier, operation, subject_id)
        observation.update(subject_sha="0" * 40, finding_id="NONE")
        if carrier == "INGRESS":
            # Read only the rejected envelope's structural selectors, never its action.
            from .authoring_ingress import parse_envelope
            try:
                envelope = parse_envelope(event["issue"]["body"])
                observation["operation"] = envelope.operation
                identity = envelope.identity
                observation["subject_id"] = next((identity[k] for k in ("run_id", "source_run_id", "failed_run_id", "task_id") if k in identity), "NONE")
                observation["finding_id"] = identity.get("finding_id", "NONE")
                expected = envelope.expected_state
                observation["subject_sha"] = next((expected[k] for k in ("expected_candidate_sha", "expected_reviewed_sha", "reviewed_sha", "expected_failed_head_sha") if k in expected), "0" * 40)
                if envelope.operation == "AUTHOR_REPAIR" and observation["subject_sha"] == "0" * 40:
                    payload = envelope.payload if isinstance(envelope.payload, dict) else _load_yaml(envelope.payload, "prepared repair")
                    observation["subject_sha"] = payload.get("failed_head_sha", "0" * 40)
                # Only a directly observed exact predecessor mismatch is a conflict.
                ref, predecessor = None, None
                if "expected_main_sha" in expected:
                    ref, predecessor = "refs/heads/main", expected["expected_main_sha"]
                elif "expected_candidate_sha" in expected and envelope.operation == "SUBMIT_REVIEW":
                    ref = "refs/heads/aios/review/" + observation["subject_id"]
                    predecessor = expected["expected_candidate_sha"]
                elif "expected_reviewed_sha" in expected and envelope.operation == "AUTHOR_REMEDIATION":
                    ref = "refs/heads/aios/review/" + observation["subject_id"]
                    predecessor = expected["expected_reviewed_sha"]
                if ref is not None:
                    _value(predecessor, "sha")
                    repo = os.environ.get("GITHUB_WORKSPACE", ".")
                    observed = GitSources(Path(repo), _git, "origin").refs(ref).get(ref)
                    if observed is not None and predecessor != observed:
                        observation = dict(boundary="CANONICAL_CONFLICT", prepared_sha=observation["subject_sha"],
                                           prepared_digest=observation["body_digest"], predecessor_ref=ref,
                                           predecessor_sha=predecessor, observed_sha=observed)
            except Exception:
                pass  # Malformed carriers remain the generic rejection family.
        write_source(path, [observation])


def record_ingress_success(delivery, repo):
    path = os.environ.get("AIOS_ATTENTION_SOURCE_PATH")
    if not path or delivery.ingress_result.operation != "SUBMIT_REVIEW":
        return
    outcome = delivery.ingress_result
    prefix = "refs/heads/aios/review-decision/"
    if outcome.status not in {"CANONICALIZED", "IDEMPOTENT"} or not outcome.canonical_destination.startswith(prefix):
        raise AttentionError("UNPROVEN_REVIEW_INGRESS")
    identity, review = GitSources(Path(repo), _git, "origin").review(outcome.canonical_destination[len(prefix):], outcome.canonical_sha)
    write_source(path, [dict(boundary="REVIEW_FOLLOWUP", **identity)] if review["verdict"] in {"CHANGES_REQUIRED", "BLOCKED"} else [])


def observe_carrier_rejection(event_path, carrier):
    try:
        record_carrier_rejection(event_path, carrier)
    except Exception:
        print("attention source rejected: SOURCE_UNPROVEN", file=sys.stderr)


def observe_ingress_success(delivery, repo):
    try:
        record_ingress_success(delivery, repo)
    except Exception:
        print("attention source rejected: SOURCE_UNPROVEN", file=sys.stderr)


class GitSources:
    """Read exact refs and bounded identity blobs in a disposable Git store."""

    def __init__(self, path, git, remote):
        self.path, self.git, self.remote = path, git, remote
        # This reader belongs to one disposable observation. Only successful
        # exact-SHA fetches are reusable; mutable ref snapshots are never cached.
        self._fetched = set()

    def refs(self, *patterns):
        values = {}
        for line in self.git(self.path, "ls-remote", "--refs", self.remote, *patterns).splitlines():
            sha, ref = line.split()
            _value(sha, "sha")
            if ref in values:
                raise AttentionError("CONFLICTING_REF")
            values[ref] = sha
        return values

    def fetch(self, sha):
        self.acquire(sha)

    def acquire(self, *shas):
        for sha in shas:
            _value(sha, "sha")
        missing = sorted(set(shas) - self._fetched)
        if not missing:
            return
        self.git(self.path, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--refmap=", self.remote, *missing)
        self._fetched.update(missing)

    def frozen(self, refs):
        # A new reader per observation; never inherit another reader's fetch cache.
        return _GitObservation(self.path, self.git, self.remote, refs)

    def remediation(self, sha, subject, decision):
        name = f".ai/remediations/REMEDIATION-{subject}.yaml"
        correction = self.document(sha, name)
        parents = self.git(self.path, "rev-list", "--parents", "--max-count=1", sha).split()
        changed = self.git(self.path, "diff-tree", "--no-commit-id", "--name-only", "-r", decision, sha).splitlines()
        if parents != [sha, decision] or changed != [name]:
            raise AttentionError("UNPROVEN_REMEDIATION_LINEAGE")
        return correction

    def document(self, sha, name):
        self.fetch(sha)
        return _load_yaml(self.git(self.path, "show", sha + ":" + name), "attention source identity")

    def optional_document(self, sha, name):
        self.fetch(sha)
        paths = self.git(self.path, "ls-tree", "-r", "--name-only", sha, "--", name).splitlines()
        if not paths:
            return None
        if paths != [name]:
            raise AttentionError("CONFLICTING_SOURCE_DOCUMENT")
        return self.document(sha, name)

    def review(self, run_id, decision_sha=None):
        _value(run_id, "run")
        root = "refs/heads/aios/"
        patterns = _review_patterns(run_id)
        before = self.refs(*patterns)
        artifact, head, decision = [before.get(root + x + "/" + run_id) for x in ("artifacts", "review", "review-decision")]
        if (not all((artifact, head, decision)) or any(root + x + "/" + run_id in before for x in ("failure-artifacts", "failure"))
                or (decision_sha is not None and decision != decision_sha)):
            raise AttentionError("UNPROVEN_REVIEW_IDENTITY")
        run = self.document(artifact, ".ai/transport/run.json")
        if run.get("kind") == "REMEDIATION":
            run = run.get("execution", {}).get("run", {})
        elif "kind" in run:
            raise AttentionError("UNPROVEN_REVIEW_IDENTITY")
        task = run.get("task")
        result = self.document(artifact, ".ai/transport/result.json")
        if (run.get("run_id") != run_id or not isinstance(task, dict) or set(task) != {"id", "revision"}
                or not isinstance(task["id"], str) or not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task["id"])
                or type(task["revision"]) is not int or task["revision"] < 1
                or run.get("head_sha") not in (None, head) or result.get("result", {}).get("head_sha") != head):
            raise AttentionError("UNPROVEN_RUN_BINDING")
        self.fetch(decision)
        paths = self.git(self.path, "ls-tree", "-r", "--name-only", decision, "--", ".ai/reviews").splitlines()
        if len(paths) != 1 or paths[0] not in {f".ai/reviews/REVIEW-{run_id[4:]}.yaml", f".ai/reviews/REVIEW-{run_id[4:]}.yml"}:
            raise AttentionError("UNPROVEN_REVIEW_IDENTITY")
        review = self.document(decision, paths[0])
        from .review import parse_review
        parse_review(json.dumps(review))
        if (not {"review_id", "reviewed_sha", "mode", "verdict", "acceptance", "findings"}.issubset(review)
                or review["review_id"] != "REVIEW-" + run_id[4:] or review["reviewed_sha"] != head
                or review["verdict"] not in {"PASS", "CHANGES_REQUIRED", "BLOCKED"} or self.refs(*patterns) != before):
            raise AttentionError("UNPROVEN_REVIEW_IDENTITY")
        return dict(run_id=run_id, artifact_sha=artifact, decision_sha=decision, reviewed_sha=head), review

    def included(self, sha, main):
        self.fetch(sha)
        self.fetch(main)
        # Unlike is-ancestor, rev-list distinguishes negative proof from read failure
        # through successful bounded output; read failure propagates as UNKNOWN.
        remaining = self.git(self.path, "rev-list", "--max-count=1", sha, "--not", main)
        if remaining:
            _value(remaining, "sha")
        return not remaining


class _GitObservation(GitSources):
    """Local proof over one initial snapshot; the caller owns the final read."""

    def __init__(self, path, git, remote, refs):
        super().__init__(path, git, remote)
        self._refs = dict(refs)
        self.acquire(*self._refs.values())

    def refs(self, *patterns):
        return {ref: sha for ref, sha in self._refs.items()
                if any(fnmatchcase(ref, pattern) for pattern in patterns)}

    def fetch(self, sha):
        _value(sha, "sha")
        if sha not in self._fetched:
            # An event-selected ancestor may be inspected locally, but never
            # causes a second remote acquisition or an unbound exact-SHA fetch.
            self.git(self.path, "cat-file", "-e", sha + "^{commit}")


def _review_patterns(run_id):
    return [f"refs/heads/aios/{namespace}/{run_id}" for namespace in
            ("artifacts", "failure-artifacts", "review", "failure", "review-decision")]


def resolve_return_affinity(event_id, sources, artifacts=None):
    """Read-only exact-lineage proof; uncertainty yields no routable selector.

    Event grammar is unchanged. Every mutable ref consulted is checked again;
    descendants can only inherit the exact TASK selector, never select a route.
    """
    from .return_affinity import document_affinity, require_same_affinity
    from .run import Run, RunTaskReference
    from .task import validate_task

    snapshots, visited, proven = {}, set(), {}

    def refs(*patterns):
        key = tuple(patterns)
        value = sources.refs(*patterns)
        if key in snapshots and snapshots[key] != value:
            raise AttentionError("AFFINITY_LINEAGE_MOVED")
        snapshots[key] = value
        return value

    def task_at(sha, identity):
        _value(sha, "sha")
        task = validate_task(sources.document(sha, f".ai/tasks/{identity['id']}.yaml"))
        if {"id": task.task_id, "revision": task.revision} != identity:
            raise AttentionError("AFFINITY_TASK_MISMATCH")
        return task

    def run_affinity(run_id, artifact_sha=None, candidate_sha=None, kind=None, expected_task=None):
        _value(run_id, "run")
        if run_id in proven:
            selector, artifact, candidate, family, reference = proven[run_id]
            if (artifact_sha is not None and artifact_sha != artifact
                    or candidate_sha is not None and candidate_sha != candidate
                    or kind is not None and kind != family
                    or expected_task is not None and expected_task != reference):
                raise AttentionError("AFFINITY_SOURCE_SUBSTITUTION")
            return selector
        if run_id in visited or len(visited) + len(proven) >= 32:
            raise AttentionError("AFFINITY_LINEAGE_BOUND")
        visited.add(run_id)
        root = "refs/heads/aios/"
        current = refs(*_review_patterns(run_id))
        success, failure = [current.get(root + namespace + "/" + run_id)
                            for namespace in ("artifacts", "failure-artifacts")]
        if bool(success) == bool(failure) or (kind == RESULT and not success) or (kind == FAILURE and not failure):
            raise AttentionError("AFFINITY_TERMINAL_UNPROVEN")
        sha = success or failure
        _value(sha, "sha")
        head = current.get(root + ("review/" if success else "failure/") + run_id)
        _value(head, "sha")
        if (artifact_sha is not None and artifact_sha != sha
                or candidate_sha is not None and candidate_sha != head):
            raise AttentionError("AFFINITY_SOURCE_SUBSTITUTION")
        document = sources.document(sha, ".ai/transport/run.json")
        if document.get("kind") == "REMEDIATION":
            execution = document["execution"]
            value = execution["run"]
        elif "kind" not in document:
            execution, value = None, document
        else:
            raise AttentionError("AFFINITY_RUN_KIND")
        reference = value["task"]
        if set(reference) != {"id", "revision"} or expected_task is not None and reference != expected_task:
            raise AttentionError("AFFINITY_TASK_MISMATCH")
        _value(reference["id"], "subject")
        if not reference["id"].startswith("TASK-"):
            raise AttentionError("AFFINITY_TASK_MISMATCH")
        run = Run(run_id=value["run_id"], task=RunTaskReference(**reference),
                  executor=value["executor"], base_sha=value["base_sha"],
                  workspace=value["workspace"], head_sha=value.get("head_sha"),
                  status=value["status"], return_affinity=document_affinity(value))
        if run.run_id != run_id or run.status != "ACTIVE" or run.head_sha not in (None, head):
            raise AttentionError("AFFINITY_RUN_MISMATCH")
        terminal = sources.document(sha, ".ai/transport/" + ("result.json" if success else "failure.json"))
        if success:
            if terminal["result"]["head_sha"] != head:
                raise AttentionError("AFFINITY_RESULT_MISMATCH")
        elif terminal.get("run_id") != run_id or terminal.get("task") != reference or terminal.get("failed_head_sha") != head:
            raise AttentionError("AFFINITY_FAILURE_MISMATCH")
        task = task_at(run.base_sha, reference)
        require_same_affinity(task, run)
        if task_at(head, reference).return_affinity != task.return_affinity or not sources.included(run.base_sha, head):
            raise AttentionError("AFFINITY_CANDIDATE_DRIFT")
        if execution is not None:
            from .review import parse_remediation
            correction = parse_remediation(json.dumps(execution["remediation"]))
            review_id = execution["review_id"]
            if not isinstance(review_id, str) or not review_id.startswith("REVIEW-"):
                raise AttentionError("AFFINITY_REMEDIATION_MISMATCH")
            source_id = "RUN-" + review_id[7:]
            predecessor = document.get("predecessor")
            if predecessor is not None and predecessor != dict(source_run_id=source_id, review_id=review_id,
                    finding_id=correction.finding_id, reviewed_sha=correction.reviewed_sha):
                raise AttentionError("AFFINITY_REMEDIATION_MISMATCH")
            if execution["finding"]["id"] != correction.finding_id:
                raise AttentionError("AFFINITY_REMEDIATION_MISMATCH")
            if run_affinity(source_id, candidate_sha=correction.reviewed_sha, kind=RESULT,
                            expected_task=reference) != task.return_affinity:
                raise AttentionError("AFFINITY_DESCENDANT_DRIFT")
            identity, review = sources.review(source_id)
            if (identity["reviewed_sha"] != correction.reviewed_sha or review.get("review_id") != review_id
                    or review.get("verdict") != "CHANGES_REQUIRED"
                    or not any(finding.get("id") == correction.finding_id and finding.get("action") == correction.action
                               for finding in review.get("findings", []))):
                raise AttentionError("AFFINITY_REMEDIATION_MISMATCH")
            execution_base = document.get("execution_base")
            if execution_base is not None:
                base_id = execution_base.get("cumulative_tip_run_id", execution_base.get("run_id"))
                base_head = execution_base.get("cumulative_tip_candidate_sha", execution_base.get("candidate_sha"))
                if run_affinity(base_id, candidate_sha=base_head, kind=RESULT,
                                expected_task=reference) != task.return_affinity:
                    raise AttentionError("AFFINITY_DESCENDANT_DRIFT")
            elif run.base_sha != correction.reviewed_sha:
                raise AttentionError("AFFINITY_REMEDIATION_MISMATCH")
        repair = sources.optional_document(sha, ".ai/transport/repair.json")
        if repair is not None:
            if (execution is not None or repair.get("run") != value
                    or repair.get("failed_head_sha") != run.base_sha
                    or document_affinity(repair["task"]) != task.return_affinity):
                raise AttentionError("AFFINITY_REPAIR_MISMATCH")
            for nested in ("failure", "repair"):
                if nested in repair:
                    original = repair[nested]
                    identity_key = "run_id" if nested == "failure" else "failed_run_id"
                    if (original.get(identity_key) != repair["failed_run_id"]
                            or original.get("failed_head_sha") != repair["failed_head_sha"]
                            or original.get("task") != reference):
                        raise AttentionError("AFFINITY_REPAIR_MISMATCH")
            if run_affinity(repair["failed_run_id"], candidate_sha=repair["failed_head_sha"], kind=FAILURE,
                            expected_task=reference) != task.return_affinity:
                raise AttentionError("AFFINITY_DESCENDANT_DRIFT")
        visited.remove(run_id)
        proven[run_id] = (task.return_affinity, sha, head, RESULT if success else FAILURE, reference)
        return task.return_affinity

    try:
        item = parse_event_id(event_id)
        if item.family == RECOVERY:
            item = parse_event_id(item.selectors["original_event_id"])
        s = dict(item.selectors)
        publication_context = None
        if set(_POINTER).issubset(s):
            pointer = {key: s[key] for key in _POINTER}
            if artifacts is None:
                return None
            source = artifacts.artifact(pointer)
            if item.event_id not in {candidate.event_id for candidate in project_source(source, pointer)}:
                return None
            publication_context = _publication_context(source, item)
        if item.family in {RESULT, FAILURE}:
            affinity = run_affinity(s["run_id"], s["artifact_sha"], kind=item.family)
        elif item.family in {REVIEW, PUBLICATION_FAILURE, PUBLICATION_SUCCESS}:
            refs(*_review_patterns(s["run_id"]))
            identity, review = sources.review(s["run_id"], s["decision_sha"])
            if any(s[key] != identity[key] for key in _REVIEW):
                return None
            if review["verdict"] not in ({"CHANGES_REQUIRED", "BLOCKED"} if item.family == REVIEW else {"PASS"}):
                return None
            affinity = run_affinity(s["run_id"], s["artifact_sha"], s["reviewed_sha"], RESULT)
            if item.family == PUBLICATION_SUCCESS:
                main = refs("refs/heads/main").get("refs/heads/main")
                if not main or not sources.included(s["published_sha"], main):
                    return None
        elif item.family == CONFLICT and publication_context is not None:
            refs(*_review_patterns(publication_context["run_id"]))
            if publication_context["blocker"] is not None:
                refs(*_review_patterns(publication_context["blocker"]["source_run"]))
            if not _current_publication_context(sources, publication_context):
                return None
            if refs("refs/heads/main").get("refs/heads/main") != s["observed_sha"]:
                return None
            affinity = run_affinity(publication_context["run_id"], publication_context["artifact_sha"],
                                    publication_context["reviewed_sha"], RESULT)
        elif item.family in {DISPATCH, PRE_AIOS}:
            if s["subject_id"] == "NONE" or s["subject_sha"] == "0" * 40:
                return None
            if s["operation"] == "PRIMARY":
                task_doc = sources.document(s["subject_sha"], f".ai/tasks/{s['subject_id']}.yaml")
                task = validate_task(task_doc)
                main = refs("refs/heads/main").get("refs/heads/main")
                if task.task_id != s["subject_id"] or not main or not sources.included(s["subject_sha"], main):
                    return None
                affinity = task.return_affinity
            elif s["operation"] == "REMEDIATION":
                affinity = run_affinity(s["subject_id"], candidate_sha=s["subject_sha"], kind=RESULT)
            else:
                ref = "refs/heads/aios/repair/" + s["subject_id"]
                if refs(ref).get(ref) != s["subject_sha"]:
                    return None
                repair = sources.document(s["subject_sha"], ".ai/transport/repair.json")
                if repair.get("failed_run_id") != s["subject_id"]:
                    return None
                affinity = run_affinity(s["subject_id"], candidate_sha=repair["failed_head_sha"], kind=FAILURE)
        elif item.family in {INGRESS, REVIEW_INGRESS, AUTHORING}:
            issue = artifacts.request(f"/issues/{s['issue_number']}")
            if issue.get("id") != s["issue_id"] or not isinstance(issue.get("body"), str) or hashlib.sha256(issue["body"].encode("utf-8")).hexdigest() != s["body_digest"]:
                return None
            from .authoring_ingress import parse_envelope
            from .return_affinity import require_authored_affinity
            envelope = parse_envelope(issue["body"])
            if envelope.operation != s["operation"]:
                return None
            key = {"AUTHOR_TASK": "task_id", "SUBMIT_REVIEW": "run_id", "AUTHOR_REMEDIATION": "source_run_id", "AUTHOR_REPAIR": "failed_run_id"}[envelope.operation]
            if envelope.identity[key] != s["subject_id"] or envelope.identity.get("finding_id", "NONE") != s["finding_id"]:
                return None
            if envelope.operation == "AUTHOR_TASK" and item.family == INGRESS and s["carrier"] == "INGRESS":
                payload = envelope.payload if isinstance(envelope.payload, dict) else _load_yaml(envelope.payload, "admitted TASK")
                if payload.get("task_id") != s["subject_id"] or type(payload.get("revision")) is not int or payload["revision"] < 1:
                    return None
                affinity = require_authored_affinity(payload)
                main = refs("refs/heads/main").get("refs/heads/main")
                if main is None:
                    return None
                existing = sources.optional_document(main, f".ai/tasks/{s['subject_id']}.yaml")
                if existing is not None and document_affinity(existing) != affinity:
                    return None
            elif s["subject_sha"] != "0" * 40:
                expected = envelope.expected_state
                bound = next((expected[k] for k in ("expected_candidate_sha", "expected_reviewed_sha", "reviewed_sha", "expected_failed_head_sha") if k in expected), None)
                if envelope.operation == "AUTHOR_REPAIR" and bound is None:
                    payload = envelope.payload if isinstance(envelope.payload, dict) else _load_yaml(envelope.payload, "admitted repair")
                    bound = payload.get("failed_head_sha")
                if bound != s["subject_sha"]:
                    return None
                affinity = run_affinity(s["subject_id"], candidate_sha=bound,
                                        kind=FAILURE if envelope.operation == "AUTHOR_REPAIR" else RESULT)
            else:
                return None
        else:
            return None  # No semantic inference for unbound conflict/unknown carriers.
        if any(sources.refs(*patterns) != value for patterns, value in snapshots.items()):
            return None
        return affinity
    except Exception:
        return None  # No raw canonical or local data in transport diagnostics.


# workflow_run must identify a trusted producer path, never just a display name.
# Reusable carrier artifacts inherit their parent workflow identity.
PRODUCERS = frozenset({
    ".github/workflows/aios-issue-carrier.yml",
    ".github/workflows/aios-brain-ingress.yml", ".github/workflows/aios-brain-wakeup.yml",
    ".github/workflows/aios-brain-repair-wakeup.yml", ".github/workflows/aios-brain-remediation-intent.yml",
    ".github/workflows/aios-self-hosted-wakeup.yml", ".github/workflows/aios-self-hosted-repair-wakeup.yml",
    ".github/workflows/aios-approved-remediation-wakeup.yml", ".github/workflows/aios-approved-remediation-intent.yml",
    ".github/workflows/aios-auto-publish.yml",
})


class ArtifactSources:
    """Bounded read-only GitHub source evidence; no logs or chat history."""

    def __init__(self, repository, deadline=None):
        self.repository = repository
        self.deadline = deadline if deadline is not None else time.monotonic() + 30

    def request(self, path, binary=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AttentionError("SOURCE_TIMEOUT")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "AIOS-attention-v1"}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        # Download redirects are fetched separately to avoid credential forwarding.
        request = Request("https://api.github.com/repos/" + self.repository + path, headers=headers)
        from urllib.request import HTTPRedirectHandler, build_opener
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        from urllib.error import HTTPError
        try:
            response = build_opener(NoRedirect()).open(request, timeout=min(10, remaining))
        except HTTPError as exc:
            if not binary or exc.code != 302:
                raise AttentionError("SOURCE_UNAVAILABLE") from None
            from urllib.parse import urlsplit
            location = exc.headers.get("Location", "")
            parts = urlsplit(location)
            if parts.scheme != "https" or parts.username or parts.password:
                raise AttentionError("INVALID_ARTIFACT_LOCATION")
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise AttentionError("SOURCE_TIMEOUT")
            response = urlopen(Request(location, headers={"User-Agent": "AIOS-attention-v1"}), timeout=min(10, remaining))
        with response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise AttentionError("SOURCE_SIZE")
        if time.monotonic() > self.deadline:
            raise AttentionError("SOURCE_TIMEOUT")
        return raw if binary else load_json(raw, 1048576)

    def run(self, run_id, attempt):
        _value(run_id, "positive")
        _value(attempt, "positive")
        run = self.request(f"/actions/runs/{run_id}/attempts/{attempt}")
        if (type(run.get("id")) is not int or type(run.get("run_attempt")) is not int
                or run.get("id") != run_id or run.get("run_attempt") != attempt
                or run.get("path") not in PRODUCERS or run.get("status") != "completed"
                or run.get("event") not in {"issues", "push", "workflow_dispatch"}
                or run.get("head_repository", {}).get("full_name") != self.repository
                or (run.get("event") != "push" and run.get("head_branch") != "main")):
            raise AttentionError("UNTRUSTED_PRODUCER")
        return run

    def artifact(self, pointer, *, receipt=False):
        _value(pointer.get("artifact_id"), "positive")
        run = self.run(pointer["workflow_run_id"], pointer["run_attempt"])
        artifact = self.request(f"/actions/artifacts/{pointer['artifact_id']}")
        source_names = {
            f"aios-attention-source-v1-{lane}-attempt-{pointer['run_attempt']}" for lane in
            ("ingress", "primary", "repair", "remediation", "publication", "dispatch")}
        operational_names = {
            f"aios-operational-receipt-v2-{operation}-attempt-{pointer['run_attempt']}" for operation in
            ("primary", "repair", "remediation")}
        if (type(artifact.get("id")) is not int or artifact.get("id") != pointer["artifact_id"] or artifact.get("expired") is not False
                or type(artifact.get("workflow_run", {}).get("id")) is not int
                or artifact.get("workflow_run", {}).get("id") != run["id"]
                or artifact.get("name") not in source_names | operational_names
                or type(artifact.get("size_in_bytes")) is not int or not 0 < artifact["size_in_bytes"] <= 1048576):
            raise AttentionError("ARTIFACT_IDENTITY_MISMATCH")
        archive = self.request(f"/actions/artifacts/{pointer['artifact_id']}/zip", binary=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            entries = zipped.infolist()
            operational = artifact["name"] in operational_names
            lane = artifact["name"].split("-attempt-")[0].removeprefix("aios-operational-receipt-v2-")
            expected = f"aios-{lane}-operational-receipt-v2.json" if operational else "source.json"
            if (len(entries) != 1 or entries[0].filename != expected
                    or entries[0].file_size > MAX_SOURCE_BYTES or entries[0].flag_bits & 1):
                raise AttentionError("ARTIFACT_SCHEMA_MISMATCH")
            source = load_json(zipped.read(entries[0]), MAX_SOURCE_BYTES)
            raw_receipt = source if operational else None
            if operational:
                if source.get("family") != lane.upper():
                    raise AttentionError("OPERATION_SUBSTITUTION")
                source = source_document([operational_observation(source)])
        if pointer.get("source_digest") is not None and pointer["source_digest"] != digest(source):
            raise AttentionError("ARTIFACT_CONTENT_MISMATCH")
        if receipt and raw_receipt is None:
            raise AttentionError("OPERATIONAL_RECEIPT_MISSING")
        return raw_receipt if receipt else source

    def delivery_successor(self, selectors):
        """Only bounded reruns of this exact workflow, never search for a newer RUN."""
        current = self.request(f"/actions/runs/{selectors['workflow_run_id']}")
        latest = current.get("run_attempt")
        _value(latest, "positive")
        if latest == selectors["run_attempt"]:
            return None
        if latest < selectors["run_attempt"] or latest > selectors["run_attempt"] + 8:
            raise AttentionError("SUCCESSOR_BOUND_EXCEEDED")
        self.run(selectors["workflow_run_id"], latest)
        listing = self.request(f"/actions/runs/{selectors['workflow_run_id']}/artifacts?per_page=100")
        if (type(listing.get("total_count")) is not int or not 0 <= listing["total_count"] <= 100
                or not isinstance(listing.get("artifacts"), list) or len(listing["artifacts"]) != listing["total_count"]):
            raise AttentionError("ARTIFACT_LIST_UNBOUNDED")
        expected = f"aios-operational-receipt-v2-{selectors['operation'].lower()}-attempt-{latest}"
        matches = [item for item in listing.get("artifacts", []) if item.get("name") == expected]
        if len(matches) > 1:
            raise AttentionError("CONFLICTING_DELIVERY_SUCCESSOR")
        if not matches:
            return None
        receipt = self.artifact(dict(workflow_run_id=selectors["workflow_run_id"], run_attempt=latest, artifact_id=matches[0]["id"]), receipt=True)
        if (receipt.get("family") != selectors["operation"]
                or receipt.get("delivery", {}).get("id") != selectors["delivery_id"]
                or _delivery_subject(selectors["operation"], receipt["selectors"]) != {
                    key: selectors[key] for key in ("subject_id", "subject_sha", "finding_id")}):
            raise AttentionError("DELIVERY_SUBSTITUTION")
        if receipt.get("boundary") not in {"RUN_ATTRIBUTED", "TERMINAL_POINTER"} or receipt.get("run_created") is not True:
            return None
        run_id = receipt.get("run_id") or receipt.get("terminal_pointer", {}).get("run_id")
        _value(run_id, "run")
        return run_id, receipt


def _delivery_resolution(s, sources, artifacts, successor):
    if successor is None:
        return "UNRESOLVED"
    run_id, receipt = successor
    root = "refs/heads/aios/"
    patterns = [root + namespace + "/" + run_id for namespace in ("artifacts", "failure-artifacts", "review", "failure")]
    before = sources.refs(*patterns)
    successes = before.get(root + "artifacts/" + run_id)
    failures = before.get(root + "failure-artifacts/" + run_id)
    if bool(successes) == bool(failures):
        return "UNKNOWN"
    sha = successes or failures
    run_data = sources.document(sha, ".ai/transport/run.json")
    run = run_data.get("execution", {}).get("run", {}) if run_data.get("kind") == "REMEDIATION" else run_data
    selectors = receipt["selectors"]
    if run.get("run_id") != run_id:
        return "UNKNOWN"
    if s["operation"] == "PRIMARY":
        if run.get("kind") is not None or run.get("task") != {"id": selectors.get("task_id"), "revision": selectors.get("task_revision")}:
            return "UNKNOWN"
    elif s["operation"] == "REMEDIATION":
        lineage = run_data.get("execution", {}).get("remediation", {})
        if lineage.get("source_run_id") != s["subject_id"] or lineage.get("finding_id") != s["finding_id"]:
            return "UNKNOWN"
    else:
        repair = sources.document(sha, ".ai/transport/repair.json")
        if repair.get("failed_run_id") != s["subject_id"]:
            return "UNKNOWN"
        source_ref = root + "repair/" + s["subject_id"]
        if sources.refs(source_ref).get(source_ref) != s["subject_sha"]:
            return "UNKNOWN"
        exact_authorization = sources.document(s["subject_sha"], ".ai/transport/repair.json")
        if repair != exact_authorization:
            return "UNKNOWN"
    if successes:
        result = sources.document(sha, ".ai/transport/result.json")
        head = result.get("result", {}).get("head_sha")
        candidate = before.get(root + "review/" + run_id)
    else:
        failure = sources.document(sha, ".ai/transport/failure.json")
        if failure.get("run_id") != run_id or failure.get("task") != run.get("task"):
            return "UNKNOWN"
        head = failure.get("failed_head_sha")
        candidate = before.get(root + "failure/" + run_id)
    _value(head, "sha")
    if head != candidate or run.get("head_sha") not in (None, head) or sources.refs(*patterns) != before:
        return "UNKNOWN"
    return "RESOLVED"


def freshness(item, sources, artifacts):
    """One coherent before/after snapshot around an observation-local proof."""
    s = dict(item.selectors)
    if item.family in {RESULT, FAILURE, RECOVERY}:
        raise AttentionError("USE_ORIGINAL_LANE_FRESHNESS")
    publication_context = None
    if set(_POINTER).issubset(s):
        pointer = {key: s[key] for key in _POINTER}
        source = artifacts.artifact(pointer)
        candidates = project_source(source, pointer)
        if item.event_id not in {candidate.event_id for candidate in candidates}:
            return "UNKNOWN"
        publication_context = _publication_context(source, item)
    root = "refs/heads/aios/"
    patterns, successor = [], None
    if item.family in {REVIEW, PUBLICATION_FAILURE, PUBLICATION_SUCCESS}:
        patterns = _review_patterns(s["run_id"])
        patterns += ([root + "remediation/" + s["run_id"] + "-*"] if item.family == REVIEW
                     else ["refs/heads/main"])
    elif item.family == CONFLICT:
        patterns = [s["predecessor_ref"]]
        if publication_context is not None:
            patterns += _review_patterns(publication_context["run_id"])
            if publication_context["blocker"] is not None:
                patterns += _review_patterns(publication_context["blocker"]["source_run"])
    elif item.family == REVIEW_INGRESS and s["subject_sha"] != "0" * 40:
        patterns = _review_patterns(s["subject_id"])
    elif item.family == AUTHORING and s["subject_sha"] != "0" * 40:
        if s["operation"] == "AUTHOR_REMEDIATION":
            patterns = _review_patterns(s["subject_id"]) + [root + "remediation/" + s["subject_id"] + "-" + s["finding_id"]]
        else:
            patterns = [root + "repair/" + s["subject_id"]]
    elif item.family == INGRESS and s["carrier"] == "INGRESS" and s["operation"] == "AUTHOR_TASK":
        patterns = ["refs/heads/main"]
    elif item.family in {DISPATCH, PRE_AIOS}:
        successor = artifacts.delivery_successor(s)
        if successor is not None:
            run_id, _ = successor
            _value(run_id, "run")
            patterns = [root + namespace + "/" + run_id for namespace in
                        ("artifacts", "failure-artifacts", "review", "failure")]
            if s["operation"] == "REPAIR":
                patterns.append(root + "repair/" + s["subject_id"])
    if not patterns:
        return _freshness(item, sources, artifacts, successor)
    before = sources.refs(*patterns)
    observation = sources.frozen(before)
    state = _freshness(item, observation, artifacts, successor, publication_context)
    return state if sources.refs(*patterns) == before else "UNKNOWN"


def _findings(review):
    from .review import REMEDIATION_ACTIONS
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise AttentionError("UNPROVEN_FINDINGS")
    actions = {}
    for finding in findings:
        if not isinstance(finding, dict):
            raise AttentionError("UNPROVEN_FINDINGS")
        finding_id = finding.get("id")
        _value(finding_id, "delivery")
        if finding_id in actions or finding.get("action") not in REMEDIATION_ACTIONS:
            raise AttentionError("UNPROVEN_FINDINGS")
        actions[finding_id] = finding["action"]
    return actions


def _remediation(sources, ref, sha, identity, actions):
    from .review import parse_remediation
    subject = ref.rsplit("/", 1)[-1]
    correction = parse_remediation(json.dumps(sources.remediation(sha, subject, identity["decision_sha"])))
    if (ref != "refs/heads/aios/remediation/" + identity["run_id"] + "-" + correction.finding_id
            or correction.reviewed_sha != identity["reviewed_sha"]
            or actions.get(correction.finding_id) != correction.action):
        raise AttentionError("UNPROVEN_REMEDIATION_BINDING")
    return correction.finding_id


def _freshness(item, sources, artifacts, successor, publication_context=None):
    """Exact source reconstruction, then only exact canonical successor facts."""
    s = dict(item.selectors)
    if item.family in {REVIEW, PUBLICATION_FAILURE, PUBLICATION_SUCCESS}:
        identity, review = sources.review(s["run_id"], s["decision_sha"])
        if any(s[key] != identity[key] for key in _REVIEW):
            return "UNKNOWN"
        if item.family == REVIEW:
            if review["verdict"] not in {"CHANGES_REQUIRED", "BLOCKED"}:
                return "UNKNOWN"
            # An exact correction artifact is a successor; never choose its strategy.
            actions = _findings(review)
            pattern = f"refs/heads/aios/remediation/{s['run_id']}-*"
            refs = sources.refs(pattern)
            corrected = set()
            for ref, sha in refs.items():
                corrected.add(_remediation(sources, ref, sha, identity, actions))
            return "RESOLVED" if actions and corrected == set(actions) else "UNRESOLVED"
        if review["verdict"] != "PASS":
            return "UNKNOWN"
        main_ref = "refs/heads/main"
        main = sources.refs(main_ref).get(main_ref)
        if main is None:
            return "UNKNOWN"
        included = sources.included(s["reviewed_sha"], main)
        if item.family == PUBLICATION_FAILURE:
            return "RESOLVED" if included else "UNRESOLVED"
        if not included:
            return "UNKNOWN"
        # Planning resolution requires an exact published source bookmark, never next_action.
        roadmap = sources.document(main, ".ai/roadmap-state.yaml")
        if type(roadmap.get("version")) is not int or roadmap["version"] != 1:
            return "UNKNOWN"
        bookmarks = roadmap.get("sequence")
        if not isinstance(bookmarks, list):
            return "UNKNOWN"
        reconciled = False
        for bookmark in bookmarks:
            if not isinstance(bookmark, dict):
                return "UNKNOWN"
            record = bookmark.get("completed_by")
            if isinstance(record, dict) and record.get("run_id") == s["run_id"]:
                if (record.get("published_sha") != s["published_sha"] or record.get("artifacts_sha") != s["artifact_sha"]
                        or record.get("review_id") != "REVIEW-" + s["run_id"][4:]
                        or record.get("review_decision_sha") != s["decision_sha"] or record.get("reviewed_sha") != s["reviewed_sha"]):
                    return "UNKNOWN"
                reconciled |= bookmark.get("status") == "DONE"
        if sources.refs(main_ref).get(main_ref) != main:
            return "UNKNOWN"
        return "RESOLVED" if reconciled else "UNRESOLVED"
    if item.family == CONFLICT:
        if publication_context is not None and not _current_publication_context(sources, publication_context):
            return "UNKNOWN"
        current = sources.refs(s["predecessor_ref"]).get(s["predecessor_ref"])
        if current is None:
            return "UNKNOWN"
        if s["prepared_sha"] != "0" * 40:
            if current == s["prepared_sha"] or (s["predecessor_ref"] == "refs/heads/main" and sources.included(s["prepared_sha"], current)):
                return "RESOLVED" if sources.refs(s["predecessor_ref"]).get(s["predecessor_ref"]) == current else "UNKNOWN"
        # Missing or a third unbound predecessor is a conflict hold, never a replacement choice.
        if current != s["observed_sha"]:
            return "UNKNOWN"
        return "UNRESOLVED"
    if item.family in {INGRESS, REVIEW_INGRESS, AUTHORING}:
        if item.family == REVIEW_INGRESS and s["subject_sha"] != "0" * 40:
            decision = sources.refs(f"refs/heads/aios/review-decision/{s['subject_id']}")
            if decision:
                identity, _ = sources.review(s["subject_id"])
                return "RESOLVED" if identity["reviewed_sha"] == s["subject_sha"] else "UNKNOWN"
        if item.family == AUTHORING and s["subject_sha"] != "0" * 40:
            if s["operation"] == "AUTHOR_REMEDIATION":
                subject = s["subject_id"] + "-" + s["finding_id"]
                ref = "refs/heads/aios/remediation/" + subject
                refs = sources.refs(ref)
                if ref in refs:
                    identity, review = sources.review(s["subject_id"])
                    if identity["reviewed_sha"] != s["subject_sha"] or review["verdict"] != "CHANGES_REQUIRED":
                        return "UNKNOWN"
                    if _remediation(sources, ref, refs[ref], identity, _findings(review)) == s["finding_id"]:
                        return "RESOLVED"
                    return "UNKNOWN"
            else:
                ref = "refs/heads/aios/repair/" + s["subject_id"]
                refs = sources.refs(ref)
                if ref in refs:
                    repair = sources.document(refs[ref], ".ai/transport/repair.json")
                    if (repair.get("failed_run_id") == s["subject_id"] and repair.get("failed_head_sha") == s["subject_sha"]
                            and sources.refs(ref) == refs):
                        return "RESOLVED"
                    return "UNKNOWN"
        # Changes to the exact issue are not canonical supersession or source proof.
        issue = artifacts.request(f"/issues/{s['issue_number']}")
        if issue.get("id") != s["issue_id"] or not isinstance(issue.get("body"), str):
            return "UNKNOWN"
        if hashlib.sha256(issue["body"].encode("utf-8")).hexdigest() != s["body_digest"]:
            return "UNKNOWN"
        if item.family == INGRESS and s["carrier"] == "INGRESS" and s["operation"] == "AUTHOR_TASK":
            from .authoring_ingress import parse_envelope
            envelope = parse_envelope(issue["body"])
            payload = envelope.payload if isinstance(envelope.payload, dict) else _load_yaml(envelope.payload, "prepared task")
            main_ref = "refs/heads/main"
            before = sources.refs(main_ref)
            main = before.get(main_ref)
            if main is None:
                return "UNKNOWN"
            task = sources.optional_document(main, f".ai/tasks/{s['subject_id']}.yaml")
            if sources.refs(main_ref) != before:
                return "UNKNOWN"
            if task == payload:
                return "RESOLVED"
        return "UNRESOLVED"
    if item.family in {DISPATCH, PRE_AIOS}:
        return _delivery_resolution(s, sources, artifacts, successor)
    return "UNKNOWN"


def _git(path, *args):
    process = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                             timeout=15, check=False, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if process.returncode or len(process.stdout) > 1048576:
        raise AttentionError("CANONICAL_UNKNOWN")
    return process.stdout.decode("utf-8", errors="strict").strip()


def capture_publication(report_path, source_path, run_id, decision_sha, repo="."):
    """Read the exact publisher report and canonical lineage, not workflow conclusion."""
    sources = GitSources(Path(repo), _git, "origin")
    identity, review = sources.review(run_id, decision_sha)
    if review["verdict"] in {"CHANGES_REQUIRED", "BLOCKED"}:
        return write_source(source_path, [dict(boundary="REVIEW_FOLLOWUP", **identity)])
    report = load_json(Path(report_path).read_bytes(), MAX_SOURCE_BYTES)
    legacy_fields = {"source_run", "reviewed_sha", "prior_main_sha", "outcome", "detail"}
    if (not isinstance(report, dict)
            or set(report) not in (legacy_fields, legacy_fields | {"cause", "recovery", "blocker"})
            or not isinstance(report["outcome"], str) or not isinstance(report["detail"], str)
            or report["source_run"] != run_id or report["reviewed_sha"] != identity["reviewed_sha"]):
        raise AttentionError("UNPROVEN_PUBLICATION_ATTEMPT")
    _value(report["prior_main_sha"], "sha")
    outcome = report["outcome"]
    if outcome in {"PUBLISHED", "ALREADY_PUBLISHED", "ALREADY_INCLUDED"}:
        if (report.get("cause", "NONE") != "NONE" or report.get("recovery") is not None
                or report.get("blocker") is not None):
            raise AttentionError("UNPROVEN_PUBLICATION_ATTEMPT")
        main = sources.refs("refs/heads/main").get("refs/heads/main")
        if main is None or not sources.included(identity["reviewed_sha"], main):
            raise AttentionError("UNPROVEN_PUBLICATION")
        if sources.refs("refs/heads/main").get("refs/heads/main") != main:
            raise AttentionError("UNPROVEN_PUBLICATION")
        observation = dict(boundary="PUBLICATION_PROVEN", **identity, published_sha=identity["reviewed_sha"])
    elif outcome == "INTEGRATION_REQUIRED":
        # Historical intermediate classification is not an actionable recovery
        # result. Never ring Brain merely to rediscover a Git procedure.
        return write_source(source_path, [])
    elif outcome in {"RECOVERY_REVIEW_REQUIRED", "RECOVERY_BLOCKED", "FAILED"}:
        attempt = _publication_attempt(dict(
            **identity, sampled_main_sha=report["prior_main_sha"], outcome=outcome,
            cause=report.get("cause", "PUBLICATION_GATE_FAILED"),
            recovery=report.get("recovery"), blocker=report.get("blocker")))
        if attempt["blocker"] is not None:
            blocker = attempt["blocker"]
            blocked_identity, blocked_review = sources.review(blocker["source_run"], blocker["decision_sha"])
            if blocked_review["verdict"] != "PASS" or blocked_identity["reviewed_sha"] != blocker["reviewed_sha"]:
                raise AttentionError("UNPROVEN_PUBLICATION_BLOCKER")
        main = sources.refs("refs/heads/main").get("refs/heads/main")
        _value(main, "sha")
        if outcome == "FAILED":
            observation = dict(boundary="PUBLICATION_FAILED", **identity, stage="EXECUTION",
                               publication_attempt=attempt)
        else:
            if main != report["prior_main_sha"]:
                raise AttentionError("STALE_PUBLICATION_RECOVERY")
            observation = dict(boundary="CANONICAL_CONFLICT", prepared_sha=identity["reviewed_sha"],
                               prepared_digest=digest(attempt), predecessor_ref="refs/heads/main",
                               predecessor_sha=report["prior_main_sha"], observed_sha=main,
                               publication_attempt=attempt)
    else:
        raise AttentionError("UNKNOWN_PUBLICATION_OUTCOME")
    return write_source(source_path, [observation])


def publication_event(source_path, workflow_run_id, attempt, artifact_id, source_digest, repository=REPOSITORY):
    """Project the already-exported publisher source through the shared classifier.

    The capture digest and upload artifact ID are current-run outputs. No completed
    workflow notification, artifact search, logs or synthetic pointer is needed.
    """
    if repository != REPOSITORY:
        raise AttentionError("REPOSITORY_SUBSTITUTION")
    with Path(source_path).open("rb") as stream:
        source = load_json(stream.read(MAX_SOURCE_BYTES + 1), MAX_SOURCE_BYTES)
    pointer = dict(workflow_run_id=workflow_run_id, run_attempt=attempt,
                   artifact_id=artifact_id, source_digest=source_digest)
    items = project_source(source, pointer)
    if len(items) > 1:
        raise AttentionError("PUBLICATION_SOURCE_CAPACITY")
    return items[0].event_id if items else ""


def collect(run_id, attempt, repository=REPOSITORY, artifacts=None):
    if repository != REPOSITORY:
        raise AttentionError("REPOSITORY_SUBSTITUTION")
    artifacts = artifacts or ArtifactSources(repository)
    artifacts.run(run_id, attempt)
    listing = artifacts.request(f"/actions/runs/{run_id}/artifacts?per_page=100")
    if (type(listing.get("total_count")) is not int or not 0 <= listing["total_count"] <= 100
            or not isinstance(listing.get("artifacts"), list) or len(listing["artifacts"]) != listing["total_count"]):
        raise AttentionError("ARTIFACT_LIST_UNBOUNDED")
    events, names = [], set()
    for artifact in listing["artifacts"]:
        name = artifact.get("name", "")
        if not re.fullmatch(r"(?:aios-attention-source-v1-(ingress|primary|repair|remediation|publication|dispatch)|aios-operational-receipt-v2-(primary|repair|remediation))-attempt-" + str(attempt), name):
            continue
        if name in names:
            raise AttentionError("DUPLICATE_SOURCE_ARTIFACT")
        names.add(name)
        pointer = dict(workflow_run_id=run_id, run_attempt=attempt, artifact_id=artifact["id"])
        source = artifacts.artifact(pointer)
        pointer["source_digest"] = digest(source)
        events.extend(item.event_id for item in project_source(source, pointer))
    # The exact same canonical review/publication can be observed by multiple sources.
    return list(dict.fromkeys(events))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        print("attention source rejected: INVALID_INPUT", file=sys.stderr)
        raise SystemExit(1)


def main(argv=None):
    parser = _Parser(description="Project bounded attention sources", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--run-id", type=int, required=True)
    collect_parser.add_argument("--attempt", type=int, required=True)
    collect_parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    operational = sub.add_parser("operational")
    operational.add_argument("--receipt", required=True)
    operational.add_argument("--source", required=True)
    publication = sub.add_parser("publication")
    publication.add_argument("--report", required=True)
    publication.add_argument("--source", required=True)
    publication.add_argument("--run-id", required=True)
    publication.add_argument("--decision-sha", required=True)
    publication.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    publication_identity = sub.add_parser("publication-event")
    publication_identity.add_argument("--source", required=True)
    publication_identity.add_argument("--workflow-run-id", type=int, required=True)
    publication_identity.add_argument("--attempt", type=int, required=True)
    publication_identity.add_argument("--artifact-id", type=int, required=True)
    publication_identity.add_argument("--source-digest", required=True)
    publication_identity.add_argument("--repository", required=True)
    publication_identity.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--source", required=True)
    dispatch.add_argument("--operation", choices=("PRIMARY", "REMEDIATION", "REPAIR", "PUBLICATION"), required=True)
    dispatch.add_argument("--delivery-id")
    dispatch.add_argument("--run-id")
    dispatch.add_argument("--subject-id", default="NONE")
    dispatch.add_argument("--subject-sha", default="0" * 40)
    dispatch.add_argument("--finding-id", default="NONE")
    args = parser.parse_args(argv)
    try:
        validate_registry(".ai/brain-attention-families.yaml")
        if args.command == "collect":
            events = collect(args.run_id, args.attempt)
            with Path(args.output).open("a", encoding="utf-8") as stream:
                stream.write("events=" + json.dumps(events, separators=(",", ":")) + "\n")
                stream.write("has_events=" + ("true" if events else "false") + "\n")
        elif args.command == "operational":
            receipt = load_json(Path(args.receipt).read_bytes(), MAX_SOURCE_BYTES)
            write_source(args.source, [operational_observation(receipt)])
        elif args.command == "publication":
            source = capture_publication(args.report, args.source, args.run_id, args.decision_sha)
            if args.output:
                with Path(args.output).open("a", encoding="utf-8") as stream:
                    stream.write("source_digest=" + digest(source) + "\n")
        elif args.command == "publication-event":
            event_id = publication_event(args.source, args.workflow_run_id, args.attempt,
                                         args.artifact_id, args.source_digest, args.repository)
            with Path(args.output).open("a", encoding="utf-8") as stream:
                stream.write("event_id=" + event_id + "\n")
        elif args.operation == "PUBLICATION":
            identity, review = GitSources(Path("."), _git, "origin").review(args.run_id)
            if review["verdict"] != "PASS":
                raise AttentionError("UNPROVEN_PUBLICATION_DISPATCH")
            write_source(args.source, [dict(boundary="PUBLICATION_FAILED", **identity, stage="DISPATCH")])
        else:
            write_source(args.source, [dict(boundary="DISPATCH_REJECTED", operation=args.operation,
                                           delivery_id=args.delivery_id, run_created=False,
                                           subject_id=args.subject_id, subject_sha=args.subject_sha, finding_id=args.finding_id)])
    except Exception:
        print("attention source rejected: SOURCE_UNPROVEN", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
