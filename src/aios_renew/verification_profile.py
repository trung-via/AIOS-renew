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
        selected.get("workers") != 4,
        selected.get("distribution") != "load",
        selected.get("max_worker_restart") != 0,
    )):
        raise ProbeError("incompatible selected verification profile")
    provenance = selected.get("selection_provenance")
    if not isinstance(provenance, dict) or provenance != {
        "run_id": "RUN-159-008",
        "evidence_id": "RUN-159-008-V001",
        "subject_sha": "2ed725df2a965d4f1def4750678672bcef4f0b4f",
    }:
        raise ProbeError("incompatible selected verification provenance")
    baseline = selected.get("baseline")
    if not isinstance(baseline, dict) or not isinstance(baseline.get("canonical_collection"), dict):
        raise ProbeError("missing selected verification baseline")
    for key in COMPARABLE_TOOLCHAIN_KEYS:
        if not isinstance(baseline.get(key), str):
            raise ProbeError("malformed selected verification baseline identity")
    if {key: baseline[key] for key in COMPARABLE_TOOLCHAIN_KEYS} != {
        "platform_system": "Windows",
        "platform_machine": "AMD64",
        "python_implementation": "CPython",
        "python_version": "3.14.7",
        "pytest_version": "8.4.2",
        "pytest_xdist_version": "3.8.0",
    }:
        raise ProbeError("incompatible selected verification baseline identity")
    for key in ("serial_seconds", "selected_parallel_seconds", "attention_threshold_seconds"):
        value = baseline.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ProbeError("malformed selected verification baseline timing")
    if baseline.get("rule") != "HALF_MEASURED_GAIN_LOST":
        raise ProbeError("incompatible selected verification attention rule")
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
        raise ProbeError("incompatible selected verification baseline")
    return selected


def performance_guard(
    profile: dict[str, Any], collection: dict[str, Any],
    toolchain: dict[str, str], elapsed_seconds: float,
) -> dict[str, Any]:
    baseline = profile["baseline"]
    comparable = (
        collection == baseline["canonical_collection"]
        and all(toolchain.get(key) == baseline[key] for key in COMPARABLE_TOOLCHAIN_KEYS)
        and profile["workers"] == 4
        and profile["distribution"] == "load"
        and profile["max_worker_restart"] == 0
    )
    status = (
        "NOT_COMPARABLE" if not comparable else
        "ATTENTION" if elapsed_seconds > baseline["attention_threshold_seconds"] else
        "WITHIN"
    )
    return {
        "status": status,
        "rule": baseline["rule"],
        "attention_threshold_seconds": baseline["attention_threshold_seconds"],
        "baseline_run_id": profile["selection_provenance"]["run_id"],
        "baseline_evidence_id": profile["selection_provenance"]["evidence_id"],
    }
