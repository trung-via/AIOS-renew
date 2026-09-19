#!/usr/bin/env python3
"""AIOS Antigravity PreToolUse guard for run_command.

This guard is loaded as a repository workspace PreToolUse hook from
.agents/hooks.json. It is inert for ordinary / manual Antigravity use.

It only enforces AIOS policy when the native adapter explicitly
activates it through the AIOS_ANTIGRAVITY_ACTIVE environment variable.
When active, the guard denies any run_command call whose command
matches a known Runtime-owned verification or background-risk class
before execution, including:

  * pytest invocations (any pytest run)
  * Explicit pytest modules such as RUN-136-004's
    "pytest tests/test_authoring_ingress.py"
  * Long-running build / verification suites
  * Watchers, dev servers, and persistent processes
  * Explicit sleep / wait / nohup commands
  * Re-invocation of the agy CLI or AIOS operator launchers
  * Any command that ends with a Windows shell background marker

Otherwise the guard allows the call. Bounded git terminalization
commands (status, diff, add, commit, rev-parse, log, show) remain
allowed.

The guard parses stdin defensively and never raises to the shell:
malformed active-AIOS input fails closed by denying the call. The guard
writes only valid hook JSON to stdout and never prints diagnostics or
prose to stdout that could corrupt the hook protocol.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any


ACTIVATION_ENV = "AIOS_ANTIGRAVITY_ACTIVE"
ACTIVE_VALUE = "1"
DENY_REASON_RUNTIME = "AIOS_RUNTIME_OWNS_VERIFICATION"
DENY_REASON_BACKGROUND = "AIOS_BACKGROUND_RISK_DENIED"
DENY_REASON_MALFORMED = "AIOS_GUARD_MALFORMED_INPUT"


def _allow() -> dict[str, Any]:
    return {"decision": "allow"}


def _deny(reason: str, *, message: str) -> dict[str, Any]:
    return {"decision": "deny", "reason": reason}


def _read_stdin() -> str:
    try:
        data = sys.stdin.read()
    except (OSError, UnicodeError):
        return ""
    if data is None:
        return ""
    return data


def _parse_payload(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _extract_command(payload: dict[str, Any]) -> str:
    # Antigravity PreToolUse protojson payload uses camelCase keys.
    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict):
        args = tool_call.get("args")
        if isinstance(args, dict):
            for key in ("CommandLine", "command", "cmd", "Command"):
                value = args.get(key)
                if isinstance(value, str):
                    return value
    # Defensive fallback for snake_case payloads from other hook providers.
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("CommandLine", "command", "cmd", "Command"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value
    return ""


# Background-risk tokens / commands. These block the call regardless of args.
_BACKGROUND_TOKENS = (
    "sleep", "timeout", "wait", "start-sleep",
    "nohup", "watch", "tail -f",
    "npm run dev", "npm run watch", "npm start",
    "flask run", "uvicorn", "gunicorn", "python -m http.server",
    "manage_task", "schedule",
)

# Re-invocation of agy or AIOS operator launchers is forbidden under AIOS mode.
_REINVOCATION_TOKENS = (
    "agy --print",
    "agy -p",
    "agy --prompt",
    "aios ",
    "aios-run",
    "aios-renew",
)

# Verification / build commands. Runtime owns canonical verification.
_VERIFICATION_TOKENS = (
    "pytest", "py.test",
    "unittest",
    "tox",
    "nosetests",
    "mvn ", "gradle ", "gradlew ", "gradlew.bat",
    "npm test", "npm run test", "npm run lint", "npm run build",
    "yarn test", "yarn build",
    "cargo test", "cargo build",
    "go test", "go build",
    "make test", "make build", "make check",
    "coverage", "nox ",
    "ruff check", "ruff format",
    "black ", "isort ", "mypy ", "flake8 ", "pylint ",
)

# Allow-list of bounded repository / git terminalization commands.
_GIT_ALLOW_RE = re.compile(
    r"^\s*git\s+(?:"
    r"status|diff(?:\s+--check|\s+--name-only|\s+--stat|\s+--shortstat|\s+--cached|\s+HEAD|\s+\S+)?|"
    r"add|commit|rev-parse|log|show|branch|remote|config|ls-files|stash|tag|fetch|merge|rebase|reset|restore"
    r")(?:\s+[^|&<>`\\\n\r]*)?\s*$",
    re.IGNORECASE,
)


def _is_background_risk(cmd: str) -> bool:
    if any(tok in cmd for tok in (" &\n", " &\r", " &\t")):
        return True
    if cmd.rstrip().endswith(" &"):
        return True
    lowered = cmd.lower()
    for tok in _BACKGROUND_TOKENS:
        if tok.lower() in lowered:
            return True
    return False


def _is_reinvocation(cmd: str) -> bool:
    lowered = cmd.lower()
    for tok in _REINVOCATION_TOKENS:
        if tok.lower() in lowered:
            return True
    return False


def _is_verification(cmd: str) -> bool:
    lowered = cmd.lower()
    for tok in _VERIFICATION_TOKENS:
        if tok.lower() in lowered:
            return True
    return False


def _is_allowed_bounded(cmd: str) -> bool:
    return _GIT_ALLOW_RE.match(cmd) is not None


def evaluate(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return _deny(
            DENY_REASON_MALFORMED,
            message="AIOS PreToolUse guard received malformed tool input",
        )

    cmd = _extract_command(payload)
    if not cmd or not cmd.strip():
        # Empty command cannot be classified; fail closed in AIOS mode.
        return _deny(
            DENY_REASON_MALFORMED,
            message="AIOS PreToolUse guard received a tool call with no command",
        )

    if _is_allowed_bounded(cmd):
        return _allow()

    if _is_reinvocation(cmd):
        return _deny(
            DENY_REASON_RUNTIME,
            message="AIOS native execution does not permit re-invoking the provider CLI or an AIOS operator launcher",
        )

    if _is_background_risk(cmd):
        return _deny(
            DENY_REASON_BACKGROUND,
            message="AIOS native execution rejects background-risk run_command; bounded synchronous commands only",
        )

    if _is_verification(cmd):
        return _deny(
            DENY_REASON_RUNTIME,
            message="AIOS native execution rejects Runtime-owned verification commands; Runtime owns canonical verification",
        )

    return _allow()


def main() -> int:
    if os.environ.get(ACTIVATION_ENV) != ACTIVE_VALUE:
        sys.stdout.write(json.dumps(_allow()))
        return 0

    raw = _read_stdin()
    payload = _parse_payload(raw)
    sys.stdout.write(json.dumps(evaluate(payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
