from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from aios_renew import github_issue_remediation_intent as carrier


POLICY = {
    "format": "AIOS_BRAIN_REMEDIATION_INTENT_CARRIERS_POLICY",
    "version": 1,
    "github_issue": {
        "enabled": True,
        "repository": "trung-via/AIOS-renew",
        "authorized_actors": ["trung-via"],
        "title_marker": "[AIOS REMEDIATION INTENT]",
        "max_body_bytes": 4096,
    },
}


def _body(**updates: object) -> str:
    request: dict[str, object] = {
        "format": "AIOS_REMEDIATION_INTENT_REQUEST",
        "version": 1,
        "correction_dispatch_id": "remediation-intent-112",
        "source_run_id": "RUN-110-001",
        "finding_id": "F1",
        "executor": "codex",
    }
    request.update(updates)
    return yaml.safe_dump(request, sort_keys=False)


def _event(body: object | None = None) -> dict[str, object]:
    return {
        "action": "opened",
        "sender": {"login": "trung-via"},
        "repository": {"full_name": "trung-via/AIOS-renew"},
        "issue": {
            "number": 112,
            "title": "[AIOS REMEDIATION INTENT]",
            "user": {"login": "trung-via"},
            "body": _body() if body is None else body,
        },
    }


def _write_policy(tmp_path: Path, policy: object = POLICY) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    return path


def _write_event(tmp_path: Path, event: object) -> Path:
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
    return path


def test_valid_issue_forwards_only_four_selectors_and_binds_event_actor(
    tmp_path: Path,
) -> None:
    policy = carrier.load_policy(_write_policy(tmp_path))
    request = carrier.admit_event(_write_event(tmp_path, _event()), policy)

    assert request == carrier.RemediationIntentRequest(
        correction_dispatch_id="remediation-intent-112",
        source_run_id="RUN-110-001",
        finding_id="F1",
        executor="codex",
        approver="trung-via",
    )
    assert request.github_outputs().splitlines() == [
        "correction_dispatch_id=remediation-intent-112",
        "source_run_id=RUN-110-001",
        "finding_id=F1",
        "executor=codex",
    ]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda event: event.update(action="edited"), "action"),
        (lambda event: event["repository"].update(full_name="other/repo"), "repository"),
        (lambda event: event["sender"].update(login="intruder"), "actor"),
        (lambda event: event["issue"]["user"].update(login="intruder"), "actor"),
        (lambda event: event["issue"].update(title="almost"), "title"),
        (lambda event: event["issue"].update(number=0), "number"),
        (lambda event: event["issue"].update(body=None), "body"),
        (lambda event: event["issue"].update(body=""), "empty"),
        (lambda event: event["issue"].update(body="x" * 4097), "bound"),
    ],
)
def test_wrong_or_malformed_event_fails_before_dispatch(
    tmp_path: Path, mutation, reason: str
) -> None:
    event = _event()
    mutation(event)
    policy = carrier.load_policy(_write_policy(tmp_path))
    with pytest.raises(carrier.GitHubIssueRemediationIntentError, match=reason):
        carrier.admit_event(_write_event(tmp_path, event), policy)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda policy: policy["github_issue"].update(enabled=False),
        lambda policy: policy["github_issue"].update(repository="other/repo"),
        lambda policy: policy["github_issue"].update(
            authorized_actors=["trung-via", "someone-else"]
        ),
        lambda policy: policy["github_issue"].update(title_marker="almost"),
        lambda policy: policy["github_issue"].update(max_body_bytes=0),
        lambda policy: policy.update(unreviewed_authority=True),
    ],
)
def test_policy_is_versioned_and_fully_bound(tmp_path: Path, mutation) -> None:
    policy = json.loads(json.dumps(POLICY))
    mutation(policy)
    with pytest.raises(carrier.GitHubIssueRemediationIntentError):
        carrier.load_policy(_write_policy(tmp_path, policy))


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"task_id": "TASK-112"}, "missing or unknown"),
        ({"review_id": "REVIEW-1"}, "missing or unknown"),
        ({"action": "CODE_FIX"}, "missing or unknown"),
        ({"remediation_sha": "a" * 40}, "missing or unknown"),
        ({"command": "$(git push)"}, "missing or unknown"),
        ({"workflow": "owned.yml"}, "missing or unknown"),
        ({"ref": "attacker"}, "missing or unknown"),
        ({"correction_dispatch_id": "../escape"}, "correction_dispatch_id"),
        ({"source_run_id": "RUN-1; touch owned"}, "source_run_id"),
        ({"finding_id": "$(whoami)"}, "finding_id"),
        ({"executor": "fallback"}, "executor"),
        ({"executor": "antigravity-minimax"}, "executor"),
    ],
)
def test_unknown_authority_and_malicious_values_are_inert_and_rejected(
    updates: dict[str, object], reason: str
) -> None:
    with pytest.raises(carrier.GitHubIssueRemediationIntentError, match=reason):
        carrier.parse_request(_body(**updates))


def test_missing_and_duplicate_fields_fail_closed() -> None:
    missing = """format: AIOS_REMEDIATION_INTENT_REQUEST
version: 1
correction_dispatch_id: remediation-intent-112
source_run_id: RUN-110-001
finding_id: F1
"""
    duplicate = _body() + "executor: antigravity\n"
    with pytest.raises(carrier.GitHubIssueRemediationIntentError, match="missing"):
        carrier.parse_request(missing)
    with pytest.raises(carrier.GitHubIssueRemediationIntentError, match="duplicate"):
        carrier.parse_request(duplicate)


def test_cli_emits_no_outputs_on_rejection_and_bounds_receipt(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output.txt"
    receipt = tmp_path / "receipt.txt"
    event = _event(_body(command="git push"))
    code = carrier.main(
        [
            "--event",
            str(_write_event(tmp_path, event)),
            "--policy",
            str(_write_policy(tmp_path)),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ]
    )
    assert code == 1
    assert not output.exists()
    text = receipt.read_text(encoding="utf-8")
    assert len(text) <= 3500
    assert "status: REJECTED" in text
    assert "a3_approval: not_observed" in text
    assert "remediation_run_outcome: not_observed" in text
