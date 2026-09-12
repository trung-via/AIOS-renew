"""Coverage for the ``aios performance`` read-only Human-facing surface."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aios_renew.performance_observation import (
    PerformanceObservationError,
    collect_performance_observation,
    parse_task_selectors,
)
from aios_renew.review_transport import (
    PERFORMANCE_MAX_TERMINAL_RUNS,
    ReviewTransportError,
    resolve_remote_performance_namespace,
    transport_failure,
    transport_post_pass,
)


TASK = {"id": "TASK-101", "revision": 1}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    remote = root / "upstream.git"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "Performance Test")
    git(repo, "config", "user.email", "performance@example.invalid")
    git(repo, "branch", "-M", "main")
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK-101.yaml").write_text(
        "task_id: TASK-101\nrevision: 1\ngoal: aggregate observation\n",
        encoding="utf-8",
    )
    (repo / "subject.txt").write_text("root\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "root")
    subprocess.run(("git", "init", "--bare", "--quiet", str(remote)), check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "main")
    return repo, remote


def write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(content)
    return content


def commit_candidate(repo: Path, label: str) -> str:
    (repo / "subject.txt").write_text(f"{label}\n", encoding="utf-8")
    git(repo, "add", "subject.txt")
    git(repo, "commit", "--quiet", "-m", label)
    return git(repo, "rev-parse", "HEAD")


def publish_success(
    repo: Path,
    files: Path,
    *,
    run_id: str,
    head_sha: str,
    root_base_sha: str,
    observation: dict | None = None,
) -> None:
    run = {
        "run_id": run_id,
        "task": TASK,
        "executor": "codex",
        "base_sha": root_base_sha,
        "workspace": str(repo),
        "head_sha": head_sha,
        "status": "PASS",
    }
    result = {
        "result": {
            "head_sha": head_sha,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "candidate delivered",
                    "evidence": [],
                }
            ],
            "changed_files": ["subject.txt"],
            "unresolved": [],
        },
        "evidence": [],
    }
    directory = files / run_id
    run_path = directory / "run.json"
    result_path = directory / "result.json"
    write_json(run_path, run)
    write_json(result_path, result)
    observation_path = None
    if observation is not None:
        observation_path = directory / "observation.json"
        write_json(observation_path, observation)
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        result_path=result_path,
        observation_path=observation_path,
    )


def publish_failure(
    repo: Path,
    files: Path,
    *,
    run_id: str,
    head_sha: str,
    root_base_sha: str,
    observation: dict | None = None,
) -> None:
    run = {
        "run_id": run_id,
        "task": TASK,
        "executor": "codex",
        "base_sha": root_base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "FAIL",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": run_id,
        "task": TASK,
        "executor": "codex",
        "base_sha": root_base_sha,
        "failed_head_sha": head_sha,
        "candidate": {
            "transportable": True,
            "repairable": False,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["subject.txt"],
            "outside_task_scope": [],
        },
    }
    directory = files / run_id
    run_path = directory / "run.json"
    failure_path = directory / "failure.json"
    write_json(run_path, run)
    write_json(failure_path, failure)
    observation_path = None
    if observation is not None:
        observation_path = directory / "observation.json"
        write_json(observation_path, observation)
    transport_failure(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        failure_path=failure_path,
        observation_path=observation_path,
    )


def observation_for(
    run_id: str,
    *,
    terminal_kind: str,
    operation: str = "PRIMARY",
    executor: str = "codex",
    base_sha: str | None = None,
    admitted_run_seconds: float = 12.0,
    executor_seconds: float | None = 10.0,
    verification_seconds: float | None = 2.0,
    soft_budget: dict | None = None,
    token_usage: dict | None = None,
) -> dict:
    durations = {
        "admitted_run_seconds": admitted_run_seconds,
        "executor_seconds": executor_seconds,
        "verification_seconds": verification_seconds,
    }
    data = {
        "kind": "RUN_OBSERVATION",
        "run_id": run_id,
        "task": TASK,
        "operation": operation,
        "executor": executor,
        "base_sha": base_sha,
        "terminal_kind": terminal_kind,
        "executor_invoked": executor_seconds is not None,
        "durations": durations,
        "token_usage": token_usage,
    }
    if soft_budget is not None:
        data["soft_budget"] = soft_budget
    # Validate locally to ensure it round-trips through the validator.
    from aios_renew.run_observation import validate_observation

    validate_observation(data)
    return data


def test_parse_selectors_accepts_one_to_thirty_two_unique(tmp_path: Path) -> None:
    pairs = [(f"TASK-{index:03d}", 1) for index in range(2)]
    selectors = parse_task_selectors(
        [f"{task_id}:{revision}" for task_id, revision in pairs]
    )
    assert len(selectors) == 2
    assert all(isinstance(selector.task_id, str) for selector in selectors)
    assert selectors[0].task_id < selectors[1].task_id


def test_parse_selectors_rejects_over_32_unique() -> None:
    too_many = [f"TASK-{index:04d}:1" for index in range(33)]
    with pytest.raises(PerformanceObservationError, match="more than"):
        parse_task_selectors(too_many)


def test_parse_selectors_rejects_duplicates() -> None:
    with pytest.raises(PerformanceObservationError, match="duplicate"):
        parse_task_selectors(["TASK-101:1", "TASK-101:1"])


def test_parse_selectors_rejects_missing_revision() -> None:
    with pytest.raises(PerformanceObservationError, match="explicit revision"):
        parse_task_selectors(["TASK-101"])


def test_parse_selectors_rejects_non_positive_revision() -> None:
    with pytest.raises(PerformanceObservationError, match="positive"):
        parse_task_selectors(["TASK-101:0"])


def test_parse_selectors_rejects_malformed_id() -> None:
    with pytest.raises(PerformanceObservationError, match="invalid TASK id"):
        parse_task_selectors(["BAD-ID:1"])


def test_parse_selectors_rejects_empty_input() -> None:
    with pytest.raises(PerformanceObservationError, match="required"):
        parse_task_selectors([])


def test_empty_namespace_emits_zero_counts_and_null_durations(
    tmp_path: Path,
) -> None:
    repo, _remote = make_repo(tmp_path)
    observation = collect_performance_observation(repo, ["TASK-101:1"])
    payload = observation.render()
    assert payload["kind"] == "AIOS_PERFORMANCE_OBSERVATION"
    assert payload["version"] == 1
    assert payload["coverage"]["distinct_terminal_runs"] == 0
    assert payload["counts"]["by_terminal_kind"] == {"RESULT": 0, "FAILURE": 0}
    assert payload["durations"]["admitted_run_seconds"] == {
        "samples": 0,
        "p50": None,
        "p95": None,
    }
    assert payload["failure_rate"] is None
    assert payload["verification_share"] is None
    assert payload["token_usage"] == {
        "sample_count": 0,
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
    }


def test_single_observation_round_trip_is_deterministic(tmp_path: Path) -> None:
    repo, _remote = make_repo(tmp_path)
    files = tmp_path / "files"
    base_sha = git(repo, "rev-parse", "HEAD")
    head_sha = commit_candidate(repo, "subject")
    observation_payload = observation_for(

        "RUN-101-001",
        terminal_kind="RESULT",
        operation="PRIMARY",
        base_sha=base_sha,
        admitted_run_seconds=12.0,
        executor_seconds=10.0,
        verification_seconds=2.0,
        soft_budget={
            "threshold_seconds": 1800,
            "status": "WITHIN",
        },
        token_usage={
            "input_tokens": 100,
            "cached_input_tokens": 25,
            "output_tokens": 30,
        },
    )
    publish_success(
        repo,
        files,
        run_id="RUN-101-001",
        head_sha=head_sha,
        root_base_sha=base_sha,
        observation=observation_payload,
    )

    first = collect_performance_observation(repo, ["TASK-101:1"])
    second = collect_performance_observation(repo, ["TASK-101:1"])
    assert first.render() == second.render()
    payload = first.render()

    assert payload["coverage"]["distinct_terminal_runs"] == 1
    assert payload["coverage"]["missing_observations"] == []
    assert payload["coverage"]["malformed_observations"] == []
    assert payload["counts"]["by_operation"] == {
        "PRIMARY": 1,
        "REMEDIATION": 0,
        "REPAIR": 0,
    }
    assert payload["counts"]["by_terminal_kind"] == {"RESULT": 1, "FAILURE": 0}
    assert payload["counts"]["by_executor"] == {"codex": 1}
    assert payload["counts"]["by_task_revision"] == {"1": 1}
    assert payload["counts"]["by_soft_budget_status"] == {
        "WITHIN": 1,
        "EXCEEDED": 0,
        "NOT_APPLICABLE": 0,
    }
    assert payload["durations"]["admitted_run_seconds"]["p50"] == 12.0
    assert payload["durations"]["executor_seconds"]["p50"] == 10.0
    assert payload["durations"]["verification_seconds"]["p50"] == 2.0
    assert payload["failure_rate"] == 0.0
    assert payload["verification_share"] == pytest.approx(2 / 12, abs=1e-6)
    assert payload["token_usage"] == {
        "sample_count": 1,
        "input_tokens": 100,
        "cached_input_tokens": 25,
        "output_tokens": 30,
    }
    assert payload["soft_budget"]["trusted_sample_count"] == 1


def test_p50_p95_use_nearest_rank_over_exact_non_null_samples(
    tmp_path: Path,
) -> None:
    repo, _remote = make_repo(tmp_path)
    files = tmp_path / "files"
    base_sha = git(repo, "rev-parse", "HEAD")
    samples = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    for index, duration in enumerate(samples, start=1):
        head_sha = commit_candidate(repo, f"step-{index}")
        publish_success(
            repo,
            files,
            run_id=f"RUN-101-{index:03d}",
            head_sha=head_sha,
            root_base_sha=base_sha,
            observation=observation_for(
                f"RUN-101-{index:03d}",
                terminal_kind="RESULT",
                base_sha=base_sha,
                admitted_run_seconds=duration,
                executor_seconds=duration - 1.0,
                verification_seconds=1.0,
            ),
        )

    payload = collect_performance_observation(repo, ["TASK-101:1"]).render()

    assert (
        payload["durations"]["admitted_run_seconds"]["samples"] == 10
    )
    # 10 samples, p50 rank = ceil(0.5*10) = 5 -> samples[4] = 5.0
    # p95 rank = ceil(0.95*10) = 10 -> samples[9] = 10.0
    assert payload["durations"]["admitted_run_seconds"]["p50"] == 5.0
    assert payload["durations"]["admitted_run_seconds"]["p95"] == 10.0
    assert (
        payload["durations"]["executor_seconds"]["samples"] == 10
    )
    assert payload["durations"]["executor_seconds"]["p50"] == 4.0
    assert payload["durations"]["executor_seconds"]["p95"] == 9.0


def test_token_totals_count_only_trusted_complete_samples(tmp_path: Path) -> None:
    repo, _remote = make_repo(tmp_path)
    files = tmp_path / "files"
    base_sha = git(repo, "rev-parse", "HEAD")
    head_a = commit_candidate(repo, "step-a")
    publish_success(
        repo,
        files,
        run_id="RUN-101-001",
        head_sha=head_a,
        root_base_sha=base_sha,
        observation=observation_for(
            "RUN-101-001",
            terminal_kind="RESULT",
            base_sha=base_sha,
            admitted_run_seconds=4.0,
            executor_seconds=3.0,
            verification_seconds=1.0,
            token_usage={
                "input_tokens": 100,
                "cached_input_tokens": 10,
                "output_tokens": 20,
            },
        ),
    )
    head_b = commit_candidate(repo, "step-b")
    publish_success(
        repo,
        files,
        run_id="RUN-101-002",
        head_sha=head_b,
        root_base_sha=base_sha,
        observation=observation_for(
            "RUN-101-002",
            terminal_kind="RESULT",
            base_sha=base_sha,
            admitted_run_seconds=6.0,
            executor_seconds=4.0,
            verification_seconds=2.0,
            # No token usage -> omitted from totals
        ),
    )

    payload = collect_performance_observation(repo, ["TASK-101:1"]).render()
    assert payload["token_usage"]["sample_count"] == 1
    assert payload["token_usage"]["input_tokens"] == 100
    assert payload["token_usage"]["cached_input_tokens"] == 10
    assert payload["token_usage"]["output_tokens"] == 20


def test_failure_rate_and_verification_share_use_exact_formulas(
    tmp_path: Path,
) -> None:
    repo, _remote = make_repo(tmp_path)
    files = tmp_path / "files"
    base_sha = git(repo, "rev-parse", "HEAD")
    head_ok = commit_candidate(repo, "ok")
    publish_success(
        repo,
        files,
        run_id="RUN-101-001",
        head_sha=head_ok,
        root_base_sha=base_sha,
        observation=observation_for(
            "RUN-101-001",
            terminal_kind="RESULT",
            base_sha=base_sha,
            admitted_run_seconds=10.0,
            executor_seconds=8.0,
            verification_seconds=2.0,
        ),
    )
    head_fail = commit_candidate(repo, "fail")
    publish_failure(
        repo,
        files,
        run_id="RUN-101-002",
        head_sha=head_fail,
        root_base_sha=base_sha,
        observation=observation_for(
            "RUN-101-002",
            terminal_kind="FAILURE",
            base_sha=base_sha,
            admitted_run_seconds=6.0,
            executor_seconds=None,
            verification_seconds=None,
        ),
    )

    payload = collect_performance_observation(repo, ["TASK-101:1"]).render()
    assert payload["failure_rate"] == 0.5
    # verification_share = 2 / (10 + 6)
    assert payload["verification_share"] == pytest.approx(2 / 16, abs=1e-6)


def test_observation_sidecar_binding_fails_closed_on_mismatched_identity(
    tmp_path: Path,
) -> None:
    repo, _remote = make_repo(tmp_path)
    files = tmp_path / "files"
    base_sha = git(repo, "rev-parse", "HEAD")
    head_sha = commit_candidate(repo, "subject")
    # Publish a fresh run with a mismatched observation (executor "antigravity"
    # in the observation but "codex" in the RUN envelope).
    files_dir = files / "RUN-101-001"
    files_dir.mkdir(parents=True, exist_ok=True)
    run_payload = {
        "run_id": "RUN-101-001",
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": head_sha,
        "status": "PASS",
    }
    result_payload = {
        "result": {
            "head_sha": head_sha,
            "claims": [{"id": "C1", "satisfies": ["AC1"], "claim": "x", "evidence": []}],
            "changed_files": ["subject.txt"],
            "unresolved": [],
        },
        "evidence": [],
    }
    write_json(files_dir / "run.json", run_payload)
    write_json(files_dir / "result.json", result_payload)
    bogus = observation_for(
        "RUN-101-001",
        terminal_kind="RESULT",
        base_sha=base_sha,
        executor="antigravity",  # wrong executor -> binding must fail
    )
    bogus["executor"] = "antigravity"
    write_json(files_dir / "observation.json", bogus)
    from aios_renew.review_transport import transport_post_pass

    transport_post_pass(
        repo,
        run_id="RUN-101-001",
        head_sha=head_sha,
        run_path=files_dir / "run.json",
        result_path=files_dir / "result.json",
        observation_path=files_dir / "observation.json",
    )

    payload = collect_performance_observation(repo, ["TASK-101:1"]).render()
    assert payload["coverage"]["malformed_observations"] == ["RUN-101-001"]
    assert payload["coverage"]["distinct_terminal_runs"] == 1


def test_missing_observation_is_recorded_as_coverage_gap(tmp_path: Path) -> None:
    repo, _remote = make_repo(tmp_path)
    files = tmp_path / "files"
    base_sha = git(repo, "rev-parse", "HEAD")
    head_sha = commit_candidate(repo, "subject")
    publish_success(
        repo,
        files,
        run_id="RUN-101-001",
        head_sha=head_sha,
        root_base_sha=base_sha,
        observation=None,
    )

    payload = collect_performance_observation(repo, ["TASK-101:1"]).render()
    assert payload["coverage"]["missing_observations"] == ["RUN-101-001"]
    assert payload["coverage"]["distinct_terminal_runs"] == 1
    assert payload["counts"]["by_terminal_kind"] == {"RESULT": 0, "FAILURE": 0}


def test_conflicting_run_ids_fail_closed(tmp_path: Path) -> None:
    repo, _remote = make_repo(tmp_path)
    files = tmp_path / "files"
    base_sha = git(repo, "rev-parse", "HEAD")
    head_success = commit_candidate(repo, "step-1")
    publish_success(
        repo,
        files,
        run_id="RUN-101-001",
        head_sha=head_success,
        root_base_sha=base_sha,
        observation=observation_for(
            "RUN-101-001",
            terminal_kind="RESULT",
            base_sha=base_sha,
            admitted_run_seconds=2.0,
            executor_seconds=1.0,
            verification_seconds=1.0,
        ),
    )
    # Add a FAILURE ref for the same RUN id to create conflict.
    head_failure = commit_candidate(repo, "step-2")
    publish_failure(
        repo,
        files,
        run_id="RUN-101-001",
        head_sha=head_failure,
        root_base_sha=base_sha,
        observation=observation_for(
            "RUN-101-001",
            terminal_kind="FAILURE",
            base_sha=base_sha,
            admitted_run_seconds=2.0,
            executor_seconds=None,
            verification_seconds=None,
        ),
    )

    payload = collect_performance_observation(repo, ["TASK-101:1"]).render()
    # The first-seen kind wins, but the other is reported as conflicting.
    assert payload["coverage"]["conflicting_run_ids"] == [["TASK-101", "RUN-101-001"]]
    assert payload["coverage"]["distinct_terminal_runs"] == 1


def test_overflow_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _remote = make_repo(tmp_path)
    # Patch the bound to force overflow.
    monkeypatch.setattr(
        "aios_renew.performance_observation.PERFORMANCE_MAX_SELECTORS",
        1,
    )
    with pytest.raises(PerformanceObservationError, match="more than"):
        parse_task_selectors(["TASK-101:1", "TASK-102:1"])


def test_performance_command_fails_closed_on_remote_mismatch(tmp_path: Path) -> None:
    repo, _remote = make_repo(tmp_path)
    files = tmp_path / "files"
    base_sha = git(repo, "rev-parse", "HEAD")
    head = commit_candidate(repo, "subject")
    # Publish a fresh run with a tampered result.head_sha that does not match
    # the candidate SHA at aios/review/<RUN_ID>.
    files_dir = files / "RUN-101-001"
    files_dir.mkdir(parents=True, exist_ok=True)
    run_payload = {
        "run_id": "RUN-101-001",
        "task": {"id": "TASK-101", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": head,
        "status": "PASS",
    }
    result_payload = {
        "result": {
            "head_sha": "f" * 40,  # tampered; will not match review ref
            "claims": [{"id": "C1", "satisfies": ["AC1"], "claim": "x", "evidence": []}],
            "changed_files": ["subject.txt"],
            "unresolved": [],
        },
        "evidence": [],
    }
    write_json(files_dir / "run.json", run_payload)
    write_json(files_dir / "result.json", result_payload)
    write_json(files_dir / "observation.json", observation_for(
        "RUN-101-001",
        terminal_kind="RESULT",
        base_sha=base_sha,
        admitted_run_seconds=1.0,
        executor_seconds=0.5,
        verification_seconds=0.5,
    ))
    from aios_renew.review_transport import transport_post_pass

    transport_post_pass(
        repo,
        run_id="RUN-101-001",
        head_sha=head,
        run_path=files_dir / "run.json",
        result_path=files_dir / "result.json",
        observation_path=files_dir / "observation.json",
    )

    with pytest.raises(ReviewTransportError):
        collect_performance_observation(repo, ["TASK-101:1"])


def test_max_terminal_run_bound_is_256() -> None:
    assert PERFORMANCE_MAX_TERMINAL_RUNS == 256


def test_observation_emits_deterministic_canonical_envelope(tmp_path: Path) -> None:
    repo, _remote = make_repo(tmp_path)
    files = tmp_path / "files"
    base_sha = git(repo, "rev-parse", "HEAD")
    head = commit_candidate(repo, "subject")
    publish_success(
        repo,
        files,
        run_id="RUN-101-001",
        head_sha=head,
        root_base_sha=base_sha,
        observation=observation_for(
            "RUN-101-001",
            terminal_kind="RESULT",
            base_sha=base_sha,
            admitted_run_seconds=1.0,
            executor_seconds=0.5,
            verification_seconds=0.5,
        ),
    )

    raw_one = json.dumps(
        collect_performance_observation(repo, ["TASK-101:1"]).render(),
        sort_keys=True,
        separators=(",", ":"),
    )
    raw_two = json.dumps(
        collect_performance_observation(repo, ["TASK-101:1"]).render(),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert raw_one == raw_two
