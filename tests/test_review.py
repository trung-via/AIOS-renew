import pytest

from aios_renew import (
    ReviewValidationError,
    parse_remediation,
    parse_result,
    parse_review,
    parse_task,
    validate_remediation,
    validate_review,
)


TASK_SOURCE = """
task_id: TASK-006
revision: 1
goal: Define REVIEW and REMEDIATION contracts.
problem: Review judgments need immutable, validated records.
assumptions: []
scope:
  inspect: []
  modify:
    - src/aios_renew/review.py
non_goals:
  - Review orchestration.
constraints:
  hard:
    - Do not execute remediation.
acceptance:
  - id: AC1
    condition: REVIEW binds to immutable state.
  - id: AC2
    condition: REMEDIATION references one valid finding.
verification:
  required:
    - pytest tests/test_review.py
"""

RESULT_SOURCE = """
head_sha: def456
claims: []
changed_files:
  - src/aios_renew/review.py
unresolved: []
"""

PASS_REVIEW = """
review_id: REVIEW-006-001
reviewed_sha: def456
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
  AC2: PASS
findings: []
"""

CHANGES_REVIEW = """
review_id: REVIEW-006-001
reviewed_sha: def456
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
  AC2: PASS
findings:
  - id: R1
    basis: AC1
    action: CODE_FIX
    location: src/aios_renew/review.py
    issue: reviewed_sha is not checked.
    expected: Bind REVIEW to RESULT head_sha.
"""


def contracts():
    return parse_task(TASK_SOURCE), parse_result(RESULT_SOURCE)


def test_validates_primary_pass_against_result_sha() -> None:
    task, result = contracts()
    review = parse_review(PASS_REVIEW)

    assert validate_review(task=task, result=result, review=review) is review


def test_rejects_reviewed_sha_mismatch() -> None:
    task, result = contracts()
    review = parse_review(PASS_REVIEW.replace("def456", "other"))

    with pytest.raises(ReviewValidationError, match="immutable RESULT head_sha"):
        validate_review(task=task, result=result, review=review)


def test_rejects_finding_basis_outside_task_acceptance() -> None:
    task, result = contracts()
    review = parse_review(CHANGES_REVIEW.replace("basis: AC1", "basis: AC9"))

    with pytest.raises(ReviewValidationError, match="unknown acceptance: AC9"):
        validate_review(task=task, result=result, review=review)


def test_rejects_pass_with_failed_acceptance() -> None:
    task, result = contracts()
    review = parse_review(PASS_REVIEW.replace("AC1: PASS", "AC1: FAIL"))

    with pytest.raises(ReviewValidationError, match="PASS.*failed.*AC1"):
        validate_review(task=task, result=result, review=review)


def test_rejects_primary_review_missing_acceptance() -> None:
    task, result = contracts()
    review = parse_review(PASS_REVIEW.replace("  AC2: PASS\n", ""))

    with pytest.raises(ReviewValidationError, match="missing.*AC2"):
        validate_review(task=task, result=result, review=review)


def test_rejects_finding_whose_basis_is_marked_pass() -> None:
    task, result = contracts()
    review = parse_review(
        CHANGES_REVIEW.replace("AC1: FAIL", "AC1: PASS").replace(
            "AC2: PASS", "AC2: FAIL"
        )
    )

    with pytest.raises(ReviewValidationError, match="R1.*marked FAIL"):
        validate_review(task=task, result=result, review=review)


def test_rejects_changes_required_without_failed_acceptance() -> None:
    task, result = contracts()
    review = parse_review(CHANGES_REVIEW.replace("AC1: FAIL", "AC1: PASS"))

    with pytest.raises(ReviewValidationError, match="at least one acceptance FAIL"):
        validate_review(task=task, result=result, review=review)


def test_pass_cannot_contain_finding() -> None:
    invalid = CHANGES_REVIEW.replace("verdict: CHANGES_REQUIRED", "verdict: PASS")

    with pytest.raises(ReviewValidationError, match="PASS.*must not"):
        parse_review(invalid)


def test_changes_required_must_contain_finding() -> None:
    invalid = PASS_REVIEW.replace("verdict: PASS", "verdict: CHANGES_REQUIRED")

    with pytest.raises(ReviewValidationError, match="at least one finding"):
        parse_review(invalid)


@pytest.mark.parametrize("action", ["CODE_FIX", "EVIDENCE_ONLY"])
def test_allows_only_canonical_finding_actions(action: str) -> None:
    review = parse_review(CHANGES_REVIEW.replace("CODE_FIX", action))

    assert review.findings[0].action == action


def test_rejects_unknown_finding_action() -> None:
    with pytest.raises(ReviewValidationError, match=r"findings\[0\]\.action"):
        parse_review(CHANGES_REVIEW.replace("CODE_FIX", "RETRY"))


def test_allows_blocked_verdict_without_finding() -> None:
    review = parse_review(PASS_REVIEW.replace("verdict: PASS", "verdict: BLOCKED"))

    assert review.verdict == "BLOCKED"


@pytest.mark.parametrize(
    ("original", "invalid", "path"),
    [
        ("mode: PRIMARY", "mode: SECONDARY", "mode must be one of"),
        ("verdict: PASS", "verdict: RETRY", "verdict must be one of"),
    ],
)
def test_rejects_noncanonical_mode_and_verdict(
    original: str, invalid: str, path: str
) -> None:
    with pytest.raises(ReviewValidationError, match=path):
        parse_review(PASS_REVIEW.replace(original, invalid))


def test_delta_review_can_reference_prior_finding() -> None:
    task, result = contracts()
    prior = parse_review(CHANGES_REVIEW)
    delta = parse_review(
        PASS_REVIEW.replace("mode: PRIMARY", "mode: DELTA").replace(
            "findings: []", "prior_finding_id: R1\nfindings: []"
        )
    )

    assert (
        validate_review(
            task=task,
            result=result,
            review=delta,
            prior_review=prior,
        )
        is delta
    )


def test_delta_review_rejects_unknown_prior_finding() -> None:
    task, result = contracts()
    prior = parse_review(CHANGES_REVIEW)
    delta = parse_review(
        PASS_REVIEW.replace("mode: PRIMARY", "mode: DELTA").replace(
            "findings: []", "prior_finding_id: R9\nfindings: []"
        )
    )

    with pytest.raises(ReviewValidationError, match="unknown prior finding: R9"):
        validate_review(
            task=task,
            result=result,
            review=delta,
            prior_review=prior,
        )


@pytest.mark.parametrize("action", ["CODE_FIX", "EVIDENCE_ONLY"])
def test_remediation_binds_to_finding_action_and_sha(action: str) -> None:
    review = parse_review(CHANGES_REVIEW.replace("CODE_FIX", action))
    remediation = parse_remediation(
        f"""
finding_id: R1
action: {action}
reviewed_sha: def456
"""
    )

    assert validate_remediation(review=review, remediation=remediation) is remediation


def test_remediation_carries_narrow_scope_verification_and_constraints() -> None:
    task, _ = contracts()
    review = parse_review(CHANGES_REVIEW)
    remediation = parse_remediation(
        """
finding_id: R1
action: CODE_FIX
reviewed_sha: def456
scope:
  modify:
    - src/aios_renew/review.py
verification:
  affected:
    - pytest tests/test_review.py
constraints:
  hard:
    - Do not execute remediation.
"""
    )

    assert remediation.modification_scope == ("src/aios_renew/review.py",)
    assert remediation.affected_verification == (
        "pytest tests/test_review.py",
    )
    assert validate_remediation(
        review=review, remediation=remediation, task=task
    ) is remediation


def test_remediation_scope_cannot_widen_original_task() -> None:
    task, _ = contracts()
    review = parse_review(CHANGES_REVIEW)
    remediation = parse_remediation(
        """
finding_id: R1
action: CODE_FIX
reviewed_sha: def456
modification_scope: [outside.py]
affected_verification: [pytest tests/test_review.py]
"""
    )

    with pytest.raises(ReviewValidationError, match="widens TASK.scope.modify"):
        validate_remediation(review=review, remediation=remediation, task=task)


def test_rejects_noncanonical_remediation_action() -> None:
    with pytest.raises(ReviewValidationError, match="action must be one of"):
        parse_remediation(
            """
finding_id: R1
action: RETRY
reviewed_sha: def456
"""
        )


def test_rejects_remediation_for_unknown_finding() -> None:
    review = parse_review(CHANGES_REVIEW)
    remediation = parse_remediation(
        """
finding_id: R9
action: CODE_FIX
reviewed_sha: def456
"""
    )

    with pytest.raises(ReviewValidationError, match="unknown remediation finding"):
        validate_remediation(review=review, remediation=remediation)


def test_rejects_remediation_sha_mismatch() -> None:
    review = parse_review(CHANGES_REVIEW)
    remediation = parse_remediation(
        """
finding_id: R1
action: CODE_FIX
reviewed_sha: other
"""
    )

    with pytest.raises(ReviewValidationError, match="does not match REVIEW"):
        validate_remediation(review=review, remediation=remediation)
