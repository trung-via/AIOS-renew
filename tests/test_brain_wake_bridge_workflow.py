"""Bounded workflow authority and delivery/source handoff wiring."""

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
    assert data["permissions"] == {"contents": "read", "actions": "read", "pull-requests": "read"}
    ring = data["jobs"]["ring"]
    assert ring["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert ring["concurrency"]["cancel-in-progress"] == "false"
    assert ring["concurrency"]["group"] == "aios-brain-wake-1200"
    job = data["jobs"]["project"]
    assert job["permissions"] == {"contents": "read", "actions": "read", "pull-requests": "read"}
    assert job["if"] == "github.repository == 'trung-via/AIOS-renew'"
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["persist-credentials"] == "false"
    source = WORKFLOW.read_text()
    assert "continue-on-error" not in source and "if: always()" not in source
    assert source.count("contents: write") == 1
    assert "github.rest.issues.createComment" not in source
    assert ring["steps"][0]["with"] == {"ref": "main", "fetch-depth": "1", "persist-credentials": "false"}
    delivery = ring["steps"][-1]
    assert delivery["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert delivery["env"]["AIOS_WAKE_PROJECTION"] == "${{ needs.project.outputs.projection }}"
    assert "--deliver --projection" in delivery["run"]
    assert "separate" in source and "post-publication probe" in source
    assert "Human/Brain architecture review" in source
    for forbidden in ("secrets.", "actions: write", "workflow: write", "createWorkflowDispatch", "pulls.merge", "git push", "next_action"):
        assert forbidden not in source


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
        "src/aios_renew/brain_wake_bridge.py", "tests/test_brain_wake_bridge.py",
        "tests/test_brain_wake_bridge_workflow.py",
    }
    # TASK-255 r4's admitted RUN-255-005 base and exact modify scope.
    result = subprocess.run(["git", "diff", "--name-only", "309c37ea66bac817aa1c10da3915d741c0c58ec5", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    assert set(result.stdout.splitlines()) <= authorized
