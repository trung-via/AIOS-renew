import json
import subprocess
from dataclasses import asdict

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
    parse_task,
)


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
        "--sandbox",
        "workspace-write",
        "--output-schema",
        str(RESULT_PACKAGE_SCHEMA_PATH),
        "--color",
        "never",
        "-",
    )
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["text"] is True
    assert calls[0][1]["check"] is False


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
    assert claim_prop["properties"]["evidence"]["minItems"] == 1

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

    import jsonschema

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
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=empty_claim_evidence, schema=schema)


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


def test_hands_off_unchanged_canonical_task_and_run() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    captured = {}

    def runner(command, **kwargs):
        captured["input"] = kwargs["input"]
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
    expected = json.loads(json.dumps({"task": asdict(task), "run": asdict(run)}))
    assert payload == expected


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
