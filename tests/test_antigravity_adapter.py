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
    HEADLESS_PRINT_MODE_CONTRACT,
    REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH,
    REPAIR_RESULT_PACKAGE_SCHEMA_PATH,
    _native_instruction,
    extract_token_usage,
    native_instruction,
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
    assert "--mode" not in command
    assert "plan" not in command
    assert "--dangerously-skip-permissions" not in command
    assert "--disable-slash-commands" in command
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--json-schema") + 1] == str(RESULT_PACKAGE_SCHEMA_PATH)
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


def test_continue_implementation_native_repair_transport_allows_bounded_capture_once(
    tmp_path: Path,
) -> None:
    task, _, _, _ = make_execution()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "--quiet", str(repo)), check=True)
    subprocess.run(
        ("git", "-C", str(repo), "config", "user.name", "Antigravity Repair Test"),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(repo),
            "config",
            "user.email",
            "antigravity-repair@example.invalid",
        ),
        check=True,
    )
    (repo / "seed.txt").write_text("failed lineage\n", encoding="utf-8")
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
        executor="antigravity",
        base_sha=failed_head,
        workspace=str(repo),
    )
    handoff_path = repo / ".git" / "aios" / "repair-handoff.json"
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        instruction = command[command.index("--print") + 1]
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        assert handoff["repair"]["action"] == "CONTINUE_IMPLEMENTATION"
        assert handoff["failed_run_id"] == "RUN-182-002"
        assert handoff["failed_head_sha"] == failed_head
        assert (
            "necessary bounded repository inspection, discovery, or live capture"
            in instruction
        )
        assert "unfinished original TASK work is not prohibited" in instruction
        assert (
            "CODE_FIX authorizes mutation only to correct an established defect"
            in instruction
        )
        assert "NO_CHANGE authorizes no repository mutation" in instruction
        assert "recursively continue" in instruction
        assert "invoke an AIOS operator or worker launcher" in instruction

        (repo / "capture.json").write_text('{"captured": true}\n', encoding="utf-8")
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
            command,
            returncode=0,
            stdout=json.dumps(
                {"status": "SUCCESS", "response": "", "structured_output": payload}
            ),
            stderr="",
        )

    result = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
    ).execute_repair(
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


def test_native_no_change_repair_instruction_preserves_zero_mutation_semantics(
    tmp_path: Path,
) -> None:
    _, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    handoff_path = repo / "repair-handoff.json"
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["head_sha"] = run.base_sha
    payload["result"]["changed_files"] = []
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        instruction = command[command.index("--print") + 1]
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        assert handoff["repair"]["action"] == "NO_CHANGE"
        assert "NO_CHANGE authorizes no repository mutation" in instruction
        assert (
            "does not permit resuming unfinished original TASK implementation"
            in instruction
        )
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {"status": "SUCCESS", "response": "", "structured_output": payload}
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
            "root_base_sha": run.base_sha,
            "repair": {
                "action": "NO_CHANGE",
                "instructions": ["Return the unchanged candidate."],
                "modification_scope": [],
            },
        }
    )

    assert len(calls) == 1


def test_native_finalize_candidate_instruction_is_read_only_and_single_shot(
    tmp_path: Path,
) -> None:
    _, run, _, _ = make_execution()
    handoff_path = tmp_path / "repair-handoff.json"
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["head_sha"] = run.base_sha
    payload["result"]["changed_files"] = []
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append(command)
        instruction = command[command.index("--print") + 1]
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        assert handoff["repair"]["action"] == "FINALIZE_CANDIDATE"
        assert "FINALIZE_CANDIDATE authorizes no repository mutation" in instruction
        assert "inspect only the supplied exact clean failed candidate" in instruction
        assert "Do not edit files, commit, push" in instruction
        assert "do not execute canonical verification" in instruction
        assert "retry this admitted continuation" in instruction
        assert "reroute or fall back to another Executor" in instruction
        assert "--mode" not in command
        assert "plan" not in command
        assert "--dangerously-skip-permissions" not in command
        assert "--disable-slash-commands" in command
        assert command[command.index("--output-format") + 1] == "json"
        assert command[command.index("--json-schema") + 1] == str(REPAIR_RESULT_PACKAGE_SCHEMA_PATH)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {"status": "SUCCESS", "response": "", "structured_output": payload}
            ),
            stderr="",
        )

    package = AntigravityAdapter(
        runner=runner,
        repo=tmp_path,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=False),
    ).execute_repair(
        execution={
            "run": run,
            "failed_head_sha": run.base_sha,
            "repair": {
                "action": "FINALIZE_CANDIDATE",
                "instructions": ["Return the missing structural package."],
                "modification_scope": [],
            },
        }
    )

    assert len(calls) == 1
    assert isinstance(package, ResultPackage)
    assert package.result.head_sha == run.base_sha
    assert package.result.changed_files == ()
    assert package.result.claims[0].satisfies == ("AC1",)
    assert package.result.claims[0].evidence == ()
    assert package.evidence == ()


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
        REPAIR_RESULT_PACKAGE_SCHEMA_PATH
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


def test_zero_mutation_finalize_candidate_command_excludes_contradictory_plan_mode(
    tmp_path: Path,
) -> None:
    """AC1 & AC2: Zero-mutation command is coherent non-interactive and omits --mode plan and mutation flags."""
    _, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    handoff_path = repo / "repair-handoff.json"
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["head_sha"] = run.base_sha
    payload["result"]["changed_files"] = []
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {"status": "SUCCESS", "response": "", "structured_output": payload}
            ),
            stderr="",
        )

    adapter = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=False),
    )
    adapter.execute_repair(
        execution={
            "run": run,
            "failed_head_sha": run.base_sha,
            "repair": {
                "action": "FINALIZE_CANDIDATE",
                "instructions": ["Return missing structural package."],
                "modification_scope": [],
            },
        }
    )

    assert len(calls) == 1
    cmd = calls[0]
    # Verify non-interactive structural invocation
    assert cmd[0] == "agy"
    assert cmd[1] == "--print"
    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--json-schema") + 1] == str(REPAIR_RESULT_PACKAGE_SCHEMA_PATH)
    assert "--disable-slash-commands" in cmd

    # AC1: no contradictory plan-mode/disabled-slash-command combination
    assert "--mode" not in cmd
    assert "plan" not in cmd
    # AC2: zero mutation capability retained
    assert "--dangerously-skip-permissions" not in cmd
    assert "accept-edits" not in cmd


def test_zero_mutation_response_normalizes_to_structural_result_package(
    tmp_path: Path,
) -> None:
    """AC3: Valid schema-constrained native zero-mutation response normalizes into ResultPackage."""
    _, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    handoff_path = repo / "repair-handoff.json"
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["head_sha"] = run.base_sha
    payload["result"]["changed_files"] = []
    payload["result"]["claims"][0]["satisfies"] = "AC1"
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {"status": "SUCCESS", "response": "", "structured_output": payload}
            ),
            stderr="",
        )

    adapter = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=False),
    )
    result_pkg = adapter.execute_repair(
        execution={
            "run": run,
            "failed_head_sha": run.base_sha,
            "repair": {
                "action": "FINALIZE_CANDIDATE",
                "instructions": ["Return missing structural package."],
                "modification_scope": [],
            },
        }
    )

    assert len(calls) == 1
    assert isinstance(result_pkg, ResultPackage)
    assert result_pkg.result.head_sha == run.base_sha
    assert result_pkg.result.changed_files == ()
    assert len(result_pkg.result.claims) == 1
    assert result_pkg.result.claims[0].satisfies == ("AC1",)
    assert result_pkg.result.claims[0].evidence == ()
    assert result_pkg.result.unresolved == ()
    assert result_pkg.evidence == ()


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode", "expected_err_type", "match_pattern"),
    [
        # RUN-121-005 exact scenario: empty stdout with plan mode warning on stderr
        (
            "",
            "warning: --mode plan has no effect while slash command expansion is disabled.",
            0,
            AntigravityExecutionError,
            "Antigravity ResultPackage missing: warning: --mode plan has no effect while slash command expansion is disabled.",
        ),
        # Nonzero exit code
        (
            "",
            "process failed",
            1,
            AntigravityExecutionError,
            "Antigravity CLI returned nonzero",
        ),
        # Malformed terminal JSON
        (
            "not json at all",
            "",
            0,
            AntigravityExecutionError,
            "malformed terminal JSON",
        ),
        # Terminal envelope status is ERROR
        (
            json.dumps({"status": "ERROR", "error": "rate limit reached"}),
            "",
            0,
            AntigravityExecutionError,
            "Antigravity CLI terminal status is ERROR: rate limit reached",
        ),
        # Terminal envelope missing structured_output
        (
            json.dumps({"status": "SUCCESS", "response": "Done without payload"}),
            "",
            0,
            AntigravityExecutionError,
            "Antigravity ResultPackage missing",
        ),
        # structured_output missing required fields (e.g. missing result)
        (
            json.dumps({"status": "SUCCESS", "structured_output": {"evidence": []}}),
            "",
            0,
            AntigravityOutputError,
            "invalid structural output",
        ),
    ],
)
def test_zero_mutation_fails_closed_without_synthesis_retry_or_fallback(
    tmp_path: Path,
    stdout: str,
    stderr: str,
    returncode: int,
    expected_err_type: type[Exception],
    match_pattern: str,
) -> None:
    """AC4: Missing, malformed, or invalid native structural output fails closed without retry, fallback, or synthesis."""
    _, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    handoff_path = repo / "repair-handoff.json"
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=returncode,
            stdout=stdout.encode("utf-8"),
            stderr=stderr.encode("utf-8"),
        )

    adapter = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=False),
    )

    with pytest.raises(expected_err_type, match=match_pattern):
        adapter.execute_repair(
            execution={
                "run": run,
                "failed_head_sha": run.base_sha,
                "repair": {
                    "action": "FINALIZE_CANDIDATE",
                    "instructions": ["Return missing structural package."],
                    "modification_scope": [],
                },
            }
        )

    # Fail closed: exactly one invocation, no retry, no reroute, no fallback
    assert len(calls) == 1


def test_mutation_authorized_command_behavior_unchanged_across_all_operations(
    tmp_path: Path,
) -> None:
    """AC5: Mutation-authorized command construction preserves --mode accept-edits and --dangerously-skip-permissions."""
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
    repair_exec_code_fix = {
        "run": run,
        "root_base_sha": "abc123",
        "repair": {
            "action": "CODE_FIX",
            "instructions": ["Fix defect."],
            "modification_scope": ["src/aios_renew/antigravity_adapter.py"],
        },
    }
    repair_exec_continue = {
        "run": run,
        "failed_run_id": "RUN-126-001",
        "failed_head_sha": "abc123",
        "root_base_sha": "abc123",
        "repair": {
            "action": "CONTINUE_IMPLEMENTATION",
            "instructions": ["Continue unfinished implementation."],
            "modification_scope": ["src/aios_renew/antigravity_adapter.py"],
        },
    }

    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {"status": "SUCCESS", "response": "", "structured_output": payload}
            ),
            stderr="",
        )

    # Mutation-authorized policy
    adapter_mut = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=repo / ".git" / "aios" / "handoff.json",
        execution_policy=NativeExecutionPolicy(authorizes_mutation=True),
    )

    # 1. PRIMARY with mutation authorized
    adapter_mut.execute(task=task, run=run)
    cmd_primary = calls[-1]
    assert cmd_primary[cmd_primary.index("--mode") + 1] == "accept-edits"
    assert "--dangerously-skip-permissions" in cmd_primary
    assert "--disable-slash-commands" in cmd_primary
    assert cmd_primary[cmd_primary.index("--json-schema") + 1] == str(RESULT_PACKAGE_SCHEMA_PATH)

    # 2. REMEDIATION CODE_FIX with mutation authorized
    adapter_mut.execute_remediation(execution=remediation_exec)
    cmd_remediation = calls[-1]
    assert cmd_remediation[cmd_remediation.index("--mode") + 1] == "accept-edits"
    assert "--dangerously-skip-permissions" in cmd_remediation
    assert "--disable-slash-commands" in cmd_remediation
    assert cmd_remediation[cmd_remediation.index("--json-schema") + 1] == str(REMEDIATION_RESULT_PACKAGE_SCHEMA_PATH)

    # 3. REPAIR CODE_FIX with mutation authorized
    adapter_mut.execute_repair(execution=repair_exec_code_fix)
    cmd_repair_cf = calls[-1]
    assert cmd_repair_cf[cmd_repair_cf.index("--mode") + 1] == "accept-edits"
    assert "--dangerously-skip-permissions" in cmd_repair_cf
    assert "--disable-slash-commands" in cmd_repair_cf
    assert cmd_repair_cf[cmd_repair_cf.index("--json-schema") + 1] == str(REPAIR_RESULT_PACKAGE_SCHEMA_PATH)

    # 4. REPAIR CONTINUE_IMPLEMENTATION with mutation authorized
    adapter_mut.execute_repair(execution=repair_exec_continue)
    cmd_repair_cont = calls[-1]
    assert cmd_repair_cont[cmd_repair_cont.index("--mode") + 1] == "accept-edits"
    assert "--dangerously-skip-permissions" in cmd_repair_cont
    assert "--disable-slash-commands" in cmd_repair_cont
    assert cmd_repair_cont[cmd_repair_cont.index("--json-schema") + 1] == str(REPAIR_RESULT_PACKAGE_SCHEMA_PATH)

    # Zero-mutation policy across operations
    adapter_ro = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=repo / ".git" / "aios" / "handoff.json",
        execution_policy=NativeExecutionPolicy(authorizes_mutation=False),
    )

    # Zero-mutation PRIMARY
    adapter_ro.execute(task=task, run=run)
    cmd_ro_primary = calls[-1]
    assert "--mode" not in cmd_ro_primary
    assert "plan" not in cmd_ro_primary
    assert "--dangerously-skip-permissions" not in cmd_ro_primary
    assert "--disable-slash-commands" in cmd_ro_primary

    # Zero-mutation REMEDIATION
    adapter_ro.execute_remediation(execution=remediation_exec)
    cmd_ro_remediation = calls[-1]
    assert "--mode" not in cmd_ro_remediation
    assert "plan" not in cmd_ro_remediation
    assert "--dangerously-skip-permissions" not in cmd_ro_remediation
    assert "--disable-slash-commands" in cmd_ro_remediation

    # Zero-mutation REPAIR (FINALIZE_CANDIDATE)
    adapter_ro.execute_repair(
        execution={
            "run": run,
            "failed_head_sha": run.base_sha,
            "repair": {
                "action": "FINALIZE_CANDIDATE",
                "instructions": ["Return package."],
                "modification_scope": [],
            },
        }
    )
    cmd_ro_repair = calls[-1]
    assert "--mode" not in cmd_ro_repair
    assert "plan" not in cmd_ro_repair
    assert "--dangerously-skip-permissions" not in cmd_ro_repair
    assert "--disable-slash-commands" in cmd_ro_repair


# ============================================================================
# TASK-137: Deterministic Native Headless Print-Mode Finish Terminal Contract
# ============================================================================


def _assert_valid_terminal_contract(
    instruction: str,
    operation: str,
    repair_action: str | None = None,
) -> None:
    """Validate that native instruction strictly satisfies the hardened finish terminal contract."""
    # 1. No-background rule: explicitly forbid starting, detaching, or leaving background tasks,
    # asynchronous manage_task work, or long-lived servers or watchers.
    assert "background tasks" in instruction
    assert "manage_task" in instruction
    assert "servers or watchers" in instruction

    # 2. No delegated subagent rule: explicitly forbid delegated implementation subagents or invoke_subagent.
    assert "delegated implementation subagents" in instruction
    assert "invoke_subagent" in instruction

    # 3. Synchronous-command rule: every command must be bounded and complete synchronously before proceeding.
    assert "complete synchronously" in instruction
    assert "bounded" in instruction
    assert "merely for ceremony" in instruction

    # 4. No-terminal-while-active rule: forbid completion while any tool/subagent/background work remains active;
    # deterministically wait/join or fail closed.
    assert "while any tool, subagent, or background work remains active" in instruction
    assert "fail closed rather than fabricate completion" in instruction

    # 5. Required commit-before-terminal rule: complete authorized repository work and required commit first;
    # zero-mutation actions must not create a commit merely to satisfy terminal mechanics.
    assert "required commit completion first" in instruction
    assert "zero-mutation actions must not create a commit merely to satisfy terminal mechanics" in instruction

    # 6. Actual-final-HEAD binding: result.head_sha must bind to actual final Git HEAD.
    assert "Bind result.head_sha to actual final Git HEAD" in instruction
    assert "Obtain actual final Git HEAD" in instruction

    # 7. Builtin finish exactly once: invoke builtin finish tool exactly once as only successful terminal action.
    assert "builtin finish tool exactly once" in instruction
    assert "only successful terminal action" in instruction
    assert "satisfying the supplied response schema" in instruction

    # 8. Ban on prose / second terminal responses: conversational completion prose, markdown, summaries,
    # synthetic terminal notifications, and any second terminal response before or after finish are prohibited.
    assert "Conversational completion prose" in instruction
    assert "markdown" in instruction
    assert "summaries" in instruction
    assert "synthetic terminal notifications" in instruction
    assert "second terminal response" in instruction
    assert "return the structural ResultPackage as the only response" not in instruction
    assert "Return one structural ResultPackage as the only response" not in instruction
    assert "Return one structural ResultPackage for the complete original TASK contract as the only response" not in instruction

    # Universal ban on push
    assert "do not push" in instruction or "Do not push" in instruction

    # Operation-specific contracts
    if operation == "PRIMARY":
        assert "Complete all authorized implementation work and required commit completion first" in instruction
        assert "Root evidence and every claim.evidence must be empty" in instruction
        assert "Runtime constructs canonical EVIDENCE" in instruction
        assert "Every claim.satisfies entry must be a known TASK acceptance ID" in instruction
    elif operation == "REMEDIATION":
        assert "Complete all authorized remediation work and required commit completion first" in instruction
        assert "For CODE_FIX, commit the permitted remediation delta before invoking finish" in instruction
        assert "for EVIDENCE_ONLY, do not create a code commit" in instruction
        assert "Root evidence, result.claims, and result.unresolved must be empty" in instruction
    elif operation == "REPAIR":
        assert "Complete all authorized repair work and required commit completion first" in instruction
        assert "For CODE_FIX and CONTINUE_IMPLEMENTATION, commit the final permitted repository state" in instruction
        assert "for NO_CHANGE and FINALIZE_CANDIDATE, do not create a code commit" in instruction
        assert "Do not create or restart a fresh PRIMARY lineage" in instruction
        assert "retry this admitted continuation" in instruction
        assert "reroute or fall back to another Executor" in instruction
        assert "widen scope" in instruction
        if repair_action == "CODE_FIX":
            assert "CODE_FIX authorizes mutation only to correct an established defect" in instruction
        elif repair_action == "CONTINUE_IMPLEMENTATION":
            assert "CONTINUE_IMPLEMENTATION authorizes mutation to resume the unfinished original TASK implementation" in instruction
        elif repair_action == "FINALIZE_CANDIDATE":
            assert "FINALIZE_CANDIDATE authorizes no repository mutation" in instruction
            assert "bind result.head_sha to the unchanged failed_head_sha" in instruction
            assert "finish-exactly-once terminal contract" in instruction
        elif repair_action == "NO_CHANGE":
            assert "NO_CHANGE authorizes no repository mutation" in instruction


def test_task137_headless_print_mode_contract_shared_across_all_operations(
    tmp_path: Path,
) -> None:
    """AC1: Shared deterministic headless print-mode contract is present across all native operations."""
    handoff_path = tmp_path / "handoff.json"
    primary_inst = _native_instruction(operation="PRIMARY", handoff_path=handoff_path)
    remediation_inst = _native_instruction(operation="REMEDIATION", handoff_path=handoff_path)
    repair_inst = _native_instruction(operation="REPAIR", handoff_path=handoff_path)

    # HEADLESS_PRINT_MODE_CONTRACT must be present verbatim in all three operations
    assert HEADLESS_PRINT_MODE_CONTRACT in primary_inst
    assert HEADLESS_PRINT_MODE_CONTRACT in remediation_inst
    assert HEADLESS_PRINT_MODE_CONTRACT in repair_inst

    # All three expose identical native_instruction alias
    assert native_instruction(operation="PRIMARY", handoff_path=handoff_path) == primary_inst
    assert native_instruction(operation="REMEDIATION", handoff_path=handoff_path) == remediation_inst
    assert native_instruction(operation="REPAIR", handoff_path=handoff_path) == repair_inst


def test_task137_primary_instruction_terminal_contract(tmp_path: Path) -> None:
    """AC1 & AC2: PRIMARY instruction satisfies the hardened finish terminal contract."""
    handoff_path = tmp_path / "handoff.json"
    instruction = _native_instruction(operation="PRIMARY", handoff_path=handoff_path)

    _assert_valid_terminal_contract(instruction, "PRIMARY")
    assert str(handoff_path) in instruction
    assert "Runtime owns canonical verification" in instruction


def test_task137_remediation_instruction_terminal_contract(tmp_path: Path) -> None:
    """AC1 & AC2: REMEDIATION instruction satisfies the hardened finish terminal contract."""
    handoff_path = tmp_path / "remediation_handoff.json"
    instruction = _native_instruction(operation="REMEDIATION", handoff_path=handoff_path)

    _assert_valid_terminal_contract(instruction, "REMEDIATION")
    assert str(handoff_path) in instruction
    assert "Runtime owns affected verification" in instruction
    assert "remediation.modification_scope" in instruction


@pytest.mark.parametrize(
    "repair_action",
    ["CODE_FIX", "CONTINUE_IMPLEMENTATION", "FINALIZE_CANDIDATE", "NO_CHANGE"],
)
def test_task137_repair_instruction_terminal_contract_all_actions(
    tmp_path: Path, repair_action: str
) -> None:
    """AC1, AC2, AC3: REPAIR instruction satisfies terminal contract for all 4 repair actions."""
    handoff_path = tmp_path / "repair_handoff.json"
    instruction = _native_instruction(operation="REPAIR", handoff_path=handoff_path)

    _assert_valid_terminal_contract(instruction, "REPAIR", repair_action=repair_action)
    assert str(handoff_path) in instruction
    assert "Runtime owns complete original TASK verification" in instruction


def test_task137_terminal_contract_fails_if_weakened(tmp_path: Path) -> None:
    """AC5: Regressions reject weakened terminal contracts that omit background, synchronous, finish, commit, or final-HEAD rules."""
    handoff_path = tmp_path / "handoff.json"
    canonical = _native_instruction(operation="PRIMARY", handoff_path=handoff_path)

    # 0. Canonical instruction passes contract validation
    _assert_valid_terminal_contract(canonical, "PRIMARY")

    # 1. Omitting no-background rule fails
    no_background = canonical.replace(
        "Do not start, detach, or leave background tasks, asynchronous manage_task work, or long-lived servers or watchers.",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_background, "PRIMARY")

    # 2. Omitting no delegated subagent rule fails
    no_subagent = canonical.replace(
        "Do not invoke delegated implementation subagents or invoke_subagent-style delegation.",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_subagent, "PRIMARY")

    # 3. Omitting synchronous-command rule fails
    no_sync = canonical.replace(
        "Every command used during execution must be bounded and complete synchronously before the agent proceeds.",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_sync, "PRIMARY")

    # 4. Omitting no-terminal-while-active rule fails
    no_active = canonical.replace(
        "Do not complete execution or invoke finish while any tool, subagent, or background work remains active.",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_active, "PRIMARY")

    # 5. Omitting required commit-before-terminal rule fails
    no_commit = canonical.replace(
        "Complete all authorized implementation work and required commit completion first; ",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_commit, "PRIMARY")

    # 6. Omitting actual-final-HEAD binding fails
    no_head = canonical.replace(
        "Bind result.head_sha to actual final Git HEAD.",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_head, "PRIMARY")

    # 7. Omitting builtin finish exactly once fails
    no_finish = canonical.replace(
        "builtin finish tool exactly once",
        "output model response",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_finish, "PRIMARY")

    # 8. Omitting ban on prose / second terminal responses fails
    no_prose_ban = canonical.replace(
        "Conversational completion prose, markdown, summaries, synthetic terminal notifications, and any second terminal response before or after finish are prohibited.",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(no_prose_ban, "PRIMARY")

    # 9. Legacy generic prose replacement fails
    legacy_weakened = canonical.replace(
        "Obtain actual final Git HEAD, then invoke the builtin finish tool exactly once satisfying the supplied response schema as the only successful terminal action.",
        "Obtain final Git HEAD, and return the structural ResultPackage as the only response.",
    )
    with pytest.raises(AssertionError):
        _assert_valid_terminal_contract(legacy_weakened, "PRIMARY")


def test_task137_adapter_rejects_status_error_even_with_structured_output(
    tmp_path: Path,
) -> None:
    """AC4: Adapter remains single-invocation and rejects terminal status ERROR even when structured_output is present."""
    task, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    envelope = {
        "status": "ERROR",
        "error": "background task still running at boundary",
        "structured_output": payload,
    }

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(envelope).encode("utf-8"),
            stderr=b"",
        )

    adapter = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=repo / ".git" / "aios" / "handoff.json",
    )
    with pytest.raises(
        AntigravityExecutionError,
        match="Antigravity CLI terminal status is ERROR: background task still running at boundary",
    ):
        adapter.execute(task=task, run=run)

    # Must remain strictly single-invocation: no retry, no fallback, no reroute
    assert len(calls) == 1


def test_task137_repair_actions_retain_mutation_authority_and_no_background_invariant(
    tmp_path: Path,
) -> None:
    """AC3: REPAIR CONTINUE_IMPLEMENTATION and CODE_FIX retain mutation authority while NO_CHANGE and FINALIZE_CANDIDATE remain read-only, and all obey the no-background terminal invariant."""
    _, run, _, _ = make_execution()
    repo = tmp_path.resolve()
    handoff_path = repo / "repair-handoff.json"
    calls = []
    payload = successful_output(run.run_id)
    payload["result"]["head_sha"] = run.base_sha
    payload["result"]["changed_files"] = []
    payload["result"]["claims"][0]["evidence"] = []
    payload["evidence"] = []

    def runner(command, **kwargs):
        calls.append(command)
        instruction = command[command.index("--print") + 1]
        _assert_valid_terminal_contract(instruction, "REPAIR")
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(
                {"status": "SUCCESS", "response": "", "structured_output": payload}
            ),
            stderr="",
        )

    # 1. CODE_FIX with mutation authorized policy
    adapter_mut = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=True),
    )
    adapter_mut.execute_repair(
        execution={
            "run": run,
            "root_base_sha": run.base_sha,
            "repair": {
                "action": "CODE_FIX",
                "instructions": ["Fix defect."],
                "modification_scope": ["src/fix.py"],
            },
        }
    )
    cmd_cf = calls[-1]
    assert cmd_cf[cmd_cf.index("--mode") + 1] == "accept-edits"
    assert "--dangerously-skip-permissions" in cmd_cf

    # 2. CONTINUE_IMPLEMENTATION with mutation authorized policy
    adapter_mut.execute_repair(
        execution={
            "run": run,
            "failed_run_id": "RUN-137-000",
            "failed_head_sha": run.base_sha,
            "root_base_sha": run.base_sha,
            "repair": {
                "action": "CONTINUE_IMPLEMENTATION",
                "instructions": ["Resume work."],
                "modification_scope": ["src/fix.py"],
            },
        }
    )
    cmd_cont = calls[-1]
    assert cmd_cont[cmd_cont.index("--mode") + 1] == "accept-edits"
    assert "--dangerously-skip-permissions" in cmd_cont

    # 3. NO_CHANGE with read-only policy
    adapter_ro = AntigravityAdapter(
        runner=runner,
        repo=repo,
        handoff_path=handoff_path,
        execution_policy=NativeExecutionPolicy(authorizes_mutation=False),
    )
    adapter_ro.execute_repair(
        execution={
            "run": run,
            "root_base_sha": run.base_sha,
            "repair": {
                "action": "NO_CHANGE",
                "instructions": ["Keep unchanged."],
                "modification_scope": [],
            },
        }
    )
    cmd_nc = calls[-1]
    assert "--mode" not in cmd_nc
    assert "--dangerously-skip-permissions" not in cmd_nc

    # 4. FINALIZE_CANDIDATE with read-only policy
    adapter_ro.execute_repair(
        execution={
            "run": run,
            "failed_head_sha": run.base_sha,
            "repair": {
                "action": "FINALIZE_CANDIDATE",
                "instructions": ["Finalize package."],
                "modification_scope": [],
            },
        }
    )
    cmd_fc = calls[-1]
    assert "--mode" not in cmd_fc
    assert "--dangerously-skip-permissions" not in cmd_fc

    # Total 4 calls, all obey terminal invariant and contract
    assert len(calls) == 4
