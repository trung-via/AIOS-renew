"""Reusable operator-layer GitHub transport for terminal RUN state."""

from __future__ import annotations

from .return_affinity import document_affinity

import hashlib
import json
import math
import re
import shlex
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .terminal_attention import (
    TerminalAttentionError,
    publish_terminal_attention,
)


class ReviewTransportError(RuntimeError):
    """Raised when post-PASS review or artifact transport fails."""


class _FailureProjectionError(ValueError):
    """Closed diagnostic blocker; never contains private observation text."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


PUBLIC_ARTIFACT_MAX_BYTES = 1024 * 1024
PUBLIC_FAILURE_MAX_BYTES = 16 * 1024 * 1024
PUBLIC_FAILURE_FORMAT = "AIOS_PUBLIC_FAILURE_V1"
PUBLIC_DIAGNOSTIC_FAILURE_FORMAT = "AIOS_PUBLIC_FAILURE_V2"
PUBLIC_OBSERVATION_FORMAT = "AIOS_PRIVATE_OBSERVATION_V1"
PUBLIC_INCOMPLETE_OBSERVATION_FORMAT = "AIOS_PRIVATE_OBSERVATION_V2"

_FAILURE_BLOCKER_MESSAGES = {
    "invalid verification reference observation": "FAILURE_OBSERVATION_SOURCE_INVALID",
    "verification reference outcome binding mismatch": "FAILURE_OBSERVATION_SOURCE_INVALID",
    "verification reference subject binding mismatch": "FAILURE_OBSERVATION_SOURCE_INVALID",
    "verification reference RUN binding mismatch": "FAILURE_OBSERVATION_SOURCE_INVALID",
    "verification reference base binding mismatch": "FAILURE_OBSERVATION_SOURCE_INVALID",
    "verification reference reuse binding mismatch": "FAILURE_OBSERVATION_SOURCE_INVALID",
    "invalid verification reference failure population": "FAILURE_OBSERVATION_COUNTS_INVALID",
    "invalid verification reference observation conditions": "FAILURE_OBSERVATION_CONDITIONS_INVALID",
    "contradictory verification reference failure conditions": "FAILURE_OBSERVATION_CONDITIONS_INVALID",
    "invalid verification reference source profile": "FAILURE_OBSERVATION_CONDITIONS_INVALID",
    "invalid verification reference raw metadata": "FAILURE_EVIDENCE_IDENTITY_INVALID",
    "invalid verification reference role": "FAILURE_EVIDENCE_ROLE_INVALID",
    "EVIDENCE identity outside a validated structural role": "FAILURE_EVIDENCE_ROLE_INVALID",
    "unsafe public verification command": "FAILURE_PUBLIC_COMMAND_REJECTED",
    "unsafe quoted pytest command argument": "FAILURE_PUBLIC_COMMAND_REJECTED",
    "unsafe quoted pytest selector": "FAILURE_PUBLIC_COMMAND_REJECTED",
    "public artifact exceeds byte bound": "FAILURE_ARTIFACT_EXCEEDS_BOUND",
    "public artifact exceeds nesting bound": "FAILURE_ARTIFACT_EXCEEDS_BOUND",
    "invalid public artifact JSON": "FAILURE_JSON_INVALID",
}


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


def _public_metadata_value(value: Any, *, historical_failure_view: bool = False) -> Any:
    """Exact read projection, also used inside ordinary bounded corrections."""
    if isinstance(value, list):
        return [_public_metadata_value(item, historical_failure_view=historical_failure_view) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("kind") == "FAILURE" and _has_failure_observations(value):
        projected = _project_failure(value)
        return _historical_failure_view(value, projected) if historical_failure_view else projected
    data = {key: _public_metadata_value(item, historical_failure_view=historical_failure_view) for key, item in value.items()}
    if "run_id" in data and "workspace" in data and "task" in data:
        data["workspace"] = "protected-runtime-workspace"
    if "evidence_id" in data and "raw" in data:
        _redact_evidence(data)
    if data.get("kind") == "FAILURE" and isinstance(data.get("error"), dict):
        error = data["error"]
        error["message"] = "Protected Runtime diagnostics remain local."
        error.pop("native_diagnostics", None)
        error.pop("native_captures", None)
        error.pop("executor_diagnostics", None)
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
    error = data.get("error")
    if isinstance(error, Mapping) and (
        {"exit_code", "native_diagnostics", "native_captures", "executor_diagnostics", "verification"} & error.keys()
        or "workspace_state" in data
    ):
        return True
    def observation(value: Any) -> bool:
        if isinstance(value, list):
            return any(observation(item) for item in value)
        return isinstance(value, Mapping) and (
            (value.get("policy") == "minimum-sufficient-v2" and isinstance(value.get("candidate"), Mapping))
            or any(observation(item) for item in value.values()))

    return observation(data)


def _failure_digest(value: Any) -> str:
    from .verification_contract import verification_digest
    return verification_digest(value)


def _historical_failure_view(private: Mapping[str, Any], projected: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct only the formerly lawful V1 view of an original private source.

    New Runtime diagnostic records cannot downgrade their native facts. This is
    an exact read/replay comparison, never a new public writer or proof grant.
    """
    if ("public_projection" in private or "workspace_state" in private
            or {"native_kind", "native_timeout_seconds", "native_captures"} & private.get("error", {}).keys()):
        return projected
    def observation(value: Any) -> bool:
        if isinstance(value, list):
            return any(observation(item) for item in value)
        return isinstance(value, Mapping) and (
            value.get("policy") == "minimum-sufficient-v2" and isinstance(value.get("candidate"), Mapping)
            or any(observation(item) for item in value.values()))
    if not observation(private) or projected.get("public_projection", {}).get("format") != PUBLIC_DIAGNOSTIC_FAILURE_FORMAT:
        return projected
    if any(ref.get(side, {}).get("public_observation") == PUBLIC_INCOMPLETE_OBSERVATION_FORMAT
           for item in projected["error"]["verification"]
           for ref in [item.get("verification", {})] for side in ("base", "candidate")):
        return projected  # Incomplete details never had a lawful historical V1 view.
    legacy = json.loads(json.dumps(projected))
    legacy["error"].pop("diagnostics", None)
    legacy["error"].pop("exit_code", None)
    legacy["public_projection"]["format"] = PUBLIC_FAILURE_FORMAT
    legacy["public_projection"]["projection_digest"] = _failure_digest({key: value for key, value in legacy.items() if key != "public_projection"})
    validate_public_failure(legacy)
    return legacy


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _credential_shape(value: str) -> bool:
    return re.search(r"(?i)(?:\b(?:sk-|gh[pousr]_|github_pat_|AKIA)[A-Za-z0-9_-]+|(?:password|passwd|secret|token|credential|authorization|api[_ -]?key)\s*[:=]|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)", value) is not None


def _public_command(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 8192
            or _credential_shape(value)
            or re.search(r"(?i)(?:^|\s)--?(?:password|passwd|secret|token|credential|authorization|api[-_]?key|auth)(?:[=\s]|$)", value)
            or re.search(r"(?i)(?:[a-z]:[/\\]|://|(?:password|secret|token|credential|api_key)\s*[:=]|(?:^|\s|=)/|(?:^|[/\s=])\.\.(?:/|\s|$)|\b(?:sk-|gh[pousr]_|github_pat_|AKIA)[A-Za-z0-9_-]+)", value)):
        raise ValueError("unsafe public verification command")
    plain = value
    if "\"" in value or "'" in value:
        # Quoting is allowed only for one bounded pytest boolean selector.
        # Never treat the command as a shell or remove quoting before validation.
        if "\\" in value or any(c in value for c in "$`;|&<>\r\n"):
            raise ValueError("unsafe public verification command")
        matches = list(re.finditer(r"(?<!\S)(?P<option>-(?:k|m)) +(?P<quote>['\"])(?P<selector>[^'\"]+)(?P=quote)(?= |$)", value))
        if not matches:
            raise ValueError("unsafe public verification command")
        for item in reversed(matches):
            _pytest_selector(item["selector"])
            plain = plain[:item.start()] + item["option"] + " selector" + plain[item.end():]
        if "\"" in plain or "'" in plain:
            raise ValueError("unsafe public verification command")
        argv = shlex.split(plain)
        if tuple(argv[:3]) not in {("python", "-m", "pytest"), ("python3", "-m", "pytest"), ("py", "-m", "pytest")} and argv[:1] != ["pytest"]:
            raise ValueError("unsafe public verification command")
        _quoted_pytest_arguments(argv[1:] if argv[0] == "pytest" else argv[3:])
    if re.fullmatch(r"[A-Za-z0-9_ ./,:=+*?@%\[\]()-]+", plain) is None:
        raise ValueError("unsafe public verification command")
    return value


def _quoted_pytest_arguments(argv: list[str]) -> None:
    """Quoted selectors do not admit arbitrary CLI values or filesystem options."""
    flags = {"-q", "-qq", "-v", "-vv", "-vvv", "-x", "-s", "--exitfirst",
             "--disable-warnings", "--strict-markers", "--strict-config", "--collect-only"}
    cursor = 0
    while cursor < len(argv):
        argument = argv[cursor]
        cursor += 1
        if argument in {"-k", "-m"}:
            if cursor == len(argv) or argv[cursor] != "selector":
                raise ValueError("invalid quoted pytest selector argument")
            cursor += 1
        elif (argument in flags or re.fullmatch(r"--tb=(?:auto|short|long|line|native|no)", argument)
                or re.fullmatch(r"--maxfail=\d{1,5}", argument)
                or re.fullmatch(r"(?:tests(?:/[A-Za-z0-9_./-]+)?|test_[A-Za-z0-9_-]+\.py)(?:::[A-Za-z0-9_.\[\]-]+)*", argument)):
            continue
        else:
            raise ValueError("unsafe quoted pytest command argument")


def _pytest_selector(value: str) -> None:
    """Closed boolean grammar, without shell, paths, escapes or free prose."""
    if len(value) > 512 or re.fullmatch(r"[A-Za-z0-9_ ()-]+", value) is None:
        raise ValueError("unsafe quoted pytest selector")
    tokens = re.findall(r"[A-Za-z0-9_-]+|[()]", value)
    cursor = 0

    def expression() -> None:
        nonlocal cursor
        atom()
        while cursor < len(tokens) and tokens[cursor] in {"and", "or"}:
            cursor += 1
            atom()

    def atom() -> None:
        nonlocal cursor
        if cursor >= len(tokens):
            raise ValueError("invalid quoted pytest selector")
        word = tokens[cursor]
        cursor += 1
        if word == "not":
            atom()
        elif word == "(":
            expression()
            if cursor >= len(tokens) or tokens[cursor] != ")":
                raise ValueError("invalid quoted pytest selector")
            cursor += 1
        elif (word in {"and", "or", ")"} or len(word) > 96
              or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", word) is None
              or re.search(r"(?i)(?:password|secret|credential|token|api[_-]?key|^(?:sk-|gh[pousr]_|github_pat_|AKIA)|^[0-9a-f]{32,}$)", word)):
            raise ValueError("unsafe quoted pytest selector")

    expression()
    if cursor != len(tokens):
        raise ValueError("invalid quoted pytest selector")


_SAFE_CAUSES = {
    "NATIVE_TIMEOUT": "Native execution exceeded its time limit.",
    "NATIVE_INTERRUPTED": "Native execution was interrupted.",
    "CONNECTION_TIMEOUT": "The provider connection timed out.",
    "CONNECTION_FAILED": "The provider connection failed.",
    "PROVIDER_HTTP_502": "The provider gateway failed (HTTP 502).",
    "PROVIDER_HTTP_503": "The provider service is unavailable (HTTP 503).",
    "PROVIDER_HTTP_504": "The provider gateway timed out (HTTP 504).",
    "AUTHENTICATION_FAILED": "The provider rejected authentication.",
    "RATE_LIMITED": "The provider rate limit was reached.",
    "REPOSITORY_DIRTY": "The candidate contains uncommitted changes.",
    "SCOPE_VIOLATION": "Candidate changes exceed the authorized modification scope.",
    "COMPLETION_INCOMPLETE": "The Executor reported remaining implementation work.",
    "ASSERTION_FAILED": "A verification assertion failed.",
    "VERIFICATION_FAILED": "Runtime verification returned a nonzero exit code.",
    "VERIFICATION_INTERRUPTED": "Runtime verification was interrupted.",
    "COMPLETION_INTERRUPTED": "Runtime completion was interrupted.",
    "VERIFICATION_UNAVAILABLE": "Runtime verification has no recorded exit outcome.",
    "VERIFICATION_OBSERVATION_INCOMPLETE": "Runtime verification observations are incomplete and provide no comparable PASS proof.",
}


def _safe_diagnostic_cause(line: str) -> tuple[str, str] | None:
    """Extract facts using a closed grammar; captured text is never instructions.

    Unknown prose is retained privately. Credentials, local paths, escape syntax
    and mixed instruction/diagnostic lines do not enter the portable view.
    """
    if (not line or len(line) > 512 or re.search(
        r"(?i)(?:secret|password|passwd|token|credential|authorization|api[_ -]?key|"
        r"sk-|gh[pousr]_|github_pat_|AKIA|[a-z]:[/\\]|://|[/\\]|[$`;|&<>]|[\x00-\x1f])", line
    )):
        return None
    native = re.fullmatch(r"Codex CLI exited with code -?\d{1,6}: (.+)", line)
    if native:
        return _safe_diagnostic_cause(native[1])
    provider = re.fullmatch(r"(?i)(?:provider (?:unavailable|gateway error|gateway timeout)|HTTP) (502|503|504)", line)
    if provider:
        code = "PROVIDER_HTTP_" + provider[1]
        return code, _SAFE_CAUSES[code]
    if re.fullmatch(r"(?i)(?:codex(?: CLI)?(?: exec)?|native execution) (?:timed out|timeout)(?: after \d+(?:\.\d+)?(?:s| seconds))?\.?", line):
        return "NATIVE_TIMEOUT", _SAFE_CAUSES["NATIVE_TIMEOUT"]
    if re.fullmatch(r"(?i)(?:request|connection|provider connection) timed out(?: after \d+(?:\.\d+)?(?:s| seconds))?\.?", line):
        return "CONNECTION_TIMEOUT", _SAFE_CAUSES["CONNECTION_TIMEOUT"]
    if re.fullmatch(r"(?i)(?:native execution interrupted by Human|provider interrupted|execution interrupted)\.?", line):
        return "NATIVE_INTERRUPTED", _SAFE_CAUSES["NATIVE_INTERRUPTED"]
    if line == "Runtime verification interrupted by Human":
        return "VERIFICATION_INTERRUPTED", _SAFE_CAUSES["VERIFICATION_INTERRUPTED"]
    if line == "Runtime completion interrupted by Human":
        return "COMPLETION_INTERRUPTED", _SAFE_CAUSES["COMPLETION_INTERRUPTED"]
    if re.fullmatch(r"(?i)(?:connection refused|connection failed|provider connection failed)\.?", line):
        return "CONNECTION_FAILED", _SAFE_CAUSES["CONNECTION_FAILED"]
    if re.fullmatch(r"(?i)(?:authentication failed|unauthorized|HTTP 401|HTTP 403)\.?", line):
        return "AUTHENTICATION_FAILED", _SAFE_CAUSES["AUTHENTICATION_FAILED"]
    if re.fullmatch(r"(?i)(?:rate limit exceeded|HTTP 429|too many requests)\.?", line):
        return "RATE_LIMITED", _SAFE_CAUSES["RATE_LIMITED"]
    if line in {"repository dirty", "repository dirty after synchronization"}:
        return "REPOSITORY_DIRTY", _SAFE_CAUSES["REPOSITORY_DIRTY"]
    if re.fullmatch(r"AssertionError(?:: expected -?\d{1,9}(?:\.\d{1,6})?, got -?\d{1,9}(?:\.\d{1,6})?)?", line):
        return "ASSERTION_FAILED", line if ":" in line else _SAFE_CAUSES["ASSERTION_FAILED"]
    missing = re.fullmatch(r"ModuleNotFoundError: No module named ['\"]([A-Za-z_][A-Za-z0-9_.]{0,95})['\"]", line)
    if missing:
        return "MODULE_UNAVAILABLE", "Python module unavailable: " + missing[1]
    return None


def _diagnostic_stream(value: Any, *, historical: bool = False) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    if value is None:
        return dict(availability="UNAVAILABLE", truncated=None, withheld=False), []
    if isinstance(value, str) and historical:
        value = dict(availability="captured", text=value, truncated=None)
    if not isinstance(value, Mapping):
        raise ValueError("invalid private native diagnostic stream")
    state = value.get("availability")
    if state not in {"captured", "empty", "unavailable"}:
        raise ValueError("invalid private native diagnostic availability")
    if state != "captured":
        if set(value) - {"availability", "reason"} or value.get("reason") not in {None, "invalid_utf8", "unsupported_type"}:
            raise ValueError("invalid unavailable native diagnostic")
        return dict(availability=state.upper(), truncated=False if state == "empty" else None, withheld=False), []
    text, truncated = value.get("text"), value.get("truncated")
    if (set(value) != {"availability", "text", "truncated"} or not isinstance(text, str)
            or (type(truncated) is not bool and not (historical and truncated is None))):
        raise ValueError("invalid captured native diagnostic")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    causes = [cause for line in lines if (cause := _safe_diagnostic_cause(line)) is not None]
    return dict(availability="CAPTURED", truncated=truncated, withheld=len(causes) < len(lines)), causes


def _project_diagnostics(data: Mapping[str, Any]) -> dict[str, Any]:
    error = data["error"]
    validate_private_native_captures(error.get("native_captures", {}))
    if "exit_code" in error and error["exit_code"] is not None and type(error["exit_code"]) is not int:
        raise ValueError("native exit code must be an integer or null")
    timeout = error.get("native_timeout_seconds")
    if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 31536000
                                or error.get("native_kind") != "TIMEOUT"):
        raise ValueError("invalid private native timeout fact")
    causes: set[tuple[str, str, str]] = set()
    message = error["message"]
    if not isinstance(message, str):
        raise ValueError("invalid private diagnostic message")
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    safe_message = [cause for line in lines if (cause := _safe_diagnostic_cause(line)) is not None]
    causes.update((code, summary, "ERROR_MESSAGE") for code, summary in safe_message)
    if "native_kind" in error:
        if error["native_kind"] != "TIMEOUT":
            raise ValueError("invalid private native error kind")
        causes.add(("NATIVE_TIMEOUT", _SAFE_CAUSES["NATIVE_TIMEOUT"], "NATIVE_BOUNDARY"))
    withheld = len(safe_message) < len(lines)
    native = error.get("native_diagnostics", {})
    if not isinstance(native, Mapping) or set(native) - {"limit_chars", "stdout", "stderr"}:
        raise ValueError("invalid private native diagnostics")
    if "limit_chars" in native and (type(native["limit_chars"]) is not int or native["limit_chars"] < 1):
        raise ValueError("invalid native diagnostic limit")
    streams = {}
    for name in ("stdout", "stderr"):
        if name not in native:
            if "limit_chars" in native:
                raise ValueError("missing private native diagnostic stream")
            stream, facts = dict(availability="UNKNOWN", truncated=None, withheld=False), []
        else:
            stream, facts = _diagnostic_stream(native[name], historical="limit_chars" not in native)
        streams[name] = stream
        withheld |= stream["withheld"]
        causes.update((code, summary, "NATIVE_" + name.upper()) for code, summary in facts)
    executor = error.get("executor_diagnostics")
    if executor is not None:
        if not isinstance(executor, Mapping) or set(executor) - {"unresolved", "stdout", "stderr"}:
            raise ValueError("invalid private Executor diagnostics")
        unresolved = executor.get("unresolved", [])
        if not isinstance(unresolved, list) or any(not isinstance(item, str) for item in unresolved):
            raise ValueError("invalid private Executor unresolved diagnostics")
        if unresolved:
            causes.add(("COMPLETION_INCOMPLETE", _SAFE_CAUSES["COMPLETION_INCOMPLETE"], "EXECUTOR_UNRESOLVED"))
            for item in unresolved:
                fact = _safe_diagnostic_cause(item)
                if fact:
                    causes.add((*fact, "EXECUTOR_UNRESOLVED"))
                else:
                    withheld = True
        withheld |= bool(executor.get("stdout") or executor.get("stderr"))
    for outcome in error.get("verification", []):
        if outcome["exit_code"] != 0:
            causes.add(("VERIFICATION_FAILED", _SAFE_CAUSES["VERIFICATION_FAILED"], "VERIFICATION_OUTCOME"))
        fact = _safe_diagnostic_cause(outcome["summary"])
        if fact:
            causes.add((*fact, "VERIFICATION_SUMMARY"))
        elif outcome["summary"]:
            withheld = True
        reference = outcome.get("verification", {})
        if any(reference.get(side, {}).get("complete") is False for side in ("candidate", "base")):
            causes.add(("VERIFICATION_OBSERVATION_INCOMPLETE", _SAFE_CAUSES["VERIFICATION_OBSERVATION_INCOMPLETE"], "VERIFICATION_REPORT"))
        for report in reference.get("candidate", {}).get("reports", []):
            for key in ("detail", "captured_output"):
                text = report.get(key, "")
                if text is None and key == "detail" and reference["candidate"]["complete"] is False:
                    continue
                if not isinstance(text, str):
                    raise ValueError("invalid private verification diagnostic text")
                for line in text.splitlines():
                    if not line.strip():
                        continue
                    fact = _safe_diagnostic_cause(line.strip())
                    if fact:
                        causes.add((*fact, "VERIFICATION_REPORT"))
                    else:
                        withheld = True
    candidate = data["candidate"]
    if data.get("phase") == "VERIFICATION" and not error.get("verification"):
        causes.add(("VERIFICATION_UNAVAILABLE", _SAFE_CAUSES["VERIFICATION_UNAVAILABLE"], "VERIFICATION_OUTCOME"))
    if candidate["dirty"]:
        causes.add(("REPOSITORY_DIRTY", _SAFE_CAUSES["REPOSITORY_DIRTY"], "CANDIDATE"))
    if candidate["outside_task_scope"]:
        causes.add(("SCOPE_VIOLATION", _SAFE_CAUSES["SCOPE_VIOLATION"], "CANDIDATE"))
    workspace = data.get("workspace_state")
    if workspace is not None:
        if (not isinstance(workspace, Mapping) or set(workspace) != {"dirty_entry_count", "outside_task_scope_count"}
                or any(type(n) is not int or n < 0 for n in workspace.values())
                or workspace["outside_task_scope_count"] > workspace["dirty_entry_count"]
                or bool(workspace["dirty_entry_count"]) is not candidate["dirty"]):
            raise ValueError("invalid private candidate workspace state")
    priority = {"NATIVE_BOUNDARY": 0, "CANDIDATE": 1, "VERIFICATION_OUTCOME": 2}
    selected = sorted(causes, key=lambda fact: (priority.get(fact[2], 3), fact))[:16]
    public_causes = [dict(code=code, summary=summary, origin=origin) for code, summary, origin in sorted(selected)]
    partial = (withheld or len(causes) > 16 or any(item["truncated"] is True for item in streams.values())
               or any(cause[0] == "VERIFICATION_OBSERVATION_INCOMPLETE" for cause in causes))
    return dict(format="AIOS_FAILURE_DIAGNOSTICS_V1", authority="RUNTIME_OBSERVATION_DATA",
        native_timeout_seconds=timeout,
        verification_availability=("RECORDED" if error.get("verification") else "UNAVAILABLE" if data.get("phase") == "VERIFICATION" else "NOT_APPLICABLE"),
        native_exit=dict(code=error.get("exit_code"), state=("KNOWN" if type(error.get("exit_code")) is int
                        else "UNKNOWN" if "exit_code" in error else "UNAVAILABLE")),
        safe_causes=public_causes, native_streams=streams,
        message_availability=("EMPTY" if not lines else "RESTRICTED" if not safe_message else "PARTIAL" if len(safe_message) < len(lines) else "AVAILABLE"),
        candidate_workspace=(dict(availability="KNOWN", **workspace) if workspace is not None
                             else dict(availability="UNKNOWN", dirty_entry_count=None, outside_task_scope_count=None)),
        completeness=("PARTIAL" if partial and public_causes else "RESTRICTED" if withheld
                      else "PARTIAL" if partial else "UNKNOWN" if not native
                      or any(item["truncated"] is None for item in streams.values()) else "COMPLETE"),
        withheld=withheld, causes_truncated=len(causes) > 16,
        private_access=dict(availability="LOCAL_ONLY", delivered_to_brain=False))


def validate_private_native_captures(value: Any) -> None:
    if not isinstance(value, dict) or set(value) - {"stdout", "stderr"}:
        raise ValueError("invalid private native capture population")
    for capture in value.values():
        if capture == {"availability": "UNAVAILABLE"}:
            continue
        if (not isinstance(capture, dict) or set(capture) != {"availability", "raw_path", "raw_digest", "raw_bytes"}
                or capture["availability"] != "AVAILABLE"
                or not isinstance(capture["raw_path"], str) or not capture["raw_path"]
                or not _is_digest(capture["raw_digest"])
                or type(capture["raw_bytes"]) is not int or capture["raw_bytes"] < 0):
            raise ValueError("invalid private native capture identity")


def validate_failure_diagnostics(value: Any) -> None:
    fields = {"format", "authority", "native_exit", "safe_causes", "native_streams", "message_availability",
              "candidate_workspace", "completeness", "withheld", "causes_truncated", "private_access", "native_timeout_seconds", "verification_availability"}
    if (not isinstance(value, dict) or set(value) != fields
            or value["format"] != "AIOS_FAILURE_DIAGNOSTICS_V1" or value["authority"] != "RUNTIME_OBSERVATION_DATA"
            or value["completeness"] not in {"COMPLETE", "PARTIAL", "RESTRICTED", "UNKNOWN", "UNAVAILABLE"}
            or value["message_availability"] not in {"AVAILABLE", "PARTIAL", "RESTRICTED", "EMPTY", "UNAVAILABLE", "UNKNOWN"}
            or value["verification_availability"] not in {"RECORDED", "UNAVAILABLE", "NOT_APPLICABLE"}
            or type(value["withheld"]) is not bool or type(value["causes_truncated"]) is not bool
            or not isinstance(value["private_access"], dict) or set(value["private_access"]) != {"availability", "delivered_to_brain"}
            or value["private_access"]["availability"] != "LOCAL_ONLY" or value["private_access"]["delivered_to_brain"] is not False):
        raise ValueError("invalid public failure diagnostic fields")
    timeout = value["native_timeout_seconds"]
    if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 31536000):
        raise ValueError("invalid public native timeout fact")
    native = value["native_exit"]
    if (not isinstance(native, dict) or set(native) != {"code", "state"}
            or native["state"] not in {"KNOWN", "UNKNOWN", "UNAVAILABLE"}
            or (type(native["code"]) is int) != (native["state"] == "KNOWN")
            or (native["state"] != "KNOWN" and native["code"] is not None)):
        raise ValueError("invalid public native exit facts")
    streams = value["native_streams"]
    if not isinstance(streams, dict) or set(streams) != {"stdout", "stderr"}:
        raise ValueError("invalid public native stream facts")
    for stream in streams.values():
        if (not isinstance(stream, dict) or set(stream) != {"availability", "truncated", "withheld"}
                or stream["availability"] not in {"CAPTURED", "EMPTY", "UNAVAILABLE", "UNKNOWN"}
                or (stream["truncated"] is not None and type(stream["truncated"]) is not bool)
                or type(stream["withheld"]) is not bool):
            raise ValueError("invalid public native stream state")
        if (stream["availability"] == "EMPTY" and (stream["truncated"] is not False or stream["withheld"] is not False)
                or stream["availability"] in {"UNAVAILABLE", "UNKNOWN"} and (stream["truncated"] is not None or stream["withheld"] is not False)
                or stream["withheld"] and not value["withheld"]):
            raise ValueError("ambiguous public native stream state")
    if value["completeness"] == "COMPLETE" and (value["withheld"] or value["causes_truncated"] or any(s["truncated"] is not False for s in streams.values())):
        raise ValueError("untruthful public diagnostic completeness")
    workspace = value["candidate_workspace"]
    if not isinstance(workspace, dict) or set(workspace) != {"availability", "dirty_entry_count", "outside_task_scope_count"}:
        raise ValueError("invalid public candidate workspace facts")
    if workspace["availability"] == "KNOWN":
        if (any(type(workspace[key]) is not int or workspace[key] < 0 for key in ("dirty_entry_count", "outside_task_scope_count"))
                or workspace["outside_task_scope_count"] > workspace["dirty_entry_count"]):
            raise ValueError("invalid public dirty population")
    elif workspace != dict(availability="UNKNOWN", dirty_entry_count=None, outside_task_scope_count=None):
        raise ValueError("invalid public unknown candidate state")
    causes = value["safe_causes"]
    if not isinstance(causes, list) or len(causes) > 16:
        raise ValueError("unbounded public safe causes")
    previous = None
    for cause in causes:
        if (not isinstance(cause, dict) or set(cause) != {"code", "summary", "origin"}
                or cause["origin"] not in {"ERROR_MESSAGE", "NATIVE_STDOUT", "NATIVE_STDERR", "NATIVE_BOUNDARY", "EXECUTOR_UNRESOLVED", "VERIFICATION_OUTCOME", "VERIFICATION_SUMMARY", "VERIFICATION_REPORT", "CANDIDATE"}
                or not isinstance(cause["summary"], str)
                or not (cause["code"] in _SAFE_CAUSES and cause["summary"] == _SAFE_CAUSES[cause["code"]]
                        or cause["code"] == "MODULE_UNAVAILABLE" and _safe_diagnostic_cause("ModuleNotFoundError: No module named '" + cause["summary"].removeprefix("Python module unavailable: ") + "'") == (cause["code"], cause["summary"])
                        or cause["code"] == "ASSERTION_FAILED" and _safe_diagnostic_cause(cause["summary"]) == (cause["code"], cause["summary"]))):
            raise ValueError("unsafe public diagnostic cause")
        key = (cause["code"], cause["summary"], cause["origin"])
        if previous is not None and key <= previous:
            raise ValueError("duplicate or unordered public diagnostic causes")
        previous = key
    if timeout is not None and not any(cause["code"] == "NATIVE_TIMEOUT" and cause["origin"] == "NATIVE_BOUNDARY" for cause in causes):
        raise ValueError("public timeout has no native boundary source")


def _private_identity(value: Any) -> dict[str, str]:
    return {"private_identity_digest": _failure_digest(value)}


def _project_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    from .verification_contract import (
        MAX_CANONICAL_REPORTS, MAX_CANONICAL_FAILURES, MAX_CANONICAL_NODEID_CHARS,
        MAX_FAILURE_DETAIL_CHARS,
    )
    reports = value.get("reports")
    if (not isinstance(reports, list) or len(reports) > MAX_CANONICAL_REPORTS
            or type(value.get("complete")) is not bool or type(value.get("unstable")) is not bool
            or value["complete"] and value["unstable"]
            or type(value.get("exit_code")) is not int
            or type(value.get("failure_count")) is not int or value["failure_count"] < 0):
        raise _FailureProjectionError("FAILURE_OBSERVATION_COUNTS_INVALID")
    counts = dict(PASS=0, FAIL=0, SKIP=0)
    failed = []
    reasons: set[str] = set()
    nodes: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for report in reports:
        if not isinstance(report, dict) or report.get("outcome") not in counts:
            raise _FailureProjectionError("FAILURE_OBSERVATION_COUNTS_INVALID")
        node, phase = report.get("nodeid"), report.get("phase")
        if (not isinstance(node, str) or not node or len(node) > MAX_CANONICAL_NODEID_CHARS
                or not isinstance(phase, str) or phase not in {"collect", "setup", "call", "teardown"}
                or "\\" in node.split("::", 1)[0]
                or (node, phase) in seen):
            raise _FailureProjectionError("FAILURE_OBSERVATION_IDENTITY_INVALID")
        seen.add((node, phase))
        nodes.add(node)
        counts[report["outcome"]] += 1
        if report["outcome"] == "FAIL":
            detail, fingerprint = report.get("detail"), report.get("fingerprint")
            if (detail is not None and not isinstance(detail, str)
                    or fingerprint is not None and not _is_digest(fingerprint)
                    or detail and fingerprint is not None and fingerprint != _failure_digest(detail)):
                raise _FailureProjectionError("FAILURE_OBSERVATION_DETAIL_INVALID")
            detail_state = "UNAVAILABLE" if not detail else "OVERSIZED" if len(detail) > MAX_FAILURE_DETAIL_CHARS else "AVAILABLE"
            fingerprint_state = "UNAVAILABLE" if fingerprint is None else "UNVERIFIED" if not detail else "AVAILABLE"
            if detail_state != "AVAILABLE":
                reasons.add("MISSING_FAILURE_DETAIL" if detail_state == "UNAVAILABLE" else "OVERSIZED_FAILURE_DETAIL")
            if fingerprint_state == "UNAVAILABLE":
                reasons.add("MISSING_FAILURE_FINGERPRINT")
            failed.append(dict(nodeid_digest=_failure_digest(node), phase=phase,
                               fingerprint=fingerprint, detail_availability=detail_state,
                               fingerprint_availability=fingerprint_state))
    if (counts["FAIL"] > MAX_CANONICAL_FAILURES or value["failure_count"] < counts["FAIL"]
            or value["exit_code"] == 0 and (counts["FAIL"] or value["failure_count"])):
        raise _FailureProjectionError("FAILURE_OBSERVATION_COUNTS_INVALID")
    if value["failure_count"] != counts["FAIL"]:
        reasons.add("PARTIAL_FAILURE_POPULATION")
    if value["exit_code"] == 1 and not counts["FAIL"]:
        reasons.add("UNAVAILABLE_FAILURE_POPULATION")
    if reasons and value["complete"]:
        raise _FailureProjectionError("FAILURE_OBSERVATION_DETAIL_INVALID")
    if not reasons:
        failed = [{key: item[key] for key in ("nodeid_digest", "phase", "fingerprint")} for item in failed]
    projected = dict(public_observation=PUBLIC_INCOMPLETE_OBSERVATION_FORMAT if reasons else PUBLIC_OBSERVATION_FORMAT,
        observation_digest=_failure_digest(value), subject_sha=value["subject_sha"],
        command=_public_command(value["command"]), profile=_private_identity(value["profile"]),
        toolchain=_private_identity(value["toolchain"]), complete=value["complete"],
        unstable=value["unstable"], exit_code=value["exit_code"], failure_count=value["failure_count"],
        report_count=len(reports), node_count=len(nodes), outcome_counts=counts,
        reports_digest=_failure_digest(reports),
        failed_reports=sorted(failed, key=lambda item: (item["nodeid_digest"], item["phase"])))
    if reasons:
        projected["diagnostic_incompleteness"] = dict(proof="UNAVAILABLE", reasons=sorted(reasons))
    return projected


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


def _project_failure(data: Mapping[str, Any]) -> dict[str, Any]:
    """Produce FAILURE metadata; omitted proof remains bound, never recreated."""
    if "public_projection" in data:
        validate_public_failure(data)
        return dict(data)
    if data.get("error", {}).get("message") == "Protected Runtime diagnostics remain local.":
        _validate_legacy_failure_public(data)
        return dict(data)
    from .unified_state import _validated_private_public_view
    # Validate the original role, owner, conditions and observation digests first.
    _validated_private_public_view(data, "METADATA")
    projected = {key: data[key] for key in (
        "kind", "run_id", "task", "executor", "base_sha", "failed_head_sha",
        "candidate", "phase", "continuation_of") if key in data}
    error = data["error"]
    if set(error) - {"type", "message", "exit_code", "native_kind", "native_timeout_seconds", "native_diagnostics", "native_captures", "executor_diagnostics",
                     "verification", "evidence", "evidence_raw_digests", "metadata", "embedded_package"}:
        raise ValueError("undeclared private FAILURE error fields")
    if set(data) & {"stdout", "stderr", "captured_output", "traceback", "raw_path", "workspace"}:
        raise ValueError("undeclared private FAILURE capture fields")
    outcomes = []
    for item in error.get("verification", []):
        outcome = dict(command=_public_command(item["command"]), exit_code=item["exit_code"],
                       summary="Runtime verification exit code " + str(item["exit_code"]))
        if "verification" in item:
            outcome["verification"] = _project_reference(item["verification"])
        outcomes.append(outcome)
    projected["error"] = dict(type=error["type"], message="Protected Runtime diagnostics remain local.",
                              verification=outcomes)
    projected["error"]["diagnostics"] = _project_diagnostics(data)
    if "exit_code" in error:
        projected["error"]["exit_code"] = error["exit_code"]
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
    projected["public_projection"] = dict(format=PUBLIC_DIAGNOSTIC_FAILURE_FORMAT,
        private_failure_digest=_failure_digest(data), projection_digest=_failure_digest(projected),
        proof="PRIVATE_NON_PASS_FAILURE")
    validate_public_failure(projected)
    return projected


def _validate_legacy_failure_public(data: Mapping[str, Any]) -> None:
    """Keep lawful small redaction, rejecting undeclared public diagnostics."""
    allowed = {"kind", "run_id", "task", "executor", "base_sha", "failed_head_sha",
               "candidate", "phase", "continuation_of", "error"}
    if set(data) - allowed:
        raise ValueError("undeclared public FAILURE material")
    task = data.get("task")
    if task is not None and (not isinstance(task, dict) or set(task) != {"id", "revision"}
            or not isinstance(task["id"], str) or re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task["id"]) is None
            or type(task["revision"]) is not int or task["revision"] < 1):
        raise ValueError("invalid legacy public FAILURE TASK")
    error = data.get("error")
    if error is not None:
        if (not isinstance(error, dict) or set(error) - {"type", "message", "verification", "evidence", "exit_code"}
                or not isinstance(error.get("type"), str)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", error["type"]) is None
                or _credential_shape(error["type"])
                or error.get("message") != "Protected Runtime diagnostics remain local."):
            raise ValueError("undeclared public FAILURE diagnostics")
        if "exit_code" in error and error["exit_code"] is not None and type(error["exit_code"]) is not int:
            raise ValueError("invalid public native exit code")
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
    if candidate is not None and not isinstance(candidate, dict):
        raise ValueError("invalid legacy public FAILURE candidate")
    if isinstance(candidate, dict):
        if set(candidate) - {"transportable", "repairable", "dirty", "descends_from_base",
                             "changed_files", "outside_task_scope"}:
            raise ValueError("undeclared public FAILURE candidate material")
        if any(type(candidate[key]) is not bool for key in ("transportable", "repairable", "dirty", "descends_from_base") if key in candidate):
            raise ValueError("invalid legacy public FAILURE candidate flag type")
        for key in ("changed_files", "outside_task_scope"):
            paths = candidate.get(key, [])
            if (not isinstance(paths, list) or any(not isinstance(path, str)
                    or re.fullmatch(r"[A-Za-z0-9_./@ -]{1,4096}", path) is None
                    or path.startswith("/") or ".." in path.split("/") or _credential_shape(path) for path in paths)):
                raise ValueError("unsafe public FAILURE repository paths")

    def diagnostics(value: Any) -> None:
        if isinstance(value, str) and _credential_shape(value):
            raise ValueError("credential-shaped public FAILURE material")
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


def public_failure(content: bytes) -> bytes:
    """FAILURE-only finite budget; ordinary transport retains its existing cap."""
    data = _public_document(content, max_bytes=PUBLIC_FAILURE_MAX_BYTES)
    if data.get("kind") != "FAILURE":
        raise ReviewTransportError("public FAILURE kind is invalid")
    try:
        error = data.get("error")
        if (isinstance(error, dict)
                and error.get("message") == "Protected Runtime diagnostics remain local."
                and "public_projection" not in data):
            # A purported public original must already be lawful. Re-redacting
            # it would conceal contaminated or forged public diagnostics.
            _validate_legacy_failure_public(data)
        outcomes = error.get("verification", []) if isinstance(error, dict) else []
        if not _has_failure_observations(data) and isinstance(outcomes, list) and any(
            isinstance(item, dict) and isinstance(item.get("verification"), dict)
            and "candidate" in item["verification"] for item in outcomes
        ):
            raise ValueError("public FAILURE observation has no admitted terminal binding")
        projected = (_project_failure(data) if _has_failure_observations(data)
                     else _public_metadata_value(data))
        # A diagnostic-only record must still fit the ordinary legacy format.
        if not _has_failure_observations(data) and len(content) > PUBLIC_ARTIFACT_MAX_BYTES:
            raise ValueError("large FAILURE requires a private observation projection")
        if not _has_failure_observations(data):
            _validate_legacy_failure_public(projected)
        return _public_bytes(projected, max_bytes=PUBLIC_FAILURE_MAX_BYTES)
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        error = ReviewTransportError("invalid public FAILURE projection")
        fallback = "FAILURE_REQUIRED_FIELD_MISSING" if isinstance(exc, KeyError) else "FAILURE_FIELD_TYPE_INVALID" if isinstance(exc, (TypeError, AttributeError)) else "FAILURE_PROJECTION_REJECTED"
        error.reason_code = getattr(exc, "reason_code", _FAILURE_BLOCKER_MESSAGES.get(str(exc), fallback))
        raise error from exc


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
            or marker["format"] not in {PUBLIC_FAILURE_FORMAT, PUBLIC_DIAGNOSTIC_FAILURE_FORMAT} or marker["proof"] != "PRIVATE_NON_PASS_FAILURE"
            or not _is_digest(marker["private_failure_digest"])
            or marker["projection_digest"] != _failure_digest({k: v for k, v in data.items() if k != "public_projection"})):
        raise ValueError("invalid public FAILURE projection identity")
    _public_document(_public_bytes(data, max_bytes=PUBLIC_FAILURE_MAX_BYTES), max_bytes=PUBLIC_FAILURE_MAX_BYTES)

    def match(value: Any, pattern: str) -> bool:
        return isinstance(value, str) and re.fullmatch(pattern, value) is not None and not _credential_shape(value)

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
                or p.startswith("/") or ".." in p.split("/") or _credential_shape(p) for p in paths)
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
        incomplete = isinstance(value, dict) and value.get("public_observation") == PUBLIC_INCOMPLETE_OBSERVATION_FORMAT
        if incomplete:
            fields.add("diagnostic_incompleteness")
        if (not isinstance(value, dict) or set(value) != fields
                or value["public_observation"] not in {PUBLIC_OBSERVATION_FORMAT, PUBLIC_INCOMPLETE_OBSERVATION_FORMAT}
                or incomplete and (marker["format"] != PUBLIC_DIAGNOSTIC_FAILURE_FORMAT or value["complete"] is not False)
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
                or not 0 <= counts["FAIL"] <= MAX_CANONICAL_FAILURES
                or value["failure_count"] < counts["FAIL"]
                or not incomplete and value["failure_count"] != counts["FAIL"]
                or (value["exit_code"] == 0 and value["failure_count"])
                or (value["exit_code"] == 1 and not counts["FAIL"] and not incomplete)
                or not isinstance(value["failed_reports"], list) or len(value["failed_reports"]) != counts["FAIL"]):
            raise ValueError("invalid public FAILURE report counts")
        previous = None
        reasons = set()
        for report in value["failed_reports"]:
            report_fields = {"nodeid_digest", "phase", "fingerprint"}
            if incomplete:
                report_fields |= {"detail_availability", "fingerprint_availability"}
            if (not isinstance(report, dict) or set(report) != report_fields
                    or not _is_digest(report["nodeid_digest"])
                    or not incomplete and not _is_digest(report["fingerprint"])
                    or not isinstance(report["phase"], str) or report["phase"] not in {"collect", "setup", "call", "teardown"}):
                raise ValueError("invalid public FAILURE failed identity")
            if incomplete:
                detail, fingerprint = report["detail_availability"], report["fingerprint_availability"]
                if (detail not in {"AVAILABLE", "UNAVAILABLE", "OVERSIZED"}
                        or fingerprint not in {"AVAILABLE", "UNAVAILABLE", "UNVERIFIED"}
                        or fingerprint == "UNAVAILABLE" and report["fingerprint"] is not None
                        or fingerprint != "UNAVAILABLE" and not _is_digest(report["fingerprint"])
                        or fingerprint == "UNVERIFIED" and detail != "UNAVAILABLE"
                        or fingerprint == "AVAILABLE" and detail == "UNAVAILABLE"):
                    raise ValueError("invalid public FAILURE detail availability")
                if detail != "AVAILABLE":
                    reasons.add("MISSING_FAILURE_DETAIL" if detail == "UNAVAILABLE" else "OVERSIZED_FAILURE_DETAIL")
                if fingerprint == "UNAVAILABLE":
                    reasons.add("MISSING_FAILURE_FINGERPRINT")
            key = (report["nodeid_digest"], report["phase"])
            if previous is not None and key <= previous:
                raise ValueError("duplicate public FAILURE failed identity")
            previous = key
        if len({item["nodeid_digest"] for item in value["failed_reports"]}) > value["node_count"]:
            raise ValueError("invalid public FAILURE failed node count")
        if incomplete:
            if value["failure_count"] != counts["FAIL"]:
                reasons.add("PARTIAL_FAILURE_POPULATION")
            if value["exit_code"] == 1 and not counts["FAIL"]:
                reasons.add("UNAVAILABLE_FAILURE_POPULATION")
            if not reasons or value["diagnostic_incompleteness"] != dict(proof="UNAVAILABLE", reasons=sorted(reasons)):
                raise ValueError("untruthful public FAILURE diagnostic incompleteness")

    error = data["error"]
    if (not isinstance(error, dict) or not {"type", "message", "verification"} <= error.keys()
            or set(error) - {"type", "message", "verification", "evidence_identities", "exit_code", "diagnostics"}
            or not match(error["type"], r"[A-Za-z_][A-Za-z0-9_.]{0,127}")
            or _credential_shape(error["type"])
            or error["message"] != "Protected Runtime diagnostics remain local."
            or not isinstance(error["verification"], list)):
        raise ValueError("invalid public FAILURE diagnostics")
    if marker["format"] == PUBLIC_DIAGNOSTIC_FAILURE_FORMAT:
        validate_failure_diagnostics(error.get("diagnostics"))
        native = error["diagnostics"]["native_exit"]
        if (("exit_code" in error) != (native["state"] != "UNAVAILABLE")
                or error.get("exit_code") != native["code"]
                or ("exit_code" in error and error["exit_code"] is not None and type(error["exit_code"]) is not int)):
            raise ValueError("public FAILURE native exit binding mismatch")
        workspace = error["diagnostics"]["candidate_workspace"]
        if workspace["availability"] == "KNOWN" and bool(workspace["dirty_entry_count"]) is not candidate["dirty"]:
            raise ValueError("public FAILURE dirty state mismatch")
        expected_verification = "RECORDED" if error["verification"] else "UNAVAILABLE" if data["phase"] == "VERIFICATION" else "NOT_APPLICABLE"
        if error["diagnostics"]["verification_availability"] != expected_verification:
            raise ValueError("public FAILURE verification availability mismatch")
    elif {"diagnostics", "exit_code"} & error.keys():
        raise ValueError("diagnostics require the bound public FAILURE format")
    identities = error.get("evidence_identities", [])
    if not isinstance(identities, list):
        raise ValueError("invalid public FAILURE evidence identities")
    seen = set()
    for item in identities:
        if (not isinstance(item, dict) or set(item) != {"evidence_id", "run_id", "subject_sha", "record_digest"}
                or not match(item["evidence_id"], r"[A-Za-z0-9_-]{1,96}") or _credential_shape(item["evidence_id"]) or item["evidence_id"] in seen
                or item["run_id"] != data["run_id"] or item["subject_sha"] != data["failed_head_sha"]
                or not _is_digest(item["record_digest"])):
            raise ValueError("invalid public FAILURE evidence identity binding")
        seen.add(item["evidence_id"])
    interrupted_or_unavailable = (marker["format"] == PUBLIC_DIAGNOSTIC_FAILURE_FORMAT
        and not error["verification"] and error["diagnostics"]["verification_availability"] == "UNAVAILABLE")
    if data["phase"] == "VERIFICATION" and not interrupted_or_unavailable and not any(
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
                or not match(ref["evidence_id"], r"[A-Za-z0-9_-]{1,96}") or _credential_shape(ref["evidence_id"])
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


def failure_document(content: bytes) -> Mapping[str, Any]:
    """Shared finite JSON/depth/schema gate for exact canonical FAILURE readers."""
    data = _public_document(content, max_bytes=PUBLIC_FAILURE_MAX_BYTES)
    if data.get("kind") != "FAILURE":
        raise ReviewTransportError("invalid canonical FAILURE kind")
    error = data.get("error")
    if error is not None:
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            raise ReviewTransportError("invalid canonical FAILURE diagnostics")
        if "exit_code" in error and error["exit_code"] is not None and type(error["exit_code"]) is not int:
            raise ReviewTransportError("invalid canonical FAILURE native exit code")
    if ("public_projection" not in data and isinstance(error, dict)
            and error.get("message") == "Protected Runtime diagnostics remain local."):
        def captured(value: Any) -> bool:
            if isinstance(value, list):
                return any(captured(item) for item in value)
            return isinstance(value, dict) and (
                bool(set(value) & {"captured_output", "stdout", "stderr", "traceback", "longrepr",
                                   "native_diagnostics", "native_captures", "executor_diagnostics"})
                or any(captured(item) for item in value.values()))

        if captured(data):
            raise ReviewTransportError("private captured output in public FAILURE")
        if "public_projection" not in data:
            try:
                # Match the prospective small FAILURE writer's privacy gate;
                # historical private originals retain their exact read path.
                _validate_legacy_failure_public(data)
            except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
                raise ReviewTransportError("invalid canonical public FAILURE privacy") from exc
    if "public_projection" in data:
        try:
            validate_public_failure(data)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
            raise ReviewTransportError("invalid canonical public FAILURE projection") from exc
    return data


def failure_diagnostic_handoff(
    failure: Mapping[str, Any], *, artifact_sha: str | None = None,
    private_available: bool = False, transport_blocker: str | None = None,
) -> dict[str, Any]:
    """Bounded useful facts from an already authenticated Runtime terminal.

    Callers establish canonical/local RUN binding first. A digest is provenance,
    never authentication by itself, and this summary grants no repair authority.
    """
    public = failure_document(public_failure(_public_bytes(failure, max_bytes=PUBLIC_FAILURE_MAX_BYTES)))
    error = public.get("error", {})
    diagnostic = error.get("diagnostics")
    if diagnostic is None:
        # Historical protected records cannot recover omitted native facts.
        # Their known typed outcomes and candidate facts remain useful.
        original = dict(public)
        original["error"] = dict(error, message="")
        original["error"].pop("evidence", None)
        diagnostic = _project_diagnostics(original)
        diagnostic["message_availability"] = "UNAVAILABLE"
        diagnostic["completeness"] = "PARTIAL" if diagnostic["safe_causes"] else "UNAVAILABLE"
    references: dict[str, dict[str, Any]] = {}
    for item in error.get("evidence_identities", []):
        references[item["evidence_id"]] = dict(item)
    for item in error.get("verification", []):
        ref = item.get("verification")
        if isinstance(ref, Mapping):
            references[ref["evidence_id"]] = dict(evidence_id=ref["evidence_id"], run_id=ref["run_id"],
                subject_sha=public["failed_head_sha"], record_digest=ref["private_reference_digest"])
    for item in error.get("evidence", []):
        references[item["evidence_id"]] = dict(evidence_id=item["evidence_id"], run_id=item["run_id"],
            subject_sha=item["subject_sha"], record_digest=_failure_digest(item))
    marker = public.get("public_projection", {})
    verification = []
    command_chars = 0
    for item in error.get("verification", []):
        if len(verification) == 32 or command_chars + len(item["command"]) > 16384:
            break
        command_chars += len(item["command"])
        verification.append(dict(command=item["command"], exit_code=item["exit_code"]))
    body = dict(format="AIOS_FAILURE_HANDOFF_V1", authority="RUNTIME_OBSERVATION_DATA",
        source=dict(run_id=public["run_id"], task=public["task"], subject_sha=public["failed_head_sha"],
                    artifact_sha=artifact_sha, private_failure_digest=marker.get("private_failure_digest"),
                    public_failure_digest=_failure_digest(public)),
        phase=public.get("phase", "UNKNOWN"), error_type=error.get("type"),
        candidate=public["candidate"], diagnostics=diagnostic,
        verification=verification,
        evidence_identities=[references[key] for key in sorted(references)[:32]],
        identities_truncated=len(references) > 32, verification_truncated=len(error.get("verification", [])) > len(verification),
        private_evidence=dict(availability="AVAILABLE" if private_available else "RESTRICTED",
                              channel="AUTHORIZED_LOCAL_RUNTIME_WORKSPACE", delivered_to_brain=False),
        transport_blocker=transport_blocker)
    validate_failure_handoff(body)
    return body


FAILURE_TRANSPORT_BLOCKERS = frozenset({"FAILURE_PROJECTION_REJECTED", "ATTENTION_DELIVERY_FAILED",
    "ARTIFACT_IDENTITY_CONFLICT", "REMOTE_QUERY_UNAVAILABLE", "REMOTE_PUBLICATION_FAILED", "FAILURE_TRANSPORT_UNAVAILABLE",
    "FAILURE_OBSERVATION_COUNTS_INVALID", "FAILURE_OBSERVATION_IDENTITY_INVALID", "FAILURE_OBSERVATION_DETAIL_INVALID",
    "FAILURE_REQUIRED_FIELD_MISSING", "FAILURE_FIELD_TYPE_INVALID", *_FAILURE_BLOCKER_MESSAGES.values()})


def validate_failure_context(body: Any) -> None:
    if isinstance(body, dict) and body.get("format") == "AIOS_FAILURE_HANDOFF_V1":
        validate_failure_handoff(body)
        return
    fields = {"format", "run_id", "subject_sha", "availability", "reason_code"}
    if (not isinstance(body, dict) or set(body) != fields
            or body["format"] != "AIOS_FAILURE_DIAGNOSTIC_GAP_V1"
            or body["availability"] != "UNAVAILABLE"
            or body["reason_code"] != "INVALID_OR_MISSING_DIAGNOSTIC_SOURCE"
            or not isinstance(body["run_id"], str) or _credential_shape(body["run_id"])
            or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", body["run_id"]) is None
            or not isinstance(body["subject_sha"], str) or re.fullmatch(r"[0-9a-f]{40}", body["subject_sha"]) is None):
        raise ValueError("invalid failure diagnostic gap")


def validate_failure_handoff(body: Any) -> None:
    fields = {"format", "authority", "source", "phase", "error_type", "candidate", "diagnostics", "verification",
              "evidence_identities", "identities_truncated", "verification_truncated", "private_evidence", "transport_blocker"}
    if (not isinstance(body, dict) or set(body) != fields or body["format"] != "AIOS_FAILURE_HANDOFF_V1"
            or body["authority"] != "RUNTIME_OBSERVATION_DATA" or len(_public_bytes(body)) > 65536
            or body["phase"] not in {"EXECUTION", "COMPLETION_GATE", "VERIFICATION", "UNKNOWN"}
            or body["error_type"] is not None and (not isinstance(body["error_type"], str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", body["error_type"]) is None)
            or type(body["identities_truncated"]) is not bool or type(body["verification_truncated"]) is not bool
            or body["transport_blocker"] is not None and body["transport_blocker"] not in FAILURE_TRANSPORT_BLOCKERS):
        raise ValueError("invalid failure handoff")
    source = body["source"]
    fields = {"run_id", "task", "subject_sha", "artifact_sha", "private_failure_digest", "public_failure_digest"}
    if (not isinstance(source, dict) or set(source) != fields
            or not isinstance(source["run_id"], str) or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", source["run_id"]) is None
            or not isinstance(source["subject_sha"], str) or re.fullmatch(r"[0-9a-f]{40}", source["subject_sha"]) is None
            or source["artifact_sha"] is not None and (not isinstance(source["artifact_sha"], str) or re.fullmatch(r"[0-9a-f]{40}", source["artifact_sha"]) is None)
            or not _is_digest(source["public_failure_digest"])
            or source["private_failure_digest"] is not None and not _is_digest(source["private_failure_digest"])):
        raise ValueError("invalid failure handoff source")
    # Reuse the closed candidate/TASK/identity/privacy gate; no proof is asserted.
    check = dict(kind="FAILURE", run_id=source["run_id"], task=source["task"], executor="codex",
        base_sha=source["subject_sha"], failed_head_sha=source["subject_sha"], candidate=body["candidate"],
        phase="EXECUTION", error=dict(type=body["error_type"] or "UnknownError", message="Protected Runtime diagnostics remain local.", verification=[]))
    check["public_projection"] = dict(format=PUBLIC_FAILURE_FORMAT, private_failure_digest="0" * 64,
                                      projection_digest=_failure_digest(check), proof="PRIVATE_NON_PASS_FAILURE")
    validate_public_failure(check)
    validate_failure_diagnostics(body["diagnostics"])
    private = body["private_evidence"]
    if (not isinstance(private, dict) or set(private) != {"availability", "channel", "delivered_to_brain"}
            or private["availability"] not in {"AVAILABLE", "RESTRICTED", "UNAVAILABLE"}
            or private["channel"] != "AUTHORIZED_LOCAL_RUNTIME_WORKSPACE" or private["delivered_to_brain"] is not False):
        raise ValueError("invalid failure handoff private access state")
    for field in ("verification", "evidence_identities"):
        if not isinstance(body[field], list) or len(body[field]) > 32:
            raise ValueError("unbounded failure handoff identities")
    for item in body["verification"]:
        if not isinstance(item, dict) or set(item) != {"command", "exit_code"} or type(item["exit_code"]) is not int:
            raise ValueError("invalid failure handoff verification outcome")
        _public_command(item["command"])
    seen = set()
    for item in body["evidence_identities"]:
        if (not isinstance(item, dict) or set(item) != {"evidence_id", "run_id", "subject_sha", "record_digest"}
                or not isinstance(item["evidence_id"], str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", item["evidence_id"]) is None
                or _credential_shape(item["evidence_id"])
                or item["evidence_id"] in seen or item["subject_sha"] != source["subject_sha"]
                or not isinstance(item["run_id"], str) or re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", item["run_id"]) is None
                or _credential_shape(item["run_id"])
                or not _is_digest(item["record_digest"])):
            raise ValueError("invalid failure handoff evidence binding")
        seen.add(item["evidence_id"])


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

    if "public_projection" in failure:
        validate_public_failure(failure)
    task = failure.get("task")
    if (
        failure.get("kind") != "FAILURE"
        or failure.get("run_id") != run_id
        or not isinstance(task, Mapping)
        or dict(task) != {"id": task_id, "revision": task_revision}
        or type(task.get("revision")) is not int or type(task_revision) is not int
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
    code, content, _ = _git_cmd(repo, "show", f"{commit_sha}:{rel_path}", strip=False, allow_fail=True)
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

    required = {"kind", "execution"}
    allowed = required | {"predecessor", "execution_base", "acceptance"}
    if not required.issubset(document) or set(document).difference(allowed):
        raise ReviewTransportError(
            f"canonical REMEDIATION wrapper is invalid for {run_id}"
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
        or type(task.get("revision")) is not int or type(run.task.revision) is not int
        or terminal.get("executor") != run.executor
        or terminal.get("base_sha") != run.base_sha
        or run.head_sha not in (None, terminal.get("failed_head_sha"))
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
    repo: Path, *, task_id: str, task_revision: int
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
    remote = resolve_transport_remote(repo)
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
    remote = resolve_transport_remote(repo)
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
                input=(public_run(path.read_bytes()) if name == "run.json" else
                       public_result(path.read_bytes()) if name == "result.json" else
                       public_failure(path.read_bytes()) if name == "failure.json" else
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
    _validate_transport_affinity(repo, run_path.read_bytes(), head_sha,
                                 lineage_path.read_bytes() if lineage_path is not None else None)
    expected = {
        ".ai/transport/run.json": public_run(run_path.read_bytes()),
        ".ai/transport/failure.json": public_failure(failure_path.read_bytes()),
    }
    public_terminal = failure_document(expected[".ai/transport/failure.json"])
    if public_terminal.get("public_projection", {}).get("format") == PUBLIC_DIAGNOSTIC_FAILURE_FORMAT:
        task_ref = public_terminal["task"]
        _, _, _, bound_head = _bind_performance_terminal_identity(
            expected[".ai/transport/run.json"], expected[".ai/transport/failure.json"],
            ref=artifacts_ref, run_id=run_id, task_id=task_ref["id"], terminal_kind="FAILURE")
        if bound_head != head_sha or type(publish_candidate) is not bool or publish_candidate != public_terminal["candidate"]["transportable"]:
            raise ReviewTransportError("FAILURE transport candidate publication binding mismatch")
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
    if "public_projection" in public_terminal:
        success_refs = (f"refs/heads/aios/review/{run_id}", f"refs/heads/aios/artifacts/{run_id}")
        refs = _exact_remote_refs(repo, remote, candidate_ref, artifacts_ref, *success_refs)
        if any(ref in refs for ref in success_refs):
            raise ReviewTransportError("FAILURE transport has a conflicting RESULT identity")
    else:
        queried_refs = (candidate_ref, artifacts_ref) if publish_candidate else (artifacts_ref,)
        code, output, _ = _git_cmd(repo, "ls-remote", remote, *queried_refs, allow_fail=True)
        if code:
            raise ReviewTransportError(f"failed to query remote refs from {remote}")
        refs = {parts[1]: parts[0] for line in output.splitlines() if len(parts := line.split()) >= 2}
    if "public_projection" in public_terminal and not publish_candidate and candidate_ref in refs:
        raise ReviewTransportError("artifact-only FAILURE has a conflicting published candidate ref")
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
        if observed != expected and observed != legacy:
            from .unified_state import _matches_public_document
            compatible = True
            try:
                for path, original in legacy.items():
                    counterpart = observed[path]
                    if counterpart is None:
                        compatible = False
                        break
                    kind = "RUN" if path.endswith("run.json") else "METADATA"
                    reader = failure_document if path.endswith("failure.json") else lambda raw: _public_document(raw, max_bytes=PUBLIC_FAILURE_MAX_BYTES)
                    compatible &= _matches_public_document(reader(original), reader(counterpart), kind)
            except (ValueError, TypeError, KeyError, ReviewTransportError):
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
        commit = _create_named_artifacts_commit(
            repo, run_path=run_path, artifact_path=failure_path,
            artifact_name="failure.json", run_id=run_id,
            lineage_path=lineage_path,
            observation_path=observation_path,
            preverification_path=preverification_path,
            execution_profile_path=execution_profile_path,
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


_ADMISSION_ACTIONS = {
    "CONFIGURED_REPOSITORY_UNAVAILABLE": "Restore the configured control repository before admission.",
    "PRIMARY_SYNCHRONIZATION_REJECTED": "Supply a lawful clean control checkout or independently authorized reconciliation; preserve the divergent candidate.",
    "REPOSITORY_ADMISSION_REJECTED": "Resolve the control checkout admission blocker while preserving private candidate work.",
    "REPOSITORY_STATE_REJECTED": "Resolve the control checkout admission blocker while preserving private candidate work.",
    "REMOTE_TRANSPORT_UNAVAILABLE": "Restore authenticated canonical remote access before admission.",
    "AUTHORIZED_TASK_IDENTITY_MISMATCH": "Use the exact authorized canonical TASK revision and SHA.",
    "TASK_CONTRACT_REJECTED": "Resolve the canonical TASK contract rejection before admission.",
    "CANONICAL_LINEAGE_MISSING": "Restore the exact immutable canonical lineage required by this admission.",
    "CANONICAL_LINEAGE_AMBIGUOUS": "Resolve the competing canonical lineage identities before admission.",
    "CANONICAL_LINEAGE_INVALID": "Resolve the canonical lineage integrity rejection before admission.",
    "RUN_NAMESPACE_CONFLICT": "Resolve the exact RUN namespace conflict before admission.",
    "HISTORICAL_SUBJECT_REJECTED": "Restore the exact authorized historical subject without replacing private work.",
    "REUSABLE_STATE_REJECTED": "Resolve the exact reusable candidate binding rejection before admission.",
    "CUMULATIVE_BASE_REJECTED": "Resolve the authorized cumulative base binding before admission.",
    "INTEGRATION_REQUIRED": "Obtain the authorized canonical integration required by the current base.",
}


def safe_admission_message(message: str) -> str:
    """Portable pre-RUN causes; free-form exception capture is not a log channel."""
    known = {
        "local branch has diverged from upstream",
        "repository dirty", "repository dirty after synchronization",
        "canonical remote remediation lineage not found",
        "canonical remote repair lineage not found",
        "local main has diverged and lacks a canonical preservation ref",
        "configured local main has diverged and lacks a canonical preservation ref",
        "Protected pre-AIOS detail remains local.",
        "repository-integrity BLOCKED: no canonical preservation ref binds the divergent local HEAD",
        "local branch is ahead of upstream", "current branch has no resolved upstream",
        "configured upstream is ambiguous", "configured upstream does not resolve to main",
        "unsafe reload/restart condition",
    }
    if message in known or message in _SAFE_CAUSES.values():
        return message
    if (message.startswith(("repository-integrity BLOCKED:", "local-control", "local main", "configured local main"))
            and "preserv" in message.lower()):
        return "local main has diverged and lacks a canonical preservation ref"
    module = re.fullmatch(r"Python module unavailable: ([A-Za-z_][A-Za-z0-9_.]{0,95})", message)
    if module and _safe_diagnostic_cause("ModuleNotFoundError: No module named '" + module[1] + "'") is not None:
        return message
    cause = _safe_diagnostic_cause(message)
    return cause[1] if cause is not None else "Protected pre-AIOS detail remains local."


def admission_diagnostic_handoff(record: Mapping[str, Any], *, content: bytes, identity: str) -> dict[str, Any]:
    """Source-bound BP-2 facts, with no invented engineering terminal identity."""
    if (hashlib.sha256(content).hexdigest() != identity or _public_document(content) != dict(record)
            or record.get("format") != "AIOS_ADMISSION_FAILURE" or type(record.get("version")) is not int or record["version"] != 2
            or record.get("kind") != "ADMISSION_FAILURE" or record.get("executor_invoked") is not False
            or {"run_id", "run_created", "evidence", "result", "terminal_pointer", "verification"} & record.keys()):
        raise ValueError("invalid admission diagnostic source binding")
    operation, phase, reason = record.get("operation"), record.get("phase"), record.get("reason_code")
    if (operation not in _ADMISSION_DELIVERY_FIELDS or not isinstance(phase, str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", phase) is None
            or not isinstance(reason, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", reason) is None
            or _credential_shape(phase) or _credential_shape(reason)):
        raise ValueError("invalid admission diagnostic boundary")
    error = record.get("error")
    if (not isinstance(error, Mapping) or set(error) != {"type", "message"}
            or not isinstance(error["type"], str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", error["type"]) is None
            or _credential_shape(error["type"])
            or not isinstance(error["message"], str)):
        raise ValueError("invalid admission diagnostic error")
    facts = {}
    for key in ("current_head_sha", "control_head_sha", "task_blob_sha", "task_commit_sha", "current_task_blob_sha", "observed_sha"):
        if key in record:
            if not isinstance(record[key], str) or re.fullmatch(r"[0-9a-f]{40}", record[key]) is None:
                raise ValueError("invalid admission diagnostic Git identity")
            facts[key] = record[key]
    query = record.get("remote_query")
    if query is not None:
        if (not isinstance(query, Mapping) or set(query) != {"operation", "outcome", "category"}
                or query["operation"] != "LS_REMOTE" or query["outcome"] != "UNAVAILABLE"
                or query["category"] not in {"AUTH", "DNS", "TLS", "TIMEOUT", "CONNECTIVITY", "UNKNOWN"}):
            raise ValueError("invalid admission remote diagnostic facts")
        facts["remote_query"] = dict(query)
    return dict(format="AIOS_ADMISSION_DIAGNOSTICS_V1", operation=operation, phase=phase, reason_code=reason,
        source_digest=identity, run_created=False, executor_invoked=False, error_type=error["type"],
        summary=safe_admission_message(error["message"]),
        action=_ADMISSION_ACTIONS.get(reason, "Resolve the reported admission boundary before requesting execution."),
        facts=facts, completeness="PARTIAL", private_access="RESTRICTED")


def validate_admission_context(value: Any) -> None:
    if isinstance(value, dict) and value.get("format") == "AIOS_ADMISSION_DIAGNOSTICS_V1":
        validate_admission_diagnostics(value)
        return
    from .correction_preflight import _ADMISSION_PHASES, _ADMISSION_REASONS
    if (not isinstance(value, dict) or set(value) != {"operation", "phase", "reason_code"}
            or value["operation"] not in {"PRIMARY", "REMEDIATION", "REPAIR"}
            or value["phase"] not in _ADMISSION_PHASES or value["reason_code"] not in _ADMISSION_REASONS):
        raise ValueError("invalid historical admission context")


def validate_admission_diagnostics(value: Any) -> None:
    fields = {"format", "operation", "phase", "reason_code", "source_digest", "run_created", "executor_invoked",
              "error_type", "summary", "action", "facts", "completeness", "private_access"}
    if (not isinstance(value, dict) or set(value) != fields or value["format"] != "AIOS_ADMISSION_DIAGNOSTICS_V1"
            or value["operation"] not in _ADMISSION_DELIVERY_FIELDS
            or not isinstance(value["phase"], str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value["phase"]) is None
            or not isinstance(value["reason_code"], str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value["reason_code"]) is None
            or _credential_shape(value["phase"]) or _credential_shape(value["reason_code"])
            or not _is_digest(value["source_digest"]) or value["run_created"] is not False or value["executor_invoked"] is not False
            or value["error_type"] is not None and (not isinstance(value["error_type"], str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", value["error_type"]) is None or _credential_shape(value["error_type"]))
            or not isinstance(value["summary"], str) or safe_admission_message(value["summary"]) != value["summary"]
            or value["action"] != _ADMISSION_ACTIONS.get(value["reason_code"], "Resolve the reported admission boundary before requesting execution.")
            or value["completeness"] != "PARTIAL" or value["private_access"] != "RESTRICTED"):
        raise ValueError("unsafe admission diagnostic context")
    facts = value["facts"]
    if not isinstance(facts, dict) or set(facts) - {"current_head_sha", "control_head_sha", "task_blob_sha", "task_commit_sha", "current_task_blob_sha", "observed_sha", "remote_query"}:
        raise ValueError("unknown admission diagnostic facts")
    for key, fact in facts.items():
        if key == "remote_query":
            if (not isinstance(fact, dict) or set(fact) != {"operation", "outcome", "category"}
                    or fact["operation"] != "LS_REMOTE" or fact["outcome"] != "UNAVAILABLE"
                    or fact["category"] not in {"AUTH", "DNS", "TLS", "TIMEOUT", "CONNECTIVITY", "UNKNOWN"}):
                raise ValueError("unsafe admission remote diagnostic facts")
        elif not isinstance(fact, str) or re.fullmatch(r"[0-9a-f]{40}", fact) is None:
            raise ValueError("unsafe admission diagnostic identity")


def validate_diagnostic_attention(value: Any) -> None:
    if not isinstance(value, dict) or not {"event_id", "availability"} <= value.keys():
        raise ValueError("invalid diagnostic attention")
    from .brain_attention import parse_event_id, FAILURE, PRE_AIOS, DISPATCH, RECOVERY
    item = parse_event_id(value["event_id"])
    if item.family == RECOVERY:
        item = parse_event_id(item.selectors["original_event_id"])
    if value["availability"] == "UNAVAILABLE":
        if (set(value) != {"event_id", "availability", "reason_code"}
                or value["reason_code"] not in {"DIAGNOSTIC_SOURCE_UNAVAILABLE", "NO_FAILURE_DIAGNOSTIC_SOURCE"}):
            raise ValueError("invalid diagnostic attention gap")
    elif value["availability"] == "AVAILABLE" and set(value) == {"event_id", "availability", "failure_diagnostics"}:
        diagnostic = value["failure_diagnostics"]
        validate_failure_handoff(diagnostic)
        if (item.family != FAILURE or diagnostic["source"]["run_id"] != item.selectors["run_id"]
                or diagnostic["source"]["artifact_sha"] != item.selectors["artifact_sha"]):
            raise ValueError("diagnostic attention failure source substitution")
    elif value["availability"] == "AVAILABLE" and set(value) == {"event_id", "availability", "admission_diagnostics"}:
        validate_admission_diagnostics(value["admission_diagnostics"])
        if item.family not in {PRE_AIOS, DISPATCH} or value["admission_diagnostics"]["operation"] != item.selectors["operation"]:
            raise ValueError("diagnostic attention admission source substitution")
    else:
        raise ValueError("invalid diagnostic attention state")


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
    def privacy(value: Any) -> None:
        if isinstance(value, str) and (_credential_shape(value) or re.search(r"(?i)(?:[a-z]:[/\\]|https?://[^/@\s]+@|(?:^|\s)/(?:Users|home|private|tmp)/)", value)):
            raise ReviewTransportError("unsafe public admission diagnostic material")
        if isinstance(value, list):
            for item in value:
                privacy(item)
        elif isinstance(value, Mapping):
            if set(value) & {"stdout", "stderr", "captured_output", "traceback", "workspace", "raw_path"}:
                raise ReviewTransportError("private capture in public admission diagnostic")
            for item in value.values():
                privacy(item)
    privacy(record)
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
