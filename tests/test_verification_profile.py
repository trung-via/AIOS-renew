"""Focused selected-profile policy and historical-baseline checks."""

from pathlib import Path

from aios_renew.verification_profile import load_policy, performance_guard


def test_selected_policy_has_no_comparable_twelve_worker_baseline() -> None:
    profile = load_policy(Path(__file__).resolve().parents[1])
    assert profile["workers"] == 12
    assert profile["distribution"] == "load"
    assert profile["max_worker_restart"] == 0
    assert profile["selection_provenance"] == {
        "authority": "HUMAN", "task_id": "TASK-231",
    }
    assert profile["selected_baseline"] is None
    baseline = profile["baseline"]
    assert baseline["context"] == "historical-workers-4-only"
    assert baseline["workers"] == 4
    assert baseline["run_id"] == "RUN-159-008"
    assert baseline["evidence_id"] == "RUN-159-008-V001"
    collection = baseline["canonical_collection"]
    toolchain = {key: baseline[key] for key in (
        "platform_system", "platform_machine", "python_implementation",
        "python_version", "pytest_version", "pytest_xdist_version",
    )}
    threshold = baseline["attention_threshold_seconds"]
    for observed_collection, observed_toolchain in (
        (collection, toolchain),
        ({**collection, "count": collection["count"] + 1}, toolchain),
        (collection, {**toolchain, "pytest_version": "8.4.3"}),
    ):
        guard = performance_guard(
            profile, observed_collection, observed_toolchain, threshold + 1,
        )
        assert guard == {
            "status": "NOT_COMPARABLE",
            "rule": None,
            "attention_threshold_seconds": None,
            "baseline_run_id": None,
            "baseline_evidence_id": None,
        }
