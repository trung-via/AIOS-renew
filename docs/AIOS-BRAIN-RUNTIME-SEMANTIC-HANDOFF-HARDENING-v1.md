# AIOS Brain–Runtime Semantic Handoff Hardening v1

Status: HUMAN/BRAIN PLANNING BASELINE  
Approved priority: 2026-09-30  
Architecture subject main: `a77cb976ed70f9e066a94abc3a1e0c78db680cd2`  
Audit profile: `brain-high-value-v2`  
Scope: prospective control-plane hardening only; no engineering completion claim

## 1. Purpose

This track closes demonstrated continuity gaps between canonical AIOS state, Brain
semantic reasoning, canonical authoring mutation, correction strategy selection and
future Brain attention. It is intentionally upstream-first. Performance work resumes
only after this track closes; Python Agent adopts the final reviewed/published
generation only after both this track and VPRC close.

The target is not more autonomy. The target is fewer places where a fresh Brain must
remember an unstated procedural obligation.

## 2. Evidence basis

The architecture is grounded in current AIOS-renew and fresh downstream observations:

- BP-4A already implements a real two-stage `CONSTRUCT -> ADVERSARIAL_AUDIT_AND_RECONCILE`
  protocol for ARCHITECTURE, TASK_AUTHORING, REMEDIATION_AUTHORING and
  REPAIR_AUTHORING.
- BP-5 already provides provider-neutral request/decision identity and a freshness
  gate, but a validated Brain decision does not itself mutate canonical state.
- Current `AUTHOR_TASK` ingress validates TASK schema/revision/main/verification
  policy but can canonicalize a directly supplied TASK body without validated
  two-stage audit handoff proof.
- Current Correction Preflight evaluates reusable verification only after a concrete
  REPAIR action has already been supplied. Therefore NO_CHANGE ineligibility can be
  discovered after Brain has chosen NO_CHANGE.
- Current Runtime completion gates require every TASK acceptance id to be covered by
  Executor claims before canonical verification. A TASK acceptance statement that
  contains Runtime-owned future truth can therefore deadlock FINALIZE_CANDIDATE.
- Current terminal attention carries exact RUN, terminal kind and artifact SHA, but
  it intentionally carries no semantic continuation contract. Fresh Brain Sync still
  has to be remembered by the consumer.
- Fresh Python Agent main `fd2a07fcf5e4634ac0dcf6b1aa1d9f9c8dae9b11` remains pinned
  to AIOS-renew `44eee353eda376c9db8cd88d97184d3122651bf5` and records three
  upstream-only deferred gaps: two-stage authoring enforcement,
  FINALIZE_CANDIDATE pre-verification acceptance deadlock, and successful-REPAIR to
  subsequent REMEDIATION lineage. That pin does not contain Research Assurance.

## 3. Governing invariants

This hardening must preserve all existing authority boundaries:

- Human owns intent, priority and risk acceptance.
- Brain owns semantic interpretation and correction strategy.
- Executor owns HOW for exactly one admitted execution authority.
- Runtime owns canonical admission, identity, verification, evidence and lifecycle
  reduction.
- Reviewer owns semantic verdict.
- Publisher owns publication of the exact eligible reviewed source candidate.
- Roadmap remains Human/Brain planning state and never auto-advances from execution.
- GitHub carriers remain transport; delivery does not prove RUN, RESULT, review or
  publication truth.
- No Planner, generic lifecycle router, automatic correction selector, retry/failover
  controller, persistent reasoning store or second semantic state database is added.

## 4. Frozen-kernel compatibility

Kernel v0.1 freezes the canonical TASK/RUN/RESULT/EVIDENCE/REVIEW contract family.
This track therefore does **not** add a field to the canonical TASK acceptance schema
and does not redefine RESULT claims.

The acceptance deadlock is closed prospectively by hardening the audited authoring
handoff: before a new TASK/revision is canonically mutated, Brain must explicitly
classify acceptance statements by proof phase inside a transient audited handoff.
A condition whose truth belongs only after Runtime verification must be reconciled
out of canonical `acceptance` and represented by the existing Runtime-owned
`verification.required` contract (plus ordinary constraints/non-goals where
needed). The final frozen TASK candidate still uses the existing schema.

Historical TASKs and immutable lineage are never rewritten.

## 5. H1 — Audited Authoring Gate

### 5.1 Boundary

Prospectively require validated two-stage Brain handoff for new canonical mutations
of exactly these Brain-owned operations:

- `AUTHOR_TASK -> TASK_AUTHORING`
- `AUTHOR_REMEDIATION -> REMEDIATION_AUTHORING`
- `AUTHOR_REPAIR -> REPAIR_AUTHORING`

`SUBMIT_REVIEW` is excluded because Reviewer authority is separate. Architecture and
diagnostic output are not silently converted into artifact mutation authority.

### 5.2 Transient handoff

Introduce one carrier-neutral transient `AIOS_AUDITED_AUTHORING_HANDOFF v1`. It is
not lifecycle truth and is not persisted as a second semantic-decision database.

The handoff supplies the bounded BP-4A Stage-1 construct and Stage-2 audit/reconcile
material needed for ingress to validate the candidate against a **freshly composed**
Decision Packet and the repository-owned `brain-high-value-v2` profile.

Ingress must:

1. reconstruct current Brain Sync / Work Context / Flow Resolution for the exact
   operation;
2. compile the fresh Decision Packet using existing BP-3/BP-4 functions;
3. validate Stage 1 and Stage 2 using existing BP-4A validators;
4. require Stage-2 outcome `CANDIDATE`;
5. require exact normalized handoff-candidate equality with the supplied canonical
   payload;
6. preserve the operation's existing CAS/currentness/lineage checks;
7. fail closed on stale packet, candidate substitution, profile mismatch, missing
   stage, closure blocker or wrong selected flow.

No new semantic validator is created; the existing TASK/REMEDIATION/REPAIR validator
still validates the final family payload.

### 5.3 Bootstrap and replay

H1 itself is authored under the pre-H1 ingress contract after a completed manual
`brain-high-value-v2` two-stage audit. Enforcement becomes prospective only after
the exact reviewed H1 source is published.

An identical idempotent replay of an already-canonical artifact may remain a
non-mutating replay path. Any new task identity/revision or correction authorization
mutation requires the audited handoff.

## 6. H2 — Pre-Brain REPAIR strategy facts and correction-lineage closure

### 6.1 Action-neutral projection

Add one read-only, action-neutral REPAIR decision-preflight projection before Brain
chooses a correction strategy. It observes exact canonical FAILURE/TASK/candidate and
pre-verification state without creating a RUN, choosing an action or acquiring
mutation authority.

Minimum deterministic facts:

```text
reusable_preverification_state: PRESENT | ABSENT | INVALID
structural_result_package:      PRESENT | MISSING | INVALID
candidate_clean_committed:      true | false
candidate_transportable:        true | false
candidate_repairable:           true | false
candidate_descends_from_base:   true | false
outside_task_scope:             [] | bounded paths
acceptance_coverage:            COMPLETE | INCOMPLETE | INVALID
action_structural_eligibility:
  NO_CHANGE:               ELIGIBLE | INELIGIBLE
  FINALIZE_CANDIDATE:      ELIGIBLE | INELIGIBLE
  CONTINUE_IMPLEMENTATION: ELIGIBLE | INELIGIBLE
  CODE_FIX:                ELIGIBLE | INELIGIBLE
```

Eligibility is a structural prerequisite only. It must not rank, recommend or select
an action.

`candidate_mutation_required: YES|NO` is deliberately **not** projected as a
Runtime fact because whether more semantic work is required belongs to Brain.
Deterministic support may expose committed-delta/cleanliness facts; Brain decides
what they mean for correction strategy.

The resulting bounded facts are projected into the REPAIR_AUTHORING Decision Packet
before Stage 1, so Stage 1 and Stage 2 reason over the same exact eligibility state.

### 6.2 Existing downstream lineage gap

The same hardening track must close the proven successful-REPAIR -> DELTA
CHANGES_REQUIRED -> REMEDIATION source-lineage gap. Canonical lineage parsers must
recognize the reviewed REPAIR wrapper as a valid exact successful source without
collapsing REPAIR into PRIMARY/REMEDIATION or fabricating a new lifecycle family.

This is a compatibility correction, not a generic correction router.

## 7. H3 — Acceptance proof-phase authoring contract

Preserve the frozen TASK schema while making proof ownership explicit before
canonical authoring.

For TASK_AUTHORING only, the transient audited handoff carries one bounded
`acceptance_phase_ledger` with exactly one entry per final candidate acceptance id:

```text
CLAIM_NOW
PROOF_LATER
```

Semantic meaning:

- `CLAIM_NOW`: the Executor can truthfully make the acceptance claim from the
  candidate/implementation state before Runtime verification.
- `PROOF_LATER`: truth depends on Runtime-owned verification or other post-Executor
  evidence.

Stage 2 must reconcile every `PROOF_LATER` condition out of final canonical
`acceptance` before a TASK candidate can be handed to ingress. The requirement is
moved to the existing verification/constraint surface as appropriate. Therefore the
final handoff ledger for a valid frozen TASK contains every final acceptance id as
`CLAIM_NOW`.

Ingress checks exact ledger coverage and rejects any final TASK handoff containing a
`PROOF_LATER` acceptance entry.

This does not make Runtime interpret natural-language acceptance criteria. The Brain
performs the semantic classification during the already-required two-stage audit;
ingress only enforces complete declared coverage and the no-PROOF_LATER invariant.

## 8. H4 — Brain attention and zero-Work Human wake relay

The proven ChatGPT Work wake path remains historical conformance evidence only.
Production use of Work for AIOS wake/ACK/semantic continuation is rejected by current
Human priority because Work shares the same included allowance as Codex and therefore
can consume scarce Executor capacity merely to observe or acknowledge lifecycle
events.

H4 instead uses a deterministic **Human Wake Relay**. The objective is not zero model
usage; it is zero Work invocation and therefore zero additional draw on the shared
Work/Codex allowance for wake transport itself.

### 8.1 H4A — Deterministic Human Attention Surface

AIOS/GitHub must tell the Human **that a semantic checkpoint exists** without telling
the Brain what semantic decision to make.

The attention projection may carry only bounded transport/selector material such as:

```text
event_id
repository
attention_family
canonical selector(s)
fresh_brain_sync_required: true
```

It must not carry an authoritative `next_action`, correction strategy, Reviewer
verdict, roadmap successor, publication claim, copied semantic payload or model
instruction.

Reuse the existing central attention projection/deduplication machinery where
possible, but deliver the final production signal to a Human-facing GitHub surface
that can use native GitHub notifications without invoking ChatGPT Work. The surface
is presentation/attention only and explicitly non-canonical. Existing TASK/RUN/
RESULT/FAILURE/REVIEW/REMEDIATION/REPAIR/publication artifacts remain engineering
truth.

Each unresolved attention event must have one deterministic event identity and at
most one active Human notification projection. Duplicate source delivery must be a
NOOP at the Human-notification layer. Resolution is determined from canonical
successor state, never merely from notification delivery or acknowledgement.

The prior PR #1200 Work wake markers and ACKs remain immutable historical proof that
GitHub -> ChatGPT Work transport functioned. They are not a production dependency.
No active Work webhook task is required by H4A.

### 8.2 H4B — Regular Chat Brain resume contract

After a Human receives an AIOS attention notification, the Human may open any ChatGPT
conversation inside the AIOS-renew Project and provide a minimal continuation intent
such as `tiếp tục` or `kiểm tra`. The mechanism must not depend on reopening the
same historical chat.

That regular Chat invocation must:

1. perform fresh Brain Sync of canonical `main` and exact current lineage;
2. treat the Human notification only as an untrusted pointer/selector;
3. verify that the attention subject is still unresolved;
4. resolve the current flow through existing Unified State / Flow Resolver contracts;
5. occupy only the authority selected for that semantic step;
6. use existing Brain or Reviewer protocols and canonical ingress surfaces;
7. stop or request Human authority when new intent, priority or risk acceptance is
   required.

Regular Chat usage is intentionally separated from the Work/Codex shared allowance;
the Human chooses when to spend Chat reasoning by explicitly resuming the project.
The relay therefore trades zero-touch continuation for bounded Human involvement: one
notification plus one minimal Chat action at each semantic checkpoint.

Scheduled Chat polling is not the default H4 mechanism because it invokes a model on
a timer, adds latency and spends usage even when nothing changed. A dedicated OpenAI
API Brain is also not the default because it introduces usage-based API cost and
changes the current product/cost assumptions. Either alternative requires a separate
Human decision and audit before adoption.

Human progress remains observable on the GitHub attention/progress surface. Regular
Chat may add a bounded presentation receipt after fresh sync or semantic action, but
that receipt is never lifecycle truth and must not become a second state database.

## 9. H5 — Integration/conformance closure

Before the hardening track closes, minimum conformance must cover:

1. new unaudited TASK mutation is rejected;
2. valid two-stage audited TASK candidate is accepted;
3. Stage-2 candidate/payload substitution is rejected;
4. stale Stage-2 handoff after relevant canonical movement is rejected;
5. REMEDIATION and REPAIR audited authoring use the same gate without merging
   authorities;
6. absent/invalid reusable pre-verification state is visible before REPAIR strategy
   selection;
7. NO_CHANGE structural ineligibility is reported before wakeup/Runtime execution;
8. FINALIZE_CANDIDATE structural eligibility remains distinct from NO_CHANGE reuse;
9. TASK candidate with PROOF_LATER acceptance cannot cross authoring ingress;
10. successful REPAIR -> DELTA CHANGES_REQUIRED -> REMEDIATION lineage is resolvable;
11. terminal/semantic attention produces one deduplicated Human-facing notification
    without requiring any ChatGPT Work invocation;
12. a fresh regular Chat Brain can resume from that notification using only canonical
    state and exact selectors, independent of historical chat memory;
13. no test requires provider identity, hidden machine-local state or Work as a
    production wake dependency.

Closure evidence must remain minimum-sufficient. No ceremonial full-suite rerun is
required when focused evidence subsumes the changed boundary; a canonical full suite
is used only when the implementation invalidates broader evidence.

## 10. Sequencing

Implementation should remain small and reviewable:

1. **H1** audited authoring gate.
2. **H2A** action-neutral REPAIR strategy fact projection.
3. **H2B** successful-REPAIR -> REMEDIATION lineage compatibility correction.
4. **H3** acceptance proof-phase audited handoff.
5. **H4A** deterministic Human Attention Surface with zero Work invocation.
6. **H4B** regular Chat Brain resume contract from fresh canonical state.
7. **H5** integration/conformance closure.

A milestone may require more than one TASK if review uncovers a bounded defect.
Do not merge these into one mega-TASK.

## 11. Two-stage architecture audit reconciliation

The mandatory `brain-high-value-v2` audit found and reconciled the material risks
before this baseline was canonicalized:

- **Frozen TASK schema risk:** rejected a TASK-schema extension; H3 uses a transient
  audited ledger and preserves the frozen schema.
- **Duplicate semantic authority risk:** rejected Runtime inference of
  `candidate_mutation_required`; H2 exposes only structural facts/admissibility.
- **Provider coupling risk:** the authoring gate binds BP-4A semantic stages and a
  fresh Decision Packet, not provider/model/session identity.
- **Persistent reasoning-store risk:** audited handoff is transient and discarded
  after ingress validation; canonical artifact families remain the only engineering
  lineage.
- **Staleness/substitution risk:** ingress reconstructs fresh canonical context and
  requires exact Stage-2 CANDIDATE/payload identity plus existing CAS checks.
- **Lifecycle-router risk:** attention signals require fresh Brain Sync and never
  choose next action.
- **Bootstrap risk:** H1 is the one prospectively grandfathered implementation task;
  enforcement starts only after its reviewed publication.
- **Downstream divergence risk:** Python Agent remains pinned until upstream hardening
  and VPRC close; no local workaround or silent pin change is authorized.

Closure outcome: **CLEAR / CANDIDATE** for this architecture baseline.

## 12. Downstream and VPRC handoff

After H5 closes, roadmap returns to `verification-performance-residual-cost-v3`.
Only after VPRC closes does Human/Brain perform a fresh Python Agent Brain Sync and
authorize one exact downstream adoption of the final reviewed/source-published
AIOS-renew generation, including Research Assurance and this hardening generation.
