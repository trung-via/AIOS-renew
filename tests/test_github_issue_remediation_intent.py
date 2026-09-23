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
        "version": 2,
        "correction_dispatch_id": "remediation-intent-112",
        "source_run_id": "RUN-110-001",
        "finding_id": "F1",
        "executor": "codex",
        "model": None,
        "reasoning_effort": None,
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
        model="gpt-6-sol",
        reasoning_effort="medium",
        model_source="REPOSITORY_DEFAULT",
        effort_source="REPOSITORY_DEFAULT",
    )
    assert request.github_outputs().splitlines() == [
        "correction_dispatch_id=remediation-intent-112",
        "source_run_id=RUN-110-001",
        "finding_id=F1",
        "executor=codex",
        "model=gpt-6-sol",
        "reasoning_effort=medium",
        "model_source=REPOSITORY_DEFAULT",
        "effort_source=REPOSITORY_DEFAULT",
    ]


def test_downstream_policy_preserves_trusted_actor_and_sanitized_selectors(
    tmp_path: Path,
) -> None:
    downstream = json.loads(json.dumps(POLICY))
    downstream["github_issue"]["repository"] = "trung-via/python_complete_agent"
    downstream["github_issue"]["authorized_actors"] = [
        "downstream-owner",
        "release-bot",
    ]
    policy = carrier.load_policy(_write_policy(tmp_path, downstream))
    event = _event()
    event["repository"]["full_name"] = "trung-via/python_complete_agent"
    event["sender"]["login"] = "release-bot"
    event["issue"]["user"]["login"] = "release-bot"

    request = carrier.admit_event(_write_event(tmp_path, event), policy)

    assert request.approver == "release-bot"
    assert request.github_outputs().splitlines() == [
        "correction_dispatch_id=remediation-intent-112",
        "source_run_id=RUN-110-001",
        "finding_id=F1",
        "executor=codex",
        "model=gpt-6-sol",
        "reasoning_effort=medium",
        "model_source=REPOSITORY_DEFAULT",
        "effort_source=REPOSITORY_DEFAULT",
    ]
    event["sender"]["login"] = "intruder"
    with pytest.raises(carrier.GitHubIssueRemediationIntentError, match="actor"):
        carrier.admit_event(_write_event(tmp_path, event), policy)


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
        lambda policy: policy["github_issue"].update(repository="missing-owner"),
        lambda policy: policy["github_issue"].update(authorized_actors=[]),
        lambda policy: policy["github_issue"].update(
            authorized_actors=["trung-via", "trung-via"]
        ),
        lambda policy: policy["github_issue"].update(authorized_actors=["bad_actor"]),
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
        ({"version": 1}, "version"),
    ],
)
def test_unknown_authority_and_malicious_values_are_inert_and_rejected(
    updates: dict[str, object], reason: str
) -> None:
    with pytest.raises(carrier.GitHubIssueRemediationIntentError, match=reason):
        carrier.parse_request(_body(**updates))


def test_missing_and_duplicate_fields_fail_closed() -> None:
    missing = """format: AIOS_REMEDIATION_INTENT_REQUEST
version: 2
correction_dispatch_id: remediation-intent-112
source_run_id: RUN-110-001
finding_id: F1
model:
reasoning_effort:
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


def test_issue_532_regression_remediation_package_relative_unavailable_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[1]
    canonical_policy = root / ".ai" / "executor-profiles.yaml"
    assert canonical_policy.is_file()

    original_loader = carrier.load_execution_profile_policy

    def explicit_only(source=None):
        if source is None:
            raise carrier.ExecutionProfileError("implicit profile policy unavailable")
        return original_loader(source)

    monkeypatch.setattr(carrier, "load_execution_profile_policy", explicit_only)
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    output = tmp_path / "output.txt"
    receipt = tmp_path / "receipt.txt"
    event = _event()
    policy_path = _write_policy(tmp_path)

    # 1. With explicit trusted canonical profile-policy, admission succeeds
    exit_code = carrier.main(
        [
            "--event",
            str(_write_event(tmp_path, event)),
            "--policy",
            str(policy_path),
            "--profile-policy",
            str(canonical_policy),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ]
    )
    assert exit_code == 0
    assert output.exists()
    assert "executor=codex" in output.read_text(encoding="utf-8")
    assert "status: ADMITTED" in receipt.read_text(encoding="utf-8")

    # 2. A sibling policy cannot replace the omitted trusted source
    output.unlink()
    receipt.unlink()
    isolated_policy = tmp_path / "isolated" / "policy.yaml"
    isolated_policy.parent.mkdir(parents=True, exist_ok=True)
    isolated_policy.write_text(policy_path.read_text(encoding="utf-8"), encoding="utf-8")
    (isolated_policy.parent / "executor-profiles.yaml").write_bytes(
        canonical_policy.read_bytes()
    )

    exit_code_isolated = carrier.main(
        [
            "--event",
            str(_write_event(tmp_path, event)),
            "--policy",
            str(isolated_policy),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ]
    )
    assert exit_code_isolated == 1
    assert not output.exists()
    receipt_text = receipt.read_text(encoding="utf-8")
    assert "status: REJECTED" in receipt_text
    assert "remediation_run_outcome: not_observed" in receipt_text


def test_missing_or_malformed_profile_policy_fails_remediation(tmp_path: Path) -> None:
    output = tmp_path / "output.txt"
    receipt = tmp_path / "receipt.txt"
    event = _event()

    # Missing profile policy file
    exit_code = carrier.main(
        [
            "--event",
            str(_write_event(tmp_path, event)),
            "--policy",
            str(_write_policy(tmp_path)),
            "--profile-policy",
            str(tmp_path / "missing-executor-profiles.yaml"),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ]
    )
    assert exit_code == 1
    assert not output.exists()
    assert "status: REJECTED" in receipt.read_text(encoding="utf-8")
    assert "not found" in receipt.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("model", "effort", "expected_model_source", "expected_effort_source"),
    [
        (None, None, "REPOSITORY_DEFAULT", "REPOSITORY_DEFAULT"),
        ("provider/custom-v1", None, "EXPLICIT", "REPOSITORY_DEFAULT"),
        (None, "high", "REPOSITORY_DEFAULT", "EXPLICIT"),
        ("provider/custom-v2", "low", "EXPLICIT", "EXPLICIT"),
    ],
)
def test_remediation_explicit_vs_default_source_attribution(
    model: str | None,
    effort: str | None,
    expected_model_source: str,
    expected_effort_source: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    canonical_policy = root / ".ai" / "executor-profiles.yaml"
    request = carrier.parse_request(
        _body(model=model, reasoning_effort=effort),
        profile_policy=canonical_policy,
    )
    assert request.model_source == expected_model_source
    assert request.effort_source == expected_effort_source
    if model is not None:
        assert request.model == model
    if effort is not None:
        assert request.reasoning_effort == effort
