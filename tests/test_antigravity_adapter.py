import inspect
import json
import subprocess
from pathlib import Path

import pytest

from aios_renew import (
    RESULT_PACKAGE_SCHEMA_PATH,
    AntigravityAdapter,
    AntigravityExecutionError,
    AntigravityOutputError,
    ExecutorBoundary,
    ExecutorBoundaryError,
    ResultPackage,
    Run,
    RunLeaseRegistry,
    parse_task,
    parse_remediation,
    parse_review,
)
from aios_renew.review import RemediationExecution
from aios_renew.dispatcher import NativeExecutionPolicy
from aios_renew.antigravity_adapter import (
    REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH,
    extract_token_usage,
)
from aios_renew.run_observation import TokenUsage


TASK_SOURCE = """
task_id: TASK-008
revision: 1
goal: Add a minimal native Antigravity adapter.
problem: ExecutorBoundary cannot yet invoke Antigravity.
assumptions: []
scope:
  inspect: []
  modify:
    - src/aios_renew/antigravity_adapter.py
non_goals:
  - Executor routing.
constraints:
  hard:
    - Pass TASK and RUN through unchanged.
acceptance:
  - id: AC1
    condition: Antigravity output normalizes into ResultPackage.
verification:
  required:
    - pytest tests/test_antigravity_adapter.py
"""


def make_execution():
    task = parse_task(TASK_SOURCE)
    run = Run.from_task(
        run_id="RUN-008-001",
        task=task,
        executor="antigravity",
        base_sha="abc123",
        workspace="C:/workspace",
    )
    registry = RunLeaseRegistry()
    return task, run, registry, ExecutorBoundary(registry)


def make_remediation_execution() -> RemediationExecution:
    task, run, _, _ = make_execution()
    review = parse_review(
        """
review_id: REVIEW-008-001
reviewed_sha: abc123
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance: {AC1: FAIL}
findings:
  - id: R1
    basis: AC1
    action: EVIDENCE_ONLY
    location: tests/test_antigravity_adapter.py
    issue: Evidence missing.
    expected: Supply evidence.
"""
    )
    return RemediationExecution(
        review_id=review.review_id,
        finding=review.findings[0],
        remediation=parse_remediation(
            """
finding_id: R1
action: EVIDENCE_ONLY
reviewed_sha: abc123
modification_scope: []
affected_verification: [pytest tests/test_antigravity_adapter.py]
"""
        ),
        run=run,
    )


def successful_output(run_id: str) -> dict:
    return {
        "result": {
            "head_sha": "def456",
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "Antigravity adapter completed the task.",
                    "evidence": ["E1"],
                }
            ],
            "changed_files": ["src/aios_renew/antigravity_adapter.py"],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id,
                "subject_sha": "def456",
                "type": "TEST",
                "source": {
                    "command": "pytest tests/test_antigravity_adapter.py"
                },
                "result": {"exit_code": 0, "summary": "tests passed"},
                "raw": {"path": ".ai/evidence/E1.log"},
            }
        ],
    }


def test_antigravity_adapter_identity() -> None:
    adapter = AntigravityAdapter(transport=lambda **kwargs: {})

    assert adapter.executor == "antigravity"
    assert list(inspect.signature(adapter.execute).parameters) == ["task", "run"]


def test_hands_off_unchanged_task_and_run() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    captured = {}

    def transport(*, task, run):
        captured["task"] = task
        captured["run"] = run
        return successful_output(run.run_id)

    boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=AntigravityAdapter(transport=transport),
    )

    assert captured == {"task": task, "run": run}
    assert captured["task"] is task
    assert captured["run"] is run


def test_hands_off_one_narrow_remediation_execution() -> None:
    execution = make_remediation_execution()
    run = execution.run
    captured = []
    output = successful_output(run.run_id)
    output["result"]["claims"] = []

    package = AntigravityAdapter(
        transport=lambda **kwargs: (captured.append(kwargs), output)[1]
    ).execute_remediation(execution=execution)

    assert captured == [{"execution": execution}]
    assert package.result.claims == ()


def test_boundary_rejects_without_active_lease_before_native_invocation() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    registry.release(lease)
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        raise AssertionError("transport must not be invoked")

    with pytest.raises(ExecutorBoundaryError, match="active task lease"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=transport),
        )

    assert calls == []


def test_success_normalizes_result_package() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    adapter = AntigravityAdapter(
        transport=lambda **kwargs: json.dumps(successful_output(run.run_id))
    )

    package = boundary.invoke(task=task, run=run, lease=lease, adapter=adapter)

    assert isinstance(package, ResultPackage)
    assert package.result.head_sha == "def456"
    assert package.result.claims[0].satisfies == ("AC1",)
    assert package.evidence[0].run_id == run.run_id


def test_singleton_string_satisfies_is_wrapped_before_validation() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    output = successful_output(run.run_id)
    output["result"]["claims"][0]["satisfies"] = "AC1"

    package = boundary.invoke(
        task=task,
        run=run,
        lease=lease,
        adapter=AntigravityAdapter(transport=lambda **kwargs: output),
    )

    assert package.result.claims[0].satisfies == ("AC1",)


def test_list_satisfies_is_preserved() -> None:
    output = successful_output("RUN-008-001")
    output["result"]["claims"][0]["satisfies"] = ["AC1", "AC2"]

    package = AntigravityAdapter._normalize(output)

    assert package.result.claims[0].satisfies == ("AC1", "AC2")


def test_structural_output_accepts_empty_runtime_owned_evidence() -> None:
    task, run, _, _ = make_execution()
    output = successful_output("RUN-008-001")
    output["result"]["claims"][0]["satisfies"] = "AC1"
    output["result"]["claims"][0]["evidence"] = []
    output["evidence"] = []

    package = AntigravityAdapter(
        transport=lambda **kwargs: output,
        structural_output=True,
    ).execute(task=task, run=run)

    assert package.result.claims[0].satisfies == ("AC1",)
    assert package.result.claims[0].evidence == ()
    assert package.evidence == ()


def test_structural_remediation_accepts_one_leading_bom() -> None:
    execution = make_remediation_execution()
    output = successful_output(execution.run.run_id)
    output["result"]["claims"][0]["evidence"] = []
    output["evidence"] = []
    serialized = json.dumps(output)

    bom_package = AntigravityAdapter(
        transport=lambda **kwargs: "\ufeff" + serialized,
        structural_output=True,
    ).execute_remediation(execution=execution)
    plain_package = AntigravityAdapter(
        transport=lambda **kwargs: serialized,
        structural_output=True,
    ).execute_remediation(execution=execution)
    mapping_package = AntigravityAdapter(
        transport=lambda **kwargs: output,
        structural_output=True,
    ).execute_remediation(execution=execution)

    assert bom_package == plain_package == mapping_package


@pytest.mark.parametrize("malformed", ["{", "\ufeff{", "\ufeff\ufeff{}"])
def test_structural_remediation_malformed_json_remains_fail_closed(
    malformed: str,
) -> None:
    execution = make_remediation_execution()
    adapter = AntigravityAdapter(
        transport=lambda **kwargs: malformed,
        structural_output=True,
    )

    with pytest.raises(AntigravityOutputError, match="invalid structural output"):
        adapter.execute_remediation(execution=execution)


def test_canonical_output_still_requires_claim_evidence() -> None:
    output = successful_output("RUN-008-001")
    output["result"]["claims"][0]["evidence"] = []

    with pytest.raises(AntigravityOutputError, match="invalid canonical output"):
        AntigravityAdapter._normalize(output)


@pytest.mark.parametrize("malformed", [1, {"id": "AC1"}, None])
def test_malformed_non_string_satisfies_still_fails(malformed) -> None:
    output = successful_output("RUN-008-001")
    output["result"]["claims"][0]["satisfies"] = malformed

    with pytest.raises(AntigravityOutputError, match="invalid canonical output"):
        AntigravityAdapter._normalize(output)


@pytest.mark.parametrize("satisfies", ["AC1,AC2", "AC-UNKNOWN"])
def test_string_satisfies_is_not_reinterpreted_and_remains_canonically_bound(
    satisfies: str,
) -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    output = successful_output(run.run_id)
    output["result"]["claims"][0]["satisfies"] = satisfies

    with pytest.raises(ValueError, match="unknown acceptance criteria"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=lambda **kwargs: output),
        )


def test_native_failure_propagates() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)

    def transport(**kwargs):
        raise OSError("native session unavailable")

    with pytest.raises(
        AntigravityExecutionError, match="native session unavailable"
    ) as captured:
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=transport),
        )

    assert isinstance(captured.value.__cause__, OSError)


def test_invalid_output_is_explicit_failure() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)

    with pytest.raises(AntigravityOutputError, match="invalid canonical output"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=lambda **kwargs: {"evidence": []}),
        )


def test_boundary_retains_canonical_artifact_validation() -> None:
    task, run, registry, boundary = make_execution()
    lease = registry.acquire(run)
    output = successful_output("RUN-WRONG")

    with pytest.raises(ValueError, match="does not reference RUN"):
        boundary.invoke(
            task=task,
            run=run,
            lease=lease,
            adapter=AntigravityAdapter(transport=lambda **kwargs: output),
        )


def test_core_executor_boundary_does_not_require_antigravity_specific_logic() -> None:
    source = inspect.getsource(ExecutorBoundary)

    assert "antigravity" not in source.lower()


def test_native_adapter_owns_read_only_command_handoff_and_envelope(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    handoff_path = repo / ".git" / "aios" / "handoff.json"
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "SUCCESS",
                    "response": "",
                    "structured_output": payload,
                }
            ),
            stderr="",
        )

    package = AntigravityAdapter(
        runner=runner,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=False),
        repo=repo,
        handoff_path=handoff_path,
    ).execute(task=task, run=run)

    command, kwargs = calls[0]
    assert command[command.index("--model") + 1] == "gemini-3.8-flash"
    assert command[command.index("--effort") + 1] == "high"
    assert command[command.index("--mode") + 1] == "plan"
    assert "--dangerously-skip-permissions" not in command
    assert command[command.index("--print-timeout") + 1] == "60m"
    assert kwargs["timeout"] == 65 * 60
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert "verification" not in handoff["task"]
    assert "Runtime is the canonical verification owner" in command[2]
    assert "baseline, full, or canonical verification" in command[2]
    assert "repository-wide rediscovery" in command[2]
    assert handoff["execution_context"]["operation"] == "PRIMARY"
    assert package.result.head_sha == "def456"


def test_native_repair_instruction_assigns_complete_changed_files_to_runtime(
    tmp_path: Path,
) -> None:
    _, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    handoff_path = repo / ".git" / "aios" / "repair-handoff.json"
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "SUCCESS",
                    "response": "",
                    "structured_output": payload,
                }
            ),
            stderr="",
        )

    AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
    ).execute_repair(
        execution={
            "run": run,
            "root_base_sha": "root",
            "repair": {
                "action": "CODE_FIX",
                "instructions": ["Apply the narrow correction."],
                "modification_scope": ["src/aios_renew/antigravity_adapter.py"],
            },
        }
    )

    command = calls[0][0]
    instruction = command[command.index("--print") + 1]
    assert (
        "Runtime derives and persists canonical result.changed_files" in instruction
    )
    assert (
        "do not reconstruct or enumerate that historical file set" in instruction
    )
    assert "only the narrow repair delta or be empty" in instruction
    assert "complete original TASK delta" not in instruction


def test_native_antigravity_timeout_is_terminal_to_one_invocation(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    adapter = AntigravityAdapter(
        runner=runner,
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
    )
    with pytest.raises(AntigravityExecutionError, match="60-minute"):
        adapter.execute(task=task, run=run)

    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 65 * 60


def test_native_antigravity_timeout_preserves_process_supplied_partial_output(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()

    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            output=b"partial native progress",
            stderr=b"provider deadline",
        )

    adapter = AntigravityAdapter(
        runner=runner,
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
    )
    with pytest.raises(AntigravityExecutionError) as captured:
        adapter.execute(task=task, run=run)

    assert captured.value.stdout == b"partial native progress"
    assert captured.value.stderr == b"provider deadline"


def test_antigravity_early_native_return_is_accepted_immediately(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "SUCCESS",
                    "response": "",
                    "structured_output": payload,
                }
            ),
            stderr="",
        )

    package = AntigravityAdapter(
        runner=runner,
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
    ).execute(task=task, run=run)

    assert package.result.head_sha == "def456"
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[command.index("--print-timeout") + 1] == "60m"
    assert kwargs["timeout"] == 65 * 60


def test_antigravity_command_deterministic_model_and_effort_across_operations(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    remediation_exec = RemediationExecution(
        review_id="REV-1",
        finding=parse_review(
            "review_id: REV-1\nreviewed_sha: '123456'\nmode: PRIMARY\nverdict: CHANGES_REQUIRED\nacceptance: {AC1: FAIL}\nfindings:\n  - id: F1\n    basis: AC1\n    action: CODE_FIX\n    location: f.py\n    issue: i\n    expected: e\n"
        ).findings[0],
        remediation=parse_remediation(
            "finding_id: F1\naction: CODE_FIX\nreviewed_sha: '123456'\nmodification_scope: [src/aios_renew/antigravity_adapter.py]\naffected_verification: [pytest]\n"
        ),
        run=run,
    )
    repair_exec = {
        "run": run,
        "root_base_sha": "abc123",
        "repair": {
            "action": "CODE_FIX",
            "instructions": ["Fix"],
            "modification_scope": ["src/aios_renew/antigravity_adapter.py"],
        },
    }
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "SUCCESS",
                    "response": "",
                    "structured_output": payload,
                }
            ),
            stderr="",
        )

    adapter = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=repo / ".git" / "aios" / "handoff.json",
    )

    # 1. PRIMARY
    adapter.execute(task=task, run=run)
    cmd_primary = calls[-1][0]
    assert cmd_primary[cmd_primary.index("--model") + 1] == "gemini-3.8-flash"
    assert cmd_primary[cmd_primary.index("--effort") + 1] == "high"

    # 2. REMEDIATION
    adapter.execute_remediation(execution=remediation_exec)
    cmd_remediation = calls[-1][0]
    assert cmd_remediation[cmd_remediation.index("--model") + 1] == "gemini-3.8-flash"
    assert cmd_remediation[cmd_remediation.index("--effort") + 1] == "high"

    # 3. REPAIR
    adapter.execute_repair(execution=repair_exec)
    cmd_repair = calls[-1][0]
    assert cmd_repair[cmd_repair.index("--model") + 1] == "gemini-3.8-flash"
    assert cmd_repair[cmd_repair.index("--effort") + 1] == "high"

    # Preserves other flags
    assert "--disable-slash-commands" in cmd_primary
    assert cmd_primary[cmd_primary.index("--mode") + 1] == "accept-edits"
    assert "--dangerously-skip-permissions" in cmd_primary
    assert cmd_primary[cmd_primary.index("--output-format") + 1] == "json"
    assert cmd_primary[cmd_primary.index("--json-schema") + 1] == str(RESULT_PACKAGE_SCHEMA_PATH)
    assert cmd_remediation[cmd_remediation.index("--json-schema") + 1] == str(
        REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH
    )
    assert cmd_repair[cmd_repair.index("--json-schema") + 1] == str(
        RESULT_PACKAGE_SCHEMA_PATH
    )


def test_antigravity_unsupported_model_fails_closed_without_fallback_or_retry(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=1,
            stdout="",
            stderr="Error: model 'gemini-3.8-flash' not recognized or unavailable",
        )

    adapter = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=repo / ".git" / "aios" / "handoff.json",
    )
    with pytest.raises(
        AntigravityExecutionError,
        match="model 'gemini-3.8-flash' not recognized or unavailable",
    ):
        adapter.execute(task=task, run=run)

    assert len(calls) == 1


def test_antigravity_extract_token_usage_envelope() -> None:
    pkg = successful_output("RUN-008-001")
    envelope = {
        "status": "SUCCESS",
        "response": "Done",
        "structured_output": pkg,
        "usage": {
            "input_tokens": 2000,
            "cache_read_tokens": 500,
            "output_tokens": 300,
            "thinking_tokens": 150,
            "total_tokens": 2450,
        },
    }
    usage = extract_token_usage(json.dumps(envelope))
    assert usage == TokenUsage(input_tokens=2000, cached_input_tokens=500, output_tokens=300)


def test_antigravity_extract_token_usage_cached_key_alternatives() -> None:
    # 1. cached_input_tokens
    u1 = extract_token_usage(json.dumps({
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 40,
            "output_tokens": 20,
        }
    }))
    assert u1 == TokenUsage(input_tokens=100, cached_input_tokens=40, output_tokens=20)

    # 2. cached_tokens
    u2 = extract_token_usage(json.dumps({
        "usage": {
            "input_tokens": 100,
            "cached_tokens": 35,
            "output_tokens": 20,
        }
    }))
    assert u2 == TokenUsage(input_tokens=100, cached_input_tokens=35, output_tokens=20)

    # 3. prompt_tokens_details.cached_tokens
    u3 = extract_token_usage(json.dumps({
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 30},
        }
    }))
    assert u3 == TokenUsage(input_tokens=100, cached_input_tokens=30, output_tokens=20)


@pytest.mark.parametrize(
    "payload",
    [
        # Missing cached counter
        {"usage": {"input_tokens": 100, "output_tokens": 20}},
        # Missing output tokens
        {"usage": {"input_tokens": 100, "cache_read_tokens": 20}},
        # Missing input tokens
        {"usage": {"cache_read_tokens": 20, "output_tokens": 20}},
        # Bool counter
        {"usage": {"input_tokens": True, "cache_read_tokens": 20, "output_tokens": 20}},
        {"usage": {"input_tokens": 100, "cache_read_tokens": False, "output_tokens": 20}},
        # Negative counter
        {"usage": {"input_tokens": 100, "cache_read_tokens": -5, "output_tokens": 20}},
        {"usage": {"input_tokens": 100, "cache_read_tokens": 20, "output_tokens": -1}},
        # cached > input
        {"usage": {"input_tokens": 50, "cache_read_tokens": 100, "output_tokens": 20}},
        # Conflicting cached counters
        {"usage": {"input_tokens": 100, "cache_read_tokens": 20, "cached_input_tokens": 30, "output_tokens": 20}},
        # Malformed non-mapping usage
        {"usage": "not-a-dict"},
        # None or non-json
        "plain text without json",
    ],
)
def test_antigravity_extract_token_usage_fail_soft_edge_cases(payload: object) -> None:
    data = json.dumps(payload) if isinstance(payload, dict) else payload
    assert extract_token_usage(data) is None


def test_antigravity_execute_records_usage_and_preserves_single_invocation(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()
    calls = []
    recorded_usages = []

    payload = successful_output(run.run_id)
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    envelope = {
        "status": "SUCCESS",
        "response": "",
        "structured_output": payload,
        "usage": {
            "input_tokens": 2200,
            "cache_read_tokens": 700,
            "output_tokens": 180,
            "thinking_tokens": 90,
            "total_tokens": 2470,
        },
    }

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope).encode("utf-8"),
            stderr=b"",
        )

    runner.record_token_usage = lambda u: recorded_usages.append(u)

    adapter = AntigravityAdapter(
        runner=runner,
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
    )
    result_pkg = adapter.execute(task=task, run=run)

    assert len(calls) == 1  # Exactly one native invocation (AC8)
    assert result_pkg.result.head_sha == "def456"
    assert recorded_usages == [TokenUsage(input_tokens=2200, cached_input_tokens=700, output_tokens=180)]


def test_antigravity_execute_nonzero_exit_preserves_token_usage(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()
    recorded_usages = []

    envelope = {
        "status": "FAILURE",
        "response": "",
        "usage": {
            "input_tokens": 1100,
            "cache_read_tokens": 300,
            "output_tokens": 80,
        },
    }

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=1,
            stdout=json.dumps(envelope).encode("utf-8"),
            stderr=b"process terminated unexpectedly",
        )

    runner.record_token_usage = lambda u: recorded_usages.append(u)

    adapter = AntigravityAdapter(
        runner=runner,
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
    )
    with pytest.raises(AntigravityExecutionError, match="returned nonzero"):
        adapter.execute(task=task, run=run)

    assert recorded_usages == [TokenUsage(input_tokens=1100, cached_input_tokens=300, output_tokens=80)]


def test_antigravity_execute_envelope_error_preserves_token_usage(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()
    recorded_usages = []

    envelope = {
        "status": "ERROR",
        "error": "rate limit exceeded",
        "usage": {
            "input_tokens": 1400,
            "cache_read_tokens": 400,
            "output_tokens": 90,
        },
    }

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope).encode("utf-8"),
            stderr=b"",
        )

    runner.record_token_usage = lambda u: recorded_usages.append(u)

    adapter = AntigravityAdapter(
        runner=runner,
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
    )
    with pytest.raises(AntigravityExecutionError, match="rate limit exceeded"):
        adapter.execute(task=task, run=run)

    assert recorded_usages == [TokenUsage(input_tokens=1400, cached_input_tokens=400, output_tokens=90)]


def test_antigravity_execute_missing_structured_output_preserves_token_usage(
    tmp_path: Path,
) -> None:
    task, run, _, _ = make_execution()
    recorded_usages = []

    envelope = {
        "status": "SUCCESS",
        "response": "Done with no payload",
        "usage": {
            "input_tokens": 1600,
            "cache_read_tokens": 500,
            "output_tokens": 120,
        },
    }

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope).encode("utf-8"),
            stderr=b"",
        )

    runner.record_token_usage = lambda u: recorded_usages.append(u)

    adapter = AntigravityAdapter(
        runner=runner,
        repo=tmp_path,
        handoff_path=tmp_path / "handoff.json",
    )
    with pytest.raises(AntigravityExecutionError, match="ResultPackage missing"):
        adapter.execute(task=task, run=run)

    assert recorded_usages == [TokenUsage(input_tokens=1600, cached_input_tokens=500, output_tokens=120)]
