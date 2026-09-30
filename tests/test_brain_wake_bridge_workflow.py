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


def test_workflow_uses_only_bounded_events_and_minimum_permissions():
    data = workflow()
    assert set(data["on"]) == {"issues", "issue_comment", "workflow_run"}
    assert data["on"]["issues"]["types"] == ["opened"]
    assert data["on"]["issue_comment"]["types"] == ["created"]
    assert data["on"]["workflow_run"]["types"] == ["completed"]
    assert set(data["on"]["workflow_run"]["workflows"]) == {v["name"] for v in bridge.WORKFLOWS.values()}
    assert data["permissions"] == {"contents": "read", "actions": "read", "pull-requests": "write"}
    assert data["concurrency"]["cancel-in-progress"] == "false"
    group = data["concurrency"]["group"]
    for selector in ("github.repository", "github.event_name", "github.event.comment.id", "github.event.issue.id", "github.event.workflow_run.id", "github.event.workflow_run.run_attempt"):
        assert selector in group
    job = data["jobs"]["project-and-ring"]
    assert "github.event.issue.number != 1200" in job["if"]
    assert "!github.event.issue.pull_request" in job["if"]
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["persist-credentials"] == "false"
    source = WORKFLOW.read_text()
    assert "download-artifact" not in source
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
        wake = bridge.project_event(event_name="issues", repository=bridge.REPOSITORY, policy=policy, event={"action": "opened", "repository": {"full_name": bridge.REPOSITORY}, "sender": dict(bridge.TRUSTED_SOURCE), "issue": {"id": 42, "number": 1210, "title": "[AIOS TERMINAL ATTENTION]", "user": dict(bridge.TRUSTED_SOURCE)}})
    path = tmp_path / "wake.json"
    path.write_text(json.dumps(wake), encoding="utf-8")
    script = workflow()["jobs"]["project-and-ring"]["steps"][-1]["with"]["script"]
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
    result = subprocess.run([node, "-e", harness], env={**os.environ, "AIOS_WAKE_PROJECTION": str(path)}, capture_output=True, text=True, check=True)
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
