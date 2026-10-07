"""One repository-owned selected full-suite profile and soft attention rule."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aios_renew.parallel_verification import ProbeError

FORMAT = "AIOS_VERIFICATION_PROFILE_POLICY"
OBSERVATION_FORMAT = "AIOS_SELECTED_FULL_SUITE_OBSERVATION"
PROFILE = "bounded-parallel-full-suite-v1"
COMPARABLE_TOOLCHAIN_KEYS = (
    "platform_system", "platform_machine", "python_implementation",
    "python_version", "pytest_version", "pytest_xdist_version",
)


def selected_profile_identity(policy: dict[str, Any]) -> dict[str, Any]:
    """Bind attribution to the existing Human-selected profile without selection."""
    if not isinstance(policy, dict):
        raise ProbeError("missing selected comparison profile")
    selected = policy.get("ordinary_canonical_full_suite")
    if (policy.get("format") != FORMAT or policy.get("version") != 1
            or not isinstance(selected, dict)
            or selected.get("profile") != PROFILE
            or selected.get("command") != "python scripts/aios_parallel_full_suite.py"
            or selected.get("workers") != 12 or selected.get("distribution") != "load"
            or selected.get("max_worker_restart") != 0
            or selected.get("selection_provenance") != {"authority": "HUMAN", "task_id": "TASK-231"}):
        raise ProbeError("incompatible selected comparison profile")
    return {key: selected[key] for key in (
        "profile", "workers", "distribution", "max_worker_restart", "selection_provenance")}


def load_policy(repository: Path) -> dict[str, Any]:
    path = repository / ".ai" / "verification-profiles.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProbeError("missing or malformed verification profile policy") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != 1:
        raise ProbeError("incompatible verification profile policy")
    selected = data.get("ordinary_canonical_full_suite")
    if not isinstance(selected, dict) or any((
        selected.get("profile") != PROFILE,
        selected.get("command") != "python scripts/aios_parallel_full_suite.py",
        selected.get("workers") != 12,
        selected.get("distribution") != "load",
        selected.get("max_worker_restart") != 0,
    )):
        raise ProbeError("incompatible selected verification profile")
    if selected.get("selection_provenance") != {
        "authority": "HUMAN", "task_id": "TASK-231",
    }:
        raise ProbeError("incompatible selected verification provenance")
    if "baseline" not in selected or selected["baseline"] is not None:
        raise ProbeError("selected workers=12 has no bound canonical performance baseline")
    historical = data.get("historical_workers_4")
    if not isinstance(historical, dict) or any((
        historical.get("historical_context_only") is not True,
        historical.get("workers") != 4,
        historical.get("distribution") != "load",
        historical.get("max_worker_restart") != 0,
        historical.get("provenance") != {
            "run_id": "RUN-159-008",
            "evidence_id": "RUN-159-008-V001",
            "subject_sha": "2ed725df2a965d4f1def4750678672bcef4f0b4f",
        },
    )):
        raise ProbeError("incompatible historical workers=4 context")
    baseline = historical.get("baseline")
    if not isinstance(baseline, dict) or not isinstance(baseline.get("canonical_collection"), dict):
        raise ProbeError("missing historical verification baseline")
    for key in COMPARABLE_TOOLCHAIN_KEYS:
        if not isinstance(baseline.get(key), str):
            raise ProbeError("malformed historical verification baseline identity")
    if {key: baseline[key] for key in COMPARABLE_TOOLCHAIN_KEYS} != {
        "platform_system": "Windows",
        "platform_machine": "AMD64",
        "python_implementation": "CPython",
        "python_version": "3.14.7",
        "pytest_version": "8.4.2",
        "pytest_xdist_version": "3.8.0",
    }:
        raise ProbeError("incompatible historical verification baseline identity")
    for key in ("serial_seconds", "selected_parallel_seconds", "attention_threshold_seconds"):
        value = baseline.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ProbeError("malformed historical verification baseline timing")
    if baseline.get("rule") != "HALF_MEASURED_GAIN_LOST":
        raise ProbeError("incompatible historical verification attention rule")
    if (
        baseline["serial_seconds"] != 1594.934653
        or baseline["selected_parallel_seconds"] != 1097.184058
        or baseline["attention_threshold_seconds"] != 1346.059356
        or baseline["canonical_collection"] != {
            "rule": "sorted-posix-nodeid-lf-sha256-v1",
            "count": 1825,
            "digest": "2e40a04acedcf9869e3961718337744761ee0a29f55258d2313b0a50d5fd66bb",
        }
    ):
        raise ProbeError("incompatible historical verification baseline")
    return selected


def performance_guard(
    profile: dict[str, Any], collection: dict[str, Any],
    toolchain: dict[str, str], elapsed_seconds: float,
) -> dict[str, Any]:
    # TASK-231 binds a Human policy decision, not a Runtime timing baseline.
    # Historical workers=4 data cannot establish comparability for workers=12.
    return {
        "status": "NOT_COMPARABLE",
        "rule": "HALF_MEASURED_GAIN_LOST",
        "attention_threshold_seconds": None,
        "baseline_run_id": None,
        "baseline_evidence_id": None,
    }
