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
    def reject(source, *, repo, stdin_bytes=None):
        calls.append((source, stdin_bytes))
        raise AuthoringIngressError("audited authoring requires Stage-2 CANDIDATE")
    monkeypatch.setattr(carrier, "ingest_carrier", reject)
    output = tmp_path / "github-output"
    assert carrier.main(["--event", str(event), "--policy", str(policy),
                         "--repo", str(tmp_path), "--output", str(output)]) == 1
    assert calls == [("-", body.encode("utf-8"))]
    receipt = capsys.readouterr().out
    assert "status: FAIL" in receipt
    assert "CANDIDATE" in receipt
    assert "status: PASS" not in receipt
    assert not output.exists()


def test_receipts_are_bounded() -> None:
    failure = carrier.render_failure(ValueError("x" * 10_000))
    assert len(failure) <= 3_500
    assert failure.endswith("[truncated]")


def test_issue_carrier_canonicalizes_direct_final_task_without_audit(tmp_path, monkeypatch):
    from tests.test_authoring_ingress import setup_test_repo, V2_TASK_105_SOURCE, git

    repo, remote, main_sha = setup_test_repo(tmp_path)
    body = {"format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"}, "expected_state": {"expected_main_sha": main_sha},
            "payload": V2_TASK_105_SOURCE}
    event = _write_event(tmp_path, _event(json.dumps(body)))
    policy = _write_policy(tmp_path)
    monkeypatch.setattr(carrier, "admit_local", lambda *a, **k: pytest.fail("legacy TASK cannot consume origin proof"))
    delivery = carrier.deliver_event(event, policy, repo=repo)
    assert delivery.ingress_result.status == "CANONICALIZED"
    assert git(remote, "rev-parse", "refs/heads/main") == delivery.ingress_result.canonical_sha
    assert delivery.github_outputs() == ""
    assert carrier.deliver_event(event, policy, repo=repo).ingress_result.replayed


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


def test_successful_author_repair_emits_deterministic_repair_handoff(
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
    )
    assert delivery.github_outputs() == expected_outputs

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
