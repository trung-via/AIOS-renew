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


@pytest.mark.parametrize("policy", (None, "minimum-sufficient-v1", "minimum-sufficient-v2"))
def test_rejects_empty_required_verification(policy) -> None:
    source = VALID_TASK.replace(
        "  required:\n    - pytest tests/test_task.py\n", "  required: []\n"
    )
    if policy is not None:
        source = source.replace("verification:\n", f"verification:\n  policy: {policy}\n")

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


def test_v2_task_is_one_policy_and_preserves_v1_readability() -> None:
    for policy in ("minimum-sufficient-v1", "minimum-sufficient-v2"):
        task = parse_task(VALID_TASK.replace("verification:\n", f"verification:\n  policy: {policy}\n"))
        assert task.verification.policy == policy
    for independent in ("attributed-minimum-verification", "deterministic-delta-minimum-verification"):
        with pytest.raises(TaskValidationError, match="verification.policy"):
            parse_task(VALID_TASK.replace("verification:\n", f"verification:\n  policy: {independent}\n"))


@pytest.mark.parametrize("policy", (None, "minimum-sufficient-v1", "minimum-sufficient-v2"))
def test_task_decoding_keeps_historical_policy_and_exact_authored_order(policy):
    import yaml
    body = yaml.safe_load(VALID_TASK)
    commands = ["opaque  second --literal='two  spaces'", "python -m pytest -q tests/test_task.py"]
    body["verification"]["required"] = commands
    if policy is not None:
        body["verification"]["policy"] = policy
    task = parse_task(yaml.safe_dump(body))
    assert task.verification.policy == policy
    assert task.verification.required == tuple(commands)
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


def test_future_intake_is_never_an_implicit_task_or_policy_route(monkeypatch):
    import aios_renew.task as task_module
    import yaml
    from aios_renew.verification_contract import CURRENT_VERIFICATION_POLICY, VERIFICATION_POLICIES

    def prohibited(*args, **kwargs):
        raise AssertionError("ordinary TASK parsing must not invoke semantic proof intake")

    monkeypatch.setattr(task_module, "validate_semantic_proof_declaration", prohibited)
    monkeypatch.setattr(task_module, "validate_semantic_proof_intake", prohibited)
    assert CURRENT_VERIFICATION_POLICY == "minimum-sufficient-v1"
    assert VERIFICATION_POLICIES == {"minimum-sufficient-v1", "minimum-sufficient-v2"}
    for policy in (None, *sorted(VERIFICATION_POLICIES)):
        body = yaml.safe_load(VALID_TASK)
        if policy is not None:
            body["verification"]["policy"] = policy
        task = task_module.validate_task(body)
        assert task == parse_task(yaml.safe_dump(body))
        assert task.verification.required == ("pytest tests/test_task.py",)
        assert task.verification.policy == policy
        for field in ("schema", "proof_contract", "semantic_proof_intake"):
            with pytest.raises(TaskValidationError, match="unknown field"):
                task_module.validate_task({**body, field: "task-semantic-proof-intake-v1"})
        body["verification"]["policy"] = "task-semantic-proof-intake-v1"
        with pytest.raises(TaskValidationError, match="verification.policy"):
            task_module.validate_task(body)


@pytest.mark.parametrize("policy", (None, "minimum-sufficient-v1", "minimum-sufficient-v2"))
def test_semantic_intake_does_not_reinterpret_historical_opaque_commands(policy):
    import yaml
    body = yaml.safe_load(VALID_TASK)
    if policy is not None:
        body["verification"]["policy"] = policy
    # Historical parsing intentionally retains opaque authored strings. The new
    # explicit API must neither reinterpret these nor silently change rejection.
    body["verification"]["required"] = ["proof-unit", "opaque --literal='two  spaces'"]
    task = parse_task(yaml.safe_dump(body))
    assert task.verification.required == tuple(body["verification"]["required"])
    assert task.verification.policy == policy
    body["verification"]["required"].append("proof-unit")
    with pytest.raises(TaskValidationError, match="duplicate command"):
        parse_task(yaml.safe_dump(body))


def test_future_declaration_cannot_parse_as_current_task():
    with pytest.raises(TaskValidationError, match="unknown field"):
        parse_task("schema: task-semantic-proof-intake-v1\ncontract: {}\nmapping: {}\n")


def test_duplicate_future_fields_are_not_silently_accepted_by_current_task_loader():
    with pytest.raises(TaskValidationError, match="duplicate TASK mapping field"):
        parse_task("schema: task-semantic-proof-intake-v1\nschema: task-semantic-proof-intake-v1\n")
