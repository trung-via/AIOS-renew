# AIOS H4 Execution Roadmap v1

Status: **HUMAN_APPROVED**  
Authority: Human / Brain planning  
Prepared: 2026-10-06  
Approved: 2026-10-06  
Repository: `trung-via/AIOS-renew`

This roadmap is the Human-approved execution gate for the audited H4 architecture. Its
approval does not itself author a TASK, select an Executor, create a RUN, claim
verification, advance H4/H5, or mutate historical engineering lineage. Each new TASK
identity still requires the explicit Human delegation gate stated below.


## Pre-H4 Brain optimization preemption — approved 2026-10-06

The Human explicitly moved the complete Brain Optimization Roadmap v1 ahead of TASK-310.
This H4 roadmap remains authoritative for H4 semantics and sequencing after optimization,
but TASK-310 and all remaining H4 implementation are paused until
`BRAIN_OPTIMIZATION_V1_CLOSED_RETURN_TO_TASK310`.

The optimization program is defined in
`docs/AIOS-BRAIN-OPTIMIZATION-ROADMAP-v1.md`. It does not alter TASK-310/TASK-311/TASK-312
objectives or H4 safety invariants. Any optimization defect or architecture change is resolved
inside the optimization program before H4 resumes.

## 1. Goal

Reach the final H4 production shape in which:

- one ordinary Human message from any supported regular Chat, including another browser
  or phone, is sufficient to start or continue an AIOS flow;
- no normal production flow requires the Human to return to the laptop and press Connect;
- exact conversation identity is proved through bounded opaque rendezvous, never recent
  chat, active tab, timestamp, transcript similarity or model memory;
- immutable canonical TASK/RUN base affinity remains historical truth;
- Human-authorized TASK continuation may select a different effective destination without
  repointing the conversation route or moving unrelated TASKs;
- H4D only performs safe acquisition/delivery to an already resolved exact destination;
- independent conversation lanes make bounded progress without cross-delivery,
  duplicate send or execution-runner starvation;
- final H4B semantic resume and H5 conformance use this exact production path.

## 2. Architecture invariants that are frozen for implementation

These are not retirement candidates:

1. Human owns intent, priority, risk acceptance, delegation and constitutional change.
2. Brain owns semantics/roadmap/TASK meaning but does not implement production code.
3. Runtime remains the only canonical verification/lifecycle reducer.
4. Reviewer and Publisher authorities remain separate.
5. Frozen Kernel v0.1 is unchanged.
6. Raw Chat URLs, credentials, transcripts and rendezvous markers remain noncanonical.
7. Canonical TASK/RUN base `return_affinity` is immutable historical lineage truth.
8. Exact canonical attention identity and root TASK reconstruction precede continuation.
9. Conversation route identity is conversation-scoped; TASK continuation is
   repository+TASK scoped.
10. Ambiguous/submitted attempts are never blindly resent.
11. Missing/uncertain route, continuation or rendezvous state fails closed.
12. No repository-default, recent/active-chat, timestamp or semantic fallback.
13. Wake/rendezvous transport never reads assistant output to choose lifecycle action.
14. No Planner, generic router, retry/failover authority, Reviewer, Publisher or second
    engineering-state store is created.

## 3. Human-approved retirements

### R1 — Page-scoped bootstrap as sole origin-proof issuer — APPROVED

Retire only the normative **sole-source** requirement.

Preserve:

- self-hosted machine-local exact-origin admission;
- exact route handle + generation proof;
- carrier/run-attempt binding;
- TASK id + expected-main binding;
- ingress-envelope digest binding;
- deployment-owned HMAC receipt;
- bounded freshness, replay and same-attempt idempotence;
- no self-certified TASK provenance.

Transition rule:

- TASK-310 itself may be bootstrapped with one lawful current page-scoped proof;
- page bootstrap remains active until reviewed TASK-310 production issuer activation;
- TASK-310 publication alone does not activate the new issuer;
- explicit canonical activation follows reviewed publication;
- after activation, ordinary production authoring no longer requires Connect;
- page bootstrap may remain only as bounded manual diagnostic/recovery compatibility.

### R2 — Shared-runner periodic all-lanes cron — APPROVED

Retire the current `*/5` recovery schedule from the PRIMARY/REPAIR/REMEDIATION critical
runner path.

Preserve durable deferred recovery, canonical revalidation, dedupe and ambiguity safety.

Replacement may be event-driven, separately isolated capacity, or another reviewed
nonblocking transport mechanism. It must not become a generic retry daemon or lifecycle
router.

### R3 — Standalone H4D natural-event gate — CONDITIONAL, NOT RETIRED

Keep it opportunistically. Any naturally occurring qualifying event during the phases
below may close H4D.

If TASK-310 and TASK-311 are both reviewed/published and no qualifying event has occurred,
Brain must stop and ask Human whether to fold the same mandatory target-not-preopened
evidence into H4E. Evidence is never waived.

## 4. Detailed execution sequence

### Phase 0A — AUTHOR_TASK ingress simplification transition

Status: **MOVED TO BRAIN OPTIMIZATION ROADMAP BO-1; H4 PAUSED**

Human decision already approved:

- retire mandatory serialized `AIOS_AUDITED_AUTHORING_HANDOFF` as an AUTHOR_TASK
  mutation prerequisite;
- keep the two-stage Brain audit as semantic authoring discipline;
- preserve Runtime ownership of canonical mutation and all exact provenance gates.

Reason:

The current ingress requires Brain to reconstruct a full deterministic Decision Packet,
Stage-1/Stage-2 audit envelope, packet fingerprints and acceptance-phase ledger merely to
submit a final TASK contract. That cognitive-support plumbing is not canonical engineering
truth and is duplicative once Human/Brain planning has already fixed TASK semantics.

Target production shape after this transition:

```text
Human-approved roadmap + Human Executor selection
  -> Brain two-stage semantic audit
  -> final TASK contract
  -> AUTHOR_TASK carrier
  -> Runtime validates:
       exact task identity
       expected canonical main
       final TASK schema/scope/verification contract
       exact ORIGIN_AFFINE provenance where applicable
       carrier attempt / envelope digest / replay safety
  -> canonical TASK
```

AUTHOR_TASK must no longer require:

- serialized Decision Packet;
- `packet_fingerprint`;
- `construct_fingerprint`;
- Stage-1/Stage-2 audit envelope;
- `acceptance_phase_ledger` as ingress transport material;
- deterministic reconstruction of Brain audit support sections.

The Runtime must still fail closed on invalid TASK contract, stale expected-main,
origin-proof mismatch, carrier-attempt mismatch, replay conflict or mutation conflict.

Bootstrap paradox and transition:

- the currently published Runtime still enforces the old audited-handoff gate;
- Brain must not bypass Runtime by committing a TASK file directly;
- therefore exactly one transition implementation TASK may need to pass through the old
  gate one final time;
- after that reviewed implementation is published and activated, TASK-310 is authored
  through the simplified ingress;
- do not widen TASK-310 itself with this control-plane cleanup.

The roadmap-approval prerequisite for this transition is satisfied. The transition now
belongs to Brain Optimization Roadmap v1 BO-1 and remains unauthored only until its own fresh
Human Executor/model/effort delegation is supplied; TASK-310 remains blocked until the complete
Brain Optimization Roadmap v1 closes.

Exit: `AUTHOR_TASK_SERIALIZED_AUDIT_HANDOFF_RETIRED_IN_RUNTIME`.

### Phase 0 — Governance transition and roadmap approval

Status at roadmap creation:

- integrated H4 two-stage audit: complete;
- R1 Human approval: complete;
- Project Contract sole-issuer coupling: prospectively retired;
- R2 Human approval: complete;
- R2 implementation: not started;
- TASK-310/TASK-311/TASK-312: UNAUTHORED.

Gate P0:

- Human approves this roadmap.
- No TASK authoring before P0 PASS.

Exit: `H4_EXECUTION_ROADMAP_APPROVED`.

### Phase 1 — TASK-310: device-independent exact-origin rendezvous

Candidate identity: **TASK-310**  
Contract: `OPAQUE_ORIGIN_RENDEZVOUS_V1`

Purpose:

Implement one bounded two-way handshake:

```text
Human message
  -> bounded marker precommit
  -> exact assistant marker render
  -> self-host exact account search
  -> bounded same-token indexing retry
  -> sole candidate open + marker re-proof
  -> final uniqueness/stability recheck
  -> route allocate/reuse
  -> exact-origin authoring proof
  -> one transport-only completion signal to the proved Chat
  -> fresh Brain Sync
```

Mandatory properties:

- one normal Human message is sufficient;
- no normal Connect gesture;
- marker is correlation evidence, never credential/lifecycle authority;
- zero/multiple/incomplete/unavailable search fails closed;
- completion signal is not Brain Attention and selects no lifecycle action;
- carrier/render/search/completion partial failures create no TASK/RUN/continuation
  mutation;
- duplicate/stale completion is idempotent;
- newer explicit Human intent is reconciled after fresh sync and may supersede the older
  pending semantic action;
- route registry, generation and TASK-309 admission safety are reused;
- index wait/recovery cannot starve execution-critical runner capacity;
- no provider-specific GitHub connector dependency in core semantics.

Authoring precondition:

- Brain Optimization Roadmap v1 closure PASS;
- roadmap P0 PASS;
- fresh Human Executor/model/effort selection;
- one lawful transition page-scoped proof may bootstrap TASK-310 itself.

Verification:

- focused rendezvous/origin/authoring/wake/carrier tests;
- full suite because shared origin registry, provenance and wake boundaries are composed;
- Runtime verification only; Executor claims are insufficient.

Review/publication gate:

- semantic Review PASS;
- exact reviewed candidate publication;
- no automatic activation of replacement issuer.

Exit: `TASK310_REVIEWED_PUBLISHED`.

### Phase 2 — TASK-310 issuer activation and live handshake observation

No new implementation TASK is assumed.

Brain/Human must fresh-sync the reviewed publication, then explicitly activate the
reviewed rendezvous issuer as a valid exact-origin proof source under the Project Contract.

Required live observation:

- initiate from a fresh supported regular Chat on another browser/phone;
- Human sends one ordinary message only;
- rendezvous resolves the correct conversation;
- completion returns to that exact conversation;
- Brain resumes through fresh sync without Human second message or laptop Connect;
- raw marker/URL remain noncanonical.

Negative/live boundaries:

- delayed index convergence is bounded;
- permanent zero/multiple/unavailable search fails closed;
- duplicate/stale completion is harmless;
- non-writable destination fails closed;
- no fallback selection.

If implementation defect is found: stop, audit the defect, and obtain Human approval
before any correction TASK is authored.

Exit: `DEVICE_INDEPENDENT_ORIGIN_ISSUER_ACTIVE`.

### Phase 3 — TASK-311: subject-scoped continuation

Candidate identity: **TASK-311**  
Contract: `SUBJECT_SCOPED_CONTINUATION_BINDING_V1`

Dependency: Phase 2 PASS.

Purpose:

Allow exactly one Human-selected canonical TASK subject to continue in a newly proved Chat
without changing historical TASK/RUN affinity or moving unrelated TASKs.

Core state:

```text
(repository, TASK id)
  -> immutable base affinity
  -> current effective destination
  -> monotonic continuation epoch
```

Required behavior:

- TASK-1/TASK-2/TASK-3 may share source Chat A;
- moving TASK-1 to D does not move TASK-2/TASK-3;
- generic `continue` works only when canonical state yields one eligible subject;
- otherwise Human disambiguation is required;
- same TASK revisions retain binding;
- distinct replacement TASK ids do not inherit automatically;
- RESULT/FAILURE/REVIEW/REMEDIATION/REPAIR/publication/recovery prove root TASK before
  continuation lookup;
- event/continuation race is linearizable to old or new epoch;
- PENDING/DEFERRED pre-submit event may rehome exactly once;
- AMBIGUOUS/SUBMITTED old-destination event remains pinned and never resends;
- multiple subjects targeting one Chat share the existing conversation lock;
- continuation store loss/corruption fails closed and never silently reverts to base Chat;
- H4D current `base affinity == delivery binding` assumption is refactored into:
  base-affinity proof + continuation proof -> exact effective destination -> H4D.

Authoring precondition:

- fresh Brain Sync after Phase 2;
- roadmap still approved and unchanged in relevant semantics;
- fresh Human Executor/model/effort selection for TASK-311.

Exit: `TASK311_REVIEWED_PUBLISHED`.

### Phase 4 — Retire critical-runner periodic recovery

Implementation identity: **UNASSIGNED — MUST NOT BE AUTHORED BEFORE HUMAN REVIEW AT THIS PHASE**

Dependency:

- may be implemented after Phase 1;
- must be complete before H4E live execution.

Purpose:

Remove current `*/5` all-lanes recovery from the execution-critical self-host runner
without losing durable deferred recovery.

Acceptance boundary:

- no scheduled recovery job can indefinitely block PRIMARY/REPAIR/REMEDIATION;
- deferred events remain durable;
- canonical freshness and exact-event dedupe remain;
- ambiguous attempts remain proof-only;
- independent eligible lanes retain bounded progress opportunities;
- no generic retry/failover daemon;
- no roadmap/lifecycle authority.

Brain must perform a fresh targeted audit immediately before authoring because actual
workflow/runtime state may have changed by then. Human reviews scope and selects Executor.

Exit: `WAKE_RECOVERY_CRITICAL_RUNNER_ISOLATION_PASS`.

### Phase 5 — H4D live-exit reconciliation

H4D is not reimplemented here.

First, inspect whether a naturally occurring event during Phases 1–4 already proves:

- exact origin-affine target;
- target not pre-opened/prepared by Human;
- unattended environment acquisition/restoration;
- exact target/surface gates;
- exactly one doorbell;
- no Human UI preparation.

If yes: Human/Brain may close H4D from that evidence.

If no, and both TASK-310 and TASK-311 are already reviewed/published: **STOP FOR HUMAN
DECISION** on conditional R3. Do not fabricate or replay an event.

Possible Human choices then:

- keep waiting for a natural H4D carrier; or
- retire H4D as a standalone sequencing gate and fold the identical evidence obligation
  into H4E.

Exit: `H4D_LIVE_EVIDENCE_SATISFIED_OR_HUMAN_FOLD_DECISION`.

### Phase 6 — H4E integrated production routing/concurrency conformance

Prerequisites:

- TASK-310 reviewed/published and issuer activated;
- TASK-311 reviewed/published;
- Phase 4 runner-recovery isolation PASS;
- H4D live evidence satisfied, or Human explicitly approved R3 fold.

H4E proves the integrated production shape, including:

Origin:

- other-device origin with one Human message/no Connect;
- zero->one bounded index convergence;
- permanent zero/multiple/incomplete/unavailable fail closed;
- exact completion resume to proved Chat.

Continuation:

- TASK-1/2/3 source-sharing with only TASK-1 moved;
- queued/paused TASK resumed after TASK-2/TASK-3;
- active RUN continuation;
- same TASK revision vs replacement TASK behavior;
- competing destination CAS;
- A->B->C and explicit route revisit.

Race/lineage:

- all attention families reconstruct the same root TASK;
- pre-submit rehome;
- post-submit ambiguity pinning;
- transfer/event race linearizability;
- resolved/superseded NOOP;
- no root TASK -> no continuation override.

Concurrency:

- one conversation lock for multiple subjects/repositories;
- different Chats progress independently;
- draft/generation in A does not globally block B;
- acquisition/recovery cannot permanently starve another eligible lane.

Recovery/capacity:

- process restart safety;
- no unresolved-state silent eviction;
- route/continuation registry uncertainty fails closed;
- no critical-runner starvation.

Any failure is a blocker. Brain audits it before proposing correction; no automatic retry
or correction TASK authoring.

Exit: `H4E_PRODUCTION_CONFORMANCE_PASS`.

### Phase 7 — Final H4B production-shape semantic resume proof

Dependency: H4E PASS.

Use one real unresolved production attention path:

```text
canonical attention
 -> exact root TASK
 -> base affinity + optional continuation
 -> exact effective destination
 -> unattended H4D delivery
 -> selector-only doorbell
 -> minimum fresh Brain Sync
 -> semantic continuation
```

Forbidden substitutes:

- model/chat memory;
- assistant transcript scraping;
- fabricated checkpoint;
- replay of already resolved attention;
- repository-default fallback.

Exit: `H4B_FINAL_PRODUCTION_SHAPE_PASS`.

### Phase 8 — H5 H4-related integration/conformance closure

Dependency: final H4B PASS.

Close the full H4-related H5 matrix, including:

- origin capture integrity;
- same-repository multi-chat isolation;
- no silent default route;
- subject-scoped continuation safety;
- unattended delivery;
- transport independence;
- production multi-lane live proof;
- production-shape semantic resume;
- base-affinity/effective-destination separation;
- multi-lane fairness/no global acquisition lock-in;
- continuation-state fail-close;
- zero-Human-followup rendezvous handshake.

H5 does not auto-close from tests/workflows. Human/Brain owns planning closure after exact
canonical evidence is reconciled.

Exit: `H5_H4_CONFORMANCE_CLOSED`.

### Phase 9 — Return to the blocked TASK-308 objective

Candidate identity: **TASK-312**, still UNAUTHORED.

Default sequencing in this roadmap places TASK-312 after H4/H5 production closure so the
replacement consumes the fully proved production origin/continuation shape instead of
becoming another H4 architecture experiment.

Before TASK-312 authoring:

- fresh Brain Sync;
- confirm TASK-308 BLOCKED lineage remains unchanged;
- confirm TASK-309 provenance lineage remains valid historical evidence;
- confirm TASK-312 objective is still needed and unchanged;
- fresh Human Executor/model/effort selection.

Human may explicitly move TASK-312 earlier after TASK-310/TASK-311 publication if they
want it to serve as a real production consumer, but that is a prospective priority change
and must be canonicalized before execution.

## 5. Stop/approval gates

Brain must stop for Human at:

1. approval/rejection/modification of this roadmap;
2. Executor/model/effort selection for each new TASK identity;
3. any architecture-affecting defect or new retirement proposal;
4. conditional H4D standalone-gate decision if no natural carrier appears;
5. any acceptance of risk after a fail-closed production observation;
6. H4E/H4B/H5 planning closure decisions.

Brain does **not** stop merely because an already approved task enters ordinary
RUN/RESULT/FAILURE/REVIEW/REMEDIATION/REPAIR flow; it continues according to canonical
lineage and existing authority unless a genuine Human risk/intent decision is required.

## 6. No-authoring gate

This document was explicitly approved by Human on 2026-10-06 with
`APPROVE H4 EXECUTION ROADMAP V1`.

Approval releases only the roadmap gate. It does not release the per-TASK Human delegation
gate. TASK-310, TASK-311 and TASK-312 remain UNAUTHORED. In addition, TASK-310 is explicitly
blocked until Brain Optimization Roadmap v1 closes. After that closure, their own authoring
preconditions and Human Executor/model/effort delegation rules still apply. The
runner-recovery correction still has no TASK identity and requires fresh phase-specific
audit plus Human review before authoring.