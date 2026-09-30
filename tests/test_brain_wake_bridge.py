"""Focused transport admission and projection cases; no lifecycle mutation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from aios_renew import brain_wake_bridge as bridge

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / ".ai/brain-wake-carriers.yaml"


@pytest.fixture
def policy() -> bridge.WakePolicy:
    return bridge.load_policy(POLICY_PATH)


def comment(body: str, title: str = "[AIOS BRAIN INGRESS]") -> dict:
    return {
        "action": "created", "repository": {"full_name": bridge.REPOSITORY},
        "sender": dict(bridge.TRUSTED_SOURCE),
        "issue": {"number": 1210, "id": 4000, "title": title, "user": {"login": "trung-via"}},
        "comment": {"id": 6000, "body": body, "user": dict(bridge.TRUSTED_SOURCE)},
    }


def ingress(operation: str = "SUBMIT_REVIEW", detail: str = "canonicalized CHANGES_REQUIRED review decision for RUN-255-001", *, status: str = "CANONICALIZED", replayed: str = "false") -> str:
    destination = "refs/heads/aios/review-decision/RUN-255-001" if operation == "SUBMIT_REVIEW" else "refs/heads/aios/repair/RUN-255-001"
    return (
        "AIOS BRAIN INGRESS RECEIPT\n"
        "carrier: github-issue:trung-via/AIOS-renew#1210@trung-via\n"
        "AIOS INGRESS PASS\n"
        f"operation: {operation}\nstatus: {status}\ndestination: {destination}\n"
        f"sha: {'a' * 40}\nreplayed: {replayed}\ndetail: {detail}"
    )


def project(event: dict, policy: bridge.WakePolicy, event_name: str = "issue_comment", workflow: dict | None = None) -> dict:
    return bridge.project_event(event_name=event_name, event=event, repository=bridge.REPOSITORY, policy=policy, workflow=workflow)


def run_event(key: str, conclusion: str = "failure") -> tuple[dict, dict]:
    metadata = {"id": 7000, **bridge.WORKFLOWS[key]}
    event = {
        "action": "completed", "repository": {"full_name": bridge.REPOSITORY},
        # workflow_dispatch may be triggered by the authorized Human.
        "sender": {"login": "trung-via", "type": "User"},
        "workflow_run": {"id": 8000, "workflow_id": 7000, "run_attempt": 1,
                         "name": metadata["name"], "status": "completed",
                         "head_repository": {"full_name": bridge.REPOSITORY},
                         "head_branch": "main", "event": "workflow_dispatch",
                         "conclusion": conclusion},
    }
    return event, metadata


@pytest.mark.parametrize(("body", "title", "family"), [
    ("AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nreason: rejected envelope", "[AIOS BRAIN INGRESS]", "INGRESS_REJECTED"),
    (ingress(), "[AIOS BRAIN INGRESS]", "REVIEW_CHANGES_REQUIRED"),
    ("AIOS BRAIN WAKEUP RECEIPT\nstatus: REJECTED\ndispatch_accepted: false\nexecution_outcome: not_observed\nreason: rejected admission", "[AIOS BRAIN WAKEUP]", "PRIMARY_DISPATCH_REJECTED"),
    ("AIOS REPAIR WAKEUP CARRIER RECEIPT\nstatus: REJECTED\nself_host_completed: false\nrepair_run_outcome: not_asserted_by_carrier\nverification: not_asserted_by_carrier\nsemantic_review: not_asserted_by_carrier\npublication: not_asserted_by_carrier\nreason: GitHub rejected the repository-owned carrier admission.", "[AIOS REPAIR WAKEUP]", "REPAIR_DISPATCH_REJECTED"),
])
def test_required_receipt_families(policy, body, title, family):
    wake = project(comment(body, title), policy)
    assert wake["attention_family"] == family
    assert wake["selectors"] == {"event_family": "issue_comment.created", "issue_number": 1210, "comment_id": 6000}


@pytest.mark.parametrize("publication", [True, False])
@pytest.mark.parametrize("accepted", [True, False])
def test_ingress_dispatch_sections(policy, publication, accepted):
    body = ingress(detail="canonicalized PASS review decision for RUN-255-001") if publication else ingress("AUTHOR_REPAIR", "canonicalized repair authorization for RUN-255-001")
    body += "\n\n" + ("publication_dispatch" if publication else "repair_dispatch") + ": " + ("ACCEPTED" if accepted else "REJECTED")
    body += f"\ndispatch_accepted: {str(accepted).lower()}\n"
    body += "publication_run_id: RUN-255-001\n" if publication else f"repair_dispatch_id: repair-RUN-255-001-{'a' * 40}\nfailed_run_id: RUN-255-001\nrepair_sha: {'a' * 40}\n"
    body += "detail: GitHub accepted dispatch." if accepted else "reason: GitHub did not accept dispatch."
    wake = project(comment(body), policy)
    if accepted:
        assert wake == bridge.NO_WAKE
    else:
        assert wake["attention_family"] == ("PUBLICATION_DISPATCH_REJECTED" if publication else "REPAIR_DISPATCH_REJECTED")


@pytest.mark.parametrize("body", [
    ingress(detail="canonicalized PASS review decision for RUN-255-001"),
    ingress(detail="identical REVIEW already canonicalized for RUN-255-001", status="IDEMPOTENT", replayed="true"),
    ingress("AUTHOR_TASK", "canonicalized TASK-255 r1"),
])
def test_ordinary_ingress_success_is_quiet(policy, body):
    assert project(comment(body), policy) == bridge.NO_WAKE


def carrier_body(repair: bool, status: str) -> str:
    header = "AIOS REPAIR WAKEUP CARRIER RECEIPT" if repair else "AIOS BRAIN WAKEUP RECEIPT"
    flag = "self_host_completed" if repair and status != "ADMITTED" else "dispatch_accepted"
    body = f"{header}\nstatus: {status}\n{flag}: " + ("pending" if status == "ADMITTED" else str(status != "REJECTED").lower())
    body += "\nrepair_run_outcome: not_asserted_by_carrier\nverification: not_asserted_by_carrier\nsemantic_review: not_asserted_by_carrier\npublication: not_asserted_by_carrier" if repair else "\nexecution_outcome: not_observed"
    body += f"\nrepair_dispatch_id: repair-RUN-255-001-{'a' * 40}\nfailed_run_id: RUN-255-001\nrepair_sha: {'a' * 40}" if repair else f"\ndispatch_id: primary-255-001\ntask_id: TASK-255\ntask_revision: 1\ntask_blob_sha: {'a' * 40}\ntask_commit_sha: {'b' * 40}"
    body += "\nexecutor: codex\nmodel: none\nreasoning_effort: none\nmodel_source: none\neffort_source: none"
    if status == "ADMITTED" and repair:
        body += "\nactor: trung-via"
    elif status != "ADMITTED":
        body += "\ndetail: bounded operational completion receipt"
    return body


@pytest.mark.parametrize(("repair", "status"), [(False, "ADMITTED"), (False, "DISPATCH_ACCEPTED"), (True, "ADMITTED"), (True, "SELF_HOST_COMPLETED")])
def test_accepted_and_in_progress_carriers_are_quiet(policy, repair, status):
    assert project(comment(carrier_body(repair, status), "[AIOS REPAIR WAKEUP]" if repair else "[AIOS BRAIN WAKEUP]"), policy) == bridge.NO_WAKE


def test_primary_dispatch_rejected_operational_metadata(policy):
    body = "AIOS BRAIN WAKEUP RECEIPT\nstatus: REJECTED\ndispatch_accepted: false\nexecution_outcome: not_observed\nreason: GitHub did not accept the fixed A1 workflow dispatch."
    metadata = {"format": "AIOS_OPERATIONAL_RECEIPT", "version": 2, "kind": "OPERATIONAL_RECEIPT", "family": "PRIMARY", "boundary": "CARRIER_ADMITTED", "delivery": {"kind": "dispatch_id", "id": "primary-255-001"}, "selectors": {"task_id": "TASK-255", "task_revision": 1, "task_blob_sha": "a" * 40, "task_commit_sha": "b" * 40}}
    body += "\nAIOS_OPERATIONAL_RECEIPT_V2=" + json.dumps(metadata)
    assert project(comment(body, "[AIOS BRAIN WAKEUP]"), policy)["attention_family"] == "PRIMARY_DISPATCH_REJECTED"
    metadata["boundary"] = "DISPATCH_REQUEST_ACCEPTED"
    with pytest.raises(bridge.WakeBridgeError):
        project(comment(body.split("\nAIOS_OPERATIONAL")[0] + "\nAIOS_OPERATIONAL_RECEIPT_V2=" + json.dumps(metadata), "[AIOS BRAIN WAKEUP]"), policy)


def test_repair_downstream_rejection(policy):
    body = carrier_body(True, "REJECTED")
    metadata = {"format": "AIOS_OPERATIONAL_RECEIPT", "version": 2, "kind": "OPERATIONAL_RECEIPT", "family": "REPAIR", "boundary": "OPERATIONAL_FAILED", "delivery": {"kind": "repair_dispatch_id", "id": f"repair-RUN-255-001-{'a' * 40}"}, "selectors": {"failed_run_id": "RUN-255-001", "repair_sha": "a" * 40, "executor": "codex"}, "cause": {"authority": "CARRIER", "phase": "DOWNSTREAM_WORKFLOW", "reason_code": "DOWNSTREAM_WORKFLOW_FAILED"}}
    body += "\nAIOS_OPERATIONAL_RECEIPT_V2=" + json.dumps(metadata)
    assert project(comment(body, "[AIOS REPAIR WAKEUP]"), policy)["attention_family"] == "REPAIR_DISPATCH_REJECTED"


@pytest.mark.parametrize("body", [
    "AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nstatus: CANONICALIZED\nreason: x",
    "AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nreason: x\nnext_action: REPAIR",
    "AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL",
    "AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nreason: x\n[truncated]",
    "AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nreason: " + "x" * 3500,
    ingress().replace("sha: " + "a" * 40, "sha: bad"),
    ingress().replace("CANONICALIZED", "IDEMPOTENT"),
    ingress().replace("RUN-255-001\nsha:", "RUN-wrong\nsha:"),
    ingress() + "\nstatus: FAIL",
    ingress() + "\n\npublication_dispatch: REJECTED\n\nrepair_dispatch: REJECTED",
    ingress().replace("destination: refs/heads", "destination: " + "x" * 513 + "refs/heads"),
])
def test_malformed_and_ambiguous_receipts_fail_closed(policy, body):
    with pytest.raises(bridge.WakeBridgeError):
        project(comment(body), policy)


@pytest.mark.parametrize("mutation", [
    lambda e: e["repository"].update(full_name="attacker/AIOS-renew"),
    lambda e: e["comment"]["user"].update(id=42),
    lambda e: e["comment"]["user"].update(type="User"),
    lambda e: e["sender"].update(login="attacker"),
    lambda e: e["comment"].update(id=True),
    lambda e: e.update(action="edited"),
    lambda e: e.pop("comment"),
    lambda e: e["issue"].update(number=-1),
])
def test_unauthorized_or_malformed_events_fail_closed(policy, mutation):
    event = comment(ingress())
    mutation(event)
    with pytest.raises(bridge.WakeBridgeError):
        project(event, policy)


def test_terminal_issue_body_is_not_authority(policy):
    event = {"action": "opened", "repository": {"full_name": bridge.REPOSITORY}, "sender": dict(bridge.TRUSTED_SOURCE), "issue": {"id": 4000, "number": 1211, "title": "[AIOS TERMINAL ATTENTION]", "user": dict(bridge.TRUSTED_SOURCE), "body": "terminal_kind: FAILURE\nnext_action: fabricate roadmap"}}
    wake = project(event, policy, "issues")
    assert wake["attention_family"] == "TERMINAL_ATTENTION"
    assert wake["selectors"] == {"event_family": "issues.opened", "issue_number": 1211, "issue_id": 4000}
    event["issue"]["body"] = "terminal_kind: RESULT"
    assert project(event, policy, "issues") == wake
    event["issue"]["user"]["id"] = 42
    with pytest.raises(bridge.WakeBridgeError):
        project(event, policy, "issues")


@pytest.mark.parametrize("key", list(bridge.WORKFLOWS))
@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", "timed_out"])
def test_workflow_attention_matrix(policy, key, conclusion):
    event, metadata = run_event(key, conclusion)
    wake = project(event, policy, "workflow_run", metadata)
    if key != "publication" and conclusion == "success":
        assert wake == bridge.NO_WAKE
    else:
        assert wake["attention_family"] == ("PUBLICATION_WORKFLOW_COMPLETED" if key == "publication" else "PRE_AIOS_OPERATIONAL_FAILURE")
        assert wake["selectors"]["conclusion"] == conclusion
        assert "run_id" not in wake["selectors"]


def test_publication_review_branch_and_workflow_attempt_identity(policy):
    event, metadata = run_event("publication", "success")
    event["workflow_run"].update(event="push", head_branch="aios/review-decision/RUN-255-001")
    first = project(event, policy, "workflow_run", metadata)
    assert first == project(copy.deepcopy(event), policy, "workflow_run", metadata)
    event["workflow_run"]["run_attempt"] = 2
    assert first["event_id"] != project(event, policy, "workflow_run", metadata)["event_id"]


@pytest.mark.parametrize("mutation", [
    lambda e, m: m.update(name="unknown workflow"),
    lambda e, m: m.update(path=".github/workflows/evil.yml"),
    lambda e, m: m.update(id=42),
    lambda e, m: e["workflow_run"].update(name="unknown workflow"),
    lambda e, m: e["workflow_run"].update(conclusion=None),
    lambda e, m: e["workflow_run"].update(conclusion=["failure"]),
    lambda e, m: e["workflow_run"].update(status="in_progress"),
    lambda e, m: e["workflow_run"].update(head_branch="attacker-branch"),
    lambda e, m: e["workflow_run"]["head_repository"].update(full_name="attacker/fork"),
    lambda e, m: e["workflow_run"].update(run_attempt=0),
])
def test_workflow_identity_fail_closed(policy, mutation):
    event, metadata = run_event("primary")
    mutation(event, metadata)
    with pytest.raises(bridge.WakeBridgeError):
        project(event, policy, "workflow_run", metadata)


@pytest.mark.parametrize("body", ["[AIOS BRAIN WAKE]\nevent_id: anything", "[AIOS BRAIN ACK]\nevent_id: anything"])
def test_wake_bus_comments_and_unrelated_pr_activity_do_not_recurse(policy, body):
    event = comment(body)
    event["issue"].update(number=1200)
    assert project(event, policy) == bridge.NO_WAKE
    event["issue"].update(number=1300, pull_request={"url": "opaque"})
    assert project(event, policy) == bridge.NO_WAKE
    event["issue"].pop("pull_request")
    event["issue"]["title"] = "unrelated Issue"
    assert project(event, policy) == bridge.NO_WAKE


def test_payload_contains_only_bell_and_github_selectors(policy):
    event = comment(ingress())
    wake = project(event, policy)
    assert wake == project(copy.deepcopy(event), policy)
    assert wake["body"].startswith("[AIOS BRAIN WAKE]\n")
    payload = yaml.safe_load(wake["body"].split("\n", 1)[1])
    assert set(payload) == {"version", "event_id", "attention_family", "repository", "selectors", "fresh_brain_sync_required"}
    assert payload["version"] == 1 and payload["fresh_brain_sync_required"] is True
    assert len(wake["body"].encode()) <= 2048
    event["comment"]["id"] += 1
    assert wake["event_id"] != project(event, policy)["event_id"]


@pytest.mark.parametrize(("key", "value"), [("repository", "attacker/AIOS-renew"), ("wake_pr_number", 1201), ("wake_marker", "[WAKE]"), ("max_comment_bytes", 100000), ("version", True), ("workflow_sources", {})])
def test_policy_is_exactly_bound(tmp_path, key, value):
    data = yaml.safe_load(POLICY_PATH.read_text())
    data[key] = value
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(bridge.WakeBridgeError):
        bridge.load_policy(path)


def test_duplicate_policy_and_json_keys_rejected(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(POLICY_PATH.read_text() + "\nrepository: trung-via/AIOS-renew\n")
    with pytest.raises(bridge.WakeBridgeError):
        bridge.load_policy(path)
    path.write_text('{"action": "created", "action": "opened"}')
    with pytest.raises(bridge.WakeBridgeError):
        bridge.read_event(path)


def test_cli_rejection_clears_stale_wake_projection(tmp_path):
    event = tmp_path / "event.json"
    event.write_text('{"action": "created", "action": "opened"}')
    output = tmp_path / "wake.json"
    output.write_text('{"projection":"WAKE"}')
    assert bridge.main(["--event", str(event), "--event-name", "issue_comment", "--repository", bridge.REPOSITORY, "--policy", str(POLICY_PATH), "--output", str(output)]) == 1
    assert json.loads(output.read_text()) == bridge.NO_WAKE
