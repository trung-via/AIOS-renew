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

### Explicit unsafe Windows Codex opt-in

The Codex sandbox defaults to `workspace-write`. Where the documented native
Windows ACL limitation prevents execution, a Human may explicitly opt into:

```powershell
aios run TASK-101 --executor codex `
  --codex-sandbox danger-full-access
```

`danger-full-access` is unsafe and is never selected automatically or used as
the production default.
