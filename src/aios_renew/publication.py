"""Deterministic publication of one canonical PASS review decision."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .artifacts import (
    ResultPackage,
    validate_evidence,
    validate_result,
    validate_result_package,
)
from .review import (
    Remediation,
    Review,
    parse_remediation,
    parse_review,
    validate_remediation,
    validate_review,
)
from .run import ACTIVE, Run, RunTaskReference
from .task import parse_task


_RUN_ID = re.compile(r"RUN-[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA = re.compile(r"[0-9a-f]{40}")
_DECISION_PREFIX = "refs/heads/aios/review-decision/"


class PublicationError(RuntimeError):
    """Raised after a publication gate fails closed."""

    def __init__(self, message: str, report: PublicationReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class PublicationReport:
    """Attributable outcome emitted for every publication attempt."""

    source_run: str
    reviewed_sha: str
    prior_main_sha: str
    outcome: str
    detail: str


def _git(
    repo: Path, *args: str, allow_fail: bool = False
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=False,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8", errors="strict").strip()
        stderr = completed.stderr.decode("utf-8", errors="strict").strip()
    except (OSError, UnicodeError) as exc:
        if allow_fail:
            return 1, "", str(exc)
        raise RuntimeError(f"Git command failed: {exc}") from exc
    if completed.returncode and not allow_fail:
        detail = stderr or stdout or f"exit {completed.returncode}"
        raise RuntimeError(f"Git command failed: {detail}")
    return completed.returncode, stdout, stderr


def _failed(
    run_id: str,
    message: str,
    *,
    reviewed_sha: str = "UNKNOWN",
    prior_main_sha: str = "UNKNOWN",
) -> PublicationError:
    return PublicationError(
        message,
        PublicationReport(
            source_run=run_id,
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
            outcome="FAILED",
            detail=message,
        ),
    )


def _single_remote_sha(
    repo: Path, remote: str, ref: str, *, run_id: str
) -> str:
    code, output, _ = _git(
        repo, "ls-remote", "--refs", remote, ref, allow_fail=True
    )
    if code:
        raise _failed(run_id, f"cannot query canonical ref {ref}")
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if (
        len(lines) != 1
        or len(lines[0]) != 2
        or lines[0][1] != ref
        or _SHA.fullmatch(lines[0][0]) is None
    ):
        raise _failed(run_id, f"canonical ref {ref} is missing or ambiguous")
    return lines[0][0]


def _fetch_object(repo: Path, remote: str, sha: str, *, run_id: str) -> None:
    code, _, _ = _git(
        repo, "fetch", "--no-tags", remote, sha, allow_fail=True
    )
    if code:
        raise _failed(run_id, f"cannot fetch canonical object {sha}")
    code, kind, _ = _git(repo, "cat-file", "-t", sha, allow_fail=True)
    if code or kind != "commit":
        raise _failed(run_id, f"canonical object {sha} is not a commit")


def _read_blob(repo: Path, commit_sha: str, path: str, *, run_id: str) -> bytes:
    code, output, _ = _git(
        repo, "show", f"{commit_sha}:{path}", allow_fail=True
    )
    if code:
        raise _failed(run_id, f"canonical content missing: {path}")
    return output.encode("utf-8")


def _json_no_duplicates(source: bytes, *, document: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{document} contains duplicate key: {key}")
            result[key] = value
        return result

    return json.loads(source.decode("utf-8", errors="strict"), object_pairs_hook=object_pairs)


def _mapping(value: Any, document: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{document} must be a mapping")
    return value


def _run_from_data(data: Any, document: str) -> Run:
    root = _mapping(data, document)
    task_data = _mapping(root.get("task"), f"{document}.task")
    return Run(
        run_id=root["run_id"],
        task=RunTaskReference(
            id=task_data["id"], revision=task_data["revision"]
        ),
        executor=root["executor"],
        base_sha=root["base_sha"],
        workspace=root["workspace"],
        head_sha=root.get("head_sha"),
        status=root["status"],
    )


def _remediation_lineage(
    run_data: Mapping[str, Any], *, run_id: str
) -> tuple[Run, Remediation, Review]:
    execution = _mapping(run_data.get("execution"), "REMEDIATION.execution")
    run = _run_from_data(execution.get("run"), "REMEDIATION.execution.run")
    if run.run_id != run_id:
        raise ValueError("RUN-ID mismatch between decision ref and REMEDIATION RUN")

    remediation_data = _mapping(
        execution.get("remediation"), "REMEDIATION.execution.remediation"
    )
    remediation = parse_remediation(json.dumps(remediation_data))
    finding_data = _mapping(
        execution.get("finding"), "REMEDIATION.execution.finding"
    )
    prior_review = parse_review(
        json.dumps(
            {
                "review_id": execution["review_id"],
                "reviewed_sha": remediation.reviewed_sha,
                "mode": "PRIMARY",
                "verdict": "CHANGES_REQUIRED",
                "acceptance": {finding_data["basis"]: "FAIL"},
                "findings": [finding_data],
            }
        )
    )
    original_constraints = execution.get("original_constraints", [])
    if (
        not isinstance(original_constraints, list)
        or not all(isinstance(item, str) for item in original_constraints)
        or tuple(original_constraints) != remediation.constraints
    ):
        raise ValueError(
            "REMEDIATION original_constraints do not match its constraints"
        )
    return run, remediation, prior_review


def _validate_remediation_package(
    repo: Path,
    *,
    source_sha: str,
    task: Any,
    run: Run,
    remediation: Remediation,
    prior_review: Review,
    result: Any,
    evidence: tuple[Any, ...],
) -> ResultPackage:
    if run.task.id != task.task_id or run.task.revision != task.revision:
        raise ValueError("REMEDIATION RUN does not reference the supplied TASK")
    if run.base_sha != remediation.reviewed_sha:
        raise ValueError("REMEDIATION RUN base_sha does not match reviewed_sha")
    validate_remediation(
        review=prior_review, remediation=remediation, task=task
    )
    if remediation.action == "CODE_FIX" and not remediation.modification_scope:
        raise ValueError("CODE_FIX remediation modification scope is empty")
    if not remediation.affected_verification:
        raise ValueError("REMEDIATION affected verification is empty")
    if result.claims:
        raise ValueError("remediation RESULT claims must be empty")
    if result.unresolved:
        raise ValueError("remediation RESULT has unresolved items")

    evidence_by_id: set[str] = set()
    for item in evidence:
        if item.evidence_id in evidence_by_id:
            raise ValueError(f"duplicate evidence_id: {item.evidence_id}")
        evidence_by_id.add(item.evidence_id)
        if item.run_id != run.run_id:
            raise ValueError(
                f"{item.evidence_id} does not reference RUN {run.run_id}"
            )
        if item.subject_sha != result.head_sha:
            raise ValueError(
                f"{item.evidence_id} subject_sha does not match RESULT head_sha"
            )
    for command in remediation.affected_verification:
        matching = [item for item in evidence if item.source.command == command]
        if not matching:
            raise ValueError(
                "missing affected verification evidence for required command: "
                + command
            )
        if not any(item.result.exit_code == 0 for item in matching):
            raise ValueError(
                "affected verification command has no successful evidence: "
                + command
            )

    code, changed_output, _ = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        remediation.reviewed_sha,
        source_sha,
        allow_fail=True,
    )
    if code:
        raise ValueError("cannot inspect REMEDIATION committed delta")
    changed_files = {path for path in changed_output.split("\0") if path}
    if set(result.changed_files) != changed_files:
        raise ValueError("RESULT.changed_files mismatch")
    outside_scope = changed_files.difference(remediation.modification_scope)
    if outside_scope:
        raise ValueError(
            "committed changed paths outside REMEDIATION modification scope: "
            + ", ".join(sorted(outside_scope))
        )
    if remediation.action == "EVIDENCE_ONLY":
        if source_sha != remediation.reviewed_sha or changed_files:
            raise ValueError("EVIDENCE_ONLY remediation changed repository HEAD")
    elif source_sha == remediation.reviewed_sha or not changed_files:
        raise ValueError("CODE_FIX remediation committed delta is empty")
    return ResultPackage(result=result, evidence=evidence)


def _load_success_lineage(
    repo: Path,
    *,
    remote: str,
    run_id: str,
    decision_sha: str,
) -> tuple[str, ResultPackage]:
    code, tree, _ = _git(
        repo,
        "ls-tree",
        "-r",
        "--name-only",
        decision_sha,
        "--",
        ".ai/reviews",
        allow_fail=True,
    )
    if code:
        raise _failed(run_id, "cannot inspect canonical review decision")
    review_paths = [
        path
        for path in tree.splitlines()
        if path.startswith(".ai/reviews/")
        and path.endswith((".yaml", ".yml"))
    ]
    if len(review_paths) != 1:
        raise _failed(
            run_id, "review decision must contain exactly one REVIEW document"
        )

    review_bytes = _read_blob(
        repo, decision_sha, review_paths[0], run_id=run_id
    )
    artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"
    source_ref = f"refs/heads/aios/review/{run_id}"
    artifacts_sha = _single_remote_sha(
        repo, remote, artifacts_ref, run_id=run_id
    )
    source_sha = _single_remote_sha(repo, remote, source_ref, run_id=run_id)
    _fetch_object(repo, remote, artifacts_sha, run_id=run_id)
    _fetch_object(repo, remote, source_sha, run_id=run_id)

    run_bytes = _read_blob(
        repo, artifacts_sha, ".ai/transport/run.json", run_id=run_id
    )
    result_bytes = _read_blob(
        repo, artifacts_sha, ".ai/transport/result.json", run_id=run_id
    )
    try:
        run_data = _mapping(
            _json_no_duplicates(run_bytes, document="RUN"), "RUN"
        )
        remediation = None
        prior_review = None
        if "kind" not in run_data:
            if run_data.get("run_id") != run_id:
                raise ValueError("RUN-ID mismatch between decision ref and RUN")
            run = _run_from_data(run_data, "RUN")
        elif run_data.get("kind") == "REMEDIATION":
            run, remediation, prior_review = _remediation_lineage(
                run_data, run_id=run_id
            )
        else:
            raise ValueError("unknown canonical RUN kind")
        if run.status != ACTIVE:
            raise ValueError("canonical successful RUN status is invalid")
        code, kind, _ = _git(
            repo, "cat-file", "-t", run.base_sha, allow_fail=True
        )
        if code or kind != "commit":
            raise ValueError("RUN base_sha is not a canonical commit")
        code, _, _ = _git(
            repo,
            "merge-base",
            "--is-ancestor",
            run.base_sha,
            source_sha,
            allow_fail=True,
        )
        if code:
            raise ValueError("reviewed candidate does not descend from RUN base_sha")

        result_data = _mapping(
            _json_no_duplicates(result_bytes, document="ResultPackage"),
            "ResultPackage",
        )
        result = validate_result(result_data["result"])
        evidence_data = result_data["evidence"]
        if not isinstance(evidence_data, list):
            raise ValueError("ResultPackage.evidence must be a list")
        evidence = tuple(validate_evidence(item) for item in evidence_data)

        task_bytes = _read_blob(
            repo,
            source_sha,
            f".ai/tasks/{run.task.id}.yaml",
            run_id=run_id,
        )
        task = parse_task(task_bytes.decode("utf-8", errors="strict"))
        if remediation is None:
            package = validate_result_package(
                task=task, run=run, result=result, evidence=evidence
            )
        else:
            assert prior_review is not None
            package = _validate_remediation_package(
                repo,
                source_sha=source_sha,
                task=task,
                run=run,
                remediation=remediation,
                prior_review=prior_review,
                result=result,
                evidence=evidence,
            )
        if package.result.unresolved:
            raise ValueError("successful RESULT contains unresolved items")

        review = parse_review(review_bytes.decode("utf-8", errors="strict"))
        if remediation is None:
            validate_review(task=task, result=result, review=review)
        else:
            if review.mode != "DELTA":
                raise ValueError("REMEDIATION candidate requires a DELTA REVIEW")
            if review.prior_finding_id != prior_review.findings[0].id:
                raise ValueError(
                    "DELTA REVIEW prior finding does not match REMEDIATION"
                )
            validate_review(
                task=task,
                result=result,
                review=review,
                prior_review=prior_review,
            )
        if review.verdict != "PASS":
            raise ValueError(f"REVIEW verdict is {review.verdict}, not PASS")
        if review.reviewed_sha != source_sha:
            raise ValueError(
                "REVIEW.reviewed_sha does not match canonical source ref"
            )
        if result.head_sha != source_sha:
            raise ValueError(
                "RESULT head_sha does not match canonical source ref"
            )
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        reviewed_sha = locals().get("reviewed_sha", source_sha)
        raise _failed(
            run_id,
            f"invalid canonical publication lineage: {exc}",
            reviewed_sha=reviewed_sha,
        ) from exc
    return source_sha, package


def publish_review_decision(
    repo: str | Path,
    *,
    run_id: str,
    decision_sha: str,
    remote: str = "origin",
) -> PublicationReport:
    """Validate one immutable decision lineage and fast-forward remote main."""

    root = Path(repo).resolve()
    if _RUN_ID.fullmatch(run_id) is None or "/" in run_id or "\\" in run_id:
        raise _failed(run_id, "invalid source RUN id")
    if _SHA.fullmatch(decision_sha) is None:
        raise _failed(run_id, "invalid review-decision event SHA")
    if not remote or remote.startswith("-"):
        raise _failed(run_id, "invalid publication remote")

    main_ref = "refs/heads/main"
    try:
        prior_main_sha = _single_remote_sha(
            root, remote, main_ref, run_id=run_id
        )
    except PublicationError as exc:
        raise _failed(
            run_id,
            str(exc),
        ) from exc
    expected_reviewed_sha = "UNKNOWN"
    try:
        expected_reviewed_sha = _single_remote_sha(
            root,
            remote,
            f"refs/heads/aios/review/{run_id}",
            run_id=run_id,
        )
        decision_ref = f"{_DECISION_PREFIX}{run_id}"
        remote_decision_sha = _single_remote_sha(
            root, remote, decision_ref, run_id=run_id
        )
        if remote_decision_sha != decision_sha:
            raise _failed(
                run_id,
                "review-decision event SHA does not match canonical remote ref",
            )
        _fetch_object(root, remote, decision_sha, run_id=run_id)
        reviewed_sha, _ = _load_success_lineage(
            root,
            remote=remote,
            run_id=run_id,
            decision_sha=decision_sha,
        )
    except PublicationError as exc:
        reviewed = exc.report.reviewed_sha
        if reviewed == "UNKNOWN":
            reviewed = expected_reviewed_sha
        raise _failed(
            run_id,
            str(exc),
            reviewed_sha=reviewed,
            prior_main_sha=prior_main_sha,
        ) from exc
    try:
        _fetch_object(root, remote, prior_main_sha, run_id=run_id)
    except PublicationError as exc:
        raise _failed(
            run_id,
            str(exc),
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
        ) from exc

    if prior_main_sha == reviewed_sha:
        return PublicationReport(
            source_run=run_id,
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
            outcome="ALREADY_PUBLISHED",
            detail="remote main already equals the reviewed candidate",
        )

    code, _, _ = _git(
        root,
        "merge-base",
        "--is-ancestor",
        prior_main_sha,
        reviewed_sha,
        allow_fail=True,
    )
    if code:
        raise _failed(
            run_id,
            "remote main is not an ancestor of the reviewed candidate",
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
        )

    code, output, stderr = _git(
        root,
        "push",
        "--porcelain",
        "--no-tags",
        f"--force-with-lease={main_ref}:{prior_main_sha}",
        remote,
        f"{reviewed_sha}:{main_ref}",
        allow_fail=True,
    )
    if code:
        detail = stderr or output or "remote rejected publication"
        raise _failed(
            run_id,
            f"fast-forward publication failed: {detail}",
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
        )

    try:
        final_main_sha = _single_remote_sha(
            root, remote, main_ref, run_id=run_id
        )
    except PublicationError as exc:
        raise _failed(
            run_id,
            str(exc),
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
        ) from exc
    if final_main_sha != reviewed_sha:
        raise _failed(
            run_id,
            "remote main postcondition does not equal reviewed candidate",
            reviewed_sha=reviewed_sha,
            prior_main_sha=prior_main_sha,
        )
    return PublicationReport(
        source_run=run_id,
        reviewed_sha=reviewed_sha,
        prior_main_sha=prior_main_sha,
        outcome="PUBLISHED",
        detail="remote main advanced by fast-forward to reviewed candidate",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish one canonical AIOS PASS review decision"
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--decision-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = publish_review_decision(
            args.repo,
            run_id=args.run_id,
            decision_sha=args.decision_sha,
            remote=args.remote,
        )
    except PublicationError as exc:
        print(json.dumps(asdict(exc.report), sort_keys=True))
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(asdict(report), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
