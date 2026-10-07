"""Legacy delivery retirement and retained bounded source handoff contracts."""

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


def test_legacy_work_wake_bridge_workflow_is_retired():
    # H4A0 retires delivery only; deterministic parser/projection support remains.
    assert not WORKFLOW.exists()


@pytest.mark.parametrize("key", list(bridge.SOURCE_WORKFLOWS))
def test_sources_export_one_attempt_bound_selector_artifact_without_wake_authority(key):
    configured = bridge.SOURCE_WORKFLOWS[key]
    path = ROOT / configured["path"]
    source = path.read_text()
    data = yaml.load(source, Loader=yaml.BaseLoader)
    assert data["name"] == configured["name"]
    all_steps = [step for job in data["jobs"].values() for step in job.get("steps", [])]
    uploads = [step for step in all_steps if step.get("uses") == "actions/upload-artifact@v4"]
    selector_name = "aios-wake-source-v1-attempt-${{ github.run_attempt }}"
    selector_uploads = [step for step in uploads if step["with"].get("name") == selector_name]
    assert len(selector_uploads) == 1
    selector_upload = selector_uploads[0]
    assert selector_upload["with"] == {
        "name": "aios-wake-source-v1-attempt-${{ github.run_attempt }}",
        "path": "${{ runner.temp }}/aios-wake-source/source.json",
        "if-no-files-found": "error", "retention-days": "1",
    }
    # Other authenticated-origin and attention artifacts are separate transports.
    # None may substitute for the exact numeric wake selector export.
    assert all(step["with"].get("path") != selector_upload["with"]["path"]
               for step in uploads if step is not selector_upload)
    assert selector_upload["if"].startswith("always() && steps.")
    assert "outputs.ready == 'true'" in selector_upload["if"]
    writer = next(step for step in all_steps if step.get("name") == "Write exact wake source selectors")
    assert writer["if"].startswith("always() && steps.")
    assert all_steps.index(writer) < all_steps.index(selector_upload)
    if key in {"repair_carrier", "terminal"}:
        assert data["permissions"] == {"contents": "read", "issues": "write"}
    elif key == "primary_carrier":
        assert data["permissions"] == {"contents": "read", "issues": "write", "actions": "write"}
    else:
        assert data["permissions"] == {"contents": "write", "issues": "write", "actions": "write"}
    # Event identities elsewhere in the workflow are delivery correlation. The
    # selector writer itself carries no semantic or lifecycle instructions.
    for forbidden in ("[AIOS BRAIN WAKE]", "attention_family", "fresh_brain_sync_required", "event_id",
                      "next_action", "wake_pr_number", "semantic_review", "publication", "authority"):
        assert forbidden not in writer["with"]["script"]
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
    assert node is not None, "Node is required for the source-writer harness"
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

        # The retained writer still feeds the historical pure projection contract.
        # These independently reacquired objects are fixtures, with no delivery API.
        bodies = {
            "ingress": "AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nreason: rejected envelope",
            "primary_carrier": "AIOS BRAIN WAKEUP RECEIPT\nstatus: REJECTED\ndispatch_accepted: false\nexecution_outcome: not_observed\nreason: rejected admission",
            "repair_carrier": "AIOS REPAIR WAKEUP CARRIER RECEIPT\nstatus: REJECTED\nself_host_completed: false\nrepair_run_outcome: not_asserted_by_carrier\nverification: not_asserted_by_carrier\nsemantic_review: not_asserted_by_carrier\npublication: not_asserted_by_carrier\nreason: rejected admission",
            "terminal": f"format: AIOS_TERMINAL_ATTENTION\nversion: 1\nrun_id: RUN-260-001\nterminal_kind: FAILURE\nartifact_sha: {'a' * 40}\n",
        }
        issue_url = f"https://api.github.com/repos/{bridge.REPOSITORY}/issues/1210"
        issue = {"id": 4000, "number": 1210, "url": issue_url, "title": bridge.SOURCE_TITLES[key]}
        comment = None
        if key == "terminal":
            issue.update(body=bodies[key], user=dict(bridge.TRUSTED_SOURCE))
        else:
            assert pointer["comment_id"] == 6000
            comment = {
                "id": 6000, "url": f"https://api.github.com/repos/{bridge.REPOSITORY}/issues/comments/6000",
                "issue_url": issue_url, "body": bodies[key], "user": dict(bridge.TRUSTED_SOURCE),
            }
        policy = bridge.load_policy(ROOT / ".ai/brain-wake-carriers.yaml")
        inputs = {"key": key, "pointer": pointer, "issue": issue, "comment": comment, "policy": policy}
        wake = bridge.project_source(**inputs)
        assert wake == bridge.project_source(**inputs)
        assert wake["attention_family"] == {
            "ingress": "INGRESS_REJECTED", "primary_carrier": "PRIMARY_DISPATCH_REJECTED",
            "repair_carrier": "REPAIR_DISPATCH_REJECTED", "terminal": "TERMINAL_ATTENTION",
        }[key]
        assert wake["selectors"] == {
            "event_family": "issues.opened" if key == "terminal" else "issue_comment.created",
            **{field: value for field, value in pointer.items() if field not in {"version", "source_kind"}},
        }
        payload = yaml.safe_load(wake["body"].split("\n", 1)[1])
        assert set(payload) == {"version", "event_id", "attention_family", "repository", "selectors", "fresh_brain_sync_required"}


def test_candidate_changes_are_inside_authorized_scope():
    # Runtime owns each candidate's TASK/Git scope gate. The enduring workflow
    # invariant is that these writers can change only their transport sidecar.
    for key, configured in bridge.SOURCE_WORKFLOWS.items():
        data = yaml.load((ROOT / configured["path"]).read_text(), Loader=yaml.BaseLoader)
        writer = next(step for job in data["jobs"].values() for step in job.get("steps", [])
                      if step.get("name") == "Write exact wake source selectors")
        env = writer["env"]
        assert env["AIOS_WAKE_SOURCE_PATH"] == "${{ runner.temp }}/aios-wake-source/source.json"
        assert set(env) == {"AIOS_WAKE_SOURCE_PATH", "AIOS_ISSUE_ID", "AIOS_ISSUE_NUMBER"} | (
            {"AIOS_COMMENT_ID"} if key != "terminal" else set())
        script = writer["with"]["script"]
        assert script.count("fs.writeFileSync(") == 1
        assert "fs.writeFileSync(process.env.AIOS_WAKE_SOURCE_PATH, JSON.stringify(pointer), 'utf8')" in script
        assert "core.setOutput('ready', 'true')" in script
        for forbidden in (".ai/", "exec", "spawn", "createComment", "dispatch", "review", "publish"):
            assert forbidden not in script


@pytest.mark.parametrize("field", ["event_id", "next_action", "authority", "task", "verification",
                                  "semantic_review", "publication"])
def test_source_transport_metadata_cannot_add_semantic_or_lifecycle_authority(field):
    pointer = {"version": 1, "source_kind": "comment", "issue_id": 4000,
               "issue_number": 1210, "comment_id": 6000}
    assert bridge.read_pointer(json.dumps(pointer).encode()) == pointer
    with pytest.raises(bridge.WakeBridgeError):
        bridge.read_pointer(json.dumps({**pointer, field: "claimed authority"}).encode())
