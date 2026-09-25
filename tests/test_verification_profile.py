"""Focused selected-profile policy and soft-attention checks."""

from pathlib import Path

from aios_renew.verification_profile import load_policy, performance_guard


def test_selected_policy_and_strict_soft_boundary() -> None:
    profile = load_policy(Path(__file__).resolve().parents[1])
    baseline = profile["baseline"]
    collection = baseline["canonical_collection"]
    toolchain = {key: baseline[key] for key in (
        "platform_system", "platform_machine", "python_implementation",
        "python_version", "pytest_version", "pytest_xdist_version",
    )}
    threshold = baseline["attention_threshold_seconds"]
    assert performance_guard(profile, collection, toolchain, threshold)["status"] == "WITHIN"
    assert performance_guard(profile, collection, toolchain, threshold + 0.000001)["status"] == "ATTENTION"
    assert performance_guard(profile, collection, {**toolchain, "pytest_version": "8.4.3"}, threshold + 1)["status"] == "NOT_COMPARABLE"
    assert performance_guard(profile, {**collection, "count": collection["count"] + 1}, toolchain, threshold + 1)["status"] == "NOT_COMPARABLE"
