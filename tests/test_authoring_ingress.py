"""Deterministic tests for generic Brain authoring ingress."""

import json
import subprocess
from pathlib import Path

import pytest

from aios_renew.authoring_ingress import (
    AuthoringIngressError,
    IngressEnvelope,
    IngressResult,
    ingest_carrier,
    parse_envelope,
    read_carrier_input,
    execute_ingress,
)
from aios_renew.publication import publish_review_decision
from aios_renew.review_transport import (
    ReviewTransportError,
    resolve_remote_repair_authorization,
    resolve_remote_task_lifecycle,
    transport_failure,
    transport_post_pass,
)
from aios_renew.unified_state import observe_unified_state


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=check,
    )
    return proc.stdout.strip()


def assert_exact_metadata_delta(
    repo: Path,
    commit_sha: str,
    parent_sha: str,
    metadata_path: str,
    metadata_bytes: bytes,
) -> None:
    assert git(repo, "show", "-s", "--format=%P", commit_sha).split() == [parent_sha]
    assert git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        parent_sha,
        commit_sha,
    ).splitlines() == [metadata_path]
    entry = git(repo, "ls-tree", commit_sha, "--", metadata_path)
    assert entry.startswith("100644 blob ")
    assert entry.endswith(f"\t{metadata_path}")
    blob = subprocess.run(
        ("git", "-C", str(repo), "show", f"{commit_sha}:{metadata_path}"),
        capture_output=True,
        check=True,
    ).stdout
    assert blob == metadata_bytes


TASK_105_SOURCE = """\
task_id: TASK-105
revision: 1
goal: Implement generic ingress capability.
problem: Brain lacks generic transport-neutral authoring ingress.
assumptions:
  - Canonical main is established.
scope:
  inspect: []
  modify: [src/sample.py]
non_goals:
  - Arbitrary mutations.
constraints:
  hard:
    - Bounded mutation authority only.
acceptance:
  - id: AC1
    condition: Ingress envelope is validated.
verification:
  required:
    - git diff --check
"""

TASK_105_R2_SOURCE = """\
task_id: TASK-105
revision: 2
goal: Revise generic ingress capability.
problem: Second revision needed.
assumptions:
  - Canonical main is established.
scope:
  inspect: []
  modify: [src/sample.py]
non_goals:
  - Arbitrary mutations.
constraints:
  hard:
    - Bounded mutation authority only.
acceptance:
  - id: AC1
    condition: Ingress envelope is validated.
verification:
  required:
    - git diff --check
"""


def setup_test_repo(root: Path) -> tuple[Path, Path, str]:
    """Create local repo and bare upstream git repo."""
    repo = root / "repo"
    remote = root / "upstream.git"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "init", "--bare", "--quiet", str(remote)), check=True)

    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "AIOS Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "branch", "-M", "main")
    (repo / "README.md").write_text("initial repo\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "initial commit")
    base_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "main")
    return repo, remote, base_sha


def setup_candidate_lineage(
    root: Path,
    *,
    run_id: str = "RUN-105-001",
    task_id: str = "TASK-105",
    task_source: str = TASK_105_SOURCE,
    candidate_file: str = "src/sample.py",
    candidate_content: str = "def sample(): return True\n",
    run_override: dict[str, object] | None = None,
    result_override: dict[str, object] | None = None,
) -> dict[str, object]:
    repo, remote, base_sha = setup_test_repo(root)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task_id}.yaml").write_text(task_source, encoding="utf-8")
    workflow = repo / ".github" / "workflows" / "aios-auto-publish.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("name: AIOS auto publish\n", encoding="utf-8")
    (repo / "README.md").write_text("base content\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", f"add {task_id}")
    task_main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    sample_path = repo / candidate_file
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(candidate_content, encoding="utf-8")
    git(repo, "add", candidate_file)
    git(repo, "commit", "--quiet", "-m", "candidate implementation")
    candidate_sha = git(repo, "rev-parse", "HEAD")

    # Post-pass transport
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    run_path = state / "run.json"
    result_path = state / "result.json"

    run_payload = {
        "run_id": run_id,
        "task": {"id": task_id, "revision": 1},
        "executor": "antigravity",
        "base_sha": task_main_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    result_payload = {
        "result": {
            "head_sha": candidate_sha,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "The candidate is valid.",
                    "evidence": ["E1"],
                }
            ],
            "changed_files": [candidate_file],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id,
                "subject_sha": candidate_sha,
                "type": "verification",
                "source": {"command": "git diff --check"},
                "result": {"exit_code": 0, "summary": "clean"},
                "raw": {"path": ".git/aios/evidence/E1.log"},
            }
        ],
    }
    if run_override:
        run_payload.update(run_override)
    if result_override:
        for k, v in result_override.items():
            if k == "result" and isinstance(v, dict):
                v_copy = dict(v)
                if v_copy.get("head_sha") == "candidate_sha":
                    v_copy["head_sha"] = candidate_sha
                result_payload["result"] = v_copy
            elif k == "evidence" and isinstance(v, list):
                ev_list = []
                for item in v:
                    item_copy = dict(item)
                    if item_copy.get("subject_sha") == "candidate_sha":
                        item_copy["subject_sha"] = candidate_sha
                    ev_list.append(item_copy)
                result_payload["evidence"] = ev_list
            else:
                result_payload[k] = v

    run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    result_path.write_text(json.dumps(result_payload), encoding="utf-8")

    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=candidate_sha,
        run_path=run_path,
        result_path=result_path,
    )

    return {
        "repo": repo,
        "remote": remote,
        "task_id": task_id,
        "run_id": run_id,
        "main_sha": task_main_sha,
        "candidate_sha": candidate_sha,
    }


# ===========================================================================
# AC1: Envelope validation, reject unknown/prohibited fields, carrier input
# ===========================================================================

def test_envelope_validation_rejection():
    # Invalid format
    with pytest.raises(AuthoringIngressError, match="invalid envelope format"):
        parse_envelope({"format": "INVALID", "version": 1, "operation": "AUTHOR_TASK"})

    # Invalid version
    with pytest.raises(AuthoringIngressError, match="invalid envelope version"):
        parse_envelope({"format": "AIOS_INGRESS_ENVELOPE", "version": 2, "operation": "AUTHOR_TASK"})

    # Unknown operation
    with pytest.raises(AuthoringIngressError, match="invalid envelope operation"):
        parse_envelope({"format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "UNKNOWN_OP"})

    # Prohibited destination field
    with pytest.raises(AuthoringIngressError, match="prohibited field detected: 'destination'"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "destination": ".ai/tasks/TASK-105.yaml",
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Prohibited command field
    with pytest.raises(AuthoringIngressError, match="prohibited field detected: 'command'"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "command": "git push origin main",
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Prohibited authority override
    with pytest.raises(AuthoringIngressError, match="prohibited field detected: 'credentials'"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "credentials": "token-123",
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Prohibited ref
    with pytest.raises(AuthoringIngressError, match="prohibited field detected: 'ref'"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "ref": "refs/heads/main",
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Unknown top-level field
    with pytest.raises(AuthoringIngressError, match="envelope contains unknown field"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "mystery_param": True,
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Envelope format alias rejection (AIOS_AUTHORING_INGRESS is rejected)
    with pytest.raises(AuthoringIngressError, match="invalid envelope format"):
        parse_envelope({
            "format": "AIOS_AUTHORING_INGRESS",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Top-level identity alias rejection
    with pytest.raises(AuthoringIngressError, match="identity"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "task_id": "TASK-105",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Duplicate identity representation (both top-level and in identity) fails closed
    with pytest.raises(AuthoringIngressError, match="identity"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "task_id": "TASK-105",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Conflicting identity representation fails closed
    with pytest.raises(AuthoringIngressError, match="identity"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "task_id": "TASK-999",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Missing identity mapping fails closed
    with pytest.raises(AuthoringIngressError, match="identity is required and must be a mapping"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    with pytest.raises(AuthoringIngressError, match="identity is required and must be a mapping"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": "TASK-105",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Unexpected keys in identity mapping fail closed
    with pytest.raises(AuthoringIngressError, match="identity contains unexpected key"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105", "run_id": "RUN-105-001"},
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })


def test_carrier_parsing(tmp_path):
    envelope_data = {
        "format": "AIOS_INGRESS_ENVELOPE",
        "version": 1,
        "operation": "AUTHOR_TASK",
        "identity": {"task_id": "TASK-105"},
        "expected_state": {"expected_main_sha": "a" * 40},
        "payload": TASK_105_SOURCE,
    }
    # File delivery
    file_path = tmp_path / "envelope.json"
    file_path.write_text(json.dumps(envelope_data), encoding="utf-8")
    env = read_carrier_input(str(file_path))
    assert env.operation == "AUTHOR_TASK"
    assert env.identity["task_id"] == "TASK-105"

    # Stdin delivery
    env_stdin = read_carrier_input("-", stdin_bytes=json.dumps(envelope_data).encode("utf-8"))
    assert env_stdin.operation == "AUTHOR_TASK"

    # Missing file fails closed
    with pytest.raises(AuthoringIngressError, match="envelope file not found"):
        read_carrier_input(str(tmp_path / "nonexistent.json"))


# ===========================================================================
# AC2: AUTHOR_TASK deterministic canonicalization, revisions, continuity, CAS
# ===========================================================================

def test_author_task_new_and_revision(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)

    # 1. Author new TASK-105 revision 1
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": base_sha},
        payload=TASK_105_SOURCE,
    )
    result = execute_ingress(envelope, repo=repo)
    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == ".ai/tasks/TASK-105.yaml"
    assert result.replayed is False
    assert (repo / ".ai" / "tasks" / "TASK-105.yaml").is_file()

    new_main_sha = git(repo, "rev-parse", "refs/heads/main")
    assert new_main_sha == result.canonical_sha
    assert git(remote, "rev-parse", "refs/heads/main") == new_main_sha

    # 2. Idempotent replay of identical TASK revision 1
    replay_result = execute_ingress(envelope, repo=repo)
    assert replay_result.status == "IDEMPOTENT"
    assert replay_result.replayed is True
    assert replay_result.canonical_sha == new_main_sha

    # 3. Conflicting attempt (different payload for same revision 1) fails closed
    conflicting_source = TASK_105_SOURCE.replace("Implement generic ingress capability.", "Different goal.")
    conflicting_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": new_main_sha},
        payload=conflicting_source,
    )
    with pytest.raises(AuthoringIngressError, match="conflicting TASK payload"):
        execute_ingress(conflicting_env, repo=repo)

    # 4. Stale CAS attempt fails closed
    stale_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": base_sha},  # stale
        payload=TASK_105_R2_SOURCE,
    )
    with pytest.raises(AuthoringIngressError, match="expected main SHA mismatch"):
        execute_ingress(stale_env, repo=repo)

    # 5. Continuity violation: revision 3 when revision 1 is on main fails closed
    r3_source = TASK_105_R2_SOURCE.replace("revision: 2", "revision: 3")
    r3_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": new_main_sha},
        payload=r3_source,
    )
    with pytest.raises(AuthoringIngressError, match="revision continuity violation"):
        execute_ingress(r3_env, repo=repo)

    # 6. Valid revision 2 succeeds
    r2_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": new_main_sha},
        payload=TASK_105_R2_SOURCE,
    )
    r2_result = execute_ingress(r2_env, repo=repo)
    assert r2_result.status == "CANONICALIZED"
    assert "revision: 2" in (repo / ".ai" / "tasks" / "TASK-105.yaml").read_text(encoding="utf-8")


# ===========================================================================
# AC3, AC4, AC5: SUBMIT_REVIEW (PASS and CHANGES_REQUIRED), main separation
# ===========================================================================

def test_submit_review_pass_and_changes_required(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    candidate_sha = lineage["candidate_sha"]
    main_before = git(repo, "rev-parse", "refs/heads/main")

    pass_review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
"""
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id},
        expected_state={"expected_candidate_sha": candidate_sha},
        payload=pass_review,
    )
    res = execute_ingress(envelope, repo=repo)
    assert res.status == "CANONICALIZED"
    assert res.canonical_destination == f"refs/heads/aios/review-decision/{run_id}"
    assert_exact_metadata_delta(
        repo,
        res.canonical_sha,
        candidate_sha,
        ".ai/reviews/REVIEW-105-001.yaml",
        pass_review.encode("utf-8"),
    )
    assert git(
        repo,
        "rev-parse",
        f"{res.canonical_sha}:.github/workflows/aios-auto-publish.yml",
    ) == git(
        repo,
        "rev-parse",
        f"{candidate_sha}:.github/workflows/aios-auto-publish.yml",
    )

    # AC3: Prove review metadata cannot become implementation-main content
    main_after = git(repo, "rev-parse", "refs/heads/main")
    assert main_after == main_before
    code, _, _ = subprocess.run(
        ("git", "-C", str(repo), "show", "refs/heads/main:.ai/reviews/REVIEW-105-001.yaml"),
        capture_output=True,
    ).returncode, "", ""
    assert code != 0, "review metadata must not exist on implementation main"

    # AC5: Ingress does not publish main, but canonical publication independently succeeds
    pub_report = publish_review_decision(
        repo,
        run_id=run_id,
        decision_sha=res.canonical_sha,
    )
    assert pub_report.outcome == "PUBLISHED"
    # Now main has advanced to reviewed candidate
    assert git(repo, "rev-parse", "origin/main") == candidate_sha
    # And candidate on main STILL does not contain review metadata!
    code, _, _ = subprocess.run(
        ("git", "-C", str(repo), "show", "origin/main:.ai/reviews/REVIEW-105-001.yaml"),
        capture_output=True,
    ).returncode, "", ""
    assert code != 0, "candidate on main must not contain review metadata"


def test_changes_required_review_observable_in_unified_state(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    task_id = lineage["task_id"]
    candidate_sha = lineage["candidate_sha"]

    cr_review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Missing required feature.
    expected: Implement feature.
"""
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id},
        expected_state={"expected_candidate_sha": candidate_sha},
        payload=cr_review,
    )
    res = execute_ingress(envelope, repo=repo)
    assert res.status == "CANONICALIZED"
    assert_exact_metadata_delta(
        repo,
        res.canonical_sha,
        candidate_sha,
        ".ai/reviews/REVIEW-105-001.yaml",
        cr_review.encode("utf-8"),
    )
    assert git(
        repo,
        "rev-parse",
        f"{res.canonical_sha}:.github/workflows/aios-auto-publish.yml",
    ) == git(
        repo,
        "rev-parse",
        f"{candidate_sha}:.github/workflows/aios-auto-publish.yml",
    )

    # AC4: Observable through existing Unified State path without creating RUN or invoking Executor
    runs_dir = repo / ".git" / "aios" / "runs"
    assert not runs_dir.exists() or len(list(runs_dir.glob("*.json"))) == 0

    obs = observe_unified_state(task_id, repo=repo)
    assert obs.lifecycle_state == "CORRECTION"
    assert obs.next_action == "AUTHOR_REMEDIATION"
    assert obs.finding_id == "F1"

    # A same-payload replay cannot certify or replace a malformed destination.
    decision_ref = f"refs/heads/aios/review-decision/{run_id}"
    decision_tree = git(repo, "rev-parse", f"{res.canonical_sha}^{{tree}}")
    malformed_sha = git(
        repo,
        "commit-tree",
        decision_tree,
        "-p",
        lineage["main_sha"],
        "-m",
        "malformed review decision",
    )
    git(repo, "push", "--quiet", "--force", "origin", f"{malformed_sha}:{decision_ref}")
    with pytest.raises(AuthoringIngressError, match="conflicting review decision"):
        execute_ingress(envelope, repo=repo)
    assert git(repo, "ls-remote", "--refs", "origin", decision_ref).split()[0] == malformed_sha
    assert git(
        repo, "show", f"{malformed_sha}:.ai/reviews/REVIEW-105-001.yaml"
    )


def test_submit_review_rejects_run_identity_and_task_revision_mismatches(tmp_path):
    # 1. RUN run_id mismatch
    p1 = tmp_path / "case1"
    lineage1 = setup_candidate_lineage(
        p1,
        run_override={"run_id": "RUN-105-999"},
    )
    repo1 = lineage1["repo"]
    run_id1 = lineage1["run_id"]
    candidate_sha1 = lineage1["candidate_sha"]
    env1 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id1},
        expected_state={"expected_candidate_sha": candidate_sha1},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha1}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="RUN run_id mismatch"):
        execute_ingress(env1, repo=repo1)

    # 2. TASK revision mismatch (RUN references r2, but candidate has r1)
    p2 = tmp_path / "case2"
    lineage2 = setup_candidate_lineage(
        p2,
        run_override={"task": {"id": "TASK-105", "revision": 2}},
    )
    repo2 = lineage2["repo"]
    run_id2 = lineage2["run_id"]
    candidate_sha2 = lineage2["candidate_sha"]
    env2 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id2},
        expected_state={"expected_candidate_sha": candidate_sha2},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha2}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="RUN does not reference the supplied TASK"):
        execute_ingress(env2, repo=repo2)


def test_submit_review_rejects_evidence_and_verification_mismatches(tmp_path):
    # 1. Evidence run_id mismatch
    p1 = tmp_path / "case1"
    lineage1 = setup_candidate_lineage(
        p1,
        result_override={
            "evidence": [
                {
                    "evidence_id": "E1",
                    "run_id": "RUN-OTHER-999",
                    "subject_sha": "candidate_sha",
                    "type": "verification",
                    "source": {"command": "git diff --check"},
                    "result": {"exit_code": 0, "summary": "clean"},
                    "raw": {"path": ".git/aios/evidence/E1.log"},
                }
            ]
        },
    )
    repo1 = lineage1["repo"]
    run_id1 = lineage1["run_id"]
    candidate_sha1 = lineage1["candidate_sha"]
    env1 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id1},
        expected_state={"expected_candidate_sha": candidate_sha1},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha1}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="does not reference RUN"):
        execute_ingress(env1, repo=repo1)

    # 2. Evidence subject_sha mismatch
    p2 = tmp_path / "case2"
    lineage2 = setup_candidate_lineage(
        p2,
        result_override={
            "evidence": [
                {
                    "evidence_id": "E1",
                    "run_id": "RUN-105-001",
                    "subject_sha": "0" * 40,
                    "type": "verification",
                    "source": {"command": "git diff --check"},
                    "result": {"exit_code": 0, "summary": "clean"},
                    "raw": {"path": ".git/aios/evidence/E1.log"},
                }
            ]
        },
    )
    repo2 = lineage2["repo"]
    run_id2 = lineage2["run_id"]
    candidate_sha2 = lineage2["candidate_sha"]
    env2 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id2},
        expected_state={"expected_candidate_sha": candidate_sha2},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha2}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="subject_sha does not match RESULT head_sha"):
        execute_ingress(env2, repo=repo2)

    # 3. Missing required verification evidence
    p3 = tmp_path / "case3"
    lineage3 = setup_candidate_lineage(
        p3,
        result_override={
            "result": {
                "head_sha": "candidate_sha",
                "claims": [
                    {
                        "id": "C1",
                        "satisfies": ["AC1"],
                        "claim": "The candidate is valid.",
                        "evidence": ["E2"],
                    }
                ],
                "changed_files": ["src/sample.py"],
                "unresolved": [],
            },
            "evidence": [
                {
                    "evidence_id": "E2",
                    "run_id": "RUN-105-001",
                    "subject_sha": "candidate_sha",
                    "type": "verification",
                    "source": {"command": "other-command"},
                    "result": {"exit_code": 0, "summary": "clean"},
                    "raw": {"path": ".git/aios/evidence/E2.log"},
                }
            ],
        },
    )
    repo3 = lineage3["repo"]
    run_id3 = lineage3["run_id"]
    candidate_sha3 = lineage3["candidate_sha"]
    env3 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id3},
        expected_state={"expected_candidate_sha": candidate_sha3},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha3}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="missing verification evidence for required command"):
        execute_ingress(env3, repo=repo3)


def test_submit_review_rejects_base_sha_ancestry_and_invalid_run_status(tmp_path):
    # 1. Candidate does not descend from RUN base_sha
    p1 = tmp_path / "case1"
    repo1, remote1, _ = setup_test_repo(p1)
    task_dir1 = repo1 / ".ai" / "tasks"
    task_dir1.mkdir(parents=True, exist_ok=True)
    (task_dir1 / "TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    git(repo1, "add", ".")
    git(repo1, "commit", "--quiet", "-m", "add TASK-105")
    git(repo1, "push", "--quiet", "origin", "main")

    # Create orphaned commit on unrelated branch to use as RUN base_sha
    git(repo1, "checkout", "--orphan", "unrelated-branch")
    git(repo1, "rm", "-rf", ".")
    (repo1 / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    git(repo1, "add", "unrelated.txt")
    git(repo1, "commit", "--quiet", "-m", "unrelated commit")
    unrelated_base = git(repo1, "rev-parse", "HEAD")
    git(repo1, "checkout", "main")

    sample_path1 = repo1 / "src" / "sample.py"
    sample_path1.parent.mkdir(parents=True, exist_ok=True)
    sample_path1.write_text("def sample(): return True\n", encoding="utf-8")
    git(repo1, "add", "src/sample.py")
    git(repo1, "commit", "--quiet", "-m", "candidate implementation")
    candidate_sha1 = git(repo1, "rev-parse", "HEAD")

    run_id1 = "RUN-105-001"
    state1 = p1 / "state"
    state1.mkdir(parents=True, exist_ok=True)
    run_path1 = state1 / "run.json"
    result_path1 = state1 / "result.json"

    run_payload1 = {
        "run_id": run_id1,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": unrelated_base,
        "workspace": str(repo1),
        "head_sha": None,
        "status": "ACTIVE",
    }
    result_payload1 = {
        "result": {
            "head_sha": candidate_sha1,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "The candidate is valid.",
                    "evidence": ["E1"],
                }
            ],
            "changed_files": ["src/sample.py"],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id1,
                "subject_sha": candidate_sha1,
                "type": "verification",
                "source": {"command": "git diff --check"},
                "result": {"exit_code": 0, "summary": "clean"},
                "raw": {"path": ".git/aios/evidence/E1.log"},
            }
        ],
    }
    run_path1.write_text(json.dumps(run_payload1), encoding="utf-8")
    result_path1.write_text(json.dumps(result_payload1), encoding="utf-8")
    transport_post_pass(
        repo1,
        run_id=run_id1,
        head_sha=candidate_sha1,
        run_path=run_path1,
        result_path=result_path1,
    )

    env1 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id1},
        expected_state={"expected_candidate_sha": candidate_sha1},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha1}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="does not descend from RUN base_sha"):
        execute_ingress(env1, repo=repo1)

    # 2. RUN status is not ACTIVE
    p2 = tmp_path / "case2"
    lineage2 = setup_candidate_lineage(p2, run_override={"status": "FAILED"})
    repo2 = lineage2["repo"]
    run_id2 = lineage2["run_id"]
    candidate_sha2 = lineage2["candidate_sha"]
    env2 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id2},
        expected_state={"expected_candidate_sha": candidate_sha2},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha2}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="canonical successful RUN status is invalid"):
        execute_ingress(env2, repo=repo2)

    # 3. Invalid executor in RUN
    p3 = tmp_path / "case3"
    lineage3 = setup_candidate_lineage(p3, run_override={"executor": "unsupported_executor"})
    repo3 = lineage3["repo"]
    run_id3 = lineage3["run_id"]
    candidate_sha3 = lineage3["candidate_sha"]
    env3 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id3},
        expected_state={"expected_candidate_sha": candidate_sha3},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha3}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="invalid canonical RUN"):
        execute_ingress(env3, repo=repo3)


# ===========================================================================
# AC6: AUTHOR_REMEDIATION and AUTHOR_REPAIR
# ===========================================================================

def test_author_remediation_success_and_rejections(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    candidate_sha = lineage["candidate_sha"]

    # First submit CHANGES_REQUIRED review with two findings F1 and F2
    cr_review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Defect found.
    expected: Fix defect.
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Another defect found.
    expected: Fix another defect.
"""
    review_result = execute_ingress(
        IngressEnvelope(
            format="AIOS_INGRESS_ENVELOPE",
            version=1,
            operation="SUBMIT_REVIEW",
            identity={"run_id": run_id},
            expected_state={"expected_candidate_sha": candidate_sha},
            payload=cr_review,
        ),
        repo=repo,
    )

    remediation_payload = f"""\
finding_id: F1
action: CODE_FIX
reviewed_sha: {candidate_sha}
modification_scope:
  - src/sample.py
affected_verification:
  - git diff --check
constraints:
  hard:
    - Bounded mutation authority only.
"""
    rem_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F1"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=remediation_payload,
    )
    result = execute_ingress(rem_env, repo=repo)
    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == f"refs/heads/aios/remediation/{run_id}-F1"
    assert_exact_metadata_delta(
        repo,
        result.canonical_sha,
        review_result.canonical_sha,
        f".ai/remediations/REMEDIATION-{run_id}-F1.yaml",
        remediation_payload.encode("utf-8"),
    )
    assert git(
        repo,
        "rev-parse",
        f"{result.canonical_sha}:.ai/reviews/REVIEW-105-001.yaml",
    ) == git(
        repo,
        "rev-parse",
        f"{review_result.canonical_sha}:.ai/reviews/REVIEW-105-001.yaml",
    )

    # Idempotent replay
    replay = execute_ingress(rem_env, repo=repo)
    assert replay.status == "IDEMPOTENT"
    assert replay.replayed is True

    # Conflicting replay for F1 fails closed
    conflict_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F1"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=remediation_payload.replace("Bounded mutation authority only.", "Different constraint."),
    )
    with pytest.raises(AuthoringIngressError, match="conflicting canonical remediation"):
        execute_ingress(conflict_env, repo=repo)

    # Rejection: unknown finding
    bad_finding_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F999"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=remediation_payload.replace("finding_id: F1", "finding_id: F999"),
    )
    with pytest.raises(AuthoringIngressError, match="not found in source review"):
        execute_ingress(bad_finding_env, repo=repo)

    # Rejection: scope widens task scope (tested on uncanonicalized finding F2)
    wide_scope_payload = f"""\
finding_id: F2
action: CODE_FIX
reviewed_sha: {candidate_sha}
modification_scope:
  - src/sample.py
  - outside.txt
affected_verification:
  - git diff --check
constraints:
  hard:
    - Bounded mutation authority only.
"""
    wide_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F2"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=wide_scope_payload,
    )
    with pytest.raises(AuthoringIngressError, match="widens TASK.scope.modify"):
        execute_ingress(wide_env, repo=repo)

    # Malformed historical content remains readable, but replay fails without overwrite.
    remediation_ref = f"refs/heads/aios/remediation/{run_id}-F1"
    remediation_tree = git(repo, "rev-parse", f"{result.canonical_sha}^{{tree}}")
    malformed_sha = git(
        repo,
        "commit-tree",
        remediation_tree,
        "-p",
        candidate_sha,
        "-m",
        "malformed remediation",
    )
    git(repo, "push", "--quiet", "--force", "origin", f"{malformed_sha}:{remediation_ref}")
    with pytest.raises(AuthoringIngressError, match="conflicting canonical remediation"):
        execute_ingress(rem_env, repo=repo)
    assert git(repo, "ls-remote", "--refs", "origin", remediation_ref).split()[0] == malformed_sha
    assert git(
        repo,
        "show",
        f"{malformed_sha}:.ai/remediations/REMEDIATION-{run_id}-F1.yaml",
    )


def test_author_remediation_rejects_already_resolved_finding(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    candidate_sha = lineage["candidate_sha"]

    # Submit CHANGES_REQUIRED review with finding F1
    cr_review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Defect found.
    expected: Fix defect.
"""
    execute_ingress(
        IngressEnvelope(
            format="AIOS_INGRESS_ENVELOPE",
            version=1,
            operation="SUBMIT_REVIEW",
            identity={"run_id": run_id},
            expected_state={"expected_candidate_sha": candidate_sha},
            payload=cr_review,
        ),
        repo=repo,
    )

    remediation_payload = f"""\
finding_id: F1
action: CODE_FIX
reviewed_sha: {candidate_sha}
modification_scope:
  - src/sample.py
affected_verification:
  - git diff --check
constraints:
  hard:
    - Bounded mutation authority only.
"""
    rem_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F1"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=remediation_payload,
    )

    # Advance canonical main past reviewed candidate (simulating publication of later lineage)
    sample_path = repo / "src" / "sample.py"
    sample_path.write_text("def sample(): return 'resolved'\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "resolve finding on main")
    advanced_main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/heads/main", advanced_main_sha)
    git(repo, "push", "--quiet", "origin", "main")

    # Rejection: finding is no longer outstanding after canonical main advanced past reviewed_sha
    with pytest.raises(
        AuthoringIngressError,
        match="already resolved, superseded, or otherwise no longer outstanding",
    ):
        execute_ingress(rem_env, repo=repo)

    # Verify no remediation ref was created
    rem_ref = f"refs/heads/aios/remediation/{run_id}-F1"
    output = git(repo, "ls-remote", "--refs", "origin", rem_ref)
    assert not output.strip()


def test_author_repair_success_and_rejections(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# broken\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "failed head candidate")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    failed_run_id = "RUN-105-001"
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)

    run_payload = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": failed_head_sha,
        "status": "ACTIVE",
    }
    failure_payload = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }

    run_path = state / "run.json"
    failure_path = state / "failure.json"
    run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    failure_path.write_text(json.dumps(failure_payload), encoding="utf-8")

    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failed_head_sha,
        run_path=run_path,
        failure_path=failure_path,
    )

    repair_payload = {
        "repair_id": "REPAIR-105-001",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Fix the broken implementation."],
        "constraints": ["Bounded mutation authority only."],
    }
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REPAIR",
        identity={"failed_run_id": failed_run_id},
        expected_state={"expected_failed_head_sha": failed_head_sha},
        payload=repair_payload,
    )
    result = execute_ingress(envelope, repo=repo)
    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == f"refs/heads/aios/repair/{failed_run_id}"
    assert_exact_metadata_delta(
        repo,
        result.canonical_sha,
        failed_head_sha,
        ".ai/transport/repair.json",
        json.dumps(
            repair_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
    )

    # Idempotent replay
    replay = execute_ingress(envelope, repo=repo)
    assert replay.status == "IDEMPOTENT"
    assert replay.replayed is True

    # Stale expected_failed_head_sha rejection
    stale_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REPAIR",
        identity={"failed_run_id": failed_run_id},
        expected_state={"expected_failed_head_sha": "0" * 40},
        payload=repair_payload,
    )
    with pytest.raises(AuthoringIngressError, match="expected failed head SHA mismatch"):
        execute_ingress(stale_env, repo=repo)

    # A malformed existing repair remains readable and cannot be certified by replay.
    repair_ref = f"refs/heads/aios/repair/{failed_run_id}"
    repair_tree = git(repo, "rev-parse", f"{result.canonical_sha}^{{tree}}")
    malformed_sha = git(
        repo,
        "commit-tree",
        repair_tree,
        "-p",
        base_sha,
        "-m",
        "malformed repair",
    )
    git(repo, "push", "--quiet", "--force", "origin", f"{malformed_sha}:{repair_ref}")
    with pytest.raises(AuthoringIngressError, match="conflicting canonical repair"):
        execute_ingress(envelope, repo=repo)
    assert git(repo, "ls-remote", "--refs", "origin", repair_ref).split()[0] == malformed_sha
    assert git(repo, "show", f"{malformed_sha}:.ai/transport/repair.json")


def test_author_repair_immutable_supersession_resolves_one_current_tip(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    (repo / ".ai" / "tasks").mkdir(parents=True, exist_ok=True)
    (repo / ".ai" / "tasks" / "TASK-105.yaml").write_text(
        TASK_105_SOURCE, encoding="utf-8"
    )
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# candidate\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "failed candidate")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    failed_run_id = "RUN-105-004"
    state = tmp_path / "supersession-state"
    state.mkdir()
    run = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": failed_head_sha,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "phase": "EXECUTION",
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }
    run_path = state / "run.json"
    failure_path = state / "failure.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failed_head_sha,
        run_path=run_path,
        failure_path=failure_path,
    )
    failure_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    failure_sha = git(repo, "ls-remote", "--refs", "origin", failure_ref).split()[0]

    predecessor = {
        "repair_id": "REPAIR-105-004",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Continue the interrupted implementation."],
        "constraints": ["Bounded mutation authority only."],
    }
    first = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {"expected_failed_head_sha": failed_head_sha},
            predecessor,
        ),
        repo=repo,
    )
    successor = {
        **predecessor,
        "action": "FINALIZE_CANDIDATE",
        "modification_scope": [],
        "instructions": ["Finalize the exact existing candidate without mutation."],
    }
    superseding_envelope = IngressEnvelope(
        "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
        {"failed_run_id": failed_run_id},
        {
            "expected_failed_head_sha": failed_head_sha,
            "expected_current_repair_sha": first.canonical_sha,
            "expected_failure_artifacts_sha": failure_sha,
        },
        successor,
    )
    second = execute_ingress(superseding_envelope, repo=repo)
    current = resolve_remote_repair_authorization(repo, failed_run_id)

    assert second.canonical_destination.endswith(f"/{failed_run_id}/2")
    assert current.commit_sha == second.canonical_sha
    assert current.revision == 2
    assert current.predecessor_sha == first.canonical_sha
    assert json.loads(current.repair) == successor
    lifecycle = resolve_remote_task_lifecycle(
        repo, task_id="TASK-105", task_revision=1
    )
    assert lifecycle.repair_selectors == (
        (failed_run_id, second.canonical_sha, current.repair),
    )
    assert git(
        repo, "ls-remote", "--refs", "origin",
        f"refs/heads/aios/repair/{failed_run_id}",
    ).split()[0] == first.canonical_sha
    replay = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {
                "expected_failed_head_sha": failed_head_sha,
                "expected_current_repair_sha": second.canonical_sha,
                "expected_failure_artifacts_sha": failure_sha,
            },
            successor,
        ),
        repo=repo,
    )
    assert replay.status == "IDEMPOTENT"
    assert replay.canonical_sha == second.canonical_sha

    stale = {**successor, "instructions": ["Different explicit intent."]}
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                superseding_envelope.expected_state,
                stale,
            ),
            repo=repo,
        )

    continuation = tmp_path / "continuation-author"
    git(tmp_path, "clone", "--quiet", str(remote), str(continuation))
    git(continuation, "switch", "--quiet", "main")
    git(continuation, "config", "user.name", "AIOS Test")
    git(continuation, "config", "user.email", "test@example.invalid")
    (continuation / ".ai" / "transport").mkdir(parents=True, exist_ok=True)
    (continuation / ".ai" / "transport" / "repair.json").write_text(
        json.dumps({"failed_run_id": failed_run_id}), encoding="utf-8"
    )
    git(continuation, "add", ".ai/transport/repair.json")
    git(continuation, "commit", "--quiet", "-m", "admitted continuation")
    git(
        continuation,
        "push",
        "--quiet",
        "origin",
        "HEAD:refs/heads/aios/artifacts/RUN-105-005",
    )
    with pytest.raises(AuthoringIngressError, match="continuation already exists"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": second.canonical_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                stale,
            ),
            repo=repo,
        )

    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"{second.canonical_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/4",
    )
    with pytest.raises(ReviewTransportError, match="identity|discontinuous"):
        resolve_remote_repair_authorization(repo, failed_run_id)


# ===========================================================================
# AC7 & AC8: Concurrency, idempotency, destination derivation, authority separation
# ===========================================================================

def test_authority_separation_and_destination_derivation(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)

    # Brain cannot specify custom destination path
    with pytest.raises(AuthoringIngressError, match="prohibited field detected"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "destination_path": "arbitrary/path.yaml",
            "expected_state": {"expected_main_sha": base_sha},
            "payload": TASK_105_SOURCE,
        })

    # Brain cannot inject git command
    with pytest.raises(AuthoringIngressError, match="prohibited field detected"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "git_command": "git reset --hard",
            "expected_state": {"expected_main_sha": base_sha},
            "payload": TASK_105_SOURCE,
        })


def test_author_repair_multi_generation_supersession_production_topology_ac1_to_ac7(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    (repo / ".ai" / "tasks").mkdir(parents=True, exist_ok=True)
    (repo / ".ai" / "tasks" / "TASK-121.yaml").write_text(
        """task_id: TASK-121
revision: 1
goal: Repair interrupted implementation.
problem: Production failed candidate needs repair.
assumptions: []
scope:
  inspect: []
  modify: [src/sample.py]
non_goals: []
constraints:
  hard:
    - Bounded mutation authority only.
acceptance:
  - id: AC1
    condition: Candidate is repaired.
verification:
  required:
    - git diff --check
""",
        encoding="utf-8",
    )
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# initial implementation\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "failed candidate for RUN-121-004")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    failed_run_id = "RUN-121-004"
    state = tmp_path / "supersession-state"
    state.mkdir()
    run = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-121", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": failed_head_sha,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-121", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "phase": "EXECUTION",
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }
    run_path = state / "run.json"
    failure_path = state / "failure.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failed_head_sha,
        run_path=run_path,
        failure_path=failure_path,
    )
    failure_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    failure_sha = git(repo, "ls-remote", "--refs", "origin", failure_ref).split()[0]

    # --- Revision 1: Legacy CONTINUE_IMPLEMENTATION ---
    rev1_payload = {
        "repair_id": "REPAIR-121-004",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-121", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Continue implementation from interrupted candidate."],
        "constraints": ["Bounded mutation authority only."],
    }
    rev1_res = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {"expected_failed_head_sha": failed_head_sha},
            rev1_payload,
        ),
        repo=repo,
    )
    assert rev1_res.status == "CANONICALIZED"
    assert rev1_res.canonical_destination == f"refs/heads/aios/repair/{failed_run_id}"
    rev1_sha = rev1_res.canonical_sha

    current_r1 = resolve_remote_repair_authorization(repo, failed_run_id)
    assert current_r1.revision == 1
    assert current_r1.commit_sha == rev1_sha

    # --- Revision 2: First supersession (CONTINUE_IMPLEMENTATION -> FINALIZE_CANDIDATE) ---
    rev2_payload = {
        **rev1_payload,
        "action": "FINALIZE_CANDIDATE",
        "modification_scope": [],
        "instructions": ["Finalize existing candidate without further mutation."],
    }
    rev2_res = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {
                "expected_failed_head_sha": failed_head_sha,
                "expected_current_repair_sha": rev1_sha,
                "expected_failure_artifacts_sha": failure_sha,
            },
            rev2_payload,
        ),
        repo=repo,
    )
    assert rev2_res.status == "CANONICALIZED"
    assert rev2_res.canonical_destination == f"refs/heads/aios/repair-supersession/{failed_run_id}/2"
    rev2_sha = rev2_res.canonical_sha

    # Verify revision 1 immutable record was preserved and not rewritten
    assert git(repo, "ls-remote", "--refs", "origin", f"refs/heads/aios/repair/{failed_run_id}").split()[0] == rev1_sha
    current_r2 = resolve_remote_repair_authorization(repo, failed_run_id)
    assert current_r2.revision == 2
    assert current_r2.commit_sha == rev2_sha
    assert current_r2.predecessor_sha == rev1_sha

    # Negative check AC3: Stale revision-1 selection after revision 2 exists fails closed
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev1_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                {**rev2_payload, "instructions": ["Stale revision 1 intent."]},
            ),
            repo=repo,
        )

    # --- Revision 3: Second supersession (AC1: extending revision 2 to revision 3 without collision) ---
    rev3_payload = {
        **rev1_payload,
        "action": "FINALIZE_CANDIDATE",
        "modification_scope": [],
        "instructions": ["Finalize candidate with refined recovery intent."],
    }
    rev3_res = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {
                "expected_failed_head_sha": failed_head_sha,
                "expected_current_repair_sha": rev2_sha,
                "expected_failure_artifacts_sha": failure_sha,
            },
            rev3_payload,
        ),
        repo=repo,
    )
    assert rev3_res.status == "CANONICALIZED"
    assert rev3_res.canonical_destination == f"refs/heads/aios/repair-supersession/{failed_run_id}/3"
    rev3_sha = rev3_res.canonical_sha

    # --- AC2: Verify all 3 immutable records preserved, bindings intact, revision 3 sole current tip ---
    assert git(repo, "ls-remote", "--refs", "origin", f"refs/heads/aios/repair/{failed_run_id}").split()[0] == rev1_sha
    assert git(repo, "ls-remote", "--refs", "origin", f"refs/heads/aios/repair-supersession/{failed_run_id}/2").split()[0] == rev2_sha
    assert git(repo, "ls-remote", "--refs", "origin", f"refs/heads/aios/repair-supersession/{failed_run_id}/3").split()[0] == rev3_sha

    # Verify commit ancestry chain
    assert git(repo, "rev-parse", f"{rev3_sha}^") == rev2_sha
    assert git(repo, "rev-parse", f"{rev2_sha}^") == rev1_sha
    assert git(repo, "rev-parse", f"{rev1_sha}^") == failed_head_sha

    # Verify deterministic current tip resolution
    current_r3 = resolve_remote_repair_authorization(repo, failed_run_id)
    assert current_r3.revision == 3
    assert current_r3.commit_sha == rev3_sha
    assert current_r3.predecessor_sha == rev2_sha
    assert current_r3.failure_artifacts_sha == failure_sha
    assert json.loads(current_r3.repair) == rev3_payload

    lifecycle = resolve_remote_task_lifecycle(repo, task_id="TASK-121", task_revision=1)
    assert lifecycle.repair_selectors == ((failed_run_id, rev3_sha, current_r3.repair),)

    # --- AC6: Idempotent replay of exact current revision 3 ---
    replay_r3 = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {
                "expected_failed_head_sha": failed_head_sha,
                "expected_current_repair_sha": rev3_sha,
                "expected_failure_artifacts_sha": failure_sha,
            },
            rev3_payload,
        ),
        repo=repo,
    )
    assert replay_r3.status == "IDEMPOTENT"
    assert replay_r3.canonical_sha == rev3_sha

    # --- AC3: Stale selector rejection after revision 3 exists ---
    # Attempting to supersede using revision 1 SHA
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev1_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                {**rev3_payload, "instructions": ["Stale revision 1 intent."]},
            ),
            repo=repo,
        )

    # Attempting to supersede using revision 2 SHA
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev2_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                {**rev3_payload, "instructions": ["Stale revision 2 intent."]},
            ),
            repo=repo,
        )

    # Replaying superseded revision 2 fails closed
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev2_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                rev2_payload,
            ),
            repo=repo,
        )

    # --- AC4: Operator / Preflight / Execution binding binds only revision 3 ---
    from aios_renew.operator import OperatorError, preflight_repair, run_repair

    # Stale revision 1 selector fails closed in preflight
    pf_r1 = preflight_repair(failed_run_id, repo=repo, required_repair_sha=rev1_sha)
    assert pf_r1.status == "BLOCKED"
    assert pf_r1.reason_code == "CANONICAL_LINEAGE_INVALID"

    # Stale revision 2 selector fails closed in preflight
    pf_r2 = preflight_repair(failed_run_id, repo=repo, required_repair_sha=rev2_sha)
    assert pf_r2.status == "BLOCKED"
    assert pf_r2.reason_code == "CANONICAL_LINEAGE_INVALID"

    # Exact current revision 3 succeeds in preflight
    pf_r3 = preflight_repair(failed_run_id, repo=repo, required_repair_sha=rev3_sha)
    assert pf_r3.status == "READY"
    assert pf_r3.authorization_sha == rev3_sha

    # Direct run_repair execution fails closed on stale revision 1 or revision 2 selector
    with pytest.raises(OperatorError, match="canonical REPAIR selector changed after observation"):
        run_repair(failed_run_id=failed_run_id, repo=repo, required_repair_sha=rev1_sha, executor="codex")

    with pytest.raises(OperatorError, match="canonical REPAIR selector changed after observation"):
        run_repair(failed_run_id=failed_run_id, repo=repo, required_repair_sha=rev2_sha, executor="codex")

    # --- AC3: Negative lineage cases ---
    # Case A: Revision discontinuity (gap: commit with revision 5 pushed to /5, skipping revision 4)
    gap_meta = {
        "format": "AIOS_REPAIR_SUPERSESSION",
        "version": 1,
        "failed_run_id": failed_run_id,
        "authorization_revision": 5,
        "predecessor_repair_sha": rev3_sha,
        "failure_artifacts_sha": failure_sha,
    }
    git(repo, "checkout", "--quiet", "--detach", rev3_sha)
    (repo / ".ai" / "transport" / "repair-supersession.json").write_text(json.dumps(gap_meta), encoding="utf-8")
    git(repo, "add", ".ai/transport/repair-supersession.json")
    git(repo, "commit", "--quiet", "-m", "discontinuous revision 5")
    gap_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", f"{gap_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/5")
    git(repo, "checkout", "--quiet", "main")
    with pytest.raises(ReviewTransportError, match="discontinuous"):
        resolve_remote_repair_authorization(repo, failed_run_id, remote=str(remote))
    git(repo, "push", "--quiet", "origin", f":refs/heads/aios/repair-supersession/{failed_run_id}/5")

    # Case B: Competing successor / ambiguous selector
    git(repo, "push", "--quiet", "origin", f"{rev3_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/03")
    with pytest.raises(ReviewTransportError, match="ambiguous|malformed|identity"):
        resolve_remote_repair_authorization(repo, failed_run_id, remote=str(remote))
    git(repo, "push", "--quiet", "origin", f":refs/heads/aios/repair-supersession/{failed_run_id}/03")

    # Case C: Broken predecessor binding in successor metadata
    broken_meta = {
        "format": "AIOS_REPAIR_SUPERSESSION",
        "version": 1,
        "failed_run_id": failed_run_id,
        "authorization_revision": 4,
        "predecessor_repair_sha": rev1_sha,
        "failure_artifacts_sha": failure_sha,
    }
    git(repo, "checkout", "--quiet", "--detach", rev1_sha)
    (repo / ".ai" / "transport" / "repair-supersession.json").write_text(json.dumps(broken_meta), encoding="utf-8")
    git(repo, "add", ".ai/transport/repair-supersession.json")
    git(repo, "commit", "--quiet", "-m", "broken revision 4")
    broken_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", f"{broken_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/4")
    git(repo, "checkout", "--quiet", "main")
    with pytest.raises(ReviewTransportError, match="predecessor chain is broken"):
        resolve_remote_repair_authorization(repo, failed_run_id, remote=str(remote))
    git(repo, "push", "--quiet", "origin", f":refs/heads/aios/repair-supersession/{failed_run_id}/4")

    # Case D: Stale canonical FAILURE identity
    stale_failure_meta = {
        "format": "AIOS_REPAIR_SUPERSESSION",
        "version": 1,
        "failed_run_id": failed_run_id,
        "authorization_revision": 4,
        "predecessor_repair_sha": rev3_sha,
        "failure_artifacts_sha": "0" * 40,
    }
    git(repo, "checkout", "--quiet", "--detach", rev3_sha)
    (repo / ".ai" / "transport" / "repair-supersession.json").write_text(json.dumps(stale_failure_meta), encoding="utf-8")
    git(repo, "add", ".ai/transport/repair-supersession.json")
    git(repo, "commit", "--quiet", "-m", "stale failure revision 4")
    stale_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", f"{stale_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/4")
    git(repo, "checkout", "--quiet", "main")
    with pytest.raises(ReviewTransportError, match="canonical REPAIR FAILURE identity is stale"):
        resolve_remote_repair_authorization(repo, failed_run_id, remote=str(remote))
    git(repo, "push", "--quiet", "origin", f":refs/heads/aios/repair-supersession/{failed_run_id}/4")

    # --- Continuation admission refusal ---
    continuation = tmp_path / "continuation-worker"
    git(tmp_path, "clone", "--quiet", str(remote), str(continuation))
    git(continuation, "switch", "--quiet", "main")
    git(continuation, "config", "user.name", "AIOS Test")
    git(continuation, "config", "user.email", "test@example.invalid")
    (continuation / ".ai" / "transport").mkdir(parents=True, exist_ok=True)
    (continuation / ".ai" / "transport" / "repair.json").write_text(
        json.dumps({"failed_run_id": failed_run_id}), encoding="utf-8"
    )
    git(continuation, "add", ".ai/transport/repair.json")
    git(continuation, "commit", "--quiet", "-m", "continuation admitted")
    git(continuation, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/artifacts/RUN-121-005")

    with pytest.raises(AuthoringIngressError, match="continuation already exists"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev3_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                {**rev3_payload, "instructions": ["Attempting supersession after continuation."]},
            ),
            repo=repo,
        )


def test_author_repair_failed_remediation_production_topology_ac1_to_ac6(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# original sample\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base commit with task and sample")
    base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    source_run_id = "RUN-105-001"
    review_id = "REVIEW-105-001"
    finding_id = "F1"
    remediation_run_id = "RUN-105-002"

    # Failed remediation candidate commit
    (repo / "src" / "sample.py").write_text("# broken remediation attempt\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "failed remediation head candidate")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    state = tmp_path / "remediation_state"
    state.mkdir(parents=True, exist_ok=True)

    embedded_run = {
        "run_id": remediation_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    execution_record = {
        "review_id": review_id,
        "finding": {
            "id": finding_id,
            "basis": "AC1",
            "action": "CODE_FIX",
            "location": "src/sample.py",
            "issue": "Remediation needed for sample.",
            "expected": "Corrected sample.",
        },
        "remediation": {
            "finding_id": finding_id,
            "action": "CODE_FIX",
            "reviewed_sha": base_sha,
            "modification_scope": ["src/sample.py"],
            "affected_verification": ["git diff --check"],
            "constraints": ["Bounded mutation authority only."],
        },
        "run": embedded_run,
        "original_constraints": ["Bounded mutation authority only."],
    }
    predecessor_record = {
        "source_run_id": source_run_id,
        "review_id": review_id,
        "finding_id": finding_id,
        "reviewed_sha": base_sha,
    }
    remediation_run_payload = {
        "kind": "REMEDIATION",
        "predecessor": predecessor_record,
        "execution": execution_record,
    }
    failure_payload = {
        "kind": "FAILURE",
        "run_id": remediation_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }

    run_path = state / "run.json"
    failure_path = state / "failure.json"
    run_path.write_text(json.dumps(remediation_run_payload), encoding="utf-8")
    failure_path.write_text(json.dumps(failure_payload), encoding="utf-8")

    transport_failure(
        repo,
        run_id=remediation_run_id,
        head_sha=failed_head_sha,
        run_path=run_path,
        failure_path=failure_path,
    )

    repair_payload = {
        "repair_id": "REPAIR-105-002",
        "failed_run_id": remediation_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Fix the broken remediation candidate."],
        "constraints": ["Bounded mutation authority only."],
    }
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REPAIR",
        identity={"failed_run_id": remediation_run_id},
        expected_state={"expected_failed_head_sha": failed_head_sha},
        payload=repair_payload,
    )

    # AC1 & AC2: Author repair succeeds for failed REMEDIATION, removing the historical blocker
    result = execute_ingress(envelope, repo=repo)
    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == f"refs/heads/aios/repair/{remediation_run_id}"
    assert_exact_metadata_delta(
        repo,
        result.canonical_sha,
        failed_head_sha,
        ".ai/transport/repair.json",
        json.dumps(
            repair_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
    )

    # Idempotent replay
    replay = execute_ingress(envelope, repo=repo)
    assert replay.status == "IDEMPOTENT"
    assert replay.replayed is True

    # Lineage preservation: failure-artifacts ref preserves exact REMEDIATION wrapper and FAILURE identity
    artifacts_ref = f"refs/heads/aios/failure-artifacts/{remediation_run_id}"
    artifacts_sha = git(repo, "ls-remote", "--refs", "origin", artifacts_ref).split()[0]
    preserved_run_raw = git(repo, "show", f"{artifacts_sha}:.ai/transport/run.json")
    preserved_run = json.loads(preserved_run_raw)
    assert preserved_run["kind"] == "REMEDIATION"
    assert preserved_run["predecessor"]["source_run_id"] == source_run_id
    assert preserved_run["predecessor"]["finding_id"] == finding_id
    assert preserved_run["execution"]["run"]["run_id"] == remediation_run_id
    assert preserved_run["execution"]["run"]["base_sha"] == base_sha

    # AC4: Multi-generation supersession on failed remediation (Revision 2)
    rev2_payload = {
        **repair_payload,
        "repair_id": "REPAIR-105-002-R2",
        "instructions": ["Updated instructions for revision 2."],
    }
    rev2_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REPAIR",
        identity={"failed_run_id": remediation_run_id},
        expected_state={
            "expected_failed_head_sha": failed_head_sha,
            "expected_current_repair_sha": result.canonical_sha,
            "expected_failure_artifacts_sha": artifacts_sha,
        },
        payload=rev2_payload,
    )
    rev2_res = execute_ingress(rev2_env, repo=repo)
    assert rev2_res.status == "CANONICALIZED"
    assert rev2_res.canonical_destination == f"refs/heads/aios/repair-supersession/{remediation_run_id}/2"

    current = resolve_remote_repair_authorization(repo, remediation_run_id)
    assert current.revision == 2
    assert current.commit_sha == rev2_res.canonical_sha


def test_author_repair_failed_remediation_malformed_negatives(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# original\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base commit")
    base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    (repo / "src" / "sample.py").write_text("# failed\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "failed candidate")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    state = tmp_path / "neg_state"
    state.mkdir(parents=True, exist_ok=True)
    run_path = state / "run.json"
    failure_path = state / "failure.json"

    def build_failure(run_id: str, *, failure_base: str = base_sha, fail_task: dict | None = None) -> dict:
        payload = {
            "kind": "FAILURE",
            "run_id": run_id,
            "task": fail_task or {"id": "TASK-105", "revision": 1},
            "executor": "antigravity",
            "base_sha": failure_base,
            "failed_head_sha": failed_head_sha,
            "candidate": {
                "transportable": True,
                "repairable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": ["src/sample.py"],
                "outside_task_scope": [],
            },
        }
        return payload

    repair_payload = {
        "repair_id": "REPAIR-105-001",
        "failed_run_id": "RUN-105-NEG",
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Fix."],
        "constraints": ["Bounded."],
    }

    def try_repair(run_doc: dict, fail_doc: dict, run_id: str = "RUN-105-NEG") -> None:
        run_path.write_text(json.dumps(run_doc), encoding="utf-8")
        failure_path.write_text(json.dumps(fail_doc), encoding="utf-8")
        transport_failure(repo, run_id=run_id, head_sha=failed_head_sha, run_path=run_path, failure_path=failure_path)
        payload = {**repair_payload, "failed_run_id": run_id}
        env = IngressEnvelope(
            format="AIOS_INGRESS_ENVELOPE",
            version=1,
            operation="AUTHOR_REPAIR",
            identity={"failed_run_id": run_id},
            expected_state={"expected_failed_head_sha": failed_head_sha},
            payload=payload,
        )
        execute_ingress(env, repo=repo)

    # 1. Missing execution in REMEDIATION wrapper
    with pytest.raises(AuthoringIngressError, match="canonical REMEDIATION execution must be a mapping"):
        try_repair({"kind": "REMEDIATION"}, build_failure("RUN-105-N01"), "RUN-105-N01")

    # 2. Non-mapping execution in REMEDIATION wrapper
    with pytest.raises(AuthoringIngressError, match="canonical REMEDIATION execution must be a mapping"):
        try_repair({"kind": "REMEDIATION", "execution": "not-a-map"}, build_failure("RUN-105-N02"), "RUN-105-N02")

    # 3. Missing run in REMEDIATION.execution
    with pytest.raises(AuthoringIngressError, match="canonical REMEDIATION execution.run must be a mapping"):
        try_repair({"kind": "REMEDIATION", "execution": {}}, build_failure("RUN-105-N03"), "RUN-105-N03")

    # 4. Non-mapping run in REMEDIATION.execution
    with pytest.raises(AuthoringIngressError, match="canonical REMEDIATION execution.run must be a mapping"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": 123}}, build_failure("RUN-105-N04"), "RUN-105-N04")

    # 5. REMEDIATION.execution.run run_id mismatch
    mismatch_run = {
        "run_id": "RUN-DIFFERENT",
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "status": "ACTIVE",
    }
    with pytest.raises(AuthoringIngressError, match="RUN run_id mismatch"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": mismatch_run}}, build_failure("RUN-105-N05"), "RUN-105-N05")

    # 6. Missing base_sha in REMEDIATION.execution.run
    no_base_run = {
        "run_id": "RUN-105-N06",
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "workspace": str(repo),
        "status": "ACTIVE",
    }
    with pytest.raises(AuthoringIngressError, match="RUN base_sha is invalid"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": no_base_run}}, build_failure("RUN-105-N06"), "RUN-105-N06")

    # 7. Invalid base_sha format (non-hex) in REMEDIATION.execution.run
    bad_base_run = {**no_base_run, "run_id": "RUN-105-N07", "base_sha": "not-a-valid-sha"}
    with pytest.raises(AuthoringIngressError, match="RUN base_sha is invalid"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": bad_base_run}}, build_failure("RUN-105-N07"), "RUN-105-N07")

    # 8. base_sha is not a canonical commit in git
    non_commit_run = {**no_base_run, "run_id": "RUN-105-N08", "base_sha": "0" * 40}
    with pytest.raises(AuthoringIngressError, match="RUN base_sha is not a canonical commit"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": non_commit_run}}, build_failure("RUN-105-N08", failure_base="0" * 40), "RUN-105-N08")

    # 9. Contradictory base_sha between FAILURE and RUN
    valid_base_run = {**no_base_run, "run_id": "RUN-105-N09", "base_sha": base_sha}
    with pytest.raises(AuthoringIngressError, match="FAILURE base_sha does not match RUN"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": valid_base_run}}, build_failure("RUN-105-N09", failure_base=failed_head_sha), "RUN-105-N09")

    # 10. Missing task in execution.run
    no_task_run = {
        "run_id": "RUN-105-N10",
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "status": "ACTIVE",
    }
    with pytest.raises(AuthoringIngressError, match="cannot resolve task_id from RUN"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": no_task_run}}, build_failure("RUN-105-N10"), "RUN-105-N10")

    # 11. Invalid task revision in execution.run
    bad_rev_run = {
        "run_id": "RUN-105-N11",
        "task": {"id": "TASK-105", "revision": "not-an-int"},
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "status": "ACTIVE",
    }
    with pytest.raises(AuthoringIngressError, match="cannot resolve task revision from RUN"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": bad_rev_run}}, build_failure("RUN-105-N11"), "RUN-105-N11")

    # 12. Unknown RUN kind
    with pytest.raises(AuthoringIngressError, match="unknown canonical RUN kind"):
        try_repair({"kind": "UNSUPPORTED_WRAPPER"}, build_failure("RUN-105-N12"), "RUN-105-N12")

    # Verify no repair refs were created for any failed negative case
    for n in range(1, 13):
        nid = f"RUN-105-N{n:02d}"
        ref = f"refs/heads/aios/repair/{nid}"
        output = git(repo, "ls-remote", "--refs", "origin", ref)
        assert not output.strip(), f"repair ref unexpectedly created for {nid}"
