# Brain TASK Authoring Contract

This is the compact authoring boundary for canonical AIOS-renew TASKs. It supplements, and does not replace or revise, the frozen v0.1 specification.

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

The list must be non-empty and contain unique, deterministic, non-interactive, minimum-sufficient commands in the required order. Prefer focused checks that establish the acceptance criteria. Do not add Git cleanliness, HEAD, changed-files, or similar repository-integrity checks by default: Runtime already owns those gates. Runtime executes canonical verification and constructs its EVIDENCE.

Do not instruct the Executor to push. Executor implementation ends at the permitted final local commit; synchronization and publication remain outside Brain-authored implementation instructions.
