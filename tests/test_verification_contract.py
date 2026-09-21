"""Focused regression tests for deterministic verification-contract policy."""

import pytest

from aios_renew.verification_contract import (
    VerificationContractError,
    normalize_verification,
    parse_bp_v4_probe_workers,
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


@pytest.mark.parametrize(
    ("command", "workers"),
    [
        ("python scripts/bp_v4_parallel_probe.py --workers 2", (2,)),
        ("python scripts/bp_v4_parallel_probe.py --workers 2 3", (2, 3)),
        ("python scripts/bp_v4_parallel_probe.py --workers 2 3 4", (2, 3, 4)),
        ("python scripts/bp_v4_parallel_probe.py --workers 3 4", (3, 4)),
    ],
)
def test_bp_v4_probe_exact_grammar_is_recognized(command, workers) -> None:
    assert parse_bp_v4_probe_workers(command) == workers
    coverage = parse_pytest_coverage(command)
    assert coverage is not None
    assert coverage.launcher == "python -m pytest"
    assert coverage.is_full_suite
    assert coverage.measurement
    validate_v1_verification(
        (command,),
        full_suite_reason="One bounded same-subject BP-V4 measurement experiment.",
        path="verification.required",
    )


@pytest.mark.parametrize(
    "command",
    [
        "python scripts/bp_v4_parallel_probe.py",
        "python scripts/bp_v4_parallel_probe.py --workers",
        "python scripts/bp_v4_parallel_probe.py --workers auto",
        "python scripts/bp_v4_parallel_probe.py --workers 1",
        "python scripts/bp_v4_parallel_probe.py --workers 5",
        "python scripts/bp_v4_parallel_probe.py --workers 2 2",
        "python scripts/bp_v4_parallel_probe.py --workers 3 2",
        "python scripts/bp_v4_parallel_probe.py --workers 2 --extra",
        "python scripts/bp_v4_parallel_probe.py --workers 2 && echo injected",
        "python scripts/bp_v4_parallel_probe.py --workers '2",
        "py scripts/bp_v4_parallel_probe.py --workers 2",
        "python .\\scripts\\bp_v4_parallel_probe.py --workers 2",
        "python scripts/BP_V4_PARALLEL_PROBE.py --workers 2",
    ],
)
def test_malformed_probe_family_fails_closed(command) -> None:
    assert parse_bp_v4_probe_workers(command) is None
    with pytest.raises(VerificationContractError, match="malformed BP-V4 probe"):
        validate_v1_verification(
            (command,), full_suite_reason=None, path="verification.required"
        )


def test_probe_subsumes_module_full_suite_but_not_direct_launcher() -> None:
    probe_command = "python scripts/bp_v4_parallel_probe.py --workers 2 4"
    module_full_suite = "python -m pytest -q"
    direct_full_suite = "pytest -q"

    with pytest.raises(VerificationContractError, match="subsumed"):
        validate_v1_verification(
            (module_full_suite, probe_command),
            full_suite_reason="The bounded probe contains the module full-suite proof.",
            path="verification.required",
        )
    assert normalize_verification((module_full_suite, probe_command)) == (
        probe_command,
    )
    assert normalize_verification((probe_command, module_full_suite)) == (
        probe_command,
    )
    assert normalize_verification((direct_full_suite, probe_command)) == (
        direct_full_suite,
        probe_command,
    )


def test_multiple_probe_commands_cannot_authorize_duplicate_measurement() -> None:
    commands = (
        "python scripts/bp_v4_parallel_probe.py --workers 2",
        "python scripts/bp_v4_parallel_probe.py --workers 2 3",
    )
    with pytest.raises(VerificationContractError, match="coverage-equivalent"):
        validate_v1_verification(
            commands,
            full_suite_reason="Only one measurement is permitted.",
            path="verification.required",
        )
    assert normalize_verification(commands) == (commands[0],)


def test_unrelated_opaque_behavior_remains_compatible() -> None:
    command = "python scripts/unrelated_probe.py --workers auto && echo opaque"
    assert parse_pytest_coverage(command) is None
    validate_v1_verification(
        (command,), full_suite_reason=None, path="verification.required"
    )
