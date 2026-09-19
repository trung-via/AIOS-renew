#!/usr/bin/env python3
"""AIOS Antigravity PreToolUse guard for run_command.

This guard is loaded as a repository workspace PreToolUse hook from
.agents/hooks.json. It is inert for ordinary / manual Antigravity use.

It only enforces AIOS policy when the native adapter explicitly
activates it through the AIOS_ANTIGRAVITY_ACTIVE environment variable.
When active, the guard denies any run_command call whose command
matches a known Runtime-owned verification, background-risk, or
unbounded-git class before execution, including:

  * pytest invocations (any pytest run)
  * Explicit pytest modules such as RUN-136-004's
    "pytest tests/test_authoring_ingress.py"
  * Long-running build / verification suites
  * Watchers, dev servers, and persistent processes
  * Explicit sleep / wait / nohup commands
  * Re-invocation of the agy CLI or AIOS operator launchers
  * Any command that ends with a Windows shell background marker

GIT CATEGORY FAIL-CLOSED POLICY:
  When active, the guard classifies a command as a git invocation when
  the executable is `git` or `git.exe` and may be invoked as bare
  `git`, path-prefixed (e.g. `./bin/git`), absolute-Windows
  (`C:\\path\\git`), quoted (e.g. `"path\\git.exe"`), or with
  an `.exe` suffix. After git classification, only the bounded
  terminalization subcommands are admitted: `status`,
  ordinary `diff` (excluding `--check`), `add`, normal `commit`
  (excluding `--amend`), `rev-parse`, `log`, `show`,
  inspection-only `branch` (no create/delete/rename/move/copy),
  and `ls-files`. `git diff --check` is Runtime-owned verification
  in every benign argument ordering (including `git diff HEAD --check`)
  and is denied with AIOS_RUNTIME_OWNS_VERIFICATION. `git commit
  --amend` is history-changing and is denied with
  AIOS_GIT_OPERATION_NOT_ADMITTED. `git branch -D/-d/-m/-c` and
  any unknown git subcommand (clone, fetch, reset, restore, etc.)
  are also denied with AIOS_GIT_OPERATION_NOT_ADMITTED. Any other
  invocation - including clone, fetch, reset, restore, checkout,
  switch, push, pull, merge, rebase, stash, remote, config, tag,
  cherry-pick, revert, clean, rm, mv, submodule, worktree, reflog,
  filter-branch, gc, update-ref, or any unknown subcommand - is also
  denied with AIOS_GIT_OPERATION_NOT_ADMITTED.

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
from typing import Any, Sequence


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
# `git diff --check` is Runtime-owned canonical verification and must
# be denied in active AIOS execution regardless of argument ordering.
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

# ---------------------------------------------------------------------------
# Git classification: detect `git` and `git.exe` invocations including
# quoted or absolute-Windows-path executable paths. The allow-list is
# narrowed to the bounded terminalization surface explicitly authorized
# for the active AIOS coding Executor. `git diff --check`,
# `git commit --amend`, and any branch create / delete / rename / move /
# copy form are matched separately below.
# ---------------------------------------------------------------------------

# Optional env var assignments (`FOO=bar`) followed by an optional quoted
# or absolute-Windows-path prefix, then `git` or `git.exe`. After
# matching, the original prefix is replaced with the canonical literal
# `git` so downstream tokenization and matching work on a stable form.
_GIT_INVOCATION_PREFIX_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*=[^\s\\'\"]*\s+)*"
    r"(?:"
    r"\"([^\"]+)\"|"        # 1: double-quoted path
    r"\'([^\']+)\'|"          # 2: single-quoted path
    r"([A-Za-z]:[^\"\'\s]*[\\/])|"  # 3: absolute Windows path
    r"([^\"\'\s]*[\\/])"   # 4: posix path
    r")?"
    r"(?:git|git\.exe)\b",
    re.IGNORECASE,
)


def _normalize_git_invocation(cmd):
    """Detect a git invocation and return a canonical form beginning with
    `git` so downstream logic operates on a stable string. Returns
    `None` when `cmd` is not a git invocation."""
    if not isinstance(cmd, str):
        return None
    match = _GIT_INVOCATION_PREFIX_RE.match(cmd)
    if match is None:
        return None
    return "git" + cmd[match.end():]


_BOUNDED_GIT_SUBCOMMANDS = frozenset({
    "status",
    "diff",
    "add",
    "commit",
    "rev-parse",
    "log",
    "show",
    "branch",
    "ls-files",
})

# branch flags that turn an inspection-only `git branch` into a
# destructive / history-changing / metadata mutation. These must be
# denied with AIOS_GIT_OPERATION_NOT_ADMITTED.
_BRANCH_FORBIDDEN_FLAGS = frozenset({
    "-d", "-D", "-m", "-c", "-M",
    "--delete", "--create", "--move", "--copy",
    "--set-upstream", "--set-upstream-to",
    "--unset-upstream",
    "--track", "--no-track",
})


def _classify_git(args):
    """Classify a normalized git invocation. `args` is the list of
    arguments after the canonical `git` executable token. Returns a
    tuple `(decision_kind, reason)` where `decision_kind` is one of
    `"allow"`, `"deny_runtime"`, or `"deny_git"`.

    - `allow`: the bounded subcommand is admitted with no forbidden flags.
    - `deny_runtime`: the bounded subcommand is admitted but the args
      contain a Runtime-owned verification flag (`diff --check`).
    - `deny_git`: the subcommand is not in the bounded list, or the
      subcommand is bounded but the args contain a history-changing /
      destructive / metadata mutation flag (`commit --amend`,
      `branch -d/-D/-m/-c`).
    """
    if not args:
        return ("deny_git", DENY_REASON_GIT_NOT_ADMITTED)

    subcommand = args[0].lower()

    if subcommand == "status":
        return ("allow", None)

    if subcommand == "diff":
        for arg in args[1:]:
            if arg == "--check" or arg.startswith("--check="):
                return ("deny_runtime", DENY_REASON_RUNTIME)
        return ("allow", None)

    if subcommand == "add":
        return ("allow", None)

    if subcommand == "commit":
        for arg in args[1:]:
            if arg == "--amend" or arg.startswith("--amend="):
                return ("deny_git", DENY_REASON_GIT_NOT_ADMITTED)
        return ("allow", None)

    if subcommand == "rev-parse":
        return ("allow", None)

    if subcommand == "log":
        return ("allow", None)

    if subcommand == "show":
        return ("allow", None)

    if subcommand == "branch":
        for arg in args[1:]:
            if arg in _BRANCH_FORBIDDEN_FLAGS:
                return ("deny_git", DENY_REASON_GIT_NOT_ADMITTED)
        return ("allow", None)

    if subcommand == "ls-files":
        return ("allow", None)

    # Anything else (clone, fetch, reset, restore, merge, rebase, stash,
    # remote, config, tag, push, pull, cherry-pick, revert, clean, rm,
    # mv, checkout, switch, worktree, submodule, reflog, filter-branch,
    # gc, update-ref, or any unknown subcommand) is denied.
    return ("deny_git", DENY_REASON_GIT_NOT_ADMITTED)


def _split_git_args(canonical_cmd):
    """Split the canonical git invocation into args, ignoring extra
    whitespace, and skipping any leading env-var assignment tokens."""
    tokens = canonical_cmd.split()
    start = 0
    while start < len(tokens):
        tok = tokens[start]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            start += 1
            continue
        break
    if start >= len(tokens) or tokens[start].lower() not in ("git", "git.exe"):
        return None
    return tokens[start + 1:]


def _evaluate_git(cmd):
    """Return a verdict for a command classified as a git invocation, or
    `None` if `cmd` is not a git invocation."""
    canonical = _normalize_git_invocation(cmd)
    if canonical is None:
        return None
    args = _split_git_args(canonical)
    if args is None:
        return _deny(
            DENY_REASON_GIT_NOT_ADMITTED,
            message="AIOS native execution does not admit unknown git invocations",
        )
    kind, reason = _classify_git(args)
    if kind == "allow":
        return _allow()
    if kind == "deny_runtime":
        return _deny(
            reason,
            message="AIOS native execution rejects Runtime-owned verification commands; Runtime owns canonical verification",
        )
    return _deny(
        reason,
        message="AIOS native execution does not admit destructive, history-changing, or network git operations",
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

    # 1. Git category fail-closed: detect `git` / `git.exe` (including
    # quoted or absolute-Windows-path executable paths) and apply the
    # narrow bounded allow-list. `git diff --check` is denied with
    # AIOS_RUNTIME_OWNS_VERIFICATION regardless of benign argument
    # ordering (e.g. `git diff HEAD --check`). `git commit --amend`
    # and any branch create / delete / rename / move / copy form are
    # denied with AIOS_GIT_OPERATION_NOT_ADMITTED. Unknown git
    # subcommands are also denied with AIOS_GIT_OPERATION_NOT_ADMITTED.
    git_verdict = _evaluate_git(cmd)
    if git_verdict is not None:
        return git_verdict

    # 2. Re-invocation of the provider CLI or an AIOS operator launcher is
    # expected policy to be denied; report it as Runtime-owned so the
    # stable hook reason is consistent across AIOS-native providers.
    if _is_reinvocation(cmd):
        return _deny(
            DENY_REASON_RUNTIME,
            message="AIOS native execution does not permit re-invoking the provider CLI or an AIOS operator launcher",
        )

    # 3. Background-risk commands are denied before execution.
    if _is_background_risk(cmd):
        return _deny(
            DENY_REASON_BACKGROUND,
            message="AIOS native execution rejects background-risk run_command; bounded synchronous commands only",
        )

    # 4. Verification / build commands are Runtime-owned.
    if _is_verification(cmd):
        return _deny(
            DENY_REASON_RUNTIME,
            message="AIOS native execution rejects Runtime-owned verification commands; Runtime owns canonical verification",
        )

    return _allow()


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
