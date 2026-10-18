"""Bounded Operational Receipt v2 projections for remote AIOS handoffs.

Receipts are read-only, subordinate observations.  They neither reconcile a
dispatch nor create, complete, or reinterpret canonical lifecycle artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .review_transport import ReviewTransportError, resolve_admission_failure_delivery


FAMILIES = frozenset({"PRIMARY", "REMEDIATION", "REPAIR"})
BOUNDARIES = frozenset({
    "CARRIER_ADMITTED",
    "DISPATCH_REQUEST_ACCEPTED",
    "RUNNER_STARTED",
    "AIOS_INVOKED",
    "ADMISSION_ACCEPTED",
    "ADMISSION_REJECTED",
    "RUN_ATTRIBUTED",
    "OPERATIONAL_FAILED",
    "TERMINAL_POINTER",
    "SELF_HOST_COMPLETED",
})
WORKFLOW_REASONS = frozenset({
    "AIOS_REPO_ROOT_NOT_CONFIGURED",
    "CONFIGURED_REPOSITORY_UNAVAILABLE",
    "CONFIGURED_REPOSITORY_INVALID",
    "AIOS_COMMAND_UNAVAILABLE",
    "CONTROL_SOURCE_PATH_OCCUPIED",
    "CONTROL_SOURCE_MATERIALIZATION_FAILED",
    "CONTROL_SOURCE_SHA_MISMATCH",
    "CONTROL_SOURCE_DIRTY",
    "CONTROL_SOURCE_NOT_DISTINCT",
    "CONTROL_ENTRY_UNAVAILABLE",
    "INVALID_BOUNDED_INPUT",
    "UNEXPECTED_REPOSITORY_IDENTITY",
    "NONCANONICAL_WORKFLOW_REF",
    "UNAUTHORIZED_DELIVERY_ACTOR",
    "DOWNSTREAM_WORKFLOW_FAILED",
    "UNKNOWN",
})
_DELIVERY_FIELDS = {
    "PRIMARY": ("dispatch_id", "dispatches"),
    "REMEDIATION": ("correction_dispatch_id", "correction-dispatches"),
    "REPAIR": ("repair_dispatch_id", "repair-dispatches"),
}
_DELIVERY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_RUN_PATTERN = re.compile(r"^RUN-[A-Za-z0-9_-]+-\d{3,}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SELECTOR_FIELDS = frozenset({
    "task_id", "task_revision", "task_blob_sha", "task_commit_sha", "executor",
    "source_run_id", "finding_id", "failed_run_id", "repair_sha",
    "model", "reasoning_effort", "model_source", "effort_source",
})


class OperationalReceiptError(RuntimeError):
    """The exact delivery cannot be projected safely."""


@dataclass(frozen=True)
class OperationalReceipt:
    """One bounded statement of the highest directly proven boundary."""

    family: str
    delivery_id: str
    boundary: str
    selectors: Mapping[str, Any]
    run_created: bool = False
    executor_invoked: bool = False
    cause: Mapping[str, str] | None = None
    run_id: str | None = None
    terminal_pointer: Mapping[str, str] | None = None
    control_sha: str | None = None

    def as_dict(self) -> dict[str, Any]:
        _validate_family_delivery(self.family, self.delivery_id)
        if self.boundary not in BOUNDARIES:
            raise OperationalReceiptError("invalid operational boundary")
        if type(self.run_created) is not bool or type(self.executor_invoked) is not bool:
            raise OperationalReceiptError("invalid operational attribution flags")
        if self.run_id is not None and self.run_created is not True:
            raise OperationalReceiptError("RUN attribution requires run_created=true")
        selectors = _bounded_selectors(self.selectors)
        delivery_field = _DELIVERY_FIELDS[self.family][0]
        payload: dict[str, Any] = {
            "format": "AIOS_OPERATIONAL_RECEIPT",
            "version": 2,
            "kind": "OPERATIONAL_RECEIPT",
            "family": self.family,
            "delivery": {"kind": delivery_field, "id": self.delivery_id},
            "boundary": self.boundary,
            "selectors": selectors,
            "run_created": self.run_created,
            "executor_invoked": self.executor_invoked,
        }
        control_sha = self.control_sha or os.environ.get("AIOS_CONTROL_SHA")
        if control_sha is not None:
            if not isinstance(control_sha, str) or not _SHA_PATTERN.fullmatch(control_sha):
                raise OperationalReceiptError("invalid control source SHA")
            payload["control_sha"] = control_sha
        if self.cause is not None:
            authority = self.cause.get("authority")
            phase = self.cause.get("phase")
            reason = self.cause.get("reason_code")
            if (
                authority not in {"WORKFLOW", "AIOS_ADMISSION_FAILURE", "CORRECTION_PREFLIGHT", "CARRIER"}
                or not isinstance(phase, str) or not phase or len(phase) > 64
                or not isinstance(reason, str) or not reason or len(reason) > 128
            ):
                raise OperationalReceiptError("invalid bounded operational cause")
            payload["cause"] = {
                "authority": authority,
                "phase": phase,
                "reason_code": reason,
            }
        if self.run_id is not None:
            if not _RUN_PATTERN.fullmatch(self.run_id):
                raise OperationalReceiptError("invalid attributed RUN identity")
            if self.boundary != "TERMINAL_POINTER":
                payload["run_id"] = self.run_id
        if self.terminal_pointer is not None:
            pointer = dict(self.terminal_pointer)
            if (
                pointer.get("kind") not in {"RESULT", "FAILURE"}
                or pointer.get("run_id") != self.run_id
                or not isinstance(pointer.get("artifact"), str)
                or len(pointer["artifact"]) > 512
            ):
                raise OperationalReceiptError("invalid canonical terminal pointer")
            payload["terminal_pointer"] = pointer
        return payload

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

    def attention_observation(self) -> dict[str, Any]:
        """Project only proven boundaries through the shared attention registry."""
        from .brain_attention import operational_observation
        return operational_observation(self.as_dict())


def workflow_failure_receipt(
    family: str,
    delivery_id: str,
    reason_code: str,
    *,
    selectors: Mapping[str, Any] | None = None,
) -> OperationalReceipt:
    """Describe a deterministic failure before the bounded AIOS command ran."""

    if reason_code not in WORKFLOW_REASONS:
        raise OperationalReceiptError("invalid workflow-owned operational reason")
    return OperationalReceipt(
        family=family,
        delivery_id=delivery_id,
        boundary="OPERATIONAL_FAILED",
        selectors=selectors or {},
        cause={
            "authority": "WORKFLOW",
            "phase": "PRE_AIOS",
            "reason_code": reason_code,
        },
        run_created=False,
        executor_invoked=False,
    )


def invoked_receipt(
    family: str,
    delivery_id: str,
    *,
    selectors: Mapping[str, Any] | None = None,
) -> OperationalReceipt:
    """Record only that the bounded AIOS command was invoked."""

    return OperationalReceipt(
        family=family,
        delivery_id=delivery_id,
        boundary="AIOS_INVOKED",
        selectors=selectors or {},
    )


def correction_preflight_receipt(
    family: str,
    delivery_id: str,
    preflight: Mapping[str, Any],
    *,
    selectors: Mapping[str, Any] | None = None,
) -> OperationalReceipt:
    """Project the exact blocked correction preflight returned by this call."""

    if family not in {"REMEDIATION", "REPAIR"} or preflight.get("status") != "BLOCKED":
        raise OperationalReceiptError("correction preflight is not a blocked delivery")
    phase = preflight.get("phase")
    reason = preflight.get("reason_code")
    if not isinstance(phase, str) or not isinstance(reason, str):
        raise OperationalReceiptError("correction preflight has no typed cause")
    return OperationalReceipt(
        family=family,
        delivery_id=delivery_id,
        boundary="ADMISSION_REJECTED",
        selectors=selectors or {},
        cause={
            "authority": "CORRECTION_PREFLIGHT",
            "phase": phase,
            "reason_code": reason,
        },
    )


def project_delivery_receipt(
    state_root: Path,
    *,
    family: str,
    delivery_id: str,
    selectors: Mapping[str, Any] | None = None,
    remote_repo: Path | None = None,
) -> OperationalReceipt:
    """Read the exact family journal and project its strongest proven fact.

    No timestamp, filename ordering, metadata-only RUN matching, or journal
    reconciliation is performed.
    """

    _validate_family_delivery(family, delivery_id)
    bounded = _bounded_selectors(selectors or {})
    delivery_field, directory = _DELIVERY_FIELDS[family]

    def rejection_or_invoked() -> OperationalReceipt:
        cause = _exact_admission_cause(
            state_root / "admission-failures", family, delivery_field, delivery_id
        )
        if cause is None and remote_repo is not None:
            try:
                cause = resolve_admission_failure_delivery(
                    remote_repo, family=family, delivery_id=delivery_id
                )
            except ReviewTransportError as exc:
                raise OperationalReceiptError(str(exc)) from exc
        if cause is None:
            return invoked_receipt(family, delivery_id, selectors=bounded)
        return OperationalReceipt(
            family=family, delivery_id=delivery_id, boundary="ADMISSION_REJECTED",
            selectors=bounded, cause=cause,
        )

    key = hashlib.sha256(delivery_id.encode("ascii")).hexdigest()
    path = state_root / directory / f"{key}.json"
    if not path.is_file():
        if remote_repo is not None:
            return rejection_or_invoked()
        return invoked_receipt(family, delivery_id, selectors=bounded)
    record = _read_mapping(path, "dispatch journal")
    if record.get(delivery_field) != delivery_id:
        raise OperationalReceiptError("dispatch journal identity collision")
    for name, expected in bounded.items():
        if name in record and record.get(name) != expected:
            raise OperationalReceiptError("dispatch journal selector collision")

    run_id = record.get("run_id")
    if run_id is None:
        return rejection_or_invoked()
    if not isinstance(run_id, str) or not _RUN_PATTERN.fullmatch(run_id):
        raise OperationalReceiptError("dispatch journal has invalid RUN attribution")

    result_path = state_root / "results" / f"{run_id}.json"
    failure_path = state_root / "failures" / f"{run_id}.json"
    if result_path.is_file() and failure_path.is_file():
        raise OperationalReceiptError("attributed RUN has conflicting terminals")
    if result_path.is_file() or failure_path.is_file():
        kind = "RESULT" if result_path.is_file() else "FAILURE"
        artifact = f".git/aios/{kind.lower()}s/{run_id}.json"
        return OperationalReceipt(
            family=family,
            delivery_id=delivery_id,
            boundary="TERMINAL_POINTER",
            selectors=bounded,
            run_created=True,
            executor_invoked=False,
            run_id=run_id,
            terminal_pointer={"kind": kind, "run_id": run_id, "artifact": artifact},
        )
    return OperationalReceipt(
        family=family,
        delivery_id=delivery_id,
        boundary="RUN_ATTRIBUTED",
        selectors=bounded,
        run_created=True,
        executor_invoked=False,
        run_id=run_id,
    )


def new_admission_blocker(
    admission_failures: Path,
    *,
    before: Iterable[str],
    operation: str,
    exact_facts: Mapping[str, Any],
) -> Mapping[str, str] | None:
    """Return one typed diagnostic created by this exact delegated call."""

    prior = frozenset(before)
    matches: list[Mapping[str, Any]] = []
    if not admission_failures.is_dir():
        return None
    for path in admission_failures.glob("*.json"):
        if path.name in prior:
            continue
        try:
            payload = _read_mapping(path, "admission failure")
        except OperationalReceiptError:
            continue
        if (
            payload.get("format") == "AIOS_ADMISSION_FAILURE"
            and payload.get("version") == 2
            and payload.get("operation") == operation
            and all(payload.get(name) == value for name, value in exact_facts.items())
        ):
            matches.append(payload)
    if len(matches) != 1:
        return None
    phase = matches[0].get("phase")
    reason = matches[0].get("reason_code")
    if not isinstance(phase, str) or not isinstance(reason, str):
        return None
    return {
        "code": reason,
        "phase": phase,
        "reason_code": reason,
        "source": "AIOS_ADMISSION_FAILURE",
    }


def write_receipt(path: str | Path, receipt: OperationalReceipt) -> None:
    """Atomically replace one workflow-run receipt outside canonical state."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(receipt.render(), encoding="utf-8", newline="")
    os.replace(temporary, target)


def write_environment_receipt(receipt: OperationalReceipt) -> None:
    path = os.environ.get("AIOS_OPERATIONAL_RECEIPT_PATH")
    if path:
        # A finally-block's generic observation must not erase the exact
        # preflight rejection written by the invocation it is observing.
        if Path(path).is_file() and receipt.boundary in {"AIOS_INVOKED", "SELF_HOST_COMPLETED"}:
            previous = _read_mapping(Path(path), "operational receipt")
            current = receipt.as_dict()
            if (previous.get("family") == receipt.family and previous.get("delivery") == current["delivery"]
                    and previous.get("selectors") == current["selectors"]
                    and previous.get("control_sha") == current.get("control_sha")
                    and previous.get("boundary") in {"ADMISSION_REJECTED", "OPERATIONAL_FAILED", "TERMINAL_POINTER"}):
                from .brain_attention import operational_observation
                operational_observation(dict(previous))
                return
        write_receipt(path, receipt)


REPAIR_REPOSITORY = "trung-via/AIOS-renew"
REPAIR_SOURCE_LANES = frozenset({"ingress", "wakeup", "delivery"})


def _positive(value: Any) -> bool:
    return type(value) is int and 0 < value <= 2**53 - 1


def validate_repair_source(value: Any) -> dict[str, Any]:
    fields = {"format", "version", "lane", "repository", "workflow_run_id", "run_attempt", "control_sha",
        "event", "actor", "triggering_actor", "original_actor", "delivery_id", "failed_run_id", "repair_sha", "issue", "parent"}
    if (not isinstance(value, dict) or set(value) != fields or value["format"] != "AIOS_REPAIR_WORKFLOW_SOURCE"
            or type(value["version"]) is not int or value["version"] != 1 or value["lane"] not in REPAIR_SOURCE_LANES
            or value["repository"] != REPAIR_REPOSITORY
            or any(not _positive(value[k]) for k in ("workflow_run_id", "run_attempt"))
            or any(not isinstance(value[k], str) or not _SHA_PATTERN.fullmatch(value[k]) for k in ("control_sha", "repair_sha"))
            or value["event"] not in {"issues", "workflow_dispatch"}
            or any(value[k] not in {"trung-via", "github-actions[bot]"} for k in ("actor", "triggering_actor", "original_actor"))
            or not isinstance(value["delivery_id"], str) or not _DELIVERY_PATTERN.fullmatch(value["delivery_id"])
            or not isinstance(value["failed_run_id"], str) or not _RUN_PATTERN.fullmatch(value["failed_run_id"])):
        raise OperationalReceiptError("REPAIR_SOURCE_INVALID")
    issue, parent = value["issue"], value["parent"]
    if issue is not None and (not isinstance(issue, dict) or set(issue) != {"id", "number", "body_digest"}
            or not _positive(issue["id"]) or not _positive(issue["number"])
            or not isinstance(issue["body_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", issue["body_digest"])):
        raise OperationalReceiptError("REPAIR_ISSUE_SOURCE_INVALID")
    if parent is not None and (not isinstance(parent, dict)
            or set(parent) != {"workflow_run_id", "run_attempt", "artifact_id"}
            or any(not _positive(x) for x in parent.values()) or value["event"] != "workflow_dispatch"
            or value["actor"] != "github-actions[bot]"):
        raise OperationalReceiptError("REPAIR_PARENT_SOURCE_INVALID")
    if ((value["event"] == "issues") != (issue is not None)
            or value["event"] == "issues" and value["original_actor"] != "trung-via"
            or value["event"] == "workflow_dispatch" and value["original_actor"] != value["actor"]
            or value["lane"] != "delivery" and value["event"] != "issues"):
        raise OperationalReceiptError("REPAIR_EVENT_SOURCE_INVALID")
    return value


def write_repair_workflow_source(event_path: str | Path, *, lane: str, delivery_id: str,
                                failed_run_id: str, repair_sha: str) -> None:
    """Called only after existing event admission, from GitHub-owned context.

    This records provenance, never authorizes a delivery. No Issue body, profile,
    credential, command, private path, or raw diagnostic enters the artifact.
    """
    path = os.environ.get("AIOS_REPAIR_SOURCE_PATH")
    if not path:
        return
    from .brain_attention import load_json
    raw = Path(event_path).read_bytes()
    event = load_json(raw, 1048576)
    env = os.environ
    actor = env.get("GITHUB_ACTOR")
    if (event.get("repository", {}).get("full_name") != env.get("GITHUB_REPOSITORY")
            or event.get("sender", {}).get("login") not in {actor, "trung-via"}
            or env.get("GITHUB_REF") != "refs/heads/main"):
        raise OperationalReceiptError("REPAIR_EVENT_SOURCE_INVALID")
    issue = event.get("issue")
    original_actor = event.get("sender", {}).get("login")
    parent = None
    if issue is not None:
        if (event.get("action") != "opened" or issue.get("user", {}).get("login") != original_actor
                or not isinstance(issue.get("body"), str)):
            raise OperationalReceiptError("REPAIR_ISSUE_SOURCE_INVALID")
        issue = dict(id=issue.get("id"), number=issue.get("number"),
                     body_digest=hashlib.sha256(issue["body"].encode("utf-8")).hexdigest())
    else:
        inputs = event.get("inputs", {})
        if any(inputs.get(k, "") != v for k, v in (
                ("repair_dispatch_id", delivery_id), ("failed_run_id", failed_run_id), ("repair_sha", repair_sha))):
            raise OperationalReceiptError("REPAIR_DISPATCH_SOURCE_INVALID")
        values = [inputs.get(k, "") for k in ("origin_workflow_run_id", "origin_run_attempt", "origin_artifact_id")]
        if any(values):
            try:
                parent = dict(zip(("workflow_run_id", "run_attempt", "artifact_id"), map(int, values)))
            except (ValueError, TypeError):
                raise OperationalReceiptError("REPAIR_PARENT_SOURCE_INVALID") from None
    try:
        source = validate_repair_source(dict(format="AIOS_REPAIR_WORKFLOW_SOURCE", version=1, lane=lane,
            repository=env.get("GITHUB_REPOSITORY"), workflow_run_id=int(env.get("GITHUB_RUN_ID", "")),
            run_attempt=int(env.get("GITHUB_RUN_ATTEMPT", "")), control_sha=env.get("GITHUB_SHA"),
            event=env.get("GITHUB_EVENT_NAME"), actor=actor,
            triggering_actor=env.get("GITHUB_TRIGGERING_ACTOR", actor), original_actor=original_actor,
            delivery_id=delivery_id, failed_run_id=failed_run_id, repair_sha=repair_sha, issue=issue, parent=parent))
    except (ValueError, TypeError):
        raise OperationalReceiptError("REPAIR_SOURCE_INVALID") from None
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(source, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def record_repair_workflow_source(event_path, **selectors) -> bool:
    """Subordinate best-effort observation, never changes delivery authority."""
    from .brain_attention import AttentionError
    try:
        write_repair_workflow_source(event_path, **selectors)
        return True
    except (OperationalReceiptError, AttentionError, OSError, ValueError, TypeError, KeyError):
        return False


def observe_repair_delivery(*, delivery_id: str | None = None, issue_number: int | None = None,
                           workflow_run_id: int | None = None, run_attempt: int | None = None,
                           sources=None) -> dict[str, Any]:
    """Bounded read-only discovery from an exact delivery or original Issue.

    The source backend authenticates every artifact against its GitHub attempt;
    matching never uses a title, timestamp, run name, log, or user-provided URL.
    """
    from .brain_attention import ArtifactSources, AttentionError, digest
    if (delivery_id is None and issue_number is None or delivery_id is not None and
            (not isinstance(delivery_id, str) or not _DELIVERY_PATTERN.fullmatch(delivery_id))
            or issue_number is not None and not _positive(issue_number)
            or (workflow_run_id is None) != (run_attempt is None)
            or workflow_run_id is not None and (not _positive(workflow_run_id) or not _positive(run_attempt))):
        raise OperationalReceiptError("REPAIR_SELECTOR_INVALID")
    backend = sources or ArtifactSources(REPAIR_REPOSITORY)
    output = dict(format="AIOS_REPAIR_DELIVERY_CONTEXT", version=1, repository=REPAIR_REPOSITORY,
        selector=dict(delivery_id=delivery_id, issue_number=issue_number,
                      workflow_run_id=workflow_run_id, run_attempt=run_attempt),
        availability="UNAVAILABLE", blocker=None, attempt=None, run_created=None, executor_invoked=None,
        proof="OPERATIONAL_OBSERVATION_ONLY")
    try:
        issue = backend.repair_issue(issue_number) if issue_number is not None else None
        pointers = backend.repair_pointers(issue, workflow_run_id, run_attempt, delivery_id)
        candidates = []
        origins = []
        for pointer in pointers:
            source = backend.repair_source(pointer)
            if delivery_id is not None and source["delivery_id"] != delivery_id:
                continue
            # Bot dispatches inherit an original Issue/actor only through an
            # authenticated immutable parent artifact, with exact selector equality.
            origin = source
            if source["parent"] is not None:
                origin = backend.repair_source(source["parent"])
                if (origin["lane"] != "ingress" or origin["parent"] is not None
                        or any(origin[k] != source[k] for k in ("delivery_id", "failed_run_id", "repair_sha"))):
                    raise OperationalReceiptError("REPAIR_PARENT_SUBSTITUTION")
            if issue is not None:
                bound = origin["issue"]
                if bound is None or bound["id"] != issue["id"] or bound["number"] != issue_number:
                    continue
                if (bound["body_digest"] != hashlib.sha256(issue["body"].encode("utf-8")).hexdigest()
                        or origin["original_actor"] != issue["user"]["login"]):
                    raise OperationalReceiptError("REPAIR_ISSUE_SOURCE_MOVED")
            if source["lane"] == "ingress":
                origins.append((source, pointer))
            else:
                candidates.append((source, origin, pointer))
        # A wakeup and delivery artifact of the same reusable workflow identify
        # one attempt. Distinct attempts remain ambiguous unless selected exactly.
        unique = {}
        for source, origin, pointer in candidates:
            key = (source["workflow_run_id"], source["run_attempt"])
            if key in unique:
                prior = unique[key][0]
                if any(prior[k] != source[k] for k in ("delivery_id", "failed_run_id", "repair_sha", "issue", "original_actor")):
                    raise OperationalReceiptError("REPAIR_SOURCE_CONFLICT")
            unique[key] = (source, origin, pointer)
        if len(unique) > 1:
            raise OperationalReceiptError("REPAIR_ATTEMPT_AMBIGUOUS")
        if not unique:
            output["blocker"] = {"code": "REPAIR_ATTEMPT_UNAVAILABLE", "action": "READ_AUTHENTICATED_ACTIONS_SOURCE"}
            if len(origins) == 1:
                source, pointer = origins[0]
                output["origin"] = dict(delivery_id=source["delivery_id"], failed_run_id=source["failed_run_id"],
                    repair_sha=source["repair_sha"], original_actor=source["original_actor"],
                    workflow_run_id=source["workflow_run_id"], run_attempt=source["run_attempt"],
                    source_digest=digest(source))
            return output
        source, origin, pointer = next(iter(unique.values()))
        attempt = backend.repair_jobs(source)
        receipt_blocker = None
        try:
            receipt = backend.repair_receipt(source)
        except AttentionError as exc:
            if str(exc) not in {"SOURCE_UNAVAILABLE", "SOURCE_TIMEOUT", "REPAIR_ARTIFACT_EXPIRED"}:
                raise
            receipt = None
            receipt_blocker = {"code": str(exc), "action": "READ_EXACT_OPERATIONAL_RECEIPT"}
        cause = dict(authority="WORKFLOW", phase="WORKFLOW", reason_code="UNKNOWN")
        boundary, run_id, run_created = "WORKFLOW_" + attempt["status"].upper(), None, None
        if receipt is not None:
            from .brain_attention import operational_observation
            operational_observation(receipt)  # Closed schema and bounded cause validation.
            if (any(k in receipt and type(receipt[k]) is not bool for k in ("run_created", "executor_invoked"))
                    or receipt.get("cause") is not None and receipt["boundary"] not in {"ADMISSION_REJECTED", "OPERATIONAL_FAILED"}):
                raise OperationalReceiptError("REPAIR_RECEIPT_INVALID")
            if (receipt.get("family") != "REPAIR" or receipt.get("delivery", {}).get("id") != source["delivery_id"]
                    or any(receipt.get("selectors", {}).get(k) != source[k] for k in ("failed_run_id", "repair_sha"))
                    or receipt.get("control_sha", source["control_sha"]) != source["control_sha"]):
                raise OperationalReceiptError("REPAIR_RECEIPT_SUBSTITUTION")
            if receipt["boundary"] in {"ADMISSION_REJECTED", "OPERATIONAL_FAILED", "RUN_ATTRIBUTED", "TERMINAL_POINTER"}:
                boundary = receipt["boundary"]
                run_created = receipt.get("run_created")
                run_id = receipt.get("run_id") or receipt.get("terminal_pointer", {}).get("run_id")
                if run_id is not None and (not isinstance(run_id, str) or not _RUN_PATTERN.fullmatch(run_id) or run_created is not True):
                    raise OperationalReceiptError("REPAIR_RUN_ATTRIBUTION_INVALID")
                if receipt["boundary"] in {"RUN_ATTRIBUTED", "TERMINAL_POINTER"} and (run_created is not True or run_id is None):
                    raise OperationalReceiptError("REPAIR_RUN_ATTRIBUTION_INVALID")
                if receipt["boundary"] == "TERMINAL_POINTER":
                    terminal = receipt.get("terminal_pointer")
                    if (not isinstance(terminal, dict) or set(terminal) != {"kind", "run_id", "artifact"}
                            or terminal["kind"] not in {"RESULT", "FAILURE"} or terminal["run_id"] != run_id
                            or not isinstance(terminal["artifact"], str) or len(terminal["artifact"]) > 512):
                        raise OperationalReceiptError("REPAIR_TERMINAL_POINTER_INVALID")
            elif receipt.get("run_created") not in (None, False) or receipt.get("run_id") is not None or receipt.get("terminal_pointer") is not None:
                raise OperationalReceiptError("REPAIR_RUN_ATTRIBUTION_INVALID")
            if receipt.get("cause"):
                cause = receipt["cause"]
        failed = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
        workflow_failure = (attempt["conclusion"] in failed or any(job["conclusion"] in failed
            or any(step["conclusion"] in failed for step in job["steps"]) for job in attempt["jobs"]))
        if workflow_failure:
            if boundary not in {"ADMISSION_REJECTED", "RUN_ATTRIBUTED", "TERMINAL_POINTER"}:
                boundary = "OPERATIONAL_FAILED"
        attempt.update(boundary=boundary, cause=cause, run_id=run_id, delivery_id=source["delivery_id"],
            workflow_failure_observed=workflow_failure, receipt_availability="AVAILABLE" if receipt else "UNAVAILABLE",
            receipt_blocker=receipt_blocker,
            failed_run_id=source["failed_run_id"], repair_sha=source["repair_sha"],
            actor=source["actor"], triggering_actor=source["triggering_actor"], original_actor=origin["original_actor"],
            source=dict(**pointer, source_digest=digest(source)))
        output.update(availability="AVAILABLE", attempt=attempt, run_created=run_created,
            executor_invoked=receipt.get("executor_invoked") if run_created is not None and receipt else None)
        # Re-read mutable GitHub identities after all dependent reads.
        backend.repair_jobs(source, expected=attempt)
        if issue is not None and backend.repair_issue(issue_number) != issue:
            raise OperationalReceiptError("REPAIR_ISSUE_SOURCE_MOVED")
        validate_repair_delivery_context(output)
        return output
    except (AttentionError, OperationalReceiptError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        code = str(exc)
        if not isinstance(exc, (AttentionError, OperationalReceiptError)) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", code):
            code = "REPAIR_SOURCE_UNAVAILABLE" if isinstance(exc, OSError) else "REPAIR_SOURCE_INTEGRITY_INVALID"
        output.update(availability="UNAVAILABLE", attempt=None, run_created=None, executor_invoked=None,
            blocker={"code": code, "action": "READ_AUTHENTICATED_ACTIONS_SOURCE"})
        return output


def validate_repair_delivery_context(value: Any) -> None:
    """Portable output gate: no raw GitHub response, URL, profile or log text."""
    from .brain_attention import _operational_cause
    fields = {"format", "version", "repository", "selector", "availability", "blocker", "attempt",
              "run_created", "executor_invoked", "proof"}
    if (not isinstance(value, dict) or set(value) not in (fields, fields | {"origin"})
            or value["format"] != "AIOS_REPAIR_DELIVERY_CONTEXT" or type(value["version"]) is not int or value["version"] != 1
            or value["repository"] != REPAIR_REPOSITORY or value["proof"] != "OPERATIONAL_OBSERVATION_ONLY"
            or value["availability"] not in {"AVAILABLE", "UNAVAILABLE"}
            or any(value[k] is not None and type(value[k]) is not bool for k in ("run_created", "executor_invoked"))):
        raise OperationalReceiptError("REPAIR_CONTEXT_INVALID")
    selector = value["selector"]
    if (not isinstance(selector, dict) or set(selector) != {"delivery_id", "issue_number", "workflow_run_id", "run_attempt"}
            or selector["delivery_id"] is not None and (not isinstance(selector["delivery_id"], str) or not _DELIVERY_PATTERN.fullmatch(selector["delivery_id"]))
            or any(selector[k] is not None and not _positive(selector[k]) for k in ("issue_number", "workflow_run_id", "run_attempt"))):
        raise OperationalReceiptError("REPAIR_CONTEXT_SELECTOR_INVALID")
    blocker = value["blocker"]
    if value["availability"] == "UNAVAILABLE":
        if (value["attempt"] is not None or value["run_created"] is not None or value["executor_invoked"] is not None
                or not isinstance(blocker, dict) or set(blocker) != {"code", "action"}
                or not isinstance(blocker["code"], str) or re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", blocker["code"]) is None
                or blocker["action"] != "READ_AUTHENTICATED_ACTIONS_SOURCE"):
            raise OperationalReceiptError("REPAIR_CONTEXT_BLOCKER_INVALID")
    elif blocker is not None:
        raise OperationalReceiptError("REPAIR_CONTEXT_BLOCKER_INVALID")
    if "origin" in value:
        origin = value["origin"]
        if (not isinstance(origin, dict) or set(origin) != {"delivery_id", "failed_run_id", "repair_sha", "original_actor", "workflow_run_id", "run_attempt", "source_digest"}
                or not _DELIVERY_PATTERN.fullmatch(origin["delivery_id"]) or not _RUN_PATTERN.fullmatch(origin["failed_run_id"])
                or not _SHA_PATTERN.fullmatch(origin["repair_sha"]) or origin["original_actor"] != "trung-via"
                or not _positive(origin["workflow_run_id"]) or not _positive(origin["run_attempt"])
                or re.fullmatch(r"[0-9a-f]{64}", origin["source_digest"]) is None):
            raise OperationalReceiptError("REPAIR_CONTEXT_ORIGIN_INVALID")
    attempt = value["attempt"]
    if attempt is None:
        return
    fields = {"workflow_run_id", "run_attempt", "status", "conclusion", "jobs", "jobs_availability", "jobs_blocker", "boundary", "cause", "run_id",
              "workflow_failure_observed", "receipt_availability", "receipt_blocker",
              "delivery_id", "failed_run_id", "repair_sha", "actor", "triggering_actor", "original_actor", "source"}
    statuses = {"queued", "running", "completed"}
    conclusions = {None, "success", "failure", "cancelled", "skipped", "neutral", "timed_out", "action_required", "startup_failure", "stale"}
    if (not isinstance(attempt, dict) or set(attempt) != fields or value["availability"] != "AVAILABLE"
            or not _positive(attempt["workflow_run_id"]) or not _positive(attempt["run_attempt"])
            or attempt["status"] not in statuses or attempt["conclusion"] not in conclusions
            or attempt["boundary"] not in BOUNDARIES | {"WORKFLOW_QUEUED", "WORKFLOW_RUNNING", "WORKFLOW_COMPLETED"}
            or not isinstance(attempt["delivery_id"], str) or not _DELIVERY_PATTERN.fullmatch(attempt["delivery_id"])
            or not isinstance(attempt["failed_run_id"], str) or not _RUN_PATTERN.fullmatch(attempt["failed_run_id"])
            or not isinstance(attempt["repair_sha"], str) or not _SHA_PATTERN.fullmatch(attempt["repair_sha"])
            or any(attempt[k] not in {"trung-via", "github-actions[bot]"} for k in ("actor", "triggering_actor", "original_actor"))
            or not isinstance(attempt["jobs"], list) or len(attempt["jobs"]) > 32
            or attempt["jobs_availability"] not in {"AVAILABLE", "UNAVAILABLE"}
            or type(attempt["workflow_failure_observed"]) is not bool
            or attempt["receipt_availability"] not in {"AVAILABLE", "UNAVAILABLE"}
            or attempt["run_id"] is not None and (not isinstance(attempt["run_id"], str) or not _RUN_PATTERN.fullmatch(attempt["run_id"]) or value["run_created"] is not True)):
        raise OperationalReceiptError("REPAIR_CONTEXT_ATTEMPT_INVALID")
    for field, action, codes in (
            ("jobs_blocker", "READ_AUTHENTICATED_ACTIONS_JOBS", {"SOURCE_UNAVAILABLE", "SOURCE_TIMEOUT"}),
            ("receipt_blocker", "READ_EXACT_OPERATIONAL_RECEIPT", {"SOURCE_UNAVAILABLE", "SOURCE_TIMEOUT", "REPAIR_ARTIFACT_EXPIRED"})):
        blocker = attempt[field]
        if blocker is not None and (not isinstance(blocker, dict) or set(blocker) != {"code", "action"}
                or blocker["code"] not in codes or blocker["action"] != action):
            raise OperationalReceiptError("REPAIR_CONTEXT_BLOCKER_INVALID")
    failed = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
    observed_failure = (attempt["conclusion"] in failed or any(job.get("conclusion") in failed
        or any(step.get("conclusion") in failed for step in job.get("steps", [])) for job in attempt["jobs"]))
    if attempt["workflow_failure_observed"] is not observed_failure:
        raise OperationalReceiptError("REPAIR_CONTEXT_STATUS_SUBSTITUTION")
    for key in ("delivery_id", "workflow_run_id", "run_attempt"):
        if selector[key] is not None and selector[key] != attempt[key]:
            raise OperationalReceiptError("REPAIR_CONTEXT_SUBSTITUTION")
    pointer = attempt["source"]
    if (not isinstance(pointer, dict) or set(pointer) != {"workflow_run_id", "run_attempt", "artifact_id", "source_digest"}
            or any(pointer[k] != attempt[k] for k in ("workflow_run_id", "run_attempt")) or not _positive(pointer["artifact_id"])
            or not isinstance(pointer["source_digest"], str) or re.fullmatch(r"[0-9a-f]{64}", pointer["source_digest"]) is None):
        raise OperationalReceiptError("REPAIR_CONTEXT_SOURCE_INVALID")
    cause = attempt["cause"]
    if cause != dict(authority="WORKFLOW", phase="WORKFLOW", reason_code="UNKNOWN"):
        _operational_cause(cause, "PRE_AIOS_FAILED" if cause.get("authority") == "WORKFLOW" else "DISPATCH_REJECTED")
    for job in attempt["jobs"]:
        if (not isinstance(job, dict) or set(job) != {"job_id", "name", "status", "conclusion", "steps"}
                or not _positive(job["job_id"]) or job["status"] not in statuses or job["conclusion"] not in conclusions
                or not isinstance(job["name"], str) or job["name"] not in REPAIR_PUBLIC_NAMES
                or not isinstance(job["steps"], list) or len(job["steps"]) > 64):
            raise OperationalReceiptError("REPAIR_CONTEXT_JOB_INVALID")
        for step in job["steps"]:
            if (not isinstance(step, dict) or set(step) != {"number", "name", "status", "conclusion"}
                    or not _positive(step["number"]) or step["status"] not in statuses or step["conclusion"] not in conclusions
                    or not isinstance(step["name"], str) or step["name"] not in REPAIR_PUBLIC_NAMES):
                raise OperationalReceiptError("REPAIR_CONTEXT_STEP_INVALID")


REPAIR_PUBLIC_NAMES = frozenset({"WITHHELD", "execute-repair", "dispatch / execute-repair", "repair_dispatch / execute-repair", "admit", "deliver", "receipt",
    "Prepare exact transient control source", "Deliver one immutable REPAIR intent", "Expose bounded Operational Receipt v2",
    "Persist bounded Operational Receipt v2", "Remove transient control source", "Complete job", "Set up job",
    "Admit and sanitize one REPAIR wakeup", "Deliver immutable Issue event once"})


def _validate_family_delivery(family: str, delivery_id: str) -> None:
    if family not in FAMILIES:
        raise OperationalReceiptError("invalid operational family")
    if not isinstance(delivery_id, str) or not _DELIVERY_PATTERN.fullmatch(delivery_id):
        raise OperationalReceiptError("invalid operational delivery identity")


def _bounded_selectors(selectors: Mapping[str, Any]) -> dict[str, Any]:
    if any(name not in _SELECTOR_FIELDS for name in selectors):
        raise OperationalReceiptError("unbounded operational selector")
    bounded: dict[str, Any] = {}
    for name, value in selectors.items():
        if value is None:
            continue
        if name == "task_revision":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise OperationalReceiptError("invalid operational selector")
        elif not isinstance(value, str) or not value or len(value) > 256:
            raise OperationalReceiptError("invalid operational selector")
        bounded[name] = value
    return bounded


def _read_mapping(path: Path, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OperationalReceiptError(f"invalid {label}") from exc
    if not isinstance(payload, Mapping):
        raise OperationalReceiptError(f"invalid {label}")
    return payload


def _exact_admission_cause(
    directory: Path, family: str, delivery_field: str, delivery_id: str
) -> Mapping[str, str] | None:
    matches: list[Mapping[str, Any]] = []
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                payload = _read_mapping(path, "admission failure")
            except OperationalReceiptError:
                continue
            if (
                payload.get("format") == "AIOS_ADMISSION_FAILURE"
                and payload.get("version") == 2
                and payload.get("operation") == family
                and payload.get(delivery_field) == delivery_id
            ):
                matches.append(payload)
    if len(matches) != 1:
        return None
    phase = matches[0].get("phase")
    reason = matches[0].get("reason_code")
    if not isinstance(phase, str) or not isinstance(reason, str):
        return None
    return {
        "authority": "AIOS_ADMISSION_FAILURE",
        "phase": phase,
        "reason_code": reason,
    }


__all__ = [
    "BOUNDARIES",
    "OperationalReceipt",
    "OperationalReceiptError",
    "correction_preflight_receipt",
    "invoked_receipt",
    "new_admission_blocker",
    "project_delivery_receipt",
    "workflow_failure_receipt",
    "write_environment_receipt",
    "write_receipt",
]
