# PTE — Dirty EXECUTION FAILURE recovery and Post-TASK end-to-end

> **SUPERSEDED PROSPECTIVELY — Human decision 2026-10-24 / TASK-416 r1.**
> Current PTE scope is [PTE Information-Only v2](AIOS-PTE-INFORMATION-ONLY-v2.md),
> recorded in `.ai/roadmap-state.yaml:human_pte_information_only_planning_20261024`.
> The original narrative below, including its Identity, approvals and rationale,
> is retained as history. Its future package order, snapshot/preservation PTE-04,
> PTE-05/06 recovery/REPAIR sequence, mandatory TASK-401 r3 PTE-07 chain and
> mandatory TCA-2 / ROA-1 R3 / MOA-1 instructions are historical superseded text,
> not active instructions or resumable dependencies under this plan. PTE-04 is
> now conditional source-bound read-only observation; PTE-05/06 leave PTE scope;
> PTE-07 becomes bounded information-only conformance. Published PTE-01..03 and
> all exact TASK/RUN/REVIEW/publication history keep their original meanings.
> The historical NEXT statements below do not compete with current canonical NEXT.

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

### PTE-03 — Source-bound diagnostic access across FAILURE phases

**Human-approved 2026-10-23 architecture audit (AOA-1 v1):** Extend the original native EXECUTION FAILURE objective to admitted VERIFICATION and COMPLETION_GATE failures; keep pre-RUN ingress/transport failures in their existing operational-receipt readers. This remains ONE PTE-03 implementation package. Reuse existing `verification.py`, `review_transport.py`, `operator.py`, `brain_attention.py` and portable `brain_context.py` seams. No independent log store, DB, extra lifecycle reducer, generic router, retry authority or automatic REPAIR choice.

**Observed motivating sources:** RUN-401-002 native patch-context failure must retain exact origin and dirty-worktree distinction without inventing the sole CLI cause. RUN-403-001 verification exit 1 exposed `MALFORMED_SUMMARY`, empty failed nodes and unknown cause while Runtime had captured stdout/stderr. RUN-403-002 produced a failed test identity but `error_types: [UNKNOWN]`, lacking discriminating traceback/assertion content. Immutable original RUN/FAILURE identities are unchanged; testing may use authenticated original sources or faithful fixtures and must distinguish those.

**Field-level diagnostic release policy:**
- Class A — validated structural identity: source-bound TASK/RUN/revision/phase/SHA/digest, verification command, exit code, safe test identity, exception class, repo-relative line, failed count, authority, worker/job status and typed blocker should be delivered without blanket withholding.
- Class B — technical source spans: assertion actual/expected, relevant traceback frames, patch context and diff, bounded test parameter excerpts, selected stdout/stderr lines may be delivered only with explicit field-level secret/PII inspection, redaction markers, source authenticity and byte/line budgets. A secret in one span must not suppress independent safe facts.
- Class C — protected raw: complete stdout/stderr, local environment and executor transcript remain inside authorized self-host origin; only a derived bounded safe projection crosses the boundary.
- Class D — denied/removed: credentials, tokens, cookies, private keys, unnecessary personal/customer identifiers, unsafe paths, unknown-origin or forged/stale/cross-RUN/ref sources, oversize captures and unauthorized access. Redact affected spans or block only the affected source, returning precise availability, redaction and blocker codes. Parser failure is NOT confidentiality classification.

**Source ladder:** Check canonical public/safe failure detail and existing paged observation first; check separately authenticated GitHub Actions/job/step evidence when correctly attributable; then request the existing protected self-host reader for the exact RUN/source if necessary. Use actual SHA/digest/currentness and bounded origin authorization. If parsing a pytest summary fails or a parsed node lacks assertion evidence, continue to the next available lawful source; if no source remains, return precise SOURCE_MISSING / PARSER_UNSUPPORTED / INTEGRITY_MISMATCH / ACCESS_DENIED / LIMIT_EXHAUSTED and honest UNKNOWN. No need to rerun original verification merely to extract diagnostic text. Do not disclose whole raw logs, silently replace a source, reconstruct historical facts, or call diagnostic output a Runtime PASS.

**Failure-family matrix:** admitted EXECUTION (CLI invocation, patch mismatch, output format, timeout/interruption, network/auth/quota, dirty candidate); VERIFICATION (assertion/fixture/import, pytest/xdist setup, worker crash, missing Git historical object, unstable test, malformed/ANSI/partial summary, timeout and exit categories); COMPLETION_GATE (missing AC claims, unresolved, ResultPackage/schema, EVIDENCE, scope/dirty). Pre-RUN ingress, wake, carrier transport and REVIEW/PUBLISH retain their own reader and authority, supplying bounded operational causes without fabricated RUNs. Unknown future failures report OTHER/UNKNOWN with exact phase, witness, limitation and next lawful evidence source.

**PTE-03 acceptance gates (mandatory for TCA-2 TASK authoring):**
1. Exact phase coverage and pre-RUN boundary (no fake RUN).
2. Bound TASK/RUN/revision/terminal/failed SHA, private source digest, source currentness and channel authorization.
3. Malformed/unsupported/ANSI/truncated pytest parsing does not terminate while a lawful raw or structured source exists; otherwise precise typed blocker.
4. Safe failed node, exception, file:line, expected/actual/assertion/traceback where evidence exists; do not claim semantic cause from an exit code alone.
5. Selective span/field redaction with explicit `redacted` reason; no whole-output suppression of independent safe fields.
6. Reject stale/forged/opposite/cross-task/cross-run/wrong-origin/competing refs and payload-instruction attempts.
7. Protected self-host-only raw, no public token/cookie/private key/PII exposure, path traversal, symlink/reparse, URL spoofing or accidental credential echo.
8. Bounded bytes, time, read count, node paging, source fallback and positive/negative observability; no full-suite default.
9. Real authenticated Brain consumer automatically receives diagnostic when an allowed source exists, without asking Human to run diagnostic Python/PowerShell.
10. Regression examples RUN-401-002, RUN-403-001 and RUN-403-002 plus synthetic benign/malicious sources; test parser success AND failure.
11. Legacy safe detail V1–V4 and pages, direct-call APIs, authenticated readers and current tests remain valid.
12. No mutation by a reader, no altered historical FAILURES, no self-selected repair, no second state authority; Runtime verifies, Brain diagnoses, Executor edits, Reviewer and Publisher keep distinct authority.

**Closure:** PTE-03 is incomplete until both a source-bound technical error and an authenticated Brain read across the actual supported transport succeed, or an exact unavailable-source blocker is demonstrated. PTE-04/05/06 and TASK-401 r3 remain separately gated and are not automatically activated.

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
