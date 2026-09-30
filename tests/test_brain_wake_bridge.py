"""Focused transport admission and projection cases; no lifecycle mutation."""

from __future__ import annotations

import copy
import io
import json
import zipfile
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
        "issue": {"url": f"https://api.github.com/repos/{bridge.REPOSITORY}/issues/1210", "number": 1210, "id": 4000, "title": title, "user": {"login": "trung-via"}},
        "comment": {"url": f"https://api.github.com/repos/{bridge.REPOSITORY}/issues/comments/6000", "issue_url": f"https://api.github.com/repos/{bridge.REPOSITORY}/issues/1210", "id": 6000, "body": body, "user": dict(bridge.TRUSTED_SOURCE)},
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
    if event_name == "workflow_run":
        return bridge.consume_handoff(event=event, workflow=workflow, policy=policy, api=None)
    issue = event["issue"]
    if issue["number"] == 1200 or "pull_request" in issue or issue["title"] not in bridge.SOURCE_TITLES.values():
        return dict(bridge.NO_WAKE)
    key = next(key for key, title in bridge.SOURCE_TITLES.items() if title == issue["title"])
    pointer = {"version": 1, "source_kind": "issue" if key == "terminal" else "comment", "issue_id": issue["id"], "issue_number": issue["number"]}
    source_comment = event.get("comment")
    if key != "terminal":
        pointer["comment_id"] = 6000
    return bridge.project_source(key=key, pointer=pointer, issue=issue, comment=source_comment, policy=policy)


def run_event(key: str, conclusion: str = "failure") -> tuple[dict, dict]:
    metadata = {"id": 7000, **(bridge.WORKFLOWS | bridge.SOURCE_WORKFLOWS)[key]}
    event = {
        "action": "completed", "repository": {"full_name": bridge.REPOSITORY},
        # workflow_dispatch may be triggered by the authorized Human.
        "sender": {"login": "trung-via", "type": "User"},
        "workflow_run": {"id": 8000, "workflow_id": 7000, "run_attempt": 1,
                         "name": metadata["name"], "status": "completed", "head_sha": "a" * 40,
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
    assert wake["selectors"] == {"event_family": "issue_comment.created", "issue_number": 1210, "issue_id": 4000, "comment_id": 6000}


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
    lambda e: e["comment"]["user"].update(id=42),
    lambda e: e["comment"]["user"].update(type="User"),
    lambda e: e["comment"].update(id=True),
    lambda e: e.pop("comment"),
    lambda e: e["issue"].update(number=-1),
    lambda e: e["issue"].update(url="https://api.github.com/repos/attacker/fork/issues/1210"),
    lambda e: e["comment"].update(issue_url="https://api.github.com/repos/trung-via/AIOS-renew/issues/99"),
])
def test_unauthorized_or_malformed_events_fail_closed(policy, mutation):
    event = comment(ingress())
    mutation(event)
    with pytest.raises(bridge.WakeBridgeError):
        project(event, policy)


def test_terminal_issue_requires_strict_inert_grammar(policy):
    event = {"issue": {"url": f"https://api.github.com/repos/{bridge.REPOSITORY}/issues/1211", "id": 4000, "number": 1211, "title": "[AIOS TERMINAL ATTENTION]", "user": dict(bridge.TRUSTED_SOURCE), "body": f"format: AIOS_TERMINAL_ATTENTION\nversion: 1\nrun_id: RUN-255-001\nterminal_kind: FAILURE\nartifact_sha: {'a' * 40}\n"}}
    wake = project(event, policy, "issues")
    assert wake["attention_family"] == "TERMINAL_ATTENTION"
    assert wake["selectors"] == {"event_family": "issues.opened", "issue_number": 1211, "issue_id": 4000}
    event["issue"]["body"] += "next_action: fabricate roadmap\n"
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
    changed = event["comment"].copy()
    changed["id"] = 6001
    changed["url"] = f"https://api.github.com/repos/{bridge.REPOSITORY}/issues/comments/6001"
    pointer = {"version": 1, "source_kind": "comment", "issue_id": 4000, "issue_number": 1210, "comment_id": 6001}
    assert wake["event_id"] != bridge.project_source(key="ingress", pointer=pointer, issue=event["issue"], comment=changed, policy=policy)["event_id"]


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


START = "2026-10-01T00:00:00Z"
CREATED = "2026-10-01T00:00:10Z"
END = "2026-10-01T00:00:20Z"


def archive(raw, filename="source.json", extra=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(filename, raw)
        if extra:
            bundle.writestr("other.json", "{}")
    return stream.getvalue()


class SourceAPI:
    """GitHub fixture: no issues.opened or issue_comment.created delivery exists."""
    def __init__(self, key="ingress", attempt=1):
        self.event, self.workflow = run_event(key)
        self.event["workflow_run"].update(event="workflow_dispatch" if key == "terminal" else "issues", run_attempt=attempt)
        self.attempt = {**copy.deepcopy(self.event["workflow_run"]), "repository": {"full_name": bridge.REPOSITORY}, "run_started_at": START, "updated_at": END}
        self.jobs = {"total_count": 1, "jobs": [{
            "id": 8500, "run_id": 8000, "head_sha": "a" * 40,
            "run_url": f"https://api.github.com/repos/{bridge.REPOSITORY}/actions/runs/8000",
            "name": bridge.SOURCE_ENTRY_JOBS.get(key, "publish"),
            "status": "completed", "conclusion": "success", "steps": [{"name": "Export source pointer"}],
        }]}
        source = comment("AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nreason: rejected envelope", bridge.SOURCE_TITLES[key])
        self.issue = source["issue"]
        self.comment = {**source["comment"], "created_at": CREATED, "updated_at": CREATED}
        self.pointer = {"version": 1, "source_kind": "comment", "issue_id": 4000, "issue_number": 1210, "comment_id": 6000}
        if key == "terminal":
            self.pointer.pop("comment_id")
            self.pointer["source_kind"] = "issue"
            self.issue.update(user=dict(bridge.TRUSTED_SOURCE), body=f"format: AIOS_TERMINAL_ATTENTION\nversion: 1\nrun_id: RUN-255-001\nterminal_kind: RESULT\nartifact_sha: {'a' * 40}\n")
        self.items = [{"id": 9000, "name": bridge.ARTIFACT_PREFIX + str(attempt), "expired": False, "size_in_bytes": 300, "created_at": CREATED, "workflow_run": {"id": 8000, "head_sha": "a" * 40}}]
        self.raw = archive(json.dumps(self.pointer))
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        values = {
            f"actions/workflows/{self.workflow['id']}": self.workflow,
            f"actions/runs/8000/attempts/{self.attempt['run_attempt']}": self.attempt,
            f"actions/runs/8000/attempts/{self.attempt['run_attempt']}/jobs?per_page=100": self.jobs,
            "issues/1210": self.issue,
            "issues/comments/6000": self.comment,
        }
        return values[path]

    def artifacts(self, run_id):
        assert run_id == 8000
        self.calls.append(f"artifacts:{run_id}")
        return self.items

    def archive(self, artifact_id):
        assert artifact_id == 9000
        self.calls.append(f"archive:{artifact_id}")
        return self.raw

    def project(self, policy):
        return bridge.consume_handoff(event=self.event, workflow=self.workflow, policy=policy, api=self)


def test_suppressed_event_topology_reacquires_exact_objects_and_rerun_is_same_bell(policy):
    first = SourceAPI()
    wake = first.project(policy)
    assert wake["attention_family"] == "INGRESS_REJECTED"
    assert first.calls == ["actions/runs/8000/attempts/1", "actions/runs/8000/attempts/1/jobs?per_page=100", "artifacts:8000", "archive:9000", "issues/1210", "issues/comments/6000"]
    assert wake == first.project(policy)
    rerun = SourceAPI(attempt=2)
    assert wake == rerun.project(policy)
    # Old-attempt artifact does not get selected on a rerun.
    rerun.items[0]["name"] = bridge.ARTIFACT_PREFIX + "1"
    failure = rerun.project(policy)
    assert failure["attention_family"] == "WAKE_SOURCE_HANDOFF_FAILURE"
    assert failure["event_id"] != wake["event_id"]


@pytest.mark.parametrize("target", list(bridge.SOURCE_ENTRY_JOBS))
@pytest.mark.parametrize("conclusion", ["skipped", "success"])
@pytest.mark.parametrize("attempt", [1, 2])
def test_one_ordinary_issue_and_its_skipped_sibling_runs_generate_no_attention(policy, target, conclusion, attempt):
    bodies = {
        "ingress": ingress("AUTHOR_TASK", "canonicalized TASK-255 r2"),
        "primary_carrier": carrier_body(False, "DISPATCH_ACCEPTED"),
        "repair_carrier": carrier_body(True, "SELF_HOST_COMPLETED"),
    }
    for key in bridge.SOURCE_ENTRY_JOBS:
        source = SourceAPI(key, attempt=attempt)
        source.issue["title"] = bridge.SOURCE_TITLES[target]
        source.comment["body"] = bodies[target]
        if key != target:
            source.event["workflow_run"]["conclusion"] = conclusion
            source.attempt["conclusion"] = conclusion
            source.jobs["jobs"][0].update(conclusion="skipped", steps=[])
            if key == "repair_carrier":
                source.jobs["jobs"].append({**source.jobs["jobs"][0], "id": 8501, "name": "dispatch / run"})
                source.jobs["total_count"] = 2
            source.items.clear()
        assert source.project(policy) == bridge.NO_WAKE
        if key != target:
            assert source.calls == [f"actions/runs/8000/attempts/{attempt}", f"actions/runs/8000/attempts/{attempt}/jobs?per_page=100"]


@pytest.mark.parametrize("key", list(bridge.SOURCE_ENTRY_JOBS))
@pytest.mark.parametrize("conclusion", ["success", "failure", "skipped"])
@pytest.mark.parametrize("defect", ["missing", "malformed", "substituted"])
def test_executed_target_entry_still_requires_handoff_regardless_of_run_conclusion(policy, key, conclusion, defect):
    source = SourceAPI(key)
    source.event["workflow_run"]["conclusion"] = conclusion
    source.attempt["conclusion"] = conclusion
    if defect == "missing":
        source.items.clear()
    elif defect == "malformed":
        source.raw = archive("not JSON")
    else:
        source.raw = archive(json.dumps({**source.pointer, "issue_id": 42}))
    wake = source.project(policy)
    assert wake["attention_family"] == "WAKE_SOURCE_HANDOFF_FAILURE"
    assert set(wake["selectors"]) == {"event_family", "workflow_run_id", "run_attempt", "workflow_id"}


@pytest.mark.parametrize("mutation", [
    lambda s: s.jobs.update(jobs=[]),
    lambda s: s.jobs.update(total_count=True),
    lambda s: s.jobs.update(total_count=101),
    lambda s: s.jobs["jobs"].append(copy.deepcopy(s.jobs["jobs"][0])),
    lambda s: s.jobs["jobs"][0].update(name="dispatch / run"),
    lambda s: s.jobs["jobs"][0].update(run_id=42),
    lambda s: s.jobs["jobs"][0].update(head_sha="b" * 40),
    lambda s: s.jobs["jobs"][0].update(run_url="https://api.github.com/repos/attacker/fork/actions/runs/8000"),
    lambda s: s.jobs["jobs"][0].update(status="in_progress"),
    lambda s: s.jobs["jobs"][0].update(steps=[{"name": "Export source pointer"}]),
])
def test_unbound_or_ambiguous_skipped_entry_cannot_suppress_handoff_failure(policy, mutation):
    source = SourceAPI()
    source.event["workflow_run"]["conclusion"] = "skipped"
    source.attempt["conclusion"] = "skipped"
    source.jobs["jobs"][0].update(conclusion="skipped", steps=[])
    source.items.clear()
    mutation(source)
    assert source.project(policy)["attention_family"] == "WAKE_SOURCE_HANDOFF_FAILURE"


@pytest.mark.parametrize("conclusion", sorted(bridge.FAILED_CONCLUSIONS))
def test_failed_target_with_unstarted_entry_is_not_quiet_bookkeeping(policy, conclusion):
    source = SourceAPI()
    source.event["workflow_run"]["conclusion"] = conclusion
    source.attempt["conclusion"] = conclusion
    source.jobs["jobs"][0].update(conclusion="skipped", steps=[])
    source.items.clear()
    assert source.project(policy)["attention_family"] == "WAKE_SOURCE_HANDOFF_FAILURE"


@pytest.mark.parametrize("key", list(bridge.SOURCE_WORKFLOWS))
def test_all_source_workflows_reacquire_and_map_only_their_receipt(policy, key):
    source = SourceAPI(key)
    bodies = {
        "ingress": ingress(),
        "primary_carrier": "AIOS BRAIN WAKEUP RECEIPT\nstatus: REJECTED\ndispatch_accepted: false\nexecution_outcome: not_observed\nreason: rejected",
        "repair_carrier": "AIOS REPAIR WAKEUP CARRIER RECEIPT\nstatus: REJECTED\nself_host_completed: false\nrepair_run_outcome: not_asserted_by_carrier\nverification: not_asserted_by_carrier\nsemantic_review: not_asserted_by_carrier\npublication: not_asserted_by_carrier\nreason: rejected",
    }
    if key != "terminal":
        source.comment["body"] = bodies[key]
    expected = {"ingress": "REVIEW_CHANGES_REQUIRED", "primary_carrier": "PRIMARY_DISPATCH_REJECTED", "repair_carrier": "REPAIR_DISPATCH_REJECTED", "terminal": "TERMINAL_ATTENTION"}
    assert source.project(policy)["attention_family"] == expected[key]
    assert "issues/1210" in source.calls


@pytest.mark.parametrize("body", [
    ingress(detail="canonicalized PASS review decision for RUN-255-001"),
    ingress(detail="identical REVIEW already canonicalized for RUN-255-001", status="IDEMPOTENT", replayed="true"),
    ingress("AUTHOR_TASK", "canonicalized TASK-255 r2"),
])
def test_reacquired_ordinary_and_replayed_ingress_remains_quiet(policy, body):
    source = SourceAPI()
    source.comment["body"] = body
    assert source.project(policy) == bridge.NO_WAKE


def test_terminal_push_uses_exact_kind_run_and_sha_ref(policy):
    source = SourceAPI("terminal")
    branch = f"aios/terminal-attention/RESULT/RUN-255-001/{'a' * 40}"
    source.event["workflow_run"].update(event="push", head_branch=branch)
    source.attempt.update(event="push", head_branch=branch)
    assert source.project(policy)["attention_family"] == "TERMINAL_ATTENTION"
    source.event["workflow_run"]["head_branch"] = "aios/terminal-attention/RUN-255-001"
    with pytest.raises(bridge.WakeBridgeError):
        source.project(policy)


@pytest.mark.parametrize("mutation", [
    lambda s: s.items.clear(),
    lambda s: s.items.append(copy.deepcopy(s.items[0])),
    lambda s: s.items[0].update(expired=True),
    lambda s: s.items[0].update(size_in_bytes=4097),
    lambda s: s.items[0].update(created_at="2026-09-30T23:59:00Z"),
    lambda s: s.items[0]["workflow_run"].update(id=42),
    lambda s: s.items[0]["workflow_run"].update(head_sha="b" * 40),
    lambda s: s.attempt.update(head_sha="b" * 40),
    lambda s: s.attempt.update(run_attempt=2),
    lambda s: s.attempt["repository"].update(full_name="attacker/fork"),
    lambda s: setattr(s, "raw", b"x" * 4097),
    lambda s: setattr(s, "raw", b"not a zip"),
    lambda s: setattr(s, "raw", archive(json.dumps(s.pointer), "../source.json")),
    lambda s: setattr(s, "raw", archive(json.dumps(s.pointer), extra=True)),
    lambda s: setattr(s, "raw", archive(" " * 513)),
    lambda s: setattr(s, "raw", archive('{"version":1,"version":1}')),
    lambda s: setattr(s, "raw", archive(json.dumps({**s.pointer, "next_action": "REPAIR"}))),
    lambda s: setattr(s, "raw", archive(json.dumps({**s.pointer, "comment_id": True}))),
    lambda s: setattr(s, "raw", archive(json.dumps({**s.pointer, "issue_id": 42}))),
    lambda s: s.issue.update(id=42),
    lambda s: s.issue.update(title="[AIOS BRAIN WAKEUP]"),
    lambda s: s.issue.update(url="https://api.github.com/repos/attacker/fork/issues/1210"),
    lambda s: s.comment.update(id=42),
    lambda s: s.comment.update(issue_url="https://api.github.com/repos/trung-via/AIOS-renew/issues/99"),
    lambda s: s.comment.update(url="https://api.github.com/repos/attacker/fork/issues/comments/6000"),
    lambda s: s.comment["user"].update(id=42),
    lambda s: s.comment["user"].update(login="attacker"),
    lambda s: s.comment["user"].update(type="User"),
    lambda s: s.comment.update(created_at="2026-09-30T23:59:00Z"),
    lambda s: s.comment.update(updated_at=END),
    lambda s: s.comment.update(body="AIOS BRAIN INGRESS RECEIPT\nstatus: FAIL\nnext_action: REPAIR"),
])
def test_malformed_stale_and_substituted_handoffs_only_operational_failure(policy, mutation):
    source = SourceAPI()
    mutation(source)
    wake = source.project(policy)
    assert wake["attention_family"] == "WAKE_SOURCE_HANDOFF_FAILURE"
    assert set(wake["selectors"]) == {"event_family", "workflow_run_id", "run_attempt", "workflow_id"}
    assert wake == source.project(policy)
    payload = yaml.safe_load(wake["body"].split("\n", 1)[1])
    assert set(payload) == {"version", "event_id", "attention_family", "repository", "selectors", "fresh_brain_sync_required"}


@pytest.mark.parametrize("mutation", [
    lambda s: s.workflow.update(id=42),
    lambda s: s.workflow.update(path=".github/workflows/evil.yml"),
    lambda s: s.workflow.update(name="AIOS Brain PRIMARY Wakeup Carrier"),
    lambda s: s.event["workflow_run"].update(name="unknown"),
    lambda s: s.event["workflow_run"].update(event="issue_comment"),
    lambda s: s.event["workflow_run"].update(head_branch="attacker"),
    lambda s: s.event["repository"].update(full_name="attacker/fork"),
    lambda s: s.event["workflow_run"]["head_repository"].update(full_name="attacker/fork"),
])
def test_unconfigured_source_identity_rejected_before_handoff_reads(policy, mutation):
    source = SourceAPI()
    mutation(source)
    with pytest.raises(bridge.WakeBridgeError):
        source.project(policy)
    assert source.calls == []


def test_cli_resolves_workflow_by_exact_id_then_consumes_only_triggering_run(monkeypatch, tmp_path):
    source = SourceAPI()
    monkeypatch.setenv("GH_TOKEN", "fixture")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(bridge, "GitHubAPI", lambda token, temp: source)
    event = tmp_path / "event.json"
    event.write_text(json.dumps(source.event))
    output = tmp_path / "projection.json"
    assert bridge.main(["--event", str(event), "--event-name", "workflow_run", "--repository", bridge.REPOSITORY, "--policy", str(POLICY_PATH), "--output", str(output)]) == 0
    assert source.calls[0] == "actions/workflows/7000"
    assert json.loads(output.read_text())["attention_family"] == "INGRESS_REJECTED"


def test_download_location_must_be_outside_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    with pytest.raises(bridge.WakeBridgeError):
        bridge.GitHubAPI("fixture", str(tmp_path / "artifacts"))
    bridge.GitHubAPI("fixture", str(tmp_path.parent))


def test_terminal_reuse_same_source_identity_across_distinct_runs(policy):
    first, second = SourceAPI("terminal"), SourceAPI("terminal")
    second.event["workflow_run"]["id"] = 8001
    second.attempt["id"] = 8001
    second.items[0]["workflow_run"]["id"] = 8001
    second.get = lambda path: second.attempt if path.startswith("actions/runs/") else second.issue
    second.artifacts = lambda run_id: second.items
    assert first.project(policy) == second.project(policy)
