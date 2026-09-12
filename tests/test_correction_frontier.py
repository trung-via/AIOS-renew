"""Tests for the pure deterministic correction frontier authority."""

from dataclasses import FrozenInstanceError
from types import MappingProxyType
import pytest

from aios_renew.correction_frontier import (
    CorrectionFrontier,
    CorrectionFrontierError,
    OutstandingFinding,
    advance_frontier,
    derive_frontier,
)
from aios_renew.publication import RemediationPredecessor
from aios_renew.review import Finding, Review


def make_finding(finding_id: str = "R1", basis: str = "AC1") -> Finding:
    return Finding(
        id=finding_id,
        basis=basis,
        action="CODE_FIX",
        location="product.txt",
        issue=f"Issue for {finding_id}",
        expected=f"Expected fix for {finding_id}",
    )


def make_primary_review(
    review_id: str = "REVIEW-001-001",
    reviewed_sha: str = "a" * 40,
    verdict: str = "CHANGES_REQUIRED",
    findings: tuple[Finding, ...] = (),
) -> Review:
    acceptance = {f.basis: "FAIL" for f in findings}
    if not acceptance:
        acceptance = {"AC1": "PASS" if verdict == "PASS" else "FAIL"}
    return Review(
        review_id=review_id,
        reviewed_sha=reviewed_sha,
        mode="PRIMARY",
        verdict=verdict,
        acceptance=MappingProxyType(acceptance),
        findings=findings,
        prior_finding_id=None,
    )


def make_delta_review(
    review_id: str = "REVIEW-002-001",
    reviewed_sha: str = "b" * 40,
    prior_finding_id: str = "R1",
    verdict: str = "PASS",
    findings: tuple[Finding, ...] = (),
) -> Review:
    acceptance = {"AC1": "PASS" if verdict == "PASS" else "FAIL"}
    return Review(
        review_id=review_id,
        reviewed_sha=reviewed_sha,
        mode="DELTA",
        verdict=verdict,
        acceptance=MappingProxyType(acceptance),
        findings=findings,
        prior_finding_id=prior_finding_id,
    )


def test_correction_frontier_from_primary_represents_all_findings_ac1() -> None:
    f1 = make_finding("R1", "AC1")
    f2 = make_finding("R2", "AC2")
    f3 = make_finding("R3", "AC3")
    sha = "1" * 40
    rev = make_primary_review(
        review_id="REVIEW-001-001",
        reviewed_sha=sha,
        verdict="CHANGES_REQUIRED",
        findings=(f1, f2, f3),
    )

    frontier = CorrectionFrontier.from_primary("RUN-001-001", rev)

    assert len(frontier) == 3
    assert not frontier.is_empty
    assert bool(frontier) is True

    # Deterministic representation sorted by key (source_run_id, review_id, finding_id, reviewed_sha)
    assert tuple(f.finding_id for f in frontier.findings) == ("R1", "R2", "R3")
    for item in frontier.findings:
        assert item.source_run_id == "RUN-001-001"
        assert item.review_id == "REVIEW-001-001"
        assert item.reviewed_sha == sha
        assert item.finding.id in ("R1", "R2", "R3")

    # Membership checks
    assert "R1" in frontier
    assert "R2" in frontier
    assert "R3" in frontier
    assert "R4" not in frontier


def test_correction_frontier_from_primary_pass_is_empty_ac1() -> None:
    rev = make_primary_review(
        review_id="REVIEW-001-001",
        reviewed_sha="1" * 40,
        verdict="PASS",
        findings=(),
    )
    frontier = CorrectionFrontier.from_primary("RUN-001-001", rev)
    assert frontier.is_empty
    assert len(frontier) == 0
    assert bool(frontier) is False


def test_correction_frontier_from_primary_rejects_invalid_inputs_ac1() -> None:
    f1 = make_finding("R1")
    rev = make_primary_review(findings=(f1,))

    with pytest.raises(CorrectionFrontierError, match="invalid source_run_id"):
        CorrectionFrontier.from_primary("INVALID_ID", rev)

    with pytest.raises(CorrectionFrontierError, match="review must be a Review instance"):
        CorrectionFrontier.from_primary("RUN-001-001", "not-a-review")  # type: ignore

    delta_rev = make_delta_review(findings=(f1,))
    with pytest.raises(CorrectionFrontierError, match="PRIMARY review mode required"):
        CorrectionFrontier.from_primary("RUN-001-001", delta_rev)

    blocked_rev = make_primary_review(verdict="BLOCKED", findings=(f1,))
    with pytest.raises(CorrectionFrontierError, match="unsupported PRIMARY verdict"):
        CorrectionFrontier.from_primary("RUN-001-001", blocked_rev)


def test_correction_frontier_delta_pass_removes_exact_finding_and_preserves_siblings_ac2() -> None:
    f1 = make_finding("R1", "AC1")
    f2 = make_finding("R2", "AC2")
    sha1 = "1" * 40
    primary_rev = make_primary_review(
        review_id="REVIEW-001-001",
        reviewed_sha=sha1,
        findings=(f1, f2),
    )
    frontier = CorrectionFrontier.from_primary("RUN-001-001", primary_rev)

    sha2 = "2" * 40
    delta_rev = make_delta_review(
        review_id="REVIEW-002-001",
        reviewed_sha=sha2,
        prior_finding_id="R1",
        verdict="PASS",
    )
    pred = RemediationPredecessor(
        source_run_id="RUN-001-001",
        review_id="REVIEW-001-001",
        finding_id="R1",
        reviewed_sha=sha1,
    )

    advanced = frontier.advance(
        delta_run_id="RUN-002-001",
        delta_review=delta_rev,
        predecessor=pred,
    )

    assert len(advanced) == 1
    assert not advanced.is_empty
    assert "R1" not in advanced
    assert "R2" in advanced

    remaining = advanced.findings[0]
    assert remaining.finding_id == "R2"
    assert remaining.source_run_id == "RUN-001-001"
    assert remaining.review_id == "REVIEW-001-001"
    assert remaining.reviewed_sha == sha1


def test_correction_frontier_delta_advancement_fails_closed_on_invalid_or_resolved_selection_ac2() -> None:
    f1 = make_finding("R1", "AC1")
    sha1 = "1" * 40
    primary_rev = make_primary_review(
        review_id="REVIEW-001-001",
        reviewed_sha=sha1,
        findings=(f1,),
    )
    frontier = CorrectionFrontier.from_primary("RUN-001-001", primary_rev)

    # 1. Delta review with mismatching prior_finding_id vs predecessor
    pred = RemediationPredecessor(
        source_run_id="RUN-001-001",
        review_id="REVIEW-001-001",
        finding_id="R1",
        reviewed_sha=sha1,
    )
    mismatched_review = make_delta_review(prior_finding_id="R2", verdict="PASS")
    with pytest.raises(CorrectionFrontierError, match="does not match predecessor finding_id"):
        frontier.advance(
            delta_run_id="RUN-002-001",
            delta_review=mismatched_review,
            predecessor=pred,
        )

    # 2. Predecessor with mismatched reviewed_sha
    mismatched_sha_pred = RemediationPredecessor(
        source_run_id="RUN-001-001",
        review_id="REVIEW-001-001",
        finding_id="R1",
        reviewed_sha="9" * 40,
    )
    valid_delta_rev = make_delta_review(prior_finding_id="R1", verdict="PASS")
    with pytest.raises(CorrectionFrontierError, match="predecessor identity mismatch"):
        frontier.advance(
            delta_run_id="RUN-002-001",
            delta_review=valid_delta_rev,
            predecessor=mismatched_sha_pred,
        )

    # 3. Predecessor with mismatched review_id
    mismatched_rev_pred = RemediationPredecessor(
        source_run_id="RUN-001-001",
        review_id="REVIEW-001-WRONG",
        finding_id="R1",
        reviewed_sha=sha1,
    )
    with pytest.raises(CorrectionFrontierError, match="predecessor identity mismatch"):
        frontier.advance(
            delta_run_id="RUN-002-001",
            delta_review=valid_delta_rev,
            predecessor=mismatched_rev_pred,
        )

    # 4. Predecessor with mismatched source_run_id
    mismatched_run_pred = RemediationPredecessor(
        source_run_id="RUN-001-WRONG",
        review_id="REVIEW-001-001",
        finding_id="R1",
        reviewed_sha=sha1,
    )
    with pytest.raises(CorrectionFrontierError, match="predecessor identity mismatch"):
        frontier.advance(
            delta_run_id="RUN-002-001",
            delta_review=valid_delta_rev,
            predecessor=mismatched_run_pred,
        )

    # 5. Non-existent finding
    missing_pred = RemediationPredecessor(
        source_run_id="RUN-001-001",
        review_id="REVIEW-001-001",
        finding_id="R_NONEXISTENT",
        reviewed_sha=sha1,
    )
    missing_delta_rev = make_delta_review(prior_finding_id="R_NONEXISTENT", verdict="PASS")
    with pytest.raises(CorrectionFrontierError, match="not outstanding in the correction frontier"):
        frontier.advance(
            delta_run_id="RUN-002-001",
            delta_review=missing_delta_rev,
            predecessor=missing_pred,
        )

    # 6. Already-resolved finding fails closed
    resolved_frontier = frontier.advance(
        delta_run_id="RUN-002-001",
        delta_review=valid_delta_rev,
        predecessor=pred,
    )
    assert resolved_frontier.is_empty
    with pytest.raises(CorrectionFrontierError, match="not outstanding in the correction frontier"):
        resolved_frontier.advance(
            delta_run_id="RUN-003-001",
            delta_review=valid_delta_rev,
            predecessor=pred,
        )


def test_correction_frontier_delta_changes_required_replaces_finding_ac3() -> None:
    f1 = make_finding("R1", "AC1")
    f2 = make_finding("R2", "AC2")
    sha1 = "1" * 40
    primary_rev = make_primary_review(
        review_id="REVIEW-001-001",
        reviewed_sha=sha1,
        findings=(f1, f2),
    )
    frontier = CorrectionFrontier.from_primary("RUN-001-001", primary_rev)

    f1_a = make_finding("R1_a", "AC1")
    f1_b = make_finding("R1_b", "AC1")
    sha2 = "2" * 40
    delta_rev = make_delta_review(
        review_id="REVIEW-002-001",
        reviewed_sha=sha2,
        prior_finding_id="R1",
        verdict="CHANGES_REQUIRED",
        findings=(f1_a, f1_b),
    )
    pred = RemediationPredecessor(
        source_run_id="RUN-001-001",
        review_id="REVIEW-001-001",
        finding_id="R1",
        reviewed_sha=sha1,
    )

    advanced = frontier.advance(
        delta_run_id="RUN-002-001",
        delta_review=delta_rev,
        predecessor=pred,
    )

    assert len(advanced) == 3
    assert "R1" not in advanced
    assert "R2" in advanced
    assert "R1_a" in advanced
    assert "R1_b" in advanced

    findings_by_id = {f.finding_id: f for f in advanced.findings}
    # Check preserved sibling R2
    assert findings_by_id["R2"].source_run_id == "RUN-001-001"
    assert findings_by_id["R2"].review_id == "REVIEW-001-001"
    assert findings_by_id["R2"].reviewed_sha == sha1

    # Check replacement findings R1_a, R1_b
    assert findings_by_id["R1_a"].source_run_id == "RUN-002-001"
    assert findings_by_id["R1_a"].review_id == "REVIEW-002-001"
    assert findings_by_id["R1_a"].reviewed_sha == sha2
    assert findings_by_id["R1_b"].source_run_id == "RUN-002-001"
    assert findings_by_id["R1_b"].review_id == "REVIEW-002-001"
    assert findings_by_id["R1_b"].reviewed_sha == sha2


def test_correction_frontier_delta_blocked_does_not_resolve_frontier_ac3() -> None:
    f1 = make_finding("R1", "AC1")
    f2 = make_finding("R2", "AC2")
    sha1 = "1" * 40
    primary_rev = make_primary_review(
        review_id="REVIEW-001-001",
        reviewed_sha=sha1,
        findings=(f1, f2),
    )
    frontier = CorrectionFrontier.from_primary("RUN-001-001", primary_rev)

    sha2 = "2" * 40
    delta_rev = make_delta_review(
        review_id="REVIEW-002-001",
        reviewed_sha=sha2,
        prior_finding_id="R1",
        verdict="BLOCKED",
    )
    pred = RemediationPredecessor(
        source_run_id="RUN-001-001",
        review_id="REVIEW-001-001",
        finding_id="R1",
        reviewed_sha=sha1,
    )

    advanced = frontier.advance(
        delta_run_id="RUN-002-001",
        delta_review=delta_rev,
        predecessor=pred,
    )

    # Selected finding R1 remains outstanding, siblings preserved
    assert len(advanced) == 2
    assert "R1" in advanced
    assert "R2" in advanced
    assert not advanced.is_empty


def test_correction_frontier_multi_step_sequential_resolution_to_empty_ac4() -> None:
    f1 = make_finding("R1", "AC1")
    f2 = make_finding("R2", "AC2")
    sha0 = "0" * 40
    primary_rev = make_primary_review(
        review_id="REVIEW-001-001",
        reviewed_sha=sha0,
        findings=(f1, f2),
    )

    sha1 = "1" * 40
    step1_rev = make_delta_review(
        review_id="REVIEW-002-001",
        reviewed_sha=sha1,
        prior_finding_id="R1",
        verdict="PASS",
    )
    step1_pred = RemediationPredecessor(
        source_run_id="RUN-001-001",
        review_id="REVIEW-001-001",
        finding_id="R1",
        reviewed_sha=sha0,
    )

    sha2 = "2" * 40
    step2_rev = make_delta_review(
        review_id="REVIEW-003-001",
        reviewed_sha=sha2,
        prior_finding_id="R2",
        verdict="PASS",
    )
    step2_pred = RemediationPredecessor(
        source_run_id="RUN-001-001",
        review_id="REVIEW-001-001",
        finding_id="R2",
        reviewed_sha=sha0,
    )

    steps = [
        ("RUN-002-001", step1_pred, step1_rev),
        ("RUN-003-001", step2_pred, step2_rev),
    ]

    final_frontier = derive_frontier("RUN-001-001", primary_rev, steps)
    assert final_frontier.is_empty
    assert len(final_frontier) == 0


def test_correction_frontier_is_pure_read_only_and_immutable_ac1() -> None:
    f1 = make_finding("R1", "AC1")
    rev = make_primary_review(findings=(f1,))
    frontier = CorrectionFrontier.from_primary("RUN-001-001", rev)

    with pytest.raises(FrozenInstanceError):
        frontier.findings = ()  # type: ignore

    item = frontier.findings[0]
    with pytest.raises(FrozenInstanceError):
        item.source_run_id = "MUTATED"  # type: ignore


def test_correction_frontier_supports_mapping_predecessor() -> None:
    f1 = make_finding("R1", "AC1")
    sha1 = "1" * 40
    rev = make_primary_review(reviewed_sha=sha1, findings=(f1,))
    frontier = CorrectionFrontier.from_primary("RUN-001-001", rev)

    delta_rev = make_delta_review(prior_finding_id="R1", verdict="PASS")
    mapping_pred = {
        "source_run_id": "RUN-001-001",
        "review_id": "REVIEW-001-001",
        "finding_id": "R1",
        "reviewed_sha": sha1,
    }

    advanced = advance_frontier(
        frontier,
        delta_run_id="RUN-002-001",
        delta_review=delta_rev,
        predecessor=mapping_pred,
    )
    assert advanced.is_empty
