# Test-only shared support for operator test suites.
import json
from pathlib import Path
import subprocess

import aios_renew.operator as operator_module
from aios_renew.operator import runtime_paths, runtime_state_root
from aios_renew.review_transport import transport_post_pass

TASK_SOURCE = """
task_id: TASK-101
revision: 1
goal: Create one deterministic operator test output.
problem: Exercise the thin operator without a real executor.
assumptions: []
scope:
  inspect: []
  modify:
    - OUTPUT.txt
non_goals:
  - Change the frozen kernel.
constraints:
  hard:
    - Commit the output.
acceptance:
  - id: AC1
    condition: OUTPUT.txt is committed.
verification:
  required:
    - git status --porcelain
"""

MULTI_ACCEPTANCE_TASK_SOURCE = TASK_SOURCE.replace(
    "verification:\n",
    "  - id: AC2\n    condition: The second criterion is satisfied.\nverification:\n",
)

READONLY_TASK_SOURCE = TASK_SOURCE.replace(
    "  modify:\n    - OUTPUT.txt\n",
    "  modify: []\n",
)

READONLY_MULTI_ACCEPTANCE_TASK_SOURCE = MULTI_ACCEPTANCE_TASK_SOURCE.replace(
    "  modify:\n    - OUTPUT.txt\n",
    "  modify: []\n",
)

def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

def make_repo(
    root: Path,
    *,
    task_source: str | None = TASK_SOURCE,
) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "AIOS Operator Test")
    git(repo, "config", "user.email", "operator@example.invalid")
    git(repo, "branch", "-M", "main")
    (repo / "README.md").write_text("# operator test\n", encoding="utf-8")
    if task_source is not None:
        task_dir = repo / ".ai" / "tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "TASK-101.yaml").write_text(task_source, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "baseline")
    upstream = root / "upstream.git"
    subprocess.run(("git", "init", "--bare", "--quiet", str(upstream)), check=True)
    git(repo, "remote", "add", "origin", str(upstream))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "main")
    subprocess.run(
        ("git", "-C", str(upstream), "symbolic-ref", "HEAD", "refs/heads/main"),
        check=True,
    )
    return repo

def canonical_result_payload(
    run_id: str,
    head_sha: str,
    *,
    changed_files: list[str] | None = None,
) -> dict:
    """Build a canonical package valid for PRIMARY and correction terminals."""

    commands = ("git status --porcelain", "git diff --check")
    evidence = [
        {
            "evidence_id": f"E-{run_id}-{index}",
            "run_id": run_id,
            "subject_sha": head_sha,
            "type": "TEST",
            "source": {"command": command},
            "result": {"exit_code": 0, "summary": "verified"},
            "raw": {"path": f".ai/evidence/{run_id}-{index}.log"},
        }
        for index, command in enumerate(commands, start=1)
    ]
    return {
        "result": {
            "head_sha": head_sha,
            "claims": [],
            "changed_files": [] if changed_files is None else changed_files,
            "unresolved": [],
        },
        "evidence": evidence,
    }

def static_payload(
    *,
    satisfies: list[str] | None = None,
    unresolved: list[str] | None = None,
) -> dict:
    criteria = ["AC1"] if satisfies is None else satisfies
    claims = []
    evidence = []
    if criteria:
        claims.append(
            {
                "id": "C1",
                "satisfies": criteria,
                "claim": "The stated acceptance criteria are satisfied.",
                "evidence": [],
            }
        )
    return {
        "result": {
            "head_sha": "replaced-by-runner",
            "claims": claims,
            "changed_files": [],
            "unresolved": [] if unresolved is None else unresolved,
        },
        "evidence": evidence,
    }

def repair_contract(
    repo: Path, *, action: str = "CODE_FIX"
) -> tuple[str, dict]:
    state = runtime_paths(repo)
    failed_run_id = "RUN-101-000"
    failed_head = git(repo, "rev-parse", "HEAD")
    run_data = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": failed_head,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    (state.runs / f"{failed_run_id}.json").write_text(
        json.dumps(run_data), encoding="utf-8"
    )
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": failed_head,
        "failed_head_sha": failed_head,
        "candidate": {"repairable": True, "changed_files": []},
    }
    if action == "CONTINUE_IMPLEMENTATION":
        failure.update({
            "phase": "COMPLETION_GATE",
            "candidate": {
                "transportable": True,
                "repairable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        })
    (state.failures / f"{failed_run_id}.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )
    repair = {
        "repair_id": f"REPAIR-101-{action}",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": action,
        "modification_scope": (
            ["OUTPUT.txt"]
            if action in ("CODE_FIX", "CONTINUE_IMPLEMENTATION")
            else []
        ),
        "instructions": ["Apply only the authorized correction."],
        "constraints": ["Commit the output."],
    }
    return failed_run_id, repair

def publish_upstream(
    repo: Path, files: dict[str, str], message: str = "publish"
) -> str:
    publisher = repo.parent / "publisher"
    if not publisher.exists():
        subprocess.run(
            (
                "git",
                "clone",
                "--quiet",
                git(repo, "remote", "get-url", "origin"),
                str(publisher),
            ),
            check=True,
        )
    else:
        git(publisher, "pull", "--quiet")
    git(publisher, "config", "user.name", "AIOS Publisher")
    git(publisher, "config", "user.email", "publisher@example.invalid")
    for name, content in files.items():
        path = publisher / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(publisher, "add", ".")
    git(publisher, "commit", "--quiet", "-m", message)
    git(publisher, "push", "--quiet")
    return git(publisher, "rev-parse", "HEAD")

def publish_test_remediation_lineage(
    repo: Path,
    root: Path,
    *,
    source_run_id: str,
    finding_id: str,
    task_id: str = "TASK-101",
    task_revision: int = 1,
    extra_reviews: int = 0,
    skip_artifacts: bool = False,
    reviewed_sha: str | None = None,
) -> str:
    sha = reviewed_sha or git(repo, "rev-parse", "HEAD")
    state = runtime_paths(repo)
    if not skip_artifacts:
        run_file = state.runs / f"{source_run_id}.json"
        res_file = state.results / f"{source_run_id}.json"
        run_file.write_text(
            json.dumps(
                {
                    "run_id": source_run_id,
                    "task": {"id": task_id, "revision": task_revision},
                    "executor": "codex",
                    "base_sha": sha,
                    "workspace": str(repo),
                    "head_sha": None,
                    "status": "ACTIVE",
                }
            ),
            encoding="utf-8",
        )
        payload = static_payload()
        payload["result"]["head_sha"] = sha
        payload["result"]["claims"][0]["evidence"] = ["E1"]
        payload["evidence"] = [
            {
                "evidence_id": "E1",
                "run_id": source_run_id,
                "subject_sha": sha,
                "type": "TEST",
                "source": {"command": "git status --porcelain"},
                "result": {"exit_code": 0, "summary": "verified"},
                "raw": {"path": ".ai/evidence/E1.log"},
            }
        ]
        res_file.write_text(json.dumps(payload), encoding="utf-8")
        from aios_renew.review_transport import transport_post_pass

        transport_post_pass(
            repo,
            run_id=source_run_id,
            head_sha=sha,
            run_path=run_file,
            result_path=res_file,
        )
    author = root / f"author-{source_run_id}-{finding_id}"
    subprocess.run(
        ("git", "clone", "--quiet", str(root / "upstream.git"), str(author)),
        check=True,
    )
    git(author, "config", "user.name", "AIOS Reviewer Test")
    git(author, "config", "user.email", "reviewer@example.invalid")
    review_dir = author / ".ai" / "reviews"
    remediation_dir = author / ".ai" / "remediations"
    review_dir.mkdir(parents=True, exist_ok=True)
    remediation_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / f"REVIEW-{source_run_id}.yaml").write_text(
        f"""review_id: REVIEW-{source_run_id}
reviewed_sha: {sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: {finding_id}
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The output is absent.
    expected: Commit only the output.
""",
        encoding="utf-8",
    )
    for i in range(extra_reviews):
        (review_dir / f"REVIEW-{source_run_id}-extra-{i}.yaml").write_text(
            f"""review_id: REVIEW-{source_run_id}-extra-{i}
reviewed_sha: {sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {{AC1: FAIL}}
findings:
  - id: {finding_id}
    basis: AC1
    action: CODE_FIX
    location: OUTPUT.txt
    issue: The output is absent.
    expected: Commit only the output.
""",
            encoding="utf-8",
        )
    (remediation_dir / f"REMEDIATION-{source_run_id}-{finding_id}.yaml").write_text(
        f"""finding_id: {finding_id}
action: CODE_FIX
reviewed_sha: {sha}
modification_scope: [OUTPUT.txt]
affected_verification: [git diff --check]
constraints:
  hard: [Commit the output.]
""",
        encoding="utf-8",
    )
    git(author, "add", ".ai")
    git(author, "commit", "--quiet", "-m", "canonical review and remediation")
    ref = f"refs/heads/aios/remediation/{source_run_id}-{finding_id}"
    git(author, "push", "--quiet", "origin", f"HEAD:{ref}")
    return ref

def _runtime_bytes(repo: Path) -> dict[str, bytes]:
    root = runtime_state_root(repo)
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
