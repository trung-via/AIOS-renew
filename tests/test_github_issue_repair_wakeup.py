from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from aios_renew import github_issue_repair_wakeup as carrier


POLICY = {
    "format": "AIOS_BRAIN_REPAIR_WAKEUP_CARRIERS_POLICY",
    "version": 1,
    "github_issue": {
        "enabled": True,
        "repository": "trung-via/AIOS-renew",
        "authorized_actors": ["trung-via"],
        "title_marker": "[AIOS REPAIR WAKEUP]",
        "max_body_bytes": 4096,
    },
}


def _body(*, executor: object = "codex", **updates: object) -> str:
    request: dict[str, object] = {
        "format": "AIOS_REPAIR_WAKEUP_REQUEST",
        "version": 1,
        "repair_dispatch_id": "repair-111",
        "failed_run_id": "RUN-111-001",
        "repair_sha": "a" * 40,
    }
    if executor is not None:
        request["executor"] = executor
    request.update(updates)
    return yaml.safe_dump(request, sort_keys=False)


def _event(body: object | None = None) -> dict[str, object]:
    return {
        "action": "opened",
        "sender": {"login": "trung-via"},
        "repository": {"full_name": "trung-via/AIOS-renew"},
        "issue": {
            "number": 111,
            "title": "[AIOS REPAIR WAKEUP]",
            "user": {"login": "trung-via"},
            "body": _body() if body is None else body,
        },
    }


def _write(tmp_path: Path, name: str, value: object) -> Path:
    path = tmp_path / name
    if name.endswith(".json"):
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def test_valid_issue_forwards_only_four_bounded_selectors(tmp_path: Path) -> None:
    policy = carrier.load_policy(_write(tmp_path, "policy.yaml", POLICY))
    request = carrier.admit_event(_write(tmp_path, "event.json", _event()), policy)
    assert request == carrier.RepairWakeupRequest(
        "repair-111", "RUN-111-001", "a" * 40, "codex", "trung-via"
    )
    assert request.github_outputs().splitlines() == [
        "repair_dispatch_id=repair-111",
        "failed_run_id=RUN-111-001",
        f"repair_sha={'a' * 40}",
        "executor=codex",
    ]


def test_downstream_policy_preserves_repair_family_and_actor_binding(
    tmp_path: Path,
) -> None:
    downstream = json.loads(json.dumps(POLICY))
    downstream["github_issue"]["repository"] = "trung-via/python_complete_agent"
    downstream["github_issue"]["authorized_actors"] = [
        "downstream-owner",
        "release-bot",
    ]
    policy = carrier.load_policy(_write(tmp_path, "policy.yaml", downstream))
    event = _event()
    event["repository"]["full_name"] = "trung-via/python_complete_agent"
    event["sender"]["login"] = "release-bot"
    event["issue"]["user"]["login"] = "release-bot"

    request = carrier.admit_event(_write(tmp_path, "event.json", event), policy)

    assert request.actor == "release-bot"
    assert request.github_outputs().splitlines() == [
        "repair_dispatch_id=repair-111",
        "failed_run_id=RUN-111-001",
        f"repair_sha={'a' * 40}",
        "executor=codex",
    ]
    event["repository"]["full_name"] = "trung-via/AIOS-renew"
    with pytest.raises(carrier.GitHubIssueRepairWakeupError, match="repository"):
        carrier.admit_event(_write(tmp_path, "event.json", event), policy)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda policy: policy["github_issue"].update(repository="missing-owner"),
        lambda policy: policy["github_issue"].update(authorized_actors=[]),
        lambda policy: policy["github_issue"].update(
            authorized_actors=["trung-via", "trung-via"]
        ),
        lambda policy: policy["github_issue"].update(authorized_actors=["bad_actor"]),
    ],
)
def test_malformed_repository_and_actor_policy_fails_closed(
    tmp_path: Path, mutation
) -> None:
    policy = json.loads(json.dumps(POLICY))
    mutation(policy)
    with pytest.raises(carrier.GitHubIssueRepairWakeupError):
        carrier.load_policy(_write(tmp_path, "policy.yaml", policy))


def test_no_change_shape_omits_executor_authority() -> None:
    request = carrier.parse_request(_body(executor=None))
    assert request.executor is None
    assert request.github_outputs().splitlines()[-1] == "executor="


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"operation": "REMEDIATION"}, "unknown"),
        ({"task_id": "TASK-111"}, "unknown"),
        ({"action": "CODE_FIX"}, "unknown"),
        ({"scope": ["src/owned.py"]}, "unknown"),
        ({"workflow": "attacker.yml"}, "unknown"),
        ({"ref": "attacker"}, "unknown"),
        ({"command": "git push"}, "unknown"),
        ({"repair_dispatch_id": "../escape"}, "repair_dispatch_id"),
        ({"failed_run_id": "RUN;owned"}, "failed_run_id"),
        ({"repair_sha": "HEAD"}, "repair_sha"),
        ({"executor": "fallback"}, "executor"),
    ],
)
def test_untrusted_authority_and_malformed_selectors_fail_closed(
    updates: dict[str, object], reason: str
) -> None:
    with pytest.raises(carrier.GitHubIssueRepairWakeupError, match=reason):
        carrier.parse_request(_body(**updates))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda event: event.update(action="edited"),
        lambda event: event["repository"].update(full_name="other/repo"),
        lambda event: event["sender"].update(login="intruder"),
        lambda event: event["issue"].update(title="almost"),
        lambda event: event["issue"].update(body=""),
        lambda event: event["issue"].update(body="x" * 4097),
    ],
)
def test_wrong_event_framing_fails_before_forwarding(tmp_path: Path, mutation) -> None:
    event = _event()
    mutation(event)
    policy = carrier.load_policy(_write(tmp_path, "policy.yaml", POLICY))
    with pytest.raises(carrier.GitHubIssueRepairWakeupError):
        carrier.admit_event(_write(tmp_path, "event.json", event), policy)


def test_duplicate_key_and_missing_selector_fail_closed() -> None:
    duplicate = _body() + "repair_sha: " + "b" * 40 + "\n"
    missing = _body().replace("failed_run_id: RUN-111-001\n", "")
    with pytest.raises(carrier.GitHubIssueRepairWakeupError, match="duplicate"):
        carrier.parse_request(duplicate)
    with pytest.raises(carrier.GitHubIssueRepairWakeupError, match="missing"):
        carrier.parse_request(missing)


def test_cli_rejection_writes_no_outputs_and_never_claims_success(
    tmp_path: Path,
) -> None:
    output = tmp_path / "outputs.txt"
    receipt = tmp_path / "receipt.txt"
    event = _event(_body(command="owned"))
    code = carrier.main(
        [
            "--event",
            str(_write(tmp_path, "event.json", event)),
            "--policy",
            str(_write(tmp_path, "policy.yaml", POLICY)),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ]
    )
    assert code == 1
    assert not output.exists()
    text = receipt.read_text(encoding="utf-8")
    assert "status: REJECTED" in text
    assert "repair_run_outcome: not_observed" in text
    assert "publication: not_observed" in text


def test_non_target_brain_ingress_event_rejection_remains_bounded_to_transport(
    tmp_path: Path,
) -> None:
    output = tmp_path / "outputs.txt"
    receipt = tmp_path / "receipt.txt"
    non_target_event = _event()
    non_target_event["issue"]["title"] = "[AIOS BRAIN INGRESS]"
    policy_path = _write(tmp_path, "policy.yaml", POLICY)
    event_path = _write(tmp_path, "event.json", non_target_event)

    policy = carrier.load_policy(policy_path)
    with pytest.raises(carrier.GitHubIssueRepairWakeupError, match="title"):
        carrier.admit_event(event_path, policy)

    code = carrier.main(
        [
            "--event",
            str(event_path),
            "--policy",
            str(policy_path),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ]
    )
    assert code == 1
    assert not output.exists()
    text = receipt.read_text(encoding="utf-8")
    assert "status: REJECTED" in text
    assert "dispatch_accepted: false" in text
    assert "repair_run_outcome: not_observed" in text
    assert "verification: not_observed" in text
    assert "semantic_review: not_observed" in text
    assert "publication: not_observed" in text
    assert "reason: event Issue title marker is invalid" in text
