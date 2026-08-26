# AIOS-renew Kernel v0.1 Freeze Record

**Status:** FROZEN
**Freeze date:** 2026-08-26
**Baseline:** `73747ec74e8669dbd0746ccf614558b6ee3d28aa`

Kernel v0.1 is frozen as the minimum stable contract for real engineering use. This is not a declaration that the full AIOS product is finished. Future kernel features or changes require separate tasks backed by observed evidence.

## Frozen Kernel

- Canonical TASK and RUN contracts.
- One-active-executor lease enforcement and ExecutorBoundary authority.
- Canonical RESULT, EVIDENCE, REVIEW, and narrow remediation contracts.
- Native Codex adapter and injectable Antigravity native adapter boundary.
- One semantic TASK model shared by both executors.
- Independent deterministic repository verification with separated Brain, Executor, Reviewer, and Runtime responsibilities.

## Executor Conformance

Codex native CLI integration was proven in a disposable Git repository. The executor created the required file, committed it, and left a clean worktree; the observed successful disposable commit was `86a9105` (`smoke-test`).

Antigravity used the same semantic TASK contract through the real manual conformance handoff `RUN-010-SMOKE`. From base SHA `f1c35e0d7ee2f8a7e84dd763cc9e7b78cf871aca`, it produced final SHA `dc098edbd2a5cfcc6392407e45ac92a26098a25f`. AIOS deterministic verification returned `ANTIGRAVITY SMOKE PASS` and confirmed `SMOKE_OK.txt` as the changed file.

## Known Limitation

On the tested native Windows environment, `codex exec --sandbox workspace-write` produced filesystem/ACL behavior that made a Codex-created file inaccessible to the host process, untracked by Git, and absent from the committed Git object. This is treated as an executor-environment limitation, not an AIOS kernel protocol failure.

`danger-full-access` is not the production default. No kernel workaround or default sandbox-policy change is added without future evidence.

## Deferred Features

- H-Series.
- Planner agent.
- Router.
- Model scoring.
- Multi-agent voting.
- Multiple reviewers.
- Autonomous retries.
- Message broker.
- Redis.
- Orchestration database.
- Complex artifact graph.
- Persistent learning.
- Generalized orchestration DSL.

## Next Phase

Kernel development stops here by default. Next work should use the frozen kernel on real engineering tasks. Future kernel changes require observed evidence from real usage.
