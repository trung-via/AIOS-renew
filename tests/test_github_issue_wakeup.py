from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from aios_renew import github_issue_wakeup as carrier


POLICY = {
    "format": "AIOS_BRAIN_WAKEUP_CARRIERS_POLICY",
    "version": 1,
    "github_issue": {
        "enabled": True,
        "repository": "trung-via/AIOS-renew",
        "authorized_actors": ["trung-via"],
        "title_marker": "[AIOS BRAIN WAKEUP]",
        "max_body_bytes": 4096,
    },
}


def _body(**updates: object) -> str:
    request: dict[str, object] = {
        "format": "AIOS_PRIMARY_WAKEUP_REQUEST",
        "version": 1,
        "dispatch_id": "brain-wakeup-108",
        "task_id": "TASK-108",
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
            "number": 108,
            "title": "[AIOS BRAIN WAKEUP]",
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
    path.write_text(
        json.dumps(event, ensure_ascii=False), encoding="utf-8"
    )
    return path


def test_valid_issue_produces_only_the_three_sanitized_a1_inputs(
    tmp_path: Path,
) -> None:
    policy = carrier.load_policy(_write_policy(tmp_path))
    request = carrier.admit_event(_write_event(tmp_path, _event()), policy)

    assert request == carrier.WakeupRequest(
        dispatch_id="brain-wakeup-108",
        task_id="TASK-108",
        executor="codex",
    )
    assert request.github_outputs().splitlines() == [
        "dispatch_id=brain-wakeup-108",
        "task_id=TASK-108",
        "executor=codex",
    ]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda event: event.update(action="edited"), "action"),
        (
            lambda event: event["repository"].update(full_name="other/repo"),
            "repository",
        ),
        (
            lambda event: event["sender"].update(login="intruder"),
            "actor",
        ),
        (
            lambda event: event["issue"]["user"].update(login="intruder"),
            "actor",
        ),
        (
            lambda event: event["issue"].update(title="almost"),
            "title",
        ),
        (lambda event: event["issue"].update(number=0), "number"),
        (lambda event: event["issue"].update(body=None), "body"),
        (lambda event: event["issue"].update(body=""), "empty"),
        (lambda event: event["issue"].update(body="x" * 4097), "bound"),
    ],
)
def test_invalid_event_fails_before_request_can_be_forwarded(
    tmp_path: Path, mutation, reason: str
) -> None:
    event = _event()
    mutation(event)
    policy = carrier.load_policy(_write_policy(tmp_path))
    with pytest.raises(carrier.GitHubIssueWakeupError, match=reason):
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
def test_policy_is_versioned_and_fully_bound(
    tmp_path: Path, mutation
) -> None:
    policy = json.loads(json.dumps(POLICY))
    mutation(policy)
    with pytest.raises(carrier.GitHubIssueWakeupError):
        carrier.load_policy(_write_policy(tmp_path, policy))


@pytest.mark.parametrize(
    ("request", "reason"),
    [
        ({}, "missing or unknown"),
        ({"workflow": "owned.yml"}, "missing or unknown"),
        ({"ref": "attacker"}, "missing or unknown"),
        ({"command": "git push --force"}, "missing or unknown"),
        ({"runner": "self-hosted"}, "missing or unknown"),
        ({"model": "caller-selected"}, "missing or unknown"),
        ({"reasoning": "max"}, "missing or unknown"),
        ({"verification": "skip"}, "missing or unknown"),
        ({"credentials": "token"}, "missing or unknown"),
        ({"dispatch_id": "../escape"}, "dispatch_id"),
        ({"dispatch_id": "$(git push)"}, "dispatch_id"),
        ({"task_id": "TASK-108; touch owned"}, "task_id"),
        ({"executor": "fallback"}, "executor"),
    ],
)
def test_unknown_authority_fields_and_malicious_values_are_rejected(
    request: dict[str, object], reason: str
) -> None:
    with pytest.raises(carrier.GitHubIssueWakeupError, match=reason):
        carrier.parse_request(_body(**request))


def test_missing_field_and_duplicate_key_fail_closed() -> None:
    missing = """format: AIOS_PRIMARY_WAKEUP_REQUEST
version: 1
dispatch_id: brain-wakeup-108
task_id: TASK-108
"""
    duplicate = _body() + "executor: antigravity\n"
    with pytest.raises(carrier.GitHubIssueWakeupError, match="missing"):
        carrier.parse_request(missing)
    with pytest.raises(carrier.GitHubIssueWakeupError, match="duplicate"):
        carrier.parse_request(duplicate)


def test_cli_writes_outputs_only_after_admission_and_bounds_rejections(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output.txt"
    receipt = tmp_path / "receipt.txt"
    event = _event()
    event["issue"]["body"] = _body(command="git push")
    exit_code = carrier.main(
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

    assert exit_code == 1
    assert not output.exists()
    text = receipt.read_text(encoding="utf-8")
    assert len(text) <= 3500
    assert "status: REJECTED" in text
    assert "dispatch_accepted: false" in text
    assert "execution_outcome: not_observed" in text
