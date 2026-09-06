import json
import subprocess
from pathlib import Path

import pytest

import aios_renew.publication as publication_module
from aios_renew.publication import PublicationError, publish_review_decision
from aios_renew.review_transport import transport_failure, transport_post_pass


TASK_SOURCE = """\
task_id: TASK-063
revision: 2
goal: Publish an exact reviewed candidate.
problem: Publication is a deterministic coordination step.
assumptions: []
scope:
  inspect: []
  modify: [product.txt]
non_goals: [Do not publish review metadata.]
constraints:
  hard: [Publish only canonical PASS state.]
acceptance:
  - id: AC1
    condition: The candidate is publishable.
verification:
  required: [git diff --check]
"""


def git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=check,
    ).stdout.strip()


def review_source(
    reviewed_sha: str,
    verdict: str = "PASS",
    *,
    remediation: bool = False,
) -> str:
    if verdict == "CHANGES_REQUIRED":
        acceptance = "{AC1: FAIL}"
        findings = """\
  - id: R1
    basis: AC1
    action: CODE_FIX
    location: product.txt
    issue: The candidate needs a correction.
    expected: Correct the candidate.
"""
        findings_value = f"\n{findings}"
    else:
        acceptance = "{AC1: PASS}"
        findings_value = "[]"
    mode = "DELTA" if remediation else "PRIMARY"
    prior_finding = "prior_finding_id: R1\n" if remediation else ""
    return f"""\
review_id: REVIEW-063-001
reviewed_sha: {reviewed_sha}
mode: {mode}
verdict: {verdict}
acceptance: {acceptance}
{prior_finding}findings: {findings_value}
"""


def result_payload(
    run_id: str, head_sha: str, *, remediation: bool = False
) -> dict:
    claims = [] if remediation else [
        {
            "id": "C1",
            "satisfies": ["AC1"],
            "claim": "The candidate is publishable.",
            "evidence": ["E1"],
        }
    ]
    return {
        "result": {
            "head_sha": head_sha,
            "claims": claims,
            "changed_files": ["product.txt"],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id,
                "subject_sha": head_sha,
                "type": "verification",
                "source": {"command": "git diff --check"},
                "result": {"exit_code": 0, "summary": "clean"},
                "raw": {"path": ".git/aios/evidence/E1.log"},
            }
        ],
    }


def make_lineage(
    root: Path,
    *,
    run_id: str = "RUN-063-001",
    artifact_run_id: str | None = None,
    result_sha: str | None = None,
    review_sha: str | None = None,
    verdict: str = "PASS",
    review_documents: int = 1,
    intermediate_candidate: bool = False,
    remediation: bool = False,
    remediation_scope: tuple[str, ...] = ("product.txt",),
) -> dict[str, object]:
    repo = root / "repo"
    remote = root / "upstream.git"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "AIOS Publication Test")
    git(repo, "config", "user.email", "publication@example.invalid")
    git(repo, "branch", "-M", "main")
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK-063.yaml").write_text(TASK_SOURCE, encoding="utf-8")
    (repo / "product.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base")
    base_sha = git(repo, "rev-parse", "HEAD")
    subprocess.run(("git", "init", "--bare", "--quiet", str(remote)), check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "main")

    intermediate_sha = None
    if intermediate_candidate:
        (repo / "product.txt").write_text(
            "intermediate candidate\n", encoding="utf-8"
        )
        git(repo, "add", "product.txt")
        git(repo, "commit", "--quiet", "-m", "intermediate candidate")
        intermediate_sha = git(repo, "rev-parse", "HEAD")
    (repo / "product.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", "product.txt")
    git(repo, "commit", "--quiet", "-m", "candidate")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    state = root / "state"
    state.mkdir()
    run_path = state / "run.json"
    result_path = state / "result.json"
    operational_run = {
        "run_id": artifact_run_id or run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    run_payload = operational_run
    if remediation:
        run_payload = {
            "kind": "REMEDIATION",
            "execution": {
                "review_id": "REVIEW-063-000",
                "finding": {
                    "id": "R1",
                    "basis": "AC1",
                    "action": "CODE_FIX",
                    "location": "product.txt",
                    "issue": "The prior candidate needs a narrow correction.",
                    "expected": "Commit the corrected product.",
                },
                "remediation": {
                    "finding_id": "R1",
                    "action": "CODE_FIX",
                    "reviewed_sha": base_sha,
                    "modification_scope": list(remediation_scope),
                    "affected_verification": ["git diff --check"],
                    "constraints": [],
                },
                "run": operational_run,
                "original_constraints": [],
            },
        }
    run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    result_path.write_text(
        json.dumps(
            result_payload(
                run_id,
                result_sha or candidate_sha,
                remediation=remediation,
            )
        ),
        encoding="utf-8",
    )
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=candidate_sha,
        run_path=run_path,
        result_path=result_path,
    )

    review_dir = repo / ".ai" / "reviews"
    if review_documents:
        review_dir.mkdir(parents=True)
        source = review_source(
            review_sha or candidate_sha,
            verdict,
            remediation=remediation,
        )
        for index in range(review_documents):
            (review_dir / f"REVIEW-063-{index + 1:03}.yaml").write_text(
                source, encoding="utf-8"
            )
    else:
        metadata = repo / ".ai" / "decision.txt"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text("no review\n", encoding="utf-8")
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", "review decision metadata")
    decision_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"HEAD:refs/heads/aios/review-decision/{run_id}",
    )
    return {
        "repo": repo,
        "remote": remote,
        "run_id": run_id,
        "base_sha": base_sha,
        "intermediate_sha": intermediate_sha,
        "candidate_sha": candidate_sha,
        "decision_sha": decision_sha,
    }


def publish(lineage: dict[str, object]):
    return publish_review_decision(
        lineage["repo"],
        run_id=lineage["run_id"],
        decision_sha=lineage["decision_sha"],
    )


def remote_main(lineage: dict[str, object]) -> str:
    return git(lineage["remote"], "rev-parse", "refs/heads/main")


def _push_repair_authorization(
    repo: Path, *, failed_run_id: str, authorization: dict
) -> None:
    subject_sha = git(repo, "rev-parse", "HEAD")
    path = repo / ".ai" / "transport" / "repair.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(authorization), encoding="utf-8")
    git(repo, "add", ".ai/transport/repair.json")
    git(repo, "commit", "--quiet", "-m", "canonical repair authorization")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"HEAD:refs/heads/aios/repair/{failed_run_id}",
    )
    git(repo, "reset", "--hard", "--quiet", subject_sha)


def make_repair_lineage(
    root: Path,
    *,
    predecessor_kind: str = "REMEDIATION",
    lineage_mutation: str | None = None,
) -> dict[str, object]:
    repo = root / "repo"
    remote = root / "upstream.git"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "AIOS Publication Test")
    git(repo, "config", "user.email", "publication@example.invalid")
    git(repo, "branch", "-M", "main")
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK-063.yaml").write_text(TASK_SOURCE, encoding="utf-8")
    (repo / "product.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base")
    base_sha = git(repo, "rev-parse", "HEAD")
    subprocess.run(("git", "init", "--bare", "--quiet", str(remote)), check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "main")

    failed_run_id = "RUN-063-003"
    run_id = "RUN-063-004"
    (repo / "product.txt").write_text("failed remediation state\n", encoding="utf-8")
    git(repo, "add", "product.txt")
    git(repo, "commit", "--quiet", "-m", "failed correction candidate")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    predecessor_run = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    if predecessor_kind == "REMEDIATION":
        predecessor_payload = {
            "kind": "REMEDIATION",
            "execution": {
                "review_id": "REVIEW-063-001",
                "finding": {
                    "id": "R1",
                    "basis": "AC1",
                    "action": "CODE_FIX",
                    "location": "product.txt",
                    "issue": "The candidate needs a correction.",
                    "expected": "Correct the candidate.",
                },
                "remediation": {
                    "finding_id": "R1",
                    "action": "CODE_FIX",
                    "reviewed_sha": base_sha,
                    "modification_scope": ["product.txt"],
                    "affected_verification": ["git diff --check"],
                    "constraints": [],
                },
                "run": predecessor_run,
                "original_constraints": [],
            },
        }
    else:
        predecessor_payload = predecessor_run
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "phase": "VERIFICATION",
        "error": {"type": "RuntimeVerificationError", "message": "failed"},
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["product.txt"],
            "outside_task_scope": [],
        },
    }
    state = root / "state"
    state.mkdir()
    failed_run_path = state / "failed-run.json"
    failure_path = state / "failure.json"
    failed_run_path.write_text(json.dumps(predecessor_payload), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failed_head_sha,
        run_path=failed_run_path,
        failure_path=failure_path,
    )

    authorization = {
        "repair_id": "REPAIR-063-003",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-063", "revision": 2},
        "action": "CODE_FIX",
        "modification_scope": ["product.txt"],
        "instructions": ["Repair only the failed candidate."],
        "constraints": [],
    }
    _push_repair_authorization(
        repo, failed_run_id=failed_run_id, authorization=authorization
    )

    (repo / "product.txt").write_text("repaired candidate\n", encoding="utf-8")
    git(repo, "add", "product.txt")
    git(repo, "commit", "--quiet", "-m", "repair failed candidate")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    successful_run = {
        "run_id": run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": failed_head_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    persisted_authorization = dict(authorization)
    if lineage_mutation == "unauthorized":
        persisted_authorization["instructions"] = ["Different authorization."]
    persisted_failure = dict(failure)
    if lineage_mutation == "conflicting_failure":
        persisted_failure["run_id"] = "RUN-063-999"
    persisted_failed_head = failed_head_sha
    if lineage_mutation == "wrong_failed_head":
        persisted_failed_head = base_sha
    lineage = {
        "failed_run_id": failed_run_id,
        "root_base_sha": base_sha,
        "failed_head_sha": persisted_failed_head,
        "failure": persisted_failure,
        "task": {"task_id": "TASK-063", "revision": 2},
        "repair": persisted_authorization,
        "run": successful_run,
    }
    run_path = state / "run.json"
    result_path = state / "result.json"
    lineage_path = state / "repair.json"
    run_path.write_text(json.dumps(successful_run), encoding="utf-8")
    result_path.write_text(
        json.dumps(result_payload(run_id, candidate_sha)), encoding="utf-8"
    )
    lineage_path.write_text(json.dumps(lineage), encoding="utf-8")
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=candidate_sha,
        run_path=run_path,
        result_path=result_path,
        lineage_path=lineage_path,
    )

    review_dir = repo / ".ai" / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "REVIEW-063-002.yaml").write_text(
        review_source(
            candidate_sha, remediation=predecessor_kind == "REMEDIATION"
        ).replace("REVIEW-063-001", "REVIEW-063-002"),
        encoding="utf-8",
    )
    git(repo, "add", ".ai/reviews/REVIEW-063-002.yaml")
    git(repo, "commit", "--quiet", "-m", "review repaired candidate")
    decision_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"HEAD:refs/heads/aios/review-decision/{run_id}",
    )
    return {
        "repo": repo,
        "remote": remote,
        "run_id": run_id,
        "base_sha": base_sha,
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "candidate_sha": candidate_sha,
        "decision_sha": decision_sha,
    }


def test_pass_publication_moves_main_to_source_without_review_commit(
    tmp_path: Path,
) -> None:
    lineage = make_lineage(tmp_path)

    report = publish(lineage)

    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert report.prior_main_sha == lineage["base_sha"]
    assert report.outcome == "PUBLISHED"
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert remote_main(lineage) != lineage["decision_sha"]
    assert (
        git(
            lineage["remote"],
            "show",
            f"{remote_main(lineage)}:.ai/reviews/REVIEW-063-001.yaml",
            check=False,
        )
        == ""
    )


def test_pass_reviewed_remediation_publication_moves_main_to_exact_source(
    tmp_path: Path,
) -> None:
    lineage = make_lineage(tmp_path, remediation=True)

    report = publish(lineage)

    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert report.outcome == "PUBLISHED"
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert remote_main(lineage) != lineage["decision_sha"]


def test_repair_of_failed_remediation_reconstructs_delta_review_lineage(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(tmp_path)

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.source_run == "RUN-063-004"
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]


def test_repair_before_semantic_finding_accepts_primary_review(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(tmp_path, predecessor_kind="PRIMARY")

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert remote_main(lineage) == lineage["candidate_sha"]


@pytest.mark.parametrize(
    "lineage_mutation, match",
    [
        ("wrong_failed_head", "base_sha does not match failed_head_sha"),
        ("conflicting_failure", "FAILURE identity mismatch"),
        ("unauthorized", "authorization is not canonical"),
    ],
)
def test_malformed_repair_lineage_does_not_mutate_main(
    tmp_path: Path, lineage_mutation: str, match: str
) -> None:
    lineage = make_repair_lineage(
        tmp_path, lineage_mutation=lineage_mutation
    )

    with pytest.raises(PublicationError, match=match):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_missing_canonical_repair_authorization_does_not_mutate_main(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(tmp_path)
    git(
        lineage["remote"],
        "update-ref",
        "-d",
        f"refs/heads/aios/repair/{lineage['failed_run_id']}",
    )

    with pytest.raises(PublicationError, match="missing or ambiguous"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


@pytest.mark.parametrize("verdict", ["CHANGES_REQUIRED", "BLOCKED"])
def test_non_pass_decisions_do_not_mutate_main(
    tmp_path: Path, verdict: str
) -> None:
    lineage = make_lineage(tmp_path, verdict=verdict)

    with pytest.raises(PublicationError, match="not PASS"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


@pytest.mark.parametrize("review_documents", [0, 2])
def test_missing_or_ambiguous_review_does_not_mutate_main(
    tmp_path: Path, review_documents: int
) -> None:
    lineage = make_lineage(tmp_path, review_documents=review_documents)

    with pytest.raises(PublicationError, match="exactly one REVIEW"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_malformed_review_does_not_mutate_main(tmp_path: Path) -> None:
    lineage = make_lineage(tmp_path)
    repo = lineage["repo"]
    review_path = repo / ".ai" / "reviews" / "REVIEW-063-001.yaml"
    review_path.write_text("verdict: [not valid\n", encoding="utf-8")
    git(repo, "add", ".ai/reviews/REVIEW-063-001.yaml")
    git(repo, "commit", "--quiet", "-m", "malformed decision")
    decision_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--quiet",
        "--force",
        "origin",
        f"HEAD:refs/heads/aios/review-decision/{lineage['run_id']}",
    )
    lineage["decision_sha"] = decision_sha

    with pytest.raises(PublicationError, match="invalid canonical"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_run_id_mismatch_does_not_mutate_main(tmp_path: Path) -> None:
    lineage = make_lineage(tmp_path, artifact_run_id="RUN-063-999")

    with pytest.raises(PublicationError, match="RUN-ID mismatch"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_remediation_run_id_mismatch_does_not_mutate_main(tmp_path: Path) -> None:
    lineage = make_lineage(
        tmp_path,
        remediation=True,
        artifact_run_id="RUN-063-999",
    )

    with pytest.raises(PublicationError, match="RUN-ID mismatch"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_malformed_remediation_scope_does_not_mutate_main(tmp_path: Path) -> None:
    lineage = make_lineage(
        tmp_path,
        remediation=True,
        remediation_scope=(),
    )

    with pytest.raises(PublicationError, match="modification scope is empty"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_missing_success_artifacts_do_not_mutate_main(tmp_path: Path) -> None:
    lineage = make_lineage(tmp_path)
    git(
        lineage["remote"],
        "update-ref",
        "-d",
        f"refs/heads/aios/artifacts/{lineage['run_id']}",
    )

    with pytest.raises(PublicationError, match="missing or ambiguous"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


@pytest.mark.parametrize("mismatch", ["review", "result"])
def test_review_or_result_sha_mismatch_does_not_mutate_main(
    tmp_path: Path, mismatch: str
) -> None:
    other = "1" * 40
    lineage = make_lineage(
        tmp_path,
        review_sha=other if mismatch == "review" else None,
        result_sha=other if mismatch == "result" else None,
    )

    with pytest.raises(PublicationError, match="invalid canonical"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_divergent_candidate_requires_integration_before_lease_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lineage = make_lineage(tmp_path)
    repo = lineage["repo"]
    git(repo, "checkout", "--quiet", "-b", "diverged", lineage["base_sha"])
    (repo / "product.txt").write_text("diverged\n", encoding="utf-8")
    git(repo, "add", "product.txt")
    git(repo, "commit", "--quiet", "-m", "diverged main")
    diverged_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    real_git = publication_module._git
    push_attempted = False
    commands: list[tuple[str, ...]] = []

    def recording_git(repo_path, *args, **kwargs):
        nonlocal push_attempted
        commands.append(tuple(args))
        if args and args[0] == "push":
            push_attempted = True
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_git", recording_git)

    with pytest.raises(PublicationError, match="integration candidate") as raised:
        publish(lineage)

    assert push_attempted is False
    assert remote_main(lineage) == diverged_sha
    assert raised.value.report.source_run == lineage["run_id"]
    assert raised.value.report.reviewed_sha == lineage["candidate_sha"]
    assert raised.value.report.prior_main_sha == diverged_sha
    assert raised.value.report.outcome == "INTEGRATION_REQUIRED"
    assert not any(
        command and command[0] in {"merge", "rebase", "cherry-pick"}
        for command in commands
    )


def test_concurrent_main_update_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lineage = make_lineage(tmp_path)
    repo = lineage["repo"]
    git(repo, "checkout", "--quiet", "-b", "concurrent", lineage["base_sha"])
    (repo / "concurrent.txt").write_text("concurrent\n", encoding="utf-8")
    git(repo, "add", "concurrent.txt")
    git(repo, "commit", "--quiet", "-m", "concurrent update")
    concurrent_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        "HEAD:refs/heads/aios/test-concurrent",
    )
    real_git = publication_module._git
    raced = False

    def racing_git(repo_path, *args, **kwargs):
        nonlocal raced
        if args and args[0] == "push" and not raced:
            raced = True
            git(
                lineage["remote"],
                "update-ref",
                "refs/heads/main",
                concurrent_sha,
                lineage["base_sha"],
            )
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_git", racing_git)

    with pytest.raises(PublicationError, match="publication failed"):
        publish(lineage)

    assert raced is True
    assert remote_main(lineage) == concurrent_sha


def test_compatible_concurrent_main_update_fails_exact_sha_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lineage = make_lineage(tmp_path, intermediate_candidate=True)
    real_git = publication_module._git
    raced = False

    def racing_git(repo_path, *args, **kwargs):
        nonlocal raced
        if args and args[0] == "push" and not raced:
            raced = True
            git(
                lineage["remote"],
                "update-ref",
                "refs/heads/main",
                lineage["intermediate_sha"],
                lineage["base_sha"],
            )
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_git", racing_git)

    with pytest.raises(PublicationError, match="publication failed"):
        publish(lineage)

    assert raced is True
    assert remote_main(lineage) == lineage["intermediate_sha"]


def test_already_published_is_an_idempotent_no_op(tmp_path: Path) -> None:
    lineage = make_lineage(tmp_path)
    git(
        lineage["repo"],
        "push",
        "--quiet",
        "origin",
        f"{lineage['candidate_sha']}:refs/heads/main",
    )
    before = remote_main(lineage)

    report = publish(lineage)

    assert report.outcome == "ALREADY_PUBLISHED"
    assert remote_main(lineage) == before == lineage["candidate_sha"]
    assert remote_main(lineage) != lineage["decision_sha"]


def test_reviewed_sha_already_contained_in_main_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lineage = make_lineage(tmp_path)
    repo = lineage["repo"]
    git(repo, "checkout", "--quiet", "--detach", lineage["candidate_sha"])
    (repo / "later.txt").write_text("later product state\n", encoding="utf-8")
    git(repo, "add", "later.txt")
    git(repo, "commit", "--quiet", "-m", "later published product")
    later_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    real_git = publication_module._git
    push_attempted = False

    def recording_git(repo_path, *args, **kwargs):
        nonlocal push_attempted
        if args and args[0] == "push":
            push_attempted = True
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_git", recording_git)

    report = publish(lineage)

    assert report.outcome == "ALREADY_INCLUDED"
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert report.prior_main_sha == later_sha
    assert push_attempted is False
    assert remote_main(lineage) == later_sha


def test_canonical_decision_replay_uses_immutable_identity_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lineage = make_lineage(tmp_path)
    first = publish(lineage)
    refs_before = git(lineage["remote"], "show-ref")
    review_before = git(
        lineage["remote"],
        "show",
        f"{lineage['decision_sha']}:.ai/reviews/REVIEW-063-001.yaml",
    )
    result_before = git(
        lineage["remote"],
        "show",
        f"refs/heads/aios/artifacts/{lineage['run_id']}:.ai/transport/result.json",
    )
    real_run = subprocess.run
    commands: list[tuple[str, ...]] = []

    def recording_run(command, **kwargs):
        commands.append(tuple(command))
        return real_run(command, **kwargs)

    monkeypatch.setattr(publication_module.subprocess, "run", recording_run)

    replay = publish_review_decision(
        lineage["repo"],
        run_id=lineage["run_id"],
        decision_sha=lineage["decision_sha"],
        control_sha=lineage["candidate_sha"],
    )

    assert first.outcome == "PUBLISHED"
    assert replay.outcome == "ALREADY_PUBLISHED"
    assert refs_before == git(lineage["remote"], "show-ref")
    assert review_before == git(
        lineage["remote"],
        "show",
        f"{lineage['decision_sha']}:.ai/reviews/REVIEW-063-001.yaml",
    )
    assert result_before == git(
        lineage["remote"],
        "show",
        f"refs/heads/aios/artifacts/{lineage['run_id']}:.ai/transport/result.json",
    )
    assert commands and all(command[0] == "git" for command in commands)


def test_publication_executes_git_coordination_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lineage = make_lineage(tmp_path)
    real_run = subprocess.run
    commands: list[tuple[str, ...]] = []

    def recording_run(command, **kwargs):
        commands.append(tuple(command))
        return real_run(command, **kwargs)

    monkeypatch.setattr(publication_module.subprocess, "run", recording_run)

    publish(lineage)

    assert commands
    assert all(command[0] == "git" for command in commands)
    invoked_executables = {Path(command[0]).name.lower() for command in commands}
    assert invoked_executables.isdisjoint(
        {"pytest", "codex", "antigravity", "model"}
    )


def test_workflow_has_canonical_trigger_and_minimum_authority() -> None:
    workflow = Path(".github/workflows/aios-auto-publish.yml").read_text(
        encoding="utf-8"
    )

    assert '"aios/review-decision/**"' in workflow
    assert "contents: write" in workflow
    assert "actions: write" not in workflow
    assert "pull-requests: write" not in workflow
    assert "secrets." not in workflow
    assert "aios_renew.publication" in workflow
    assert "workflow_dispatch:" in workflow
    assert "ref: main" in workflow
    assert "ref: ${{ github.sha }}" not in workflow
    assert "git ls-remote --refs origin \"$decision_ref\"" in workflow
    assert "git fetch --no-tags origin refs/heads/main" in workflow
    assert 'git checkout --detach "$control_sha"' in workflow
    assert "AIOS_REPLAY_RUN_ID: ${{ inputs.run_id }}" in workflow
    assert "candidate_sha" not in workflow
    assert "decision-sha \"$decision_sha\"" in workflow
    assert "control-sha \"$control_sha\"" in workflow
    assert "aios run" not in workflow
    assert "aios remediate" not in workflow
    assert "aios repair" not in workflow
    assert "pytest" not in workflow
