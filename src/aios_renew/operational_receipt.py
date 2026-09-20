"""Bounded Operational Receipt v2 projections for remote AIOS handoffs.

Receipts are read-only, subordinate observations.  They neither reconcile a
dispatch nor create, complete, or reinterpret canonical lifecycle artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


FAMILIES = frozenset({"PRIMARY", "REMEDIATION", "REPAIR"})
BOUNDARIES = frozenset({
    "CARRIER_ADMITTED",
    "DISPATCH_REQUEST_ACCEPTED",
    "RUNNER_STARTED",
    "AIOS_INVOKED",
    "ADMISSION_ACCEPTED",
    "ADMISSION_REJECTED",
    "RUN_ATTRIBUTED",
    "OPERATIONAL_FAILED",
    "TERMINAL_POINTER",
    "SELF_HOST_COMPLETED",
})
WORKFLOW_REASONS = frozenset({
    "AIOS_REPO_ROOT_NOT_CONFIGURED",
    "CONFIGURED_REPOSITORY_UNAVAILABLE",
    "CONFIGURED_REPOSITORY_INVALID",
    "AIOS_COMMAND_UNAVAILABLE",
    "INVALID_BOUNDED_INPUT",
    "UNEXPECTED_REPOSITORY_IDENTITY",
    "NONCANONICAL_WORKFLOW_REF",
    "UNAUTHORIZED_DELIVERY_ACTOR",
    "DOWNSTREAM_WORKFLOW_FAILED",
})
_DELIVERY_FIELDS = {
    "PRIMARY": ("dispatch_id", "dispatches"),
    "REMEDIATION": ("correction_dispatch_id", "correction-dispatches"),
    "REPAIR": ("repair_dispatch_id", "repair-dispatches"),
}
_DELIVERY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_RUN_PATTERN = re.compile(r"^RUN-[A-Za-z0-9_-]+-\d{3,}$")
_SELECTOR_FIELDS = frozenset({
    "task_id", "task_revision", "task_blob_sha", "task_commit_sha", "executor",
    "source_run_id", "finding_id", "failed_run_id", "repair_sha",
})


class OperationalReceiptError(RuntimeError):
    """The exact delivery cannot be projected safely."""


@dataclass(frozen=True)
class OperationalReceipt:
    """One bounded statement of the highest directly proven boundary."""

    family: str
    delivery_id: str
    boundary: str
    selectors: Mapping[str, Any]
    run_created: bool = False
    executor_invoked: bool = False
    cause: Mapping[str, str] | None = None
    run_id: str | None = None
    terminal_pointer: Mapping[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        _validate_family_delivery(self.family, self.delivery_id)
        if self.boundary not in BOUNDARIES:
            raise OperationalReceiptError("invalid operational boundary")
        selectors = _bounded_selectors(self.selectors)
        delivery_field = _DELIVERY_FIELDS[self.family][0]
        payload: dict[str, Any] = {
            "format": "AIOS_OPERATIONAL_RECEIPT",
            "version": 2,
            "kind": "OPERATIONAL_RECEIPT",
            "family": self.family,
            "delivery": {"kind": delivery_field, "id": self.delivery_id},
            "boundary": self.boundary,
            "selectors": selectors,
            "run_created": self.run_created,
            "executor_invoked": self.executor_invoked,
        }
        if self.cause is not None:
            authority = self.cause.get("authority")
            phase = self.cause.get("phase")
            reason = self.cause.get("reason_code")
            if (
                authority not in {"WORKFLOW", "AIOS_ADMISSION_FAILURE", "CORRECTION_PREFLIGHT", "CARRIER"}
                or not isinstance(phase, str) or not phase or len(phase) > 64
                or not isinstance(reason, str) or not reason or len(reason) > 128
            ):
                raise OperationalReceiptError("invalid bounded operational cause")
            payload["cause"] = {
                "authority": authority,
                "phase": phase,
                "reason_code": reason,
            }
        if self.run_id is not None:
            if not _RUN_PATTERN.fullmatch(self.run_id):
                raise OperationalReceiptError("invalid attributed RUN identity")
            if self.boundary != "TERMINAL_POINTER":
                payload["run_id"] = self.run_id
        if self.terminal_pointer is not None:
            pointer = dict(self.terminal_pointer)
            if (
                pointer.get("kind") not in {"RESULT", "FAILURE"}
                or pointer.get("run_id") != self.run_id
                or not isinstance(pointer.get("artifact"), str)
                or len(pointer["artifact"]) > 512
            ):
                raise OperationalReceiptError("invalid canonical terminal pointer")
            payload["terminal_pointer"] = pointer
        return payload

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def workflow_failure_receipt(
    family: str,
    delivery_id: str,
    reason_code: str,
    *,
    selectors: Mapping[str, Any] | None = None,
) -> OperationalReceipt:
    """Describe a deterministic failure before the bounded AIOS command ran."""

    if reason_code not in WORKFLOW_REASONS:
        raise OperationalReceiptError("invalid workflow-owned operational reason")
    return OperationalReceipt(
        family=family,
        delivery_id=delivery_id,
        boundary="OPERATIONAL_FAILED",
        selectors=selectors or {},
        cause={
            "authority": "WORKFLOW",
            "phase": "PRE_AIOS",
            "reason_code": reason_code,
        },
        run_created=False,
        executor_invoked=False,
    )


def invoked_receipt(
    family: str,
    delivery_id: str,
    *,
    selectors: Mapping[str, Any] | None = None,
) -> OperationalReceipt:
    """Record only that the bounded AIOS command was invoked."""

    return OperationalReceipt(
        family=family,
        delivery_id=delivery_id,
        boundary="AIOS_INVOKED",
        selectors=selectors or {},
    )


def correction_preflight_receipt(
    family: str,
    delivery_id: str,
    preflight: Mapping[str, Any],
    *,
    selectors: Mapping[str, Any] | None = None,
) -> OperationalReceipt:
    """Project the exact blocked correction preflight returned by this call."""

    if family not in {"REMEDIATION", "REPAIR"} or preflight.get("status") != "BLOCKED":
        raise OperationalReceiptError("correction preflight is not a blocked delivery")
    phase = preflight.get("phase")
    reason = preflight.get("reason_code")
    if not isinstance(phase, str) or not isinstance(reason, str):
        raise OperationalReceiptError("correction preflight has no typed cause")
    return OperationalReceipt(
        family=family,
        delivery_id=delivery_id,
        boundary="ADMISSION_REJECTED",
        selectors=selectors or {},
        cause={
            "authority": "CORRECTION_PREFLIGHT",
            "phase": phase,
            "reason_code": reason,
        },
    )


def project_delivery_receipt(
    state_root: Path,
    *,
    family: str,
    delivery_id: str,
    selectors: Mapping[str, Any] | None = None,
) -> OperationalReceipt:
    """Read the exact family journal and project its strongest proven fact.

    No timestamp, filename ordering, metadata-only RUN matching, or journal
    reconciliation is performed.
    """

    _validate_family_delivery(family, delivery_id)
    bounded = _bounded_selectors(selectors or {})
    delivery_field, directory = _DELIVERY_FIELDS[family]
    key = hashlib.sha256(delivery_id.encode("ascii")).hexdigest()
    path = state_root / directory / f"{key}.json"
    if not path.is_file():
        return invoked_receipt(family, delivery_id, selectors=bounded)
    record = _read_mapping(path, "dispatch journal")
    if record.get(delivery_field) != delivery_id:
        raise OperationalReceiptError("dispatch journal identity collision")
    for name, expected in bounded.items():
        if name in record and record.get(name) != expected:
            raise OperationalReceiptError("dispatch journal selector collision")

    run_id = record.get("run_id")
    if run_id is None:
        cause = _exact_admission_cause(
            state_root / "admission-failures", family, delivery_field, delivery_id
        )
        if cause is not None:
            return OperationalReceipt(
                family=family,
                delivery_id=delivery_id,
                boundary="ADMISSION_REJECTED",
                selectors=bounded,
                cause=cause,
            )
        return invoked_receipt(family, delivery_id, selectors=bounded)
    if not isinstance(run_id, str) or not _RUN_PATTERN.fullmatch(run_id):
        raise OperationalReceiptError("dispatch journal has invalid RUN attribution")

    result_path = state_root / "results" / f"{run_id}.json"
    failure_path = state_root / "failures" / f"{run_id}.json"
    if result_path.is_file() and failure_path.is_file():
        raise OperationalReceiptError("attributed RUN has conflicting terminals")
    if result_path.is_file() or failure_path.is_file():
        kind = "RESULT" if result_path.is_file() else "FAILURE"
        artifact = f".git/aios/{kind.lower()}s/{run_id}.json"
        return OperationalReceipt(
            family=family,
            delivery_id=delivery_id,
            boundary="TERMINAL_POINTER",
            selectors=bounded,
            run_created=True,
            executor_invoked=False,
            run_id=run_id,
            terminal_pointer={"kind": kind, "run_id": run_id, "artifact": artifact},
        )
    return OperationalReceipt(
        family=family,
        delivery_id=delivery_id,
        boundary="RUN_ATTRIBUTED",
        selectors=bounded,
        run_created=True,
        executor_invoked=False,
        run_id=run_id,
    )


def new_admission_blocker(
    admission_failures: Path,
    *,
    before: Iterable[str],
    operation: str,
    exact_facts: Mapping[str, Any],
) -> Mapping[str, str] | None:
    """Return one typed diagnostic created by this exact delegated call."""

    prior = frozenset(before)
    matches: list[Mapping[str, Any]] = []
    if not admission_failures.is_dir():
        return None
    for path in admission_failures.glob("*.json"):
        if path.name in prior:
            continue
        try:
            payload = _read_mapping(path, "admission failure")
        except OperationalReceiptError:
            continue
        if (
            payload.get("format") == "AIOS_ADMISSION_FAILURE"
            and payload.get("version") == 2
            and payload.get("operation") == operation
            and all(payload.get(name) == value for name, value in exact_facts.items())
        ):
            matches.append(payload)
    if len(matches) != 1:
        return None
    phase = matches[0].get("phase")
    reason = matches[0].get("reason_code")
    if not isinstance(phase, str) or not isinstance(reason, str):
        return None
    return {
        "code": reason,
        "phase": phase,
        "reason_code": reason,
        "source": "AIOS_ADMISSION_FAILURE",
    }


def write_receipt(path: str | Path, receipt: OperationalReceipt) -> None:
    """Atomically replace one workflow-run receipt outside canonical state."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(receipt.render(), encoding="utf-8", newline="")
    os.replace(temporary, target)


def write_environment_receipt(receipt: OperationalReceipt) -> None:
    path = os.environ.get("AIOS_OPERATIONAL_RECEIPT_PATH")
    if path:
        write_receipt(path, receipt)


def _validate_family_delivery(family: str, delivery_id: str) -> None:
    if family not in FAMILIES:
        raise OperationalReceiptError("invalid operational family")
    if not isinstance(delivery_id, str) or not _DELIVERY_PATTERN.fullmatch(delivery_id):
        raise OperationalReceiptError("invalid operational delivery identity")


def _bounded_selectors(selectors: Mapping[str, Any]) -> dict[str, Any]:
    if any(name not in _SELECTOR_FIELDS for name in selectors):
        raise OperationalReceiptError("unbounded operational selector")
    bounded: dict[str, Any] = {}
    for name, value in selectors.items():
        if value is None:
            continue
        if name == "task_revision":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise OperationalReceiptError("invalid operational selector")
        elif not isinstance(value, str) or not value or len(value) > 256:
            raise OperationalReceiptError("invalid operational selector")
        bounded[name] = value
    return bounded


def _read_mapping(path: Path, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OperationalReceiptError(f"invalid {label}") from exc
    if not isinstance(payload, Mapping):
        raise OperationalReceiptError(f"invalid {label}")
    return payload


def _exact_admission_cause(
    directory: Path, family: str, delivery_field: str, delivery_id: str
) -> Mapping[str, str] | None:
    matches: list[Mapping[str, Any]] = []
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                payload = _read_mapping(path, "admission failure")
            except OperationalReceiptError:
                continue
            if (
                payload.get("format") == "AIOS_ADMISSION_FAILURE"
                and payload.get("version") == 2
                and payload.get("operation") == family
                and payload.get(delivery_field) == delivery_id
            ):
                matches.append(payload)
    if len(matches) != 1:
        return None
    phase = matches[0].get("phase")
    reason = matches[0].get("reason_code")
    if not isinstance(phase, str) or not isinstance(reason, str):
        return None
    return {
        "authority": "AIOS_ADMISSION_FAILURE",
        "phase": phase,
        "reason_code": reason,
    }


__all__ = [
    "BOUNDARIES",
    "OperationalReceipt",
    "OperationalReceiptError",
    "correction_preflight_receipt",
    "invoked_receipt",
    "new_admission_blocker",
    "project_delivery_receipt",
    "workflow_failure_receipt",
    "write_environment_receipt",
    "write_receipt",
]
