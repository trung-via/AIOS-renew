"""Deterministic GitHub wake bells; source metadata is never lifecycle truth.

Issue/comment authors are the exact Actions bot. Workflow runs instead bind the
repository, configured name/path and independently fetched GitHub workflow ID:
their triggering actor may legitimately be a Human or GitHub Actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPOSITORY = "trung-via/AIOS-renew"
WAKE_MARKER = "[AIOS BRAIN WAKE]"
TRUSTED_SOURCE = {"login": "github-actions[bot]", "id": 41898282, "type": "Bot"}
WORKFLOWS = {
    "publication": {"name": "AIOS auto-publish reviewed candidate", "path": ".github/workflows/aios-auto-publish.yml"},
    "primary": {"name": "AIOS self-hosted primary wakeup", "path": ".github/workflows/aios-self-hosted-wakeup.yml"},
    "repair": {"name": "AIOS self-hosted REPAIR wakeup", "path": ".github/workflows/aios-self-hosted-repair-wakeup.yml"},
    "remediation": {"name": "AIOS approved remediation wakeup", "path": ".github/workflows/aios-approved-remediation-wakeup.yml"},
}
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


def load_policy(path: str | Path) -> WakePolicy:
    root = _mapping(yaml.load(_read(path, 8192).decode("utf-8"), Loader=_PolicyLoader))
    expected = {
        "format": "AIOS_BRAIN_WAKE_CARRIERS_POLICY", "version": 1,
        "repository": REPOSITORY, "wake_pr_number": 1200,
        "wake_marker": WAKE_MARKER, "max_comment_bytes": 2048,
        "trusted_source": TRUSTED_SOURCE, "workflow_sources": WORKFLOWS,
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


def project_event(*, event_name: str, event: Mapping[str, Any], repository: str, policy: WakePolicy, workflow: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a single bounded projection, or reject before any comment mutation."""
    event = _mapping(event)
    if repository != policy.repository or _mapping(event.get("repository")).get("full_name") != policy.repository:
        raise WakeBridgeError("wrong event repository")
    action = {"issues": "opened", "issue_comment": "created", "workflow_run": "completed"}.get(event_name)
    if action is None or event.get("action") != action:
        raise WakeBridgeError("unsupported GitHub event family")
    if event_name == "workflow_run":
        run = _mapping(event.get("workflow_run"))
        run_id = _integer(run.get("id"))
        attempt = _integer(run.get("run_attempt"))
        workflow_id = _integer(run.get("workflow_id"))
        metadata = _mapping(workflow)
        if _integer(metadata.get("id")) != workflow_id:
            raise WakeBridgeError("workflow ID disagreement")
        matches = [key for key, item in WORKFLOWS.items() if metadata.get("name") == item["name"] and metadata.get("path") == item["path"] and run.get("name") == item["name"]]
        if len(matches) != 1 or run.get("status") != "completed" or _mapping(run.get("head_repository")).get("full_name") != policy.repository:
            raise WakeBridgeError("untrusted workflow identity")
        family = matches[0]
        branch = _text(run.get("head_branch"), 240)
        if not (branch == "main" and run.get("event") == "workflow_dispatch"):
            if family != "publication" or run.get("event") != "push" or not re.fullmatch(rf"aios/review-decision/{RUN}", branch):
                raise WakeBridgeError("unauthorized workflow source ref/event")
        conclusion = run.get("conclusion")
        if not isinstance(conclusion, str) or conclusion not in CONCLUSIONS:
            raise WakeBridgeError("unknown workflow conclusion")
        if family != "publication" and conclusion not in FAILED_CONCLUSIONS:
            return dict(NO_WAKE)
        attention = "PUBLICATION_WORKFLOW_COMPLETED" if family == "publication" else "PRE_AIOS_OPERATIONAL_FAILURE"
        return _wake(policy, "workflow_run", f"{run_id}/{attempt}", attention, {"event_family": "workflow_run.completed", "workflow_run_id": run_id, "run_attempt": attempt, "workflow_id": workflow_id, "workflow_name": metadata["name"], "workflow_path": metadata["path"], "conclusion": conclusion})
    issue = _mapping(event.get("issue"))
    number = _integer(issue.get("number"))
    # The entire bus and all PR activity are excluded, including wake/ACK replay.
    if number == policy.wake_pr_number or "pull_request" in issue:
        return dict(NO_WAKE)
    title = _text(issue.get("title"), 256)
    if event_name == "issues":
        if title != "[AIOS TERMINAL ATTENTION]":
            return dict(NO_WAKE)
        _trusted(issue.get("user"))
        _trusted(event.get("sender"))
        identity = _integer(issue.get("id"))
        # Deliberately do not parse/copy the Issue body or terminal claims.
        return _wake(policy, "issues", identity, "TERMINAL_ATTENTION", {"event_family": "issues.opened", "issue_number": number, "issue_id": identity})
    if title not in {"[AIOS BRAIN INGRESS]", "[AIOS BRAIN WAKEUP]", "[AIOS REPAIR WAKEUP]"}:
        return dict(NO_WAKE)
    comment = _mapping(event.get("comment"))
    _trusted(comment.get("user"))
    _trusted(event.get("sender"))
    identity = _integer(comment.get("id"))
    body = _text(comment.get("body"), 14_000).removesuffix("\n")
    if not body or len(body) > 3500 or "[truncated]" in body:
        raise WakeBridgeError("receipt is empty, oversized or truncated")
    family = _ingress(body, issue) if title == "[AIOS BRAIN INGRESS]" else _carrier(body, title == "[AIOS REPAIR WAKEUP]")
    if family is None:
        return dict(NO_WAKE)
    return _wake(policy, "issue_comment", identity, family, {"event_family": "issue_comment.created", "issue_number": number, "comment_id": identity})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project one GitHub event into a bounded wake bell")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--policy", default=".ai/brain-wake-carriers.yaml")
    parser.add_argument("--workflow-metadata")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output)
    # Clear any stale projection before admission, including on malformed input.
    output.write_text(json.dumps(NO_WAKE) + "\n", encoding="utf-8")
    try:
        if not args.event or not args.event_name or not args.repository:
            raise WakeBridgeError("incomplete GitHub event framing")
        result = project_event(event_name=args.event_name, event=read_event(args.event), repository=args.repository, policy=load_policy(args.policy), workflow=read_event(args.workflow_metadata) if args.workflow_metadata else None)
        output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    except (WakeBridgeError, OSError, UnicodeError, yaml.YAMLError, json.JSONDecodeError, TypeError, RecursionError) as exc:
        print(f"wake projection rejected: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
