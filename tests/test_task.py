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
