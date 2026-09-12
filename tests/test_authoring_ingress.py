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
from aios_renew.review_transport import transport_failure, transport_post_pass
from aios_renew.unified_state import observe_unified_state


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=check,
    )
    return proc.stdout.strip()


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
) -> dict[str, object]:
    repo, remote, base_sha = setup_test_repo(root)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task_id}.yaml").write_text(task_source, encoding="utf-8")
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

    # AC4: Observable through existing Unified State path without creating RUN or invoking Executor
    runs_dir = repo / ".git" / "aios" / "runs"
    assert not runs_dir.exists() or len(list(runs_dir.glob("*.json"))) == 0

    obs = observe_unified_state(task_id, repo=repo)
    assert obs.lifecycle_state == "CORRECTION"
    assert obs.next_action == "AUTHOR_REMEDIATION"
    assert obs.finding_id == "F1"


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
    result = execute_ingress(rem_env, repo=repo)
    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == f"refs/heads/aios/remediation/{run_id}-F1"

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
