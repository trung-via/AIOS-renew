"""Canonical REVIEW and REMEDIATION contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import yaml

from .artifacts import Result
from .run import Run
from .task import Task


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
    modification_scope = _optional_remediation_list(
        root, "modification_scope", nested=("scope", "modify")
    )
    affected_verification = _optional_remediation_list(
        root, "affected_verification", nested=("verification", "affected")
    )
    if not affected_verification and "verification" in root:
        verification = _mapping(root["verification"], "verification")
        if "required" in verification:
            affected_verification = _string_tuple(
                verification["required"], "verification.required"
            )
    constraints: tuple[str, ...] = ()
    if "constraints" in root:
        value = root["constraints"]
        if isinstance(value, Mapping):
            constraints = _string_tuple(
                _required(value, "hard", "constraints"), "constraints.hard"
            )
        else:
            constraints = _string_tuple(value, "constraints")

    return Remediation(
        finding_id=_string(
            _required(root, "finding_id", "REMEDIATION"), "finding_id"
        ),
        action=_choice(
            _required(root, "action", "REMEDIATION"),
            "action",
            REMEDIATION_ACTIONS,
        ),
        reviewed_sha=_string(
            _required(root, "reviewed_sha", "REMEDIATION"),
            "reviewed_sha",
        ),
        modification_scope=modification_scope,
        affected_verification=affected_verification,
        constraints=constraints,
    )


def validate_remediation(
    *, review: Review, remediation: Remediation, task: Task | None = None
) -> Remediation:
    """Bind REMEDIATION to one explicit finding without executing it."""

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
