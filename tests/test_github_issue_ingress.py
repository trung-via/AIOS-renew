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
