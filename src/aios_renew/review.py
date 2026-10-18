"""Canonical REVIEW and REMEDIATION contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, ClassVar

import yaml

from .artifacts import Result
from .run import Run
from .task import Task
from .verification_contract import (
    MINIMUM_SUFFICIENT_V1,
    VERIFICATION_POLICIES,
    VerificationContractError,
    validate_v1_verification,
)


REVIEW_MODES = frozenset({"PRIMARY", "DELTA"})
REVIEW_VERDICTS = frozenset({"PASS", "CHANGES_REQUIRED", "BLOCKED"})
REMEDIATION_ACTIONS = frozenset({"CODE_FIX", "EVIDENCE_ONLY"})
ACCEPTANCE_RESULTS = frozenset({"PASS", "FAIL"})


class ReviewValidationError(ValueError):
    """Raised when REVIEW or REMEDIATION violates its contract."""


@dataclass(frozen=True)
class Finding:
    id: str
    basis: str
    action: str
    location: str
    issue: str
    expected: str


@dataclass(frozen=True)
class Review:
    review_id: str
    reviewed_sha: str
    mode: str
    verdict: str
    acceptance: Mapping[str, str]
    findings: tuple[Finding, ...]
    prior_finding_id: str | None = None


@dataclass(frozen=True)
class Remediation:
    finding_id: str
    action: str
    reviewed_sha: str
    modification_scope: tuple[str, ...] = ()
    affected_verification: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    verification_policy: ClassVar[str | None] = None
    full_suite_reason: ClassVar[str | None] = None


@dataclass(frozen=True)
class _PolicyRemediation(Remediation):
    """Policy extension that leaves serialized legacy remediation unchanged."""

    verification_policy: str = MINIMUM_SUFFICIENT_V1
    full_suite_reason: str | None = None


@dataclass(frozen=True)
class RemediationExecution:
    """The complete, deliberately narrow semantic input to an executor."""

    review_id: str
    finding: Finding
    remediation: Remediation
    run: Run
    original_constraints: tuple[str, ...] = ()


def parse_review(source: str) -> Review:
    """Parse and structurally validate one YAML REVIEW document."""

    root = _mapping(_parse_yaml(source, "REVIEW"), "REVIEW")
    if _batch_document(root, "AIOS_MULTI_FINDING_DELTA_REVIEW"):
        return _parse_batch_review(_strict_batch_yaml(source))
    mode = _choice(
        _required(root, "mode", "REVIEW"), "mode", REVIEW_MODES
    )
    verdict = _choice(
        _required(root, "verdict", "REVIEW"),
        "verdict",
        REVIEW_VERDICTS,
    )

    acceptance_data = _mapping(
        _required(root, "acceptance", "REVIEW"), "acceptance"
    )
    acceptance: dict[str, str] = {}
    for key, value in acceptance_data.items():
        acceptance_id = _string(key, "acceptance key")
        acceptance[acceptance_id] = _choice(
            value,
            f"acceptance.{acceptance_id}",
            ACCEPTANCE_RESULTS,
        )

    findings_data = _list(
        _required(root, "findings", "REVIEW"), "findings"
    )
    findings: list[Finding] = []
    finding_ids: set[str] = set()
    for index, item in enumerate(findings_data):
        path = f"findings[{index}]"
        finding_data = _mapping(item, path)
        finding_id = _string(
            _required(finding_data, "id", path), f"{path}.id"
        )
        if finding_id in finding_ids:
            raise ReviewValidationError(
                f"findings contains duplicate id: {finding_id}"
            )
        finding_ids.add(finding_id)
        findings.append(
            Finding(
                id=finding_id,
                basis=_string(
                    _required(finding_data, "basis", path), f"{path}.basis"
                ),
                action=_choice(
                    _required(finding_data, "action", path),
                    f"{path}.action",
                    REMEDIATION_ACTIONS,
                ),
                location=_string(
                    _required(finding_data, "location", path),
                    f"{path}.location",
                ),
                issue=_string(
                    _required(finding_data, "issue", path), f"{path}.issue"
                ),
                expected=_string(
                    _required(finding_data, "expected", path),
                    f"{path}.expected",
                ),
            )
        )

    prior_finding_id = None
    if "prior_finding_id" in root and root["prior_finding_id"] is not None:
        prior_finding_id = _string(root["prior_finding_id"], "prior_finding_id")
        if mode != "DELTA":
            raise ReviewValidationError(
                "prior_finding_id is allowed only for DELTA review"
            )

    if verdict == "PASS" and findings:
        raise ReviewValidationError("PASS review must not contain findings")
    if verdict == "CHANGES_REQUIRED" and not findings:
        raise ReviewValidationError(
            "CHANGES_REQUIRED review must contain at least one finding"
        )

    return Review(
        review_id=_string(
            _required(root, "review_id", "REVIEW"), "review_id"
        ),
        reviewed_sha=_string(
            _required(root, "reviewed_sha", "REVIEW"), "reviewed_sha"
        ),
        mode=mode,
        verdict=verdict,
        acceptance=MappingProxyType(acceptance),
        findings=tuple(findings),
        prior_finding_id=prior_finding_id,
    )


def validate_review(
    *,
    task: Task,
    result: Result,
    review: Review,
    prior_review: Review | None = None,
) -> Review:
    """Bind a REVIEW to TASK acceptance and immutable RESULT state."""

    if not isinstance(review, Review):
        raise ReviewValidationError("review must be a Review")
    if isinstance(review, BatchReview):
        return validate_batch_review(task=task, result=result, review=review, prior_review=prior_review)
    if review.reviewed_sha != result.head_sha:
        raise ReviewValidationError(
            "reviewed_sha does not match immutable RESULT head_sha"
        )

    acceptance_ids = {criterion.id for criterion in task.acceptance}
    unknown_assessments = set(review.acceptance) - acceptance_ids
    if unknown_assessments:
        unknown = ", ".join(sorted(unknown_assessments))
        raise ReviewValidationError(
            f"acceptance mapping references unknown criteria: {unknown}"
        )
    if review.mode == "PRIMARY":
        missing_assessments = acceptance_ids - set(review.acceptance)
        if missing_assessments:
            missing = ", ".join(sorted(missing_assessments))
            raise ReviewValidationError(
                f"PRIMARY review is missing acceptance criteria: {missing}"
            )

    failed_acceptance = {
        acceptance_id
        for acceptance_id, outcome in review.acceptance.items()
        if outcome == "FAIL"
    }
    if review.verdict == "PASS" and failed_acceptance:
        failed = ", ".join(sorted(failed_acceptance))
        raise ReviewValidationError(
            f"PASS review contains failed acceptance criteria: {failed}"
        )
    if review.verdict == "CHANGES_REQUIRED" and not failed_acceptance:
        raise ReviewValidationError(
            "CHANGES_REQUIRED review must contain at least one acceptance FAIL"
        )

    for finding in review.findings:
        if finding.basis not in acceptance_ids:
            raise ReviewValidationError(
                f"{finding.id} basis references unknown acceptance: "
                f"{finding.basis}"
            )
        if (
            review.verdict == "CHANGES_REQUIRED"
            and finding.basis not in failed_acceptance
        ):
            raise ReviewValidationError(
                f"{finding.id} basis must reference an acceptance marked FAIL"
            )

    if review.prior_finding_id is not None:
        if prior_review is None:
            raise ReviewValidationError(
                "DELTA prior_finding_id requires a prior REVIEW"
            )
        prior_ids = {finding.id for finding in prior_review.findings}
        if review.prior_finding_id not in prior_ids:
            raise ReviewValidationError(
                f"unknown prior finding: {review.prior_finding_id}"
            )

    return review


def parse_remediation(source: str) -> Remediation:
    """Parse and structurally validate one YAML REMEDIATION document."""

    root = _mapping(_parse_yaml(source, "REMEDIATION"), "REMEDIATION")
    if _batch_document(root, "AIOS_BATCH_REMEDIATION"):
        return parse_batch_remediation(_strict_batch_yaml(source))
    modification_scope = _optional_remediation_list(
        root, "modification_scope", nested=("scope", "modify")
    )
    verification_policy = None
    full_suite_reason = None
    affected_verification = _optional_remediation_list(
        root, "affected_verification", nested=("verification", "affected")
    )
    if "verification" in root:
        verification = _mapping(root["verification"], "verification")
        if "policy" in verification:
            unknown = sorted(
                repr(key)
                for key in verification
                if key not in {"policy", "affected", "full_suite_reason"}
            )
            if unknown:
                raise ReviewValidationError(
                    "verification contains unknown field(s): " + ", ".join(unknown)
                )
            verification_policy = _string(
                verification["policy"], "verification.policy"
            )
            if verification_policy not in VERIFICATION_POLICIES:
                raise ReviewValidationError(
                    "verification.policy must be minimum-sufficient-v1 or minimum-sufficient-v2"
                )
            if "affected" not in verification:
                raise ReviewValidationError("verification.affected is required")
            affected_verification = _string_tuple(
                verification["affected"], "verification.affected"
            )
            if "full_suite_reason" in verification:
                full_suite_reason = _string(
                    verification["full_suite_reason"],
                    "verification.full_suite_reason",
                )
            try:
                validate_v1_verification(
                    affected_verification,
                    full_suite_reason=full_suite_reason,
                    path="verification.affected",
                )
            except VerificationContractError as exc:
                raise ReviewValidationError(str(exc)) from exc
        elif "required" in verification and not affected_verification:
            affected_verification = _string_tuple(
                verification["required"], "verification.required"
            )
    elif root.get("verification_policy") is not None:
        # Dataclass transports use this flat spelling when rehydrating the
        # already-validated canonical remediation contract.
        verification_policy = _string(
            root["verification_policy"], "verification_policy"
        )
        if verification_policy not in VERIFICATION_POLICIES:
            raise ReviewValidationError(
                "verification_policy must be minimum-sufficient-v1 or minimum-sufficient-v2"
            )
        if root.get("full_suite_reason") is not None:
            full_suite_reason = _string(
                root["full_suite_reason"], "full_suite_reason"
            )
        try:
            validate_v1_verification(
                affected_verification,
                full_suite_reason=full_suite_reason,
                path="affected_verification",
            )
        except VerificationContractError as exc:
            raise ReviewValidationError(str(exc)) from exc
    constraints: tuple[str, ...] = ()
    if "constraints" in root:
        value = root["constraints"]
        if isinstance(value, Mapping):
            constraints = _string_tuple(
                _required(value, "hard", "constraints"), "constraints.hard"
            )
        else:
            constraints = _string_tuple(value, "constraints")

    values = {
        "finding_id": _string(
            _required(root, "finding_id", "REMEDIATION"), "finding_id"
        ),
        "action": _choice(
            _required(root, "action", "REMEDIATION"),
            "action",
            REMEDIATION_ACTIONS,
        ),
        "reviewed_sha": _string(
            _required(root, "reviewed_sha", "REMEDIATION"),
            "reviewed_sha",
        ),
        "modification_scope": modification_scope,
        "affected_verification": affected_verification,
        "constraints": constraints,
    }
    if verification_policy is None:
        return Remediation(**values)
    return _PolicyRemediation(
        **values,
        verification_policy=verification_policy,
        full_suite_reason=full_suite_reason,
    )


def validate_remediation(
    *, review: Review, remediation: Remediation, task: Task | None = None
) -> Remediation:
    """Bind REMEDIATION to one explicit finding without executing it."""

    if isinstance(remediation, BatchRemediation):
        return validate_batch_remediation(review=review, remediation=remediation, task=task)

    if remediation.reviewed_sha != review.reviewed_sha:
        raise ReviewValidationError(
            "REMEDIATION reviewed_sha does not match REVIEW reviewed_sha"
        )
    findings = {finding.id: finding for finding in review.findings}
    finding = findings.get(remediation.finding_id)
    if finding is None:
        raise ReviewValidationError(
            f"unknown remediation finding: {remediation.finding_id}"
        )
    if remediation.action != finding.action:
        raise ReviewValidationError(
            "REMEDIATION action does not match finding action"
        )
    if task is not None:
        if task.verification.policy in VERIFICATION_POLICIES:
            if remediation.verification_policy != task.verification.policy:
                raise ReviewValidationError(
                    f"REMEDIATION for a {task.verification.policy} TASK must use "
                    f"verification.policy {task.verification.policy}"
                )
        outside_scope = set(remediation.modification_scope).difference(
            task.scope.modify
        )
        if outside_scope:
            raise ReviewValidationError(
                "REMEDIATION modification scope widens TASK.scope.modify: "
                + ", ".join(sorted(outside_scope))
            )
        unknown_constraints = set(remediation.constraints).difference(
            task.constraints.hard
        )
        if unknown_constraints:
            raise ReviewValidationError(
                "REMEDIATION constraints are not original TASK constraints: "
                + ", ".join(sorted(unknown_constraints))
            )
    return remediation


def _optional_remediation_list(
    root: Mapping[Any, Any], key: str, *, nested: tuple[str, str]
) -> tuple[str, ...]:
    if key in root:
        return _string_tuple(root[key], key)
    parent_key, child_key = nested
    if parent_key not in root:
        return ()
    parent = _mapping(root[parent_key], parent_key)
    if child_key not in parent:
        return ()
    return _string_tuple(parent[child_key], f"{parent_key}.{child_key}")


def _parse_yaml(source: str, document: str) -> Any:
    try:
        return yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ReviewValidationError(f"Invalid {document} YAML: {exc}") from exc


def _required(mapping: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise ReviewValidationError(f"{path}.{key} is required")
    return mapping[key]


def _mapping(value: Any, path: str) -> Mapping[Any, Any]:
    if not isinstance(value, Mapping):
        raise ReviewValidationError(f"{path} must be a mapping")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReviewValidationError(f"{path} must be a list")
    return value


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{path}[{index}]")
        for index, item in enumerate(_list(value, path))
    )


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _choice(value: Any, path: str, allowed: frozenset[str]) -> str:
    selected = _string(value, path)
    if selected not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ReviewValidationError(f"{path} must be one of: {choices}")
    return selected


# Prospective v1 extension. These objects deliberately serialize no singular
# finding/finding_id. The selector property is for existing transport plumbing;
# every semantic consumer must discriminate the concrete batch type.
BATCH_FORMAT = "AIOS_BATCH_REMEDIATION"
BATCH_REVIEW_FORMAT = "AIOS_MULTI_FINDING_DELTA_REVIEW"
_BATCH_ID = re.compile(r"BATCH-[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_BATCH_SELECTION_FIELDS = {
    "source_run_id", "review_id", "task_id", "task_revision", "reviewed_sha",
    "review_decision_sha", "source_artifacts_sha", "finding_ids",
}


def is_batch_selector(value: Any) -> bool:
    return isinstance(value, str) and _BATCH_ID.fullmatch(value) is not None


def _closed_batch(value: Any, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ReviewValidationError(f"{name} has unknown or missing fields")
    return value


def _strict_batch_yaml(source: str) -> Mapping[str, Any]:
    from .task import _TaskLoader, TaskValidationError
    if len(source.encode("utf-8")) > 262144:
        raise ReviewValidationError("batch document exceeds byte bound")
    try:
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(source)):
            raise ReviewValidationError("batch YAML aliases/anchors are forbidden")
        return yaml.load(source, Loader=_TaskLoader)
    except (yaml.YAMLError, TaskValidationError, RecursionError) as exc:
        raise ReviewValidationError(f"invalid strict batch document: {exc}") from exc


def _batch_document(root: Mapping[str, Any], expected: str) -> bool:
    reserved = {"selection", "batch", "batch_selection", "batch_id", "finding_ids", "prior_finding_ids",
                "selected_finding_ids", "finding_outcomes", "per_finding_scope"}
    if expected == BATCH_FORMAT:
        reserved.add("findings")
    if root.get("format") in (BATCH_FORMAT, BATCH_REVIEW_FORMAT) or reserved.intersection(root) or (
            isinstance(root.get("format"), str) and any(t in root["format"] for t in ("BATCH", "MULTI_FINDING"))):
        if root.get("format") != expected or type(root.get("version")) is not int or root["version"] != 1:
            raise ReviewValidationError("explicit supported batch format/version required")
        return True
    return False


def _batch_strings(value: Any, name: str, *, nonempty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ReviewValidationError(f"{name} must be an explicit list")
    items = tuple(value)
    if (nonempty and not items) or any(type(v) is not str or not v or v != v.strip() for v in items):
        raise ReviewValidationError(f"{name} contains invalid values")
    if len(set(items)) != len(items):
        raise ReviewValidationError(f"{name} contains duplicates")
    return items


@dataclass(frozen=True)
class BatchSelection:
    source_run_id: str
    review_id: str
    task_id: str
    task_revision: int
    reviewed_sha: str
    review_decision_sha: str
    source_artifacts_sha: str
    finding_ids: tuple[str, ...]

    @property
    def batch_id(self) -> str:
        body = asdict(self)
        return "BATCH-" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def finding_id(self) -> str:
        return self.batch_id


def parse_batch_selection(value: Any) -> BatchSelection:
    root = _closed_batch(value, _BATCH_SELECTION_FIELDS, "batch selection")
    ids = _batch_strings(root["finding_ids"], "finding_ids")
    if not 2 <= len(ids) <= 32 or any(not _IDENTITY.fullmatch(v) or is_batch_selector(v) for v in ids):
        raise ReviewValidationError("batch requires 2–32 distinct explicit finding identities")
    if (not isinstance(root["source_run_id"], str) or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", root["source_run_id"]) is None
            or not isinstance(root["task_id"], str) or re.fullmatch(r"TASK-[A-Za-z0-9_-]+", root["task_id"]) is None
            or type(root["task_revision"]) is not int or root["task_revision"] < 1
            or not isinstance(root["review_id"], str) or not _IDENTITY.fullmatch(root["review_id"])
            or any(not isinstance(root[k], str) or re.fullmatch(r"[0-9a-f]{40}", root[k]) is None
                   for k in ("reviewed_sha", "review_decision_sha", "source_artifacts_sha"))):
        raise ReviewValidationError("invalid exact batch source identity")
    if re.fullmatch(re.escape("RUN-" + root["task_id"].removeprefix("TASK-") + "-") + r"\d{3,}", root["source_run_id"]) is None:
        raise ReviewValidationError("batch source RUN must belong to the exact TASK namespace")
    return BatchSelection(**{k: root[k] for k in _BATCH_SELECTION_FIELDS if k != "finding_ids"},
                          finding_ids=tuple(sorted(ids)))


@dataclass(frozen=True)
class FindingScope:
    finding_id: str
    modification_scope: tuple[str, ...]


@dataclass(frozen=True)
class BatchRemediation:
    format: str
    version: int
    batch_id: str
    selection: BatchSelection
    findings: tuple[Finding, ...]
    per_finding_scope: tuple[FindingScope, ...]
    modification_scope: tuple[str, ...]
    affected_verification: tuple[str, ...]
    constraints: tuple[str, ...]
    action: str = "CODE_FIX"
    verification_policy: str = MINIMUM_SUFFICIENT_V1

    @property
    def finding_id(self) -> str:
        return self.batch_id

    @property
    def reviewed_sha(self) -> str:
        return self.selection.reviewed_sha

    @property
    def full_suite_reason(self) -> None:
        return None


@dataclass(frozen=True)
class BatchRemediationExecution:
    format: str
    version: int
    review_id: str
    findings: tuple[Finding, ...]
    remediation: BatchRemediation
    run: Run
    original_constraints: tuple[str, ...]


def parse_batch_remediation(root: Mapping[str, Any]) -> BatchRemediation:
    fields = {"format", "version", "batch_id", "selection", "findings", "per_finding_scope",
              "modification_scope", "affected_verification", "constraints", "action", "verification_policy"}
    _closed_batch(root, fields, "batch REMEDIATION")
    if (root["format"] != BATCH_FORMAT or type(root["version"]) is not int or root["version"] != 1
            or root["action"] != "CODE_FIX" or root["verification_policy"] != MINIMUM_SUFFICIENT_V1):
        raise ReviewValidationError("unsupported batch contract/action/policy")
    if len(json.dumps(root, default=list).encode()) > 262144:
        raise ReviewValidationError("batch REMEDIATION exceeds byte bound")
    selection = parse_batch_selection(root["selection"])
    if root["batch_id"] != selection.batch_id:
        raise ReviewValidationError("batch_id does not address exact selection")
    raw_findings = root["findings"]
    if not isinstance(raw_findings, (list, tuple)) or len(raw_findings) != len(selection.finding_ids):
        raise ReviewValidationError("exact batch finding subjects required")
    findings = []
    for item in raw_findings:
        _closed_batch(item, {"id", "basis", "action", "location", "issue", "expected"}, "batch finding")
        if any(type(v) is not str or not v or v != v.strip() for v in item.values()) or item["action"] != "CODE_FIX":
            raise ReviewValidationError("batch subjects must all be exact CODE_FIX findings")
        findings.append(Finding(**item))
    if tuple(f.id for f in findings) != selection.finding_ids:
        raise ReviewValidationError("batch subjects must match sorted explicit selection exactly")
    scopes = []
    if not isinstance(root["per_finding_scope"], (list, tuple)):
        raise ReviewValidationError("per_finding_scope must be explicit")
    for item in root["per_finding_scope"]:
        _closed_batch(item, {"finding_id", "modification_scope"}, "finding scope")
        paths = _batch_strings(item["modification_scope"], "finding modification_scope")
        if any(p.startswith(("/", "\\")) or "\\" in p or ":" in p or any(c in p for c in "*?[]")
               or any(ord(c) < 32 or ord(c) == 127 for c in p)
               or any(c in ("", ".", "..") for c in p.split("/")) for p in paths):
            raise ReviewValidationError("batch scope requires exact repository paths")
        scopes.append(FindingScope(item["finding_id"], paths))
    if tuple(s.finding_id for s in scopes) != selection.finding_ids:
        raise ReviewValidationError("one exact scope per selected finding required")
    combined = _batch_strings(root["modification_scope"], "modification_scope")
    if combined != tuple(sorted({p for s in scopes for p in s.modification_scope})):
        raise ReviewValidationError("batch scope must be exact sorted union of per-finding scopes")
    commands = _batch_strings(root["affected_verification"], "affected_verification")
    try:
        validate_v1_verification(commands, full_suite_reason=None, path="affected_verification")
    except VerificationContractError as exc:
        raise ReviewValidationError(str(exc)) from exc
    return BatchRemediation(BATCH_FORMAT, 1, selection.batch_id, selection, tuple(findings), tuple(scopes),
                            combined, commands, _batch_strings(root["constraints"], "constraints", nonempty=False))


def validate_batch_remediation(*, review: Review, remediation: BatchRemediation, task: Task | None) -> BatchRemediation:
    # Reparse even direct dataclass callers; frozen dataclasses are not authority.
    canonical = parse_batch_remediation(asdict(remediation))
    selected = canonical.selection
    if (review.verdict != "CHANGES_REQUIRED" or review.review_id != selected.review_id
            or review.reviewed_sha != selected.reviewed_sha):
        raise ReviewValidationError("batch source must be one exact CHANGES_REQUIRED REVIEW")
    source = {f.id: f for f in review.findings}
    if any(source.get(f.id) != f for f in canonical.findings):
        raise ReviewValidationError("batch contains substituted, foreign or mixed source findings")
    if task is None or (task.task_id, task.revision) != (selected.task_id, selected.task_revision):
        raise ReviewValidationError("batch requires exact original TASK/revision")
    if (task.verification.policy != MINIMUM_SUFFICIENT_V1
            or canonical.affected_verification != tuple(task.verification.required)
            or canonical.constraints != tuple(task.constraints.hard)
            or set(canonical.modification_scope) - set(task.scope.modify)):
        raise ReviewValidationError("batch must preserve original scope, hard constraints and minimum-sufficient-v1 commands")
    return canonical


@dataclass(frozen=True)
class BatchReview(Review):
    format: str = BATCH_REVIEW_FORMAT
    version: int = 1
    batch_id: str = ""
    selection: BatchSelection | None = None
    finding_outcomes: Mapping[str, str] = None


def _parse_batch_review(root: Mapping[str, Any]) -> BatchReview:
    _closed_batch(root, {"format", "version", "review_id", "reviewed_sha", "mode", "verdict", "acceptance",
                         "findings", "batch_id", "selection", "finding_outcomes"}, "multi-finding DELTA REVIEW")
    selection = parse_batch_selection(root["selection"])
    if (not isinstance(root["review_id"], str) or not _IDENTITY.fullmatch(root["review_id"])
            or not isinstance(root["reviewed_sha"], str) or re.fullmatch(r"[0-9a-f]{40}", root["reviewed_sha"]) is None):
        raise ReviewValidationError("batch REVIEW requires exact canonical identity and candidate SHA")
    outcomes = _mapping(root["finding_outcomes"], "finding_outcomes")
    if root["batch_id"] != selection.batch_id or set(outcomes) != set(selection.finding_ids):
        raise ReviewValidationError("batch REVIEW requires every selected outcome exactly once")
    if any(type(v) is not str or v not in REVIEW_VERDICTS for v in outcomes.values()) or root["mode"] != "DELTA":
        raise ReviewValidationError("batch REVIEW requires DELTA and independent per-finding verdicts")
    # Parse shared fields with the unchanged legacy decoder. Unresolved source
    # findings are represented by outcomes, not minted replacement identities.
    shared = {k: root[k] for k in ("review_id", "reviewed_sha", "mode", "verdict", "acceptance", "findings")}
    raw_verdict = shared["verdict"]
    if raw_verdict == "CHANGES_REQUIRED" and not shared["findings"]:
        shared["verdict"] = "BLOCKED"
    legacy = parse_review(yaml.safe_dump(shared))
    if len(legacy.findings) > 32 or any(not _IDENTITY.fullmatch(f.id) for f in legacy.findings):
        raise ReviewValidationError("batch REVIEW new material defects exceed identity/count bounds")
    expected = ("BLOCKED" if "BLOCKED" in outcomes.values() else "CHANGES_REQUIRED"
                if "CHANGES_REQUIRED" in outcomes.values() or legacy.findings else "PASS")
    if raw_verdict != expected or set(selection.finding_ids).intersection(f.id for f in legacy.findings):
        raise ReviewValidationError("contradictory batch verdict or reused original finding identity")
    return BatchReview(legacy.review_id, legacy.reviewed_sha, "DELTA", raw_verdict, legacy.acceptance,
                       legacy.findings, None, BATCH_REVIEW_FORMAT, 1, selection.batch_id, selection,
                       MappingProxyType(dict(sorted(outcomes.items()))))


def validate_batch_review(*, task: Task, result: Result, review: BatchReview, prior_review: Review | None) -> BatchReview:
    if (review.format != BATCH_REVIEW_FORMAT or type(review.version) is not int or review.version != 1
            or review.selection is None or review.batch_id != review.selection.batch_id
            or review.mode != "DELTA" or review.prior_finding_id is not None
            or review.reviewed_sha != result.head_sha or prior_review is None
            or prior_review.review_id != review.selection.review_id
            or prior_review.reviewed_sha != review.selection.reviewed_sha
            or prior_review.verdict != "CHANGES_REQUIRED"
            or (task.task_id, task.revision) != (review.selection.task_id, review.selection.task_revision)
            or set(review.finding_outcomes or {}) != set(review.selection.finding_ids)):
        raise ReviewValidationError("multi-finding REVIEW exact source/outcome binding failed")
    if parse_batch_selection(asdict(review.selection)) != review.selection:
        raise ReviewValidationError("batch REVIEW selection is not canonical")
    source = {f.id: f for f in prior_review.findings}
    if any(i not in source or source[i].action != "CODE_FIX" for i in review.selection.finding_ids):
        raise ReviewValidationError("multi-finding REVIEW contains foreign or mixed subjects")
    expected = ("BLOCKED" if "BLOCKED" in review.finding_outcomes.values() else "CHANGES_REQUIRED"
                if "CHANGES_REQUIRED" in review.finding_outcomes.values() or review.findings else "PASS")
    if review.verdict != expected or any(type(v) is not str or v not in REVIEW_VERDICTS for v in review.finding_outcomes.values()):
        raise ReviewValidationError("contradictory per-finding outcomes")
    ids = {c.id for c in task.acceptance}
    bases = {source[i].basis for i in review.selection.finding_ids}
    if not bases.issubset(review.acceptance) or set(review.acceptance) - ids:
        raise ReviewValidationError("batch REVIEW must assess every selected finding basis")
    if any(type(v) is not str or v not in ACCEPTANCE_RESULTS for v in review.acceptance.values()) or len({f.id for f in review.findings}) != len(review.findings):
        raise ReviewValidationError("batch REVIEW contains invalid assessments or duplicated new defects")
    required_fail = {source[i].basis for i, v in review.finding_outcomes.items() if v == "CHANGES_REQUIRED"}
    if any(review.acceptance.get(b) != "FAIL" for b in required_fail):
        raise ReviewValidationError("unresolved batch finding requires acceptance FAIL")
    for f in review.findings:
        if f.id in source or f.basis not in ids or review.acceptance.get(f.basis) != "FAIL":
            raise ReviewValidationError("new material defect must be distinct and assess a TASK basis as FAIL")
    if review.verdict == "PASS" and (review.findings or any(v != "PASS" for v in review.acceptance.values())):
        raise ReviewValidationError("PASS batch REVIEW contains outstanding assessment")
    return review


def validate_batch_evidence(*, run: Run, remediation: BatchRemediation, result: Result,
                            evidence: tuple[Any, ...], actual_head: str) -> None:
    """One successful Runtime verification episode, exactly on this batch candidate."""
    commands = remediation.affected_verification
    if (result.head_sha != actual_head or run.head_sha not in (None, actual_head)
            or result.claims or result.unresolved or len(evidence) != len(commands)
            or len({e.evidence_id for e in evidence}) != len(evidence)
            or tuple(e.source.command for e in evidence) != commands
            or any(e.type != "VERIFICATION" or e.run_id != run.run_id or e.subject_sha != actual_head
                   or type(e.result.exit_code) is not int or e.result.exit_code != 0 for e in evidence)):
        raise ReviewValidationError("batch requires one exact successful minimum-sufficient verification episode bound to RUN/RESULT")
