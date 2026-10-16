"""Authoritative Unified State + Next Action reduction boundary for AIOS-renew.

Reduces the exact current TASK lifecycle and computes next action without acquiring
mutation authority, creating RUNs, invoking Executors, running verification,
mutating runtime state, or reconciling repository state.
"""

from __future__ import annotations

from .return_affinity import document_affinity, require_same_affinity

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import aios_renew.publication as publication_module
from .artifacts import (
    ArtifactValidationError,
    ResultPackage,
    validate_evidence,
    validate_result,
    validate_result_package,
)
from .correction_preflight import (
    CorrectionPreflightResult,
    preflight_remediation,
    preflight_repair,
)
from .correction_frontier import CorrectionFrontier, CorrectionFrontierError
from .review import (
    Review,
    ReviewValidationError,
    parse_review,
    validate_review,
)
from .review_transport import (
    RemoteLifecycleReview,
    RemoteLifecycleTerminal,
    RemoteQueryError,
    RemoteTaskLifecycle,
    ReviewTransportError,
    resolve_remote_primary_recovery,
    resolve_remote_task_lifecycle,
    resolve_transport_remote,
    task_run_prefix,
    validate_runtime_failure_binding,
)
from .run import Run
from .task import Task
from .verification_contract import (
    MINIMUM_SUFFICIENT_V2,
    validate_evidence_binding,
    validate_observation,
    verification_digest,
)


_UNIFIED_NEXT_ACTIONS = frozenset({
    "EXECUTE_PRIMARY", "WAIT", "SEMANTIC_REVIEW", "AUTHOR_REMEDIATION",
    "EXECUTE_REMEDIATION", "AUTHOR_REPAIR", "EXECUTE_REPAIR",
    "RETRY_TRANSPORT", "RECOVER_PRIMARY", "PUBLICATION", "DONE", "NONE",
})

_UNIFIED_AUTHORITIES = {
    "EXECUTE_PRIMARY": "HUMAN_RUNTIME",
    "WAIT": "NONE",
    "SEMANTIC_REVIEW": "REVIEWER",
    "AUTHOR_REMEDIATION": "BRAIN",
    "EXECUTE_REMEDIATION": "HUMAN_RUNTIME",
    "AUTHOR_REPAIR": "BRAIN",
    "EXECUTE_REPAIR": "HUMAN_RUNTIME",
    "RETRY_TRANSPORT": "HUMAN_RUNTIME",
    "RECOVER_PRIMARY": "HUMAN_RUNTIME",
    "PUBLICATION": "PUBLICATION",
    "DONE": "NONE",
    "NONE": "NONE",
}

_OUTSTANDING_FINDING_LIMIT = 32


class CumulativeExecutionBase(tuple):
    run_id: str
    candidate_sha: str
    integrated_base: Mapping[str, Any] | None

    def __new__(
        cls,
        run_id: str,
        candidate_sha: str,
        integrated_base: Mapping[str, Any] | None = None,
    ):
        obj = super().__new__(cls, (run_id, candidate_sha))
        obj.run_id = run_id
        obj.candidate_sha = candidate_sha
        obj.integrated_base = integrated_base
        return obj


@dataclass(frozen=True)
class UnifiedStateObservation:
    """Versioned, bounded and mutation-free lifecycle reduction."""

    task_id: str
    task_revision: int
    lifecycle_state: str
    next_action: str
    run_id: str | None = None
    source_run_id: str | None = None
    failed_run_id: str | None = None
    review_id: str | None = None
    finding_id: str | None = None
    candidate_sha: str | None = None
    reviewed_sha: str | None = None
    failed_head_sha: str | None = None
    correction_sha: str | None = None
    execution_base_run_id: str | None = None
    execution_base_sha: str | None = None
    integrated_base: Mapping[str, Any] | None = None
    correction: Mapping[str, Any] | None = None
    # Exact repair content is retained only for the immediate admission handoff.
    # It is deliberately excluded from the Human/state renderings.
    correction_document: Mapping[str, Any] | None = None
    blocker: Mapping[str, Any] | None = None
    outstanding_findings: tuple[Mapping[str, str], ...] = ()
    admission_failures: tuple[Mapping[str, Any], ...] = ()
    failure_context: Mapping[str, Any] | None = None
    # Private handoff from the one canonical reduction to the read-only scope.
    _scope_context: _SemanticScopeContext | None = field(
        default=None, repr=False, compare=False
    )

    def as_dict(self) -> dict[str, Any]:
        if self.next_action not in _UNIFIED_NEXT_ACTIONS:
            raise ValueError("invalid Unified State next action")
        return {
            "format": "AIOS_UNIFIED_STATE",
            "version": 1,
            "kind": "UNIFIED_STATE",
            "task": {"id": self.task_id, "revision": self.task_revision},
            "lifecycle_state": self.lifecycle_state,
            "next_action": self.next_action,
            "authority": _UNIFIED_AUTHORITIES[self.next_action],
            "run_id": self.run_id,
            "source_run_id": self.source_run_id,
            "failed_run_id": self.failed_run_id,
            "review_id": self.review_id,
            "finding_id": self.finding_id,
            "candidate_sha": self.candidate_sha,
            "reviewed_sha": self.reviewed_sha,
            "failed_head_sha": self.failed_head_sha,
            "correction_sha": self.correction_sha,
            "execution_base": (
                dict(self.integrated_base)
                if self.integrated_base is not None
                else (
                    {
                        "run_id": self.execution_base_run_id,
                        "candidate_sha": self.execution_base_sha,
                    }
                    if self.execution_base_run_id is not None
                    and self.execution_base_sha is not None
                    else None
                )
            ),
            "correction_preflight": (
                dict(self.correction) if self.correction is not None else None
            ),
            "outstanding_findings": [dict(item) for item in self.outstanding_findings],
            "blocker": dict(self.blocker) if self.blocker is not None else None,
            "admission_failures": [dict(item) for item in self.admission_failures],
            "failure_context": dict(self.failure_context) if self.failure_context is not None else None,
            "run_created": False,
            "executor_invoked": False,
            "verification_invoked": False,
            "state_mutated": False,
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def _operator():
    import aios_renew.operator as op

    return op


@dataclass(frozen=True)
class _LifecycleRun:
    run_id: str
    family: str
    run: Run
    candidate_sha: str
    terminal_kind: str
    terminal: Mapping[str, Any]
    parent_run_id: str | None = None
    review_id: str | None = None
    finding_id: str | None = None
    candidate_available: bool = True
    correction: Mapping[str, Any] | None = None
    run_document: Mapping[str, Any] | None = None
    semantic_predecessor: Any | None = None
    execution_base_run_id: str | None = None
    execution_base: Any | None = None
    publication_recovery: Mapping[str, Any] | None = None
    transport_blocker: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class _SemanticScopeContext:
    tip: _LifecycleRun
    lifecycle: tuple[_LifecycleRun, ...]
    reviews: Mapping[str, Review]


def _unified_blocked(
    task: Task,
    code: str,
    *,
    admission_failures: tuple[Mapping[str, Any], ...],
    phase: str | None = None,
    reason_code: str | None = None,
    blocker_details: Mapping[str, Any] | None = None,
    **facts: Any,
) -> UnifiedStateObservation:
    blocker: dict[str, Any] = {"code": code}
    if blocker_details is not None:
        blocker.update(blocker_details)
    if phase is not None:
        blocker["phase"] = phase
    if reason_code is not None:
        blocker["reason_code"] = reason_code
    return UnifiedStateObservation(
        task.task_id,
        task.revision,
        "BLOCKED",
        "NONE",
        blocker=blocker,
        admission_failures=admission_failures,
        **facts,
    )


def _bounded_admission_context(
    state: Any, task: Task
) -> tuple[Mapping[str, Any], ...]:
    """Return structural v2 context without interpreting historical prose."""

    contexts: set[tuple[str, str, str, str]] = set()
    op = _operator()
    if not state.admission_failures.is_dir():
        return ()
    for path in state.admission_failures.glob("*.json"):
        try:
            with path.open("rb") as stream:
                original = stream.read(1024 * 1024 + 1)
            if len(original) > 1024 * 1024:
                continue
            from .review_transport import _public_document
            value = _public_document(original)
        except (OSError, UnicodeError, json.JSONDecodeError, ReviewTransportError):
            continue
        if not isinstance(value, Mapping):
            continue
        bound = value.get("task")
        requested = value.get("requested_task_id")
        exact = (
            isinstance(bound, Mapping)
            and bound.get("id") == task.task_id
            and type(bound.get("revision")) is int
            and bound.get("revision") == task.revision
        ) or (bound is None and requested == task.task_id)
        identity = (value.get("operation"), value.get("phase"), value.get("reason_code"))
        if (
            value.get("format") == "AIOS_ADMISSION_FAILURE"
            and type(value.get("version")) is int and value.get("version") == 2
            and value.get("kind") == "ADMISSION_FAILURE" and value.get("executor_invoked") is False
            and value.get("run_id") is None and ("run_created" not in value or value["run_created"] is False)
            and exact
            and all(isinstance(item, str) for item in identity)
            and identity[0] in op._ADMISSION_OPERATIONS
            and identity[1] in (op._ADMISSION_PHASES | {"MIGRATION_PRE_HANDOFF"})
            and identity[2] in (op._ADMISSION_REASONS | {"MIGRATION_PRE_HANDOFF_REJECTED"})
        ):
            contexts.add((*identity, hashlib.sha256(original).hexdigest()))
    return tuple(
        {"operation": operation, "phase": phase, "reason_code": reason, "source_digest": source_digest,
         "run_created": False, "executor_invoked": False}
        for operation, phase, reason, source_digest in sorted(contexts)[:32]
    )


def _lifecycle_document(
    raw: bytes, name: str, *, private_original: bool = False,
) -> Mapping[str, Any]:
    # Historical readers must not inherit the prospective public writer's size cap.
    if name == "FAILURE":
        from .review_transport import failure_document
        return failure_document(raw, private_original=private_original)
    data = publication_module._mapping(
        publication_module._json_no_duplicates(raw, document=name), name
    )
    if data.get("kind") == "FAILURE":
        from .review_transport import failure_document
        return failure_document(raw, private_original=private_original)
    return data


def _validated_private_public_view(
    private: Mapping[str, Any],
    kind: str,
) -> Mapping[str, Any]:
    """Validate private privacy roles and produce the bounded read projection.

    This does not establish canonical equality or transport success. Pending local
    records need the same role validation independently of a public counterpart.
    The historical read projection deliberately has no writer input cap.
    """

    def evidence(item: dict[str, Any], owner: Mapping[str, Any] | None = None) -> None:
        validate_evidence(item)
        identity, raw, outcome = item["evidence_id"], item["raw"], item["result"]
        if (not isinstance(identity, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identity) is None
                or not isinstance(raw, dict) or set(raw) != {"path"}
                or not isinstance(raw["path"], str) or not raw["path"]
                or not isinstance(outcome, dict) or not isinstance(outcome.get("summary"), str)
                or type(outcome.get("exit_code")) is not int
                or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", item["subject_sha"]) is None):
            raise ValueError("invalid private EVIDENCE privacy fields")
        if owner is not None and (
            item["run_id"] != owner.get("run_id")
            or item["subject_sha"] != owner.get("failed_head_sha")
        ):
            raise ValueError("full EVIDENCE does not bind its FAILURE owner")
        item["raw"] = {"path": "protected-runtime-raw/" + identity}
        outcome["summary"] = "Runtime verification exit code " + str(outcome["exit_code"])

    def workspace(item: dict[str, Any]) -> None:
        if not isinstance(item.get("workspace"), str) or not item["workspace"].strip():
            raise ValueError("invalid private RUN workspace")
        item["workspace"] = "protected-runtime-workspace"

    def reference(item: dict[str, Any], owner: Mapping[str, Any]) -> None:
        # Only FAILURE.error.verification[].verification grants this role. It is
        # an observation reference, never an incomplete full EVIDENCE object.
        identity = item.get("evidence_id")
        if (item.get("policy") != MINIMUM_SUFFICIENT_V2
                or not isinstance(identity, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identity) is None
                or set(item).intersection({"raw", "result", "source", "subject_sha", "type"})
                or not isinstance(item.get("run_id"), str) or not item["run_id"]):
            raise ValueError("invalid verification reference role")
        verification_digest(item)
        for prefix in ("", "base_"):
            path_key, digest_key = prefix + "raw_path", prefix + "raw_digest"
            if prefix and not ({path_key, digest_key} & item.keys()):
                continue
            if (not isinstance(item.get(path_key), str) or not item[path_key]
                    or not isinstance(item.get(digest_key), str)
                    or re.fullmatch(r"[0-9a-f]{64}", item[digest_key]) is None):
                raise ValueError("invalid verification reference raw metadata")
        binding = validate_evidence_binding(item.get("binding"))

        source_profile_fields = {"observer_blob", "selected_policy_blob", "repository_tool_blobs"}

        def profile(value: Any) -> dict[str, Any]:
            if not isinstance(value, dict) or not value:
                raise ValueError("invalid verification reference profile")
            # Historical matching profiles remain readable. Source-bound Runtime
            # profiles must retain their own identities and execution facts.
            if source_profile_fields & value.keys():
                required = source_profile_fields | {
                    "profile", "workers", "distribution", "max_worker_restart",
                    "collect_only", "environment_digest", "isolation_policy",
                }
                tools = value.get("repository_tool_blobs")
                if (not required <= value.keys()
                        or not isinstance(value["profile"], str) or not value["profile"]
                        or type(value["workers"]) is not int or value["workers"] < 0
                        or value["distribution"] not in {"load", "no"}
                        or type(value["max_worker_restart"]) is not int or value["max_worker_restart"] != 0
                        or type(value["collect_only"]) is not bool
                        or not isinstance(value["environment_digest"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", value["environment_digest"]) is None
                        or value["isolation_policy"] != "clean-clone-external-temp-v1"
                        or not isinstance(tools, dict) or not tools
                        or any(not isinstance(path, str) or not path for path in tools)):
                    raise ValueError("invalid verification reference source profile")
                blobs = [value["observer_blob"], *tools.values()]
                if value["selected_policy_blob"] is not None:
                    blobs.append(value["selected_policy_blob"])
                if any(not isinstance(blob, str) or re.fullmatch(r"[0-9a-f]{40}", blob) is None
                       for blob in blobs):
                    raise ValueError("invalid verification reference source identity")
            if value.get("profile") == "bounded-parallel-full-suite-v1":
                if (binding["command"] != "python scripts/aios_parallel_full_suite.py"
                        or type(value.get("workers")) is not int or value["workers"] != 12
                        or value.get("distribution") != "load"
                        or type(value.get("max_worker_restart")) is not int or value["max_worker_restart"] != 0
                        or value.get("collect_only") is not False
                        or value.get("selected_policy_blob") is None
                        or value.get("selection_provenance") != {"authority": "HUMAN", "task_id": "TASK-231"}):
                    raise ValueError("contradictory verification reference worker profile")
            if value.get("worker_execution_contract") == "fixed-human-pytest-v1" and (
                    type(value.get("workers")) is not int
                    or value["workers"] != (0 if value.get("collect_only") is True else 12)
                    or value.get("distribution") != ("no" if value.get("collect_only") is True else "load")):
                raise ValueError("contradictory verification reference worker contract")
            return value

        def observation(name: str) -> dict[str, Any]:
            value = item.get(name)
            if (not isinstance(value, dict)
                    or type(value.get("complete")) is not bool
                    or type(value.get("unstable")) is not bool
                    or type(value.get("exit_code")) is not int
                    or item.get(name + "_digest") != verification_digest(value)):
                raise ValueError("invalid verification reference observation")
            conditions = profile(value.get("profile"))
            toolchain = value.get("toolchain")
            if (not isinstance(toolchain, dict)
                    or any(not isinstance(toolchain.get(key), str) or not toolchain[key] for key in (
                        "python_implementation", "python_version", "python_executable", "platform_system",
                        "platform_machine", "pytest_version", "pytest_xdist_version"))
                    or not isinstance(value.get("reports"), list)
                    or type(value.get("failure_count")) is not int or value["failure_count"] < 0):
                raise ValueError("invalid verification reference observation conditions")
            # Incomplete diagnostics cannot acquire proof authority, but their
            # recorded failure conditions still cannot contradict the binding.
            for report in value["reports"]:
                if not isinstance(report, dict) or report.get("outcome") not in {"PASS", "FAIL", "SKIP"}:
                    raise ValueError("invalid verification reference report")
                if report.get("outcome") == "FAIL" and (
                        ((value["complete"] or "profile" in report) and report.get("profile") != conditions)
                        or ((value["complete"] or "toolchain" in report) and report.get("toolchain") != toolchain)
                        or ("observed_profile" in report and report["observed_profile"] != {
                            key: conditions.get(key) for key in (
                                "profile", "workers", "distribution", "max_worker_restart", "collect_only")})
                        or ("observed_toolchain" in report and report["observed_toolchain"] != toolchain)):
                    raise ValueError("contradictory verification reference failure conditions")
            if value["failure_count"] != sum(report["outcome"] == "FAIL" for report in value["reports"]):
                raise ValueError("invalid verification reference failure population")
            if value["complete"]:
                validate_observation(value)
            else:
                from .review_transport import _project_observation
                _project_observation(value)  # Validate recorded identities; do not fill missing detail.
            return value

        candidate = observation("candidate")
        if (any(candidate.get(key) != binding[key]
                for key in ("subject_sha", "command", "profile", "toolchain"))
                or type(candidate.get("exit_code")) is not int
                or candidate["exit_code"] != owner.get("exit_code")
                or binding["command"] != owner.get("command")):
            raise ValueError("verification reference outcome binding mismatch")
        effective = binding
        if "reuse" in item:
            reuse = item["reuse"]
            if not isinstance(reuse, dict):
                raise ValueError("invalid verification reference reuse")
            effective = validate_evidence_binding(reuse.get("binding"))
            if (reuse.get("source_evidence_id") != identity
                    or {key: value for key, value in effective.items() if key != "subject_sha"}
                    != {key: value for key, value in binding.items() if key != "subject_sha"}):
                raise ValueError("verification reference reuse binding mismatch")
        elif item["run_id"] != owner.get("run_id"):
            raise ValueError("verification reference RUN binding mismatch")
        if effective["subject_sha"] != owner.get("subject_sha"):
            raise ValueError("verification reference subject binding mismatch")
        if "base" in item:
            base = observation("base")
            base_profile, candidate_profile = base["profile"], binding["profile"]
            # This is FAILURE readability, never proof comparability. Only the
            # per-subject source identities may differ; Runtime attribution and
            # reuse continue to compare the entire profile without this exception.
            profiles_valid = base_profile == candidate_profile or (
                source_profile_fields <= base_profile.keys()
                and source_profile_fields <= candidate_profile.keys()
                and base_profile["repository_tool_blobs"].keys() == candidate_profile["repository_tool_blobs"].keys()
                and {key: value for key, value in base_profile.items() if key not in source_profile_fields}
                == {key: value for key, value in candidate_profile.items() if key not in source_profile_fields}
            )
            if (base.get("subject_sha") != binding["base_sha"]
                    or any(base.get(key) != binding[key] for key in ("command", "toolchain"))
                    or not profiles_valid
                    or not {"base_raw_path", "base_raw_digest"} <= item.keys()
                    or binding["failure_set_digest"] != item["base_digest"]):
                raise ValueError("verification reference base binding mismatch")
        elif {"base_digest", "base_raw_path", "base_raw_digest"} & item.keys():
            raise ValueError("verification reference has orphaned base metadata")

    def metadata(value: Any, role: str = "metadata", owner: Mapping[str, Any] | None = None) -> Any:
        mapping_roles = {"root", "failure", "failure_error", "declared_metadata", "package",
                         "reference", "full_evidence", "outcome"}
        if isinstance(value, list):
            if role in mapping_roles:
                raise ValueError("terminal verification record must be a mapping")
            member_role = {"outcomes": "outcome", "evidence": "full_evidence"}.get(role, role)
            return [metadata(item, member_role, owner) for item in value]
        if not isinstance(value, dict):
            if role in mapping_roles | {"outcomes", "evidence"}:
                raise ValueError("malformed terminal verification metadata")
            return value
        if role in {"outcomes", "evidence"}:
            raise ValueError("terminal verification population must be a list")
        if value.get("kind") == "FAILURE" and "public_projection" in value:
            from .review_transport import validate_public_failure
            if role not in {"root", "failure"}:
                raise ValueError("FAILURE outside a validated structural role")
            if owner is not None and (
                value.get("run_id") != owner.get("run_id")
                or value.get("failed_head_sha") != owner.get("failed_head_sha")
            ):
                raise ValueError("nested FAILURE does not bind its correction owner")
            validate_public_failure(value)
            return value
        if role == "reference":
            if owner is None:
                raise ValueError("verification reference has no structural owner")
            reference(value, owner)
            # Authenticated observation/provenance remains verbatim, including
            # historical annotations, but cannot hide additional EVIDENCE roles.
            # No raw locator is opened by this reader.
            for item in value.values():
                metadata(item, "record_metadata")
            return value
        full_evidence = role == "full_evidence"
        if "evidence_id" in value and not full_evidence:
            raise ValueError("EVIDENCE identity outside a validated structural role")
        if value.get("kind") == "FAILURE":
            if role not in {"root", "failure"}:
                raise ValueError("FAILURE outside a validated structural role")
            if (not isinstance(value.get("run_id"), str) or not value["run_id"]
                    or not isinstance(value.get("failed_head_sha"), str)
                    or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value["failed_head_sha"]) is None):
                raise ValueError("FAILURE structural owner is malformed")
            if owner is not None and (
                value["run_id"] != owner.get("run_id")
                or value["failed_head_sha"] != owner.get("failed_head_sha")
            ):
                raise ValueError("nested FAILURE does not bind its correction owner")
            role = "failure"
            owner = value
        elif role == "failure":
            raise ValueError("nested FAILURE structural owner is missing")
        if role == "root" and "failure" in value and (
            not {"failed_run_id", "failed_head_sha", "root_base_sha", "task", "run", "repair"} <= value.keys()
            or any(not isinstance(value.get(key), dict) for key in ("task", "run", "repair"))
        ):
            raise ValueError("nested FAILURE has no correction lineage")
        if role == "package" and (
            not isinstance(value.get("result"), dict) or not isinstance(value.get("evidence"), list)
        ):
            raise ValueError("malformed embedded ResultPackage")
        data = {}
        for key, item in value.items():
            # Only declared populations grant full EVIDENCE authority. Record
            # fields cannot create new populations or replace the inherited owner.
            child_role = "record_metadata" if role in {"full_evidence", "package", "record_metadata"} else "metadata"
            child_owner = owner
            if key == "failure" and role == "root":
                child_role = "failure"
                child_owner = dict(run_id=value.get("failed_run_id"), failed_head_sha=value.get("failed_head_sha"))
            elif key == "error" and role == "failure":
                child_role, child_owner = "failure_error", value
            elif key == "verification" and role == "failure_error":
                child_role, child_owner = "outcomes", owner
            elif key == "verification" and role == "outcome":
                if owner is None:
                    raise ValueError("FAILURE verification has no structural owner")
                child_role = "reference"
                child_owner = dict(command=value.get("command"), exit_code=value.get("exit_code"),
                    run_id=owner.get("run_id"), subject_sha=owner.get("failed_head_sha"))
            elif key == "verification" and full_evidence:
                # validate_evidence owns a full record's verification binding.
                # It cannot contain another full record masquerading as one.
                if isinstance(item, dict) and set(item).intersection(
                    {"raw", "result", "source", "subject_sha", "type"}
                ):
                    raise ValueError("full EVIDENCE impersonates reference metadata")
                if isinstance(item, dict):
                    for member in item.values():
                        metadata(member, "record_metadata")
                data[key] = item
                continue
            elif (key == "metadata" and role in {"root", "failure", "failure_error"}
                  and isinstance(item, dict)):
                # Only this immediate container may declare a package. Unknown
                # descendants retain generic metadata without that authority.
                child_role = "declared_metadata"
            elif key == "embedded_package" and role in {"root", "failure", "failure_error", "declared_metadata"}:
                child_role = "package"
            elif key == "evidence" and role in {"package", "failure_error"}:
                child_role, child_owner = "evidence", owner
            data[key] = metadata(item, child_role, child_owner)
        if "run_id" in data and "workspace" in data and "task" in data:
            workspace(data)
        if full_evidence:
            if owner is None:
                raise ValueError("full EVIDENCE has no structural owner")
            evidence(data, owner)
        # Early historical predecessors may have no diagnostics at all. Their
        # immutable identity/candidate still binds the correction; absence must
        # not manufacture an error, an observation or proof.
        if data.get("kind") == "FAILURE" and "error" in data:
            error = data.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("message"), str):
                raise ValueError("invalid private FAILURE diagnostics")
            if (not isinstance(error.get("type"), str)
                    or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", error["type"]) is None):
                raise ValueError("invalid private FAILURE error type")
            from .review_transport import _NATIVE_REASONS, _validate_native_failure_exit
            if "native_reason" in error:
                if not isinstance(error["native_reason"], str) or error["native_reason"] not in _NATIVE_REASONS:
                    raise ValueError("invalid private FAILURE native reason")
            _validate_native_failure_exit(data)
            error["message"] = "Protected Runtime diagnostics remain local."
            error.pop("native_diagnostics", None)
            error.pop("executor_diagnostics", None)
            for item in error.get("verification", []):
                if (not isinstance(item, dict) or type(item.get("exit_code")) is not int
                        or not isinstance(item.get("summary"), str)):
                    raise ValueError("invalid private FAILURE verification")
                item["summary"] = "Runtime verification exit code " + str(item["exit_code"])
        return data

    projected = json.loads(json.dumps(private, allow_nan=False))
    if kind == "RUN":
        workspace(projected["execution"]["run"] if projected.get("kind") == "REMEDIATION" else projected)
    elif kind == "RESULT":
        for item in projected["evidence"]:
            evidence(item)
    elif kind == "METADATA":
        projected = metadata(projected, "root")
    else:
        raise ValueError("unknown terminal representation")
    return projected


def _matches_public_document(
    private: Mapping[str, Any] | None,
    canonical: Mapping[str, Any] | None,
    kind: str,
) -> bool:
    """Compare a validated private record with its exact transport representation.

    Validate even the historical exact representation. Only the writer's authorized
    privacy fields may differ; projecting both sides would conceal corruption of
    the public record.
    """
    if private is None or canonical is None:
        return private is canonical

    def encoded(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def protected_view(value: Any) -> bool:
        if isinstance(value, list):
            return any(protected_view(item) for item in value)
        if not isinstance(value, Mapping):
            return False
        raw = value.get("raw")
        error = value.get("error")
        return (value.get("workspace") == "protected-runtime-workspace"
                or (isinstance(raw, Mapping) and isinstance(raw.get("path"), str)
                    and raw["path"].startswith("protected-runtime-raw/"))
                or (isinstance(error, Mapping)
                    and error.get("message") == "Protected Runtime diagnostics remain local.")
                or any(protected_view(item) for item in value.values()))

    original = encoded(private)
    public = encoded(canonical)
    def has_projection(value: Any) -> bool:
        if isinstance(value, list):
            return any(has_projection(item) for item in value)
        return isinstance(value, Mapping) and (
            "public_projection" in value or any(has_projection(item) for item in value.values()))

    if kind == "METADATA" and (has_projection(private) or has_projection(canonical)):
        from .review_transport import _public_metadata_value
        # Validate both structural roles, then project only the private side.
        # Comparing two reprojected records would conceal forged public fields.
        _validated_private_public_view(private, kind)
        _validated_private_public_view(canonical, kind)
        return public == encoded(_public_metadata_value(dict(private), canonical))
    projected = _validated_private_public_view(private, kind)
    if public == original and not protected_view(canonical):
        return True
    return public == encoded(projected)


def _matches_canonical_failure(
    embedded: Mapping[str, Any], canonical: Mapping[str, Any], *, canonical_workspace: str,
) -> bool:
    """Bind correction content to an immutable predecessor in its known view.

    A public predecessor must itself be exactly redacted. A historical private
    predecessor may appear verbatim or as its exact public projection in a new
    correction; projecting both sides would hide forged public diagnostics.
    """
    if canonical_workspace == "protected-runtime-workspace":
        projected = _validated_private_public_view(canonical, "METADATA")
        if json.dumps(canonical, sort_keys=True, allow_nan=False) != json.dumps(
            projected, sort_keys=True, allow_nan=False
        ):
            return False
        return _matches_public_document(embedded, canonical, "METADATA")
    return _matches_public_document(canonical, embedded, "METADATA")


def _decode_lifecycle_run(
    raw: bytes, *, run_id: str
) -> tuple[Run, str, str | None, str | None]:
    value = _lifecycle_document(raw, "RUN")
    op = _operator()
    execution_data = value.get("execution")
    run_data = (execution_data.get("run") if isinstance(execution_data, Mapping) else None
                ) if value.get("kind") == "REMEDIATION" else value
    if (not isinstance(run_data, Mapping)
            or not isinstance(run_data.get("task"), Mapping)
            or type(run_data["task"].get("revision")) is not int
            or run_data["task"]["revision"] < 1
            or any(not isinstance(run_data.get(key), str) or not run_data[key].strip()
                   for key in ("run_id", "executor", "base_sha", "workspace", "status"))
            or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", run_data["base_sha"]) is None
            or (run_data.get("head_sha") is not None and
                (not isinstance(run_data["head_sha"], str) or
                 re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", run_data["head_sha"]) is None))):
        raise ValueError("RUN identity fields are malformed")
    if value.get("kind") == "REMEDIATION":
        execution = op._remediation_execution_from_data(value.get("execution"))
        predecessor = None
        execution_base = None
        if "predecessor" in value:
            predecessor = op._parse_remediation_predecessor(value["predecessor"])
            if (
                predecessor.review_id != execution.review_id
                or predecessor.finding_id != execution.finding.id
                or predecessor.reviewed_sha != execution.remediation.reviewed_sha
            ):
                raise ValueError("REMEDIATION predecessor identity mismatch")
        if "execution_base" in value:
            if predecessor is None:
                raise ValueError("REMEDIATION execution_base requires predecessor")
            execution_base = op._parse_remediation_execution_base(value["execution_base"])
            if execution_base.candidate_sha != execution.run.base_sha:
                raise ValueError("REMEDIATION execution_base does not match RUN base")
        elif execution.run.base_sha != execution.remediation.reviewed_sha:
            raise ValueError("legacy REMEDIATION RUN base does not match reviewed SHA")
        return (
            execution.run,
            "REMEDIATION",
            execution.review_id,
            execution.finding.id,
        )
    if "kind" in value:
        raise ValueError("unknown RUN kind")
    run = op._run_from_data(value)
    return run, "PRIMARY", None, None


def _validate_lifecycle_result(
    *,
    task: Task,
    run: Run,
    family: str,
    run_document: Mapping[str, Any],
    terminal: Mapping[str, Any],
    candidate_sha: str,
) -> ResultPackage:
    """Validate one canonical/local ResultPackage before lifecycle reduction."""

    result = validate_result(terminal["result"])
    evidence_data = terminal["evidence"]
    if not isinstance(evidence_data, list):
        raise TypeError("ResultPackage evidence must be a list")
    evidence = tuple(validate_evidence(item) for item in evidence_data)
    package = ResultPackage(result=result, evidence=evidence)
    if family == "REMEDIATION":
        op = _operator()
        execution = op._remediation_execution_from_data(run_document["execution"])
        op._require_remediation_package_contract(
            execution, package, actual_head=result.head_sha
        )
    else:
        validate_result_package(
            task=task, run=run, result=result, evidence=evidence
        )
    if result.head_sha != candidate_sha:
        raise ValueError("RESULT head does not match candidate ref")
    return package


def _validate_lifecycle_failure(
    *,
    repo: Path,
    task: Task,
    run: Run,
    terminal: Mapping[str, Any],
    candidate_sha: str,
    candidate_available: bool,
    continuation_of: str | None,
    public_representation: bool = True,
) -> None:
    """Bind one FAILURE to Runtime's exact RUN and candidate facts.

    Canonical terminals must already be public representations. Local private
    terminals validate the same roles before their separate reconciliation.
    """

    op = _operator()
    actual_descends = None
    actual_changed = None
    if candidate_available:
        try:
            actual_descends = op._git_is_ancestor(repo, run.base_sha, candidate_sha)
            actual_changed = (
                op._committed_changed_files(repo, run.base_sha, candidate_sha)
                if actual_descends
                else set()
            )
        except op.OperatorError as exc:
            raise ValueError("FAILURE candidate Git binding is invalid") from exc
    validate_runtime_failure_binding(
        terminal,
        run_id=run.run_id,
        task_id=task.task_id,
        task_revision=task.revision,
        executor=run.executor,
        base_sha=run.base_sha,
        candidate_sha=candidate_sha,
        modification_scope=task.scope.modify,
        actual_descends_from_base=actual_descends,
        actual_changed_files=actual_changed,
    )
    if terminal.get("continuation_of") != continuation_of:
        raise ValueError("FAILURE continuation does not match RUN family")
    if type(terminal["task"].get("revision")) is not int:
        raise ValueError("FAILURE TASK revision is malformed")
    if public_representation:
        if not _matches_canonical_failure(terminal, terminal, canonical_workspace=run.workspace):
            raise ValueError("FAILURE privacy representation is invalid")
    else:
        _validated_private_public_view(terminal, "METADATA")


def _decode_publication_continuation(
    repo: Path,
    *,
    task: Task,
    run: Run,
    family: str,
    parent_run_id: str | None,
    raw: bytes,
    candidate_sha: str,
    package: ResultPackage | None,
    local_repository: Path | None = None,
) -> tuple[Mapping[str, Any], str]:
    """Project the same validated recovery provenance for local and remote terminals."""

    document = publication_module._mapping(publication_module._json_no_duplicates(
        raw, document="publication continuation"), "publication continuation")
    if family != "PRIMARY" or parent_run_id is not None:
        raise ValueError("publication continuation cannot impersonate a correction")
    plan = publication_module.publication_plan_from_data(document["plan"])
    if package is not None:
        publication_module.validate_publication_recovery(repo,
            remote=resolve_transport_remote(repo), raw=raw,
            run=run, task=task, candidate_sha=candidate_sha, package=package,
            local_repository=local_repository)
    elif (set(document) != {"format", "version", "plan", "identity", "reservation_sha"}
            or document["format"] != "AIOS_PUBLICATION_RECOVERY_ADMISSION"
            or document["version"] != 1 or document["identity"] != plan.identity
            or run.base_sha != plan.main_sha or run.task.id != plan.task_id
            or run.task.revision != plan.task_revision):
        raise ValueError("publication failure has no exact Runtime admission")
    return document, plan.source_run_id


def _repair_predecessor_bases(
    parent: _LifecycleRun,
    lifecycle: list[_LifecycleRun],
    *,
    seen: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """Derive semantic root and result base from the exact decoded ancestry.

    Integrated REMEDIATION changes Git attribution, while its semantic source
    still supplies the original TASK root. REPAIR inherits both identities.
    """
    if parent.run_id in seen:
        raise ValueError("REPAIR origin is cyclic")
    if parent.family == "PRIMARY":
        return parent.run.base_sha, parent.run.base_sha
    predecessors = [item for item in lifecycle if item.run_id == parent.parent_run_id]
    if len(predecessors) != 1:
        raise ValueError("REPAIR origin is missing or ambiguous")
    root, result_base = _repair_predecessor_bases(
        predecessors[0], lifecycle, seen=seen.union((parent.run_id,)),
    )
    if parent.family == "REMEDIATION":
        result_base = (
            parent.execution_base.integration_candidate_sha
            if parent.execution_base is not None and parent.execution_base.is_integrated
            else root
        )
    elif parent.family == "REPAIR":
        if (parent.correction is None
                or parent.correction.get("root_base_sha") != root
                or ("result_base_sha" in parent.correction
                    and parent.correction["result_base_sha"] != result_base)):
            raise ValueError("REPAIR origin base lineage mismatch")
    else:
        raise ValueError("REPAIR origin family is invalid")
    return root, result_base


def _decode_remote_lifecycle(
    repo: Path, task: Task, lifecycle: RemoteTaskLifecycle
) -> tuple[list[_LifecycleRun], dict[str, Review]]:
    op = _operator()
    reviews: dict[str, Review] = {}
    for item in lifecycle.reviews:
        if item.run_id in reviews:
            raise ValueError("competing semantic review decisions")
        reviews[item.run_id] = parse_review(
            item.review.decode("utf-8", errors="strict")
        )
    decoded: list[_LifecycleRun] = []
    for item in lifecycle.terminals:
        run_document = _lifecycle_document(item.run, "RUN")
        if not isinstance(run_document, Mapping):
            raise ValueError("RUN must be a mapping")
        run, family, review_id, finding_id = _decode_lifecycle_run(
            item.run, run_id=item.run_id
        )
        semantic_predecessor = None
        execution_base_run_id = None
        execution_base = None
        if family == "REMEDIATION" and "predecessor" in run_document:
            semantic_predecessor = op._parse_remediation_predecessor(
                run_document["predecessor"]
            )
            if "execution_base" in run_document:
                execution_base = op._parse_remediation_execution_base(
                    run_document["execution_base"]
                )
                execution_base_run_id = (
                    execution_base.cumulative_tip_run_id
                    if execution_base.is_integrated
                    else execution_base.run_id
                )
            else:
                execution_base_run_id = semantic_predecessor.source_run_id
        require_same_affinity(task, run)
        if (
            run.run_id != item.run_id
            or run.task.id != task.task_id
            or run.task.revision != task.revision
            or run.status != "ACTIVE"
        ):
            raise ValueError("terminal RUN does not bind the current TASK revision")
        parent_run_id = None
        correction = None
        if item.correction is not None:
            correction = _lifecycle_document(item.correction, "REPAIR")
            if not isinstance(correction, Mapping):
                raise ValueError("REPAIR lineage must be a mapping")
            if not _matches_public_document(correction, correction, "METADATA"):
                raise ValueError("REPAIR privacy representation is invalid")
            parent_run_id = correction.get("failed_run_id")
            if not isinstance(parent_run_id, str) or not parent_run_id:
                raise ValueError("REPAIR lineage has no failed RUN identity")
            if family != "PRIMARY":
                raise ValueError("terminal has competing correction families")
            embedded_run = correction.get("run")
            embedded_failure = correction.get("failure")
            authorization = correction.get("repair")
            authorization_sha = correction.get("repair_authorization_sha")
            correction_task = correction.get("task")
            run_value = run_document
            if (
                correction.get("failed_head_sha") != run.base_sha
                or not isinstance(correction.get("root_base_sha"), str)
                or not correction.get("root_base_sha")
                or not isinstance(embedded_run, Mapping)
                or dict(embedded_run) != dict(run_value)
                or not isinstance(embedded_failure, Mapping)
                or embedded_failure.get("run_id") != parent_run_id
                or embedded_failure.get("failed_head_sha") != run.base_sha
                or not isinstance(authorization, Mapping)
                or authorization.get("failed_run_id") != parent_run_id
                or authorization.get("failed_head_sha") != run.base_sha
                or ("task" in authorization and (
                    not isinstance(authorization["task"], Mapping)
                    or type(authorization["task"].get("revision")) is not int
                    or dict(authorization["task"]) != {"id": task.task_id, "revision": task.revision}
                ))
                or (
                    authorization_sha is not None
                    and (
                        not isinstance(authorization_sha, str)
                        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", authorization_sha)
                        is None
                    )
                )
                or not isinstance(correction_task, Mapping)
                or correction_task.get("task_id") != task.task_id
                or type(correction_task.get("revision")) is not int
                or correction_task.get("revision") != task.revision
                or document_affinity(correction_task) != task.return_affinity
            ):
                raise ValueError("REPAIR execution lineage is invalid")
            # The acquired snapshot already resolves canonical authorization
            # chains. Bind executed corrections to those immutable selectors
            # whenever present; historical snapshots without selectors retain
            # their existing embedded-lineage validation.
            selectors = [selector for selector in lifecycle.repair_selectors
                         if selector[0] == parent_run_id]
            if not selectors and lifecycle.observed_refs:
                raise ValueError("REPAIR canonical authorization is missing")
            if selectors:
                if len(selectors) != 1:
                    raise ValueError("REPAIR canonical authorization is ambiguous")
                _, selected_sha, selected_raw = selectors[0]
                selected = _lifecycle_document(selected_raw, "REPAIR authorization")
                publication_module._validate_repair_authorization(
                    authorization, failed_run_id=parent_run_id, failed_head_sha=run.base_sha,
                    task=task, failed_changed_files=set(embedded_failure["candidate"]["changed_files"]),
                )
                if dict(selected) != dict(authorization):
                    raise ValueError("persisted REPAIR authorization is not canonical")
                if authorization_sha is not None and authorization_sha != selected_sha:
                    raise ValueError(
                        "persisted repair_authorization_sha does not match current canonical authorization"
                    )
            family = "REPAIR"
        terminal = _lifecycle_document(item.terminal, item.kind)
        if not isinstance(terminal, Mapping):
            raise ValueError("terminal document must be a mapping")
        package = None
        if item.kind == "RESULT":
            package = _validate_lifecycle_result(
                task=task,
                run=run,
                family=family,
                run_document=run_document,
                terminal=terminal,
                candidate_sha=item.candidate_sha,
            )
        else:
            _validate_lifecycle_failure(
                repo=repo,
                task=task,
                run=run,
                terminal=terminal,
                candidate_sha=item.candidate_sha,
                candidate_available=item.candidate_available,
                continuation_of=parent_run_id if family == "REPAIR" else None,
            )
        recovery_document = None
        if item.publication_recovery is not None:
            recovery_document, parent_run_id = _decode_publication_continuation(
                repo, task=task, run=run, family=family, parent_run_id=parent_run_id,
                raw=item.publication_recovery, candidate_sha=item.candidate_sha,
                package=package,
            )
        decoded.append(
            _LifecycleRun(
                item.run_id,
                family,
                run,
                item.candidate_sha,
                item.kind,
                terminal,
                parent_run_id,
                review_id,
                finding_id,
                item.candidate_available,
                correction,
                run_document,
                semantic_predecessor,
                execution_base_run_id,
                execution_base,
                recovery_document,
            )
        )
    # A remediation points to the uniquely reviewed predecessor with the same
    # immutable reviewed SHA and finding identity.  No numeric RUN ordering is used.
    by_candidate: dict[str, list[_LifecycleRun]] = {}
    for item in decoded:
        by_candidate.setdefault(item.candidate_sha, []).append(item)
    resolved: list[_LifecycleRun] = []
    for item in decoded:
        if item.publication_recovery is not None:
            plan = publication_module.publication_plan_from_data(item.publication_recovery["plan"])
            parents = [parent for parent in decoded if parent.run_id == plan.source_run_id
                       and parent.candidate_sha == plan.reviewed_sha and parent.terminal_kind == "RESULT"]
            prior = reviews.get(plan.source_run_id)
            if (len(parents) != 1 or prior is None or prior.verdict != "PASS"
                    or prior.reviewed_sha != plan.reviewed_sha
                    or next((review.decision_sha for review in lifecycle.reviews
                             if review.run_id == plan.source_run_id), None) != plan.decision_sha):
                raise ValueError("publication continuation original PASS predecessor is missing or ambiguous")
        if item.family != "REMEDIATION":
            resolved.append(item)
            continue
        pred = item.semantic_predecessor
        if pred is not None:
            parents = [
                parent for parent in decoded
                if parent.run_id == pred.source_run_id
                and parent.candidate_sha == pred.reviewed_sha
                and parent.terminal_kind == "RESULT"
                and parent.run_id in reviews
                and reviews[parent.run_id].review_id == pred.review_id
                and pred.finding_id in {f.id for f in reviews[parent.run_id].findings}
            ]
        else:
            parents = [
                parent
                for parent in by_candidate.get(item.run.base_sha, ())
                if parent.terminal_kind == "RESULT"
                and parent.run_id in reviews
                and reviews[parent.run_id].review_id == item.review_id
                and item.finding_id in {finding.id for finding in reviews[parent.run_id].findings}
            ]
        if len(parents) != 1:
            raise ValueError("REMEDIATION predecessor is missing or ambiguous")
        if item.execution_base is not None and getattr(item.execution_base, "is_integrated", False):
            base_parents = [
                parent for parent in decoded
                if parent.run_id == item.execution_base.cumulative_tip_run_id
                and parent.terminal_kind == "RESULT"
                and parent.candidate_sha == item.execution_base.cumulative_tip_candidate_sha
            ]
            if len(base_parents) != 1:
                raise ValueError("REMEDIATION execution base cumulative tip is missing or ambiguous")
            if item.run.base_sha != item.execution_base.integration_candidate_sha:
                raise ValueError("REMEDIATION execution base candidate does not match RUN base")
            try:
                if not op._git_is_ancestor(
                    repo, item.execution_base.authorized_main_sha, lifecycle.main_sha
                ):
                    raise ValueError(
                        "REMEDIATION execution base authorized main is not an ancestor of canonical main"
                    )
            except op.OperatorError as exc:
                raise ValueError(
                    "REMEDIATION execution base authorized main ancestry is invalid"
                ) from exc
            from .correction_integration import resolve_valid_integration

            integration = resolve_valid_integration(
                repo,
                task_id=task.task_id,
                task_revision=task.revision,
                cumulative_tip_run_id=item.execution_base.cumulative_tip_run_id,
                cumulative_tip_candidate_sha=item.execution_base.cumulative_tip_candidate_sha,
                authorized_main_sha=item.execution_base.authorized_main_sha,
                require_remote=True,
            )
            if (
                integration is None
                or integration.integration_id != item.execution_base.integration_id
                or integration.integration_candidate_sha != item.execution_base.integration_candidate_sha
            ):
                raise ValueError("REMEDIATION integration candidate or evidence is invalid")
        else:
            base_parents = [
                parent for parent in decoded
                if parent.run_id == item.execution_base_run_id
                and parent.terminal_kind == "RESULT"
                and parent.candidate_sha == item.run.base_sha
            ]
            if item.execution_base_run_id is not None and len(base_parents) != 1:
                raise ValueError("REMEDIATION execution base is missing or ambiguous")
        resolved.append(replace(item, parent_run_id=parents[0].run_id))

    # A REPAIR keeps its immediate failed RUN as parent, while inheriting the
    # semantic identity embedded by the correction it repairs.  Resolve that
    # identity only by walking the immutable parent chain; never search the TASK
    # by finding id.
    by_run_id: dict[str, list[_LifecycleRun]] = {}
    for item in resolved:
        by_run_id.setdefault(item.run_id, []).append(item)

    def correction_origin(
        item: _LifecycleRun, seen: frozenset[str] = frozenset()
    ) -> _LifecycleRun | None:
        if item.family == "REMEDIATION":
            return item
        if item.family != "REPAIR":
            return None
        if item.run_id in seen or item.parent_run_id is None:
            raise ValueError("REPAIR predecessor is missing or cyclic")
        parents = by_run_id.get(item.parent_run_id, ())
        if len(parents) != 1:
            raise ValueError("REPAIR predecessor is missing or ambiguous")
        parent = parents[0]
        root_base, result_base = _repair_predecessor_bases(parent, resolved)
        if (
            parent.terminal_kind != "FAILURE"
            or item.run.base_sha != parent.candidate_sha
            or item.correction is None
            or not _matches_canonical_failure(item.correction["failure"], parent.terminal,
                                             canonical_workspace=parent.run.workspace)
            or parent.terminal.get("base_sha") != parent.run.base_sha
            or item.correction["root_base_sha"] != root_base
            or ("result_base_sha" in item.correction
                and item.correction["result_base_sha"] != result_base)
        ):
            raise ValueError("REPAIR predecessor identity or failed SHA mismatch")
        return correction_origin(parent, seen.union((item.run_id,)))

    correction_complete: list[_LifecycleRun] = []
    for item in resolved:
        if item.family != "REPAIR":
            correction_complete.append(item)
            continue
        origin = correction_origin(item)
        correction_complete.append(
            replace(
                item,
                review_id=None if origin is None else origin.review_id,
                finding_id=None if origin is None else origin.finding_id,
            )
        )
    return correction_complete, reviews


def _correction_prior_review(
    tip: _LifecycleRun,
    lifecycle: list[_LifecycleRun],
    reviews: Mapping[str, Review],
) -> Review:
    """Resolve a DELTA predecessor through one exact correction lineage."""

    by_run_id: dict[str, list[_LifecycleRun]] = {}
    for item in lifecycle:
        by_run_id.setdefault(item.run_id, []).append(item)
    current = tip
    seen: set[str] = set()
    while current.family == "REPAIR":
        if current.run_id in seen or current.parent_run_id is None:
            raise ValueError("DELTA review predecessor is missing or ambiguous")
        seen.add(current.run_id)
        parents = by_run_id.get(current.parent_run_id, ())
        if len(parents) != 1:
            raise ValueError("DELTA review predecessor is missing or ambiguous")
        current = parents[0]
    if current.family != "REMEDIATION" or current.parent_run_id is None:
        raise ValueError("DELTA review predecessor is missing or ambiguous")
    prior_review = reviews.get(current.parent_run_id)
    semantic_reviewed_sha = (
        current.semantic_predecessor.reviewed_sha
        if current.semantic_predecessor is not None
        else current.run.base_sha
    )
    if (
        prior_review is None
        or prior_review.review_id != current.review_id
        or prior_review.reviewed_sha != semantic_reviewed_sha
        or tip.review_id != current.review_id
        or tip.finding_id != current.finding_id
    ):
        raise ValueError("DELTA review predecessor is missing or ambiguous")
    return prior_review


def _operational_parent(item: _LifecycleRun) -> str | None:
    if item.family == "REMEDIATION":
        if item.execution_base is not None and getattr(item.execution_base, "is_integrated", False):
            return item.execution_base.cumulative_tip_run_id
        if item.execution_base_run_id is not None:
            return item.execution_base_run_id
    return item.parent_run_id


def _ordered_lineage(
    tip: _LifecycleRun, lifecycle: list[_LifecycleRun]
) -> list[_LifecycleRun]:
    by_id: dict[str, list[_LifecycleRun]] = {}
    for item in lifecycle:
        by_id.setdefault(item.run_id, []).append(item)
    ordered = [tip]
    seen = {tip.run_id}
    current = tip
    while (parent_id := _operational_parent(current)) is not None:
        parents = by_id.get(parent_id, ())
        if len(parents) != 1 or parent_id in seen:
            raise ValueError("cumulative correction lineage is missing or ambiguous")
        current = parents[0]
        ordered.append(current)
        seen.add(parent_id)
    ordered.reverse()
    if not ordered or ordered[0].family != "PRIMARY":
        raise ValueError("cumulative correction lineage has no PRIMARY root")
    return ordered


def _semantic_origin(
    item: _LifecycleRun, lifecycle: list[_LifecycleRun]
) -> _LifecycleRun:
    by_id = {candidate.run_id: candidate for candidate in lifecycle}
    current = item
    seen: set[str] = set()
    while current.family == "REPAIR":
        if current.run_id in seen or current.parent_run_id not in by_id:
            raise ValueError("correction semantic origin is missing or cyclic")
        seen.add(current.run_id)
        current = by_id[current.parent_run_id]
    if current.family != "REMEDIATION":
        raise ValueError("correction semantic origin is not REMEDIATION")
    return current


def _derive_tip_frontier(
    tip: _LifecycleRun,
    lifecycle: list[_LifecycleRun],
    reviews: Mapping[str, Review],
) -> CorrectionFrontier:
    ordered = _ordered_lineage(tip, lifecycle)
    # A recovered source starts a new PRIMARY semantic obligation. Earlier PASS
    # decisions remain validated provenance and cannot compete as its verdict.
    recovery_roots = [idx for idx, item in enumerate(ordered) if item.publication_recovery is not None]
    if recovery_roots:
        ordered = ordered[recovery_roots[-1]:]
    primary_roots = [
        (idx, item, review)
        for idx, item in enumerate(ordered)
        if (review := reviews.get(item.run_id)) is not None and review.mode == "PRIMARY"
    ]
    if not primary_roots:
        raise ValueError("PRIMARY correction frontier review is missing")
    if len(primary_roots) > 1:
        raise ValueError("cumulative correction lineage has competing semantic roots")
    root_idx, semantic_root, primary_review = primary_roots[0]
    if semantic_root.terminal_kind != "RESULT":
        raise ValueError("PRIMARY correction frontier review must belong to a successful RESULT")
    if root_idx == 0:
        if semantic_root.family != "PRIMARY":
            raise ValueError("PRIMARY correction frontier root is invalid")
    else:
        if semantic_root.family != "REPAIR":
            raise ValueError("PRIMARY correction frontier review must belong to a PRIMARY or REPAIR run")
        if any(
            ancestor.terminal_kind != "FAILURE" or ancestor.family not in ("PRIMARY", "REPAIR")
            for ancestor in ordered[:root_idx]
        ):
            raise ValueError("operational ancestor of repaired PRIMARY is not a failure")
        if any(ancestor.run_id in reviews for ancestor in ordered[:root_idx]):
            raise ValueError("failed operational ancestor must not have a semantic review")
    frontier = CorrectionFrontier.from_primary(semantic_root.run_id, primary_review)
    for item in ordered[root_idx + 1:]:
        review = reviews.get(item.run_id)
        if review is None:
            continue
        origin = _semantic_origin(item, lifecycle)
        predecessor = origin.semantic_predecessor
        if predecessor is None:
            if origin.parent_run_id is None:
                raise ValueError("legacy correction predecessor is missing")
            prior = reviews.get(origin.parent_run_id)
            if prior is None:
                raise ValueError("legacy correction predecessor review is missing")
            predecessor = _operator().RemediationPredecessor(
                source_run_id=origin.parent_run_id,
                review_id=origin.review_id,
                finding_id=origin.finding_id,
                reviewed_sha=prior.reviewed_sha,
            )
        frontier = frontier.advance(
            delta_run_id=item.run_id,
            delta_review=review,
            predecessor=predecessor,
        )
    return frontier


def _frontier_identities(frontier: CorrectionFrontier) -> tuple[Mapping[str, str], ...]:
    if len(frontier) > _OUTSTANDING_FINDING_LIMIT:
        raise OverflowError("outstanding finding frontier exceeds fixed bound")
    return tuple(
        {
            "source_run_id": item.source_run_id,
            "review_id": item.review_id,
            "finding_id": item.finding_id,
            "reviewed_sha": item.reviewed_sha,
        }
        for item in frontier.findings
    )


def _resolve_cumulative_execution_base(
    repo: Path,
    task: Task,
    lifecycle: RemoteTaskLifecycle,
    *,
    source_run_id: str,
    review_id: str,
    finding_id: str,
    reviewed_sha: str,
    semantic_review: Review | None = None,
) -> tuple[str, str]:
    """Resolve the exact operational tip while retaining semantic finding identity."""

    decoded, reviews = _decode_remote_lifecycle(repo, task, lifecycle)
    if semantic_review is not None:
        # A first correction may carry the canonical review only on its selected
        # REMEDIATION lineage.  Reconstruct that decision solely for its exact
        # current-revision source RESULT; never infer it from the operational tip.
        if (
            semantic_review.review_id != review_id
            or semantic_review.reviewed_sha != reviewed_sha
        ):
            raise ValueError("selected semantic review identity is inconsistent")
        sources = [
            item
            for item in decoded
            if item.run_id == source_run_id
            and item.terminal_kind == "RESULT"
            and item.candidate_sha == reviewed_sha
        ]
        if len(sources) != 1:
            raise ValueError("selected semantic review source is missing or ambiguous")
        published_review = reviews.get(source_run_id)
        if published_review is not None and published_review != semantic_review:
            raise ValueError("selected semantic review conflicts with published review")
        reviews.setdefault(source_run_id, semantic_review)
    children: dict[str, list[_LifecycleRun]] = {}
    for item in decoded:
        parent_id = _operational_parent(item)
        if parent_id is not None:
            children.setdefault(parent_id, []).append(item)
    if any(len(value) != 1 for value in children.values()):
        raise ValueError("cumulative correction lineage has competing continuations")
    tips = [item for item in decoded if item.run_id not in children]
    if len(tips) != 1 or tips[0].terminal_kind != "RESULT":
        raise ValueError("cumulative correction tip is missing or ambiguous")
    tip = tips[0]
    frontier = _derive_tip_frontier(tip, decoded, reviews)
    expected = (source_run_id, review_id, finding_id, reviewed_sha)
    if not any(item.key == expected for item in frontier.findings):
        raise ValueError("selected semantic finding is not outstanding at execution base")
    if not _operator()._git_is_ancestor(repo, reviewed_sha, tip.candidate_sha):
        raise ValueError("cumulative execution base does not descend from reviewed SHA")
    if not _operator()._git_is_ancestor(repo, lifecycle.main_sha, tip.candidate_sha):
        from .correction_integration import resolve_valid_integration

        integration = resolve_valid_integration(
            repo,
            task_id=task.task_id,
            task_revision=task.revision,
            cumulative_tip_run_id=tip.run_id,
            cumulative_tip_candidate_sha=tip.candidate_sha,
            authorized_main_sha=lifecycle.main_sha,
            require_remote=True,
        )
        if integration is None:
            raise ValueError("current main and cumulative execution base require integration")
        integrated_base = {
            "version": 1,
            "kind": "INTEGRATED",
            "cumulative_tip_run_id": tip.run_id,
            "cumulative_tip_candidate_sha": tip.candidate_sha,
            "authorized_main_sha": lifecycle.main_sha,
            "integration_candidate_sha": integration.integration_candidate_sha,
            "integration_id": integration.integration_id,
        }
        return CumulativeExecutionBase(
            tip.run_id, integration.integration_candidate_sha, integrated_base
        )
    return CumulativeExecutionBase(tip.run_id, tip.candidate_sha, None)


def _local_pending_runs(
    repo: Path,
    state: Any,
    task: Task,
    remote: list[_LifecycleRun],
    reviews: Mapping[str, Review],
    *,
    local_repository: Path | None = None,
) -> tuple[list[_LifecycleRun], list[_LifecycleRun]]:
    op = _operator()
    remote_kinds = {(item.run_id, item.terminal_kind) for item in remote}
    terminal_pending: list[_LifecycleRun] = []
    active_pending: list[_LifecycleRun] = []
    prefix = task_run_prefix(task.task_id)
    transport_blockers: dict[str, Mapping[str, Any]] = {}

    def terminal_paths(directory: Path):
        for path in directory.glob(f"{prefix}*.json"):
            sidecar = re.fullmatch(re.escape(prefix) + r"\d{3,}\.transport\.json", path.name)
            if sidecar:
                if path.stat().st_size > 65536:
                    raise ValueError("unbounded transport sidecar")
                data = _lifecycle_document(path.read_bytes(), "transport sidecar")
                typed = set(data) == {"kind", "run_id", "subject_sha", "code", "error"}
                if (set(data) not in ({"error"}, {"run_id", "error"}) and not typed
                        or not isinstance(data.get("error"), str) or not data["error"]
                        or ("run_id" in data and data["run_id"] != path.name.removesuffix(".transport.json"))):
                    raise ValueError("malformed transport sidecar")
                if typed:
                    from .review_transport import FAILURE_TRANSPORT_CODES
                    if (data["kind"] != "FAILURE_TRANSPORT_BLOCKER" or not isinstance(data["code"], str)
                            or data["code"] not in FAILURE_TRANSPORT_CODES or not isinstance(data["subject_sha"], str)
                            or re.fullmatch(r"[0-9a-f]{40}", data["subject_sha"]) is None):
                        raise ValueError("invalid typed transport blocker")
                    transport_blockers[data["run_id"]] = dict(code=data["code"], subject_sha=data["subject_sha"],
                                                             source_digest=verification_digest(data))
                continue
            if not re.fullmatch(re.escape(prefix) + r"\d{3,}\.json", path.name):
                raise ValueError("malformed persisted terminal filename")
            yield path

    if not state.runs.is_dir():
        if any(directory.is_dir() and any(terminal_paths(directory))
               for directory in (state.results, state.failures)):
            raise ValueError("persisted terminal has no RUN")
        return terminal_pending, active_pending
    for directory in (state.results, state.failures):
        if directory.is_dir() and any(not (state.runs / path.name).is_file()
                                     for path in terminal_paths(directory)):
            raise ValueError("persisted terminal has no RUN")
    for path in state.runs.glob(f"{prefix}*.json"):
        run_id = path.stem
        raw = path.read_bytes()
        run_document = _lifecycle_document(raw, "persisted RUN")
        if not isinstance(run_document, Mapping):
            raise ValueError("persisted RUN must be a mapping")
        run, family, review_id, finding_id = _decode_lifecycle_run(raw, run_id=run_id)
        if run.task.id != task.task_id or run.task.revision > task.revision:
            raise ValueError("persisted RUN TASK identity is invalid")
        if run.task.revision != task.revision:
            if any(item.run_id == run_id for item in remote):
                raise ValueError("persisted RUN TASK conflicts with canonical terminal")
            continue
        require_same_affinity(task, run)
        if run.run_id != run_id or run.status != "ACTIVE":
            raise ValueError("persisted RUN identity is invalid")
        same_run = [item for item in remote if item.run_id == run_id]
        parent_run_id = None
        correction = None
        semantic_predecessor = None
        execution_base_run_id = None
        execution_base = None
        repair_path = state.repairs / path.name
        if repair_path.is_file():
            if family != "PRIMARY":
                raise ValueError("local RUN has competing correction families")
            repair_execution = _lifecycle_document(repair_path.read_bytes(), "persisted REPAIR")
            if not isinstance(repair_execution, Mapping):
                raise ValueError("persisted REPAIR execution must be a mapping")
            # Private structural/privacy validation is not canonical equality.
            # A transported correction is compared with its recognized public
            # counterpart below; a pending one must remain pending.
            _validated_private_public_view(repair_execution, "METADATA")
            correction = repair_execution
            parent_run_id = repair_execution.get("failed_run_id")
            failure = repair_execution.get("failure")
            authorization = repair_execution.get("repair")
            authorization_sha = repair_execution.get("repair_authorization_sha")
            embedded_run = repair_execution.get("run")
            correction_task = repair_execution.get("task")
            if (
                not isinstance(parent_run_id, str)
                or not parent_run_id
                or repair_execution.get("failed_head_sha") != run.base_sha
                or not isinstance(repair_execution.get("root_base_sha"), str)
                or not repair_execution.get("root_base_sha")
                or not isinstance(failure, Mapping)
                or failure.get("run_id") != parent_run_id
                or failure.get("failed_head_sha") != run.base_sha
                or not isinstance(authorization, Mapping)
                or authorization.get("failed_run_id") != parent_run_id
                or authorization.get("failed_head_sha") != run.base_sha
                or ("task" in authorization and (
                    not isinstance(authorization["task"], Mapping)
                    or type(authorization["task"].get("revision")) is not int
                    or dict(authorization["task"]) != {"id": task.task_id, "revision": task.revision}
                ))
                or (
                    authorization_sha is not None
                    and (
                        not isinstance(authorization_sha, str)
                        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", authorization_sha)
                        is None
                    )
                )
                or not isinstance(embedded_run, Mapping)
                or dict(embedded_run) != dict(run_document)
                or not isinstance(correction_task, Mapping)
                or correction_task.get("task_id") != task.task_id
                or type(correction_task.get("revision")) is not int
                or correction_task.get("revision") != task.revision
                or document_affinity(correction_task) != task.return_affinity
            ):
                raise ValueError("persisted REPAIR execution lineage is invalid")
            family = "REPAIR"
        elif family == "REMEDIATION":
            local_value = run_document
            execution = op._remediation_execution_from_data(local_value["execution"])
            if "predecessor" in local_value:
                semantic_predecessor = op._parse_remediation_predecessor(
                    local_value["predecessor"]
                )
            if "execution_base" in local_value:
                execution_base = op._parse_remediation_execution_base(
                    local_value["execution_base"]
                )
                execution_base_run_id = (
                    execution_base.cumulative_tip_run_id
                    if execution_base.is_integrated
                    else execution_base.run_id
                )
            elif execution.remediation.reviewed_sha != run.base_sha:
                raise ValueError("persisted legacy REMEDIATION reviewed SHA is invalid")
            parents = [
                parent
                for parent in remote
                if (
                    (semantic_predecessor is None and parent.candidate_sha == run.base_sha)
                    or (
                        semantic_predecessor is not None
                        and parent.run_id == semantic_predecessor.source_run_id
                        and parent.candidate_sha == semantic_predecessor.reviewed_sha
                    )
                )
                and parent.terminal_kind == "RESULT"
                and parent.run_id in reviews
                and reviews[parent.run_id].review_id == review_id
                and reviews[parent.run_id].reviewed_sha
                == execution.remediation.reviewed_sha
                and finding_id in {
                    finding.id for finding in reviews[parent.run_id].findings
                }
            ]
            if len(parents) == 1:
                parent_run_id = parents[0].run_id
        if family == "REPAIR":
            parents = [
                parent
                for parent in remote
                if parent.run_id == parent_run_id
                and parent.candidate_sha == run.base_sha
                and parent.terminal_kind == "FAILURE"
                and correction is not None
                and isinstance(correction.get("failure"), Mapping)
                and _matches_canonical_failure(correction["failure"], parent.terminal,
                                               canonical_workspace=parent.run.workspace)
                and parent.terminal.get("base_sha") == parent.run.base_sha
            ]
            if len(parents) != 1:
                raise ValueError("persisted REPAIR predecessor is missing or ambiguous")
            root_base, result_base = _repair_predecessor_bases(parents[0], remote)
            if (correction["root_base_sha"] != root_base
                    or ("result_base_sha" in correction
                        and correction["result_base_sha"] != result_base)):
                raise ValueError("persisted REPAIR base lineage mismatch")
            if parents[0].family in ("REMEDIATION", "REPAIR"):
                # Match canonical lifecycle decoding: correction identity belongs
                # to the exact failed correction chain, never to repair prose or
                # a task-wide finding search.  A PRIMARY origin intentionally
                # leaves both values unset.
                review_id = parents[0].review_id
                finding_id = parents[0].finding_id
            if not same_run:
                # Without a transported child, validate the private authorization
                # contract independently; this cannot prove canonical equality.
                publication_module._validate_repair_authorization(
                    correction["repair"], failed_run_id=parent_run_id,
                    failed_head_sha=run.base_sha, task=task,
                    failed_changed_files=set(parents[0].terminal["candidate"]["changed_files"]),
                )
        result_path = state.results / path.name
        failure_path = state.failures / path.name
        has_result = result_path.is_file()
        has_failure = failure_path.is_file()
        if has_result and has_failure:
            raise ValueError("persisted RUN has conflicting terminal state")
        if not has_result and not has_failure:
            if any(item.run_id == run_id for item in remote):
                raise ValueError("persisted RUN is missing its canonical terminal")
            if not any(item.run_id == run_id for item in remote):
                active_pending.append(
                    _LifecycleRun(
                        run_id, family, run, run.base_sha, "ACTIVE", {},
                        parent_run_id, review_id, finding_id,
                        correction=correction, run_document=run_document,
                        semantic_predecessor=semantic_predecessor,
                        execution_base_run_id=execution_base_run_id,
                        execution_base=execution_base,
                    )
                )
            continue
        kind = "RESULT" if has_result else "FAILURE"
        value = _lifecycle_document(
            (result_path if has_result else failure_path).read_bytes(), "persisted terminal",
            private_original=True,
        )
        if not isinstance(value, Mapping):
            raise ValueError("persisted terminal document must be a mapping")
        package = None
        if has_result:
            result_data = value.get("result")
            candidate_sha = (
                result_data.get("head_sha")
                if isinstance(result_data, Mapping)
                else None
            )
            if not isinstance(candidate_sha, str) or not candidate_sha:
                raise ValueError("persisted RESULT has no candidate head")
            package = _validate_lifecycle_result(
                task=task,
                run=run,
                family=family,
                run_document=run_document,
                terminal=value,
                candidate_sha=candidate_sha,
            )
            _validated_private_public_view(value, "RESULT")
        else:
            candidate_sha = value.get("failed_head_sha")
            if not isinstance(candidate_sha, str) or not candidate_sha:
                raise ValueError("persisted FAILURE has no failed head")
            _validate_lifecycle_failure(
                repo=repo,
                task=task,
                run=run,
                terminal=value,
                candidate_sha=candidate_sha,
                candidate_available=True,
                continuation_of=parent_run_id if family == "REPAIR" else None,
                public_representation=False,
            )
        recovery_document = None
        recovery_path = state.root / "publication-recovery" / path.name
        if recovery_path.is_file():
            recovery_document, parent_run_id = _decode_publication_continuation(
                repo, task=task, run=run, family=family, parent_run_id=parent_run_id,
                raw=recovery_path.read_bytes(), candidate_sha=candidate_sha,
                package=package,
                local_repository=local_repository,
            )
        transport_blocker = transport_blockers.get(run_id)
        if transport_blocker is not None and transport_blocker["subject_sha"] != candidate_sha:
            raise ValueError("transport blocker does not bind its candidate")
        if same_run:
            canonical = same_run[0] if len(same_run) == 1 else None
            if (
                canonical is None
                or (run_id, kind) not in remote_kinds
                or canonical.candidate_sha != candidate_sha
                or canonical.family != family
                or canonical.parent_run_id != parent_run_id
                or canonical.execution_base_run_id != execution_base_run_id
                or canonical.execution_base != execution_base
                or canonical.review_id != review_id
                or canonical.finding_id != finding_id
                or canonical.publication_recovery != recovery_document
                or canonical.run_document is None
                or not _matches_public_document(run_document, canonical.run_document, "RUN")
                or not _matches_public_document(value, canonical.terminal, kind if has_result else "METADATA")
                or not _matches_public_document(correction, canonical.correction, "METADATA")
            ):
                raise ValueError("local and canonical terminal state conflict")
            continue
        terminal_pending.append(
            _LifecycleRun(
                run_id, family, run, candidate_sha, kind, value,
                parent_run_id=parent_run_id,
                review_id=review_id, finding_id=finding_id,
                correction=correction, run_document=run_document,
                semantic_predecessor=semantic_predecessor,
                execution_base_run_id=execution_base_run_id,
                execution_base=execution_base,
                publication_recovery=recovery_document,
                transport_blocker=transport_blocker,
            )
        )
    return terminal_pending, active_pending


def _is_exact_local_correction_tip(
    pending: _LifecycleRun, remote: list[_LifecycleRun]
) -> bool:
    """Recognize one local child that exactly continues the canonical remote tip."""

    if pending.family not in ("REMEDIATION", "REPAIR"):
        return False
    remote_ids = [item.run_id for item in remote]
    if len(set(remote_ids)) != len(remote_ids):
        return False
    identities = set(remote_ids)
    children: dict[str, list[str]] = {}
    for item in remote:
        parent_id = _operational_parent(item)
        if parent_id is None:
            continue
        if parent_id not in identities:
            return False
        children.setdefault(parent_id, []).append(item.run_id)
    if any(len(set(value)) != 1 for value in children.values()):
        return False
    tips = [item for item in remote if item.run_id not in children]
    return len(tips) == 1 and _operational_parent(pending) == tips[0].run_id


def observe_unified_state(
    task_id: str, *, repo: str | Path | None = None
) -> UnifiedStateObservation:
    """Reduce the exact current TASK lifecycle without acquiring mutation authority."""

    op = _operator()
    root = op.resolve_repository(repo)
    task = op.load_task(root, task_id)
    state = op._runtime_paths_readonly(root)
    admission_context = _bounded_admission_context(state, task)
    try:
        with op._remote_observation_repository(root) as observer:
            resolve_lifecycle_fn = getattr(
                op, "resolve_remote_task_lifecycle", resolve_remote_task_lifecycle
            )
            lifecycle = resolve_lifecycle_fn(
                observer, task_id=task.task_id, task_revision=task.revision
            )
            remote, reviews = _decode_remote_lifecycle(observer, task, lifecycle)
            pending_terminal, pending_active = _local_pending_runs(
                observer, state, task, remote, reviews, local_repository=root
            )
            if (
                len(pending_terminal) > 1
                or (pending_terminal and pending_active)
                or (
                    pending_terminal
                    and remote
                    and not _is_exact_local_correction_tip(
                        pending_terminal[0], remote
                    )
                )
            ):
                return _unified_blocked(
                    task, "AMBIGUOUS_LOCAL_TIPS", admission_failures=admission_context
                )
            if pending_terminal:
                tip = pending_terminal[0]
                from .review_transport import failure_context
                return UnifiedStateObservation(
                    task.task_id, task.revision, "TRANSPORT", "RETRY_TRANSPORT",
                    run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                    failed_run_id=(tip.run_id if tip.terminal_kind == "FAILURE" else None),
                    failed_head_sha=(tip.candidate_sha if tip.terminal_kind == "FAILURE" else None),
                    admission_failures=admission_context,
                    failure_context=(failure_context(tip.terminal, candidate_available=tip.candidate_available)
                                     if tip.terminal_kind == "FAILURE" else None),
                    blocker=tip.transport_blocker,
                )
            if len(pending_active) > 1 or (
                pending_active
                and remote
                and not _is_exact_local_correction_tip(pending_active[0], remote)
            ):
                return _unified_blocked(
                    task, "AMBIGUOUS_ACTIVE_TIPS", admission_failures=admission_context
                )
            if pending_active:
                return UnifiedStateObservation(
                    task.task_id, task.revision, "WAIT", "WAIT",
                    run_id=pending_active[0].run_id,
                    admission_failures=admission_context,
                )

            kinds: dict[str, set[str]] = {}
            for item in remote:
                kinds.setdefault(item.run_id, set()).add(item.terminal_kind)
            conflicts = sorted(run_id for run_id, values in kinds.items() if len(values) > 1)
            if conflicts:
                if len(conflicts) != 1 or len(kinds) != 1:
                    return _unified_blocked(
                        task, "TERMINAL_CONFLICT", admission_failures=admission_context
                    )
                conflict = conflicts[0]
                try:
                    resolve_recovery_fn = getattr(
                        op, "resolve_remote_primary_recovery", resolve_remote_primary_recovery
                    )
                    recovery = resolve_recovery_fn(observer, run_id=conflict)
                    recovered_run, family, _, _ = _decode_lifecycle_run(
                        recovery.success_run, run_id=conflict
                    )
                    if (
                        family != "PRIMARY"
                        or recovered_run.return_affinity != task.return_affinity
                        or recovered_run.task.id != task.task_id
                        or recovered_run.task.revision != task.revision
                    ):
                        raise ValueError("recovery does not bind current PRIMARY TASK")
                except (ReviewTransportError, ValueError, KeyError, TypeError, UnicodeError):
                    return _unified_blocked(
                        task, "TERMINAL_CONFLICT", admission_failures=admission_context,
                        run_id=conflict,
                    )
                return UnifiedStateObservation(
                    task.task_id, task.revision, "RECOVERY", "RECOVER_PRIMARY",
                    run_id=conflict, source_run_id=conflict,
                    candidate_sha=recovery.candidate_sha,
                    admission_failures=admission_context,
                )
            if not remote:
                return UnifiedStateObservation(
                    task.task_id, task.revision, "READY", "EXECUTE_PRIMARY",
                    admission_failures=admission_context,
                )

            children: dict[str, list[str]] = {}
            identities = {item.run_id for item in remote}
            for item in remote:
                operational_parent = _operational_parent(item)
                if operational_parent is not None:
                    if operational_parent not in identities:
                        raise ValueError("correction parent is not canonical")
                    children.setdefault(operational_parent, []).append(item.run_id)
            if any(len(set(value)) != 1 for value in children.values()):
                return _unified_blocked(
                    task, "COMPETING_CONTINUATIONS", admission_failures=admission_context
                )
            tips = [item for item in remote if item.run_id not in children]
            if len(tips) != 1:
                return _unified_blocked(
                    task, "AMBIGUOUS_LINEAGE_TIP", admission_failures=admission_context
                )
            tip = tips[0]
            if tip.terminal_kind == "FAILURE":
                from .review_transport import failure_context
                context = failure_context(tip.terminal, artifact_sha=dict(lifecycle.observed_refs).get(
                    "refs/heads/aios/failure-artifacts/" + tip.run_id), candidate_available=tip.candidate_available)
                candidate = tip.terminal.get("candidate")
                if (
                    not tip.candidate_available
                    or not isinstance(candidate, Mapping)
                    or candidate.get("repairable") is not True
                    or candidate.get("transportable") is not True
                ):
                    return _unified_blocked(
                        task, "NON_REPAIRABLE_FAILURE", admission_failures=admission_context,
                        run_id=tip.run_id, failed_run_id=tip.run_id,
                        failed_head_sha=tip.candidate_sha,
                        failure_context=context,
                    )
                selectors = [
                    item for item in lifecycle.repair_selectors if item[0] == tip.run_id
                ]
                if not selectors:
                    return UnifiedStateObservation(
                        task.task_id, task.revision, "CORRECTION", "AUTHOR_REPAIR",
                        run_id=tip.run_id, failed_run_id=tip.run_id,
                        failed_head_sha=tip.candidate_sha,
                        admission_failures=admission_context,
                        failure_context=context,
                    )
                if len(selectors) != 1:
                    return _unified_blocked(
                        task, "COMPETING_REPAIRS", admission_failures=admission_context,
                        run_id=tip.run_id, failed_run_id=tip.run_id,
                        failed_head_sha=tip.candidate_sha,
                        failure_context=context,
                    )
                repair_authorization = json.loads(
                    selectors[0][2].decode("utf-8", errors="strict")
                )
                if not isinstance(repair_authorization, Mapping):
                    raise ValueError("canonical REPAIR authorization must be a mapping")
                preflight_repair_fn = getattr(op, "preflight_repair", preflight_repair)
                preflight = preflight_repair_fn(
                    tip.run_id, repo=root, repair=repair_authorization,
                    required_repair_sha=selectors[0][1],
                )
                correction = preflight.as_dict()
                if preflight.status != "READY":
                    return _unified_blocked(
                        task, "CORRECTION_PREFLIGHT_BLOCKED",
                        phase=preflight.phase, reason_code=preflight.reason_code,
                        admission_failures=admission_context, run_id=tip.run_id,
                        failed_run_id=tip.run_id, failed_head_sha=tip.candidate_sha,
                        correction_sha=selectors[0][1], correction=correction,
                        failure_context=context,
                    )
                return UnifiedStateObservation(
                    task.task_id, task.revision, "CORRECTION", "EXECUTE_REPAIR",
                    run_id=tip.run_id, failed_run_id=tip.run_id,
                    failed_head_sha=tip.candidate_sha, correction=correction,
                    correction_sha=selectors[0][1],
                    correction_document=repair_authorization,
                    admission_failures=admission_context,
                    failure_context=context,
                )

            review = reviews.get(tip.run_id)
            result = validate_result(tip.terminal["result"])
            if review is None:
                return UnifiedStateObservation(
                    task.task_id, task.revision, "REVIEW", "SEMANTIC_REVIEW",
                    run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                    source_run_id=(
                        tip.parent_run_id if tip.family == "REMEDIATION" else None
                    ),
                    failed_run_id=(
                        tip.parent_run_id if tip.family == "REPAIR" else None
                    ),
                    review_id=tip.review_id, finding_id=tip.finding_id,
                    admission_failures=admission_context,
                    _scope_context=_SemanticScopeContext(tip, tuple(remote), reviews),
                )
            prior_review = None
            if review.prior_finding_id is not None:
                prior_review = _correction_prior_review(tip, remote, reviews)
                if (
                    review.prior_finding_id != tip.finding_id
                ):
                    raise ValueError("DELTA review predecessor is missing or ambiguous")
            validate_review(task=task, result=result, review=review, prior_review=prior_review)
            if review.reviewed_sha != tip.candidate_sha:
                raise ValueError("review decision does not bind candidate ref")
            if review.verdict == "BLOCKED":
                return _unified_blocked(
                    task, "SEMANTIC_REVIEW_BLOCKED", admission_failures=admission_context,
                    run_id=tip.run_id, review_id=review.review_id,
                    candidate_sha=tip.candidate_sha, reviewed_sha=review.reviewed_sha,
                )
            try:
                frontier = _derive_tip_frontier(tip, remote, reviews)
                outstanding = _frontier_identities(frontier)
            except OverflowError:
                over_bound_frontier = _derive_tip_frontier(tip, remote, reviews)
                return _unified_blocked(
                    task, "OUTSTANDING_FINDINGS_BOUND_EXCEEDED",
                    admission_failures=admission_context,
                    blocker_details={
                        "count": len(over_bound_frontier),
                        "limit": _OUTSTANDING_FINDING_LIMIT,
                    },
                )
            if review.verdict == "CHANGES_REQUIRED" or not frontier.is_empty:
                if any(
                    not op._git_is_ancestor(
                        observer, identity["reviewed_sha"], tip.candidate_sha
                    )
                    for identity in outstanding
                ):
                    return _unified_blocked(
                        task, "CUMULATIVE_BASE_INVALID",
                        admission_failures=admission_context,
                        run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                        execution_base_run_id=tip.run_id,
                        execution_base_sha=tip.candidate_sha,
                    )
                if not op._git_is_ancestor(
                    observer, lifecycle.main_sha, tip.candidate_sha
                ):
                    from .correction_integration import resolve_valid_integration

                    integration = resolve_valid_integration(
                        observer,
                        task_id=task.task_id,
                        task_revision=task.revision,
                        cumulative_tip_run_id=tip.run_id,
                        cumulative_tip_candidate_sha=tip.candidate_sha,
                        authorized_main_sha=lifecycle.main_sha,
                        require_remote=True,
                    )
                    if integration is None:
                        return _unified_blocked(
                            task, "INTEGRATION_REQUIRED",
                            admission_failures=admission_context,
                            run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                            execution_base_run_id=tip.run_id,
                            execution_base_sha=tip.candidate_sha,
                        )
                    effective_execution_base_sha = integration.integration_candidate_sha
                    integrated_base = {
                        "version": 1,
                        "kind": "INTEGRATED",
                        "cumulative_tip_run_id": tip.run_id,
                        "cumulative_tip_candidate_sha": tip.candidate_sha,
                        "authorized_main_sha": lifecycle.main_sha,
                        "integration_candidate_sha": integration.integration_candidate_sha,
                        "integration_id": integration.integration_id,
                    }
                else:
                    effective_execution_base_sha = tip.candidate_sha
                    integrated_base = None
                selector_matches = [
                    (identity, selector)
                    for identity in outstanding
                    for selector in lifecycle.remediation_selectors
                    if selector[0] == identity["source_run_id"]
                    and selector[1] == identity["finding_id"]
                ]
                if not selector_matches:
                    if len(outstanding) != 1:
                        return UnifiedStateObservation(
                            task.task_id, task.revision, "CORRECTION", "AUTHOR_REMEDIATION",
                            run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                            execution_base_run_id=tip.run_id,
                            execution_base_sha=effective_execution_base_sha,
                            integrated_base=integrated_base,
                            outstanding_findings=outstanding,
                            admission_failures=admission_context,
                        )
                    identity = outstanding[0]
                    return UnifiedStateObservation(
                        task.task_id, task.revision, "CORRECTION", "AUTHOR_REMEDIATION",
                        run_id=tip.run_id,
                        source_run_id=identity["source_run_id"],
                        review_id=identity["review_id"],
                        finding_id=identity["finding_id"],
                        candidate_sha=tip.candidate_sha,
                        reviewed_sha=identity["reviewed_sha"],
                        execution_base_run_id=tip.run_id,
                        execution_base_sha=effective_execution_base_sha,
                        integrated_base=integrated_base,
                        outstanding_findings=outstanding,
                        admission_failures=admission_context,
                    )
                if len(selector_matches) != 1:
                    return _unified_blocked(
                        task, "COMPETING_REMEDIATIONS", admission_failures=admission_context,
                        run_id=tip.run_id, candidate_sha=tip.candidate_sha,
                    )
                identity, selector = selector_matches[0]
                preflight_remediation_fn = getattr(
                    op, "preflight_remediation", preflight_remediation
                )
                preflight = preflight_remediation_fn(
                    task.task_id, finding_id=identity["finding_id"],
                    source_run_id=identity["source_run_id"],
                    approved_remediation_sha=selector[2], repo=root,
                )
                correction = preflight.as_dict()
                if preflight.status != "READY":
                    return _unified_blocked(
                        task, "CORRECTION_PREFLIGHT_BLOCKED",
                        phase=preflight.phase, reason_code=preflight.reason_code,
                        admission_failures=admission_context, run_id=tip.run_id,
                        source_run_id=identity["source_run_id"],
                        review_id=identity["review_id"],
                        finding_id=identity["finding_id"], reviewed_sha=identity["reviewed_sha"],
                        correction_sha=selector[2], correction=correction,
                        outstanding_findings=outstanding,
                    )
                return UnifiedStateObservation(
                    task.task_id, task.revision, "CORRECTION", "EXECUTE_REMEDIATION",
                    run_id=tip.run_id, source_run_id=identity["source_run_id"],
                    review_id=identity["review_id"], finding_id=identity["finding_id"],
                    candidate_sha=tip.candidate_sha, reviewed_sha=identity["reviewed_sha"],
                    execution_base_run_id=preflight.execution_base_run_id,
                    execution_base_sha=preflight.execution_base_sha,
                    integrated_base=integrated_base,
                    correction_sha=selector[2], correction=correction,
                    outstanding_findings=outstanding,
                    admission_failures=admission_context,
                )
            if op._git_is_ancestor(observer, tip.candidate_sha, lifecycle.main_sha):
                return UnifiedStateObservation(
                    task.task_id, task.revision, "DONE", "DONE",
                    run_id=tip.run_id, review_id=review.review_id,
                    candidate_sha=tip.candidate_sha, reviewed_sha=review.reviewed_sha,
                    outstanding_findings=outstanding,
                    admission_failures=admission_context,
                )
            # Reuse the safe-publication lineage validator in the isolated
            # observer before classifying ancestry.  Runtime PASS and a parsed
            # PASS document alone do not establish publication eligibility.
            try:
                publication_candidate, *_ = publication_module._load_success_lineage(
                    observer,
                    remote=resolve_transport_remote(observer),
                    run_id=tip.run_id,
                    decision_sha=next(
                        item.decision_sha
                        for item in lifecycle.reviews
                        if item.run_id == tip.run_id
                    ),
                )
                if publication_candidate != tip.candidate_sha:
                    raise ValueError("publication candidate changed during observation")
            except (
                publication_module.PublicationError,
                ReviewTransportError,
                StopIteration,
                ValueError,
            ):
                return _unified_blocked(
                    task, "PUBLICATION_LINEAGE_INVALID",
                    admission_failures=admission_context, run_id=tip.run_id,
                    review_id=review.review_id, candidate_sha=tip.candidate_sha,
                    reviewed_sha=review.reviewed_sha,
                )
            if op._git_is_ancestor(observer, lifecycle.main_sha, tip.candidate_sha):
                return UnifiedStateObservation(
                    task.task_id, task.revision, "PUBLICATION", "PUBLICATION",
                    run_id=tip.run_id, review_id=review.review_id,
                    candidate_sha=tip.candidate_sha, reviewed_sha=review.reviewed_sha,
                    admission_failures=admission_context,
                )
            recovery = publication_module._classify_recovery(observer, run_id=tip.run_id,
                reviewed_sha=tip.candidate_sha, main_sha=lifecycle.main_sha,
                modification_scope=tuple(task.scope.modify))
            if recovery.report.classification == "CLEAN_RECOVERY_ELIGIBLE" and tip.publication_recovery is None:
                return UnifiedStateObservation(task.task_id, task.revision, "PUBLICATION", "PUBLICATION",
                    run_id=tip.run_id, review_id=review.review_id, candidate_sha=tip.candidate_sha,
                    reviewed_sha=review.reviewed_sha, admission_failures=admission_context)
            return _unified_blocked(task, "INTEGRATION_REQUIRED", admission_failures=admission_context,
                run_id=tip.run_id, review_id=review.review_id, candidate_sha=tip.candidate_sha,
                reviewed_sha=review.reviewed_sha)
    except RemoteQueryError as exc:
        raise op.OperatorError(
            f"Unified State canonical observation unavailable ({exc.category})"
        ) from exc
    except ReviewTransportError as exc:
        from .review_transport import FailureDiagnosticError
        return _unified_blocked(task, exc.code if isinstance(exc, FailureDiagnosticError) else "MALFORMED_CANONICAL_STATE",
                                admission_failures=admission_context)
    except (ReviewTransportError, publication_module.PublicationError,
            ArtifactValidationError, ReviewValidationError,
            KeyError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        # Canonical facts were acquired but cannot support a guessed continuation.
        return _unified_blocked(
            task, "MALFORMED_CANONICAL_STATE", admission_failures=admission_context
        )


def observe_semantic_review_scope(
    task_id: str, *, repo: str | Path | None = None
) -> dict[str, Any]:
    """Project the exact current unreviewed semantic subject from Unified State."""

    observation = observe_unified_state(task_id, repo=repo)
    context = observation._scope_context
    if observation.next_action != "SEMANTIC_REVIEW" or context is None:
        raise ValueError("no exact SEMANTIC_REVIEW obligation")

    tip, lifecycle, reviews = context.tip, list(context.lifecycle), context.reviews
    result = validate_result(tip.terminal["result"])
    if (
        tip.terminal_kind != "RESULT"
        or tip.run_id != observation.run_id
        or tip.candidate_sha != observation.candidate_sha
        or result.head_sha != tip.candidate_sha
        or tip.run_id in reviews
    ):
        raise ValueError("semantic review candidate identity is inconsistent")

    # The reducer already selected the sole canonical tip. Its operational
    # lineage validates parent uniqueness/cycles, including integrated bases.
    ordered = _ordered_lineage(tip, lifecycle)
    origin = next((item for item in reversed(ordered) if item.family != "REPAIR"), None)
    if origin is None or origin.family not in ("PRIMARY", "REMEDIATION"):
        raise ValueError("semantic review origin is missing or ambiguous")
    origin_index = ordered.index(origin)
    continuations = ordered[origin_index + 1:]
    if any(
        item.family != "REPAIR"
        or item.parent_run_id != parent.run_id
        or parent.terminal_kind != "FAILURE"
        or parent.run_id in reviews
        for parent, item in zip(ordered[origin_index:-1], continuations)
    ):
        raise ValueError("semantic review repair lineage is inconsistent")
    if origin != tip and (origin.terminal_kind != "FAILURE" or origin.run_id in reviews):
        raise ValueError("semantic review origin already has a review")

    prior_run_id = prior_review_id = prior_finding_id = None
    if origin.family == "PRIMARY":
        if ((origin_index != 0 and origin.publication_recovery is None)
                or tip.review_id is not None or tip.finding_id is not None):
            raise ValueError("PRIMARY semantic review identity is inconsistent")
        mode = "PRIMARY"
        semantic_base = origin.run.base_sha
    else:
        if _semantic_origin(tip, lifecycle) != origin:
            raise ValueError("DELTA semantic origin is ambiguous")
        prior_review = _correction_prior_review(tip, lifecycle, reviews)
        predecessor = origin.semantic_predecessor
        prior_run_id = origin.parent_run_id
        if (
            prior_run_id is None
            or tip.review_id is None
            or tip.finding_id is None
            or tip.finding_id not in {finding.id for finding in prior_review.findings}
            or (
                predecessor is not None
                and (
                    predecessor.source_run_id != prior_run_id
                    or predecessor.review_id != prior_review.review_id
                    or predecessor.finding_id != tip.finding_id
                    or predecessor.reviewed_sha != prior_review.reviewed_sha
                )
            )
        ):
            raise ValueError("DELTA predecessor identity is inconsistent")
        mode = "DELTA"
        semantic_base = prior_review.reviewed_sha
        prior_review_id = prior_review.review_id
        prior_finding_id = tip.finding_id

    body: dict[str, Any] = {
        "format": "AIOS_SEMANTIC_REVIEW_SCOPE",
        "version": 1,
        "kind": "SEMANTIC_REVIEW_SCOPE",
        "task": {"id": observation.task_id, "revision": observation.task_revision},
        "reviewed_run_id": tip.run_id,
        "review_mode": mode,
        "semantic_origin_run_id": origin.run_id,
        "semantic_base_sha": semantic_base,
        "latest_delta_base_sha": tip.run.base_sha,
        "reviewed_head_sha": tip.candidate_sha,
        "prior_review_run_id": prior_run_id,
        "prior_review_id": prior_review_id,
        "prior_finding_id": prior_finding_id,
    }
    if origin.publication_recovery is not None:
        plan = publication_module.publication_plan_from_data(origin.publication_recovery["plan"])
        body["publication_provenance"] = dict(source_run_id=plan.source_run_id,
            source_sha=plan.reviewed_sha, source_decision_sha=plan.decision_sha,
            source_artifacts_sha=plan.artifacts_sha, current_main_sha=plan.main_sha,
            recovery_identity=plan.identity, prior_pass_is_verdict=False)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    body["scope_fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return body


__all__ = [
    "UnifiedStateObservation",
    "observe_unified_state",
    "observe_semantic_review_scope",
]
