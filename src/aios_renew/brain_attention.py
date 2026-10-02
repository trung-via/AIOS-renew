"""Versioned selector-only attention projection. No semantic routing authority.

Source workflows export bounded observations, never a chosen attention family.
Immutable attempt artifacts are subordinate evidence, not RUN/FAILURE truth.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
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


def project_observation(observation, pointer):
    """The sole family classifier. Source status is a fact, never a flow choice."""
    if not isinstance(observation, dict) or not isinstance(observation.get("boundary"), str):
        raise AttentionError("INVALID_OBSERVATION")
    boundary = observation["boundary"]
    fields = {k: v for k, v in observation.items() if k != "boundary"}
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
        _value(sha, "sha")
        self.git(self.path, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--refmap=", self.remote, sha)

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
        patterns = [root + x + "/" + run_id for x in ("artifacts", "failure-artifacts", "review", "review-decision")]
        before = self.refs(*patterns)
        artifact, head, decision = [before.get(root + x + "/" + run_id) for x in ("artifacts", "review", "review-decision")]
        if (not all((artifact, head, decision)) or root + "failure-artifacts/" + run_id in before
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
        self.deadline = deadline or time.monotonic() + 30

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


def _delivery_resolution(s, sources, artifacts):
    successor = artifacts.delivery_successor(s)
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
    """Exact source reconstruction, then only exact canonical successor facts."""
    s = dict(item.selectors)
    if item.family in {RESULT, FAILURE, RECOVERY}:
        raise AttentionError("USE_ORIGINAL_LANE_FRESHNESS")
    if set(_POINTER).issubset(s):
        pointer = {key: s[key] for key in _POINTER}
        candidates = project_source(artifacts.artifact(pointer), pointer)
        if item.event_id not in {candidate.event_id for candidate in candidates}:
            return "UNKNOWN"
    if item.family in {REVIEW, PUBLICATION_FAILURE, PUBLICATION_SUCCESS}:
        identity, review = sources.review(s["run_id"], s["decision_sha"])
        if any(s[key] != identity[key] for key in _REVIEW):
            return "UNKNOWN"
        main_ref = "refs/heads/main"
        main = sources.refs(main_ref).get(main_ref)
        if main is None:
            return "UNKNOWN"
        included = sources.included(s["reviewed_sha"], main)
        if sources.refs(main_ref).get(main_ref) != main:
            return "UNKNOWN"
        if item.family == REVIEW:
            if review["verdict"] not in {"CHANGES_REQUIRED", "BLOCKED"}:
                return "UNKNOWN"
            # An exact correction artifact is a successor; never choose its strategy.
            findings = review.get("findings")
            if not isinstance(findings, list):
                return "UNKNOWN"
            ids = set()
            for finding in findings:
                if not isinstance(finding, dict) or finding.get("id") in ids:
                    return "UNKNOWN"
                _value(finding.get("id"), "delivery")
                ids.add(finding["id"])
            pattern = f"refs/heads/aios/remediation/{s['run_id']}-*"
            refs = sources.refs(pattern)
            corrected = set()
            for ref, sha in refs.items():
                subject = ref.rsplit("/", 1)[-1]
                correction = sources.document(sha, f".ai/remediations/REMEDIATION-{subject}.yaml")
                if (correction.get("source_run_id") != s["run_id"]
                        or correction.get("reviewed_sha") != s["reviewed_sha"]
                        or correction.get("finding_id") not in ids
                        or subject != s["run_id"] + "-" + correction["finding_id"]):
                    return "UNKNOWN"
                corrected.add(correction["finding_id"])
            if sources.refs(pattern) != refs:
                return "UNKNOWN"
            return "RESOLVED" if ids and corrected == ids else "UNRESOLVED"
        if review["verdict"] != "PASS":
            return "UNKNOWN"
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
                    identity, _ = sources.review(s["subject_id"])
                    correction = sources.document(refs[ref], f".ai/remediations/REMEDIATION-{subject}.yaml")
                    if (identity["reviewed_sha"] == s["subject_sha"] and correction.get("source_run_id") == s["subject_id"]
                            and correction.get("finding_id") == s["finding_id"] and correction.get("reviewed_sha") == s["subject_sha"]
                            and sources.refs(ref) == refs):
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
        return _delivery_resolution(s, sources, artifacts)
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
        write_source(source_path, [dict(boundary="REVIEW_FOLLOWUP", **identity)])
        return
    report = load_json(Path(report_path).read_bytes(), MAX_SOURCE_BYTES)
    if (set(report) != {"source_run", "reviewed_sha", "prior_main_sha", "outcome", "detail"}
            or report["source_run"] != run_id or report["reviewed_sha"] != identity["reviewed_sha"]):
        raise AttentionError("UNPROVEN_PUBLICATION_ATTEMPT")
    _value(report["prior_main_sha"], "sha")
    outcome = report["outcome"]
    if outcome in {"PUBLISHED", "ALREADY_PUBLISHED", "ALREADY_INCLUDED"}:
        main = sources.refs("refs/heads/main").get("refs/heads/main")
        if main is None or not sources.included(identity["reviewed_sha"], main):
            raise AttentionError("UNPROVEN_PUBLICATION")
        observation = dict(boundary="PUBLICATION_PROVEN", **identity, published_sha=identity["reviewed_sha"])
    elif outcome == "INTEGRATION_REQUIRED":
        main = sources.refs("refs/heads/main").get("refs/heads/main")
        observation = dict(boundary="CANONICAL_CONFLICT", prepared_sha=identity["reviewed_sha"],
                           prepared_digest=digest(identity), predecessor_ref="refs/heads/main",
                           predecessor_sha=report["prior_main_sha"], observed_sha=main)
    elif outcome == "FAILED":
        observation = dict(boundary="PUBLICATION_FAILED", **identity, stage="EXECUTION")
    else:
        raise AttentionError("UNKNOWN_PUBLICATION_OUTCOME")
    write_source(source_path, [observation])


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
            capture_publication(args.report, args.source, args.run_id, args.decision_sha)
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
