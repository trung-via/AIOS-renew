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
  * Explicit pytest modules such as RUN-136-004s
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
  (`C:\\path\\git`), quoted (e.g. `"path\\git.exe"`, including quoted
  Windows paths that contain spaces such as
  `"C:\\Program Files\\Git\\cmd\\git.exe"`), or with an `.exe` suffix.
  The PowerShell call-operator form (`& <git-exec> ...`, e.g.
  `& "C:\\Program Files\\Git\\cmd\\git.exe" status` or
  `& C:\\repo\\tools\\git.exe status`) is normalized before git policy
  evaluation so the underlying invocation is classified as the same
  git invocation category as direct git execution. After git
  classification, only the bounded terminalization subcommands are
  admitted: `status`, ordinary `diff` (excluding `--check`), `add`,
  normal `commit` (excluding `--amend`), `rev-parse`, `log`, `show`,
  inspection-only `branch` (only bare `git branch`, `--show-current`,
  `-a`/`--all`, `--list`; no create/delete/rename/move/copy/upstream
  mutation forms), and `ls-files`. `git diff --check` is Runtime-owned
  verification in every benign argument ordering (including
  `git diff HEAD --check`) and is denied with
  AIOS_RUNTIME_OWNS_VERIFICATION. `git commit --amend` is
  history-changing and is denied with AIOS_GIT_OPERATION_NOT_ADMITTED.
  `git branch <positional-name>` is bare branch creation and is denied
  with AIOS_GIT_OPERATION_NOT_ADMITTED. Any unknown git subcommand
  (clone, fetch, reset, restore, etc.) is also denied with
  AIOS_GIT_OPERATION_NOT_ADMITTED.

SHELL-OPERATOR / CHAINED-COMMAND BYPASS PREVENTION:
  When the command starts with a git invocation, the guard refuses to
  admit any chained form. A command such as
  `git status & Start-Sleep -Seconds 20`,
  `git status && git push origin main`,
  `git add file.py && git push origin main`,
  or any other shell-operator / piped / redirected / statement-
  separated form is denied. Background-risk segments in the chain deny
  with AIOS_BACKGROUND_RISK_DENIED; other chained forms deny with
  AIOS_GIT_OPERATION_NOT_ADMITTED. The first bounded git subcommand
  cannot be used to admit a chained destructive / network git
  invocation or a chained background-risk command.

POWERHELL CALL-OPERATOR NORMALIZATION:
  The PowerShell call operator (`& <command>`) executes the named
  command or executable. A leading `& ` followed by a quoted or
  unquoted git/git.exe executable path is the PowerShell call
  operator, NOT a chained shell operator. The guard strips this
  prefix before git classification so that
  `& "C:\\Program Files\\Git\\cmd\\git.exe" status` is classified as
  the same git invocation category as `"C:\\Program Files\\Git\\cmd\\git.exe" status`.
  Non-git invocations through the call operator (e.g.
  `& Start-Sleep -Seconds 20`) are NOT normalized; the original
  command is preserved so the background, verification, and other
  policies can evaluate it normally. The guard does NOT globally
  block `&` and does NOT widen generic shell execution.

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
from typing import Any, List, Sequence, Tuple


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

# Matches the executable token of a command (after optional env
# assignments). The token may be bare (`git`, `git.exe`), single-quoted
# (`'C:\path\git.exe'`), double-quoted (`"C:\path with spaces\git.exe"`),
# or path-prefixed (`./bin/git`, `C:\bin\git`). The tokenization must
# capture the entire path (including quoted paths that contain spaces
# such as `"C:\Program Files\Git\cmd\git.exe"`), so the regex allows
# backslashes within unquoted tokens and any character inside quotes.
_GIT_EXECUTABLE_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*=[^\s\\\x27\"]*\s+)*"
    r"(?:"
    r"\"[^\"]*\""            # double-quoted token (may contain spaces, slashes)
    r"|"
    r"\x27[^\x27]*\x27"      # single-quoted token
    r"|"
    r"[^\s\"\x27]+"          # unquoted token (no whitespace, no quotes)
    r")"
)


def _basename_of_executable(token):
    """Strip surrounding quotes and path separators from an executable
    token, returning the basename (case-insensitive comparison done by
    the caller)."""
    if not token:
        return ""
    if len(token) >= 2 and (
        (token[0] == "\"" and token[-1] == "\"")
        or (token[0] == "\x27" and token[-1] == "\x27")
    ):
        token = token[1:-1]
    token = token.rstrip("\\/")
    if "\\" in token:
        token = token.rsplit("\\", 1)[-1]
    if "/" in token:
        token = token.rsplit("/", 1)[-1]
    return token


def _normalize_git_invocation(cmd):
    """Detect a git invocation and return a canonical form beginning with
    `git` so downstream logic operates on a stable string. Returns
    `None` when `cmd` is not a git invocation. Handles bare
    `git`/`git.exe`, single- and double-quoted Windows paths (including
    paths with spaces such as `"C:\\Program Files\\Git\\cmd\\git.exe"`),
    and unquoted path-prefixed forms."""
    if not isinstance(cmd, str):
        return None
    match = _GIT_EXECUTABLE_RE.match(cmd)
    if match is None:
        return None
    token = match.group(0)
    base = _basename_of_executable(token).lower()
    if base not in ("git", "git.exe"):
        return None
    return "git" + cmd[match.end():]


def _strip_powershell_call_operator(cmd):
    """If `cmd` begins with a PowerShell call operator (`& `) followed
    by a quoted or unquoted git/git.exe executable path, return the
    underlying command without the leading `& ` so it can be classified
    under the same git-category policy as direct git execution.
    Otherwise return None so the original command remains unchanged and
    other policies (background, verification, etc.) can evaluate it
    normally.

    The PowerShell call operator (`& <command>`) executes the named
    command or executable. For example:
        & "C:\\Program Files\\Git\\cmd\\git.exe" status
        & "C:\\Program Files\\Git\\cmd\\git.exe" diff HEAD --check
        & git status
        & git.exe status
        & C:\\repo\\tools\\git.exe status
        & C:\\repo\\tools\\git status

    This is distinct from:
        - Chained shell operators: `git status & echo done`
        - Logical AND operator: `git status && git push origin main`
        - Trailing background marker: `echo hello &`

    Only the form `& ` (ampersand followed by whitespace) followed by a
    quoted or unquoted git executable path is normalized. Any other
    use of `&` (chained operator, background marker, or call operator
    pointing at a non-git executable) leaves `cmd` unchanged so the
    appropriate non-git policy still applies. The guard does NOT
    globally block `&` and does NOT widen generic shell execution.
    """
    if not isinstance(cmd, str):
        return None
    stripped = cmd.lstrip()
    if not stripped or stripped[0] != "&":
        return None
    # The character after the `&` must be whitespace. This rejects
    # `&&` (logical AND), `&` followed immediately by a token (no
    # separator), and `&` at end of string.
    if len(stripped) < 2 or not stripped[1].isspace():
        return None
    rest = stripped[1:].lstrip()
    if not rest:
        return None
    # Only normalize when the call operator points at a git executable.
    # For non-git invocations, return None so background / verification /
    # other policies can still evaluate the original command.
    match = _GIT_EXECUTABLE_RE.match(rest)
    if match is None:
        return None
    token = match.group(0)
    base = _basename_of_executable(token).lower()
    if base not in ("git", "git.exe"):
        return None
    return rest


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

# `git branch` is inspection-only and accepts only these flags.
# Any positional name or mutation flag (--delete / -d / -D / --move /
# -m / --copy / -c / -M / --set-upstream / --unset-upstream / --track /
# --no-track) is denied as branch creation / mutation.
_BRANCH_ALLOWED_FLAGS = frozenset({
    "--show-current",
    "-a", "--all",
    "--list",
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
      branch bare-name creation, branch mutation flags).
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
        # Inspection-only: bare `git branch` is allowed (lists local
        # branches). Any additional argument MUST be an explicit
        # inspection flag in `_BRANCH_ALLOWED_FLAGS`. Anything else
        # (positional name = creation, mutation flag = destructive
        # branch mutation, or unknown flag) is denied.
        if len(args) == 1:
            return ("allow", None)
        for arg in args[1:]:
            if arg in _BRANCH_ALLOWED_FLAGS:
                continue
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


def _evaluate_bounded_git(canonical_cmd):
    """Evaluate a normalized git invocation against the bounded
    subcommand allow-list. Returns a verdict dict."""
    args = _split_git_args(canonical_cmd)
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


def _evaluate_git(cmd):
    """Return a verdict for a command classified as a git invocation, or
    `None` if `cmd` is not a git invocation."""
    canonical = _normalize_git_invocation(cmd)
    if canonical is None:
        return None
    return _evaluate_bounded_git(canonical)


# ---------------------------------------------------------------------------
# Shell-operator / chained-command detection. The guard refuses to admit
# any git invocation whose command body contains shell operators
# (outside quoted strings). Background-risk segments take precedence;
# other chained forms deny with AIOS_GIT_OPERATION_NOT_ADMITTED.
# ---------------------------------------------------------------------------

_SHELL_OPERATORS = ("&", "|", ";", ">", "<")


def _split_shell_segments(cmd):
    """Split a command body into segments delimited by shell operators,
    respecting single- and double-quoted strings. Returns a tuple of
    `(segments, has_operator)` where `has_operator` is True iff at least
    one operator was encountered outside quotes."""
    segments: List[str] = []
    has_operator = False
    last_end = 0
    in_double = False
    in_single = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        # Skip escaped characters (e.g. \&) inside unquoted text.
        if (
            c == "\\"
            and i + 1 < len(cmd)
            and not in_double
            and not in_single
        ):
            i += 2
            continue
        if not in_single and c == "\"":
            in_double = not in_double
            i += 1
            continue
        if not in_double and c == "\x27":
            in_single = not in_single
            i += 1
            continue
        if not in_double and not in_single and c in _SHELL_OPERATORS:
            has_operator = True
            seg = cmd[last_end:i].strip()
            if seg:
                segments.append(seg)
            # Consume the multi-character operators (`&&`, `||`) first.
            if c == "&" and i + 1 < len(cmd) and cmd[i + 1] == "&":
                i += 2
            elif c == "|" and i + 1 < len(cmd) and cmd[i + 1] == "|":
                i += 2
            else:
                i += 1
            last_end = i
            continue
        i += 1
    seg = cmd[last_end:].strip()
    if seg:
        segments.append(seg)
    return segments, has_operator


def _segment_is_background_risk(segment):
    """Return True if a single command segment is background-risk."""
    if not segment:
        return False
    stripped = segment.rstrip()
    # Trailing single `&` (not `&&`) is a background marker.
    if stripped.endswith(" &"):
        return True
    lowered = segment.lower()
    for tok in _BACKGROUND_TOKENS:
        if tok.lower() in lowered:
            return True
    return False


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

    # Normalize the PowerShell call-operator form. A leading `& ` followed
    # by a quoted or unquoted git/git.exe executable path is the
    # PowerShell call operator, NOT a chained shell operator. Strip it
    # so the underlying git invocation is classified under the same
    # git-category policy as direct git execution. Non-git invocations
    # through the call operator (e.g. `& Start-Sleep -Seconds 20`) are
    # left unchanged so the background, verification, and other policies
    # can still evaluate them. This guard does NOT globally block `&` and
    # does NOT widen generic shell execution.
    cmd = _strip_powershell_call_operator(cmd) or cmd

    # 1. Git category fail-closed. When the command starts with a git
    # invocation, deny chained forms first (background-risk segments
    # take precedence over generic git-not-admitted), then apply the
    # bounded git allow-list.
    git_canonical = _normalize_git_invocation(cmd)
    if git_canonical is not None:
        segments, has_operator = _split_shell_segments(cmd)
        if has_operator:
            if any(_segment_is_background_risk(seg) for seg in segments):
                return _deny(
                    DENY_REASON_BACKGROUND,
                    message="AIOS native execution rejects chained commands with background risk; bounded synchronous single commands only",
                )
            return _deny(
                DENY_REASON_GIT_NOT_ADMITTED,
                message="AIOS native execution rejects chained git commands; only bounded single git invocations are admitted",
            )
        # Single git invocation - apply bounded classification.
        git_verdict = _evaluate_bounded_git(git_canonical)
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
