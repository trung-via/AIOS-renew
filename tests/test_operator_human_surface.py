# Tests for Unified Human Surface and continue command.
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import aios_renew.human_surface as human_surface_module
from aios_renew.human_surface import (
    HumanSurfaceResult as DirectHumanSurfaceResult,
    continue_task as direct_continue_task,
)
import aios_renew.operator as operator_module
from aios_renew.operator import (
    HumanSurfaceResult,
    OperatorError,
    continue_task,
    load_task,
    runtime_paths,
)
from tests.operator_test_support import (
    TASK_SOURCE,
    _runtime_bytes,
    git,
    make_repo,
    publish_upstream,
)

def _human_observation(
    action: str, **facts,
) -> operator_module.UnifiedStateObservation:
    task_id = facts.pop("task_id", "TASK-101")
    revision = facts.pop("revision", 1)
    return operator_module.UnifiedStateObservation(
        task_id, revision, "TEST", action, **facts
    )

@pytest.mark.parametrize(
    ("action", "disposition", "authority"),
    [
        ("SEMANTIC_REVIEW", "EXTERNAL_AUTHORITY_REQUIRED", "REVIEWER"),
        ("AUTHOR_REMEDIATION", "EXTERNAL_AUTHORITY_REQUIRED", "BRAIN"),
        ("AUTHOR_REPAIR", "EXTERNAL_AUTHORITY_REQUIRED", "BRAIN"),
        ("PUBLICATION", "EXTERNAL_AUTHORITY_REQUIRED", "PUBLISHER"),
        ("WAIT", "NO_ACTION", "NONE"),
        ("DONE", "NO_ACTION", "NONE"),
        ("NONE", "BLOCKED", "NONE"),
    ],
)
def test_human_surface_non_delegated_actions_are_bounded_and_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    disposition: str,
    authority: str,
) -> None:
    repo = make_repo(tmp_path)
    blocker = {"code": "TASK_086_BLOCKER"} if action == "NONE" else None
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: _human_observation(
            action,
            run_id="RUN-101-001",
            blocker=blocker,
        ),
    )
    forbidden = lambda *_args, **_kwargs: pytest.fail("operation was delegated")
    monkeypatch.setattr(operator_module, "run_task", forbidden)
    monkeypatch.setattr(operator_module, "run_remediation", forbidden)
    monkeypatch.setattr(operator_module, "run_repair", forbidden)
    monkeypatch.setattr(operator_module, "retry_transport", forbidden)
    monkeypatch.setattr(operator_module, "recover_primary", forbidden)

    outcome, exit_code = operator_module.continue_task(
        "TASK-101", executor="antigravity", repo=repo
    )

    assert exit_code == 0
    assert outcome is not None
    payload = outcome.as_dict()
    assert payload["format"] == "AIOS_HUMAN_SURFACE"
    assert payload["version"] == 1
    assert payload["observed_next_action"] == action
    assert payload["disposition"] == disposition
    assert payload["authority"] == authority
    assert payload["delegated_operation"] is None
    assert payload["selectors"]["run_id"] == "RUN-101-001"
    assert payload["blocker"] == blocker


@pytest.mark.parametrize(
    "action", ["EXECUTE_PRIMARY", "EXECUTE_REMEDIATION", "EXECUTE_REPAIR"]
)
def test_human_surface_requires_explicit_executor_before_coding_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    repo = make_repo(tmp_path)
    correction = {"action": "CODE_FIX"} if action == "EXECUTE_REPAIR" else None
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: _human_observation(
            action, correction=correction
        ),
    )
    forbidden = lambda *_args, **_kwargs: pytest.fail("operation was delegated")
    monkeypatch.setattr(operator_module, "run_task", forbidden)
    monkeypatch.setattr(operator_module, "run_remediation", forbidden)
    monkeypatch.setattr(operator_module, "run_repair", forbidden)

    outcome, exit_code = operator_module.continue_task("TASK-101", repo=repo)

    assert exit_code == 0
    assert outcome is not None
    assert outcome.disposition == "EXECUTOR_REQUIRED"
    assert outcome.authority == "HUMAN"
    assert outcome.executor_required is True
    assert outcome.executor_supplied is False


def test_human_surface_delegates_exact_remediation_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    correction_sha = "a" * 40
    observation = _human_observation(
        "EXECUTE_REMEDIATION",
        run_id="RUN-101-001",
        source_run_id="RUN-101-001",
        finding_id="F1",
        correction_sha=correction_sha,
    )
    monkeypatch.setattr(
        operator_module, "observe_unified_state", lambda *_args, **_kwargs: observation
    )
    calls = []

    def remediation(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(run_id="RUN-101-002", head_sha="b" * 40)

    monkeypatch.setattr(operator_module, "run_remediation", remediation)

    outcome, exit_code = operator_module.continue_task(
        "TASK-101", executor="codex", repo=repo
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][1]["source_run_id"] == "RUN-101-001"
    assert calls[0][1]["finding_id"] == "F1"
    assert calls[0][1]["approved_remediation_sha"] == correction_sha
    assert outcome is not None
    assert outcome.delegated_operation == "REMEDIATION"
    assert outcome.resulting_run_id == "RUN-101-002"


def test_human_surface_elides_executor_only_for_ready_no_change_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    repair_sha = "c" * 40
    repair = {
        "repair_id": "REPAIR-101-001",
        "failed_run_id": "RUN-101-001",
        "failed_head_sha": "d" * 40,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "NO_CHANGE",
        "modification_scope": [],
        "instructions": ["Reuse the eligible verification state."],
        "constraints": [],
    }
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: _human_observation(
            "EXECUTE_REPAIR",
            run_id="RUN-101-001",
            failed_run_id="RUN-101-001",
            failed_head_sha="d" * 40,
            correction_sha=repair_sha,
            correction={
                "action": "NO_CHANGE",
                "status": "READY",
                "executor_required": False,
            },
            correction_document=repair,
        ),
    )
    calls = []

    def execute_repair(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(run_id="RUN-101-002", head_sha="d" * 40)

    monkeypatch.setattr(operator_module, "run_repair", execute_repair)

    outcome, exit_code = operator_module.continue_task("TASK-101", repo=repo)

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][1]["executor"] is None
    assert calls[0][1]["repair"] == repair
    assert calls[0][1]["required_repair_sha"] == repair_sha
    assert outcome is not None
    assert outcome.executor_required is False
    assert outcome.executor_supplied is False
    assert outcome.delegated_operation == "REPAIR"


def test_human_surface_requires_executor_for_non_reusable_no_change_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    before = _runtime_bytes(repo)
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: _human_observation(
            "EXECUTE_REPAIR",
            failed_run_id="RUN-101-001",
            correction_sha="c" * 40,
            correction={
                "action": "NO_CHANGE",
                "status": "READY",
                "executor_required": True,
            },
            correction_document={"action": "NO_CHANGE"},
        ),
    )
    monkeypatch.setattr(
        operator_module,
        "run_repair",
        lambda *_args, **_kwargs: pytest.fail("REPAIR was delegated"),
    )

    outcome, exit_code = operator_module.continue_task("TASK-101", repo=repo)

    assert exit_code == 0
    assert outcome is not None
    assert outcome.disposition == "EXECUTOR_REQUIRED"
    assert outcome.authority == "HUMAN"
    assert outcome.executor_required is True
    assert list(state.admission_failures.glob("*.json")) == []
    assert _runtime_bytes(repo) == before


def test_human_surface_continue_implementation_requires_executor_and_delegates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    repair_sha = "c" * 40
    failed_head = "d" * 40
    repair = {
        "repair_id": "REPAIR-101-CONTINUE",
        "failed_run_id": "RUN-101-001",
        "failed_head_sha": failed_head,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Continue the unfinished implementation."],
        "constraints": [],
    }
    observation = _human_observation(
        "EXECUTE_REPAIR",
        run_id="RUN-101-001",
        failed_run_id="RUN-101-001",
        failed_head_sha=failed_head,
        correction_sha=repair_sha,
        correction={
            "action": "CONTINUE_IMPLEMENTATION",
            "status": "READY",
            # The semantic action itself must fail closed even if an observed
            # requirement flag is malformed or conflicting.
            "executor_required": False,
        },
        correction_document=repair,
    )
    monkeypatch.setattr(
        operator_module, "observe_unified_state", lambda *_args, **_kwargs: observation
    )
    calls = []

    def execute_repair(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(run_id="RUN-101-002", head_sha="e" * 40)

    monkeypatch.setattr(operator_module, "run_repair", execute_repair)

    required, exit_code = operator_module.continue_task("TASK-101", repo=repo)

    assert exit_code == 0
    assert required is not None
    assert required.disposition == "EXECUTOR_REQUIRED"
    assert calls == []

    outcome, exit_code = operator_module.continue_task(
        "TASK-101", executor="codex", repo=repo
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][1]["executor"] == "codex"
    assert calls[0][0] == ("RUN-101-001",)
    assert calls[0][1]["required_repair_sha"] == repair_sha
    assert calls[0][1]["repair"] == repair
    assert outcome is not None
    assert outcome.delegated_operation == "REPAIR"


@pytest.mark.parametrize("admitted", [False, True])
def test_continue_cli_bounds_delegated_repair_failure_without_second_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    admitted: bool,
) -> None:
    repo = make_repo(tmp_path)
    state = runtime_paths(repo)
    repair_sha = "c" * 40
    repair = {
        "repair_id": "REPAIR-101-001",
        "failed_run_id": "RUN-101-001",
        "failed_head_sha": "d" * 40,
        "task": {"id": "TASK-101", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["OUTPUT.txt"],
        "instructions": ["Apply the repair."],
        "constraints": [],
    }
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: _human_observation(
            "EXECUTE_REPAIR",
            run_id="RUN-101-001",
            failed_run_id="RUN-101-001",
            failed_head_sha="d" * 40,
            correction_sha=repair_sha,
            correction={"action": "CODE_FIX", "executor_required": True},
            correction_document=repair,
        ),
    )
    calls = []

    def fail_once(*_args, **_kwargs):
        calls.append("REPAIR")
        artifact = (
            state.failures / "RUN-101-002.json"
            if admitted
            else state.admission_failures / "REPAIR-test.json"
        )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"canonical":true}', encoding="utf-8")
        raise OperatorError("canonical operation failed")

    monkeypatch.setattr(operator_module, "run_repair", fail_once)

    exit_code = operator_module.main(
        ["continue", "TASK-101", "--executor", "codex", "--repo", str(repo)]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 1
    assert calls == ["REPAIR"]
    assert captured.err == ""
    assert payload["format"] == "AIOS_HUMAN_SURFACE"
    assert payload["version"] == 1
    assert payload["disposition"] == "DELEGATED"
    assert payload["delegated_operation"] == "REPAIR"
    assert payload["blocker"] == {"code": "DELEGATED_OPERATION_FAILED"}
    artifacts = list(state.failures.glob("*.json")) + list(
        state.admission_failures.glob("*.json")
    )
    assert len(artifacts) == 1
    assert artifacts[0].read_text(encoding="utf-8") == '{"canonical":true}'


@pytest.mark.parametrize("action", ["RETRY_TRANSPORT", "RECOVER_PRIMARY"])
def test_human_surface_non_coding_delegation_uses_exact_run_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    repo = make_repo(tmp_path)
    run_id = "RUN-101-001"
    observation = _human_observation(
        action,
        run_id=run_id,
        source_run_id=run_id if action == "RECOVER_PRIMARY" else None,
        candidate_sha="a" * 40,
    )
    monkeypatch.setattr(
        operator_module, "observe_unified_state", lambda *_args, **_kwargs: observation
    )
    calls = []
    forbidden = lambda *_args, **_kwargs: pytest.fail("coding operation was delegated")
    monkeypatch.setattr(operator_module, "run_task", forbidden)
    monkeypatch.setattr(operator_module, "run_remediation", forbidden)
    monkeypatch.setattr(operator_module, "run_repair", forbidden)
    if action == "RETRY_TRANSPORT":
        monkeypatch.setattr(
            operator_module,
            "retry_transport",
            lambda selected, **_kwargs: calls.append(selected),
        )
        monkeypatch.setattr(operator_module, "recover_primary", forbidden)
    else:
        monkeypatch.setattr(operator_module, "retry_transport", forbidden)

        def recover(selected, **_kwargs):
            calls.append(selected)
            return SimpleNamespace(run_id="RUN-101-002", head_sha="b" * 40)

        monkeypatch.setattr(operator_module, "recover_primary", recover)

    outcome, exit_code = operator_module.continue_task(
        "TASK-101", executor="antigravity", repo=repo
    )

    assert exit_code == 0
    assert calls == [run_id]
    assert outcome is not None
    assert outcome.disposition == "DELEGATED"
    assert outcome.executor_required is False
    assert outcome.executor_supplied is True
    assert outcome.delegated_operation == (
        "TRANSPORT" if action == "RETRY_TRANSPORT" else "RECOVER_PRIMARY"
    )


def test_human_surface_primary_restart_reenters_same_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    observations = []
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: observations.append("observed")
        or _human_observation("EXECUTE_PRIMARY"),
    )
    captured = []

    def preflight(*args, **kwargs):
        captured.append(kwargs["argv"])
        return operator_module.PreflightResult(restart_code=17)

    monkeypatch.setattr(operator_module, "_preflight_primary_admission", preflight)
    monkeypatch.setattr(
        operator_module,
        "run_task",
        lambda *_args, **_kwargs: pytest.fail("stale PRIMARY was executed"),
    )
    argv = [
        "continue", "TASK-101", "--executor", "codex", "--repo", str(repo)
    ]

    outcome, exit_code = operator_module.continue_task(
        "TASK-101", executor="codex", repo=repo, argv=argv
    )

    assert outcome is None
    assert exit_code == 17
    assert observations == ["observed"]
    assert captured == [argv]


def test_continue_cli_emits_one_versioned_human_surface_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: _human_observation(
            "SEMANTIC_REVIEW",
            run_id="RUN-101-001",
            review_id="REVIEW-101-001",
            candidate_sha="a" * 40,
        ),
    )

    exit_code = operator_module.main(
        [
            "continue",
            "TASK-101",
            "--executor",
            "codex",
            "--repo",
            str(repo),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "AIOS_HUMAN_SURFACE"
    assert payload["version"] == 1
    assert payload["task"] == {"id": "TASK-101", "revision": 1}
    assert payload["observed_next_action"] == "SEMANTIC_REVIEW"
    assert payload["disposition"] == "EXTERNAL_AUTHORITY_REQUIRED"
    assert payload["authority"] == "REVIEWER"
    assert payload["executor"] == {
        "required": False,
        "supplied": True,
        "identity": "codex",
    }

@pytest.mark.parametrize(
    "task_id", ["", "TASK/102", "TASK\\102", "../TASK-102"]
)
def test_continue_rejects_invalid_task_before_pre_resolution_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_id: str,
) -> None:
    repo = make_repo(tmp_path, task_source=None)
    before = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(
        operator_module,
        "_preflight_primary_sync",
        lambda *_args, **_kwargs: pytest.fail("invalid TASK triggered sync"),
    )
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: pytest.fail("invalid TASK was observed"),
    )

    with pytest.raises(OperatorError, match="invalid TASK id"):
        operator_module.continue_task(task_id, repo=repo)

    assert git(repo, "rev-parse", "HEAD") == before
    assert not list(operator_module._runtime_paths_readonly(repo).runs.glob("*.json"))


def test_continue_existing_safe_non_prefixed_task_never_syncs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "LEGACY-101"
    repo = make_repo(tmp_path, task_source=None)
    task_path = repo / ".ai" / "tasks" / f"{task_id}.yaml"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        TASK_SOURCE.replace("TASK-101", task_id), encoding="utf-8"
    )
    monkeypatch.setattr(
        operator_module,
        "_preflight_primary_sync",
        lambda *_args, **_kwargs: pytest.fail("safe local TASK triggered pre-sync"),
    )
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: _human_observation("NONE", task_id=task_id),
    )

    outcome, exit_code = operator_module.continue_task(task_id, repo=repo)

    assert load_task(repo, task_id).task_id == task_id
    assert exit_code == 0
    assert outcome is not None
    assert outcome.task_id == task_id


@pytest.mark.parametrize(
    "action", ["EXECUTE_REMEDIATION", "SEMANTIC_REVIEW", "WAIT", "NONE"]
)
def test_continue_existing_task_never_uses_pre_resolution_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setattr(
        operator_module,
        "_preflight_primary_sync",
        lambda *_args, **_kwargs: pytest.fail("existing TASK triggered pre-sync"),
    )
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: _human_observation(action),
    )
    monkeypatch.setattr(
        operator_module,
        "run_remediation",
        lambda *_args, **_kwargs: pytest.fail("correction was delegated"),
    )

    outcome, exit_code = operator_module.continue_task("TASK-101", repo=repo)

    assert exit_code == 0
    assert outcome is not None
    assert outcome.next_action == action


def test_continue_existing_malformed_task_does_not_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path, task_source="task_id: TASK-101\n")
    monkeypatch.setattr(
        operator_module,
        "_preflight_primary_sync",
        lambda *_args, **_kwargs: pytest.fail("existing malformed TASK triggered sync"),
    )

    with pytest.raises(OperatorError, match="invalid TASK TASK-101"):
        operator_module.continue_task("TASK-101", repo=repo)


@pytest.mark.parametrize("task_id", ["TASK-102", "LEGACY-102"])
def test_continue_missing_upstream_task_syncs_then_reenters_fresh_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task_id: str,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path, task_source=None)
    task_source = TASK_SOURCE.replace("TASK-101", task_id)
    published_sha = publish_upstream(
        repo, {f".ai/tasks/{task_id}.yaml": task_source}, f"publish {task_id}"
    )
    argv = ["continue", task_id, "--executor", "codex", "--repo", str(repo)]
    events = []
    child_results = []
    git_calls = []
    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    def observe(*_args, **_kwargs):
        events.append(("observe", git(repo, "rev-parse", "HEAD")))
        assert load_task(repo, task_id).task_id == task_id
        return _human_observation("EXECUTE_PRIMARY", task_id=task_id)

    def execute(*_args, **kwargs):
        events.append(("execute", kwargs["preflight_sha"]))
        return SimpleNamespace(run_id="RUN-102-001", head_sha=published_sha)

    def restart(root, *, argv=None, runner=subprocess.run):
        events.append(("restart", tuple(argv or ())))
        monkeypatch.setenv("AIOS_RESTART_ATTEMPTED", "1")
        child_results.append(
            operator_module.continue_task(
                task_id, executor="codex", repo=root, argv=argv
            )
        )
        return child_results[-1][1]

    monkeypatch.setattr(operator_module, "_git", recording_git)
    monkeypatch.setattr(operator_module, "observe_unified_state", observe)
    monkeypatch.setattr(operator_module, "run_task", execute)
    monkeypatch.setattr(operator_module, "_restart_primary_invocation", restart)

    outcome, exit_code = operator_module.continue_task(
        task_id, executor="codex", repo=repo, argv=argv
    )

    assert outcome is None
    assert exit_code == 0
    assert git(repo, "rev-parse", "HEAD") == published_sha
    assert events == [
        ("restart", tuple(argv)),
        ("observe", published_sha),
        ("execute", published_sha),
    ]
    assert child_results[0][0] is not None
    assert child_results[0][0].delegated_operation == "PRIMARY"
    ff_calls = [args for args in git_calls if args[:2] == ("merge", "--ff-only")]
    assert len(ff_calls) == 1


@pytest.mark.parametrize(
    ("action", "executor", "disposition"),
    [
        ("EXECUTE_PRIMARY", None, "EXECUTOR_REQUIRED"),
        ("SEMANTIC_REVIEW", "codex", "EXTERNAL_AUTHORITY_REQUIRED"),
        ("AUTHOR_REMEDIATION", "codex", "EXTERNAL_AUTHORITY_REQUIRED"),
        ("PUBLICATION", "codex", "EXTERNAL_AUTHORITY_REQUIRED"),
        ("WAIT", "codex", "NO_ACTION"),
        ("DONE", "codex", "NO_ACTION"),
        ("NONE", "codex", "BLOCKED"),
    ],
)
def test_continue_restart_honors_fresh_zero_or_executor_required_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    executor: str | None,
    disposition: str,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path, task_source=None)
    task_source = TASK_SOURCE.replace("TASK-101", "TASK-102")
    publish_upstream(repo, {".ai/tasks/TASK-102.yaml": task_source})
    argv = ["continue", "TASK-102"]
    if executor is not None:
        argv.extend(["--executor", executor])
    argv.extend(["--repo", str(repo)])
    child_results = []
    observations = []
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: observations.append(action)
        or _human_observation(action, task_id="TASK-102"),
    )
    forbidden = lambda *_args, **_kwargs: pytest.fail("fresh state was bypassed")
    monkeypatch.setattr(operator_module, "run_task", forbidden)
    monkeypatch.setattr(operator_module, "run_remediation", forbidden)
    monkeypatch.setattr(operator_module, "run_repair", forbidden)

    def restart(root, *, argv=None, runner=subprocess.run):
        monkeypatch.setenv("AIOS_RESTART_ATTEMPTED", "1")
        child_results.append(
            operator_module.continue_task(
                "TASK-102", executor=executor, repo=root, argv=argv
            )
        )
        return child_results[-1][1]

    monkeypatch.setattr(operator_module, "_restart_primary_invocation", restart)

    parent, exit_code = operator_module.continue_task(
        "TASK-102", executor=executor, repo=repo, argv=argv
    )

    assert parent is None
    assert exit_code == 0
    assert observations == [action]
    child, child_code = child_results[0]
    assert child_code == 0
    assert child is not None
    assert child.disposition == disposition


@pytest.mark.parametrize(
    "state",
    [
        "dirty",
        "detached",
        "non-main",
        "missing-upstream",
        "ambiguous-upstream",
        "ahead",
        "diverged",
        "fetch-failure",
    ],
)
def test_continue_missing_task_preserves_sync_fail_closed_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path, task_source=None)
    if state == "dirty":
        (repo / "DIRTY.txt").write_text("dirty\n", encoding="utf-8")
    elif state == "detached":
        git(repo, "checkout", "--quiet", "--detach")
    elif state == "non-main":
        git(repo, "checkout", "--quiet", "-b", "feature")
    elif state == "missing-upstream":
        git(repo, "branch", "--unset-upstream")
    elif state == "ambiguous-upstream":
        git(repo, "config", "--add", "branch.main.remote", "second-remote")
    elif state == "ahead":
        git(repo, "commit", "--allow-empty", "--quiet", "-m", "local ahead")
    elif state == "diverged":
        publish_upstream(repo, {"REMOTE.txt": "remote\n"})
        git(repo, "commit", "--allow-empty", "--quiet", "-m", "local diverged")
    else:
        git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    git_calls = []
    real_git = operator_module._git

    def recording_git(root, *args, **kwargs):
        git_calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", recording_git)
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: pytest.fail("unsafe checkout reached Unified State"),
    )
    monkeypatch.setattr(
        operator_module,
        "run_task",
        lambda *_args, **_kwargs: pytest.fail("unsafe checkout invoked Executor path"),
    )

    with pytest.raises(OperatorError):
        operator_module.continue_task("TASK-102", executor="codex", repo=repo)

    assert not list(operator_module._runtime_paths_readonly(repo).runs.glob("*.json"))
    assert len([args for args in git_calls if args[:2] == ("merge", "--ff-only")]) <= 1
    prohibited = {"rebase", "reset", "checkout", "stash", "clean", "pull", "push"}
    assert not any(args and args[0] in prohibited for args in git_calls)


def test_continue_missing_task_native_fast_forward_failure_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path, task_source=None)
    before = git(repo, "rev-parse", "HEAD")
    publish_upstream(repo, {"UPSTREAM.txt": "upstream\n"})
    git_calls = []
    real_git = operator_module._git

    def failing_git(root, *args, **kwargs):
        git_calls.append(args)
        if args[:2] == ("merge", "--ff-only"):
            raise OperatorError("simulated native fast-forward failure")
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(operator_module, "_git", failing_git)

    with pytest.raises(OperatorError, match="upstream fast-forward failed"):
        operator_module.continue_task("TASK-102", executor="codex", repo=repo)

    assert git(repo, "rev-parse", "HEAD") == before
    assert len([args for args in git_calls if args[:2] == ("merge", "--ff-only")]) == 1
    assert not list(operator_module._runtime_paths_readonly(repo).runs.glob("*.json"))


@pytest.mark.parametrize("advance", [False, True])
def test_continue_sync_without_requested_task_fails_once_before_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    advance: bool,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path, task_source=None)
    expected_head = git(repo, "rev-parse", "HEAD")
    if advance:
        expected_head = publish_upstream(repo, {"UPSTREAM.txt": "upstream\n"})
    sync_calls = []
    real_sync = operator_module._synchronize_primary_branch

    def recording_sync(*args, **kwargs):
        sync_calls.append((args, kwargs))
        return real_sync(*args, **kwargs)

    monkeypatch.setattr(operator_module, "_synchronize_primary_branch", recording_sync)
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: pytest.fail("missing TASK reached Unified State"),
    )

    with pytest.raises(OperatorError, match="TASK not found: TASK-102"):
        operator_module.continue_task("TASK-102", executor="codex", repo=repo)

    assert len(sync_calls) == 1
    assert git(repo, "rev-parse", "HEAD") == expected_head
    assert not list(operator_module._runtime_paths_readonly(repo).runs.glob("*.json"))


def test_continue_restart_without_requested_task_does_not_sync_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIOS_RESTART_ATTEMPTED", raising=False)
    repo = make_repo(tmp_path, task_source=None)
    publish_upstream(repo, {"src/aios_renew/marker.py": "# synchronized kernel\n"})
    argv = ["continue", "TASK-102", "--executor", "codex", "--repo", str(repo)]
    sync_calls = []
    child_errors = []
    real_sync = operator_module._synchronize_primary_branch

    def recording_sync(*args, **kwargs):
        sync_calls.append((args, kwargs))
        return real_sync(*args, **kwargs)

    def restart(root, *, argv=None, runner=subprocess.run):
        monkeypatch.setenv("AIOS_RESTART_ATTEMPTED", "1")
        try:
            operator_module.continue_task(
                "TASK-102", executor="codex", repo=root, argv=argv
            )
        except OperatorError as exc:
            child_errors.append(str(exc))
        return 1

    monkeypatch.setattr(operator_module, "_synchronize_primary_branch", recording_sync)
    monkeypatch.setattr(operator_module, "_restart_primary_invocation", restart)
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: pytest.fail("missing TASK reached Unified State"),
    )

    outcome, exit_code = operator_module.continue_task(
        "TASK-102", executor="codex", repo=repo, argv=argv
    )

    assert outcome is None
    assert exit_code == 1
    assert len(sync_calls) == 1
    assert child_errors == ["TASK not found: TASK-102"]
    assert not list(operator_module._runtime_paths_readonly(repo).runs.glob("*.json"))


def test_human_surface_module_boundary_and_operator_compatibility() -> None:
    assert operator_module.HumanSurfaceResult is DirectHumanSurfaceResult
    assert operator_module.continue_task is direct_continue_task
    assert human_surface_module.HumanSurfaceResult is operator_module.HumanSurfaceResult
    assert human_surface_module.continue_task is operator_module.continue_task


def test_human_surface_direct_module_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setattr(
        operator_module,
        "observe_unified_state",
        lambda *_args, **_kwargs: _human_observation("WAIT", run_id="RUN-101-001"),
    )
    outcome, exit_code = direct_continue_task("TASK-101", repo=repo)
    assert exit_code == 0
    assert outcome is not None
    payload = outcome.as_dict()
    assert payload["format"] == "AIOS_HUMAN_SURFACE"
    assert payload["version"] == 1
    assert payload["observed_next_action"] == "WAIT"
    assert payload["disposition"] == "NO_ACTION"
    assert payload["authority"] == "NONE"
    assert payload["selectors"]["run_id"] == "RUN-101-001"


def test_human_surface_ac5_exposes_outstanding_findings_and_does_not_delegate_multi_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    finding_1 = human_surface_module.OutstandingFindingIdentity(
        source_run_id="RUN-101-001",
        review_id="REVIEW-101-001",
        finding_id="F1",
        reviewed_sha="a" * 40,
    )
    finding_2 = human_surface_module.OutstandingFindingIdentity(
        source_run_id="RUN-101-001",
        review_id="REVIEW-101-001",
        finding_id="F2",
        reviewed_sha="a" * 40,
    )
    observation = operator_module.UnifiedStateObservation(
        "TASK-101",
        1,
        "CORRECTION",
        "AUTHOR_REMEDIATION",
        run_id="RUN-101-001",
        source_run_id="RUN-101-001",
        review_id="REVIEW-101-001",
        finding_id=None,
        candidate_sha="a" * 40,
        reviewed_sha="a" * 40,
        outstanding_findings=(finding_1, finding_2),
    )
    monkeypatch.setattr(
        operator_module, "observe_unified_state", lambda *_args, **_kwargs: observation
    )
    forbidden = lambda *_args, **_kwargs: pytest.fail("operation was delegated")
    monkeypatch.setattr(operator_module, "run_task", forbidden)
    monkeypatch.setattr(operator_module, "run_remediation", forbidden)
    monkeypatch.setattr(operator_module, "run_repair", forbidden)

    # AC5: Supplying an Executor must not grant selection authority or cause a multi-finding correction to execute
    outcome, exit_code = operator_module.continue_task(
        "TASK-101", executor="antigravity", repo=repo
    )

    assert exit_code == 0
    assert outcome is not None
    payload = outcome.as_dict()
    assert payload["format"] == "AIOS_HUMAN_SURFACE"
    assert payload["version"] == 1
    assert payload["observed_next_action"] == "AUTHOR_REMEDIATION"
    assert payload["disposition"] == "EXTERNAL_AUTHORITY_REQUIRED"
    assert payload["authority"] == "BRAIN"
    assert payload["delegated_operation"] is None
    assert payload["selectors"]["finding_id"] is None
    assert payload["selectors"]["source_run_id"] == "RUN-101-001"
    assert payload["executor"]["supplied"] is True
    assert payload["executor"]["identity"] == "antigravity"
    assert len(payload["outstanding_findings"]) == 2
    assert payload["outstanding_findings"] == [dict(finding_1), dict(finding_2)]
