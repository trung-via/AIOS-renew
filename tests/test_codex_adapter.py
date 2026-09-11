import json
import subprocess
import sys
from dataclasses import asdict

import jsonschema
import pytest

from aios_renew import (
    RESULT_PACKAGE_SCHEMA_PATH,
    CodexAdapter,
    CodexExecutionError,
    CodexOutputError,
    ExecutorBoundary,
    ExecutorBoundaryError,
    Run,
    RunLeaseRegistry,
    parse_remediation,
    parse_review,
    parse_task,
)
from aios_renew.dispatcher import NativeExecutionPolicy
from aios_renew.review import RemediationExecution
from aios_renew.codex_adapter import (
    REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH,
    REPAIR_RESULT_PACKAGE_SCHEMA_PATH,
    extract_token_usage,
)
from aios_renew.run_observation import TokenUsage


TASK_SOURCE = """
task_id: TASK-007
revision: 1
goal: Add a minimal native Codex adapter.
problem: ExecutorBoundary cannot yet invoke Codex CLI.
assumptions: []
scope:
  inspect: []
  modify:
    - src/aios_renew/codex_adapter.py
non_goals:
  - Executor orchestration.
constraints:
  hard:
    - Invoke Codex once without retry.
acceptance:
  - id: AC1
    condition: Codex output normalizes into ResultPackage.
verification:
  required:
    - pytest tests/test_codex_adapter.py
"""


def make_execution():
    task = parse_task(TASK_SOURCE)
    run = Run.from_task(
        run_id="RUN-007-001",
        task=task,
        executor="codex",
        base_sha="abc123",
        workspace="C:/workspace",
    )
    registry = RunLeaseRegistry()
    return task, run, registry, ExecutorBoundary(registry)


def assert_native_executor_context(context: dict, *, operation: str) -> None:
    assert context["role"] == "NATIVE_EXECUTOR"
    assert context["selected_executor"] == "codex"
    assert context["operation"] == operation
    assert context["already_admitted"] is True
    assert context["direct_implementation"] is True
    assert context["operator_dispatch_authority"] is False
    assert context["runtime_verification_authority"] is False


def successful_output(run_id: str) -> str:
    return json.dumps(
        {
            "result": {
                "head_sha": "def456",
                "claims": [
                    {
                        "id": "C1",
                        "satisfies": ["AC1"],
                        "claim": "Codex adapter completed the task.",
                        "evidence": ["E1"],
                    }
                ],
                "changed_files": ["src/aios_renew/codex_adapter.py"],
                "unresolved": [],
            },
            "evidence": [
                {
                    "evidence_id": "E1",
                    "run_id": run_id,
                    "subject_sha": "def456",
                    "type": "TEST",
                    "source": {"command": "pytest tests/test_codex_adapter.py"},
                    "result": {"exit_code": 0, "summary": "tests passed"},
                    "raw": {"path": ".ai/evidence/E1.log"},
                }
            ],
        }
    )


def test_constructs_and_invokes_native_codex_command() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(run.run_id),
            stderr="",
        )

    boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=CodexAdapter(runner=runner),
    )

    assert calls[0][0] == (
        "codex",
        "exec",
        "--cd",
        "C:/workspace",
        "-m",
        "gpt-5.6-sol",
        "-c",
        'model_reasoning_effort="high"',
        "--sandbox",
        "workspace-write",
        "--output-schema",
        str(RESULT_PACKAGE_SCHEMA_PATH),
        "--color",
        "never",
        "--json",
        "-",
    )
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["text"] is False
    assert "encoding" not in calls[0][1]
    assert "errors" not in calls[0][1]
    assert isinstance(calls[0][1]["input"], bytes)
    assert calls[0][1]["check"] is False
    assert calls[0][1]["timeout"] == 65 * 60


def test_codex_adapter_owns_read_only_sandbox_mapping() -> None:
    task, run, _, _ = make_execution()
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(run.run_id),
            stderr="",
        )

    adapter = CodexAdapter(
        runner=runner,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=False)
    )

    adapter.execute(task=task, run=run)

    command = calls[0][0]
    assert command[command.index("--sandbox") + 1] == "read-only"


def test_codex_timeout_is_terminal_to_one_native_invocation() -> None:
    task, run, _, _ = make_execution()
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    adapter = CodexAdapter(runner=runner)
    with pytest.raises(CodexExecutionError, match="60-minute"):
        adapter.execute(task=task, run=run)

    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 65 * 60


def test_codex_timeout_preserves_process_supplied_partial_output() -> None:
    task, run, _, _ = make_execution()

    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            output=b"partial native progress",
            stderr=b"provider deadline",
        )

    with pytest.raises(CodexExecutionError) as captured:
        CodexAdapter(runner=runner).execute(task=task, run=run)

    assert captured.value.stdout == b"partial native progress"
    assert captured.value.stderr == b"provider deadline"


def test_codex_early_native_return_is_accepted_immediately() -> None:
    task, run, _, _ = make_execution()
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(run.run_id),
            stderr="",
        )

    package = CodexAdapter(runner=runner).execute(task=task, run=run)

    assert package.result.head_sha == "def456"
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 65 * 60


def test_utf8_output_outside_cp1252_is_preserved() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    output = json.loads(successful_output(run.run_id))
    output["result"]["claims"][0]["claim"] = "Completed: 漢字 🚀"

    package = boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=CodexAdapter(
            runner=lambda command, **kwargs: subprocess.CompletedProcess(
                command,
                returncode=0,
                stdout=json.dumps(output, ensure_ascii=False),
                stderr="",
            )
        ),
    )

    assert package.result.claims[0].claim == "Completed: 漢字 🚀"


@pytest.mark.parametrize("file_descriptor", [1, 2])
def test_real_subprocess_malformed_utf8_fails_closed(
    file_descriptor: int,
) -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)

    def runner(command, **kwargs):
        return subprocess.run(
            (
                sys.executable,
                "-c",
                f"import os; os.write({file_descriptor}, b'\\xff')",
            ),
            **kwargs,
        )

    with pytest.raises(CodexExecutionError, match="invocation failed"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=CodexAdapter(runner=runner),
        )


def test_primary_prompt_excludes_runtime_verification_commands() -> None:
    task, run, _, _ = make_execution()

    prompt = CodexAdapter.prompt_for(task=task, run=run)

    payload = json.loads(prompt.split("CANONICAL_INPUT:\n", 1)[1])
    assert_native_executor_context(payload["execution_context"], operation="PRIMARY")
    assert "verification" not in payload["task"]
    assert task.verification.required[0] not in prompt
    assert "Runtime is the canonical verification owner" in prompt
    assert "baseline, full, or canonical verification" in prompt
    assert "repository-wide rediscovery" in prompt


def test_primary_prompt_requires_final_implementation_commit_without_push() -> None:
    task, run, _, _ = make_execution()

    prompt = CodexAdapter.prompt_for(task=task, run=run)

    assert "push" in prompt.lower()
    assert "head_sha" in prompt


def test_remediation_prompt_excludes_complete_original_task_contract() -> None:
    task, run, _, _ = make_execution()
    review = parse_review(
        """
review_id: REVIEW-007-001
reviewed_sha: abc123
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {AC1: FAIL}
findings:
  - id: R1
    basis: AC1
    action: CODE_FIX
    location: src/aios_renew/codex_adapter.py
    issue: Narrow issue.
    expected: Narrow fix.
"""
    )
    remediation = parse_remediation(
        """
finding_id: R1
action: CODE_FIX
reviewed_sha: abc123
modification_scope: [src/aios_renew/codex_adapter.py]
affected_verification: [pytest tests/test_codex_adapter.py]
"""
    )
    execution = RemediationExecution(
        review_id=review.review_id,
        finding=review.findings[0],
        remediation=remediation,
        run=run,
    )

    prompt = CodexAdapter.remediation_prompt_for(execution=execution)
    payload = json.loads(prompt.split("REMEDIATION_INPUT:\n", 1)[1])

    assert_native_executor_context(
        payload["execution_context"], operation="REMEDIATION"
    )
    assert payload["finding"]["issue"] == "Narrow issue."
    assert "affected_verification" not in payload["remediation"]
    assert "pytest tests/test_codex_adapter.py" not in prompt
    assert "goal" not in json.dumps(payload)
    assert "acceptance" not in json.dumps(payload)


def test_repair_prompt_marks_direct_already_admitted_executor_role() -> None:
    _, run, _, _ = make_execution()

    prompt = CodexAdapter.repair_prompt_for(
        execution={
            "run": run,
            "repair": {
                "action": "NO_CHANGE",
                "instructions": ["Return the bound result directly."],
                "modification_scope": [],
            },
        }
    )
    payload = json.loads(prompt.split("REPAIR_INPUT:\n", 1)[1])

    assert_native_executor_context(payload["execution_context"], operation="REPAIR")
    assert payload["repair"]["action"] == "NO_CHANGE"
    assert "Runtime derives and persists canonical result.changed_files" in prompt
    assert "do not reconstruct or enumerate that historical file set" in prompt
    assert "only the narrow repair delta or be empty" in prompt
    assert "complete original TASK delta" not in prompt
    assert "CODE_FIX authorizes mutation only to correct an established defect" in prompt
    assert "NO_CHANGE authorizes no repository mutation" in prompt
    assert (
        "NO_CHANGE authorizes no repository mutation and does not permit resuming "
        "unfinished original TASK implementation" in prompt
    )
    assert (
        "CONTINUE_IMPLEMENTATION authorizes mutation to resume the unfinished original "
        "TASK implementation" in prompt
    )


def test_continue_implementation_repair_uses_real_prompt_path_for_bounded_capture_and_commit(
    tmp_path,
) -> None:
    task, _, _, _ = make_execution()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "--quiet", str(repo)), check=True)
    subprocess.run(
        ("git", "-C", str(repo), "config", "user.name", "Codex Repair Test"),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(repo),
            "config",
            "user.email",
            "codex-repair@example.invalid",
        ),
        check=True,
    )
    seed = repo / "seed.txt"
    seed.write_text("failed lineage\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(repo), "add", "seed.txt"), check=True)
    subprocess.run(
        ("git", "-C", str(repo), "commit", "--quiet", "-m", "failed lineage"),
        check=True,
    )
    failed_head = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    run = Run.from_task(
        run_id="RUN-182-003",
        task=task,
        executor="codex",
        base_sha=failed_head,
        workspace=str(repo),
    )
    calls = []

    def runner(command, **kwargs):
        prompt = kwargs["input"].decode("utf-8")
        calls.append((command, prompt))
        execution = json.loads(prompt.split("REPAIR_INPUT:\n", 1)[1])
        assert execution["repair"]["action"] == "CONTINUE_IMPLEMENTATION"
        assert execution["failed_run_id"] == "RUN-182-002"
        assert execution["failed_head_sha"] == failed_head
        assert (
            "necessary bounded repository inspection, discovery, or live capture" in prompt
        )
        assert "unfinished original TASK work is not prohibited" in prompt
        assert "repository-wide rediscovery, new intent" in prompt
        assert "retry this admitted continuation" in prompt
        assert "reroute or fall back to another Executor" in prompt

        capture = repo / "capture.json"
        capture.write_text('{"captured": true}\n', encoding="utf-8")
        subprocess.run(("git", "-C", str(repo), "add", "capture.json"), check=True)
        subprocess.run(
            (
                "git",
                "-C",
                str(repo),
                "commit",
                "--quiet",
                "-m",
                "continue unfinished capture",
            ),
            check=True,
        )
        head_sha = subprocess.run(
            ("git", "-C", str(repo), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        payload = {
            "result": {
                "head_sha": head_sha,
                "claims": [
                    {
                        "id": "C1",
                        "satisfies": ["AC1"],
                        "claim": "The unfinished capture was committed.",
                        "evidence": [],
                    }
                ],
                "changed_files": ["capture.json"],
                "unresolved": [],
            },
            "evidence": [],
        }
        return subprocess.CompletedProcess(
            command, returncode=0, stdout=json.dumps(payload), stderr=""
        )

    result = CodexAdapter(runner=runner).execute_repair(
        execution={
            "run": run,
            "failed_run_id": "RUN-182-002",
            "failed_head_sha": failed_head,
            "root_base_sha": failed_head,
            "repair": {
                "action": "CONTINUE_IMPLEMENTATION",
                "instructions": ["Finish the bounded live capture."],
                "modification_scope": ["capture.json"],
            },
        }
    )

    assert len(calls) == 1
    assert result.result.head_sha != failed_head
    assert subprocess.run(
        ("git", "-C", str(repo), "diff", "--name-only", failed_head, "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "capture.json"


def test_output_schema_is_passed() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(run.run_id),
            stderr="",
        )

    boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=CodexAdapter(runner=runner),
    )

    cmd = calls[0]
    assert "--output-schema" in cmd
    idx = cmd.index("--output-schema")
    assert cmd[idx + 1] == str(RESULT_PACKAGE_SCHEMA_PATH)


def test_schema_represents_canonical_result_and_evidence_shape() -> None:
    assert RESULT_PACKAGE_SCHEMA_PATH.exists()
    schema = json.loads(RESULT_PACKAGE_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema.get("type") == "object"
    assert set(schema.get("required", [])) == {"result", "evidence"}
    assert schema.get("additionalProperties") is False

    result_prop = schema["properties"]["result"]
    assert result_prop["type"] == "object"
    assert set(result_prop["required"]) == {
        "head_sha",
        "claims",
        "changed_files",
        "unresolved",
    }
    claims_prop = result_prop["properties"]["claims"]
    claim_prop = claims_prop["items"]
    assert "minItems" not in claims_prop
    assert claim_prop["properties"]["satisfies"]["minItems"] == 1
    assert "minItems" not in claim_prop["properties"]["evidence"]

    evidence_prop = schema["properties"]["evidence"]
    assert evidence_prop["type"] == "array"
    assert "minItems" not in evidence_prop
    assert set(evidence_prop["items"]["required"]) == {
        "evidence_id",
        "run_id",
        "subject_sha",
        "type",
        "source",
        "result",
        "raw",
    }

    valid_payload = json.loads(successful_output("RUN-007-001"))
    jsonschema.validate(instance=valid_payload, schema=schema)

    invalid_payload = {"evidence": valid_payload["evidence"]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid_payload, schema=schema)

    empty_satisfies = json.loads(successful_output("RUN-007-001"))
    empty_satisfies["result"]["claims"][0]["satisfies"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=empty_satisfies, schema=schema)

    empty_claim_evidence = json.loads(successful_output("RUN-007-001"))
    empty_claim_evidence["result"]["claims"][0]["evidence"] = []
    empty_claim_evidence["evidence"] = []
    jsonschema.validate(instance=empty_claim_evidence, schema=schema)


def test_remediation_schema_requires_runtime_owned_arrays_to_be_empty() -> None:
    assert REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH.exists()
    schema = json.loads(
        REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH.read_text(encoding="utf-8")
    )
    result_properties = schema["properties"]["result"]["properties"]

    assert set(schema["required"]) == {"result", "evidence"}
    assert set(schema["properties"]["result"]["required"]) == {
        "head_sha",
        "claims",
        "changed_files",
        "unresolved",
    }
    assert result_properties["claims"]["maxItems"] == 0
    assert result_properties["unresolved"]["maxItems"] == 0
    assert schema["properties"]["evidence"]["maxItems"] == 0
    assert result_properties["head_sha"]["minLength"] == 1
    assert result_properties["changed_files"]["items"]["minLength"] == 1

    source_payload = json.loads(successful_output("RUN-007-001"))
    valid_payload = json.loads(json.dumps(source_payload))
    valid_payload["result"]["claims"] = []
    valid_payload["result"]["unresolved"] = []
    valid_payload["evidence"] = []
    jsonschema.validate(instance=valid_payload, schema=schema)

    for path, item in (
        (
            ("result", "claims"),
            source_payload["result"]["claims"][0],
        ),
        (("result", "unresolved"), "still unresolved"),
        (("evidence",), source_payload["evidence"][0]),
    ):
        invalid_payload = json.loads(json.dumps(valid_payload))
        target = invalid_payload
        for segment in path[:-1]:
            target = target[segment]
        target[path[-1]] = [item]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=invalid_payload, schema=schema)


def test_repair_schema_preserves_result_package_shape() -> None:
    assert REPAIR_RESULT_PACKAGE_SCHEMA_PATH.exists()
    schema = json.loads(
        REPAIR_RESULT_PACKAGE_SCHEMA_PATH.read_text(encoding="utf-8")
    )
    result_properties = schema["properties"]["result"]["properties"]
    claim_properties = result_properties["claims"]["items"]["properties"]

    assert set(schema["required"]) == {"result", "evidence"}
    assert set(schema["properties"]["result"]["required"]) == {
        "head_sha",
        "claims",
        "changed_files",
        "unresolved",
    }
    assert set(result_properties["claims"]["items"]["required"]) == {
        "id",
        "satisfies",
        "claim",
        "evidence",
    }
    assert result_properties["claims"]["minItems"] == 1
    assert result_properties["unresolved"]["maxItems"] == 0
    assert schema["properties"]["evidence"]["maxItems"] == 0
    assert claim_properties["evidence"]["maxItems"] == 0
    assert result_properties["head_sha"]["minLength"] == 1
    assert result_properties["changed_files"]["items"]["minLength"] == 1
    assert claim_properties["id"]["minLength"] == 1
    assert claim_properties["satisfies"]["minItems"] == 1
    assert claim_properties["satisfies"]["items"]["minLength"] == 1
    assert claim_properties["claim"]["minLength"] == 1

    source_payload = json.loads(successful_output("RUN-079-002"))
    valid_payload = json.loads(json.dumps(source_payload))
    valid_payload["result"]["claims"][0]["evidence"] = []
    valid_payload["evidence"] = []
    jsonschema.validate(instance=valid_payload, schema=schema)


def test_repair_schema_rejects_run_079_002_empty_claims_regression() -> None:
    schema = json.loads(
        REPAIR_RESULT_PACKAGE_SCHEMA_PATH.read_text(encoding="utf-8")
    )
    payload = json.loads(successful_output("RUN-079-002"))
    payload["result"]["claims"] = []
    payload["evidence"] = []

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("result", "unresolved"), ["still unresolved"]),
        (("evidence",), None),
        (("result", "claims", 0, "evidence"), ["E1"]),
    ],
    ids=["unresolved", "root-evidence", "claim-evidence"],
)
def test_repair_schema_rejects_nonempty_runtime_owned_arrays(path, value) -> None:
    schema = json.loads(
        REPAIR_RESULT_PACKAGE_SCHEMA_PATH.read_text(encoding="utf-8")
    )
    payload = json.loads(successful_output("RUN-080-001"))
    runtime_evidence = payload["evidence"]
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []
    if path == ("evidence",):
        value = runtime_evidence
    target = payload
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


@pytest.mark.parametrize(
    "path",
    [
        ("result", "head_sha"),
        ("result", "claims", 0, "id"),
        ("result", "claims", 0, "satisfies", 0),
        ("result", "claims", 0, "claim"),
        ("result", "claims", 0, "evidence", 0),
        ("result", "changed_files", 0),
        ("result", "unresolved", 0),
        ("evidence", 0, "evidence_id"),
        ("evidence", 0, "run_id"),
        ("evidence", 0, "subject_sha"),
        ("evidence", 0, "type"),
        ("evidence", 0, "source", "command"),
        ("evidence", 0, "result", "summary"),
        ("evidence", 0, "raw", "path"),
    ],
)
def test_schema_rejects_empty_canonical_string(path) -> None:
    schema = json.loads(RESULT_PACKAGE_SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = json.loads(successful_output("RUN-007-001"))
    payload["result"]["unresolved"] = ["known issue"]

    target = payload
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = ""

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


@pytest.mark.parametrize(
    "array_path",
    [
        ("result", "claims"),
        ("result", "changed_files"),
        ("result", "unresolved"),
        ("evidence",),
    ],
)
def test_schema_keeps_canonical_arrays_allowed_empty(array_path) -> None:
    schema = json.loads(RESULT_PACKAGE_SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = json.loads(successful_output("RUN-007-001"))
    if array_path == ("evidence",):
        payload["result"]["claims"] = []

    target = payload
    for segment in array_path[:-1]:
        target = target[segment]
    target[array_path[-1]] = []

    jsonschema.validate(instance=payload, schema=schema)


def test_invalid_output_remains_explicit_failure_cases() -> None:
    task, run, registry, boundary = make_execution()

    def runner_non_json(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=0, stdout="not-json", stderr=""
        )

    lease1 = registry.acquire(run)
    with pytest.raises(CodexOutputError, match="invalid canonical output"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease1,
            adapter=CodexAdapter(runner=runner_non_json),
        )
    registry.release(lease1)

    def runner_missing_key(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps({"evidence": []}),
            stderr="",
        )

    lease2 = registry.acquire(run)
    with pytest.raises(CodexOutputError, match="invalid canonical output"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease2,
            adapter=CodexAdapter(runner=runner_missing_key),
        )
    registry.release(lease2)

    def runner_bad_type(command, **kwargs):
        payload = json.loads(successful_output(run.run_id))
        payload["evidence"][0]["result"]["exit_code"] = "zero"
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    lease3 = registry.acquire(run)
    with pytest.raises(CodexOutputError, match="invalid canonical output"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease3,
            adapter=CodexAdapter(runner=runner_bad_type),
        )
    registry.release(lease3)


def test_handoff_preserves_semantics_but_withholds_runtime_verification() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    captured = {}

    def runner(command, **kwargs):
        captured["input"] = kwargs["input"].decode("utf-8", errors="strict")
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(run.run_id),
            stderr="",
        )

    boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=CodexAdapter(runner=runner),
    )

    payload = json.loads(captured["input"].split("CANONICAL_INPUT:\n", 1)[1])
    context = payload.pop("execution_context")
    expected_task = asdict(task)
    expected_task.pop("verification")
    expected = json.loads(json.dumps({"task": expected_task, "run": asdict(run)}))
    assert_native_executor_context(context, operation="PRIMARY")
    assert payload == expected
    assert task.verification.required == ("pytest tests/test_codex_adapter.py",)


def test_success_normalizes_and_validates_result_package() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(run.run_id),
            stderr="diagnostic",
        )

    package = boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=CodexAdapter(runner=runner),
    )

    assert package.result.head_sha == "def456"
    assert package.result.claims[0].satisfies == ("AC1",)
    assert package.evidence[0].run_id == run.run_id


def test_nonzero_exit_propagates_without_result_package() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=7,
            stdout="partial output",
            stderr="native failure",
        )

    with pytest.raises(CodexExecutionError, match="code 7") as captured:
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=CodexAdapter(runner=runner),
        )

    assert captured.value.exit_code == 7
    assert captured.value.stdout == "partial output"
    assert captured.value.stderr == "native failure"


def test_invalid_success_output_is_explicit_failure() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout="not-json",
            stderr="",
        )

    with pytest.raises(CodexOutputError, match="invalid canonical output"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=CodexAdapter(runner=runner),
        )


def test_boundary_rejects_inactive_lease_before_process_invocation() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    registry.release(lease)
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        raise AssertionError("runner must not be invoked")

    with pytest.raises(ExecutorBoundaryError, match="active task lease"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=CodexAdapter(runner=runner),
        )

    assert calls == []


def test_codex_command_deterministic_model_and_reasoning_across_operations() -> None:
    task, run, _, _ = make_execution()
    remediation_exec = RemediationExecution(
        review_id="REV-1",
        finding=parse_review(
            "review_id: REV-1\nreviewed_sha: '123456'\nmode: PRIMARY\nverdict: CHANGES_REQUIRED\nacceptance: {AC1: FAIL}\nfindings:\n  - id: F1\n    basis: AC1\n    action: CODE_FIX\n    location: f.py\n    issue: i\n    expected: e\n"
        ).findings[0],
        remediation=parse_remediation(
            "finding_id: F1\naction: CODE_FIX\nreviewed_sha: '123456'\nmodification_scope: [src/aios_renew/codex_adapter.py]\naffected_verification: [pytest]\n"
        ),
        run=run,
    )
    repair_exec = {
        "run": run,
        "root_base_sha": "abc123",
        "repair": {
            "action": "CODE_FIX",
            "instructions": ["Fix"],
            "modification_scope": ["src/aios_renew/codex_adapter.py"],
        },
    }
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=successful_output(run.run_id),
            stderr="",
        )

    adapter = CodexAdapter(runner=runner)

    # 1. PRIMARY
    adapter.execute(task=task, run=run)
    cmd_primary = calls[-1][0]
    assert cmd_primary[cmd_primary.index("-m") + 1] == "gpt-5.6-sol"
    assert cmd_primary[cmd_primary.index("-c") + 1] == 'model_reasoning_effort="high"'

    # 2. REMEDIATION
    adapter.execute_remediation(execution=remediation_exec)
    cmd_remediation = calls[-1][0]
    assert cmd_remediation[cmd_remediation.index("-m") + 1] == "gpt-5.6-sol"
    assert cmd_remediation[cmd_remediation.index("-c") + 1] == 'model_reasoning_effort="high"'

    # 3. REPAIR
    adapter.execute_repair(execution=repair_exec)
    cmd_repair = calls[-1][0]
    assert cmd_repair[cmd_repair.index("-m") + 1] == "gpt-5.6-sol"
    assert cmd_repair[cmd_repair.index("-c") + 1] == 'model_reasoning_effort="high"'

    # Preserves sandbox, output-schema, color, cd
    assert cmd_primary[cmd_primary.index("--cd") + 1] == "C:/workspace"
    assert cmd_primary[cmd_primary.index("--sandbox") + 1] == "workspace-write"
    assert cmd_primary[cmd_primary.index("--output-schema") + 1] == str(RESULT_PACKAGE_SCHEMA_PATH)
    assert cmd_remediation[cmd_remediation.index("--output-schema") + 1] == str(
        REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH
    )
    assert cmd_repair[cmd_repair.index("--output-schema") + 1] == str(
        REPAIR_RESULT_PACKAGE_SCHEMA_PATH
    )
    assert cmd_primary[cmd_primary.index("--color") + 1] == "never"
    assert "--json" in cmd_primary


def test_codex_unsupported_model_fails_closed_without_fallback_or_retry() -> None:
    task, run, _, _ = make_execution()
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=1,
            stdout="",
            stderr="Error: model 'gpt-5.6-sol' not found or unsupported",
        )

    adapter = CodexAdapter(runner=runner)
    with pytest.raises(CodexExecutionError, match="model 'gpt-5.6-sol' not found or unsupported") as exc_info:
        adapter.execute(task=task, run=run)

    assert len(calls) == 1
    assert exc_info.value.exit_code == 1


def test_codex_extract_token_usage_jsonl_stream() -> None:
    pkg = json.loads(successful_output("RUN-007-001"))
    lines = [
        json.dumps({"type": "session.started", "session_id": "sess_1"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(pkg)}}),
        json.dumps({
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1200,
                "cached_input_tokens": 400,
                "cache_write_input_tokens": 150,
                "output_tokens": 350,
                "reasoning_output_tokens": 80,
                "total_tokens": 1550,
            },
        }),
    ]
    stdout = "\n".join(lines)
    usage = extract_token_usage(stdout)
    assert usage == TokenUsage(input_tokens=1200, cached_input_tokens=400, output_tokens=350)


def test_codex_extract_token_usage_cached_key_alternatives() -> None:
    # 1. cached_tokens
    u1 = extract_token_usage(
        json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "cached_tokens": 30, "output_tokens": 20},
        })
    )
    assert u1 == TokenUsage(input_tokens=100, cached_input_tokens=30, output_tokens=20)

    # 2. prompt_tokens_details.cached_tokens
    u2 = extract_token_usage(
        json.dumps({
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 25},
            },
        })
    )
    assert u2 == TokenUsage(input_tokens=100, cached_input_tokens=25, output_tokens=20)


@pytest.mark.parametrize(
    "payload",
    [
        # Missing cached tokens
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}},
        # Missing output tokens
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20}},
        # Missing input tokens
        {"type": "turn.completed", "usage": {"cached_input_tokens": 20, "output_tokens": 20}},
        # Bool counter
        {"type": "turn.completed", "usage": {"input_tokens": True, "cached_input_tokens": 20, "output_tokens": 20}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": False, "output_tokens": 20}},
        # Negative counter
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": -5, "output_tokens": 20}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": -1}},
        # cached > input
        {"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 100, "output_tokens": 20}},
        # Conflicting cached counters in same object
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "cached_tokens": 30, "output_tokens": 20}},
        # Malformed non-mapping usage
        {"type": "turn.completed", "usage": "not-a-dict"},
        # None or non-json
        "plain text without json",
    ],
)
def test_codex_extract_token_usage_fail_soft_edge_cases(payload: object) -> None:
    data = json.dumps(payload) if isinstance(payload, dict) else payload
    assert extract_token_usage(data) is None


def test_codex_extract_token_usage_conflicting_records_fail_soft() -> None:
    lines = [
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 200, "cached_input_tokens": 50, "output_tokens": 30}}),
    ]
    assert extract_token_usage("\n".join(lines)) is None


def test_codex_extract_token_usage_unknown_event_yields_unavailable() -> None:
    # Unknown event with otherwise valid usage-shaped payload yields unavailable/null
    unknown_record = {
        "type": "unknown.event",
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 20,
            "output_tokens": 10,
        },
    }
    assert extract_token_usage(json.dumps(unknown_record)) is None
    assert extract_token_usage(unknown_record) is None

    # Unknown event with direct counters yields unavailable/null
    unknown_direct = {
        "type": "future.token_event",
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 10,
    }
    assert extract_token_usage(json.dumps(unknown_direct)) is None
    assert extract_token_usage(unknown_direct) is None

    # Unknown event in JSONL stream does not supply token_usage
    stream = "\n".join([
        json.dumps({"type": "session.started", "session_id": "sess_1"}),
        json.dumps({"type": "turn.started"}),
        json.dumps(unknown_record),
    ])
    assert extract_token_usage(stream) is None

    # Untyped usage record yields unavailable/null
    untyped = {
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 20,
            "output_tokens": 10,
        },
    }
    assert extract_token_usage(json.dumps(untyped)) is None
    assert extract_token_usage(untyped) is None



def test_codex_execute_records_usage_and_preserves_single_invocation() -> None:
    task, run, _, _ = make_execution()
    calls = []
    recorded_usages = []

    pkg = json.loads(successful_output(run.run_id))
    pkg["result"]["claims"][0]["evidence"] = []
    pkg["evidence"] = []

    lines = [
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(pkg)}}),
        json.dumps({
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1500,
                "cached_input_tokens": 600,
                "cache_write_input_tokens": 100,
                "output_tokens": 250,
                "reasoning_output_tokens": 50,
            },
        }),
    ]
    stdout_content = "\n".join(lines)

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        proc = subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=stdout_content.encode("utf-8"),
            stderr=b"",
        )
        return proc

    runner.record_token_usage = lambda u: recorded_usages.append(u)

    adapter = CodexAdapter(runner=runner)
    result_pkg = adapter.execute(task=task, run=run)

    assert len(calls) == 1  # Exactly one invocation (AC8)
    assert result_pkg.result.head_sha == "def456"
    assert recorded_usages == [TokenUsage(input_tokens=1500, cached_input_tokens=600, output_tokens=250)]


def test_codex_execute_failure_preserves_token_usage_without_altering_error() -> None:
    task, run, _, _ = make_execution()
    recorded_usages = []

    lines = [
        json.dumps({
            "type": "turn.completed",
            "usage": {
                "input_tokens": 800,
                "cached_input_tokens": 200,
                "output_tokens": 50,
            },
        }),
    ]
    stdout_content = "\n".join(lines)

    def runner(command, **kwargs):
        proc = subprocess.CompletedProcess(
            command,
            returncode=2,
            stdout=stdout_content.encode("utf-8"),
            stderr=b"fatal: syntax error in generated code",
        )
        return proc

    runner.record_token_usage = lambda u: recorded_usages.append(u)

    adapter = CodexAdapter(runner=runner)
    with pytest.raises(CodexExecutionError, match="syntax error in generated code") as exc_info:
        adapter.execute(task=task, run=run)

    assert exc_info.value.exit_code == 2
    # Usage was safely recorded despite failure
    assert recorded_usages == [TokenUsage(input_tokens=800, cached_input_tokens=200, output_tokens=50)]


def test_codex_execute_output_error_preserves_token_usage() -> None:
    task, run, _, _ = make_execution()
    recorded_usages = []

    lines = [
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "not valid result package json"}}),
        json.dumps({
            "type": "turn.completed",
            "usage": {
                "input_tokens": 900,
                "cached_input_tokens": 300,
                "output_tokens": 70,
            },
        }),
    ]
    stdout_content = "\n".join(lines)

    def runner(command, **kwargs):
        proc = subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=stdout_content.encode("utf-8"),
            stderr=b"",
        )
        return proc

    runner.record_token_usage = lambda u: recorded_usages.append(u)

    adapter = CodexAdapter(runner=runner)
    with pytest.raises(CodexOutputError):
        adapter.execute(task=task, run=run)

    assert recorded_usages == [TokenUsage(input_tokens=900, cached_input_tokens=300, output_tokens=70)]
