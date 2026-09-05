"""Deterministic publication of one canonical PASS-reviewed RUN."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from .artifacts import ArtifactValidationError, Result, validate_evidence, validate_result
from .review import ReviewValidationError, parse_review, validate_review
from .task import TaskValidationError, parse_task


_RUN_ID = re.compile(r"^RUN-[A-Za-z0-9][A-Za-z0-9.-]*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DECISION_PREFIX = "refs/heads/aios/review-decision/"


@dataclass(frozen=True)
class PublicationOutcome:
    run_id: str
    reviewed_sha: str
    prior_main_sha: str
    outcome: str
    detail: str


class PublicationError(RuntimeError):
    """A fail-closed publication decision with attributable known facts."""

    def __init__(self, message: str, outcome: PublicationOutcome) -> None:
        super().__init__(message)
        self.outcome = replace(outcome, outcome="FAILED", detail=message)


def _git(repo: Path, *args: str, allow_fail: bool = False) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Git command failed: {exc}") from exc
    if completed.returncode and not allow_fail:
        detail = stderr.strip() or stdout.strip() or "unknown Git failure"
        raise RuntimeError(f"Git command failed: {detail}")
    if completed.returncode:
        return ""
    return stdout


def _fail(record: PublicationOutcome, message: str) -> None:
    raise PublicationError(message, record)


def _remote_refs(
    repo: Path, remote: str, refs: Sequence[str], record: PublicationOutcome
) -> dict[str, str]:
    try:
        output = _git(repo, "ls-remote", "--refs", remote, *refs)
    except RuntimeError as exc:
        _fail(record, str(exc))
    found: dict[str, str] = {}
    requested = set(refs)
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[1] not in requested or not _SHA.fullmatch(fields[0]):
            _fail(record, "malformed remote ref response")
        if fields[1] in found:
            _fail(record, f"ambiguous remote ref: {fields[1]}")
        found[fields[1]] = fields[0]
    return found


def _require_ref(
    refs: Mapping[str, str], ref: str, record: PublicationOutcome
) -> str:
    sha = refs.get(ref)
    if sha is None:
        _fail(record, f"required canonical ref is missing: {ref}")
    return sha


def _fetch_commit(
    repo: Path, remote: str, sha: str, label: str, record: PublicationOutcome
) -> None:
    try:
        _git(repo, "fetch", "--quiet", "--no-tags", remote, sha)
        _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    except RuntimeError as exc:
        _fail(record, f"cannot resolve {label} commit {sha}: {exc}")


def _read_blob(
    repo: Path, commit: str, path: str, label: str, record: PublicationOutcome
) -> bytes:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), "show", f"{commit}:{path}"),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        _fail(record, f"cannot read {label}: {exc}")
    if completed.returncode:
        _fail(record, f"{label} is missing")
    return completed.stdout


def _decision_review_path(
    repo: Path, decision_sha: str, record: PublicationOutcome
) -> str:
    try:
        output = _git(
            repo,
            "ls-tree",
            "-r",
            "--name-only",
            decision_sha,
            "--",
            ".ai/reviews",
        )
    except RuntimeError as exc:
        _fail(record, f"cannot inspect review decision: {exc}")
    paths = [
        path
        for path in output.splitlines()
        if path.startswith(".ai/reviews/") and path.endswith((".yaml", ".yml"))
    ]
    if len(paths) != 1:
        _fail(record, "review decision must contain exactly one REVIEW document")
    return paths[0]


def _reject_duplicate_yaml_keys(source: str, record: PublicationOutcome) -> None:
    try:
        root = yaml.compose(source, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        _fail(record, f"malformed REVIEW: {exc}")

    def visit(node: yaml.Node | None, path: str) -> None:
        if isinstance(node, yaml.MappingNode):
            keys: set[str] = set()
            for key, value in node.value:
                if not isinstance(key, yaml.ScalarNode) or key.value in keys:
                    _fail(record, f"ambiguous REVIEW mapping at {path}")
                keys.add(key.value)
                visit(value, f"{path}.{key.value}")
        elif isinstance(node, yaml.SequenceNode):
            for index, value in enumerate(node.value):
                visit(value, f"{path}[{index}]")

    visit(root, "REVIEW")


def _json_mapping(source: bytes, label: str, record: PublicationOutcome) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(source.decode("utf-8", errors="strict"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        _fail(record, f"malformed {label}: {exc}")
    if not isinstance(value, Mapping):
        _fail(record, f"{label} must be a JSON object")
    return value


def _source_run(run_data: Mapping[str, Any], record: PublicationOutcome) -> Mapping[str, Any]:
    if "kind" not in run_data:
        return run_data
    if run_data.get("kind") != "REMEDIATION":
        _fail(record, "canonical RUN has an unknown lineage kind")
    execution = run_data.get("execution")
    if not isinstance(execution, Mapping) or not isinstance(execution.get("run"), Mapping):
        _fail(record, "canonical remediation RUN lineage is malformed")
    return execution["run"]


def _validate_success_artifacts(
    run_bytes: bytes,
    result_bytes: bytes,
    *,
    run_id: str,
    reviewed_sha: str,
    record: PublicationOutcome,
) -> tuple[str, str, int, Result, Mapping[str, Any]]:
    run_root = _json_mapping(run_bytes, "RUN", record)
    run = _source_run(run_root, record)
    if run.get("run_id") != run_id:
        _fail(record, "RUN-ID mismatch between event and canonical artifacts")
    base_sha = run.get("base_sha")
    if not isinstance(base_sha, str) or not _SHA.fullmatch(base_sha):
        _fail(record, "canonical RUN base_sha is invalid")
    if run.get("status") != "ACTIVE":
        _fail(record, "canonical RUN status is invalid")
    if run.get("head_sha") not in (None, reviewed_sha):
        _fail(record, "canonical RUN head_sha conflicts with REVIEW")
    if run.get("executor") not in ("codex", "antigravity"):
        _fail(record, "canonical RUN executor is invalid")
    task = run.get("task")
    if not isinstance(task, Mapping) or not isinstance(task.get("id"), str):
        _fail(record, "canonical RUN task reference is malformed")
    task_id = task["id"]
    task_revision = task.get("revision")
    if (
        not re.fullmatch(r"TASK-[A-Za-z0-9][A-Za-z0-9.-]*", task_id)
        or isinstance(task_revision, bool)
        or not isinstance(task_revision, int)
        or task_revision < 1
    ):
        _fail(record, "canonical RUN task reference is malformed")

    package = _json_mapping(result_bytes, "ResultPackage", record)
    if set(package) != {"result", "evidence"}:
        _fail(record, "canonical ResultPackage fields are malformed")
    try:
        result = validate_result(package["result"])
    except (ArtifactValidationError, TypeError) as exc:
        _fail(record, f"canonical RESULT is invalid: {exc}")
    if result.head_sha != reviewed_sha:
        _fail(record, "REVIEW/RESULT SHA mismatch")
    evidence_data = package["evidence"]
    if not isinstance(evidence_data, list) or not evidence_data:
        _fail(record, "canonical successful artifacts have no verification evidence")
    evidence_by_id = {}
    try:
        for value in evidence_data:
            item = validate_evidence(value)
            if item.evidence_id in evidence_by_id:
                _fail(record, f"duplicate canonical evidence id: {item.evidence_id}")
            evidence_by_id[item.evidence_id] = item
            if item.run_id != run_id:
                _fail(record, "canonical evidence RUN-ID mismatch")
            if item.subject_sha != reviewed_sha:
                _fail(record, "canonical evidence SHA mismatch")
            if item.result.exit_code != 0:
                _fail(record, "canonical artifacts contain unsuccessful evidence")
    except (ArtifactValidationError, TypeError) as exc:
        _fail(record, f"canonical EVIDENCE is invalid: {exc}")
    for claim in result.claims:
        if any(item not in evidence_by_id for item in claim.evidence):
            _fail(record, f"claim {claim.id} references missing canonical evidence")
    return base_sha, task_id, task_revision, result, run_root


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            (
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"cannot establish publication ancestry: {exc}") from exc
    if completed.returncode not in (0, 1):
        raise RuntimeError("cannot establish publication ancestry")
    return completed.returncode == 0


def publish_review_decision(
    repo: Path,
    *,
    remote: str,
    run_id: str,
    decision_sha: str,
) -> PublicationOutcome:
    """Validate canonical lineage and advance main to the exact reviewed candidate."""

    root = Path(repo).resolve()
    record = PublicationOutcome(run_id, "UNRESOLVED", "UNRESOLVED", "FAILED", "")
    if not _RUN_ID.fullmatch(run_id):
        _fail(record, "invalid RUN-ID in review-decision event")
    if not _SHA.fullmatch(decision_sha):
        _fail(record, "invalid review-decision event SHA")

    decision_ref = f"{_DECISION_PREFIX}{run_id}"
    source_ref = f"refs/heads/aios/review/{run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"
    main_ref = "refs/heads/main"
    refs = _remote_refs(
        root, remote, (decision_ref, source_ref, artifacts_ref, main_ref), record
    )
    record = replace(
        record,
        reviewed_sha=refs.get(source_ref, "UNRESOLVED"),
        prior_main_sha=refs.get(main_ref, "UNRESOLVED"),
    )
    if _require_ref(refs, decision_ref, record) != decision_sha:
        _fail(record, "review-decision event is stale or does not match its canonical ref")
    source_sha = _require_ref(refs, source_ref, record)
    artifacts_sha = _require_ref(refs, artifacts_ref, record)
    prior_main = _require_ref(refs, main_ref, record)

    for sha, label in (
        (decision_sha, "review-decision"),
        (source_sha, "source"),
        (artifacts_sha, "artifacts"),
        (prior_main, "main"),
    ):
        _fetch_commit(root, remote, sha, label, record)

    review_path = _decision_review_path(root, decision_sha, record)
    review_bytes = _read_blob(root, decision_sha, review_path, "REVIEW document", record)
    try:
        review_source = review_bytes.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        _fail(record, f"malformed REVIEW encoding: {exc}")
    _reject_duplicate_yaml_keys(review_source, record)
    try:
        review = parse_review(review_source)
    except ReviewValidationError as exc:
        _fail(record, f"malformed REVIEW: {exc}")
    if not _SHA.fullmatch(review.reviewed_sha):
        _fail(record, "REVIEW reviewed_sha is not a full Git SHA")
    record = replace(record, reviewed_sha=review.reviewed_sha)
    if review.verdict != "PASS":
        _fail(record, f"REVIEW verdict is {review.verdict}, not PASS")
    if not review.acceptance or any(value != "PASS" for value in review.acceptance.values()):
        _fail(record, "PASS REVIEW must contain only successful acceptance decisions")
    if review.reviewed_sha != source_sha:
        _fail(record, "REVIEW does not match the canonical RUN source ref")

    run_bytes = _read_blob(
        root, artifacts_sha, ".ai/transport/run.json", "canonical RUN artifact", record
    )
    result_bytes = _read_blob(
        root,
        artifacts_sha,
        ".ai/transport/result.json",
        "canonical ResultPackage artifact",
        record,
    )
    base_sha, task_id, task_revision, result, run_root = _validate_success_artifacts(
        run_bytes,
        result_bytes,
        run_id=run_id,
        reviewed_sha=review.reviewed_sha,
        record=record,
    )
    task_bytes = _read_blob(
        root,
        review.reviewed_sha,
        f".ai/tasks/{task_id}.yaml",
        "canonical TASK contract",
        record,
    )
    try:
        task_contract = parse_task(task_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, TaskValidationError) as exc:
        _fail(record, f"canonical TASK is invalid: {exc}")
    if task_contract.task_id != task_id or task_contract.revision != task_revision:
        _fail(record, "canonical RUN/TASK identity mismatch")
    try:
        if review.mode == "PRIMARY":
            validate_review(task=task_contract, result=result, review=review)
        else:
            execution = run_root.get("execution")
            finding = execution.get("finding") if isinstance(execution, Mapping) else None
            if (
                run_root.get("kind") != "REMEDIATION"
                or not isinstance(finding, Mapping)
                or review.prior_finding_id != finding.get("id")
            ):
                _fail(record, "DELTA REVIEW does not match remediation RUN lineage")
            unknown = set(review.acceptance).difference(
                criterion.id for criterion in task_contract.acceptance
            )
            if unknown:
                _fail(record, "DELTA REVIEW references unknown TASK acceptance")
    except ReviewValidationError as exc:
        _fail(record, f"REVIEW/TASK validation failed: {exc}")
    _fetch_commit(root, remote, base_sha, "RUN base", record)
    try:
        if not _is_ancestor(root, base_sha, review.reviewed_sha):
            _fail(record, "reviewed candidate is not descended from its RUN base")
        if _is_ancestor(root, decision_sha, review.reviewed_sha):
            _fail(record, "review-decision metadata is contained in the product candidate")
        if prior_main != review.reviewed_sha and not _is_ancestor(
            root, prior_main, review.reviewed_sha
        ):
            _fail(record, "remote main has diverged from the reviewed candidate")
    except RuntimeError as exc:
        _fail(record, str(exc))

    current = _remote_refs(
        root, remote, (decision_ref, source_ref, artifacts_ref, main_ref), record
    )
    if current != refs:
        _fail(record, "canonical publication refs changed during preflight")
    if prior_main == review.reviewed_sha:
        return replace(record, outcome="ALREADY_PUBLISHED", detail="main already equals reviewed SHA")

    try:
        # The lease is only the compare-and-swap race guard. The candidate was
        # proven above to be a descendant of this exact expected main, and the
        # refspec is non-forcing, so the only eligible mutation is a fast-forward.
        _git(
            root,
            "push",
            "--porcelain",
            "--no-tags",
            f"--force-with-lease={main_ref}:{prior_main}",
            remote,
            f"{review.reviewed_sha}:{main_ref}",
        )
    except RuntimeError as exc:
        _fail(record, f"race-safe fast-forward publication failed: {exc}")
    final = _remote_refs(root, remote, (main_ref,), record)
    if _require_ref(final, main_ref, record) != review.reviewed_sha:
        _fail(record, "remote main postcondition does not equal reviewed SHA")
    return replace(record, outcome="PUBLISHED", detail="main advanced to reviewed SHA")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--decision-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        outcome = publish_review_decision(
            arguments.repo,
            remote=arguments.remote,
            run_id=arguments.run_id,
            decision_sha=arguments.decision_sha,
        )
    except PublicationError as exc:
        print(json.dumps(asdict(exc.outcome), sort_keys=True))
        return 1
    print(json.dumps(asdict(outcome), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
