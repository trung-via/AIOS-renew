"""Deterministic prospective correction frontier authority."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .review import Finding, Review


_RUN_ID = re.compile(r"RUN-[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA = re.compile(r"[0-9a-f]{40}")


class CorrectionFrontierError(ValueError):
    """Raised when correction frontier derivation or advancement violates contract."""


@dataclass(frozen=True)
class OutstandingFinding:
    """Exact semantic lineage state for one outstanding finding."""

    source_run_id: str
    review_id: str
    finding_id: str
    reviewed_sha: str
    finding: Finding

    def __post_init__(self) -> None:
        if not isinstance(self.source_run_id, str) or _RUN_ID.fullmatch(self.source_run_id) is None:
            raise CorrectionFrontierError(f"invalid source_run_id: {self.source_run_id}")
        if not isinstance(self.review_id, str) or not self.review_id or "/" in self.review_id or "\\" in self.review_id:
            raise CorrectionFrontierError(f"invalid review_id: {self.review_id}")
        if not isinstance(self.finding_id, str) or not self.finding_id or "/" in self.finding_id or "\\" in self.finding_id:
            raise CorrectionFrontierError(f"invalid finding_id: {self.finding_id}")
        if not isinstance(self.reviewed_sha, str) or _SHA.fullmatch(self.reviewed_sha) is None:
            raise CorrectionFrontierError(f"invalid reviewed_sha: {self.reviewed_sha}")
        if not isinstance(self.finding, Finding):
            raise CorrectionFrontierError("finding must be a Finding instance")
        if self.finding_id != self.finding.id:
            raise CorrectionFrontierError(
                f"finding_id {self.finding_id!r} does not match finding.id {self.finding.id!r}"
            )

    @property
    def key(self) -> tuple[str, str, str, str]:
        """Exact semantic lineage identity tuple."""
        return (self.source_run_id, self.review_id, self.finding_id, self.reviewed_sha)

    def matches_predecessor(
        self,
        *,
        source_run_id: str,
        review_id: str,
        finding_id: str,
        reviewed_sha: str,
    ) -> bool:
        return self.key == (source_run_id, review_id, finding_id, reviewed_sha)

    @classmethod
    def from_finding(
        cls,
        *,
        source_run_id: str,
        review_id: str,
        reviewed_sha: str,
        finding: Finding,
    ) -> OutstandingFinding:
        return cls(
            source_run_id=source_run_id,
            review_id=review_id,
            finding_id=finding.id,
            reviewed_sha=reviewed_sha,
            finding=finding,
        )


def _extract_predecessor_fields(predecessor: Any) -> tuple[str, str, str, str]:
    if isinstance(predecessor, Mapping):
        source_run_id = predecessor.get("source_run_id")
        review_id = predecessor.get("review_id")
        finding_id = predecessor.get("finding_id")
        reviewed_sha = predecessor.get("reviewed_sha")
    else:
        source_run_id = getattr(predecessor, "source_run_id", None)
        review_id = getattr(predecessor, "review_id", None)
        finding_id = getattr(predecessor, "finding_id", None)
        reviewed_sha = getattr(predecessor, "reviewed_sha", None)

    if not isinstance(source_run_id, str) or _RUN_ID.fullmatch(source_run_id) is None:
        raise CorrectionFrontierError(f"invalid predecessor source_run_id: {source_run_id}")
    if not isinstance(review_id, str) or not review_id or "/" in review_id or "\\" in review_id:
        raise CorrectionFrontierError(f"invalid predecessor review_id: {review_id}")
    if not isinstance(finding_id, str) or not finding_id or "/" in finding_id or "\\" in finding_id:
        raise CorrectionFrontierError(f"invalid predecessor finding_id: {finding_id}")
    if not isinstance(reviewed_sha, str) or _SHA.fullmatch(reviewed_sha) is None:
        raise CorrectionFrontierError(f"invalid predecessor reviewed_sha: {reviewed_sha}")

    return source_run_id, review_id, finding_id, reviewed_sha


@dataclass(frozen=True)
class CorrectionFrontier:
    """Pure, read-only representation of outstanding findings across correction lineage."""

    findings: tuple[OutstandingFinding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.findings, tuple):
            object.__setattr__(self, "findings", tuple(self.findings))
        for item in self.findings:
            if not isinstance(item, OutstandingFinding):
                raise CorrectionFrontierError("all items in frontier must be OutstandingFinding instances")
        # Ensure determinism: sorted by identity key
        sorted_findings = tuple(sorted(self.findings, key=lambda f: f.key))
        keys = set()
        for f in sorted_findings:
            if f.key in keys:
                raise CorrectionFrontierError(f"duplicate finding in frontier: {f.key}")
            keys.add(f.key)
        object.__setattr__(self, "findings", sorted_findings)

    @property
    def is_empty(self) -> bool:
        return len(self.findings) == 0

    def __len__(self) -> int:
        return len(self.findings)

    def __bool__(self) -> bool:
        return len(self.findings) > 0

    def __iter__(self):
        return iter(self.findings)

    def __contains__(self, item: Any) -> bool:
        if isinstance(item, OutstandingFinding):
            return item in self.findings
        if isinstance(item, str):
            return any(f.finding_id == item for f in self.findings)
        return False

    @classmethod
    def from_primary(
        cls,
        source_run_id: str,
        review: Review,
    ) -> CorrectionFrontier:
        """Initialize the correction frontier from one PRIMARY review."""
        if not isinstance(source_run_id, str) or _RUN_ID.fullmatch(source_run_id) is None:
            raise CorrectionFrontierError(f"invalid source_run_id: {source_run_id}")
        if not isinstance(review, Review):
            raise CorrectionFrontierError("review must be a Review instance")
        if review.mode != "PRIMARY":
            raise CorrectionFrontierError(
                f"PRIMARY review mode required to initialize frontier, got {review.mode}"
            )
        if review.verdict == "PASS":
            return cls(findings=())
        if review.verdict != "CHANGES_REQUIRED":
            raise CorrectionFrontierError(
                f"unsupported PRIMARY verdict for frontier initialization: {review.verdict}"
            )
        findings = tuple(
            OutstandingFinding.from_finding(
                source_run_id=source_run_id,
                review_id=review.review_id,
                reviewed_sha=review.reviewed_sha,
                finding=finding,
            )
            for finding in review.findings
        )
        return cls(findings=findings)

    def advance(
        self,
        *,
        delta_run_id: str,
        delta_review: Review,
        predecessor: Any,
    ) -> CorrectionFrontier:
        """Advance the frontier by applying one DELTA review decision."""
        if not isinstance(delta_run_id, str) or _RUN_ID.fullmatch(delta_run_id) is None:
            raise CorrectionFrontierError(f"invalid delta_run_id: {delta_run_id}")
        if not isinstance(delta_review, Review):
            raise CorrectionFrontierError("delta_review must be a Review instance")
        if delta_review.mode != "DELTA":
            raise CorrectionFrontierError(
                f"DELTA review mode required for advancement, got {delta_review.mode}"
            )

        pred_source_run_id, pred_review_id, pred_finding_id, pred_reviewed_sha = (
            _extract_predecessor_fields(predecessor)
        )

        if delta_review.prior_finding_id is None:
            raise CorrectionFrontierError("DELTA review prior_finding_id is required")
        if delta_review.prior_finding_id != pred_finding_id:
            raise CorrectionFrontierError(
                f"DELTA prior_finding_id {delta_review.prior_finding_id!r} "
                f"does not match predecessor finding_id {pred_finding_id!r}"
            )

        exact_matches = [
            f for f in self.findings
            if f.matches_predecessor(
                source_run_id=pred_source_run_id,
                review_id=pred_review_id,
                finding_id=pred_finding_id,
                reviewed_sha=pred_reviewed_sha,
            )
        ]

        if not exact_matches:
            partial_matches = [
                f for f in self.findings if f.finding_id == pred_finding_id
            ]
            if partial_matches:
                expected_key = partial_matches[0].key
                actual_key = (pred_source_run_id, pred_review_id, pred_finding_id, pred_reviewed_sha)
                raise CorrectionFrontierError(
                    f"predecessor identity mismatch for finding {pred_finding_id!r}: "
                    f"expected {expected_key}, got {actual_key}"
                )
            raise CorrectionFrontierError(
                f"selected finding {pred_finding_id!r} is not outstanding in the correction frontier "
                f"(missing or already resolved)"
            )

        if len(exact_matches) > 1:
            raise CorrectionFrontierError(
                f"ambiguous predecessor match for {pred_finding_id!r}: {len(exact_matches)} matches found"
            )

        target = exact_matches[0]
        remaining = tuple(f for f in self.findings if f != target)

        if delta_review.verdict == "PASS":
            return CorrectionFrontier(findings=remaining)

        if delta_review.verdict == "CHANGES_REQUIRED":
            new_findings = tuple(
                OutstandingFinding.from_finding(
                    source_run_id=delta_run_id,
                    review_id=delta_review.review_id,
                    reviewed_sha=delta_review.reviewed_sha,
                    finding=new_f,
                )
                for new_f in delta_review.findings
            )
            return CorrectionFrontier(findings=remaining + new_findings)

        if delta_review.verdict == "BLOCKED":
            # DELTA BLOCKED does not silently resolve the frontier; the finding remains outstanding.
            return self

        raise CorrectionFrontierError(
            f"unrecognized DELTA review verdict: {delta_review.verdict}"
        )


def advance_frontier(
    frontier: CorrectionFrontier,
    *,
    delta_run_id: str,
    delta_review: Review,
    predecessor: Any,
) -> CorrectionFrontier:
    """Pure functional wrapper around CorrectionFrontier.advance."""
    if not isinstance(frontier, CorrectionFrontier):
        raise CorrectionFrontierError("frontier must be a CorrectionFrontier instance")
    return frontier.advance(
        delta_run_id=delta_run_id,
        delta_review=delta_review,
        predecessor=predecessor,
    )


def derive_frontier(
    primary_run_id: str,
    primary_review: Review,
    delta_steps: Sequence[tuple[str, Any, Review]],
) -> CorrectionFrontier:
    """Derive the deterministic correction frontier across a sequence of delta steps."""
    frontier = CorrectionFrontier.from_primary(primary_run_id, primary_review)
    for step_run_id, step_pred, step_review in delta_steps:
        frontier = frontier.advance(
            delta_run_id=step_run_id,
            delta_review=step_review,
            predecessor=step_pred,
        )
    return frontier
