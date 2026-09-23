"""Tests for execution profile policy, resolution, validation, and sidecar persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aios_renew.execution_profile import (
    ALLOWED_SOURCE_ATTRIBUTIONS,
    CANONICAL_SOURCE_ATTRIBUTIONS,
    EXECUTION_PROFILE_ALLOWED_FIELDS,
    EXECUTION_PROFILE_FORMAT,
    EXECUTION_PROFILE_VERSION,
    POLICY_ALLOWED_FIELDS,
    POLICY_FORMAT,
    POLICY_VERSION,
    ExecutionProfileConflictError,
    ExecutionProfileError,
    ExecutionProfilePolicy,
    ExecutionProfileValidationError,
    ResolvedExecutionProfile,
    default_execution_profile,
    bind_execution_profile,
    execution_profile_path,
    is_profile_managed_executor,
    load_execution_profile_policy,
    parse_execution_profile,
    parse_execution_profile_policy,
    persist_execution_profile,
    resolve_execution_profile,
    validate_execution_profile,
    validate_model_identifier,
)

VALID_POLICY_YAML = """
format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1

executors:
  codex:
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts:
      - low
      - medium
      - high
  antigravity:
    default_model: gemini-3.8-flash
    default_reasoning_effort: high
    supported_reasoning_efforts:
      - low
      - medium
      - high
"""


def test_default_policy_loads_exact_task_163_defaults() -> None:
    policy = load_execution_profile_policy()
    assert policy.format == "AIOS_EXECUTOR_PROFILES_POLICY"
    assert policy.version == 1
    assert policy.executors["codex"].default_model == "gpt-6-sol"
    assert policy.executors["codex"].default_reasoning_effort == "medium"
    assert policy.executors["antigravity"].default_model == "gemini-3.8-flash"
    assert policy.executors["antigravity"].default_reasoning_effort == "medium"
    assert set(policy.executors["codex"].supported_reasoning_efforts) == {"low", "medium", "high"}
    assert set(policy.executors["antigravity"].supported_reasoning_efforts) == {"low", "medium", "high"}


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
    assert codex_prof.model == "gpt-6-sol"
    assert codex_prof.reasoning_effort == "medium"
    assert codex_prof.model_source == "REPOSITORY_DEFAULT"
    assert codex_prof.effort_source == "REPOSITORY_DEFAULT"

    anti_prof = resolve_execution_profile(policy, run_id="RUN-101", executor="antigravity")
    assert anti_prof.run_id == "RUN-101"
    assert anti_prof.executor == "antigravity"
    assert anti_prof.model == "gemini-3.8-flash"
    assert anti_prof.reasoning_effort == "medium"
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


def test_partial_selection_and_explicit_default_preserve_attribution() -> None:
    policy = load_execution_profile_policy()
    partial = bind_execution_profile(
        run_id="RUN-103",
        executor="codex",
        reasoning_effort="high",
        policy=policy,
    )
    assert partial.model == "gpt-6-sol"
    assert partial.model_source == "REPOSITORY_DEFAULT"
    assert partial.reasoning_effort == "high"
    assert partial.effort_source == "EXPLICIT"

    explicit_defaults = bind_execution_profile(
        run_id="RUN-104",
        executor="codex",
        model="gpt-6-sol",
        reasoning_effort="medium",
        policy=policy,
    )
    assert explicit_defaults.model_source == "EXPLICIT"
    assert explicit_defaults.effort_source == "EXPLICIT"


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

    with pytest.raises(ExecutionProfileValidationError):
        load_execution_profile_policy(tmp_path)


def test_policy_rejection_of_missing_executor(tmp_path: Path) -> None:
    policy_path = tmp_path / ".ai" / "executor-profiles.yaml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps({
            "format": "AIOS_EXECUTOR_PROFILES_POLICY",
            "version": 1,
            "executors": {
                "codex": {
                    "default_model": "gpt-5.6-sol",
                    "default_reasoning_effort": "high",
                    "supported_reasoning_efforts": ["low", "medium", "high"],
                }
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(ExecutionProfileValidationError, match="missing required executor 'antigravity'"):
        load_execution_profile_policy(tmp_path)


def test_parse_execution_profile_strict_validation() -> None:
    base = {
        "format": "AIOS_EXECUTION_PROFILE",
        "version": 1,
        "run_id": "RUN-001",
        "executor": "codex",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "model_source": "REPOSITORY_DEFAULT",
        "effort_source": "REPOSITORY_DEFAULT",
    }
    parsed = parse_execution_profile(base)
    assert parsed.run_id == "RUN-001"
    assert parsed.model == "gpt-5.6-sol"
    assert parsed.reasoning_effort == "high"
    assert parsed.model_source == "REPOSITORY_DEFAULT"
    assert parsed.effort_source == "REPOSITORY_DEFAULT"

    with pytest.raises(ExecutionProfileValidationError, match="format"):
        parse_execution_profile(dict(base, format="WRONG_FORMAT"))

    with pytest.raises(ExecutionProfileValidationError, match="version"):
        parse_execution_profile(dict(base, version=2))

    with pytest.raises(ExecutionProfileValidationError, match="unsupported executor"):
        parse_execution_profile(dict(base, executor="unknown"))

    with pytest.raises(ExecutionProfileValidationError, match="invalid model identifier"):
        parse_execution_profile(dict(base, model="invalid model!"))


def test_policy_rejection_of_unknown_top_level_fields() -> None:
    bad_yaml = VALID_POLICY_YAML + "\nextra_top_level_field: unexpected\n"
    with pytest.raises(ExecutionProfileValidationError, match="unknown field"):
        parse_execution_profile_policy(bad_yaml)


def test_policy_rejection_of_unknown_per_executor_fields() -> None:
    bad_yaml = """
format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1

executors:
  codex:
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts:
      - low
      - medium
      - high
    extra_field_in_spec: invalid
  antigravity:
    default_model: gemini-3.8-flash
    default_reasoning_effort: high
    supported_reasoning_efforts:
      - low
      - medium
      - high
"""
    with pytest.raises(ExecutionProfileValidationError, match="unknown field"):
        parse_execution_profile_policy(bad_yaml)


def test_policy_rejection_of_unknown_executor() -> None:
    bad_yaml = """
format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1

executors:
  codex:
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts:
      - low
      - medium
      - high
  antigravity:
    default_model: gemini-3.8-flash
    default_reasoning_effort: high
    supported_reasoning_efforts:
      - low
      - medium
      - high
  rogue_executor:
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts:
      - low
"""
    with pytest.raises(ExecutionProfileValidationError, match="unknown executor"):
        parse_execution_profile_policy(bad_yaml)


def test_policy_rejection_of_duplicate_yaml_keys_top_level() -> None:
    duplicate_top = """
format: AIOS_EXECUTOR_PROFILES_POLICY
format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1
executors:
  codex:
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
  antigravity:
    default_model: gemini-3.8-flash
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
"""
    with pytest.raises(ExecutionProfileValidationError, match="duplicate key"):
        parse_execution_profile_policy(duplicate_top)


def test_policy_rejection_of_duplicate_yaml_keys_in_spec() -> None:
    duplicate_spec = """
format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1
executors:
  codex:
    default_model: gpt-5.6-sol
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
  antigravity:
    default_model: gemini-3.8-flash
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
"""
    with pytest.raises(ExecutionProfileValidationError, match="duplicate key"):
        parse_execution_profile_policy(duplicate_spec)


def test_policy_rejection_of_duplicate_yaml_keys_in_executors() -> None:
    duplicate_exec = """
format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1
executors:
  codex:
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
  codex:
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
  antigravity:
    default_model: gemini-3.8-flash
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
"""
    with pytest.raises(ExecutionProfileValidationError, match="duplicate key"):
        parse_execution_profile_policy(duplicate_exec)


def test_load_policy_rejection_of_duplicate_yaml_keys_from_file(tmp_path: Path) -> None:
    policy_path = tmp_path / ".ai" / "executor-profiles.yaml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        """
format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1
version: 1
executors:
  codex:
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
  antigravity:
    default_model: gemini-3.8-flash
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
""",
        encoding="utf-8",
    )
    with pytest.raises(ExecutionProfileValidationError, match="duplicate key"):
        load_execution_profile_policy(tmp_path)


def test_policy_rejection_of_duplicate_supported_reasoning_efforts() -> None:
    dup_efforts = """
format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1
executors:
  codex:
    default_model: gpt-5.6-sol
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, high, high]
  antigravity:
    default_model: gemini-3.8-flash
    default_reasoning_effort: high
    supported_reasoning_efforts: [low, medium, high]
"""
    with pytest.raises(ExecutionProfileValidationError, match="duplicate effort"):
        parse_execution_profile_policy(dup_efforts)


def test_profile_rejection_of_unknown_fields() -> None:
    base = {
        "format": "AIOS_EXECUTION_PROFILE",
        "version": 1,
        "run_id": "RUN-001",
        "executor": "codex",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "model_source": "REPOSITORY_DEFAULT",
        "effort_source": "REPOSITORY_DEFAULT",
        "unexpected_extra_field": "disallowed",
    }
    with pytest.raises(ExecutionProfileValidationError, match="unknown field"):
        parse_execution_profile(base)


def test_profile_rejection_of_duplicate_json_keys() -> None:
    raw_json = """{
  "format": "AIOS_EXECUTION_PROFILE",
  "version": 1,
  "run_id": "RUN-001",
  "executor": "codex",
  "model": "gpt-5.6-sol",
  "reasoning_effort": "high",
  "model_source": "REPOSITORY_DEFAULT",
  "model_source": "EXPLICIT",
  "effort_source": "REPOSITORY_DEFAULT"
}"""
    with pytest.raises(ExecutionProfileValidationError, match="duplicate key"):
        parse_execution_profile(raw_json)


def test_profile_source_attribution_canonical_allowed() -> None:
    base = {
        "format": "AIOS_EXECUTION_PROFILE",
        "version": 1,
        "run_id": "RUN-001",
        "executor": "codex",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "model_source": "REPOSITORY_DEFAULT",
        "effort_source": "REPOSITORY_DEFAULT",
    }
    p1 = parse_execution_profile(dict(base, model_source="EXPLICIT", effort_source="EXPLICIT"))
    assert p1.model_source == "EXPLICIT"
    assert p1.effort_source == "EXPLICIT"

    p2 = parse_execution_profile(dict(base, model_source="REPOSITORY_DEFAULT", effort_source="EXPLICIT"))
    assert p2.model_source == "REPOSITORY_DEFAULT"
    assert p2.effort_source == "EXPLICIT"

    assert ALLOWED_SOURCE_ATTRIBUTIONS == frozenset({"REPOSITORY_DEFAULT", "EXPLICIT"})
    assert CANONICAL_SOURCE_ATTRIBUTIONS == ALLOWED_SOURCE_ATTRIBUTIONS


def test_profile_source_attribution_invalid_labels_rejected() -> None:
    base = {
        "format": "AIOS_EXECUTION_PROFILE",
        "version": 1,
        "run_id": "RUN-001",
        "executor": "codex",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "model_source": "REPOSITORY_DEFAULT",
        "effort_source": "REPOSITORY_DEFAULT",
    }
    for invalid in ["CUSTOM", "USER", "INLINE", "default", "", None, 123]:
        with pytest.raises(ExecutionProfileValidationError, match="model_source"):
            parse_execution_profile(dict(base, model_source=invalid))

        with pytest.raises(ExecutionProfileValidationError, match="effort_source"):
            parse_execution_profile(dict(base, effort_source=invalid))


def test_resolved_execution_profile_direct_validation() -> None:
    # Direct construction with invalid model_source fails
    with pytest.raises(ExecutionProfileValidationError, match="model_source"):
        ResolvedExecutionProfile(
            run_id="RUN-001",
            executor="codex",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            model_source="INVALID_SOURCE",
        )

    # Direct construction with invalid effort_source fails
    with pytest.raises(ExecutionProfileValidationError, match="effort_source"):
        ResolvedExecutionProfile(
            run_id="RUN-001",
            executor="codex",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            effort_source="INVALID_SOURCE",
        )

    # Direct construction with invalid executor fails
    with pytest.raises(ExecutionProfileValidationError, match="unsupported executor"):
        ResolvedExecutionProfile(
            run_id="RUN-001",
            executor="unsupported_executor",
            model="gpt-5.6-sol",
            reasoning_effort="high",
        )


def test_validate_execution_profile_valid() -> None:
    policy = load_execution_profile_policy()
    profile = ResolvedExecutionProfile(
        run_id="RUN-106",
        executor="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        model_source="REPOSITORY_DEFAULT",
        effort_source="REPOSITORY_DEFAULT",
    )
    validated = validate_execution_profile(profile, policy)
    assert validated == profile
    assert validated.reasoning_effort == "high"


def test_validate_execution_profile_unsupported_effort_fails() -> None:
    policy = load_execution_profile_policy()
    profile = ResolvedExecutionProfile(
        run_id="RUN-107",
        executor="codex",
        model="gpt-5.6-sol",
        reasoning_effort="ultra",
        model_source="REPOSITORY_DEFAULT",
        effort_source="EXPLICIT",
    )
    with pytest.raises(
        ExecutionProfileValidationError,
        match="unsupported reasoning effort 'ultra' for executor 'codex'",
    ):
        validate_execution_profile(profile, policy)


def test_validate_execution_profile_preserves_bound_profile_despite_policy_default_changes() -> None:
    custom_policy_yaml = """
format: AIOS_EXECUTOR_PROFILES_POLICY
version: 1

executors:
  codex:
    default_model: gpt-6-sol
    default_reasoning_effort: medium
    supported_reasoning_efforts:
      - low
      - medium
      - high
  antigravity:
    default_model: gemini-3.8-flash
    default_reasoning_effort: medium
    supported_reasoning_efforts:
      - low
      - medium
      - high
"""
    custom_policy = parse_execution_profile_policy(custom_policy_yaml)
    bound_profile = ResolvedExecutionProfile(
        run_id="RUN-108",
        executor="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        model_source="REPOSITORY_DEFAULT",
        effort_source="REPOSITORY_DEFAULT",
    )
    validated = validate_execution_profile(bound_profile, custom_policy)
    assert validated == bound_profile
    assert validated.model == "gpt-5.6-sol"
    assert validated.reasoning_effort == "high"

