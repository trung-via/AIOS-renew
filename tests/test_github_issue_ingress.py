from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from aios_renew.authoring_ingress import AuthoringIngressError, IngressResult
from aios_renew import github_issue_ingress as carrier


ROOT = Path(__file__).resolve().parents[1]
BRAIN_INGRESS_WORKFLOW = ROOT / ".github/workflows/aios-brain-ingress.yml"
SELF_HOSTED_REPAIR_WORKFLOW = ROOT / ".github/workflows/aios-self-hosted-repair-wakeup.yml"

POLICY = {
    "format": "AIOS_BRAIN_INGRESS_CARRIERS_POLICY",
    "version": 1,
    "github_issue": {
        "enabled": True,
        "repository": "trung-via/AIOS-renew",
        "authorized_actors": ["trung-via"],
        "title_marker": "[AIOS BRAIN INGRESS]",
        "max_body_bytes": 131072,
    },
}


def test_selection_http_requires_independent_credentials_without_network(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(carrier.urllib.request, "build_opener", lambda *a: pytest.fail("unauthenticated request"))
    with pytest.raises(carrier.GitHubIssueIngressError, match="authenticated GitHub"):
        carrier._github_selection_json(POLICY["github_issue"]["repository"], "/issues/107")


def test_selection_has_no_lifecycle_wakeup_or_result_claim():
    result = IngressResult(operation="SELECT_MAPPING_REVIEW", canonical_sha="a" * 40,
        canonical_destination="refs/heads/aios/l1-human-selection/TASK-1/" + "b" * 40,
        detail="Authenticated Human choice only; no review, publication or activation result.")
    delivery = carrier.IssueDelivery("github-issue:synthetic/repo#1@human", result)
    assert delivery.github_outputs() == ""
    assert delivery.publication_run_id is None and delivery.repair_dispatch_id is None
    workflow = yaml.load(BRAIN_INGRESS_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    step = next(item for item in workflow["jobs"]["deliver"]["steps"] if item.get("id") == "ingress")
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_selection_http_is_fixed_authenticated_bounded_and_read_only(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "synthetic-token")
    observations = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, maximum):
            assert maximum == carrier._MAX_EVENT_FILE_BYTES + 1
            return b'{"synthetic":true}'

    class Opener:
        def open(self, request, *, timeout):
            assert timeout == 15 and request.get_header("Authorization") == "Bearer synthetic-token"
            observations.append(request)
            return Response()

    def opener(handler):
        assert handler.redirect_request(None, None, None, None, None, None) is None
        return Opener()

    monkeypatch.setattr(carrier.urllib.request, "build_opener", opener)
    repository = POLICY["github_issue"]["repository"]
    carrier._github_selection_json(repository, "/issues/107")
    carrier._github_selection_json(repository, "/selection-edit-history/107")
    assert observations[0].get_method() == "GET"
    assert observations[0].full_url == "https://api.github.com/repos/trung-via/AIOS-renew/issues/107"
    assert observations[1].full_url == "https://api.github.com/graphql"
    query = json.loads(observations[1].data)
    assert query["query"].startswith("query(") and "mutation" not in query["query"]
    assert query["variables"] == dict(owner="trung-via", name="AIOS-renew", number=107)
    with pytest.raises(carrier.GitHubIssueIngressError, match="bounded contract"):
        carrier._github_selection_json(repository, "/arbitrary")


def _write_policy(tmp_path: Path, policy: object = POLICY) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    return path


def _event(body: object = "opaque body") -> dict[str, object]:
    return {
        "action": "opened",
        "repository": {"full_name": "trung-via/AIOS-renew"},
        "issue": {
            "number": 107,
            "title": "[AIOS BRAIN INGRESS]",
            "user": {"login": "trung-via"},
            "body": body,
        },
    }


def _write_event(tmp_path: Path, event: object) -> Path:
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
    return path


def origin_carrier_fixture(tmp_path, monkeypatch):
    from dataclasses import asdict
    from tests.test_authoring_ingress import setup_test_repo, origin_authoring_fixture
    repo, remote, main_sha = setup_test_repo(tmp_path)
    registry, envelope, admission = origin_authoring_fixture(tmp_path, repo, main_sha)
    for key, value in {"GITHUB_REPOSITORY": "trung-via/AIOS-renew", "GITHUB_RUN_ID": "900",
                       "GITHUB_RUN_ATTEMPT": "1", "RUNNER_ENVIRONMENT": "self-hosted",
                       "AIOS_ORIGIN_REGISTRY": str(registry.path), "AIOS_ORIGIN_ADMISSION_KEY": "c" * 64}.items():
        monkeypatch.setenv(key, value)
    body = asdict(envelope)
    del body["audited_handoff"]
    event = _write_event(tmp_path, _event(json.dumps(body)))
    policy = _write_policy(tmp_path)
    receipt = tmp_path / "admission.json"
    return repo, remote, main_sha, registry, envelope, admission, event, policy, receipt


def test_production_origin_carrier_uses_local_admission_then_authenticated_hosted_delivery(tmp_path, monkeypatch):
    from tests.test_authoring_ingress import git
    repo, remote, main_sha, registry, envelope, _, event, policy, receipt = origin_carrier_fixture(tmp_path, monkeypatch)
    assert "audited_handoff" not in json.loads(json.loads(event.read_text(encoding="utf-8"))["issue"]["body"])
    carrier.prepare_origin_admission(event, policy, receipt)
    before = receipt.read_bytes()
    carrier.prepare_origin_admission(event, policy, receipt)
    assert receipt.read_bytes() == before  # Same attempt is byte-stable.
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha
    # Hosted delivery authenticates the artifact; it never reads the local registry.
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.delenv("AIOS_ORIGIN_REGISTRY")
    monkeypatch.setattr(carrier, "admit_local", lambda *a, **k: pytest.fail("hosted local-origin validation"))
    delivery = carrier.deliver_event(event, policy, repo=repo, origin_admission_path=receipt)
    assert delivery.ingress_result.status == "CANONICALIZED"
    replay = carrier.deliver_event(event, policy, repo=repo, origin_admission_path=receipt)
    assert replay.ingress_result.status == "IDEMPOTENT"
    task = yaml.safe_load(git(repo, "show", f"{delivery.ingress_result.canonical_sha}:.ai/tasks/TASK-105.yaml"))
    assert task["return_affinity"] == yaml.safe_load(envelope.payload)["return_affinity"]
    exported = receipt.read_text(encoding="utf-8")
    from tests.test_origin_bootstrap import URL_A, ENDPOINT
    assert all(secret not in exported for secret in (URL_A, ENDPOINT, str(registry.path), "c" * 64))


@pytest.mark.parametrize("fault", ("missing", "stale", "wrong_generation", "selector", "forged",
                                    "cross_attempt", "other_issue", "body", "duplicate", "wrong_key"))
def test_origin_carrier_bad_admission_fails_before_semantic_ingress(tmp_path, monkeypatch, fault):
    from aios_renew import origin_authoring_proof as proofs
    from tests.test_authoring_ingress import git
    repo, remote, main_sha, _, _, _, event_path, policy, receipt_path = origin_carrier_fixture(tmp_path, monkeypatch)
    carrier.prepare_origin_admission(event_path, policy, receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if fault == "missing":
        receipt_path.unlink()
    elif fault == "stale":
        monkeypatch.setattr(proofs, "_clock", lambda: receipt["expires_at"])
    elif fault in {"wrong_generation", "selector", "forged"}:
        field, value = {"wrong_generation": ("generation", 2),
                        "selector": ("route_handle", "page-origin-v1:" + "0" * 64),
                        "forged": ("signature", "0" * 64)}[fault]
        receipt[field] = value
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif fault == "cross_attempt":
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    elif fault == "wrong_key":
        monkeypatch.setenv("AIOS_ORIGIN_ADMISSION_KEY", "d" * 64)
    elif fault == "duplicate":
        receipt_path.write_text(json.dumps(receipt)[:-1] + ',"generation":1}', encoding="utf-8")
    else:
        event = json.loads(event_path.read_text(encoding="utf-8"))
        if fault == "other_issue":
            event["issue"]["number"] = 108
        else:
            body = json.loads(event["issue"]["body"])
            body["payload"] = body["payload"].replace("Implement generic", "Substitute generic")
            event["issue"]["body"] = json.dumps(body)
        event_path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **k: pytest.fail("premature semantic ingress"))
    with pytest.raises(carrier.GitHubIssueIngressError, match="origin"):
        carrier.deliver_event(event_path, policy, repo=repo, origin_admission_path=receipt_path)
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha


@pytest.mark.parametrize("fault", ("hosted", "missing_registry", "stale", "cross_attempt", "ambiguous"))
def test_self_hosted_provenance_failure_exports_no_admission_or_canonical_mutation(tmp_path, monkeypatch, fault):
    from aios_renew import origin_authoring_proof as proofs
    from tests.test_authoring_ingress import git
    repo, remote, main_sha, registry, _, admission, event, policy, receipt = origin_carrier_fixture(tmp_path, monkeypatch)
    if fault == "hosted":
        monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    elif fault == "missing_registry":
        monkeypatch.delenv("AIOS_ORIGIN_REGISTRY")
    elif fault == "stale":
        monkeypatch.setattr(proofs, "_clock", lambda: admission.receipt["expires_at"])
    elif fault == "cross_attempt":
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    else:
        data = registry.read()
        for route in data["routes"].values():
            route["attempt"]["status"] = "AMBIGUOUS"
        registry.write(data)
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **k: pytest.fail("admission is not semantic ingress"))
    with pytest.raises(carrier.GitHubIssueIngressError, match="origin"):
        carrier.prepare_origin_admission(event, policy, receipt)
    assert not receipt.exists()
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha


def test_hosted_origin_request_framing_has_no_local_state_or_mutation(tmp_path, monkeypatch, capsys):
    _, _, _, _, _, _, event, policy, _ = origin_carrier_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.delenv("AIOS_ORIGIN_REGISTRY")
    monkeypatch.setattr(carrier, "admit_local", lambda *a, **k: pytest.fail("hosted cannot admit local proof"))
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **k: pytest.fail("framing cannot canonicalize"))
    output = tmp_path / "frame-output"
    assert carrier.main(["--mode", "frame", "--event", str(event), "--policy", str(policy), "--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == "origin_proof_required=true\n"
    assert "origin-authoring-v1:" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "operation",
    ["AUTHOR_TASK", "SUBMIT_REVIEW", "AUTHOR_REMEDIATION", "AUTHOR_REPAIR"],
)
def test_all_operation_families_use_the_same_single_opaque_ingress_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    body = (
        f"format: AIOS_INGRESS_ENVELOPE\nversion: 1\noperation: {operation}\n"
        "payload: |\n  exact: $() `git push` ; & | < >\n"
    )
    event_path = _write_event(tmp_path, _event(body))
    policy_path = _write_policy(tmp_path)
    calls: list[tuple[object, Path, bytes | None]] = []

    def fake_ingress(
        source: object, *, repo: Path, stdin_bytes: bytes | None = None
    ) -> IngressResult:
        calls.append((source, repo, stdin_bytes))
        return IngressResult(operation=operation, canonical_sha="a" * 40)

    monkeypatch.setattr(carrier, "ingest_carrier", fake_ingress)
    delivery = carrier.deliver_event(event_path, policy_path, repo=tmp_path)

    assert calls == [("-", tmp_path, body.encode("utf-8"))]
    assert delivery.ingress_result.operation == operation
    assert "github-issue:trung-via/AIOS-renew#107@trung-via" in delivery.render_receipt()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda event: event.update(action="edited"), "action"),
        (
            lambda event: event["repository"].update(full_name="other/project"),
            "repository",
        ),
        (lambda event: event["issue"]["user"].update(login="intruder"), "authorized"),
        (lambda event: event["issue"].update(title="almost"), "title"),
        (lambda event: event["issue"].update(number=0), "number"),
        (lambda event: event["issue"].update(body=None), "body"),
        (lambda event: event["issue"].update(body=""), "empty"),
        (lambda event: event["issue"].update(body="x" * 131073), "bound"),
    ],
)
def test_event_admission_failures_happen_before_ingress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    reason: str,
) -> None:
    event = _event()
    mutation(event)
    event_path = _write_event(tmp_path, event)
    policy_path = _write_policy(tmp_path)
    calls = 0

    def forbidden_ingress(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("semantic ingress must not be invoked")

    monkeypatch.setattr(carrier, "ingest_carrier", forbidden_ingress)
    with pytest.raises(carrier.GitHubIssueIngressError, match=reason):
        carrier.deliver_event(event_path, policy_path, repo=tmp_path)
    assert calls == 0


@pytest.mark.parametrize(
    "policy_mutation",
    [
        lambda policy: policy["github_issue"].update(enabled=False),
        lambda policy: policy["github_issue"].update(authorized_actors=[]),
        lambda policy: policy["github_issue"].update(max_body_bytes=0),
        lambda policy: policy.update(unreviewed_authority=True),
    ],
)
def test_disabled_or_malformed_policy_fails_before_ingress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy_mutation
) -> None:
    policy = json.loads(json.dumps(POLICY))
    policy_mutation(policy)
    policy_path = _write_policy(tmp_path, policy)
    event_path = _write_event(tmp_path, _event())
    monkeypatch.setattr(
        carrier,
        "ingest_carrier",
        lambda *args, **kwargs: pytest.fail("semantic ingress must not be invoked"),
    )
    with pytest.raises(carrier.GitHubIssueIngressError):
        carrier.deliver_event(event_path, policy_path, repo=tmp_path)


def test_malformed_event_file_fails_before_ingress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_bytes(b"{not-json")
    monkeypatch.setattr(
        carrier,
        "ingest_carrier",
        lambda *args, **kwargs: pytest.fail("semantic ingress must not be invoked"),
    )
    with pytest.raises(carrier.GitHubIssueIngressError, match="UTF-8 JSON"):
        carrier.deliver_event(event_path, _write_policy(tmp_path), repo=tmp_path)


def test_shell_and_destination_strings_remain_inert_and_unmodified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = """format: AIOS_INGRESS_ENVELOPE
version: 1
operation: AUTHOR_TASK
identity:
  task_id: TASK-X
expected_state:
  expected_main_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
payload:
  command: "$(touch owned); git push --force refs/heads/main"
  destination: ".github/workflows/owned.yml"
  multiline: |
    {yaml: [json, `$HOME`]}
"""
    event_path = _write_event(tmp_path, _event(body))
    policy_path = _write_policy(tmp_path)
    observed: list[bytes] = []
    real_ingress = carrier.ingest_carrier

    def observing_ingress(source, *, repo, stdin_bytes=None):
        observed.append(stdin_bytes)
        return real_ingress(source, repo=repo, stdin_bytes=stdin_bytes)

    monkeypatch.setattr(carrier, "ingest_carrier", observing_ingress)
    with pytest.raises(AuthoringIngressError, match="prohibited field"):
        carrier.deliver_event(event_path, policy_path, repo=tmp_path)
    assert observed == [body.encode("utf-8")]
    assert not (tmp_path / "owned").exists()


@pytest.mark.parametrize("operation", ["AUTHOR_REMEDIATION", "AUTHOR_REPAIR"])
def test_audited_handoff_remains_opaque_and_semantic_rejection_has_no_success_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str
) -> None:
    body = (
        f"format: AIOS_INGRESS_ENVELOPE\nversion: 1\noperation: {operation}\n"
        "expected_state:\n  expected_current_repair_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "  expected_failure_artifacts_sha: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
        "audited_handoff:\n  format: AIOS_AUDITED_AUTHORING_HANDOFF\n  version: 1\n"
        "  stage1: {opaque: '$() `git push`'}\n  stage2: {outcome: NO_DECISION}\n"
    )
    event = _write_event(tmp_path, _event(body))
    policy = _write_policy(tmp_path)
    calls = []
    rejection = ("repair authorization invalid: final REPAIR identity mismatch"
                 if operation == "AUTHOR_REPAIR" else "remediation validation failed: final finding identity mismatch")
    def reject(source, *, repo, stdin_bytes=None):
        calls.append((source, stdin_bytes))
        raise AuthoringIngressError(rejection)
    monkeypatch.setattr(carrier, "ingest_carrier", reject)
    output = tmp_path / "github-output"
    assert carrier.main(["--event", str(event), "--policy", str(policy),
                         "--repo", str(tmp_path), "--output", str(output)]) == 1
    assert calls == [("-", body.encode("utf-8"))]
    receipt = capsys.readouterr().out
    assert "status: FAIL" in receipt
    assert rejection in receipt
    assert "status: PASS" not in receipt
    assert not output.exists()


def test_receipts_are_bounded() -> None:
    failure = carrier.render_failure(ValueError("x" * 10_000))
    assert len(failure) <= 3_500
    assert failure.endswith("[truncated]")


def test_issue_carrier_canonicalizes_direct_final_task_without_audit(tmp_path, monkeypatch):
    from tests.test_authoring_ingress import setup_test_repo, V1_TASK_105_SOURCE, git

    repo, remote, main_sha = setup_test_repo(tmp_path)
    body = {"format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"}, "expected_state": {"expected_main_sha": main_sha},
            "payload": V1_TASK_105_SOURCE}
    event = _write_event(tmp_path, _event(json.dumps(body)))
    policy = _write_policy(tmp_path)
    monkeypatch.setattr(carrier, "admit_local", lambda *a, **k: pytest.fail("legacy TASK cannot consume origin proof"))
    delivery = carrier.deliver_event(event, policy, repo=repo)
    assert delivery.ingress_result.status == "CANONICALIZED"
    assert git(remote, "rev-parse", "refs/heads/main") == delivery.ingress_result.canonical_sha
    assert delivery.github_outputs() == ""
    assert carrier.deliver_event(event, policy, repo=repo).ingress_result.replayed


@pytest.mark.parametrize("task_policy", ["legacy", "minimum-sufficient-v2"])
def test_issue_carrier_rejects_noncurrent_task_policy_before_mutation(tmp_path, monkeypatch, task_policy):
    from tests.test_authoring_ingress import (
        setup_test_repo, TASK_105_SOURCE, V2_TASK_105_SOURCE, forbid_correction_mutation, git,
    )
    from aios_renew.operator import _runtime_paths_readonly

    repo, remote, main_sha = setup_test_repo(tmp_path)
    body = {"format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"}, "expected_state": {"expected_main_sha": main_sha},
            "payload": TASK_105_SOURCE if task_policy == "legacy" else V2_TASK_105_SOURCE}
    event = _write_event(tmp_path, _event(json.dumps(body)))
    policy = _write_policy(tmp_path)
    refs = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    local_refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    forbid_correction_mutation(monkeypatch)
    with pytest.raises(AuthoringIngressError, match="new TASK identities and revisions require verification.policy minimum-sufficient-v1"):
        carrier.deliver_event(event, policy, repo=repo)
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == refs
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == local_refs
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert not _runtime_paths_readonly(repo).runs.exists()


def test_issue_carrier_canonicalizes_direct_final_repair_without_audit(tmp_path, monkeypatch):
    from tests.test_authoring_ingress import (completion_gate_supersession_fixture,
                                            forbid_repair_audit_dependencies, git)

    repo, remote, main, failure_sha, envelope = completion_gate_supersession_fixture(tmp_path)
    forbid_repair_audit_dependencies(monkeypatch)
    body = {"format": envelope.format, "version": 1, "operation": "AUTHOR_REPAIR",
            "identity": envelope.identity, "expected_state": envelope.expected_state,
            "payload": envelope.payload}
    assert "audited_handoff" not in body
    event = _write_event(tmp_path, _event(json.dumps(body)))
    policy = _write_policy(tmp_path)
    delivery = carrier.deliver_event(event, policy, repo=repo)
    result = delivery.ingress_result
    assert result.status == "CANONICALIZED"
    assert delivery.repair_failed_run_id == "RUN-254-001"
    assert delivery.repair_sha == result.canonical_sha
    assert json.loads(git(repo, "show", f"{result.canonical_sha}:.ai/transport/repair.json")) == envelope.payload
    assert git(remote, "rev-parse", "refs/heads/main") == main
    assert git(remote, "rev-parse", "refs/heads/aios/failure-artifacts/RUN-254-001") == failure_sha
    replay = carrier.deliver_event(event, policy, repo=repo)
    assert replay.ingress_result.replayed and replay.repair_failed_run_id is None
    # Legacy audit differences remain opaque carrier data, including retired
    # profile material; only the identical final contract owns this replay.
    body["audited_handoff"] = {"stage1": {"audit_profile_ref": {"id": "brain-high-value-v3"}},
                               "stage2": {"outcome": "NO_DECISION"}}
    body["payload"] = json.dumps(envelope.payload, indent=2)
    legacy_event = _write_event(tmp_path, _event(json.dumps(body)))
    legacy_replay = carrier.deliver_event(legacy_event, policy, repo=repo)
    assert legacy_replay.ingress_result.replayed
    assert legacy_replay.ingress_result.canonical_sha == result.canonical_sha
    assert legacy_replay.repair_failed_run_id is None
    assert not git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/artifacts/*")


@pytest.mark.parametrize("fault", ["actor", "repository", "origin_proof", "forged_final", "stale_failure"])
def test_issue_carrier_direct_final_repair_keeps_actor_origin_and_subject_gates(tmp_path, monkeypatch, fault):
    from tests.test_authoring_ingress import (completion_gate_supersession_fixture,
                                            forbid_correction_mutation, git)

    repo, remote, main, _, envelope = completion_gate_supersession_fixture(tmp_path)
    body = {"format": envelope.format, "version": 1, "operation": "AUTHOR_REPAIR",
            "identity": envelope.identity, "expected_state": dict(envelope.expected_state),
            "payload": dict(envelope.payload)}
    event_doc = _event(json.dumps(body))
    if fault == "actor":
        event_doc["issue"]["user"]["login"] = "unauthorized-actor"
    elif fault == "repository":
        event_doc["repository"]["full_name"] = "different/repository"
    elif fault == "origin_proof":
        body["origin_authoring_proof"] = "page-origin-proof-v1:" + "a" * 64
    elif fault == "forged_final":
        body["payload"]["modification_scope"] = ["outside.py"]
        body["audited_handoff"] = {"stage2": {"outcome": "CANDIDATE"}}
    else:
        body["expected_state"]["expected_failure_artifacts_sha"] = main
    if fault not in ("actor", "repository"):
        event_doc["issue"]["body"] = json.dumps(body)
    event = _write_event(tmp_path, event_doc)
    policy = _write_policy(tmp_path)
    before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    forbid_correction_mutation(monkeypatch)
    with pytest.raises((AuthoringIngressError, carrier.GitHubIssueIngressError)):
        carrier.deliver_event(event, policy, repo=repo)
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before


@pytest.mark.parametrize("origin", (False, True))
@pytest.mark.parametrize("mode", ("frame", "deliver"))
def test_issue_carrier_rejects_task_handoff_with_fail_receipt_and_no_mutation(
    tmp_path, monkeypatch, capsys, origin, mode
):
    from tests.test_authoring_ingress import setup_test_repo, V2_TASK_105_SOURCE, git
    from aios_renew import brain_attention

    monkeypatch.setattr(brain_attention, "observe_carrier_rejection", lambda *a, **k: None)

    if origin:
        repo, remote, main_sha, _, _, _, event, policy, _ = origin_carrier_fixture(tmp_path, monkeypatch)
        body = json.loads(json.loads(event.read_text(encoding="utf-8"))["issue"]["body"])
    else:
        repo, remote, main_sha = setup_test_repo(tmp_path)
        body = {"format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_TASK",
                "identity": {"task_id": "TASK-105"}, "expected_state": {"expected_main_sha": main_sha},
                "payload": V2_TASK_105_SOURCE}
        policy = _write_policy(tmp_path)
    body["audited_handoff"] = {"format": "AIOS_AUDITED_AUTHORING_HANDOFF", "version": 1,
                               "stage1": {}, "stage2": {}}
    event = _write_event(tmp_path, _event(json.dumps(body)))
    output = tmp_path / "handoff-rejection-output"
    assert carrier.main(["--mode", mode, "--event", str(event), "--policy", str(policy),
                         "--repo", str(repo), "--output", str(output)]) == 1
    receipt = capsys.readouterr().out
    assert "AUTHOR_TASK does not accept audited_handoff" in receipt
    assert "status: FAIL" in receipt and "status: PASS" not in receipt
    assert not output.exists()
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha


def test_successful_submit_review_emits_publication_run_id_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "format: AIOS_INGRESS_ENVELOPE\nversion: 1\noperation: SUBMIT_REVIEW\n"
    event_path = _write_event(tmp_path, _event(body))
    policy_path = _write_policy(tmp_path)

    fake_result = IngressResult(
        operation="SUBMIT_REVIEW",
        status="CANONICALIZED",
        canonical_destination="refs/heads/aios/review-decision/RUN-108-003",
        canonical_sha="b" * 40,
        replayed=False,
        detail="canonicalized PASS review decision for RUN-108-003",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: fake_result)

    delivery = carrier.deliver_event(event_path, policy_path, repo=tmp_path)
    assert delivery.publication_run_id == "RUN-108-003"
    assert (
        delivery.github_outputs()
        == "publication_run_id=RUN-108-003\nrun_id=RUN-108-003\n"
    )


def test_changes_required_submit_review_emits_no_publication_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "format: AIOS_INGRESS_ENVELOPE\nversion: 1\noperation: SUBMIT_REVIEW\n"
    event_path = _write_event(tmp_path, _event(body))
    policy_path = _write_policy(tmp_path)

    fake_result = IngressResult(
        operation="SUBMIT_REVIEW",
        status="CANONICALIZED",
        canonical_destination="refs/heads/aios/review-decision/RUN-108-003",
        canonical_sha="b" * 40,
        replayed=False,
        detail="canonicalized CHANGES_REQUIRED review decision for RUN-108-003",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: fake_result)

    delivery = carrier.deliver_event(event_path, policy_path, repo=tmp_path)
    assert delivery.publication_run_id is None
    assert delivery.github_outputs() == ""


@pytest.mark.parametrize(
    ("operation", "destination"),
    [
        ("AUTHOR_TASK", ".ai/tasks/TASK-110.yaml"),
        ("AUTHOR_REMEDIATION", "refs/heads/aios/remediation/RUN-108-001-F1"),
    ],
)
def test_non_submit_review_operations_emit_no_publication_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, destination: str
) -> None:
    body = f"format: AIOS_INGRESS_ENVELOPE\nversion: 1\noperation: {operation}\n"
    event_path = _write_event(tmp_path, _event(body))
    policy_path = _write_policy(tmp_path)

    fake_result = IngressResult(
        operation=operation,
        status="CANONICALIZED",
        canonical_destination=destination,
        canonical_sha="c" * 40,
        replayed=False,
        detail="ok",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: fake_result)

    delivery = carrier.deliver_event(event_path, policy_path, repo=tmp_path)
    assert delivery.publication_run_id is None
    assert delivery.github_outputs() == ""


def test_submit_review_replay_emits_canonical_run_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "format: AIOS_INGRESS_ENVELOPE\nversion: 1\noperation: SUBMIT_REVIEW\n"
    event_path = _write_event(tmp_path, _event(body))
    policy_path = _write_policy(tmp_path)

    fake_result = IngressResult(
        operation="SUBMIT_REVIEW",
        status="IDEMPOTENT",
        canonical_destination="refs/heads/aios/review-decision/RUN-108-003",
        canonical_sha="d" * 40,
        replayed=True,
        detail="identical REVIEW already canonicalized for RUN-108-003",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: fake_result)

    delivery = carrier.deliver_event(event_path, policy_path, repo=tmp_path)
    assert delivery.publication_run_id == "RUN-108-003"
    assert (
        delivery.github_outputs()
        == "publication_run_id=RUN-108-003\nrun_id=RUN-108-003\n"
    )


@pytest.mark.parametrize(
    "bad_destination",
    [
        "refs/heads/main",
        "refs/heads/aios/review-decision/",
        "refs/heads/aios/review-decision/not-a-run",
        "refs/heads/aios/review-decision/RUN-bad;inject",
        "refs/heads/aios/review-decision/TASK-108",
    ],
)
def test_malformed_decision_destination_emits_no_publication_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_destination: str
) -> None:
    fake_delivery = carrier.IssueDelivery(
        carrier_identity="github-issue:trung-via/AIOS-renew#1@trung-via",
        ingress_result=IngressResult(
            operation="SUBMIT_REVIEW",
            status="CANONICALIZED",
            canonical_destination=bad_destination,
            canonical_sha="e" * 40,
            detail="canonicalized PASS review decision for RUN-x",
        ),
    )
    assert fake_delivery.publication_run_id is None
    assert fake_delivery.github_outputs() == ""


def test_main_cli_writes_github_output_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_path = _write_event(tmp_path, _event("body"))
    policy_path = _write_policy(tmp_path)
    output_path = tmp_path / "github_output.txt"

    pass_result = IngressResult(
        operation="SUBMIT_REVIEW",
        status="CANONICALIZED",
        canonical_destination="refs/heads/aios/review-decision/RUN-108-003",
        canonical_sha="f" * 40,
        replayed=False,
        detail="canonicalized PASS review decision for RUN-108-003",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: pass_result)

    code = carrier.main(
        [
            "--event",
            str(event_path),
            "--policy",
            str(policy_path),
            "--repo",
            str(tmp_path),
            "--output",
            str(output_path),
        ]
    )
    assert code == 0
    assert output_path.read_text(encoding="utf-8") == (
        "publication_run_id=RUN-108-003\nrun_id=RUN-108-003\n"
    )

    # Now verify AUTHOR_TASK does not write publication outputs
    task_output = tmp_path / "task_output.txt"
    task_result = IngressResult(
        operation="AUTHOR_TASK",
        status="CANONICALIZED",
        canonical_destination=".ai/tasks/TASK-110.yaml",
        canonical_sha="1" * 40,
        detail="canonicalized TASK-110 r1",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: task_result)

    code = carrier.main(
        [
            "--event",
            str(event_path),
            "--policy",
            str(policy_path),
            "--repo",
            str(tmp_path),
            "--output",
            str(task_output),
        ]
    )
    assert code == 0
    assert not task_output.exists() or task_output.read_text(encoding="utf-8") == ""


def test_successful_author_repair_exposes_subject_without_granting_wakeup_from_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "format: AIOS_INGRESS_ENVELOPE\nversion: 1\noperation: AUTHOR_REPAIR\n"
    event_path = _write_event(tmp_path, _event(body))
    policy_path = _write_policy(tmp_path)

    canonical_sha = "a" * 40
    fake_result = IngressResult(
        operation="AUTHOR_REPAIR",
        status="CANONICALIZED",
        canonical_destination="refs/heads/aios/repair/RUN-121-005",
        canonical_sha=canonical_sha,
        replayed=False,
        detail="canonicalized repair authorization for RUN-121-005",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: fake_result)

    delivery = carrier.deliver_event(event_path, policy_path, repo=tmp_path)
    assert delivery.publication_run_id is None
    assert delivery.repair_failed_run_id == "RUN-121-005"
    assert delivery.repair_sha == canonical_sha
    assert delivery.repair_dispatch_id == f"repair-RUN-121-005-{canonical_sha}"
    expected_outputs = (
        f"repair_dispatch_id=repair-RUN-121-005-{canonical_sha}\n"
        "failed_run_id=RUN-121-005\n"
        "repair_failed_run_id=RUN-121-005\n"
        f"repair_sha={canonical_sha}\n"
        "repair_wakeup_route=HUMAN_WAKEUP_REQUIRED\n"
        "repair_wakeup_reason=CURRENT_PREFLIGHT_REQUIRED\n"
    )
    assert delivery.github_outputs() == expected_outputs
    assert delivery.repair_wakeup_route == "HUMAN_WAKEUP_REQUIRED"

    emitted = dict(
        line.split("=", 1) for line in delivery.github_outputs().splitlines()
    )
    assert emitted["repair_dispatch_id"] == (
        f"repair-{emitted['failed_run_id']}-{emitted['repair_sha']}"
    )

    ingress_workflow = BRAIN_INGRESS_WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_id: 'aios-self-hosted-repair-wakeup.yml'" in ingress_workflow
    assert (
        "AIOS_REPAIR_DISPATCH_ID: ${{ steps.ingress.outputs.repair_dispatch_id }}"
        in ingress_workflow
    )
    assert (
        "AIOS_FAILED_RUN_ID: ${{ steps.ingress.outputs.failed_run_id }}"
        in ingress_workflow
    )
    assert "AIOS_REPAIR_SHA: ${{ steps.ingress.outputs.repair_sha }}" in ingress_workflow
    assert "executor: ''," in ingress_workflow

    target_workflow = SELF_HOSTED_REPAIR_WORKFLOW.read_text(encoding="utf-8")
    assert "$env:AIOS_DELIVERY_ACTOR -ceq 'github-actions[bot]'" in target_workflow
    assert "$env:AIOS_DELIVERY_EVENT -cne 'workflow_dispatch'" in target_workflow
    assert (
        '$expectedRepairDispatchId = "repair-$($env:AIOS_FAILED_RUN_ID)-$($env:AIOS_REPAIR_SHA)"'
        in target_workflow
    )
    assert "$env:AIOS_REPAIR_DISPATCH_ID -cne $expectedRepairDispatchId" in target_workflow


@pytest.mark.parametrize("revision", [2, 3])
def test_author_repair_supersession_destination_extracts_failed_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: int
) -> None:
    body = "format: AIOS_INGRESS_ENVELOPE\nversion: 1\noperation: AUTHOR_REPAIR\n"
    event_path = _write_event(tmp_path, _event(body))
    policy_path = _write_policy(tmp_path)

    canonical_sha = "b" * 40
    fake_result = IngressResult(
        operation="AUTHOR_REPAIR",
        status="CANONICALIZED",
        canonical_destination=f"refs/heads/aios/repair-supersession/RUN-121-005/{revision}",
        canonical_sha=canonical_sha,
        replayed=False,
        detail=f"canonicalized repair authorization for RUN-121-005",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: fake_result)

    delivery = carrier.deliver_event(event_path, policy_path, repo=tmp_path)
    assert delivery.repair_failed_run_id == "RUN-121-005"
    assert delivery.repair_sha == canonical_sha
    assert delivery.repair_dispatch_id == f"repair-RUN-121-005-{canonical_sha}"


@pytest.mark.parametrize(
    ("status", "replayed"),
    [
        ("IDEMPOTENT", True),
        ("CANONICALIZED", True),
    ],
)
def test_replayed_or_idempotent_author_repair_suppresses_repair_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, replayed: bool
) -> None:
    body = "format: AIOS_INGRESS_ENVELOPE\nversion: 1\noperation: AUTHOR_REPAIR\n"
    event_path = _write_event(tmp_path, _event(body))
    policy_path = _write_policy(tmp_path)

    fake_result = IngressResult(
        operation="AUTHOR_REPAIR",
        status=status,
        canonical_destination="refs/heads/aios/repair/RUN-121-005",
        canonical_sha="c" * 40,
        replayed=replayed,
        detail="replayed",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: fake_result)

    delivery = carrier.deliver_event(event_path, policy_path, repo=tmp_path)
    assert delivery.repair_failed_run_id is None
    assert delivery.repair_sha is None
    assert delivery.repair_dispatch_id is None
    assert delivery.github_outputs() == ""


@pytest.mark.parametrize(
    "bad_destination",
    [
        "refs/heads/main",
        "refs/heads/aios/repair/",
        "refs/heads/aios/repair/not-a-run",
        "refs/heads/aios/repair-supersession/RUN-121-005/1",
        "refs/heads/aios/repair-supersession/RUN-121-005/notanumber",
        "refs/heads/aios/repair-supersession/RUN-121-005",
    ],
)
def test_malformed_repair_destination_emits_no_repair_handoff(bad_destination: str) -> None:
    delivery = carrier.IssueDelivery(
        carrier_identity="github-issue:trung-via/AIOS-renew#1@trung-via",
        ingress_result=IngressResult(
            operation="AUTHOR_REPAIR",
            status="CANONICALIZED",
            canonical_destination=bad_destination,
            canonical_sha="d" * 40,
            replayed=False,
            detail="ok",
        ),
    )
    assert delivery.repair_failed_run_id is None
    assert delivery.repair_sha is None
    assert delivery.repair_dispatch_id is None
    assert delivery.github_outputs() == ""


def test_malformed_repair_sha_emits_no_repair_handoff() -> None:
    delivery = carrier.IssueDelivery(
        carrier_identity="github-issue:trung-via/AIOS-renew#1@trung-via",
        ingress_result=IngressResult(
            operation="AUTHOR_REPAIR",
            status="CANONICALIZED",
            canonical_destination="refs/heads/aios/repair/RUN-121-005",
            canonical_sha="not-a-sha",
            replayed=False,
            detail="ok",
        ),
    )
    assert delivery.repair_failed_run_id == "RUN-121-005"
    assert delivery.repair_sha is None
    assert delivery.repair_dispatch_id is None
    assert delivery.github_outputs() == ""


def test_main_cli_writes_repair_github_output_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_path = _write_event(tmp_path, _event("repair body"))
    policy_path = _write_policy(tmp_path)
    output_path = tmp_path / "repair_output.txt"

    sha = "e" * 40
    repair_result = IngressResult(
        operation="AUTHOR_REPAIR",
        status="CANONICALIZED",
        canonical_destination="refs/heads/aios/repair/RUN-121-005",
        canonical_sha=sha,
        replayed=False,
        detail="canonicalized repair authorization for RUN-121-005",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: repair_result)

    code = carrier.main(
        [
            "--event",
            str(event_path),
            "--policy",
            str(policy_path),
            "--repo",
            str(tmp_path),
            "--output",
            str(output_path),
        ]
    )
    assert code == 0
    expected = (
        f"repair_dispatch_id=repair-RUN-121-005-{sha}\n"
        "failed_run_id=RUN-121-005\n"
        "repair_failed_run_id=RUN-121-005\n"
        f"repair_sha={sha}\n"
        "repair_wakeup_route=HUMAN_WAKEUP_REQUIRED\n"
        "repair_wakeup_reason=CURRENT_PREFLIGHT_REQUIRED\n"
    )
    assert output_path.read_text(encoding="utf-8") == expected


def test_issue_140_ordering_topology_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the Issue #140 ordering topology:

    1. Original non-target issue-opened REPAIR carrier path cannot advance execution on [AIOS BRAIN INGRESS].
    2. Successful AUTHOR_REPAIR canonicalization subsequently exposes the exact bound post-canonicalization handoff.
    3. Repeated / replayed delivery suppresses duplicate continuation execution.
    """
    from aios_renew import github_issue_repair_wakeup as repair_carrier

    repair_policy_path = tmp_path / "repair_policy.yaml"
    repair_policy_path.write_text(
        yaml.safe_dump(
            {
                "format": "AIOS_BRAIN_REPAIR_WAKEUP_CARRIERS_POLICY",
                "version": 1,
                "github_issue": {
                    "enabled": True,
                    "repository": "trung-via/AIOS-renew",
                    "authorized_actors": ["trung-via"],
                    "title_marker": "[AIOS REPAIR WAKEUP]",
                    "max_body_bytes": 16384,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    ingress_policy_path = _write_policy(tmp_path)

    body = """format: AIOS_INGRESS_ENVELOPE
version: 1
operation: AUTHOR_REPAIR
identity:
  failed_run_id: RUN-121-005
expected_state:
  expected_failed_head_sha: c3d822c147e52bb85f2786d03f113fc78b8f8e67
payload:
  repair_id: REPAIR-RUN-121-005-01
  failed_run_id: RUN-121-005
  failed_head_sha: c3d822c147e52bb85f2786d03f113fc78b8f8e67
  task:
    id: TASK-121
    revision: 2
  action: CODE_FIX
  modification_scope:
    - src/aios_renew/sample.py
  instructions:
    - Fix the candidate.
  constraints: []
"""
    # Event opened with title '[AIOS BRAIN INGRESS]' as in production Issue #140
    ingress_event_data = {
        "action": "opened",
        "sender": {"login": "trung-via"},
        "repository": {"full_name": "trung-via/AIOS-renew"},
        "issue": {
            "number": 140,
            "title": "[AIOS BRAIN INGRESS]",
            "user": {"login": "trung-via"},
            "body": body,
        },
    }
    event_path = _write_event(tmp_path, ingress_event_data)

    # Step 1: The dedicated REPAIR wakeup carrier listening to the issue-opened event
    # rejects the non-target [AIOS BRAIN INGRESS] issue and cannot advance execution.
    repair_policy = repair_carrier.load_policy(repair_policy_path)
    with pytest.raises(repair_carrier.GitHubIssueRepairWakeupError, match="title"):
        repair_carrier.admit_event(event_path, repair_policy)

    receipt_path = tmp_path / "carrier_receipt.txt"
    output_path = tmp_path / "carrier_outputs.txt"
    exit_code = repair_carrier.main(
        [
            "--event",
            str(event_path),
            "--policy",
            str(repair_policy_path),
            "--output",
            str(output_path),
            "--receipt",
            str(receipt_path),
        ]
    )
    assert exit_code == 1
    assert not output_path.exists()
    rejection_text = receipt_path.read_text(encoding="utf-8")
    assert "status: REJECTED" in rejection_text
    assert "dispatch_accepted: false" in rejection_text
    assert "repair_run_outcome: not_observed" in rejection_text

    # Step 2: Ingress carrier executes AUTHOR_REPAIR canonicalization and
    # subsequently exposes the exact bound post-canonicalization handoff.
    canonical_repair_sha = "d4a8602be912f3272b00e36add262cfa5fa196ae"
    canonical_result = IngressResult(
        operation="AUTHOR_REPAIR",
        status="CANONICALIZED",
        canonical_destination="refs/heads/aios/repair/RUN-121-005",
        canonical_sha=canonical_repair_sha,
        replayed=False,
        detail="canonicalized repair authorization for RUN-121-005",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: canonical_result)

    delivery = carrier.deliver_event(event_path, ingress_policy_path, repo=tmp_path)
    assert delivery.repair_failed_run_id == "RUN-121-005"
    assert delivery.repair_sha == canonical_repair_sha
    assert (
        delivery.repair_dispatch_id
        == f"repair-RUN-121-005-{canonical_repair_sha}"
    )
    assert "failed_run_id=RUN-121-005\n" in delivery.github_outputs()
    assert f"repair_sha={canonical_repair_sha}\n" in delivery.github_outputs()
    assert (
        f"repair_dispatch_id=repair-RUN-121-005-{canonical_repair_sha}\n"
        in delivery.github_outputs()
    )

    # Step 3: Repeated / replayed delivery suppresses duplicate continuation execution.
    replayed_result = IngressResult(
        operation="AUTHOR_REPAIR",
        status="IDEMPOTENT",
        canonical_destination="refs/heads/aios/repair/RUN-121-005",
        canonical_sha=canonical_repair_sha,
        replayed=True,
        detail="identical REPAIR already canonicalized for RUN-121-005",
    )
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **kw: replayed_result)

    replay_delivery = carrier.deliver_event(event_path, ingress_policy_path, repo=tmp_path)
    assert replay_delivery.repair_failed_run_id is None
    assert replay_delivery.repair_sha is None
    assert replay_delivery.repair_dispatch_id is None
    assert replay_delivery.github_outputs() == ""


@pytest.mark.parametrize("legacy_support", [False, True])
def test_issue_carrier_direct_final_remediation_preserves_separate_a3_and_delegation(tmp_path, monkeypatch, legacy_support):
    from aios_renew import operator
    from aios_renew.remote_surface import RemoteSurfaceError, require_current_approval
    from tests.test_authoring_ingress import (exact_correction_fixture, forbid_repair_audit_dependencies,
                                            V1_TASK_105_SOURCE, git)
    repo, remote, main, decision, envelope = exact_correction_fixture(
        tmp_path, "AUTHOR_REMEDIATION", "unrelated", task_source=V1_TASK_105_SOURCE)
    forbid_repair_audit_dependencies(monkeypatch)
    for name in ("run_task", "run_remediation", "run_approved_remediation_intent"):
        monkeypatch.setattr(operator, name, lambda *a, **k: pytest.fail("authoring cannot execute a correction"))
    monkeypatch.setattr(carrier, "admit_local", lambda *a, **k: pytest.fail("correction cannot consume TASK origin proof"))
    body = {"format": envelope.format, "version": 1, "operation": envelope.operation,
            "identity": envelope.identity, "expected_state": envelope.expected_state, "payload": envelope.payload}
    if legacy_support:
        body["audited_handoff"] = {"stage1": {"packet_fingerprint": "retired"},
                                   "stage2": {"outcome": "NO_DECISION", "provider_identity": "private"}}
    event = _write_event(tmp_path, _event(json.dumps(body)))
    policy = _write_policy(tmp_path)
    delivery = carrier.deliver_event(event, policy, repo=repo)
    result = delivery.ingress_result
    assert result.status == "CANONICALIZED" and not result.replayed
    assert git(repo, "rev-parse", f"{result.canonical_sha}^") == decision
    path = f".ai/remediations/REMEDIATION-{envelope.identity['source_run_id']}-F1.yaml"
    assert yaml.safe_load(git(repo, "show", f"{result.canonical_sha}:{path}")) == envelope.payload
    assert delivery.publication_run_id is None and delivery.repair_failed_run_id is None
    assert delivery.github_outputs() == ""
    assert all(value not in delivery.render_receipt() for value in ("private", "packet_fingerprint", "NO_DECISION"))
    from aios_renew.operator import _runtime_paths_readonly, runtime_state_root
    assert not _runtime_paths_readonly(repo).runs.exists()
    assert not (runtime_state_root(repo) / "approvals").exists()
    with pytest.raises(RemoteSurfaceError, match="approval is missing"):
        require_current_approval(envelope.identity["source_run_id"], "F1", repo=repo)
    replay = carrier.deliver_event(event, policy, repo=repo)
    assert replay.ingress_result.replayed and replay.ingress_result.canonical_sha == result.canonical_sha
    assert replay.github_outputs() == ""
    assert git(remote, "rev-parse", "refs/heads/main") == main


@pytest.mark.parametrize("fault", ["actor", "repository", "origin_proof", "finding", "source", "stale_review",
                                   "scope", "derived_support"])
def test_issue_carrier_direct_final_remediation_keeps_all_carrier_and_subject_gates(tmp_path, monkeypatch, fault):
    from tests.test_authoring_ingress import exact_correction_fixture, forbid_correction_mutation, git
    repo, remote, main, _, envelope = exact_correction_fixture(tmp_path, "AUTHOR_REMEDIATION", "matching")
    body = {"format": envelope.format, "version": 1, "operation": envelope.operation,
            "identity": dict(envelope.identity), "expected_state": dict(envelope.expected_state),
            "payload": dict(envelope.payload)}
    event_doc = _event(json.dumps(body))
    if fault == "actor":
        event_doc["issue"]["user"]["login"] = "unauthorized-actor"
    elif fault == "repository":
        event_doc["repository"]["full_name"] = "different/repository"
    elif fault == "origin_proof":
        body["origin_authoring_proof"] = "page-origin-proof-v1:" + "a" * 64
    elif fault == "finding":
        body["identity"]["finding_id"] = "F999"
    elif fault == "source":
        body["identity"]["source_run_id"] = "RUN-999-001"
    elif fault == "stale_review":
        body["expected_state"]["expected_reviewed_sha"] = main
    elif fault == "scope":
        body["payload"]["modification_scope"] = ["outside.py"]
    else:
        body["expected_state"]["canonical_state_fingerprint"] = "0" * 64
    if fault not in {"actor", "repository"}:
        body["audited_handoff"] = {"stage2": {"outcome": "CANDIDATE"}}
        event_doc["issue"]["body"] = json.dumps(body)
    event, policy = _write_event(tmp_path, event_doc), _write_policy(tmp_path)
    before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    forbid_correction_mutation(monkeypatch)
    with pytest.raises((AuthoringIngressError, carrier.GitHubIssueIngressError)):
        carrier.deliver_event(event, policy, repo=repo)
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before


def repair_ingress_fixture(tmp_path, *, action, reuse="absent", coding_profile=False):
    """Isolated FAILURE/package, never PASS; malformed input must fail transport."""
    from tests.operator_test_support import make_repo, git
    from aios_renew.review_transport import transport_failure

    repo = make_repo(tmp_path)
    if coding_profile:
        (repo / ".ai/executor-profiles.yaml").write_bytes((ROOT / ".ai/executor-profiles.yaml").read_bytes())
        git(repo, "add", ".ai/executor-profiles.yaml")
        git(repo, "commit", "--quiet", "-m", "fixture coding policy")
        git(repo, "push", "--quiet", "origin", "main")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "--quiet", "-c", "failed-candidate")
    git(repo, "config", "branch.failed-candidate.remote", "origin")
    git(repo, "config", "branch.failed-candidate.merge", "refs/heads/main")
    (repo / "OUTPUT.txt").write_text("committed candidate\n", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "--quiet", "-m", "fixture candidate")
    head = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-101-001"
    task = {"id": "TASK-101", "revision": 1}
    run = {"run_id": run_id, "task": task, "executor": "codex", "base_sha": base,
           "head_sha": head, "workspace": str(repo), "status": "ACTIVE"}
    failure = {"kind": "FAILURE", "run_id": run_id, "task": task, "executor": "codex",
               "base_sha": base, "failed_head_sha": head,
               "phase": "VERIFICATION" if action == "NO_CHANGE" else "COMPLETION_GATE",
               "error": {"type": "OperatorError", "message": "protected fixture diagnostic"},
               "candidate": {"transportable": True, "repairable": True, "dirty": False,
                             "descends_from_base": True, "changed_files": ["OUTPUT.txt"],
                             "outside_task_scope": []}}
    material = tmp_path / "source-material"
    material.mkdir()
    run_path, failure_path = material / "run.json", material / "failure.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    preverification_path = None
    profile_path = None
    if coding_profile:
        from aios_renew.execution_profile import ResolvedExecutionProfile
        profile_path = material / "execution-profile.json"
        profile_path.write_text(ResolvedExecutionProfile(
            run_id, "codex", "source/authenticated-model", "low", "REPOSITORY_DEFAULT", "EXPLICIT",
        ).to_json(), encoding="utf-8")
    if reuse != "absent":
        structural = {"kind": "PRE_VERIFICATION_CANDIDATE", "run_id": run_id, "task": task,
                      "subject_sha": head, "package": {"result": {
                          "head_sha": head, "changed_files": ["OUTPUT.txt"], "unresolved": [],
                          "claims": [{"id": "C1", "satisfies": ["AC1"],
                                      "claim": "OUTPUT.txt contains the committed fixture candidate.",
                                      "evidence": []}]}, "evidence": []}}
        if reuse == "stale":
            structural["subject_sha"] = base
        preverification_path = material / "preverification.json"
        preverification_path.write_text(
            "not-json" if reuse == "malformed" else json.dumps(structural), encoding="utf-8",
        )
    transport_failure(repo, run_id=run_id, head_sha=head, run_path=run_path,
                      failure_path=failure_path, preverification_path=preverification_path,
                      execution_profile_path=profile_path)
    git(repo, "switch", "--quiet", "main")
    payload = {"repair_id": "REPAIR-101-001", "failed_run_id": run_id,
               "failed_head_sha": head, "task": task, "action": action,
               "modification_scope": [] if action in ("NO_CHANGE", "FINALIZE_CANDIDATE") else ["OUTPUT.txt"],
               "instructions": ["Use the exact authorized correction."],
               "constraints": ["Commit the output."]}
    body = {"format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_REPAIR",
            "identity": {"failed_run_id": run_id},
            "expected_state": {"expected_failed_head_sha": head}, "payload": payload}
    event_doc = _event(json.dumps(body))
    event_doc["issue"]["number"] = 168
    return repo, _write_event(tmp_path, event_doc), _write_policy(tmp_path)


def test_repair_failure_transport_rejects_malformed_package_before_publication(tmp_path, monkeypatch):
    from aios_renew import review_transport
    from aios_renew.operator import _runtime_paths_readonly
    from tests.operator_test_support import _runtime_bytes, git

    transport = review_transport.transport_failure
    attempts = []

    def observe_transport(repo, **kwargs):
        assert kwargs["preverification_path"].read_bytes() == b"not-json"
        attempts.append((repo, git(repo, "ls-remote", "--refs", "origin"),
                         git(repo, "rev-parse", "HEAD"), _runtime_bytes(repo)))
        return transport(repo, **kwargs)

    monkeypatch.setattr(review_transport, "transport_failure", observe_transport)
    with pytest.raises(review_transport.ReviewTransportError, match="invalid canonical public artifact JSON"):
        repair_ingress_fixture(tmp_path, action="FINALIZE_CANDIDATE", reuse="malformed")

    assert len(attempts) == 1
    repo, refs, head, runtime = attempts[0]
    assert git(repo, "ls-remote", "--refs", "origin") == refs
    assert "refs/heads/aios/" not in refs
    assert git(repo, "rev-parse", "HEAD") == head
    assert _runtime_bytes(repo) == runtime
    assert not _runtime_paths_readonly(repo).runs.exists()


@pytest.mark.parametrize("action,reuse,route", [
    ("CODE_FIX", "absent", "HUMAN_WAKEUP_REQUIRED"),
    ("CONTINUE_IMPLEMENTATION", "absent", "HUMAN_WAKEUP_REQUIRED"),
    ("FINALIZE_CANDIDATE", "absent", "HUMAN_WAKEUP_REQUIRED"),
    ("FINALIZE_CANDIDATE", "eligible", "EXECUTORLESS"),
    ("NO_CHANGE", "eligible", "EXECUTORLESS"),
])
def test_ingress_repair_real_transport_routes_once_without_run_or_verification(tmp_path, action, reuse, route):
    from tests.operator_test_support import _runtime_bytes, git
    from aios_renew.operator import _runtime_paths_readonly
    from tests.test_github_issue_ingress_workflow import repair_consumer_steps

    repo, event, policy = repair_ingress_fixture(tmp_path, action=action, reuse=reuse)
    before = _runtime_bytes(repo)
    main = git(repo, "rev-parse", "HEAD")
    delivery = carrier.deliver_event(event, policy, repo=repo)
    assert delivery.ingress_result.status == "CANONICALIZED"
    outputs = dict(line.split("=", 1) for line in delivery.github_outputs().splitlines())
    assert outputs["repair_wakeup_route"] == route
    consumer = repair_consumer_steps(outputs)
    assert consumer["Dispatch safe REPAIR wakeup once"] is (route == "EXECUTORLESS")
    assert not consumer["Observe rejected REPAIR dispatch"]
    assert consumer["Preserve failed delivery outcome"] is (route == "EXECUTORLESS")
    assert outputs["repair_dispatch_id"] == f"repair-{outputs['failed_run_id']}-{outputs['repair_sha']}"
    assert delivery.repair_wakeup_reason == (
        "EXECUTOR_REQUIRED" if route == "HUMAN_WAKEUP_REQUIRED" else "CURRENT_EXECUTORLESS_REPAIR"
    )
    receipt = delivery.render_receipt()
    assert "github-issue:trung-via/AIOS-renew#168@trung-via" in receipt
    for key in ("failed_run_id", "repair_sha", "repair_dispatch_id"):
        assert f"{key}: {outputs[key]}" in receipt
    assert "run_created: false" in receipt and "executor_invoked: false" in receipt
    assert "protected fixture diagnostic" not in receipt
    replay = carrier.deliver_event(event, policy, repo=repo)
    assert replay.ingress_result.replayed and replay.github_outputs() == ""
    assert not any(repair_consumer_steps({}).values())
    assert git(repo, "rev-parse", "HEAD") == main
    assert git(repo, "ls-remote", "--refs", "origin", "refs/heads/main").split()[0] == main
    assert not _runtime_paths_readonly(repo).runs.exists()
    assert _runtime_bytes(repo) == before
    assert not git(repo, "ls-remote", "--refs", "origin", "refs/heads/aios/artifacts/*")


def _repair_attestations(action="FINALIZE_CANDIDATE", required=False):
    from aios_renew.correction_preflight import CorrectionPreflightResult
    from aios_renew.unified_state import UnifiedStateObservation

    proof = CorrectionPreflightResult(
        "REPAIR", "READY", "READY", "READY", task_id="TASK-101", task_revision=1,
        failed_run_id="RUN-101-001", failed_head_sha="b" * 40, subject_mode="HISTORICAL",
        action=action, executor_required=required, authorization_sha="a" * 40,
    ).as_dict()
    document = {"task": proof["task"], "failed_run_id": proof["failed_run_id"],
                "failed_head_sha": proof["failed_head_sha"], "action": action}
    state = UnifiedStateObservation(
        "TASK-101", 1, "CORRECTION", "EXECUTE_REPAIR", run_id="RUN-101-001",
        failed_run_id="RUN-101-001", failed_head_sha="b" * 40, correction_sha="a" * 40,
        correction=proof, correction_document=document,
    ).as_dict()
    # Keep the independent attestations independent under adversarial mutation.
    return json.loads(json.dumps(proof)), json.loads(json.dumps(state)), document


def _install_repair_attestations(monkeypatch, proof, state, document, *, current=None):
    from types import SimpleNamespace
    from aios_renew import correction_preflight, unified_state

    calls = []
    def preflight(failed_run_id, *, repo, required_repair_sha):
        calls.append((failed_run_id, repo, required_repair_sha))
        value = current if current is not None and len(calls) > 1 else proof
        return SimpleNamespace(as_dict=lambda: value)
    monkeypatch.setattr(correction_preflight, "preflight_repair", preflight)
    monkeypatch.setattr(unified_state, "observe_unified_state", lambda *a, **k: SimpleNamespace(
        as_dict=lambda: state, correction_document=document,
    ))
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **k: IngressResult(
        operation="AUTHOR_REPAIR", canonical_destination="refs/heads/aios/repair/RUN-101-001",
        canonical_sha="a" * 40,
    ))
    return calls


@pytest.mark.parametrize("fault", [
    "proof_missing", "proof_blocked", "proof_requirement_missing", "proof_requirement_string",
    "proof_requirement_number", "proof_sha", "proof_run", "proof_version", "proof_task_revision",
    "proof_family", "proof_head", "state_missing", "state_requirement", "state_requirement_number",
    "state_proof_missing", "state_sha", "state_run", "state_head", "state_task", "state_task_boolean",
    "state_blocker", "state_action", "state_effect", "document_missing", "document_action",
    "document_run", "document_task", "moved_proof", "moved_requirement", "contradictory_action",
])
def test_ingress_repair_missing_conflicting_or_moved_attestation_never_grants_dispatch(tmp_path, monkeypatch, fault):
    proof, state, document = _repair_attestations()
    current = None
    if fault == "proof_missing":
        proof = None
    elif fault.startswith("proof_"):
        key, value = {
            "proof_blocked": ("status", "BLOCKED"), "proof_requirement_missing": ("executor_required", None),
            "proof_requirement_string": ("executor_required", "false"), "proof_requirement_number": ("executor_required", 0),
            "proof_sha": ("authorization_sha", "c" * 40), "proof_run": ("failed_run_id", "RUN-102-001"),
            "proof_version": ("version", True), "proof_family": ("family", "REMEDIATION"),
            "proof_head": ("failed_head_sha", "not-a-sha"), "proof_task_revision": ("task", {"id": "TASK-101", "revision": True}),
        }[fault]
        proof[key] = value
    elif fault == "state_missing":
        state = None
    elif fault in ("state_requirement", "state_requirement_number"):
        state["correction_preflight"]["executor_required"] = True if fault == "state_requirement" else 0
    elif fault.startswith("state_"):
        key, value = {
            "state_proof_missing": ("correction_preflight", None), "state_sha": ("correction_sha", "c" * 40),
            "state_run": ("failed_run_id", "RUN-102-001"), "state_head": ("failed_head_sha", "c" * 40),
            "state_task": ("task", {"id": "TASK-102", "revision": 1}),
            "state_task_boolean": ("task", {"id": "TASK-101", "revision": True}),
            "state_blocker": ("blocker", {"code": "STALE"}), "state_action": ("next_action", "WAIT"),
            "state_effect": ("run_created", True),
        }[fault]
        state[key] = value
    elif fault == "document_missing":
        document = None
    elif fault.startswith("document_"):
        key, value = {"document_action": ("action", "CODE_FIX"),
                      "document_run": ("failed_run_id", "RUN-102-001"),
                      "document_task": ("task", {"id": "TASK-102", "revision": 1})}[fault]
        document[key] = value
    elif fault.startswith("moved_"):
        current = dict(proof)
        current["authorization_sha" if fault == "moved_proof" else "executor_required"] = (
            "c" * 40 if fault == "moved_proof" else True
        )
    else:
        proof, state, document = _repair_attestations(action="CODE_FIX", required=False)
    _install_repair_attestations(monkeypatch, proof, state, document, current=current)
    delivery = carrier.deliver_event(_write_event(tmp_path, _event()), _write_policy(tmp_path), repo=tmp_path)
    assert delivery.repair_wakeup_route == "HUMAN_WAKEUP_REQUIRED"
    assert "repair_wakeup_route=EXECUTORLESS" not in delivery.github_outputs()
    assert "human_action:" in delivery.render_receipt()


@pytest.mark.parametrize("boundary", ["preflight", "unified"])
def test_ingress_repair_observation_exception_is_bounded_and_does_not_undo_authoring(tmp_path, monkeypatch, boundary):
    from aios_renew import correction_preflight, unified_state

    proof, state, document = _repair_attestations()
    _install_repair_attestations(monkeypatch, proof, state, document)
    def unavailable(*a, **k):
        raise RuntimeError("private path / token / transport stderr")
    module, name = (correction_preflight, "preflight_repair") if boundary == "preflight" else (unified_state, "observe_unified_state")
    monkeypatch.setattr(module, name, unavailable)
    output = tmp_path / "output"
    event, policy = _write_event(tmp_path, _event()), _write_policy(tmp_path)
    assert carrier.main(["--event", str(event), "--policy", str(policy), "--repo", str(tmp_path),
                         "--output", str(output)]) == 0
    assert "repair_wakeup_reason=CURRENTNESS_UNAVAILABLE" in output.read_text(encoding="utf-8")
    delivery = carrier.deliver_event(event, policy, repo=tmp_path)
    assert delivery.ingress_result.status == "CANONICALIZED"
    assert "private path" not in delivery.render_receipt()


def test_ingress_repair_human_action_and_exact_selectors_survive_receipt_bound():
    delivery = carrier.IssueDelivery(
        "github-issue:trung-via/AIOS-renew#168@trung-via",
        IngressResult(operation="AUTHOR_REPAIR", canonical_sha="a" * 40,
                      canonical_destination="refs/heads/aios/repair/RUN-101-001", detail="x" * 10_000),
        repair_wakeup_reason="EXECUTOR_REQUIRED",
    )
    receipt = delivery.render_receipt()
    assert len(receipt) <= 3500 and receipt.endswith("[truncated]")
    assert "repair_wakeup: HUMAN_WAKEUP_REQUIRED" in receipt
    assert "reason: EXECUTOR_REQUIRED" in receipt and "human_action:" in receipt
    assert f"repair_sha: {delivery.repair_sha}" in receipt
    assert f"repair_dispatch_id: {delivery.repair_dispatch_id}" in receipt
    assert "failed_run_id: RUN-101-001" in receipt


def test_task372_author_repair_main_records_original_source_after_existing_preflight(tmp_path, monkeypatch):
    from aios_renew.operational_receipt import validate_repair_source
    proof, state, document = _repair_attestations(action="NO_CHANGE")
    calls = _install_repair_attestations(monkeypatch, proof, state, document)
    event = _event()
    event["issue"]["id"] = 10777
    event["sender"] = {"login": "trung-via", "type": "User"}
    path = _write_event(tmp_path, event)
    source = tmp_path / "source.json"
    for name, value in {"GITHUB_REPOSITORY": "trung-via/AIOS-renew", "GITHUB_ACTOR": "trung-via",
            "GITHUB_EVENT_NAME": "issues", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": "c" * 40,
            "GITHUB_RUN_ID": "1001", "GITHUB_RUN_ATTEMPT": "1", "AIOS_REPAIR_SOURCE_PATH": str(source)}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GITHUB_TRIGGERING_ACTOR", raising=False)
    arguments = ["--event", str(path), "--policy", str(_write_policy(tmp_path)), "--repo", str(tmp_path),
                 "--output", str(tmp_path / "outputs")]
    assert carrier.main(arguments) == 0
    observed = validate_repair_source(json.loads(source.read_bytes()))
    assert observed["lane"] == "ingress" and observed["issue"]["id"] == 10777
    assert observed["original_actor"] == "trung-via" and observed["repair_sha"] == "a" * 40
    assert observed["delivery_id"] == "repair-RUN-101-001-" + "a" * 40
    assert calls == [("RUN-101-001", tmp_path, "a" * 40)] * 2
    before = source.read_bytes()
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **k: IngressResult(
        operation="AUTHOR_REPAIR", status="IDEMPOTENT", replayed=True,
        canonical_destination="refs/heads/aios/repair/RUN-101-001", canonical_sha="a" * 40))
    assert carrier.main(arguments) == 0 and source.read_bytes() == before
    assert len(calls) == 2  # No preflight replay, delivery, retry or RUN admission.


@pytest.mark.parametrize("action", ["NO_CHANGE", "FINALIZE_CANDIDATE"])
def test_ingress_repair_executorless_route_rechecks_exact_subject_and_replay_is_read_only(tmp_path, monkeypatch, action):
    from aios_renew import correction_preflight, unified_state

    proof, state, document = _repair_attestations(action=action)
    calls = _install_repair_attestations(monkeypatch, proof, state, document)
    event, policy = _write_event(tmp_path, _event()), _write_policy(tmp_path)
    delivery = carrier.deliver_event(event, policy, repo=tmp_path)
    assert delivery.repair_wakeup_route == "EXECUTORLESS"
    assert calls == [("RUN-101-001", tmp_path, "a" * 40)] * 2
    monkeypatch.setattr(carrier, "ingest_carrier", lambda *a, **k: IngressResult(
        operation="AUTHOR_REPAIR", status="IDEMPOTENT", replayed=True,
        canonical_destination=delivery.ingress_result.canonical_destination, canonical_sha="a" * 40,
    ))
    for module, name in ((correction_preflight, "preflight_repair"), (unified_state, "observe_unified_state")):
        monkeypatch.setattr(module, name, lambda *a, **k: pytest.fail("replay must not observe or reschedule"))
    assert carrier.deliver_event(event, policy, repo=tmp_path).github_outputs() == ""
