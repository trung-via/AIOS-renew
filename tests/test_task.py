from pathlib import Path

import pytest

from aios_renew import TaskValidationError, parse_task


VALID_TASK = """
task_id: TASK-002
revision: 1
goal: Parse and validate the canonical TASK contract.
problem: Invalid task documents must not enter execution.
assumptions:
  - TASK input is YAML.
scope:
  inspect:
    - src/aios_renew/**
  modify:
    - src/aios_renew/task.py
non_goals:
  - Executor integration.
constraints:
  hard:
    - Keep the kernel minimal.
acceptance:
  - id: AC1
    condition: A valid TASK document parses successfully.
  - id: AC2
    condition: An invalid TASK document is rejected.
verification:
  required:
    - pytest tests/test_task.py
"""


def test_parse_valid_task() -> None:
    task = parse_task(VALID_TASK)

    assert task.task_id == "TASK-002"
    assert task.revision == 1
    assert task.scope.inspect == ("src/aios_renew/**",)
    assert task.scope.modify == ("src/aios_renew/task.py",)
    assert [criterion.id for criterion in task.acceptance] == ["AC1", "AC2"]


def test_return_affinity_historical_missing_is_explicitly_legacy_only():
    from aios_renew.return_affinity import LEGACY, document_affinity
    assert parse_task(VALID_TASK).return_affinity == LEGACY
    assert document_affinity({}) == LEGACY
    assert parse_task(VALID_TASK + "return_affinity: {kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}\n").return_affinity == LEGACY


def test_origin_affinity_round_trips_only_opaque_selector():
    from dataclasses import asdict
    import yaml
    from aios_renew.return_affinity import OriginAffinity
    affinity = OriginAffinity("page-origin-v1:" + "a" * 64, 7)
    task = parse_task(VALID_TASK + yaml.safe_dump({"return_affinity": asdict(affinity)}))
    assert task.return_affinity == affinity
    assert set(asdict(task)["return_affinity"]) == {"kind", "route_handle", "generation"}


@pytest.mark.parametrize("selector", [
    None, {}, {"kind": "UNKNOWN"},
    {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE", "generation": 1},
    *[{"kind": "ORIGIN_AFFINE", "route_handle": "page-origin-v1:" + "a" * 64, "generation": value}
      for value in (True, 0, -1, "1", 1.0, 2147483648)],
    {"kind": "ORIGIN_AFFINE", "route_handle": "page-origin-v1:" + "A" * 64, "generation": 1},
    {"kind": "ORIGIN_AFFINE", "route_handle": "page-origin-v1:" + "a" * 64, "generation": 1, "chat_url": "private"},
])
def test_return_affinity_rejects_invalid_or_private_fields(selector):
    import yaml
    with pytest.raises(TaskValidationError, match="return_affinity"):
        parse_task(VALID_TASK + yaml.safe_dump({"return_affinity": selector}))


def test_duplicate_affinity_cannot_overwrite_exact_selector():
    with pytest.raises(TaskValidationError, match="duplicate"):
        parse_task(VALID_TASK + "return_affinity: {kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}\n"
                   "return_affinity: {kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}\n")


def test_rejects_missing_required_field() -> None:
    with pytest.raises(TaskValidationError, match=r"TASK\.task_id is required"):
        parse_task(VALID_TASK.replace("task_id: TASK-002\n", ""))


def test_rejects_duplicate_acceptance_ids() -> None:
    duplicate = VALID_TASK.replace("id: AC2", "id: AC1")

    with pytest.raises(TaskValidationError, match="duplicate id: AC1"):
        parse_task(duplicate)


@pytest.mark.parametrize(
    ("source", "path"),
    [
        (VALID_TASK + "unexpected: value\n", "TASK"),
        (
            VALID_TASK.replace(
                "  inspect:\n", "  unexpected: value\n  inspect:\n"
            ),
            "scope",
        ),
        (
            VALID_TASK.replace("  hard:\n", "  unexpected: value\n  hard:\n"),
            "constraints",
        ),
        (
            VALID_TASK.replace("  required:\n", "  unexpected: value\n  required:\n"),
            "verification",
        ),
        (
            VALID_TASK.replace(
                "  - id: AC1\n", "  - id: AC1\n    unexpected: value\n"
            ),
            r"acceptance\[0\]",
        ),
    ],
)
def test_rejects_unknown_mapping_fields(source: str, path: str) -> None:
    with pytest.raises(
        TaskValidationError, match=rf"{path} contains unknown field"
    ):
        parse_task(source)


def test_rejects_empty_required_verification() -> None:
    source = VALID_TASK.replace(
        "  required:\n    - pytest tests/test_task.py\n", "  required: []\n"
    )

    with pytest.raises(
        TaskValidationError, match="must contain at least one command"
    ):
        parse_task(source)


def test_rejects_duplicate_required_verification_deterministically() -> None:
    source = VALID_TASK.replace(
        "    - pytest tests/test_task.py\n",
        "    - pytest tests/test_task.py\n    - pytest tests/test_task.py\n",
    )

    with pytest.raises(
        TaskValidationError,
        match=r"duplicate command: pytest tests/test_task\.py",
    ):
        parse_task(source)


def test_parses_minimum_sufficient_v1_verification() -> None:
    source = VALID_TASK.replace(
        "verification:\n",
        "verification:\n  policy: minimum-sufficient-v1\n",
    )

    task = parse_task(source)

    assert task.verification.policy == "minimum-sufficient-v1"
    assert task.verification.full_suite_reason is None


def test_v1_task_rejects_known_redundant_verification() -> None:
    source = VALID_TASK.replace(
        "verification:\n  required:\n    - pytest tests/test_task.py\n",
        "verification:\n"
        "  policy: minimum-sufficient-v1\n"
        "  required:\n"
        "    - pytest tests\n"
        "    - pytest tests/test_task.py\n",
    )

    with pytest.raises(TaskValidationError, match="provably subsumed"):
        parse_task(source)


@pytest.mark.parametrize(
    "invalid_path",
    [
        "/src/aios_renew/task.py",
        "C:/src/aios_renew/task.py",
        "../src/aios_renew/task.py",
        "src/../tests/test_task.py",
        r"src\aios_renew\task.py",
        "src/aios_renew/*.py",
        "src/aios_renew/task?.py",
        "src/aios_renew/[t]ask.py",
    ],
)
def test_rejects_unsafe_or_non_exact_modify_paths(invalid_path: str) -> None:
    source = VALID_TASK.replace("src/aios_renew/task.py", invalid_path)

    with pytest.raises(TaskValidationError, match=r"scope\.modify\[0\]"):
        parse_task(source)


def test_accepts_safe_exact_modify_paths_without_glob_expansion() -> None:
    source = VALID_TASK.replace(
        "    - src/aios_renew/task.py\n",
        "    - src/aios_renew/task.py\n    - docs/authoring-contract.md\n",
    )

    task = parse_task(source)

    assert task.scope.modify == (
        "src/aios_renew/task.py",
        "docs/authoring-contract.md",
    )


def test_canonical_task_corpus_remains_compatible() -> None:
    task_directory = Path(__file__).parents[1] / ".ai" / "tasks"

    for number in range(13, 30):
        source = (task_directory / f"TASK-{number:03}.yaml").read_text(
            encoding="utf-8"
        )
        task = parse_task(source)

        assert task.task_id == f"TASK-{number:03}"
