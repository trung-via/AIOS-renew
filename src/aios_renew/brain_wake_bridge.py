"""Deterministic GitHub wake bells; source metadata is never lifecycle truth.

Issue/comment authors are the exact Actions bot. Workflow runs instead bind the
repository, configured name/path and independently fetched GitHub workflow ID:
their triggering actor may legitimately be a Human or GitHub Actions.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import yaml

REPOSITORY = "trung-via/AIOS-renew"
WAKE_MARKER = "[AIOS BRAIN WAKE]"
DELIVERY_POLICY = {
    "version": 1, "head_branch": "aios-brain-wake-bus-v1",
    "marker_path": ".ai/brain-wake-marker.json", "max_marker_bytes": 2048,
    "ledger_marker": "[AIOS WAKE DELIVERY LEDGER V1]", "max_ledger_bytes": 512,
}
TRUSTED_SOURCE = {"login": "github-actions[bot]", "id": 41898282, "type": "Bot"}
WORKFLOWS = {
    "publication": {"name": "AIOS auto-publish reviewed candidate", "path": ".github/workflows/aios-auto-publish.yml"},
    "primary": {"name": "AIOS self-hosted primary wakeup", "path": ".github/workflows/aios-self-hosted-wakeup.yml"},
    "repair": {"name": "AIOS self-hosted REPAIR wakeup", "path": ".github/workflows/aios-self-hosted-repair-wakeup.yml"},
    "remediation": {"name": "AIOS approved remediation wakeup", "path": ".github/workflows/aios-approved-remediation-wakeup.yml"},
}
SOURCE_WORKFLOWS = {
    "ingress": {"name": "AIOS Brain Issue Ingress", "path": ".github/workflows/aios-brain-ingress.yml"},
    "primary_carrier": {"name": "AIOS Brain PRIMARY Wakeup Carrier", "path": ".github/workflows/aios-brain-wakeup.yml"},
    "repair_carrier": {"name": "AIOS Brain REPAIR wakeup carrier", "path": ".github/workflows/aios-brain-repair-wakeup.yml"},
    "terminal": {"name": "AIOS terminal attention carrier", "path": ".github/workflows/aios-terminal-attention.yml"},
}
SOURCE_TITLES = {"ingress": "[AIOS BRAIN INGRESS]", "primary_carrier": "[AIOS BRAIN WAKEUP]", "repair_carrier": "[AIOS REPAIR WAKEUP]", "terminal": "[AIOS TERMINAL ATTENTION]"}
SOURCE_ENTRY_JOBS = {"ingress": "deliver", "primary_carrier": "admit-and-dispatch", "repair_carrier": "admit"}
POINTER_MAX_BYTES = 512
ARCHIVE_MAX_BYTES = 4096
ARTIFACT_PREFIX = "aios-wake-source-v1-attempt-"
NO_WAKE = {"projection": "NO_WAKE"}
MAX_EVENT_BYTES = 262_144
RUN = r"RUN-[A-Za-z0-9][A-Za-z0-9._-]{0,119}"
SHA = r"[0-9a-f]{40}"
CONCLUSIONS = {"success", "failure", "cancelled", "timed_out", "action_required", "neutral", "skipped", "stale", "startup_failure"}
FAILED_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure"}


class WakeBridgeError(ValueError):
    """Invalid or unauthorized transport input, with no permitted wake."""


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise WakeBridgeError("expected a string-keyed mapping")
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WakeBridgeError("duplicate mapping key")
        result[key] = value
    return result


class _PolicyLoader(yaml.SafeLoader):
    pass


def _yaml_mapping(loader: _PolicyLoader, node: yaml.MappingNode) -> dict[str, Any]:
    return _unique_pairs([(loader.construct_object(k), loader.construct_object(v)) for k, v in node.value])


_PolicyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def _read(path: str | Path, maximum: int) -> bytes:
    with Path(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if not raw or len(raw) > maximum:
        raise WakeBridgeError("file outside transport size bound")
    return raw


def _json(raw: str | bytes) -> Mapping[str, Any]:
    return _mapping(json.loads(raw, object_pairs_hook=_unique_pairs))


def read_event(path: str | Path) -> Mapping[str, Any]:
    return _json(_read(path, MAX_EVENT_BYTES).decode("utf-8", errors="strict"))


def _integer(value: Any) -> int:
    if type(value) is not int or not 0 < value <= 9_007_199_254_740_991:
        raise WakeBridgeError("invalid GitHub integer identity")
    return value


def _text(value: Any, maximum: int = 512) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise WakeBridgeError("invalid or oversized transport string")
    if any(ord(c) < 32 and c != "\n" for c in value):
        raise WakeBridgeError("control characters in transport string")
    return value


def _match(value: Any, pattern: str) -> str:
    text = _text(value)
    if not re.fullmatch(pattern, text):
        raise WakeBridgeError("invalid bounded selector grammar")
    return text


@dataclass(frozen=True)
class WakePolicy:
    repository: str
    wake_pr_number: int
    wake_marker: str
    max_comment_bytes: int
    head_branch: str = DELIVERY_POLICY["head_branch"]
    marker_path: str = DELIVERY_POLICY["marker_path"]


def load_policy(path: str | Path) -> WakePolicy:
    root = _mapping(yaml.load(_read(path, 8192).decode("utf-8"), Loader=_PolicyLoader))
    expected = {
        "format": "AIOS_BRAIN_WAKE_CARRIERS_POLICY", "version": 1,
        "repository": REPOSITORY, "wake_pr_number": 1200,
        "wake_marker": WAKE_MARKER, "max_comment_bytes": 2048,
        "wake_delivery": DELIVERY_POLICY,
        "trusted_source": TRUSTED_SOURCE, "workflow_sources": WORKFLOWS,
        "source_workflows": SOURCE_WORKFLOWS,
        "source_handoff": {"version": 1, "artifact_prefix": ARTIFACT_PREFIX, "filename": "source.json", "max_pointer_bytes": POINTER_MAX_BYTES, "max_archive_bytes": ARCHIVE_MAX_BYTES, "retention_days": 1},
    }
    # Serialized comparison also distinguishes booleans/floats from integer IDs.
    if json.dumps(root, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise WakeBridgeError("wake policy must exactly bind the reviewed carrier")
    return WakePolicy(REPOSITORY, 1200, WAKE_MARKER, 2048)


def _trusted(value: Any) -> None:
    source = _mapping(value)
    if any(source.get(k) != v or type(source.get(k)) is not type(v) for k, v in TRUSTED_SOURCE.items()):
        raise WakeBridgeError("source is not the trusted GitHub Actions identity")


def _fields(lines: list[str], allowed: set[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([a-z_]+): (.*)", line)
        if not match or match[1] not in allowed or match[1] in fields:
            raise WakeBridgeError("ambiguous or unknown receipt field")
        key, value = match.groups()
        fields[key] = _text(value, 3500 if key in {"reason", "detail"} else 512)
    return fields


def _ingress(body: str, issue: Mapping[str, Any]) -> str | None:
    sections = body.split("\n\n")
    if len(sections) > 2:
        raise WakeBridgeError("ambiguous ingress receipt sections")
    lines = sections[0].splitlines()
    if lines[0] != "AIOS BRAIN INGRESS RECEIPT":
        raise WakeBridgeError("unknown ingress receipt")
    if len(lines) > 1 and lines[1] == "status: FAIL":
        fields = _fields(lines[1:], {"status", "reason"})
        if set(fields) != {"status", "reason"} or not fields["reason"] or len(sections) != 1:
            raise WakeBridgeError("malformed failed ingress receipt")
        return "INGRESS_REJECTED"
    if len(lines) < 3 or lines[2] != "AIOS INGRESS PASS":
        raise WakeBridgeError("unknown ingress receipt grammar")
    carrier = _fields(lines[1:2], {"carrier"}).get("carrier")
    actor = _match(_mapping(issue.get("user")).get("login"), r"[A-Za-z0-9][A-Za-z0-9_\[\]-]{0,79}")
    if carrier != f"github-issue:{REPOSITORY}#{issue['number']}@{actor}":
        raise WakeBridgeError("ingress carrier selector does not match source")
    fields = _fields(lines[3:], {"operation", "status", "destination", "sha", "replayed", "detail"})
    if set(fields) != {"operation", "status", "destination", "sha", "replayed", "detail"}:
        raise WakeBridgeError("incomplete ingress receipt")
    operation = fields["operation"]
    if operation not in {"AUTHOR_TASK", "SUBMIT_REVIEW", "AUTHOR_REMEDIATION", "AUTHOR_REPAIR"}:
        raise WakeBridgeError("unknown ingress operation")
    if (fields["status"], fields["replayed"]) not in {("CANONICALIZED", "false"), ("IDEMPOTENT", "true")}:
        raise WakeBridgeError("ambiguous ingress outcome")
    _match(fields["sha"], SHA)
    _match(fields["destination"], r"(?:refs/heads/aios/[A-Za-z0-9._/-]{1,240}|\.ai/tasks/TASK-[A-Za-z0-9._-]{1,120}\.yaml)")
    family = None
    review = re.fullmatch(rf"canonicalized CHANGES_REQUIRED review decision for ({RUN})", fields["detail"])
    if review:
        if operation != "SUBMIT_REVIEW" or fields["status"] != "CANONICALIZED" or fields["destination"] != f"refs/heads/aios/review-decision/{review[1]}":
            raise WakeBridgeError("inconsistent CHANGES_REQUIRED receipt")
        family = "REVIEW_CHANGES_REQUIRED"
    if len(sections) == 2:
        dispatch = _fields(sections[1].splitlines(), {"publication_dispatch", "repair_dispatch", "dispatch_accepted", "publication_run_id", "repair_dispatch_id", "failed_run_id", "repair_sha", "detail", "reason"})
        publication = "publication_dispatch" in dispatch
        key = "publication_dispatch" if publication else "repair_dispatch"
        status = dispatch.get(key)
        expected = {key, "dispatch_accepted", "detail" if status == "ACCEPTED" else "reason"}
        expected |= {"publication_run_id"} if publication else {"repair_dispatch_id", "failed_run_id", "repair_sha"}
        if set(dispatch) != expected or status not in {"ACCEPTED", "REJECTED"} or dispatch["dispatch_accepted"] != str(status == "ACCEPTED").lower():
            raise WakeBridgeError("ambiguous dispatch receipt")
        if family or operation != ("SUBMIT_REVIEW" if publication else "AUTHOR_REPAIR"):
            raise WakeBridgeError("dispatch disagrees with ingress receipt")
        if publication:
            run = _match(dispatch["publication_run_id"], RUN)
            if fields["destination"] != f"refs/heads/aios/review-decision/{run}":
                raise WakeBridgeError("publication selector disagreement")
        else:
            run = _match(dispatch["failed_run_id"], RUN)
            sha = _match(dispatch["repair_sha"], SHA)
            if dispatch["repair_dispatch_id"] != f"repair-{run}-{sha}" or sha != fields["sha"]:
                raise WakeBridgeError("repair selector disagreement")
        if status == "REJECTED":
            family = "PUBLICATION_DISPATCH_REJECTED" if publication else "REPAIR_DISPATCH_REJECTED"
    return family


def _bounded_metadata(value: Any, depth: int = 0) -> None:
    if depth > 4:
        raise WakeBridgeError("unbounded operational metadata")
    if isinstance(value, Mapping):
        if len(value) > 16:
            raise WakeBridgeError("unbounded operational metadata fields")
        for key, child in value.items():
            _match(key, r"[a-z_]{1,40}")
            _bounded_metadata(child, depth + 1)
    elif isinstance(value, str):
        _text(value)
    elif value is not None and type(value) not in {int, bool}:
        raise WakeBridgeError("invalid operational metadata scalar")


def _carrier(body: str, repair: bool) -> str | None:
    lines = body.splitlines()
    expected_header = "AIOS REPAIR WAKEUP CARRIER RECEIPT" if repair else "AIOS BRAIN WAKEUP RECEIPT"
    if lines[0] != expected_header:
        raise WakeBridgeError("unknown wakeup receipt")
    metadata = None
    if lines[-1].startswith("AIOS_OPERATIONAL_RECEIPT_V2="):
        metadata = _json(lines.pop().split("=", 1)[1])
        _bounded_metadata(metadata)
    fields = _fields(lines[1:], {"status", "dispatch_accepted", "execution_outcome", "self_host_completed", "repair_run_outcome", "verification", "semantic_review", "publication", "reason", "detail", "dispatch_id", "repair_dispatch_id", "task_id", "task_revision", "task_blob_sha", "task_commit_sha", "failed_run_id", "repair_sha", "executor", "model", "reasoning_effort", "model_source", "effort_source", "actor"})
    status = fields.get("status")
    allowed = {"REJECTED", "ADMITTED", "SELF_HOST_COMPLETED" if repair else "DISPATCH_ACCEPTED"}
    if status not in allowed:
        raise WakeBridgeError("unsupported carrier outcome")
    expected_flag = "pending" if status == "ADMITTED" else str(status != "REJECTED").lower()
    flag = "self_host_completed" if repair and "self_host_completed" in fields else "dispatch_accepted"
    if "self_host_completed" in fields and "dispatch_accepted" in fields:
        raise WakeBridgeError("ambiguous carrier flags")
    if fields.get(flag) != expected_flag:
        raise WakeBridgeError("inconsistent carrier outcome")
    if not repair and fields.get("execution_outcome") != "not_observed":
        raise WakeBridgeError("carrier cannot assert execution truth")
    if repair and any(fields.get(k) not in {"not_observed", "not_asserted_by_carrier"} for k in ("repair_run_outcome", "verification", "semantic_review", "publication")):
        raise WakeBridgeError("repair carrier cannot assert lifecycle truth")
    if status == "REJECTED" and not (fields.get("reason") or fields.get("detail")):
        raise WakeBridgeError("rejection receipt missing reason")
    common = {"status", flag}
    common |= {"repair_run_outcome", "verification", "semantic_review", "publication"} if repair else {"execution_outcome"}
    profile = {"executor", "model", "reasoning_effort", "model_source", "effort_source"}
    selectors = {"repair_dispatch_id", "failed_run_id", "repair_sha"} if repair else {"dispatch_id", "task_id", "task_revision", "task_blob_sha", "task_commit_sha"}
    if status == "REJECTED" and (not repair or "reason" in fields):
        expected = common | {"reason"}
    else:
        expected = common | profile | selectors
        if status == "ADMITTED":
            if repair:
                expected.add("actor")
        else:
            expected.add("detail")
    if set(fields) != expected:
        raise WakeBridgeError("unknown or incomplete carrier grammar")
    patterns = {
        "task_id": r"TASK-[A-Za-z0-9][A-Za-z0-9._-]{0,119}",
        "task_revision": r"[1-9][0-9]{0,8}",
        "task_blob_sha": SHA, "task_commit_sha": SHA,
        "failed_run_id": RUN, "repair_sha": SHA,
        "dispatch_id": r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}",
        "repair_dispatch_id": r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}",
    }
    for key in selectors & fields.keys():
        _match(fields[key], patterns[key])
    if metadata is not None:
        if set(metadata) != {"format", "version", "kind", "family", "delivery", "boundary", "selectors"} | ({"cause"} if repair and status == "REJECTED" else set()):
            raise WakeBridgeError("unknown operational receipt fields")
        if metadata.get("format") != "AIOS_OPERATIONAL_RECEIPT" or type(metadata.get("version")) is not int or metadata["version"] != 2 or metadata.get("kind") != "OPERATIONAL_RECEIPT" or metadata.get("family") != ("REPAIR" if repair else "PRIMARY"):
            raise WakeBridgeError("unknown operational receipt identity")
        boundary = "SELF_HOST_COMPLETED" if repair else "DISPATCH_REQUEST_ACCEPTED"
        if status == "REJECTED":
            boundary = "OPERATIONAL_FAILED" if repair else "CARRIER_ADMITTED"
        if metadata.get("boundary") != boundary:
            raise WakeBridgeError("contradictory operational receipt")
        delivery = _mapping(metadata.get("delivery"))
        delivery_key = "repair_dispatch_id" if repair else "dispatch_id"
        if set(delivery) != {"kind", "id"} or delivery.get("kind") != delivery_key:
            raise WakeBridgeError("unknown operational delivery identity")
        _match(delivery.get("id"), r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}")
        if delivery_key in fields and delivery["id"] != fields[delivery_key]:
            raise WakeBridgeError("carrier delivery selector disagreement")
        metadata_selectors = _mapping(metadata.get("selectors"))
        allowed_selectors = (selectors - {delivery_key}) | profile
        if set(metadata_selectors) - allowed_selectors or not (selectors - {delivery_key}) <= set(metadata_selectors):
            raise WakeBridgeError("unknown or missing operational selector")
        for key, value in metadata_selectors.items():
            if key == "task_revision":
                _integer(value)
                _match(str(value), patterns[key])
            elif key in patterns:
                _match(value, patterns[key])
            else:
                _text(value)
            if key in fields and str(value) != fields[key]:
                raise WakeBridgeError("carrier selector disagreement")
        if repair and status == "REJECTED" and metadata.get("cause") != {"authority": "CARRIER", "phase": "DOWNSTREAM_WORKFLOW", "reason_code": "DOWNSTREAM_WORKFLOW_FAILED"}:
            raise WakeBridgeError("unknown operational failure boundary")
    elif status == "REJECTED" and "detail" in fields:
        raise WakeBridgeError("downstream rejection missing operational receipt")
    return ("REPAIR_DISPATCH_REJECTED" if repair else "PRIMARY_DISPATCH_REJECTED") if status == "REJECTED" else None


def _wake(policy: WakePolicy, source: str, identity: int | str, family: str, selectors: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(f"v1\n{policy.repository}\n{source}\n{identity}\n{family}".encode("ascii")).hexdigest()
    event_id = f"github-v1-{digest}"
    payload = {"version": 1, "event_id": event_id, "attention_family": family, "repository": policy.repository, "selectors": selectors, "fresh_brain_sync_required": True}
    body = policy.wake_marker + "\n" + yaml.safe_dump(payload, sort_keys=False)
    if len(body.encode("utf-8")) > policy.max_comment_bytes:
        raise WakeBridgeError("wake comment exceeds policy bound")
    return {"projection": "WAKE", **payload, "wake_pr_number": policy.wake_pr_number, "max_comment_bytes": policy.max_comment_bytes, "body": body}


def workflow_identity(event: Mapping[str, Any], workflow: Mapping[str, Any], policy: WakePolicy) -> str:
    """Admit exact repository/workflow identity before reading any handoff."""
    event, workflow = _mapping(event), _mapping(workflow)
    if event.get("action") != "completed" or _mapping(event.get("repository")).get("full_name") != policy.repository:
        raise WakeBridgeError("wrong completed event repository")
    run = _mapping(event.get("workflow_run"))
    for key in ("id", "run_attempt", "workflow_id"):
        _integer(run.get(key))
    _match(run.get("head_sha"), SHA)
    if _integer(workflow.get("id")) != run["workflow_id"]:
        raise WakeBridgeError("workflow ID disagreement")
    matches = [key for key, item in (WORKFLOWS | SOURCE_WORKFLOWS).items()
               if workflow.get("name") == item["name"] and workflow.get("path") == item["path"] and run.get("name") == item["name"]]
    if len(matches) != 1 or run.get("status") != "completed" or _mapping(run.get("head_repository")).get("full_name") != policy.repository:
        raise WakeBridgeError("untrusted workflow identity")
    key = matches[0]
    branch, trigger = _text(run.get("head_branch"), 240), run.get("event")
    allowed = branch == "main" and trigger == ("issues" if key in SOURCE_WORKFLOWS and key != "terminal" else "workflow_dispatch")
    if key == "publication" and trigger == "push":
        allowed = bool(re.fullmatch(rf"aios/review-decision/{RUN}", branch))
    if key == "terminal" and trigger == "push":
        allowed = bool(re.fullmatch(rf"aios/terminal-attention/(RESULT|FAILURE)/{RUN}/{SHA}", branch))
    if not allowed or not isinstance(run.get("conclusion"), str) or run["conclusion"] not in CONCLUSIONS:
        raise WakeBridgeError("unauthorized workflow source ref/event or conclusion")
    return key


def read_pointer(raw: bytes) -> Mapping[str, Any]:
    if not raw or len(raw) > POINTER_MAX_BYTES:
        raise WakeBridgeError("pointer outside size bound")
    pointer = _json(raw.decode("utf-8", errors="strict"))
    kind = pointer.get("source_kind")
    expected = {"version", "source_kind", "issue_id", "issue_number"}
    if kind == "comment":
        expected.add("comment_id")
    elif kind != "issue":
        raise WakeBridgeError("unknown pointer kind")
    if set(pointer) != expected or type(pointer.get("version")) is not int or pointer["version"] != 1:
        raise WakeBridgeError("unknown pointer schema")
    for field in expected - {"version", "source_kind"}:
        _integer(pointer[field])
    return pointer


def _source_url(value: Any, suffix: str) -> None:
    if value != f"https://api.github.com/repos/{REPOSITORY}/{suffix}":
        raise WakeBridgeError("source belongs to another repository or parent")


def project_source(*, key: str, pointer: Mapping[str, Any], issue: Mapping[str, Any], comment: Mapping[str, Any] | None, policy: WakePolicy) -> dict[str, Any]:
    """Classify independently reacquired GitHub objects, never pointer semantics."""
    pointer = read_pointer(json.dumps(dict(pointer)).encode())
    number, identity = _integer(issue.get("number")), _integer(issue.get("id"))
    if (number, identity) != (pointer["issue_number"], pointer["issue_id"]):
        raise WakeBridgeError("substituted Issue identity")
    _source_url(issue.get("url"), f"issues/{number}")
    if number == policy.wake_pr_number or "pull_request" in issue:
        return dict(NO_WAKE)
    if issue.get("title") != SOURCE_TITLES[key]:
        raise WakeBridgeError("source title does not match workflow")
    if key == "terminal":
        if pointer["source_kind"] != "issue" or comment is not None:
            raise WakeBridgeError("wrong terminal source kind")
        _trusted(issue.get("user"))
        body = _text(issue.get("body"), 512)
        if not re.fullmatch(rf"format: AIOS_TERMINAL_ATTENTION\nversion: 1\nrun_id: {RUN}\nterminal_kind: (RESULT|FAILURE)\nartifact_sha: {SHA}\n", body):
            raise WakeBridgeError("non-canonical terminal attention grammar")
        return _wake(policy, "issues", identity, "TERMINAL_ATTENTION", {"event_family": "issues.opened", "issue_number": number, "issue_id": identity})
    if pointer["source_kind"] != "comment":
        raise WakeBridgeError("wrong receipt source kind")
    comment = _mapping(comment)
    identity = _integer(comment.get("id"))
    if identity != pointer["comment_id"]:
        raise WakeBridgeError("substituted comment identity")
    _source_url(comment.get("url"), f"issues/comments/{identity}")
    _source_url(comment.get("issue_url"), f"issues/{number}")
    _trusted(comment.get("user"))
    body = _text(comment.get("body"), 14_000).removesuffix("\n")
    if not body or len(body) > 3500 or "[truncated]" in body:
        raise WakeBridgeError("receipt outside grammar bound")
    family = _ingress(body, issue) if key == "ingress" else _carrier(body, key == "repair_carrier")
    if family is None:
        return dict(NO_WAKE)
    return _wake(policy, "issue_comment", identity, family, {"event_family": "issue_comment.created", "issue_number": number, "issue_id": issue["id"], "comment_id": identity})


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(_text(value, 40).replace("Z", "+00:00"))


def reconcile_attempt(run: Mapping[str, Any], attempt: Mapping[str, Any]) -> None:
    for key in ("id", "workflow_id", "run_attempt", "name", "head_sha", "head_branch", "event", "status", "conclusion"):
        if type(run.get(key)) is not type(attempt.get(key)) or run.get(key) != attempt.get(key):
            raise WakeBridgeError("substituted or stale workflow attempt")
    if _mapping(attempt.get("repository")).get("full_name") != REPOSITORY or _mapping(attempt.get("head_repository")).get("full_name") != REPOSITORY:
        raise WakeBridgeError("attempt repository disagreement")


def _source_entry_skipped(key: str, run: Mapping[str, Any], api: Any) -> bool:
    """A title-gated entry job that never executed owes no source pointer."""
    data = _mapping(api.get(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"))
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not 0 < len(jobs) <= 100 or type(data.get("total_count")) is not int or data["total_count"] != len(jobs):
        raise WakeBridgeError("missing or incomplete source jobs")
    matches = [job for job in map(_mapping, jobs) if job.get("name") == SOURCE_ENTRY_JOBS[key]]
    if len(matches) != 1:
        raise WakeBridgeError("missing or ambiguous source entry job")
    job = matches[0]
    _integer(job.get("id"))
    if _integer(job.get("run_id")) != run["id"] or job.get("head_sha") != run["head_sha"]:
        raise WakeBridgeError("source job provenance disagreement")
    _source_url(job.get("run_url"), f"actions/runs/{run['id']}")
    if job.get("status") != "completed" or not isinstance(job.get("conclusion"), str) or job["conclusion"] not in CONCLUSIONS:
        raise WakeBridgeError("incomplete source entry job")
    if job["conclusion"] != "skipped":
        return False
    if job.get("steps") != []:
        raise WakeBridgeError("skipped source entry job contains steps")
    # Cancellation/failure can skip a target before it starts; those runs still
    # owe an operational failure rather than being treated as quiet siblings.
    return run["conclusion"] in {"success", "skipped"}


def consume_handoff(*, event: Mapping[str, Any], workflow: Mapping[str, Any], policy: WakePolicy, api: Any) -> dict[str, Any]:
    key = workflow_identity(event, workflow, policy)
    run = event["workflow_run"]
    if key not in SOURCE_WORKFLOWS:
        if key != "publication" and run["conclusion"] not in FAILED_CONCLUSIONS:
            return dict(NO_WAKE)
        family = "PUBLICATION_WORKFLOW_COMPLETED" if key == "publication" else "PRE_AIOS_OPERATIONAL_FAILURE"
        return _wake(policy, "workflow_run", f"{run['id']}:{run['run_attempt']}", family,
                     {"event_family": "workflow_run.completed", "workflow_run_id": run["id"], "run_attempt": run["run_attempt"], "workflow_id": run["workflow_id"], "conclusion": run["conclusion"]})
    try:
        attempt = _mapping(api.get(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}"))
        reconcile_attempt(run, attempt)
        # Issue triggers are repository-wide; only the title-gated entry job
        # distinguishes sibling bookkeeping from a source that owes a handoff.
        if key in SOURCE_ENTRY_JOBS and _source_entry_skipped(key, run, api):
            return dict(NO_WAKE)
        start, end = _timestamp(attempt.get("run_started_at")), _timestamp(attempt.get("updated_at"))
        if end < start:
            raise WakeBridgeError("invalid attempt interval")
        artifacts = api.artifacts(run["id"])
        expected = ARTIFACT_PREFIX + str(run["run_attempt"])
        matches = [a for a in map(_mapping, artifacts) if a.get("name") == expected]
        if len(matches) != 1:
            raise WakeBridgeError("missing or ambiguous source handoff")
        artifact = _mapping(matches[0])
        if artifact.get("expired") is not False or not 0 < _integer(artifact.get("size_in_bytes")) <= ARCHIVE_MAX_BYTES:
            raise WakeBridgeError("expired or oversized source handoff")
        artifact_run = _mapping(artifact.get("workflow_run"))
        if _integer(artifact_run.get("id")) != run["id"] or _match(artifact_run.get("head_sha"), SHA) != run["head_sha"]:
            raise WakeBridgeError("artifact run provenance disagreement")
        if not start <= _timestamp(artifact.get("created_at")) <= end:
            raise WakeBridgeError("stale-attempt artifact")
        archive = api.archive(_integer(artifact.get("id")))
        if not archive or len(archive) > ARCHIVE_MAX_BYTES:
            raise WakeBridgeError("oversized artifact download")
        # Never extract attacker-selected paths into the checkout (or elsewhere).
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            entries = bundle.infolist()
            if len(entries) != 1 or entries[0].filename != "source.json" or entries[0].file_size > POINTER_MAX_BYTES or entries[0].is_dir():
                raise WakeBridgeError("unexpected artifact contents")
            pointer = read_pointer(bundle.read(entries[0]))
        issue = _mapping(api.get(f"issues/{pointer['issue_number']}"))
        comment = None
        if pointer["source_kind"] == "comment":
            comment = _mapping(api.get(f"issues/comments/{pointer['comment_id']}"))
            if not start <= _timestamp(comment.get("created_at")) <= end:
                raise WakeBridgeError("receipt was not produced by this attempt")
            if comment.get("updated_at") != comment.get("created_at"):
                raise WakeBridgeError("source receipt was edited after creation")
        return project_source(key=key, pointer=pointer, issue=issue, comment=comment, policy=policy)
    except (WakeBridgeError, OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError, zipfile.BadZipFile, RuntimeError):
        return _wake(policy, "source_handoff", f"{run['id']}:{run['run_attempt']}", "WAKE_SOURCE_HANDOFF_FAILURE",
                     {"event_family": "workflow_run.completed", "workflow_run_id": run["id"], "run_attempt": run["run_attempt"], "workflow_id": run["workflow_id"]})


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def _marker_payload(value: Any) -> Mapping[str, Any]:
    """Exact r2 selectors and event identity, with no semantic extension fields."""
    payload = _mapping(value)
    if set(payload) != {"version", "event_id", "attention_family", "repository", "selectors", "fresh_brain_sync_required"}:
        raise WakeBridgeError("unknown marker fields")
    if type(payload["version"]) is not int or payload["version"] != 1 or payload["repository"] != REPOSITORY or payload["fresh_brain_sync_required"] is not True:
        raise WakeBridgeError("invalid marker identity")
    selectors = _mapping(payload["selectors"])
    family = payload["attention_family"]
    event_family = selectors.get("event_family")
    if event_family in {"issues.opened", "issue_comment.created"}:
        expected = {"event_family", "issue_id", "issue_number"}
        source = "issues"
        families = {"TERMINAL_ATTENTION"}
        identity = _integer(selectors.get("issue_id"))
        _integer(selectors.get("issue_number"))
        if selectors["issue_number"] == 1200:
            raise WakeBridgeError("Wake Bus cannot be its own source")
        if event_family == "issue_comment.created":
            expected.add("comment_id")
            source, identity = "issue_comment", _integer(selectors.get("comment_id"))
            families = {"INGRESS_REJECTED", "REVIEW_CHANGES_REQUIRED", "PRIMARY_DISPATCH_REJECTED", "REPAIR_DISPATCH_REJECTED", "PUBLICATION_DISPATCH_REJECTED"}
    elif event_family == "workflow_run.completed":
        expected = {"event_family", "workflow_run_id", "run_attempt", "workflow_id"}
        identity = f"{_integer(selectors.get('workflow_run_id'))}:{_integer(selectors.get('run_attempt'))}"
        _integer(selectors.get("workflow_id"))
        source = "source_handoff" if family == "WAKE_SOURCE_HANDOFF_FAILURE" else "workflow_run"
        families = {"WAKE_SOURCE_HANDOFF_FAILURE", "PUBLICATION_WORKFLOW_COMPLETED", "PRE_AIOS_OPERATIONAL_FAILURE"}
        if source == "workflow_run":
            expected.add("conclusion")
            conclusion = selectors.get("conclusion")
            if not isinstance(conclusion, str) or conclusion not in CONCLUSIONS or (family == "PRE_AIOS_OPERATIONAL_FAILURE" and conclusion not in FAILED_CONCLUSIONS):
                raise WakeBridgeError("invalid workflow conclusion selector")
    else:
        raise WakeBridgeError("invalid marker source selectors")
    if set(selectors) != expected or not isinstance(family, str) or family not in families:
        raise WakeBridgeError("unknown marker selectors or attention family")
    digest = hashlib.sha256(f"v1\n{REPOSITORY}\n{source}\n{identity}\n{family}".encode("ascii")).hexdigest()
    if payload["event_id"] != f"github-v1-{digest}":
        raise WakeBridgeError("marker event identity disagreement")
    return payload


def marker_content(wake: Mapping[str, Any], policy: WakePolicy) -> bytes:
    if set(wake) != {"projection", "version", "event_id", "attention_family", "repository", "selectors", "fresh_brain_sync_required", "wake_pr_number", "max_comment_bytes", "body"}:
        raise WakeBridgeError("invalid bounded wake projection fields")
    if wake["projection"] != "WAKE" or type(wake["wake_pr_number"]) is not int or wake["wake_pr_number"] != 1200 or type(wake["max_comment_bytes"]) is not int or wake["max_comment_bytes"] != 2048:
        raise WakeBridgeError("invalid bounded wake projection")
    payload = _marker_payload({key: wake[key] for key in ("version", "event_id", "attention_family", "repository", "selectors", "fresh_brain_sync_required")})
    body = _text(wake["body"], policy.max_comment_bytes)
    if not body.startswith(WAKE_MARKER + "\n"):
        raise WakeBridgeError("invalid r2 wake body")
    parsed = yaml.load(body[len(WAKE_MARKER) + 1:], Loader=_PolicyLoader)
    if _canonical(_mapping(parsed)) != _canonical(payload):
        raise WakeBridgeError("projection body and selectors disagree")
    raw = _canonical(payload)
    if len(raw) > DELIVERY_POLICY["max_marker_bytes"]:
        raise WakeBridgeError("marker exceeds transport bound")
    return raw


def _live_head(api: Any, policy: WakePolicy, previous: Mapping[str, Any] | None = None, *, same_sha: bool = False) -> Mapping[str, Any]:
    pr = _mapping(api.get("pulls/1200"))
    if type(pr.get("number")) is not int or pr["number"] != 1200 or pr.get("state") != "open" or pr.get("merged") is not False:
        raise WakeBridgeError("Wake Bus PR is closed or substituted")
    _source_url(pr.get("url"), "pulls/1200")
    _integer(pr.get("id"))
    head, base = _mapping(pr.get("head")), _mapping(pr.get("base"))
    head_repo, base_repo = _mapping(head.get("repo")), _mapping(base.get("repo"))
    for repo in (head_repo, base_repo):
        if repo.get("full_name") != REPOSITORY or repo.get("fork") is not False:
            raise WakeBridgeError("forked or substituted Wake Bus repository")
        _integer(repo.get("id"))
    if head_repo["id"] != base_repo["id"] or head.get("ref") != policy.head_branch or head.get("label") != f"trung-via:{policy.head_branch}" or base.get("ref") != "main":
        raise WakeBridgeError("substituted Wake Bus head identity")
    sha = _match(head.get("sha"), SHA)
    ref = _mapping(api.get(f"git/ref/heads/{policy.head_branch}"))
    obj = _mapping(ref.get("object"))
    if ref.get("ref") != f"refs/heads/{policy.head_branch}" or obj.get("type") != "commit" or obj.get("sha") != sha:
        raise WakeBridgeError("live PR and branch head disagree")
    identity = {"pr_id": pr["id"], "repo_id": head_repo["id"], "sha": sha}
    if previous is not None and (identity["pr_id"] != previous["pr_id"] or identity["repo_id"] != previous["repo_id"] or (same_sha and sha != previous["sha"])):
        raise WakeBridgeError("Wake Bus identity changed during delivery")
    return identity


def _ledger_body(event_id: str, digest: str, state: str) -> str:
    body = DELIVERY_POLICY["ledger_marker"] + "\n" + _canonical({"version": 1, "event_id": event_id, "marker_sha256": digest, "state": state}).decode("ascii")
    if len(body.encode("ascii")) > DELIVERY_POLICY["max_ledger_bytes"]:
        raise WakeBridgeError("ledger exceeds transport bound")
    return body


def _ledger_fields(value: Any) -> Mapping[str, Any]:
    body = _text(value, DELIVERY_POLICY["max_ledger_bytes"])
    prefix = DELIVERY_POLICY["ledger_marker"] + "\n"
    if not body.startswith(prefix):
        raise WakeBridgeError("malformed delivery ledger")
    entry = _json(body[len(prefix):])
    if set(entry) != {"version", "event_id", "marker_sha256", "state"} or type(entry["version"]) is not int or entry["version"] != 1 or entry["state"] not in ("PENDING", "EMITTED"):
        raise WakeBridgeError("unknown delivery ledger state")
    _match(entry["event_id"], r"github-v1-[0-9a-f]{64}")
    _match(entry["marker_sha256"], r"[0-9a-f]{64}")
    if body != _ledger_body(entry["event_id"], entry["marker_sha256"], entry["state"]):
        raise WakeBridgeError("non-exact delivery ledger")
    return entry


def _ledger_comment(comment: Any) -> tuple[int, Mapping[str, Any]]:
    comment = _mapping(comment)
    _trusted(comment.get("user"))
    comment_id = _integer(comment.get("id"))
    _source_url(comment.get("url"), f"issues/comments/{comment_id}")
    _source_url(comment.get("issue_url"), "issues/1200")
    return comment_id, _ledger_fields(comment.get("body"))


def _ledgers(api: Any) -> dict[str, tuple[int, Mapping[str, Any]]]:
    entries: dict[str, tuple[int, Mapping[str, Any]]] = {}
    ids: set[int] = set()
    for comment in api.comments():
        body = _text(_mapping(comment).get("body") or "", MAX_EVENT_BYTES)
        # Quoted or malformed ledger markers are ambiguous, never completion.
        if DELIVERY_POLICY["ledger_marker"] not in body:
            continue
        comment_id, entry = _ledger_comment(comment)
        if entry["event_id"] in entries or comment_id in ids:
            raise WakeBridgeError("duplicated delivery ledger")
        ids.add(comment_id)
        entries[entry["event_id"]] = (comment_id, entry)
    if sum(entry["state"] == "PENDING" for _, entry in entries.values()) > 1:
        raise WakeBridgeError("ambiguous pending delivery")
    return entries


def _current_marker(api: Any, policy: WakePolicy, head: Mapping[str, Any]) -> tuple[bytes | None, str | None]:
    try:
        item = _mapping(api.get(f"contents/{policy.marker_path}?ref={head['sha']}"))
    except HTTPError as exc:
        if exc.code != 404:
            raise
        return None, None
    if item.get("type") != "file" or item.get("path") != policy.marker_path or item.get("encoding") != "base64" or type(item.get("size")) is not int or not 0 < item["size"] <= DELIVERY_POLICY["max_marker_bytes"]:
        raise WakeBridgeError("invalid bounded marker file")
    encoded = _text(item.get("content"), 4096)
    raw = base64.b64decode(encoded.replace("\n", ""), validate=True)
    if len(raw) != item["size"]:
        raise WakeBridgeError("marker size disagreement")
    payload = _marker_payload(_json(raw))
    if raw != _canonical(payload):
        raise WakeBridgeError("non-exact marker content")
    sha = _match(item.get("sha"), SHA)
    if sha != hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest():
        raise WakeBridgeError("marker blob identity disagreement")
    return raw, sha


def deliver_wake(*, wake: Mapping[str, Any], policy: WakePolicy, api: Any) -> str:
    """Operational delivery only; callers must hold the global workflow boundary.

    Commit-update Work wake/ACK remains a separate post-publication conformance
    gate. Failure there returns to Human/Brain architecture review; no credential
    or other transport fallback is authorized.
    """
    if wake == NO_WAKE:
        return "NO_WAKE"
    if policy != WakePolicy(REPOSITORY, 1200, WAKE_MARKER, 2048):
        raise WakeBridgeError("unauthorized delivery policy")
    raw = marker_content(wake, policy)
    event_id, digest = wake["event_id"], hashlib.sha256(raw).hexdigest()
    head = _live_head(api, policy)
    entries = _ledgers(api)
    current = entries.get(event_id)
    if current and current[1]["marker_sha256"] != digest:
        raise WakeBridgeError("replayed event marker mismatch")
    if any(key != event_id and entry["state"] == "PENDING" for key, (_, entry) in entries.items()):
        raise WakeBridgeError("unresolved delivery blocks unrelated event")
    if current and current[1]["state"] == "EMITTED":
        return "NOOP"
    old_raw, blob_sha = _current_marker(api, policy, head)
    if old_raw is not None:
        old_id = _json(old_raw)["event_id"]
        old_entry = entries.get(old_id)
        if old_entry is None or old_entry[1]["marker_sha256"] != hashlib.sha256(old_raw).hexdigest():
            raise WakeBridgeError("marker has no exact delivery ledger")
        if old_id == event_id and old_raw != raw:
            raise WakeBridgeError("current event marker mismatch")
        if old_id != event_id and old_entry[1]["state"] != "EMITTED":
            raise WakeBridgeError("unresolved current marker")
    elif any(entry["state"] == "EMITTED" for _, entry in entries.values()):
        raise WakeBridgeError("previously emitted marker is missing")
    if current is None:
        _live_head(api, policy, head, same_sha=True)
        created = api.write("POST", "issues/1200/comments", {"body": _ledger_body(event_id, digest, "PENDING")})
        comment_id, entry = _ledger_comment(created)
        if entry != {"version": 1, "event_id": event_id, "marker_sha256": digest, "state": "PENDING"}:
            raise WakeBridgeError("created ledger disagreement")
        current = (comment_id, entry)
    # Reacquire history before writing, so ambiguous or interrupted comment writes
    # never become a fabricated completion. No automatic transport retry occurs.
    entries = _ledgers(api)
    if entries.get(event_id) != current or any(key != event_id and entry["state"] == "PENDING" for key, (_, entry) in entries.items()):
        raise WakeBridgeError("ledger changed during delivery")
    if old_raw != raw:
        update = {"branch": policy.head_branch, "message": f"AIOS Wake Bus marker {event_id}", "content": base64.b64encode(raw).decode("ascii")}
        if blob_sha is not None:
            update["sha"] = blob_sha
        _live_head(api, policy, head, same_sha=True)
        response = _mapping(api.write("PUT", f"contents/{policy.marker_path}", update))
        commit_sha = _match(_mapping(response.get("commit")).get("sha"), SHA)
        written_head = _live_head(api, policy, head)
        if written_head["sha"] != commit_sha:
            raise WakeBridgeError("marker commit and live PR head disagree")
        head = written_head
    # This also recovers a write that succeeded before the PENDING -> EMITTED
    # transition failed: exact content completes the SAME ledger without a commit.
    head = _live_head(api, policy, head, same_sha=True)
    confirmed, _ = _current_marker(api, policy, head)
    if confirmed != raw:
        raise WakeBridgeError("written marker mismatch")
    if _ledgers(api) != entries:
        raise WakeBridgeError("ledger changed before completion")
    _live_head(api, policy, head, same_sha=True)
    emitted = api.write("PATCH", f"issues/comments/{current[0]}", {"body": _ledger_body(event_id, digest, "EMITTED")})
    if _ledger_comment(emitted) != (current[0], {**current[1], "state": "EMITTED"}):
        raise WakeBridgeError("completed ledger disagreement")
    return "EMITTED"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubAPI:
    """Bounded reads plus only the fixed operational ledger/marker writes."""
    def __init__(self, token: str, temp_root: str):
        self.token, self.temp_root = token, Path(temp_root).resolve()
        workspace = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
        if self.temp_root == workspace or workspace in self.temp_root.parents:
            raise WakeBridgeError("handoff download must be outside workspace")

    def request(self, path: str) -> Request:
        return Request(f"https://api.github.com/repos/{REPOSITORY}/{path}", headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})

    def get(self, path: str) -> Mapping[str, Any]:
        with urlopen(self.request(path), timeout=30) as response:
            raw = response.read(MAX_EVENT_BYTES + 1)
        if len(raw) > MAX_EVENT_BYTES:
            raise WakeBridgeError("oversized GitHub response")
        return _json(raw)

    def comments(self) -> list[Mapping[str, Any]]:
        comments = []
        for page in range(1, 101):
            with build_opener(_NoRedirect).open(self.request(f"issues/1200/comments?per_page=100&page={page}"), timeout=30) as response:
                raw = response.read(MAX_EVENT_BYTES + 1)
            if len(raw) > MAX_EVENT_BYTES:
                raise WakeBridgeError("oversized comment history response")
            batch = json.loads(raw, object_pairs_hook=_unique_pairs)
            if not isinstance(batch, list) or len(batch) > 100:
                raise WakeBridgeError("malformed comment history")
            comments.extend(_mapping(item) for item in batch)
            if len(batch) < 100:
                return comments
        raise WakeBridgeError("delivery ledger history exceeds bound")

    def write(self, method: str, path: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
        if method == "PUT" and path == f"contents/{DELIVERY_POLICY['marker_path']}":
            if set(value) not in ({"branch", "message", "content"}, {"branch", "message", "content", "sha"}) or value.get("branch") != DELIVERY_POLICY["head_branch"]:
                raise WakeBridgeError("unauthorized marker write")
            raw = base64.b64decode(_text(value["content"], 4096), validate=True)
            payload = _marker_payload(_json(raw))
            if len(raw) > DELIVERY_POLICY["max_marker_bytes"] or raw != _canonical(payload) or value["message"] != f"AIOS Wake Bus marker {payload['event_id']}":
                raise WakeBridgeError("unauthorized marker write content")
            if "sha" in value:
                _match(value["sha"], SHA)
        elif (method == "POST" and path == "issues/1200/comments") or (method == "PATCH" and re.fullmatch(r"issues/comments/[1-9][0-9]{0,15}", path)):
            if set(value) != {"body"} or not _text(value["body"], DELIVERY_POLICY["max_ledger_bytes"]).startswith(DELIVERY_POLICY["ledger_marker"] + "\n"):
                raise WakeBridgeError("unauthorized ledger write")
            # Reuse exact ledger grammar before crossing the HTTP write boundary.
            _ledger_fields(value["body"])
        else:
            raise WakeBridgeError("unauthorized delivery write target")
        request = self.request(path)
        request.method, request.data = method, _canonical(value)
        request.add_header("Content-Type", "application/json")
        with build_opener(_NoRedirect).open(request, timeout=30) as response:
            raw = response.read(MAX_EVENT_BYTES + 1)
        if len(raw) > MAX_EVENT_BYTES:
            raise WakeBridgeError("oversized delivery response")
        return _json(raw)

    def artifacts(self, run_id: int) -> list[Mapping[str, Any]]:
        artifacts = []
        for page in range(1, 11):
            data = self.get(f"actions/runs/{run_id}/artifacts?per_page=100&page={page}")
            batch = data.get("artifacts")
            if not isinstance(batch, list):
                raise WakeBridgeError("malformed artifacts response")
            artifacts.extend(_mapping(item) for item in batch)
            if len(batch) < 100:
                return artifacts
        raise WakeBridgeError("artifact listing exceeds bound")

    def archive(self, artifact_id: int) -> bytes:
        try:
            response = build_opener(_NoRedirect).open(self.request(f"actions/artifacts/{artifact_id}/zip"), timeout=30)
        except HTTPError as exc:
            if exc.code != 302:
                raise
            location = exc.headers.get("Location", "")
            parsed = urlparse(location)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise WakeBridgeError("invalid artifact download redirect")
            # The signed download URL is used without forwarding the GitHub token.
            response = urlopen(Request(location), timeout=30)
        with response, tempfile.TemporaryFile(dir=self.temp_root) as archive:
            raw = response.read(ARCHIVE_MAX_BYTES + 1)
            if len(raw) > ARCHIVE_MAX_BYTES:
                raise WakeBridgeError("archive exceeds download bound")
            archive.write(raw)
            archive.seek(0)
            return archive.read()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reacquire one completed workflow source and project a bounded bell")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--policy", default=".ai/brain-wake-carriers.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--projection")
    args = parser.parse_args(argv)
    output = Path(args.output)
    initial = {"delivery": "INCOMPLETE"} if args.deliver else NO_WAKE
    output.write_text(json.dumps(initial) + "\n", encoding="utf-8")
    try:
        policy = load_policy(args.policy)
        if args.deliver:
            if args.repository != policy.repository:
                raise WakeBridgeError("unauthorized delivery repository")
            wake = _json(_read(args.projection, 8192))
            api = GitHubAPI(os.environ["GH_TOKEN"], os.environ["RUNNER_TEMP"])
            delivery = deliver_wake(wake=wake, policy=policy, api=api)
            output.write_text(json.dumps({"delivery": delivery}) + "\n", encoding="utf-8")
            return 0
        if args.event_name != "workflow_run" or args.repository != policy.repository:
            raise WakeBridgeError("only exact completed workflow delivery is admitted")
        event = read_event(args.event)
        run = _mapping(event.get("workflow_run"))
        api = GitHubAPI(os.environ["GH_TOKEN"], os.environ["RUNNER_TEMP"])
        workflow = api.get(f"actions/workflows/{_integer(run.get('workflow_id'))}")
        result = consume_handoff(event=event, workflow=workflow, policy=policy, api=api)
        output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    except (WakeBridgeError, OSError, UnicodeError, ValueError, yaml.YAMLError, TypeError, KeyError, RecursionError) as exc:
        phase = "delivery" if args.deliver else "projection"
        detail = f": {str(exc)[:160]}" if args.deliver and isinstance(exc, WakeBridgeError) else ""
        print(f"wake {phase} rejected: {type(exc).__name__}{detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


