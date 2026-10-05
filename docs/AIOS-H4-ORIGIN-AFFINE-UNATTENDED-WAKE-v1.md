# AIOS H4 Origin-Affine Return and Unattended Local Wake v1

Status: HUMAN APPROVED / CANONICAL PLANNING
Approved: 2026-10-04
Authority: HUMAN_BRAIN_PLANNING
Parent track: `brain-runtime-semantic-handoff-hardening-v1`
Production requirement: `ORIGIN_AFFINE_RETURN + UNATTENDED_LOCAL_WAKE_REQUIRED_BEFORE_H5`

## H4C1 implementation contract (TASK-301)

### New-TASK origin provenance gate (TASK-309)

Prospective revision-1 `ORIGIN_AFFINE` AUTHOR_TASK now requires an independently
admitted `origin_authoring_proof` in the operational ingress envelope. A selector
alone cannot establish provenance. H4C0 appends a random
`origin-authoring-v1:<64 lowercase hex digits>` proof to the exact proved page's
bootstrap envelope. An external `.authoring` sidecar binds its digest to that
route, generation and successful bootstrap attempt, with a one-hour lifetime.
Issuance precedes insertion; admission requires the registry's exact `SUBMITTED`
attempt. Unsubmitted, ambiguous, replaced, expired, wrong-generation, missing
or conflicting local state fails closed. No raw chat identity or endpoint leaves
the local boundary. The proof is operational provenance, never TASK/RUN truth.

The reusable Brain ingress workflow frames the immutable authorized Issue on a
hosted runner, then runs a bounded provenance job on the existing self-hosted
Windows `aios-renew` runner only for an origin revision-1 request. That job reads
the machine-owned `AIOS_ORIGIN_REGISTRY` environment setting and its sidecar;
hosted code cannot read or claim to validate that local state. Under exclusive
registry/sidecar locks it consumes the proof for exactly one carrier binding:
repository/Issue/actor, GitHub run id and run attempt, TASK id, expected main SHA,
route handle, generation, and parsed ingress-envelope digest (including the
audited handoff). The same binding returns the same receipt without another
state mutation. Any different attempt, subject, predecessor or envelope fails.

The self-hosted job signs only those bounded selectors and proof validity times
with HMAC-SHA256 using the deployment-owned `AIOS_ORIGIN_ADMISSION_KEY` secret
(64 lowercase hexadecimal characters, independently provisioned). Its one-day
operational artifact is named for the current workflow attempt. Hosted delivery
downloads only that run's artifact, authenticates its signature and entire
binding, and passes the authenticated context outside the semantic payload.
AUTHOR_TASK authenticates again using the deployment key, matches the admitted
route/generation to the final parsed TASK, and rechecks validity before object
creation and canonical publication. Missing setup, failed local admission,
missing artifact, signature/body/attempt mismatch or expiry blocks mutation;
the existing hosted FAIL receipt and attention path remain available. There is
no hosted-only substitute or Issue-supplied admission flag.

Revision-1 origin replay also requires its original admitted attempt and complete
envelope. Ordinary revisions use no current-chat proof and must preserve the
existing affinity exactly. Supplying a proof on a revision or explicit legacy
request is rejected; historical legacy replay remains separate. No provenance
is inferred from a TASK, prior TASK, default/recent chat, transcript or Brain
memory. Proofs are not appended to canonical TASK, RUN, RESULT or review records.
The sidecar is bounded to 256 proofs and 262,144 bytes; uncertain writes, stale
locks, malformed/duplicate state and exhaustion require Human handling, without
automatic eviction, retry or transfer. A fresh bootstrap invalidates a prior
proof for that route by changing its local bootstrap attempt, never generation.

This gate changes no RUN/attention propagation, wake event identity, lane
ownership, exact-target/generation/draft checks, dedupe or no-blind-resend rule.
It changes no TASK-308/REVIEW-308-001 or TASK-303/RUN-303-004 lineage or disposition.
Runtime owns canonical verification and EVIDENCE; Reviewer owns verdict; Publisher
owns exact publication; Human/Brain owns later TASK-308 disposition and phase
closure. Implementation and regression definitions claim none of those outcomes.

The permitted candidate implements the following selector/lineage/lane contract.
This section records implementation behavior; it does not record Runtime PASS,
Reviewer verdict, publication, local migration/setup, live routing conformance,
or H4C1 roadmap closure. Those facts belong to their downstream authorities.

Canonical TASK has exactly one optional historical `return_affinity` carrier:

```yaml
return_affinity:
  kind: ORIGIN_AFFINE
  route_handle: page-origin-v1:<64 lowercase hexadecimal characters>
  generation: 1
```

The only other form is `{kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}`. The origin
form permits only these three fields and a positive integer generation bounded
by H4C0's unchanged maximum, 2147483647. Null, booleans, unknown fields, raw
identity/endpoint/path fields, malformed handles and duplicate YAML fields fail
closed. A historical missing carrier reads as legacy, never as exact origin.
After TASK-301's exact publication activates this authoring implementation, new
AUTHOR_TASK identities and revisions require explicit classification. Historical
legacy identical revision replay remains readable; origin revision-1 replay follows
TASK-309's admitted-attempt gate above. Ordinary revision authoring preserves
the previous selector, including generation; changing ownership requires separate
explicit Human authority and a future transfer contract. Runtime and transport
cannot infer, authorize, increment or perform a transfer.

PRIMARY, REMEDIATION and REPAIR allocations copy the TASK selector into the frozen
RUN. REMEDIATION's carrier resides in `execution.run.return_affinity`; PRIMARY
and REPAIR use `return_affinity` in their RUN record, with REPAIR's exact embedded
RUN/TASK lineage retaining it. Lease identity, persisted lifecycle observations,
decision material, review ingress, correction admission and terminal transport
reject conflicting selectors. RESULT provides no selector-selection authority.
No raw chat URL/UUID, endpoint, local path, registry, draft, transcript, assistant
output or credential belongs in any canonical artifact.

Attention event identities and selector schemas are unchanged. The read-only
resolver binds exact terminal refs, artifact RUN, base/candidate TASK revision,
and correction ancestors; review/publication events additionally bind their
exact review identity (and proven main inclusion for publication success).
Dispatch uses the exact authorized TASK commit or source RUN/REPAIR lineage.
Parseable pre-canonical AUTHOR_TASK rejection uses only the exact trusted source
artifact and issue payload bytes identified by the event's issue identity and
body digest. Recovery resolves only its stored original event. Consulted refs
are checked again, ancestry traversal is bounded to 32 RUNs, and missing,
conflicting, changed or unprovable lineage produces no routable affinity.
Unknown carriers and conflict events lacking reconstructible subject lineage
remain blocked; repository identity never fills that gap.

Machine-local routing configuration uses `AIOS_LOCAL_CHAT_WAKE_CONFIG` with this
closed version-3 shape, outside every repository and bare Git store:

```json
{
  "version": 3,
  "origin_registry": "<absolute machine-local H4C0 registry file>",
  "lane_directory": "<existing absolute machine-local directory>",
  "repositories": ["<configured owner/repository>"],
  "legacy_config": "<optional separate historical configuration file>"
}
```

These placeholders describe a later Human-owned setup; this candidate performs
no setup or migration. ORIGIN_AFFINE resolves only the exact H4C0 opaque handle
and generation in the bounded registry. Missing/duplicate/conflicting handles,
invalid state, stale generation, uncertain writes/locks or an unresolved
bootstrap attempt block delivery. There is no legacy fallback. The raw normalized
chat URL and loopback endpoint remain inside this local resolution/browser
boundary. H4C0 allocation, gesture proof and single-submit semantics are unchanged;
TASK-309 adds only the bounded authoring proof alongside that existing boundary.

One handle owns one durable lane file and exclusive lock, independent of TASK
and generation. Separate repository buckets preserve unchanged event identities
even when one conversation carries flows from multiple repositories. Exact-event
inboxes, binding snapshots, ambiguity records and dedupe belong to that lane.
Distinct routes in the same repository have separate files/locks/inboxes and
cannot consume, compact, reconcile or block each other's subjects. Legacy files
remain separate and explicitly classified, and cannot occupy the origin directory.

A possible click durably records its attempted generation before submission.
AMBIGUOUS reconciliation uses only the original lane's stored URL/endpoint and
generation snapshot and may prove the exact user turn or canonical resolution;
it never submits. A new registry generation cannot redirect or replay that held
attempt. Pending subjects cross fresh affinity, registry and canonical barriers
before a possible click. Human draft/active-generation guards, bounded surface
validation, event-local ambiguity and at-most-one submission per lane pass remain.

Direct delivery resolves its exact event. The workflow's bounded follow-up uses
`--drain --lane-event-id` with that same admitted event and never enumerates other
routes. Scheduled `--drain --all-lanes` enumerates only bounded already configured/
admitted local lanes, without canonical TASK search or browser acquisition.
Independent lane workers preserve progress after another lane's error or hold;
scheduled passes retain finite rechecks and a shared 180-second observation
budget. Existing freshness, exact-event dedupe, proof-only reconciliation and
no-blind-resend rules apply within every lane.

Focused regression definitions cover strict TASK classification and revision
transfer rejection, frozen RUN/lease propagation, persisted selector substitution,
admitted ingress, terminal/review/publication/dispatch and descendant lineage,
legacy separation, same-route multi-flow serialization, same-repository route
isolation, cross-repository shared-handle locking, missing/stale/conflicting origin
state, generation-change ambiguity and follow-up/scheduled scoping. Runtime owns
their canonical execution and EVIDENCE. Reviewed exact publication precedes any
local migration, live routing proof or Human/Brain H4C1 closure decision. H4D/H4E,
unattended browser acquisition, final H4B/H5 and roadmap advancement remain
downstream; this implementation does not authorize or perform them.

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
- origin must never be inferred from timestamps, active tab, most-recent conversation, chat memory, semantic transcript similarity, or free-form assistant-output interpretation; exact matching of a precommitted machine-generated opaque rendezvous token is permitted only inside the bounded origin-capture boundary and grants no semantic or lifecycle authority;
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
- remain independent of semantic chat meaning: Human/assistant prose, topic similarity and inferred intent must not select origin; a precommitted high-entropy opaque rendezvous token may be searched and exactly matched as operational transport evidence only;
- never infer origin from the active tab, most-recent chat, timestamps, model memory, provider/account identity, semantic transcript similarity, or other heuristic correlation; exact full-token rendezvous matching and host-supplied tool-call session metadata may be evaluated only as replaceable transport-origin inputs, not canonical identity;
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


### Human-approved 2026-10-06 correction — opaque cross-device origin rendezvous

The Human explicitly superseded the broad `NO_TIMESTAMP_OR_TRANSCRIPT_MATCHING`
planning prohibition for origin capture after two-stage Brain architecture audit found
that it unnecessarily blocks a deterministic cross-device rendezvous mechanism. This
does **not** authorize semantic transcript inference, recent-chat selection, active-tab
selection, timestamp correlation, model-memory correlation, or assistant-output parsing
for lifecycle or roadmap action.

The selected architecture candidate is:

`OPAQUE_ORIGIN_RENDEZVOUS_V1`

Its intent is to make the Human's ordinary initiating ChatGPT message the only Human
gesture required for a new AIOS flow, including when that message originates from a
phone or another browser. Brain may emit one bounded machine-generated high-entropy
rendezvous marker in the exact originating conversation and carry the same marker over
a subordinate operational channel to the self-host transport. The local origin resolver
may search the authenticated ChatGPT account only for the exact full marker and must
fail closed unless exactly one regular-Chat conversation is proven to contain it.

The marker is operational transport evidence only. It must be random/high-entropy,
single-purpose, bounded, expiring, and non-semantic. It must not encode TASK, RUN,
roadmap, user text, repository secrets, chat URL, account identity, lifecycle state or
semantic instructions. Matching is byte/exact-token matching, not similarity search or
model interpretation.

A successful resolution requires at minimum:

1. the bounded account-search result set is complete enough to establish exactly one
   result for the full rendezvous marker; the first/top result alone is never uniqueness
   proof, and incomplete/ambiguous enumeration fails closed;
2. opening that sole result yields exactly one regular-Chat conversation;
3. the exact full marker is re-proven in that same conversation after navigation;
4. the normalized conversation route is stable across proof;
5. route allocation/reuse is performed only after that proof;
6. zero or multiple matches, unavailable history/search, deleted/unsynced conversation,
   account/profile mismatch, route movement, or proof uncertainty fail closed;
7. no fallback to recent chat, active tab, repository default, timestamp, semantic text
   or partial token match is permitted.

This origin-only exception does not weaken the production wake rule that assistant
output must not select lifecycle action. Wake transport remains a bounded doorbell and
must still reconstruct semantics from canonical state after delivery. The origin resolver
may observe only the exact precommitted rendezvous marker needed to establish the
conversation route.

The candidate is not yet production authority. A 2026-10-06 Human operational
observation established only that the exact marker was discoverable in the live account
search surface. That is positive partial feasibility evidence, not proof of exhaustive
unique resolution, opened-conversation re-proof, cross-device origin capture, zero-match
failure or multiple-match rejection. Before TASK authoring the bounded live probe must
close those remaining cases, including a conversation initiated from another device.
Failure of that live probe returns an architecture blocker rather than authorizing
heuristic fallback.

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

### TASK-302 bounded H4D acquisition contract, corrected by TASK-304, TASK-305, TASK-306, and TASK-307

`src/aios_renew/unattended_chat_wake.py` is a transport primitive under the
existing durable lane pass, with no standalone launcher, router, queue, dispatch,
account selector, lifecycle reducer, or retry service. Only an existing exact
H4C1 origin Binding can receive acquisition authorization. Historical explicitly
legacy delivery remains attach-only; absent/stale origin affinity never consults
that legacy route. Proof-only reconciliation of an ambiguous attempt also remains
attach-only and cannot launch, create, navigate, insert, or click.

The fast path attaches to the Binding's endpoint and preserves its one exact
existing target page. It does not inspect acquisition configuration, launch,
navigate, select another tab, or create a duplicate. If availability is missing,
the lane must positively prove canonical `UNRESOLVED`, the original event's exact
affinity, and unchanged Binding before starting acquisition. Canonical `UNKNOWN`
cannot authorize acquisition. After acquisition, these same barriers run again
before surface inspection/editing, before insertion, and before the durable
pre-click ambiguity write. A resolved subject becomes `RESOLVED_NOOP`; uncertainty
or changed affinity/Binding cannot submit. Existing attach-path freshness policy,
exact regular-Chat surface checks, Human draft/active-generation guards, outbound
dedupe, exact user-turn proof, and ambiguity/no-blind-resend rules are preserved.

Human authorization is an external machine-local JSON file named by the runner's
inherited `AIOS_UNATTENDED_CHAT_WAKE_CONFIG`. It is separate from the H4C1
`AIOS_LOCAL_CHAT_WAKE_CONFIG` registry/lanes. Its schema is exactly:

```json
{
  "version": 1,
  "environments": [
    {
      "cdp_endpoint": "<exact authorized IPv4 loopback HTTP endpoint>",
      "executable": "<absolute path to the authorized Chromium executable>",
      "user_data_dir": "<absolute path to the dedicated authorized user-data directory>",
      "profile_directory": "<explicit existing profile directory name>",
      "allow_launch": true,
      "exclusive_user_data": true
    }
  ]
}
```

Placeholders describe required local values and are not usable configuration.
There are at most eight environments, each with one distinct endpoint and a
nonoverlapping user-data directory. The endpoint must be an exact
`http://127.0.0.1:<port>` binding (optional root slash); it is never derived from
the executable or a listening browser. Executable, user-data directory, and named
profile must already exist. The supported production acquisition boundary is
the Windows self-hosted runner and an explicitly configured Chromium `.exe`.
UNC/network paths, repository/bare-store paths, path aliases into those stores,
arbitrary extra launch arguments, duplicate environments, and inferred profiles
are rejected. `exclusive_user_data: true` records Human authorization of a
dedicated environment; it does not replace the OS ownership/occupancy checks.
`allow_launch: false` permits owned-endpoint page acquisition but never launch.

The Human configures and authenticates this dedicated environment beforehand;
delivery requires no per-event Human browser launch, tab preparation, foreground
selection, navigation, or Send action. Authentication expiry is a fail-closed
surface condition requiring Human restoration, never credential extraction or
account selection. The host and runner must be running. This contract provides
no powered-off-host wake, remote/cloud environment, or ChatGPT Work fallback.

If attachment fails, launch permission requires a successful bounded Windows OS
listener-table query proving zero listening sockets on the exact configured local
port, across all local addresses including wildcard and IPv6 listeners. The query
enumerates the table before filtering for listening sockets on that port, so zero
matches are an explicit empty-array observation rather than a suppressed query
error. Query failure, malformed/non-array output, ambiguous/multiple observations,
or any listener on that port fails closed before launch. TCP refusal, timeout,
acceptance, or any other connection outcome alone never authorizes launch; no
cause of a timeout is inferred or changed. A zero-listener observation permits
only the existing one bounded acquisition attempt under explicit `allow_launch`.

Listener absence does not establish profile availability. The independent
conservative singleton/lock/DevTools markers and OS process command-line checks
remain required before launch; stale markers remain for Human reconciliation.
TASK-305 narrows process occupancy proof to the exact Human-configured dedicated
`user_data_dir`. Configured-directory `SingletonLock`, `SingletonSocket`,
`SingletonCookie`, `lockfile`, and `DevToolsActivePort` markers remain authoritative
before process classification. A readable non-child process with exactly one
non-empty absolute explicit `--user-data-dir=<value>` resolving to that directory
is `PROFILE_LOCKED`, regardless of executable identity. A well-formed explicit
different directory is not occupancy of the configured directory. A readable
same-executable main process with no `--user-data-dir` token supplies no
deterministic occupancy proof for that dedicated directory and does not by
itself block acquisition. No conclusion is drawn about its implicit profile;
there is no default-profile inference, profile/account discovery, or browser
policy lookup. The observed combination of that no-switch process and a second
main process explicitly naming a different directory is subject to the same
exact-directory rule.

Explicit user-data-dir evidence remains fail-closed: split spelling, duplicate
or malformed mentions, empty/relative values, and argument parsing failure are
`BROWSER_OWNERSHIP_UNPROVEN`. Unreadable/missing required metadata, empty or
invalid metadata, malformed process rows, invalid/non-array observations, or
more than 256 process observations cannot authorize launch. The existing child
process exclusion remains unchanged. A lack of exact configured-directory
occupancy signals permits continuation only through the existing bounded
acquisition path under `exclusive_user_data: true` and `allow_launch`; Human
authorization substitutes for neither occupancy nor listener/owner proof.
It grants no authority to select another profile, target, or fallback route.

Launch uses only the configured executable/user-data/profile
and fixed loopback CDP/no-startup-window switches, with no conversation URL
or workflow freshness token passed to the process. An exited launcher or listener
whose PID, executable, profile, user-data directory, address, or port differs
fails closed. Exact post-launch ownership proof remains authoritative before page
acquisition and is repeated before returning the acquired page for editing. If
another process acquires the port after the zero-listener observation, it cannot
authorize editing or submission: an ownership mismatch fails closed at the
initial or repeated check, with the initial check preceding page acquisition.
The transport never terminates a browser or removes profile locks.
An exclusive local `.aios-unattended-acquisition.lock` serializes acquisition
across invocations sharing the environment; it never serializes ordinary attached
delivery. A preexisting or stale acquisition lock also fails closed and remains
for Human reconciliation.

For an owned available endpoint with no exact page, creation requires exactly one
attached Playwright context and bounded browser-CDP proof that no non-default
contexts exist. TASK-306 permits exactly two `Target.getBrowserContexts` response
keysets: `browserContextIds` alone, or `browserContextIds` plus
`defaultBrowserContextId`. In both shapes, `browserContextIds` must be a list
and exactly empty. The optional default ID must be a non-empty string; it is
opaque compatibility metadata only and is never used to select or identify a
context, account, profile, route, or target. Unknown keys, missing or malformed
fields, and any non-default context remain `BROWSER_CONTEXT_UNPROVEN`.
The exact one-context list must match the supplied snapshot before proof and
remain stable after bounded CDP-session detach, within the acquisition deadline.
The transport issues at most one page-creation request in that proved context,
requires the returned page to begin as `about:blank` in that same context alongside
the unchanged prior page list, and navigates only to the original
`Binding.chat_url`. Multiple exact pages, context
ambiguity, an unexpected new-page URL, an intervening exact target, redirects,
or target/context drift fail closed. No alternate page/context is selected.
Page/context stability is checked after detach, creation, navigation, and the
repeated exact OS listener-owner proof before returning the page for editing.

One delivery invocation shares one acquisition budget across its existing finite
lane rechecks and, for an all-lanes drain, across its lane workers. The finite
recheck stops after acquisition was attempted. Endpoint restoration waits inside
that attempt have a 20-second acquisition deadline, with individually bounded
CDP connection, OS-helper, page-creation, and navigation operations. The initial
attach attempt retains its existing ten-second bound; canonical barriers retain their own
existing bounded read budgets. Playwright methods without public timeout
arguments use its existing sync loop/implementation mapping with timed async
cancellation. Expected operational failures in context/session/page bridge
calls, result mapping, or bounded page navigation are contained inside the
acquisition boundary: timeouts remain `ACQUISITION_TIMED_OUT`; unavailable or
otherwise unproved bridge operations, including cancellation and uncertain page
creation, become `BROWSER_CONTEXT_UNPROVEN`. Existing fixed ownership and target
failure reasons are preserved. A failed disconnect cannot replace an acquisition
reason with generic `LOCAL_FAILURE`. These failures grant no retry, unbounded
fallback, or second creation request. Failure only disconnects the client; any
uncertain created page/process is preserved and no submission is inferred.

Fixed bounded failures include `ACQUISITION_CONFIG_INVALID`,
`ACQUISITION_LIMIT_REACHED`, `ACQUISITION_TIMED_OUT`, `LAUNCH_NOT_AUTHORIZED`,
`PROFILE_LOCKED`, `BROWSER_OWNERSHIP_UNPROVEN`, `ENDPOINT_MISMATCH`,
`LAUNCH_UNCERTAIN`, and `BROWSER_CONTEXT_UNPROVEN`, alongside the existing exact
target, affinity, canonical-state, draft, generation, and surface failure codes.
Dependency/browser/OS exception text and default browser context ID values never
enter a public receipt. Raw chat URLs,
endpoints, executable/profile paths, command lines, and process IDs remain in the
machine-local transport boundary and never enter attention identities or canonical
TASK/RUN/RESULT/REVIEW/publication state. The GitHub workflow still passes only
selectors and the existing step-local read credential, with read-only
repository/actions permissions. It inherits local configuration and adds no
GitHub input, output, secret, browser download, or account selector.

Deterministic regressions use inert CDP, OS/process, and regular-Chat surface
fixtures, including exact acquisition/submission and durable ambiguity without
live ChatGPT access. Listener-proof regressions cover zero-listener authorization,
occupied/query-failure/malformed rejection, non-authoritative socket outcomes,
independent profile occupancy, and a post-observation port-ownership race.
Dedicated-directory regressions exercise the production process classifier,
including the native Windows argument parser on Windows, with a same-executable
no-switch main process plus a second explicit different-directory process.
They cover exact configured occupancy rejection, split/duplicate/empty/relative
or malformed explicit claims, parse failure, unreadable/malformed metadata, and
invalid/excessive process observations. Configured marker precedence and the
existing listener-race/exact-owner barriers remain covered without live access.
TASK-306 adds a production-shaped inert launched acquisition through attach,
exact owner proof, both accepted CDP context shapes, bounded detach, one blank
page creation, exact navigation, second owner proof, and the existing pre-submit
surface checks. That regression stops before editing or Send and opens no live
ChatGPT connection. Negative cases include unsupported response shapes, malformed
default IDs, non-default contexts, detach drift, operational bridge failures and
timeouts, page-result/mapping uncertainty, cleanup failure, and page/context or
owner races. The existing listener/profile, v3 origin-affinity, freshness, dedupe,
draft/generation, submission-limit, ambiguity, and no-blind-resend barriers retain
their regression coverage and authority.
Implementation and those regressions do not close H4D.
H4D roadmap closure remains downstream of reviewed publication and separate
Human/Brain live proof of the unprepared-target exit gate. H4E production
multi-lane live conformance, final H4B semantic resume, and H5 closure remain
separate downstream work; this implementation does not authorize or claim them.

### TASK-307 bounded unattended launch correction

TASK-307 removes only the explicit `--enable-automation` argument from
`Environment.command()`. The configured executable, explicit `--user-data-dir`
and `--profile-directory`, IPv4 loopback `--remote-debugging-address=127.0.0.1`,
configured `--remote-debugging-port`, `--no-first-run`,
`--no-default-browser-check`, and `--no-startup-window` remain unchanged.
Exact owner proof still binds the executable, user-data directory, profile,
loopback address/port, and launched-process identity; it never requires
`--enable-automation`. Listener/profile checks, launch environment sanitization,
one-attempt budget, context/page proof, exact navigation, repeated owner proof,
and all freshness, affinity, generation, draft, dedupe, ambiguity, and
no-blind-resend barriers keep their existing authority and fail-closed behavior.

The bounded Human diagnostic motivates this correction but is noncanonical
planning input. Its Human-visible startup path differs from unattended launch,
so it establishes neither an anti-bot root cause nor H4D live-exit success.
Removing the explicit automation opt-in introduces no replacement switch,
JavaScript patch, browser preference, extension, spoofing, or stealth mechanism.
CAPTCHA solving, human-verification bypass, anti-bot evasion, and automation
concealment remain outside this contract; a verification interstitial still
fails closed and requires Human handling.

Focused deterministic regressions assert the exact retained launch arguments
and exclusion of `--enable-automation`, pass the captured corrected launch
through the existing exact owner classifier (with native argument parsing on
Windows), and retain invalid/ambiguous ownership and launched-owner race
failures. They use inert OS/CDP/page fixtures without contacting live ChatGPT.
These regressions supply no Runtime evidence or live-exit proof. Runtime
verification, Reviewer judgment, and exact reviewed publication remain separate
from the later Human/Brain observation of the unprepared-target live exit gate.

This correction does not repair, resolve, supersede, or rewrite TASK-303 or
RUN-303-003 lineage or its failure/attention references. After exact reviewed
publication, the existing unresolved origin-affine RUN-303-003 attention subject
remains the intended real live carrier unless canonical freshness says
otherwise. No synthetic replacement subject or replay is authorized, and no
H4D, H4E, final H4B, or H5 advancement follows from this implementation.

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
