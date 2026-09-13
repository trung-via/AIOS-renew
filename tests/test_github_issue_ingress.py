from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from aios_renew.authoring_ingress import AuthoringIngressError, IngressResult
from aios_renew import github_issue_ingress as carrier


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


def test_receipts_are_bounded() -> None:
    failure = carrier.render_failure(ValueError("x" * 10_000))
    assert len(failure) <= 3_500
    assert failure.endswith("[truncated]")


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
        ("AUTHOR_REPAIR", "refs/heads/aios/repair/RUN-108-002"),
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
