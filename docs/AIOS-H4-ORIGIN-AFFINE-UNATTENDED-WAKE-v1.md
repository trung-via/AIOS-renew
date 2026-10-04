# AIOS H4 Origin-Affine Return and Unattended Local Wake v1

Status: HUMAN APPROVED / CANONICAL PLANNING
Approved: 2026-10-04
Authority: HUMAN_BRAIN_PLANNING
Parent track: `brain-runtime-semantic-handoff-hardening-v1`
Production requirement: `ORIGIN_AFFINE_RETURN + UNATTENDED_LOCAL_WAKE_REQUIRED_BEFORE_H5`

## 1. Human production objective

Before H5 may close the Brain-Runtime semantic-handoff hardening track:

1. a semantic flow initiated from one regular ChatGPT conversation must return later Brain-attention wake events to that exact originating conversation;
2. distinct flows from different conversations inside the same repository/project must retain independent return affinity;
3. wake delivery must not require the Human to pre-open Chrome, navigate to the correct Project, open the target conversation, or leave that exact tab active;
4. independent project/conversation lanes must not block, redirect, consume, or receive another lane's wake because of busy, draft, generation, lock, failure, recovery, or binding state;
5. the wake path remains transport only and creates no Brain, Planner, lifecycle-router, Reviewer, Runtime, Publisher, retry/failover, or roadmap authority.

These are production closure requirements, not post-H5 enhancements.

## 2. Preserved authority and truth boundaries

- Human owns intent, priority, risk acceptance, and explicit route ownership transfer.
- Brain owns semantic interpretation, architecture, roadmap reasoning, and selected semantic authority.
- Runtime owns canonical lifecycle coordination, mutation authority, canonical verification, evidence, and lifecycle-state reduction.
- Reviewer owns semantic verdict.
- Publisher owns publication of the exact eligible reviewed source candidate.
- Wake transport owns bounded delivery only.
- Conversation identity and URL remain operational transport configuration, not engineering truth.
- Wake payload remains an untrusted selector-only doorbell.
- Raw ChatGPT conversation URLs, cookies, credentials, account/session data, and transcript content MUST NOT become canonical TASK/RUN/RESULT/REVIEW semantics.
- No assistant-output parsing may select lifecycle action.
- No ChatGPT Work production wake fallback.
- No automatic roadmap advancement.

## 3. Architecture audit

Audit profile: `brain-high-value-v3`.

### Stage 1 — CONSTRUCT

Outcome: `RISK_FOUND`.

The initial proposal correctly identified repository-level fixed-chat binding and Human-prepared browser state as production gaps, but it was incomplete because it did not prove how origin identity is captured without guessing, did not distinguish conversation-lane identity from TASK identity, and risked conflating unattended capability with a specific headless implementation.

### Stage 2 — ADVERSARIAL_AUDIT_AND_RECONCILE

Closure: `CLEAR_WITH_MANDATORY_REFINEMENTS`.

Reconciled findings:

- current repository-keyed binding is insufficient for same-repository multi-chat flows;
- origin capture must be separately proven before generic return routing implementation;
- origin must never be inferred from timestamps, active tab, most-recent conversation, chat memory, or transcript scraping;
- raw chat URL remains noncanonical; any return selector exposed to lineage is bounded and opaque;
- lane identity is conversation/return-route scoped, not TASK scoped;
- route transfer requires explicit Human authority;
- origin-affine flows never silently fall back to the historical repository-default H4A3 chat;
- unattended delivery is the capability requirement; `true headless` is not a roadmap-mandated HOW;
- a local unattended transport requires the host and authorized authenticated environment to be available; powered-off-host remote wake is out of scope;
- existing H4A4 queue, dedupe, deferred recovery, Human supersession, ambiguity, and multi-project isolation are reused rather than rebuilt;
- final H4B live semantic-resume proof must exercise the production origin-affine unattended path.

Outcome: `CANDIDATE_APPROVED_BY_HUMAN`.

## 4. H4C0 — Origin Capture Feasibility & Contract Freeze

Objective: establish one deterministic, authority-safe method by which AIOS can associate a newly initiated semantic flow with the exact regular-Chat conversation from which that flow originated.

This phase answers only how origin identity is established. It does not yet implement generic return routing.

Required properties:

- distinguish two conversations belonging to the same repository/project;
- establish origin at or before the semantic flow's initial authorized handoff;
- produce only a bounded operational route identity;
- remain independent of chat transcript contents;
- never infer origin from the active tab, most-recent chat, timestamps, model memory, provider/account identity, or other heuristic correlation; host-supplied tool-call session metadata may be evaluated only as a replaceable transport-origin input, not canonical identity;
- keep raw conversation URL outside canonical TASK/RUN semantics;
- fail closed when origin is missing, stale, conflicting, or ambiguous;
- create no semantic or lifecycle authority.

Exit gate:

1. one origin-capture mechanism has passed two-stage architecture audit;
2. it has been demonstrated against two distinct regular conversations;
3. exact origin is established without content inference or heuristic correlation;
4. missing/stale/ambiguous origin fails closed;
5. no repository-default fallback is used for a new origin-affine flow.

If no safe mechanism is feasible, H4C0 returns an explicit architecture blocker and stops.

### TASK-292 bounded feasibility candidate

The [OpenAI Plugin Reference](https://developers.openai.com/plugins/reference) documents host tool-call `_meta["openai/session"]` for correlating calls within a ChatGPT session. TASK-292 evaluates this exact field through `OPENAI_SESSION_ORIGIN_CAPTURE_V1`, terminating at a provider-neutral `ORIGIN_HANDLE_V1` result. Only a deterministic, versioned opaque handle and bounded status metadata are exposed; raw metadata is never emitted, logged, persisted, or made canonical. This transport feasibility basis does not establish that the session value equals, contains, or can be converted to a chat URL/UUID.

The [H4C0 conformance contract](AIOS-H4C0-ORIGIN-CAPTURE-CONFORMANCE-v1.md) freezes the bounds, fail-closed behavior, one read-only development MCP entry, and post-publication Human/Brain procedure: two calls each from two distinct regular ChatGPT conversations, stable opaque identity within each and distinct identity across them, with ambiguity recorded as `UNPROVED`. Raw session values and transcript content must never enter canonical records.

TASK-292 acceptance and synthetic tests do not prove that future live observation or close H4C0. Reviewed publication makes the candidate available for the later Human/Brain observation; Human/Brain alone decides H4C0 live closure or architecture fallback. **H4C1 remains blocked until H4C0 live closure**, including the required architecture audit. No local-wake, lifecycle, Reviewer, Publisher, Runtime, or roadmap authority changes follow from this probe.


### Human-approved H4C0 fallback — page-scoped origin bootstrap

On 2026-10-04 the Human stopped the optional Responses API smoke test and approved an architecture fallback after the current regular-Chat account surface did not expose the Developer Mode/custom MCP entry needed for the planned TASK-292 two-chat observation. This is an operational availability observation for the current surface, not a claim that OpenAI session metadata is invalid or unavailable on every account.

TASK-292 remains immutable engineering evidence: its bounded `OPENAI_SESSION_ORIGIN_CAPTURE_V1` implementation, Runtime verification, semantic PASS, and exact publication remain valid. Its task acceptance and publication never closed H4C0, and the unavailable current-surface live path does not retroactively invalidate that work.

The selected fallback architecture candidate is:

`PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1`

Two-stage architecture audit used `brain-high-value-v3`:

- Stage 1 `CONSTRUCT`: `RISK_FOUND`;
- Stage 2 `ADVERSARIAL_AUDIT_AND_RECONCILE`: `CLEAR_WITH_MANDATORY_REFINEMENTS`;
- Human outcome: `CANDIDATE_APPROVED_BY_HUMAN`.

The contract is a one-time explicit Human attestation per regular-Chat conversation, not a new way to send every message. A chat that has not yet established an AIOS return route exposes an in-page Human action conceptually equivalent to `Connect this chat to AIOS` / `Start AIOS Flow`. The gesture originates inside the exact regular-Chat document selected by the Human. Browser-toolbar selection, global hotkeys that first query the active tab, most-recent-tab selection, timestamps, transcript contents, assistant output, and model memory are not origin authority.

The page-scoped bootstrap must:

1. create a bounded ephemeral challenge in the exact document receiving the Human gesture;
2. prove exactly one regular-Chat page owns that challenge and that the normalized conversation URL is unchanged across the proof;
3. mint or reuse one opaque conversation-scoped return-route handle;
4. durably store only the sensitive route mapping machine-locally, including exact normalized conversation route, authorized browser/profile binding where required, and binding generation;
5. revalidate the same page/challenge after the durable write and before bootstrap-envelope insertion;
6. insert only bounded opaque route metadata into the initial AIOS handoff on that same page;
7. fail closed before submission when binding, durability, uniqueness, surface, challenge, generation, or page continuity is unproved.

Ordering is mandatory:

```text
explicit in-page Human gesture
        ↓
exact-page challenge proof
        ↓
mint/reuse opaque route handle
        ↓
durable machine-local binding
        ↓
same-page revalidation
        ↓
bounded bootstrap envelope
        ↓
initial authorized AIOS handoff
```

Sending first and attempting to recover origin afterward is forbidden. A route handle is a bounded selector only; possession of it grants no TASK, execution, review, publication, roadmap, or route-transfer authority. The raw conversation URL remains noncanonical and must not enter TASK/RUN/RESULT/REVIEW semantics.

One regular-Chat conversation reuses one stable return-route handle across multiple flows unless an explicit Human-authorized rebind/transfer later changes its generation. Two distinct conversations in the same repository must receive distinct handles. Multiple tabs showing the same conversation do not create multiple conversation routes; the ephemeral challenge identifies the exact document on which the Human acted while the durable route remains conversation-scoped.

The origin subsystem does not inspect the Human prompt, transcript, or assistant response and must not become a Planner, Runtime, Reviewer, Publisher, generic router, or second wake queue. Existing bounded surface validation, URL normalization, durable-state, generation, ambiguity, and no-blind-resend primitives should be reused where semantically applicable rather than duplicated.

At minimum, fail closed for unproved gesture, non-unique challenge, non-regular Chat surface, invalid conversation route, page change during bootstrap, registry conflict, uncertain durable write, binding-generation change, bootstrap-envelope insertion failure, or ambiguous submission.

The fallback does not require Developer Mode, MCP, Responses API credit, or host-provided `openai/session` metadata. Those mechanisms remain replaceable transport options rather than H4C0 authority.

H4C0 live closure still requires real two-chat evidence. The selected fallback proof must demonstrate at least: Chat A bootstrap twice resolves to the same route A; Chat B bootstrap twice resolves to the same route B; A differs from B; same-repository chats remain distinct; reload/reopen of the same conversation preserves its route when valid; page/challenge ambiguity fails closed; and raw conversation identity remains outside canonical engineering artifacts.

**H4C1 remains blocked until the fallback implementation is reviewed/published and the Human/Brain-owned two-chat live conformance gate closes.** No automatic roadmap advancement follows from implementation, review, publication, or live transport success.

### TASK-293 bounded implementation and live-procedure binding

The selected contract is `PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1`, implemented
by `src/aios_renew/origin_bootstrap.py` with one bounded attach-only development
entry, `scripts/aios_origin_bootstrap.py`. The [H4C0 conformance contract, sections
6–10](AIOS-H4C0-ORIGIN-CAPTURE-CONFORMANCE-v1.md#6-selected-page-scoped-origin-bootstrap-contract)
defines the in-document trusted Human gesture, 30-second ephemeral challenge,
exclusive durable machine-local conversation registry, opaque envelope, exact
pre-submit ordering, fail-closed cases, and no automatic ambiguous resend.

The development surface uses the existing authorized regular-Chat browser through
its loopback CDP attachment. It does not select an active/recent tab, navigate,
start a browser, or depend on Developer Mode/MCP/Responses API/ChatGPT Work/session
metadata. Raw conversation identity and sensitive endpoint binding remain bounded
machine-local state; the initial envelope contains only contract, opaque route
handle, and generation. Existing local-wake safety/normalization/durability
primitives are reused without changing wake delivery or creating a queue/router.

After reviewed publication, Human/Brain must observe A1 = A2, B1 = B2, A1 != B1
for two distinct regular chats in the same repository, valid reload/reopen route
continuity, and page/challenge ambiguity failing closed. TASK-293 acceptance,
synthetic tests, review, and publication alone are not that live observation and
neither close H4C0 nor authorize H4C1. TASK-292's prior accepted/published lineage
remains immutable valid evidence. Human/Brain alone decides closure/fallback;
Runtime, Reviewer, Publisher, and this helper gain no roadmap or semantic authority.

## 5. H4C1 — Origin-Affine Return Routing

Objective: bind downstream Brain-attention delivery for one semantic flow to the exact route established at flow origin.

Target shape:

```text
Repository R

Chat A
  ├─ Flow X
  └─ Flow Z

Chat B
  └─ Flow Y

X -> Chat A
Z -> Chat A
Y -> Chat B
```

Rules:

- repository identity alone is insufficient to select a destination for new origin-affine flows;
- the machine-local registry owns the sensitive route-to-conversation mapping;
- the raw conversation URL remains noncanonical;
- one conversation/return route owns one serialized lane;
- multiple flows sharing one chat share that lane rather than creating competing composer locks;
- distinct chat lanes may progress independently;
- normal descendants of one semantic flow preserve its return affinity where applicable;
- transport cannot semantically decide lineage membership;
- route ownership moves only through explicit Human-authorized transfer;
- ambiguous post-submit attempts remain bound to the generation on which they were attempted;
- missing/stale/conflicting affinity fails closed or requires Human rebind;
- no silent fallback to H4A3/repository-default chat.

Legacy pre-origin-affinity flows may retain an explicitly classified `LEGACY_REPOSITORY_DEFAULT_ROUTE`; this must never be represented as exact origin affinity.

Required conformance:

- same repository, Chat A/Flow X and Chat B/Flow Y: X wakes only A; Y wakes only B;
- same Chat A, multiple flows: one lane, no simultaneous composer race, fresh pending-subject revalidation after each completed Brain turn;
- explicit route-generation change: no silent redirect and post-submit ambiguity stays bound to its attempted generation.

## 6. H4D — Unattended Local Wake Transport

Objective: remove the requirement that the Human prepare the ChatGPT UI before wake delivery.

Required production behavior:

```text
AIOS attention
      ↓
local unattended transport
      ↓
resolve exact origin route
      ↓
make target regular-Chat surface available
      ↓
prove exact target/surface safety
      ↓
submit one bounded doorbell
```

The Human must not need to:

- launch or prepare the delivery browser manually;
- navigate to the Project;
- open the target conversation;
- keep the target tab foregrounded;
- press Send.

The roadmap requires unattended delivery, not a specific browser implementation. `true headless`, visible background browser, or another bounded local mechanism is an Executor HOW decision subject to TASK acceptance.

Local availability boundary:

```text
host running + authorized authenticated environment available
=> unattended delivery may proceed
```

Powered-off-host remote/cloud wake is not authorized by this milestone.

Preserved hard gates include exact conversation identity, regular-Chat validation, Human draft protection, active-generation protection, duplicate suppression, canonical freshness barriers, binding-generation safety, post-send ambiguity no-resend, and no assistant-content extraction.

Failure to prove authentication/surface/route/target must fail closed. Transport must never silently choose another chat or account.

Live exit gate: with the target conversation not pre-opened or prepared by the Human, the transport obtains/restores the authorized delivery environment, reaches the exact target, proves all required safety gates, and submits exactly one doorbell without Human UI preparation.

## 7. H4E — Production Routing & Concurrency Live Conformance

Objective: prove H4C + H4D under real production multi-lane conditions and close the existing H4A4 second-live-lane residual.

Required live cases:

### A. Same repository, different chats

```text
AIOS-renew / Chat A -> Flow X
AIOS-renew / Chat B -> Flow Y
```

Both become eligible for attention.

Required: X -> A exactly once; Y -> B exactly once; no cross-delivery.

### B. Different repositories

Project A is busy/generating while Project B is idle.

Required: B remains independently deliverable; no global `BRAIN_BUSY`.

### C. Same chat, multiple pending subjects

Required: one in-flight Brain wake per conversation lane; later subjects remain durable; canonical state is freshly revalidated after completion; resolved/stale subjects become NOOP; only still-unresolved subjects may send.

### D. Human supersession

A wake is deferred and Human/Brain handles that exact canonical subject manually.

Required: fresh canonical reconciliation -> `RESOLVED_NOOP`; no duplicate Chat turn. Unrelated Human chat activity does not consume the event.

### E. Missing/stale origin route

Required: fail closed; never fallback to repository-default/H4A3 chat.

### F. Route generation change

Required: no redirect of ambiguous attempts; generation-safe pre-submit re-resolution.

### G. Unattended target acquisition

The exact target chat is not pre-opened/prepared.

Required: unattended delivery succeeds without Human navigation.

The historical H4A5 residual `SECOND_SEPARATELY_AUTHORIZED_DEPLOYED_LIVE_LANE_UNAVAILABLE` must be proven closed for production H5 closure; it is no longer waivable under this approved production requirement.

## 8. Final H4B semantic-resume proof

The final H4B proof occurs after H4C0, H4C1, H4D, and H4E.

Use one naturally occurring real unresolved canonical semantic checkpoint.

Required sequence:

```text
real canonical attention
        ↓
origin-affine route resolution
        ↓
unattended exact-chat delivery
        ↓
selector-only doorbell
        ↓
MINIMUM_FRESH_BRAIN_SYNC_V1
        ↓
exact unresolved lineage
        ↓
Unified State / Flow Card
        ↓
selected Brain/Reviewer authority
        ↓
at most one semantic continuation step
        ↓
new Human/Runtime boundary
```

Forbidden substitutes: chat memory, wake-payload semantics, assistant transcript scraping, fabricated checkpoint, replay of resolved attention, or historical fixed H4A3 chat unless that exact chat is the captured origin.

## 9. H5 additional mandatory conformance

Existing H5 conditions 1–24 remain.

Add:

25. `ORIGIN_CAPTURE_INTEGRITY` — a new origin-affine flow cannot enter production return routing without exact route affinity established through H4C0.
26. `SAME_REPOSITORY_MULTI_CHAT_ISOLATION` — flows from separate conversations in one repository cannot cross-deliver or consume one another.
27. `NO_SILENT_DEFAULT_ROUTE_FALLBACK` — missing/stale/ambiguous/conflicting affinity never redirects to H4A3/repository default.
28. `EXPLICIT_ROUTE_TRANSFER_SAFETY` — only Human-authorized route transfer moves future affinity; pending/ambiguous attempts obey generation safety.
29. `UNATTENDED_DELIVERY` — Human is not required to pre-open or navigate to the target conversation.
30. `TRANSPORT_INDEPENDENCE` — browser/session acquisition and restoration remain delivery mechanisms only and cannot select semantic/lifecycle action.
31. `PRODUCTION_MULTI_LANE_LIVE_PROOF` — at least two independently authorized deployed live lanes prove isolation under real busy/generation conditions.
32. `PRODUCTION_SHAPE_SEMANTIC_RESUME` — final H4B unresolved semantic-resume proof uses the origin-affine unattended production path.

H5 is blocked until these conditions and the original H5 matrix are satisfied.

## 10. Explicit non-goals

This plan does not authorize:

- storing raw ChatGPT conversation URLs in canonical TASK/RUN artifacts;
- chat history as engineering truth;
- browser-state semantic routing;
- automatic Human-intent inference;
- automatic roadmap progression;
- provider/model-specific lifecycle semantics;
- assistant-output parsing to select completion or next action;
- ChatGPT Work as production wake fallback;
- remote/cloud wake while the local host is powered off;
- automatic route migration between conversations;
- rebuilding valid H4A4 queue/dedupe/recovery semantics.

## 11. Approved sequencing

```text
close/supersede currently proven H4B bounded blocker(s)
        ↓
H4C0 ORIGIN CAPTURE FEASIBILITY
        ↓
Human/Brain architecture decision gate if feasibility is not CLEAR
        ↓
H4C1 ORIGIN-AFFINE RETURN ROUTING
        ↓
H4D UNATTENDED LOCAL WAKE
        ↓
H4E PRODUCTION ROUTING + MULTI-LANE LIVE CONFORMANCE
        ↓
H4B FINAL REAL SEMANTIC RESUME PROOF
        ↓
H5 INTEGRATION / CONFORMANCE CLOSURE
        ↓
existing post-hardening roadmap
```

No existing TASK/RUN is retrospectively changed by this planning decision. Approval of this plan does not itself authorize an Executor, create a TASK/RUN, prove implementation, or advance any engineering lifecycle state.
