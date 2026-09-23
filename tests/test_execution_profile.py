"""Tests for execution profile policy, resolution, validation, and sidecar persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aios_renew.execution_profile import (
    EXECUTION_PROFILE_FORMAT,
    EXECUTION_PROFILE_VERSION,
    ExecutionProfileConflictError,
    ExecutionProfileError,
    ExecutionProfilePolicy,
    ExecutionProfilePolicyError,
    ExecutionProfileValidationError,
    ResolvedExecutionProfile,
    default_execution_profile,
    execution_profile_path,
    is_profile_managed_executor,
    load_execution_profile_policy,
    parse_execution_profile,
    persist_execution_profile,
    resolve_execution_profile,
    validate_model_identifier,
)


def test_default_policy_loads_and_has_exact_task_061_defaults() -> None:
    policy = load_execution_profile_policy()
    assert policy.format == "AIOS_EXECUTOR_PROFILES_POLICY"
    assert policy.version == 1
    assert policy.defaults["codex"]["model"] == "gpt-5.6-sol"
    assert policy.defaults["codex"]["reasoning_effort"] == "high"
    assert policy.defaults["antigravity"]["model"] == "gemini-3.8-flash"
    assert policy.defaults["antigravity"]["reasoning_effort"] == "high"
    assert set(policy.supported_efforts["codex"]) == {"low", "medium", "high"}
    assert set(policy.supported_efforts["antigravity"]) == {"low", "medium", "high"}


def test_is_profile_managed_executor() -> None:
    assert is_profile_managed_executor("codex") is True
    assert is_profile_managed_executor("antigravity") is True
    assert is_profile_managed_executor("antigravity-minimax") is False
    assert is_profile_managed_executor("other") is False


def test_model_identifier_validation() -> None:
    assert validate_model_identifier("gpt-5.6-sol") == "gpt-5.6-sol"
    assert validate_model_identifier("gemini-3.8-flash") == "gemini-3.8-flash"
    assert validate_model_identifier("future-sol-x1") == "future-sol-x1"
    assert validate_model_identifier("provider/model:tag.v1") == "provider/model:tag.v1"

    # Invalid identifiers fail
    with pytest.raises(ExecutionProfileValidationError):
        validate_model_identifier("")
    with pytest.raises(ExecutionProfileValidationError):
        validate_model_identifier("-invalid-start")
    with pytest.raises(ExecutionProfileValidationError):
        validate_model_identifier("invalid with spaces")
    with pytest.raises(ExecutionProfileValidationError):
        validate_model_identifier("a" * 129)


def test_resolve_default_execution_profile() -> None:
    policy = load_execution_profile_policy()
    codex_prof = resolve_execution_profile(policy, run_id="RUN-100", executor="codex")
    assert codex_prof.run_id == "RUN-100"
    assert codex_prof.executor == "codex"
    assert codex_prof.model == "gpt-5.6-sol"
    assert codex_prof.reasoning_effort == "high"
    assert codex_prof.model_source == "REPOSITORY_DEFAULT"
    assert codex_prof.effort_source == "REPOSITORY_DEFAULT"

    anti_prof = resolve_execution_profile(policy, run_id="RUN-101", executor="antigravity")
    assert anti_prof.run_id == "RUN-101"
    assert anti_prof.executor == "antigravity"
    assert anti_prof.model == "gemini-3.8-flash"
    assert anti_prof.reasoning_effort == "high"
    assert anti_prof.model_source == "REPOSITORY_DEFAULT"
    assert anti_prof.effort_source == "REPOSITORY_DEFAULT"


def test_resolve_synthetic_future_model_without_global_allowlist() -> None:
    policy = load_execution_profile_policy()
    future_prof = resolve_execution_profile(
        policy,
        run_id="RUN-102",
        executor="codex",
        model="gpt-7-sol",
        reasoning_effort="medium",
    )
    assert future_prof.run_id == "RUN-102"
    assert future_prof.executor == "codex"
    assert future_prof.model == "gpt-7-sol"
    assert future_prof.reasoning_effort == "medium"
    assert future_prof.model_source == "EXPLICIT"
    assert future_prof.effort_source == "EXPLICIT"


def test_resolve_invalid_effort_fails_closed_without_fallback() -> None:
    policy = load_execution_profile_policy()
    with pytest.raises(ExecutionProfileValidationError, match="unsupported reasoning effort"):
        resolve_execution_profile(
            policy,
            run_id="RUN-103",
            executor="codex",
            reasoning_effort="ultra",
        )

    with pytest.raises(ExecutionProfileValidationError, match="unsupported reasoning effort"):
        resolve_execution_profile(
            policy,
            run_id="RUN-104",
            executor="antigravity",
            reasoning_effort="maximum",
        )


def test_resolve_non_managed_executor_fails() -> None:
    policy = load_execution_profile_policy()
    with pytest.raises(ExecutionProfileError, match="not profile-managed"):
        resolve_execution_profile(policy, run_id="RUN-105", executor="antigravity-minimax")


def test_profile_persistence_and_parse_roundtrip(tmp_path: Path) -> None:
    profile = ResolvedExecutionProfile(
        run_id="RUN-200",
        executor="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        model_source="REPOSITORY_DEFAULT",
        effort_source="REPOSITORY_DEFAULT",
    )
    sidecar_path = tmp_path / "RUN-200.json"
    persist_execution_profile(sidecar_path, profile)

    parsed = parse_execution_profile(sidecar_path.read_text(encoding="utf-8"))
    assert parsed == profile
    assert parsed.format == EXECUTION_PROFILE_FORMAT
    assert parsed.version == EXECUTION_PROFILE_VERSION


def test_profile_persistence_idempotent(tmp_path: Path) -> None:
    profile = ResolvedExecutionProfile(
        run_id="RUN-201",
        executor="antigravity",
        model="gemini-3.8-flash",
        reasoning_effort="high",
        model_source="REPOSITORY_DEFAULT",
        effort_source="REPOSITORY_DEFAULT",
    )
    sidecar_path = tmp_path / "RUN-201.json"
    persist_execution_profile(sidecar_path, profile)
    # Identical write succeeds
    persist_execution_profile(sidecar_path, profile)
    assert parse_execution_profile(sidecar_path.read_text(encoding="utf-8")) == profile


def test_profile_persistence_conflict_fails(tmp_path: Path) -> None:
    profile1 = ResolvedExecutionProfile(
        run_id="RUN-202",
        executor="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        model_source="REPOSITORY_DEFAULT",
        effort_source="REPOSITORY_DEFAULT",
    )
    sidecar_path = tmp_path / "RUN-202.json"
    persist_execution_profile(sidecar_path, profile1)

    profile2 = ResolvedExecutionProfile(
        run_id="RUN-202",
        executor="codex",
        model="gpt-6-sol",
        reasoning_effort="medium",
        model_source="EXPLICIT",
        effort_source="EXPLICIT",
    )
    with pytest.raises(ExecutionProfileConflictError, match="conflicts with existing"):
        persist_execution_profile(sidecar_path, profile2)


def test_policy_rejection_of_malformed_yaml(tmp_path: Path) -> None:
    bad_policy_path = tmp_path / ".ai" / "executor-profiles.yaml"
    bad_policy_path.parent.mkdir(parents=True)
    bad_policy_path.write_text("invalid: [yaml: broken", encoding="utf-8")

    with pytest.raises(ExecutionProfilePolicyError):
        load_execution_profile_policy(tmp_path)


def test_policy_rejection_of_missing_executor(tmp_path: Path) -> None:
    policy_path = tmp_path / ".ai" / "executor-profiles.yaml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps({
            "format": "AIOS_EXECUTOR_PROFILES_POLICY",
            "version": 1,
            "defaults": {"codex": {"model": "gpt-5.6-sol", "reasoning_effort": "high"}},
            "supported_efforts": {"codex": ["low", "medium", "high"]},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ExecutionProfilePolicyError, match="missing required executor 'antigravity'"):
        load_execution_profile_policy(tmp_path)
