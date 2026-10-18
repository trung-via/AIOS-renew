"""Synthetic independent GitHub Human intent plus real local canonical lineage.

Only the authenticated GitHub HTTP adapter is replaced. No private authority
reader is replaced, and fixture PASS bytes confer no live approval or evidence.
"""

import copy
import json
import subprocess
from dataclasses import asdict

import pytest

from aios_renew import github_issue_ingress as carrier
from aios_renew import review_transport as transport
from aios_renew.authoring_ingress import AuthoringIngressError, execute_ingress, parse_envelope
from tests.test_github_issue_ingress import POLICY, _write_policy
from tests.test_lean_vp_l1_review_authority import (
    RUN_ID, assert_not_established, bind, fixture_ref, install_decision, make_reviewed_package,
)
from tests.test_lean_vp_l1_source_binding import commit, encoded, state_without_objects


def selection_fixture(tmp_path, monkeypatch, **options):
    package = make_reviewed_package(tmp_path, human_policy=POLICY, **options)
    selected = package["selection"]
    request = dict(format="AIOS_INGRESS_ENVELOPE", version=1, operation="SELECT_MAPPING_REVIEW",
        identity=dict(target_task_id=selected.scope.target_task.task_id),
        expected_state=dict(expected_main_sha=package["main"], expected_remote_url=package["remote"].as_posix()),
        payload=dict(scope=asdict(selected.scope), sources={role: asdict(source)
            for role, source in package["sources"].items()}, review_task_source=asdict(selected.review_task_source),
            run=dict(id=RUN_ID, sha256=selected.run_sha256, base_sha=selected.base_sha,
                     candidate_sha=selected.reviewed_sha)))
    # JSON is the sole new carrier encoding; lifecycle YAML remains unchanged.
    request = json.loads(encoded(request))
    user = dict(login="trung-via", id=1234, type="User")
    issue = dict(number=107, id=2107, node_id="I_synthetic_107", title=POLICY["github_issue"]["title_marker"],
        user=user, body=encoded(request).decode(), repository_url="https://api.github.com/repos/trung-via/AIOS-renew",
        created_at="2026-10-18T00:00:00Z", updated_at="2026-10-18T00:00:00Z")
    repository = dict(full_name=POLICY["github_issue"]["repository"], id=500)
    event = dict(action="opened", repository=repository, issue=copy.deepcopy(issue), sender=copy.deepcopy(user))
    package.update(request=request, issue=issue, event=event, event_path=tmp_path / "event.json",
                   policy_path=_write_policy(tmp_path), repository=repository, last_edited_at=None)

    def api(repository_name, suffix):
        assert repository_name == POLICY["github_issue"]["repository"]
        if suffix == "":
            return copy.deepcopy(package["repository"])
        if suffix == f"/issues/{package['issue']['number']}":
            return copy.deepcopy(package["issue"])
        if suffix == f"/selection-edit-history/{package['issue']['number']}":
            actual = package["issue"]
            return dict(data=dict(repository=dict(nameWithOwner=repository_name, databaseId=500,
                issue=dict(id=actual["node_id"], number=actual["number"], body=actual["body"],
                    title=actual["title"], createdAt=actual["created_at"], lastEditedAt=package["last_edited_at"]))))
        raise carrier.GitHubIssueIngressError("synthetic Issue unavailable")

    monkeypatch.setattr(carrier, "_github_selection_json", api)
    write_event(package)
    return package


def write_event(package, *, update_body=False):
    if update_body:
        package["issue"]["body"] = encoded(package["request"]).decode()
        package["event"]["issue"]["body"] = package["issue"]["body"]
    package["event_path"].write_bytes(encoded(package["event"]))


def deliver(package):
    return carrier.deliver_event(package["event_path"], package["policy_path"], repo=package["repo"])


def selection_ref(package):
    request = package["request"]
    return transport._l1_selection_ref(request["identity"]["target_task_id"], request["payload"]["scope"]["candidate_sha"])


def test_authenticated_selection_then_independent_complete_published_review(tmp_path, monkeypatch):
    package = selection_fixture(tmp_path, monkeypatch, inspect_contract_only=True)
    assert_not_established(bind(package))
    main = (package["remote"] / "refs/heads/main").read_bytes()
    delivery = deliver(package)
    assert delivery.ingress_result.operation == "SELECT_MAPPING_REVIEW"
    assert delivery.github_outputs() == "" and delivery.publication_run_id is None
    assert delivery.repair_dispatch_id is None
    assert (package["remote"] / "refs/heads/main").read_bytes() == main
    record = transport._l1_selection_record(package["repo"], delivery.ingress_result.canonical_sha)
    assert set(record) == {"format", "version", "request", "provenance", "policy"}
    assert record["provenance"]["issue_id"] == package["issue"]["id"]
    value = bind(package)
    assert value.reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"
    assert value.human_intent == "AUTHENTICATED_SELECTION"
    assert value.lineage.human_selection_sha == delivery.ingress_result.canonical_sha
    assert value.lineage.human_event_identity == "github-issue:trung-via/AIOS-renew#107@trung-via"
    assert value.canonical_verification == "UNESTABLISHED"
    assert value.future_version_admission == "NOT_ADMITTED"
    assert value.production_activation == "NOT_ACTIVATED" and not value.execution_authorized


def test_identical_replay_is_immutable_but_another_human_event_conflicts(tmp_path, monkeypatch):
    package = selection_fixture(tmp_path, monkeypatch)
    first = deliver(package)
    before = state_without_objects(package["remote"])
    replay = deliver(package)
    assert replay.ingress_result.status == "IDEMPOTENT" and replay.ingress_result.replayed
    assert replay.ingress_result.canonical_sha == first.ingress_result.canonical_sha
    assert state_without_objects(package["remote"]) == before
    package["issue"].update(number=108, id=2108, node_id="I_synthetic_108")
    package["event"]["issue"] = copy.deepcopy(package["issue"])
    write_event(package)
    with pytest.raises(AuthoringIngressError, match="divergent"):
        deliver(package)
    assert state_without_objects(package["remote"]) == before


@pytest.mark.parametrize("fault", ["actor", "sender", "copied-body", "issue-id", "repository-id", "edited", "bot"])
def test_event_and_copied_actor_records_never_authenticate_intent(tmp_path, monkeypatch, fault):
    package = selection_fixture(tmp_path, monkeypatch)
    if fault == "actor":
        package["issue"]["user"]["login"] = "not-allowlisted"
    elif fault == "sender":
        package["event"]["sender"]["id"] = 9999
    elif fault == "copied-body":
        package["issue"]["body"] = "a different authentic Human request"
    elif fault == "issue-id":
        package["event"]["issue"]["id"] += 1
    elif fault == "repository-id":
        package["event"]["repository"] = dict(full_name=package["repository"]["full_name"], id=999)
    elif fault == "edited":
        package["last_edited_at"] = "2026-10-18T00:00:01Z"
    else:
        package["issue"]["user"]["type"] = "Bot"
    write_event(package)
    before = state_without_objects(package["remote"])
    with pytest.raises((AuthoringIngressError, carrier.GitHubIssueIngressError)):
        deliver(package)
    assert state_without_objects(package["remote"]) == before


@pytest.mark.parametrize("fault", ["target", "candidate", "review-task", "acceptance", "mapping", "run", "unknown", "version"])
def test_authenticated_human_choice_cannot_swap_exact_subjects(tmp_path, monkeypatch, fault):
    package = selection_fixture(tmp_path, monkeypatch)
    request = package["request"]
    scope = request["payload"]["scope"]
    if fault == "target":
        request["identity"]["target_task_id"] = "TASK-OTHER"
    elif fault == "candidate":
        scope["candidate_sha"] = "e" * 40
    elif fault == "review-task":
        scope["review_task"]["task_id"] = scope["target_task"]["task_id"]
    elif fault == "acceptance":
        scope["review_task"]["acceptance_ids"] = ["MR1"]
    elif fault == "mapping":
        request["payload"]["sources"]["mapping"]["blob_sha"] = "e" * 40
    elif fault == "run":
        request["payload"]["run"]["id"] = "RUN-377-002"
    elif fault == "unknown":
        request["payload"]["reviewer"] = "PASS"
    else:
        request["version"] = True
    write_event(package, update_body=True)
    before = state_without_objects(package["remote"])
    with pytest.raises((AuthoringIngressError, carrier.GitHubIssueIngressError)):
        deliver(package)
    assert state_without_objects(package["remote"]) == before


def test_stale_main_rejects_initial_choice_and_replay(tmp_path, monkeypatch):
    package = selection_fixture(tmp_path, monkeypatch)
    deliver(package)
    (package["repo"] / "subject.txt").write_bytes(b"later canonical main\n")
    package["main"] = commit(package["repo"], package["remote"], ["subject.txt"])
    with pytest.raises(AuthoringIngressError, match="stale"):
        deliver(package)
    # The already immutable choice remains observable when its sources survive.
    assert bind(package).reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"


def test_transport_receipt_and_issue_close_do_not_revoke_original_body_intent(tmp_path, monkeypatch):
    package = selection_fixture(tmp_path, monkeypatch)
    deliver(package)
    package["issue"].update(updated_at="2026-10-18T00:01:00Z", state="closed")
    assert deliver(package).ingress_result.status == "IDEMPOTENT"
    assert bind(package).reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"


@pytest.mark.parametrize("fault", ["missing-review", "incomplete-review", "changed-decision", "moved-selection", "spoofed-record", "revoked-human"])
def test_human_selection_alone_or_conflicting_lineage_never_elevates(tmp_path, monkeypatch, fault):
    package = selection_fixture(tmp_path, monkeypatch)
    delivery = deliver(package)
    if fault == "missing-review":
        (package["remote"] / f"refs/heads/aios/review-decision/{RUN_ID}").unlink()
    elif fault == "incomplete-review":
        package["review"]["acceptance"].pop("MR2")
        install_decision(package)
    elif fault == "changed-decision":
        install_decision(package, extra={"proof/mapping.json": b"{}"})
    elif fault == "moved-selection":
        (package["remote"] / selection_ref(package)).write_text(package["base"] + "\n", encoding="ascii")
    elif fault == "spoofed-record":
        record = transport._l1_selection_record(package["repo"], delivery.ingress_result.canonical_sha)
        record["provenance"]["actor_id"] = 9999
        fixture_ref(package, selection_ref(package), {**package["candidate_files"],
            transport.L1_SELECTION_PATH: json.dumps(record, sort_keys=True, separators=(",", ":")).encode()},
            parents=(package["main"],))
    else:
        package["issue"]["user"]["login"] = "not-allowlisted"
    value = bind(package)
    assert_not_established(value)
    if fault == "missing-review":
        assert value.human_intent == "AUTHENTICATED_SELECTION"


@pytest.mark.parametrize("inspect_only", [False, True])
def test_changed_contract_requires_explicit_modify_in_authenticated_choice(tmp_path, monkeypatch, inspect_only):
    package = selection_fixture(tmp_path, monkeypatch, changed_contract=True, inspect_contract_only=inspect_only)
    if inspect_only:
        with pytest.raises(AuthoringIngressError, match="modify"):
            deliver(package)
    else:
        deliver(package)
        assert bind(package).reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"


def test_reader_rechecks_selection_ref_after_review_observation(tmp_path, monkeypatch):
    package = selection_fixture(tmp_path, monkeypatch)
    deliver(package)
    real = transport._exact_remote_refs
    calls = 0

    def observe(repo, url, *patterns):
        nonlocal calls
        if selection_ref(package) in patterns:
            calls += 1
            if calls == 4:
                (package["remote"] / selection_ref(package)).write_text(package["base"] + "\n", encoding="ascii")
        return real(repo, url, *patterns)

    monkeypatch.setattr(transport, "_exact_remote_refs", observe)
    assert_not_established(bind(package))


def test_competing_selection_namespace_is_rejected_without_fallback(tmp_path, monkeypatch):
    package = selection_fixture(tmp_path, monkeypatch)
    fixture_ref(package, selection_ref(package) + "/competing", {"unrelated.txt": b"synthetic"})
    before = state_without_objects(package["remote"])
    with pytest.raises(AuthoringIngressError, match="competing"):
        deliver(package)
    assert state_without_objects(package["remote"]) == before
    assert_not_established(bind(package))


def test_reader_is_read_only_with_authenticated_selection_and_review(tmp_path, monkeypatch):
    package = selection_fixture(tmp_path, monkeypatch)
    deliver(package)
    before = state_without_objects(package["repo"]), state_without_objects(package["remote"])
    real = subprocess.run

    def read_only(argv, *args, **kwargs):
        assert argv[0] == "git"
        tail = list(argv[3:])
        if tail[0] == "--no-replace-objects":
            tail = tail[1:]
            while tail[0] == "-c":
                tail = tail[2:]
        assert tail[0] in {"symbolic-ref", "config", "remote", "ls-remote", "fetch", "rev-parse", "cat-file", "ls-tree"}
        if tail[0] == "config":
            assert tail[1].startswith("--get")
        if tail[0] == "fetch":
            assert {"--no-tags", "--no-write-fetch-head", "--refmap=", "--no-auto-maintenance",
                    "--no-recurse-submodules"} <= set(tail)
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", read_only)
    assert bind(package).reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"
    assert (state_without_objects(package["repo"]), state_without_objects(package["remote"])) == before


def test_generic_ingress_cannot_treat_a_copied_selection_as_authority(tmp_path, monkeypatch):
    package = selection_fixture(tmp_path, monkeypatch)
    with pytest.raises(AuthoringIngressError, match="authenticated GitHub"):
        execute_ingress(parse_envelope(encoded(package["request"])), repo=package["repo"])
    assert_not_established(bind(package))
