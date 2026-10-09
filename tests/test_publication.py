import json
import re
import subprocess
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from threading import Event

import pytest
import yaml

from aios_renew import brain_attention as brain
import aios_renew.publication as publication_module
from aios_renew.publication import PublicationError, publish_review_decision
from aios_renew.review_transport import transport_failure, transport_post_pass
from tests.git_fixture_support import (
    commit_fixture_state,
    materialize_git_baseline,
    read_git_ref,
)


def test_repair_package_uses_integrated_result_base_for_attribution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    integrated_base = "1" * 40
    repaired_head = "2" * 40
    observed: list[tuple[str, str]] = []
    package = object()
    monkeypatch.setattr(
        publication_module,
        "validate_result_package",
        lambda **_kwargs: package,
    )
    monkeypatch.setattr(
        publication_module,
        "_changed_files",
        lambda _repo, base, head: observed.append((base, head))
        or {"product.txt"},
    )

    actual = publication_module._validate_repair_package(
        tmp_path,
        source_sha=repaired_head,
        result_base_sha=integrated_base,
        task=SimpleNamespace(scope=SimpleNamespace(modify=("product.txt",))),
        run=object(),
        result=SimpleNamespace(changed_files=("product.txt",)),
        evidence=(),
    )

    assert actual is package
    assert observed == [(integrated_base, repaired_head)]


TASK_SOURCE = """\
task_id: TASK-063
revision: 2
goal: Publish an exact reviewed candidate.
problem: Publication is a deterministic coordination step.
assumptions: []
scope:
  inspect: []
  modify: [product.txt, secondary.txt]
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
    if check and args == ("rev-parse", "HEAD"):
        return read_git_ref(repo)
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=check,
    ).stdout.strip()


def materialize_publication_baseline(
    root: Path, *, secondary: bool = False
) -> tuple[Path, Path, str]:
    files = {
        ".ai/tasks/TASK-063.yaml": TASK_SOURCE,
        "product.txt": "base\n",
    }
    if secondary:
        files["secondary.txt"] = "base\n"
    return materialize_git_baseline(
        root,
        files=files,
        user_name="AIOS Publication Test",
        user_email="publication@example.invalid",
        commit_message="base",
    )


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
    run_id: str,
    head_sha: str,
    *,
    remediation: bool = False,
    changed_files: tuple[str, ...] = ("product.txt",),
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
            "changed_files": list(changed_files),
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
    repo, remote, base_sha = materialize_publication_baseline(root)

    intermediate_sha = None
    if intermediate_candidate:
        (repo / "product.txt").write_text(
            "intermediate candidate\n", encoding="utf-8"
        )
        intermediate_sha = commit_fixture_state(
            repo,
            paths=("product.txt",),
            message="intermediate candidate",
            user_name="AIOS Publication Test",
            user_email="publication@example.invalid",
        )
    (repo / "product.txt").write_text("candidate\n", encoding="utf-8")
    candidate_sha = commit_fixture_state(
        repo,
        paths=("product.txt",),
        message="candidate",
        user_name="AIOS Publication Test",
        user_email="publication@example.invalid",
    )
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
    decision_sha = commit_fixture_state(
        repo,
        paths=(".ai",),
        message="review decision metadata",
        user_name="AIOS Publication Test",
        user_email="publication@example.invalid",
        remote=remote,
        remote_ref=f"refs/heads/aios/review-decision/{run_id}",
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


def make_integrated_remediation_repair_lineage(
    root: Path,
    *,
    execution_base_mutation: str | None = None,
    result_changed_files: tuple[str, ...] = ("product.txt",),
    prior_finding_id: str = "R1",
) -> dict[str, object]:
    """Turn the production-shaped integrated REMEDIATION into a repaired RUN."""

    lineage = make_integrated_predecessor_lineage(root)
    repo = lineage["repo"]
    remote = lineage["remote"]
    failed_run_id = lineage["run_id"]
    run_id = "RUN-063-003"
    failed_head_sha = lineage["candidate_sha"]
    remediation_run = json.loads(json.dumps(lineage["remediation_run"]))
    execution_base = remediation_run["execution_base"]
    if execution_base_mutation == "authorized_main":
        execution_base["authorized_main_sha"] = lineage["base_sha"]
    elif execution_base_mutation == "integration_candidate":
        execution_base["integration_candidate_sha"] = failed_head_sha

    for ref in (
        f"refs/heads/aios/artifacts/{failed_run_id}",
        f"refs/heads/aios/review/{failed_run_id}",
        f"refs/heads/aios/review-decision/{failed_run_id}",
    ):
        git(remote, "update-ref", "-d", ref)
    git(repo, "reset", "--hard", "--quiet", failed_head_sha)

    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": lineage["integration_candidate_sha"],
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
    state = root / "integrated-repair-state"
    state.mkdir()
    failed_run_path = state / "failed-run.json"
    failure_path = state / "failure.json"
    failed_run_path.write_text(json.dumps(remediation_run), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failed_head_sha,
        run_path=failed_run_path,
        failure_path=failure_path,
    )

    authorization = {
        "repair_id": "REPAIR-063-002",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-063", "revision": 2},
        "action": "CODE_FIX",
        "modification_scope": ["product.txt"],
        "instructions": ["Repair the failed integrated remediation."],
        "constraints": [],
    }
    authorization_sha = _push_repair_authorization(
        repo, failed_run_id=failed_run_id, authorization=authorization
    )
    (repo / "product.txt").write_text(
        "repaired integrated remediation\n", encoding="utf-8"
    )
    candidate_sha = commit_fixture_state(
        repo,
        paths=("product.txt",),
        message="repair integrated remediation",
        user_name="AIOS Publication Test",
        user_email="publication@example.invalid",
    )
    repair_run = {
        "run_id": run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": failed_head_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    repair_lineage = {
        "failed_run_id": failed_run_id,
        "root_base_sha": lineage["base_sha"],
        "result_base_sha": lineage["integration_candidate_sha"],
        "failed_head_sha": failed_head_sha,
        "failure": failure,
        "task": {"task_id": "TASK-063", "revision": 2},
        "repair": authorization,
        "repair_authorization_sha": authorization_sha,
        "run": repair_run,
    }
    run_path = state / "run.json"
    result_path = state / "result.json"
    repair_path = state / "repair.json"
    run_path.write_text(json.dumps(repair_run), encoding="utf-8")
    result_path.write_text(
        json.dumps(
            result_payload(
                run_id,
                candidate_sha,
                changed_files=result_changed_files,
            )
        ),
        encoding="utf-8",
    )
    repair_path.write_text(json.dumps(repair_lineage), encoding="utf-8")
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=candidate_sha,
        run_path=run_path,
        result_path=result_path,
        lineage_path=repair_path,
    )

    review = f"""review_id: REVIEW-063-003
reviewed_sha: {candidate_sha}
mode: DELTA
verdict: PASS
prior_finding_id: {prior_finding_id}
acceptance:
  AC1: PASS
findings: []
"""
    review_path = repo / ".ai" / "reviews" / "REVIEW-063-003.yaml"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(review, encoding="utf-8")
    decision_sha = commit_fixture_state(
        repo,
        paths=(".ai/reviews/REVIEW-063-003.yaml",),
        message="review repaired integrated remediation",
        user_name="AIOS Publication Test",
        user_email="publication@example.invalid",
        remote=remote,
        remote_ref=f"refs/heads/aios/review-decision/{run_id}",
    )
    return {
        **lineage,
        "run_id": run_id,
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
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
) -> str:
    subject_sha = git(repo, "rev-parse", "HEAD")
    path = repo / ".ai" / "transport" / "repair.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(authorization), encoding="utf-8")
    auth_sha = commit_fixture_state(
        repo,
        paths=(".ai/transport/repair.json",),
        message="canonical repair authorization",
        user_name="AIOS Publication Test",
        user_email="publication@example.invalid",
        remote=repo.parent / "upstream.git",
        remote_ref=f"refs/heads/aios/repair/{failed_run_id}",
    )
    git(repo, "reset", "--hard", "--quiet", subject_sha)
    return auth_sha


def _push_repair_supersession(
    repo: Path,
    *,
    failed_run_id: str,
    revision: int,
    predecessor_repair_sha: str,
    failure_artifacts_sha: str,
    authorization: dict,
) -> str:
    branch = git(repo, "branch", "--show-current")
    git(repo, "checkout", "--quiet", "--detach", predecessor_repair_sha)
    repair_path = repo / ".ai" / "transport" / "repair.json"
    supersession_path = repo / ".ai" / "transport" / "repair-supersession.json"
    repair_path.parent.mkdir(parents=True, exist_ok=True)
    repair_path.write_text(json.dumps(authorization), encoding="utf-8")
    supersession_payload = {
        "format": "AIOS_REPAIR_SUPERSESSION",
        "version": 1,
        "failed_run_id": failed_run_id,
        "authorization_revision": revision,
        "predecessor_repair_sha": predecessor_repair_sha,
        "failure_artifacts_sha": failure_artifacts_sha,
    }
    supersession_path.write_text(json.dumps(supersession_payload), encoding="utf-8")
    supersession_sha = commit_fixture_state(
        repo,
        paths=(
            ".ai/transport/repair.json",
            ".ai/transport/repair-supersession.json",
        ),
        message=f"superseding repair authorization r{revision}",
        user_name="AIOS Publication Test",
        user_email="publication@example.invalid",
        remote=repo.parent / "upstream.git",
        remote_ref=(
            f"refs/heads/aios/repair-supersession/{failed_run_id}/{revision}"
        ),
    )
    if branch:
        git(repo, "checkout", "--quiet", branch)
    return supersession_sha


def make_repair_lineage(
    root: Path,
    *,
    predecessor_kind: str = "REMEDIATION",
    lineage_mutation: str | None = None,
    action: str = "CODE_FIX",
    phase: str | None = None,
    repair_scope: tuple[str, ...] = ("product.txt",),
    failure_overrides: dict[str, object] | None = None,
    candidate_overrides: dict[str, object] | None = None,
    failed_state: str = "mutation",
    final_state: str = "mutation",
    recursive_continue: bool = False,
    recursive_action: str = "CONTINUE_IMPLEMENTATION",
    recursive_failure_phase: str = "COMPLETION_GATE",
    recursive_authorization_conflict: bool = False,
    include_repair_authorization_sha: bool = False,
    repair_authorization_sha: str | int | None = None,
    supersede_authorization: bool = False,
    supersede_repair: dict | None = None,
    supersede_revision_count: int = 1,
    use_superseded_authorization: bool | int = False,
) -> dict[str, object]:
    repo, remote, base_sha = materialize_publication_baseline(
        root, secondary=True
    )

    state = root / "state"
    state.mkdir()
    prior_lineage = None
    prior_failure = None
    prior_authorization = None
    if recursive_continue:
        prior_failed_run_id = "RUN-063-002"
        prior_run = {
            "run_id": prior_failed_run_id,
            "task": {"id": "TASK-063", "revision": 2},
            "executor": "codex",
            "base_sha": base_sha,
            "workspace": str(repo),
            "head_sha": None,
            "status": "ACTIVE",
        }
        prior_failure = {
            "kind": "FAILURE",
            "run_id": prior_failed_run_id,
            "task": {"id": "TASK-063", "revision": 2},
            "executor": "codex",
            "base_sha": base_sha,
            "failed_head_sha": base_sha,
            "phase": recursive_failure_phase,
            "error": {"type": "RuntimeCompletionError", "message": "blocked"},
            "candidate": {
                "transportable": True,
                "repairable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        }
        prior_run_path = state / "prior-failed-run.json"
        prior_failure_path = state / "prior-failure.json"
        prior_run_path.write_text(json.dumps(prior_run), encoding="utf-8")
        prior_failure_path.write_text(json.dumps(prior_failure), encoding="utf-8")
        transport_failure(
            repo,
            run_id=prior_failed_run_id,
            head_sha=base_sha,
            run_path=prior_run_path,
            failure_path=prior_failure_path,
        )
        prior_authorization = {
            "repair_id": "REPAIR-063-002",
            "failed_run_id": prior_failed_run_id,
            "failed_head_sha": base_sha,
            "task": {"id": "TASK-063", "revision": 2},
            "action": recursive_action,
            "modification_scope": ["product.txt"],
            "instructions": ["Continue the unfinished implementation."],
            "constraints": [],
        }
        _push_repair_authorization(
            repo,
            failed_run_id=prior_failed_run_id,
            authorization=prior_authorization,
        )

    failed_run_id = "RUN-063-003"
    run_id = "RUN-063-004"
    if failed_state == "mutation":
        (repo / "product.txt").write_text(
            "failed remediation state\n", encoding="utf-8"
        )
        commit_fixture_state(
            repo,
            paths=("product.txt",),
            message="failed correction candidate",
            user_name="AIOS Publication Test",
            user_email="publication@example.invalid",
        )
    elif failed_state == "empty_commit":
        commit_fixture_state(
            repo,
            paths=(),
            message="empty failed state",
            user_name="AIOS Publication Test",
            user_email="publication@example.invalid",
        )
    elif failed_state != "unchanged":
        raise ValueError(f"unknown failed_state: {failed_state}")
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
    if recursive_continue:
        persisted_prior_authorization = dict(prior_authorization)
        if recursive_authorization_conflict:
            persisted_prior_authorization["instructions"] = [
                "Conflicting continuation authorization."
            ]
        prior_lineage = {
            "failed_run_id": "RUN-063-002",
            "root_base_sha": base_sha,
            "failed_head_sha": base_sha,
            "failure": prior_failure,
            "task": {"task_id": "TASK-063", "revision": 2},
            "repair": persisted_prior_authorization,
            "run": predecessor_run,
        }
    failed_changed_files = tuple(
        path
        for path in git(
            repo, "diff", "--name-only", base_sha, failed_head_sha
        ).splitlines()
        if path
    )
    failure_candidate = {
        "transportable": True,
        "repairable": True,
        "dirty": False,
        "descends_from_base": True,
        "changed_files": list(failed_changed_files),
        "outside_task_scope": [],
    }
    if candidate_overrides:
        failure_candidate.update(candidate_overrides)
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "phase": (
            phase
            if phase is not None
            else (
                "COMPLETION_GATE"
                if action == "CONTINUE_IMPLEMENTATION"
                else "VERIFICATION"
            )
        ),
        "error": {"type": "RuntimeVerificationError", "message": "failed"},
        "candidate": failure_candidate,
    }
    if failure_overrides:
        failure.update(failure_overrides)
    failed_run_path = state / "failed-run.json"
    failure_path = state / "failure.json"
    failed_run_path.write_text(json.dumps(predecessor_payload), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    predecessor_lineage_path = None
    if prior_lineage is not None:
        predecessor_lineage_path = state / "prior-repair.json"
        predecessor_lineage_path.write_text(
            json.dumps(prior_lineage), encoding="utf-8"
        )
    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failed_head_sha,
        run_path=failed_run_path,
        failure_path=failure_path,
        lineage_path=predecessor_lineage_path,
    )

    authorization = {
        "repair_id": "REPAIR-063-003",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-063", "revision": 2},
        "action": action,
        "modification_scope": list(repair_scope),
        "instructions": ["Repair only the failed candidate."],
        "constraints": [],
    }
    first_auth_sha = _push_repair_authorization(
        repo, failed_run_id=failed_run_id, authorization=authorization
    )
    auth_sha = first_auth_sha
    auth_shas_by_revision = {1: first_auth_sha}
    auth_payloads_by_revision = {1: dict(authorization)}
    num_supersessions = (
        supersede_revision_count
        if (supersede_authorization or supersede_repair is not None or supersede_revision_count > 1)
        else 0
    )
    if num_supersessions > 0:
        failure_artifacts_sha = git(
            remote, "rev-parse", f"refs/heads/aios/failure-artifacts/{failed_run_id}"
        )
        current_pred_sha = first_auth_sha
        for rev_idx in range(2, 2 + num_supersessions):
            successor_auth = (
                supersede_repair
                if (supersede_repair is not None and rev_idx == 1 + num_supersessions)
                else {
                    **authorization,
                    "instructions": [f"Superseded instructions for repair r{rev_idx}."],
                }
            )
            current_pred_sha = _push_repair_supersession(
                repo,
                failed_run_id=failed_run_id,
                revision=rev_idx,
                predecessor_repair_sha=current_pred_sha,
                failure_artifacts_sha=failure_artifacts_sha,
                authorization=successor_auth,
            )
            auth_shas_by_revision[rev_idx] = current_pred_sha
            auth_payloads_by_revision[rev_idx] = dict(successor_auth)
        auth_sha = current_pred_sha

    if isinstance(use_superseded_authorization, int) and not isinstance(
        use_superseded_authorization, bool
    ):
        authorization = auth_payloads_by_revision[use_superseded_authorization]
    elif use_superseded_authorization is True:
        authorization = auth_payloads_by_revision[1]
    elif num_supersessions > 0:
        authorization = auth_payloads_by_revision[1 + num_supersessions]

    if final_state == "mutation":
        (repo / "product.txt").write_text(
            "repaired candidate\n", encoding="utf-8"
        )
        commit_fixture_state(
            repo,
            paths=("product.txt",),
            message="repair failed candidate",
            user_name="AIOS Publication Test",
            user_email="publication@example.invalid",
        )
    elif final_state == "outside_scope":
        (repo / "secondary.txt").write_text(
            "unauthorized mutation\n", encoding="utf-8"
        )
        commit_fixture_state(
            repo,
            paths=("secondary.txt",),
            message="mutate outside repair scope",
            user_name="AIOS Publication Test",
            user_email="publication@example.invalid",
        )
    elif final_state == "empty_commit":
        commit_fixture_state(
            repo,
            paths=(),
            message="empty repair",
            user_name="AIOS Publication Test",
            user_email="publication@example.invalid",
        )
    elif final_state == "divergent":
        git(repo, "reset", "--hard", "--quiet", base_sha)
        (repo / "product.txt").write_text("divergent candidate\n", encoding="utf-8")
        commit_fixture_state(
            repo,
            paths=("product.txt",),
            message="divergent repair candidate",
            user_name="AIOS Publication Test",
            user_email="publication@example.invalid",
        )
    elif final_state != "unchanged":
        raise ValueError(f"unknown final_state: {final_state}")
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
    if repair_authorization_sha == "use_predecessor":
        resolved_repair_auth_sha = auth_shas_by_revision[max(1, len(auth_shas_by_revision) - 1)]
    elif isinstance(repair_authorization_sha, int) and not isinstance(
        repair_authorization_sha, bool
    ):
        resolved_repair_auth_sha = auth_shas_by_revision[repair_authorization_sha]
    elif use_superseded_authorization is True:
        resolved_repair_auth_sha = first_auth_sha
    elif isinstance(use_superseded_authorization, int) and not isinstance(
        use_superseded_authorization, bool
    ):
        resolved_repair_auth_sha = auth_shas_by_revision[use_superseded_authorization]
    elif repair_authorization_sha is not None:
        resolved_repair_auth_sha = repair_authorization_sha
    else:
        resolved_repair_auth_sha = auth_sha

    if include_repair_authorization_sha or repair_authorization_sha is not None:
        lineage["repair_authorization_sha"] = resolved_repair_auth_sha
    if lineage_mutation == "extra_field":
        lineage["unexpected_field"] = "illegal"
    run_path = state / "run.json"
    result_path = state / "result.json"
    lineage_path = state / "repair.json"
    run_path.write_text(json.dumps(successful_run), encoding="utf-8")
    result_path.write_text(
        json.dumps(
            result_payload(
                run_id,
                candidate_sha,
                changed_files=tuple(
                    path
                    for path in git(
                        repo, "diff", "--name-only", base_sha, candidate_sha
                    ).splitlines()
                    if path
                ),
            )
        ),
        encoding="utf-8",
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
    decision_sha = commit_fixture_state(
        repo,
        paths=(".ai/reviews/REVIEW-063-002.yaml",),
        message="review repaired candidate",
        user_name="AIOS Publication Test",
        user_email="publication@example.invalid",
        remote=remote,
        remote_ref=f"refs/heads/aios/review-decision/{run_id}",
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
        "failure": failure,
        "authorization": authorization,
        "successful_run": successful_run,
        "repair_lineage": lineage,
        "authorization_commit_sha": auth_sha,
        "first_authorization_commit_sha": first_auth_sha,
        "authorization_shas_by_revision": auth_shas_by_revision,
        "prior_failure": prior_failure,
        "prior_authorization": prior_authorization,
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


def test_no_change_repair_publication_remains_unchanged(tmp_path: Path) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="NO_CHANGE",
        repair_scope=(),
        final_state="unchanged",
    )

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.reviewed_sha == lineage["failed_head_sha"]
    assert remote_main(lineage) == lineage["failed_head_sha"]
    assert remote_main(lineage) != lineage["decision_sha"]


def test_continue_implementation_publication_preserves_exact_primary_lineage(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CONTINUE_IMPLEMENTATION",
        phase="COMPLETION_GATE",
        failed_state="unchanged",
    )

    report = publish(lineage)

    failure = lineage["failure"]
    authorization = lineage["authorization"]
    successful_run = lineage["successful_run"]
    repair_lineage = lineage["repair_lineage"]
    assert report.outcome == "PUBLISHED"
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert remote_main(lineage) != lineage["decision_sha"]
    assert (
        git(
            lineage["remote"],
            "show",
            f"{remote_main(lineage)}:.ai/reviews/REVIEW-063-002.yaml",
            check=False,
        )
        == ""
    )
    assert failure["run_id"] == authorization["failed_run_id"]
    assert failure["task"] == authorization["task"] == {
        "id": "TASK-063",
        "revision": 2,
    }
    assert (
        failure["failed_head_sha"]
        == authorization["failed_head_sha"]
        == successful_run["base_sha"]
        == lineage["failed_head_sha"]
    )
    assert failure["base_sha"] == lineage["base_sha"]
    assert repair_lineage["root_base_sha"] == lineage["base_sha"]
    assert repair_lineage["failed_run_id"] == lineage["failed_run_id"]
    assert repair_lineage["task"] == {"task_id": "TASK-063", "revision": 2}
    assert repair_lineage["repair"] == authorization
    assert authorization["action"] == "CONTINUE_IMPLEMENTATION"
    assert authorization["modification_scope"] == ["product.txt"]
    transported_result = json.loads(
        git(
            lineage["remote"],
            "show",
            f"refs/heads/aios/artifacts/{lineage['run_id']}:"
            ".ai/transport/result.json",
        )
    )
    assert transported_result["result"]["head_sha"] == lineage["candidate_sha"]
    assert transported_result["result"]["changed_files"] == ["product.txt"]


@pytest.mark.parametrize(
    ("case", "kwargs", "match"),
    [
        (
            "verification phase",
            {"phase": "VERIFICATION", "failed_state": "unchanged"},
            "requires a pre-verification failure",
        ),
        (
            "not transportable",
            {
                "failed_state": "unchanged",
                "candidate_overrides": {"transportable": False},
            },
            "requires a clean transportable candidate",
        ),
        (
            "not repairable",
            {
                "failed_state": "unchanged",
                "candidate_overrides": {"repairable": False},
            },
            "not authorized as repairable",
        ),
        (
            "dirty",
            {
                "failed_state": "unchanged",
                "candidate_overrides": {"dirty": True},
            },
            "requires a clean transportable candidate",
        ),
        (
            "not descended from base",
            {
                "failed_state": "unchanged",
                "candidate_overrides": {"descends_from_base": False},
            },
            "requires a clean transportable candidate",
        ),
        (
            "outside task scope",
            {
                "failed_state": "unchanged",
                "candidate_overrides": {"outside_task_scope": ["FOREIGN.txt"]},
            },
            "requires a clean transportable candidate",
        ),
        (
            "empty authorization scope",
            {"failed_state": "unchanged", "repair_scope": ()},
            "modification scope is empty",
        ),
        (
            "scope exceeds correction authority",
            {
                "failed_state": "unchanged",
                "repair_scope": ("FOREIGN.txt",),
            },
            "scope exceeds correction authority",
        ),
        (
            "canonical authorization conflict",
            {"failed_state": "unchanged", "lineage_mutation": "unauthorized"},
            "authorization is not canonical",
        ),
        (
            "unchanged final head",
            {"failed_state": "unchanged", "final_state": "unchanged"},
            "committed delta is empty",
        ),
        (
            "empty committed delta",
            {"failed_state": "unchanged", "final_state": "empty_commit"},
            "committed delta is empty",
        ),
        (
            "divergent final head",
            {"failed_state": "mutation", "final_state": "divergent"},
            "does not descend from RUN base_sha",
        ),
        (
            "mutation outside repair scope",
            {"failed_state": "unchanged", "final_state": "outside_scope"},
            "delta exceeds modification scope",
        ),
    ],
)
def test_invalid_continue_implementation_lineage_does_not_mutate_main(
    tmp_path: Path, case: str, kwargs: dict[str, object], match: str
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CONTINUE_IMPLEMENTATION",
        **kwargs,
    )

    with pytest.raises(PublicationError, match=match):
        publish(lineage)

    assert case
    assert remote_main(lineage) == lineage["base_sha"]


def test_recursive_repair_lineage_accepts_historical_continue_implementation(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        recursive_continue=True,
    )

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert report.prior_main_sha == lineage["base_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert remote_main(lineage) != lineage["decision_sha"]


def test_finalize_candidate_publication_preserves_unchanged_failed_subject(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="FINALIZE_CANDIDATE",
        phase="EXECUTION",
        repair_scope=(),
        failed_state="mutation",
        final_state="unchanged",
    )

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.reviewed_sha == lineage["failed_head_sha"]
    assert lineage["candidate_sha"] == lineage["failed_head_sha"]
    assert remote_main(lineage) == lineage["failed_head_sha"]


def test_recursive_repair_accepts_truthful_zero_delta_continue_failure(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CONTINUE_IMPLEMENTATION",
        phase="EXECUTION",
        failed_state="unchanged",
        recursive_continue=True,
    )

    report = publish(lineage)

    prior_failure = lineage["prior_failure"]
    prior_authorization = lineage["prior_authorization"]
    assert report.outcome == "PUBLISHED"
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert report.prior_main_sha == lineage["base_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert remote_main(lineage) != lineage["decision_sha"]
    assert prior_failure["run_id"] == prior_authorization["failed_run_id"]
    assert prior_failure["task"] == prior_authorization["task"] == {
        "id": "TASK-063",
        "revision": 2,
    }
    assert (
        prior_failure["base_sha"]
        == prior_failure["failed_head_sha"]
        == prior_authorization["failed_head_sha"]
        == lineage["base_sha"]
    )
    assert prior_failure["candidate"]["changed_files"] == []
    assert lineage["repair_lineage"]["root_base_sha"] == lineage["base_sha"]
    transported_result = json.loads(
        git(
            lineage["remote"],
            "show",
            f"refs/heads/aios/artifacts/{lineage['run_id']}:"
            ".ai/transport/result.json",
        )
    )
    assert transported_result["result"]["head_sha"] == lineage["candidate_sha"]
    assert transported_result["result"]["changed_files"] == ["product.txt"]
    assert (
        git(
            lineage["remote"],
            "show",
            f"{remote_main(lineage)}:.ai/reviews/REVIEW-063-002.yaml",
            check=False,
        )
        == ""
    )


def test_recursive_repair_accepts_truthful_zero_delta_code_fix_failure(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CONTINUE_IMPLEMENTATION",
        phase="EXECUTION",
        failed_state="unchanged",
        recursive_continue=True,
        recursive_action="CODE_FIX",
        recursive_failure_phase="VERIFICATION",
    )

    report = publish(lineage)

    failure = lineage["failure"]
    prior_failure = lineage["prior_failure"]
    prior_authorization = lineage["prior_authorization"]
    authorization = lineage["authorization"]
    assert report.outcome == "PUBLISHED"
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert prior_failure["phase"] == "VERIFICATION"
    assert prior_authorization["action"] == "CODE_FIX"
    assert authorization["action"] == "CONTINUE_IMPLEMENTATION"
    assert (
        failure["base_sha"]
        == failure["failed_head_sha"]
        == lineage["successful_run"]["base_sha"]
        == lineage["base_sha"]
    )
    assert failure["phase"] == "EXECUTION"
    assert failure["candidate"] == {
        "transportable": True,
        "repairable": True,
        "dirty": False,
        "descends_from_base": True,
        "changed_files": [],
        "outside_task_scope": [],
    }


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {"failure_overrides": {"run_id": "RUN-063-999"}},
            "FAILURE identity",
        ),
        (
            {"failure_overrides": {"executor": "antigravity"}},
            "FAILURE identity",
        ),
        (
            {
                "failure_overrides": {
                    "task": {"id": "TASK-063", "revision": 1}
                }
            },
            "FAILURE identity",
        ),
        (
            {"failure_overrides": {"failed_head_sha": "0" * 40}},
            "FAILURE identity",
        ),
        (
            {"phase": "VERIFICATION"},
            "requires a pre-verification failure",
        ),
        (
            {"candidate_overrides": {"transportable": False}},
            "requires a clean transportable candidate",
        ),
        (
            {"candidate_overrides": {"repairable": False}},
            "not authorized as repairable",
        ),
        (
            {"candidate_overrides": {"dirty": True}},
            "requires a clean transportable candidate",
        ),
        (
            {"candidate_overrides": {"descends_from_base": False}},
            "requires a clean transportable candidate",
        ),
        (
            {
                "candidate_overrides": {"outside_task_scope": ["secondary.txt"]}
            },
            "requires a clean transportable candidate",
        ),
        (
            {"candidate_overrides": {"changed_files": ["product.txt"]}},
            "FAILURE candidate changed-files binding",
        ),
        (
            {"failed_state": "empty_commit"},
            "zero-delta CODE_FIX failure facts are invalid",
        ),
        (
            {"recursive_authorization_conflict": True},
            "authorization is not canonical",
        ),
        (
            {"failure_overrides": {"base_sha": "0" * 40}},
            "predecessor identity mismatch",
        ),
    ],
)
def test_invalid_zero_delta_code_fix_failure_does_not_mutate_main(
    tmp_path: Path, kwargs: dict[str, object], match: str
) -> None:
    settings: dict[str, object] = {
        "phase": "EXECUTION",
        "failed_state": "unchanged",
    }
    settings.update(kwargs)
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CONTINUE_IMPLEMENTATION",
        recursive_continue=True,
        recursive_action="CODE_FIX",
        **settings,
    )

    with pytest.raises(PublicationError, match=match):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


@pytest.mark.parametrize("final_state", ["unchanged", "empty_commit"])
def test_successful_code_fix_still_requires_committed_delta(
    tmp_path: Path, final_state: str
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        failed_state="mutation",
        final_state=final_state,
    )

    with pytest.raises(PublicationError, match="CODE_FIX REPAIR committed delta is empty"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_zero_delta_continue_requires_canonical_repair_authorization(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CONTINUE_IMPLEMENTATION",
        phase="EXECUTION",
        failed_state="unchanged",
        recursive_continue=True,
    )
    git(
        lineage["remote"],
        "update-ref",
        "-d",
        "refs/heads/aios/repair/RUN-063-002",
    )

    with pytest.raises(PublicationError, match="missing or ambiguous"):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


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


def test_divergent_candidate_classifies_conflict_before_lease_mutation(
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

    with pytest.raises(PublicationError, match="semantic conflict resolution") as raised:
        publish(lineage)

    assert push_attempted is False
    assert remote_main(lineage) == diverged_sha
    assert raised.value.report.source_run == lineage["run_id"]
    assert raised.value.report.reviewed_sha == lineage["candidate_sha"]
    assert raised.value.report.prior_main_sha == diverged_sha
    assert raised.value.report.outcome == "RECOVERY_BLOCKED"
    assert raised.value.report.cause == "MERGE_CONFLICT"
    assert raised.value.report.recovery is None
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
    main_pushes: list[tuple[str, ...]] = []

    def racing_git(repo_path, *args, **kwargs):
        nonlocal raced
        # Reservation pushes precede the exact-main CAS boundary under test.
        if args and args[0] == "push" and args[-1].endswith(":refs/heads/main"):
            main_pushes.append(tuple(args))
            if not raced:
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

    with pytest.raises(PublicationError, match="publication failed") as raised:
        publish(lineage)

    assert raced is True
    assert len(main_pushes) == 1
    assert main_pushes[0][-1] == f"{lineage['candidate_sha']}:refs/heads/main"
    assert f"--force-with-lease=refs/heads/main:{lineage['base_sha']}" in main_pushes[0]
    assert raised.value.report.reviewed_sha == lineage["candidate_sha"]
    assert raised.value.report.cause == "MAIN_CAS_FAILED"
    assert remote_main(lineage) == concurrent_sha


def test_compatible_concurrent_main_update_fails_exact_sha_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lineage = make_lineage(tmp_path, intermediate_candidate=True)
    real_git = publication_module._git
    raced = False
    main_pushes: list[tuple[str, ...]] = []

    def racing_git(repo_path, *args, **kwargs):
        nonlocal raced
        # Compatible ancestry still must reject movement at the exact-main CAS.
        if args and args[0] == "push" and args[-1].endswith(":refs/heads/main"):
            main_pushes.append(tuple(args))
            if not raced:
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

    with pytest.raises(PublicationError, match="publication failed") as raised:
        publish(lineage)

    assert raced is True
    assert len(main_pushes) == 1
    assert main_pushes[0][-1] == f"{lineage['candidate_sha']}:refs/heads/main"
    assert f"--force-with-lease=refs/heads/main:{lineage['base_sha']}" in main_pushes[0]
    assert raised.value.report.reviewed_sha == lineage["candidate_sha"]
    assert raised.value.report.cause == "MAIN_CAS_FAILED"
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
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CONTINUE_IMPLEMENTATION",
        failed_state="unchanged",
    )
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


def publication_workflow():
    text = Path(".github/workflows/aios-auto-publish.yml").read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def publication_condition(expression, context, prior_failed=False):
    # Include the implicit success gate so an eligible failed publisher is covered.
    if prior_failed and "always()" not in expression:
        return False
    expression = expression.replace("always()", "True")
    expression = re.sub(r"\b(?:needs|steps|vars|github)(?:\.[A-Za-z0-9_-]+)+",
                        lambda match: repr(context.get(match[0], "")), expression)
    return bool(eval("(" + expression.replace("&&", " and ") + ")", {"__builtins__": {}}, {}))


def test_publication_direct_handoff_reuses_only_existing_selector_boundary():
    parsed, text = publication_workflow()
    assert set(parsed["on"]) == {"push", "workflow_dispatch"}
    assert parsed["permissions"] == {"contents": "write"}
    assert set(parsed["jobs"]) == {"publish", "local-chat-wake"}
    publish_job = parsed["jobs"]["publish"]
    assert publish_job["outputs"] == {"event_id": "${{ steps.attention.outputs.event_id }}"}
    steps = publish_job["steps"]
    by_id = {step["id"]: step for step in steps if "id" in step}
    assert steps.index(by_id["publication"]) < steps.index(by_id["attention-source"])
    assert steps.index(by_id["attention-source"]) < steps.index(by_id["attention-artifact"]) < steps.index(by_id["attention"])
    assert "aios_renew.brain_attention publication" in by_id["attention-source"]["run"]
    assert '--output "$GITHUB_OUTPUT"' in by_id["attention-source"]["run"]
    assert by_id["attention-artifact"]["uses"] == "actions/upload-artifact@v4"
    assert by_id["attention-artifact"]["with"] == {
        "name": "aios-attention-source-v1-publication-attempt-${{ github.run_attempt }}",
        "path": "${{ runner.temp }}/aios-attention-publication/source.json",
        "if-no-files-found": "ignore", "retention-days": "90",
    }
    direct = by_id["attention"]
    assert "aios_renew.brain_attention publication-event" in direct["run"]
    assert direct["env"] == {
        "PYTHONPATH": "src", "AIOS_ATTENTION_SOURCE_PATH": "${{ runner.temp }}/aios-attention-publication/source.json",
        "AIOS_SOURCE_RUN": "${{ github.run_id }}", "AIOS_SOURCE_ATTEMPT": "${{ github.run_attempt }}",
        "AIOS_SOURCE_ARTIFACT": "${{ steps.attention-artifact.outputs.artifact-id }}",
        "AIOS_SOURCE_DIGEST": "${{ steps.attention-source.outputs.source_digest }}",
        "AIOS_SOURCE_REPOSITORY": "${{ github.repository }}",
    }
    handoff = parsed["jobs"]["local-chat-wake"]
    assert handoff["needs"] == "publish"
    assert handoff["uses"] == "./.github/workflows/aios-local-chat-wake.yml"
    assert handoff["permissions"] == {"contents": "read", "actions": "read"}
    assert handoff["with"] == {"event_id": "${{ needs.publish.outputs.event_id }}", "repository": brain.REPOSITORY}
    assert set(handoff) == {"needs", "if", "uses", "permissions", "with"}
    for forbidden in ("actions: write", "repository_dispatch", "createWorkflowDispatch", "gh workflow run",
                      "secrets.", "issues:", "comments", "workflow_run", "schedule:", "next_action",
                      "review_verdict", "roadmap_successor", "cdp_endpoint", "assistant_output",
                      "PUBLICATION_PROVEN", "PUBLICATION_SUCCESS_REQUIRING_HUMAN_BRAIN_PLANNING"):
        assert forbidden not in text


def test_publication_reusable_handoff_covers_callee_permission_ceiling() -> None:
    parsed, _ = publication_workflow()
    handoff = parsed["jobs"]["local-chat-wake"]
    caller_permissions = handoff["permissions"]
    assert set(caller_permissions.values()) == {"read"}
    callee = yaml.load(
        Path(handoff["uses"]).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    levels = {"none": 0, "read": 1, "write": 2}
    required_permissions = set()
    # The call chain must cover declared permissions even on gated/skipped jobs.
    for job_name, job in callee["jobs"].items():
        permissions = job.get("permissions", callee.get("permissions", {}))
        assert isinstance(permissions, dict), job_name
        for permission, level in permissions.items():
            assert levels[level] <= levels[caller_permissions.get(permission, "none")], (
                job_name, permission, level
            )
            if level != "none":
                required_permissions.add(permission)
    assert set(caller_permissions) == required_permissions


@pytest.mark.parametrize("prior_failed", [False, True])
@pytest.mark.parametrize("changed,value,allowed", [
    (None, None, True), ("steps.attention-source.outcome", "failure", False),
    ("steps.attention-source.outcome", "skipped", False),
    ("steps.attention-source.outputs.source_digest", "", False),
    ("steps.attention-artifact.outcome", "failure", False),
    ("steps.attention-artifact.outcome", "skipped", False),
    ("steps.attention-artifact.outputs.artifact-id", "", False),
])
def test_publication_direct_projection_requires_exact_export_even_after_failure(prior_failed, changed, value, allowed):
    parsed, _ = publication_workflow()
    steps = {step["id"]: step for step in parsed["jobs"]["publish"]["steps"] if "id" in step}
    context = {"steps.attention-source.outcome": "success", "steps.attention-source.outputs.source_digest": "a" * 64,
               "steps.attention-artifact.outcome": "success", "steps.attention-artifact.outputs.artifact-id": "200"}
    if changed:
        context[changed] = value
    assert publication_condition(steps["attention"]["if"], context, prior_failed) is allowed
    context.update({"steps.publication.outputs.run_id": "RUN-fixture-001", "steps.publication.outputs.decision_sha": "b" * 40})
    assert publication_condition(steps["attention-source"]["if"], context, prior_failed)


@pytest.mark.parametrize("prior_failed", [False, True])
@pytest.mark.parametrize("changed,value,allowed", [
    (None, None, True), ("needs.publish.outputs.event_id", "", False),
    ("github.repository", "other/repository", False),
    ("vars.AIOS_LOCAL_CHAT_WAKE_ENABLED", "", False),
    ("vars.AIOS_LOCAL_CHAT_WAKE_ENABLED", "false", False),
    ("vars.AIOS_LOCAL_CHAT_WAKE_ENABLED", "TRUE", False),
])
def test_publication_reusable_handoff_preserves_human_gate_without_success_only_filter(prior_failed, changed, value, allowed):
    parsed, _ = publication_workflow()
    context = {"needs.publish.outputs.event_id": "exact-projected-identity", "github.repository": brain.REPOSITORY,
               "vars.AIOS_LOCAL_CHAT_WAKE_ENABLED": "true"}
    if changed:
        context[changed] = value
    assert publication_condition(parsed["jobs"]["local-chat-wake"]["if"], context, prior_failed) is allowed


def test_exact_reviewed_publication_and_replay_export_same_direct_planning_identity(tmp_path: Path) -> None:
    lineage = make_lineage(tmp_path)
    event_ids = []
    for index, outcome in enumerate(("PUBLISHED", "ALREADY_PUBLISHED")):
        report = publish(lineage)
        assert report.outcome == outcome
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps({name: getattr(report, name) for name in
            ("source_run", "reviewed_sha", "prior_main_sha", "outcome", "detail")}), encoding="utf-8")
        source_path = tmp_path / "source.json"
        source = brain.capture_publication(report_path, source_path, lineage["run_id"],
                                           lineage["decision_sha"], repo=lineage["repo"])
        event_id = brain.publication_event(source_path, 100 + index, 1, 200 + index, brain.digest(source))
        item = brain.parse_event_id(event_id)
        assert item.family == brain.PUBLICATION_SUCCESS
        assert dict(item.selectors) == dict(run_id=lineage["run_id"],
            artifact_sha=git(lineage["remote"], "rev-parse", "refs/heads/aios/artifacts/" + lineage["run_id"]),
            decision_sha=lineage["decision_sha"], reviewed_sha=lineage["candidate_sha"],
            published_sha=lineage["candidate_sha"], source_boundary="PUBLICATION_PROVEN")
        event_ids.append(event_id)
    assert event_ids[0] == event_ids[1]


def advance_divergent_main(lineage, *, rename=False, control_branch=False):
    repo = lineage["repo"]
    git(repo, "checkout", "--quiet", "--detach", lineage["base_sha"])
    if rename:
        git(repo, "mv", "product.txt", "renamed.txt")
    else:
        (repo / "later.txt").write_text("later main work\n", encoding="utf-8")
        git(repo, "add", "later.txt")
    git(repo, "commit", "--quiet", "-m", "later canonical main")
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    if control_branch:
        # Recovery writes require explicit upstream authority on current-main
        # control, just as the production publisher's checkout of main does.
        branch = "fixture/publication-control"
        git(repo, "checkout", "--quiet", "-b", branch, main_sha)
        git(repo, "config", f"branch.{branch}.remote", "origin")
        git(repo, "config", f"branch.{branch}.merge", "refs/heads/main")
    return main_sha


def add_sibling_pass(lineage, run_id="RUN-063-002"):
    """A canonical Runtime-success transport and PASS on a sibling source."""
    repo = lineage["repo"]
    branch = f"fixture/{run_id}"
    git(repo, "checkout", "--quiet", "-b", branch, lineage["base_sha"])
    git(repo, "config", f"branch.{branch}.remote", "origin")
    git(repo, "config", f"branch.{branch}.merge", "refs/heads/main")
    (repo / "product.txt").write_text("later candidate\n", encoding="utf-8")
    candidate = commit_fixture_state(
        repo, paths=("product.txt",), message="later reviewed source",
        user_name="AIOS Publication Test", user_email="publication@example.invalid")
    state = repo.parent / "later-state"
    state.mkdir()
    run_path, result_path = state / "run.json", state / "result.json"
    run_path.write_text(json.dumps(dict(
        run_id=run_id, task=dict(id="TASK-063", revision=2), executor="codex",
        base_sha=lineage["base_sha"], workspace=str(repo), head_sha=None, status="ACTIVE")),
        encoding="utf-8")
    result_path.write_text(json.dumps(result_payload(run_id, candidate)), encoding="utf-8")
    transport_post_pass(repo, run_id=run_id, head_sha=candidate,
                        run_path=run_path, result_path=result_path)
    review_dir = repo / ".ai" / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "REVIEW-063-002.yaml").write_text(
        review_source(candidate).replace("REVIEW-063-001", "REVIEW-063-002"), encoding="utf-8")
    decision = commit_fixture_state(
        repo, paths=(".ai",), message="later canonical PASS",
        user_name="AIOS Publication Test", user_email="publication@example.invalid",
        remote=lineage["remote"], remote_ref=f"refs/heads/aios/review-decision/{run_id}")
    return dict(lineage, run_id=run_id, candidate_sha=candidate, decision_sha=decision)


def test_clean_divergence_exposes_only_an_ineligible_tree_and_one_actionable_attention(tmp_path):
    lineage = make_lineage(tmp_path)
    main_sha = advance_divergent_main(lineage)
    refs_before = git(lineage["remote"], "show-ref")
    with pytest.raises(PublicationError) as raised:
        publish(lineage)
    report = raised.value.report
    assert report.outcome == "RECOVERY_REVIEW_REQUIRED"
    assert report.cause == "FRESH_EXACT_REVIEW_REQUIRED"
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert report.prior_main_sha == main_sha
    assert report.recovery.merge_base_sha == lineage["base_sha"]
    assert report.recovery.publication_eligible is False
    assert git(lineage["repo"], "cat-file", "-t", report.recovery.tree_sha) == "tree"
    assert "Runtime verification and fresh Reviewer PASS" in report.detail
    assert git(lineage["remote"], "show-ref") == refs_before
    assert remote_main(lineage) == main_sha
    assert "candidate_sha" not in asdict(report.recovery)
    report_path, source_path = tmp_path / "report.json", tmp_path / "source.json"
    report_path.write_text(json.dumps(asdict(report)), encoding="utf-8")
    source = brain.capture_publication(report_path, source_path, lineage["run_id"],
                                      lineage["decision_sha"], repo=lineage["repo"])
    obs = source["observations"][0]
    assert obs["publication_attempt"]["cause"] == report.cause
    assert obs["publication_attempt"]["sampled_main_sha"] == main_sha
    assert obs["publication_attempt"]["reviewed_sha"] == lineage["candidate_sha"]
    event = brain.parse_event_id(brain.publication_event(source_path, 100, 1, 200, brain.digest(source)))
    assert event.family == brain.CONFLICT
    assert "detail" not in json.dumps(source)


def test_recovery_rename_outside_task_scope_fails_closed(tmp_path):
    lineage = make_lineage(tmp_path)
    main_sha = advance_divergent_main(lineage, rename=True)
    with pytest.raises(PublicationError) as raised:
        publish(lineage)
    assert raised.value.report.outcome == "RECOVERY_BLOCKED"
    assert raised.value.report.cause == "RECOVERY_SCOPE_ESCAPE"
    assert raised.value.report.recovery is None
    assert remote_main(lineage) == main_sha


@pytest.mark.parametrize("bases,code", [("", 1), ("a" * 40 + "\n" + "b" * 40, 0)])
def test_missing_or_ambiguous_merge_base_is_typed_and_cannot_mutate_main(tmp_path, monkeypatch, bases, code):
    lineage = make_lineage(tmp_path)
    main_sha = advance_divergent_main(lineage)
    real_git = publication_module._git
    def uncertain_git(repo, *args, **kwargs):
        if args[:2] == ("merge-base", "--all"):
            return code, bases, ""
        assert args[0] != "push"
        return real_git(repo, *args, **kwargs)
    monkeypatch.setattr(publication_module, "_git", uncertain_git)
    with pytest.raises(PublicationError) as raised:
        publish(lineage)
    assert raised.value.report.cause == "AMBIGUOUS_ANCESTRY"
    assert raised.value.report.outcome == "RECOVERY_BLOCKED"
    assert remote_main(lineage) == main_sha


def test_later_publisher_cannot_strand_an_observed_pending_exact_pass(tmp_path, monkeypatch):
    earlier = make_lineage(tmp_path)
    later = add_sibling_pass(earlier)
    refs_before = git(earlier["remote"], "show-ref")
    real_git = publication_module._git
    commands = []
    def recorded_git(repo, *args, **kwargs):
        commands.append(args)
        return real_git(repo, *args, **kwargs)
    monkeypatch.setattr(publication_module, "_git", recorded_git)
    # The earlier PASS workflow has not attempted publication. A later canonical
    # Publisher continuation observes that authority and holds its own mutation.
    with pytest.raises(PublicationError) as raised:
        publish(later)
    report = raised.value.report
    assert report.outcome == "RECOVERY_BLOCKED"
    assert report.cause == "COMPETING_REVIEWED_SOURCE"
    assert asdict(report.blocker) == dict(source_run=earlier["run_id"],
        decision_sha=earlier["decision_sha"], reviewed_sha=earlier["candidate_sha"])
    assert not any(command[0] == "push" for command in commands)
    assert remote_main(earlier) == earlier["base_sha"]
    assert git(earlier["remote"], "show-ref") == refs_before
    # No semantic ordering or winner is inferred from two competing PASS sources.
    with pytest.raises(PublicationError) as reverse:
        publish(earlier)
    assert reverse.value.report.cause == "COMPETING_REVIEWED_SOURCE"
    assert remote_main(earlier) == earlier["base_sha"]


def test_main_movement_during_lineage_validation_is_rechecked_before_push(tmp_path, monkeypatch):
    lineage = make_lineage(tmp_path)
    later = add_sibling_pass(lineage)
    real_load = publication_module._load_success_lineage
    def moving_lineage(*args, **kwargs):
        result = real_load(*args, **kwargs)
        git(lineage["remote"], "update-ref", "refs/heads/main",
            later["candidate_sha"], lineage["base_sha"])
        return result
    monkeypatch.setattr(publication_module, "_load_success_lineage", moving_lineage)
    with pytest.raises(PublicationError) as raised:
        publish(lineage)
    assert raised.value.report.cause == "CONCURRENT_MAIN_MOVEMENT"
    assert raised.value.report.prior_main_sha == lineage["base_sha"]
    assert remote_main(lineage) == later["candidate_sha"]


@pytest.mark.parametrize("namespace", ["review", "artifacts", "review-decision"])
def test_moved_canonical_review_binding_fails_before_mutation(tmp_path, monkeypatch, namespace):
    lineage = make_lineage(tmp_path)
    ref = f"refs/heads/aios/{namespace}/{lineage['run_id']}"
    real_load = publication_module._load_success_lineage
    def moving_lineage(*args, **kwargs):
        result = real_load(*args, **kwargs)
        git(lineage["remote"], "update-ref", ref, lineage["base_sha"])
        return result
    monkeypatch.setattr(publication_module, "_load_success_lineage", moving_lineage)
    with pytest.raises(PublicationError) as raised:
        publish(lineage)
    assert raised.value.report.cause == "STALE_REVIEW_BINDING"
    assert remote_main(lineage) == lineage["base_sha"]


def test_new_decision_during_pending_snapshot_fails_closed(tmp_path, monkeypatch):
    lineage = make_lineage(tmp_path)
    real_git = publication_module._git
    snapshots = 0
    def changing_decisions(repo, *args, **kwargs):
        nonlocal snapshots
        if args == ("ls-remote", "--refs", "origin", "refs/heads/aios/review-decision/*"):
            snapshots += 1
            if snapshots == 2:
                git(lineage["remote"], "update-ref", "refs/heads/aios/review-decision/RUN-063-002",
                    lineage["decision_sha"])
        assert args[0] != "push"
        return real_git(repo, *args, **kwargs)
    monkeypatch.setattr(publication_module, "_git", changing_decisions)
    with pytest.raises(PublicationError) as raised:
        publish(lineage)
    assert raised.value.report.cause == "DECISION_SET_CHANGED"
    assert remote_main(lineage) == lineage["base_sha"]


def test_stale_control_report_preserves_exact_source_and_sampled_main(tmp_path):
    lineage = make_lineage(tmp_path)
    with pytest.raises(PublicationError) as raised:
        publish_review_decision(lineage["repo"], run_id=lineage["run_id"],
            decision_sha=lineage["decision_sha"], control_sha=lineage["candidate_sha"])
    assert raised.value.report.cause == "STALE_CONTROL_BINDING"
    assert raised.value.report.reviewed_sha == lineage["candidate_sha"]
    assert raised.value.report.prior_main_sha == lineage["base_sha"]
    assert remote_main(lineage) == lineage["base_sha"]


def test_review_321_003_never_authorizes_reset_or_a_different_sha(tmp_path, monkeypatch):
    reviewed = "1278440199805edfea4979e65a75fff38ad1171b"
    main = "3944d717af68a8ca97048a4e7472c8c46a5dadc4"
    decision, artifact, base, tree = "a" * 40, "b" * 40, "c" * 40, "d" * 40
    refs = {"refs/heads/main": main, "refs/heads/aios/review/RUN-321-003": reviewed,
            "refs/heads/aios/artifacts/RUN-321-003": artifact,
            "refs/heads/aios/review-decision/RUN-321-003": decision,
            publication_module.PUBLICATION_RESERVATION_REF: None}
    review = f"review_id: REVIEW-321-003\nreviewed_sha: {reviewed}\nverdict: PASS\n"
    source_path = "src/aios_renew/brain_context.py"
    original_blob, reviewed_blob, planning_blob = "e" * 40, "f" * 40, "0" * 40
    trees = {
        base: {source_path: original_blob},
        reviewed: {source_path: reviewed_blob},
        main: {source_path: original_blob, "planning.txt": planning_blob},
        tree: {source_path: reviewed_blob, "planning.txt": planning_blob},
    }
    commands = []
    def bounded_git(repo, *args, **kwargs):
        commands.append(args)
        if args[0] == "ls-remote":
            sha = refs[args[-1]]
            return 0, "" if sha is None else sha + "\t" + args[-1], ""
        if args[0] == "fetch":
            return 0, "", ""
        if args == ("show", f"{artifact}:{publication_module.PUBLICATION_RECOVERY_PATH}"):
            assert kwargs["allow_fail"] is True
            return 128, "", "historical artifacts have no recovery sidecar"
        if args[:3] == ("ls-tree", "-r", "-z"):
            assert len(args) == 4
            return 0, "".join(f"100644 blob {blob}\t{path}\0"
                               for path, blob in sorted(trees[args[3]].items())), ""
        if args[0] == "cat-file":
            return 0, "tree" if args[-1] == tree else "commit", ""
        if args[:2] == ("merge-base", "--is-ancestor"):
            return 1, "", ""
        if args[:2] == ("merge-base", "--all"):
            return 0, base, ""
        if args[0] == "merge-tree":
            return 0, tree, ""
        if args[0] == "diff":
            return 0, "src/aios_renew/brain_context.py\0", ""
        pytest.fail(f"historical publication attempted an unauthorized operation: {args[0]}")
    monkeypatch.setattr(publication_module, "_git", bounded_git)
    def exact_lineage(repo, **kwargs):
        assert kwargs["run_id"] == "RUN-321-003" and kwargs["decision_sha"] == decision
        assert kwargs["publication_main_sha"] == main
        assert "REVIEW-321-003" in review and reviewed in review
        return reviewed, None, ("src/aios_renew/brain_context.py",)
    monkeypatch.setattr(publication_module, "_load_success_lineage", exact_lineage)
    with pytest.raises(PublicationError) as raised:
        publish_review_decision(tmp_path, run_id="RUN-321-003", decision_sha=decision, control_sha=main)
    report = raised.value.report
    assert report.reviewed_sha == reviewed and report.prior_main_sha == main
    assert report.outcome == "RECOVERY_REVIEW_REQUIRED" and report.recovery.publication_eligible is False
    assert report.recovery.merge_base_sha == base and report.recovery.tree_sha == tree
    assert refs["refs/heads/main"] == main
    assert [command for command in commands if command[0] == "show"] == [
        ("show", f"{artifact}:{publication_module.PUBLICATION_RECOVERY_PATH}")]
    assert {command[3] for command in commands if command[:3] == ("ls-tree", "-r", "-z")} == set(trees)
    assert not any(command[0] in {"push", "reset", "merge", "rebase", "cherry-pick", "commit-tree"}
                   for command in commands)


def make_predecessor_lineage(
    root: Path,
    *,
    pred_run_id: str = "RUN-063-001",
    predecessor_override: dict[str, object] | None = None,
    sibling_findings: bool = False,
    remediation_scope: tuple[str, ...] = ("product.txt",),
    repaired_predecessor: bool = False,
) -> dict[str, object]:
    if repaired_predecessor:
        repaired = make_repair_lineage(root, predecessor_kind="PRIMARY")
        repo, remote = repaired["repo"], repaired["remote"]
        base_sha = repaired["candidate_sha"]
        pred_run_id = repaired["run_id"]
        # Replace the fixture's PASS decision with the canonical finding that
        # authorizes this next REMEDIATION of the successful REPAIR candidate.
        git(repo, "reset", "--hard", "--quiet", base_sha)
    else:
        repo, remote, base_sha = materialize_publication_baseline(root)

    state = root / "state"
    state.mkdir(exist_ok=True)

    pred_run = {
        "run_id": pred_run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    pred_run_path = state / "pred-run.json"
    pred_result_path = state / "pred-result.json"
    pred_run_path.write_text(json.dumps(pred_run), encoding="utf-8")
    pred_result_path.write_text(
        json.dumps(result_payload(pred_run_id, base_sha, remediation=False)),
        encoding="utf-8",
    )
    if not repaired_predecessor:
        transport_post_pass(
            repo,
            run_id=pred_run_id,
            head_sha=base_sha,
            run_path=pred_run_path,
            result_path=pred_result_path,
        )

    review_dir = repo / ".ai" / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    if sibling_findings:
        pred_review_source = f"""review_id: REVIEW-063-001
reviewed_sha: {base_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
  AC2: FAIL
findings:
  - id: R1
    basis: AC1
    action: CODE_FIX
    location: product.txt
    issue: Primary candidate needs narrow correction.
    expected: Commit the corrected product.
  - id: R2
    basis: AC2
    action: CODE_FIX
    location: product.txt
    issue: Sibling finding on candidate.
    expected: Sibling correction.
"""
    else:
        pred_review_source = f"""review_id: REVIEW-063-001
reviewed_sha: {base_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: R1
    basis: AC1
    action: CODE_FIX
    location: product.txt
    issue: Primary candidate needs narrow correction.
    expected: Commit the corrected product.
"""
    (review_dir / "REVIEW-063-001.yaml").write_text(pred_review_source, encoding="utf-8")
    remediation_dir = repo / ".ai" / "remediations"
    remediation_dir.mkdir(parents=True, exist_ok=True)
    (remediation_dir / "R1.yaml").write_text(
        f"""finding_id: R1
action: CODE_FIX
reviewed_sha: {base_sha}
modification_scope: [product.txt]
affected_verification: [git diff --check]
constraints: []
""",
        encoding="utf-8",
    )
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", "predecessor review and remediation")
    git(repo, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/remediation/{pred_run_id}-R1")
    if repaired_predecessor:
        git(
            repo, "push", "--quiet", "--force", "origin",
            f"HEAD:refs/heads/aios/review-decision/{pred_run_id}",
        )

    git(repo, "reset", "--hard", "--quiet", base_sha)

    (repo / "product.txt").write_text("remediated product\n", encoding="utf-8")
    git(repo, "add", "product.txt")
    git(repo, "commit", "--quiet", "-m", "remediation candidate")
    candidate_sha = git(repo, "rev-parse", "HEAD")

    rem_run_id = "RUN-063-005" if repaired_predecessor else "RUN-063-002"
    rem_operational_run = {
        "run_id": rem_run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    if callable(predecessor_override):
        predecessor_record = predecessor_override(pred_run_id, base_sha)
    elif predecessor_override is not None:
        predecessor_record = predecessor_override
    else:
        predecessor_record = {
            "source_run_id": pred_run_id,
            "review_id": "REVIEW-063-001",
            "finding_id": "R1",
            "reviewed_sha": base_sha,
        }
    rem_run_payload = {
        "kind": "REMEDIATION",
        "predecessor": predecessor_record,
        "execution": {
            "review_id": "REVIEW-063-001",
            "finding": {
                "id": "R1",
                "basis": "AC1",
                "action": "CODE_FIX",
                "location": "product.txt",
                "issue": "Primary candidate needs narrow correction.",
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
            "run": rem_operational_run,
            "original_constraints": [],
        },
    }
    rem_run_path = state / "rem-run.json"
    rem_result_path = state / "rem-result.json"
    rem_run_path.write_text(json.dumps(rem_run_payload), encoding="utf-8")
    rem_result_path.write_text(
        json.dumps(result_payload(rem_run_id, candidate_sha, remediation=True)),
        encoding="utf-8",
    )
    transport_post_pass(
        repo,
        run_id=rem_run_id,
        head_sha=candidate_sha,
        run_path=rem_run_path,
        result_path=rem_result_path,
    )

    review_dir = repo / ".ai" / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "REVIEW-063-002.yaml").write_text(
        f"""review_id: REVIEW-063-002
reviewed_sha: {candidate_sha}
mode: DELTA
verdict: PASS
prior_finding_id: R1
acceptance:
  AC1: PASS
findings: []
""",
        encoding="utf-8",
    )
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", "remediation review decision")
    decision_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"HEAD:refs/heads/aios/review-decision/{rem_run_id}",
    )

    return {
        "repo": repo,
        "remote": remote,
        "run_id": rem_run_id,
        "pred_run_id": pred_run_id,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "decision_sha": decision_sha,
    }


def _repair_source_validation_inputs(lineage: dict[str, object]) -> dict:
    from aios_renew.artifacts import ResultPackage, validate_evidence, validate_result
    from aios_renew.review import parse_review
    from aios_renew.task import parse_task

    repo = lineage["remote"]
    source_id = lineage["pred_run_id"]
    artifacts_ref = f"refs/heads/aios/artifacts/{source_id}"
    run_data = json.loads(git(repo, "show", f"{artifacts_ref}:.ai/transport/run.json"))
    package = json.loads(git(repo, "show", f"{artifacts_ref}:.ai/transport/result.json"))
    # Read bytes without stripping the transport's trailing whitespace.
    repair = subprocess.run(
        ("git", "-C", str(repo), "show", f"{artifacts_ref}:.ai/transport/repair.json"),
        capture_output=True, check=True,
    ).stdout
    review = parse_review(git(
        repo, "show",
        f"refs/heads/aios/review-decision/{source_id}:.ai/reviews/REVIEW-063-001.yaml",
    ))
    return {
        "task": parse_task(TASK_SOURCE),
        "run_data": run_data,
        "run": publication_module._run_from_data(run_data, "source REPAIR RUN"),
        "package": ResultPackage(
            result=validate_result(package["result"]),
            evidence=tuple(validate_evidence(item) for item in package["evidence"]),
        ),
        "review": review,
        "repair": repair,
    }


@pytest.mark.parametrize("remote", ["origin", "publisher"])
def test_detached_publication_validates_successful_repair_predecessor_and_frontier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote: str,
) -> None:
    import aios_renew.operator as operator_module

    lineage = make_predecessor_lineage(tmp_path, repaired_predecessor=True)
    repo = lineage["repo"]
    if remote != "origin":
        git(repo, "remote", "rename", "origin", remote)
        # An unrelated origin must never replace the Publisher's identity.
        git(repo, "remote", "add", "origin", str(tmp_path / "unavailable.git"))
    git(repo, "checkout", "--quiet", "--detach", lineage["base_sha"])
    assert git(repo, "branch", "--show-current") == ""
    observed = []
    validate_source = operator_module._validated_repair_remediation_source

    def record_source(*args, **kwargs):
        observed.append(kwargs.get("remote"))
        return validate_source(*args, **kwargs)

    monkeypatch.setattr(operator_module, "_validated_repair_remediation_source", record_source)
    report = publish_review_decision(
        repo, run_id=lineage["run_id"], decision_sha=lineage["decision_sha"], remote=remote,
    )

    assert report.outcome == "PUBLISHED"
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert observed == [remote, remote]


def test_detached_repair_source_requires_explicit_remote(tmp_path: Path) -> None:
    from aios_renew.operator import _validated_repair_remediation_source
    from aios_renew.review_transport import ReviewTransportError

    lineage = make_predecessor_lineage(tmp_path, repaired_predecessor=True)
    inputs = _repair_source_validation_inputs(lineage)
    repo = lineage["repo"]
    expected = _validated_repair_remediation_source(repo, **inputs)
    assert expected is not None
    git(repo, "checkout", "--quiet", "--detach", lineage["base_sha"])

    with pytest.raises(ReviewTransportError, match="no configured upstream Git remote"):
        _validated_repair_remediation_source(repo, **inputs)
    assert _validated_repair_remediation_source(repo, remote="origin", **inputs) == expected
    assert remote_main(lineage) != lineage["candidate_sha"]


@pytest.mark.parametrize("remote", ["missing", "unavailable", "empty", ""])
def test_explicit_repair_remote_never_falls_back_to_valid_origin(
    tmp_path: Path, remote: str,
) -> None:
    from aios_renew.operator import _validated_repair_remediation_source

    lineage = make_predecessor_lineage(tmp_path, repaired_predecessor=True)
    inputs = _repair_source_validation_inputs(lineage)
    repo = lineage["repo"]
    git(repo, "remote", "add", "unavailable", str(tmp_path / "unavailable.git"))
    empty_remote = tmp_path / "empty.git"
    git(tmp_path, "init", "--quiet", "--bare", str(empty_remote))
    git(repo, "remote", "add", "empty", str(empty_remote))
    git(repo, "checkout", "--quiet", "--detach", lineage["base_sha"])

    assert _validated_repair_remediation_source(repo, remote="origin", **inputs) is not None
    with pytest.raises(PublicationError):
        _validated_repair_remediation_source(repo, remote=remote, **inputs)
    assert remote_main(lineage) != lineage["candidate_sha"]


def test_predecessor_bearing_remediation_publication_advances_main_ac5(
    tmp_path: Path,
) -> None:
    lineage = make_predecessor_lineage(tmp_path)
    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]


def test_predecessor_mismatched_source_run_fails_closed_ac3(
    tmp_path: Path,
) -> None:
    lineage = make_predecessor_lineage(
        tmp_path,
        predecessor_override=lambda pred_id, base_sha: {
            "source_run_id": "RUN-063-999",
            "review_id": "REVIEW-063-001",
            "finding_id": "R1",
            "reviewed_sha": base_sha,
        },
    )
    with pytest.raises(PublicationError) as exc_info:
        publish(lineage)
    assert exc_info.value.report.outcome == "FAILED"
    assert remote_main(lineage) == lineage["base_sha"]


def test_predecessor_mismatched_review_id_fails_closed_ac3(
    tmp_path: Path,
) -> None:
    lineage = make_predecessor_lineage(
        tmp_path,
        predecessor_override=lambda pred_id, base_sha: {
            "source_run_id": pred_id,
            "review_id": "REVIEW-063-WRONG",
            "finding_id": "R1",
            "reviewed_sha": base_sha,
        },
    )
    with pytest.raises(PublicationError) as exc_info:
        publish(lineage)
    assert exc_info.value.report.outcome == "FAILED"
    assert remote_main(lineage) == lineage["base_sha"]


def test_predecessor_mismatched_finding_id_fails_closed_ac3(
    tmp_path: Path,
) -> None:
    lineage = make_predecessor_lineage(
        tmp_path,
        predecessor_override=lambda pred_id, base_sha: {
            "source_run_id": pred_id,
            "review_id": "REVIEW-063-001",
            "finding_id": "R-WRONG",
            "reviewed_sha": base_sha,
        },
    )
    with pytest.raises(PublicationError) as exc_info:
        publish(lineage)
    assert exc_info.value.report.outcome == "FAILED"
    assert remote_main(lineage) == lineage["base_sha"]


def test_predecessor_mismatched_reviewed_sha_fails_closed_ac3(
    tmp_path: Path,
) -> None:
    lineage = make_predecessor_lineage(
        tmp_path,
        predecessor_override=lambda pred_id, base_sha: {
            "source_run_id": pred_id,
            "review_id": "REVIEW-063-001",
            "finding_id": "R1",
            "reviewed_sha": "0" * 40,
        },
    )
    with pytest.raises(PublicationError) as exc_info:
        publish(lineage)
    assert exc_info.value.report.outcome == "FAILED"
    assert remote_main(lineage) == lineage["base_sha"]


def test_predecessor_multi_finding_review_fails_closed_ac5(
    tmp_path: Path,
) -> None:
    lineage = make_predecessor_lineage(tmp_path, sibling_findings=True)

    with pytest.raises(PublicationError) as exc_info:
        publish(lineage)
    assert exc_info.value.report.outcome == "FAILED"
    assert "outstanding findings in correction frontier: R2" in exc_info.value.report.detail
    assert "R1" not in exc_info.value.report.detail
    assert remote_main(lineage) == lineage["base_sha"]


test_predecessor_multi_finding_review_fails_closed_ac6 = test_predecessor_multi_finding_review_fails_closed_ac5


def test_publication_execution_base_parser_is_exact() -> None:
    parsed = publication_module._parse_remediation_execution_base({
        "run_id": "RUN-101-001", "candidate_sha": "a" * 40,
    })
    assert parsed == publication_module.RemediationExecutionBase(
        "RUN-101-001", "a" * 40
    )
    with pytest.raises(ValueError, match="fields do not match"):
        publication_module._parse_remediation_execution_base({
            "run_id": "RUN-101-001", "candidate_sha": "a" * 40,
            "reviewed_sha": "a" * 40,
        })


def test_publication_integrated_execution_base_parser() -> None:
    valid_integrated = {
        "version": 1,
        "kind": "INTEGRATED",
        "cumulative_tip_run_id": "RUN-101-001",
        "cumulative_tip_candidate_sha": "a" * 40,
        "authorized_main_sha": "b" * 40,
        "integration_candidate_sha": "c" * 40,
        "integration_id": "d" * 64,
    }
    parsed = publication_module._parse_remediation_execution_base(valid_integrated)
    assert parsed.is_integrated is True
    assert parsed.run_id == "RUN-101-001"
    assert parsed.candidate_sha == "c" * 40
    assert parsed.cumulative_tip_run_id == "RUN-101-001"
    assert parsed.cumulative_tip_candidate_sha == "a" * 40
    assert parsed.authorized_main_sha == "b" * 40
    assert parsed.integration_candidate_sha == "c" * 40
    assert parsed.integration_id == "d" * 64

    # Bad version
    with pytest.raises(ValueError, match="version is invalid"):
        publication_module._parse_remediation_execution_base({**valid_integrated, "version": 2})

    # Bad kind
    with pytest.raises(ValueError, match="kind is invalid"):
        publication_module._parse_remediation_execution_base({**valid_integrated, "kind": "OTHER"})

    # Bad SHA
    with pytest.raises(ValueError, match="authorized_main_sha is invalid"):
        publication_module._parse_remediation_execution_base({**valid_integrated, "authorized_main_sha": "invalid"})


def test_publication_predecessor_parser_rejects_alias_and_conflicting_fields() -> None:
    valid_payload = {
        "source_run_id": "RUN-063-001",
        "review_id": "REVIEW-063-001",
        "finding_id": "R1",
        "reviewed_sha": "0" * 40,
    }
    parsed = publication_module._parse_remediation_predecessor(valid_payload)
    assert parsed.source_run_id == "RUN-063-001"
    assert parsed.review_id == "REVIEW-063-001"
    assert parsed.finding_id == "R1"
    assert parsed.reviewed_sha == "0" * 40

    # Non-mapping input fails closed
    with pytest.raises(ValueError, match="REMEDIATION predecessor must be a mapping"):
        publication_module._parse_remediation_predecessor("not-a-mapping")

    # Alternate alias keys fail closed
    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        publication_module._parse_remediation_predecessor({
            "run_id": "RUN-063-001",
            "review_id": "REVIEW-063-001",
            "finding_id": "R1",
            "reviewed_sha": "0" * 40,
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        publication_module._parse_remediation_predecessor({
            "source_run_id": "RUN-063-001",
            "source_review_id": "REVIEW-063-001",
            "finding_id": "R1",
            "reviewed_sha": "0" * 40,
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        publication_module._parse_remediation_predecessor({
            "source_run_id": "RUN-063-001",
            "review_id": "REVIEW-063-001",
            "selected_finding_id": "R1",
            "reviewed_sha": "0" * 40,
        })

    # Conflicting keys fail closed
    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        publication_module._parse_remediation_predecessor({
            **valid_payload,
            "run_id": "RUN-063-999",
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        publication_module._parse_remediation_predecessor({
            **valid_payload,
            "source_review_id": "REVIEW-063-999",
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        publication_module._parse_remediation_predecessor({
            **valid_payload,
            "selected_finding_id": "R2",
        })

    with pytest.raises(
        ValueError, match="REMEDIATION predecessor contains unexpected fields"
    ):
        publication_module._parse_remediation_predecessor({
            **valid_payload,
            "extra_field": "disallowed",
        })


def test_publication_predecessor_canonical_run_identity_consistent_ac3() -> None:
    admissible_run_ids = [
        "RUN-066-1",
        "RUN-066-001",
        "RUN-task.1-001",
        "RUN-101-candidate",
        "RUN-A_B-001",
        "RUN-TASK-999-1",
    ]
    for run_id in admissible_run_ids:
        payload = {
            "source_run_id": run_id,
            "review_id": "REVIEW-066-001",
            "finding_id": "R1",
            "reviewed_sha": "a" * 40,
        }
        parsed = publication_module._parse_remediation_predecessor(payload)
        assert parsed.source_run_id == run_id

    invalid_run_ids = [
        "NOT-A-RUN",
        "RUN-",
        "RUN-/slash",
        "RUN-\\backslash",
        "TASK-101",
        "RUN-@bad",
    ]
    for invalid_id in invalid_run_ids:
        payload = {
            "source_run_id": invalid_id,
            "review_id": "REVIEW-066-001",
            "finding_id": "R1",
            "reviewed_sha": "a" * 40,
        }
        with pytest.raises(
            ValueError,
            match="REMEDIATION predecessor source RUN identity is invalid",
        ):
            publication_module._parse_remediation_predecessor(payload)


def test_predecessor_alias_keys_fail_closed_ac3(
    tmp_path: Path,
) -> None:
    lineage = make_predecessor_lineage(
        tmp_path,
        predecessor_override=lambda pred_id, base_sha: {
            "run_id": pred_id,
            "review_id": "REVIEW-063-001",
            "finding_id": "R1",
            "reviewed_sha": base_sha,
        },
    )
    with pytest.raises(PublicationError) as exc_info:
        publish(lineage)
    assert exc_info.value.report.outcome == "FAILED"
    assert "unexpected fields" in exc_info.value.report.detail
    assert remote_main(lineage) == lineage["base_sha"]


def test_predecessor_conflicting_keys_fail_closed_ac3(
    tmp_path: Path,
) -> None:
    # Conflicting source_run_id and run_id
    lineage = make_predecessor_lineage(
        tmp_path / "case1",
        predecessor_override=lambda pred_id, base_sha: {
            "source_run_id": pred_id,
            "run_id": "RUN-063-999",
            "review_id": "REVIEW-063-001",
            "finding_id": "R1",
            "reviewed_sha": base_sha,
        },
    )
    with pytest.raises(PublicationError) as exc_info:
        publish(lineage)
    assert exc_info.value.report.outcome == "FAILED"
    assert "unexpected fields" in exc_info.value.report.detail
    assert remote_main(lineage) == lineage["base_sha"]

    # Conflicting review_id and source_review_id
    lineage2 = make_predecessor_lineage(
        tmp_path / "case2",
        predecessor_override=lambda pred_id, base_sha: {
            "source_run_id": pred_id,
            "review_id": "REVIEW-063-001",
            "source_review_id": "REVIEW-063-999",
            "finding_id": "R1",
            "reviewed_sha": base_sha,
        },
    )
    with pytest.raises(PublicationError) as exc_info2:
        publish(lineage2)
    assert exc_info2.value.report.outcome == "FAILED"
    assert "unexpected fields" in exc_info2.value.report.detail
    assert remote_main(lineage2) == lineage2["base_sha"]

    # Conflicting finding_id and selected_finding_id
    lineage3 = make_predecessor_lineage(
        tmp_path / "case3",
        predecessor_override=lambda pred_id, base_sha: {
            "source_run_id": pred_id,
            "review_id": "REVIEW-063-001",
            "finding_id": "R1",
            "selected_finding_id": "R2",
            "reviewed_sha": base_sha,
        },
    )
    with pytest.raises(PublicationError) as exc_info3:
        publish(lineage3)
    assert exc_info3.value.report.outcome == "FAILED"
    assert "unexpected fields" in exc_info3.value.report.detail
    assert remote_main(lineage3) == lineage3["base_sha"]


def test_predecessor_canonically_admissible_run_identity_accepted_in_publication(
    tmp_path: Path,
) -> None:
    lineage = make_predecessor_lineage(tmp_path, pred_run_id="RUN-063-1")
    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]


# ===========================================================================
# AC2, AC3, AC4, AC5: Extended REPAIR execution and authorization supersession
# ===========================================================================


def test_run_123_002_equivalent_extended_repair_publication_succeeds_ac2_ac5(
    tmp_path: Path,
) -> None:
    # Exact RUN-123-002 causal topology: a failed RUN has a canonical CODE_FIX authorization SHA,
    # its continuation execution persists repair_authorization_sha, Runtime RESULT/review state is
    # valid, and publication accepts that exact current authorization rather than rejecting the
    # extended field set.
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        include_repair_authorization_sha=True,
    )

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert report.prior_main_sha == lineage["base_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert remote_main(lineage) != lineage["decision_sha"]


def test_extended_repair_with_superseding_authorization_lineage_succeeds_ac2(
    tmp_path: Path,
) -> None:
    # A failed RUN has a canonical REPAIR root authorization. It is superseded by revision 2.
    # The continuation is admitted with revision 2 and persists revision 2's repair_authorization_sha.
    # Publication validates the exact current authorization SHA and advances main.
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        include_repair_authorization_sha=True,
        supersede_authorization=True,
    )

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]


def test_extended_repair_stale_or_mismatched_authorization_sha_fails_closed_ac3_ac5(
    tmp_path: Path,
) -> None:
    # Negative variant 1: Persisted repair_authorization_sha does not match canonical authorization SHA.
    mismatched_sha = "0123456789abcdef0123456789abcdef01234567"
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        repair_authorization_sha=mismatched_sha,
    )

    with pytest.raises(
        PublicationError,
        match="persisted repair_authorization_sha does not match current canonical authorization",
    ):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_extended_repair_superseded_predecessor_selector_fails_closed_ac3_ac5(
    tmp_path: Path,
) -> None:
    # Negative variant 2: Root authorization is revision 1. A superseding authorization revision 2
    # is canonical. The continuation is executed with revision 2 intent but persists revision 1's
    # authorization SHA (superseded predecessor selector).
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        supersede_authorization=True,
        repair_authorization_sha="use_predecessor",
    )

    with pytest.raises(
        PublicationError,
        match="persisted repair_authorization_sha does not match current canonical authorization",
    ):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


@pytest.mark.parametrize(
    "malformed_sha",
    [
        "invalid-sha",
        "1234",
        "0123456789abcdef0123456789abcdef0123456G",
    ],
)
def test_extended_repair_malformed_authorization_sha_fails_closed_ac3(
    tmp_path: Path, malformed_sha: str
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        repair_authorization_sha=malformed_sha,
    )

    with pytest.raises(
        PublicationError, match="REPAIR authorization SHA is invalid"
    ):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_extended_repair_extra_fields_fail_closed_ac3(
    tmp_path: Path,
) -> None:
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        lineage_mutation="extra_field",
        include_repair_authorization_sha=True,
    )

    with pytest.raises(
        PublicationError,
        match="REPAIR execution fields do not match persisted lineage",
    ):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_extended_repair_broken_supersession_chain_fails_closed_ac3(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        include_repair_authorization_sha=True,
    )
    failed_run_id = lineage["failed_run_id"]
    # Push discontinuous supersession revision 3 without revision 2
    subject_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"{subject_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/3",
    )

    with pytest.raises(
        PublicationError, match="canonical REPAIR authorization is invalid"
    ):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_legacy_repair_without_repair_authorization_sha_remains_compatible_ac4(
    tmp_path: Path,
) -> None:
    # Verify legacy canonical REPAIR execution without repair_authorization_sha
    # remains 100% compatible with existing publication behavior.
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        include_repair_authorization_sha=False,
    )
    assert "repair_authorization_sha" not in lineage["repair_lineage"]

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]


def test_multi_generation_repair_publishes_with_exact_revision_3_authorization_sha_ac5(
    tmp_path: Path,
) -> None:
    # In a multi-generation supersession topology (r1 -> r2 -> r3):
    # publication of a valid continuation execution bound to current authorization (r3)
    # succeeds and advances canonical main.
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        include_repair_authorization_sha=True,
        supersede_revision_count=2,
    )

    auth_shas = lineage["authorization_shas_by_revision"]
    assert len(auth_shas) == 3
    assert lineage["repair_lineage"]["repair_authorization_sha"] == auth_shas[3]

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert report.prior_main_sha == lineage["base_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert remote_main(lineage) != lineage["decision_sha"]


@pytest.mark.parametrize("stale_revision", [1, 2])
def test_multi_generation_repair_superseded_revision_selector_fails_closed_ac5(
    tmp_path: Path,
    stale_revision: int,
) -> None:
    # In a 3-generation supersession topology (r1 -> r2 -> r3), a continuation candidate
    # persisting a superseded predecessor authorization SHA (either r1 or r2) must fail closed
    # before remote main mutation.
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        include_repair_authorization_sha=True,
        supersede_revision_count=2,
        repair_authorization_sha=stale_revision,
    )

    with pytest.raises(
        PublicationError,
        match="persisted repair_authorization_sha does not match current canonical authorization",
    ):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


@pytest.mark.parametrize("stale_revision", [1, 2])
def test_multi_generation_repair_superseded_payload_fails_closed_ac5(
    tmp_path: Path,
    stale_revision: int,
) -> None:
    # In a 3-generation topology, if continuation lineage carries superseded authorization content,
    # publication fails closed before main mutation.
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        include_repair_authorization_sha=True,
        supersede_revision_count=2,
        use_superseded_authorization=stale_revision,
    )

    with pytest.raises(
        PublicationError,
        match="persisted REPAIR authorization is not canonical",
    ):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def test_multi_generation_repair_broken_predecessor_chain_fails_closed_ac5(
    tmp_path: Path,
) -> None:
    # In a multi-generation topology, if a successor's predecessor link does not match
    # the preceding generation's commit SHA (e.g. r3 claims r1 instead of r2), publication
    # fails closed before remote main mutation.
    repo = tmp_path / "repo"
    remote = tmp_path / "upstream.git"
    lineage = make_repair_lineage(
        tmp_path,
        predecessor_kind="PRIMARY",
        action="CODE_FIX",
        include_repair_authorization_sha=True,
        supersede_revision_count=1,
    )
    failed_run_id = lineage["failed_run_id"]
    auth_shas = lineage["authorization_shas_by_revision"]
    failure_artifacts_sha = git(
        remote, "rev-parse", f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    )

    # Push r3 with predecessor pointing to r1 instead of r2
    _push_repair_supersession(
        repo,
        failed_run_id=failed_run_id,
        revision=3,
        predecessor_repair_sha=auth_shas[1],
        failure_artifacts_sha=failure_artifacts_sha,
        authorization=lineage["authorization"],
    )

    with pytest.raises(
        PublicationError, match="canonical REPAIR authorization is invalid"
    ):
        publish(lineage)

    assert remote_main(lineage) == lineage["base_sha"]


def make_integrated_predecessor_lineage(root: Path) -> dict[str, object]:
    from aios_renew.correction_integration import integrate_correction

    pred_run_id = "RUN-063-001"
    rem_run_id = "RUN-063-002"

    repo, remote, base_sha = materialize_publication_baseline(root)

    state = root / "state"
    state.mkdir(exist_ok=True)

    pred_run = {
        "run_id": pred_run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    pred_run_path = state / "pred-run.json"
    pred_result_path = state / "pred-result.json"
    pred_run_path.write_text(json.dumps(pred_run), encoding="utf-8")
    pred_result_path.write_text(
        json.dumps(result_payload(pred_run_id, base_sha, remediation=False)),
        encoding="utf-8",
    )
    transport_post_pass(
        repo,
        run_id=pred_run_id,
        head_sha=base_sha,
        run_path=pred_run_path,
        result_path=pred_result_path,
    )

    review_dir = repo / ".ai" / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    pred_review_source = f"""review_id: REVIEW-063-001
reviewed_sha: {base_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: R1
    basis: AC1
    action: CODE_FIX
    location: product.txt
    issue: Primary candidate needs narrow correction.
    expected: Commit the corrected product.
"""
    (review_dir / "REVIEW-063-001.yaml").write_text(pred_review_source, encoding="utf-8")
    remediation_dir = repo / ".ai" / "remediations"
    remediation_dir.mkdir(parents=True, exist_ok=True)
    (remediation_dir / "R1.yaml").write_text(
        f"""finding_id: R1
action: CODE_FIX
reviewed_sha: {base_sha}
modification_scope: [product.txt]
affected_verification: [git diff --check]
constraints: []
""",
        encoding="utf-8",
    )
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", "predecessor review and remediation")
    git(repo, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/remediation/{pred_run_id}-R1")

    # Advance main independently
    git(repo, "reset", "--hard", "--quiet", base_sha)
    (repo / "UNRELATED.txt").write_text("unrelated main change\n", encoding="utf-8")
    git(repo, "add", "UNRELATED.txt")
    git(repo, "commit", "--quiet", "-m", "advance main independently")
    new_main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    # Integrate correction explicitly
    int_result = integrate_correction(
        "TASK-063",
        task_revision=2,
        cumulative_tip_run_id=pred_run_id,
        cumulative_tip_candidate_sha=base_sha,
        authorized_main_sha=new_main_sha,
        repo=repo,
    )
    git(repo, "push", "--quiet", "origin", f"{int_result.integration_candidate_sha}:{int_result.integration_ref}")

    # Author remediation candidate on top of integrated candidate
    git(repo, "reset", "--hard", "--quiet", int_result.integration_candidate_sha)
    (repo / "product.txt").write_text("remediated product on integrated base\n", encoding="utf-8")
    git(repo, "add", "product.txt")
    git(repo, "commit", "--quiet", "-m", "remediation candidate on integrated base")
    candidate_sha = git(repo, "rev-parse", "HEAD")

    rem_operational_run = {
        "run_id": rem_run_id,
        "task": {"id": "TASK-063", "revision": 2},
        "executor": "codex",
        "base_sha": int_result.integration_candidate_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    predecessor_record = {
        "source_run_id": pred_run_id,
        "review_id": "REVIEW-063-001",
        "finding_id": "R1",
        "reviewed_sha": base_sha,
    }
    execution_base_record = {
        "version": 1,
        "kind": "INTEGRATED",
        "cumulative_tip_run_id": pred_run_id,
        "cumulative_tip_candidate_sha": base_sha,
        "authorized_main_sha": new_main_sha,
        "integration_candidate_sha": int_result.integration_candidate_sha,
        "integration_id": int_result.integration_id,
    }
    rem_run_payload = {
        "kind": "REMEDIATION",
        "predecessor": predecessor_record,
        "execution_base": execution_base_record,
        "execution": {
            "review_id": "REVIEW-063-001",
            "finding": {
                "id": "R1",
                "basis": "AC1",
                "action": "CODE_FIX",
                "location": "product.txt",
                "issue": "Primary candidate needs narrow correction.",
                "expected": "Commit the corrected product.",
            },
            "remediation": {
                "finding_id": "R1",
                "action": "CODE_FIX",
                "reviewed_sha": base_sha,
                "modification_scope": ["product.txt"],
                "affected_verification": ["git diff --check"],
                "constraints": [],
            },
            "run": rem_operational_run,
            "original_constraints": [],
        },
    }
    rem_run_path = state / "rem-run.json"
    rem_result_path = state / "rem-result.json"
    rem_run_path.write_text(json.dumps(rem_run_payload), encoding="utf-8")
    rem_result_path.write_text(
        json.dumps(result_payload(rem_run_id, candidate_sha, remediation=True)),
        encoding="utf-8",
    )
    transport_post_pass(
        repo,
        run_id=rem_run_id,
        head_sha=candidate_sha,
        run_path=rem_run_path,
        result_path=rem_result_path,
    )

    review_dir = repo / ".ai" / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "REVIEW-063-002.yaml").write_text(
        f"""review_id: REVIEW-063-002
reviewed_sha: {candidate_sha}
mode: DELTA
verdict: PASS
prior_finding_id: R1
acceptance:
  AC1: PASS
findings: []
""",
        encoding="utf-8",
    )
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", "remediation review decision")
    decision_sha = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"HEAD:refs/heads/aios/review-decision/{rem_run_id}",
    )

    return {
        "repo": repo,
        "remote": remote,
        "run_id": rem_run_id,
        "pred_run_id": pred_run_id,
        "base_sha": base_sha,
        "new_main_sha": new_main_sha,
        "integration_candidate_sha": int_result.integration_candidate_sha,
        "integration_ref": int_result.integration_ref,
        "candidate_sha": candidate_sha,
        "decision_sha": decision_sha,
        "remediation_run": rem_run_payload,
    }


def test_integrated_remediation_publication_advances_main_ac7(
    tmp_path: Path,
) -> None:
    lineage = make_integrated_predecessor_lineage(tmp_path)
    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]


def test_integrated_remediation_repair_publication_validates_result_base_package_ac6(
    tmp_path: Path,
) -> None:
    lineage = make_integrated_remediation_repair_lineage(tmp_path)
    assert lineage["integration_candidate_sha"] != lineage["base_sha"]
    assert lineage["failed_head_sha"] != lineage["base_sha"]

    report = publish(lineage)

    assert report.outcome == "PUBLISHED"
    assert report.source_run == "RUN-063-003"
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert remote_main(lineage) == lineage["candidate_sha"]


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_finding", "prior finding does not match repaired REMEDIATION"),
        ("wrong_package", "REPAIR RESULT.changed_files mismatch"),
        ("missing_ref", "canonical ref .* is missing or ambiguous"),
        ("moved_ref", "remote integration ref does not match"),
        ("authorized_main", "integration_id does not match"),
        ("integration_candidate", "candidate SHA does not match RUN base_sha"),
    ],
)
def test_integrated_remediation_repair_publication_fails_closed_on_forged_lineage(
    tmp_path: Path, case: str, message: str
) -> None:
    lineage = make_integrated_remediation_repair_lineage(
        tmp_path,
        execution_base_mutation=(
            case if case in {"authorized_main", "integration_candidate"} else None
        ),
        result_changed_files=() if case == "wrong_package" else ("product.txt",),
        prior_finding_id="R2" if case == "wrong_finding" else "R1",
    )
    if case == "missing_ref":
        git(lineage["remote"], "update-ref", "-d", lineage["integration_ref"])
    elif case == "moved_ref":
        git(
            lineage["remote"],
            "update-ref",
            lineage["integration_ref"],
            lineage["failed_head_sha"],
        )

    with pytest.raises(PublicationError, match=message):
        publish(lineage)

    assert remote_main(lineage) == lineage["new_main_sha"]


def test_integrated_remediation_publication_rejects_missing_ref_and_stale_main_ac7(
    tmp_path: Path,
) -> None:
    lineage = make_integrated_predecessor_lineage(tmp_path)
    remote = lineage["remote"]

    # Delete integration ref on remote -> fails closed
    git(remote, "update-ref", "-d", lineage["integration_ref"])
    with pytest.raises(PublicationError, match="canonical ref .* is missing or ambiguous"):
        publish(lineage)

    # Re-create ref
    git(remote, "update-ref", lineage["integration_ref"], lineage["integration_candidate_sha"])

    # Now advance main on remote ahead of authorized_main_sha -> fails closed
    git(lineage["repo"], "checkout", "--quiet", "--detach", lineage["new_main_sha"])
    (lineage["repo"] / "LATER.txt").write_text("later advance\n", encoding="utf-8")
    git(lineage["repo"], "add", "LATER.txt")
    git(lineage["repo"], "commit", "-m", "advance main after integration")
    later_main = git(lineage["repo"], "rev-parse", "HEAD")
    git(lineage["repo"], "push", "origin", f"{later_main}:refs/heads/main")

    with pytest.raises(PublicationError, match="stale"):
        publish(lineage)


def _integrated_publication_lineage(root: Path, operation: str) -> dict[str, object]:
    if operation == "REPAIR":
        return make_integrated_remediation_repair_lineage(root)
    return make_integrated_predecessor_lineage(root)


def _set_integrated_replay_main(lineage: dict[str, object], *, later: bool) -> str:
    repo = lineage["repo"]
    git(repo, "checkout", "--quiet", "--detach", lineage["candidate_sha"])
    if later:
        (repo / "LATER.txt").write_text("later canonical commit\n", encoding="utf-8")
        git(repo, "add", "LATER.txt")
        git(repo, "commit", "--quiet", "-m", "later canonical commit")
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", f"{main_sha}:refs/heads/main")
    return main_sha


def _record_publication_pushes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    real_git = publication_module._git
    pushes = []

    def recording_git(repo_path, *args, **kwargs):
        if args and args[0] == "push":
            pushes.append(args)
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_git", recording_git)
    return pushes


def _rewrite_publication_fixture_document(
    lineage: dict[str, object], ref: str, path: str, mutate
) -> str:
    repo = lineage["repo"]
    ref_sha = git(lineage["remote"], "rev-parse", ref)
    git(repo, "checkout", "--quiet", "--detach", ref_sha)
    document_path = repo / path
    document = yaml.safe_load(document_path.read_text(encoding="utf-8"))
    mutate(document)
    document_path.write_text(
        json.dumps(document) if path.endswith(".json") else yaml.safe_dump(document),
        encoding="utf-8",
    )
    git(repo, "add", path)
    git(repo, "commit", "--quiet", "-m", "alter publication fixture lineage")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "--force", "origin", f"{sha}:{ref}")
    return sha


@pytest.mark.parametrize("operation", ["REMEDIATION", "REPAIR"])
@pytest.mark.parametrize("later", [False, True], ids=["equal", "strict-ancestor"])
def test_integrated_correction_replay_tolerates_historical_main_only_for_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, later: bool
) -> None:
    lineage = _integrated_publication_lineage(tmp_path, operation)
    main_sha = _set_integrated_replay_main(lineage, later=later)
    assert main_sha != lineage["new_main_sha"]
    pushes = _record_publication_pushes(monkeypatch)

    report = publish(lineage)

    assert report.outcome == ("ALREADY_INCLUDED" if later else "ALREADY_PUBLISHED")
    assert report.source_run == lineage["run_id"]
    assert report.reviewed_sha == lineage["candidate_sha"]
    assert report.prior_main_sha == main_sha
    assert pushes == []
    assert remote_main(lineage) == main_sha


@pytest.mark.parametrize("operation", ["REMEDIATION", "REPAIR"])
@pytest.mark.parametrize("main_kind", ["divergent", "integration-base"])
def test_integrated_correction_stale_main_without_reviewed_candidate_rejects_before_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, main_kind: str
) -> None:
    lineage = _integrated_publication_lineage(tmp_path, operation)
    repo = lineage["repo"]
    if main_kind == "integration-base":
        main_sha = lineage["integration_candidate_sha"]
    else:
        git(repo, "checkout", "--quiet", "--detach", lineage["new_main_sha"])
        (repo / "LATER.txt").write_text("independent main advance\n", encoding="utf-8")
        git(repo, "add", "LATER.txt")
        git(repo, "commit", "--quiet", "-m", "independent main advance")
        main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", f"{main_sha}:refs/heads/main")
    pushes = _record_publication_pushes(monkeypatch)

    with pytest.raises(PublicationError, match="authorized main SHA .* is stale") as raised:
        publish(lineage)

    assert raised.value.report.outcome == "FAILED"
    assert pushes == []
    assert remote_main(lineage) == main_sha


@pytest.mark.parametrize("operation", ["REMEDIATION", "REPAIR"])
@pytest.mark.parametrize("later", [False, True], ids=["equal", "strict-ancestor"])
@pytest.mark.parametrize(
    "case",
    [
        "missing_integration_ref", "moved_integration_ref", "integration_id",
        "integration_candidate", "missing_predecessor", "missing_predecessor_field",
        "missing_execution_base", "predecessor_identity", "predecessor_review",
        "moved_decision", "missing_review", "source_candidate", "review",
        "result", "outstanding_frontier",
    ],
)
def test_integrated_correction_inclusion_cannot_bypass_invalid_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    operation: str, later: bool, case: str,
) -> None:
    lineage = _integrated_publication_lineage(tmp_path, operation)
    main_sha = _set_integrated_replay_main(lineage, later=later)
    remote = lineage["remote"]
    origin_run_id = lineage["failed_run_id"] if operation == "REPAIR" else lineage["run_id"]
    origin_ref = (
        f"refs/heads/aios/failure-artifacts/{origin_run_id}"
        if operation == "REPAIR" else f"refs/heads/aios/artifacts/{origin_run_id}"
    )
    predecessor_review_ref = f"refs/heads/aios/remediation/{lineage['pred_run_id']}-R1"
    if case == "missing_integration_ref":
        git(remote, "update-ref", "-d", lineage["integration_ref"])
    elif case == "moved_integration_ref":
        git(remote, "update-ref", lineage["integration_ref"], lineage["candidate_sha"])
    elif case in {
        "integration_id", "integration_candidate", "predecessor_identity",
        "missing_predecessor_field", "missing_execution_base",
    }:
        def mutate_origin(document):
            if case == "missing_predecessor_field":
                del document["predecessor"]
            elif case == "missing_execution_base":
                del document["execution_base"]
            elif case == "predecessor_identity":
                document["predecessor"]["finding_id"] = "R2"
            elif case == "integration_id":
                document["execution_base"]["integration_id"] = "forged-integration"
            else:
                document["execution_base"]["integration_candidate_sha"] = lineage["candidate_sha"]
        _rewrite_publication_fixture_document(
            lineage, origin_ref, ".ai/transport/run.json", mutate_origin,
        )
    elif case == "missing_predecessor":
        git(remote, "update-ref", "-d", f"refs/heads/aios/artifacts/{lineage['pred_run_id']}")
    elif case in {"predecessor_review", "outstanding_frontier"}:
        def mutate_predecessor(document):
            if case == "predecessor_review":
                document["reviewed_sha"] = lineage["new_main_sha"]
            else:
                document["findings"].append({
                    **document["findings"][0], "id": "R2",
                    "issue": "Independent outstanding sibling finding.",
                })
        _rewrite_publication_fixture_document(
            lineage, predecessor_review_ref, ".ai/reviews/REVIEW-063-001.yaml",
            mutate_predecessor,
        )
    elif case == "review":
        lineage["decision_sha"] = _rewrite_publication_fixture_document(
            lineage, f"refs/heads/aios/review-decision/{lineage['run_id']}",
            f".ai/reviews/REVIEW-{lineage['run_id'][4:]}.yaml",
            lambda document: document.update(reviewed_sha=lineage["new_main_sha"]),
        )
    elif case in {"moved_decision", "missing_review"}:
        git(remote, "update-ref", f"refs/heads/aios/review-decision/{lineage['run_id']}",
            lineage["candidate_sha"])
        if case == "missing_review":
            lineage["decision_sha"] = lineage["candidate_sha"]
    elif case == "source_candidate":
        git(remote, "update-ref", f"refs/heads/aios/review/{lineage['run_id']}",
            lineage["integration_candidate_sha"])
    else:
        _rewrite_publication_fixture_document(
            lineage, f"refs/heads/aios/artifacts/{lineage['run_id']}",
            ".ai/transport/result.json",
            lambda document: document["result"].update(head_sha=lineage["new_main_sha"]),
        )
    pushes = _record_publication_pushes(monkeypatch)

    with pytest.raises(PublicationError) as raised:
        publish(lineage)

    assert raised.value.report.outcome == "FAILED"
    if case == "outstanding_frontier":
        assert "outstanding findings in correction frontier: R2" in str(raised.value)
    assert pushes == []
    assert remote_main(lineage) == main_sha


@pytest.mark.parametrize("later", [False, True], ids=["equal", "strict-ancestor"])
def test_integrated_repair_replay_requires_canonical_repair_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, later: bool
) -> None:
    lineage = make_integrated_remediation_repair_lineage(tmp_path)
    main_sha = _set_integrated_replay_main(lineage, later=later)
    git(lineage["remote"], "update-ref", "-d", f"refs/heads/aios/repair/{lineage['failed_run_id']}")
    pushes = _record_publication_pushes(monkeypatch)

    with pytest.raises(PublicationError):
        publish(lineage)

    assert pushes == []
    assert remote_main(lineage) == main_sha


@pytest.mark.parametrize("operation", ["REMEDIATION", "REPAIR"])
def test_integrated_publication_preserves_exact_main_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    lineage = _integrated_publication_lineage(tmp_path, operation)
    real_git = publication_module._git
    pushes = []

    def racing_git(repo_path, *args, **kwargs):
        if args and args[0] == "push" and args[-1].endswith(":refs/heads/main"):
            pushes.append(args)
            git(lineage["remote"], "update-ref", "refs/heads/main",
                lineage["integration_candidate_sha"], lineage["new_main_sha"])
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_git", racing_git)

    with pytest.raises(PublicationError, match="publication failed"):
        publish(lineage)

    assert len(pushes) == 1
    assert f"--force-with-lease=refs/heads/main:{lineage['new_main_sha']}" in pushes[0]
    assert remote_main(lineage) == lineage["integration_candidate_sha"]


@pytest.mark.parametrize("operation", ["REMEDIATION", "REPAIR"])
def test_integrated_inclusion_on_later_observation_cannot_relax_sampled_mutation_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    lineage = _integrated_publication_lineage(tmp_path, operation)
    git(lineage["remote"], "update-ref", "refs/heads/main", lineage["integration_candidate_sha"])
    real_git = publication_module._git
    main_observations = 0
    pushes = []

    def racing_git(repo_path, *args, **kwargs):
        nonlocal main_observations
        if args == ("ls-remote", "--refs", "origin", "refs/heads/main"):
            main_observations += 1
            if main_observations == 2:
                git(lineage["remote"], "update-ref", "refs/heads/main", lineage["candidate_sha"])
        if args and args[0] == "push":
            pushes.append(args)
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_git", racing_git)

    with pytest.raises(PublicationError, match="authorized main SHA .* is stale"):
        publish(lineage)

    assert main_observations >= 2
    assert pushes == []
    assert remote_main(lineage) == lineage["candidate_sha"]


def _remove_fixture_decision(lineage):
    ref = f"refs/heads/aios/review-decision/{lineage['run_id']}"
    git(lineage["remote"], "update-ref", "-d", ref)
    git(lineage["repo"], "update-ref", "-d", ref)


def _review_envelope(lineage):
    from aios_renew.authoring_ingress import IngressEnvelope
    return IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE", version=1, operation="SUBMIT_REVIEW",
        identity={"run_id": lineage["run_id"]},
        expected_state={"expected_candidate_sha": lineage["candidate_sha"]},
        payload=review_source(lineage["candidate_sha"]),
    )


def test_publisher_reservation_prevents_concurrent_durable_pass_from_being_stranded(tmp_path, monkeypatch):
    """A real Publisher main push holds the same boundary as direct review ingress."""
    from aios_renew.authoring_ingress import execute_ingress, AuthoringIngressError
    earlier = make_lineage(tmp_path)
    later = add_sibling_pass(earlier)
    _remove_fixture_decision(earlier)
    checkout = tmp_path / "publisher-checkout"
    git(tmp_path, "clone", "--quiet", str(earlier["remote"]), str(checkout))
    later = dict(later, repo=checkout)
    at_main_write, continue_write = Event(), Event()
    real_git = publication_module._git

    def paused_main_write(repo, *args, **kwargs):
        if args[0] == "push" and args[-1].endswith(":refs/heads/main"):
            at_main_write.set()
            assert continue_write.wait(20), "bounded concurrency rendezvous expired"
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_git", paused_main_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        continuation = pool.submit(publish, later)
        try:
            assert at_main_write.wait(20)
            token = git(earlier["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF)
            with pytest.raises(AuthoringIngressError, match="RESERVATION_CONTENDED"):
                execute_ingress(_review_envelope(earlier), repo=earlier["repo"])
            assert git(earlier["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF) == token
            assert git(earlier["remote"], "show-ref", "--verify", "--hash",
                       f"refs/heads/aios/review-decision/{earlier['run_id']}", check=False) == ""
            assert remote_main(earlier) == earlier["base_sha"]
        finally:
            continue_write.set()
        assert continuation.result(timeout=20).outcome == "PUBLISHED"
    assert remote_main(earlier) == later["candidate_sha"]
    assert git(earlier["remote"], "show-ref", "--verify", "--hash",
               publication_module.PUBLICATION_RESERVATION_REF, check=False) == ""


def test_review_reservation_blocks_competing_publisher_before_and_after_durable_pass(tmp_path, monkeypatch):
    import aios_renew.authoring_ingress as ingress
    earlier = make_lineage(tmp_path)
    later = add_sibling_pass(earlier)
    _remove_fixture_decision(earlier)
    checkout = tmp_path / "competing-publisher"
    git(tmp_path, "clone", "--quiet", str(earlier["remote"]), str(checkout))
    later = dict(later, repo=checkout)
    before_decision, continue_decision = Event(), Event()
    real_write = ingress._publish_ingress_ref

    def paused_decision(repo, remote, ref, new_sha, *args, **kwargs):
        if ref.startswith("refs/heads/aios/review-decision/"):
            before_decision.set()
            assert continue_decision.wait(20), "bounded concurrency rendezvous expired"
        return real_write(repo, remote, ref, new_sha, *args, **kwargs)

    monkeypatch.setattr(ingress, "_publish_ingress_ref", paused_decision)
    with ThreadPoolExecutor(max_workers=1) as pool:
        submission = pool.submit(ingress.execute_ingress, _review_envelope(earlier), repo=earlier["repo"])
        try:
            assert before_decision.wait(20)
            token = git(earlier["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF)
            with pytest.raises(PublicationError) as blocked:
                publish(later)
            assert blocked.value.report.cause == "RESERVATION_CONTENDED"
            assert blocked.value.report.reviewed_sha == later["candidate_sha"]
            assert remote_main(earlier) == earlier["base_sha"]
        finally:
            continue_decision.set()
        decision = submission.result(timeout=20)
    assert git(earlier["remote"], "rev-parse",
               f"refs/heads/aios/review-decision/{earlier['run_id']}") == decision.canonical_sha
    assert git(earlier["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF) == token
    with pytest.raises(PublicationError) as blocked:
        publish(later)
    assert blocked.value.report.cause == "RESERVATION_CONTENDED"
    assert remote_main(earlier) == earlier["base_sha"]
    # Historical competing PASS authority remains a typed conflict under r2's
    # cumulative guard. Coordination never picks between those semantic sources.
    with pytest.raises(PublicationError) as legacy_conflict:
        publish(dict(earlier, decision_sha=decision.canonical_sha))
    assert legacy_conflict.value.report.cause == "COMPETING_REVIEWED_SOURCE"
    assert remote_main(earlier) == earlier["base_sha"]


def test_durable_pass_reserves_through_publisher_and_blocks_task_main_writer(tmp_path, monkeypatch):
    import aios_renew.authoring_ingress as ingress
    lineage = make_lineage(tmp_path)
    _remove_fixture_decision(lineage)
    # The competing writer starts from canonical main, independently of the
    # review checkout's candidate and decision commits.
    writer_checkout = tmp_path / "task-main-writer"
    git(tmp_path, "clone", "--quiet", "--branch", "main", str(lineage["remote"]), str(writer_checkout))
    before_decision, continue_decision = Event(), Event()
    real_write = ingress._publish_ingress_ref

    def paused_decision(repo, remote, ref, new_sha, *args, **kwargs):
        if ref.startswith("refs/heads/aios/review-decision/"):
            before_decision.set()
            assert continue_decision.wait(20), "bounded concurrency rendezvous expired"
        return real_write(repo, remote, ref, new_sha, *args, **kwargs)

    monkeypatch.setattr(ingress, "_publish_ingress_ref", paused_decision)
    task = yaml.safe_load(TASK_SOURCE)
    task["revision"] = 3
    task["verification"]["policy"] = "minimum-sufficient-v2"
    task["return_affinity"] = {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}
    authoring = ingress.IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE", version=1, operation="AUTHOR_TASK",
        identity={"task_id": "TASK-063"}, expected_state={"expected_main_sha": lineage["base_sha"]},
        payload=task,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        submission = pool.submit(ingress.execute_ingress, _review_envelope(lineage), repo=lineage["repo"])
        try:
            assert before_decision.wait(20)
            token = git(lineage["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF)
            with pytest.raises(ingress.AuthoringIngressError, match="RESERVATION_CONTENDED"):
                ingress.execute_ingress(authoring, repo=writer_checkout)
            assert git(lineage["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF) == token
            assert remote_main(lineage) == lineage["base_sha"]
        finally:
            continue_decision.set()
        decision = submission.result(timeout=20)
    assert decision.status == "CANONICALIZED"
    assert git(lineage["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF) == token
    assert ingress.execute_ingress(_review_envelope(lineage), repo=lineage["repo"]).status == "IDEMPOTENT"
    assert git(lineage["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF) == token
    checkout = tmp_path / "reserved-publisher"
    git(tmp_path, "clone", "--quiet", str(lineage["remote"]), str(checkout))
    report = publish_review_decision(checkout, run_id=lineage["run_id"], decision_sha=decision.canonical_sha)
    assert report.outcome == "PUBLISHED"
    assert remote_main(lineage) == lineage["candidate_sha"]
    assert git(lineage["remote"], "show-ref", "--verify", "--hash",
               publication_module.PUBLICATION_RESERVATION_REF, check=False) == ""
    assert ingress.execute_ingress(_review_envelope(lineage), repo=lineage["repo"]).status == "IDEMPOTENT"
    assert git(lineage["remote"], "show-ref", "--verify", "--hash",
               publication_module.PUBLICATION_RESERVATION_REF, check=False) == ""


def _reserve_fixture_review(lineage):
    return publication_module.reserve_publication(
        lineage["repo"], "origin", kind="REVIEW_TO_PUBLICATION", subject=lineage["run_id"],
        source_sha=lineage["candidate_sha"], main_sha=lineage["base_sha"],
        decision_sha=lineage["decision_sha"], artifacts_sha=git(lineage["remote"], "rev-parse",
            f"refs/heads/aios/artifacts/{lineage['run_id']}"),
    )


def test_reservation_exact_identity_expiry_and_release_are_bounded_and_idempotent(tmp_path, monkeypatch):
    lineage = make_lineage(tmp_path)
    reservation = _reserve_fixture_review(lineage)
    assert _reserve_fixture_review(lineage) == reservation
    with pytest.raises(PublicationError) as contention:
        publication_module.reserve_publication(
            lineage["repo"], "origin", kind="MAIN_MUTATION", subject="TASK-063",
            source_sha=lineage["candidate_sha"], main_sha=lineage["base_sha"],
        )
    assert contention.value.report.cause == "RESERVATION_CONTENDED"
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(publication_module.time, "time", lambda: reservation.expires_at)
        with pytest.raises(PublicationError) as stale:
            _reserve_fixture_review(lineage)
        assert stale.value.report.cause == "RESERVATION_STALE"
        assert git(lineage["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF) == reservation.token_sha
    publication_module.release_publication_reservation(lineage["repo"], "origin", reservation)
    publication_module.release_publication_reservation(lineage["repo"], "origin", reservation)
    assert remote_main(lineage) == lineage["base_sha"]


def test_reservation_malformed_or_moved_owner_never_grants_main_authority(tmp_path):
    lineage = make_lineage(tmp_path)
    reservation = _reserve_fixture_review(lineage)
    git(lineage["remote"], "update-ref", publication_module.PUBLICATION_RESERVATION_REF,
        lineage["base_sha"], reservation.token_sha)
    with pytest.raises(PublicationError) as moved:
        publication_module.check_publication_reservation(lineage["repo"], "origin", reservation)
    assert moved.value.report.cause == "RESERVATION_CAS_FAILED"
    with pytest.raises(PublicationError) as malformed:
        _reserve_fixture_review(lineage)
    assert malformed.value.report.cause == "RESERVATION_INVALID"
    assert remote_main(lineage) == lineage["base_sha"]


@pytest.mark.parametrize("included", [False, True])
def test_reserved_exact_source_replay_releases_only_its_completed_owner(tmp_path, included):
    lineage = make_lineage(tmp_path)
    reservation = _reserve_fixture_review(lineage)
    main_sha = lineage["candidate_sha"]
    if included:
        repo = lineage["repo"]
        git(repo, "checkout", "--quiet", "--detach", main_sha)
        (repo / "later.txt").write_text("later published history\n", encoding="utf-8")
        main_sha = commit_fixture_state(
            repo, paths=("later.txt",), message="later history containing exact source",
            user_name="AIOS Publication Test", user_email="publication@example.invalid",
        )
    # Transfer the descendant's objects with an ordinary fast-forward; a bare
    # update-ref cannot install a commit that only exists in the source checkout.
    git(lineage["repo"], "push", "--quiet", "origin", f"{main_sha}:refs/heads/main")
    report = publish(lineage)
    assert report.outcome == ("ALREADY_INCLUDED" if included else "ALREADY_PUBLISHED")
    assert report.reviewed_sha == reservation.identity["source_sha"]
    assert remote_main(lineage) == main_sha
    assert git(lineage["remote"], "show-ref", "--verify", "--hash",
               publication_module.PUBLICATION_RESERVATION_REF, check=False) == ""


def test_reserved_main_movement_fails_closed_without_reclassifying_pass_authority(tmp_path):
    lineage = make_lineage(tmp_path)
    reservation = _reserve_fixture_review(lineage)
    moved = advance_divergent_main(lineage)
    with pytest.raises(PublicationError) as raised:
        publish(lineage)
    assert raised.value.report.cause == "CONCURRENT_MAIN_MOVEMENT"
    assert raised.value.report.reviewed_sha == lineage["candidate_sha"]
    assert raised.value.report.prior_main_sha == moved
    assert remote_main(lineage) == moved
    assert git(lineage["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF) == reservation.token_sha


def test_ingress_uncovered_main_writer_names_exact_scope_gap_before_mutation(tmp_path):
    from aios_renew.authoring_ingress import _publish_ingress_ref, AuthoringIngressError
    lineage = make_lineage(tmp_path)
    with pytest.raises(AuthoringIngressError, match="WRITER_SCOPE_GAP: src/aios_renew/authoring_ingress.py::_publish_ingress_ref"):
        _publish_ingress_ref(lineage["repo"], "origin", "refs/heads/main",
                             lineage["candidate_sha"], expected_old_sha=lineage["base_sha"])
    assert remote_main(lineage) == lineage["base_sha"]


def recover_publication_fixture(tmp_path, monkeypatch, *, lineage=None, fail_wake=False):
    """Local real-Git Runtime, with a stand-in for published activation only."""
    from aios_renew import operator, review_transport
    lineage = make_lineage(tmp_path) if lineage is None else lineage
    main = advance_divergent_main(lineage, control_branch=True)
    monkeypatch.setattr(operator, "_require_publication_recovery_activation", lambda *_: None)
    calls = []

    def runner(command, **kwargs):
        calls.append(tuple(command))
        return subprocess.run(command, **kwargs)

    if fail_wake:
        from aios_renew.terminal_attention import TerminalAttentionError
        original = review_transport.publish_terminal_attention
        failed = []

        def wake(*args, **kwargs):
            if kwargs["run_id"] != lineage["run_id"] and not failed:
                failed.append(True)
                raise TerminalAttentionError("lost review wake fixture")
            return original(*args, **kwargs)

        monkeypatch.setattr(review_transport, "publish_terminal_attention", wake)
    return lineage, main, calls, runner


def test_publication_recovery_admits_once_then_requires_new_exact_review(tmp_path, monkeypatch):
    from aios_renew import operator
    from aios_renew.authoring_ingress import execute_ingress, IngressEnvelope
    from aios_renew.unified_state import observe_unified_state, observe_semantic_review_scope
    lineage, main, calls, runner = recover_publication_fixture(tmp_path, monkeypatch)
    repo = lineage["repo"]
    before_source = git(repo, "show", f"{lineage['candidate_sha']}:product.txt")
    original_decision = git(lineage["remote"], "rev-parse", f"refs/heads/aios/review-decision/{lineage['run_id']}")
    recovered = operator.recover_publication_source(lineage["run_id"], decision_sha=lineage["decision_sha"],
        expected_main_sha=main, repo=repo, verification_runner=runner)
    assert recovered.run_id != lineage["run_id"] and recovered.head_sha != lineage["candidate_sha"]
    assert remote_main(lineage) == main and len(calls) == 1
    assert git(repo, "show", f"{recovered.head_sha}:product.txt") == before_source
    assert git(repo, "show", f"{recovered.head_sha}:later.txt") == git(repo, "show", f"{main}:later.txt")
    assert git(repo, "rev-parse", f"{recovered.head_sha}^@").splitlines() == [main, lineage["candidate_sha"]]
    observation = observe_unified_state("TASK-063", repo=repo)
    assert observation.next_action == "SEMANTIC_REVIEW" and observation.run_id == recovered.run_id
    scope = observe_semantic_review_scope("TASK-063", repo=repo)
    assert scope["review_mode"] == "PRIMARY" and scope["reviewed_head_sha"] == recovered.head_sha
    assert scope["publication_provenance"]["prior_pass_is_verdict"] is False
    refs = git(lineage["remote"], "show-ref")
    replay = operator.recover_publication_source(lineage["run_id"], decision_sha=lineage["decision_sha"],
        expected_main_sha=main, repo=repo, verification_runner=runner)
    assert replay.head_sha == recovered.head_sha and replay.run_id == recovered.run_id
    assert len(calls) == 1 and git(lineage["remote"], "show-ref") == refs
    with pytest.raises(PublicationError):
        publish_review_decision(repo, run_id=recovered.run_id, decision_sha=lineage["decision_sha"], control_sha=main)
    assert remote_main(lineage) == main
    review = review_source(recovered.head_sha).replace("REVIEW-063-001", "REVIEW-" + recovered.run_id[4:])
    ingress = execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW",
        {"run_id": recovered.run_id}, {"expected_candidate_sha": recovered.head_sha}, review), repo=repo)
    report = publication_module.continue_publication(repo, run_id=lineage["run_id"],
        decision_sha=lineage["decision_sha"], control_sha=main)
    assert report.source_run == recovered.run_id and report.outcome == "PUBLISHED"
    assert remote_main(lineage) == recovered.head_sha and len(calls) == 1
    assert git(lineage["remote"], "rev-parse", f"refs/heads/aios/review-decision/{lineage['run_id']}") == original_decision
    assert publish_review_decision(repo, run_id=recovered.run_id, decision_sha=ingress.canonical_sha,
                                   control_sha=recovered.head_sha).classification == "ALREADY_INCLUDED"


def test_publication_recovery_lost_review_wake_resumes_canonical_result_without_proof(tmp_path, monkeypatch):
    from aios_renew import operator
    from aios_renew.review_transport import ReviewTransportError
    lineage, main, calls, runner = recover_publication_fixture(tmp_path, monkeypatch, fail_wake=True)
    with pytest.raises(ReviewTransportError, match="attention delivery failed"):
        operator.recover_publication_source(lineage["run_id"], decision_sha=lineage["decision_sha"],
            expected_main_sha=main, repo=lineage["repo"], verification_runner=runner)
    recovered = operator.recover_publication_source(lineage["run_id"], decision_sha=lineage["decision_sha"],
        expected_main_sha=main, repo=lineage["repo"], verification_runner=runner)
    assert len(calls) == 1 and remote_main(lineage) == main
    assert git(lineage["remote"], "show-ref", "--verify", "--hash",
               f"refs/heads/aios/failure-artifacts/{recovered.run_id}", check=False) == ""


@pytest.mark.parametrize("mutation", ["main", "review", "artifact", "task", "overlap", "missing-evidence"])
def test_publication_recovery_counterexamples_never_mutate_main(tmp_path, monkeypatch, mutation):
    from aios_renew import operator
    lineage, main, _, runner = recover_publication_fixture(tmp_path, monkeypatch)
    repo, remote = lineage["repo"], lineage["remote"]
    if mutation in {"task", "overlap", "main"}:
        path = ".ai/tasks/TASK-063.yaml" if mutation == "task" else "product.txt" if mutation == "overlap" else "other.txt"
        (repo / path).write_text(TASK_SOURCE.replace("revision: 2", "revision: 3") if mutation == "task" else "moved\n", encoding="utf-8")
        git(repo, "add", path)
        git(repo, "commit", "--quiet", "-m", mutation)
        git(repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
        if mutation != "main":
            main = git(repo, "rev-parse", "HEAD")
    elif mutation == "review":
        git(remote, "update-ref", f"refs/heads/aios/review-decision/{lineage['run_id']}", lineage["candidate_sha"])
    elif mutation in {"artifact", "missing-evidence"}:
        # Missing canonical RESULT/EVIDENCE or artifacts cannot be papered over.
        git(remote, "update-ref", f"refs/heads/aios/artifacts/{lineage['run_id']}", lineage["base_sha"])
    before = remote_main(lineage)
    with pytest.raises((PublicationError, operator.OperatorError, ValueError)):
        operator.recover_publication_source(lineage["run_id"], decision_sha=lineage["decision_sha"],
            expected_main_sha=main, repo=repo, verification_runner=runner)
    assert remote_main(lineage) == before
    assert git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/publication-source/") == ""


def test_publication_recovery_without_published_activation_has_no_run(tmp_path):
    from aios_renew import operator
    lineage = make_lineage(tmp_path)
    main = advance_divergent_main(lineage, control_branch=True)
    with pytest.raises(PublicationError) as raised:
        operator.recover_publication_source(lineage["run_id"], decision_sha=lineage["decision_sha"],
            expected_main_sha=main, repo=lineage["repo"])
    assert raised.value.report.cause == "RECOVERY_NOT_ACTIVATED"
    assert list(operator.runtime_paths(lineage["repo"]).runs.glob("*.json")) == []
    assert remote_main(lineage) == main


def test_publication_recovery_competing_divergent_pass_cannot_select_a_winner(tmp_path, monkeypatch):
    from aios_renew import operator
    lineage, main, calls, runner = recover_publication_fixture(tmp_path, monkeypatch)
    sibling = add_sibling_pass(lineage)
    git(lineage["repo"], "checkout", "--quiet", "fixture/publication-control")
    assert git(lineage["repo"], "rev-parse", "HEAD") == main
    with pytest.raises(PublicationError) as raised:
        operator.recover_publication_source(lineage["run_id"], decision_sha=lineage["decision_sha"],
            expected_main_sha=main, repo=lineage["repo"], verification_runner=runner)
    assert raised.value.report.classification == "COMPETING_SOURCE"
    assert raised.value.report.blocker.source_run == sibling["run_id"]
    assert calls == [] and remote_main(lineage) == main
    assert git(lineage["remote"], "for-each-ref", "--format=%(refname)", "refs/heads/aios/publication-source/") == ""


def test_publication_outcome_classification_is_closed_and_receipts_are_not_success():
    report = publication_module.PublicationReport
    assert report("RUN-1", "a" * 40, "b" * 40, "PUBLISHED", "included").classification == "PUBLISHABLE"
    assert report("RUN-1", "a" * 40, "b" * 40, "ALREADY_PUBLISHED", "included").classification == "ALREADY_INCLUDED"
    assert report("RUN-1", "a" * 40, "b" * 40, "FAILED", "queued").classification == "CONFLICT_OR_UNKNOWN"
    assert report("RUN-1", "a" * 40, "b" * 40, "FAILED", "moved", "CONCURRENT_MAIN_MOVEMENT").classification == "STALE_BINDING"
    assert report("RUN-1", "a" * 40, "b" * 40, "RECOVERY_BLOCKED", "competing", "COMPETING_REVIEWED_SOURCE").classification == "COMPETING_SOURCE"


def run326_planning_divergence_fixture(tmp_path, *, material_overlap=False):
    """Reproduce the RUN-326-001 source/planning topology without live refs.

    The historical observed identities remain immutable test provenance:
    source 9b2ec4afbedc4ef342b3ea198c59abbb48938f74,
    artifact 5b5af1e276c9a1bb7aa0031952e1390fe607fc9c,
    PASS b526cfdded14d74985af42fc4a14b7b54045b260. Disposable
    commits model that same three-file source / roadmap-only main delta.
    """
    paths = ("src/aios_renew/proof_coverage_contract.py", "tests/test_proof_coverage_contract.py",
             "docs/AIOS-VP02-PROOF-COVERAGE-CONTRACT-v1.md")
    task = TASK_SOURCE.replace("TASK-063", "TASK-326").replace("revision: 2", "revision: 1").replace(
        "[product.txt, secondary.txt]", json.dumps(paths))
    repo, remote, base = materialize_git_baseline(tmp_path,
        files={".ai/tasks/TASK-326.yaml": task, ".ai/roadmap-state.yaml": "route: prior\n",
               **{path: "base\n" for path in paths}}, user_name="Publication Fixture",
        user_email="fixture@aios.invalid", commit_message="RUN-326 base topology")
    for path in paths:
        (repo / path).write_text("exact reviewed proof contract\n", encoding="utf-8")
    candidate = commit_fixture_state(repo, paths=paths, message="RUN-326 reviewed source",
        user_name="Publication Fixture", user_email="fixture@aios.invalid")
    state = tmp_path / "state"
    state.mkdir()
    run_path, result_path = state / "run.json", state / "result.json"
    run_path.write_text(json.dumps(dict(run_id="RUN-326-001", task=dict(id="TASK-326", revision=1),
        executor="codex", base_sha=base, workspace=str(repo), head_sha=None, status="ACTIVE")), encoding="utf-8")
    result_path.write_text(json.dumps(result_payload("RUN-326-001", candidate, changed_files=paths)), encoding="utf-8")
    transport_post_pass(repo, run_id="RUN-326-001", head_sha=candidate, run_path=run_path, result_path=result_path)
    review_path = repo / ".ai/reviews/REVIEW-326-001.yaml"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(review_source(candidate).replace("REVIEW-063-001", "REVIEW-326-001"), encoding="utf-8")
    decision = commit_fixture_state(repo, paths=(".ai/reviews/REVIEW-326-001.yaml",), message="REVIEW-326 PASS",
        user_name="Publication Fixture", user_email="fixture@aios.invalid", remote=remote,
        remote_ref="refs/heads/aios/review-decision/RUN-326-001")
    git(repo, "checkout", "--quiet", "--detach", base)
    path = paths[0] if material_overlap else ".ai/roadmap-state.yaml"
    (repo / path).write_text("competing material\n" if material_overlap else "route: LEGACY_REPOSITORY_DEFAULT_ROUTE\n", encoding="utf-8")
    main = commit_fixture_state(repo, paths=(path,), message="Human legacy-route planning" if not material_overlap else "material divergence",
        user_name="Publication Fixture", user_email="fixture@aios.invalid", remote=remote, remote_ref="refs/heads/main")
    return dict(repo=repo, remote=remote, run_id="RUN-326-001", base_sha=base,
                candidate_sha=candidate, decision_sha=decision, main_sha=main, paths=paths)


@pytest.mark.parametrize("material_overlap", [False, True])
def test_original_run326_planning_divergence_and_material_counterexample(tmp_path, material_overlap):
    lineage = run326_planning_divergence_fixture(tmp_path, material_overlap=material_overlap)
    before = remote_main(lineage)
    if material_overlap:
        with pytest.raises(PublicationError) as raised:
            publication_module.prepare_publication_recovery(lineage["repo"], run_id=lineage["run_id"],
                decision_sha=lineage["decision_sha"], expected_main_sha=before)
        assert raised.value.report.classification == "CONFLICT_OR_UNKNOWN"
    else:
        plan, task, _, _, _, _ = publication_module.prepare_publication_recovery(lineage["repo"],
            run_id=lineage["run_id"], decision_sha=lineage["decision_sha"], expected_main_sha=before)
        assert plan.task_id == "TASK-326" and plan.task_revision == 1
        assert set(path for path, _, _ in plan.delta) == set(lineage["paths"])
        assert git(lineage["repo"], "show", f"{plan.tree_sha}:.ai/roadmap-state.yaml") == "route: LEGACY_REPOSITORY_DEFAULT_ROUTE"
        for path, mode, blob in plan.delta:
            assert mode == "100644" and blob == git(lineage["repo"], "rev-parse", f"{lineage['candidate_sha']}:{path}")
        assert task.task_id == "TASK-326"
    assert remote_main(lineage) == before
    assert git(lineage["remote"], "for-each-ref", "--format=%(refname)", "refs/heads/aios/publication-source/") == ""


def publication_proof_fixture(tmp_path, monkeypatch):
    from aios_renew import verification, correction_integration
    from aios_renew.artifacts import Result, ResultPackage, Evidence, EvidenceSource, EvidenceOutcome
    from aios_renew.verification_contract import verification_digest
    import hashlib
    command, original, candidate, main, base, tree = "python -m pytest -q tests/test_product.py", "a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40
    toolchain = dict(python_implementation="CPython", python_version="3.11", python_executable="fixture",
        platform_system="fixture", platform_machine="fixture", pytest_version="8", pytest_xdist_version="3")
    profile = {"profile": "fixture", "environment_digest": "same", "helper_blob": "same"}
    task = SimpleNamespace(scope=SimpleNamespace(modify=("product.txt",)),
        verification=SimpleNamespace(required=(command,), policy="minimum-sufficient-v2"))
    plan = SimpleNamespace(identity="f" * 64, reviewed_sha=original, artifacts_sha="1" * 40,
        decision_sha="2" * 40, main_sha=main, tree_sha=tree, delta=(("product.txt", "100644", "3" * 40),))
    source_run, run = SimpleNamespace(run_id="RUN-063-001", base_sha=base), SimpleNamespace(run_id="RUN-063-002", base_sha=main)
    raw = tmp_path / "original.raw"
    raw.write_bytes(b"original observed pass")
    binding = dict(subject_sha=original, base_sha=base, tree_sha=tree, command=command, profile=profile,
        toolchain=toolchain, envelope_digest=verification_digest({"authored": (command,), "operation": "PRIMARY", "modification_scope": ["product.txt"]}),
        changed_files_digest=verification_digest(("product.txt",)), failure_set_digest=verification_digest([]))
    observation = dict(subject_sha=original, command=command, profile=profile, toolchain=toolchain,
        complete=True, unstable=False, reports=[], failure_count=0, exit_code=0)
    record = dict(policy="minimum-sufficient-v2", evidence_id="E1", binding=binding, candidate=observation,
        candidate_digest=verification_digest(observation), raw_digest=hashlib.sha256(raw.read_bytes()).hexdigest())
    item = Evidence("E1", source_run.run_id, original, "VERIFICATION", EvidenceSource(command), EvidenceOutcome(0, "observed pass"), str(raw), record)
    package = ResultPackage(Result(original, (), ("product.txt",), ()), (item,))
    monkeypatch.setattr(verification, "_v2_profile", lambda *_args, **_kwargs: profile)
    monkeypatch.setattr(verification, "_v2_toolchain", lambda: toolchain)
    monkeypatch.setattr(correction_integration, "publication_tree_entries", lambda _repo, sha:
        {"product.txt": ("100644", ("0" if sha in {base, main} else "3") * 40)})
    monkeypatch.setattr(verification, "_git", lambda _repo, _op, subject: main + "\n" + original if subject.endswith("^@") else tree)
    arguments = dict(task=task, run=run, plan=plan, source_run=source_run, source_package=package,
        subject_sha=candidate, repository=tmp_path, raw_directory=tmp_path / "proof", cache_directory=tmp_path / "cache", environment={})
    return verification, arguments, item, raw


def test_publication_unchanged_proof_is_reused_with_distinct_integration_guard(tmp_path, monkeypatch):
    verification, arguments, source, raw = publication_proof_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(verification, "execute_minimum_verification", lambda *_args, **_kwargs: pytest.fail("valid proof repeated"))
    evidence, audit = verification.execute_publication_verification(**arguments)
    assert audit["records"][0]["validity"] == "VALID" and audit["records"][0]["disposition"] == "REUSED"
    assert evidence[0].subject_sha == arguments["subject_sha"] and evidence[0].run_id == arguments["run"].run_id
    receipt = json.loads(Path(evidence[0].raw_path).read_text(encoding="utf-8"))
    assert receipt["source_evidence_id"] == source.evidence_id
    assert receipt["source_verification"] == source.verification and raw.read_bytes() == b"original observed pass"
    assert evidence[-1].type == "PUBLICATION_INTEGRATION"


@pytest.mark.parametrize("condition", ["profile", "toolchain", "test", "fixture", "helper", "missing-raw", "missing-evidence", "ambiguous-evidence"])
def test_publication_invalid_or_unknown_proof_requires_bounded_execution_not_false_pass(tmp_path, monkeypatch, condition):
    from aios_renew import correction_integration
    from dataclasses import replace
    verification, arguments, source, raw = publication_proof_fixture(tmp_path, monkeypatch)
    if condition == "profile":
        monkeypatch.setattr(verification, "_v2_profile", lambda *_args, **_kwargs: {"profile": "changed"})
    elif condition == "toolchain":
        monkeypatch.setattr(verification, "_v2_toolchain", lambda: {"toolchain": "changed"})
    elif condition in {"test", "fixture", "helper"}:
        monkeypatch.setattr(correction_integration, "publication_tree_entries", lambda _repo, sha:
            {"product.txt": ("100644", ("0" if sha in {arguments["source_run"].base_sha, arguments["run"].base_sha} else "3") * 40),
             condition + ".py": ("100644", ("4" if sha in {arguments["subject_sha"], arguments["run"].base_sha} else "5") * 40)})
    elif condition == "missing-raw":
        raw.unlink()
    else:
        arguments["source_package"] = replace(arguments["source_package"], evidence=() if condition == "missing-evidence" else (source, source))
    calls = []

    def fail_required(commands, **kwargs):
        calls.append((tuple(commands), kwargs["base_sha"], kwargs["subject_sha"]))
        raise verification.RuntimeVerificationError("affected proof failed")

    monkeypatch.setattr(verification, "execute_minimum_verification", fail_required)
    with pytest.raises(verification.RuntimeVerificationError, match="affected proof failed"):
        verification.execute_publication_verification(**arguments)
    assert calls == [(arguments["task"].verification.required, arguments["run"].base_sha, arguments["subject_sha"])]
