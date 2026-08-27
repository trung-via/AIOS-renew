import pytest

from aios_renew import (
    ArtifactValidationError,
    ResultPackage,
    Run,
    parse_evidence,
    parse_result,
    parse_task,
    validate_result_package,
)


TASK_SOURCE = """
task_id: TASK-005
revision: 1
goal: Define canonical RESULT and EVIDENCE contracts.
problem: Executor claims require immutable proof.
assumptions: []
scope:
  inspect: []
  modify:
    - src/aios_renew/artifacts.py
non_goals:
  - Semantic review.
constraints:
  hard:
    - Claims require evidence.
acceptance:
  - id: AC1
    condition: RESULT claims bind to acceptance and evidence.
verification:
  required:
    - pytest tests/test_artifacts.py
"""

RESULT_SOURCE = """
head_sha: def456
claims:
  - id: C1
    satisfies:
      - AC1
    claim: RESULT and EVIDENCE are implemented.
    evidence:
      - E1
changed_files:
  - src/aios_renew/artifacts.py
unresolved: []
"""

EVIDENCE_SOURCE = """
evidence_id: E1
run_id: RUN-005-001
subject_sha: def456
type: TEST
source:
  command: pytest tests/test_artifacts.py
result:
  exit_code: 0
  summary: 6 passed
raw:
  path: .ai/evidence/E1.log
"""


def make_contracts():
    task = parse_task(TASK_SOURCE)
    run = Run.from_task(
        run_id="RUN-005-001",
        task=task,
        executor="codex",
        base_sha="abc123",
        workspace="C:/workspace",
    )
    return task, run, parse_result(RESULT_SOURCE), parse_evidence(EVIDENCE_SOURCE)


def test_parses_result_contract() -> None:
    result = parse_result(RESULT_SOURCE)

    assert result.head_sha == "def456"
    assert result.claims[0].satisfies == ("AC1",)
    assert result.claims[0].evidence == ("E1",)


def test_parses_evidence_contract() -> None:
    evidence = parse_evidence(EVIDENCE_SOURCE)

    assert evidence.run_id == "RUN-005-001"
    assert evidence.result.exit_code == 0
    assert evidence.raw_path == ".ai/evidence/E1.log"


def test_builds_sha_bound_result_package() -> None:
    task, run, result, evidence = make_contracts()

    package = validate_result_package(
        task=task,
        run=run,
        result=result,
        evidence=[evidence],
    )

    assert package == ResultPackage(result=result, evidence=(evidence,))


def test_rejects_claim_without_evidence_reference() -> None:
    invalid = RESULT_SOURCE.replace("evidence:\n      - E1", "evidence: []")

    with pytest.raises(ArtifactValidationError, match="evidence must not be empty"):
        parse_result(invalid)


def test_rejects_missing_referenced_evidence() -> None:
    task, run, result, _ = make_contracts()

    with pytest.raises(ArtifactValidationError, match="missing evidence: E1"):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[],
        )


def test_rejects_evidence_for_different_sha() -> None:
    task, run, result, _ = make_contracts()
    wrong_sha = parse_evidence(
        EVIDENCE_SOURCE.replace("subject_sha: def456", "subject_sha: other")
    )

    with pytest.raises(ArtifactValidationError, match="subject_sha"):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[wrong_sha],
        )


def test_rejects_unknown_acceptance_reference() -> None:
    task, run, _, evidence = make_contracts()
    result = parse_result(RESULT_SOURCE.replace("- AC1", "- AC9"))

    with pytest.raises(ArtifactValidationError, match="unknown acceptance.*AC9"):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[evidence],
        )


def test_rejects_missing_required_verification_evidence() -> None:
    task, run, result, _ = make_contracts()
    unrelated = parse_evidence(
        EVIDENCE_SOURCE.replace(
            "command: pytest tests/test_artifacts.py",
            "command: pytest tests/test_executor.py",
        )
    )

    with pytest.raises(
        ArtifactValidationError, match="missing verification evidence"
    ):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[unrelated],
        )


def test_rejects_failed_required_verification_evidence() -> None:
    task, run, result, _ = make_contracts()
    failed = parse_evidence(EVIDENCE_SOURCE.replace("exit_code: 0", "exit_code: 1"))

    with pytest.raises(ArtifactValidationError, match="no successful evidence"):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[failed],
        )


def test_required_verification_uses_exact_command_equality() -> None:
    task, run, result, _ = make_contracts()
    different = parse_evidence(
        EVIDENCE_SOURCE.replace(
            "command: pytest tests/test_artifacts.py",
            "command: pytest  tests/test_artifacts.py",
        )
    )

    with pytest.raises(
        ArtifactValidationError, match="missing verification evidence"
    ):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[different],
        )


def test_allows_extra_evidence_with_successful_required_verification() -> None:
    task, run, result, required = make_contracts()
    extra = parse_evidence(
        EVIDENCE_SOURCE.replace("evidence_id: E1", "evidence_id: E2").replace(
            "command: pytest tests/test_artifacts.py",
            "command: git diff --check",
        )
    )

    package = validate_result_package(
        task=task,
        run=run,
        result=result,
        evidence=[required, extra],
    )

    assert package.evidence == (required, extra)


def test_verification_evidence_need_not_be_referenced_by_claim() -> None:
    task, run, result, required = make_contracts()
    claim_evidence = parse_evidence(
        EVIDENCE_SOURCE.replace("evidence_id: E1", "evidence_id: E2").replace(
            "command: pytest tests/test_artifacts.py",
            "command: manual acceptance inspection",
        )
    )
    result = parse_result(RESULT_SOURCE.replace("- E1", "- E2"))

    package = validate_result_package(
        task=task,
        run=run,
        result=result,
        evidence=[claim_evidence, required],
    )

    assert package.result == result
