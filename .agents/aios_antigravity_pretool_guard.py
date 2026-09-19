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
commands (status, ordinary bounded diff inspection excluding diff
--check, add, commit, rev-parse, log, show, branch inspection, and
ls-files) remain allowed. Destructive, history-changing, or network
git operations (reset, restore, merge, rebase, stash, fetch, remote,
config, tag, push, pull, cherry-pick, revert, clean, rm, mv, checkout,
switch, worktree, submodule, reflog, filter-branch, gc, update-ref)
are not admitted in active AIOS execution. ``git diff --check`` is
Runtime-owned verification and is denied with
AIOS_RUNTIME_OWNS_VERIFICATION.

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
DENY_REASON_GIT_NOT_ADMITTED = "AIOS_GIT_OPERATION_NOT_ADMITTED"


def _allow():
    return {"decision": "allow"}


def _deny(reason, *, message):
    return {"decision": "deny", "reason": reason}


def _read_stdin():
    try:
        data = sys.stdin.read()
    except (OSError, UnicodeError):
        return ""
    if data is None:
        return ""
    return data


def _parse_payload(raw):
    if not raw or not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _extract_command(payload):
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
# ``git diff --check`` is Runtime-owned canonical verification and must
# be denied in active AIOS execution.
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
    "git diff --check",
)

# Allow-list of bounded repository / git terminalization commands.
# Narrowed to the implementation/terminalization surface explicitly
# authorized for the active AIOS coding Executor. ``git diff --check``
# is excluded here because it is Runtime-owned verification.
# Destructive, history-changing, or network git operations are excluded
# here and matched separately by ``_GIT_NOT_ADMITTED_RE`` below.
_GIT_ALLOW_RE = re.compile(
    r"^\s*git\s+(?:"
    r"status|diff(?:\s+--name-only|\s+--stat|\s+--shortstat|\s+--cached|\s+HEAD|\s+\S+)?|"
    r"add|commit|rev-parse|log|show|branch|ls-files"
    r")(?:\s+[^|&<>`\\\n\r]*)?\s*$",
    re.IGNORECASE,
)

# Destructive / history-changing / network git operations are not admitted
# in active AIOS execution. This deny-list is the narrow complement of the
# bounded allow-list above; canonical need (e.g. an existing required test)
# must expand the allow-list, not carve into this deny-list.
_GIT_NOT_ADMITTED_RE = re.compile(
    r"^\s*git\s+(?:"
    r"reset|restore|merge|rebase|stash|fetch|remote|config|tag|"
    r"push|pull|cherry-pick|revert|clean|rm|mv|"
    r"checkout|switch|worktree|submodule|reflog|"
    r"filter-branch|gc|update-ref"
    r")(?:\s+[^|&<>`\\\n\r]*)?\s*$",
    re.IGNORECASE,
)


def _is_background_risk(cmd):
    if any(tok in cmd for tok in (" &\n", " &\r", " &\t")):
        return True
    if cmd.rstrip().endswith(" &"):
        return True
    lowered = cmd.lower()
    for tok in _BACKGROUND_TOKENS:
        if tok.lower() in lowered:
            return True
    return False


def _is_reinvocation(cmd):
    lowered = cmd.lower()
    for tok in _REINVOCATION_TOKENS:
        if tok.lower() in lowered:
            return True
    return False


def _is_verification(cmd):
    lowered = cmd.lower()
    for tok in _VERIFICATION_TOKENS:
        if tok.lower() in lowered:
            return True
    return False


def _is_allowed_bounded(cmd):
    return _GIT_ALLOW_RE.match(cmd) is not None


def _is_not_admitted_git(cmd):
    return _GIT_NOT_ADMITTED_RE.match(cmd) is not None



def evaluate(payload):
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

    # Runtime-owned canonical verification must be denied even when its
    # surface form would otherwise match the bounded git allow-list.
    # ``git diff --check`` is the canonical example: ``git diff`` is part
    # of bounded terminalization, but ``git diff --check`` is Runtime-owned
    # verification and must be rejected regardless.
    if _is_runtime_owned_diff_check(cmd):
        return _deny(
            DENY_REASON_RUNTIME,
            message="AIOS native execution rejects Runtime-owned verification commands; Runtime owns canonical verification",
        )

    if _is_allowed_bounded(cmd):
        return _allow()

    if _is_not_admitted_git(cmd):
        return _deny(
            DENY_REASON_GIT_NOT_ADMITTED,
            message="AIOS native execution does not admit destructive, history-changing, or network git operations",
        )

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


def _is_runtime_owned_diff_check(cmd):
    """``git diff --check`` is Runtime-owned canonical verification.

    The bounded git allow-list permits ``git diff`` (and ``git diff
    --name-only``, ``--stat``, ``--cached``, etc.) for ordinary bounded
    inspection, but ``git diff --check`` is whitespace / conflict-error
    verification that Runtime owns. It must be denied with
    ``AIOS_RUNTIME_OWNS_VERIFICATION`` regardless of any later allow
    classification.
    """
    stripped = cmd.lstrip().lower()
    return stripped == "git diff --check" or stripped.startswith("git diff --check ")


def main():
    if os.environ.get(ACTIVATION_ENV) != ACTIVE_VALUE:
        sys.stdout.write(json.dumps(_allow()))
        return 0

    raw = _read_stdin()
    payload = _parse_payload(raw)
    sys.stdout.write(json.dumps(evaluate(payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())