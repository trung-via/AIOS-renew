# AIOS Brain Semantic Audit Protocol — Planning Evidence

Status: HUMAN/BRAIN PLANNING BASELINE
Canonical evidence cut: main 157732551f0a549b9644688a48ddc9c7c730c96d
Scope: read-only retrospective plus BP-4A architecture intent; no implementation authority

## 1. Purpose

BP-4A exists to make high-value Brain decisions reproducibly achieve the useful quality gain observed when a first design is followed by a deeper adversarial audit, without depending on chat memory or on a Human remembering to request "audit kỹ hơn".

The target protocol is:

```text
AIOS_DECISION_PACKET
  -> Construct Pass
  -> fixed repository-owned Adversarial Audit Profile
  -> transient Risk/Coverage Ledger
  -> Reconciliation Pass
  -> one final Brain semantic decision
```

This is cognitive support inside one Brain authority. It is not a new Planner, Reviewer, voting system, lifecycle router, correction selector, persistent reasoning store, or source of engineering truth.

## 2. Read-only retrospective basis

The retrospective sampled TASK-164 through TASK-178 inclusive: 15 recent canonical TASKs immediately preceding BP-4 closure.

Observed canonical shape:

- 6 of 15 TASKs received semantic TASK revisions before closure: TASK-164, TASK-167, TASK-168, TASK-174, TASK-177, and TASK-178.
- 8 of 15 TASKs have at least one REPAIR lineage in their history: TASK-165, TASK-166, TASK-167, TASK-168, TASK-172, TASK-173, TASK-174, and TASK-177.
- 5 of 15 TASKs reached reviewed/published closure in revision 1 without REPAIR lineage: TASK-169, TASK-170, TASK-171, TASK-175, and TASK-176. This is evidence against forcing an expensive deep protocol on every routine Brain action.
- TASK-178 r2 exposed the separate semantic-review failure class: nine reviewed RUNs from RUN-178-001 through RUN-178-009 and eight canonical REMEDIATION authorizations before the final reviewed candidate was published.

These counts are descriptive, not a causal experiment. TASK difficulty and selection bias differ. They justify a bounded protocol and future measurement, not a claim that two passes guarantee fewer failures.

## 3. What the deeper audits changed

Canonical revision and review deltas repeatedly fell into the following lens families:

1. Authority boundary
   - TASK-167 corrected Executor-vs-Runtime verification ownership and completion ordering.
   - TASK-177 strengthened Human request vs canonical engineering facts, Brain/Reviewer ownership, and side-flow precedence.
   - TASK-178 prevented deterministic support from selecting semantic corrections and removed duplicate semantic-enum authority.

2. Scope and non-goals
   - TASK-164 r2 failed closed against production widening while modernizing only stale test fixtures.
   - TASK-168 r2 preserved useful bounded candidate work while refusing production Correction Integration changes.
   - TASK-174 r2 bounded structural-completion changes to the exact Executor contract.

3. Provenance and lineage
   - TASK-178 r2 added exact remediation authorization identity and later reviews tightened RUN/TASK/failure cross-binding.
   - TASK-164 r2 preserved failed lineage rather than reclassifying an old RUN.

4. Failure-mode discovery
   - TASK-167 found an acceptance/verification ordering contradiction and then an unnecessary Executor-shell dependency.
   - TASK-168 found the same ordering class plus unmapped known diagnostic families.
   - TASK-178 reviews found Runtime-shaped FAILURE, type-strict identity, and unbound observation cases.

5. Acceptance-criteria strengthening
   - TASK-167, TASK-168, TASK-174, TASK-177, and TASK-178 materially strengthened ACs after adversarial examination.

6. Verification correction
   - TASK-167 and TASK-168 separated pre-verification structural claims from Runtime-owned canonical verification.
   - TASK-174 hardened truthful completion semantics instead of using downstream verification as an Executor claim.

7. Portability and boundedness
   - TASK-177 bounded Human input and prohibited persistent context truth.
   - TASK-178 enforced provider-neutral packet limits, minimum necessary correction context, and machine-local exclusions.

8. Simplification / unnecessary-work removal
   - TASK-167 r3 removed redundant native Executor repository inspection.
   - FINDING-178-004 removed full RESULT/REVIEW replication from every correction-frontier item.

The recurring value comes from fixed adversarial lenses and counterexample search, not merely from spending more tokens or selecting a higher reasoning-effort model.

## 4. BP-4A protocol constraints

### Same semantic basis

Both passes must bind the same immutable Decision Packet fingerprint and the same audit profile identity. A changed or stale packet invalidates the in-flight construct/audit state; the protocol does not merge decisions made over different canonical subjects.

### Exactly two bounded passes

Pass 1 constructs the best current semantic candidate.

Pass 2 is adversarial. It attempts to falsify or improve that candidate using only the same Decision Packet plus bounded transient pass-1 outputs. It must not recursively invoke further audits or turn into an unbounded "think until satisfied" loop.

### Repository-owned audit profile

The audit profile is versioned procedural policy. It contains audit lenses, applicability, bounded output shape, and reconciliation requirements. It contains no predetermined verdict, task content, correction choice, executor choice, or provider-specific prompt.

A future BP-5 AIOS_BRAIN_REQUEST may bind an immutable audit_profile_ref. BP-4A does not itself define the provider request/adapter protocol.

### Transient candidate and Risk/Coverage Ledger

The construct candidate and ledger are request-scoped cognitive support. They are not canonical lineage, lifecycle state, model memory, a conversation store, or a reasoning database.

The ledger records bounded lens coverage such as checked / risk-found / not-applicable, the counterexample or contract conflict found, and the required reconciliation disposition. It does not rank findings, vote, or become correction-frontier truth.

### Adversarial lenses

The initial profile must cover at least:

- authority ownership and forbidden authority transfer;
- scope/non-goal leakage;
- exact provenance/lineage binding;
- plausible failure modes and malformed-state counterexamples;
- acceptance-criteria completeness and internal consistency;
- verification ownership, ordering, and minimum-sufficient evidence;
- portability, privacy, boundedness, and machine-local leakage;
- simplification, duplicate authority, and unnecessary work.

### Reconciliation

One Brain authority reconciles the construct candidate with all audit risks into one final semantic decision. Each raised risk must be resolved, explicitly retained as uncertainty/blocker, or cause fail-closed output. There is no majority vote and no second independent semantic authority.

Chain-of-thought is neither required nor stored. Only bounded protocol outputs may cross a provider boundary.

## 5. Applicability and cost control

The retrospective does not justify two-pass execution for every Brain action.

BP-4A should initially target high-value Brain-owned semantic decisions:

- TASK_AUTHORING;
- REMEDIATION_AUTHORING;
- REPAIR_AUTHORING;
- ARCHITECTURE when it changes canonical roadmap/contract intent.

DIAGNOSTIC remains single-pass unless the resulting decision crosses into one of the high-value authoring families. SEMANTIC_REVIEW remains Reviewer-owned and is explicitly outside BP-4A.

Applicability must be repository policy, not a model-scored heuristic or automatic lifecycle router.

## 6. Measurement baseline

Post-BP-4A evaluation should compare equivalent future high-value decisions against this retrospective baseline using observational metrics only:

- TASK semantic revision after first admitted execution;
- admitted RUN failure / REPAIR incidence;
- REVIEW CHANGES_REQUIRED and REMEDIATION incidence;
- defect lens caught in pass 2 before canonical authoring;
- wall time and token overhead;
- duplicate/no-value lens rate.

No target metric becomes completion truth or automatic roadmap authority. Human/Brain interpretation remains required.

## 7. BP-4A exit gate

BP-4A is complete only when reviewed/published evidence proves:

- same Decision Packet fingerprint across both passes;
- exactly two bounded passes with no recursive audit;
- repository-owned immutable audit profile identity;
- transient construct candidate plus Risk/Coverage Ledger;
- adversarial reconciliation against the fixed lens set;
- no new semantic/lifecycle/review/publication authority;
- no persistent reasoning or conversation store;
- no multi-agent voting or consensus;
- no automatic lifecycle routing, correction selection, retry, fallback, or Executor selection;
- provider-neutral protocol behavior;
- compatibility for BP-5 to bind audit_profile_ref without changing Brain authority.

## 8. Planning decision

BP-4 is engineering-complete through TASK-178 r2, RUN-178-009, REVIEW-178-009 PASS, and publication of 157732551f0a549b9644688a48ddc9c7c730c96d.

BP-4A is the next Human/Brain planning milestone. This document does not authorize implementation. A fresh executor-neutral TASK must be separately designed and canonically authored before any production mutation.
