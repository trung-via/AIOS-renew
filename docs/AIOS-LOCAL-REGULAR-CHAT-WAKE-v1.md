# AIOS Local Regular Chat Wake v1

Status: HUMAN/BRAIN PLANNING CANDIDATE
Approved priority: 2026-10-01
Audit profile: `brain-high-value-v2`
Scope: GitHub/AIOS semantic attention -> exact existing regular ChatGPT Project conversation
Supersedes for production wake: `HUMAN_WAKE_RELAY_V1`
Preserves as historical evidence only: ChatGPT Work wake/ACK conformance

## 1. Human objective

When canonical AIOS state reaches a semantic checkpoint, wake the Human-visible regular
ChatGPT Brain automatically in one exact bound conversation without using ChatGPT Work,
without consuming the Work/Codex shared allowance for wake transport, and without making
the wake transport a Brain, Reviewer, Planner, Runtime, Publisher or lifecycle router.

Target shape:

```text
AIOS/GitHub canonical attention
        |
        v
deterministic bounded wake event
        |
        v
local exact-chat wake transport
        |
        v
bound regular ChatGPT Project conversation
        |
        v
one bounded wake message is submitted
        |
        v
regular Chat Brain performs fresh Brain Sync
        |
        v
existing Flow Resolver / Brain / Reviewer / ingress contracts
```

The Human observes semantic progress in the bound ChatGPT conversation. Canonical
engineering truth remains repository state and exact immutable lineage.

## 2. Evidence basis

Current OpenAI product surfaces do not provide an ordinary Plus Project-chat public API
that accepts an existing `chatgpt.com/c/<conversation-id>` and appends a turn from an
external GitHub event without using Work or a separately billed API/managed-agent
surface.

External implementations demonstrate the technical browser/session pattern but are not
AIOS authority:

- `cobuildwithus/review-gpt`: reopens an exact ChatGPT conversation URL, can auto-send
  a follow-up, validates the selected chat surface, preserves exact thread identity and
  fails closed on uncertain submission.
- `qayshp/chatgpt-playwright-backend`: attaches to an already-authenticated Chrome
  session over CDP, opens/selects an exact conversation, refuses mismatched/busy/draft
  states and warns against automatic resend after uncertain submission.
- `yudduy/chatgpt-pro-web`: uses a persistent browser profile and supports
  `--continue <conversation-url>`.
- `eimexdev/RightClickGPT`: browser-extension content-script pattern for locating the
  ChatGPT composer and submitting a prompt.

These are feasibility evidence only. AIOS does not import their lifecycle semantics and
does not require any one implementation technique.

## 3. Authority boundary

The local wake transport owns only delivery of one bounded Human-visible wake message to
one exact operationally bound regular ChatGPT conversation.

It MUST NOT:

- infer or carry authoritative `next_action`;
- select correction strategy;
- perform semantic review;
- allocate TASK/RUN/REVIEW/REPAIR identities;
- invoke Executor, Runtime verification or Publisher;
- read/parse assistant output to drive lifecycle actions;
- scrape conversation history as engineering truth;
- advance roadmap state;
- retry an ambiguous accepted submission;
- use ChatGPT Work as a fallback.

The regular Chat Brain retains the existing Brain/Reviewer authority selected after
fresh canonical reconstruction. Runtime and Publisher boundaries are unchanged.

## 4. Operational chat binding

The exact target conversation is replaceable transport configuration, not canonical
engineering state.

The binding MUST:

- identify one exact durable `https://chatgpt.com/c/<conversation-id>` target;
- remain outside committed repository state;
- be explicitly established or changed by the Human;
- never be treated as TASK/RUN/roadmap identity or semantic authority;
- fail closed when missing, malformed, logged out, on the wrong ChatGPT surface or
  otherwise unprovable.

A future supported OpenAI conversation-trigger API may replace this browser/session
transport without changing the semantic contract.

## 5. Wake message

The transport-submitted message is a doorbell, not truth. It should be minimal and
bounded, for example:

```text
[AIOS LOCAL CHAT WAKE]
event_id: <deterministic attention event id>
repository: trung-via/AIOS-renew
fresh_brain_sync_required: true
```

It MUST NOT include copied TASK semantics, logs, a review verdict, correction strategy,
roadmap successor, model selection or lifecycle command.

On receipt, the regular Chat Brain must ignore semantic claims from the message, perform
fresh Brain Sync from canonical `main` and exact lineage, verify that the event remains
unresolved, resolve the current flow and take only the authority allowed by current
canonical contracts.

## 6. Delivery and idempotency

Each canonical attention subject has one deterministic event identity.

Before sending, the local transport must establish that:

1. the event is eligible for Brain attention;
2. the exact target conversation is the configured target;
3. the ChatGPT session is authenticated and on regular Chat;
4. there is no existing unsent draft;
5. no response generation is active;
6. the same event has not already been proven submitted.

Successful submission proof is operational only. It may use bounded local delivery
metadata but must not become a second engineering-state database.

Duplicate source delivery is a NOOP. If send acceptance is ambiguous, the adapter stops
and surfaces a Human-visible operational failure; it never blindly resends.

## 7. Concurrency and Human interaction

Manual Human use of the bound conversation outranks automated wake delivery.

If the conversation is busy, contains a draft, is generating, is on another durable
conversation identity or cannot prove its target, the adapter fails closed or defers
through a bounded transport mechanism without semantic interpretation.

The adapter must not overwrite Human text or navigate away from an active unrelated
conversation merely to deliver a wake.

## 8. Privacy and output boundary

The wake transport does not require assistant-response extraction.

Production v1 ends its authority once the exact wake user turn is proven submitted.
The ChatGPT product renders the Brain response directly to the Human in the same
conversation. Any later GitHub mutation is performed through the regular Chat Brain's
authorized connector/ingress flow, not by parsing browser output.

No cookies, credentials or conversation content are committed to the repository.

## 9. H4 sequencing

### H4A.0 — Retire legacy Work Wake Bus workflow

The existing `.github/workflows/aios-brain-wake-bridge.yml` is a legacy delivery
workflow for the superseded PR #1200 -> ChatGPT Work path. It is not part of the
production Local Regular Chat Wake design and must be retired through a reviewed
implementation change. Historical PR #1200 markers, ledgers, bridge evidence and the
deterministic projection/parser code may remain as immutable evidence or reusable
transport logic until an explicit cleanup proves they are no longer needed.

Retirement must not delete historical canonical lineage or conformance evidence and
must not change Runtime, Reviewer, Publisher or current TASK semantics.

### H4A.1 — Remove top-level Issue workflow fanout noise

Current Issue carriers share `issues: opened` and reject unrelated titles inside jobs,
which causes visible skipped workflow runs. H4A should replace that presentation-noise
fanout with one deterministic syntactic entry/demultiplexing boundary, or an equivalent
GitHub-native mechanism if available, so one recognized Issue title activates only the
corresponding carrier path.

This boundary may map exact title markers to existing carrier workflows only. It must
not inspect lifecycle state, select semantic flow, choose correction strategy, infer
NEXT, or become a Planner/router authority. Unknown titles fail closed without
dispatch.

### H4A.2 — Local Regular Chat Wake transport

Implement and prove the bounded transport contract only:

- canonical attention -> local delivery signal;
- exact operational chat binding;
- authenticated regular-Chat target validation;
- one-message dedupe;
- busy/draft/generation protection;
- ambiguous-send fail-close;
- zero ChatGPT Work invocation;
- no assistant-output extraction.

The first live conformance probe is ACK-only in semantic effect: one fresh synthetic or
bounded attention event must cause exactly one short wake message to appear in the exact
bound conversation. The Brain side performs no lifecycle mutation for this probe.

### H4A.3 — ACK-only exact-chat conformance

Before semantic continuation is enabled, prove one bounded eligible event produces
exactly one wake user turn in the exact bound conversation, with duplicate delivery,
busy/draft/generation, wrong-chat and ambiguous-send cases failing closed. No lifecycle
mutation is permitted in this probe.

### H4A.4 — Durable deferred wake recovery and multi-project isolation

After the exact-chat probe passes, harden safe delivery into eventual delivery without
moving semantic authority into transport.

The transport must distinguish retry-safe **pre-submit deferral** from uncertain
**post-submit ambiguity**:

- `GENERATION_ACTIVE`, `DRAFT_PRESENT`, temporarily unavailable target/CDP and other
  proven pre-submit conditions may enter a durable operational `DEFERRED` state and be
  rechecked without consuming Brain authority;
- every deferred attempt must freshly revalidate the exact canonical attention subject
  immediately before insertion/submission; if Human/Brain activity has already produced
  a canonical successor or superseding state for that exact subject, the wake becomes
  `RESOLVED_NOOP`;
- Human chat activity alone never resolves an event. Only canonical state may prove the
  subject resolved, superseded or still actionable;
- once submission may have happened, the event enters an ambiguity hold. It is never
  automatically resent. Recovery may perform **proof-only reconciliation** by looking
  only for the exact outbound wake user turn; exact proof may mark it submitted, while
  absence or uncertainty keeps the hold;
- operational queue/receipt state stays outside repository engineering truth and cannot
  select `next_action`, correction strategy, review verdict, roadmap successor or
  Executor;
- submitted/resolved operational records may be compacted only when fresh canonical
  reconciliation makes later source redelivery safe to classify as NOOP; unresolved
  records are never silently evicted.

Wake delivery becomes lane-scoped. A machine-local Human-owned registry maps each
supported project/repository lane to one exact regular-Chat conversation, local state
and lock. Active mappings must reject duplicate chat identity or state ownership across
independent lanes. Busy state in one exact chat never creates a global `BRAIN_BUSY`
condition for another lane.

A pending pre-submit event follows the current explicitly Human-owned binding generation.
A binding change during preflight aborts that attempt and re-resolves the new generation.
An ambiguous post-submit attempt remains bound to the generation on which it was
attempted until exact proof, canonical resolution or Human reconciliation closes it.

Within one lane, only one wake may be in submission/Brain-generation flight at a time.
Additional events remain pending. After each completion the transport revalidates every
pending subject, discards stale/resolved subjects as NOOP, and submits only the next
still-unresolved event. It must not semantically coalesce unrelated attention subjects.
For a proven submitted flight whose transient BUSY state was missed, the exact-turn-bound
structural completion contract in section 13 supplies an additional fail-closed release
mechanism. IDLE alone never supplies that proof.

This milestone must preserve project independence even when multiple target chats share
one authenticated browser/CDP session. Page-state checks are scoped to the exact target
page; global browser activity is not a substitute for target-chat state.

### H4A.5 — Complete attention-family coverage

After H4A.4 proves durable lane behavior, generalize the local wake intake from the
current terminal `RESULT|FAILURE` grammar to the bounded Brain-attention matrix already
owned by AIOS. The transport still carries selectors only and never a semantic action.

Coverage must include at least:

- ingress/carrier rejection requiring Brain attention;
- PRIMARY/REMEDIATION/REPAIR dispatch rejection;
- pre-AIOS operational failure requiring diagnosis;
- canonical RUN `RESULT` and `FAILURE`;
- review ingress rejection;
- `CHANGES_REQUIRED` or `BLOCKED` review follow-up;
- REMEDIATION/REPAIR authoring rejection;
- publication dispatch/execution failure;
- successful publication when Human/Brain roadmap reconciliation or another semantic
  commitment is required;
- canonical conflict/staleness invalidating a prepared Brain candidate;
- wake-delivery recovery for an unresolved attention event.

Every family requires deterministic event identity, exact immutable/source selectors
sufficient for fresh canonical reconstruction, per-lane dedupe and the same pre-send
freshness barrier. Ordinary progress signals such as runner started, Executor in
progress, verification in progress, dispatch accepted and auto-publication start remain
non-wake events.

H4A.5 adds coverage, not routing authority. A generic attention envelope may identify
the attention family and canonical subject but cannot encode an authoritative next
action, Reviewer verdict, correction strategy, roadmap successor or model selection.

### H4B — Regular Chat Brain resume

After H4A transport conformance passes, prove that one real unresolved canonical semantic
checkpoint can wake the bound conversation and that the regular Chat Brain:

1. performs fresh Brain Sync;
2. reconstructs exact canonical lineage;
3. resolves the current Flow Card;
4. occupies only the selected authority;
5. uses the existing Brain/Reviewer protocol and canonical ingress;
6. takes at most one semantic continuation step for that wake;
7. stops at a Human intent/priority/risk boundary;
8. leaves a Human-visible result in the same conversation.

No ChatGPT Work task is part of this path.

## 10. H5 exit gates added by this revision

Hardening integration cannot close until live evidence proves:

- Work wake automations remain disabled for production AIOS wake;
- one eligible canonical attention event produces exactly one message in the exact bound
  regular ChatGPT conversation;
- duplicate delivery does not duplicate the wake turn;
- stale/resolved events do not produce a new wake;
- retry-safe pre-submit blockage is durably deferred and later delivered when the exact
  lane becomes eligible, without silently dropping the attention event;
- Human/Brain canonical continuation while a wake is deferred causes pre-send
  reconciliation to close that exact event as `RESOLVED_NOOP`, while unrelated Human
  chat activity leaves it pending;
- two independently bound project lanes can progress concurrently: busy/generation in
  one target chat does not block an idle target chat in another lane;
- same-chat or same-state multi-project binding is rejected before delivery;
- wrong chat, logged-out state, existing draft and active generation fail closed;
- uncertain post-submit state never auto-resends and may only close automatically by
  exact outbound proof or fresh canonical resolution;
- binding changes are generation-safe across pending and ambiguous attempts;
- unresolved queue entries are never silently evicted and operational compaction cannot
  weaken duplicate suppression;
- the complete audited attention-family matrix reaches the same local wake boundary,
  while ordinary in-progress signals remain non-wake;
- the transport never reads assistant output to choose lifecycle action;
- the woken regular Chat performs fresh canonical reconstruction before semantic action;
- Human can observe the semantic result in that exact conversation;
- canonical state and roadmap remain independent of conversation/session identity.

## 11. Two-stage architecture audit

Stage 1 — CONSTRUCT: **RISK_FOUND**.

Material risks:
- unstable/private browser UI surface;
- exact-conversation substitution;
- duplicate and ambiguous submission;
- concurrent Human/chat generation interference;
- authentication/profile drift;
- accidental Work-surface selection;
- transport becoming a lifecycle agent;
- conversation identity becoming engineering truth.

Stage 2 — ADVERSARIAL_AUDIT_AND_RECONCILE: each material risk is bounded by exact target
binding, regular-Chat validation, one-message authority, deterministic event identity,
busy/draft/generation gates, no blind resend, no assistant-output extraction, local-only
operational binding and mandatory fresh Brain Sync after wake.

Closure: **CLEAR**.
Outcome: **CANDIDATE**.

This candidate changes only the planned wake transport. It does not claim implementation
or conformance, does not alter current TASK lineage and does not advance the active
roadmap milestone automatically.
## 12. H4A.4/H4A.5 two-stage extension audit — 2026-10-02

Stage 1 — CONSTRUCT: **RISK_FOUND**.

The first-pass candidate added durable deferred delivery, per-project lanes and Human
supersession. The construct audit identified these material failure modes in the
published H4A.2/H4A.3 shape:

- transient pre-submit fail-close can strand an otherwise valid canonical attention
  event indefinitely because no retry/reconciliation loop exists;
- a single repository/config/state binding is insufficient for independent concurrent
  project chats;
- Human can manually continue the exact subject while its automatic wake is deferred,
  creating a duplicate-delivery race unless canonical state is rechecked before send;
- an unresolved-to-resolved transition can occur after an earlier queue check but before
  browser insertion/click (pre-send TOCTOU);
- post-send uncertainty cannot safely share the same retry behavior as pre-submit
  blockage;
- changing a Human-owned chat binding while an event is pending can redirect an attempt
  unless the attempt is bound to a checked binding generation;
- fixed operational event capacity can become a long-running availability failure if
  unresolved records are silently evicted or all historical receipts are retained
  forever;
- multiple pending events in one chat can flood the composer or become stale while an
  earlier Brain turn is still generating;
- terminal-only `RESULT|FAILURE` intake cannot close the already-approved full Brain
  attention matrix;
- a shared global lock/busy interpretation can incorrectly serialize independent
  project lanes.

Stage 2 — ADVERSARIAL_AUDIT_AND_RECONCILE: **CLEAR**.

The candidate is reconciled by five authority-preserving contracts:

1. `DURABLE_DEFERRED_WAKE_RECOVERY_V1` — retry only proven pre-submit transient
   blockage; retain unresolved events; use condition-based rechecks and bounded
   operational escalation without semantic interpretation.
2. `PRE_SEND_FRESHNESS_BARRIER_V1` — freshly revalidate the exact canonical attention
   subject immediately before every deferred submission. Canonical Human/Brain progress
   resolves/supersedes the event; mere chat activity does not.
3. `AMBIGUOUS_SUBMISSION_PROOF_ONLY_V1` — once send acceptance is uncertain, never
   auto-resend. Reconciliation may prove the exact outbound user turn or observe
   canonical resolution, otherwise the event remains held for Human attention.
4. `MULTI_PROJECT_WAKE_ISOLATION_V1` — Human-owned machine-local unique lane bindings,
   per-lane queue/state/lock and exact-page checks prevent one busy project/chat from
   blocking or receiving another project's wake.
5. `ATTENTION_FAMILY_COVERAGE_V1` — a later bounded H4A.5 generalizes selectors across
   the approved attention matrix without turning transport into a lifecycle router.

Adversarial reconciliation additionally requires safe binding-generation changes,
one in-flight wake per lane, pending-event canonical pruning after each Brain turn,
safe compaction only after canonical resolution, and no automatic downstream project
activation. Python Agent or another repository adopts the capability only through its
own later reviewed downstream pin/binding.

Closure: **CLEAR**.
Outcome: **CANDIDATE** for roadmap insertion as H4A.4 then H4A.5 before H4B.

## 13. TASK-287 exact-turn-bound completion hardening

### Observed stale flight and bounded recovery

RUN-285-001 publication-success attention was proven
`SUBMITTED/EXACT_USER_TURN_PROVEN`. Later read-only local inspection found its holder
record still `SUBMITTED`, reason `NONE`, binding generation 0, with a flight pointer
whose `seen_busy` was false. The exact submitted wake user turn was still EXACT and
the exact bound browser was IDLE. There was no state lock, pending write or queue file.
The missed transient BUSY left the flight held; RUN-286-001 RESULT and publication-success
attention then deferred as `LANE_IN_FLIGHT`. This is a transport liveness failure, not
proof of semantic continuation or canonical resolution.

Human authorized state-only recovery: clear only the stale flight pointer and preserve
the RUN-285 SUBMITTED event, dedupe identity, all seven event records, bindings,
tombstones and queue state. TASK-286 completed with REVIEW-286-001 PASS and reviewed/
published SHA `6955668893636752bd6a7c41b0a0b29ce6adf961`. After that exact completion was
bookmarked, one bounded drain classified the deferred RUN-286-001 publication-success
attention as `NOOP/CANONICALLY_RESOLVED` without sending a new Chat turn. The incident
is operationally recovered; neither resolved subject may be replayed or redrained to
manufacture H4B evidence.

### Content-free completion contract

TASK-287 retains the existing observed BUSY -> `seen_busy=true` -> later IDLE release
and fresh canonical-resolution release mechanisms. For a SUBMITTED holder with
`seen_busy=false`, release is additionally permitted only when all of these hold:

- The adapter uses the holder's original binding generation and proves the exact unique,
  authenticated regular-Chat target is IDLE through the existing surface/draft/generation
  gates before and after the structural witness. Any uncertainty retains the flight.
- The same outbound resolver used for submission proof identifies the one exact wake
  user turn. Only that user identity text is compared, in-page, with the already bounded
  selector-only doorbell. No text or turn identity is returned by the witness.
- That user marker belongs to one visible flat turn container in the unique main. Its
  immediate next element sibling is the final visible assistant turn container, with
  consecutive `conversation-turn-N` structural ordinals and explicit user/assistant
  `data-turn` roles. Both ordinals must identify unique containers, including hidden
  matches. There can be no skipped sibling, later sibling, nested turn container or
  intervening user turn. Missing/virtualized ordinal gaps cannot establish completion.
- The successor contains exactly one explicit assistant-role marker, with no user bubble,
  mixed role or nested assistant marker. Its marker and enclosing structure are visible;
  hidden, aria-hidden and invisible wrappers are rejected. Role-bearing ancestor or
  descendant ambiguity is rejected. Completion identity candidates are capped at 256
  and ancestor inspections at 32 levels; exceeding either bound retains the flight.

The witness consumes only exact wake-user identity and bounded structural ordering,
role, containment and visibility metadata. Assistant text, innerText, innerHTML, rendered
content, hidden reasoning, semantic meaning and raw chat history are never read or
exported. The witness receives no URL, account/session/credential data, CDP endpoint or
local path; the existing surrounding surface gates alone retain exact-target authority.
Only a Boolean leaves the in-page witness. It cannot select a flow, verdict or lifecycle
action, and it does not prove that Brain performed semantic work.

An absent or inexact wake, missing/hidden/duplicate/nested/role-ambiguous successor,
unsupported structure, intervening user, virtualized gap, target mismatch, unproven
surface, Human draft, active generation or adapter error keeps the flight held and
unresolved pending subjects unsent. IDLE, elapsed time, subject age, cooldowns and retry
counts never substitute for this witness. AMBIGUOUS attempts remain proof-only: this
witness cannot bypass exact submission proof or authorize resend.

On structural completion, only the flight pointer is cleared. The holder remains
SUBMITTED with its existing generation, reason and dedupe identity. No canonical
engineering state changes. The next distinct pending subject still crosses fresh
canonical and binding-generation barriers, exact-target/surface checks, draft/generation
guards, insertion acceptance and the immediate pre-click barrier; each finite FIFO lane
pass can submit at most one new wake. A resolved pending subject may instead NOOP.

The candidate changes only the local wake module, its focused synthetic tests, this
document and the H4B conformance document. The durable lane schema is unchanged; no
timestamp, cooldown, time-derived completion or provider-specific persistent state is
introduced. Budgets stay unchanged: workflow timeout 5 minutes, canonical observation
30 seconds, CDP attach 10 seconds, page/insert waits 3 seconds, submission proof 5 seconds,
two follow-up rechecks at 15-second intervals, and the optional scheduled drain of four
rechecks with that same interval and five-minute cron cadence. There is no second
transport, background loop, browser launcher or new lifecycle/semantic authority.

Focused deterministic fixtures model the stale-flight pair and fail-closed counterexamples
without a live browser or canonical checkpoint. Runtime owns canonical verification and
EVIDENCE. TASK-287 acceptance is a reliability prerequisite selected by Human priority;
H4B stays open until a future naturally produced real unresolved RESULT supplies the
separate Human/Brain live semantic-resume observation. It neither closes H4B nor starts H5.

## 14. TASK-289 terminal FAILURE same-TASK revision supersession

A deferred terminal FAILURE for an older TASK revision can outlive its correction
obligation. The observed RUN-288-001 FAILURE belongs to TASK-288 r1, while canonical
main contains TASK-288 r2. Its old `DEFERRED/LANE_IN_FLIGHT` record must not cause a
stale correction wake when the flight is absent. Browser IDLE and later RUN-288-002,
REVIEW-288-002 or publication facts are not the supersession proof.

The bounded terminal FAILURE reader first reconstructs the exact failure artifact,
RUN identity, failed head and TASK binding and validates any existing exact REPAIR
or REPAIR-supersession lineage. Those resolution paths remain valid independently
of TASK acquisition; malformed repair lineage still fails closed. If they do not
resolve the FAILURE, the reader fetches the observed canonical main commit and
reads only `.ai/tasks/<failed-task-id>.yaml`. Duplicate-key rejection and the
canonical TASK contract validator must accept that document. Its `task_id` must
match the failed RUN exactly. A strictly greater revision resolves the old FAILURE;
an equal revision without existing REPAIR remains UNRESOLVED. Authoring Ingress
enforces revision continuity one revision at a time; the reader need not require
the current revision to be the immediately following one.

The TASK document itself is the Human/Brain semantic successor. No roadmap status
or `next_action`, later RUN, REVIEW, publication, chat activity, elapsed time or
browser state is consulted to establish this proof. Missing/unreadable main or TASK,
malformed contracts, identity mismatch and lower revisions produce UNKNOWN. Main
is included in the same before/after canonical ref snapshot as the terminal lineage;
movement during acquisition, including during the TASK read, produces UNKNOWN.
The existing 30-second observation budget applies. Terminal RESULT freshness is
unchanged and does not acquire main/TASK for this rule.

The existing finite drain turns a freshly superseded deferred FAILURE into
`NOOP/CANONICALLY_RESOLVED` and stores `RESOLVED_NOOP` before browser attachment or
Send. Unrelated subjects retain independent fresh canonical and binding-generation
barriers, browser/draft checks, at most one submission per pass, permanent dedupe
and proof-only ambiguity. No lane schema, completion witness, selector, timing,
retry authority, generic successor router or lifecycle reducer changes.

The four-file candidate includes synthetic freshness and drain fixtures and this
contract; it establishes pre-verification implementation properties only. Runtime
owns EVIDENCE and the unchanged minimum-sufficient verification commands:

```text
python -m pytest -q tests/test_local_chat_wake.py tests/test_h4b_regular_chat_brain_resume.py
git diff --check
```

Reviewer owns semantic verdict and Publisher owns publication. H4B remains
**UNPROVED**. Human/Brain owns any later authorized live drain, real unresolved
RESULT semantic-resume observation and roadmap closure. Executor performs no live
drain, local-state recovery, H4B evidence manufacture or roadmap advancement.
