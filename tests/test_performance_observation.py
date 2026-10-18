"""Focused regression coverage for AIOS_PERFORMANCE_OBSERVATION v1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from aios_renew.performance_observation import (
    PerformanceObservationError,
    L1_MAX_RECORD_BYTES,
    L1_MAX_SNAPSHOT_BYTES,
    build_l1_baseline_readiness,
    build_performance_observation,
    nearest_rank_percentile,
    validate_performance_observation,
    validate_task_selectors,
)
from aios_renew.review_transport import (
    RemotePerformanceSnapshot,
    RemoteTerminalArtifact,
)
from aios_renew.run_observation import (
    RunObservation,
    SoftBudget,
    TokenUsage,
    observation_data,
)


def terminal(
    run_id: str,
    *,
    revision: int = 4,
    kind: str = "RESULT",
    observation: RunObservation | None = None,
    run: dict | None = None,
    terminal_document: dict | None = None,
) -> RemoteTerminalArtifact:
    base_sha = "a" * 40
    run_document = run or {
        "run_id": run_id,
        "task": {"id": "TASK-101", "revision": revision},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": "bounded",
        "head_sha": None,
        "status": "ACTIVE",
    }
    if terminal_document is None and kind == "RESULT":
        terminal_document = {
            "result": {
                "head_sha": "b" * 40,
                "claims": [],
                "changed_files": [],
                "unresolved": [],
            },
            "evidence": [],
        }
    elif terminal_document is None:
        terminal_document = {
            "kind": "FAILURE",
            "run_id": run_id,
            "task": {"id": "TASK-101", "revision": revision},
            "executor": "codex",
            "base_sha": base_sha,
            "failed_head_sha": "c" * 40,
            "phase": "EXECUTION",
            "error": {"type": "Example", "message": "bounded"},
            "candidate": {
                "transportable": True,
                "repairable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        }
    return RemoteTerminalArtifact(
        run_id=run_id,
        task_id="TASK-101",
        terminal_kind=kind,
        artifact_sha=hashlib.sha256(run_id.encode()).hexdigest()[:40],
        run=json.dumps(run_document).encode(),
        terminal=json.dumps(terminal_document).encode(),
        observation=(
            None
            if observation is None
            else json.dumps(observation_data(observation)).encode()
        ),
    )


def observed(
    run_id: str,
    *,
    revision: int = 4,
    operation: str = "PRIMARY",
    kind: str = "RESULT",
    admitted: float = 10.0,
    executor_seconds: float | None = 4.0,
    verification_seconds: float | None = 2.0,
    token_usage: TokenUsage | None = None,
    soft_budget: SoftBudget | None = None,
) -> RunObservation:
    return RunObservation(
        run_id=run_id,
        task_id="TASK-101",
        task_revision=revision,
        operation=operation,
        executor="codex",
        base_sha="a" * 40,
        terminal_kind=kind,
        executor_invoked=executor_seconds is not None,
        admitted_run_elapsed_seconds=admitted,
        executor_elapsed_seconds=executor_seconds,
        verification_elapsed_seconds=verification_seconds,
        token_usage=token_usage,
        soft_budget=soft_budget,
    )


def test_selectors_are_bare_unique_bounded_and_sorted() -> None:
    assert validate_task_selectors(["TASK-102", "TASK-101"]) == (
        "TASK-101",
        "TASK-102",
    )
    for invalid in ("TASK-101:4", "TASK-101@4", "task-101", "TASK-101 "):
        with pytest.raises(PerformanceObservationError):
            validate_task_selectors([invalid])
    with pytest.raises(PerformanceObservationError, match="duplicate"):
        validate_task_selectors(["TASK-101", "TASK-101"])
    with pytest.raises(PerformanceObservationError, match="maximum"):
        validate_task_selectors([f"TASK-{index:03d}" for index in range(33)])


def test_terminal_binding_precedes_missing_coverage_classification() -> None:
    malformed_failure = terminal(
        "RUN-101-001",
        kind="FAILURE",
        terminal_document={
            "kind": "FAILURE",
            "run_id": "RUN-101-OTHER",
            "task": {"id": "TASK-101", "revision": 4},
            "executor": "codex",
            "base_sha": "a" * 40,
            "failed_head_sha": "c" * 40,
            "candidate": {},
        },
    )
    with pytest.raises(PerformanceObservationError, match="FAILURE identity"):
        build_performance_observation(
            RemotePerformanceSnapshot(("TASK-101",), (malformed_failure,))
        )


def test_missing_observation_is_explicit_only_after_valid_terminal() -> None:
    result = build_performance_observation(
        RemotePerformanceSnapshot(
            ("TASK-101",), (terminal("RUN-101-001"),)
        )
    )
    assert result.coverage.terminal_runs == 1
    assert result.coverage.valid_observations == 0
    assert result.coverage.missing_observations == 1
    assert result.failure_rate is None
    assert result.verification_share is None


def test_observation_identity_conflict_fails_closed() -> None:
    mismatched = observed("RUN-101-999")
    item = terminal("RUN-101-001", observation=mismatched)
    with pytest.raises(PerformanceObservationError, match="identity conflicts"):
        build_performance_observation(
            RemotePerformanceSnapshot(("TASK-101",), (item,))
        )


def test_exact_formulas_counts_and_complete_sample_coverage() -> None:
    first = observed(
        "RUN-101-001",
        admitted=10.0,
        verification_seconds=2.0,
        token_usage=TokenUsage(100, 25, 40),
        soft_budget=SoftBudget(1800, "WITHIN"),
    )
    second = observed(
        "RUN-101-002",
        revision=3,
        operation="REPAIR",
        kind="FAILURE",
        admitted=30.0,
        executor_seconds=None,
        verification_seconds=None,
        soft_budget=SoftBudget(900, "NOT_APPLICABLE"),
    )
    result = build_performance_observation(
        RemotePerformanceSnapshot(
            ("TASK-101",),
            (
                terminal("RUN-101-001", observation=first),
                terminal(
                    "RUN-101-002",
                    revision=3,
                    kind="FAILURE",
                    observation=second,
                ),
            ),
        )
    )
    assert result.counts["by_operation"] == {
        "PRIMARY": 1,
        "REMEDIATION": 0,
        "REPAIR": 1,
    }
    assert result.counts["by_task_revision"] == {"3": 1, "4": 1}
    assert result.failure_rate == 0.5
    assert result.verification_share == 2.0 / 40.0
    assert result.durations["admitted_run_seconds"].p50 == 10.0
    assert result.durations["admitted_run_seconds"].p95 == 30.0
    assert result.token_usage.sample_count == 1
    assert result.token_usage.input_tokens == 100
    assert result.coverage.token_usage_samples == 1
    assert result.soft_budget["sample_count"] == 2


def test_nearest_rank_has_no_interpolation_or_rounding() -> None:
    assert nearest_rank_percentile([], 50) is None
    assert nearest_rank_percentile([1.125, 2.875], 50) == 1.125
    assert nearest_rank_percentile([1.125, 2.875], 95) == 2.875


def l1_snapshot(*items: RemoteTerminalArtifact,
                selectors: tuple[str, ...] = ("TASK-101",)) -> RemotePerformanceSnapshot:
    return RemotePerformanceSnapshot(
        selectors, tuple(items), tuple(
            (f"refs/heads/aios/{'artifacts' if item.terminal_kind == 'RESULT' else 'failure-artifacts'}/{item.run_id}",
             item.artifact_sha) for item in items
        ),
    )


def l1_data(snapshot: RemotePerformanceSnapshot) -> dict:
    return build_l1_baseline_readiness(snapshot, task_ids=snapshot.task_selectors).as_dict()


def test_l1_complete_samples_and_family_denominators() -> None:
    first = terminal("RUN-101-001", observation=observed(
        "RUN-101-001", token_usage=TokenUsage(100, 25, 40)))
    second = terminal("RUN-101-002", kind="FAILURE", observation=observed(
        "RUN-101-002", operation="REPAIR", kind="FAILURE", admitted=30.0,
        token_usage=TokenUsage(10, 0, 5)))
    data = l1_data(l1_snapshot(first, second))
    assert data["coverage"] == {
        "status": "OBSERVED", "sample_count": 2, "population_count": 2, "missing_count": 0,
    }
    assert data["metrics"]["admitted_run_seconds"] == {
        "status": "OBSERVED", "sample_count": 2, "population_count": 2, "missing_count": 0,
        "unit": "seconds", "observed_sum": 40.0, "p50": 10.0, "p95": 30.0,
    }
    for name, total in (("input_tokens", 110), ("cached_input_tokens", 25), ("output_tokens", 45)):
        assert data["metrics"][name] == {
            "status": "OBSERVED", "sample_count": 2, "population_count": 2, "missing_count": 0,
            "unit": "tokens", "observed_sum": total,
        }
    assert data["counts"]["by_terminal_kind"]["values"] == {"FAILURE": 1, "RESULT": 1}
    assert data["counts"]["by_task"]["values"] == {"TASK-101": 2}
    assert data["counts"]["by_task_revision"]["values"] == {"TASK-101@4": 2}
    for family, key in (("operation", "PRIMARY"), ("terminal_kind", "RESULT")):
        group = data["families"][family]["groups"][key]
        assert group["outcomes"]["values"] == {"FAILURE": 0, "RESULT": 1}
        assert group["metrics"]["input_tokens"]["population_count"] == 1
        assert group["metrics"]["input_tokens"]["observed_sum"] == 100
    assert data["gaps"]["whole_episode_seconds"]["status"] == "PARTIAL"
    for name in ("proof_coverage", "proof_applicability", "matched_episode_equivalence",
                 "comparative_speedup", "safety_conformance"):
        assert data["gaps"][name]["status"] == "UNKNOWN"
        assert data["gaps"][name]["value"] is None
    for name in ("executed_test_items", "unique_test_items", "equivalent_duplicate_test_items",
                 "collection_seconds", "worker_startup_seconds", "checkpoint_seconds",
                 "applicability_seconds", "review_seconds", "publication_seconds", "dollar_cost"):
        assert data["gaps"][name]["status"] == "UNAVAILABLE"
        assert data["gaps"][name]["value"] is None


def test_l1_partial_zero_missing_and_unknown_operation_are_distinct() -> None:
    data = l1_data(l1_snapshot(
        terminal("RUN-101-001", observation=observed(
            "RUN-101-001", admitted=0.0, executor_seconds=0.0, verification_seconds=0.0,
            token_usage=TokenUsage(0, 0, 0))),
        terminal("RUN-101-002", kind="FAILURE", observation=observed(
            "RUN-101-002", kind="FAILURE", executor_seconds=None, verification_seconds=None)),
        terminal("RUN-101-003"),
    ))
    assert data["coverage"]["sample_count"] == 2
    assert data["coverage"]["population_count"] == 3
    for name in ("executor_seconds", "verification_seconds", "input_tokens",
                 "cached_input_tokens", "output_tokens"):
        metric = data["metrics"][name]
        assert metric["status"] == "PARTIAL"
        assert metric["sample_count"] == 1
        assert metric["population_count"] == 3
        assert metric["missing_count"] == 2
        assert metric["observed_sum"] == 0
    assert data["counts"]["by_terminal_kind"]["sample_count"] == 3
    assert data["counts"]["by_operation"]["status"] == "PARTIAL"
    family = data["families"]["operation"]
    assert family["unclassified_terminal_runs"] == 1
    assert family["groups"]["PRIMARY"]["terminal_runs"] == 2
    assert family["groups"]["PRIMARY"]["metrics"]["input_tokens"]["population_count"] == 2
    assert data["families"]["task"]["groups"]["TASK-101"]["metrics"]["input_tokens"]["population_count"] == 3


def test_l1_invoked_executor_can_have_no_token_or_verification_sample() -> None:
    data = l1_data(l1_snapshot(terminal("RUN-101-001", observation=observed(
        "RUN-101-001", verification_seconds=None))))
    assert data["metrics"]["executor_seconds"]["status"] == "OBSERVED"
    for name in ("verification_seconds", "input_tokens", "cached_input_tokens", "output_tokens"):
        assert data["metrics"][name]["status"] == "UNAVAILABLE"
        assert data["metrics"][name]["population_count"] == 1
        assert data["metrics"][name]["observed_sum"] is None


def test_l1_binds_all_terminals_before_any_metric_even_without_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aios_renew.performance_observation as performance

    good = terminal("RUN-101-001", observation=observed("RUN-101-001"))
    bad = terminal("RUN-101-002", kind="FAILURE")
    failure = json.loads(bad.terminal)
    failure["run_id"] = "RUN-101-999"
    bad = replace(bad, terminal=json.dumps(failure).encode())

    def premature_metric(*args, **kwargs):
        raise AssertionError("metrics preceded complete terminal binding")

    monkeypatch.setattr(performance, "_l1_metrics", premature_metric)
    with pytest.raises(PerformanceObservationError, match="FAILURE identity"):
        l1_data(l1_snapshot(good, bad))


@pytest.mark.parametrize("with_terminal", [False, True])
def test_l1_missing_samples_never_become_measured_zero(with_terminal: bool) -> None:
    items = (terminal("RUN-101-001"),) if with_terminal else ()
    data = l1_data(l1_snapshot(*items))
    for metric in data["metrics"].values():
        assert metric["status"] == "UNAVAILABLE"
        assert metric["sample_count"] == 0
        assert metric["population_count"] == int(with_terminal)
        assert metric["observed_sum"] is None
    assert data["gaps"]["whole_episode_seconds"]["status"] == "UNKNOWN"
    assert data["counts"]["by_operation"]["values"] is None
    if not with_terminal:
        assert data["counts"]["by_terminal_kind"]["values"] is None


def test_l1_exact_multi_task_set_retains_distinct_revision_populations() -> None:
    first = terminal("RUN-101-001", observation=observed("RUN-101-001"))
    second = replace(
        first, run_id="RUN-102-001", task_id="TASK-102", artifact_sha="e" * 40,
        run=first.run.replace(b"TASK-101", b"TASK-102").replace(b"RUN-101", b"RUN-102"),
        observation=first.observation.replace(b"TASK-101", b"TASK-102").replace(b"RUN-101", b"RUN-102"),
    )
    snapshot = l1_snapshot(first, second, selectors=("TASK-101", "TASK-102"))
    data = build_l1_baseline_readiness(snapshot, task_ids=["TASK-102", "TASK-101"]).as_dict()
    assert data["counts"]["by_task_revision"]["values"] == {"TASK-101@4": 1, "TASK-102@4": 1}
    for task_id in snapshot.task_selectors:
        assert data["families"]["task"]["groups"][task_id]["terminal_runs"] == 1
    moved = replace(snapshot, terminals=(first, replace(second, task_id="TASK-101")))
    with pytest.raises(PerformanceObservationError):
        l1_data(moved)


def test_l1_deterministic_exact_source_binding_and_detached_output() -> None:
    first = terminal("RUN-101-001", observation=observed("RUN-101-001"))
    second = terminal("RUN-101-002")
    snapshot = l1_snapshot(second, first)
    before = repr(snapshot)
    view = build_l1_baseline_readiness(snapshot, task_ids=["TASK-101"])
    assert view.render() == build_l1_baseline_readiness(
        l1_snapshot(first, second), task_ids=["TASK-101"]).render()
    original = view.as_dict()
    edited = view.as_dict()
    edited["metrics"]["input_tokens"]["sample_count"] = 99
    assert view.as_dict() == original
    assert repr(snapshot) == before
    assert len(original["source"]["binding_sha256"]) == 64
    source = dict(original["source"])
    digest = source.pop("binding_sha256")
    assert digest == hashlib.sha256(json.dumps(
        source, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")).hexdigest()
    for moved in (replace(first, artifact_sha="e" * 40),
                  replace(first, run=first.run + b" "),
                  replace(first, terminal=first.terminal + b" "),
                  replace(first, observation=first.observation + b" ")):
        changed = l1_data(l1_snapshot(moved, second))
        assert changed["source"]["binding_sha256"] != original["source"]["binding_sha256"]
    with pytest.raises(PerformanceObservationError, match="exact sorted TASK"):
        build_l1_baseline_readiness(snapshot, task_ids=["TASK-101", "TASK-102"])


@pytest.mark.parametrize("mutation", [
    "missing_refs", "stale_sha", "moved_ref", "duplicate_ref", "extra_ref",
    "duplicate_run", "competing_terminal", "reused_artifact", "fake_run_id", "bad_sha", "fake_kind",
    "cross_run", "stale_revision", "stale_base", "conflicting_candidate", "forged_terminal",
])
def test_l1_rejects_stale_forged_cross_run_and_duplicate_sources(mutation: str) -> None:
    item = terminal("RUN-101-001", observation=observed("RUN-101-001"))
    snapshot = l1_snapshot(item)
    if mutation == "missing_refs":
        snapshot = replace(snapshot, observed_refs=())
    elif mutation == "stale_sha":
        snapshot = replace(snapshot, observed_refs=((snapshot.observed_refs[0][0], "e" * 40),))
    elif mutation == "moved_ref":
        snapshot = replace(snapshot, observed_refs=(("refs/heads/aios/artifacts/RUN-101-002", item.artifact_sha),))
    elif mutation in ("duplicate_ref", "extra_ref"):
        extra = snapshot.observed_refs[0] if mutation == "duplicate_ref" else (
            "refs/heads/aios/artifacts/RUN-101-002", "e" * 40)
        snapshot = replace(snapshot, observed_refs=snapshot.observed_refs + (extra,))
    elif mutation == "duplicate_run":
        snapshot = l1_snapshot(item, item)
    elif mutation == "competing_terminal":
        snapshot = l1_snapshot(item, terminal("RUN-101-001", kind="FAILURE"))
    elif mutation == "reused_artifact":
        snapshot = l1_snapshot(item, replace(terminal("RUN-101-002"), artifact_sha=item.artifact_sha))
    elif mutation == "fake_run_id":
        snapshot = l1_snapshot(replace(item, run_id="RUN-101-FAKE"))
    elif mutation == "bad_sha":
        snapshot = l1_snapshot(replace(item, artifact_sha="main"))
    elif mutation == "fake_kind":
        snapshot = l1_snapshot(replace(item, terminal_kind="PASS"))
    elif mutation == "cross_run":
        snapshot = l1_snapshot(replace(item, observation=terminal(
            "RUN-101-002", observation=observed("RUN-101-002")).observation))
    elif mutation in ("stale_revision", "stale_base"):
        raw = json.loads(item.observation)
        if mutation == "stale_revision":
            raw["task"]["revision"] = 3
        else:
            raw["base_sha"] = "e" * 40
        snapshot = l1_snapshot(replace(item, observation=json.dumps(raw).encode()))
    elif mutation == "conflicting_candidate":
        snapshot = l1_snapshot(replace(item, task_revision=4, executor="codex",
                                       base_sha="a" * 40, candidate_sha="e" * 40))
    else:
        snapshot = l1_snapshot(replace(item, terminal=b'{"result":{"head_sha":"fake"},"evidence":[]}'))
    with pytest.raises(PerformanceObservationError):
        l1_data(snapshot)


@pytest.mark.parametrize("mutation", [
    "partial_tokens", "cached_exceeds_input", "bool_tokens", "negative_tokens",
    "no_invocation", "duplicate_counter", "nan_duration", "unsupported_version",
])
def test_l1_strict_observation_counters_and_missingness(mutation: str) -> None:
    item = terminal("RUN-101-001", observation=observed(
        "RUN-101-001", token_usage=TokenUsage(5, 2, 0)))
    raw = json.loads(item.observation)
    if mutation == "partial_tokens":
        del raw["token_usage"]["output_tokens"]
    elif mutation == "cached_exceeds_input":
        raw["token_usage"]["cached_input_tokens"] = 6
    elif mutation == "bool_tokens":
        raw["token_usage"]["input_tokens"] = True
    elif mutation == "negative_tokens":
        raw["token_usage"]["output_tokens"] = -1
    elif mutation == "no_invocation":
        raw["executor_invoked"] = False
        raw["durations"]["executor_seconds"] = None
    elif mutation == "nan_duration":
        raw["durations"]["executor_seconds"] = float("nan")
    elif mutation == "unsupported_version":
        raw["version"] = 2
    content = json.dumps(raw).encode()
    if mutation == "duplicate_counter":
        content = content.replace(b'"input_tokens": 5', b'"input_tokens": 5, "input_tokens": 6')
    with pytest.raises(PerformanceObservationError):
        l1_data(l1_snapshot(replace(item, observation=content)))


def test_l1_bounded_source_and_selector_populations() -> None:
    item = terminal("RUN-101-001")
    invalid = (
        l1_snapshot(*[replace(item, run_id=f"RUN-101-{index:03d}") for index in range(257)]),
        l1_snapshot(replace(item, terminal=b" " * (L1_MAX_RECORD_BYTES + 1))),
        l1_snapshot(*[replace(item, run_id=f"RUN-101-{index:03d}",
                             artifact_sha=f"{index:040x}",
                             terminal=b" " * L1_MAX_RECORD_BYTES)
                       for index in range(L1_MAX_SNAPSHOT_BYTES // L1_MAX_RECORD_BYTES + 1)]),
    )
    for snapshot in invalid:
        with pytest.raises(PerformanceObservationError, match="bound"):
            l1_data(snapshot)
    for selectors in (["TASK-101"] * 2, [f"TASK-{index}" for index in range(33)],
                      ["TASK-" + "x" * 129], ["TASK-101@4"]):
        with pytest.raises(PerformanceObservationError):
            build_l1_baseline_readiness(l1_snapshot(item), task_ids=selectors)


def test_l1_rejects_unbound_summary_and_conflicting_declared_coverage() -> None:
    summary = build_performance_observation(l1_snapshot(terminal("RUN-101-001"))).as_dict()
    summary["coverage"]["token_usage_samples"] = 999
    summary["durations"]["executor_seconds"]["sample_count"] = 999
    # Historical structural decoding does not authenticate counters. L1 never
    # accepts a detached v1 summary (even one decoded successfully) as its source.
    for source in (summary, validate_performance_observation({
        **summary, "durations": {
            name: {"sample_count": 0, "p50": None, "p95": None}
            for name in summary["durations"]
        },
    })):
        with pytest.raises(PerformanceObservationError, match="canonical terminal snapshot"):
            build_l1_baseline_readiness(source, task_ids=["TASK-101"])


def test_l1_preserves_historical_v1_bytes_and_older_observation_semantics() -> None:
    item = terminal("RUN-101-001", observation=observed("RUN-101-001"))
    snapshot = l1_snapshot(item)
    before = build_performance_observation(snapshot).render()
    l1_data(snapshot)
    assert build_performance_observation(snapshot).render() == before
    assert validate_performance_observation(json.loads(before)).render() == before
    data = json.loads(before)
    assert set(data) == {"format", "version", "kind", "task_selectors", "coverage", "counts",
                         "durations", "duration_summaries", "failure_rate", "verification_share",
                         "token_usage", "soft_budget"}
    assert "soft_budget" not in json.loads(item.observation)  # legitimate older sidecar
    assert l1_data(snapshot)["metrics"]["executor_seconds"]["observed_sum"] == 4.0
    invalid = replace(item, observation=b'{"kind":"RUN_OBSERVATION","version":999}')
    for builder in (build_performance_observation,
                    lambda value: build_l1_baseline_readiness(value, task_ids=["TASK-101"])):
        with pytest.raises(PerformanceObservationError):
            builder(l1_snapshot(invalid))


def test_l1_privacy_and_effect_traps(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import importlib
    import os
    import socket
    import subprocess
    import time
    from pathlib import Path
    import aios_renew.performance_observation as performance
    import aios_renew.review_transport as transport
    import aios_renew.run_observation as run_observation
    import aios_renew.verification as verification

    item = terminal("RUN-101-001", observation=observed(
        "RUN-101-001", token_usage=TokenUsage(0, 0, 0)))
    run = json.loads(item.run)
    run["workspace"] = "C:/private/SECRET_WORKSPACE"
    package = json.loads(item.terminal)
    package["result"]["unresolved"] = ["SECRET_MODEL_RESPONSE"]
    item = replace(item, run=json.dumps(run).encode(), terminal=json.dumps(package).encode(),
                   execution_profile=b"SECRET_PROVIDER_PROFILE")
    snapshot = l1_snapshot(item)
    before = repr(snapshot)
    effects = []

    def forbidden(*args, **kwargs):
        effects.append(True)
        raise AssertionError("L1 projection attempted an external effect")

    with monkeypatch.context() as guard:
        for owner, name in (
            (builtins, "open"), (Path, "open"), (Path, "read_bytes"), (Path, "write_bytes"),
            (Path, "iterdir"), (Path, "glob"), (Path, "mkdir"), (Path, "unlink"),
            (os, "system"), (subprocess, "run"), (subprocess, "Popen"),
            (socket, "socket"), (socket, "create_connection"), (time, "monotonic"),
            (importlib, "import_module"),
            (performance, "resolve_remote_performance_snapshot"),
            (performance, "observe_performance"), (transport, "resolve_remote_performance_snapshot"),
            (transport, "_git_cmd"), (transport, "_read_lifecycle_blob"),
            (run_observation, "persist_observation"), (run_observation, "RunObservationTracker"),
        ):
            guard.setattr(owner, name, forbidden)
        # Trap verification entry points, including materialization/evidence attachment.
        for name, value in vars(verification).items():
            if callable(value) and getattr(value, "__module__", None) == verification.__name__:
                guard.setattr(verification, name, forbidden)
        rendered = build_l1_baseline_readiness(snapshot, task_ids=["TASK-101"]).render()
        assert l1_data(l1_snapshot())["coverage"]["status"] == "UNAVAILABLE"
        with pytest.raises(PerformanceObservationError):
            l1_data(replace(snapshot, observed_refs=()))
    assert effects == []
    assert repr(snapshot) == before
    assert "SECRET" not in rendered
    assert "workspace" not in rendered
    assert "claims" not in rendered
    assert "execution_profile" not in rendered
