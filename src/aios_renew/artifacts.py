"""Canonical RESULT and EVIDENCE contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from .run import Run
from .task import Task
from .verification_contract import (
    MINIMUM_SUFFICIENT_V2, attributed_task_passes, verification_digest,
    VerificationContractError,
    parse_pytest_coverage,
    validate_evidence_binding, validate_observation,
)


class ArtifactValidationError(ValueError):
    """Raised when RESULT or EVIDENCE violates its canonical contract."""


@dataclass(frozen=True)
class Claim:
    id: str
    satisfies: tuple[str, ...]
    claim: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Result:
    head_sha: str
    claims: tuple[Claim, ...]
    changed_files: tuple[str, ...]
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceSource:
    command: str


@dataclass(frozen=True)
class EvidenceOutcome:
    exit_code: int
    summary: str


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    run_id: str
    subject_sha: str
    type: str
    source: EvidenceSource
    result: EvidenceOutcome
    raw_path: str
    verification: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResultPackage:
    result: Result
    evidence: tuple[Evidence, ...]


def parse_result(source: str) -> Result:
    """Parse and validate one YAML RESULT document."""

    return validate_result(_parse_yaml(source, "RESULT"))


def validate_result(data: Any) -> Result:
    """Validate decoded RESULT data."""

    return _validate_result(data, require_claim_evidence=True)


def validate_structural_result(data: Any) -> Result:
    """Validate executor-produced RESULT structure before Runtime verification."""

    return _validate_result(data, require_claim_evidence=False)


def _validate_result(data: Any, *, require_claim_evidence: bool) -> Result:
    """Validate RESULT data at either the structural or canonical stage."""

    root = _mapping(data, "RESULT")
    claims_data = _list(_required(root, "claims", "RESULT"), "claims")
    claims: list[Claim] = []
    claim_ids: set[str] = set()

    for index, item in enumerate(claims_data):
        path = f"claims[{index}]"
        claim_data = _mapping(item, path)
        claim_id = _string(_required(claim_data, "id", path), f"{path}.id")
        if claim_id in claim_ids:
            raise ArtifactValidationError(f"claims contains duplicate id: {claim_id}")
        claim_ids.add(claim_id)

        satisfies = _string_list(
            _required(claim_data, "satisfies", path), f"{path}.satisfies"
        )
        evidence = _string_list(
            _required(claim_data, "evidence", path), f"{path}.evidence"
        )
        if not satisfies:
            raise ArtifactValidationError(f"{path}.satisfies must not be empty")
        if require_claim_evidence and not evidence:
            raise ArtifactValidationError(f"{path}.evidence must not be empty")

        claims.append(
            Claim(
                id=claim_id,
                satisfies=satisfies,
                claim=_string(
                    _required(claim_data, "claim", path), f"{path}.claim"
                ),
                evidence=evidence,
            )
        )

    return Result(
        head_sha=_string(_required(root, "head_sha", "RESULT"), "head_sha"),
        claims=tuple(claims),
        changed_files=_string_list(
            _required(root, "changed_files", "RESULT"), "changed_files"
        ),
        unresolved=_string_list(
            _required(root, "unresolved", "RESULT"), "unresolved"
        ),
    )


def parse_evidence(source: str) -> Evidence:
    """Parse and validate one YAML EVIDENCE document."""

    return validate_evidence(_parse_yaml(source, "EVIDENCE"))


def validate_evidence(data: Any) -> Evidence:
    """Validate decoded EVIDENCE data."""

    root = _mapping(data, "EVIDENCE")
    source = _mapping(_required(root, "source", "EVIDENCE"), "source")
    outcome = _mapping(_required(root, "result", "EVIDENCE"), "result")
    raw = _mapping(_required(root, "raw", "EVIDENCE"), "raw")
    exit_code = _required(outcome, "exit_code", "result")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ArtifactValidationError("result.exit_code must be an integer")
    verification = root.get("verification")
    if verification is not None:
        if not isinstance(verification, dict) or verification.get("policy") != MINIMUM_SUFFICIENT_V2:
            raise ArtifactValidationError("invalid Runtime verification record")
        try:
            verification_digest(verification)
            binding = validate_evidence_binding(verification.get("binding"))
            candidate = verification["candidate"]
            if candidate.get("complete") is True:
                validate_observation(candidate)
            if (verification.get("candidate_digest") != verification_digest(candidate)
                    or any(candidate.get(k) != binding[k] for k in ("subject_sha", "command", "profile", "toolchain"))
                    or candidate.get("exit_code") != exit_code
                    or binding["command"] != source.get("command")):
                raise VerificationContractError("verification observation binding mismatch")
            effective = binding
            if "reuse" in verification:
                effective = validate_evidence_binding(verification["reuse"].get("binding"))
                if ({k: v for k, v in effective.items() if k != "subject_sha"}
                        != {k: v for k, v in binding.items() if k != "subject_sha"}
                        or verification["reuse"].get("source_evidence_id") != verification.get("evidence_id")):
                    raise VerificationContractError("invalid evidence reuse binding")
            if effective["subject_sha"] != root.get("subject_sha"):
                raise VerificationContractError("verification subject mismatch")
            if "base" in verification:
                if (verification.get("base_digest") != verification_digest(verification["base"])
                        or verification["base"].get("subject_sha") != binding["base_sha"]
                        or binding["failure_set_digest"] != verification["base_digest"]):
                    raise VerificationContractError("verification base binding mismatch")
        except (VerificationContractError, TypeError, ValueError, KeyError, AttributeError) as exc:
            raise ArtifactValidationError("invalid or unbounded Runtime verification record") from exc

    return Evidence(
        evidence_id=_string(
            _required(root, "evidence_id", "EVIDENCE"), "evidence_id"
        ),
        run_id=_string(_required(root, "run_id", "EVIDENCE"), "run_id"),
        subject_sha=_string(
            _required(root, "subject_sha", "EVIDENCE"), "subject_sha"
        ),
        type=_string(_required(root, "type", "EVIDENCE"), "type"),
        source=EvidenceSource(
            command=_exact_string(
                _required(source, "command", "source"), "source.command"
            )
        ),
        result=EvidenceOutcome(
            exit_code=exit_code,
            summary=_string(
                _required(outcome, "summary", "result"), "result.summary"
            ),
        ),
        raw_path=_string(_required(raw, "path", "raw"), "raw.path"),
        verification=verification,
    )


def validate_result_package(
    *,
    task: Task,
    run: Run,
    result: Result,
    evidence: Iterable[Evidence],
) -> ResultPackage:
    """Bind RESULT and EVIDENCE to their TASK, RUN, and immutable SHA."""

    if not isinstance(result, Result):
        raise ArtifactValidationError("result must be a Result")
    items = tuple(evidence)
    if not all(isinstance(item, Evidence) for item in items):
        raise ArtifactValidationError("evidence must contain only Evidence")
    if run.task.id != task.task_id or run.task.revision != task.revision:
        raise ArtifactValidationError("RUN does not reference the supplied TASK")
    if run.head_sha is not None and run.head_sha != result.head_sha:
        raise ArtifactValidationError("RESULT head_sha does not match RUN head_sha")

    evidence_by_id: dict[str, Evidence] = {}
    for item in items:
        if item.evidence_id in evidence_by_id:
            raise ArtifactValidationError(
                f"duplicate evidence_id: {item.evidence_id}"
            )
        evidence_by_id[item.evidence_id] = item
        if item.run_id != run.run_id:
            raise ArtifactValidationError(
                f"{item.evidence_id} does not reference RUN {run.run_id}"
            )
        if item.subject_sha != result.head_sha:
            raise ArtifactValidationError(
                f"{item.evidence_id} subject_sha does not match RESULT head_sha"
            )
        if (task.verification.policy == MINIMUM_SUFFICIENT_V2 and item.verification is not None
                and item.verification.get("binding", {}).get("base_sha") != run.base_sha):
            raise ArtifactValidationError("V2 attribution base does not match RUN base_sha")

    acceptance_ids = {criterion.id for criterion in task.acceptance}
    for claim in result.claims:
        if not claim.satisfies:
            raise ArtifactValidationError(
                f"claim {claim.id} satisfies must not be empty"
            )
        if not claim.evidence:
            raise ArtifactValidationError(
                f"claim {claim.id} evidence must not be empty"
            )
        unknown_acceptance = set(claim.satisfies) - acceptance_ids
        if unknown_acceptance:
            unknown = ", ".join(sorted(unknown_acceptance))
            raise ArtifactValidationError(
                f"{claim.id} references unknown acceptance criteria: {unknown}"
            )
        missing_evidence = set(claim.evidence) - evidence_by_id.keys()
        if missing_evidence:
            missing = ", ".join(sorted(missing_evidence))
            raise ArtifactValidationError(
                f"{claim.id} references missing evidence: {missing}"
            )

    for command in task.verification.required:
        matching = [item for item in items if item.source.command == command]
        if not matching:
            raise ArtifactValidationError(
                f"missing verification evidence for required command: {command}"
            )
        if not any(verification_requirement_passes(item, policy=task.verification.policy) for item in matching):
            raise ArtifactValidationError(
                f"required verification command has no successful evidence: {command}"
            )
    if task.verification.policy == MINIMUM_SUFFICIENT_V2:
        for item in items:
            if item.type == "VERIFICATION" and not verification_requirement_passes(item, policy=task.verification.policy):
                raise ArtifactValidationError("blocking V2 observation cannot appear in canonical RESULT")

    return ResultPackage(result=result, evidence=items)


def verification_requirement_passes(item: Evidence, *, policy: str | None) -> bool:
    """Keep legacy raw success semantics; V2 recomputes attribution separately."""
    if policy != MINIMUM_SUFFICIENT_V2:
        return item.result.exit_code == 0
    # Opaque authored commands have no comparable test observation. They still
    # require ordinary raw success, and can never use nonzero attribution.
    if item.verification is None:
        coverage = parse_pytest_coverage(item.source.command)
        return item.result.exit_code == 0 and (coverage is None or coverage.measurement)
    return attributed_task_passes(item.verification, subject_sha=item.subject_sha,
                                 command=item.source.command, exit_code=item.result.exit_code)


def validate_structural_result_package(
    *,
    task: Task,
    run: Run,
    result: Result,
    evidence: Iterable[Evidence],
) -> ResultPackage:
    """Bind executor structure to TASK/RUN without accepting it as canonical proof."""

    if not isinstance(result, Result):
        raise ArtifactValidationError("result must be a Result")
    items = tuple(evidence)
    if not all(isinstance(item, Evidence) for item in items):
        raise ArtifactValidationError("evidence must contain only Evidence")
    if run.task.id != task.task_id or run.task.revision != task.revision:
        raise ArtifactValidationError("RUN does not reference the supplied TASK")
    if run.head_sha is not None and run.head_sha != result.head_sha:
        raise ArtifactValidationError("RESULT head_sha does not match RUN head_sha")

    evidence_by_id: dict[str, Evidence] = {}
    for item in items:
        if item.evidence_id in evidence_by_id:
            raise ArtifactValidationError(
                f"duplicate evidence_id: {item.evidence_id}"
            )
        evidence_by_id[item.evidence_id] = item
        if item.run_id != run.run_id:
            raise ArtifactValidationError(
                f"{item.evidence_id} does not reference RUN {run.run_id}"
            )
        if item.subject_sha != result.head_sha:
            raise ArtifactValidationError(
                f"{item.evidence_id} subject_sha does not match RESULT head_sha"
            )

    acceptance_ids = {criterion.id for criterion in task.acceptance}
    for claim in result.claims:
        unknown_acceptance = set(claim.satisfies) - acceptance_ids
        if unknown_acceptance:
            unknown = ", ".join(sorted(unknown_acceptance))
            raise ArtifactValidationError(
                f"{claim.id} references unknown acceptance criteria: {unknown}"
            )
        missing_evidence = set(claim.evidence) - evidence_by_id.keys()
        if missing_evidence:
            missing = ", ".join(sorted(missing_evidence))
            raise ArtifactValidationError(
                f"{claim.id} references missing evidence: {missing}"
            )

    return ResultPackage(result=result, evidence=items)


def _parse_yaml(source: str, document: str) -> Any:
    try:
        return yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ArtifactValidationError(f"Invalid {document} YAML: {exc}") from exc


def _required(mapping: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise ArtifactValidationError(f"{path}.{key} is required")
    return mapping[key]


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactValidationError(f"{path} must be a mapping")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ArtifactValidationError(f"{path} must be a list")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _exact_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactValidationError(f"{path} must be a non-empty string")
    return value


def _string_list(value: Any, path: str) -> tuple[str, ...]:
    items = _list(value, path)
    return tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(items))
