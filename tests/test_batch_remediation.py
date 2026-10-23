"""Prospective batch integration on disposable real Git repos and synthetic runners.

No provider, hosted workflow, or production execution is launched by these tests.
"""

from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import pytest
import yaml

from aios_renew import operator as op
import aios_renew.publication as publication_module
from aios_renew.artifacts import Result, ResultPackage, validate_evidence, validate_result
from aios_renew.authoring_ingress import AuthoringIngressError, IngressEnvelope, execute_ingress
from aios_renew.correction_frontier import CorrectionFrontier, CorrectionFrontierError
from aios_renew.publication import PublicationError, _validate_remediation_package, publish_review_decision
from aios_renew.review import (BatchRemediation, BatchSelection, ReviewValidationError,
    parse_batch_selection, parse_remediation, parse_review, validate_remediation, validate_review)
from aios_renew.review_transport import transport_post_pass
from aios_renew.run import Run
from aios_renew.task import parse_task
from aios_renew.terminal_attention import parse_signal_ref, selector as attention_selector
from aios_renew.unified_state import observe_semantic_review_scope, observe_unified_state
from tests.git_fixture_support import materialize_git_baseline, commit_fixture_state


TASK_SOURCE = """task_id: TASK-379
revision: 1
goal: Correct explicitly selected synthetic defects.
problem: Exercise batch identity and cumulative correction.
assumptions: []
scope:
  inspect: []
  modify: [src/product.py]
non_goals: [Automatic finding selection.]
constraints:
  hard: [Preserve the original contract.]
acceptance:
  - {id: AC1, condition: First defect is corrected.}
  - {id: AC2, condition: Second defect is corrected.}
  - {id: AC3, condition: No additional material defect.}
verification:
  policy: minimum-sufficient-v1
  required: [git diff --check]
return_affinity: {kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}
"""


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def plain(value):
    return json.loads(json.dumps(value))


def selection_data(**updates):
    data = dict(source_run_id="RUN-379-001", review_id="REVIEW-379-001", task_id="TASK-379",
        task_revision=1, reviewed_sha="a" * 40, review_decision_sha="b" * 40, source_artifacts_sha="d" * 40, finding_ids=["F1", "F2"])
    data.update(updates)
    return data


def batch_payload(selection, findings):
    return dict(format="AIOS_BATCH_REMEDIATION", version=1, batch_id=selection.batch_id,
        selection=plain(asdict(selection)), findings=deepcopy(findings),
        per_finding_scope=[dict(finding_id=i, modification_scope=["src/product.py"]) for i in selection.finding_ids],
        modification_scope=["src/product.py"], affected_verification=["git diff --check"],
        constraints=["Preserve the original contract."], action="CODE_FIX", verification_policy="minimum-sufficient-v1")


def batch_fixture(tmp_path, *, foreign=False, mixed=False, authorize=True, task_number=379):
    task_source = TASK_SOURCE.replace("379", str(task_number))
    task = parse_task(task_source)
    profiles = Path(__file__).resolve().parents[1] / ".ai/executor-profiles.yaml"
    repo, remote, main = materialize_git_baseline(tmp_path, files={
        "README.md": "synthetic batch fixture\n", ".gitattributes": ".ai/** -text\n",
        f".ai/tasks/{task.task_id}.yaml": task_source, ".ai/executor-profiles.yaml": profiles.read_bytes()},
        user_name="Batch Test", user_email="batch@example.invalid", commit_message="batch baseline", remote_head_main=True)
    path = repo / "src/product.py"
    path.parent.mkdir(parents=True)
    path.write_text("first = False\nsecond = False\n", encoding="utf-8")
    candidate = commit_fixture_state(repo, paths=("src/product.py",), message="source candidate",
                                     user_name="Batch Test", user_email="batch@example.invalid")
    source = Run.from_task(run_id=f"RUN-{task_number}-001", task=task, executor="codex", base_sha=main, workspace=str(repo))
    state = tmp_path / "source-artifacts"
    state.mkdir()
    run_path, result_path = state / "run.json", state / "result.json"
    run_path.write_text(json.dumps(asdict(source)), encoding="utf-8")
    evidence = dict(evidence_id=source.run_id + "-V001", run_id=source.run_id, subject_sha=candidate,
        type="VERIFICATION", source=dict(command="git diff --check"), result=dict(exit_code=0, summary="synthetic"), raw=dict(path="synthetic.raw"))
    result_path.write_text(json.dumps(dict(result=dict(head_sha=candidate,
        claims=[dict(id="C1", satisfies=["AC1", "AC2", "AC3"], claim="Synthetic source.", evidence=[evidence["evidence_id"]])],
        changed_files=["src/product.py"], unresolved=[]), evidence=[evidence])), encoding="utf-8")
    transport_post_pass(repo, run_id=source.run_id, head_sha=candidate, run_path=run_path, result_path=result_path)
    findings = [dict(id=i, basis=b, action="CODE_FIX", location="src/product.py", issue="Defect " + i, expected="Correct " + i)
                for i, b in (("F1", "AC1"), ("F2", "AC2"))]
    if mixed:
        findings[1]["action"] = "EVIDENCE_ONLY"
    if foreign:
        findings.append(dict(id="F3", basis="AC3", action="CODE_FIX", location="src/product.py", issue="Other defect", expected="Correct it"))
    review_data = dict(review_id=f"REVIEW-{task_number}-001", reviewed_sha=candidate, mode="PRIMARY", verdict="CHANGES_REQUIRED",
        acceptance={"AC1": "FAIL", "AC2": "FAIL", "AC3": "FAIL" if foreign else "PASS"}, findings=findings)
    decision = execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW", {"run_id": source.run_id},
        {"expected_candidate_sha": candidate}, review_data), repo=repo)
    git(repo, "reset", "--hard", main)
    selection = parse_batch_selection(selection_data(source_run_id=source.run_id, task_id=task.task_id,
        review_id=review_data["review_id"], reviewed_sha=candidate, review_decision_sha=decision.canonical_sha,
        source_artifacts_sha=git(remote, "rev-parse", "refs/heads/aios/artifacts/" + source.run_id)))
    payload = batch_payload(selection, findings[:2])
    envelope = IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REMEDIATION",
        {"source_run_id": source.run_id, "batch_id": selection.batch_id, "finding_ids": list(selection.finding_ids)},
        {"expected_reviewed_sha": candidate, "expected_review_decision_sha": decision.canonical_sha}, payload)
    authorized = execute_ingress(envelope, repo=repo) if authorize else None
    return dict(repo=repo, remote=remote, main=main, task=task, selection=selection, payload=payload,
        source_review=parse_review(yaml.safe_dump(review_data)), envelope=envelope, authorized=authorized)


def execute_batch(fixture, *, selector=None, dispatch_id="batch-379"):
    calls = {"native": [], "verification": []}

    def native(command, **kwargs):
        execution = json.loads(kwargs["input"].decode().split("REMEDIATION_INPUT:\n", 1)[1])
        calls["native"].append(execution)
        subject = Path(execution["run"]["workspace"])
        (subject / "src/product.py").write_text("first = True\nsecond = True\n# correction " + execution["run"]["run_id"] + "\n", encoding="utf-8")
        git(subject, "add", "src/product.py")
        git(subject, "-c", "user.name=Batch Test", "-c", "user.email=batch@example.invalid", "commit", "--quiet", "-m", "one batch correction")
        head = git(subject, "rev-parse", "HEAD")
        package = dict(result=dict(head_sha=head, claims=[], changed_files=["src/product.py"], unresolved=[]), evidence=[])
        return subprocess.CompletedProcess(command, 0, json.dumps(package).encode(), b"")

    def verification(command, **kwargs):
        calls["verification"].append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"synthetic successful verification", b"")

    approval, outcome = op.run_approved_remediation_intent(dispatch_id, fixture["selection"].source_run_id,
        selector or fixture["selection"].batch_id, executor="codex", approver="human", repo=fixture["repo"],
        native_runner=native, verification_runner=verification)
    assert outcome.status == "SUCCEEDED"
    return outcome, calls, approval


def delta_payload(fixture, head, *, partial=False, blocked=False, new_defect=False):
    findings = [dict(id="NEW1", basis="AC3", action="CODE_FIX", location="src/product.py", issue="New material defect", expected="Correct it")] if new_defect else []
    review_id = "REVIEW-" + fixture["task"].task_id.removeprefix("TASK-") + "-002"
    return dict(format="AIOS_MULTI_FINDING_DELTA_REVIEW", version=1, review_id=review_id, reviewed_sha=head,
        mode="DELTA", verdict="BLOCKED" if blocked else "CHANGES_REQUIRED" if partial or new_defect else "PASS",
        acceptance={"AC1": "PASS", "AC2": "FAIL" if partial else "PASS", "AC3": "FAIL" if new_defect else "PASS"},
        findings=findings, batch_id=fixture["selection"].batch_id, selection=plain(asdict(fixture["selection"])),
        finding_outcomes={"F1": "PASS", "F2": "BLOCKED" if blocked else "CHANGES_REQUIRED" if partial else "PASS"})


def submit_delta(fixture, outcome, **verdict):
    execution = json.loads((op.runtime_paths(fixture["repo"]).runs / (outcome.run_id + ".json")).read_text())["execution"]
    head = json.loads((op.runtime_paths(fixture["repo"]).results / (outcome.run_id + ".json")).read_text())["result"]["head_sha"]
    payload = delta_payload(fixture, head, **verdict)
    decision = execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW", {"run_id": outcome.run_id},
        {"expected_candidate_sha": head}, payload), repo=fixture["repo"])
    return decision, head, execution


def batch_publication_fixture(tmp_path, **verdict):
    # Reproduce the TASK-410 namespace with prospective local commits, not
    # historical artifacts, hosted workflow timestamps, or production transport.
    fixture = batch_fixture(tmp_path, task_number=410)
    outcome, _, _ = execute_batch(fixture)
    decision, head, _ = submit_delta(fixture, outcome, **verdict)
    return fixture, outcome, decision, head


def result_attention_ref(fixture, run_id):
    artifact = git(fixture["remote"], "rev-parse", f"refs/heads/aios/artifacts/{run_id}")
    item = attention_selector(run_id, "RESULT", artifact)
    assert parse_signal_ref(item.signal_ref) == item
    # Operational refs point to carrier main; the actual terminal identity is
    # in the nested name. This is the production terminal-attention grammar.
    git(fixture["remote"], "update-ref", item.signal_ref, fixture["main"])
    return item.signal_ref


@pytest.mark.parametrize("attention_change", ["existing", "add", "delete", "move"])
def test_batch_nested_result_attention_publishes_exact_reviewed_source(tmp_path, monkeypatch, attention_change):
    fixture, outcome, decision, head = batch_publication_fixture(tmp_path)
    result_attention_ref(fixture, fixture["selection"].source_run_id)
    attention = attention_selector(outcome.run_id, "RESULT", git(
        fixture["remote"], "rev-parse", f"refs/heads/aios/artifacts/{outcome.run_id}")).signal_ref
    if attention_change != "add":
        result_attention_ref(fixture, outcome.run_id)

    # The old broad Git wildcard really matches these nested refs. Its unchanged
    # result differs from the old final-segment filter, reproducing the false
    # stale predicate. Normal Publisher below fails pre-fix on this same fixture.
    legacy = dict((row.split()[1], row.split()[0]) for row in git(
        fixture["repo"], "ls-remote", "--refs", "origin", "refs/heads/main",
        "refs/heads/aios/*/RUN-410-*").splitlines())
    legacy_expected = {ref: sha for ref, sha in legacy.items()
                       if ref == "refs/heads/main" or ref.rsplit("/", 1)[-1].startswith("RUN-410-")}
    assert legacy != legacy_expected

    actual_reserve, actual_snapshot = publication_module.reserve_publication, publication_module._lifecycle_snapshot
    actual_git = publication_module._git
    snapshots, main_pushes, changed = [], [], []
    token = git(fixture["remote"], "rev-parse", publication_module.PUBLICATION_RESERVATION_REF)

    def snapshot(*args, **kwargs):
        observed = actual_snapshot(*args, **kwargs)
        snapshots.append(dict(observed))
        return observed

    def reserve(*args, **kwargs):
        reservation = actual_reserve(*args, **kwargs)
        assert reservation.token_sha == token
        if not changed:
            if attention_change == "add":
                result_attention_ref(fixture, outcome.run_id)
            elif attention_change == "delete":
                git(fixture["remote"], "update-ref", "-d", attention)
            elif attention_change == "move":
                git(fixture["remote"], "update-ref", attention, head)
            changed.append(True)
        return reservation

    def record(path, *args, **kwargs):
        if args[0] == "push" and args[-1].endswith(":refs/heads/main"):
            main_pushes.append(args)
        return actual_git(path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_lifecycle_snapshot", snapshot)
    monkeypatch.setattr(publication_module, "reserve_publication", reserve)
    monkeypatch.setattr(publication_module, "_git", record)
    report = publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    assert report.outcome == "PUBLISHED" and report.cause == "NONE"
    assert report.reviewed_sha == head == git(fixture["remote"], "rev-parse", "refs/heads/main")
    assert report.lineage.decision_sha == decision.canonical_sha
    assert report.lineage.reservation_sha == token
    assert len(snapshots) >= 3 and all(item == snapshots[0] for item in snapshots)
    assert not any("/terminal-attention/" in ref for item in snapshots for ref in item)
    assert len(main_pushes) == 1
    assert f"--force-with-lease=refs/heads/main:{fixture['main']}" in main_pushes[0]
    before = git(fixture["remote"], "show-ref")
    replay = publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    assert replay.outcome == "ALREADY_PUBLISHED" and replay.reviewed_sha == head
    assert git(fixture["remote"], "show-ref") == before and len(main_pushes) == 1


def test_batch_attention_arriving_during_source_authentication_allows_publication(tmp_path, monkeypatch):
    from aios_renew import authoring_ingress

    fixture, outcome, decision, head = batch_publication_fixture(tmp_path)
    actual_query = authoring_ingress._query_matching_refs
    changed = []

    def observe(repo, remote, *patterns):
        refs = actual_query(repo, remote, *patterns)
        if not changed and patterns == ("refs/heads/main", "refs/heads/aios/*/RUN-410-*"):
            result_attention_ref(fixture, outcome.run_id)
            changed.append(True)
        return refs

    # This additional source-proof window is still governed by the out-of-scope
    # authoring_ingress reader. Keep the required behavior explicit for Runtime;
    # do not skip it, suppress its error, or replace its canonical validators.
    monkeypatch.setattr(authoring_ingress, "_query_matching_refs", observe)
    report = publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    assert changed and report.outcome == "PUBLISHED" and report.reviewed_sha == head
    assert git(fixture["remote"], "rev-parse", "refs/heads/main") == head


@pytest.mark.parametrize("change,cause", [
    ("add-terminal", "STALE_REVIEW_BINDING"),
    ("delete-artifacts", "STALE_REVIEW_BINDING"),
    ("move-artifacts", "STALE_REVIEW_BINDING"),
    ("single-authority", "STALE_REVIEW_BINDING"),
    ("batch-authority", "STALE_REVIEW_BINDING"),
    ("repair-revision", "STALE_REVIEW_BINDING"),
    ("source-review", "STALE_REVIEW_BINDING"),
    ("delta-review", "STALE_REVIEW_BINDING"),
    ("source-decision", "STALE_REVIEW_BINDING"),
    ("delta-decision", "STALE_REVIEW_BINDING"),
    ("foreign-decision", "DECISION_SET_CHANGED"),
    ("reservation-owner", "RESERVATION_CAS_FAILED"),
])
def test_batch_protected_ref_races_during_pending_reservation_stop_main(tmp_path, monkeypatch, change, cause):
    fixture, outcome, decision, head = batch_publication_fixture(tmp_path)
    result_attention_ref(fixture, outcome.run_id)
    remote = fixture["remote"]
    token = git(remote, "rev-parse", publication_module.PUBLICATION_RESERVATION_REF)
    actual_reserve, actual_git = publication_module.reserve_publication, publication_module._git
    reservations, main_pushes = [], []

    def reserve_then_race(*args, **kwargs):
        reservation = actual_reserve(*args, **kwargs)
        assert reservation.token_sha == token
        reservations.append(reservation)
        # First resume the existing PASS owner, then change a protected ref
        # after the final reservation check and before the final binding read.
        if len(reservations) == 2:
            if change == "delete-artifacts":
                git(remote, "update-ref", "-d", f"refs/heads/aios/artifacts/{outcome.run_id}")
            else:
                refs = {
                    "add-terminal": "refs/heads/aios/failure/RUN-410-003",
                    "move-artifacts": f"refs/heads/aios/artifacts/{outcome.run_id}",
                    "single-authority": "refs/heads/aios/remediation/RUN-410-001-F1",
                    "batch-authority": "refs/heads/aios/remediation/RUN-410-001-BATCH-" + "f" * 64,
                    "repair-revision": "refs/heads/aios/repair-supersession/RUN-410-001/2",
                    "source-review": "refs/heads/aios/review/RUN-410-001",
                    "delta-review": f"refs/heads/aios/review/{outcome.run_id}",
                    "source-decision": "refs/heads/aios/review-decision/RUN-410-001",
                    "delta-decision": f"refs/heads/aios/review-decision/{outcome.run_id}",
                    "foreign-decision": "refs/heads/aios/review-decision/RUN-999-001",
                    "reservation-owner": publication_module.PUBLICATION_RESERVATION_REF,
                }
                git(remote, "update-ref", refs[change], fixture["main"])
        return reservation

    def record(path, *args, **kwargs):
        if args[0] == "push" and args[-1].endswith(":refs/heads/main"):
            main_pushes.append(args)
        return actual_git(path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "reserve_publication", reserve_then_race)
    monkeypatch.setattr(publication_module, "_git", record)
    with pytest.raises(PublicationError) as raised:
        publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    report = raised.value.report
    assert len(reservations) == 2 and main_pushes == []
    assert report.cause == cause and report.classification == "STALE_BINDING"
    assert report.reviewed_sha == head and report.prior_main_sha == fixture["main"]
    assert git(remote, "rev-parse", "refs/heads/main") == fixture["main"]
    expected_token = fixture["main"] if change == "reservation-owner" else token
    assert git(remote, "rev-parse", publication_module.PUBLICATION_RESERVATION_REF) == expected_token


@pytest.mark.parametrize("fault", ["subject-sha", "run-id", "failed-evidence"])
def test_batch_nested_attention_does_not_authorize_stale_evidence(tmp_path, fault):
    fixture, outcome, decision, _ = batch_publication_fixture(tmp_path)
    result_attention_ref(fixture, outcome.run_id)
    ref = f"refs/heads/aios/artifacts/{outcome.run_id}"
    original = git(fixture["remote"], "rev-parse", ref)
    git(fixture["repo"], "checkout", "--quiet", "--detach", original)
    path = fixture["repo"] / ".ai/transport/result.json"
    package = json.loads(path.read_text(encoding="utf-8"))
    if fault == "subject-sha":
        package["evidence"][0]["subject_sha"] = fixture["main"]
    elif fault == "run-id":
        package["evidence"][0]["run_id"] = "RUN-410-999"
    else:
        package["evidence"][0]["result"]["exit_code"] = 1
    path.write_text(json.dumps(package), encoding="utf-8")
    replacement = commit_fixture_state(fixture["repo"], paths=(".ai/transport/result.json",),
        message="stale synthetic evidence", user_name="Batch Test", user_email="batch@example.invalid")
    git(fixture["repo"], "push", "--quiet", f"--force-with-lease={ref}:{original}", "origin", f"{replacement}:{ref}")
    before = git(fixture["remote"], "show-ref")
    with pytest.raises(PublicationError) as raised:
        publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    assert raised.value.report.cause == "MALFORMED_LINEAGE"
    assert git(fixture["remote"], "show-ref") == before
    assert git(fixture["remote"], "rev-parse", "refs/heads/main") == fixture["main"]


def test_batch_nested_attention_does_not_select_over_competing_pass(tmp_path):
    fixture, outcome, decision, head = batch_publication_fixture(tmp_path)
    result_attention_ref(fixture, outcome.run_id)
    repo = fixture["repo"]
    # A valid historical PASS writer on a distinct source remains a blocker,
    # even though operational attention refs have been excluded from the set.
    git(repo, "checkout", "--quiet", "-b", "fixture/competing-pass", fixture["main"])
    git(repo, "config", "branch.fixture/competing-pass.remote", "origin")
    git(repo, "config", "branch.fixture/competing-pass.merge", "refs/heads/main")
    path = repo / "src/product.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("first = True\nsecond = True\n# distinct candidate\n", encoding="utf-8")
    competing_head = commit_fixture_state(repo, paths=("src/product.py",), message="competing source",
        user_name="Batch Test", user_email="batch@example.invalid")
    run = Run.from_task(run_id="RUN-410-003", task=fixture["task"], executor="codex",
                        base_sha=fixture["main"], workspace=str(repo))
    state = tmp_path / "competing-artifacts"
    state.mkdir()
    run_path, result_path = state / "run.json", state / "result.json"
    run_path.write_text(json.dumps(asdict(run)), encoding="utf-8")
    evidence = dict(evidence_id=run.run_id + "-V001", run_id=run.run_id, subject_sha=competing_head,
        type="VERIFICATION", source=dict(command="git diff --check"),
        result=dict(exit_code=0, summary="synthetic"), raw=dict(path="synthetic.raw"))
    result_path.write_text(json.dumps(dict(result=dict(head_sha=competing_head,
        claims=[dict(id="C1", satisfies=["AC1", "AC2", "AC3"], claim="Synthetic competing source.",
                     evidence=[evidence["evidence_id"]])],
        changed_files=["src/product.py"], unresolved=[]), evidence=[evidence])), encoding="utf-8")
    transport_post_pass(repo, run_id=run.run_id, head_sha=competing_head, run_path=run_path, result_path=result_path)
    review_path = repo / ".ai/reviews/REVIEW-410-003.yaml"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(yaml.safe_dump(dict(review_id="REVIEW-410-003", reviewed_sha=competing_head,
        mode="PRIMARY", verdict="PASS", acceptance={"AC1": "PASS", "AC2": "PASS", "AC3": "PASS"}, findings=[])),
        encoding="utf-8")
    competing_decision = commit_fixture_state(repo, paths=(".ai/reviews/REVIEW-410-003.yaml",),
        message="historical competing PASS", user_name="Batch Test", user_email="batch@example.invalid",
        remote=fixture["remote"], remote_ref=f"refs/heads/aios/review-decision/{run.run_id}")
    before = git(fixture["remote"], "show-ref")
    with pytest.raises(PublicationError) as raised:
        publish_review_decision(repo, run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    report = raised.value.report
    assert report.cause == "COMPETING_REVIEWED_SOURCE" and report.classification == "COMPETING_SOURCE"
    assert report.reviewed_sha == head and report.blocker.source_run == run.run_id
    assert report.blocker.reviewed_sha == competing_head and report.blocker.decision_sha == competing_decision
    assert git(fixture["remote"], "show-ref") == before
    assert git(fixture["remote"], "rev-parse", "refs/heads/main") == fixture["main"]


@pytest.mark.parametrize("namespace", ["review", "review-decision", "artifacts"])
def test_batch_initial_snapshot_cannot_replace_event_lineage(tmp_path, monkeypatch, namespace):
    fixture, outcome, decision, head = batch_publication_fixture(tmp_path)
    actual_snapshot = publication_module._lifecycle_snapshot
    changed = []

    def snapshot(*args, **kwargs):
        if not changed:
            git(fixture["remote"], "update-ref", f"refs/heads/aios/{namespace}/{outcome.run_id}", fixture["main"])
            changed.append(True)
        return actual_snapshot(*args, **kwargs)

    monkeypatch.setattr(publication_module, "_lifecycle_snapshot", snapshot)
    with pytest.raises(PublicationError) as raised:
        publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    assert raised.value.report.cause == "STALE_REVIEW_BINDING"
    assert raised.value.report.reviewed_sha == head
    assert git(fixture["remote"], "rev-parse", "refs/heads/main") == fixture["main"]


def test_batch_nested_attention_already_included_source_is_idempotent(tmp_path):
    fixture, outcome, decision, head = batch_publication_fixture(tmp_path)
    result_attention_ref(fixture, outcome.run_id)
    report = publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    assert report.outcome == "PUBLISHED"
    tree = git(fixture["repo"], "rev-parse", head + "^{tree}")
    descendant = git(fixture["repo"], "-c", "user.name=Batch Test", "-c", "user.email=batch@example.invalid",
        "commit-tree", tree, "-p", head, "-m", "later main includes exact reviewed source")
    git(fixture["remote"], "fetch", "--no-tags", str(fixture["repo"]), descendant)
    git(fixture["remote"], "update-ref", "refs/heads/main", descendant, head)
    before = git(fixture["remote"], "show-ref")
    replay = publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    assert replay.outcome == "ALREADY_INCLUDED" and replay.reviewed_sha == head
    assert replay.prior_main_sha == descendant and replay.lineage.decision_sha == decision.canonical_sha
    assert git(fixture["remote"], "show-ref") == before


def test_two_findings_one_executor_run_episode_and_publication(tmp_path):
    fixture = batch_fixture(tmp_path)
    replay = execute_ingress(fixture["envelope"], repo=fixture["repo"])
    assert replay.replayed and replay.canonical_sha == fixture["authorized"].canonical_sha
    outcome, calls, _ = execute_batch(fixture)
    assert len(calls["native"]) == len(calls["verification"]) == 1
    assert [f["id"] for f in calls["native"][0]["findings"]] == ["F1", "F2"]
    assert "finding" not in calls["native"][0]
    scope = observe_semantic_review_scope("TASK-379", repo=fixture["repo"])
    assert scope["version"] == 2 and scope["batch_id"] == fixture["selection"].batch_id
    decision, head, execution = submit_delta(fixture, outcome)
    assert execution["run"]["base_sha"] == fixture["selection"].reviewed_sha
    observed = observe_unified_state("TASK-379", repo=fixture["repo"])
    assert observed.next_action == "PUBLICATION" and not observed.outstanding_findings
    publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    assert git(fixture["remote"], "rev-parse", "refs/heads/main") == head


@pytest.mark.parametrize("verdict", [{"partial": True}, {"blocked": True}, {"new_defect": True}])
def test_partial_blocked_and_new_defects_retain_full_frontier_and_block_publication(tmp_path, verdict):
    fixture = batch_fixture(tmp_path, foreign=True)
    outcome, _, _ = execute_batch(fixture)
    decision, head, _ = submit_delta(fixture, outcome, **verdict)
    result_attention_ref(fixture, outcome.run_id)
    state = observe_unified_state("TASK-379", repo=fixture["repo"])
    keys = {(i["source_run_id"], i["finding_id"]) for i in state.outstanding_findings}
    assert ("RUN-379-001", "F3") in keys and ("RUN-379-001", "F1") not in keys
    assert (("RUN-379-001", "F2") in keys) == bool(verdict.get("partial") or verdict.get("blocked"))
    if verdict.get("new_defect"):
        assert (outcome.run_id, "NEW1") in keys
    before = git(fixture["remote"], "rev-parse", "refs/heads/main")
    with pytest.raises(PublicationError):
        publish_review_decision(fixture["repo"], run_id=outcome.run_id, decision_sha=decision.canonical_sha)
    assert git(fixture["remote"], "rev-parse", "refs/heads/main") == before


@pytest.mark.parametrize("fault", ["duplicate", "implicit", "oversized", "wrong-task", "bool-revision", "bad-sha"])
def test_batch_selection_is_explicit_bounded_and_exact(fault):
    data = selection_data()
    if fault == "duplicate": data["finding_ids"] = ["F1", "F1"]
    elif fault == "implicit": data["finding_ids"] = ["F1"]
    elif fault == "oversized": data["finding_ids"] = [f"F{i}" for i in range(33)]
    elif fault == "wrong-task": data["task_id"] = "RUN-379-001"
    elif fault == "bool-revision": data["task_revision"] = True
    else: data["reviewed_sha"] = "not-a-sha"
    with pytest.raises(ReviewValidationError): parse_batch_selection(data)


@pytest.mark.parametrize("fault", ["mixed", "foreign", "stale", "substituted", "scope", "constraints", "verification"])
def test_authoring_rejects_invalid_batch_without_canonical_mutation(tmp_path, fault):
    fixture = batch_fixture(tmp_path, mixed=fault == "mixed", authorize=False)
    payload = deepcopy(fixture["payload"])
    if fault == "foreign": payload["findings"][1]["id"] = "FOREIGN"
    elif fault == "stale": payload["selection"]["reviewed_sha"] = "f" * 40
    elif fault == "substituted": payload["findings"][1]["issue"] = "Substituted semantic subject"
    elif fault == "scope": payload["modification_scope"] = ["README.md"]
    elif fault == "constraints": payload["constraints"] = []
    elif fault == "verification": payload["affected_verification"] = ["git status --porcelain"]
    before = git(fixture["remote"], "for-each-ref", "--format=%(refname) %(objectname)")
    with pytest.raises((AuthoringIngressError, ReviewValidationError)):
        execute_ingress(replace(fixture["envelope"], payload=payload), repo=fixture["repo"])
    assert git(fixture["remote"], "for-each-ref", "--format=%(refname) %(objectname)") == before


@pytest.mark.parametrize("fault", ["missing", "wrong-run", "wrong-sha", "nonzero", "duplicate", "extra-command"])
def test_forged_batch_evidence_fails_package_gate(tmp_path, fault):
    fixture = batch_fixture(tmp_path)
    outcome, _, _ = execute_batch(fixture)
    state = op.runtime_paths(fixture["repo"])
    run_data = json.loads((state.runs / (outcome.run_id + ".json")).read_text())
    execution = op._remediation_execution_from_data(run_data["execution"])
    package = json.loads((state.results / (outcome.run_id + ".json")).read_text())
    evidence = package["evidence"]
    if fault == "missing": evidence.clear()
    elif fault == "wrong-run": evidence[0]["run_id"] = "RUN-379-999"
    elif fault == "wrong-sha": evidence[0]["subject_sha"] = "0" * 40
    elif fault == "nonzero": evidence[0]["result"]["exit_code"] = 1
    elif fault == "duplicate": evidence.append(deepcopy(evidence[0]))
    else: evidence[0]["source"]["command"] = "git status --porcelain"
    with pytest.raises(ValueError):
        _validate_remediation_package(fixture["repo"], source_sha=package["result"]["head_sha"], task=fixture["task"],
            run=execution.run, remediation=execution.remediation, prior_review=fixture["source_review"],
            result=validate_result(package["result"]), evidence=tuple(validate_evidence(e) for e in evidence),
            execution_base_sha=execution.run.base_sha)


def test_batch_frontier_rejects_missing_foreign_and_replayed_outcomes():
    selected = parse_batch_selection(selection_data())
    findings = [dict(id=i, basis=b, action="CODE_FIX", location="src/product.py", issue=i, expected="fix")
                for i, b in (("F1", "AC1"), ("F2", "AC2"))]
    source = parse_review(yaml.safe_dump(dict(review_id=selected.review_id, reviewed_sha=selected.reviewed_sha,
        mode="PRIMARY", verdict="CHANGES_REQUIRED", acceptance={"AC1": "FAIL", "AC2": "FAIL"}, findings=findings)))
    frontier = CorrectionFrontier.from_primary(selected.source_run_id, source)
    fixture = {"selection": selected}
    delta = parse_review(yaml.safe_dump(delta_payload(fixture, "c" * 40)))
    assert frontier.advance(delta_run_id="RUN-379-002", delta_review=delta, predecessor=selected).is_empty
    with pytest.raises(CorrectionFrontierError):
        CorrectionFrontier(()).advance(delta_run_id="RUN-379-003", delta_review=delta, predecessor=selected)
    for outcomes in ({"F1": "PASS"}, {"F1": "PASS", "FOREIGN": "PASS"}):
        with pytest.raises(ReviewValidationError):
            parse_review(yaml.safe_dump({**delta_payload(fixture, "c" * 40), "finding_outcomes": outcomes}))


def test_batch_never_decodes_as_single_finding():
    selection = parse_batch_selection(selection_data())
    payload = batch_payload(selection, [])
    payload.pop("format")
    payload["finding_id"] = "F1"
    with pytest.raises(ReviewValidationError): parse_remediation(yaml.safe_dump(payload))
    legacy = parse_remediation("finding_id: F1\naction: CODE_FIX\nreviewed_sha: legacy-sha\n")
    assert not isinstance(legacy, BatchRemediation) and legacy.finding_id == "F1"


def test_batch_success_replay_conflicts_and_wrong_base_fail_before_second_invocation(tmp_path):
    from aios_renew.correction_dispatch import CorrectionDispatchError, reject_existing_selector_collision, existing_correction_profile
    fixture = batch_fixture(tmp_path)
    outcome, _, _ = execute_batch(fixture)
    state = op.runtime_paths(fixture["repo"])
    _, profile = existing_correction_profile(state_root=state.root, repo_root=fixture["repo"], correction_dispatch_id="batch-379")
    with pytest.raises(CorrectionDispatchError):
        reject_existing_selector_collision(state_root=state.root, repo_root=fixture["repo"], correction_dispatch_id="batch-379",
            source_run_id=fixture["selection"].source_run_id, finding_id="F1", executor="codex", execution_profile=profile)
    run_data = json.loads((state.runs / (outcome.run_id + ".json")).read_text())
    from aios_renew.publication import _parse_remediation_run
    run_data["execution_base"]["candidate_sha"] = fixture["main"]
    with pytest.raises(ValueError):
        _parse_remediation_run(run_data, run_id=outcome.run_id)
    # A new delivery cannot consume the same immutable batch a second time.
    with pytest.raises(op.OperatorError):
        op.run_remediation("TASK-379", finding_id=fixture["selection"].batch_id,
            source_run_id=fixture["selection"].source_run_id,
            approved_remediation_sha=fixture["authorized"].canonical_sha, executor="codex", repo=fixture["repo"],
            native_runner=lambda *a, **k: pytest.fail("consumed batch reinvoked Executor"))


def test_historical_single_finding_authoring_execution_and_delta_remain_valid(tmp_path):
    fixture = batch_fixture(tmp_path, authorize=False)
    initial = fixture["envelope"]
    payload = dict(finding_id="F1", action="CODE_FIX", reviewed_sha=fixture["selection"].reviewed_sha,
        modification_scope=["src/product.py"], constraints=["Preserve the original contract."],
        verification=dict(policy="minimum-sufficient-v1", affected=["git diff --check"]))
    execute_ingress(replace(initial, identity={"source_run_id": fixture["selection"].source_run_id, "finding_id": "F1"},
        payload=payload), repo=fixture["repo"])
    outcome, calls, _ = execute_batch(fixture, selector="F1", dispatch_id="single-379")
    assert len(calls["native"]) == len(calls["verification"]) == 1
    assert calls["native"][0]["finding"]["id"] == "F1" and "findings" not in calls["native"][0]
    state = op.runtime_paths(fixture["repo"])
    head = json.loads((state.results / (outcome.run_id + ".json")).read_text())["result"]["head_sha"]
    delta = dict(review_id="REVIEW-379-002", reviewed_sha=head, mode="DELTA", verdict="PASS", prior_finding_id="F1",
                 acceptance={"AC1": "PASS"}, findings=[])
    with pytest.raises(AuthoringIngressError, match="cannot reinterpret"):
        execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW", {"run_id": outcome.run_id},
            {"expected_candidate_sha": head}, delta_payload(fixture, head)), repo=fixture["repo"])
    execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW", {"run_id": outcome.run_id},
        {"expected_candidate_sha": head}, delta), repo=fixture["repo"])
    observed = observe_unified_state("TASK-379", repo=fixture["repo"])
    assert [f["finding_id"] for f in observed.outstanding_findings] == ["F2"]


def test_batch_authority_authenticates_from_deep_observer(tmp_path):
    from aios_renew.review_transport import resolve_authenticated_batch

    fixture = batch_fixture(tmp_path)
    selected = fixture["selection"]
    # Runtime's isolated TEMP adds enough depth for `show commit:path` to fail
    # Windows filename probing even though the committed metadata blob exists.
    with TemporaryDirectory(prefix="batch-read-") as folder:
        observer = Path(folder) / ("observer-" + "x" * max(0, 140 - len(folder)))
        observer.mkdir()
        git(observer, "init", "--quiet")
        git(observer, "remote", "add", "origin", str(fixture["remote"]))
        binding, authorized, sha = resolve_authenticated_batch(
            observer, source_run_id=selected.source_run_id, batch_id=selected.batch_id,
            required_sha=fixture["authorized"].canonical_sha, remote="origin")

    assert binding.review == fixture["source_review"]
    assert plain(asdict(authorized)) == fixture["payload"]
    assert sha == fixture["authorized"].canonical_sha


def test_batch_source_artifacts_cannot_be_substituted_after_authoring(tmp_path):
    from aios_renew.review_transport import resolve_authenticated_batch, ReviewTransportError
    fixture = batch_fixture(tmp_path)
    selected = fixture["selection"]
    old = selected.source_artifacts_sha
    tree = git(fixture["repo"], "rev-parse", old + "^{tree}")
    moved = git(fixture["repo"], "-c", "user.name=Batch Test", "-c", "user.email=batch@example.invalid",
        "commit-tree", tree, "-p", old, "-m", "substituted source artifact identity")
    git(fixture["remote"], "fetch", "--no-tags", str(fixture["repo"]), moved)
    git(fixture["remote"], "update-ref", "refs/heads/aios/artifacts/" + selected.source_run_id, moved)
    with pytest.raises((ReviewTransportError, AuthoringIngressError)):
        resolve_authenticated_batch(fixture["repo"], source_run_id=selected.source_run_id, batch_id=selected.batch_id)
    assert not list(op.runtime_paths(fixture["repo"]).runs.glob("*.json"))


def test_partial_batch_followup_single_uses_latest_candidate_and_closes_complete_frontier(tmp_path):
    fixture = batch_fixture(tmp_path)
    batch, _, _ = execute_batch(fixture)
    _, batch_head, _ = submit_delta(fixture, batch, partial=True)
    # A fresh authoring checkout has no execution records to confuse canonical
    # Human/Brain authority with local Runtime state.
    author = tmp_path / "followup-author"
    subprocess.run(["git", "clone", "--quiet", str(fixture["remote"]), str(author)], check=True, capture_output=True)
    selected = fixture["selection"]
    single = dict(finding_id="F2", action="CODE_FIX", reviewed_sha=selected.reviewed_sha,
        modification_scope=["src/product.py"], constraints=["Preserve the original contract."],
        verification=dict(policy="minimum-sufficient-v1", affected=["git diff --check"]))
    execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REMEDIATION",
        {"source_run_id": selected.source_run_id, "finding_id": "F2"}, fixture["envelope"].expected_state, single), repo=author)
    followup, calls, _ = execute_batch(fixture, selector="F2", dispatch_id="followup-single")
    assert calls["native"][0]["run"]["base_sha"] == batch_head
    assert calls["native"][0]["remediation"]["reviewed_sha"] == selected.reviewed_sha
    state = op.runtime_paths(fixture["repo"])
    head = json.loads((state.results / (followup.run_id + ".json")).read_text())["result"]["head_sha"]
    review = dict(review_id="REVIEW-379-003", reviewed_sha=head, mode="DELTA", verdict="PASS", prior_finding_id="F2",
        acceptance={"AC2": "PASS"}, findings=[])
    decision = execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW", {"run_id": followup.run_id},
        {"expected_candidate_sha": head}, review), repo=fixture["repo"])
    assert not observe_unified_state("TASK-379", repo=fixture["repo"]).outstanding_findings
    publish_review_decision(fixture["repo"], run_id=followup.run_id, decision_sha=decision.canonical_sha)
    assert git(fixture["remote"], "rev-parse", "refs/heads/main") == head
