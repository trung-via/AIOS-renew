# Brain TASK Authoring Contract

This is the compact authoring boundary for canonical AIOS-renew TASKs. It supplements, and does not replace or revise, the frozen v0.1 specification.

## Constitutional preflight

Before drafting or revising a TASK, perform a constitutional preflight against the canonical governance baseline:
- [AIOS Manifesto](AIOS-MANIFESTO.md) — Ensure the work serves verified useful work and the optimization North Star (`Verified Useful Work / (Time + Tokens + Human Effort)`), avoiding unneeded agents, calls, reasoning loops, or activity.
- [AIOS Constitution](AIOS-CONSTITUTION.md) — Verify adherence to constitutional principles: Human intent ownership, separation of authority (Brain WHAT/WHY, Executor HOW, Runtime coordination, Reviewer judgment), bounded mutation authority, claims requiring evidence, reproducible canonical state, and correction continuity. Confirm that proposed delivery mechanisms remain subordinate and replaceable.

## Establish identity first

Before reasoning about a TASK, REVIEW, REMEDIATION, or FIX lineage, establish the canonical repository and worktree, the relevant canonical artifact identifiers and revisions, and the immutable Git SHA to which they bind. Never infer identity or lineage from chat history, screenshots, UI labels, or remembered state.

New Human intent is not a FIX. It requires a new TASK or TASK revision. REVIEW findings and any REMEDIATION must be resolved from canonical artifacts and their immutable reviewed SHA; remediation addresses only the authorized finding and minimum supporting delta.

## Author the outcome

The Brain owns **WHAT and WHY**: the goal, problem, assumptions, non-goals, constraints, scope, and acceptance criteria. The Executor owns **HOW**. Do not prescribe an implementation plan except where a true architectural constraint makes a choice part of the required outcome.

A TASK is executor-neutral: the same contract must be executable by either Codex or Antigravity, with no executor identity, model-specific directions, or adapter-specific workflow embedded in it.

Use `scope.inspect` only as minimum-context guidance. Use `scope.modify` as the hard mutation authority: list only the exact, minimal, repo-relative file paths required by the outcome. Do not use directories, absolute paths, traversal, backslashes, or glob patterns.

Acceptance criteria must be atomic, observable, and collectively complete. Each criterion should describe one independently reviewable outcome, avoid implementation steps, and leave no required behavior merely implied.

## Specify verification once

Put every canonical verification command only in `verification.required`. Do not repeat commands as execution instructions in the goal, problem, assumptions, non-goals, or constraints.

Every newly authored TASK identity or revision must declare `verification.policy: minimum-sufficient-v1`. The `required` list must be non-empty and contain deterministic, non-interactive, minimum-sufficient commands in the required order. A newly authored REMEDIATION for such a TASK must use the same policy under `verification`, with its commands in `verification.affected`. Legacy TASK and REMEDIATION artifacts remain readable and usable; the rule is prospective, and an identical same-revision historical TASK replay remains idempotent.

The v1 contract rejects exact duplicates and only those equivalent or subsumed pytest commands whose coverage relationship is mechanically proven by the repository-owned grammar. That grammar keeps `pytest` and `python -m pytest` as separate launcher families and recognizes only quiet output flags, explicit positional test paths, and an optional `-k` narrowing expression. Unsupported, malformed, shell-composed, or ambiguous commands are opaque and are never removed based on similarity.

A recognized full-suite pytest command (one with no path and no `-k` filter) requires a non-empty `full_suite_reason` of at most 512 characters. The field is invalid without such a command. This is structural policy only: Brain and Reviewer authority retain semantic judgment over the command selection and whether the reason is persuasive.

Prefer focused checks that establish the acceptance criteria. Do not add Git cleanliness, HEAD, changed-files, or similar repository-integrity checks by default: Runtime already owns those gates. REPAIR may deterministically normalize only overlap created when it combines already-authorized v1 TASK and origin REMEDIATION lists. Runtime still executes the resulting canonical list and constructs EVIDENCE; no evidence authority moves to authoring or repair policy.

Do not instruct the Executor to push. Executor implementation ends at the permitted final local commit; synchronization and publication remain outside Brain-authored implementation instructions.
