"""Bounded workflow authority and actual comment-poster replay behavior."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from aios_renew import brain_wake_bridge as bridge

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/aios-brain-wake-bridge.yml"


def workflow():
    # BaseLoader preserves GitHub's "on" key under YAML 1.1.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_workflow_uses_only_completed_handoffs_and_minimum_permissions():
    data = workflow()
    assert set(data["on"]) == {"workflow_run"}
    assert data["on"]["workflow_run"]["types"] == ["completed"]
    assert set(data["on"]["workflow_run"]["workflows"]) == {v["name"] for v in (bridge.WORKFLOWS | bridge.SOURCE_WORKFLOWS).values()}
    assert data["permissions"] == {"contents": "read", "actions": "read", "pull-requests": "write"}
    ring = data["jobs"]["ring"]
    assert ring["permissions"] == {"pull-requests": "write"}
    assert ring["concurrency"]["cancel-in-progress"] == "false"
    assert "needs.project.outputs.event_id" in ring["concurrency"]["group"]
    job = data["jobs"]["project"]
    assert job["permissions"] == {"contents": "read", "actions": "read", "pull-requests": "read"}
    assert job["if"] == "github.repository == 'trung-via/AIOS-renew'"
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["persist-credentials"] == "false"
    source = WORKFLOW.read_text()
    assert "continue-on-error" not in source and "if: always()" not in source
    assert source.count("github.rest.issues.createComment") == 1
    for forbidden in ("contents: write", "actions: write", "workflow: write", "createWorkflowDispatch", "pulls.merge", "git push", "next_action"):
        assert forbidden not in source


def execute_poster(tmp_path: Path, comments: list[dict], *, wake: dict | None = None, repeats: int = 1) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the GitHub-script behavioral harness")
    if wake is None:
        policy = bridge.load_policy(ROOT / ".ai/brain-wake-carriers.yaml")
        wake = bridge._wake(policy, "issues", 42, "TERMINAL_ATTENTION", {"event_family": "issues.opened", "issue_id": 42, "issue_number": 1210})
    path = tmp_path / "wake.json"
    path.write_text(json.dumps(wake), encoding="utf-8")
    script = workflow()["jobs"]["ring"]["steps"][-1]["with"]["script"]
    harness = r"""
const comments = INITIAL_COMMENTS;
const calls = [];
const reads = [];
const github = {
  rest: {issues: {
    listComments: 'listComments',
    createComment: async args => {calls.push(args); comments.push({body: args.body});},
  }},
  paginate: async (method, args) => {reads.push({method, args}); return comments;},
};
const post = new Function('require', 'github', 'context', 'core',
  'return (async () => {' + POSTER + '})()');
(async () => {
  try {
    for (let i = 0; i < REPEATS; i++) await post(require, github, {}, {info: () => {}});
    process.stdout.write(JSON.stringify({calls, reads}));
  } catch (error) {
    process.stdout.write(JSON.stringify({calls, reads, error: error.message}));
  }
})();
""".replace("INITIAL_COMMENTS", json.dumps(comments)).replace("POSTER", json.dumps(script)).replace("REPEATS", str(repeats))
    result = subprocess.run([node, "-e", harness], env={**os.environ, "AIOS_WAKE_PROJECTION": json.dumps(wake)}, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_same_source_projection_posts_exactly_one_top_level_comment(tmp_path):
    result = execute_poster(tmp_path, [], repeats=2)
    assert len(result["calls"]) == 1
    assert len(result["reads"]) == 2
    assert result["calls"][0]["issue_number"] == 1200
    assert result["calls"][0]["owner"] == "trung-via"
    assert result["calls"][0]["repo"] == "AIOS-renew"
    assert all(read["method"] == "listComments" and read["args"]["per_page"] == 100 for read in result["reads"])


def test_substring_duplicate_and_ack_do_not_suppress_new_wake(tmp_path):
    initial = execute_poster(tmp_path, [])
    body = initial["calls"][0]["body"]
    result = execute_poster(tmp_path, [
        {"body": body.replace("[AIOS BRAIN WAKE]", "[AIOS BRAIN ACK]")},
        {"body": body.replace("event_id: ", "event_id: prefix-")},
        {"body": "quoted wake:\n" + body},
    ])
    assert len(result["calls"]) == 1
    result = execute_poster(tmp_path, [{"body": body}])
    assert result["calls"] == []


def test_no_wake_does_not_read_or_write_comment_history(tmp_path):
    assert execute_poster(tmp_path, [], wake=bridge.NO_WAKE) == {"calls": [], "reads": []}


def test_invalid_projection_never_posts(tmp_path):
    result = execute_poster(tmp_path, [], wake={"projection": "WAKE", "version": 1, "repository": "attacker/fork"})
    assert result["calls"] == [] and result["reads"] == []
    assert "error" in result


@pytest.mark.parametrize("key", list(bridge.SOURCE_WORKFLOWS))
def test_sources_export_one_attempt_bound_selector_artifact_without_wake_authority(key):
    configured = bridge.SOURCE_WORKFLOWS[key]
    path = ROOT / configured["path"]
    source = path.read_text()
    data = yaml.load(source, Loader=yaml.BaseLoader)
    assert data["name"] == configured["name"]
    all_steps = [step for job in data["jobs"].values() for step in job.get("steps", [])]
    uploads = [step for step in all_steps if step.get("uses") == "actions/upload-artifact@v4"]
    assert len(uploads) == 1
    assert uploads[0]["with"] == {
        "name": "aios-wake-source-v1-attempt-${{ github.run_attempt }}",
        "path": "${{ runner.temp }}/aios-wake-source/source.json",
        "if-no-files-found": "error", "retention-days": "1",
    }
    assert uploads[0]["if"].startswith("always() && steps.")
    assert "outputs.ready == 'true'" in uploads[0]["if"]
    writer = next(step for step in all_steps if step.get("name") == "Write exact wake source selectors")
    assert writer["if"].startswith("always() && steps.")
    assert all_steps.index(writer) < all_steps.index(uploads[0])
    if key in {"repair_carrier", "terminal"}:
        assert data["permissions"] == {"contents": "read", "issues": "write"}
    elif key == "primary_carrier":
        assert data["permissions"] == {"contents": "read", "issues": "write", "actions": "write"}
    else:
        assert data["permissions"] == {"contents": "write", "issues": "write", "actions": "write"}
    for forbidden in ("[AIOS BRAIN WAKE]", "attention_family", "fresh_brain_sync_required", "event_id", "next_action", "wake_pr_number"):
        assert forbidden not in source
    if key != "terminal":
        receipt = next(step for step in all_steps if step.get("id") == "wake_source")
        script = receipt["with"]["script"]
        assert script.index("const source = await github.rest.issues.createComment") < script.index("core.setOutput('comment_id', String(source.data.id))")
    else:
        notify = next(step for step in all_steps if step.get("id") == "notify")
        assert "String(exact[0].id)" in notify["with"]["script"]
        assert "String(created.data.id)" in notify["with"]["script"]


@pytest.mark.parametrize("key", list(bridge.SOURCE_WORKFLOWS))
@pytest.mark.parametrize("bad", [False, True])
def test_real_source_writer_has_exact_numeric_selector_only_schema(key, bad):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the source-writer harness")
    data = yaml.load((ROOT / bridge.SOURCE_WORKFLOWS[key]["path"]).read_text(), Loader=yaml.BaseLoader)
    writer = next(step for job in data["jobs"].values() for step in job.get("steps", []) if step.get("name") == "Write exact wake source selectors")
    script = writer["with"]["script"]
    harness = r"""
const writes = [];
const outputs = {};
const fs = {mkdirSync: () => {}, writeFileSync: (path, raw) => writes.push(JSON.parse(raw))};
const req = name => name === 'fs' ? fs : require(name);
const write = new Function('require', 'core', 'return (async () => {' + SCRIPT + '})()');
(async () => {
  try {await write(req, {setOutput: (key, value) => {outputs[key] = value;}});}
  catch (error) {outputs.error = error.message;}
  process.stdout.write(JSON.stringify({writes, outputs}));
})();
""".replace("SCRIPT", json.dumps(script))
    result = subprocess.run([node, "-e", harness], env={**os.environ, "AIOS_ISSUE_ID": "bad" if bad else "4000", "AIOS_ISSUE_NUMBER": "1210", "AIOS_COMMENT_ID": "6000", "AIOS_WAKE_SOURCE_PATH": "fixture/source.json"}, capture_output=True, text=True, check=True)
    observed = json.loads(result.stdout)
    if bad:
        assert observed["writes"] == [] and "ready" not in observed["outputs"]
    else:
        assert len(observed["writes"]) == 1
        pointer = bridge.read_pointer(json.dumps(observed["writes"][0]).encode())
        assert pointer["source_kind"] == ("issue" if key == "terminal" else "comment")
        assert pointer["issue_id"] == 4000 and pointer["issue_number"] == 1210
        assert observed["outputs"] == {"ready": "true"}


def test_candidate_changes_are_inside_authorized_scope():
    authorized = {
        ".ai/brain-wake-carriers.yaml", ".github/workflows/aios-brain-wake-bridge.yml",
        ".github/workflows/aios-brain-ingress.yml", ".github/workflows/aios-brain-wakeup.yml",
        ".github/workflows/aios-brain-repair-wakeup.yml", ".github/workflows/aios-terminal-attention.yml",
        "src/aios_renew/brain_wake_bridge.py", "tests/test_brain_wake_bridge.py",
        "tests/test_brain_wake_bridge_workflow.py", "tests/test_github_issue_ingress_workflow.py",
        "tests/test_github_issue_wakeup_workflow.py", "tests/test_github_issue_repair_wakeup_workflow.py",
        "tests/test_terminal_attention_workflow.py",
    }
    result = subprocess.run(["git", "diff", "--name-only", "2b32ecd22164d0b23ea20e5af02161a9666f731d", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    assert set(result.stdout.splitlines()) <= authorized
