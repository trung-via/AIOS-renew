"""Focused regression tests for deterministic verification-contract policy."""

import pytest

from aios_renew.verification_contract import (
    VerificationContractError,
    normalize_verification,
    parse_pytest_coverage,
    validate_v1_verification,
)


def test_supported_grammar_keeps_launcher_families_separate() -> None:
    direct = parse_pytest_coverage("pytest -q tests/test_task.py -k contract")
    module = parse_pytest_coverage(
        "python -m pytest --quiet tests/test_task.py -k contract"
    )

    assert direct is not None and direct.launcher == "pytest"
    assert module is not None and module.launcher == "python -m pytest"
    validate_v1_verification(
        (
            "pytest -q tests/test_task.py -k contract",
            "python -m pytest --quiet tests/test_task.py -k contract",
        ),
        full_suite_reason=None,
        path="verification.required",
    )


@pytest.mark.parametrize(
    "commands",
    [
        ("pytest tests/test_task.py", "pytest tests/test_task.py"),
        ("pytest -q tests/test_task.py", "pytest tests/test_task.py --quiet"),
        ("pytest tests", "pytest tests/test_task.py"),
        ("pytest tests/test_task.py", "pytest tests/test_task.py -k valid"),
    ],
)
def test_v1_rejects_duplicate_equivalent_and_subsumed_commands(commands) -> None:
    with pytest.raises(VerificationContractError):
        validate_v1_verification(
            commands,
            full_suite_reason=None,
            path="verification.required",
        )


def test_opaque_commands_have_no_inferred_relation() -> None:
    commands = (
        "pytest tests/test_task.py --maxfail=1",
        "pytest tests/test_task.py --maxfail=2",
        "pytest tests/test_task.py && echo done",
    )

    assert all(parse_pytest_coverage(command) is None for command in commands)
    validate_v1_verification(
        commands, full_suite_reason=None, path="verification.required"
    )


def test_full_suite_reason_is_required_exactly_for_recognized_full_suite() -> None:
    with pytest.raises(VerificationContractError, match="is required"):
        validate_v1_verification(
            ("pytest -q",), full_suite_reason=None, path="verification.required"
        )
    with pytest.raises(VerificationContractError, match="allowed only"):
        validate_v1_verification(
            ("pytest tests/test_task.py",),
            full_suite_reason="Not actually a full suite.",
            path="verification.required",
        )
    with pytest.raises(VerificationContractError, match="at most 512"):
        validate_v1_verification(
            ("pytest",),
            full_suite_reason="x" * 513,
            path="verification.required",
        )


def test_normalization_keeps_earliest_equivalent_and_broader_command() -> None:
    commands = (
        "pytest -q tests/test_task.py",
        "opaque --one",
        "pytest tests/test_task.py",
        "pytest tests",
        "opaque --one",
        "python -m pytest tests/test_task.py",
    )

    assert normalize_verification(commands) == (
        "opaque --one",
        "pytest tests",
        "python -m pytest tests/test_task.py",
    )
