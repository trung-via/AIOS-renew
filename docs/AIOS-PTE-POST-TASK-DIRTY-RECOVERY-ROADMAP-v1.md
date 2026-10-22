# PTE — Dirty EXECUTION FAILURE recovery and Post-TASK end-to-end

**Identity:** `post-task-dirty-recovery-pte-20261022`  
**Approved:** Human, 2026-10-22 (Asia/Ho_Chi_Minh)  
**Status:** Approved planning only; six future implementation TASKs, not yet authored or admitted.  
**Canonical planning authority:** `.ai/roadmap-state.yaml:human_pte01_06_then_task401_r3_priority_20261022`.

## Priority and relationship to Lean VP

The Human explicitly selects **PTE-01 → PTE-02 → PTE-03 → PTE-04 → PTE-05 → PTE-06**, all separately verified, independently reviewed and published, followed by prospective **TASK-401 revision 3** as the **PTE-07 live end-to-end conformance case**. There is no seventh new standalone PTE implementation TASK. The unique first current NEXT is PTE-01; later items are committed planning order, not automatically selected by lifecycle results.

Prior roadmap NEXT `lean-vp-intra-v13-budget-defaults-successor` is **PAUSED / RESUMABLE**, not DONE, skipped or retired. Its **L3-06/G3, separate G6, L4** remain unproved. This priority change does not activate KA-01 G6, change any downstream pin, rewrite `TASK-401` r1/r2 or `RUN-401-001/002`, or confer new Executor/Runtime/Reviewer/Publisher authority. `TASK-401 r2` remains the current canonical authored revision until r3 is separately, prospectively authored and admitted.

## Six packages — WHAT / success boundary / scope dependencies

### PTE-01 — Terminal return-affinity and durable wake for dirty FAILURE

Resolve observed `RUN-401-002` terminal attention admission PASS followed by local Chat wake `AFFINITY_UNPROVEN` when `aios/failure-artifacts/<RUN>` exists but there is no committed `aios/failure/<RUN>` candidate ref. Verify exact TASK/base/RUN/FAILURE artifact and return affinity without synthesizing a missing candidate. Ensure original-lane wake, duplicate suppression, preserved pending inbox during `STATE_LOCKED_OR_UNAVAILABLE`, no duplicate Send, and fail-closed forged/stale/opposite terminal tests. Reuse existing `terminal_attention.py`, `brain_attention.py` and `local_chat_wake.py` plus affected callers and tests. **No terminal notification creates RUN, repair, or publication authority.**

### PTE-02 — Minimum Post-TASK canonical state read

Reuse `brain_sync.observe_brain_sync`, `unified_state.observe_unified_state` and exact terminal/review/publication locators. Given an authenticated TASK or wake selector, return one bounded state: TASK revision; attributed RUN; ACTIVE/RESULT/FAILURE; next_action/authority/blocker; candidate/artifact SHA; reviewer/publication inclusion when established; next exact data source only if required. Do not crawl raw logs on ordinary RESULT, create a second state reducer or claim RESULT means REVIEW PASS. Include real caller portability beyond local-only unit tests. **If existing code already satisfies the outcome, do not manufacture new implementation.**

### PTE-03 — Source-bound native EXECUTION FAILURE diagnosis

Use canonical FAILURE public/safe detail first, pinned GitHub Actions workflow/job logs when attributable, then existing origin-safe protected self-host diagnostics only when necessary. Brain must not require Human PowerShell/Python merely to extract native error. Test observed `RUN-401-002` native `apply_patch verification failed` context mismatch; distinguish observed symptom from unproved sole CLI exit cause. Bound diagnostic privacy, identities, size, currentness, unavailable sources and reason categories; preserve uncertainty as UNKNOWN, never automatically choose CODE_FIX. Reuse TASK-393/394/399 functionality rather than duplicate retrieval infrastructure.

### PTE-04 — Origin-affine dirty worktree preservation

On admitted native EXECUTION FAILURE, retain a protected, immutable, hash-bound snapshot/inventory of tracked and necessary untracked modifications, base/HEAD, TASK/RUN/FAILURE identity; bound sensitive/ignored content and reject symlink/reparse, size overflow, concurrent writes, partial capture, changed hash and wrong origin. Do not run unsafe clean/reset, overwrite dirty checkout, publish private snapshot as normal candidate, mutate old terminal FAILURE or fabricate verification proof. Snapshot failure reports explicit blocker and preserves existing work when possible. Keep operational state subordinate to Runtime.

### PTE-05 — Prospective lawful dirty recovery contract (AUTHORITY GATE)

Audit frozen Kernel and Constitution, source-to-target provenance, versioned snapshot validity and exact correction scope. Current Runtime computes `repairable = not dirty && descends` and Unified State requires clean transportable candidate to offer AUTHOR_REPAIR; existing CONTINUE_IMPLEMENTATION also requires a clean transportable source. Compare reuse/no-build and minimum lawful versioned extension; define a prospective contract that does **not** change historical `RUN-401-002` or relabel original `repairable=false`. If a Kernel or constitutional amendment is actually needed, obtain its specific explicit Human approval separately; the current scheduling approval is **not** prior risk/constitutional acceptance. Negative cases and compatibility must pass before PTE-06 may implement recovery admission.

### PTE-06 — Implement lawful dirty recovery → Brain-authorized REPAIR

Implement only PTE-05's lawful contract inside existing Runtime, operator/correction preflight/Unified State. Runtime authenticates snapshot and predecessor FAILURE and prepares a safe isolated recovery seed. Brain separately audits a specific REPAIR strategy with active ROA-1 R3 and exact scope; the single Human-delegated Executor owns edits/HOW. New authorized REPAIR creates its own RUN/RESULT/EVIDENCE and independent review; failed RUN remains terminal. No auto-strategy, generic retry, competing writer, dirty overwrite or unstated Executor delegation. Test valid continuation and forged/stale/out-of-scope/partial-state rejection.

## PTE-07 — Live conformance through prospective TASK-401 r3

**Not a standalone PTE TASK.** Only after all PTE-01..06 are independently reviewed and Publisher-included in canonical main, fresh Brain Sync reconciles `TASK-401 r2` and `RUN-401-001/002`. Brain prospectively AUTHORS r3 under TCA-2, reexamining actual Lean VP L3-06/G3 goal, API/tests and sufficient scope.inspect/modify. Human's Lean VP Codex GPT-6.1 Sol Max delegation is not a KA-01 G6 consent and must be authenticated as applicable for the new revision.

Use the real r3 episode to demonstrate Human/wake→Brain exact status→appropriate RESULT semantic REVIEW or FAILURE source-bound diagnostic/REPAIR→possible CHANGES_REQUIRED/REMEDIATION→eligible Publisher inclusion. Branches not naturally exercised by one live episode require bounded negative/positive tests from PTE-01..06 and targeted r3 evidence; do not falsely declare all E2E branches live-proven from a single PASS.

If r3 fails, retain exact evidence and use its actual unique canonical next_action; do not claim conformance PASS. Follow-up Lean VP resumption requires new explicit Human/Brain planning reconciliation, not automatic roadmap advancement.

## Authoring, risk and evidence invariants

1. Audit each new exact PTE TASK under current **TCA-2 v2**, using canonical current source/API/legacy tests/AST guards. ARCHITECTURE uses **AOA-1 v1**, REPAIR **ROA-1 R3 v3**, REMEDIATION **MOA-1 v1**. Human's decision fixes planning order only; no exact TASK ID, RUN, executor authorization or constitution change is forged.
2. Include every necessary file in `scope.inspect`; include every necessary modified consumer/test in `scope.modify` with an evidenced causal relation. No arbitrary file-count limit, no inspect-only mutation or weakening tests.
3. Runtime validates identity, SHA, original TASK/FAILURE, admission, policy, relevant verification/EVIDENCE and immutable refs. Reviewer alone provides verdict; Publisher alone publishes exact eligible reviewed source; GitHub issue, wake and workflow receipts are transport facts only.
4. Use minimum-sufficient proof; do not repeat valid unchanged work, rerun broad full suite by default or invent timing/productivity claims. Preserve full episode metrics when observed.
5. Exactly **one** current roadmap NEXT; historical planning priorities remain readable, not active competitors. Immutable engineering artifacts and G6/Ka-01 activation remain unchanged.
