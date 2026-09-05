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
from .review import parse_review, validate_review
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
        if run_data.get("run_id") != run_id:
            raise ValueError("RUN-ID mismatch between decision ref and RUN")
        task_data = _mapping(run_data.get("task"), "RUN.task")
        run = Run(
            run_id=run_data["run_id"],
            task=RunTaskReference(
                id=task_data["id"], revision=task_data["revision"]
            ),
            executor=run_data["executor"],
            base_sha=run_data["base_sha"],
            workspace=run_data["workspace"],
            head_sha=run_data.get("head_sha"),
            status=run_data["status"],
        )
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
        package = validate_result_package(
            task=task, run=run, result=result, evidence=evidence
        )
        if package.result.unresolved:
            raise ValueError("successful RESULT contains unresolved items")

        review = parse_review(review_bytes.decode("utf-8", errors="strict"))
        validate_review(task=task, result=result, review=review)
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
