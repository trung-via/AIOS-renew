# H4B Regular Chat Brain Resume conformance v1

TASK-285 establishes an offline composition contract and a bounded live procedure.
TASK-286 adds bounded pre-submit structural diagnosis to the existing local wake.
TASK-287 adds exact-turn-bound content-free completion for a proven submitted stale flight.
TASK-289 adds bounded terminal FAILURE retirement on a newer canonical revision of the same TASK.
TASK-290 separates per-event canonical uncertainty from durable flight and pass-local safety blockers.
TASK-291 applies explicit Human risk-accepted TEMPORARY_PERMISSIVE_WAKE_V1 transport eligibility.
TASK-294 applies explicit Human wake-first risk acceptance through
TEMPORARY_WAKE_FIRST_CUTOVER_V2, prospectively retiring cross-event lane-flight eligibility.
None of these deterministic acceptances reports a completed regular-Chat semantic resume
or closes H4B. Runtime owns canonical verification and EVIDENCE; Reviewer owns
semantic verdict; Publisher owns reviewed publication; Human/Brain owns the live
observation, residual-risk
disposition and roadmap planning. Executor implementation performs no live wake,
browser operation, binding change, review submission or downstream activation.

## Existing contract and authority

The governing sources are the [Constitution](AIOS-CONSTITUTION.md),
[ChatGPT Project Contract](CHATGPT_PROJECT_CONTRACT.md),
[semantic handoff hardening baseline](AIOS-BRAIN-RUNTIME-SEMANTIC-HANDOFF-HARDENING-v1.md),
[local regular-Chat wake contract](AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md),
[H4A5 conformance and closure disposition](AIOS-H4A5-COMPLETE-ATTENTION-COVERAGE-CONFORMANCE-v1.md)
and the current [canonical planning bookmark](../.ai/roadmap-state.yaml).
H4A5 is closed with Human-accepted residual live-evidence gaps; its strict live
matrix remains INCOMPLETE. Earlier historical statements that H4A5 blocked H4B
must be read with that closure disposition and the subsequent Human H4B start
authorization. Neither planning decision supplies engineering lifecycle truth.

Reuse this existing path without adding a router or reducer:

```text
fresh canonical repository/main + exact selected TASK/RUN/RESULT lineage
  -> brain_sync.observe_brain_sync
       -> unified_state.observe_unified_state
  -> brain_context.compose_brain_work_context
  -> brain_context.resolve_flow
  -> observed repository's .ai/flow-cards.yaml
```

The wake is an untrusted selector-only doorbell. Its event family, event_id,
free-form instructions and alleged verdict do not select semantic flow, authority,
next_action or lifecycle action. Parse selectors only to locate and cross-check
canonical evidence. An event naming RESULT is insufficient to establish an
unresolved review obligation. A missing RESULT can leave EXECUTE_PRIMARY as the
canonical next_action without giving the wake or Brain execution authority.

For a unique current unreviewed RESULT, Unified State establishes SEMANTIC_REVIEW
and REVIEWER. The repository-owned SEMANTIC_REVIEW Flow Card specifies
`review.validate_review`, `REVIEW_CONTRACT_PROPOSAL` and `AUTHORING_INGRESS`.
Brain and Reviewer can occupy the same conversation, but their authorities remain
separate. Brain coordination, a wake receipt or co-location cannot issue a Reviewer
verdict, invoke an Executor, run verification or publish a candidate.

Arbitrary Human/chat text remains semantic input rather than canonical state.
An explicit current Human intent or priority change is a new planning boundary.
The existing resolver also accepts deliberate structured Human side-flow selectors
such as DIAGNOSTIC; this capability is preserved. A wake must never be converted
into such a selector. A side flow retains the pending canonical review and its
Reviewer owner, and requires fresh reconstruction before continuation. Text that
merely contains `flow_selector: DIAGNOSTIC` is not that structured authorization.

## RUN-285-001 observation and TASK-286 structural diagnosis

Historical diagnosis: TASK-291 retires ACCOUNT_NOT_UNIQUE as an active eligibility
cause under the explicit temporary policy below; the other structural causes remain.

TASK-285 r1 engineering acceptance is complete: RUN-285-001 RESULT artifact
`abf4876b3da997be503707e9579d73651a8135b1`, REVIEW-285-001 PASS, and reviewed/published
candidate `61408a376ff4fecab61aeb48493bb52d427acc3d`. Those facts are separate from
H4B live conformance. The unresolved RESULT wake returned **DEFERRED/SURFACE_UNPROVEN**
before composer insertion on the initial attempt and two follow-up rechecks separated
by 15 seconds. The later publication-success wake for that same RUN, through the same
published local transport, returned **SUBMITTED/EXACT_USER_TURN_PROVEN**.

This establishes an opaque transient pre-submit observation gap rather than a
fixed-selector failure. The earlier receipt does not identify which structural
predicate was unsafe; the later successful publication wake does not retrospectively
prove arrival or semantic resume of the unresolved RESULT wake. No selector repair or
timeout increase follows from these observations. The RESULT subject is now canonically
resolved by REVIEW-285-001 and must not be replayed or redrained for H4B proof.

TASK-286 keeps the durable retry reason `SURFACE_UNPROVEN` and its existing `DEFERRED`
classification. Its attempt receipt adds one operational `surface_cause` string,
derived only from the existing exact-target URL, main, account, login, non-regular,
composer and disabled predicates. `check()` and `generation_state()` share the same
structural observation and cause. Generation reconciliation retains its existing
`BUSY` interpretation when the only structural cause is a disabled composer and the
Stop control proves active generation; this does not permit insertion. Without Stop,
the disabled composer holds as `SURFACE_UNPROVEN`. Existing exact-page selection
still fails closed before this observation when the target is unavailable or replaced.

| Closed surface_cause value | Existing unsafe structural predicate |
| --- | --- |
| `TARGET_URL_MISMATCH` | Selected page URL no longer matches the full exact binding |
| `MAIN_NOT_UNIQUE` | Visible main is missing or ambiguous |
| `ACCOUNT_NOT_UNIQUE` | Visible account control is missing or ambiguous |
| `LOGIN_PRESENT` | Existing visible login marker is present |
| `NON_REGULAR_SURFACE` | Existing visible non-regular marker is present |
| `COMPOSER_NOT_UNIQUE` | Visible composer is missing or ambiguous |
| `COMPOSER_DISABLED` | The unique visible composer is aria-disabled |
| `MULTIPLE_OR_AMBIGUOUS` | Simultaneous unsafe predicates or an unspecified/unrecognized cause |

All readable structural predicates are evaluated; simultaneous failures produce
`MULTIPLE_OR_AMBIGUOUS` rather than an arbitrary priority. A non-unique composer is
never queried for a disabled attribute. The diagnostic exports no DOM, user/chat
text, URL, account/session identity, selector text, counts, credentials, CDP endpoint
or local binding path. The cause appears only in the bounded attempt receipt/log;
it is absent from durable event records and doorbell text, canonical TASK/RUN/RESULT/
FAILURE/REVIEW identity, attention event identity and roadmap truth. It cannot select
semantic flow, authority, next_action or lifecycle state, or replace canonical freshness.

[Focused synthetic coverage](../tests/test_local_chat_wake.py) replaces CDP acquisition
only and drives the production pre-submit path for each predicate, missing/ambiguous
matches and simultaneous failures. It asserts no insertion on deferral, shared
generation holds, bounded receipts and unchanged durable subject identity. The same
synthetic page becoming healthy uses the existing durable drain: unknown canonical
freshness holds the subject, fresh barriers precede insertion and click, exact proof
permits one submission, resolved subjects NOOP, and post-submit ambiguity allows only
proof reconciliation without resend. These fixtures make no live browser connection
and manufacture no canonical checkpoint or verification EVIDENCE.

TASK-286 changes only the local wake module, its focused tests and this document.
All existing selectors, draft/generation/outbound protections, exact-target binding
and post-submit ambiguity guards are retained. Budgets remain unchanged: workflow
timeout 5 minutes, canonical observation 30 seconds, CDP attach 10 seconds, page and
insert waits 3 seconds, submission proof 5 seconds, two follow-up rechecks at 15-second
intervals, and the existing optional scheduled drain of four rechecks with the same
15-second interval and five-minute cron cadence. No workflow, browser launcher,
scheduler, second transport, semantic router, lifecycle reducer or assistant-output
scraping changes are introduced.

H4B remains **UNPROVED** pending a future real unresolved canonical semantic checkpoint
and the separate Human/Brain live procedure below. TASK-286 acceptance itself supplies
no live closure, authorizes no replay or fabricated checkpoint, and does not start H5.

## RUN-285 stale-flight recovery, RUN-286 NOOP and TASK-287 completion contract

Historical flight-release policy: TASK-294 below supersedes the requirement to
release an older flight before a distinct event may progress. Historical recovery
facts remain unchanged; the retained structural helper supplies no V2 eligibility
or same-event resend permission.

The later RUN-285-001 publication-success wake was proven SUBMITTED, but subsequent
read-only local proof observed `flight_present=true`, `seen_busy=false`, its exact wake
user turn EXACT and the exact bound browser IDLE. The holder remained SUBMITTED with
reason NONE and binding generation 0; there was no state lock, pending write or queue
file. The existing BUSY-then-IDLE mechanism had missed transient generation. That stale
pointer caused RUN-286-001 RESULT and publication-success attention to defer as
`LANE_IN_FLIGHT`. Submission arrival still supplied no proof of unresolved RESULT resume.

Human authorized a state-only recovery that cleared only the stale flight pointer,
preserving the RUN-285 SUBMITTED event, dedupe identity, all seven event records,
bindings, tombstones and queue state. TASK-286 is complete and published at reviewed
SHA `6955668893636752bd6a7c41b0a0b29ce6adf961`, with REVIEW-286-001 PASS. After its exact
completion was bookmarked, one bounded drain retired the deferred RUN-286-001
publication-success attention as `NOOP/CANONICALLY_RESOLVED`, without a new Chat turn.
These facts establish operational recovery, not H4B live semantic conformance.

TASK-287 permanently addresses that transport liveness shape under the
[exact-turn-bound completion contract](AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md#13-task-287-exact-turn-bound-completion-hardening).
The existing observed BUSY -> seen_busy -> later IDLE path and canonical-resolution
release remain valid. A SUBMITTED holder whose BUSY was never observed may release only
when the adapter proves all of the following against its original binding generation:

1. The exact unique authenticated regular-Chat target passes the existing surface,
   draft and generation gates as IDLE before and after witness inspection.
2. The shared submission resolver proves one exact outbound doorbell user turn. Its
   marker has one visible flat turn container in the unique main.
3. The immediate next element sibling is the final visible assistant turn container,
   with consecutive, unique `conversation-turn-N` ordinals and explicit `data-turn`
   user/assistant roles. There is exactly one visible assistant-role marker, no user
   bubble, nested container, mixed role, later sibling or intervening user turn.

The in-page witness uses only the existing exact wake-user identity comparison and
structural role/order/containment/visibility metadata. It reads no assistant text,
innerText, innerHTML/content, hidden reasoning, semantic meaning or raw chat history.
It receives no URL, account/session/credential data, CDP endpoint or local path and
returns only a Boolean; existing surrounding surface gates retain target authority.
Completion identity inspection allows at most 256 user-specific candidates and
32 ancestor levels. Missing, virtualized, hidden/aria-hidden, duplicate, nested,
role-ambiguous or unsupported structure, an intervening user, target/surface uncertainty,
Human draft, active generation or adapter error all retain the flight. IDLE alone,
elapsed time, cooldowns and retry counts never release it. No semantic conclusion is
drawn from structural completion.

Only the flight pointer changes on this release. The holder stays SUBMITTED with the
same reason, generation and dedupe identity; canonical engineering state is untouched.
Every later pending subject still requires its own fresh canonical and binding-generation
barriers, exact-target proof, draft/generation protection, insertion acceptance and
immediate pre-click barrier. A finite lane pass may send that second subject at most
once or NOOP it on fresh canonical resolution. An AMBIGUOUS post-submit attempt remains
proof-only and is never automatically resent; the completion witness is unavailable
until exact submission proof has established SUBMITTED.

The focused synthetic coverage models a first exact SUBMITTED wake with seen_busy=false
and a distinct pending subject, executes the production witness with traps on all
assistant/wrapper/page content getters, and models the fail-closed structural and
target/generation counterexamples. It also covers fresh-send barriers after release,
canonical NOOP retirement and proof-only ambiguity. It uses no live browser or
manufactured canonical checkpoint and produces no verification EVIDENCE.

Only the four TASK-287-authorized files change. Durable lane schema, workflow timeout,
canonical/CDP/page/insert/proof waits, follow-up recheck counts/intervals and scheduled
cadence remain as recorded above. No timestamp, cooldown, provider-specific persistent
state, second transport, background loop, launcher, lifecycle reducer or semantic
authority is added. Runtime owns verification; Reviewer and Publisher retain their
ordinary boundaries.

H4B remains **UNPROVED**. Human priority selected TASK-287 as a reliability prerequisite;
its acceptance does not supply live resume proof. RUN-285 and RUN-286 are operationally
recovered and resolved attention must not be replayed/redrained. Closure still requires
a future naturally produced real unresolved canonical RESULT, its eligible wake and the
separate Human/Brain semantic-resume observation below. No execution is created to
manufacture that proof, no roadmap state is advanced, and H5 does not start.

## TASK-289 terminal FAILURE freshness prerequisite

After TASK-288 r2 completed, read-only local observation still found the exact
RUN-288-001 FAILURE for TASK-288 r1 deferred as `LANE_IN_FLIGHT`, with no active flight
or queue file. The canonical planning bookmark prohibits a global drain until this
freshness gap is closed. The [same-TASK revision contract](AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md#14-task-289-terminal-failure-same-task-revision-supersession)
adds only a bounded terminal FAILURE branch after exact failure/RUN/head/TASK
reconstruction and existing REPAIR-lineage validation.

When existing REPAIR does not resolve the subject, one coherent current-main snapshot
must contain a valid canonical TASK document whose identity equals the failed RUN's
task_id and whose revision is strictly higher. That TASK contract alone is the
Human/Brain semantic successor; later RUN, REVIEW or publication facts and roadmap
`next_action` are neither required nor substitutes. Equal revision without REPAIR
remains UNRESOLVED. Missing, malformed, unreadable, wrong-task, lower-revision or
moving main/TASK observations remain UNKNOWN. The same canonical-ref recheck covers
main acquisition and the TASK read. Exact REPAIR and REPAIR-supersession retain their
existing resolution paths; terminal RESULT freshness and unrelated subjects are
unchanged.

Focused synthetic fixtures model stable higher/equal revisions, invalid contracts,
failure-binding counterexamples and movement during TASK acquisition. Drain fixtures
model an old `DEFERRED/LANE_IN_FLIGHT` FAILURE becoming `NOOP/CANONICALLY_RESOLVED`
before browser attachment or Send while independent pending subjects retain their
freshness, binding/browser gates, finite-pass submission limit and no-resend ambiguity.
These fixtures use synthetic refs/blobs and temporary lane state, with no live browser,
canonical checkpoint or verification EVIDENCE.

TASK-289 changes only the local wake module, its focused tests and these two contract
documents. Its structural RESULT reports pre-verification candidate properties with
unresolved empty. Runtime owns the unchanged minimum-sufficient commands documented
in the linked contract and canonical EVIDENCE; Reviewer and Publisher retain their
ordinary authority. H4B remains **UNPROVED**. Executor implementation authorizes no
live drain, wake replay, local operational-state rewrite, roadmap closure, H5 start
or downstream adoption. Any later live drain and semantic-resume observation remain
separate Human/Brain work.

## TASK-290 non-blocking per-event transport prerequisite

Historical TASK-290 policy: TASK-291 below supersedes UNKNOWN suppression and
pass-blocker propagation; TASK-294 supersedes durable-flight serialization.
Exact-event no-resend guarantees remain.

The [NON_BLOCKING_PER_EVENT_WAKE_V1 contract](AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md#15-task-290-non-blocking-per-event-wake)
removes the pass-local pseudo-flight created by an unrelated deferred receipt.
An earlier `DEFERRED/CANONICAL_UNKNOWN` subject retains that classification while
the finite drain independently observes later subjects in stable admission order.
The first healthy canonically UNRESOLVED subject may attempt delivery through the
existing canonical, binding-generation, target/surface, draft/generation, insertion
and immediate pre-click barriers. This eligibility scan supplies no semantic
priority, lifecycle selection or routing authority.

`LANE_IN_FLIGHT` requires current durable flight or ambiguous-attempt state. Other
lane-wide pre-submit safety blockers prevent later sends with the proven blocker
reason when flight is absent. Later UNKNOWN subjects retain CANONICAL_UNKNOWN;
later RESOLVED subjects may become RESOLVED_NOOP without browser attachment for
that subject, even behind a blocker or real flight. Per-event freshness and
permanent dedupe remain intact. The existing pre-click durable AMBIGUOUS record and
flight precede the possible click; a successful or ambiguous actual submission
continues to serialize later unresolved subjects. Each finite pass may attempt
at most one user turn. Ambiguous submission remains proof-only with no blind resend.

Focused synthetic fixtures model an earlier unknown subject with `flight=null`,
one later submitted subject, and a further unresolved subject held by that real
flight. They also model no-browser resolved retirement and compaction dedupe,
draft/generation/target/surface/binding and insertion/send blockers without phantom
flight, and ambiguous no-resend behavior. The existing TASK-289 canonical-race
fixture now permits the later independently eligible subject; a production adapter
fixture preserves the remaining-draft gate after UNKNOWN at the pre-click barrier.
These are candidate implementation assertions using temporary lane state, not live observation or
verification EVIDENCE.

Only the four TASK-290 scope.modify files change. The structural RESULT reports
unresolved empty for completed implementation. Runtime owns the unchanged focused
suite plus `git diff --check`, canonical verification and EVIDENCE; Reviewer owns
semantic verdict and Publisher owns exact reviewed publication. Durable schema,
completion witnesses, timeout/recheck budgets, project isolation and authority
boundaries remain unchanged.

H4B remains **UNPROVED**. This transport-liveness prerequisite supplies no
regular-Chat semantic-resume proof. Human/Brain retains post-publication live-state
observation, separately authorized live drain, the future real unresolved canonical
RESULT observation and roadmap/H4B judgment. Executor performs no live drain,
operational-state rewrite or wake replay and advances neither H4B nor H5.

## TASK-291 temporary permissive transport risk acceptance

Historical V1 serialization and rollback/retightening policy is prospectively
superseded by TASK-294 below. Account-presence retirement, UNKNOWN transport
eligibility, exact-event no-resend and exact destination safety remain in V2.

The [TEMPORARY_PERMISSIVE_WAKE_V1 contract](AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md#16-task-291-temporary_permissive_wake_v1)
records the Human's explicit temporary risk acceptance: stable exact target/main/composer
observations with zero account-selector matches make further profile investigation a
low-value blocker. Account/profile presence is removed from every local wake surface,
insertion/acceptance/click, submission proof, generation and completion gate. No
replacement profile/avatar selector is introduced.

For pending/deferred unattempted doorbells, canonical UNKNOWN is non-suppressing
transport uncertainty and may reach the browser barriers; it remains UNKNOWN and
supplies no canonical UNRESOLVED or lifecycle truth. Exact RESOLVED remains a NOOP.
Non-flight pre-submit failures are per-event; later subjects receive their own
canonical and browser checks. Real durable flight or AMBIGUOUS attempt alone causes
LANE_IN_FLIGHT, including for an unknown later subject, and preserves at most one
actual submission in each finite pass. Held attempts remain proof-only with no blind
resend; compaction still requires exact resolution and preserves permanent dedupe.

Exact target identity/uniqueness/change checks, unique usable composer/main, composer
disabled rejection, login/non-regular rejection, draft and active-generation protection,
exact outbound dedupe, binding/state integrity and capacity, insertion/application
Send acceptance, durable ambiguity before possible click and exact submission proof
remain hard interlocks. Existing exact-turn completion/IDLE surroundings and BUSY/IDLE
release behavior remain; account absence alone cannot hold a proven completed wake.

Human accepts possible stale selector-only arrival when transport cannot establish
canonical freshness, and the loss of the account-control witness. Brain must still
perform fresh canonical Brain Sync before any semantic continuation. This is transport
eligibility only, with no Planner, semantic router, priority selector, lifecycle reducer
or new retry authority. The rollback/retightening boundary is the three eligibility
changes and their focused fixtures/documents under later explicit Human/Brain decision
and the ordinary lifecycle owners. The existing enable gate can pause affected wake
transport after a safety defect; rollback cannot reset dedupe, discard held ambiguity,
authorize replay or rewrite historical lineage. See section 16 for the bounded policy.

The focused fixtures cover zero account success, UNKNOWN eligibility, independent
subject barriers, true-flight/ambiguous serialization and retained hard-interlock
counterexamples. Runtime owns canonical verification/EVIDENCE, Reviewer owns semantic
verdict, Publisher owns exact reviewed publication and Human/Brain owns later live
observation, risk retightening and roadmap choices. Only the four TASK-291 files change;
no live wake/drain, operational-state rewrite or semantic proof is created here.
H4B remains **UNPROVED**. Its separate live conformance procedure and closure boundary
below are unchanged. TASK-291 advances neither H4B nor H5 nor roadmap state.

## TASK-294 temporary wake-first risk acceptance

The [TEMPORARY_WAKE_FIRST_CUTOVER_V2 contract](AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md#17-task-294-temporary_wake_first_cutover_v2)
records the Human's explicit 2026-10-04 wake-first risk acceptance: fix wake before
returning to the H4 roadmap, without another per-boundary hardening cycle. Human
accepts coexistence of an earlier submitted/ambiguous doorbell and a later distinct
attempt. V2 prospectively supersedes V1 eligibility serialization.
`LANE_IN_FLIGHT` is retired as cross-event eligibility; an older event's uncertainty
blocks only resend of that exact event. The supplied RUN-293 repeated stale-flight
deferral is transport-policy context, not a TASK-293 defect or lifecycle truth.
TASK-293 artifacts, verification/review and separately checkpointed publication
integration remain unchanged.

Valid legacy flight pointers are normalized idempotently to null while preserving
event records, permanent tombstones, original binding generations and ambiguous
same-event holds. Malformed state still fails closed; no completion or canonical
resolution is invented. Each distinct pending/deferred event crosses its own fresh
canonical, current-binding and exact browser checks. SUBMITTED, RESOLVED_NOOP and
compacted duplicates never resend. Ambiguous attempts retain proof-only reconciliation
on their original binding generation or exact canonical resolution, with no blind resend.

At most one possible-click boundary may be crossed per finite invocation. Durable
exact-event ambiguity precedes it, with no durable flight. Further send attempts stop
after that boundary, including uncertain click/proof/persistence outcomes. Later
subjects can receive INVOCATION_LIMIT_REACHED for that invocation or canonical
RESOLVED NOOP; a later invocation can independently attempt a different event.
Older completion witnesses are no eligibility prerequisite. Exact target URL/page
uniqueness, authorized repository binding/generation safety, login/non-regular
rejection, draft/generation guards, application Send acceptance, exact outbound
proof and state integrity remain mandatory. Transient browser/UI failures remain
bounded event-local outcomes; they do not propagate to independent events or create
Brain architecture or roadmap blockers. UNKNOWN remains transport uncertainty and
does not replace fresh canonical Brain Sync.

Rollback closes the existing global `AIOS_LOCAL_CHAT_WAKE_ENABLED` gate. It does
not initiate another per-boundary investigation or restore cross-event flight
serialization, clear local history/ambiguity, or authorize wake replay. Later policy
changes retain explicit Human intent and ordinary lifecycle authority.

Focused deterministic fixtures model the RUN-293 stale-flight shape and preserve
exact-event dedupe, canonical NOOP, proof-only recovery, current target/binding
checks, application acceptance/proof and independent transient outcomes. Runtime
owns canonical verification/EVIDENCE; Reviewer owns semantic verdict; Publisher owns
exact reviewed publication. Executor changes only the four permitted TASK-294 files,
commits implementation and performs no live wake, operational-state reset, publication
or roadmap mutation. No generic router/queue or new authority is introduced;
H4C0/H4C1 semantics are unchanged. H4B remains **UNPROVED** and the separate Human/Brain
live observation and closure boundaries below remain in force. V2 acceptance advances
neither H4B, H5 nor roadmap truth.

## Offline conformance surface

[The focused tests](../tests/test_h4b_regular_chat_brain_resume.py) use disposable
local Git repositories with no configured network remote. In-memory acquisition
fixtures represent an already verified, unresolved PRIMARY RESULT with distinct
main/base and candidate commits. Their static terminal bytes are synthetic test
inputs, including the modeled existing verification entries; they execute no
verification command and create no admitted RUN, canonical RESULT/EVIDENCE/REVIEW
artifact or lifecycle ref. They are not live evidence or a review verdict.

Only remote observation acquisition is replaced. The tests compose the production
Brain Sync, Unified State, Brain Work Context and Flow Resolver functions and load
the existing repository-owned Flow Card registry. They assert:

- Exact TASK identity/revision, RUN, candidate and semantic base survive composition;
  the scope projection names the same unreviewed subject, and SEMANTIC_REVIEW retains
  REVIEWER authority and the existing Flow Card contract.
- Genuine doorbells, a wrong FAILURE/RUN selector and forged free-form flow, authority,
  verdict, publication or execution instructions do not override canonical selection.
  Unsupported request fields and semantic/lifecycle selectors are rejected.
- Missing/malformed roadmap, missing or moved TASK revision, competing RESULT tips,
  substituted RUN identity and altered RESULT binding cannot restore a remembered
  semantic subject. Missing TASK may expose blocked TASK_AUTHORING; that does not
  authorize review of a remembered RUN. Missing RESULT selects no semantic flow.
- Relevant main, canonical review, selected RUN and current-request movement changes
  the context fingerprint; combining fresh facts with an old binding and mutating
  lineage/authority in place are rejected. A newly reviewed BLOCKED subject no longer
  supplies an exact SEMANTIC_REVIEW scope.
- The representative minimum read set contains one roadmap, the exact TASK and one
  Flow Card registry, with one selected TASK/revision lifecycle acquisition. Traps
  reject unrelated file reads, repository glob scans and Git history scans. Malformed
  unrelated history does not need to be hydrated to select this review flow.
- Operator-only remote URL and synthetic credentials, raw-log pointers, chat history
  and provider/session metadata do not enter the reasoning projection. Human input
  retains the existing 16384 UTF-8 byte limit. Rendered minimum contexts are bounded.
- Mutation, RUN allocation, execution, verification, network and browser/binding
  entry points are trapped at the observation boundary; Git HEAD, worktree status,
  refs and object counts remain unchanged, with no Runtime state directory created.
  Unrelated, missing or authority-altered Flow Card registries fail closed.

These are implementation properties available for TASK acceptance before Runtime
verification. This document does not claim that Runtime has verified them, or that
deterministic acceptance proves the future regular-Chat live step. No production
Python, workflow, attention schema, transport, binding or Flow Card changes were part
of the TASK-285 composition delta; TASK-286/TASK-287/TASK-289/TASK-290 local wake changes are bounded above.
There is no second semantic router, lifecycle reducer, persistent
reasoning store, assistant-output scraper, ChatGPT Work wake dependency or new
mutation authority.

## Minimum fresh context and invalidation

Before any continuation, freshly establish canonical repository identity and main,
exact selected TASK/revision and RUN/RESULT subject, its unresolved status, Unified
State, next_action, authority and the repository-owned Flow Card. Require agreement
between the selected subject and the exact wake selectors. Use
`unified_state.observe_semantic_review_scope` for the current review subject when
the review needs exact PRIMARY/DELTA origin, base and predecessor bindings; do not
invent them from a RUN number, chat narrative or candidate similarity.

Work Context is transient. Its fingerprint binds repository identity, main,
roadmap/selection, semantic subject/lifecycle, blockers and the bounded current
request. It detects altered facts and distinguishes fresh compositions after
movement. `resolve_flow` does not poll remote state or prove that an old snapshot
is still current. Always acquire a fresh canonical observation before continuing;
an unchanged in-memory fingerprint alone is not a freshness proof. Check the
current repository-owned card again; its loader rejects unrelated overrides and
authority drift. Relevant governance/card binding movement invalidates reuse too.

After the minimum succeeds, hydrate only what that flow needs: exact TASK,
RUN/RESULT/EVIDENCE, relevant candidate delta and applicable prior review lineage
for semantic review. Prove an unchanged canonical governance/specification binding
or digest before reusing its bounded projection. Do not reread entire unchanged
bodies for ceremony, scan all AIOS refs, enumerate unrelated TASK/RUN history or
hydrate the full roadmap narrative. The existing Brain Sync may parse its supplied
roadmap and inspect DONE ancestry for conflicts; the focused fixture does not claim
to remove those existing checks or prove constant cost for every roadmap shape.

Ambiguity may expand reconstruction only to directly relevant canonical material.
If one exact current subject, unresolved status, lineage, authority and card still
cannot be established, stop and report the blocker. Do not use chat memory, provider
identity, browser state, cached semantic judgment, raw logs, credentials or private
conversation content as a fallback. The operational repository root/remote alias
locates reads; the remote URL and machine-local conversation binding are unnecessary
reasoning material. No persistent context cache is introduced.

## Bounded live procedure for Human/Brain observation

This procedure is a separately recorded Human/Brain conformance observation after
Runtime supplies an eligible subject. It authorizes no Executor live probe, new
execution, fabricated checkpoint, binding change, blind redrain or replay. Use the
published local wake path and the already Human-bound regular Chat. If there is no
real eligible unresolved canonical RESULT, record the live case as UNPROVED and
stop. The resolved RUN-285-001 RESULT is ineligible and must not be replayed. A future
naturally produced unresolved canonical checkpoint may be used only while eligible;
its future wake, review and publication are not prerequisites for TASK-286/TASK-287's own
deterministic acceptance.

1. Select at most one real existing unresolved canonical semantic checkpoint for
   this observation. Confirm the exact repository, TASK/revision, RUN, RESULT
   artifact and candidate lineage. Observe its one selector-only wake user turn
   reaching the already-bound regular Chat through the existing transport guards.
   Delivery proof establishes arrival only. Preserve dedupe, draft/generation,
   exact-target and ambiguity/no-resend rules; no assistant-output extraction is
   used to establish delivery or to drive actions.
2. In that same conversation, Brain ignores any payload semantic assertion and
   performs fresh canonical reconstruction through the existing path above.
   Cross-check the selected TASK/RUN/candidate against the exact artifact selectors.
   Confirm the subject remains unreviewed and unresolved now, rather than assuming
   the pre-send transport observation is still current. A resolved, moved, missing,
   malformed, stale or competing subject stops semantic continuation; report the
   fresh fact or blocker without choosing another subject from chat history.
3. Compose the bounded Work Context and resolve the current repository-owned card.
   For this RESULT case require canonical SEMANTIC_REVIEW, REVIEWER and exact
   semantic review scope agreeing with the Brain Sync/Work Context subject. Only
   then hydrate the flow-required evidence. Recheck the minimum canonical binding
   immediately before any semantic continuation or
   authorized handoff. If main, lineage, lifecycle, request, governance or card has
   moved, discard the old proposal/context and stop this observation; any later
   attempt requires fresh reconstruction. Do not loop through successors.
4. If no new Human boundary exists, occupy only Reviewer authority and take at most
   **one semantic continuation step**: evaluate the exact subject and produce one
   bounded REVIEW_CONTRACT_PROPOSAL under the existing review contract. If ordinary
   Reviewer submission authority is already present, its one exact SUBMIT_REVIEW
   handoff must use the existing ingress validation and currentness gates; this
   document grants no additional submission authority. Proposal text alone is not
   a canonical REVIEW. Do not chain into remediation/repair authoring, Executor
   dispatch, verification, publication control or roadmap advancement. Subsequent
   lifecycle work retains its ordinary owners.
5. Stop before or during that step at any new Human intent, priority, scope or risk
   boundary. Surface the exact pending obligation and the decision needed from
   Human/Brain; do not assume risk acceptance from silence, an older conversation
   or H4A5's limited acceptance. Zero semantic steps is correct for a blocker or
   new boundary. Any later continuation starts from fresh canonical facts.
6. Leave the Human-visible response in the **same conversation**: a compact SYNC
   CHECKPOINT, exact sanitized subject bindings, unresolved/resolved observation,
   selected flow and authority, card identity, the one proposal/handoff or stop
   reason, and semantic step count of zero or one. Human observes this response
   directly. Conversation/account/session identity remains machine-local transport
   configuration, never TASK/RUN identity, review evidence or canonical truth.

A bounded observation note may retain canonical repository/main, TASK/revision,
RUN/candidate/artifact selectors, currentness outcome, flow/card/authority, step
count and stop boundary. Do not copy raw chat history, assistant transcripts,
browser URLs, CDP endpoints, credentials, local binding/state files or account/turn
identifiers into canonical records. A Human-observed response is live conformance
observation, not transport scraping and not Runtime EVIDENCE. Record unavailable
or ambiguous observations as UNPROVED rather than inferring a successful resume.

## H4A5 residuals, reopen triggers and closure boundaries

Carry forward the exact Human-accepted H4A5 disposition: accepted for that milestone
only, no engineering-defect waiver, no fabricated lifecycle evidence and no
reclassification of unproved cases as PASS. The strict live matrix stays INCOMPLETE.

| Accepted residual live-evidence gap | Continuing status | Affected reopen trigger |
| --- | --- | --- |
| Five representative attention families without a current exact eligible source | UNPROVED | Transport defect or duplicate Send in an affected family |
| Complete immutable progress-group live zero-wake set unavailable | UNPROVED | A progress signal producing attention |
| Human-draft stale-source variant | UNPROVED | Stale-source misdelivery or duplicate Send |
| Second separately authorized deployed live lane unavailable | UNPROVED | Cross-lane interference or transport defect |
| Human-owned binding-generation rollover/race | UNPROVED | Generation misbinding or duplicate Send |
| Generic recovery-family live case unavailable | UNPROVED | Recovery resend or transport defect |

Any future evidence of a transport defect, duplicate Send, stale-source misdelivery,
cross-lane interference, generation misbinding, recovery resend or progress signal
producing attention reopens the affected H4A5 risk **before relying on it downstream**.
One H4B semantic resume observation does not discharge those six gaps. Missing real
sources or operational setup remain explicit; no new RUN/REVIEW, second lane or
binding mutation is manufactured to fill the matrix.

Runtime verification of TASK-285/TASK-286/TASK-287/TASK-289/TASK-290, Reviewer judgment and Publisher publication each
remain separate lifecycle facts. Deterministic TASK acceptance by itself proves
neither a future regular-Chat semantic resume nor H4B closure. Human/Brain separately
records and evaluates the live observation, reconciles any reopened residual risk
and decides whether to close H4B. H5 start requires its own later Human/Brain
planning action after H4B closure; there is no automatic H5 or downstream advance,
Executor/model/effort selection or roadmap mutation from this conformance delta.
