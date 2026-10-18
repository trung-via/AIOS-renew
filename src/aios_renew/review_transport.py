"""Reusable operator-layer GitHub transport for terminal RUN state."""

from __future__ import annotations
from contextvars import ContextVar

from .return_affinity import document_affinity

import hashlib
import json
import math
import re
import shlex
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from .task import SemanticTask

from .proof_coverage_contract import MappingReviewScope, SemanticProofIntake
from .terminal_attention import (
    TerminalAttentionError,
    publish_terminal_attention,
)


class ReviewTransportError(RuntimeError):
    """Raised when post-PASS review or artifact transport fails."""


PUBLIC_ARTIFACT_MAX_BYTES = 1024 * 1024
PUBLIC_FAILURE_MAX_BYTES = 16 * 1024 * 1024
LEGACY_PUBLIC_FAILURE_FORMAT = "AIOS_PUBLIC_FAILURE_V1"
PUBLIC_FAILURE_FORMAT = "AIOS_PUBLIC_FAILURE_V2"
PUBLIC_OBSERVATION_FORMAT = "AIOS_PRIVATE_OBSERVATION_V1"
INCOMPLETE_OBSERVATION_FORMAT = "AIOS_PRIVATE_OBSERVATION_V2"
_PROTECTED_DIAGNOSTICS = "Protected Runtime diagnostics remain local."


class FailureDiagnosticError(ReviewTransportError):
    """A bounded diagnostic blocker, never a successful observation."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(code if message is None else code + ": " + message)


_NATIVE_REASONS = frozenset({"TIMEOUT", "INTERRUPTED", "PROCESS_EXIT", "INVOCATION_FAILED", "OUTPUT_INVALID"})
FAILURE_TRANSPORT_CODES = frozenset({
    "FAILURE_PROJECTION_INVALID", "FAILURE_KIND_INVALID", "FAILURE_SOURCE_SIZE_EXCEEDED",
    "FAILURE_SOURCE_DOCUMENT_INVALID", "FAILURE_PUBLIC_PRIVACY_INVALID", "FAILURE_PUBLIC_INTEGRITY_INVALID",
    "FAILURE_PRIVATE_INTEGRITY_INVALID", "FAILURE_TRANSPORT_IDENTITY_INVALID",
    "FAILURE_TRANSPORT_AFFINITY_INVALID",
    "FAILURE_TRANSPORT_SOURCE_UNAVAILABLE",
    "FAILURE_CANDIDATE_NOT_PUBLISHABLE", "FAILURE_TRANSPORT_UNAVAILABLE",
})
_FAILURE_CATEGORIES = frozenset({
    "NATIVE_TIMEOUT", "NATIVE_INTERRUPTED", "NATIVE_OUTPUT_INVALID", "NATIVE_INVOCATION_FAILED",
    "NATIVE_AUTHENTICATION", "NATIVE_RATE_LIMIT", "NATIVE_QUOTA", "NATIVE_TRANSPORT",
    "NATIVE_EXIT", "NATIVE_EXIT_UNKNOWN", "VERIFICATION_TEST_FAILURE", "VERIFICATION_INTERRUPTED",
    "VERIFICATION_INTERNAL_ERROR", "VERIFICATION_USAGE_ERROR", "VERIFICATION_NO_TESTS",
    "VERIFICATION_COMMAND_FAILED", "COMPLETION_DIRTY", "COMPLETION_SCOPE", "COMPLETION_GATE",
    "RUNTIME_FAILURE",
})
_PUBLIC_ERROR_TYPES = frozenset({
    "CodexExecutionError", "CodexOutputError", "AntigravityExecutionError", "AntigravityOutputError", "RuntimeVerificationError",
    "KeyboardInterrupt", "OperatorError", "RuntimeError", "ArtifactValidationError", "RuntimeFailure",
})

# These observations explain an existing FAILURE. They are never verification
# reports, EVIDENCE, a completion decision, or a capability to read private bytes.
_DETAIL_ERRORS = frozenset({"AssertionError", "TypeError", "AttributeError", "ValueError",
    "NameError", "KeyError", "IndexError", "ImportError", "ModuleNotFoundError",
    "SyntaxError", "RuntimeError", "FixtureLookupError", "Failed", "UNKNOWN"})
_DETAIL_CAUSES = frozenset({"UNKNOWN", "MISSING_ACCEPTANCE_CLAIMS", "RESULT_PACKAGE_INVALID",
    "UNRESOLVED_ITEMS", "EXECUTOR_EVIDENCE_INVALID", "NATIVE_OUTPUT_INVALID",
    "NATIVE_TIMEOUT", "NATIVE_INTERRUPTED", "NATIVE_INVOCATION_FAILED", "NATIVE_EXIT"})
_RESPONSE_SHAPES = frozenset({"UNKNOWN", "UNAVAILABLE", "INCOMPLETE_CAPTURE", "EMPTY",
    "INVALID_JSON", "NON_OBJECT", "MISSING_RESULT", "MISSING_EVIDENCE", "INVALID_RESULT",
    "INVALID_EVIDENCE", "INVALID_ENVELOPE_FIELDS", "INVALID_RESULT_FIELDS", "INVALID_CLAIMS", "RESULT_PACKAGE_SHAPE"})


def _safe_test_identity(node: str) -> tuple[str | None, bool]:
    # Parameter values can contain secrets, paths, newlines, and even summary
    # delimiters. Never retain any part of that suffix.
    if not isinstance(node, str):
        return None, False
    identity, separator, _ = node.partition("[")
    valid = (len(identity) <= 512 and re.fullmatch(
        r"tests/(?:[A-Za-z_][A-Za-z0-9_]*/)*test_[A-Za-z0-9_]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*)*", identity)
        is not None)
    return identity if valid else None, bool(separator)


def _detail_error_types(text: str) -> list[str]:
    if not isinstance(text, str):
        return ["UNKNOWN"]
    found = {name for name in _DETAIL_ERRORS - {"UNKNOWN"} if re.search(
        r"(?:^|\n| - )(?:E\s+)?" + name + r"(?:\s*:|\b$)", text[:65536])}
    if re.search(r"(?m)^(?:E\s+)?\s*fixture '[^\r\n]*' not found\s*$", text[:65536]):
        found.add("FixtureLookupError")
    return sorted(found) or ["UNKNOWN"]


def _response_shape(native: Any) -> str:
    if not isinstance(native, dict):
        return "UNAVAILABLE"
    capture = native.get("stdout")
    if isinstance(capture, dict):
        if capture.get("truncated") is True:
            return "INCOMPLETE_CAPTURE"
        capture = capture.get("text") if capture.get("availability") == "captured" else None
    if not isinstance(capture, str):
        return "UNAVAILABLE"
    if not capture.strip():
        return "EMPTY"
    if len(capture) > 65536:
        return "INCOMPLETE_CAPTURE"
    # Follow the native adapter's final agent-message boundary. A task_complete
    # event, usage event, clean candidate, or independent test result is no proxy.
    last, direct_payload, saw_event = None, None, False
    decoded = True
    try:
        value = json.loads(capture)
    except (ValueError, TypeError):
        value = None
        decoded = False
    if not isinstance(value, dict) or not {"result", "evidence"} <= value.keys():
        for line in capture.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            saw_event = saw_event or isinstance(event.get("type"), str)
            if {"result", "evidence"} <= event.keys():
                direct_payload = event
            item = event.get("item")
            if (event.get("type") == "item.completed" and isinstance(item, dict)
                    and item.get("type") == "agent_message" and isinstance(item.get("text"), str)):
                last = item["text"]
        if last is not None:
            try:
                value = json.loads(last)
            except ValueError:
                return "INVALID_JSON"
        elif direct_payload is not None:
            value = direct_payload
        elif saw_event:
            return "UNKNOWN"
        elif not decoded:
            return "INVALID_JSON"
    if not isinstance(value, dict):
        return "NON_OBJECT"
    if "result" not in value:
        return "MISSING_RESULT"
    if "evidence" not in value:
        return "MISSING_EVIDENCE"
    if set(value) != {"result", "evidence"}:
        return "INVALID_ENVELOPE_FIELDS"
    if not isinstance(value["result"], dict):
        return "INVALID_RESULT"
    if not isinstance(value["evidence"], list):
        return "INVALID_EVIDENCE"
    result = value["result"]
    if set(result) != {"head_sha", "claims", "changed_files", "unresolved"}:
        return "INVALID_RESULT_FIELDS"
    claims = result["claims"]
    if (not isinstance(claims, list) or any(not isinstance(claim, dict)
            or set(claim) != {"id", "satisfies", "claim", "evidence"}
            or not isinstance(claim["id"], str) or not isinstance(claim["claim"], str)
            or any(not isinstance(claim[k], list) or any(not isinstance(x, str) for x in claim[k])
                   for k in ("satisfies", "evidence")) for claim in claims)):
        return "INVALID_CLAIMS"
    from .artifacts import ArtifactValidationError, validate_structural_result
    try:
        validate_structural_result(result)
    except (ArtifactValidationError, ValueError, TypeError, KeyError):
        return "INVALID_RESULT"
    return "RESULT_PACKAGE_SHAPE"


def safe_failure_detail(data: Mapping[str, Any]) -> dict[str, Any]:
    """Project only already-lawful, source-bound private observations.

    No path, URL, raw capture or caller-supplied diagnostic block is opened. The
    transport writer calls this after validating the private FAILURE's role.
    Historical public input cannot regain withheld observations.
    """
    error = data["error"]
    private = "public_projection" not in data
    source_digest = (_failure_digest(data) if private else
                     data["public_projection"]["private_failure_digest"])
    detail = dict(format="AIOS_SAFE_FAILURE_DETAIL", version=1, run_id=data["run_id"],
        subject_sha=data["failed_head_sha"], private_failure_digest=source_digest, phase=data["phase"],
        availability="UNAVAILABLE", verification=[], verification_truncated=False,
        cause="UNKNOWN", missing_acceptance_ids=[], final_response_shape="UNKNOWN",
        raw_output_withheld=True)
    if not private:
        return detail
    outcomes = error.get("verification", []) if data["phase"] == "VERIFICATION" else []
    for index, outcome in enumerate(outcomes[:32]):
        if outcome["exit_code"] == 0:
            continue
        nodes = []
        ref = outcome.get("verification")
        observed = ref.get("candidate") if isinstance(ref, dict) else None
        summary = outcome.get("summary", "")
        observation_complete = observed.get("complete") if isinstance(observed, dict) else None
        unstable = observed.get("unstable") if isinstance(observed, dict) else None
        if isinstance(observed, dict):
            reports = [r for r in observed["reports"] if r["outcome"] == "FAIL"]
            source_kind, digest = "PROTECTED_OBSERVATION", _failure_digest(observed)
            failure_count = observed["failure_count"]
        else:
            # A summary is an observed output excerpt, never a complete pytest
            # report. Exit code 1 alone identifies no individual failing test.
            source_kind, digest = "PROTECTED_SUMMARY", _failure_digest(summary)
            reports = []
            excerpt = summary[:65536]
            pytest_command = re.match(r"^python(?:3)? -m pytest(?: |$)", outcome["command"]) or outcome["command"] == "python scripts/aios_parallel_full_suite.py"
            header = re.search(r"(?m)^=+ short test summary info =+\s*$", excerpt) if pytest_command else None
            for line in (excerpt[header.end():].splitlines() if header else []):
                match = re.match(r"^(FAILED|ERROR) (tests/[^\r\n]+)", line)
                if match:
                    reports.append(dict(nodeid=match[2].split(" - ", 1)[0],
                        phase="unknown", detail=line.split(" - ", 1)[-1]))
            failure_count = None
        for report in reports[:32]:
            identity, parameters = _safe_test_identity(report["nodeid"])
            nodes.append(dict(identity=identity, identity_digest=_failure_digest(report["nodeid"]),
                parameters_withheld=parameters, phase=report["phase"],
                error_types=_detail_error_types(report.get("detail", ""))))
        detail["verification"].append(dict(index=index, command=_public_command(outcome["command"]),
            exit_code=outcome["exit_code"], source_kind=source_kind, source_digest=digest,
            availability="AVAILABLE" if observed and observation_complete and not unstable else
                "PARTIAL" if reports else "UNAVAILABLE",
            complete=observation_complete, unstable=unstable, failure_count=failure_count,
            failed_nodes=nodes, nodes_truncated=len(reports) > len(nodes)))
    detail["verification_truncated"] = len(outcomes) > 32
    message = error.get("message", "")
    if data["phase"] == "COMPLETION_GATE":
        missing = re.fullmatch(r"RESULT does not satisfy acceptance criteria: (AC[A-Za-z0-9_-]{1,64}(?:, AC[A-Za-z0-9_-]{1,64}){0,31})", message)
        if missing:
            detail.update(cause="MISSING_ACCEPTANCE_CLAIMS", missing_acceptance_ids=missing[1].split(", "))
        elif message.startswith("invalid canonical ResultPackage: "):
            detail["cause"] = "RESULT_PACKAGE_INVALID"
        elif message == "RESULT has unresolved items":
            detail["cause"] = "UNRESOLVED_ITEMS"
        elif message in {"executor structural output evidence must be empty",
                         "executor structural claim evidence references must be empty"}:
            detail["cause"] = "EXECUTOR_EVIDENCE_INVALID"
    elif data["phase"] == "EXECUTION":
        diagnostic = _safe_failure_diagnostic(data)
        category = diagnostic["category"]
        if category in _DETAIL_CAUSES:
            detail["cause"] = category
        if error.get("type") in {"CodexOutputError", "AntigravityOutputError"}:
            detail["final_response_shape"] = _response_shape(error.get("native_diagnostics"))
    if detail["cause"] != "UNKNOWN" or any(v["availability"] != "UNAVAILABLE" for v in detail["verification"]):
        detail["availability"] = "PARTIAL"  # Only allowlisted structural observations, never full diagnostics.
    return detail


def validate_safe_failure_detail(detail: Any, owner: Mapping[str, Any]) -> None:
    fields = {"format", "version", "run_id", "subject_sha", "private_failure_digest", "phase",
        "availability", "verification", "verification_truncated", "cause", "missing_acceptance_ids",
        "final_response_shape", "raw_output_withheld"}
    if (not isinstance(detail, dict) or set(detail) != fields or detail["format"] != "AIOS_SAFE_FAILURE_DETAIL"
            or type(detail["version"]) is not int or detail["version"] != 1
            or any(detail[k] != owner[k] for k in ("run_id", "subject_sha", "private_failure_digest", "phase"))
            or detail["availability"] not in {"UNAVAILABLE", "PARTIAL"}
            or type(detail["verification_truncated"]) is not bool or detail["raw_output_withheld"] is not True
            or detail["cause"] not in _DETAIL_CAUSES or detail["final_response_shape"] not in _RESPONSE_SHAPES
            or not isinstance(detail["verification"], list) or len(detail["verification"]) > 32
            or not isinstance(detail["missing_acceptance_ids"], list) or len(detail["missing_acceptance_ids"]) > 32
            or any(not isinstance(x, str) or re.fullmatch(r"AC[A-Za-z0-9_-]{1,64}", x) is None
                   for x in detail["missing_acceptance_ids"])):
        raise ValueError("invalid safe FAILURE detail")
    if (bool(detail["missing_acceptance_ids"]) != (detail["cause"] == "MISSING_ACCEPTANCE_CLAIMS")
            or detail["cause"] in {"MISSING_ACCEPTANCE_CLAIMS", "RESULT_PACKAGE_INVALID", "UNRESOLVED_ITEMS", "EXECUTOR_EVIDENCE_INVALID"}
                and detail["phase"] != "COMPLETION_GATE"
            or detail["cause"].startswith("NATIVE_") and detail["phase"] != "EXECUTION"
            or detail["final_response_shape"] != "UNKNOWN" and detail["phase"] != "EXECUTION"
            or detail["verification"] and detail["phase"] != "VERIFICATION"):
        raise ValueError("safe FAILURE detail phase mismatch")
    previous = -1
    for item in detail["verification"]:
        fields = {"index", "command", "exit_code", "source_kind", "source_digest", "availability",
            "complete", "unstable", "failure_count", "failed_nodes", "nodes_truncated"}
        if (not isinstance(item, dict) or set(item) != fields or type(item["index"]) is not int
                or not previous < item["index"] < 32 or item["index"] >= len(owner["verification"])
                or any(item[k] != owner["verification"][item["index"]][k] for k in ("command", "exit_code"))
                or type(item["exit_code"]) is not int or item["exit_code"] == 0
                or item["source_kind"] not in {"PROTECTED_OBSERVATION", "PROTECTED_SUMMARY"}
                or not _is_digest(item["source_digest"]) or item["availability"] not in {"AVAILABLE", "PARTIAL", "UNAVAILABLE"}
                or any(item[k] is not None and type(item[k]) is not bool for k in ("complete", "unstable"))
                or (item["complete"] is True and item["unstable"] is True)
                or item["failure_count"] is not None and (type(item["failure_count"]) is not int or not 0 <= item["failure_count"] <= 4096)
                or not isinstance(item["failed_nodes"], list) or len(item["failed_nodes"]) > 32
                or type(item["nodes_truncated"]) is not bool):
            raise ValueError("invalid safe FAILURE verification detail")
        previous = item["index"]
        _public_command(item["command"])
        for node in item["failed_nodes"]:
            if (not isinstance(node, dict) or set(node) != {"identity", "identity_digest", "parameters_withheld", "phase", "error_types"}
                    or node["identity"] is not None and _safe_test_identity(node["identity"]) != (node["identity"], False)
                    or not _is_digest(node["identity_digest"]) or type(node["parameters_withheld"]) is not bool
                    or node["phase"] not in {"collect", "setup", "call", "teardown", "unknown"}
                    or not isinstance(node["error_types"], list) or not 1 <= len(node["error_types"]) <= len(_DETAIL_ERRORS)
                    or any(not isinstance(t, str) or t not in _DETAIL_ERRORS for t in node["error_types"])):
                raise ValueError("invalid safe failed node")


def _capture_fact(value: Any, *, partial: bool) -> dict[str, Any]:
    """Describe capture without copying any native string into public material."""
    availability, truncated = "UNAVAILABLE", None
    if isinstance(value, str):  # Historical native captures were plain strings.
        availability = "CAPTURED" if value else "EMPTY"
    elif isinstance(value, dict):
        availability = {"captured": "CAPTURED", "empty": "EMPTY", "unavailable": "UNAVAILABLE"}.get(
            value.get("availability"), "UNAVAILABLE")
        if availability == "CAPTURED" and isinstance(value.get("text"), str):
            truncated = value.get("truncated") if type(value.get("truncated")) is bool else None
        elif availability == "CAPTURED":
            availability = "UNAVAILABLE"
    return dict(availability=availability, truncated=truncated,
                partial=(True if partial else False if truncated is not None else None) if availability == "CAPTURED" else None,
                withheld=availability == "CAPTURED")


def _validate_native_failure_exit(data: Mapping[str, Any], *, historical_read: bool = False) -> None:
    """Require an EXECUTION owner, except for the minimal historical read format."""
    error = data.get("error")
    if isinstance(error, Mapping) and "exit_code" in error:
        # This closed historical payload is only a lifecycle fact. Immutable
        # source readers authenticate it independently; neither its sentinel nor
        # its shape grants a private role, a projection or admission authority.
        historical = (historical_read and set(data) == {"kind", "run_id", "error"}
            and data.get("kind") == "FAILURE" and isinstance(data.get("run_id"), str)
            and re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", data["run_id"]) is not None
            and set(error) == {"type", "message", "exit_code"}
            and error.get("type") in {"CodexExecutionError", "CodexOutputError",
                "AntigravityExecutionError", "AntigravityOutputError", "KeyboardInterrupt"}
            and error.get("message") == _PROTECTED_DIAGNOSTICS)
        if data.get("phase") != "EXECUTION" and not historical:
            raise ValueError("native FAILURE exit code outside EXECUTION")
        if error["exit_code"] is not None and type(error["exit_code"]) is not int:
            raise ValueError("invalid native FAILURE exit code")


def _safe_failure_diagnostic(data: Mapping[str, Any]) -> dict[str, Any]:
    _validate_native_failure_exit(data)
    error = data["error"]
    reason = error.get("native_reason")
    if reason is not None and reason not in _NATIVE_REASONS:
        raise ValueError("invalid native FAILURE reason")
    code = error.get("exit_code")
    native = error.get("native_diagnostics", {})
    if not isinstance(native, dict):
        native = {}
    category, source = "RUNTIME_FAILURE", "UNAVAILABLE"
    if data["phase"] == "EXECUTION":
        category, source = "NATIVE_EXIT_UNKNOWN", "RUNTIME_TYPE"
        message = error.get("message", "")
        if not reason and isinstance(message, str) and re.match(
                r"^Codex CLI exceeded [0-9]+-second process watchdog\b", message):
            reason = "TIMEOUT"
        if error["type"] == "KeyboardInterrupt":
            reason = "INTERRUPTED"
        if error["type"] in {"CodexOutputError", "AntigravityOutputError"}:
            reason = "OUTPUT_INVALID"
        if reason in {"TIMEOUT", "INTERRUPTED", "OUTPUT_INVALID", "INVOCATION_FAILED"}:
            category = "NATIVE_" + reason
        else:
            if type(code) is int:
                category = "NATIVE_EXIT"
            # Only fixed categories cross this boundary, including on secret-bearing lines.
            patterns = (
                ("NATIVE_QUOTA", r"(?:insufficient_quota|quota (?:exceeded|exhausted)|usage limit (?:exceeded|reached))"),
                ("NATIVE_RATE_LIMIT", r"(?:rate.?limit(?:ed| exceeded)?|too many requests|http 429)"),
                ("NATIVE_AUTHENTICATION", r"(?:authentication failed|invalid (?:api key|authentication)|unauthorized|http 401)"),
                ("NATIVE_TRANSPORT", r"(?:connection (?:refused|reset|timed out)|name resolution failed|tls handshake failed)"),
            )
            for stream in ("stderr", "stdout"):
                capture = native.get(stream)
                text = capture if isinstance(capture, str) else capture.get("text", "") if isinstance(capture, dict) else ""
                if not isinstance(text, str):
                    continue
                for label, pattern in patterns:
                    if re.search(r"(?im)^(?:error:\s*)?" + pattern + r"\b", text[:4096]):
                        category, source = label, "NATIVE_" + stream.upper()
                        break
                if source.startswith("NATIVE_"):
                    break
    elif data["phase"] == "VERIFICATION":
        failed = [item for item in error.get("verification", []) if item["exit_code"] != 0]
        category, source = "VERIFICATION_COMMAND_FAILED", "VERIFICATION_EXIT" if failed else "UNAVAILABLE"
        if failed:
            command = failed[0]["command"]
            if re.match(r"^python(?:3)? -m pytest(?: |$)", command) or command == "python scripts/aios_parallel_full_suite.py":
                category = {1: "VERIFICATION_TEST_FAILURE", 2: "VERIFICATION_INTERRUPTED",
                            3: "VERIFICATION_INTERNAL_ERROR", 4: "VERIFICATION_USAGE_ERROR",
                            5: "VERIFICATION_NO_TESTS"}.get(failed[0]["exit_code"], category)
    elif data["phase"] == "COMPLETION_GATE":
        category, source = "COMPLETION_GATE", "RUNTIME_TYPE"
        if data["candidate"]["dirty"]:
            category, source = "COMPLETION_DIRTY", "CANDIDATE"
        elif data["candidate"]["outside_task_scope"]:
            category, source = "COMPLETION_SCOPE", "CANDIDATE"
    return dict(category=category, cause_source=source,
                native_exit=dict(availability=("AVAILABLE" if type(code) is int else
                    "UNKNOWN" if "exit_code" in error else "UNAVAILABLE"), value=code),
                capture={stream: _capture_fact(native.get(stream), partial=reason in {"TIMEOUT", "INTERRUPTED"})
                         for stream in ("stdout", "stderr")},
                message_withheld=True, output_withheld=True)


def _validate_failure_diagnostic(data: Mapping[str, Any]) -> None:
    _validate_native_failure_exit(data)
    error = data["error"]
    diagnostic = error.get("diagnostic")
    if (error["type"] not in _PUBLIC_ERROR_TYPES or not isinstance(diagnostic, dict)
            or set(diagnostic) != {"category", "cause_source", "native_exit", "capture", "message_withheld", "output_withheld"}
            or diagnostic["category"] not in _FAILURE_CATEGORIES
            or diagnostic["cause_source"] not in {"RUNTIME_TYPE", "NATIVE_STDERR", "NATIVE_STDOUT", "VERIFICATION_EXIT", "CANDIDATE", "UNAVAILABLE"}
            or diagnostic["message_withheld"] is not True or diagnostic["output_withheld"] is not True):
        raise ValueError("invalid typed FAILURE diagnostic")
    exit_fact = diagnostic["native_exit"]
    expected_exit = dict(availability=("AVAILABLE" if type(error.get("exit_code")) is int else
                         "UNKNOWN" if "exit_code" in error else "UNAVAILABLE"), value=error.get("exit_code"))
    if (not isinstance(exit_fact, dict) or exit_fact != expected_exit
            or (exit_fact.get("value") is not None and type(exit_fact.get("value")) is not int)):
        raise ValueError("invalid typed FAILURE native exit")
    capture = diagnostic["capture"]
    if not isinstance(capture, dict) or set(capture) != {"stdout", "stderr"}:
        raise ValueError("invalid typed FAILURE capture")
    for fact in capture.values():
        if (not isinstance(fact, dict) or set(fact) != {"availability", "truncated", "partial", "withheld"}
                or fact["availability"] not in {"CAPTURED", "EMPTY", "UNAVAILABLE"}
                or (fact["truncated"] is not None and type(fact["truncated"]) is not bool)
                or (fact["partial"] is not None and type(fact["partial"]) is not bool)
                or fact["withheld"] is not (fact["availability"] == "CAPTURED")
                or (fact["availability"] != "CAPTURED" and (fact["truncated"] is not None or fact["partial"] is not None))):
            raise ValueError("invalid typed FAILURE stream status")
    category, phase, source = diagnostic["category"], data["phase"], diagnostic["cause_source"]
    if ((phase == "EXECUTION" and not category.startswith("NATIVE_"))
            or (phase == "VERIFICATION" and not category.startswith("VERIFICATION_"))
            or (phase == "COMPLETION_GATE" and category not in {"COMPLETION_DIRTY", "COMPLETION_SCOPE", "COMPLETION_GATE"})
            or (category == "COMPLETION_DIRTY" and not data["candidate"]["dirty"])
            or (category == "COMPLETION_SCOPE" and not data["candidate"]["outside_task_scope"])
            or (category == "NATIVE_EXIT" and exit_fact["availability"] != "AVAILABLE")
            or (category == "NATIVE_EXIT_UNKNOWN" and exit_fact["availability"] == "AVAILABLE")
            or (category in {"NATIVE_AUTHENTICATION", "NATIVE_RATE_LIMIT", "NATIVE_QUOTA", "NATIVE_TRANSPORT"}
                and source not in {"NATIVE_STDOUT", "NATIVE_STDERR"})
            or (source in {"NATIVE_STDOUT", "NATIVE_STDERR"} and category not in {
                "NATIVE_AUTHENTICATION", "NATIVE_RATE_LIMIT", "NATIVE_QUOTA", "NATIVE_TRANSPORT"})
            or (source in {"NATIVE_STDOUT", "NATIVE_STDERR"} and capture[source[7:].lower()]["availability"] != "CAPTURED")):
        raise ValueError("typed FAILURE cause binding mismatch")
    if phase == "VERIFICATION":
        expected = _safe_failure_diagnostic(data)
        if any(diagnostic[k] != expected[k] for k in ("category", "cause_source")):
            raise ValueError("typed verification cause binding mismatch")
    if category in {"NATIVE_TIMEOUT", "NATIVE_INTERRUPTED"} and any(
        fact["availability"] == "CAPTURED" and fact["partial"] is not True for fact in capture.values()
    ):
        raise ValueError("typed native interruption capture mismatch")


def failure_context(data: Mapping[str, Any], *, artifact_sha: str | None = None,
                    candidate_available: bool | None = None) -> dict[str, Any] | None:
    """Useful portable facts from an already source-bound terminal, never proof.

    Callers bind the RUN/TASK and immutable artifact before invoking this pure
    projection. No file or raw locator is opened here.
    """
    if candidate_available is not None and type(candidate_available) is not bool:
        raise FailureDiagnosticError("FAILURE_SOURCE_IDENTITY_INVALID")
    if artifact_sha is not None and (not isinstance(artifact_sha, str) or re.fullmatch(r"[0-9a-f]{40}", artifact_sha) is None):
        raise FailureDiagnosticError("FAILURE_SOURCE_IDENTITY_INVALID")
    if "public_projection" in data:
        validate_public_failure(data)
        public = dict(data)
    elif not {"error", "phase"} <= data.keys():
        # Older canonical predecessors need not contain a diagnostic source.
        # Validate present roles, but do not invent a phase, cause or projection
        # identity merely to enrich the existing FAILURE lifecycle fact.
        from .unified_state import _validated_private_public_view
        _validated_private_public_view(data, "METADATA")
        return None
    else:
        public = _project_failure(data)
    error = public["error"]
    diagnostic = error.get("diagnostic")
    if diagnostic is None:
        # V1/legacy public metadata never regains the withheld native cause.
        diagnostic = _safe_failure_diagnostic(public)
    outcomes = []
    for item in error["verification"][:32]:
        outcome = {key: item[key] for key in ("command", "exit_code")}
        reference = item.get("verification")
        if reference:
            observation = reference["candidate"]
            available_details = sum("fingerprint" in report for report in observation["failed_reports"])
            detail_status = ("UNAVAILABLE" if not available_details else
                             "PARTIAL" if available_details < len(observation["failed_reports"]) else "WITHHELD")
            outcome.update(evidence_id=reference["evidence_id"], source_run_id=reference["run_id"],
                           raw_digest=reference["raw_digest"], observation_digest=reference["candidate_digest"],
                           complete=observation["complete"], unstable=observation["unstable"],
                           failure_count=observation["failure_count"],
                           detail_status=detail_status)
        else:
            outcome.update(observation_availability="UNAVAILABLE", detail_status="UNAVAILABLE")
        outcomes.append(outcome)
    context = dict(format="AIOS_FAILURE_CONTEXT", version=1, kind="FAILURE_CONTEXT",
                run_id=public["run_id"], task=public["task"], executor=public["executor"],
                base_sha=public["base_sha"], subject_sha=public["failed_head_sha"], phase=public["phase"],
                candidate=public["candidate"], candidate_availability=("AVAILABLE" if candidate_available is True else
                    "UNAVAILABLE" if candidate_available is False else "UNKNOWN"),
                diagnostic=diagnostic, verification=outcomes,
                verification_truncated=len(error["verification"]) > len(outcomes),
                evidence_identities=error.get("evidence_identities", [])[:32],
                evidence_identities_truncated=len(error.get("evidence_identities", [])) > 32,
                source=dict(artifact_sha=artifact_sha, failure_digest=_failure_digest(data),
                            private_failure_digest=public["public_projection"]["private_failure_digest"]),
                proof="NON_PASS_DIAGNOSTIC")
    context["safe_detail"] = error.get("safe_detail") or safe_failure_detail(public)
    return context


def validate_failure_context(value: Any) -> None:
    """Closed portable schema; a context cannot carry raw output or proof."""
    from .verification_contract import MAX_CANONICAL_FAILURES
    fields = {"format", "version", "kind", "run_id", "task", "executor", "base_sha", "subject_sha", "phase",
              "candidate", "candidate_availability", "diagnostic", "verification", "verification_truncated", "evidence_identities",
              "evidence_identities_truncated", "source", "proof"}
    if (not isinstance(value, dict) or set(value) - {"safe_detail", "detail_blocker"} != fields or value["format"] != "AIOS_FAILURE_CONTEXT"
            or type(value["version"]) is not int or value["version"] != 1 or value["kind"] != "FAILURE_CONTEXT"
            or value["proof"] != "NON_PASS_DIAGNOSTIC"
            or value["candidate_availability"] not in {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}
            or any(type(value[k]) is not bool for k in ("verification_truncated", "evidence_identities_truncated"))
            or not isinstance(value["verification"], list) or len(value["verification"]) > 32
            or not isinstance(value["evidence_identities"], list) or len(value["evidence_identities"]) > 32):
        raise ValueError("invalid portable FAILURE context")
    source = value["source"]
    if (not isinstance(source, dict) or set(source) != {"artifact_sha", "failure_digest", "private_failure_digest"}
            or any(not _is_digest(source[k]) for k in ("failure_digest", "private_failure_digest"))
            or (source["artifact_sha"] is not None and (not isinstance(source["artifact_sha"], str)
                or re.fullmatch(r"[0-9a-f]{40}", source["artifact_sha"]) is None))):
        raise ValueError("invalid portable FAILURE source")
    outcomes = []
    for item in value["verification"]:
        common = {"command", "exit_code", "detail_status"}
        unavailable = common | {"observation_availability"}
        observed = common | {"evidence_id", "source_run_id", "raw_digest", "observation_digest", "complete", "unstable", "failure_count"}
        if (not isinstance(item, dict) or set(item) not in (unavailable, observed)
                or item["detail_status"] not in {"UNAVAILABLE", "PARTIAL", "WITHHELD"}):
            raise ValueError("invalid portable FAILURE observation")
        if set(item) == unavailable:
            if item["observation_availability"] != "UNAVAILABLE" or item["detail_status"] != "UNAVAILABLE":
                raise ValueError("invalid unavailable FAILURE observation")
        elif (not isinstance(item["evidence_id"], str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", item["evidence_id"]) is None
                or not isinstance(item["source_run_id"], str) or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", item["source_run_id"]) is None
                or any(not _is_digest(item[k]) for k in ("raw_digest", "observation_digest"))
                or any(type(item[k]) is not bool for k in ("complete", "unstable"))
                or (item["complete"] and item["unstable"])
                or type(item["failure_count"]) is not int or not 0 <= item["failure_count"] <= MAX_CANONICAL_FAILURES):
            raise ValueError("invalid portable FAILURE observation identity")
        outcomes.append(dict(command=item["command"], exit_code=item["exit_code"],
                             summary="Runtime verification exit code " + str(item["exit_code"])))
    error = dict(type="RuntimeFailure", message=_PROTECTED_DIAGNOSTICS, verification=outcomes,
                 diagnostic=value["diagnostic"], evidence_identities=value["evidence_identities"])
    native_exit = value["diagnostic"].get("native_exit") if isinstance(value["diagnostic"], dict) else None
    if isinstance(native_exit, dict) and native_exit.get("availability") != "UNAVAILABLE":
        error["exit_code"] = native_exit.get("value")
    projected = {k: value[k] for k in ("run_id", "task", "executor", "base_sha", "phase", "candidate")}
    projected.update(kind="FAILURE", failed_head_sha=value["subject_sha"], error=error)
    projected["public_projection"] = dict(format=PUBLIC_FAILURE_FORMAT, proof="PRIVATE_NON_PASS_FAILURE",
        private_failure_digest=source["private_failure_digest"], projection_digest=_failure_digest(projected))
    validate_public_failure(projected)
    if "safe_detail" in value:
        validate_safe_failure_detail(value["safe_detail"], dict(run_id=value["run_id"],
            subject_sha=value["subject_sha"], phase=value["phase"],
            private_failure_digest=source["private_failure_digest"], verification=value["verification"]))
    if "detail_blocker" in value:
        blocker = value["detail_blocker"]
        if (not isinstance(blocker, dict) or set(blocker) != {"code", "action"}
                or blocker["code"] not in {"PRIVATE_DETAIL_UNAVAILABLE", "PRIVATE_DETAIL_ACCESS_DENIED", "PRIVATE_DETAIL_INTEGRITY_INVALID"}
                or blocker["action"] != "READ_EXACT_RUNTIME_PROJECTION"):
            raise ValueError("invalid FAILURE detail blocker")
    _public_bytes(value, max_bytes=256 * 1024)


def _public_document(
    content: bytes, *, max_bytes: int = PUBLIC_ARTIFACT_MAX_BYTES,
) -> dict[str, Any]:
    """Decode prospective writer input; historical readers retain their bounds."""
    if len(content) > max_bytes:
        raise ReviewTransportError("public artifact exceeds byte bound")
    try:
        data = _performance_json_mapping(content, "public artifact")

        def bounded(value: Any, depth: int = 0) -> None:
            if depth > 64:
                raise ReviewTransportError("public artifact exceeds nesting bound")
            if isinstance(value, dict):
                for item in value.values():
                    bounded(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    bounded(item, depth + 1)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ReviewTransportError("public artifact contains non-finite JSON")

        bounded(data)
        return dict(data)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ReviewTransportError("invalid public artifact JSON") from exc


def _public_bytes(data: Any, *, max_bytes: int = PUBLIC_ARTIFACT_MAX_BYTES) -> bytes:
    try:
        content = json.dumps(data, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
        if len(content) > max_bytes:
            raise ReviewTransportError("public artifact exceeds byte bound")
        return content
    except (ValueError, TypeError, RecursionError) as exc:
        raise ReviewTransportError("invalid public artifact projection") from exc


def _redact_evidence(item: dict[str, Any]) -> None:
    identity = item["evidence_id"]
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identity):
        raise ReviewTransportError("unsafe public evidence identity")
    item["raw"] = {"path": "protected-runtime-raw/" + identity}
    item["result"]["summary"] = "Runtime verification exit code " + str(item["result"]["exit_code"])


def public_result(content: bytes) -> bytes:
    """Keep canonical EVIDENCE identities/digests while hiding raw and diagnostics."""
    data = _public_document(content)
    try:
        for item in data.get("evidence", []):
            _redact_evidence(item)
        return _public_bytes(data)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ReviewTransportError("invalid public RESULT projection") from exc


def public_run(content: bytes) -> bytes:
    """Hide the local workspace in PRIMARY, REPAIR and REMEDIATION RUNs."""
    data = _public_document(content)
    try:
        run = data.get("execution", {}).get("run") if data.get("kind") == "REMEDIATION" else data
        if isinstance(run, dict) and "workspace" in run:
            run["workspace"] = "protected-runtime-workspace"
        return _public_bytes(data)
    except (TypeError, AttributeError) as exc:
        raise ReviewTransportError("invalid public RUN projection") from exc


def _public_metadata_value(value: Any, template: Any = None) -> Any:
    """Exact read projection, also used inside ordinary bounded corrections."""
    if isinstance(value, list):
        return [_public_metadata_value(item, template[index] if isinstance(template, list) and index < len(template) else None)
                for index, item in enumerate(value)]
    if not isinstance(value, dict):
        return value
    if value.get("kind") == "FAILURE" and _needs_failure_projection(value):
        marker = template.get("public_projection", {}) if isinstance(template, Mapping) else {}
        return _project_failure(value, projection_format=marker.get("format", PUBLIC_FAILURE_FORMAT),
            include_detail=("safe_detail" in template.get("error", {}) if marker else True))
    data = {key: _public_metadata_value(item, template.get(key) if isinstance(template, Mapping) else None)
            for key, item in value.items()}
    if "run_id" in data and "workspace" in data and "task" in data:
        data["workspace"] = "protected-runtime-workspace"
    if "evidence_id" in data and "raw" in data:
        _redact_evidence(data)
    if data.get("kind") == "FAILURE" and isinstance(data.get("error"), dict):
        _validate_native_failure_exit(data)
        error = data["error"]
        error["message"] = "Protected Runtime diagnostics remain local."
        error.pop("native_diagnostics", None)
        error.pop("executor_diagnostics", None)
        error.pop("native_reason", None)
        for item in error.get("verification", []):
            item["summary"] = "Runtime verification exit code " + str(item["exit_code"])
    return data


def public_metadata(content: bytes) -> bytes:
    """Redact correction metadata without widening its ordinary transport cap."""
    try:
        return _public_bytes(_public_metadata_value(_public_document(content)))
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise ReviewTransportError("invalid public correction projection") from exc


def _has_failure_observations(data: Mapping[str, Any]) -> bool:
    if "public_projection" in data:
        return True
    # Partial historical metadata is not an admitted terminal. Preserve its
    # established diagnostic redaction; exact terminal readers bind full RUNs.
    if not {"task", "executor", "base_sha", "candidate", "phase"} <= data.keys():
        return False
    def observation(value: Any) -> bool:
        if isinstance(value, list):
            return any(observation(item) for item in value)
        return isinstance(value, Mapping) and (
            (value.get("policy") == "minimum-sufficient-v2" and isinstance(value.get("candidate"), Mapping))
            or any(observation(item) for item in value.values()))

    return observation(data)


def _needs_failure_projection(data: Mapping[str, Any]) -> bool:
    return _has_failure_observations(data) or (
        {"task", "executor", "base_sha", "failed_head_sha", "candidate", "phase", "error"} <= data.keys()
        and isinstance(data["error"], Mapping))


def _failure_digest(value: Any) -> str:
    from .verification_contract import verification_digest
    return verification_digest(value)


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _public_command(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise ValueError("unsafe public verification command")
    # Quotes are admitted only around a pytest selector argument, never shell
    # syntax, executable identity, escapes or interpolation. Preserve exact bytes.
    quoted = re.compile(r'''(?<!\S)(-k|-m) (?:"([A-Za-z0-9_ .()*?\[\]+-]+)"|'([A-Za-z0-9_ .()*?\[\]+-]+)')(?= |$)''')
    unquoted = quoted.sub(r"\1 selector", value)
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValueError("unsafe public verification command") from exc
    if (re.fullmatch(r"[A-Za-z0-9_ ./,:=+*?@%\[\]()-]+", unquoted) is None
            or (("'" in value or '"' in value) and tokens[:3] not in (["python", "-m", "pytest"], ["python3", "-m", "pytest"]))
            or re.search(r"(?i)(?:[a-z]:[/\\]|://|(?:^|[ =])/|(?:^|\s)(?:--?[A-Za-z0-9_-]*(?:password|secret|token|credential|api[-_]key)[A-Za-z0-9_-]*(?:=|\s|$)|[A-Za-z0-9_-]*(?:password|secret|token|credential|api[-_]key)[A-Za-z0-9_-]*=))", unquoted)):
        raise ValueError("unsafe public verification command")
    return value


def _private_identity(value: Any) -> dict[str, str]:
    return {"private_identity_digest": _failure_digest(value)}


def _project_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    from .verification_contract import (
        MAX_CANONICAL_REPORTS, MAX_CANONICAL_FAILURES, MAX_CANONICAL_NODEID_CHARS,
        MAX_FAILURE_DETAIL_CHARS,
    )
    reports = value["reports"]
    if len(reports) > MAX_CANONICAL_REPORTS:
        raise ValueError("FAILURE report population exceeds bound")
    counts = dict(PASS=0, FAIL=0, SKIP=0)
    failed = []
    incomplete_detail = False
    nodes: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for report in reports:
        node, phase = report.get("nodeid"), report.get("phase")
        if (not isinstance(node, str) or not node or len(node) > MAX_CANONICAL_NODEID_CHARS
                or not isinstance(phase, str) or phase not in {"collect", "setup", "call", "teardown"}
                or (node, phase) in seen):
            raise ValueError("invalid FAILURE phase identity")
        seen.add((node, phase))
        nodes.add(node)
        counts[report["outcome"]] += 1
        if report["outcome"] == "FAIL":
            detail = report.get("detail")
            fingerprint = report.get("fingerprint")
            if ((detail is not None and (not isinstance(detail, str) or not detail or len(detail) > MAX_FAILURE_DETAIL_CHARS))
                    or (fingerprint is not None and not _is_digest(fingerprint))
                    or (detail is not None and fingerprint is not None and fingerprint != _failure_digest(detail))):
                raise ValueError("invalid FAILURE detail digest")
            projected = dict(nodeid_digest=_failure_digest(node), phase=phase)
            if detail is not None and fingerprint is not None:
                projected["fingerprint"] = fingerprint
            elif not value["complete"]:
                incomplete_detail = True
                projected["detail_status"] = "UNAVAILABLE" if detail is None else "WITHHELD"
            else:
                raise ValueError("complete FAILURE is missing detail identity")
            failed.append(projected)
    if counts["FAIL"] > MAX_CANONICAL_FAILURES or counts["FAIL"] != value["failure_count"]:
        raise ValueError("invalid FAILURE failure population")
    if (value["exit_code"] == 0 and counts["FAIL"]) or (value["complete"] and value["exit_code"] == 1 and not counts["FAIL"]):
        raise ValueError("contradictory FAILURE exit and reports")
    return dict(public_observation=INCOMPLETE_OBSERVATION_FORMAT if incomplete_detail else PUBLIC_OBSERVATION_FORMAT,
        observation_digest=_failure_digest(value), subject_sha=value["subject_sha"],
        command=_public_command(value["command"]), profile=_private_identity(value["profile"]),
        toolchain=_private_identity(value["toolchain"]), complete=value["complete"],
        unstable=value["unstable"], exit_code=value["exit_code"], failure_count=value["failure_count"],
        report_count=len(reports), node_count=len(nodes), outcome_counts=counts,
        reports_digest=_failure_digest(reports),
        failed_reports=sorted(failed, key=lambda item: (item["nodeid_digest"], item["phase"])))


def _project_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    public = {key: binding[key] for key in (
        "subject_sha", "base_sha", "tree_sha", "command", "envelope_digest",
        "changed_files_digest", "failure_set_digest", "correction_base_sha",
        "correction_changed_files_digest") if key in binding}
    public["command"] = _public_command(binding["command"])
    public.update(profile=_private_identity(binding["profile"]),
                  toolchain=_private_identity(binding["toolchain"]))
    return public


def _project_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    projected = dict(policy=value["policy"], evidence_id=value["evidence_id"], run_id=value["run_id"],
        private_reference_digest=_failure_digest(value), binding=_project_binding(value["binding"]),
        binding_digest=_failure_digest(value["binding"]), raw_digest=value["raw_digest"],
        candidate=_project_observation(value["candidate"]), candidate_digest=value["candidate_digest"])
    if "base" in value:
        projected.update(base=_project_observation(value["base"]), base_digest=value["base_digest"],
                         base_raw_digest=value["base_raw_digest"])
    if "reuse" in value:
        projected["reuse_binding"] = _project_binding(value["reuse"]["binding"])
    return projected


def _project_failure(data: Mapping[str, Any], *, projection_format: str = PUBLIC_FAILURE_FORMAT,
                     include_detail: bool = True) -> dict[str, Any]:
    """Produce FAILURE metadata; omitted proof remains bound, never recreated."""
    if "public_projection" in data:
        validate_public_failure(data)
        return dict(data)
    from .unified_state import _validated_private_public_view
    # Validate the original role, owner, conditions and observation digests first.
    _validated_private_public_view(data, "METADATA")
    projected = {key: data[key] for key in (
        "kind", "run_id", "task", "executor", "base_sha", "failed_head_sha",
        "candidate", "phase", "continuation_of") if key in data}
    error = data["error"]
    diagnostic = _safe_failure_diagnostic(data)
    outcomes = []
    for item in error.get("verification", []):
        outcome = dict(command=_public_command(item["command"]), exit_code=item["exit_code"],
                       summary="Runtime verification exit code " + str(item["exit_code"]))
        if "verification" in item:
            outcome["verification"] = _project_reference(item["verification"])
        outcomes.append(outcome)
    projected["error"] = dict(type=error["type"], message="Protected Runtime diagnostics remain local.",
                              verification=outcomes)
    if projection_format == PUBLIC_FAILURE_FORMAT:
        projected["error"]["type"] = error["type"] if error["type"] in _PUBLIC_ERROR_TYPES else "RuntimeFailure"
        if "exit_code" in error:
            projected["error"]["exit_code"] = error["exit_code"]
        projected["error"]["diagnostic"] = diagnostic
        if include_detail:
            projected["error"]["safe_detail"] = safe_failure_detail(data)
    # Full EVIDENCE in any validated package role retains identity/digest only.
    identities: dict[str, dict[str, str]] = {}

    def collect(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            if "evidence_id" in value and "raw" in value:
                item = dict(evidence_id=value["evidence_id"], run_id=value["run_id"],
                            subject_sha=value["subject_sha"], record_digest=_failure_digest(value))
                prior = identities.setdefault(item["evidence_id"], item)
                if prior != item:
                    raise ValueError("conflicting private FAILURE evidence identities")
            for item in value.values():
                collect(item)

    collect(data)
    if identities:
        projected["error"]["evidence_identities"] = [identities[key] for key in sorted(identities)]
    projected["public_projection"] = dict(format=projection_format,
        private_failure_digest=_failure_digest(data), projection_digest=_failure_digest(projected),
        proof="PRIVATE_NON_PASS_FAILURE")
    validate_public_failure(projected)
    return projected


def _validate_legacy_failure_public(data: Mapping[str, Any], *, historical_read: bool = False) -> None:
    """Keep lawful small redaction, rejecting undeclared public diagnostics."""
    allowed = {"kind", "run_id", "task", "executor", "base_sha", "failed_head_sha",
               "candidate", "phase", "continuation_of", "error"}
    if set(data) - allowed:
        raise ValueError("undeclared public FAILURE material")
    error = data.get("error")
    if error is not None:
        if (not isinstance(error, dict) or set(error) - {"type", "message", "verification", "evidence", "exit_code"}
                or not isinstance(error.get("type"), str)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", error["type"]) is None
                or error.get("message") != "Protected Runtime diagnostics remain local."):
            raise ValueError("undeclared public FAILURE diagnostics")
        _validate_native_failure_exit(data, historical_read=historical_read)
        outcomes = error.get("verification", [])
        if not isinstance(outcomes, list):
            raise ValueError("invalid public FAILURE outcomes")
        for item in outcomes:
            if (not isinstance(item, dict) or set(item) != {"command", "exit_code", "summary"}
                    or type(item["exit_code"]) is not int
                    or item["summary"] != "Runtime verification exit code " + str(item["exit_code"])):
                raise ValueError("unprojected public FAILURE verification material")
            _public_command(item["command"])
    candidate = data.get("candidate")
    if isinstance(candidate, dict):
        if set(candidate) - {"transportable", "repairable", "dirty", "descends_from_base",
                             "changed_files", "outside_task_scope"}:
            raise ValueError("undeclared public FAILURE candidate material")
        for key in ("changed_files", "outside_task_scope"):
            paths = candidate.get(key, [])
            if (not isinstance(paths, list) or any(not isinstance(path, str)
                    or re.fullmatch(r"[A-Za-z0-9_./@ -]{1,4096}", path) is None
                    or path.startswith("/") or ".." in path.split("/") for path in paths)):
                raise ValueError("unsafe public FAILURE repository paths")

    def diagnostics(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                diagnostics(item)
        elif isinstance(value, dict):
            if set(value) & {"stdout", "stderr", "captured_output", "traceback", "longrepr", "detail",
                             "raw_path", "base_raw_path", "python_executable", "workspace"}:
                raise ValueError("private material in public FAILURE")
            if "evidence_id" in value and "source" in value:
                _public_command(value["source"]["command"])
                identity = value["evidence_id"]
                if (not isinstance(identity, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identity) is None
                        or value.get("raw") != {"path": "protected-runtime-raw/" + identity}
                        or not isinstance(value.get("result"), dict)
                        or type(value["result"].get("exit_code")) is not int
                        or value["result"].get("summary") != "Runtime verification exit code " + str(value["result"]["exit_code"])):
                    raise ValueError("unprojected public FAILURE evidence diagnostics")
            for item in value.values():
                diagnostics(item)

    diagnostics(data)


def public_failure(content: bytes, *, private_original: bool = False) -> bytes:
    """FAILURE-only finite budget; ordinary transport retains its existing cap."""
    data = _failure_input(content)
    if data.get("kind") != "FAILURE":
        raise FailureDiagnosticError("FAILURE_KIND_INVALID")
    try:
        error = data.get("error")
        if private_original and "public_projection" not in data:
            failure_document(content, private_original=True)
        elif (isinstance(error, dict)
                and error.get("message") == "Protected Runtime diagnostics remain local."
                and "public_projection" not in data):
            # A purported public original must already be lawful. Re-redacting
            # it would conceal contaminated or forged public diagnostics.
            failure_document(content)
        outcomes = error.get("verification", []) if isinstance(error, dict) else []
        if not _has_failure_observations(data) and isinstance(outcomes, list) and any(
            isinstance(item, dict) and isinstance(item.get("verification"), dict)
            and "candidate" in item["verification"] for item in outcomes
        ):
            raise ValueError("public FAILURE observation has no admitted terminal binding")
        projected = (_project_failure(data) if _needs_failure_projection(data)
                     else _public_metadata_value(data))
        # A diagnostic-only record must still fit the ordinary legacy format.
        if not _has_failure_observations(data) and len(content) > PUBLIC_ARTIFACT_MAX_BYTES:
            raise ValueError("large FAILURE requires a private observation projection")
        if "public_projection" not in projected:
            _validate_legacy_failure_public(projected)
        return _public_bytes(projected, max_bytes=PUBLIC_FAILURE_MAX_BYTES)
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise FailureDiagnosticError("FAILURE_PROJECTION_INVALID") from exc


def validate_public_failure(data: Mapping[str, Any]) -> None:
    """Validate an exact public non-PASS summary, without granting proof authority."""
    from .verification_contract import (
        MINIMUM_SUFFICIENT_V2, MAX_CANONICAL_REPORTS, MAX_CANONICAL_FAILURES,
        validate_evidence_binding,
    )
    required = {"kind", "run_id", "task", "executor", "base_sha", "failed_head_sha",
                "candidate", "phase", "error", "public_projection"}
    marker = data.get("public_projection")
    if (not required <= data.keys() or set(data) - required - {"continuation_of"}
            or data["kind"] != "FAILURE" or not isinstance(marker, dict)
            or set(marker) != {"format", "private_failure_digest", "projection_digest", "proof"}
            or marker["format"] not in {LEGACY_PUBLIC_FAILURE_FORMAT, PUBLIC_FAILURE_FORMAT}
            or marker["proof"] != "PRIVATE_NON_PASS_FAILURE"
            or not _is_digest(marker["private_failure_digest"])
            or marker["projection_digest"] != _failure_digest({k: v for k, v in data.items() if k != "public_projection"})):
        raise ValueError("invalid public FAILURE projection identity")
    _public_document(_public_bytes(data, max_bytes=PUBLIC_FAILURE_MAX_BYTES), max_bytes=PUBLIC_FAILURE_MAX_BYTES)

    def match(value: Any, pattern: str) -> bool:
        return isinstance(value, str) and re.fullmatch(pattern, value) is not None

    if (not match(data["run_id"], r"RUN-[A-Za-z0-9_-]+-\d{3,}")
            or any(not match(data[k], r"[0-9a-f]{40}") for k in ("base_sha", "failed_head_sha"))
            or not match(data["executor"], r"[A-Za-z0-9_-]{1,96}")
            or data["phase"] not in {"EXECUTION", "VERIFICATION", "COMPLETION_GATE"}
            or ("continuation_of" in data and not match(data["continuation_of"], r"RUN-[A-Za-z0-9_-]+-\d{3,}"))):
        raise ValueError("invalid public FAILURE subject")
    task = data["task"]
    if (not isinstance(task, dict) or set(task) != {"id", "revision"}
            or not match(task["id"], r"TASK-[A-Za-z0-9_-]+")
            or type(task["revision"]) is not int or task["revision"] < 1):
        raise ValueError("invalid public FAILURE TASK")
    candidate = data["candidate"]
    fields = {"transportable", "repairable", "dirty", "descends_from_base", "changed_files", "outside_task_scope"}
    if not isinstance(candidate, dict) or set(candidate) != fields:
        raise ValueError("invalid public FAILURE candidate")
    if any(type(candidate[k]) is not bool for k in fields - {"changed_files", "outside_task_scope"}):
        raise ValueError("invalid public FAILURE candidate flags")
    for key in ("changed_files", "outside_task_scope"):
        paths = candidate[key]
        if (not isinstance(paths, list) or any(not match(p, r"[A-Za-z0-9_./@ -]{1,4096}")
                or p.startswith("/") or ".." in p.split("/") for p in paths)
                or len(set(paths)) != len(paths)):
            raise ValueError("invalid public FAILURE repository paths")
    repairable = not candidate["dirty"] and candidate["descends_from_base"]
    if (candidate["repairable"] is not repairable
            or not set(candidate["outside_task_scope"]) <= set(candidate["changed_files"])
            or candidate["transportable"] is not (repairable and not candidate["outside_task_scope"])):
        raise ValueError("invalid public FAILURE candidate binding")

    def identity(value: Any) -> None:
        if (not isinstance(value, dict) or set(value) != {"private_identity_digest"}
                or not _is_digest(value["private_identity_digest"])):
            raise ValueError("invalid public verification conditions")

    def binding(value: Any) -> dict[str, Any]:
        value = validate_evidence_binding(value)
        if set(value) - {"subject_sha", "base_sha", "tree_sha", "command", "envelope_digest",
                        "changed_files_digest", "failure_set_digest", "correction_base_sha",
                        "correction_changed_files_digest", "profile", "toolchain"}:
            raise ValueError("invalid public FAILURE binding fields")
        _public_command(value["command"])
        identity(value["profile"])
        identity(value["toolchain"])
        return value

    def observation(value: Any, digest: str) -> None:
        fields = {"public_observation", "observation_digest", "subject_sha", "command", "profile", "toolchain",
                  "complete", "unstable", "exit_code", "failure_count", "report_count", "node_count",
                  "outcome_counts", "reports_digest", "failed_reports"}
        if (not isinstance(value, dict) or set(value) != fields
                or value["public_observation"] not in {PUBLIC_OBSERVATION_FORMAT, INCOMPLETE_OBSERVATION_FORMAT}
                or not _is_digest(digest) or value["observation_digest"] != digest
                or not _is_digest(value["reports_digest"]) or not match(value["subject_sha"], r"[0-9a-f]{40}")
                or type(value["complete"]) is not bool or type(value["unstable"]) is not bool
                or (value["complete"] and value["unstable"])
                or type(value["exit_code"]) is not int):
            raise ValueError("invalid public FAILURE observation")
        _public_command(value["command"])
        identity(value["profile"])
        identity(value["toolchain"])
        counts = value["outcome_counts"]
        if (not isinstance(counts, dict) or set(counts) != {"PASS", "FAIL", "SKIP"}
                or any(type(n) is not int or n < 0 for n in counts.values())
                or any(type(value[k]) is not int for k in ("report_count", "failure_count", "node_count"))
                or not 0 <= value["node_count"] <= value["report_count"] <= MAX_CANONICAL_REPORTS
                or (value["node_count"] == 0) != (value["report_count"] == 0)
                or value["report_count"] > 4 * value["node_count"]
                or sum(counts.values()) != value["report_count"]
                or not 0 <= value["failure_count"] == counts["FAIL"] <= MAX_CANONICAL_FAILURES
                or (value["exit_code"] == 0 and counts["FAIL"])
                or (value["complete"] and value["exit_code"] == 1 and not counts["FAIL"])
                or not isinstance(value["failed_reports"], list) or len(value["failed_reports"]) != counts["FAIL"]):
            raise ValueError("invalid public FAILURE report counts")
        previous = None
        for report in value["failed_reports"]:
            detail_available = isinstance(report, dict) and set(report) == {"nodeid_digest", "phase", "fingerprint"}
            detail_unavailable = (isinstance(report, dict) and set(report) == {"nodeid_digest", "phase", "detail_status"}
                                  and value["public_observation"] == INCOMPLETE_OBSERVATION_FORMAT
                                  and value["complete"] is False and report["detail_status"] in {"UNAVAILABLE", "WITHHELD"})
            if (not (detail_available or detail_unavailable)
                    or not _is_digest(report["nodeid_digest"])
                    or (detail_available and not _is_digest(report["fingerprint"]))
                    or not isinstance(report["phase"], str) or report["phase"] not in {"collect", "setup", "call", "teardown"}):
                raise ValueError("invalid public FAILURE failed identity")
            key = (report["nodeid_digest"], report["phase"])
            if previous is not None and key <= previous:
                raise ValueError("duplicate public FAILURE failed identity")
            previous = key
        if len({item["nodeid_digest"] for item in value["failed_reports"]}) > value["node_count"]:
            raise ValueError("invalid public FAILURE failed node count")

    error = data["error"]
    extra = {"exit_code", "diagnostic", "safe_detail"} if marker["format"] == PUBLIC_FAILURE_FORMAT else set()
    if (not isinstance(error, dict) or not {"type", "message", "verification"} <= error.keys()
            or set(error) - {"type", "message", "verification", "evidence_identities"} - extra
            or not match(error["type"], r"[A-Za-z_][A-Za-z0-9_.]{0,127}")
            or error["message"] != "Protected Runtime diagnostics remain local."
            or not isinstance(error["verification"], list)):
        raise ValueError("invalid public FAILURE diagnostics")
    if marker["format"] == PUBLIC_FAILURE_FORMAT:
        _validate_failure_diagnostic(data)
    if "safe_detail" in error:
        validate_safe_failure_detail(error["safe_detail"], dict(run_id=data["run_id"],
            subject_sha=data["failed_head_sha"], phase=data["phase"],
            private_failure_digest=marker["private_failure_digest"], verification=error["verification"]))
    identities = error.get("evidence_identities", [])
    if not isinstance(identities, list):
        raise ValueError("invalid public FAILURE evidence identities")
    seen = set()
    for item in identities:
        if (not isinstance(item, dict) or set(item) != {"evidence_id", "run_id", "subject_sha", "record_digest"}
                or not match(item["evidence_id"], r"[A-Za-z0-9_-]{1,96}") or item["evidence_id"] in seen
                or item["run_id"] != data["run_id"] or item["subject_sha"] != data["failed_head_sha"]
                or not _is_digest(item["record_digest"])):
            raise ValueError("invalid public FAILURE evidence identity binding")
        seen.add(item["evidence_id"])
    # Historical verification failures can lack outcomes. An empty population
    # conveys unavailable diagnostics, never a successful command or PASS proof.
    # A recorded population must still contain an actual nonzero outcome.
    if data["phase"] == "VERIFICATION" and error["verification"] and not any(
        isinstance(item, dict) and type(item.get("exit_code")) is int and item["exit_code"] != 0
        for item in error["verification"]
    ):
        raise ValueError("public VERIFICATION FAILURE has no nonzero outcome")
    for outcome in error["verification"]:
        if (not isinstance(outcome, dict) or not {"command", "exit_code", "summary"} <= outcome.keys()
                or set(outcome) - {"command", "exit_code", "summary", "verification"}
                or type(outcome["exit_code"]) is not int
                or outcome["summary"] != "Runtime verification exit code " + str(outcome["exit_code"])):
            raise ValueError("invalid public FAILURE outcome")
        _public_command(outcome["command"])
        if "verification" not in outcome:
            continue
        ref = outcome["verification"]
        fields = {"policy", "evidence_id", "run_id", "private_reference_digest", "binding", "binding_digest",
                  "raw_digest", "candidate", "candidate_digest"}
        if (not isinstance(ref, dict) or not fields <= ref.keys()
                or set(ref) - fields - {"base", "base_digest", "base_raw_digest", "reuse_binding"}
                or ref["policy"] != MINIMUM_SUFFICIENT_V2
                or not match(ref["evidence_id"], r"[A-Za-z0-9_-]{1,96}")
                or not match(ref["run_id"], r"RUN-[A-Za-z0-9_-]+-\d{3,}")
                or any(not _is_digest(ref[k]) for k in ("private_reference_digest", "binding_digest", "raw_digest"))):
            raise ValueError("invalid public FAILURE reference")
        bound = binding(ref["binding"])
        observation(ref["candidate"], ref["candidate_digest"])
        observed = ref["candidate"]
        if (any(observed[k] != bound[k] for k in ("subject_sha", "command", "profile", "toolchain"))
                or observed["command"] != outcome["command"] or observed["exit_code"] != outcome["exit_code"]):
            raise ValueError("public FAILURE outcome binding mismatch")
        effective = bound
        if "reuse_binding" in ref:
            effective = binding(ref["reuse_binding"])
            if {k: v for k, v in effective.items() if k != "subject_sha"} != {
                    k: v for k, v in bound.items() if k != "subject_sha"}:
                raise ValueError("public FAILURE reuse binding mismatch")
        elif ref["run_id"] != data["run_id"]:
            raise ValueError("public FAILURE RUN binding mismatch")
        if effective["subject_sha"] != data["failed_head_sha"]:
            raise ValueError("public FAILURE subject binding mismatch")
        if "base" in ref:
            if not {"base_digest", "base_raw_digest"} <= ref.keys() or not _is_digest(ref["base_raw_digest"]):
                raise ValueError("invalid public FAILURE base metadata")
            observation(ref["base"], ref["base_digest"])
            base = ref["base"]
            if (base["subject_sha"] != bound["base_sha"] or base["command"] != bound["command"]
                    or base["toolchain"] != bound["toolchain"] or bound["failure_set_digest"] != ref["base_digest"]):
                raise ValueError("public FAILURE base binding mismatch")
        elif {"base_digest", "base_raw_digest"} & ref.keys():
            raise ValueError("orphaned public FAILURE base metadata")


def _failure_input(content: bytes) -> dict[str, Any]:
    if len(content) > PUBLIC_FAILURE_MAX_BYTES:
        raise FailureDiagnosticError("FAILURE_SOURCE_SIZE_EXCEEDED", "FAILURE exceeds byte bound")
    try:
        return _public_document(content, max_bytes=PUBLIC_FAILURE_MAX_BYTES)
    except ReviewTransportError as exc:
        raise FailureDiagnosticError("FAILURE_SOURCE_DOCUMENT_INVALID") from exc


def failure_document(content: bytes, *, private_original: bool = False) -> Mapping[str, Any]:
    """Read an exact FAILURE; only a known local source grants the private role.

    A message is not provenance. Callers authenticating a Runtime-owned local
    original may retain its private diagnostics even if its message happens to
    equal the public sentinel. Public input keeps the strict redaction gate.
    Minimal protected historical records can omit phase; accepting that read
    format neither authenticates their source nor permits a new native write.
    """
    data = _failure_input(content)
    if data.get("kind") != "FAILURE":
        raise FailureDiagnosticError("FAILURE_KIND_INVALID")
    if private_original and "public_projection" not in data:
        try:
            from .unified_state import _validated_private_public_view
            _validated_private_public_view(data, "METADATA")
            if {"phase", "error", "candidate"} <= data.keys():
                _safe_failure_diagnostic(data)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
            raise FailureDiagnosticError("FAILURE_PRIVATE_INTEGRITY_INVALID") from exc
        return data
    error = data.get("error")
    if isinstance(error, dict) and error.get("message") == "Protected Runtime diagnostics remain local.":
        outcomes = error.get("verification", [])
        historical_references = [item["verification"] for item in outcomes
            if isinstance(item, dict) and isinstance(item.get("verification"), dict)
            and item["verification"].get("policy") == "minimum-sufficient-v2"
            and isinstance(item["verification"].get("candidate"), dict)
            and item["verification"]["candidate"].get("complete") is False
        ] if isinstance(outcomes, list) else []
        historical_annotations = {id(annotation) for ref in historical_references
            if isinstance(ref.get("attribution"), list) for annotation in ref["attribution"]
            if isinstance(annotation, dict) and isinstance(annotation.get("detail"), str)}

        def captured(value: Any) -> bool:
            if isinstance(value, list):
                return any(captured(item) for item in value)
            # Authenticated incomplete historical references retain private
            # attribution annotations. They are not report detail or public
            # diagnostics, and the projection binds them only by source digest.
            # The role-aware validator below still checks their entire contents.
            forbidden = {"captured_output", "stdout", "stderr", "traceback", "longrepr",
                         "native_diagnostics", "executor_diagnostics", "detail"}
            if id(value) in historical_annotations:
                forbidden.remove("detail")
            return isinstance(value, dict) and (
                bool(set(value) & forbidden)
                or any(captured(item) for item in value.values()))

        if "public_projection" not in data and captured(data):
            raise ReviewTransportError("private captured output in public FAILURE")
        if "public_projection" not in data:
            try:
                # Match the prospective small FAILURE writer's privacy gate;
                # historical private originals retain their exact read path.
                if _has_failure_observations(data):
                    from .unified_state import _validated_private_public_view
                    _validated_private_public_view(data, "METADATA")
                else:
                    _validate_legacy_failure_public(data, historical_read=True)
                    if {"run_id", "failed_head_sha"} <= data.keys():
                        from .unified_state import _validated_private_public_view
                        _validated_private_public_view(data, "METADATA")
            except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
                raise FailureDiagnosticError("FAILURE_PUBLIC_PRIVACY_INVALID") from exc
    if "public_projection" in data:
        try:
            validate_public_failure(data)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
            raise FailureDiagnosticError("FAILURE_PUBLIC_INTEGRITY_INVALID") from exc
    else:
        try:
            _validate_native_failure_exit(data, historical_read=True)
            if _needs_failure_projection(data):
                from .unified_state import _validated_private_public_view
                _validated_private_public_view(data, "METADATA")
                _safe_failure_diagnostic(data)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
            raise FailureDiagnosticError("FAILURE_PRIVATE_INTEGRITY_INVALID") from exc
    return data


class RemoteQueryError(ReviewTransportError):
    """Bounded failure to acquire a canonical remote ref snapshot."""

    def __init__(self, message: str, *, category: str = "UNKNOWN") -> None:
        super().__init__(message)
        self.category = category if category in {
            "AUTH", "DNS", "TLS", "TIMEOUT", "CONNECTIVITY", "UNKNOWN"
        } else "UNKNOWN"


def validate_runtime_failure_binding(
    failure: Mapping[str, Any],
    *,
    run_id: str,
    task_id: str,
    task_revision: int,
    executor: str,
    base_sha: str,
    candidate_sha: str,
    modification_scope: tuple[str, ...],
    actual_descends_from_base: bool | None = None,
    actual_changed_files: set[str] | None = None,
) -> None:
    """Validate the Runtime FAILURE identity and repair-candidate contract."""

    _validate_native_failure_exit(failure)
    if "public_projection" in failure:
        validate_public_failure(failure)
    task = failure.get("task")
    if (
        failure.get("kind") != "FAILURE"
        or failure.get("run_id") != run_id
        or not isinstance(task, Mapping)
        or dict(task) != {"id": task_id, "revision": task_revision}
        or type(task.get("revision")) is not int
        or failure.get("executor") != executor
        or failure.get("base_sha") != base_sha
        or failure.get("failed_head_sha") != candidate_sha
    ):
        raise ValueError("FAILURE identity does not match RUN and candidate")

    candidate = failure.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("FAILURE candidate must be a mapping")
    flags = (
        candidate.get("transportable"),
        candidate.get("repairable"),
        candidate.get("dirty"),
        candidate.get("descends_from_base"),
    )
    if not all(isinstance(value, bool) for value in flags):
        raise ValueError("FAILURE candidate flags must be booleans")

    def paths(name: str) -> list[str]:
        value = candidate.get(name)
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) and item for item in value)
            or len(value) != len(set(value))
        ):
            raise ValueError(f"FAILURE candidate {name} is invalid")
        return value

    changed_files = paths("changed_files")
    outside_scope = paths("outside_task_scope")
    if set(outside_scope) != set(changed_files).difference(modification_scope):
        raise ValueError("FAILURE candidate scope binding is invalid")
    descends = candidate["descends_from_base"]
    repairable = not candidate["dirty"] and descends
    transportable = repairable and not outside_scope
    if (
        candidate["repairable"] is not repairable
        or candidate["transportable"] is not transportable
    ):
        raise ValueError("FAILURE candidate repair binding is invalid")
    if (
        actual_descends_from_base is not None
        and descends is not actual_descends_from_base
    ):
        raise ValueError("FAILURE candidate ancestry binding is invalid")
    if (
        actual_changed_files is not None
        and set(changed_files) != actual_changed_files
    ):
        raise ValueError("FAILURE candidate changed-files binding is invalid")


@dataclass(frozen=True)
class RemoteRemediationLineage:
    """Immutable canonical inputs resolved from one remote remediation ref."""

    ref: str
    source_run_id: str
    review: bytes
    remediation: bytes
    run: bytes
    result: bytes
    repair: bytes | None = None
    commit_sha: str = ""
    task_id: str = ""
    task_revision: int = 0


_BATCH_SOURCE_PROOFS: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("batch_source_proofs", default=())


def resolve_authenticated_batch(repo: Path, *, source_run_id: str, batch_id: str,
                                required_sha: str | None = None, remote: str | None = None):
    seen = _BATCH_SOURCE_PROOFS.get()
    key = (source_run_id, batch_id)
    if key in seen or len(seen) >= 32:
        raise ReviewTransportError("cyclic or oversized batch source proof lineage")
    token = _BATCH_SOURCE_PROOFS.set((*seen, key))
    try:
        return _resolve_authenticated_batch(repo, source_run_id=source_run_id, batch_id=batch_id,
            required_sha=required_sha, remote=remote)
    finally:
        _BATCH_SOURCE_PROOFS.reset(token)


def _resolve_authenticated_batch(repo: Path, *, source_run_id: str, batch_id: str,
                                 required_sha: str | None, remote: str | None):
    """Use AUTHOR_REMEDIATION's immutable source proof for a batch selector."""
    from .review import BatchRemediation, is_batch_selector, parse_remediation, validate_remediation
    from .authoring_ingress import (IngressEnvelope, _read_direct_remediation_binding,
        _direct_remediation_blob, _validate_metadata_commit, _recheck_direct_remediation_binding)
    if not is_batch_selector(batch_id):
        raise ReviewTransportError("explicit versioned batch selector required")
    remote = remote or resolve_transport_remote(repo)
    lineages = resolve_remote_remediation_lineages(repo, finding_id=batch_id, source_run_id=source_run_id, remote=remote)
    if len(lineages) != 1:
        raise ReviewTransportError("batch authority is missing or ambiguous")
    lineage = lineages[0]
    rem = parse_remediation(lineage.remediation.decode("utf-8"))
    if not isinstance(rem, BatchRemediation) or rem.batch_id != batch_id or rem.selection.source_run_id != source_run_id:
        raise ReviewTransportError("batch selector cannot be decoded as a legacy finding")
    if required_sha is not None and lineage.commit_sha != required_sha:
        raise ReviewTransportError("approved batch authorization SHA is stale")
    envelope = IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REMEDIATION",
        {"source_run_id": source_run_id, "batch_id": batch_id, "finding_ids": list(rem.selection.finding_ids)},
        {"expected_reviewed_sha": rem.reviewed_sha, "expected_review_decision_sha": rem.selection.review_decision_sha},
        lineage.remediation.decode("utf-8"))
    binding = _read_direct_remediation_binding(envelope, repo, require_current=False, observation_remote=remote)
    if binding.existing_sha != lineage.commit_sha or binding.refs.get(f"refs/heads/aios/artifacts/{source_run_id}") != rem.selection.source_artifacts_sha:
        raise ReviewTransportError("canonical batch authority ref moved")
    path = f".ai/remediations/REMEDIATION-{source_run_id}-{batch_id}.yaml"
    raw = _direct_remediation_blob(repo, lineage.commit_sha, path)
    _validate_metadata_commit(repo, lineage.commit_sha, expected_parent_sha=rem.selection.review_decision_sha,
        metadata_path=path, metadata_bytes=raw, operation="batch authority")
    if raw != lineage.remediation:
        raise ReviewTransportError("batch authority bytes were substituted")
    validate_remediation(review=binding.review, remediation=rem, task=binding.task)
    _recheck_direct_remediation_binding(envelope, repo, binding, observation_remote=remote)
    return binding, rem, lineage.commit_sha


def reject_overlapping_remediations(repo: Path, *, remote: str, refs: Mapping[str, str],
                                    source_run_id: str, finding_ids: Sequence[str], own_ref: str) -> None:
    """Reject simultaneous single/batch authority over the same source findings."""
    from .review import BatchRemediation, is_batch_selector, parse_remediation
    prefix = f"refs/heads/aios/remediation/{source_run_id}-"
    if len(finding_ids) == 1 and not any(ref.startswith(prefix) and is_batch_selector(ref[len(prefix):]) for ref in refs):
        return
    consumed: set[str] = set()
    for ref, sha in refs.items():
        if ref.startswith(("refs/heads/aios/artifacts/", "refs/heads/aios/failure-artifacts/")):
            raw = _read_remote_blob(repo, remote, sha, ".ai/transport/run.json")
            if raw is None:
                raise ReviewTransportError("canonical correction RUN content missing")
            run = _performance_json_mapping(raw, "correction RUN")
            if run.get("remediation_authorization_sha") is not None:
                consumed.add(run["remediation_authorization_sha"])
    for ref, sha in refs.items():
        if not ref.startswith(prefix) or ref == own_ref or sha in consumed:
            continue
        selector = ref[len(prefix):]
        if not is_batch_selector(selector) and selector not in finding_ids:
            continue
        raw = _read_remote_blob(repo, remote, sha, f".ai/remediations/REMEDIATION-{source_run_id}-{selector}.yaml")
        if raw is None:
            _, paths, _ = _git_cmd(repo, "ls-tree", "-r", "--name-only", sha, "--", ".ai/remediations")
            candidates = [p for p in paths.splitlines() if p.endswith((".yaml", ".yml"))]
            if len(candidates) != 1:
                raise ReviewTransportError("canonical competing remediation is malformed")
            raw = _read_remote_blob(repo, remote, sha, candidates[0])
            if raw is None:
                raise ReviewTransportError("canonical competing remediation bytes missing")
        rem = parse_remediation(raw.decode("utf-8"))
        ids = rem.selection.finding_ids if isinstance(rem, BatchRemediation) else (rem.finding_id,)
        if set(ids).intersection(finding_ids):
            raise ReviewTransportError("overlapping active batch/single remediation authority")


@dataclass(frozen=True)
class RemoteFailureArtifacts:
    """Immutable Runtime-owned facts for one canonical failed RUN."""

    run_id: str
    candidate_sha: str
    run: bytes
    failure: bytes
    repair: bytes | None
    preverification: bytes | None = None
    execution_profile: bytes | None = None


@dataclass(frozen=True)
class RemoteRepairRecovery:
    """Portable failed correction chain and canonical remote RUN namespace."""

    failures: tuple[RemoteFailureArtifacts, ...]
    remote_run_ids: tuple[str, ...]
    observed_refs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RemoteRunNamespace:
    """Validated canonical terminal RUN identities for one TASK namespace."""

    run_ids: tuple[str, ...]
    conflicts: tuple[str, ...]
    observed_refs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RemotePrimaryRecovery:
    """Exact immutable facts for one conflicting PRIMARY terminal identity."""

    run_id: str
    candidate_sha: str
    success_run: bytes
    result: bytes
    failure_run: bytes
    failure: bytes
    remote_run_ids: tuple[str, ...]
    observed_refs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RemoteLifecycleTerminal:
    """One immutable terminal identity observed for Unified State."""

    run_id: str
    kind: str
    candidate_sha: str
    run: bytes
    terminal: bytes
    correction: bytes | None = None
    candidate_available: bool = True
    publication_recovery: bytes | None = None


@dataclass(frozen=True)
class RemoteLifecycleReview:
    """One immutable semantic decision bound to a RUN identity."""

    run_id: str
    decision_sha: str
    review: bytes


@dataclass(frozen=True)
class RemoteTaskLifecycle:
    """Allowlisted canonical ref snapshot used by the read-only state reducer."""

    main_sha: str
    terminals: tuple[RemoteLifecycleTerminal, ...]
    reviews: tuple[RemoteLifecycleReview, ...]
    remediation_selectors: tuple[tuple[str, str, str], ...]
    repair_selectors: tuple[tuple[str, str, bytes], ...]
    observed_refs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RemoteRepairAuthorization:
    """The uniquely current immutable authorization for one failed RUN."""

    failed_run_id: str
    ref: str
    commit_sha: str
    revision: int
    repair: bytes
    predecessor_sha: str | None = None
    failure_artifacts_sha: str | None = None


REPAIR_SUPERSESSION_FORMAT = "AIOS_REPAIR_SUPERSESSION"
REPAIR_SUPERSESSION_PATH = ".ai/transport/repair-supersession.json"
REPAIR_SUPERSESSION_PREFIX = "refs/heads/aios/repair-supersession/"


@dataclass(frozen=True)
class RemoteTerminalArtifact:
    """One canonically bound terminal artifact from the upstream snapshot."""

    run_id: str
    task_id: str
    terminal_kind: str
    artifact_sha: str
    run: bytes
    terminal: bytes
    observation: bytes | None = None
    task_revision: int | None = None
    executor: str | None = None
    base_sha: str | None = None
    candidate_sha: str | None = None
    execution_profile: bytes | None = None


@dataclass(frozen=True)
class RemotePerformanceSnapshot:
    """Bounded terminal snapshot selected only by canonical upstream refs."""

    task_selectors: tuple[str, ...]
    terminals: tuple[RemoteTerminalArtifact, ...]
    observed_refs: tuple[tuple[str, str], ...] = ()


PERFORMANCE_MAX_TERMINAL_RUNS = 256


def _git_cmd(repo: Path, *args: str, strip: bool = True, allow_fail: bool = False) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=False,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        if allow_fail:
            return 1, "", str(exc)
        raise ReviewTransportError(f"Git command failed: {exc}") from exc
    if completed.returncode != 0 and not allow_fail:
        detail = stderr.strip() or stdout.strip()
        raise ReviewTransportError(f"Git command failed: {detail}")
    return completed.returncode, stdout.strip() if strip else stdout, stderr.strip()


def resolve_transport_remote(repo: Path) -> str:
    """Resolve configured upstream remote for the current branch or fail closed."""
    code, branch, _ = _git_cmd(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allow_fail=True)
    if code == 0 and branch:
        code, remote, _ = _git_cmd(repo, "config", "--get", f"branch.{branch}.remote", allow_fail=True)
        if code == 0 and remote:
            return remote
    raise ReviewTransportError("no configured upstream Git remote for current branch")


def resolve_detached_observation_remote(repo: Path) -> str:
    """Resolve a detached reader from unanimous configured branch upstreams.

    A remote's mere presence (including origin) does not establish canonical
    authority. This is only for read-only observation, never transport writes.
    """
    code, _, _ = _git_cmd(repo, "symbolic-ref", "--quiet", "HEAD", allow_fail=True)
    if code == 0:
        raise ReviewTransportError("detached observation requires detached HEAD")
    code, bindings, _ = _git_cmd(
        repo, "config", "--get-regexp", r"^branch\..*\.remote$", allow_fail=True
    )
    if code not in (0, 1):
        raise ReviewTransportError("cannot read detached observation upstreams")
    remotes = set()
    for line in bindings.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[1].strip():
            raise ReviewTransportError("invalid detached observation upstream")
        remotes.add(parts[1].strip())
    if len(remotes) != 1 or "." in remotes:
        raise ReviewTransportError("missing or ambiguous detached observation remote")
    remote = remotes.pop()
    code, url, _ = _git_cmd(repo, "config", "--get", f"remote.{remote}.url", allow_fail=True)
    if code != 0 or not url:
        raise ReviewTransportError("detached observation remote URL is missing")
    return remote


def _read_remote_blob(repo: Path, remote: str, commit_sha: str, rel_path: str) -> bytes | None:
    """Read a blob's content at commit_sha:rel_path from remote or local object DB."""
    _git_cmd(repo, "fetch", "--no-tags", remote, commit_sha, allow_fail=True)
    # `show` also stats commit:path as a worktree filename. Long batch metadata
    # names exceed Windows' path limit in isolated observers despite a valid blob.
    code, content, _ = _git_cmd(repo, "cat-file", "blob", f"{commit_sha}:{rel_path}", strip=False, allow_fail=True)
    if code == 0:
        raw = content.encode("utf-8")
        if rel_path == ".ai/transport/failure.json":
            failure_document(raw)
        return raw
    return None


def _read_lifecycle_blob(
    repo: Path, remote: str, commit_sha: str, rel_path: str
) -> bytes | None:
    """Read in an observer while distinguishing unavailable transport from absence."""

    fetch_code, _, fetch_error = _git_cmd(
        repo, "fetch", "--no-tags", remote, commit_sha, allow_fail=True
    )
    code, content, _ = _git_cmd(
        repo, "show", f"{commit_sha}:{rel_path}", strip=False, allow_fail=True
    )
    if code == 0:
        raw = content.encode("utf-8")
        if rel_path == ".ai/transport/failure.json":
            failure_document(raw)
        return raw
    if fetch_code:
        raise RemoteQueryError(
            "failed to acquire canonical lifecycle object",
            category=_classify_remote_failure(fetch_error),
        )
    return None


def _read_local_blob(repo: Path, commit_sha: str, rel_path: str) -> bytes | None:
    """Read an already-fetched optional blob without another remote operation."""

    code, content, _ = _git_cmd(
        repo, "show", f"{commit_sha}:{rel_path}", strip=False, allow_fail=True
    )
    if code != 0:
        return None
    raw = content.encode("utf-8")
    if rel_path == ".ai/transport/failure.json":
        failure_document(raw)
    return raw


def task_run_prefix(task_id: str) -> str:
    """Return the deterministic RUN prefix for one TASK."""
    if not task_id or "/" in task_id or "\\" in task_id:
        raise ReviewTransportError(f"invalid TASK id: {task_id!r}")
    task_part = task_id.removeprefix("TASK-")
    if not task_part:
        raise ReviewTransportError(f"invalid TASK id: {task_id!r}")
    return f"RUN-{task_part}-"


def resolve_remote_run_namespace(
    repo: Path, *, task_id: str, task_revision: int
) -> RemoteRunNamespace:
    """Resolve the TASK namespace, retaining RUNs from every valid revision."""

    if (
        isinstance(task_revision, bool)
        or not isinstance(task_revision, int)
        or task_revision < 1
    ):
        raise ReviewTransportError("invalid TASK revision")
    task_prefix = task_run_prefix(task_id)
    remote = resolve_transport_remote(repo)
    refs = _exact_remote_refs(
        repo,
        remote,
        f"refs/heads/aios/failure-artifacts/{task_prefix}*",
        f"refs/heads/aios/artifacts/{task_prefix}*",
        f"refs/heads/aios/publication-recovery/{task_prefix}*",
    )
    try:
        return _remote_run_namespace_from_refs(
            repo,
            remote,
            task_id=task_id,
            task_revision=task_revision,
            task_prefix=task_prefix,
            refs=refs,
        )
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(refs.items()))
        raise


def resolve_remote_performance_snapshot(
    repo: Path, *, task_ids: Sequence[str]
) -> RemotePerformanceSnapshot:
    """Acquire one bounded, read-only snapshot of selected terminal namespaces.

    The terminal refs are the sole selection authority.  RUN and terminal identity
    are completely bound before an optional observation sidecar is read, so absent
    telemetry can never mask malformed terminal lineage.
    """

    selectors = _validate_performance_task_ids(task_ids)
    prefixes = {task_id: task_run_prefix(task_id) for task_id in selectors}
    patterns = tuple(
        pattern
        for task_id in selectors
        for pattern in (
            f"refs/heads/aios/artifacts/{prefixes[task_id]}*",
            f"refs/heads/aios/failure-artifacts/{prefixes[task_id]}*",
        )
    )
    remote = resolve_transport_remote(repo)
    refs = _exact_remote_refs(repo, remote, *patterns)
    try:
        records: list[tuple[str, str, str, str, str]] = []
        kinds: dict[str, str] = {}
        for ref, artifact_sha in sorted(refs.items()):
            if ref.startswith("refs/heads/aios/artifacts/"):
                terminal_kind = "RESULT"
                run_id = ref.removeprefix("refs/heads/aios/artifacts/")
            elif ref.startswith("refs/heads/aios/failure-artifacts/"):
                terminal_kind = "FAILURE"
                run_id = ref.removeprefix(
                    "refs/heads/aios/failure-artifacts/"
                )
            else:
                raise ReviewTransportError("canonical ref namespace mismatch")

            matches = [
                task_id
                for task_id, prefix in prefixes.items()
                if re.fullmatch(rf"{re.escape(prefix)}\d{{3,}}", run_id)
            ]
            if len(matches) != 1:
                raise ReviewTransportError(
                    f"canonical RUN ref identity is invalid: {ref}"
                )
            previous = kinds.get(run_id)
            if previous is not None and previous != terminal_kind:
                raise ReviewTransportError(
                    f"competing RESULT/FAILURE terminal identity for RUN: {run_id}"
                )
            kinds[run_id] = terminal_kind
            records.append(
                (run_id, matches[0], terminal_kind, ref, artifact_sha)
            )

        if len(kinds) > PERFORMANCE_MAX_TERMINAL_RUNS:
            raise ReviewTransportError(
                "terminal RUN snapshot exceeds maximum bound of "
                f"{PERFORMANCE_MAX_TERMINAL_RUNS}: {len(kinds)}"
            )

        terminals: list[RemoteTerminalArtifact] = []
        for run_id, task_id, terminal_kind, ref, artifact_sha in records:
            run_bytes = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/run.json"
            )
            if run_bytes is None:
                raise ReviewTransportError(
                    f"canonical {terminal_kind} RUN content is missing for {run_id}"
                )
            terminal_path = (
                ".ai/transport/result.json"
                if terminal_kind == "RESULT"
                else ".ai/transport/failure.json"
            )
            terminal_bytes = _read_lifecycle_blob(
                repo, remote, artifact_sha, terminal_path
            )
            if terminal_bytes is None:
                raise ReviewTransportError(
                    f"canonical {terminal_kind} content is missing for {run_id}"
                )

            revision, executor, base_sha, candidate_sha = (
                _bind_performance_terminal_identity(
                    run_bytes,
                    terminal_bytes,
                    ref=ref,
                    run_id=run_id,
                    task_id=task_id,
                    terminal_kind=terminal_kind,
                )
            )
            # Coverage is classified only after the RUN and terminal family bind.
            observation = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/observation.json"
            )
            execution_profile = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/execution-profile.json"
            )
            terminals.append(
                RemoteTerminalArtifact(
                    run_id=run_id,
                    task_id=task_id,
                    terminal_kind=terminal_kind,
                    artifact_sha=artifact_sha,
                    run=run_bytes,
                    terminal=terminal_bytes,
                    observation=observation,
                    task_revision=revision,
                    executor=executor,
                    base_sha=base_sha,
                    candidate_sha=candidate_sha,
                    execution_profile=execution_profile,
                )
            )
        return RemotePerformanceSnapshot(
            task_selectors=selectors,
            terminals=tuple(sorted(terminals, key=lambda item: item.run_id)),
            observed_refs=tuple(sorted(refs.items())),
        )
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(refs.items()))
        raise


def _validate_performance_task_ids(task_ids: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(task_ids, (list, tuple)) or not task_ids:
        raise ReviewTransportError(
            "task selectors must contain between 1 and 32 exact TASK identities"
        )
    if len(task_ids) > 32:
        raise ReviewTransportError("task selectors exceed maximum bound of 32")
    if any(not isinstance(item, str) for item in task_ids):
        raise ReviewTransportError("malformed TASK selector identity")
    if len(task_ids) != len(set(task_ids)):
        raise ReviewTransportError("task selectors contain duplicate identities")
    for task_id in task_ids:
        if re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task_id) is None:
            raise ReviewTransportError(
                f"malformed TASK selector identity: {task_id!r}"
            )
        if ":" in task_id or "@" in task_id:
            raise ReviewTransportError(
                f"revision-qualified TASK selector is not allowed: {task_id!r}"
            )
    return tuple(sorted(task_ids))


def _bind_performance_terminal_identity(
    run_bytes: bytes,
    terminal_bytes: bytes,
    *,
    ref: str,
    run_id: str,
    task_id: str,
    terminal_kind: str,
) -> tuple[int, str, str, str]:
    """Bind terminal identity to a fully validated admitted RUN."""

    from .artifacts import validate_evidence, validate_result
    from .run import ACTIVE, Run, RunTaskReference

    run_document = _performance_json_mapping(run_bytes, "RUN")
    if run_document.get("kind") == "REMEDIATION":
        execution = run_document.get("execution")
        if not isinstance(execution, Mapping):
            raise ReviewTransportError(
                f"canonical REMEDIATION execution is invalid for {run_id}"
            )
        run_data = execution.get("run")
        if not isinstance(run_data, Mapping):
            raise ReviewTransportError(
                f"canonical REMEDIATION RUN is invalid for {run_id}"
            )
        _bind_remediation_wrapper(run_document, execution, run_id=run_id)
    elif "kind" in run_document:
        raise ReviewTransportError(f"unknown canonical RUN kind at {ref}")
    else:
        run_data = run_document
    task = run_data.get("task") if isinstance(run_data, Mapping) else None
    if not isinstance(task, Mapping) or set(task) != {"id", "revision"}:
        raise ReviewTransportError(f"canonical RUN task is invalid for {run_id}")
    try:
        run = Run(
            run_id=run_data["run_id"],
            task=RunTaskReference(id=task["id"], revision=task["revision"]),
            executor=run_data["executor"],
            base_sha=run_data["base_sha"],
            workspace=run_data["workspace"],
            head_sha=run_data.get("head_sha"),
            status=run_data["status"],
            return_affinity=document_affinity(run_data),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewTransportError(
            f"canonical RUN is invalid for {run_id}: {exc}"
        ) from exc
    if run.run_id != run_id or run.task.id != task_id or run.status != ACTIVE:
        raise ReviewTransportError(
            f"canonical RUN identity does not match terminal ref for {run_id}"
        )
    if re.fullmatch(r"[0-9a-f]{40}", run.base_sha) is None or (
        run.head_sha is not None
        and re.fullmatch(r"[0-9a-f]{40}", run.head_sha) is None
    ):
        raise ReviewTransportError(
            f"canonical RUN SHA identity is invalid for {run_id}"
        )

    terminal = _performance_json_mapping(terminal_bytes, terminal_kind)
    if terminal_kind == "RESULT":
        if set(terminal) != {"result", "evidence"}:
            raise ReviewTransportError(
                f"canonical RESULT package is invalid for {run_id}"
            )
        try:
            result = validate_result(terminal["result"])
            raw_evidence = terminal["evidence"]
            if not isinstance(raw_evidence, list):
                raise TypeError("evidence must be a list")
            evidence = tuple(validate_evidence(item) for item in raw_evidence)
        except (KeyError, TypeError, ValueError) as exc:
            raise ReviewTransportError(
                f"canonical RESULT package is invalid for {run_id}: {exc}"
            ) from exc
        _bind_result_identity(run, result, evidence)
        candidate_sha = result.head_sha
    elif terminal_kind == "FAILURE":
        candidate_sha = _bind_failure_identity(terminal, run)
    else:
        raise ReviewTransportError("unsupported terminal family")
    if re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is None:
        raise ReviewTransportError(
            f"canonical {terminal_kind} candidate identity is invalid for {run_id}"
        )
    acceptance = run_document.get("acceptance")
    if acceptance is not None and (
        not isinstance(acceptance, Mapping)
        or dict(acceptance)
        != {"mode": "DIRECT_CANDIDATE", "candidate_head": candidate_sha}
    ):
        raise ReviewTransportError(
            f"canonical terminal candidate binding conflicts for {run_id}"
        )
    return run.task.revision, run.executor, run.base_sha, candidate_sha


def _bind_remediation_wrapper(
    document: Mapping[str, Any], execution: Mapping[str, Any], *, run_id: str
) -> None:
    from .review import REMEDIATION_ACTIONS, parse_remediation

    if "format" in document or "findings" in execution:
        from .operator import _remediation_execution_from_data, _parse_remediation_execution_base
        from .review import BatchRemediation, parse_batch_selection
        try:
            if (set(document) != {"format", "version", "kind", "execution", "predecessor", "execution_base", "remediation_authorization_sha"}
                    or document["format"] != "AIOS_BATCH_REMEDIATION_RUN" or type(document["version"]) is not int or document["version"] != 1
                    or document["kind"] != "REMEDIATION"
                    or re.fullmatch(r"[0-9a-f]{40}", str(document["remediation_authorization_sha"])) is None):
                raise ValueError("invalid versioned batch RUN wrapper")
            parsed = _remediation_execution_from_data(execution)
            predecessor = parse_batch_selection(document["predecessor"])
            if (not isinstance(parsed.remediation, BatchRemediation) or parsed.remediation.selection != predecessor
                    or parsed.run.run_id != run_id
                    or _parse_remediation_execution_base(document["execution_base"]).candidate_sha != parsed.run.base_sha):
                raise ValueError("batch RUN source/execution base mismatch")
            return
        except (KeyError, TypeError, ValueError) as exc:
            raise ReviewTransportError(f"invalid batch RUN: {exc}") from exc

    required = {"kind", "execution"}
    allowed = required | {"predecessor", "execution_base", "acceptance", "remediation_authorization_sha"}
    if not required.issubset(document) or set(document).difference(allowed):
        raise ReviewTransportError(
            f"canonical REMEDIATION wrapper is invalid for {run_id}"
        )
    if "remediation_authorization_sha" in document and (
        not isinstance(document["remediation_authorization_sha"], str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", document["remediation_authorization_sha"]) is None
    ):
        raise ReviewTransportError(
            f"canonical REMEDIATION authorization SHA is invalid for {run_id}"
        )
    finding = execution.get("finding")
    remediation_data = execution.get("remediation")
    run_data = execution.get("run")
    review_id = execution.get("review_id")
    original_constraints = execution.get("original_constraints", ())
    if (
        not isinstance(review_id, str)
        or not review_id
        or not isinstance(finding, Mapping)
        or set(finding) != {
            "id", "basis", "action", "location", "issue", "expected"
        }
        or not all(isinstance(value, str) and value for value in finding.values())
        or finding.get("action") not in REMEDIATION_ACTIONS
        or not isinstance(remediation_data, Mapping)
        or not isinstance(run_data, Mapping)
        or not isinstance(original_constraints, (list, tuple))
        or not all(
            isinstance(value, str) and value for value in original_constraints
        )
    ):
        raise ReviewTransportError(
            f"canonical REMEDIATION execution is invalid for {run_id}"
        )
    try:
        remediation = parse_remediation(json.dumps(remediation_data))
    except (TypeError, ValueError) as exc:
        raise ReviewTransportError(
            f"canonical REMEDIATION contract is invalid for {run_id}: {exc}"
        ) from exc
    if (
        remediation.finding_id != finding.get("id")
        or remediation.action != finding.get("action")
    ):
        raise ReviewTransportError(
            f"canonical REMEDIATION finding binding conflicts for {run_id}"
        )
    predecessor = document.get("predecessor")
    if predecessor is not None:
        if (
            not isinstance(predecessor, Mapping)
            or set(predecessor) != {
                "source_run_id", "review_id", "finding_id", "reviewed_sha"
            }
            or not isinstance(predecessor.get("source_run_id"), str)
            or re.fullmatch(
                r"RUN-[A-Za-z0-9_-]+-\d{3,}", predecessor["source_run_id"]
            )
            is None
        ):
            raise ReviewTransportError(
                f"canonical REMEDIATION predecessor is invalid for {run_id}"
            )
        expected = (
            review_id,
            finding.get("id"),
            remediation.reviewed_sha,
        )
        actual = (
            predecessor.get("review_id"),
            predecessor.get("finding_id"),
            predecessor.get("reviewed_sha"),
        )
        if expected != actual:
            raise ReviewTransportError(
                f"canonical REMEDIATION predecessor conflicts for {run_id}"
            )
    execution_base = document.get("execution_base")
    if execution_base is not None:
        if (
            predecessor is None
            or not isinstance(execution_base, Mapping)
            or set(execution_base) != {"run_id", "candidate_sha"}
            or not isinstance(run_data, Mapping)
            or execution_base.get("candidate_sha") != run_data.get("base_sha")
            or not isinstance(execution_base.get("run_id"), str)
            or re.fullmatch(
                r"RUN-[A-Za-z0-9_-]+-\d{3,}", execution_base["run_id"]
            )
            is None
        ):
            raise ReviewTransportError(
                f"canonical REMEDIATION execution base conflicts for {run_id}"
            )
    elif run_data.get("base_sha") != remediation.reviewed_sha:
        raise ReviewTransportError(
            f"canonical REMEDIATION base conflicts for {run_id}"
        )


def _bind_result_identity(run: Any, result: Any, evidence: Sequence[Any]) -> None:
    if run.head_sha is not None and run.head_sha != result.head_sha:
        raise ReviewTransportError("canonical RESULT head conflicts with RUN")
    by_id: dict[str, Any] = {}
    for item in evidence:
        if item.evidence_id in by_id:
            raise ReviewTransportError("canonical RESULT evidence identity is duplicated")
        by_id[item.evidence_id] = item
        if item.run_id != run.run_id or item.subject_sha != result.head_sha:
            raise ReviewTransportError(
                "canonical RESULT evidence does not bind to RUN and candidate"
            )
    for claim in result.claims:
        if set(claim.evidence).difference(by_id):
            raise ReviewTransportError(
                "canonical RESULT claim references missing evidence"
            )


def _bind_failure_identity(terminal: Mapping[str, Any], run: Any) -> str:
    task = terminal.get("task")
    if (
        terminal.get("kind") != "FAILURE"
        or terminal.get("run_id") != run.run_id
        or not isinstance(task, Mapping)
        or dict(task) != {"id": run.task.id, "revision": run.task.revision}
        or type(task.get("revision")) is not int
        or terminal.get("executor") != run.executor
        or terminal.get("base_sha") != run.base_sha
    ):
        raise ReviewTransportError(
            f"canonical FAILURE identity does not match RUN for {run.run_id}"
        )
    candidate = terminal.get("candidate")
    if not isinstance(candidate, Mapping) or set(candidate) != {
        "transportable",
        "repairable",
        "dirty",
        "descends_from_base",
        "changed_files",
        "outside_task_scope",
    }:
        raise ReviewTransportError(
            f"canonical FAILURE candidate is invalid for {run.run_id}"
        )
    flags = tuple(
        candidate[name]
        for name in (
            "transportable", "repairable", "dirty", "descends_from_base"
        )
    )
    if not all(isinstance(value, bool) for value in flags):
        raise ReviewTransportError(
            f"canonical FAILURE candidate flags are invalid for {run.run_id}"
        )
    paths: dict[str, list[str]] = {}
    for name in ("changed_files", "outside_task_scope"):
        value = candidate[name]
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) and item for item in value)
            or len(value) != len(set(value))
        ):
            raise ReviewTransportError(
                f"canonical FAILURE candidate paths are invalid for {run.run_id}"
            )
        paths[name] = value
    if not set(paths["outside_task_scope"]).issubset(paths["changed_files"]):
        raise ReviewTransportError(
            f"canonical FAILURE candidate scope conflicts for {run.run_id}"
        )
    repairable = not candidate["dirty"] and candidate["descends_from_base"]
    transportable = repairable and not paths["outside_task_scope"]
    if candidate["repairable"] is not repairable or candidate[
        "transportable"
    ] is not transportable:
        raise ReviewTransportError(
            f"canonical FAILURE candidate binding conflicts for {run.run_id}"
        )
    failed_head = terminal.get("failed_head_sha")
    if not isinstance(failed_head, str):
        raise ReviewTransportError(
            f"canonical FAILURE failed-head identity is invalid for {run.run_id}"
        )
    continuation = terminal.get("continuation_of")
    if continuation is not None and (
        not isinstance(continuation, str)
        or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", continuation) is None
    ):
        raise ReviewTransportError(
            f"canonical FAILURE continuation identity is invalid for {run.run_id}"
        )
    return failed_head


def _performance_json_mapping(content: bytes, name: str) -> Mapping[str, Any]:
    if name == "FAILURE":
        return failure_document(content)
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    def constant(_value: str) -> Any:
        raise ValueError("non-finite JSON")

    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
            parse_constant=constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ReviewTransportError(f"invalid canonical {name} JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ReviewTransportError(f"canonical {name} must be a mapping")
    return value


def resolve_remote_task_lifecycle(
    repo: Path, *, task_id: str, task_revision: int, remote: str | None = None
) -> RemoteTaskLifecycle:
    """Acquire one bounded canonical snapshot for Unified State.

    The caller supplies an isolated observation repository.  This function never
    updates a ref and derives no ordering from ref or filesystem timestamps.
    """

    if (
        isinstance(task_revision, bool)
        or not isinstance(task_revision, int)
        or task_revision < 1
    ):
        raise ReviewTransportError("invalid TASK revision")
    task_prefix = task_run_prefix(task_id)
    remote = remote or resolve_transport_remote(repo)
    refs = _exact_remote_refs(
        repo,
        remote,
        "refs/heads/main",
        f"refs/heads/aios/failure/{task_prefix}*",
        f"refs/heads/aios/failure-artifacts/{task_prefix}*",
        f"refs/heads/aios/artifacts/{task_prefix}*",
        f"refs/heads/aios/review/{task_prefix}*",
        f"refs/heads/aios/review-decision/{task_prefix}*",
        f"refs/heads/aios/publication-recovery/{task_prefix}*",
        f"refs/heads/aios/remediation/{task_prefix}*",
        f"refs/heads/aios/repair/{task_prefix}*",
        f"{REPAIR_SUPERSESSION_PREFIX}{task_prefix}*/*",
    )
    try:
        main_ref = "refs/heads/main"
        main_sha = refs.get(main_ref)
        if main_sha is None:
            raise RemoteQueryError("canonical main ref is missing")
        main_fetch, _, main_error = _git_cmd(
            repo, "fetch", "--no-tags", remote, main_sha, allow_fail=True
        )
        if main_fetch:
            raise RemoteQueryError(
                "failed to acquire canonical main",
                category=_classify_remote_failure(main_error),
            )
        publication_admissions = {}
        for ref, sha in refs.items():
            if not ref.startswith("refs/heads/aios/publication-recovery/"):
                continue
            run_bytes = _read_lifecycle_blob(repo, remote, sha, ".ai/transport/run.json")
            metadata = _read_lifecycle_blob(repo, remote, sha, ".ai/transport/publication-recovery.json")
            if run_bytes is None or metadata is None:
                raise ReviewTransportError("publication admission is incomplete")
            bound_task, revision, admitted_id = _decode_run_task_identity(run_bytes, ref)
            if bound_task != task_id:
                raise ReviewTransportError("publication admission TASK mismatch")
            if revision != task_revision:
                continue
            if admitted_id in publication_admissions:
                raise ReviewTransportError("competing publication admissions for the same RUN")
            publication_admissions[admitted_id] = (run_bytes, metadata)
        terminals: list[RemoteLifecycleTerminal] = []
        for ref, artifact_sha in sorted(refs.items()):
            if ref.startswith("refs/heads/aios/failure-artifacts/"):
                kind = "FAILURE"
                candidate_ref = "refs/heads/aios/failure/" + ref.rsplit("/", 1)[-1]
                terminal_path = ".ai/transport/failure.json"
            elif ref.startswith("refs/heads/aios/artifacts/"):
                kind = "RESULT"
                candidate_ref = "refs/heads/aios/review/" + ref.rsplit("/", 1)[-1]
                terminal_path = ".ai/transport/result.json"
            else:
                continue
            run_id = ref.rsplit("/", 1)[-1]
            run = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/run.json"
            )
            if run is None:
                raise ReviewTransportError(
                    f"canonical {kind} RUN content is missing for {run_id}"
                )
            bound_task, bound_revision, bound_run = _decode_run_task_identity(run, ref)
            if bound_task != task_id or bound_run != run_id:
                raise ReviewTransportError(
                    f"canonical terminal identity does not match TASK: {run_id}"
                )
            if bound_revision != task_revision:
                continue
            admitted = publication_admissions.get(run_id)
            if admitted is not None and _json_mapping(admitted[0], "admitted RUN") != _json_mapping(run, "terminal RUN"):
                raise ReviewTransportError("publication terminal RUN differs from admitted RUN")
            terminal = _read_lifecycle_blob(repo, remote, artifact_sha, terminal_path)
            correction = _read_lifecycle_blob(
                repo, remote, artifact_sha, ".ai/transport/repair.json"
            )
            if terminal is None:
                raise ReviewTransportError(
                    f"canonical {kind} content is missing for {run_id}"
                )
            candidate_sha = refs.get(candidate_ref)
            candidate_available = candidate_sha is not None
            if candidate_sha is None:
                if kind != "FAILURE":
                    raise ReviewTransportError(
                        f"canonical {kind} candidate ref is missing for {run_id}"
                    )
                failure_data = _json_mapping(terminal, "FAILURE")
                candidate_sha = failure_data.get("failed_head_sha")
                if not isinstance(candidate_sha, str) or not candidate_sha:
                    raise ReviewTransportError(
                        f"canonical FAILURE has no failed head for {run_id}"
                    )
            else:
                candidate_fetch, _, candidate_error = _git_cmd(
                    repo, "fetch", "--no-tags", remote, candidate_sha, allow_fail=True
                )
                if candidate_fetch:
                    raise RemoteQueryError(
                        f"failed to acquire canonical candidate for {run_id}",
                        category=_classify_remote_failure(candidate_error),
                    )
            terminals.append(
                RemoteLifecycleTerminal(
                    run_id, kind, candidate_sha, run, terminal, correction,
                    candidate_available,
                    _read_lifecycle_blob(repo, remote, artifact_sha,
                                         ".ai/transport/publication-recovery.json")
                    or (admitted[1] if admitted is not None else None),
                )
            )

        reviews: list[RemoteLifecycleReview] = []
        decision_prefix = "refs/heads/aios/review-decision/"
        current_run_ids = {item.run_id for item in terminals}
        for ref, decision_sha in sorted(refs.items()):
            if not ref.startswith(decision_prefix):
                continue
            run_id = ref[len(decision_prefix) :]
            if run_id not in current_run_ids:
                continue
            decision_fetch, _, decision_error = _git_cmd(
                repo, "fetch", "--no-tags", remote, decision_sha, allow_fail=True
            )
            if decision_fetch:
                raise RemoteQueryError(
                    "failed to acquire canonical review decision",
                    category=_classify_remote_failure(decision_error),
                )
            code, tree, _ = _git_cmd(
                repo,
                "ls-tree",
                "-r",
                "--name-only",
                decision_sha,
                "--",
                ".ai/reviews",
                allow_fail=True,
            )
            paths = [
                path
                for path in tree.splitlines()
                if path.startswith(".ai/reviews/")
                and path.endswith((".yaml", ".yml"))
            ]
            if code or len(paths) != 1:
                raise ReviewTransportError(
                    f"canonical review decision is missing or ambiguous for {run_id}"
                )
            review = _read_lifecycle_blob(repo, remote, decision_sha, paths[0])
            if review is None:
                raise ReviewTransportError(
                    f"canonical review decision content is missing for {run_id}"
                )
            reviews.append(RemoteLifecycleReview(run_id, decision_sha, review))

        remediation_selectors: list[tuple[str, str, str]] = []
        remediation_prefix = "refs/heads/aios/remediation/"
        run_pattern = re.compile(
            rf"^({re.escape(task_prefix)}\d{{3,}})-(.+)$"
        )
        repair_candidates: dict[str, list[RemoteRepairAuthorization]] = {}
        for ref, sha in sorted(refs.items()):
            if ref.startswith(remediation_prefix):
                match = run_pattern.fullmatch(ref[len(remediation_prefix) :])
                if match is None:
                    raise ReviewTransportError(
                        "canonical REMEDIATION selector identity is malformed"
                    )
                remediation_selectors.append((match.group(1), match.group(2), sha))
            elif ref.startswith("refs/heads/aios/repair/"):
                run_id = ref.rsplit("/", 1)[-1]
                if not re.fullmatch(rf"{re.escape(task_prefix)}\d{{3,}}", run_id):
                    raise ReviewTransportError(
                        "canonical REPAIR selector identity is malformed"
                    )
                repair = _read_lifecycle_blob(
                    repo, remote, sha, ".ai/transport/repair.json"
                )
                if repair is None:
                    raise ReviewTransportError(
                        f"canonical REPAIR content is missing for {run_id}"
                    )
                repair_candidates.setdefault(run_id, []).append(
                    RemoteRepairAuthorization(run_id, ref, sha, 1, repair)
                )
            elif ref.startswith(REPAIR_SUPERSESSION_PREFIX):
                suffix = ref[len(REPAIR_SUPERSESSION_PREFIX) :]
                parts = suffix.split("/")
                if (
                    len(parts) != 2
                    or not re.fullmatch(rf"{re.escape(task_prefix)}\d{{3,}}", parts[0])
                    or not parts[1].isdigit()
                ):
                    raise ReviewTransportError(
                        "canonical REPAIR supersession selector identity is malformed"
                    )
                run_id = parts[0]
                revision = int(parts[1])
                repair = _read_lifecycle_blob(
                    repo, remote, sha, ".ai/transport/repair.json"
                )
                metadata = _read_lifecycle_blob(
                    repo, remote, sha, REPAIR_SUPERSESSION_PATH
                )
                if repair is None or metadata is None:
                    raise ReviewTransportError(
                        f"canonical REPAIR supersession content is missing for {run_id}"
                    )
                predecessor, failure_sha = _decode_repair_supersession(
                    metadata, run_id=run_id, revision=revision
                )
                parent_code, parent_sha, _ = _git_cmd(
                    repo, "rev-parse", f"{sha}^", allow_fail=True
                )
                if parent_code or parent_sha != predecessor:
                    raise ReviewTransportError(
                        "canonical REPAIR successor commit parent is invalid"
                    )
                repair_candidates.setdefault(run_id, []).append(
                    RemoteRepairAuthorization(
                        run_id, ref, sha, revision, repair, predecessor, failure_sha
                    )
                )
        repair_selectors: list[tuple[str, str, bytes]] = []
        for run_id, candidates in sorted(repair_candidates.items()):
            failure_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
            selected = _select_current_repair_authorization(
                candidates, failure_artifacts_sha=refs.get(failure_ref)
            )
            repair_selectors.append((run_id, selected.commit_sha, selected.repair))
        return RemoteTaskLifecycle(
            main_sha=main_sha,
            terminals=tuple(terminals),
            reviews=tuple(reviews),
            remediation_selectors=tuple(remediation_selectors),
            repair_selectors=tuple(repair_selectors),
            observed_refs=tuple(sorted(refs.items())),
        )
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(refs.items()))
        raise


def _remote_run_namespace_from_refs(
    repo: Path,
    remote: str,
    *,
    task_id: str,
    task_revision: int,
    task_prefix: str,
    refs: Mapping[str, str],
) -> RemoteRunNamespace:
    """Validate a TASK terminal namespace from an already-acquired ref mapping."""

    terminal_kinds: dict[str, set[str]] = {}
    run_pattern = re.compile(rf"^{re.escape(task_prefix)}\d{{3,}}$")
    for ref, artifact_sha in refs.items():
        if ref.startswith("refs/heads/aios/publication-recovery/"):
            run_bytes = _read_remote_blob(repo, remote, artifact_sha, ".ai/transport/run.json")
            if run_bytes is None:
                raise ReviewTransportError("publication admission has no persisted RUN")
            bound_task, _, admitted_id = _decode_run_task_identity(run_bytes, ref)
            if bound_task != task_id or not run_pattern.fullmatch(admitted_id):
                raise ReviewTransportError("publication admission RUN namespace mismatch")
            terminal_kinds.setdefault(admitted_id, set())
            continue
        if ref.startswith("refs/heads/aios/failure-artifacts/"):
            terminal_kind = "FAILURE"
        elif ref.startswith("refs/heads/aios/artifacts/"):
            terminal_kind = "RESULT"
        elif ref.startswith("refs/heads/aios/failure/"):
            continue
        else:
            raise ReviewTransportError("canonical RUN ref namespace mismatch")
        run_id = ref.rsplit("/", 1)[-1]
        if not run_pattern.fullmatch(run_id):
            raise ReviewTransportError(
                f"canonical RUN ref identity is invalid for {task_id}: {ref}"
            )
        run_bytes = _read_remote_blob(
            repo, remote, artifact_sha, ".ai/transport/run.json"
        )
        if run_bytes is None:
            raise ReviewTransportError(f"canonical RUN content missing at {ref}")
        bound_task_id, _bound_revision, bound_run_id = _decode_run_task_identity(
            run_bytes, ref
        )
        if bound_run_id != run_id:
            raise ReviewTransportError(
                f"canonical RUN id mismatch at {ref}: expected {run_id}, got {bound_run_id}"
            )
        if bound_task_id != task_id:
            raise ReviewTransportError(
                f"canonical RUN TASK identity mismatch at {ref}"
            )
        terminal_kinds.setdefault(run_id, set()).add(terminal_kind)
    conflicts = tuple(
        sorted(
            run_id
            for run_id, kinds in terminal_kinds.items()
            if kinds == {"FAILURE", "RESULT"}
        )
    )
    return RemoteRunNamespace(
        tuple(sorted(terminal_kinds)), conflicts, tuple(sorted(refs.items()))
    )


def resolve_remote_primary_recovery(
    repo: Path, *, run_id: str
) -> RemotePrimaryRecovery:
    """Resolve one exact conflicting PRIMARY terminal identity from remote state."""

    task_prefix = _run_task_prefix(run_id)
    if not re.fullmatch(rf"{re.escape(task_prefix)}\d{{3,}}", run_id):
        raise ReviewTransportError(f"invalid RUN id: {run_id!r}")
    remote = resolve_transport_remote(repo)
    failure_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    success_ref = f"refs/heads/aios/artifacts/{run_id}"
    candidate_ref = f"refs/heads/aios/review/{run_id}"
    refs = _exact_remote_refs(
        repo, remote, failure_ref, success_ref, candidate_ref
    )
    try:
        return _resolve_remote_primary_recovery_from_refs(
            repo,
            remote=remote,
            run_id=run_id,
            failure_ref=failure_ref,
            success_ref=success_ref,
            candidate_ref=candidate_ref,
            refs=refs,
        )
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(refs.items()))
        raise


def _resolve_remote_primary_recovery_from_refs(
    repo: Path,
    *,
    remote: str,
    run_id: str,
    failure_ref: str,
    success_ref: str,
    candidate_ref: str,
    refs: Mapping[str, str],
) -> RemotePrimaryRecovery:
    """Validate PRIMARY recovery from one exact observed ref set."""

    missing = [
        ref for ref in (failure_ref, success_ref, candidate_ref) if ref not in refs
    ]
    if missing:
        raise ReviewTransportError(
            f"canonical PRIMARY terminal conflict is incomplete for {run_id}"
        )

    failure_run = _read_remote_blob(
        repo, remote, refs[failure_ref], ".ai/transport/run.json"
    )
    failure = _read_remote_blob(
        repo, remote, refs[failure_ref], ".ai/transport/failure.json"
    )
    success_run = _read_remote_blob(
        repo, remote, refs[success_ref], ".ai/transport/run.json"
    )
    result = _read_remote_blob(
        repo, remote, refs[success_ref], ".ai/transport/result.json"
    )
    if None in (failure_run, failure, success_run, result):
        raise ReviewTransportError(
            f"canonical PRIMARY terminal conflict content missing for {run_id}"
        )
    assert failure_run is not None
    assert failure is not None
    assert success_run is not None
    assert result is not None
    if (
        _read_remote_blob(
            repo, remote, refs[failure_ref], ".ai/transport/repair.json"
        )
        is not None
        or _read_remote_blob(
            repo, remote, refs[success_ref], ".ai/transport/repair.json"
        )
        is not None
    ):
        raise ReviewTransportError(
            f"canonical terminal conflict is not a PRIMARY lineage: {run_id}"
        )

    failure_identity = _decode_run_task_identity(failure_run, failure_ref)
    success_identity = _decode_run_task_identity(success_run, success_ref)
    if failure_identity[2] != run_id or success_identity[2] != run_id:
        raise ReviewTransportError("canonical PRIMARY terminal RUN identity mismatch")
    if failure_identity[:2] != success_identity[:2]:
        raise ReviewTransportError("canonical PRIMARY terminal TASK identity mismatch")
    namespace = resolve_remote_run_namespace(
        repo,
        task_id=success_identity[0],
        task_revision=success_identity[1],
    )
    if run_id not in namespace.conflicts:
        raise ReviewTransportError(
            f"canonical PRIMARY terminal identity is not conflicting: {run_id}"
        )
    return RemotePrimaryRecovery(
        run_id=run_id,
        candidate_sha=refs[candidate_ref],
        success_run=success_run,
        result=result,
        failure_run=failure_run,
        failure=failure,
        remote_run_ids=namespace.run_ids,
        observed_refs=tuple(sorted(refs.items())),
    )


def _decode_run_task_identity(run_bytes: bytes, ref: str) -> tuple[str, int, str]:
    try:
        run_data = json.loads(run_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewTransportError(f"canonical RUN JSON invalid for {ref}: {exc}") from exc
    if not isinstance(run_data, Mapping):
        raise ReviewTransportError(f"canonical RUN at {ref} must be a mapping")
    if run_data.get("kind") == "REMEDIATION":
        execution = run_data.get("execution")
        if not isinstance(execution, Mapping):
            raise ReviewTransportError(
                f"canonical REMEDIATION execution at {ref} must be a mapping"
            )
        source_run = execution.get("run")
        if not isinstance(source_run, Mapping):
            raise ReviewTransportError(
                f"canonical REMEDIATION run at {ref} must be a mapping"
            )
        run_id = source_run.get("run_id")
        task_data = source_run.get("task")
    elif "kind" not in run_data:
        run_id = run_data.get("run_id")
        task_data = run_data.get("task")
    else:
        raise ReviewTransportError(f"unknown canonical RUN kind at {ref}")
    if not isinstance(run_id, str) or not run_id:
        raise ReviewTransportError(f"canonical RUN run_id at {ref} is invalid")
    if not isinstance(task_data, Mapping):
        raise ReviewTransportError(f"canonical RUN task at {ref} must be a mapping")
    task_id = task_data.get("id")
    revision = task_data.get("revision")
    if not isinstance(task_id, str) or not task_id:
        raise ReviewTransportError(f"canonical RUN task.id at {ref} is invalid")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ReviewTransportError(f"canonical RUN task.revision at {ref} is invalid")
    try:
        document_affinity(source_run if run_data.get("kind") == "REMEDIATION" else run_data)
    except ValueError as exc:
        raise ReviewTransportError("canonical RUN affinity is invalid") from exc
    return task_id, revision, run_id


def resolve_remote_remediation_lineages(
    repo: Path,
    *,
    finding_id: str,
    task_id: str | None = None,
    task_revision: int | None = None,
    source_run_id: str | None = None,
    remote: str | None = None,
) -> tuple[RemoteRemediationLineage, ...]:
    """Resolve all structurally complete remote lineages for one finding id.

    TASK binding and frozen-contract validation are deliberately performed by the
    operator, which then requires exactly one matching lineage.
    """

    exact_source_requested = source_run_id is not None
    if not finding_id or "/" in finding_id or "\\" in finding_id:
        raise ReviewTransportError(f"invalid finding id: {finding_id!r}")
    if task_revision is not None and (
        isinstance(task_revision, bool)
        or not isinstance(task_revision, int)
        or task_revision < 1
    ):
        raise ReviewTransportError("invalid TASK revision")
    if source_run_id is not None:
        try:
            source_prefix = _run_task_prefix(source_run_id)
        except ReviewTransportError as exc:
            raise ReviewTransportError("invalid source RUN id") from exc
        if not re.fullmatch(rf"{re.escape(source_prefix)}\d{{3,}}", source_run_id):
            raise ReviewTransportError("invalid source RUN id")
    remote = remote or resolve_transport_remote(repo)
    if source_run_id is not None:
        task_prefix = None
        pattern = f"refs/heads/aios/remediation/{source_run_id}-{finding_id}"
    elif task_id is not None:
        task_prefix = task_run_prefix(task_id)
        pattern = f"refs/heads/aios/remediation/{task_prefix}*-{finding_id}"
    else:
        task_prefix = None
        pattern = f"refs/heads/aios/remediation/*-{finding_id}"
    code, output, _ = _git_cmd(
        repo, "ls-remote", "--refs", remote, pattern, allow_fail=True
    )
    if code:
        raise RemoteQueryError("failed to query canonical REMEDIATION refs")

    observed_refs: dict[str, str] = {}
    try:
        observed_lines: list[tuple[str, str]] = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) != 2:
                raise ReviewTransportError("malformed canonical REMEDIATION ref result")
            commit_sha, ref = parts
            observed_refs[ref] = commit_sha
            observed_lines.append((commit_sha, ref))

        resolved: list[RemoteRemediationLineage] = []
        for commit_sha, ref in observed_lines:
            prefix = "refs/heads/aios/remediation/"
            suffix = f"-{finding_id}"
            if not ref.startswith(prefix) or not ref.endswith(suffix):
                raise ReviewTransportError("canonical REMEDIATION ref name mismatch")
            source_run_id = ref[len(prefix) : -len(suffix)]
            if not source_run_id:
                raise ReviewTransportError("canonical REMEDIATION ref has no source RUN")
            if task_prefix is not None and not source_run_id.startswith(task_prefix):
                continue

            artifacts_ref = f"refs/heads/aios/artifacts/{source_run_id}"
            if exact_source_requested:
                failure_ref = f"refs/heads/aios/failure-artifacts/{source_run_id}"
                failure_code, failure_output, _ = _git_cmd(
                    repo, "ls-remote", "--refs", remote, failure_ref, allow_fail=True
                )
                if failure_code:
                    raise RemoteQueryError(
                        f"failed to query canonical source state for {source_run_id}"
                    )
                failure_lines = [item.split() for item in failure_output.splitlines()]
                if failure_lines:
                    if any(len(item) != 2 for item in failure_lines):
                        raise ReviewTransportError(
                            f"canonical source state is malformed for {source_run_id}"
                        )
                    raise ReviewTransportError(
                        "canonical source RUN has conflicting terminal artifacts: "
                        f"{source_run_id}"
                    )
            artifacts_code, artifacts_output, _ = _git_cmd(
                repo, "ls-remote", "--refs", remote, artifacts_ref, allow_fail=True
            )
            artifact_lines = [item.split() for item in artifacts_output.splitlines()]
            if (
                artifacts_code
                or len(artifact_lines) != 1
                or len(artifact_lines[0]) != 2
            ):
                raise ReviewTransportError(
                    f"canonical source artifacts missing or ambiguous for {source_run_id}"
                )
            artifacts_sha = artifact_lines[0][0]
            run = _read_remote_blob(
                repo, remote, artifacts_sha, ".ai/transport/run.json"
            )
            result = _read_remote_blob(
                repo, remote, artifacts_sha, ".ai/transport/result.json"
            )
            repair = _read_remote_blob(
                repo, remote, artifacts_sha, ".ai/transport/repair.json"
            )
            if run is None or result is None:
                raise ReviewTransportError(f"canonical lineage content missing at {ref}")

            run_task_id, run_revision, run_id = _decode_run_task_identity(run, ref)
            if run_id != source_run_id:
                raise ReviewTransportError(
                    f"canonical RUN id mismatch at {ref}: expected {source_run_id}, "
                    f"got {run_id}"
                )
            if task_id is not None and run_task_id != task_id:
                continue
            if task_revision is not None and run_revision != task_revision:
                continue

            _git_cmd(repo, "fetch", "--no-tags", remote, commit_sha, allow_fail=True)
            tree_code, tree_output, _ = _git_cmd(
                repo,
                "ls-tree",
                "-r",
                "--name-only",
                commit_sha,
                "--",
                ".ai/reviews",
                ".ai/remediations",
                allow_fail=True,
            )
            if tree_code:
                raise ReviewTransportError(f"cannot inspect canonical lineage at {ref}")
            review_paths = [
                path
                for path in tree_output.splitlines()
                if path.startswith(".ai/reviews/")
                and path.endswith((".yaml", ".yml"))
            ]
            remediation_paths = [
                path
                for path in tree_output.splitlines()
                if path.startswith(".ai/remediations/")
                and path.endswith((".yaml", ".yml"))
            ]
            if len(review_paths) != 1 or len(remediation_paths) != 1:
                raise ReviewTransportError(
                    f"canonical lineage at {ref} must contain exactly one REVIEW and "
                    "REMEDIATION"
                )
            review = _read_remote_blob(repo, remote, commit_sha, review_paths[0])
            remediation = _read_remote_blob(
                repo, remote, commit_sha, remediation_paths[0]
            )
            if None in (review, remediation):
                raise ReviewTransportError(f"canonical lineage content missing at {ref}")
            resolved.append(
                RemoteRemediationLineage(
                    ref=ref,
                    source_run_id=source_run_id,
                    review=review,
                    remediation=remediation,
                    run=run,
                    result=result,
                    repair=repair,
                    commit_sha=commit_sha,
                    task_id=run_task_id,
                    task_revision=run_revision,
                )
            )
        return tuple(resolved)
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(observed_refs.items()))
        raise


def resolve_remote_repair_recovery(
    repo: Path, *, failed_run_id: str
) -> RemoteRepairRecovery:
    """Resolve one failed correction chain entirely from canonical remote refs."""

    task_prefix = _run_task_prefix(failed_run_id)
    remote = resolve_transport_remote(repo)
    refs = _historical_repair_ref_snapshot(repo, remote, task_prefix)
    try:
        return _resolve_remote_repair_recovery_from_snapshot(
            repo,
            remote=remote,
            failed_run_id=failed_run_id,
            task_prefix=task_prefix,
            refs=refs,
        )
    except ReviewTransportError as exc:
        exc.observed_refs = tuple(sorted(refs.items()))
        raise


def _resolve_remote_repair_recovery_from_snapshot(
    repo: Path,
    *,
    remote: str,
    failed_run_id: str,
    task_prefix: str,
    refs: Mapping[str, str],
) -> RemoteRepairRecovery:
    """Reconstruct historical REPAIR state from one already-observed snapshot."""

    discovered_run_ids = _remote_run_ids_from_refs(refs, task_prefix)
    if failed_run_id not in discovered_run_ids:
        raise ReviewTransportError(
            f"canonical failed RUN not found: {failed_run_id}"
        )
    cache: dict[str, RemoteFailureArtifacts] = {}

    def read_failure(run_id: str) -> RemoteFailureArtifacts:
        if run_id in cache:
            return cache[run_id]
        if _run_task_prefix(run_id) != task_prefix:
            raise ReviewTransportError("correction lineage crosses TASK identity")
        artifacts_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
        candidate_ref = f"refs/heads/aios/failure/{run_id}"
        if artifacts_ref not in refs or candidate_ref not in refs:
            raise ReviewTransportError(
                f"canonical failed RUN refs missing for {run_id}"
            )
        artifacts_sha = refs[artifacts_ref]
        run = _read_remote_blob(repo, remote, artifacts_sha, ".ai/transport/run.json")
        failure = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/failure.json"
        )
        repair = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/repair.json"
        )
        preverification = _read_local_blob(
            repo,
            artifacts_sha,
            ".ai/transport/pre-verification-candidate.json",
        )
        execution_profile = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/execution-profile.json"
        )
        if run is None or failure is None:
            raise ReviewTransportError(
                f"canonical failed RUN content missing for {run_id}"
            )
        artifact = RemoteFailureArtifacts(
            run_id,
            refs[candidate_ref],
            run,
            failure,
            repair,
            preverification,
            execution_profile=execution_profile,
        )
        cache[run_id] = artifact
        return artifact

    chain: list[RemoteFailureArtifacts] = []
    seen: set[str] = set()
    current = failed_run_id
    while True:
        if current in seen:
            raise ReviewTransportError("cyclic failed RUN continuation lineage")
        seen.add(current)
        artifact = read_failure(current)
        chain.append(artifact)
        failure_data = _json_mapping(artifact.failure, "FAILURE")
        if failure_data.get("kind") != "FAILURE" or failure_data.get("run_id") != current:
            raise ReviewTransportError("canonical FAILURE identity mismatch")
        if failure_data.get("failed_head_sha") != artifact.candidate_sha:
            raise ReviewTransportError("canonical failed-head ref mismatch")
        continuation = failure_data.get("continuation_of")
        if continuation is None:
            break
        if not isinstance(continuation, str) or not continuation:
            raise ReviewTransportError("invalid FAILURE continuation_of")
        if artifact.repair is not None:
            repair_data = _json_mapping(artifact.repair, "REPAIR execution")
            if repair_data.get("failed_run_id") != continuation:
                raise ReviewTransportError("conflicting REPAIR execution lineage")
        current = continuation

    target_task_id, target_revision, target_run_id = _decode_run_task_identity(
        chain[0].run,
        f"refs/heads/aios/failure-artifacts/{failed_run_id}",
    )
    if target_run_id != failed_run_id:
        raise ReviewTransportError("canonical failed RUN identity mismatch")
    if task_run_prefix(target_task_id) != task_prefix:
        raise ReviewTransportError("canonical failed RUN TASK identity mismatch")
    namespace = _remote_run_namespace_from_refs(
        repo,
        remote,
        task_id=target_task_id,
        task_revision=target_revision,
        task_prefix=task_prefix,
        refs=refs,
    )
    if namespace.conflicts:
        raise ReviewTransportError(
            "canonical RUN has conflicting terminal artifacts: "
            + ", ".join(namespace.conflicts)
        )
    remote_run_ids = set(namespace.run_ids)

    continuations: dict[str, list[str]] = {}
    for run_id in remote_run_ids:
        failure_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
        artifact_ref = f"refs/heads/aios/artifacts/{run_id}"
        if failure_ref in refs and artifact_ref in refs:
            raise ReviewTransportError(
                f"canonical RUN has conflicting terminal artifacts: {run_id}"
            )
        selected = refs.get(failure_ref) or refs.get(artifact_ref)
        if selected is None:
            continue
        if failure_ref in refs:
            failure = _read_remote_blob(
                repo, remote, selected, ".ai/transport/failure.json"
            )
            if failure is None:
                raise ReviewTransportError(
                    f"canonical FAILURE content missing for {run_id}"
                )
            parent = _json_mapping(failure, "FAILURE").get("continuation_of")
            if isinstance(parent, str) and parent:
                continuations.setdefault(parent, []).append(run_id)
        repair = _read_remote_blob(repo, remote, selected, ".ai/transport/repair.json")
        if repair is None:
            continue
        parent = _json_mapping(repair, "REPAIR execution").get("failed_run_id")
        if isinstance(parent, str) and parent and run_id not in continuations.get(parent, []):
            continuations.setdefault(parent, []).append(run_id)
    duplicates = continuations.get(failed_run_id, [])
    if duplicates:
        raise ReviewTransportError(
            "canonical continuation already exists for failed RUN: "
            + ", ".join(sorted(duplicates))
        )
    return RemoteRepairRecovery(
        tuple(chain), tuple(sorted(remote_run_ids)), tuple(sorted(refs.items()))
    )


def read_remote_task(repo: Path, *, commit_sha: str, task_id: str) -> bytes:
    """Read an exact historical TASK without checking out its subject tree."""

    if not task_id or "/" in task_id or "\\" in task_id:
        raise ReviewTransportError(f"invalid TASK id: {task_id!r}")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit_sha):
        raise ReviewTransportError("invalid historical TASK commit SHA")
    remote = resolve_transport_remote(repo)
    content = _read_remote_blob(
        repo, remote, commit_sha, f".ai/tasks/{task_id}.yaml"
    )
    if content is None:
        raise ReviewTransportError(
            f"historical TASK not found at {commit_sha}: {task_id}"
        )
    return content


# Explicit prospective L1 reader only. No active lifecycle caller uses this API.
L1_SOURCE_MAX_BYTES = 1024 * 1024
L1_TASK_MAX_BYTES = 256 * 1024
L1_INTAKE_MAX_BYTES = 4 * 1024 * 1024
_L1_MAX_COMMITS = 4096
_L1_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)


@dataclass(frozen=True)
class GitSemanticSource:
    """Exact caller locators; authority is established only by the reader."""

    source_sha: str
    tree_sha: str
    path: str
    blob_sha: str


@dataclass(frozen=True)
class AuthenticatedSemanticSource:
    role: str
    locator: GitSemanticSource
    content_sha256: str
    byte_count: int


@dataclass(frozen=True)
class CanonicalSemanticSourceBinding:
    """Bounded Git consistency observation, never approval or proof validity."""

    remote: str
    remote_url: str
    main_before: str
    main_after: str
    sources: tuple[AuthenticatedSemanticSource, ...]
    intake: SemanticProofIntake
    readiness: str = field(default="SOURCE_CONSISTENT_ONLY", init=False)
    source_authentication: str = field(default="ESTABLISHED_FOR_OBSERVATION", init=False)
    source_currentness: str = field(default="ESTABLISHED_FOR_OBSERVATION", init=False)
    unestablished: tuple[str, ...] = field(default=(
        "independent_human_approval", "independent_semantic_mapping_review",
        "semantic_adequacy", "canonical_runtime_verification", "evidence_applicability",
        "future_version_policy_admission", "production_opt_in",
    ), init=False)
    canonical_verification: str = field(default="UNESTABLISHED", init=False)
    production_eligible: bool = field(default=False, init=False)
    execution_authorized: bool = field(default=False, init=False)
    ka01: str = field(default="NOT_ACTIVATED", init=False)


def _l1_sha(value: object) -> str:
    if type(value) is not str or _L1_SHA.fullmatch(value) is None:
        raise ReviewTransportError("L1 requires exact lowercase SHA-1 object identities")
    return value


def _l1_git(repo: Path, *args: str, strip: bool = True) -> str:
    # Raw objects, never replace refs; disable incidental fetch maintenance.
    return _git_cmd(repo, "--no-replace-objects", "-c", "gc.auto=0",
                    "-c", "fetch.writeCommitGraph=false", "-c", "protocol.ext.allow=never",
                    *args, strip=strip)[1]


def _l1_remote(repo: Path, expected_url: str) -> str:
    code, _, _ = _git_cmd(repo, "symbolic-ref", "--quiet", "HEAD", allow_fail=True)
    remote = (resolve_transport_remote(repo) if code == 0 else
              resolve_detached_observation_remote(repo))
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", remote) is None:
        raise ReviewTransportError("missing or ambiguous L1 configured remote")
    if code == 0:
        branch = _git_cmd(repo, "symbolic-ref", "--quiet", "--short", "HEAD")[1]
        if _git_cmd(repo, "config", "--get-all", f"branch.{branch}.remote")[1] != remote:
            raise ReviewTransportError("ambiguous L1 branch upstream binding")
    # Both raw and effective URLs must be unique and exact. URL rewriting,
    # multiple URLs and local-branch '.' upstreams cannot change the trust root.
    raw = _git_cmd(repo, "config", "--get-all", f"remote.{remote}.url", strip=False)[1]
    effective = _git_cmd(repo, "remote", "get-url", "--all", remote, strip=False)[1]
    if raw != expected_url + "\n" or effective != expected_url + "\n":
        raise ReviewTransportError("wrong or ambiguous L1 canonical remote URL")
    return remote


def _l1_main(repo: Path, url: str, expected: str) -> None:
    if _exact_remote_refs(repo, url, "refs/heads/main") != {"refs/heads/main": expected}:
        raise ReviewTransportError("missing, conflicting or moved L1 canonical main")


def _l1_json(content: bytes, limit: int) -> dict[str, Any]:
    if type(content) is not bytes or not 0 < len(content) <= limit:
        raise ReviewTransportError("L1 JSON input exceeds byte bounds")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("non-finite JSON number")

    try:
        text = content.decode("utf-8", errors="strict")
        # Bound parser recursion before decoding, ignoring quoted delimiters.
        depth, quoted, escaped, tokens = 0, False, False, 0
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                tokens += 1
                if depth > 32 or tokens > 65536:
                    raise ValueError("JSON structure exceeds bounds")
            elif char in "]}":
                depth -= 1
        data = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        pending, nodes = [data], 0
        while pending:
            item = pending.pop()
            nodes += 1
            if nodes > 65536:
                raise ValueError("JSON node bound exceeded")
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, str):
                item.encode("utf-8", errors="strict")  # Reject lone surrogates.
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("non-finite JSON number")
        if type(data) is not dict:
            raise ValueError("JSON object required")
        return data
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReviewTransportError("invalid bounded strict L1 JSON document") from exc


def _l1_text_object(repo: Path, sha: str, kind: str, limit: int) -> bytes:
    if _l1_git(repo, "cat-file", "-t", sha) != kind:
        raise ReviewTransportError("wrong L1 Git object type")
    size = _l1_git(repo, "cat-file", "-s", sha)
    if not size.isdecimal() or not 0 < int(size) <= limit:
        raise ReviewTransportError("L1 Git object exceeds byte bounds")
    content = _l1_git(repo, "cat-file", kind, sha, strip=False).encode("utf-8")
    header = f"{kind} {len(content)}\0".encode("ascii")
    if len(content) != int(size) or hashlib.sha1(header + content).hexdigest() != sha:
        raise ReviewTransportError("L1 Git content/object identity mismatch")
    return content


def _l1_commit(repo: Path, sha: str) -> tuple[str, tuple[str, ...]]:
    content = _l1_text_object(repo, sha, "commit", 64 * 1024)
    header, separator, _ = content.partition(b"\n\n")
    # Git recognizes LF and contiguous parent headers immediately after tree.
    # Unicode/splitlines separators or later author/signature text cannot add
    # an ancestor that Git's native commit parser would not recognize.
    headers = header.split(b"\n")
    if not separator or not headers[0].startswith(b"tree "):
        raise ReviewTransportError("invalid bounded L1 commit headers")
    index, parents = 1, []
    while index < len(headers) and headers[index].startswith(b"parent "):
        parents.append(_l1_sha(headers[index][7:].decode("utf-8")))
        index += 1
    if len(parents) > 256 or any(line.startswith((b"tree ", b"parent ")) for line in headers[index:]):
        raise ReviewTransportError("invalid bounded L1 commit parent headers")
    return _l1_sha(headers[0][5:].decode("utf-8")), tuple(parents)


def _l1_anchor_sources(repo: Path, main: str, wanted: set[str]) -> dict[str, str]:
    # Walk authenticated raw parent identities. merge-base/rev-list can honor
    # local grafts or shallow metadata, which cannot establish source authority.
    pending, seen, trees = [main], set(), {}
    while pending and not wanted <= trees.keys():
        sha = pending.pop()
        if sha in seen:
            continue
        if len(seen) >= _L1_MAX_COMMITS:
            raise ReviewTransportError("L1 source ancestry exceeds commit bound")
        seen.add(sha)
        tree, parents = _l1_commit(repo, sha)
        trees[sha] = tree
        pending.extend(reversed(parents))
    if not wanted <= trees.keys():
        raise ReviewTransportError("L1 source commit is not anchored in canonical main")
    return trees


def _l1_tree(repo: Path, sha: str) -> dict[str, tuple[str, str, str]]:
    if _l1_git(repo, "cat-file", "-t", sha) != "tree":
        raise ReviewTransportError("wrong L1 tree object type")
    size = _l1_git(repo, "cat-file", "-s", sha)
    if not size.isdecimal() or not 0 < int(size) <= L1_SOURCE_MAX_BYTES:
        raise ReviewTransportError("L1 Git tree exceeds byte bounds")
    listing = _l1_git(repo, "ls-tree", "-z", sha, strip=False)
    if not listing.endswith("\0"):
        raise ReviewTransportError("invalid L1 Git tree listing")
    entries, raw = {}, bytearray()
    for entry in listing[:-1].split("\0"):
        metadata, separator, name = entry.partition("\t")
        fields = metadata.split(" ")
        if (not separator or not name or name in entries or "/" in name
                or name in {".", ".."} or len(fields) != 3):
            raise ReviewTransportError("ambiguous L1 Git tree entry")
        mode, kind, oid = fields
        if (mode, kind) not in {("040000", "tree"), ("100644", "blob"),
                                ("100755", "blob"), ("120000", "blob"), ("160000", "commit")}:
            raise ReviewTransportError("invalid L1 Git tree entry type")
        _l1_sha(oid)
        raw.extend(mode.lstrip("0").encode("ascii") + b" " + name.encode("utf-8")
                   + b"\0" + bytes.fromhex(oid))
        entries[name] = (mode, kind, oid)
    header = f"tree {len(raw)}\0".encode("ascii")
    if len(raw) != int(size) or hashlib.sha1(header + raw).hexdigest() != sha:
        raise ReviewTransportError("L1 Git tree content/object identity mismatch")
    return entries


def _l1_path_blob(repo: Path, tree: str, path: str,
                  cache: dict[str, dict[str, tuple[str, str, str]]], *,
                  missing_ok: bool = False) -> str | None:
    components = path.split("/")
    for index, component in enumerate(components):
        if tree not in cache:
            cache[tree] = _l1_tree(repo, tree)
        if component not in cache[tree]:
            if missing_ok:
                return None
            raise ReviewTransportError("missing L1 source path")
        mode, kind, sha = cache[tree][component]
        if index < len(components) - 1:
            if (mode, kind) != ("040000", "tree"):
                raise ReviewTransportError("L1 source path traverses a non-tree")
            tree = sha
        elif mode not in {"100644", "100755"} or kind != "blob":
            raise ReviewTransportError("L1 source must be a regular Git blob")
    return sha


def _l1_yaml_text(content: bytes) -> str:
    import yaml

    # Shared bounded lexical guard; the existing TASK/REVIEW parsers still own
    # their schemas. Reject graphs, tags and excessive nesting before parsing.
    if type(content) is not bytes or not 0 < len(content) <= L1_TASK_MAX_BYTES:
        raise ReviewTransportError("L1 YAML input exceeds byte bounds")
    try:
        text = content.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            raise ValueError("YAML BOM is not accepted")
        depth = 0
        for count, token in enumerate(yaml.scan(text)):
            if count >= 65536 or isinstance(token, (
                    yaml.tokens.AliasToken, yaml.tokens.AnchorToken,
                    yaml.tokens.TagToken, yaml.tokens.DirectiveToken)):
                raise ValueError("unbounded or ambiguous L1 YAML")
            if isinstance(token, (yaml.tokens.BlockMappingStartToken,
                                  yaml.tokens.BlockSequenceStartToken,
                                  yaml.tokens.FlowMappingStartToken, yaml.tokens.FlowSequenceStartToken)):
                depth += 1
            elif isinstance(token, (yaml.tokens.BlockEndToken, yaml.tokens.FlowMappingEndToken,
                                    yaml.tokens.FlowSequenceEndToken)):
                depth -= 1
            if depth > 32:
                raise ValueError("YAML nesting exceeds bound")
        return text
    except (ValueError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise ReviewTransportError("invalid bounded canonical L1 YAML document") from exc


def _l1_task_binding(content: bytes) -> dict[str, Any]:
    from .task import parse_task

    try:
        task = parse_task(_l1_yaml_text(content))
    except ValueError as exc:
        raise ReviewTransportError("invalid bounded canonical L1 TASK document") from exc
    return dict(task_id=task.task_id, revision=task.revision,
                envelope_digest=hashlib.sha256(content).hexdigest(),
                acceptance_ids=sorted(item.id for item in task.acceptance))


def _l1_locator(locator: GitSemanticSource) -> None:
    if type(locator) is not GitSemanticSource:
        raise ReviewTransportError("typed exact L1 source locator required")
    for sha in (locator.source_sha, locator.tree_sha, locator.blob_sha):
        _l1_sha(sha)
    if (type(locator.path) is not str or len(locator.path) > 512
            or re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", locator.path) is None
            or len(locator.path.split("/")) > 32
            or any(part in {".", "..", ".git"} for part in locator.path.split("/"))):
        raise ReviewTransportError("exact normalized L1 repository-relative path required")


def bind_canonical_semantic_proof_sources(
    repo: Path, declaration: bytes, *, expected_remote_url: str,
    expected_main_sha: str, expected_candidate_sha: str,
    sources: Mapping[str, GitSemanticSource],
) -> CanonicalSemanticSourceBinding:
    """Explicit read-only L1 source binding for TASK-376; no production admission.

    The expected URL/main/candidate and all three exact locators are mandatory.
    Only configured canonical main history supplies source authority. Source
    paths must still have their selected blob at current main. Raw TASK bytes
    define envelope_digest; contract/mapping pins use VP-02 normalization.
    Fetch adds immutable objects only, with no ref, FETCH_HEAD or index writes.
    Raises ReviewTransportError on any unavailable, stale or conflicting input.
    """
    return _l1_bind_semantic_sources(
        repo, declaration, expected_remote_url=expected_remote_url,
        expected_main_sha=expected_main_sha, expected_candidate_sha=expected_candidate_sha,
        sources=sources, prospective=False)[0]


def _l1_bind_semantic_sources(
    repo: Path, declaration: bytes, *, expected_remote_url: str,
    expected_main_sha: str, expected_candidate_sha: str,
    sources: Mapping[str, GitSemanticSource], prospective: bool,
):
    # Only the explicit prospective readiness entry point selects this mode.
    # Legacy readers always retain their original TASK decoder.
    from .proof_coverage_contract import (
        ProofCoverageError, proof_mapping_digest, semantic_requirement, validate_proof_contract,
    )
    from .task import parse_semantic_task, validate_semantic_proof_intake

    _l1_sha(expected_main_sha)
    _l1_sha(expected_candidate_sha)
    if (type(expected_remote_url) is not str or not 0 < len(expected_remote_url) <= 2048
            or expected_remote_url != expected_remote_url.strip()
            or expected_remote_url.startswith("-")
            or "::" in expected_remote_url
            or any(ord(char) < 32 or ord(char) == 127 for char in expected_remote_url)):
        raise ReviewTransportError("exact bounded L1 remote URL required")
    incoming = _l1_json(declaration, L1_INTAKE_MAX_BYTES)
    if incoming.get("schema") != "task-semantic-proof-intake-v1":
        raise ReviewTransportError("explicit task-semantic-proof-intake-v1 declaration required")
    if type(sources) is not dict or set(sources) != {"task", "contract", "mapping"}:
        raise ReviewTransportError("exact task/contract/mapping L1 locators required")
    locators = dict(sources)
    for locator in locators.values():
        _l1_locator(locator)
    if len({item.path for item in locators.values()}) != 3:
        raise ReviewTransportError("separate L1 semantic source paths required")
    remote = _l1_remote(repo, expected_remote_url)
    _l1_main(repo, expected_remote_url, expected_main_sha)
    if _l1_git(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise ReviewTransportError("L1 requires a complete Git object history")
    _l1_git(repo, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=",
            "--no-auto-maintenance", "--no-recurse-submodules", expected_remote_url, expected_main_sha)
    trees = _l1_anchor_sources(repo, expected_main_sha,
                               {item.source_sha for item in locators.values()})
    contents, observations, pins, tree_cache = {}, [], {}, {}
    for role in ("task", "contract", "mapping"):
        locator = locators[role]
        if trees[locator.source_sha] != locator.tree_sha:
            raise ReviewTransportError("L1 source commit/tree identity mismatch")
        if (_l1_path_blob(repo, locator.tree_sha, locator.path, tree_cache) != locator.blob_sha
                or _l1_path_blob(repo, trees[expected_main_sha], locator.path, tree_cache) != locator.blob_sha):
            raise ReviewTransportError("wrong or stale L1 source path/blob identity")
        content = _l1_text_object(repo, locator.blob_sha, "blob",
                                  L1_TASK_MAX_BYTES if role == "task" else L1_SOURCE_MAX_BYTES)
        contents[role] = content
        pins[role] = dict(source_sha=locator.source_sha, blob_sha=locator.blob_sha)
        observations.append(AuthenticatedSemanticSource(
            role, locator, hashlib.sha256(content).hexdigest(), len(content)))
    semantic_task = None
    if prospective:
        try:
            semantic_task = parse_semantic_task(contents["task"])
        except ValueError as exc:
            raise ReviewTransportError("invalid authenticated prospective semantic TASK") from exc
        task_binding = dict(task_id=semantic_task.task_id, revision=semantic_task.revision,
                            envelope_digest=hashlib.sha256(contents["task"]).hexdigest(),
                            acceptance_ids=[item.id for item in semantic_task.acceptance])
    else:
        task_binding = _l1_task_binding(contents["task"])
    if locators["task"].path != f".ai/tasks/{task_binding['task_id']}.yaml":
        raise ReviewTransportError("L1 TASK identity/path mismatch")
    contract = _l1_json(contents["contract"], L1_SOURCE_MAX_BYTES)
    mapping = _l1_json(contents["mapping"], L1_SOURCE_MAX_BYTES)
    try:
        parsed = validate_proof_contract(contract, expected_task=task_binding)
        if semantic_task is not None and semantic_task.proof_obligations != tuple(
                semantic_requirement(item) for item in parsed.obligations):
            raise ProofCoverageError("prospective TASK requirements differ from authenticated contract")
        contract_pin = dict(id=parsed.id, revision=parsed.revision, digest=parsed.digest)
        mapping_pin = dict(id=mapping.get("id"), revision=mapping.get("revision"),
                           digest=proof_mapping_digest(mapping))
        expected = dict(expected_task=task_binding, expected_candidate_sha=expected_candidate_sha,
                        expected_sources=pins, expected_contract=contract_pin,
                        expected_mapping=mapping_pin)
        # Validate the actual authenticated documents, then the caller's exact
        # prospective declaration against the same independently derived pins.
        validate_semantic_proof_intake(dict(
            schema="task-semantic-proof-intake-v1", task=task_binding,
            candidate_sha=expected_candidate_sha, sources=pins, contract=contract,
            mapping=mapping), **expected)
        intake = validate_semantic_proof_intake(incoming, **expected)
    except ProofCoverageError as exc:
        raise ReviewTransportError("L1 authenticated semantic intake is inconsistent") from exc
    if _l1_remote(repo, expected_remote_url) != remote:
        raise ReviewTransportError("L1 configured canonical remote moved during observation")
    _l1_main(repo, expected_remote_url, expected_main_sha)
    return (CanonicalSemanticSourceBinding(remote, expected_remote_url, expected_main_sha,
                                           expected_main_sha, tuple(observations), intake), semantic_task)


@dataclass(frozen=True)
class _L1MappingReviewSelection:
    """Private expected facts from independently authenticated Human context.

    Neither construction of this type nor Human intent establishes review PASS.
    Only the authenticated immutable selection reader supplies production facts.
    """

    scope: MappingReviewScope
    sources: tuple[AuthenticatedSemanticSource, ...]
    review_task_source: GitSemanticSource
    run_id: str
    run_sha256: str
    base_sha: str
    reviewed_sha: str
    review_id: str
    decision_sha: str
    artifacts_sha: str
    published_sha: str
    human_selection_sha: str | None = None
    human_event_identity: str | None = None


@dataclass(frozen=True)
class MappingReviewLineage:
    scope: MappingReviewScope
    review_task_source: AuthenticatedSemanticSource
    run_id: str
    run_sha256: str
    base_sha: str
    reviewed_sha: str
    review_id: str
    review_sha256: str
    result_sha256: str
    decision_sha: str
    artifacts_sha: str
    published_sha: str
    observed_refs: tuple[tuple[str, str], ...]
    human_selection_sha: str | None = None
    human_event_identity: str | None = None


@dataclass(frozen=True)
class AuthenticatedHumanReviewSelection:
    """Authenticated intent only, independent of Reviewer/Publisher outcome."""

    selection_sha: str
    event_identity: str


@dataclass(frozen=True)
class SemanticReviewAuthorityEligibility:
    """One immutable observation; no independent judgment or proof is issued."""

    source_binding: CanonicalSemanticSourceBinding
    lineage: MappingReviewLineage | None
    reason: Literal["EXACT_MAPPING_REVIEW_LINEAGE", "HUMAN_MAPPING_SELECTION_UNAVAILABLE",
                    "MAPPING_REVIEW_NOT_ESTABLISHED"]
    human_selection: AuthenticatedHumanReviewSelection | None = None
    source_status: Literal["SOURCE_CONSISTENT"] = field(default="SOURCE_CONSISTENT", init=False)
    semantic_adequacy: Literal["REQUIRES_INDEPENDENT_REVIEWER_JUDGMENT"] = field(
        default="REQUIRES_INDEPENDENT_REVIEWER_JUDGMENT", init=False)
    canonical_verification: Literal["UNESTABLISHED"] = field(default="UNESTABLISHED", init=False)
    evidence_applicability: Literal["UNESTABLISHED"] = field(default="UNESTABLISHED", init=False)
    future_version_admission: Literal["NOT_ADMITTED"] = field(default="NOT_ADMITTED", init=False)
    ka01_g3: Literal["NOT_ESTABLISHED"] = field(default="NOT_ESTABLISHED", init=False)
    ka01_g6: Literal["NOT_ESTABLISHED"] = field(default="NOT_ESTABLISHED", init=False)
    production_opt_in: Literal["NOT_ESTABLISHED"] = field(default="NOT_ESTABLISHED", init=False)
    production_activation: Literal["NOT_ACTIVATED"] = field(default="NOT_ACTIVATED", init=False)
    production_eligible: Literal[False] = field(default=False, init=False)
    execution_authorized: Literal[False] = field(default=False, init=False)

    @property
    def human_intent(self) -> Literal["AUTHENTICATED_SELECTION", "NOT_ESTABLISHED"]:
        return "AUTHENTICATED_SELECTION" if self.human_selection is not None else "NOT_ESTABLISHED"

    @property
    def reviewer_authority(self) -> Literal["REVIEW_AUTHORITY_ESTABLISHED", "NOT_ESTABLISHED"]:
        return "REVIEW_AUTHORITY_ESTABLISHED" if self.lineage is not None else "NOT_ESTABLISHED"


def _l1_authenticated_mapping_review_selection(
    repo: Path, binding: CanonicalSemanticSourceBinding,
) -> _L1MappingReviewSelection | AuthenticatedHumanReviewSelection | None:
    """Read exactly one Runtime selection and independently reauthenticate intent.

    The ref is derived only from the authenticated target/candidate. No public
    selector, callback, mapping label, actor record or alternative provider exists.
    """
    from .github_issue_ingress import _authenticate_selection_issue, GitHubIssueIngressError

    ref = _l1_selection_ref(binding.intake.coverage.contract.task.task_id, binding.intake.candidate_sha)
    refs = _exact_remote_refs(repo, binding.remote_url, ref, ref + "/*")
    if not refs:
        return None
    if set(refs) != {ref}:
        raise ReviewTransportError("ambiguous L1 Human selection")
    sha = _l1_sha(refs[ref])
    _l1_fetch(repo, binding.remote_url, sha)
    record = _l1_selection_record(repo, sha)
    request = _l1_selection_request(record["request"])
    selected_sources = _l1_validate_selection_request(repo, binding, request)
    policy, policy_pin = _l1_selection_policy(repo, binding.main_after)
    source_policy, original_pin = _l1_selection_policy(repo, request["expected_state"]["expected_main_sha"])
    if (record["policy"] != original_pin or policy_pin["blob_sha"] != original_pin["blob_sha"]
            or policy != source_policy or request["expected_state"]["expected_remote_url"] != binding.remote_url):
        raise ReviewTransportError("stale L1 Human selection policy or transport")
    provenance = record["provenance"]
    try:
        actual, body = _authenticate_selection_issue(policy, provenance.get("issue_number"))
    except GitHubIssueIngressError as exc:
        raise ReviewTransportError(str(exc)) from None
    if (json.dumps(actual, sort_keys=True) != json.dumps(provenance, sort_keys=True)
            or _l1_selection_request(_l1_json(body, L1_SELECTION_MAX_BYTES)) != request):
        raise ReviewTransportError("copied or conflicting L1 Human selection")
    human = AuthenticatedHumanReviewSelection(sha,
        f"github-issue:{actual['repository']}#{actual['issue_number']}@{actual['actor']}")
    if _exact_remote_refs(repo, binding.remote_url, ref, ref + "/*") != refs:
        raise ReviewTransportError("L1 Human selection moved")
    run = request["payload"]["run"]
    run_id = run["id"]
    review_refs = _exact_remote_refs(repo, binding.remote_url,
        f"refs/heads/aios/review/{run_id}", f"refs/heads/aios/artifacts/{run_id}",
        f"refs/heads/aios/review-decision/{run_id}")
    if len(review_refs) != 3 or review_refs.get(f"refs/heads/aios/review/{run_id}") != run["candidate_sha"]:
        return human
    decision = _l1_sha(review_refs[f"refs/heads/aios/review-decision/{run_id}"])
    _l1_fetch(repo, binding.remote_url, decision)
    decision_tree, _ = _l1_commit(repo, decision)
    reviews = _l1_subtree(repo, decision_tree, (".ai", "reviews"), {})
    if len(reviews) != 1:
        raise ReviewTransportError("ambiguous selected mapping REVIEW")
    name = next(iter(reviews))
    if not re.fullmatch(r"REVIEW-[A-Za-z0-9_-]{1,121}\.yaml", name):
        raise ReviewTransportError("invalid selected mapping REVIEW identity")
    if _exact_remote_refs(repo, binding.remote_url, ref, ref + "/*") != refs:
        raise ReviewTransportError("L1 Human selection moved")
    scope, sources, task_source = selected_sources
    return _L1MappingReviewSelection(scope, sources, task_source, run_id, run["sha256"],
        run["base_sha"], run["candidate_sha"], name[:-5], decision,
        review_refs[f"refs/heads/aios/artifacts/{run_id}"], run["candidate_sha"], sha,
        human.event_identity)


L1_SELECTION_MAX_BYTES = 131072
L1_SELECTION_PATH = ".ai/transport/l1-human-selection.json"
L1_SELECTION_PREFIX = "refs/heads/aios/l1-human-selection/"
L1_SELECTION_OPERATION = "SELECT_MAPPING_REVIEW"


def _l1_selection_ref(task_id: str, candidate: str) -> str:
    if type(task_id) is not str or not re.fullmatch(r"TASK-[A-Za-z0-9_-]{1,122}", task_id):
        raise ReviewTransportError("invalid L1 selection target TASK")
    return f"{L1_SELECTION_PREFIX}{task_id}/{_l1_sha(candidate)}"


def _l1_exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ReviewTransportError("missing or unknown L1 selection fields")
    return value


def _l1_selection_request(value: dict) -> dict:
    """One closed version-1 JSON contract; no lifecycle decisions or actor fields."""
    from .proof_coverage_contract import DeclarationPin, MappingReviewAcceptance, TaskBinding

    value = _l1_json(json.dumps(value, allow_nan=False).encode("utf-8"), L1_SELECTION_MAX_BYTES)
    _l1_exact(value, ("format", "version", "operation", "identity", "expected_state", "payload"))
    if (value["format"] != "AIOS_INGRESS_ENVELOPE" or type(value["version"]) is not int
            or value["version"] != 1 or value["operation"] != L1_SELECTION_OPERATION):
        raise ReviewTransportError("unsupported L1 Human selection contract")
    identity = _l1_exact(value["identity"], ("target_task_id",))
    state = _l1_exact(value["expected_state"], ("expected_main_sha", "expected_remote_url"))
    _l1_sha(state["expected_main_sha"])
    if type(state["expected_remote_url"]) is not str:
        raise ReviewTransportError("exact L1 selection canonical URL required")
    payload = _l1_exact(value["payload"], ("scope", "sources", "review_task_source", "run"))
    raw = _l1_exact(payload["scope"], ("version", "review_task", "target_task", "candidate_sha",
                                      "contract_pin", "mapping_pin", "acceptance"))

    def task(pin):
        _l1_exact(pin, ("task_id", "revision", "envelope_digest", "acceptance_ids"))
        if type(pin["acceptance_ids"]) is not list:
            raise ReviewTransportError("complete L1 selection acceptance required")
        return TaskBinding(**{**pin, "acceptance_ids": tuple(pin["acceptance_ids"])})

    for name in ("contract_pin", "mapping_pin"):
        _l1_exact(raw[name], ("id", "revision", "digest"))
    if type(raw["acceptance"]) is not list or not 0 < len(raw["acceptance"]) <= 256:
        raise ReviewTransportError("bounded L1 selection correspondence required")
    correspondence = []
    for item in raw["acceptance"]:
        _l1_exact(item, ("review_acceptance_id", "target_acceptance_ids", "obligation_ids"))
        if any(type(item[name]) is not list for name in ("target_acceptance_ids", "obligation_ids")):
            raise ReviewTransportError("exact L1 selection correspondence lists required")
        correspondence.append(MappingReviewAcceptance(item["review_acceptance_id"],
            tuple(item["target_acceptance_ids"]), tuple(item["obligation_ids"])))
    scope = MappingReviewScope(raw["version"], task(raw["review_task"]), task(raw["target_task"]),
        raw["candidate_sha"], DeclarationPin(**raw["contract_pin"]), DeclarationPin(**raw["mapping_pin"]),
        tuple(correspondence))
    _l1_selection_ref(identity["target_task_id"], scope.candidate_sha)
    if (identity["target_task_id"] != scope.target_task.task_id
            or scope.review_task.task_id == scope.target_task.task_id):
        raise ReviewTransportError("distinct exact L1 selection TASKs required")
    sources = _l1_exact(payload["sources"], ("task", "contract", "mapping"))
    for source in (*sources.values(), payload["review_task_source"]):
        _l1_exact(source, ("source_sha", "tree_sha", "path", "blob_sha"))
        _l1_locator(GitSemanticSource(**source))
    run = _l1_exact(payload["run"], ("id", "sha256", "base_sha", "candidate_sha"))
    if (type(run["id"]) is not str or len(run["id"]) > 128 or not re.fullmatch(
            re.escape(task_run_prefix(scope.review_task.task_id)) + r"\d{3,}", run["id"])
            or not _is_digest(run["sha256"])):
        raise ReviewTransportError("exact selected L1 mapping RUN required")
    _l1_sha(run["base_sha"])
    _l1_sha(run["candidate_sha"])
    return value


def _l1_request_scope(request: dict) -> MappingReviewScope:
    from .proof_coverage_contract import DeclarationPin, MappingReviewAcceptance, TaskBinding
    raw = request["payload"]["scope"]
    tasks = [TaskBinding(**{**raw[key], "acceptance_ids": tuple(raw[key]["acceptance_ids"])})
             for key in ("review_task", "target_task")]
    return MappingReviewScope(raw["version"], *tasks, raw["candidate_sha"],
        DeclarationPin(**raw["contract_pin"]), DeclarationPin(**raw["mapping_pin"]),
        tuple(MappingReviewAcceptance(item["review_acceptance_id"], tuple(item["target_acceptance_ids"]),
              tuple(item["obligation_ids"])) for item in raw["acceptance"]))


def _l1_fetch(repo: Path, url: str, sha: str) -> None:
    _l1_git(repo, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=",
            "--no-auto-maintenance", "--no-recurse-submodules", url, _l1_sha(sha))


def _l1_selection_policy(repo: Path, main: str):
    from .github_issue_ingress import _decode_policy, GitHubIssueIngressError
    tree, _ = _l1_commit(repo, main)
    blob = _l1_path_blob(repo, tree, ".ai/brain-ingress-carriers.yaml", {}, missing_ok=True)
    if blob is None:
        raise ReviewTransportError("canonical L1 Human selection policy unavailable")
    raw = _l1_text_object(repo, blob, "blob", 65536)
    try:
        policy = _decode_policy(raw)
    except GitHubIssueIngressError as exc:
        raise ReviewTransportError(str(exc)) from None
    return policy, dict(main_sha=main, blob_sha=blob, sha256=hashlib.sha256(raw).hexdigest())


def _l1_validate_selection_request(repo: Path, binding: CanonicalSemanticSourceBinding, request: dict):
    from .proof_coverage_contract import TaskBinding, validate_mapping_review_scope
    from .task import parse_task
    scope = _l1_request_scope(request)
    payload = request["payload"]
    if (tuple(source.locator for source in binding.sources) != tuple(
            GitSemanticSource(**payload["sources"][role]) for role in ("task", "contract", "mapping"))
            or request["expected_state"]["expected_remote_url"] != binding.remote_url):
        raise ReviewTransportError("L1 selection source/transport substitution")
    locator = GitSemanticSource(**payload["review_task_source"])
    run = payload["run"]
    trees = _l1_anchor_sources(repo, binding.main_after,
        {request["expected_state"]["expected_main_sha"], locator.source_sha, run["candidate_sha"]})
    reviewed = _l1_anchor_sources(repo, run["candidate_sha"], {run["base_sha"], locator.source_sha})
    cache = {}
    if (locator.tree_sha != trees[locator.source_sha]
            or locator.path != f".ai/tasks/{scope.review_task.task_id}.yaml"
            or any(_l1_path_blob(repo, tree, locator.path, cache) != locator.blob_sha
                   for tree in (locator.tree_sha, trees[binding.main_after], reviewed[run["base_sha"]],
                                trees[run["candidate_sha"]]))):
        raise ReviewTransportError("L1 selected mapping-review TASK is stale or substituted")
    raw = _l1_text_object(repo, locator.blob_sha, "blob", L1_TASK_MAX_BYTES)
    task_pin = _l1_task_binding(raw)
    task_pin["acceptance_ids"] = tuple(task_pin["acceptance_ids"])
    scope = validate_mapping_review_scope(scope, intake=binding.intake,
                                          expected_review_task=TaskBinding(**task_pin))
    task = parse_task(raw.decode("utf-8"))
    for source in binding.sources:
        if source.role == "task":
            continue
        path = source.locator.path
        if (path not in (*task.scope.inspect, *task.scope.modify)
                or _l1_path_blob(repo, trees[run["candidate_sha"]], path, cache) != source.locator.blob_sha):
            raise ReviewTransportError("L1 semantic source outside selected TASK scope/candidate")
        changed = _l1_path_blob(repo, reviewed[run["base_sha"]], path, cache,
                               missing_ok=True) != source.locator.blob_sha
        if changed and path not in task.scope.modify:
            raise ReviewTransportError("changed L1 semantic source requires selected TASK modify scope")
        if source.role == "mapping" and (not changed or path not in task.scope.modify
                or source.locator.source_sha != run["candidate_sha"]):
            raise ReviewTransportError("selected L1 mapping requires an actual authorized Git delta")
    return scope, binding.sources, locator


def _l1_selection_record(repo: Path, sha: str) -> dict:
    tree, parents = _l1_commit(repo, sha)
    if len(parents) != 1:
        raise ReviewTransportError("invalid L1 selection commit parent")
    parent_tree, _ = _l1_commit(repo, parents[0])
    cache = {}
    blob = _l1_path_blob(repo, tree, L1_SELECTION_PATH, cache)
    raw = _l1_text_object(repo, blob, "blob", L1_SELECTION_MAX_BYTES)
    record = _l1_json(raw, L1_SELECTION_MAX_BYTES)
    _l1_exact(record, ("format", "version", "request", "provenance", "policy"))
    if raw != json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"):
        raise ReviewTransportError("noncanonical immutable L1 selection record bytes")
    if (record["format"] != "AIOS_L1_HUMAN_REVIEW_SELECTION" or type(record["version"]) is not int
            or record["version"] != 1 or type(record["provenance"]) is not dict
            or parents != (record["request"]["expected_state"]["expected_main_sha"],)):
        raise ReviewTransportError("invalid immutable L1 selection record")

    def only_added(old_tree, new_tree, parts):
        old = dict(_l1_tree(repo, old_tree)) if old_tree else {}
        new = dict(_l1_tree(repo, new_tree))
        before, after = old.pop(parts[0], None), new.pop(parts[0], None)
        if old != new or after is None:
            raise ReviewTransportError("L1 selection commit changes unrelated source bytes")
        if len(parts) == 1:
            if before is not None or after != ("100644", "blob", blob):
                raise ReviewTransportError("L1 selection must add exactly one immutable record")
        else:
            if after[:2] != ("040000", "tree") or (before and before[:2] != ("040000", "tree")):
                raise ReviewTransportError("invalid L1 selection metadata tree")
            only_added(before[2] if before else None, after[2], parts[1:])

    only_added(parent_tree, tree, L1_SELECTION_PATH.split("/"))
    return record


def _l1_subtree(repo: Path, tree: str, parts: tuple[str, ...], cache: dict) -> dict:
    for part in parts:
        if tree not in cache:
            cache[tree] = _l1_tree(repo, tree)
        mode, kind, tree = cache[tree].get(part, (None, None, None))
        if (mode, kind) != ("040000", "tree"):
            raise ReviewTransportError("missing regular L1 lineage directory")
    if tree not in cache:
        cache[tree] = _l1_tree(repo, tree)
    return cache[tree]


def _l1_mapping_review_lineage(
    repo: Path, binding: CanonicalSemanticSourceBinding, selected: _L1MappingReviewSelection,
) -> MappingReviewLineage:
    from .artifacts import validate_evidence, validate_result, validate_result_package
    from .proof_coverage_contract import TaskBinding, validate_mapping_review_scope
    from .publication import _run_from_data
    from .review import parse_review, validate_review
    from .task import _TaskLoader, parse_task
    import yaml

    if type(selected) is not _L1MappingReviewSelection or type(selected.scope) is not MappingReviewScope:
        raise ReviewTransportError("unrecognized L1 mapping review authority source")
    validate_mapping_review_scope(selected.scope, intake=binding.intake,
                                  expected_review_task=selected.scope.review_task)
    if type(selected.sources) is not tuple or selected.sources != binding.sources:
        raise ReviewTransportError("L1 selected mapping source bytes/locators mismatch")
    _l1_locator(selected.review_task_source)
    for sha in (selected.base_sha, selected.reviewed_sha, selected.decision_sha,
                selected.artifacts_sha, selected.published_sha):
        _l1_sha(sha)
    review_task = selected.scope.review_task
    if (type(review_task) is not TaskBinding or type(selected.run_id) is not str
            or len(selected.run_id) > 128 or not re.fullmatch(
                re.escape(task_run_prefix(review_task.task_id)) + r"\d{3,}", selected.run_id)
            or type(selected.review_id) is not str
            or re.fullmatch(r"REVIEW-[A-Za-z0-9_-]{1,121}", selected.review_id) is None
            or type(selected.run_sha256) is not str or not _is_digest(selected.run_sha256)
            or selected.published_sha != selected.reviewed_sha):
        raise ReviewTransportError("invalid exact L1 mapping review selection pins")
    run_id = selected.run_id
    expected_refs = {
        "refs/heads/main": binding.main_after,
        f"refs/heads/aios/review/{run_id}": selected.reviewed_sha,
        f"refs/heads/aios/artifacts/{run_id}": selected.artifacts_sha,
        f"refs/heads/aios/review-decision/{run_id}": selected.decision_sha,
    }
    patterns = (*expected_refs, f"refs/heads/aios/failure/{run_id}",
                f"refs/heads/aios/failure-artifacts/{run_id}",
                f"refs/heads/aios/repair/{run_id}",
                f"{REPAIR_SUPERSESSION_PREFIX}{run_id}/*",
                f"refs/heads/aios/remediation/{run_id}-*",
                f"refs/heads/aios/publication-recovery/{run_id}")
    if _exact_remote_refs(repo, binding.remote_url, *patterns) != expected_refs:
        raise ReviewTransportError("missing, conflicting or moved L1 review/publication lineage")
    for sha in (selected.artifacts_sha, selected.decision_sha):
        _l1_git(repo, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=",
                "--no-auto-maintenance", "--no-recurse-submodules", binding.remote_url, sha)
    locator = selected.review_task_source
    trees = _l1_anchor_sources(repo, binding.main_after,
                               {selected.published_sha, locator.source_sha})
    candidate_tree = trees[selected.reviewed_sha]
    # Authenticate raw ancestry, including the RUN base, without graft-sensitive
    # graph convenience commands. The exact published candidate must contain it.
    reviewed_trees = _l1_anchor_sources(repo, selected.reviewed_sha,
        {selected.base_sha, locator.source_sha, *(source.locator.source_sha for source in binding.sources)})
    base_tree = reviewed_trees[selected.base_sha]
    cache = {}
    if (trees[locator.source_sha] != locator.tree_sha
            or locator.path != f".ai/tasks/{review_task.task_id}.yaml"
            or any(_l1_path_blob(repo, tree, locator.path, cache) != locator.blob_sha
                   for tree in (locator.tree_sha, base_tree, candidate_tree, trees[binding.main_after]))):
        raise ReviewTransportError("stale or swapped L1 mapping review TASK source")
    task_bytes = _l1_text_object(repo, locator.blob_sha, "blob", L1_TASK_MAX_BYTES)
    actual_task = _l1_task_binding(task_bytes)
    actual_task["acceptance_ids"] = tuple(actual_task["acceptance_ids"])
    scope = validate_mapping_review_scope(selected.scope, intake=binding.intake,
                                          expected_review_task=TaskBinding(**actual_task))
    task = parse_task(task_bytes.decode("utf-8"))
    semantic_paths, changed_semantic_paths = set(), set()
    for source in binding.sources:
        if source.role not in {"contract", "mapping"}:
            continue
        source_locator = source.locator
        semantic_paths.add(source_locator.path)
        if (source_locator.path not in (*task.scope.inspect, *task.scope.modify)
                or _l1_path_blob(repo, candidate_tree, source_locator.path, cache) != source_locator.blob_sha):
            raise ReviewTransportError("semantic source is outside the selected reviewed TASK scope")
        # A missing base path is a lawful addition; malformed trees still fail.
        changed = _l1_path_blob(repo, base_tree, source_locator.path, cache,
                                missing_ok=True) != source_locator.blob_sha
        if changed:
            if source_locator.path not in task.scope.modify:
                raise ReviewTransportError("changed semantic source is outside the selected reviewed TASK modification scope")
            changed_semantic_paths.add(source_locator.path)
        if source.role != "mapping":
            continue
        if (source_locator.source_sha != selected.reviewed_sha or source_locator.tree_sha != candidate_tree
                or source_locator.path not in task.scope.modify):
            raise ReviewTransportError("mapping source is outside the selected reviewed TASK delta")
        if not changed:
            raise ReviewTransportError("selected review RUN did not change the mapping source")

    artifact_tree, artifact_parents = _l1_commit(repo, selected.artifacts_sha)
    decision_tree, decision_parents = _l1_commit(repo, selected.decision_sha)
    artifact_names = _l1_subtree(repo, artifact_tree, (".ai", "transport"), cache)
    review_names = _l1_subtree(repo, decision_tree, (".ai", "reviews"), cache)
    if (artifact_parents or decision_parents != (selected.reviewed_sha,)
            or not {"run.json", "result.json"} <= artifact_names.keys()
            or not artifact_names.keys() <= {"run.json", "result.json", "observation.json", "execution-profile.json"}
            or set(review_names) != {selected.review_id + ".yaml"}):
        raise ReviewTransportError("unrecognized or incomplete existing L1 PRIMARY review lineage")
    for source_locator in (locator, *(source.locator for source in binding.sources)):
        if _l1_path_blob(repo, decision_tree, source_locator.path, cache) != source_locator.blob_sha:
            raise ReviewTransportError("L1 decision tree changes the selected reviewed semantic sources")

    def read(tree, path, limit):
        return _l1_text_object(repo, _l1_path_blob(repo, tree, path, cache), "blob", limit)

    run_bytes = read(artifact_tree, ".ai/transport/run.json", L1_TASK_MAX_BYTES)
    result_bytes = read(artifact_tree, ".ai/transport/result.json", L1_SOURCE_MAX_BYTES)
    review_bytes = read(decision_tree, f".ai/reviews/{selected.review_id}.yaml", L1_TASK_MAX_BYTES)
    run_data = _l1_json(run_bytes, L1_TASK_MAX_BYTES)
    if (set(run_data) - {"run_id", "task", "executor", "base_sha", "workspace", "head_sha",
                         "status", "return_affinity"}
            or run_data.get("task") != dict(id=scope.review_task.task_id, revision=scope.review_task.revision)
            or hashlib.sha256(run_bytes).hexdigest() != selected.run_sha256):
        raise ReviewTransportError("immutable L1 mapping review RUN source mismatch")
    run = _run_from_data(run_data, "L1 mapping review RUN")
    # Runtime preserves the admitted ACTIVE RUN bytes with a null head; the
    # canonical RESULT/EVIDENCE below bind the candidate to the exact selection.
    if (run.run_id != run_id or run.status != "ACTIVE" or run.base_sha != selected.base_sha
            or run.head_sha not in (None, selected.reviewed_sha) or run.return_affinity != task.return_affinity):
        raise ReviewTransportError("L1 mapping review RUN identity mismatch")
    package_data = _l1_json(result_bytes, L1_SOURCE_MAX_BYTES)
    if set(package_data) != {"result", "evidence"} or type(package_data["evidence"]) is not list:
        raise ReviewTransportError("invalid L1 canonical mapping review ResultPackage")
    result = validate_result(package_data["result"])
    evidence = tuple(validate_evidence(item) for item in package_data["evidence"])
    validate_result_package(task=task, run=run, result=result, evidence=evidence)
    if (result.head_sha != selected.reviewed_sha or result.unresolved
            or {ac for claim in result.claims for ac in claim.satisfies} != set(scope.review_task.acceptance_ids)
            or semantic_paths.intersection(result.changed_files) != changed_semantic_paths):
        raise ReviewTransportError("incomplete or contradictory L1 mapping review RESULT")
    text = _l1_yaml_text(review_bytes)
    # The historical REVIEW parser remains unchanged. This new boundary rejects
    # duplicate/unknown fields before passing bytes to that existing parser.
    review_data = yaml.load(text, Loader=_TaskLoader)
    if (type(review_data) is not dict
            or set(review_data) - {"review_id", "reviewed_sha", "mode", "verdict", "acceptance",
                                   "findings", "prior_finding_id"}):
        raise ReviewTransportError("unrecognized L1 mapping REVIEW source")
    review = parse_review(text)
    validate_review(task=task, result=result, review=review)
    if (review.review_id != selected.review_id or review.reviewed_sha != selected.reviewed_sha
            or review.verdict != "PASS" or review.mode != "PRIMARY" or review.prior_finding_id is not None
            or set(review.acceptance) != set(scope.review_task.acceptance_ids)
            or any(value != "PASS" for value in review.acceptance.values()) or review.findings):
        raise ReviewTransportError("full independent L1 mapping REVIEW PASS is absent")
    if _exact_remote_refs(repo, binding.remote_url, *patterns) != expected_refs:
        raise ReviewTransportError("L1 mapping review/publication subjects moved")
    return MappingReviewLineage(scope, AuthenticatedSemanticSource(
        "mapping_review_task", locator, hashlib.sha256(task_bytes).hexdigest(), len(task_bytes)),
        run_id, selected.run_sha256, selected.base_sha, selected.reviewed_sha, selected.review_id,
        hashlib.sha256(review_bytes).hexdigest(), hashlib.sha256(result_bytes).hexdigest(),
        selected.decision_sha, selected.artifacts_sha, selected.published_sha, tuple(sorted(expected_refs.items())),
        selected.human_selection_sha, selected.human_event_identity)


def bind_canonical_semantic_review_authority(
    repo: Path, declaration: bytes, *, expected_remote_url: str,
    expected_main_sha: str, expected_candidate_sha: str, sources: Mapping[str, GitSemanticSource],
) -> SemanticReviewAuthorityEligibility:
    """Explicit, read-only, non-operative L1 eligibility observation.

    Public inputs are TASK-377 source locators only. They cannot choose a review
    TASK, resolver, review ref, reviewer label or trust source. Source failures
    raise as before. Unavailable/unrecognized mapping-specific Human selection
    or existing REVIEW/Publisher lineage yields NOT_ESTABLISHED, never a search
    for a substitute TASK or PASS. No active admission path invokes this API.
    """
    binding = bind_canonical_semantic_proof_sources(
        repo, declaration, expected_remote_url=expected_remote_url, expected_main_sha=expected_main_sha,
        expected_candidate_sha=expected_candidate_sha, sources=sources)
    return _l1_bind_review_authority(repo, binding)


def _l1_bind_review_authority(
    repo: Path, binding: CanonicalSemanticSourceBinding,
) -> SemanticReviewAuthorityEligibility:
    from .proof_coverage_contract import ProofCoverageError
    import yaml

    lineage, human, selected, reason = None, None, None, "MAPPING_REVIEW_NOT_ESTABLISHED"
    try:
        selected = _l1_authenticated_mapping_review_selection(repo, binding)
        if selected is None:
            reason = "HUMAN_MAPPING_SELECTION_UNAVAILABLE"
        elif type(selected) is AuthenticatedHumanReviewSelection:
            human = selected
        else:
            if type(selected) is _L1MappingReviewSelection and selected.human_selection_sha is not None:
                human = AuthenticatedHumanReviewSelection(selected.human_selection_sha, selected.human_event_identity)
            lineage = _l1_mapping_review_lineage(repo, binding, selected)
            reason = "EXACT_MAPPING_REVIEW_LINEAGE"
    except (ReviewTransportError, ProofCoverageError, ValueError, TypeError, KeyError,
            UnicodeError, RecursionError, yaml.YAMLError):
        lineage, reason = None, "MAPPING_REVIEW_NOT_ESTABLISHED"
    if selected is not None:
        try:
            if _l1_authenticated_mapping_review_selection(repo, binding) != selected:
                raise ReviewTransportError("L1 Human mapping selection moved")
        except (ReviewTransportError, ProofCoverageError, ValueError, TypeError, KeyError,
                UnicodeError, RecursionError, yaml.YAMLError):
            lineage, human, reason = None, None, "MAPPING_REVIEW_NOT_ESTABLISHED"
    # Even a failed review observation must not retain successful currentness
    # if canonical main or the independently configured remote changed meanwhile.
    if _l1_remote(repo, binding.remote_url) != binding.remote:
        raise ReviewTransportError("L1 canonical remote moved during review observation")
    _l1_main(repo, binding.remote_url, binding.main_after)
    return SemanticReviewAuthorityEligibility(binding, lineage, reason, human)


@dataclass(frozen=True)
class SemanticTaskReadiness:
    """Prospective source/review diagnostics; never a runnable eligibility token."""

    task: "SemanticTask"
    authority: SemanticReviewAuthorityEligibility
    structure: Literal["STRUCTURALLY_COMPLETE"] = field(default="STRUCTURALLY_COMPLETE", init=False)
    canonical_verification: Literal["UNESTABLISHED"] = field(default="UNESTABLISHED", init=False)
    evidence_applicability: Literal["UNESTABLISHED"] = field(default="UNESTABLISHED", init=False)
    future_version_admission: Literal["NOT_ADMITTED"] = field(default="NOT_ADMITTED", init=False)
    ka01_g3: Literal["NOT_ESTABLISHED"] = field(default="NOT_ESTABLISHED", init=False)
    ka01_g6: Literal["NOT_ESTABLISHED"] = field(default="NOT_ESTABLISHED", init=False)
    production_opt_in: Literal["NOT_ESTABLISHED"] = field(default="NOT_ESTABLISHED", init=False)
    ka01: Literal["NOT_ACTIVATED"] = field(default="NOT_ACTIVATED", init=False)
    production_eligible: Literal[False] = field(default=False, init=False)
    execution_authorized: Literal[False] = field(default=False, init=False)

    @property
    def source_consistency(self) -> str:
        return self.authority.source_status

    @property
    def source_authentication(self) -> str:
        return self.authority.source_binding.source_authentication

    @property
    def source_currentness(self) -> str:
        return self.authority.source_binding.source_currentness

    @property
    def human_selection(self) -> str:
        return self.authority.human_intent

    @property
    def reviewer_authority(self) -> str:
        return self.authority.reviewer_authority

    @property
    def publisher_authority(self) -> str:
        return "EXACT_SOURCE_PUBLICATION_ESTABLISHED" if self.authority.lineage else "NOT_ESTABLISHED"

    @property
    def semantic_adequacy(self) -> str:
        return self.authority.semantic_adequacy


def read_semantic_task_readiness(
    repo: Path, declaration: bytes, *, expected_remote_url: str,
    expected_main_sha: str, expected_candidate_sha: str, sources: Mapping[str, GitSemanticSource],
) -> SemanticTaskReadiness:
    """Explicit read-only future-version source/selection/review observation.

    TASK bytes are read from immutable canonical Git objects, never supplied as
    a decoded authority assertion. TASK-376/377 own coverage and sources;
    TASK-378/380 own authenticated Human choice and Reviewer/Publisher lineage.
    No expected contract pin, review selector, resolver or authority callback is
    accepted. Invalid structure/sources raise; absent authority stays unestablished.
    Ordinary admission and historical readers never call this entry point.
    """
    binding, task = _l1_bind_semantic_sources(
        repo, declaration, expected_remote_url=expected_remote_url,
        expected_main_sha=expected_main_sha, expected_candidate_sha=expected_candidate_sha,
        sources=sources, prospective=True)
    return SemanticTaskReadiness(task, _l1_bind_review_authority(repo, binding))


def _run_task_prefix(run_id: str) -> str:
    stem, separator, sequence = run_id.rpartition("-")
    if (
        not separator
        or not stem.startswith("RUN-")
        or len(stem) <= len("RUN-")
        or not sequence.isdigit()
    ):
        raise ReviewTransportError(f"invalid RUN id: {run_id!r}")
    return f"{stem}-"


def _remote_run_ids(repo: Path, remote: str, task_prefix: str) -> set[str]:
    refs = _exact_remote_refs(
        repo,
        remote,
        f"refs/heads/aios/failure-artifacts/{task_prefix}*",
        f"refs/heads/aios/artifacts/{task_prefix}*",
    )
    return _remote_run_ids_from_refs(refs, task_prefix)


def _remote_run_ids_from_refs(
    refs: Mapping[str, str], task_prefix: str
) -> set[str]:
    result: set[str] = set()
    for ref in refs:
        if not ref.startswith(
            ("refs/heads/aios/failure-artifacts/", "refs/heads/aios/artifacts/")
        ):
            continue
        run_id = ref.rsplit("/", 1)[-1]
        if not run_id.startswith(task_prefix):
            raise ReviewTransportError("canonical RUN ref TASK identity mismatch")
        _run_task_prefix(run_id)
        result.add(run_id)
    return result


def _historical_repair_ref_snapshot(
    repo: Path, remote: str, task_prefix: str
) -> Mapping[str, str]:
    """Acquire and validate one immutable TASK-scoped historical REPAIR snapshot."""

    patterns = (
        f"refs/heads/aios/failure-artifacts/{task_prefix}*",
        f"refs/heads/aios/artifacts/{task_prefix}*",
        f"refs/heads/aios/failure/{task_prefix}*",
    )
    code, output, stderr = _git_cmd(
        repo, "ls-remote", "--refs", remote, *patterns, allow_fail=True
    )
    if code:
        category = _remote_snapshot_failure_category(stderr)
        raise RemoteQueryError(
            "historical REPAIR snapshot acquisition failed: "
            f"exit_status={code} category={category}",
            category=category,
        )

    allowed_prefixes = (
        "refs/heads/aios/failure-artifacts/",
        "refs/heads/aios/artifacts/",
        "refs/heads/aios/failure/",
    )
    run_pattern = re.compile(rf"^{re.escape(task_prefix)}\d{{3,}}$")
    object_pattern = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
    refs: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            raise ReviewTransportError(
                "malformed historical REPAIR canonical ref snapshot"
            )
        commit_sha, ref = parts
        matching_prefixes = [
            prefix for prefix in allowed_prefixes if ref.startswith(prefix)
        ]
        if len(matching_prefixes) != 1:
            raise ReviewTransportError(
                "invalid historical REPAIR canonical ref snapshot prefix"
            )
        run_id = ref[len(matching_prefixes[0]) :]
        if not run_pattern.fullmatch(run_id) or not object_pattern.fullmatch(commit_sha):
            raise ReviewTransportError(
                "invalid historical REPAIR canonical ref snapshot identity"
            )
        if ref in refs:
            raise ReviewTransportError(
                "ambiguous historical REPAIR canonical ref snapshot"
            )
        refs[ref] = commit_sha
    return MappingProxyType(refs)


def _remote_snapshot_failure_category(stderr: str) -> str:
    """Return a bounded operational category without relaying raw Git diagnostics."""

    detail = stderr.casefold()
    if any(
        signal in detail
        for signal in (
            "authentication failed",
            "authorization failed",
            "could not read username",
            "permission denied",
            "access denied",
        )
    ):
        return "AUTH"
    if any(
        signal in detail
        for signal in ("could not resolve host", "name resolution", "no such host")
    ):
        return "DNS"
    if any(
        signal in detail
        for signal in ("certificate", "ssl", "tls")
    ):
        return "TLS"
    if any(signal in detail for signal in ("timed out", "timeout")):
        return "TIMEOUT"
    if any(
        signal in detail
        for signal in (
            "could not connect",
            "couldn't connect",
            "failed to connect",
            "connection reset",
            "network is unreachable",
        )
    ):
        return "CONNECTIVITY"
    return "UNKNOWN"


def _exact_remote_refs(repo: Path, remote: str, *patterns: str) -> dict[str, str]:
    code, output, _ = _git_cmd(
        repo, "ls-remote", "--refs", remote, *patterns, allow_fail=True
    )
    if code:
        raise RemoteQueryError("failed to query canonical refs")
    refs: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2 or parts[1] in refs:
            raise ReviewTransportError("malformed or ambiguous canonical ref result")
        refs[parts[1]] = parts[0]
    return refs


def _json_mapping(content: bytes, name: str) -> Mapping[str, Any]:
    if name == "FAILURE":
        return failure_document(content)
    try:
        value = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewTransportError(f"invalid canonical {name} JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ReviewTransportError(f"canonical {name} must be a mapping")
    return value


def _create_artifacts_commit(
    repo: Path,
    *,
    run_path: Path,
    result_path: Path,
    run_id: str,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
    execution_profile_path: Path | None = None,
    publication_recovery_path: Path | None = None,
) -> str:
    """Create an isolated success artifact tree with optional operational state."""
    if publication_recovery_path is not None:
        return _create_named_artifacts_commit(repo, run_path=run_path,
            artifact_path=result_path, artifact_name="result.json", run_id=run_id,
            lineage_path=lineage_path, observation_path=observation_path,
            execution_profile_path=execution_profile_path,
            publication_recovery_path=publication_recovery_path)
    if not run_path.is_file():
        raise ReviewTransportError(f"persisted RUN JSON missing: {run_path}")
    if not result_path.is_file():
        raise ReviewTransportError(f"persisted canonical ResultPackage JSON missing: {result_path}")

    run_bytes = public_run(run_path.read_bytes())
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=run_bytes,
            capture_output=True,
            check=True,
        )
        run_blob_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to hash run.json: {exc}") from exc

    result_bytes = public_result(result_path.read_bytes())
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=result_bytes,
            capture_output=True,
            check=True,
        )
        result_blob_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to hash result.json: {exc}") from exc

    lineage_entry = ""
    if lineage_path is not None:
        if not lineage_path.is_file():
            raise ReviewTransportError(f"persisted REPAIR lineage JSON missing: {lineage_path}")
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=public_metadata(lineage_path.read_bytes()), capture_output=True, check=True,
            )
            lineage_sha = proc.stdout.decode("utf-8", errors="strict").strip()
            lineage_entry = f"100644 blob {lineage_sha}\trepair.json\n"
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(f"failed to hash repair.json: {exc}") from exc

    observation_entry = ""
    if observation_path is not None:
        if not observation_path.is_file():
            raise ReviewTransportError(
                f"persisted RUN_OBSERVATION JSON missing: {observation_path}"
            )
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=observation_path.read_bytes(), capture_output=True, check=True,
            )
            observation_sha = proc.stdout.decode("utf-8", errors="strict").strip()
            observation_entry = (
                f"100644 blob {observation_sha}\tobservation.json\n"
            )
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(
                f"failed to hash observation.json: {exc}"
            ) from exc

    execution_profile_entry = ""
    if execution_profile_path is not None:
        if not execution_profile_path.is_file():
            raise ReviewTransportError(
                f"persisted execution profile JSON missing: {execution_profile_path}"
            )
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=execution_profile_path.read_bytes(), capture_output=True, check=True,
            )
            execution_profile_sha = proc.stdout.decode("utf-8", errors="strict").strip()
            execution_profile_entry = (
                f"100644 blob {execution_profile_sha}\texecution-profile.json\n"
            )
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(
                f"failed to hash execution-profile.json: {exc}"
            ) from exc

    tree_entries = [
        f"100644 blob {run_blob_sha}\trun.json\n",
        f"100644 blob {result_blob_sha}\tresult.json\n",
    ]
    if lineage_entry:
        tree_entries.append(lineage_entry)
    if observation_entry:
        tree_entries.append(observation_entry)
    if execution_profile_entry:
        tree_entries.append(execution_profile_entry)
    tree_entries.sort(key=lambda line: line.split("\t")[1])
    tree_input = "".join(tree_entries)
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=tree_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        transport_tree_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create transport tree: {exc}") from exc

    ai_tree_input = f"040000 tree {transport_tree_sha}\ttransport\n"
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=ai_tree_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        ai_tree_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create .ai tree: {exc}") from exc

    root_tree_input = f"040000 tree {ai_tree_sha}\t.ai\n"
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=root_tree_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        root_tree_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create root artifacts tree: {exc}") from exc

    commit_message = f"AIOS artifacts for {run_id}"
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "commit-tree", root_tree_sha, "-m", commit_message),
            capture_output=True,
            check=True,
        )
        commit_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create artifacts commit: {exc}") from exc

    return commit_sha


def _create_named_artifacts_commit(
    repo: Path,
    *,
    run_path: Path,
    artifact_path: Path,
    artifact_name: str,
    run_id: str,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
    preverification_path: Path | None = None,
    execution_profile_path: Path | None = None,
    publication_recovery_path: Path | None = None,
    projected_inputs: Mapping[str, bytes] | None = None,
) -> str:
    """Create an isolated artifacts commit without touching the worktree."""

    if not run_path.is_file():
        raise ReviewTransportError(f"persisted RUN JSON missing: {run_path}")
    if not artifact_path.is_file():
        raise ReviewTransportError(f"persisted {artifact_name} JSON missing: {artifact_path}")

    blobs: dict[str, str] = {}
    inputs = [("run.json", run_path), (artifact_name, artifact_path)]
    if publication_recovery_path is not None:
        inputs.append(("publication-recovery.json", publication_recovery_path))
    if lineage_path is not None:
        if not lineage_path.is_file():
            raise ReviewTransportError(
                f"persisted REPAIR lineage JSON missing: {lineage_path}"
            )
        inputs.append(("repair.json", lineage_path))
    if observation_path is not None:
        if not observation_path.is_file():
            raise ReviewTransportError(
                f"persisted RUN_OBSERVATION JSON missing: {observation_path}"
            )
        inputs.append(("observation.json", observation_path))
    if preverification_path is not None:
        if not preverification_path.is_file():
            raise ReviewTransportError(
                "persisted pre-verification candidate JSON missing: "
                f"{preverification_path}"
            )
        inputs.append(
            ("pre-verification-candidate.json", preverification_path)
        )
    if execution_profile_path is not None:
        if not execution_profile_path.is_file():
            raise ReviewTransportError(
                f"persisted execution profile JSON missing: {execution_profile_path}"
            )
        inputs.append(("execution-profile.json", execution_profile_path))
    for name, path in inputs:
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=(projected_inputs[".ai/transport/" + name] if projected_inputs is not None else
                       public_run(path.read_bytes()) if name == "run.json" else
                       public_result(path.read_bytes()) if name == "result.json" else
                       public_failure(path.read_bytes(), private_original=True) if name == "failure.json" else
                       public_metadata(path.read_bytes()) if name in {"repair.json", "pre-verification-candidate.json"} else path.read_bytes()),
                capture_output=True, check=True,
            )
            blobs[name] = proc.stdout.decode("utf-8", errors="strict").strip()
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(f"failed to hash {name}: {exc}") from exc
    tree_input = "".join(
        f"100644 blob {sha}\t{name}\n" for name, sha in sorted(blobs.items())
    )
    try:
        transport = subprocess.run(
            ("git", "-C", str(repo), "mktree"), input=tree_input.encode(),
            capture_output=True, check=True,
        ).stdout.decode().strip()
        ai = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {transport}\ttransport\n".encode(),
            capture_output=True, check=True,
        ).stdout.decode().strip()
        root = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {ai}\t.ai\n".encode(),
            capture_output=True, check=True,
        ).stdout.decode().strip()
        return subprocess.run(
            ("git", "-C", str(repo), "commit-tree", root, "-m", f"AIOS {artifact_name} for {run_id}"),
            capture_output=True, check=True,
        ).stdout.decode().strip()
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        raise ReviewTransportError(f"failed to create {artifact_name} artifacts commit: {exc}") from exc


def transport_failure(
    repo: Path, *, run_id: str, head_sha: str, run_path: Path, failure_path: Path,
    publish_candidate: bool = True,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
    preverification_path: Path | None = None,
    execution_profile_path: Path | None = None,
) -> None:
    """Publish an immutable, authority-checked failed candidate and its facts."""

    remote = resolve_transport_remote(repo)
    candidate_ref = f"refs/heads/aios/failure/{run_id}"
    artifacts_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    try:
        _validate_transport_affinity(repo, run_path.read_bytes(), head_sha,
                                     lineage_path.read_bytes() if lineage_path is not None else None)
    except ReviewTransportError as exc:
        raise FailureDiagnosticError("FAILURE_TRANSPORT_AFFINITY_INVALID") from exc
    expected = {
        ".ai/transport/run.json": public_run(run_path.read_bytes()),
        ".ai/transport/failure.json": public_failure(failure_path.read_bytes(), private_original=True),
    }
    failure = failure_document(expected[".ai/transport/failure.json"])
    if "public_projection" in failure:
        try:
            bound_head = _bind_performance_terminal_identity(
                expected[".ai/transport/run.json"], expected[".ai/transport/failure.json"],
                ref=artifacts_ref, run_id=run_id, task_id=failure["task"]["id"], terminal_kind="FAILURE")[3]
            if bound_head != head_sha:
                raise ValueError("candidate subject mismatch")
            if publish_candidate and failure["candidate"]["transportable"] is not True:
                raise FailureDiagnosticError("FAILURE_CANDIDATE_NOT_PUBLISHABLE")
        except FailureDiagnosticError:
            raise
        except (ReviewTransportError, KeyError, TypeError, ValueError) as exc:
            raise FailureDiagnosticError("FAILURE_TRANSPORT_IDENTITY_INVALID") from exc
    if lineage_path is not None:
        expected[".ai/transport/repair.json"] = public_metadata(lineage_path.read_bytes())
    expected_observation = (
        observation_path.read_bytes() if observation_path is not None else None
    )
    expected_preverification = (
        public_metadata(preverification_path.read_bytes())
        if preverification_path is not None
        else None
    )
    if execution_profile_path is None:
        default_profile_path = (
            run_path.parent.parent / "execution-profiles" / f"{run_id}.json"
        )
        if default_profile_path.is_file():
            execution_profile_path = default_profile_path
    expected_execution_profile = (
        execution_profile_path.read_bytes()
        if execution_profile_path is not None and execution_profile_path.is_file()
        else None
    )
    queried_refs = (candidate_ref, artifacts_ref) if publish_candidate else (artifacts_ref,)
    code, output, _ = _git_cmd(repo, "ls-remote", remote, *queried_refs, allow_fail=True)
    if code:
        raise ReviewTransportError(f"failed to query remote refs from {remote}")
    refs = {parts[1]: parts[0] for line in output.splitlines() if len(parts := line.split()) >= 2}
    specs: list[str] = []
    if publish_candidate:
        if candidate_ref in refs:
            if refs[candidate_ref] != head_sha:
                raise ReviewTransportError(f"remote failure ref {candidate_ref} conflict")
        else:
            specs.append(f"{head_sha}:{candidate_ref}")
    terminal_artifact_sha = refs.get(artifacts_ref)
    if artifacts_ref in refs:
        observed = {path: _read_remote_blob(repo, remote, refs[artifacts_ref], path) for path in expected}
        legacy = {".ai/transport/run.json": run_path.read_bytes(),
                  ".ai/transport/failure.json": failure_path.read_bytes()}
        if lineage_path is not None:
            legacy[".ai/transport/repair.json"] = lineage_path.read_bytes()
        compatible = observed == expected or observed == legacy
        if not compatible:
            from .unified_state import _matches_public_document
            try:
                compatible = all(observed[path] is not None and _matches_public_document(
                    _public_document(legacy[path], max_bytes=PUBLIC_FAILURE_MAX_BYTES),
                    _public_document(observed[path], max_bytes=PUBLIC_FAILURE_MAX_BYTES),
                    "RUN" if path.endswith("run.json") else "METADATA") for path in expected)
            except (KeyError, TypeError, ValueError, ReviewTransportError):
                compatible = False
        if not compatible:
            raise ReviewTransportError(
                f"remote failure artifacts ref {artifacts_ref} exists with different artifact content"
            )
        remote_observation = _read_remote_blob(
            repo, remote, refs[artifacts_ref], ".ai/transport/observation.json"
        )
        if (
            expected_observation is not None
            and remote_observation not in (None, expected_observation)
        ):
            raise ReviewTransportError(
                f"remote failure artifacts ref {artifacts_ref} exists with different observation content"
            )
        remote_preverification = _read_remote_blob(
            repo,
            remote,
            refs[artifacts_ref],
            ".ai/transport/pre-verification-candidate.json",
        )
        if (
            expected_preverification is not None
            and remote_preverification not in (expected_preverification, preverification_path.read_bytes())
        ):
            raise ReviewTransportError(
                f"remote failure artifacts ref {artifacts_ref} exists with different pre-verification candidate content"
            )
        remote_execution_profile = _read_remote_blob(
            repo, remote, refs[artifacts_ref], ".ai/transport/execution-profile.json"
        )
        if remote_execution_profile != expected_execution_profile:
            raise ReviewTransportError(
                f"remote failure artifacts ref {artifacts_ref} exists with different execution profile content"
            )
    else:
        projected_inputs = dict(expected)
        for name, content in (("observation.json", expected_observation),
                              ("pre-verification-candidate.json", expected_preverification),
                              ("execution-profile.json", expected_execution_profile)):
            if content is not None:
                projected_inputs[".ai/transport/" + name] = content
        commit = _create_named_artifacts_commit(
            repo, run_path=run_path, artifact_path=failure_path,
            artifact_name="failure.json", run_id=run_id,
            lineage_path=lineage_path,
            observation_path=observation_path,
            preverification_path=preverification_path,
            execution_profile_path=execution_profile_path,
            projected_inputs=projected_inputs,
        )
        terminal_artifact_sha = commit
        specs.append(f"{commit}:{artifacts_ref}")
    if specs:
        code, _, stderr = _git_cmd(repo, "push", "--no-tags", "--atomic",
            *(f"--force-with-lease={spec.split(':', 1)[1]}:" for spec in specs), remote, *specs, allow_fail=True)
        if code:
            raise ReviewTransportError(f"failed to push transport refs to {remote}: {stderr}")
    assert terminal_artifact_sha is not None
    try:
        publish_terminal_attention(
            repo,
            remote=remote,
            run_id=run_id,
            terminal_kind="FAILURE",
            artifact_sha=terminal_artifact_sha,
        )
    except TerminalAttentionError as exc:
        raise ReviewTransportError(
            f"terminal FAILURE is canonical but attention delivery failed: {exc}"
        ) from exc


_ADMISSION_DELIVERY_FIELDS = {
    "PRIMARY": "dispatch_id",
    "REMEDIATION": "correction_dispatch_id",
    "REPAIR": "repair_dispatch_id",
}


def admission_failure_delivery_ref(family: str, delivery_id: str) -> str:
    """Address one delivery without time ordering or unrelated ref discovery."""
    if not isinstance(family, str) or family not in _ADMISSION_DELIVERY_FIELDS or not isinstance(delivery_id, str) or (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", delivery_id) is None
    ):
        raise ReviewTransportError("invalid admission delivery identity")
    key = hashlib.sha256(delivery_id.encode("ascii")).hexdigest()
    return f"refs/heads/aios/admission-failure-delivery/{family.lower()}/{key}"


def _admission_delivery_cause(
    content: bytes, *, family: str, delivery_id: str
) -> dict[str, str]:
    if len(content) > 65536:
        raise ReviewTransportError("admission diagnostic exceeds bound")
    record = _performance_json_mapping(content, "Admission Failure")
    field = _ADMISSION_DELIVERY_FIELDS[family]
    phase, reason = record.get("phase"), record.get("reason_code")
    error = record.get("error")
    if (
        record.get("format") != "AIOS_ADMISSION_FAILURE"
        or type(record.get("version")) is not int or record["version"] != 2
        or record.get("kind") != "ADMISSION_FAILURE"
        or record.get("operation") != family
        or record.get(field) != delivery_id
        or any(name in record for name in _ADMISSION_DELIVERY_FIELDS.values() if name != field)
        or record.get("executor_invoked") is not False
        or any(name in record for name in (
            "run_id", "run_created", "result", "evidence", "terminal_pointer",
            "verification", "review", "publication", "executor_success",
        ))
        or not isinstance(phase, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", phase)
        or not isinstance(reason, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", reason)
        or not isinstance(error, Mapping) or set(error) != {"type", "message"}
        or not isinstance(error.get("type"), str) or not 0 < len(error["type"]) <= 128
        or not isinstance(error.get("message"), str) or len(error["message"]) > 2048
    ):
        raise ReviewTransportError("invalid delivery-bound Admission Failure schema")
    allowed = {
        "format", "version", "kind", "operation", "executor_invoked", "phase",
        "reason_code", "error", "requested_executor", "task", "requested_task_id",
        "finding_id", "review_id", "reviewed_sha", "failed_head_sha", "current_head_sha",
        "control_head_sha", "failed_run_id", "source_run_id", "dispatch_id",
        "correction_dispatch_id", "repair_dispatch_id", "task_blob_sha", "task_commit_sha",
        "current_task_blob_sha", "observed_ref", "observed_sha", "observed_snapshot_sha256",
        "requested_task_revision", "observed_ref_count", "observed_refs", "remote_query",
    }
    if set(record).difference(allowed):
        raise ReviewTransportError("unknown Admission Failure schema fields")
    string_fields = allowed - {
        "version", "executor_invoked", "error", "task", "requested_task_revision",
        "observed_ref_count", "observed_refs", "remote_query",
    }
    if any(not isinstance(record[name], str) or len(record[name]) > 256
           for name in string_fields if name in record):
        raise ReviewTransportError("invalid bounded Admission Failure identity")
    if "requested_executor" in record and record["requested_executor"] not in {
        "codex", "antigravity", "antigravity-minimax"
    }:
        raise ReviewTransportError("invalid Admission Failure requested Executor")
    task = record.get("task")
    if "task" in record and (
        not isinstance(task, Mapping) or set(task) != {"id", "revision"}
        or not isinstance(task.get("id"), str) or not 0 < len(task["id"]) <= 256
        or type(task.get("revision")) is not int or task["revision"] < 1
    ):
        raise ReviewTransportError("invalid Admission Failure task identity")
    for name, minimum in (("requested_task_revision", 1), ("observed_ref_count", 0)):
        if name in record and (type(record[name]) is not int or record[name] < minimum):
            raise ReviewTransportError("invalid Admission Failure observation count")
    observed = record.get("observed_refs")
    if "observed_refs" in record and (
        not isinstance(observed, list) or len(observed) > 8
        or any(not isinstance(item, Mapping) or set(item) != {"ref", "sha"}
               or not isinstance(item.get("ref"), str) or len(item["ref"]) > 256
               or not isinstance(item.get("sha"), str) or len(item["sha"]) > 64
               for item in observed)
    ):
        raise ReviewTransportError("invalid bounded Admission Failure ref observations")
    query = record.get("remote_query")
    if "remote_query" in record and (
        not isinstance(query, Mapping) or set(query) != {"operation", "outcome", "category"}
        or query.get("operation") != "LS_REMOTE" or query.get("outcome") != "UNAVAILABLE"
        or not isinstance(query.get("category"), str)
        or query.get("category") not in {"AUTH", "DNS", "TLS", "TIMEOUT", "CONNECTIVITY", "UNKNOWN"}
    ):
        raise ReviewTransportError("invalid Admission Failure remote query facts")
    return {"authority": "AIOS_ADMISSION_FAILURE", "phase": phase, "reason_code": reason}


def resolve_admission_failure_delivery(
    repo: Path, *, family: str, delivery_id: str
) -> dict[str, str] | None:
    """Read only one remote delivery diagnostic; never infer RUN/lifecycle truth.

    All fetching happens in a disposable object store, leaving the caller's refs,
    FETCH_HEAD, index, worktree and Runtime evidence unchanged.
    """
    alias = admission_failure_delivery_ref(family, delivery_id)
    remote = resolve_transport_remote(repo)
    url = str(repo.resolve()) if remote == "." else _git_cmd(repo, "remote", "get-url", remote)[1]
    with tempfile.TemporaryDirectory(prefix="aios-admission-observer-") as raw:
        observer = Path(raw)
        _git_cmd(observer, "init", "--quiet")
        _git_cmd(observer, "remote", "add", "canonical", url)
        refs = _exact_remote_refs(observer, "canonical", alias)
        if not refs:
            return None
        if set(refs) != {alias}:
            raise ReviewTransportError("unexpected admission delivery refs")
        commit = refs[alias]
        _git_cmd(observer, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", "canonical", commit)
        content = _read_local_blob(observer, commit, ".ai/transport/admission-failure.json")
        if content is None:
            raise ReviewTransportError("admission delivery content missing")
        cause = _admission_delivery_cause(content, family=family, delivery_id=delivery_id)
        identity = hashlib.sha256(content).hexdigest()
        ref = f"refs/heads/aios/admission-failure/{identity}"
        if _exact_remote_refs(observer, "canonical", ref, alias) != {ref: commit, alias: commit}:
            raise ReviewTransportError("admission delivery content identity conflict")
        return cause


def prove_remote_failed_candidate(
    repo: Path, *, failed_run_id: str, failed_head_sha: str
) -> tuple[str, dict[str, str]]:
    """Validate exact transported FAILURE in an isolated caller-owned observer."""
    if re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", failed_run_id) is None:
        raise ReviewTransportError("invalid failed RUN identity")
    remote = resolve_transport_remote(repo)
    candidate_ref = f"refs/heads/aios/failure/{failed_run_id}"
    artifact_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    patterns = (candidate_ref, artifact_ref,
                f"refs/heads/aios/review/{failed_run_id}",
                f"refs/heads/aios/artifacts/{failed_run_id}")
    refs = _exact_remote_refs(repo, remote, *patterns)
    if set(refs) != {candidate_ref, artifact_ref} or refs.get(candidate_ref) != failed_head_sha:
        raise ReviewTransportError("missing or conflicting canonical FAILURE transport")
    for commit in refs.values():
        _git_cmd(repo, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", remote, commit)
    run = _read_local_blob(repo, refs[artifact_ref], ".ai/transport/run.json")
    failure = _read_local_blob(repo, refs[artifact_ref], ".ai/transport/failure.json")
    if run is None or failure is None or len(run) > 65536 or len(failure) > PUBLIC_FAILURE_MAX_BYTES:
        raise ReviewTransportError("missing or unbounded canonical FAILURE content")
    task_id = "TASK-" + _run_task_prefix(failed_run_id)[4:-1]
    _, _, base_sha, candidate_sha = _bind_performance_terminal_identity(
        run, failure, ref=artifact_ref, run_id=failed_run_id,
        task_id=task_id, terminal_kind="FAILURE",
    )
    run_document = _performance_json_mapping(run, "RUN")
    if run_document.get("kind") == "REMEDIATION":
        run_document = run_document["execution"]["run"]
    if run_document.get("head_sha") not in (None, failed_head_sha):
        raise ReviewTransportError("canonical RUN failed-head binding conflicts")
    record = _performance_json_mapping(failure, "FAILURE")
    if candidate_sha != failed_head_sha or record["candidate"]["transportable"] is not True:
        raise ReviewTransportError("canonical FAILURE is not the clean transportable candidate")
    if _exact_remote_refs(repo, remote, *patterns) != refs:
        raise ReviewTransportError("canonical FAILURE transport drift")
    return base_sha, refs


def transport_admission_failure(
    repo: Path, *, identity: str, diagnostic_path: Path
) -> None:
    """Publish one immutable, content-addressed pre-RUN diagnostic."""

    if len(identity) != 64 or any(
        character not in "0123456789abcdef" for character in identity
    ):
        raise ReviewTransportError("invalid admission-failure identity")
    if not diagnostic_path.is_file():
        raise ReviewTransportError(
            f"persisted admission-failure JSON missing: {diagnostic_path}"
        )
    content = diagnostic_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != identity:
        raise ReviewTransportError(
            "admission-failure identity does not match diagnostic content"
        )
    record = _json_mapping(content, "Admission Failure")
    family = record.get("operation")
    field = _ADMISSION_DELIVERY_FIELDS.get(family) if isinstance(family, str) else None
    delivery = record.get(field) if field else None
    alias = None
    if isinstance(delivery, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", delivery):
        alias = admission_failure_delivery_ref(family, delivery)
        _admission_delivery_cause(content, family=family, delivery_id=delivery)
    remote = resolve_transport_remote(repo)
    ref = f"refs/heads/aios/admission-failure/{identity}"
    expected_refs = (ref, alias) if alias else (ref,)
    refs = _exact_remote_refs(repo, remote, *expected_refs)
    if set(refs).difference(expected_refs):
        raise ReviewTransportError("unexpected admission-failure ref result")
    for existing_ref, sha in refs.items():
        if _read_remote_blob(repo, remote, sha, ".ai/transport/admission-failure.json") != content:
            raise ReviewTransportError(f"remote admission-failure ref {existing_ref} exists with different content")
    if len(set(refs.values())) > 1:
        raise ReviewTransportError("admission alias and content ref commit conflict")
    missing = [name for name in expected_refs if name not in refs]
    if not missing:
        return
    commit = next(iter(refs.values()), None)
    if commit is None:
        commit = _create_admission_failure_commit(
            repo, identity=identity, diagnostic_path=diagnostic_path, content=content
        )
    code, _, stderr = _git_cmd(
        repo,
        "push",
        "--no-tags",
        *(("--atomic",) if alias else ()),
        *(f"--force-with-lease={name}:" for name in missing),
        remote,
        *(f"{commit}:{name}" for name in missing),
        allow_fail=True,
    )
    if code:
        raise ReviewTransportError(
            f"failed to push admission-failure ref to {remote}: {stderr}"
        )


def _create_admission_failure_commit(
    repo: Path, *, identity: str, diagnostic_path: Path, content: bytes | None = None
) -> str:
    """Create an isolated diagnostic commit without touching the worktree."""

    try:
        blob = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=diagnostic_path.read_bytes() if content is None else content,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        transport_tree = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=(
                f"100644 blob {blob}\tadmission-failure.json\n"
            ).encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        ai_tree = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {transport_tree}\ttransport\n".encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        root_tree = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {ai_tree}\t.ai\n".encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        return subprocess.run(
            (
                "git",
                "-C",
                str(repo),
                "commit-tree",
                root_tree,
                "-m",
                f"AIOS admission failure {identity}",
            ),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        raise ReviewTransportError(
            f"failed to create admission-failure commit: {exc}"
        ) from exc


def _decode_repair_supersession(
    content: bytes, *, run_id: str, revision: int
) -> tuple[str, str]:
    metadata = _json_mapping(content, "REPAIR supersession")
    required = {
        "format",
        "version",
        "failed_run_id",
        "authorization_revision",
        "predecessor_repair_sha",
        "failure_artifacts_sha",
    }
    if set(metadata) != required:
        raise ReviewTransportError("REPAIR supersession fields are invalid")
    predecessor = metadata.get("predecessor_repair_sha")
    failure_sha = metadata.get("failure_artifacts_sha")
    if (
        metadata.get("format") != REPAIR_SUPERSESSION_FORMAT
        or metadata.get("version") != 1
        or metadata.get("failed_run_id") != run_id
        or metadata.get("authorization_revision") != revision
        or revision < 2
        or not isinstance(predecessor, str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", predecessor) is None
        or not isinstance(failure_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", failure_sha) is None
    ):
        raise ReviewTransportError("REPAIR supersession identity is invalid")
    return predecessor, failure_sha


def _select_current_repair_authorization(
    candidates: Sequence[RemoteRepairAuthorization],
    *,
    failure_artifacts_sha: str | None,
) -> RemoteRepairAuthorization:
    """Select one contiguous immutable authorization chain or fail closed."""

    roots = [item for item in candidates if item.revision == 1]
    if len(roots) != 1:
        raise ReviewTransportError("canonical REPAIR root is missing or ambiguous")
    by_revision: dict[int, list[RemoteRepairAuthorization]] = {}
    for item in candidates:
        by_revision.setdefault(item.revision, []).append(item)
    if any(len(items) != 1 for items in by_revision.values()):
        raise ReviewTransportError("canonical REPAIR successor is ambiguous")
    revisions = sorted(by_revision)
    if revisions != list(range(1, revisions[-1] + 1)):
        raise ReviewTransportError("canonical REPAIR revision chain is discontinuous")
    current = roots[0]
    root_identity = _json_mapping(current.repair, "REPAIR authorization")
    root_task = root_identity.get("task")
    if (
        root_identity.get("failed_run_id") != current.failed_run_id
        or not isinstance(root_identity.get("failed_head_sha"), str)
        or not isinstance(root_task, Mapping)
        or not isinstance(root_task.get("id"), str)
        or isinstance(root_task.get("revision"), bool)
        or not isinstance(root_task.get("revision"), int)
    ):
        raise ReviewTransportError("canonical REPAIR root identity is invalid")
    for revision in revisions[1:]:
        successor = by_revision[revision][0]
        if successor.predecessor_sha != current.commit_sha:
            raise ReviewTransportError("canonical REPAIR predecessor chain is broken")
        if (
            failure_artifacts_sha is None
            or successor.failure_artifacts_sha != failure_artifacts_sha
        ):
            raise ReviewTransportError("canonical REPAIR FAILURE identity is stale")
        identity = _json_mapping(successor.repair, "REPAIR authorization")
        for key in ("failed_run_id", "failed_head_sha", "task"):
            if identity.get(key) != root_identity.get(key):
                raise ReviewTransportError(
                    "canonical REPAIR supersession changes failed RUN identity"
                )
        current = successor
    return current


def resolve_remote_repair_authorization(
    repo: Path, run_id: str, *, remote: str | None = None
) -> RemoteRepairAuthorization:
    """Resolve the unique current REPAIR while preserving every immutable ref."""

    if re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", run_id) is None:
        raise ReviewTransportError(f"invalid failed RUN id: {run_id!r}")
    if remote is None:
        remote = resolve_transport_remote(repo)
    legacy_ref = f"refs/heads/aios/repair/{run_id}"
    successor_pattern = f"{REPAIR_SUPERSESSION_PREFIX}{run_id}/*"
    failure_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    refs = _exact_remote_refs(repo, remote, legacy_ref, successor_pattern, failure_ref)
    legacy_sha = refs.get(legacy_ref)
    if legacy_sha is None:
        raise ReviewTransportError(f"remote REPAIR not found for {run_id}")
    repair = _read_remote_blob(
        repo, remote, legacy_sha, ".ai/transport/repair.json"
    )
    if repair is None:
        raise ReviewTransportError(f"remote REPAIR JSON missing for {run_id}")
    candidates = [
        RemoteRepairAuthorization(run_id, legacy_ref, legacy_sha, 1, repair)
    ]
    prefix = f"{REPAIR_SUPERSESSION_PREFIX}{run_id}/"
    for ref, sha in sorted(refs.items()):
        if not ref.startswith(prefix):
            continue
        suffix = ref[len(prefix) :]
        if not suffix.isdigit():
            raise ReviewTransportError("remote REPAIR supersession is ambiguous")
        revision = int(suffix)
        successor_repair = _read_remote_blob(
            repo, remote, sha, ".ai/transport/repair.json"
        )
        metadata = _read_remote_blob(
            repo, remote, sha, REPAIR_SUPERSESSION_PATH
        )
        if successor_repair is None or metadata is None:
            raise ReviewTransportError("remote REPAIR supersession content is missing")
        predecessor, failure_sha = _decode_repair_supersession(
            metadata, run_id=run_id, revision=revision
        )
        parent_code, parent_sha, _ = _git_cmd(
            repo, "rev-parse", f"{sha}^", allow_fail=True
        )
        if parent_code or parent_sha != predecessor:
            raise ReviewTransportError(
                "remote REPAIR successor commit parent is invalid"
            )
        candidates.append(
            RemoteRepairAuthorization(
                run_id, ref, sha, revision, successor_repair,
                predecessor, failure_sha,
            )
        )
    return _select_current_repair_authorization(
        candidates, failure_artifacts_sha=refs.get(failure_ref)
    )


def read_remote_repair(repo: Path, run_id: str, *, remote: str | None = None) -> bytes:
    """Read the deterministically current ChatGPT-authored REPAIR."""

    return resolve_remote_repair_authorization(repo, run_id, remote=remote).repair


def _validate_transport_affinity(repo, run_bytes, head_sha, lineage_bytes=None):
    """Reject altered persisted selectors before any terminal ref write."""
    from .return_affinity import LEGACY
    from .terminal_attention import _load_yaml
    try:
        document = _performance_json_mapping(run_bytes, "RUN")
        if document.get("kind") == "REMEDIATION":
            execution = document.get("execution")
            if not isinstance(execution, Mapping) or not isinstance(execution.get("run"), Mapping):
                return  # The canonical structural validator owns malformed wrappers.
            value = execution["run"]
        elif "kind" in document:
            return
        else:
            value = document
        reference = value.get("task")
        base_sha = value.get("base_sha")
        if (not isinstance(reference, Mapping) or not isinstance(reference.get("id"), str)
                or type(reference.get("revision")) is not int or reference["revision"] < 1
                or not isinstance(base_sha, str) or re.fullmatch(r"[0-9a-f]{40}", base_sha) is None):
            return  # Do not replace existing RUN identity/base diagnostics with affinity errors.
        carriers, missing_task = [], False
        name = f".ai/tasks/{reference['id']}.yaml"
        for sha in (base_sha, head_sha):
            content = _read_local_blob(repo, sha, name)
            if content is None:
                missing_task = True
                continue
            task = _load_yaml(content, "transport TASK")
            if {"id": task.get("task_id"), "revision": task.get("revision")} != reference:
                return  # TASK identity/revision validation remains the canonical owner's job.
            carriers.append(task)
        if lineage_bytes is not None:
            lineage = _performance_json_mapping(lineage_bytes, "REPAIR")
            lineage_run, lineage_task = lineage.get("run"), lineage.get("task")
            if not isinstance(lineage_run, Mapping) or not isinstance(lineage_task, Mapping):
                return
            if (lineage_run.get("task") != reference
                    or {"id": lineage_task.get("task_id"), "revision": lineage_task.get("revision")} != reference):
                return
            carriers.extend((lineage_run, lineage_task))
        affinity = document_affinity(value)
        if missing_task and affinity != LEGACY:
            raise ValueError("origin TASK carrier missing")
        if any(document_affinity(carrier) != affinity for carrier in carriers):
            raise ValueError("persisted TASK/RUN/REPAIR affinity mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewTransportError("canonical terminal affinity conflicts with TASK/RUN lineage") from exc


def transport_post_pass(
    repo: Path,
    *,
    run_id: str,
    head_sha: str,
    run_path: Path,
    result_path: Path,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
    execution_profile_path: Path | None = None,
    publication_recovery_path: Path | None = None,
) -> str:
    """Publish aios/review/<RUN_ID> and aios/artifacts/<RUN_ID> to upstream remote."""
    remote = resolve_transport_remote(repo)
    review_ref = f"refs/heads/aios/review/{run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"

    if not run_path.is_file():
        raise ReviewTransportError(f"persisted RUN JSON missing: {run_path}")
    if not result_path.is_file():
        raise ReviewTransportError(f"persisted canonical ResultPackage JSON missing: {result_path}")

    expected_run_bytes = public_run(run_path.read_bytes())
    expected_result_bytes = public_result(result_path.read_bytes())
    original_run_bytes, original_result_bytes = run_path.read_bytes(), result_path.read_bytes()
    original_lineage_bytes = lineage_path.read_bytes() if lineage_path is not None else None
    expected_lineage_bytes = public_metadata(original_lineage_bytes) if original_lineage_bytes is not None else None
    expected_recovery_bytes = (publication_recovery_path.read_bytes()
                               if publication_recovery_path is not None else None)
    _validate_transport_affinity(repo, expected_run_bytes, head_sha, expected_lineage_bytes)
    expected_observation_bytes = (
        observation_path.read_bytes() if observation_path is not None else None
    )
    if execution_profile_path is None:
        default_profile_path = (
            run_path.parent.parent / "execution-profiles" / f"{run_id}.json"
        )
        if default_profile_path.is_file():
            execution_profile_path = default_profile_path
    expected_execution_profile_bytes = (
        execution_profile_path.read_bytes()
        if execution_profile_path is not None and execution_profile_path.is_file()
        else None
    )

    code, ls_out, _ = _git_cmd(repo, "ls-remote", remote, review_ref, artifacts_ref, allow_fail=True)
    if code != 0:
        raise ReviewTransportError(f"failed to query remote refs from {remote}")

    existing_remote_refs: dict[str, str] = {}
    for line in ls_out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            existing_remote_refs[parts[1]] = parts[0]

    push_review = True
    if review_ref in existing_remote_refs:
        existing_review_sha = existing_remote_refs[review_ref]
        if existing_review_sha == head_sha:
            push_review = False
        else:
            raise ReviewTransportError(
                f"remote review ref {review_ref} conflict: points to {existing_review_sha}, expected {head_sha}"
            )

    push_artifacts = True
    if artifacts_ref in existing_remote_refs:
        existing_artifacts_sha = existing_remote_refs[artifacts_ref]
        remote_run_bytes = _read_remote_blob(repo, remote, existing_artifacts_sha, ".ai/transport/run.json")
        remote_result_bytes = _read_remote_blob(repo, remote, existing_artifacts_sha, ".ai/transport/result.json")
        remote_lineage_bytes = _read_remote_blob(
            repo, remote, existing_artifacts_sha, ".ai/transport/repair.json"
        )
        remote_observation_bytes = _read_remote_blob(
            repo, remote, existing_artifacts_sha, ".ai/transport/observation.json"
        )

        remote_execution_profile_bytes = _read_remote_blob(
            repo, remote, existing_artifacts_sha, ".ai/transport/execution-profile.json"
        )
        remote_recovery_bytes = _read_remote_blob(
            repo, remote, existing_artifacts_sha, ".ai/transport/publication-recovery.json")

        if (
            ((remote_run_bytes == expected_run_bytes and remote_result_bytes == expected_result_bytes
              and remote_lineage_bytes == expected_lineage_bytes)
             or (remote_run_bytes == original_run_bytes and remote_result_bytes == original_result_bytes
                 and remote_lineage_bytes == original_lineage_bytes))
            and (
                expected_observation_bytes is None
                or remote_observation_bytes in (None, expected_observation_bytes)
            )
            and remote_execution_profile_bytes == expected_execution_profile_bytes
            and remote_recovery_bytes == expected_recovery_bytes
        ):
            push_artifacts = False
        else:
            raise ReviewTransportError(
                f"remote artifacts ref {artifacts_ref} exists with different artifact content"
            )

    artifacts_commit_sha = existing_remote_refs.get(artifacts_ref)
    if push_artifacts:
        artifacts_commit_sha = _create_artifacts_commit(
            repo,
            run_path=run_path,
            result_path=result_path,
            run_id=run_id,
            lineage_path=lineage_path,
            observation_path=observation_path,
            execution_profile_path=execution_profile_path,
            publication_recovery_path=publication_recovery_path,
        )

    push_specs: list[str] = []
    if push_review:
        push_specs.append(f"{head_sha}:{review_ref}")
    if push_artifacts:
        assert artifacts_commit_sha is not None
        push_specs.append(f"{artifacts_commit_sha}:{artifacts_ref}")

    if push_specs:
        recovery_cas = ["--atomic", *(f"--force-with-lease={spec.split(':', 1)[1]}:" for spec in push_specs)]
        code, _, stderr = _git_cmd(repo, "push", "--no-tags", *recovery_cas,
                                   remote, *push_specs, allow_fail=True)
        if code != 0:
            raise ReviewTransportError(f"failed to push transport refs to {remote}: {stderr}")
    assert artifacts_commit_sha is not None
    try:
        publish_terminal_attention(
            repo,
            remote=remote,
            run_id=run_id,
            terminal_kind="RESULT",
            artifact_sha=artifacts_commit_sha,
        )
    except TerminalAttentionError as exc:
        raise ReviewTransportError(
            f"terminal RESULT is canonical but attention delivery failed: {exc}"
        ) from exc
    return artifacts_commit_sha
