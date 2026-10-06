# AIOS Brain Optimization Roadmap v1

Status: **HUMAN_APPROVED — PREEMPTS H4 IMPLEMENTATION**  
Authority: Human / Brain planning  
Approved: 2026-10-06  
Repository: `trung-via/AIOS-renew`

## 1. Human priority decision

The Human explicitly prioritizes Brain/control-plane optimization ahead of TASK-310 and
all remaining H4 implementation. H4 semantics, already reviewed H4 architecture, and the
reserved TASK-310/TASK-311/TASK-312 objectives remain intact, but implementation is paused
until this roadmap closes.

This roadmap does not itself create a TASK, select an Executor/model/effort for a new TASK,
create a RUN, claim verification, or advance H4. Each implementation TASK remains subject
to the existing Human delegation boundary and ordinary Runtime/Reviewer/Publisher lifecycle.

## 2. Optimization North Star

Target operating shape:

```text
Human intent
 -> one bounded fresh context projection
 -> Brain semantic reasoning once
 -> two-stage Brain audit
 -> final canonical artifact
 -> deterministic ingress
 -> deterministic continuation under existing Human authority
 -> self-host
```

On resume:

```text
wake / Human continuation
 -> one minimum fresh projection
 -> one semantic continuation
 -> deterministic next boundary
```

Brain should not spend semantic-model effort reconstructing transport plumbing, repeatedly
hydrating unchanged policy, rereading canonical identifiers just emitted by ingress,
reselecting an already Human-bound execution profile, scanning unbounded planning history,
or reconciling stale duplicated normative text.

## 3. Frozen safety boundaries

Optimization must preserve:

1. Human ownership of intent, priority, risk acceptance and Executor/model/effort delegation.
2. Brain ownership of WHAT/WHY and semantic task design.
3. exactly one active Executor owning HOW.
4. Runtime ownership of canonical admission, verification, lifecycle reduction and evidence.
5. Reviewer semantic verdict authority and Publisher exact-source publication authority.
6. expected-main/CAS, exact lineage, replay/idempotence and mutation-conflict fail-close.
7. exact origin-affine provenance requirements for new revision-1 ORIGIN_AFFINE TASKs.
8. no chat memory, transcript similarity, recent-tab, timestamp or repository-default authority.
9. no automatic roadmap advancement or semantic routing by deterministic transport.
10. two-stage Brain audit remains semantic discipline even where serialized audit plumbing is retired.

## 4. Two-stage planning audit

### Stage 1 — CONSTRUCT

Risks found:

- optimization could become one mega-task and make failures hard to localize;
- removing duplicated validation could accidentally remove a trust-boundary check;
- compacting roadmap history could create a second planning database or lose provenance;
- minimum fresh sync could become stale semantic cache rather than fresh canonical projection;
- sticky execution-profile reuse could silently choose a profile for a new TASK;
- AUTHOR_TASK-to-PRIMARY continuation could be mistaken for automatic planning authority;
- architecture-to-TASK delta audit could skip new task-specific risks;
- REMEDIATION/REPAIR simplification could overgeneralize AUTHOR_TASK findings;
- batching planning mutations could hide distinct Human decisions;
- provider/token optimization could weaken stateless/provider-neutral substitution protection.

### Stage 2 — ADVERSARIAL_AUDIT_AND_RECONCILE

Closure: **CLEAR_WITH_PHASE_GATES**

Reconciliation:

- implementation is split into bounded phases BO-1 through BO-9;
- every phase preserves an explicit list of authority/trust-boundary invariants;
- deterministic freshness checks may replace repeated semantic recomposition only when exact
  binding/digest/currentness is independently proved;
- canonical historical evidence remains in Git/artifact history; no second planning store is added;
- execution-profile reuse is valid only for the same exact TASK identity under existing Human
  delegation and expires on TASK-id change or explicit Human override;
- deterministic continuation may carry out already-authorized lifecycle transport, but may not
  choose roadmap work, TASK meaning, Executor/model/effort, review verdict or correction strategy;
- delta audit reuses only fingerprinted settled architecture; every TASK still receives two-stage
  task-specific audit and must expand context on ambiguity/conflict;
- BO-7 is audit-first and may retain existing REMEDIATION/REPAIR handoff where a distinct invariant
  is proven necessary;
- planning batching combines only one already-made Human planning decision and cannot merge separate
  approval/risk decisions;
- BO-9 is conditional on measurement and must preserve self-contained stateless provider requests.

## 5. Execution sequence

### BO-0 — Priority preemption and canonical sequencing

Status: **COMPLETED BY HUMAN PLANNING DECISION**

Purpose: make Brain optimization the sole planning priority before TASK-310, remove stale
approval ambiguity in roadmap sequencing, and freeze H4 implementation until BO-1..BO-9 close.

Exit: `BRAIN_OPTIMIZATION_PREEMPTION_CANONICAL`.

### BO-1 — AUTHOR_TASK mutation-path simplification

Purpose: implement the already Human-approved retirement of mandatory serialized
`AIOS_AUDITED_AUTHORING_HANDOFF` for AUTHOR_TASK.

Remove from AUTHOR_TASK mutation prerequisites:

- serialized Decision Packet;
- packet/construct/reconciled audit fingerprints as mutation authority;
- Stage-1/Stage-2 serialized handoff;
- acceptance-phase ledger as ingress transport;
- repeated audit-support reconstruction.

Preserve direct TASK schema/revision/verification validation, expected-main CAS, return-affinity,
origin-proof admission, carrier/envelope binding, idempotence and mutation conflict fail-close.

Also converge normative documentation so Project Contract, TASK Authoring Contract and semantic
handoff hardening text do not require contradictory AUTHOR_TASK gates.

Exit: `AUTHOR_TASK_DIRECT_FINAL_CONTRACT_INGRESS_ACTIVE`.

### BO-2 — Bounded active-roadmap projection and history boundary

Purpose: stop normal Brain Sync from loading/scanning the current ~600 KB monolithic roadmap
history merely to identify current planning state.

Target:

- bounded current planning projection containing active track, unique NEXT, current blockers,
  current Human decisions/delegations and exact predecessor anchors;
- historical planning remains canonical through Git/history/archive evidence, not a new database;
- normal sync does not ancestry-scan every historical DONE item;
- ambiguity/conflict can explicitly expand into historical material.

Exit: `BOUNDED_ACTIVE_PLANNING_PROJECTION_ACTIVE`.

### BO-3 — MINIMUM_FRESH_BRAIN_SYNC_V1 production fast path

Purpose: productionize the already approved minimum-fresh-sync design.

Always fresh: main identity, exact subject, exact lineage, TASK id/revision, unresolved status,
Unified State, next_action, authority and Flow Card.

Hydrate only selected-flow material. Unchanged governance/spec bodies may be reused only after
fresh exact digest/binding proof. Ambiguity expands context rather than guessing.

Exit: `MINIMUM_FRESH_BRAIN_SYNC_PRODUCTION_ACTIVE`.

### BO-4 — Task-scoped sticky execution profile

Purpose: implement the already approved `TASK_SCOPED_STICKY_EXECUTION_PROFILE_V1`.

The Human selects Executor/model/effort once per TASK. The exact profile is reused for PRIMARY,
REMEDIATION, REPAIR and continuation within that TASK unless Human explicitly changes it.
New TASK id always requires fresh Human selection. No adaptive fallback/difficulty routing.

Exit: `TASK_SCOPED_STICKY_EXECUTION_PROFILE_ACTIVE`.

### BO-5 — Exact AUTHOR_TASK-to-PRIMARY continuation

Purpose: remove Brain canonical reread/manual selector glue after successful TASK authoring.

AUTHOR_TASK success exposes a bounded exact identity sufficient for continuation, including
TASK id, revision, blob SHA and authoring commit/main identity. Where an exact current Human
task-scoped execution delegation already exists, deterministic transport may dispatch PRIMARY
without Brain copying/re-discovering those selectors.

Runtime independently revalidates TASK commit/blob/revision/currentness before RUN admission.
No planning or execution-profile choice moves into Runtime/transport.

Exit: `AUTHOR_TASK_PRIMARY_EXACT_CONTINUATION_ACTIVE`.

### BO-6 — Delta semantic audit over settled architecture

Purpose: retain two-stage Brain audit while eliminating repeated reasoning over already-settled,
fingerprinted architecture/governance.

TASK audit focuses on architecture fidelity, task-specific scope/non-goals, acceptance,
verification and new counterexamples. Constitutional preflight becomes a bound governance
input/lens rather than a separate redundant semantic pass.

Any architecture fingerprint movement, ambiguity or conflict forces expanded fresh audit.

Exit: `TASK_SPECIFIC_DELTA_AUDIT_ACTIVE`.

### BO-7 — REMEDIATION/REPAIR authoring duplication audit

Purpose: determine whether serialized audited-handoff plumbing remains necessary for
AUTHOR_REMEDIATION and AUTHOR_REPAIR.

This phase is audit-first. It must distinguish semantic authority from direct canonical
family validation/currentness/CAS. Remove only proven redundant recomposition. Preserve any
distinct correction-lineage or strategy invariant that cannot be enforced by direct bounded
validation.

Exit: `CORRECTION_AUTHORING_DUPLICATION_RESOLVED` (retained or simplified with bounded basis).

### BO-8 — Planning transaction batching / main-churn reduction

Purpose: reduce multiple near-consecutive planning commits for one already-made Human decision.

One Human planning decision should normally produce one atomic canonical planning mutation when
the affected files are known together. Distinct Human approvals, risk acceptances or later
semantic decisions remain separate transactions.

Exit: `PLANNING_MUTATION_BATCHING_ACTIVE`.

### BO-9 — Conditional provider/context serialization optimization

Purpose: measure remaining Brain/Reviewer/RA payload and token cost after BO-1..BO-8.

Only optimize Stage-1/Stage-2, Reviewer or Research Assurance serialization where measurement
shows material residual cost. Preserve self-contained stateless requests, exact lineage,
substitution protection and provider neutrality. If residual gain is immaterial, close NO_CHANGE.

Exit: `BRAIN_PROVIDER_PAYLOAD_COST_RESOLVED`.

## 6. H4 resume gate

TASK-310 and all remaining H4 implementation stay blocked until BO-1 through BO-9 are closed
(or a phase closes NO_CHANGE after its required audit).

Before returning to TASK-310:

- fresh Brain Sync using the optimized path;
- prove the H4 roadmap/objective is still current;
- confirm TASK-310 remains unauthored and required;
- confirm its previously recorded Executor delegation is still valid under the final sticky-profile
  contract, otherwise obtain fresh Human selection;
- author TASK-310 only through the final reviewed AUTHOR_TASK path.

Exit: `BRAIN_OPTIMIZATION_V1_CLOSED_RETURN_TO_TASK310`.

## 7. Stop boundaries

Stop for Human only when a phase introduces a new semantic/constitutional tradeoff, needs a new
TASK's Executor/model/effort selection, requires risk acceptance, or proposes scope outside this
approved optimization program. Ordinary implementation/result/review/repair/publication inside an
already authorized optimization TASK follows canonical lifecycle without ceremonial Human stops.

No phase automatically advances H4 or authors TASK-310.
