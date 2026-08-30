# AIOS-renew

AIOS-renew provides a thin Human-facing operator above the frozen v0.1 kernel.

## Install

```powershell
pip install -e .
```

Store canonical engineering tasks in the target repository:

```text
.ai/tasks/TASK-101.yaml
```

Inspect or execute a stored task:

```powershell
aios task TASK-101
aios run TASK-101 --executor codex
aios run TASK-101 --executor antigravity
```

Use `--repo PATH` to target a repository other than the current Git repository.

## Direct Candidate Acceptance

For a committed candidate produced directly by one Human-selected Executor in
response to a canonical `CHANGES_REQUIRED` CODE_FIX finding, accept it with only
the TASK id, finding id, and selected Executor identity:

```powershell
aios accept-candidate TASK-101 --finding F1 --executor codex
```

The Human attests that exactly one Executor held mutation authority while making
the candidate and must leave the candidate committed at the current clean HEAD.
AIOS does not claim a pre-acceptance lease for this mode. From the acceptance
boundary onward, Runtime resolves the REVIEW, REMEDIATION, and authoritative
prior result from the configured Git remote; rejects non-descendant or
out-of-scope candidates before admission; invokes no Executor; and runs only the
canonical affected verification. Successful candidates use the existing
post-PASS review transport for ChatGPT DELTA review. See
`docs/AIOS-RENEW-v0.1.4-DIRECT-CANDIDATE-ACCEPTANCE.md`.

### Explicit unsafe Windows Codex opt-in

The Codex sandbox defaults to `workspace-write`. Where the documented native
Windows ACL limitation prevents execution, a Human may explicitly opt into:

```powershell
aios run TASK-101 --executor codex `
  --codex-sandbox danger-full-access
```

`danger-full-access` is unsafe and is never selected automatically or used as
the production default.
