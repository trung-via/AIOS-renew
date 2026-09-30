"""Focused Human-selected profile and historical baseline separation checks."""

from pathlib import Path

import pytest
import yaml

from aios_renew.parallel_verification import ProbeError
from aios_renew.verification_profile import load_policy, performance_guard


REPOSITORY = Path(__file__).resolve().parents[1]


def policy_data():
    return yaml.safe_load(
        (REPOSITORY / ".ai" / "verification-profiles.yaml").read_text(encoding="utf-8")
    )


def test_selected_policy_records_human_authority_without_runtime_evidence() -> None:
    profile = load_policy(REPOSITORY)
    assert profile["workers"] == 12
    assert profile["distribution"] == "load"
    assert profile["max_worker_restart"] == 0
    assert profile["command"] == "python scripts/aios_parallel_full_suite.py"
    assert profile["selection_provenance"] == {"authority": "HUMAN", "task_id": "TASK-231"}
    assert profile["baseline"] is None


@pytest.mark.parametrize("elapsed", [0.001, 1346.059356, 1346.059357, 100000.0])
def test_historical_identity_and_timing_never_supply_selected_baseline(elapsed) -> None:
    profile = load_policy(REPOSITORY)
    historical = policy_data()["historical_full_suite"]
    assert historical["workers"] == 4
    assert historical["measurement_provenance"] == {
        "run_id": "RUN-159-008", "evidence_id": "RUN-159-008-V001",
        "subject_sha": "2ed725df2a965d4f1def4750678672bcef4f0b4f",
    }
    baseline = historical["baseline"]
    assert baseline["serial_seconds"] == 1594.934653
    assert baseline["selected_parallel_seconds"] == 1097.184058
    assert baseline["attention_threshold_seconds"] == 1346.059356
    assert baseline["canonical_collection"] == {
        "rule": "sorted-posix-nodeid-lf-sha256-v1", "count": 1825,
        "digest": "2e40a04acedcf9869e3961718337744761ee0a29f55258d2313b0a50d5fd66bb",
    }
    guard = performance_guard(profile, baseline["canonical_collection"], baseline, elapsed)
    assert guard == {
        "status": "NOT_COMPARABLE", "rule": "HALF_MEASURED_GAIN_LOST",
        "attention_threshold_seconds": None,
        "baseline_run_id": None, "baseline_evidence_id": None,
    }


@pytest.mark.parametrize("defect", [
    "workers", "distribution", "restart", "authority", "task", "runtime_provenance",
    "baseline", "missing_baseline", "historical_timing", "historical_provenance",
])
def test_policy_rejects_alternate_selection_or_relabelled_history(tmp_path, defect) -> None:
    data = policy_data()
    profile = data["ordinary_canonical_full_suite"]
    if defect == "workers":
        profile["workers"] = 4
    elif defect == "distribution":
        profile["distribution"] = "worksteal"
    elif defect == "restart":
        profile["max_worker_restart"] = 1
    elif defect == "authority":
        profile["selection_provenance"]["authority"] = "RUNTIME"
    elif defect == "task":
        profile["selection_provenance"]["task_id"] = "TASK-230"
    elif defect == "runtime_provenance":
        profile["selection_provenance"]["run_id"] = "RUN-231-001"
    elif defect == "baseline":
        profile["baseline"] = data["historical_full_suite"]["baseline"]
    elif defect == "missing_baseline":
        del profile["baseline"]
    elif defect == "historical_timing":
        data["historical_full_suite"]["baseline"]["selected_parallel_seconds"] = 1.0
    else:
        data["historical_full_suite"]["measurement_provenance"]["run_id"] = "RUN-231-001"
    (tmp_path / ".ai").mkdir()
    (tmp_path / ".ai" / "verification-profiles.yaml").write_text(
        yaml.safe_dump(data), encoding="utf-8"
    )
    with pytest.raises(ProbeError):
        load_policy(tmp_path)
