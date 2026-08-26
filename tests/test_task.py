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
    assert task.scope.modify == ("src/aios_renew/task.py",)
    assert [criterion.id for criterion in task.acceptance] == ["AC1", "AC2"]


def test_rejects_missing_required_field() -> None:
    with pytest.raises(TaskValidationError, match=r"TASK\.task_id is required"):
        parse_task(VALID_TASK.replace("task_id: TASK-002\n", ""))


def test_rejects_duplicate_acceptance_ids() -> None:
    duplicate = VALID_TASK.replace("id: AC2", "id: AC1")

    with pytest.raises(TaskValidationError, match="duplicate id: AC1"):
        parse_task(duplicate)
