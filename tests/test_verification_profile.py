"""Focused Human-selected policy and historical baseline checks."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from aios_renew.parallel_verification import ProbeError
from aios_renew.verification_profile import (
    COMPARABLE_TOOLCHAIN_KEYS, load_policy, performance_guard, selected_profile_identity,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def policy_data():
    return yaml.safe_load(
        (REPOSITORY / ".ai" / "verification-profiles.yaml").read_text(encoding="utf-8")
    )


def write_policy(repository, data):
    directory = repository / ".ai"
    directory.mkdir()
    (directory / "verification-profiles.yaml").write_text(
        yaml.safe_dump(data), encoding="utf-8",
    )


def test_selected_policy_has_human_authority_and_no_timing_baseline() -> None:
    profile = load_policy(REPOSITORY)
    assert profile["workers"] == 12
    assert profile["distribution"] == "load"
    assert profile["max_worker_restart"] == 0
    assert profile["command"] == "python scripts/aios_parallel_full_suite.py"
    assert profile["selection_provenance"] == {
        "authority": "HUMAN", "task_id": "TASK-231",
    }
    assert profile["baseline"] is None


def test_comparison_identity_reads_the_existing_human_profile_without_selecting():
    data = policy_data()
    before = deepcopy(data)
    identity = selected_profile_identity(data)
    assert identity["workers"] == 12 and identity["selection_provenance"]["authority"] == "HUMAN"
    assert data == before
    for defect in (4, 8, 16):
        changed = deepcopy(data)
        changed["ordinary_canonical_full_suite"]["workers"] = defect
        with pytest.raises(ProbeError, match="comparison profile"):
            selected_profile_identity(changed)


def test_historical_workers_four_remains_context_only() -> None:
    historical = policy_data()["historical_workers_4"]
    assert historical["historical_context_only"] is True
    assert historical["workers"] == 4
    assert historical["distribution"] == "load"
    assert historical["max_worker_restart"] == 0
    assert historical["provenance"] == {
        "run_id": "RUN-159-008", "evidence_id": "RUN-159-008-V001",
        "subject_sha": "2ed725df2a965d4f1def4750678672bcef4f0b4f",
    }
    assert historical["baseline"] == {
        "canonical_collection": {
            "rule": "sorted-posix-nodeid-lf-sha256-v1", "count": 1825,
            "digest": "2e40a04acedcf9869e3961718337744761ee0a29f55258d2313b0a50d5fd66bb",
        },
        "platform_system": "Windows", "platform_machine": "AMD64",
        "python_implementation": "CPython", "python_version": "3.14.7",
        "pytest_version": "8.4.2", "pytest_xdist_version": "3.8.0",
        "serial_seconds": 1594.934653,
        "selected_parallel_seconds": 1097.184058,
        "attention_threshold_seconds": 1346.059356,
        "rule": "HALF_MEASURED_GAIN_LOST",
    }


@pytest.mark.parametrize("elapsed", [0.0, 1097.184058, 1346.059356, 1400.0])
def test_historical_timing_never_makes_selected_profile_comparable(elapsed) -> None:
    profile = load_policy(REPOSITORY)
    baseline = policy_data()["historical_workers_4"]["baseline"]
    toolchain = {key: baseline[key] for key in COMPARABLE_TOOLCHAIN_KEYS}
    assert performance_guard(
        profile, baseline["canonical_collection"], toolchain, elapsed,
    ) == {
        "status": "NOT_COMPARABLE", "rule": "HALF_MEASURED_GAIN_LOST",
        "attention_threshold_seconds": None,
        "baseline_run_id": None, "baseline_evidence_id": None,
    }


@pytest.mark.parametrize("field,value", [
    ("workers", 4), ("workers", 8), ("workers", 16), ("workers", "auto"),
    ("distribution", "worksteal"), ("max_worker_restart", 1),
    ("command", "python scripts/aios_parallel_full_suite.py --workers 12"),
    ("selection_provenance", {"authority": "RUNTIME", "task_id": "TASK-231"}),
    ("selection_provenance", {"authority": "HUMAN", "task_id": "TASK-230"}),
    ("selection_provenance", {"run_id": "RUN-231-001"}),
])
def test_policy_rejects_alternate_profile_or_selection_provenance(tmp_path, field, value):
    data = policy_data()
    data["ordinary_canonical_full_suite"][field] = value
    write_policy(tmp_path, data)
    with pytest.raises(ProbeError):
        load_policy(tmp_path)


def test_policy_rejects_historical_baseline_as_selected_baseline(tmp_path):
    data = policy_data()
    data["ordinary_canonical_full_suite"]["baseline"] = deepcopy(
        data["historical_workers_4"]["baseline"]
    )
    write_policy(tmp_path, data)
    with pytest.raises(ProbeError, match="no bound canonical performance baseline"):
        load_policy(tmp_path)


def test_policy_rejects_modified_historical_timing(tmp_path):
    data = policy_data()
    data["historical_workers_4"]["baseline"]["selected_parallel_seconds"] = 1.0
    write_policy(tmp_path, data)
    with pytest.raises(ProbeError, match="historical verification baseline"):
        load_policy(tmp_path)
