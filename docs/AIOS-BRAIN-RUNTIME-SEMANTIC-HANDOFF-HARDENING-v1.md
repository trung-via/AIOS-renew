# AIOS Brain–Runtime Semantic Handoff Hardening v1

Status: HUMAN/BRAIN PLANNING BASELINE  
Approved priority: 2026-09-30  
Architecture subject main: `a77cb976ed70f9e066a94abc3a1e0c78db680cd2`  
Audit profile: `brain-high-value-v2`  
Scope: prospective control-plane hardening only; no engineering completion claim

## Navigation classification

Current generic self-host operational navigation: [AIOS Self-Host End-to-End Flow v1](AIOS-SELF-HOST-END-TO-END-FLOW-v1.md).
REPLACE_WITH_POINTER applies to that traversal only.
RETAIN_NORMATIVE: independent authority/protocol, H4 planning/conformance and
H5 exit requirements, sequencing and audit requirements, except claims explicitly
superseded by later published authority. HISTORICAL_ONLY: source-baseline
observations and superseded authoring-gate/transport policy claims. In section 8.1
the original per-repository ownership and cross-event in-flight rule are superseded
claims; exact-event ambiguity, dedupe, isolation and binding safety remain active.
No whole-file historical label retires the still-active H4/H5 planning authority.

## Post-BO-1 AUTHOR_TASK rule and historical supersession

`AUTHOR_TASK` Runtime mutation validates the final TASK contract and
canonical/provenance bindings directly. Supplied `audited_handoff` is rejected,
including an explicit null carrier field. Runtime TASK mutation must not require,
reconstruct, validate, fingerprint or freshness-recheck Decision Packet,
Stage-1/Stage-2 material, audit fingerprints, `acceptance_phase_ledger` or
TASK_AUTHORING support sections. There is one direct-final-contract production
TASK mutation path.

The mandatory Brain two-stage audit remains `CONSTRUCT` followed by
`ADVERSARIAL_AUDIT_AND_RECONCILE`, including semantic acceptance-phase
reconciliation and the applicable profile discipline. Its transient support is
not canonical engineering truth or Runtime mutation authority.

The original H1/H3 AUTHOR_TASK gates in sections 4, 5 and 7 and their historical
sequencing/audit descriptions are **HISTORICAL_ONLY / SUPERSEDED_FOR_AUTHOR_TASK**.
They remain below as the historical design, not a competing production contract.
The TASK-specific ingress statement at the end of section 12 is likewise
superseded. H1 audited-handoff, canonical reconstruction, correction-lineage and
freshness requirements remain normative for `AUTHOR_REMEDIATION` and
`AUTHOR_REPAIR`; BO-1 introduces no correction-authoring simplification. H2,
H4/H5 planning authority and the Brain semantic protocol retain their own scope.

Direct TASK safeguards retain schema and exact identity/revision continuity,
minimum-sufficient verification policy, authored affinity rules, expected-main
currentness, unrelated-delta rejection and expected-old-main compare-and-swap.
Concurrent mutation and all direct validation/provenance conflicts fail closed.
New revision-1 ORIGIN_AFFINE TASKs still require independently admitted origin
proof bound to exact carrier attempt, TASK id, expected main, route/generation
and envelope digest, with HMAC, freshness, replay and same-attempt idempotence.
Legacy/revision separation and historical replay bounds are unchanged.

Historical transition: TASK-313 alone was authorized to use the legacy audited
TASK ingress for the final time to implement BO-1. This document does not claim
Runtime verification, Review PASS, publication, phase closure or roadmap advancement.

## 1. Purpose

This track closes demonstrated continuity gaps between canonical AIOS state, Brain
semantic reasoning, canonical authoring mutation, correction strategy selection and
future Brain attention. It is intentionally upstream-first. Performance work resumes
only after this track closes; Python Agent adopts the final reviewed/published
generation only after both this track and VPRC close.

The target is not more autonomy. The target is fewer places where a fresh Brain must
remember an unstated procedural obligation.

## 2. Evidence basis

The original architecture was grounded in the following source-baseline AIOS-renew
and downstream observations (historical facts, not post-BO-1 ingress requirements):

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

**Superseded for AUTHOR_TASK only by the post-BO-1 rule above.** The original
design below is retained as history for TASK mutation and remains normative for
REMEDIATION/REPAIR mutation.

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
Decision Packet and the repository-owned current `brain-high-value-v3` profile.

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

**Historical Runtime TASK gate, superseded by BO-1.** Brain semantic proof-phase
classification/reconciliation remains mandatory; Runtime no longer consumes or
validates the ledger for AUTHOR_TASK mutation. The original H3 design follows.

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

## 8. H4 — Local Regular Chat wake and Brain resume

H4 now follows the reviewed planning contract in
`docs/AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md`.

The prior ChatGPT Work wake path remains historical conformance evidence only and is
not a production dependency. The interim `HUMAN_WAKE_RELAY_V1` is superseded by
`LOCAL_REGULAR_CHAT_WAKE_V1`: eligible AIOS semantic attention is delivered by a
replaceable local transport to one exact Human-bound regular ChatGPT Project
conversation, where one bounded wake message is submitted without using ChatGPT Work.

The local transport is operational only. It owns no Brain, Reviewer, Runtime,
Publisher, roadmap or lifecycle authority; it must not read assistant output to drive
actions, infer `next_action`, allocate canonical identities or blindly resend an
ambiguous submission.

### 8.1 H4A — Local Regular Chat Wake transport

H4A establishes and live-proves only the delivery boundary:

- one exact operationally bound durable ChatGPT conversation target;
- authenticated regular-Chat validation;
- canonical attention -> bounded local wake signal;
- deterministic event identity and one-message dedupe;
- no overwrite of an existing Human draft;
- no send while the target conversation is generating;
- exact-target recheck immediately before submission;
- ambiguous-send fail-close with no automatic resend;
- zero ChatGPT Work invocation;
- no assistant-response extraction.

The first live probe is ACK-only in semantic effect: one bounded eligible event must
produce exactly one short wake user turn in the exact bound conversation, with no
TASK/RUN/REVIEW/REMEDIATION/REPAIR/publication/roadmap mutation caused by the probe.

#### H4A.4 — Durable deferred recovery and multi-project isolation

Before semantic continuation is enabled for sustained unattended use, safe fail-close
delivery must also become eventual delivery for retry-safe pre-submit conditions.
Durable operational recovery is permitted only outside canonical engineering truth.

H4A.4 requires:

- per-project/repository wake lanes with Human-owned machine-local exact-chat binding,
  queue/state/lock isolation and rejection of duplicate chat/state ownership;
- no global busy condition: generation or draft state in one exact target chat cannot
  block an independently bound project chat;
- durable `PENDING/DEFERRED` handling for proven pre-submit transient conditions with no
  silent attention loss;
- a fresh canonical unresolved/resolved check immediately before every deferred send;
- Human/Brain canonical continuation of the same exact subject to supersede the delayed
  wake as `RESOLVED_NOOP`, while unrelated Human conversation does not consume it;
- one in-flight wake per lane, with later pending events freshly pruned against
  canonical state before delivery;
- binding-generation checks so a Human binding change cannot redirect an in-flight
  attempt silently;
- post-send ambiguity held separately from pre-submit deferral: never auto-resend;
  proof-only reconciliation may inspect only the exact outbound wake user turn or fresh
  canonical resolution;
- safe operational receipt compaction only after canonical resolution makes later
  duplicate source delivery independently classifiable as NOOP.

The operational reconciler may decide only whether an exact attention subject is still
eligible for delivery. It cannot infer `next_action`, review verdict, correction
strategy, roadmap successor, model selection or lifecycle transition.

#### H4A.5 — Complete attention-family coverage

After H4A.4, the local wake boundary must support the complete already-approved Brain
attention matrix rather than only terminal `RESULT|FAILURE` events. Every attention
family carries deterministic identity and exact canonical selectors sufficient for
fresh Brain reconstruction, but never an authoritative semantic action.

Coverage includes ingress/carrier rejection, dispatch rejection, pre-AIOS operational
failure requiring diagnosis, RUN RESULT/FAILURE, review ingress rejection,
CHANGES_REQUIRED/BLOCKED follow-up, REMEDIATION/REPAIR authoring rejection, publication
failure, publication-success planning attention, canonical conflict/staleness and
wake-delivery recovery. Runner-started, Executor/verification in-progress, accepted
dispatch and auto-publication start remain non-wake progress signals.

### 8.2 H4B — Regular Chat Brain resume

Generic wake-to-Brain operational reconstruction is REPLACE_WITH_POINTER to
[the single entrypoint minimum fresh context](AIOS-SELF-HOST-END-TO-END-FLOW-v1.md#1-authority-and-minimum-fresh-context)
and its section 8 RETURN.

RETAIN_NORMATIVE specialized H4B conformance: one real unresolved checkpoint
must produce exact-chat attention followed by fresh canonical main, selected
TASK/revision, exact current unresolved lineage, Unified State/next_action,
selected authority and existing Flow Card under MINIMUM_FRESH_BRAIN_SYNC_V1.
Existing Brain/Reviewer protocols and canonical ingress remain the permitted
handoff boundaries; no persistent context or lifecycle store is introduced.
Hydrate only flow-required canonical context; freshly prove unchanged governance
bindings before reuse. Ambiguity expands bounded reconstruction or fails closed.
Wake payload, chat/provider/session memory and cached judgment cannot supply truth.
The specialized live proof takes at most one semantic step and stops at a new
Human intent, priority or risk boundary. This proof limit is not TASK-308
publication-turn policy. Human observes the response in that same conversation;
conversation identity remains transport configuration, not engineering truth.

## 9. H5 — Integration/conformance closure

Before the hardening track closes, minimum conformance must cover the following.
BO-1 supersedes the original TASK gate requirements in items 1-4 and 9; the
updated requirements below preserve the correction and attention boundaries:

1. a directly valid new or revised final TASK is accepted without audited handoff;
2. AUTHOR_TASK rejects every supplied audited_handoff instead of retaining a dual path;
3. direct malformed-contract, identity/revision, verification-policy, affinity,
   stale-main, unrelated-delta and concurrent-main failures remain closed;
4. independently admitted new-TASK origin provenance retains exact carrier/envelope
   binding, HMAC, freshness, replay/idempotence and legacy/revision separation;
5. REMEDIATION and REPAIR retain audited handoff validation, canonical
   reconstruction, candidate equality and freshness without merging authorities;
6. absent/invalid reusable pre-verification state is visible before REPAIR strategy
   selection;
7. NO_CHANGE structural ineligibility is reported before wakeup/Runtime execution;
8. FINALIZE_CANDIDATE structural eligibility remains distinct from NO_CHANGE reuse;
9. Brain reconciles PROOF_LATER acceptance before final TASK delivery as semantic
   discipline, without a Runtime TASK ledger gate;
10. successful REPAIR -> DELTA CHANGES_REQUIRED -> REMEDIATION lineage is resolvable;
11. one eligible terminal/semantic attention event produces exactly one bounded wake
    user turn in the exact Human-bound regular ChatGPT conversation with zero ChatGPT
    Work invocation;
12. duplicate and stale/resolved events produce no duplicate wake turn;
13. retry-safe pre-submit busy/draft/generation/temporary-unavailable conditions enter
    durable deferred delivery and are retried only after exact target and canonical
    subject revalidation;
14. Human/Brain handling of the same exact subject while a wake is deferred closes the
    delayed wake as `RESOLVED_NOOP`; unrelated Human chat activity leaves it pending;
15. two independently bound project lanes remain isolated: a busy/generating chat in
    one lane does not block or receive another lane's wake, and duplicate chat/state
    bindings are rejected;
16. binding changes are generation-safe and cannot redirect an ambiguous attempt;
17. ambiguous post-send attempts never auto-resend and may close automatically only by
    exact outbound-user-turn proof or fresh canonical resolution;
18. pending/submitted operational state cannot silently evict unresolved events or turn
    into a second engineering-state database;
19. all approved Brain-attention families reach the same wake boundary with exact
    selectors, while ordinary in-progress signals remain non-wake;
20. wrong-chat, logged-out, existing-draft and active-generation conditions preserve
    Human text and fail/defer without duplicate delivery;
21. the local wake transport never reads assistant output to select lifecycle action;
22. the woken regular Chat Brain performs fresh canonical reconstruction before any
    semantic action and can continue using only canonical state plus exact selectors;
23. Human can observe the semantic result in the same bound conversation while
    conversation/session identity remains non-canonical;
24. no semantic or lifecycle decision depends on provider identity, hidden browser
    state or Work as a production wake dependency.

Closure evidence must remain minimum-sufficient. No ceremonial full-suite rerun is
required when focused evidence subsumes the changed boundary; a canonical full suite
is used only when the implementation invalidates broader evidence.

## 10. Sequencing

Implementation should remain small and reviewable:

1. **H1** audited authoring gate.
2. **H2A** action-neutral REPAIR strategy fact projection.
3. **H2B** successful-REPAIR -> REMEDIATION lineage compatibility correction.
4. **H3** acceptance proof-phase audited handoff.
5. **H4A0-H4A3** Local Regular Chat Wake transport and ACK-only exact-chat probe.
6. **H4A4** durable deferred recovery, Human supersession and multi-project isolation.
7. **H4A5** complete Brain-attention-family coverage over the same safe wake boundary.
8. **H4B** regular Chat Brain resume from fresh canonical state.
9. **H5** integration/conformance closure.

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

## 11.1 H4A.4/H4A.5 extension audit — 2026-10-02

A fresh two-stage Human/Brain planning audit was performed after live-design review of
deferred wake failure, concurrent project chats and Human intervention races.

Stage 1 `CONSTRUCT` found material risks: stranded fail-closed attention, single-binding
cross-project coupling, Human-vs-delayed-wake duplication, pre-send TOCTOU, unsafe
post-send retry, binding-change redirection, finite-ledger exhaustion, same-lane wake
flooding and terminal-only attention coverage.

Stage 2 `ADVERSARIAL_AUDIT_AND_RECONCILE` closed those risks with:

- `DURABLE_DEFERRED_WAKE_RECOVERY_V1`;
- `PRE_SEND_FRESHNESS_BARRIER_V1`;
- `AMBIGUOUS_SUBMISSION_PROOF_ONLY_V1`;
- `MULTI_PROJECT_WAKE_ISOLATION_V1`;
- `HUMAN_INTERVENTION_SUPERSESSION_V1`;
- `ATTENTION_FAMILY_COVERAGE_V1`.

The extension deliberately splits implementation into H4A.4 and H4A.5 so recovery,
isolation and supersession are proven before the broader attention-family envelope.
No transport component gains Planner, Brain, Reviewer, Runtime, Publisher or roadmap
authority. Downstream repositories remain inactive until their later reviewed pin and
Human-owned binding.

Closure: **CLEAR / CANDIDATE**.

## 12. Downstream and VPRC handoff

After H5 closes, roadmap returns to `verification-performance-residual-cost-v3`.
Only after VPRC closes does Human/Brain perform a fresh Python Agent Brain Sync and
authorize one exact downstream adoption of the final reviewed/source-published
AIOS-renew generation, including Research Assurance and this hardening generation.


## 12. Prospective Brain Audit v3 conformance (TASK-267)

The current registry selects `brain-high-value-v3` first for new canonical
TASK, REMEDIATION and REPAIR authoring. Historical `brain-high-value-v2` remains
parseable and directly validatable with its original profile digest and Stage-2
grammar; it is not converted to v3. Identical canonical replay remains read-only.
The architecture baseline and historical audit statements above retain their v2
identity. The three new sections apply only to v3 TASK_AUTHORING Stage 2.

Each section is a closed object with exactly `packet_fingerprint`,
`reconciled_candidate_fingerprint`, `status`, `basis`, and `entries`. The digests
bind the exact supplied Decision Packet and the final normalized reconciled
candidate, including after a risk reconciliation. Stage-2 identity includes all
three sections. The combined sections are limited to 65536 UTF-8 JSON bytes,
each list to 16 entries, and each text field to 2048 UTF-8 bytes. Existing depth,
Stage-2 size, strict JSON and privacy limits still apply. All text is non-empty.

### 12.1 Caller-owned cross-authority context

`cross_authority_context` has status `CLEAR`, `NOT_APPLICABLE`, or `BLOCKED`.
Entries contain exactly `id`, `status`, `basis`, `candidate_anchor`,
`caller_authority`, `consumer_authority`, `context_role`, and `propagation`.
Entry status is `COVERED`, `NOT_APPLICABLE`, or `BLOCKED`; ids are unique.
Brain identifies the relevant caller-owned authority inputs and call edges,
including shared helper edges, and describes how the explicit input survives
instead of being rediscovered from ambient state. Publisher remote selection is
the demonstrated example; no actual remote URL or secret belongs in this matrix.

`CLEAR` requires at least one explicit `COVERED` entry. Non-applicability or a
section-level blockage may instead be declared with no entries and a bounded
basis. `NOT_APPLICABLE` permits only non-applicable entries. A blocked entry
requires section `BLOCKED`, never a bare clear assertion.

### 12.2 Canonical lineage/state shapes

`canonical_shape` has the same section and entry statuses. Entries contain
exactly `id`, `status`, `basis`, `candidate_anchor`, and `shape`. The exact ordered
coverage is `PRIMARY_RESULT`, `FAILURE`, `REVIEWED_RESULT`, `REMEDIATION_RESULT`,
`REPAIR_RESULT`. Each describes the valid lineage/state shape considered or gives
explicit non-applicability/blockage with basis. In particular, FAILURE without a
RESULT and RESULT with an existing REVIEW must both appear; one observed shape
cannot stand for the family. `CLEAR` requires a covered entry; a non-applicable
section requires every fixed entry to be non-applicable. This is a bounded Brain
consideration surface, not another lifecycle reducer or an exhaustive state engine.

### 12.3 Terminal lifecycle feasibility

`terminal_lifecycle` has status `CLEAR` or `BLOCKED`. Entries contain exactly `id`,
`status`, `basis`, and `candidate_anchor`. Their exact ordered ids are:

1. `PRIMARY_PASS_TO_PUBLICATION`: PRIMARY completion through Reviewer PASS to
   Publisher publication.
2. `CHANGES_REQUIRED_REMEDIATION_PASS_TO_PUBLICATION`: CHANGES_REQUIRED through
   REMEDIATION, subsequent PASS and publication.
3. `FAILURE_REPAIR_PASS_TO_PUBLICATION`: FAILURE through REPAIR, subsequent PASS
   and publication.

Each entry is `FEASIBLE` or `BLOCKED`. These normal required paths cannot be
omitted, substituted or declared non-applicable. Brain assesses current canonical
control-plane capabilities and known blockers, including whether a proposed
prerequisite can finish through normal correction and publication. No future RUN,
EVIDENCE, REVIEW or successful publication is a pre-authoring requirement.

Any blocked entry requires a blocked section. Any blocked section requires a
Stage-2 closure `BLOCKER` on its corresponding existing lens: `AUTHORITY_BOUNDARY`
for context, `FAILURE_MODE_COUNTEREXAMPLES` for shape, or
`AC_CONSISTENCY_COMPLETENESS` for terminal feasibility. Closure must be
`NO_DECISION`, with no candidate handoff. This remains Brain semantic discipline.
The original TASK ingress requirement to validate fresh packet lineage and exact
audit-candidate/payload identity is **SUPERSEDED_FOR_AUTHOR_TASK** by BO-1. Runtime
validates the final TASK and canonical/provenance bindings directly before mutation
and preserves CAS; REMEDIATION/REPAIR retain their audited freshness and exact
candidate/payload validation.

### 12.4 Authority and privacy

The return contract tells Brain how to supply these transient audited sections.
They never enter the frozen TASK candidate or canonical TASK bytes, Runtime
EVIDENCE, REVIEW artifacts, roadmap lifecycle truth, provider memory, or a
persistent reasoning store. Reserved section names are rejected even when nested
inside candidate material. The existing privacy restrictions cover section
entries and bases as well as candidates and audit ledgers.

Deterministic support validates only profile identity, bounded closed fields,
exact coverage, unique ids, packet/candidate lineage and status/closure
consistency. Brain alone decides which edges and shapes apply and whether paths
are feasible. No deterministic code infers correction strategy, semantic verdict,
publication eligibility, roadmap successor or Human risk acceptance. Runtime
verification, Reviewer verdict and Publisher publication remain with their
existing owners. No Planner, lifecycle router or automatic selector is added.
