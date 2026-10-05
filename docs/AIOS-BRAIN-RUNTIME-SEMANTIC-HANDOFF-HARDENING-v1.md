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

After H4A passes, one real unresolved semantic checkpoint may wake the bound
conversation. The regular Chat Brain must treat the wake turn only as a doorbell and:

1. perform fresh Brain Sync of canonical `main` and exact current lineage;
2. verify the attention subject is still unresolved;
3. resolve the current Unified State / Flow Card;
4. occupy only the selected semantic authority;
5. use existing Brain or Reviewer protocols and canonical ingress surfaces;
6. take at most one semantic continuation step for that wake;
7. stop at any new Human intent, priority or risk-acceptance boundary.

H4B uses `MINIMUM_FRESH_BRAIN_SYNC_V1`. Freshness applies to the identity and
lifecycle facts that can invalidate the next semantic decision; it does not require
ceremonially rereading the entire repository, roadmap history or every governance
document on every wake.

Every wake must freshly establish from canonical state, using the wake payload only as
an untrusted selector:

- current canonical `main` identity;
- the exact wake/attention subject and its exact current lineage;
- selected TASK identity/revision and whether the subject remains unresolved;
- current Unified State, `next_action`, selected authority and Flow Card.

Only after that minimum reconstruction succeeds may the Brain hydrate additional
canonical material required by the selected flow. Examples include exact TASK +
RESULT/EVIDENCE and applicable prior review lineage for semantic review; TASK +
FAILURE/failed RUN plus current H2 strategy facts for REPAIR authoring; exact source
REVIEW/finding/provenance for REMEDIATION authoring; and the current roadmap item plus
the relevant architecture contract for TASK authoring.

Unchanged governance/specification bodies need not be reread in full when their exact
canonical binding or digest is freshly proven unchanged; a deterministic bounded
projection may be reused until the relevant binding changes. This reuse is never model
memory or an independent state store. If the minimum projection cannot establish one
unambiguous current subject, lifecycle state, authority or flow, reconstruction expands
only as far as needed to resolve that ambiguity and otherwise fails closed.

The minimum sync must not scan unrelated TASK/RUN history, all AIOS refs, the complete
roadmap sequence or unrelated architecture material merely for ceremony. It must also
never let the wake payload, chat history, provider/session identity or cached semantic
judgment substitute for canonical truth.

#### Default Brain Sync roadmap projection (TASK-303 r2)

`observe_brain_sync()` reads the authoritative roadmap YAML and exposes a bounded
`active_item` plus at most two items along explicit `return_to` edges. When no NEXT
exists, one unambiguous `BLOCKED` or `LIVE_EXIT_GATE_PENDING` item can supply the
active planning observation; this does not authorize execution or change the existing
no-NEXT lifecycle boundary. A further return edge remains an exact id with
`return_path_truncated: true`. No successor is inferred from sequence position.

The projection includes current track/status, up to sixteen NEXT identities, exact
active TASK authoring pointers, bounded gate/blocker fields and relevant publication
identity. Text fields are limited to 256 UTF-8 bytes (2048 for an objective); revisions
are bounded positive integers. Duplicate/conflicting NEXT identities, missing or
cyclic return edges, ambiguous gates, stale exact TASK blobs/revisions/authoring
commits, conflicting publication identities and off-main active publications fail
closed. Git observation errors remain observation errors, rather than fabricated
lineage conflicts. Selected TASK loading and Unified State continue to own the
existing lifecycle/action projection.

Historical DONE bodies are absent by default. The YAML is still parsed as one
authority input; default observation performs no ancestry sweep of unrelated DONE
publications and creates no archive, cache, planning authority or persisted history.
Explicit Python callers can request `observe_brain_sync(repo=repo,
include_history=True)` to retrieve the retained DONE bodies and check their declared
publication ancestry. Exact original YAML and immutable Git artifacts remain directly
retrievable through their existing paths and refs. Reviews, repairs, audits and Human
requests hydrate the additional exact historical material their decision needs.

`publication.status` is `RELEVANT_ACTIVE_LINEAGE` only for a proven publication on
the explicit active/return path. Its `item_id` states the relevance basis. The legacy
`last_published_task` key mirrors that pointer's task id; it makes no global recency
claim. Otherwise the pointer is `UNAVAILABLE`, the legacy value is null, and the
checkpoint prints `LAST PUBLISHED: unavailable`. Reordering historical DONE entries
cannot alter this value, including during explicit historical hydration.

#### Cleanup retention and coverage map

No source, test, workflow, package/entrypoint or carrier path is deleted by this
candidate. The positive dependencies below prohibit deletion without a separate
complete proof, regardless of a diagnostic question's historical closure. These are
repository witnesses for Runtime to inspect, not Executor verification EVIDENCE.

| Candidate group | Retention witness |
| --- | --- |
| All ten scoped `*_diagnostic.py` scripts | Each corresponding retained `tests/test_<module>.py` imports the script. |
| Contention, serial, residual and stable-failure diagnostics | Cross-imports also retain `bp_v4_parallel_diagnostic`, `aios_full_suite_contention_diagnostic`, `aios_parallel_git_fixture_push_diagnostic` and `aios_serial_context_diagnostic`. |
| Origin capture probe | `AIOS-H4C0-ORIGIN-CAPTURE-CONFORMANCE-v1.md` specifies its path and MCP invocation contract. |
| Primary/repair Brain and self-hosted wake workflows | `.ai/brain-wake-carriers.yaml` lists their production paths. |
| Issue carrier and nested repair continuation | `aios-issue-carrier.yml` uses both Brain reusable workflows; `aios-brain-repair-wakeup.yml` uses `aios-self-hosted-repair-wakeup.yml`. Primary Brain wake also dispatches the self-hosted primary workflow. |
| Wake/bootstrap production modules and their tests | Retained with their package, workflow and test callers; no production removal or behavior adaptation is attempted. |

Runtime can obtain deterministic dependency observations with `rg -n` over these
exact module/workflow names in `scripts`, `tests`, `.github/workflows`, the three
scoped carrier registries, `pyproject.toml`, `src/aios_renew/__init__.py` and the scoped
wake/origin documents. `test_cleanup_retention_dependency_witnesses` provides
executable positive witnesses for every diagnostic candidate and the retained carrier
chain. Because the deletion set is empty, no caller update or dangling deletion
reference is introduced. Question closure, dynamic dependency uncertainty and queued
roadmap dependence never become grounds for speculative deletion.

The only consolidation is `_write_roadmap` in `tests/test_brain_sync.py`, replacing
identical path creation/YAML-writing setup. Every pre-existing named test and its
success, ambiguity, missing/unauthored selection, lifecycle-conflict, read-only,
detached-main and remote-identity assertions remains. The completed-track test now
asserts publication unavailability. Historical off-main and ancestry-error cases
retain their exact assertions through explicit `include_history=True`; new default
and active-gate cases distinguish unrelated history from decision-relevant lineage.
No wake race, provenance, privacy, permission, freshness or dedupe case is removed.

The retained ingress metadata diagnostic control now calls the existing audited
authoring fixture with explicit legacy return affinity, matching its source fixture.
It still submits the same review, authors the same bounded remediation, and requires
`CANONICALIZED`; production audited ingress and origin-affinity enforcement remain
unchanged.

RUN-303-001 is diagnostic history only. Its displayed failure identities include
provider v3 Stage-2 fixtures, the BP8 Reviewer proof and this ingress control. Pure
retained-caller reconstruction on current code identifies two additional compatibility
conflicts: `reviewer_provider_protocol.py` closes the TASK projection without
`return_affinity`, while canonical `Task` serialization includes it; and
`test_brain_provider_protocol.py` selects the current v3 profile but its shared
`stage2_response` omits `cross_authority_context`, `canonical_shape` and
`terminal_lifecycle`. Those paths are outside TASK-303 `scope.modify`. They remain
present and require a bounded scope correction, rather than an in-scope monkeypatch
or weakened published contract. These observations neither attribute all 69 historical
failures to one cause nor replace fresh Runtime selected-suite verification.

Human observability comes from the Brain response appearing directly in the same
conversation. Conversation/session identity never becomes engineering truth.

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
`NO_DECISION`, with no candidate handoff. Ingress still validates fresh packet
lineage, exact candidate/payload identity and CAS before constructing any authoring
blob, index, commit or ref update.

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
